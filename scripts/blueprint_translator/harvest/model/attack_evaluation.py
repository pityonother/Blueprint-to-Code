"""Evaluate one recovered attack against one exact node resource entry."""

from __future__ import annotations

import math
from typing import Any, Iterable

from ..contracts import (
    INFORMATIONAL_QUANTITY_GAPS,
    STATIC_MODEL_OMITTED_FACTORS,
    YIELD_SCORE_BASIS,
)
from ..facts.extraction import normalize_unreal_object_identity
from .complete_node import estimate_complete_node_yield

_INFORMATIONAL_QUANTITY_GAPS = INFORMATIONAL_QUANTITY_GAPS
_STATIC_MODEL_OMITTED_FACTORS = STATIC_MODEL_OMITTED_FACTORS

def damage_type_chain(damage_type: str, parents: dict[str, str]) -> list[str]:
    chain: list[str] = []
    normalized_parents = {
        normalize_unreal_object_identity(child): normalize_unreal_object_identity(parent)
        for child, parent in parents.items()
        if normalize_unreal_object_identity(child)
    }
    current = normalize_unreal_object_identity(damage_type)
    seen: set[str] = set()
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = normalized_parents.get(current, "")
    return chain


def _nearest_override(
    overrides: dict[str, Any],
    chain: list[str],
    fallback: Any,
) -> tuple[Any, str | None]:
    normalized_overrides = {
        normalize_unreal_object_identity(key): value
        for key, value in overrides.items()
        if normalize_unreal_object_identity(key)
    }
    for damage_type in chain:
        identity = normalize_unreal_object_identity(damage_type)
        if identity in normalized_overrides:
            return normalized_overrides[identity], identity
    return fallback, None


