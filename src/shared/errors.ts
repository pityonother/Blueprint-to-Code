import { ApiFailure } from './api';


export function readableError(error: unknown): string {
  if (error instanceof ApiFailure) {
    const attempted = error.payload.attemptedPaths;
    if (Array.isArray(attempted) && attempted.length) {
      return `${error.message} 尝试路径：${attempted.slice(0, 3).join('；')}`;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}
