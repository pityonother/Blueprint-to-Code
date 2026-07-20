import './styles.css';

type ReportKey =
  | 'agent_index'
  | 'next_actions'
  | 'notes_todo'
  | 'behavior_summary'
  | 'context_review'
  | 'asset_memory_card'
  | 'context_pack'
  | 'formula_candidates'
  | 'formula_candidates_json'
  | 'unresolved_formulas'
  | 'capture_quality_report'
  | 'diagnostics_report'
  | 'asset_report'
  | 'call_graph_summary'
  | 'uasset_graph_read_report'
  | 'uasset_property_parse_report'
  | 'uasset_link_resolution_report'
  | 'uasset_partial_graph_triage'
  | 'uasset_quality_gates'
  | 'uasset_vs_clipboard_compare'
  | 'uasset_class_defaults_report'
  | 'uasset_structure_report';

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
  skippedAttempts: number;
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
  hasGraphQueue: boolean;
  graphQueueCount: number;
  graphQueueCompactCount: number;
  graphQueueRecommendedCount: number;
  graphQueueOptionalCount: number;
  graphQueueDeferredCount: number;
  graphQueueFocusedCount: number;
  hasGraphCandidates: boolean;
  graphCandidateCount: number;
  hasUassetStructure: boolean;
  uassetEdGraphCount: number;
  uassetFunctionGraphCount: number;
  uassetCollapsedGraphCount: number;
  uassetStandaloneGraphCount: number;
  uassetFunctionCount: number;
  hasUassetGraphRead: boolean;
  hasEvidenceStore?: boolean;
  evidenceRevision?: string;
  preservedLegacyReports?: boolean;
  uassetReadGraphCount: number;
  uassetReadNodeCount: number;
  uassetReadPinCount: number;
  uassetReadLinkCount: number;
  uassetReadCompleteCount: number;
  uassetReadPartialCount: number;
  uassetReadNeedsClipboardCount: number;
  hasDefaults: boolean;
  defaultsCount: number;
  hasComponents: boolean;
  componentsCount: number;
  hasNotes: boolean;
  hasOutput: boolean;
  lastOutputAt: string;
  reports: ReportMap;
  formulaCandidateCount: number;
  unresolvedFormulaCount: number;
  assetMemoryCardExists: boolean;
  contextPackExists: boolean;
  exportQuality: ExportQuality;
}

interface KnowledgeBaseSummary {
  exists: boolean;
  root: string;
  indexPath: string;
  reportPath: string;
  reportExists: boolean;
  globalReportPath: string;
  globalReportExists: boolean;
  priorityReportPath: string;
  priorityReportExists: boolean;
  priorityResultsPath: string;
  priorityResultsExists: boolean;
  priorityQueuePath: string;
  priorityQueueExists: boolean;
  generated: string;
  focus: string;
  assetCount: number;
  systemCount: number;
  globalAssetCount: number;
  capturedAssetCount: number;
}

