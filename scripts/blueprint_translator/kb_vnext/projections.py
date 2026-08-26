"""Disposable, evidence-complete domain read models derived from Core facts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from .adapters import ADAPTER_SPECS


PROJECTION_SCHEMA_VERSION = "ark-kb-domain-projection/v2"
PROJECTION_REVIEW_SCHEMA = "ark-kb-projection-review/v1"
PROJECTION_CONTENT_DIGEST_SCHEMA = (
    "ark-kb-domain-projection-content/v1"
)
DOMAIN_PROJECTIONS: dict[str, tuple[str, ...]] = {
    "buff_effects": ("STATUS_EFFECT",),
    "loot_entries": ("LOOT_ENTRY",),
    "item_properties": ("ITEM_PROPERTY",),
    "status_values": ("STATUS_VALUE",),
    "harvest_rules": ("HARVEST_RULE",),
    "mission_rewards": ("MISSION_REWARD",),
}

PROJECTION_SCHEMA_SQL = """
PRAGMA foreign_keys=ON;

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
    ontology_version TEXT NOT NULL,
    completeness_status TEXT NOT NULL
        CHECK(completeness_status IN ('COMPLETE', 'PARTIAL')),
    evidence_count INTEGER NOT NULL,
    source_revision_set_hash TEXT NOT NULL
);

CREATE TABLE projection_evidence(
    fact_id INTEGER NOT NULL,
    source_revision_id INTEGER NOT NULL,
    evidence_uri TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    freshness_status TEXT NOT NULL,
    PRIMARY KEY(fact_id, source_revision_id, evidence_uri),
    FOREIGN KEY(fact_id) REFERENCES projection_rows(fact_id)
) WITHOUT ROWID;

CREATE TABLE projection_lineage(
    decision_key TEXT PRIMARY KEY,
    fact_id INTEGER NOT NULL,
    source_fact_id INTEGER NOT NULL,
    legacy_lineage_id INTEGER,
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    FOREIGN KEY(fact_id) REFERENCES projection_rows(fact_id)
) WITHOUT ROWID;

CREATE TABLE projection_reviews(
    review_id TEXT PRIMARY KEY,
    fact_id INTEGER NOT NULL,
    review_status TEXT NOT NULL,
    evidence_uri TEXT NOT NULL,
    review_version TEXT NOT NULL,
    FOREIGN KEY(fact_id) REFERENCES projection_rows(fact_id)
) WITHOUT ROWID;

CREATE INDEX idx_projection_entity
    ON projection_rows(entity_id, fact_type, fact_name);
CREATE INDEX idx_projection_status
    ON projection_rows(status, confidence, entity_id);
CREATE INDEX idx_projection_evidence_uri
    ON projection_evidence(evidence_uri, fact_id);
CREATE INDEX idx_projection_lineage_source
    ON projection_lineage(source_fact_id, legacy_lineage_id);
