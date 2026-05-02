import './styles.css';

type ReportKey =
  | 'next_actions'
  | 'notes_todo'
  | 'behavior_summary'
  | 'capture_quality_report'
  | 'diagnostics_report'
  | 'asset_report'
  | 'call_graph_summary';

type OpenTarget =
  | ReportKey
  | 'asset_folder'
  | 'output_folder'
  | 'graph_reports'
  | 'notes'
  | 'defaults'
  | 'components'
  | 'devkit_report';

type ReportMap = Record<string, boolean>;

interface ExportQuality {
  status: string;
  hasLog: boolean;
  hasReport: boolean;
  warnings: number;
  errors: number;
  skipped: number;
  debugMessages: number;
  safeScsComponentCount: number;
  manualOrRestoredComponentCount: number;
  summary: string;
  reportCounts: Record<string, number>;
  componentSourceCounts: Record<string, number>;
}

interface AssetSummary {
  name: string;
  path: string;
  graphs: number;
  hasDefaults: boolean;
  defaultsCount: number;
  hasComponents: boolean;
  componentsCount: number;
  hasNotes: boolean;
  hasOutput: boolean;
  lastOutputAt: string;
  reports: ReportMap;
  exportQuality: ExportQuality;
}

interface AppState extends ApiResult {
  ok: boolean;
  projectRoot: string;
  captureRoot: string;
  assets: AssetSummary[];
  devkitRequestPath: string;
  devkitAssetPath: string;
  devkitPythonCommand: string;
  devkitOutputLogCommand: string;
}

interface ApiResult {
  ok: boolean;
  error?: string;
  code?: string;
  [key: string]: unknown;
}

interface JobInfo {
  id: string;
  kind: string;
  title: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'timed_out';
  command: string;
  stdout: string;
  stderr: string;
  returnCode: number | null;
  durationSeconds: number;
  error?: string;
  result?: Record<string, unknown>;
}

const app = document.querySelector<HTMLDivElement>('#app');
if (!app) {
  throw new Error('Missing #app root.');
}
const root = app;

class ApiFailure extends Error {
  payload: ApiResult;
  status: number;
  code?: string;

  constructor(payload: ApiResult, status: number) {
    super(payload.error || `请求失败：${status}`);
    this.payload = payload;
    this.status = status;
    this.code = typeof payload.code === 'string' ? payload.code : undefined;
  }
}

const reportLabels: Record<ReportKey, string> = {
  next_actions: '下一步',
  notes_todo: '缺失函数',
  behavior_summary: '行为说明',
  capture_quality_report: '采集质量',
  diagnostics_report: '诊断',
  asset_report: '完整报告',
  call_graph_summary: '调用摘要',
};

const graphTypes = ['EventGraph', 'Function', 'Macro', 'ConstructionScript', 'Unknown'];
const reportTargets = Object.keys(reportLabels) as ReportKey[];

let state: AppState | null = null;
let selectedPath = window.localStorage.getItem('blueprint-tool.selected') || '';
let selectedReport: ReportKey = 'next_actions';
let reportContent = '';
let reportPath = '';
let reportLoading = false;
let busy = false;
let devkitInput = '';
let captureAssetName = '';
let captureGraphName = '';
let captureGraphType = 'Unknown';
let compareOldPath = '';
let compareNewPath = '';
let compareContent = '';
let comparePath = '';
let logs: string[] = ['控制中心已就绪。请选择资产、采集图页，或重新生成分析报告。'];
let activeJobId = '';
let activeJobLabel = '';

function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function appendLog(message: string): void {
  const timestamp = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  logs = [`[${timestamp}] ${message}`, ...logs].slice(0, 90);
  render();
}

async function api<T extends ApiResult>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = (await response.json()) as T;
  if (!response.ok || !payload.ok) {
    throw new ApiFailure(payload, response.status);
  }
  return payload;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function isJobDone(job: JobInfo): boolean {
  return ['succeeded', 'failed', 'cancelled', 'timed_out'].includes(job.status);
}

function jobResultString(job: JobInfo, key: string): string {
  const value = job.result?.[key];
  return typeof value === 'string' ? value : '';
}

