"""Evidence-backed declared and effective fact materialization."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .class_hierarchy import (
    CONFIRMED_ASSIGNMENT_CONFIDENCE,
    CONFIRMED_ASSIGNMENT_STATUSES,
    CONFIRMED_CLASS_CONFIDENCE,
    CONFIRMED_CLASS_STATUSES,
)
from .ontology import OntologyBundle


FACT_STORE_VERSION = "ark-kb-facts/v2"
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
RevisionIdentity = tuple[str, str, str, str, str, str, str]


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
    allowed_value_kinds = ontology.fact_value_kinds.get(fact_type, ())
    if value.value_kind not in allowed_value_kinds:
        raise ValueError(
            f"{fact_type} does not allow value kind {value.value_kind}"
        )
    if scope_kind not in ontology.scope_kinds:
        raise ValueError(f"Unknown fact scope: {scope_kind}")
    if not fact_name.strip():
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
    covered_properties: Iterable[tuple[str, str]] | None = None,
    freshness_gap_assets: Iterable[str] | None = None,
    untrusted_assets: Iterable[str] | None = None,
) -> dict[str, int]:
    """Import the bounded default surface without inventing decoded values."""

    if not _table_exists(discovery, "default_property_surface"):
        return {
            "declaredFacts": 0,
            "factEvidence": 0,
            "notRecoveredFacts": 0,
            "invalidIdentityRows": 0,
        }
    discovery.row_factory = sqlite3.Row
    entity_ids = {
        str(uri): int(entity_id)
        for uri, entity_id in core.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    covered = set(covered_properties or ())
    freshness_gaps = set(freshness_gap_assets or ())
    untrusted = set(untrusted_assets or ())
    asset_columns = (
        {
            str(column[1])
            for column in discovery.execute('PRAGMA table_info("assets")')
        }
        if _table_exists(discovery, "assets")
        else set()
    )
    freshness_projection = (
        """
        , (
            SELECT CASE
                WHEN COUNT(*)=1 THEN MAX(asset.evidence_freshness)
                ELSE 'AMBIGUOUS'
            END
            FROM assets AS asset
            WHERE asset.object_path=surface.asset_object_path
          ) AS evidence_freshness
        """
        if {"object_path", "evidence_freshness"}.issubset(asset_columns)
        else ", '' AS evidence_freshness"
    )
    cursor = discovery.execute(
        f"""
        SELECT
            surface.asset_object_path, surface.property_name,
            surface.property_type, surface.has_value,
            surface.value_status, surface.value_fingerprint,
            surface.source_evidence_id, surface.confidence
            {freshness_projection}
        FROM default_property_surface AS surface
        ORDER BY
            surface.asset_object_path,
            surface.property_name,
            surface.surface_id
        """
    )
    imported: set[int] = set()
    not_recovered = 0
    invalid_identity_rows = 0
    for batch in iter(lambda: cursor.fetchmany(10_000), []):
        for source in batch:
            row = dict(source)
            property_name = str(row["property_name"])
            if not property_name.strip():
                invalid_identity_rows += 1
                continue
            property_key = (
                str(row["asset_object_path"]),
                property_name,
            )
            if property_key in covered:
                continue
            entity_id = entity_ids.get(str(row["asset_object_path"]))
            if entity_id is None:
                continue
            freshness = str(row.get("evidence_freshness") or "").upper()
            if str(row["asset_object_path"]) in freshness_gaps:
                row["value_status"] = "STALE"
            elif str(row["asset_object_path"]) in untrusted:
                row["value_status"] = "NOT_RECOVERED"
            elif freshness == "STALE":
                row["value_status"] = "STALE"
            elif freshness in {
                "SOURCE_NOT_AVAILABLE",
                "NOT_AVAILABLE",
            }:
                row["value_status"] = "SOURCE_NOT_AVAILABLE"
            elif freshness != "FRESH" and str(
                row.get("value_status") or ""
            ).upper() not in {
                "NOT_RECOVERED",
                "SOURCE_NOT_AVAILABLE",
                "STALE",
                "UNKNOWN",
            }:
                row["value_status"] = "NOT_RECOVERED"
            value, status = _declared_value(row)
            if status in MISSING_STATUSES:
                not_recovered += 1
            fact_id = store_fact(
                core,
                ontology=ontology,
                subject_entity_id=entity_id,
                fact_type="DECLARED_DEFAULT",
                fact_name=property_name,
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
        "invalidIdentityRows": invalid_identity_rows,
    }


_USABLE_EFFECTIVE_STATUSES = {
    "CONFIRMED",
    "VERIFIED",
    "RESOLVED",
    "CONFIRMED_EMPTY",
}
_CONFIRMED_PATH_STATUSES = {"CONFIRMED", "VERIFIED", "RESOLVED"}
_CONFIRMED_PATH_CONFIDENCE = {"HIGH", "CONFIRMED"}
_INHERITANCE_EDGE_KINDS = {"blueprint_parent", "native_parent", "parent"}
_EFFECTIVE_PATH_SCHEMA = "ark-kb-effective-path/v1"
_NATIVE_ROOT_PROOF_SCHEMA = "ark-kb-native-root-proof/v1"


@dataclass(frozen=True)
class _LogicalClassEdge:
    child_class_id: int
    parent_class_id: int
    edge_kind: str
    evidence_ids: tuple[str, ...]
    revision_identities: tuple[RevisionIdentity, ...]
    confirmed: bool


@dataclass(frozen=True)
class _ClassPath:
    classes: tuple[int, ...]
    edges: tuple[_LogicalClassEdge, ...]


@dataclass(frozen=True)
class _DeclaredCandidate:
    fact_id: int
    owner_entity_id: int
    owner_class_id: int
    fact_name: str
    value_kind: str
    value_text: object
    value_number: object
    value_integer: object
    value_json: object
    status: str
    declared_on_entity_id: int | None
    fresh_revisions: tuple[RevisionIdentity, ...]
    base_rejection_reason: str
    owner_assignment_verified: bool


@dataclass(frozen=True)
class _CandidateResolution:
    candidate: _DeclaredCandidate
    depth: int
    path_status: str
    path: _ClassPath


@dataclass(frozen=True)
class _ClassResolutionContext:
    paths: Mapping[int, tuple[_ClassPath, ...]]
    ambiguity_reasons: tuple[str, ...]
    parent_chain_open: bool
    native_root_path: _ClassPath | None
    native_root_revision: RevisionIdentity | None


def _empty_revision_hash() -> str:
    return hashlib.sha256(b"[]").hexdigest()


def _stable_revision_hash(
    identities: Iterable[RevisionIdentity],
) -> str:
    payload = sorted(set(identities))
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _json_value_is_portable(value: object) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, int):
        return -(2**63) <= value <= 2**63 - 1
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value_is_portable(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _json_value_is_portable(item)
            for key, item in value.items()
        )
    return False


def _typed_value_is_usable(candidate: _DeclaredCandidate) -> bool:
    kind = candidate.value_kind
    text = candidate.value_text
    number = candidate.value_number
    integer = candidate.value_integer
    raw_json = candidate.value_json
    if kind == "CONFIRMED_EMPTY":
        return candidate.status == "CONFIRMED_EMPTY" and all(
            item is None for item in (text, number, integer, raw_json)
        )
    if candidate.status == "CONFIRMED_EMPTY":
        return False
    if kind == "BOOLEAN":
        return (
            integer in {0, 1}
            and text is None
            and number is None
            and raw_json is None
        )
    if kind == "INTEGER":
        return (
            isinstance(integer, int)
            and text is None
            and number is None
            and raw_json is None
        )
    if kind == "NUMBER":
        return (
            isinstance(number, (int, float))
            and not isinstance(number, bool)
            and math.isfinite(float(number))
            and text is None
            and integer is None
            and raw_json is None
        )
    if kind in {"TEXT", "ENTITY_REF"}:
        return (
            isinstance(text, str)
            and number is None
            and integer is None
            and raw_json is None
            and (kind != "ENTITY_REF" or text.startswith("/"))
        )
    if kind == "JSON":
        if (
            not isinstance(raw_json, str)
            or text is not None
            or number is not None
            or integer is not None
        ):
            return False
        try:
            decoded = json.loads(raw_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return _json_value_is_portable(decoded)
    return False


def _fact_rejection_reason(candidate: _DeclaredCandidate) -> str:
    if candidate.status not in _USABLE_EFFECTIVE_STATUSES:
        if (
            candidate.status == "CONFIRMED_FINGERPRINT_ONLY"
            or candidate.value_kind == "FINGERPRINT"
        ):
            return "UNUSABLE_VALUE_KIND"
        return "UNUSABLE_FACT_STATUS"
    if not _typed_value_is_usable(candidate):
        return "UNUSABLE_VALUE_KIND"
    if not candidate.fresh_revisions:
        return "NO_FRESH_EVIDENCE"
    if candidate.declared_on_entity_id != candidate.owner_entity_id:
        return "UNUSABLE_FACT_STATUS"
    if not candidate.owner_assignment_verified:
        return "ASSIGNMENT_UNVERIFIED"
    return ""


def _assignment_rows(
    connection: sqlite3.Connection,
) -> dict[int, tuple[tuple[int, str, str], ...]]:
    rows: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
    for entity_id, class_id, status, confidence in connection.execute(
        """
        SELECT entity_id, class_id, status, confidence
        FROM asset_class_assignments
        WHERE assignment_kind='GENERATED_CLASS'
        ORDER BY entity_id, class_id
        """
    ):
        rows[int(entity_id)].append(
            (int(class_id), str(status).upper(), str(confidence).upper())
        )
    return {entity_id: tuple(values) for entity_id, values in rows.items()}


def _assignment_is_verified(
    rows: Sequence[tuple[int, str, str]],
) -> bool:
    return (
        len(rows) == 1
        and rows[0][1] in CONFIRMED_ASSIGNMENT_STATUSES
        and rows[0][2] in CONFIRMED_ASSIGNMENT_CONFIDENCE
    )


def _revision_identities(
    connection: sqlite3.Connection,
) -> dict[int, RevisionIdentity]:
    return {
        int(revision_id): (
            str(source_kind),
            str(source_uri),
            str(source_fingerprint),
            str(producer_version),
            str(schema_version),
            str(generated_at),
            str(freshness_status).upper(),
        )
        for (
            revision_id,
            source_kind,
            source_uri,
            source_fingerprint,
            producer_version,
            schema_version,
            generated_at,
            freshness_status,
        ) in connection.execute(
            """
            SELECT revision_id, source_kind, source_uri,
                   source_fingerprint, producer_version,
                   schema_version, generated_at, freshness_status
            FROM source_revisions
            """
        )
    }


def _logical_class_graph(
    connection: sqlite3.Connection,
    revisions: Mapping[int, RevisionIdentity],
) -> dict[int, tuple[_LogicalClassEdge, ...]]:
    grouped: dict[
        tuple[int, int],
        list[tuple[str, str, int | None, str, str]],
    ] = defaultdict(list)
    for (
        child_id,
        parent_id,
        edge_kind,
        evidence_id,
        revision_id,
        status,
        confidence,
    ) in connection.execute(
        """
        SELECT child_class_id, parent_class_id, edge_kind, evidence_id,
               source_revision_id, status, confidence
        FROM class_edges
        ORDER BY child_class_id, parent_class_id, edge_kind, evidence_id
        """
    ):
        kind = str(edge_kind)
        if kind not in _INHERITANCE_EDGE_KINDS:
            continue
        grouped[(int(child_id), int(parent_id))].append(
            (
                kind,
                str(evidence_id),
                int(revision_id) if revision_id is not None else None,
                str(status).upper(),
                str(confidence).upper(),
            )
        )
    graph: dict[int, list[_LogicalClassEdge]] = defaultdict(list)
    for (child_id, parent_id), rows in sorted(grouped.items()):
        confirmed_rows = [
            row
            for row in rows
            if row[3] in _CONFIRMED_PATH_STATUSES
            and row[4] in _CONFIRMED_PATH_CONFIDENCE
            and row[2] in revisions
            and revisions[int(row[2])][6] == "FRESH"
        ]
        supports = confirmed_rows or rows
        revision_values = tuple(
            sorted(
                {
                    revisions[int(row[2])]
                    for row in confirmed_rows
                    if row[2] is not None and int(row[2]) in revisions
                }
            )
        )
        graph[child_id].append(
            _LogicalClassEdge(
                child_class_id=child_id,
                parent_class_id=parent_id,
                edge_kind=sorted({row[0] for row in supports})[0],
                evidence_ids=tuple(sorted({row[1] for row in supports})),
                revision_identities=revision_values,
                confirmed=bool(confirmed_rows),
            )
        )
    return {
        child_id: tuple(
            sorted(
                edges,
                key=lambda edge: (
                    edge.parent_class_id,
                    edge.edge_kind,
                    edge.evidence_ids,
                ),
            )
        )
        for child_id, edges in graph.items()
    }


def _bounded_shortest_paths(
    start_class_id: int,
    graph: Mapping[int, Sequence[_LogicalClassEdge]],
) -> tuple[dict[int, tuple[_ClassPath, ...]], bool]:
    start = _ClassPath(classes=(start_class_id,), edges=())
    paths: dict[int, list[_ClassPath]] = {start_class_id: [start]}
    queue: deque[_ClassPath] = deque([start])
    cycle = False
    while queue:
        current_path = queue.popleft()
        current = current_path.classes[-1]
        for edge in graph.get(current, ()):
            parent = edge.parent_class_id
            if parent in current_path.classes:
                cycle = True
                continue
            candidate = _ClassPath(
                classes=(*current_path.classes, parent),
                edges=(*current_path.edges, edge),
            )
            existing = paths.get(parent)
            if existing is None:
                paths[parent] = [candidate]
                queue.append(candidate)
                continue
            candidate_depth = len(candidate.edges)
            existing_depth = len(existing[0].edges)
            if candidate_depth > existing_depth:
                continue
            if candidate_depth < existing_depth:
                paths[parent] = [candidate]
                queue.append(candidate)
                continue
            if (
                candidate.classes not in {path.classes for path in existing}
                and len(existing) < 2
            ):
                existing.append(candidate)
                existing.sort(key=lambda path: path.classes)
                queue.append(candidate)
    return {
        class_id: tuple(class_paths)
        for class_id, class_paths in paths.items()
    }, cycle


def _path_is_confirmed(
    *,
    start_class_id: int,
    path: _ClassPath,
    closure: Mapping[tuple[int, int], tuple[int, str]],
) -> bool:
    ancestor = path.classes[-1]
    expected_depth = len(path.edges)
    closure_row = closure.get((start_class_id, ancestor))
    expected_status = "SELF" if expected_depth == 0 else "CONFIRMED"
    return (
        closure_row is not None
        and closure_row[0] == expected_depth
        and closure_row[1].upper() == expected_status
        and all(edge.confirmed for edge in path.edges)
    )


def _class_resolution_context(
    *,
    start_class_id: int,
    graph: Mapping[int, Sequence[_LogicalClassEdge]],
    closure: Mapping[tuple[int, int], tuple[int, str]],
    native_class_ids: set[int],
    native_class_revisions: Mapping[int, RevisionIdentity],
    class_gaps: Mapping[int, set[str]],
) -> _ClassResolutionContext:
    paths, cycle = _bounded_shortest_paths(start_class_id, graph)
    reachable = set(paths)
    multiple_parent = any(
        len(graph.get(class_id, ())) > 1 for class_id in reachable
    )
    ambiguous_path = any(
        len(class_paths) > 1
        or not _path_is_confirmed(
            start_class_id=start_class_id,
            path=class_paths[0],
            closure=closure,
        )
        for class_paths in paths.values()
    )
    gaps = class_gaps.get(start_class_id, set())
    ambiguity_reasons: list[str] = []
    if cycle or "INHERITANCE_CYCLE" in gaps:
        ambiguity_reasons.append("INHERITANCE_CYCLE")
    if multiple_parent or "MULTIPLE_PARENT_CANDIDATES" in gaps:
        ambiguity_reasons.append("MULTIPLE_PARENT_CANDIDATES")
    if ambiguous_path:
        ambiguity_reasons.append("AMBIGUOUS_PATH")
    native_paths = [
        class_paths[0]
        for class_id, class_paths in paths.items()
        if class_id in native_class_ids
        and len(class_paths) == 1
        and _path_is_confirmed(
            start_class_id=start_class_id,
            path=class_paths[0],
            closure=closure,
        )
    ]
    native_paths.sort(
        key=lambda path: (len(path.edges), path.classes)
    )
    native_root_path = native_paths[0] if native_paths else None
    native_root_revision = (
        native_class_revisions.get(native_root_path.classes[-1])
        if native_root_path is not None
        else None
    )
    parent_chain_open = (
        native_root_path is None
        or native_root_revision is None
        or "NATIVE_ROOT_NOT_REACHED" in gaps
    )
    return _ClassResolutionContext(
        paths=paths,
        ambiguity_reasons=tuple(ambiguity_reasons),
        parent_chain_open=parent_chain_open,
        native_root_path=(
            native_root_path if native_root_revision is not None else None
        ),
        native_root_revision=native_root_revision,
    )


def _declared_candidates(
    connection: sqlite3.Connection,
    *,
    assignments: Mapping[int, Sequence[tuple[int, str, str]]],
    revisions: Mapping[int, RevisionIdentity],
) -> dict[int, dict[str, tuple[_DeclaredCandidate, ...]]]:
    evidence_by_fact: dict[int, set[RevisionIdentity]] = defaultdict(
        set
    )
    for fact_id, revision_id in connection.execute(
        """
        SELECT evidence.fact_id, evidence.source_revision_id
        FROM fact_evidence AS evidence
        JOIN facts AS fact ON fact.fact_id=evidence.fact_id
        WHERE fact.fact_type='DECLARED_DEFAULT'
          AND fact.scope_kind='DECLARED'
          AND fact.current=1
        """
    ):
        identity = revisions.get(int(revision_id))
        if identity is not None and identity[6] == "FRESH":
            evidence_by_fact[int(fact_id)].add(identity)
    result: dict[int, dict[str, list[_DeclaredCandidate]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in connection.execute(
        """
        SELECT fact_id, subject_entity_id, declared_on_entity_id,
               fact_name, value_kind, value_text, value_number,
               value_integer, value_json, status
        FROM facts
        WHERE fact_type='DECLARED_DEFAULT'
          AND scope_kind='DECLARED'
          AND current=1
        ORDER BY subject_entity_id, fact_name, fact_id
        """
    ):
        fact_id = int(row[0])
        owner_entity_id = int(row[1])
        owner_assignments = assignments.get(owner_entity_id, ())
        if len(owner_assignments) != 1:
            continue
        owner_class_id = int(owner_assignments[0][0])
        candidate = _DeclaredCandidate(
            fact_id=fact_id,
            owner_entity_id=owner_entity_id,
            owner_class_id=owner_class_id,
            fact_name=str(row[3]),
            value_kind=str(row[4]).upper(),
            value_text=row[5],
            value_number=row[6],
            value_integer=row[7],
            value_json=row[8],
            status=str(row[9]).upper(),
            declared_on_entity_id=(
                int(row[2]) if row[2] is not None else None
            ),
            fresh_revisions=tuple(sorted(evidence_by_fact.get(fact_id, set()))),
            base_rejection_reason="",
            owner_assignment_verified=_assignment_is_verified(
                owner_assignments
            ),
        )
        candidate = _DeclaredCandidate(
            **{
                **candidate.__dict__,
                "base_rejection_reason": _fact_rejection_reason(candidate),
            }
        )
        result[owner_class_id][candidate.fact_name].append(candidate)
    return {
        class_id: {
            name: tuple(sorted(values, key=lambda item: item.fact_id))
            for name, values in names.items()
        }
        for class_id, names in result.items()
    }


def _entities_for_changed_classes(
    connection: sqlite3.Connection,
    changed_class_ids: Iterable[int],
) -> set[int]:
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


def _changed_fact_work(
    connection: sqlite3.Connection,
    *,
    changed_fact_ids: Iterable[int],
    assignments: Mapping[int, Sequence[tuple[int, str, str]]],
) -> dict[int, set[str]]:
    changed = {int(value) for value in changed_fact_ids}
    if not changed:
        return {}
    work: dict[int, set[str]] = defaultdict(set)
    changed_values = sorted(changed)
    for offset in range(0, len(changed_values), 900):
        batch = changed_values[offset : offset + 900]
        placeholders = ",".join("?" for _ in batch)
        for owner_entity_id, entity_id, fact_name in connection.execute(
            f"""
            SELECT DISTINCT
                   fact.subject_entity_id,
                   target.entity_id,
                   fact.fact_name
            FROM facts AS fact
            JOIN asset_class_assignments AS owner
              ON owner.entity_id=fact.subject_entity_id
             AND owner.assignment_kind='GENERATED_CLASS'
            JOIN class_closure AS closure
              ON closure.ancestor_class_id=owner.class_id
            JOIN asset_class_assignments AS target
              ON target.class_id=closure.descendant_class_id
             AND target.assignment_kind='GENERATED_CLASS'
            WHERE fact.fact_id IN ({placeholders})
              AND fact.fact_type='DECLARED_DEFAULT'
            """,
            tuple(batch),
        ):
            owner_assignment = assignments.get(int(owner_entity_id), ())
            target_assignment = assignments.get(int(entity_id), ())
            if len(owner_assignment) != 1 or len(target_assignment) != 1:
                continue
            work[int(entity_id)].add(str(fact_name))
    return work


def _existing_effective_names(
    connection: sqlite3.Connection,
    affected_entity_ids: Iterable[int],
) -> dict[int, set[str]]:
    """Read existing keys without expanding a potentially huge SQL IN list."""

    affected = sorted({int(value) for value in affected_entity_ids})
    if not affected:
        return {}
    connection.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS effective_affected_entities(
            entity_id INTEGER PRIMARY KEY
        ) WITHOUT ROWID
        """
    )
    try:
        connection.execute("DELETE FROM effective_affected_entities")
        connection.executemany(
            "INSERT INTO effective_affected_entities VALUES (?)",
            ((entity_id,) for entity_id in affected),
        )
        result: dict[int, set[str]] = defaultdict(set)
        for entity_id, fact_name in connection.execute(
            """
            SELECT effective.entity_id, effective.fact_name
            FROM effective_facts AS effective
            JOIN effective_affected_entities AS affected
              ON affected.entity_id=effective.entity_id
            WHERE effective.fact_type='EFFECTIVE_DEFAULT'
            UNION
            SELECT candidate.entity_id, candidate.fact_name
            FROM effective_fact_candidates AS candidate
            JOIN effective_affected_entities AS affected
              ON affected.entity_id=candidate.entity_id
            WHERE candidate.fact_type='EFFECTIVE_DEFAULT'
            """
        ):
            result[int(entity_id)].add(str(fact_name))
        return result
    finally:
        connection.execute("DROP TABLE effective_affected_entities")


