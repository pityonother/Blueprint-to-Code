"""Verified, bounded rebuild worker for KB vNext invalidation tasks.

The invalidation planner marks stale state and creates queue rows.  This module
performs the distinct rebuild phase:

* it exposes eleven named backend operations, one for every queue kind;
* it owns queue claims and terminal state transitions;
* it prevents backend code from committing the core transaction;
* it independently inspects durable target state before and after rebuilding;
* it writes a content-addressed rebuild receipt into the event payload; and
* it replays orphaned ``RUNNING`` work without duplicating a completed result.

Only three selective materializers exist in the current package:
``FACT``, ``CLASS_CLOSURE``, and ``EFFECTIVE_ENTITY``.  They are wired by
:class:`CoreMaterializerRebuildBackend`.  Update orchestration must subclass it
and provide the remaining source-, cache-, and projection-aware operations.
An operation that is not configured becomes ``BLOCKED_GAP`` with a stable gap
code; it can never be reported as ``SUCCEEDED``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from .projections import (
    DOMAIN_PROJECTIONS,
    PROJECTION_SCHEMA_VERSION,
    compute_core_projection_content_digest,
    compute_projection_artifact_content_digest,
)


PENDING_REBUILD: Final = "PENDING_REBUILD"
RUNNING: Final = "RUNNING"
SUCCEEDED: Final = "SUCCEEDED"
FAILED: Final = "FAILED"
BLOCKED_GAP: Final = "BLOCKED_GAP"

QUEUE_STATUSES: Final = frozenset(
    {
        PENDING_REBUILD,
        RUNNING,
        SUCCEEDED,
        FAILED,
        BLOCKED_GAP,
    }
)

# Dependency order is deliberate: closure and registrations precede dependent
# entity read models; Native functions precede Blueprint/native links; external
# read models are last.
REBUILD_KIND_ORDER: Final = (
    "CLASS_CLOSURE",
    "REGISTRATION_ENTITY",
    "FACT",
    "EDGE_ENTITY",
    "NATIVE_FUNCTION",
    "BLUEPRINT_NATIVE_ENTITY",
    "EFFECTIVE_ENTITY",
    "ROLE_ENTITY",
    "DOMAIN_ENTITY",
    "PROJECTION",
    "QUERY_SNAPSHOT",
)
SUPPORTED_REBUILD_KINDS: Final = frozenset(REBUILD_KIND_ORDER)
_PROJECTION_NAMES: Final = tuple(DOMAIN_PROJECTIONS)
_RECEIPT_SCHEMA: Final = "ark-kb-rebuild-receipt/v1"
_RECEIPTS_KEY: Final = "_rebuildReceipts"
_PROJECTION_BATCH_KEY: Final = "_rebuildProjectionBatch"
_ATTEMPTS_KEY: Final = "_rebuildAttempts"
_ATTEMPT_SCHEMA: Final = "ark-kb-rebuild-attempt/v1"
_EXTERNAL_MARKER_SCHEMA: Final = "ark-kb-rebuild-external-marker/v1"
_RETRYABLE_TERMINAL_STATUSES: Final = frozenset({FAILED, BLOCKED_GAP})
_MAX_CLASS_CLOSURE_SCOPE: Final = 4096
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x400

EXPECTED_REBUILD_WRITE_TABLES: Final[
    Mapping[str, frozenset[str]]
] = MappingProxyType(
    {
        "FACT": frozenset({"facts", "fact_evidence"}),
        "EFFECTIVE_ENTITY": frozenset(
            {"effective_facts", "effective_fact_candidates"}
        ),
        "ROLE_ENTITY": frozenset(
            {
                "knowledge_roles",
                "knowledge_depth_policies",
                "role_metrics",
                "role_signal_metrics",
            }
        ),
        "DOMAIN_ENTITY": frozenset({"domain_memberships"}),
        "EDGE_ENTITY": frozenset({"edges"}),
        "CLASS_CLOSURE": frozenset(
            {
                "class_closure",
                "class_gaps",
                "class_ancestry_categories",
            }
        ),
        "REGISTRATION_ENTITY": frozenset({"typed_registrations"}),
        "NATIVE_FUNCTION": frozenset(
            {
                "native_functions",
                "native_field_accesses",
                "native_gold_targets",
            }
        ),
        "BLUEPRINT_NATIVE_ENTITY": frozenset(
            {"native_blueprint_links"}
        ),
        "PROJECTION": frozenset({"projection_runs"}),
        "QUERY_SNAPSHOT": frozenset(
            {
                "query_snapshots",
                "context_packs",
                "answer_plans",
                "materialized_neighborhoods",
            }
        ),
    }
)

_ROW_SCOPE_GUARD_PREFIX: Final = "ark_kb_rebuild_row_scope_"


@dataclass(frozen=True)
class _RowScopeRule:
    """Columns whose values must belong to the durable task scope.

    Multiple columns are alternatives.  This is needed for registrations,
    where either the owner URI or target URI may identify the queued entity.
    An empty tuple is reserved for an explicitly verified whole-table batch.
    """

    columns: tuple[str, ...]
    mode: str = "SCOPED_VALUES"


_ROW_SCOPE_RULES: Final[
    Mapping[str, Mapping[str, _RowScopeRule]]
] = MappingProxyType(
    {
        "FACT": MappingProxyType(
            {
                "facts": _RowScopeRule(("fact_id",)),
                "fact_evidence": _RowScopeRule(("fact_id",)),
            }
        ),
        "EFFECTIVE_ENTITY": MappingProxyType(
            {
                "effective_facts": _RowScopeRule(("entity_id",)),
                "effective_fact_candidates": _RowScopeRule(("entity_id",)),
            }
        ),
        "ROLE_ENTITY": MappingProxyType(
            {
                "knowledge_roles": _RowScopeRule(("entity_id",)),
                "knowledge_depth_policies": _RowScopeRule(("entity_id",)),
                "role_metrics": _RowScopeRule(("entity_id",)),
                "role_signal_metrics": _RowScopeRule(("entity_id",)),
            }
        ),
        "DOMAIN_ENTITY": MappingProxyType(
            {
                "domain_memberships": _RowScopeRule(("entity_id",)),
            }
        ),
        "EDGE_ENTITY": MappingProxyType(
            {
                "edges": _RowScopeRule(("source_entity_id",)),
            }
        ),
        "CLASS_CLOSURE": MappingProxyType(
            {
                "class_closure": _RowScopeRule(("descendant_class_id",)),
                "class_gaps": _RowScopeRule(("class_id",)),
                "class_ancestry_categories": _RowScopeRule(("class_id",)),
            }
        ),
        "REGISTRATION_ENTITY": MappingProxyType(
            {
                "typed_registrations": _RowScopeRule(
                    ("owner_uri", "target_uri")
                ),
            }
        ),
        "NATIVE_FUNCTION": MappingProxyType(
            {
                "native_functions": _RowScopeRule(
                    ("native_function_id",)
                ),
                "native_field_accesses": _RowScopeRule(
                    ("native_function_id",)
                ),
                "native_gold_targets": _RowScopeRule(
                    (
                        "native_function_id",
                        "qualified_symbol",
                        "expected_rva",
                    ),
                    mode="NATIVE_GOLD_IDENTITY",
                ),
            }
        ),
        "BLUEPRINT_NATIVE_ENTITY": MappingProxyType(
            {
                "native_blueprint_links": _RowScopeRule(
                    ("blueprint_entity_id",)
                ),
            }
        ),
        "PROJECTION": MappingProxyType(
            {
                "projection_runs": _RowScopeRule(("projection_name",)),
            }
        ),
        "QUERY_SNAPSHOT": MappingProxyType(
            {
                # Query invalidation is an explicit all-cache-rows batch.
                "query_snapshots": _RowScopeRule(
                    (),
                    mode="EXPLICIT_WHOLE_CACHE_BATCH",
                ),
                "context_packs": _RowScopeRule(
                    (),
                    mode="EXPLICIT_WHOLE_CACHE_BATCH",
                ),
                "answer_plans": _RowScopeRule(
                    (),
                    mode="EXPLICIT_WHOLE_CACHE_BATCH",
                ),
                "materialized_neighborhoods": _RowScopeRule(
                    (),
                    mode="EXPLICIT_WHOLE_CACHE_BATCH",
                ),
            }
        ),
    }
)


class RebuildWorkerError(RuntimeError):
    """Base error for rebuild contracts and queue infrastructure."""


class RebuildVerificationError(RebuildWorkerError):
    """The backend returned without producing a verified target state."""


class RebuildBlockedGap(RuntimeError):
    """Tell the worker that required rebuild input is not available."""

    def __init__(self, gap_code: str, detail: str = "") -> None:
        normalized = str(gap_code).strip().upper()
        if not normalized:
            raise ValueError("gap_code is required")
        super().__init__(detail or normalized)
        self.gap_code = normalized
        self.detail = str(detail)


@dataclass(frozen=True)
class RebuildTask:
    """One claimed queue target."""

    event_id: str
    downstream_kind: str
    downstream_id: int
    dependency_reason: str
    recovered: bool = False

    @property
    def kind(self) -> str:
        return self.downstream_kind

    @property
    def target_id(self) -> int:
        return self.downstream_id

    @property
    def reason(self) -> str:
        return self.dependency_reason

    @property
    def receipt_key(self) -> str:
        return f"{self.downstream_kind}:{self.downstream_id}"


@dataclass(frozen=True)
class RebuildTargetState:
    """Worker-owned durable state inspection for one target."""

    digest: str
    complete: bool
    gap_codes: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class _ClassClosureSeed:
    root_class_id: int
    affected_class_ids: tuple[int, ...]
    event_source_revision_proof: str


@dataclass(frozen=True)
class RebuildTaskOutcome:
    task: RebuildTask
    status: str
    proof: str = ""
    gap_code: str = ""
    detail: str = ""
    touched_tables: tuple[str, ...] = ()
    cache_hit: bool = False


@dataclass(frozen=True)
class RebuildDrainReport:
    max_items: int
    recovered_running: int
    attempted: int
    succeeded: int
    failed: int
    blocked_gap: int
    remaining_pending: int
    remaining_running: int
    outcomes: tuple[RebuildTaskOutcome, ...]

    @property
    def drained(self) -> bool:
        return self.remaining_pending == 0 and self.remaining_running == 0


class GuardedCursor:
    """Cursor view that never exposes its owning raw connection."""

    __slots__ = ("__cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.__cursor = cursor

    def __iter__(self) -> "GuardedCursor":
        return self

    def __next__(self) -> object:
        return next(self.__cursor)

    def fetchone(self) -> object:
        return self.__cursor.fetchone()

    def fetchmany(self, size: int | None = None) -> list[object]:
        if size is None:
            return self.__cursor.fetchmany()
        return self.__cursor.fetchmany(size)

    def fetchall(self) -> list[object]:
        return self.__cursor.fetchall()

    def close(self) -> None:
        self.__cursor.close()

    @property
    def rowcount(self) -> int:
        return self.__cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self.__cursor.lastrowid

    @property
    def description(
        self,
    ) -> tuple[tuple[object, ...], ...] | None:
        return self.__cursor.description

    @property
    def arraysize(self) -> int:
        return self.__cursor.arraysize

    @arraysize.setter
    def arraysize(self, value: int) -> None:
        self.__cursor.arraysize = value


class GuardedConnection:
    """Transaction-bound subset of ``sqlite3.Connection`` for backends.

    Existing materializers call ``commit()`` internally.  Here that call is a
    deliberate no-op: the worker alone commits semantic state together with
    the queue status and receipt.  Direct transaction SQL and rollback are
    rejected.  ``executescript`` is reimplemented statement-by-statement so it
    does not perform sqlite3's implicit pre-script commit.
    """

    __slots__ = ("__connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.__connection = connection

    @staticmethod
    def _first_meaningful_sql(sql: str) -> str:
        """Skip SQLite-accepted empty statements and leading comments."""

        offset = 0
        length = len(sql)
        while offset < length:
            while offset < length and (
                sql[offset].isspace() or sql[offset] == ";"
            ):
                offset += 1
            if sql.startswith("--", offset):
                newline = sql.find("\n", offset + 2)
                if newline < 0:
                    return ""
                offset = newline + 1
                continue
            if sql.startswith("/*", offset):
                closing = sql.find("*/", offset + 2)
                if closing < 0:
                    raise RebuildWorkerError(
                        "backend SQL contains an unterminated comment"
                    )
                offset = closing + 2
                continue
            break
        return sql[offset:]

    @classmethod
    def _reject_transaction_sql(cls, sql: str) -> None:
        meaningful = cls._first_meaningful_sql(sql)
        first = meaningful.split(None, 1)
        token = first[0].rstrip(";").upper() if first else ""
        if (
            token == "ROLLBACK"
            and len(first) == 2
            and first[1].lstrip().upper().startswith("TO ")
        ):
            return
        if token in {"BEGIN", "COMMIT", "END", "ROLLBACK"}:
            raise RebuildWorkerError(
                "backend transaction control is forbidden"
            )

    def execute(
        self,
        sql: str,
        parameters: Sequence[object] = (),
    ) -> GuardedCursor:
        self._reject_transaction_sql(sql)
        return GuardedCursor(
            self.__connection.execute(sql, parameters)
        )

    def executemany(
        self,
        sql: str,
        parameters: Iterable[Sequence[object]],
    ) -> GuardedCursor:
        self._reject_transaction_sql(sql)
        return GuardedCursor(
            self.__connection.executemany(sql, parameters)
        )

    def executescript(self, script: str) -> None:
        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if not sqlite3.complete_statement(buffer):
                continue
            statement = buffer.strip()
            buffer = ""
            if statement:
                self.execute(statement)
        if buffer.strip():
            raise RebuildWorkerError("backend SQL script is incomplete")

    def commit(self) -> None:
        """Suppress a materializer's local commit."""

    def rollback(self) -> None:
        raise RebuildWorkerError(
            "backend rollback is forbidden; raise to let the worker roll back"
        )

    @property
    def in_transaction(self) -> bool:
        return self.__connection.in_transaction

    @property
    def total_changes(self) -> int:
        return self.__connection.total_changes


