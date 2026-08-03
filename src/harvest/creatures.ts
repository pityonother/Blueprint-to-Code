import { fetchHarvestJson } from './api';
import { escapeHtml } from '../shared/html';
import type {
  HarvestCreaturePage,
  HarvestCreatureSpecialties,
  HarvestCreatureSummary,
  HarvestRankingMetric,
} from './types';

function finiteCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.round(value)
    : null;
}


function formatCount(value: unknown): string {
  const count = finiteCount(value);
  return count === null ? '—' : new Intl.NumberFormat('zh-CN').format(count);
}


function formatScore(value: unknown, maximumFractionDigits = 2): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits }).format(value)
    : '—';
}


function displayResource(resource: { displayName?: string; resource?: string }): string {
  return resource.displayName
    || String(resource.resource || '').replace('PrimalItemResource_', '').replace(/_C$/, '')
    || '未知资源';
}


function displayCreature(creature: HarvestCreatureSummary): string {
  const name = String(creature.name || '').trim();
  if (name && !/[<>]/.test(name)) {
    return name;
  }
  return creature.dinoNameTag || creature.speciesKey || '未知生物';
}


function hasEstimatedYieldMetric(result: HarvestCreatureSpecialties): boolean {
  return result.methodology.metric === 'estimatedYieldPerNode'
    || result.items.some(
      (row) => typeof row.estimatedYieldPerNode === 'number'
        && Number.isFinite(row.estimatedYieldPerNode),
    );
}


function renderSpecialtyTier(
  heading: string,
  rows: NonNullable<HarvestCreatureSpecialties['items']>,
  unit: string,
  empty: string,
  conditional: boolean,
): string {
  return `
    <section class="harvest-ranking-tier ${conditional ? 'conditional' : 'confirmed'}">
      <h3>${escapeHtml(heading)}</h3>
      ${rows.length ? `<div class="harvest-specialty-list" role="list">${rows.map((row) => `
        <article class="harvest-specialty-row" role="listitem">
          <div class="harvest-specialty-rank"><span>${escapeHtml(row.rank)}</span><small>本榜名次</small></div>
          <div class="harvest-specialty-identity">
            <strong>${escapeHtml(displayResource(row.resource))}</strong>
            <span>${escapeHtml(row.node.name || row.node.id)}</span>
            <code>${escapeHtml(row.resource.resource)}</code>
          </div>
          <div class="harvest-specialty-score">
            <strong>相对同层节点榜首 ${escapeHtml(formatScore(row.relativeToNodeTopPercent))}%</strong>
            <small>绝对值 ${escapeHtml(formatScore(row.selectedMetricValue))}${unit ? ` <code>${escapeHtml(unit)}</code>` : ''}</small>
            <small>同层榜首 ${escapeHtml(formatScore(row.nodeTopSelectedMetricValue))}${unit ? ` <code>${escapeHtml(unit)}</code>` : ''}</small>
            ${row.runtimeStatus ? `<small>实测：<code>${escapeHtml(row.runtimeStatus)}</code></small>` : ''}
          </div>
          <div class="harvest-specialty-attack">
            <strong>${escapeHtml(row.attackName || '攻击名称未恢复')}</strong>
            <small>榜首：${escapeHtml(row.nodeTop.creature || row.nodeTop.speciesKey || '未知')} · ${escapeHtml(row.nodeTop.attackName || '攻击未知')}</small>
            <small>变体：<code>${escapeHtml(row.variantSelection?.selectedObjectPath || row.creatureObjectPath || '未标记')}</code></small>
            <span class="status-pill ${row.rankingTier === 'CONFIRMED' ? 'good' : 'warn'}">${row.rankingTier === 'CONFIRMED' ? '已确认' : '条件性估算'}</span>
          </div>
        </article>
      `).join('')}</div>` : `<div class="empty-state compact">${escapeHtml(empty)}</div>`}
    </section>
  `;
}