async function waitForJob(jobId: string, label: string): Promise<JobInfo> {
  let lastProgressAt = 0;
  let lastOutputLength = 0;
  const localStartedAt = Date.now();
  while (true) {
    const payload = await api<ApiResult & { job: JobInfo }>(`/api/jobs/${jobId}`);
    const job = payload.job;
    const outputLength = (job.stdout || '').length + (job.stderr || '').length;
    const now = Date.now();
    if (!isJobDone(job)) {
      if (now - lastProgressAt > 4000 || outputLength !== lastOutputLength) {
        const seconds = Math.round((now - localStartedAt) / 1000);
        appendLog(`${label}后台任务仍在运行：${job.status}，已耗时约 ${seconds}s。`);
        lastProgressAt = now;
        lastOutputLength = outputLength;
      }
      await delay(1000);
      continue;
    }
    return job;
  }
}

function selectedAsset(): AssetSummary | undefined {
  const byPath = state?.assets.find((asset) => asset.path === selectedPath);
  const ready = state?.assets.find((asset) => asset.graphs > 0 && asset.hasOutput);
  return byPath || ready || state?.assets[0];
}

function assetStatus(asset: AssetSummary): string {
  if (!asset.graphs) {
    return 'needs-work';
  }
  if (!asset.hasDefaults || !asset.hasComponents || !asset.hasOutput) {
    return 'partial';
  }
  return 'ready';
}

function statusText(asset: AssetSummary): string {
  const status = assetStatus(asset);
  if (status === 'ready') {
    return '已就绪';
  }
  if (status === 'partial') {
    return '需补上下文';
  }
  return '需采集';
}

function exportStatusText(status: string): string {
  if (status === 'ok') {
    return '正常';
  }
  if (status === 'warning') {
    return '有警告';
  }
  if (status === 'error') {
    return '有错误';
  }
  return '未导出';
}

function graphTypeLabel(type: string): string {
  if (type === 'EventGraph') {
    return '事件图';
  }
  if (type === 'Function') {
    return '函数';
  }
  if (type === 'Macro') {
    return '宏';
  }
  if (type === 'ConstructionScript') {
    return 'Construction Script';
  }
  return '未知';
}