@dataclass(frozen=True)
class RebuildScope:
    """Resources made available to one named backend operation."""

    task: RebuildTask
    core: GuardedConnection
    cache: GuardedConnection | None
    projection_dir: Path | None
    class_closure_ids: tuple[int, ...] = ()


class RebuildBackend:
    """Typed backend boundary with one named operation per queue kind."""

    def __init__(
        self,
        *,
        projection_dir: Path | None = None,
        cache_connection: sqlite3.Connection | None = None,
    ) -> None:
        if projection_dir is not None and _is_reparse_point(projection_dir):
            raise ValueError("projection_dir cannot be a symlink or reparse point")
        self.__projection_dir = (
            projection_dir.resolve()
            if projection_dir is not None
            else None
        )
        self.__cache_connection = cache_connection

    @staticmethod
    def _not_configured(kind: str) -> None:
        raise RebuildBlockedGap(
            f"BACKEND_NOT_CONFIGURED_{kind}",
            f"No real {kind} rebuild operation was supplied.",
        )

    def rebuild_fact(self, scope: RebuildScope) -> None:
        self._not_configured("FACT")

    def rebuild_effective_entity(self, scope: RebuildScope) -> None:
        self._not_configured("EFFECTIVE_ENTITY")

    def rebuild_role_entity(self, scope: RebuildScope) -> None:
        self._not_configured("ROLE_ENTITY")

    def rebuild_domain_entity(self, scope: RebuildScope) -> None:
        self._not_configured("DOMAIN_ENTITY")

    def rebuild_edge_entity(self, scope: RebuildScope) -> None:
        self._not_configured("EDGE_ENTITY")

    def rebuild_class_closure(self, scope: RebuildScope) -> None:
        self._not_configured("CLASS_CLOSURE")

    def rebuild_registration_entity(self, scope: RebuildScope) -> None:
        self._not_configured("REGISTRATION_ENTITY")

    def rebuild_native_function(self, scope: RebuildScope) -> None:
        self._not_configured("NATIVE_FUNCTION")

    def rebuild_blueprint_native_entity(
        self,
        scope: RebuildScope,
    ) -> None:
        self._not_configured("BLUEPRINT_NATIVE_ENTITY")

    def rebuild_projection(self, scope: RebuildScope) -> None:
        self._not_configured("PROJECTION")

    def rebuild_query_snapshot(self, scope: RebuildScope) -> None:
        self._not_configured("QUERY_SNAPSHOT")


def _backend_projection_dir(backend: RebuildBackend) -> Path | None:
    return object.__getattribute__(
        backend,
        "_RebuildBackend__projection_dir",
    )


def _backend_cache_connection(
    backend: RebuildBackend,
) -> sqlite3.Connection | None:
    return object.__getattribute__(
        backend,
        "_RebuildBackend__cache_connection",
    )


class CoreMaterializerRebuildBackend(RebuildBackend):
    """Default bindings for the currently selective core materializers."""

    def rebuild_fact(self, scope: RebuildScope) -> None:
        from .fact_store import materialize_blueprint_fact

        if not materialize_blueprint_fact(
            scope.core,  # type: ignore[arg-type]
            fact_id=scope.task.downstream_id,
        ):
            raise RebuildBlockedGap(
                "FACT_SOURCE_NOT_MATERIALIZABLE",
                "The fact lacks a fresh, revision-bound Blueprint Evidence "
                "source that the production materializer can reactivate.",
            )

    def rebuild_class_closure(self, scope: RebuildScope) -> None:
        from .class_hierarchy import rebuild_class_closure

        rebuild_class_closure(
            scope.core,  # type: ignore[arg-type]
            changed_class_ids=(
                scope.class_closure_ids
                or (scope.task.downstream_id,)
            ),
        )

    def rebuild_effective_entity(self, scope: RebuildScope) -> None:
        from .fact_store import materialize_effective_defaults

        materialize_effective_defaults(
            scope.core,  # type: ignore[arg-type]
            affected_entity_ids=(scope.task.downstream_id,),
        )


def _dispatch_backend(
    backend: RebuildBackend,
    scope: RebuildScope,
) -> None:
    kind = scope.task.downstream_kind
    if kind == "FACT":
        backend.rebuild_fact(scope)
    elif kind == "EFFECTIVE_ENTITY":
        backend.rebuild_effective_entity(scope)
    elif kind == "ROLE_ENTITY":
        backend.rebuild_role_entity(scope)
    elif kind == "DOMAIN_ENTITY":
        backend.rebuild_domain_entity(scope)
    elif kind == "EDGE_ENTITY":
        backend.rebuild_edge_entity(scope)
    elif kind == "CLASS_CLOSURE":
        backend.rebuild_class_closure(scope)
    elif kind == "REGISTRATION_ENTITY":
        backend.rebuild_registration_entity(scope)
    elif kind == "NATIVE_FUNCTION":
        backend.rebuild_native_function(scope)
    elif kind == "BLUEPRINT_NATIVE_ENTITY":
        backend.rebuild_blueprint_native_entity(scope)
    elif kind == "PROJECTION":
        backend.rebuild_projection(scope)
    elif kind == "QUERY_SNAPSHOT":
        backend.rebuild_query_snapshot(scope)
    else:
        raise RebuildWorkerError(f"unsupported queued kind: {kind}")


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        _compact_json(value).encode("utf-8")
    ).hexdigest()


def _state(
    payload: object,
    *,
    complete: bool,
    gaps: Iterable[str] = (),
    summary: str,
) -> RebuildTargetState:
    return RebuildTargetState(
        digest=_digest(payload),
        complete=complete,
        gap_codes=tuple(
            sorted({str(value).strip().upper() for value in gaps if value})
        ),
        summary=summary,
    )


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Sequence[object] = (),
) -> list[tuple[object, ...]]:
    return [tuple(row) for row in connection.execute(sql, parameters)]


def _fact_seed(
    connection: sqlite3.Connection,
    fact_id: int,
) -> tuple[object, ...] | None:
    row = connection.execute(
        """
        SELECT subject_entity_id, fact_type, fact_name, scope_kind
        FROM facts
        WHERE fact_id=?
        """,
        (fact_id,),
    ).fetchone()
    return None if row is None else tuple(row)


def _native_seed(
    connection: sqlite3.Connection,
    native_function_id: int,
) -> tuple[str, str, str] | None:
    row = connection.execute(
        """
        SELECT canonical_uri, qualified_symbol, rva
        FROM native_functions
        WHERE native_function_id=?
        """,
        (native_function_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0]), str(row[1]), str(row[2])


def _event_class_closure_scope(
    connection: sqlite3.Connection,
    task: RebuildTask,
) -> tuple[tuple[int, ...], str]:
    payload = _event_payload(connection, task.event_id)
    scopes = payload.get("_classClosureScopes")
    proofs = payload.get("_classClosureSourceRevisionProofs")
    if scopes is None and proofs is None:
        return (), ""
    if not isinstance(scopes, dict) or not isinstance(proofs, dict):
        raise RebuildWorkerError(
            "class closure event scope/proof is malformed"
        )
    raw_scope = scopes.get(str(task.downstream_id))
    raw_proof = proofs.get(str(task.downstream_id))
    if (
        not isinstance(raw_scope, list)
        or not raw_scope
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in raw_scope
        )
        or not isinstance(raw_proof, str)
        or len(raw_proof) != 64
        or any(character not in "0123456789abcdef" for character in raw_proof)
    ):
        raise RebuildWorkerError(
            "class closure event scope/proof is invalid"
        )
    scope = tuple(sorted(set(int(value) for value in raw_scope)))
    if (
        task.downstream_id not in scope
        or len(scope) > _MAX_CLASS_CLOSURE_SCOPE
    ):
        raise RebuildWorkerError(
            "class closure event scope is unsafe"
        )
    return scope, raw_proof


def _class_closure_seed(
    connection: sqlite3.Connection,
    task: RebuildTask,
) -> _ClassClosureSeed:
    from .class_hierarchy import _affected_descendants, _graph

    event_scope, event_proof = _event_class_closure_scope(
        connection, task
    )
    payload = _event_payload(connection, task.event_id)
    raw_affected_entities = payload.get("EFFECTIVE_ENTITY", [])
    if (
        not isinstance(raw_affected_entities, list)
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in raw_affected_entities
        )
        or len(raw_affected_entities) > _MAX_CLASS_CLOSURE_SCOPE
    ):
        raise RebuildWorkerError(
            "class closure affected-entity scope is invalid"
        )
    affected_entity_ids = tuple(
        sorted(set(int(value) for value in raw_affected_entities))
    )
    affected_entity_classes: set[int] = set()
    if affected_entity_ids:
        placeholders = ",".join("?" for _ in affected_entity_ids)
        affected_entity_classes = {
            int(row[0])
            for row in connection.execute(
                f"""
                SELECT DISTINCT class_id
                FROM asset_class_assignments
                WHERE assignment_kind='GENERATED_CLASS'
                  AND entity_id IN ({placeholders})
                """,
                affected_entity_ids,
            )
        }
    _parents, children = _graph(connection)
    current_scope = _affected_descendants(
        children, (task.downstream_id,)
    )
    durable_scope = {
        int(row[0])
        for row in connection.execute(
            """
            SELECT descendant_class_id
            FROM class_closure
            WHERE ancestor_class_id=?
            """,
            (task.downstream_id,),
        )
    }
    affected = (
        set(event_scope)
        | current_scope
        | durable_scope
        | affected_entity_classes
        | {task.downstream_id}
    )
    if len(affected) > _MAX_CLASS_CLOSURE_SCOPE:
        raise RebuildWorkerError(
            "class closure scope exceeds the verified rebuild bound"
        )
    return _ClassClosureSeed(
        root_class_id=task.downstream_id,
        affected_class_ids=tuple(sorted(affected)),
        event_source_revision_proof=event_proof,
    )


def _seed_for_task(
    connection: sqlite3.Connection,
    task: RebuildTask,
) -> object:
    if task.downstream_kind == "FACT":
        return _fact_seed(connection, task.downstream_id)
    if task.downstream_kind == "NATIVE_FUNCTION":
        return _native_seed(connection, task.downstream_id)
    if task.downstream_kind == "CLASS_CLOSURE":
        return _class_closure_seed(connection, task)
    if task.downstream_kind == "PROJECTION":
        index = task.downstream_id - 1
        return (
            _PROJECTION_NAMES[index]
            if 0 <= index < len(_PROJECTION_NAMES)
            else None
        )
    return task.downstream_id


def _inspect_fact(
    connection: sqlite3.Connection,
    seed: object,
) -> RebuildTargetState:
    if not isinstance(seed, tuple) or len(seed) != 4:
        return _state(
            {"seed": seed},
            complete=False,
            gaps=("FACT_TARGET_NOT_FOUND",),
            summary="fact target identity is missing",
        )
    values = _rows(
        connection,
        """
        SELECT
            fact.fact_id, fact.current, fact.status, fact.confidence,
            fact.value_kind, fact.value_text, fact.value_number,
            fact.value_integer, fact.value_json,
            evidence.source_revision_id, evidence.evidence_uri,
            revision.freshness_status
        FROM facts AS fact
        LEFT JOIN fact_evidence AS evidence
          ON evidence.fact_id=fact.fact_id
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=evidence.source_revision_id
        WHERE fact.subject_entity_id=?
          AND fact.fact_type=?
          AND fact.fact_name=?
          AND fact.scope_kind=?
        ORDER BY fact.fact_id, evidence.source_revision_id,
                 evidence.evidence_uri
        """,
        seed,
    )
    complete = any(
        int(row[1]) == 1
        and str(row[2]).upper() != "STALE"
        and str(row[11]).upper() == "FRESH"
        for row in values
    )
    return _state(
        {"seed": seed, "rows": values},
        complete=complete,
        summary=f"{len(values)} fact/evidence rows",
    )


