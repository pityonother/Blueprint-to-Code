#!/usr/bin/env python3
"""Build a compact all-creature catalog for lazy ARK harvest Top-10 queries."""

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
from typing import Any, Callable, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    EVALUATION_CATALOG_SCHEMA,
    TAMED_RIDDEN,
    extract_creature_identity,
)
from blueprint_translator.harvest_ranking import (  # noqa: E402
    YIELD_MODEL_VERSION,
    YIELD_SCORE_BASIS,
    extract_creature_attacks,
)
from blueprint_translator.creature_asset_scan_cache import (  # noqa: E402
    CreatureAssetScanCache,
)
from rank_ark_harvest import (  # noqa: E402
    AssetReader,
    build_class_index,
    build_damage_context,
    sha256_file,
    uasset_object_path,
)


DEFAULT_DEVKIT_ROOT = Path(r"C:\Program Files\Epic Games\ARKDevkit")
DEFAULT_RANKING_REPORT = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_ranking_all_resources.full.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "analysis" / "harvest_rankings" / "harvest_evaluation_catalog.json"
)
DEFAULT_AI_OUTPUT = DEFAULT_OUTPUT.with_name("harvest_evaluation_catalog.ai.json")
DEFAULT_SCAN_CACHE = DEFAULT_OUTPUT.with_name("creature_asset_scan_cache.json")
AI_SCHEMA = "ark-harvest-evaluation-catalog-ai/v2"
FORMULA_VERSION = YIELD_MODEL_VERSION
CREATURE_EXTRACTOR_VERSION = "ark-creature-attack-catalog/v3"
CREATURE_CANDIDATE_PATTERNS = ("*Character*.uasset", "*Char_BP*.uasset")
PREVIOUS_CREATURE_CANDIDATE_PATTERN = "*Character_BP*.uasset"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover every PrimalDinoCharacter-derived asset and build a compact "
            "catalog for lazy node/resource Top-10 evaluation."
        )
    )
    parser.add_argument("--devkit-root", type=Path, default=DEFAULT_DEVKIT_ROOT)
    parser.add_argument("--ranking-report", type=Path, default=DEFAULT_RANKING_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ai-output", type=Path, default=DEFAULT_AI_OUTPUT)
    parser.add_argument("--scan-cache", type=Path, default=DEFAULT_SCAN_CACHE)
    parser.add_argument("--no-scan-cache", action="store_true")
    parser.add_argument("--refresh-scan-cache", action="store_true")
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Optional diagnostic limit; 0 scans every discovered candidate.",
    )
    return parser.parse_args(argv)


def _open_creature_scan_cache(
    args: argparse.Namespace,
) -> CreatureAssetScanCache | None:
    if args.no_scan_cache:
        return None
    return CreatureAssetScanCache(
        args.scan_cache.resolve(),
        refresh=bool(args.refresh_scan_cache),
        extractor_version=CREATURE_EXTRACTOR_VERSION,
    )


def _content_root(devkit_root: Path) -> Path:
    return Path(devkit_root) / "Projects" / "ShooterGame" / "Content"


