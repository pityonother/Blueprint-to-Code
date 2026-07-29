import { readableError } from '../shared/errors';
import { escapeHtml } from '../shared/html';
import {
  compareKnowledge,
  fetchKnowledgeEntity,
  fetchKnowledgeEntityPage,
  fetchKnowledgeHealth,
  queryKnowledge,
  searchKnowledgeEntities,
} from './api';
import type {
  KnowledgeEntity,
  KnowledgeEntityDetail,
  KnowledgeHealth,
  KnowledgePage,
  KnowledgeQueryResult,
  KnowledgeShadowCompareResult,
} from './types';


type EntitySection = KnowledgePage<Record<string, unknown>>;
type QueryMode = 'legacy' | 'vnext' | 'compare';


function statusClass(status: unknown): string {
  const value = String(status || '').toUpperCase();
  if (['CONFIRMED', 'FRESH', 'READY', 'RESOLVED', 'DB_ONLY_COMPLETE'].includes(value)) return 'good';
  if (['UNKNOWN', 'NOT_RECOVERED', 'CANDIDATE', 'SHADOW', 'EVIDENCE_REQUIRED'].includes(value)) return 'warn';
  if (['STALE', 'INVALID', 'ERROR'].includes(value)) return 'danger';
  return 'idle';
}


function valueText(row: Record<string, unknown>): string {
  for (const key of ['valueText', 'valueNumber', 'valueInteger', 'valueJson']) {
    const value = row[key];
    if (value !== null && value !== undefined && value !== '') {
      return String(value);
    }
  }
  return '—';
}


function evidenceList(items: Array<Record<string, unknown>>): string {
  if (!items.length) return '<p class="kb-empty">当前结果没有 Evidence 指针。</p>';
  return `
    <ul class="kb-evidence-list">
      ${items.slice(0, 50).map((item) => `
        <li>
          <code>${escapeHtml(item.evidenceUri || item.sourceUri || 'UNKNOWN')}</code>
          <span>${escapeHtml(item.evidenceRole || item.role || item.freshness || '')}</span>
        </li>
      `).join('')}
    </ul>
  `;
}


export class KnowledgeWorkspace {
  private health: KnowledgeHealth | null = null;
  private searchText = '';
  private searchResults: KnowledgeEntity[] = [];
  private selected: KnowledgeEntityDetail | null = null;
  private sections: Record<string, EntitySection | null> = {
    facts: null,
    relationships: null,
    coverage: null,
    'effective-defaults': null,
  };
  private queryResult: KnowledgeQueryResult | null = null;
  private compareResult: KnowledgeShadowCompareResult | null = null;
  private queryMode: QueryMode = 'compare';
  private loading = false;
  private error = '';
  private initialized = false;

  constructor(private readonly notify: () => void) {}

  ensureLoaded(): void {
    if (this.initialized) return;
    this.initialized = true;
    void this.refreshHealth();
  }

  private async refreshHealth(): Promise<void> {
    this.loading = true;
    this.error = '';
    this.notify();
    try {
      this.health = await fetchKnowledgeHealth();
    } catch (error) {
      this.error = readableError(error);
    } finally {
      this.loading = false;
      this.notify();
    }
  }

  private renderHealth(): string {
    if (!this.health) {
      return `<section class="panel kb-health" aria-busy="${this.loading}">
        <p class="kb-empty">${this.loading ? '正在读取 vNext 快照状态…' : '尚未读取快照状态。'}</p>
      </section>`;
    }
    const cutover = this.health.cutover || { mode: 'shadow', defaultQuerySource: 'legacy' };
    return `
      <section class="panel kb-health">
        <div>
          <p class="eyebrow">并行快照</p>
          <h2>vNext ${escapeHtml(this.health.status)}</h2>
          <p class="hint">Build ${escapeHtml(this.health.buildId || '尚未构建')} · Schema ${escapeHtml(this.health.schemaVersion || '—')}</p>
        </div>
        <div class="kb-health-metrics">
          <span class="status-pill ${statusClass(this.health.status)}">${escapeHtml(this.health.status)}</span>
          <span class="status-pill ${statusClass(cutover.mode)}">模式：${escapeHtml(cutover.mode)}</span>
          <span class="status-pill idle">默认：${escapeHtml(cutover.defaultQuerySource)}</span>
        </div>
        ${this.health.gap?.length ? `<p class="kb-health-gap">${escapeHtml(this.health.gap[0].detail || this.health.gap[0].code)}</p>` : ''}
        <button class="button ghost" type="button" data-kb-action="refresh-health">刷新状态</button>
      </section>
    `;
  }

