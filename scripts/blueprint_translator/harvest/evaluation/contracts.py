"""Ranking Contract v2 schemas, policies, and metric identities."""

EVALUATION_CATALOG_SCHEMA = "ark-harvest-evaluation-catalog/v2"
RANKING_RESULT_SCHEMA = "blueprint-to-code.harvest-ranking-result/v4"
TAMED_RIDDEN = "TAMED_RIDDEN"
HARVEST_RANKING_CONTRACT_VERSION = "harvest-ranking-contract/v2"
HARVEST_RANKING_POLICY_VERSION = (
    "harvest-ranking-policy/v2-confirmed-canonical-relative-specialty"
)

POLICY_CONFIRMED = "confirmed"
POLICY_INCLUDE_CONDITIONAL = "includeConditional"
VARIANT_CANONICAL = "CANONICAL_VARIANT"
VARIANT_ALL = "ALL_VARIANTS"
VARIANT_BEST_DISCOVERED_EXPLORATORY = "BEST_DISCOVERED_VARIANT_EXPLORATORY"
METRIC_STATIC_TOTAL = "staticCompleteNodeTargetYield"
METRIC_STATIC_CYCLE_SPEED = "staticYieldPerAttackCycleSecond"
METRIC_OBSERVED_PER_NODE = "observedYieldPerNode"
METRIC_OBSERVED_PER_SECOND = "observedYieldPerSecond"
AVAILABILITY_GLOBAL_TRANSFER_ALLOWED = "GLOBAL_TRANSFER_ALLOWED"

METRIC_CONTRACTS: dict[str, dict[str, object]] = {
    METRIC_STATIC_TOTAL: {
        "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
        "unit": "target_resource_units/node",
        "runtime": False,
    },
    METRIC_STATIC_CYCLE_SPEED: {
        "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_ATTACK_CYCLE_SECOND",
        "unit": "target_resource_units/attack_cycle_second",
        "runtime": False,
    },
    METRIC_OBSERVED_PER_NODE: {
        "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
        "unit": "target_resource_units/node",
        "runtime": True,
    },
    METRIC_OBSERVED_PER_SECOND: {
        "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND",
        "unit": "target_resource_units/second",
        "runtime": True,
    },
}

EVIDENCE_POLICIES = {POLICY_CONFIRMED, POLICY_INCLUDE_CONDITIONAL}
VARIANT_POLICIES = {
    VARIANT_CANONICAL,
    VARIANT_ALL,
    VARIANT_BEST_DISCOVERED_EXPLORATORY,
}
RANKING_METRICS = set(METRIC_CONTRACTS)
AVAILABILITY_POLICIES = {AVAILABILITY_GLOBAL_TRANSFER_ALLOWED}
