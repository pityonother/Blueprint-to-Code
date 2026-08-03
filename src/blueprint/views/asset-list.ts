import { escapeHtml } from '../../shared/html';
import type { BlueprintAssetListItem, BlueprintPage } from '../types';


function healthTone(status: string): string {
  if (status === 'READY') return 'ready';
  if (status === 'STALE' || status === 'MIGRATION_REQUIRED') return 'warning';
  if (status === 'INVALID') return 'danger';
  return 'missing';
}

export function renderBlueprintAssetList(
  items: BlueprintAssetListItem[],
  selectedAsset: string,
  query: string,
  loading: boolean,
  page: BlueprintPage | null = null,
): string {
  const rows = items.length
    ? items.map((item) => {
      const status = String(item.health.status || 'MISSING');
      const selected = item.asset === selectedAsset;
      return `
        <button class="blueprint-asset-row ${selected ? 'active' : ''}"
                type="button"
                data-blueprint-asset="${escapeHtml(item.asset)}"
                aria-pressed="${selected}">
          <span>
            <strong>${escapeHtml(item.asset)}</strong>
            <small>${escapeHtml(item.health.reasonCode || 'Interpretation Contract v1')}</small>
          </span>
          <span class="blueprint-status ${healthTone(status)}">${escapeHtml(status)}</span>
        </button>
      `;
    }).join('')
    : `<p class="blueprint-empty">${loading ? '正在读取 path-free 资产目录…' : '没有匹配的 Blueprint 资产。'}</p>`;
  return `
    <aside class="blueprint-asset-browser panel" aria-label="Blueprint 资产">
      <div class="blueprint-section-heading">
        <div>
          <p class="eyebrow">Assets</p>
          <h2>选择资产</h2>
        </div>
        <button class="button ghost" type="button" data-blueprint-action="refresh-assets" ${loading ? 'disabled' : ''}>刷新</button>
      </div>
      <form class="blueprint-asset-search" data-blueprint-form="asset-search">
        <label for="blueprint-asset-query">按资产名筛选</label>
        <div>
          <input id="blueprint-asset-query" type="search" value="${escapeHtml(query)}" autocomplete="off" />
          <button class="button secondary" type="submit" ${loading ? 'disabled' : ''}>筛选</button>
        </div>
      </form>
      <div class="blueprint-asset-rows" aria-live="polite">${rows}</div>
      ${page?.nextCursor ? `<button class="button secondary blueprint-load-more" type="button" data-blueprint-action="load-more-assets" ${loading ? 'disabled' : ''}>加载更多资产</button>` : ''}
    </aside>
  `;
}
