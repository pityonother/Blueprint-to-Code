"""Runtime capability checks for versioned ARK KB Core snapshots."""

from __future__ import annotations

import sqlite3


CORE_SCHEMA_VERSION = "ark-kb-core/v4"
EFFECTIVE_CANDIDATE_COLUMNS = frozenset(
    {
        "entity_id",
        "fact_type",
        "fact_name",
        "candidate_fact_id",
        "declared_on_entity_id",
        "inheritance_depth",
        "path_status",
        "selected",
        "rejection_reason",
    }
)
SEMANTIC_ADAPTER_RUN_COLUMNS = frozenset(
    {
        "adapter_id",
        "adapter_version",
        "built_at",
        "promoted_fact_count",
        "promoted_decision_count",
        "rejected_decision_count",
        "validation_status",
    }
)
SEMANTIC_ADAPTER_DECISION_COLUMNS = frozenset(
    {
        "decision_key",
        "adapter_id",
        "adapter_version",
        "rule_id",
        "source_mode",
        "object_path",
        "property_name",
        "decision_status",
        "reason_code",
        "source_fact_id",
        "semantic_fact_id",
        "legacy_lineage_id",
        "source_revision_id",
        "evidence_uri",
        "decided_at",
    }
)
MAP_USAGE_SOURCE_COLUMNS = frozenset(
    {
        "source_id",
        "source_revision_id",
        "source_uri",
        "source_schema_version",
        "status",
        "freshness_status",
        "claims_complete_map_usage",
        "claims_spawn_coordinates",
        "input_count",
        "materialized_count",
        "candidate_count",
        "rejected_count",
        "failure_reason",
        "extractor_version",
    }
)
MAP_USAGE_EVIDENCE_COLUMNS = frozenset(
    {
        "map_usage_id",
        "edge_id",
        "source_item_id",
        "evidence_layer",
        "map_family",
        "map_kind",
        "source_evidence_status",
        "usage_status",
        "freshness_status",
        "claims_complete_map_usage",
        "claims_spawn_coordinates",
        "evidence_count",
        "evidence_examples_json",
        "extractor_version",
    }
)
CONFIRMED_MAP_USAGE_VIEW_COLUMNS = frozenset(
    {
        "edge_id",
        "source_entity_id",
        "target_entity_id",
        "edge_type",
        "edge_strength",
        "status",
        "confidence",
        "source_revision_id",
        "evidence_uri",
        "map_usage_id",
        "evidence_layer",
        "map_family",
        "map_kind",
        "source_evidence_status",
        "usage_status",
        "freshness_status",
        "claims_complete_map_usage",
        "claims_spawn_coordinates",
        "evidence_count",
        "evidence_examples_json",
    }
)
CONFIRMED_MAP_USAGE_REQUIRED_SQL = (
    "fromedgesasedgejoinmap_usage_edge_evidenceasevidence"
    "onevidence.edge_id=edge.edge_id",
    "joinsource_revisionsasrevision"
    "onrevision.revision_id=edge.source_revision_id",
    "whereedge.edge_typein("
    "'map_direct_reference','map_pcg_dependency',"
    "'map_world_partition_reference')",
    "edge.statusin('confirmed','verified','resolved')",
    "edge.confidencein('high','confirmed')",
    "evidence.source_evidence_statusin("
    "'confirmed','verified','resolved')",
    "evidence.usage_statusin('confirmed','verified','resolved')",
    "evidence.freshness_status='fresh'",
    "revision.freshness_status='fresh'",
    "upper(trim(revision.source_kind))notin(",
    "upper(trim(revision.source_uri))notin(",
    "upper(trim(revision.source_fingerprint))notin(",
    "upper(trim(revision.producer_version))notin(",
    "upper(trim(revision.schema_version))notin(",
    "upper(trim(revision.generated_at))notin(",
    "edge.evidence_uriglob'registry-reference://?*'",
    "edge.evidence_uriglob'map-evidence://asset-registry/?*'",
    (
        "edge.evidence_uri"
        "glob'map-evidence://resource-node-catalog/?*'"
    ),
    "evidence.map_usage_id<>''",
    "evidence.evidence_layer<>''",
    "evidence.evidence_count>=1",
)
QUERY_PROVENANCE_COLUMNS = {
    "packages": frozenset(
        {
            "package_id",
            "package_path",
            "mount_point",
            "content_pack_id",
            "current_revision_id",
        }
    ),
    "entities": frozenset(
        {
            "entity_id",
            "canonical_uri",
            "entity_kind",
            "package_id",
            "class_id",
            "display_name",
            "internal_name",
            "status",
            "confidence",
        }
    ),
    "aliases": frozenset(
        {"alias", "entity_id", "alias_kind", "language", "confidence"}
    ),
    "knowledge_roles": frozenset(
        {
            "entity_id",
            "role",
            "confidence",
            "status",
            "reasons_json",
            "classifier_version",
            "source_revision_id",
        }
    ),
    "domain_memberships": frozenset(
        {
            "entity_id",
            "domain_id",
            "membership_kind",
            "confidence",
            "status",
            "evidence_id",
            "ontology_version",
            "source_revision_id",
        }
    ),
    "asset_class_assignments": frozenset(
        {
            "entity_id",
            "class_id",
            "assignment_kind",
            "evidence_uri",
            "status",
            "confidence",
            "source_revision_id",
        }
    ),
    "classes": frozenset(
        {
            "class_id",
            "class_path",
            "status",
            "confidence",
            "source_revision_id",
        }
    ),
    "class_edges": frozenset(
        {
            "child_class_id",
            "parent_class_id",
            "edge_kind",
            "evidence_id",
            "status",
            "confidence",
            "source_revision_id",
        }
    ),
    "class_gaps": frozenset(
        {"class_id", "gap_kind", "detail", "status"}
    ),
    "native_blueprint_links": frozenset(
        {
            "link_id",
            "blueprint_entity_id",
            "blueprint_graph_evidence_uri",
            "blueprint_function_name",
            "native_function_id",
            "native_evidence_uri",
            "resolution_method",
            "status",
            "confidence",
            "blueprint_graph_source_revision_id",
        }
    ),
    "native_functions": frozenset(
        {
            "native_function_id",
            "canonical_uri",
            "qualified_symbol",
            "module_name",
            "rva",
            "signature",
            "binary_sha256",
            "pdb_sha256",
            "pdb_guid_age",
            "recipe_ids_json",
            "evidence_set_ids_json",
            "caller_count",
            "callee_count",
            "callsite_status",
            "status",
            "confidence",
            "source_revision_id",
        }
    ),
    "edges": frozenset(
        {
            "edge_id",
            "source_entity_id",
            "target_entity_id",
            "edge_type",
            "edge_strength",
            "status",
            "confidence",
            "source_revision_id",
            "evidence_uri",
            "source_property",
            "source_graph",
        }
    ),
    "facts": frozenset(
        {
            "fact_id",
            "subject_entity_id",
            "fact_type",
            "fact_name",
            "scope_kind",
            "declared_on_entity_id",
            "value_kind",
            "value_text",
            "value_number",
            "value_integer",
            "value_json",
            "unit",
            "status",
            "confidence",
            "ontology_version",
            "current",
            "canonical_fact_key",
        }
    ),
    "effective_facts": frozenset(
        {
            "entity_id",
            "fact_type",
            "fact_name",
            "fact_id",
            "inherited_from_entity_id",
            "resolution_chain_json",
            "resolution_status",
            "source_revision_set_hash",
        }
    ),
    "fact_evidence": frozenset(
        {
            "fact_id",
            "source_revision_id",
            "evidence_uri",
            "evidence_role",
        }
    ),
    "source_revisions": frozenset(
        {
            "revision_id",
            "source_kind",
            "source_uri",
            "source_fingerprint",
            "producer_version",
            "schema_version",
            "generated_at",
            "freshness_status",
        }
    ),
}


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> frozenset[str]:
    return frozenset(
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        )
    )