def _inspect_effective(
    connection: sqlite3.Connection,
    entity_id: int,
) -> RebuildTargetState:
    expected = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT fact.fact_name
            FROM asset_class_assignments AS target
            JOIN class_closure AS closure
              ON closure.descendant_class_id=target.class_id
            JOIN asset_class_assignments AS owner
              ON owner.class_id=closure.ancestor_class_id
             AND owner.assignment_kind='GENERATED_CLASS'
            JOIN facts AS fact
              ON fact.subject_entity_id=owner.entity_id
            WHERE target.entity_id=?
              AND target.assignment_kind='GENERATED_CLASS'
              AND fact.fact_type='DECLARED_DEFAULT'
              AND fact.scope_kind='DECLARED'
              AND fact.current=1
            """,
            (entity_id,),
        )
    }
    effective = _rows(
        connection,
        """
        SELECT fact_type, fact_name, fact_id, inherited_from_entity_id,
               resolution_chain_json, resolution_status,
               source_revision_set_hash
        FROM effective_facts
        WHERE entity_id=?
        ORDER BY fact_type, fact_name
        """,
        (entity_id,),
    )
    candidates = _rows(
        connection,
        """
        SELECT fact_type, fact_name, candidate_fact_id,
               declared_on_entity_id, inheritance_depth, path_status,
               selected, rejection_reason
        FROM effective_fact_candidates
        WHERE entity_id=?
        ORDER BY fact_type, fact_name, candidate_fact_id
        """,
        (entity_id,),
    )
    actual = {str(row[1]) for row in effective}
    gaps = {
        str(row[5]).upper()
        for row in effective
        if str(row[5]).upper() != "RESOLVED"
    }
    complete = expected == actual and not gaps
    return _state(
        {
            "entityId": entity_id,
            "expected": sorted(expected),
            "effective": effective,
            "candidates": candidates,
        },
        complete=complete,
        gaps=gaps,
        summary=f"{len(effective)}/{len(expected)} effective facts",
    )


def _inspect_role(
    connection: sqlite3.Connection,
    entity_id: int,
) -> RebuildTargetState:
    metrics = _rows(
        connection,
        "SELECT * FROM role_metrics WHERE entity_id=?",
        (entity_id,),
    )
    signals = _rows(
        connection,
        "SELECT * FROM role_signal_metrics WHERE entity_id=?",
        (entity_id,),
    )
    roles = _rows(
        connection,
        """
        SELECT role.role, role.confidence, role.status, role.reasons_json,
               role.classifier_version, role.source_revision_id,
               revision.freshness_status
        FROM knowledge_roles AS role
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=role.source_revision_id
        WHERE role.entity_id=?
        ORDER BY role.role
        """,
        (entity_id,),
    )
    depth = _rows(
        connection,
        """
        SELECT depth_policy, reasons_json, classifier_version
        FROM knowledge_depth_policies
        WHERE entity_id=?
        """,
        (entity_id,),
    )
    complete = (
        len(metrics) == 1
        and len(signals) == 1
        and bool(roles)
        and len(depth) == 1
        and all(
            str(row[2]).upper() != "STALE"
            and (
                row[5] is None
                or str(row[6]).upper() == "FRESH"
            )
            for row in roles
        )
    )
    return _state(
        {
            "entityId": entity_id,
            "metrics": metrics,
            "signals": signals,
            "roles": roles,
            "depth": depth,
        },
        complete=complete,
        summary=f"{len(roles)} role rows",
    )


def _inspect_domain(
    connection: sqlite3.Connection,
    entity_id: int,
) -> RebuildTargetState:
    values = _rows(
        connection,
        """
        SELECT membership.domain_id, membership.membership_kind,
               membership.confidence, membership.status,
               membership.evidence_id, membership.ontology_version,
               membership.source_revision_id,
               revision.freshness_status
        FROM domain_memberships AS membership
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=membership.source_revision_id
        WHERE membership.entity_id=?
        ORDER BY membership.domain_id, membership.membership_kind,
                 membership.evidence_id
        """,
        (entity_id,),
    )
    stale = [
        row
        for row in values
        if str(row[3]).upper() == "STALE"
        or (
            row[6] is not None
            and str(row[7]).upper() != "FRESH"
        )
    ]
    return _state(
        {"entityId": entity_id, "memberships": values},
        complete=not stale,
        gaps=("DOMAIN_SOURCE_STALE",) if stale else (),
        summary=f"{len(values)} domain memberships",
    )


def _inspect_edge(
    connection: sqlite3.Connection,
    entity_id: int,
) -> RebuildTargetState:
    values = _rows(
        connection,
        """
        SELECT edge.edge_id, edge.target_entity_id, edge.edge_type,
               edge.edge_strength, edge.status, edge.confidence,
               edge.source_revision_id, edge.evidence_uri,
               edge.source_property, edge.source_graph,
               revision.freshness_status
        FROM edges AS edge
        JOIN source_revisions AS revision
          ON revision.revision_id=edge.source_revision_id
        WHERE edge.source_entity_id=?
        ORDER BY edge.edge_id
        """,
        (entity_id,),
    )
    stale = [
        row
        for row in values
        if str(row[4]).upper() == "STALE"
        or str(row[10]).upper() != "FRESH"
    ]
    return _state(
        {"entityId": entity_id, "edges": values},
        complete=not stale,
        gaps=("EDGE_SOURCE_STALE",) if stale else (),
        summary=f"{len(values)} outgoing edges",
    )


def _inspect_one_class_closure(
    connection: sqlite3.Connection,
    class_id: int,
) -> RebuildTargetState:
    from .class_hierarchy import _closure_for_descendant, _graph

    class_row = connection.execute(
        """
        SELECT class_id, source_revision_id, revision.freshness_status
        FROM classes AS class
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=class.source_revision_id
        WHERE class.class_id=?
        """,
        (class_id,),
    ).fetchone()
    if class_row is None:
        return _state(
            {"classId": class_id},
            complete=False,
            gaps=("CLASS_TARGET_NOT_FOUND",),
            summary="class target is missing",
        )
    parents, _children = _graph(connection)
    expected_map, cycle_nodes, _ambiguous_parent = (
        _closure_for_descendant(class_id, parents)
    )
    expected = sorted(
        (
            int(ancestor),
            class_id,
            int(depth),
            "AMBIGUOUS" if cycle_nodes else str(path_status),
        )
        for ancestor, (depth, path_status) in expected_map.items()
    )
    closure = _rows(
        connection,
        """
        SELECT ancestor_class_id, descendant_class_id, depth, path_status
        FROM class_closure
        WHERE descendant_class_id=?
        ORDER BY depth, ancestor_class_id
        """,
        (class_id,),
    )
    gaps = _rows(
        connection,
        """
        SELECT gap_kind, detail, status
        FROM class_gaps
        WHERE class_id=?
        ORDER BY gap_kind, detail
        """,
        (class_id,),
    )
    self_row = any(
        int(row[0]) == class_id
        and int(row[1]) == class_id
        and int(row[2]) == 0
        for row in closure
    )
    blocking = {
        str(row[0]).upper()
        for row in gaps
        if str(row[2]).upper()
        in {"AMBIGUOUS", "NOT_RECOVERED", "SOURCE_NOT_AVAILABLE"}
    }
    source_stale = (
        class_row[1] is not None
        and str(class_row[2]).upper() != "FRESH"
    )
    actual = sorted(
        (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            str(row[3]),
        )
        for row in closure
    )
    return _state(
        {
            "classId": class_id,
            "expected": expected,
            "closure": actual,
            "gaps": gaps,
        },
        complete=(
            self_row
            and actual == expected
            and not blocking
            and not source_stale
        ),
        gaps=(
            (*blocking, "CLASS_SOURCE_STALE")
            if source_stale
            else blocking
        ),
        summary=(
            f"{len(actual)}/{len(expected)} verified closure rows"
        ),
    )


def _class_source_revision_rows(
    connection: sqlite3.Connection,
    class_ids: Sequence[int],
) -> list[tuple[object, ...]]:
    if not class_ids:
        return []
    placeholders = ",".join("?" for _ in class_ids)
    return _rows(
        connection,
        f"""
        SELECT class.class_id, class.source_revision_id,
               revision.source_fingerprint,
               revision.freshness_status
        FROM classes AS class
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=class.source_revision_id
        WHERE class.class_id IN ({placeholders})
        ORDER BY class.class_id
        """,
        tuple(class_ids),
    )


def _class_source_revision_proof(
    connection: sqlite3.Connection,
    class_ids: Sequence[int],
) -> tuple[str, list[tuple[object, ...]]]:
    rows = _class_source_revision_rows(connection, class_ids)
    normalized = [
        (
            int(class_id),
            int(revision_id) if revision_id is not None else None,
            str(source_fingerprint or ""),
            str(freshness_status or ""),
        )
        for (
            class_id,
            revision_id,
            source_fingerprint,
            freshness_status,
        ) in rows
    ]
    return _digest(normalized), normalized


def _inspect_class_closure(
    connection: sqlite3.Connection,
    seed: object,
) -> RebuildTargetState:
    if not isinstance(seed, _ClassClosureSeed):
        return _state(
            {"seed": repr(seed)},
            complete=False,
            gaps=("CLASS_REBUILD_SCOPE_MISSING",),
            summary="class closure rebuild scope is missing",
        )
    inspections = [
        (
            class_id,
            _inspect_one_class_closure(connection, class_id),
        )
        for class_id in seed.affected_class_ids
    ]
    source_proof, source_rows = _class_source_revision_proof(
        connection, seed.affected_class_ids
    )
    source_bound = {
        int(row[0]) for row in source_rows
    } == set(seed.affected_class_ids)
    event_proof_matches = (
        not seed.event_source_revision_proof
        or seed.event_source_revision_proof == source_proof
    )
    gaps = {
        gap
        for _class_id, state in inspections
        for gap in state.gap_codes
    }
    if not source_bound:
        gaps.add("CLASS_SCOPE_SOURCE_MISSING")
    if not event_proof_matches:
        gaps.add("CLASS_SOURCE_REVISION_CHANGED_AFTER_EVENT")
    complete_count = sum(state.complete for _class_id, state in inspections)
    return _state(
        {
            "rootClassId": seed.root_class_id,
            "affectedClassIds": seed.affected_class_ids,
            "classStates": [
                {
                    "classId": class_id,
                    "digest": state.digest,
                    "complete": state.complete,
                    "gaps": state.gap_codes,
                }
                for class_id, state in inspections
            ],
            "sourceRevisions": source_rows,
            "sourceRevisionProof": source_proof,
            "eventSourceRevisionProof": (
                seed.event_source_revision_proof
            ),
        },
        complete=(
            complete_count == len(inspections)
            and source_bound
            and event_proof_matches
        ),
        gaps=gaps,
        summary=(
            f"{complete_count}/{len(inspections)} affected class closures "
            "verified"
        ),
    )


def _entity_uri(
    connection: sqlite3.Connection,
    entity_id: int,
) -> str | None:
    row = connection.execute(
        "SELECT canonical_uri FROM entities WHERE entity_id=?",
        (entity_id,),
    ).fetchone()
    return None if row is None else str(row[0])


def _inspect_registration(
    connection: sqlite3.Connection,
    entity_id: int,
) -> RebuildTargetState:
    uri = _entity_uri(connection, entity_id)
    if uri is None:
        return _state(
            {"entityId": entity_id},
            complete=False,
            gaps=("REGISTRATION_ENTITY_NOT_FOUND",),
            summary="registration entity is missing",
        )
    values = _rows(
        connection,
        """
        SELECT registration_id, owner_uri, target_uri, registration_type,
               source_property, evidence_uri, scope_kind, confidence,
               status, source_revision_id, extractor_version, match_method,
               revision.freshness_status
        FROM typed_registrations AS registration
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=registration.source_revision_id
        WHERE owner_uri=? OR target_uri=?
        ORDER BY registration_id
        """,
        (uri, uri),
    )
    stale = [
        row
        for row in values
        if str(row[8]).upper() == "STALE"
        or (
            row[9] is not None
            and str(row[12]).upper() != "FRESH"
        )
    ]
    return _state(
        {"entityId": entity_id, "uri": uri, "registrations": values},
        complete=not stale,
        gaps=("REGISTRATION_SOURCE_STALE",) if stale else (),
        summary=f"{len(values)} registration rows",
    )


def _inspect_native(
    connection: sqlite3.Connection,
    seed: object,
) -> RebuildTargetState:
    if not isinstance(seed, tuple) or len(seed) != 3:
        return _state(
            {"seed": seed},
            complete=False,
            gaps=("NATIVE_FUNCTION_TARGET_NOT_FOUND",),
            summary="native function identity is missing",
        )
    canonical_uri, qualified_symbol, rva = seed
    functions = _rows(
        connection,
        """
        SELECT function.native_function_id, function.canonical_uri,
               function.qualified_symbol, function.rva, function.signature,
               function.status, function.confidence,
               function.source_revision_id, revision.freshness_status
        FROM native_functions AS function
        JOIN source_revisions AS revision
          ON revision.revision_id=function.source_revision_id
        WHERE function.canonical_uri=?
           OR (
               function.qualified_symbol=?
               AND function.rva=?
           )
        ORDER BY function.native_function_id
        """,
        (canonical_uri, qualified_symbol, rva),
    )
    function_ids = [int(row[0]) for row in functions]
    if function_ids:
        placeholders = ",".join("?" for _value in function_ids)
        fields = _rows(
            connection,
            f"""
            SELECT native_function_id, field_name, field_offset,
                   access_kind, instruction_or_slice_uri, status,
                   confidence
            FROM native_field_accesses
            WHERE native_function_id IN ({placeholders})
            ORDER BY native_function_id, field_access_id
            """,
            function_ids,
        )
    else:
        fields = []
    gold = _rows(
        connection,
        """
        SELECT target_id, native_function_id, status, gap_code
        FROM native_gold_targets
        WHERE qualified_symbol=? AND expected_rva=?
        ORDER BY target_id
        """,
        (qualified_symbol, rva),
    )
    confirmed = any(
        str(row[5]).upper() != "STALE"
        and str(row[8]).upper() == "FRESH"
        for row in functions
    )
    gap_codes = {
        str(row[3]).upper()
        for row in gold
        if str(row[2]).upper() == "GAP" and str(row[3]).strip()
    }
    return _state(
        {
            "seed": seed,
            "functions": functions,
            "fields": fields,
            "gold": gold,
        },
        complete=confirmed and not gap_codes,
        gaps=gap_codes,
        summary=f"{len(functions)} matching native functions",
    )


def _inspect_blueprint_native(
    connection: sqlite3.Connection,
    entity_id: int,
) -> RebuildTargetState:
    values = _rows(
        connection,
        """
        SELECT link.link_id, link.blueprint_graph_evidence_uri,
               link.blueprint_function_name, link.native_function_id,
               link.native_evidence_uri, link.resolution_method,
               link.status, link.confidence,
               link.blueprint_graph_source_revision_id,
               graph_revision.freshness_status,
               function.status, native_revision.freshness_status
        FROM native_blueprint_links AS link
        JOIN source_revisions AS graph_revision
          ON graph_revision.revision_id=
             link.blueprint_graph_source_revision_id
        LEFT JOIN native_functions AS function
          ON function.native_function_id=link.native_function_id
        LEFT JOIN source_revisions AS native_revision
          ON native_revision.revision_id=function.source_revision_id
        WHERE link.blueprint_entity_id=?
        ORDER BY link.link_id
        """,
        (entity_id,),
    )
    incomplete = [
        row
        for row in values
        if str(row[6]).upper() != "CONFIRMED"
        or str(row[7]).upper() not in {"HIGH", "CONFIRMED"}
        or str(row[9]).upper() != "FRESH"
        or row[3] is None
        or str(row[10]).upper() == "STALE"
        or str(row[11]).upper() != "FRESH"
    ]
    return _state(
        {"entityId": entity_id, "links": values},
        complete=not incomplete,
        gaps=(
            ("BLUEPRINT_NATIVE_LINK_NOT_CONFIRMED",)
            if incomplete
            else ()
        ),
        summary=f"{len(values)} Blueprint/native links",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _projection_file_state(
    projection_dir: Path | None,
    projection_name: str,
) -> tuple[dict[str, object], bool, tuple[str, ...]]:
    if projection_dir is None:
        return (
            {"configured": False},
            False,
            ("PROJECTION_CONTEXT_NOT_CONFIGURED",),
        )
    path = projection_dir / f"{projection_name}.sqlite"
    if not path.is_file():
        return (
            {"path": path.name, "exists": False},
            False,
            ("PROJECTION_ARTIFACT_MISSING",),
        )
    try:
        projection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        try:
            integrity = str(
                projection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            foreign_key_violations = len(
                list(projection.execute("PRAGMA foreign_key_check"))
            )
            table_names = {
                str(row[0])
                for row in projection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table'
                    """
                )
            }
            required_tables = {
                "metadata",
                "projection_rows",
                "projection_evidence",
                "projection_lineage",
                "projection_reviews",
            }
            if not required_tables.issubset(table_names):
                missing = sorted(required_tables - table_names)
                raise sqlite3.DatabaseError(
                    "missing projection tables: " + ", ".join(missing)
                )
            metadata = dict(
                projection.execute(
                    "SELECT key, value FROM metadata ORDER BY key"
                )
            )
            row_count = int(
                projection.execute(
                    "SELECT COUNT(*) FROM projection_rows"
                ).fetchone()[0]
            )
            stale_evidence = int(
                projection.execute(
                    """
                    SELECT COUNT(*)
                    FROM projection_evidence
                    WHERE UPPER(freshness_status)<>'FRESH'
                    """
                ).fetchone()[0]
            )
            evidence_count_mismatches = int(
                projection.execute(
                    """
                    SELECT COUNT(*)
                    FROM projection_rows AS row
                    WHERE row.evidence_count<>(
                        SELECT COUNT(*)
                        FROM projection_evidence AS evidence
                        WHERE evidence.fact_id=row.fact_id
                    )
                    """
                ).fetchone()[0]
            )
            review_contract: list[dict[str, object]] = []
            for review in projection.execute(
                """
                SELECT review.review_id, row.canonical_uri,
                       row.fact_type, row.fact_name, row.value_kind,
                       row.value_text, row.value_number,
                       row.value_integer, row.value_json,
                       review.evidence_uri
                FROM projection_reviews AS review
                JOIN projection_rows AS row
                  ON row.fact_id=review.fact_id
                ORDER BY review.review_id
                """
            ):
                value_kind = str(review[4])
                contract: dict[str, object] = {
                    "reviewId": str(review[0]),
                    "canonicalUri": str(review[1]),
                    "factType": str(review[2]),
                    "factName": str(review[3]),
                    "valueKind": value_kind,
                    "evidenceUri": str(review[9]),
                }
                value_fields = {
                    "TEXT": ("valueText", review[5]),
                    "ENTITY_REF": ("valueText", review[5]),
                    "NUMBER": ("valueNumber", review[6]),
                    "INTEGER": ("valueInteger", review[7]),
                    "BOOLEAN": ("valueInteger", review[7]),
                    "JSON": ("valueJson", review[8]),
                }
                if value_kind in value_fields:
                    key, value = value_fields[value_kind]
                    contract[key] = value
                review_contract.append(contract)
            recomputed_digest = (
                compute_projection_artifact_content_digest(projection)
            )
        finally:
            projection.close()
    except (OSError, sqlite3.Error) as error:
        return (
            {"path": path.name, "error": str(error)},
            False,
            ("PROJECTION_ARTIFACT_INVALID",),
        )
    except (KeyError, TypeError, ValueError) as error:
        return (
            {"path": path.name, "error": str(error)},
            False,
            ("PROJECTION_ARTIFACT_INVALID",),
        )
    valid = (
        integrity == "ok"
        and foreign_key_violations == 0
        and metadata.get("schema_version") == PROJECTION_SCHEMA_VERSION
        and metadata.get("projection_name") == projection_name
        and bool(metadata.get("projection_version"))
        and bool(metadata.get("source_revision_set_hash"))
        and bool(metadata.get("ontology_version"))
        and metadata.get("truth_source") == "core.sqlite"
        and "review_version" in metadata
        and bool(metadata.get("review_status"))
        and metadata.get("content_digest") == recomputed_digest
        and stale_evidence == 0
        and evidence_count_mismatches == 0
    )
    return (
        {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "integrity": integrity,
            "foreignKeyViolations": foreign_key_violations,
            "rowCount": row_count,
            "staleEvidence": stale_evidence,
            "evidenceCountMismatches": evidence_count_mismatches,
            "recomputedContentDigest": recomputed_digest,
            "reviewContract": review_contract,
            "metadata": metadata,
        },
        valid,
        () if valid else ("PROJECTION_ARTIFACT_INVALID",),
    )


