"""Fail-closed foundation for artifact-bound additive Blueprint deltas.

This module compares an immutable base Core database with a staged Core
database.  It does not run rebuilds, publish snapshots, or update pointers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from .blueprint_ingest import (
    BlueprintIngestResult,
    is_canonical_unreal_uri,
)
from .invalidation import (
    InvalidationBlockedGap,
    InvalidationPlan,
    plan_additive_asset_invalidation,
)
from .projections import DOMAIN_PROJECTIONS
from .query_planner import is_valid_generic_evidence_uri
from .rebuild_worker import (
    EXPECTED_REBUILD_WRITE_TABLES,
    SUCCEEDED,
    SUPPORTED_REBUILD_KINDS,
)
from .source_manifest import (
    SourceDiff,
    SourceRevision,
    canonical_source_diff_bytes,
)


PRODUCTION = "PRODUCTION"
TEST_ONLY = "TEST_ONLY"
BLOCKED_GAP = "BLOCKED_GAP"
FOUNDATION_VERIFIED = "FOUNDATION_VERIFIED"

_CONTEXTS = {PRODUCTION, TEST_ONLY}
_SHA = re.compile(r"^[0-9a-f]{64}$")
_WRITE_TABLES = {"source_revisions", "facts", "fact_evidence"}
_PROTECTED_TABLES = ("source_revisions", "facts", "fact_evidence")
_REQUIRED_ADDITIVE_PLAN_KINDS = {
    "FACT",
    "EFFECTIVE_ENTITY",
    "ROLE_ENTITY",
    "DOMAIN_ENTITY",
    "PROJECTION",
    "QUERY_SNAPSHOT",
}
_BINDING_FIELDS = {
    "sourceId",
    "sourceFingerprint",
    "artifactUri",
    "artifactSha256",
    "artifactBytes",
    "trustContext",
}
_RECEIPT_ARTIFACT_FIELDS = _BINDING_FIELDS | {
    "sourceAggregateSha256",
    "manifestArtifact",
    "sourceRevisionLabel",
    "coreSourceUri",
    "coreSourceFingerprint",
    "entityUri",
    "producerVersion",
    "schemaVersion",
    "generatedAt",
}
_MANIFEST_ARTIFACT_FIELDS = {
    "artifactUri",
    "artifactSha256",
    "artifactBytes",
}
_BACKEND_RECEIPT_FIELDS = {
    "schema",
    "eventId",
    "downstreamKind",
    "downstreamId",
    "dependencyReason",
    "status",
    "beforeDigest",
    "afterDigest",
    "complete",
    "gapCode",
    "detail",
    "touchedTables",
    "recovered",
    "cacheHit",
    "projectionBatch",
    "verification",
    "proof",
}
_BACKEND_EVENT_FIELDS = {
    "eventId",
    "eventKind",
    "eventStatus",
    "eventPayloadSha256",
    "queueSha256",
}
_RECEIPT_FIELDS = {
    "schema",
    "operation",
    "trustContext",
    "status",
    "sourceDiffSha256",
    "artifacts",
    "beforeDatabaseSha256",
    "afterDatabaseSha256",
    "protectedTableSha256",
    "receiptDatabaseSha256",
    "changedTables",
    "sourceRevisionIds",
    "entityIds",
    "factIds",
    "invalidationPlan",
    "backendEvent",
    "backendTerminalReceipts",
    "blockedGaps",
    "published",
    "e4Scenario2Complete",
    "proof",
}


class AddOnlyDeltaBlockedGap(ValueError):
    """Stable fail-closed result for an unsafe or unsupported delta."""

    status = BLOCKED_GAP

    def __init__(self, gap_code: str, message: str) -> None:
        super().__init__(message)
        self.gap_code = gap_code


@dataclass(frozen=True)
class LogicalDatabaseState:
    schema_sha256: str
    table_sha256: Mapping[str, str]
    table_row_counts: Mapping[str, int]
    database_sha256: str


@dataclass(frozen=True)
class AddOnlyBlueprintDelta:
    trust_context: str
    source_diff_sha256: str
    source_diff_json: bytes
    artifacts: tuple[Mapping[str, object], ...]
    before_database_sha256: str
    after_database_sha256: str
    protected_table_sha256: Mapping[str, str]
    changed_tables: tuple[str, ...]
    source_revision_ids: tuple[int, ...]
    entity_ids: tuple[int, ...]
    fact_ids: tuple[int, ...]


@dataclass(frozen=True)
class _FrozenFile:
    """One immutable byte snapshot used for every identity check."""

    content: bytes
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class BlueprintEvidenceBundleInspection:
    evidence_sha256: str
    evidence_bytes: int
    manifest_sha256: str | None
    manifest_bytes: int | None
    aggregate_sha256: str
    source_revision_label: str
    core_source_uri: str
    core_source_fingerprint: str
    entity_uri: str
    producer_version: str
    schema_version: str
    generated_at: str


def _gap(code: str, message: str) -> AddOnlyDeltaBlockedGap:
    return AddOnlyDeltaBlockedGap(code, message)


def _json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _gap("NON_CANONICAL_JSON", "non-finite JSON number")
        return value
    if isinstance(value, bytes):
        return {"$binaryBase64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _gap("NON_CANONICAL_JSON", "non-string JSON key")
        return {str(key): _json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(child) for child in value]
    raise _gap("NON_CANONICAL_JSON", "non-JSON value")


def _canonical(value: object) -> bytes:
    return json.dumps(
        _json(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _deep_freeze_json(value: object) -> object:
    """Copy JSON-compatible data into recursively immutable containers."""

    normalized = _json(value)

    def freeze(child: object) -> object:
        if isinstance(child, Mapping):
            return MappingProxyType(
                {key: freeze(value) for key, value in child.items()}
            )
        if isinstance(child, (list, tuple)):
            return tuple(freeze(value) for value in child)
        return child

    return freeze(normalized)


def _frozen_mapping(
    value: Mapping[str, object],
) -> Mapping[str, object]:
    frozen = _deep_freeze_json(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - invariant
        raise _gap("NON_CANONICAL_JSON", "expected a JSON object")
    return frozen


def _strict_json(value: str) -> object:
    def object_pairs(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, child in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = child
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"non-standard JSON constant: {value}")

    return json.loads(
        value,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(mark in value for mark in ("\x00", "\r", "\n"))
    ):
        raise _gap("DELTA_CONTRACT_INVALID", f"{field} is invalid")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise _gap("DELTA_CONTRACT_INVALID", f"{field} is not SHA-256")
    return value


def _context(value: object) -> str:
    result = _text(value, "trustContext")
    if result not in _CONTEXTS:
        raise _gap("DELTA_CONTRACT_INVALID", "unknown trustContext")
    return result


def _is_integer(value: object) -> bool:
    """Reject bool, float, and integer-like wrappers at trust boundaries."""

    return type(value) is int


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _schema_objects(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (
            str(object_type),
            str(name),
            str(table_name),
            str(sql or ""),
        )
        for object_type, name, table_name, sql in connection.execute(
            """
            SELECT type, name, tbl_name, sql FROM sqlite_schema
            WHERE type IN ('table', 'index', 'trigger', 'view')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name, tbl_name
            """
        )
    )


def _rows(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(f"SELECT * FROM {_quote(table)}")
    )


@contextmanager
def _database_snapshot(
    connection: sqlite3.Connection,
    *,
    immediate: bool,
    require_new: bool = False,
) -> Iterator[None]:
    """Hold one SQLite snapshot; IMMEDIATE is the cross-process writer lock."""

    if connection.in_transaction:
        if require_new:
            raise _gap(
                "DELTA_DATABASE_TRANSACTION_ACTIVE",
                "receipt construction requires a fresh locked transaction",
            )
        yield
        return
    try:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    except sqlite3.DatabaseError as error:
        raise _gap(
            "DELTA_DATABASE_LOCK_UNAVAILABLE",
            "could not acquire the stable SQLite snapshot lock",
        ) from error
    try:
        yield
    finally:
        if connection.in_transaction:
            connection.rollback()


def _logical_database_state(
    connection: sqlite3.Connection,
) -> LogicalDatabaseState:
    schema_objects = _schema_objects(connection)
    tables = tuple(
        name
        for object_type, name, _table_name, _sql in schema_objects
        if object_type == "table"
    )
    table_sha: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    for table in tables:
        encoded = sorted(_canonical(row) for row in _rows(connection, table))
        digest = hashlib.sha256()
        for row in encoded:
            digest.update(row + b"\n")
        table_sha[table] = digest.hexdigest()
        row_counts[table] = len(encoded)
    schema_sha = _digest(schema_objects)
    database_sha = _digest(
        {
            "schemaSha256": schema_sha,
            "tableSha256": table_sha,
            "tableRowCounts": row_counts,
        }
    )
    return LogicalDatabaseState(
        schema_sha256=schema_sha,
        table_sha256=MappingProxyType(table_sha),
        table_row_counts=MappingProxyType(row_counts),
        database_sha256=database_sha,
    )


def logical_database_state(
    connection: sqlite3.Connection,
) -> LogicalDatabaseState:
    """Return one transactionally stable digest of schema and user rows."""

    with _database_snapshot(connection, immediate=False):
        return _logical_database_state(connection)


def _protected_table_sha256(
    state: LogicalDatabaseState,
) -> dict[str, str]:
    missing = set(_PROTECTED_TABLES) - set(state.table_sha256)
    if missing:
        raise _gap(
            "DELTA_TRUTH_TABLE_MISSING",
            "protected truth table is missing: "
            + ", ".join(sorted(missing)),
        )
    return {
        table: state.table_sha256[table]
        for table in _PROTECTED_TABLES
    }


def _blueprint_additions(diff: SourceDiff) -> tuple[SourceRevision, ...]:
    if diff.deleted:
        raise _gap(
            "SOURCE_DIFF_NOT_ADD_ONLY_BLUEPRINT",
            "delete cannot enter the add-only contract",
        )
    for change in diff.changed:
        old, new = change.previous, change.current
        if not (
            change.change_kind == "CHANGED"
            and old is not None
            and new is not None
            and old.source_kind == new.source_kind == "SEMANTIC_INPUT"
            and old.source_uri
            == new.source_uri
            == "semantic-input://captures"
            and old.source_id == new.source_id
        ):
            raise _gap(
                "SOURCE_DIFF_NOT_ADD_ONLY_BLUEPRINT",
                "only the captures aggregate may change",
            )
    if len(diff.added) != 1:
        raise _gap(
            "SOURCE_DIFF_REQUIRES_SINGLE_BLUEPRINT",
            "exactly one added Blueprint Evidence revision is required",
        )
    revisions = []
    for change in diff.added:
        current = change.current
        if not (
            change.change_kind == "ADDED"
            and change.previous is None
            and current is not None
            and current.source_kind == "BLUEPRINT_EVIDENCE"
            and current.source_id == change.source_id
            and current.entity_uri
        ):
            raise _gap(
                "SOURCE_DIFF_NOT_ADD_ONLY_BLUEPRINT",
                "source diff is not add-only Blueprint Evidence",
            )
        revisions.append(current)
    return tuple(revisions)


def _artifact(root: Path, raw_uri: object) -> tuple[str, Path]:
    uri = _text(raw_uri, "artifactUri")
    relative_text = uri[11:] if uri.startswith("artifact://") else ""
    if any(mark in relative_text for mark in ("\\", "%", "?", "#", "\x00")):
        relative_text = ""
    relative = PurePosixPath(relative_text)
    if (
        not relative_text
        or relative.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in relative.parts)
    ):
        raise _gap(
            "ARTIFACT_URI_UNSAFE",
            "artifactUri must be a safe relative artifact URI",
        )
    try:
        resolved_root = root.resolve(strict=True)
        path = resolved_root.joinpath(*relative.parts).resolve(strict=True)
        path.relative_to(resolved_root)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise _gap(
            "ARTIFACT_MISSING",
            "artifact is missing or outside the authorized root",
        ) from error
    if not path.is_file():
        raise _gap("ARTIFACT_MISSING", "artifact is not a file")
    return uri, path


def _freeze_file(path: Path) -> _FrozenFile:
    """Read once; all later checks operate on this immutable copy."""

    with path.open("rb") as stream:
        content = bytes(stream.read())
    return _FrozenFile(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def _evidence_identity(content: bytes) -> dict[str, str]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        connection.deserialize(content)
        connection.execute("PRAGMA query_only=ON")
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(asset_revisions)"
            )
        }
        required = {
            "revision_id",
            "asset_id",
            "object_path",
            "source_fingerprint",
            "parser_version",
            "schema_version",
            "generated_at",
        }
        if not required.issubset(columns):
            raise _gap(
                "ARTIFACT_IDENTITY_INVALID",
                "Evidence revision identity is incomplete",
            )
        rows = list(
            connection.execute(
                """
                SELECT revision_id, asset_id, object_path,
                       source_fingerprint, parser_version,
                       schema_version, generated_at
                FROM asset_revisions LIMIT 2
                """
            )
        )
    except sqlite3.DatabaseError as error:
        raise _gap(
            "ARTIFACT_IDENTITY_INVALID",
            "artifact is not readable Evidence SQLite",
        ) from error
    finally:
        if connection is not None:
            connection.close()
    if len(rows) != 1:
        raise _gap(
            "ARTIFACT_IDENTITY_INVALID",
            "Evidence artifact must contain one revision",
        )
    revision, asset, entity, fingerprint, producer, schema, generated = (
        _text(value, "artifact identity") for value in rows[0]
    )
    return {
        "sourceRevisionLabel": revision,
        "coreSourceUri": f"bp://{asset}@{revision}",
        "coreSourceFingerprint": _sha(fingerprint, "source_fingerprint"),
        "entityUri": entity,
        "producerVersion": producer,
        "schemaVersion": schema,
        "generatedAt": generated,
    }


def _source_aggregate_sha256(
    evidence: bytes,
    manifest: bytes | None,
) -> str:
    """Reproduce ``source_manifest._blueprint_revision`` from frozen bytes."""

    digest = hashlib.sha256()
    digest.update(b"evidence.sqlite\0")
    digest.update(evidence)
    digest.update(b"\n")
    if manifest is not None:
        digest.update(b"manifest.json\0")
        digest.update(manifest)
        digest.update(b"\n")
    return digest.hexdigest()


def inspect_blueprint_evidence_bundle(
    evidence: bytes,
    manifest: bytes | None,
) -> BlueprintEvidenceBundleInspection:
    """Parse one frozen Evidence bundle through the delta identity contract."""

    if type(evidence) is not bytes or not evidence:
        raise _gap(
            "ARTIFACT_IDENTITY_INVALID",
            "Evidence SQLite bytes are required",
        )
    if manifest is not None and (
        type(manifest) is not bytes or not manifest
    ):
        raise _gap(
            "ARTIFACT_IDENTITY_INVALID",
            "adjacent Evidence manifest bytes are invalid",
        )
    identity = _evidence_identity(evidence)
    entity_uri = identity["entityUri"]
    if not is_canonical_unreal_uri(entity_uri):
        raise _gap(
            "ARTIFACT_IDENTITY_INVALID",
            "Evidence object URI is not canonical",
        )
    return BlueprintEvidenceBundleInspection(
        evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        evidence_bytes=len(evidence),
        manifest_sha256=(
            hashlib.sha256(manifest).hexdigest()
            if manifest is not None
            else None
        ),
        manifest_bytes=(
            len(manifest) if manifest is not None else None
        ),
        aggregate_sha256=_source_aggregate_sha256(
            evidence,
            manifest,
        ),
        source_revision_label=identity["sourceRevisionLabel"],
        core_source_uri=identity["coreSourceUri"],
        core_source_fingerprint=identity["coreSourceFingerprint"],
        entity_uri=entity_uri,
        producer_version=identity["producerVersion"],
        schema_version=identity["schemaVersion"],
        generated_at=identity["generatedAt"],
    )


def _adjacent_manifest(
    root: Path,
    evidence_path: Path,
) -> tuple[str, _FrozenFile] | None:
    candidate = evidence_path.parent / "manifest.json"
    if not candidate.exists():
        return None
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(resolved_root)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise _gap(
            "ARTIFACT_MISSING",
            "adjacent Evidence manifest is outside the authorized root",
        ) from error
    if not resolved.is_file():
        raise _gap(
            "ARTIFACT_MISSING",
            "adjacent Evidence manifest is not a file",
        )
    return (
        "artifact://" + PurePosixPath(*relative.parts).as_posix(),
        _freeze_file(resolved),
    )


def _bindings(
    revisions: Sequence[SourceRevision],
    root: Path,
    raw_bindings: Iterable[Mapping[str, object]],
    context: str,
) -> tuple[dict[str, object], ...]:
    by_id = {revision.source_id: revision for revision in revisions}
    bindings = tuple(raw_bindings)
    if len(bindings) != len(by_id):
        raise _gap(
            "ARTIFACT_BINDING_SET_MISMATCH",
            "artifact binding count differs from source additions",
        )
    normalized = []
    seen: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, Mapping) or set(binding) != _BINDING_FIELDS:
            raise _gap(
                "ARTIFACT_BINDING_INVALID",
                "artifact binding fields are invalid",
            )
        source_id = _sha(binding.get("sourceId"), "sourceId")
        revision = by_id.get(source_id)
        if revision is None or source_id in seen:
            raise _gap(
                "ARTIFACT_BINDING_SET_MISMATCH",
                "artifact binding source does not match source diff",
            )
        fingerprint = _sha(
            binding.get("sourceFingerprint"),
            "sourceFingerprint",
        )
        if fingerprint != revision.fingerprint:
            raise _gap(
                "ARTIFACT_BINDING_SET_MISMATCH",
                "artifact fingerprint does not match source diff",
            )
        binding_context = _context(binding.get("trustContext"))
        if binding_context != context:
            if binding_context == TEST_ONLY and context == PRODUCTION:
                raise _gap(
                    "TEST_ONLY_ARTIFACT_IN_PRODUCTION",
                    "TEST_ONLY artifact is not valid in PRODUCTION",
                )
            raise _gap(
                "ARTIFACT_TRUST_CONTEXT_MISMATCH",
                "artifact trust context differs from validator context",
            )
        uri, path = _artifact(root, binding.get("artifactUri"))
        evidence = _freeze_file(path)
        if evidence.sha256 != _sha(
            binding.get("artifactSha256"),
            "artifactSha256",
        ):
            raise _gap(
                "ARTIFACT_SHA256_MISMATCH",
                "artifact SHA-256 does not match bytes",
            )
        size = binding.get("artifactBytes")
        if not _is_integer(size) or size < 1:
            raise _gap("ARTIFACT_BINDING_INVALID", "artifactBytes is invalid")
        if size != evidence.size_bytes:
            raise _gap(
                "ARTIFACT_SIZE_MISMATCH",
                "artifactBytes does not match bytes",
            )
        if revision.size_bytes != evidence.size_bytes:
            raise _gap(
                "SOURCE_DIFF_SIZE_MISMATCH",
                "source diff size differs from frozen Evidence bytes",
            )
        manifest = _adjacent_manifest(root, path)
        inspection = inspect_blueprint_evidence_bundle(
            evidence.content,
            manifest[1].content if manifest is not None else None,
        )
        aggregate = inspection.aggregate_sha256
        if aggregate != revision.fingerprint:
            raise _gap(
                "SOURCE_DIFF_AGGREGATE_MISMATCH",
                "source diff fingerprint differs from frozen artifact bundle",
            )
        identity = {
            "sourceRevisionLabel": inspection.source_revision_label,
            "coreSourceUri": inspection.core_source_uri,
            "coreSourceFingerprint": (
                inspection.core_source_fingerprint
            ),
            "entityUri": inspection.entity_uri,
            "producerVersion": inspection.producer_version,
            "schemaVersion": inspection.schema_version,
            "generatedAt": inspection.generated_at,
        }
        if identity["sourceRevisionLabel"] != revision.revision_label:
            raise _gap(
                "SOURCE_DIFF_REVISION_LABEL_MISMATCH",
                "source diff revisionLabel differs from Evidence bytes",
            )
        if identity["entityUri"] != revision.entity_uri:
            raise _gap(
                "ARTIFACT_IDENTITY_MISMATCH",
                "artifact entity differs from source diff",
            )
        normalized.append(
            {
                **dict(binding),
                "artifactUri": uri,
                "sourceAggregateSha256": aggregate,
                "manifestArtifact": (
                    {
                        "artifactUri": manifest[0],
                        "artifactSha256": manifest[1].sha256,
                        "artifactBytes": manifest[1].size_bytes,
                    }
                    if manifest is not None
                    else None
                ),
                **identity,
            }
        )
        seen.add(source_id)
    if seen != set(by_id):
        raise _gap(
            "ARTIFACT_BINDING_SET_MISMATCH",
            "artifact binding set differs from source diff",
        )
    return tuple(sorted(normalized, key=lambda item: str(item["sourceId"])))


def _source_revision_ids(
    base: sqlite3.Connection,
    staged: sqlite3.Connection,
    artifacts: Sequence[Mapping[str, object]],
) -> tuple[int, ...]:
    result = []
    for artifact in artifacts:
        key = (
            artifact["coreSourceUri"],
            artifact["coreSourceFingerprint"],
        )
        base_rows = list(
            base.execute(
                """
                SELECT revision_id FROM source_revisions
                WHERE source_kind='blueprint_evidence'
                  AND source_uri=? AND source_fingerprint=?
                """,
                key,
            )
        )
        rows = list(
            staged.execute(
                """
                SELECT revision_id, producer_version, schema_version,
                       generated_at, freshness_status
                FROM source_revisions
                WHERE source_kind='blueprint_evidence'
                  AND source_uri=? AND source_fingerprint=?
                """,
                key,
            )
        )
        if base_rows or len(rows) != 1:
            raise _gap(
                "CORE_SOURCE_REVISION_NOT_ADDED",
                "artifact does not map to one new Core revision",
            )
        row = rows[0]
        expected = (
            artifact["producerVersion"],
            artifact["schemaVersion"],
            artifact["generatedAt"],
        )
        if tuple(str(value) for value in row[1:4]) != expected:
            raise _gap(
                "CORE_SOURCE_REVISION_IDENTITY_MISMATCH",
                "Core revision differs from Evidence identity",
            )
        if str(row[4]).upper() != "FRESH":
            raise _gap(
                "ADDITIVE_ASSET_INGEST_NOT_FRESH",
                "new Core source revision is not FRESH",
            )
        if not _is_integer(row[0]) or row[0] < 1:
            raise _gap(
                "CORE_SOURCE_REVISION_IDENTITY_MISMATCH",
                "Core revision ID is not a positive integer",
            )
        result.append(row[0])
    if len(set(result)) != len(result):
        raise _gap(
            "CORE_SOURCE_REVISION_IDENTITY_MISMATCH",
            "artifacts share one Core revision",
        )
    return tuple(sorted(result))


def _scoped_ids(
    staged: sqlite3.Connection,
    revisions: Sequence[SourceRevision],
    ingest: BlueprintIngestResult,
    source_revision_ids: Sequence[int],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    expected_uris = {revision.entity_uri for revision in revisions}
    placeholders = ",".join("?" for _ in expected_uris)
    resolved: dict[str, int] = {}
    for entity_id, uri in staged.execute(
        f"""
        SELECT entity_id, canonical_uri FROM entities
        WHERE canonical_uri IN ({placeholders})
        """,
        tuple(sorted(expected_uris)),
    ):
        if (
            not _is_integer(entity_id)
            or entity_id < 1
            or type(uri) is not str
        ):
            raise _gap(
                "ADDITIVE_ASSET_ENTITY_SCOPE_INVALID",
                "durable entity scope contains non-canonical IDs",
            )
        resolved[uri] = entity_id
    entity_ids = tuple(sorted(resolved.values()))
    if any(
        not _is_integer(value) or value < 1
        for value in ingest.entity_ids
    ):
        raise _gap(
            "ADDITIVE_ASSET_ENTITY_SCOPE_INVALID",
            "ingest entity scope contains non-integer IDs",
        )
    if set(resolved) != expected_uris or set(entity_ids) != set(
        ingest.entity_ids
    ):
        raise _gap(
            "ADDITIVE_ASSET_ENTITY_SCOPE_INVALID",
            "ingest entity scope differs from source diff",
        )
    if any(not _is_integer(value) or value < 1 for value in ingest.fact_ids):
        raise _gap(
            "ADDITIVE_ASSET_FACT_SCOPE_INVALID",
            "ingest fact scope contains a non-integer ID",
        )
    fact_ids = tuple(sorted(ingest.fact_ids))
    if not fact_ids:
        raise _gap(
            "ADDITIVE_ASSET_FACT_EVIDENCE_MISSING",
            "ingest produced no fact evidence scope",
        )
    fact_marks = ",".join("?" for _ in fact_ids)
    facts = list(
        staged.execute(
            f"""
            SELECT fact_id, subject_entity_id, fact_type, scope_kind,
                   status, confidence, current
            FROM facts WHERE fact_id IN ({fact_marks})
            """,
            fact_ids,
        )
    )
    if (
        any(
            not _is_integer(row[0])
            or not _is_integer(row[1])
            or not _is_integer(row[6])
            for row in facts
        )
        or {row[0] for row in facts} != set(fact_ids)
        or any(
            row[1] not in set(entity_ids)
            or str(row[2]).upper() != "DECLARED_DEFAULT"
            or str(row[3]).upper() != "DECLARED"
            for row in facts
        )
    ):
        raise _gap(
            "ADDITIVE_ASSET_FACT_SCOPE_INVALID",
            "ingest fact scope is missing or unrelated",
        )
    if any(
        str(row[4]).upper() != "CONFIRMED"
        or str(row[5]).upper() != "HIGH"
        or row[6] != 1
        for row in facts
    ):
        raise _gap(
            "ADDITIVE_ASSET_FACT_QUALITY_INVALID",
            "new facts must be current CONFIRMED/HIGH facts",
        )
    revision_marks = ",".join("?" for _ in source_revision_ids)
    evidence_rows = list(
        staged.execute(
            f"""
            SELECT evidence.fact_id, evidence.source_revision_id,
                   evidence.evidence_uri, evidence.evidence_role,
                   revision.source_uri
            FROM fact_evidence AS evidence
            JOIN source_revisions AS revision
              ON revision.revision_id=evidence.source_revision_id
            WHERE evidence.fact_id IN ({fact_marks})
              AND evidence.source_revision_id IN ({revision_marks})
            ORDER BY evidence.fact_id, evidence.source_revision_id,
                     evidence.evidence_uri, evidence.evidence_role
            """,
            (*fact_ids, *source_revision_ids),
        )
    )
    if any(
        not _is_integer(row[0]) or not _is_integer(row[1])
        for row in evidence_rows
    ):
        raise _gap(
            "ADDITIVE_ASSET_FACT_EVIDENCE_INVALID",
            "fact evidence contains non-canonical IDs",
        )
    evidence_facts = {row[0] for row in evidence_rows}
    if evidence_facts != set(fact_ids):
        raise _gap(
            "ADDITIVE_ASSET_FACT_EVIDENCE_MISSING",
            "each fact must bind the added Blueprint revision",
        )
    if any(
        str(row[3]).upper() != "DEFAULT_VALUE_ACTUAL"
        or not is_valid_generic_evidence_uri(row[2])
        or not str(row[2]).startswith(str(row[4]) + "/")
        for row in evidence_rows
    ):
        raise _gap(
            "ADDITIVE_ASSET_FACT_EVIDENCE_INVALID",
            "fact evidence must use the bound Blueprint URI and actual role",
        )
    return entity_ids, fact_ids


def _changed_tables(
    before: LogicalDatabaseState,
    after: LogicalDatabaseState,
) -> tuple[str, ...]:
    if (
        before.schema_sha256 != after.schema_sha256
        or set(before.table_sha256) != set(after.table_sha256)
    ):
        raise _gap("CORE_SCHEMA_CHANGED", "base/staged schemas differ")
    changed = tuple(
        table
        for table in sorted(before.table_sha256)
        if before.table_sha256[table] != after.table_sha256[table]
    )
    if "fact_evidence" not in changed:
        raise _gap(
            "ADDITIVE_ASSET_FACT_EVIDENCE_MISSING",
            "actual durable delta contains no fact evidence",
        )
    if "source_revisions" not in changed or not set(changed).issubset(
        _WRITE_TABLES
    ):
        raise _gap(
            "ADDITIVE_ASSET_WRITE_SCOPE_UNSUPPORTED",
            "actual durable write scope exceeds source/fact/evidence",
        )
    return changed


def _durable_truth_changed_tables(
    before: LogicalDatabaseState,
    after: LogicalDatabaseState,
) -> tuple[str, ...]:
    """Identify the exact truth delta after derived rebuild writes."""

    if (
        before.schema_sha256 != after.schema_sha256
        or set(before.table_sha256) != set(after.table_sha256)
    ):
        raise _gap("CORE_SCHEMA_CHANGED", "base/staged schemas differ")
    changed = tuple(
        table
        for table in sorted(_WRITE_TABLES)
        if before.table_sha256[table] != after.table_sha256[table]
    )
    if set(changed) != _WRITE_TABLES:
        raise _gap(
            "ADDITIVE_ASSET_WRITE_SCOPE_UNSUPPORTED",
            "post-rebuild Core does not retain the exact additive truth delta",
        )
    return changed


def _verify_row_delta(
    base: sqlite3.Connection,
    staged: sqlite3.Connection,
    source_ids: Sequence[int],
    fact_ids: Sequence[int],
) -> None:
    source_set = set(source_ids)
    fact_set = set(fact_ids)
    for table, allowed in (
        ("source_revisions", source_set),
        ("facts", fact_set),
    ):
        old, new = set(_rows(base, table)), set(_rows(staged, table))
        added_rows = new - old
        if (
            old - new
            or any(
                not row or not _is_integer(row[0]) for row in added_rows
            )
            or {row[0] for row in added_rows} - allowed
        ):
            raise _gap(
                "ADDITIVE_ASSET_MUTATION_NOT_ALLOWED",
                f"{table} delta exceeds add-only scope",
            )
    old_evidence = set(_rows(base, "fact_evidence"))
    new_evidence = set(_rows(staged, "fact_evidence"))
    added = new_evidence - old_evidence
    if (
        old_evidence - new_evidence
        or not added
        or any(
            len(row) < 2
            or not _is_integer(row[0])
            or not _is_integer(row[1])
            or row[0] not in fact_set
            or row[1] not in source_set
            for row in added
        )
        or {row[0] for row in added} != fact_set
    ):
        raise _gap(
            "ADDITIVE_ASSET_FACT_EVIDENCE_MISSING",
            "durable fact evidence is missing or outside scope",
        )


def build_add_only_blueprint_delta(
    base: sqlite3.Connection,
    staged: sqlite3.Connection,
    *,
    source_diff: SourceDiff,
    ingest_result: BlueprintIngestResult,
    artifact_root: Path,
    artifact_bindings: Iterable[Mapping[str, object]],
    trust_context: str = PRODUCTION,
    durable_derived_state: bool = False,
) -> AddOnlyBlueprintDelta:
    """Verify a staged add-only ingest without applying or publishing it."""

    if type(durable_derived_state) is not bool:
        raise TypeError("durable_derived_state must be a boolean")
    context = _context(trust_context)
    if context != TEST_ONLY:
        raise _gap(
            "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED",
            "PRODUCTION additive Evidence requires an independent signed "
            "authorization contract that is not available in this foundation",
        )
    revisions = _blueprint_additions(source_diff)
    if ingest_result.freshness_gap_assets or ingest_result.untrusted_assets:
        raise _gap(
            "ADDITIVE_ASSET_INGEST_NOT_FRESH",
            "ingest result contains freshness or trust gaps",
        )
    artifacts = _bindings(
        revisions,
        artifact_root,
        artifact_bindings,
        context,
    )
    with _database_snapshot(base, immediate=False, require_new=True):
        with _database_snapshot(staged, immediate=True, require_new=True):
            before = logical_database_state(base)
            after = logical_database_state(staged)
            changed = (
                _durable_truth_changed_tables(before, after)
                if durable_derived_state
                else _changed_tables(before, after)
            )
            source_ids = _source_revision_ids(base, staged, artifacts)
            entity_ids, fact_ids = _scoped_ids(
                staged,
                revisions,
                ingest_result,
                source_ids,
            )
            _verify_row_delta(base, staged, source_ids, fact_ids)
            final_before = logical_database_state(base)
            final_after = logical_database_state(staged)
            if (
                final_before.database_sha256 != before.database_sha256
                or final_after.database_sha256 != after.database_sha256
            ):
                raise _gap(
                    "DELTA_DATABASE_SNAPSHOT_DRIFT",
                    "base or staged durable state changed during validation",
                )
            source_json = canonical_source_diff_bytes(source_diff)
            return AddOnlyBlueprintDelta(
                trust_context=context,
                source_diff_sha256=hashlib.sha256(source_json).hexdigest(),
                source_diff_json=source_json,
                artifacts=tuple(
                    _frozen_mapping(binding) for binding in artifacts
                ),
                before_database_sha256=before.database_sha256,
                after_database_sha256=after.database_sha256,
                protected_table_sha256=MappingProxyType(
                    _protected_table_sha256(after)
                ),
                changed_tables=changed,
                source_revision_ids=source_ids,
                entity_ids=entity_ids,
                fact_ids=fact_ids,
            )


def _plan_tasks(
    plan: InvalidationPlan,
) -> dict[tuple[str, int], str]:
    if (
        plan.event_kind != "ASSET"
        or plan.upstream_revision_id is not None
        or set(plan.downstream) != set(plan.reasons)
        or not {"FACT", "EFFECTIVE_ENTITY"}.issubset(plan.downstream)
        or not set(plan.downstream).issubset(SUPPORTED_REBUILD_KINDS)
    ):
        raise _gap(
            "DELTA_INVALIDATION_PLAN_MISMATCH",
            "invalidation plan is not a supported additive ASSET plan",
        )
    tasks: dict[tuple[str, int], str] = {}
    for kind, raw_values in plan.downstream.items():
        reason = _text(plan.reasons.get(kind), "dependency reason")
        if any(not _is_integer(value) for value in raw_values):
            raise _gap(
                "DELTA_INVALIDATION_PLAN_MISMATCH",
                "invalidation target id is not an integer",
            )
        values = tuple(sorted(raw_values))
        if not values or len(values) != len(set(values)):
            raise _gap(
                "DELTA_INVALIDATION_PLAN_MISMATCH",
                "invalidation task scope is empty or duplicated",
            )
        for value in values:
            if value < 1:
                raise _gap(
                    "DELTA_INVALIDATION_PLAN_MISMATCH",
                    "invalidation target id is invalid",
                )
            tasks[(kind, value)] = reason
    return tasks


def _validate_additive_plan_scope(
    plan: InvalidationPlan,
    *,
    fact_ids: Sequence[int],
    entity_ids: Sequence[int],
    source_revision_ids: Sequence[int],
) -> dict[tuple[str, int], str]:
    tasks = _plan_tasks(plan)
    if not _REQUIRED_ADDITIVE_PLAN_KINDS.issubset(plan.downstream):
        raise _gap(
            "DELTA_INVALIDATION_PLAN_MISMATCH",
            "additive plan omits a required derived dependency family",
        )
    exact_scopes = {
        "FACT": tuple(sorted(fact_ids)),
        "ROLE_ENTITY": tuple(sorted(entity_ids)),
        "DOMAIN_ENTITY": tuple(sorted(entity_ids)),
        "PROJECTION": tuple(range(1, len(DOMAIN_PROJECTIONS) + 1)),
        "QUERY_SNAPSHOT": tuple(sorted(source_revision_ids)),
    }
    if any(
        tuple(sorted(plan.downstream.get(kind, ()))) != expected
        for kind, expected in exact_scopes.items()
    ) or not set(entity_ids).issubset(
        plan.downstream.get("EFFECTIVE_ENTITY", ())
    ):
        raise _gap(
            "DELTA_INVALIDATION_PLAN_MISMATCH",
            "additive plan scope is not bound to the verified delta",
        )
    return tasks


def _validate_backend_row_scope(
    value: object,
    *,
    kind: str,
    target_id: int,
    event_id: str,
) -> None:
    if not isinstance(value, Mapping):
        raise _gap(
            "BACKEND_TERMINAL_OUTCOME_UNPROVEN",
            "backend receipt has no row-scope proof",
        )
    mode = value.get("mode")
    scoped_target = value.get("targetId")
    target_matches = (
        _is_integer(scoped_target) and scoped_target == target_id
    )
    if kind == "QUERY_SNAPSHOT":
        valid = (
            set(value) == {"mode", "eventId", "targetId", "tables"}
            and mode == "EXPLICIT_WHOLE_CACHE_BATCH"
            and value.get("eventId") == event_id
            and target_matches
            and value.get("tables")
            == sorted(EXPECTED_REBUILD_WRITE_TABLES[kind])
        )
    elif kind == "PROJECTION":
        valid = (
            set(value)
            == {"mode", "eventId", "targetId", "projectionNames"}
            and mode == "EXPLICIT_PROJECTION_BATCH"
            and value.get("eventId") == event_id
            and target_matches
            and value.get("projectionNames") == list(DOMAIN_PROJECTIONS)
        )
    elif kind == "CLASS_CLOSURE":
        class_ids = value.get("classIds")
        valid = (
            set(value) == {"mode", "rootClassId", "classIds"}
            and mode == "AFFECTED_CLASS_IDS"
            and _is_integer(value.get("rootClassId"))
            and value.get("rootClassId") == target_id
            and isinstance(class_ids, list)
            and bool(class_ids)
            and all(_is_integer(item) and item > 0 for item in class_ids)
            and class_ids == sorted(set(class_ids))
            and target_id in class_ids
        )
    elif kind == "REGISTRATION_ENTITY":
        valid = (
            set(value) == {"mode", "targetId", "entityUri"}
            and mode == "ENTITY_URI"
            and target_matches
            and isinstance(value.get("entityUri"), str)
            and bool(value.get("entityUri"))
        )
    elif kind == "NATIVE_FUNCTION":
        valid = (
            set(value)
            == {
                "mode",
                "targetId",
                "canonicalUri",
                "qualifiedSymbol",
                "rva",
            }
            and mode == "NATIVE_FUNCTION_IDENTITY"
            and target_matches
            and all(
                isinstance(value.get(field), str)
                and bool(value.get(field))
                for field in ("canonicalUri", "qualifiedSymbol", "rva")
            )
        )
    else:
        valid = (
            set(value) == {"mode", "targetId"}
            and mode == "TASK_TARGET_ID"
            and target_matches
        )
    if not valid:
        raise _gap(
            "BACKEND_TERMINAL_OUTCOME_UNPROVEN",
            "backend row-scope proof does not match the exact task",
        )


def _terminal_receipt(
    raw: Mapping[str, object],
    *,
    expected_kind: str,
    expected_id: int,
    expected_reason: str,
    expected_event_id: str | None,
) -> tuple[dict[str, object], str]:
    if not isinstance(raw, Mapping) or set(raw) != _BACKEND_RECEIPT_FIELDS:
        raise _gap(
            "BACKEND_TERMINAL_RECEIPT_INVALID",
            "backend terminal receipt fields are invalid",
        )
    normalized = dict(raw)
    body = dict(normalized)
    proof = body.pop("proof", None)
    if proof != "rebuild-proof://" + _digest(body):
        raise _gap(
            "BACKEND_TERMINAL_RECEIPT_INVALID",
            "backend terminal receipt proof does not match",
        )
    event_id = _text(normalized.get("eventId"), "backend eventId")
    kind = _text(
        normalized.get("downstreamKind"),
        "backend downstreamKind",
    )
    target = normalized.get("downstreamId")
    reason = _text(
        normalized.get("dependencyReason"),
        "backend dependencyReason",
    )
    if (
        kind != expected_kind
        or not _is_integer(target)
        or target != expected_id
        or reason != expected_reason
        or expected_event_id is not None
        and event_id != expected_event_id
    ):
        raise _gap(
            "BACKEND_TERMINAL_RECEIPT_REPLAY",
            "backend receipt is not bound to the exact event task",
        )
    if normalized.get("schema") != "ark-kb-rebuild-receipt/v1":
        raise _gap(
            "BACKEND_TERMINAL_RECEIPT_INVALID",
            "backend terminal receipt schema is invalid",
        )
    before = _sha(normalized.get("beforeDigest"), "beforeDigest")
    after = _sha(normalized.get("afterDigest"), "afterDigest")
    status = normalized.get("status")
    complete = normalized.get("complete")
    recovered = normalized.get("recovered")
    cache_hit = normalized.get("cacheHit")
    touched = normalized.get("touchedTables")
    projection_batch = normalized.get("projectionBatch")
    verification = normalized.get("verification")
    if (
        not isinstance(complete, bool)
        or not isinstance(recovered, bool)
        or not isinstance(cache_hit, bool)
        or not isinstance(touched, list)
        or any(not isinstance(value, str) or not value for value in touched)
        or len(touched) != len(set(touched))
        or not isinstance(projection_batch, Mapping)
        or not isinstance(verification, Mapping)
        or not isinstance(normalized.get("gapCode"), str)
        or not isinstance(normalized.get("detail"), str)
    ):
        raise _gap(
            "BACKEND_TERMINAL_RECEIPT_INVALID",
            "backend terminal outcome fields are invalid",
        )
    if status == SUCCEEDED:
        expected_tables = EXPECTED_REBUILD_WRITE_TABLES[kind]
        operations = verification.get("writeOperations")
        operation_tables: set[str] = set()
        operations_valid = isinstance(operations, list) and bool(operations)
        if operations_valid:
            for operation in operations:
                if (
                    not isinstance(operation, str)
                    or operation != operation.strip()
                    or operation.count(":") != 1
                ):
                    operations_valid = False
                    break
                table, operation_kind = operation.split(":", 1)
                if (
                    not table
                    or operation_kind not in {"INSERT", "UPDATE", "DELETE"}
                ):
                    operations_valid = False
                    break
                operation_tables.add(table)
        touched_tables = set(touched)
        _validate_backend_row_scope(
            verification.get("rowScope"),
            kind=kind,
            target_id=expected_id,
            event_id=event_id,
        )
        if (
            complete is not True
            or before == after
            or cache_hit is not False
            or not touched_tables
            or not touched_tables.issubset(expected_tables)
            or verification.get("basis") != "TARGET_STATE_CHANGED"
            or not operations_valid
            or not operation_tables
            or not operation_tables.issubset(expected_tables)
            or operation_tables != touched_tables
            or normalized.get("gapCode") != ""
        ):
            raise _gap(
                "BACKEND_TERMINAL_OUTCOME_UNPROVEN",
                "backend receipt does not prove durable target work",
            )
        return normalized, ""
    if status == BLOCKED_GAP:
        gap_code = _text(
            normalized.get("gapCode"),
            "backend gapCode",
        ).upper()
        return normalized, gap_code
    raise _gap(
        "BACKEND_TERMINAL_OUTCOME_UNPROVEN",
        "backend receipt is not a successful or blocked terminal outcome",
    )


def _queue_event_status(statuses: Sequence[str]) -> str:
    counts = {status: statuses.count(status) for status in set(statuses)}
    if not counts:
        return "APPLIED"
    if counts.get("RUNNING", 0):
        return "RUNNING"
    if counts.get("PENDING_REBUILD", 0):
        return "PENDING_REBUILD"
    if counts.get("FAILED", 0):
        return "FAILED"
    if counts.get(BLOCKED_GAP, 0):
        return BLOCKED_GAP
    if set(counts) == {SUCCEEDED}:
        return SUCCEEDED
    return "FAILED"


def _durable_terminal_receipts(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    plan: InvalidationPlan,
) -> tuple[tuple[Mapping[str, object], ...], dict[str, str]]:
    """Read actual queue state and receipts from one durable event."""

    normalized_event_id = _text(event_id, "backend eventId")
    try:
        event_rows = list(
            connection.execute(
                """
                SELECT event_kind, typeof(event_kind),
                       upstream_revision_id, typeof(upstream_revision_id),
                       payload_json, typeof(payload_json),
                       status, typeof(status)
                FROM invalidation_events
                WHERE event_id=?
                """,
                (normalized_event_id,),
            )
        )
        raw_queue_rows = list(
            connection.execute(
                """
                SELECT downstream_kind, typeof(downstream_kind),
                       downstream_id, typeof(downstream_id),
                       dependency_reason, typeof(dependency_reason),
                       status, typeof(status)
                FROM invalidation_queue
                WHERE event_id=?
                ORDER BY downstream_kind, downstream_id
                """,
                (normalized_event_id,),
            )
        )
    except sqlite3.DatabaseError as error:
        raise _gap(
            "BACKEND_EVENT_INVALID",
            "durable backend event tables are unreadable",
        ) from error
    if len(event_rows) != 1:
        raise _gap(
            "BACKEND_EVENT_MISSING",
            "one durable backend event is required",
        )
    (
        event_kind,
        event_kind_type,
        upstream_revision_id,
        upstream_revision_id_type,
        raw_payload,
        raw_payload_type,
        event_status,
        event_status_type,
    ) = event_rows[0]
    if (
        type(event_kind) is not str
        or event_kind_type != "text"
        or event_kind != "ASSET"
        or upstream_revision_id is not None
        or upstream_revision_id_type != "null"
        or type(raw_payload) is not str
        or raw_payload_type != "text"
        or type(event_status) is not str
        or event_status_type != "text"
    ):
        raise _gap(
            "BACKEND_EVENT_PLAN_MISMATCH",
            "durable backend event is not the additive ASSET event",
        )
    queue_rows: list[tuple[str, int, str, str]] = []
    for (
        kind,
        kind_type,
        target_id,
        target_id_type,
        reason,
        reason_type,
        status,
        status_type,
    ) in raw_queue_rows:
        if (
            type(kind) is not str
            or kind_type != "text"
            or not _is_integer(target_id)
            or target_id_type != "integer"
            or target_id < 1
            or type(reason) is not str
            or reason_type != "text"
            or not reason
            or type(status) is not str
            or status_type != "text"
        ):
            raise _gap(
                "BACKEND_EVENT_INVALID",
                "durable backend queue contains a non-canonical row",
            )
        queue_rows.append((kind, target_id, reason, status))
    try:
        payload = _strict_json(raw_payload)
    except (TypeError, ValueError) as error:
        raise _gap(
            "BACKEND_EVENT_INVALID",
            "durable backend event payload is invalid JSON",
        ) from error
    if not isinstance(payload, dict):
        raise _gap(
            "BACKEND_EVENT_INVALID",
            "durable backend event payload is not an object",
        )

    tasks = _plan_tasks(plan)
    queue_by_task = {
        (kind, target_id): (reason, status)
        for kind, target_id, reason, status in queue_rows
    }
    allowed_queue_statuses = {
        "PENDING_REBUILD",
        "RUNNING",
        SUCCEEDED,
        "FAILED",
        BLOCKED_GAP,
    }
    payload_plan_matches = all(
        type(payload.get(kind)) is list
        and all(_is_integer(item) for item in payload[kind])
        and tuple(payload[kind]) == tuple(values)
        for kind, values in plan.downstream.items()
    )
    if (
        len(queue_by_task) != len(queue_rows)
        or set(queue_by_task) != set(tasks)
        or any(
            status not in allowed_queue_statuses
            for _reason, status in queue_by_task.values()
        )
        or any(
            queue_by_task[key][0] != reason
            for key, reason in tasks.items()
        )
        or not payload_plan_matches
        or any(
            not str(key).startswith("_") and key not in plan.downstream
            for key in payload
        )
    ):
        raise _gap(
            "BACKEND_EVENT_PLAN_MISMATCH",
            "durable backend event queue differs from the verified plan",
        )
    observed_event_status = _queue_event_status(
        [status for _reason, status in queue_by_task.values()]
    )
    if event_status != observed_event_status:
        raise _gap(
            "BACKEND_EVENT_STATUS_MISMATCH",
            "durable event status differs from queue terminal state",
        )

    receipt_node = payload.get("_rebuildReceipts", {})
    if not isinstance(receipt_node, dict):
        raise _gap(
            "BACKEND_EVENT_INVALID",
            "durable backend receipt set is invalid",
        )
    receipts: list[Mapping[str, object]] = []
    receipt_tasks: set[tuple[str, int]] = set()
    for receipt_key, raw_receipt in receipt_node.items():
        if not isinstance(raw_receipt, Mapping):
            raise _gap(
                "BACKEND_TERMINAL_RECEIPT_INVALID",
                "durable backend receipt is not an object",
            )
        kind = raw_receipt.get("downstreamKind")
        target = raw_receipt.get("downstreamId")
        task_key = (
            (kind, target)
            if type(kind) is str and _is_integer(target)
            else ("", -1)
        )
        if (
            str(receipt_key) != f"{task_key[0]}:{task_key[1]}"
            or task_key not in queue_by_task
            or raw_receipt.get("eventId") != normalized_event_id
            or raw_receipt.get("status") != queue_by_task[task_key][1]
        ):
            raise _gap(
                "BACKEND_TERMINAL_RECEIPT_REPLAY",
                "durable backend receipt is not bound to its queue row",
            )
        receipts.append(raw_receipt)
        receipt_tasks.add(task_key)
    terminal_tasks = {
        task_key
        for task_key, (_reason, status) in queue_by_task.items()
        if status in {SUCCEEDED, "FAILED", BLOCKED_GAP}
    }
    if receipt_tasks != terminal_tasks:
        raise _gap(
            "BACKEND_EVENT_STATUS_MISMATCH",
            "terminal queue rows and durable receipts differ",
        )

    backend_event = {
        "eventId": normalized_event_id,
        "eventKind": "ASSET",
        "eventStatus": observed_event_status,
        "eventPayloadSha256": hashlib.sha256(
            raw_payload.encode("utf-8")
        ).hexdigest(),
        "queueSha256": _digest(queue_rows),
    }
    return tuple(receipts), backend_event


def _bind_terminal_receipts(
    plan: InvalidationPlan,
    raw_receipts: Iterable[Mapping[str, object]],
) -> tuple[tuple[dict[str, object], ...], list[str]]:
    tasks = _plan_tasks(plan)
    raw_values = tuple(raw_receipts)
    by_task: dict[tuple[str, int], Mapping[str, object]] = {}
    for raw in raw_values:
        if not isinstance(raw, Mapping):
            raise _gap(
                "BACKEND_TERMINAL_RECEIPT_INVALID",
                "backend terminal receipt is not a mapping",
            )
        kind = raw.get("downstreamKind")
        target = raw.get("downstreamId")
        key = (
            (kind, target)
            if type(kind) is str and _is_integer(target)
            else ("", -1)
        )
        if key not in tasks or key in by_task:
            raise _gap(
                "BACKEND_TERMINAL_RECEIPT_REPLAY",
                "backend receipt set contains an unrelated or duplicate task",
            )
        by_task[key] = raw

    normalized: list[dict[str, object]] = []
    gaps: list[str] = []
    event_id: str | None = None
    for (kind, target), reason in sorted(tasks.items()):
        raw = by_task.get((kind, target))
        if raw is None:
            gaps.append(
                f"BACKEND_TERMINAL_RECEIPT_MISSING_{kind}_{target}"
            )
            continue
        receipt, gap_code = _terminal_receipt(
            raw,
            expected_kind=kind,
            expected_id=target,
            expected_reason=reason,
            expected_event_id=event_id,
        )
        event_id = event_id or str(receipt["eventId"])
        normalized.append(receipt)
        if gap_code:
            gaps.append(gap_code)
    return tuple(normalized), sorted(set(gaps))


def build_add_only_delta_receipt(
    delta: AddOnlyBlueprintDelta,
    plan: InvalidationPlan,
    *,
    backend_connection: sqlite3.Connection,
    backend_event_id: str,
) -> dict[str, object]:
    context = _context(delta.trust_context)
    if context != TEST_ONLY:
        raise _gap(
            "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED",
            "receipt construction lacks independent signed production "
            "authorization",
        )
    artifact_payload = _json(delta.artifacts)
    if not isinstance(artifact_payload, list):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "delta artifact set is not a JSON array",
        )
    for artifact in artifact_payload:
        if (
            isinstance(artifact, Mapping)
            and artifact.get("trustContext") == PRODUCTION
        ):
            raise _gap(
                "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED",
                "receipt artifact lacks independent signed production "
                "authorization",
            )
    _validate_receipt_artifacts(
        artifact_payload,
        trust_context=context,
    )
    with _database_snapshot(
        backend_connection,
        immediate=True,
        require_new=True,
    ):
        return _build_add_only_delta_receipt_in_snapshot(
            delta,
            plan,
            backend_connection=backend_connection,
            backend_event_id=backend_event_id,
        )


def _build_add_only_delta_receipt_in_snapshot(
    delta: AddOnlyBlueprintDelta,
    plan: InvalidationPlan,
    *,
    backend_connection: sqlite3.Connection,
    backend_event_id: str,
) -> dict[str, object]:
    tasks = _validate_additive_plan_scope(
        plan,
        fact_ids=delta.fact_ids,
        entity_ids=delta.entity_ids,
        source_revision_ids=delta.source_revision_ids,
    )
    fact_scope = tuple(sorted(plan.downstream.get("FACT", ())))
    effective_scope = tuple(
        sorted(plan.downstream.get("EFFECTIVE_ENTITY", ()))
    )
    if (
        fact_scope != delta.fact_ids
        or not set(delta.entity_ids).issubset(effective_scope)
        or not tasks
    ):
        raise _gap(
            "DELTA_INVALIDATION_PLAN_MISMATCH",
            "invalidation plan is not bound to the verified delta",
        )
    current_database = logical_database_state(backend_connection)
    current_protected = _protected_table_sha256(current_database)
    if current_protected != dict(delta.protected_table_sha256):
        raise _gap(
            "DELTA_TRUTH_TABLE_DRIFT",
            "protected source/fact/evidence tables changed after delta "
            "verification",
        )
    try:
        observed_plan = plan_additive_asset_invalidation(
            backend_connection,
            fact_ids=delta.fact_ids,
            entity_ids=delta.entity_ids,
            source_revision_ids=delta.source_revision_ids,
            actual_write_tables=delta.changed_tables,
        )
    except InvalidationBlockedGap as error:
        raise _gap(
            "DELTA_INVALIDATION_PLAN_MISMATCH",
            f"{error.gap_code}: {error}",
        ) from error
    if observed_plan != plan:
        raise _gap(
            "DELTA_INVALIDATION_PLAN_MISMATCH",
            "current durable dependency graph differs from the supplied plan",
        )
    durable_receipts, backend_event = _durable_terminal_receipts(
        backend_connection,
        event_id=backend_event_id,
        plan=plan,
    )
    terminal_receipts, gaps = _bind_terminal_receipts(
        plan,
        durable_receipts,
    )
    final_database = logical_database_state(backend_connection)
    if (
        final_database.database_sha256 != current_database.database_sha256
        or _protected_table_sha256(final_database) != current_protected
    ):
        raise _gap(
            "DELTA_RECEIPT_SNAPSHOT_DRIFT",
            "durable state changed while the receipt snapshot was built",
        )
    body: dict[str, object] = {
        "schema": "ark-kb-add-only-blueprint-delta-receipt/v2",
        "operation": "ADD_ONLY_BLUEPRINT_ASSET_FOUNDATION",
        "trustContext": delta.trust_context,
        "status": BLOCKED_GAP if gaps else FOUNDATION_VERIFIED,
        "sourceDiffSha256": delta.source_diff_sha256,
        "artifacts": [_json(value) for value in delta.artifacts],
        "beforeDatabaseSha256": delta.before_database_sha256,
        "afterDatabaseSha256": delta.after_database_sha256,
        "protectedTableSha256": dict(delta.protected_table_sha256),
        "receiptDatabaseSha256": current_database.database_sha256,
        "changedTables": list(delta.changed_tables),
        "sourceRevisionIds": list(delta.source_revision_ids),
        "entityIds": list(delta.entity_ids),
        "factIds": list(delta.fact_ids),
        "invalidationPlan": {
            "eventKind": plan.event_kind,
            "downstream": {
                kind: list(values)
                for kind, values in sorted(plan.downstream.items())
            },
            "reasons": dict(sorted(plan.reasons.items())),
        },
        "backendEvent": backend_event,
        "backendTerminalReceipts": list(terminal_receipts),
        "blockedGaps": gaps,
        "published": False,
        "e4Scenario2Complete": False,
    }
    return {**body, "proof": "delta-proof://" + _digest(body)}


def _positive_ids(value: object, field: str) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not _is_integer(item) or item < 1
            for item in value
        )
        or value != sorted(set(value))
    ):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            f"{field} must be sorted unique positive integers",
        )
    return tuple(value)


def _receipt_artifact_uri(value: object, field: str) -> str:
    uri = _text(value, field)
    relative_text = uri[11:] if uri.startswith("artifact://") else ""
    if any(mark in relative_text for mark in ("\\", "%", "?", "#", "\x00")):
        relative_text = ""
    relative = PurePosixPath(relative_text)
    if (
        not relative_text
        or relative.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in relative.parts)
    ):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            f"{field} is not a safe artifact URI",
        )
    return uri


def _positive_size(value: object, field: str) -> int:
    if not _is_integer(value) or value < 1:
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            f"{field} is not a positive byte size",
        )
    return value


def _validate_receipt_artifacts(
    value: object,
    *,
    trust_context: str,
) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or len(value) != 1:
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "exactly one additive Blueprint artifact is required",
        )
    artifact = value[0]
    if (
        not isinstance(artifact, Mapping)
        or set(artifact) != _RECEIPT_ARTIFACT_FIELDS
    ):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "receipt artifact fields are invalid",
        )
    source_fingerprint = _sha(
        artifact.get("sourceFingerprint"),
        "artifact sourceFingerprint",
    )
    aggregate = _sha(
        artifact.get("sourceAggregateSha256"),
        "artifact sourceAggregateSha256",
    )
    if (
        source_fingerprint != aggregate
        or _context(artifact.get("trustContext")) != trust_context
    ):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "artifact fingerprint or trust context is inconsistent",
        )
    _sha(artifact.get("sourceId"), "artifact sourceId")
    _receipt_artifact_uri(artifact.get("artifactUri"), "artifactUri")
    _sha(artifact.get("artifactSha256"), "artifactSha256")
    _positive_size(artifact.get("artifactBytes"), "artifactBytes")
    _text(artifact.get("sourceRevisionLabel"), "sourceRevisionLabel")
    core_source_uri = _text(
        artifact.get("coreSourceUri"),
        "coreSourceUri",
    )
    if not core_source_uri.startswith("bp://"):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "coreSourceUri is not a Blueprint source URI",
        )
    _sha(
        artifact.get("coreSourceFingerprint"),
        "coreSourceFingerprint",
    )
    for field in (
        "entityUri",
        "producerVersion",
        "schemaVersion",
        "generatedAt",
    ):
        _text(artifact.get(field), field)
    manifest = artifact.get("manifestArtifact")
    if manifest is not None:
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != _MANIFEST_ARTIFACT_FIELDS
        ):
            raise _gap(
                "DELTA_RECEIPT_INVALID",
                "manifest artifact fields are invalid",
            )
        _receipt_artifact_uri(
            manifest.get("artifactUri"),
            "manifest artifactUri",
        )
        _sha(manifest.get("artifactSha256"), "manifest artifactSha256")
        _positive_size(
            manifest.get("artifactBytes"),
            "manifest artifactBytes",
        )
    return (artifact,)


def _receipt_plan(value: object) -> InvalidationPlan:
    if not isinstance(value, Mapping) or set(value) != {
        "eventKind",
        "downstream",
        "reasons",
    }:
        raise _gap("DELTA_RECEIPT_INVALID", "receipt plan is invalid")
    downstream = value.get("downstream")
    reasons = value.get("reasons")
    if not isinstance(downstream, Mapping) or not isinstance(reasons, Mapping):
        raise _gap("DELTA_RECEIPT_INVALID", "receipt plan is invalid")
    normalized: dict[str, tuple[int, ...]] = {}
    for raw_kind, raw_values in downstream.items():
        if not isinstance(raw_kind, str) or not isinstance(raw_values, list):
            raise _gap("DELTA_RECEIPT_INVALID", "receipt plan is invalid")
        if any(not _is_integer(item) for item in raw_values):
            raise _gap("DELTA_RECEIPT_INVALID", "receipt plan is invalid")
        normalized[raw_kind] = tuple(raw_values)
    if any(
        not isinstance(key, str) or not isinstance(child, str)
        for key, child in reasons.items()
    ):
        raise _gap("DELTA_RECEIPT_INVALID", "receipt reasons are invalid")
    return InvalidationPlan(
        event_kind=str(value.get("eventKind") or ""),
        upstream_revision_id=None,
        downstream=normalized,
        reasons=dict(reasons),
    )


def validate_add_only_delta_receipt(
    receipt: Mapping[str, object],
    *,
    expected_receipt_sha256: str,
) -> Mapping[str, object]:
    """Validate against an out-of-band content address, not a self-hash."""

    expected = _sha(
        expected_receipt_sha256,
        "expectedReceiptSha256",
    )
    if not isinstance(receipt, Mapping) or set(receipt) != _RECEIPT_FIELDS:
        raise _gap("DELTA_RECEIPT_INVALID", "receipt fields are invalid")
    body = dict(receipt)
    proof = body.pop("proof", None)
    observed = _digest(body)
    if proof != "delta-proof://" + observed:
        raise _gap(
            "DELTA_RECEIPT_PROOF_INVALID",
            "delta receipt content proof does not match",
        )
    if observed != expected:
        raise _gap(
            "OUT_OF_BAND_RECEIPT_SHA256_MISMATCH",
            "out-of-band expected receipt SHA-256 does not match",
        )
    gaps = receipt.get("blockedGaps")
    status = receipt.get("status")
    invalid_status = (
        not isinstance(gaps, list)
        or any(not isinstance(value, str) or not value for value in gaps)
        or gaps != sorted(set(gaps))
        or status == FOUNDATION_VERIFIED
        and bool(gaps)
        or status == BLOCKED_GAP
        and not gaps
        or status not in {FOUNDATION_VERIFIED, BLOCKED_GAP}
    )
    changed = receipt.get("changedTables")
    backend_event = receipt.get("backendEvent")
    terminal = receipt.get("backendTerminalReceipts")
    protected = receipt.get("protectedTableSha256")
    if (
        invalid_status
        or receipt.get("schema")
        != "ark-kb-add-only-blueprint-delta-receipt/v2"
        or receipt.get("operation")
        != "ADD_ONLY_BLUEPRINT_ASSET_FOUNDATION"
        or receipt.get("published") is not False
        or receipt.get("e4Scenario2Complete") is not False
        or not isinstance(changed, list)
        or changed != sorted(_WRITE_TABLES)
        or not isinstance(protected, Mapping)
        or set(protected) != set(_PROTECTED_TABLES)
        or not isinstance(backend_event, Mapping)
        or set(backend_event) != _BACKEND_EVENT_FIELDS
        or not isinstance(terminal, list)
    ):
        raise _gap("DELTA_RECEIPT_INVALID", "receipt contract is invalid")
    context = _context(receipt.get("trustContext"))
    if context != TEST_ONLY:
        raise _gap(
            "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED",
            "receipt lacks independent signed production authorization",
        )
    for table in _PROTECTED_TABLES:
        _sha(protected.get(table), f"protectedTableSha256.{table}")
    source_revision_ids = _positive_ids(
        receipt.get("sourceRevisionIds"),
        "sourceRevisionIds",
    )
    entity_ids = _positive_ids(receipt.get("entityIds"), "entityIds")
    fact_ids = _positive_ids(receipt.get("factIds"), "factIds")
    if len(source_revision_ids) != 1 or len(entity_ids) != 1:
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "single-Blueprint receipt has an invalid source/entity scope",
        )
    _validate_receipt_artifacts(
        receipt.get("artifacts"),
        trust_context=context,
    )
    for field in (
        "sourceDiffSha256",
        "beforeDatabaseSha256",
        "afterDatabaseSha256",
        "receiptDatabaseSha256",
    ):
        _sha(receipt.get(field), field)
    if (
        receipt.get("beforeDatabaseSha256")
        == receipt.get("afterDatabaseSha256")
    ):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "base and staged database digests must differ",
        )
    backend_event_id = _text(
        backend_event.get("eventId"),
        "backend eventId",
    )
    if (
        backend_event.get("eventKind") != "ASSET"
        or backend_event.get("eventStatus")
        not in {
            "APPLIED",
            "PENDING_REBUILD",
            "RUNNING",
            "SUCCEEDED",
            "FAILED",
            BLOCKED_GAP,
        }
    ):
        raise _gap("DELTA_RECEIPT_INVALID", "backend event is invalid")
    _sha(
        backend_event.get("eventPayloadSha256"),
        "eventPayloadSha256",
    )
    _sha(backend_event.get("queueSha256"), "queueSha256")
    plan = _receipt_plan(receipt.get("invalidationPlan"))
    _validate_additive_plan_scope(
        plan,
        fact_ids=fact_ids,
        entity_ids=entity_ids,
        source_revision_ids=source_revision_ids,
    )
    normalized_terminal, observed_gaps = _bind_terminal_receipts(
        plan,
        terminal,
    )
    if (
        observed_gaps != gaps
        or list(normalized_terminal) != terminal
        or status == FOUNDATION_VERIFIED
        and backend_event.get("eventStatus") != SUCCEEDED
        or status == BLOCKED_GAP
        and backend_event.get("eventStatus") == SUCCEEDED
        or any(
            value.get("eventId") != backend_event_id
            for value in normalized_terminal
        )
    ):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "terminal receipt set does not match blocked gaps",
        )
    return _frozen_mapping(receipt)


def validate_add_only_delta_receipt_durable_state(
    receipt: Mapping[str, object],
    *,
    connection: sqlite3.Connection,
) -> None:
    """Rebind a validated v2 diagnostic receipt to one live staged Core."""

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("SQLite connection is required")
    proof = receipt.get("proof") if isinstance(receipt, Mapping) else None
    content_sha256 = (
        proof.removeprefix("delta-proof://")
        if isinstance(proof, str)
        else ""
    )
    validate_add_only_delta_receipt(
        receipt,
        expected_receipt_sha256=content_sha256,
    )
    plan = _receipt_plan(receipt.get("invalidationPlan"))
    backend_event = receipt.get("backendEvent")
    if not isinstance(backend_event, Mapping):
        raise _gap(
            "DELTA_RECEIPT_INVALID",
            "receipt backend event is invalid",
        )
    event_id = _text(backend_event.get("eventId"), "backend eventId")
    with _database_snapshot(
        connection,
        immediate=True,
        require_new=True,
    ):
        before = logical_database_state(connection)
        if (
            receipt.get("receiptDatabaseSha256")
            != before.database_sha256
            or receipt.get("afterDatabaseSha256")
            != before.database_sha256
            or dict(receipt.get("protectedTableSha256") or {})
            != _protected_table_sha256(before)
        ):
            raise _gap(
                "DELTA_RECEIPT_DATABASE_BINDING_MISMATCH",
                "receipt does not match the live staged Core",
            )
        durable_receipts, durable_event = _durable_terminal_receipts(
            connection,
            event_id=event_id,
            plan=plan,
        )
        terminal, gaps = _bind_terminal_receipts(
            plan,
            durable_receipts,
        )
        if (
            list(terminal)
            != list(receipt.get("backendTerminalReceipts") or ())
            or durable_event != dict(backend_event)
            or gaps != list(receipt.get("blockedGaps") or ())
        ):
            raise _gap(
                "DELTA_RECEIPT_DURABLE_EVENT_MISMATCH",
                "receipt event or terminal outcomes differ from staged Core",
            )
        after = logical_database_state(connection)
        if after.database_sha256 != before.database_sha256:
            raise _gap(
                "DELTA_RECEIPT_SNAPSHOT_DRIFT",
                "staged Core changed during durable receipt inspection",
            )
