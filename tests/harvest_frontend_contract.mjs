import assert from 'node:assert/strict';
import { createServer } from 'vite';


const server = await createServer({
  logLevel: 'silent',
  server: { middlewareMode: true },
});

try {
  const {
    mapEvidenceLabel,
    mapFamilies,
    renderHarvestDatasetBar,
    renderHarvestRankingResult,
  } = await server.ssrLoadModule(
    '/src/harvest/explorer.ts',
  );
  const {
    renderHarvestCreaturePage,
    renderHarvestCreatureSpecialties,
  } = await server.ssrLoadModule('/src/harvest/creatures.ts');
  const { renderHarvestBuildPanel } = await server.ssrLoadModule(
    '/src/harvest/build-control.ts',
  );
  assert.deepEqual(
    mapFamilies({
      mapUsage: {
        status: 'PARTIAL',
        claimsCompleteMapUsage: false,
        families: [
          { mapFamily: 'TheIsland' },
          { mapFamily: 'Genesis2' },
        ],
      },
      mapReferences: {
        status: 'REFERENCE_SCAN_COMPLETE',
        count: 1,
        items: [
          {
            id: 'pcg',
            name: 'PCGBiome_Jungle',
            objectPath: '/Game/Art_Tools/Level_Tools/PCG/PCG_Biomes/TheIsland/PCGBiome_Jungle',
            mapFamily: 'TheIsland',
            mapKind: 'PLAYABLE_MAP_EVIDENCE',
            relation: 'PCG_BIOME_REFERENCE',
          },
        ],
      },
    }),
    ['Genesis2', 'TheIsland'],
  );
  assert.equal(mapEvidenceLabel({ relation: 'PCG_BIOME_REFERENCE' }), 'PCG 生物群系依赖');
  const v2 = {
    ok: true,
    schema: 'blueprint-to-code.harvest-ranking-result/v2',
    dataset: {},
    node: { id: 'node', name: 'Metal Rock', objectPath: '/Game/Node' },
    resource: {
      entryIndex: 0,
      resource: 'PrimalItemResource_Metal_C',
      nodeResourceId: 'resource',
    },
    methodology: {
      metric: 'engineComparisonIndex',
      scoreBasis: 'INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD',
      formulaVersion: 'harvest-engine-comparison-index/v1',
      usageScope: 'TAMED_RIDDEN',
      warning: 'Not observed yield.',
    },
    scopeStatus: 'ALL_DISCOVERED_CREATURES_EVALUATED',
    claimsCompleteWithinScope: true,
    claimsGlobalTop: false,
    coverage: {
      candidateDiscovery: { candidatesDiscovered: 1422 },
      creatureAssetsCataloged: 343,
      speciesCataloged: 278,
      attacksDecoded: 1038,
      attacksEvaluated: 849,
      attacksExcludedByScope: 188,
      attacksExcludedByCreatureScope: 1,
      attacksRanked: 560,
      attacksUnranked: 140,
      attacksIncompatible: 149,
      sourceFingerprintsComplete: true,
      damageTypesDecoded: 52,
      damageTypesWithGaps: 0,
      excludedByReason: {
        ATTACK_PREVENTED_WITH_RIDER: 188,
        '<script>unsafe-reason</script>': 1,
      },
      excludedCreatureByReason: { BOSS_DINO: 1 },
      returned: 1,
      omitted: 2,
    },
    items: [
      {
        rank: 1,
        creature: '<img src=x onerror=alert(1)>',
        creatureObjectPath: '/Game/Dinos/AnkyVariant',
        speciesKey: 'anky',
        variantCount: 2,
        attackName: 'Tail',
        rankingStatus: 'RANKED',
        reasonCode: 'ENGINE_COEFFICIENTS_RECOVERED',
        baseAttackInterval: 0.5,
        riderAttackInterval: 2,
        attackInterval: 2,
        attackIntervalSource: 'RIDER_ATTACK_INTERVAL',
        tameabilityStatus: 'UNKNOWN',
        tameabilityReasonCodes: ['TAMEABILITY_NOT_RECOVERED'],
        rideabilityStatus: 'ALLOWED',
        rideabilityReasonCodes: [],
        evidence: { status: 'PARTIAL', gaps: ['TAMEABILITY_NOT_RECOVERED'] },
        engineComparisonIndex: 80,
        relativeToNodeTopPercent: 100,
        rankingTier: 'CONDITIONAL',
        missingFacts: [],
        warnings: [],
      },
    ],
  };

  const html = renderHarvestRankingResult(v2);
  assert.match(html, /已扫描范围完整 Top 10/);
  assert.match(html, /候选生物/);
  assert.match(html, /物种/);
  assert.match(html, /本次评估/);
  assert.match(html, /排除 189/);
  assert.match(html, /骑乘间隔 2 秒/);
  assert.match(html, /2 个变体/);
  assert.match(html, /可驯服性尚未恢复/);
  assert.match(html, /可骑乘已确认/);
  assert.match(html, /条件证据/);
  assert.match(html, /ATTACK_PREVENTED_WITH_RIDER/);
  assert.match(html, /相对本节点最强 100%/);
  assert.match(html, /证据与口径/);
  assert.doesNotMatch(html, /<img src=x onerror=alert\(1\)>/);
  assert.doesNotMatch(html, /<script>unsafe-reason<\/script>/);

  const partialEvidenceHtml = renderHarvestRankingResult({
    ...v2,
    evidence: { status: 'PARTIAL', blockers: ['DAMAGE_TYPE_GAP'] },
  });
  assert.match(partialEvidenceHtml, /证据部分缺失/);
  assert.match(partialEvidenceHtml, /DAMAGE_TYPE_GAP/);

  const v1 = {
    ...v2,
    schema: 'blueprint-to-code.harvest-ranking-result/v1',
    claimsCompleteWithinScope: undefined,
    coverage: { creaturesLoaded: 4, returned: 1, omitted: 0 },
    items: [{ ...v2.items[0], variantCount: undefined }],
  };
  const legacyHtml = renderHarvestRankingResult(v1);
  assert.match(legacyHtml, /已扫描范围 Top 10/);
  assert.match(legacyHtml, /生物资产[^<]*4/);

  const datasetBar = renderHarvestDatasetBar({
    ok: true,
    schema: 'ark-resource-node-catalog/v1',
    dataset: { revision: 'c'.repeat(64), sourceStatus: 'CURRENT_AT_GENERATION' },
    coverage: {
      discoveryMode: 'DISCOVERED',
      candidateDiscovery: { candidatesDiscovered: 1984, candidatesSelected: 1984 },
      nodesDecoded: 1327,
      creatureCandidatesDiscovered: 1422,
      creatureAssetsCataloged: 1322,
      speciesCataloged: 254,
      rankingCreatures: 4,
      mapScan: { filesScanned: 1490 },
      images: { available: 1326 },
    },
    total: 1327,
    offset: 0,
    limit: 16,
    nextOffset: 16,
    items: [],
  });
  assert.match(datasetBar, /候选生物 1,422/);
  assert.match(datasetBar, /生物资产 1,322/);
  assert.match(datasetBar, /物种 254/);
  assert.doesNotMatch(datasetBar, /当前仅评估 4 只生物/);

  const creaturePageHtml = renderHarvestCreaturePage({
    ok: true,
    schema: 'blueprint-to-code.harvest-creature-page/v1',
    dataset: { evaluationRevision: 'e'.repeat(64) },
    coverage: { speciesCataloged: 254, claimsAllCreatures: false },
    total: 2,
    offset: 0,
    limit: 20,
    nextOffset: null,
    items: [
      {
        speciesKey: 'anky',
        name: '<img src=x onerror=alert(1)>',
        dinoNameTag: 'Anky',
        variantCount: 3,
        attackCount: 5,
        tameabilityStatuses: ['TAMEABLE'],
        rideabilityStatuses: ['ALLOWED'],
      },
    ],
  }, 'anky');
  assert.match(creaturePageHtml, /Anky/);
  assert.match(creaturePageHtml, /3 个变体/);
  assert.match(creaturePageHtml, /5 个攻击/);
  assert.doesNotMatch(creaturePageHtml, /<img src=x onerror=alert\(1\)>/);

  const specialtyHtml = renderHarvestCreatureSpecialties({
    ok: true,
    schema: 'blueprint-to-code.harvest-creature-specialties/v1',
    dataset: { evaluationRevision: 'e'.repeat(64) },
    species: { speciesKey: 'anky', name: 'Ankylosaurus', dinoNameTag: 'Anky', variantCount: 3 },
    methodology: {
      metric: 'engineComparisonIndex',
      sortMetric: 'relativeToNodeTopPercent',
      scoreBasis: 'INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD',
      warning: '不是实测掉落量。',
    },
    scopeStatus: 'PARTIAL_CREATURE_EVIDENCE',
    claimsCompleteWithinScope: false,
    claimsGlobalTop: false,
    claimBlockers: ['CREATURE_DISCOVERY_NOT_PROVEN_COMPLETE'],
    evidence: { status: 'PARTIAL', blockers: [] },
    coverage: {
      nodeResourcePairsDiscovered: 7402,
      uniqueEvaluationPairs: 903,
      uniqueEvaluationPairsRanked: 650,
      nodeResourcePairsRanked: 650,
      returned: 1,
    },
    page: { offset: 0, limit: 24, total: 650, returned: 1, omitted: 649 },
    items: [
      {
        rank: 1,
        creature: 'Ankylosaurus',
        speciesKey: 'anky',
        attackName: 'Tail',
        engineComparisonIndex: 42.5,
        relativeToNodeTopPercent: 97.25,
        nodeTopEngineComparisonIndex: 43.7,
        rankingStatus: 'RANKED',
        rankingTier: 'CONFIRMED',
        node: { id: 'metal-rock', name: '<script>bad</script>', objectPath: '/Game/MetalRock' },
        resource: {
          entryIndex: 0,
          resource: 'PrimalItemResource_Metal_C',
          displayName: 'Metal',
          nodeResourceId: 'metal-entry',
        },
        nodeTop: {
          speciesKey: 'doed',
          creature: 'Doedicurus',
          attackName: 'Roll',
          engineComparisonIndex: 43.7,
        },
        evidence: { status: 'COMPLETE', gaps: [] },
      },
    ],
  });
  assert.match(specialtyHtml, /97.25%/);
  assert.match(specialtyHtml, /42.5/);
  assert.match(specialtyHtml, /Doedicurus/);
  assert.match(specialtyHtml, /650/);
  assert.match(specialtyHtml, /不声称全游戏实测产量/);
  assert.doesNotMatch(specialtyHtml, /<script>bad<\/script>/);

  const buildHtml = renderHarvestBuildPanel({
    id: 'job-1',
    status: 'RUNNING',
    pid: 1234,
    returnCode: null,
    createdAt: '2026-07-21T00:00:00+00:00',
    startedAt: '2026-07-21T00:00:01+00:00',
    finishedAt: null,
    cancelRequested: false,
    error: '',
    progress: { current: 5, total: 8, label: 'build_ark_resource_node_catalog.py', line: '[5/8] build' },
    progressLines: ['[5/8] build'],
    logTail: '<script>unsafe</script>\nmap scan',
    logTruncated: false,
    logCharLimit: 32768,
  });
  assert.match(buildHtml, /5 \/ 8/);
  assert.match(buildHtml, /取消构建/);
  assert.match(buildHtml, /build_ark_resource_node_catalog\.py/);
  assert.doesNotMatch(buildHtml, /<script>unsafe<\/script>/);
} finally {
  await server.close();
}

console.log('harvest frontend contract: ok');