function renderHarvestCreatureSpecialtiesV2(
  result: HarvestCreatureSpecialties,
): string {
  const confirmed = result.confirmedItems || [];
  const conditional = result.conditionalItems || [];
  const metric = result.methodology.metric;
  const unit = result.methodology.unit || '';
  const variant = result.queryPolicy?.variant || 'CANONICAL_VARIANT';
  const includePreliminary = result.queryPolicy?.includePreliminary === true;
  const runtimeProfilesAvailable = result.runtimeCoverage?.runtimeProfilesAvailable || [];
  const runtimeProfileSelected = result.runtimeCoverage?.runtimeProfileSelected
    || result.queryPolicy?.runtimeProfileId
    || '';
  const observedMetric = result.methodology.runtime === true || metric.startsWith('observed');
  const runtimeAvailable = runtimeProfilesAvailable.length > 0
    || (result.runtimeCoverage?.publishableConfirmedRows || 0) > 0
    || (includePreliminary && (result.runtimeCoverage?.preliminaryRows || 0) > 0);
  const runtimeProfileReady = runtimeProfilesAvailable.length <= 1 || Boolean(runtimeProfileSelected);
  const observedMetricAvailable = runtimeAvailable && runtimeProfileReady;
  const runtimeProfileOptions = runtimeProfilesAvailable.map((profileId) => `
    <option value="${escapeHtml(profileId)}" ${profileId === runtimeProfileSelected ? 'selected' : ''}>${escapeHtml(profileId)}</option>
  `).join('');
  return `
    <section class="panel harvest-creature-detail-pane" aria-label="恐龙擅长资源排行" data-ranking-contract="v2">
      <div class="harvest-ranking-heading">
        <div>
          <p class="eyebrow">RELATIVE-FIRST · RANKING CONTRACT V2</p>
          <h2>${escapeHtml(displayCreature(result.species))} 擅长什么</h2>
          <p>服务端先按同证据层的相对节点榜首百分比，再按所选绝对指标排序；界面不重新排序。</p>
        </div>
        <span class="status-pill ${result.confirmedStatus === 'AVAILABLE' ? 'good' : 'warn'}">已确认榜 ${result.confirmedStatus === 'AVAILABLE' ? '可用' : '不可用'}</span>
      </div>
      <div class="harvest-ranking-policy-controls" aria-label="反向排行口径">
        <label>指标
          <select id="harvest-specialty-metric">
            <option value="staticCompleteNodeTargetYield" ${metric === 'staticCompleteNodeTargetYield' ? 'selected' : ''}>静态单节点总产量</option>
            <option value="staticYieldPerAttackCycleSecond" ${metric === 'staticYieldPerAttackCycleSecond' ? 'selected' : ''}>静态攻击周期速度</option>
            <option value="observedYieldPerNode" ${metric === 'observedYieldPerNode' ? 'selected' : ''} ${observedMetricAvailable ? '' : 'disabled'}>受控实测单节点${runtimeAvailable ? (runtimeProfileReady ? '' : '（先选环境）') : '（未实测）'}</option>
            <option value="observedYieldPerSecond" ${metric === 'observedYieldPerSecond' ? 'selected' : ''} ${observedMetricAvailable ? '' : 'disabled'}>受控实测每秒${runtimeAvailable ? (runtimeProfileReady ? '' : '（先选环境）') : '（未实测）'}</option>
          </select>
        </label>
        <label>变体
          <select id="harvest-specialty-variant">
            <option value="CANONICAL_VARIANT" ${variant === 'CANONICAL_VARIANT' ? 'selected' : ''}>规范变体（默认）</option>
            <option value="ALL_VARIANTS" ${variant === 'ALL_VARIANTS' ? 'selected' : ''}>全部变体</option>
            <option value="BEST_DISCOVERED_VARIANT_EXPLORATORY" ${variant === 'BEST_DISCOVERED_VARIANT_EXPLORATORY' ? 'selected' : ''}>探索性最高变体</option>
          </select>
        </label>
        ${runtimeProfilesAvailable.length ? `<label>实测环境
          <select id="harvest-specialty-runtime-profile">
            <option value="">${runtimeProfilesAvailable.length === 1 ? '自动使用唯一环境' : '请选择一个环境'}</option>
            ${runtimeProfileOptions}
          </select>
        </label>
        <label><input id="harvest-specialty-include-preliminary" type="checkbox" ${includePreliminary ? 'checked' : ''}> 显式包含初步观察</label>` : ''}
        <span>地图可用性：<code>${escapeHtml(result.queryPolicy?.availability || 'GLOBAL_TRANSFER_ALLOWED')}</code></span>
        ${observedMetric ? `<span>runtimeProfileId：<code>${escapeHtml(runtimeProfileSelected || 'AUTO_OR_REQUIRED')}</code></span>` : ''}
        ${observedMetric ? `<span><code>includePreliminary=${includePreliminary ? 'true' : 'false'}</code></span>` : ''}
      </div>
      ${renderSpecialtyTier('已确认专长（独立编号）', confirmed, unit, '没有已确认专长；条件性结果不会被提升。', false)}
      ${renderSpecialtyTier('条件性专长（不占已确认名次）', conditional, unit, '当前请求没有条件性专长。', true)}
      <details class="harvest-scope-details">
        <summary>证据身份与排序合同</summary>
        <p>Metric <code>${escapeHtml(metric)}</code></p>
        <p>Score basis <code>${escapeHtml(result.methodology.scoreBasis || '缺失')}</code></p>
        <p>Unit <code>${escapeHtml(unit || '未标记')}</code></p>
        <p><code>${escapeHtml(result.methodology.sortMetric || '')}</code></p>
        <p>Extractor <code>${escapeHtml(result.identity?.extractorVersion || '缺失')}</code></p>
        <p>Model <code>${escapeHtml(result.identity?.modelVersion || '缺失')}</code></p>
        <p>Policy <code>${escapeHtml(result.identity?.policyVersion || '缺失')}</code></p>
        <p>Result schema <code>${escapeHtml(result.identity?.resultSchemaVersion || result.schema)}</code></p>
        ${observedMetric ? `<p>runtimeProfilesAvailable <code>${escapeHtml(runtimeProfilesAvailable.join(', ') || '无')}</code></p>` : ''}
        ${observedMetric ? `<p>runtimeProfileSelected <code>${escapeHtml(runtimeProfileSelected || '未选择')}</code></p>` : ''}
        ${observedMetric ? `<p><code>includePreliminary=${includePreliminary ? 'true' : 'false'}</code></p>` : ''}
      </details>
    </section>
  `;
}


