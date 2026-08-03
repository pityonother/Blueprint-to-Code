"""Fail-closed reader for immutable Blueprint Evidence Publication v3.

The v3 current pointer is deliberately a small trust root.  This module reads
the pointer and manifest as raw bytes, binds every declared artifact, validates
the SQLite identity in read-only mode, and only then reports the revision as
available.  It never falls back to the v2 or legacy layouts.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .evidence_schema import (
    EVIDENCE_SCHEMA_USER_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    make_asset_id,
    make_revision_id,
)
from .bound_database import (
    BoundDatabaseError,
    materialize_bound_database_snapshot,
)


CURRENT_POINTER_SCHEMA = "blueprint-to-code.evidence-current/v1"
_CURRENT_POINTER_KEYS = frozenset(
    {"schema", "revisionId", "manifest", "manifestSha256", "mode"}
)
EVIDENCE_REVISION_MANIFEST_SCHEMA = (
    "blueprint-to-code.evidence-revision-manifest/v3"
)

FRESH = "FRESH"
STALE = "STALE"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_ASSET_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_MAX_POINTER_BYTES = 64 * 1024
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_AGENT_INDEX_BYTES = 16 * 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_REVISION_FILES = frozenset({"evidence.sqlite", "manifest.json", "agent_index.md"})
_CORE_COUNT_FIELDS = (
    "graphs",
    "nodes",
    "pins",
    "links",
    "edgeObservations",
)
_COUNT_FIELD_TABLE = {
    "graphs": "graphs",
    "nodes": "nodes",
    "pins": "pins",
    "links": "edges",
    "edges": "edges",
    "edgeObservations": "edge_observations",
    "edge_observations": "edge_observations",
    "assetRevisions": "asset_revisions",
    "asset_revisions": "asset_revisions",
    "properties": "properties",
    "classDefaults": "class_defaults",
    "class_defaults": "class_defaults",
    "diagnostics": "diagnostics",
    "coverage": "coverage",
    "references": "references",
    "derivedClaims": "derived_claims",
    "derived_claims": "derived_claims",
    "claimEvidence": "claim_evidence",
    "claim_evidence": "claim_evidence",
    "searchEntities": "search_entities",
    "search_entities": "search_entities",
    "searchMaterialization": "search_materialization",
    "search_materialization": "search_materialization",
    "sourceManifest": "source_manifest",
    "source_manifest": "source_manifest",
}
_ALLOWED_COUNT_FIELDS = frozenset(_COUNT_FIELD_TABLE)
_REQUIRED_TABLES = frozenset(
    {
        "asset_revisions",
        "graphs",
        "nodes",
        "pins",
        "edges",
        "edge_observations",
        "source_manifest",
    }
)


class EvidenceArtifactInvalid(ValueError):
    """A v3 pointer, manifest, artifact, or SQLite identity is invalid."""

    def __init__(self, code: str, message: str | None = None) -> None:
        if message is None:
            message = str(code)
            code = "EVIDENCE_ARTIFACT_INVALID"
        self.code = str(code).strip().upper() or "EVIDENCE_ARTIFACT_INVALID"
        self.detail = str(message)
        super().__init__(f"{self.code}: {self.detail}")


@dataclass(frozen=True)
class ValidatedEvidenceRevision:
    """Paths and verified public identity for one immutable v3 revision."""

    paths: dict[str, Path]
    manifest: dict[str, Any]
    manifest_raw: bytes
    agent_index_raw: bytes
    revision_id: str
    asset_id: str
    object_path: str
    manifest_sha256: str
    pointer_sha256: str | None
    freshness_status: str
    release_authority: bool

    @property
    def asset_dir(self) -> Path:
        return self.paths["asset"]

    @property
    def evidence_root(self) -> Path:
        return self.paths["evidence"]

    @property
    def revision_dir(self) -> Path:
        return self.paths["revision"]

    @property
    def manifest_path(self) -> Path:
        return self.paths["manifest"]

    @property
    def database_path(self) -> Path:
        return self.paths["database"]

    @property
    def agent_index_path(self) -> Path:
        return self.paths["agent_index"]

    @property
    def pointer_path(self) -> Path | None:
        return self.paths.get("pointer")


def _invalid(code: str, detail: str) -> EvidenceArtifactInvalid:
    return EvidenceArtifactInvalid(code, detail)


def _absolute_path(value: str | os.PathLike[str]) -> Path:
    # abspath removes lexical ``..`` without resolving a symlink or junction;
    # the latter must remain visible to lstat/reparse checks below.
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        int(getattr(metadata, "st_file_attributes", 0))
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _assert_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise FileNotFoundError(f"{label.upper().replace(' ', '_')}_MISSING") from None
    except OSError as exc:
        raise _invalid("PATH_UNREADABLE", f"{label} cannot be inspected") from exc
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
        raise _invalid("REPARSE_PATH_REJECTED", f"{label} must not be a symlink or reparse point")
    if not stat.S_ISDIR(metadata.st_mode):
        raise _invalid("SPECIAL_PATH_REJECTED", f"{label} must be a directory")
    return metadata


def _assert_plain_directory_chain(path: Path, label: str) -> None:
    """Reject a link/reparse component anywhere in an asset directory path."""

    components: list[Path] = []
    current = path
    while current != current.parent:
        components.append(current)
        current = current.parent
    for component in reversed(components):
        _assert_directory(component, label)


def _assert_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise FileNotFoundError(f"{label.upper().replace(' ', '_')}_MISSING") from None
    except OSError as exc:
        raise _invalid("PATH_UNREADABLE", f"{label} cannot be inspected") from exc
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
        raise _invalid("REPARSE_PATH_REJECTED", f"{label} must not be a symlink or reparse point")
    if not stat.S_ISREG(metadata.st_mode):
        raise _invalid("SPECIAL_FILE_REJECTED", f"{label} must be a regular file")
    if int(metadata.st_nlink) != 1:
        raise _invalid("HARDLINK_REJECTED", f"{label} must not have hard-link aliases")
    return metadata


def _assert_contained(root: Path, path: Path, label: str) -> None:
    try:
        common = os.path.commonpath((os.fspath(root), os.fspath(path)))
    except ValueError as exc:
        raise _invalid("PATH_ESCAPE", f"{label} is outside the evidence root") from exc
    if os.path.normcase(common) != os.path.normcase(os.fspath(root)):
        raise _invalid("PATH_ESCAPE", f"{label} is outside the evidence root")


def _safe_relative_posix(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise _invalid("PATH_INVALID", f"{label} path is missing")
    if value != value.strip() or "\\" in value or "\x00" in value:
        raise _invalid("PATH_INVALID", f"{label} must be a normalized POSIX path")
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise _invalid("PATH_INVALID", f"{label} must be a relative POSIX path")
    path = PurePosixPath(value)
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _invalid("PATH_TRAVERSAL", f"{label} contains an unsafe segment")
    if any(":" in part for part in parts):
        raise _invalid("PATH_INVALID", f"{label} contains a drive or alternate stream")
    if any(any(ord(character) < 32 for character in part) for part in parts):
        raise _invalid("PATH_INVALID", f"{label} contains a control character")
    return path


def _path_from_relative(root: Path, value: object, label: str) -> Path:
    relative = _safe_relative_posix(value, label)
    candidate = _absolute_path(root.joinpath(*relative.parts))
    _assert_contained(root, candidate, label)
    return candidate


def _file_signature(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _open_bound_file(path: Path, label: str) -> tuple[int, os.stat_result]:
    before = _assert_regular_file(path, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _invalid("FILE_OPEN_FAILED", f"{label} cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _invalid("SPECIAL_FILE_REJECTED", f"{label} must be a regular file")
        # Size + inode/device bind the opened handle to the lstat observation.
        if _file_signature(opened)[:3] != _file_signature(before)[:3]:
            raise _invalid("FILE_CHANGED_DURING_READ", f"{label} changed while opening")
        return descriptor, before
    except Exception:
        os.close(descriptor)
        raise


def _finish_bound_file(path: Path, label: str, before: os.stat_result) -> None:
    after = _assert_regular_file(path, label)
    if _file_signature(after) != _file_signature(before):
        raise _invalid("FILE_CHANGED_DURING_READ", f"{label} changed while reading")


def _read_bound_bytes(path: Path, label: str, *, maximum: int) -> bytes:
    descriptor, before = _open_bound_file(path, label)
    try:
        if before.st_size > maximum:
            raise _invalid("FILE_TOO_LARGE", f"{label} exceeds its size limit")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum:
            raise _invalid("FILE_TOO_LARGE", f"{label} exceeds its size limit")
    finally:
        os.close(descriptor)
    _finish_bound_file(path, label, before)
    return payload


def _hash_bound_file(
    path: Path,
    label: str,
    *,
    expected_size: int | None = None,
) -> tuple[str, int, os.stat_result]:
    descriptor, before = _open_bound_file(path, label)
    digest = hashlib.sha256()
    size = 0
    size_mismatch = (
        expected_size is not None and int(before.st_size) != int(expected_size)
    )
    try:
        if not size_mismatch:
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    finally:
        os.close(descriptor)
    _finish_bound_file(path, label, before)
    if size_mismatch:
        raise _invalid(
            "FILE_SIZE_MISMATCH",
            f"{label} size differs from its evidence binding",
        )
    if size != int(before.st_size):
        raise _invalid("FILE_CHANGED_DURING_READ", f"{label} size changed while hashing")
    return digest.hexdigest(), size, before


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _assert_unicode_scalars(value: object) -> None:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        return
    if isinstance(value, list):
        for item in value:
            _assert_unicode_scalars(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_unicode_scalars(key)
            _assert_unicode_scalars(item)


def _strict_json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_float,
        )
        _assert_unicode_scalars(payload)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _invalid("JSON_INVALID", f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise _invalid("JSON_TYPE_INVALID", f"{label} must contain one JSON object")
    return payload


def _required_text(
    payload: dict[str, Any],
    field: str,
    label: str,
    *,
    maximum: int = 1024,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        raise _invalid("FIELD_INVALID", f"{label}.{field} must be a non-empty string")
    if len(value) > maximum:
        raise _invalid("FIELD_INVALID", f"{label}.{field} is too long")
    return value


def _required_sha256(payload: dict[str, Any], field: str, label: str) -> str:
    value = _required_text(payload, field, label, maximum=64)
    if not _SHA256_RE.fullmatch(value):
        raise _invalid("HASH_INVALID", f"{label}.{field} must be a lowercase SHA-256")
    return value


def _required_size(payload: dict[str, Any], field: str, label: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid("SIZE_INVALID", f"{label}.{field} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class _SourceEntry:
    path: str
    sha256: str
    size_bytes: int
    source_kind: str


@dataclass(frozen=True)
class _ManifestContract:
    database_path: Path
    agent_index_path: Path
    database_sha256: str
    database_bytes: int
    agent_index_sha256: str
    agent_index_bytes: int
    sources: tuple[_SourceEntry, ...]
    counts: dict[str, int]
    graph_coverage: dict[str, int]
    link_recovery_counts: dict[str, int]


@dataclass(frozen=True)
class _DatabaseIdentity:
    uasset_path: str
    sources: tuple[_SourceEntry, ...]
    counts: dict[str, int]
    graph_coverage: dict[str, int]
    link_recovery_counts: dict[str, int]


def _reject_public_local_paths(payload: object, *, field: str = "manifest") -> None:
    """Reject obvious machine-local paths from the public JSON envelope."""

    if isinstance(payload, str):
        leaf_field = field.rsplit(".", 1)[-1].replace("_", "").casefold()
        if (
            _WINDOWS_ABSOLUTE_RE.match(payload)
            or payload.startswith("\\\\")
            or payload.casefold().startswith("file://")
            or (payload.startswith("/") and leaf_field != "objectpath")
        ):
            raise _invalid(
                "LOCAL_PATH_DISCLOSURE",
                f"{field} contains a machine-local absolute path",
            )
        return
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            _reject_public_local_paths(item, field=f"{field}[{index}]")
        return
    if isinstance(payload, dict):
        forbidden_keys = {
            "assetdir",
            "databasepath",
            "agentindexpath",
            "manifestpath",
            "sourcepath",
            "uassetpath",
        }
        for key, item in payload.items():
            if str(key).replace("_", "").casefold() in forbidden_keys:
                raise _invalid(
                    "LOCAL_PATH_DISCLOSURE",
                    f"{field} contains a machine-local path field",
                )
            _reject_public_local_paths(item, field=f"{field}.{key}")


def _source_entries(value: object) -> tuple[_SourceEntry, ...]:
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        # Accept a path-keyed representation for old v3 prerelease artifacts,
        # while normalizing it to the canonical list representation.
        rows = []
        for path, metadata in value.items():
            if not isinstance(metadata, dict):
                raise _invalid(
                    "SOURCE_MANIFEST_INVALID",
                    "sourceManifest path entries must contain objects",
                )
            rows.append({"path": path, **metadata})
    else:
        raise _invalid(
            "SOURCE_MANIFEST_INVALID",
            "manifest.sourceManifest must be an array",
        )
    if not rows:
        raise _invalid(
            "SOURCE_MANIFEST_INVALID",
            "manifest.sourceManifest must not be empty",
        )

    entries: list[_SourceEntry] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        label = f"manifest.sourceManifest[{index}]"
        if not isinstance(raw, dict):
            raise _invalid("SOURCE_MANIFEST_INVALID", f"{label} must be an object")
        path = _required_text(raw, "path", label, maximum=2048)
        _safe_relative_posix(path, f"{label}.path")
        if path in seen:
            raise _invalid("SOURCE_MANIFEST_INVALID", f"duplicate source path {path!r}")
        seen.add(path)
        sha256 = _required_sha256(raw, "sha256", label)
        if "bytes" in raw:
            size_bytes = _required_size(raw, "bytes", label)
            if "sizeBytes" in raw and raw["sizeBytes"] != size_bytes:
                raise _invalid(
                    "SOURCE_MANIFEST_INVALID",
                    f"{label} has conflicting byte sizes",
                )
        else:
            size_bytes = _required_size(raw, "sizeBytes", label)
        source_kind = _required_text(raw, "sourceKind", label, maximum=128)
        entries.append(
            _SourceEntry(
                path=path,
                sha256=sha256,
                size_bytes=size_bytes,
                source_kind=source_kind,
            )
        )
    entries.sort(key=lambda row: row.path)
    return tuple(entries)


def _count_map(
    value: object,
    label: str,
    *,
    required: tuple[str, ...] = (),
    allowed: frozenset[str] | None = None,
) -> dict[str, int]:
    if not isinstance(value, dict):
        raise _invalid("COUNTS_INVALID", f"{label} must be an object")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            raise _invalid("COUNTS_INVALID", f"{label} contains an invalid key")
        if allowed is not None and key not in allowed:
            raise _invalid("COUNTS_INVALID", f"{label} contains unsupported key {key!r}")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise _invalid("COUNTS_INVALID", f"{label}.{key} must be a non-negative integer")
        result[key] = count
    missing = [key for key in required if key not in result]
    if missing:
        raise _invalid("COUNTS_INVALID", f"{label} is missing required counts")
    return result


def _validate_artifact(
    artifacts: dict[str, Any],
    key: str,
    expected_path: str,
    revision_dir: Path,
) -> tuple[Path, str, int]:
    value = artifacts.get(key)
    label = f"manifest.artifacts.{key}"
    if not isinstance(value, dict):
        raise _invalid("ARTIFACT_DECLARATION_INVALID", f"{label} must be an object")
    relative = _required_text(value, "path", label, maximum=128)
    _safe_relative_posix(relative, f"{label}.path")
    if relative != expected_path:
        raise _invalid(
            "ARTIFACT_PATH_INVALID",
            f"{label}.path must be {expected_path!r}",
        )
    artifact_path = _path_from_relative(revision_dir, relative, label)
    sha256 = _required_sha256(value, "sha256", label)
    size_bytes = _required_size(value, "bytes", label)
    return artifact_path, sha256, size_bytes


def _validate_manifest_shape(
    manifest: dict[str, Any],
    *,
    revision_id: str,
    revision_dir: Path,
) -> _ManifestContract:
    _reject_public_local_paths(manifest)
    if manifest.get("schema") != EVIDENCE_REVISION_MANIFEST_SCHEMA:
        raise _invalid("MANIFEST_SCHEMA_INVALID", "evidence manifest schema is invalid")
    if _required_text(manifest, "revisionId", "manifest", maximum=24) != revision_id:
        raise _invalid("REVISION_MISMATCH", "manifest revision differs from its directory")
    asset_id = _required_text(manifest, "assetId", "manifest", maximum=24)
    if not _ASSET_ID_RE.fullmatch(asset_id):
        raise _invalid("ASSET_ID_INVALID", "manifest.assetId is invalid")
    _required_text(manifest, "objectPath", "manifest", maximum=2048)
    _required_text(manifest, "parserVersion", "manifest", maximum=256)
    if _required_text(
        manifest,
        "evidenceSchemaVersion",
        "manifest",
        maximum=128,
    ) != EVIDENCE_SCHEMA_VERSION:
        raise _invalid("EVIDENCE_SCHEMA_INVALID", "manifest evidence schema is invalid")
    _required_sha256(manifest, "sourceFingerprint", "manifest")
    semantic_digest = _required_sha256(manifest, "semanticDigest", "manifest")
    if "semanticDigestSchema" in manifest and manifest["semanticDigestSchema"] != (
        "blueprint-to-code.evidence-semantic-digest/v1"
    ):
        raise _invalid("SEMANTIC_DIGEST_INVALID", "semantic digest schema is invalid")
    generated_at = _required_text(manifest, "generatedAt", "manifest", maximum=128)
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _invalid("GENERATED_AT_INVALID", "manifest.generatedAt is not ISO-8601") from exc
    if timestamp.tzinfo is None:
        raise _invalid("GENERATED_AT_INVALID", "manifest.generatedAt must include a timezone")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"database", "agentIndex"}:
        raise _invalid(
            "ARTIFACT_DECLARATION_INVALID",
            "manifest.artifacts must bind database and agentIndex only",
        )
    database_path, database_sha256, database_bytes = _validate_artifact(
        artifacts,
        "database",
        "evidence.sqlite",
        revision_dir,
    )
    agent_index_path, agent_index_sha256, agent_index_bytes = _validate_artifact(
        artifacts,
        "agentIndex",
        "agent_index.md",
        revision_dir,
    )
    if database_bytes == 0:
        raise _invalid("SIZE_INVALID", "evidence database must not be empty")
    if agent_index_bytes > _MAX_AGENT_INDEX_BYTES:
        raise _invalid("FILE_TOO_LARGE", "agent index exceeds its size limit")

    sources = _source_entries(manifest.get("sourceManifest"))
    counts = _count_map(
        manifest.get("counts"),
        "manifest.counts",
        required=_CORE_COUNT_FIELDS,
        allowed=_ALLOWED_COUNT_FIELDS,
    )
    graph_coverage = _count_map(
        manifest.get("graphCoverage"),
        "manifest.graphCoverage",
    )
    link_recovery_counts = _count_map(
        manifest.get("linkRecoveryCounts"),
        "manifest.linkRecoveryCounts",
    )
    # Read now so a future digest implementation cannot accidentally accept a
    # non-string via implicit coercion.
    assert semantic_digest
    return _ManifestContract(
        database_path=database_path,
        agent_index_path=agent_index_path,
        database_sha256=database_sha256,
        database_bytes=database_bytes,
        agent_index_sha256=agent_index_sha256,
        agent_index_bytes=agent_index_bytes,
        sources=sources,
        counts=counts,
        graph_coverage=graph_coverage,
        link_recovery_counts=link_recovery_counts,
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _semantic_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, float) and not math.isfinite(value):
        raise _invalid("SEMANTIC_DIGEST_INVALID", "SQLite contains a non-finite number")
    return value


def _semantic_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _database_semantic_digest(connection: sqlite3.Connection) -> str:
    """Recompute the publisher's v1 logical digest from read-only SQLite."""

    digest = hashlib.sha256()
    digest.update(b"blueprint-to-code.evidence-semantic-digest/v1\n")
    table_rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for table_name_raw, create_sql_raw in table_rows:
        table_name = str(table_name_raw)
        if table_name.startswith("search_fts"):
            continue
        columns_info = connection.execute(
            f"PRAGMA table_info({_quote_identifier(table_name)})"
        ).fetchall()
        selected = [
            str(row[1])
            for row in columns_info
            if str(row[1]).casefold() not in {"generated_at", "uasset_path"}
        ]
        digest.update(
            _semantic_json_bytes(
                {
                    "table": table_name,
                    "columns": selected,
                    "schema": str(create_sql_raw or ""),
                }
            )
        )
        if not selected:
            continue
        primary_key = [
            (int(row[5]), str(row[1]))
            for row in columns_info
            if int(row[5] or 0) > 0 and str(row[1]) in selected
        ]
        primary_key.sort()
        order_columns = [name for _ordinal, name in primary_key]
        select_sql = ", ".join(_quote_identifier(name) for name in selected)
        query = f"SELECT {select_sql} FROM {_quote_identifier(table_name)}"
        if order_columns:
            query += " ORDER BY " + ", ".join(
                _quote_identifier(name) for name in order_columns
            )
        else:
            query += " ORDER BY rowid"
        for row in connection.execute(query):
            digest.update(
                _semantic_json_bytes([_semantic_value(value) for value in row])
            )
    return digest.hexdigest()


