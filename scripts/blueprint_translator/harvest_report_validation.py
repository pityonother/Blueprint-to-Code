"""Validation contract for full and token-bounded ARK harvest reports."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .harvest_ranking import (
    NORMALIZED_HARVEST_AMOUNT_SCALE,
    YIELD_MODEL_VERSION,
    YIELD_SCORE_BASIS,
    rank_harvest_rows,
)


MAX_COMPACT_TOKENS = 12_000
MAX_DISCOVERIES_PER_RESOURCE = 6
MAX_COMPONENT_INDEX_ITEMS = 16
COMPACT_SCHEMA = "ark-harvest-compact/v3"
EXPECTED_SCORE_BASIS = YIELD_SCORE_BASIS
EXPECTED_FORMULA = (
    "completeNodeGrantCalls * normalizedResourceWeight * "
    "expectedQuantityPerSelection"
)
EXPECTED_NOT_INCLUDED = [
    "runtime melee stat and damage scaling",
    "server and runtime harvest multiplier overrides",
    "Blueprint, buff, gene, and mission hooks",
    "nonlinear quantity random powers",
    "bIsSingleUnitHarvest and nonzero additional-effectiveness cases (rows fail closed)",
    "actual animation wall-clock timing",
    "nodes hit per swing",
    "controlled observed yield",
]
EXPECTED_METHODOLOGY_KEYS = {
    "metric",
    "scoreBasis",
    "formulaVersion",
    "usageScope",
    "observedYieldPerSecond",
    "formula",
    "normalizedProfile",
    "legacyCompatibility",
    "notIncluded",
}


def _canonical_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_ranking_revision_fields(payload: dict[str, Any]) -> dict[str, str]:
    failures = payload.get("failures") if isinstance(payload.get("failures"), dict) else {}
    creatures = payload.get("creatures") if isinstance(payload.get("creatures"), list) else []
    creature_manifest = {
        "creatures": [
            {
                key: row.get(key)
                for key in ("name", "objectPath", "attacks", "warnings")
            }
            for row in creatures
            if isinstance(row, dict)
        ],
        "failures": failures.get("creatures") if isinstance(failures, dict) else [],
    }
    damage_types = (
        payload.get("damageTypes") if isinstance(payload.get("damageTypes"), list) else []
    )
    source_hashes = sorted(
        str(row.get("sha256") or "")
        for row in payload.get("sources", [])
        if isinstance(row, dict) and row.get("sha256")
    ) if isinstance(payload.get("sources"), list) else []
    creature_hash = _canonical_hash(creature_manifest)
    damage_hash = _canonical_hash(damage_types)
    revision = _canonical_hash(
        {
            "schema": payload.get("schema"),
            "resources": payload.get("resources"),
            "resourceSelectionMode": payload.get("resourceSelectionMode"),
            "methodology": payload.get("methodology"),
            "componentManifestHash": payload.get("scanManifestHash"),
            "creatureManifestHash": creature_hash,
            "damageTypeManifestHash": damage_hash,
            "sourceHashes": source_hashes,
        }
    )
    return {
        "creatureScanManifestHash": creature_hash,
        "damageTypeManifestHash": damage_hash,
        "datasetRevision": revision,
    }
EXPECTED_COVERAGE_KEYS = {
    "creaturesRequested",
    "creaturesLoaded",
    "attacksDecoded",
    "componentsScanned",
    "componentsAttempted",
    "componentsDecoded",
    "componentsSemanticGap",
    "componentsMatched",
    "componentCatalogEntries",
    "componentSourceFingerprints",
    "resourceClassesDiscovered",
    "rows",
    "rankedRows",
    "incompatibleRows",
    "unrankedRows",
}

_COMPACT_ROW_KEYS = (
    "resource",
    "component",
    "componentObjectPath",
    "creature",
    "creatureObjectPath",
    "attackIndex",
    "attackName",
    "rankingStatus",
    "reasonCode",
    "sourceDamageType",
    "effectiveDamageType",
    "damageOverrideApplied",
    "damageTypeMatch",
    "baseDamage",
    "attackInterval",
    "damageMultiplier",
    "harvestQuantityMultiplier",
    "resourceWeight",
    "resourceWeightShare",
    "overrideQuantityMin",
    "overrideQuantityMax",
    "overrideQuantityRandomPower",
    "quantityRandomPowerSource",
    "quantityOverrideMatch",
    "estimatedYieldPerNode",
    "estimatedGrantCallsPerNode",
    "estimatedHitsToDepleteNode",
    "expectedQuantityPerSelection",
    "clampResourceHarvestDamage",
    "normalizedHarvestAmountScale",
    "yieldModelVersion",
    "yieldModelBasis",
    "yieldModelStatus",
    "yieldModelCaveats",
    "harvestPressurePerSecond",
    "engineComparisonIndex",
    "legacyDiagnostics",
    "maxHarvestHealth",
    "harvestHealthGiveResourceInterval",
    "observedYieldPerSecond",
    "missingFacts",
    "missingFactsByScope",
    "warnings",
    "warningsByScope",
    "scoreBasis",
)

_DISCOVERY_KEYS = (
    "resource",
    "component",
    "componentObjectPath",
    "componentAliases",
    "componentObjectPaths",
    "creature",
    "creatureObjectPath",
    "attackIndex",
    "attackName",
    "rankingStatus",
    "effectiveDamageType",
    "damageMultiplier",
    "harvestQuantityMultiplier",
    "resourceWeightShare",
    "maxHarvestHealth",
    "estimatedYieldPerNode",
    "estimatedGrantCallsPerNode",
    "expectedQuantityPerSelection",
    "engineComparisonIndex",
    "scoreBasis",
)


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in _COMPACT_ROW_KEYS if key in row}


def _row_identity(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("resource") or ""),
        str(row.get("component") or ""),
        str(row.get("creature") or ""),
        str(row.get("attackIndex") if row.get("attackIndex") is not None else ""),
        str(row.get("attackName") or ""),
    )


def _row_subset_matches(full_row: dict[str, Any], compact_row: dict[str, Any]) -> bool:
    ignored = {"componentAliases"}
    return all(
        key in full_row and full_row.get(key) == value
        for key, value in compact_row.items()
        if key not in ignored
    )


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "rankedRows": sum(row.get("rankingStatus") == "RANKED" for row in rows),
        "incompatibleRows": sum(row.get("rankingStatus") == "INCOMPATIBLE" for row in rows),
        "unrankedRows": sum(row.get("rankingStatus") == "UNRANKED" for row in rows),
    }


def _selected_best_rows_from_full_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select canonical full rows without dropping sort fallback fields."""

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        identity = (
            str(row.get("resource") or ""),
            str(row.get("componentObjectPath") or row.get("component") or ""),
            str(row.get("creatureObjectPath") or row.get("creature") or ""),
        )
        groups.setdefault(identity, []).append(row)
    selected: list[dict[str, Any]] = []
    for group in groups.values():
        ranked = [row for row in group if row.get("rankingStatus") == "RANKED"]
        unknown = [row for row in group if row.get("rankingStatus") == "UNRANKED"]
        pool = ranked or unknown or group
        if pool:
            selected.append(rank_harvest_rows(pool)[0])
    return rank_harvest_rows(selected)