def _unranked_row(
    base: dict[str, Any],
    reason_code: str,
    missing: Iterable[str] = (),
    *,
    scope: str = "unknown",
    missing_by_scope: dict[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    scoped: dict[str, list[str]] = {}
    if missing_by_scope:
        for scope_name, values in missing_by_scope.items():
            normalized = sorted({str(item) for item in values if str(item)})
            if normalized:
                scoped[str(scope_name)] = normalized
    direct = sorted({str(item) for item in missing if str(item)})
    if direct:
        scoped[scope] = sorted(set(scoped.get(scope, [])) | set(direct))
    flattened = sorted({item for values in scoped.values() for item in values})
    return {
        **base,
        "rankingStatus": "UNRANKED",
        "reasonCode": reason_code,
        "missingFacts": flattened,
        "missingFactsByScope": scoped,
        "estimatedYieldPerNode": None,
        "engineComparisonIndex": None,
        "harvestPressurePerSecond": None,
        "observedYieldPerSecond": None,
        "scoreBasis": YIELD_SCORE_BASIS,
    }


def evaluate_attack_resource(
    *,
    creature: str,
    creature_object_path: str,
    attack: dict[str, Any],
    component: dict[str, Any],
    resource: str,
    resource_entry_index: int | None = None,
    damage_type_parents: dict[str, str],
    resource_damage_overrides: dict[tuple[str, str], str],
    damage_type_gaps: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Estimate one attack's target-resource yield from one complete node."""

    source_damage_type = normalize_unreal_object_identity(attack.get("damageType"))
    target_resource = normalize_unreal_object_identity(resource)
    target_entry_index = (
        int(resource_entry_index)
        if isinstance(resource_entry_index, int) and not isinstance(resource_entry_index, bool)
        else None
    )
    base: dict[str, Any] = {
        "creature": creature,
        "creatureObjectPath": creature_object_path,
        "attackIndex": attack.get("attackIndex"),
        "attackName": attack.get("attackName"),
        "sourceDamageType": source_damage_type,
        "component": component.get("component"),
        "componentObjectPath": component.get("objectPath"),
        "resource": target_resource,
        "resourceEntryIndex": target_entry_index,
        "estimatedYieldPerNode": None,
        "observedYieldPerSecond": None,
    }
    component_warnings = sorted(set(component.get("informationalGaps") or []))
    if component_warnings:
        base["warnings"] = component_warnings
        base["warningsByScope"] = {"component": component_warnings}
    missing = [
        str(gap)
        for gap in attack.get("gaps") or []
        if str(gap) != "AttackInterval"
    ]
    if not source_damage_type:
        missing.append("MeleeDamageType")
    if not isinstance(attack.get("baseDamage"), (int, float)):
        missing.append("MeleeDamageAmount")
    if missing:
        return _unranked_row(
            base,
            "REQUIRED_ATTACK_FACT_NOT_RECOVERED",
            missing,
            scope="attack",
        )

    component_ranking_gaps = component.get("rankingGaps")
    if not isinstance(component_ranking_gaps, list):
        component_ranking_gaps = [
            str(gap)
            for gap in component.get("gaps") or []
            if str(gap).startswith("HARVEST_")
        ]
    container_gaps = [
        str(gap)
        for gap in component_ranking_gaps
        if str(gap)
        in {
            "HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED",
            "HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED",
        }
    ]
    if container_gaps:
        return _unranked_row(
            base,
            "REQUIRED_COMPONENT_FACT_NOT_RECOVERED",
            container_gaps,
            scope="component",
        )

    attack_interval_value = (
        float(attack["attackInterval"])
        if isinstance(attack.get("attackInterval"), (int, float))
        else None
    )
    base_damage_value = float(attack["baseDamage"])
    base.update(
        {
            "baseDamage": base_damage_value,
            "attackInterval": attack_interval_value,
            "potentialAttackRate": (
                base_damage_value / attack_interval_value
                if isinstance(attack_interval_value, float)
                and attack_interval_value > 0
                else None
            ),
        }
    )
    if base_damage_value <= 0:
        return {
            **base,
            "rankingStatus": "INCOMPATIBLE",
            "reasonCode": "NON_POSITIVE_HARVEST_DAMAGE",
            "missingFacts": [],
            "estimatedYieldPerNode": None,
            "engineComparisonIndex": None,
            "harvestPressurePerSecond": None,
            "observedYieldPerSecond": None,
            "scoreBasis": YIELD_SCORE_BASIS,
        }
    if attack.get("useBlueprintAdjustOutputDamage") is True:
        return _unranked_row(
            base,
            "BLUEPRINT_OUTPUT_DAMAGE_NOT_RECOVERED",
            ["BlueprintAdjustOutputDamage"],
            scope="attack",
        )

    normalized_resource_overrides = {
        (
            normalize_unreal_object_identity(source),
            normalize_unreal_object_identity(candidate_resource),
        ): normalize_unreal_object_identity(replacement)
        for (source, candidate_resource), replacement in resource_damage_overrides.items()
    }
    effective_damage_type = normalized_resource_overrides.get(
        (source_damage_type, target_resource), source_damage_type
    )
    chain = damage_type_chain(effective_damage_type, damage_type_parents)
    base.update(
        {
            "effectiveDamageType": effective_damage_type,
            "damageOverrideApplied": effective_damage_type != source_damage_type,
            "damageTypeChain": chain,
        }
    )
    damage_type_gaps = {
        normalize_unreal_object_identity(key): list(value)
        for key, value in (damage_type_gaps or {}).items()
    }
    direct_damage_type_gaps = [
        gap
        for damage_type in {source_damage_type, effective_damage_type}
        for gap in damage_type_gaps.get(str(damage_type), [])
    ]
    if direct_damage_type_gaps:
        return _unranked_row(
            base,
            "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED",
            direct_damage_type_gaps,
            scope="damageType",
        )

    resource_entries = component.get("resourceEntries")
    if not isinstance(resource_entries, list):
        return _unranked_row(
            base,
            "REQUIRED_COMPONENT_FACT_NOT_RECOVERED",
            ["HarvestResourceEntries"],
            scope="component",
        )
    has_indexed_resource_entries = any(
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
            and normalize_unreal_object_identity(entry.get("resource")) == target_resource
            and (
                target_entry_index is None
                or not has_indexed_resource_entries
                or entry.get("entryIndex") == target_entry_index
            )
        ),
        None,
    )
    if not isinstance(target_entry, dict):
        resource_identity_gaps = [
            "ResourceItem"
            for entry in resource_entries
            if isinstance(entry, dict)
            and "RESOURCE_ITEM_NOT_RECOVERED" in (entry.get("rankingGaps") or entry.get("gaps") or [])
        ]
        if resource_identity_gaps:
            return _unranked_row(
                base,
                "TARGET_RESOURCE_FACT_NOT_RECOVERED",
                resource_identity_gaps,
                scope="target",
            )
        return {
            **base,
            "rankingStatus": "INCOMPATIBLE",
            "reasonCode": "RESOURCE_NOT_IN_COMPONENT",
            "missingFacts": [],
            "estimatedYieldPerNode": None,
            "engineComparisonIndex": None,
            "harvestPressurePerSecond": None,
            "observedYieldPerSecond": None,
            "scoreBasis": YIELD_SCORE_BASIS,
        }
    target_warnings = sorted(
        set(target_entry.get("informationalGaps") or [])
        | (set(target_entry.get("gaps") or []) & _INFORMATIONAL_QUANTITY_GAPS)
    )
    if target_warnings:
        base.setdefault("warningsByScope", {})["target"] = target_warnings
        base["warnings"] = sorted(
            set(base.get("warnings") or []) | set(target_warnings)
        )

    damage_entries = component.get("damageEntries")
    if not isinstance(damage_entries, list):
        return _unranked_row(
            base,
            "REQUIRED_COMPONENT_FACT_NOT_RECOVERED",
            ["HarvestDamageTypeEntries"],
            scope="component",
        )
    damage_entry: dict[str, Any] | None = None
    damage_match: str | None = None
    unresolved_chain_gaps: list[str] = []
    for candidate in chain:
        candidate_entry = next(
            (
                entry
                for entry in damage_entries
                if isinstance(entry, dict)
                and normalize_unreal_object_identity(entry.get("damageTypeParent")) == candidate
            ),
            None,
        )
        candidate_gaps = damage_type_gaps.get(candidate, [])
        if candidate_entry is not None:
            if unresolved_chain_gaps or candidate_gaps:
                return _unranked_row(
                    base,
                    "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED",
                    [*unresolved_chain_gaps, *candidate_gaps],
                    scope="damageType",
                )
            damage_entry = candidate_entry
            damage_match = candidate
            break
        unresolved_chain_gaps.extend(candidate_gaps)
    if damage_entry is None:
        unresolved_damage_entries = [
            gap
            for entry in damage_entries
            if isinstance(entry, dict)
            for gap in entry.get("gaps") or []
            if gap == "DAMAGE_TYPE_PARENT_NOT_RECOVERED"
        ]
        if unresolved_chain_gaps:
            return _unranked_row(
                base,
                "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED",
                unresolved_chain_gaps,
                scope="damageType",
            )
        if unresolved_damage_entries:
            return _unranked_row(
                base,
                "REQUIRED_COMPONENT_FACT_NOT_RECOVERED",
                unresolved_damage_entries,
                scope="component",
            )
        return {
            **base,
            "rankingStatus": "INCOMPATIBLE",
            "reasonCode": "DAMAGE_TYPE_NOT_ACCEPTED",
            "missingFacts": [],
            "damageTypeMatch": None,
            "estimatedYieldPerNode": None,
            "engineComparisonIndex": None,
            "harvestPressurePerSecond": None,
            "observedYieldPerSecond": None,
            "scoreBasis": YIELD_SCORE_BASIS,
        }

    weighted_entries: list[tuple[dict[str, Any], float | None, str | None]] = []
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
            matched = None
        else:
            overrides = entry.get("weightOverrides")
            value, matched = _nearest_override(
                overrides if isinstance(overrides, dict) else {},
                chain,
                entry.get("entryWeight"),
            )
            weight = float(value) if isinstance(value, (int, float)) else None
        weighted_entries.append((entry, weight, matched))
    target_weight_row = next(
        (
            row
            for row in weighted_entries
            if row[0] is target_entry
        ),
        None,
    )
    if target_weight_row is None or target_weight_row[1] is None:
        return _unranked_row(
            base,
            "RESOURCE_WEIGHT_NOT_RECOVERED",
            ["EntryWeight"],
            scope="target",
        )
    resource_weight = float(target_weight_row[1])
    base.update(
        {
            "damageTypeMatch": damage_match,
            "resourceWeight": resource_weight,
            "resourceWeightMatch": target_weight_row[2],
        }
    )
    if resource_weight <= 0.0:
        return {
            **base,
            "rankingStatus": "INCOMPATIBLE",
            "reasonCode": "ZERO_RESOURCE_WEIGHT",
            "missingFacts": [],
            "resourceWeightShare": 0.0,
            "estimatedYieldPerNode": None,
            "engineComparisonIndex": None,
            "harvestPressurePerSecond": None,
            "observedYieldPerSecond": None,
            "scoreBasis": YIELD_SCORE_BASIS,
        }
    unknown_competing_weights = [
        f"EntryWeight:{entry.get('resource') or '#' + str(entry.get('entryIndex'))}"
        for entry, weight, _match in weighted_entries
        if entry is not target_entry and weight is None
    ]
    if unknown_competing_weights:
        return _unranked_row(
            base,
            "RESOURCE_WEIGHT_NORMALIZATION_NOT_RECOVERED",
            unknown_competing_weights,
            scope="component",
        )
    total_positive_weight = sum(
        max(0.0, float(weight))
        for _entry, weight, _match in weighted_entries
        if weight is not None
    )
    base["totalPositiveResourceWeight"] = total_positive_weight
    if total_positive_weight <= 0.0:
        return _unranked_row(
            base,
            "RESOURCE_WEIGHT_NORMALIZATION_NOT_RECOVERED",
            ["EntryWeight"],
            scope="component",
        )

    damage_multiplier = damage_entry.get("damageMultiplier")
    quantity_multiplier = damage_entry.get("harvestQuantityMultiplier")
    component_coefficients: list[str] = []
    if not isinstance(damage_multiplier, (int, float)):
        component_coefficients.append("DamageMultiplier")
    if not isinstance(quantity_multiplier, (int, float)):
        component_coefficients.append("HarvestQuantityMultiplier")
    max_harvest_health = component.get("maxHarvestHealth")
    give_resource_interval = component.get("harvestHealthGiveResourceInterval")
    if not isinstance(max_harvest_health, (int, float)):
        component_coefficients.append("MaxHarvestHealth")
    if not isinstance(give_resource_interval, (int, float)):
        component_coefficients.append("HarvestHealthGiveResourceInterval")
    if component_coefficients:
        return _unranked_row(
            base,
            "REQUIRED_COEFFICIENT_NOT_RECOVERED",
            missing_by_scope={"component": component_coefficients},
        )
    if component.get("isSingleUnitHarvest") is True:
        return _unranked_row(
            base,
            "SINGLE_UNIT_HARVEST_MODEL_NOT_RECOVERED",
            ["bIsSingleUnitHarvest"],
            scope="component",
        )

    target_entry_gaps = set(
        target_entry.get("rankingGaps") or target_entry.get("gaps") or []
    )
    target_override_types = target_entry.get("damageTypeEntryValues")

    def relevant_quantity_override_gap(gap_code: str) -> bool:
        return gap_code in target_entry_gaps and (
            not isinstance(target_override_types, list)
            or any(
                normalize_unreal_object_identity(candidate) in chain
                for candidate in target_override_types
            )
        )

    unresolved_quantity_overrides = [
        gap_code
        for gap_code in (
            "DAMAGE_TYPE_MIN_QUANTITY_OVERRIDE_NOT_RECOVERED",
            "DAMAGE_TYPE_MAX_QUANTITY_OVERRIDE_NOT_RECOVERED",
        )
        if relevant_quantity_override_gap(gap_code)
    ]
    if unresolved_quantity_overrides:
        return _unranked_row(
            base,
            "RESOURCE_QUANTITY_MODEL_NOT_RECOVERED",
            unresolved_quantity_overrides,
            scope="target",
        )

    min_quantity, min_match = _nearest_override(
        target_entry.get("minQuantityOverrides")
        if isinstance(target_entry.get("minQuantityOverrides"), dict)
        else {},
        chain,
        target_entry.get("overrideQuantityMin"),
    )
    max_quantity, max_match = _nearest_override(
        target_entry.get("maxQuantityOverrides")
        if isinstance(target_entry.get("maxQuantityOverrides"), dict)
        else {},
        chain,
        target_entry.get("overrideQuantityMax"),
    )
    quantity_random_power = target_entry.get("overrideQuantityRandomPower")
    if quantity_random_power is None:
        # Legacy component reports predate this field.  The native struct and
        # every resource entry in the current DevKit corpus use the linear 1.0
        # default, so the compatibility assumption is explicit in the output.
        quantity_random_power = 1.0
        random_power_source = "NATIVE_LINEAR_DEFAULT_FOR_LEGACY_REPORT"
    else:
        random_power_source = "RECOVERED_COMPONENT_VALUE"
    quantity_facts: list[str] = []
    if not isinstance(min_quantity, (int, float)):
        quantity_facts.append("OverrideQuantityMin")
    if not isinstance(max_quantity, (int, float)):
        quantity_facts.append("OverrideQuantityMax")
    if not isinstance(quantity_random_power, (int, float)):
        quantity_facts.append("OverrideQuantityRandomPower")
    if quantity_facts:
        return _unranked_row(
            base,
            "RESOURCE_QUANTITY_MODEL_NOT_RECOVERED",
            quantity_facts,
            scope="target",
        )
    additional_effectiveness = damage_entry.get(
        "damageHarvestAdditionalEffectiveness"
    )
    if additional_effectiveness is None:
        additional_effectiveness = 0.0
    if not isinstance(additional_effectiveness, (int, float)):
        return _unranked_row(
            base,
            "HARVEST_EFFECTIVENESS_MODEL_NOT_RECOVERED",
            ["DamageHarvestAdditionalEffectiveness"],
            scope="component",
        )
    if not math.isclose(
        float(additional_effectiveness), 0.0, rel_tol=0.0, abs_tol=1e-9
    ):
        return _unranked_row(
            base,
            "NONZERO_HARVEST_EFFECTIVENESS_MODEL_NOT_IMPLEMENTED",
            ["DamageHarvestAdditionalEffectiveness=0"],
            scope="component",
        )
    weight_share = resource_weight / total_positive_weight
    try:
        yield_estimate = estimate_complete_node_yield(
            base_damage=base_damage_value,
            damage_multiplier=float(damage_multiplier),
            harvest_quantity_multiplier=float(quantity_multiplier),
            max_harvest_health=float(max_harvest_health),
            harvest_health_give_resource_interval=float(give_resource_interval),
            resource_weight_share=weight_share,
            minimum_quantity=float(min_quantity),
            maximum_quantity=float(max_quantity),
            quantity_random_power=float(quantity_random_power),
            clamp_resource_harvest_damage=bool(
                component.get("clampResourceHarvestDamage")
            ),
        )
    except ValueError as exc:
        return _unranked_row(
            base,
            "COMPLETE_NODE_YIELD_MODEL_NOT_APPLICABLE",
            [str(exc)],
            scope="yieldModel",
        )
    attack_interval = attack_interval_value
    pressure = (
        base_damage_value
        / attack_interval
        * float(damage_multiplier)
        * float(quantity_multiplier)
        if isinstance(attack_interval, float) and attack_interval > 0
        else None
    )
    legacy_index = pressure * weight_share if pressure is not None else None
    estimated_yield = float(yield_estimate["estimatedYieldPerNode"])
    estimated_hits = int(yield_estimate["estimatedHitsToDepleteNode"])
    static_cycle_seconds = (
        float(estimated_hits) * attack_interval
        if isinstance(attack_interval, float) and attack_interval > 0
        else None
    )
    static_cycle_speed = (
        estimated_yield / static_cycle_seconds
        if isinstance(static_cycle_seconds, float) and static_cycle_seconds > 0
        else None
    )
    effectiveness_quantity_multiplier = target_entry.get(
        "effectivenessQuantityMultiplier"
    )
    conditional_evidence = bool(
        attack.get("usageConditionReasonCodes")
        or attack.get("useBlueprintCanRiderAttack") is True
        or attack.get("useBlueprintAdjustOutputDamage") is True
    )
    score_breakdown = {
        "metric": "estimatedYieldPerNode",
        "grantCalls": yield_estimate["estimatedGrantCallsPerNode"],
        "resourceWeightShare": weight_share,
        "expectedQuantityPerSelection": yield_estimate[
            "expectedQuantityPerSelection"
        ],
        "estimatedHits": yield_estimate["estimatedHitsToDepleteNode"],
        "effectiveDamagePerHit": base_damage_value * float(damage_multiplier),
        "contributions": [
            {
                "factor": "grantCalls",
                "value": yield_estimate["estimatedGrantCallsPerNode"],
            },
            {"factor": "resourceWeightShare", "value": weight_share},
            {
                "factor": "expectedQuantityPerSelection",
                "value": yield_estimate["expectedQuantityPerSelection"],
            },
        ],
        "omittedFactors": list(_STATIC_MODEL_OMITTED_FACTORS),
        "evidenceTier": "CONDITIONAL" if conditional_evidence else "CONFIRMED",
    }
    return {
        **base,
        **yield_estimate,
        "rankingStatus": "RANKED",
        "reasonCode": "COMPLETE_NODE_YIELD_ESTIMATED",
        "missingFacts": [],
        "baseDamage": base_damage_value,
        "attackInterval": attack_interval,
        "damageMultiplier": float(damage_multiplier),
        "harvestQuantityMultiplier": float(quantity_multiplier),
        "damageHarvestAdditionalEffectiveness": float(additional_effectiveness),
        "effectivenessQuantityMultiplier": effectiveness_quantity_multiplier,
        "resourceWeightShare": weight_share,
        "overrideQuantityMin": min_quantity,
        "overrideQuantityMax": max_quantity,
        "overrideQuantityRandomPower": float(quantity_random_power),
        "quantityRandomPowerSource": random_power_source,
        "quantityOverrideMatch": min_match or max_match,
        "maxHarvestHealth": float(max_harvest_health),
        "harvestHealthGiveResourceInterval": float(give_resource_interval),
        "harvestPressurePerSecond": pressure,
        "estimatedYieldPerNode": estimated_yield,
        "staticCompleteNodeTargetYield": estimated_yield,
        "staticAttackCycleSecondsToDepleteNode": static_cycle_seconds,
        "staticYieldPerAttackCycleSecond": static_cycle_speed,
        "staticFirstHitTiming": "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE",
        # One-release compatibility alias.  It intentionally equals the new
        # yield metric so an old renderer cannot show a score that contradicts
        # the order returned by the API.
        "engineComparisonIndex": estimated_yield,
        "legacyDiagnostics": {
            "harvestPressurePerSecond": pressure,
            "engineComparisonIndex": legacy_index,
            "scoreBasis": "DEPRECATED_ATTACK_CADENCE_COEFFICIENT",
        },
        "observedYieldPerNode": None,
        "observedYieldPerSecond": None,
        "runtimeStatus": "NOT_MEASURED",
        "scoreBasis": YIELD_SCORE_BASIS,
        "scoreBreakdown": score_breakdown,
        "yieldModelStatus": "STATIC_NORMALIZED_PROFILE",
        "yieldModelCaveats": [
            "RUNTIME_BLUEPRINT_BUFF_GENE_MISSION_AND_SERVER_HOOKS_NOT_APPLIED",
            "STANDARD_BASE_MELEE_DAMAGE_PROFILE",
        ],
    }