def _validate_database(
    connection: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    contract: _ManifestContract,
) -> _DatabaseIdentity:
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
        if query_only != 1:
            raise _invalid("SQLITE_NOT_QUERY_ONLY", "SQLite query_only could not be enabled")
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if user_version != EVIDENCE_SCHEMA_USER_VERSION:
            raise _invalid("SQLITE_USER_VERSION_INVALID", "SQLite user_version is invalid")

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not _REQUIRED_TABLES.issubset(tables):
            raise _invalid("SQLITE_SCHEMA_INVALID", "evidence database tables are incomplete")
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        if len(integrity_rows) != 1 or str(integrity_rows[0][0]) != "ok":
            raise _invalid("SQLITE_INTEGRITY_FAILED", "SQLite integrity_check failed")
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_rows:
            raise _invalid("SQLITE_FOREIGN_KEY_FAILED", "SQLite foreign_key_check failed")

        revision_count = int(
            connection.execute("SELECT COUNT(*) FROM asset_revisions").fetchone()[0]
        )
        if revision_count != 1:
            raise _invalid("SQLITE_IDENTITY_INVALID", "evidence database must contain one revision")
        identity = connection.execute(
            "SELECT revision_id, asset_id, object_path, source_fingerprint, "
            "parser_version, schema_version, generated_at, uasset_path "
            "FROM asset_revisions LIMIT 1"
        ).fetchone()
        if identity is None:
            raise _invalid("SQLITE_IDENTITY_INVALID", "evidence database identity is missing")
        object_path = str(identity["object_path"])
        asset_id = str(identity["asset_id"])
        if make_asset_id(object_path) != asset_id:
            raise _invalid(
                "SQLITE_ASSET_ID_INVALID",
                "SQLite asset id is not derived from its object path",
            )
        comparisons = (
            ("revision_id", "revisionId", "revision"),
            ("asset_id", "assetId", "asset"),
            ("object_path", "objectPath", "object path"),
            ("source_fingerprint", "sourceFingerprint", "source fingerprint"),
            ("parser_version", "parserVersion", "parser version"),
            ("schema_version", "evidenceSchemaVersion", "evidence schema"),
            ("generated_at", "generatedAt", "generated timestamp"),
        )
        for database_field, manifest_field, label in comparisons:
            if str(identity[database_field]) != str(manifest.get(manifest_field) or ""):
                raise _invalid("SQLITE_IDENTITY_MISMATCH", f"manifest {label} differs from SQLite")

        source_rows = tuple(
            sorted(
                (
                    _SourceEntry(
                        path=str(row[0]),
                        sha256=str(row[1]),
                        size_bytes=int(row[2]),
                        source_kind=str(row[3]),
                    )
                    for row in connection.execute(
                        "SELECT path, sha256, size_bytes, source_kind "
                        "FROM source_manifest ORDER BY path"
                    )
                ),
                key=lambda row: row.path,
            )
        )
        source_hashes = {row.path: row.sha256 for row in source_rows}
        expected_revision_id = make_revision_id(
            source_hashes,
            parser_version=str(identity["parser_version"]),
            schema_version=str(identity["schema_version"]),
        )
        if expected_revision_id != str(identity["revision_id"]):
            raise _invalid(
                "SQLITE_REVISION_ID_INVALID",
                "SQLite revision id is not derived from its source identity",
            )
        fingerprint_payload = json.dumps(
            sorted(source_hashes.items()),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if hashlib.sha256(fingerprint_payload).hexdigest() != str(
            identity["source_fingerprint"]
        ):
            raise _invalid(
                "SQLITE_SOURCE_FINGERPRINT_INVALID",
                "SQLite source fingerprint is not derived from sourceManifest",
            )
        actual_counts = {
            field: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{_COUNT_FIELD_TABLE[field]}"'
                ).fetchone()[0]
            )
            for field in contract.counts
        }
        if actual_counts != contract.counts:
            raise _invalid("COUNT_MISMATCH", "manifest counts differ from SQLite")
        graph_coverage = {
            str(row[0] or "unknown"): int(row[1])
            for row in connection.execute(
                "SELECT status, COUNT(*) FROM graphs GROUP BY status ORDER BY status"
            )
        }
        if graph_coverage != contract.graph_coverage:
            raise _invalid(
                "GRAPH_COVERAGE_MISMATCH",
                "manifest graph coverage differs from SQLite",
            )
        link_recovery_counts = {
            str(row[0] or "NOT_RECOVERED"): int(row[1])
            for row in connection.execute(
            "SELECT COALESCE(NULLIF(resolution_status, ''), status, ''), COUNT(*) "
            "FROM edge_observations GROUP BY 1"
            )
        }
        if link_recovery_counts != contract.link_recovery_counts:
            raise _invalid(
                "LINK_RECOVERY_MISMATCH",
                "manifest link recovery counts differ from SQLite",
            )
        semantic_digest = _database_semantic_digest(connection)
        if semantic_digest != str(manifest.get("semanticDigest") or ""):
            raise _invalid(
                "SEMANTIC_DIGEST_MISMATCH",
                "manifest semantic digest differs from SQLite",
            )
    except EvidenceArtifactInvalid:
        raise
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        raise _invalid("SQLITE_INVALID", "evidence database validation failed") from exc
    return _DatabaseIdentity(
        uasset_path=str(identity["uasset_path"] or ""),
        sources=source_rows,
        counts=actual_counts,
        graph_coverage=graph_coverage,
        link_recovery_counts=link_recovery_counts,
    )


