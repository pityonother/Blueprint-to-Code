"""Canonical cutover gate contract shared by evaluation and publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


QUALITY_GATE_SCHEMA = "ark-kb-quality-gates/v1"
BENCHMARK_SCHEMA = "ark-kb-query-benchmark/v2"


QUALITY_GATE_CONTRACT = frozenset(
    {
        ("domains.source_revision_provenance", "domains", True),
        ("facts.canonical_deduplicated", "facts", True),
        ("facts.declared_effective_separated", "facts", True),
        ("facts.effective_candidate_consistency", "facts", True),
        ("facts.effective_resolution_reality", "facts", True),
        ("facts.effective_usable_value_rate", "facts", True),
        ("facts.provenance_complete", "facts", True),
        ("facts.semantic_fresh_evidence", "facts", True),
        ("facts.semantic_nonzero", "facts", True),
        ("facts.unknown_not_zero", "facts", True),
        ("facts.usable_value_rate", "facts", True),
        ("identity.blueprint_asset_class_path", "identity", True),
        ("identity.data_asset_ancestry_model", "identity", True),
        ("identity.deep_parent_native_closure", "identity", True),
        ("identity.package_revision_provenance", "identity", True),
        ("incremental.dependency_graph", "incremental", True),
        ("maps.confirmed_view_integrity", "maps", True),
        ("maps.no_domain_membership_substitution", "maps", True),
        ("maps.typed_usage_capability", "maps", True),
        ("maps.typed_usage_nonzero", "maps", True),
        ("native.blueprint_link_precision", "native", True),
        ("native.gold_targets_resolved", "native", True),
        ("performance.large_query_indexed", "performance", True),
        ("privacy.no_local_paths", "privacy", True),
        (
            "projections.buff_effects.semantic_ready",
            "projections",
            True,
        ),
        (
            "projections.harvest_rules.semantic_ready",
            "projections",
            True,
        ),
        (
            "projections.item_properties.semantic_ready",
            "projections",
            True,
        ),
        (
            "projections.loot_entries.semantic_ready",
            "projections",
            True,
        ),
        (
            "projections.mission_rewards.semantic_ready",
            "projections",
            True,
        ),
        (
            "projections.status_values.semantic_ready",
            "projections",
            True,
        ),
        ("queries.cache_build_rejected", "performance", True),
        ("queries.cache_expired_rejected", "performance", True),
        ("queries.cache_hit_p95_ms", "performance", True),
        (
            "queries.cache_invalidation_token_rejected",
            "performance",
            True,
        ),
        (
            "queries.cache_source_revision_rejected",
            "performance",
            True,
        ),
        ("queries.cache_valid_hit", "performance", True),
        ("queries.context_budget", "performance", True),
        ("queries.corpus_ready_for_cutover", "queries", True),
        ("queries.degree_cohorts_covered", "performance", True),
        (
            "queries.degree_stratified_two_hop_p95_ms",
            "performance",
            True,
        ),
        ("queries.evidence_backed_complete", "queries", True),
        ("queries.expected_gap_match", "queries", True),
        ("queries.fixed_gold_cases", "queries", True),
        ("queries.human_gold_cases", "queries", True),
        ("queries.identity_not_semantic", "queries", True),
        ("queries.no_candidate_edge_completion", "queries", True),
        ("queries.no_stale_leaks", "queries", True),
        ("queries.no_unexpected_ambiguous_answers", "queries", True),
        ("queries.no_wrong_answers", "queries", True),
        ("queries.one_hop_p95_ms", "performance", True),
        ("queries.protocol_compliance", "queries", True),
        ("queries.search_fts_plan_used", "performance", True),
        ("queries.search_fuzzy_p95_ms", "performance", True),
        ("queries.semantic_exact_match", "queries", True),
        ("queries.single_entity_p95_ms", "performance", True),
        ("queries.storage_paths_covered", "queries", True),
        ("queries.two_hop_p95_ms", "performance", True),
        ("queries.usable_value_answer", "queries", True),
        (
            "registrations.classification_precision",
            "registrations",
            True,
        ),
        (
            "registrations.classification_recall",
            "registrations",
            True,
        ),
        (
            "registrations.edge_materialization",
            "registrations",
            True,
        ),
        (
            "registrations.evidence_correctness",
            "registrations",
            True,
        ),
        ("registrations.gold_precision", "registrations", True),
        ("registrations.gold_recall", "registrations", True),
        ("registrations.lineage_complete", "registrations", True),
        (
            "registrations.noncomplete_high_confidence",
            "registrations",
            True,
        ),
        ("registrations.owner_resolution", "registrations", True),
        (
            "registrations.real_relationship_gold_count",
            "registrations",
            True,
        ),
        ("registrations.target_resolution", "registrations", True),
        ("roles.explainable", "roles", True),
        ("roles.independent_gold_set", "roles", True),
        ("roles.source_revision_provenance", "roles", True),
        ("roles.visual_false_promotion", "roles", True),
        ("storage.core_smaller_than_discovery", "storage", True),
        ("storage.integrity", "storage", True),
    }
)


def validate_quality_gate_contract(
    gates: Sequence[Mapping[str, object]],
) -> None:
    """Reject missing, substituted, duplicated, or weakened gate contracts."""

    observed = [
        (
            str(gate.get("id") or ""),
            str(gate.get("category") or ""),
            gate.get("critical"),
        )
        for gate in gates
    ]
    if (
        len(observed) != len(QUALITY_GATE_CONTRACT)
        or len(set(observed)) != len(observed)
        or frozenset(observed) != QUALITY_GATE_CONTRACT
    ):
        raise ValueError(
            "quality gate contract is incomplete, substituted, or weakened"
        )


__all__ = [
    "BENCHMARK_SCHEMA",
    "QUALITY_GATE_CONTRACT",
    "QUALITY_GATE_SCHEMA",
    "validate_quality_gate_contract",
]
