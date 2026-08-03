import { escapeHtml } from '../../shared/html';
import { isBlueprintStale, type BlueprintWorkspaceState } from '../state';


export function renderBlueprintAssetHealth(state: BlueprintWorkspaceState): string {
  const health = state.health?.health;
  if (!state.selectedAsset) {
    return '<section class="panel blueprint-health"><p class="blueprint-empty">选择一个资产以读取 Evidence 与 Interpretation 身份。</p></section>';
  }
  if (!health) {
    return `<section class="panel blueprint-health" aria-busy="${state.loading}">
      <p class="blueprint-empty">${state.loading ? '正在验证 Evidence / Interpretation current…' : '未读取到资产健康状态。'}</p>
    </section>`;
  }
  const status = String(health.status || 'UNKNOWN');
  const evidence = health.evidence;
  const interpretation = health.interpretation;
  const stale = isBlueprintStale(state);
  return `
    <section class="panel blueprint-health">
      ${stale ? `<div class="blueprint-stale-banner" role="alert">
        <strong>STALE：Interpretation 不再匹配当前 Evidence</strong>
        <span>${escapeHtml(state.staleCode || health.reasonCode || evidence?.freshnessStatus || 'BLUEPRINT_INTERPRETATION_STALE')}</span>
      </div>` : ''}
      <div class="blueprint-health-head">
        <div>
          <p class="eyebrow">Evidence-bound identity</p>
          <h2>${escapeHtml(state.selectedAsset)}</h2>
          <code>${escapeHtml(health.asset?.objectPath || 'Object Path 未公开')}</code>
        </div>
        <span class="blueprint-status ${status === 'READY' ? 'ready' : status === 'INVALID' ? 'danger' : 'warning'}">${escapeHtml(status)}</span>
      </div>
      <dl class="blueprint-identity-grid">
        <div><dt>Evidence revision</dt><dd>${escapeHtml(evidence?.revisionId || '—')}</dd></div>
        <div><dt>Interpretation revision</dt><dd>${escapeHtml(interpretation?.revisionId || '—')}</dd></div>
        <div><dt>Evidence freshness</dt><dd>${escapeHtml(evidence?.freshnessStatus || 'UNKNOWN')}</dd></div>
        <div><dt>Release authority</dt><dd>${evidence?.releaseAuthority === true ? 'YES' : 'NO'}</dd></div>
      </dl>
    </section>
  `;
}
