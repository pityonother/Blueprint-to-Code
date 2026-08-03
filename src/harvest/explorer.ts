import { fetchHarvestJson, HarvestApiError } from './api';
import { HarvestBuildControl } from './build-control';
import { HarvestCreatureExplorer } from './creatures';
import type {
  HarvestMapFilterMode,
  HarvestNode,
  HarvestNodeDetail,
  HarvestNodePage,
  HarvestRankingResult,
  HarvestRankingMetric,
  HarvestResourceEntry,
  HarvestResourceTypeFacet,
} from './types';


function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}


function mapFamily(objectPath: string): string {
  const parts = objectPath.split('/').filter(Boolean);
  if (parts[0] === 'Game' && parts[1] === 'Maps' && parts[2]) {
    return parts[2];
  }
  return parts[1] || parts[0] || '未知地图';
}


function displayMapFamily(value: string): string {
  const labels: Record<string, string> = {
    Genesis2: 'Genesis 2',
    ScorchedEarth: 'Scorched Earth',
    TheCenter: 'The Center',
    TheIsland: 'The Island',
    TestMaps: '测试地图',
  };
  const canonical = Object.keys(labels).find(
    (candidate) => candidate.toLowerCase() === value.toLowerCase(),
  );
  return (canonical ? labels[canonical] : '') || value.replaceAll('_', ' ');
}


function isAuxiliaryMap(reference: { objectPath: string; mapKind?: string }): boolean {
  if (reference.mapKind) {
    return reference.mapKind !== 'PLAYABLE_MAP_EVIDENCE';
  }
  return /\/(TestMaps|Test|Art_Tools)\//i.test(reference.objectPath);
}


export function mapFamilies(node: HarvestNode): string[] {
  const summarized = (node.mapUsage?.families || [])
    .map((item) => item.mapFamily)
    .filter(Boolean);
  if (summarized.length) {
    return Array.from(new Set(summarized)).sort((left, right) => left.localeCompare(right));
  }
  return Array.from(
    new Set(
      (node.mapReferences?.items || [])
        .filter((item) => !isAuxiliaryMap(item))
        .map((item) => item.mapFamily || mapFamily(item.objectPath)),
    ),
  ).sort((left, right) => left.localeCompare(right));
}


export function mapEvidenceLabel(reference: { relation?: string; evidenceType?: string }): string {
  const kind = reference.evidenceType || reference.relation || '';
  const labels: Record<string, string> = {
    UMAP_DIRECT_PACKAGE_REFERENCE: '地图包直接引用',
    DIRECT_PACKAGE_REFERENCE: '地图包直接引用',
    WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE: 'World Partition 外部 Actor',
    PCG_BIOME_REFERENCE: 'PCG 生物群系依赖',
  };
  return labels[kind] || kind || '未分类证据';
}


function formatScore(value: number | undefined): string {
  return typeof value === 'number'
    ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
    : '—';
}


function hasEstimatedYieldMetric(ranking: HarvestRankingResult): boolean {
  return ranking.methodology.metric === 'estimatedYieldPerNode'
    || ranking.items.some(
      (row) => typeof row.estimatedYieldPerNode === 'number'
        && Number.isFinite(row.estimatedYieldPerNode),
    );
}


function resourceClassDisplayName(resource: string): string {
  return resource
    .replace(/^PrimalItemResource_/, '')
    .replace(/_C$/, '')
    .replaceAll('_', ' ');
}


function resourceClassFromFilter(value: string): string {
  const normalized = value.trim().replace(/^BlueprintGeneratedClass'/, '').replace(/'$/, '');
  return normalized.split('.').at(-1) || normalized.split('/').at(-1) || normalized;
}


function resourceFilterDisplayName(value: string): string {
  return resourceClassDisplayName(resourceClassFromFilter(value));
}


function resourceFacetKey(resource: HarvestResourceTypeFacet): string {
  return resource.resourceKey || resource.resourceObjectPath || resource.resource;
}


function sameResourceIdentity(left: string, right: string): boolean {
  return left.trim().toLowerCase() === right.trim().toLowerCase();
}


function resourceName(resource: HarvestResourceEntry): string {
  return resource.displayName || resourceClassDisplayName(resource.resource);
}


function harvestImageUrl(node: HarvestNode): string {
  const value = node.image?.status === 'AVAILABLE' ? node.image.url || '' : '';
  return /^\/api\/harvest\/images\/[0-9a-f]{64}\.jpg$/.test(value) ? value : '';
}


function imageDimension(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 1 && value <= 4096
    ? Math.round(value)
    : 256;
}


function countValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.round(value)
    : null;
}


function firstCount(...values: unknown[]): number | null {
  for (const value of values) {
    const count = countValue(value);
    if (count !== null) {
      return count;
    }
  }
  return null;
}


function sumCounts(...values: unknown[]): number | null {
  const counts = values.map(countValue).filter((value): value is number => value !== null);
  return counts.length ? counts.reduce((total, value) => total + value, 0) : null;
}


function formatCount(value: number | null): string {
  return value === null ? '—' : new Intl.NumberFormat('zh-CN').format(value);
}