  private renderSearch(): string {
    const results = this.searchResults.length
      ? this.searchResults.map((entity) => `
          <button class="kb-entity-row ${this.selected?.entity.entityId === entity.entityId ? 'active' : ''}"
                  type="button" data-kb-entity="${entity.entityId}">
            <strong>${escapeHtml(entity.displayName || entity.internalName || `Entity ${entity.entityId}`)}</strong>
            <code>${escapeHtml(entity.canonicalUri)}</code>
            <span>${escapeHtml(entity.entityKind)} · ${escapeHtml(entity.status)}</span>
          </button>
        `).join('')
      : '<p class="kb-empty">输入资产名、内部名或 /Game/... Object Path 搜索。</p>';
    return `
      <section class="panel kb-search-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">实体搜索</p>
            <h2>定位 canonical entity</h2>
          </div>
          <span class="soft-label">最多返回 25 条</span>
        </div>
        <form id="kb-search-form" class="kb-search-form">
          <label for="kb-search-input">资产名或 Object Path</label>
          <div>
            <input id="kb-search-input" type="search" value="${escapeHtml(this.searchText)}"
                   placeholder="例如 PrimalItem 或 /Game/PrimalEarth/..." autocomplete="off" />
            <button class="button primary" type="submit" ${this.loading ? 'disabled' : ''}>搜索</button>
          </div>
        </form>
        <div class="kb-entity-list" aria-live="polite">${results}</div>
      </section>
    `;
  }

