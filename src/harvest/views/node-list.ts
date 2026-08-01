import { escapeHtml } from '../../shared/html';
import type { HarvestNodeFilterState } from '../filters';
import { renderHarvestNodeEmptyState } from '../filters';
import {
  displayMapFamily,
  harvestImageUrl,
  mapFamilies,
  resourceName,
} from '../format';
import type { HarvestNode, HarvestNodePage } from '../types';

export interface HarvestNodeListViewState {
  filters: HarvestNodeFilterState;
  loadingPage: boolean;
  page: HarvestNodePage | null;
  selectedNodeId: string | null;
}

export function renderHarvestNodeList(state: HarvestNodeListViewState): string {
  const { filters, loadingPage, page, selectedNodeId } = state;
  if (loadingPage && !page) {
    return '<section class="panel harvest-node-pane"><div class="empty-state">正在加载节点索引…</div></section>';
  }
  if (!page || !page.items.length) {
    return `
      <section class="panel harvest-node-pane">
        <div class="panel-heading"><h2>资源节点</h2></div>
        ${renderHarvestNodeEmptyState(page, {
          query: filters.query,
          mapFamily: filters.mapFamily,
          mapMode: filters.mapMode,
          resource: filters.resource,
        })}
      </section>
    `;
  }
  const cards = page.items.map((node) => renderHarvestNodeCard(node, selectedNodeId === node.id)).join('');
  const previousOffset = Math.max(0, page.offset - page.limit);
  return `
    <section class="panel harvest-node-pane" aria-label="资源节点列表">
      <div class="panel-heading">
        <div><p class="eyebrow">RESOURCE NODES</p><h2>${page.total} 个匹配节点</h2></div>
        <span class="soft-label">${page.offset + 1}–${page.offset + page.items.length}</span>
      </div>
      <div class="harvest-node-list">${cards}</div>
      <div class="harvest-pagination">
        <button class="button ghost" type="button" data-harvest-page="${previousOffset}" ${page.offset <= 0 ? 'disabled' : ''}>上一页</button>
        <button class="button ghost" type="button" data-harvest-page="${page.nextOffset ?? 0}" ${page.nextOffset === null ? 'disabled' : ''}>下一页</button>
      </div>
    </section>
  `;
}

export function renderHarvestNodeCard(node: HarvestNode, active: boolean): string {
  const resources = node.resources?.items || [];
  const families = mapFamilies(node);
  const imageUrl = harvestImageUrl(node);
  const gapCount = node.gapCount ?? node.gaps?.length ?? 0;
  return `
    <button class="harvest-node-card ${active ? 'active' : ''}" type="button" data-harvest-node="${escapeHtml(node.id)}" aria-pressed="${active}">
      <span class="harvest-thumb" aria-label="${node.image?.status === 'AVAILABLE' ? '节点图片' : '节点图片尚未提取'}">
        ${imageUrl
          ? `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy">`
          : `<span>${escapeHtml((node.mesh?.name || node.name).slice(0, 2).toUpperCase())}</span>`}
      </span>
      <span class="harvest-node-copy">
        <strong>${escapeHtml(node.mesh?.name || node.name)}</strong>
        <small>${escapeHtml(resources.map(resourceName).join(' · ') || '产出资源未恢复')}</small>
        <small>${escapeHtml(families.map(displayMapFamily).join(' · ') || '地图使用证据尚未恢复')}</small>
      </span>
      <span class="status-pill ${gapCount ? 'warn' : 'good'}">${gapCount ? `${gapCount} 缺口` : '已确认'}</span>
    </button>
  `;
}