function statusSummary(
  values: string[] | undefined,
  empty: string,
  labels: Record<string, string>,
): string {
  const normalized = Array.from(new Set((values || []).filter(Boolean)));
  return normalized.length
    ? normalized.map((value) => labels[value] || value).join(' / ')
    : empty;
}


export function renderHarvestCreaturePage(
  page: HarvestCreaturePage,
  selectedSpeciesKey = '',
): string {
  if (!page.items.length) {
    return `
      <section class="panel harvest-creature-list-pane" aria-label="恐龙列表">
        <div class="panel-heading"><h2>恐龙</h2></div>
        <div class="empty-state">没有找到匹配的恐龙。</div>
      </section>
    `;
  }
  const cards = page.items.map((creature) => {
    const active = creature.speciesKey === selectedSpeciesKey;
    const initials = displayCreature(creature).slice(0, 2).toUpperCase();
    return `
      <button class="harvest-creature-card ${active ? 'active' : ''}" type="button"
        data-harvest-species="${escapeHtml(creature.speciesKey)}" aria-pressed="${active}">
        <span class="harvest-creature-mark" aria-hidden="true">${escapeHtml(initials)}</span>
        <span class="harvest-creature-copy">
          <strong>${escapeHtml(displayCreature(creature))}</strong>
          <small>${escapeHtml(creature.name || creature.speciesKey)}</small>
          <span>
            <small>${escapeHtml(formatCount(creature.variantCount))} 个变体</small>
            <small>${escapeHtml(formatCount(creature.attackCount))} 个攻击</small>
          </span>
        </span>
        <span class="harvest-creature-flags">
          <small>${escapeHtml(statusSummary(creature.tameabilityStatuses, '驯服性未知', { ALLOWED: '可驯服', PREVENTED: '不可驯服', UNKNOWN: '驯服性未知' }))}</small>
          <small>${escapeHtml(statusSummary(creature.rideabilityStatuses, '骑乘性未知', { ALLOWED: '可骑乘', PREVENTED: '不可骑乘', UNKNOWN: '骑乘性未知' }))}</small>
        </span>
      </button>
    `;
  }).join('');
  const previousOffset = Math.max(0, page.offset - page.limit);
  return `
    <section class="panel harvest-creature-list-pane" aria-label="恐龙列表">
      <div class="panel-heading">
        <div><p class="eyebrow">CREATURES</p><h2>${escapeHtml(formatCount(page.total))} 个匹配物种</h2></div>
        <span class="soft-label">${escapeHtml(formatCount(page.offset + 1))}–${escapeHtml(formatCount(page.offset + page.items.length))}</span>
      </div>
      <div class="harvest-creature-list">${cards}</div>
      <div class="harvest-pagination">
        <button class="button ghost" type="button" data-creature-page="${previousOffset}" ${page.offset <= 0 ? 'disabled' : ''}>上一页</button>
        <button class="button ghost" type="button" data-creature-page="${page.nextOffset ?? 0}" ${page.nextOffset === null ? 'disabled' : ''}>下一页</button>
      </div>
    </section>
  `;
}


