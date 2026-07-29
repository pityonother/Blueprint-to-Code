"""Evidence resolver and materializer for reviewed semantic adapters."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from ..fact_store import CONFIRMED_STATUSES, FactValue, store_fact
from ..ontology import OntologyBundle
from .base import (
    AdapterSpec,
    LegacyTableSpec,
    LineageAnchorSpec,
    SemanticRule,
)
from .json_shapes import semantic_json_shape_is_valid


BLUEPRINT_EVIDENCE_KIND = "blueprint_evidence"
BLUEPRINT_EVIDENCE_SCHEMA = "ark.blueprint.evidence.v2"
DEFAULT_VALUE_EVIDENCE_ROLE = "DEFAULT_VALUE_ACTUAL"
CONFIDENCE_RANK = {
    "UNKNOWN": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CONFIRMED": 4,
}
USABLE_SOURCE_STATUSES = CONFIRMED_STATUSES - {
    "CONFIRMED_FINGERPRINT_ONLY",
    "CONFIRMED_EMPTY",
}
DIRECT_SOURCE_MODE = "CORE_TYPED_FACT"
LEGACY_SOURCE_MODE = "LEGACY_TABLE"


class AdapterSchemaError(ValueError):
    """A declared legacy adapter source does not match its required schema."""


@dataclass(frozen=True)
class _Evidence:
    source_revision_id: int
    evidence_uri: str
    evidence_role: str


@dataclass(frozen=True)
class _ResolvedFact:
    row: Mapping[str, object]
    evidence: tuple[_Evidence, ...]


@dataclass(frozen=True)
class _Resolution:
    resolved: _ResolvedFact | None
    reason_code: str
    source_fact_id: int | None
    evidence_uri: str
    source_revision_id: int | None


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _safe_object_path(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text.startswith(("/Game/", "/Mods/")):
        return ""
    if "\\" in text or ":" in text or ".." in text or text.count(".") != 1:
        return ""
    package, asset = text.rsplit(".", 1)
    if not package or not asset or "/" in asset:
        return ""
    if package.rsplit("/", 1)[-1] != asset:
        return ""
    return text


def _safe_entity_reference(value: object) -> bool:
    text = "" if value is None else str(value).strip()
    if not text.startswith(("/Game/", "/Mods/", "/Script/")):
        return False
    return "\\" not in text and ":" not in text and ".." not in text


def _database_schema(connection: sqlite3.Connection) -> str:
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='schema'"
        ).fetchone()
    except sqlite3.DatabaseError:
        return ""
    return "" if row is None else str(row[0])


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> frozenset[str]:
    return frozenset(
        str(row[1])
        for row in connection.execute(
            f"PRAGMA table_info({_quote(table_name)})"
        )
    )


def _primary_key_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> tuple[str, ...]:
    columns = list(
        connection.execute(f"PRAGMA table_info({_quote(table_name)})")
    )
    return tuple(
        str(row[1])
        for row in sorted(columns, key=lambda item: int(item[5]))
        if int(row[5]) > 0
    )


def _validate_source_schema(
    *,
    legacy_root: Path,
    database_name: str,
    schema_version: str,
    table_name: str,
    required_columns: frozenset[str],
    primary_key_columns: tuple[str, ...] | None = None,
) -> Path | None:
    database_path = legacy_root / database_name
    if not database_path.is_file():
        return None
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        actual_schema = _database_schema(connection)
        if actual_schema != schema_version:
            raise AdapterSchemaError(
                f"{database_name} schema mismatch: "
                f"expected {schema_version}, got {actual_schema or 'MISSING'}"
            )
        columns = _table_columns(connection, table_name)
        if not columns:
            raise AdapterSchemaError(
                f"{database_name}.{table_name} is missing"
            )
        missing = required_columns - columns
        if missing:
            raise AdapterSchemaError(
                f"{database_name}.{table_name} missing columns: "
                f"{sorted(missing)}"
            )
        if primary_key_columns is not None:
            actual_primary_key = _primary_key_columns(
                connection,
                table_name,
            )
            if actual_primary_key != primary_key_columns:
                raise AdapterSchemaError(
                    f"{database_name}.{table_name} primary key mismatch: "
                    f"expected {primary_key_columns}, got {actual_primary_key}"
                )
    finally:
        connection.close()
    return database_path


def _validate_legacy_source(
    legacy_root: Path,
    source: LegacyTableSpec,
) -> Path | None:
    return _validate_source_schema(
        legacy_root=legacy_root,
        database_name=source.database_name,
        schema_version=source.schema_version,
        table_name=source.table_name,
        required_columns=source.required_columns,
        primary_key_columns=source.primary_key_columns,
    )


def _validate_lineage_anchor(
    legacy_root: Path,
    anchor: LineageAnchorSpec,
) -> Path | None:
    return _validate_source_schema(
        legacy_root=legacy_root,
        database_name=anchor.database_name,
        schema_version=anchor.schema_version,
        table_name=anchor.table_name,
        required_columns=anchor.required_columns,
    )


def _legacy_primary_key(
    row: Mapping[str, object],
    columns: Sequence[str],
) -> str:
    return json.dumps(
        {name: row.get(name) for name in columns},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _legacy_lineage_id(
    core: sqlite3.Connection,
    *,
    source: LegacyTableSpec,
    primary_key: str,
) -> int | None:
    row = core.execute(
        """
        SELECT lineage_id
        FROM legacy_lineage
        WHERE legacy_database=?
          AND legacy_table=?
          AND legacy_primary_key=?
        """,
        (source.database_name, source.table_name, primary_key),
    ).fetchone()
    return None if row is None else int(row[0])


def _anchor_lineage_id(
    core: sqlite3.Connection,
    *,
    anchor: LineageAnchorSpec,
    entity_id: int,
    object_path: str,
) -> int | None:
    row = core.execute(
        """
        SELECT lineage_id
        FROM legacy_lineage
        WHERE legacy_database=?
          AND legacy_table=?
          AND target_id=?
          AND source_asset_uri=?
        ORDER BY lineage_id
        LIMIT 1
        """,
        (
            anchor.database_name,
            anchor.table_name,
            entity_id,
            object_path,
        ),
    ).fetchone()
    return None if row is None else int(row[0])


def _matching_rule(
    rules: Sequence[SemanticRule],
    property_name: str,
) -> SemanticRule | None:
    matches = [
        rule for rule in rules if property_name in rule.source_properties
    ]
    if len(matches) > 1:
        raise ValueError(
            f"Property {property_name} has multiple semantic adapter rules"
        )
    return matches[0] if matches else None


def _legacy_payload(
    row: Mapping[str, object],
    *,
    source: LegacyTableSpec,
    property_name: str,
) -> tuple[object | None, str]:
    if source.reference_value_column:
        value = row.get(source.reference_value_column)
        if not _safe_entity_reference(value):
            return None, "NON_ASSET_SUBOBJECT_REF"
        return value, ""
    if not source.source_json_column:
        return None, "NO_DIRECT_TYPED_PAYLOAD"
    raw_text = row.get(source.source_json_column)
    try:
        payload = json.loads(str(raw_text))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "INVALID_SOURCE_JSON"
    if not isinstance(payload, Mapping):
        return None, "INVALID_SOURCE_JSON"
    if str(payload.get("key") or "") != property_name:
        return None, "SOURCE_PROPERTY_MISMATCH"
    raw = payload.get("raw")
    if not isinstance(raw, Mapping) or "value" not in raw:
        return None, "NO_DIRECT_TYPED_PAYLOAD"
    return raw["value"], ""


def _confidence_at_least(value: object, minimum: str) -> bool:
    actual_rank = CONFIDENCE_RANK.get(str(value or "").upper(), 0)
    return actual_rank >= CONFIDENCE_RANK.get(minimum.upper(), 0)


def _typed_payload_valid(row: Mapping[str, object]) -> bool:
    kind = str(row["value_kind"]).upper()
    text = row["value_text"]
    number = row["value_number"]
    integer = row["value_integer"]
    value_json = row["value_json"]
    if kind in {"TEXT", "ENTITY_REF", "FINGERPRINT"}:
        return (
            isinstance(text, str)
            and number is None
            and integer is None
            and value_json is None
        )
    if kind == "NUMBER":
        return (
            isinstance(number, (int, float))
            and not isinstance(number, bool)
            and math.isfinite(float(number))
            and text is None
            and integer is None
            and value_json is None
        )
    if kind == "INTEGER":
        return (
            isinstance(integer, int)
            and not isinstance(integer, bool)
            and text is None
            and number is None
            and value_json is None
        )
    if kind == "BOOLEAN":
        return (
            isinstance(integer, int)
            and integer in (0, 1)
            and text is None
            and number is None
            and value_json is None
        )
    if kind == "JSON":
        if (
            not isinstance(value_json, str)
            or text is not None
            or number is not None
            or integer is not None
        ):
            return False
        try:
            json.loads(value_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return True
    return False


def _fact_value(row: Mapping[str, object]) -> FactValue:
    return FactValue(
        str(row["value_kind"]).upper(),
        value_text=(
            None if row["value_text"] is None else str(row["value_text"])
        ),
        value_number=(
            None
            if row["value_number"] is None
            else float(row["value_number"])
        ),
        value_integer=(
            None
            if row["value_integer"] is None
            else int(row["value_integer"])
        ),
        value_json=(
            None if row["value_json"] is None else str(row["value_json"])
        ),
    )


def _payload_matches(row: Mapping[str, object], expected: object) -> bool:
    kind = str(row["value_kind"]).upper()
    if kind == "NUMBER":
        return (
            isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and float(row["value_number"]) == float(expected)
        )
    if kind == "INTEGER":
        return (
            isinstance(expected, int)
            and not isinstance(expected, bool)
            and int(row["value_integer"]) == expected
        )
    if kind == "BOOLEAN":
        return isinstance(expected, bool) and bool(row["value_integer"]) is expected
    if kind in {"TEXT", "ENTITY_REF"}:
        return isinstance(expected, str) and str(row["value_text"]) == expected
    if kind == "JSON":
        try:
            stored = json.loads(str(row["value_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return stored == expected
    return False


def _has_confirmed_native_root(
    core: sqlite3.Connection,
    *,
    entity_id: int,
    roots: Sequence[str],
) -> bool:
    if not roots:
        return False
    placeholders = ",".join("?" for _ in roots)
    row = core.execute(
        f"""
        WITH RECURSIVE
        assigned(class_id) AS (
            SELECT assigned_class.class_id
            FROM asset_class_assignments AS assignment
            JOIN classes AS assigned_class
              ON assigned_class.class_id=assignment.class_id
            JOIN source_revisions AS assigned_revision
              ON assigned_revision.revision_id=
                 assigned_class.source_revision_id
            WHERE assignment.entity_id=?
              AND assignment.assignment_kind='GENERATED_CLASS'
              AND assignment.evidence_uri<>''
              AND UPPER(assignment.status) IN (
                  'EXTRACTED', 'IDENTIFIED', 'CONFIRMED',
                  'VERIFIED', 'RESOLVED'
              )
              AND UPPER(assignment.confidence) IN ('HIGH', 'CONFIRMED')
              AND UPPER(assigned_class.status) IN (
                  'IDENTIFIED', 'CONFIRMED', 'VERIFIED', 'RESOLVED'
              )
              AND UPPER(assigned_class.confidence) IN ('HIGH', 'CONFIRMED')
              AND UPPER(assigned_revision.freshness_status)='FRESH'
        ),
        confirmed_chain(class_id) AS (
            SELECT class_id FROM assigned
            UNION
            SELECT edge.parent_class_id
            FROM confirmed_chain AS chain
            JOIN class_edges AS edge
              ON edge.child_class_id=chain.class_id
            JOIN source_revisions AS edge_revision
              ON edge_revision.revision_id=edge.source_revision_id
            JOIN classes AS parent
              ON parent.class_id=edge.parent_class_id
            JOIN source_revisions AS parent_revision
              ON parent_revision.revision_id=parent.source_revision_id
            WHERE edge.edge_kind IN ('blueprint_parent', 'native_parent')
              AND edge.evidence_id LIKE 'class-edge://%'
              AND UPPER(edge.status) IN (
                  'EXTRACTED', 'IDENTIFIED', 'CONFIRMED',
                  'VERIFIED', 'RESOLVED'
              )
              AND UPPER(edge.confidence) IN ('HIGH', 'CONFIRMED')
              AND UPPER(edge_revision.freshness_status)='FRESH'
              AND UPPER(parent.status) IN (
                  'IDENTIFIED', 'CONFIRMED', 'VERIFIED', 'RESOLVED'
              )
              AND UPPER(parent.confidence) IN ('HIGH', 'CONFIRMED')
              AND UPPER(parent_revision.freshness_status)='FRESH'
        )
        SELECT 1
        FROM assigned
        JOIN confirmed_chain AS chain
          ON 1=1
        JOIN classes AS root
          ON root.class_id=chain.class_id
        JOIN class_closure AS closure
          ON closure.descendant_class_id=assigned.class_id
         AND closure.ancestor_class_id=root.class_id
        WHERE root.is_native=1
          AND root.class_kind='NATIVE_UCLASS'
          AND UPPER(root.status) IN (
              'IDENTIFIED', 'CONFIRMED', 'VERIFIED', 'RESOLVED'
          )
          AND UPPER(root.confidence) IN ('HIGH', 'CONFIRMED')
          AND UPPER(closure.path_status) IN ('SELF', 'CONFIRMED')
          AND root.class_path IN ({placeholders})
        LIMIT 1
        """,
        (entity_id, *roots),
    ).fetchone()
    return row is not None


def _source_fact_rows(
    core: sqlite3.Connection,
    *,
    entity_id: int,
    property_name: str,
) -> dict[int, tuple[dict[str, object], list[_Evidence], set[str]]]:
    grouped: dict[
        int,
        tuple[dict[str, object], list[_Evidence], set[str]],
    ] = {}
    previous_row_factory = core.row_factory
    core.row_factory = sqlite3.Row
    try:
        for sqlite_row in core.execute(
            """
            SELECT
                fact.*,
                evidence.source_revision_id,
                evidence.evidence_uri,
                evidence.evidence_role,
                revision.freshness_status,
                revision.source_kind,
                revision.source_uri,
                revision.schema_version
            FROM facts AS fact
            LEFT JOIN fact_evidence AS evidence
              ON evidence.fact_id=fact.fact_id
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=evidence.source_revision_id
            WHERE fact.subject_entity_id=?
              AND fact.fact_type='DECLARED_DEFAULT'
              AND fact.fact_name=?
              AND fact.current=1
            ORDER BY
                fact.fact_id,
                evidence.source_revision_id,
                evidence.evidence_uri
            """,
            (entity_id, property_name),
        ):
            row = dict(sqlite_row)
            fact_id = int(row["fact_id"])
            if fact_id not in grouped:
                grouped[fact_id] = (row, [], set())
            _, evidence, freshness = grouped[fact_id]
            uri = str(row.get("evidence_uri") or "")
            revision_id = row.get("source_revision_id")
            freshness_status = str(
                row.get("freshness_status") or ""
            ).upper()
            source_uri = str(row.get("source_uri") or "")
            canonical_uri = (
                f"{source_uri}/default/{quote(property_name, safe='')}"
            )
            canonical_evidence = (
                str(row.get("source_kind") or "") == BLUEPRINT_EVIDENCE_KIND
                and str(row.get("schema_version") or "")
                == BLUEPRINT_EVIDENCE_SCHEMA
                and str(row.get("evidence_role") or "")
                == DEFAULT_VALUE_EVIDENCE_ROLE
                and uri == canonical_uri
            )
            if revision_id is not None and freshness_status:
                freshness.add(freshness_status)
                if not canonical_evidence:
                    freshness.add("NONCANONICAL")
            if (
                revision_id is not None
                and canonical_evidence
                and freshness_status == "FRESH"
            ):
                evidence.append(
                    _Evidence(
                        source_revision_id=int(revision_id),
                        evidence_uri=uri,
                        evidence_role=str(row.get("evidence_role") or ""),
                    )
                )
    finally:
        core.row_factory = previous_row_factory
    return grouped


def _resolve_source_fact(
    core: sqlite3.Connection,
    *,
    entity_id: int,
    property_name: str,
    rule: SemanticRule,
    ontology_version: str,
    expected: object | None,
    require_expected_match: bool,
) -> _Resolution:
    if not _has_confirmed_native_root(
        core,
        entity_id=entity_id,
        roots=rule.required_native_roots,
    ):
        return _Resolution(
            None,
            "CLASS_ROOT_NOT_CONFIRMED",
            None,
            "",
            None,
        )
    grouped = _source_fact_rows(
        core,
        entity_id=entity_id,
        property_name=property_name,
    )
    if not grouped:
        return _Resolution(None, "SOURCE_FACT_MISSING", None, "", None)
    rejected_reasons: list[tuple[str, int]] = []
    candidates: list[_ResolvedFact] = []
    for fact_id, (row, evidence, freshness) in grouped.items():
        if str(row["ontology_version"]) != ontology_version:
            rejected_reasons.append(("SOURCE_ONTOLOGY_MISMATCH", fact_id))
            continue
        status = str(row["status"]).upper()
        if status == "STALE":
            rejected_reasons.append(("SOURCE_STALE", fact_id))
            continue
        if status not in USABLE_SOURCE_STATUSES:
            rejected_reasons.append(("VALUE_STATUS_NOT_USABLE", fact_id))
            continue
        kind = str(row["value_kind"]).upper()
        if kind not in rule.allowed_value_kinds:
            rejected_reasons.append(("UNSUPPORTED_VALUE_TYPE", fact_id))
            continue
        if not _typed_payload_valid(row):
            rejected_reasons.append(("INVALID_TYPED_PAYLOAD", fact_id))
            continue
        if not _confidence_at_least(row["confidence"], rule.minimum_confidence):
            rejected_reasons.append(("CONFIDENCE_TOO_LOW", fact_id))
            continue
        if not evidence:
            reason = (
                "EVIDENCE_NOT_CANONICAL"
                if "NONCANONICAL" in freshness
                else "EVIDENCE_NOT_FRESH"
                if freshness
                else "EVIDENCE_MISSING"
            )
            rejected_reasons.append((reason, fact_id))
            continue
        if require_expected_match and not _payload_matches(row, expected):
            rejected_reasons.append(("VALUE_MISMATCH", fact_id))
            continue
        if kind == "ENTITY_REF" and not _safe_entity_reference(
            row["value_text"]
        ):
            rejected_reasons.append(("NON_ASSET_SUBOBJECT_REF", fact_id))
            continue
        if (rule.require_nonempty_json or rule.json_shape) and kind == "JSON":
            parsed = json.loads(str(row["value_json"]))
            if rule.require_nonempty_json and parsed in ([], {}, ""):
                rejected_reasons.append(("EMPTY_SEMANTIC_PAYLOAD", fact_id))
                continue
            if not semantic_json_shape_is_valid(
                shape=rule.json_shape,
                property_name=property_name,
                value=parsed,
            ):
                rejected_reasons.append(
                    ("INVALID_SEMANTIC_JSON_SHAPE", fact_id)
                )
                continue
        if rule.reject_denormal_number and kind == "NUMBER":
            number = float(row["value_number"])
            if number != 0.0 and abs(number) < 1e-30:
                rejected_reasons.append(("DENORMAL_NUMBER", fact_id))
                continue
        candidates.append(_ResolvedFact(row=row, evidence=tuple(evidence)))
    if len(candidates) > 1:
        return _Resolution(
            None,
            "AMBIGUOUS_SOURCE_FACT",
            int(candidates[0].row["fact_id"]),
            "",
            None,
        )
    if not candidates:
        reason, fact_id = rejected_reasons[0]
        return _Resolution(None, reason, fact_id, "", None)
    resolved = candidates[0]
    first = resolved.evidence[0]
    return _Resolution(
        resolved,
        "",
        int(resolved.row["fact_id"]),
        first.evidence_uri,
        first.source_revision_id,
    )


def _decision_key(payload: Sequence[object]) -> str:
    compact = json.dumps(
        list(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return "adapter-decision://" + hashlib.sha256(
        compact.encode("utf-8")
    ).hexdigest()


def _record_decision(
    core: sqlite3.Connection,
    *,
    spec: AdapterSpec,
    rule_id: str,
    source_mode: str,
    object_path: str,
    property_name: str,
    decision_status: str,
    reason_code: str,
    source_fact_id: int | None,
    semantic_fact_id: int | None,
    legacy_lineage_id: int | None,
    source_revision_id: int | None,
    evidence_uri: str,
    generated_at: str,
) -> None:
    key = _decision_key(
        (
            spec.adapter_id,
            spec.adapter_version,
            rule_id,
            source_mode,
            legacy_lineage_id,
            source_fact_id,
            object_path,
            property_name,
            decision_status,
            reason_code,
        )
    )
    core.execute(
        """
        INSERT OR REPLACE INTO semantic_adapter_decisions(
            decision_key, adapter_id, adapter_version, rule_id,
            source_mode, object_path, property_name, decision_status,
            reason_code, source_fact_id, semantic_fact_id,
            legacy_lineage_id, source_revision_id, evidence_uri,
            decided_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            spec.adapter_id,
            spec.adapter_version,
            rule_id,
            source_mode,
            object_path,
            property_name,
            decision_status,
            reason_code,
            source_fact_id,
            semantic_fact_id,
            legacy_lineage_id,
            source_revision_id,
            evidence_uri,
            generated_at,
        ),
    )


