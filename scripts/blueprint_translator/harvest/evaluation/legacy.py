"""Legacy Harvest ranking fallback kept isolated from Contract v2 orchestration."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from ...resource_nodes import canonical_package_path
from ..contracts import YIELD_MODEL_VERSION, YIELD_SCORE_BASIS
from .aggregation import (
    _estimated_yield,
    _stable_row_identity,
    find_node_and_resource,
    prepare_attack_for_usage_scope,
)
from .contracts import RANKING_RESULT_SCHEMA, TAMED_RIDDEN


def rank_node_resource_v1(
    engine: Any,
    node_catalog: dict[str, Any],
    *,
    node_id: str,
    node_resource_id: str,
    limit: int = 10,
    evaluate_attack: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    node, resource = find_node_and_resource(
        node_catalog, node_id, node_resource_id
    )
    component_ref = node.get("harvestComponent")
    component_package = canonical_package_path(
        component_ref.get("packagePath") if isinstance(component_ref, dict) else ""
    )
    component = engine.components.get(component_package.casefold())
    if not isinstance(component, dict):
        raise KeyError("HARVEST_COMPONENT_NOT_FOUND")

    usage_scope = str(
        engine.catalog.get("methodology", {}).get("usageScope") or TAMED_RIDDEN
    )
    require_confirmed_rideability = (
        engine.catalog.get("methodology", {}).get("rideabilityRequirement")
        == "B_ALLOW_RIDING_TRUE"
    )
    resource_class = str(resource.get("resource") or "")
    resource_entry_index = resource.get("entryIndex")
    considered_creatures = [
        creature
        for creature in engine.creatures
        if str(creature.get("tameability", {}).get("status") or "UNKNOWN")
        != "PREVENTED"
        and (
            not require_confirmed_rideability
            or str(creature.get("rideability", {}).get("status") or "UNKNOWN")
            == "ALLOWED"
        )
    ]
    variant_counts = Counter(
        str(creature.get("speciesKey") or creature.get("objectPath") or "").casefold()
        for creature in considered_creatures
    )
    excluded = Counter()
    excluded_creatures = Counter()
    attacks_excluded_by_creature_scope = 0
    dispositions = Counter()
    conditional_evaluations = Counter()
    attacks_conditionally_evaluated = 0
    conditionally_ranked_attacks = 0
    best_by_species: dict[str, dict[str, Any]] = {}
    species_with_eligible_attack: set[str] = set()

    for creature in engine.creatures:
        tameability = creature.get("tameability")
        if (
            isinstance(tameability, dict)
            and tameability.get("status") == "PREVENTED"
        ):
            reasons = tameability.get("reasonCodes")
            reason_values = (
                [str(value) for value in reasons if value]
                if isinstance(reasons, list)
                else ["CREATURE_NOT_TAMEABLE"]
            )
            for reason in reason_values or ["CREATURE_NOT_TAMEABLE"]:
                excluded_creatures[reason] += 1
            attacks_excluded_by_creature_scope += sum(
                1 for attack in creature.get("attacks", []) if isinstance(attack, dict)
            )
            continue
        rideability = creature.get("rideability")
        rideability_status = str(
            rideability.get("status")
            if isinstance(rideability, dict)
            else "UNKNOWN"
        ) or "UNKNOWN"
        rideability_reason_codes = (
            [str(value) for value in rideability.get("reasonCodes", []) if value]
            if isinstance(rideability, dict)
            else ["RIDEABILITY_NOT_RECOVERED"]
        )
        if require_confirmed_rideability and rideability_status != "ALLOWED":
            fallback_reason = (
                "RIDING_NOT_ALLOWED"
                if rideability_status == "PREVENTED"
                else "RIDEABILITY_NOT_RECOVERED"
            )
            for reason in rideability_reason_codes or [fallback_reason]:
                excluded_creatures[reason] += 1
            attacks_excluded_by_creature_scope += sum(
                1 for attack in creature.get("attacks", []) if isinstance(attack, dict)
            )
            continue
        species_key = str(
            creature.get("speciesKey") or creature.get("objectPath") or creature.get("name") or ""
        ).casefold()
        tameability_status = str(
            tameability.get("status")
            if isinstance(tameability, dict)
            else "UNKNOWN"
        ) or "UNKNOWN"
        tameability_reason_codes = (
            [str(value) for value in tameability.get("reasonCodes", []) if value]
            if isinstance(tameability, dict)
            else ["TAMEABILITY_NOT_RECOVERED"]
        )
        for attack in creature.get("attacks", []):
            if not isinstance(attack, dict):
                continue
            prepared, exclusion_reason = prepare_attack_for_usage_scope(
                attack,
                usage_scope=usage_scope,
            )
            if prepared is None:
                excluded[str(exclusion_reason or "ATTACK_SCOPE_UNKNOWN")] += 1
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
            species_with_eligible_attack.add(species_key)
            row = evaluate_attack(
                creature=str(creature.get("name") or "Unknown creature"),
                creature_object_path=str(creature.get("objectPath") or ""),
                attack=prepared,
                component=component,
                resource=resource_class,
                resource_entry_index=(
                    int(resource_entry_index)
                    if isinstance(resource_entry_index, int)
                    and not isinstance(resource_entry_index, bool)
                    else None
                ),
                damage_type_parents=engine.damage_type_parents,
                resource_damage_overrides=engine.resource_damage_overrides,
                damage_type_gaps=engine.damage_type_gaps,
            )
            disposition = str(row.get("rankingStatus") or "UNRANKED")
            dispositions[disposition] += 1
            score = _estimated_yield(row)
            if disposition != "RANKED" or score is None:
                continue
            if condition_reasons:
                conditionally_ranked_attacks += 1
            creature_evidence_confirmed = tameability_status == "ALLOWED" and (
                not require_confirmed_rideability
                or rideability_status == "ALLOWED"
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
                        if require_confirmed_rideability
                        and rideability_status != "ALLOWED"
                        else []
                    )
                )
            )
            score_breakdown = dict(row.get("scoreBreakdown") or {})
            if score_breakdown:
                score_breakdown["evidenceTier"] = (
                    "CONFIRMED" if evidence_confirmed else "CONDITIONAL"
                )
            row.update(
                {
                    "speciesKey": species_key,
                    "dinoNameTag": creature.get("dinoNameTag"),
                    "variantCount": variant_counts[species_key],
                    "baseAttackInterval": prepared.get("baseAttackInterval"),
                    "riderAttackInterval": prepared.get("riderAttackInterval"),
                    "attackIntervalSource": prepared.get("attackIntervalSource"),
                    "usageEligibilityStatus": prepared.get(
                        "usageEligibilityStatus"
                    ),
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
                    "scoreBreakdown": score_breakdown,
                }
            )
            current = best_by_species.get(species_key)
            current_score = (
                _estimated_yield(current) if current is not None else None
            )
            if (
                current is None
                or current_score is None
                or score > current_score
                or (
                    score == current_score
                    and _stable_row_identity(row) < _stable_row_identity(current)
                )
            ):
                best_by_species[species_key] = row

    ranked = sorted(
        best_by_species.values(),
        key=lambda row: (
            -float(_estimated_yield(row) or 0.0),
            *_stable_row_identity(row),
        ),
    )
    previous_score: float | None = None
    previous_rank = 0
    ranked_with_positions: list[dict[str, Any]] = []
    for ordinal, source_row in enumerate(ranked, start=1):
        row = dict(source_row)
        score = _estimated_yield(row)
        if score is None:
            continue
        if previous_score is None or score != previous_score:
            previous_rank = ordinal
            previous_score = score
        row["rank"] = previous_rank
        ranked_with_positions.append(row)

    bounded_limit = max(1, min(int(limit), 10))
    selected = ranked_with_positions[:bounded_limit]
    top_score = (
        _estimated_yield(ranked_with_positions[0])
        if ranked_with_positions
        else 0.0
    )
    for row in selected:
        score = _estimated_yield(row) or 0.0
        row["relativeToNodeTopPercent"] = (
            round(min(100.0, max(0.0, score / top_score * 100.0)), 3)
            if top_score is not None and top_score > 0
            else 0.0
        )
        row["rankingTier"] = (
            "CONFIRMED"
            if row.get("evidence", {}).get("status") == "CONFIRMED"
            else "CONDITIONAL"
        )

    coverage = dict(engine.catalog.get("coverage") or {})
    coverage.update(
        {
            "speciesEvaluated": len(species_with_eligible_attack),
            "attacksEvaluated": sum(dispositions.values()),
            "attacksRanked": dispositions["RANKED"],
            "attacksUnranked": dispositions["UNRANKED"],
            "attacksIncompatible": dispositions["INCOMPATIBLE"],
            "attacksExcludedByScope": sum(excluded.values()),
            "excludedByReason": dict(sorted(excluded.items())),
            "attacksConditionallyEvaluated": attacks_conditionally_evaluated,
            "conditionallyRankedAttacks": conditionally_ranked_attacks,
            "conditionalEvaluationByReason": dict(
                sorted(conditional_evaluations.items())
            ),
            "creatureAssetsExcludedFromScope": sum(excluded_creatures.values()),
            "attacksExcludedByCreatureScope": attacks_excluded_by_creature_scope,
            "excludedCreatureByReason": dict(sorted(excluded_creatures.items())),
            "rankedForNodeResource": len(ranked_with_positions),
            "rankedSpeciesWithUnknownTameability": sum(
                1
                for row in ranked_with_positions
                if row.get("tameabilityStatus") != "ALLOWED"
            ),
            "rankedSpeciesWithUnknownRideability": sum(
                1
                for row in ranked_with_positions
                if row.get("rideabilityStatus") != "ALLOWED"
            ),
            "rankedSpeciesConfirmed": sum(
                1
                for row in ranked_with_positions
                if row.get("evidence", {}).get("status") == "CONFIRMED"
            ),
            "rankedSpeciesConditional": sum(
                1
                for row in ranked_with_positions
                if row.get("evidence", {}).get("status") != "CONFIRMED"
            ),
            "returned": len(selected),
            "omitted": max(0, len(ranked_with_positions) - len(selected)),
        }
    )
    complete_scope = coverage.get("claimsAllCreatures") is True
    claim_blockers = [
        str(value)
        for value in engine.catalog.get("claimBlockers", [])
        if str(value)
    ]
    evaluation_dataset = engine.catalog.get("dataset")
    dataset = dict(node_catalog.get("dataset") or {})
    if isinstance(evaluation_dataset, dict):
        dataset["evaluationRevision"] = evaluation_dataset.get("revision")
        dataset["evaluationGeneratedAt"] = evaluation_dataset.get("generatedAt")
    return {
        "schema": RANKING_RESULT_SCHEMA,
        "dataset": dataset,
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
            **dict(engine.catalog.get("methodology") or {}),
            "formulaVersion": YIELD_MODEL_VERSION,
            "metric": "estimatedYieldPerNode",
            "scoreBasis": YIELD_SCORE_BASIS,
            "relativeBasis": (
                "ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE_DIVIDED_BY_"
                "NODE_RESOURCE_TOP_YIELD"
            ),
            "tiePolicy": "COMPETITION_RANK_FOR_EQUAL_ESTIMATED_YIELD_1_1_3",
            "engineComparisonIndexPolicy": (
                "COMPATIBILITY_ALIAS_EQUAL_TO_ESTIMATED_YIELD_PER_NODE_"
                "NEVER_USED_FOR_ORDERING"
            ),
            "conditionalEstimatePolicy": (
                "BLUEPRINT_OUTPUT_DAMAGE_HOOKS_FAIL_CLOSED;OTHER_DYNAMIC_"
                "ATTACK_GATES_REMAIN_CONDITIONAL"
            ),
            "warning": (
                "排名按一个完整新鲜资源点的预计目标资源产量排序；这是静态标准化模型，"
                "不是服务器环境下的实测产量。"
            ),
        },
        "scopeStatus": (
            "ALL_DISCOVERED_CREATURES_EVALUATED"
            if complete_scope
            else "PARTIAL_CREATURE_EVIDENCE"
        ),
        "claimsCompleteWithinScope": complete_scope,
        "claimsGlobalTop": False,
        "claimBlockers": claim_blockers,
        "evidence": {
            "status": "COMPLETE" if complete_scope else "PARTIAL",
            "blockers": claim_blockers,
        },
        "coverage": coverage,
        "items": selected,
    }
