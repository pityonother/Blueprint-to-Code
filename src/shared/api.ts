export interface ApiResult {
  ok: boolean;
  error?: string;
  code?: string;
  attemptedPaths?: unknown;
}


export class ApiFailure extends Error {
  payload: ApiResult;
  status: number;
  code?: string;

  constructor(payload: ApiResult, status: number) {
    super(payload.error || `请求失败：${status}`);
    this.payload = payload;
    this.status = status;
    this.code = typeof payload.code === 'string' ? payload.code : undefined;
  }
}


interface SessionResponse extends ApiResult {
  sessionToken?: string;
}


let sessionToken = '';
let sessionRequest: Promise<string> | null = null;
let remoteAuthToken = '';


export function configureApiAuthToken(token: string): void {
  remoteAuthToken = String(token || '').trim();
  sessionToken = '';
  sessionRequest = null;
}


async function loadSession(): Promise<string> {
  const headers = new Headers();
  if (remoteAuthToken) {
    headers.set('Authorization', `Bearer ${remoteAuthToken}`);
  }
  const response = await fetch('/api/session', {
    method: 'GET',
    headers,
    cache: 'no-store',
    credentials: 'same-origin',
  });
  const payload = (await response.json()) as SessionResponse;
  if (
    !response.ok
    || !payload.ok
    || typeof payload.sessionToken !== 'string'
    || !payload.sessionToken
  ) {
    throw new ApiFailure(payload, response.status);
  }
  sessionToken = payload.sessionToken;
  return sessionToken;
}


async function ensureSession(): Promise<string> {
  if (sessionToken) {
    return sessionToken;
  }
  if (!sessionRequest) {
    sessionRequest = loadSession().finally(() => {
      sessionRequest = null;
    });
  }
  return sessionRequest;
}


export async function api<T extends ApiResult>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const method = String(options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  const request: RequestInit = {
    ...options,
    method,
    headers,
    credentials: options.credentials || 'same-origin',
  };
  if (method === 'POST') {
    headers.set('X-Blueprint-Session', await ensureSession());
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    if (remoteAuthToken) {
      headers.set('Authorization', `Bearer ${remoteAuthToken}`);
    }
    if (request.body === undefined || request.body === null) {
      request.body = '{}';
    }
  }
  const response = await fetch(path, request);
  const payload = (await response.json()) as T;
  if (!response.ok || !payload.ok) {
    throw new ApiFailure(payload, response.status);
  }
  return payload;
}
