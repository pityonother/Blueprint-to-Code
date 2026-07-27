"""ARK Knowledge Base vNext semantic indexing primitives."""

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
from .snapshot import build_vnext_snapshot

__all__ = [
    "DEPTH_POLICIES",
    "KNOWLEDGE_ROLES",
    "RoleDecision",
    "classify_asset",
    "enrich_type_percentiles",
    "materialize_discovery_roles",
    "inheritance_path_to_native_root",
    "materialize_discovery_classes",
    "rebuild_class_closure",
    "infer_domain_memberships",
    "load_ontology",
    "materialize_typed_registrations",
    "build_vnext_snapshot",
]
