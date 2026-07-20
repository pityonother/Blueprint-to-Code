import type { HarvestApiResult } from './types';


export class HarvestApiError extends Error {
  code?: string;
  status: number;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}


export async function fetchHarvestJson<T extends HarvestApiResult>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  return requestHarvestJson<T>(path, { signal });
}


export async function requestHarvestJson<T extends HarvestApiResult>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers || {}),
    },
  });
  const payload = (await response.json()) as T;
  if (!response.ok || !payload.ok) {
    throw new HarvestApiError(payload.error || `请求失败：${response.status}`, response.status, payload.code);
  }
  return payload;
}
