"""Evidence-backed declared and effective fact materialization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping

from .ontology import OntologyBundle


FACT_STORE_VERSION = "ark-kb-facts/v1"
MISSING_STATUSES = {
    "UNKNOWN",
    "NOT_RECOVERED",
    "SOURCE_NOT_AVAILABLE",
}
CONFIRMED_STATUSES = {
    "CONFIRMED",
    "VERIFIED",
    "RESOLVED",
    "CONFIRMED_FINGERPRINT_ONLY",
    "CONFIRMED_EMPTY",
}


@dataclass(frozen=True)
class FactValue:
    value_kind: str
    value_text: str | None = None
    value_number: float | None = None
    value_integer: int | None = None
    value_json: str | None = None

    def normalized_payload(self) -> tuple[object, ...]:
        return (
            self.value_kind,
            self.value_text,
            self.value_number,
            self.value_integer,
            self.value_json,
        )


def _canonical_fact_key(
    *,
    subject_entity_id: int,
    fact_type: str,
    fact_name: str,
    scope_kind: str,
    declared_on_entity_id: int | None,
    value: FactValue,
    unit: str,
    status: str,
) -> str:
    payload = json.dumps(
        [
            subject_entity_id,
            fact_type,
            fact_name,
            scope_kind,
            declared_on_entity_id,
            *value.normalized_payload(),
            unit,
            status,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "fact://" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_missing_value(status: str, value: FactValue) -> None:
    if status not in MISSING_STATUSES:
        return
    if value.value_kind != "UNKNOWN" or any(
        item is not None
        for item in (
            value.value_text,
            value.value_number,
            value.value_integer,
            value.value_json,
        )
    ):
        raise ValueError(
            f"{status} must use UNKNOWN with no zero or empty placeholder"
        )


def store_fact(
    connection: sqlite3.Connection,
    *,
    ontology: OntologyBundle,
    subject_entity_id: int,
    fact_type: str,
    fact_name: str,
    scope_kind: str,
    declared_on_entity_id: int | None,
    value: FactValue,
    unit: str = "",
    status: str,
    confidence: str,
    source_revision_id: int,
    evidence_uri: str,
    evidence_role: str,
) -> int:
    """Store one canonical fact and merge all independent evidence pointers."""

    fact_type = fact_type.upper()
    scope_kind = scope_kind.upper()
    status = status.upper()
    if fact_type not in ontology.fact_types:
        raise ValueError(f"Unknown fact type: {fact_type}")
    if scope_kind not in ontology.scope_kinds:
        raise ValueError(f"Unknown fact scope: {scope_kind}")
    if not fact_name:
        raise ValueError("fact_name is required")
    if not evidence_uri:
        raise ValueError("Every fact requires an evidence URI")
    _validate_missing_value(status, value)
    canonical_key = _canonical_fact_key(
        subject_entity_id=subject_entity_id,
        fact_type=fact_type,
        fact_name=fact_name,
        scope_kind=scope_kind,
        declared_on_entity_id=declared_on_entity_id,
        value=value,
        unit=unit,
        status=status,
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO facts(
            subject_entity_id, fact_type, fact_name, scope_kind,
            declared_on_entity_id, value_kind, value_text, value_number,
            value_integer, value_json, unit, status, confidence,
            ontology_version, current, canonical_fact_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            subject_entity_id,
            fact_type,
            fact_name,
            scope_kind,
            declared_on_entity_id,
            value.value_kind,
            value.value_text,
            value.value_number,
            value.value_integer,
            value.value_json,
            unit,
            status,
            confidence,
            ontology.version,
            canonical_key,
        ),
    )
    fact_id = int(
        connection.execute(
            "SELECT fact_id FROM facts WHERE canonical_fact_key=?",
            (canonical_key,),
        ).fetchone()[0]
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO fact_evidence(
            fact_id, source_revision_id, evidence_uri, evidence_role
        ) VALUES (?, ?, ?, ?)
        """,
        (fact_id, source_revision_id, evidence_uri, evidence_role),
    )
    return fact_id


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name=?
            """,
            (table,),
        ).fetchone()
        is not None
    )


def _declared_value(row: Mapping[str, object]) -> tuple[FactValue, str]:
    status = str(row.get("value_status") or "UNKNOWN").upper()
    if status in MISSING_STATUSES:
        return FactValue("UNKNOWN"), status
    if status == "STALE":
        return FactValue("UNKNOWN"), status
    if status == "CONFIRMED_EMPTY":
        return FactValue("CONFIRMED_EMPTY"), status
    fingerprint = str(row.get("value_fingerprint") or "").strip()
    if (
        status == "CONFIRMED_FINGERPRINT_ONLY"
        and int(row.get("has_value") or 0) == 1
        and fingerprint
    ):
        return FactValue("FINGERPRINT", value_text=fingerprint), status
    return FactValue("UNKNOWN"), "NOT_RECOVERED"


def materialize_declared_defaults(
    discovery: sqlite3.Connection,
    core: sqlite3.Connection,
    *,
    ontology: OntologyBundle,
    source_revision_id: int,
) -> dict[str, int]:
    """Import the bounded default surface without inventing decoded values."""

    if not _table_exists(discovery, "default_property_surface"):
        return {
            "declaredFacts": 0,
            "factEvidence": 0,
            "notRecoveredFacts": 0,
        }
    discovery.row_factory = sqlite3.Row
    entity_ids = {
        str(uri): int(entity_id)
        for uri, entity_id in core.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    cursor = discovery.execute(
        """
        SELECT
            asset_object_path, property_name, property_type,
            has_value, value_status, value_fingerprint,
            source_evidence_id, confidence
        FROM default_property_surface
        ORDER BY asset_object_path, property_name, surface_id
        """
    )
    imported: set[int] = set()
    not_recovered = 0
    for batch in iter(lambda: cursor.fetchmany(10_000), []):
        for source in batch:
            row = dict(source)
            entity_id = entity_ids.get(str(row["asset_object_path"]))
            if entity_id is None:
                continue
            value, status = _declared_value(row)
            if status in MISSING_STATUSES:
                not_recovered += 1
            fact_id = store_fact(
                core,
                ontology=ontology,
                subject_entity_id=entity_id,
                fact_type="DECLARED_DEFAULT",
                fact_name=str(row["property_name"]),
                scope_kind="DECLARED",
                declared_on_entity_id=entity_id,
                value=value,
                status=status,
                confidence=str(row["confidence"] or "UNKNOWN").upper(),
                source_revision_id=source_revision_id,
                evidence_uri=str(row["source_evidence_id"]),
                evidence_role=(
                    "DEFAULT_VALUE_FINGERPRINT"
                    if value.value_kind == "FINGERPRINT"
                    else "DEFAULT_VALUE_GAP"
                ),
            )
            imported.add(fact_id)
    core.commit()
    evidence_count = int(
        core.execute(
            """
            SELECT COUNT(*) FROM fact_evidence AS e
            JOIN facts AS f ON f.fact_id=e.fact_id
            WHERE f.fact_type='DECLARED_DEFAULT'
            """
        ).fetchone()[0]
    )
    return {
        "declaredFacts": len(imported),
        "factEvidence": evidence_count,
        "notRecoveredFacts": not_recovered,
    }


def _effective_source_revision_hash(
    connection: sqlite3.Connection, fact_id: int
) -> str:
    revisions = [
        int(row[0])
        for row in connection.execute(
            """
            SELECT source_revision_id FROM fact_evidence
            WHERE fact_id=? ORDER BY source_revision_id, evidence_uri
            """,
            (fact_id,),
        )
    ]
    return hashlib.sha256(
        json.dumps(revisions, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _affected_entity_ids(
    connection: sqlite3.Connection,
    changed_class_ids: Iterable[int] | None,
) -> set[int]:
    if changed_class_ids is None:
        return {
            int(row[0])
            for row in connection.execute(
                """
                SELECT entity_id FROM asset_class_assignments
                WHERE assignment_kind='GENERATED_CLASS'
                """
            )
        }
    changed = {int(value) for value in changed_class_ids}
    if not changed:
        return set()
    placeholders = ",".join("?" for _ in changed)
    return {
        int(row[0])
        for row in connection.execute(
            f"""
            SELECT DISTINCT assignment.entity_id
            FROM asset_class_assignments AS assignment
            JOIN class_closure AS closure
              ON closure.descendant_class_id=assignment.class_id
            WHERE assignment.assignment_kind='GENERATED_CLASS'
              AND closure.ancestor_class_id IN ({placeholders})
            """,
            tuple(sorted(changed)),
        )
    }


def materialize_effective_defaults(
    connection: sqlite3.Connection,
    *,
    changed_class_ids: Iterable[int] | None = None,
) -> dict[str, int]:
    """Resolve nearest declared defaults along confirmed class ancestry."""

    affected = _affected_entity_ids(connection, changed_class_ids)
    if not affected:
        return {"effectiveFacts": 0, "affectedEntities": 0}
    placeholders = ",".join("?" for _ in affected)
    values = tuple(sorted(affected))
    connection.execute(
        f"DELETE FROM effective_facts WHERE entity_id IN ({placeholders})",
        values,
    )
    generated_class_by_entity = {
        int(entity_id): int(class_id)
        for entity_id, class_id in connection.execute(
            f"""
            SELECT entity_id, class_id
            FROM asset_class_assignments
            WHERE assignment_kind='GENERATED_CLASS'
              AND entity_id IN ({placeholders})
            """,
            values,
        )
    }
    class_entities: dict[int, list[int]] = {}
    for entity_id, class_id in connection.execute(
        """
        SELECT entity_id, class_id FROM asset_class_assignments
        WHERE assignment_kind='GENERATED_CLASS'
        ORDER BY entity_id
        """
    ):
        class_entities.setdefault(int(class_id), []).append(int(entity_id))
    fact_rows_by_entity: dict[int, dict[str, tuple[object, ...]]] = {}
    for row in connection.execute(
        """
        SELECT
            fact_id, subject_entity_id, fact_name, value_kind, status
        FROM facts
        WHERE fact_type='DECLARED_DEFAULT'
          AND scope_kind='DECLARED'
          AND current=1
        ORDER BY
            subject_entity_id, fact_name,
            CASE
              WHEN status IN (
                'CONFIRMED', 'VERIFIED', 'RESOLVED',
                'CONFIRMED_EMPTY', 'CONFIRMED_FINGERPRINT_ONLY'
              ) THEN 0
              WHEN status='STALE' THEN 2
              ELSE 1
            END,
            fact_id DESC
        """
    ):
        entity_id = int(row[1])
        fact_rows_by_entity.setdefault(entity_id, {}).setdefault(
            str(row[2]), tuple(row)
        )
    inserted = 0
    for entity_id in sorted(affected):
        class_id = generated_class_by_entity.get(entity_id)
        if class_id is None:
            continue
        ancestry = [
            (int(ancestor_id), int(depth), str(path_status))
            for ancestor_id, depth, path_status in connection.execute(
                """
                SELECT ancestor_class_id, depth, path_status
                FROM class_closure
                WHERE descendant_class_id=?
                ORDER BY depth, ancestor_class_id
                """,
                (class_id,),
            )
        ]
        chain = [
            {
                "classId": ancestor_id,
                "depth": depth,
                "pathStatus": path_status,
            }
            for ancestor_id, depth, path_status in ancestry
        ]
        property_names: set[str] = set()
        candidates: list[tuple[int, int, str, tuple[object, ...]]] = []
        for ancestor_id, depth, path_status in ancestry:
            for owner_id in class_entities.get(ancestor_id, ()):
                for name, fact_row in fact_rows_by_entity.get(
                    owner_id, {}
                ).items():
                    property_names.add(name)
                    candidates.append(
                        (depth, owner_id, path_status, fact_row)
                    )
        for name in sorted(property_names):
            choices = [
                item
                for item in candidates
                if str(item[3][2]) == name
            ]
            choices.sort(key=lambda item: (item[0], item[1]))
            nearest_depth = choices[0][0]
            nearest = [
                item for item in choices if item[0] == nearest_depth
            ]
            chosen = next(
                (
                    item
                    for item in nearest
                    if str(item[3][4]).upper() in CONFIRMED_STATUSES
                ),
                nearest[0],
            )
            depth, owner_id, path_status, fact_row = chosen
            fact_id = int(fact_row[0])
            source_status = str(fact_row[4]).upper()
            if path_status not in {"SELF", "CONFIRMED"}:
                resolution_status = "AMBIGUOUS_INHERITANCE"
            elif source_status == "CONFIRMED_FINGERPRINT_ONLY":
                resolution_status = "FINGERPRINT_ONLY"
            elif source_status in MISSING_STATUSES:
                resolution_status = source_status
            elif source_status == "STALE":
                resolution_status = "STALE"
            else:
                resolution_status = "RESOLVED"
            resolution_chain = {
                "classes": chain,
                "declaredOnEntityId": owner_id,
                "overrideDepth": depth,
            }
            connection.execute(
                """
                INSERT INTO effective_facts(
                    entity_id, fact_type, fact_name, fact_id,
                    inherited_from_entity_id, resolution_chain_json,
                    resolution_status, source_revision_set_hash
                ) VALUES (?, 'EFFECTIVE_DEFAULT', ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    name,
                    fact_id,
                    None if owner_id == entity_id else owner_id,
                    json.dumps(
                        resolution_chain,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    resolution_status,
                    _effective_source_revision_hash(connection, fact_id),
                ),
            )
            inserted += 1
    connection.commit()
    return {
        "effectiveFacts": inserted,
        "affectedEntities": len(affected),
    }
