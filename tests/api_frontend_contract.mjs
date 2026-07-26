import assert from 'node:assert/strict';
import { createServer } from 'vite';

import './frontend_core_contract.mjs';


const requests = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = async (path, init = {}) => {
  const headers = new Headers(init.headers || {});
  requests.push({
    path: String(path),
    method: String(init.method || 'GET').toUpperCase(),
    headers,
    body: init.body,
  });
  if (path === '/api/session') {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        sessionToken: 'session-contract-token-1234567890',
      }),
    };
  }
  return {
    ok: true,
    status: 200,
    json: async () => ({ ok: true }),
  };
};

const server = await createServer({
  logLevel: 'silent',
  server: { middlewareMode: true },
});

try {
  const { api, configureApiAuthToken } = await server.ssrLoadModule(
    '/src/shared/api.ts',
  );
  const { requestHarvestJson } = await server.ssrLoadModule(
    '/src/harvest/api.ts',
  );

  await api('/api/open-captures', {
    method: 'POST',
    body: '{}',
    headers: { 'X-Caller-Header': 'kept' },
  });
  await requestHarvestJson('/api/harvest/build', {
    method: 'POST',
    body: JSON.stringify({ options: {} }),
  });
  await api('/api/state');
  configureApiAuthToken('remote-contract-token');
  await api('/api/state?remote=1');
  await api('/api/open-captures', {
    method: 'POST',
    body: '{}',
  });
  await api('/health');

  const sessionRequests = requests.filter(
    (request) => request.path === '/api/session',
  );
  assert.equal(sessionRequests.length, 2);
  assert.equal(sessionRequests[0].headers.get('Authorization'), null);
  assert.equal(
    sessionRequests[1].headers.get('Authorization'),
    'Bearer remote-contract-token',
  );
  const posts = requests.filter((request) => request.method === 'POST');
  assert.equal(posts.length, 3);
  for (const post of posts) {
    assert.equal(
      post.headers.get('X-Blueprint-Session'),
      'session-contract-token-1234567890',
    );
    assert.equal(post.headers.get('Content-Type'), 'application/json');
  }
  assert.equal(posts[0].headers.get('X-Caller-Header'), 'kept');
  assert.equal(posts[0].headers.get('Authorization'), null);
  assert.equal(posts[1].headers.get('Authorization'), null);
  assert.equal(
    posts[2].headers.get('Authorization'),
    'Bearer remote-contract-token',
  );
  const stateGet = requests.find((request) => request.path === '/api/state');
  assert.equal(stateGet.headers.get('X-Blueprint-Session'), null);
  assert.equal(stateGet.headers.get('Authorization'), null);
  const remoteStateGet = requests.find(
    (request) => request.path === '/api/state?remote=1',
  );
  assert.equal(
    remoteStateGet.headers.get('Authorization'),
    'Bearer remote-contract-token',
  );
  assert.equal(remoteStateGet.headers.get('X-Blueprint-Session'), null);
  const nonApiGet = requests.find((request) => request.path === '/health');
  assert.equal(nonApiGet.headers.get('Authorization'), null);
} finally {
  await server.close();
  globalThis.fetch = originalFetch;
}

console.log('api frontend contract: ok');
