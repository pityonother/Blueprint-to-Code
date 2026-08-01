import { escapeHtml } from '../../shared/html';
import { fetchHarvestJson, HarvestApiError } from '../api';
import { HarvestBuildControl } from '../build-control';
import { HarvestCreatureExplorer } from '../creatures';
import {
  buildHarvestNodeSearchParams,
  renderHarvestNodeFilterForm,
} from '../filters';
import {
} from '../format';
import type {
  HarvestMapFilterMode,
  HarvestNode,
  HarvestNodeDetail,
  HarvestNodePage,
  HarvestRankingMetric,
  HarvestRankingResult,
} from '../types';
import { renderHarvestDatasetBar } from '../views/dataset-status';
import { renderHarvestNodeDetail } from '../views/node-detail';
import { renderHarvestNodeList } from '../views/node-list';

export class HarvestExplorer {
  private mode: 'nodes' | 'creatures' | 'build' = 'nodes';
  private readonly creatureExplorer: HarvestCreatureExplorer;
  private readonly buildControl: HarvestBuildControl;
  private page: HarvestNodePage | null = null;
  private selectedNode: HarvestNode | null = null;
  private ranking: HarvestRankingResult | null = null;
  private rankingMetric: HarvestRankingMetric = 'staticCompleteNodeTargetYield';
  private rankingVariantPolicy = 'CANONICAL_VARIANT';
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
    return renderHarvestNodeList({
      filters: {
        query: this.query,
        mapFamily: this.mapFilter,
        mapMode: this.mapMode,
        resource: this.resourceFilter,
      },
      loadingPage: this.loadingPage,
      page: this.page,
      selectedNodeId: this.selectedNode?.id || null,
    });
  }

  private renderDetail(): string {
    return renderHarvestNodeDetail(
      this.selectedNode,
      this.ranking,
      this.loadingRanking,
    );
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