export function renderHarvestCreatureSpecialties(
  result: HarvestCreatureSpecialties,
): string {
  if (result.contractVersion === 'harvest-ranking-contract/v2') {
    return renderHarvestCreatureSpecialtiesV2(result);
  }
  const rows = result.items || [];
  const isEstimatedYield = hasEstimatedYieldMetric(result);
  const page = result.page || {
    offset: result.offset || 0,
    limit: result.limit || 24,
    total: result.total ?? rows.length,
    returned: rows.length,
    omitted: Math.max(0, (result.total ?? rows.length) - rows.length),
  };
  const warning = isEstimatedYield
    ? result.methodology.warning
      || '这是根据当前已恢复游戏数据估算的一整个完整节点产量，不是受控游戏实测值。'
    : `旧版响应：以下数值仅为旧版比较指数，不代表完整节点产量。${result.methodology.warning ? ` ${result.methodology.warning}` : ''}`;
  const blockers = Array.from(new Set([
    ...(result.claimBlockers || []),
    ...(result.evidence?.blockers || []),
  ].filter(Boolean)));
  const evidenceComplete = result.evidence?.status === 'COMPLETE'
    || result.claimsCompleteWithinScope === true;
  const previousOffset = Math.max(0, page.offset - page.limit);
  const nextOffset = page.offset + page.returned < page.total
    ? page.offset + page.returned
    : null;
  const rankingExplanation = isEstimatedYield
    ? '按该恐龙的每完整节点预计产量从高到低排列；相对百分比仅用于说明它与同一节点资源榜首的差距。'
    : '旧版响应按“该恐龙比较指数 ÷ 同一节点资源的旧版榜首指数”排序；该百分比不代表完整节点产量。';
  const selectedMetricLabel = isEstimatedYield ? '本龙预计产量' : '本龙旧版比较指数';
  const topMetricLabel = isEstimatedYield ? '节点最高预计产量' : '节点旧版榜首指数';
  const scopeExplanation = isEstimatedYield
    ? '绝对值表示同一公式下一整个完整节点的预计资源单位数；相对百分比以每个节点资源的当前最高预计产量为 100%。这仍是游戏数据估算，不是受控实测。'
    : '这是旧版比较指数响应；绝对值和相对百分比都不应解释为完整节点产量或游戏实测产量。';

  return `
    <section class="panel harvest-creature-detail-pane" aria-label="恐龙擅长资源排行">
      <div class="harvest-ranking-heading">
        <div>
          <p class="eyebrow">CREATURE SPECIALTIES</p>
          <h2>${escapeHtml(displayCreature(result.species))} 擅长什么</h2>
          <p>${escapeHtml(rankingExplanation)}</p>
        </div>
        <span class="status-pill ${evidenceComplete ? 'good' : 'warn'}">${evidenceComplete ? '范围证据完整' : '范围仍有缺口'}</span>
      </div>
      <p class="harvest-warning">${escapeHtml(warning)}</p>
      <div class="harvest-coverage-grid" aria-label="恐龙反向排行覆盖范围">
        <div class="harvest-coverage-metric"><span>节点资源</span><strong>${escapeHtml(formatCount(result.coverage.nodeResourcePairsDiscovered))}</strong></div>
        <div class="harvest-coverage-metric"><span>独立组合</span><strong>${escapeHtml(formatCount(result.coverage.uniqueEvaluationPairs))}</strong></div>
        <div class="harvest-coverage-metric"><span>可排名组合</span><strong>${escapeHtml(formatCount(result.coverage.uniqueEvaluationPairsRanked))}</strong></div>
        <div class="harvest-coverage-metric"><span>结果</span><strong>${escapeHtml(formatCount(page.total))}</strong></div>
      </div>
      ${rows.length ? `
        <div class="harvest-specialty-list" role="list">
          ${rows.map((row) => {
            const selectedMetric = isEstimatedYield
              ? row.estimatedYieldPerNode
              : row.engineComparisonIndex;
            const topMetric = isEstimatedYield
              ? row.nodeTopEstimatedYieldPerNode ?? row.nodeTop.estimatedYieldPerNode
              : row.nodeTopEngineComparisonIndex ?? row.nodeTop.engineComparisonIndex;
            return `
            <article class="harvest-specialty-row" role="listitem">
              <div class="harvest-specialty-rank"><span>${escapeHtml(row.rank)}</span><small>名</small></div>
              <div class="harvest-specialty-identity">
                <strong>${escapeHtml(displayResource(row.resource))}</strong>
                <span>${escapeHtml(row.node.name || row.node.id)}</span>
                <code>${escapeHtml(row.resource.resource)}</code>
              </div>
              <div class="harvest-specialty-score">
                <strong>${isEstimatedYield ? `${escapeHtml(selectedMetricLabel)} ${escapeHtml(formatScore(selectedMetric))}` : `${escapeHtml(formatScore(row.relativeToNodeTopPercent))}%`}</strong>
                <small>${isEstimatedYield ? `相对节点榜首 ${escapeHtml(formatScore(row.relativeToNodeTopPercent))}%` : `${escapeHtml(selectedMetricLabel)} ${escapeHtml(formatScore(selectedMetric))}`}</small>
                <small>${escapeHtml(topMetricLabel)} ${escapeHtml(formatScore(topMetric))}</small>
              </div>
              <div class="harvest-specialty-attack">
                <strong>${escapeHtml(row.attackName || '攻击名称未恢复')}</strong>
                <small>榜首：${escapeHtml(row.nodeTop.creature || row.nodeTop.speciesKey || '未知')} · ${escapeHtml(row.nodeTop.attackName || '攻击未知')}</small>
                <span class="status-pill ${row.rankingTier === 'CONFIRMED' ? 'good' : 'warn'}">${escapeHtml(row.rankingTier || row.evidence?.status || '条件证据')}</span>
              </div>
            </article>
          `;
          }).join('')}
        </div>
      ` : '<div class="empty-state">当前证据范围内，这只恐龙没有可数值排名的节点资源组合；未知值没有按 0 处理。</div>'}
      <div class="harvest-pagination">
        <button class="button ghost" type="button" data-specialty-page="${previousOffset}" ${page.offset <= 0 ? 'disabled' : ''}>上一页</button>
        <span class="soft-label">${escapeHtml(formatCount(page.offset + 1))}–${escapeHtml(formatCount(page.offset + rows.length))} / ${escapeHtml(formatCount(page.total))}</span>
        <button class="button ghost" type="button" data-specialty-page="${nextOffset ?? 0}" ${nextOffset === null ? 'disabled' : ''}>下一页</button>
      </div>
      <details class="harvest-scope-details">
        <summary>证据范围与限制</summary>
        <p>${escapeHtml(scopeExplanation)}</p>
        ${blockers.length ? `<ul class="harvest-blocker-list">${blockers.map((value) => `<li><code>${escapeHtml(value)}</code></li>`).join('')}</ul>` : '<p class="hint">当前响应没有额外范围阻断项。</p>'}
      </details>
    </section>
  `;
}