def _public_source_path(value: str) -> str:
    """Mirror the publisher's stable aliasing without exposing the raw path."""

    text = str(value or "").replace("\\", "/")
    if text.startswith("@memory/"):
        return text
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != text
        or (len(text) >= 2 and text[1] == ":")
    ):
        name = PurePosixPath(text).name or "source"
        alias = hashlib.sha256(
            text.encode("utf-8", errors="surrogatepass")
        ).hexdigest()[:16]
        return f"@external/{alias}/{name}"
    return text


def _public_source_entry(entry: _SourceEntry) -> _SourceEntry:
    return _SourceEntry(
        path=_public_source_path(entry.path),
        sha256=entry.sha256,
        size_bytes=entry.size_bytes,
        source_kind=entry.source_kind,
    )


def _source_candidate(
    asset_dir: Path,
    entry: _SourceEntry,
    *,
    uasset_path: str,
) -> tuple[Path | None, bool]:
    """Return ``(candidate, file_backed)`` for a private SQLite source row."""

    kind = entry.source_kind.strip().casefold()
    logical = entry.path.replace("\\", "/")
    if logical.startswith("@memory/") or kind in {
        "in_memory_capture",
        "memory",
        "derived_in_memory",
    }:
        return None, False

    if kind == "package_binary" or logical.startswith("binary/"):
        if not uasset_path:
            return None, True
        base = _absolute_path(uasset_path)
        name = PurePosixPath(logical).name
        return (base if base.name == name else base.with_name(name)), True

    if (
        _WINDOWS_ABSOLUTE_RE.match(logical)
        or logical.startswith("//")
        or logical.startswith("/")
    ):
        return _absolute_path(entry.path), True
    if logical.startswith("@external/"):
        return None, True
    try:
        relative = _safe_relative_posix(logical, "source manifest path")
    except EvidenceArtifactInvalid:
        return None, True
    candidate = _absolute_path(asset_dir.joinpath(*relative.parts))
    _assert_contained(asset_dir, candidate, "source file")
    return candidate, True


