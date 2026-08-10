import { ApiFailure, api, type ApiResult } from '../shared/api';
import { readableError } from '../shared/errors';
import { escapeHtml } from '../shared/html';
import {
  graphQueueModeLabel,
  parseCaptureQueue,
} from './capture-queue';
import type {
  AdvancedViewModel,
} from './views/advanced';
import {
  renderAdvancedSection,
  renderKnowledgeBaseSection,
} from './views/advanced';
import type { CaptureViewModel } from './views/capture';
import { renderRecaptureSection } from './views/capture';
import {
  actionButton,
  defaultReport,
  preferredReportForAsset,
  reportTargets,
} from './views/common';
import type { WorkflowViewModel } from './views/workflow';
import {
  renderStepActions,
  renderStepPath,
  renderStepReports,
  renderStepResult,
} from './views/workflow';
import type {
  AppState,
  AssetSummary,
  CaptureQueueItem,
  GraphQueueSummary,
  JobInfo,
  KnowledgeOpenTarget,
  MainNoticeTone,
  MissingFunctionItem,
  OpenTarget,
  ReportKey,
  ReportLevel,
} from './types';


const DEFAULT_ARTIFACT_MODE = 'indexed' as const;


export class LegacyControlCenterWorkspace {
  private state: AppState | null = null;
  private appVersion = '';
  private selectedPath = window.localStorage.getItem('blueprint-tool.selected') || '';
  private selectedReport: ReportKey = defaultReport;
  private reportContent = '';
  private reportPath = '';
  private reportLoading = false;
  private busy = false;
  private devkitInput = '';
  private captureAssetName = '';
  private captureGraphName = '';
  private captureGraphType = 'Unknown';
  private captureQueueText = window.localStorage.getItem('blueprint-tool.captureQueue') || '';
  private captureQueueCursor = Number(window.localStorage.getItem('blueprint-tool.captureQueueCursor') || '0') || 0;
  private compareOldPath = '';
  private compareNewPath = '';
  private compareContent = '';
  private comparePath = '';
  private logs: string[] = ['控制中心已就绪。请选择资产、采集图页，或重新生成分析报告。'];
  private activeJobId = '';
  private activeJobLabel = '';
  private mainNotice = '';
  private mainNoticeTone: MainNoticeTone = 'info';
  private missingFunctions: MissingFunctionItem[] = [];
  private selectedMissingFunctions = new Set<string>();
  private graphQueueSummary: GraphQueueSummary | null = null;
  private graphQueueSummaryAssetPath = '';
  private stateRequest: Promise<void> | null = null;
  private versionRequest: Promise<void> | null = null;

  constructor(private readonly notify: () => void) {}

  isLoaded(): boolean {
    return this.state !== null;
  }

  version(): string {
    return this.appVersion;
  }

  selectedAssetName(): string {
    return this.synchronizedAsset()?.name || '';
  }

  ensureLoaded(): void {
    if (this.state || this.stateRequest) return;
    this.stateRequest = this.refreshState()
      .catch((error) => {
        this.logs = [error instanceof Error ? error.message : String(error)];
        this.notify();
      })
      .finally(() => {
        this.stateRequest = null;
      });
  }

  ensureVersionLoaded(): void {
    if (this.appVersion || this.versionRequest) return;
    this.versionRequest = this.refreshVersion()
      .catch((error) => {
        this.logs = [error instanceof Error ? error.message : String(error)];
        this.notify();
      })
      .finally(() => {
        this.versionRequest = null;
      });
  }

  selectAssetByName(assetName: string): void {
    const asset = this.state?.assets.find((candidate) => candidate.name === assetName);
    if (!asset || asset.path === this.selectedPath) return;
    this.selectedPath = asset.path;
    this.devkitInput = asset.path;
    this.captureAssetName = asset.name;
    this.selectedReport = preferredReportForAsset(asset);
    this.reportContent = '';
    this.reportPath = '';
    this.missingFunctions = [];
    this.selectedMissingFunctions.clear();
    window.localStorage.setItem('blueprint-tool.selected', this.selectedPath);
    void this.loadReport(this.selectedReport);
    void this.loadMissingFunctions();
  }

  renderShellActions(): string {
    return `
      ${actionButton('刷新状态', 'refresh', 'ghost', this.busy)}
      ${actionButton('打开 captures 目录', 'open-capture-root', 'ghost')}
    `;
  }

  renderLegacy(): string {
    const asset = this.synchronizedAsset();
    return renderStepReports(this.workflowView(asset));
  }

  renderExperimental(): string {
    const asset = this.synchronizedAsset();
    const workflow = this.workflowView(asset);
    return `
      ${renderStepPath(workflow)}
      ${renderStepActions(workflow)}
      ${renderStepResult(asset)}
      ${renderKnowledgeBaseSection(this.state, this.busy)}
      ${renderRecaptureSection(this.captureView(asset))}
      ${renderAdvancedSection(this.advancedView(asset))}
      <p class="footnote">日志最近一条：${escapeHtml(this.logs[0] || '无')}</p>
    `;
  }