export class HarvestCreatureExplorer {
  private page: HarvestCreaturePage | null = null;
  private specialties: HarvestCreatureSpecialties | null = null;
  private selectedSpeciesKey = '';
  private query = '';
  private offset = 0;
  private specialtyOffset = 0;
  private specialtyMetric: HarvestRankingMetric = 'staticCompleteNodeTargetYield';
  private specialtyVariantPolicy = 'CANONICAL_VARIANT';
  private specialtyRuntimeProfileId = '';
  private specialtyIncludePreliminary = false;
  private loadingPage = false;
  private loadingSpecialties = false;
  private initialized = false;
  private error = '';
  private pageController: AbortController | null = null;
  private specialtyController: AbortController | null = null;
  private pageSequence = 0;
  private specialtySequence = 0;
  private searchTimer = 0;

  constructor(private readonly requestRender: () => void) {
    const params = new URLSearchParams(window.location.search);
    this.query = params.get('creatureQ') || '';
    this.selectedSpeciesKey = params.get('species') || '';
    this.offset = Math.max(0, Number(params.get('creatureOffset') || 0));
    this.specialtyOffset = Math.max(0, Number(params.get('specialtyOffset') || 0));
    const requestedMetric = params.get('specialtyMetric');
    if (
      requestedMetric === 'staticYieldPerAttackCycleSecond'
      || requestedMetric === 'observedYieldPerNode'
      || requestedMetric === 'observedYieldPerSecond'
    ) {
      this.specialtyMetric = requestedMetric;
    }
    const requestedVariant = params.get('specialtyVariant');
    if (
      requestedVariant === 'ALL_VARIANTS'
      || requestedVariant === 'BEST_DISCOVERED_VARIANT_EXPLORATORY'
    ) {
      this.specialtyVariantPolicy = requestedVariant;
    }
    this.specialtyRuntimeProfileId = params.get('specialtyRuntimeProfile') || '';
    this.specialtyIncludePreliminary = params.get('specialtyIncludePreliminary') === 'true';
  }