def _best_rows_from_full_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute the compact bestRows representation from full rows."""

    return [_compact_row(row) for row in _selected_best_rows_from_full_rows(rows)]


def summarize_unknown_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a deterministic, bounded summary of every non-ranked full row."""

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("rankingStatus") == "RANKED":
            continue
        key = (str(row.get("rankingStatus") or "UNRANKED"), str(row.get("reasonCode") or "UNKNOWN"))
        summary = groups.setdefault(
            key,
            {
                "rankingStatus": key[0],
                "reasonCode": key[1],
                "count": 0,
                "missingFacts": set(),
                "examples": [],
            },
        )
        summary["count"] += 1
        summary["missingFacts"].update(str(item) for item in row.get("missingFacts") or [])
        example = {
            "resource": row.get("resource"),
            "creature": row.get("creature"),
            "creatureObjectPath": row.get("creatureObjectPath"),
            "attackIndex": row.get("attackIndex"),
            "attackName": row.get("attackName"),
            "component": row.get("component"),
            "componentObjectPath": row.get("componentObjectPath"),
        }
        if example not in summary["examples"] and len(summary["examples"]) < 3:
            summary["examples"].append(example)
    return [
        {**summary, "missingFacts": sorted(summary["missingFacts"])}
        for _key, summary in sorted(groups.items())
    ]


