import { escapeHtml } from '../shared/html';
import type {
  HarvestMapFilterMode,
  HarvestNodePage,
  HarvestResourceTypeFacet,
} from './types';
import {
  displayMapFamily,
  formatCount,
  resourceClassFromFilter,
  resourceFacetKey,
  resourceFilterDisplayName,
  sameResourceIdentity,
} from './format';

export interface HarvestNodeFilterState {
  query: string;
  mapFamily: string;
  mapMode: HarvestMapFilterMode;
  resource: string;
}

export interface HarvestNodeSearchState extends HarvestNodeFilterState {
  offset: number;
  limit: number;
}


export function buildHarvestNodeSearchParams(
  state: HarvestNodeSearchState,
): URLSearchParams {
  const params = new URLSearchParams();
  if (state.query) {
    params.set('q', state.query);
  }
  if (state.mapFamily) {
    if (state.mapMode === 'evidenceExclusive') {
      params.set('onlyMapFamily', state.mapFamily);
    } else {
      params.set('map', state.mapFamily);
    }
  }
  if (state.resource) {
    params.set('resource', state.resource);
  }
  params.set('offset', String(Math.max(0, state.offset)));
  params.set('limit', String(Math.max(1, state.limit)));
  return params;
}


export function renderHarvestNodeFilterForm(
  page: HarvestNodePage | null,
  state: HarvestNodeFilterState,
): string {
  const exclusiveCounts = new Map(
    (page?.facets?.onlyMapFamilies || []).map((item) => [
      item.mapFamily.toLowerCase(),
      item.nodeCount,
    ]),
  );
  const mapOptionsByKey = new Map<string, string>();
  [
    ...(page?.coverage.mapScan?.mapFamilies || []),
    ...(page?.facets?.onlyMapFamilies || []).map((item) => item.mapFamily),
    ...(state.mapFamily ? [state.mapFamily] : []),
  ].forEach((family) => {
    const key = family.toLowerCase();
    if (!mapOptionsByKey.has(key)) {
      mapOptionsByKey.set(key, family);
    }
  });
  const mapOptions = Array.from(mapOptionsByKey.values()).sort(
    (left, right) => left.localeCompare(right),
  );
  const selectedMapKey = state.mapFamily.toLowerCase();
  const resourceOptionsByKey = new Map<string, HarvestResourceTypeFacet>();
  (page?.facets?.resources || []).forEach((item) => {
    const key = resourceFacetKey(item).trim();
    if (key && !resourceOptionsByKey.has(key.toLowerCase())) {
      resourceOptionsByKey.set(key.toLowerCase(), item);
    }
  });
  const resourceOptions = Array.from(resourceOptionsByKey.values());
  const exactSelectedResource = state.resource
    ? resourceOptions.find((item) => sameResourceIdentity(resourceFacetKey(item), state.resource))
    : undefined;
  const legacyClassMatches = state.resource && !exactSelectedResource
    ? resourceOptions.filter((item) => sameResourceIdentity(item.resource, state.resource))
    : [];
  let selectedResourceKey = exactSelectedResource
    ? resourceFacetKey(exactSelectedResource)
    : state.resource;
  if (state.resource && !exactSelectedResource && legacyClassMatches.length === 1) {
    selectedResourceKey = resourceFacetKey(legacyClassMatches[0]);
  } else if (state.resource && !exactSelectedResource && legacyClassMatches.length > 1) {
    resourceOptions.push({
      resourceKey: state.resource,
      resource: state.resource,
      displayName: `${resourceFilterDisplayName(state.resource)}（全部同名蓝图）`,
      nodeCount: page?.total ?? 0,
    });
  } else if (state.resource && !exactSelectedResource && legacyClassMatches.length === 0) {
    resourceOptions.push({
      resourceKey: state.resource,
      resource: resourceClassFromFilter(state.resource),
      displayName: resourceFilterDisplayName(state.resource),
      nodeCount: 0,
    });
  }
  resourceOptions.sort((left, right) => (
    (left.displayName || left.resource).localeCompare(right.displayName || right.resource)
    || resourceFacetKey(left).localeCompare(resourceFacetKey(right))
  ));
  const mapModeDisabled = !state.mapFamily;
  const exclusiveSelected = state.mapMode === 'evidenceExclusive' && !mapModeDisabled;
  const evidenceNote = mapModeDisabled
    ? '请先选择地图，再选择匹配方式。'
    : exclusiveSelected
      ? '“当前证据仅此地图”表示已恢复的正式可玩地图家族只有所选地图；地图使用证据尚未声明完整时，不代表该节点在全游戏中绝对不会出现在其他地图。测试和工具地图不参与该筛选。'
      : '“包含所选地图”会保留同时在其他地图出现的节点。';
  return `
    <form class="harvest-search panel" data-harvest-search>
      <label for="harvest-query">搜索与筛选资源节点</label>
      <div class="harvest-search-row">
        <input id="harvest-query" name="q" value="${escapeHtml(state.query)}" placeholder="例如 MetalRock、UmbrellaTree" autocomplete="off">
        <button class="button primary" type="submit">搜索</button>
      </div>
      <div class="harvest-filter-grid">
        <label for="harvest-map-filter">地图家族
          <select id="harvest-map-filter" name="mapFilter">
            <option value="">全部地图证据</option>
            ${mapOptions.map((family) => {
              const exclusiveCount = exclusiveCounts.get(family.toLowerCase());
              const countLabel = state.mapMode === 'evidenceExclusive' && exclusiveCount !== undefined
                ? ` · ${formatCount(exclusiveCount)} 个节点`
                : '';
              return `<option value="${escapeHtml(family)}" ${family.toLowerCase() === selectedMapKey ? 'selected' : ''}>${escapeHtml(displayMapFamily(family))}${escapeHtml(countLabel)}</option>`;
            }).join('')}
          </select>
        </label>
        <label for="harvest-map-mode">地图匹配方式
          <select id="harvest-map-mode" name="mapMode" aria-describedby="harvest-exclusive-map-note" ${mapModeDisabled ? 'disabled' : ''}>
            <option value="contains" ${!exclusiveSelected ? 'selected' : ''}>包含所选地图</option>
            <option value="evidenceExclusive" ${exclusiveSelected ? 'selected' : ''}>当前证据仅此地图</option>
          </select>
        </label>
        <label for="harvest-resource-filter">包含资源类型
          <select id="harvest-resource-filter" name="resourceFilter">
            <option value="">全部资源类型</option>
            ${resourceOptions.map((item) => {
              const key = resourceFacetKey(item);
              return `<option value="${escapeHtml(key)}" ${sameResourceIdentity(key, selectedResourceKey) ? 'selected' : ''}>${escapeHtml(item.displayName || item.resource)} · ${escapeHtml(formatCount(item.nodeCount))} 个节点 — ${escapeHtml(item.resource)}</option>`;
            }).join('')}
          </select>
        </label>
      </div>
      <p id="harvest-exclusive-map-note" class="harvest-filter-note ${exclusiveSelected ? 'warn' : ''}">${escapeHtml(evidenceNote)}</p>
    </form>
  `;
}