"""

_USABLE_FACT_PREDICATE = """
(
    UPPER(fact.status) IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
    AND (
        (
            UPPER(fact.value_kind)='TEXT'
            AND TYPEOF(fact.value_text)='text'
            AND fact.value_number IS NULL
            AND fact.value_integer IS NULL
            AND fact.value_json IS NULL
        )
        OR (
            UPPER(fact.value_kind)='NUMBER'
            AND TYPEOF(fact.value_number) IN ('integer', 'real')
            AND ABS(fact.value_number)<=1.7976931348623157e308
            AND fact.value_text IS NULL
            AND fact.value_integer IS NULL
            AND fact.value_json IS NULL
        )
        OR (
            UPPER(fact.value_kind)='ENTITY_REF'
            AND TYPEOF(fact.value_text)='text'
            AND SUBSTR(fact.value_text, 1, 1)='/'
            AND fact.value_number IS NULL
            AND fact.value_integer IS NULL
            AND fact.value_json IS NULL
        )
        OR (
            UPPER(fact.value_kind)='INTEGER'
            AND TYPEOF(fact.value_integer)='integer'
            AND fact.value_text IS NULL
            AND fact.value_number IS NULL
            AND fact.value_json IS NULL
        )
        OR (
            UPPER(fact.value_kind)='BOOLEAN'
            AND TYPEOF(fact.value_integer)='integer'
            AND fact.value_integer IN (0, 1)
            AND fact.value_text IS NULL
            AND fact.value_number IS NULL
            AND fact.value_json IS NULL
        )
        OR (
            UPPER(fact.value_kind)='JSON'
            AND TYPEOF(fact.value_json)='text'
            AND JSON_VALID(fact.value_json)=1
            AND fact.value_text IS NULL
            AND fact.value_number IS NULL
            AND fact.value_integer IS NULL
        )
    )
)
"""

CURRENT_ADAPTER_RULE_REGISTRY = frozenset(
    (
        spec.adapter_id,
        spec.adapter_version,
        rule.rule_id,
        rule.output_fact_type,
        source_mode,
    )
    for spec in ADAPTER_SPECS
    for source_mode, rule in (
        *(
            ("LEGACY_TABLE", rule)
            for source in spec.legacy_sources
            for rule in source.rules
        ),
        *(
            ("CORE_TYPED_FACT", rule)
            for rule in spec.direct_rules
        ),
    )
    if rule.output_fact_type in spec.output_fact_types
)


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_CURRENT_ADAPTER_RULE_SQL = " OR ".join(
    (
        "(decision.adapter_id="
        f"{_sql_text(adapter_id)} "
        "AND decision.adapter_version="
        f"{_sql_text(adapter_version)} "
        "AND decision.rule_id="
        f"{_sql_text(rule_id)} "
        "AND fact.fact_type="
        f"{_sql_text(output_fact_type)} "
        "AND decision.source_mode="
        f"{_sql_text(source_mode)} "
        "AND (decision.source_mode<>'LEGACY_TABLE' "
        "OR decision.legacy_lineage_id IS NOT NULL))"
    )
    for (
        adapter_id,
        adapter_version,
        rule_id,
        output_fact_type,
        source_mode,
    ) in sorted(CURRENT_ADAPTER_RULE_REGISTRY)
)
if not _CURRENT_ADAPTER_RULE_SQL:
    raise RuntimeError("Semantic adapter rule registry is empty")


ADAPTER_OWNED_SEMANTIC_FACT_PREDICATE = """
EXISTS (
    SELECT 1
    FROM semantic_adapter_decisions AS decision
    JOIN semantic_adapter_runs AS adapter_run
      ON adapter_run.adapter_id=decision.adapter_id
     AND adapter_run.adapter_version=decision.adapter_version
    WHERE decision.semantic_fact_id=fact.fact_id
      AND decision.decision_status='PROMOTED'
      AND decision.reason_code IN ('VERIFIED', 'VERIFIED_PARTIAL')
      AND UPPER(adapter_run.validation_status)='VALID'
      AND ({current_adapter_rule_sql})
)
""".format(current_adapter_rule_sql=_CURRENT_ADAPTER_RULE_SQL)


ACTIVE_PROMOTED_DERIVATION_PREDICATE = """
EXISTS (
    SELECT 1
    FROM semantic_adapter_decisions AS decision
    JOIN semantic_adapter_runs AS adapter_run
      ON adapter_run.adapter_id=decision.adapter_id
     AND adapter_run.adapter_version=decision.adapter_version
    JOIN facts AS source_fact
      ON source_fact.fact_id=decision.source_fact_id
    JOIN fact_evidence AS source_evidence
      ON source_evidence.fact_id=source_fact.fact_id
     AND source_evidence.source_revision_id=decision.source_revision_id
     AND source_evidence.evidence_uri=decision.evidence_uri
    JOIN fact_evidence AS semantic_evidence
      ON semantic_evidence.fact_id=fact.fact_id
     AND semantic_evidence.source_revision_id=decision.source_revision_id
     AND semantic_evidence.evidence_uri=decision.evidence_uri
    JOIN source_revisions AS source_revision
      ON source_revision.revision_id=decision.source_revision_id
    WHERE decision.semantic_fact_id=fact.fact_id
      AND decision.decision_status='PROMOTED'
      AND decision.reason_code IN ('VERIFIED', 'VERIFIED_PARTIAL')
      AND UPPER(adapter_run.validation_status)='VALID'
      AND source_fact.current=1
      AND source_fact.fact_type='DECLARED_DEFAULT'
      AND source_fact.ontology_version=fact.ontology_version
      AND decision.property_name=source_fact.fact_name
      AND source_fact.fact_name=fact.fact_name
      AND source_fact.subject_entity_id=fact.subject_entity_id
      AND source_fact.declared_on_entity_id IS fact.declared_on_entity_id
      AND source_fact.value_kind=fact.value_kind
      AND source_fact.value_text IS fact.value_text
      AND source_fact.value_number IS fact.value_number
      AND source_fact.value_integer IS fact.value_integer
      AND source_fact.value_json IS fact.value_json
      AND source_fact.unit=fact.unit
      AND source_fact.status=fact.status
      AND source_fact.confidence=fact.confidence
      AND decision.evidence_uri<>''
      AND source_revision.source_kind='blueprint_evidence'
      AND source_revision.schema_version='ark.blueprint.evidence.v2'
      AND UPPER(source_revision.freshness_status)='FRESH'
      AND source_evidence.evidence_role='DEFAULT_VALUE_ACTUAL'
      AND semantic_evidence.evidence_role=(
          'SEMANTIC_ADAPTER:' || decision.rule_id
      )
      AND ({current_adapter_rule_sql})
)
""".format(current_adapter_rule_sql=_CURRENT_ADAPTER_RULE_SQL)


def _revision_hash(
    revisions: Sequence[tuple[str, str, str, str]],
) -> str:
    payload = json.dumps(
        sorted(set(revisions)),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _revision_identities(
    core: sqlite3.Connection,
) -> dict[int, tuple[str, str, str, str]]:
    return {
        int(revision_id): (
            str(source_kind),
            str(source_uri),
            str(source_fingerprint),
            str(freshness_status).upper(),
        )
        for (
            revision_id,
            source_kind,
            source_uri,
            source_fingerprint,
            freshness_status,
        ) in core.execute(
            """
            SELECT revision_id, source_kind, source_uri,
                   source_fingerprint, freshness_status
            FROM source_revisions
            """
        )
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_projection_review_contract(
    review_path: Path | None,
) -> tuple[str, dict[str, list[dict[str, object]]], str]:
    if review_path is None:
        return "", {}, ""
    raw_bytes = review_path.read_bytes()
    data = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema") != PROJECTION_REVIEW_SCHEMA:
        raise ValueError(
            f"{review_path.name} must use {PROJECTION_REVIEW_SCHEMA}"
        )
    version = str(data.get("version") or "")
    if not version:
        raise ValueError(f"{review_path.name} review version is required")
    raw_projections = data.get("projections")
    if not isinstance(raw_projections, dict):
        raise ValueError(f"{review_path.name} projections must be an object")
    reviews: dict[str, list[dict[str, object]]] = {}
    review_ids: set[str] = set()
    for projection_name, raw_entries in raw_projections.items():
        if projection_name not in DOMAIN_PROJECTIONS:
            raise ValueError(f"Unknown reviewed projection: {projection_name}")
        if not isinstance(raw_entries, list):
            raise ValueError(f"{projection_name} reviews must be an array")
        entries: list[dict[str, object]] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise ValueError(
                    f"{projection_name} review entries must be objects"
                )
            entry = dict(raw)
            review_id = str(entry.get("reviewId") or "")
            required = (
                review_id,
                str(entry.get("canonicalUri") or ""),
                str(entry.get("factType") or ""),
                str(entry.get("factName") or ""),
                str(entry.get("valueKind") or ""),
                str(entry.get("evidenceUri") or ""),
            )
            if not all(required) or review_id in review_ids:
                raise ValueError(
                    f"{projection_name} has invalid or duplicate review ID"
                )
            review_ids.add(review_id)
            entries.append(entry)
        reviews[str(projection_name)] = entries
    return (
        version,
        reviews,
        hashlib.sha256(raw_bytes).hexdigest(),
    )


def _fact_rows(
    core: sqlite3.Connection,
    fact_types: Sequence[str],
    ontology_version: str,
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in fact_types)
    return list(
        core.execute(
            f"""
            SELECT
                fact.fact_id,
                fact.subject_entity_id AS entity_id,
                entity.canonical_uri,
                fact.fact_type,
                fact.fact_name,
                fact.scope_kind,
                fact.value_kind,
                fact.value_text,
                fact.value_number,
                fact.value_integer,
                fact.value_json,
                fact.unit,
                fact.status,
                fact.confidence,
                fact.ontology_version
            FROM facts AS fact
            JOIN entities AS entity
              ON entity.entity_id=fact.subject_entity_id
            WHERE fact.current=1
              AND fact.fact_type IN ({placeholders})
              AND fact.ontology_version=?
              AND {_USABLE_FACT_PREDICATE}
              AND {ACTIVE_PROMOTED_DERIVATION_PREDICATE}
              AND EXISTS (
                  SELECT 1
                  FROM fact_evidence AS evidence
                  JOIN source_revisions AS revision
                    ON revision.revision_id=evidence.source_revision_id
                  WHERE evidence.fact_id=fact.fact_id
                    AND evidence.evidence_uri<>''
                    AND UPPER(revision.freshness_status)='FRESH'
              )
            ORDER BY fact.fact_id
            """,
            (*fact_types, ontology_version),
        )
    )


def _fresh_evidence(
    core: sqlite3.Connection,
    fact_types: Sequence[str],
    allowed_fact_ids: set[int],
) -> dict[int, list[tuple[int, str, str, str]]]:
    placeholders = ",".join("?" for _ in fact_types)
    result: dict[int, list[tuple[int, str, str, str]]] = defaultdict(list)
    for fact_id, revision_id, uri, role, freshness in core.execute(
        f"""
        SELECT
            evidence.fact_id,
            evidence.source_revision_id,
            evidence.evidence_uri,
            evidence.evidence_role,
            revision.freshness_status
        FROM fact_evidence AS evidence
        JOIN facts AS fact ON fact.fact_id=evidence.fact_id
        JOIN source_revisions AS revision
          ON revision.revision_id=evidence.source_revision_id
        WHERE fact.current=1
          AND fact.fact_type IN ({placeholders})
          AND evidence.evidence_uri<>''
          AND UPPER(revision.freshness_status)='FRESH'
        ORDER BY
            evidence.fact_id,
            evidence.source_revision_id,
            evidence.evidence_uri
        """,
        tuple(fact_types),
    ):
        parsed_fact_id = int(fact_id)
        if parsed_fact_id not in allowed_fact_ids:
            continue
        result[parsed_fact_id].append(
            (
                int(revision_id),
                str(uri),
                str(role),
                str(freshness).upper(),
            )
        )
    return result


def _promoted_lineage(
    core: sqlite3.Connection,
    fact_types: Sequence[str],
    allowed_fact_ids: set[int],
) -> dict[int, list[tuple[object, ...]]]:
    placeholders = ",".join("?" for _ in fact_types)
    result: dict[int, list[tuple[object, ...]]] = defaultdict(list)
    for row in core.execute(
        f"""
        SELECT
            decision.decision_key,
            decision.semantic_fact_id,
            decision.source_fact_id,
            decision.legacy_lineage_id,
            decision.adapter_id,
            decision.adapter_version,
            decision.rule_id,
            decision.source_mode,
            decision.reason_code
        FROM semantic_adapter_decisions AS decision
        JOIN semantic_adapter_runs AS adapter_run
          ON adapter_run.adapter_id=decision.adapter_id
         AND adapter_run.adapter_version=decision.adapter_version
        JOIN facts AS fact
          ON fact.fact_id=decision.semantic_fact_id
        JOIN facts AS source_fact
          ON source_fact.fact_id=decision.source_fact_id
        JOIN fact_evidence AS source_evidence
          ON source_evidence.fact_id=source_fact.fact_id
         AND source_evidence.source_revision_id=decision.source_revision_id
         AND source_evidence.evidence_uri=decision.evidence_uri
        JOIN fact_evidence AS semantic_evidence
          ON semantic_evidence.fact_id=fact.fact_id
         AND semantic_evidence.source_revision_id=decision.source_revision_id
         AND semantic_evidence.evidence_uri=decision.evidence_uri
        JOIN source_revisions AS source_revision
          ON source_revision.revision_id=decision.source_revision_id
        WHERE decision.decision_status='PROMOTED'
          AND decision.reason_code IN ('VERIFIED', 'VERIFIED_PARTIAL')
          AND UPPER(adapter_run.validation_status)='VALID'
          AND fact.current=1
          AND source_fact.current=1
          AND source_fact.fact_type='DECLARED_DEFAULT'
          AND source_fact.ontology_version=fact.ontology_version
          AND decision.property_name=source_fact.fact_name
          AND source_fact.fact_name=fact.fact_name
          AND source_fact.subject_entity_id=fact.subject_entity_id
          AND source_fact.declared_on_entity_id IS fact.declared_on_entity_id
          AND source_fact.value_kind=fact.value_kind
          AND source_fact.value_text IS fact.value_text
          AND source_fact.value_number IS fact.value_number
          AND source_fact.value_integer IS fact.value_integer
          AND source_fact.value_json IS fact.value_json
          AND source_fact.unit=fact.unit
          AND source_fact.status=fact.status
          AND source_fact.confidence=fact.confidence
          AND decision.evidence_uri<>''
          AND source_revision.source_kind='blueprint_evidence'
          AND source_revision.schema_version='ark.blueprint.evidence.v2'
          AND UPPER(source_revision.freshness_status)='FRESH'
          AND source_evidence.evidence_role='DEFAULT_VALUE_ACTUAL'
          AND semantic_evidence.evidence_role=(
              'SEMANTIC_ADAPTER:' || decision.rule_id
          )
          AND ({_CURRENT_ADAPTER_RULE_SQL})
          AND fact.fact_type IN ({placeholders})
        ORDER BY decision.semantic_fact_id, decision.decision_key
        """,
        tuple(fact_types),
    ):
        fact_id = int(row[1])
        if fact_id in allowed_fact_ids:
            result[fact_id].append(tuple(row))
    return result


def _projection_completeness(
    *,
    projection_name: str,
    fact_ids: set[int],
    lineage_by_fact: Mapping[int, Sequence[tuple[object, ...]]],
) -> dict[int, str]:
    completeness_by_fact: dict[int, str] = {}
    for fact_id in fact_ids:
        reasons = {
            str(value[8])
            for value in lineage_by_fact.get(fact_id, ())
        }
        if "VERIFIED" in reasons:
            completeness_by_fact[fact_id] = "COMPLETE"
        elif "VERIFIED_PARTIAL" in reasons:
            completeness_by_fact[fact_id] = "PARTIAL"
        else:
            raise ValueError(
                f"{projection_name} fact {fact_id} has no "
                "active verified semantic lineage"
            )
    return completeness_by_fact


def _projection_content_digest(
    *,
    rows: Sequence[Mapping[str, object]],
    evidence_by_fact: Mapping[
        int,
        Sequence[tuple[int, str, str, str]],
    ],
    lineage_by_fact: Mapping[int, Sequence[tuple[object, ...]]],
    completeness_by_fact: Mapping[int, str],
    review_rows: Sequence[tuple[str, int, str, str, str]] = (),
) -> str:
    row_records = [
        {
            "factId": int(row["fact_id"]),
            "entityId": int(row["entity_id"]),
            "canonicalUri": str(row["canonical_uri"]),
            "factType": str(row["fact_type"]),
            "factName": str(row["fact_name"]),
            "scopeKind": str(row["scope_kind"]),
            "valueKind": str(row["value_kind"]),
            "valueText": (
                None
                if row["value_text"] is None
                else str(row["value_text"])
            ),
            "valueNumber": (
                None
                if row["value_number"] is None
                else float(row["value_number"])
            ),
            "valueInteger": (
                None
                if row["value_integer"] is None
                else int(row["value_integer"])
            ),
            "valueJson": (
                None
                if row["value_json"] is None
                else str(row["value_json"])
            ),
            "unit": str(row["unit"]),
            "status": str(row["status"]),
            "confidence": str(row["confidence"]),
            "ontologyVersion": str(row["ontology_version"]),
            "completeness": str(
                completeness_by_fact[int(row["fact_id"])]
            ),
        }
        for row in rows
    ]
    evidence_records = [
        {
            "factId": fact_id,
            "sourceRevisionId": int(revision_id),
            "evidenceUri": str(evidence_uri),
            "evidenceRole": str(evidence_role),
            "freshnessStatus": str(freshness_status),
        }
        for fact_id, evidence_values in evidence_by_fact.items()
        for (
            revision_id,
            evidence_uri,
            evidence_role,
            freshness_status,
        ) in evidence_values
    ]
    lineage_records = [
        {
            "decisionKey": str(lineage[0]),
            "factId": int(lineage[1]),
            "sourceFactId": int(lineage[2]),
            "legacyLineageId": (
                None if lineage[3] is None else int(lineage[3])
            ),
            "adapterId": str(lineage[4]),
            "adapterVersion": str(lineage[5]),
            "ruleId": str(lineage[6]),
            "sourceMode": str(lineage[7]),
            "reasonCode": str(lineage[8]),
        }
        for lineage_values in lineage_by_fact.values()
        for lineage in lineage_values
    ]
    review_records = [
        {
            "reviewId": str(review_id),
            "factId": int(fact_id),
            "reviewStatus": str(review_status),
            "evidenceUri": str(evidence_uri),
            "reviewVersion": str(review_version),
        }
        for (
            review_id,
            fact_id,
            review_status,
            evidence_uri,
            review_version,
        ) in review_rows
    ]

    def canonical_sort_key(record: Mapping[str, object]) -> str:
        return json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    payload = {
        "schema": PROJECTION_CONTENT_DIGEST_SCHEMA,
        "rows": sorted(row_records, key=canonical_sort_key),
        "evidence": sorted(evidence_records, key=canonical_sort_key),
        "lineage": sorted(lineage_records, key=canonical_sort_key),
        "reviews": sorted(review_records, key=canonical_sort_key),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_core_projection_content_digest(
    core: sqlite3.Connection,
    *,
    projection_name: str,
    fact_types: Sequence[str],
    ontology_version: str,
    review_version: str = "",
    reviews: Sequence[Mapping[str, object]] = (),
    matched_review_rows: Sequence[tuple[object, ...]] | None = None,
) -> str:
    """Return the canonical semantic digest expected from current Core."""

    original_row_factory = core.row_factory
    core.row_factory = sqlite3.Row
    try:
        rows = _fact_rows(core, fact_types, ontology_version)
        fact_ids = {int(row["fact_id"]) for row in rows}
        evidence_by_fact = _fresh_evidence(core, fact_types, fact_ids)
        lineage_by_fact = _promoted_lineage(core, fact_types, fact_ids)
        completeness_by_fact = _projection_completeness(
            projection_name=projection_name,
            fact_ids=fact_ids,
            lineage_by_fact=lineage_by_fact,
        )
        if matched_review_rows is None:
            _review_status, matched_reviews, _review_failures = (
                _review_projection(
                    projection_name=projection_name,
                    rows=rows,
                    evidence_by_fact=evidence_by_fact,
                    review_version=review_version,
                    reviews=reviews,
                )
            )
        else:
            matched_reviews = list(matched_review_rows)
    finally:
        core.row_factory = original_row_factory
    return _projection_content_digest(
        rows=rows,
        evidence_by_fact=evidence_by_fact,
        lineage_by_fact=lineage_by_fact,
        completeness_by_fact=completeness_by_fact,
        review_rows=matched_reviews,
    )


def compute_projection_artifact_content_digest(
    projection: sqlite3.Connection,
) -> str:
    """Recompute a projection's canonical semantic digest from SQLite."""

    projection.row_factory = sqlite3.Row
    rows = list(
        projection.execute(
            """
            SELECT
                fact_id,
                entity_id,
                canonical_uri,
                fact_type,
                fact_name,
                scope_kind,
                value_kind,
                value_text,
                value_number,
                value_integer,
                value_json,
                unit,
                status,
                confidence,
                ontology_version,
                completeness_status
            FROM projection_rows
            ORDER BY fact_id
            """
        )
    )
    fact_ids = {int(row["fact_id"]) for row in rows}
    evidence_by_fact: dict[
        int,
        list[tuple[int, str, str, str]],
    ] = defaultdict(list)
    for row in projection.execute(
        """
        SELECT
            fact_id,
            source_revision_id,
            evidence_uri,
            evidence_role,
            freshness_status
        FROM projection_evidence
        ORDER BY fact_id, source_revision_id, evidence_uri
        """
    ):
        evidence_by_fact[int(row["fact_id"])].append(
            (
                int(row["source_revision_id"]),
                str(row["evidence_uri"]),
                str(row["evidence_role"]),
                str(row["freshness_status"]),
            )
        )
    lineage_by_fact: dict[int, list[tuple[object, ...]]] = defaultdict(list)
    for row in projection.execute(
        """
        SELECT
            decision_key,
            fact_id,
            source_fact_id,
            legacy_lineage_id,
            adapter_id,
            adapter_version,
            rule_id,
            source_mode,
            reason_code
        FROM projection_lineage
        ORDER BY fact_id, decision_key
        """
    ):
        lineage_by_fact[int(row["fact_id"])].append(tuple(row))
    completeness_by_fact = {
        int(row["fact_id"]): str(row["completeness_status"])
        for row in rows
    }
    if (
        set(evidence_by_fact) - fact_ids
        or set(lineage_by_fact) - fact_ids
    ):
        raise ValueError("Projection content contains orphan identities")
    review_rows = [
        (
            str(row["review_id"]),
            int(row["fact_id"]),
            str(row["review_status"]),
            str(row["evidence_uri"]),
            str(row["review_version"]),
        )
        for row in projection.execute(
            """
            SELECT
                review_id,
                fact_id,
                review_status,
                evidence_uri,
                review_version
            FROM projection_reviews
            ORDER BY review_id
            """
        )
    ]
    return _projection_content_digest(
        rows=rows,
        evidence_by_fact=evidence_by_fact,
        lineage_by_fact=lineage_by_fact,
        completeness_by_fact=completeness_by_fact,
        review_rows=review_rows,
    )