def discover_creature_candidates(
    content_root: Path,
    *,
    prefer_rg: bool = True,
) -> tuple[list[Path], str]:
    """Discover the broad Character-named family, then prove ancestry per asset.

    This remains a filename candidate set rather than a global Unreal class
    registry.  The wider pattern is intentional: current DevKit assets such as
    ``EndBoss_Character`` and ``Trilobite_Character`` are confirmed
    PrimalDinoCharacter descendants but do not contain ``Character_BP``.
    """

    root = Path(content_root).resolve()
    rg = shutil.which("rg") if prefer_rg else None
    if rg:
        completed = subprocess.run(
            [
                rg,
                "--files",
                *(
                    argument
                    for pattern in CREATURE_CANDIDATE_PATTERNS
                    for argument in ("-g", pattern)
                ),
                str(root),
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

    paths: list[Path] = []
    for directory, _subdirectories, filenames in os.walk(root):
        base = Path(directory)
        for filename in filenames:
            folded = filename.casefold()
            if filename.endswith(".uasset") and (
                "character" in folded or "char_bp" in folded
            ):
                paths.append((base / filename).resolve())
    return sorted(set(paths)), "OS_WALK"


def _path_from_parent_reference(
    parent: str,
    *,
    content_root: Path,
    class_index: dict[str, Path],
) -> Path | None:
    text = str(parent or "").strip().strip("\"'").replace("\\", "/")
    if text.startswith("/Game/"):
        package = text.split(".", 1)[0].removeprefix("/Game/")
        candidate = (content_root / Path(package + ".uasset")).resolve()
        return candidate if candidate.is_file() else None
    indexed = class_index.get(text) or class_index.get(text.casefold())
    return indexed.resolve() if isinstance(indexed, Path) and indexed.is_file() else None


def _native_primal_dino(parent: str) -> bool:
    normalized = str(parent or "").strip().casefold()
    return normalized in {
        "primaldinocharacter",
        "/script/shootergame.primaldinocharacter",
    } or normalized.endswith(".primaldinocharacter")


def trace_primal_dino_ancestry(
    path: Path,
    *,
    content_root: Path,
    load_asset: Callable[[Path], dict[str, Any]],
    class_index: dict[str, Path],
    max_depth: int = 64,
) -> dict[str, Any]:
    """Trace full parent paths until the native PrimalDinoCharacter boundary."""

    current = Path(path).resolve()
    source_paths: list[str] = []
    object_chain = [uasset_object_path(current, content_root)]
    seen: set[Path] = set()
    for _depth in range(max(1, int(max_depth))):
        if current in seen:
            return {
                "status": "ANCESTRY_CYCLE",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
            }
        seen.add(current)
        source_paths.append(str(current))
        fact = load_asset(current)
        parent = str(fact.get("parent") or "")
        if not parent:
            return {
                "status": "PARENT_NOT_RECOVERED",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
            }
        object_chain.append(parent)
        if _native_primal_dino(parent):
            return {
                "status": "CONFIRMED",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
            }
        if parent.startswith("/Script/"):
            return {
                "status": "NOT_PRIMAL_DINO_CHARACTER",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
            }
        parent_path = _path_from_parent_reference(
            parent,
            content_root=content_root,
            class_index=class_index,
        )
        if parent_path is None:
            return {
                "status": "PARENT_ASSET_NOT_FOUND",
                "objectPathChain": object_chain,
                "sourcePaths": source_paths,
                "missingParent": parent,
            }
        current = parent_path
    return {
        "status": "ANCESTRY_DEPTH_EXCEEDED",
        "objectPathChain": object_chain,
        "sourcePaths": source_paths,
    }


def _semantic_value(prop: dict[str, Any] | None) -> Any:
    if not isinstance(prop, dict):
        return None
    if str(prop.get("type") or "") == "ObjectProperty":
        return prop.get("object_path") or prop.get("object") or prop.get("value")
    return prop.get("value")


def _effective_properties(
    ancestry: dict[str, Any],
    load_asset: Callable[[Path], dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    paths = [Path(value) for value in ancestry.get("sourcePaths", []) if value]
    for source in reversed(paths):
        fact = load_asset(source)
        rows = fact.get("properties")
        source_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        has_attack_array = any(
            str(row.get("name") or "") == "AttackInfos"
            and str(row.get("type") or "") == "ArrayProperty"
            for row in source_rows
        )
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            if (
                name == "AttackInfos"
                and str(row.get("type") or "") != "ArrayProperty"
                and has_attack_array
            ):
                # Some current DevKit assets expose a low-confidence ghost StructProperty
                # immediately after the real array tag. It is parser noise, not a child
                # override, and must not erase an explicit empty AttackInfos array.
                continue
            if name:
                merged[name] = row
    return list(merged.values())


def _attack_applicability(attack: dict[str, Any]) -> dict[str, Any]:
    if attack.get("skipTamed") is True:
        return {
            "scope": TAMED_RIDDEN,
            "status": "INELIGIBLE",
            "reasonCodes": ["ATTACK_SKIPPED_WHEN_TAMED"],
        }
    if attack.get("onlyOnWildDinos") is True:
        return {
            "scope": TAMED_RIDDEN,
            "status": "INELIGIBLE",
            "reasonCodes": ["ATTACK_ONLY_ON_WILD_DINOS"],
        }
    if attack.get("preventWithRider") is True:
        return {
            "scope": TAMED_RIDDEN,
            "status": "INELIGIBLE",
            "reasonCodes": ["ATTACK_PREVENTED_WITH_RIDER"],
        }
    conditional_reasons: list[str] = []
    if attack.get("useBlueprintCanRiderAttack") is True:
        conditional_reasons.append("BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED")
    if attack.get("useBlueprintAdjustOutputDamage") is True:
        conditional_reasons.append(
            "BLUEPRINT_ADJUST_OUTPUT_DAMAGE_NOT_RECOVERED"
        )
    if conditional_reasons:
        return {
            "scope": TAMED_RIDDEN,
            "status": "CONDITIONAL",
            "reasonCodes": conditional_reasons,
        }
    return {"scope": TAMED_RIDDEN, "status": "ELIGIBLE", "reasonCodes": []}


def _compact_attack(attack: dict[str, Any], creature_object_path: str) -> dict[str, Any]:
    keys = (
        "attackIndex",
        "attackName",
        "damageType",
        "damageTypeObjectPath",
        "baseDamage",
        "attackInterval",
        "riderAttackInterval",
        "skipTamed",
        "skipAI",
        "onlyOnWildDinos",
        "preventWithRider",
        "useBlueprintCanRiderAttack",
        "useBlueprintAdjustOutputDamage",
        "meleeSwingRadius",
        "basicAttack",
        "valueStatus",
        "gaps",
    )
    result = {key: attack.get(key) for key in keys if key in attack}
    result["attackId"] = f"{creature_object_path}#{attack.get('attackIndex')}"
    result["applicability"] = _attack_applicability(attack)
    return result


def _tameability(properties: list[dict[str, Any]]) -> dict[str, Any]:
    rows = {
        str(row.get("name") or ""): row
        for row in properties
        if isinstance(row, dict) and row.get("name")
    }
    boss = _semantic_value(rows.get("bIsBossDino"))
    can_be_tamed = _semantic_value(rows.get("bCanBeTamed"))
    if boss is True:
        return {"status": "PREVENTED", "reasonCodes": ["BOSS_DINO"]}
    if can_be_tamed is False:
        return {"status": "PREVENTED", "reasonCodes": ["CANNOT_BE_TAMED"]}
    if can_be_tamed is True:
        return {"status": "ALLOWED", "reasonCodes": []}
    return {"status": "UNKNOWN", "reasonCodes": ["TAMEABILITY_NOT_RECOVERED"]}


def _rideability(properties: list[dict[str, Any]]) -> dict[str, Any]:
    rows = {
        str(row.get("name") or ""): row
        for row in properties
        if isinstance(row, dict) and row.get("name")
    }
    allow_riding = _semantic_value(rows.get("bAllowRiding"))
    if allow_riding is True:
        return {"status": "ALLOWED", "reasonCodes": []}
    if allow_riding is False:
        return {"status": "PREVENTED", "reasonCodes": ["RIDING_NOT_ALLOWED"]}
    return {"status": "UNKNOWN", "reasonCodes": ["RIDEABILITY_NOT_RECOVERED"]}


def build_creature_record(
    path: Path,
    *,
    content_root: Path,
    load_asset: Callable[[Path], dict[str, Any]],
    ancestry: dict[str, Any],
) -> dict[str, Any]:
    if ancestry.get("status") != "CONFIRMED":
        raise ValueError("Creature ancestry must be confirmed before projection.")
    resolved = Path(path).resolve()
    object_path = uasset_object_path(resolved, content_root)
    properties = _effective_properties(ancestry, load_asset)
    identity = extract_creature_identity(properties, fallback_name=resolved.stem)
    attacks = extract_creature_attacks(properties)
    attack_infos = next(
        (
            row
            for row in properties
            if isinstance(row, dict) and str(row.get("name") or "") == "AttackInfos"
        ),
        None,
    )
    parse = attack_infos.get("array_parse") if isinstance(attack_infos, dict) else None
    if isinstance(parse, dict) and parse.get("parsed") is True:
        attack_status = "DECODED" if attacks else "CONFIRMED_EMPTY"
    elif isinstance(attack_infos, dict) and (
        int(attack_infos.get("declared_size") or 0) == 0
        and attack_infos.get("value") == []
    ):
        attack_status = "CONFIRMED_EMPTY"
    else:
        attack_status = "NOT_RECOVERED"
    gaps: list[str] = []
    if identity["identityStatus"] != "CONFIRMED":
        gaps.append("DINO_NAME_TAG_NOT_RECOVERED")
    if attack_status == "NOT_RECOVERED":
        gaps.append("ATTACK_INFOS_NOT_RECOVERED")
    return {
        "assetId": "creature_" + hashlib.sha256(object_path.encode("utf-8")).hexdigest()[:20],
        **identity,
        "objectPath": object_path,
        "ancestryStatus": "CONFIRMED",
        "parentChain": ancestry.get("objectPathChain") or [],
        "tameability": _tameability(properties),
        "rideability": _rideability(properties),
        "attackCatalogStatus": attack_status,
        "attacks": [_compact_attack(attack, object_path) for attack in attacks],
        "gaps": sorted(gaps),
    }


def _compact_component(component: dict[str, Any]) -> dict[str, Any]:
    resource_entries = []
    for entry in component.get("resourceEntries", []):
        if not isinstance(entry, dict):
            continue
        resource_entries.append(
            {
                key: value
                for key, value in entry.items()
                if key not in {"rawOffsets", "damageTypeEntryValues"}
            }
        )
    damage_entries = []
    for entry in component.get("damageEntries", []):
        if not isinstance(entry, dict):
            continue
        damage_entries.append(
            {key: value for key, value in entry.items() if key != "rawOffsets"}
        )
    keys = (
        "component",
        "objectPath",
        "maxHarvestHealth",
        "harvestHealthGiveResourceInterval",
        "gaps",
        "rankingGaps",
        "informationalGaps",
    )
    return {
        **{key: component.get(key) for key in keys if key in component},
        "resourceEntries": resource_entries,
        "damageEntries": damage_entries,
    }


def _source_fingerprints(
    paths: Iterable[Path],
    *,
    known_hashes: dict[Path, str] | None = None,
) -> tuple[list[dict[str, str]], bool]:
    rows: list[dict[str, str]] = []
    complete = True
    for path in sorted({Path(value).resolve() for value in paths}):
        if not path.is_file():
            complete = False
            continue
        cached_hash = (known_hashes or {}).get(path)
        rows.append({"path": str(path), "sha256": cached_hash or sha256_file(path)})
    return rows, complete


def _revision(payload: dict[str, Any]) -> str:
    coverage = dict(payload.get("coverage") or {})
    coverage.pop("creatureAssetScanCache", None)
    semantic = {
        key: payload.get(key)
        for key in (
            "methodology",
            "coverage",
            "creatures",
            "components",
            "damageTypeParents",
            "resourceDamageOverrides",
            "damageTypeGaps",
            "exclusions",
            "failures",
            "claimBlockers",
            "sources",
        )
    }
    semantic["coverage"] = coverage
    semantic["componentDatasetRevision"] = payload.get("dataset", {}).get(
        "componentDatasetRevision"
    )
    return hashlib.sha256(
        json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    content_root = _content_root(args.devkit_root).resolve()
    if not content_root.is_dir():
        raise FileNotFoundError(f"ARK DevKit Content directory not found: {content_root}")
    ranking_report = json.loads(args.ranking_report.read_text(encoding="utf-8-sig"))
    components = ranking_report.get("components")
    resources = ranking_report.get("resources")
    component_revision = str(ranking_report.get("datasetRevision") or "")
    if not isinstance(components, list) or not components or not isinstance(resources, list):
        raise ValueError("Ranking report does not contain component/resource facts.")
    if len(component_revision) != 64:
        raise ValueError("Ranking report dataset revision is missing or invalid.")

    candidates, discovery_backend = discover_creature_candidates(content_root)
    if args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]
    reader = AssetReader()
    class_index = build_class_index(candidates, content_root=content_root)
    asset_facts: dict[Path, dict[str, Any]] = {}
    scan_cache = _open_creature_scan_cache(args)
    projected_property_names = {
        "AttackInfos",
        "DinoNameTag",
        "DescriptiveName",
        "bIsBossDino",
        "bCanBeTamed",
        "bAllowRiding",
    }

    def extract_asset_fact(resolved: Path) -> dict[str, Any]:
        payload = reader.defaults(resolved)
        properties = payload.get("properties")
        projected = [
            row
            for row in (properties if isinstance(properties, list) else [])
            if isinstance(row, dict)
            if str(row.get("name") or "") in projected_property_names
        ]
        return {
            "parent": reader.generated_class_parent(resolved),
            "properties": projected,
            "warnings": list(payload.get("warnings") or [])[:20],
        }

    def load_asset(path: Path) -> dict[str, Any]:
        resolved = Path(path).resolve()
        if resolved not in asset_facts:
            if scan_cache is None:
                asset_facts[resolved] = extract_asset_fact(resolved)
            else:
                asset_facts[resolved], _cache_hit = scan_cache.get_or_extract(
                    resolved, extract_asset_fact
                )
        return asset_facts[resolved]

    creatures: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    ancestry_counts: Counter[str] = Counter()
    used_creature_paths: set[Path] = set()
    for candidate in candidates:
        try:
            ancestry = trace_primal_dino_ancestry(
                candidate,
                content_root=content_root,
                load_asset=load_asset,
                class_index=class_index,
            )
            status = str(ancestry.get("status") or "UNKNOWN")
            ancestry_counts[status] += 1
            if status != "CONFIRMED":
                exclusions.append(
                    {
                        "objectPath": uasset_object_path(candidate, content_root),
                        "reasonCode": status,
                        **(
                            {"missingParent": ancestry.get("missingParent")}
                            if ancestry.get("missingParent")
                            else {}
                        ),
                    }
                )
                continue
            creature = build_creature_record(
                candidate,
                content_root=content_root,
                load_asset=load_asset,
                ancestry=ancestry,
            )
            creatures.append(creature)
            used_creature_paths.update(
                Path(value).resolve()
                for value in ancestry.get("sourcePaths", [])
                if value
            )
        except Exception as exc:
            failures.append(
                {
                    "objectPath": uasset_object_path(candidate, content_root),
                    "reasonCode": "CREATURE_CANDIDATE_DECODE_FAILED",
                    "detail": str(exc)[:300],
                }
            )
    if scan_cache is not None:
        scan_cache.flush()

    parent_map, overrides, damage_facts, damage_paths, damage_gaps = build_damage_context(
        creatures=creatures,
        resources=[str(value) for value in resources],
        content_root=content_root,
        reader=reader,
    )
    known_hashes = {
        path: value
        for path in used_creature_paths
        if scan_cache is not None
        and (value := scan_cache.content_sha256(path)) is not None
    }
    source_rows, fingerprints_complete = _source_fingerprints(
        used_creature_paths | set(damage_paths), known_hashes=known_hashes
    )
    applicability_counts = Counter(
        str(attack.get("applicability", {}).get("status") or "UNKNOWN")
        for creature in creatures
        for attack in creature.get("attacks", [])
        if isinstance(attack, dict)
    )
    tameability_counts = Counter(
        str(creature.get("tameability", {}).get("status") or "UNKNOWN")
        for creature in creatures
    )
    rideability_counts = Counter(
        str(creature.get("rideability", {}).get("status") or "UNKNOWN")
        for creature in creatures
    )
    attacks = [
        attack
        for creature in creatures
        for attack in creature.get("attacks", [])
        if isinstance(attack, dict)
    ]
    attack_catalog_counts = Counter(
        str(creature.get("attackCatalogStatus") or "UNKNOWN") for creature in creatures
    )
    attacks_complete_count = sum(
        1 for attack in attacks if attack.get("valueStatus") == "CONFIRMED"
    )
    damage_types_with_gaps = sum(1 for values in damage_gaps.values() if values)
    components_with_ranking_gaps = sum(
        1
        for component in components
        if isinstance(component, dict) and component.get("rankingGaps")
    )
    discovered_scope_complete = (
        not failures
        and not any(
            status not in {"CONFIRMED", "NOT_PRIMAL_DINO_CHARACTER"}
            for status in ancestry_counts
        )
        and attack_catalog_counts["NOT_RECOVERED"] == 0
        and attacks_complete_count == len(attacks)
        and tameability_counts["UNKNOWN"] == 0
        and rideability_counts["UNKNOWN"] == 0
        and applicability_counts["CONDITIONAL"] == 0
        and damage_types_with_gaps == 0
        and components_with_ranking_gaps == 0
        and fingerprints_complete
    )
    claim_blockers = [
        "DISCOVERY_IS_FILENAME_PATTERN_NOT_GLOBAL_CLASS_REGISTRY",
        *([] if not failures else ["CREATURE_DECODE_FAILURES"]),
        *(
            []
            if attack_catalog_counts["NOT_RECOVERED"] == 0
            else ["ATTACK_CATALOGS_NOT_RECOVERED"]
        ),
        *([] if attacks_complete_count == len(attacks) else ["ATTACK_FACTS_INCOMPLETE"]),
        *([] if tameability_counts["UNKNOWN"] == 0 else ["TAMEABILITY_NOT_RECOVERED"]),
        *([] if rideability_counts["UNKNOWN"] == 0 else ["RIDEABILITY_NOT_RECOVERED"]),
        *(
            []
            if applicability_counts["CONDITIONAL"] == 0
            else ["DYNAMIC_ATTACK_GATES_NOT_RECOVERED"]
        ),
        *([] if damage_types_with_gaps == 0 else ["DAMAGE_TYPE_FACTS_INCOMPLETE"]),
        *(
            []
            if components_with_ranking_gaps == 0
            else ["HARVEST_COMPONENT_FACTS_INCOMPLETE"]
        ),
        *([] if fingerprints_complete else ["SOURCE_FINGERPRINTS_INCOMPLETE"]),
    ]
    generated_at = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "schema": EVALUATION_CATALOG_SCHEMA,
        "dataset": {
            "revision": "",
            "generatedAt": generated_at,
            "componentDatasetRevision": component_revision,
            "extractorVersion": CREATURE_EXTRACTOR_VERSION,
        },
        "methodology": {
            "formulaVersion": FORMULA_VERSION,
            "usageScope": TAMED_RIDDEN,
            "evaluationMode": "LAZY_NODE_RESOURCE_TOP10",
            "resourceEntrySelection": "RESOURCE_CLASS_AND_ENTRY_INDEX",
            "rideabilityRequirement": "B_ALLOW_RIDING_TRUE",
            "candidateDiscoveryProof": "FILENAME_PATTERN_NOT_GLOBAL_CLASS_REGISTRY",
            "variantGrouping": "DINO_NAME_TAG_THEN_OBJECT_PATH",
            "metric": "estimatedYieldPerNode",
            "scoreBasis": YIELD_SCORE_BASIS,
            "attackCadenceRole": "DIAGNOSTIC_ONLY_NOT_USED_FOR_COMPLETE_NODE_YIELD",
        },
        "coverage": {
            "candidateDiscovery": {
                "backend": discovery_backend,
                "pattern": " + ".join(CREATURE_CANDIDATE_PATTERNS),
                "patterns": list(CREATURE_CANDIDATE_PATTERNS),
                "previousPattern": PREVIOUS_CREATURE_CANDIDATE_PATTERN,
                "outsidePreviousPatternCandidates": sum(
                    "Character_BP" not in candidate.name for candidate in candidates
                ),
                "outsidePreviousPatternCataloged": sum(
                    "Character_BP"
                    not in str(creature.get("objectPath") or "").rsplit("/", 1)[-1]
                    for creature in creatures
                ),
                "candidatesDiscovered": len(candidates),
                "selectionStrategy": "ALL" if args.max_candidates <= 0 else "SORTED_PREFIX",
            },
            "creatureCandidatesClassified": sum(ancestry_counts.values()),
            "ancestryConfirmed": ancestry_counts["CONFIRMED"],
            "ancestryByStatus": dict(sorted(ancestry_counts.items())),
            "creatureAssetsCataloged": len(creatures),
            "speciesCataloged": len(
                {
                    str(creature.get("speciesKey") or creature.get("objectPath") or "")
                    for creature in creatures
                }
            ),
            "tameabilityByStatus": dict(sorted(tameability_counts.items())),
            "rideabilityByStatus": dict(sorted(rideability_counts.items())),
            "attackCatalogByStatus": dict(sorted(attack_catalog_counts.items())),
            "attacksDecoded": len(attacks),
            "attacksComplete": attacks_complete_count,
            "attacksEligibleForScope": applicability_counts["ELIGIBLE"],
            "attacksConditionalForScope": applicability_counts["CONDITIONAL"],
            "attacksIneligibleForScope": applicability_counts["INELIGIBLE"],
            "componentCatalogEntries": len(components),
            "damageTypesDecoded": len(damage_facts),
            "damageTypesWithGaps": damage_types_with_gaps,
            "componentsWithRankingGaps": components_with_ranking_gaps,
            "sourceFingerprintsComplete": fingerprints_complete,
            "creatureAssetScanCache": (
                scan_cache.coverage()
                if scan_cache is not None
                else {
                    "status": "DISABLED",
                    "entries": 0,
                    "hits": 0,
                    "misses": len(asset_facts),
                    "invalidated": 0,
                }
            ),
            "claimsAllCreatures": False,
            "claimsAllDiscoveredCandidates": discovered_scope_complete,
            "claimsGlobalTop": False,
        },
        "claimBlockers": claim_blockers,
        "creatures": sorted(
            creatures,
            key=lambda row: (
                str(row.get("speciesKey") or ""),
                str(row.get("objectPath") or ""),
            ),
        ),
        "components": sorted(
            (_compact_component(row) for row in components if isinstance(row, dict)),
            key=lambda row: str(row.get("objectPath") or ""),
        ),
        "damageTypeParents": dict(sorted(parent_map.items())),
        "resourceDamageOverrides": [
            {
                "sourceDamageType": source,
                "resource": resource,
                "replacementDamageType": replacement,
            }
            for (source, resource), replacement in sorted(overrides.items())
        ],
        "damageTypeGaps": dict(sorted(damage_gaps.items())),
        "exclusions": {
            "total": len(exclusions),
            "byReason": dict(
                sorted(Counter(row["reasonCode"] for row in exclusions).items())
            ),
            "examples": exclusions[:50],
        },
        "failures": {
            "count": len(failures),
            "examples": failures[:50],
        },
        "sources": source_rows,
    }
    payload["dataset"]["revision"] = _revision(payload)
    return payload


def build_ai_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": AI_SCHEMA,
        "dataset": payload.get("dataset"),
        "methodology": payload.get("methodology"),
        "coverage": payload.get("coverage"),
        "exclusions": payload.get("exclusions"),
        "failures": payload.get("failures"),
        "detailArtifact": DEFAULT_OUTPUT.name,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_catalog(args)
    ai_payload = build_ai_view(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.ai_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    args.ai_output.write_text(
        json.dumps(ai_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "revision": payload["dataset"]["revision"],
                "coverage": payload["coverage"],
                "output": str(args.output.resolve()),
                "aiOutput": str(args.ai_output.resolve()),
                "bytes": args.output.stat().st_size,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
