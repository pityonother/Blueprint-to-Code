"""Publish immutable, manifest-bound Blueprint Evidence revisions.

The v3 pointer is the only mutable authority.  Revision directories are
created on the asset volume, validated in full, renamed once, and never
rewritten.  Compatibility artifacts are refreshed only after the pointer CAS
succeeds and are deliberately not release authority.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final, cast

if os.name == "nt":  # pragma: no cover - selected by platform
    import msvcrt
else:  # pragma: no cover - selected by platform
    import fcntl

from .evidence_schema import (
    EVIDENCE_SCHEMA_USER_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    make_asset_id,
)
from .context_pack import estimate_tokens
from .bound_database import materialize_bound_database_snapshot


CURRENT_SCHEMA: Final = "blueprint-to-code.evidence-current/v1"
MANIFEST_SCHEMA: Final = "blueprint-to-code.evidence-revision-manifest/v3"
PUBLICATION_SCHEMA: Final = "blueprint-translator.evidence-publication.v3"
SEMANTIC_DIGEST_SCHEMA: Final = "blueprint-to-code.evidence-semantic-digest/v1"
_MAX_POINTER_BYTES: Final = 64 * 1024
_MAX_MANIFEST_BYTES: Final = 4 * 1024 * 1024
_LOCK_TIMEOUT_SECONDS: Final = 10.0
_LOCK_RETRY_SECONDS: Final = 0.05
_REPLACE_TIMEOUT_SECONDS: Final = 5.0
_EXPECTED_POINTER_UNSET: Final = object()
_REVISION_ID_RE: Final = re.compile(r"^[0-9a-f]{24}$")
_ASSET_ID_RE: Final = re.compile(r"^[0-9a-f]{24}$")


class EvidencePublicationError(RuntimeError):
    """Base class for publication failures with a stable machine code."""

    code = "EVIDENCE_PUBLICATION_FAILED"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class EvidencePointerConflict(EvidencePublicationError):
    code = "EVIDENCE_POINTER_CONFLICT"


class EvidenceRevisionCollision(EvidencePublicationError):
    code = "EVIDENCE_REVISION_COLLISION"


class EvidencePublicationUncertain(EvidencePublicationError):
    code = "EVIDENCE_PUBLICATION_UNCERTAIN"


@dataclass(frozen=True)
class PublishedEvidenceRevision:
    schema: str
    asset_dir: str
    revision_id: str
    manifest_sha256: str
    pointer_sha256: str
    revision_dir: str
    database_path: str
    agent_index_path: str
    freshness_status: str
    release_authority: bool
    reused_existing: bool
    pointer_updated: bool
    compatibility_copy_status: str = "UPDATED"
    compatibility_error: str | None = None
    pruned_v2: bool = False
    prune_cleanup_status: str = "NOT_REQUESTED"
    prune_cleanup_error: str | None = None
    prune_cleanup_leftovers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
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


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _strict_json(raw: bytes, *, label: str, maximum_bytes: int) -> dict[str, object]:
    if len(raw) > maximum_bytes:
        raise ValueError(f"{label} exceeds its size limit")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400))
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(is_junction()) or bool(attributes & reparse_flag)


def _path_present(path: Path) -> bool:
    """Return True for every directory entry, including broken links."""

    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _lexical_absolute(value: str | os.PathLike[str]) -> Path:
    """Make an absolute path without erasing a symlink/reparse identity."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _require_plain_path_chain(path: Path, *, label: str) -> None:
    components: list[Path] = []
    current = path
    while current != current.parent:
        components.append(current)
        current = current.parent
    for component in reversed(components):
        try:
            component.lstat()
        except FileNotFoundError:
            continue
        if _is_link_or_reparse(component):
            raise ValueError(f"{label} cannot traverse a symlink, junction, or reparse point")


def _require_plain_directory(path: Path, *, label: str) -> None:
    if _is_link_or_reparse(path) or not path.is_dir():
        raise ValueError(f"{label} must be a real directory, not a link or reparse point")


def _require_plain_file(path: Path, *, label: str) -> None:
    if _is_link_or_reparse(path):
        raise ValueError(f"{label} cannot be a link or reparse point")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise FileNotFoundError(path) from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular file")