def _promote(
    core: sqlite3.Connection,
    *,
    ontology: OntologyBundle,
    rule: SemanticRule,
    resolved: _ResolvedFact,
) -> int:
    source = resolved.row
    semantic_fact_id = 0
    for evidence in resolved.evidence:
        semantic_fact_id = store_fact(
            core,
            ontology=ontology,
            subject_entity_id=int(source["subject_entity_id"]),
            fact_type=rule.output_fact_type,
            fact_name=str(source["fact_name"]),
            scope_kind="DERIVED_STATIC",
            declared_on_entity_id=(
                None
                if source["declared_on_entity_id"] is None
                else int(source["declared_on_entity_id"])
            ),
            value=_fact_value(source),
            unit=str(source["unit"] or ""),
            status=str(source["status"]),
            confidence=str(source["confidence"]),
            source_revision_id=evidence.source_revision_id,
            evidence_uri=evidence.evidence_uri,
            evidence_role=f"SEMANTIC_ADAPTER:{rule.rule_id}",
        )
    core.execute(
        """
        UPDATE facts
        SET current=1, ontology_version=?
        WHERE fact_id=?
        """,
        (ontology.version, semantic_fact_id),
    )
    return semantic_fact_id


def _entity_id(
    core: sqlite3.Connection,
    object_path: str,
) -> int | None:
    row = core.execute(
        "SELECT entity_id FROM entities WHERE canonical_uri=?",
        (object_path,),
    ).fetchone()
    return None if row is None else int(row[0])


