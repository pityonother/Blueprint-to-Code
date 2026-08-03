import type { ApiResult } from '../shared/api';


export type BlueprintPrimaryTab =
  | 'interpretation'
  | 'evidence'
  | 'gaps'
  | 'legacy'
  | 'experimental';

export interface BlueprintPage {
  limit: number;
  returned: number;
  total: number;
  nextCursor: string | null;
}

export interface BlueprintEvidenceIdentity {
  revisionId: string;
  manifestSha256: string;
  pointerSha256?: string;
  freshnessStatus?: string;
  releaseAuthority?: boolean;
  migrationRequired?: boolean;
}

export interface BlueprintInterpretationIdentity {
  revisionId: string;
  manifestSha256: string;
  pointerSha256: string;
  semanticDigest: string;
  interpreterVersion: string;
  schemaVersion: string;
  generatedAt: string;
}

export interface BlueprintIdentity {
  asset: {
    name: string;
    assetId: string;
    objectPath: string;
  };
  evidence: BlueprintEvidenceIdentity;
  interpretation: BlueprintInterpretationIdentity;
}

export interface BlueprintHealth {
  status?: string;
  reasonCode?: string;
  asset?: {
    name?: string;
    assetId?: string;
    objectPath?: string;
  };
  evidence?: BlueprintEvidenceIdentity;
  interpretation?: Partial<BlueprintInterpretationIdentity> & {
    status?: string;
  };
  [key: string]: unknown;
}

export interface BlueprintAssetListItem {
  asset: string;
  health: BlueprintHealth;
}

export interface BlueprintAssetListResponse extends ApiResult {
  schema: string;
  items: BlueprintAssetListItem[];
  page: BlueprintPage;
}

export interface BlueprintEvidenceHealthResponse extends ApiResult {
  schema: string;
  asset: string;
  health: BlueprintHealth;
}

export interface BlueprintStatement {
  id: string;
  kind: string;
  text: string;
  status: string;
  evidenceRefs: string[];
  gapRefs: string[];
  graphRef: string;
  nodeRef?: string;
  sourceOrder: number;
  [key: string]: unknown;
}

export interface BlueprintHeuristicHint {
  id?: string;
  topic?: string;
  text?: string;
  basis?: string;
  confidence?: string;
  notEvidence?: boolean;
  reviewRef?: string;
  [key: string]: unknown;
}

export interface BlueprintAssetSummary {
  assetName?: string;
  graphCount?: number;
  nodeCount?: number;
  pinCount?: number;
  edgeCount?: number;
  diagnosticGapCount?: number;
  confirmedStatementCount?: number;
  graphInventory?: Array<Record<string, unknown>>;
  graphStatusCounts?: Record<string, number>;
  [key: string]: unknown;
}

export interface BlueprintInterpretationResponse extends ApiResult {
  schema: string;
  identity: BlueprintIdentity;
  summary: BlueprintAssetSummary;
  heuristicReviewHints: BlueprintHeuristicHint[];
  filters: {
    statuses?: string[];
    kinds?: string[];
    graphRef?: string;
  };
  items: BlueprintStatement[];
  page: BlueprintPage;
}

export interface BlueprintTraceItem {
  traceKind?: string;
  statementId?: string;
  graphRef?: string;
  nodeRef?: string;
  evidenceRefs?: string[];
  gapRefs?: string[];
  pseudocodeLine?: number;
  line?: number;
  startByte?: number;
  endByte?: number;
  executable?: boolean;
  [key: string]: unknown;
}

export interface BlueprintStatementResponse extends ApiResult {
  schema: string;
  identity: BlueprintIdentity;
  statement: BlueprintStatement;
  items: BlueprintTraceItem[];
  page: BlueprintPage;
}

export interface BlueprintTraceResponse extends ApiResult {
  schema: string;
  identity: BlueprintIdentity;
  filters: Record<string, unknown>;
  items: BlueprintTraceItem[];
  page: BlueprintPage;
}

export interface BlueprintGap {
  id: string;
  code: string;
  status: string;
  graphRef?: string;
  nodeRef?: string;
  pinRef?: string;
  detail?: string;
  evidenceRefs?: string[];
  source?: string;
  [key: string]: unknown;
}

export interface BlueprintGapsResponse extends ApiResult {
  schema: string;
  identity: BlueprintIdentity;
  filters: Record<string, unknown>;
  items: BlueprintGap[];
  page: BlueprintPage;
}

export interface BlueprintEvidenceQueryResponse extends ApiResult {
  operation: string;
  asset?: Record<string, unknown>;
  items: Array<Record<string, unknown>>;
  coverage?: Record<string, unknown>;
  omissions?: Array<Record<string, unknown>>;
  nextQueries?: Array<Record<string, unknown>>;
  page?: { nextCursor?: string | null };
  budget?: Record<string, unknown>;
  freshnessStatus?: string;
  releaseAuthority?: boolean;
  migrationRequired?: boolean;
  manifestSha256?: string | null;
  pointerSha256?: string | null;
  [key: string]: unknown;
}

export type BlueprintEvidenceOperation = 'neighborhood' | 'trace';
