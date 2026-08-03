"""Independent, black-box verification for lazy ARK harvest rankings.

This module intentionally does not import the production harvest ranking or
evaluation modules.  Its formula, eligibility gates, component lookup, and
Top-N collapse are maintained separately so a shared implementation defect
cannot make the verifier pass itself.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Callable


VERIFICATION_SCHEMA = "blueprint-to-code.harvest-independent-verification/v2"
FORMULA_VERSION = "independent-harvest-estimated-yield-per-node/v1-native-static-profile"
SCORE_BASIS = "ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE"
NORMALIZED_HARVEST_AMOUNT_SCALE = 2.0
UNCLAMPED_FINAL_HIT_HEALTH_MULTIPLIER = 3.5
USAGE_SCOPE = "TAMED_RIDDEN"
V2_METRIC = "staticCompleteNodeTargetYield"
V2_SCORE_BASIS = "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE"
V2_UNIT = "target_resource_units/node"
V2_METRIC_CONTRACTS: dict[str, dict[str, object]] = {
    "staticCompleteNodeTargetYield": {
        "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
        "unit": "target_resource_units/node",
        "runtime": False,
    },
    "staticYieldPerAttackCycleSecond": {
        "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_ATTACK_CYCLE_SECOND",
        "unit": "target_resource_units/attack_cycle_second",
        "runtime": False,
    },
    "observedYieldPerNode": {
        "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
        "unit": "target_resource_units/node",
        "runtime": True,
    },
    "observedYieldPerSecond": {
        "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND",
        "unit": "target_resource_units/second",
        "runtime": True,
    },
}
V2_METRICS = tuple(V2_METRIC_CONTRACTS)
_MINIMUM_CONFIRMED_TRIALS = 3
POLICY_CONFIRMED = "confirmed"
POLICY_INCLUDE_CONDITIONAL = "includeConditional"
VARIANT_CANONICAL = "CANONICAL_VARIANT"
VARIANT_ALL = "ALL_VARIANTS"
VARIANT_BEST_DISCOVERED_EXPLORATORY = (
    "BEST_DISCOVERED_VARIANT_EXPLORATORY"
)
AVAILABILITY_GLOBAL_TRANSFER_ALLOWED = "GLOBAL_TRANSFER_ALLOWED"

_VARIANT_BASE = "BASE"
_VARIANT_MAP = "MAP_VARIANT"
_VARIANT_MISSION = "MISSION"
_VARIANT_BOSS = "BOSS"
_VARIANT_EVENT = "EVENT"
_VARIANT_TEST = "TEST"
_VARIANT_UNKNOWN = "UNKNOWN_VARIANT"

ReferenceQuery = Callable[[str, str, int, dict[str, Any]], dict[str, Any]]
ReferenceSpecialtiesQuery = Callable[
    [str, int, int, dict[str, Any]], dict[str, Any]
]
RuntimeObservationKey = tuple[str, str, str, str, int]


def _identity(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if "'" in text:
        text = text.split("'", 1)[1].rsplit("'", 1)[0]
    text = text.strip().strip("\"'")
    if ":" in text:
        text = text.split(":", 1)[0]
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.strip().strip("\"'")


def _package_path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if "'" in text:
        text = text.split("'", 1)[1].rsplit("'", 1)[0]
    text = text.strip().strip("\"'")
    if ":" in text:
        text = text.split(":", 1)[0]
    slash = text.rfind("/")
    dot = text.find(".", slash + 1)
    if dot >= 0:
        text = text[:dot]
    return text.rstrip("/")


def _damage_chain(damage_type: str, parents: dict[str, str]) -> list[str]:
    normalized_parents = {
        _identity(child): _identity(parent)
        for child, parent in parents.items()
        if _identity(child)
    }
    result: list[str] = []
    current = _identity(damage_type)
    seen: set[str] = set()
    while current and current not in seen:
        result.append(current)
        seen.add(current)
        current = normalized_parents.get(current, "")
    return result


def _nearest(overrides: object, chain: list[str], fallback: Any) -> tuple[Any, str | None]:
    normalized = {
        _identity(key): value
        for key, value in (overrides.items() if isinstance(overrides, dict) else [])
        if _identity(key)
    }
    for candidate in chain:
        key = _identity(candidate)
        if key in normalized:
            return normalized[key], key
    return fallback, None


def _stable_row_identity(row: dict[str, Any]) -> tuple[str, str, int, str]:
    """Independently reproduce the public tie policy for equal yields."""

    attack_index = row.get("attackIndex")
    return (
        str(row.get("creature") or "").casefold(),
        str(row.get("creatureObjectPath") or ""),
        int(attack_index)
        if isinstance(attack_index, int) and not isinstance(attack_index, bool)
        else 0,
        str(row.get("attackName") or "").casefold(),
    )


def _resource_override_map(rows: object) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        source = _identity(row.get("sourceDamageType"))
        resource = _identity(row.get("resource"))
        replacement = _identity(row.get("replacementDamageType"))
        if source and resource and replacement:
            result[(source, resource)] = replacement
    return result


def _scope_attack(attack: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if attack.get("skipTamed") is True:
        return None, "ATTACK_SKIPPED_WHEN_TAMED"
    if attack.get("onlyOnWildDinos") is True:
        return None, "ATTACK_ONLY_ON_WILD_DINOS"
    if attack.get("preventWithRider") is True:
        return None, "ATTACK_PREVENTED_WITH_RIDER"
    prepared = dict(attack)
    conditional_reasons: list[str] = []
    if attack.get("useBlueprintCanRiderAttack") is True:
        conditional_reasons.append("BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED")
    if attack.get("useBlueprintAdjustOutputDamage") is True:
        conditional_reasons.append("BLUEPRINT_ADJUST_OUTPUT_DAMAGE_NOT_RECOVERED")
    prepared["baseAttackInterval"] = attack.get("attackInterval")
    rider_interval = attack.get("riderAttackInterval")
    if isinstance(rider_interval, (int, float)) and float(rider_interval) > 0:
        prepared["attackInterval"] = float(rider_interval)
        prepared["attackIntervalSource"] = "RIDER_ATTACK_INTERVAL"
    else:
        prepared["attackIntervalSource"] = "GENERAL_ATTACK_INTERVAL"
    prepared["usageEligibilityStatus"] = (
        "CONDITIONAL" if conditional_reasons else "ELIGIBLE_BY_EXPLICIT_FLAGS"
    )
    prepared["usageConditionReasonCodes"] = conditional_reasons
    prepared["usageEstimateBasis"] = (
        "STATIC_ATTACK_FACTS_WITH_BLUEPRINT_RUNTIME_RESULT_NOT_RECOVERED"
        if conditional_reasons
        else "STATIC_ATTACK_FACTS"
    )
    return prepared, None


def _disposition(
    status: str,
    reason: str,
    estimated_yield: float | None = None,
    **facts: Any,
) -> dict[str, Any]:
    return {
        "rankingStatus": status,
        "reasonCode": reason,
        "estimatedYieldPerNode": estimated_yield,
        # Transitional compatibility alias.  The verifier never ranks by this
        # name and separately rejects a reference alias that disagrees with the
        # new complete-node metric.
        "engineComparisonIndex": estimated_yield,
        "scoreBasis": SCORE_BASIS,
        **facts,
    }


def _simulate_complete_node_grants(
    *,
    base_damage: float,
    damage_multiplier: float,
    harvest_quantity_multiplier: float,
    max_harvest_health: float,
    harvest_health_give_resource_interval: float,
    clamp_resource_harvest_damage: bool,
) -> tuple[int, int]:
    """Independently reproduce the recovered native finite-node grant loop."""

    values = (
        base_damage,
        damage_multiplier,
        harvest_quantity_multiplier,
        max_harvest_health,
        harvest_health_give_resource_interval,
    )
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in values):
        raise ValueError("Complete-node grant inputs must be finite numbers.")
    if base_damage <= 0 or damage_multiplier <= 0:
        raise ValueError("Complete-node yield requires positive harvest damage.")
    if harvest_quantity_multiplier < 0:
        raise ValueError("HarvestQuantityMultiplier cannot be negative.")
    if max_harvest_health <= 0 or harvest_health_give_resource_interval <= 0:
        raise ValueError("Complete-node yield requires positive node health and interval.")

    remaining_health = float(max_harvest_health)
    damage_per_hit = float(base_damage) * float(damage_multiplier)
    threshold = (
        float(harvest_health_give_resource_interval)
        / NORMALIZED_HARVEST_AMOUNT_SCALE
    )
    damage_accumulator = 0.0
    grant_calls = 0
    hit_count = 0
    max_iterations = max(1, int(math.ceil(remaining_health / damage_per_hit)) + 2)
    while remaining_health > 1e-9:
        hit_count += 1
        if hit_count > max_iterations:
            raise ValueError("Complete-node hit simulation did not converge.")
        final_hit_cap = (
            remaining_health
            if clamp_resource_harvest_damage
            else UNCLAMPED_FINAL_HIT_HEALTH_MULTIPLIER * remaining_health
        )
        credited_health_loss = min(damage_per_hit, final_hit_cap)
        damage_accumulator += credited_health_loss
        raw_grant_units = math.floor(damage_accumulator / threshold + 1e-9)
        calls_this_hit = math.trunc(
            float(harvest_quantity_multiplier) * raw_grant_units
        )
        if calls_this_hit > 0:
            grant_calls += calls_this_hit
            # Native path clears the accumulator after a successful grant,
            # including any remainder below the threshold.
            damage_accumulator = 0.0
        remaining_health = max(0.0, remaining_health - credited_health_loss)
    return grant_calls, hit_count


def _independent_evaluate(
    attack: dict[str, Any],
    component: dict[str, Any],
    *,
    resource: str,
    resource_entry_index: int | None,
    damage_type_parents: dict[str, str],
    damage_type_gaps: dict[str, list[str]],
    resource_overrides: dict[tuple[str, str], str],
) -> dict[str, Any]:
    source_damage_type = _identity(attack.get("damageType"))
    target_resource = _identity(resource)
    base_damage = attack.get("baseDamage")
    attack_interval = attack.get("attackInterval")
    attack_gaps = [
        str(gap)
        for gap in attack.get("gaps") or []
        if str(gap) != "AttackInterval"
    ]
    if (
        attack_gaps
        or not source_damage_type
        or not isinstance(base_damage, (int, float))
    ):
        return _disposition("UNRANKED", "REQUIRED_ATTACK_FACT_NOT_RECOVERED")
    if float(base_damage) <= 0:
        return _disposition("INCOMPATIBLE", "NON_POSITIVE_HARVEST_DAMAGE")
    if attack.get("useBlueprintAdjustOutputDamage") is True:
        return _disposition("UNRANKED", "BLUEPRINT_OUTPUT_DAMAGE_NOT_RECOVERED")

    component_ranking_gaps = component.get("rankingGaps")
    if not isinstance(component_ranking_gaps, list):
        component_ranking_gaps = [
            str(gap)
            for gap in component.get("gaps") or []
            if str(gap).startswith("HARVEST_")
        ]
    if any(
        gap
        in {
            "HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED",
            "HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED",
        }
        for gap in component_ranking_gaps
    ):
        return _disposition("UNRANKED", "REQUIRED_COMPONENT_FACT_NOT_RECOVERED")

    effective_damage_type = resource_overrides.get(
        (source_damage_type, target_resource), source_damage_type
    )
    chain = _damage_chain(effective_damage_type, damage_type_parents)
    normalized_gaps = {
        _identity(key): list(values)
        for key, values in damage_type_gaps.items()
    }
    if any(
        normalized_gaps.get(candidate)
        for candidate in {source_damage_type, effective_damage_type}
    ):
        return _disposition("UNRANKED", "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED")

    resource_entries = component.get("resourceEntries")
    if not isinstance(resource_entries, list):
        return _disposition("UNRANKED", "REQUIRED_COMPONENT_FACT_NOT_RECOVERED")
    indexed = any(
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
            and _identity(entry.get("resource")) == target_resource
            and (
                resource_entry_index is None
                or not indexed
                or entry.get("entryIndex") == resource_entry_index
            )
        ),
        None,
    )
    if not isinstance(target_entry, dict):
        has_unknown_resource = any(
            isinstance(entry, dict)
            and "RESOURCE_ITEM_NOT_RECOVERED"
            in (entry.get("rankingGaps") or entry.get("gaps") or [])
            for entry in resource_entries
        )
        return _disposition(
            "UNRANKED" if has_unknown_resource else "INCOMPATIBLE",
            "TARGET_RESOURCE_FACT_NOT_RECOVERED"
            if has_unknown_resource
            else "RESOURCE_NOT_IN_COMPONENT",
        )

    damage_entries = component.get("damageEntries")
    if not isinstance(damage_entries, list):
        return _disposition("UNRANKED", "REQUIRED_COMPONENT_FACT_NOT_RECOVERED")
    damage_entry: dict[str, Any] | None = None
    unresolved_chain_gaps: list[str] = []
    for candidate in chain:
        matched = next(
            (
                entry
                for entry in damage_entries
                if isinstance(entry, dict)
                and _identity(entry.get("damageTypeParent")) == candidate
            ),
            None,
        )
        candidate_gaps = normalized_gaps.get(candidate, [])
        if matched is not None:
            if unresolved_chain_gaps or candidate_gaps:
                return _disposition(
                    "UNRANKED", "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED"
                )
            damage_entry = matched
            break
        unresolved_chain_gaps.extend(candidate_gaps)
    if damage_entry is None:
        if unresolved_chain_gaps:
            return _disposition("UNRANKED", "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED")
        if any(
            "DAMAGE_TYPE_PARENT_NOT_RECOVERED" in (entry.get("gaps") or [])
            for entry in damage_entries
            if isinstance(entry, dict)
        ):
            return _disposition("UNRANKED", "REQUIRED_COMPONENT_FACT_NOT_RECOVERED")
        return _disposition("INCOMPATIBLE", "DAMAGE_TYPE_NOT_ACCEPTED")

    weighted_entries: list[tuple[dict[str, Any], float | None]] = []
    for entry in resource_entries:
        if not isinstance(entry, dict):
            continue
        entry_gaps = set(entry.get("rankingGaps") or entry.get("gaps") or [])
        override_types = entry.get("damageTypeEntryValues")
        unknown_override = (
            "DAMAGE_TYPE_WEIGHT_OVERRIDE_NOT_RECOVERED" in entry_gaps
            and (
                not isinstance(override_types, list)
                or any(_identity(candidate) in chain for candidate in override_types)
            )
        )
        if unknown_override:
            weight = None
        else:
            value, _match = _nearest(
                entry.get("weightOverrides"), chain, entry.get("entryWeight")
            )
            weight = float(value) if isinstance(value, (int, float)) else None
        weighted_entries.append((entry, weight))

    target_weight_row = next(
        (row for row in weighted_entries if row[0] is target_entry), None
    )
    if target_weight_row is None or target_weight_row[1] is None:
        return _disposition("UNRANKED", "RESOURCE_WEIGHT_NOT_RECOVERED")
    resource_weight = float(target_weight_row[1])
    if resource_weight <= 0:
        return _disposition("INCOMPATIBLE", "ZERO_RESOURCE_WEIGHT")
    if any(
        entry is not target_entry and weight is None
        for entry, weight in weighted_entries
    ):
        return _disposition(
            "UNRANKED", "RESOURCE_WEIGHT_NORMALIZATION_NOT_RECOVERED"
        )
    total_positive_weight = sum(
        max(0.0, float(weight))
        for _entry, weight in weighted_entries
        if weight is not None
    )
    if total_positive_weight <= 0:
        return _disposition(
            "UNRANKED", "RESOURCE_WEIGHT_NORMALIZATION_NOT_RECOVERED"
        )

    damage_multiplier = damage_entry.get("damageMultiplier")
    quantity_multiplier = damage_entry.get("harvestQuantityMultiplier")
    max_harvest_health = component.get("maxHarvestHealth")
    give_resource_interval = component.get("harvestHealthGiveResourceInterval")
    if not all(
        isinstance(value, (int, float))
        for value in (
            damage_multiplier,
            quantity_multiplier,
            max_harvest_health,
            give_resource_interval,
        )
    ):
        return _disposition("UNRANKED", "REQUIRED_COEFFICIENT_NOT_RECOVERED")
    if component.get("isSingleUnitHarvest") is True:
        return _disposition("UNRANKED", "SINGLE_UNIT_HARVEST_MODEL_NOT_RECOVERED")

    target_entry_gaps = set(
        target_entry.get("rankingGaps") or target_entry.get("gaps") or []
    )
    target_override_types = target_entry.get("damageTypeEntryValues")

    def relevant_quantity_override_gap(gap_code: str) -> bool:
        return gap_code in target_entry_gaps and (
            not isinstance(target_override_types, list)
            or any(_identity(candidate) in chain for candidate in target_override_types)
        )

    if any(
        relevant_quantity_override_gap(gap_code)
        for gap_code in (
            "DAMAGE_TYPE_MIN_QUANTITY_OVERRIDE_NOT_RECOVERED",
            "DAMAGE_TYPE_MAX_QUANTITY_OVERRIDE_NOT_RECOVERED",
        )
    ):
        return _disposition("UNRANKED", "RESOURCE_QUANTITY_MODEL_NOT_RECOVERED")

    minimum_quantity, minimum_match = _nearest(
        target_entry.get("minQuantityOverrides"),
        chain,
        target_entry.get("overrideQuantityMin"),
    )
    maximum_quantity, maximum_match = _nearest(
        target_entry.get("maxQuantityOverrides"),
        chain,
        target_entry.get("overrideQuantityMax"),
    )
    quantity_random_power = target_entry.get("overrideQuantityRandomPower")
    random_power_source = "RECOVERED_COMPONENT_VALUE"
    if quantity_random_power is None:
        quantity_random_power = 1.0
        random_power_source = "NATIVE_LINEAR_DEFAULT_FOR_LEGACY_REPORT"
    if not all(
        isinstance(value, (int, float))
        for value in (minimum_quantity, maximum_quantity, quantity_random_power)
    ):
        return _disposition("UNRANKED", "RESOURCE_QUANTITY_MODEL_NOT_RECOVERED")
    if not math.isclose(
        float(quantity_random_power), 1.0, rel_tol=0.0, abs_tol=1e-6
    ):
        return _disposition(
            "UNRANKED", "COMPLETE_NODE_YIELD_MODEL_NOT_APPLICABLE"
        )

    additional_effectiveness = damage_entry.get(
        "damageHarvestAdditionalEffectiveness"
    )
    if additional_effectiveness is None:
        additional_effectiveness = 0.0
    if not isinstance(additional_effectiveness, (int, float)):
        return _disposition("UNRANKED", "HARVEST_EFFECTIVENESS_MODEL_NOT_RECOVERED")
    if not math.isclose(
        float(additional_effectiveness), 0.0, rel_tol=0.0, abs_tol=1e-9
    ):
        return _disposition(
            "UNRANKED", "NONZERO_HARVEST_EFFECTIVENESS_MODEL_NOT_IMPLEMENTED"
        )

    weight_share = resource_weight / total_positive_weight
    try:
        grant_calls, hit_count = _simulate_complete_node_grants(
            base_damage=float(base_damage),
            damage_multiplier=float(damage_multiplier),
            harvest_quantity_multiplier=float(quantity_multiplier),
            max_harvest_health=float(max_harvest_health),
            harvest_health_give_resource_interval=float(give_resource_interval),
            clamp_resource_harvest_damage=bool(
                component.get("clampResourceHarvestDamage")
            ),
        )
        minimum_quantity = float(minimum_quantity)
        maximum_quantity = float(maximum_quantity)
        if (
            not math.isfinite(minimum_quantity)
            or not math.isfinite(maximum_quantity)
            or minimum_quantity < 0
            or maximum_quantity < minimum_quantity
        ):
            raise ValueError("Resource quantity bounds are invalid.")
    except ValueError:
        return _disposition(
            "UNRANKED", "COMPLETE_NODE_YIELD_MODEL_NOT_APPLICABLE"
        )

    expected_quantity = (minimum_quantity + maximum_quantity) / 2.0
    estimated_yield = float(grant_calls) * weight_share * expected_quantity
    interval_for_diagnostics = (
        float(attack_interval)
        if isinstance(attack_interval, (int, float)) and float(attack_interval) > 0
        else None
    )
    pressure = (
        float(base_damage)
        / interval_for_diagnostics
        * float(damage_multiplier)
        * float(quantity_multiplier)
        if interval_for_diagnostics is not None
        else None
    )
    return _disposition(
        "RANKED",
        "COMPLETE_NODE_YIELD_ESTIMATED",
        estimated_yield,
        sourceDamageType=source_damage_type,
        effectiveDamageType=effective_damage_type,
        baseDamage=float(base_damage),
        attackInterval=interval_for_diagnostics,
        damageMultiplier=float(damage_multiplier),
        harvestQuantityMultiplier=float(quantity_multiplier),
        damageHarvestAdditionalEffectiveness=float(additional_effectiveness),
        effectivenessQuantityMultiplier=target_entry.get(
            "effectivenessQuantityMultiplier"
        ),
        resourceWeight=resource_weight,
        totalPositiveResourceWeight=total_positive_weight,
        resourceWeightShare=weight_share,
        overrideQuantityMin=minimum_quantity,
        overrideQuantityMax=maximum_quantity,
        overrideQuantityRandomPower=float(quantity_random_power),
        quantityRandomPowerSource=random_power_source,
        quantityOverrideMatch=minimum_match or maximum_match,
        maxHarvestHealth=float(max_harvest_health),
        harvestHealthGiveResourceInterval=float(give_resource_interval),
        estimatedGrantCallsPerNode=grant_calls,
        estimatedHitsToDepleteNode=hit_count,
        expectedQuantityPerSelection=expected_quantity,
        clampResourceHarvestDamage=bool(
            component.get("clampResourceHarvestDamage")
        ),
        normalizedHarvestAmountScale=NORMALIZED_HARVEST_AMOUNT_SCALE,
        yieldModelVersion=FORMULA_VERSION,
        yieldModelBasis="NATIVE_STATIC_COMPLETE_NODE_HIT_SIMULATION",
        harvestPressurePerSecond=pressure,
    )


def _find_target(
    node_catalog: dict[str, Any], node_id: str, node_resource_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    for node in node_catalog.get("nodes", []):
        if not isinstance(node, dict) or str(node.get("id") or "") != node_id:
            continue
        for resource in node.get("resources", {}).get("items", []):
            if (
                isinstance(resource, dict)
                and str(resource.get("nodeResourceId") or "") == node_resource_id
            ):
                return node, resource
        raise KeyError("NODE_RESOURCE_NOT_FOUND")
    raise KeyError("RESOURCE_NODE_NOT_FOUND")


def _independently_rank_target_v1(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    node_id: str,
    node_resource_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Recompute one node/resource ranking without production formula code."""

    node, resource = _find_target(node_catalog, node_id, node_resource_id)
    component_package = _package_path(
        node.get("harvestComponent", {}).get("packagePath")
        if isinstance(node.get("harvestComponent"), dict)
        else ""
    )
    component = next(
        (
            row
            for row in evaluation_catalog.get("components", [])
            if isinstance(row, dict)
            and _package_path(row.get("objectPath")).casefold()
            == component_package.casefold()
        ),
        None,
    )
    if not isinstance(component, dict):
        raise KeyError("HARVEST_COMPONENT_NOT_FOUND")
    if str(evaluation_catalog.get("methodology", {}).get("usageScope") or USAGE_SCOPE) != USAGE_SCOPE:
        raise ValueError("Independent verifier only supports TAMED_RIDDEN.")

    require_riding = (
        evaluation_catalog.get("methodology", {}).get("rideabilityRequirement")
        == "B_ALLOW_RIDING_TRUE"
    )
    creatures = [
        row for row in evaluation_catalog.get("creatures", []) if isinstance(row, dict)
    ]
    considered = [
        row
        for row in creatures
        if str(row.get("tameability", {}).get("status") or "UNKNOWN") != "PREVENTED"
        and (
            not require_riding
            or str(row.get("rideability", {}).get("status") or "UNKNOWN") == "ALLOWED"
        )
    ]
    variant_counts = Counter(
        str(row.get("speciesKey") or row.get("objectPath") or "").casefold()
        for row in considered
    )
    parents = evaluation_catalog.get("damageTypeParents")
    gaps = evaluation_catalog.get("damageTypeGaps")
    overrides = _resource_override_map(evaluation_catalog.get("resourceDamageOverrides"))
    excluded_attacks = Counter()
    excluded_creatures = Counter()
    attacks_excluded_by_creature_scope = 0
    dispositions = Counter()
    conditional_evaluations = Counter()
    attacks_conditionally_evaluated = 0
    conditionally_ranked_attacks = 0
    best_by_species: dict[str, dict[str, Any]] = {}

    for creature in creatures:
        attacks = [row for row in creature.get("attacks", []) if isinstance(row, dict)]
        tameability = creature.get("tameability")
        if isinstance(tameability, dict) and tameability.get("status") == "PREVENTED":
            reasons = tameability.get("reasonCodes")
            values = (
                [str(value) for value in reasons if value]
                if isinstance(reasons, list)
                else ["CREATURE_NOT_TAMEABLE"]
            )
            for reason in values or ["CREATURE_NOT_TAMEABLE"]:
                excluded_creatures[reason] += 1
            attacks_excluded_by_creature_scope += len(attacks)
            continue
        rideability = creature.get("rideability")
        rideability_status = str(
            rideability.get("status") if isinstance(rideability, dict) else "UNKNOWN"
        ) or "UNKNOWN"
        if require_riding and rideability_status != "ALLOWED":
            reason_codes = (
                [str(value) for value in rideability.get("reasonCodes", []) if value]
                if isinstance(rideability, dict)
                else ["RIDEABILITY_NOT_RECOVERED"]
            )
            fallback = (
                "RIDING_NOT_ALLOWED"
                if rideability_status == "PREVENTED"
                else "RIDEABILITY_NOT_RECOVERED"
            )
            for reason in reason_codes or [fallback]:
                excluded_creatures[reason] += 1
            attacks_excluded_by_creature_scope += len(attacks)
            continue

        species_key = str(
            creature.get("speciesKey")
            or creature.get("objectPath")
            or creature.get("name")
            or ""
        ).casefold()
        tameability_status = str(
            tameability.get("status") if isinstance(tameability, dict) else "UNKNOWN"
        ) or "UNKNOWN"
        tameability_reason_codes = (
            [str(value) for value in tameability.get("reasonCodes", []) if value]
            if isinstance(tameability, dict)
            else ["TAMEABILITY_NOT_RECOVERED"]
        )
        rideability_reason_codes = (
            [str(value) for value in rideability.get("reasonCodes", []) if value]
            if isinstance(rideability, dict)
            else ["RIDEABILITY_NOT_RECOVERED"]
        )
        for attack in attacks:
            prepared, exclusion_reason = _scope_attack(attack)
            if prepared is None:
                excluded_attacks[str(exclusion_reason or "ATTACK_SCOPE_UNKNOWN")] += 1
                continue
            condition_reasons = [
                str(value)
                for value in prepared.get("usageConditionReasonCodes", [])
                if value
            ]
            if condition_reasons:
                attacks_conditionally_evaluated += 1
                for reason in condition_reasons:
                    conditional_evaluations[reason] += 1
            disposition = _independent_evaluate(
                prepared,
                component,
                resource=str(resource.get("resource") or ""),
                resource_entry_index=(
                    int(resource["entryIndex"])
                    if isinstance(resource.get("entryIndex"), int)
                    and not isinstance(resource.get("entryIndex"), bool)
                    else None
                ),
                damage_type_parents=dict(parents) if isinstance(parents, dict) else {},
                damage_type_gaps=dict(gaps) if isinstance(gaps, dict) else {},
                resource_overrides=overrides,
            )
            status = str(disposition.get("rankingStatus") or "UNRANKED")
            dispositions[status] += 1
            score = disposition.get("estimatedYieldPerNode")
            if status != "RANKED" or not isinstance(score, (int, float)):
                continue
            if condition_reasons:
                conditionally_ranked_attacks += 1
            creature_evidence_confirmed = tameability_status == "ALLOWED" and (
                not require_riding or rideability_status == "ALLOWED"
            )
            evidence_confirmed = (
                creature_evidence_confirmed and not condition_reasons
            )
            evidence_gaps = sorted(
                set(
                    condition_reasons
                    + (
                        []
                        if tameability_status == "ALLOWED"
                        else tameability_reason_codes
                        or ["TAMEABILITY_NOT_RECOVERED"]
                    )
                    + (
                        rideability_reason_codes
                        if require_riding and rideability_status != "ALLOWED"
                        else []
                    )
                )
            )
            row = {
                **disposition,
                "creature": str(creature.get("name") or "Unknown creature"),
                "creatureObjectPath": str(creature.get("objectPath") or ""),
                "speciesKey": species_key,
                "attackIndex": prepared.get("attackIndex"),
                "attackName": prepared.get("attackName"),
                "variantCount": variant_counts[species_key],
                "usageEligibilityStatus": prepared.get("usageEligibilityStatus"),
                "usageConditionReasonCodes": condition_reasons,
                "usageEstimateBasis": prepared.get("usageEstimateBasis"),
                "tameabilityStatus": tameability_status,
                "tameabilityReasonCodes": tameability_reason_codes,
                "rideabilityStatus": rideability_status,
                "rideabilityReasonCodes": rideability_reason_codes,
                "evidence": {
                    "status": "CONFIRMED" if evidence_confirmed else "PARTIAL",
                    "gaps": []
                    if evidence_confirmed
                    else evidence_gaps or ["TAMEABILITY_NOT_RECOVERED"],
                },
                "rankingTier": "CONFIRMED" if evidence_confirmed else "CONDITIONAL",
            }
            current = best_by_species.get(species_key)
            current_score = (
                float(current["estimatedYieldPerNode"])
                if current is not None
                else None
            )
            if (
                current is None
                or current_score is None
                or float(score) > current_score
                or (
                    float(score) == current_score
                    and _stable_row_identity(row) < _stable_row_identity(current)
                )
            ):
                best_by_species[species_key] = row

    ranked = sorted(
        best_by_species.values(),
        key=lambda row: (
            -float(row.get("estimatedYieldPerNode") or 0.0),
            *_stable_row_identity(row),
        ),
    )
    bounded_limit = max(1, min(int(limit), 10))
    selected = [dict(row) for row in ranked[:bounded_limit]]
    top_score = (
        float(ranked[0].get("estimatedYieldPerNode") or 0.0) if ranked else 0.0
    )
    previous_score: float | None = None
    competition_rank = 0
    for ordinal, row in enumerate(selected, start=1):
        score = float(row.get("estimatedYieldPerNode") or 0.0)
        if previous_score is None or score != previous_score:
            competition_rank = ordinal
            previous_score = score
        row["rank"] = competition_rank
        row["relativeToNodeTopPercent"] = (
            round(min(100.0, max(0.0, score / top_score * 100.0)), 3)
            if top_score > 0
            else 0.0
        )
    return {
        "node": {"id": node_id},
        "resource": {"nodeResourceId": node_resource_id},
        "coverage": {
            "attacksEvaluated": sum(dispositions.values()),
            "attacksRanked": dispositions["RANKED"],
            "attacksUnranked": dispositions["UNRANKED"],
            "attacksIncompatible": dispositions["INCOMPATIBLE"],
            "attacksExcludedByScope": sum(excluded_attacks.values()),
            "excludedByReason": dict(sorted(excluded_attacks.items())),
            "attacksConditionallyEvaluated": attacks_conditionally_evaluated,
            "conditionallyRankedAttacks": conditionally_ranked_attacks,
            "conditionalEvaluationByReason": dict(
                sorted(conditional_evaluations.items())
            ),
            "creatureAssetsExcludedFromScope": sum(excluded_creatures.values()),
            "attacksExcludedByCreatureScope": attacks_excluded_by_creature_scope,
            "excludedCreatureByReason": dict(sorted(excluded_creatures.items())),
            "rankedForNodeResource": len(ranked),
            "returned": len(selected),
            "omitted": max(0, len(ranked) - len(selected)),
        },
        "items": selected,
    }