def _read_bounded(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    _require_plain_file(path, label=label)
    size = path.stat().st_size
    if size > maximum_bytes:
        raise ValueError(f"{label} exceeds its size limit")
    raw = path.read_bytes()
    if len(raw) != size:
        raise ValueError(f"{label} changed while it was read")
    return raw


def _safe_public_source_path(value: object) -> str:
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
        # Never leak a local source path into a public manifest.  The hash still
        # binds the source bytes; this stable alias only removes machine identity.
        name = PurePosixPath(text).name or "source"
        alias = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
        return f"@external/{alias}/{name}"
    return text


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _semantic_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": _sha256_bytes(value)}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("evidence database contains a non-finite number")
    return value


def _semantic_digest(connection: sqlite3.Connection) -> str:
    """Hash logical rows while excluding generation time and machine paths."""

    digest = hashlib.sha256()
    digest.update((SEMANTIC_DIGEST_SCHEMA + "\n").encode("utf-8"))
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
        descriptor = {
            "table": table_name,
            "columns": selected,
            "schema": str(create_sql_raw or ""),
        }
        digest.update(_canonical_json_bytes(descriptor))
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
        if order_columns:
            order_sql = ", ".join(_quote_identifier(name) for name in order_columns)
            query = f"SELECT {select_sql} FROM {_quote_identifier(table_name)} ORDER BY {order_sql}"
        else:
            query = f"SELECT {select_sql} FROM {_quote_identifier(table_name)} ORDER BY rowid"
        for row in connection.execute(query):
            payload = [_semantic_value(value) for value in row]
            digest.update(_canonical_json_bytes(payload))
    return digest.hexdigest()


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if present is None:
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0])