def _source_freshness(
    asset_dir: Path,
    identity: _DatabaseIdentity,
) -> str:
    checked_files = 0
    unavailable = False
    for entry in identity.sources:
        candidate, file_backed = _source_candidate(
            asset_dir,
            entry,
            uasset_path=identity.uasset_path,
        )
        if not file_backed:
            continue
        checked_files += 1
        if candidate is None:
            unavailable = True
            continue
        try:
            actual_sha256, actual_bytes, _metadata = _hash_bound_file(
                candidate,
                "evidence source",
                expected_size=entry.size_bytes,
            )
        except FileNotFoundError:
            unavailable = True
            continue
        except PermissionError:
            unavailable = True
            continue
        except EvidenceArtifactInvalid as exc:
            if exc.code == "FILE_SIZE_MISMATCH":
                return STALE
            raise
        if actual_bytes != entry.size_bytes or actual_sha256 != entry.sha256:
            return STALE
    if checked_files == 0 or unavailable:
        return SOURCE_UNAVAILABLE
    return FRESH


def _assert_revision_file_set(revision_dir: Path) -> None:
    try:
        entries = list(os.scandir(revision_dir))
    except OSError as exc:
        raise _invalid("REVISION_UNREADABLE", "revision directory cannot be enumerated") from exc
    names = {entry.name for entry in entries}
    sidecars = {
        "evidence.sqlite-wal",
        "evidence.sqlite-shm",
        "evidence.sqlite-journal",
    }
    if names & sidecars:
        raise _invalid("SQLITE_SIDECAR_PRESENT", "SQLite sidecars are forbidden")
    if names != _REVISION_FILES:
        raise _invalid(
            "REVISION_FILE_SET_INVALID",
            "revision directory must contain exactly the three bound artifacts",
        )
    for entry in entries:
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise _invalid("PATH_UNREADABLE", "revision artifact cannot be inspected") from exc
        if entry.is_symlink() or _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise _invalid(
                "SPECIAL_FILE_REJECTED",
                "revision artifacts must be plain regular files",
            )


