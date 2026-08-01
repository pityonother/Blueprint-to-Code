"""Lazy all-creature evaluation and Ranking Contract v2 projection."""

from __future__ import annotations

from collections import Counter
import math
from typing import Any, Mapping

from ...resource_nodes import canonical_package_path
from ..contracts import (
    STATIC_COMPLETE_NODE_SCORE_BASIS,
    YIELD_MODEL_VERSION,
    YIELD_SCORE_BASIS,
)
from ..model.attack_evaluation import evaluate_attack_resource
from .aggregation import (
    _canonical_variant_key,
    _enrich_v2_metrics,
    _estimated_yield,
    _metric_value,
    _override_map,
    _stable_row_identity,
    find_node_and_resource,
    prepare_attack_for_usage_scope,
)
from .contracts import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    AVAILABILITY_POLICIES,
    EVALUATION_CATALOG_SCHEMA,
    EVIDENCE_POLICIES,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    METRIC_OBSERVED_PER_NODE,
    METRIC_OBSERVED_PER_SECOND,
    METRIC_STATIC_CYCLE_SPEED,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    POLICY_INCLUDE_CONDITIONAL,
    RANKING_METRICS,
    RANKING_RESULT_SCHEMA,
    TAMED_RIDDEN,
    VARIANT_ALL,
    VARIANT_BEST_DISCOVERED_EXPLORATORY,
    VARIANT_CANONICAL,
    VARIANT_POLICIES,
)

_EVIDENCE_POLICIES = EVIDENCE_POLICIES
_VARIANT_POLICIES = VARIANT_POLICIES
_RANKING_METRICS = RANKING_METRICS
_AVAILABILITY_POLICIES = AVAILABILITY_POLICIES

