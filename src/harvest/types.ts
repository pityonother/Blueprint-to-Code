export interface HarvestApiResult {
  ok: boolean;
  error?: string;
  code?: string;
}

export interface HarvestDatasetMeta {
  revision?: string;
  generatedAt?: string;
  sourceStatus?: string;
  devkitBuild?: string | null;
  rankingGeneratedAt?: string;
  rankingDatasetRevision?: string;
  evaluationRevision?: string;
  evaluationGeneratedAt?: string;
}

export interface HarvestResourceEntry {
  entryIndex: number;
  resource: string;
  displayName?: string;
  nodeResourceId: string;
  evidenceStatus?: string;
  gaps?: string[];
}

export interface HarvestMapReference {
  id: string;
  name: string;
  objectPath: string;
  mapFamily?: string;
  mapKind?: 'PLAYABLE_MAP_EVIDENCE' | 'AUXILIARY_MAP_EVIDENCE' | string;
  relation?: string;
  evidenceType?: string;
  evidenceStatus?: string;
  usageStatus?: string;
  evidenceCount?: number;
  evidenceExamples?: string[];
}

export interface HarvestNode {
  id: string;
  name: string;
  objectPath: string;
  nodeType?: string;
  assetClass?: string;
  assetOrigin?: {
    packageNamespace?: string;
    meaning?: string;
  };
  mesh?: {
    status?: string;
    name?: string;
    objectPath?: string;
  };
  harvestComponent?: {
    status?: string;
    name?: string;
    packagePath?: string;
    componentObjectPath?: string;
  };
  resources?: {
    status: string;
    count: number | null;
    items: HarvestResourceEntry[];
  };
  mapReferences?: {
    status: string;
    count: number | null;
    items: HarvestMapReference[];
    indirectStatus?: string;
  };
  mapUsage?: {
    status: string;
    claimsCompleteMapUsage?: boolean;
    familyCount?: number;
    evidenceCount?: number;
    families: Array<{
      mapFamily: string;
      displayName?: string;
      mapKind?: string;
      evidenceCount?: number;
      evidenceTypes?: string[];
    }>;
    unindexedEvidenceKinds?: string[];
  };
  image?: {
    status: string;
    url?: string;
    mimeType?: string;
    width?: number;
    height?: number;
    sizeBytes?: number;
    sha256?: string;
    extractionMethod?: string;
    reasonCode?: string;
  };
  componentSourceFreshness?: {
    status: string;
    checked: number;
    stale: string[];
    missing: string[];
  };
  gaps?: string[];
  gapCount?: number;
}

export interface HarvestNodePage extends HarvestApiResult {
  schema: string;
  dataset: HarvestDatasetMeta;
  coverage: {
    discoveryMode?: string;
    candidateDiscovery?: {
      candidatesDiscovered?: number;
      candidatesSelected?: number;
      selectionStrategy?: string;
      backends?: string[];
    };
    nodesDecoded?: number;
    nodesByType?: Record<string, number>;
    nodeCandidates?: number;
    nodeDecodeFailures?: number;
    nonFoliageAssetsSkipped?: number;
    nonResourceFoliageCandidatesSkipped?: number;
    rankingCreatures?: number;
    creatureCandidatesDiscovered?: number;
    creatureAssetsCataloged?: number;
    speciesCataloged?: number;
    attacksDecoded?: number;
    attacksEligibleForScope?: number;
    attacksConditionalForScope?: number;
    attacksIneligibleForScope?: number;
    nodesWithStaleComponentSources?: number;
    nodesWithoutComponentSourceProof?: number;
    claimsAllNodes?: boolean;
    claimsAllCreatures?: boolean;
    mapScan?: {
      status?: string;
      filesScanned?: number;
      indirectReferences?: string;
      claimsCompleteMapUsage?: boolean;
      nodesWithMapUsageEvidence?: number;
      mapFamilies?: string[];
    };
    images?: {
      status?: string;
      available?: number;
      notRecovered?: number;
      uniqueFiles?: number;
      inlineBytes?: boolean;
    };
  };
  total: number;
  offset: number;
  limit: number;
  nextOffset: number | null;
  items: HarvestNode[];
}