function formatSeconds(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    return '未恢复';
  }
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value)} 秒`;
}


function metricCard(label: string, value: number | null): string {
  const formatted = formatCount(value);
  return `
    <div class="harvest-coverage-metric" aria-label="${escapeHtml(label)} ${escapeHtml(formatted)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(formatted)}</strong>
    </div>
  `;
}


function reasonLabel(reasonCode: string): string {
  const labels: Record<string, string> = {
    ATTACK_PREVENTED_WITH_RIDER: '骑乘时明确禁用',
    BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED: '骑乘资格依赖蓝图逻辑，尚未恢复',
    BOSS_DINO: 'Boss 生物不在驯服骑乘范围',
    CANNOT_BE_TAMED: '资产明确不可驯服',
    TAMEABILITY_NOT_RECOVERED: '驯服资格尚未恢复',
    REQUIRED_ATTACK_FACT_NOT_RECOVERED: '攻击必要参数缺失',
    REQUIRED_COMPONENT_FACT_NOT_RECOVERED: '采集组件必要参数缺失',
    REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED: '伤害类型证据缺失',
    DAMAGE_TYPE_NOT_ACCEPTED: '该采集组件不接受此伤害类型',
    RESOURCE_NOT_IN_COMPONENT: '该组件没有目标资源条目',
  };
  return labels[reasonCode] || '未分类原因';
}


function reasonRows(
  heading: string,
  reasons: Record<string, number> | undefined,
): string[] {
  if (!reasons) {
    return [];
  }
  return Object.entries(reasons)
    .filter(([, count]) => countValue(count) !== null && Number(count) > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([reasonCode, count]) => `
      <li>
        <span><strong>${escapeHtml(heading)}：</strong>${escapeHtml(reasonLabel(reasonCode))}</span>
        <code>${escapeHtml(reasonCode)}</code>
        <span class="soft-label">${escapeHtml(formatCount(countValue(count)))} 条</span>
      </li>
    `);
}


function intervalCell(row: HarvestRankingResult['items'][number]): string {
  const source = row.attackIntervalSource || '';
  const effectiveInterval = row.attackInterval;
  const sourceLabels: Record<string, string> = {
    RIDER_ATTACK_INTERVAL: 'RiderAttackInterval',
    GENERAL_ATTACK_INTERVAL: 'AttackInterval',
  };
  const primary = source === 'RIDER_ATTACK_INTERVAL'
    ? `骑乘间隔 ${formatSeconds(row.riderAttackInterval ?? effectiveInterval)}`
    : `通用间隔 ${formatSeconds(effectiveInterval)}`;
  const base = source === 'RIDER_ATTACK_INTERVAL' && row.baseAttackInterval !== undefined
    ? `<small>基础间隔 ${escapeHtml(formatSeconds(row.baseAttackInterval))}</small>`
    : '';
  return `
    <span class="harvest-attack-name">${escapeHtml(row.attackName || '—')}</span>
    <small>${escapeHtml(primary)}</small>
    <small>来源 ${escapeHtml(sourceLabels[source] || source || '旧版报告未标记')}</small>
    ${base}
  `;
}


function rowEvidence(row: HarvestRankingResult['items'][number]): string {
  const gaps = [
    ...(row.missingFacts || []),
    ...(row.warnings || []),
    ...(row.evidence?.gaps || []),
  ].filter(Boolean);
  const explicit = row.evidence?.status || row.evidenceStatus;
  const recovered = row.rankingStatus === 'RANKED' && gaps.length === 0;
  const explicitLabels: Record<string, string> = {
    CONFIRMED: '证据已确认',
    COMPLETE: '证据完整',
    PARTIAL: '条件证据',
  };
  const label = (explicit && explicitLabels[explicit]) || explicit || (recovered ? '系数已恢复' : '部分证据');
  const className = recovered || explicit === 'CONFIRMED' || explicit === 'COMPLETE'
    ? 'good'
    : 'warn';
  return `<span class="status-pill ${className}" title="${escapeHtml(gaps.join('；') || row.reasonCode || '')}">${escapeHtml(label)}</span>`;
}


function rankingMetricValue(
  row: HarvestRankingResult['items'][number],
  metric: string,
): number | null {
  const value = row[metric as keyof typeof row];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}


function rankingMetricLabel(metric: string): string {
  const labels: Record<string, string> = {
    staticCompleteNodeTargetYield: '静态单节点目标资源总产量',
    staticYieldPerAttackCycleSecond: '静态攻击周期折算产量',
    observedYieldPerNode: '受控实测单节点产量',
    observedYieldPerSecond: '受控实测每秒产量',
  };
  return labels[metric] || metric;
}


function renderRankingV2Tier(
  heading: string,
  rows: HarvestRankingResult['items'],
  metric: string,
  unit: string,
  emptyText: string,
  conditional: boolean,
): string {
  if (!rows.length) {
    return `
      <section class="harvest-ranking-tier ${conditional ? 'conditional' : 'confirmed'}">
        <h4>${escapeHtml(heading)}</h4>
        <div class="empty-state compact">${escapeHtml(emptyText)}</div>
      </section>
    `;
  }
  return `
    <section class="harvest-ranking-tier ${conditional ? 'conditional' : 'confirmed'}">
      <h4>${escapeHtml(heading)}</h4>
      <div class="harvest-table-wrap">
        <table class="harvest-ranking-table">
          <caption>${escapeHtml(heading)}；顺序由服务端 Ranking Contract v2 决定</caption>
          <thead><tr><th scope="col">本榜名次</th><th scope="col">生物与变体</th><th scope="col">攻击周期</th><th scope="col">${escapeHtml(rankingMetricLabel(metric))}${unit ? `<small><code>${escapeHtml(unit)}</code></small>` : ''}</th><th scope="col">证据</th></tr></thead>
          <tbody>${rows.map((row) => {
            const selection = row.variantSelection;
            const omitted = row.scoreBreakdown?.omittedFactors || [];
            const metricValue = rankingMetricValue(row, metric);
            return `<tr>
              <td><span class="rank-number">${escapeHtml(row.rank)}</span></td>
              <td>
                <strong>${escapeHtml(row.creature)}</strong>
                <small>已选变体：<code>${escapeHtml(selection?.selectedObjectPath || row.creatureObjectPath || '未标记')}</code></small>
                ${selection?.higherExploratoryVariantExists ? '<small class="harvest-policy-warning">存在更高的探索性变体；规范榜未自动取最大</small>' : ''}
                <small>变体策略：${escapeHtml(selection?.policy || '未标记')}</small>
              </td>
              <td class="harvest-attack-cell">${intervalCell(row)}<small>首击：首个攻击周期末</small></td>
              <td class="score-cell"><span>${formatScore(metricValue ?? undefined)}</span>${unit ? `<small>单位 <code>${escapeHtml(unit)}</code></small>` : ''}<small>同证据层相对值 ${formatScore(row.relativeToNodeTopPercent)}%</small>${row.runtimeStatus ? `<small>实测：${escapeHtml(row.runtimeStatus === 'NOT_MEASURED' ? '未实测' : row.runtimeStatus)}</small>` : ''}</td>
              <td>${rowEvidence(row)}${omitted.length ? `<small title="${escapeHtml(omitted.join('；'))}">省略因素 ${escapeHtml(omitted.length)} 项</small>` : ''}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </div>
    </section>
  `;
}


function renderHarvestRankingResultV2(ranking: HarvestRankingResult): string {
  const confirmed = ranking.confirmedItems || [];
  const conditional = ranking.conditionalItems || [];
  const metric = ranking.methodology.metric;
  const unit = ranking.methodology.unit || '';
  const variantPolicy = ranking.queryPolicy?.variant || 'CANONICAL_VARIANT';
  const includePreliminary = ranking.queryPolicy?.includePreliminary === true;
  const runtimeProfilesAvailable = ranking.runtimeCoverage?.runtimeProfilesAvailable || [];
  const runtimeProfileSelected = ranking.runtimeCoverage?.runtimeProfileSelected
    || ranking.queryPolicy?.runtimeProfileId
    || '';
  const observedMetric = ranking.methodology.runtime === true || metric.startsWith('observed');
  const runtimeAvailable = runtimeProfilesAvailable.length > 0
    || (ranking.runtimeCoverage?.publishableConfirmedRows || 0) > 0
    || (includePreliminary && (ranking.runtimeCoverage?.preliminaryRows || 0) > 0)
    || (ranking.runtimeCoverage?.publishableExactRows || 0) > 0;
  const runtimeProfileReady = runtimeProfilesAvailable.length <= 1 || Boolean(runtimeProfileSelected);
  const observedMetricAvailable = runtimeAvailable && runtimeProfileReady;
  const runtimeProfileOptions = runtimeProfilesAvailable.map((profileId) => `
    <option value="${escapeHtml(profileId)}" ${profileId === runtimeProfileSelected ? 'selected' : ''}>${escapeHtml(profileId)}</option>
  `).join('');
  const identity = ranking.identity || {};
  const identityValues = [
    identity.extractorVersion,
    identity.modelVersion,
    identity.policyVersion,
    identity.resultSchemaVersion,
    identity.nodeCatalogRevision,
    identity.evaluationCatalogRevision,
    identity.componentCatalogRevision,
  ];
  const staleIdentity = identityValues.some((value) => !value);
  const omitted = Array.from(new Set(
    [...confirmed, ...conditional].flatMap((row) => row.scoreBreakdown?.omittedFactors || []),
  ));
  return `
    <div class="harvest-detail-section harvest-ranking" data-ranking-contract="v2">
      <div class="harvest-ranking-heading">
        <div>
          <p class="eyebrow">RANKING CONTRACT V2</p>
          <h3>${escapeHtml(resourceName(ranking.resource))}：分证据层排行</h3>
        </div>
        <span class="status-pill ${ranking.confirmedStatus === 'AVAILABLE' ? 'good' : 'warn'}">已确认榜 ${ranking.confirmedStatus === 'AVAILABLE' ? '可用' : '不可用'}</span>
      </div>
      ${staleIdentity ? '<div class="harvest-rebuild-banner" role="alert"><strong>数据身份不完整或已过期</strong><span>请在“数据构建”中重建；界面不会用旧结果继续排名。</span></div>' : ''}
      <p class="harvest-warning">${escapeHtml(ranking.methodology.warning || '')}</p>
      <div class="harvest-ranking-policy-controls" aria-label="排行口径">
        <label>指标
          <select id="harvest-ranking-metric" data-harvest-ranking-policy="metric">
            <option value="staticCompleteNodeTargetYield" ${metric === 'staticCompleteNodeTargetYield' ? 'selected' : ''}>静态单节点总产量</option>
            <option value="staticYieldPerAttackCycleSecond" ${metric === 'staticYieldPerAttackCycleSecond' ? 'selected' : ''}>静态攻击周期速度</option>
            <option value="observedYieldPerNode" ${metric === 'observedYieldPerNode' ? 'selected' : ''} ${observedMetricAvailable ? '' : 'disabled'}>受控实测单节点${runtimeAvailable ? (runtimeProfileReady ? '' : '（先选环境）') : '（未实测）'}</option>
            <option value="observedYieldPerSecond" ${metric === 'observedYieldPerSecond' ? 'selected' : ''} ${observedMetricAvailable ? '' : 'disabled'}>受控实测每秒${runtimeAvailable ? (runtimeProfileReady ? '' : '（先选环境）') : '（未实测）'}</option>
          </select>
        </label>
        <label>变体
          <select id="harvest-ranking-variant" data-harvest-ranking-policy="variant">
            <option value="CANONICAL_VARIANT" ${variantPolicy === 'CANONICAL_VARIANT' ? 'selected' : ''}>规范变体（默认）</option>
            <option value="ALL_VARIANTS" ${variantPolicy === 'ALL_VARIANTS' ? 'selected' : ''}>全部变体</option>
            <option value="BEST_DISCOVERED_VARIANT_EXPLORATORY" ${variantPolicy === 'BEST_DISCOVERED_VARIANT_EXPLORATORY' ? 'selected' : ''}>探索性最高变体</option>
          </select>
        </label>
        ${runtimeProfilesAvailable.length ? `<label>实测环境
          <select id="harvest-ranking-runtime-profile">
            <option value="">${runtimeProfilesAvailable.length === 1 ? '自动使用唯一环境' : '请选择一个环境'}</option>
            ${runtimeProfileOptions}
          </select>
        </label>
        <label><input id="harvest-ranking-include-preliminary" type="checkbox" ${includePreliminary ? 'checked' : ''}> 显式包含初步观察</label>` : ''}
        <span>地图可用性：<code>${escapeHtml(ranking.queryPolicy?.availability || 'GLOBAL_TRANSFER_ALLOWED')}</code></span>
        ${observedMetric ? `<span>runtimeProfileId：<code>${escapeHtml(runtimeProfileSelected || 'AUTO_OR_REQUIRED')}</code></span>` : ''}
        ${observedMetric ? `<span><code>includePreliminary=${includePreliminary ? 'true' : 'false'}</code></span>` : ''}
      </div>
      ${renderRankingV2Tier('已确认榜（独立编号）', confirmed, metric, unit, '已确认榜不可用；条件性结果不会被提升为已确认第一名。', false)}
      ${renderRankingV2Tier('条件性估算（不占已确认名次）', conditional, metric, unit, '当前请求没有可显示的条件性估算。', true)}
      <details class="harvest-scope-details">
        <summary>口径、版本与省略因素</summary>
        <dl class="harvest-evidence-list">
          <div><dt>Metric</dt><dd><code>${escapeHtml(metric)}</code></dd></div>
          <div><dt>Score basis</dt><dd><code>${escapeHtml(ranking.methodology.scoreBasis || '缺失')}</code></dd></div>
          <div><dt>Unit</dt><dd><code>${escapeHtml(unit || '未标记')}</code></dd></div>
          <div><dt>Extractor</dt><dd><code>${escapeHtml(identity.extractorVersion || '缺失')}</code></dd></div>
          <div><dt>Model</dt><dd><code>${escapeHtml(identity.modelVersion || '缺失')}</code></dd></div>
          <div><dt>Policy</dt><dd><code>${escapeHtml(identity.policyVersion || '缺失')}</code></dd></div>
          <div><dt>Result schema</dt><dd><code>${escapeHtml(identity.resultSchemaVersion || ranking.schema)}</code></dd></div>
          <div><dt>Node revision</dt><dd><code>${escapeHtml(identity.nodeCatalogRevision || '缺失')}</code></dd></div>
          <div><dt>Evaluation revision</dt><dd><code>${escapeHtml(identity.evaluationCatalogRevision || '缺失')}</code></dd></div>
          <div><dt>Component revision</dt><dd><code>${escapeHtml(identity.componentCatalogRevision || '缺失')}</code></dd></div>
          <div><dt>首击计时</dt><dd><code>${escapeHtml(ranking.methodology.firstHitTiming || '未标记')}</code></dd></div>
          ${observedMetric ? `<div><dt>runtimeProfilesAvailable</dt><dd><code>${escapeHtml(runtimeProfilesAvailable.join(', ') || '无')}</code></dd></div>` : ''}
          ${observedMetric ? `<div><dt>runtimeProfileSelected</dt><dd><code>${escapeHtml(runtimeProfileSelected || '未选择')}</code></dd></div>` : ''}
          ${observedMetric ? `<div><dt>Preliminary opt-in</dt><dd><code>includePreliminary=${includePreliminary ? 'true' : 'false'}</code></dd></div>` : ''}
        </dl>
        <p class="hint">静态周期速度只是固定攻击间隔的折算值，不是移动、耐力、负重、节点密度和服务器 hook 下的真实每秒产量。</p>
        ${omitted.length ? `<ul class="harvest-blocker-list">${omitted.map((value) => `<li><code>${escapeHtml(value)}</code></li>`).join('')}</ul>` : '<p class="hint">响应未列出省略因素。</p>'}
      </details>
    </div>
  `;
}


export function renderHarvestRankingResult(ranking: HarvestRankingResult): string {
  if (ranking.contractVersion === 'harvest-ranking-contract/v2') {
    return renderHarvestRankingResultV2(ranking);
  }
  const rows = ranking.items || [];
  const coverage = ranking.coverage || {};
  const isEstimatedYield = hasEstimatedYieldMetric(ranking);
  const isV2 = ranking.schema.endsWith('/v2') || ranking.claimsCompleteWithinScope !== undefined;
  const completeWithinScope = ranking.claimsCompleteWithinScope === true;
  const scopeTitle = completeWithinScope ? '已扫描范围完整 Top 10' : '已扫描范围 Top 10';
  const scopeLabel = completeWithinScope
    ? '本地目录范围完整'
    : isV2
      ? '证据仍有缺口'
      : `生物资产 ${formatCount(firstCount(coverage.creaturesLoaded, coverage.creaturesRequested))}`;
  const scopeClass = completeWithinScope ? 'good' : 'warn';
  const candidates = firstCount(
    coverage.candidateDiscovery?.candidatesDiscovered,
    coverage.creaturesRequested,
  );
  const creatureAssets = firstCount(coverage.creatureAssetsCataloged, coverage.creaturesLoaded);
  const species = firstCount(coverage.speciesCataloged, coverage.speciesEvaluated, coverage.creaturesLoaded);
  const attacks = firstCount(coverage.attacksDecoded);
  const evaluated = firstCount(coverage.attacksEvaluated);
  const excluded = sumCounts(
    coverage.attacksExcludedByScope,
    coverage.attacksExcludedByCreatureScope,
  );
  const reasonItems = [
    ...reasonRows('攻击排除', coverage.excludedByReason),
    ...reasonRows('生物排除', coverage.excludedCreatureByReason),
  ];
  const richerReasons = [
    ...(ranking.exclusions?.catalog?.byReason || []),
    ...(ranking.exclusions?.usageScope?.byReason || []),
    ...(ranking.exclusions?.queryDisposition?.byReason || []),
  ];
  if (!reasonItems.length) {
    richerReasons.forEach((reason) => {
      const count = countValue(reason.count);
      if (!reason.reasonCode || count === null || count <= 0) {
        return;
      }
      reasonItems.push(`
        <li>
          <span>${escapeHtml(reasonLabel(reason.reasonCode))}</span>
          <code>${escapeHtml(reason.reasonCode)}</code>
          <span class="soft-label">${escapeHtml(formatCount(count))} 条</span>
        </li>
      `);
    });
  }
  const explicitEvidenceStatus = ranking.evidence?.status;
  const rowsHavePartialEvidence = rows.some((row) =>
    row.tameabilityStatus === 'UNKNOWN'
    || row.evidence?.status === 'PARTIAL'
    || (row.evidence?.gaps?.length || 0) > 0);
  const evidenceComplete = explicitEvidenceStatus
    ? explicitEvidenceStatus === 'COMPLETE'
    : completeWithinScope && coverage.sourceFingerprintsComplete !== false && !rowsHavePartialEvidence;
  const evidenceLabel = evidenceComplete ? '证据完整' : isV2 ? '证据部分缺失' : '旧版样本证据';
  const blockers = [
    ...(ranking.claimBlockers || []),
    ...(ranking.evidence?.blockers || []),
  ].filter(Boolean);
  const attackDisposition = sumCounts(
    coverage.attacksRanked,
    coverage.attacksUnranked,
    coverage.attacksIncompatible,
  );
  const heading = isEstimatedYield ? '完整节点预计产量排行' : '旧版比较指数排行（非产量）';
  const caption = isEstimatedYield
    ? '所选节点和资源内，一整个完整节点的预计产量'
    : '旧版响应中的比较指数；该数值不代表完整节点产量';
  const metricLabel = isEstimatedYield ? '预计产量/完整节点' : '旧版比较指数';
  const relativeLabel = isEstimatedYield
    ? '相对本节点最高预计产量'
    : '相对旧版指数榜首';
  const warning = isEstimatedYield
    ? ranking.methodology.warning
    : `旧版响应：以下数值仅为旧版比较指数，不代表完整节点产量。${ranking.methodology.warning ? ` ${ranking.methodology.warning}` : ''}`;
  const emptyState = isEstimatedYield
    ? '当前扫描范围内没有可按完整节点预计产量排名的记录；未知和不兼容记录没有被当成 0。'
    : '旧版响应中没有可显示的比较指数记录；未知和不兼容记录没有被当成 0。';
  const scopeHint = isEstimatedYield
    ? ranking.claimsGlobalTop
      ? '该响应声明完整节点预计产量的全局排行。'
      : '这是本地 DevKit 与当前使用范围内的完整节点预计产量排行；它不是受控游戏实测值。'
    : '这是旧版比较指数响应，不应解释为完整节点产量排行或游戏实测产量排行。';

  return `
    <div class="harvest-detail-section harvest-ranking">
      <div class="harvest-ranking-heading">
        <div>
          <p class="eyebrow">${escapeHtml(scopeTitle)}</p>
          <h3>${escapeHtml(resourceName(ranking.resource))}：${escapeHtml(heading)}</h3>
        </div>
        <span class="status-pill ${scopeClass}">${escapeHtml(scopeLabel)}</span>
      </div>
      <p class="harvest-warning">${escapeHtml(warning)}</p>
      <div class="harvest-coverage-grid" aria-label="本次排行覆盖范围">
        ${metricCard('候选生物', candidates)}
        ${metricCard('生物资产', creatureAssets)}
        ${metricCard('物种', species)}
        ${metricCard('攻击', attacks)}
        ${metricCard('本次评估', evaluated)}
        ${metricCard('排除', excluded)}
      </div>
      ${rows.length
        ? `<div class="harvest-table-wrap"><table class="harvest-ranking-table"><caption>${escapeHtml(caption)}</caption><thead><tr><th scope="col">名次</th><th scope="col">生物</th><th scope="col">攻击与间隔</th><th scope="col">${escapeHtml(metricLabel)}</th><th scope="col">证据</th></tr></thead><tbody>${rows.map((row) => {
          const score = isEstimatedYield ? row.estimatedYieldPerNode : row.engineComparisonIndex;
          return `<tr><td><span class="rank-number">${escapeHtml(row.rank)}</span></td><td><strong>${escapeHtml(row.creature)}</strong>${row.dinoNameTag ? `<small>DinoNameTag：${escapeHtml(row.dinoNameTag)}</small>` : ''}${countValue(row.variantCount) !== null && Number(row.variantCount) > 1 ? `<small>${escapeHtml(formatCount(countValue(row.variantCount)))} 个变体归为同一物种</small>` : ''}${row.rideabilityStatus === 'ALLOWED' ? '<small>可骑乘已确认</small>' : ''}${row.tameabilityStatus === 'UNKNOWN' ? '<small>可驯服性尚未恢复</small>' : ''}</td><td class="harvest-attack-cell">${intervalCell(row)}</td><td class="score-cell">${formatScore(score)}${typeof row.relativeToNodeTopPercent === 'number' ? `<small>${escapeHtml(relativeLabel)} ${formatScore(row.relativeToNodeTopPercent)}%</small>` : ''}</td><td>${rowEvidence(row)}</td></tr>`;
        }).join('')}</tbody></table></div>`
        : `<div class="empty-state">${escapeHtml(emptyState)}</div>`}
      ${rows.length < 10 ? `<p class="hint">当前只有 ${escapeHtml(rows.length)} 条可排名记录，不足 10 条；不会用未知值补齐。</p>` : ''}
      <div class="harvest-ranking-details">
        <details>
          <summary>排除与无法排名（${escapeHtml(formatCount(excluded))}）</summary>
          <p class="hint">排除项不参与排行；未知、不兼容和条件未恢复均不会按 0 分处理。</p>
          ${reasonItems.length
            ? `<ul class="harvest-reason-list">${reasonItems.join('')}</ul>`
            : '<p class="hint">当前响应没有返回明确的排除原因明细。</p>'}
          <p class="hint">本次已计算 ${escapeHtml(formatCount(attackDisposition))} 个攻击结果：可排行 ${escapeHtml(formatCount(firstCount(coverage.attacksRanked)))}，未知 ${escapeHtml(formatCount(firstCount(coverage.attacksUnranked, coverage.nonRankedForNodeResource)))}，不兼容 ${escapeHtml(formatCount(firstCount(coverage.attacksIncompatible)))}。</p>
        </details>
        <details>
          <summary>证据与口径：${escapeHtml(evidenceLabel)}</summary>
          <dl class="harvest-evidence-list">
            <div><dt>范围状态</dt><dd><code>${escapeHtml(ranking.scopeStatus || 'UNKNOWN')}</code></dd></div>
            <div><dt>使用场景</dt><dd><code>${escapeHtml(ranking.methodology.usageScope || '旧版报告未标记')}</code></dd></div>
            <div><dt>攻击参数</dt><dd>${escapeHtml(formatCount(firstCount(coverage.attacksComplete)))} / ${escapeHtml(formatCount(attacks))} 完整</dd></div>
            <div><dt>伤害类型</dt><dd>${escapeHtml(formatCount(firstCount(coverage.damageTypesDecoded)))} 已恢复；${escapeHtml(formatCount(firstCount(coverage.damageTypesWithGaps)))} 有缺口</dd></div>
            <div><dt>源文件指纹</dt><dd>${coverage.sourceFingerprintsComplete === true ? '完整' : coverage.sourceFingerprintsComplete === false ? '不完整' : '旧版报告未标记'}</dd></div>
            <div><dt>公式版本</dt><dd><code>${escapeHtml(ranking.methodology.formulaVersion || '旧版报告未标记')}</code></dd></div>
          </dl>
          ${blockers.length ? `<ul class="harvest-blocker-list">${blockers.map((value) => `<li><code>${escapeHtml(value)}</code></li>`).join('')}</ul>` : ''}
          <p class="hint">${escapeHtml(scopeHint)}</p>
        </details>
      </div>
    </div>
  `;
}


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


export interface HarvestNodeFilterState {
  query: string;
  mapFamily: string;
  mapMode: HarvestMapFilterMode;
  resource: string;
}

export interface HarvestNodeSearchState extends HarvestNodeFilterState {
  offset: number;
  limit: number;
}


export function buildHarvestNodeSearchParams(
  state: HarvestNodeSearchState,
): URLSearchParams {
  const params = new URLSearchParams();
  if (state.query) {
    params.set('q', state.query);
  }
  if (state.mapFamily) {
    if (state.mapMode === 'evidenceExclusive') {
      params.set('onlyMapFamily', state.mapFamily);
    } else {
      params.set('map', state.mapFamily);
    }
  }
  if (state.resource) {
    params.set('resource', state.resource);
  }
  params.set('offset', String(Math.max(0, state.offset)));
  params.set('limit', String(Math.max(1, state.limit)));
  return params;
}


export function renderHarvestNodeFilterForm(
  page: HarvestNodePage | null,
  state: HarvestNodeFilterState,
): string {
  const exclusiveCounts = new Map(
    (page?.facets?.onlyMapFamilies || []).map((item) => [
      item.mapFamily.toLowerCase(),
      item.nodeCount,
    ]),
  );
  const mapOptionsByKey = new Map<string, string>();
  [
    ...(page?.coverage.mapScan?.mapFamilies || []),
    ...(page?.facets?.onlyMapFamilies || []).map((item) => item.mapFamily),
    ...(state.mapFamily ? [state.mapFamily] : []),
  ].forEach((family) => {
    const key = family.toLowerCase();
    if (!mapOptionsByKey.has(key)) {
      mapOptionsByKey.set(key, family);
    }
  });
  const mapOptions = Array.from(mapOptionsByKey.values()).sort(
    (left, right) => left.localeCompare(right),
  );
  const selectedMapKey = state.mapFamily.toLowerCase();
  const resourceOptionsByKey = new Map<string, HarvestResourceTypeFacet>();
  (page?.facets?.resources || []).forEach((item) => {
    const key = resourceFacetKey(item).trim();
    if (key && !resourceOptionsByKey.has(key.toLowerCase())) {
      resourceOptionsByKey.set(key.toLowerCase(), item);
    }
  });
  const resourceOptions = Array.from(resourceOptionsByKey.values());
  const exactSelectedResource = state.resource
    ? resourceOptions.find((item) => sameResourceIdentity(resourceFacetKey(item), state.resource))
    : undefined;
  const legacyClassMatches = state.resource && !exactSelectedResource
    ? resourceOptions.filter((item) => sameResourceIdentity(item.resource, state.resource))
    : [];
  let selectedResourceKey = exactSelectedResource
    ? resourceFacetKey(exactSelectedResource)
    : state.resource;
  if (state.resource && !exactSelectedResource && legacyClassMatches.length === 1) {
    selectedResourceKey = resourceFacetKey(legacyClassMatches[0]);
  } else if (state.resource && !exactSelectedResource && legacyClassMatches.length > 1) {
    resourceOptions.push({
      resourceKey: state.resource,
      resource: state.resource,
      displayName: `${resourceFilterDisplayName(state.resource)}（全部同名蓝图）`,
      nodeCount: page?.total ?? 0,
    });
  } else if (state.resource && !exactSelectedResource && legacyClassMatches.length === 0) {
    resourceOptions.push({
      resourceKey: state.resource,
      resource: resourceClassFromFilter(state.resource),
      displayName: resourceFilterDisplayName(state.resource),
      nodeCount: 0,
    });
  }
  resourceOptions.sort((left, right) => (
    (left.displayName || left.resource).localeCompare(right.displayName || right.resource)
    || resourceFacetKey(left).localeCompare(resourceFacetKey(right))
  ));
  const mapModeDisabled = !state.mapFamily;
  const exclusiveSelected = state.mapMode === 'evidenceExclusive' && !mapModeDisabled;
  const evidenceNote = mapModeDisabled
    ? '请先选择地图，再选择匹配方式。'
    : exclusiveSelected
      ? '“当前证据仅此地图”表示已恢复的正式可玩地图家族只有所选地图；地图使用证据尚未声明完整时，不代表该节点在全游戏中绝对不会出现在其他地图。测试和工具地图不参与该筛选。'
      : '“包含所选地图”会保留同时在其他地图出现的节点。';
  return `
    <form class="harvest-search panel" data-harvest-search>
      <label for="harvest-query">搜索与筛选资源节点</label>
      <div class="harvest-search-row">
        <input id="harvest-query" name="q" value="${escapeHtml(state.query)}" placeholder="例如 MetalRock、UmbrellaTree" autocomplete="off">
        <button class="button primary" type="submit">搜索</button>
      </div>
      <div class="harvest-filter-grid">
        <label for="harvest-map-filter">地图家族
          <select id="harvest-map-filter" name="mapFilter">
            <option value="">全部地图证据</option>
            ${mapOptions.map((family) => {
              const exclusiveCount = exclusiveCounts.get(family.toLowerCase());
              const countLabel = state.mapMode === 'evidenceExclusive' && exclusiveCount !== undefined
                ? ` · ${formatCount(exclusiveCount)} 个节点`
                : '';
              return `<option value="${escapeHtml(family)}" ${family.toLowerCase() === selectedMapKey ? 'selected' : ''}>${escapeHtml(displayMapFamily(family))}${escapeHtml(countLabel)}</option>`;
            }).join('')}
          </select>
        </label>
        <label for="harvest-map-mode">地图匹配方式
          <select id="harvest-map-mode" name="mapMode" aria-describedby="harvest-exclusive-map-note" ${mapModeDisabled ? 'disabled' : ''}>
            <option value="contains" ${!exclusiveSelected ? 'selected' : ''}>包含所选地图</option>
            <option value="evidenceExclusive" ${exclusiveSelected ? 'selected' : ''}>当前证据仅此地图</option>
          </select>
        </label>
        <label for="harvest-resource-filter">包含资源类型
          <select id="harvest-resource-filter" name="resourceFilter">
            <option value="">全部资源类型</option>
            ${resourceOptions.map((item) => {
              const key = resourceFacetKey(item);
              return `<option value="${escapeHtml(key)}" ${sameResourceIdentity(key, selectedResourceKey) ? 'selected' : ''}>${escapeHtml(item.displayName || item.resource)} · ${escapeHtml(formatCount(item.nodeCount))} 个节点 — ${escapeHtml(item.resource)}</option>`;
            }).join('')}
          </select>
        </label>
      </div>
      <p id="harvest-exclusive-map-note" class="harvest-filter-note ${exclusiveSelected ? 'warn' : ''}">${escapeHtml(evidenceNote)}</p>
    </form>
  `;
}


export function renderHarvestNodeEmptyState(
  page: HarvestNodePage | null,
  state: HarvestNodeFilterState,
): string {
  const conditions: string[] = [];
  if (state.query) {
    conditions.push(`搜索“${state.query}”`);
  }
  if (state.mapFamily) {
    const mapName = displayMapFamily(state.mapFamily);
    conditions.push(
      state.mapMode === 'evidenceExclusive'
        ? `当前证据仅属于 ${mapName}`
        : `地图证据包含 ${mapName}`,
    );
  }
  if (state.resource) {
    const resourceFacets = page?.facets?.resources || [];
    const exactResourceFacet = resourceFacets.find(
      (item) => sameResourceIdentity(resourceFacetKey(item), state.resource),
    );
    const legacyResourceFacets = exactResourceFacet
      ? []
      : resourceFacets.filter((item) => sameResourceIdentity(item.resource, state.resource));
    const resourceFacet = exactResourceFacet
      || (legacyResourceFacets.length === 1 ? legacyResourceFacets[0] : undefined);
    conditions.push(
      `包含 ${resourceFacet?.displayName || resourceFilterDisplayName(state.resource)}`,
    );
  }
  if (!conditions.length) {
    return '<div class="empty-state">当前数据库中没有可显示的资源节点。</div>';
  }
  return `
    <div class="empty-state">
      <p>当前数据库证据中，没有同时满足这些条件的资源节点：${escapeHtml(conditions.join('、'))}。</p>
      <button class="button secondary" type="button" data-harvest-action="clear-filters">清除全部条件</button>
    </div>
  `;
}


export class HarvestExplorer {
  private mode: 'nodes' | 'creatures' | 'build' = 'nodes';
  private readonly creatureExplorer: HarvestCreatureExplorer;
  private readonly buildControl: HarvestBuildControl;
  private page: HarvestNodePage | null = null;
  private selectedNode: HarvestNode | null = null;
  private ranking: HarvestRankingResult | null = null;
  private rankingMetric: HarvestRankingMetric = 'staticCompleteNodeTargetYield';
  private rankingVariantPolicy = 'CANONICAL_VARIANT';
  private rankingRuntimeProfileId = '';
  private rankingIncludePreliminary = false;
  private query = '';
  private mapFilter = '';
  private mapMode: HarvestMapFilterMode = 'contains';
  private resourceFilter = '';
  private offset = 0;
  private loadingPage = false;
  private loadingNode = false;
  private loadingRanking = false;
  private error = '';
  private errorCode = '';
  private initialized = false;
  private requestSequence = 0;
  private nodeRequestSequence = 0;
  private rankingRequestSequence = 0;
  private listController: AbortController | null = null;
  private nodeController: AbortController | null = null;
  private rankingController: AbortController | null = null;
  private searchTimer = 0;

  constructor(private readonly requestRender: () => void) {
    const params = new URLSearchParams(window.location.search);
    const requestedMode = params.get('harvestMode');
    this.mode = requestedMode === 'creatures' || requestedMode === 'build'
      ? requestedMode
      : 'nodes';
    this.query = params.get('q') || '';
    const onlyMapFamily = params.get('onlyMapFamily') || '';
    this.mapFilter = onlyMapFamily || params.get('mapFilter') || '';
    this.mapMode = onlyMapFamily ? 'evidenceExclusive' : 'contains';
    this.resourceFilter = params.get('resourceFilter') || '';
    const requestedMetric = params.get('rankingMetric');
    if (
      requestedMetric === 'staticYieldPerAttackCycleSecond'
      || requestedMetric === 'observedYieldPerNode'
      || requestedMetric === 'observedYieldPerSecond'
    ) {
      this.rankingMetric = requestedMetric;
    }
    const requestedVariant = params.get('rankingVariant');
    if (
      requestedVariant === 'ALL_VARIANTS'
      || requestedVariant === 'BEST_DISCOVERED_VARIANT_EXPLORATORY'
    ) {
      this.rankingVariantPolicy = requestedVariant;
    }
    this.rankingRuntimeProfileId = params.get('rankingRuntimeProfile') || '';
    this.rankingIncludePreliminary = params.get('rankingIncludePreliminary') === 'true';
    this.creatureExplorer = new HarvestCreatureExplorer(requestRender);
    this.buildControl = new HarvestBuildControl(requestRender);
  }

  ensureLoaded(force = false): void {
    if (this.mode === 'creatures') {
      this.creatureExplorer.ensureLoaded(force);
      return;
    }
    if (this.mode === 'build') {
      this.buildControl.ensureLoaded(force);
      return;
    }
    if (force) {
      this.initialized = false;
    }
    if (this.initialized || this.loadingPage) {
      return;
    }
    this.initialized = true;
    void this.loadNodes();
  }

  render(): string {
    const content = this.mode === 'creatures'
      ? this.creatureExplorer.render()
      : this.mode === 'build'
        ? this.buildControl.render()
        : this.renderNodeExplorer();
    return `
      <nav class="harvest-mode-tabs" aria-label="采集查询视角">
        <button class="harvest-mode-tab ${this.mode === 'nodes' ? 'active' : ''}" type="button" data-harvest-mode="nodes" aria-current="${this.mode === 'nodes' ? 'page' : 'false'}">
          <span>按资源点</span><small>节点 → 资源 → 恐龙 Top 10</small>
        </button>
        <button class="harvest-mode-tab ${this.mode === 'creatures' ? 'active' : ''}" type="button" data-harvest-mode="creatures" aria-current="${this.mode === 'creatures' ? 'page' : 'false'}">
          <span>按恐龙</span><small>恐龙 → 擅长节点与资源</small>
        </button>
        <button class="harvest-mode-tab ${this.mode === 'build' ? 'active' : ''}" type="button" data-harvest-mode="build" aria-current="${this.mode === 'build' ? 'page' : 'false'}">
          <span>数据构建</span><small>全量重建、进度与取消</small>
        </button>
      </nav>
      ${content}
    `;
  }

  private renderNodeExplorer(): string {
    return `
      <section class="harvest-hero" aria-labelledby="harvest-title">
        <div>
          <p class="eyebrow">ARK RESOURCE NODE EXPLORER</p>
          <h2 id="harvest-title">资源点采集排行</h2>
          <p>先选择真实资源节点，再选择该节点产出的资源。排行严格绑定节点的 HarvestComponent，不跨节点混排。</p>
        </div>
        <button class="button ghost" type="button" data-harvest-action="refresh">重新读取索引</button>
      </section>
      ${this.renderDatasetBar()}
      ${renderHarvestNodeFilterForm(this.page, {
        query: this.query,
        mapFamily: this.mapFilter,
        mapMode: this.mapMode,
        resource: this.resourceFilter,
      })}
      <div class="harvest-live" aria-live="polite">
        ${this.loadingPage ? '正在读取资源节点…' : ''}
        ${this.loadingNode ? '正在读取节点详情…' : ''}
        ${this.loadingRanking ? '正在计算所选资源的排行…' : ''}
        ${!this.loadingPage && !this.loadingNode && !this.loadingRanking && this.page ? `共找到 ${this.page.total} 个节点。` : ''}
      </div>
      ${this.error ? this.renderError() : ''}
      <div class="harvest-explorer-grid">
        ${this.renderNodeList()}
        ${this.renderDetail()}
      </div>
    `;
  }

  bind(): void {
    document.querySelectorAll<HTMLButtonElement>('[data-harvest-mode]').forEach((button) => {
      button.addEventListener('click', () => {
        const requested = button.dataset.harvestMode;
        const nextMode = requested === 'creatures' || requested === 'build'
          ? requested
          : 'nodes';
        if (nextMode === this.mode) {
          return;
        }
        this.mode = nextMode;
        const url = new URL(window.location.href);
        url.searchParams.set('view', 'harvest');
        if (nextMode === 'nodes') {
          url.searchParams.delete('harvestMode');
        } else {
          url.searchParams.set('harvestMode', nextMode);
        }
        window.history.replaceState({}, '', url);
        this.requestRender();
      });
    });
    if (this.mode === 'creatures') {
      this.creatureExplorer.bind();
      return;
    }
    if (this.mode === 'build') {
      this.buildControl.bind();
      return;
    }
    document.querySelector<HTMLFormElement>('[data-harvest-search]')?.addEventListener('submit', (event) => {
      event.preventDefault();
      this.cancelPendingSearch();
      const input = document.querySelector<HTMLInputElement>('#harvest-query');
      const mapInput = document.querySelector<HTMLSelectElement>('#harvest-map-filter');
      const mapModeInput = document.querySelector<HTMLSelectElement>('#harvest-map-mode');
      const resourceInput = document.querySelector<HTMLSelectElement>('#harvest-resource-filter');
      this.query = input?.value.trim() || '';
      this.mapFilter = mapInput?.value.trim() || '';
      this.mapMode = this.mapFilter && mapModeInput?.value === 'evidenceExclusive'
        ? 'evidenceExclusive'
        : 'contains';
      this.resourceFilter = resourceInput?.value.trim() || '';
      this.resetNodeSelection();
      this.updateUrl({
        q: this.query,
        ...this.nodeFilterUrlValues(),
        node: '',
        resource: '',
      });
      void this.loadNodes();
    });

    document.querySelector<HTMLInputElement>('#harvest-query')?.addEventListener('input', (event) => {
      const value = (event.currentTarget as HTMLInputElement).value;
      window.clearTimeout(this.searchTimer);
      this.searchTimer = window.setTimeout(() => {
        this.searchTimer = 0;
        this.query = value.trim();
        this.resetNodeSelection();
        this.updateUrl({ q: this.query, node: '', resource: '' });
        void this.loadNodes();
      }, 300);
    });

    document.querySelector<HTMLSelectElement>('#harvest-map-filter')?.addEventListener('change', (event) => {
      this.mapFilter = (event.currentTarget as HTMLSelectElement).value.trim();
      if (!this.mapFilter) {
        this.mapMode = 'contains';
      }
      this.resourceFilter = '';
      this.resetNodeSelection();
      this.updateUrl({
        ...this.nodeFilterUrlValues(),
        node: '',
        resource: '',
      });
      void this.loadNodes();
    });

    document.querySelector<HTMLSelectElement>('#harvest-map-mode')?.addEventListener('change', (event) => {
      const requestedMode = (event.currentTarget as HTMLSelectElement).value;
      this.mapMode = this.mapFilter && requestedMode === 'evidenceExclusive'
        ? 'evidenceExclusive'
        : 'contains';
      this.resourceFilter = '';
      this.resetNodeSelection();
      this.updateUrl({
        ...this.nodeFilterUrlValues(),
        node: '',
        resource: '',
      });
      void this.loadNodes();
    });

    document.querySelector<HTMLSelectElement>('#harvest-resource-filter')?.addEventListener('change', (event) => {
      this.resourceFilter = (event.currentTarget as HTMLSelectElement).value.trim();
      this.resetNodeSelection();
      this.updateUrl({
        ...this.nodeFilterUrlValues(),
        node: '',
        resource: '',
      });
      void this.loadNodes();
    });

    document.querySelectorAll<HTMLButtonElement>('[data-harvest-node]').forEach((button) => {
      button.addEventListener('click', () => void this.selectNode(button.dataset.harvestNode || ''));
    });
    document.querySelectorAll<HTMLButtonElement>('[data-harvest-resource]').forEach((button) => {
      button.addEventListener('click', () => void this.selectResource(button.dataset.harvestResource || ''));
    });
    document.querySelector<HTMLSelectElement>('#harvest-ranking-metric')?.addEventListener('change', (event) => {
      const requested = (event.currentTarget as HTMLSelectElement).value;
      if (
        requested === 'staticCompleteNodeTargetYield'
        || requested === 'staticYieldPerAttackCycleSecond'
        || requested === 'observedYieldPerNode'
        || requested === 'observedYieldPerSecond'
      ) {
        this.rankingMetric = requested;
        const resourceId = this.ranking?.resource.nodeResourceId || '';
        this.updateUrl({ rankingMetric: requested });
        if (resourceId) {
          void this.selectResource(resourceId);
        }
      }
    });
    document.querySelector<HTMLSelectElement>('#harvest-ranking-variant')?.addEventListener('change', (event) => {
      const requested = (event.currentTarget as HTMLSelectElement).value;
      if (
        requested === 'CANONICAL_VARIANT'
        || requested === 'ALL_VARIANTS'
        || requested === 'BEST_DISCOVERED_VARIANT_EXPLORATORY'
      ) {
        this.rankingVariantPolicy = requested;
        const resourceId = this.ranking?.resource.nodeResourceId || '';
        this.updateUrl({ rankingVariant: requested });
        if (resourceId) {
          void this.selectResource(resourceId);
        }
      }
    });
    document.querySelector<HTMLSelectElement>('#harvest-ranking-runtime-profile')?.addEventListener('change', (event) => {
      this.rankingRuntimeProfileId = (event.currentTarget as HTMLSelectElement).value.trim();
      const resourceId = this.ranking?.resource.nodeResourceId || '';
      this.updateUrl({ rankingRuntimeProfile: this.rankingRuntimeProfileId });
      if (resourceId) {
        void this.selectResource(resourceId);
      }
    });
    document.querySelector<HTMLInputElement>('#harvest-ranking-include-preliminary')?.addEventListener('change', (event) => {
      this.rankingIncludePreliminary = (event.currentTarget as HTMLInputElement).checked;
      const resourceId = this.ranking?.resource.nodeResourceId || '';
      this.updateUrl({
        rankingIncludePreliminary: this.rankingIncludePreliminary ? 'true' : '',
      });
      if (resourceId) {
        void this.selectResource(resourceId);
      }
    });
    document.querySelectorAll<HTMLButtonElement>('[data-harvest-page]').forEach((button) => {
      button.addEventListener('click', () => {
        this.offset = Math.max(0, Number(button.dataset.harvestPage || 0));
        this.selectedNode = null;
        this.ranking = null;
        void this.loadNodes();
      });
    });
    document.querySelectorAll<HTMLButtonElement>('[data-harvest-action]').forEach((button) => {
      button.addEventListener('click', () => {
        if (button.dataset.harvestAction === 'refresh' || button.dataset.harvestAction === 'retry') {
          this.error = '';
          this.ensureLoaded(true);
        } else if (button.dataset.harvestAction === 'clear-filters') {
          this.cancelPendingSearch();
          this.query = '';
          this.mapFilter = '';
          this.mapMode = 'contains';
          this.resourceFilter = '';
          this.resetNodeSelection();
          this.updateUrl({
            q: '',
            mapFilter: '',
            onlyMapFamily: '',
            resourceFilter: '',
            node: '',
            resource: '',
          });
          void this.loadNodes();
        }
      });
    });
  }

  private renderDatasetBar(): string {
    if (!this.page) {
      return '';
    }
    return renderHarvestDatasetBar(this.page);
  }

  private renderError(): string {
    const rebuildRequired = this.errorCode === 'HARVEST_DATASET_INVALID'
      || this.errorCode === 'HARVEST_DATASET_NOT_BUILT';
    return `
      <section class="empty-state harvest-error">
        <strong>${rebuildRequired ? '排行数据身份过期，需要重建' : '资源节点数据暂时不可用'}</strong>
        <p>${escapeHtml(this.error)}</p>
        ${rebuildRequired ? '<p>系统已停止使用旧 revision；请切换到“数据构建”完成重建。</p>' : ''}
        <button class="button secondary" type="button" data-harvest-action="retry">重试</button>
      </section>
    `;
  }

  private renderNodeList(): string {
    if (this.loadingPage && !this.page) {
      return '<section class="panel harvest-node-pane"><div class="empty-state">正在加载节点索引…</div></section>';
    }
    if (!this.page || !this.page.items.length) {
      return `
        <section class="panel harvest-node-pane">
          <div class="panel-heading"><h2>资源节点</h2></div>
          ${renderHarvestNodeEmptyState(this.page, {
            query: this.query,
            mapFamily: this.mapFilter,
            mapMode: this.mapMode,
            resource: this.resourceFilter,
          })}
        </section>
      `;
    }
    const cards = this.page.items.map((node) => this.renderNodeCard(node)).join('');
    const previousOffset = Math.max(0, this.page.offset - this.page.limit);
    return `
      <section class="panel harvest-node-pane" aria-label="资源节点列表">
        <div class="panel-heading">
          <div><p class="eyebrow">RESOURCE NODES</p><h2>${this.page.total} 个匹配节点</h2></div>
          <span class="soft-label">${this.page.offset + 1}–${this.page.offset + this.page.items.length}</span>
        </div>
        <div class="harvest-node-list">${cards}</div>
        <div class="harvest-pagination">
          <button class="button ghost" type="button" data-harvest-page="${previousOffset}" ${this.page.offset <= 0 ? 'disabled' : ''}>上一页</button>
          <button class="button ghost" type="button" data-harvest-page="${this.page.nextOffset ?? 0}" ${this.page.nextOffset === null ? 'disabled' : ''}>下一页</button>
        </div>
      </section>
    `;
  }

  private renderNodeCard(node: HarvestNode): string {
    const resources = node.resources?.items || [];
    const families = mapFamilies(node);
    const active = this.selectedNode?.id === node.id;
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

  private renderDetail(): string {
    const node = this.selectedNode;
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
              ? resources.map((resource) => `<button class="harvest-resource-button ${this.ranking?.resource.nodeResourceId === resource.nodeResourceId ? 'active' : ''}" type="button" data-harvest-resource="${escapeHtml(resource.nodeResourceId)}"><strong>${escapeHtml(resourceName(resource))}</strong><small>${escapeHtml(resource.resource)}</small></button>`).join('')
              : '<div class="empty-state compact">该节点的资源条目尚未恢复，不能生成数值排行。</div>'}
          </div>
        </div>
        ${this.renderRanking()}
      </section>
    `;
  }

  private renderRanking(): string {
    if (this.loadingRanking) {
      return '<div class="harvest-detail-section"><div class="empty-state">正在读取排行…</div></div>';
    }
    if (!this.ranking) {
      return '<div class="harvest-detail-section"><div class="empty-state compact">点击一个产出资源后才会读取排行。</div></div>';
    }
    return renderHarvestRankingResult(this.ranking);
  }

  private async loadNodes(): Promise<void> {
    this.listController?.abort();
    this.nodeController?.abort();
    this.rankingController?.abort();
    this.nodeRequestSequence += 1;
    this.rankingRequestSequence += 1;
    this.listController = new AbortController();
    const sequence = ++this.requestSequence;
    this.loadingPage = true;
    this.loadingNode = false;
    this.loadingRanking = false;
    this.error = '';
    this.requestRender();
    try {
      const params = buildHarvestNodeSearchParams({
        query: this.query,
        mapFamily: this.mapFilter,
        mapMode: this.mapMode,
        resource: this.resourceFilter,
        offset: this.offset,
        limit: 16,
      });
      const page = await fetchHarvestJson<HarvestNodePage>(
        `/api/harvest/nodes?${params.toString()}`,
        this.listController.signal,
      );
      if (sequence !== this.requestSequence) {
        return;
      }
      this.page = page;
      const urlParams = new URLSearchParams(window.location.search);
      const requestedNode = urlParams.get('node');
      const requestedResource = urlParams.get('resource');
      const initialNode = page.items.find((item) => item.id === requestedNode) || page.items[0] || null;
      this.selectedNode = initialNode;
      this.ranking = null;
      if (initialNode) {
        this.updateUrl({ node: initialNode.id });
        void this.selectNode(initialNode.id, requestedResource || '');
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      this.error = error instanceof Error ? error.message : String(error);
      this.errorCode = error instanceof HarvestApiError ? error.code || '' : '';
    } finally {
      if (sequence === this.requestSequence) {
        this.loadingPage = false;
        this.requestRender();
      }
    }
  }

  private async selectNode(nodeId: string, requestedResource = ''): Promise<void> {
    if (!nodeId) {
      return;
    }
    this.nodeController?.abort();
    this.rankingController?.abort();
    this.nodeController = new AbortController();
    const sequence = ++this.nodeRequestSequence;
    this.rankingRequestSequence += 1;
    this.loadingNode = true;
    this.loadingRanking = false;
    this.ranking = null;
    this.error = '';
    this.requestRender();
    try {
      const payload = await fetchHarvestJson<HarvestNodeDetail>(
        `/api/harvest/nodes/${encodeURIComponent(nodeId)}`,
        this.nodeController.signal,
      );
      if (sequence !== this.nodeRequestSequence) {
        return;
      }
      this.selectedNode = payload.node;
      this.updateUrl({ node: nodeId, resource: '' });
      if (
        requestedResource
        && payload.node.resources?.items.some((item) => item.nodeResourceId === requestedResource)
      ) {
        void this.selectResource(requestedResource);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      if (sequence !== this.nodeRequestSequence) {
        return;
      }
      this.error = error instanceof Error ? error.message : String(error);
      this.errorCode = error instanceof HarvestApiError ? error.code || '' : '';
    } finally {
      if (sequence === this.nodeRequestSequence) {
        this.loadingNode = false;
        this.requestRender();
      }
    }
  }

  private async selectResource(nodeResourceId: string): Promise<void> {
    if (!this.selectedNode || !nodeResourceId) {
      return;
    }
    this.rankingController?.abort();
    this.rankingController = new AbortController();
    const sequence = ++this.rankingRequestSequence;
    const nodeId = this.selectedNode.id;
    this.loadingRanking = true;
    this.error = '';
    this.requestRender();
    try {
      const params = new URLSearchParams({
        nodeId,
        nodeResourceId,
        limit: '10',
        policy: 'includeConditional',
        metric: this.rankingMetric,
        variantPolicy: this.rankingVariantPolicy,
        availabilityPolicy: 'GLOBAL_TRANSFER_ALLOWED',
      });
      if (this.rankingRuntimeProfileId) {
        params.set('runtimeProfileId', this.rankingRuntimeProfileId);
      }
      if (this.rankingIncludePreliminary) {
        params.set('includePreliminary', 'true');
      }
      const ranking = await fetchHarvestJson<HarvestRankingResult>(
        `/api/harvest/rankings?${params.toString()}`,
        this.rankingController.signal,
      );
      if (
        sequence !== this.rankingRequestSequence
        || this.selectedNode?.id !== nodeId
      ) {
        return;
      }
      this.ranking = ranking;
      this.updateUrl({
        node: nodeId,
        resource: nodeResourceId,
        rankingMetric: this.rankingMetric,
        rankingVariant: this.rankingVariantPolicy,
        rankingRuntimeProfile: this.rankingRuntimeProfileId,
        rankingIncludePreliminary: this.rankingIncludePreliminary ? 'true' : '',
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      if (sequence !== this.rankingRequestSequence) {
        return;
      }
      this.error = error instanceof Error ? error.message : String(error);
      this.errorCode = error instanceof HarvestApiError ? error.code || '' : '';
    } finally {
      if (sequence === this.rankingRequestSequence) {
        this.loadingRanking = false;
        this.requestRender();
      }
    }
  }

  private updateUrl(values: {
    q?: string;
    mapFilter?: string;
    onlyMapFamily?: string;
    resourceFilter?: string;
    node?: string;
    resource?: string;
    rankingMetric?: string;
    rankingVariant?: string;
    rankingRuntimeProfile?: string;
    rankingIncludePreliminary?: string;
  }): void {
    const url = new URL(window.location.href);
    url.searchParams.set('view', 'harvest');
    Object.entries(values).forEach(([key, value]) => {
      if (value) {
        url.searchParams.set(key, value);
      } else {
        url.searchParams.delete(key);
      }
    });
    window.history.replaceState({}, '', url);
  }

  private nodeFilterUrlValues(): {
    mapFilter: string;
    onlyMapFamily: string;
    resourceFilter: string;
  } {
    return {
      mapFilter: this.mapMode === 'contains' ? this.mapFilter : '',
      onlyMapFamily: this.mapMode === 'evidenceExclusive' ? this.mapFilter : '',
      resourceFilter: this.resourceFilter,
    };
  }

  private resetNodeSelection(): void {
    this.offset = 0;
    this.selectedNode = null;
    this.ranking = null;
  }

  private cancelPendingSearch(): void {
    window.clearTimeout(this.searchTimer);
    this.searchTimer = 0;
  }
}
