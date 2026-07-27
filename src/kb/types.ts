import type { ApiResult } from '../shared/api';


export interface KnowledgeHealth extends ApiResult {
  available: boolean;
  status: string;
  buildId: string;
  schemaVersion?: string;
  ontologyVersion?: string;
  cutover: {
    mode: string;
    defaultQuerySource: string;
    reason?: string;
  };
  freshness: string;
  gap: Array<{ code: string; detail?: string }>;
}


export interface KnowledgeEntity {
  entityId: number;
  canonicalUri: string;
  entityKind: string;
  displayName: string;
  internalName: string;
  status: string;
  confidence: string;
}


export interface KnowledgePage<T> extends ApiResult {
  items: T[];
  returned: number;
  omitted: number;
  nextQuery: string;
  freshness: string;
  evidence: Array<Record<string, unknown>>;
  gap: Array<Record<string, unknown>>;
}


export interface KnowledgeEntityDetail extends ApiResult {
  entity: KnowledgeEntity;
  roles: Array<{
    role: string;
    confidence: string;
    status: string;
    reasons: string[];
  }>;
  domains: Array<{
    domainId: string;
    membershipKind: string;
    confidence: string;
    status: string;
    evidenceUri: string;
  }>;
  evidence: Array<Record<string, unknown>>;
}


export interface KnowledgeQueryResult extends ApiResult {
  route: string;
  entity: KnowledgeEntity | null;
  facts: Array<Record<string, unknown>>;
  relationships: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
  missingRequirements: Array<{ code: string; requirement: string }>;
  recommendedProbes: Array<Record<string, unknown>>;
  contextPack: {
    estimatedTokens: number;
    budgetTokens: number;
    returned: number;
    omitted: number;
    truncated: boolean;
    content: string;
  };
  freshness: string;
  gap: Array<Record<string, unknown>>;
}
