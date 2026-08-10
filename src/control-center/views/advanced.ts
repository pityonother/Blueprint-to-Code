import { escapeHtml } from '../../shared/html';
import type {
  AppState,
  AssetSummary,
  MissingFunctionItem,
} from '../types';
import {
  actionButton,
  assetOptions,
  exportStatusText,
  metric,
  renderAssetList,
} from './common';


export interface AdvancedViewModel {
  asset?: AssetSummary;
  busy: boolean;
  compareContent: string;
  compareNewPath: string;
  compareOldPath: string;
  comparePath: string;
  logs: string[];
  missingFunctions: MissingFunctionItem[];
  selectedMissingFunctions: ReadonlySet<string>;
  state: AppState | null;
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


function renderNotesPanel(view: AdvancedViewModel): string {
  const rows = view.missingFunctions;
  const selectedCount = view.selectedMissingFunctions.size;
  const content = rows.length
    ? rows
        .map((item) => {
          const checked = view.selectedMissingFunctions.has(item.function) ? 'checked' : '';
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
        <span class="soft-label">${view.asset ? `${rows.length} 个待确认，已选 ${selectedCount}` : '未选择资产'}</span>
      </div>
      <div class="review-list">${content}</div>
      <div class="button-row">
        ${actionButton('全选队列', 'select-all-missing', 'secondary', !rows.length)}
        ${actionButton('清空选择', 'clear-missing-selection', 'ghost', !selectedCount)}
        ${actionButton('标记为父类/原生', 'mark-missing-inherited', 'primary', !view.asset || !selectedCount || view.busy)}
        ${actionButton('标记为忽略', 'mark-missing-ignore', 'secondary', !view.asset || !selectedCount || view.busy)}
        ${actionButton('打开 notes.md', 'open-notes', 'ghost', !view.asset)}
      </div>
    </section>
  `;
}


function renderComparePanel(view: AdvancedViewModel): string {
  const assets = view.state?.assets || [];
  const oldValue = view.compareOldPath || assets[0]?.path || '';
  const newValue = view.compareNewPath || assets[1]?.path || assets[0]?.path || '';
  return `
    <section class="panel compare-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">资产对比</p>
          <h2>生成行为影响报告</h2>
        </div>
        <span class="soft-label">${escapeHtml(view.comparePath || '还没有运行对比')}</span>
      </div>
      <div class="form-grid two">
        <label>
          <span>旧资产</span>
          <select id="compare-old">${assetOptions(assets, oldValue)}</select>
        </label>
        <label>
          <span>新资产</span>
          <select id="compare-new">${assetOptions(assets, newValue)}</select>
        </label>
      </div>
      <div class="button-row">
        ${actionButton('运行行为对比', 'run-compare', 'primary', view.busy || assets.length < 2)}
      </div>
      <pre class="compare-preview">${escapeHtml(view.compareContent || '对比输出会显示在这里。')}</pre>
    </section>
  `;
}


function renderAdvancedDevkit(view: AdvancedViewModel): string {
  return `
    <section class="advanced-card">
      <h3>DevKit 导出辅助</h3>
      <p class="hint">把当前路径写入 DevKit 请求文件，复制 Python 命令贴到 DevKit Output Log。一般只在 .uasset 解析不出来、或想用 DevKit 重新导出默认值时才用。</p>
      <div class="button-row">
        ${actionButton('保存路径并复制 Python 命令', 'save-devkit-request', 'primary', view.busy)}
        ${actionButton('复制 Python 命令', 'copy-python-command', 'secondary')}
        ${actionButton('复制 Output Log 命令', 'copy-output-command', 'secondary')}
      </div>
      <div class="button-row">
        ${actionButton('从 .uasset 提取分页候选名', 'mine-uasset-candidates', 'ghost', view.busy)}
        ${actionButton('提取并复制 DevKit 验证命令', 'mine-uasset-candidates-copy', 'ghost', view.busy)}
      </div>
      <small class="path-line">请求文件：${escapeHtml(view.state?.devkitRequestPath || '')}</small>
    </section>
  `;
}


function renderAdvancedAnalyze(view: AdvancedViewModel): string {
  return `
    <section class="advanced-card">
      <h3>更多分析模式与质检</h3>
      <p class="hint">普通用户用主流程的“重新生成完整报告”就够。下面是给开发者排查问题用的。</p>
      <div class="button-row">
        ${actionButton('生成 compact 报告（精简）', 'analyze-compact', 'secondary', !view.asset || !view.asset.graphs || view.busy)}
        ${actionButton('生成 debug 报告（含调试信息）', 'analyze-debug', 'danger', !view.asset || !view.asset.graphs || view.busy)}
      </div>
      <div class="button-row">
        ${actionButton('查看连线解析', 'open-uasset-links', 'ghost', !view.asset?.reports.uasset_link_resolution_report)}
        ${actionButton('查看读取质量自检', 'open-uasset-gates', 'ghost', !view.asset?.reports.uasset_quality_gates)}
        ${actionButton('和剪贴板复制对比', 'open-uasset-compare', 'ghost', !view.asset?.reports.uasset_vs_clipboard_compare)}
      </div>
    </section>
  `;
}


export function renderKnowledgeBaseSection(state: AppState | null, busy: boolean): string {
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


function renderAssetHistory(view: AdvancedViewModel): string {
  const assets = view.state?.assets || [];
  if (!assets.length) {
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
      <div class="asset-list">${renderAssetList(assets, view.asset)}</div>
    </section>
  `;
}


function renderAdvancedLog(logs: string[]): string {
  return `
    <section class="advanced-card">
      <h3>运行日志</h3>
      <pre class="log-output">${escapeHtml(logs.join('\n'))}</pre>
    </section>
  `;
}


export function renderAdvancedSection(view: AdvancedViewModel): string {
  return `
    <details class="advanced-section">
      <summary>高级功能（DevKit 导出、对比、debug、notes 判定、历史资产、日志）</summary>
      <div class="advanced-grid">
        ${renderAdvancedDevkit(view)}
        ${renderAdvancedAnalyze(view)}
        ${renderQualityPanel(view.asset)}
        ${renderNotesPanel(view)}
        ${renderComparePanel(view)}
        ${renderAssetHistory(view)}
        ${renderAdvancedLog(view.logs)}
      </div>
    </details>
  `;
}
