import { escapeHtml } from '../../shared/html';
import type { HarvestRankingResult } from '../types';
import {
  countValue,
  firstCount,
  formatCount,
  formatScore,
  formatSeconds,
  hasEstimatedYieldMetric,
  resourceName,
  sumCounts,
} from '../format';

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
