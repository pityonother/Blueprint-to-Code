import { escapeHtml } from '../../shared/html';
import type { HarvestNodePage } from '../types';
import { countValue, formatCount } from '../format';

export function renderHarvestDatasetBar(page: HarvestNodePage): string {
  const coverage = page.coverage;
  const allCandidatesSelected =
    coverage.discoveryMode === 'DISCOVERED'
    && coverage.candidateDiscovery?.candidatesDiscovered === coverage.candidateDiscovery?.candidatesSelected;
  const nodeScope = coverage.claimsAllNodes
    ? '全类型资源点扫描'
    : allCandidatesSelected
      ? (coverage.nodesByType?.FOLIAGE_ACTOR || 0) > 0
        ? 'Foliage Mesh + Actor 候选扫描'
        : '全量 Foliage 候选扫描'
      : '有界节点样本';
  const maps = coverage.mapScan?.filesScanned ?? 0;
  const images = coverage.images?.available ?? 0;
  const sourceStatus = page.dataset.sourceStatus || 'UNKNOWN';
  const sourceCurrent = sourceStatus === 'CURRENT_AT_GENERATION';
  const hasEvaluationCatalog = coverage.creatureCandidatesDiscovered !== undefined
    || coverage.creatureAssetsCataloged !== undefined
    || coverage.speciesCataloged !== undefined;
  const creatureCoverage = hasEvaluationCatalog
    ? `
      <span>候选生物 ${escapeHtml(formatCount(countValue(coverage.creatureCandidatesDiscovered)))}</span>
      <span>生物资产 ${escapeHtml(formatCount(countValue(coverage.creatureAssetsCataloged)))}</span>
      <span>物种 ${escapeHtml(formatCount(countValue(coverage.speciesCataloged)))}</span>
    `
    : `<span class="status-pill warn">当前仅评估 ${escapeHtml(formatCount(countValue(coverage.rankingCreatures) ?? 0))} 只生物</span>`;
  return `
    <section class="harvest-dataset-bar" aria-label="数据覆盖范围">
      <span class="status-pill warn">${escapeHtml(nodeScope)}</span>
      <span class="status-pill ${sourceCurrent ? 'good' : 'danger'}">来源 ${escapeHtml(sourceStatus)}</span>
      <span>节点 ${coverage.nodesDecoded ?? page.total}</span>
      <span>地图包已扫 ${maps}</span>
      <span>缩略图 ${images}</span>
      ${creatureCoverage}
      <span title="${escapeHtml(page.dataset.revision || '')}">revision ${escapeHtml((page.dataset.revision || '').slice(0, 10))}</span>
    </section>
  `;
}
