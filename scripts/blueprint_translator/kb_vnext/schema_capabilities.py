"""Runtime capability checks for versioned ARK KB Core snapshots."""

from __future__ import annotations

import sqlite3


CORE_SCHEMA_VERSION = "ark-kb-core/v2"
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


def supports_effective_candidate_explanations(
    connection: sqlite3.Connection,
) -> bool:
    """Return whether the connected Core can serve v2 candidate lineage."""

    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(effective_fact_candidates)"
        )
    }
    return EFFECTIVE_CANDIDATE_COLUMNS.issubset(columns)


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
    return {
        "schemaVersion": schema_version,
        "effectiveCandidateExplanations": effective_candidates,
        "compatible": (
            schema_version == CORE_SCHEMA_VERSION
            and effective_candidates
        ),
    }
