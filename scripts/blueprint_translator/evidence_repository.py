"""Repository boundary for indexed and legacy Blueprint evidence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import zlib
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .evidence_query import EvidenceQueryService
from .evidence_publication import (
    _database_projection,
    _lexical_absolute,
    _require_plain_path_chain,
    _validate_v2_manifest,
)
from .evidence_revision import load_current_evidence_revision
from .evidence_values import project_default_value
from .evidence_writer import write_evidence_store_from_capture


@dataclass(frozen=True)
class ResolvedEvidenceState:
    asset_dir: Path
    database_path: Path
    agent_index_path: Path
    manifest_path: Path
    pointer_path: Path | None
    source_kind: str
    release_authority: bool
    freshness_status: str
    migration_required: bool
    manifest_sha256: str | None
    pointer_sha256: str | None
    database_sha256: str
    database_bytes: int
    manifest_content_sha256: str
    manifest_bytes: int
    manifest_raw: bytes
    agent_index_sha256: str
    agent_index_bytes: int
    agent_index_raw: bytes


def evidence_state_metadata(
    state: ResolvedEvidenceState,
) -> dict[str, object]:
    """Project the six publication-trust fields used by public consumers."""

    return {
        "sourceKind": state.source_kind,
        "freshnessStatus": state.freshness_status,
        "releaseAuthority": state.release_authority,
        "migrationRequired": state.migration_required,
        "manifestSha256": state.manifest_sha256,
        "pointerSha256": state.pointer_sha256,
    }


class EvidenceRepository:
    """Own a read-only query service and any temporary legacy projection."""

    def __init__(
        self,
        service: EvidenceQueryService,
        *,
        source_kind: str,
        release_authority: bool,
        freshness_status: str,
        migration_required: bool,
        manifest_sha256: str | None = None,
        pointer_sha256: str | None = None,
        state: ResolvedEvidenceState | None = None,
        temporary: tempfile.TemporaryDirectory[str] | None = None,
    ) -> None:
        self._service = service
        self._temporary = temporary
        self.source_kind = source_kind
        self.release_authority = bool(release_authority)
        self.freshness_status = freshness_status
        self.migration_required = bool(migration_required)
        self.manifest_sha256 = manifest_sha256
        self.pointer_sha256 = pointer_sha256
        self.agent_index_path = state.agent_index_path if state is not None else None
        self.manifest_path = state.manifest_path if state is not None else None
        self.pointer_path = state.pointer_path if state is not None else None
        self.manifest_payload = (
            _decode_bound_manifest(state) if state is not None else None
        )
        self.agent_index_text = (
            _decode_bound_agent_index(state) if state is not None else None
        )
        self.metadata = {
            "source_kind": self.source_kind,
            "release_authority": self.release_authority,
            "freshness_status": self.freshness_status,
            "migration_required": self.migration_required,
            "manifest_sha256": self.manifest_sha256,
            "pointer_sha256": self.pointer_sha256,
        }
        self.database_path = service.database_path
        self.asset_id = service.asset_id
        self.revision_id = service.revision_id
        self._closed = False

    def query(self, request: Mapping[str, object]) -> dict[str, object]:
        if self._closed:
            raise RuntimeError("EvidenceRepository is closed")
        return self._service.query(request)

    def identity(self) -> dict[str, object]:
        row = self._service._connection.execute(  # noqa: SLF001 - repository owns the service
            "SELECT asset_id, asset_name, object_path, revision_id, source_fingerprint, uasset_path "
            "FROM asset_revisions LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("evidence database has no asset revision")
        return {key: row[key] for key in row.keys()}

    def graph_summaries(self) -> list[dict[str, object]]:
        rows = self._service._connection.execute(  # noqa: SLF001
            "SELECT graph_ref, export_index, name, graph_type, status, confidence, node_count, pin_count, "
            "link_observation_count, coverage_json FROM graphs ORDER BY export_index"
        ).fetchall()
        return [
            {
                "ref": str(row["graph_ref"]),
                "export_index": int(row["export_index"]),
                "name": str(row["name"]),
                "graph_type": str(row["graph_type"]),
                "status": str(row["status"]),
                "confidence": str(row["confidence"]),
                "node_count": int(row["node_count"]),
                "pin_count": int(row["pin_count"]),
                "link_count": int(row["link_observation_count"]),
                "coverage": json.loads(str(row["coverage_json"] or "{}")),
            }
            for row in rows
        ]

    def node_summaries(self) -> list[dict[str, object]]:
        rows = self._service._connection.execute(  # noqa: SLF001
            "SELECT node_ref, graph_ref, name, label, class_name, node_type, function_name, variable_name, "
            "event_name, x, y, confidence FROM nodes ORDER BY graph_ref, local_index"
        ).fetchall()
        return [
            {
                "ref": str(row["node_ref"]),
                "graph_ref": str(row["graph_ref"]),
                "name": str(row["name"]),
                "label": str(row["label"]),
                "class_name": str(row["class_name"]),
                "node_type": str(row["node_type"]),
                "function": str(row["function_name"]),
                "variable": str(row["variable_name"]),
                "event": str(row["event_name"]),
                "x": row["x"],
                "y": row["y"],
                "confidence": str(row["confidence"]),
            }
            for row in rows
        ]

    @staticmethod
    def _decode_value(row: Any) -> object:
        codec = str(row["value_codec"] or "json")
        if codec == "json":
            return json.loads(str(row["value_json"]))
        if codec == "zlib-json-utf8":
            return json.loads(zlib.decompress(bytes(row["value_blob"])).decode("utf-8"))
        raise ValueError(f"unsupported evidence value codec: {codec}")

    def default_summaries(self, *, include_values: bool = True) -> list[dict[str, object]]:
        rows = self._service._connection.execute(  # noqa: SLF001
            "SELECT default_ref, name, type_name, value_json, value_codec, value_blob, confidence, source, extra_json "
            "FROM class_defaults ORDER BY name"
        ).fetchall()
        summaries: list[dict[str, object]] = []
        for row in rows:
            value_loaded = include_values or str(row["value_codec"] or "json") == "json"
            if include_values:
                value = self._decode_value(row)
            elif value_loaded:
                try:
                    value = json.loads(str(row["value_json"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    value = None
            else:
                value = None
            try:
                extra = json.loads(str(row["extra_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                extra = {}
            summaries.append(
                {
                    "ref": str(row["default_ref"]),
                    "name": str(row["name"]),
                    "type": str(row["type_name"]),
                    "confidence": str(row["confidence"]),
                    "source": str(row["source"]),
                    **project_default_value(
                        str(row["type_name"]),
                        value,
                        extra,
                        value_loaded=value_loaded,
                    ),
                    **({"value": value} if include_values else {}),
                }
            )
        return summaries

    @staticmethod
    def _gap_summary_row(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "ref": str(row.get("ref") or ""),
            "scope_kind": str(row.get("scopeKind") or ""),
            "scope_ref": str(row.get("scopeRef") or row.get("graphRef") or ""),
            "name": str(row.get("name") or ""),
            "status": str(row.get("status") or ""),
            "reason_code": str(row.get("reasonCode") or ""),
            "detail": str(row.get("detail") or row.get("title") or ""),
            "next_probe": str(row.get("nextProbe") or ""),
            "kind": str(row.get("kind") or "diagnostic"),
        }

    def _all_gap_summary_rows(self) -> list[dict[str, object]]:
        """Materialize each gap once for accurate aggregate coverage.

        Query pagination intentionally limits response size, but repeatedly
        calling the public gaps query would rebuild the full gap set for every
        page.  The repository owns the query service, so it uses the same item
        projectors directly and aggregates before applying its downstream cap.
        """

        diagnostic_rows = self._service._connection.execute(  # noqa: SLF001
            "SELECT * FROM diagnostics WHERE revision_id = ?",
            (self.revision_id,),
        ).fetchall()
        raw_rows: list[Mapping[str, object]] = [
            self._service._diagnostic_item(row)  # noqa: SLF001
            for row in diagnostic_rows
        ]
        raw_rows.extend(self._service._observation_gap_items())  # noqa: SLF001
        raw_rows.extend(self._service._default_value_gap_items())  # noqa: SLF001
        summaries = [self._gap_summary_row(row) for row in raw_rows]
        summaries.sort(
            key=lambda row: (
                str(row.get("status") or ""),
                str(row.get("reason_code") or ""),
                str(row.get("ref") or ""),
            )
        )
        return summaries

    def gap_summary(
        self,
        *,
        limit: int = 200,
        example_limit: int = 3,
    ) -> dict[str, object]:
        """Return bounded rows plus loss-aware aggregates for every gap."""

        bounded_limit = max(0, int(limit))
        bounded_example_limit = max(0, int(example_limit))
        summaries = self._all_gap_summary_rows()
        returned_rows = summaries[:bounded_limit]
        by_status: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        groups: dict[tuple[str, str], dict[str, object]] = {}
        for row in summaries:
            status = str(row.get("status") or "")
            reason = str(row.get("reason_code") or "")
            by_status[status] = by_status.get(status, 0) + 1
            by_reason[reason] = by_reason.get(reason, 0) + 1
            key = (status, reason)
            group = groups.setdefault(
                key,
                {
                    "status": status,
                    "reason_code": reason,
                    "count": 0,
                    "examples": [],
                },
            )
            group["count"] = int(group["count"]) + 1
            examples = group["examples"]
            if isinstance(examples, list) and len(examples) < bounded_example_limit:
                examples.append(dict(row))

        total = len(summaries)
        returned = len(returned_rows)
        omitted = max(0, total - returned)
        return {
            "items": returned_rows,
            "total": total,
            "returned": returned,
            "omitted": omitted,
            "truncated": omitted > 0,
            "by_status": dict(sorted(by_status.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "groups": [groups[key] for key in sorted(groups)],
        }

    def gap_summaries(self, *, limit: int = 200) -> list[dict[str, object]]:
        """Compatibility view of bounded gap rows; use ``gap_summary`` for coverage."""

        if limit <= 0:
            return []
        projection = self.gap_summary(limit=limit)
        items = projection.get("items")
        return items if isinstance(items, list) else []

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._service.close()
        finally:
            if self._temporary is not None:
                self._temporary.cleanup()
            self._closed = True

    def __enter__(self) -> "EvidenceRepository":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(getattr(metadata, "st_nlink", 1)),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _read_bound_file_bytes(
    path: Path,
    *,
    label: str,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    maximum_size: int | None = None,
) -> bytes:
    """Read exact bytes from one stable, plain file descriptor."""

    _require_plain_path_chain(path, label=label)
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(path) from exc
    if not stat.S_ISREG(before.st_mode) or int(getattr(before, "st_nlink", 1)) != 1:
        raise ValueError(f"{label} must remain one plain regular file")
    size = int(before.st_size)
    if expected_size is not None and size != int(expected_size):
        raise ValueError(f"{label} size drifted from its binding")
    if maximum_size is not None and size > int(maximum_size):
        raise ValueError(f"{label} exceeds its bounded size limit")

    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise ValueError(f"{label} changed while its descriptor was opened")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            digest.update(chunk)
            copied += len(chunk)
        opened_after = os.fstat(descriptor)
        _require_plain_path_chain(path, label=label)
        after = path.lstat()
        if (
            int(getattr(opened_after, "st_nlink", 1)) != 1
            or not stat.S_ISREG(after.st_mode)
            or int(getattr(after, "st_nlink", 1)) != 1
        ):
            raise ValueError(f"{label} must remain one plain regular file")
        if (
            _file_identity(opened) != _file_identity(opened_after)
            or _file_identity(before) != _file_identity(after)
            or copied != size
        ):
            raise ValueError(f"{label} changed while its bytes were read")
        observed_sha256 = digest.hexdigest()
        if expected_sha256 is not None and observed_sha256 != expected_sha256:
            raise ValueError(f"{label} hash drifted from its binding")
        return b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _stable_file_binding(path: Path, *, label: str) -> tuple[str, int]:
    raw = _read_bound_file_bytes(path, label=label)
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _decode_bound_manifest(state: ResolvedEvidenceState) -> dict[str, Any]:
    if len(state.manifest_raw) != state.manifest_bytes:
        raise ValueError("resolved evidence manifest size binding is invalid")
    if hashlib.sha256(state.manifest_raw).hexdigest() != state.manifest_content_sha256:
        raise ValueError("resolved evidence manifest hash binding is invalid")
    try:
        payload = json.loads(state.manifest_raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("resolved evidence manifest is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("resolved evidence manifest must be one JSON object")
    return payload


def _decode_bound_agent_index(state: ResolvedEvidenceState) -> str:
    if len(state.agent_index_raw) != state.agent_index_bytes:
        raise ValueError("resolved agent index size binding is invalid")
    if hashlib.sha256(state.agent_index_raw).hexdigest() != state.agent_index_sha256:
        raise ValueError("resolved agent index hash binding is invalid")
    try:
        return state.agent_index_raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("resolved agent index is not valid UTF-8") from exc


def evidence_manifest_payload(state: ResolvedEvidenceState) -> dict[str, Any]:
    """Return the manifest JSON captured from the resolved generation."""

    return _decode_bound_manifest(state)


def evidence_agent_index_text(state: ResolvedEvidenceState) -> str:
    """Return the UTF-8 agent index captured from the resolved generation."""

    return _decode_bound_agent_index(state)


def _verify_resolved_database_binding(state: ResolvedEvidenceState) -> None:
    """Revalidate the database bytes represented by a resolved state.

    Resolution binds a v3 manifest (or a validated v2 compatibility
    manifest) to exact database bytes.  Consumers that subsequently open the
    database must retain that binding instead of trusting the path a second
    time.  The identity checks also reject a replacement during hashing.
    """

    observed_sha256, observed_bytes = _stable_file_binding(
        state.database_path,
        label="resolved evidence database",
    )
    if observed_bytes != state.database_bytes:
        raise ValueError("resolved evidence database size drifted from its manifest binding")
    if observed_sha256 != state.database_sha256:
        raise ValueError("resolved evidence database hash drifted from its manifest binding")


@contextmanager
def open_bound_evidence_database(
    state: ResolvedEvidenceState,
) -> Iterator[sqlite3.Connection]:
    """Open one resolved Evidence database read-only and keep it byte-bound.

    ``EvidenceQueryService.open`` performs the pre-open and post-open identity,
    size, and SHA-256 checks and enables SQLite ``query_only``.  This wrapper
    additionally revalidates while the handle is open and immediately after
    close so direct-SQL consumers cannot silently drift away from the state
    returned by the resolver.
    """

    service = EvidenceQueryService.open(
        state.database_path,
        expected_sha256=state.database_sha256,
        expected_size=state.database_bytes,
    )
    try:
        yield service._connection  # noqa: SLF001 - helper owns service lifetime
    finally:
        try:
            _verify_resolved_database_binding(state)
        finally:
            service.close()
            _verify_resolved_database_binding(state)


def _v2_freshness(
    database_path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> str:
    service = EvidenceQueryService.open(
        database_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    )
    connection = service._connection  # noqa: SLF001 - helper owns service lifetime
    try:
        identity = connection.execute(
            "SELECT uasset_path FROM asset_revisions LIMIT 1"
        ).fetchone()
        uasset_path = Path(str(identity[0])).expanduser() if identity and identity[0] else None
        sources = connection.execute(
            "SELECT path, sha256, size_bytes FROM source_manifest ORDER BY path"
        ).fetchall()
    finally:
        service.close()
    verified = 0
    unavailable = 0
    for logical_path, expected_sha, expected_size in sources:
        logical = str(logical_path).replace("\\", "/")
        if not logical.startswith("binary/"):
            continue
        if uasset_path is None:
            unavailable += 1
            continue
        suffix = Path(logical).suffix
        candidate = uasset_path if suffix == uasset_path.suffix else uasset_path.with_suffix(suffix)
        if not candidate.is_file():
            unavailable += 1
            continue
        if candidate.stat().st_size != int(expected_size) or _sha256_file(candidate) != str(expected_sha):
            return "STALE"
        verified += 1
    if verified:
        return "FRESH"
    return "SOURCE_UNAVAILABLE" if unavailable or sources else "SOURCE_UNAVAILABLE"


def resolve_asset_evidence_state(
    asset_dir: str | Path,
    *,
    allow_stale: bool = False,
) -> ResolvedEvidenceState:
    """Resolve validated indexed evidence without opening a query connection."""

    root = _lexical_absolute(asset_dir)
    _require_plain_path_chain(root, label="asset directory")
    if not root.is_dir():
        raise FileNotFoundError(f"NO_EVIDENCE: asset directory not found: {root}")
    current_pointer = root / "evidence" / "current.json"
    try:
        current_pointer.lstat()
    except FileNotFoundError:
        has_current_pointer = False
    else:
        has_current_pointer = True
    if has_current_pointer:
        validated = load_current_evidence_revision(root, allow_stale=allow_stale)
        artifacts = validated.manifest["artifacts"]
        database_artifact = artifacts["database"]
        index_artifact = artifacts["agentIndex"]
        database_sha256, database_bytes = _stable_file_binding(
            validated.database_path,
            label="v3 evidence database",
        )
        manifest_raw = validated.manifest_raw
        manifest_content_sha256 = hashlib.sha256(manifest_raw).hexdigest()
        manifest_bytes = len(manifest_raw)
        agent_index_raw = validated.agent_index_raw
        agent_index_sha256 = hashlib.sha256(agent_index_raw).hexdigest()
        agent_index_bytes = len(agent_index_raw)
        if (
            database_sha256 != str(database_artifact["sha256"])
            or database_bytes != int(database_artifact["bytes"])
            or manifest_content_sha256 != validated.manifest_sha256
            or agent_index_sha256 != str(index_artifact["sha256"])
            or agent_index_bytes != int(index_artifact["bytes"])
        ):
            raise ValueError("v3 evidence bytes changed after reader validation")
        return ResolvedEvidenceState(
            asset_dir=root,
            database_path=validated.database_path,
            agent_index_path=validated.agent_index_path,
            manifest_path=validated.manifest_path,
            pointer_path=validated.pointer_path,
            source_kind="INDEXED_V3_CURRENT",
            release_authority=validated.release_authority,
            freshness_status=validated.freshness_status,
            migration_required=False,
            manifest_sha256=validated.manifest_sha256,
            pointer_sha256=validated.pointer_sha256,
            database_sha256=database_sha256,
            database_bytes=database_bytes,
            manifest_content_sha256=manifest_content_sha256,
            manifest_bytes=manifest_bytes,
            manifest_raw=manifest_raw,
            agent_index_sha256=agent_index_sha256,
            agent_index_bytes=agent_index_bytes,
            agent_index_raw=agent_index_raw,
        )

    indexed_database = root / "evidence" / "evidence.sqlite"
    try:
        indexed_database.lstat()
    except FileNotFoundError:
        has_v2_database = False
    else:
        has_v2_database = True
    if has_v2_database:
        v2_manifest = root / "evidence" / "manifest.json"
        v2_index = root / "output" / "agent_index.md"
        for path, label in (
            (indexed_database, "v2 evidence database"),
            (v2_manifest, "v2 evidence manifest"),
            (v2_index, "v2 agent index"),
        ):
            _require_plain_path_chain(path, label=label)
        database_binding = _stable_file_binding(
            indexed_database,
            label="v2 evidence database",
        )
        manifest_raw = _read_bound_file_bytes(
            v2_manifest,
            label="v2 evidence manifest",
            maximum_size=1024 * 1024,
        )
        agent_index_raw = _read_bound_file_bytes(
            v2_index,
            label="v2 agent index",
            maximum_size=256 * 1024,
        )
        bindings_before = {
            "database": database_binding,
            "manifest": (hashlib.sha256(manifest_raw).hexdigest(), len(manifest_raw)),
            "agent_index": (
                hashlib.sha256(agent_index_raw).hexdigest(),
                len(agent_index_raw),
            ),
        }
        projection = _database_projection(
            indexed_database,
            expected_sha256=database_binding[0],
            expected_size=database_binding[1],
        )
        _validate_v2_manifest(
            v2_manifest,
            projection=projection,
            agent_index_path=v2_index,
            manifest_raw=manifest_raw,
            agent_index_raw=agent_index_raw,
        )
        freshness = _v2_freshness(
            indexed_database,
            expected_sha256=database_binding[0],
            expected_size=database_binding[1],
        )
        if freshness == "STALE" and not allow_stale:
            raise ValueError("STALE_EVIDENCE_SOURCE: v2 evidence source bytes changed")
        database_sha256, database_bytes = _stable_file_binding(
            indexed_database,
            label="v2 evidence database",
        )
        manifest_raw_after = _read_bound_file_bytes(
            v2_manifest,
            label="v2 evidence manifest",
            maximum_size=1024 * 1024,
        )
        manifest_content_sha256 = hashlib.sha256(manifest_raw_after).hexdigest()
        manifest_bytes = len(manifest_raw_after)
        agent_index_raw_after = _read_bound_file_bytes(
            v2_index,
            label="v2 agent index",
            maximum_size=256 * 1024,
        )
        agent_index_sha256 = hashlib.sha256(agent_index_raw_after).hexdigest()
        agent_index_bytes = len(agent_index_raw_after)
        bindings_after = {
            "database": (database_sha256, database_bytes),
            "manifest": (manifest_content_sha256, manifest_bytes),
            "agent_index": (agent_index_sha256, agent_index_bytes),
        }
        if bindings_before != bindings_after:
            raise ValueError("v2 evidence artifacts changed while they were validated")
        return ResolvedEvidenceState(
            asset_dir=root,
            database_path=indexed_database,
            agent_index_path=v2_index,
            manifest_path=v2_manifest,
            pointer_path=None,
            source_kind="INDEXED_V2_COMPATIBILITY",
            release_authority=False,
            freshness_status=freshness,
            migration_required=True,
            manifest_sha256=None,
            pointer_sha256=None,
            database_sha256=database_sha256,
            database_bytes=database_bytes,
            manifest_content_sha256=manifest_content_sha256,
            manifest_bytes=manifest_bytes,
            manifest_raw=manifest_raw_after,
            agent_index_sha256=agent_index_sha256,
            agent_index_bytes=agent_index_bytes,
            agent_index_raw=agent_index_raw_after,
        )

    raise FileNotFoundError(f"NO_EVIDENCE: indexed evidence not found under {root}")


def open_asset_repository(
    asset_dir: str | Path,
    *,
    allow_legacy_fallback: bool = False,
    allow_stale: bool = False,
) -> EvidenceRepository:
    """Open validated v3, compatibility v2, or an explicitly allowed legacy view.

    The legacy projection never writes into ``asset_dir`` and is never release
    authority.  A present but damaged v3 pointer fails closed rather than
    silently falling back to another evidence generation.
    """

    root = _lexical_absolute(asset_dir)
    _require_plain_path_chain(root, label="asset directory")
    current_path = root / "evidence" / "current.json"
    indexed_path = root / "evidence" / "evidence.sqlite"
    try:
        current_path.lstat()
    except FileNotFoundError:
        current_declared = False
    else:
        current_declared = True
    try:
        indexed_path.lstat()
    except FileNotFoundError:
        v2_declared = False
    else:
        v2_declared = True
    try:
        state = resolve_asset_evidence_state(root, allow_stale=allow_stale)
    except FileNotFoundError:
        if current_declared or v2_declared:
            raise
        state = None
    if state is not None:
        return open_resolved_asset_repository(state)

    legacy_manifest = root / "graphs_from_uasset_manifest.json"
    if not legacy_manifest.is_file() or not allow_legacy_fallback:
        raise FileNotFoundError(f"NO_EVIDENCE: evidence not found under {root}")
    temporary = tempfile.TemporaryDirectory(prefix="blueprint-evidence-legacy-")
    try:
        database_path = Path(temporary.name) / "evidence.sqlite"
        write_evidence_store_from_capture(root, database_path)
        service = EvidenceQueryService.open(database_path)
    except Exception:
        temporary.cleanup()
        raise
    return EvidenceRepository(
        service,
        source_kind="LEGACY_TEMPORARY_PROJECTION",
        release_authority=False,
        freshness_status="FRESH",
        migration_required=True,
        temporary=temporary,
    )


def open_resolved_asset_repository(
    state: ResolvedEvidenceState,
) -> EvidenceRepository:
    """Open exactly the immutable evidence generation represented by ``state``.

    Public consumers that need both publication metadata and queries must
    resolve once, then pass that same state here. Re-resolving through
    :func:`open_asset_repository` could observe a newer ``current.json`` and
    combine metadata from one revision with query results from another.
    """

    return EvidenceRepository(
        EvidenceQueryService.open(
            state.database_path,
            expected_sha256=state.database_sha256,
            expected_size=state.database_bytes,
        ),
        source_kind=state.source_kind,
        release_authority=state.release_authority,
        freshness_status=state.freshness_status,
        migration_required=state.migration_required,
        manifest_sha256=state.manifest_sha256,
        pointer_sha256=state.pointer_sha256,
        state=state,
    )


__all__ = [
    "EvidenceRepository",
    "ResolvedEvidenceState",
    "evidence_agent_index_text",
    "evidence_manifest_payload",
    "evidence_state_metadata",
    "open_bound_evidence_database",
    "open_asset_repository",
    "open_resolved_asset_repository",
    "resolve_asset_evidence_state",
]