interface AppState extends ApiResult {
  ok: boolean;
  projectRoot: string;
  captureRoot: string;
  assets: AssetSummary[];
  knowledgeBase: KnowledgeBaseSummary;
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

interface MissingFunctionItem {
  function: string;
  sourceGraphs: string[];
  areas: string[];
  suggested: string;
}

interface GraphQueueItem {
  name: string;
  type: string;
  line: string;
  tier: 'recommended' | 'optional' | 'deferred';
  reason: string;
}

interface GraphQueueSummary {
  total: number;
  compact: number;
  recommended: number;
  optional: number;
  deferred: number;
  focused: number;
  items: GraphQueueItem[];
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
  agent_index: 'AI 证据索引',
  next_actions: '下一步',
  notes_todo: '缺失函数',
  behavior_summary: '行为说明（legacy）',
  context_review: '上下文复查',
  asset_memory_card: '资产小卡片',
  context_pack: '问题上下文包',
  formula_candidates: '公式候选',
  formula_candidates_json: '公式候选 JSON',
  unresolved_formulas: 'unresolved formulas',
  capture_quality_report: '采集质量',
  diagnostics_report: '诊断（legacy）',
  asset_report: '完整报告（legacy）',
  call_graph_summary: '调用摘要（legacy）',
  uasset_graph_read_report: '.uasset 图内容',
  uasset_property_parse_report: '.uasset 属性',
  uasset_link_resolution_report: '.uasset 连线',
  uasset_partial_graph_triage: 'Partial 归因',
  uasset_quality_gates: '质量门槛',
  uasset_vs_clipboard_compare: '二进制/复制对比',
  uasset_class_defaults_report: '.uasset 默认值',
  uasset_structure_report: '.uasset 结构',
};

const graphTypes = ['EventGraph', 'Function', 'Macro', 'ConstructionScript', 'Unknown'];
const reportTargets = Object.keys(reportLabels) as ReportKey[];
const defaultReport: ReportKey = 'agent_index';
const DEFAULT_ARTIFACT_MODE = 'indexed' as const;

let state: AppState | null = null;
let selectedPath = window.localStorage.getItem('blueprint-tool.selected') || '';
let selectedReport: ReportKey = defaultReport;
let reportContent = '';
let reportPath = '';
let reportLoading = false;
let busy = false;
let devkitInput = '';
let captureAssetName = '';
let captureGraphName = '';
let captureGraphType = 'Unknown';
let captureQueueText = window.localStorage.getItem('blueprint-tool.captureQueue') || '';
let captureQueueCursor = Number(window.localStorage.getItem('blueprint-tool.captureQueueCursor') || '0') || 0;
let compareOldPath = '';
let compareNewPath = '';
let compareContent = '';
let comparePath = '';
let logs: string[] = ['控制中心已就绪。请选择资产、采集图页，或重新生成分析报告。'];
let activeJobId = '';
let activeJobLabel = '';
let mainNotice = '';
let mainNoticeTone: 'info' | 'good' | 'warn' | 'danger' = 'info';
let missingFunctions: MissingFunctionItem[] = [];
let selectedMissingFunctions = new Set<string>();
let graphQueueSummary: GraphQueueSummary | null = null;
let graphQueueSummaryAssetPath = '';

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

function setMainNotice(message: string, tone: 'info' | 'good' | 'warn' | 'danger' = 'info'): void {
  mainNotice = message;
  mainNoticeTone = tone;
  appendLog(message);
}

function readableError(error: unknown): string {
  if (error instanceof ApiFailure) {
    const attempted = error.payload.attemptedPaths;
    if (Array.isArray(attempted) && attempted.length) {
      return `${error.message} 尝试路径：${attempted.slice(0, 3).join('；')}`;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
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

function normalizeObjectPathInput(rawText: string): string {
  let text = (rawText || '').trim().replace(/\\/g, '/').replace(/^["']|["']$/g, '');
  const quoted = text.match(/['"](?<path>[^'"]+)['"]/);
  if (quoted?.groups?.path) {
    text = quoted.groups.path.trim();
  }
  const pathMatch = text.match(/(?<path>\/Game\/[^\s,'"]+)/);
  if (pathMatch?.groups?.path) {
    text = pathMatch.groups.path.trim();
  } else {
    const shorthandMatch = text.match(/(?<path>(?:\/?Game|\/?Mods)\/[^\s,'"]+|[A-Za-z0-9_][\w.-]*\/[^\s,'"]+)/);
    if (shorthandMatch?.groups?.path) {
      text = shorthandMatch.groups.path.trim();
    }
  }
  text = text.replace(/^["']|["']$/g, '');
  const lowered = text.toLowerCase();
  if (lowered.startsWith('/game/')) {
    text = `/Game/${text.slice(6)}`;
  } else if (lowered.startsWith('game/')) {
    text = `/Game/${text.slice(5)}`;
  } else if (lowered.startsWith('/mods/')) {
    text = `/Game${text}`;
  } else if (lowered.startsWith('mods/')) {
    text = `/Game/${text}`;
  } else if (/^[A-Za-z0-9_][\w.-]*\//.test(text)) {
    text = `/Game/Mods/${text.replace(/^\/+/, '')}`;
  } else {
    return '';
  }
  if (text.includes('.') && text.endsWith('_C')) {
    const dot = text.lastIndexOf('.');
    text = `${text.slice(0, dot + 1)}${text.slice(dot + 1, -2)}`;
  }
  if (!text.includes('.')) {
    const objectName = text.split('/').pop() || '';
    if (objectName) {
      text = `${text}.${objectName}`;
    }
  }
  return text;
}

function assetNameFromObjectPath(rawText: string): string {
  const normalized = normalizeObjectPathInput(rawText);
  if (!normalized) {
    return '';
  }
  return normalized.split('.').pop() || normalized.split('/').pop() || '';
}

function preferredReportForAsset(asset?: AssetSummary): ReportKey {
  if (asset?.reports?.[defaultReport]) {
    return defaultReport;
  }
  if (asset?.reports?.next_actions) {
    return 'next_actions';
  }
  if (asset?.reports?.asset_memory_card) {
    return 'asset_memory_card';
  }
  if (asset?.reports?.formula_candidates) {
    return 'formula_candidates';
  }
  return defaultReport;
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

function graphQueueTierLabel(tier: string): string {
  if (tier === 'recommended') {
    return '推荐采集';
  }
  if (tier === 'optional') {
    return '可选采集';
  }
  if (tier === 'deferred') {
    return '暂不采集';
  }
  return '未分类';
}

function graphQueueModeLabel(mode: string): string {
  if (mode === 'compact') {
    return '精简采集队列';
  }
  if (mode === 'recommended') {
    return '推荐分页';
  }
  if (mode === 'focused') {
    return '推荐+可选分页';
  }
  if (mode === 'all') {
    return '全部分页';
  }
  return '分页队列';
}

interface CaptureQueueItem {
  name: string;
  type: string;
  raw: string;
}

function normalizeGraphType(value: string): string {
  const text = value.trim().toLowerCase();
  if (!text) {
    return 'Unknown';
  }
  if (text === 'eventgraph' || text === 'event graph' || text === 'event' || text === '事件图') {
    return 'EventGraph';
  }
  if (text === 'function' || text === 'func' || text === '函数') {
    return 'Function';
  }
  if (text === 'macro' || text === '宏') {
    return 'Macro';
  }
  if (text === 'constructionscript' || text === 'construction script' || text === 'construction' || text === '构造脚本') {
    return 'ConstructionScript';
  }
  return graphTypes.includes(value.trim()) ? value.trim() : 'Unknown';
}

function parseCaptureQueue(text: string): CaptureQueueItem[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'))
    .map((line) => {
      let name = line;
      let type = 'Unknown';
      const pipeParts = line.split('|').map((part) => part.trim()).filter(Boolean);
      const tabParts = line.split(/\t|,/).map((part) => part.trim()).filter(Boolean);
      if (pipeParts.length >= 2) {
        name = pipeParts[0];
        type = normalizeGraphType(pipeParts[1]);
      } else if (tabParts.length >= 2 && normalizeGraphType(tabParts[tabParts.length - 1]) !== 'Unknown') {
        name = tabParts.slice(0, -1).join(' ');
        type = normalizeGraphType(tabParts[tabParts.length - 1]);
      }
      return { name: name.trim(), type, raw: line };
    })
    .filter((item) => item.name);
}

function captureQueueItems(): CaptureQueueItem[] {
  return parseCaptureQueue(captureQueueText);
}

function currentCaptureQueueItem(): CaptureQueueItem | undefined {
  const items = captureQueueItems();
  if (!items.length || captureQueueCursor >= items.length) {
    return undefined;
  }
  const index = Math.max(0, captureQueueCursor);
  return items[index];
}

function saveCaptureQueueState(): void {
  window.localStorage.setItem('blueprint-tool.captureQueue', captureQueueText);
  window.localStorage.setItem('blueprint-tool.captureQueueCursor', String(captureQueueCursor));
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

function renderGraphQueuePreview(asset?: AssetSummary): string {
  if (!asset || graphQueueSummaryAssetPath !== asset.path || !graphQueueSummary) {
    return '';
  }
  const summary = graphQueueSummary;
  const buckets: Array<['recommended' | 'optional' | 'deferred', string]> = [
    ['recommended', '优先复制这些，通常是事件入口、RPC、状态修改和关键 ARK 行为。'],
    ['optional', '分析报告提示缺上下文时，再补这些判断或辅助函数。'],
    ['deferred', '折叠图或低价值 Getter，默认先不碰。'],
  ];
  const bucketHtml = buckets
    .map(([tier, hint]) => {
      const rows = summary.items
        .filter((item) => item.tier === tier)
        .slice(0, 12)
        .map(
          (item) => `
            <div class="queue-preview-row">
              <strong>${escapeHtml(item.name)}</strong>
              <small>${escapeHtml(graphTypeLabel(item.type))} - ${escapeHtml(item.reason)}</small>
            </div>
          `,
        )
        .join('');
      const count = summary[tier];
      return `
        <div class="queue-bucket ${tier}">
          <div class="queue-bucket-head">
            <strong>${escapeHtml(graphQueueTierLabel(tier))}</strong>
            <span>${escapeHtml(count)}</span>
          </div>
          <p>${escapeHtml(hint)}</p>
          <div class="queue-preview-list">${rows || '<div class="queue-empty compact">无</div>'}</div>
        </div>
      `;
    })
    .join('');
  return `
    <div class="queue-filter-panel">
      <div class="queue-filter-metrics">
        ${metric('精简', summary.compact ?? summary.recommended, 'good')}
        ${metric('可选', summary.optional, 'warn')}
        ${metric('暂不采集', summary.deferred)}
      </div>
      ${bucketHtml}
    </div>
  `;
}

function renderCaptureQueue(): string {
  const items = captureQueueItems();
  const asset = selectedAsset();
  const hasItems = items.length > 0;
  const currentIndex = hasItems ? Math.max(0, Math.min(captureQueueCursor, items.length - 1)) : 0;
  const current = hasItems && captureQueueCursor < items.length ? items[currentIndex] : undefined;
  const progress = hasItems ? `${Math.min(captureQueueCursor, items.length)} / ${items.length} 已保存` : '未设置';
  const list = hasItems
    ? items
        .map((item, index) => {
          const classes = ['queue-row'];
          if (index < captureQueueCursor) {
            classes.push('done');
          }
          if (index === currentIndex && captureQueueCursor < items.length) {
            classes.push('current');
          }
          return `
            <div class="${classes.join(' ')}">
              <span>${index + 1}</span>
              <strong>${escapeHtml(item.name)}</strong>
              <small>${escapeHtml(graphTypeLabel(item.type))}</small>
            </div>
          `;
        })
        .join('')
    : '<div class="queue-empty">把 My Blueprint 里的分页名称粘贴到这里，一行一个。</div>';
  return `
    <div class="capture-queue">
      <label>
        <span>批量图页队列</span>
        <textarea id="capture-queue-text" spellcheck="false" placeholder="SetParachuteState&#10;OnRep_bWantsToParachute | Function&#10;EventGraph | EventGraph">${escapeHtml(captureQueueText)}</textarea>
      </label>
      ${
        asset?.hasGraphQueue
          ? `<div class="button-row tight">
              ${actionButton(`载入精简采集 ${asset.graphQueueCompactCount} 个`, 'load-graph-queue-compact', 'primary', busy || !asset.graphQueueCompactCount)}
              ${actionButton(`载入补充上下文 ${asset.graphQueueFocusedCount} 个`, 'load-graph-queue-focused', 'secondary', busy || !asset.graphQueueFocusedCount)}
              ${actionButton(`载入全部 ${asset.graphQueueCount} 个`, 'load-graph-queue-all', 'ghost', busy || !asset.graphQueueCount)}
              ${actionButton('查看分页分类', 'inspect-graph-queue', 'ghost', busy)}
            </div>`
          : ''
      }
      ${renderGraphQueuePreview(asset)}
      <div class="queue-summary">
        <span>当前：${current ? escapeHtml(current.name) : '无'}</span>
        <strong>${escapeHtml(progress)}</strong>
      </div>
      <div class="queue-list">${list}</div>
      <div class="button-row">
        ${actionButton('保存队列当前项', 'capture-queue-current', 'primary', busy)}
        ${actionButton('跳过当前项', 'capture-queue-skip', 'secondary', busy)}
        ${actionButton('重置队列', 'capture-queue-reset', 'ghost', busy)}
        ${actionButton('清空队列', 'capture-queue-clear', 'ghost', busy)}
      </div>
    </div>
  `;
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
      ${renderCaptureQueue()}
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
        ${metric('跳过属性', `${quality?.skipped ?? '-'} / ${quality?.skippedAttempts ?? quality?.skipped ?? '-'}`)}
      </div>
      <div class="source-box">
        <strong>组件来源</strong>
        <ul>${sourceRows}</ul>
      </div>
    </section>
  `;
}

function renderNotesPanel(asset?: AssetSummary): string {
  const rows = missingFunctions;
  const selectedCount = selectedMissingFunctions.size;
  const content = rows.length
    ? rows
        .map((item) => {
          const checked = selectedMissingFunctions.has(item.function) ? 'checked' : '';
          return `
            <label class="review-row">
              <input type="checkbox" data-missing-function="${escapeHtml(item.function)}" ${checked} />
              <span>
                <strong>${escapeHtml(item.function)}</strong>
                <small>${escapeHtml(item.areas.join(', ') || '未分类')} - ${escapeHtml(item.sourceGraphs.slice(0, 4).join(', ') || '未知来源')}</small>
              </span>
            </label>
          `;
        })
        .join('')
    : '<div class="empty-state compact">暂无缺失函数队列。请先运行标准分析，或查看上下文复查报告。</div>';
  return `
    <section class="panel notes-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">notes.md 判定</p>
          <h2>把父类/原生函数移出误报</h2>
        </div>
        <span class="soft-label">${asset ? `${missingFunctions.length} 个待确认，已选 ${selectedCount}` : '未选择资产'}</span>
      </div>
      <div class="review-list">${content}</div>
      <div class="button-row">
        ${actionButton('全选队列', 'select-all-missing', 'secondary', !rows.length)}
        ${actionButton('清空选择', 'clear-missing-selection', 'ghost', !selectedCount)}
        ${actionButton('标记为父类/原生', 'mark-missing-inherited', 'primary', !asset || !selectedCount || busy)}
        ${actionButton('标记为忽略', 'mark-missing-ignore', 'secondary', !asset || !selectedCount || busy)}
        ${actionButton('打开 notes.md', 'open-notes', 'ghost', !asset)}
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

function currentAsset(): AssetSummary | undefined {
  return selectedAsset();
}

function renderTopbar(): string {
  return `
    <header class="topbar">
      <div class="topbar-title">
        <h1>蓝图分析工作台</h1>
        <small>从 .uasset 还原 Unreal/ARK Blueprint，再生成中文行为说明。</small>
      </div>
      <div class="top-actions">
        ${actionButton('刷新状态', 'refresh', 'ghost', busy)}
        ${actionButton('打开 captures 目录', 'open-capture-root', 'ghost')}
      </div>
    </header>
  `;
}

function readStatusBadge(asset?: AssetSummary): string {
  if (!asset) {
    const typedPath = normalizeObjectPathInput(devkitInput || state?.devkitAssetPath || '');
    if (typedPath) {
      return '<span class="status-line muted">路径格式已识别，但这个资产还没有读取进历史列表。点下面绿色按钮开始读取。</span>';
    }
    return '<span class="status-line muted">未识别。粘贴 /Game/... Object Path，或 Kaminan_server/... 这种 mod 相对路径。</span>';
  }
  if (!asset.hasUassetGraphRead) {
    return `<span class="status-line muted">这个资产还没有从 .uasset 读取过。点下面的“从 .uasset 读取图内容”开始。</span>`;
  }
  const total = asset.uassetReadGraphCount;
  const complete = asset.uassetReadCompleteCount;
  const partial = asset.uassetReadPartialCount;
  const need = asset.uassetReadNeedsClipboardCount;
  const tone = need > 0 ? 'danger' : partial > 0 ? 'warn' : 'good';
  return `
    <span class="status-line ${tone}">
      已读取 ${total} 个图页 · 完整 ${complete} · 部分 ${partial} · 需手动补 ${need}${asset.lastOutputAt ? ` · 最近分析 ${escapeHtml(asset.lastOutputAt)}` : ''}
    </span>
  `;
}

function renderStepPath(asset?: AssetSummary): string {
  const value = devkitInput || state?.devkitAssetPath || '';
  const typedAssetName = assetNameFromObjectPath(value);
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">1</span>
        <div class="step-title">
          <h2>粘贴蓝图 Object Path</h2>
          <p class="hint">在 ARK DevKit 里右键资产 → <code>Copy Reference</code>，把整段路径粘贴到下面。例如 <code>/Game/ASA/Dinos/Gigantoraptor/Gigantoraptor_Character_BP.Gigantoraptor_Character_BP</code>，或 <code>Kaminan_server/.../Asset.Asset</code>。</p>
        </div>
      </div>
      <textarea id="devkit-path" spellcheck="false" placeholder="/Game/ASA/.../Asset.Asset 或 Kaminan_server/.../Asset.Asset">${escapeHtml(value)}</textarea>
      <div class="status-row">
        <strong>已选资产：</strong>
        <span class="asset-name">${escapeHtml(asset?.name || typedAssetName || '无')}</span>
        ${readStatusBadge(asset)}
      </div>
    </section>
  `;
}

function renderStepActions(asset?: AssetSummary): string {
  const readPath = devkitInput || state?.devkitAssetPath || '';
  const canRead = !busy && Boolean(readPath.trim());
  const canAnalyze = !busy && Boolean(asset && asset.graphs);
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">2</span>
        <div class="step-title">
          <h2>读取并生成报告</h2>
          <p class="hint">第一次操作只需要点左边绿色按钮，生成当前 revision 的 Evidence Store 和 AI 索引。右边只在需要人类长报告时使用。</p>
        </div>
      </div>
      <div class="big-action-row">
        <button class="big-btn primary" data-action="read-uasset-graphs" ${canRead ? '' : 'disabled'}>
          <strong>从 .uasset 读取图内容</strong>
          <small>解析 .uasset / .uexp，默认生成低 token 证据库和 AI 索引。</small>
        </button>
        <button class="big-btn secondary" data-action="analyze-standard" ${canAnalyze ? '' : 'disabled'}>
          <strong>生成 / 刷新人类报告</strong>
          <small>按同一 Object Path 以 dual 模式重读，再生成匹配当前 revision 的 asset_report 等。</small>
        </button>
      </div>
      ${
        activeJobId
          ? `<div class="job-bar"><span>正在后台执行：${escapeHtml(activeJobLabel || '任务')}……可以等它跑完，也可以取消。</span>${actionButton('取消任务', 'cancel-job', 'danger')}</div>`
          : ''
      }
      ${mainNotice ? `<div class="action-notice ${mainNoticeTone}">${escapeHtml(mainNotice)}</div>` : ''}
    </section>
  `;
}

function renderStepResult(asset?: AssetSummary): string {
  if (!asset || !asset.hasUassetGraphRead) {
    return `
      <section class="panel step-panel">
        <div class="step-head">
          <span class="step-num">3</span>
          <div class="step-title">
            <h2>读取结果</h2>
            <p class="hint">点完上面那个按钮以后，这里会出现：读取了多少图页，多少完整、多少部分、多少需要手动补。</p>
          </div>
        </div>
        <div class="empty-state">还没有读取过这个资产。</div>
      </section>
    `;
  }
  const partial = asset.uassetReadPartialCount;
  const need = asset.uassetReadNeedsClipboardCount;
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">3</span>
        <div class="step-title">
          <h2>读取结果</h2>
          <p class="hint">这是工具从 .uasset 文件里成功还原出多少图页的统计。</p>
        </div>
      </div>
      <div class="result-grid">
        <div class="result-tile good">
          <span class="tile-num">${asset.uassetReadCompleteCount}</span>
          <strong>已完整读取</strong>
          <small>节点、连线、属性都还原成功，可以直接信任报告里这部分内容。</small>
        </div>
        <div class="result-tile ${partial ? 'warn' : 'idle'}">
          <span class="tile-num">${partial}</span>
          <strong>部分读取</strong>
          <small>能看，但有些连线或字段是工具猜出来的（启发式）。报告里相关说明仅供参考，必要时再补采。</small>
        </div>
        <div class="result-tile ${need ? 'danger' : 'idle'}">
          <span class="tile-num">${need}</span>
          <strong>需要手动补充</strong>
          <small>这些图页 .uasset 解析失败，需要回 DevKit 里复制粘贴。下方会自动出现“补采”面板。</small>
        </div>
      </div>
      <div class="result-meta">共 ${asset.uassetReadGraphCount} 个图页 · 节点 ${asset.uassetReadNodeCount} · pin ${asset.uassetReadPinCount} · 连线 ${asset.uassetReadLinkCount}</div>
    </section>
  `;
}

function reportTile(
  key: ReportKey,
  title: string,
  hint: string,
  asset?: AssetSummary,
  missingHint = '尚未生成 — 先完成第 2 步“读取图内容”。',
): string {
  const exists = Boolean(asset?.reports?.[key]);
  return `
    <button class="report-tile ${exists ? '' : 'missing'}" data-action="open-report-${key}" ${exists ? '' : 'disabled'}>
      <strong>${escapeHtml(title)}</strong>
      <small>${escapeHtml(exists ? hint : missingHint)}</small>
    </button>
  `;
}

function renderStepReports(asset?: AssetSummary): string {
  const legacyHint = asset?.preservedLegacyReports
    ? '这是保留的 legacy 文件，可能早于当前 evidence revision；需要最新人类报告时请点“生成 / 刷新人类报告”。'
    : 'legacy 人类报告；indexed 默认不生成，需要时请显式重新分析。';
  const legacyMissing = 'indexed 默认只生成 AI 证据索引；需要这份人类报告时请点“生成 / 刷新人类报告”。';
  const tiles = [
    reportTile('agent_index', 'AI 证据索引 (agent_index)', `默认给 AI 的低 token 入口；按 Evidence ID 搜索和下钻，revision ${asset?.evidenceRevision || '-'}。`, asset),
    reportTile('context_pack', '问题上下文包 (context_pack)', `默认给 GPT 的小上下文，候选 ${asset?.formulaCandidateCount || 0} 个，未解析 ${asset?.unresolvedFormulaCount || 0} 个。`, asset),
    reportTile('asset_memory_card', '资产小卡片 (asset_memory_card)', '几 KB 级资产记忆卡，只保留身份、摘要、关键默认值和证据指针。', asset),
    reportTile('formula_candidates', '公式候选 (formula_candidates)', '概率、属性、XP、掉落、Buff 等机制候选；不会写成最终公式。', asset),
    reportTile('unresolved_formulas', 'unresolved formulas', '查看 native、父类、heuristic 连线等公式阻塞原因和下一步验证。', asset),
    reportTile('asset_report', '完整报告（历史/按需报告）', legacyHint, asset, legacyMissing),
    reportTile('behavior_summary', '行为说明（历史/按需报告）', legacyHint, asset, legacyMissing),
    reportTile('diagnostics_report', '诊断报告（历史/按需报告）', legacyHint, asset, legacyMissing),
    reportTile('call_graph_summary', '调用关系摘要（历史/按需报告）', legacyHint, asset, legacyMissing),
  ].join('');
  const allTabs = reportTargets.map((k) => reportButton(k, asset)).join('');
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">4</span>
        <div class="step-title">
          <h2>打开索引 / 按需报告</h2>
          <p class="hint">AI 默认读当前 revision 的证据索引；保留的 legacy Markdown 可能来自旧 revision，卡片会明确标注。</p>
        </div>
      </div>
      <div class="report-tile-grid">${tiles}</div>
      <details class="more-reports">
        <summary>查看预览 / 更多分项报告</summary>
        <div class="report-tabs">${allTabs}</div>
        ${renderReportPreview(asset)}
        <div class="button-row tight">
          ${actionButton('在编辑器里打开当前预览', 'open-current-report', 'secondary', !asset || !asset.reports[selectedReport])}
          ${actionButton('打开 graph_reports 目录', 'open-graph-reports', 'ghost', !asset)}
          ${actionButton('打开输出目录', 'open-output', 'ghost', !asset || !asset.hasOutput)}
        </div>
      </details>
    </section>
  `;
}

function renderRecaptureSection(asset?: AssetSummary): string {
  if (!asset || !asset.hasUassetGraphRead) return '';
  const need = asset.uassetReadNeedsClipboardCount;
  const partial = asset.uassetReadPartialCount;
  if (!need && !partial) return '';
  return `
    <section class="panel step-panel alert">
      <div class="step-head">
        <span class="step-num warn">!</span>
        <div class="step-title">
          <h2>需要手动补采的图页</h2>
          <p class="hint">.uasset 里有 <strong>${need}</strong> 个图页解析失败，<strong>${partial}</strong> 个只能部分还原。补采办法：在 DevKit 里打开对应蓝图的图页，按 <code>Ctrl+A</code>、<code>Ctrl+C</code>，回到这里展开下方的补采面板粘贴保存。</p>
        </div>
      </div>
      <div class="button-row">
        ${actionButton('载入失败图页到补采队列', 'load-uasset-failed-queue', 'primary', !asset.hasUassetGraphRead)}
        ${actionButton('查看部分读取的原因', 'open-uasset-triage', 'secondary', !asset.reports.uasset_partial_graph_triage)}
        ${actionButton('查看资产解析诊断', 'open-uasset-diagnostics', 'secondary', !asset.reports.uasset_graph_read_report)}
      </div>
      <details class="recapture-detail">
        <summary>展开补采面板（粘贴 DevKit 剪贴板内容）</summary>
        ${renderCapturePanel(asset)}
      </details>
    </section>
  `;
}

function renderAdvancedDevkit(): string {
  return `
    <section class="advanced-card">
      <h3>DevKit 导出辅助</h3>
      <p class="hint">把当前路径写入 DevKit 请求文件，复制 Python 命令贴到 DevKit Output Log。一般只在 .uasset 解析不出来、或想用 DevKit 重新导出默认值时才用。</p>
      <div class="button-row">
        ${actionButton('保存路径并复制 Python 命令', 'save-devkit-request', 'primary', busy)}
        ${actionButton('复制 Python 命令', 'copy-python-command', 'secondary')}
        ${actionButton('复制 Output Log 命令', 'copy-output-command', 'secondary')}
      </div>
      <div class="button-row">
        ${actionButton('从 .uasset 提取分页候选名', 'mine-uasset-candidates', 'ghost', busy)}
        ${actionButton('提取并复制 DevKit 验证命令', 'mine-uasset-candidates-copy', 'ghost', busy)}
      </div>
      <small class="path-line">请求文件：${escapeHtml(state?.devkitRequestPath || '')}</small>
    </section>
  `;
}

function renderAdvancedAnalyze(asset?: AssetSummary): string {
  return `
    <section class="advanced-card">
      <h3>更多分析模式与质检</h3>
      <p class="hint">普通用户用主流程的“重新生成完整报告”就够。下面是给开发者排查问题用的。</p>
      <div class="button-row">
        ${actionButton('生成 compact 报告（精简）', 'analyze-compact', 'secondary', !asset || !asset.graphs || busy)}
        ${actionButton('生成 debug 报告（含调试信息）', 'analyze-debug', 'danger', !asset || !asset.graphs || busy)}
      </div>
      <div class="button-row">
        ${actionButton('查看连线解析', 'open-uasset-links', 'ghost', !asset?.reports.uasset_link_resolution_report)}
        ${actionButton('查看读取质量自检', 'open-uasset-gates', 'ghost', !asset?.reports.uasset_quality_gates)}
        ${actionButton('和剪贴板复制对比', 'open-uasset-compare', 'ghost', !asset?.reports.uasset_vs_clipboard_compare)}
      </div>
    </section>
  `;
}

function renderKnowledgeBaseSection(): string {
  const kb = state?.knowledgeBase;
  const status = kb?.exists
    ? `已生成 ${escapeHtml(kb.generated || '未知时间')}，全局索引 ${escapeHtml(kb.globalAssetCount || 0)} 个 .uasset，专题深读 ${escapeHtml(kb.assetCount || 0)} 个资产。`
    : '还没有生成。会先建立全局 DevKit 资产索引，再生成巨盗龙专题样本。';
  return `
    <section class="panel knowledge-panel">
      <div class="step-head">
        <span class="step-num">KB</span>
        <div class="step-title">
          <h2>背景知识库</h2>
          <p class="hint">先扫整个 ARK DevKit 的 .uasset 资产作为底座，再把已深度读取的蓝图合成专题机制地图。</p>
        </div>
      </div>
      <div class="status-row">
        <strong>当前状态：</strong>
        <span>${status}</span>
      </div>
      <div class="button-row">
        ${actionButton('生成/更新全局知识库', 'build-knowledge-base', 'primary', busy)}
        ${actionButton('自动解析第一批重点资产', 'read-priority-assets', 'primary', busy || !kb?.priorityQueueExists)}
        ${actionButton('打开五类补读清单', 'open-knowledge-priority-report', 'secondary', !kb?.priorityReportExists)}
        ${actionButton('打开自动解析结果', 'open-knowledge-priority-results', 'secondary', !kb?.priorityResultsExists)}
        ${actionButton('打开知识库报告', 'open-knowledge-report', 'secondary', !kb?.reportExists)}
        ${actionButton('打开全局资产索引', 'open-knowledge-global-report', 'secondary', !kb?.globalReportExists)}
        ${actionButton('打开知识库目录', 'open-knowledge-folder', 'ghost', !kb?.exists)}
      </div>
      ${kb?.reportExists ? `<small class="path-line">${escapeHtml(kb.reportPath)}</small>` : ''}
    </section>
  `;
}

function renderAssetHistory(asset?: AssetSummary): string {
  if (!state?.assets.length) {
    return `
      <section class="advanced-card">
        <h3>历史资产</h3>
        <div class="empty-state compact">还没有处理过任何资产。</div>
      </section>
    `;
  }
  return `
    <section class="advanced-card">
      <h3>历史资产</h3>
      <p class="hint">点一行会把那个资产的路径填到顶部输入框，方便切换。</p>
      <div class="asset-list">${renderAssetList(asset)}</div>
    </section>
  `;
}

function renderAdvancedLog(): string {
  return `
    <section class="advanced-card">
      <h3>运行日志</h3>
      <pre class="log-output">${escapeHtml(logs.join('\n'))}</pre>
    </section>
  `;
}

function renderAdvancedSection(asset?: AssetSummary): string {
  return `
    <details class="advanced-section">
      <summary>高级功能（DevKit 导出、对比、debug、notes 判定、历史资产、日志）</summary>
      <div class="advanced-grid">
        ${renderAdvancedDevkit()}
        ${renderAdvancedAnalyze(asset)}
        ${renderQualityPanel(asset)}
        ${renderNotesPanel(asset)}
        ${renderComparePanel()}
        ${renderAssetHistory(asset)}
        ${renderAdvancedLog()}
      </div>
    </details>
  `;
}

function renderMain(): void {
  const asset = currentAsset();
  if (asset && asset.path !== selectedPath) {
    selectedPath = asset.path;
    window.localStorage.setItem('blueprint-tool.selected', selectedPath);
  }

  root.innerHTML = `
    <div class="shell">
      ${renderTopbar()}
      <main class="workspace">
        ${renderStepPath(asset)}
        ${renderStepActions(asset)}
        ${renderStepResult(asset)}
        ${renderStepReports(asset)}
        ${renderKnowledgeBaseSection()}
        ${renderRecaptureSection(asset)}
        ${renderAdvancedSection(asset)}
        <p class="footnote">日志最近一条：${escapeHtml(logs[0] || '无')}</p>
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
  const nextQueueText = document.querySelector<HTMLTextAreaElement>('#capture-queue-text')?.value;
  captureAssetName = document.querySelector<HTMLInputElement>('#capture-asset-name')?.value || captureAssetName;
  captureGraphName = document.querySelector<HTMLInputElement>('#capture-graph-name')?.value || captureGraphName;
  captureGraphType = document.querySelector<HTMLSelectElement>('#capture-graph-type')?.value || captureGraphType;
  if (typeof nextQueueText === 'string' && nextQueueText !== captureQueueText) {
    captureQueueText = nextQueueText;
    captureQueueCursor = 0;
    saveCaptureQueueState();
  }
  devkitInput = document.querySelector<HTMLTextAreaElement>('#devkit-path')?.value || devkitInput;
  compareOldPath = document.querySelector<HTMLSelectElement>('#compare-old')?.value || compareOldPath;
  compareNewPath = document.querySelector<HTMLSelectElement>('#compare-new')?.value || compareNewPath;
}

function bindEvents(): void {
  document.querySelectorAll<HTMLButtonElement>('[data-select-asset]').forEach((button) => {
    button.addEventListener('click', () => {
      syncInputs();
      selectedPath = button.dataset.selectAsset || '';
      devkitInput = selectedPath;
      window.localStorage.setItem('blueprint-tool.selected', selectedPath);
      captureAssetName = selectedAsset()?.name || captureAssetName;
      selectedReport = preferredReportForAsset(selectedAsset());
      reportContent = '';
      reportPath = '';
      void loadReport(selectedReport);
      void loadMissingFunctions();
      render();
    });
  });

  const devkitField = document.querySelector<HTMLTextAreaElement>('#devkit-path');
  if (devkitField) {
    devkitField.addEventListener('input', () => {
      devkitInput = devkitField.value;
      document
        .querySelector<HTMLButtonElement>('[data-action="read-uasset-graphs"]')
        ?.toggleAttribute('disabled', busy || !devkitInput.trim());
    });
    devkitField.addEventListener('change', () => {
      syncInputs();
      render();
    });
  }

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

  document.querySelectorAll<HTMLInputElement>('[data-missing-function]').forEach((input) => {
    input.addEventListener('change', () => {
      const name = input.dataset.missingFunction || '';
      if (!name) {
        return;
      }
      if (input.checked) {
        selectedMissingFunctions.add(name);
      } else {
        selectedMissingFunctions.delete(name);
      }
      render();
    });
  });

  document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('input, textarea, select').forEach((input) => {
    input.addEventListener('input', syncInputs);
    input.addEventListener('change', syncInputs);
  });
}

async function refreshState(keepReport = true): Promise<void> {
  const previousSelectedPath = selectedPath;
  const payload = await api<AppState>('/api/state');
  state = payload;
  if (!selectedPath || !state.assets.some((asset) => asset.path === selectedPath)) {
    selectedPath = state.assets.find((asset) => asset.graphs > 0 && asset.hasOutput)?.path || state.assets[0]?.path || '';
  }
  if (previousSelectedPath && previousSelectedPath !== selectedPath) {
    missingFunctions = [];
    selectedMissingFunctions.clear();
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
  const asset = selectedAsset();
  if (asset && (previousSelectedPath !== selectedPath || !asset.reports[selectedReport])) {
    selectedReport = preferredReportForAsset(asset);
  }
  if (selectedPath) {
    await loadMissingFunctions(false);
  }
  render();
  if (keepReport && selectedPath) {
    await loadReport(selectedReport, false);
    render();
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
  if (rerender) {
    render();
  }
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

async function loadMissingFunctions(rerender = true): Promise<void> {
  const asset = selectedAsset();
  if (!asset || (!asset.reports.context_review && !asset.reports.notes_todo)) {
    missingFunctions = [];
    selectedMissingFunctions.clear();
    if (rerender) {
      render();
    }
    return;
  }
  try {
    const query = new URLSearchParams({ assetPath: asset.path });
    const payload = await api<ApiResult & { items: MissingFunctionItem[] }>(`/api/missing-functions?${query}`);
    missingFunctions = payload.items || [];
    const available = new Set(missingFunctions.map((item) => item.function));
    selectedMissingFunctions = new Set([...selectedMissingFunctions].filter((name) => available.has(name)));
  } catch (error) {
    missingFunctions = [];
    selectedMissingFunctions.clear();
    appendLog(error instanceof Error ? error.message : String(error));
  } finally {
    if (rerender) {
      render();
    }
  }
}

function selectedMissingFunctionNames(): string[] {
  const available = new Set(missingFunctions.map((item) => item.function));
  return [...selectedMissingFunctions].filter((name) => available.has(name));
}

async function appendMissingNotes(kind: 'inherited' | 'ignore_missing'): Promise<void> {
  const asset = selectedAsset();
  const functions = selectedMissingFunctionNames();
  if (!asset || !functions.length) {
    appendLog('请先选择要判定的缺失函数。');
    return;
  }
  busy = true;
  appendLog(`正在写入 notes.md：${functions.length} 个函数。`);
  try {
    const payload = await api<ApiResult & { notesPath: string; added: string[]; skipped: string[]; items: MissingFunctionItem[] }>(
      '/api/notes-append',
      {
        method: 'POST',
        body: JSON.stringify({
          assetPath: asset.path,
          kind,
          functions,
          reason: kind === 'inherited' ? '在控制中心确认属于父类/原生实现。' : '在控制中心确认暂不作为本资产漏采图页处理。',
        }),
      },
    );
    missingFunctions = payload.items || [];
    selectedMissingFunctions.clear();
    appendLog(`已更新 notes.md：新增 ${payload.added.length} 个，跳过重复 ${payload.skipped.length} 个。`);
    appendLog('正在重新生成标准报告，让报告预览同步 notes.md 判定。');
    const analysisPayload = await api<ApiResult & { job: JobInfo }>(
      '/api/analyze',
      {
        method: 'POST',
        body: JSON.stringify({ assetPath: asset.path, reportLevel: 'standard' }),
      },
    );
    activeJobId = analysisPayload.job.id;
    activeJobLabel = 'notes 后分析';
    render();
    const job = await waitForJob(analysisPayload.job.id, 'notes 后分析');
    const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
    appendLog(`notes 后分析${outcome}，耗时 ${job.durationSeconds}s。`);
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
      await loadReport('context_review');
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

async function runAnalysis(reportLevel: 'compact' | 'standard' | 'debug'): Promise<void> {
  const asset = selectedAsset();
  if (!asset) {
    appendLog('还没有选择资产。');
    return;
  }
  busy = true;
  appendLog(`开始为 ${asset.name} 刷新来源并生成 ${reportLevel} 人类报告。`);
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
      selectedReport = 'context_pack';
      await loadReport('context_pack');
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

async function capturePage(analyzeAfter: boolean, allowOverwrite = false, fromQueue = false): Promise<void> {
  syncInputs();
  const queueItem = fromQueue ? currentCaptureQueueItem() : undefined;
  if (fromQueue && !queueItem) {
    appendLog('图页采集队列里没有当前项。请先粘贴分页名称，或重置队列。');
    return;
  }
  const graphName = (queueItem?.name || captureGraphName).trim();
  const graphType = queueItem?.type || captureGraphType;
  if (!graphName) {
    appendLog('保存剪贴板图页前，需要先填写图页名。');
    return;
  }
  busy = true;
  appendLog(`正在从剪贴板采集图页：“${graphName}”。`);
  try {
    const asset = selectedAsset();
    const payload = await api<ApiResult & { asset: AssetSummary; graphPath: string; record: { warnings?: string[]; backup_path?: string }; analysisJob?: JobInfo }>(
      '/api/capture-graph',
      {
        method: 'POST',
        body: JSON.stringify({
          assetPath: asset?.path || '',
          assetName: captureAssetName,
          graphName,
          graphType,
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
    if (fromQueue) {
      const total = captureQueueItems().length;
      captureQueueCursor = Math.min(captureQueueCursor + 1, total);
      saveCaptureQueueState();
      const next = currentCaptureQueueItem();
      appendLog(next ? `队列已前进到下一项：${next.name}` : '队列已保存完。可以运行标准分析。');
    } else {
      captureGraphName = '';
    }
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
        selectedReport = 'context_pack';
        await loadReport('context_pack');
      }
    }
  } catch (error) {
    if (error instanceof ApiFailure && error.code === 'overwrite_required') {
      busy = false;
      render();
      const ok = window.confirm('这个图页已经存在。要覆盖它吗？旧文件会自动备份到 graphs/_backups/。');
      if (ok) {
        await capturePage(analyzeAfter, true, fromQueue);
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

async function loadGraphQueueFromAsset(mode = 'all', applyToQueue = true): Promise<void> {
  const asset = selectedAsset();
  if (!asset) {
    appendLog('请先选择一个资产。');
    return;
  }
  try {
    const query = new URLSearchParams({ assetPath: asset.path, mode });
    const payload = await api<ApiResult & { path: string; content: string; summary: GraphQueueSummary }>(`/api/graph-queue?${query}`);
    graphQueueSummary = payload.summary;
    graphQueueSummaryAssetPath = asset.path;
    if (!payload.content.trim()) {
      appendLog('这个资产还没有 graph_queue.txt。请先在 DevKit 里运行默认值导出器。');
      render();
      return;
    }
    if (applyToQueue) {
      captureQueueText = payload.content;
      captureQueueCursor = 0;
      saveCaptureQueueState();
      appendLog(`已载入${graphQueueModeLabel(mode)}：${parseCaptureQueue(payload.content).length} 个，来源 ${payload.path}`);
    } else {
      appendLog(
        `分页分类：推荐 ${payload.summary.recommended}，可选 ${payload.summary.optional}，暂不采集 ${payload.summary.deferred}，全量 ${payload.summary.total}。`,
      );
    }
    render();
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
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

async function mineUassetCandidates(copyCommand: boolean): Promise<void> {
  syncInputs();
  if (!devkitInput) {
    appendLog('请先粘贴目标蓝图 Object Path。');
    return;
  }
  busy = true;
  render();
  try {
    const payload = await api<
      ApiResult & {
        assetPath: string;
        uassetPath: string;
        candidateCount: number;
        rawStringCount: number;
        jsonPath: string;
        pythonCommand: string;
        structure?: {
          loaded?: boolean;
          graph_exports_count?: number;
          function_graph_exports_count?: number;
          collapsed_graph_exports_count?: number;
          standalone_graph_exports_count?: number;
          function_exports_count?: number;
        };
      }
    >('/api/uasset-candidates', {
      method: 'POST',
      body: JSON.stringify({ assetPath: devkitInput }),
    });
    devkitInput = payload.assetPath;
    appendLog(
      `已从 .uasset 提取 ${payload.candidateCount} 个分页候选名；源文件：${payload.uassetPath || '未找到本地 .uasset'}`,
    );
    if (payload.structure?.loaded) {
      appendLog(
        `结构解析：EdGraph ${payload.structure.graph_exports_count ?? 0} 个，函数图 ${payload.structure.function_graph_exports_count ?? 0} 个，折叠图 ${payload.structure.collapsed_graph_exports_count ?? 0} 个，独立图 ${payload.structure.standalone_graph_exports_count ?? 0} 个。`,
      );
    }
    appendLog(`候选文件：${payload.jsonPath}`);
    if (copyCommand) {
      await copyText(payload.pythonCommand, 'DevKit Python 验证命令');
    }
    await refreshState(false);
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  } finally {
    busy = false;
    render();
  }
}

async function readUassetGraphs(): Promise<void> {
  syncInputs();
  if (!devkitInput) {
    setMainNotice('请先粘贴目标蓝图 Object Path。', 'warn');
    return;
  }
  busy = true;
  mainNotice = `正在读取：${normalizeObjectPathInput(devkitInput) || devkitInput}`;
  mainNoticeTone = 'info';
  render();
  try {
    const payload = await api<
      ApiResult & {
        assetPath: string;
        uassetPath: string;
        graphCount: number;
        nodeCount: number;
        pinCount: number;
        linkCount: number;
        graphReportPath: string;
        agentIndexPath?: string;
        artifactMode?: string;
        analysisJob?: JobInfo;
      }
    >('/api/uasset-graphs', {
      method: 'POST',
      body: JSON.stringify({
        assetPath: devkitInput,
        analyzeAfter: true,
        reportLevel: 'standard',
        artifactMode: DEFAULT_ARTIFACT_MODE,
      }),
    });
    devkitInput = payload.assetPath;
    setMainNotice(
      `已从 .uasset 读取 ${payload.graphCount} 个图、${payload.nodeCount} 个节点、${payload.pinCount} 个 pin、${payload.linkCount} 条候选连线。`,
      'good',
    );
    if (payload.graphReportPath) {
      appendLog(`资产解析报告：${payload.graphReportPath}`);
    }
    if (payload.agentIndexPath) {
      appendLog(`AI 证据索引：${payload.agentIndexPath}`);
    }
    await refreshState(false);
    if (payload.analysisJob) {
      activeJobId = payload.analysisJob.id;
      activeJobLabel = '.uasset 分析';
      render();
      const job = await waitForJob(payload.analysisJob.id, '.uasset 分析');
      const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
      appendLog(`.uasset 图内容分析${outcome}，耗时 ${job.durationSeconds}s。`);
      if (job.stderr) {
        appendLog(job.stderr.trim().slice(-1200));
      }
      await refreshState(false);
      if (job.status === 'succeeded') {
        selectedReport = 'agent_index';
        await loadReport('agent_index');
      }
    } else if (payload.agentIndexPath) {
      selectedReport = 'agent_index';
      await loadReport('agent_index');
    }
  } catch (error) {
    setMainNotice(readableError(error), 'danger');
  } finally {
    busy = false;
    activeJobId = '';
    activeJobLabel = '';
    render();
  }
}

async function loadUassetFailedQueue(): Promise<void> {
  const asset = selectedAsset();
  if (!asset) {
    appendLog('请先选择一个资产。');
    return;
  }
  try {
    const query = new URLSearchParams({ assetPath: asset.path });
    const payload = await api<ApiResult & { path: string; content: string; summary: GraphQueueSummary }>(`/api/uasset-failed-queue?${query}`);
    if (!payload.content.trim()) {
      appendLog('没有需要手动补采的 .uasset 失败图页。');
      return;
    }
    captureQueueText = payload.content;
    captureQueueCursor = 0;
    graphQueueSummary = payload.summary;
    graphQueueSummaryAssetPath = asset.path;
    saveCaptureQueueState();
    appendLog(`已载入 .uasset 失败补采队列：${parseCaptureQueue(payload.content).length} 个，来源 ${payload.path}`);
    render();
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

async function buildKnowledgeBase(): Promise<void> {
  busy = true;
  appendLog('开始生成 ARK DevKit 全局背景知识库。');
  try {
    const payload = await api<ApiResult & { job: JobInfo }>(
      '/api/knowledge-base/build',
      {
        method: 'POST',
        body: JSON.stringify({
          focus: 'gigantoraptor',
          assets: [
            'Gigantoraptor_Character_BP',
            'PrimalItemResource_GigantoraptorFeather',
            'Buff_GigantoraptorCallPlayer',
          ],
        }),
      },
    );
    activeJobId = payload.job.id;
    activeJobLabel = '背景知识库';
    appendLog(`知识库后台任务已创建：${payload.job.id}`);
    render();
    const job = await waitForJob(payload.job.id, '背景知识库');
    const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
    appendLog(`知识库生成${outcome}，耗时 ${job.durationSeconds}s。`);
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
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  } finally {
    busy = false;
    activeJobId = '';
    activeJobLabel = '';
    render();
  }
}

async function readPriorityAssets(): Promise<void> {
  busy = true;
  appendLog('开始小批量读取重点资产，并生成行为报告与质量评估。');
  try {
    const payload = await api<ApiResult & { job: JobInfo }>(
      '/api/knowledge-base/read-priority',
      {
        method: 'POST',
        body: JSON.stringify({ limit: 25, analyze: true }),
      },
    );
    activeJobId = payload.job.id;
    activeJobLabel = '重点资产自动解析';
    appendLog(`重点资产解析后台任务已创建：${payload.job.id}`);
    render();
    const job = await waitForJob(payload.job.id, '重点资产自动解析');
    const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
    appendLog(`重点资产自动解析${outcome}，耗时 ${job.durationSeconds}s。`);
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
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  } finally {
    busy = false;
    activeJobId = '';
    activeJobLabel = '';
    render();
  }
}

async function openKnowledgeBase(
  target: 'report' | 'folder' | 'index' | 'global_report' | 'priority_report' | 'priority_results' = 'report',
): Promise<void> {
  try {
    const payload = await api<ApiResult & { path: string }>('/api/knowledge-base/open', {
      method: 'POST',
      body: JSON.stringify({ target }),
    });
    appendLog(`已打开：${payload.path}`);
  } catch (error) {
    appendLog(error instanceof Error ? error.message : String(error));
  }
}

async function handleAction(action: string): Promise<void> {
  if (action.startsWith('open-report-')) {
    const key = action.slice('open-report-'.length) as ReportKey;
    if (reportTargets.includes(key)) {
      selectedReport = key;
      await openTarget(key);
      await loadReport(key);
    }
    return;
  }
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
  if (action === 'build-knowledge-base') {
    await buildKnowledgeBase();
    return;
  }
  if (action === 'read-priority-assets') {
    await readPriorityAssets();
    return;
  }
  if (action === 'open-knowledge-report') {
    await openKnowledgeBase('report');
    return;
  }
  if (action === 'open-knowledge-global-report') {
    await openKnowledgeBase('global_report');
    return;
  }
  if (action === 'open-knowledge-priority-report') {
    await openKnowledgeBase('priority_report');
    return;
  }
  if (action === 'open-knowledge-priority-results') {
    await openKnowledgeBase('priority_results');
    return;
  }
  if (action === 'open-knowledge-folder') {
    await openKnowledgeBase('folder');
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
  if (action === 'load-graph-queue-compact') {
    await loadGraphQueueFromAsset('compact', true);
    return;
  }
  if (action === 'load-graph-queue-recommended') {
    await loadGraphQueueFromAsset('recommended', true);
    return;
  }
  if (action === 'load-graph-queue-focused') {
    await loadGraphQueueFromAsset('focused', true);
    return;
  }
  if (action === 'load-graph-queue-all') {
    await loadGraphQueueFromAsset('all', true);
    return;
  }
  if (action === 'inspect-graph-queue') {
    await loadGraphQueueFromAsset('all', false);
    return;
  }
  if (action === 'capture-queue-current') {
    await capturePage(false, false, true);
    return;
  }
  if (action === 'capture-queue-skip') {
    syncInputs();
    const current = currentCaptureQueueItem();
    if (!current) {
      appendLog('队列里没有可跳过的当前项。');
      return;
    }
    captureQueueCursor = Math.min(captureQueueCursor + 1, captureQueueItems().length);
    saveCaptureQueueState();
    appendLog(`已跳过队列项：${current.name}`);
    render();
    return;
  }
  if (action === 'capture-queue-reset') {
    syncInputs();
    captureQueueCursor = 0;
    saveCaptureQueueState();
    appendLog('图页采集队列已重置到第一项。');
    render();
    return;
  }
  if (action === 'capture-queue-clear') {
    captureQueueText = '';
    captureQueueCursor = 0;
    saveCaptureQueueState();
    appendLog('图页采集队列已清空。');
    render();
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
  if (action === 'select-all-missing') {
    selectedMissingFunctions = new Set(missingFunctions.map((item) => item.function));
    appendLog(`已选择 ${selectedMissingFunctions.size} 个缺失函数。`);
    render();
    return;
  }
  if (action === 'clear-missing-selection') {
    selectedMissingFunctions.clear();
    appendLog('已清空缺失函数选择。');
    render();
    return;
  }
  if (action === 'mark-missing-inherited') {
    await appendMissingNotes('inherited');
    return;
  }
  if (action === 'mark-missing-ignore') {
    await appendMissingNotes('ignore_missing');
    return;
  }
  if (action === 'open-notes') {
    await openTarget('notes');
    return;
  }
  if (action === 'save-devkit-request') {
    await saveDevkitRequest();
    return;
  }
  if (action === 'mine-uasset-candidates') {
    await mineUassetCandidates(false);
    return;
  }
  if (action === 'mine-uasset-candidates-copy') {
    await mineUassetCandidates(true);
    return;
  }
  if (action === 'read-uasset-graphs') {
    await readUassetGraphs();
    return;
  }
  if (action === 'open-uasset-diagnostics') {
    await openTarget('uasset_graph_read_report');
    return;
  }
  if (action === 'open-uasset-links') {
    await openTarget('uasset_link_resolution_report');
    return;
  }
  if (action === 'open-uasset-triage') {
    await openTarget('uasset_partial_graph_triage');
    return;
  }
  if (action === 'open-uasset-gates') {
    await openTarget('uasset_quality_gates');
    return;
  }
  if (action === 'open-uasset-compare') {
    await openTarget('uasset_vs_clipboard_compare');
    return;
  }
  if (action === 'load-uasset-failed-queue') {
    await loadUassetFailedQueue();
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