  ensureLoaded(force = false): void {
    if (force) {
      this.initialized = false;
    }
    if (this.initialized || this.loadingPage) {
      return;
    }
    this.initialized = true;
    void this.loadCreatures();
  }

  render(): string {
    return `
      <section class="harvest-subhero" aria-labelledby="creature-view-title">
        <div>
          <p class="eyebrow">REVERSE QUERY</p>
          <h2 id="creature-view-title">按恐龙查看擅长资源</h2>
          <p>先选恐龙，再比较它在每个精确资源点与资源条目中的相对强项。</p>
        </div>
        <button class="button ghost" type="button" data-creature-action="refresh">重新读取</button>
      </section>
      <form class="harvest-search panel" data-creature-search>
        <label for="harvest-creature-query">搜索恐龙</label>
        <div class="harvest-search-row">
          <input id="harvest-creature-query" name="creatureQ" value="${escapeHtml(this.query)}" placeholder="例如 Anky、Doedicurus" autocomplete="off">
          <button class="button primary" type="submit">搜索</button>
        </div>
      </form>
      <div class="harvest-live" aria-live="polite">
        ${this.loadingPage ? '正在读取恐龙目录…' : ''}
        ${this.loadingSpecialties ? '正在按节点资源复算该恐龙的强项…' : ''}
      </div>
      ${this.error ? `<section class="empty-state harvest-error"><strong>恐龙反向查询暂时不可用</strong><p>${escapeHtml(this.error)}</p><button class="button secondary" type="button" data-creature-action="retry">重试</button></section>` : ''}
      <div class="harvest-creature-grid">
        ${this.page
          ? renderHarvestCreaturePage(this.page, this.selectedSpeciesKey)
          : '<section class="panel harvest-creature-list-pane"><div class="empty-state">正在加载恐龙目录…</div></section>'}
        ${this.renderSpecialties()}
      </div>
    `;
  }