def _iter_legacy_rows(
    database_path: Path,
    source: LegacyTableSpec,
) -> Iterable[Mapping[str, object]]:
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    order = ", ".join(_quote(name) for name in source.primary_key_columns)
    try:
        cursor = connection.execute(
            f"SELECT * FROM {_quote(source.table_name)} ORDER BY {order}"
        )
        while batch := cursor.fetchmany(2_000):
            for row in batch:
                yield dict(row)
    finally:
        connection.close()


def _process_legacy_source(
    core: sqlite3.Connection,
    *,
    legacy_root: Path,
    ontology: OntologyBundle,
    generated_at: str,
    spec: AdapterSpec,
    source: LegacyTableSpec,
) -> tuple[set[int], int, int]:
    database_path = _validate_legacy_source(legacy_root, source)
    if database_path is None:
        return set(), 0, 0
    promoted: set[int] = set()
    promoted_decisions = 0
    rejected_decisions = 0
    for row in _iter_legacy_rows(database_path, source):
        primary_key = _legacy_primary_key(row, source.primary_key_columns)
        lineage_id = _legacy_lineage_id(
            core,
            source=source,
            primary_key=primary_key,
        )
        raw_path = row.get(source.object_path_column)
        object_path = _safe_object_path(raw_path)
        if not object_path:
            _record_decision(
                core,
                spec=spec,
                rule_id="legacy.object-path.v1",
                source_mode=LEGACY_SOURCE_MODE,
                object_path=str(raw_path or ""),
                property_name="",
                decision_status="LEGACY_UNVERIFIED",
                reason_code="INVALID_OBJECT_PATH",
                source_fact_id=None,
                semantic_fact_id=None,
                legacy_lineage_id=lineage_id,
                source_revision_id=None,
                evidence_uri="",
                generated_at=generated_at,
            )
            rejected_decisions += 1
            continue
        if lineage_id is None:
            _record_decision(
                core,
                spec=spec,
                rule_id="legacy.lineage.v1",
                source_mode=LEGACY_SOURCE_MODE,
                object_path=object_path,
                property_name=str(
                    row.get(source.property_column or "") or ""
                ),
                decision_status="LEGACY_UNVERIFIED",
                reason_code="LEGACY_LINEAGE_MISSING",
                source_fact_id=None,
                semantic_fact_id=None,
                legacy_lineage_id=None,
                source_revision_id=None,
                evidence_uri="",
                generated_at=generated_at,
            )
            rejected_decisions += 1
            continue
        if source.reject_all_reason:
            _record_decision(
                core,
                spec=spec,
                rule_id="legacy.reject-reviewed.v1",
                source_mode=LEGACY_SOURCE_MODE,
                object_path=object_path,
                property_name="",
                decision_status="LEGACY_UNVERIFIED",
                reason_code=source.reject_all_reason,
                source_fact_id=None,
                semantic_fact_id=None,
                legacy_lineage_id=lineage_id,
                source_revision_id=None,
                evidence_uri="",
                generated_at=generated_at,
            )
            rejected_decisions += 1
            continue
        property_name = str(row.get(source.property_column or "") or "")
        rule = _matching_rule(source.rules, property_name)
        if rule is None:
            continue
        entity_id = _entity_id(core, object_path)
        if entity_id is None:
            _record_decision(
                core,
                spec=spec,
                rule_id=rule.rule_id,
                source_mode=LEGACY_SOURCE_MODE,
                object_path=object_path,
                property_name=property_name,
                decision_status="LEGACY_UNVERIFIED",
                reason_code="OBJECT_PATH_NOT_RESOLVED",
                source_fact_id=None,
                semantic_fact_id=None,
                legacy_lineage_id=lineage_id,
                source_revision_id=None,
                evidence_uri="",
                generated_at=generated_at,
            )
            rejected_decisions += 1
            continue
        expected, payload_reason = _legacy_payload(
            row,
            source=source,
            property_name=property_name,
        )
        if payload_reason:
            _record_decision(
                core,
                spec=spec,
                rule_id=rule.rule_id,
                source_mode=LEGACY_SOURCE_MODE,
                object_path=object_path,
                property_name=property_name,
                decision_status="LEGACY_UNVERIFIED",
                reason_code=payload_reason,
                source_fact_id=None,
                semantic_fact_id=None,
                legacy_lineage_id=lineage_id,
                source_revision_id=None,
                evidence_uri="",
                generated_at=generated_at,
            )
            rejected_decisions += 1
            continue
        if not _confidence_at_least(
            row.get("confidence"),
            rule.minimum_confidence,
        ):
            _record_decision(
                core,
                spec=spec,
                rule_id=rule.rule_id,
                source_mode=LEGACY_SOURCE_MODE,
                object_path=object_path,
                property_name=property_name,
                decision_status="LEGACY_UNVERIFIED",
                reason_code="LEGACY_CONFIDENCE_TOO_LOW",
                source_fact_id=None,
                semantic_fact_id=None,
                legacy_lineage_id=lineage_id,
                source_revision_id=None,
                evidence_uri="",
                generated_at=generated_at,
            )
            rejected_decisions += 1
            continue
        resolution = _resolve_source_fact(
            core,
            entity_id=entity_id,
            property_name=property_name,
            rule=rule,
            ontology_version=ontology.version,
            expected=expected,
            require_expected_match=True,
        )
        if resolution.resolved is None:
            _record_decision(
                core,
                spec=spec,
                rule_id=rule.rule_id,
                source_mode=LEGACY_SOURCE_MODE,
                object_path=object_path,
                property_name=property_name,
                decision_status="LEGACY_UNVERIFIED",
                reason_code=resolution.reason_code,
                source_fact_id=resolution.source_fact_id,
                semantic_fact_id=None,
                legacy_lineage_id=lineage_id,
                source_revision_id=resolution.source_revision_id,
                evidence_uri=resolution.evidence_uri,
                generated_at=generated_at,
            )
            rejected_decisions += 1
            continue
        semantic_fact_id = _promote(
            core,
            ontology=ontology,
            rule=rule,
            resolved=resolution.resolved,
        )
        promoted.add(semantic_fact_id)
        _record_decision(
            core,
            spec=spec,
            rule_id=rule.rule_id,
            source_mode=LEGACY_SOURCE_MODE,
            object_path=object_path,
            property_name=property_name,
            decision_status="PROMOTED",
            reason_code=("VERIFIED_PARTIAL" if rule.partial else "VERIFIED"),
            source_fact_id=resolution.source_fact_id,
            semantic_fact_id=semantic_fact_id,
            legacy_lineage_id=lineage_id,
            source_revision_id=resolution.source_revision_id,
            evidence_uri=resolution.evidence_uri,
            generated_at=generated_at,
        )
        if lineage_id is not None:
            core.execute(
                """
                UPDATE legacy_lineage
                SET status='SEMANTICALLY_VERIFIED'
                WHERE lineage_id=?
                """,
                (lineage_id,),
            )
        promoted_decisions += 1
    return promoted, promoted_decisions, rejected_decisions