def _normalized_schema_sql(value: object) -> str:
    normalized = "".join(str(value or "").split()).lower()
    return (
        normalized.replace('"', "")
        .replace("`", "")
        .replace("[", "")
        .replace("]", "")
        .rstrip(";")
    )


def supports_effective_candidate_explanations(
    connection: sqlite3.Connection,
) -> bool:
    """Return whether the connected Core can serve v2 candidate lineage."""

    columns = _table_columns(connection, "effective_fact_candidates")
    return EFFECTIVE_CANDIDATE_COLUMNS.issubset(columns)


def supports_semantic_adapter_derivations(
    connection: sqlite3.Connection,
) -> bool:
    """Return whether Core has the v3 semantic ownership contract."""

    return (
        SEMANTIC_ADAPTER_RUN_COLUMNS.issubset(
            _table_columns(connection, "semantic_adapter_runs")
        )
        and SEMANTIC_ADAPTER_DECISION_COLUMNS.issubset(
            _table_columns(connection, "semantic_adapter_decisions")
        )
    )


def supports_typed_map_usage_evidence(
    connection: sqlite3.Connection,
) -> bool:
    """Return whether Core has the v4 confirmed-only map contract."""

    view_row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type='view' AND name='confirmed_map_usage_edges'
        """
    ).fetchone()
    view_sql = (
        _normalized_schema_sql(view_row[0])
        if view_row is not None
        else ""
    )
    return (
        MAP_USAGE_SOURCE_COLUMNS.issubset(
            _table_columns(connection, "map_usage_sources")
        )
        and MAP_USAGE_EVIDENCE_COLUMNS.issubset(
            _table_columns(connection, "map_usage_edge_evidence")
        )
        and view_row is not None
        and CONFIRMED_MAP_USAGE_VIEW_COLUMNS.issubset(
            _table_columns(connection, "confirmed_map_usage_edges")
        )
        and all(
            fragment in view_sql
            for fragment in CONFIRMED_MAP_USAGE_REQUIRED_SQL
        )
    )


def supports_query_provenance(
    connection: sqlite3.Connection,
) -> bool:
    """Return whether identity and semantic read paths expose lineage."""

    return all(
        required.issubset(_table_columns(connection, table_name))
        for table_name, required in QUERY_PROVENANCE_COLUMNS.items()
    )


def core_schema_capabilities(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    """Read the version and capabilities needed by current read paths."""

    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError:
        row = None
    schema_version = str(row[0]) if row is not None else ""
    effective_candidates = supports_effective_candidate_explanations(
        connection
    )
    semantic_derivations = supports_semantic_adapter_derivations(connection)
    typed_map_usage = supports_typed_map_usage_evidence(connection)
    query_provenance = supports_query_provenance(connection)
    return {
        "schemaVersion": schema_version,
        "effectiveCandidateExplanations": effective_candidates,
        "semanticAdapterDerivations": semantic_derivations,
        "typedMapUsageEvidence": typed_map_usage,
        "queryProvenance": query_provenance,
        "compatible": (
            schema_version == CORE_SCHEMA_VERSION
            and effective_candidates
            and semantic_derivations
            and typed_map_usage
            and query_provenance
        ),
    }
