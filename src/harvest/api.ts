import type { HarvestApiResult } from './types';
import { ApiFailure, api } from '../shared/api';


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
  try {
    return await api<T>(path, init);
  } catch (error) {
    if (error instanceof ApiFailure) {
      throw new HarvestApiError(
        error.message,
        error.status,
        error.code,
      );
    }
    throw error;
  }
}
