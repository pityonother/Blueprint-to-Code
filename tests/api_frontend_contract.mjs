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
  const { api } = await server.ssrLoadModule('/src/shared/api.ts');
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

  assert.equal(
    requests.filter((request) => request.path === '/api/session').length,
    1,
    'all clients should share one in-memory session bootstrap',
  );
  const posts = requests.filter((request) => request.method === 'POST');
  assert.equal(posts.length, 2);
  for (const post of posts) {
    assert.equal(
      post.headers.get('X-Blueprint-Session'),
      'session-contract-token-1234567890',
    );
    assert.equal(post.headers.get('Content-Type'), 'application/json');
  }
  assert.equal(posts[0].headers.get('X-Caller-Header'), 'kept');
  const stateGet = requests.find((request) => request.path === '/api/state');
  assert.equal(stateGet.headers.get('X-Blueprint-Session'), null);
} finally {
  await server.close();
  globalThis.fetch = originalFetch;
}

console.log('api frontend contract: ok');
