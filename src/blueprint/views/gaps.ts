import { escapeHtml } from '../../shared/html';
import type { BlueprintGapsResponse } from '../types';


function evidenceAction(ref: string): string {
  if (!ref.includes('/n/') && !ref.includes('/p/')) {
    return `<code>${escapeHtml(ref)}</code>`;
  }
  return `<button type="button" data-blueprint-evidence-ref="${escapeHtml(ref)}" data-blueprint-evidence-operation="neighborhood">追溯 ${escapeHtml(ref)}</button>`;
}

export function renderBlueprintGaps(
  response: BlueprintGapsResponse | null,
  loading: boolean,
): string {
  const rows = response?.items.length
    ? response.items.map((gap) => `
      <article class="blueprint-gap-row">
        <div>
          <span class="blueprint-status warning">${escapeHtml(gap.status)}</span>
          <strong>${escapeHtml(gap.code)}</strong>
        </div>
        <p>${escapeHtml(gap.detail || 'Evidence 标记了未恢复内容。')}</p>
        <code>${escapeHtml(gap.graphRef || gap.nodeRef || gap.id)}</code>
        <div class="blueprint-inline-actions">
          ${(gap.evidenceRefs || []).map(evidenceAction).join('')}
        </div>
      </article>
    `).join('')
    : `<p class="blueprint-empty">${loading ? '正在读取 gaps.json 的 path-free 投影…' : '当前 Interpretation 没有显式 gap。'}</p>`;
  return `
    <section class="panel blueprint-gaps-view">
      <div class="blueprint-section-heading">
        <div><p class="eyebrow">Gaps</p><h2>不填补未知内容</h2></div>
        <span class="blueprint-contract-badge">${escapeHtml(response?.page.total || 0)} explicit gap(s)</span>
      </div>
      <p class="hint">SOURCE_NOT_AVAILABLE、未解析边和外部 callable body 会保持为显式 gap，不会被伪造为可确认行为。</p>
      <div class="blueprint-gap-list">${rows}</div>
      ${response?.page.nextCursor ? `<button class="button secondary blueprint-load-more" type="button" data-blueprint-action="load-more-gaps" ${loading ? 'disabled' : ''}>加载更多 Gaps</button>` : ''}
    </section>
  `;
}