def _inspect_projection(
    connection: sqlite3.Connection,
    projection_dir: Path | None,
    seed: object,
) -> RebuildTargetState:
    if not isinstance(seed, str):
        return _state(
            {"seed": seed},
            complete=False,
            gaps=("PROJECTION_ID_UNKNOWN",),
            summary="projection ID is outside the ontology projection set",
        )
    runs = _rows(
        connection,
        """
        SELECT projection_version, source_revision_set_hash,
               ontology_version, built_at, row_count, validation_status
        FROM projection_runs
        WHERE projection_name=?
        ORDER BY projection_version
        """,
        (seed,),
    )
    file_state, file_valid, file_gaps = _projection_file_state(
        projection_dir,
        seed,
    )
    metadata = file_state.get("metadata")
    core_content_matches = False
    core_content_digest = ""
    core_content_error = ""
    if file_valid and isinstance(metadata, dict):
        review_contract = file_state.get("reviewContract")
        try:
            core_content_digest = compute_core_projection_content_digest(
                connection,
                projection_name=seed,
                fact_types=DOMAIN_PROJECTIONS[seed],
                ontology_version=str(metadata["ontology_version"]),
                review_version=str(
                    metadata.get("review_version") or ""
                ),
                reviews=(
                    review_contract
                    if isinstance(review_contract, list)
                    else ()
                ),
            )
            core_content_matches = (
                core_content_digest == metadata.get("content_digest")
            )
        except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
            core_content_error = f"{type(error).__name__}: {error}"
    file_state["coreContentDigest"] = core_content_digest
    file_state["coreContentError"] = core_content_error
    matching_run = (
        isinstance(metadata, dict)
        and any(
            str(row[5]).upper() == "VALID"
            and str(row[0]) == metadata.get("projection_version")
            and str(row[1]) == metadata.get(
                "source_revision_set_hash"
            )
            and str(row[2]) == metadata.get("ontology_version")
            and int(row[4]) == file_state.get("rowCount")
            for row in runs
        )
    )
    gaps = list(file_gaps)
    if file_valid and not matching_run:
        gaps.append("PROJECTION_CORE_ARTIFACT_MISMATCH")
    if file_valid and not core_content_matches:
        gaps.append("PROJECTION_CORE_CONTENT_MISMATCH")
    return _state(
        {"projection": seed, "runs": runs, "artifact": file_state},
        complete=(
            bool(matching_run)
            and file_valid
            and core_content_matches
        ),
        gaps=gaps,
        summary=f"{seed}: {len(runs)} projection runs",
    )


def _inspect_query_snapshot(
    cache: sqlite3.Connection | None,
    revision_id: int,
) -> RebuildTargetState:
    if cache is None:
        return _state(
            {"revisionId": revision_id, "configured": False},
            complete=False,
            gaps=("CACHE_CONTEXT_NOT_CONFIGURED",),
            summary="cache database is not configured",
        )
    tables = (
        "query_snapshots",
        "context_packs",
        "answer_plans",
        "materialized_neighborhoods",
    )
    content = {
        table: _rows(cache, f'SELECT * FROM "{table}" ORDER BY 1')
        for table in tables
    }
    count = sum(len(values) for values in content.values())
    return _state(
        {"revisionId": revision_id, "cache": content},
        complete=count == 0,
        summary=f"{count} cached rows remain",
    )


def _inspect_target(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
    task: RebuildTask,
    seed: object,
    *,
    projection_dir: Path | None = None,
) -> RebuildTargetState:
    kind = task.downstream_kind
    target_id = task.downstream_id
    if kind == "FACT":
        return _inspect_fact(connection, seed)
    if kind == "EFFECTIVE_ENTITY":
        return _inspect_effective(connection, target_id)
    if kind == "ROLE_ENTITY":
        return _inspect_role(connection, target_id)
    if kind == "DOMAIN_ENTITY":
        return _inspect_domain(connection, target_id)
    if kind == "EDGE_ENTITY":
        return _inspect_edge(connection, target_id)
    if kind == "CLASS_CLOSURE":
        return _inspect_class_closure(connection, seed)
    if kind == "REGISTRATION_ENTITY":
        return _inspect_registration(connection, target_id)
    if kind == "NATIVE_FUNCTION":
        return _inspect_native(connection, seed)
    if kind == "BLUEPRINT_NATIVE_ENTITY":
        return _inspect_blueprint_native(connection, target_id)
    if kind == "PROJECTION":
        return _inspect_projection(
            connection,
            (
                projection_dir
                if projection_dir is not None
                else _backend_projection_dir(backend)
            ),
            seed,
        )
    if kind == "QUERY_SNAPSHOT":
        return _inspect_query_snapshot(
            _backend_cache_connection(backend),
            target_id,
        )
    raise RebuildWorkerError(f"unsupported queued kind: {kind}")


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> frozenset[str]:
    return frozenset(
        str(row[1])
        for row in connection.execute(
            f'PRAGMA main.table_info("{table_name}")'
        )
    )


