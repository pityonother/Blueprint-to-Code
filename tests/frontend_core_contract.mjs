import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'vite';


const server = await createServer({
  logLevel: 'silent',
  server: { middlewareMode: true },
});

try {
  const { ApiFailure } = await server.ssrLoadModule('/src/shared/api.ts');
  const { readableError } = await server.ssrLoadModule(
    '/src/shared/errors.ts',
  );
  const { escapeHtml } = await server.ssrLoadModule('/src/shared/html.ts');
  const { workspaceUrl, workspaceViewFromSearch } = await server.ssrLoadModule(
    '/src/app/router.ts',
  );

  assert.equal(
    escapeHtml(`<&>"'`),
    '&lt;&amp;&gt;&quot;&#039;',
  );
  assert.equal(workspaceViewFromSearch('?view=harvest'), 'harvest');
  assert.equal(workspaceViewFromSearch('?view=blueprint'), 'blueprint');

  const harvestUrl = workspaceUrl(
    'http://127.0.0.1:8765/?q=metal&keep=1',
    'harvest',
  );
  assert.equal(harvestUrl.searchParams.get('view'), 'harvest');
  assert.equal(harvestUrl.searchParams.get('q'), 'metal');
  assert.equal(harvestUrl.searchParams.get('keep'), '1');

  const blueprintUrl = workspaceUrl(
    'http://127.0.0.1:8765/?view=harvest&q=metal&node=n1&resource=r1&keep=1',
    'blueprint',
  );
  assert.equal(blueprintUrl.searchParams.has('view'), false);
  assert.equal(blueprintUrl.searchParams.has('q'), false);
  assert.equal(blueprintUrl.searchParams.has('node'), false);
  assert.equal(blueprintUrl.searchParams.has('resource'), false);
  assert.equal(blueprintUrl.searchParams.get('keep'), '1');

  const failure = new ApiFailure(
    {
      ok: false,
      error: 'request failed',
      attemptedPaths: ['one', 'two', 'three', 'four'],
    },
    400,
  );
  const message = readableError(failure);
  assert.match(message, /request failed/);
  assert.match(message, /one/);
  assert.match(message, /three/);
  assert.doesNotMatch(message, /four/);
  assert.equal(readableError('plain failure'), 'plain failure');

  const mainSource = await readFile(
    new URL('../src/main.ts', import.meta.url),
    'utf8',
  );
  assert.match(mainSource, /from '.\/shared\/errors'/);
  assert.match(mainSource, /from '.\/shared\/html'/);
  assert.match(mainSource, /from '.\/app\/router'/);
  assert.doesNotMatch(mainSource, /function escapeHtml\(/);
  assert.doesNotMatch(mainSource, /function readableError\(/);
} finally {
  await server.close();
}

console.log('frontend core contract: ok');
