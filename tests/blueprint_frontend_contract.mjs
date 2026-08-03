import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'vite';


const server = await createServer({
  logLevel: 'silent',
  server: { middlewareMode: true },
});

try {
  const {
    BLUEPRINT_CLIENT_STALE_CODE,
    BlueprintController,
    blueprintEvidenceQueryMatchesHealth,
    blueprintIdentitiesMatch,
    blueprintIdentityMatchesHealth,
  } = await server.ssrLoadModule(
    '/src/blueprint/controller.ts',
  );
  const { blueprintAssetEndpoint } = await server.ssrLoadModule(
    '/src/blueprint/api.ts',
  );
  const { renderBlueprintAssetList } = await server.ssrLoadModule(
    '/src/blueprint/views/asset-list.ts',
  );
  const { renderBlueprintAssetHealth } = await server.ssrLoadModule(
    '/src/blueprint/views/asset-health.ts',
  );
  const { renderBlueprintInterpretation } = await server.ssrLoadModule(
    '/src/blueprint/views/interpretation.ts',
  );
  const { renderBlueprintStatementDetail } = await server.ssrLoadModule(
    '/src/blueprint/views/statement-detail.ts',
  );
  const { renderBlueprintEvidenceTrace } = await server.ssrLoadModule(
    '/src/blueprint/views/evidence-trace.ts',
  );
  const { renderBlueprintGaps } = await server.ssrLoadModule(
    '/src/blueprint/views/gaps.ts',
  );

  const controller = new BlueprintController(() => {});
  const shell = controller.render({ legacy: '<section>legacy slot</section>', experimental: '' });
  for (const label of ['Interpretation', 'Evidence', 'Gaps', 'Legacy', 'Experimental']) {
    assert.ok(shell.includes(`>${label}<`), `missing primary tab ${label}`);
  }
  assert.match(shell, /role="tablist"/);
  assert.match(shell, /role="tab"/);
  assert.match(shell, /aria-selected="true"/);
  assert.match(shell, /tabindex="0"/);
  assert.match(shell, /role="tabpanel"/);

  assert.equal(
    blueprintAssetEndpoint('Fixture Asset', '/interpretation'),
    '/api/blueprint/assets/Fixture%20Asset/interpretation',
  );

  const assetList = renderBlueprintAssetList([
    {
      asset: '<img src=x onerror=alert(1)>',
      health: { status: 'READY', reasonCode: '<script>bad</script>' },
    },
  ], '', '<svg onload=alert(1)>', false);
  assert.match(assetList, /data-blueprint-form="asset-search"/);
  assert.match(assetList, /data-blueprint-asset=/);
  assert.doesNotMatch(assetList, /<img src=x/);
  assert.doesNotMatch(assetList, /<script>bad<\/script>/);
  assert.doesNotMatch(assetList, /<svg onload=/);

  const healthState = {
    activeTab: 'interpretation',
    assetQuery: '',
    assets: [],
    selectedAsset: 'Fixture',
    health: {
      ok: true,
      schema: 'blueprint-to-code.blueprint-evidence-health-response/v1',
      asset: 'Fixture',
      health: {
        status: 'STALE',
        reasonCode: 'INTERPRETATION_STALE_EVIDENCE',
        asset: { objectPath: '/Game/Test/<unsafe>.Fixture' },
        evidence: {
          revisionId: 'e'.repeat(24),
          manifestSha256: 'a'.repeat(64),
          freshnessStatus: 'STALE',
          releaseAuthority: true,
        },
        interpretation: { revisionId: 'i'.repeat(24) },
      },
    },
    interpretation: null,
    trace: null,
    gaps: null,
    selectedStatement: null,
    selectedEvidenceRef: '',
    evidenceQuery: null,
    loading: false,
    detailLoading: false,
    error: '',
    staleCode: 'BLUEPRINT_INTERPRETATION_STALE',
  };
  const healthHtml = renderBlueprintAssetHealth(healthState);
  assert.match(healthHtml, /blueprint-stale-banner/);
  assert.match(healthHtml, /Evidence revision/);
  assert.match(healthHtml, /Interpretation revision/);
  assert.doesNotMatch(healthHtml, /<unsafe>/);

  const identity = {
    asset: { name: 'Fixture', assetId: 'a'.repeat(24), objectPath: '/Game/Test/Fixture.Fixture' },
    evidence: { revisionId: 'b'.repeat(24), manifestSha256: 'c'.repeat(64) },
    interpretation: {
      revisionId: 'd'.repeat(24),
      manifestSha256: 'e'.repeat(64),
      pointerSha256: 'f'.repeat(64),
      semanticDigest: '1'.repeat(64),
      interpreterVersion: 'fixture',
      schemaVersion: 'blueprint-to-code.blueprint-interpretation/v1',
      generatedAt: '2026-08-03T00:00:00Z',
    },
  };
  const statement = {
    id: 'statement://fixture/event/0',
    kind: 'EVENT',
    text: '<img src=x onerror=alert(1)> Event BeginPlay',
    status: 'CONFIRMED',
    evidenceRefs: ['bp://asset@revision/g/7/n/1'],
    gapRefs: [],
    graphRef: 'bp://asset@revision/g/7',
    nodeRef: 'bp://asset@revision/g/7/n/1',
    sourceOrder: 0,
  };
  const interpretation = {
    ok: true,
    schema: 'blueprint-to-code.blueprint-interpretation-response/v1',
    identity,
    summary: { graphCount: 1, nodeCount: 2, diagnosticGapCount: 1 },
    heuristicReviewHints: [{
      topic: '<script>Damage</script>',
      text: '<img src=x> keyword only',
      basis: 'KEYWORD_AND_NAME_HEURISTIC',
      confidence: 'HEURISTIC',
      notEvidence: true,
    }],
    filters: {},
    items: [statement, { ...statement, id: 'statement://fixture/gap/1', kind: 'GAP', status: 'SOURCE_NOT_AVAILABLE', evidenceRefs: [] }],
    page: { limit: 100, returned: 2, total: 2, nextCursor: null },
  };
  const interpretationHtml = renderBlueprintInterpretation(interpretation, [{ id: 'gap://1' }], false);
  assert.match(interpretationHtml, /本页已确认/);
  assert.match(interpretationHtml, /HEURISTIC：仅供人工复查，不是证据/);
  assert.match(interpretationHtml, /basis=KEYWORD_AND_NAME_HEURISTIC/);
  assert.match(interpretationHtml, /confidence=HEURISTIC/);
  assert.match(interpretationHtml, /notEvidence=true/);
  assert.match(interpretationHtml, /data-blueprint-statement=/);
  assert.doesNotMatch(interpretationHtml, /<img src=x/);
  assert.doesNotMatch(interpretationHtml, /<script>Damage<\/script>/);

  const statementHtml = renderBlueprintStatementDetail({
    ok: true,
    schema: 'blueprint-to-code.blueprint-statement-response/v1',
    identity,
    statement,
    items: [{ traceKind: 'PSEUDOCODE_LINE', statementId: statement.id, pseudocodeLine: 4 }],
    page: { limit: 100, returned: 1, total: 1, nextCursor: null },
  }, false);
  assert.match(statementHtml, /data-blueprint-evidence-operation="neighborhood"/);
  assert.match(statementHtml, /data-blueprint-evidence-operation="trace"/);
  assert.doesNotMatch(statementHtml, /<img src=x/);

  const traceHtml = renderBlueprintEvidenceTrace({
    ok: true,
    schema: 'blueprint-to-code.blueprint-trace-response/v1',
    identity,
    filters: {},
    items: [{ traceKind: 'PSEUDOCODE_LINE', statementId: statement.id, evidenceRefs: statement.evidenceRefs, pseudocodeLine: 4 }],
    page: { limit: 100, returned: 1, total: 1, nextCursor: null },
  }, {
    ok: true,
    operation: 'neighborhood',
    items: [{ label: '<script>alert(1)</script>' }],
  }, statement.evidenceRefs[0], false);
  assert.match(traceHtml, /Evidence bundles/);
  assert.match(traceHtml, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(traceHtml, /<script>alert\(1\)<\/script>/);

  const gapsHtml = renderBlueprintGaps({
    ok: true,
    schema: 'blueprint-to-code.blueprint-gaps-response/v1',
    identity,
    filters: {},
    items: [{
      id: 'gap://fixture/1',
      code: 'EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE',
      status: 'SOURCE_NOT_AVAILABLE',
      detail: '<img src=x onerror=alert(1)>',
      evidenceRefs: statement.evidenceRefs,
    }],
    page: { limit: 100, returned: 1, total: 1, nextCursor: null },
  }, false);
  assert.match(gapsHtml, /不填补未知内容/);
  assert.match(gapsHtml, /EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE/);
  assert.doesNotMatch(gapsHtml, /<img src=x/);

  const evidencePointerSha256 = '2'.repeat(64);
  const readyHealth = {
    ok: true,
    schema: 'blueprint-to-code.blueprint-evidence-health-response/v1',
    asset: 'Fixture',
    health: {
      status: 'READY',
      reasonCode: '',
      asset: { ...identity.asset },
      evidence: {
        ...identity.evidence,
        pointerSha256: evidencePointerSha256,
        freshnessStatus: 'READY',
        releaseAuthority: true,
        migrationRequired: false,
      },
      interpretation: { status: 'READY', ...identity.interpretation },
    },
  };
  const gapsResponse = {
    ok: true,
    schema: 'blueprint-to-code.blueprint-gaps-response/v1',
    identity,
    filters: {},
    items: [{
      id: 'gap://fixture/1',
      code: 'SOURCE_NOT_AVAILABLE',
      status: 'SOURCE_NOT_AVAILABLE',
      detail: 'fixture gap',
      evidenceRefs: statement.evidenceRefs,
    }],
    page: { limit: 100, returned: 1, total: 1, nextCursor: null },
  };
  const traceResponse = {
    ok: true,
    schema: 'blueprint-to-code.blueprint-trace-response/v1',
    identity,
    filters: {},
    items: [{ traceKind: 'PSEUDOCODE_LINE', statementId: statement.id, pseudocodeLine: 4 }],
    page: { limit: 100, returned: 1, total: 1, nextCursor: null },
  };
  const statementResponse = {
    ok: true,
    schema: 'blueprint-to-code.blueprint-statement-response/v1',
    identity,
    statement,
    items: [{ traceKind: 'PSEUDOCODE_LINE', statementId: statement.id, pseudocodeLine: 4 }],
    page: { limit: 100, returned: 1, total: 1, nextCursor: null },
  };
  const evidenceQuery = {
    ok: true,
    operation: 'neighborhood',
    items: [{ marker: 'base' }],
    manifestSha256: identity.evidence.manifestSha256,
    pointerSha256: evidencePointerSha256,
    freshnessStatus: 'READY',
  };
  const assetResponse = {
    ok: true,
    schema: 'blueprint-to-code.blueprint-asset-list-response/v1',
    items: [{ asset: 'Fixture', health: readyHealth.health }],
    page: { limit: 100, returned: 1, total: 1, nextCursor: null },
  };
  const clone = (value) => structuredClone(value);
  const makeClient = (overrides = {}) => ({
    fetchAssets: async () => clone(assetResponse),
    fetchHealth: async () => clone(readyHealth),
    fetchInterpretation: async () => clone(interpretation),
    fetchStatement: async () => clone(statementResponse),
    fetchTrace: async () => clone(traceResponse),
    fetchGaps: async () => clone(gapsResponse),
    queryEvidence: async () => clone(evidenceQuery),
    ...overrides,
  });
  const deferred = () => {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
      resolve = resolvePromise;
      reject = rejectPromise;
    });
    return { promise, resolve, reject };
  };

  assert.equal(blueprintIdentityMatchesHealth(readyHealth, identity, 'Fixture'), true);
  for (const mutate of [
    (candidate) => { candidate.evidence.revisionId = '9'.repeat(24); },
    (candidate) => { candidate.evidence.manifestSha256 = '9'.repeat(64); },
    (candidate) => { candidate.interpretation.revisionId = '9'.repeat(24); },
    (candidate) => { candidate.interpretation.manifestSha256 = '9'.repeat(64); },
    (candidate) => { candidate.interpretation.pointerSha256 = '9'.repeat(64); },
    (candidate) => { candidate.interpretation.semanticDigest = '9'.repeat(64); },
    (candidate) => { candidate.interpretation.interpreterVersion = 'tampered'; },
    (candidate) => { candidate.interpretation.schemaVersion = 'tampered'; },
    (candidate) => { candidate.interpretation.generatedAt = '2099-01-01T00:00:00Z'; },
  ]) {
    const mismatched = clone(identity);
    mutate(mismatched);
    assert.equal(blueprintIdentityMatchesHealth(readyHealth, mismatched, 'Fixture'), false);
    assert.equal(blueprintIdentitiesMatch(identity, mismatched), false);
  }
  assert.equal(blueprintEvidenceQueryMatchesHealth(readyHealth, evidenceQuery), true);
  assert.equal(blueprintEvidenceQueryMatchesHealth(readyHealth, {
    ...evidenceQuery,
    manifestSha256: '9'.repeat(64),
  }), false);

  // Refresh/search and a non-READY health result must clear every asset-scoped payload.
  let currentHealth = clone(readyHealth);
  let observedQuery = '';
  const refreshClient = makeClient({
    fetchAssets: async (query) => {
      observedQuery = query;
      return clone(assetResponse);
    },
    fetchHealth: async () => clone(currentHealth),
  });
  const refreshController = new BlueprintController(() => {}, () => {}, refreshClient);
  await refreshController.refreshAssets('Fixture');
  assert.ok(refreshController.snapshot().interpretation);
  refreshController.snapshot().assetQuery = 'needle';
  currentHealth.health.status = 'STALE';
  const pendingRefresh = refreshController.refreshAssets('Fixture');
  assert.equal(refreshController.snapshot().interpretation, null);
  assert.equal(refreshController.snapshot().gaps, null);
  assert.equal(refreshController.snapshot().trace, null);
  assert.equal(refreshController.snapshot().selectedStatement, null);
  assert.equal(refreshController.snapshot().evidenceQuery, null);
  await pendingRefresh;
  assert.equal(observedQuery, 'needle');
  assert.equal(refreshController.snapshot().health.health.status, 'STALE');
  assert.equal(refreshController.snapshot().staleCode, BLUEPRINT_CLIENT_STALE_CODE);

  // Switching assets clears the old payload synchronously, before the new health request settles.
  const otherHealth = deferred();
  const switchController = new BlueprintController(() => {}, () => {}, makeClient({
    fetchHealth: async (asset) => asset === 'Fixture' ? clone(readyHealth) : otherHealth.promise,
  }));
  await switchController.refreshAssets('Fixture');
  const switching = switchController.selectAsset('Other', true);
  assert.equal(switchController.snapshot().selectedAsset, 'Other');
  assert.equal(switchController.snapshot().interpretation, null);
  otherHealth.resolve({
    ...clone(readyHealth),
    asset: 'Other',
    health: {
      ...clone(readyHealth.health),
      status: 'MISSING',
      asset: { name: 'Other', assetId: '', objectPath: '' },
    },
  });
  await switching;
  assert.equal(switchController.snapshot().interpretation, null);

  // Interpretation and gaps are admitted atomically; any mixed revision fails closed.
  const mismatchedGaps = clone(gapsResponse);
  mismatchedGaps.identity.interpretation.pointerSha256 = '9'.repeat(64);
  const mismatchController = new BlueprintController(() => {}, () => {}, makeClient({
    fetchGaps: async () => clone(mismatchedGaps),
  }));
  await mismatchController.refreshAssets('Fixture');
  assert.equal(mismatchController.snapshot().interpretation, null);
  assert.equal(mismatchController.snapshot().gaps, null);
  assert.equal(mismatchController.snapshot().staleCode, BLUEPRINT_CLIENT_STALE_CODE);

  const mismatchedTrace = clone(traceResponse);
  mismatchedTrace.identity.evidence.manifestSha256 = '9'.repeat(64);
  const traceMismatchController = new BlueprintController(() => {}, () => {}, makeClient({
    fetchTrace: async () => clone(mismatchedTrace),
  }));
  await traceMismatchController.refreshAssets('Fixture');
  await traceMismatchController.loadTrace();
  assert.equal(traceMismatchController.snapshot().trace, null);
  assert.equal(traceMismatchController.snapshot().interpretation, null);
  assert.equal(traceMismatchController.snapshot().staleCode, BLUEPRINT_CLIENT_STALE_CODE);

  const mismatchedStatement = clone(statementResponse);
  mismatchedStatement.identity.interpretation.revisionId = '9'.repeat(24);
  const statementMismatchController = new BlueprintController(() => {}, () => {}, makeClient({
    fetchStatement: async () => clone(mismatchedStatement),
  }));
  await statementMismatchController.refreshAssets('Fixture');
  await statementMismatchController.selectStatement(statement.id);
  assert.equal(statementMismatchController.snapshot().selectedStatement, null);
  assert.equal(statementMismatchController.snapshot().interpretation, null);
  assert.equal(statementMismatchController.snapshot().staleCode, BLUEPRINT_CLIENT_STALE_CODE);

  // Statement and Evidence details use independent epochs; late responses cannot win.
  const statementA = deferred();
  const statementB = deferred();
  const evidenceA = deferred();
  const evidenceB = deferred();
  const detailClient = makeClient({
    fetchStatement: async (_asset, statementId) => statementId.endsWith('/A') ? statementA.promise : statementB.promise,
    queryEvidence: async (_asset, evidenceRef) => evidenceRef.endsWith('/1') ? evidenceA.promise : evidenceB.promise,
  });
  const detailController = new BlueprintController(() => {}, () => {}, detailClient);
  await detailController.refreshAssets('Fixture');
  const statementAId = 'statement://fixture/A';
  const statementBId = 'statement://fixture/B';
  const loadStatementA = detailController.selectStatement(statementAId);
  const loadStatementB = detailController.selectStatement(statementBId);
  statementB.resolve({ ...clone(statementResponse), statement: { ...clone(statement), id: statementBId } });
  await loadStatementB;
  statementA.resolve({ ...clone(statementResponse), statement: { ...clone(statement), id: statementAId } });
  await loadStatementA;
  assert.equal(detailController.snapshot().selectedStatement.statement.id, statementBId);

  const evidenceRefA = 'bp://asset@revision/g/7/n/1';
  const evidenceRefB = 'bp://asset@revision/g/7/n/2';
  const loadEvidenceA = detailController.inspectEvidence(evidenceRefA, 'neighborhood');
  const loadEvidenceB = detailController.inspectEvidence(evidenceRefB, 'neighborhood');
  evidenceB.resolve({ ...clone(evidenceQuery), items: [{ marker: 'B' }] });
  await loadEvidenceB;
  evidenceA.resolve({ ...clone(evidenceQuery), items: [{ marker: 'A' }] });
  await loadEvidenceA;
  assert.equal(detailController.snapshot().selectedEvidenceRef, evidenceRefB);
  assert.equal(detailController.snapshot().evidenceQuery.items[0].marker, 'B');

  // Focus contracts survive the shell's full innerHTML redraw.
  let focused = '';
  const statementRow = { dataset: { blueprintStatement: statementBId }, focus: () => { focused = 'statement'; } };
  const detailHeading = { dataset: {}, focus: () => { focused = 'detail-heading'; } };
  const evidenceHeading = { dataset: {}, focus: () => { focused = 'evidence-heading'; } };
  const originalDocument = globalThis.document;
  globalThis.document = {
    activeElement: null,
    querySelector: (selector) => selector.includes('statement-detail-heading')
      ? detailHeading
      : selector.includes('evidence-query-heading') ? evidenceHeading : null,
    querySelectorAll: (selector) => selector.includes('blueprint-statement') ? [statementRow] : [],
    getElementById: () => null,
  };
  detailController.pendingFocus = { kind: 'detail-heading', value: '' };
  detailController.restoreFocus();
  assert.equal(focused, 'detail-heading');
  detailController.closeStatement();
  detailController.restoreFocus();
  assert.equal(focused, 'statement');
  detailController.pendingFocus = { kind: 'evidence-heading', value: '' };
  detailController.restoreFocus();
  assert.equal(focused, 'evidence-heading');
  globalThis.document = originalDocument;

  // Every cursor-bearing collection has an actionable, identity-bound next page.
  const firstInterpretation = clone(interpretation);
  firstInterpretation.items = [clone(statement)];
  firstInterpretation.page = { limit: 1, returned: 1, total: 2, nextCursor: 'interpretation-next' };
  const firstGaps = clone(gapsResponse);
  firstGaps.page = { limit: 1, returned: 1, total: 2, nextCursor: 'gaps-next' };
  const firstAssets = clone(assetResponse);
  firstAssets.page = { limit: 1, returned: 1, total: 2, nextCursor: 'assets-next' };
  const firstTrace = clone(traceResponse);
  firstTrace.page = { limit: 1, returned: 1, total: 2, nextCursor: 'trace-next' };
  const firstStatement = clone(statementResponse);
  firstStatement.page = { limit: 1, returned: 1, total: 2, nextCursor: 'statement-next' };
  const paginationClient = makeClient({
    fetchAssets: async (_query, cursor) => cursor ? {
      ...clone(assetResponse),
      items: [{ asset: 'FixtureTwo', health: clone(readyHealth.health) }],
      page: { limit: 1, returned: 1, total: 2, nextCursor: null },
    } : clone(firstAssets),
    fetchInterpretation: async (_asset, cursor) => cursor ? {
      ...clone(interpretation),
      items: [{ ...clone(statement), id: 'statement://fixture/page/2' }],
      page: { limit: 1, returned: 1, total: 2, nextCursor: null },
    } : clone(firstInterpretation),
    fetchGaps: async (_asset, cursor) => cursor ? {
      ...clone(gapsResponse),
      items: [{ ...clone(gapsResponse.items[0]), id: 'gap://fixture/2' }],
      page: { limit: 1, returned: 1, total: 2, nextCursor: null },
    } : clone(firstGaps),
    fetchTrace: async (_asset, cursor) => cursor ? {
      ...clone(traceResponse),
      items: [{ traceKind: 'PSEUDOCODE_LINE', statementId: statement.id, pseudocodeLine: 5 }],
      page: { limit: 1, returned: 1, total: 2, nextCursor: null },
    } : clone(firstTrace),
    fetchStatement: async (_asset, _statementId, cursor) => cursor ? {
      ...clone(statementResponse),
      items: [{ traceKind: 'PSEUDOCODE_LINE', statementId: statement.id, pseudocodeLine: 5 }],
      page: { limit: 1, returned: 1, total: 2, nextCursor: null },
    } : clone(firstStatement),
  });
  const paginationController = new BlueprintController(() => {}, () => {}, paginationClient);
  await paginationController.refreshAssets('Fixture');
  await paginationController.loadMoreAssets();
  await paginationController.loadMoreInterpretation();
  await paginationController.loadMoreGaps();
  await paginationController.loadTrace();
  await paginationController.loadTrace(undefined, 'trace-next');
  await paginationController.selectStatement(statement.id);
  await paginationController.selectStatement(statement.id, 'statement-next');
  assert.equal(paginationController.snapshot().assets.length, 2);
  assert.equal(paginationController.snapshot().interpretation.items.length, 2);
  assert.equal(paginationController.snapshot().gaps.items.length, 2);
  assert.equal(paginationController.snapshot().trace.items.length, 2);
  assert.equal(paginationController.snapshot().selectedStatement.items.length, 2);
  assert.equal(paginationController.snapshot().interpretation.page.nextCursor, null);
  assert.equal(paginationController.snapshot().gaps.page.nextCursor, null);
  assert.equal(paginationController.snapshot().trace.page.nextCursor, null);
  assert.equal(paginationController.snapshot().selectedStatement.page.nextCursor, null);

  const paginationHtml = paginationController.render({ legacy: '', experimental: '' });
  assert.doesNotMatch(paginationHtml, /load-more-statements/);
  assert.doesNotMatch(paginationHtml, /load-more-assets/);
  assert.match(renderBlueprintInterpretation(firstInterpretation, [], false), /data-blueprint-action="load-more-statements"/);
  assert.match(renderBlueprintGaps(firstGaps, false), /data-blueprint-action="load-more-gaps"/);
  assert.match(renderBlueprintEvidenceTrace(firstTrace, null, '', false), /data-blueprint-action="load-more-trace"/);
  assert.match(renderBlueprintStatementDetail(firstStatement, false), /data-blueprint-action="load-more-statement-trace"/);
  assert.match(renderBlueprintAssetList(firstAssets.items, 'Fixture', '', false, firstAssets.page), /data-blueprint-action="load-more-assets"/);

  const pageA = deferred();
  const pageB = deferred();
  let interpretationPageCall = 0;
  const epochController = new BlueprintController(() => {}, () => {}, makeClient({
    fetchInterpretation: async (_asset, cursor) => {
      if (!cursor) return clone(firstInterpretation);
      interpretationPageCall += 1;
      return interpretationPageCall === 1 ? pageA.promise : pageB.promise;
    },
    fetchGaps: async () => clone(gapsResponse),
  }));
  await epochController.refreshAssets('Fixture');
  const olderPage = epochController.loadMoreInterpretation();
  const newerPage = epochController.loadMoreInterpretation();
  pageB.resolve({
    ...clone(interpretation),
    items: [{ ...clone(statement), id: 'statement://fixture/newest-page' }],
    page: { limit: 1, returned: 1, total: 2, nextCursor: null },
  });
  await newerPage;
  pageA.resolve({
    ...clone(interpretation),
    items: [{ ...clone(statement), id: 'statement://fixture/older-page' }],
    page: { limit: 1, returned: 1, total: 2, nextCursor: null },
  });
  await olderPage;
  assert.deepEqual(
    epochController.snapshot().interpretation.items.map((item) => item.id),
    [statement.id, 'statement://fixture/newest-page'],
  );

  // A cursor that does not advance is rejected instead of creating an infinite replay loop.
  const replayController = new BlueprintController(() => {}, () => {}, makeClient({
    fetchInterpretation: async (_asset, cursor) => cursor ? {
      ...clone(interpretation),
      items: [{ ...clone(statement), id: 'statement://fixture/replayed' }],
      page: { limit: 1, returned: 1, total: 3, nextCursor: cursor },
    } : clone(firstInterpretation),
    fetchGaps: async () => clone(gapsResponse),
  }));
  await replayController.refreshAssets('Fixture');
  await replayController.loadMoreInterpretation();
  assert.equal(replayController.snapshot().interpretation, null);
  assert.equal(replayController.snapshot().staleCode, BLUEPRINT_CLIENT_STALE_CODE);

  const apiSource = await readFile(new URL('../src/blueprint/api.ts', import.meta.url), 'utf8');
  assert.match(apiSource, /\/api\/blueprint\/assets/);
  assert.match(apiSource, /\/api\/evidence-queries/);
  assert.doesNotMatch(apiSource, /\/api\/state/);
  assert.doesNotMatch(apiSource, /\/api\/report/);

  const main = await readFile(new URL('../src/main.ts', import.meta.url), 'utf8');
  assert.match(main, /new BlueprintController/);
  assert.match(main, /blueprintController\.render/);
  assert.match(main, /blueprintController\.ensureLoaded/);
  assert.match(main, /legacy: renderStepReports/);

  const css = await readFile(new URL('../src/styles.css', import.meta.url), 'utf8');
  assert.match(css, /\.blueprint-primary-layout/);
  assert.match(css, /grid-template-columns: minmax\(230px, 280px\) minmax\(0, 1fr\)/);
  assert.match(css, /@media \(max-width: 430px\)/);
  assert.match(css, /overflow-wrap: anywhere/);
  assert.match(css, /\.blueprint-primary-tab:focus-visible/);
  assert.match(css, /\.blueprint-load-more/);
  assert.match(css, /\.blueprint-load-more \{[\s\S]*max-width: 100%/);

  const workflow = await readFile(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8');
  assert.match(workflow, /node tests\/blueprint_frontend_contract\.mjs/);
} finally {
  await server.close();
}

console.log('blueprint frontend contract: ok');