def _direct_candidates(
    core: sqlite3.Connection,
    rule: SemanticRule,
) -> list[tuple[int, int, str, str]]:
    placeholders = ",".join("?" for _ in rule.source_properties)
    return [
        (int(row[0]), int(row[1]), str(row[2]), str(row[3]))
        for row in core.execute(
            f"""
            SELECT
                fact.fact_id, fact.subject_entity_id,
                entity.canonical_uri, fact.fact_name
            FROM facts AS fact
            JOIN entities AS entity
              ON entity.entity_id=fact.subject_entity_id
            WHERE fact.current=1
              AND fact.fact_type='DECLARED_DEFAULT'
              AND fact.fact_name IN ({placeholders})
            ORDER BY fact.fact_id
            """,
            rule.source_properties,
        )
    ]


def _process_direct_rules(
    core: sqlite3.Connection,
    *,
    legacy_root: Path,
    ontology: OntologyBundle,
    generated_at: str,
    spec: AdapterSpec,
) -> tuple[set[int], int, int]:
    if not spec.direct_rules:
        return set(), 0, 0
    anchor = spec.lineage_anchor
    anchor_available = bool(
        anchor is not None
        and _validate_lineage_anchor(legacy_root, anchor) is not None
    )
    promoted: set[int] = set()
    promoted_decisions = 0
    rejected_decisions = 0
    seen_source_facts: set[int] = set()
    for rule in spec.direct_rules:
        for fact_id, entity_id, raw_path, property_name in _direct_candidates(
            core,
            rule,
        ):
            if fact_id in seen_source_facts:
                continue
            seen_source_facts.add(fact_id)
            object_path = _safe_object_path(raw_path)
            lineage_id = (
                _anchor_lineage_id(
                    core,
                    anchor=anchor,
                    entity_id=entity_id,
                    object_path=object_path,
                )
                if anchor_available and anchor is not None and object_path
                else None
            )
            reason = "" if object_path else "INVALID_OBJECT_PATH"
            if reason:
                _record_decision(
                    core,
                    spec=spec,
                    rule_id=rule.rule_id,
                    source_mode=DIRECT_SOURCE_MODE,
                    object_path=raw_path,
                    property_name=property_name,
                    decision_status="LEGACY_UNVERIFIED",
                    reason_code=reason,
                    source_fact_id=fact_id,
                    semantic_fact_id=None,
                    legacy_lineage_id=lineage_id,
                    source_revision_id=None,
                    evidence_uri="",
                    generated_at=generated_at,
                )
                rejected_decisions += 1
                continue
            resolution = _resolve_source_fact(
                core,
                entity_id=entity_id,
                property_name=property_name,
                rule=rule,
                ontology_version=ontology.version,
                expected=None,
                require_expected_match=False,
            )
            if resolution.resolved is None:
                _record_decision(
                    core,
                    spec=spec,
                    rule_id=rule.rule_id,
                    source_mode=DIRECT_SOURCE_MODE,
                    object_path=object_path,
                    property_name=property_name,
                    decision_status="LEGACY_UNVERIFIED",
                    reason_code=resolution.reason_code,
                    source_fact_id=resolution.source_fact_id or fact_id,
                    semantic_fact_id=None,
                    legacy_lineage_id=lineage_id,
                    source_revision_id=resolution.source_revision_id,
                    evidence_uri=resolution.evidence_uri,
                    generated_at=generated_at,
                )
                rejected_decisions += 1
                continue
            semantic_fact_id = _promote(
                core,
                ontology=ontology,
                rule=rule,
                resolved=resolution.resolved,
            )
            promoted.add(semantic_fact_id)
            _record_decision(
                core,
                spec=spec,
                rule_id=rule.rule_id,
                source_mode=DIRECT_SOURCE_MODE,
                object_path=object_path,
                property_name=property_name,
                decision_status="PROMOTED",
                reason_code=(
                    "VERIFIED_PARTIAL" if rule.partial else "VERIFIED"
                ),
                source_fact_id=resolution.source_fact_id,
                semantic_fact_id=semantic_fact_id,
                legacy_lineage_id=lineage_id,
                source_revision_id=resolution.source_revision_id,
                evidence_uri=resolution.evidence_uri,
                generated_at=generated_at,
            )
            promoted_decisions += 1
    return promoted, promoted_decisions, rejected_decisions


