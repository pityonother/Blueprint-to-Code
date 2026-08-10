import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';


const readSource = (relativePath) => readFile(
  new URL(`../${relativePath}`, import.meta.url),
  'utf8',
);


const expectedActions = [
  'analyze-compact',
  'analyze-debug',
  'analyze-standard',
  'build-knowledge-base',
  'cancel-job',
  'capture-page',
  'capture-page-analyze',
  'capture-queue-clear',
  'capture-queue-current',
  'capture-queue-reset',
  'capture-queue-skip',
  'clear-missing-selection',
  'copy-output-command',
  'copy-python-command',
  'inspect-graph-queue',
  'load-graph-queue-all',
  'load-graph-queue-compact',
  'load-graph-queue-focused',
  'load-graph-queue-recommended',
  'load-uasset-failed-queue',
  'mark-missing-ignore',
  'mark-missing-inherited',
  'mine-uasset-candidates',
  'mine-uasset-candidates-copy',
  'open-capture-root',
  'open-current-report',
  'open-graph-reports',
  'open-knowledge-folder',
  'open-knowledge-global-report',
  'open-knowledge-priority-report',
  'open-knowledge-priority-results',
  'open-knowledge-report',
  'open-notes',
  'open-output',
  'open-uasset-compare',
  'open-uasset-diagnostics',
  'open-uasset-gates',
  'open-uasset-links',
  'open-uasset-triage',
  'read-priority-assets',
  'read-uasset-graphs',
  'refresh',
  'run-compare',
  'save-devkit-request',
  'select-all-missing',
].sort();

const expectedStorageKeys = [
  'blueprint-tool.captureQueue',
  'blueprint-tool.captureQueueCursor',
  'blueprint-tool.selected',
];

const mainSource = await readSource('src/main.ts');
const shellSource = await readSource('src/app/shell.ts');
const workspaceSource = await readSource('src/control-center/workspace.ts');
const controlCenterSources = await Promise.all([
  'src/control-center/types.ts',
  'src/control-center/capture-queue.ts',
  'src/control-center/workspace.ts',
  'src/control-center/views/common.ts',
  'src/control-center/views/workflow.ts',
  'src/control-center/views/capture.ts',
  'src/control-center/views/advanced.ts',
].map(readSource));
const viewSources = controlCenterSources.slice(3);

assert.match(mainSource, /import \{ LegacyControlCenterWorkspace \} from '.\/control-center\/workspace'/);
assert.doesNotMatch(mainSource, /\/api\/capture-graph/);
assert.doesNotMatch(mainSource, /\/api\/analyze/);
assert.doesNotMatch(mainSource, /\/api\/knowledge-base\/build/);
assert.doesNotMatch(mainSource, /interface AppState/);
assert.doesNotMatch(mainSource, /function capturePage/);

assert.match(workspaceSource, /export class LegacyControlCenterWorkspace/);
assert.match(workspaceSource, /private state: AppState \| null/);
assert.match(workspaceSource, /async handleAction\(/);
assert.match(workspaceSource, /\/api\/capture-graph/);
assert.match(workspaceSource, /\/api\/analyze/);
assert.match(workspaceSource, /\/api\/knowledge-base\/build/);

for (const source of viewSources) {
  assert.doesNotMatch(source, /\bapi\s*\(/);
  assert.doesNotMatch(source, /\bfetch\s*\(/);
  assert.doesNotMatch(source, /\bdocument\./);
  assert.doesNotMatch(source, /\bwindow\./);
  assert.doesNotMatch(source, /\bnavigator\./);
}

for (const source of controlCenterSources) {
  assert.doesNotMatch(source, /from ['"]\.\.\/main['"]/);
  assert.doesNotMatch(source, /from ['"]\.\.\/\.\.\/main['"]/);
}

assert.doesNotMatch(shellSource, /BlueprintController/);
assert.doesNotMatch(shellSource, /HarvestExplorer/);
assert.doesNotMatch(shellSource, /KnowledgeWorkspace/);
assert.doesNotMatch(shellSource, /LegacyControlCenterWorkspace/);

const dispatchedActions = [...workspaceSource.matchAll(/action === '([^']+)'/g)]
  .map((match) => match[1])
  .sort();
assert.deepEqual(dispatchedActions, expectedActions);
assert.match(workspaceSource, /action\.startsWith\('open-report-'\)/);

const storageKeys = [...new Set(
  controlCenterSources
    .flatMap((source) => [...source.matchAll(/blueprint-tool\.[A-Za-z0-9._-]+/g)])
    .map((match) => match[0]),
)].sort();
assert.deepEqual(storageKeys, expectedStorageKeys);

assert.match(mainSource, /new BlueprintController\(/);
assert.match(mainSource, /new HarvestExplorer\(/);
assert.match(mainSource, /new KnowledgeWorkspace\(/);

console.log('frontend shell modularity contract: ok');
