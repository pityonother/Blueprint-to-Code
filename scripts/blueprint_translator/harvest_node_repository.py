"""Read-only cached access to generated ARK resource-node datasets."""

from __future__ import annotations

import copy
import json
import math
import re
import threading
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

from .harvest_catalog_sqlite import (
    SQLiteHarvestCatalog,
    SQLiteHarvestCatalogInvalid,
)
from .harvest_evaluation_catalog import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    EVALUATION_CATALOG_SCHEMA,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    METRIC_CONTRACTS,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    POLICY_INCLUDE_CONDITIONAL,
    RANKING_RESULT_SCHEMA,
    VARIANT_CANONICAL,
    HarvestEvaluationEngine,
    find_node_and_resource,
    prepare_attack_for_usage_scope,
)
from .harvest_ranking import (
    YIELD_MODEL_VERSION,
    YIELD_SCORE_BASIS,
    estimate_complete_node_yield,
    evaluate_attack_resource,
    normalize_unreal_object_identity,
)
from .harvest_runtime_observations import (
    HarvestRuntimeObservationIndex,
    HarvestRuntimeProfileError,
    load_harvest_runtime_observations,
)
from .resource_nodes import query_resource_nodes, rank_node_resource
from .resource_nodes import canonical_package_path


_REVISION_PATTERN = re.compile(r"[0-9a-f]{64}")
_LAZY_CACHE_CAPACITY = 256
_TOP_BASELINE_CACHE_CAPACITY = 1024
_CREATURE_PAIR_CACHE_CAPACITY = 2048
_V2_TIER_BASELINE_CACHE_CAPACITY = 1024
_SPECIALTY_RESPONSE_CACHE_CAPACITY = 128
_RUNTIME_OBSERVATION_CACHE_CAPACITY = 32
CREATURE_SPECIALTIES_SCHEMA = "blueprint-to-code.harvest-creature-specialties/v3"
CREATURE_PAGE_SCHEMA = "blueprint-to-code.harvest-creature-page/v1"

_SPECIALTY_ROW_FIELDS = (
    "creature",
    "creatureObjectPath",
    "speciesKey",
    "dinoNameTag",
    "variantCount",
    "attackIndex",
    "attackName",
    "sourceDamageType",
    "effectiveDamageType",
    "damageOverrideApplied",
    "baseDamage",
    "baseAttackInterval",
    "riderAttackInterval",
    "attackInterval",
    "attackIntervalSource",
    "usageEligibilityStatus",
    "usageConditionReasonCodes",
    "usageEstimateBasis",
    "damageMultiplier",
    "harvestQuantityMultiplier",
    "resourceWeightShare",
    "harvestPressurePerSecond",
    "estimatedYieldPerNode",
    "staticCompleteNodeTargetYield",
    "staticYieldPerAttackCycleSecond",
    "staticAttackCycleSecondsToDepleteNode",
    "staticFirstHitTiming",
    "observedYieldPerNode",
    "observedYieldPerSecond",
    "runtimeStatus",
    "runtimeObservation",
    "estimatedGrantCallsPerNode",
    "estimatedHitsToDepleteNode",
    "expectedQuantityPerSelection",
    "quantityRandomPower",
    "normalizedHarvestAmountScale",
    "yieldModelVersion",
    "yieldModelBasis",
    "engineComparisonIndex",
    "rankingStatus",
    "reasonCode",
    "rankingTier",
    "scoreBasis",
    "tameabilityStatus",
    "tameabilityReasonCodes",
    "rideabilityStatus",
    "rideabilityReasonCodes",
    "warnings",
    "warningsByScope",
    "evidence",
    "scoreBreakdown",
    "variantSelection",
)


def _compact_specialty_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(row[key])
        for key in _SPECIALTY_ROW_FIELDS
        if key in row
    }


def _specialty_competition_key(row: dict[str, Any]) -> tuple[float, float]:
    return (
        float(row.get("relativeToNodeTopPercent") or 0.0),
        float(row.get("selectedMetricValue") or 0.0),
    )