def _validate_schema(connection: sqlite3.Connection) -> None:
    temp_objects = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_temp_master
            WHERE type IN ('table', 'view')
            ORDER BY name
            """
        )
    ]
    if temp_objects:
        raise RebuildWorkerError(
            "rebuild worker rejects connections with TEMP tables/views: "
            + ", ".join(temp_objects)
        )
    requirements = {
        "invalidation_queue": {
            "event_id",
            "downstream_kind",
            "downstream_id",
            "dependency_reason",
            "status",
        },
        "invalidation_events": {
            "event_id",
            "payload_json",
            "created_at",
            "status",
        },
    }
    for table, required in requirements.items():
        missing = required - _table_columns(connection, table)
        if missing:
            raise RebuildWorkerError(
                f"{table} is missing required columns: "
                + ", ".join(sorted(missing))
            )


def _require_clean_connection(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RebuildWorkerError(
            "rebuild worker requires a transaction-free core connection"
        )
    if connection.row_factory is not None:
        raise RebuildWorkerError(
            "rebuild worker requires the default core row_factory"
        )


def _event_payload(
    connection: sqlite3.Connection,
    event_id: str,
) -> dict[str, object]:
    row = connection.execute(
        "SELECT payload_json FROM invalidation_events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise RebuildWorkerError(f"invalidation event not found: {event_id}")
    def reject_duplicates(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            str(row[0]),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise RebuildWorkerError(
            f"invalidation event payload is invalid: {event_id}"
        ) from error
    if not isinstance(payload, dict):
        raise RebuildWorkerError(
            f"invalidation event payload is not an object: {event_id}"
        )
    return payload


def _role_scope_proof(
    connection: sqlite3.Connection,
    task: RebuildTask,
) -> dict[str, object]:
    proof = _event_payload(connection, task.event_id).get("_roleScopeProof")
    if not isinstance(proof, dict):
        raise RebuildVerificationError("role dependency proof is missing")
    expected_keys = {
        "schema",
        "classifierVersion",
        "sourceRevisionId",
        "changedEntityIds",
        "roleEntityIds",
        "transitions",
        "proof",
    }
    changed = proof.get("changedEntityIds")
    role_ids = proof.get("roleEntityIds")
    source_revision_id = proof.get("sourceRevisionId")
    proof_uri = proof.get("proof")
    body = dict(proof)
    body.pop("proof", None)
    queued_role_ids = [
        row[0]
        for row in connection.execute(
            """
            SELECT downstream_id
            FROM invalidation_queue
            WHERE event_id=? AND downstream_kind='ROLE_ENTITY'
            ORDER BY downstream_id
            """,
            (task.event_id,),
        )
    ]
    def valid_ids(value: object) -> bool:
        return (
            isinstance(value, list)
            and bool(value)
            and all(type(item) is int and item > 0 for item in value)
            and value == sorted(set(value))
        )
    if (
        set(proof) != expected_keys
        or proof.get("schema")
        != "ark-kb-additive-role-dependency-scope/v1"
        or not isinstance(proof.get("classifierVersion"), str)
        or not proof.get("classifierVersion")
        or type(source_revision_id) is not int
        or source_revision_id < 1
        or not valid_ids(changed)
        or not valid_ids(role_ids)
        or not set(changed).issubset(role_ids)
        or role_ids != queued_role_ids
        or task.downstream_id not in role_ids
        or not isinstance(proof.get("transitions"), list)
        or proof_uri != "role-scope://" + _digest(body)
    ):
        raise RebuildVerificationError("role dependency proof is invalid")
    revision = connection.execute(
        "SELECT freshness_status FROM source_revisions WHERE revision_id=?",
        (source_revision_id,),
    ).fetchone()
    if revision is None or str(revision[0]).upper() != "FRESH":
        raise RebuildVerificationError(
            "role dependency proof source revision is not fresh"
        )
    return proof


def _attempt_record_is_valid(
    record: object,
    task: RebuildTask,
) -> bool:
    return (
        isinstance(record, dict)
        and record.get("schema") == _ATTEMPT_SCHEMA
        and record.get("eventId") == task.event_id
        and record.get("downstreamKind") == task.downstream_kind
        and record.get("downstreamId") == task.downstream_id
        and isinstance(record.get("token"), str)
        and bool(str(record["token"]).strip())
    )


def _attempt_token(
    connection: sqlite3.Connection,
    task: RebuildTask,
) -> str | None:
    attempts = _event_payload(connection, task.event_id).get(
        _ATTEMPTS_KEY
    )
    if not isinstance(attempts, dict):
        return None
    record = attempts.get(task.receipt_key)
    if not _attempt_record_is_valid(record, task):
        return None
    assert isinstance(record, dict)
    return str(record["token"])


def _issue_attempt_token(
    connection: sqlite3.Connection,
    task: RebuildTask,
    *,
    preserve_existing: bool,
) -> str:
    payload = _event_payload(connection, task.event_id)
    attempts = payload.setdefault(_ATTEMPTS_KEY, {})
    if not isinstance(attempts, dict):
        raise RebuildWorkerError("event rebuild attempts are invalid")
    existing = attempts.get(task.receipt_key)
    if preserve_existing and _attempt_record_is_valid(existing, task):
        assert isinstance(existing, dict)
        return str(existing["token"])
    token = uuid.uuid4().hex
    attempts[task.receipt_key] = {
        "schema": _ATTEMPT_SCHEMA,
        "eventId": task.event_id,
        "downstreamKind": task.downstream_kind,
        "downstreamId": task.downstream_id,
        "token": token,
    }
    connection.execute(
        """
        UPDATE invalidation_events
        SET payload_json=?
        WHERE event_id=?
        """,
        (_compact_json(payload), task.event_id),
    )
    return token


def _receipt_proof(receipt: dict[str, object]) -> str:
    return "rebuild-proof://" + _digest(receipt)


def _write_receipt(
    connection: sqlite3.Connection,
    task: RebuildTask,
    *,
    status: str,
    before: RebuildTargetState,
    after: RebuildTargetState,
    touched_tables: Iterable[str] = (),
    gap_code: str = "",
    detail: str = "",
    cache_hit: bool = False,
    projection_batch: dict[str, str] | None = None,
    verification: Mapping[str, object] | None = None,
) -> str:
    payload = _event_payload(connection, task.event_id)
    receipts = payload.setdefault(_RECEIPTS_KEY, {})
    if not isinstance(receipts, dict):
        raise RebuildWorkerError("event rebuild receipts are invalid")
    receipt: dict[str, object] = {
        "schema": _RECEIPT_SCHEMA,
        "eventId": task.event_id,
        "downstreamKind": task.downstream_kind,
        "downstreamId": task.downstream_id,
        "dependencyReason": task.dependency_reason,
        "status": status,
        "beforeDigest": before.digest,
        "afterDigest": after.digest,
        "complete": after.complete,
        "gapCode": gap_code,
        "detail": detail,
        "touchedTables": sorted(set(touched_tables)),
        "recovered": task.recovered,
        "cacheHit": cache_hit,
        "projectionBatch": projection_batch or {},
        "verification": dict(verification or {}),
    }
    proof = _receipt_proof(receipt)
    receipt["proof"] = proof
    receipts[task.receipt_key] = receipt
    if projection_batch is not None:
        payload[_PROJECTION_BATCH_KEY] = projection_batch
    connection.execute(
        """
        UPDATE invalidation_events
        SET payload_json=?
        WHERE event_id=?
        """,
        (_compact_json(payload), task.event_id),
    )
    return proof


def _prior_receipt(
    connection: sqlite3.Connection,
    task: RebuildTask,
) -> dict[str, object] | None:
    payload = _event_payload(connection, task.event_id)
    receipts = payload.get(_RECEIPTS_KEY)
    if not isinstance(receipts, dict):
        return None
    receipt = receipts.get(task.receipt_key)
    return receipt if isinstance(receipt, dict) else None


def _receipt_is_valid(
    receipt: dict[str, object],
    *,
    task: RebuildTask | None = None,
) -> bool:
    proof = str(receipt.get("proof") or "")
    body = dict(receipt)
    body.pop("proof", None)
    if (
        receipt.get("schema") != _RECEIPT_SCHEMA
        or proof != _receipt_proof(body)
    ):
        return False
    if task is None:
        return True
    return (
        receipt.get("eventId") == task.event_id
        and receipt.get("downstreamKind") == task.downstream_kind
        and receipt.get("downstreamId") == task.downstream_id
    )


def _projection_batch_hit(
    connection: sqlite3.Connection,
    task: RebuildTask,
    state: RebuildTargetState,
) -> bool:
    del connection, task, state
    return False


def _projection_batch_state(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for projection_id, name in enumerate(_PROJECTION_NAMES, start=1):
        state = _inspect_projection(
            connection,
            _backend_projection_dir(backend),
            name,
        )
        if state.complete:
            result[str(projection_id)] = state.digest
    return result


def _event_status(
    connection: sqlite3.Connection,
    event_id: str,
) -> str:
    counts = {
        str(status): int(count)
        for status, count in connection.execute(
            """
            SELECT status, COUNT(*)
            FROM invalidation_queue
            WHERE event_id=?
            GROUP BY status
            """,
            (event_id,),
        )
    }
    if not counts:
        return "APPLIED"
    if counts.get(RUNNING, 0):
        return RUNNING
    if counts.get(PENDING_REBUILD, 0):
        return PENDING_REBUILD
    if counts.get(FAILED, 0):
        return FAILED
    if counts.get(BLOCKED_GAP, 0):
        return BLOCKED_GAP
    if set(counts) == {SUCCEEDED}:
        return SUCCEEDED
    return FAILED


def _refresh_event_status(
    connection: sqlite3.Connection,
    event_id: str,
) -> None:
    cursor = connection.execute(
        "UPDATE invalidation_events SET status=? WHERE event_id=?",
        (_event_status(connection, event_id), event_id),
    )
    if cursor.rowcount != 1:
        raise RebuildWorkerError(
            f"invalidation event disappeared: {event_id}"
        )


def _set_task_status(
    connection: sqlite3.Connection,
    task: RebuildTask,
    status: str,
    *,
    require_running: bool,
) -> None:
    suffix = " AND status=?" if require_running else ""
    parameters: tuple[object, ...] = (
        status,
        task.event_id,
        task.downstream_kind,
        task.downstream_id,
    )
    if require_running:
        parameters = (*parameters, RUNNING)
    cursor = connection.execute(
        f"""
        UPDATE invalidation_queue
        SET status=?
        WHERE event_id=?
          AND downstream_kind=?
          AND downstream_id=?
          {suffix}
        """,
        parameters,
    )
    if cursor.rowcount != 1:
        raise RebuildWorkerError(
            "queue item left RUNNING unexpectedly"
        )
    _refresh_event_status(connection, task.event_id)


def _recover_running(
    connection: sqlite3.Connection,
    *,
    event_id: str | None = None,
    limit: int | None = None,
) -> set[tuple[str, str, int]]:
    where = "status=?"
    parameters: list[object] = [RUNNING]
    if event_id is not None:
        where += " AND event_id=?"
        parameters.append(event_id)
    limit_sql = ""
    if limit is not None:
        if limit < 0:
            raise ValueError("recovery limit must be non-negative")
        limit_sql = " LIMIT ?"
        parameters.append(limit)
    recovered = {
        (str(row[0]), str(row[1]), int(row[2]))
        for row in connection.execute(
            f"""
            SELECT event_id, downstream_kind, downstream_id
            FROM invalidation_queue
            WHERE {where}
            ORDER BY event_id, downstream_kind, downstream_id
            {limit_sql}
            """,
            parameters,
        )
    }
    connection.executemany(
        """
        UPDATE invalidation_queue
        SET status=?
        WHERE event_id=? AND downstream_kind=?
          AND downstream_id=? AND status=?
        """,
        [
            (PENDING_REBUILD, *identity, RUNNING)
            for identity in sorted(recovered)
        ],
    )
    for recovered_event in sorted({key[0] for key in recovered}):
        _refresh_event_status(connection, recovered_event)
    return recovered


def recover_running_rebuild_tasks(
    connection: sqlite3.Connection,
    *,
    event_id: str | None = None,
) -> int:
    """Recover orphaned work under the single-worker lease contract."""

    _require_clean_connection(connection)
    _validate_schema(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        recovered = _recover_running(connection, event_id=event_id)
        connection.commit()
        return len(recovered)
    except Exception:
        connection.rollback()
        raise


def _claim_next_task(
    connection: sqlite3.Connection,
    recovered: set[tuple[str, str, int]],
) -> RebuildTask | None:
    order_sql = "CASE queue.downstream_kind " + " ".join(
        f"WHEN ? THEN {index}"
        for index, _kind in enumerate(REBUILD_KIND_ORDER)
    ) + " ELSE 999 END"
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            f"""
            SELECT queue.event_id, queue.downstream_kind,
                   queue.downstream_id, queue.dependency_reason
            FROM invalidation_queue AS queue
            JOIN invalidation_events AS event
              ON event.event_id=queue.event_id
            WHERE queue.status=?
            ORDER BY event.created_at, event.event_id,
                     {order_sql}, queue.downstream_id
            LIMIT 1
            """,
            (PENDING_REBUILD, *REBUILD_KIND_ORDER),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        identity = (str(row[0]), str(row[1]), int(row[2]))
        task = RebuildTask(
            event_id=identity[0],
            downstream_kind=identity[1],
            downstream_id=identity[2],
            dependency_reason=str(row[3]),
            recovered=identity in recovered,
        )
        cursor = connection.execute(
            """
            UPDATE invalidation_queue
            SET status=?
            WHERE event_id=? AND downstream_kind=?
              AND downstream_id=? AND status=?
            """,
            (
                RUNNING,
                task.event_id,
                task.downstream_kind,
                task.downstream_id,
                PENDING_REBUILD,
            ),
        )
        if cursor.rowcount != 1:
            raise RebuildWorkerError(
                "queue item could not be atomically claimed"
            )
        _issue_attempt_token(
            connection,
            task,
            preserve_existing=task.recovered,
        )
        connection.execute(
            "UPDATE invalidation_events SET status=? WHERE event_id=?",
            (RUNNING, task.event_id),
        )
        connection.commit()
        return task
    except Exception:
        connection.rollback()
        raise


def _quoted_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _sql_literal(value: object) -> str:
    if isinstance(value, bool):
        raise RebuildVerificationError(
            "boolean row-scope values are not allowed"
        )
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value:
        return "'" + value.replace("'", "''") + "'"
    raise RebuildVerificationError("invalid row-scope value")


def _projection_scope(
    connection: sqlite3.Connection,
    task: RebuildTask,
    seed: object,
) -> tuple[str, ...]:
    if not isinstance(seed, str) or seed not in _PROJECTION_NAMES:
        return ()
    raw_ids = [
        row[0]
        for row in connection.execute(
            """
            SELECT downstream_id
            FROM invalidation_queue
            WHERE event_id=? AND downstream_kind='PROJECTION'
            ORDER BY downstream_id
            """,
            (task.event_id,),
        )
    ]
    if (
        not raw_ids
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= len(_PROJECTION_NAMES)
            for value in raw_ids
        )
    ):
        raise RebuildVerificationError(
            "projection event batch scope is invalid"
        )
    projection_ids = tuple(sorted(set(int(value) for value in raw_ids)))
    if task.downstream_id not in projection_ids:
        raise RebuildVerificationError(
            "projection task is outside its durable event batch"
        )
    expected_seed = _PROJECTION_NAMES[task.downstream_id - 1]
    if seed != expected_seed:
        raise RebuildVerificationError(
            "projection downstream ID does not match its canonical name"
        )
    return (expected_seed,)


def _row_scope_values(
    connection: sqlite3.Connection,
    task: RebuildTask,
    seed: object,
) -> tuple[object, ...] | None:
    kind = task.downstream_kind
    if kind == "QUERY_SNAPSHOT":
        # The four declared cache tables form one explicit invalidation batch.
        return None
    if kind == "CLASS_CLOSURE":
        if not isinstance(seed, _ClassClosureSeed):
            return ()
        return tuple(seed.affected_class_ids)
    if kind == "REGISTRATION_ENTITY":
        uri = _entity_uri(connection, task.downstream_id)
        return () if uri is None else (uri,)
    if kind == "PROJECTION":
        return _projection_scope(connection, task, seed)
    return (task.downstream_id,)


def _row_scope_predicate(
    rule: _RowScopeRule,
    values: tuple[object, ...] | None,
    row_alias: str,
    task: RebuildTask,
    seed: object,
) -> str:
    if rule.mode == "EXPLICIT_WHOLE_CACHE_BATCH":
        if (
            rule.columns
            or values is not None
            or task.downstream_kind != "QUERY_SNAPSHOT"
        ):
            raise RebuildVerificationError(
                "whole-cache scope requires the explicit query batch policy"
            )
        return "1"
    if rule.mode == "NATIVE_GOLD_IDENTITY":
        if (
            task.downstream_kind != "NATIVE_FUNCTION"
            or not isinstance(seed, tuple)
            or len(seed) != 3
        ):
            return "0"
        _canonical_uri, qualified_symbol, expected_rva = seed
        identity_predicate = (
            f"{row_alias}.\"native_function_id\" = "
            f"{_sql_literal(task.downstream_id)}"
            " OR ("
            f"{row_alias}.\"qualified_symbol\" = "
            f"{_sql_literal(str(qualified_symbol))}"
            " AND "
            f"{row_alias}.\"expected_rva\" = "
            f"{_sql_literal(str(expected_rva))}"
            ")"
        )
        return f"COALESCE(({identity_predicate}), 0)"
    if rule.mode != "SCOPED_VALUES":
        raise RebuildVerificationError(
            f"unknown row-scope policy mode: {rule.mode}"
        )
    if values is None:
        raise RebuildVerificationError(
            "row-scoped table cannot use a whole-table batch"
        )
    if not values:
        return "0"
    literals = ", ".join(_sql_literal(value) for value in values)
    predicate = " OR ".join(
        (
            f"{row_alias}.{_quoted_identifier(column)} "
            f"IN ({literals})"
        )
        for column in rule.columns
    )
    return f"COALESCE(({predicate}), 0)"


def _install_row_scope_guards(
    connection: sqlite3.Connection,
    task: RebuildTask,
    seed: object,
    *,
    cache_scope: bool,
) -> None:
    kind = task.downstream_kind
    if set(_ROW_SCOPE_RULES) != set(SUPPORTED_REBUILD_KINDS):
        raise RebuildVerificationError(
            "row-scope policies do not cover every supported rebuild kind"
        )
    rules = _ROW_SCOPE_RULES.get(kind)
    expected_tables = EXPECTED_REBUILD_WRITE_TABLES.get(kind)
    if rules is None or expected_tables is None:
        raise RebuildVerificationError(
            f"no row-scope policy for supported kind {kind}"
        )
    if set(rules) != set(expected_tables):
        raise RebuildVerificationError(
            f"row-scope tables differ from canonical write scope for {kind}"
        )
    is_cache_kind = kind == "QUERY_SNAPSHOT"
    if cache_scope != is_cache_kind:
        return
    values = _row_scope_values(connection, task, seed)
    for table, rule in rules.items():
        columns = {
            str(row[1])
            for row in connection.execute(
                f"PRAGMA table_info({_quoted_identifier(table)})"
            )
        }
        if not columns or not set(rule.columns).issubset(columns):
            raise RebuildVerificationError(
                f"row-scope columns are missing for {kind}:{table}"
            )
        trigger_base = (
            _ROW_SCOPE_GUARD_PREFIX
            + kind.lower()
            + "_"
            + table.lower()
        )
        table_sql = f"main.{_quoted_identifier(table)}"
        for operation, predicate in (
            (
                "insert",
                _row_scope_predicate(
                    rule,
                    values,
                    "NEW",
                    task,
                    seed,
                ),
            ),
            (
                "delete",
                _row_scope_predicate(
                    rule,
                    values,
                    "OLD",
                    task,
                    seed,
                ),
            ),
            (
                "update",
                (
                    "("
                    + _row_scope_predicate(
                        rule,
                        values,
                        "OLD",
                        task,
                        seed,
                    )
                    + ") AND ("
                    + _row_scope_predicate(
                        rule,
                        values,
                        "NEW",
                        task,
                        seed,
                    )
                    + ")"
                ),
            ),
        ):
            trigger_name = f"{trigger_base}_{operation}"
            connection.execute(
                f"""
                CREATE TEMP TRIGGER {_quoted_identifier(trigger_name)}
                BEFORE {operation.upper()} ON {table_sql}
                WHEN NOT ({predicate})
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'backend write exceeded durable task row scope'
                    );
                END
                """
            )


def _drop_row_scope_guards(connection: sqlite3.Connection) -> None:
    names = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_temp_schema
            WHERE type='trigger' AND name GLOB ?
            ORDER BY name
            """,
            (_ROW_SCOPE_GUARD_PREFIX + "*",),
        )
    ]
    for name in names:
        connection.execute(
            f"DROP TRIGGER IF EXISTS temp.{_quoted_identifier(name)}"
        )


