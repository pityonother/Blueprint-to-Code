"""Runtime capability checks for versioned ARK KB Core snapshots."""

from __future__ import annotations

import sqlite3


CORE_SCHEMA_VERSION = "ark-kb-core/v3"
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
    return {
        "schemaVersion": schema_version,
        "effectiveCandidateExplanations": effective_candidates,
        "semanticAdapterDerivations": semantic_derivations,
        "compatible": (
            schema_version == CORE_SCHEMA_VERSION
            and effective_candidates
            and semantic_derivations
        ),
    }