def _variant_class(creature: dict[str, Any]) -> str:
    """Independently classify generic path markers without species allowlists."""

    object_path = str(creature.get("objectPath") or "").replace("\\", "/")
    normalized = object_path.casefold()
    segments = [segment for segment in normalized.split("/") if segment]
    if not segments:
        return _VARIANT_UNKNOWN

    def has_marker(*markers: str) -> bool:
        return any(
            marker in segment
            for marker in markers
            for segment in segments
        )

    if has_marker("test", "debug", "developer"):
        return _VARIANT_TEST
    if has_marker("mission"):
        return _VARIANT_MISSION
    if has_marker("boss"):
        return _VARIANT_BOSS
    if has_marker("event"):
        return _VARIANT_EVENT
    if has_marker("mapvariant", "map_variant") or "/maps/" in normalized:
        return _VARIANT_MAP
    if has_marker("variant", "special"):
        return _VARIANT_UNKNOWN
    return _VARIANT_BASE


def _normalized_variant_package(value: object) -> str:
    return _package_path(value).casefold()


def _base_variant_ancestry(
    base_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Independently derive BASE roots only from explicit parent chains."""

    package_by_identity = {
        id(creature): _normalized_variant_package(creature.get("objectPath"))
        for creature in base_candidates
    }
    roots: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    for creature in base_candidates:
        own_package = package_by_identity[id(creature)]
        parent_chain = creature.get("parentChain")
        ancestor_packages = {
            _normalized_variant_package(value)
            for value in parent_chain
            if _normalized_variant_package(value)
        } if isinstance(parent_chain, list) else set()
        ancestor_packages.discard(own_package)
        other_base_packages = {
            package
            for other_identity, package in package_by_identity.items()
            if other_identity != id(creature) and package
        }
        if ancestor_packages & other_base_packages:
            derived.append(creature)
        else:
            roots.append(creature)
    return roots, derived


def _canonical_variant_audit(
    species_key: str,
    variants: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select only a unique BASE candidate and explain every fail-closed case."""

    classified = [(creature, _variant_class(creature)) for creature in variants]
    base_candidates = [
        creature
        for creature, variant_class in classified
        if variant_class == _VARIANT_BASE
    ]
    excluded_classes = sorted(
        {
            variant_class
            for _creature, variant_class in classified
            if variant_class != _VARIANT_BASE
        }
    )
    ancestry_roots: list[dict[str, Any]] = []
    derived_base_candidates: list[dict[str, Any]] = []
    if len(base_candidates) > 1:
        ancestry_roots, derived_base_candidates = _base_variant_ancestry(
            base_candidates
        )
        if derived_base_candidates:
            excluded_classes = sorted({*excluded_classes, _VARIANT_UNKNOWN})

    if len(base_candidates) == 1:
        canonical_path: str | None = str(
            base_candidates[0].get("objectPath") or ""
        )
        selection_reasons = ["UNIQUE_BASE_VARIANT"]
        ambiguous = False
        ambiguity_reasons: list[str] = []
    elif len(ancestry_roots) == 1:
        canonical_path = str(ancestry_roots[0].get("objectPath") or "")
        selection_reasons = ["UNIQUE_ANCESTRY_ROOT_BASE_VARIANT"]
        ambiguous = False
        ambiguity_reasons = []
    elif not base_candidates:
        canonical_path = None
        selection_reasons = []
        ambiguous = True
        ambiguity_reasons = [
            "CANONICAL_VARIANT_AMBIGUOUS",
            "NO_BASE_VARIANT_CANDIDATE",
        ]
    else:
        canonical_path = None
        selection_reasons = []
        ambiguous = True
        ambiguity_reasons = [
            "CANONICAL_VARIANT_AMBIGUOUS",
            "MULTIPLE_BASE_VARIANT_CANDIDATES",
            (
                "NO_ANCESTRY_ROOT_BASE_VARIANT"
                if not ancestry_roots
                else "MULTIPLE_ANCESTRY_ROOT_BASE_VARIANTS"
            ),
        ]
    return {
        "speciesKey": species_key,
        "canonicalObjectPath": canonical_path,
        "selectionReasons": selection_reasons,
        "excludedVariantClasses": excluded_classes,
        "ambiguous": ambiguous,
        "ambiguityReasons": ambiguity_reasons,
    }


def _metric_value(row: dict[str, Any], metric: str) -> float | None:
    value = row.get(metric)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        return None
    return float(value)


def _enrich_v2_metrics(row: dict[str, Any], *, metric: str) -> None:
    """Project all static fields independently from the complete-node result."""

    static_total = row.get("estimatedYieldPerNode")
    if (
        not isinstance(static_total, (int, float))
        or isinstance(static_total, bool)
        or not math.isfinite(float(static_total))
    ):
        static_total = None
    else:
        static_total = float(static_total)
    row["staticCompleteNodeTargetYield"] = static_total
    row["estimatedYieldPerNode"] = static_total
    hit_count = row.get("estimatedHitsToDepleteNode")
    interval = row.get("attackInterval")
    cycle_seconds = (
        float(hit_count) * float(interval)
        if isinstance(hit_count, (int, float))
        and not isinstance(hit_count, bool)
        and float(hit_count) > 0
        and isinstance(interval, (int, float))
        and not isinstance(interval, bool)
        and float(interval) > 0
        else None
    )
    row["staticAttackCycleSecondsToDepleteNode"] = cycle_seconds
    row["staticYieldPerAttackCycleSecond"] = (
        static_total / cycle_seconds
        if static_total is not None and cycle_seconds is not None
        else None
    )
    row["staticFirstHitTiming"] = "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE"
    row["observedYieldPerNode"] = None
    row["observedYieldPerSecond"] = None
    row["runtimeStatus"] = "NOT_MEASURED"
    row["scoreBasis"] = V2_METRIC_CONTRACTS[metric]["scoreBasis"]
    row["scoreBreakdown"] = {"metric": metric}


def _runtime_profile_context(
    runtime_observations: dict[RuntimeObservationKey, dict[str, Any]] | None,
    *,
    requested_profile_id: str | None,
    runtime_metric: bool,
) -> tuple[str | None, dict[str, Any]]:
    """Independently enforce a single comparable runtime cohort."""

    observations = [
        row
        for row in (runtime_observations or {}).values()
        if isinstance(row, dict)
    ]
    available_profiles = sorted(
        {
            str(row.get("runtimeProfileId") or "").strip()
            for row in observations
            if row.get("synthetic") is False
            and str(row.get("runtimeProfileId") or "").strip()
        }
    )
    selected_profile = (
        str(requested_profile_id).strip()
        if requested_profile_id is not None
        else None
    )
    if runtime_metric:
        if selected_profile is not None:
            if selected_profile not in available_profiles:
                raise ValueError(
                    f"Requested runtimeProfileId {selected_profile!r} is not available."
                )
        elif len(available_profiles) > 1:
            raise ValueError(
                "Multiple runtime profiles are available; select runtimeProfileId."
            )
        elif available_profiles:
            selected_profile = available_profiles[0]

    synthetic_excluded = 0
    publishable_confirmed_rows = 0
    preliminary_rows = 0
    profile_mismatch_excluded = 0
    for observation in observations:
        if observation.get("synthetic") is not False:
            if observation.get("synthetic") is True:
                synthetic_excluded += 1
            continue
        observation_profile = str(
            observation.get("runtimeProfileId") or ""
        ).strip()
        if selected_profile is None:
            continue
        if observation_profile != selected_profile:
            profile_mismatch_excluded += 1
            continue
        runtime_status = str(observation.get("runtimeStatus") or "")
        trial_count = observation.get("trialCount")
        if (
            runtime_status == "OBSERVED_CONFIRMED"
            and isinstance(trial_count, int)
            and not isinstance(trial_count, bool)
            and trial_count >= _MINIMUM_CONFIRMED_TRIALS
        ):
            publishable_confirmed_rows += 1
        elif (
            runtime_status == "OBSERVED_PRELIMINARY"
            and isinstance(trial_count, int)
            and not isinstance(trial_count, bool)
            and 0 < trial_count < _MINIMUM_CONFIRMED_TRIALS
        ):
            preliminary_rows += 1

    return selected_profile, {
        "runtimeProfilesAvailable": available_profiles,
        "runtimeProfileSelected": selected_profile,
        "publishableConfirmedRows": publishable_confirmed_rows,
        "preliminaryRows": preliminary_rows,
        "syntheticExcluded": synthetic_excluded,
        "profileMismatchExcluded": profile_mismatch_excluded,
    }


def _eligible_runtime_observation(
    observation: object,
    *,
    runtime_profile_id: str | None,
    include_preliminary: bool,
) -> dict[str, Any] | None:
    """Accept only explicit, same-profile, non-synthetic controlled evidence."""

    if (
        not isinstance(observation, dict)
        or observation.get("synthetic") is not False
        or runtime_profile_id is None
    ):
        return None
    observation_profile = str(observation.get("runtimeProfileId") or "")
    if observation_profile != runtime_profile_id:
        return None
    runtime_status = str(observation.get("runtimeStatus") or "")
    trial_count = observation.get("trialCount")
    if (
        not isinstance(trial_count, int)
        or isinstance(trial_count, bool)
        or trial_count <= 0
    ):
        return None
    if runtime_status == "OBSERVED_CONFIRMED":
        if trial_count < _MINIMUM_CONFIRMED_TRIALS:
            return None
    elif runtime_status == "OBSERVED_PRELIMINARY":
        if trial_count >= _MINIMUM_CONFIRMED_TRIALS or not include_preliminary:
            return None
    else:
        return None
    observed_per_node = observation.get("observedYieldPerNode")
    observed_per_second = observation.get("observedYieldPerSecond")
    for value in (observed_per_node, observed_per_second):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            return None
    return dict(observation)


def _independently_rank_target_v2(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    node_id: str,
    node_resource_id: str,
    limit: int = 10,
    evidence_policy: str = POLICY_CONFIRMED,
    variant_policy: str = VARIANT_CANONICAL,
    metric: str = V2_METRIC,
    availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    runtime_observations: dict[RuntimeObservationKey, dict[str, Any]] | None = None,
    runtime_profile_id: str | None = None,
    include_preliminary: bool = False,
) -> dict[str, Any]:
    """Independently verify one v2 forward metric and policy context."""

    if evidence_policy not in {POLICY_CONFIRMED, POLICY_INCLUDE_CONDITIONAL}:
        raise ValueError("Unsupported harvest evidence policy.")
    if variant_policy not in {
        VARIANT_CANONICAL,
        VARIANT_ALL,
        VARIANT_BEST_DISCOVERED_EXPLORATORY,
    }:
        raise ValueError("Unsupported harvest variant policy.")
    if metric not in V2_METRIC_CONTRACTS:
        raise ValueError("Unsupported harvest ranking metric.")
    if availability_policy != AVAILABILITY_GLOBAL_TRANSFER_ALLOWED:
        raise ValueError("Unsupported harvest availability policy.")
    runtime_profile_id, runtime_coverage = _runtime_profile_context(
        runtime_observations,
        requested_profile_id=runtime_profile_id,
        runtime_metric=V2_METRIC_CONTRACTS[metric]["runtime"] is True,
    )

    node, resource = _find_target(node_catalog, node_id, node_resource_id)
    component_package = _package_path(
        node.get("harvestComponent", {}).get("packagePath")
        if isinstance(node.get("harvestComponent"), dict)
        else ""
    )
    component = next(
        (
            row
            for row in evaluation_catalog.get("components", [])
            if isinstance(row, dict)
            and _package_path(row.get("objectPath")).casefold()
            == component_package.casefold()
        ),
        None,
    )
    if not isinstance(component, dict):
        raise KeyError("HARVEST_COMPONENT_NOT_FOUND")
    creatures = [
        row for row in evaluation_catalog.get("creatures", []) if isinstance(row, dict)
    ]
    parents = evaluation_catalog.get("damageTypeParents")
    damage_gaps = evaluation_catalog.get("damageTypeGaps")
    overrides = _resource_override_map(
        evaluation_catalog.get("resourceDamageOverrides")
    )
    all_grouped: dict[str, list[dict[str, Any]]] = {}
    for creature in creatures:
        species_key = str(
            creature.get("speciesKey")
            or creature.get("objectPath")
            or creature.get("name")
            or ""
        ).casefold()
        if species_key:
            all_grouped.setdefault(species_key, []).append(creature)
    grouped: dict[str, list[dict[str, Any]]] = {}
    excluded_attacks = Counter()
    excluded_creatures = Counter()
    attacks_excluded_by_creature_scope = 0
    dispositions = Counter()
    conditional_evaluations = Counter()
    attacks_conditionally_evaluated = 0
    conditionally_ranked_attacks = 0
    rows_with_effectiveness_field = 0
    rows_with_non_neutral_effectiveness = 0
    rows_conditional_because_effectiveness = 0
    for creature in creatures:
        attacks = [row for row in creature.get("attacks", []) if isinstance(row, dict)]
        tameability = creature.get("tameability")
        rideability = creature.get("rideability")
        tameability_status = str(
            tameability.get("status") if isinstance(tameability, dict) else "UNKNOWN"
        ) or "UNKNOWN"
        rideability_status = str(
            rideability.get("status") if isinstance(rideability, dict) else "UNKNOWN"
        ) or "UNKNOWN"
        if tameability_status == "PREVENTED":
            excluded_creatures["CREATURE_NOT_TAMEABLE"] += 1
            attacks_excluded_by_creature_scope += len(attacks)
            continue
        if rideability_status == "PREVENTED":
            excluded_creatures["RIDING_NOT_ALLOWED"] += 1
            attacks_excluded_by_creature_scope += len(attacks)
            continue
        species_key = str(
            creature.get("speciesKey")
            or creature.get("objectPath")
            or creature.get("name")
            or ""
        ).casefold()
        if species_key:
            grouped.setdefault(species_key, []).append(creature)

    variant_audit_by_species = {
        species_key: _canonical_variant_audit(species_key, variants)
        for species_key, variants in sorted(all_grouped.items())
    }
    all_variant_selection_audits = [
        dict(variant_audit_by_species[species_key])
        for species_key in sorted(variant_audit_by_species)
    ]
    ambiguous_variant_audits = [
        audit
        for audit in all_variant_selection_audits
        if audit["ambiguous"] is True
    ]
    variant_selection_audits = [
        dict(audit) for audit in all_variant_selection_audits[:10]
    ]

    selected_species_rows: list[dict[str, Any]] = []
    for species_key, variants in sorted(grouped.items()):
        variant_audit = variant_audit_by_species[species_key]
        canonical_path = variant_audit["canonicalObjectPath"]
        best_by_variant_and_tier: dict[str, list[dict[str, Any]]] = {
            "CONFIRMED": [],
            "CONDITIONAL": [],
        }
        for creature in variants:
            tameability = creature.get("tameability")
            rideability = creature.get("rideability")
            tameability_status = str(
                tameability.get("status")
                if isinstance(tameability, dict)
                else "UNKNOWN"
            ) or "UNKNOWN"
            rideability_status = str(
                rideability.get("status")
                if isinstance(rideability, dict)
                else "UNKNOWN"
            ) or "UNKNOWN"
            tameability_reasons = (
                [str(value) for value in tameability.get("reasonCodes", []) if value]
                if isinstance(tameability, dict)
                else ["TAMEABILITY_NOT_RECOVERED"]
            )
            rideability_reasons = (
                [str(value) for value in rideability.get("reasonCodes", []) if value]
                if isinstance(rideability, dict)
                else ["RIDEABILITY_NOT_RECOVERED"]
            )
            attack_rows: list[dict[str, Any]] = []
            for attack in creature.get("attacks", []):
                if not isinstance(attack, dict):
                    continue
                prepared, exclusion_reason = _scope_attack(attack)
                if prepared is None:
                    excluded_attacks[
                        str(exclusion_reason or "ATTACK_SCOPE_UNKNOWN")
                    ] += 1
                    continue
                condition_reasons = [
                    str(value)
                    for value in prepared.get("usageConditionReasonCodes", [])
                    if value
                ]
                disposition = _independent_evaluate(
                    prepared,
                    component,
                    resource=str(resource.get("resource") or ""),
                    resource_entry_index=(
                        int(resource["entryIndex"])
                        if isinstance(resource.get("entryIndex"), int)
                        and not isinstance(resource.get("entryIndex"), bool)
                        else None
                    ),
                    damage_type_parents=(
                        dict(parents) if isinstance(parents, dict) else {}
                    ),
                    damage_type_gaps=(
                        dict(damage_gaps) if isinstance(damage_gaps, dict) else {}
                    ),
                    resource_overrides=overrides,
                )
                status = str(disposition.get("rankingStatus") or "UNRANKED")
                dispositions[status] += 1
                if status != "RANKED":
                    continue
                _enrich_v2_metrics(disposition, metric=metric)
                runtime_key = (
                    str(node_id),
                    str(node_resource_id),
                    species_key,
                    str(creature.get("objectPath") or "").casefold(),
                    int(disposition.get("attackIndex") or prepared.get("attackIndex") or 0),
                )
                observation = _eligible_runtime_observation(
                    runtime_observations.get(runtime_key)
                    if runtime_observations is not None
                    else None,
                    runtime_profile_id=runtime_profile_id,
                    include_preliminary=include_preliminary,
                )
                if observation is not None:
                    disposition.update(
                        {
                            "observedYieldPerNode": float(
                                observation["observedYieldPerNode"]
                            ),
                            "observedYieldPerSecond": float(
                                observation["observedYieldPerSecond"]
                            ),
                            "runtimeStatus": observation.get("runtimeStatus"),
                            "runtimeObservation": {
                                "observationSetId": observation.get(
                                    "observationSetId"
                                ),
                                "runtimeProfileId": observation.get(
                                    "runtimeProfileId"
                                )
                                or runtime_profile_id,
                                "environmentFingerprint": observation.get(
                                    "environmentFingerprint"
                                ),
                                "trialCount": observation.get("trialCount"),
                                "synthetic": False,
                            },
                        }
                    )
                if "effectivenessQuantityMultiplier" in disposition:
                    rows_with_effectiveness_field += 1
                effectiveness = disposition.get("effectivenessQuantityMultiplier")
                effectiveness_is_non_neutral = (
                    isinstance(effectiveness, (int, float))
                    and not isinstance(effectiveness, bool)
                    and not math.isclose(
                        float(effectiveness), 1.0, rel_tol=0.0, abs_tol=1e-9
                    )
                )
                if effectiveness_is_non_neutral:
                    rows_with_non_neutral_effectiveness += 1
                    rows_conditional_because_effectiveness += 1
                score = _metric_value(disposition, metric)
                if score is None:
                    continue
                if condition_reasons:
                    attacks_conditionally_evaluated += 1
                    conditionally_ranked_attacks += 1
                    for reason in condition_reasons:
                        conditional_evaluations[reason] += 1
                evidence_gaps = list(condition_reasons)
                if tameability_status != "ALLOWED":
                    evidence_gaps.extend(
                        tameability_reasons or ["TAMEABILITY_NOT_RECOVERED"]
                    )
                if rideability_status != "ALLOWED":
                    evidence_gaps.extend(
                        rideability_reasons or ["RIDEABILITY_NOT_RECOVERED"]
                    )
                if effectiveness_is_non_neutral:
                    evidence_gaps.append(
                        "EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED"
                    )
                if (
                    V2_METRIC_CONTRACTS[metric]["runtime"] is True
                    and observation is not None
                    and observation.get("runtimeStatus")
                    == "OBSERVED_PRELIMINARY"
                ):
                    evidence_gaps.append(
                        "OBSERVED_PRELIMINARY_MINIMUM_TRIALS_NOT_MET"
                    )
                evidence_gaps = sorted(set(evidence_gaps))
                confirmed = not evidence_gaps
                attack_rows.append(
                    {
                        **disposition,
                        "creature": str(creature.get("name") or "Unknown creature"),
                        "creatureObjectPath": str(creature.get("objectPath") or ""),
                        "speciesKey": species_key,
                        "attackIndex": prepared.get("attackIndex"),
                        "attackName": prepared.get("attackName"),
                        "variantCount": len(variants),
                        "usageEligibilityStatus": prepared.get(
                            "usageEligibilityStatus"
                        ),
                        "usageConditionReasonCodes": condition_reasons,
                        "usageEstimateBasis": prepared.get("usageEstimateBasis"),
                        "tameabilityStatus": tameability_status,
                        "tameabilityReasonCodes": tameability_reasons,
                        "rideabilityStatus": rideability_status,
                        "rideabilityReasonCodes": rideability_reasons,
                        "evidence": {
                            "status": "CONFIRMED" if confirmed else "PARTIAL",
                            "gaps": evidence_gaps,
                        },
                        "rankingTier": (
                            "CONFIRMED" if confirmed else "CONDITIONAL"
                        ),
                    }
                )
            attack_rows.sort(
                key=lambda row: (
                    -float(_metric_value(row, metric) or 0.0),
                    *_stable_row_identity(row),
                )
            )
            for tier in ("CONFIRMED", "CONDITIONAL"):
                best_for_tier = next(
                    (row for row in attack_rows if row.get("rankingTier") == tier),
                    None,
                )
                if best_for_tier is not None:
                    best_by_variant_and_tier[tier].append(best_for_tier)
        variant_paths = [str(creature.get("objectPath") or "") for creature in variants]
        for tier, best_by_variant in best_by_variant_and_tier.items():
            if not best_by_variant:
                continue
            exploratory_row = min(
                best_by_variant,
                key=lambda row: (
                    -float(_metric_value(row, metric) or 0.0),
                    *_stable_row_identity(row),
                ),
            )
            rows_by_path = {
                str(row.get("creatureObjectPath") or ""): row
                for row in best_by_variant
            }
            canonical_row = rows_by_path.get(canonical_path)
            if variant_policy == VARIANT_ALL:
                selected_rows = sorted(
                    best_by_variant,
                    key=lambda row: _stable_row_identity(row),
                )
            elif variant_policy == VARIANT_BEST_DISCOVERED_EXPLORATORY:
                selected_rows = [exploratory_row]
            else:
                selected_rows = [canonical_row] if canonical_row is not None else []
            comparison = [
                {
                    "objectPath": path,
                    "creature": (
                        rows_by_path[path].get("creature")
                        if path in rows_by_path
                        else next(
                            (
                                candidate.get("name")
                                for candidate in variants
                                if str(candidate.get("objectPath") or "") == path
                            ),
                            None,
                        )
                    ),
                    "selectedMetricValue": (
                        _metric_value(rows_by_path[path], metric)
                        if path in rows_by_path
                        else None
                    ),
                    "rankingTier": tier if path in rows_by_path else None,
                    "canonical": path == canonical_path,
                    "exploratoryBest": path
                    == str(exploratory_row.get("creatureObjectPath") or ""),
                }
                for path in variant_paths
            ]
            for source_row in selected_rows:
                row = dict(source_row)
                selected_score = _metric_value(row, metric)
                exploratory_score = _metric_value(exploratory_row, metric)
                selected_path = str(row.get("creatureObjectPath") or "")
                row["variantSelection"] = {
                    "policy": variant_policy,
                    "selectedObjectPath": selected_path,
                    "canonicalObjectPath": canonical_path,
                    "selectionReasons": list(variant_audit["selectionReasons"]),
                    "excludedVariantClasses": list(
                        variant_audit["excludedVariantClasses"]
                    ),
                    "ambiguous": variant_audit["ambiguous"],
                    "ambiguityReasons": list(variant_audit["ambiguityReasons"]),
                    "excludedObjectPaths": [
                        path for path in variant_paths if path != selected_path
                    ],
                    "comparison": comparison,
                    "higherExploratoryVariantExists": bool(
                        selected_score is not None
                        and exploratory_score is not None
                        and exploratory_score > selected_score
                    ),
                }
                selected_species_rows.append(row)

    bounded_limit = max(1, min(int(limit), 10))

    def ranked_tier(tier: str) -> list[dict[str, Any]]:
        ordered = sorted(
            [row for row in selected_species_rows if row.get("rankingTier") == tier],
            key=lambda row: (
                -float(_metric_value(row, metric) or 0.0),
                *_stable_row_identity(row),
            ),
        )
        selected = [dict(row) for row in ordered[:bounded_limit]]
        top_score = (
            float(_metric_value(ordered[0], metric) or 0.0)
            if ordered
            else 0.0
        )
        previous_score: float | None = None
        competition_rank = 0
        for ordinal, row in enumerate(selected, start=1):
            score = float(_metric_value(row, metric) or 0.0)
            if previous_score is None or score != previous_score:
                competition_rank = ordinal
                previous_score = score
            row["rank"] = competition_rank
            row["relativeToNodeTopPercent"] = (
                round(min(100.0, max(0.0, score / top_score * 100.0)), 6)
                if top_score > 0
                else 0.0
            )
            row["relativeBasisTier"] = tier
        return selected

    confirmed_items = ranked_tier("CONFIRMED")
    conditional_items = (
        ranked_tier("CONDITIONAL")
        if evidence_policy == POLICY_INCLUDE_CONDITIONAL
        else []
    )
    confirmed_all = [
        row for row in selected_species_rows if row.get("rankingTier") == "CONFIRMED"
    ]
    conditional_all = [
        row
        for row in selected_species_rows
        if row.get("rankingTier") == "CONDITIONAL"
    ]
    return {
        "node": {"id": node_id},
        "resource": {"nodeResourceId": node_resource_id},
        "queryPolicy": {
            "evidence": evidence_policy,
            "variant": variant_policy,
            "metric": metric,
            "availability": availability_policy,
            "runtimeProfileId": runtime_profile_id,
            "includePreliminary": bool(include_preliminary),
            "exploratory": variant_policy
            == VARIANT_BEST_DISCOVERED_EXPLORATORY,
        },
        "methodology": {
            "metric": metric,
            **V2_METRIC_CONTRACTS[metric],
            "firstHitTiming": "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE",
            "relativeBasis": "WITHIN_SAME_EVIDENCE_TIER_SELECTED_METRIC",
            "tiePolicy": "COMPETITION_RANK_FOR_EQUAL_SELECTED_METRIC_1_1_3",
            "variantSelection": variant_policy,
            "availabilityPolicy": availability_policy,
            "warning": (
                "实测指标仅可在所选 runtimeProfileId 环境内比较；preliminary "
                "仍为条件性结果，synthetic 永不进入可发布排行。"
                if V2_METRIC_CONTRACTS[metric]["runtime"] is True
                else (
                    "静态模型指标不是服务器环境下的实测产量或真实每秒产量；"
                    "条件性结果不会占用已确认榜名次或基线。"
                )
            ),
        },
        "coverage": {
            "speciesEvaluated": len(grouped),
            "attacksEvaluated": sum(dispositions.values()),
            "attacksRanked": dispositions["RANKED"],
            "attacksUnranked": dispositions["UNRANKED"],
            "attacksIncompatible": dispositions["INCOMPATIBLE"],
            "attacksExcludedByScope": sum(excluded_attacks.values()),
            "excludedByReason": dict(sorted(excluded_attacks.items())),
            "attacksConditionallyEvaluated": attacks_conditionally_evaluated,
            "conditionallyRankedAttacks": conditionally_ranked_attacks,
            "conditionalEvaluationByReason": dict(
                sorted(conditional_evaluations.items())
            ),
            "rowsWithEffectivenessField": rows_with_effectiveness_field,
            "rowsWithNonNeutralEffectiveness": rows_with_non_neutral_effectiveness,
            "rowsConditionalBecauseEffectiveness": (
                rows_conditional_because_effectiveness
            ),
            "canonicalVariantAmbiguousSpecies": len(ambiguous_variant_audits),
            "canonicalCreatureAssetsAudited": len(creatures),
            "canonicalVariantsAudited": len(all_variant_selection_audits),
            "variantSelectionAuditsReturned": len(variant_selection_audits),
            "variantSelectionAuditsOmitted": max(
                0,
                len(all_variant_selection_audits) - len(variant_selection_audits),
            ),
            "canonicalVariantAmbiguityExamples": [
                dict(audit) for audit in ambiguous_variant_audits[:10]
            ],
            "creatureAssetsExcludedFromScope": sum(excluded_creatures.values()),
            "attacksExcludedByCreatureScope": attacks_excluded_by_creature_scope,
            "excludedCreatureByReason": dict(sorted(excluded_creatures.items())),
            "rankedForNodeResource": len(selected_species_rows),
            "rankedSpeciesConfirmed": len(confirmed_all),
            "rankedSpeciesConditional": len(conditional_all),
            "returnedConfirmed": len(confirmed_items),
            "returnedConditional": len(conditional_items),
            "returned": len(confirmed_items),
            "omitted": max(0, len(confirmed_all) - len(confirmed_items)),
        },
        "variantSelectionAudits": variant_selection_audits,
        "runtimeCoverage": runtime_coverage,
        "confirmedItems": confirmed_items,
        "conditionalItems": conditional_items,
        "items": list(confirmed_items),
    }


def independently_rank_target(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    node_id: str,
    node_resource_id: str,
    limit: int = 10,
    evidence_policy: str = POLICY_CONFIRMED,
    variant_policy: str = VARIANT_CANONICAL,
    metric: str = V2_METRIC,
    availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    runtime_observations: dict[RuntimeObservationKey, dict[str, Any]] | None = None,
    runtime_profile_id: str | None = None,
    include_preliminary: bool = False,
) -> dict[str, Any]:
    methodology = evaluation_catalog.get("methodology")
    if (
        isinstance(methodology, dict)
        and methodology.get("contractVersion") == "harvest-ranking-contract/v2"
    ):
        return _independently_rank_target_v2(
            node_catalog,
            evaluation_catalog,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=limit,
            evidence_policy=evidence_policy,
            variant_policy=variant_policy,
            metric=metric,
            availability_policy=availability_policy,
            runtime_observations=runtime_observations,
            runtime_profile_id=runtime_profile_id,
            include_preliminary=include_preliminary,
        )
    return _independently_rank_target_v1(
        node_catalog,
        evaluation_catalog,
        node_id=node_id,
        node_resource_id=node_resource_id,
        limit=limit,
    )


def independently_rank_specialties(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    species_key: str,
    offset: int = 0,
    limit: int = 24,
    evidence_policy: str = POLICY_CONFIRMED,
    variant_policy: str = VARIANT_CANONICAL,
    metric: str = V2_METRIC,
    availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    runtime_observations: dict[RuntimeObservationKey, dict[str, Any]] | None = None,
    runtime_profile_id: str | None = None,
    include_preliminary: bool = False,
    _forward_cache: dict[tuple[object, ...], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Independently derive reverse specialties from same-context forward rows."""

    if (
        evaluation_catalog.get("methodology", {}).get("contractVersion")
        != "harvest-ranking-contract/v2"
    ):
        raise ValueError("Reverse independent verification requires contract v2.")
    if metric not in V2_METRIC_CONTRACTS:
        raise ValueError("Unsupported harvest ranking metric.")
    if evidence_policy not in {POLICY_CONFIRMED, POLICY_INCLUDE_CONDITIONAL}:
        raise ValueError("Unsupported harvest evidence policy.")
    runtime_profile_id, runtime_coverage = _runtime_profile_context(
        runtime_observations,
        requested_profile_id=runtime_profile_id,
        runtime_metric=V2_METRIC_CONTRACTS[metric]["runtime"] is True,
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

    occurrences: list[
        tuple[tuple[object, ...], dict[str, Any], dict[str, Any]]
    ] = []
    representatives: dict[tuple[object, ...], tuple[str, str]] = {}
    runtime_metric = V2_METRIC_CONTRACTS[metric]["runtime"] is True
    for node in node_catalog.get("nodes", []):
        if not isinstance(node, dict):
            continue
        component_ref = node.get("harvestComponent")
        component_package = _package_path(
            component_ref.get("packagePath")
            if isinstance(component_ref, dict)
            else ""
        )
        resources = node.get("resources", {}).get("items", [])
        for resource in resources if isinstance(resources, list) else []:
            if not isinstance(resource, dict):
                continue
            entry_index = resource.get("entryIndex")
            entry_index = (
                int(entry_index)
                if isinstance(entry_index, int) and not isinstance(entry_index, bool)
                else None
            )
            pair = (
                str(node.get("id") or ""),
                str(resource.get("nodeResourceId") or ""),
            )
            evaluation_key: tuple[object, ...] = (
                component_package.casefold(),
                str(resource.get("resource") or "").casefold(),
                entry_index,
            )
            key = (*evaluation_key, *pair) if runtime_metric else evaluation_key
            representatives.setdefault(key, pair)
            occurrences.append((key, node, resource))

    forward_cache = _forward_cache if _forward_cache is not None else {}

    def forward(
        *,
        catalog: dict[str, Any],
        cache_scope: str,
        node_id: str,
        node_resource_id: str,
    ) -> dict[str, Any]:
        cache_key = (
            cache_scope,
            node_id,
            node_resource_id,
            metric,
            variant_policy,
            availability_policy,
            runtime_profile_id,
            bool(include_preliminary),
        )
        cached = forward_cache.get(cache_key)
        if cached is None:
            cached = independently_rank_target(
                node_catalog,
                catalog,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=10,
                evidence_policy=POLICY_INCLUDE_CONDITIONAL,
                variant_policy=variant_policy,
                metric=metric,
                availability_policy=availability_policy,
                runtime_observations=runtime_observations,
                runtime_profile_id=runtime_profile_id,
                include_preliminary=include_preliminary,
            )
            forward_cache[cache_key] = cached
        return cached

    selected_catalog = {**evaluation_catalog, "creatures": variants}
    selected_by_key: dict[tuple[object, ...], dict[str, dict[str, Any]]] = {}
    top_by_key: dict[tuple[object, ...], dict[str, dict[str, Any]]] = {}
    for key, (node_id, node_resource_id) in representatives.items():
        selected_result = forward(
            catalog=selected_catalog,
            cache_scope=f"selected:{requested_key}",
            node_id=node_id,
            node_resource_id=node_resource_id,
        )
        baseline_result = forward(
            catalog=evaluation_catalog,
            cache_scope="baseline:all",
            node_id=node_id,
            node_resource_id=node_resource_id,
        )
        for tier, field in (
            ("CONFIRMED", "confirmedItems"),
            ("CONDITIONAL", "conditionalItems"),
        ):
            selected_rows = selected_result.get(field)
            baseline_rows = baseline_result.get(field)
            selected_row = (
                next(
                    (
                        dict(row)
                        for row in selected_rows
                        if isinstance(row, dict)
                        and str(row.get("speciesKey") or "").casefold()
                        == requested_key
                    ),
                    None,
                )
                if isinstance(selected_rows, list)
                else None
            )
            top_row = (
                dict(baseline_rows[0])
                if isinstance(baseline_rows, list)
                and baseline_rows
                and isinstance(baseline_rows[0], dict)
                else None
            )
            if selected_row is not None and top_row is not None:
                selected_by_key.setdefault(key, {})[tier] = selected_row
                top_by_key.setdefault(key, {})[tier] = top_row

    ranked_rows: list[dict[str, Any]] = []
    for key, node, resource in occurrences:
        for tier in ("CONFIRMED", "CONDITIONAL"):
            selected_row = selected_by_key.get(key, {}).get(tier)
            top_row = top_by_key.get(key, {}).get(tier)
            if selected_row is None or top_row is None:
                continue
            selected_score = _metric_value(selected_row, metric)
            top_score = _metric_value(top_row, metric)
            if selected_score is None or top_score is None or top_score <= 0:
                continue
            relative = round(
                min(100.0, max(0.0, selected_score / top_score * 100.0)),
                6,
            )
            component_ref = node.get("harvestComponent")
            component_package = _package_path(
                component_ref.get("packagePath")
                if isinstance(component_ref, dict)
                else ""
            )
            ranked_rows.append(
                {
                    **selected_row,
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
                    "selectedMetricValue": selected_score,
                    "nodeTopSelectedMetricValue": top_score,
                    "nodeTopStaticCompleteNodeTargetYield": top_row.get(
                        "staticCompleteNodeTargetYield"
                    ),
                    "nodeTopEstimatedYieldPerNode": top_row.get(
                        "estimatedYieldPerNode"
                    ),
                    "relativeToNodeTopPercent": relative,
                    "relativeBasisTier": tier,
                    "nodeTop": {
                        "speciesKey": top_row.get("speciesKey"),
                        "creature": top_row.get("creature"),
                        "creatureObjectPath": top_row.get("creatureObjectPath"),
                        "attackIndex": top_row.get("attackIndex"),
                        "attackName": top_row.get("attackName"),
                        "selectedMetric": metric,
                        "selectedMetricValue": top_score,
                        "staticCompleteNodeTargetYield": top_row.get(
                            "staticCompleteNodeTargetYield"
                        ),
                        "estimatedYieldPerNode": top_row.get(
                            "estimatedYieldPerNode"
                        ),
                        "rankingTier": top_row.get("rankingTier"),
                        "evidence": dict(top_row.get("evidence") or {}),
                    },
                }
            )

    def reverse_competition_key(row: dict[str, Any]) -> tuple[float, float]:
        return (
            float(row.get("relativeToNodeTopPercent") or 0.0),
            float(row.get("selectedMetricValue") or 0.0),
        )

    def reverse_identity_key(row: dict[str, Any]) -> tuple[object, ...]:
        """Independently apply the public immutable reverse tie contract."""

        def normalized_identity(value: object) -> str:
            return str(value or "").strip().replace("\\", "/").casefold()

        resource_value = row.get("resource")
        resource = resource_value if isinstance(resource_value, dict) else {}
        node_value = row.get("node")
        node = node_value if isinstance(node_value, dict) else {}
        raw_entry_index = resource.get("entryIndex")
        if isinstance(raw_entry_index, int) and not isinstance(
            raw_entry_index, bool
        ):
            entry_identity = (0, int(raw_entry_index), "")
        else:
            entry_identity = (
                1,
                0,
                normalized_identity(raw_entry_index),
            )
        raw_attack_index = row.get("attackIndex")
        if isinstance(raw_attack_index, int) and not isinstance(
            raw_attack_index, bool
        ):
            attack_identity = (0, int(raw_attack_index), "")
        else:
            attack_identity = (
                1,
                0,
                normalized_identity(raw_attack_index),
            )
        return (
            normalized_identity(resource.get("nodeResourceId")),
            normalized_identity(node.get("id")),
            normalized_identity(resource.get("resource")),
            normalized_identity(node.get("objectPath")),
            entry_identity,
            normalized_identity(resource.get("harvestComponentPackagePath")),
            normalized_identity(row.get("creatureObjectPath")),
            attack_identity,
        )

    def reverse_page_sort_key(row: dict[str, Any]) -> tuple[object, ...]:
        score_key = reverse_competition_key(row)
        return (-score_key[0], -score_key[1], *reverse_identity_key(row))

    def rank_tier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows.sort(key=reverse_page_sort_key)
        previous_primary: tuple[float, float] | None = None
        previous_rank = 0
        for ordinal, row in enumerate(rows, start=1):
            primary = reverse_competition_key(row)
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
    bounded_offset = max(0, int(offset))
    bounded_limit = max(1, min(int(limit), 100))
    page_rows = visible_rows[bounded_offset : bounded_offset + bounded_limit]
    confirmed_page = [
        dict(row) for row in page_rows if row.get("rankingTier") == "CONFIRMED"
    ]
    conditional_page = [
        dict(row) for row in page_rows if row.get("rankingTier") != "CONFIRMED"
    ]
    total = len(visible_rows)
    metric_contract = V2_METRIC_CONTRACTS[metric]
    representative = min(
        variants,
        key=lambda creature: (
            len(str(creature.get("objectPath") or "")),
            str(creature.get("objectPath") or "").casefold(),
        ),
    )
    return {
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
            "runtimeProfileId": runtime_profile_id,
            "includePreliminary": bool(include_preliminary),
            "exploratory": variant_policy
            == VARIANT_BEST_DISCOVERED_EXPLORATORY,
        },
        "methodology": {
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
            **metric_contract,
        },
        "runtimeCoverage": runtime_coverage,
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


def _all_targets(
    node_catalog: dict[str, Any], evaluation_catalog: dict[str, Any]
) -> list[dict[str, str]]:
    component_packages = {
        _package_path(row.get("objectPath")).casefold()
        for row in evaluation_catalog.get("components", [])
        if isinstance(row, dict) and _package_path(row.get("objectPath"))
    }
    targets: list[dict[str, str]] = []
    for node in node_catalog.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        component_ref = node.get("harvestComponent")
        component_package = _package_path(
            component_ref.get("packagePath") if isinstance(component_ref, dict) else ""
        )
        if not node_id or component_package.casefold() not in component_packages:
            continue
        for resource in node.get("resources", {}).get("items", []):
            if not isinstance(resource, dict):
                continue
            resource_id = str(resource.get("nodeResourceId") or "")
            if resource_id:
                targets.append(
                    {
                        "nodeId": node_id,
                        "nodeResourceId": resource_id,
                        "key": f"{node_id}::{resource_id}",
                    }
                )
    return sorted(targets, key=lambda row: row["key"])


def deterministic_targets(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    sample_size: int | None,
    seed: str,
) -> list[dict[str, str]]:
    targets = _all_targets(node_catalog, evaluation_catalog)
    if sample_size is None or sample_size >= len(targets):
        return targets
    bounded = max(1, int(sample_size))
    return sorted(
        targets,
        key=lambda row: hashlib.sha256(
            f"{row['key']}{seed}".encode("utf-8")
        ).hexdigest(),
    )[:bounded]


_COVERAGE_FIELDS = (
    "attacksEvaluated",
    "attacksRanked",
    "attacksUnranked",
    "attacksIncompatible",
    "attacksExcludedByScope",
    "excludedByReason",
    "attacksConditionallyEvaluated",
    "conditionallyRankedAttacks",
    "conditionalEvaluationByReason",
    "creatureAssetsExcludedFromScope",
    "attacksExcludedByCreatureScope",
    "excludedCreatureByReason",
    "rankedForNodeResource",
)

_V2_COVERAGE_FIELDS = (
    *_COVERAGE_FIELDS,
    "speciesEvaluated",
    "rowsWithEffectivenessField",
    "rowsWithNonNeutralEffectiveness",
    "rowsConditionalBecauseEffectiveness",
    "canonicalVariantAmbiguousSpecies",
    "canonicalCreatureAssetsAudited",
    "canonicalVariantsAudited",
    "variantSelectionAuditsReturned",
    "variantSelectionAuditsOmitted",
    "canonicalVariantAmbiguityExamples",
    "rankedSpeciesConfirmed",
    "rankedSpeciesConditional",
    "returnedConfirmed",
    "returnedConditional",
    "returned",
    "omitted",
)


def _append_mismatch(
    mismatches: list[dict[str, Any]],
    *,
    target: str,
    field: str,
    expected: Any,
    actual: Any,
) -> None:
    mismatches.append(
        {"target": target, "field": field, "expected": expected, "actual": actual}
    )


def _compare_row_group(
    mismatches: list[dict[str, Any]],
    *,
    target: str,
    group_name: str,
    expected_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
    contract_v2: bool,
    metric: str,
    float_tolerance: float,
) -> None:
    """Compare one ranking tier without flattening confirmed and conditional rows."""

    if len(expected_rows) != len(actual_rows):
        _append_mismatch(
            mismatches,
            target=target,
            field=f"{group_name}.length",
            expected=len(expected_rows),
            actual=len(actual_rows),
        )
    for index in range(min(len(expected_rows), len(actual_rows))):
        expected_row = expected_rows[index]
        actual_row = actual_rows[index]
        prefix = f"{group_name}[{index}]"
        for field in (
            "speciesKey",
            "creatureObjectPath",
            "attackIndex",
            "attackName",
            "rankingStatus",
            "reasonCode",
            "rankingTier",
            "rank",
            "usageEligibilityStatus",
            "usageConditionReasonCodes",
            "evidence",
        ):
            if expected_row.get(field) != actual_row.get(field):
                _append_mismatch(
                    mismatches,
                    target=target,
                    field=f"{prefix}.{field}",
                    expected=expected_row.get(field),
                    actual=actual_row.get(field),
                )

        expected_score = expected_row.get("estimatedYieldPerNode")
        actual_score = actual_row.get("estimatedYieldPerNode")
        scores_match = (
            isinstance(expected_score, (int, float))
            and isinstance(actual_score, (int, float))
            and math.isclose(
                float(expected_score),
                float(actual_score),
                rel_tol=float_tolerance,
                abs_tol=float_tolerance,
            )
        )
        if not scores_match:
            _append_mismatch(
                mismatches,
                target=target,
                field=f"{prefix}.estimatedYieldPerNode",
                expected=expected_score,
                actual=actual_score,
            )

        alias_basis = actual_score
        if contract_v2:
            if expected_row.get("scoreBasis") != actual_row.get("scoreBasis"):
                _append_mismatch(
                    mismatches,
                    target=target,
                    field=f"{prefix}.scoreBasis",
                    expected=expected_row.get("scoreBasis"),
                    actual=actual_row.get("scoreBasis"),
                )
            for runtime_field in ("runtimeStatus", "runtimeObservation"):
                if expected_row.get(runtime_field) != actual_row.get(runtime_field):
                    _append_mismatch(
                        mismatches,
                        target=target,
                        field=f"{prefix}.{runtime_field}",
                        expected=expected_row.get(runtime_field),
                        actual=actual_row.get(runtime_field),
                    )
            expected_selected = expected_row.get(metric)
            actual_selected = actual_row.get(metric)
            selected_matches = (
                isinstance(expected_selected, (int, float))
                and not isinstance(expected_selected, bool)
                and isinstance(actual_selected, (int, float))
                and not isinstance(actual_selected, bool)
                and math.isclose(
                    float(expected_selected),
                    float(actual_selected),
                    rel_tol=float_tolerance,
                    abs_tol=float_tolerance,
                )
            )
            if not selected_matches:
                _append_mismatch(
                    mismatches,
                    target=target,
                    field=f"{prefix}.{metric}",
                    expected=expected_selected,
                    actual=actual_selected,
                )
            expected_static = expected_row.get("staticCompleteNodeTargetYield")
            actual_static = actual_row.get("staticCompleteNodeTargetYield")
            static_matches = (
                isinstance(expected_static, (int, float))
                and isinstance(actual_static, (int, float))
                and math.isclose(
                    float(expected_static),
                    float(actual_static),
                    rel_tol=float_tolerance,
                    abs_tol=float_tolerance,
                )
            )
            if not static_matches:
                _append_mismatch(
                    mismatches,
                    target=target,
                    field=f"{prefix}.staticCompleteNodeTargetYield",
                    expected=expected_static,
                    actual=actual_static,
                )
            alias_basis = actual_static
            expected_selection = expected_row.get("variantSelection") or {}
            actual_selection = actual_row.get("variantSelection") or {}
            for selection_field in (
                "policy",
                "selectedObjectPath",
                "canonicalObjectPath",
                "selectionReasons",
                "excludedVariantClasses",
                "ambiguous",
                "ambiguityReasons",
                "excludedObjectPaths",
                "comparison",
                "higherExploratoryVariantExists",
            ):
                if expected_selection.get(selection_field) != actual_selection.get(
                    selection_field
                ):
                    _append_mismatch(
                        mismatches,
                        target=target,
                        field=f"{prefix}.variantSelection.{selection_field}",
                        expected=expected_selection.get(selection_field),
                        actual=actual_selection.get(selection_field),
                    )
            expected_breakdown_metric = (expected_row.get("scoreBreakdown") or {}).get(
                "metric"
            )
            actual_breakdown_metric = (actual_row.get("scoreBreakdown") or {}).get(
                "metric"
            )
            if expected_breakdown_metric != actual_breakdown_metric:
                _append_mismatch(
                    mismatches,
                    target=target,
                    field=f"{prefix}.scoreBreakdown.metric",
                    expected=expected_breakdown_metric,
                    actual=actual_breakdown_metric,
                )

        expected_relative = expected_row.get("relativeToNodeTopPercent")
        actual_relative = actual_row.get("relativeToNodeTopPercent")
        relative_matches = (
            isinstance(expected_relative, (int, float))
            and isinstance(actual_relative, (int, float))
            and math.isclose(
                float(expected_relative),
                float(actual_relative),
                rel_tol=float_tolerance,
                abs_tol=float_tolerance,
            )
        )
        if not relative_matches:
            _append_mismatch(
                mismatches,
                target=target,
                field=f"{prefix}.relativeToNodeTopPercent",
                expected=expected_relative,
                actual=actual_relative,
            )

        # Optional compatibility field: when present it is only an alias of
        # the selected static metric and cannot carry a second ranking value.
        if "engineComparisonIndex" in actual_row:
            alias_score = actual_row.get("engineComparisonIndex")
            alias_matches = (
                isinstance(alias_basis, (int, float))
                and isinstance(alias_score, (int, float))
                and math.isclose(
                    float(alias_basis),
                    float(alias_score),
                    rel_tol=float_tolerance,
                    abs_tol=float_tolerance,
                )
            )
            if not alias_matches:
                _append_mismatch(
                    mismatches,
                    target=target,
                    field=f"{prefix}.engineComparisonIndexAlias",
                    expected=alias_basis,
                    actual=alias_score,
                )


def _compare_specialty_group(
    mismatches: list[dict[str, Any]],
    *,
    target: str,
    group_name: str,
    expected_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
    float_tolerance: float,
) -> None:
    if len(expected_rows) != len(actual_rows):
        _append_mismatch(
            mismatches,
            target=target,
            field=f"{group_name}.length",
            expected=len(expected_rows),
            actual=len(actual_rows),
        )
    for index in range(min(len(expected_rows), len(actual_rows))):
        expected_row = expected_rows[index]
        actual_row = actual_rows[index]
        prefix = f"{group_name}[{index}]"
        scalar_fields = (
            "speciesKey",
            "creatureObjectPath",
            "attackIndex",
            "attackName",
            "rankingTier",
            "relativeBasisTier",
            "rank",
            "selectedMetric",
            "scoreBasis",
            "runtimeStatus",
            "runtimeObservation",
        )
        for field in scalar_fields:
            if expected_row.get(field) != actual_row.get(field):
                _append_mismatch(
                    mismatches,
                    target=target,
                    field=f"{prefix}.{field}",
                    expected=expected_row.get(field),
                    actual=actual_row.get(field),
                )
        for container_name, fields in (
            ("node", ("id", "name", "objectPath")),
            (
                "resource",
                (
                    "nodeResourceId",
                    "resource",
                    "entryIndex",
                    "harvestComponentPackagePath",
                ),
            ),
            (
                "nodeTop",
                (
                    "speciesKey",
                    "creatureObjectPath",
                    "attackIndex",
                    "attackName",
                    "selectedMetric",
                    "rankingTier",
                ),
            ),
        ):
            expected_container = expected_row.get(container_name) or {}
            actual_container = actual_row.get(container_name) or {}
            for field in fields:
                if expected_container.get(field) != actual_container.get(field):
                    _append_mismatch(
                        mismatches,
                        target=target,
                        field=f"{prefix}.{container_name}.{field}",
                        expected=expected_container.get(field),
                        actual=actual_container.get(field),
                    )
        for field in (
            "selectedMetricValue",
            "nodeTopSelectedMetricValue",
            "relativeToNodeTopPercent",
        ):
            expected_value = expected_row.get(field)
            actual_value = actual_row.get(field)
            matches = (
                isinstance(expected_value, (int, float))
                and not isinstance(expected_value, bool)
                and isinstance(actual_value, (int, float))
                and not isinstance(actual_value, bool)
                and math.isclose(
                    float(expected_value),
                    float(actual_value),
                    rel_tol=float_tolerance,
                    abs_tol=float_tolerance,
                )
            )
            if not matches:
                _append_mismatch(
                    mismatches,
                    target=target,
                    field=f"{prefix}.{field}",
                    expected=expected_value,
                    actual=actual_value,
                )


def _v2_query_options(
    *,
    metric: str,
    runtime_profile_id: str | None,
    include_preliminary: bool,
) -> dict[str, Any]:
    return {
        "evidence_policy": POLICY_INCLUDE_CONDITIONAL,
        "variant_policy": VARIANT_CANONICAL,
        "metric": metric,
        "availability_policy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        "runtime_profile_id": runtime_profile_id,
        "include_preliminary": bool(include_preliminary),
    }


def _compare_v2_context(
    mismatches: list[dict[str, Any]],
    *,
    target: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    metric: str,
    reverse: bool,
) -> None:
    expected_methodology = expected.get("methodology") or {}
    actual_methodology = actual.get("methodology") or {}
    fields = ["metric", "scoreBasis", "unit", "runtime"]
    if reverse:
        fields.extend(["sortMetric", "relativeBasis", "tiePolicy"])
    else:
        fields.append("warning")
    for field in fields:
        if expected_methodology.get(field) != actual_methodology.get(field):
            _append_mismatch(
                mismatches,
                target=target,
                field=f"methodology.{field}",
                expected=expected_methodology.get(field),
                actual=actual_methodology.get(field),
            )
    expected_policy = expected.get("queryPolicy") or {}
    actual_policy = actual.get("queryPolicy") or {}
    for field in (
        "evidence",
        "variant",
        "metric",
        "availability",
        "runtimeProfileId",
        "includePreliminary",
        "exploratory",
    ):
        if expected_policy.get(field) != actual_policy.get(field):
            _append_mismatch(
                mismatches,
                target=target,
                field=f"queryPolicy.{field}",
                expected=expected_policy.get(field),
                actual=actual_policy.get(field),
            )
    if expected_methodology.get("metric") != metric:
        _append_mismatch(
            mismatches,
            target=target,
            field="independent.methodology.metric",
            expected=metric,
            actual=expected_methodology.get("metric"),
        )


def _verify_catalogs_v2(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    reference_query: ReferenceQuery,
    reference_specialties_query: ReferenceSpecialtiesQuery | None,
    runtime_observations: dict[RuntimeObservationKey, dict[str, Any]] | None,
    runtime_profile_id: str | None,
    include_preliminary: bool,
    reverse_species: list[str] | None,
    reverse_page_size: int,
    sample_size: int | None,
    seed: str,
    limit: int,
    float_tolerance: float,
) -> dict[str, Any]:
    all_targets = _all_targets(node_catalog, evaluation_catalog)
    targets = deterministic_targets(
        node_catalog, evaluation_catalog, sample_size=sample_size, seed=seed
    )
    mismatches: list[dict[str, Any]] = []
    forward_coverage: dict[str, dict[str, Any]] = {}
    total_expected_rows = 0
    total_actual_rows = 0
    total_comparisons = 0
    recomputed = Counter()
    if not targets:
        _append_mismatch(
            mismatches,
            target="<selection>",
            field="selection.targetsEligible",
            expected="at least one node/resource target",
            actual=0,
        )

    runtime_profile_id, runtime_coverage = _runtime_profile_context(
        runtime_observations,
        requested_profile_id=runtime_profile_id,
        runtime_metric=True,
    )
    runtime_ready = bool(runtime_profile_id) and (
        runtime_coverage["publishableConfirmedRows"] > 0
        or (
            include_preliminary
            and runtime_coverage["preliminaryRows"] > 0
        )
    )
    for metric in V2_METRICS:
        if V2_METRIC_CONTRACTS[metric]["runtime"] is True and not runtime_ready:
            forward_coverage[metric] = {
                "status": "SKIPPED_WITH_REASON",
                "reason": "CONTROLLED_RUNTIME_FIXTURE_AND_PROFILE_REQUIRED",
                "targetsSelected": len(targets),
                "targetsCompared": 0,
                "rowsCompared": 0,
            }
            continue
        mismatch_start = len(mismatches)
        metric_targets_compared = 0
        metric_rows_compared = 0
        options = _v2_query_options(
            metric=metric,
            runtime_profile_id=(runtime_profile_id if runtime_ready else None),
            include_preliminary=include_preliminary,
        )
        for selected_target in targets:
            key = selected_target["key"]
            comparison_target = f"forward:{metric}:{key}"
            try:
                expected = independently_rank_target(
                    node_catalog,
                    evaluation_catalog,
                    node_id=selected_target["nodeId"],
                    node_resource_id=selected_target["nodeResourceId"],
                    limit=limit,
                    evidence_policy=options["evidence_policy"],
                    variant_policy=options["variant_policy"],
                    metric=metric,
                    availability_policy=options["availability_policy"],
                    runtime_observations=runtime_observations,
                    runtime_profile_id=options["runtime_profile_id"],
                    include_preliminary=options["include_preliminary"],
                )
                actual = reference_query(
                    selected_target["nodeId"],
                    selected_target["nodeResourceId"],
                    limit,
                    dict(options),
                )
            except Exception as exc:
                _append_mismatch(
                    mismatches,
                    target=comparison_target,
                    field="queryError",
                    expected="successful comparison",
                    actual=f"{type(exc).__name__}: {exc}",
                )
                continue

            for group_name in ("confirmedItems", "conditionalItems"):
                expected_group = expected.get(group_name)
                actual_group = actual.get(group_name)
                expected_group = (
                    expected_group if isinstance(expected_group, list) else []
                )
                actual_group = actual_group if isinstance(actual_group, list) else []
                total_expected_rows += len(expected_group)
                total_actual_rows += len(actual_group)
                metric_rows_compared += min(
                    len(expected_group), len(actual_group)
                )
                _compare_row_group(
                    mismatches,
                    target=comparison_target,
                    group_name=group_name,
                    expected_rows=expected_group,
                    actual_rows=actual_group,
                    contract_v2=True,
                    metric=metric,
                    float_tolerance=float_tolerance,
                )
            expected_confirmed = expected.get("confirmedItems", [])
            actual_confirmed = actual.get("confirmedItems", [])
            if expected.get("items", []) != expected_confirmed:
                _append_mismatch(
                    mismatches,
                    target=comparison_target,
                    field="independent.itemsConfirmedCompatibility",
                    expected=expected_confirmed,
                    actual=expected.get("items", []),
                )
            if actual.get("items", []) != actual_confirmed:
                _append_mismatch(
                    mismatches,
                    target=comparison_target,
                    field="items.confirmedCompatibility",
                    expected=actual_confirmed,
                    actual=actual.get("items", []),
                )
            _compare_v2_context(
                mismatches,
                target=comparison_target,
                expected=expected,
                actual=actual,
                metric=metric,
                reverse=False,
            )
            if expected.get("variantSelectionAudits", []) != actual.get(
                "variantSelectionAudits", []
            ):
                _append_mismatch(
                    mismatches,
                    target=comparison_target,
                    field="variantSelectionAudits",
                    expected=expected.get("variantSelectionAudits", []),
                    actual=actual.get("variantSelectionAudits", []),
                )
            expected_coverage = expected.get("coverage") or {}
            actual_coverage = actual.get("coverage") or {}
            for field in _V2_COVERAGE_FIELDS:
                if expected_coverage.get(field) != actual_coverage.get(field):
                    _append_mismatch(
                        mismatches,
                        target=comparison_target,
                        field=f"coverage.{field}",
                        expected=expected_coverage.get(field),
                        actual=actual_coverage.get(field),
                    )
            for field in (
                "attacksEvaluated",
                "attacksRanked",
                "attacksUnranked",
                "attacksIncompatible",
                "attacksExcludedByScope",
                "attacksExcludedByCreatureScope",
            ):
                value = expected_coverage.get(field)
                if isinstance(value, int):
                    recomputed[field] += value
            metric_targets_compared += 1
            total_comparisons += 1
        metric_status = (
            "FAILED"
            if len(mismatches) > mismatch_start
            else (
                "SKIPPED_WITH_REASON"
                if V2_METRIC_CONTRACTS[metric]["runtime"] is True
                and metric_rows_compared == 0
                else "VERIFIED"
            )
        )
        forward_coverage[metric] = {
            "status": metric_status,
            "targetsSelected": len(targets),
            "targetsCompared": metric_targets_compared,
            "rowsCompared": metric_rows_compared,
            **(
                {"reason": "NO_CONTROLLED_RUNTIME_ROWS_IN_SELECTED_TARGETS"}
                if metric_status == "SKIPPED_WITH_REASON"
                else {}
            ),
        }

    if reference_specialties_query is None:
        reverse_coverage: dict[str, Any] = {
            "status": "SKIPPED_WITH_REASON",
            "reason": "REFERENCE_SPECIALTIES_QUERY_NOT_PROVIDED",
            "metrics": {},
        }
    else:
        available_species = sorted(
            {
                str(
                    creature.get("speciesKey")
                    or creature.get("objectPath")
                    or creature.get("name")
                    or ""
                ).casefold()
                for creature in evaluation_catalog.get("creatures", [])
                if isinstance(creature, dict)
                and str(
                    creature.get("speciesKey")
                    or creature.get("objectPath")
                    or creature.get("name")
                    or ""
                )
            }
        )
        selected_species = (
            [str(value).casefold() for value in reverse_species if str(value)]
            if reverse_species is not None
            else sorted(
                available_species,
                key=lambda value: hashlib.sha256(
                    f"reverse:{value}:{seed}".encode("utf-8")
                ).hexdigest(),
            )[:1]
        )
        reverse_metrics: dict[str, dict[str, Any]] = {}
        reverse_forward_cache: dict[tuple[object, ...], dict[str, Any]] = {}
        page_size = max(1, min(int(reverse_page_size), 100))
        for metric in V2_METRICS:
            if V2_METRIC_CONTRACTS[metric]["runtime"] is True and not runtime_ready:
                reverse_metrics[metric] = {
                    "status": "SKIPPED_WITH_REASON",
                    "reason": "CONTROLLED_RUNTIME_FIXTURE_AND_PROFILE_REQUIRED",
                    "speciesCompared": 0,
                    "pagesCompared": 0,
                    "rowsCompared": 0,
                }
                continue
            mismatch_start = len(mismatches)
            options = _v2_query_options(
                metric=metric,
                runtime_profile_id=(runtime_profile_id if runtime_ready else None),
                include_preliminary=include_preliminary,
            )
            species_compared = 0
            pages_compared = 0
            rows_compared = 0
            for species in selected_species:
                for page_offset in (0, page_size):
                    comparison_target = (
                        f"reverse:{metric}:{species}:offset={page_offset}"
                    )
                    try:
                        expected = independently_rank_specialties(
                            node_catalog,
                            evaluation_catalog,
                            species_key=species,
                            offset=page_offset,
                            limit=page_size,
                            evidence_policy=options["evidence_policy"],
                            variant_policy=options["variant_policy"],
                            metric=metric,
                            availability_policy=options["availability_policy"],
                            runtime_observations=runtime_observations,
                            runtime_profile_id=options["runtime_profile_id"],
                            include_preliminary=options["include_preliminary"],
                            _forward_cache=reverse_forward_cache,
                        )
                        actual = reference_specialties_query(
                            species,
                            page_offset,
                            page_size,
                            dict(options),
                        )
                    except Exception as exc:
                        _append_mismatch(
                            mismatches,
                            target=comparison_target,
                            field="queryError",
                            expected="successful comparison",
                            actual=f"{type(exc).__name__}: {exc}",
                        )
                        continue
                    for group_name in ("confirmedItems", "conditionalItems"):
                        expected_group = expected.get(group_name)
                        actual_group = actual.get(group_name)
                        expected_group = (
                            expected_group if isinstance(expected_group, list) else []
                        )
                        actual_group = (
                            actual_group if isinstance(actual_group, list) else []
                        )
                        rows_compared += min(
                            len(expected_group), len(actual_group)
                        )
                        _compare_specialty_group(
                            mismatches,
                            target=comparison_target,
                            group_name=group_name,
                            expected_rows=expected_group,
                            actual_rows=actual_group,
                            float_tolerance=float_tolerance,
                        )
                    if actual.get("items", []) != actual.get("confirmedItems", []):
                        _append_mismatch(
                            mismatches,
                            target=comparison_target,
                            field="items.confirmedCompatibility",
                            expected=actual.get("confirmedItems", []),
                            actual=actual.get("items", []),
                        )
                    _compare_v2_context(
                        mismatches,
                        target=comparison_target,
                        expected=expected,
                        actual=actual,
                        metric=metric,
                        reverse=True,
                    )
                    for field in (
                        "page",
                        "total",
                        "offset",
                        "limit",
                        "nextOffset",
                    ):
                        if expected.get(field) != actual.get(field):
                            _append_mismatch(
                                mismatches,
                                target=comparison_target,
                                field=field,
                                expected=expected.get(field),
                                actual=actual.get(field),
                            )
                    pages_compared += 1
                species_compared += 1
            metric_status = (
                "FAILED"
                if len(mismatches) > mismatch_start
                else (
                    "SKIPPED_WITH_REASON"
                    if V2_METRIC_CONTRACTS[metric]["runtime"] is True
                    and rows_compared == 0
                    else "VERIFIED"
                )
            )
            reverse_metrics[metric] = {
                "status": metric_status,
                "speciesCompared": species_compared,
                "pagesCompared": pages_compared,
                "rowsCompared": rows_compared,
                **(
                    {"reason": "NO_CONTROLLED_RUNTIME_ROWS_FOR_SELECTED_SPECIES"}
                    if metric_status == "SKIPPED_WITH_REASON"
                    else {}
                ),
            }
        reverse_status = (
            "FAILED"
            if any(
                row.get("status") == "FAILED" for row in reverse_metrics.values()
            )
            else (
                "PARTIALLY_VERIFIED"
                if any(
                    row.get("status") == "SKIPPED_WITH_REASON"
                    for row in reverse_metrics.values()
                )
                else "VERIFIED"
            )
        )
        reverse_coverage = {
            "status": reverse_status,
            "speciesSelected": selected_species,
            "metrics": reverse_metrics,
        }

    mismatch_count = len(mismatches)
    node_bytes = json.dumps(
        node_catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    evaluation_bytes = json.dumps(
        evaluation_catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": VERIFICATION_SCHEMA,
        "status": "PASS" if mismatch_count == 0 else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "verificationBoundary": {
            "proves": (
                "production forward/reverse contract == independent contract oracle"
            ),
            "doesNotProve": "contract agreement == real game yield",
            "rankingContractVersion": "harvest-ranking-contract/v2",
            "runtimeGoldCreatedByVerifier": False,
            "controlledRuntimeFixtureProvided": runtime_ready,
        },
        "methodology": {
            "formulaVersion": FORMULA_VERSION,
            "independenceBoundary": (
                "NO_IMPORT_OR_CALL_TO_HARVEST_EVALUATION_ENGINE_OR_"
                "EVALUATE_ATTACK_RESOURCE"
            ),
            "referenceMode": "BLACK_BOX_FORWARD_AND_REVERSE_CALLBACKS",
            "metric": V2_METRIC,
            "scoreBasis": V2_SCORE_BASIS,
            "unit": V2_UNIT,
            "runtime": False,
            "metricContracts": V2_METRIC_CONTRACTS,
            "metricsAttempted": list(V2_METRICS),
            "reverseSort": (
                "relative DESC, selected metric DESC, stable resource/node identity"
            ),
            "paginationRank": "GLOBAL_TIER_RANK_BEFORE_PAGE_SLICE",
            "usageScope": USAGE_SCOPE,
            "floatTolerance": float_tolerance,
        },
        "inputs": {
            "nodeCatalogSha256": hashlib.sha256(node_bytes).hexdigest(),
            "evaluationCatalogSha256": hashlib.sha256(evaluation_bytes).hexdigest(),
            "nodeDataset": dict(node_catalog.get("dataset") or {}),
            "evaluationDataset": dict(evaluation_catalog.get("dataset") or {}),
            "controlledRuntimeObservationRows": len(runtime_observations or {}),
            "runtimeProfileId": runtime_profile_id,
            "includePreliminary": bool(include_preliminary),
        },
        "selection": {
            "mode": "ALL" if sample_size is None else "DETERMINISTIC_SAMPLE",
            "seed": seed,
            "targetsEligible": len(all_targets),
            "targetsSelected": len(targets),
            "targetKeys": [row["key"] for row in targets],
        },
        "coverageByDirection": {
            "forward": forward_coverage,
            "reverse": reverse_coverage,
        },
        "comparison": {
            "targetsCompared": total_comparisons,
            "expectedTopRows": total_expected_rows,
            "actualTopRows": total_actual_rows,
            "mismatchCount": mismatch_count,
        },
        "independentRecomputation": {
            "attackResourcePairsEvaluated": recomputed["attacksEvaluated"],
            "rankedAttackResourcePairs": recomputed["attacksRanked"],
            "unrankedAttackResourcePairs": recomputed["attacksUnranked"],
            "incompatibleAttackResourcePairs": recomputed[
                "attacksIncompatible"
            ],
            "attacksExcludedByUsageScope": recomputed[
                "attacksExcludedByScope"
            ],
            "attacksExcludedByCreatureScope": recomputed[
                "attacksExcludedByCreatureScope"
            ],
        },
        "mismatches": mismatches[:100],
        "mismatchesOmitted": max(0, mismatch_count - 100),
    }


def verify_catalogs(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    reference_query: ReferenceQuery,
    reference_specialties_query: ReferenceSpecialtiesQuery | None = None,
    runtime_observations: dict[RuntimeObservationKey, dict[str, Any]] | None = None,
    runtime_profile_id: str | None = None,
    include_preliminary: bool = False,
    reverse_species: list[str] | None = None,
    reverse_page_size: int = 2,
    sample_size: int | None = 32,
    seed: str = "phase5-v1",
    limit: int = 10,
    float_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Compare independent recomputation with a black-box query callback."""

    contract_v2 = (
        evaluation_catalog.get("methodology", {}).get("contractVersion")
        == "harvest-ranking-contract/v2"
    )
    if contract_v2:
        return _verify_catalogs_v2(
            node_catalog,
            evaluation_catalog,
            reference_query=reference_query,
            reference_specialties_query=reference_specialties_query,
            runtime_observations=runtime_observations,
            runtime_profile_id=runtime_profile_id,
            include_preliminary=include_preliminary,
            reverse_species=reverse_species,
            reverse_page_size=reverse_page_size,
            sample_size=sample_size,
            seed=seed,
            limit=limit,
            float_tolerance=float_tolerance,
        )

    all_targets = _all_targets(node_catalog, evaluation_catalog)
    targets = deterministic_targets(
        node_catalog, evaluation_catalog, sample_size=sample_size, seed=seed
    )
    mismatches: list[dict[str, Any]] = []
    expected_rows = 0
    actual_rows = 0
    comparisons = 0
    recomputed = Counter()
    if not targets:
        _append_mismatch(
            mismatches,
            target="<selection>",
            field="selection.targetsEligible",
            expected="at least one node/resource target",
            actual=0,
        )
    for target in targets:
        key = target["key"]
        try:
            expected = independently_rank_target(
                node_catalog,
                evaluation_catalog,
                node_id=target["nodeId"],
                node_resource_id=target["nodeResourceId"],
                limit=limit,
            )
            actual = reference_query(
                target["nodeId"], target["nodeResourceId"], limit
            )
        except Exception as exc:  # surfaced as structured verification evidence
            _append_mismatch(
                mismatches,
                target=key,
                field="queryError",
                expected="successful comparison",
                actual=f"{type(exc).__name__}: {exc}",
            )
            continue
        if contract_v2:
            row_groups = ("confirmedItems", "conditionalItems")
        else:
            row_groups = ("items",)
        for group_name in row_groups:
            expected_group = expected.get(group_name, [])
            actual_group = actual.get(group_name, [])
            expected_group = (
                expected_group if isinstance(expected_group, list) else []
            )
            actual_group = actual_group if isinstance(actual_group, list) else []
            expected_rows += len(expected_group)
            actual_rows += len(actual_group)
            _compare_row_group(
                mismatches,
                target=key,
                group_name=group_name,
                expected_rows=expected_group,
                actual_rows=actual_group,
                contract_v2=contract_v2,
                metric=(V2_METRIC if contract_v2 else "estimatedYieldPerNode"),
                float_tolerance=float_tolerance,
            )
        comparisons += 1

        if contract_v2:
            expected_confirmed = expected.get("confirmedItems", [])
            actual_confirmed = actual.get("confirmedItems", [])
            expected_compatibility = expected.get("items", [])
            actual_compatibility = actual.get("items", [])
            if expected_compatibility != expected_confirmed:
                _append_mismatch(
                    mismatches,
                    target=key,
                    field="independent.itemsConfirmedCompatibility",
                    expected=expected_confirmed,
                    actual=expected_compatibility,
                )
            if actual_compatibility != actual_confirmed:
                _append_mismatch(
                    mismatches,
                    target=key,
                    field="items.confirmedCompatibility",
                    expected=actual_confirmed,
                    actual=actual_compatibility,
                )

            expected_methodology = expected.get("methodology", {})
            actual_methodology = actual.get("methodology", {})
            for field in ("metric", "scoreBasis", "unit", "runtime"):
                if expected_methodology.get(field) != actual_methodology.get(field):
                    _append_mismatch(
                        mismatches,
                        target=key,
                        field=f"methodology.{field}",
                        expected=expected_methodology.get(field),
                        actual=actual_methodology.get(field),
                    )

            expected_audits = expected.get("variantSelectionAudits", [])
            actual_audits = actual.get("variantSelectionAudits", [])
            if expected_audits != actual_audits:
                _append_mismatch(
                    mismatches,
                    target=key,
                    field="variantSelectionAudits",
                    expected=expected_audits,
                    actual=actual_audits,
                )
        expected_coverage = expected.get("coverage", {})
        actual_coverage = actual.get("coverage", {})
        for field in (
            "attacksEvaluated",
            "attacksRanked",
            "attacksUnranked",
            "attacksIncompatible",
            "attacksExcludedByScope",
            "attacksExcludedByCreatureScope",
        ):
            value = expected_coverage.get(field)
            if isinstance(value, int):
                recomputed[field] += value
        coverage_fields = _V2_COVERAGE_FIELDS if contract_v2 else _COVERAGE_FIELDS
        for field in coverage_fields:
            if expected_coverage.get(field) != actual_coverage.get(field):
                _append_mismatch(
                    mismatches,
                    target=key,
                    field=f"coverage.{field}",
                    expected=expected_coverage.get(field),
                    actual=actual_coverage.get(field),
                )

    mismatch_count = len(mismatches)
    node_bytes = json.dumps(
        node_catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    evaluation_bytes = json.dumps(
        evaluation_catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "schema": VERIFICATION_SCHEMA,
        "status": "PASS" if mismatch_count == 0 else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "verificationBoundary": {
            "proves": "production implementation == independent implementation",
            "doesNotProve": "static model == real game",
            **(
                {
                    "rankingContractVersion": "harvest-ranking-contract/v2",
                    "runtimeGoldCreatedByVerifier": False,
                }
                if contract_v2
                else {}
            ),
        },
        "methodology": {
            "formulaVersion": FORMULA_VERSION,
            "independenceBoundary": (
                "NO_IMPORT_OR_CALL_TO_HARVEST_EVALUATION_ENGINE_OR_"
                "EVALUATE_ATTACK_RESOURCE"
            ),
            "referenceMode": "BLACK_BOX_QUERY_CALLBACK",
            "metric": (
                V2_METRIC
                if contract_v2
                else "estimatedYieldPerNode"
            ),
            "scoreBasis": (
                V2_SCORE_BASIS
                if contract_v2
                else SCORE_BASIS
            ),
            **(
                {"unit": V2_UNIT, "runtime": False}
                if contract_v2
                else {}
            ),
            "score": (
                "finite native-style per-hit node simulation; grantCalls * "
                "normalizedResourceWeight * (quantityMin + quantityMax) / 2"
            ),
            "attackIntervalRole": "DIAGNOSTIC_ONLY_NOT_USED_FOR_NODE_YIELD_ORDER",
            "normalizedHarvestAmountScale": NORMALIZED_HARVEST_AMOUNT_SCALE,
            "unclampedFinalHitCap": "3.5 * remainingHarvestHealth",
            "unsupportedModelsFailClosed": [
                "bIsSingleUnitHarvest=true",
                "DamageHarvestAdditionalEffectiveness!=0",
                "OverrideQuantityRandomPower!=1",
                "bUseBlueprintAdjustOutputDamage=true",
            ],
            "usageScope": USAGE_SCOPE,
            "floatTolerance": float_tolerance,
        },
        "inputs": {
            "nodeCatalogSha256": hashlib.sha256(node_bytes).hexdigest(),
            "evaluationCatalogSha256": hashlib.sha256(evaluation_bytes).hexdigest(),
            "nodeDataset": dict(node_catalog.get("dataset") or {}),
            "evaluationDataset": dict(evaluation_catalog.get("dataset") or {}),
        },
        "selection": {
            "mode": "ALL" if sample_size is None else "DETERMINISTIC_SAMPLE",
            "seed": seed,
            "targetsEligible": len(all_targets),
            "targetsSelected": len(targets),
            "targetKeys": [row["key"] for row in targets],
        },
        "comparison": {
            "targetsCompared": comparisons,
            "expectedTopRows": expected_rows,
            "actualTopRows": actual_rows,
            "mismatchCount": mismatch_count,
        },
        "independentRecomputation": {
            "attackResourcePairsEvaluated": recomputed["attacksEvaluated"],
            "rankedAttackResourcePairs": recomputed["attacksRanked"],
            "unrankedAttackResourcePairs": recomputed["attacksUnranked"],
            "incompatibleAttackResourcePairs": recomputed["attacksIncompatible"],
            "attacksExcludedByUsageScope": recomputed["attacksExcludedByScope"],
            "attacksExcludedByCreatureScope": recomputed[
                "attacksExcludedByCreatureScope"
            ],
        },
        "mismatches": mismatches[:100],
        "mismatchesOmitted": max(0, mismatch_count - 100),
    }