def _value_matches_review(
    row: Mapping[str, object],
    review: Mapping[str, object],
) -> bool:
    kind = str(row["value_kind"])
    if kind != str(review.get("valueKind") or ""):
        return False
    expected_keys = {
        "TEXT": ("valueText", row["value_text"]),
        "ENTITY_REF": ("valueText", row["value_text"]),
        "NUMBER": ("valueNumber", row["value_number"]),
        "INTEGER": ("valueInteger", row["value_integer"]),
        "BOOLEAN": ("valueInteger", row["value_integer"]),
        "JSON": ("valueJson", row["value_json"]),
    }
    if kind not in expected_keys:
        return False
    review_key, actual = expected_keys[kind]
    expected = review.get(review_key)
    if kind == "NUMBER":
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if kind in {"INTEGER", "BOOLEAN"}:
        try:
            return int(actual) == int(expected)
        except (TypeError, ValueError):
            return False
    if kind == "JSON":
        try:
            return json.loads(str(actual)) == json.loads(str(expected))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
    return actual == expected


def _review_projection(
    *,
    projection_name: str,
    rows: Sequence[sqlite3.Row],
    evidence_by_fact: Mapping[int, Sequence[tuple[int, str, str, str]]],
    review_version: str,
    reviews: Sequence[Mapping[str, object]],
) -> tuple[str, list[tuple[str, int, str, str, str]], list[str]]:
    if not reviews:
        return "UNREVIEWED", [], []
    by_key: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_key[
            (
                str(row["canonical_uri"]),
                str(row["fact_type"]),
                str(row["fact_name"]),
            )
        ].append(row)
    matched: list[tuple[str, int, str, str, str]] = []
    failures: list[str] = []
    for review in reviews:
        review_id = str(review["reviewId"])
        key = (
            str(review["canonicalUri"]),
            str(review["factType"]),
            str(review["factName"]),
        )
        candidates = by_key.get(key, [])
        if len(candidates) != 1:
            failures.append(f"{review_id}:FACT_NOT_UNIQUE")
            continue
        row = candidates[0]
        fact_id = int(row["fact_id"])
        evidence_uri = str(review["evidenceUri"])
        evidence_uris = {
            value[1] for value in evidence_by_fact.get(fact_id, ())
        }
        if not _value_matches_review(row, review):
            failures.append(f"{review_id}:VALUE_MISMATCH")
            continue
        if evidence_uri not in evidence_uris:
            failures.append(f"{review_id}:EVIDENCE_MISMATCH")
            continue
        matched.append(
            (
                review_id,
                fact_id,
                "FIXTURE_EXACT",
                evidence_uri,
                review_version,
            )
        )
    status = "FIXTURE_EXACT" if not failures else "REVIEW_MISMATCH"
    return status, matched, failures


