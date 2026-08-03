"""Compatibility exports for modular Harvest evaluation.

New imports should use :mod:`blueprint_translator.harvest.evaluation`.
"""

from .harvest.evaluation import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    EVALUATION_CATALOG_SCHEMA,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    METRIC_CONTRACTS,
    METRIC_OBSERVED_PER_NODE,
    METRIC_OBSERVED_PER_SECOND,
    METRIC_STATIC_CYCLE_SPEED,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    POLICY_INCLUDE_CONDITIONAL,
    RANKING_RESULT_SCHEMA,
    TAMED_RIDDEN,
    VARIANT_ALL,
    VARIANT_BEST_DISCOVERED_EXPLORATORY,
    VARIANT_CANONICAL,
    HarvestEvaluationEngine,
    extract_creature_identity,
    find_node_and_resource,
    prepare_attack_for_usage_scope,
)
from .harvest.model.attack_evaluation import evaluate_attack_resource

__all__ = [
    "AVAILABILITY_GLOBAL_TRANSFER_ALLOWED",
    "EVALUATION_CATALOG_SCHEMA",
    "HARVEST_RANKING_CONTRACT_VERSION",
    "HARVEST_RANKING_POLICY_VERSION",
    "METRIC_CONTRACTS",
    "METRIC_OBSERVED_PER_NODE",
    "METRIC_OBSERVED_PER_SECOND",
    "METRIC_STATIC_CYCLE_SPEED",
    "METRIC_STATIC_TOTAL",
    "POLICY_CONFIRMED",
    "POLICY_INCLUDE_CONDITIONAL",
    "RANKING_RESULT_SCHEMA",
    "TAMED_RIDDEN",
    "VARIANT_ALL",
    "VARIANT_BEST_DISCOVERED_EXPLORATORY",
    "VARIANT_CANONICAL",
    "HarvestEvaluationEngine",
    "evaluate_attack_resource",
    "extract_creature_identity",
    "find_node_and_resource",
    "prepare_attack_for_usage_scope",
]
