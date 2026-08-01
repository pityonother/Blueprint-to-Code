"""Deterministic, source-bound audit for harvest ranking dominance.

The audit evaluates one unique HarvestComponent/resource/entry identity at a
time.  It never materializes the species x node/resource Cartesian product and
it does not change the ranking policy it is inspecting.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Iterable

from .harvest_catalog_sqlite import (
    SQLiteHarvestCatalog,
    SQLiteHarvestCatalogInvalid,
)
from .harvest_evaluation_catalog import (
    EVALUATION_CATALOG_SCHEMA,
    HARVEST_RANKING_POLICY_VERSION,
    HarvestEvaluationEngine,
)
from .harvest_ranking import YIELD_MODEL_VERSION
from .resource_nodes import canonical_package_path


AUDIT_SCHEMA = "blueprint-to-code.harvest-dominance-audit/v1"
EXPECTED_EXTRACTOR_VERSION = "ark-creature-attack-catalog/v3"
VERIFICATION_PROVES = "production implementation == independent implementation"
VERIFICATION_DOES_NOT_PROVE = "static model == real game"


class HarvestDominanceAuditInvalid(ValueError):
    """Raised when an audit input is stale, inconsistent, or tampered."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise HarvestDominanceAuditInvalid(f"{label} is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise HarvestDominanceAuditInvalid(f"{label} cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise HarvestDominanceAuditInvalid(f"{label} must contain a JSON object.")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_identity(path: Path) -> dict[str, Any]:
    stat = Path(path).stat()
    return {"sha256": _sha256_file(path), "bytes": stat.st_size}


def _normalized_species_key(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _entry_index(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _is_special_variant(object_path: str) -> bool:
    normalized = str(object_path or "").casefold()
    return any(
        token in normalized
        for token in (
            "/mission/",
            "/missions/",
            "_mission",
            "_special",
            "/boss/",
            "_boss",
            "_minion",
            "/event/",
            "_alpha",
            "_beta",
            "_gamma",
            "_ghost",
        )
    )


def _canonical_variant_key(creature: dict[str, Any]) -> tuple[int, int, int, str]:
    object_path = str(creature.get("objectPath") or "")
    normalized = object_path.casefold()
    package_priority = (
        0
        if normalized.startswith("/game/primalearth/dinos/")
        else 1
        if normalized.startswith("/game/asa/dinos/")
        else 2
    )
    return (
        1 if _is_special_variant(object_path) else 0,
        package_priority,
        len(object_path),
        normalized,
    )


def _revision(value: object, label: str) -> str:
    normalized = str(value or "")
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise HarvestDominanceAuditInvalid(
            f"{label} must be a 64-character lowercase revision."
        )
    return normalized


def _validate_identities(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if node_catalog.get("schema") != "ark-resource-node-catalog/v1":
        raise HarvestDominanceAuditInvalid("NODE catalog schema is invalid.")
    if evaluation_catalog.get("schema") != EVALUATION_CATALOG_SCHEMA:
        raise HarvestDominanceAuditInvalid("EVALUATION catalog schema is invalid.")
    node_dataset = node_catalog.get("dataset")
    evaluation_dataset = evaluation_catalog.get("dataset")
    methodology = evaluation_catalog.get("methodology")
    if not isinstance(node_dataset, dict) or not isinstance(evaluation_dataset, dict):
        raise HarvestDominanceAuditInvalid("Dataset identity metadata is incomplete.")
    if not isinstance(methodology, dict):
        raise HarvestDominanceAuditInvalid("Ranking methodology identity is incomplete.")

    expected_evaluation = _revision(
        node_dataset.get("evaluationDatasetRevision"),
        "Resource-node evaluation revision",
    )
    actual_evaluation = _revision(
        evaluation_dataset.get("revision"),
        "Harvest evaluation revision",
    )
    if expected_evaluation != actual_evaluation:
        raise HarvestDominanceAuditInvalid(
            "Resource-node and evaluation revisions do not match."
        )
    expected_component = _revision(
        node_dataset.get("componentDatasetRevision"),
        "Resource-node component revision",
    )
    actual_component = _revision(
        evaluation_dataset.get("componentDatasetRevision"),
        "Harvest evaluation component revision",
    )
    if expected_component != actual_component:
        raise HarvestDominanceAuditInvalid(
            "Resource-node and evaluation component revisions do not match."
        )

    model_version = str(methodology.get("formulaVersion") or "")
    if model_version != YIELD_MODEL_VERSION:
        raise HarvestDominanceAuditInvalid(
            f"MODEL_VERSION_STALE: expected {YIELD_MODEL_VERSION}, got {model_version or 'MISSING'}."
        )
    extractor_version = str(evaluation_dataset.get("extractorVersion") or "")
    if extractor_version != EXPECTED_EXTRACTOR_VERSION:
        raise HarvestDominanceAuditInvalid(
            "EXTRACTOR_VERSION_STALE: expected "
            f"{EXPECTED_EXTRACTOR_VERSION}, got {extractor_version or 'MISSING'}."
        )
    policy_version = str(methodology.get("policyVersion") or "")
    if policy_version != HARVEST_RANKING_POLICY_VERSION:
        raise HarvestDominanceAuditInvalid(
            "POLICY_VERSION_STALE: expected "
            f"{HARVEST_RANKING_POLICY_VERSION}, got {policy_version or 'MISSING'}."
        )
    return node_dataset, evaluation_dataset


def _git_commit(project_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HarvestDominanceAuditInvalid(
            "CODE_COMMIT_NOT_AVAILABLE: pass code_commit explicitly."
        ) from exc
    commit = completed.stdout.strip().casefold()
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise HarvestDominanceAuditInvalid("CODE_COMMIT identity is invalid.")
    return commit


def _evaluation_key(
    *,
    component_package: str,
    resource: str,
    entry_index: int | None,
    usage_scope: str,
    model_version: str,
    policy_version: str,
) -> tuple[str, str, int | None, str, str, str]:
    return (
        component_package.casefold(),
        str(resource or "").casefold(),
        entry_index,
        usage_scope,
        model_version,
        policy_version,
    )


def _key_payload(key: tuple[str, str, int | None, str, str, str]) -> dict[str, Any]:
    return {
        "harvestComponent": key[0],
        "resourceIdentity": key[1],
        "entryIndex": key[2],
        "usageScope": key[3],
        "modelVersion": key[4],
        "policyVersion": key[5],
    }


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "rank",
        "speciesKey",
        "creature",
        "creatureObjectPath",
        "variantCount",
        "attackIndex",
        "attackName",
        "usageEligibilityStatus",
        "usageConditionReasonCodes",
        "usageEstimateBasis",
        "tameabilityStatus",
        "rideabilityStatus",
        "rankingTier",
        "evidence",
        "sourceDamageType",
        "effectiveDamageType",
        "damageTypeChain",
        "baseDamage",
        "damageMultiplier",
        "harvestQuantityMultiplier",
        "resourceWeight",
        "totalPositiveResourceWeight",
        "resourceWeightShare",
        "overrideQuantityMin",
        "overrideQuantityMax",
        "overrideQuantityRandomPower",
        "effectivenessQuantityMultiplier",
        "maxHarvestHealth",
        "harvestHealthGiveResourceInterval",
        "clampResourceHarvestDamage",
        "estimatedHitsToDepleteNode",
        "estimatedGrantCallsPerNode",
        "estimatedYieldPerNode",
        "scoreBreakdown",
    )
    return {field: row[field] for field in fields if field in row}


def _input_differences(winner: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "creatureObjectPath",
        "attackIndex",
        "attackName",
        "rankingTier",
        "baseDamage",
        "damageMultiplier",
        "harvestQuantityMultiplier",
        "resourceWeightShare",
        "overrideQuantityMin",
        "overrideQuantityMax",
        "overrideQuantityRandomPower",
        "effectivenessQuantityMultiplier",
        "maxHarvestHealth",
        "harvestHealthGiveResourceInterval",
        "clampResourceHarvestDamage",
        "estimatedHitsToDepleteNode",
        "estimatedGrantCallsPerNode",
        "estimatedYieldPerNode",
    )
    return {
        field: {"winner": winner.get(field), "comparison": other.get(field)}
        for field in fields
        if winner.get(field) != other.get(field)
    }


def _root_causes(
    winner: dict[str, Any],
    *,
    canonical_variant: str,
    top_rows: list[dict[str, Any]],
) -> list[str]:
    causes: list[str] = []
    if winner.get("rankingTier") == "CONFIRMED":
        causes.append("CONFIRMED_STATIC_TOTAL_YIELD")
    else:
        causes.append("CONDITIONAL_ATTACK_WON")
    selected_variant = str(winner.get("creatureObjectPath") or "")
    if (
        selected_variant.casefold() != canonical_variant.casefold()
        and _is_special_variant(selected_variant)
    ):
        causes.append("SPECIAL_VARIANT_MAX_POOLING")
    condition_reasons = set(winner.get("usageConditionReasonCodes") or [])
    if condition_reasons & {
        "BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED",
        "BLUEPRINT_ADJUST_OUTPUT_DAMAGE_NOT_RECOVERED",
    }:
        causes.append("RUNTIME_HOOK_NOT_MODELED")
    effectiveness = winner.get("effectivenessQuantityMultiplier")
    if isinstance(effectiveness, (int, float)) and float(effectiveness) != 1.0:
        causes.append("EFFECTIVENESS_FIELD_NOT_MODELED")
    causes.extend(
        [
            "MAP_AVAILABILITY_NOT_MODELED",
            "PRACTICAL_EFFICIENCY_NOT_THE_METRIC",
        ]
    )
    if len(top_rows) > 1:
        causes.append("TIE_PRESENT")
    return causes or ["UNKNOWN"]


def _case_payload(
    *,
    key: tuple[str, str, int | None, str, str, str],
    node: dict[str, Any],
    resource: dict[str, Any],
    winner: dict[str, Any],
    ranked_rows: list[dict[str, Any]],
    canonical_variant: str,
) -> dict[str, Any]:
    top_rows = [row for row in ranked_rows if row.get("rank") == 1]
    comparisons: list[dict[str, Any]] = []
    for row in ranked_rows[:3]:
        compact = _compact_row(row)
        compact["inputDifferencesFromWinner"] = _input_differences(winner, row)
        comparisons.append(compact)
    return {
        "evaluationKey": _key_payload(key),
        "node": {
            "id": node.get("id"),
            "name": node.get("name"),
            "objectPath": node.get("objectPath"),
        },
        "mapEvidence": {
            "references": node.get("mapReferences") or {},
            "usage": node.get("mapUsage") or {},
        },
        "resource": {
            **resource,
            "harvestComponent": canonical_package_path(
                node.get("harvestComponent", {}).get("packagePath")
                if isinstance(node.get("harvestComponent"), dict)
                else ""
            ),
        },
        "effectivenessQuantityMultiplier": winner.get(
            "effectivenessQuantityMultiplier"
        ),
        "winner": _compact_row(winner),
        "canonicalVariant": canonical_variant,
        "exclusiveTop": len(top_rows) == 1,
        "comparisonRows": comparisons,
        "whyCurrentPolicySelected": (
            "V1 selects the highest static complete-node yield per species across "
            "all discovered variants and then combines confirmed and conditional rows."
        ),
        "rootCauses": _root_causes(
            winner,
            canonical_variant=canonical_variant,
            top_rows=top_rows,
        ),
    }


def _sorted_occurrences(node_catalog: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for node in node_catalog.get("nodes", []):
        if not isinstance(node, dict):
            continue
        resources = node.get("resources")
        items = resources.get("items") if isinstance(resources, dict) else None
        for resource in items if isinstance(items, list) else []:
            if isinstance(resource, dict):
                rows.append((node, resource))
    rows.sort(
        key=lambda row: (
            str(row[0].get("id") or ""),
            _entry_index(row[1].get("entryIndex"))
            if _entry_index(row[1].get("entryIndex")) is not None
            else -1,
            str(row[1].get("resource") or "").casefold(),
            str(row[1].get("nodeResourceId") or ""),
        )
    )
    return rows


def _target_variants(
    evaluation_catalog: dict[str, Any], species_query: str
) -> list[dict[str, Any]]:
    requested = _normalized_species_key(species_query)
    exact = [
        row
        for row in evaluation_catalog.get("creatures", [])
        if isinstance(row, dict)
        and _normalized_species_key(
            row.get("speciesKey") or row.get("objectPath") or row.get("name")
        )
        == requested
    ]
    if exact:
        return sorted(exact, key=_canonical_variant_key)
    matches = [
        row
        for row in evaluation_catalog.get("creatures", [])
        if isinstance(row, dict)
        and requested
        in _normalized_species_key(
            " ".join(
                str(row.get(field) or "")
                for field in ("speciesKey", "name", "dinoNameTag", "objectPath")
            )
        )
    ]
    return sorted(matches, key=_canonical_variant_key)


def audit_harvest_rankings(
    *,
    node_catalog_path: Path,
    evaluation_catalog_path: Path,
    sqlite_catalog_path: Path,
    species_query: str,
    code_commit: str | None = None,
) -> dict[str, Any]:
    """Audit current v1 ranking dominance without changing ranking behavior."""

    node_catalog_path = Path(node_catalog_path)
    evaluation_catalog_path = Path(evaluation_catalog_path)
    sqlite_catalog_path = Path(sqlite_catalog_path)
    node_catalog = _read_object(node_catalog_path, "Resource-node catalog")
    evaluation_catalog = _read_object(
        evaluation_catalog_path, "Harvest evaluation catalog"
    )
    node_dataset, evaluation_dataset = _validate_identities(
        node_catalog, evaluation_catalog
    )
    try:
        sqlite_catalog = SQLiteHarvestCatalog(sqlite_catalog_path)
        sqlite_dataset = sqlite_catalog.dataset()
        sqlite_catalog.assert_matches_source(node_catalog_path)
    except FileNotFoundError as exc:
        raise HarvestDominanceAuditInvalid(
            f"SQLite harvest catalog is missing: {sqlite_catalog_path}"
        ) from exc
    except SQLiteHarvestCatalogInvalid as exc:
        raise HarvestDominanceAuditInvalid(str(exc)) from exc
    if sqlite_dataset != node_dataset:
        raise HarvestDominanceAuditInvalid(
            "SQLite harvest catalog dataset metadata does not match the canonical JSON."
        )

    variants = _target_variants(evaluation_catalog, species_query)
    if not variants:
        raise HarvestDominanceAuditInvalid(
            f"Target species was not found: {species_query}"
        )
    target_species_key = _normalized_species_key(
        variants[0].get("speciesKey") or species_query
    )
    canonical_variant = str(variants[0].get("objectPath") or "")
    model_version = str(evaluation_catalog["methodology"]["formulaVersion"])
    policy_version = str(evaluation_catalog["methodology"]["policyVersion"])
    usage_scope = str(evaluation_catalog["methodology"].get("usageScope") or "")
    engine = HarvestEvaluationEngine(evaluation_catalog)

    grouped: OrderedDict[
        tuple[str, str, int | None, str, str, str],
        dict[str, Any],
    ] = OrderedDict()
    occurrences = _sorted_occurrences(node_catalog)
    for node, resource in occurrences:
        component_ref = node.get("harvestComponent")
        component_package = canonical_package_path(
            component_ref.get("packagePath")
            if isinstance(component_ref, dict)
            else ""
        )
        key = _evaluation_key(
            component_package=component_package,
            resource=str(resource.get("resource") or ""),
            entry_index=_entry_index(resource.get("entryIndex")),
            usage_scope=usage_scope,
            model_version=model_version,
            policy_version=policy_version,
        )
        group = grouped.setdefault(
            key,
            {
                "representative": (node, resource),
                "occurrences": [],
            },
        )
        group["occurrences"].append((node, resource))

    rankable_occurrences = 0
    rankable_unique = 0
    tie_occurrences = 0
    confirmed_top_row_occurrences = 0
    conditional_top_row_occurrences = 0
    winner_counts: dict[str, Counter[str]] = {}
    target_top_occurrences = 0
    target_top_unique = 0
    target_confirmed_occurrences = 0
    target_conditional_occurrences = 0
    target_exclusive_occurrences = 0
    target_confirmed_unique = 0
    target_conditional_unique = 0
    cause_counts: Counter[str] = Counter()
    cases: list[dict[str, Any]] = []
    unique_winners: list[dict[str, Any]] = []

    for key, group in grouped.items():
        node, resource = group["representative"]
        group_occurrences = group["occurrences"]
        try:
            ranking = engine.rank_node_resource(
                node_catalog,
                node_id=str(node.get("id") or ""),
                node_resource_id=str(resource.get("nodeResourceId") or ""),
                limit=10,
            )
        except KeyError:
            continue
        ranked_rows = [
            row for row in ranking.get("items", []) if isinstance(row, dict)
        ]
        top_rows = [row for row in ranked_rows if row.get("rank") == 1]
        if not top_rows:
            continue
        occurrence_count = len(group_occurrences)
        rankable_unique += 1
        rankable_occurrences += occurrence_count
        if len(top_rows) > 1:
            tie_occurrences += occurrence_count
        for top in top_rows:
            species = _normalized_species_key(
                top.get("speciesKey") or top.get("creatureObjectPath")
            )
            counter = winner_counts.setdefault(species, Counter())
            counter["uniqueEvaluationKeys"] += 1
            counter["occurrences"] += occurrence_count
            if len(top_rows) == 1:
                counter["exclusiveOccurrences"] += occurrence_count
            if top.get("rankingTier") == "CONFIRMED":
                counter["confirmedOccurrences"] += occurrence_count
                confirmed_top_row_occurrences += occurrence_count
            else:
                counter["conditionalOccurrences"] += occurrence_count
                conditional_top_row_occurrences += occurrence_count
        unique_winners.append(
            {
                "evaluationKey": _key_payload(key),
                "nodeOccurrenceCount": occurrence_count,
                "topRows": [_compact_row(row) for row in top_rows],
            }
        )
        target_winner = next(
            (
                row
                for row in top_rows
                if _normalized_species_key(row.get("speciesKey"))
                == target_species_key
            ),
            None,
        )
        if target_winner is None:
            continue
        target_top_unique += 1
        target_top_occurrences += occurrence_count
        if len(top_rows) == 1:
            target_exclusive_occurrences += occurrence_count
        if target_winner.get("rankingTier") == "CONFIRMED":
            target_confirmed_unique += 1
            target_confirmed_occurrences += occurrence_count
        else:
            target_conditional_unique += 1
            target_conditional_occurrences += occurrence_count
        for occurrence_node, occurrence_resource in group_occurrences:
            case = _case_payload(
                key=key,
                node=occurrence_node,
                resource=occurrence_resource,
                winner=target_winner,
                ranked_rows=ranked_rows,
                canonical_variant=canonical_variant,
            )
            cases.append(case)
            cause_counts.update(case["rootCauses"])

    cases.sort(
        key=lambda case: (
            str(case.get("node", {}).get("id") or ""),
            str(case.get("resource", {}).get("resource") or "").casefold(),
            int(case.get("resource", {}).get("entryIndex") or 0),
        )
    )
    unique_winners.sort(
        key=lambda row: json.dumps(
            row.get("evaluationKey") or {}, sort_keys=True, separators=(",", ":")
        )
    )
    species_winners = []
    for species, counts in sorted(winner_counts.items()):
        species_winners.append(
            {
                "speciesKey": species,
                **dict(sorted(counts.items())),
                "occurrenceSharePercent": (
                    round(counts["occurrences"] / rankable_occurrences * 100.0, 6)
                    if rankable_occurrences
                    else 0.0
                ),
            }
        )

    commit = str(code_commit or "").casefold() or _git_commit(
        Path(__file__).resolve().parents[2]
    )
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise HarvestDominanceAuditInvalid("CODE_COMMIT identity is invalid.")
    return {
        "schema": AUDIT_SCHEMA,
        "targetQuery": species_query,
        "identity": {
            "codeCommit": commit,
            "modelVersion": model_version,
            "policyVersion": policy_version,
            "extractorVersion": evaluation_dataset.get("extractorVersion"),
            "nodeDatasetRevision": node_dataset.get("revision"),
            "evaluationRevision": evaluation_dataset.get("revision"),
            "componentDatasetRevision": evaluation_dataset.get(
                "componentDatasetRevision"
            ),
            "nodeGeneratedAt": node_dataset.get("generatedAt"),
            "evaluationGeneratedAt": evaluation_dataset.get("generatedAt"),
            "sourceHashes": {
                "nodeCatalog": _source_identity(node_catalog_path),
                "evaluationCatalog": _source_identity(evaluation_catalog_path),
                "sqliteCatalog": _source_identity(sqlite_catalog_path),
            },
        },
        "processing": {
            "evaluationStrategy": "STREAM_UNIQUE_KEYS",
            "cartesianProductMaterialized": False,
            "rankingsComputed": len(grouped),
            "maximumRankingRowsRetainedPerKey": 10,
        },
        "global": {
            "occurrencesTotal": len(occurrences),
            "uniqueEvaluationKeysTotal": len(grouped),
            "rankableOccurrences": rankable_occurrences,
            "rankableUniqueEvaluationKeys": rankable_unique,
            "confirmedTopRowOccurrences": confirmed_top_row_occurrences,
            "conditionalTopRowOccurrences": conditional_top_row_occurrences,
            "tieOccurrences": tie_occurrences,
            "speciesWinners": species_winners,
        },
        "targetSpecies": {
            "speciesKey": target_species_key,
            "variantCount": len(variants),
            "variants": [str(row.get("objectPath") or "") for row in variants],
            "canonicalVariant": canonical_variant,
            "topOccurrences": target_top_occurrences,
            "topUniqueEvaluationKeys": target_top_unique,
            "confirmedTopOccurrences": target_confirmed_occurrences,
            "conditionalTopOccurrences": target_conditional_occurrences,
            "confirmedTopUniqueEvaluationKeys": target_confirmed_unique,
            "conditionalTopUniqueEvaluationKeys": target_conditional_unique,
            "exclusiveTopOccurrences": target_exclusive_occurrences,
        },
        "rootCauseCounts": dict(sorted(cause_counts.items())),
        "uniqueWinners": unique_winners,
        "cases": cases,
        "verificationBoundary": {
            "proves": VERIFICATION_PROVES,
            "doesNotProve": VERIFICATION_DOES_NOT_PROVE,
        },
    }


def _markdown_table(rows: Iterable[tuple[str, object]]) -> list[str]:
    result = ["| Field | Value |", "|---|---|"]
    for label, value in rows:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(
            value, (dict, list)
        ) else str(value)
        result.append(f"| {label} | {rendered.replace('|', chr(92) + '|')} |")
    return result


def render_harvest_dominance_markdown(report: dict[str, Any]) -> str:
    """Render every target top occurrence in a deterministic review document."""

    identity = report.get("identity") or {}
    global_stats = report.get("global") or {}
    target = report.get("targetSpecies") or {}
    boundary = report.get("verificationBoundary") or {}
    lines = [
        "# Harvest Ranking Dominance Audit",
        "",
        "## Verification boundary",
        "",
        f"- Proves: `{boundary.get('proves')}`",
        f"- Does not prove: `{boundary.get('doesNotProve')}`",
        "",
        "## Data identity",
        "",
        *_markdown_table(
            (
                ("Code commit", identity.get("codeCommit")),
                ("Model version", identity.get("modelVersion")),
                ("Policy version", identity.get("policyVersion")),
                ("Extractor version", identity.get("extractorVersion")),
                ("Node revision", identity.get("nodeDatasetRevision")),
                ("Evaluation revision", identity.get("evaluationRevision")),
                ("Component revision", identity.get("componentDatasetRevision")),
                ("Source hashes", identity.get("sourceHashes")),
            )
        ),
        "",
        "## Global statistics",
        "",
        *_markdown_table(
            (
                ("Occurrences", global_stats.get("occurrencesTotal")),
                ("Unique evaluation keys", global_stats.get("uniqueEvaluationKeysTotal")),
                ("Rankable occurrences", global_stats.get("rankableOccurrences")),
                ("Rankable unique keys", global_stats.get("rankableUniqueEvaluationKeys")),
                ("Tie occurrences", global_stats.get("tieOccurrences")),
                ("Target top occurrences", target.get("topOccurrences")),
                ("Target top unique keys", target.get("topUniqueEvaluationKeys")),
                ("Target confirmed top", target.get("confirmedTopOccurrences")),
                ("Target conditional top", target.get("conditionalTopOccurrences")),
            )
        ),
        "",
        "## Root causes",
        "",
        "```json",
        json.dumps(report.get("rootCauseCounts") or {}, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Target variants",
        "",
        "```json",
        json.dumps(
            {
                "canonicalVariant": target.get("canonicalVariant"),
                "variants": target.get("variants") or [],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
    ]
    for index, case in enumerate(report.get("cases") or [], start=1):
        node = case.get("node") or {}
        resource = case.get("resource") or {}
        lines.extend(
            [
                "",
                f"## Case {index}: {node.get('id')} / {resource.get('resource')}[{resource.get('entryIndex')}]",
                "",
                "```json",
                json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"