function metric(label: string, value: string | number, tone = ''): string {
  return `
    <div class="metric ${tone}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function actionButton(label: string, action: string, variant = 'primary', disabled = false): string {
  return `
    <button class="button ${variant}" data-action="${escapeHtml(action)}" ${disabled ? 'disabled' : ''}>
      ${escapeHtml(label)}
    </button>
  `;
}

function reportButton(key: ReportKey, asset?: AssetSummary): string {
  const exists = Boolean(asset?.reports?.[key]);
  const classes = ['report-tab'];
  if (selectedReport === key) {
    classes.push('active');
  }
  if (!exists) {
    classes.push('missing');
  }
  return `
    <button class="${classes.join(' ')}" data-report="${key}" ${exists ? '' : 'title="这个报告还没有生成"'}>
      ${escapeHtml(reportLabels[key])}
    </button>
  `;
}

function renderAssetList(asset?: AssetSummary): string {
  if (!state?.assets.length) {
    return '<div class="empty-state">还没有捕获资产。可以在“图页采集”面板新建资产，或使用命令行采集向导。</div>';
  }
  return state.assets
    .map((item) => {
      const active = item.path === asset?.path ? 'active' : '';
      const status = assetStatus(item);
      return `
        <button class="asset-row ${active}" data-select-asset="${escapeHtml(item.path)}">
          <span class="asset-row-main">
            <strong>${escapeHtml(item.name)}</strong>
            <small>${item.graphs} 个图页 - ${escapeHtml(item.lastOutputAt || '未分析')}</small>
          </span>
          <span class="status-pill ${status}">${escapeHtml(statusText(item))}</span>
        </button>
      `;
    })
    .join('');
}

function renderReportPreview(asset?: AssetSummary): string {
  if (!asset) {
    return '<div class="report-preview muted">选择资产后，这里会显示关键报告预览。</div>';
  }
  if (reportLoading) {
    return '<div class="report-preview muted">正在读取报告...</div>';
  }
  if (!asset.reports[selectedReport]) {
    return '<div class="report-preview muted">这个报告还没有生成。请先运行标准分析。</div>';
  }
  return `
    <div class="report-path">${escapeHtml(reportPath)}</div>
    <pre class="report-preview">${escapeHtml(reportContent || '报告为空。')}</pre>
  `;
}

function assetOptions(selected: string): string {
  return (state?.assets || [])
    .map((asset) => `<option value="${escapeHtml(asset.path)}" ${asset.path === selected ? 'selected' : ''}>${escapeHtml(asset.name)}</option>`)
    .join('');
}

function graphTypeOptions(): string {
  return graphTypes
    .map((type) => `<option value="${escapeHtml(type)}" ${captureGraphType === type ? 'selected' : ''}>${escapeHtml(graphTypeLabel(type))}</option>`)
    .join('');
}

function renderCapturePanel(asset?: AssetSummary): string {
  const assetName = captureAssetName || asset?.name || '';
  return `
    <section class="panel capture-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">图页采集</p>
          <h2>从剪贴板保存当前蓝图图页</h2>
        </div>
        <span class="soft-label">在 ARK DevKit 里 Ctrl+A / Ctrl+C，然后回这里保存</span>
      </div>
      <div class="form-grid">
        <label>
          <span>资产名</span>
          <input id="capture-asset-name" value="${escapeHtml(assetName)}" placeholder="MilkGlider_Character_BP" />
        </label>
        <label>
          <span>图页名</span>
          <input id="capture-graph-name" value="${escapeHtml(captureGraphName)}" placeholder="Client Tick Gliding" />
        </label>
        <label>
          <span>图页类型</span>
          <select id="capture-graph-type">${graphTypeOptions()}</select>
        </label>
      </div>
      <div class="button-row">
        ${actionButton('保存剪贴板图页', 'capture-page', 'primary', busy)}
        ${actionButton('保存并分析', 'capture-page-analyze', 'secondary', busy)}
      </div>
    </section>
  `;
}

function renderQualityPanel(asset?: AssetSummary): string {
  const quality = asset?.exportQuality;
  const counts = quality?.reportCounts || {};
  const sources = quality?.componentSourceCounts || {};
  const sourceRows = Object.keys(sources).length
    ? Object.entries(sources)
        .map(([source, count]) => `<li>${escapeHtml(source)}: ${escapeHtml(count)}</li>`)
        .join('')
    : '<li>无</li>';
  return `
    <section class="panel quality-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">默认值 / 组件</p>
          <h2>DevKit 导出质量检查</h2>
        </div>
        <span class="status-pill ${quality?.status || 'needs-work'}">${escapeHtml(exportStatusText(quality?.status || 'missing'))}</span>
      </div>
      <p class="soft-copy">${escapeHtml(quality?.summary || '还没有选择资产。')}</p>
      <div class="metrics-grid compact">
        ${metric('导出变量', counts.blueprintVariables ?? '-')}
        ${metric('类默认值', counts.classDefaults ?? asset?.defaultsCount ?? '-')}
        ${metric('导出组件', counts.componentsExported ?? asset?.componentsCount ?? '-')}
        ${metric('跳过属性', quality?.skipped ?? '-')}
      </div>
      <div class="source-box">
        <strong>组件来源</strong>
        <ul>${sourceRows}</ul>
      </div>
    </section>
  `;
}

function renderDevkitPanel(): string {
  const currentPath = devkitInput || state?.devkitAssetPath || '';
  return `
    <section class="panel devkit-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">DevKit 导出</p>
          <h2>准备导出器命令</h2>
        </div>
        <span class="soft-label">请求文件：${escapeHtml(state?.devkitRequestPath || '')}</span>
      </div>
      <textarea id="devkit-path" spellcheck="false" placeholder="/Game/Mods/.../Asset.Asset">${escapeHtml(currentPath)}</textarea>
      <div class="button-row">
        ${actionButton('保存路径并复制 Python 命令', 'save-devkit-request', 'primary', busy)}
        ${actionButton('复制 Python 命令', 'copy-python-command', 'secondary')}
        ${actionButton('复制 Output Log 命令', 'copy-output-command', 'secondary')}
      </div>
    </section>
  `;
}

function renderComparePanel(): string {
  const assets = state?.assets || [];
  const oldValue = compareOldPath || assets[0]?.path || '';
  const newValue = compareNewPath || assets[1]?.path || assets[0]?.path || '';
  return `
    <section class="panel compare-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">资产对比</p>
          <h2>生成行为影响报告</h2>
        </div>
        <span class="soft-label">${escapeHtml(comparePath || '还没有运行对比')}</span>
      </div>
      <div class="form-grid two">
        <label>
          <span>旧资产</span>
          <select id="compare-old">${assetOptions(oldValue)}</select>
        </label>
        <label>
          <span>新资产</span>
          <select id="compare-new">${assetOptions(newValue)}</select>
        </label>
      </div>
      <div class="button-row">
        ${actionButton('运行行为对比', 'run-compare', 'primary', busy || assets.length < 2)}
      </div>
      <pre class="compare-preview">${escapeHtml(compareContent || '对比输出会显示在这里。')}</pre>
    </section>
  `;
}

function renderMain(): void {
  const asset = selectedAsset();
  if (asset && asset.path !== selectedPath) {
    selectedPath = asset.path;
    window.localStorage.setItem('blueprint-tool.selected', selectedPath);
  }

  root.innerHTML = `
    <div class="shell">
      <header class="topbar">
        <div>
          <p class="eyebrow">ARK DevKit Blueprint Translator</p>
          <h1>蓝图分析控制中心</h1>
        </div>
        <div class="top-actions">
          ${actionButton('刷新资产', 'refresh', 'secondary', busy)}
          ${actionButton('打开 captures', 'open-capture-root', 'ghost')}
        </div>
      </header>

      <aside class="sidebar">
        <div class="sidebar-heading">
          <span>捕获资产</span>
          <strong>${state?.assets.length || 0}</strong>
        </div>
        <div class="asset-list">${renderAssetList(asset)}</div>
      </aside>

      <main class="workspace">
        <section class="panel hero-panel">
          <div class="status-banner ${busy ? 'working' : ''}">
            <strong>${busy ? '正在执行' : '当前状态'}</strong>
            <span>${escapeHtml(logs[0] || '等待操作。')}</span>
          </div>
          <div class="asset-title">
            <div>
              <p class="eyebrow">当前资产</p>
              <h2>${escapeHtml(asset?.name || '未选择资产')}</h2>
              <p class="path-line">${escapeHtml(asset?.path || state?.captureRoot || '')}</p>
            </div>
            ${asset ? `<span class="status-pill large ${assetStatus(asset)}">${escapeHtml(statusText(asset))}</span>` : ''}
          </div>
          <div class="metrics-grid">
            ${metric('图页', asset?.graphs ?? 0)}
            ${metric('默认值', asset?.hasDefaults ? asset.defaultsCount : '缺失', asset?.hasDefaults ? 'good' : 'warn')}
            ${metric('组件', asset?.hasComponents ? asset.componentsCount : '缺失', asset?.hasComponents ? 'good' : 'warn')}
            ${metric('最近分析', asset?.lastOutputAt || '无', asset?.hasOutput ? 'good' : 'warn')}
          </div>
          <div class="button-row">
            ${actionButton('重新分析标准报告', 'analyze-standard', 'primary', !asset || !asset.graphs || busy)}
            ${actionButton('生成 debug 包', 'analyze-debug', 'danger', !asset || !asset.graphs || busy)}
            ${actionButton('生成 compact 报告', 'analyze-compact', 'secondary', !asset || !asset.graphs || busy)}
            ${actionButton('打开输出文件夹', 'open-output', 'secondary', !asset || !asset.hasOutput)}
            ${activeJobId ? actionButton(`取消${activeJobLabel}`, 'cancel-job', 'danger') : ''}
          </div>
        </section>

        ${renderCapturePanel(asset)}

        <section class="panel report-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">报告</p>
              <h2>先看这些关键文件</h2>
            </div>
            <div class="button-row tight">
              ${actionButton('打开当前报告', 'open-current-report', 'secondary', !asset || !asset.reports[selectedReport])}
              ${actionButton('打开 graph_reports', 'open-graph-reports', 'ghost', !asset)}
            </div>
          </div>
          <div class="report-tabs">
            ${reportTargets.map((key) => reportButton(key, asset)).join('')}
          </div>
          ${renderReportPreview(asset)}
        </section>

        <div class="split-grid">
          ${renderQualityPanel(asset)}
          ${renderDevkitPanel()}
        </div>

        ${renderComparePanel()}

        <section class="panel log-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">运行日志</p>
              <h2>${busy ? '正在执行...' : '最近动作'}</h2>
            </div>
          </div>
          <pre class="log-output">${escapeHtml(logs.join('\n'))}</pre>
        </section>
      </main>
    </div>
  `;

  bindEvents();
}

function renderLoading(): void {
  root.innerHTML = `
    <div class="boot-screen">
      <div class="boot-card">
        <p class="eyebrow">Blueprint Tool</p>
        <h1>正在连接本地控制中心</h1>
        <p>如果页面停在这里，请先运行 <code>scripts\\launch_blueprint_tool.ps1</code>。</p>
      </div>
    </div>
  `;
}

function render(): void {
  if (!state) {
    renderLoading();
    return;
  }
  renderMain();
}

function syncInputs(): void {
  captureAssetName = document.querySelector<HTMLInputElement>('#capture-asset-name')?.value || captureAssetName;
  captureGraphName = document.querySelector<HTMLInputElement>('#capture-graph-name')?.value || captureGraphName;
  captureGraphType = document.querySelector<HTMLSelectElement>('#capture-graph-type')?.value || captureGraphType;
  devkitInput = document.querySelector<HTMLTextAreaElement>('#devkit-path')?.value || devkitInput;
  compareOldPath = document.querySelector<HTMLSelectElement>('#compare-old')?.value || compareOldPath;
  compareNewPath = document.querySelector<HTMLSelectElement>('#compare-new')?.value || compareNewPath;
}

function bindEvents(): void {
  document.querySelectorAll<HTMLButtonElement>('[data-select-asset]').forEach((button) => {
    button.addEventListener('click', () => {
      syncInputs();
      selectedPath = button.dataset.selectAsset || '';
      window.localStorage.setItem('blueprint-tool.selected', selectedPath);
      captureAssetName = selectedAsset()?.name || captureAssetName;
      reportContent = '';
      reportPath = '';
      void loadReport(selectedReport);
      render();
    });
  });

  document.querySelectorAll<HTMLButtonElement>('[data-report]').forEach((button) => {
    button.addEventListener('click', () => {
      syncInputs();
      selectedReport = (button.dataset.report || 'next_actions') as ReportKey;
      void loadReport(selectedReport);
    });
  });

  document.querySelectorAll<HTMLButtonElement>('[data-action]').forEach((button) => {
    button.addEventListener('click', () => {
      syncInputs();
      void handleAction(button.dataset.action || '');
    });
  });

  document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('input, textarea, select').forEach((input) => {
    input.addEventListener('input', syncInputs);
    input.addEventListener('change', syncInputs);
  });
}

async function refreshState(keepReport = true): Promise<void> {
  const payload = await api<AppState>('/api/state');
  state = payload;
  if (!selectedPath || !state.assets.some((asset) => asset.path === selectedPath)) {
    selectedPath = state.assets.find((asset) => asset.graphs > 0 && asset.hasOutput)?.path || state.assets[0]?.path || '';
  }
  if (!captureAssetName) {
    captureAssetName = selectedAsset()?.name || '';
  }
  if (!devkitInput) {
    devkitInput = state.devkitAssetPath;
  }
  if (!compareOldPath) {
    compareOldPath = state.assets[0]?.path || '';
  }
  if (!compareNewPath) {
    compareNewPath = state.assets[1]?.path || state.assets[0]?.path || '';
  }
  render();
  if (keepReport && selectedPath) {
    await loadReport(selectedReport, false);
  }
}

async function loadReport(key: ReportKey, rerender = true): Promise<void> {
  const asset = selectedAsset();
  selectedReport = key;
  if (!asset || !asset.reports[key]) {
    reportContent = '';
    reportPath = '';
    if (rerender) {
      render();
    }
    return;
  }
  reportLoading = true;
  render();
  try {
    const query = new URLSearchParams({ assetPath: asset.path, target: key });
    const payload = await api<ApiResult & { content: string; path: string }>(`/api/report?${query}`);
    reportContent = payload.content;
    reportPath = payload.path;
  } catch (error) {
    reportContent = error instanceof Error ? error.message : String(error);
    reportPath = '';
  } finally {
    reportLoading = false;
    if (rerender) {
      render();
    }
  }
}

async function runAnalysis(reportLevel: 'compact' | 'standard' | 'debug'): Promise<void> {
  const asset = selectedAsset();
  if (!asset) {
    appendLog('还没有选择资产。');
    return;
  }
  busy = true;
  appendLog(`开始为 ${asset.name} 生成 ${reportLevel} 报告。`);
  try {
    const payload = await api<ApiResult & { job: JobInfo }>(
      '/api/analyze',
      {
        method: 'POST',
        body: JSON.stringify({ assetPath: asset.path, reportLevel }),
      },
    );
    activeJobId = payload.job.id;
    activeJobLabel = '分析';
    appendLog(`分析后台任务已创建：${payload.job.id}`);
    render();
    const job = await waitForJob(payload.job.id, '分析');
    const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
    appendLog(`分析${outcome}，耗时 ${job.durationSeconds}s。`);
    if (job.error) {
      appendLog(job.error);
    }
    if (job.stderr) {
      appendLog(job.stderr.trim().slice(-1200));
    }
    if (job.stdout) {
      appendLog(job.stdout.trim().slice(-1200));
    }
    await refreshState(false);
    if (job.status === 'succeeded') {
      await loadReport('next_actions');
    }
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  } finally {
    busy = false;
    activeJobId = '';
    activeJobLabel = '';
    render();
  }
}

async function capturePage(analyzeAfter: boolean, allowOverwrite = false): Promise<void> {
  syncInputs();
  if (!captureGraphName.trim()) {
    appendLog('保存剪贴板图页前，需要先填写图页名。');
    return;
  }
  busy = true;
  appendLog(`正在从剪贴板采集图页：“${captureGraphName}”。`);
  try {
    const asset = selectedAsset();
    const payload = await api<ApiResult & { asset: AssetSummary; graphPath: string; record: { warnings?: string[]; backup_path?: string }; analysisJob?: JobInfo }>(
      '/api/capture-graph',
      {
        method: 'POST',
        body: JSON.stringify({
          assetPath: asset?.path || '',
          assetName: captureAssetName,
          graphName: captureGraphName,
          graphType: captureGraphType,
          analyzeAfter,
          reportLevel: 'standard',
          allowOverwrite,
        }),
      },
    );
    selectedPath = payload.asset.path;
    window.localStorage.setItem('blueprint-tool.selected', selectedPath);
    appendLog(`已保存图页：${payload.graphPath}`);
    if (payload.record.backup_path) {
      appendLog(`已备份被覆盖的旧图页：${payload.record.backup_path}`);
    }
    if (payload.record.warnings?.length) {
      appendLog(`采集警告：${payload.record.warnings.join('; ')}`);
    }
    captureGraphName = '';
    await refreshState(false);
    if (analyzeAfter && payload.analysisJob) {
      activeJobId = payload.analysisJob.id;
      activeJobLabel = '分析';
      appendLog(`保存后分析后台任务已创建：${payload.analysisJob.id}`);
      render();
      const job = await waitForJob(payload.analysisJob.id, '保存后分析');
      const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
      appendLog(`保存后分析${outcome}，耗时 ${job.durationSeconds}s。`);
      if (job.error) {
        appendLog(job.error);
      }
      if (job.stderr) {
        appendLog(job.stderr.trim().slice(-1200));
      }
      await refreshState(false);
      if (job.status === 'succeeded') {
        await loadReport('next_actions');
      }
    }
  } catch (error) {
    if (error instanceof ApiFailure && error.code === 'overwrite_required') {
      busy = false;
      render();
      const ok = window.confirm('这个图页已经存在。要覆盖它吗？旧文件会自动备份到 graphs/_backups/。');
      if (ok) {
        await capturePage(analyzeAfter, true);
      } else {
        appendLog('已取消覆盖，原图页保持不变。');
      }
      return;
    }
    appendLog(error instanceof Error ? error.message : String(error));
  } finally {
    busy = false;
    activeJobId = '';
    activeJobLabel = '';
    render();
  }
}

async function openTarget(target: OpenTarget): Promise<void> {
  const asset = selectedAsset();
  if (!asset) {
    appendLog('还没有选择资产。');
    return;
  }
  try {
    const payload = await api<ApiResult & { path: string }>('/api/open', {
      method: 'POST',
      body: JSON.stringify({ assetPath: asset.path, target }),
    });
    appendLog(`已打开：${payload.path}`);
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  }
}

async function copyText(text: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    appendLog(`已复制：${label}`);
  } catch {
    appendLog(`浏览器拒绝剪贴板权限，请手动复制：${text}`);
  }
}

async function saveDevkitRequest(): Promise<void> {
  syncInputs();
  try {
    const payload = await api<ApiResult & { assetPath: string; pythonCommand: string }>('/api/devkit-request', {
      method: 'POST',
      body: JSON.stringify({ assetPath: devkitInput }),
    });
    devkitInput = payload.assetPath;
    await copyText(payload.pythonCommand, 'DevKit Python 命令');
    appendLog(`已保存 DevKit 导出路径：${payload.assetPath}`);
    await refreshState(false);
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  }
}

async function runCompare(): Promise<void> {
  syncInputs();
  if (!compareOldPath || !compareNewPath || compareOldPath === compareNewPath) {
    appendLog('运行对比前，请选择两个不同的资产。');
    return;
  }
  busy = true;
  appendLog('正在运行资产行为对比。');
  try {
    const payload = await api<ApiResult & { job: JobInfo }>(
      '/api/compare-asset',
      {
        method: 'POST',
        body: JSON.stringify({ oldAssetPath: compareOldPath, newAssetPath: compareNewPath }),
      },
    );
    activeJobId = payload.job.id;
    activeJobLabel = '对比';
    appendLog(`对比后台任务已创建：${payload.job.id}`);
    render();
    const job = await waitForJob(payload.job.id, '对比');
    compareContent = jobResultString(job, 'behaviorImpact') || job.stderr || job.error || '对比已完成，但没有生成行为影响报告。';
    comparePath = jobResultString(job, 'behaviorImpactPath');
    const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
    appendLog(`对比${outcome}，耗时 ${job.durationSeconds}s。`);
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  } finally {
    busy = false;
    activeJobId = '';
    activeJobLabel = '';
    render();
  }
}

async function cancelCurrentJob(): Promise<void> {
  if (!activeJobId) {
    appendLog('当前没有正在运行的后台任务。');
    return;
  }
  try {
    await api<ApiResult & { job: JobInfo }>(`/api/jobs/${activeJobId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    appendLog(`已请求取消${activeJobLabel || '当前'}任务。`);
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  }
}

