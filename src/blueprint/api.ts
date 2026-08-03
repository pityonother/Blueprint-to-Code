import { ApiFailure, api } from '../shared/api';
import type {
  BlueprintAssetListResponse,
  BlueprintEvidenceHealthResponse,
  BlueprintEvidenceOperation,
  BlueprintEvidenceQueryResponse,
  BlueprintGapsResponse,
  BlueprintInterpretationResponse,
  BlueprintStatementResponse,
  BlueprintTraceResponse,
} from './types';


export class BlueprintApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function assetSegment(asset: string): string {
  const value = String(asset || '').trim();
  if (!value || value.length > 255 || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error('Blueprint asset identifier is invalid.');
  }
  return encodeURIComponent(value);
}

function queryString(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') {
      params.set(key, String(value));
    }
  }
  const rendered = params.toString();
  return rendered ? `?${rendered}` : '';
}

async function requestBlueprint<T extends { ok: boolean }>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  try {
    return await api<T>(path, options);
  } catch (error) {
    if (error instanceof ApiFailure) {
      throw new BlueprintApiError(error.message, error.status, error.code);
    }
    throw error;
  }
}

export function blueprintAssetEndpoint(asset: string, suffix = ''): string {
  return `/api/blueprint/assets/${assetSegment(asset)}${suffix}`;
}

export function fetchBlueprintAssets(
  query = '',
  cursor = '',
): Promise<BlueprintAssetListResponse> {
  return requestBlueprint<BlueprintAssetListResponse>(
    `/api/blueprint/assets${queryString({ q: query.trim(), limit: 100, cursor })}`,
  );
}

export function fetchBlueprintHealth(asset: string): Promise<BlueprintEvidenceHealthResponse> {
  return requestBlueprint<BlueprintEvidenceHealthResponse>(
    blueprintAssetEndpoint(asset, '/evidence/health'),
  );
}

export function fetchBlueprintInterpretation(
  asset: string,
  cursor = '',
): Promise<BlueprintInterpretationResponse> {
  return requestBlueprint<BlueprintInterpretationResponse>(
    `${blueprintAssetEndpoint(asset, '/interpretation')}${queryString({ limit: 100, cursor })}`,
  );
}

export function fetchBlueprintStatement(
  asset: string,
  statementId: string,
  cursor = '',
): Promise<BlueprintStatementResponse> {
  const segment = encodeURIComponent(String(statementId || ''));
  return requestBlueprint<BlueprintStatementResponse>(
    `${blueprintAssetEndpoint(asset, `/statements/${segment}`)}${queryString({ limit: 100, cursor })}`,
  );
}

export function fetchBlueprintTrace(
  asset: string,
  cursor = '',
): Promise<BlueprintTraceResponse> {
  return requestBlueprint<BlueprintTraceResponse>(
    `${blueprintAssetEndpoint(asset, '/trace')}${queryString({ limit: 100, cursor })}`,
  );
}

export function fetchBlueprintGaps(
  asset: string,
  cursor = '',
): Promise<BlueprintGapsResponse> {
  return requestBlueprint<BlueprintGapsResponse>(
    `${blueprintAssetEndpoint(asset, '/gaps')}${queryString({ limit: 100, cursor })}`,
  );
}

export function queryBlueprintEvidence(
  asset: string,
  evidenceRef: string,
  operation: BlueprintEvidenceOperation,
): Promise<BlueprintEvidenceQueryResponse> {
  return requestBlueprint<BlueprintEvidenceQueryResponse>('/api/evidence-queries', {
    method: 'POST',
    body: JSON.stringify({
      asset: String(asset || '').trim(),
      request: {
        operation,
        selector: { ref: String(evidenceRef || '').trim() },
        traversal: {
          maxHops: operation === 'trace' ? 2 : 1,
          direction: operation === 'trace' ? 'downstream' : 'both',
          edgeKinds: operation === 'trace' ? ['exec'] : ['exec', 'data'],
        },
        pageSize: 20,
        budgetTokens: 2400,
      },
    }),
  });
}