def _deduplicate_ranked_discoveries(
    rows: list[dict[str, Any]],
    *,
    limit: int | None = 8,
) -> list[dict[str, Any]]:
    """Collapse only exact component aliases; distinct attacks remain distinct."""

    grouped: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _compact_row(raw)
        signature_payload = {
            key: value
            for key, value in row.items()
            if key not in {"component", "componentObjectPath"}
        }
        signature = json.dumps(
            signature_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        component = str(row.get("component") or "")
        component_object_path = str(row.get("componentObjectPath") or "")
        if signature not in grouped:
            grouped[signature] = {
                **row,
                "componentAliases": [component] if component else [],
                "componentObjectPaths": [component_object_path] if component_object_path else [],
            }
        else:
            if component and component not in grouped[signature]["componentAliases"]:
                grouped[signature]["componentAliases"].append(component)
            if (
                component_object_path
                and component_object_path not in grouped[signature]["componentObjectPaths"]
            ):
                grouped[signature]["componentObjectPaths"].append(component_object_path)
    for row in grouped.values():
        row["componentAliases"].sort()
        row["componentObjectPaths"].sort()
    ranked = [
        row
        for row in rank_harvest_rows(grouped.values())
        if row.get("rankingStatus") == "RANKED"
    ]
    compact = [
        {key: row.get(key) for key in _DISCOVERY_KEYS if key in row}
        for row in ranked
    ]
    return compact if limit is None else compact[: max(0, limit)]


def _resource_discovery_status(rows: list[dict[str, Any]]) -> str:
    statuses = {str(row.get("rankingStatus") or "") for row in rows}
    if "RANKED" in statuses:
        return "RANKED_CANDIDATES_AVAILABLE"
    if "UNRANKED" in statuses:
        return "ONLY_UNRANKED_CANDIDATES"
    if "INCOMPATIBLE" in statuses:
        return "ONLY_INCOMPATIBLE_CANDIDATES"
    return "NO_ROWS"


def _resource_focus_component(resource: str, rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    leaf = str(resource).rsplit("/", 1)[-1].split(".")[-1]
    if leaf.startswith("PrimalItemResource_"):
        leaf = leaf[len("PrimalItemResource_") :]
    leaf = leaf.removesuffix("_C")
    preferred = f"{leaf}HarvestComponent".casefold()
    for row in rows:
        component = str(row.get("component") or "")
        if component.casefold() == preferred:
            return component
    ordered = rank_harvest_rows(rows)
    return str(ordered[0].get("component") or "") or None


def _resource_views(resources: list[str], best_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for resource in resources:
        candidates = rank_harvest_rows(
            row for row in best_rows if str(row.get("resource") or "") == resource
        )
        focus_component = _resource_focus_component(resource, candidates)
        discoveries = _deduplicate_ranked_discoveries(candidates, limit=None)
        returned = discoveries[:MAX_DISCOVERIES_PER_RESOURCE]
        views.append(
            {
                "resource": resource,
                "discoveryStatus": _resource_discovery_status(candidates),
                "rankedDiscoveryStatus": (
                    "RANKED_ROWS_AVAILABLE"
                    if any(row.get("rankingStatus") == "RANKED" for row in candidates)
                    else "NO_RANKED_ROW"
                ),
                "candidateCounts": {
                    "total": len(candidates),
                    "ranked": sum(row.get("rankingStatus") == "RANKED" for row in candidates),
                    "incompatible": sum(
                        row.get("rankingStatus") == "INCOMPATIBLE" for row in candidates
                    ),
                    "unranked": sum(
                        row.get("rankingStatus") == "UNRANKED" for row in candidates
                    ),
                },
                "focusComponent": focus_component,
                "focusRows": [
                    _compact_row(row)
                    for row in candidates
                    if row.get("component") == focus_component
                ],
                "rankedDiscoveries": returned,
                "rankedDiscoveryCoverage": {
                    "total": len(discoveries),
                    "returned": len(returned),
                    "omitted": max(0, len(discoveries) - len(returned)),
                    "truncated": len(discoveries) > len(returned),
                },
            }
        )
    return views


def _resource_index(resources: list[str], best_rows: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for resource in resources:
        candidates = [
            row for row in best_rows if str(row.get("resource") or "") == resource
        ]
        items.append(
            {
                "resource": resource,
                "discoveryStatus": _resource_discovery_status(candidates),
                "rankedDiscoveryStatus": (
                    "RANKED_ROWS_AVAILABLE"
                    if any(row.get("rankingStatus") == "RANKED" for row in candidates)
                    else "NO_RANKED_ROW"
                ),
                "candidateCounts": {
                    "total": len(candidates),
                    "ranked": sum(row.get("rankingStatus") == "RANKED" for row in candidates),
                    "incompatible": sum(
                        row.get("rankingStatus") == "INCOMPATIBLE" for row in candidates
                    ),
                    "unranked": sum(
                        row.get("rankingStatus") == "UNRANKED" for row in candidates
                    ),
                },
            }
        )
    return {
        "total": len(items),
        "returned": len(items),
        "omitted": 0,
        "truncated": False,
        "items": items,
    }


def _component_index(
    component_rows: list[dict[str, Any]],
    resource_views: list[dict[str, Any]],
    *,
    max_items: int = MAX_COMPONENT_INDEX_ITEMS,
) -> dict[str, Any]:
    items = [
        {
            "component": component.get("component"),
            "componentObjectPath": component.get("objectPath"),
            "maxHarvestHealth": component.get("maxHarvestHealth"),
            "harvestHealthGiveResourceInterval": component.get(
                "harvestHealthGiveResourceInterval"
            ),
            "matchedResources": component.get("matchedResources") or [],
            "discoveryStatus": component.get("discoveryStatus"),
            "gaps": component.get("gaps") or [],
        }
        for component in component_rows
    ]
    priority_paths: list[str] = []
    for view in resource_views:
        for row in [*(view.get("focusRows") or []), *(view.get("rankedDiscoveries") or [])]:
            paths = row.get("componentObjectPaths") or [row.get("componentObjectPath")]
            for path in paths:
                value = str(path or "")
                if value and value not in priority_paths:
                    priority_paths.append(value)
    ordered: list[dict[str, Any]] = []
    for path in priority_paths:
        match = next(
            (item for item in items if str(item.get("componentObjectPath") or "") == path),
            None,
        )
        if match is not None and match not in ordered:
            ordered.append(match)
    ordered.extend(item for item in items if item not in ordered)
    returned = ordered[: max(0, int(max_items))]
    return {
        "total": len(items),
        "returned": len(returned),
        "omitted": max(0, len(items) - len(returned)),
        "truncated": len(items) > len(returned),
        "items": returned,
    }


def _resource_candidates(
    resources: list[str],
    best_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "resource": view["resource"],
            "discoveryStatus": view["discoveryStatus"],
            "rankedDiscoveryStatus": view["rankedDiscoveryStatus"],
            "bestRows": [
                _compact_row(row)
                for row in rank_harvest_rows(
                    candidate
                    for candidate in best_rows
                    if str(candidate.get("resource") or "") == view["resource"]
                )
            ],
        }
        for view in _resource_views(resources, best_rows)
    ]


def _component_gap_summary(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in manifest:
        for raw_gap in record.get("gaps") or []:
            gap = str(raw_gap or "")
            if not gap:
                continue
            summary = grouped.setdefault(gap, {"gap": gap, "count": 0, "examples": []})
            summary["count"] += 1
            component = str(record.get("componentObjectPath") or record.get("component") or "")
            if component and component not in summary["examples"] and len(summary["examples"]) < 5:
                summary["examples"].append(component)
    return [grouped[key] for key in sorted(grouped)]


def _scan_manifest_hash(manifest: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_source_path(value: Any) -> str:
    raw = str(value or "").strip()
    return str(Path(raw).resolve()).casefold() if raw else ""


def _normalized_object_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def _has_sha256_fingerprint(row: dict[str, Any]) -> bool:
    digest = row.get("sha256")
    return (
        isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdefABCDEF" for character in digest)
    )


def _component_source_fingerprint_coverage(
    manifest: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    attempted_paths = {
        normalized
        for row in manifest
        if (normalized := _normalized_source_path(row.get("path")))
    }
    fingerprinted_paths = {
        normalized
        for row in sources
        if _has_sha256_fingerprint(row)
        if (normalized := _normalized_source_path(row.get("path")))
    }
    covered_paths = attempted_paths & fingerprinted_paths
    return {
        "attemptedPaths": len(attempted_paths),
        "fingerprintedPaths": len(covered_paths),
        "complete": covered_paths == attempted_paths,
    }


def _summarize_failures(rows: Any, *, kind: str) -> dict[str, Any]:
    failure_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    grouped: dict[str, dict[str, Any]] = {}
    for row in failure_rows:
        reason = str(row.get("reasonCode") or "UNKNOWN")
        summary = grouped.setdefault(
            reason,
            {"reasonCode": reason, "count": 0, "examples": []},
        )
        summary["count"] += 1
        example = {
            key: row.get(key)
            for key in (
                ("name", "objectPath") if kind == "creature" else ("component", "path")
            )
            if row.get(key) not in {None, ""}
        }
        if example and example not in summary["examples"] and len(summary["examples"]) < 3:
            summary["examples"].append(example)
    return {
        "count": len(failure_rows),
        "byReason": [grouped[key] for key in sorted(grouped)],
    }


def _failure_summary(payload: dict[str, Any]) -> dict[str, Any]:
    failures = payload.get("failures") if isinstance(payload.get("failures"), dict) else {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    component_gap_summary = payload.get("componentGapSummary")
    gap_rows = (
        [
            {
                "gap": row.get("gap"),
                "count": row.get("count"),
                "examples": list(row.get("examples") or [])[:3],
            }
            for row in component_gap_summary
            if isinstance(row, dict)
        ]
        if isinstance(component_gap_summary, list)
        else []
    )
    return {
        "creatures": _summarize_failures(failures.get("creatures"), kind="creature"),
        "components": _summarize_failures(failures.get("components"), kind="component"),
        "componentSemanticGaps": {
            "count": int(coverage.get("componentsSemanticGap") or 0),
            "byGap": gap_rows,
        },
    }


def build_canonical_ai_view(
    payload: dict[str, Any],
    *,
    detail_location: str,
) -> dict[str, Any]:
    """Derive every evidence-bearing compact field from the full payload."""

    components = payload.get("componentCatalog")
    if not isinstance(components, list):
        components = payload.get("components")
    component_rows = (
        [row for row in components if isinstance(row, dict)]
        if isinstance(components, list)
        else []
    )
    rows = payload.get("rows")
    full_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    best_rows = _selected_best_rows_from_full_rows(full_rows)
    source_rows = [
        row
        for row in payload.get("sources", [])
        if isinstance(row, dict)
    ] if isinstance(payload.get("sources"), list) else []
    manifest = payload.get("componentScanManifest")
    manifest_rows = (
        [row for row in manifest if isinstance(row, dict)]
        if isinstance(manifest, list)
        else []
    )
    source_lines = "\n".join(f"{row.get('path')}|{row.get('sha256')}" for row in source_rows)
    resources = [str(item) for item in payload.get("resources", [])]
    index_mode = payload.get("resourceSelectionMode") == "ALL_DISCOVERED"
    resource_views = [] if index_mode else _resource_views(resources, best_rows)
    resource_index = (
        _resource_index(resources, best_rows)
        if index_mode
        else {
            "total": len(resources),
            "returned": 0,
            "omitted": 0,
            "truncated": False,
            "items": [],
        }
    )
    return {
        "detailLocation": detail_location,
        "viewMode": "RESOURCE_INDEX" if index_mode else "RESOURCE_DETAILS",
        "resourceViews": resource_views,
        "resourceIndex": resource_index,
        "unknownSummaryScope": "allRows",
        "unknownSummary": summarize_unknown_rows(full_rows),
        "componentIndex": _component_index(
            component_rows,
            resource_views,
            max_items=0 if index_mode else MAX_COMPONENT_INDEX_ITEMS,
        ),
        "failureSummary": _failure_summary(payload),
        "scanManifest": {
            "count": len(manifest_rows),
            "sha256": _scan_manifest_hash(manifest_rows),
        },
        "sourceSet": {
            "count": len(source_rows),
            "sha256": hashlib.sha256(source_lines.encode("utf-8")).hexdigest(),
        },
    }


def validate_harvest_report(
    full_payload: dict[str, Any],
    ai_payload: dict[str, Any],
    *,
    full_path: str | Path | None = None,
    full_characters: int | None = None,
    ai_characters: int | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if full_payload.get("schema") != ai_payload.get("schema"):
        errors.append("schema mismatch")
    if ai_payload.get("compactSchema") != COMPACT_SCHEMA:
        errors.append("compactSchema mismatch")
    if full_payload.get("generatedAt") != ai_payload.get("generatedAt"):
        errors.append("generatedAt mismatch")
    if full_payload.get("resources") != ai_payload.get("resources"):
        errors.append("resource scope mismatch")
    if full_payload.get("resourceSelectionMode") not in {"EXPLICIT", "ALL_DISCOVERED"}:
        errors.append("resourceSelectionMode mismatch")
    if full_payload.get("methodology") != ai_payload.get("methodology"):
        errors.append("methodology mismatch")
    methodology = (
        full_payload.get("methodology")
        if isinstance(full_payload.get("methodology"), dict)
        else {}
    )
    if methodology.get("scoreBasis") != EXPECTED_SCORE_BASIS:
        errors.append("full methodology scoreBasis mismatch")
    if methodology.get("metric") != "estimatedYieldPerNode":
        errors.append("full methodology metric mismatch")
    if methodology.get("formulaVersion") != YIELD_MODEL_VERSION:
        errors.append("full methodology formulaVersion mismatch")
    if methodology.get("usageScope") not in {
        "UNFILTERED_ENGINE_ATTACKS",
        "TAMED_PLAYER",
        "TAMED_AI",
        "WILD",
    }:
        errors.append("full methodology usageScope mismatch")
    if methodology.get("observedYieldPerSecond") is not None:
        errors.append("full methodology must not invent observedYieldPerSecond")
    if methodology.get("formula") != EXPECTED_FORMULA:
        errors.append("full methodology formula mismatch")
    if methodology.get("normalizedProfile") != {
        "harvestAmountScale": NORMALIZED_HARVEST_AMOUNT_SCALE,
        "nodeStartState": "FRESH",
        "nodeCompletion": "FULLY_HARVESTED",
    }:
        errors.append("full methodology normalizedProfile mismatch")
    if methodology.get("legacyCompatibility") != {
        "engineComparisonIndex": "DEPRECATED_ALIAS_OF_ESTIMATED_YIELD_PER_NODE",
        "harvestPressurePerSecond": "DIAGNOSTIC_ONLY_NOT_USED_FOR_ORDER",
    }:
        errors.append("full methodology legacyCompatibility mismatch")
    if set(methodology) != EXPECTED_METHODOLOGY_KEYS:
        errors.append("full methodology fields mismatch")
    if methodology.get("notIncluded") != EXPECTED_NOT_INCLUDED:
        errors.append("full methodology notIncluded mismatch")
    if full_payload.get("coverage") != ai_payload.get("coverage"):
        errors.append("coverage mismatch")

    rows = full_payload.get("rows")
    full_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    coverage = full_payload.get("coverage") if isinstance(full_payload.get("coverage"), dict) else {}
    if set(coverage) != EXPECTED_COVERAGE_KEYS:
        errors.append("full coverage fields mismatch")
    if coverage.get("rows") != len(full_rows):
        errors.append("full row count does not match coverage")
    for key, count in _status_counts(full_rows).items():
        if coverage.get(key) != count:
            errors.append(f"full {key} does not match coverage")
    for index, row in enumerate(full_rows):
        status = str(row.get("rankingStatus") or "")
        score = row.get("estimatedYieldPerNode")
        compatibility_score = row.get("engineComparisonIndex")
        if row.get("observedYieldPerSecond") is not None:
            errors.append(f"full row {index} must not invent observedYieldPerSecond")
        if status == "RANKED" and (
            not isinstance(score, (int, float)) or isinstance(score, bool)
        ):
            errors.append(f"full row {index} ranked without numeric estimatedYieldPerNode")
        if status != "RANKED" and score is not None:
            errors.append(f"full row {index} non-ranked with estimatedYieldPerNode")
        if compatibility_score is not None and compatibility_score != score:
            errors.append(
                f"full row {index} deprecated engineComparisonIndex differs from estimatedYieldPerNode"
            )

    selected_best_rows = _selected_best_rows_from_full_rows(full_rows)
    best_rows = [_compact_row(row) for row in selected_best_rows]
    if full_payload.get("bestRows") != best_rows:
        errors.append("full bestRows does not match rows")
    resources = [str(item) for item in full_payload.get("resources", [])]
    expected_candidates = _resource_candidates(resources, selected_best_rows)
    if full_payload.get("resourceCandidates") != expected_candidates:
        errors.append("full resourceCandidates mismatch")

    manifest = full_payload.get("componentScanManifest")
    manifest_rows = (
        [row for row in manifest if isinstance(row, dict)]
        if isinstance(manifest, list)
        else []
    )
    if not isinstance(manifest, list):
        errors.append("full componentScanManifest missing")
    manifest_hash = _scan_manifest_hash(manifest_rows)
    if full_payload.get("scanManifestHash") != manifest_hash:
        errors.append("full scanManifestHash mismatch")
    expected_component_gaps = _component_gap_summary(manifest_rows)
    if full_payload.get("componentGapSummary") != expected_component_gaps:
        errors.append("full componentGapSummary mismatch")
    manifest_coverage = {
        "componentsScanned": len(manifest_rows),
        "componentsAttempted": len(manifest_rows),
        "componentsDecoded": sum(row.get("decoded") is True for row in manifest_rows),
        "componentsSemanticGap": sum(row.get("semanticGap") is True for row in manifest_rows),
        "componentsMatched": sum(row.get("matched") is True for row in manifest_rows),
    }
    for key, count in manifest_coverage.items():
        if coverage.get(key) != count:
            errors.append(f"full {key} does not match componentScanManifest")

    manifest_paths = [_normalized_source_path(row.get("path")) for row in manifest_rows]
    manifest_object_paths = [
        _normalized_object_path(row.get("componentObjectPath"))
        for row in manifest_rows
    ]
    if (full_rows or full_payload.get("components")) and not manifest_rows:
        errors.append("full componentScanManifest cannot be empty when rows or components exist")
    if any(not path for path in manifest_paths):
        errors.append("full componentScanManifest contains a missing component path")
    if len(manifest_paths) != len(set(manifest_paths)):
        errors.append("full componentScanManifest contains a duplicate component path")
    if any(not path for path in manifest_object_paths):
        errors.append("full componentScanManifest contains a missing componentObjectPath")
    if len(manifest_object_paths) != len(set(manifest_object_paths)):
        errors.append("full componentScanManifest contains a duplicate componentObjectPath")

    manifest_object_path_set = set(manifest_object_paths)
    components_value = full_payload.get("components")
    component_rows = (
        [row for row in components_value if isinstance(row, dict)]
        if isinstance(components_value, list)
        else []
    )
    component_object_paths = {
        _normalized_object_path(row.get("objectPath") or row.get("componentObjectPath"))
        for row in component_rows
    }
    if "" in component_object_paths:
        errors.append("full components contain a missing objectPath")
        component_object_paths.discard("")
    if component_object_paths - manifest_object_path_set:
        errors.append("full components missing from componentScanManifest")

    component_catalog_value = full_payload.get("componentCatalog")
    component_catalog_rows = (
        [row for row in component_catalog_value if isinstance(row, dict)]
        if isinstance(component_catalog_value, list)
        else []
    )
    if coverage.get("componentCatalogEntries") != len(component_catalog_rows):
        errors.append("full componentCatalogEntries does not match componentCatalog")
    component_catalog_paths = {
        _normalized_object_path(row.get("objectPath") or row.get("componentObjectPath"))
        for row in component_catalog_rows
    }
    if "" in component_catalog_paths:
        errors.append("full componentCatalog contains a missing objectPath")
        component_catalog_paths.discard("")
    if component_catalog_paths - manifest_object_path_set:
        errors.append("full componentCatalog missing from componentScanManifest")

    row_object_paths = {
        _normalized_object_path(row.get("componentObjectPath"))
        for row in full_rows
    }
    if "" in row_object_paths:
        errors.append("full rows contain a missing componentObjectPath")
        row_object_paths.discard("")
    if row_object_paths - manifest_object_path_set:
        errors.append("full rows missing from componentScanManifest")

    sources = full_payload.get("sources")
    source_rows = (
        [row for row in sources if isinstance(row, dict)]
        if isinstance(sources, list)
        else []
    )
    source_paths = [_normalized_source_path(row.get("path")) for row in source_rows]
    if any(not path for path in source_paths):
        errors.append("full sources contain a missing source path")
    if len(source_paths) != len(set(source_paths)):
        errors.append("full sources contain a duplicate source path")
    if any(not _has_sha256_fingerprint(row) for row in source_rows):
        errors.append("full sources contain an invalid source SHA-256")

    fingerprinted_source_paths = {
        _normalized_source_path(row.get("path"))
        for row in source_rows
        if _has_sha256_fingerprint(row)
        and _normalized_source_path(row.get("path"))
    }
    referenced_source_paths: set[str] = set()
    for collection_name in (
        "creatures",
        "components",
        "componentCatalog",
        "damageTypes",
        "componentScanManifest",
    ):
        collection = full_payload.get(collection_name)
        for row in collection if isinstance(collection, list) else []:
            if not isinstance(row, dict):
                continue
            if path := _normalized_source_path(row.get("path")):
                referenced_source_paths.add(path)
            source_chain = row.get("sourceChain")
            for chain_path in source_chain if isinstance(source_chain, list) else []:
                if path := _normalized_source_path(chain_path):
                    referenced_source_paths.add(path)
    if referenced_source_paths - fingerprinted_source_paths:
        errors.append("full referenced asset paths missing from sources")

    expected_fingerprint_coverage = _component_source_fingerprint_coverage(
        manifest_rows,
        source_rows,
    )
    if coverage.get("componentSourceFingerprints") != expected_fingerprint_coverage:
        errors.append(
            "full componentSourceFingerprints does not match componentScanManifest and sources"
        )
    if expected_fingerprint_coverage.get("complete") is not True:
        errors.append("full componentSourceFingerprints must be complete")

    expected_revision_fields = build_ranking_revision_fields(full_payload)
    for field, expected_value in expected_revision_fields.items():
        if full_payload.get(field) != expected_value:
            errors.append(f"full {field} mismatch")

    if full_path is None:
        errors.append("full path is required to validate detailLocation")
        expected_detail_location = str(ai_payload.get("detailLocation") or "")
    else:
        expected_detail_location = Path(full_path).name
    expected_view = build_canonical_ai_view(
        full_payload,
        detail_location=expected_detail_location,
    )
    evidence_fields = (
        "detailLocation",
        "viewMode",
        "resourceViews",
        "resourceIndex",
        "unknownSummaryScope",
        "unknownSummary",
        "componentIndex",
        "failureSummary",
        "scanManifest",
        "sourceSet",
    )
    for field in evidence_fields:
        if ai_payload.get(field) != expected_view[field]:
            errors.append(f"{field} mismatch")

    expected_keys = {
        "schema",
        "compactSchema",
        "generatedAt",
        "resources",
        "methodology",
        "coverage",
        *evidence_fields,
        "tokenEstimate",
    }
    returned_keys = set(ai_payload)
    missing_keys = sorted(expected_keys - returned_keys)
    unexpected_keys = sorted(returned_keys - expected_keys)
    if missing_keys:
        errors.append(f"compact report missing fields: {', '.join(missing_keys)}")
    if unexpected_keys:
        errors.append(f"compact report has unexpected fields: {', '.join(unexpected_keys)}")

    token_estimate = (
        ai_payload.get("tokenEstimate")
        if isinstance(ai_payload.get("tokenEstimate"), dict)
        else {}
    )
    expected_token_estimate_keys = {"method", "characters", "estimatedTokens"}
    if set(token_estimate) != expected_token_estimate_keys:
        errors.append("tokenEstimate fields mismatch")
    if token_estimate.get("method") != "ceil(characters/4)":
        errors.append("tokenEstimate method mismatch")
    declared_characters = token_estimate.get("characters")
    declared_tokens = token_estimate.get("estimatedTokens")
    characters_are_valid = (
        isinstance(declared_characters, int)
        and not isinstance(declared_characters, bool)
        and declared_characters >= 0
    )
    tokens_are_valid = (
        isinstance(declared_tokens, int)
        and not isinstance(declared_tokens, bool)
        and declared_tokens >= 0
    )
    if not characters_are_valid:
        errors.append("tokenEstimate characters type mismatch")
    if not tokens_are_valid:
        errors.append("tokenEstimate estimatedTokens type mismatch")
    if characters_are_valid and tokens_are_valid:
        if declared_tokens != math.ceil(declared_characters / 4):
            errors.append("tokenEstimate estimatedTokens mismatch")
    if isinstance(ai_characters, int) and ai_characters >= 0:
        if declared_characters != ai_characters:
            errors.append("tokenEstimate characters mismatch")
        if declared_tokens != math.ceil(ai_characters / 4):
            errors.append("tokenEstimate does not match compact report length")
        if math.ceil(ai_characters / 4) > MAX_COMPACT_TOKENS:
            errors.append(
                f"compact report exceeds {MAX_COMPACT_TOKENS} token budget"
            )
    if (
        isinstance(full_characters, int)
        and full_characters > 0
        and isinstance(ai_characters, int)
        and ai_characters >= full_characters
    ):
        errors.append("compact report must be smaller than full report")

    compression: dict[str, Any] = {
        "fullCharacters": full_characters,
        "aiCharacters": ai_characters,
        "characterReductionPct": None,
    }
    if (
        isinstance(full_characters, int)
        and full_characters > 0
        and isinstance(ai_characters, int)
        and ai_characters >= 0
    ):
        compression["characterReductionPct"] = round(
            (1.0 - ai_characters / full_characters) * 100.0,
            2,
        )
    return {
        "schema": "ark-harvest-report-validation/v1",
        "valid": not errors,
        "errors": errors,
        "checks": {
            "fullRows": len(full_rows),
            "bestRows": len(best_rows),
            "resourceViews": len(expected_view["resourceViews"]),
            "focusRows": sum(
                len(view.get("focusRows") or [])
                for view in expected_view["resourceViews"]
            ),
            "sources": expected_view["sourceSet"]["count"],
        },
        "compression": compression,
    }