  syncInputs(): void {
    const nextQueueText = document.querySelector<HTMLTextAreaElement>('#capture-queue-text')?.value;
    this.captureAssetName = document.querySelector<HTMLInputElement>('#capture-asset-name')?.value || this.captureAssetName;
    this.captureGraphName = document.querySelector<HTMLInputElement>('#capture-graph-name')?.value || this.captureGraphName;
    this.captureGraphType = document.querySelector<HTMLSelectElement>('#capture-graph-type')?.value || this.captureGraphType;
    if (typeof nextQueueText === 'string' && nextQueueText !== this.captureQueueText) {
      this.captureQueueText = nextQueueText;
      this.captureQueueCursor = 0;
      this.saveCaptureQueueState();
    }
    this.devkitInput = document.querySelector<HTMLTextAreaElement>('#devkit-path')?.value || this.devkitInput;
    this.compareOldPath = document.querySelector<HTMLSelectElement>('#compare-old')?.value || this.compareOldPath;
    this.compareNewPath = document.querySelector<HTMLSelectElement>('#compare-new')?.value || this.compareNewPath;
  }

  bind(): void {
    document.querySelectorAll<HTMLButtonElement>('[data-select-asset]').forEach((button) => {
      button.addEventListener('click', () => {
        this.syncInputs();
        this.selectedPath = button.dataset.selectAsset || '';
        this.devkitInput = this.selectedPath;
        window.localStorage.setItem('blueprint-tool.selected', this.selectedPath);
        this.captureAssetName = this.selectedAsset()?.name || this.captureAssetName;
        this.selectedReport = preferredReportForAsset(this.selectedAsset());
        this.reportContent = '';
        this.reportPath = '';
        void this.loadReport(this.selectedReport);
        void this.loadMissingFunctions();
        this.notify();
      });
    });

    const devkitField = document.querySelector<HTMLTextAreaElement>('#devkit-path');
    if (devkitField) {
      devkitField.addEventListener('input', () => {
        this.devkitInput = devkitField.value;
        document
          .querySelector<HTMLButtonElement>('[data-action="read-uasset-graphs"]')
          ?.toggleAttribute('disabled', this.busy || !this.devkitInput.trim());
      });
      devkitField.addEventListener('change', () => {
        this.syncInputs();
        this.notify();
      });
    }

    document.querySelectorAll<HTMLButtonElement>('[data-report]').forEach((button) => {
      button.addEventListener('click', () => {
        this.syncInputs();
        this.selectedReport = (button.dataset.report || 'next_actions') as ReportKey;
        void this.loadReport(this.selectedReport);
      });
    });

    document.querySelectorAll<HTMLButtonElement>('[data-action]').forEach((button) => {
      button.addEventListener('click', () => {
        this.syncInputs();
        void this.handleAction(button.dataset.action || '');
      });
    });

    document.querySelectorAll<HTMLInputElement>('[data-missing-function]').forEach((input) => {
      input.addEventListener('change', () => {
        const name = input.dataset.missingFunction || '';
        if (!name) return;
        if (input.checked) {
          this.selectedMissingFunctions.add(name);
        } else {
          this.selectedMissingFunctions.delete(name);
        }
        this.notify();
      });
    });

    document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('input, textarea, select').forEach((input) => {
      input.addEventListener('input', () => this.syncInputs());
      input.addEventListener('change', () => this.syncInputs());
    });
  }

  private appendLog(message: string): void {
    const timestamp = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    this.logs = [`[${timestamp}] ${message}`, ...this.logs].slice(0, 90);
    this.notify();
  }

