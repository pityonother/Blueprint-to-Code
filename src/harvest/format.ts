import type {
  HarvestNode,
  HarvestRankingResult,
  HarvestResourceEntry,
  HarvestResourceTypeFacet,
} from './types';

export function mapFamily(objectPath: string): string {
  const parts = objectPath.split('/').filter(Boolean);
  if (parts[0] === 'Game' && parts[1] === 'Maps' && parts[2]) {
    return parts[2];
  }
  return parts[1] || parts[0] || '未知地图';
}


export function displayMapFamily(value: string): string {
  const labels: Record<string, string> = {
    Genesis2: 'Genesis 2',
    ScorchedEarth: 'Scorched Earth',
    TheCenter: 'The Center',
    TheIsland: 'The Island',
    TestMaps: '测试地图',
  };
  const canonical = Object.keys(labels).find(
    (candidate) => candidate.toLowerCase() === value.toLowerCase(),
  );
  return (canonical ? labels[canonical] : '') || value.replaceAll('_', ' ');
}


export function isAuxiliaryMap(reference: { objectPath: string; mapKind?: string }): boolean {
  if (reference.mapKind) {
    return reference.mapKind !== 'PLAYABLE_MAP_EVIDENCE';
  }
  return /\/(TestMaps|Test|Art_Tools)\//i.test(reference.objectPath);
}


export function mapFamilies(node: HarvestNode): string[] {
  const summarized = (node.mapUsage?.families || [])
    .map((item) => item.mapFamily)
    .filter(Boolean);
  if (summarized.length) {
    return Array.from(new Set(summarized)).sort((left, right) => left.localeCompare(right));
  }
  return Array.from(
    new Set(
      (node.mapReferences?.items || [])
        .filter((item) => !isAuxiliaryMap(item))
        .map((item) => item.mapFamily || mapFamily(item.objectPath)),
    ),
  ).sort((left, right) => left.localeCompare(right));
}


export function mapEvidenceLabel(reference: { relation?: string; evidenceType?: string }): string {
  const kind = reference.evidenceType || reference.relation || '';
  const labels: Record<string, string> = {
    UMAP_DIRECT_PACKAGE_REFERENCE: '地图包直接引用',
    DIRECT_PACKAGE_REFERENCE: '地图包直接引用',
    WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE: 'World Partition 外部 Actor',
    PCG_BIOME_REFERENCE: 'PCG 生物群系依赖',
  };
  return labels[kind] || kind || '未分类证据';
}


export function formatScore(value: number | undefined): string {
  return typeof value === 'number'
    ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
    : '—';
}


export function hasEstimatedYieldMetric(ranking: HarvestRankingResult): boolean {
  return ranking.methodology.metric === 'estimatedYieldPerNode'
    || ranking.items.some(
      (row) => typeof row.estimatedYieldPerNode === 'number'
        && Number.isFinite(row.estimatedYieldPerNode),
    );
}


export function resourceClassDisplayName(resource: string): string {
  return resource
    .replace(/^PrimalItemResource_/, '')
    .replace(/_C$/, '')
    .replaceAll('_', ' ');
}


export function resourceClassFromFilter(value: string): string {
  const normalized = value.trim().replace(/^BlueprintGeneratedClass'/, '').replace(/'$/, '');
  return normalized.split('.').at(-1) || normalized.split('/').at(-1) || normalized;
}


export function resourceFilterDisplayName(value: string): string {
  return resourceClassDisplayName(resourceClassFromFilter(value));
}


export function resourceFacetKey(resource: HarvestResourceTypeFacet): string {
  return resource.resourceKey || resource.resourceObjectPath || resource.resource;
}


export function sameResourceIdentity(left: string, right: string): boolean {
  return left.trim().toLowerCase() === right.trim().toLowerCase();
}


export function resourceName(resource: HarvestResourceEntry): string {
  return resource.displayName || resourceClassDisplayName(resource.resource);
}


export function harvestImageUrl(node: HarvestNode): string {
  const value = node.image?.status === 'AVAILABLE' ? node.image.url || '' : '';
  return /^\/api\/harvest\/images\/[0-9a-f]{64}\.jpg$/.test(value) ? value : '';
}


export function imageDimension(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 1 && value <= 4096
    ? Math.round(value)
    : 256;
}


export function countValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.round(value)
    : null;
}


export function firstCount(...values: unknown[]): number | null {
  for (const value of values) {
    const count = countValue(value);
    if (count !== null) {
      return count;
    }
  }
  return null;
}


export function sumCounts(...values: unknown[]): number | null {
  const counts = values.map(countValue).filter((value): value is number => value !== null);
  return counts.length ? counts.reduce((total, value) => total + value, 0) : null;
}


export function formatCount(value: number | null): string {
  return value === null ? '—' : new Intl.NumberFormat('zh-CN').format(value);
}


export function formatSeconds(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    return '未恢复';
  }
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value)} 秒`;
}