def _row_scope_receipt(
    connection: sqlite3.Connection,
    task: RebuildTask,
    seed: object,
) -> dict[str, object]:
    kind = task.downstream_kind
    if kind == "QUERY_SNAPSHOT":
        return {
            "mode": "EXPLICIT_WHOLE_CACHE_BATCH",
            "eventId": task.event_id,
            "targetId": task.downstream_id,
            "tables": sorted(EXPECTED_REBUILD_WRITE_TABLES[kind]),
        }
    if kind == "PROJECTION":
        return {
            "mode": "EXACT_PROJECTION",
            "eventId": task.event_id,
            "targetId": task.downstream_id,
            "projectionName": _projection_scope(connection, task, seed)[0],
        }
    if kind == "CLASS_CLOSURE":
        if not isinstance(seed, _ClassClosureSeed):
            raise RebuildVerificationError(
                "class closure row scope has no verified affected set"
            )
        return {
            "mode": "AFFECTED_CLASS_IDS",
            "rootClassId": seed.root_class_id,
            "classIds": list(seed.affected_class_ids),
        }
    if kind == "REGISTRATION_ENTITY":
        uri = _entity_uri(connection, task.downstream_id)
        if uri is None:
            raise RebuildVerificationError(
                "registration row scope has no entity URI"
            )
        return {
            "mode": "ENTITY_URI",
            "targetId": task.downstream_id,
            "entityUri": uri,
        }
    if kind == "ROLE_ENTITY":
        if "_roleScopeProof" not in _event_payload(
            connection, task.event_id
        ):
            return {
                "mode": "TASK_TARGET_ID",
                "targetId": task.downstream_id,
            }
        proof = _role_scope_proof(connection, task)
        return {
            "mode": "PROVEN_PERCENTILE_CLOSURE",
            "targetId": task.downstream_id,
            "sourceRevisionId": proof["sourceRevisionId"],
            "changedEntityIds": proof["changedEntityIds"],
            "roleEntityIds": proof["roleEntityIds"],
            "dependencyProof": proof["proof"],
        }
    if kind == "NATIVE_FUNCTION":
        if not isinstance(seed, tuple) or len(seed) != 3:
            raise RebuildVerificationError(
                "native row scope has no verified function identity"
            )
        return {
            "mode": "NATIVE_FUNCTION_IDENTITY",
            "targetId": task.downstream_id,
            "canonicalUri": str(seed[0]),
            "qualifiedSymbol": str(seed[1]),
            "rva": str(seed[2]),
        }
    return {
        "mode": "TASK_TARGET_ID",
        "targetId": task.downstream_id,
    }


class _WriteTracker:
    def __init__(
        self,
        *,
        forbidden_tables: Iterable[str] = (),
        allowed_temp_tables: Iterable[str] = (),
    ) -> None:
        self.tables: set[str] = set()
        self.operations: set[tuple[str, str]] = set()
        self.forbidden_tables = frozenset(forbidden_tables)
        self.allowed_temp_tables = frozenset(allowed_temp_tables)

    def authorizer(
        self,
        action: int,
        argument_one: str | None,
        _argument_two: str | None,
        database: str | None,
        _source: str | None,
    ) -> int:
        if action in {
            sqlite3.SQLITE_CREATE_TEMP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_TABLE,
        }:
            return (
                sqlite3.SQLITE_OK
                if argument_one in self.allowed_temp_tables
                else sqlite3.SQLITE_DENY
            )
        if action in {
            sqlite3.SQLITE_CREATE_TEMP_INDEX,
            sqlite3.SQLITE_DROP_TEMP_INDEX,
        }:
            return (
                sqlite3.SQLITE_OK
                if self.allowed_temp_tables
                else sqlite3.SQLITE_DENY
            )
        if action in {
            sqlite3.SQLITE_TRANSACTION,
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_ANALYZE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
            sqlite3.SQLITE_CREATE_TEMP_VIEW,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_CREATE_VIEW,
            sqlite3.SQLITE_CREATE_VTABLE,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_DROP_TEMP_TRIGGER,
            sqlite3.SQLITE_DROP_TEMP_VIEW,
            sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_DROP_VIEW,
            sqlite3.SQLITE_DROP_VTABLE,
            sqlite3.SQLITE_REINDEX,
        }:
            return sqlite3.SQLITE_DENY
        if action in {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
        } and argument_one:
            if database != "main":
                if (
                    database == "temp"
                    and (
                        str(argument_one) in self.allowed_temp_tables
                        or (
                            str(argument_one) == "sqlite_temp_master"
                            and self.allowed_temp_tables
                        )
                    )
                ):
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            if str(argument_one) in self.forbidden_tables:
                return sqlite3.SQLITE_DENY
            table_name = str(argument_one)
            self.tables.add(table_name)
            operation = {
                sqlite3.SQLITE_INSERT: "INSERT",
                sqlite3.SQLITE_UPDATE: "UPDATE",
                sqlite3.SQLITE_DELETE: "DELETE",
            }[action]
            self.operations.add((table_name, operation))
        return sqlite3.SQLITE_OK


def _begin_backend_transactions(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
    task: RebuildTask,
    seed: object,
) -> tuple[sqlite3.Connection | None, _WriteTracker, _WriteTracker]:
    connection.execute("BEGIN IMMEDIATE")
    core_tracker = _WriteTracker(
        forbidden_tables={
            "invalidation_events",
            "invalidation_queue",
        },
        allowed_temp_tables=(
            {
                "effective_affected_entities",
                "effective_work",
            }
            if task.downstream_kind == "EFFECTIVE_ENTITY"
            else ()
        ),
    )
    cache_tracker = _WriteTracker()
    cache = (
        _backend_cache_connection(backend)
        if task.downstream_kind == "QUERY_SNAPSHOT"
        else None
    )
    if cache is not None:
        if cache.in_transaction:
            connection.set_authorizer(None)
            connection.rollback()
            raise RebuildWorkerError(
                "cache connection has an active transaction"
            )
        if cache.row_factory is not None:
            connection.set_authorizer(None)
            connection.rollback()
            raise RebuildWorkerError(
                "rebuild worker requires the default cache row_factory"
            )
        cache.execute("BEGIN IMMEDIATE")
    try:
        _install_row_scope_guards(
            connection,
            task,
            seed,
            cache_scope=False,
        )
        if cache is not None:
            _install_row_scope_guards(
                cache,
                task,
                seed,
                cache_scope=True,
            )
    except Exception:
        _drop_row_scope_guards(connection)
        if cache is not None:
            _drop_row_scope_guards(cache)
            if cache.in_transaction:
                cache.rollback()
        if connection.in_transaction:
            connection.rollback()
        raise
    connection.set_authorizer(core_tracker.authorizer)
    if cache is not None:
        cache.set_authorizer(cache_tracker.authorizer)
    return cache, core_tracker, cache_tracker


def _end_authorizers(
    connection: sqlite3.Connection,
    cache: sqlite3.Connection | None,
) -> None:
    connection.set_authorizer(None)
    _drop_row_scope_guards(connection)
    if cache is not None:
        cache.set_authorizer(None)
        _drop_row_scope_guards(cache)


def _rollback_backend_transactions(
    connection: sqlite3.Connection,
    cache: sqlite3.Connection | None,
) -> None:
    _end_authorizers(connection, cache)
    if cache is not None and cache.in_transaction:
        cache.rollback()
    if connection.in_transaction:
        connection.rollback()


def _commit_backend_transactions(
    connection: sqlite3.Connection,
    cache: sqlite3.Connection | None,
) -> None:
    _end_authorizers(connection, cache)
    # Cache/external work is replay-safe.  Commit it first so a crash before
    # the core receipt leaves RUNNING, which the next worker verifies/replays.
    if cache is not None:
        cache.commit()
    connection.commit()