class HarvestEvaluationEngine:
    """Pre-index a compact catalog and evaluate only the selected component/resource."""

    def __init__(self, catalog: dict[str, Any]):
        if catalog.get("schema") != EVALUATION_CATALOG_SCHEMA:
            raise ValueError("Harvest evaluation catalog schema is invalid.")
        components = catalog.get("components")
        creatures = catalog.get("creatures")
        if not isinstance(components, list) or not isinstance(creatures, list):
            raise ValueError("Harvest evaluation catalog facts are incomplete.")
        self.catalog = catalog
        self.components = {
            canonical_package_path(component.get("objectPath")).casefold(): component
            for component in components
            if isinstance(component, dict)
            and canonical_package_path(component.get("objectPath"))
        }
        self.creatures = [row for row in creatures if isinstance(row, dict)]
        parents = catalog.get("damageTypeParents")
        self.damage_type_parents = dict(parents) if isinstance(parents, dict) else {}
        gaps = catalog.get("damageTypeGaps")
        self.damage_type_gaps = dict(gaps) if isinstance(gaps, dict) else {}
        self.resource_damage_overrides = _override_map(
            catalog.get("resourceDamageOverrides")
        )

    def _rank_node_resource_v1(
        self,
        node_catalog: dict[str, Any],
        *,
        node_id: str,
        node_resource_id: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        node, resource = find_node_and_resource(
            node_catalog, node_id, node_resource_id
        )
        component_ref = node.get("harvestComponent")
        component_package = canonical_package_path(
            component_ref.get("packagePath") if isinstance(component_ref, dict) else ""
        )
        component = self.components.get(component_package.casefold())
        if not isinstance(component, dict):
            raise KeyError("HARVEST_COMPONENT_NOT_FOUND")

        usage_scope = str(
            self.catalog.get("methodology", {}).get("usageScope") or TAMED_RIDDEN
        )
        require_confirmed_rideability = (
            self.catalog.get("methodology", {}).get("rideabilityRequirement")
            == "B_ALLOW_RIDING_TRUE"
        )
        resource_class = str(resource.get("resource") or "")
        resource_entry_index = resource.get("entryIndex")
        considered_creatures = [
            creature
            for creature in self.creatures
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

        for creature in self.creatures:
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
                row = evaluate_attack_resource(
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
                    damage_type_parents=self.damage_type_parents,
                    resource_damage_overrides=self.resource_damage_overrides,
                    damage_type_gaps=self.damage_type_gaps,
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

        coverage = dict(self.catalog.get("coverage") or {})
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
            for value in self.catalog.get("claimBlockers", [])
            if str(value)
        ]
        evaluation_dataset = self.catalog.get("dataset")
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
                **dict(self.catalog.get("methodology") or {}),
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

    def rank_node_resource(
        self,
        node_catalog: dict[str, Any],
        *,
        node_id: str,
        node_resource_id: str,
        limit: int = 10,
        evidence_policy: str = POLICY_CONFIRMED,
        variant_policy: str = VARIANT_CANONICAL,
        metric: str = METRIC_STATIC_TOTAL,
        availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        runtime_observations: Mapping[
            tuple[str, str, str, str, int], Mapping[str, Any]
        ]
        | None = None,
    ) -> dict[str, Any]:
        """Return Ranking Contract v2 rows, split by evidence tier.

        Legacy in-memory fixtures without a v2 contract marker keep the v1
        behavior. Generated catalogs always carry the marker, so HTTP/API
        defaults are the fail-closed v2 policy.
        """

        methodology = self.catalog.get("methodology")
        contract_version = (
            str(methodology.get("contractVersion") or "")
            if isinstance(methodology, dict)
            else ""
        )
        if contract_version != HARVEST_RANKING_CONTRACT_VERSION:
            return self._rank_node_resource_v1(
                node_catalog,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=limit,
            )
        if evidence_policy not in _EVIDENCE_POLICIES:
            raise ValueError("Unsupported harvest evidence policy.")
        if variant_policy not in _VARIANT_POLICIES:
            raise ValueError("Unsupported harvest variant policy.")
        if metric not in _RANKING_METRICS:
            raise ValueError("Unsupported harvest ranking metric.")
        if availability_policy not in _AVAILABILITY_POLICIES:
            raise ValueError("Unsupported harvest availability policy.")

        node, resource = find_node_and_resource(
            node_catalog, node_id, node_resource_id
        )
        component_ref = node.get("harvestComponent")
        component_package = canonical_package_path(
            component_ref.get("packagePath")
            if isinstance(component_ref, dict)
            else ""
        )
        component = self.components.get(component_package.casefold())
        if not isinstance(component, dict):
            raise KeyError("HARVEST_COMPONENT_NOT_FOUND")
        usage_scope = str(
            self.catalog.get("methodology", {}).get("usageScope") or TAMED_RIDDEN
        )
        resource_class = str(resource.get("resource") or "")
        raw_entry_index = resource.get("entryIndex")
        resource_entry_index = (
            int(raw_entry_index)
            if isinstance(raw_entry_index, int)
            and not isinstance(raw_entry_index, bool)
            else None
        )

        grouped: dict[str, list[dict[str, Any]]] = {}
        excluded_creatures: Counter[str] = Counter()
        attacks_excluded: Counter[str] = Counter()
        dispositions: Counter[str] = Counter()
        conditional_evaluations: Counter[str] = Counter()
        attacks_conditionally_evaluated = 0
        conditionally_ranked_attacks = 0
        attacks_excluded_by_creature_scope = 0
        for creature in self.creatures:
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

        all_species_rows: list[dict[str, Any]] = []
        attacks_evaluated = 0
        for species_key, variants in grouped.items():
            canonical_creature = min(variants, key=_canonical_variant_key)
            canonical_path = str(canonical_creature.get("objectPath") or "")
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
                    row = evaluate_attack_resource(
                        creature=str(creature.get("name") or "Unknown creature"),
                        creature_object_path=str(creature.get("objectPath") or ""),
                        attack=prepared,
                        component=component,
                        resource=resource_class,
                        resource_entry_index=resource_entry_index,
                        damage_type_parents=self.damage_type_parents,
                        resource_damage_overrides=self.resource_damage_overrides,
                        damage_type_gaps=self.damage_type_gaps,
                    )
                    attacks_evaluated += 1
                    disposition = str(row.get("rankingStatus") or "UNRANKED")
                    dispositions[disposition] += 1
                    if disposition != "RANKED":
                        continue
                    _enrich_v2_metrics(row)
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
                    if runtime_observation is not None:
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
                    effectiveness = row.get("effectivenessQuantityMultiplier")
                    if (
                        isinstance(effectiveness, (int, float))
                        and not isinstance(effectiveness, bool)
                        and not math.isclose(
                            float(effectiveness), 1.0, rel_tol=0.0, abs_tol=1e-9
                        )
                    ):
                        evidence_gaps.append(
                            "EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED"
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

            variant_paths = [
                str(creature.get("objectPath") or "") for creature in variants
            ]
            for tier, variant_best_rows in variant_best_rows_by_tier.items():
                if not variant_best_rows:
                    continue
                rows_by_path = {
                    str(row.get("creatureObjectPath") or ""): row
                    for row in variant_best_rows
                }
                canonical_row = rows_by_path.get(canonical_path)
                exploratory_row = min(
                    variant_best_rows,
                    key=lambda row: (
                        -float(_metric_value(row, metric) or 0.0),
                        *_stable_row_identity(row),
                    ),
                )
                if variant_policy == VARIANT_ALL:
                    selected_rows = sorted(
                        variant_best_rows,
                        key=lambda row: (
                            _canonical_variant_key(
                                next(
                                    creature
                                    for creature in variants
                                    if str(creature.get("objectPath") or "")
                                    == str(row.get("creatureObjectPath") or "")
                                )
                            ),
                            _stable_row_identity(row),
                        ),
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
                                    creature.get("name")
                                    for creature in variants
                                    if str(creature.get("objectPath") or "") == path
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
                for selected_row in selected_rows:
                    row = dict(selected_row)
                    selected_score = _metric_value(row, metric)
                    exploratory_score = _metric_value(exploratory_row, metric)
                    selected_path = str(row.get("creatureObjectPath") or "")
                    row["variantSelection"] = {
                        "policy": variant_policy,
                        "selectedObjectPath": selected_path,
                        "canonicalObjectPath": canonical_path,
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
                    all_species_rows.append(row)

        def ranked_tier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            ordered = sorted(
                rows,
                key=lambda row: (
                    -float(_metric_value(row, metric) or 0.0),
                    *_stable_row_identity(row),
                ),
            )
            previous_score: float | None = None
            previous_rank = 0
            top_score = _metric_value(ordered[0], metric) if ordered else None
            result: list[dict[str, Any]] = []
            for ordinal, source_row in enumerate(ordered, start=1):
                row = dict(source_row)
                score = _metric_value(row, metric)
                if score is None:
                    continue
                if previous_score is None or score != previous_score:
                    previous_rank = ordinal
                    previous_score = score
                row["rank"] = previous_rank
                row["relativeToNodeTopPercent"] = (
                    round(min(100.0, max(0.0, score / top_score * 100.0)), 6)
                    if top_score is not None and top_score > 0
                    else 0.0
                )
                row["relativeBasisTier"] = row.get("rankingTier")
                result.append(row)
            return result

        confirmed_all = ranked_tier(
            [row for row in all_species_rows if row.get("rankingTier") == "CONFIRMED"]
        )
        conditional_all = ranked_tier(
            [row for row in all_species_rows if row.get("rankingTier") != "CONFIRMED"]
        )
        bounded_limit = max(1, min(int(limit), 10))
        confirmed_items = confirmed_all[:bounded_limit]
        conditional_items = (
            conditional_all[:bounded_limit]
            if evidence_policy == POLICY_INCLUDE_CONDITIONAL
            else []
        )
        compatibility_items = [*confirmed_items, *conditional_items]

        coverage = dict(self.catalog.get("coverage") or {})
        coverage.update(
            {
                "speciesEvaluated": len(grouped),
                "attacksEvaluated": attacks_evaluated,
                "attacksRanked": dispositions["RANKED"],
                "attacksUnranked": dispositions["UNRANKED"],
                "attacksIncompatible": dispositions["INCOMPATIBLE"],
                "attacksExcludedByScope": sum(attacks_excluded.values()),
                "excludedByReason": dict(sorted(attacks_excluded.items())),
                "attacksConditionallyEvaluated": attacks_conditionally_evaluated,
                "conditionallyRankedAttacks": conditionally_ranked_attacks,
                "conditionalEvaluationByReason": dict(
                    sorted(conditional_evaluations.items())
                ),
                "creatureAssetsExcludedFromScope": sum(excluded_creatures.values()),
                "attacksExcludedByCreatureScope": attacks_excluded_by_creature_scope,
                "excludedCreatureByReason": dict(sorted(excluded_creatures.items())),
                "rankedForNodeResource": len(confirmed_all) + len(conditional_all),
                "rankedSpeciesConfirmed": len(confirmed_all),
                "rankedSpeciesConditional": len(conditional_all),
                "returnedConfirmed": len(confirmed_items),
                "returnedConditional": len(conditional_items),
                "returned": len(compatibility_items),
                "omitted": max(
                    0,
                    len(confirmed_all)
                    + (len(conditional_all) if evidence_policy == POLICY_INCLUDE_CONDITIONAL else 0)
                    - len(compatibility_items),
                ),
            }
        )
        complete_scope = coverage.get("claimsAllCreatures") is True
        claim_blockers = [
            str(value) for value in self.catalog.get("claimBlockers", []) if str(value)
        ]
        evaluation_dataset = self.catalog.get("dataset")
        node_dataset = dict(node_catalog.get("dataset") or {})
        evaluation_dataset = (
            dict(evaluation_dataset) if isinstance(evaluation_dataset, dict) else {}
        )
        dataset = {
            **node_dataset,
            "evaluationRevision": evaluation_dataset.get("revision"),
            "evaluationGeneratedAt": evaluation_dataset.get("generatedAt"),
        }
        identity = {
            "extractorVersion": evaluation_dataset.get("extractorVersion"),
            "modelVersion": YIELD_MODEL_VERSION,
            "policyVersion": HARVEST_RANKING_POLICY_VERSION,
            "resultSchemaVersion": RANKING_RESULT_SCHEMA,
            "nodeCatalogRevision": node_dataset.get("revision"),
            "evaluationCatalogRevision": evaluation_dataset.get("revision"),
            "componentCatalogRevision": evaluation_dataset.get(
                "componentDatasetRevision"
            ),
        }
        metric_labels = {
            METRIC_STATIC_TOTAL: "静态单节点目标资源总产量",
            METRIC_STATIC_CYCLE_SPEED: "静态攻击周期折算产量",
            METRIC_OBSERVED_PER_NODE: "受控实测单节点目标资源产量",
            METRIC_OBSERVED_PER_SECOND: "受控实测每秒目标资源产量",
        }
        return {
            "schema": RANKING_RESULT_SCHEMA,
            "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
            "identity": identity,
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
            "queryPolicy": {
                "evidence": evidence_policy,
                "variant": variant_policy,
                "metric": metric,
                "availability": availability_policy,
                "exploratory": variant_policy
                == VARIANT_BEST_DISCOVERED_EXPLORATORY,
            },
            "methodology": {
                **dict(self.catalog.get("methodology") or {}),
                "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
                "policyVersion": HARVEST_RANKING_POLICY_VERSION,
                "formulaVersion": YIELD_MODEL_VERSION,
                "metric": metric,
                "metricLabel": metric_labels[metric],
                "scoreBasis": STATIC_COMPLETE_NODE_SCORE_BASIS,
                "firstHitTiming": "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE",
                "relativeBasis": "WITHIN_SAME_EVIDENCE_TIER_SELECTED_METRIC",
                "tiePolicy": "COMPETITION_RANK_FOR_EQUAL_SELECTED_METRIC_1_1_3",
                "variantSelection": variant_policy,
                "availabilityPolicy": availability_policy,
                "engineComparisonIndexPolicy": (
                    "COMPATIBILITY_ALIAS_EQUAL_TO_STATIC_COMPLETE_NODE_TARGET_YIELD_"
                    "NEVER_USED_FOR_ORDERING"
                ),
                "warning": (
                    "静态指标不是服务器环境下的实测产量或真实每秒产量；"
                    "条件性结果不会占用已确认榜名次或基线。"
                ),
            },
            "confirmedStatus": "AVAILABLE" if confirmed_all else "UNAVAILABLE",
            "conditionalStatus": "AVAILABLE" if conditional_all else "UNAVAILABLE",
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
            "confirmedItems": confirmed_items,
            "conditionalItems": conditional_items,
            "items": compatibility_items,
        }
