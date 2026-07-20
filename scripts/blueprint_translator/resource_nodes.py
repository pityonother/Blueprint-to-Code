"""Evidence-aware ARK resource-node catalog and ranking queries.

The physical node and the yielded resource are separate ARK assets.  A node
usually points at a HarvestComponent through ``AttachedComponentClass``; that
component then defines one or more resource entries.  This module keeps that
join explicit so rankings cannot accidentally cross node/component boundaries.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from .map_reference_scan_cache import MapReferenceScanCache

from .uasset_graphs import (
    cdo_property_tag_blocks,
    export_data_bytes,
    parse_property_block_value,
    parse_uasset_summary,
    parse_uasset_package,
)


CATALOG_SCHEMA = "ark-resource-node-catalog/v1"
NODE_PAGE_SCHEMA = "blueprint-to-code.harvest-node-page/v1"
RANKING_RESULT_SCHEMA = "blueprint-to-code.harvest-ranking-result/v1"
NODE_PAGE_MAX_LIMIT = 16

CONFIRMED = "CONFIRMED"
NOT_RECOVERED = "NOT_RECOVERED"
SOURCE_NOT_AVAILABLE = "SOURCE_NOT_AVAILABLE"
NOT_INDEXED = "NOT_INDEXED"
STALE_REVISION = "STALE_REVISION"


class NotFoliageTypeAsset(ValueError):
    """The candidate is a valid asset, but not a FoliageType node."""


_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read JPEG SOF dimensions without depending on an image library."""

    if len(data) < 8 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        return None
    cursor = 2
    while cursor + 3 < len(data):
        marker_start = data.find(b"\xff", cursor)
        if marker_start < 0 or marker_start + 1 >= len(data):
            return None
        marker_cursor = marker_start + 1
        while marker_cursor < len(data) and data[marker_cursor] == 0xFF:
            marker_cursor += 1
        if marker_cursor >= len(data):
            return None
        marker = data[marker_cursor]
        cursor = marker_cursor + 1
        if marker in {0x00, 0x01, *range(0xD0, 0xDA)}:
            continue
        if cursor + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[cursor : cursor + 2], "big")
        if segment_length < 2 or cursor + segment_length > len(data):
            return None
        if marker in _JPEG_SOF_MARKERS:
            if segment_length < 7:
                return None
            height = int.from_bytes(data[cursor + 3 : cursor + 5], "big")
            width = int.from_bytes(data[cursor + 5 : cursor + 7], "big")
            return (width, height) if width > 0 and height > 0 else None
        if marker == 0xDA:
            return None
        cursor += segment_length
    return None


def extract_length_prefixed_jpeg_thumbnail(data: bytes, header_size: int) -> dict[str, Any]:
    """Recover an exact length-prefixed JPEG wholly contained in a uasset header."""

    if header_size <= 0 or header_size > len(data):
        return {
            "status": NOT_RECOVERED,
            "reasonCode": "UASSET_HEADER_BOUNDS_INVALID",
        }
    candidates: list[dict[str, Any]] = []
    cursor = 0
    while True:
        offset = data.find(b"\xff\xd8\xff", cursor, header_size)
        if offset < 0:
            break
        cursor = offset + 3
        if offset < 4:
            continue
        size = int.from_bytes(data[offset - 4 : offset], "little")
        end = offset + size
        if size < 8 or end > header_size or end > len(data):
            continue
        jpeg = data[offset:end]
        dimensions = _jpeg_dimensions(jpeg)
        if dimensions is None:
            continue
        width, height = dimensions
        candidates.append(
            {
                "status": "AVAILABLE",
                "mimeType": "image/jpeg",
                "width": width,
                "height": height,
                "sizeBytes": size,
                "offset": offset,
                "lengthPrefixOffset": offset - 4,
                "data": jpeg,
            }
        )
    if len(candidates) == 1:
        return candidates[0]
    return {
        "status": NOT_RECOVERED,
        "reasonCode": (
            "UASSET_THUMBNAIL_AMBIGUOUS"
            if len(candidates) > 1
            else "UASSET_THUMBNAIL_NOT_RECOVERED"
        ),
        "candidateCount": len(candidates),
    }


def extract_uasset_thumbnail(path: Path) -> dict[str, Any]:
    """Extract a package thumbnail while keeping payload bytes out of JSON reports."""

    try:
        data = Path(path).read_bytes()
        summary, _warnings = parse_uasset_summary(data)
        header_size = int(summary.get("total_header_size") or 0)
        return extract_length_prefixed_jpeg_thumbnail(data, header_size)
    except Exception as exc:
        return {
            "status": NOT_RECOVERED,
            "reasonCode": "UASSET_THUMBNAIL_PARSE_FAILED",
            "detail": str(exc)[:200],
        }


def cache_resource_node_thumbnail(path: Path, image_root: Path) -> dict[str, Any]:
    """Store a recovered thumbnail by content hash and return bounded metadata."""

    result = extract_uasset_thumbnail(path)
    if result.get("status") != "AVAILABLE":
        return {key: value for key, value in result.items() if key != "data"}
    payload = result.get("data")
    if not isinstance(payload, bytes):
        return {
            "status": NOT_RECOVERED,
            "reasonCode": "UASSET_THUMBNAIL_BYTES_NOT_AVAILABLE",
        }
    digest = hashlib.sha256(payload).hexdigest()
    output_root = Path(image_root)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"{digest}.jpg"
    if (
        not output_path.is_file()
        or output_path.stat().st_size != len(payload)
        or _sha256_file(output_path) != digest
    ):
        temporary = output_root / f".{digest}.{os.getpid()}.tmp"
        temporary.write_bytes(payload)
        temporary.replace(output_path)
    return {
        key: value
        for key, value in {
            **result,
            "sha256": digest,
            "url": f"/api/harvest/images/{digest}.jpg",
            "extractionMethod": "UASSET_HEADER_LENGTH_PREFIXED_JPEG",
        }.items()
        if key != "data"
    }


