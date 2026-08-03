import type {
  BlueprintAssetListItem,
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

export function createBlueprintWorkspaceState(): BlueprintWorkspaceState {
  return {
    activeTab: 'interpretation',
    assetQuery: '',
    assets: [],
    assetsPage: null,
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
