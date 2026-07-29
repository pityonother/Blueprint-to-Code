import { api } from '../shared/api';
import type {
  KnowledgeEntity,
  KnowledgeEntityDetail,
  KnowledgeHealth,
  KnowledgePage,
  KnowledgeQueryResult,
  KnowledgeShadowCompareResult,
} from './types';


export function fetchKnowledgeHealth(): Promise<KnowledgeHealth> {
  return api<KnowledgeHealth>('/api/kb/health');
}


export function searchKnowledgeEntities(
  query: string,
  limit = 25,
): Promise<KnowledgePage<KnowledgeEntity>> {
  const params = new URLSearchParams({
    q: query,
    limit: String(limit),
    cursor: '0',
  });
  return api<KnowledgePage<KnowledgeEntity>>(
    `/api/kb/entities/search?${params}`,
  );
}


export function fetchKnowledgeEntity(
  entityId: number,
): Promise<KnowledgeEntityDetail> {
  return api<KnowledgeEntityDetail>(`/api/kb/entities/${entityId}`);
}


export function fetchKnowledgeEntityPage(
  entityId: number,
  kind: 'facts' | 'relationships' | 'coverage' | 'effective-defaults',
): Promise<KnowledgePage<Record<string, unknown>>> {
  return api<KnowledgePage<Record<string, unknown>>>(
    `/api/kb/entities/${entityId}/${kind}?limit=50&cursor=0`,
  );
}


export function queryKnowledge(
  request: Record<string, unknown>,
): Promise<KnowledgeQueryResult> {
  return api<KnowledgeQueryResult>('/api/kb/query', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}


export function compareKnowledge(
  request: Record<string, unknown>,
): Promise<KnowledgeShadowCompareResult> {
  return api<KnowledgeShadowCompareResult>('/api/kb/compare', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}