def _database_projection(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> dict[str, object]:
    _require_plain_file(path, label="evidence database")
    for suffix in ("-wal", "-shm"):
        if path.with_name(path.name + suffix).exists():
            raise ValueError(f"evidence database sidecar is forbidden: {path.name}{suffix}")
    snapshot = materialize_bound_database_snapshot(
        path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    )
    connection = snapshot.open_connection()
    snapshot.close()
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if user_version != EVIDENCE_SCHEMA_USER_VERSION:
            raise ValueError(
                f"evidence schema user_version must be {EVIDENCE_SCHEMA_USER_VERSION}, got {user_version}"
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if len(integrity) != 1 or str(integrity[0][0]).casefold() != "ok":
            raise ValueError(f"evidence database integrity check failed: {integrity[:3]}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise ValueError(f"evidence database foreign key check failed: {foreign_keys[:3]}")
        identity_rows = connection.execute(
            "SELECT asset_id, asset_name, object_path, revision_id, source_fingerprint, "
            "parser_version, schema_version, generated_at FROM asset_revisions"
        ).fetchall()
        if len(identity_rows) != 1:
            raise ValueError("evidence database must contain exactly one asset revision")
        identity = identity_rows[0]
        if str(identity["schema_version"]) != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("evidence database schema identity is unsupported")
        sources = [
            {
                "path": _safe_public_source_path(row[0]),
                "sha256": str(row[1]),
                "bytes": int(row[2]),
                "sourceKind": str(row[3] or ""),
            }
            for row in connection.execute(
                "SELECT path, sha256, size_bytes, source_kind "
                "FROM source_manifest ORDER BY path"
            )
        ]
        if not sources:
            raise ValueError("evidence database source manifest is empty")
        counts = {
            "graphs": _table_count(connection, "graphs"),
            "nodes": _table_count(connection, "nodes"),
            "pins": _table_count(connection, "pins"),
            "links": _table_count(connection, "edges"),
            "edgeObservations": _table_count(connection, "edge_observations"),
            "classDefaults": _table_count(connection, "class_defaults"),
            "diagnostics": _table_count(connection, "diagnostics"),
        }
        graph_coverage = {
            str(row[0] or "unknown"): int(row[1])
            for row in connection.execute(
                "SELECT status, COUNT(*) FROM graphs GROUP BY status ORDER BY status"
            )
        }
        link_recovery = {
            str(row[0] or "NOT_RECOVERED"): int(row[1])
            for row in connection.execute(
                "SELECT COALESCE(NULLIF(resolution_status, ''), NULLIF(status, ''), 'NOT_RECOVERED'), COUNT(*) "
                "FROM edge_observations GROUP BY 1 ORDER BY 1"
            )
        }
        semantic_digest = _semantic_digest(connection)
    finally:
        connection.close()
    return {
        "assetId": str(identity["asset_id"]),
        "assetName": str(identity["asset_name"]),
        "objectPath": str(identity["object_path"]),
        "revisionId": str(identity["revision_id"]),
        "sourceFingerprint": str(identity["source_fingerprint"]),
        "parserVersion": str(identity["parser_version"]),
        "evidenceSchemaVersion": str(identity["schema_version"]),
        "generatedAt": str(identity["generated_at"]),
        "sourceManifest": sources,
        "counts": counts,
        "graphCoverage": graph_coverage,
        "linkRecoveryCounts": link_recovery,
        "semanticDigest": semantic_digest,
    }


def _copy_stable(source: Path, destination: Path) -> None:
    _require_plain_path_chain(source, label="publication source")
    _require_plain_file(source, label="publication source")
    before_size = source.stat().st_size
    before_sha = _sha256_file(source)
    shutil.copyfile(source, destination)
    with destination.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    after_size = source.stat().st_size
    after_sha = _sha256_file(source)
    if before_size != after_size or before_sha != after_sha:
        raise ValueError("publication source changed while it was copied")
    if destination.stat().st_size != before_size or _sha256_file(destination) != before_sha:
        raise ValueError("staged publication copy differs from its source")


def _validate_agent_index(path: Path) -> None:
    _require_plain_file(path, label="agent index")
    if path.stat().st_size > 256 * 1024:
        raise ValueError("agent_index.md exceeds its bounded publication limit")
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("agent_index.md must be valid UTF-8") from exc
    tokens = estimate_tokens(text)
    if tokens > 1500:
        raise ValueError(
            f"agent_index.md estimated token count {tokens} exceeds 1500"
        )


def _manifest_payload(
    projection: Mapping[str, object],
    *,
    database_path: Path,
    agent_index_path: Path,
) -> dict[str, object]:
    return {
        "schema": MANIFEST_SCHEMA,
        "assetId": projection["assetId"],
        "objectPath": projection["objectPath"],
        "revisionId": projection["revisionId"],
        "revisionAlgorithm": "source-manifest-parser-schema-sha256-24/v1",
        "parserVersion": projection["parserVersion"],
        "evidenceSchemaVersion": projection["evidenceSchemaVersion"],
        "sourceFingerprint": projection["sourceFingerprint"],
        "sourceManifest": projection["sourceManifest"],
        "artifacts": {
            "database": {
                "path": "evidence.sqlite",
                "bytes": database_path.stat().st_size,
                "sha256": _sha256_file(database_path),
            },
            "agentIndex": {
                "path": "agent_index.md",
                "bytes": agent_index_path.stat().st_size,
                "sha256": _sha256_file(agent_index_path),
            },
        },
        "counts": projection["counts"],
        "graphCoverage": projection["graphCoverage"],
        "linkRecoveryCounts": projection["linkRecoveryCounts"],
        "generatedAt": projection["generatedAt"],
        "semanticDigest": projection["semanticDigest"],
        "semanticDigestSchema": SEMANTIC_DIGEST_SCHEMA,
    }


def _pointer_payload(revision_id: str, manifest_sha256: str) -> dict[str, object]:
    return {
        "schema": CURRENT_SCHEMA,
        "revisionId": revision_id,
        "manifest": f"revisions/{revision_id}/manifest.json",
        "manifestSha256": manifest_sha256,
        "mode": "indexed",
    }


def _read_pointer_raw(evidence_root: Path) -> bytes | None:
    path = evidence_root / "current.json"
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return _read_bounded(path, label="evidence current pointer", maximum_bytes=_MAX_POINTER_BYTES)


def _pointer_sha(raw: bytes | None) -> str | None:
    return None if raw is None else _sha256_bytes(raw)


def _ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)


def _try_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _publication_lock(asset_dir: Path, *, timeout_seconds: float = _LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    lock_path = asset_dir / ".publication.lock"
    if _is_link_or_reparse(lock_path):
        raise ValueError("publication lock cannot be a link or reparse point")
    try:
        before_open = lock_path.lstat()
    except FileNotFoundError:
        before_open = None
    if before_open is not None and (
        not stat.S_ISREG(before_open.st_mode)
        or int(getattr(before_open, "st_nlink", 1)) != 1
    ):
        raise ValueError("publication lock must be one plain regular file")
    deadline = time.monotonic() + timeout_seconds
    with lock_path.open("a+b") as handle:
        opened = os.fstat(handle.fileno())
        try:
            observed = lock_path.lstat()
        except FileNotFoundError as exc:
            raise ValueError("publication lock path disappeared while opening") from exc
        if (
            _is_link_or_reparse(lock_path)
            or not stat.S_ISREG(observed.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or int(getattr(observed, "st_nlink", 1)) != 1
            or int(getattr(opened, "st_nlink", 1)) != 1
        ):
            raise ValueError("publication lock must remain one plain regular file")
        if (int(opened.st_dev), int(opened.st_ino)) != (
            int(observed.st_dev),
            int(observed.st_ino),
        ):
            raise ValueError("publication lock changed while it was opened")
        _ensure_lock_byte(handle)
        opened_after_write = os.fstat(handle.fileno())
        observed_after_write = lock_path.lstat()
        if (
            _is_link_or_reparse(lock_path)
            or not stat.S_ISREG(observed_after_write.st_mode)
            or not stat.S_ISREG(opened_after_write.st_mode)
            or int(getattr(observed_after_write, "st_nlink", 1)) != 1
            or int(getattr(opened_after_write, "st_nlink", 1)) != 1
            or (int(opened_after_write.st_dev), int(opened_after_write.st_ino))
            != (int(observed_after_write.st_dev), int(observed_after_write.st_ino))
        ):
            raise ValueError("publication lock changed while it was initialized")
        while True:
            try:
                _try_lock(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise EvidencePublicationError("timed out acquiring publication lock") from exc
                time.sleep(_LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            try:
                _unlock(handle)
            except OSError:
                pass


@contextlib.contextmanager
def evidence_publication_lock(
    asset_dir: str | Path,
    *,
    timeout_seconds: float = _LOCK_TIMEOUT_SECONDS,
) -> Iterator[Path]:
    """Hold the stable asset-level lock used by evidence/interpretation writers."""

    root = _lexical_absolute(asset_dir)
    _require_plain_path_chain(root, label="asset directory")
    _require_plain_directory(root, label="asset directory")
    with _publication_lock(root, timeout_seconds=timeout_seconds):
        yield root


def _replace_with_retry(source: Path, destination: Path) -> None:
    deadline = time.monotonic() + _REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if not source.exists() or time.monotonic() >= deadline:
                raise EvidencePublicationError(
                    f"atomic replace remained blocked: {destination.name}"
                ) from exc
            time.sleep(_LOCK_RETRY_SECONDS)


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _atomic_write_if_changed(path: Path, raw: bytes) -> bool:
    _require_plain_path_chain(path, label=f"compatibility artifact {path.name}")
    try:
        _require_plain_file(path, label=f"compatibility artifact {path.name}")
    except FileNotFoundError:
        existing = None
    else:
        existing = path.read_bytes() if path.stat().st_size == len(raw) else None
    if existing == raw:
        return False
    _atomic_write(path, raw)
    return True


def _call_fault(fault_injector: Callable[[str], None] | None, checkpoint: str) -> None:
    if fault_injector is not None:
        fault_injector(checkpoint)


def _validate_v2_manifest(
    manifest_path: Path,
    *,
    projection: Mapping[str, object],
    agent_index_path: Path,
    manifest_raw: bytes | None = None,
    agent_index_raw: bytes | None = None,
) -> dict[str, object]:
    raw = (
        manifest_raw
        if manifest_raw is not None
        else _read_bounded(
            manifest_path,
            label="v2 evidence manifest",
            maximum_bytes=_MAX_MANIFEST_BYTES,
        )
    )
    manifest = _strict_json(raw, label="v2 evidence manifest", maximum_bytes=_MAX_MANIFEST_BYTES)
    from .evidence_revision import _reject_public_local_paths

    _reject_public_local_paths(manifest, field="v2Manifest")
    aliases = {
        "asset_id": "assetId",
        "asset_name": "assetName",
        "object_path": "objectPath",
        "revision_id": "revisionId",
        "source_fingerprint": "sourceFingerprint",
        "parser_version": "parserVersion",
    }
    for old_name, projection_name in aliases.items():
        if str(manifest.get(old_name) or "") != str(projection[projection_name]):
            raise ValueError(f"v2 evidence manifest {old_name} differs from the database")
    if str(manifest.get("schema") or "") != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("v2 evidence manifest schema is unsupported")
    if manifest.get("database") != "evidence.sqlite":
        raise ValueError("v2 evidence manifest database path must be evidence.sqlite")
    if manifest.get("agent_index") != "../output/agent_index.md":
        raise ValueError(
            "v2 evidence manifest agent_index path must be ../output/agent_index.md"
        )

    counts = manifest.get("counts")
    if not isinstance(counts, Mapping):
        raise ValueError("v2 evidence manifest counts must be an object")
    count_aliases = {
        "graphs": "graphs",
        "nodes": "nodes",
        "pins": "pins",
        "edges": "links",
        "links": "links",
        "edge_observations": "edgeObservations",
        "edgeObservations": "edgeObservations",
        "class_defaults": "classDefaults",
        "classDefaults": "classDefaults",
        "diagnostics": "diagnostics",
    }
    required_count_groups = (
        ("graphs",),
        ("nodes",),
        ("pins",),
        ("edges", "links"),
    )
    for alternatives in required_count_groups:
        if not any(name in counts for name in alternatives):
            raise ValueError(
                "v2 evidence manifest counts omit " + "/".join(alternatives)
            )
    for name, value in counts.items():
        projection_name = count_aliases.get(str(name))
        if projection_name is None:
            raise ValueError(f"v2 evidence manifest count field is unsupported: {name}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"v2 evidence manifest count {name} is invalid")
        if int(value) != int(cast(Mapping[str, object], projection["counts"])[projection_name]):
            raise ValueError(f"v2 evidence manifest count {name} differs from the database")

    index_raw = (
        agent_index_raw
        if agent_index_raw is not None
        else _read_bounded(
            agent_index_path,
            label="v2 agent index",
            maximum_bytes=256 * 1024,
        )
    )
    try:
        index_text = index_raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("v2 agent index must be valid UTF-8") from exc
    if estimate_tokens(index_text) > 1500:
        raise ValueError("v2 agent index exceeds its bounded token limit")
    if str(projection["revisionId"]) not in index_text:
        raise ValueError("v2 agent index does not identify the database revision")
    return manifest


def _legacy_manifest_bytes(projection: Mapping[str, object]) -> bytes:
    counts = projection.get("counts")
    return _canonical_json_bytes(
        {
            "schema": EVIDENCE_SCHEMA_VERSION,
            "asset_id": projection["assetId"],
            "asset_name": projection.get("assetName", ""),
            "object_path": projection["objectPath"],
            "revision_id": projection["revisionId"],
            "source_fingerprint": projection["sourceFingerprint"],
            "parser_version": projection["parserVersion"],
            "counts": counts,
            "database": "evidence.sqlite",
            "agent_index": "../output/agent_index.md",
            "legacy_artifacts_deleted": False,
        }
    )


def _existing_revision_matches(
    destination: Path,
    *,
    intended_manifest: Mapping[str, object],
) -> tuple[bool, str]:
    _require_plain_directory(destination, label="existing evidence revision")
    actual_names = {item.name for item in destination.iterdir()}
    if actual_names != {"evidence.sqlite", "agent_index.md", "manifest.json"}:
        return False, "existing revision has an unexpected file set"
    raw = _read_bounded(
        destination / "manifest.json",
        label="existing evidence manifest",
        maximum_bytes=_MAX_MANIFEST_BYTES,
    )
    existing = _strict_json(raw, label="existing evidence manifest", maximum_bytes=_MAX_MANIFEST_BYTES)
    if existing.get("schema") != MANIFEST_SCHEMA:
        return False, "existing revision manifest schema differs"
    if existing.get("revisionId") != intended_manifest.get("revisionId"):
        return False, "existing revision identity differs"
    if existing.get("semanticDigest") != intended_manifest.get("semanticDigest"):
        return False, "existing revision semantic digest differs"
    existing_artifacts = existing.get("artifacts")
    intended_artifacts = intended_manifest.get("artifacts")
    if not isinstance(existing_artifacts, Mapping) or not isinstance(intended_artifacts, Mapping):
        return False, "existing revision artifact declarations are invalid"
    existing_index = existing_artifacts.get("agentIndex")
    intended_index = intended_artifacts.get("agentIndex")
    if not isinstance(existing_index, Mapping) or not isinstance(intended_index, Mapping):
        return False, "existing revision agent index declaration is invalid"
    if (
        existing_index.get("sha256") != intended_index.get("sha256")
        or existing_index.get("bytes") != intended_index.get("bytes")
    ):
        return False, "existing revision agent index differs"
    for name, key in (("evidence.sqlite", "database"), ("agent_index.md", "agentIndex")):
        declaration = existing_artifacts.get(key)
        if not isinstance(declaration, Mapping):
            return False, f"existing revision {key} declaration is invalid"
        path = destination / name
        _require_plain_file(path, label=f"existing revision {name}")
        if path.stat().st_size != declaration.get("bytes") or _sha256_file(path) != declaration.get("sha256"):
            return False, f"existing revision {name} bytes do not match its manifest"
    projection = _database_projection(destination / "evidence.sqlite")
    if projection["semanticDigest"] != existing.get("semanticDigest"):
        return False, "existing revision database semantic digest differs"
    return True, _sha256_bytes(raw)


def publish_prepared_evidence_revision(
    *,
    asset_dir: str | Path,
    database_path: str | Path,
    agent_index_path: str | Path | None = None,
    agent_index_bytes: bytes | None = None,
    asset_id: str | None = None,
    object_path: str | None = None,
    expected_pointer_sha256: str | None | object = _EXPECTED_POINTER_UNSET,
    fault_injector: Callable[[str], None] | None = None,
    compatibility_manifest_bytes: bytes | None = None,
) -> PublishedEvidenceRevision:
    """Publish prepared v2 bytes as one immutable v3 revision."""

    root = _lexical_absolute(asset_dir)
    _require_plain_path_chain(root, label="asset directory")
    root.mkdir(parents=True, exist_ok=True)
    _require_plain_directory(root, label="asset directory")
    evidence_root = root / "evidence"
    evidence_root.mkdir(exist_ok=True)
    _require_plain_directory(evidence_root, label="evidence root")
    revisions_root = evidence_root / "revisions"
    revisions_root.mkdir(exist_ok=True)
    _require_plain_directory(revisions_root, label="evidence revisions root")
    from .evidence_revision import EvidenceArtifactInvalid, load_evidence_revision

    source_database = _lexical_absolute(database_path)
    _require_plain_path_chain(source_database, label="evidence database source")
    projection = _database_projection(source_database)
    revision_id = str(projection["revisionId"])
    projected_asset_id = str(projection["assetId"])
    projected_object_path = str(projection["objectPath"])
    if not _REVISION_ID_RE.fullmatch(revision_id):
        raise EvidenceArtifactInvalid(
            "REVISION_ID_INVALID",
            "evidence database revision_id must be 24 lowercase hexadecimal characters",
        )
    if not _ASSET_ID_RE.fullmatch(projected_asset_id):
        raise EvidenceArtifactInvalid(
            "ASSET_ID_INVALID",
            "evidence database asset_id must be 24 lowercase hexadecimal characters",
        )
    if make_asset_id(projected_object_path) != projected_asset_id:
        raise EvidenceArtifactInvalid(
            "SQLITE_ASSET_ID_INVALID",
            "evidence database asset_id is not derived from object_path",
        )
    if asset_id is not None and str(asset_id) != projection["assetId"]:
        raise ValueError("requested asset_id differs from the evidence database")
    if object_path is not None and str(object_path) != projection["objectPath"]:
        raise ValueError("requested object_path differs from the evidence database")
    if (agent_index_path is None) == (agent_index_bytes is None):
        raise ValueError("provide exactly one of agent_index_path or agent_index_bytes")
    source_index = _lexical_absolute(agent_index_path) if agent_index_path is not None else None
    if source_index is not None:
        _require_plain_path_chain(source_index, label="agent index source")

    baseline_raw = _read_pointer_raw(evidence_root)
    baseline_sha = _pointer_sha(baseline_raw)
    if expected_pointer_sha256 is _EXPECTED_POINTER_UNSET:
        expected_sha = baseline_sha
    else:
        if expected_pointer_sha256 is not None and (
            not isinstance(expected_pointer_sha256, str)
            or len(expected_pointer_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_pointer_sha256)
        ):
            raise ValueError("expected_pointer_sha256 must be lowercase hexadecimal SHA-256 or None")
        expected_sha = expected_pointer_sha256

    stage_container = Path(
        tempfile.mkdtemp(prefix=".evidence-v3-stage-", dir=evidence_root)
    )
    validation_asset = stage_container / "asset"
    stage = validation_asset / "evidence" / "revisions" / revision_id
    stage.mkdir(parents=True)
    reused_existing = False
    pointer_updated = False
    revision_dir: Path | None = None
    try:
        staged_database = stage / "evidence.sqlite"
        staged_index = stage / "agent_index.md"
        _copy_stable(source_database, staged_database)
        if source_index is not None:
            _copy_stable(source_index, staged_index)
        else:
            assert agent_index_bytes is not None
            staged_index.write_bytes(agent_index_bytes)
            with staged_index.open("r+b") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        _validate_agent_index(staged_index)
        staged_projection = _database_projection(staged_database)
        if staged_projection["revisionId"] != projection["revisionId"]:
            raise ValueError("staged database revision changed during publication")
        intended_manifest = _manifest_payload(
            staged_projection,
            database_path=staged_database,
            agent_index_path=staged_index,
        )
        manifest_raw = _canonical_json_bytes(intended_manifest)
        (stage / "manifest.json").write_bytes(manifest_raw)
        with (stage / "manifest.json").open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())

        # Run the public reader against a complete shadow layout before the
        # revision can enter the immutable namespace.  The same validation is
        # repeated after rename, but the first pass prevents invalid identity
        # or path data from leaving an orphan/collision behind.
        manifest_sha = _sha256_bytes(manifest_raw)
        load_evidence_revision(
            validation_asset,
            revision_id,
            allow_stale=True,
            manifest_sha256=manifest_sha,
        )
        _call_fault(fault_injector, "after_stage_validated")

        revision_dir = revisions_root / revision_id
        if _path_present(revision_dir):
            matches, existing_manifest_sha = _existing_revision_matches(
                revision_dir,
                intended_manifest=intended_manifest,
            )
            if not matches:
                raise EvidenceRevisionCollision(existing_manifest_sha)
            reused_existing = True
            manifest_sha = existing_manifest_sha
            shutil.rmtree(stage_container)
        else:
            os.replace(stage, revision_dir)

        try:
            load_evidence_revision(
                root,
                revision_id,
                allow_stale=True,
                manifest_sha256=manifest_sha,
            )
        except EvidenceArtifactInvalid as exc:
            if reused_existing:
                raise EvidenceRevisionCollision(
                    f"existing revision failed full validation: {exc}"
                ) from exc
            raise
        _call_fault(fault_injector, "after_revision_rename")

        pointer_raw = _canonical_json_bytes(_pointer_payload(revision_id, manifest_sha))
        pointer_sha256 = _sha256_bytes(pointer_raw)
        with _publication_lock(root):
            observed_raw = _read_pointer_raw(evidence_root)
            observed_sha = _pointer_sha(observed_raw)
            if observed_sha != expected_sha:
                raise EvidencePointerConflict(
                    f"expected pointer SHA {expected_sha!r}, observed {observed_sha!r}"
                )
            if observed_raw == pointer_raw:
                pointer_updated = False
            else:
                _call_fault(fault_injector, "before_pointer_replace")
                _atomic_write(evidence_root / "current.json", pointer_raw)
                pointer_updated = True
            verified_raw = _read_pointer_raw(evidence_root)
            if verified_raw != pointer_raw:
                raise EvidencePublicationUncertain("current pointer did not verify after replace")
        try:
            _call_fault(fault_injector, "after_pointer_replace")
        except Exception as exc:
            raise EvidencePublicationUncertain(
                "current pointer committed before the post-replace failure"
            ) from exc

        # Validate the pointer trust chain before producing any compatibility copy.
        from .evidence_revision import load_current_evidence_revision

        try:
            validated = load_current_evidence_revision(root, allow_stale=True)
        except Exception as exc:
            raise EvidencePublicationUncertain(
                "current pointer committed but its trust chain could not be revalidated"
            ) from exc
        current_still_intended = (
            validated.revision_id == revision_id
            and validated.manifest_sha256 == manifest_sha
            and validated.pointer_sha256 == pointer_sha256
        )
        compatibility_status = (
            "UPDATED" if current_still_intended else "SKIPPED_CURRENT_ADVANCED"
        )
        compatibility_error: str | None = None
        if current_still_intended:
            try:
                with _publication_lock(root):
                    if _read_pointer_raw(evidence_root) != pointer_raw:
                        current_still_intended = False
                        compatibility_status = "SKIPPED_CURRENT_ADVANCED"
                    else:
                        compatibility_database = evidence_root / "evidence.sqlite"
                        compatibility_manifest = evidence_root / "manifest.json"
                        compatibility_index = root / "output" / "agent_index.md"
                        database_artifact = cast(
                            Mapping[str, object],
                            cast(Mapping[str, object], validated.manifest["artifacts"])[
                                "database"
                            ],
                        )
                        database_snapshot = materialize_bound_database_snapshot(
                            revision_dir / "evidence.sqlite",
                            expected_sha256=str(database_artifact["sha256"]),
                            expected_size=int(database_artifact["bytes"]),
                        )
                        try:
                            _atomic_write_if_changed(
                                compatibility_database,
                                database_snapshot.data,
                            )
                        finally:
                            database_snapshot.close()
                        _atomic_write_if_changed(
                            compatibility_manifest,
                            compatibility_manifest_bytes
                            if compatibility_manifest_bytes is not None
                            else _legacy_manifest_bytes(staged_projection),
                        )
                        _atomic_write_if_changed(
                            compatibility_index,
                            validated.agent_index_raw,
                        )
            except Exception as exc:
                compatibility_status = "FAILED_PRESERVED_CURRENT"
                compatibility_error = str(exc)

        try:
            final_current = load_current_evidence_revision(root, allow_stale=True)
        except Exception as exc:
            raise EvidencePublicationUncertain(
                "current pointer could not be revalidated before returning publication status"
            ) from exc
        current_still_intended = (
            final_current.revision_id == revision_id
            and final_current.manifest_sha256 == manifest_sha
            and final_current.pointer_sha256 == pointer_sha256
        )
        final_validated = (
            final_current
            if current_still_intended
            else load_evidence_revision(
                root,
                revision_id,
                allow_stale=True,
                manifest_sha256=manifest_sha,
            )
        )
        return PublishedEvidenceRevision(
            schema=PUBLICATION_SCHEMA,
            asset_dir=str(root),
            revision_id=revision_id,
            manifest_sha256=manifest_sha,
            pointer_sha256=pointer_sha256,
            revision_dir=str(revision_dir),
            database_path=str(revision_dir / "evidence.sqlite"),
            agent_index_path=str(revision_dir / "agent_index.md"),
            freshness_status=final_validated.freshness_status,
            release_authority=(
                current_still_intended and validated.release_authority
            ),
            reused_existing=reused_existing,
            pointer_updated=pointer_updated,
            compatibility_copy_status=compatibility_status,
            compatibility_error=compatibility_error,
        )
    finally:
        if stage_container.exists():
            shutil.rmtree(stage_container, ignore_errors=True)


def publish_v2_evidence_revision(
    *,
    asset_dir: str | Path,
    expected_pointer_sha256: str | None | object = _EXPECTED_POINTER_UNSET,
    fault_injector: Callable[[str], None] | None = None,
) -> PublishedEvidenceRevision:
    root = _lexical_absolute(asset_dir)
    _require_plain_path_chain(root, label="asset directory")
    database = root / "evidence" / "evidence.sqlite"
    manifest = root / "evidence" / "manifest.json"
    index = root / "output" / "agent_index.md"
    snapshot_root = Path(
        tempfile.mkdtemp(prefix=".v2-publication-snapshot-", dir=root)
    )
    try:
        snapshot_database = snapshot_root / "evidence.sqlite"
        snapshot_manifest = snapshot_root / "manifest.json"
        snapshot_index = snapshot_root / "agent_index.md"
        # The flat v2 trio is mutable compatibility state.  Snapshot all three
        # under the shared publication lock, then validate and publish only the
        # snapshot.  A concurrent writer can advance the flat layout after the
        # lock is released, but it cannot make this publication mix generations.
        with _publication_lock(root):
            _copy_stable(database, snapshot_database)
            _copy_stable(manifest, snapshot_manifest)
            _copy_stable(index, snapshot_index)
        projection = _database_projection(snapshot_database)
        _validate_v2_manifest(
            snapshot_manifest,
            projection=projection,
            agent_index_path=snapshot_index,
        )
        manifest_raw = _read_bounded(
            snapshot_manifest,
            label="v2 evidence manifest snapshot",
            maximum_bytes=_MAX_MANIFEST_BYTES,
        )
        return publish_prepared_evidence_revision(
            asset_dir=root,
            database_path=snapshot_database,
            agent_index_path=snapshot_index,
            expected_pointer_sha256=expected_pointer_sha256,
            fault_injector=fault_injector,
            compatibility_manifest_bytes=manifest_raw,
        )
    finally:
        shutil.rmtree(snapshot_root, ignore_errors=True)


def _validate_v2_prune_path(
    root: Path,
    path: Path,
    *,
    require_file: bool,
) -> Path:
    asset_root = _lexical_absolute(root)
    target = _lexical_absolute(path)
    _require_plain_path_chain(asset_root, label="asset directory")
    _require_plain_directory(asset_root, label="asset directory")
    try:
        relative = target.relative_to(asset_root)
    except ValueError as exc:
        raise ValueError("v2 prune target escapes the asset directory") from exc
    if not relative.parts:
        raise ValueError("v2 prune target cannot be the asset directory")
    _require_plain_path_chain(target, label="v2 prune target")
    _require_plain_directory(target.parent, label="v2 prune parent")
    if require_file:
        _require_plain_file(target, label="v2 compatibility artifact")
        if int(getattr(target.lstat(), "st_nlink", 1)) != 1:
            raise ValueError("v2 compatibility artifact must not be hard-linked")
    return target


def _prune_v2_exact(root: Path) -> tuple[str, str | None, tuple[str, ...]]:
    root = _lexical_absolute(root)
    _require_plain_path_chain(root, label="asset directory")
    _require_plain_directory(root, label="asset directory")
    targets = (
        root / "evidence" / "evidence.sqlite",
        root / "evidence" / "manifest.json",
        root / "output" / "agent_index.md",
    )
    for target in targets:
        if _path_present(target):
            _validate_v2_prune_path(root, target, require_file=True)
    moved: list[tuple[Path, Path]] = []
    operation_id = uuid.uuid4().hex
    try:
        for target in targets:
            if not _path_present(target):
                continue
            quarantine = target.with_name(
                f".{target.name}.v2-prune-{operation_id}.tmp"
            )
            _validate_v2_prune_path(root, target, require_file=True)
            _validate_v2_prune_path(root, quarantine, require_file=False)
            if _path_present(quarantine):
                raise ValueError("v2 prune quarantine path already exists")
            os.replace(target, quarantine)
            moved.append((target, quarantine))
    except Exception:
        rollback_errors: list[str] = []
        for target, quarantine in reversed(moved):
            try:
                if _path_present(quarantine) and not _path_present(target):
                    _validate_v2_prune_path(
                        root,
                        quarantine,
                        require_file=True,
                    )
                    _validate_v2_prune_path(
                        root,
                        target,
                        require_file=False,
                    )
                    os.replace(quarantine, target)
            except (OSError, ValueError) as rollback_error:
                rollback_errors.append(f"{target}: {rollback_error}")
        if rollback_errors:
            raise EvidencePublicationUncertain(
                "v2 prune preflight failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            )
        raise
    cleanup_errors: list[str] = []
    for _target, quarantine in moved:
        try:
            _validate_v2_prune_path(
                root,
                quarantine,
                require_file=True,
            )
            quarantine.unlink()
        except (OSError, ValueError) as exc:
            cleanup_errors.append(f"{quarantine.name}: {exc}")
    leftovers = tuple(
        quarantine.relative_to(root).as_posix()
        for _target, quarantine in moved
        if _path_present(quarantine)
    )
    if leftovers:
        return (
            "PENDING",
            "; ".join(cleanup_errors) or "one or more prune quarantines remain",
            leftovers,
        )
    return "COMPLETE", None, ()


def migrate_v2_evidence_to_v3(
    asset_dir: str | Path,
    *,
    prune_v2: bool = False,
) -> PublishedEvidenceRevision:
    root = _lexical_absolute(asset_dir)
    _require_plain_path_chain(root, label="asset directory")
    published = publish_v2_evidence_revision(asset_dir=root)
    cleanup_status = "NOT_REQUESTED"
    cleanup_error: str | None = None
    cleanup_leftovers: tuple[str, ...] = ()
    if prune_v2:
        # Revalidate the exact current authority before deleting compatibility files.
        from .evidence_revision import load_current_evidence_revision

        with _publication_lock(root):
            validated = load_current_evidence_revision(root, allow_stale=True)
            if (
                validated.revision_id != published.revision_id
                or validated.manifest_sha256 != published.manifest_sha256
            ):
                raise EvidencePublicationUncertain("current revision changed before v2 pruning")
            cleanup_status, cleanup_error, cleanup_leftovers = _prune_v2_exact(root)
    return PublishedEvidenceRevision(
        **{
            **asdict(published),
            "pruned_v2": bool(prune_v2),
            "prune_cleanup_status": cleanup_status,
            "prune_cleanup_error": cleanup_error,
            "prune_cleanup_leftovers": cleanup_leftovers,
        }
    )


__all__ = [
    "CURRENT_SCHEMA",
    "MANIFEST_SCHEMA",
    "EvidencePointerConflict",
    "EvidencePublicationError",
    "EvidencePublicationUncertain",
    "EvidenceRevisionCollision",
    "PublishedEvidenceRevision",
    "evidence_publication_lock",
    "migrate_v2_evidence_to_v3",
    "publish_prepared_evidence_revision",
    "publish_v2_evidence_revision",
]
