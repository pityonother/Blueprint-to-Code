import { escapeHtml } from '../../shared/html';
import type {
  BlueprintEvidenceQueryResponse,
  BlueprintTraceResponse,
} from '../types';


function renderJson(value: unknown): string {
  return escapeHtml(JSON.stringify(value, null, 2));
}

function traversalButton(ref: string): string {
  if (!ref.includes('/n/') && !ref.includes('/p/')) {
    return `<code>${escapeHtml(ref)}</code>`;
  }
  return `<button type="button" data-blueprint-evidence-ref="${escapeHtml(ref)}" data-blueprint-evidence-operation="neighborhood">${escapeHtml(ref)}</button>`;
}

export function renderBlueprintEvidenceTrace(
  trace: BlueprintTraceResponse | null,
  evidenceResult: BlueprintEvidenceQueryResponse | null,
  selectedEvidenceRef: string,
  loading: boolean,
): string {
  const traceRows = trace?.items.length
    ? trace.items.map((item) => {
      const refs = item.evidenceRefs || [];
      return `
        <article class="blueprint-trace-row">
          <div>
            <strong>${escapeHtml(item.traceKind || (item.executable ? 'PSEUDOCODE_LINE' : 'TRACE'))}</strong>
            <span>line ${escapeHtml(item.pseudocodeLine || item.line || '—')}</span>
          </div>
          <code>${escapeHtml(item.statementId || 'No executable statement')}</code>
          <div class="blueprint-inline-actions">
            ${refs.map(traversalButton).join('')}
          </div>
        </article>
      `;
    }).join('')
    : `<p class="blueprint-empty">${loading ? '正在读取 trace.json 的 path-free 投影…' : '选择 Interpretation 语句中的 Evidence ref，查看邻域或执行流。'}</p>`;
  const queryPanel = evidenceResult
    ? `
      <section class="blueprint-evidence-query-result" aria-live="polite">
        <div class="blueprint-section-heading">
          <div>
            <p class="eyebrow">Evidence query</p>
            <h3 tabindex="-1" data-blueprint-evidence-query-heading>${escapeHtml(evidenceResult.operation)} · ${escapeHtml(selectedEvidenceRef)}</h3>
          </div>
          <span class="blueprint-contract-badge">${escapeHtml(evidenceResult.freshnessStatus || 'manifest-bound')}</span>
        </div>
        <div class="blueprint-evidence-query-actions">
          <button class="button secondary" type="button" data-blueprint-evidence-ref="${escapeHtml(selectedEvidenceRef)}" data-blueprint-evidence-operation="neighborhood">邻域</button>
          <button class="button secondary" type="button" data-blueprint-evidence-ref="${escapeHtml(selectedEvidenceRef)}" data-blueprint-evidence-operation="trace">执行流</button>
        </div>
        <details open><summary>返回的 Evidence bundles (${evidenceResult.items.length})</summary><pre>${renderJson(evidenceResult.items)}</pre></details>
        <details><summary>Coverage / omissions</summary><pre>${renderJson({ coverage: evidenceResult.coverage, omissions: evidenceResult.omissions, nextQueries: evidenceResult.nextQueries })}</pre></details>
      </section>
    `
    : '';
  return `
    <section class="panel blueprint-evidence-view">
      <div class="blueprint-section-heading">
        <div><p class="eyebrow">Evidence</p><h2>语句 → trace → exact ref</h2></div>
        <span class="blueprint-contract-badge">GET 投影 path-free</span>
      </div>
      <div class="blueprint-trace-list">${traceRows}</div>
      ${trace?.page.nextCursor ? `<button class="button secondary blueprint-load-more" type="button" data-blueprint-action="load-more-trace" ${loading ? 'disabled' : ''}>加载更多 Trace</button>` : ''}
      ${queryPanel}
    </section>
  `;
}