export function renderHarvestNodeEmptyState(
  page: HarvestNodePage | null,
  state: HarvestNodeFilterState,
): string {
  const conditions: string[] = [];
  if (state.query) {
    conditions.push(`搜索“${state.query}”`);
  }
  if (state.mapFamily) {
    const mapName = displayMapFamily(state.mapFamily);
    conditions.push(
      state.mapMode === 'evidenceExclusive'
        ? `当前证据仅属于 ${mapName}`
        : `地图证据包含 ${mapName}`,
    );
  }
  if (state.resource) {
    const resourceFacets = page?.facets?.resources || [];
    const exactResourceFacet = resourceFacets.find(
      (item) => sameResourceIdentity(resourceFacetKey(item), state.resource),
    );
    const legacyResourceFacets = exactResourceFacet
      ? []
      : resourceFacets.filter((item) => sameResourceIdentity(item.resource, state.resource));
    const resourceFacet = exactResourceFacet
      || (legacyResourceFacets.length === 1 ? legacyResourceFacets[0] : undefined);
    conditions.push(
      `包含 ${resourceFacet?.displayName || resourceFilterDisplayName(state.resource)}`,
    );
  }
  if (!conditions.length) {
    return '<div class="empty-state">当前数据库中没有可显示的资源节点。</div>';
  }
  return `
    <div class="empty-state">
      <p>当前数据库证据中，没有同时满足这些条件的资源节点：${escapeHtml(conditions.join('、'))}。</p>
      <button class="button secondary" type="button" data-harvest-action="clear-filters">清除全部条件</button>
    </div>
  `;
}
