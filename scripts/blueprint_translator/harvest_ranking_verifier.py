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


VERIFICATION_SCHEMA = "blueprint-to-code.harvest-independent-verification/v1"
FORMULA_VERSION = "independent-harvest-engine-comparison-index/v2-conditional-scope"
USAGE_SCOPE = "TAMED_RIDDEN"

ReferenceQuery = Callable[[str, str, int], dict[str, Any]]


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


def _disposition(status: str, reason: str, score: float | None = None, **facts: Any) -> dict[str, Any]:
    return {
        "rankingStatus": status,
        "reasonCode": reason,
        "engineComparisonIndex": score,
        **facts,
    }


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
    if (
        list(attack.get("gaps") or [])
        or not source_damage_type
        or not isinstance(base_damage, (int, float))
        or not isinstance(attack_interval, (int, float))
    ):
        return _disposition("UNRANKED", "REQUIRED_ATTACK_FACT_NOT_RECOVERED")

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
    if (
        not isinstance(damage_multiplier, (int, float))
        or not isinstance(quantity_multiplier, (int, float))
        or float(attack_interval) <= 0
    ):
        return _disposition("UNRANKED", "REQUIRED_COEFFICIENT_NOT_RECOVERED")
    pressure = (
        float(base_damage)
        / float(attack_interval)
        * float(damage_multiplier)
        * float(quantity_multiplier)
    )
    return _disposition(
        "RANKED",
        "ENGINE_COEFFICIENTS_RECOVERED",
        pressure * (resource_weight / total_positive_weight),
        sourceDamageType=source_damage_type,
        effectiveDamageType=effective_damage_type,
        baseDamage=float(base_damage),
        attackInterval=float(attack_interval),
        damageMultiplier=float(damage_multiplier),
        harvestQuantityMultiplier=float(quantity_multiplier),
        resourceWeight=resource_weight,
        totalPositiveResourceWeight=total_positive_weight,
        resourceWeightShare=resource_weight / total_positive_weight,
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


def independently_rank_target(
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
            score = disposition.get("engineComparisonIndex")
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
            if current is None or float(score) > float(current["engineComparisonIndex"]):
                best_by_species[species_key] = row

    ranked = sorted(
        best_by_species.values(),
        key=lambda row: (
            -float(row.get("engineComparisonIndex") or 0.0),
            str(row.get("creature") or "").casefold(),
            str(row.get("creatureObjectPath") or ""),
            int(row.get("attackIndex") or 0),
        ),
    )
    bounded_limit = max(1, min(int(limit), 10))
    selected = [dict(row) for row in ranked[:bounded_limit]]
    top_score = float(ranked[0].get("engineComparisonIndex") or 0.0) if ranked else 0.0
    for rank, row in enumerate(selected, start=1):
        row["rank"] = rank
        score = float(row.get("engineComparisonIndex") or 0.0)
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


def verify_catalogs(
    node_catalog: dict[str, Any],
    evaluation_catalog: dict[str, Any],
    *,
    reference_query: ReferenceQuery,
    sample_size: int | None = 32,
    seed: str = "phase5-v1",
    limit: int = 10,
    float_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Compare independent recomputation with a black-box query callback."""

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
        expected_items = expected.get("items") if isinstance(expected, dict) else []
        actual_items = actual.get("items") if isinstance(actual, dict) else []
        expected_items = expected_items if isinstance(expected_items, list) else []
        actual_items = actual_items if isinstance(actual_items, list) else []
        expected_rows += len(expected_items)
        actual_rows += len(actual_items)
        comparisons += 1
        if len(expected_items) != len(actual_items):
            _append_mismatch(
                mismatches,
                target=key,
                field="items.length",
                expected=len(expected_items),
                actual=len(actual_items),
            )
        for index in range(min(len(expected_items), len(actual_items))):
            expected_row = expected_items[index]
            actual_row = actual_items[index]
            for field in (
                "speciesKey",
                "creatureObjectPath",
                "attackIndex",
                "attackName",
                "rankingStatus",
                "reasonCode",
                "rankingTier",
                "usageEligibilityStatus",
                "usageConditionReasonCodes",
                "evidence",
            ):
                if expected_row.get(field) != actual_row.get(field):
                    _append_mismatch(
                        mismatches,
                        target=key,
                        field=f"items[{index}].{field}",
                        expected=expected_row.get(field),
                        actual=actual_row.get(field),
                    )
            expected_score = expected_row.get("engineComparisonIndex")
            actual_score = actual_row.get("engineComparisonIndex")
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
                    target=key,
                    field=f"items[{index}].engineComparisonIndex",
                    expected=expected_score,
                    actual=actual_score,
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
        for field in _COVERAGE_FIELDS:
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
        "methodology": {
            "formulaVersion": FORMULA_VERSION,
            "independenceBoundary": (
                "NO_IMPORT_OR_CALL_TO_HARVEST_EVALUATION_ENGINE_OR_"
                "EVALUATE_ATTACK_RESOURCE"
            ),
            "referenceMode": "BLACK_BOX_QUERY_CALLBACK",
            "score": (
                "baseDamage / attackInterval * damageMultiplier * "
                "harvestQuantityMultiplier * normalizedResourceWeight"
            ),
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
