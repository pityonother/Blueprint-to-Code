"""Assemble a versioned harvest evaluation catalog from recovered facts."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..contracts import STATIC_COMPLETE_NODE_SCORE_BASIS
from ..evaluation.contracts import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    EVALUATION_CATALOG_SCHEMA,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    TAMED_RIDDEN,
    VARIANT_CANONICAL,
)
from rank_ark_harvest import (
    AssetReader,
    build_class_index,
    build_damage_context,
    sha256_file,
    uasset_object_path,
)
from .ancestry import trace_primal_dino_ancestry
from .asset_projection import _compact_component, build_creature_record
from .constants import (
    AI_SCHEMA,
    CREATURE_CANDIDATE_PATTERNS,
    CREATURE_EXTRACTOR_VERSION,
    DEFAULT_OUTPUT,
    FORMULA_VERSION,
    PREVIOUS_CREATURE_CANDIDATE_PATTERN,
)
from .creature_discovery import (
    _content_root,
    _open_creature_scan_cache,
    discover_creature_candidates,
)


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
            "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
            "formulaVersion": FORMULA_VERSION,
            "policyVersion": HARVEST_RANKING_POLICY_VERSION,
            "usageScope": TAMED_RIDDEN,
            "evaluationMode": "LAZY_NODE_RESOURCE_TOP10",
            "resourceEntrySelection": "RESOURCE_CLASS_AND_ENTRY_INDEX",
            "rideabilityRequirement": "B_ALLOW_RIDING_TRUE",
            "candidateDiscoveryProof": "FILENAME_PATTERN_NOT_GLOBAL_CLASS_REGISTRY",
            "variantGrouping": "DINO_NAME_TAG_THEN_OBJECT_PATH",
            "defaultEvidencePolicy": POLICY_CONFIRMED,
            "defaultVariantPolicy": VARIANT_CANONICAL,
            "defaultAvailabilityPolicy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
            "metric": METRIC_STATIC_TOTAL,
            "scoreBasis": STATIC_COMPLETE_NODE_SCORE_BASIS,
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