async function handleAction(action: string): Promise<void> {
  if (action === 'refresh') {
    await refreshState();
    appendLog('资产状态已刷新。');
    return;
  }
  if (action === 'open-capture-root') {
    try {
      const payload = await api<ApiResult & { path: string }>('/api/open-captures', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      appendLog(`已打开：${payload.path}`);
    } catch (error) {
      appendLog(error instanceof Error ? error.message : String(error));
    }
    return;
  }
  if (action === 'analyze-standard') {
    await runAnalysis('standard');
    return;
  }
  if (action === 'analyze-debug') {
    await runAnalysis('debug');
    return;
  }
  if (action === 'analyze-compact') {
    await runAnalysis('compact');
    return;
  }
  if (action === 'cancel-job') {
    await cancelCurrentJob();
    return;
  }
  if (action === 'capture-page') {
    await capturePage(false);
    return;
  }
  if (action === 'capture-page-analyze') {
    await capturePage(true);
    return;
  }
  if (action === 'open-output') {
    await openTarget('output_folder');
    return;
  }
  if (action === 'open-current-report') {
    await openTarget(selectedReport);
    return;
  }
  if (action === 'open-graph-reports') {
    await openTarget('graph_reports');
    return;
  }
  if (action === 'save-devkit-request') {
    await saveDevkitRequest();
    return;
  }
  if (action === 'copy-python-command') {
    await copyText(state?.devkitPythonCommand || '', 'DevKit Python 命令');
    return;
  }
  if (action === 'copy-output-command') {
    await copyText(state?.devkitOutputLogCommand || '', 'DevKit Output Log 命令');
    return;
  }
  if (action === 'run-compare') {
    await runCompare();
  }
}

render();
refreshState().catch((error) => {
  logs = [error instanceof Error ? error.message : String(error)];
  render();
});