def _revoke_previous_promotions(
    core: sqlite3.Connection,
    *,
    adapter_ids: Sequence[str],
) -> None:
    if not adapter_ids:
        return
    placeholders = ",".join("?" for _ in adapter_ids)
    old_fact_ids = {
        int(row[0])
        for row in core.execute(
            f"""
            SELECT DISTINCT semantic_fact_id
            FROM semantic_adapter_decisions
            WHERE adapter_id IN ({placeholders})
              AND decision_status='PROMOTED'
              AND semantic_fact_id IS NOT NULL
            """,
            tuple(adapter_ids),
        )
    }
    old_lineage_ids = {
        int(row[0])
        for row in core.execute(
            f"""
            SELECT DISTINCT legacy_lineage_id
            FROM semantic_adapter_decisions
            WHERE adapter_id IN ({placeholders})
              AND decision_status='PROMOTED'
              AND legacy_lineage_id IS NOT NULL
            """,
            tuple(adapter_ids),
        )
    }
    core.execute(
        f"DELETE FROM semantic_adapter_runs "
        f"WHERE adapter_id IN ({placeholders})",
        tuple(adapter_ids),
    )
    core.execute(
        f"DELETE FROM semantic_adapter_decisions "
        f"WHERE adapter_id IN ({placeholders})",
        tuple(adapter_ids),
    )
    for fact_id in old_fact_ids:
        remaining_owner = core.execute(
            """
            SELECT 1
            FROM semantic_adapter_decisions
            WHERE semantic_fact_id=?
              AND decision_status='PROMOTED'
            LIMIT 1
            """,
            (fact_id,),
        ).fetchone()
        if remaining_owner is None:
            core.execute(
                "UPDATE facts SET current=0 WHERE fact_id=?",
                (fact_id,),
            )
    for lineage_id in old_lineage_ids:
        remaining_owner = core.execute(
            """
            SELECT 1
            FROM semantic_adapter_decisions
            WHERE legacy_lineage_id=?
              AND decision_status='PROMOTED'
            LIMIT 1
            """,
            (lineage_id,),
        ).fetchone()
        if remaining_owner is None:
            core.execute(
                """
                UPDATE legacy_lineage
                SET status='LEGACY_UNVERIFIED'
                WHERE lineage_id=?
                """,
                (lineage_id,),
            )


