"""Reverse-specialty evaluation helpers shared by repositories and tests."""

from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING, Any

from ..model.attack_evaluation import evaluate_attack_resource
from ..facts.extraction import normalize_unreal_object_identity
from ..model.complete_node import estimate_complete_node_yield
from .aggregation import prepare_attack_for_usage_scope

if TYPE_CHECKING:
    from .engine import HarvestEvaluationEngine


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