def _load_bound_revision(
    asset_dir: Path,
    revision_id: str,
    *,
    expected_manifest_sha256: str | None,
    pointer_sha256: str | None,
    pointer_path: Path | None,
    allow_stale: bool,
) -> ValidatedEvidenceRevision:
    evidence_root = asset_dir / "evidence"
    revisions_root = evidence_root / "revisions"
    revision_dir = revisions_root / revision_id
    _assert_directory(evidence_root, "evidence root")
    _assert_directory(revisions_root, "evidence revisions root")
    _assert_directory(revision_dir, "evidence revision")
    _assert_contained(evidence_root, revision_dir, "evidence revision")
    _assert_revision_file_set(revision_dir)

    manifest_path = revision_dir / "manifest.json"
    manifest_raw = _read_bound_bytes(
        manifest_path,
        "evidence manifest",
        maximum=_MAX_MANIFEST_BYTES,
    )
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    if (
        expected_manifest_sha256 is not None
        and manifest_sha256 != expected_manifest_sha256
    ):
        raise _invalid("MANIFEST_HASH_MISMATCH", "manifest raw SHA-256 differs from its binding")
    manifest = _strict_json_object(manifest_raw, "evidence manifest")
    contract = _validate_manifest_shape(
        manifest,
        revision_id=revision_id,
        revision_dir=revision_dir,
    )

    try:
        database_snapshot = materialize_bound_database_snapshot(
            contract.database_path,
            expected_sha256=contract.database_sha256,
            expected_size=contract.database_bytes,
        )
    except BoundDatabaseError as exc:
        code = (
            "DATABASE_HASH_MISMATCH"
            if exc.code in {"DATABASE_HASH_MISMATCH", "DATABASE_SIZE_MISMATCH"}
            else exc.code
        )
        raise _invalid(code, exc.detail) from exc

    try:
        index_raw = _read_bound_bytes(
            contract.agent_index_path,
            "agent index",
            maximum=_MAX_AGENT_INDEX_BYTES,
        )
        index_sha256 = hashlib.sha256(index_raw).hexdigest()
        index_bytes = len(index_raw)
        if (
            index_sha256 != contract.agent_index_sha256
            or index_bytes != contract.agent_index_bytes
        ):
            raise _invalid(
                "AGENT_INDEX_HASH_MISMATCH",
                "agent index bytes differ from the manifest",
            )
        try:
            index_raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise _invalid("AGENT_INDEX_INVALID", "agent index is not UTF-8") from exc

        connection = database_snapshot.open_connection()
        try:
            identity = _validate_database(
                connection,
                manifest=manifest,
                contract=contract,
            )
        finally:
            connection.close()
    except BoundDatabaseError as exc:
        raise _invalid(exc.code, exc.detail) from exc
    finally:
        database_snapshot.close()
    public_database_sources = tuple(
        sorted((_public_source_entry(row) for row in identity.sources), key=lambda row: row.path)
    )
    if public_database_sources != contract.sources:
        raise _invalid(
            "SOURCE_MANIFEST_MISMATCH",
            "public sourceManifest differs from SQLite",
        )
    freshness_status = _source_freshness(asset_dir, identity)
    if freshness_status == STALE and not allow_stale:
        raise _invalid("STALE_SOURCE", "one or more local evidence sources changed")

    # Catch a sidecar or unexpected file created during SQLite validation.
    _assert_revision_file_set(revision_dir)
    return ValidatedEvidenceRevision(
        paths={
            "asset": asset_dir,
            "evidence": evidence_root,
            "revision": revision_dir,
            "manifest": manifest_path,
            "database": contract.database_path,
            "agent_index": contract.agent_index_path,
            **({"pointer": pointer_path} if pointer_path is not None else {}),
        },
        manifest=manifest,
        manifest_raw=manifest_raw,
        agent_index_raw=index_raw,
        revision_id=revision_id,
        asset_id=str(manifest["assetId"]),
        object_path=str(manifest["objectPath"]),
        manifest_sha256=manifest_sha256,
        pointer_sha256=pointer_sha256,
        freshness_status=freshness_status,
        release_authority=pointer_path is not None and pointer_sha256 is not None,
    )