def _validate_promoted_decisions(
    core: sqlite3.Connection,
    *,
    spec: AdapterSpec,
    ontology_version: str,
) -> None:
    allowed_modes: dict[str, set[str]] = {}
    for source in spec.legacy_sources:
        for rule in source.rules:
            allowed_modes.setdefault(rule.rule_id, set()).add(
                LEGACY_SOURCE_MODE
            )
    for rule in spec.direct_rules:
        allowed_modes.setdefault(rule.rule_id, set()).add(DIRECT_SOURCE_MODE)
    rule_ids = set(allowed_modes)
    promoted_count = int(
        core.execute(
            """
            SELECT COUNT(*)
            FROM semantic_adapter_decisions
            WHERE adapter_id=? AND decision_status='PROMOTED'
            """,
            (spec.adapter_id,),
        ).fetchone()[0]
    )
    if promoted_count == 0:
        return
    if not rule_ids or not spec.output_fact_types:
        raise ValueError(
            f"{spec.adapter_id} produced facts without declared output rules"
        )
    for rule_id, source_mode, lineage_id in core.execute(
        """
        SELECT rule_id, source_mode, legacy_lineage_id
        FROM semantic_adapter_decisions
        WHERE adapter_id=? AND decision_status='PROMOTED'
        """,
        (spec.adapter_id,),
    ):
        if str(source_mode) not in allowed_modes.get(str(rule_id), set()):
            raise ValueError(
                f"{spec.adapter_id}.{rule_id} used invalid source mode "
                f"{source_mode}"
            )
        if source_mode == LEGACY_SOURCE_MODE and lineage_id is None:
            raise ValueError(
                f"{spec.adapter_id}.{rule_id} omitted legacy lineage"
            )
    rule_placeholders = ",".join("?" for _ in rule_ids)
    fact_placeholders = ",".join("?" for _ in spec.output_fact_types)
    invalid = int(
        core.execute(
            f"""
            SELECT COUNT(*)
            FROM semantic_adapter_decisions AS decision
            LEFT JOIN facts AS source
              ON source.fact_id=decision.source_fact_id
            LEFT JOIN facts AS semantic
              ON semantic.fact_id=decision.semantic_fact_id
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=decision.source_revision_id
            WHERE decision.adapter_id=?
              AND decision.decision_status='PROMOTED'
              AND (
                  decision.adapter_version<>?
                  OR decision.rule_id NOT IN ({rule_placeholders})
                  OR source.fact_id IS NULL
                  OR source.current<>1
                  OR source.fact_type<>'DECLARED_DEFAULT'
                  OR source.fact_name<>decision.property_name
                  OR source.ontology_version<>?
                  OR semantic.fact_id IS NULL
                  OR semantic.current<>1
                  OR semantic.fact_type NOT IN ({fact_placeholders})
                  OR semantic.scope_kind<>'DERIVED_STATIC'
                  OR semantic.ontology_version<>?
                  OR semantic.subject_entity_id<>source.subject_entity_id
                  OR semantic.fact_name<>source.fact_name
                  OR semantic.declared_on_entity_id
                     IS NOT source.declared_on_entity_id
                  OR semantic.value_kind<>source.value_kind
                  OR semantic.value_text IS NOT source.value_text
                  OR semantic.value_number IS NOT source.value_number
                  OR semantic.value_integer IS NOT source.value_integer
                  OR semantic.value_json IS NOT source.value_json
                  OR semantic.unit<>source.unit
                  OR semantic.status<>source.status
                  OR semantic.confidence<>source.confidence
                  OR revision.revision_id IS NULL
                  OR revision.source_kind<>'blueprint_evidence'
                  OR revision.schema_version<>'ark.blueprint.evidence.v2'
                  OR UPPER(revision.freshness_status)<>'FRESH'
                  OR NOT EXISTS (
                      SELECT 1
                      FROM fact_evidence AS source_evidence
                      WHERE source_evidence.fact_id=source.fact_id
                        AND source_evidence.source_revision_id=
                            decision.source_revision_id
                        AND source_evidence.evidence_uri=
                            decision.evidence_uri
                        AND source_evidence.evidence_role=
                            'DEFAULT_VALUE_ACTUAL'
                  )
                  OR NOT EXISTS (
                      SELECT 1
                      FROM fact_evidence AS semantic_evidence
                      WHERE semantic_evidence.fact_id=semantic.fact_id
                        AND semantic_evidence.source_revision_id=
                            decision.source_revision_id
                        AND semantic_evidence.evidence_uri=
                            decision.evidence_uri
                        AND semantic_evidence.evidence_role=
                            'SEMANTIC_ADAPTER:' || decision.rule_id
                  )
              )
            """,
            (
                spec.adapter_id,
                spec.adapter_version,
                *sorted(rule_ids),
                ontology_version,
                *spec.output_fact_types,
                ontology_version,
            ),
        ).fetchone()[0]
    )
    if invalid:
        raise ValueError(
            f"{spec.adapter_id} produced {invalid} invalid semantic decisions"
        )


