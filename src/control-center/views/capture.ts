import { escapeHtml } from '../../shared/html';
import {
  graphQueueTierLabel,
  graphTypeLabel,
  graphTypes,
} from '../capture-queue';
import type {
  AssetSummary,
  CaptureQueueItem,
  GraphQueueSummary,
} from '../types';
import { actionButton, metric } from './common';


export interface CaptureViewModel {
  asset?: AssetSummary;
  busy: boolean;
  captureAssetName: string;
  captureGraphName: string;
  captureGraphType: string;
  captureQueueCursor: number;
  captureQueueItems: CaptureQueueItem[];
  captureQueueText: string;
  graphQueueSummary: GraphQueueSummary | null;
  graphQueueSummaryAssetPath: string;
}


function graphTypeOptions(selectedType: string): string {
  return graphTypes
    .map((type) => `<option value="${escapeHtml(type)}" ${selectedType === type ? 'selected' : ''}>${escapeHtml(graphTypeLabel(type))}</option>`)
    .join('');
}


function renderGraphQueuePreview(view: CaptureViewModel): string {
  const { asset, graphQueueSummary, graphQueueSummaryAssetPath } = view;
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


function renderCaptureQueue(view: CaptureViewModel): string {
  const {
    asset,
    busy,
    captureQueueCursor,
    captureQueueItems: items,
    captureQueueText,
  } = view;
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
      ${renderGraphQueuePreview(view)}
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


export function renderCapturePanel(view: CaptureViewModel): string {
  const assetName = view.captureAssetName || view.asset?.name || '';
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
          <input id="capture-graph-name" value="${escapeHtml(view.captureGraphName)}" placeholder="Client Tick Gliding" />
        </label>
        <label>
          <span>图页类型</span>
          <select id="capture-graph-type">${graphTypeOptions(view.captureGraphType)}</select>
        </label>
      </div>
      <div class="button-row">
        ${actionButton('保存剪贴板图页', 'capture-page', 'primary', view.busy)}
        ${actionButton('保存并分析', 'capture-page-analyze', 'secondary', view.busy)}
      </div>
      ${renderCaptureQueue(view)}
    </section>
  `;
}


export function renderRecaptureSection(view: CaptureViewModel): string {
  const { asset } = view;
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
        ${renderCapturePanel(view)}
      </details>
    </section>
  `;
}