def load_evidence_revision(
    asset_dir: str | os.PathLike[str],
    revision_id: str,
    allow_stale: bool = False,
    *,
    manifest_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> ValidatedEvidenceRevision:
    """Validate one explicitly addressed immutable v3 revision.

    Explicit revision reads do not require that the revision is current.  If a
    manifest hash is supplied it binds the addressed revision exactly.
    """

    root = _absolute_path(asset_dir)
    _assert_plain_directory_chain(root, "asset directory")
    if not isinstance(revision_id, str) or not _REVISION_ID_RE.fullmatch(revision_id):
        raise _invalid("REVISION_ID_INVALID", "revision id must be 24 lowercase hex characters")
    if manifest_sha256 is not None and expected_manifest_sha256 is not None:
        if manifest_sha256 != expected_manifest_sha256:
            raise _invalid("MANIFEST_HASH_CONFLICT", "conflicting expected manifest hashes")
    expected = (
        manifest_sha256
        if manifest_sha256 is not None
        else expected_manifest_sha256
    )
    if expected is not None and not _SHA256_RE.fullmatch(expected):
        raise _invalid("HASH_INVALID", "expected manifest hash must be lowercase SHA-256")
    return _load_bound_revision(
        root,
        revision_id,
        expected_manifest_sha256=expected,
        pointer_sha256=None,
        pointer_path=None,
        allow_stale=bool(allow_stale),
    )


def load_current_evidence_revision(
    asset_dir: str | os.PathLike[str],
    allow_stale: bool = False,
) -> ValidatedEvidenceRevision:
    """Validate the manifest-bound revision selected by ``evidence/current.json``."""

    root = _absolute_path(asset_dir)
    _assert_plain_directory_chain(root, "asset directory")
    evidence_root = root / "evidence"
    _assert_directory(evidence_root, "evidence root")
    pointer_path = evidence_root / "current.json"
    try:
        pointer_raw = _read_bound_bytes(
            pointer_path,
            "evidence current pointer",
            maximum=_MAX_POINTER_BYTES,
        )
    except FileNotFoundError:
        raise FileNotFoundError("EVIDENCE_CURRENT_POINTER_MISSING") from None
    pointer_sha256 = hashlib.sha256(pointer_raw).hexdigest()
    pointer = _strict_json_object(pointer_raw, "evidence current pointer")
    _reject_public_local_paths(pointer, field="pointer")
    if set(pointer) != _CURRENT_POINTER_KEYS:
        raise _invalid(
            "POINTER_FIELDS_INVALID",
            "evidence current pointer fields must match the v1 contract exactly",
        )
    if pointer.get("schema") != CURRENT_POINTER_SCHEMA:
        raise _invalid("POINTER_SCHEMA_INVALID", "evidence current pointer schema is invalid")
    if pointer.get("mode") != "indexed":
        raise _invalid("POINTER_MODE_INVALID", "evidence current pointer mode must be indexed")
    revision_id = _required_text(pointer, "revisionId", "pointer", maximum=24)
    if not _REVISION_ID_RE.fullmatch(revision_id):
        raise _invalid("REVISION_ID_INVALID", "pointer revision id is invalid")
    manifest_sha256 = _required_sha256(pointer, "manifestSha256", "pointer")
    manifest_relative = _required_text(pointer, "manifest", "pointer", maximum=256)
    expected_relative = f"revisions/{revision_id}/manifest.json"
    if manifest_relative != expected_relative:
        raise _invalid(
            "POINTER_MANIFEST_PATH_INVALID",
            "pointer manifest path does not match its revision",
        )
    _path_from_relative(evidence_root, manifest_relative, "pointer manifest")
    return _load_bound_revision(
        root,
        revision_id,
        expected_manifest_sha256=manifest_sha256,
        pointer_sha256=pointer_sha256,
        pointer_path=pointer_path,
        allow_stale=bool(allow_stale),
    )


__all__ = [
    "CURRENT_POINTER_SCHEMA",
    "EVIDENCE_REVISION_MANIFEST_SCHEMA",
    "EvidenceArtifactInvalid",
    "FRESH",
    "SOURCE_UNAVAILABLE",
    "STALE",
    "ValidatedEvidenceRevision",
    "load_current_evidence_revision",
    "load_evidence_revision",
]