def _path_edges_json(path: _ClassPath) -> list[dict[str, object]]:
    return [
        {
            "childClassId": edge.child_class_id,
            "parentClassId": edge.parent_class_id,
            "edgeKind": edge.edge_kind,
            "evidenceIds": list(edge.evidence_ids),
            "status": "CONFIRMED",
        }
        for edge in path.edges
    ]


def _native_root_proof(
    *,
    start_class_id: int,
    context: _ClassResolutionContext,
) -> dict[str, object] | None:
    path = context.native_root_path
    revision = context.native_root_revision
    if path is None or revision is None:
        return None
    (
        source_kind,
        source_uri,
        source_fingerprint,
        producer_version,
        schema_version,
        generated_at,
        freshness_status,
    ) = revision
    return {
        "schema": _NATIVE_ROOT_PROOF_SCHEMA,
        "startClassId": start_class_id,
        "rootClassId": path.classes[-1],
        "classes": list(path.classes),
        "edges": _path_edges_json(path),
        "sourceRevision": {
            "sourceKind": source_kind,
            "sourceUri": source_uri,
            "sourceFingerprint": source_fingerprint,
            "producerVersion": producer_version,
            "schemaVersion": schema_version,
            "generatedAt": generated_at,
            "freshnessStatus": freshness_status,
        },
    }


