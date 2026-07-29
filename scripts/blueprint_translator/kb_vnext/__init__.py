"""ARK Knowledge Base vNext semantic indexing primitives."""

from .adapters import ADAPTER_SPECS, materialize_semantic_adapters
from .roles import (
    DEPTH_POLICIES,
    KNOWLEDGE_ROLES,
    RoleDecision,
    classify_asset,
    enrich_type_percentiles,
    materialize_discovery_roles,
)
from .class_hierarchy import (
    inheritance_path_to_native_root,
    materialize_discovery_classes,
    rebuild_class_closure,
)
from .ontology import infer_domain_memberships, load_ontology
from .registrations import materialize_typed_registrations
from .map_usage import materialize_map_usage_edges
from .snapshot import build_vnext_snapshot
from .legacy import import_legacy_lineage
from .fact_store import (
    FactValue,
    materialize_declared_defaults,
    materialize_effective_defaults,
    store_fact,
)
from .projections import build_domain_projections
from .native_gold_set import (
    load_native_gold_set,
    materialize_native_gold_set,
)
from .invalidation import (
    InvalidationPlan,
    apply_invalidation_plan,
    plan_invalidation,
    rebuild_invalidation_dependencies,
)
from .query_planner import (
    QueryRequirements,
    plan_query,
    resolve_entities,
)
from .kb_api import KnowledgeApiError, VNextKnowledgeService
from .kb_context import build_bounded_context_pack
from .shadow_compare import (
    LegacyVNextComparator,
    query_legacy_read_only,
)
from .benchmark import (
    BENCHMARK_SCHEMA,
    TIER_COUNTS,
    build_benchmark_cases,
    materialize_benchmark_queries,
    run_query_benchmark,
)
from .quality_gates import (
    QUALITY_GATE_SCHEMA,
    evaluate_quality_gates,
    publish_gate_report,
)

__all__ = [
    "DEPTH_POLICIES",
    "ADAPTER_SPECS",
    "KNOWLEDGE_ROLES",
    "RoleDecision",
    "classify_asset",
    "enrich_type_percentiles",
    "materialize_discovery_roles",
    "materialize_semantic_adapters",
    "inheritance_path_to_native_root",
    "materialize_discovery_classes",
    "rebuild_class_closure",
    "infer_domain_memberships",
    "load_ontology",
    "materialize_typed_registrations",
    "materialize_map_usage_edges",
    "build_vnext_snapshot",
    "import_legacy_lineage",
    "FactValue",
    "materialize_declared_defaults",
    "materialize_effective_defaults",
    "store_fact",
    "build_domain_projections",
    "load_native_gold_set",
    "materialize_native_gold_set",
    "InvalidationPlan",
    "apply_invalidation_plan",
    "plan_invalidation",
    "rebuild_invalidation_dependencies",
    "QueryRequirements",
    "plan_query",
    "resolve_entities",
    "KnowledgeApiError",
    "VNextKnowledgeService",
    "build_bounded_context_pack",
    "LegacyVNextComparator",
    "query_legacy_read_only",
    "BENCHMARK_SCHEMA",
    "TIER_COUNTS",
    "build_benchmark_cases",
    "materialize_benchmark_queries",
    "run_query_benchmark",
    "QUALITY_GATE_SCHEMA",
    "evaluate_quality_gates",
    "publish_gate_report",
]