def _external_marker_key(task: RebuildTask) -> str:
    identity = _digest(
        {
            "eventId": task.event_id,
            "downstreamKind": task.downstream_kind,
            "downstreamId": task.downstream_id,
        }
    )
    return f"_rebuild_worker_marker:{identity}"


def _external_marker_value(
    task: RebuildTask,
    attempt_token: str,
) -> str:
    marker: dict[str, object] = {
        "schema": _EXTERNAL_MARKER_SCHEMA,
        "eventId": task.event_id,
        "downstreamKind": task.downstream_kind,
        "downstreamId": task.downstream_id,
        "attemptToken": attempt_token,
    }
    marker["proof"] = "rebuild-external://" + _digest(marker)
    return _compact_json(marker)


def _external_marker_value_is_valid(
    value: object,
    task: RebuildTask,
    attempt_token: str,
) -> bool:
    try:
        marker = json.loads(str(value))
    except json.JSONDecodeError:
        return False
    if not isinstance(marker, dict):
        return False
    proof = str(marker.get("proof") or "")
    body = dict(marker)
    body.pop("proof", None)
    return (
        marker.get("schema") == _EXTERNAL_MARKER_SCHEMA
        and marker.get("eventId") == task.event_id
        and marker.get("downstreamKind") == task.downstream_kind
        and marker.get("downstreamId") == task.downstream_id
        and marker.get("attemptToken") == attempt_token
        and proof == "rebuild-external://" + _digest(body)
    )


def _write_external_marker(
    task: RebuildTask,
    attempt_token: str,
    *,
    cache: sqlite3.Connection | None,
    projection_dir: Path | None,
    projection_name: object,
) -> bool:
    key = _external_marker_key(task)
    value = _external_marker_value(task, attempt_token)
    if task.downstream_kind == "QUERY_SNAPSHOT":
        if cache is None:
            raise RebuildVerificationError(
                "query cache disappeared before durable marker"
            )
        cache.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            (key, value),
        )
        return True
    if task.downstream_kind == "PROJECTION":
        if projection_dir is None or not isinstance(
            projection_name, str
        ):
            raise RebuildVerificationError(
                "projection output disappeared before durable marker"
            )
        path = projection_dir / f"{projection_name}.sqlite"
        projection = sqlite3.connect(path)
        try:
            projection.execute(
                """
                INSERT OR REPLACE INTO metadata(key, value)
                VALUES (?, ?)
                """,
                (key, value),
            )
            projection.commit()
        finally:
            projection.close()
        return True
    return False


def _external_marker_matches(
    backend: RebuildBackend,
    task: RebuildTask,
    attempt_token: str,
    *,
    projection_dir: Path | None = None,
    projection_name: object = None,
) -> bool:
    key = _external_marker_key(task)
    if task.downstream_kind == "QUERY_SNAPSHOT":
        cache = _backend_cache_connection(backend)
        if cache is None:
            return False
        row = cache.execute(
            "SELECT value FROM metadata WHERE key=?",
            (key,),
        ).fetchone()
        return (
            row is not None
            and _external_marker_value_is_valid(
                row[0],
                task,
                attempt_token,
            )
        )
    if task.downstream_kind == "PROJECTION":
        directory = projection_dir or _backend_projection_dir(backend)
        if directory is None or not isinstance(projection_name, str):
            return False
        path = directory / f"{projection_name}.sqlite"
        if not path.is_file():
            return False
        try:
            projection = sqlite3.connect(
                f"file:{path.resolve().as_posix()}?mode=ro",
                uri=True,
            )
            try:
                row = projection.execute(
                    "SELECT value FROM metadata WHERE key=?",
                    (key,),
                ).fetchone()
            finally:
                projection.close()
        except sqlite3.Error:
            return False
        return (
            row is not None
            and _external_marker_value_is_valid(
                row[0],
                task,
                attempt_token,
            )
        )
    return False