  private renderFacts(rows: Array<Record<string, unknown>>): string {
    if (!rows.length) return '<p class="kb-empty">当前实体没有可用事实。</p>';
    return `<div class="kb-table-wrap"><table class="kb-table">
      <thead><tr><th>事实</th><th>值</th><th>状态</th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td><strong>${escapeHtml(row.factName || '—')}</strong><small>${escapeHtml(row.factType || row.scopeKind || '')}</small></td>
        <td><code>${escapeHtml(valueText(row))}</code><small>${escapeHtml(row.valueKind || '')}</small></td>
        <td><span class="status-pill ${statusClass(row.status || row.resolutionStatus)}">${escapeHtml(row.resolutionStatus || row.status || 'UNKNOWN')}</span></td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  }

  private renderDetail(): string {
    if (!this.selected) {
      return `<section class="panel kb-detail-panel"><p class="kb-empty">从左侧选择一个实体后，这里会显示角色、领域、事实、关系、有效默认值和 Coverage。</p></section>`;
    }
    const detail = this.selected;
    const facts = this.sections.facts?.items || [];
    const effective = this.sections['effective-defaults']?.items || [];
    const relationships = this.sections.relationships?.items || [];
    const coverage = this.sections.coverage?.items || [];
    const allEvidence = [
      ...(this.sections.facts?.evidence || []),
      ...(this.sections['effective-defaults']?.evidence || []),
      ...(this.sections.relationships?.evidence || []),
      ...detail.evidence,
    ];
    return `
      <section class="panel kb-detail-panel">
        <div class="kb-entity-head">
          <div>
            <p class="eyebrow">${escapeHtml(detail.entity.entityKind)}</p>
            <h2>${escapeHtml(detail.entity.displayName || detail.entity.internalName)}</h2>
            <code>${escapeHtml(detail.entity.canonicalUri)}</code>
          </div>
          <span class="status-pill ${statusClass(detail.entity.status)}">${escapeHtml(detail.entity.status)}</span>
        </div>
        <div class="kb-tag-section">
          <h3>背景角色</h3>
          <div class="kb-tags">${detail.roles.length ? detail.roles.map((role) => `<span>${escapeHtml(role.role)} · ${escapeHtml(role.status)}</span>`).join('') : '<em>未分类</em>'}</div>
        </div>
        <div class="kb-tag-section">
          <h3>领域成员</h3>
          <div class="kb-tags">${detail.domains.length ? detail.domains.map((domain) => `<span>${escapeHtml(domain.domainId)} · ${escapeHtml(domain.membershipKind)}</span>`).join('') : '<em>未分类</em>'}</div>
        </div>
        <details open><summary>声明事实 (${facts.length})</summary>${this.renderFacts(facts)}</details>
        <details open><summary>有效默认值 (${effective.length})</summary>${this.renderFacts(effective)}</details>
        <details><summary>关系 (${relationships.length})</summary>
          ${relationships.length ? `<ul class="kb-relationship-list">${relationships.map((row) => `<li><strong>${escapeHtml(row.edgeType)}</strong><code>${escapeHtml(row.targetUri || row.sourceUri)}</code><span>${escapeHtml(row.status)}</span></li>`).join('')}</ul>` : '<p class="kb-empty">当前没有类型化关系。</p>'}
        </details>
        <details><summary>Coverage / Gaps (${coverage.length})</summary>
          ${coverage.length ? `<div class="kb-coverage-grid">${coverage.map((row) => `<div><strong>${escapeHtml(row.stage)}</strong><span class="status-pill ${statusClass(row.status)}">${escapeHtml(row.status)}</span><small>${escapeHtml(row.failureReason || '无缺口')}</small></div>`).join('')}</div>` : '<p class="kb-empty">没有 Coverage 记录。</p>'}
        </details>
        <details><summary>Evidence (${allEvidence.length})</summary>${evidenceList(allEvidence)}</details>
      </section>
    `;
  }

  private renderQuery(): string {
    const result = this.queryResult;
    const comparison = this.compareResult;
    const modeStatus = this.queryMode === 'vnext'
      ? result?.route
      : this.queryMode === 'legacy'
        ? comparison?.legacy.freshness
        : comparison?.consistent === true
          ? 'CONSISTENT'
          : comparison?.consistent === false
            ? 'DIFFERENT'
            : comparison
              ? 'NOT_COMPARABLE'
              : '';
    return `
      <section class="panel kb-query-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">数据库优先查询</p><h2>检查能否直接回答</h2></div>
          ${modeStatus ? `<span class="status-pill ${statusClass(modeStatus)}">${escapeHtml(modeStatus)}</span>` : ''}
        </div>
        <form id="kb-query-form" class="kb-query-form">
          <label>查询模式
            <select id="kb-query-mode">
              <option value="compare" ${this.queryMode === 'compare' ? 'selected' : ''}>compare：并排核对</option>
              <option value="vnext" ${this.queryMode === 'vnext' ? 'selected' : ''}>vNext：只读新 Core</option>
              <option value="legacy" ${this.queryMode === 'legacy' ? 'selected' : ''}>legacy：只读旧库</option>
            </select>
          </label>
          <label>目标实体<input id="kb-query-entity" required value="${escapeHtml(this.selected?.entity.canonicalUri || this.searchText)}" placeholder="/Game/... 或唯一资产名" /></label>
          <label>Fact types<input id="kb-query-facts" value="ITEM_PROPERTY" placeholder="ITEM_PROPERTY,EFFECTIVE_DEFAULT" /></label>
          <label>Fact names<input id="kb-query-names" placeholder="Weight,ItemRating（可留空）" /></label>
          <div class="kb-checks">
            <label><input id="kb-query-native" type="checkbox" /> 需要确认 Native 边</label>
            <label><input id="kb-query-runtime" type="checkbox" /> 需要运行时观察</label>
            <label><input id="kb-query-map" type="checkbox" /> 需要地图/PCG 使用证据</label>
          </div>
          <button class="button primary" type="submit" ${this.loading ? 'disabled' : ''}>执行只读查询</button>
        </form>
        ${this.renderComparison()}
        ${result && this.queryMode !== 'legacy' ? `
          <div class="kb-query-result" aria-live="polite">
            <div class="kb-query-metrics">
              <span>Freshness <strong>${escapeHtml(result.freshness)}</strong></span>
              <span>Context <strong>${result.contextPack.estimatedTokens}/${result.contextPack.budgetTokens} tokens</strong></span>
              <span>Evidence <strong>${result.evidence.length}</strong></span>
            </div>
            <div class="kb-query-columns">
              <div><h3>缺口</h3>${result.missingRequirements.length ? `<ul>${result.missingRequirements.map((gap) => `<li><strong>${escapeHtml(gap.code)}</strong><span>${escapeHtml(gap.requirement)}</span></li>`).join('')}</ul>` : '<p class="kb-empty">没有阻断缺口，可由 DB-only 回答。</p>'}</div>
              <div><h3>最小补证</h3>${result.recommendedProbes.length ? `<ul>${result.recommendedProbes.map((probe) => `<li><strong>${escapeHtml(probe.probeType)}</strong><span>${escapeHtml(probe.operation || probe.reason)}</span></li>`).join('')}</ul>` : '<p class="kb-empty">无需启动解析器。</p>'}</div>
            </div>
            <details><summary>查看有界 Context Pack</summary><pre>${escapeHtml(result.contextPack.content)}</pre></details>
          </div>
        ` : comparison ? '' : '<p class="kb-empty">查询只读 Core；证据不足时返回明确 gap 与定向 probe，不会自动全量解析。</p>'}
      </section>
    `;
  }

  private renderComparison(): string {
    const comparison = this.compareResult;
    if (!comparison) return '';
    const legacyRows = comparison.legacy.items;
    const comparisonLabel = comparison.consistent === true
      ? '语义一致'
      : comparison.consistent === false
        ? '存在差异'
        : '证据不可直接比较';
    const reasons = comparison.differenceReasons.length
      ? comparison.differenceReasons.map((reason) => `<li><code>${escapeHtml(reason)}</code></li>`).join('')
      : '<li>没有差异原因。</li>';
    return `
      <div class="kb-shadow-result" aria-live="polite">
        <div class="kb-query-metrics">
          <span>当前模式 <strong>${escapeHtml(this.queryMode)}</strong></span>
          ${this.queryMode === 'compare' ? `<span>对比结论 <strong>${escapeHtml(comparisonLabel)}</strong></span>` : ''}
          <span>Legacy <strong>${escapeHtml(comparison.legacy.freshness)} · ${legacyRows.length} rows</strong></span>
          ${this.queryMode === 'compare' ? `<span>vNext <strong>${escapeHtml(comparison.vnext.freshness)} · ${comparison.vnext.facts.length} facts</strong></span>` : ''}
          <span>优先来源 <strong>${escapeHtml(comparison.preferredSource)}</strong></span>
        </div>
        ${this.queryMode === 'compare' ? `
          <div class="kb-compare-grid">
            <div>
              <h3>差异与可比性</h3>
              <ul class="kb-compare-reasons">${reasons}</ul>
            </div>
            <div>
              <h3>Evidence 完整度</h3>
              <dl>
                <div><dt>legacy</dt><dd>${comparison.evidenceCompleteness.legacy}</dd></div>
                <div><dt>vNext</dt><dd>${comparison.evidenceCompleteness.vnext}</dd></div>
                <div><dt>vNext complete</dt><dd>${comparison.evidenceCompleteness.vnextComplete ? 'yes' : 'no'}</dd></div>
              </dl>
            </div>
          </div>
        ` : ''}
        <details ${this.queryMode === 'legacy' ? 'open' : ''}>
          <summary>Legacy 只读匹配 (${legacyRows.length})</summary>
          ${legacyRows.length ? `<div class="kb-table-wrap"><table class="kb-table">
            <thead><tr><th>来源表</th><th>事实</th><th>值</th><th>状态</th></tr></thead>
            <tbody>${legacyRows.map((row) => `<tr>
              <td><strong>${escapeHtml(row.database)}</strong><small>${escapeHtml(row.table)}</small></td>
              <td><strong>${escapeHtml(row.factName || '未命名')}</strong><small>${escapeHtml(row.factType || '')}</small></td>
              <td><code>${escapeHtml(row.value ?? '—')}</code></td>
              <td><span class="status-pill ${statusClass(row.status)}">${escapeHtml(row.status || 'UNKNOWN')}</span></td>
            </tr>`).join('')}</tbody>
          </table></div>` : '<p class="kb-empty">Legacy 稳定身份列没有匹配行。</p>'}
        </details>
      </div>
    `;
  }

  render(): string {
    return `
      <div class="kb-workspace">
        ${this.error ? `<div class="action-notice danger" role="alert">${escapeHtml(this.error)}</div>` : ''}
        ${this.renderHealth()}
        <div class="kb-explorer-grid">${this.renderSearch()}${this.renderDetail()}</div>
        ${this.renderQuery()}
      </div>
    `;
  }

  bind(): void {
    document.querySelector<HTMLButtonElement>('[data-kb-action="refresh-health"]')
      ?.addEventListener('click', () => void this.refreshHealth());
    const searchInput = document.querySelector<HTMLInputElement>('#kb-search-input');
    searchInput?.addEventListener('input', () => { this.searchText = searchInput.value; });
    document.querySelector<HTMLFormElement>('#kb-search-form')?.addEventListener('submit', (event) => {
      event.preventDefault();
      this.searchText = searchInput?.value.trim() || '';
      void this.search();
    });
    document.querySelectorAll<HTMLButtonElement>('[data-kb-entity]').forEach((button) => {
      button.addEventListener('click', () => void this.selectEntity(Number(button.dataset.kbEntity)));
    });
    document.querySelector<HTMLFormElement>('#kb-query-form')?.addEventListener('submit', (event) => {
      event.preventDefault();
      void this.runQuery();
    });
  }

  private async search(): Promise<void> {
    if (!this.searchText) return;
    this.loading = true;
    this.error = '';
    this.notify();
    try {
      const result = await searchKnowledgeEntities(this.searchText);
      this.searchResults = result.items;
    } catch (error) {
      this.error = readableError(error);
    } finally {
      this.loading = false;
      this.notify();
    }
  }

  private async selectEntity(entityId: number): Promise<void> {
    if (!Number.isInteger(entityId) || entityId <= 0) return;
    this.loading = true;
    this.error = '';
    this.notify();
    try {
      const [detail, facts, relationships, coverage, effective] = await Promise.all([
        fetchKnowledgeEntity(entityId),
        fetchKnowledgeEntityPage(entityId, 'facts'),
        fetchKnowledgeEntityPage(entityId, 'relationships'),
        fetchKnowledgeEntityPage(entityId, 'coverage'),
        fetchKnowledgeEntityPage(entityId, 'effective-defaults'),
      ]);
      this.selected = detail;
      this.sections = { facts, relationships, coverage, 'effective-defaults': effective };
    } catch (error) {
      this.error = readableError(error);
    } finally {
      this.loading = false;
      this.notify();
    }
  }

  private async runQuery(): Promise<void> {
    const entity = document.querySelector<HTMLInputElement>('#kb-query-entity')?.value.trim() || '';
    if (!entity) return;
    const split = (id: string) => (document.querySelector<HTMLInputElement>(id)?.value || '')
      .split(',').map((value) => value.trim()).filter(Boolean);
    const modeValue = document.querySelector<HTMLSelectElement>('#kb-query-mode')?.value;
    const mode: QueryMode = modeValue === 'legacy' || modeValue === 'vnext'
      ? modeValue
      : 'compare';
    const request = {
      entity,
      factTypes: split('#kb-query-facts'),
      factNames: split('#kb-query-names'),
      requiresNative: Boolean(document.querySelector<HTMLInputElement>('#kb-query-native')?.checked),
      requiresRuntime: Boolean(document.querySelector<HTMLInputElement>('#kb-query-runtime')?.checked),
      requiresMapEvidence: Boolean(document.querySelector<HTMLInputElement>('#kb-query-map')?.checked),
      evidenceLimit: 50,
      budgetTokens: 2000,
    };
    this.queryMode = mode;
    this.loading = true;
    this.error = '';
    this.notify();
    try {
      if (mode === 'vnext') {
        this.compareResult = null;
        this.queryResult = await queryKnowledge(request);
      } else {
        this.compareResult = await compareKnowledge(request);
        this.queryResult = mode === 'compare' ? this.compareResult.vnext : null;
      }
    } catch (error) {
      this.error = readableError(error);
    } finally {
      this.loading = false;
      this.notify();
    }
  }
}