def _unresolved_chain(start_class_id: int | None) -> str:
    return json.dumps(
        {
            "schema": _EFFECTIVE_PATH_SCHEMA,
            "startClassId": start_class_id,
            "declaredOnClassId": None,
            "declaredOnEntityId": None,
            "overrideDepth": None,
            "classes": [],
            "edges": [],
            "nativeRootProof": None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _resolved_chain(
    *,
    start_class_id: int,
    selected: _CandidateResolution,
    context: _ClassResolutionContext,
) -> str:
    native_root_proof = _native_root_proof(
        start_class_id=start_class_id,
        context=context,
    )
    if native_root_proof is None:
        raise ValueError("RESOLVED effective defaults require native root proof")
    return json.dumps(
        {
            "schema": _EFFECTIVE_PATH_SCHEMA,
            "startClassId": start_class_id,
            "declaredOnClassId": selected.candidate.owner_class_id,
            "declaredOnEntityId": selected.candidate.owner_entity_id,
            "overrideDepth": selected.depth,
            "classes": list(selected.path.classes),
            "edges": _path_edges_json(selected.path),
            "nativeRootProof": native_root_proof,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def materialize_effective_defaults(
    connection: sqlite3.Connection,
    *,
    changed_class_ids: Iterable[int] | None = None,
    affected_entity_ids: Iterable[int] | None = None,
    changed_fact_ids: Iterable[int] | None = None,
) -> dict[str, int]:
    """Resolve actual typed defaults over evidence-backed class paths."""

    full_rebuild = (
        changed_class_ids is None
        and affected_entity_ids is None
        and changed_fact_ids is None
    )
    assignments = _assignment_rows(connection)
    class_wide: set[int] = (
        set(assignments)
        if full_rebuild
        else {int(value) for value in (affected_entity_ids or ())}
    )
    if full_rebuild:
        class_wide.update(
            int(row[0])
            for row in connection.execute(
                """
                SELECT entity_id
                FROM effective_facts
                WHERE fact_type='EFFECTIVE_DEFAULT'
                UNION
                SELECT entity_id
                FROM effective_fact_candidates
                WHERE fact_type='EFFECTIVE_DEFAULT'
                """
            )
        )
    if changed_class_ids is not None:
        class_wide.update(
            _entities_for_changed_classes(connection, changed_class_ids)
        )
    targeted = (
        _changed_fact_work(
            connection,
            changed_fact_ids=changed_fact_ids,
            assignments=assignments,
        )
        if changed_fact_ids is not None
        else {}
    )
    affected = class_wide.union(targeted)
    if not affected:
        return {
            "effectiveFacts": 0,
            "candidateFacts": 0,
            "affectedEntities": 0,
            "workKeys": 0,
        }

    revisions = _revision_identities(connection)
    graph = _logical_class_graph(connection, revisions)
    closure = {
        (int(descendant_id), int(ancestor_id)): (
            int(depth),
            str(path_status).upper(),
        )
        for ancestor_id, descendant_id, depth, path_status in connection.execute(
            """
            SELECT ancestor_class_id, descendant_class_id, depth, path_status
            FROM class_closure
            """
        )
    }
    native_class_rows = list(
        connection.execute(
            """
            SELECT class_id, source_revision_id, status, confidence
            FROM classes
            WHERE is_native=1
            """
        )
    )
    native_class_ids = {int(row[0]) for row in native_class_rows}
    native_class_revisions = {
        int(class_id): revisions[int(revision_id)]
        for class_id, revision_id, status, confidence in native_class_rows
        if revision_id is not None
        and int(revision_id) in revisions
        and revisions[int(revision_id)][6] == "FRESH"
        and str(status).upper() in CONFIRMED_CLASS_STATUSES
        and str(confidence).upper() in CONFIRMED_CLASS_CONFIDENCE
    }
    class_gaps: dict[int, set[str]] = defaultdict(set)
    for class_id, gap_kind in connection.execute(
        "SELECT class_id, gap_kind FROM class_gaps"
    ):
        class_gaps[int(class_id)].add(str(gap_kind).upper())
    facts_by_class = _declared_candidates(
        connection,
        assignments=assignments,
        revisions=revisions,
    )

    context_cache: dict[int, _ClassResolutionContext] = {}
    work: set[tuple[int, str]] = set()
    existing_names = _existing_effective_names(connection, affected)

    for entity_id in sorted(affected):
        if entity_id not in class_wide:
            for fact_name in targeted.get(entity_id, set()):
                work.add((entity_id, fact_name))
            continue
        names = set(existing_names.get(entity_id, set()))
        assignment = assignments.get(entity_id, ())
        if len(assignment) == 1:
            start_class_id = int(assignment[0][0])
            context = context_cache.get(start_class_id)
            if context is None:
                context = _class_resolution_context(
                    start_class_id=start_class_id,
                    graph=graph,
                    closure=closure,
                    native_class_ids=native_class_ids,
                    native_class_revisions=native_class_revisions,
                    class_gaps=class_gaps,
                )
                context_cache[start_class_id] = context
            for reachable_class_id in context.paths:
                names.update(facts_by_class.get(reachable_class_id, {}))
        names.update(targeted.get(entity_id, set()))
        for fact_name in names:
            work.add((entity_id, fact_name))
    if not work:
        return {
            "effectiveFacts": 0,
            "candidateFacts": 0,
            "affectedEntities": len(affected),
            "workKeys": 0,
        }

    connection.execute("SAVEPOINT effective_defaults_rebuild")
    inserted = 0
    candidate_count = 0
    try:
        connection.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS effective_work(
                entity_id INTEGER NOT NULL,
                fact_name TEXT NOT NULL,
                PRIMARY KEY(entity_id, fact_name)
            ) WITHOUT ROWID
            """
        )
        connection.execute("DELETE FROM effective_work")
        connection.executemany(
            "INSERT INTO effective_work VALUES (?, ?)",
            sorted(work),
        )
        connection.execute(
            """
            DELETE FROM effective_fact_candidates
            WHERE fact_type='EFFECTIVE_DEFAULT'
              AND EXISTS (
                SELECT 1 FROM effective_work AS work
                WHERE work.entity_id=effective_fact_candidates.entity_id
                  AND work.fact_name=effective_fact_candidates.fact_name
              )
            """
        )
        connection.execute(
            """
            DELETE FROM effective_facts
            WHERE fact_type='EFFECTIVE_DEFAULT'
              AND EXISTS (
                SELECT 1 FROM effective_work AS work
                WHERE work.entity_id=effective_facts.entity_id
                  AND work.fact_name=effective_facts.fact_name
              )
            """
        )

        for entity_id, fact_name in sorted(work):
            assignment = assignments.get(entity_id, ())
            start_class_id = int(assignment[0][0]) if len(assignment) == 1 else None
            assignment_verified = _assignment_is_verified(assignment)
            context = (
                context_cache.get(start_class_id)
                if start_class_id is not None
                else None
            )
            if start_class_id is not None and context is None:
                context = _class_resolution_context(
                    start_class_id=start_class_id,
                    graph=graph,
                    closure=closure,
                    native_class_ids=native_class_ids,
                    native_class_revisions=native_class_revisions,
                    class_gaps=class_gaps,
                )
                context_cache[start_class_id] = context

            resolutions: list[_CandidateResolution] = []
            if context is not None:
                for owner_class_id, class_paths in context.paths.items():
                    candidates = facts_by_class.get(owner_class_id, {}).get(
                        fact_name, ()
                    )
                    if not candidates:
                        continue
                    path = class_paths[0]
                    path_confirmed = (
                        len(class_paths) == 1
                        and _path_is_confirmed(
                            start_class_id=start_class_id,
                            path=path,
                            closure=closure,
                        )
                    )
                    path_status = (
                        "SELF"
                        if len(path.edges) == 0 and path_confirmed
                        else "CONFIRMED"
                        if path_confirmed
                        else "AMBIGUOUS"
                    )
                    for candidate in candidates:
                        resolutions.append(
                            _CandidateResolution(
                                candidate=candidate,
                                depth=len(path.edges),
                                path_status=path_status,
                                path=path,
                            )
                        )
            resolutions.sort(
                key=lambda item: (
                    item.depth,
                    item.candidate.owner_entity_id,
                    item.candidate.fact_id,
                )
            )

            reasons = {
                item.candidate.fact_id: item.candidate.base_rejection_reason
                for item in resolutions
            }
            eligible = [
                item
                for item in resolutions
                if not reasons[item.candidate.fact_id]
            ]
            selected: _CandidateResolution | None = None
            if not eligible:
                resolution_status = (
                    "ASSIGNMENT_UNVERIFIED"
                    if resolutions and not assignment_verified
                    else "NOT_RECOVERED"
                )
            else:
                nearest_depth = min(item.depth for item in eligible)
                nearest = [
                    item for item in eligible if item.depth == nearest_depth
                ]
                deeper = [
                    item for item in eligible if item.depth > nearest_depth
                ]
                for item in deeper:
                    reasons[item.candidate.fact_id] = (
                        "SHADOWED_BY_NEARER_USABLE"
                    )
                if not assignment_verified:
                    resolution_status = "ASSIGNMENT_UNVERIFIED"
                    for item in nearest:
                        reasons[item.candidate.fact_id] = (
                            "ASSIGNMENT_UNVERIFIED"
                        )
                elif (
                    context is not None
                    and context.parent_chain_open
                    and not context.ambiguity_reasons
                ):
                    resolution_status = "PARENT_CHAIN_OPEN"
                    for item in nearest:
                        reasons[item.candidate.fact_id] = "PARENT_CHAIN_OPEN"
                elif any(
                    item.path_status not in {"SELF", "CONFIRMED"}
                    for item in nearest
                ):
                    resolution_status = "AMBIGUOUS_INHERITANCE"
                    for item in nearest:
                        reasons[item.candidate.fact_id] = "AMBIGUOUS_PATH"
                else:
                    owner_counts: dict[int, int] = defaultdict(int)
                    for item in nearest:
                        owner_counts[item.candidate.owner_entity_id] += 1
                    if any(count > 1 for count in owner_counts.values()):
                        resolution_status = "AMBIGUOUS_DECLARATION"
                        for item in nearest:
                            reasons[item.candidate.fact_id] = (
                                "AMBIGUOUS_DECLARATION"
                            )
                    elif len(nearest) > 1:
                        resolution_status = "AMBIGUOUS_INHERITANCE"
                        for item in nearest:
                            reasons[item.candidate.fact_id] = (
                                "SAME_DEPTH_CONFLICT"
                            )
                    elif (
                        context is not None
                        and context.ambiguity_reasons
                    ):
                        resolution_status = "AMBIGUOUS_INHERITANCE"
                        reason = context.ambiguity_reasons[0]
                        for item in nearest:
                            reasons[item.candidate.fact_id] = reason
                    elif context is not None and context.parent_chain_open:
                        resolution_status = "PARENT_CHAIN_OPEN"
                        for item in nearest:
                            reasons[item.candidate.fact_id] = (
                                "PARENT_CHAIN_OPEN"
                            )
                    else:
                        selected = nearest[0]
                        resolution_status = "RESOLVED"
                        reasons[selected.candidate.fact_id] = ""

            for item in resolutions:
                is_selected = int(
                    selected is not None
                    and selected.candidate.fact_id
                    == item.candidate.fact_id
                )
                reason = reasons[item.candidate.fact_id]
                if not is_selected and not reason:
                    reason = "SHADOWED_BY_NEARER_USABLE"
                connection.execute(
                    """
                    INSERT INTO effective_fact_candidates(
                        entity_id, fact_type, fact_name, candidate_fact_id,
                        declared_on_entity_id, inheritance_depth, path_status,
                        selected, rejection_reason
                    ) VALUES (?, 'EFFECTIVE_DEFAULT', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        fact_name,
                        item.candidate.fact_id,
                        item.candidate.owner_entity_id,
                        item.depth,
                        item.path_status,
                        is_selected,
                        reason,
                    ),
                )
                candidate_count += 1

            if selected is None:
                fact_id = None
                inherited_from = None
                chain = _unresolved_chain(start_class_id)
                revision_hash = _empty_revision_hash()
            else:
                fact_id = selected.candidate.fact_id
                inherited_from = (
                    None
                    if selected.candidate.owner_entity_id == entity_id
                    else selected.candidate.owner_entity_id
                )
                chain = _resolved_chain(
                    start_class_id=int(start_class_id),
                    selected=selected,
                    context=context,
                )
                revision_values = set(selected.candidate.fresh_revisions)
                for edge in selected.path.edges:
                    revision_values.update(edge.revision_identities)
                if context is not None and context.native_root_path is not None:
                    for edge in context.native_root_path.edges:
                        revision_values.update(edge.revision_identities)
                if (
                    context is not None
                    and context.native_root_revision is not None
                ):
                    revision_values.add(context.native_root_revision)
                revision_hash = _stable_revision_hash(revision_values)
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
                    fact_name,
                    fact_id,
                    inherited_from,
                    chain,
                    resolution_status,
                    revision_hash,
                ),
            )
            inserted += 1
        connection.execute("DROP TABLE effective_work")
        connection.execute("RELEASE SAVEPOINT effective_defaults_rebuild")
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT effective_defaults_rebuild")
        connection.execute("RELEASE SAVEPOINT effective_defaults_rebuild")
        raise
    connection.commit()
    return {
        "effectiveFacts": inserted,
        "candidateFacts": candidate_count,
        "affectedEntities": len(affected),
        "workKeys": len(work),
    }
