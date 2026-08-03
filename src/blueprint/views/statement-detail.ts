import { escapeHtml } from '../../shared/html';
import type { BlueprintStatementResponse } from '../types';


function evidenceActions(ref: string): string {
  if (!ref.includes('/n/') && !ref.includes('/p/')) {
    return '<span class="blueprint-nontraversable-ref">该 ref 不是 node/pin，不能执行邻域查询</span>';
  }
  return `<span>
    <button class="button secondary" type="button" data-blueprint-evidence-ref="${escapeHtml(ref)}" data-blueprint-evidence-operation="neighborhood">查看邻域</button>
    <button class="button ghost" type="button" data-blueprint-evidence-ref="${escapeHtml(ref)}" data-blueprint-evidence-operation="trace">执行流追踪</button>
  </span>`;
}

export function renderBlueprintStatementDetail(
  response: BlueprintStatementResponse | null,
  loading: boolean,
): string {
  if (!response && !loading) return '';
  if (!response) {
    return '<section class="panel blueprint-statement-detail" aria-busy="true"><p class="blueprint-empty">正在读取语句追溯…</p></section>';
  }
  const statement = response.statement;
  const refs = statement.evidenceRefs.length
    ? statement.evidenceRefs.map((ref) => `
      <li>
        <code>${escapeHtml(ref)}</code>
        ${evidenceActions(ref)}
      </li>
    `).join('')
    : '<li>没有 exact Evidence ref；请核对关联 gap。</li>';
  const trace = response.items.length
    ? response.items.map((item) => `
      <li>
        <span>${escapeHtml(item.traceKind || 'TRACE')}</span>
        <code>line ${escapeHtml(item.pseudocodeLine || item.line || '—')}</code>
      </li>
    `).join('')
    : '<li>没有 pseudocode trace 行。</li>';
  return `
    <section class="panel blueprint-statement-detail" aria-live="polite" data-blueprint-statement-detail="${escapeHtml(statement.id)}">
      <div class="blueprint-section-heading">
        <div><p class="eyebrow">Statement detail</p><h2 tabindex="-1" data-blueprint-statement-detail-heading>${escapeHtml(statement.kind)} · ${escapeHtml(statement.status)}</h2></div>
        <button class="button ghost" type="button" data-blueprint-action="close-statement">关闭</button>
      </div>
      <p class="blueprint-statement-text">${escapeHtml(statement.text)}</p>
      <dl class="blueprint-detail-identity">
        <div><dt>Statement</dt><dd><code>${escapeHtml(statement.id)}</code></dd></div>
        <div><dt>Graph</dt><dd><code>${escapeHtml(statement.graphRef)}</code></dd></div>
      </dl>
      <h3>Exact Evidence refs</h3>
      <ul class="blueprint-evidence-ref-list">${refs}</ul>
      <h3>Pseudocode trace</h3>
      <ul class="blueprint-trace-summary">${trace}</ul>
      ${response.page.nextCursor ? `<button class="button secondary blueprint-load-more" type="button" data-blueprint-action="load-more-statement-trace" ${loading ? 'disabled' : ''}>加载更多语句追溯</button>` : ''}
    </section>
  `;
}
