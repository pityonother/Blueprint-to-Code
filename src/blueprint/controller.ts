import { escapeHtml } from '../shared/html';
import {
  BlueprintApiError,
  fetchBlueprintAssets,
  fetchBlueprintGaps,
  fetchBlueprintHealth,
  fetchBlueprintInterpretation,
  fetchBlueprintStatement,
  fetchBlueprintTrace,
  queryBlueprintEvidence,
} from './api';
import {
  BLUEPRINT_TABS,
  blueprintPanelId,
  blueprintTabId,
  isBlueprintPrimaryTab,
} from './routing';
import { createBlueprintWorkspaceState, type BlueprintWorkspaceState } from './state';
import type {
  BlueprintEvidenceHealthResponse,
  BlueprintEvidenceOperation,
  BlueprintEvidenceQueryResponse,
  BlueprintIdentity,
  BlueprintPage,
  BlueprintPrimaryTab,
} from './types';
import { renderBlueprintAssetHealth } from './views/asset-health';
import { renderBlueprintAssetList } from './views/asset-list';
import { renderBlueprintEvidenceTrace } from './views/evidence-trace';
import { renderBlueprintGaps } from './views/gaps';
import { renderBlueprintInterpretation } from './views/interpretation';
import {
  renderBlueprintExperimentalTools,
  renderBlueprintLegacyReports,
} from './views/legacy-reports';
import { renderBlueprintStatementDetail } from './views/statement-detail';


export interface BlueprintControllerContent {
  legacy: string;
  experimental: string;
}

export const BLUEPRINT_CLIENT_STALE_CODE = 'BLUEPRINT_INTERPRETATION_STALE';

export interface BlueprintApiClient {
  fetchAssets: typeof fetchBlueprintAssets;
  fetchHealth: typeof fetchBlueprintHealth;
  fetchInterpretation: typeof fetchBlueprintInterpretation;
  fetchStatement: typeof fetchBlueprintStatement;
  fetchTrace: typeof fetchBlueprintTrace;
  fetchGaps: typeof fetchBlueprintGaps;
  queryEvidence: typeof queryBlueprintEvidence;
}

const DEFAULT_API_CLIENT: BlueprintApiClient = {
  fetchAssets: fetchBlueprintAssets,
  fetchHealth: fetchBlueprintHealth,
  fetchInterpretation: fetchBlueprintInterpretation,
  fetchStatement: fetchBlueprintStatement,
  fetchTrace: fetchBlueprintTrace,
  fetchGaps: fetchBlueprintGaps,
  queryEvidence: queryBlueprintEvidence,
};

type FocusTarget =
  | { kind: 'id' | 'statement' | 'asset' | 'action' | 'tab'; value: string }
  | { kind: 'detail-heading' | 'evidence-heading'; value: '' };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isStaleError(error: unknown): error is BlueprintApiError {
  return error instanceof BlueprintApiError
    && (
      error.status === 409
      || error.code === BLUEPRINT_CLIENT_STALE_CODE
      || error.code === 'BLUEPRINT_CURSOR_STALE'
    );
}

