import assert from 'node:assert/strict';
import { createServer } from 'vite';


const server = await createServer({
  logLevel: 'silent',
  server: { middlewareMode: true },
});

try {
  const {
    buildHarvestNodeSearchParams,
    mapEvidenceLabel,
    mapFamilies,
    renderHarvestNodeEmptyState,
    renderHarvestNodeFilterForm,
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
  const metalResourceKey = '/Game/PrimalEarth/CoreBlueprints/Resources/PrimalItemResource_Metal.PrimalItemResource_Metal_C';
  const bioToxinResourceKey = '/Game/PrimalEarth/CoreBlueprints/Items/Consumables/PrimalItemConsumable_JellyVenom.PrimalItemConsumable_JellyVenom_C';
  const aberrationMushroomResourceKey = '/Game/Aberration/CoreBlueprints/Resources/PrimalItemResource_CommonMushroom.PrimalItemResource_CommonMushroom_C';
  const primalEarthMushroomResourceKey = '/Game/PrimalEarth/CoreBlueprints/Resources/PrimalItemResource_CommonMushroom.PrimalItemResource_CommonMushroom_C';
  const filterPage = {
    ok: true,
    schema: 'blueprint-to-code.harvest-node-page/v1',
    dataset: {},
    coverage: {
      mapScan: {
        claimsCompleteMapUsage: false,
        mapFamilies: ['Genesis2', 'TheIsland'],
      },
    },
    total: 37,
    offset: 0,
    limit: 16,
    nextOffset: 16,
    appliedFilters: {
      q: '',
      map: '',
      onlyMapFamily: 'TheIsland',
      resource: metalResourceKey,
    },
    facets: {
      mapExclusivity: {
        definition: 'RECOVERED_PLAYABLE_MAP_FAMILY_SET_EQUALS_SELECTED_FAMILY',
        claimsCompleteMapUsage: false,
        isGlobalExclusivityClaim: false,
      },
      onlyMapFamilies: [
        { mapFamily: 'Genesis2', nodeCount: 245 },
        { mapFamily: 'TheIsland', nodeCount: 131 },
      ],
      resources: [
        {
          resourceKey: metalResourceKey,
          resource: 'PrimalItemResource_Metal_C',
          resourceObjectPath: metalResourceKey,
          displayName: 'Metal',
          nodeCount: 37,
        },
        {
          resourceKey: bioToxinResourceKey,
          resource: 'PrimalItemConsumable_JellyVenom_C',
          resourceObjectPath: bioToxinResourceKey,
          displayName: 'Bio Toxin',
          nodeCount: 3,
        },
        {
          resourceKey: aberrationMushroomResourceKey,
          resource: 'PrimalItemResource_CommonMushroom_C',
          resourceObjectPath: aberrationMushroomResourceKey,
          displayName: 'Aggeravic Mushroom',
          nodeCount: 11,
        },
        {
          resourceKey: primalEarthMushroomResourceKey,
          resource: 'PrimalItemResource_CommonMushroom_C',
          resourceObjectPath: primalEarthMushroomResourceKey,
          displayName: 'Common Mushroom',
          nodeCount: 7,
        },
        {
          resourceKey: '<script>unsafe-key</script>',
          resource: '<script>unsafe-resource</script>',
          displayName: '<img src=x onerror=alert(1)>',
          nodeCount: 1,
        },
      ],
    },
    items: [],
  };
  const filterHtml = renderHarvestNodeFilterForm(filterPage, {
    query: '',
    mapFamily: 'TheIsland',
    mapMode: 'evidenceExclusive',
    resource: metalResourceKey,
  });
  assert.match(filterHtml, /id="harvest-map-filter"/);
  assert.match(filterHtml, /id="harvest-map-mode"/);
  assert.match(filterHtml, /id="harvest-resource-filter"/);
  assert.match(filterHtml, /当前证据仅此地图/);
  assert.match(filterHtml, /地图使用证据尚未声明完整/);
  assert.match(filterHtml, /The Island · 131 个节点/);
  assert.match(filterHtml, /Metal · 37 个节点/);
  assert.match(
    filterHtml,
    /Bio Toxin · 3 个节点 — PrimalItemConsumable_JellyVenom_C/,
  );
  assert.match(filterHtml, /Aggeravic Mushroom · 11 个节点/);
  assert.match(filterHtml, /Common Mushroom · 7 个节点/);
  assert.match(
    filterHtml,
    new RegExp(`value="${aberrationMushroomResourceKey.replaceAll('/', '\\/')}"`),
  );
  assert.match(
    filterHtml,
    new RegExp(`value="${primalEarthMushroomResourceKey.replaceAll('/', '\\/')}"`),
  );
  assert.match(
    filterHtml,
    new RegExp(`value="${metalResourceKey.replaceAll('/', '\\/')}" selected`),
  );
  assert.match(filterHtml, /aria-describedby="harvest-exclusive-map-note"/);
  assert.doesNotMatch(filterHtml, /<script>unsafe-resource<\/script>/);
  assert.doesNotMatch(filterHtml, /<img src=x onerror=alert\(1\)>/);

  const legacyUniqueResourceHtml = renderHarvestNodeFilterForm(filterPage, {
    query: '',
    mapFamily: '',
    mapMode: 'contains',
    resource: 'PrimalItemResource_Metal_C',
  });
  assert.equal(
    (legacyUniqueResourceHtml.match(/>Metal · 37 个节点/g) || []).length,
    1,
  );
  assert.match(
    legacyUniqueResourceHtml,
    new RegExp(`value="${metalResourceKey.replaceAll('/', '\\/')}" selected`),
  );

  const legacyAmbiguousResourceHtml = renderHarvestNodeFilterForm(filterPage, {
    query: '',
    mapFamily: '',
    mapMode: 'contains',
    resource: 'PrimalItemResource_CommonMushroom_C',
  });
  assert.equal(
    (legacyAmbiguousResourceHtml.match(/value="PrimalItemResource_CommonMushroom_C"/g) || []).length,
    1,
  );
  assert.match(legacyAmbiguousResourceHtml, /全部同名蓝图/);
  assert.match(
    legacyAmbiguousResourceHtml,
    /value="PrimalItemResource_CommonMushroom_C" selected/,
  );

  const disabledModeHtml = renderHarvestNodeFilterForm(filterPage, {
    query: '',
    mapFamily: '',
    mapMode: 'contains',
    resource: '',
  });
  assert.match(disabledModeHtml, /id="harvest-map-mode"[^>]*disabled/);

  const lowercaseMapHtml = renderHarvestNodeFilterForm(filterPage, {
    query: '',
    mapFamily: 'theisland',
    mapMode: 'evidenceExclusive',
    resource: '',
  });
  assert.equal(
    (lowercaseMapHtml.match(/<option value="(?:TheIsland|theisland)"/g) || []).length,
    1,
  );
  assert.match(
    lowercaseMapHtml,
    /<option value="TheIsland" selected>The Island · 131 个节点<\/option>/,
  );

  const exclusiveParams = buildHarvestNodeSearchParams({
    query: 'metal',
    mapFamily: 'TheIsland',
    mapMode: 'evidenceExclusive',
    resource: metalResourceKey,
    offset: 16,
    limit: 16,
  });
  assert.equal(exclusiveParams.get('q'), 'metal');
  assert.equal(exclusiveParams.get('onlyMapFamily'), 'TheIsland');
  assert.equal(exclusiveParams.get('map'), null);
  assert.equal(exclusiveParams.get('resource'), metalResourceKey);
  assert.equal(exclusiveParams.get('offset'), '16');

  const containsParams = buildHarvestNodeSearchParams({
    query: '',
    mapFamily: 'Genesis2',
    mapMode: 'contains',
    resource: '',
    offset: 0,
    limit: 16,
  });
  assert.equal(containsParams.get('map'), 'Genesis2');
  assert.equal(containsParams.get('onlyMapFamily'), null);

  const emptyExclusiveParams = buildHarvestNodeSearchParams({
    query: '',
    mapFamily: '',
    mapMode: 'evidenceExclusive',
    resource: '',
    offset: 0,
    limit: 16,
  });
  assert.equal(emptyExclusiveParams.get('map'), null);
  assert.equal(emptyExclusiveParams.get('onlyMapFamily'), null);

  const emptyStateHtml = renderHarvestNodeEmptyState(filterPage, {
    query: '<script>metal</script>',
    mapFamily: 'TheIsland',
    mapMode: 'evidenceExclusive',
    resource: metalResourceKey,
  });
  assert.match(emptyStateHtml, /当前证据仅属于 The Island/);
  assert.match(emptyStateHtml, /包含 Metal/);
  assert.match(emptyStateHtml, /data-harvest-action="clear-filters"/);
  assert.doesNotMatch(emptyStateHtml, /<script>metal<\/script>/);

  const absentResourceEmptyState = renderHarvestNodeEmptyState(
    { ...filterPage, facets: { ...filterPage.facets, resources: [] } },
    {
      query: '',
      mapFamily: 'TheCenter',
      mapMode: 'evidenceExclusive',
      resource: 'PrimalItemResource_Metal_C',
    },
  );
  assert.match(absentResourceEmptyState, /包含 Metal/);
  assert.doesNotMatch(absentResourceEmptyState, /包含 PrimalItemResource_Metal_C/);
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
  assert.match(html, /旧版比较指数排行（非产量）/);
  assert.match(html, /旧版响应：以下数值仅为旧版比较指数，不代表完整节点产量/);
  assert.match(html, /相对旧版指数榜首 100%/);
  assert.doesNotMatch(html, /完整节点预计产量排行/);
  assert.match(html, /证据与口径/);
  assert.doesNotMatch(html, /<img src=x onerror=alert\(1\)>/);
  assert.doesNotMatch(html, /<script>unsafe-reason<\/script>/);

  const partialEvidenceHtml = renderHarvestRankingResult({
    ...v2,
    evidence: { status: 'PARTIAL', blockers: ['DAMAGE_TYPE_GAP'] },
  });
  assert.match(partialEvidenceHtml, /证据部分缺失/);
  assert.match(partialEvidenceHtml, /DAMAGE_TYPE_GAP/);

  const v3 = {
    ...v2,
    schema: 'blueprint-to-code.harvest-ranking-result/v3',
    methodology: {
      ...v2.methodology,
      metric: 'estimatedYieldPerNode',
      scoreBasis: 'ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE',
      formulaVersion: 'harvest-estimated-yield-per-node/v1',
      warning: '完整节点预计产量仍是游戏数据估算。',
    },
    items: [
      {
        ...v2.items[0],
        estimatedYieldPerNode: 24.5,
        engineComparisonIndex: 987654,
      },
    ],
  };
  const yieldHtml = renderHarvestRankingResult(v3);
  assert.match(yieldHtml, /Metal：完整节点预计产量排行/);
  assert.match(yieldHtml, /一整个完整节点的预计产量/);
  assert.match(yieldHtml, /预计产量\/完整节点/);
  assert.match(yieldHtml, /相对本节点最高预计产量 100%/);
  assert.match(yieldHtml, />24\.5</);
  assert.doesNotMatch(yieldHtml, /987,654/);
  assert.doesNotMatch(yieldHtml, /旧版比较指数/);

  const bioToxinHtml = renderHarvestRankingResult({
    ...v3,
    resource: {
      ...v3.resource,
      resource: 'PrimalItemConsumable_JellyVenom_C',
      displayName: 'Bio Toxin',
    },
  });
  assert.match(bioToxinHtml, /Bio Toxin：完整节点预计产量排行/);
  assert.doesNotMatch(bioToxinHtml, /PrimalItemConsumable Jelly Venom/);

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

  const specialtyV2 = {
    ok: true,
    schema: 'blueprint-to-code.harvest-creature-specialties/v2',
    dataset: { evaluationRevision: 'e'.repeat(64) },
    species: { speciesKey: 'anky', name: 'Ankylosaurus', dinoNameTag: 'Anky', variantCount: 3 },
    methodology: {
      metric: 'estimatedYieldPerNode',
      sortMetric: 'estimatedYieldPerNode',
      scoreBasis: 'ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE',
      warning: '完整节点预计产量仍是游戏数据估算。',
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
        estimatedYieldPerNode: 42.5,
        engineComparisonIndex: 999.5,
        relativeToNodeTopPercent: 97.25,
        nodeTopEstimatedYieldPerNode: 43.7,
        nodeTopEngineComparisonIndex: 999.7,
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
          estimatedYieldPerNode: 43.7,
          engineComparisonIndex: 999.7,
        },
        evidence: { status: 'COMPLETE', gaps: [] },
      },
    ],
  };
  const specialtyHtml = renderHarvestCreatureSpecialties(specialtyV2);
  assert.match(specialtyHtml, /97.25%/);
  assert.match(specialtyHtml, /42.5/);
  assert.match(specialtyHtml, /本龙预计产量/);
  assert.match(specialtyHtml, /节点最高预计产量/);
  assert.match(specialtyHtml, /每完整节点预计产量/);
  assert.match(specialtyHtml, /按该恐龙的每完整节点预计产量从高到低排列/);
  assert.match(specialtyHtml, /相对节点榜首/);
  assert.match(specialtyHtml, /Doedicurus/);
  assert.match(specialtyHtml, /650/);
  assert.match(specialtyHtml, /不是受控实测/);
  assert.doesNotMatch(specialtyHtml, /999\.5/);
  assert.doesNotMatch(specialtyHtml, /999\.7/);
  assert.doesNotMatch(specialtyHtml, /旧版比较指数/);
  assert.doesNotMatch(specialtyHtml, /<script>bad<\/script>/);

  const legacySpecialtyHtml = renderHarvestCreatureSpecialties({
    ...specialtyV2,
    schema: 'blueprint-to-code.harvest-creature-specialties/v1',
    methodology: {
      ...specialtyV2.methodology,
      metric: 'engineComparisonIndex',
      scoreBasis: 'INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD',
      warning: '不是实测掉落量。',
    },
    items: [
      {
        ...specialtyV2.items[0],
        estimatedYieldPerNode: undefined,
        engineComparisonIndex: 42.5,
        nodeTopEstimatedYieldPerNode: undefined,
        nodeTopEngineComparisonIndex: 43.7,
        nodeTop: {
          ...specialtyV2.items[0].nodeTop,
          estimatedYieldPerNode: undefined,
          engineComparisonIndex: 43.7,
        },
      },
    ],
  });
  assert.match(legacySpecialtyHtml, /旧版响应：以下数值仅为旧版比较指数/);
  assert.match(legacySpecialtyHtml, /本龙旧版比较指数 42.5/);
  assert.match(legacySpecialtyHtml, /节点旧版榜首指数 43.7/);
  assert.doesNotMatch(legacySpecialtyHtml, /本龙预计产量/);

  const rankingContractV2 = {
    ok: true,
    schema: 'blueprint-to-code.harvest-ranking-result/v4',
    contractVersion: 'harvest-ranking-contract/v2',
    identity: {
      extractorVersion: 'extractor/v3',
      modelVersion: 'model/v2',
      policyVersion: 'policy/v2',
      resultSchemaVersion: 'blueprint-to-code.harvest-ranking-result/v4',
      nodeCatalogRevision: '1'.repeat(64),
      evaluationCatalogRevision: '2'.repeat(64),
      componentCatalogRevision: '3'.repeat(64),
    },
    dataset: {},
    node: { id: 'node', name: 'Node', objectPath: '/Game/Node' },
    resource: {
      entryIndex: 0,
      resource: 'PrimalItemResource_Metal_C',
      displayName: 'Metal',
      nodeResourceId: 'resource',
    },
    queryPolicy: {
      evidence: 'includeConditional',
      variant: 'CANONICAL_VARIANT',
      metric: 'staticCompleteNodeTargetYield',
      availability: 'GLOBAL_TRANSFER_ALLOWED',
    },
    methodology: {
      metric: 'staticCompleteNodeTargetYield',
      scoreBasis: 'STATIC_COMPLETE_NODE_TARGET_RESOURCE_UNITS',
      warning: '静态指标不是实测。',
      firstHitTiming: 'FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE',
    },
    confirmedStatus: 'AVAILABLE',
    conditionalStatus: 'AVAILABLE',
    scopeStatus: 'PARTIAL',
    claimsGlobalTop: false,
    coverage: {},
    runtimeCoverage: { publishableExactRows: 0 },
    confirmedItems: [{
      rank: 1,
      creature: 'Confirmed creature',
      creatureObjectPath: '/Game/Dinos/Confirmed',
      speciesKey: 'confirmed',
      attackName: 'Hit',
      attackInterval: 1,
      attackIntervalSource: 'GENERAL_ATTACK_INTERVAL',
      rankingStatus: 'RANKED',
      rankingTier: 'CONFIRMED',
      staticCompleteNodeTargetYield: 20,
      estimatedYieldPerNode: 20,
      relativeToNodeTopPercent: 100,
      runtimeStatus: 'NOT_MEASURED',
      evidence: { status: 'CONFIRMED', gaps: [] },
      variantSelection: {
        policy: 'CANONICAL_VARIANT',
        selectedObjectPath: '/Game/Dinos/Confirmed',
        higherExploratoryVariantExists: true,
      },
      scoreBreakdown: {
        omittedFactors: ['MAP_AVAILABILITY', 'EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED'],
      },
    }],
    conditionalItems: [{
      rank: 1,
      creature: '<script>Conditional high</script>',
      creatureObjectPath: '/Game/Dinos/Conditional',
      speciesKey: 'conditional',
      attackName: 'Conditional hit',
      attackInterval: 0.5,
      rankingStatus: 'RANKED',
      rankingTier: 'CONDITIONAL',
      staticCompleteNodeTargetYield: 2000,
      estimatedYieldPerNode: 2000,
      relativeToNodeTopPercent: 100,
      evidence: { status: 'PARTIAL', gaps: ['BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED'] },
      variantSelection: {
        policy: 'CANONICAL_VARIANT',
        selectedObjectPath: '/Game/Dinos/Conditional',
      },
    }],
    items: [],
  };
  rankingContractV2.items = [
    ...rankingContractV2.confirmedItems,
    ...rankingContractV2.conditionalItems,
  ];
  const rankingV2Html = renderHarvestRankingResult(rankingContractV2);
  assert.match(rankingV2Html, /已确认榜（独立编号）/);
  assert.match(rankingV2Html, /条件性估算（不占已确认名次）/);
  assert.match(rankingV2Html, /静态单节点目标资源总产量/);
  assert.match(rankingV2Html, /规范榜未自动取最大/);
  assert.match(rankingV2Html, /未实测/);
  assert.match(rankingV2Html, /GLOBAL_TRANSFER_ALLOWED/);
  assert.match(rankingV2Html, /EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED/);
  assert.doesNotMatch(rankingV2Html, /<script>Conditional high<\/script>/);

  const rankingMetricContracts = [
    {
      metric: 'staticCompleteNodeTargetYield',
      scoreBasis: 'STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE',
      unit: 'target_resource_units/node',
      value: 41.25,
      displayValue: '41.25',
    },
    {
      metric: 'staticYieldPerAttackCycleSecond',
      scoreBasis: 'STATIC_TARGET_RESOURCE_UNITS_PER_ATTACK_CYCLE_SECOND',
      unit: 'target_resource_units/attack_cycle_second',
      value: 52.5,
      displayValue: '52.5',
    },
    {
      metric: 'observedYieldPerNode',
      scoreBasis: 'OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE',
      unit: 'target_resource_units/node',
      value: 63.75,
      displayValue: '63.75',
    },
    {
      metric: 'observedYieldPerSecond',
      scoreBasis: 'OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND',
      unit: 'target_resource_units/second',
      value: 74.125,
      displayValue: '74.13',
    },
  ];
  for (const contract of rankingMetricContracts) {
    const metricRow = {
      ...rankingContractV2.confirmedItems[0],
      selectedMetric: contract.metric,
      selectedMetricValue: contract.value,
      [contract.metric]: contract.value,
    };
    const metricHtml = renderHarvestRankingResult({
      ...rankingContractV2,
      queryPolicy: {
        ...rankingContractV2.queryPolicy,
        // Methodology is authoritative even if a stale compatibility field disagrees.
        metric: 'staticCompleteNodeTargetYield',
      },
      methodology: {
        ...rankingContractV2.methodology,
        metric: contract.metric,
        scoreBasis: contract.scoreBasis,
        unit: contract.unit,
      },
      confirmedItems: [metricRow],
      conditionalItems: [],
      items: [metricRow],
    });
    assert.ok(
      metricHtml.includes(contract.scoreBasis),
      `${contract.metric} must preserve methodology.scoreBasis`,
    );
    assert.ok(
      metricHtml.includes(contract.unit),
      `${contract.metric} must preserve methodology.unit`,
    );
    assert.ok(
      metricHtml.includes(`>${contract.displayValue}<`),
      `${contract.metric} must render the methodology-selected metric value`,
    );
  }

  const observedProfileHtml = renderHarvestRankingResult({
    ...rankingContractV2,
    queryPolicy: {
      ...rankingContractV2.queryPolicy,
      metric: 'observedYieldPerNode',
      runtimeProfileId: 'server-pve-1',
      includePreliminary: true,
    },
    methodology: {
      ...rankingContractV2.methodology,
      metric: 'observedYieldPerNode',
      scoreBasis: 'OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE',
      unit: 'target_resource_units/node',
    },
    runtimeCoverage: {
      runtimeProfilesAvailable: ['server-pve-1', 'single-player-1'],
      runtimeProfileSelected: 'server-pve-1',
      publishableConfirmedRows: 0,
      preliminaryRows: 1,
      syntheticExcluded: 0,
      profileMismatchExcluded: 3,
    },
    confirmedItems: [],
    conditionalItems: [{
      ...rankingContractV2.confirmedItems[0],
      observedYieldPerNode: 9,
      selectedMetric: 'observedYieldPerNode',
      selectedMetricValue: 9,
      runtimeStatus: 'OBSERVED_PRELIMINARY',
      runtimeObservation: {
        runtimeProfileId: 'server-pve-1',
        evidenceTier: 'OBSERVED_PRELIMINARY',
      },
    }],
    items: [],
  });
  assert.ok(observedProfileHtml.includes('runtimeProfileId'));
  assert.ok(observedProfileHtml.includes('server-pve-1'));
  assert.ok(observedProfileHtml.includes('includePreliminary=true'));
  assert.ok(observedProfileHtml.includes('OBSERVED_PRELIMINARY'));
  assert.ok(observedProfileHtml.includes('id="harvest-ranking-runtime-profile"'));
  assert.ok(observedProfileHtml.includes('id="harvest-ranking-include-preliminary"'));
  assert.match(
    observedProfileHtml,
    /id="harvest-ranking-include-preliminary" type="checkbox" checked/,
  );

  const unselectedRuntimeProfileHtml = renderHarvestRankingResult({
    ...rankingContractV2,
    runtimeCoverage: {
      runtimeProfilesAvailable: ['server-pve-1', 'single-player-1'],
      runtimeProfileSelected: null,
      publishableConfirmedRows: 2,
      preliminaryRows: 0,
    },
  });
  assert.ok(unselectedRuntimeProfileHtml.includes('请选择一个环境'));
  assert.ok(unselectedRuntimeProfileHtml.includes('受控实测单节点（先选环境）'));

  const compatibilityConfirmed = {
    ...rankingContractV2.confirmedItems[0],
    creature: 'Confirmed authoritative first',
  };
  const compatibilityConditional = {
    ...rankingContractV2.conditionalItems[0],
    creature: 'Compatibility conditional first',
  };
  const splitTierHtml = renderHarvestRankingResult({
    ...rankingContractV2,
    confirmedItems: [compatibilityConfirmed],
    conditionalItems: [compatibilityConditional],
    // Deliberately adversarial compatibility order: it must not drive either tier.
    items: [compatibilityConditional, compatibilityConfirmed],
  });
  const confirmedTierIndex = splitTierHtml.indexOf('harvest-ranking-tier confirmed');
  const conditionalTierIndex = splitTierHtml.indexOf('harvest-ranking-tier conditional');
  const confirmedCreatureIndex = splitTierHtml.indexOf('Confirmed authoritative first');
  const conditionalCreatureIndex = splitTierHtml.indexOf('Compatibility conditional first');
  assert.ok(confirmedTierIndex >= 0);
  assert.ok(conditionalTierIndex > confirmedTierIndex);
  assert.ok(confirmedCreatureIndex > confirmedTierIndex);
  assert.ok(confirmedCreatureIndex < conditionalTierIndex);
  assert.ok(conditionalCreatureIndex > conditionalTierIndex);

  const conditionalOnlyHtml = renderHarvestRankingResult({
    ...rankingContractV2,
    confirmedItems: [],
    conditionalItems: [compatibilityConditional],
    items: [compatibilityConditional],
  });
  assert.equal(
    (conditionalOnlyHtml.match(/harvest-ranking-tier confirmed/g) || []).length,
    1,
  );
  assert.equal(
    (conditionalOnlyHtml.match(/harvest-ranking-tier conditional/g) || []).length,
    1,
  );

  const specialtyContractV2 = {
    ...specialtyV2,
    schema: 'blueprint-to-code.harvest-creature-specialties/v3',
    contractVersion: 'harvest-ranking-contract/v2',
    identity: rankingContractV2.identity,
    queryPolicy: rankingContractV2.queryPolicy,
    confirmedStatus: 'AVAILABLE',
    conditionalStatus: 'UNAVAILABLE',
    methodology: {
      ...specialtyV2.methodology,
      metric: 'staticCompleteNodeTargetYield',
      sortMetric: 'relativeToNodeTopPercent DESC, selectedMetricValue DESC, resourceDisplayName, nodeName, nodeId',
    },
    confirmedItems: [{
      ...specialtyV2.items[0],
      selectedMetricValue: 10,
      nodeTopSelectedMetricValue: 10,
      relativeToNodeTopPercent: 100,
      staticCompleteNodeTargetYield: 10,
      variantSelection: { selectedObjectPath: '/Game/Dinos/Anky' },
    }],
    conditionalItems: [],
  };
  specialtyContractV2.items = specialtyContractV2.confirmedItems;
  const specialtyContractHtml = renderHarvestCreatureSpecialties(specialtyContractV2);
  assert.match(specialtyContractHtml, /RELATIVE-FIRST/);
  assert.match(specialtyContractHtml, /相对同层节点榜首 100%/);
  assert.match(specialtyContractHtml, /界面不重新排序/);
  assert.match(specialtyContractHtml, /已确认专长（独立编号）/);
  assert.match(specialtyContractHtml, /条件性专长（不占已确认名次）/);

  for (const contract of rankingMetricContracts) {
    const specialtyMetricRow = {
      ...specialtyContractV2.confirmedItems[0],
      selectedMetric: contract.metric,
      selectedMetricValue: contract.value,
      nodeTopSelectedMetricValue: contract.value,
      [contract.metric]: contract.value,
    };
    const specialtyMetricHtml = renderHarvestCreatureSpecialties({
      ...specialtyContractV2,
      queryPolicy: {
        ...specialtyContractV2.queryPolicy,
        metric: 'staticCompleteNodeTargetYield',
      },
      methodology: {
        ...specialtyContractV2.methodology,
        metric: contract.metric,
        scoreBasis: contract.scoreBasis,
        unit: contract.unit,
      },
      confirmedItems: [specialtyMetricRow],
      conditionalItems: [],
      items: [specialtyMetricRow],
    });
    assert.ok(
      specialtyMetricHtml.includes(contract.scoreBasis),
      `reverse ${contract.metric} must preserve methodology.scoreBasis`,
    );
    assert.ok(
      specialtyMetricHtml.includes(contract.unit),
      `reverse ${contract.metric} must preserve methodology.unit`,
    );
    assert.ok(
      specialtyMetricHtml.includes(`value="${contract.metric}" selected`),
      `reverse ${contract.metric} must treat methodology.metric as authoritative`,
    );
    assert.ok(
      specialtyMetricHtml.includes(contract.displayValue),
      `reverse ${contract.metric} must render the selected metric value`,
    );
  }

  const observedSpecialtyProfileHtml = renderHarvestCreatureSpecialties({
    ...specialtyContractV2,
    queryPolicy: {
      ...specialtyContractV2.queryPolicy,
      runtimeProfileId: 'server-pve-1',
      includePreliminary: true,
    },
    methodology: {
      ...specialtyContractV2.methodology,
      metric: 'observedYieldPerSecond',
      scoreBasis: 'OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND',
      unit: 'target_resource_units/second',
      runtime: true,
    },
    runtimeCoverage: {
      runtimeProfilesAvailable: ['server-pve-1', 'single-player-1'],
      runtimeProfileSelected: 'server-pve-1',
      publishableConfirmedRows: 0,
      preliminaryRows: 1,
    },
    confirmedItems: [],
    conditionalItems: [{
      ...specialtyContractV2.confirmedItems[0],
      runtimeStatus: 'OBSERVED_PRELIMINARY',
      selectedMetric: 'observedYieldPerSecond',
      selectedMetricValue: 3,
      nodeTopSelectedMetricValue: 3,
    }],
    items: [],
  });
  assert.ok(observedSpecialtyProfileHtml.includes('runtimeProfilesAvailable'));
  assert.ok(observedSpecialtyProfileHtml.includes('server-pve-1'));
  assert.ok(observedSpecialtyProfileHtml.includes('includePreliminary=true'));
  assert.ok(observedSpecialtyProfileHtml.includes('OBSERVED_PRELIMINARY'));
  assert.ok(observedSpecialtyProfileHtml.includes('id="harvest-specialty-runtime-profile"'));
  assert.ok(observedSpecialtyProfileHtml.includes('id="harvest-specialty-include-preliminary"'));
  assert.match(
    observedSpecialtyProfileHtml,
    /id="harvest-specialty-include-preliminary" type="checkbox" checked/,
  );

  const unselectedSpecialtyProfileHtml = renderHarvestCreatureSpecialties({
    ...specialtyContractV2,
    runtimeCoverage: {
      runtimeProfilesAvailable: ['server-pve-1', 'single-player-1'],
      runtimeProfileSelected: null,
      publishableConfirmedRows: 2,
      preliminaryRows: 0,
    },
  });
  assert.ok(unselectedSpecialtyProfileHtml.includes('请选择一个环境'));
  assert.ok(unselectedSpecialtyProfileHtml.includes('受控实测单节点（先选环境）'));

  const reverseConfirmed = {
    ...specialtyContractV2.confirmedItems[0],
    resource: {
      ...specialtyContractV2.confirmedItems[0].resource,
      displayName: 'Confirmed specialty authoritative',
    },
  };
  const reverseConditional = {
    ...specialtyContractV2.confirmedItems[0],
    rankingTier: 'CONDITIONAL',
    resource: {
      ...specialtyContractV2.confirmedItems[0].resource,
      displayName: 'Compatibility conditional specialty first',
    },
  };
  const reverseSplitHtml = renderHarvestCreatureSpecialties({
    ...specialtyContractV2,
    confirmedItems: [reverseConfirmed],
    conditionalItems: [reverseConditional],
    items: [reverseConditional, reverseConfirmed],
  });
  const reverseConfirmedTierIndex = reverseSplitHtml.indexOf('harvest-ranking-tier confirmed');
  const reverseConditionalTierIndex = reverseSplitHtml.indexOf('harvest-ranking-tier conditional');
  const reverseConfirmedRowIndex = reverseSplitHtml.indexOf('Confirmed specialty authoritative');
  const reverseConditionalRowIndex = reverseSplitHtml.indexOf('Compatibility conditional specialty first');
  assert.ok(reverseConfirmedTierIndex >= 0);
  assert.ok(reverseConditionalTierIndex > reverseConfirmedTierIndex);
  assert.ok(reverseConfirmedRowIndex > reverseConfirmedTierIndex);
  assert.ok(reverseConfirmedRowIndex < reverseConditionalTierIndex);
  assert.ok(reverseConditionalRowIndex > reverseConditionalTierIndex);

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