def component_facts_from_report(ranking_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the complete component catalog, with legacy report fallback."""

    complete = ranking_report.get("componentCatalog")
    if isinstance(complete, list):
        return [item for item in complete if isinstance(item, dict)]
    legacy = ranking_report.get("components")
    return [item for item in legacy if isinstance(item, dict)] if isinstance(legacy, list) else []


def referenced_component_package_paths(nodes: Iterable[dict[str, Any]]) -> list[str]:
    paths: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        component = node.get("harvestComponent")
        if not isinstance(component, dict) or component.get("status") != CONFIRMED:
            continue
        package_path = canonical_package_path(component.get("packagePath"))
        if package_path:
            paths.add(package_path)
    return sorted(paths, key=str.casefold)


def canonical_package_path(value: object) -> str:
    """Return the package portion of an Unreal object path."""

    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    if "." in text:
        text = text.split(".", 1)[0]
    return text.rstrip("/")


def _object_name(value: object) -> str:
    text = str(value or "").strip()
    if "." in text:
        return text.rsplit(".", 1)[-1]
    if "/" in text:
        return text.rsplit("/", 1)[-1]
    return text


def resolve_object_reference(
    package_index: int,
    imports: list[dict[str, Any]],
    exports: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve an FPackageIndex and retain whether the full package was known."""

    if package_index == 0:
        return {
            "status": NOT_RECOVERED,
            "name": "",
            "packagePath": "",
            "objectPath": "",
            "packageIndex": 0,
        }

    if package_index > 0:
        export_index = package_index - 1
        if 0 <= export_index < len(exports):
            name = str(exports[export_index].get("object_name") or "")
            return {
                "status": NOT_RECOVERED,
                "name": name,
                "packagePath": "",
                "objectPath": name,
                "packageIndex": package_index,
            }
        return {
            "status": NOT_RECOVERED,
            "name": "",
            "packagePath": "",
            "objectPath": "",
            "packageIndex": package_index,
        }

    import_index = -package_index - 1
    if not 0 <= import_index < len(imports):
        return {
            "status": NOT_RECOVERED,
            "name": "",
            "packagePath": "",
            "objectPath": "",
            "packageIndex": package_index,
        }

    imported = imports[import_index]
    name = str(imported.get("object_name") or "")
    package_path = ""
    outer_index = imported.get("outer_index")
    seen: set[int] = set()
    while isinstance(outer_index, int) and outer_index < 0:
        outer_import_index = -outer_index - 1
        if outer_import_index in seen or not 0 <= outer_import_index < len(imports):
            break
        seen.add(outer_import_index)
        outer = imports[outer_import_index]
        outer_name = str(outer.get("object_name") or "")
        if str(outer.get("class_name") or "") == "Package" or outer_name.startswith("/Game/"):
            package_path = canonical_package_path(outer_name)
            break
        outer_index = outer.get("outer_index")

    object_path = f"{package_path}.{name}" if package_path and name else name
    return {
        "status": CONFIRMED if package_path and name else NOT_RECOVERED,
        "name": name,
        "packagePath": package_path,
        "objectPath": object_path,
        "packageIndex": package_index,
    }


def build_node_id(object_path: str) -> str:
    digest = hashlib.sha256(object_path.encode("utf-8")).hexdigest()[:20]
    return f"node_{digest}"


def build_node_resource_id(
    node_id: str,
    component_package_path: str,
    entry_index: int | None,
    resource: str,
) -> str:
    identity = "\n".join(
        [
            node_id,
            canonical_package_path(component_package_path),
            "" if entry_index is None else str(entry_index),
            str(resource or ""),
        ]
    )
    return "node_resource_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _component_index(components: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for component in components:
        if not isinstance(component, dict):
            continue
        package_path = canonical_package_path(component.get("objectPath"))
        if package_path:
            index[package_path.casefold()] = component
    return index


def attach_component_resources(
    node: dict[str, Any],
    components: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Join a physical node to the exact HarvestComponent resource entries."""

    result = deepcopy(node)
    gaps = {str(item) for item in result.get("gaps", []) if item}
    harvest_component = result.get("harvestComponent")
    component_package = canonical_package_path(
        harvest_component.get("packagePath") if isinstance(harvest_component, dict) else ""
    )
    component = _component_index(components).get(component_package.casefold()) if component_package else None
    if not isinstance(component, dict):
        gaps.add("HARVEST_COMPONENT_FACTS_NOT_AVAILABLE")
        result["resources"] = {
            "status": SOURCE_NOT_AVAILABLE,
            "count": None,
            "items": [],
        }
        result["gaps"] = sorted(gaps)
        return result

    resource_entries = component.get("resourceEntries")
    if not isinstance(resource_entries, list):
        gaps.add("HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED")
        result["resources"] = {
            "status": NOT_RECOVERED,
            "count": None,
            "items": [],
        }
        result["gaps"] = sorted(gaps)
        return result
    component_gaps = {str(item) for item in component.get("gaps", []) if item}
    if "HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED" in component_gaps:
        gaps.update(component_gaps)
        result["resources"] = {
            "status": NOT_RECOVERED,
            "count": None,
            "items": [],
        }
        result["gaps"] = sorted(gaps)
        return result

    items: list[dict[str, Any]] = []
    for ordinal, entry in enumerate(resource_entries):
        if not isinstance(entry, dict):
            continue
        entry_index = entry.get("entryIndex")
        normalized_index = int(entry_index) if isinstance(entry_index, int) else ordinal
        resource = str(entry.get("resource") or "")
        entry_gaps = sorted({str(item) for item in entry.get("gaps", []) if item})
        status = CONFIRMED if resource and not entry_gaps else NOT_RECOVERED
        if not resource:
            entry_gaps.append("RESOURCE_ITEM_NOT_RECOVERED")
        items.append(
            {
                "entryIndex": normalized_index,
                "resource": resource,
                "displayName": resource_display_name(resource),
                "nodeResourceId": build_node_resource_id(
                    str(result.get("id") or ""),
                    component_package,
                    normalized_index,
                    resource,
                ),
                "evidenceStatus": status,
                "gaps": sorted(set(entry_gaps)),
            }
        )

    if isinstance(harvest_component, dict):
        harvest_component["componentObjectPath"] = str(component.get("objectPath") or "")
        harvest_component["componentName"] = str(component.get("component") or "")
    result["resources"] = {"status": CONFIRMED, "count": len(items), "items": items}
    result["gaps"] = sorted(gaps)
    return result


def resource_display_name(resource: str) -> str:
    return (
        str(resource or "")
        .removeprefix("PrimalItemResource_")
        .removesuffix("_C")
        .replace("_", " ")
    )


def component_source_freshness(
    component: dict[str, Any],
    source_rows: Iterable[dict[str, Any]],
    *,
    hash_cache: dict[Path, str] | None = None,
) -> dict[str, Any]:
    """Compare every inherited component source against report fingerprints."""

    source_index: dict[str, dict[str, Any]] = {}
    for row in source_rows:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        source_index[str(Path(str(row["path"])).resolve()).casefold()] = row
    chain = component.get("sourceChain")
    paths = [Path(str(value)).resolve() for value in chain if value] if isinstance(chain, list) else []
    if not paths:
        return {
            "status": SOURCE_NOT_AVAILABLE,
            "checked": 0,
            "stale": [],
            "missing": ["COMPONENT_SOURCE_CHAIN_NOT_AVAILABLE"],
        }
    cache = hash_cache if hash_cache is not None else {}
    stale: list[str] = []
    missing: list[str] = []
    checked = 0
    for path in paths:
        expected = source_index.get(str(path).casefold())
        expected_hash = str(expected.get("sha256") or "") if isinstance(expected, dict) else ""
        if not path.is_file() or len(expected_hash) != 64:
            missing.append(path.name)
            continue
        if path not in cache:
            cache[path] = _sha256_file(path)
        checked += 1
        if cache[path].casefold() != expected_hash.casefold():
            stale.append(path.name)
    if stale:
        status = STALE_REVISION
    elif missing:
        status = SOURCE_NOT_AVAILABLE
    else:
        status = CONFIRMED
    return {
        "status": status,
        "checked": checked,
        "stale": sorted(set(stale)),
        "missing": sorted(set(missing)),
    }


def _matches_node_query(node: dict[str, Any], q: str, map_name: str, resource: str) -> bool:
    if q:
        haystack = " ".join(
            [
                str(node.get("name") or ""),
                str(node.get("objectPath") or ""),
                str(node.get("mesh", {}).get("name") or "")
                if isinstance(node.get("mesh"), dict)
                else "",
            ]
        ).casefold()
        if q.casefold() not in haystack:
            return False
    if map_name:
        usage_families = node.get("mapUsage", {}).get("families", [])
        map_items = node.get("mapReferences", {}).get("items", [])
        matches_family = any(
            map_name.casefold()
            in " ".join(
                [
                    str(item.get("mapFamily") or ""),
                    str(item.get("displayName") or ""),
                ]
            ).casefold()
            for item in usage_families
            if isinstance(item, dict)
        ) if isinstance(usage_families, list) else False
        matches_evidence = any(
            map_name.casefold()
            in " ".join(
                [
                    str(item.get("mapFamily") or ""),
                    str(item.get("name") or ""),
                    str(item.get("objectPath") or ""),
                ]
            ).casefold()
            for item in map_items
            if isinstance(item, dict)
        )
        if not matches_family and not matches_evidence:
            return False
    if resource:
        resource_items = node.get("resources", {}).get("items", [])
        if not any(
            str(item.get("resource") or "").casefold() == resource.casefold()
            for item in resource_items
            if isinstance(item, dict)
        ):
            return False
    return True


def _node_page_preview(node: dict[str, Any]) -> dict[str, Any]:
    map_references = node.get("mapReferences")
    map_items = map_references.get("items") if isinstance(map_references, dict) else []
    maps = [dict(item) for item in map_items if isinstance(item, dict)] if isinstance(map_items, list) else []
    resources = node.get("resources")
    resource_items = resources.get("items") if isinstance(resources, dict) else []
    recovered_resources = (
        [
            {
                key: item.get(key)
                for key in (
                    "entryIndex",
                    "resource",
                    "displayName",
                    "nodeResourceId",
                    "evidenceStatus",
                )
                if key in item
            }
            for item in resource_items
            if isinstance(item, dict)
        ]
        if isinstance(resource_items, list)
        else []
    )
    map_previews = [
        {
            key: item.get(key)
            for key in (
                "id",
                "name",
                "objectPath",
                "mapFamily",
                "mapKind",
                "relation",
                "evidenceType",
                "evidenceStatus",
                "usageStatus",
                "evidenceCount",
            )
            if key in item
        }
        for item in maps[:6]
    ]
    mesh = node.get("mesh")
    actor_class = node.get("actorClass")
    component = node.get("harvestComponent")
    image = node.get("image")
    gaps = [str(item) for item in node.get("gaps", []) if item]
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "objectPath": node.get("objectPath"),
        "nodeType": node.get("nodeType"),
        "assetClass": node.get("assetClass"),
        "mesh": {
            key: mesh.get(key)
            for key in ("status", "name")
            if isinstance(mesh, dict) and key in mesh
        },
        "actorClass": {
            key: actor_class.get(key)
            for key in ("status", "name", "packagePath")
            if isinstance(actor_class, dict) and key in actor_class
        },
        "harvestComponent": {
            key: component.get(key)
            for key in ("status", "name", "packagePath")
            if isinstance(component, dict) and key in component
        },
        "resources": {
            "status": resources.get("status") if isinstance(resources, dict) else NOT_INDEXED,
            "count": resources.get("count") if isinstance(resources, dict) else None,
            "items": recovered_resources[:8],
            "truncated": len(recovered_resources) > 8,
        },
        "mapReferences": {
            "status": map_references.get("status")
            if isinstance(map_references, dict)
            else NOT_INDEXED,
            "count": map_references.get("count") if isinstance(map_references, dict) else None,
            "items": map_previews,
            "truncated": len(maps) > 6,
            "indirectStatus": map_references.get("indirectStatus")
            if isinstance(map_references, dict)
            else NOT_INDEXED,
        },
        "mapUsage": deepcopy(node.get("mapUsage"))
        if isinstance(node.get("mapUsage"), dict)
        else _map_usage_summary(maps),
        "assetOrigin": deepcopy(node.get("assetOrigin"))
        if isinstance(node.get("assetOrigin"), dict)
        else {},
        "image": {
            key: image.get(key)
            for key in ("status", "url")
            if isinstance(image, dict) and key in image
        }
        or {"status": "NOT_EXTRACTED"},
        "gapCount": len(gaps),
    }


def _node_page_coverage(coverage: object) -> dict[str, Any]:
    if not isinstance(coverage, dict):
        return {}
    result = {
        key: deepcopy(coverage[key])
        for key in (
            "discoveryMode",
            "nodesDecoded",
            "nodeCandidates",
            "nodeDecodeFailures",
            "nodesByType",
            "nonFoliageAssetsSkipped",
            "nonResourceFoliageCandidatesSkipped",
            "rankingCreatures",
            "creatureCandidatesDiscovered",
            "creatureAssetsCataloged",
            "speciesCataloged",
            "attacksDecoded",
            "attacksEligibleForScope",
            "attacksConditionalForScope",
            "attacksIneligibleForScope",
            "nodesWithStaleComponentSources",
            "nodesWithoutComponentSourceProof",
            "claimsAllNodes",
            "claimsAllCreatures",
        )
        if key in coverage
    }
    nested_fields = {
        "candidateDiscovery": (
            "candidatesDiscovered",
            "candidatesSelected",
            "selectionStrategy",
            "backends",
        ),
        "mapScan": (
            "status",
            "filesScanned",
            "indirectReferences",
            "claimsCompleteMapUsage",
            "nodesWithMapUsageEvidence",
            "mapFamilies",
            "referenceCounts",
        ),
        "images": ("status", "available", "notRecovered", "uniqueFiles", "inlineBytes"),
    }
    for key, fields in nested_fields.items():
        value = coverage.get(key)
        if isinstance(value, dict):
            result[key] = {
                field: deepcopy(value[field]) for field in fields if field in value
            }
    node_type_discovery = coverage.get("nodeTypeDiscovery")
    if isinstance(node_type_discovery, dict):
        result["nodeTypeDiscovery"] = deepcopy(node_type_discovery)
    return result


def query_resource_nodes(
    catalog: dict[str, Any],
    *,
    q: str = "",
    map_name: str = "",
    resource: str = "",
    offset: int = 0,
    limit: int = 24,
) -> dict[str, Any]:
    if len(q) > 100:
        raise ValueError("q must be at most 100 characters")
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), NODE_PAGE_MAX_LIMIT))
    nodes = catalog.get("nodes")
    candidates = [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []
    matched = [
        node for node in candidates if _matches_node_query(node, q.strip(), map_name.strip(), resource.strip())
    ]
    matched.sort(key=lambda node: (str(node.get("name") or "").casefold(), str(node.get("id") or "")))
    items = [_node_page_preview(node) for node in matched[offset : offset + limit]]
    next_offset = offset + len(items) if offset + len(items) < len(matched) else None
    return {
        "schema": NODE_PAGE_SCHEMA,
        "dataset": catalog.get("dataset") or {},
        "coverage": _node_page_coverage(catalog.get("coverage")),
        "total": len(matched),
        "offset": offset,
        "limit": limit,
        "nextOffset": next_offset,
        "items": items,
    }


def _find_node_and_resource(
    catalog: dict[str, Any], node_id: str, node_resource_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes = catalog.get("nodes")
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or str(node.get("id") or "") != node_id:
            continue
        resources = node.get("resources", {}).get("items", [])
        for resource in resources if isinstance(resources, list) else []:
            if isinstance(resource, dict) and str(resource.get("nodeResourceId") or "") == node_resource_id:
                return node, resource
        raise KeyError("NODE_RESOURCE_NOT_FOUND")
    raise KeyError("RESOURCE_NODE_NOT_FOUND")


def rank_node_resource(
    catalog: dict[str, Any],
    ranking_report: dict[str, Any],
    *,
    node_id: str,
    node_resource_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank only rows belonging to the selected node/component/resource entry."""

    node, resource = _find_node_and_resource(catalog, node_id, node_resource_id)
    component = node.get("harvestComponent")
    component_package = canonical_package_path(
        component.get("packagePath") if isinstance(component, dict) else ""
    )
    resource_class = str(resource.get("resource") or "")
    rows = ranking_report.get("bestRows")
    candidates: list[dict[str, Any]] = []
    non_ranked = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if canonical_package_path(row.get("componentObjectPath")).casefold() != component_package.casefold():
            continue
        if str(row.get("resource") or "").casefold() != resource_class.casefold():
            continue
        if str(row.get("rankingStatus") or "") != "RANKED" or not isinstance(
            row.get("engineComparisonIndex"), (int, float)
        ):
            non_ranked += 1
            continue
        candidates.append(dict(row))

    candidates.sort(
        key=lambda row: (
            -float(row.get("engineComparisonIndex") or 0.0),
            str(row.get("creature") or "").casefold(),
            str(row.get("creatureObjectPath") or ""),
            int(row.get("attackIndex") or 0),
        )
    )
    best_by_creature: list[dict[str, Any]] = []
    seen_creatures: set[str] = set()
    for row in candidates:
        creature_key = str(row.get("creatureObjectPath") or row.get("creature") or "").casefold()
        if creature_key in seen_creatures:
            continue
        seen_creatures.add(creature_key)
        best_by_creature.append(row)
    bounded_limit = max(1, min(int(limit), 10))
    selected = best_by_creature[:bounded_limit]
    for rank, row in enumerate(selected, start=1):
        row["rank"] = rank

    coverage = dict(ranking_report.get("coverage") or {})
    coverage.update(
        {
            "rankedForNodeResource": len(best_by_creature),
            "nonRankedForNodeResource": non_ranked,
            "returned": len(selected),
            "omitted": max(0, len(best_by_creature) - len(selected)),
        }
    )
    return {
        "schema": RANKING_RESULT_SCHEMA,
        "dataset": catalog.get("dataset") or {},
        "node": {
            "id": node.get("id"),
            "name": node.get("name"),
            "objectPath": node.get("objectPath"),
        },
        "resource": {
            **resource,
            "harvestComponentPackagePath": component_package,
        },
        "methodology": {
            "metric": "engineComparisonIndex",
            "scoreBasis": "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD",
            "warning": "这是引擎系数比较指数，不是实测每秒产量或最终掉落倍率。",
        },
        "scopeStatus": "SCANNED_CREATURES_ONLY",
        "claimsGlobalTop": False,
        "coverage": coverage,
        "items": selected,
    }


def _asset_object_path(path: Path, content_root: Path) -> str:
    relative = path.resolve().relative_to(content_root.resolve()).with_suffix("").as_posix()
    return f"/Game/{relative}.{path.stem}"


def extract_resource_node(path: Path, content_root: Path) -> dict[str, Any]:
    """Decode one supported FoliageType definition into a source-traced node."""

    package = parse_uasset_package(path)
    names = package.get("names")
    imports = package.get("imports")
    exports = package.get("exports")
    if not isinstance(names, list) or not isinstance(imports, list) or not isinstance(exports, list):
        raise ValueError(f"Package maps were unavailable: {path}")
    foliage_export = next(
        (
            item
            for item in exports
            if isinstance(item, dict)
            and str(item.get("class_name") or "")
            in {"FoliageType_InstancedStaticMesh", "FoliageType_Actor"}
        ),
        None,
    )
    if not isinstance(foliage_export, dict):
        raise NotFoliageTypeAsset(f"No supported FoliageType export: {path}")
    asset_class = str(foliage_export.get("class_name") or "")
    is_actor_definition = asset_class == "FoliageType_Actor"
    export_data = export_data_bytes(package, foliage_export)
    properties: dict[str, dict[str, Any]] = {}
    for block in cdo_property_tag_blocks(export_data, names):
        name = str(block.get("name") or "")
        if name not in {"Mesh", "ActorClass", "AttachedComponentClass"}:
            continue
        parsed = parse_property_block_value(export_data, block, names, imports, exports)
        properties[name] = parsed

    object_path = _asset_object_path(path, content_root)
    object_parts = [part for part in object_path.split("/") if part]
    package_namespace = object_parts[1] if len(object_parts) > 1 else ""
    node_id = build_node_id(object_path)
    mesh_prop = properties.get("Mesh") or {}
    actor_prop = properties.get("ActorClass") or {}
    component_prop = properties.get("AttachedComponentClass") or {}
    if is_actor_definition:
        mesh = {
            "status": "NOT_APPLICABLE",
            "name": "",
            "packagePath": "",
            "objectPath": "",
            "reasonCode": "FOLIAGE_ACTOR_USES_ACTOR_CLASS",
        }
        actor_class = resolve_object_reference(
            int(actor_prop.get("package_index") or 0), imports, exports
        )
    else:
        mesh = resolve_object_reference(
            int(mesh_prop.get("package_index") or 0), imports, exports
        )
        actor_class = {
            "status": "NOT_APPLICABLE",
            "name": "",
            "packagePath": "",
            "objectPath": "",
            "reasonCode": "INSTANCED_STATIC_MESH_DEFINITION",
        }
    component = resolve_object_reference(
        int(component_prop.get("package_index") or 0), imports, exports
    )
    gaps: list[str] = []
    if not is_actor_definition and mesh.get("status") != CONFIRMED:
        gaps.append("MESH_NOT_RECOVERED")
    if is_actor_definition and actor_class.get("status") != CONFIRMED:
        gaps.append("ACTOR_CLASS_NOT_RECOVERED")
    if component.get("status") != CONFIRMED:
        gaps.append("ATTACHED_HARVEST_COMPONENT_NOT_RECOVERED")
    if not is_actor_definition:
        mesh["evidence"] = {
            "property": "Mesh",
            "offsets": mesh_prop.get("raw_offsets") or {},
            "confidence": mesh_prop.get("confidence") or "",
        }
    if is_actor_definition:
        actor_class["evidence"] = {
            "property": "ActorClass",
            "offsets": actor_prop.get("raw_offsets") or {},
            "confidence": actor_prop.get("confidence") or "",
        }
    component["evidence"] = {
        "property": "AttachedComponentClass",
        "offsets": component_prop.get("raw_offsets") or {},
        "confidence": component_prop.get("confidence") or "",
    }
    return {
        "id": node_id,
        "name": str(foliage_export.get("object_name") or path.stem),
        "objectPath": object_path,
        "nodeType": "FOLIAGE_ACTOR" if is_actor_definition else "FOLIAGE",
        "assetClass": asset_class,
        "assetOrigin": {
            "packageNamespace": package_namespace,
            "meaning": "PACKAGE_NAMESPACE_NOT_MAP_USAGE",
        },
        "mesh": mesh,
        "actorClass": actor_class,
        "harvestComponent": component,
        "resources": {"status": NOT_INDEXED, "count": None, "items": []},
        "mapReferences": {
            "status": NOT_INDEXED,
            "count": None,
            "items": [],
            "coverage": {"filesScanned": 0, "roots": []},
        },
        "mapUsage": _map_usage_summary([]),
        "image": {"status": "NOT_EXTRACTED"},
        "evidence": {
            "sourceSha256": _sha256_file(path),
            "parser": "uasset_property_tag",
        },
        "gaps": gaps,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_ASCII_UNREAL_PATH = re.compile(rb"/Game/[A-Za-z0-9_./-]{1,2048}")
_UTF16_UNREAL_PATH = re.compile(
    re.escape("/Game/".encode("utf-16-le"))
    + rb"(?:[A-Za-z0-9_./-]\x00){1,2048}"
)


def _serialized_unreal_packages(path: Path) -> set[str]:
    """Extract bounded ASCII/UTF-16 Unreal package tokens in one linear scan."""

    packages: set[str] = set()
    tail = b""
    overlap = 4096
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(4 * 1024 * 1024)
            if not chunk:
                return packages
            data = tail + chunk
            for match in _ASCII_UNREAL_PATH.finditer(data):
                token = match.group(0).decode("ascii", errors="ignore")
                package = canonical_package_path(token)
                if package:
                    packages.add(package.casefold())
            for match in _UTF16_UNREAL_PATH.finditer(data):
                token = match.group(0).decode("utf-16-le", errors="ignore")
                package = canonical_package_path(token)
                if package:
                    packages.add(package.casefold())
            tail = data[-overlap:]


def _logical_content_path(path: Path, content_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(content_root.resolve()).with_suffix("").as_posix()
        return f"/Game/{relative}"
    except ValueError:
        return path.with_suffix("").name


_CANONICAL_PLAYABLE_MAP_FAMILIES = frozenset(
    {
        "Aberration",
        "Extinction",
        "Fjordur",
        "Genesis",
        "Genesis2",
        "LostColony",
        "LostIsland",
        "Ragnarok",
        "ScorchedEarth",
        "TheCenter",
        "TheIsland",
        "Valguero",
    }
)


def _normalize_map_family(value: str) -> str:
    aliases = {
        "TheIslandSubMaps": "TheIsland",
        "TheIsland_sharedassets": "TheIsland",
    }
    return aliases.get(value, value)


def _map_family_from_object_path(object_path: object) -> str:
    """Return an evidence-path map family, without treating asset origin as use."""

    parts = [part for part in str(object_path or "").split("/") if part]
    if not parts:
        return ""
    try:
        pcg_index = parts.index("PCG_Biomes")
    except ValueError:
        pcg_index = -1
    if pcg_index >= 0 and pcg_index + 1 < len(parts):
        return _normalize_map_family(parts[pcg_index + 1])
    if len(parts) >= 3 and parts[0] == "Game" and parts[1] == "Maps":
        return _normalize_map_family(parts[2])
    if (
        len(parts) >= 5
        and parts[0] == "Game"
        and parts[1] == "__ExternalActors__"
        and parts[2] == "Maps"
    ):
        return _normalize_map_family(parts[3])
    if len(parts) >= 2 and parts[0] == "Game":
        return parts[1]
    return ""


def _is_auxiliary_map_evidence(object_path: object, family: str) -> bool:
    text = str(object_path or "")
    return bool(
        family not in _CANONICAL_PLAYABLE_MAP_FAMILIES
        or re.search(
            r"/(?:TestMaps?(?:Area)?[^/]*|Art_Tools|Developers|QA_Scripts|Repros|"
            r"SampleIsland|Templates?|Preview[^/]*)/",
            text,
            flags=re.IGNORECASE,
        )
    )


def _map_usage_summary(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    families: dict[str, dict[str, Any]] = {}
    evidence_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        family = str(item.get("mapFamily") or "")
        map_kind = str(item.get("mapKind") or "")
        if not family or map_kind != "PLAYABLE_MAP_EVIDENCE":
            continue
        raw_weight = item.get("evidenceCount", 1)
        weight = (
            max(1, int(raw_weight))
            if isinstance(raw_weight, int) and not isinstance(raw_weight, bool)
            else 1
        )
        evidence_count += weight
        relation = str(item.get("relation") or "UNKNOWN")
        summary = families.setdefault(
            family,
            {
                "mapFamily": family,
                "mapKind": "PLAYABLE_MAP",
                "evidenceCount": 0,
                "evidenceTypes": set(),
            },
        )
        summary["evidenceCount"] += weight
        summary["evidenceTypes"].add(relation)
    family_items = []
    for summary in families.values():
        family_items.append(
            {
                **summary,
                "evidenceTypes": sorted(summary["evidenceTypes"]),
            }
        )
    family_items.sort(key=lambda item: str(item["mapFamily"]).casefold())
    return {
        "status": "PARTIAL",
        "claimsCompleteMapUsage": False,
        "familyCount": len(family_items),
        "evidenceCount": evidence_count,
        "families": family_items,
        "unindexedEvidenceKinds": [
            "DEPENDENCY_CLOSURE_BEYOND_PCG",
            "RUNTIME_SPAWNER_OR_SCRIPT",
        ],
    }


def _attach_map_usage_summary(node: dict[str, Any]) -> None:
    references = node.get("mapReferences")
    items = references.get("items") if isinstance(references, dict) else []
    node["mapUsage"] = _map_usage_summary(
        item for item in items if isinstance(item, dict)
    ) if isinstance(items, list) else _map_usage_summary([])


def scan_direct_map_references(
    nodes: Iterable[dict[str, Any]],
    map_roots: Iterable[Path],
    *,
    content_root: Path,
    max_files: int = 0,
    cache_path: Path | None = None,
    refresh_cache: bool = False,
    checkpoint_every: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan .umap bytes for exact node package paths.

    This proves direct serialized references only.  Indirect use through terrain
    generators, data tables, or other dependency assets remains explicitly
    ``NOT_INDEXED``.
    """

    result = [deepcopy(node) for node in nodes]
    roots = [Path(root).resolve() for root in map_roots]
    logical_roots = [_logical_content_path(root, content_root) for root in roots]
    map_paths: list[Path] = []
    discovery_failures: list[str] = []
    for root in roots:
        if not root.exists():
            discovery_failures.append(_logical_content_path(root, content_root))
            continue
        if root.is_file() and root.suffix.casefold() == ".umap":
            map_paths.append(root)
        elif root.is_dir():
            map_paths.extend(path for path in root.rglob("*.umap") if path.is_file())
        else:
            discovery_failures.append(_logical_content_path(root, content_root))
    map_paths = sorted(set(map_paths))
    files_discovered = len(map_paths)
    truncated = max_files > 0 and files_discovered > max_files
    if max_files > 0:
        map_paths = map_paths[:max_files]

    package_to_node: dict[str, str] = {}
    for node in result:
        package_path = canonical_package_path(node.get("objectPath"))
        node_id = str(node.get("id") or "")
        if package_path and node_id:
            package_to_node[package_path.casefold()] = node_id

    references: dict[str, list[dict[str, Any]]] = {
        str(node.get("id") or ""): [] for node in result
    }
    scan_cache = (
        MapReferenceScanCache(
            Path(cache_path),
            node_packages=package_to_node,
            refresh=refresh_cache,
            checkpoint_every=checkpoint_every,
        )
        if cache_path is not None
        else None
    )
    failures: list[str] = list(discovery_failures)
    scanned = 0
    try:
        for map_path in map_paths:
            try:
                scanned += 1
                map_object_path = _logical_content_path(map_path, content_root)
                if scan_cache is not None:
                    matched_packages, _cache_hit = scan_cache.get_or_scan(
                        map_path, _serialized_unreal_packages
                    )
                else:
                    matched_packages = {
                        package
                        for package in _serialized_unreal_packages(map_path)
                        if package in package_to_node
                    }
                matched_node_ids = {
                    package_to_node[package]
                    for package in matched_packages
                    if package in package_to_node
                }
                for node_id in matched_node_ids:
                    family = _map_family_from_object_path(map_object_path)
                    references[node_id].append(
                        {
                            "id": "map_"
                            + hashlib.sha256(map_object_path.encode("utf-8")).hexdigest()[:20],
                            "name": map_path.stem,
                            "objectPath": map_object_path,
                            "mapFamily": family,
                            "mapKind": (
                                "AUXILIARY_MAP_EVIDENCE"
                                if _is_auxiliary_map_evidence(map_object_path, family)
                                else "PLAYABLE_MAP_EVIDENCE"
                            ),
                            "relation": "DIRECT_PACKAGE_REFERENCE",
                            "evidenceType": "UMAP_DIRECT_PACKAGE_REFERENCE",
                            "evidenceStatus": CONFIRMED,
                            "usageStatus": "CANDIDATE",
                        }
                    )
            except OSError:
                failures.append(_logical_content_path(map_path, content_root))
    finally:
        if scan_cache is not None:
            scan_cache.flush()

    scan_status = (
        "DIRECT_SCAN_COMPLETE"
        if not failures and not truncated
        else "DIRECT_SCAN_PARTIAL"
    )
    coverage = {
        "status": scan_status,
        "filesDiscovered": files_discovered,
        "filesScanned": scanned,
        "truncated": truncated,
        "maxFiles": max_files if max_files > 0 else None,
        "failures": len(failures),
        "failedMaps": failures[:20],
        "roots": logical_roots,
        "matcher": "SERIALIZED_UNREAL_PACKAGE_TOKEN",
        "indirectReferences": NOT_INDEXED,
        "cache": (
            scan_cache.coverage()
            if scan_cache is not None
            else {"status": "DISABLED", "hits": 0, "misses": scanned}
        ),
    }
    for node in result:
        items = sorted(
            references.get(str(node.get("id") or ""), []),
            key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("objectPath") or "")),
        )
        node["mapReferences"] = {
            "status": scan_status,
            "count": len(items),
            "items": items,
            "coverage": coverage,
            "indirectStatus": NOT_INDEXED,
        }
        _attach_map_usage_summary(node)
    return result, coverage


def scan_pcg_map_references(
    nodes: Iterable[dict[str, Any]],
    pcg_roots: Iterable[Path],
    *,
    content_root: Path,
    max_files: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add confirmed PCG-biome dependency evidence to resource nodes.

    A PCG biome asset proves that a named map family's generation configuration
    references the FoliageType package.  It does not prove a spawned coordinate,
    so the relation deliberately remains a candidate rather than a placement.
    """

    result = [deepcopy(node) for node in nodes]
    roots = [Path(root).resolve() for root in pcg_roots]
    logical_roots = [_logical_content_path(root, content_root) for root in roots]
    pcg_paths: list[tuple[Path, str]] = []
    discovery_failures: list[str] = []
    for root in roots:
        if not root.exists():
            discovery_failures.append(_logical_content_path(root, content_root))
            continue
        if root.is_file() and root.suffix.casefold() == ".uasset":
            family = _map_family_from_object_path(_logical_content_path(root, content_root))
            if family:
                pcg_paths.append((root, family))
        elif root.is_dir():
            for path in root.rglob("*.uasset"):
                if not path.is_file():
                    continue
                try:
                    relative = path.resolve().relative_to(root)
                    family = relative.parts[0] if len(relative.parts) > 1 else ""
                except ValueError:
                    family = ""
                if family:
                    pcg_paths.append((path.resolve(), family))
        else:
            discovery_failures.append(_logical_content_path(root, content_root))
    pcg_paths = sorted(set(pcg_paths), key=lambda item: str(item[0]).casefold())
    files_discovered = len(pcg_paths)
    truncated = max_files > 0 and files_discovered > max_files
    if max_files > 0:
        pcg_paths = pcg_paths[:max_files]

    package_to_node: dict[str, str] = {}
    for node in result:
        package_path = canonical_package_path(node.get("objectPath"))
        node_id = str(node.get("id") or "")
        if package_path and node_id:
            package_to_node[package_path.casefold()] = node_id

    references: dict[str, list[dict[str, Any]]] = {}
    for node in result:
        node_id = str(node.get("id") or "")
        existing = node.get("mapReferences")
        existing_items = existing.get("items") if isinstance(existing, dict) else []
        references[node_id] = [
            dict(item)
            for item in existing_items
            if isinstance(item, dict)
        ] if isinstance(existing_items, list) else []

    failures: list[str] = list(discovery_failures)
    scanned = 0
    families: set[str] = set()
    for pcg_path, family in pcg_paths:
        try:
            scanned += 1
            families.add(family)
            object_path = _logical_content_path(pcg_path, content_root)
            matched_node_ids = {
                package_to_node[package]
                for package in _serialized_unreal_packages(pcg_path)
                if package in package_to_node
            }
            for node_id in matched_node_ids:
                references[node_id].append(
                    {
                        "id": "pcg_"
                        + hashlib.sha256(object_path.encode("utf-8")).hexdigest()[:20],
                        "name": pcg_path.stem,
                        "objectPath": object_path,
                        "mapFamily": family,
                        "mapKind": "PLAYABLE_MAP_EVIDENCE",
                        "relation": "PCG_BIOME_REFERENCE",
                        "evidenceStatus": CONFIRMED,
                        "usageStatus": "CANDIDATE",
                    }
                )
        except OSError:
            failures.append(_logical_content_path(pcg_path, content_root))

    scan_status = (
        "PCG_BIOME_SCAN_COMPLETE"
        if not failures and not truncated
        else "PCG_BIOME_SCAN_PARTIAL"
    )
    coverage = {
        "status": scan_status,
        "filesDiscovered": files_discovered,
        "filesScanned": scanned,
        "truncated": truncated,
        "maxFiles": max_files if max_files > 0 else None,
        "failures": len(failures),
        "failedAssets": failures[:20],
        "roots": logical_roots,
        "families": sorted(families, key=str.casefold),
        "matcher": "SERIALIZED_UNREAL_PACKAGE_TOKEN",
        "relation": "PCG_BIOME_REFERENCE",
    }
    for node in result:
        node_id = str(node.get("id") or "")
        deduplicated = {
            (
                str(item.get("relation") or ""),
                str(item.get("objectPath") or "").casefold(),
            ): item
            for item in references.get(node_id, [])
        }
        items = sorted(
            deduplicated.values(),
            key=lambda item: (
                str(item.get("mapFamily") or "").casefold(),
                str(item.get("relation") or ""),
                str(item.get("objectPath") or ""),
            ),
        )
        existing = node.get("mapReferences")
        existing_coverage = (
            existing.get("coverage") if isinstance(existing, dict) else {}
        )
        node["mapReferences"] = {
            "status": "REFERENCE_SCAN_COMPLETE"
            if scan_status == "PCG_BIOME_SCAN_COMPLETE"
            else "REFERENCE_SCAN_PARTIAL",
            "count": len(items),
            "items": items,
            "coverage": {
                "direct": deepcopy(existing_coverage)
                if isinstance(existing_coverage, dict)
                else {},
                "pcgBiome": coverage,
            },
            "indirectStatus": scan_status,
        }
        _attach_map_usage_summary(node)
    return result, coverage


def _external_actor_identity(path: Path, content_root: Path) -> tuple[str, str] | None:
    """Recover the owning World package and playable family from WP storage."""

    try:
        relative = path.resolve().relative_to(content_root.resolve())
    except ValueError:
        return None
    parts = list(relative.parts)
    try:
        marker = parts.index("__ExternalActors__")
    except ValueError:
        return None
    owner_parts = parts[marker + 1 : -3]
    if len(owner_parts) < 2:
        return None
    if owner_parts[0] == "Maps":
        raw_family = owner_parts[1]
        family = "TheIsland" if raw_family == "TheIslandSubMaps" else raw_family
    elif owner_parts[0] == "Genesis":
        family = "Genesis"
    else:
        return None
    if _is_auxiliary_map_evidence("/".join(owner_parts), family):
        return None
    return family, "/Game/" + "/".join(owner_parts)


def _external_actor_files(roots: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix.casefold() == ".uasset":
            paths.append(root.resolve())
        elif root.is_dir():
            paths.extend(path.resolve() for path in root.rglob("*.uasset") if path.is_file())
    return sorted(set(paths), key=lambda path: str(path).casefold())


def _stream_rg_paths(
    command: list[str],
    *,
    input_text: str | None = None,
) -> tuple[list[Path], int]:
    """Run ripgrep with bounded line-by-line output consumption."""

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if input_text is not None and process.stdin is not None:
        process.stdin.write(input_text)
        process.stdin.close()
    paths: list[Path] = []
    if process.stdout is not None:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                paths.append(Path(line).resolve())
        process.stdout.close()
    return_code = process.wait()
    return paths, return_code


def scan_world_partition_external_actor_references(
    nodes: Iterable[dict[str, Any]],
    external_roots: Iterable[Path],
    *,
    content_root: Path,
    max_files: int = 0,
    prefer_rg: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Index exact node-package edges stored in World Partition actor packages.

    Evidence is aggregated per node and owning World to keep the catalog bounded;
    a few concrete actor packages remain as reproducible examples.
    """

    result = [deepcopy(node) for node in nodes]
    roots = [Path(root).resolve() for root in external_roots]
    package_to_node: dict[str, str] = {}
    canonical_patterns: list[str] = []
    for node in result:
        package = canonical_package_path(node.get("objectPath"))
        node_id = str(node.get("id") or "")
        if package and node_id:
            package_to_node[package.casefold()] = node_id
            canonical_patterns.append(package)

    matched: dict[tuple[str, str, str], dict[str, Any]] = {}
    failures: list[str] = []
    files_discovered = 0
    files_scanned = 0
    files_parsed = 0
    candidate_files_matched = 0
    truncated = False
    rg = shutil.which("rg") if prefer_rg and max_files <= 0 else None
    if rg and canonical_patterns:
        for root in roots:
            if not root.exists():
                failures.append(_logical_content_path(root, content_root))
                continue
            discovered_paths, discovered_code = _stream_rg_paths(
                [rg, "--files", "-g", "*.uasset", "--", str(root)]
            )
            if discovered_code not in {0, 1}:
                failures.append(_logical_content_path(root, content_root))
                continue
            files_discovered += len(discovered_paths)
            ascii_candidates, ascii_code = _stream_rg_paths(
                [
                    rg,
                    "-a",
                    "-l",
                    "-F",
                    "-f",
                    "-",
                    "-g",
                    "*.uasset",
                    "--",
                    str(root),
                ],
                input_text="\n".join(sorted(set(canonical_patterns))) + "\n",
            )
            utf16_candidates, utf16_code = _stream_rg_paths(
                [
                    rg,
                    "-a",
                    "-l",
                    "-P",
                    r"\x2f\x00G\x00a\x00m\x00e\x00\x2f\x00",
                    "-g",
                    "*.uasset",
                    "--",
                    str(root),
                ]
            )
            if ascii_code not in {0, 1} or utf16_code not in {0, 1}:
                failures.append(_logical_content_path(root, content_root))
                continue
            files_scanned += len(discovered_paths)
            candidate_paths = sorted(
                set(ascii_candidates) | set(utf16_candidates),
                key=lambda path: str(path).casefold(),
            )
            candidate_files_matched += len(candidate_paths)
            for actor_path in candidate_paths:
                try:
                    files_parsed += 1
                    identity = _external_actor_identity(actor_path, content_root)
                    if identity is None:
                        continue
                    family, world_path = identity
                    for package in _serialized_unreal_packages(actor_path):
                        node_id = package_to_node.get(package)
                        if not node_id:
                            continue
                        key = (node_id, family, world_path)
                        aggregate = matched.setdefault(
                            key, {"count": 0, "examples": []}
                        )
                        aggregate["count"] += 1
                        example = _logical_content_path(actor_path, content_root)
                        if (
                            example not in aggregate["examples"]
                            and len(aggregate["examples"]) < 3
                        ):
                            aggregate["examples"].append(example)
                except OSError:
                    failures.append(_logical_content_path(actor_path, content_root))
    else:
        valid_roots: list[Path] = []
        for root in roots:
            if root.exists():
                valid_roots.append(root)
            else:
                failures.append(_logical_content_path(root, content_root))
        actor_paths = _external_actor_files(valid_roots)
        files_discovered = len(actor_paths)
        truncated = max_files > 0 and files_discovered > max_files
        if max_files > 0:
            actor_paths = actor_paths[:max_files]
        for actor_path in actor_paths:
            try:
                files_scanned += 1
                files_parsed += 1
                identity = _external_actor_identity(actor_path, content_root)
                if identity is None:
                    continue
                family, world_path = identity
                for package in _serialized_unreal_packages(actor_path):
                    node_id = package_to_node.get(package)
                    if not node_id:
                        continue
                    key = (node_id, family, world_path)
                    aggregate = matched.setdefault(key, {"count": 0, "examples": []})
                    aggregate["count"] += 1
                    example = _logical_content_path(actor_path, content_root)
                    if example not in aggregate["examples"] and len(aggregate["examples"]) < 3:
                        aggregate["examples"].append(example)
            except OSError:
                failures.append(_logical_content_path(actor_path, content_root))

    references: dict[str, list[dict[str, Any]]] = {}
    for node in result:
        node_id = str(node.get("id") or "")
        existing = node.get("mapReferences")
        items = existing.get("items") if isinstance(existing, dict) else []
        references[node_id] = [
            dict(item) for item in items if isinstance(item, dict)
        ] if isinstance(items, list) else []
    for (node_id, family, world_path), aggregate in matched.items():
        references[node_id].append(
            {
                "id": "wp_"
                + hashlib.sha256(f"{node_id}|{world_path}".encode("utf-8")).hexdigest()[:20],
                "name": world_path.rsplit("/", 1)[-1],
                "objectPath": world_path,
                "mapFamily": family,
                "mapKind": "PLAYABLE_MAP_EVIDENCE",
                "relation": "WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE",
                "evidenceStatus": CONFIRMED,
                "usageStatus": "CANDIDATE",
                "evidenceCount": int(aggregate["count"]),
                "evidenceExamples": sorted(aggregate["examples"]),
            }
        )

    scan_status = (
        "WORLD_PARTITION_EXTERNAL_ACTOR_SCAN_COMPLETE"
        if not failures and not truncated
        else "WORLD_PARTITION_EXTERNAL_ACTOR_SCAN_PARTIAL"
    )
    families = sorted({family for _node, family, _world in matched}, key=str.casefold)
    coverage = {
        "status": scan_status,
        "filesDiscovered": files_discovered,
        "filesScanned": files_scanned,
        "candidateFilesMatched": candidate_files_matched,
        "filesParsed": files_parsed,
        "truncated": truncated,
        "maxFiles": max_files if max_files > 0 else None,
        "failures": len(failures),
        "failedRootsOrAssets": failures[:20],
        "roots": [_logical_content_path(root, content_root) for root in roots],
        "families": families,
        "matchedNodes": len({node_id for node_id, _family, _world in matched}),
        "aggregatedRelations": len(matched),
        "matcher": (
            "RIPGREP_ASCII_AND_UTF16_CANDIDATES_THEN_EXACT_TOKEN_PARSE"
            if rg
            else "SERIALIZED_UNREAL_PACKAGE_TOKEN"
        ),
        "relation": "WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE",
    }
    for node in result:
        node_id = str(node.get("id") or "")
        items = sorted(
            references.get(node_id, []),
            key=lambda item: (
                str(item.get("mapFamily") or "").casefold(),
                str(item.get("relation") or ""),
                str(item.get("objectPath") or ""),
            ),
        )
        existing = node.get("mapReferences")
        existing_coverage = existing.get("coverage") if isinstance(existing, dict) else {}
        node["mapReferences"] = {
            "status": "REFERENCE_SCAN_COMPLETE"
            if scan_status.endswith("_COMPLETE")
            else "REFERENCE_SCAN_PARTIAL",
            "count": len(items),
            "items": items,
            "coverage": {
                "prior": deepcopy(existing_coverage)
                if isinstance(existing_coverage, dict)
                else {},
                "worldPartitionExternalActors": coverage,
            },
            "indirectStatus": scan_status,
        }
        _attach_map_usage_summary(node)
    return result, coverage