def _cached_projection_outcome(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
    task: RebuildTask,
    seed: object,
) -> RebuildTaskOutcome | None:
    state = _inspect_target(connection, backend, task, seed)
    if not _projection_batch_hit(connection, task, state):
        return None
    connection.execute("BEGIN IMMEDIATE")
    try:
        proof = _write_receipt(
            connection,
            task,
            status=SUCCEEDED,
            before=state,
            after=state,
            cache_hit=True,
        )
        _set_task_status(
            connection,
            task,
            SUCCEEDED,
            require_running=True,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return RebuildTaskOutcome(
        task=task,
        status=SUCCEEDED,
        proof=proof,
        cache_hit=True,
    )


def _cached_external_replay_outcome(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
    task: RebuildTask,
    seed: object,
) -> RebuildTaskOutcome | None:
    if (
        not task.recovered
        or task.downstream_kind
        not in {"PROJECTION", "QUERY_SNAPSHOT"}
    ):
        return None
    attempt_token = _attempt_token(connection, task)
    if (
        attempt_token is None
        or not _external_marker_matches(
            backend,
            task,
            attempt_token,
            projection_name=seed,
        )
    ):
        return None
    state = _inspect_target(connection, backend, task, seed)
    if not state.complete:
        return None
    connection.execute("BEGIN IMMEDIATE")
    try:
        proof = _write_receipt(
            connection,
            task,
            status=SUCCEEDED,
            before=state,
            after=state,
            cache_hit=True,
        )
        _set_task_status(
            connection,
            task,
            SUCCEEDED,
            require_running=True,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return RebuildTaskOutcome(
        task=task,
        status=SUCCEEDED,
        proof=proof,
        cache_hit=True,
    )


def _finish_gap(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
    task: RebuildTask,
    seed: object,
    gap_code: str,
    detail: str,
) -> RebuildTaskOutcome:
    connection.execute("BEGIN IMMEDIATE")
    try:
        try:
            state = _inspect_target(connection, backend, task, seed)
        except Exception as inspection_error:
            state = _state(
                {
                    "kind": task.downstream_kind,
                    "targetId": task.downstream_id,
                    "inspectionError": (
                        f"{type(inspection_error).__name__}: "
                        f"{inspection_error}"
                    ),
                },
                complete=False,
                gaps=(gap_code,),
                summary="target inspection failed while recording gap",
            )
        proof = _write_receipt(
            connection,
            task,
            status=BLOCKED_GAP,
            before=state,
            after=state,
            gap_code=gap_code,
            detail=detail,
        )
        _set_task_status(
            connection,
            task,
            BLOCKED_GAP,
            require_running=True,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return RebuildTaskOutcome(
        task=task,
        status=BLOCKED_GAP,
        proof=proof,
        gap_code=gap_code,
        detail=detail,
    )


def _finish_failure(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
    task: RebuildTask,
    seed: object,
    error: Exception,
) -> RebuildTaskOutcome:
    connection.execute("BEGIN IMMEDIATE")
    try:
        try:
            state = _inspect_target(connection, backend, task, seed)
        except Exception as inspection_error:
            state = _state(
                {
                    "kind": task.downstream_kind,
                    "targetId": task.downstream_id,
                    "inspectionError": (
                        f"{type(inspection_error).__name__}: "
                        f"{inspection_error}"
                    ),
                },
                complete=False,
                summary="target inspection failed",
            )
        detail = f"{type(error).__name__}: {error}"
        proof = _write_receipt(
            connection,
            task,
            status=FAILED,
            before=state,
            after=state,
            detail=detail,
        )
        _set_task_status(
            connection,
            task,
            FAILED,
            require_running=False,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return RebuildTaskOutcome(
        task=task,
        status=FAILED,
        proof=proof,
        detail=detail,
    )


def _projection_staging_dir(
    backend: RebuildBackend,
    task: RebuildTask,
) -> Path | None:
    published_dir = _backend_projection_dir(backend)
    if (
        task.downstream_kind != "PROJECTION"
        or published_dir is None
    ):
        return None
    parent = published_dir.parent
    if (
        not parent.is_dir()
        or _is_reparse_point(parent)
        or _is_reparse_point(published_dir)
    ):
        raise RebuildBlockedGap(
            "PROJECTION_PARENT_NOT_AVAILABLE",
            f"Projection parent directory does not exist: {parent}",
        )
    return Path(
        tempfile.mkdtemp(
            prefix=".kb-rebuild-",
            dir=parent,
        )
    )


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _atomic_replace(source: Path, target: Path) -> None:
    if os.name != "nt":
        os.replace(source, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return
    import ctypes

    move_file_ex = ctypes.windll.kernel32.MoveFileExW
    move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    move_file_ex.restype = ctypes.c_int
    movefile_replace_existing = 0x1
    movefile_write_through = 0x8
    if not move_file_ex(
        str(source),
        str(target),
        movefile_replace_existing | movefile_write_through,
    ):
        raise ctypes.WinError()


def _publish_projection_staging(
    staging_dir: Path,
    output_dir: Path,
    allowed_projection_names: Iterable[str],
) -> "_ProjectionPublication":
    if (
        not output_dir.is_dir()
        or _is_reparse_point(output_dir)
        or _is_reparse_point(staging_dir)
        or staging_dir.stat().st_dev != output_dir.stat().st_dev
    ):
        raise RebuildVerificationError(
            "projection publication paths are unsafe or cross-volume"
        )
    allowed = frozenset(str(value) for value in allowed_projection_names)
    staged_projection_names = {
        path.stem for path in staging_dir.glob("*.sqlite") if path.is_file()
    }
    if (
        not allowed
        or not allowed.issubset(_PROJECTION_NAMES)
        or staged_projection_names != allowed
    ):
        raise RebuildVerificationError(
            "staged projection artifacts exceed the durable event batch"
        )
    sources = [
        staging_dir / f"{projection_name}.sqlite"
        for projection_name in _PROJECTION_NAMES
        if projection_name in allowed
        if (staging_dir / f"{projection_name}.sqlite").is_file()
    ]
    if not sources:
        raise RebuildVerificationError(
            "projection rebuild produced no publishable artifacts"
        )
    targets = [output_dir / source.name for source in sources]
    invalid_targets = [
        target
        for target in targets
        if target.exists()
        and (not target.is_file() or _is_reparse_point(target))
    ]
    if invalid_targets:
        raise RebuildVerificationError(
            "projection target is not a file: "
            + ", ".join(path.name for path in invalid_targets)
        )
    backup_dir = staging_dir / ".publish-backup"
    backup_dir.mkdir()
    backed_up: list[str] = []
    published: list[str] = []
    try:
        for target in targets:
            if target.is_file():
                backup = backup_dir / target.name
                shutil.copy2(target, backup)
                _fsync_file(backup)
                backed_up.append(target.name)
        for source, target in zip(sources, targets, strict=True):
            if _is_reparse_point(source):
                raise RebuildVerificationError(
                    "projection staging artifact is a reparse point"
                )
            _fsync_file(source)
            _atomic_replace(source, target)
            published.append(target.name)
    except Exception:
        for name in reversed(backed_up):
            backup = backup_dir / name
            if backup.is_file():
                _atomic_replace(backup, output_dir / name)
        for name in reversed(published):
            if name in backed_up:
                continue
            target = output_dir / name
            if target.is_file():
                target.unlink()
        raise
    return _ProjectionPublication(
        staging_dir=staging_dir,
        output_dir=output_dir,
        published=tuple(published),
        backed_up=tuple(backed_up),
    )


@dataclass(frozen=True)
class _ProjectionPublication:
    staging_dir: Path
    output_dir: Path
    published: tuple[str, ...]
    backed_up: tuple[str, ...]


def _restore_projection_publication(
    publication: _ProjectionPublication | None,
) -> None:
    if publication is None:
        return
    backup_dir = publication.staging_dir / ".publish-backup"
    for name in reversed(publication.backed_up):
        backup = backup_dir / name
        if backup.is_file():
            _atomic_replace(backup, publication.output_dir / name)
    for name in reversed(publication.published):
        if name in publication.backed_up:
            continue
        target = publication.output_dir / name
        if target.is_file():
            target.unlink()


def _cleanup_projection_staging(staging_dir: Path | None) -> None:
    if staging_dir is not None and staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)


def _class_revision_rebuild_verification(
    connection: sqlite3.Connection,
    *,
    seed: object,
    tracker: _WriteTracker,
    write_changes: int,
    after: RebuildTargetState,
) -> dict[str, object] | None:
    """Prove an identical closure was actually rebuilt against fresh inputs."""

    if not isinstance(seed, _ClassClosureSeed) or not after.complete:
        return None
    required_operations = {
        ("class_closure", "DELETE"),
        ("class_closure", "INSERT"),
    }
    if (
        write_changes < 2
        or not required_operations.issubset(tracker.operations)
    ):
        return None
    source_proof, source_rows = _class_source_revision_proof(
        connection, seed.affected_class_ids
    )
    if (
        len(source_rows) != len(seed.affected_class_ids)
        or any(
            revision_id is None
            or not str(source_fingerprint)
            or str(freshness_status).upper() != "FRESH"
            for (
                _class_id,
                revision_id,
                source_fingerprint,
                freshness_status,
            ) in source_rows
        )
        or (
            seed.event_source_revision_proof
            and seed.event_source_revision_proof != source_proof
        )
    ):
        return None
    return {
        "basis": "CLASS_REBUILT_AGAINST_SOURCE_REVISION",
        "classScope": list(seed.affected_class_ids),
        "classScopeDigest": _digest(seed.affected_class_ids),
        "sourceRevisionProof": source_proof,
        "eventSourceRevisionProof": (
            seed.event_source_revision_proof or source_proof
        ),
        "coreWriteChanges": write_changes,
        "writeOperations": [
            f"{table}:{operation}"
            for table, operation in sorted(tracker.operations)
        ],
    }


def _run_task(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
    task: RebuildTask,
) -> RebuildTaskOutcome:
    if task.downstream_kind not in SUPPORTED_REBUILD_KINDS:
        seed: object = task.downstream_id
        return _finish_failure(
            connection,
            backend,
            task,
            seed,
            RebuildWorkerError(
                f"unsupported queued kind: {task.downstream_kind}"
            ),
        )
    try:
        seed = _seed_for_task(connection, task)
    except Exception as error:
        return _finish_failure(
            connection,
            backend,
            task,
            task.downstream_id,
            error,
        )
    cached = _cached_projection_outcome(
        connection,
        backend,
        task,
        seed,
    )
    if cached is not None:
        return cached
    replayed = _cached_external_replay_outcome(
        connection,
        backend,
        task,
        seed,
    )
    if replayed is not None:
        return replayed

    attempt_token = _attempt_token(connection, task)
    if attempt_token is None:
        return _finish_failure(
            connection,
            backend,
            task,
            seed,
            RebuildWorkerError(
                "claimed queue item has no durable attempt token"
            ),
        )
    cache: sqlite3.Connection | None = None
    staging_dir: Path | None = None
    publication: _ProjectionPublication | None = None
    try:
        staging_dir = _projection_staging_dir(backend, task)
        cache, core_tracker, cache_tracker = _begin_backend_transactions(
            connection,
            backend,
            task,
            seed,
        )
        before = _inspect_target(
            connection,
            backend,
            task,
            seed,
        )
        core_changes_before = connection.total_changes
        scope = RebuildScope(
            task=task,
            core=GuardedConnection(connection),
            cache=GuardedConnection(cache) if cache is not None else None,
            projection_dir=staging_dir,
            class_closure_ids=(
                seed.affected_class_ids
                if isinstance(seed, _ClassClosureSeed)
                else ()
            ),
        )
        _dispatch_backend(backend, scope)
        core_write_changes = connection.total_changes - core_changes_before
        _end_authorizers(connection, cache)
        after = _inspect_target(
            connection,
            backend,
            task,
            seed,
            projection_dir=staging_dir,
        )
        touched = core_tracker.tables | cache_tracker.tables
        expected_tables = EXPECTED_REBUILD_WRITE_TABLES[
            task.downstream_kind
        ]
        unexpected_tables = touched - expected_tables
        if unexpected_tables:
            raise RebuildVerificationError(
                "backend touched tables outside canonical write scope: "
                + ", ".join(sorted(unexpected_tables))
            )
        relevant_write = bool(touched)

        if not after.complete and after.gap_codes:
            gap_code = after.gap_codes[0]
            detail = after.summary
            _rollback_backend_transactions(connection, cache)
            _cleanup_projection_staging(staging_dir)
            return _finish_gap(
                connection,
                backend,
                task,
                seed,
                gap_code,
                detail,
            )
        if not after.complete:
            raise RebuildVerificationError(
                "target remains incomplete after rebuild: "
                + after.summary
            )
        semantic_changed = before.digest != after.digest
        external_marked = _write_external_marker(
            task,
            attempt_token,
            cache=cache,
            projection_dir=staging_dir,
            projection_name=seed,
        )
        if external_marked:
            after = _inspect_target(
                connection,
                backend,
                task,
                seed,
                projection_dir=staging_dir,
            )
            if (
                not after.complete
                or not _external_marker_matches(
                    backend,
                    task,
                    attempt_token,
                    projection_dir=staging_dir,
                    projection_name=seed,
                )
            ):
                raise RebuildVerificationError(
                    "external rebuild marker failed verification"
                )
        external_kind = task.downstream_kind in {
            "PROJECTION",
            "QUERY_SNAPSHOT",
        }
        write_operations = (
            core_tracker.operations | cache_tracker.operations
        )
        explicit_whole_cache_invalidation = (
            task.downstream_kind == "QUERY_SNAPSHOT"
            and external_marked
            and touched == expected_tables
            and not core_tracker.operations
            and cache_tracker.operations
            == {
                (table, "DELETE")
                for table in EXPECTED_REBUILD_WRITE_TABLES[
                    "QUERY_SNAPSHOT"
                ]
            }
        )
        explicit_domain_owner_rebuild = (
            task.downstream_kind == "DOMAIN_ENTITY"
            and touched == {"domain_memberships"}
            and core_tracker.operations
            >= {("domain_memberships", "DELETE")}
            and not cache_tracker.operations
        )
        verification_basis = "TARGET_STATE_CHANGED"
        if not semantic_changed and explicit_whole_cache_invalidation:
            verification_basis = "EXPLICIT_WHOLE_CACHE_INVALIDATION"
        if not semantic_changed and explicit_domain_owner_rebuild:
            verification_basis = "VERIFIED_DOMAIN_OWNER_TARGET_STATE"
        verification: dict[str, object] = {
            "basis": verification_basis,
            "coreWriteChanges": core_write_changes,
            "writeOperations": [
                f"{table}:{operation}"
                for table, operation in sorted(write_operations)
            ],
        }
        class_revision_verification = (
            _class_revision_rebuild_verification(
                connection,
                seed=seed,
                tracker=core_tracker,
                write_changes=core_write_changes,
                after=after,
            )
            if (
                task.downstream_kind == "CLASS_CLOSURE"
                and not semantic_changed
            )
            else None
        )
        verified_work = (
            (
                semantic_changed
                and relevant_write
                and (not external_kind or external_marked)
            )
            or (
                relevant_write
                and class_revision_verification is not None
            )
            or explicit_whole_cache_invalidation
            or explicit_domain_owner_rebuild
        )
        if not verified_work:
            raise RebuildVerificationError(
                "backend produced no durable target rebuild"
        )
        if class_revision_verification is not None:
            verification = class_revision_verification
        verification["rowScope"] = _row_scope_receipt(
            connection,
            task,
            seed,
        )

        if staging_dir is not None:
            allowed_projection_names = _projection_scope(
                connection,
                task,
                seed,
            )
            publication = _publish_projection_staging(
                staging_dir,
                _backend_projection_dir(backend),
                allowed_projection_names,
            )
            after = _inspect_target(
                connection,
                backend,
                task,
                seed,
            )
            if not after.complete:
                raise RebuildVerificationError(
                    "published projection artifact failed verification"
                )
            if not _external_marker_matches(
                backend,
                task,
                attempt_token,
                projection_name=seed,
            ):
                raise RebuildVerificationError(
                    "published projection marker failed verification"
                )
        projection_batch = None
        proof = _write_receipt(
            connection,
            task,
            status=SUCCEEDED,
            before=before,
            after=after,
            touched_tables=touched,
            projection_batch=projection_batch,
            verification=verification,
        )
        _set_task_status(
            connection,
            task,
            SUCCEEDED,
            require_running=True,
        )
        _commit_backend_transactions(connection, cache)
        _cleanup_projection_staging(staging_dir)
        return RebuildTaskOutcome(
            task=task,
            status=SUCCEEDED,
            proof=proof,
            touched_tables=tuple(sorted(touched)),
        )
    except RebuildBlockedGap as gap:
        _rollback_backend_transactions(connection, cache)
        _restore_projection_publication(publication)
        _cleanup_projection_staging(staging_dir)
        return _finish_gap(
            connection,
            backend,
            task,
            seed,
            gap.gap_code,
            gap.detail,
        )
    except Exception as error:
        _rollback_backend_transactions(connection, cache)
        _restore_projection_publication(publication)
        _cleanup_projection_staging(staging_dir)
        return _finish_failure(
            connection,
            backend,
            task,
            seed,
            error,
        )


def _remaining_queue_counts(
    connection: sqlite3.Connection,
) -> tuple[int, int]:
    counts = {
        str(status): int(count)
        for status, count in connection.execute(
            """
            SELECT status, COUNT(*)
            FROM invalidation_queue
            WHERE status IN (?, ?)
            GROUP BY status
            """,
            (PENDING_REBUILD, RUNNING),
        )
    }
    return counts.get(PENDING_REBUILD, 0), counts.get(RUNNING, 0)


def drain_rebuild_queue(
    connection: sqlite3.Connection,
    backend: RebuildBackend,
    *,
    max_items: int = 100,
    recover_running: bool = True,
) -> RebuildDrainReport:
    """Rebuild at most ``max_items`` queue rows with durable verification."""

    if (
        isinstance(max_items, bool)
        or not isinstance(max_items, int)
        or max_items < 0
    ):
        raise ValueError("max_items must be a non-negative integer")
    if not isinstance(backend, RebuildBackend):
        raise TypeError("backend must be a RebuildBackend")
    _require_clean_connection(connection)
    _validate_schema(connection)

    recovered: set[tuple[str, str, int]] = set()
    if recover_running:
        connection.execute("BEGIN IMMEDIATE")
        try:
            recovered = _recover_running(
                connection,
                limit=max_items,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    outcomes: list[RebuildTaskOutcome] = []
    for _index in range(max_items):
        task = _claim_next_task(connection, recovered)
        if task is None:
            break
        outcomes.append(_run_task(connection, backend, task))
    pending, running = _remaining_queue_counts(connection)
    return RebuildDrainReport(
        max_items=max_items,
        recovered_running=len(recovered),
        attempted=len(outcomes),
        succeeded=sum(
            outcome.status == SUCCEEDED for outcome in outcomes
        ),
        failed=sum(outcome.status == FAILED for outcome in outcomes),
        blocked_gap=sum(
            outcome.status == BLOCKED_GAP for outcome in outcomes
        ),
        remaining_pending=pending,
        remaining_running=running,
        outcomes=tuple(outcomes),
    )


def requeue_rebuild_tasks(
    connection: sqlite3.Connection,
    *,
    statuses: Sequence[str] = (FAILED, BLOCKED_GAP),
    event_id: str | None = None,
    downstream_kind: str | None = None,
    downstream_id: int | None = None,
) -> int:
    """Explicitly retry terminal failures or resolved gaps."""

    if isinstance(statuses, (str, bytes)):
        raise TypeError("statuses must be a sequence of queue statuses")
    retry_statuses = tuple(dict.fromkeys(str(value) for value in statuses))
    if not retry_statuses:
        return 0
    unsupported = set(retry_statuses) - _RETRYABLE_TERMINAL_STATUSES
    if unsupported:
        raise ValueError(
            "only FAILED and BLOCKED_GAP tasks can be requeued: "
            + ", ".join(sorted(unsupported))
        )
    if (
        downstream_kind is not None
        and downstream_kind not in SUPPORTED_REBUILD_KINDS
    ):
        raise ValueError(
            f"unsupported rebuild kind: {downstream_kind}"
        )
    _require_clean_connection(connection)
    _validate_schema(connection)
    where = [
        "status IN ("
        + ", ".join("?" for _value in retry_statuses)
        + ")"
    ]
    parameters: list[object] = list(retry_statuses)
    if event_id is not None:
        where.append("event_id=?")
        parameters.append(event_id)
    if downstream_kind is not None:
        where.append("downstream_kind=?")
        parameters.append(downstream_kind)
    if downstream_id is not None:
        where.append("downstream_id=?")
        parameters.append(int(downstream_id))
    where_sql = " AND ".join(where)

    connection.execute("BEGIN IMMEDIATE")
    try:
        affected_events = {
            str(row[0])
            for row in connection.execute(
                f"""
                SELECT DISTINCT event_id
                FROM invalidation_queue
                WHERE {where_sql}
                """,
                parameters,
            )
        }
        cursor = connection.execute(
            f"""
            UPDATE invalidation_queue
            SET status=?
            WHERE {where_sql}
            """,
            (PENDING_REBUILD, *parameters),
        )
        for affected_event in sorted(affected_events):
            _refresh_event_status(connection, affected_event)
        connection.commit()
        return int(cursor.rowcount)
    except Exception:
        connection.rollback()
        raise


class RebuildQueueWorker:
    """Reusable object wrapper for update-command orchestration."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        backend: RebuildBackend,
    ) -> None:
        if not isinstance(backend, RebuildBackend):
            raise TypeError("backend must be a RebuildBackend")
        self.connection = connection
        self.backend = backend

    def drain(
        self,
        *,
        max_items: int = 100,
        recover_running: bool = True,
    ) -> RebuildDrainReport:
        return drain_rebuild_queue(
            self.connection,
            self.backend,
            max_items=max_items,
            recover_running=recover_running,
        )


RebuildWorker = RebuildQueueWorker


__all__ = [
    "BLOCKED_GAP",
    "CoreMaterializerRebuildBackend",
    "EXPECTED_REBUILD_WRITE_TABLES",
    "FAILED",
    "PENDING_REBUILD",
    "QUEUE_STATUSES",
    "REBUILD_KIND_ORDER",
    "RUNNING",
    "RebuildBackend",
    "RebuildBlockedGap",
    "RebuildDrainReport",
    "RebuildQueueWorker",
    "RebuildScope",
    "RebuildTargetState",
    "RebuildTask",
    "RebuildTaskOutcome",
    "RebuildVerificationError",
    "RebuildWorker",
    "RebuildWorkerError",
    "SUCCEEDED",
    "SUPPORTED_REBUILD_KINDS",
    "drain_rebuild_queue",
    "recover_running_rebuild_tasks",
    "requeue_rebuild_tasks",
]
