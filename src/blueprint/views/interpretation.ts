import { escapeHtml } from '../../shared/html';
import { interpretationCoverage } from '../state';
import type {
  BlueprintGap,
  BlueprintHeuristicHint,
  BlueprintInterpretationResponse,
  BlueprintStatement,
} from '../types';


function statusTone(status: string): string {
  return status === 'CONFIRMED' ? 'confirmed' : 'unconfirmed';
}

function renderHint(hint: BlueprintHeuristicHint): string {
  return `
    <li>
      <strong>${escapeHtml(hint.topic || 'Review hint')}</strong>
      <span>${escapeHtml(hint.text || '')}</span>
      <small>basis=${escapeHtml(hint.basis || 'KEYWORD_AND_NAME_HEURISTIC')} · confidence=${escapeHtml(hint.confidence || 'HEURISTIC')} · notEvidence=${hint.notEvidence === false ? 'false' : 'true'}</small>
    </li>
  `;
}

function renderStatement(statement: BlueprintStatement): string {
  return `
    <button class="blueprint-statement-row" type="button" data-blueprint-statement="${escapeHtml(statement.id)}">
      <span class="blueprint-statement-kind">${escapeHtml(statement.kind)}</span>
      <span class="blueprint-statement-copy">
        <strong>${escapeHtml(statement.text)}</strong>
        <small>${escapeHtml(statement.graphRef)} · ${statement.evidenceRefs.length} evidence ref(s)</small>
      </span>
      <span class="blueprint-statement-status ${statusTone(statement.status)}">${escapeHtml(statement.status)}</span>
    </button>
  `;
}

export function renderBlueprintInterpretation(
  response: BlueprintInterpretationResponse | null,
  gapItems: BlueprintGap[],
  loading: boolean,
): string {
  if (!response) {
    return `<section class="panel blueprint-interpretation" aria-busy="${loading}">
      <p class="blueprint-empty">${loading ? '正在读取 Interpretation Contract v1…' : '当前资产没有可验证的 Interpretation current。'}</p>
    </section>`;
  }
  const coverage = interpretationCoverage(response, gapItems);
  const hints = response.heuristicReviewHints || [];
  const statementRows = response.items.length
    ? response.items.map(renderStatement).join('')
    : '<p class="blueprint-empty">当前筛选没有语句。</p>';
  return `
    <section class="panel blueprint-interpretation">
      <div class="blueprint-section-heading">
        <div>
          <p class="eyebrow">Interpretation Contract v1</p>
          <h2>证据约束的蓝图解释</h2>
        </div>
        <span class="blueprint-contract-badge">Evidence-derived · not original C++</span>
      </div>
      <div class="blueprint-coverage-grid" aria-label="Interpretation coverage">
        <div><span>总语句</span><strong>${escapeHtml(coverage.total)}</strong></div>
        <div><span>本页已确认</span><strong>${escapeHtml(coverage.confirmed)}</strong></div>
        <div><span>本页非确认</span><strong>${escapeHtml(coverage.nonConfirmed)}</strong></div>
        <div><span>本页确认占比</span><strong>${escapeHtml(coverage.confirmedPercent)}%</strong></div>
        <div><span>显式 Gaps</span><strong>${escapeHtml(coverage.gaps)}</strong></div>
      </div>
      <div class="blueprint-confirmation-key">
        <span><i class="confirmed"></i>CONFIRMED：具名 exact Evidence refs</span>
        <span><i class="heuristic"></i>HEURISTIC：仅供人工复查，不是证据</span>
      </div>
      ${hints.length ? `<details class="blueprint-hints">
        <summary>Heuristic review hints (${hints.length}) — not evidence</summary>
        <ul>${hints.map(renderHint).join('')}</ul>
      </details>` : ''}
      <div class="blueprint-statement-list" aria-label="Interpretation statements">${statementRows}</div>
      ${response.page.nextCursor ? `<div class="blueprint-page-actions">
        <p class="blueprint-page-note">已加载 ${escapeHtml(response.items.length)} / ${escapeHtml(response.page.total)} 条；后续 cursor 绑定当前 revision。</p>
        <button class="button secondary blueprint-load-more" type="button" data-blueprint-action="load-more-statements" ${loading ? 'disabled' : ''}>加载更多语句</button>
      </div>` : ''}
    </section>
  `;
}
