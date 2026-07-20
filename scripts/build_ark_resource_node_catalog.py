#!/usr/bin/env python3
"""Build a bounded, evidence-aware catalog of ARK physical resource nodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.resource_nodes import (  # noqa: E402
    CATALOG_SCHEMA,
    CONFIRMED,
    NotFoliageTypeAsset,
    SOURCE_NOT_AVAILABLE,
    STALE_REVISION,
    attach_component_resources,
    cache_resource_node_thumbnail,
    canonical_package_path,
    component_facts_from_report,
    component_source_freshness,
    extract_resource_node,
    referenced_component_package_paths,
    scan_direct_map_references,
    scan_pcg_map_references,
    scan_world_partition_external_actor_references,
)
from blueprint_translator.resource_node_scan_cache import ResourceNodeScanCache  # noqa: E402
from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    EVALUATION_CATALOG_SCHEMA,
)


PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_DEVKIT_ROOT = Path(r"C:\Program Files\Epic Games\ARKDevkit")
DEFAULT_REPORT = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_ranking_all_resources.full.json"
)
DEFAULT_EVALUATION_CATALOG = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_evaluation_catalog.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json"
)
DEFAULT_SCAN_CACHE = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_scan_cache.json"
)
DEFAULT_MAP_SCAN_CACHE = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "map_reference_scan_cache.json"
)
DEFAULT_COMPONENT_MANIFEST = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "referenced_harvest_components.txt"
)
DEFAULT_IMAGE_CACHE_ROOT = PROJECT_ROOT / "analysis" / "harvest_nodes" / "images"
DEFAULT_SAMPLE_NODES = (
    "PrimalEarth/Environment/Jungle/Vegetation/Trees/UmbrellaTree/"
    "UmbrellaTree_SM_settings.uasset",
    "PrimalEarth/Environment/Shared/Rocks/MetalRocks/Meshes/"
    "SM_MetalRock_01_settings.uasset",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Decode FoliageType resource nodes, join their exact HarvestComponent resources, "
            "and optionally scan maps for direct serialized references."
        )
    )
    parser.add_argument("--devkit-root", type=Path, default=DEFAULT_DEVKIT_ROOT)
    parser.add_argument("--ranking-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--evaluation-catalog",
        type=Path,
        help=(
            "Optional compact all-creature evaluation catalog. When supplied, its "
            "revision is embedded in the node catalog and must match the ranking facts."
        ),
    )
    parser.add_argument(
        "--node",
        action="append",
        default=[],
        help="Node .uasset path or /Game object path; repeatable.",
    )
    parser.add_argument(
        "--node-file",
        type=Path,
        help="UTF-8 text file containing one .uasset or /Game node path per line.",
    )
    parser.add_argument(
        "--discover-root",
        action="append",
        default=[],
        help="Directory to discover *_settings.uasset and *FoliageType*.uasset; repeatable.",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=0,
        help="Optional safety limit after discovery; 0 means all selected candidates.",
    )
    parser.add_argument(
        "--include-unresolved",
        action="store_true",
        help="Keep discovered FoliageType assets whose AttachedComponentClass was not recovered.",
    )
    parser.add_argument(
        "--map-root",
        action="append",
        default=[],
        help="Directory containing .umap files. Default: the DevKit Content/Maps directory.",
    )
    parser.add_argument("--max-map-files", type=int, default=0)
    parser.add_argument("--map-scan-cache", type=Path, default=DEFAULT_MAP_SCAN_CACHE)
    parser.add_argument("--no-map-scan-cache", action="store_true")
    parser.add_argument("--refresh-map-scan-cache", action="store_true")
    parser.add_argument(
        "--map-checkpoint-every",
        type=int,
        default=100,
        help="Persist direct-map cache after this many misses; 0 means final only.",
    )
    parser.add_argument(
        "--pcg-map-root",
        action="append",
        default=[],
        help=(
            "PCG_Biomes directory used as explicit map-family dependency evidence; "
            "repeatable. Defaults to the DevKit PCG_Biomes root."
        ),
    )
    parser.add_argument("--max-pcg-map-files", type=int, default=0)
    parser.add_argument(
        "--external-actor-root",
        action="append",
        default=[],
        help=(
            "World Partition __ExternalActors__ directory; repeatable. Defaults to "
            "Content/__ExternalActors__/Maps."
        ),
    )
    parser.add_argument("--max-external-actor-files", type=int, default=0)
    parser.add_argument("--skip-pcg-map-scan", action="store_true")
    parser.add_argument("--skip-external-actor-scan", action="store_true")
    parser.add_argument("--skip-map-scan", action="store_true")
    parser.add_argument("--scan-cache", type=Path, default=DEFAULT_SCAN_CACHE)
    parser.add_argument("--no-scan-cache", action="store_true")
    parser.add_argument("--refresh-scan-cache", action="store_true")
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=100,
        help="Persist successful node decodes after this many candidates; 0 means final only.",
    )
    parser.add_argument("--image-cache-root", type=Path, default=DEFAULT_IMAGE_CACHE_ROOT)
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="Do not extract the length-prefixed JPEG thumbnail embedded in each node asset.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--component-manifest-output",
        type=Path,
        default=DEFAULT_COMPONENT_MANIFEST,
        help="Write exact HarvestComponent package paths referenced by confirmed nodes.",
    )
    return parser.parse_args(argv)


def _content_root(devkit_root: Path) -> Path:
    return devkit_root / "Projects" / "ShooterGame" / "Content"


def _path_from_input(value: str, content_root: Path) -> Path:
    text = str(value or "").strip().strip("\"'").replace("\\", "/")
    if text.startswith("/Game/"):
        package = text.split(".", 1)[0].removeprefix("/Game/")
        return content_root / Path(package + ".uasset")
    path = Path(text)
    return path if path.is_absolute() else content_root / path


def _logical_path(path: Path, content_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(content_root.resolve()).with_suffix("").as_posix()
        return f"/Game/{relative}"
    except ValueError:
        return path.stem


def _discover_node_candidates(
    root: Path,
    *,
    prefer_rg: bool = True,
) -> tuple[list[Path], str]:
    resolved_root = Path(root).resolve()
    rg = shutil.which("rg") if prefer_rg else None
    if rg:
        completed = subprocess.run(
            [
                rg,
                "--files",
                "-g",
                "*_settings.uasset",
                "-g",
                "*FoliageType*.uasset",
                "-g",
                "FA_*.uasset",
                str(resolved_root),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode in {0, 1}:
            return (
                sorted(
                    {
                        Path(line.strip()).resolve()
                        for line in completed.stdout.splitlines()
                        if line.strip()
                    }
                ),
                "RIPGREP",
            )

    candidates: list[Path] = []
    for directory, _subdirectories, filenames in os.walk(resolved_root):
        base = Path(directory)
        for filename in filenames:
            if filename.endswith("_settings.uasset") or (
                "FoliageType" in filename and filename.endswith(".uasset")
            ) or (filename.startswith("FA_") and filename.endswith(".uasset")):
                candidates.append((base / filename).resolve())
    return sorted(set(candidates)), "OS_WALK"


def _stratified_limit(
    paths: list[Path],
    *,
    content_root: Path,
    limit: int,
) -> list[Path]:
    if limit <= 0 or len(paths) <= limit:
        return list(paths)
    groups: dict[str, list[Path]] = {}
    for path in sorted(paths):
        try:
            relative = path.relative_to(content_root)
            family = relative.parts[0] if relative.parts else "__root__"
        except ValueError:
            family = "__external__"
        groups.setdefault(family.casefold(), []).append(path)
    selected: list[Path] = []
    positions = {key: 0 for key in groups}
    while len(selected) < limit:
        progressed = False
        for key in sorted(groups):
            position = positions[key]
            if position >= len(groups[key]):
                continue
            selected.append(groups[key][position])
            positions[key] = position + 1
            progressed = True
            if len(selected) == limit:
                break
        if not progressed:
            break
    return selected


def discover_node_paths(
    args: argparse.Namespace,
    content_root: Path,
) -> tuple[list[Path], str, dict[str, Any]]:
    requested: list[str] = list(args.node)
    if args.node_file:
        requested.extend(
            line.strip()
            for line in args.node_file.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    paths = [_path_from_input(value, content_root) for value in requested]
    discovery_mode = "EXPLICIT"
    discovery_backends: set[str] = set()
    if args.discover_root:
        discovery_mode = "DISCOVERED"
        for raw_root in args.discover_root:
            root = Path(raw_root)
            if not root.is_absolute():
                root = content_root / root
            if root.is_dir():
                discovered, backend = _discover_node_candidates(root)
                paths.extend(discovered)
                discovery_backends.add(backend)
    if not paths:
        discovery_mode = "REPRESENTATIVE_SAMPLE"
        paths = [content_root / Path(relative) for relative in DEFAULT_SAMPLE_NODES]
    paths = sorted({path.resolve() for path in paths})
    discovered_count = len(paths)
    selection_strategy = "ALL"
    if args.max_nodes > 0:
        if discovery_mode == "DISCOVERED":
            paths = _stratified_limit(
                paths,
                content_root=content_root.resolve(),
                limit=args.max_nodes,
            )
            selection_strategy = "TOP_LEVEL_ROUND_ROBIN"
        else:
            paths = paths[: args.max_nodes]
            selection_strategy = "SORTED_PREFIX"
    return paths, discovery_mode, {
        "backends": sorted(discovery_backends) or ["EXPLICIT"],
        "candidatesDiscovered": discovered_count,
        "candidatesSelected": len(paths),
        "selectionStrategy": selection_strategy,
    }


def _dataset_revision(
    nodes: list[dict[str, Any]],
    ranking_report: dict[str, Any],
    evaluation_catalog: dict[str, Any] | None = None,
) -> str:
    """Fingerprint every semantic node fact plus the ranking dataset it joins."""

    digest = hashlib.sha256()
    digest.update(
        str(
            ranking_report.get("datasetRevision")
            or ranking_report.get("scanManifestHash")
            or ""
        ).encode("utf-8")
    )
    if isinstance(evaluation_catalog, dict):
        digest.update(
            str(evaluation_catalog.get("dataset", {}).get("revision") or "").encode(
                "utf-8"
            )
        )
    for node in sorted(nodes, key=lambda item: str(item.get("objectPath") or "")):
        digest.update(
            json.dumps(
                node,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _evaluation_coverage_summary(
    evaluation_catalog: dict[str, Any] | None,
) -> dict[str, Any]:
    coverage = (
        evaluation_catalog.get("coverage")
        if isinstance(evaluation_catalog, dict)
        else {}
    )
    if not isinstance(coverage, dict):
        coverage = {}
    discovery = coverage.get("candidateDiscovery")
    candidates = (
        discovery.get("candidatesDiscovered") if isinstance(discovery, dict) else None
    )
    return {
        "creatureCandidatesDiscovered": candidates,
        "creatureAssetsCataloged": coverage.get("creatureAssetsCataloged"),
        "speciesCataloged": coverage.get("speciesCataloged"),
        "attacksDecoded": coverage.get("attacksDecoded"),
        "attacksEligibleForScope": coverage.get("attacksEligibleForScope"),
        "attacksConditionalForScope": coverage.get("attacksConditionalForScope"),
        "attacksIneligibleForScope": coverage.get("attacksIneligibleForScope"),
        "claimsAllCreatures": coverage.get("claimsAllCreatures") is True,
    }


def _indirect_map_reference_status(
    pcg_coverage: dict[str, Any],
    external_coverage: dict[str, Any],
) -> str:
    pcg_status = str(pcg_coverage.get("status") or "NOT_INDEXED")
    external_status = str(external_coverage.get("status") or "NOT_INDEXED")
    pcg_indexed = pcg_status != "NOT_INDEXED"
    external_indexed = external_status != "NOT_INDEXED"
    if pcg_indexed and external_indexed:
        return "PCG_AND_WORLD_PARTITION_INDEXED_DEPENDENCY_CLOSURE_PARTIAL"
    if pcg_indexed:
        return "PCG_INDEXED_WORLD_PARTITION_NOT_INDEXED"
    if external_indexed:
        return "WORLD_PARTITION_INDEXED_PCG_NOT_INDEXED"
    return "NOT_INDEXED"


def _node_type_discovery_coverage(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe exactly which asset models are node definitions in this catalog.

    Static meshes, spawned destructible actors, and PCG biome assets participate
    in rendering or placement, but they do not independently define the harvest
    component/resource profile.  Keeping them out of the node count prevents a
    geometry or placement asset from being misreported as a second resource node.
    """

    by_class = Counter(
        str(node.get("assetClass") or "UNKNOWN")
        for node in nodes
        if isinstance(node, dict)
    )
    return {
        "supportedDefinitionClasses": {
            "FoliageType_InstancedStaticMesh": {
                "status": "SUPPORTED_EXACT_PROPERTY_TAGS",
                "decoded": by_class["FoliageType_InstancedStaticMesh"],
                "componentProperty": "AttachedComponentClass",
                "visualProperty": "Mesh",
            },
            "FoliageType_Actor": {
                "status": "SUPPORTED_EXACT_PROPERTY_TAGS",
                "decoded": by_class["FoliageType_Actor"],
                "componentProperty": "AttachedComponentClass",
                "visualProperty": "ActorClass",
            },
        },
        "nonDefinitionAssetModels": {
            "StaticMesh": {
                "status": "NOT_A_NODE_DEFINITION",
                "reasonCode": "GEOMETRY_JOINED_THROUGH_FOLIAGE_DEFINITION",
            },
            "PrimalDestructibleFoliage": {
                "status": "NOT_A_NODE_DEFINITION",
                "reasonCode": "RUNTIME_DESTRUCTION_ACTOR_NO_SEPARATE_HARVEST_PROFILE",
            },
            "PCG": {
                "status": "NOT_A_NODE_DEFINITION",
                "reasonCode": "PLACEMENT_EVIDENCE_NOT_NODE_DEFINITION",
            },
        },
        "claimsAllNodeDefinitionClasses": False,
        "claimBlockers": [
            "DISCOVERY_REMAINS_FILENAME_CANDIDATE_PATTERNS_NOT_ASSET_REGISTRY_CLASS_ENUMERATION"
        ],
    }


