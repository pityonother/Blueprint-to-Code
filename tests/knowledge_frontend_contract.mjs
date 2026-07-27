import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'vite';


const server = await createServer({
  logLevel: 'silent',
  server: { middlewareMode: true },
});

try {
  const { KnowledgeWorkspace } = await server.ssrLoadModule(
    '/src/kb/workspace.ts',
  );
  const workspace = new KnowledgeWorkspace(() => {});
  const html = workspace.render();
  assert.match(html, /id="kb-search-form"/);
  assert.match(html, /id="kb-search-input"/);
  assert.match(html, /id="kb-query-form"/);
  assert.match(html, /id="kb-query-entity"/);
  assert.match(html, /aria-busy=/);

  const css = await readFile(
    new URL('../src/styles.css', import.meta.url),
    'utf8',
  );
  assert.match(css, /\.kb-explorer-grid/);
  assert.match(css, /@media \(max-width: 700px\)/);
  assert.match(css, /\.kb-search-form input:focus-visible/);

  const main = await readFile(
    new URL('../src/main.ts', import.meta.url),
    'utf8',
  );
  assert.match(main, /data-workspace="knowledge"/);
  assert.match(main, /knowledgeWorkspace\.ensureLoaded\(\)/);
} finally {
  await server.close();
}

console.log('knowledge frontend contract: ok');
