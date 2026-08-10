import { escapeHtml } from '../../shared/html';
import type { AssetSummary, ReportKey } from '../types';


export const reportLabels: Record<ReportKey, string> = {
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

export const reportTargets = Object.keys(reportLabels) as ReportKey[];
export const defaultReport: ReportKey = 'agent_index';


export function preferredReportForAsset(asset?: AssetSummary): ReportKey {
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


export function assetStatus(asset: AssetSummary): string {
  if (!asset.graphs) {
    return 'needs-work';
  }
  if (!asset.hasDefaults || !asset.hasComponents || !asset.hasOutput) {
    return 'partial';
  }
  return 'ready';
}


export function statusText(asset: AssetSummary): string {
  const status = assetStatus(asset);
  if (status === 'ready') {
    return '已就绪';
  }
  if (status === 'partial') {
    return '需补上下文';
  }
  return '需采集';
}


export function exportStatusText(status: string): string {
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


export function metric(label: string, value: string | number, tone = ''): string {
  return `
    <div class="metric ${tone}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}


export function actionButton(label: string, action: string, variant = 'primary', disabled = false): string {
  return `
    <button class="button ${variant}" data-action="${escapeHtml(action)}" ${disabled ? 'disabled' : ''}>
      ${escapeHtml(label)}
    </button>
  `;
}


export function reportButton(
  key: ReportKey,
  selectedReport: ReportKey,
  asset?: AssetSummary,
): string {
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


export function renderAssetList(assets: AssetSummary[], asset?: AssetSummary): string {
  if (!assets.length) {
    return '<div class="empty-state">还没有捕获资产。可以在“图页采集”面板新建资产，或使用命令行采集向导。</div>';
  }
  return assets
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


export function renderReportPreview(
  asset: AssetSummary | undefined,
  selectedReport: ReportKey,
  reportLoading: boolean,
  reportPath: string,
  reportContent: string,
): string {
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


export function assetOptions(assets: AssetSummary[], selected: string): string {
  return assets
    .map((asset) => `<option value="${escapeHtml(asset.path)}" ${asset.path === selected ? 'selected' : ''}>${escapeHtml(asset.name)}</option>`)
    .join('');
}