  bind(): void {
    document.querySelector<HTMLFormElement>('[data-creature-search]')?.addEventListener('submit', (event) => {
      event.preventDefault();
      this.query = document.querySelector<HTMLInputElement>('#harvest-creature-query')?.value.trim() || '';
      this.offset = 0;
      this.specialtyOffset = 0;
      this.selectedSpeciesKey = '';
      this.specialties = null;
      this.updateUrl();
      void this.loadCreatures();
    });
    document.querySelector<HTMLInputElement>('#harvest-creature-query')?.addEventListener('input', (event) => {
      const value = (event.currentTarget as HTMLInputElement).value;
      window.clearTimeout(this.searchTimer);
      this.searchTimer = window.setTimeout(() => {
        this.query = value.trim();
        this.offset = 0;
        this.specialtyOffset = 0;
        this.selectedSpeciesKey = '';
        this.specialties = null;
        this.updateUrl();
        void this.loadCreatures();
      }, 300);
    });
    document.querySelectorAll<HTMLButtonElement>('[data-harvest-species]').forEach((button) => {
      button.addEventListener('click', () => void this.selectSpecies(button.dataset.harvestSpecies || ''));
    });
    document.querySelectorAll<HTMLButtonElement>('[data-creature-page]').forEach((button) => {
      button.addEventListener('click', () => {
        this.offset = Math.max(0, Number(button.dataset.creaturePage || 0));
        this.selectedSpeciesKey = '';
        this.specialties = null;
        this.updateUrl();
        void this.loadCreatures();
      });
    });
    document.querySelectorAll<HTMLButtonElement>('[data-specialty-page]').forEach((button) => {
      button.addEventListener('click', () => {
        this.specialtyOffset = Math.max(0, Number(button.dataset.specialtyPage || 0));
        this.updateUrl();
        void this.selectSpecies(this.selectedSpeciesKey, this.specialtyOffset);
      });
    });
    document.querySelector<HTMLSelectElement>('#harvest-specialty-metric')?.addEventListener('change', (event) => {
      const requested = (event.currentTarget as HTMLSelectElement).value;
      if (
        requested === 'staticCompleteNodeTargetYield'
        || requested === 'staticYieldPerAttackCycleSecond'
        || requested === 'observedYieldPerNode'
        || requested === 'observedYieldPerSecond'
      ) {
        this.specialtyMetric = requested;
        this.specialtyOffset = 0;
        this.updateUrl();
        void this.selectSpecies(this.selectedSpeciesKey, 0);
      }
    });
    document.querySelector<HTMLSelectElement>('#harvest-specialty-variant')?.addEventListener('change', (event) => {
      const requested = (event.currentTarget as HTMLSelectElement).value;
      if (
        requested === 'CANONICAL_VARIANT'
        || requested === 'ALL_VARIANTS'
        || requested === 'BEST_DISCOVERED_VARIANT_EXPLORATORY'
      ) {
        this.specialtyVariantPolicy = requested;
        this.specialtyOffset = 0;
        this.updateUrl();
        void this.selectSpecies(this.selectedSpeciesKey, 0);
      }
    });
    document.querySelector<HTMLSelectElement>('#harvest-specialty-runtime-profile')?.addEventListener('change', (event) => {
      this.specialtyRuntimeProfileId = (event.currentTarget as HTMLSelectElement).value.trim();
      this.specialtyOffset = 0;
      this.updateUrl();
      void this.selectSpecies(this.selectedSpeciesKey, 0);
    });
    document.querySelector<HTMLInputElement>('#harvest-specialty-include-preliminary')?.addEventListener('change', (event) => {
      this.specialtyIncludePreliminary = (event.currentTarget as HTMLInputElement).checked;
      this.specialtyOffset = 0;
      this.updateUrl();
      void this.selectSpecies(this.selectedSpeciesKey, 0);
    });
    document.querySelectorAll<HTMLButtonElement>('[data-creature-action]').forEach((button) => {
      button.addEventListener('click', () => {
        if (button.dataset.creatureAction === 'refresh' || button.dataset.creatureAction === 'retry') {
          this.error = '';
          this.ensureLoaded(true);
        }
      });
    });
  }