export interface HarvestNodeDetail extends HarvestApiResult {
  node: HarvestNode;
}

export interface HarvestRankingRow {
  rank: number;
  creature: string;
  creatureObjectPath?: string;
  speciesKey?: string;
  dinoNameTag?: string;
  variantCount?: number;
  tameabilityStatus?: string;
  tameabilityReasonCodes?: string[];
  rideabilityStatus?: string;
  rideabilityReasonCodes?: string[];
  attackIndex?: number;
  attackName?: string;
  rankingStatus: string;
  reasonCode?: string;
  baseDamage?: number;
  baseAttackInterval?: number;
  riderAttackInterval?: number;
  attackInterval?: number;
  attackIntervalSource?: string;
  damageMultiplier?: number;
  harvestQuantityMultiplier?: number;
  resourceWeightShare?: number;
  engineComparisonIndex: number;
  relativeToNodeTopPercent?: number;
  rankingTier?: 'CONFIRMED' | 'CONDITIONAL' | string;
  missingFacts?: string[];
  warnings?: string[];
  evidenceStatus?: string;
  evidence?: {
    status?: string;
    attack?: string;
    component?: string;
    damageType?: string;
    resource?: string;
    gaps?: string[];
  };
}

export interface HarvestRankingCoverage {
  creaturesRequested?: number;
  creaturesLoaded?: number;
  candidateDiscovery?: {
    backend?: string;
    pattern?: string;
    candidatesDiscovered?: number;
    selectionStrategy?: string;
  };
  creatureCandidatesClassified?: number;
  ancestryConfirmed?: number;
  ancestryByStatus?: Record<string, number>;
  creatureAssetsCataloged?: number;
  speciesCataloged?: number;
  speciesEvaluated?: number;
  rankedSpeciesWithUnknownTameability?: number;
  rankedSpeciesWithUnknownRideability?: number;
  rankedSpeciesConfirmed?: number;
  rankedSpeciesConditional?: number;
  tameabilityByStatus?: Record<string, number>;
  rideabilityByStatus?: Record<string, number>;
  attackCatalogByStatus?: Record<string, number>;
  attacksDecoded?: number;
  attacksComplete?: number;
  attacksEligibleForScope?: number;
  attacksConditionalForScope?: number;
  attacksIneligibleForScope?: number;
  attacksEvaluated?: number;
  attacksRanked?: number;
  attacksUnranked?: number;
  attacksIncompatible?: number;
  attacksExcludedByScope?: number;
  excludedByReason?: Record<string, number>;
  creatureAssetsExcludedFromScope?: number;
  attacksExcludedByCreatureScope?: number;
  excludedCreatureByReason?: Record<string, number>;
  componentCatalogEntries?: number;
  damageTypesDecoded?: number;
  damageTypesWithGaps?: number;
  sourceFingerprintsComplete?: boolean;
  claimsAllCreatures?: boolean;
  claimsGlobalTop?: boolean;
  rankedForNodeResource?: number;
  nonRankedForNodeResource?: number;
  returned?: number;
  omitted?: number;
}

export interface HarvestRankingReasonGroup {
  reasonCode: string;
  count: number;
  examples?: Array<{
    name?: string;
    objectPath?: string;
  }>;
}