function complete(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

export function blueprintIdentityMatchesHealth(
  healthResponse: BlueprintEvidenceHealthResponse | null,
  identity: BlueprintIdentity,
  selectedAsset: string,
): boolean {
  const health = healthResponse?.health;
  const expectedEvidence = health?.evidence;
  const expectedInterpretation = health?.interpretation;
  const expectedAsset = health?.asset;
  if (
    !healthResponse
    || healthResponse.asset !== selectedAsset
    || health?.status !== 'READY'
    || expectedAsset?.name !== selectedAsset
    || identity.asset.name !== selectedAsset
    || !complete(expectedAsset.assetId)
    || !complete(expectedAsset.objectPath)
    || identity.asset.assetId !== expectedAsset.assetId
    || identity.asset.objectPath !== expectedAsset.objectPath
    || !complete(expectedEvidence?.revisionId)
    || !complete(expectedEvidence.manifestSha256)
    || identity.evidence.revisionId !== expectedEvidence.revisionId
    || identity.evidence.manifestSha256 !== expectedEvidence.manifestSha256
    || !complete(expectedInterpretation?.revisionId)
    || !complete(expectedInterpretation.manifestSha256)
    || !complete(expectedInterpretation.pointerSha256)
    || !complete(expectedInterpretation.semanticDigest)
    || !complete(expectedInterpretation.interpreterVersion)
    || !complete(expectedInterpretation.schemaVersion)
    || !complete(expectedInterpretation.generatedAt)
  ) return false;
  return identity.interpretation.revisionId === expectedInterpretation.revisionId
    && identity.interpretation.manifestSha256 === expectedInterpretation.manifestSha256
    && identity.interpretation.pointerSha256 === expectedInterpretation.pointerSha256
    && identity.interpretation.semanticDigest === expectedInterpretation.semanticDigest
    && identity.interpretation.interpreterVersion === expectedInterpretation.interpreterVersion
    && identity.interpretation.schemaVersion === expectedInterpretation.schemaVersion
    && identity.interpretation.generatedAt === expectedInterpretation.generatedAt;
}

export function blueprintIdentitiesMatch(left: BlueprintIdentity, right: BlueprintIdentity): boolean {
  return left.asset.name === right.asset.name
    && left.asset.assetId === right.asset.assetId
    && left.asset.objectPath === right.asset.objectPath
    && left.evidence.revisionId === right.evidence.revisionId
    && left.evidence.manifestSha256 === right.evidence.manifestSha256
    && left.interpretation.revisionId === right.interpretation.revisionId
    && left.interpretation.manifestSha256 === right.interpretation.manifestSha256
    && left.interpretation.pointerSha256 === right.interpretation.pointerSha256
    && left.interpretation.semanticDigest === right.interpretation.semanticDigest
    && left.interpretation.interpreterVersion === right.interpretation.interpreterVersion
    && left.interpretation.schemaVersion === right.interpretation.schemaVersion
    && left.interpretation.generatedAt === right.interpretation.generatedAt;
}

export function blueprintEvidenceQueryMatchesHealth(
  healthResponse: BlueprintEvidenceHealthResponse | null,
  response: BlueprintEvidenceQueryResponse,
): boolean {
  const evidence = healthResponse?.health.evidence;
  return healthResponse?.health.status === 'READY'
    && complete(evidence?.manifestSha256)
    && complete(evidence.pointerSha256)
    && response.manifestSha256 === evidence.manifestSha256
    && response.pointerSha256 === evidence.pointerSha256;
}

function mergedPage(page: BlueprintPage, loaded: number): BlueprintPage {
  return { ...page, returned: loaded };
}

function sameCursor(expected: string, page: BlueprintPage | null | undefined): boolean {
  return Boolean(expected) && page?.nextCursor === expected;
}

function cursorAdvanced(requestCursor: string, responsePage: BlueprintPage): boolean {
  return !responsePage.nextCursor || responsePage.nextCursor !== requestCursor;
}

export class BlueprintController {
  private readonly state: BlueprintWorkspaceState = createBlueprintWorkspaceState();
  private initialized = false;
  private generation = 0;
  private statementEpoch = 0;
  private evidenceEpoch = 0;
  private traceEpoch = 0;
  private assetPageEpoch = 0;
  private interpretationPageEpoch = 0;
  private gapPageEpoch = 0;
  private pendingFocus: FocusTarget | null = null;
  private statementBusy = false;
  private evidenceBusy = false;
  private traceBusy = false;

  constructor(
    private readonly notify: () => void,
    private readonly onAssetSelected: (asset: string) => void = () => {},
    private readonly client: BlueprintApiClient = DEFAULT_API_CLIENT,
  ) {}

  /** Read-only snapshot used by contract tests and embedding shells. */
  snapshot(): Readonly<BlueprintWorkspaceState> {
    return this.state;
  }

  ensureLoaded(preferredAsset = ''): void {
    if (!this.initialized) {
      this.initialized = true;
      void this.refreshAssets(preferredAsset);
      return;
    }
    if (
      preferredAsset
      && preferredAsset !== this.state.selectedAsset
      && this.state.assets.some((item) => item.asset === preferredAsset)
    ) {
      void this.selectAsset(preferredAsset, false);
    }
  }

  private activeFocus(): FocusTarget | null {
    if (typeof document === 'undefined') return null;
    const active = document.activeElement as HTMLElement | null;
    if (!active) return null;
    if (active.dataset.blueprintStatement) return { kind: 'statement', value: active.dataset.blueprintStatement };
    if (active.dataset.blueprintAsset) return { kind: 'asset', value: active.dataset.blueprintAsset };
    if (active.dataset.blueprintAction) return { kind: 'action', value: active.dataset.blueprintAction };
    if (active.dataset.blueprintTab) return { kind: 'tab', value: active.dataset.blueprintTab };
    return active.id ? { kind: 'id', value: active.id } : null;
  }

  private announce(focus: FocusTarget | null = this.activeFocus()): void {
    if (focus) this.pendingFocus = focus;
    this.notify();
  }

  private restoreFocus(): void {
    if (!this.pendingFocus || typeof document === 'undefined') return;
    const target = this.pendingFocus;
    this.pendingFocus = null;
    let element: HTMLElement | null = null;
    if (target.kind === 'detail-heading') {
      element = document.querySelector<HTMLElement>('[data-blueprint-statement-detail-heading]');
    } else if (target.kind === 'evidence-heading') {
      element = document.querySelector<HTMLElement>('[data-blueprint-evidence-query-heading]');
    } else if (target.kind === 'id') {
      element = document.getElementById(target.value);
    } else {
      const key = target.kind === 'statement'
        ? 'blueprintStatement'
        : target.kind === 'asset'
          ? 'blueprintAsset'
          : target.kind === 'action'
            ? 'blueprintAction'
            : 'blueprintTab';
      element = Array.from(document.querySelectorAll<HTMLElement>(`[data-${target.kind === 'statement' ? 'blueprint-statement' : target.kind === 'asset' ? 'blueprint-asset' : target.kind === 'action' ? 'blueprint-action' : 'blueprint-tab'}]`))
        .find((candidate) => candidate.dataset[key] === target.value) || null;
    }
    element?.focus();
  }

  private clearAssetPayload(keepHealth = false): void {
    if (!keepHealth) this.state.health = null;
    this.state.interpretation = null;
    this.state.trace = null;
    this.state.gaps = null;
    this.state.selectedStatement = null;
    this.state.selectedEvidenceRef = '';
    this.state.evidenceQuery = null;
    this.statementEpoch += 1;
    this.evidenceEpoch += 1;
    this.traceEpoch += 1;
    this.interpretationPageEpoch += 1;
    this.gapPageEpoch += 1;
    this.statementBusy = false;
    this.evidenceBusy = false;
    this.traceBusy = false;
    this.syncDetailLoading();
  }

  private failClosed(): void {
    this.clearAssetPayload(true);
    this.state.loading = false;
    this.state.staleCode = BLUEPRINT_CLIENT_STALE_CODE;
    this.state.error = '';
    this.announce();
  }

  private syncDetailLoading(): void {
    this.state.detailLoading = this.statementBusy || this.evidenceBusy || this.traceBusy;
  }

  private accepts(identity: BlueprintIdentity): boolean {
    return blueprintIdentityMatchesHealth(this.state.health, identity, this.state.selectedAsset);
  }

  private async refreshAssets(preferredAsset = this.state.selectedAsset): Promise<void> {
    const generation = ++this.generation;
    this.assetPageEpoch += 1;
    this.clearAssetPayload();
    this.state.assets = [];
    this.state.assetsPage = null;
    this.state.loading = true;
    this.state.error = '';
    this.state.staleCode = '';
    this.announce();
    try {
      const response = await this.client.fetchAssets(this.state.assetQuery);
      if (generation !== this.generation) return;
      this.state.assets = response.items;
      this.state.assetsPage = response.page;
      const selected = response.items.some((item) => item.asset === preferredAsset)
        ? preferredAsset
        : response.items[0]?.asset || '';
      this.state.selectedAsset = selected;
      if (selected) {
        this.onAssetSelected(selected);
        await this.loadSelectedAsset(generation);
      }
    } catch (error) {
      if (generation !== this.generation) return;
      this.state.error = errorMessage(error);
    } finally {
      if (generation === this.generation) {
        this.state.loading = false;
        this.announce();
      }
    }
  }

  private async loadMoreAssets(): Promise<void> {
    const cursor = this.state.assetsPage?.nextCursor || '';
    if (!cursor) return;
    const generation = this.generation;
    const query = this.state.assetQuery;
    const epoch = ++this.assetPageEpoch;
    this.state.loading = true;
    this.announce({ kind: 'action', value: 'load-more-assets' });
    try {
      const response = await this.client.fetchAssets(query, cursor);
      if (
        generation !== this.generation
        || epoch !== this.assetPageEpoch
        || query !== this.state.assetQuery
        || !sameCursor(cursor, this.state.assetsPage)
      ) return;
      if (!cursorAdvanced(cursor, response.page)) {
        this.recordLoadError(new BlueprintApiError('Blueprint asset cursor did not advance.', 409, 'BLUEPRINT_CURSOR_STALE'));
        return;
      }
      const known = new Set(this.state.assets.map((item) => item.asset));
      this.state.assets.push(...response.items.filter((item) => !known.has(item.asset)));
      this.state.assetsPage = mergedPage(response.page, this.state.assets.length);
    } catch (error) {
      if (generation === this.generation && epoch === this.assetPageEpoch) this.recordLoadError(error);
    } finally {
      if (generation === this.generation && epoch === this.assetPageEpoch) {
        this.state.loading = false;
        this.announce({ kind: 'action', value: 'load-more-assets' });
      }
    }
  }

  private async selectAsset(asset: string, syncLegacy: boolean): Promise<void> {
    const value = String(asset || '').trim();
    if (!value || value === this.state.selectedAsset) return;
    const generation = ++this.generation;
    this.state.selectedAsset = value;
    this.clearAssetPayload();
    this.state.error = '';
    this.state.staleCode = '';
    this.state.loading = true;
    if (syncLegacy) this.onAssetSelected(value);
    this.announce({ kind: 'asset', value });
    try {
      await this.loadSelectedAsset(generation);
    } finally {
      if (generation === this.generation) {
        this.state.loading = false;
        this.announce({ kind: 'asset', value });
      }
    }
  }

  private async loadSelectedAsset(generation: number): Promise<void> {
    const asset = this.state.selectedAsset;
    if (!asset) return;
    try {
      const health = await this.client.fetchHealth(asset);
      if (generation !== this.generation || asset !== this.state.selectedAsset) return;
      this.state.health = health;
      if (health.health.status !== 'READY') {
        this.clearAssetPayload(true);
        if (health.health.status === 'STALE') this.state.staleCode = BLUEPRINT_CLIENT_STALE_CODE;
        return;
      }
    } catch (error) {
      if (generation === this.generation) this.recordLoadError(error);
      return;
    }
    const [interpretation, gaps] = await Promise.allSettled([
      this.client.fetchInterpretation(asset),
      this.client.fetchGaps(asset),
    ]);
    if (generation !== this.generation || asset !== this.state.selectedAsset) return;
    if (
      interpretation.status === 'fulfilled'
      && gaps.status === 'fulfilled'
      && this.accepts(interpretation.value.identity)
      && this.accepts(gaps.value.identity)
      && blueprintIdentitiesMatch(interpretation.value.identity, gaps.value.identity)
    ) {
      this.state.interpretation = interpretation.value;
      this.state.gaps = gaps.value;
    } else if (
      (interpretation.status === 'fulfilled' && !this.accepts(interpretation.value.identity))
      || (gaps.status === 'fulfilled' && !this.accepts(gaps.value.identity))
      || (interpretation.status === 'fulfilled' && gaps.status === 'fulfilled'
        && !blueprintIdentitiesMatch(interpretation.value.identity, gaps.value.identity))
      || (interpretation.status === 'rejected' && isStaleError(interpretation.reason))
      || (gaps.status === 'rejected' && isStaleError(gaps.reason))
    ) {
      this.failClosed();
      return;
    } else {
      if (interpretation.status === 'rejected') this.recordLoadError(interpretation.reason);
      if (gaps.status === 'rejected') this.recordLoadError(gaps.reason);
    }
    if (this.state.activeTab === 'evidence' && this.state.interpretation) {
      await this.loadTrace(generation);
    }
  }

  private recordLoadError(error: unknown): void {
    if (isStaleError(error)) {
      this.failClosed();
      return;
    }
    if (error instanceof BlueprintApiError && error.status === 404) return;
    if (!this.state.error) this.state.error = errorMessage(error);
  }

  private async loadTrace(generation = this.generation, cursor = ''): Promise<void> {
    const asset = this.state.selectedAsset;
    if (!asset || (!cursor && this.state.trace)) return;
    if (cursor && !sameCursor(cursor, this.state.trace?.page)) return;
    const epoch = ++this.traceEpoch;
    this.traceBusy = true;
    this.syncDetailLoading();
    this.announce(cursor ? { kind: 'action', value: 'load-more-trace' } : null);
    try {
      const response = await this.client.fetchTrace(asset, cursor);
      if (
        generation !== this.generation
        || epoch !== this.traceEpoch
        || asset !== this.state.selectedAsset
        || (cursor && !sameCursor(cursor, this.state.trace?.page))
      ) return;
      if (!this.accepts(response.identity)
        || (this.state.interpretation && !blueprintIdentitiesMatch(response.identity, this.state.interpretation.identity))) {
        this.failClosed();
        return;
      }
      if (cursor && !cursorAdvanced(cursor, response.page)) {
        this.failClosed();
        return;
      }
      if (cursor && this.state.trace) {
        const items = [...this.state.trace.items, ...response.items];
        this.state.trace = { ...response, items, page: mergedPage(response.page, items.length) };
      } else {
        this.state.trace = response;
      }
    } catch (error) {
      if (generation === this.generation && epoch === this.traceEpoch) this.recordLoadError(error);
    } finally {
      if (generation === this.generation && epoch === this.traceEpoch) {
        this.traceBusy = false;
        this.syncDetailLoading();
        this.announce(cursor ? { kind: 'action', value: 'load-more-trace' } : null);
      }
    }
  }

  private async selectStatement(statementId: string, cursor = ''): Promise<void> {
    const asset = this.state.selectedAsset;
    if (!asset || !statementId) return;
    if (cursor && !sameCursor(cursor, this.state.selectedStatement?.page)) return;
    const generation = this.generation;
    const epoch = ++this.statementEpoch;
    if (!cursor) this.state.selectedStatement = null;
    this.statementBusy = true;
    this.syncDetailLoading();
    this.state.error = '';
    this.announce({ kind: 'statement', value: statementId });
    try {
      const response = await this.client.fetchStatement(asset, statementId, cursor);
      if (
        generation !== this.generation
        || epoch !== this.statementEpoch
        || asset !== this.state.selectedAsset
        || (cursor && !sameCursor(cursor, this.state.selectedStatement?.page))
      ) return;
      if (!this.accepts(response.identity)
        || (this.state.interpretation && !blueprintIdentitiesMatch(response.identity, this.state.interpretation.identity))) {
        this.failClosed();
        return;
      }
      if (cursor && !cursorAdvanced(cursor, response.page)) {
        this.failClosed();
        return;
      }
      if (cursor && this.state.selectedStatement) {
        const items = [...this.state.selectedStatement.items, ...response.items];
        this.state.selectedStatement = { ...response, items, page: mergedPage(response.page, items.length) };
      } else {
        this.state.selectedStatement = response;
      }
      this.pendingFocus = { kind: 'detail-heading', value: '' };
    } catch (error) {
      if (generation === this.generation && epoch === this.statementEpoch) this.recordLoadError(error);
    } finally {
      if (generation === this.generation && epoch === this.statementEpoch) {
        this.statementBusy = false;
        this.syncDetailLoading();
        this.announce(this.pendingFocus);
      }
    }
  }

  private async inspectEvidence(
    evidenceRef: string,
    operation: BlueprintEvidenceOperation,
  ): Promise<void> {
    const asset = this.state.selectedAsset;
    if (!asset || !evidenceRef.startsWith('bp://')
      || (!evidenceRef.includes('/n/') && !evidenceRef.includes('/p/'))) return;
    const generation = this.generation;
    const epoch = ++this.evidenceEpoch;
    this.traceEpoch += 1;
    this.traceBusy = false;
    this.state.activeTab = 'evidence';
    this.state.selectedEvidenceRef = evidenceRef;
    this.state.evidenceQuery = null;
    this.evidenceBusy = true;
    this.syncDetailLoading();
    this.state.error = '';
    this.announce({ kind: 'tab', value: 'evidence' });
    try {
      const [query, trace] = await Promise.all([
        this.client.queryEvidence(asset, evidenceRef, operation),
        this.state.trace ? Promise.resolve(this.state.trace) : this.client.fetchTrace(asset),
      ]);
      if (generation !== this.generation || epoch !== this.evidenceEpoch || asset !== this.state.selectedAsset) return;
      if (
        !blueprintEvidenceQueryMatchesHealth(this.state.health, query)
        || !this.accepts(trace.identity)
        || (this.state.interpretation && !blueprintIdentitiesMatch(trace.identity, this.state.interpretation.identity))
      ) {
        this.failClosed();
        return;
      }
      this.state.evidenceQuery = query;
      this.state.trace = trace;
      this.pendingFocus = { kind: 'evidence-heading', value: '' };
    } catch (error) {
      if (generation === this.generation && epoch === this.evidenceEpoch) this.recordLoadError(error);
    } finally {
      if (generation === this.generation && epoch === this.evidenceEpoch) {
        this.evidenceBusy = false;
        this.syncDetailLoading();
        this.announce(this.pendingFocus);
      }
    }
  }

  private async loadMoreInterpretation(): Promise<void> {
    const current = this.state.interpretation;
    const cursor = current?.page.nextCursor || '';
    const asset = this.state.selectedAsset;
    if (!current || !asset || !cursor) return;
    const generation = this.generation;
    const epoch = ++this.interpretationPageEpoch;
    this.state.loading = true;
    this.announce({ kind: 'action', value: 'load-more-statements' });
    try {
      const response = await this.client.fetchInterpretation(asset, cursor);
      if (generation !== this.generation || epoch !== this.interpretationPageEpoch
        || asset !== this.state.selectedAsset || !sameCursor(cursor, this.state.interpretation?.page)) return;
      if (!this.accepts(response.identity) || !blueprintIdentitiesMatch(response.identity, current.identity)) {
        this.failClosed();
        return;
      }
      if (!cursorAdvanced(cursor, response.page)) {
        this.failClosed();
        return;
      }
      const items = [...current.items, ...response.items];
      this.state.interpretation = { ...response, items, page: mergedPage(response.page, items.length) };
    } catch (error) {
      if (generation === this.generation && epoch === this.interpretationPageEpoch) this.recordLoadError(error);
    } finally {
      if (generation === this.generation && epoch === this.interpretationPageEpoch) {
        this.state.loading = false;
        this.announce({ kind: 'action', value: 'load-more-statements' });
      }
    }
  }

  private async loadMoreGaps(): Promise<void> {
    const current = this.state.gaps;
    const cursor = current?.page.nextCursor || '';
    const asset = this.state.selectedAsset;
    if (!current || !asset || !cursor) return;
    const generation = this.generation;
    const epoch = ++this.gapPageEpoch;
    this.state.loading = true;
    this.announce({ kind: 'action', value: 'load-more-gaps' });
    try {
      const response = await this.client.fetchGaps(asset, cursor);
      if (generation !== this.generation || epoch !== this.gapPageEpoch
        || asset !== this.state.selectedAsset || !sameCursor(cursor, this.state.gaps?.page)) return;
      if (!this.accepts(response.identity) || !blueprintIdentitiesMatch(response.identity, current.identity)) {
        this.failClosed();
        return;
      }
      if (!cursorAdvanced(cursor, response.page)) {
        this.failClosed();
        return;
      }
      const items = [...current.items, ...response.items];
      this.state.gaps = { ...response, items, page: mergedPage(response.page, items.length) };
    } catch (error) {
      if (generation === this.generation && epoch === this.gapPageEpoch) this.recordLoadError(error);
    } finally {
      if (generation === this.generation && epoch === this.gapPageEpoch) {
        this.state.loading = false;
        this.announce({ kind: 'action', value: 'load-more-gaps' });
      }
    }
  }

  private closeStatement(): void {
    const statementId = this.state.selectedStatement?.statement.id || '';
    this.statementEpoch += 1;
    this.state.selectedStatement = null;
    this.statementBusy = false;
    this.syncDetailLoading();
    this.announce(statementId ? { kind: 'statement', value: statementId } : null);
  }

  private setTab(tab: BlueprintPrimaryTab): void {
    if (tab === this.state.activeTab) return;
    this.state.activeTab = tab;
    this.announce({ kind: 'tab', value: tab });
    if (tab === 'evidence' && this.state.health?.health.status === 'READY' && this.state.interpretation) {
      void this.loadTrace();
    }
  }

  private renderTabs(): string {
    return BLUEPRINT_TABS.map((tab) => {
      const active = tab.id === this.state.activeTab;
      return `<button id="${blueprintTabId(tab.id)}" class="blueprint-primary-tab ${active ? 'active' : ''}"
        type="button" role="tab" aria-selected="${active}" aria-controls="${blueprintPanelId(tab.id)}"
        tabindex="${active ? '0' : '-1'}" data-blueprint-tab="${tab.id}">
        <strong>${tab.label}</strong><span>${tab.description}</span></button>`;
    }).join('');
  }

  private renderPanel(content: BlueprintControllerContent): string {
    const gaps = this.state.gaps?.items || [];
    if (this.state.activeTab === 'evidence') {
      return renderBlueprintEvidenceTrace(this.state.trace, this.state.evidenceQuery, this.state.selectedEvidenceRef, this.state.detailLoading);
    }
    if (this.state.activeTab === 'gaps') return renderBlueprintGaps(this.state.gaps, this.state.loading);
    if (this.state.activeTab === 'legacy') return renderBlueprintLegacyReports(content.legacy);
    if (this.state.activeTab === 'experimental') return renderBlueprintExperimentalTools(content.experimental);
    return `${renderBlueprintInterpretation(this.state.interpretation, gaps, this.state.loading)}
      ${renderBlueprintStatementDetail(this.state.selectedStatement, this.state.detailLoading)}`;
  }

  render(content: BlueprintControllerContent = { legacy: '', experimental: '' }): string {
    const active = this.state.activeTab;
    return `<section class="blueprint-primary-workspace" aria-label="Blueprint Interpretation workspace">
      <nav class="blueprint-primary-tabs" role="tablist" aria-label="Blueprint 数据视图">${this.renderTabs()}</nav>
      <div class="blueprint-primary-layout">
        ${renderBlueprintAssetList(this.state.assets, this.state.selectedAsset, this.state.assetQuery, this.state.loading, this.state.assetsPage)}
        <div class="blueprint-primary-content">
          ${this.state.error ? `<div class="action-notice danger" role="alert">${escapeHtml(this.state.error)}</div>` : ''}
          ${renderBlueprintAssetHealth(this.state)}
          <div id="${blueprintPanelId(active)}" role="tabpanel" aria-labelledby="${blueprintTabId(active)}" tabindex="0">
            ${this.renderPanel(content)}
          </div>
        </div>
      </div>
    </section>`;
  }

  bind(): void {
    document.querySelectorAll<HTMLButtonElement>('[data-blueprint-tab]').forEach((button) => {
      button.addEventListener('click', () => {
        const tab = button.dataset.blueprintTab;
        if (isBlueprintPrimaryTab(tab)) this.setTab(tab);
      });
      button.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const current = BLUEPRINT_TABS.findIndex((tab) => tab.id === this.state.activeTab);
        const index = event.key === 'Home' ? 0 : event.key === 'End' ? BLUEPRINT_TABS.length - 1
          : (current + (event.key === 'ArrowRight' ? 1 : -1) + BLUEPRINT_TABS.length) % BLUEPRINT_TABS.length;
        this.setTab(BLUEPRINT_TABS[index].id);
      });
    });
    document.querySelectorAll<HTMLButtonElement>('[data-blueprint-asset]').forEach((button) => {
      button.addEventListener('click', () => void this.selectAsset(button.dataset.blueprintAsset || '', true));
    });
    document.querySelector<HTMLFormElement>('[data-blueprint-form="asset-search"]')?.addEventListener('submit', (event) => {
      event.preventDefault();
      this.state.assetQuery = document.querySelector<HTMLInputElement>('#blueprint-asset-query')?.value.trim() || '';
      void this.refreshAssets();
    });
    document.querySelectorAll<HTMLButtonElement>('[data-blueprint-action]').forEach((button) => {
      button.addEventListener('click', () => {
        const action = button.dataset.blueprintAction;
        if (action === 'refresh-assets') void this.refreshAssets();
        if (action === 'load-more-assets') void this.loadMoreAssets();
        if (action === 'load-more-statements') void this.loadMoreInterpretation();
        if (action === 'load-more-gaps') void this.loadMoreGaps();
        if (action === 'load-more-trace') void this.loadTrace(this.generation, this.state.trace?.page.nextCursor || '');
        if (action === 'load-more-statement-trace') {
          const selected = this.state.selectedStatement;
          if (selected) void this.selectStatement(selected.statement.id, selected.page.nextCursor || '');
        }
        if (action === 'close-statement') this.closeStatement();
      });
    });
    document.querySelectorAll<HTMLButtonElement>('[data-blueprint-statement]').forEach((button) => {
      button.addEventListener('click', () => void this.selectStatement(button.dataset.blueprintStatement || ''));
    });
    document.querySelectorAll<HTMLButtonElement>('[data-blueprint-evidence-ref]').forEach((button) => {
      button.addEventListener('click', () => {
        const operation = button.dataset.blueprintEvidenceOperation === 'trace' ? 'trace' : 'neighborhood';
        void this.inspectEvidence(button.dataset.blueprintEvidenceRef || '', operation);
      });
    });
    this.restoreFocus();
  }
}
