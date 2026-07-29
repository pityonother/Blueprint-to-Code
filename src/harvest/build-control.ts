import { HarvestApiError, requestHarvestJson } from './api';
import type { HarvestBuildJob, HarvestBuildResponse } from './types';


function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}


function isActive(job: HarvestBuildJob | null): boolean {
  return job?.status === 'QUEUED' || job?.status === 'RUNNING';
}


function statusPresentation(status: HarvestBuildJob['status']): { label: string; tone: string } {
  const values: Record<HarvestBuildJob['status'], { label: string; tone: string }> = {
    QUEUED: { label: '等待启动', tone: 'warn' },
    RUNNING: { label: '正在构建', tone: 'warn' },
    SUCCEEDED: { label: '构建成功', tone: 'good' },
    FAILED: { label: '构建失败', tone: 'danger' },
    CANCELLED: { label: '已取消', tone: 'warn' },
  };
  return values[status];
}


export function renderHarvestBuildPanel(job: HarvestBuildJob | null, error = ''): string {
  const active = isActive(job);
  const progressCurrent = Math.max(0, Number(job?.progress.current || 0));
  const progressTotal = Math.max(0, Number(job?.progress.total || 0));
  const progressPercent = progressTotal > 0
    ? Math.min(100, Math.max(0, progressCurrent / progressTotal * 100))
    : 0;
  const status = job ? statusPresentation(job.status) : null;
  return `
    <section class="panel harvest-build-panel" aria-labelledby="harvest-build-title">
      <div class="harvest-ranking-heading">
        <div>
          <p class="eyebrow">AUTOMATED DATA PIPELINE</p>
          <h2 id="harvest-build-title">数据构建与验收</h2>
          <p>固定执行发现、解析、排行、地图证据、SQLite 索引和验证；页面不会执行用户输入的任意命令。</p>
        </div>
        ${status ? `<span class="status-pill ${status.tone}">${escapeHtml(status.label)}</span>` : '<span class="status-pill">尚未运行</span>'}
      </div>
      ${error ? `<div class="harvest-build-error" role="alert"><strong>操作失败</strong><p>${escapeHtml(error)}</p></div>` : ''}
      <div class="harvest-build-actions">
        <button class="button primary" type="button" data-build-action="start" ${active ? 'disabled' : ''}>${job?.status === 'SUCCEEDED' ? '重新全量构建' : '开始全量构建'}</button>
        ${active ? '<button class="button danger" type="button" data-build-action="cancel">取消构建</button>' : ''}
        <button class="button ghost" type="button" data-build-action="refresh">刷新状态</button>
      </div>
      ${job ? `
        <div class="harvest-build-progress" aria-label="构建进度 ${progressCurrent} / ${progressTotal}" aria-live="polite">
          <div>
            <strong>${escapeHtml(job.progress.label || '准备构建')}</strong>
            <span>${escapeHtml(progressCurrent)} / ${escapeHtml(progressTotal || '—')}</span>
          </div>
          <progress max="100" value="${progressPercent}">${progressPercent}%</progress>
          <small>任务 ${escapeHtml(job.id)}${job.pid ? ` · PID ${escapeHtml(job.pid)}` : ''}${job.returnCode !== null ? ` · 返回码 ${escapeHtml(job.returnCode)}` : ''}</small>
        </div>
        ${job.error ? `<div class="harvest-build-error" role="alert"><strong>构建错误</strong><p>${escapeHtml(job.error)}</p></div>` : ''}
        <details class="harvest-build-log" ${active || job.status === 'FAILED' ? 'open' : ''}>
          <summary>阶段日志${job.logTruncated ? '（只保留末尾）' : ''}</summary>
          <pre>${escapeHtml(job.logTail || '暂时没有日志。')}</pre>
        </details>
      ` : '<div class="empty-state compact">尚无构建任务。点击“开始全量构建”后，这里会显示真实阶段与日志。</div>'}
      <aside class="harvest-build-note">
        <strong>完成定义</strong>
        <p>只有所有阶段返回成功、暂存数据通过契约验证并原子替换正式数据后，GUI 才会读到新 revision；失败不会覆盖上一版可用结果。</p>
      </aside>
    </section>
  `;
}


export class HarvestBuildControl {
  private job: HarvestBuildJob | null = null;
  private initialized = false;
  private loading = false;
  private error = '';
  private pollTimer = 0;
  private controller: AbortController | null = null;

  constructor(private readonly requestRender: () => void) {}

  ensureLoaded(force = false): void {
    if (force) {
      this.initialized = false;
    }
    if (this.initialized || this.loading) {
      return;
    }
    this.initialized = true;
    void this.load();
  }

  render(): string {
    return `
      <section class="harvest-subhero" aria-labelledby="build-view-title">
        <div>
          <p class="eyebrow">BUILD STATUS</p>
          <h2 id="build-view-title">重建 DevKit 数据</h2>
          <p>从资产扫描到可查询索引是一条可取消、失败不污染正式数据的流水线。</p>
        </div>
      </section>
      <div class="harvest-live" aria-live="polite">${this.loading ? '正在读取构建状态…' : ''}</div>
      ${renderHarvestBuildPanel(this.job, this.error)}
    `;
  }

  bind(): void {
    document.querySelectorAll<HTMLButtonElement>('[data-build-action]').forEach((button) => {
      button.addEventListener('click', () => {
        const action = button.dataset.buildAction;
        if (action === 'start') {
          void this.start();
        } else if (action === 'cancel') {
          void this.cancel();
        } else if (action === 'refresh') {
          void this.load();
        }
      });
    });
  }

  private async load(): Promise<void> {
    this.controller?.abort();
    this.controller = new AbortController();
    this.loading = true;
    this.error = '';
    this.requestRender();
    try {
      const payload = await requestHarvestJson<HarvestBuildResponse>(
        '/api/harvest/build',
        { signal: this.controller.signal },
      );
      this.job = payload.job;
      this.schedulePoll();
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      if (error instanceof HarvestApiError && error.status === 404) {
        this.job = null;
        this.error = '';
      } else {
        this.error = error instanceof Error ? error.message : String(error);
      }
    } finally {
      this.loading = false;
      this.requestRender();
    }
  }

  private async start(): Promise<void> {
    this.controller?.abort();
    this.controller = new AbortController();
    this.loading = true;
    this.error = '';
    this.requestRender();
    try {
      const payload = await requestHarvestJson<HarvestBuildResponse>(
        '/api/harvest/build',
        {
          method: 'POST',
          body: JSON.stringify({ options: {} }),
          signal: this.controller.signal,
        },
      );
      this.job = payload.job;
      this.schedulePoll();
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        this.error = error instanceof Error ? error.message : String(error);
      }
    } finally {
      this.loading = false;
      this.requestRender();
    }
  }

  private async cancel(): Promise<void> {
    if (!this.job) {
      return;
    }
    this.controller?.abort();
    this.controller = new AbortController();
    this.loading = true;
    this.error = '';
    this.requestRender();
    try {
      const payload = await requestHarvestJson<HarvestBuildResponse>(
        `/api/harvest/build/${encodeURIComponent(this.job.id)}/cancel`,
        { method: 'POST', body: '{}', signal: this.controller.signal },
      );
      this.job = payload.job;
      this.schedulePoll();
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        this.error = error instanceof Error ? error.message : String(error);
      }
    } finally {
      this.loading = false;
      this.requestRender();
    }
  }

  private schedulePoll(): void {
    window.clearTimeout(this.pollTimer);
    if (!isActive(this.job)) {
      return;
    }
    this.pollTimer = window.setTimeout(() => void this.load(), 1000);
  }
}