export interface HarvestRankingResult extends HarvestApiResult {
  schema: string;
  dataset: HarvestDatasetMeta;
  node: {
    id: string;
    name: string;
    objectPath: string;
  };
  resource: HarvestResourceEntry & {
    harvestComponentPackagePath?: string;
  };
  methodology: {
    metric: string;
    scoreBasis: string;
    relativeBasis?: string;
    warning: string;
    formulaVersion?: string;
    usageScope?: string;
    evaluationMode?: string;
    variantGrouping?: string;
  };
  scopeStatus: string;
  claimScope?: string;
  claimsCompleteWithinScope?: boolean;
  claimsGlobalTop: boolean;
  claimBlockers?: string[];
  coverage: HarvestRankingCoverage;
  exclusions?: {
    catalog?: {
      total?: number;
      byReason?: HarvestRankingReasonGroup[];
    };
    usageScope?: {
      total?: number;
      byReason?: HarvestRankingReasonGroup[];
    };
    queryDisposition?: {
      incompatible?: number;
      unranked?: number;
      byReason?: HarvestRankingReasonGroup[];
    };
  };
  evidence?: {
    status?: string;
    sourceFingerprintsComplete?: boolean;
    node?: string;
    component?: string;
    creatureCatalog?: string;
    damageTypes?: string;
    blockers?: string[];
  };
  items: HarvestRankingRow[];
}

export interface HarvestCreatureSummary {
  speciesKey: string;
  name?: string;
  dinoNameTag?: string;
  variantCount?: number;
  attackCount?: number;
  attackVariantCount?: number;
  tameabilityStatuses?: string[];
  rideabilityStatuses?: string[];
}

export interface HarvestCreaturePage extends HarvestApiResult {
  schema: string;
  dataset: HarvestDatasetMeta;
  coverage: HarvestRankingCoverage;
  total: number;
  offset: number;
  limit: number;
  nextOffset: number | null;
  items: HarvestCreatureSummary[];
}

export interface HarvestCreatureSpecialtyRow extends HarvestRankingRow {
  node: {
    id: string;
    name?: string;
    objectPath?: string;
  };
  resource: HarvestResourceEntry & {
    harvestComponentPackagePath?: string;
  };
  nodeTopEngineComparisonIndex: number;
  relativeToNodeTopPercent: number;
  nodeTop: {
    speciesKey?: string;
    creature?: string;
    creatureObjectPath?: string;
    attackIndex?: number;
    attackName?: string;
    engineComparisonIndex: number;
    rankingTier?: string;
    evidence?: HarvestRankingRow['evidence'];
  };
}

export interface HarvestCreatureSpecialties extends HarvestApiResult {
  schema: string;
  dataset: HarvestDatasetMeta;
  species: HarvestCreatureSummary;
  methodology: HarvestRankingResult['methodology'] & {
    sortMetric?: string;
    relativeBasis?: string;
  };
  scopeStatus: string;
  claimsCompleteWithinScope?: boolean;
  claimsGlobalTop: boolean;
  claimBlockers?: string[];
  evidence?: {
    status?: string;
    blockers?: string[];
  };
  coverage: HarvestRankingCoverage & {
    speciesVariantsMatched?: number;
    nodeResourcePairsDiscovered?: number;
    uniqueEvaluationPairs?: number;
    uniqueEvaluationPairsRanked?: number;
    nodeResourcePairsRanked?: number;
    pairDispositionCounts?: Record<string, number>;
  };
  page?: {
    offset: number;
    limit: number;
    total: number;
    returned: number;
    omitted: number;
  };
  total?: number;
  offset?: number;
  limit?: number;
  nextOffset?: number | null;
  items: HarvestCreatureSpecialtyRow[];
}

export type HarvestBuildStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED';

export interface HarvestBuildJob {
  id: string;
  status: HarvestBuildStatus;
  pid: number | null;
  returnCode: number | null;
  command?: string[];
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  cancelRequested: boolean;
  error: string;
  progress: {
    current: number;
    total: number;
    label: string;
    line: string;
  };
  progressLines: string[];
  logTail: string;
  logTruncated: boolean;
  logCharLimit: number;
}

export interface HarvestBuildResponse extends HarvestApiResult {
  job: HarvestBuildJob | null;
}
