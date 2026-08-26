import type {
  BlueprintAssetListItem,
  BlueprintAssetReadinessSummary,
  BlueprintHealth,
  BlueprintEvidenceHealthResponse,
  BlueprintEvidenceQueryResponse,
  BlueprintGap,
  BlueprintGapsResponse,
  BlueprintInterpretationResponse,
  BlueprintPage,
  BlueprintPrimaryTab,
  BlueprintStatementResponse,
  BlueprintTraceResponse,
} from './types';


export interface BlueprintWorkspaceState {
  activeTab: BlueprintPrimaryTab;
  assetQuery: string;
  assets: BlueprintAssetListItem[];
  assetsPage: BlueprintPage | null;
  assetsSummary: BlueprintAssetReadinessSummary | null;
  selectedAsset: string;
  health: BlueprintEvidenceHealthResponse | null;
  interpretation: BlueprintInterpretationResponse | null;
  trace: BlueprintTraceResponse | null;
  gaps: BlueprintGapsResponse | null;
  selectedStatement: BlueprintStatementResponse | null;
  selectedEvidenceRef: string;
  evidenceQuery: BlueprintEvidenceQueryResponse | null;
  loading: boolean;
  detailLoading: boolean;
  error: string;
  staleCode: string;
}

export interface BlueprintCoverage {
  confirmed: number;
  nonConfirmed: number;
  total: number;
  gaps: number;
  confirmedPercent: number;
}

function complete(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

export function isBlueprintReadyHealth(health: BlueprintHealth | null | undefined): boolean {
  const evidence = health?.evidence;
  const interpretation = health?.interpretation;
  return health?.status === 'READY'
    && evidence?.freshnessStatus === 'FRESH'
    && evidence.releaseAuthority === true
    && evidence.migrationRequired === false
    && complete(evidence.revisionId)
    && complete(evidence.manifestSha256)
    && complete(evidence.pointerSha256)
    && interpretation?.status === 'CURRENT'
    && complete(interpretation.revisionId)
    && complete(interpretation.manifestSha256)
    && complete(interpretation.pointerSha256)
    && complete(interpretation.semanticDigest);
}

export function createBlueprintWorkspaceState(): BlueprintWorkspaceState {
  return {
    activeTab: 'interpretation',
    assetQuery: '',
    assets: [],
    assetsPage: null,
    assetsSummary: null,
    selectedAsset: '',
    health: null,
    interpretation: null,
    trace: null,
    gaps: null,
    selectedStatement: null,
    selectedEvidenceRef: '',
    evidenceQuery: null,
    loading: false,
    detailLoading: false,
    error: '',
    staleCode: '',
  };
}

export function interpretationCoverage(
  interpretation: BlueprintInterpretationResponse | null,
  gapItems: BlueprintGap[] = [],
): BlueprintCoverage {
  const statements = interpretation?.items || [];
  const confirmed = statements.filter((statement) => statement.status === 'CONFIRMED').length;
  const total = interpretation?.page.total ?? statements.length;
  const nonConfirmed = Math.max(0, statements.length - confirmed);
  const summaryGapCount = interpretation?.summary.diagnosticGapCount;
  return {
    confirmed,
    nonConfirmed,
    total,
    gaps: typeof summaryGapCount === 'number' ? summaryGapCount : gapItems.length,
    confirmedPercent: statements.length
      ? Math.round((confirmed / statements.length) * 100)
      : 0,
  };
}

export function isBlueprintStale(state: BlueprintWorkspaceState): boolean {
  const health = state.health?.health;
  return Boolean(
    state.staleCode
    || health?.status === 'STALE'
    || health?.evidence?.freshnessStatus === 'STALE',
  );
}
