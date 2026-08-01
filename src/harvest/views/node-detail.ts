import { escapeHtml } from '../../shared/html';
import {
  displayMapFamily,
  harvestImageUrl,
  imageDimension,
  isAuxiliaryMap,
  mapEvidenceLabel,
  mapFamilies,
  mapFamily,
  resourceName,
} from '../format';
import type { HarvestNode, HarvestRankingResult } from '../types';
import { renderHarvestRankingResult } from './ranking';

export function renderHarvestNodeDetail(
  node: HarvestNode | null,
  ranking: HarvestRankingResult | null,
  loadingRanking: boolean,
): string {
  if (!node) {
    return `
      <section class="panel harvest-detail-pane">
        <div class="empty-state">选择左侧资源节点，查看地图、产出资源和排行。</div>
      </section>
    `;
  }
  const resources = node.resources?.items || [];
  const references = node.mapReferences?.items || [];
  const auxiliaryReferenceCount = references.filter((item) => isAuxiliaryMap(item)).length;
  const playableReferenceCount = references.length - auxiliaryReferenceCount;
  const directReferenceCount = references.filter((item) =>
    !isAuxiliaryMap(item)
    && ((item.evidenceType || item.relation) === 'UMAP_DIRECT_PACKAGE_REFERENCE'
      || item.relation === 'DIRECT_PACKAGE_REFERENCE')
  ).length;
  const pcgReferenceCount = references.filter((item) => item.relation === 'PCG_BIOME_REFERENCE').length;
  const worldPartitionReferenceCount = references.filter(
    (item) => item.relation === 'WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE',
  ).length;
  const families = mapFamilies(node);
  const imageUrl = harvestImageUrl(node);
  const imageName = node.mesh?.name || node.name;
  return `
    <section class="panel harvest-detail-pane" aria-label="资源节点详情">
      <div class="harvest-detail-head">
        <div class="harvest-detail-image">
          ${imageUrl
            ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(imageName)} 资源点缩略图" loading="lazy" decoding="async" width="${imageDimension(node.image?.width)}" height="${imageDimension(node.image?.height)}">`
            : `<span>${escapeHtml(imageName.slice(0, 2).toUpperCase())}</span><small>图片未恢复</small>`}
        </div>
        <div>
          <p class="eyebrow">SELECTED NODE</p>
          <h2>${escapeHtml(node.mesh?.name || node.name)}</h2>
          <code>${escapeHtml(node.objectPath)}</code>
          <p class="harvest-component-line">HarvestComponent：${escapeHtml(node.harvestComponent?.name || '未恢复')}</p>
        </div>
      </div>
      <div class="harvest-detail-section">
        <h3>地图使用证据</h3>
        <div class="harvest-family-row">
          ${families.length
            ? families.map((family) => `<span class="harvest-chip">${escapeHtml(displayMapFamily(family))}</span>`).join('')
            : '<span class="status-pill warn">尚未找到可验证的地图使用证据</span>'}
        </div>
        <p class="hint">已恢复 ${playableReferenceCount} 条正式地图家族证据：地图包直接引用 ${directReferenceCount}，World Partition 外部 Actor ${worldPartitionReferenceCount}，PCG 生物群系依赖 ${pcgReferenceCount}${auxiliaryReferenceCount ? `；另有 ${auxiliaryReferenceCount} 条测试/工具证据` : ''}。依赖边已经确认，但 PCG 与外部 Actor 仍标为放置候选，不能冒充实际生成坐标。地图全集声明：${node.mapUsage?.claimsCompleteMapUsage ? '完整' : '尚未完整'}。</p>
        ${references.length ? `<details><summary>查看分层证据（${references.length}）</summary><ul class="harvest-map-list">${references.slice(0, 20).map((item) => `<li><strong>${escapeHtml(displayMapFamily(item.mapFamily || mapFamily(item.objectPath)))}</strong><span>${escapeHtml(mapEvidenceLabel(item))}${item.evidenceCount ? ` × ${item.evidenceCount}` : ''}</span><code>${escapeHtml(item.objectPath)}</code></li>`).join('')}</ul>${references.length > 20 ? `<p class="hint">另有 ${references.length - 20} 条未展开。</p>` : ''}</details>` : ''}
      </div>
      <div class="harvest-detail-section">
        <h3>选择该节点产出的资源</h3>
        <div class="harvest-resource-buttons">
          ${resources.length
            ? resources.map((resource) => `<button class="harvest-resource-button ${ranking?.resource.nodeResourceId === resource.nodeResourceId ? 'active' : ''}" type="button" data-harvest-resource="${escapeHtml(resource.nodeResourceId)}"><strong>${escapeHtml(resourceName(resource))}</strong><small>${escapeHtml(resource.resource)}</small></button>`).join('')
            : '<div class="empty-state compact">该节点的资源条目尚未恢复，不能生成数值排行。</div>'}
        </div>
      </div>
      ${renderHarvestRanking(ranking, loadingRanking)}
    </section>
  `;
}

export function renderHarvestRanking(
  ranking: HarvestRankingResult | null,
  loadingRanking: boolean,
): string {
  if (loadingRanking) {
    return '<div class="harvest-detail-section"><div class="empty-state">正在读取排行…</div></div>';
  }
  if (!ranking) {
    return '<div class="harvest-detail-section"><div class="empty-state compact">点击一个产出资源后才会读取排行。</div></div>';
  }
  return renderHarvestRankingResult(ranking);
}