  private setMainNotice(message: string, tone: MainNoticeTone = 'info'): void {
    this.mainNotice = message;
    this.mainNoticeTone = tone;
    this.appendLog(message);
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  private isJobDone(job: JobInfo): boolean {
    return ['succeeded', 'failed', 'cancelled', 'timed_out'].includes(job.status);
  }

  private jobResultString(job: JobInfo, key: string): string {
    const value = job.result?.[key];
    return typeof value === 'string' ? value : '';
  }

  private async waitForJob(jobId: string, label: string): Promise<JobInfo> {
    let lastProgressAt = 0;
    let lastOutputLength = 0;
    const localStartedAt = Date.now();
    while (true) {
      const payload = await api<ApiResult & { job: JobInfo }>(`/api/jobs/${jobId}`);
      const job = payload.job;
      const outputLength = (job.stdout || '').length + (job.stderr || '').length;
      const now = Date.now();
      if (!this.isJobDone(job)) {
        if (now - lastProgressAt > 4000 || outputLength !== lastOutputLength) {
          const seconds = Math.round((now - localStartedAt) / 1000);
          this.appendLog(`${label}后台任务仍在运行：${job.status}，已耗时约 ${seconds}s。`);
          lastProgressAt = now;
          lastOutputLength = outputLength;
        }
        await this.delay(1000);
        continue;
      }
      return job;
    }
  }

  private selectedAsset(): AssetSummary | undefined {
    const byPath = this.state?.assets.find((asset) => asset.path === this.selectedPath);
    const ready = this.state?.assets.find((asset) => asset.graphs > 0 && asset.hasOutput);
    return byPath || ready || this.state?.assets[0];
  }

  private synchronizedAsset(): AssetSummary | undefined {
    const asset = this.selectedAsset();
    if (asset && asset.path !== this.selectedPath) {
      this.selectedPath = asset.path;
      window.localStorage.setItem('blueprint-tool.selected', this.selectedPath);
    }
    return asset;
  }

  private normalizeObjectPathInput(rawText: string): string {
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

  private assetNameFromObjectPath(rawText: string): string {
    const normalized = this.normalizeObjectPathInput(rawText);
    if (!normalized) return '';
    return normalized.split('.').pop() || normalized.split('/').pop() || '';
  }

  private captureQueueItems(): CaptureQueueItem[] {
    return parseCaptureQueue(this.captureQueueText);
  }

  private currentCaptureQueueItem(): CaptureQueueItem | undefined {
    const items = this.captureQueueItems();
    if (!items.length || this.captureQueueCursor >= items.length) return undefined;
    return items[Math.max(0, this.captureQueueCursor)];
  }

  private saveCaptureQueueState(): void {
    window.localStorage.setItem('blueprint-tool.captureQueue', this.captureQueueText);
    window.localStorage.setItem('blueprint-tool.captureQueueCursor', String(this.captureQueueCursor));
  }

  private workflowView(asset: AssetSummary | undefined): WorkflowViewModel {
    const pathValue = this.devkitInput || this.state?.devkitAssetPath || '';
    const normalizedPath = this.normalizeObjectPathInput(pathValue);
    return {
      activeJobId: this.activeJobId,
      activeJobLabel: this.activeJobLabel,
      asset,
      busy: this.busy,
      devkitInput: this.devkitInput,
      mainNotice: this.mainNotice,
      mainNoticeTone: this.mainNoticeTone,
      reportContent: this.reportContent,
      reportLoading: this.reportLoading,
      reportPath: this.reportPath,
      selectedReport: this.selectedReport,
      state: this.state,
      typedAssetName: this.assetNameFromObjectPath(pathValue),
      typedPathRecognized: Boolean(normalizedPath),
    };
  }

  private captureView(asset: AssetSummary | undefined): CaptureViewModel {
    return {
      asset,
      busy: this.busy,
      captureAssetName: this.captureAssetName,
      captureGraphName: this.captureGraphName,
      captureGraphType: this.captureGraphType,
      captureQueueCursor: this.captureQueueCursor,
      captureQueueItems: this.captureQueueItems(),
      captureQueueText: this.captureQueueText,
      graphQueueSummary: this.graphQueueSummary,
      graphQueueSummaryAssetPath: this.graphQueueSummaryAssetPath,
    };
  }

  private advancedView(asset: AssetSummary | undefined): AdvancedViewModel {
    return {
      asset,
      busy: this.busy,
      compareContent: this.compareContent,
      compareNewPath: this.compareNewPath,
      compareOldPath: this.compareOldPath,
      comparePath: this.comparePath,
      logs: this.logs,
      missingFunctions: this.missingFunctions,
      selectedMissingFunctions: this.selectedMissingFunctions,
      state: this.state,
    };
  }

  private async refreshState(keepReport = true): Promise<void> {
    const previousSelectedPath = this.selectedPath;
    const payload = await api<AppState>('/api/state');
    this.state = payload;
    this.appVersion = payload.version;
    if (!this.selectedPath || !this.state.assets.some((asset) => asset.path === this.selectedPath)) {
      this.selectedPath = this.state.assets.find((asset) => asset.graphs > 0 && asset.hasOutput)?.path || this.state.assets[0]?.path || '';
    }
    if (previousSelectedPath && previousSelectedPath !== this.selectedPath) {
      this.missingFunctions = [];
      this.selectedMissingFunctions.clear();
    }
    if (!this.captureAssetName) {
      this.captureAssetName = this.selectedAsset()?.name || '';
    }
    if (!this.devkitInput) {
      this.devkitInput = this.state.devkitAssetPath;
    }
    if (!this.compareOldPath) {
      this.compareOldPath = this.state.assets[0]?.path || '';
    }
    if (!this.compareNewPath) {
      this.compareNewPath = this.state.assets[1]?.path || this.state.assets[0]?.path || '';
    }
    const asset = this.selectedAsset();
    if (asset && (previousSelectedPath !== this.selectedPath || !asset.reports[this.selectedReport])) {
      this.selectedReport = preferredReportForAsset(asset);
    }
    if (this.selectedPath) {
      await this.loadMissingFunctions(false);
    }
    this.notify();
    if (keepReport && this.selectedPath) {
      await this.loadReport(this.selectedReport, false);
      this.notify();
    }
  }

  private async loadReport(key: ReportKey, rerender = true): Promise<void> {
    const asset = this.selectedAsset();
    this.selectedReport = key;
    if (!asset || !asset.reports[key]) {
      this.reportContent = '';
      this.reportPath = '';
      if (rerender) this.notify();
      return;
    }
    this.reportLoading = true;
    if (rerender) this.notify();
    try {
      const query = new URLSearchParams({ assetPath: asset.path, target: key });
      const payload = await api<ApiResult & { content: string; path: string }>(`/api/report?${query}`);
      this.reportContent = payload.content;
      this.reportPath = payload.path;
    } catch (error) {
      this.reportContent = error instanceof Error ? error.message : String(error);
      this.reportPath = '';
    } finally {
      this.reportLoading = false;
      if (rerender) this.notify();
    }
  }

  private async loadMissingFunctions(rerender = true): Promise<void> {
    const asset = this.selectedAsset();
    if (!asset || (!asset.reports.context_review && !asset.reports.notes_todo)) {
      this.missingFunctions = [];
      this.selectedMissingFunctions.clear();
      if (rerender) this.notify();
      return;
    }
    try {
      const query = new URLSearchParams({ assetPath: asset.path });
      const payload = await api<ApiResult & { items: MissingFunctionItem[] }>(`/api/missing-functions?${query}`);
      this.missingFunctions = payload.items || [];
      const available = new Set(this.missingFunctions.map((item) => item.function));
      this.selectedMissingFunctions = new Set([...this.selectedMissingFunctions].filter((name) => available.has(name)));
    } catch (error) {
      this.missingFunctions = [];
      this.selectedMissingFunctions.clear();
      this.appendLog(error instanceof Error ? error.message : String(error));
    } finally {
      if (rerender) this.notify();
    }
  }

  private selectedMissingFunctionNames(): string[] {
    const available = new Set(this.missingFunctions.map((item) => item.function));
    return [...this.selectedMissingFunctions].filter((name) => available.has(name));
  }

  private async appendMissingNotes(kind: 'inherited' | 'ignore_missing'): Promise<void> {
    const asset = this.selectedAsset();
    const functions = this.selectedMissingFunctionNames();
    if (!asset || !functions.length) {
      this.appendLog('请先选择要判定的缺失函数。');
      return;
    }
    this.busy = true;
    this.appendLog(`正在写入 notes.md：${functions.length} 个函数。`);
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
      this.missingFunctions = payload.items || [];
      this.selectedMissingFunctions.clear();
      this.appendLog(`已更新 notes.md：新增 ${payload.added.length} 个，跳过重复 ${payload.skipped.length} 个。`);
      this.appendLog('正在重新生成标准报告，让报告预览同步 notes.md 判定。');
      const analysisPayload = await api<ApiResult & { job: JobInfo }>(
        '/api/analyze',
        {
          method: 'POST',
          body: JSON.stringify({ assetPath: asset.path, reportLevel: 'standard' }),
        },
      );
      this.activeJobId = analysisPayload.job.id;
      this.activeJobLabel = 'notes 后分析';
      this.notify();
      const job = await this.waitForJob(analysisPayload.job.id, 'notes 后分析');
      const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
      this.appendLog(`notes 后分析${outcome}，耗时 ${job.durationSeconds}s。`);
      if (job.error) this.appendLog(job.error);
      if (job.stderr) this.appendLog(job.stderr.trim().slice(-1200));
      if (job.stdout) this.appendLog(job.stdout.trim().slice(-1200));
      await this.refreshState(false);
      if (job.status === 'succeeded') {
        await this.loadReport('context_review');
      }
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy = false;
      this.activeJobId = '';
      this.activeJobLabel = '';
      this.notify();
    }
  }

  private async runAnalysis(reportLevel: ReportLevel): Promise<void> {
    const asset = this.selectedAsset();
    if (!asset) {
      this.appendLog('还没有选择资产。');
      return;
    }
    this.busy = true;
    this.appendLog(`开始为 ${asset.name} 刷新来源并生成 ${reportLevel} 人类报告。`);
    try {
      const payload = await api<ApiResult & { job: JobInfo }>(
        '/api/analyze',
        {
          method: 'POST',
          body: JSON.stringify({ assetPath: asset.path, reportLevel }),
        },
      );
      this.activeJobId = payload.job.id;
      this.activeJobLabel = '分析';
      this.appendLog(`分析后台任务已创建：${payload.job.id}`);
      this.notify();
      const job = await this.waitForJob(payload.job.id, '分析');
      const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
      this.appendLog(`分析${outcome}，耗时 ${job.durationSeconds}s。`);
      if (job.error) this.appendLog(job.error);
      if (job.stderr) this.appendLog(job.stderr.trim().slice(-1200));
      if (job.stdout) this.appendLog(job.stdout.trim().slice(-1200));
      await this.refreshState(false);
      if (job.status === 'succeeded') {
        this.selectedReport = 'context_pack';
        await this.loadReport('context_pack');
      }
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy = false;
      this.activeJobId = '';
      this.activeJobLabel = '';
      this.notify();
    }
  }

  private async capturePage(analyzeAfter: boolean, allowOverwrite = false, fromQueue = false): Promise<void> {
    this.syncInputs();
    const queueItem = fromQueue ? this.currentCaptureQueueItem() : undefined;
    if (fromQueue && !queueItem) {
      this.appendLog('图页采集队列里没有当前项。请先粘贴分页名称，或重置队列。');
      return;
    }
    const graphName = (queueItem?.name || this.captureGraphName).trim();
    const graphType = queueItem?.type || this.captureGraphType;
    if (!graphName) {
      this.appendLog('保存剪贴板图页前，需要先填写图页名。');
      return;
    }
    this.busy = true;
    this.appendLog(`正在从剪贴板采集图页：“${graphName}”。`);
    try {
      const asset = this.selectedAsset();
      const payload = await api<ApiResult & { asset: AssetSummary; graphPath: string; record: { warnings?: string[]; backup_path?: string }; analysisJob?: JobInfo }>(
        '/api/capture-graph',
        {
          method: 'POST',
          body: JSON.stringify({
            assetPath: asset?.path || '',
            assetName: this.captureAssetName,
            graphName,
            graphType,
            analyzeAfter,
            reportLevel: 'standard',
            allowOverwrite,
          }),
        },
      );
      this.selectedPath = payload.asset.path;
      window.localStorage.setItem('blueprint-tool.selected', this.selectedPath);
      this.appendLog(`已保存图页：${payload.graphPath}`);
      if (payload.record.backup_path) {
        this.appendLog(`已备份被覆盖的旧图页：${payload.record.backup_path}`);
      }
      if (payload.record.warnings?.length) {
        this.appendLog(`采集警告：${payload.record.warnings.join('; ')}`);
      }
      if (fromQueue) {
        const total = this.captureQueueItems().length;
        this.captureQueueCursor = Math.min(this.captureQueueCursor + 1, total);
        this.saveCaptureQueueState();
        const next = this.currentCaptureQueueItem();
        this.appendLog(next ? `队列已前进到下一项：${next.name}` : '队列已保存完。可以运行标准分析。');
      } else {
        this.captureGraphName = '';
      }
      await this.refreshState(false);
      if (analyzeAfter && payload.analysisJob) {
        this.activeJobId = payload.analysisJob.id;
        this.activeJobLabel = '分析';
        this.appendLog(`保存后分析后台任务已创建：${payload.analysisJob.id}`);
        this.notify();
        const job = await this.waitForJob(payload.analysisJob.id, '保存后分析');
        const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
        this.appendLog(`保存后分析${outcome}，耗时 ${job.durationSeconds}s。`);
        if (job.error) this.appendLog(job.error);
        if (job.stderr) this.appendLog(job.stderr.trim().slice(-1200));
        await this.refreshState(false);
        if (job.status === 'succeeded') {
          this.selectedReport = 'context_pack';
          await this.loadReport('context_pack');
        }
      }
    } catch (error) {
      if (error instanceof ApiFailure && error.code === 'overwrite_required') {
        this.busy = false;
        this.notify();
        const ok = window.confirm('这个图页已经存在。要覆盖它吗？旧文件会自动备份到 graphs/_backups/。');
        if (ok) {
          await this.capturePage(analyzeAfter, true, fromQueue);
        } else {
          this.appendLog('已取消覆盖，原图页保持不变。');
        }
        return;
      }
      this.appendLog(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy = false;
      this.activeJobId = '';
      this.activeJobLabel = '';
      this.notify();
    }
  }

  private async loadGraphQueueFromAsset(mode = 'all', applyToQueue = true): Promise<void> {
    const asset = this.selectedAsset();
    if (!asset) {
      this.appendLog('请先选择一个资产。');
      return;
    }
    try {
      const query = new URLSearchParams({ assetPath: asset.path, mode });
      const payload = await api<ApiResult & { path: string; content: string; summary: GraphQueueSummary }>(`/api/graph-queue?${query}`);
      this.graphQueueSummary = payload.summary;
      this.graphQueueSummaryAssetPath = asset.path;
      if (!payload.content.trim()) {
        this.appendLog('这个资产还没有 graph_queue.txt。请先在 DevKit 里运行默认值导出器。');
        this.notify();
        return;
      }
      if (applyToQueue) {
        this.captureQueueText = payload.content;
        this.captureQueueCursor = 0;
        this.saveCaptureQueueState();
        this.appendLog(`已载入${graphQueueModeLabel(mode)}：${parseCaptureQueue(payload.content).length} 个，来源 ${payload.path}`);
      } else {
        this.appendLog(
          `分页分类：推荐 ${payload.summary.recommended}，可选 ${payload.summary.optional}，暂不采集 ${payload.summary.deferred}，全量 ${payload.summary.total}。`,
        );
      }
      this.notify();
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    }
  }

  private async openTarget(target: OpenTarget): Promise<void> {
    const asset = this.selectedAsset();
    if (!asset) {
      this.appendLog('还没有选择资产。');
      return;
    }
    try {
      const payload = await api<ApiResult & { path: string }>('/api/open', {
        method: 'POST',
        body: JSON.stringify({ assetPath: asset.path, target }),
      });
      this.appendLog(`已打开：${payload.path}`);
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    }
  }

  private async copyText(text: string, label: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.appendLog(`已复制：${label}`);
    } catch {
      this.appendLog(`浏览器拒绝剪贴板权限，请手动复制：${text}`);
    }
  }

  private async saveDevkitRequest(): Promise<void> {
    this.syncInputs();
    try {
      const payload = await api<ApiResult & { assetPath: string; pythonCommand: string }>('/api/devkit-request', {
        method: 'POST',
        body: JSON.stringify({ assetPath: this.devkitInput }),
      });
      this.devkitInput = payload.assetPath;
      await this.copyText(payload.pythonCommand, 'DevKit Python 命令');
      this.appendLog(`已保存 DevKit 导出路径：${payload.assetPath}`);
      await this.refreshState(false);
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    }
  }

  private async mineUassetCandidates(copyCommand: boolean): Promise<void> {
    this.syncInputs();
    if (!this.devkitInput) {
      this.appendLog('请先粘贴目标蓝图 Object Path。');
      return;
    }
    this.busy = true;
    this.notify();
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
        body: JSON.stringify({ assetPath: this.devkitInput }),
      });
      this.devkitInput = payload.assetPath;
      this.appendLog(
        `已从 .uasset 提取 ${payload.candidateCount} 个分页候选名；源文件：${payload.uassetPath || '未找到本地 .uasset'}`,
      );
      if (payload.structure?.loaded) {
        this.appendLog(
          `结构解析：EdGraph ${payload.structure.graph_exports_count ?? 0} 个，函数图 ${payload.structure.function_graph_exports_count ?? 0} 个，折叠图 ${payload.structure.collapsed_graph_exports_count ?? 0} 个，独立图 ${payload.structure.standalone_graph_exports_count ?? 0} 个。`,
        );
      }
      this.appendLog(`候选文件：${payload.jsonPath}`);
      if (copyCommand) {
        await this.copyText(payload.pythonCommand, 'DevKit Python 验证命令');
      }
      await this.refreshState(false);
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy = false;
      this.notify();
    }
  }

  private async readUassetGraphs(): Promise<void> {
    this.syncInputs();
    if (!this.devkitInput) {
      this.setMainNotice('请先粘贴目标蓝图 Object Path。', 'warn');
      return;
    }
    this.busy = true;
    this.mainNotice = `正在读取：${this.normalizeObjectPathInput(this.devkitInput) || this.devkitInput}`;
    this.mainNoticeTone = 'info';
    this.notify();
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
          assetPath: this.devkitInput,
          analyzeAfter: true,
          reportLevel: 'standard',
          artifactMode: DEFAULT_ARTIFACT_MODE,
        }),
      });
      this.devkitInput = payload.assetPath;
      this.setMainNotice(
        `已从 .uasset 读取 ${payload.graphCount} 个图、${payload.nodeCount} 个节点、${payload.pinCount} 个 pin、${payload.linkCount} 条候选连线。`,
        'good',
      );
      if (payload.graphReportPath) this.appendLog(`资产解析报告：${payload.graphReportPath}`);
      if (payload.agentIndexPath) this.appendLog(`AI 证据索引：${payload.agentIndexPath}`);
      await this.refreshState(false);
      if (payload.analysisJob) {
        this.activeJobId = payload.analysisJob.id;
        this.activeJobLabel = '.uasset 分析';
        this.notify();
        const job = await this.waitForJob(payload.analysisJob.id, '.uasset 分析');
        const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
        this.appendLog(`.uasset 图内容分析${outcome}，耗时 ${job.durationSeconds}s。`);
        if (job.stderr) this.appendLog(job.stderr.trim().slice(-1200));
        await this.refreshState(false);
        if (job.status === 'succeeded') {
          this.selectedReport = 'agent_index';
          await this.loadReport('agent_index');
        }
      } else if (payload.agentIndexPath) {
        this.selectedReport = 'agent_index';
        await this.loadReport('agent_index');
      }
    } catch (error) {
      this.setMainNotice(readableError(error), 'danger');
    } finally {
      this.busy = false;
      this.activeJobId = '';
      this.activeJobLabel = '';
      this.notify();
    }
  }

  private async loadUassetFailedQueue(): Promise<void> {
    const asset = this.selectedAsset();
    if (!asset) {
      this.appendLog('请先选择一个资产。');
      return;
    }
    try {
      const query = new URLSearchParams({ assetPath: asset.path });
      const payload = await api<ApiResult & { path: string; content: string; summary: GraphQueueSummary }>(`/api/uasset-failed-queue?${query}`);
      if (!payload.content.trim()) {
        this.appendLog('没有需要手动补采的 .uasset 失败图页。');
        return;
      }
      this.captureQueueText = payload.content;
      this.captureQueueCursor = 0;
      this.graphQueueSummary = payload.summary;
      this.graphQueueSummaryAssetPath = asset.path;
      this.saveCaptureQueueState();
      this.appendLog(`已载入 .uasset 失败补采队列：${parseCaptureQueue(payload.content).length} 个，来源 ${payload.path}`);
      this.notify();
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    }
  }

  private async runCompare(): Promise<void> {
    this.syncInputs();
    if (!this.compareOldPath || !this.compareNewPath || this.compareOldPath === this.compareNewPath) {
      this.appendLog('运行对比前，请选择两个不同的资产。');
      return;
    }
    this.busy = true;
    this.appendLog('正在运行资产行为对比。');
    try {
      const payload = await api<ApiResult & { job: JobInfo }>(
        '/api/compare-asset',
        {
          method: 'POST',
          body: JSON.stringify({ oldAssetPath: this.compareOldPath, newAssetPath: this.compareNewPath }),
        },
      );
      this.activeJobId = payload.job.id;
      this.activeJobLabel = '对比';
      this.appendLog(`对比后台任务已创建：${payload.job.id}`);
      this.notify();
      const job = await this.waitForJob(payload.job.id, '对比');
      this.compareContent = this.jobResultString(job, 'behaviorImpact') || job.stderr || job.error || '对比已完成，但没有生成行为影响报告。';
      this.comparePath = this.jobResultString(job, 'behaviorImpactPath');
      const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
      this.appendLog(`对比${outcome}，耗时 ${job.durationSeconds}s。`);
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy = false;
      this.activeJobId = '';
      this.activeJobLabel = '';
      this.notify();
    }
  }

  private async cancelCurrentJob(): Promise<void> {
    if (!this.activeJobId) {
      this.appendLog('当前没有正在运行的后台任务。');
      return;
    }
    try {
      await api<ApiResult & { job: JobInfo }>(`/api/jobs/${this.activeJobId}/cancel`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      this.appendLog(`已请求取消${this.activeJobLabel || '当前'}任务。`);
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    }
  }

  private async refreshVersion(): Promise<void> {
    const payload = await api<ApiResult & { version: string }>('/api/state');
    this.appVersion = payload.version;
    this.notify();
  }

  private async buildKnowledgeBase(): Promise<void> {
    this.busy = true;
    this.appendLog('开始生成 ARK DevKit 全局背景知识库。');
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
      this.activeJobId = payload.job.id;
      this.activeJobLabel = '背景知识库';
      this.appendLog(`知识库后台任务已创建：${payload.job.id}`);
      this.notify();
      const job = await this.waitForJob(payload.job.id, '背景知识库');
      const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
      this.appendLog(`知识库生成${outcome}，耗时 ${job.durationSeconds}s。`);
      if (job.error) this.appendLog(job.error);
      if (job.stderr) this.appendLog(job.stderr.trim().slice(-1200));
      if (job.stdout) this.appendLog(job.stdout.trim().slice(-1200));
      await this.refreshState(false);
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy = false;
      this.activeJobId = '';
      this.activeJobLabel = '';
      this.notify();
    }
  }

  private async readPriorityAssets(): Promise<void> {
    this.busy = true;
    this.appendLog('开始小批量读取重点资产，并生成行为报告与质量评估。');
    try {
      const payload = await api<ApiResult & { job: JobInfo }>(
        '/api/knowledge-base/read-priority',
        {
          method: 'POST',
          body: JSON.stringify({ limit: 25, analyze: true }),
        },
      );
      this.activeJobId = payload.job.id;
      this.activeJobLabel = '重点资产自动解析';
      this.appendLog(`重点资产解析后台任务已创建：${payload.job.id}`);
      this.notify();
      const job = await this.waitForJob(payload.job.id, '重点资产自动解析');
      const outcome = job.status === 'succeeded' ? '完成' : `${job.status}，退出码 ${job.returnCode ?? '-'}`;
      this.appendLog(`重点资产自动解析${outcome}，耗时 ${job.durationSeconds}s。`);
      if (job.error) this.appendLog(job.error);
      if (job.stderr) this.appendLog(job.stderr.trim().slice(-1200));
      if (job.stdout) this.appendLog(job.stdout.trim().slice(-1200));
      await this.refreshState(false);
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy = false;
      this.activeJobId = '';
      this.activeJobLabel = '';
      this.notify();
    }
  }

  private async openKnowledgeBase(target: KnowledgeOpenTarget = 'report'): Promise<void> {
    try {
      const payload = await api<ApiResult & { path: string }>('/api/knowledge-base/open', {
        method: 'POST',
        body: JSON.stringify({ target }),
      });
      this.appendLog(`已打开：${payload.path}`);
    } catch (error) {
      this.appendLog(error instanceof Error ? error.message : String(error));
    }
  }

  private async handleAction(action: string): Promise<void> {
    if (action.startsWith('open-report-')) {
      const key = action.slice('open-report-'.length) as ReportKey;
      if (reportTargets.includes(key)) {
        this.selectedReport = key;
        await this.openTarget(key);
        await this.loadReport(key);
      }
      return;
    }
    if (action === 'refresh') {
      await this.refreshState();
      this.appendLog('资产状态已刷新。');
      return;
    }
    if (action === 'open-capture-root') {
      try {
        const payload = await api<ApiResult & { path: string }>('/api/open-captures', {
          method: 'POST',
          body: JSON.stringify({}),
        });
        this.appendLog(`已打开：${payload.path}`);
      } catch (error) {
        this.appendLog(error instanceof Error ? error.message : String(error));
      }
      return;
    }
    if (action === 'analyze-standard') {
      await this.runAnalysis('standard');
      return;
    }
    if (action === 'analyze-debug') {
      await this.runAnalysis('debug');
      return;
    }
    if (action === 'build-knowledge-base') {
      await this.buildKnowledgeBase();
      return;
    }
    if (action === 'read-priority-assets') {
      await this.readPriorityAssets();
      return;
    }
    if (action === 'open-knowledge-report') {
      await this.openKnowledgeBase('report');
      return;
    }
    if (action === 'open-knowledge-global-report') {
      await this.openKnowledgeBase('global_report');
      return;
    }
    if (action === 'open-knowledge-priority-report') {
      await this.openKnowledgeBase('priority_report');
      return;
    }
    if (action === 'open-knowledge-priority-results') {
      await this.openKnowledgeBase('priority_results');
      return;
    }
    if (action === 'open-knowledge-folder') {
      await this.openKnowledgeBase('folder');
      return;
    }
    if (action === 'analyze-compact') {
      await this.runAnalysis('compact');
      return;
    }
    if (action === 'cancel-job') {
      await this.cancelCurrentJob();
      return;
    }
    if (action === 'capture-page') {
      await this.capturePage(false);
      return;
    }
    if (action === 'capture-page-analyze') {
      await this.capturePage(true);
      return;
    }
    if (action === 'load-graph-queue-compact') {
      await this.loadGraphQueueFromAsset('compact', true);
      return;
    }
    if (action === 'load-graph-queue-recommended') {
      await this.loadGraphQueueFromAsset('recommended', true);
      return;
    }
    if (action === 'load-graph-queue-focused') {
      await this.loadGraphQueueFromAsset('focused', true);
      return;
    }
    if (action === 'load-graph-queue-all') {
      await this.loadGraphQueueFromAsset('all', true);
      return;
    }
    if (action === 'inspect-graph-queue') {
      await this.loadGraphQueueFromAsset('all', false);
      return;
    }
    if (action === 'capture-queue-current') {
      await this.capturePage(false, false, true);
      return;
    }
    if (action === 'capture-queue-skip') {
      this.syncInputs();
      const current = this.currentCaptureQueueItem();
      if (!current) {
        this.appendLog('队列里没有可跳过的当前项。');
        return;
      }
      this.captureQueueCursor = Math.min(this.captureQueueCursor + 1, this.captureQueueItems().length);
      this.saveCaptureQueueState();
      this.appendLog(`已跳过队列项：${current.name}`);
      this.notify();
      return;
    }
    if (action === 'capture-queue-reset') {
      this.syncInputs();
      this.captureQueueCursor = 0;
      this.saveCaptureQueueState();
      this.appendLog('图页采集队列已重置到第一项。');
      this.notify();
      return;
    }
    if (action === 'capture-queue-clear') {
      this.captureQueueText = '';
      this.captureQueueCursor = 0;
      this.saveCaptureQueueState();
      this.appendLog('图页采集队列已清空。');
      this.notify();
      return;
    }
    if (action === 'open-output') {
      await this.openTarget('output_folder');
      return;
    }
    if (action === 'open-current-report') {
      await this.openTarget(this.selectedReport);
      return;
    }
    if (action === 'open-graph-reports') {
      await this.openTarget('graph_reports');
      return;
    }
    if (action === 'select-all-missing') {
      this.selectedMissingFunctions = new Set(this.missingFunctions.map((item) => item.function));
      this.appendLog(`已选择 ${this.selectedMissingFunctions.size} 个缺失函数。`);
      this.notify();
      return;
    }
    if (action === 'clear-missing-selection') {
      this.selectedMissingFunctions.clear();
      this.appendLog('已清空缺失函数选择。');
      this.notify();
      return;
    }
    if (action === 'mark-missing-inherited') {
      await this.appendMissingNotes('inherited');
      return;
    }
    if (action === 'mark-missing-ignore') {
      await this.appendMissingNotes('ignore_missing');
      return;
    }
    if (action === 'open-notes') {
      await this.openTarget('notes');
      return;
    }
    if (action === 'save-devkit-request') {
      await this.saveDevkitRequest();
      return;
    }
    if (action === 'mine-uasset-candidates') {
      await this.mineUassetCandidates(false);
      return;
    }
    if (action === 'mine-uasset-candidates-copy') {
      await this.mineUassetCandidates(true);
      return;
    }
    if (action === 'read-uasset-graphs') {
      await this.readUassetGraphs();
      return;
    }
    if (action === 'open-uasset-diagnostics') {
      await this.openTarget('uasset_graph_read_report');
      return;
    }
    if (action === 'open-uasset-links') {
      await this.openTarget('uasset_link_resolution_report');
      return;
    }
    if (action === 'open-uasset-triage') {
      await this.openTarget('uasset_partial_graph_triage');
      return;
    }
    if (action === 'open-uasset-gates') {
      await this.openTarget('uasset_quality_gates');
      return;
    }
    if (action === 'open-uasset-compare') {
      await this.openTarget('uasset_vs_clipboard_compare');
      return;
    }
    if (action === 'load-uasset-failed-queue') {
      await this.loadUassetFailedQueue();
      return;
    }
    if (action === 'copy-python-command') {
      await this.copyText(this.state?.devkitPythonCommand || '', 'DevKit Python 命令');
      return;
    }
    if (action === 'copy-output-command') {
      await this.copyText(this.state?.devkitOutputLogCommand || '', 'DevKit Output Log 命令');
      return;
    }
    if (action === 'run-compare') {
      await this.runCompare();
    }
  }
}