def _extract_node_candidate(
    path: Path,
    content_root: Path,
    *,
    extractor: Callable[[Path, Path], dict[str, Any]] = extract_resource_node,
) -> dict[str, Any]:
    try:
        return extractor(path, content_root)
    except NotFoliageTypeAsset:
        return {
            "candidateStatus": "NOT_FOLIAGE_TYPE",
            "objectPath": _logical_path(path, content_root),
        }


def _attach_node_thumbnail(
    node: dict[str, Any],
    path: Path,
    image_root: Path,
    *,
    skip_images: bool,
    cacher: Callable[[Path, Path], dict[str, Any]] = cache_resource_node_thumbnail,
) -> dict[str, Any]:
    result = dict(node)
    if skip_images:
        result["image"] = {
            "status": "NOT_INDEXED",
            "reasonCode": "IMAGE_EXTRACTION_DISABLED",
        }
    else:
        result["image"] = cacher(path, image_root)
    return result


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    content_root = _content_root(args.devkit_root).resolve()
    if not content_root.is_dir():
        raise FileNotFoundError(f"ARK DevKit Content directory not found: {content_root}")
    ranking_report = json.loads(args.ranking_report.read_text(encoding="utf-8-sig"))
    if not isinstance(ranking_report, dict) or not component_facts_from_report(ranking_report):
        raise ValueError("Ranking report does not contain decoded components.")
    evaluation_catalog: dict[str, Any] | None = None
    if args.evaluation_catalog is not None:
        raw_evaluation = json.loads(args.evaluation_catalog.read_text(encoding="utf-8-sig"))
        if not isinstance(raw_evaluation, dict) or raw_evaluation.get("schema") != EVALUATION_CATALOG_SCHEMA:
            raise ValueError("Evaluation catalog schema is missing or invalid.")
        evaluation_dataset = raw_evaluation.get("dataset")
        evaluation_revision = (
            str(evaluation_dataset.get("revision") or "")
            if isinstance(evaluation_dataset, dict)
            else ""
        )
        component_revision = (
            str(evaluation_dataset.get("componentDatasetRevision") or "")
            if isinstance(evaluation_dataset, dict)
            else ""
        )
        ranking_revision = str(ranking_report.get("datasetRevision") or "")
        if len(evaluation_revision) != 64 or any(
            character not in "0123456789abcdef" for character in evaluation_revision
        ):
            raise ValueError("Evaluation catalog dataset revision is missing or invalid.")
        if component_revision != ranking_revision:
            raise ValueError(
                "Evaluation catalog component revision does not match the ranking report."
            )
        evaluation_catalog = raw_evaluation
    paths, discovery_mode, candidate_discovery = discover_node_paths(args, content_root)
    components = component_facts_from_report(ranking_report)
    component_index = {
        canonical_package_path(component.get("objectPath")).casefold(): component
        for component in components
        if isinstance(component, dict) and canonical_package_path(component.get("objectPath"))
    }
    report_sources = ranking_report.get("sources")
    source_rows = report_sources if isinstance(report_sources, list) else []
    hash_cache: dict[Path, str] = {}
    nodes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    skipped_non_resource_nodes: list[str] = []
    skipped_non_foliage_assets: list[str] = []
    scan_cache = None
    if not args.no_scan_cache:
        scan_cache = ResourceNodeScanCache(
            args.scan_cache.resolve(), refresh=bool(args.refresh_scan_cache)
        )
    checkpoint_every = max(0, int(args.checkpoint_every))
    try:
        for candidate_index, path in enumerate(paths, start=1):
            try:
                if scan_cache is None:
                    node = _extract_node_candidate(path, content_root)
                else:
                    node, _cache_hit = scan_cache.get_or_extract(
                        path,
                        lambda candidate: _extract_node_candidate(candidate, content_root),
                    )
                if node.get("candidateStatus") == "NOT_FOLIAGE_TYPE":
                    skipped_non_foliage_assets.append(
                        str(node.get("objectPath") or _logical_path(path, content_root))
                    )
                    continue
                component = node.get("harvestComponent")
                component_confirmed = (
                    isinstance(component, dict) and component.get("status") == CONFIRMED
                )
                if (
                    discovery_mode == "DISCOVERED"
                    and not component_confirmed
                    and not args.include_unresolved
                ):
                    skipped_non_resource_nodes.append(_logical_path(path, content_root))
                    continue
                node = _attach_node_thumbnail(
                    node,
                    path,
                    args.image_cache_root.resolve(),
                    skip_images=bool(args.skip_images),
                )
                node = attach_component_resources(node, components)
                component_package = canonical_package_path(
                    component.get("packagePath") if isinstance(component, dict) else ""
                )
                component_fact = component_index.get(component_package.casefold())
                freshness = (
                    component_source_freshness(
                        component_fact,
                        source_rows,
                        hash_cache=hash_cache,
                    )
                    if isinstance(component_fact, dict)
                    else {
                        "status": SOURCE_NOT_AVAILABLE,
                        "checked": 0,
                        "stale": [],
                        "missing": ["COMPONENT_FACT_NOT_AVAILABLE"],
                    }
                )
                node["componentSourceFreshness"] = freshness
                if freshness["status"] != CONFIRMED:
                    node["resources"] = {
                        "status": freshness["status"],
                        "count": None,
                        "items": [],
                    }
                    gap_code = (
                        "HARVEST_COMPONENT_SOURCE_STALE"
                        if freshness["status"] == STALE_REVISION
                        else "HARVEST_COMPONENT_SOURCE_NOT_AVAILABLE"
                    )
                    node["gaps"] = sorted(set(node.get("gaps") or []) | {gap_code})
                nodes.append(node)
            except Exception as exc:
                failures.append(
                    {
                        "objectPath": _logical_path(path, content_root),
                        "reasonCode": "RESOURCE_NODE_DECODE_FAILED",
                        "detail": str(exc)[:300],
                    }
                )
            finally:
                if (
                    scan_cache is not None
                    and checkpoint_every > 0
                    and candidate_index % checkpoint_every == 0
                ):
                    scan_cache.flush()
    finally:
        if scan_cache is not None:
            scan_cache.flush()

    if args.skip_map_scan:
        map_coverage: dict[str, Any] = {
            "status": "NOT_INDEXED",
            "filesDiscovered": 0,
            "filesScanned": 0,
            "failures": 0,
            "roots": [],
            "indirectReferences": "NOT_INDEXED",
            "claimsCompleteMapUsage": False,
        }
    else:
        raw_map_roots = args.map_root or [str(content_root / "Maps")]
        map_roots = [
            Path(value) if Path(value).is_absolute() else content_root / Path(value)
            for value in raw_map_roots
        ]
        nodes, map_coverage = scan_direct_map_references(
            nodes,
            map_roots,
            content_root=content_root,
            max_files=max(0, int(args.max_map_files)),
            cache_path=(None if args.no_map_scan_cache else args.map_scan_cache.resolve()),
            refresh_cache=bool(args.refresh_map_scan_cache),
            checkpoint_every=max(0, int(args.map_checkpoint_every)),
        )
        direct_coverage = map_coverage
        if args.skip_pcg_map_scan:
            pcg_coverage: dict[str, Any] = {
                "status": "NOT_INDEXED",
                "filesDiscovered": 0,
                "filesScanned": 0,
                "failures": 0,
                "families": [],
            }
        else:
            raw_pcg_roots = args.pcg_map_root or [
                str(
                    content_root
                    / "Art_Tools"
                    / "Level_Tools"
                    / "PCG"
                    / "PCG_Biomes"
                )
            ]
            pcg_roots = [
                Path(value) if Path(value).is_absolute() else content_root / Path(value)
                for value in raw_pcg_roots
            ]
            nodes, pcg_coverage = scan_pcg_map_references(
                nodes,
                pcg_roots,
                content_root=content_root,
                max_files=max(0, int(args.max_pcg_map_files)),
            )
        if args.skip_external_actor_scan:
            external_coverage: dict[str, Any] = {
                "status": "NOT_INDEXED",
                "filesDiscovered": 0,
                "filesScanned": 0,
                "failures": 0,
                "families": [],
                "matchedNodes": 0,
            }
        else:
            raw_external_roots = args.external_actor_root or [
                str(content_root / "__ExternalActors__" / "Maps")
            ]
            external_roots = [
                Path(value) if Path(value).is_absolute() else content_root / Path(value)
                for value in raw_external_roots
            ]
            nodes, external_coverage = scan_world_partition_external_actor_references(
                nodes,
                external_roots,
                content_root=content_root,
                max_files=max(0, int(args.max_external_actor_files)),
            )
        direct_references = 0
        pcg_references = 0
        external_references = 0
        map_families: set[str] = set()
        nodes_with_map_usage = 0
        for node in nodes:
            references = node.get("mapReferences")
            items = references.get("items") if isinstance(references, dict) else []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                relation = str(item.get("relation") or "")
                if relation == "DIRECT_PACKAGE_REFERENCE":
                    direct_references += 1
                elif relation == "PCG_BIOME_REFERENCE":
                    pcg_references += 1
                elif relation == "WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE":
                    external_references += 1
                if item.get("mapKind") == "PLAYABLE_MAP_EVIDENCE" and item.get(
                    "mapFamily"
                ):
                    map_families.add(str(item["mapFamily"]))
            usage = node.get("mapUsage")
            if isinstance(usage, dict) and int(usage.get("familyCount") or 0) > 0:
                nodes_with_map_usage += 1
        all_complete = all(
            str(coverage.get("status") or "").endswith("_COMPLETE")
            for coverage in (direct_coverage, pcg_coverage, external_coverage)
        )
        indirect_status = _indirect_map_reference_status(
            pcg_coverage, external_coverage
        )
        map_coverage = {
            "status": "REFERENCE_SCAN_COMPLETE" if all_complete else "REFERENCE_SCAN_PARTIAL",
            "filesDiscovered": sum(
                int(coverage.get("filesDiscovered") or 0)
                for coverage in (direct_coverage, pcg_coverage, external_coverage)
            ),
            "filesScanned": sum(
                int(coverage.get("filesScanned") or 0)
                for coverage in (direct_coverage, pcg_coverage, external_coverage)
            ),
            "failures": sum(
                int(coverage.get("failures") or 0)
                for coverage in (direct_coverage, pcg_coverage, external_coverage)
            ),
            "roots": sorted(
                {
                    str(root)
                    for coverage in (direct_coverage, pcg_coverage, external_coverage)
                    for root in coverage.get("roots", [])
                }
            ),
            "direct": direct_coverage,
            "pcgBiome": pcg_coverage,
            "worldPartitionExternalActors": external_coverage,
            "referenceCounts": {
                "directMapPackages": direct_references,
                "pcgBiomeDependencies": pcg_references,
                "worldPartitionWorlds": external_references,
            },
            "nodesWithMapUsageEvidence": nodes_with_map_usage,
            "mapFamilies": sorted(map_families, key=str.casefold),
            "indirectReferences": indirect_status,
            "claimsCompleteMapUsage": False,
        }
        node_map_coverage = {
            "status": map_coverage["status"],
            "directStatus": direct_coverage.get("status"),
            "pcgBiomeStatus": pcg_coverage.get("status"),
            "worldPartitionStatus": external_coverage.get("status"),
            "claimsCompleteMapUsage": False,
        }
        for node in nodes:
            references = node.get("mapReferences")
            if isinstance(references, dict):
                references["status"] = map_coverage["status"]
                references["coverage"] = node_map_coverage
                references["indirectStatus"] = map_coverage["indirectReferences"]

    generated_at = datetime.now(timezone.utc).isoformat()
    revision = _dataset_revision(nodes, ranking_report, evaluation_catalog)
    resource_count = sum(
        int(node.get("resources", {}).get("count") or 0)
        for node in nodes
        if isinstance(node.get("resources"), dict)
    )
    confirmed_components = sum(
        1
        for node in nodes
        if isinstance(node.get("harvestComponent"), dict)
        and node["harvestComponent"].get("status") == CONFIRMED
    )
    stale_component_sources = sum(
        1
        for node in nodes
        if node.get("componentSourceFreshness", {}).get("status") == STALE_REVISION
    )
    unavailable_component_sources = sum(
        1
        for node in nodes
        if node.get("componentSourceFreshness", {}).get("status") == SOURCE_NOT_AVAILABLE
    )
    images_available = sum(
        1 for node in nodes if node.get("image", {}).get("status") == "AVAILABLE"
    )
    image_hashes = {
        str(node.get("image", {}).get("sha256") or "")
        for node in nodes
        if node.get("image", {}).get("status") == "AVAILABLE"
    }
    image_hashes.discard("")
    if stale_component_sources:
        source_status = "DRIFTED"
    elif unavailable_component_sources:
        source_status = "PARTIAL"
    else:
        source_status = "CURRENT_AT_GENERATION"
    evaluation_dataset = (
        evaluation_catalog.get("dataset") if isinstance(evaluation_catalog, dict) else {}
    )
    if not isinstance(evaluation_dataset, dict):
        evaluation_dataset = {}
    evaluation_coverage_summary = _evaluation_coverage_summary(evaluation_catalog)
    return {
        "schema": CATALOG_SCHEMA,
        "dataset": {
            "revision": revision,
            "generatedAt": generated_at,
            "sourceStatus": source_status,
            "devkitBuild": None,
            "rankingSchema": ranking_report.get("schema"),
            "rankingGeneratedAt": ranking_report.get("generatedAt"),
            "rankingScanManifestHash": ranking_report.get("scanManifestHash"),
            "rankingDatasetRevision": ranking_report.get("datasetRevision"),
            "componentDatasetRevision": ranking_report.get("datasetRevision"),
            "evaluationSchema": (
                evaluation_catalog.get("schema")
                if isinstance(evaluation_catalog, dict)
                else None
            ),
            "evaluationGeneratedAt": evaluation_dataset.get("generatedAt"),
            "evaluationDatasetRevision": evaluation_dataset.get("revision"),
        },
        "coverage": {
            "discoveryMode": discovery_mode,
            "candidateDiscovery": candidate_discovery,
            "nodeCandidates": len(paths),
            "nodesDecoded": len(nodes),
            "nodesByType": dict(
                sorted(Counter(str(node.get("nodeType") or "UNKNOWN") for node in nodes).items())
            ),
            "nodeTypeDiscovery": _node_type_discovery_coverage(nodes),
            "nodeDecodeFailures": len(failures),
            "nonFoliageAssetsSkipped": len(skipped_non_foliage_assets),
            "nonResourceFoliageCandidatesSkipped": len(skipped_non_resource_nodes),
            "nodesWithConfirmedHarvestComponent": confirmed_components,
            "nodesWithStaleComponentSources": stale_component_sources,
            "nodesWithoutComponentSourceProof": unavailable_component_sources,
            "resourceEntriesRecovered": resource_count,
            "images": {
                "status": "NOT_INDEXED" if args.skip_images else "INDEXED",
                "available": images_available,
                "notRecovered": 0 if args.skip_images else len(nodes) - images_available,
                "uniqueFiles": len(image_hashes),
                "inlineBytes": False,
            },
            "nodeScanCache": (
                scan_cache.coverage()
                if scan_cache is not None
                else {"status": "DISABLED", "hits": 0, "misses": len(paths)}
            ),
            "mapScan": map_coverage,
            "rankingCreatures": ranking_report.get("coverage", {}).get("creaturesLoaded"),
            **evaluation_coverage_summary,
            "claimsAllNodes": False,
        },
        "nodes": sorted(
            nodes,
            key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("id") or "")),
        ),
        "failures": failures,
        "skipped": {
            "reasonCode": "ATTACHED_HARVEST_COMPONENT_NOT_CONFIRMED",
            "count": len(skipped_non_resource_nodes),
            "examples": skipped_non_resource_nodes[:50],
            "nonFoliage": {
                "reasonCode": "NOT_FOLIAGE_TYPE_ASSET",
                "count": len(skipped_non_foliage_assets),
                "examples": skipped_non_foliage_assets[:50],
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    catalog = build_catalog(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    component_paths = referenced_component_package_paths(catalog.get("nodes") or [])
    args.component_manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.component_manifest_output.write_text(
        "\n".join(component_paths) + ("\n" if component_paths else ""),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "schema": catalog["schema"],
                "revision": catalog["dataset"]["revision"],
                "coverage": catalog["coverage"],
                "output": str(args.output.resolve()),
                "componentManifest": str(args.component_manifest_output.resolve()),
                "referencedComponents": len(component_paths),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