def _specialty_identity_text(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def _specialty_identity_key(row: dict[str, Any]) -> tuple[object, ...]:
    """Order equal specialty scores by immutable resource/node identities."""

    resource = row.get("resource")
    resource = resource if isinstance(resource, dict) else {}
    node = row.get("node")
    node = node if isinstance(node, dict) else {}
    entry_index = resource.get("entryIndex")
    stable_entry_index = (
        (0, int(entry_index), "")
        if isinstance(entry_index, int) and not isinstance(entry_index, bool)
        else (1, 0, _specialty_identity_text(entry_index))
    )
    attack_index = row.get("attackIndex")
    stable_attack_index = (
        (0, int(attack_index), "")
        if isinstance(attack_index, int) and not isinstance(attack_index, bool)
        else (1, 0, _specialty_identity_text(attack_index))
    )
    return (
        _specialty_identity_text(resource.get("nodeResourceId")),
        _specialty_identity_text(node.get("id")),
        _specialty_identity_text(resource.get("resource")),
        _specialty_identity_text(node.get("objectPath")),
        stable_entry_index,
        _specialty_identity_text(resource.get("harvestComponentPackagePath")),
        _specialty_identity_text(row.get("creatureObjectPath")),
        stable_attack_index,
    )


def _specialty_page_sort_key(row: dict[str, Any]) -> tuple[object, ...]:
    competition_key = _specialty_competition_key(row)
    return (
        -competition_key[0],
        -competition_key[1],
        *_specialty_identity_key(row),
    )


def _creature_representative_key(creature: dict[str, Any]) -> tuple[int, int, str]:
    """Prefer the shortest base-game identity over mission/variant display names."""

    object_path = str(creature.get("objectPath") or "")
    return (
        0 if object_path.casefold().startswith("/game/earth/dinos/") else (
            1 if object_path.casefold().startswith("/game/primalearth/dinos/") else 2
        ),
        len(object_path),
        object_path.casefold(),
    )


def _normalized_damage_parents(rows: dict[str, str]) -> dict[str, str]:
    return {
        normalize_unreal_object_identity(child): normalize_unreal_object_identity(parent)
        for child, parent in rows.items()
        if normalize_unreal_object_identity(child)
    }


def _damage_chain(damage_type: str, parents: dict[str, str]) -> list[str]:
    chain: list[str] = []
    current = normalize_unreal_object_identity(damage_type)
    seen: set[str] = set()
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = parents.get(current, "")
    return chain


def _normalized_override_map(rows: object) -> dict[str, Any]:
    if not isinstance(rows, dict):
        return {}
    return {
        normalize_unreal_object_identity(key): value
        for key, value in rows.items()
        if normalize_unreal_object_identity(key)
    }


def _nearest_override_value(
    overrides: object,
    chain: list[str],
    fallback: Any,
) -> Any:
    normalized = _normalized_override_map(overrides)
    for damage_type in chain:
        if damage_type in normalized:
            return normalized[damage_type]
    return fallback


def _eligible_attack_candidates(
    catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Keep only attacks that can numerically participate in the configured scope."""

    usage_scope = str(catalog.get("methodology", {}).get("usageScope") or "TAMED_RIDDEN")
    require_confirmed_rideability = (
        catalog.get("methodology", {}).get("rideabilityRequirement")
        == "B_ALLOW_RIDING_TRUE"
    )
    considered = [
        creature
        for creature in catalog.get("creatures", [])
        if isinstance(creature, dict)
        and str(creature.get("tameability", {}).get("status") or "UNKNOWN")
        != "PREVENTED"
        and (
            not require_confirmed_rideability
            or str(creature.get("rideability", {}).get("status") or "UNKNOWN")
            == "ALLOWED"
        )
    ]
    variant_counts = Counter(
        str(creature.get("speciesKey") or creature.get("objectPath") or "").casefold()
        for creature in considered
    )
    candidates: list[dict[str, Any]] = []
    order = 0
    for creature in considered:
        tameability = creature.get("tameability")
        rideability = creature.get("rideability")
        tameability_status = str(
            tameability.get("status") if isinstance(tameability, dict) else "UNKNOWN"
        ) or "UNKNOWN"
        tameability_reasons = (
            [str(value) for value in tameability.get("reasonCodes", []) if value]
            if isinstance(tameability, dict)
            else ["TAMEABILITY_NOT_RECOVERED"]
        )
        rideability_status = str(
            rideability.get("status") if isinstance(rideability, dict) else "UNKNOWN"
        ) or "UNKNOWN"
        rideability_reasons = (
            [str(value) for value in rideability.get("reasonCodes", []) if value]
            if isinstance(rideability, dict)
            else ["RIDEABILITY_NOT_RECOVERED"]
        )
        species_key = str(
            creature.get("speciesKey")
            or creature.get("objectPath")
            or creature.get("name")
            or ""
        ).casefold()
        for attack in creature.get("attacks", []):
            if not isinstance(attack, dict):
                continue
            prepared, _exclusion_reason = prepare_attack_for_usage_scope(
                attack,
                usage_scope=usage_scope,
            )
            if prepared is None:
                continue
            source_damage_type = normalize_unreal_object_identity(
                prepared.get("damageType")
            )
            base_damage = prepared.get("baseDamage")
            ranking_gaps = [
                str(gap)
                for gap in prepared.get("gaps") or []
                if str(gap) != "AttackInterval"
            ]
            if (
                ranking_gaps
                or not source_damage_type
                or not isinstance(base_damage, (int, float))
                or float(base_damage) <= 0
                or prepared.get("useBlueprintAdjustOutputDamage") is True
            ):
                continue
            candidates.append(
                {
                    "order": order,
                    "creature": creature,
                    "preparedAttack": prepared,
                    "sourceDamageType": source_damage_type,
                    "speciesKey": species_key,
                    "variantCount": variant_counts[species_key],
                    "tameabilityStatus": tameability_status,
                    "tameabilityReasonCodes": tameability_reasons,
                    "rideabilityStatus": rideability_status,
                    "rideabilityReasonCodes": rideability_reasons,
                }
            )
            order += 1
    return candidates, variant_counts


def _component_coefficients_by_source(
    component: dict[str, Any],
    *,
    resource: str,
    resource_entry_index: int | None,
    source_damage_types: set[str],
    damage_type_parents: dict[str, str],
    resource_damage_overrides: dict[tuple[str, str], str],
    damage_type_gaps: dict[str, list[str]],
) -> dict[str, dict[str, float | bool]]:
    """Return complete-node model inputs for each safely rankable damage source.

    This is a compact precomputation for the reverse-specialty query.  It does
    not calculate a second score formula: callers must pass these inputs to
    :func:`estimate_complete_node_yield`, the same native-static hit simulator
    used by the authoritative forward evaluator.
    """

    component_ranking_gaps = component.get("rankingGaps")
    if not isinstance(component_ranking_gaps, list):
        component_ranking_gaps = [
            str(gap)
            for gap in component.get("gaps") or []
            if str(gap).startswith("HARVEST_")
        ]
    if any(
        str(gap)
        in {
            "HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED",
            "HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED",
        }
        for gap in component_ranking_gaps
    ):
        return {}

    max_harvest_health = component.get("maxHarvestHealth")
    give_resource_interval = component.get("harvestHealthGiveResourceInterval")
    if (
        not isinstance(max_harvest_health, (int, float))
        or not isinstance(give_resource_interval, (int, float))
        or float(max_harvest_health) <= 0
        or float(give_resource_interval) <= 0
        or component.get("isSingleUnitHarvest") is True
    ):
        return {}

    resource_entries = component.get("resourceEntries")
    damage_entries = component.get("damageEntries")
    if not isinstance(resource_entries, list) or not isinstance(damage_entries, list):
        return {}
    target_resource = normalize_unreal_object_identity(resource)
    indexed_entries = any(
        isinstance(entry, dict)
        and isinstance(entry.get("entryIndex"), int)
        and not isinstance(entry.get("entryIndex"), bool)
        for entry in resource_entries
    )
    target_entry = next(
        (
            entry
            for entry in resource_entries
            if isinstance(entry, dict)
            and normalize_unreal_object_identity(entry.get("resource"))
            == target_resource
            and (
                resource_entry_index is None
                or not indexed_entries
                or entry.get("entryIndex") == resource_entry_index
            )
        ),
        None,
    )
    if not isinstance(target_entry, dict):
        return {}

    quantity_random_power = target_entry.get("overrideQuantityRandomPower")
    if quantity_random_power is None:
        quantity_random_power = 1.0
    if (
        not isinstance(quantity_random_power, (int, float))
        or not math.isfinite(float(quantity_random_power))
        or not math.isclose(
            float(quantity_random_power), 1.0, rel_tol=0.0, abs_tol=1e-6
        )
    ):
        return {}

    normalized_parents = _normalized_damage_parents(damage_type_parents)
    normalized_damage_gaps = {
        normalize_unreal_object_identity(key): list(value)
        for key, value in damage_type_gaps.items()
    }
    normalized_resource_overrides = {
        (
            normalize_unreal_object_identity(source),
            normalize_unreal_object_identity(candidate_resource),
        ): normalize_unreal_object_identity(replacement)
        for (source, candidate_resource), replacement in resource_damage_overrides.items()
    }
    first_damage_entry_by_parent: dict[str, dict[str, Any]] = {}
    unresolved_damage_entry = False
    for entry in damage_entries:
        if not isinstance(entry, dict):
            continue
        parent = normalize_unreal_object_identity(entry.get("damageTypeParent"))
        if parent and parent not in first_damage_entry_by_parent:
            first_damage_entry_by_parent[parent] = entry
        if "DAMAGE_TYPE_PARENT_NOT_RECOVERED" in (entry.get("gaps") or []):
            unresolved_damage_entry = True

    result: dict[str, dict[str, float | bool]] = {}
    for source_damage_type in source_damage_types:
        effective_damage_type = normalized_resource_overrides.get(
            (source_damage_type, target_resource),
            source_damage_type,
        )
        if any(
            normalized_damage_gaps.get(damage_type)
            for damage_type in {source_damage_type, effective_damage_type}
        ):
            continue
        chain = _damage_chain(effective_damage_type, normalized_parents)
        damage_entry: dict[str, Any] | None = None
        unresolved_chain_gaps: list[str] = []
        for damage_type in chain:
            candidate_entry = first_damage_entry_by_parent.get(damage_type)
            candidate_gaps = normalized_damage_gaps.get(damage_type, [])
            if candidate_entry is not None:
                if unresolved_chain_gaps or candidate_gaps:
                    damage_entry = None
                else:
                    damage_entry = candidate_entry
                break
            unresolved_chain_gaps.extend(candidate_gaps)
        if damage_entry is None:
            if unresolved_chain_gaps or unresolved_damage_entry:
                continue
            continue

        weighted_entries: list[tuple[dict[str, Any], float | None]] = []
        for entry in resource_entries:
            if not isinstance(entry, dict):
                continue
            entry_gaps = set(entry.get("rankingGaps") or entry.get("gaps") or [])
            override_types = entry.get("damageTypeEntryValues")
            weight_override_unknown = (
                "DAMAGE_TYPE_WEIGHT_OVERRIDE_NOT_RECOVERED" in entry_gaps
                and (
                    not isinstance(override_types, list)
                    or any(
                        normalize_unreal_object_identity(candidate) in chain
                        for candidate in override_types
                    )
                )
            )
            if weight_override_unknown:
                weight = None
            else:
                value = _nearest_override_value(
                    entry.get("weightOverrides"),
                    chain,
                    entry.get("entryWeight"),
                )
                weight = float(value) if isinstance(value, (int, float)) else None
            weighted_entries.append((entry, weight))
        target_weight_row = next(
            (row for row in weighted_entries if row[0] is target_entry),
            None,
        )
        if target_weight_row is None or target_weight_row[1] is None:
            continue
        target_weight = float(target_weight_row[1])
        if target_weight <= 0 or any(
            weight is None
            for entry, weight in weighted_entries
            if entry is not target_entry
        ):
            continue
        total_positive_weight = sum(
            max(0.0, float(weight))
            for _entry, weight in weighted_entries
            if weight is not None
        )
        damage_multiplier = damage_entry.get("damageMultiplier")
        quantity_multiplier = damage_entry.get("harvestQuantityMultiplier")
        additional_effectiveness = damage_entry.get(
            "damageHarvestAdditionalEffectiveness"
        )
        if additional_effectiveness is None:
            additional_effectiveness = 0.0
        minimum_quantity = _nearest_override_value(
            target_entry.get("minQuantityOverrides"),
            chain,
            target_entry.get("overrideQuantityMin"),
        )
        maximum_quantity = _nearest_override_value(
            target_entry.get("maxQuantityOverrides"),
            chain,
            target_entry.get("overrideQuantityMax"),
        )
        if (
            total_positive_weight <= 0
            or not isinstance(damage_multiplier, (int, float))
            or not isinstance(quantity_multiplier, (int, float))
            or not isinstance(additional_effectiveness, (int, float))
            or not math.isclose(
                float(additional_effectiveness), 0.0, rel_tol=0.0, abs_tol=1e-9
            )
            or not isinstance(minimum_quantity, (int, float))
            or not isinstance(maximum_quantity, (int, float))
        ):
            continue
        result[source_damage_type] = {
            "damage_multiplier": float(damage_multiplier),
            "harvest_quantity_multiplier": float(quantity_multiplier),
            "max_harvest_health": float(max_harvest_health),
            "harvest_health_give_resource_interval": float(give_resource_interval),
            "resource_weight_share": target_weight / total_positive_weight,
            "minimum_quantity": float(minimum_quantity),
            "maximum_quantity": float(maximum_quantity),
            "quantity_random_power": float(quantity_random_power),
            "clamp_resource_harvest_damage": bool(
                component.get("clampResourceHarvestDamage")
            ),
        }
    return result


def _best_discovered_scope_row(
    engine: HarvestEvaluationEngine,
    *,
    component_package: str,
    resource: str,
    resource_entry_index: int | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compute the exact discovered-scope top without materializing species x node rows."""

    component = engine.components.get(component_package.casefold())
    if not isinstance(component, dict):
        return None
    coefficients = _component_coefficients_by_source(
        component,
        resource=resource,
        resource_entry_index=resource_entry_index,
        source_damage_types={
            str(candidate.get("sourceDamageType") or "")
            for candidate in candidates
            if candidate.get("sourceDamageType")
        },
        damage_type_parents=engine.damage_type_parents,
        resource_damage_overrides=engine.resource_damage_overrides,
        damage_type_gaps=engine.damage_type_gaps,
    )
    best_by_species: dict[str, tuple[float, int, dict[str, Any]]] = {}
    for candidate in candidates:
        yield_inputs = coefficients.get(
            str(candidate.get("sourceDamageType") or "")
        )
        if yield_inputs is None:
            continue
        prepared = candidate["preparedAttack"]
        try:
            estimate = estimate_complete_node_yield(
                base_damage=float(prepared["baseDamage"]),
                **yield_inputs,
            )
        except (TypeError, ValueError):
            continue
        score = float(estimate["estimatedYieldPerNode"])
        species_key = str(candidate.get("speciesKey") or "")
        order = int(candidate.get("order") or 0)
        current = best_by_species.get(species_key)
        if current is None or score > current[0] or (
            score == current[0] and order < current[1]
        ):
            best_by_species[species_key] = (score, order, candidate)
    if not best_by_species:
        return None
    ranked_species = sorted(
        best_by_species.values(),
        key=lambda value: (
            -value[0],
            str(value[2]["creature"].get("name") or "").casefold(),
            str(value[2]["creature"].get("objectPath") or ""),
            int(value[2]["preparedAttack"].get("attackIndex") or 0),
        ),
    )
    winner: dict[str, Any] | None = None
    row: dict[str, Any] | None = None
    for _score, _order, candidate in ranked_species:
        creature = candidate["creature"]
        prepared = candidate["preparedAttack"]
        evaluated = evaluate_attack_resource(
            creature=str(creature.get("name") or "Unknown creature"),
            creature_object_path=str(creature.get("objectPath") or ""),
            attack=prepared,
            component=component,
            resource=resource,
            resource_entry_index=resource_entry_index,
            damage_type_parents=engine.damage_type_parents,
            resource_damage_overrides=engine.resource_damage_overrides,
            damage_type_gaps=engine.damage_type_gaps,
        )
        if evaluated.get("rankingStatus") == "RANKED" and isinstance(
            evaluated.get("estimatedYieldPerNode"), (int, float)
        ):
            winner = candidate
            row = evaluated
            break
    if winner is None or row is None:
        return None
    creature = winner["creature"]
    prepared = winner["preparedAttack"]
    require_confirmed_rideability = (
        engine.catalog.get("methodology", {}).get("rideabilityRequirement")
        == "B_ALLOW_RIDING_TRUE"
    )
    condition_reasons = [
        str(value)
        for value in prepared.get("usageConditionReasonCodes", [])
        if value
    ]
    creature_evidence_confirmed = winner.get("tameabilityStatus") == "ALLOWED" and (
        not require_confirmed_rideability
        or winner.get("rideabilityStatus") == "ALLOWED"
    )
    evidence_confirmed = creature_evidence_confirmed and not condition_reasons
    evidence_gaps = sorted(
        set(
            condition_reasons
            + (
                []
                if winner.get("tameabilityStatus") == "ALLOWED"
                else list(winner.get("tameabilityReasonCodes") or [])
                or ["TAMEABILITY_NOT_RECOVERED"]
            )
            + (
                list(winner.get("rideabilityReasonCodes") or [])
                if require_confirmed_rideability
                and winner.get("rideabilityStatus") != "ALLOWED"
                else []
            )
        )
    )
    row.update(
        {
            "speciesKey": winner.get("speciesKey"),
            "dinoNameTag": creature.get("dinoNameTag"),
            "variantCount": winner.get("variantCount"),
            "baseAttackInterval": prepared.get("baseAttackInterval"),
            "riderAttackInterval": prepared.get("riderAttackInterval"),
            "attackIntervalSource": prepared.get("attackIntervalSource"),
            "usageEligibilityStatus": prepared.get("usageEligibilityStatus"),
            "usageConditionReasonCodes": condition_reasons,
            "usageEstimateBasis": prepared.get("usageEstimateBasis"),
            "tameabilityStatus": winner.get("tameabilityStatus"),
            "tameabilityReasonCodes": winner.get("tameabilityReasonCodes"),
            "rideabilityStatus": winner.get("rideabilityStatus"),
            "rideabilityReasonCodes": winner.get("rideabilityReasonCodes"),
            "evidence": {
                "status": "CONFIRMED" if evidence_confirmed else "PARTIAL",
                "gaps": []
                if evidence_confirmed
                else evidence_gaps or ["TAMEABILITY_NOT_RECOVERED"],
            },
            "rankingTier": "CONFIRMED" if evidence_confirmed else "CONDITIONAL",
        }
    )
    return row


class HarvestDatasetNotBuilt(FileNotFoundError):
    code = "HARVEST_DATASET_NOT_BUILT"


class HarvestDatasetInvalid(ValueError):
    code = "HARVEST_DATASET_INVALID"


class HarvestNodeRepository:
    """Cache generated JSON by file signature and expose bounded queries."""

    def __init__(
        self,
        catalog_path: Path,
        ranking_path: Path,
        evaluation_catalog_path: Path | None = None,
        sqlite_catalog_path: Path | None = None,
        runtime_observation_root: Path | None = None,
    ):
        self.catalog_path = Path(catalog_path)
        self.ranking_path = Path(ranking_path)
        self.evaluation_catalog_path = (
            Path(evaluation_catalog_path)
            if evaluation_catalog_path is not None
            else None
        )
        self.sqlite_catalog_path = (
            Path(sqlite_catalog_path) if sqlite_catalog_path is not None else None
        )
        self.runtime_observation_root = (
            Path(runtime_observation_root)
            if runtime_observation_root is not None
            else None
        )
        self._lock = threading.Lock()
        self._catalog_signature: tuple[int, int] | None = None
        self._ranking_signature: tuple[int, int] | None = None
        self._evaluation_signature: tuple[int, int] | None = None
        self._catalog: dict[str, Any] | None = None
        self._ranking: dict[str, Any] | None = None
        self._evaluation: dict[str, Any] | None = None
        self._evaluation_engine: HarvestEvaluationEngine | None = None
        self._sqlite_signature: tuple[int, int] | None = None
        self._sqlite_source_signature: tuple[int, int] | None = None
        self._sqlite_catalog: SQLiteHarvestCatalog | None = None
        self._runtime_dataset_signature: tuple[
            tuple[tuple[str, int, int], ...],
            tuple[tuple[str, str], ...],
        ] | None = None
        self._runtime_observation_cache: OrderedDict[
            tuple[
                tuple[tuple[str, int, int], ...],
                tuple[tuple[str, str], ...],
                tuple[str | None, bool, bool],
            ],
            HarvestRuntimeObservationIndex,
        ] = OrderedDict()
        self._lazy_ranking_cache: OrderedDict[tuple[object, ...], dict[str, Any]] = (
            OrderedDict()
        )
        self._top_baseline_cache: OrderedDict[
            tuple[str, str, str, int | None, str], dict[str, Any]
        ] = OrderedDict()
        self._creature_pair_cache: OrderedDict[
            tuple[str, str, str, str, int | None, str], dict[str, Any]
        ] = OrderedDict()
        self._v2_tier_baseline_cache: OrderedDict[
            tuple[object, ...], dict[str, Any]
        ] = OrderedDict()
        self._specialty_response_cache: OrderedDict[
            tuple[object, ...], dict[str, Any]
        ] = OrderedDict()

    @staticmethod
    def _signature(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _read_object(path: Path, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt(f"{label} has not been generated.") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise HarvestDatasetInvalid(f"{label} cannot be read: {exc}") from exc
        if not isinstance(payload, dict):
            raise HarvestDatasetInvalid(f"{label} must contain a JSON object.")
        return payload

    def _load_catalog(self) -> dict[str, Any]:
        try:
            signature = self._signature(self.catalog_path)
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt("Resource-node catalog has not been generated.") from exc
        with self._lock:
            if self._catalog is None or signature != self._catalog_signature:
                payload = self._read_object(self.catalog_path, "Resource-node catalog")
                if payload.get("schema") != "ark-resource-node-catalog/v1" or not isinstance(
                    payload.get("nodes"), list
                ):
                    raise HarvestDatasetInvalid("Resource-node catalog schema is invalid.")
                self._catalog = payload
                self._catalog_signature = signature
            return self._catalog

    def _load_ranking(self) -> dict[str, Any]:
        try:
            signature = self._signature(self.ranking_path)
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt("Harvest ranking report has not been generated.") from exc
        with self._lock:
            if self._ranking is None or signature != self._ranking_signature:
                payload = self._read_object(self.ranking_path, "Harvest ranking report")
                if payload.get("schema") not in {
                    "ark-harvest-ranking/v1",
                    "ark-harvest-ranking/v2",
                } or not isinstance(
                    payload.get("bestRows"), list
                ):
                    raise HarvestDatasetInvalid("Harvest ranking report schema is invalid.")
                self._ranking = payload
                self._ranking_signature = signature
            return self._ranking

    def _load_sqlite_catalog(self) -> SQLiteHarvestCatalog:
        path = self.sqlite_catalog_path
        if path is None:
            raise HarvestDatasetInvalid("SQLite harvest catalog is not configured.")
        try:
            signature = self._signature(path)
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt(
                "SQLite resource-node catalog has not been generated."
            ) from exc
        try:
            source_signature = self._signature(self.catalog_path)
        except FileNotFoundError:
            source_signature = None
        with self._lock:
            database_changed = (
                self._sqlite_catalog is None or signature != self._sqlite_signature
            )
            source_changed = source_signature != self._sqlite_source_signature
            if database_changed or source_changed:
                reader = (
                    SQLiteHarvestCatalog(path)
                    if database_changed
                    else self._sqlite_catalog
                )
                if reader is None:
                    raise HarvestDatasetInvalid(
                        "SQLite resource-node catalog reader is unavailable."
                    )
                try:
                    reader.dataset()
                    if source_signature is not None:
                        reader.assert_matches_source(self.catalog_path)
                except FileNotFoundError as exc:
                    raise HarvestDatasetNotBuilt(
                        "SQLite resource-node catalog has not been generated."
                    ) from exc
                except SQLiteHarvestCatalogInvalid as exc:
                    raise HarvestDatasetInvalid(str(exc)) from exc
                self._sqlite_catalog = reader
                self._sqlite_signature = signature
                self._sqlite_source_signature = source_signature
                self._lazy_ranking_cache.clear()
                self._top_baseline_cache.clear()
                self._creature_pair_cache.clear()
                self._v2_tier_baseline_cache.clear()
                self._specialty_response_cache.clear()
            return self._sqlite_catalog

    def _catalog_for_node(self, node_id: str) -> dict[str, Any]:
        if self.sqlite_catalog_path is not None:
            try:
                return self._load_sqlite_catalog().catalog_for_node(node_id)
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        return self._load_catalog()

    @staticmethod
    def _validated_revision(value: object, label: str) -> str:
        revision = str(value or "")
        if _REVISION_PATTERN.fullmatch(revision) is None:
            raise HarvestDatasetInvalid(
                f"{label} must be a 64-character lowercase SHA-256 revision."
            )
        return revision

    def _load_evaluation(
        self,
    ) -> tuple[dict[str, Any], HarvestEvaluationEngine]:
        path = self.evaluation_catalog_path
        if path is None:
            raise HarvestDatasetInvalid("Harvest evaluation catalog is not configured.")
        try:
            signature = self._signature(path)
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt(
                "Harvest evaluation catalog has not been generated."
            ) from exc
        with self._lock:
            if self._evaluation is None or signature != self._evaluation_signature:
                payload = self._read_object(path, "Harvest evaluation catalog")
                if payload.get("schema") != EVALUATION_CATALOG_SCHEMA:
                    raise HarvestDatasetInvalid(
                        "Harvest evaluation catalog schema is invalid."
                    )
                dataset = payload.get("dataset")
                if not isinstance(dataset, dict):
                    raise HarvestDatasetInvalid(
                        "Harvest evaluation catalog dataset metadata is invalid."
                    )
                self._validated_revision(
                    dataset.get("revision"),
                    "Harvest evaluation catalog revision",
                )
                self._validated_revision(
                    dataset.get("componentDatasetRevision"),
                    "Harvest evaluation component revision",
                )
                methodology = payload.get("methodology")
                if (
                    isinstance(methodology, dict)
                    and methodology.get("contractVersion")
                    == HARVEST_RANKING_CONTRACT_VERSION
                ):
                    expected_identity = {
                        "formulaVersion": YIELD_MODEL_VERSION,
                        "policyVersion": HARVEST_RANKING_POLICY_VERSION,
                    }
                    for key, expected in expected_identity.items():
                        if methodology.get(key) != expected:
                            raise HarvestDatasetInvalid(
                                f"Harvest evaluation {key} does not match this runtime."
                            )
                    if not str(dataset.get("extractorVersion") or ""):
                        raise HarvestDatasetInvalid(
                            "Harvest evaluation extractor version is missing."
                        )
                try:
                    engine = HarvestEvaluationEngine(payload)
                except (TypeError, ValueError) as exc:
                    raise HarvestDatasetInvalid(str(exc)) from exc
                self._evaluation = payload
                self._evaluation_engine = engine
                self._evaluation_signature = signature
                self._lazy_ranking_cache.clear()
                self._top_baseline_cache.clear()
                self._creature_pair_cache.clear()
                self._v2_tier_baseline_cache.clear()
                self._specialty_response_cache.clear()
            if self._evaluation_engine is None:
                raise HarvestDatasetInvalid(
                    "Harvest evaluation catalog engine is unavailable."
                )
            return self._evaluation, self._evaluation_engine

    def _load_runtime_observations(
        self,
        expected_identity: dict[str, str] | None = None,
        *,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
        allow_unselected_profiles: bool = False,
    ) -> HarvestRuntimeObservationIndex:
        root = self.runtime_observation_root
        if root is None or not root.exists():
            return load_harvest_runtime_observations(
                Path("__harvest_runtime_observations_absent__"),
                expected_identity=expected_identity,
                runtime_profile_id=runtime_profile_id,
                include_preliminary=include_preliminary,
                allow_unselected_profiles=allow_unselected_profiles,
            )
        if not root.is_dir():
            raise HarvestDatasetInvalid(
                "Harvest runtime observation root must be a directory."
            )
        signature = tuple(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(
                root.glob("*.json"), key=lambda value: value.name.casefold()
            )
        )
        expected_signature = tuple(sorted((expected_identity or {}).items()))
        request_signature = (
            str(runtime_profile_id).strip() if runtime_profile_id is not None else None,
            bool(include_preliminary),
            bool(allow_unselected_profiles),
        )
        dataset_signature = (signature, expected_signature)
        cache_key = (signature, expected_signature, request_signature)
        with self._lock:
            if dataset_signature != self._runtime_dataset_signature:
                self._runtime_dataset_signature = dataset_signature
                self._runtime_observation_cache.clear()
                self._lazy_ranking_cache.clear()
                self._top_baseline_cache.clear()
                self._creature_pair_cache.clear()
                self._v2_tier_baseline_cache.clear()
                self._specialty_response_cache.clear()
            cached = self._runtime_observation_cache.pop(cache_key, None)
            if cached is not None:
                self._runtime_observation_cache[cache_key] = cached
                return cached
            try:
                index = load_harvest_runtime_observations(
                    root,
                    expected_identity=expected_identity,
                    runtime_profile_id=runtime_profile_id,
                    include_preliminary=include_preliminary,
                    allow_unselected_profiles=allow_unselected_profiles,
                )
            except HarvestRuntimeProfileError:
                raise
            except (OSError, ValueError) as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
            self._runtime_observation_cache[cache_key] = index
            while (
                len(self._runtime_observation_cache)
                > _RUNTIME_OBSERVATION_CACHE_CAPACITY
            ):
                self._runtime_observation_cache.popitem(last=False)
            return index

    @staticmethod
    def _runtime_identity(
        node_catalog: dict[str, Any],
        evaluation_catalog: dict[str, Any],
    ) -> dict[str, str]:
        node_dataset = dict(node_catalog.get("dataset") or {})
        evaluation_dataset = dict(evaluation_catalog.get("dataset") or {})
        return {
            "extractorVersion": str(
                evaluation_dataset.get("extractorVersion") or ""
            ),
            "policyVersion": HARVEST_RANKING_POLICY_VERSION,
            "nodeCatalogRevision": str(node_dataset.get("revision") or ""),
            "evaluationCatalogRevision": str(
                evaluation_dataset.get("revision") or ""
            ),
            "componentCatalogRevision": str(
                evaluation_dataset.get("componentDatasetRevision") or ""
            ),
        }

    @staticmethod
    def _evaluation_revisions(
        node_catalog: dict[str, Any],
        evaluation_catalog: dict[str, Any],
    ) -> tuple[str, str]:
        node_dataset = node_catalog.get("dataset")
        evaluation_dataset = evaluation_catalog.get("dataset")
        if not isinstance(node_dataset, dict) or not isinstance(
            evaluation_dataset, dict
        ):
            raise HarvestDatasetInvalid(
                "Harvest node/evaluation dataset metadata is invalid."
            )
        expected_evaluation = HarvestNodeRepository._validated_revision(
            node_dataset.get("evaluationDatasetRevision"),
            "Resource-node evaluation revision",
        )
        expected_component = HarvestNodeRepository._validated_revision(
            node_dataset.get("componentDatasetRevision"),
            "Resource-node component revision",
        )
        actual_evaluation = HarvestNodeRepository._validated_revision(
            evaluation_dataset.get("revision"),
            "Harvest evaluation catalog revision",
        )
        actual_component = HarvestNodeRepository._validated_revision(
            evaluation_dataset.get("componentDatasetRevision"),
            "Harvest evaluation component revision",
        )
        if expected_evaluation != actual_evaluation:
            raise HarvestDatasetInvalid(
                "Resource-node catalog and evaluation catalog revisions do not match."
            )
        if expected_component != actual_component:
            raise HarvestDatasetInvalid(
                "Resource-node catalog and evaluation component revisions do not match."
            )
        return actual_evaluation, actual_component

    @staticmethod
    def _bind_lazy_result(
        cached: dict[str, Any],
        *,
        node_catalog: dict[str, Any],
        node: dict[str, Any],
        resource: dict[str, Any],
        component_package: str,
        limit: int,
    ) -> dict[str, Any]:
        result = copy.deepcopy(cached)
        result["dataset"] = {
            **dict(node_catalog.get("dataset") or {}),
            **{
                key: value
                for key, value in dict(cached.get("dataset") or {}).items()
                if key in {"evaluationRevision", "evaluationGeneratedAt"}
            },
        }
        result["node"] = {
            "id": node.get("id"),
            "name": node.get("name"),
            "objectPath": node.get("objectPath"),
        }
        result["resource"] = {
            **resource,
            "harvestComponentPackagePath": component_package,
        }
        bounded_limit = max(1, min(int(limit), 10))
        confirmed_items = [
            dict(row) for row in result.get("confirmedItems", [])[:bounded_limit]
        ]
        conditional_items = [
            dict(row) for row in result.get("conditionalItems", [])[:bounded_limit]
        ]
        if "confirmedItems" in result or "conditionalItems" in result:
            result["confirmedItems"] = confirmed_items
            result["conditionalItems"] = conditional_items
            items = list(confirmed_items)
        else:
            items = [dict(row) for row in result.get("items", [])[:bounded_limit]]
        result["items"] = items
        coverage = dict(result.get("coverage") or {})
        if "confirmedItems" in result or "conditionalItems" in result:
            confirmed_total = int(
                coverage.get("rankedSpeciesConfirmed") or len(confirmed_items)
            )
            conditional_total = int(
                coverage.get("rankedSpeciesConditional") or len(conditional_items)
            )
            coverage["returnedConfirmed"] = len(confirmed_items)
            coverage["returnedConditional"] = len(conditional_items)
            coverage["omittedConfirmed"] = max(
                0, confirmed_total - len(confirmed_items)
            )
            coverage["omittedConditional"] = max(
                0, conditional_total - len(conditional_items)
            )
            ranked_total = confirmed_total
        else:
            ranked_total = int(coverage.get("rankedForNodeResource") or len(items))
        coverage["returned"] = len(items)
        coverage["omitted"] = max(0, ranked_total - len(items))
        result["coverage"] = coverage
        return result

    def _lazy_rankings(
        self,
        node_catalog: dict[str, Any],
        evaluation_catalog: dict[str, Any],
        engine: HarvestEvaluationEngine,
        *,
        node_id: str,
        node_resource_id: str,
        limit: int,
        evidence_policy: str,
        variant_policy: str,
        metric: str,
        availability_policy: str,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
    ) -> dict[str, Any]:
        evaluation_revision, _component_revision = self._evaluation_revisions(
            node_catalog,
            evaluation_catalog,
        )
        node, resource = find_node_and_resource(
            node_catalog,
            node_id,
            node_resource_id,
        )
        component_ref = node.get("harvestComponent")
        component_package = canonical_package_path(
            component_ref.get("packagePath")
            if isinstance(component_ref, dict)
            else ""
        )
        resource_class = str(resource.get("resource") or "")
        raw_entry_index = resource.get("entryIndex")
        resource_entry_index = (
            int(raw_entry_index)
            if isinstance(raw_entry_index, int) and not isinstance(raw_entry_index, bool)
            else None
        )
        usage_scope = str(
            evaluation_catalog.get("methodology", {}).get("usageScope") or ""
        )
        evaluation_dataset = dict(evaluation_catalog.get("dataset") or {})
        node_dataset = dict(node_catalog.get("dataset") or {})
        runtime_index = self._load_runtime_observations(
            self._runtime_identity(node_catalog, evaluation_catalog),
            runtime_profile_id=runtime_profile_id,
            include_preliminary=include_preliminary,
            allow_unselected_profiles=not bool(
                METRIC_CONTRACTS.get(metric, {}).get("runtime")
            ),
        )
        runtime_node_identity: tuple[str, ...] = (
            (node_id, node_resource_id)
            if bool(METRIC_CONTRACTS.get(metric, {}).get("runtime"))
            else ()
        )
        cache_key = (
            str(evaluation_dataset.get("extractorVersion") or ""),
            YIELD_MODEL_VERSION,
            HARVEST_RANKING_POLICY_VERSION,
            RANKING_RESULT_SCHEMA,
            str(node_dataset.get("revision") or ""),
            evaluation_revision,
            str(evaluation_dataset.get("componentDatasetRevision") or ""),
            component_package.casefold(),
            resource_class.casefold(),
            resource_entry_index,
            *runtime_node_identity,
            usage_scope,
            evidence_policy,
            variant_policy,
            metric,
            availability_policy,
            runtime_index.revision,
            runtime_index.runtime_profile_selected,
            bool(include_preliminary),
        )
        with self._lock:
            cached = self._lazy_ranking_cache.pop(cache_key, None)
            if cached is not None:
                self._lazy_ranking_cache[cache_key] = cached
        if cached is None:
            computed = engine.rank_node_resource(
                node_catalog,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=10,
                evidence_policy=evidence_policy,
                variant_policy=variant_policy,
                metric=metric,
                availability_policy=availability_policy,
                runtime_observations=runtime_index.rows,
                runtime_profile_id=runtime_index.runtime_profile_selected,
                include_preliminary=include_preliminary,
                runtime_profiles_available=getattr(
                    runtime_index, "runtime_profiles_available", None
                ),
            )
            identity = dict(computed.get("identity") or {})
            identity["runtimeObservationRevision"] = runtime_index.revision
            computed["identity"] = identity
            computed["runtimeCoverage"] = {
                "filesScanned": runtime_index.files_scanned,
                **runtime_index.coverage,
            }
            with self._lock:
                existing = self._lazy_ranking_cache.pop(cache_key, None)
                cached = existing if existing is not None else copy.deepcopy(computed)
                self._lazy_ranking_cache[cache_key] = cached
                while len(self._lazy_ranking_cache) > _LAZY_CACHE_CAPACITY:
                    self._lazy_ranking_cache.popitem(last=False)
        return self._bind_lazy_result(
            cached,
            node_catalog=node_catalog,
            node=node,
            resource=resource,
            component_package=component_package,
            limit=limit,
        )

    def _v2_tier_baselines(
        self,
        engine: HarvestEvaluationEngine,
        node_catalog: dict[str, Any],
        *,
        evaluation_revision: str,
        node_id: str,
        node_resource_id: str,
        evidence_policy: str,
        variant_policy: str,
        metric: str,
        availability_policy: str,
        runtime_index: HarvestRuntimeObservationIndex,
        include_preliminary: bool,
    ) -> dict[str, Any]:
        """Cache the two evidence-tier baselines without materializing a cross product."""

        node_dataset = dict(node_catalog.get("dataset") or {})
        cache_key = (
            "V2_TIER_BASELINES",
            str(node_dataset.get("revision") or ""),
            evaluation_revision,
            YIELD_MODEL_VERSION,
            HARVEST_RANKING_POLICY_VERSION,
            RANKING_RESULT_SCHEMA,
            node_id,
            node_resource_id,
            evidence_policy,
            variant_policy,
            metric,
            availability_policy,
            runtime_index.revision,
            runtime_index.runtime_profile_selected,
            bool(include_preliminary),
        )
        with self._lock:
            cached = self._v2_tier_baseline_cache.pop(cache_key, None)
            if cached is not None:
                self._v2_tier_baseline_cache[cache_key] = cached
                return copy.deepcopy(cached)

        ranking = engine.rank_node_resource(
            node_catalog,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=1,
            evidence_policy=evidence_policy,
            variant_policy=variant_policy,
            metric=metric,
            availability_policy=availability_policy,
            runtime_observations=runtime_index.rows,
            runtime_profile_id=runtime_index.runtime_profile_selected,
            include_preliminary=include_preliminary,
            runtime_profiles_available=getattr(
                runtime_index, "runtime_profiles_available", None
            ),
        )
        computed: dict[str, Any] = {}
        for tier, field in (
            ("CONFIRMED", "confirmedItems"),
            ("CONDITIONAL", "conditionalItems"),
        ):
            rows = ranking.get(field)
            computed[tier] = (
                dict(rows[0])
                if isinstance(rows, list)
                and rows
                and isinstance(rows[0], dict)
                else None
            )

        with self._lock:
            existing = self._v2_tier_baseline_cache.pop(cache_key, None)
            cached = existing if existing is not None else copy.deepcopy(computed)
            self._v2_tier_baseline_cache[cache_key] = cached
            while (
                len(self._v2_tier_baseline_cache)
                > _V2_TIER_BASELINE_CACHE_CAPACITY
            ):
                self._v2_tier_baseline_cache.popitem(last=False)
        return copy.deepcopy(cached)

    def _top_baseline(
        self,
        evaluation_catalog: dict[str, Any],
        engine: HarvestEvaluationEngine,
        candidates: list[dict[str, Any]],
        *,
        evaluation_revision: str,
        component_package: str,
        resource: str,
        resource_entry_index: int | None,
    ) -> dict[str, Any] | None:
        usage_scope = str(
            evaluation_catalog.get("methodology", {}).get("usageScope") or ""
        )
        cache_key = (
            evaluation_revision,
            component_package.casefold(),
            resource.casefold(),
            resource_entry_index,
            usage_scope,
        )
        with self._lock:
            cached = self._top_baseline_cache.pop(cache_key, None)
            if cached is not None:
                self._top_baseline_cache[cache_key] = cached
        if cached is None:
            row = _best_discovered_scope_row(
                engine,
                component_package=component_package,
                resource=resource,
                resource_entry_index=resource_entry_index,
                candidates=candidates,
            )
            computed = {"row": copy.deepcopy(row)}
            with self._lock:
                existing = self._top_baseline_cache.pop(cache_key, None)
                cached = existing if existing is not None else computed
                self._top_baseline_cache[cache_key] = cached
                while len(self._top_baseline_cache) > _TOP_BASELINE_CACHE_CAPACITY:
                    self._top_baseline_cache.popitem(last=False)
        row = cached.get("row")
        return copy.deepcopy(row) if isinstance(row, dict) else None

    def _creature_pair_result(
        self,
        engine: HarvestEvaluationEngine,
        node_catalog: dict[str, Any],
        *,
        evaluation_revision: str,
        species_key: str,
        component_package: str,
        resource: str,
        resource_entry_index: int | None,
        usage_scope: str,
        node_id: str,
        node_resource_id: str,
    ) -> dict[str, Any]:
        cache_key = (
            evaluation_revision,
            species_key.casefold(),
            component_package.casefold(),
            resource.casefold(),
            resource_entry_index,
            usage_scope,
        )
        with self._lock:
            cached = self._creature_pair_cache.pop(cache_key, None)
            if cached is not None:
                self._creature_pair_cache[cache_key] = cached
        if cached is None:
            try:
                result = engine.rank_node_resource(
                    node_catalog,
                    node_id=node_id,
                    node_resource_id=node_resource_id,
                    limit=1,
                )
            except KeyError as exc:
                computed = {
                    "row": None,
                    "disposition": str(exc.args[0] if exc.args else exc),
                }
            else:
                items = result.get("items")
                row = (
                    dict(items[0])
                    if isinstance(items, list) and items and isinstance(items[0], dict)
                    else None
                )
                computed = {
                    "row": row,
                    "disposition": "RANKED" if row is not None else "NOT_RANKED_FOR_SPECIES",
                }
            with self._lock:
                existing = self._creature_pair_cache.pop(cache_key, None)
                cached = existing if existing is not None else computed
                self._creature_pair_cache[cache_key] = cached
                while len(self._creature_pair_cache) > _CREATURE_PAIR_CACHE_CAPACITY:
                    self._creature_pair_cache.popitem(last=False)
        return copy.deepcopy(cached)

    def list_nodes(
        self,
        *,
        q: str = "",
        map_name: str = "",
        only_map_family: str = "",
        resource: str = "",
        offset: int = 0,
        limit: int = 24,
    ) -> dict[str, Any]:
        if self.sqlite_catalog_path is not None:
            try:
                return self._load_sqlite_catalog().list_nodes(
                    q=q,
                    map_name=map_name,
                    only_map_family=only_map_family,
                    resource=resource,
                    offset=offset,
                    limit=limit,
                )
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        return query_resource_nodes(
            self._load_catalog(),
            q=q,
            map_name=map_name,
            only_map_family=only_map_family,
            resource=resource,
            offset=offset,
            limit=limit,
        )

    def list_creatures(
        self,
        *,
        q: str = "",
        offset: int = 0,
        limit: int = 24,
    ) -> dict[str, Any]:
        """Return one compact row per exact species identity."""

        query = " ".join(str(q or "").casefold().split())
        if len(query) > 100:
            raise ValueError("q must be at most 100 characters")
        bounded_offset = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 100))
        evaluation_catalog, _engine = self._load_evaluation()

        grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for creature in evaluation_catalog.get("creatures", []):
            if not isinstance(creature, dict):
                continue
            species_key = " ".join(
                str(
                    creature.get("speciesKey")
                    or creature.get("objectPath")
                    or creature.get("name")
                    or ""
                )
                .casefold()
                .split()
            )
            if not species_key:
                continue
            row = grouped.setdefault(
                species_key,
                {
                    "speciesKey": str(creature.get("speciesKey") or species_key),
                    "name": str(creature.get("name") or species_key),
                    "dinoNameTag": str(creature.get("dinoNameTag") or ""),
                    "variantCount": 0,
                    "attackCount": 0,
                    "attackVariantCount": 0,
                    "tameabilityStatuses": set(),
                    "rideabilityStatuses": set(),
                    "_representativePriority": _creature_representative_key(creature),
                },
            )
            priority = _creature_representative_key(creature)
            if priority < row["_representativePriority"]:
                row["name"] = str(creature.get("name") or species_key)
                row["dinoNameTag"] = str(creature.get("dinoNameTag") or "")
                row["_representativePriority"] = priority
            row["variantCount"] += 1
            attacks = creature.get("attacks")
            attack_count = len(attacks) if isinstance(attacks, list) else 0
            row["attackCount"] += attack_count
            if attack_count:
                row["attackVariantCount"] += 1
            for source_name, target_name in (
                ("tameability", "tameabilityStatuses"),
                ("rideability", "rideabilityStatuses"),
            ):
                source = creature.get(source_name)
                status = (
                    str(source.get("status") or "")
                    if isinstance(source, dict)
                    else ""
                )
                if status:
                    row[target_name].add(status)

        items: list[dict[str, Any]] = []
        for row in grouped.values():
            public_row = {
                **{
                    key: value
                    for key, value in row.items()
                    if key != "_representativePriority"
                },
                "tameabilityStatuses": sorted(row["tameabilityStatuses"]),
                "rideabilityStatuses": sorted(row["rideabilityStatuses"]),
            }
            searchable = " ".join(
                str(public_row.get(key) or "")
                for key in ("speciesKey", "name", "dinoNameTag")
            ).casefold()
            if query and query not in searchable:
                continue
            items.append(public_row)
        items.sort(
            key=lambda row: (
                str(row.get("name") or "").casefold(),
                str(row.get("speciesKey") or "").casefold(),
            )
        )
        total = len(items)
        page_items = copy.deepcopy(
            items[bounded_offset : bounded_offset + bounded_limit]
        )
        evaluation_coverage = dict(evaluation_catalog.get("coverage") or {})
        next_offset = (
            bounded_offset + len(page_items)
            if bounded_offset + len(page_items) < total
            else None
        )
        return {
            "schema": CREATURE_PAGE_SCHEMA,
            "dataset": copy.deepcopy(evaluation_catalog.get("dataset") or {}),
            "coverage": {
                "creatureAssets": len(evaluation_catalog.get("creatures", [])),
                "species": len(grouped),
                "claimsAllCreatures": evaluation_coverage.get("claimsAllCreatures")
                is True,
                "claimBlockers": [
                    str(value)
                    for value in evaluation_catalog.get("claimBlockers", [])
                    if str(value)
                ],
            },
            "total": total,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "nextOffset": next_offset,
            "items": page_items,
        }

    def creature_specialties(
        self,
        species_key: str,
        *,
        offset: int = 0,
        limit: int = 24,
        evidence_policy: str = POLICY_CONFIRMED,
        variant_policy: str = VARIANT_CANONICAL,
        metric: str = METRIC_STATIC_TOTAL,
        availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
    ) -> dict[str, Any]:
        """Return v2-contract specialties in relative-first server order."""

        evaluation_catalog, engine = self._load_evaluation()
        methodology = evaluation_catalog.get("methodology")
        if not isinstance(methodology, dict) or methodology.get(
            "contractVersion"
        ) != HARVEST_RANKING_CONTRACT_VERSION:
            return self._creature_specialties_v1(
                species_key,
                offset=offset,
                limit=limit,
            )
        sqlite_catalog: SQLiteHarvestCatalog | None = None
        if self.sqlite_catalog_path is not None:
            try:
                sqlite_catalog = self._load_sqlite_catalog()
                node_catalog = {"dataset": sqlite_catalog.dataset()}
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        else:
            node_catalog = self._load_catalog()
        # Validate policy values through the authoritative engine before any
        # potentially expensive traversal.
        if evidence_policy not in {POLICY_CONFIRMED, POLICY_INCLUDE_CONDITIONAL}:
            raise ValueError("Unsupported harvest evidence policy.")
        metric_contract = METRIC_CONTRACTS.get(metric)
        if metric_contract is None:
            raise ValueError("Unsupported harvest ranking metric.")
        runtime_index = self._load_runtime_observations(
            self._runtime_identity(node_catalog, evaluation_catalog),
            runtime_profile_id=runtime_profile_id,
            include_preliminary=include_preliminary,
            allow_unselected_profiles=not bool(metric_contract["runtime"]),
        )

        evaluation_revision, component_revision = self._evaluation_revisions(
            node_catalog, evaluation_catalog
        )
        requested_key = " ".join(str(species_key or "").casefold().split())
        variants = [
            creature
            for creature in evaluation_catalog.get("creatures", [])
            if isinstance(creature, dict)
            and " ".join(
                str(
                    creature.get("speciesKey")
                    or creature.get("objectPath")
                    or creature.get("name")
                    or ""
                )
                .casefold()
                .split()
            )
            == requested_key
        ]
        if not variants:
            raise KeyError("HARVEST_SPECIES_NOT_FOUND")
        bounded_offset = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 100))
        node_dataset = dict(node_catalog.get("dataset") or {})
        evaluation_dataset = dict(evaluation_catalog.get("dataset") or {})
        specialty_cache_key = (
            str(node_dataset.get("revision") or ""),
            evaluation_revision,
            component_revision,
            str(evaluation_dataset.get("extractorVersion") or ""),
            YIELD_MODEL_VERSION,
            HARVEST_RANKING_POLICY_VERSION,
            CREATURE_SPECIALTIES_SCHEMA,
            requested_key,
            bounded_offset,
            bounded_limit,
            evidence_policy,
            variant_policy,
            metric,
            availability_policy,
            runtime_index.revision,
            runtime_index.runtime_profile_selected,
            bool(include_preliminary),
        )
        with self._lock:
            cached_specialty = self._specialty_response_cache.pop(
                specialty_cache_key, None
            )
            if cached_specialty is not None:
                self._specialty_response_cache[
                    specialty_cache_key
                ] = cached_specialty
                return copy.deepcopy(cached_specialty)
        if sqlite_catalog is not None:
            try:
                node_catalog = sqlite_catalog.catalog_for_specialties()
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        species_engine = HarvestEvaluationEngine(
            {**evaluation_catalog, "creatures": variants}
        )

        representatives: OrderedDict[tuple[object, ...], tuple[str, str]] = (
            OrderedDict()
        )
        occurrences: list[
            tuple[tuple[object, ...], dict[str, Any], dict[str, Any]]
        ] = []
        nodes = node_catalog.get("nodes")
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            component_ref = node.get("harvestComponent")
            component_package = canonical_package_path(
                component_ref.get("packagePath")
                if isinstance(component_ref, dict)
                else ""
            )
            resources = node.get("resources", {}).get("items", [])
            for resource in resources if isinstance(resources, list) else []:
                if not isinstance(resource, dict):
                    continue
                raw_entry_index = resource.get("entryIndex")
                entry_index = (
                    int(raw_entry_index)
                    if isinstance(raw_entry_index, int)
                    and not isinstance(raw_entry_index, bool)
                    else None
                )
                evaluation_key: tuple[object, ...] = (
                    component_package.casefold(),
                    str(resource.get("resource") or "").casefold(),
                    entry_index,
                )
                pair = (
                    str(node.get("id") or ""),
                    str(resource.get("nodeResourceId") or ""),
                )
                key = (
                    (*evaluation_key, *pair)
                    if bool(metric_contract["runtime"])
                    else evaluation_key
                )
                representatives.setdefault(key, pair)
                occurrences.append((key, node, resource))

        selected_by_key: dict[
            tuple[object, ...], dict[str, dict[str, Any]]
        ] = {}
        top_by_key: dict[
            tuple[object, ...], dict[str, dict[str, Any]]
        ] = {}
        pair_dispositions: Counter[str] = Counter()
        for key, (node_id, node_resource_id) in representatives.items():
            selected_result = species_engine.rank_node_resource(
                node_catalog,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=10,
                evidence_policy=POLICY_INCLUDE_CONDITIONAL,
                variant_policy=variant_policy,
                metric=metric,
                availability_policy=availability_policy,
                runtime_observations=runtime_index.rows,
                runtime_profile_id=runtime_index.runtime_profile_selected,
                include_preliminary=include_preliminary,
                runtime_profiles_available=getattr(
                    runtime_index, "runtime_profiles_available", None
                ),
            )
            selected_rows_by_tier: dict[str, dict[str, Any]] = {}
            for tier, field in (
                ("CONFIRMED", "confirmedItems"),
                ("CONDITIONAL", "conditionalItems"),
            ):
                rows = selected_result.get(field)
                selected_row = next(
                    (
                        dict(row)
                        for row in rows
                        if isinstance(row, dict)
                        and str(row.get("speciesKey") or "").casefold()
                        == requested_key
                    ),
                    None,
                ) if isinstance(rows, list) else None
                if selected_row is not None:
                    selected_rows_by_tier[tier] = selected_row
            if not selected_rows_by_tier:
                pair_dispositions["NOT_RANKED_FOR_SPECIES"] += 1
                continue
            tier_baselines = self._v2_tier_baselines(
                engine,
                node_catalog,
                evaluation_revision=evaluation_revision,
                node_id=node_id,
                node_resource_id=node_resource_id,
                evidence_policy=POLICY_INCLUDE_CONDITIONAL,
                variant_policy=variant_policy,
                metric=metric,
                availability_policy=availability_policy,
                runtime_index=runtime_index,
                include_preliminary=include_preliminary,
            )
            for tier_key, selected_row in selected_rows_by_tier.items():
                cached_top_row = tier_baselines.get(tier_key)
                top_row = (
                    dict(cached_top_row)
                    if isinstance(cached_top_row, dict)
                    else None
                )
                if top_row is None:
                    pair_dispositions[f"{tier_key}_BASELINE_UNAVAILABLE"] += 1
                    continue
                selected_by_key.setdefault(key, {})[tier_key] = selected_row
                top_by_key.setdefault(key, {})[tier_key] = top_row
            if key in selected_by_key:
                pair_dispositions["RANKED"] += 1

        ranked_rows: list[dict[str, Any]] = []
        for key, node, resource in occurrences:
            selected_rows_by_tier = selected_by_key.get(key, {})
            top_rows_by_tier = top_by_key.get(key, {})
            for tier_key in ("CONFIRMED", "CONDITIONAL"):
                selected_row = selected_rows_by_tier.get(tier_key)
                top_row = top_rows_by_tier.get(tier_key)
                if selected_row is None or top_row is None:
                    continue
                selected_score = selected_row.get(metric)
                top_score = top_row.get(metric)
                if (
                    not isinstance(selected_score, (int, float))
                    or isinstance(selected_score, bool)
                    or not isinstance(top_score, (int, float))
                    or isinstance(top_score, bool)
                    or float(top_score) <= 0
                ):
                    continue
                relative_percent = round(
                    min(
                        100.0,
                        max(0.0, float(selected_score) / float(top_score) * 100.0),
                    ),
                    6,
                )
                component_ref = node.get("harvestComponent")
                component_package = canonical_package_path(
                    component_ref.get("packagePath")
                    if isinstance(component_ref, dict)
                    else ""
                )
                ranked_rows.append(
                    {
                        **_compact_specialty_row(selected_row),
                        "node": {
                            "id": node.get("id"),
                            "name": node.get("name"),
                            "objectPath": node.get("objectPath"),
                        },
                        "resource": {
                            **resource,
                            "harvestComponentPackagePath": component_package,
                        },
                        "selectedMetric": metric,
                        "selectedMetricValue": float(selected_score),
                        "nodeTopSelectedMetricValue": float(top_score),
                        "nodeTopStaticCompleteNodeTargetYield": top_row.get(
                            "staticCompleteNodeTargetYield"
                        ),
                        "nodeTopEstimatedYieldPerNode": top_row.get(
                            "estimatedYieldPerNode"
                        ),
                        "relativeToNodeTopPercent": relative_percent,
                        "relativeBasisTier": selected_row.get("rankingTier"),
                        "nodeTop": {
                            "speciesKey": top_row.get("speciesKey"),
                            "creature": top_row.get("creature"),
                            "creatureObjectPath": top_row.get("creatureObjectPath"),
                            "attackIndex": top_row.get("attackIndex"),
                            "attackName": top_row.get("attackName"),
                            "selectedMetric": metric,
                            "selectedMetricValue": float(top_score),
                            "staticCompleteNodeTargetYield": top_row.get(
                                "staticCompleteNodeTargetYield"
                            ),
                            "estimatedYieldPerNode": top_row.get(
                                "estimatedYieldPerNode"
                            ),
                            "rankingTier": top_row.get("rankingTier"),
                            "evidence": copy.deepcopy(top_row.get("evidence") or {}),
                        },
                    }
                )

        def rank_tier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows.sort(key=_specialty_page_sort_key)
            previous_primary: tuple[float, float] | None = None
            previous_rank = 0
            for ordinal, row in enumerate(rows, start=1):
                primary = _specialty_competition_key(row)
                if previous_primary is None or primary != previous_primary:
                    previous_rank = ordinal
                    previous_primary = primary
                row["rank"] = previous_rank
            return rows

        confirmed_all = rank_tier(
            [row for row in ranked_rows if row.get("rankingTier") == "CONFIRMED"]
        )
        conditional_all = rank_tier(
            [row for row in ranked_rows if row.get("rankingTier") != "CONFIRMED"]
        )
        visible_rows = [
            *confirmed_all,
            *(conditional_all if evidence_policy == POLICY_INCLUDE_CONDITIONAL else []),
        ]
        page_rows = visible_rows[bounded_offset : bounded_offset + bounded_limit]
        confirmed_page = [
            copy.deepcopy(row)
            for row in page_rows
            if row.get("rankingTier") == "CONFIRMED"
        ]
        conditional_page = [
            copy.deepcopy(row)
            for row in page_rows
            if row.get("rankingTier") != "CONFIRMED"
        ]

        evaluation_coverage = dict(evaluation_catalog.get("coverage") or {})
        claims_all_creatures = evaluation_coverage.get("claimsAllCreatures") is True
        claim_blockers = [
            str(value)
            for value in evaluation_catalog.get("claimBlockers", [])
            if str(value)
        ]
        representative = min(variants, key=_creature_representative_key)
        total = len(visible_rows)
        response = {
            "schema": CREATURE_SPECIALTIES_SCHEMA,
            "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
            "identity": {
                "extractorVersion": evaluation_dataset.get("extractorVersion"),
                "modelVersion": YIELD_MODEL_VERSION,
                "policyVersion": HARVEST_RANKING_POLICY_VERSION,
                "resultSchemaVersion": CREATURE_SPECIALTIES_SCHEMA,
                "nodeCatalogRevision": node_dataset.get("revision"),
                "evaluationCatalogRevision": evaluation_revision,
                "componentCatalogRevision": evaluation_dataset.get(
                    "componentDatasetRevision"
                ),
                "runtimeObservationRevision": runtime_index.revision,
            },
            "dataset": {
                **node_dataset,
                "evaluationRevision": evaluation_revision,
                "evaluationGeneratedAt": evaluation_dataset.get("generatedAt"),
            },
            "species": {
                "speciesKey": str(representative.get("speciesKey") or requested_key),
                "name": representative.get("name"),
                "dinoNameTag": representative.get("dinoNameTag"),
                "variantCount": len(variants),
            },
            "queryPolicy": {
                "evidence": evidence_policy,
                "variant": variant_policy,
                "metric": metric,
                "availability": availability_policy,
                "runtimeProfileId": runtime_index.runtime_profile_selected,
                "includePreliminary": bool(include_preliminary),
                "exploratory": variant_policy
                == "BEST_DISCOVERED_VARIANT_EXPLORATORY",
            },
            "methodology": {
                **methodology,
                "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
                "metric": metric,
                "sortMetric": (
                    "relativeToNodeTopPercent DESC, selectedMetricValue DESC, "
                    "resource.nodeResourceId, node.id, resource.resource, "
                    "node.objectPath, resource.entryIndex, "
                    "resource.harvestComponentPackagePath, "
                    "creatureObjectPath, attackIndex"
                ),
                "relativeBasis": "SAME_EVIDENCE_TIER_NODE_RESOURCE_TOP",
                "tiePolicy": (
                    "COMPETITION_RANK_FOR_EQUAL_RELATIVE_PERCENT_AND_SELECTED_METRIC"
                ),
                "scoreBasis": metric_contract["scoreBasis"],
                "unit": metric_contract["unit"],
                "runtime": metric_contract["runtime"],
            },
            "confirmedStatus": "AVAILABLE" if confirmed_all else "UNAVAILABLE",
            "conditionalStatus": "AVAILABLE" if conditional_all else "UNAVAILABLE",
            "scopeStatus": (
                "ALL_DISCOVERED_CREATURES_EVALUATED"
                if claims_all_creatures
                else "PARTIAL_CREATURE_EVIDENCE"
            ),
            "claimsCompleteWithinScope": claims_all_creatures,
            "claimsGlobalTop": False,
            "claimBlockers": claim_blockers,
            "evidence": {
                "status": "COMPLETE" if claims_all_creatures else "PARTIAL",
                "blockers": claim_blockers,
            },
            "coverage": {
                **evaluation_coverage,
                "speciesVariantsMatched": len(variants),
                "nodeResourcePairsDiscovered": len(occurrences),
                "uniqueEvaluationPairs": len(representatives),
                "uniqueEvaluationPairsRanked": len(selected_by_key),
                "nodeResourcePairsRanked": len(ranked_rows),
                "rankedConfirmed": len(confirmed_all),
                "rankedConditional": len(conditional_all),
                "pairDispositionCounts": dict(sorted(pair_dispositions.items())),
                "returned": len(page_rows),
                "omitted": max(0, total - bounded_offset - len(page_rows)),
            },
            "runtimeCoverage": {
                "filesScanned": runtime_index.files_scanned,
                **runtime_index.coverage,
            },
            "page": {
                "offset": bounded_offset,
                "limit": bounded_limit,
                "total": total,
                "returned": len(page_rows),
                "omitted": max(0, total - bounded_offset - len(page_rows)),
            },
            "total": total,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "nextOffset": (
                bounded_offset + len(page_rows)
                if bounded_offset + len(page_rows) < total
                else None
            ),
            "confirmedItems": confirmed_page,
            "conditionalItems": conditional_page,
            "items": list(confirmed_page),
        }
        with self._lock:
            existing = self._specialty_response_cache.pop(
                specialty_cache_key, None
            )
            cached_specialty = (
                existing if existing is not None else copy.deepcopy(response)
            )
            self._specialty_response_cache[
                specialty_cache_key
            ] = cached_specialty
            while (
                len(self._specialty_response_cache)
                > _SPECIALTY_RESPONSE_CACHE_CAPACITY
            ):
                self._specialty_response_cache.popitem(last=False)
        return copy.deepcopy(cached_specialty)

    def get_node(self, node_id: str) -> dict[str, Any]:
        if self.sqlite_catalog_path is not None:
            try:
                return self._load_sqlite_catalog().get_node(node_id)
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        catalog = self._load_catalog()
        nodes = catalog.get("nodes")
        for node in nodes if isinstance(nodes, list) else []:
            if isinstance(node, dict) and str(node.get("id") or "") == node_id:
                return dict(node)
        raise KeyError("RESOURCE_NODE_NOT_FOUND")

    def rankings(
        self,
        node_id: str,
        node_resource_id: str,
        *,
        limit: int = 10,
        evidence_policy: str = POLICY_CONFIRMED,
        variant_policy: str = VARIANT_CANONICAL,
        metric: str = METRIC_STATIC_TOTAL,
        availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
    ) -> dict[str, Any]:
        catalog = self._catalog_for_node(node_id)
        if self.evaluation_catalog_path is not None:
            evaluation, engine = self._load_evaluation()
            return self._lazy_rankings(
                catalog,
                evaluation,
                engine,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=limit,
                evidence_policy=evidence_policy,
                variant_policy=variant_policy,
                metric=metric,
                availability_policy=availability_policy,
                runtime_profile_id=runtime_profile_id,
                include_preliminary=include_preliminary,
            )
        ranking = self._load_ranking()
        dataset = catalog.get("dataset")
        expected_revision = (
            str(
                dataset.get("rankingDatasetRevision")
                or dataset.get("rankingScanManifestHash")
                or ""
            )
            if isinstance(dataset, dict)
            else ""
        )
        actual_revision = str(
            ranking.get("datasetRevision") or ranking.get("scanManifestHash") or ""
        )
        if expected_revision and expected_revision != actual_revision:
            raise HarvestDatasetInvalid(
                "Resource-node catalog and ranking report revisions do not match."
            )
        return rank_node_resource(
            catalog,
            ranking,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=limit,
        )

    def _creature_specialties_v1(
        self,
        species_key: str,
        *,
        offset: int = 0,
        limit: int = 24,
    ) -> dict[str, Any]:
        """Rank exact node/resource pairs for one species without a persisted cross product."""

        if self.sqlite_catalog_path is not None:
            try:
                node_catalog = self._load_sqlite_catalog().catalog_for_specialties()
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        else:
            node_catalog = self._load_catalog()
        evaluation_catalog, engine = self._load_evaluation()
        evaluation_revision, _component_revision = self._evaluation_revisions(
            node_catalog,
            evaluation_catalog,
        )
        requested_key = " ".join(str(species_key or "").casefold().split())
        variants = [
            creature
            for creature in evaluation_catalog.get("creatures", [])
            if isinstance(creature, dict)
            and " ".join(
                str(
                    creature.get("speciesKey")
                    or creature.get("objectPath")
                    or creature.get("name")
                    or ""
                )
                .casefold()
                .split()
            )
            == requested_key
        ]
        if not variants:
            raise KeyError("HARVEST_SPECIES_NOT_FOUND")

        species_catalog = {
            **evaluation_catalog,
            "creatures": variants,
        }
        species_engine = HarvestEvaluationEngine(species_catalog)
        top_candidates, _variant_counts = _eligible_attack_candidates(
            evaluation_catalog
        )
        representatives: OrderedDict[
            tuple[str, str, int | None], tuple[str, str]
        ] = OrderedDict()
        occurrences: list[
            tuple[tuple[str, str, int | None], dict[str, Any], dict[str, Any]]
        ] = []
        nodes = node_catalog.get("nodes")
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            component_ref = node.get("harvestComponent")
            component_package = canonical_package_path(
                component_ref.get("packagePath")
                if isinstance(component_ref, dict)
                else ""
            )
            resources = node.get("resources", {}).get("items", [])
            for resource in resources if isinstance(resources, list) else []:
                if not isinstance(resource, dict):
                    continue
                raw_entry_index = resource.get("entryIndex")
                entry_index = (
                    int(raw_entry_index)
                    if isinstance(raw_entry_index, int)
                    and not isinstance(raw_entry_index, bool)
                    else None
                )
                key = (
                    component_package.casefold(),
                    str(resource.get("resource") or "").casefold(),
                    entry_index,
                )
                node_id = str(node.get("id") or "")
                node_resource_id = str(resource.get("nodeResourceId") or "")
                representatives.setdefault(key, (node_id, node_resource_id))
                occurrences.append((key, node, resource))

        selected_by_key: dict[tuple[str, str, int | None], dict[str, Any]] = {}
        top_by_key: dict[tuple[str, str, int | None], dict[str, Any]] = {}
        pair_dispositions: Counter[str] = Counter()
        usage_scope = str(
            evaluation_catalog.get("methodology", {}).get("usageScope") or ""
        )
        for key, (node_id, node_resource_id) in representatives.items():
            component_package, resource_name, resource_entry_index = key
            selected_result = self._creature_pair_result(
                species_engine,
                node_catalog,
                evaluation_revision=evaluation_revision,
                species_key=requested_key,
                component_package=component_package,
                resource=resource_name,
                resource_entry_index=resource_entry_index,
                usage_scope=usage_scope,
                node_id=node_id,
                node_resource_id=node_resource_id,
            )
            selected_row = selected_result.get("row")
            if not isinstance(selected_row, dict):
                pair_dispositions[str(selected_result.get("disposition") or "UNKNOWN")] += 1
                continue
            top_row = self._top_baseline(
                evaluation_catalog,
                engine,
                top_candidates,
                evaluation_revision=evaluation_revision,
                component_package=component_package,
                resource=str(selected_row.get("resource") or ""),
                resource_entry_index=resource_entry_index,
            )
            if top_row is None:
                pair_dispositions["NODE_TOP_NOT_AVAILABLE"] += 1
                continue
            selected_by_key[key] = selected_row
            top_by_key[key] = top_row
            pair_dispositions["RANKED"] += 1

        ranked_rows: list[dict[str, Any]] = []
        for key, node, resource in occurrences:
            selected_row = selected_by_key.get(key)
            top_row = top_by_key.get(key)
            if selected_row is None or top_row is None:
                continue
            selected_score = selected_row.get("estimatedYieldPerNode")
            top_score = top_row.get("estimatedYieldPerNode")
            if not isinstance(selected_score, (int, float)) or not isinstance(
                top_score, (int, float)
            ) or float(top_score) <= 0:
                continue
            relative_percent = round(
                min(100.0, max(0.0, float(selected_score) / float(top_score) * 100.0)),
                6,
            )
            component_ref = node.get("harvestComponent")
            component_package = canonical_package_path(
                component_ref.get("packagePath")
                if isinstance(component_ref, dict)
                else ""
            )
            ranked_rows.append(
                {
                    **_compact_specialty_row(selected_row),
                    "node": {
                        "id": node.get("id"),
                        "name": node.get("name"),
                        "objectPath": node.get("objectPath"),
                    },
                    "resource": {
                        **resource,
                        "harvestComponentPackagePath": component_package,
                    },
                    "nodeTopEstimatedYieldPerNode": float(top_score),
                    # Compatibility alias for one release.  It deliberately
                    # carries the same units/value as the new yield metric.
                    "nodeTopEngineComparisonIndex": float(top_score),
                    "relativeToNodeTopPercent": relative_percent,
                    "nodeTop": {
                        "speciesKey": top_row.get("speciesKey"),
                        "creature": top_row.get("creature"),
                        "creatureObjectPath": top_row.get("creatureObjectPath"),
                        "attackIndex": top_row.get("attackIndex"),
                        "attackName": top_row.get("attackName"),
                        "estimatedYieldPerNode": float(top_score),
                        "engineComparisonIndex": float(top_score),
                        "rankingTier": top_row.get("rankingTier"),
                        "evidence": copy.deepcopy(top_row.get("evidence") or {}),
                    },
                }
            )

        ranked_rows.sort(
            key=lambda row: (
                -float(row.get("estimatedYieldPerNode") or 0.0),
                -float(row.get("relativeToNodeTopPercent") or 0.0),
                str(row.get("resource", {}).get("resource") or "").casefold(),
                str(row.get("node", {}).get("name") or "").casefold(),
                str(row.get("node", {}).get("id") or ""),
            )
        )
        previous_score: float | None = None
        previous_rank = 0
        for ordinal, row in enumerate(ranked_rows, start=1):
            score = float(row.get("estimatedYieldPerNode") or 0.0)
            if previous_score is None or score != previous_score:
                previous_rank = ordinal
                previous_score = score
            row["rank"] = previous_rank
        bounded_offset = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 100))
        page_items = [
            copy.deepcopy(row)
            for row in ranked_rows[bounded_offset : bounded_offset + bounded_limit]
        ]

        evaluation_coverage = dict(evaluation_catalog.get("coverage") or {})
        claims_all_creatures = evaluation_coverage.get("claimsAllCreatures") is True
        claim_blockers = [
            str(value)
            for value in evaluation_catalog.get("claimBlockers", [])
            if str(value)
        ]
        representative = min(variants, key=_creature_representative_key)
        canonical_species_key = str(representative.get("speciesKey") or requested_key)
        total = len(ranked_rows)
        return {
            "schema": "blueprint-to-code.harvest-creature-specialties/v2",
            "dataset": {
                **dict(node_catalog.get("dataset") or {}),
                "evaluationRevision": evaluation_revision,
                "evaluationGeneratedAt": evaluation_catalog.get("dataset", {}).get(
                    "generatedAt"
                ),
            },
            "species": {
                "speciesKey": canonical_species_key,
                "name": representative.get("name"),
                "dinoNameTag": representative.get("dinoNameTag"),
                "variantCount": len(variants),
            },
            "methodology": {
                **dict(evaluation_catalog.get("methodology") or {}),
                "metric": "estimatedYieldPerNode",
                "sortMetric": "estimatedYieldPerNode",
                "relativeBasis": (
                    "SELECTED_SPECIES_ESTIMATED_YIELD_PER_NODE_DIVIDED_BY_"
                    "NODE_RESOURCE_TOP_ESTIMATED_YIELD_PER_NODE"
                ),
                "tiePolicy": (
                    "COMPETITION_RANK_FOR_EQUAL_ESTIMATED_YIELD_1_1_3"
                ),
                "scoreBasis": YIELD_SCORE_BASIS,
                "engineComparisonIndexCompatibility": (
                    "ALIAS_OF_ESTIMATED_YIELD_PER_NODE_NOT_USED_FOR_SELECTION"
                ),
            },
            "scopeStatus": (
                "ALL_DISCOVERED_CREATURES_EVALUATED"
                if claims_all_creatures
                else "PARTIAL_CREATURE_EVIDENCE"
            ),
            "claimsCompleteWithinScope": claims_all_creatures,
            "claimsGlobalTop": False,
            "claimBlockers": claim_blockers,
            "evidence": {
                "status": "COMPLETE" if claims_all_creatures else "PARTIAL",
                "blockers": claim_blockers,
            },
            "coverage": {
                **evaluation_coverage,
                "speciesVariantsMatched": len(variants),
                "nodeResourcePairsDiscovered": len(occurrences),
                "uniqueEvaluationPairs": len(representatives),
                "uniqueEvaluationPairsRanked": len(selected_by_key),
                "nodeResourcePairsRanked": total,
                "pairDispositionCounts": dict(sorted(pair_dispositions.items())),
                "returned": len(page_items),
                "omitted": max(0, total - len(page_items)),
            },
            "page": {
                "offset": bounded_offset,
                "limit": bounded_limit,
                "total": total,
                "returned": len(page_items),
                "omitted": max(0, total - bounded_offset - len(page_items)),
            },
            "items": page_items,
        }
