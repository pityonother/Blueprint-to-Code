"""Disposable domain read models derived exclusively from Core facts."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


PROJECTION_SCHEMA_VERSION = "ark-kb-domain-projection/v1"
DOMAIN_PROJECTIONS: dict[str, tuple[str, ...]] = {
    "buff_effects": ("STATUS_EFFECT",),
    "loot_entries": ("LOOT_ENTRY",),
    "item_properties": ("ITEM_PROPERTY",),
    "status_values": ("STATUS_EFFECT",),
    "harvest_rules": ("HARVEST_RULE",),
    "mission_rewards": ("MISSION_REWARD",),
}

PROJECTION_SCHEMA_SQL = """
CREATE TABLE metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE projection_rows(
    projection_row_id INTEGER PRIMARY KEY,
    fact_id INTEGER UNIQUE NOT NULL,
    entity_id INTEGER NOT NULL,
    canonical_uri TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    fact_name TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    value_kind TEXT NOT NULL,
    value_text TEXT,
    value_number REAL,
    value_integer INTEGER,
    value_json TEXT,
    unit TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    source_revision_set_hash TEXT NOT NULL
);

CREATE INDEX idx_projection_entity
    ON projection_rows(entity_id, fact_type, fact_name);
CREATE INDEX idx_projection_status
    ON projection_rows(status, confidence, entity_id);
"""


def _revision_hash(revisions: list[int]) -> str:
    payload = json.dumps(sorted(set(revisions)), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_domain_projections(
    *,
    core_path: Path,
    output_dir: Path,
    generated_at: str,
    ontology_version: str,
) -> dict[str, dict[str, object]]:
    """Build deterministic read models; Core remains the only truth source."""

    core = sqlite3.connect(core_path)
    core.row_factory = sqlite3.Row
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    results: dict[str, dict[str, object]] = {}
    try:
        core.execute("DELETE FROM projection_runs")
        for projection_name, fact_types in DOMAIN_PROJECTIONS.items():
            placeholders = ",".join("?" for _ in fact_types)
            rows = list(
                core.execute(
                    f"""
                    SELECT
                        f.fact_id, f.subject_entity_id AS entity_id,
                        entity.canonical_uri, f.fact_type, f.fact_name,
                        f.scope_kind, f.value_kind, f.value_text,
                        f.value_number, f.value_integer, f.value_json,
                        f.unit, f.status, f.confidence,
                        COUNT(e.evidence_uri) AS evidence_count
                    FROM facts AS f
                    JOIN entities AS entity
                      ON entity.entity_id=f.subject_entity_id
                    JOIN fact_evidence AS e ON e.fact_id=f.fact_id
                    WHERE f.current=1
                      AND f.fact_type IN ({placeholders})
                    GROUP BY f.fact_id
                    ORDER BY f.fact_id
                    """,
                    fact_types,
                )
            )
            revision_rows = [
                int(row[0])
                for row in core.execute(
                    f"""
                    SELECT DISTINCT e.source_revision_id
                    FROM fact_evidence AS e
                    JOIN facts AS f ON f.fact_id=e.fact_id
                    WHERE f.current=1
                      AND f.fact_type IN ({placeholders})
                    ORDER BY e.source_revision_id
                    """,
                    fact_types,
                )
            ]
            revision_set_hash = _revision_hash(revision_rows)
            path = output_dir / f"{projection_name}.sqlite"
            projection = sqlite3.connect(path)
            try:
                projection.executescript(PROJECTION_SCHEMA_SQL)
                projection.executemany(
                    "INSERT INTO metadata VALUES (?, ?)",
                    [
                        ("schema_version", PROJECTION_SCHEMA_VERSION),
                        ("projection_name", projection_name),
                        ("projection_version", "v1"),
                        ("source_revision_set_hash", revision_set_hash),
                        ("ontology_version", ontology_version),
                        ("built_at", generated_at),
                        ("truth_source", "core.sqlite"),
                    ],
                )
                for row in rows:
                    revisions = [
                        int(value[0])
                        for value in core.execute(
                            """
                            SELECT source_revision_id FROM fact_evidence
                            WHERE fact_id=?
                            ORDER BY source_revision_id
                            """,
                            (int(row["fact_id"]),),
                        )
                    ]
                    projection.execute(
                        """
                        INSERT INTO projection_rows(
                            fact_id, entity_id, canonical_uri, fact_type,
                            fact_name, scope_kind, value_kind, value_text,
                            value_number, value_integer, value_json, unit,
                            status, confidence, evidence_count,
                            source_revision_set_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            *tuple(row),
                            _revision_hash(revisions),
                        ),
                    )
                projection.execute("ANALYZE main")
                projection.commit()
                integrity = str(
                    projection.execute("PRAGMA integrity_check").fetchone()[0]
                )
            finally:
                projection.close()
            validation_status = (
                "VALID" if integrity == "ok" else "INVALID"
            )
            core.execute(
                """
                INSERT INTO projection_runs(
                    projection_name, projection_version,
                    source_revision_set_hash, ontology_version, built_at,
                    row_count, validation_status
                ) VALUES (?, 'v1', ?, ?, ?, ?, ?)
                """,
                (
                    projection_name,
                    revision_set_hash,
                    ontology_version,
                    generated_at,
                    len(rows),
                    validation_status,
                ),
            )
            results[projection_name] = {
                "path": path.name,
                "rows": len(rows),
                "integrity": integrity,
                "sourceRevisionSetHash": revision_set_hash,
                "validationStatus": validation_status,
            }
        core.commit()
        return results
    finally:
        core.close()