def build_domain_projection(
    *,
    core: sqlite3.Connection,
    projection_name: str,
    output_path: Path,
    generated_at: str,
    ontology_version: str,
    review_path: Path | None = None,
    snapshot_build_id: str = "",
    snapshot_source_fingerprint: str = "",
) -> dict[str, object]:
    """Build exactly one complete projection without touching its siblings.

    The caller owns the Core transaction and must supply a fresh same-volume
    staging path.  This primitive never replaces a published projection.
    """

    if projection_name not in DOMAIN_PROJECTIONS:
        raise ValueError(f"unknown domain projection: {projection_name}")
    if bool(snapshot_build_id) != bool(snapshot_source_fingerprint):
        raise ValueError("projection snapshot identity requires build and source")
    expected_name = f"{projection_name}.sqlite"
    if output_path.name != expected_name:
        raise ValueError(f"projection output must be named {expected_name}")
    if not output_path.parent.is_dir() or output_path.parent.is_symlink():
        raise ValueError("projection staging parent must be a real directory")
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError("projection staging output already exists")

    review_version, review_config, review_config_sha256 = (
        load_projection_review_contract(review_path)
    )
    revision_identities = _revision_identities(core)
    fact_types = DOMAIN_PROJECTIONS[projection_name]
    rows = _fact_rows(core, fact_types, ontology_version)
    fact_ids = {int(row["fact_id"]) for row in rows}
    evidence_by_fact = _fresh_evidence(core, fact_types, fact_ids)
    lineage_by_fact = _promoted_lineage(core, fact_types, fact_ids)
    all_revision_identities = [
        revision_identities[evidence[0]]
        for values in evidence_by_fact.values()
        for evidence in values
    ]
    revision_set_hash = _revision_hash(all_revision_identities)
    completeness_by_fact = _projection_completeness(
        projection_name=projection_name,
        fact_ids=fact_ids,
        lineage_by_fact=lineage_by_fact,
    )
    review_status, matched_reviews, review_failures = _review_projection(
        projection_name=projection_name,
        rows=rows,
        evidence_by_fact=evidence_by_fact,
        review_version=review_version,
        reviews=review_config.get(projection_name, ()),
    )
    content_digest = _projection_content_digest(
        rows=rows,
        evidence_by_fact=evidence_by_fact,
        lineage_by_fact=lineage_by_fact,
        completeness_by_fact=completeness_by_fact,
        review_rows=matched_reviews,
    )

    projection = sqlite3.connect(output_path)
    try:
        projection.executescript(PROJECTION_SCHEMA_SQL)
        metadata_rows = [
            ("schema_version", PROJECTION_SCHEMA_VERSION),
            ("projection_name", projection_name),
            ("projection_version", "v2"),
            ("source_revision_set_hash", revision_set_hash),
            ("ontology_version", ontology_version),
            ("built_at", generated_at),
            ("truth_source", "core.sqlite"),
            ("review_version", review_version),
            ("review_status", review_status),
            ("review_config_sha256", review_config_sha256),
            ("content_digest", content_digest),
        ]
        if snapshot_build_id:
            metadata_rows.extend(
                [
                    ("snapshot_build_id", snapshot_build_id),
                    (
                        "snapshot_source_fingerprint",
                        snapshot_source_fingerprint,
                    ),
                ]
            )
        projection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata_rows)
        for row in rows:
            fact_id = int(row["fact_id"])
            evidence = evidence_by_fact[fact_id]
            revisions = [revision_identities[value[0]] for value in evidence]
            projection.execute(
                """
                INSERT INTO projection_rows(
                    fact_id, entity_id, canonical_uri, fact_type,
                    fact_name, scope_kind, value_kind, value_text,
                    value_number, value_integer, value_json, unit,
                    status, confidence, ontology_version,
                    completeness_status, evidence_count,
                    source_revision_set_hash
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    *tuple(row),
                    completeness_by_fact[fact_id],
                    len(evidence),
                    _revision_hash(revisions),
                ),
            )
        projection.executemany(
            """
            INSERT INTO projection_evidence(
                fact_id, source_revision_id, evidence_uri,
                evidence_role, freshness_status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (fact_id, *evidence)
                for fact_id, values in evidence_by_fact.items()
                for evidence in values
            ],
        )
        projection.executemany(
            """
            INSERT INTO projection_lineage(
                decision_key, fact_id, source_fact_id,
                legacy_lineage_id, adapter_id, adapter_version,
                rule_id, source_mode, reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                lineage
                for values in lineage_by_fact.values()
                for lineage in values
            ],
        )
        projection.executemany(
            """
            INSERT INTO projection_reviews(
                review_id, fact_id, review_status,
                evidence_uri, review_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            matched_reviews,
        )
        projection.execute("ANALYZE main")
        projection.commit()
        integrity = str(projection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = len(
            list(projection.execute("PRAGMA foreign_key_check"))
        )
        table_counts = {
            table: int(
                projection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in (
                "metadata",
                "projection_evidence",
                "projection_lineage",
                "projection_reviews",
                "projection_rows",
            )
        }
    finally:
        projection.close()

    with output_path.open("r+b") as handle:
        os.fsync(handle.fileno())
    validation_status = (
        "VALID"
        if integrity == "ok"
        and foreign_key_violations == 0
        and all(
            value in {"COMPLETE", "PARTIAL"}
            for value in completeness_by_fact.values()
        )
        else "INVALID"
    )
    if validation_status != "VALID":
        raise RuntimeError(f"invalid {projection_name} projection staging file")
    core.execute(
        "DELETE FROM projection_runs WHERE projection_name=?",
        (projection_name,),
    )
    core.execute(
        """
        INSERT INTO projection_runs(
            projection_name, projection_version,
            source_revision_set_hash, ontology_version, built_at,
            row_count, validation_status
        ) VALUES (?, 'v2', ?, ?, ?, ?, ?)
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
    return {
        "path": output_path.name,
        "schemaVersion": PROJECTION_SCHEMA_VERSION,
        "projectionVersion": "v2",
        "bytes": output_path.stat().st_size,
        "sha256": _sha256_file(output_path),
        "foreignKeyViolations": foreign_key_violations,
        "tableCounts": table_counts,
        "rows": len(rows),
        "evidenceRows": table_counts["projection_evidence"],
        "lineageRows": table_counts["projection_lineage"],
        "integrity": integrity,
        "sourceRevisionSetHash": revision_set_hash,
        "contentDigest": content_digest,
        "ontologyVersion": ontology_version,
        "validationStatus": validation_status,
        "reviewVersion": review_version,
        "reviewConfigSha256": review_config_sha256,
        "reviewStatus": review_status,
        "reviewedRows": len({value[1] for value in matched_reviews}),
        "reviewFailures": review_failures,
        "completeRows": sum(
            value == "COMPLETE" for value in completeness_by_fact.values()
        ),
        "partialRows": sum(
            value == "PARTIAL" for value in completeness_by_fact.values()
        ),
        "unspecifiedRows": sum(
            value == "UNSPECIFIED" for value in completeness_by_fact.values()
        ),
    }


def build_domain_projections(
    *,
    core_path: Path,
    output_dir: Path,
    generated_at: str,
    ontology_version: str,
    review_path: Path | None = None,
    snapshot_build_id: str = "",
    snapshot_source_fingerprint: str = "",
) -> dict[str, dict[str, object]]:
    """Build strict read models containing FRESH Evidence and derivation lineage."""

    if bool(snapshot_build_id) != bool(snapshot_source_fingerprint):
        raise ValueError(
            "projection snapshot identity requires build and source"
        )
    (
        review_version,
        review_config,
        review_config_sha256,
    ) = load_projection_review_contract(review_path)
    core = sqlite3.connect(core_path)
    core.row_factory = sqlite3.Row
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    results: dict[str, dict[str, object]] = {}
    try:
        core.execute("DELETE FROM projection_runs")
        revision_identities = _revision_identities(core)
        for projection_name, fact_types in DOMAIN_PROJECTIONS.items():
            rows = _fact_rows(core, fact_types, ontology_version)
            fact_ids = {int(row["fact_id"]) for row in rows}
            evidence_by_fact = _fresh_evidence(core, fact_types, fact_ids)
            lineage_by_fact = _promoted_lineage(core, fact_types, fact_ids)
            all_revision_identities = [
                revision_identities[evidence[0]]
                for values in evidence_by_fact.values()
                for evidence in values
            ]
            revision_set_hash = _revision_hash(all_revision_identities)
            completeness_by_fact = _projection_completeness(
                projection_name=projection_name,
                fact_ids=fact_ids,
                lineage_by_fact=lineage_by_fact,
            )
            review_status, matched_reviews, review_failures = (
                _review_projection(
                    projection_name=projection_name,
                    rows=rows,
                    evidence_by_fact=evidence_by_fact,
                    review_version=review_version,
                    reviews=review_config.get(projection_name, ()),
                )
            )
            content_digest = _projection_content_digest(
                rows=rows,
                evidence_by_fact=evidence_by_fact,
                lineage_by_fact=lineage_by_fact,
                completeness_by_fact=completeness_by_fact,
                review_rows=matched_reviews,
            )
            path = output_dir / f"{projection_name}.sqlite"
            projection = sqlite3.connect(path)
            try:
                projection.executescript(PROJECTION_SCHEMA_SQL)
                metadata_rows = [
                    ("schema_version", PROJECTION_SCHEMA_VERSION),
                    ("projection_name", projection_name),
                    ("projection_version", "v2"),
                    ("source_revision_set_hash", revision_set_hash),
                    ("ontology_version", ontology_version),
                    ("built_at", generated_at),
                    ("truth_source", "core.sqlite"),
                    ("review_version", review_version),
                    ("review_status", review_status),
                    (
                        "review_config_sha256",
                        review_config_sha256,
                    ),
                    ("content_digest", content_digest),
                ]
                if snapshot_build_id:
                    metadata_rows.extend(
                        [
                            ("snapshot_build_id", snapshot_build_id),
                            (
                                "snapshot_source_fingerprint",
                                snapshot_source_fingerprint,
                            ),
                        ]
                    )
                projection.executemany(
                    "INSERT INTO metadata VALUES (?, ?)",
                    metadata_rows,
                )
                for row in rows:
                    fact_id = int(row["fact_id"])
                    evidence = evidence_by_fact[fact_id]
                    revisions = [
                        revision_identities[value[0]] for value in evidence
                    ]
                    completeness = completeness_by_fact[fact_id]
                    projection.execute(
                        """
                        INSERT INTO projection_rows(
                            fact_id, entity_id, canonical_uri, fact_type,
                            fact_name, scope_kind, value_kind, value_text,
                            value_number, value_integer, value_json, unit,
                            status, confidence, ontology_version,
                            completeness_status,
                            evidence_count, source_revision_set_hash
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?
                        )
                        """,
                        (
                            *tuple(row),
                            completeness,
                            len(evidence),
                            _revision_hash(revisions),
                        ),
                    )
                projection.executemany(
                    """
                    INSERT INTO projection_evidence(
                        fact_id, source_revision_id, evidence_uri,
                        evidence_role, freshness_status
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (fact_id, *evidence)
                        for fact_id, values in evidence_by_fact.items()
                        for evidence in values
                    ],
                )
                projection.executemany(
                    """
                    INSERT INTO projection_lineage(
                        decision_key, fact_id, source_fact_id,
                        legacy_lineage_id, adapter_id, adapter_version,
                        rule_id, source_mode, reason_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        lineage
                        for values in lineage_by_fact.values()
                        for lineage in values
                    ],
                )
                projection.executemany(
                    """
                    INSERT INTO projection_reviews(
                        review_id, fact_id, review_status,
                        evidence_uri, review_version
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    matched_reviews,
                )
                projection.execute("ANALYZE main")
                projection.commit()
                integrity = str(
                    projection.execute("PRAGMA integrity_check").fetchone()[0]
                )
                foreign_key_violations = len(
                    list(projection.execute("PRAGMA foreign_key_check"))
                )
                table_counts = {
                    table: int(
                        projection.execute(
                            f'SELECT COUNT(*) FROM "{table}"'
                        ).fetchone()[0]
                    )
                    for table in (
                        "metadata",
                        "projection_evidence",
                        "projection_lineage",
                        "projection_reviews",
                        "projection_rows",
                    )
                }
            finally:
                projection.close()
            evidence_rows = table_counts["projection_evidence"]
            lineage_rows = table_counts["projection_lineage"]
            validation_status = (
                "VALID"
                if integrity == "ok"
                and foreign_key_violations == 0
                and all(
                    value in {"COMPLETE", "PARTIAL"}
                    for value in completeness_by_fact.values()
                )
                else "INVALID"
            )
            core.execute(
                """
                INSERT INTO projection_runs(
                    projection_name, projection_version,
                    source_revision_set_hash, ontology_version, built_at,
                    row_count, validation_status
                ) VALUES (?, 'v2', ?, ?, ?, ?, ?)
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
                "schemaVersion": PROJECTION_SCHEMA_VERSION,
                "projectionVersion": "v2",
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "foreignKeyViolations": foreign_key_violations,
                "tableCounts": table_counts,
                "rows": len(rows),
                "evidenceRows": evidence_rows,
                "lineageRows": lineage_rows,
                "integrity": integrity,
                "sourceRevisionSetHash": revision_set_hash,
                "contentDigest": content_digest,
                "ontologyVersion": ontology_version,
                "validationStatus": validation_status,
                "reviewVersion": review_version,
                "reviewConfigSha256": review_config_sha256,
                "reviewStatus": review_status,
                "reviewedRows": len(
                    {value[1] for value in matched_reviews}
                ),
                "reviewFailures": review_failures,
                "completeRows": sum(
                    value == "COMPLETE"
                    for value in completeness_by_fact.values()
                ),
                "partialRows": sum(
                    value == "PARTIAL"
                    for value in completeness_by_fact.values()
                ),
                "unspecifiedRows": sum(
                    value == "UNSPECIFIED"
                    for value in completeness_by_fact.values()
                ),
            }
        core.commit()
        return results
    finally:
        core.close()