  private renderSpecialties(): string {
    if (this.loadingSpecialties && !this.specialties) {
      return '<section class="panel harvest-creature-detail-pane"><div class="empty-state">正在复算节点资源强项…</div></section>';
    }
    if (!this.specialties) {
      return '<section class="panel harvest-creature-detail-pane"><div class="empty-state">选择左侧恐龙，查看它擅长的节点与资源。</div></section>';
    }
    return renderHarvestCreatureSpecialties(this.specialties);
  }

  private async loadCreatures(): Promise<void> {
    this.pageController?.abort();
    this.specialtyController?.abort();
    this.pageController = new AbortController();
    const sequence = ++this.pageSequence;
    this.specialtySequence += 1;
    this.loadingPage = true;
    this.loadingSpecialties = false;
    this.error = '';
    this.requestRender();
    try {
      const params = new URLSearchParams({
        q: this.query,
        offset: String(this.offset),
        limit: '20',
      });
      const page = await fetchHarvestJson<HarvestCreaturePage>(
        `/api/harvest/creatures?${params.toString()}`,
        this.pageController.signal,
      );
      if (sequence !== this.pageSequence) {
        return;
      }
      this.page = page;
      const requested = this.selectedSpeciesKey;
      const initial = page.items.find((item) => item.speciesKey === requested) || page.items[0];
      if (initial) {
        void this.selectSpecies(initial.speciesKey, this.specialtyOffset);
      } else {
        this.selectedSpeciesKey = '';
        this.specialties = null;
        this.updateUrl();
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      this.error = error instanceof Error ? error.message : String(error);
    } finally {
      if (sequence === this.pageSequence) {
        this.loadingPage = false;
        this.requestRender();
      }
    }
  }

  private async selectSpecies(speciesKey: string, offset = 0): Promise<void> {
    if (!speciesKey) {
      return;
    }
    this.specialtyController?.abort();
    this.specialtyController = new AbortController();
    const sequence = ++this.specialtySequence;
    this.selectedSpeciesKey = speciesKey;
    this.specialtyOffset = Math.max(0, offset);
    this.loadingSpecialties = true;
    this.specialties = null;
    this.error = '';
    this.updateUrl();
    this.requestRender();
    try {
      const params = new URLSearchParams({
        offset: String(this.specialtyOffset),
        limit: '24',
        policy: 'includeConditional',
        metric: this.specialtyMetric,
        variantPolicy: this.specialtyVariantPolicy,
        availabilityPolicy: 'GLOBAL_TRANSFER_ALLOWED',
      });
      if (this.specialtyRuntimeProfileId) {
        params.set('runtimeProfileId', this.specialtyRuntimeProfileId);
      }
      if (this.specialtyIncludePreliminary) {
        params.set('includePreliminary', 'true');
      }
      const result = await fetchHarvestJson<HarvestCreatureSpecialties>(
        `/api/harvest/creatures/${encodeURIComponent(speciesKey)}/specialties?${params.toString()}`,
        this.specialtyController.signal,
      );
      if (sequence !== this.specialtySequence || this.selectedSpeciesKey !== speciesKey) {
        return;
      }
      this.specialties = result;
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      if (sequence === this.specialtySequence) {
        this.error = error instanceof Error ? error.message : String(error);
      }
    } finally {
      if (sequence === this.specialtySequence) {
        this.loadingSpecialties = false;
        this.requestRender();
      }
    }
  }

  private updateUrl(): void {
    const url = new URL(window.location.href);
    url.searchParams.set('view', 'harvest');
    url.searchParams.set('harvestMode', 'creatures');
    const values: Record<string, string> = {
      creatureQ: this.query,
      creatureOffset: this.offset ? String(this.offset) : '',
      species: this.selectedSpeciesKey,
      specialtyOffset: this.specialtyOffset ? String(this.specialtyOffset) : '',
      specialtyMetric: this.specialtyMetric,
      specialtyVariant: this.specialtyVariantPolicy,
      specialtyRuntimeProfile: this.specialtyRuntimeProfileId,
      specialtyIncludePreliminary: this.specialtyIncludePreliminary ? 'true' : '',
    };
    Object.entries(values).forEach(([key, value]) => {
      if (value) {
        url.searchParams.set(key, value);
      } else {
        url.searchParams.delete(key);
      }
    });
    window.history.replaceState({}, '', url);
  }
}
