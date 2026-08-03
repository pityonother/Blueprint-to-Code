"""Ranking Contract v2 orchestration and response projection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Iterable, Mapping

from ...resource_nodes import canonical_package_path
from ..contracts import YIELD_MODEL_VERSION
from .aggregation import find_node_and_resource
from .contracts import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    AVAILABILITY_POLICIES,
    EVIDENCE_POLICIES,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    METRIC_CONTRACTS,
    METRIC_OBSERVED_PER_NODE,
    METRIC_OBSERVED_PER_SECOND,
    METRIC_STATIC_CYCLE_SPEED,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    RANKING_METRICS,
    RANKING_RESULT_SCHEMA,
    TAMED_RIDDEN,
    VARIANT_BEST_DISCOVERED_EXPLORATORY,
    VARIANT_CANONICAL,
    VARIANT_POLICIES,
)
from .runtime import _runtime_profile_context
from .species_evaluation import evaluate_species_catalog
from .tier_projection import project_tiers

_EVIDENCE_POLICIES = EVIDENCE_POLICIES
_VARIANT_POLICIES = VARIANT_POLICIES
_RANKING_METRICS = RANKING_METRICS
_AVAILABILITY_POLICIES = AVAILABILITY_POLICIES


def rank_node_resource_v2(
    engine: Any,
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
    runtime_profile_id: str | None = None,
    include_preliminary: bool = False,
    runtime_profiles_available: Iterable[str] | None = None,
    evaluate_attack: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Return Ranking Contract v2 rows, split by evidence tier.

    Legacy in-memory fixtures without a v2 contract marker keep the v1
    behavior. Generated catalogs always carry the marker, so HTTP/API
    defaults are the fail-closed v2 policy.
    """

    methodology = engine.catalog.get("methodology")
    contract_version = (
        str(methodology.get("contractVersion") or "")
        if isinstance(methodology, dict)
        else ""
    )
    if contract_version != HARVEST_RANKING_CONTRACT_VERSION:
        return engine._rank_node_resource_v1(
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
    runtime_profile_id, runtime_coverage = _runtime_profile_context(
        runtime_observations,
        requested_profile_id=runtime_profile_id,
        runtime_metric=METRIC_CONTRACTS[metric]["runtime"] is True,
        validated_profiles_available=runtime_profiles_available,
    )

    node, resource = find_node_and_resource(
        node_catalog, node_id, node_resource_id
    )
    component_ref = node.get("harvestComponent")
    component_package = canonical_package_path(
        component_ref.get("packagePath")
        if isinstance(component_ref, dict)
        else ""
    )
    component = engine.components.get(component_package.casefold())
    if not isinstance(component, dict):
        raise KeyError("HARVEST_COMPONENT_NOT_FOUND")
    usage_scope = str(
        engine.catalog.get("methodology", {}).get("usageScope") or TAMED_RIDDEN
    )
    resource_class = str(resource.get("resource") or "")
    raw_entry_index = resource.get("entryIndex")
    resource_entry_index = (
        int(raw_entry_index)
        if isinstance(raw_entry_index, int)
        and not isinstance(raw_entry_index, bool)
        else None
    )

    species_evaluation = evaluate_species_catalog(
        engine,
        component=component,
        usage_scope=usage_scope,
        resource_class=resource_class,
        resource_entry_index=resource_entry_index,
        node_id=node_id,
        node_resource_id=node_resource_id,
        metric=metric,
        variant_policy=variant_policy,
        runtime_observations=runtime_observations,
        runtime_profile_id=runtime_profile_id,
        include_preliminary=include_preliminary,
        evaluate_attack=evaluate_attack,
    )
    grouped = species_evaluation.grouped
    all_species_rows = species_evaluation.rows
    attacks_evaluated = species_evaluation.attacks_evaluated
    attacks_excluded = species_evaluation.attacks_excluded
    dispositions = species_evaluation.dispositions
    conditional_evaluations = species_evaluation.conditional_evaluations
    attacks_conditionally_evaluated = (
        species_evaluation.attacks_conditionally_evaluated
    )
    conditionally_ranked_attacks = species_evaluation.conditionally_ranked_attacks
    excluded_creatures = species_evaluation.excluded_creatures
    attacks_excluded_by_creature_scope = (
        species_evaluation.attacks_excluded_by_creature_scope
    )
    rows_with_effectiveness_field = (
        species_evaluation.rows_with_effectiveness_field
    )
    rows_with_non_neutral_effectiveness = (
        species_evaluation.rows_with_non_neutral_effectiveness
    )
    rows_conditional_because_effectiveness = (
        species_evaluation.rows_conditional_because_effectiveness
    )
    all_variant_selection_audits = (
        species_evaluation.all_variant_selection_audits
    )
    ambiguous_variant_audits = species_evaluation.ambiguous_variant_audits
    variant_selection_audits = species_evaluation.variant_selection_audits

    tiers = project_tiers(
        all_species_rows,
        metric=metric,
        evidence_policy=evidence_policy,
        limit=limit,
    )
    confirmed_all = tiers.confirmed_all
    conditional_all = tiers.conditional_all
    confirmed_items = tiers.confirmed_items
    conditional_items = tiers.conditional_items
    compatibility_items = tiers.compatibility_items

    coverage = dict(engine.catalog.get("coverage") or {})
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
            "rowsWithEffectivenessField": rows_with_effectiveness_field,
            "rowsWithNonNeutralEffectiveness": (
                rows_with_non_neutral_effectiveness
            ),
            "rowsConditionalBecauseEffectiveness": (
                rows_conditional_because_effectiveness
            ),
            "canonicalVariantAmbiguousSpecies": len(
                ambiguous_variant_audits
            ),
            "canonicalCreatureAssetsAudited": len(engine.creatures),
            "canonicalVariantsAudited": len(all_variant_selection_audits),
            "variantSelectionAuditsReturned": len(variant_selection_audits),
            "variantSelectionAuditsOmitted": max(
                0,
                len(all_variant_selection_audits)
                - len(variant_selection_audits),
            ),
            "canonicalVariantAmbiguityExamples": [
                deepcopy(audit) for audit in ambiguous_variant_audits[:10]
            ],
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
                len(confirmed_all) - len(compatibility_items),
            ),
        }
    )
    complete_scope = coverage.get("claimsAllCreatures") is True
    claim_blockers = [
        str(value) for value in engine.catalog.get("claimBlockers", []) if str(value)
    ]
    evaluation_dataset = engine.catalog.get("dataset")
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
    metric_contract = METRIC_CONTRACTS[metric]
    metric_warning = (
        "实测指标仅可在所选 runtimeProfileId 环境内比较；preliminary 仍为条件性"
        "结果，synthetic 永不进入可发布排行。"
        if metric_contract["runtime"] is True
        else (
            "静态模型指标不是服务器环境下的实测产量或真实每秒产量；条件性结果"
            "不会占用已确认榜名次或基线。"
        )
    )
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
            "runtimeProfileId": runtime_profile_id,
            "includePreliminary": bool(include_preliminary),
            "exploratory": variant_policy
            == VARIANT_BEST_DISCOVERED_EXPLORATORY,
        },
        "methodology": {
            **dict(engine.catalog.get("methodology") or {}),
            "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
            "policyVersion": HARVEST_RANKING_POLICY_VERSION,
            "formulaVersion": YIELD_MODEL_VERSION,
            "metric": metric,
            "metricLabel": metric_labels[metric],
            "scoreBasis": metric_contract["scoreBasis"],
            "unit": metric_contract["unit"],
            "runtime": metric_contract["runtime"],
            "firstHitTiming": "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE",
            "relativeBasis": "WITHIN_SAME_EVIDENCE_TIER_SELECTED_METRIC",
            "tiePolicy": "COMPETITION_RANK_FOR_EQUAL_SELECTED_METRIC_1_1_3",
            "variantSelection": variant_policy,
            "availabilityPolicy": availability_policy,
            "engineComparisonIndexPolicy": (
                "COMPATIBILITY_ALIAS_EQUAL_TO_STATIC_COMPLETE_NODE_TARGET_YIELD_"
                "NEVER_USED_FOR_ORDERING"
            ),
            "warning": metric_warning,
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
        "runtimeCoverage": runtime_coverage,
        "variantSelectionAudits": variant_selection_audits,
        "confirmedItems": confirmed_items,
        "conditionalItems": conditional_items,
        "items": compatibility_items,
    }
