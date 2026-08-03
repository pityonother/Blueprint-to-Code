"""Per-species attack evaluation for Ranking Contract v2."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Callable, Mapping

from .aggregation import (
    _enrich_v2_metrics,
    _metric_value,
    _stable_row_identity,
    prepare_attack_for_usage_scope,
)
from .contracts import METRIC_CONTRACTS
from .runtime import _eligible_runtime_observation
from .variant_selection import (
    VARIANT_SELECTION_AUDIT_LIMIT,
    project_species_variants,
)


@dataclass(frozen=True)
class SpeciesEvaluationResult:
    grouped: dict[str, list[dict[str, Any]]]
    rows: list[dict[str, Any]]
    attacks_evaluated: int
    attacks_excluded: Counter[str]
    dispositions: Counter[str]
    conditional_evaluations: Counter[str]
    attacks_conditionally_evaluated: int
    conditionally_ranked_attacks: int
    excluded_creatures: Counter[str]
    attacks_excluded_by_creature_scope: int
    rows_with_effectiveness_field: int
    rows_with_non_neutral_effectiveness: int
    rows_conditional_because_effectiveness: int
    all_variant_selection_audits: list[dict[str, Any]]
    ambiguous_variant_audits: list[dict[str, Any]]
    variant_selection_audits: list[dict[str, Any]]


def evaluate_species_catalog(
    engine: Any,
    *,
    component: dict[str, Any],
    usage_scope: str,
    resource_class: str,
    resource_entry_index: int | None,
    node_id: str,
    node_resource_id: str,
    metric: str,
    variant_policy: str,
    runtime_observations: Mapping[
        tuple[str, str, str, str, int], Mapping[str, Any]
    ]
    | None,
    runtime_profile_id: str | None,
    include_preliminary: bool,
    evaluate_attack: Callable[..., dict[str, Any]],
) -> SpeciesEvaluationResult:
    grouped: dict[str, list[dict[str, Any]]] = {}
    excluded_creatures: Counter[str] = Counter()
    attacks_excluded: Counter[str] = Counter()
    dispositions: Counter[str] = Counter()
    conditional_evaluations: Counter[str] = Counter()
    attacks_conditionally_evaluated = 0
    conditionally_ranked_attacks = 0
    attacks_excluded_by_creature_scope = 0
    rows_with_effectiveness_field = 0
    rows_with_non_neutral_effectiveness = 0
    rows_conditional_because_effectiveness = 0
    for creature in engine.creatures:
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
        if tameability_status == "PREVENTED":
            excluded_creatures["CREATURE_NOT_TAMEABLE"] += 1
            attacks_excluded_by_creature_scope += sum(
                1 for attack in creature.get("attacks", []) if isinstance(attack, dict)
            )
            continue
        if rideability_status == "PREVENTED":
            excluded_creatures["RIDING_NOT_ALLOWED"] += 1
            attacks_excluded_by_creature_scope += sum(
                1 for attack in creature.get("attacks", []) if isinstance(attack, dict)
            )
            continue
        species_key = str(
            creature.get("speciesKey")
            or creature.get("objectPath")
            or creature.get("name")
            or ""
        ).casefold()
        if species_key:
            grouped.setdefault(species_key, []).append(creature)

    variant_audit_by_species = engine._canonical_variant_audit_by_species
    all_variant_selection_audits = engine._canonical_variant_audits
    ambiguous_variant_audits = engine._canonical_ambiguous_variant_audits
    variant_selection_audits = [
        deepcopy(audit)
        for audit in all_variant_selection_audits[
            :VARIANT_SELECTION_AUDIT_LIMIT
        ]
    ]

    all_species_rows: list[dict[str, Any]] = []
    attacks_evaluated = 0
    for species_key, variants in sorted(grouped.items()):
        variant_audit = variant_audit_by_species[species_key]
        variant_best_rows_by_tier: dict[str, list[dict[str, Any]]] = {
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
                prepared, exclusion_reason = prepare_attack_for_usage_scope(
                    attack, usage_scope=usage_scope
                )
                if prepared is None:
                    attacks_excluded[
                        str(exclusion_reason or "ATTACK_SCOPE_UNKNOWN")
                    ] += 1
                    continue
                row = evaluate_attack(
                    creature=str(creature.get("name") or "Unknown creature"),
                    creature_object_path=str(creature.get("objectPath") or ""),
                    attack=prepared,
                    component=component,
                    resource=resource_class,
                    resource_entry_index=resource_entry_index,
                    damage_type_parents=engine.damage_type_parents,
                    resource_damage_overrides=engine.resource_damage_overrides,
                    damage_type_gaps=engine.damage_type_gaps,
                )
                attacks_evaluated += 1
                disposition = str(row.get("rankingStatus") or "UNRANKED")
                dispositions[disposition] += 1
                if disposition != "RANKED":
                    continue
                effectiveness = row.get("effectivenessQuantityMultiplier")
                if "effectivenessQuantityMultiplier" in row:
                    rows_with_effectiveness_field += 1
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
                _enrich_v2_metrics(row, metric=metric)
                runtime_key = (
                    str(node_id),
                    str(node_resource_id),
                    species_key,
                    str(creature.get("objectPath") or "").casefold(),
                    int(row.get("attackIndex") or 0),
                )
                runtime_observation = (
                    runtime_observations.get(runtime_key)
                    if runtime_observations is not None
                    else None
                )
                runtime_observation = _eligible_runtime_observation(
                    runtime_observation,
                    runtime_profile_id=runtime_profile_id,
                    include_preliminary=include_preliminary,
                )
                runtime_status = (
                    str(runtime_observation.get("runtimeStatus") or "")
                    if runtime_observation is not None
                    else ""
                )
                runtime_observation_is_eligible = runtime_observation is not None
                if runtime_observation_is_eligible:
                    row.update(
                        {
                            "observedYieldPerNode": runtime_observation.get(
                                "observedYieldPerNode"
                            ),
                            "observedYieldPerSecond": runtime_observation.get(
                                "observedYieldPerSecond"
                            ),
                            "runtimeStatus": runtime_observation.get(
                                "runtimeStatus"
                            ),
                            "runtimeObservation": {
                                "observationSetId": runtime_observation.get(
                                    "observationSetId"
                                ),
                                "runtimeProfileId": runtime_observation.get(
                                    "runtimeProfileId"
                                )
                                or runtime_profile_id,
                                "environmentFingerprint": runtime_observation.get(
                                    "environmentFingerprint"
                                ),
                                "trialCount": runtime_observation.get("trialCount"),
                                "synthetic": False,
                            },
                        }
                    )
                if _metric_value(row, metric) is None:
                    continue
                condition_reasons = [
                    str(value)
                    for value in prepared.get("usageConditionReasonCodes", [])
                    if value
                ]
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
                evidence_gaps.extend(
                    str(value) for value in row.get("missingFacts", []) if value
                )
                if effectiveness_is_non_neutral:
                    evidence_gaps.append(
                        "EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED"
                    )
                if (
                    METRIC_CONTRACTS[metric]["runtime"] is True
                    and runtime_observation_is_eligible
                    and runtime_status == "OBSERVED_PRELIMINARY"
                ):
                    evidence_gaps.append(
                        "OBSERVED_PRELIMINARY_MINIMUM_TRIALS_NOT_MET"
                    )
                evidence_gaps = sorted(set(evidence_gaps))
                confirmed = not evidence_gaps
                score_breakdown = dict(row.get("scoreBreakdown") or {})
                if score_breakdown:
                    score_breakdown["evidenceTier"] = (
                        "CONFIRMED" if confirmed else "CONDITIONAL"
                    )
                row.update(
                    {
                        "speciesKey": species_key,
                        "dinoNameTag": creature.get("dinoNameTag"),
                        "variantCount": len(variants),
                        "baseAttackInterval": prepared.get("baseAttackInterval"),
                        "riderAttackInterval": prepared.get("riderAttackInterval"),
                        "attackIntervalSource": prepared.get("attackIntervalSource"),
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
                        "rankingTier": "CONFIRMED" if confirmed else "CONDITIONAL",
                        "scoreBreakdown": score_breakdown,
                    }
                )
                attack_rows.append(row)
            attack_rows.sort(
                key=lambda row: (
                    -float(_metric_value(row, metric) or 0.0),
                    *_stable_row_identity(row),
                )
            )
            for tier in ("CONFIRMED", "CONDITIONAL"):
                best_for_tier = next(
                    (
                        row
                        for row in attack_rows
                        if row.get("rankingTier") == tier
                    ),
                    None,
                )
                if best_for_tier is not None:
                    variant_best_rows_by_tier[tier].append(best_for_tier)
        all_species_rows.extend(
            project_species_variants(
                variants=variants,
                variant_best_rows_by_tier=variant_best_rows_by_tier,
                variant_audit=variant_audit,
                variant_policy=variant_policy,
                metric=metric,
            )
        )

    return SpeciesEvaluationResult(
        grouped=grouped,
        rows=all_species_rows,
        attacks_evaluated=attacks_evaluated,
        attacks_excluded=attacks_excluded,
        dispositions=dispositions,
        conditional_evaluations=conditional_evaluations,
        attacks_conditionally_evaluated=attacks_conditionally_evaluated,
        conditionally_ranked_attacks=conditionally_ranked_attacks,
        excluded_creatures=excluded_creatures,
        attacks_excluded_by_creature_scope=attacks_excluded_by_creature_scope,
        rows_with_effectiveness_field=rows_with_effectiveness_field,
        rows_with_non_neutral_effectiveness=rows_with_non_neutral_effectiveness,
        rows_conditional_because_effectiveness=rows_conditional_because_effectiveness,
        all_variant_selection_audits=all_variant_selection_audits,
        ambiguous_variant_audits=ambiguous_variant_audits,
        variant_selection_audits=variant_selection_audits,
    )