def materialize_semantic_adapters(
    *,
    core: sqlite3.Connection,
    legacy_root: Path,
    ontology: OntologyBundle,
    generated_at: str,
    adapter_specs: Sequence[AdapterSpec],
    adapter_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    """Promote only exact, usable, FRESH, class-guarded semantic facts."""

    selected_ids = (
        {str(value) for value in adapter_ids}
        if adapter_ids is not None
        else {spec.adapter_id for spec in adapter_specs}
    )
    known_ids = {spec.adapter_id for spec in adapter_specs}
    unknown = selected_ids - known_ids
    if unknown:
        raise ValueError(f"Unknown semantic adapters: {sorted(unknown)}")
    selected = [
        spec for spec in adapter_specs if spec.adapter_id in selected_ids
    ]
    legacy_root = legacy_root.resolve()
    core.execute("SAVEPOINT semantic_adapter_materialization")
    try:
        _revoke_previous_promotions(
            core,
            adapter_ids=tuple(spec.adapter_id for spec in selected),
        )
        all_promoted: set[int] = set()
        by_adapter: dict[str, dict[str, int | str]] = {}
        for spec in selected:
            promoted: set[int] = set()
            promoted_decisions = 0
            rejected_decisions = 0
            for source in spec.legacy_sources:
                source_promoted, source_yes, source_no = (
                    _process_legacy_source(
                        core,
                        legacy_root=legacy_root,
                        ontology=ontology,
                        generated_at=generated_at,
                        spec=spec,
                        source=source,
                    )
                )
                promoted.update(source_promoted)
                promoted_decisions += source_yes
                rejected_decisions += source_no
            direct_promoted, direct_yes, direct_no = _process_direct_rules(
                core,
                legacy_root=legacy_root,
                ontology=ontology,
                generated_at=generated_at,
                spec=spec,
            )
            promoted.update(direct_promoted)
            promoted_decisions += direct_yes
            rejected_decisions += direct_no
            all_promoted.update(promoted)
            _validate_promoted_decisions(
                core,
                spec=spec,
                ontology_version=ontology.version,
            )
            validation_status = "VALID"
            core.execute(
                """
                INSERT INTO semantic_adapter_runs(
                    adapter_id, adapter_version, built_at,
                    promoted_fact_count, promoted_decision_count,
                    rejected_decision_count, validation_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.adapter_id,
                    spec.adapter_version,
                    generated_at,
                    len(promoted),
                    promoted_decisions,
                    rejected_decisions,
                    validation_status,
                ),
            )
            by_adapter[spec.adapter_id] = {
                "adapterVersion": spec.adapter_version,
                "promotedFacts": len(promoted),
                "promotedDecisions": promoted_decisions,
                "rejectedDecisions": rejected_decisions,
                "validationStatus": validation_status,
            }
        core.execute("RELEASE SAVEPOINT semantic_adapter_materialization")
        return {
            "adapters": len(selected),
            "promotedFacts": len(all_promoted),
            "promotedDecisions": sum(
                int(value["promotedDecisions"])
                for value in by_adapter.values()
            ),
            "rejectedDecisions": sum(
                int(value["rejectedDecisions"])
                for value in by_adapter.values()
            ),
            "byAdapter": by_adapter,
        }
    except Exception:
        core.execute("ROLLBACK TO SAVEPOINT semantic_adapter_materialization")
        core.execute("RELEASE SAVEPOINT semantic_adapter_materialization")
        raise
