"""Fail-closed incremental update orchestration for ARK KB vNext.

The command has a deliberately strict production boundary.  It can compare
the complete semantic input manifest today, but the repository does not yet
provide a selective source ingestor or a complete selective rebuild backend.
Changed input therefore stops before the write lock, staging, queue mutation,
or publication.  A future implementation may inject those capabilities
through ``UpdateHooks``; its publisher must bind the source manifest into the
new immutable snapshot in the same atomic operation that switches ``current``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Iterator


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.rebuild_worker import (  # noqa: E402
    RebuildBackend,
    RebuildQueueWorker,
)
from blueprint_translator.kb_vnext.blueprint_ingest import (  # noqa: E402
    MAX_EXPLICIT_BLUEPRINT_SOURCES,
)
from blueprint_translator.kb_vnext.snapshot import (  # noqa: E402
    _snapshot_semantic_input_hashes,
    resolve_current_snapshot,
)
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SNAPSHOT_SEMANTIC_INPUT_KEYS as SNAPSHOT_SEMANTIC_INPUT_KEYS,
    SOURCE_DIFF_SCHEMA as SOURCE_DIFF_SCHEMA,
    SOURCE_MANIFEST_SCHEMA as SOURCE_MANIFEST_SCHEMA,
    SourceChange,
    SourceDiff,
    SourceManifest,
    SourceRevision as SourceRevision,
    compare_source_manifests,
    scan_source_manifest as _scan_bound_source_manifest,
    source_id,
    source_manifest_binding as source_manifest_binding,
    source_manifest_from_binding,
    source_manifest_from_payload as source_manifest_from_payload,
)


UPDATE_RESULT_SCHEMA: Final = "ark-kb-incremental-update/v2"
MAX_ADDITIVE_BLUEPRINT_SOURCES: Final = (
    MAX_EXPLICIT_BLUEPRINT_SOURCES
)
_FULL_REBUILD_INPUTS: Final = frozenset(
    {
        "discovery",
        "classHierarchyContract",
        "semanticProducerContract",
        "legacy",
        "ontology",
        "benchmarkGold",
        "qualityGold",
        "mapEvidence",
        "nativeEvidence",
        "runtimeObservations",
    }
)


def _source_id(source_kind: str, source_uri: str) -> str:
    """Backward-compatible alias for tests and injected update hooks."""

    return source_id(source_kind, source_uri)


class UpdateBlocked(RuntimeError):
    """A missing capability or unverifiable state stops publication."""

    def __init__(
        self,
        gap_code: str,
        detail: str,
        *,
        full_rebuild_required: bool,
    ) -> None:
        normalized = str(gap_code).strip().upper()
        if not normalized:
            raise ValueError("gap_code is required")
        super().__init__(detail)
        self.gap_code = normalized
        self.detail = str(detail)
        self.full_rebuild_required = bool(full_rebuild_required)


@dataclass(frozen=True)
class UpdatePaths:
    discovery_database: Path
    capture_root: Path
    native_root: Path
    runtime_root: Path
    legacy_kb_root: Path
    map_evidence_catalog: Path
    output: Path

    def resolved(self) -> "UpdatePaths":
        return UpdatePaths(
            discovery_database=self.discovery_database.resolve(),
            capture_root=self.capture_root.resolve(),
            native_root=self.native_root.resolve(),
            runtime_root=self.runtime_root.resolve(),
            legacy_kb_root=self.legacy_kb_root.resolve(),
            map_evidence_catalog=self.map_evidence_catalog.resolve(),
            output=self.output.resolve(),
        )


@dataclass
class UpdateWorkspace:
    temporary_root: Path
    snapshot_dir: Path
    core_path: Path
    cache_path: Path
    projection_dir: Path
    invalidation_events: list[dict[str, object]] = field(
        default_factory=list
    )
    base_build_id: str = ""
    staging_receipt: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    checks: tuple[dict[str, object], ...]

    def payload(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "total": len(self.checks),
            "failed": sum(
                not bool(check.get("passed")) for check in self.checks
            ),
        }


def _validated_gate_payload(gates: GateResult) -> dict[str, object]:
    if type(gates.passed) is not bool or not gates.checks:
        raise UpdateBlocked(
            "NARROW_GATE_RESULT_INVALID",
            "Narrow gates require a non-empty, boolean result set.",
            full_rebuild_required=True,
        )
    seen: set[str] = set()
    passed_values: list[bool] = []
    for check in gates.checks:
        check_id = str(check.get("id") or "")
        passed = check.get("passed")
        if (
            not re.fullmatch(r"[a-z][a-z0-9_.-]*", check_id)
            or check_id in seen
            or type(passed) is not bool
        ):
            raise UpdateBlocked(
                "NARROW_GATE_RESULT_INVALID",
                "Narrow gate checks require unique safe IDs and booleans.",
                full_rebuild_required=True,
            )
        seen.add(check_id)
        passed_values.append(passed)
    recomputed = all(passed_values)
    if gates.passed != recomputed:
        raise UpdateBlocked(
            "NARROW_GATE_RESULT_INVALID",
            "Narrow gate aggregate contradicts its check results.",
            full_rebuild_required=True,
        )
    return {
        "passed": recomputed,
        "total": len(passed_values),
        "failed": sum(not value for value in passed_values),
    }


LoadManifest = Callable[[UpdatePaths], SourceManifest | None]
ScanManifest = Callable[[UpdatePaths], SourceManifest]
CapabilityCheck = Callable[
    [SourceManifest | None, SourceDiff],
    None,
]
StageSnapshot = Callable[[UpdatePaths], UpdateWorkspace]
PlanChanges = Callable[
    [UpdateWorkspace, SourceDiff],
    Sequence[Mapping[str, object]],
]
IngestChanges = Callable[
    [UpdateWorkspace, SourceDiff, UpdatePaths],
    Mapping[str, object],
]
DrainWorker = Callable[[UpdateWorkspace, int], object]
NarrowGates = Callable[[UpdateWorkspace], GateResult]
AtomicPublisher = Callable[
    [UpdateWorkspace, UpdatePaths, SourceManifest, SourceDiff],
    Mapping[str, object],
]
PublicationVerifier = Callable[
    [UpdatePaths, str, SourceManifest],
    bool,
]


@dataclass(frozen=True)
class UpdateHooks:
    load_previous_manifest: LoadManifest
    scan_manifest: ScanManifest
    check_capability: CapabilityCheck
    stage_snapshot: StageSnapshot
    plan_changes: PlanChanges
    ingest_changes: IngestChanges
    drain_worker: DrainWorker
    run_narrow_gates: NarrowGates
    publish_atomic: AtomicPublisher
    verify_publication: PublicationVerifier


def scan_source_manifest(paths: UpdatePaths) -> SourceManifest:
    """Fingerprint every input covered by the immutable snapshot contract."""

    paths = paths.resolved()
    try:
        hashes = _snapshot_semantic_input_hashes(
            project_root=PROJECT_ROOT,
            discovery_database=paths.discovery_database,
            legacy_kb_root=paths.legacy_kb_root,
            capture_root=paths.capture_root,
            native_root=paths.native_root,
            map_evidence_path=paths.map_evidence_catalog,
        )
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        raise UpdateBlocked(
            "SEMANTIC_INPUT_SCAN_FAILED",
            "One or more semantic inputs are missing or unreadable.",
            full_rebuild_required=True,
        ) from exc
    try:
        return _scan_bound_source_manifest(
            semantic_input_hashes=hashes,
            capture_root=paths.capture_root,
            native_root=paths.native_root,
            runtime_root=paths.runtime_root,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        raise UpdateBlocked(
            "SEMANTIC_INPUT_SCAN_FAILED",
            "One or more semantic inputs are missing or unreadable.",
            full_rebuild_required=True,
        ) from exc


def load_current_source_manifest(
    paths: UpdatePaths,
) -> SourceManifest | None:
    """Read the manifest atomically bound to the current immutable snapshot."""

    try:
        current = resolve_current_snapshot(
            paths.output,
            allow_legacy=False,
        )
    except (FileNotFoundError, ValueError):
        return None
    update = current.manifest.get("incrementalUpdate")
    if update is None:
        return None
    try:
        return source_manifest_from_binding(update)
    except ValueError as exc:
        raise UpdateBlocked(
            "CURRENT_SOURCE_MANIFEST_INVALID",
            "The current immutable snapshot has an invalid source manifest.",
            full_rebuild_required=True,
        ) from exc


def _semantic_key(change: SourceChange) -> str:
    revision = change.current or change.previous
    if revision is None:
        return ""
    prefix = "semantic-input://"
    return (
        revision.source_uri[len(prefix) :]
        if revision.source_uri.startswith(prefix)
        else ""
    )


def production_capability_check(
    previous: SourceManifest | None,
    diff: SourceDiff,
) -> None:
    """Allow only the reviewed, bounded additive Blueprint slice."""

    if previous is None:
        raise UpdateBlocked(
            "INITIAL_FULL_REBUILD_REQUIRED",
            "The current immutable snapshot has no bound source baseline.",
            full_rebuild_required=True,
        )
    semantic_changes = {
        _semantic_key(change)
        for change in diff.all_changes
        if _semantic_key(change)
    }
    if (
        semantic_changes & _FULL_REBUILD_INPUTS
        or semantic_changes - {"captures"}
    ):
        raise UpdateBlocked(
            "NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED",
            "A non-selective semantic input changed.",
            full_rebuild_required=True,
        )
    added_blueprints = tuple(
        change.current
        for change in diff.added
        if change.current is not None
        and change.current.source_kind == "BLUEPRINT_EVIDENCE"
    )
    changed_blueprints = tuple(
        change
        for change in diff.changed
        if (
            change.current is not None
            and change.current.source_kind == "BLUEPRINT_EVIDENCE"
        )
        or (
            change.previous is not None
            and change.previous.source_kind == "BLUEPRINT_EVIDENCE"
        )
    )
    deleted_blueprints = tuple(
        change.previous
        for change in diff.deleted
        if change.previous is not None
        and change.previous.source_kind == "BLUEPRINT_EVIDENCE"
    )
    added_entities = {
        revision.entity_uri for revision in added_blueprints
    }
    deleted_entities = {
        revision.entity_uri for revision in deleted_blueprints
    }
    if added_entities & deleted_entities:
        raise UpdateBlocked(
            "BLUEPRINT_RENAME_NOT_SUPPORTED",
            "Blueprint capture rename requires a reviewed rename backend.",
            full_rebuild_required=True,
        )
    if changed_blueprints:
        raise UpdateBlocked(
            "BLUEPRINT_UPDATE_NOT_SUPPORTED",
            "Blueprint Evidence updates are not closed by the additive slice.",
            full_rebuild_required=True,
        )
    if deleted_blueprints:
        raise UpdateBlocked(
            "BLUEPRINT_DELETE_NOT_SUPPORTED",
            "Blueprint Evidence deletion requires a reviewed delete backend.",
            full_rebuild_required=True,
        )
    nonsemantic_changes = tuple(
        change
        for change in diff.all_changes
        if _semantic_key(change) == ""
    )
    if len(nonsemantic_changes) != len(added_blueprints):
        raise UpdateBlocked(
            "SELECTIVE_SOURCE_KIND_NOT_SUPPORTED",
            "Only additive Blueprint Evidence sources are supported.",
            full_rebuild_required=True,
        )
    if not added_blueprints or semantic_changes != {"captures"}:
        raise UpdateBlocked(
            "BLUEPRINT_CAPTURE_AGGREGATE_BINDING_REQUIRED",
            "Additive Blueprint Evidence must be the only source change and "
            "must update the captures aggregate fingerprint.",
            full_rebuild_required=True,
        )
    if len(added_blueprints) > MAX_ADDITIVE_BLUEPRINT_SOURCES:
        raise UpdateBlocked(
            "BLUEPRINT_ADDITION_BATCH_TOO_LARGE",
            "The additive Blueprint batch exceeds the reviewed bound.",
            full_rebuild_required=False,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in sorted(
        root.rglob("*"),
        key=lambda value: value.relative_to(root).as_posix(),
    ):
        is_junction = bool(
            getattr(path, "is_junction", lambda: False)()
        )
        if path.is_symlink() or is_junction:
            raise UpdateBlocked(
                "IMMUTABLE_SNAPSHOT_LINK_UNSAFE",
                "The immutable snapshot contains a link or junction.",
                full_rebuild_required=True,
            )
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise UpdateBlocked(
                "IMMUTABLE_SNAPSHOT_SPECIAL_FILE_UNSAFE",
                "The immutable snapshot contains a special file.",
                full_rebuild_required=True,
            )
    return tuple(files)


def _snapshot_tree_digest(root: Path, files: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _backup_sqlite(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(
        f"file:{source.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        result = destination_connection.execute(
            "PRAGMA quick_check"
        ).fetchone()
        if result != ("ok",):
            raise UpdateBlocked(
                "STAGED_SQLITE_QUICK_CHECK_FAILED",
                "A staged SQLite backup failed quick_check.",
                full_rebuild_required=True,
            )
    finally:
        destination_connection.close()
        source_connection.close()


def _staging_receipt(
    *,
    build_id: str,
    source_digest: str,
    staged_digest: str,
    sqlite_files: int,
    regular_files: int,
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": "ark-kb-incremental-staging-receipt/v1",
        "baseBuildId": build_id,
        "copyMethod": "sqlite-backup-and-file-copy",
        "sourceSnapshotSha256": source_digest,
        "stagedSnapshotSha256": staged_digest,
        "sourceVerifiedUnchanged": True,
        "writableHardlinks": False,
        "sqliteFiles": sqlite_files,
        "regularFiles": regular_files,
    }
    proof = hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {**body, "proof": f"staging-proof://{proof}"}


def stage_current_snapshot(paths: UpdatePaths) -> UpdateWorkspace:
    """Clone current into same-volume staging without writable hardlinks."""

    paths = paths.resolved()
    try:
        current = resolve_current_snapshot(
            paths.output,
            allow_legacy=False,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise UpdateBlocked(
            "CURRENT_IMMUTABLE_SNAPSHOT_UNAVAILABLE",
            "The current immutable snapshot cannot be staged safely.",
            full_rebuild_required=True,
        ) from exc
    source = current.snapshot_dir.resolve()
    files = _snapshot_files(source)
    if any(
        path.name.endswith(("-wal", "-shm"))
        and ".sqlite-" in path.name
        for path in files
    ):
        raise UpdateBlocked(
            "IMMUTABLE_SNAPSHOT_SQLITE_SIDECAR",
            "The immutable snapshot contains SQLite WAL/SHM sidecars.",
            full_rebuild_required=True,
        )
    source_digest_before = _snapshot_tree_digest(source, files)
    staging_root = paths.output / ".incremental-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    temporary_root = staging_root / uuid.uuid4().hex
    temporary_root.mkdir()
    snapshot_dir = temporary_root / "snapshot"
    snapshot_dir.mkdir()
    try:
        if temporary_root.stat().st_dev != paths.output.stat().st_dev:
            raise UpdateBlocked(
                "STAGING_NOT_ON_TARGET_VOLUME",
                "Incremental staging is not on the target output volume.",
                full_rebuild_required=True,
            )
        sqlite_files = 0
        regular_files = 0
        for source_path in files:
            relative = source_path.relative_to(source)
            destination = snapshot_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source_path.suffix.casefold() == ".sqlite":
                _backup_sqlite(source_path, destination)
                sqlite_files += 1
            else:
                shutil.copy2(source_path, destination)
                regular_files += 1
            if os.path.samefile(source_path, destination):
                raise UpdateBlocked(
                    "STAGING_WRITABLE_HARDLINK_REJECTED",
                    "A staged file aliases the immutable source inode.",
                    full_rebuild_required=True,
                )
        source_digest_after = _snapshot_tree_digest(
            source,
            _snapshot_files(source),
        )
        if source_digest_before != source_digest_after:
            raise UpdateBlocked(
                "IMMUTABLE_SNAPSHOT_CHANGED_DURING_STAGE",
                "The immutable source snapshot changed during staging.",
                full_rebuild_required=True,
            )
        staged_files = _snapshot_files(snapshot_dir)
        staged_digest = _snapshot_tree_digest(
            snapshot_dir,
            staged_files,
        )
        workspace = UpdateWorkspace(
            temporary_root=temporary_root,
            snapshot_dir=snapshot_dir,
            core_path=snapshot_dir / "core.sqlite",
            cache_path=snapshot_dir / "cache.sqlite",
            projection_dir=snapshot_dir / "domain_exports",
            base_build_id=current.build_id,
            staging_receipt=_staging_receipt(
                build_id=current.build_id,
                source_digest=source_digest_before,
                staged_digest=staged_digest,
                sqlite_files=sqlite_files,
                regular_files=regular_files,
            ),
        )
        if (
            not workspace.core_path.is_file()
            or not workspace.cache_path.is_file()
            or not workspace.projection_dir.is_dir()
        ):
            raise UpdateBlocked(
                "STAGED_SNAPSHOT_LAYOUT_INCOMPLETE",
                "The staged snapshot lacks Core, Cache, or projections.",
                full_rebuild_required=True,
            )
        return workspace
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def _unavailable_stage(paths: UpdatePaths) -> UpdateWorkspace:
    """Compatibility hook name; staging itself is now production-safe."""

    return stage_current_snapshot(paths)


def _unavailable_plan(
    workspace: UpdateWorkspace,
    diff: SourceDiff,
) -> Sequence[Mapping[str, object]]:
    del workspace, diff
    raise UpdateBlocked(
        "SELECTIVE_INVALIDATION_UNAVAILABLE",
        "Selective invalidation wiring is not implemented.",
        full_rebuild_required=True,
    )


def _unavailable_ingest(
    workspace: UpdateWorkspace,
    diff: SourceDiff,
    paths: UpdatePaths,
) -> Mapping[str, object]:
    del workspace, diff, paths
    raise UpdateBlocked(
        "SELECTIVE_SOURCE_INGEST_NOT_IMPLEMENTED",
        "Selective source ingestion is not implemented.",
        full_rebuild_required=True,
    )


def _unavailable_drain(
    workspace: UpdateWorkspace,
    max_items: int,
) -> object:
    del workspace, max_items
    raise UpdateBlocked(
        "SELECTIVE_REBUILD_BACKEND_NOT_IMPLEMENTED",
        "The complete selective rebuild backend is not implemented.",
        full_rebuild_required=True,
    )


def drain_with_rebuild_backend(
    workspace: UpdateWorkspace,
    max_items: int,
    *,
    backend: RebuildBackend,
) -> object:
    """Consume the real queue once a complete reviewed backend is supplied."""

    connection = sqlite3.connect(workspace.core_path)
    try:
        return RebuildQueueWorker(connection, backend).drain(
            max_items=max_items,
            recover_running=True,
        )
    finally:
        connection.close()


def _unavailable_gates(workspace: UpdateWorkspace) -> GateResult:
    del workspace
    raise UpdateBlocked(
        "SELECTIVE_NARROW_GATES_UNAVAILABLE",
        "Selective narrow gates are not implemented.",
        full_rebuild_required=True,
    )


def _unavailable_publish(
    workspace: UpdateWorkspace,
    paths: UpdatePaths,
    manifest: SourceManifest,
    diff: SourceDiff,
) -> Mapping[str, object]:
    del workspace, paths, manifest, diff
    raise UpdateBlocked(
        "ATOMIC_INCREMENTAL_PUBLICATION_NOT_IMPLEMENTED",
        "No atomic incremental snapshot publisher is implemented.",
        full_rebuild_required=True,
    )


def verify_current_publication(
    paths: UpdatePaths,
    build_id: str,
    manifest: SourceManifest,
) -> bool:
    """Independently re-read current after the publisher's atomic switch."""

    try:
        current = resolve_current_snapshot(
            paths.output,
            allow_legacy=False,
        )
    except (FileNotFoundError, ValueError):
        return False
    if current.build_id != build_id:
        return False
    update = current.manifest.get("incrementalUpdate")
    if update is None:
        return False
    try:
        bound = source_manifest_from_binding(update)
    except ValueError:
        return False
    return bound.fingerprint == manifest.fingerprint


def default_hooks() -> UpdateHooks:
    return UpdateHooks(
        load_previous_manifest=load_current_source_manifest,
        scan_manifest=scan_source_manifest,
        check_capability=production_capability_check,
        stage_snapshot=_unavailable_stage,
        plan_changes=_unavailable_plan,
        ingest_changes=_unavailable_ingest,
        drain_worker=_unavailable_drain,
        run_narrow_gates=_unavailable_gates,
        publish_atomic=_unavailable_publish,
        verify_publication=verify_current_publication,
    )


def _worker_payload(report: object) -> dict[str, object]:
    if hasattr(report, "__dataclass_fields__"):
        return asdict(report)
    if isinstance(report, Mapping):
        return dict(report)
    raise TypeError("worker report must be a dataclass or mapping")


def _worker_blockers(payload: Mapping[str, object]) -> list[str]:
    required_groups = (
        ("attempted",),
        ("succeeded",),
        ("failed",),
        ("blocked_gap", "blockedGap"),
        ("remaining_pending", "remainingPending"),
        ("remaining_running", "remainingRunning"),
    )
    metrics: list[int] = []
    for aliases in required_groups:
        value = next(
            (payload[key] for key in aliases if key in payload),
            None,
        )
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            return ["report=incomplete"]
        metrics.append(value)
    attempted, succeeded, failed, blocked, pending, running = metrics
    if attempted == 0:
        return ["report=no_attempts"]
    if attempted != succeeded + failed + blocked:
        return ["report=count_mismatch"]
    blockers: list[str] = []
    for key, value in (
        ("failed", failed),
        ("blockedGap", blocked),
        ("remainingPending", pending),
        ("remainingRunning", running),
    ):
        if value:
            blockers.append(f"{key}={value}")
    return blockers


def _safe_source_diff(diff: SourceDiff) -> dict[str, object]:
    def identifiers(changes: Sequence[SourceChange]) -> list[str]:
        values: list[str] = []
        for change in changes:
            source_id = str(change.source_id or "")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", source_id):
                source_id = hashlib.sha256(
                    source_id.encode("utf-8")
                ).hexdigest()
            values.append(source_id.lower())
        return values

    return {
        "schema": diff.schema,
        "added": identifiers(diff.added),
        "changed": identifiers(diff.changed),
        "deleted": identifiers(diff.deleted),
    }


def _safe_plan_summary(
    plans: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    event_kinds: set[str] = set()
    affected = 0
    for plan in plans:
        kind = str(plan.get("eventKind") or "").strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", kind):
            event_kinds.add(kind)
        count = plan.get("affected")
        if (
            isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
        ):
            affected += count
    return {
        "items": len(plans),
        "eventKinds": sorted(event_kinds),
        "affected": affected,
    }


def _validate_staging_workspace(
    paths: UpdatePaths,
    workspace: UpdateWorkspace,
) -> None:
    """Confine injected staging and cleanup to one reserved output subtree."""

    allowed_root = (paths.output / ".incremental-staging").resolve()
    temporary_root = workspace.temporary_root.resolve()
    if (
        temporary_root == allowed_root
        or not temporary_root.is_relative_to(allowed_root)
    ):
        raise UpdateBlocked(
            "STAGING_ROOT_OUT_OF_SCOPE",
            "The staging workspace is outside the reserved output subtree.",
            full_rebuild_required=True,
        )
    for label, value in (
        ("snapshot", workspace.snapshot_dir),
        ("core", workspace.core_path),
        ("cache", workspace.cache_path),
        ("projection", workspace.projection_dir),
    ):
        resolved = value.resolve()
        if (
            resolved == temporary_root
            or not resolved.is_relative_to(temporary_root)
        ):
            raise UpdateBlocked(
                "STAGING_PATH_OUT_OF_SCOPE",
                f"The staged {label} path escapes its temporary workspace.",
                full_rebuild_required=True,
            )


@contextmanager
def _single_writer_lock(output: Path) -> Iterator[None]:
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / ".incremental-update.lock"
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError as exc:
        raise UpdateBlocked(
            "INCREMENTAL_UPDATE_ALREADY_RUNNING",
            "Another incremental update owns the single-writer lock.",
            full_rebuild_required=False,
        ) from exc
    try:
        os.write(descriptor, b"locked\n")
        os.close(descriptor)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _safe_publication(
    value: Mapping[str, object],
    manifest: SourceManifest,
) -> dict[str, object]:
    payload = dict(value)
    build_id = str(payload.get("buildId") or "")
    bound = str(payload.get("sourceManifestFingerprint") or "")
    if (
        not build_id
        or bound != manifest.fingerprint
        or payload.get("atomicSourceManifestBound") is not True
    ):
        raise UpdateBlocked(
            "ATOMIC_PUBLICATION_RECEIPT_INVALID",
            "Publisher did not prove atomic source-manifest binding.",
            full_rebuild_required=True,
        )
    return {
        "buildId": build_id,
        "sourceManifestFingerprint": bound,
        "atomicSourceManifestBound": True,
    }


def _blocked_result(
    *,
    base: Mapping[str, object],
    error: UpdateBlocked,
) -> dict[str, object]:
    return {
        **base,
        "status": "blocked",
        "cacheHit": False,
        "published": False,
        "gapCodes": [error.gap_code],
        "reason": error.detail,
        "fullRebuildRequired": error.full_rebuild_required,
    }


def _uncertain_after_switch_result(
    *,
    base: Mapping[str, object],
    gap_code: str,
) -> dict[str, object]:
    return {
        **base,
        "status": "uncertain_after_switch",
        "cacheHit": False,
        "published": None,
        "gapCodes": [gap_code],
        "reason": (
            "Publication was invoked, but its final current-pointer state "
            "could not be independently verified. Re-read current before "
            "retrying."
        ),
        "fullRebuildRequired": True,
    }


def run_incremental_update(
    paths: UpdatePaths,
    *,
    hooks: UpdateHooks | None = None,
    max_rebuild_items: int = 10_000,
) -> dict[str, object]:
    """Run one update without exposing host paths or unbound state."""

    if (
        isinstance(max_rebuild_items, bool)
        or not isinstance(max_rebuild_items, int)
        or max_rebuild_items <= 0
    ):
        raise ValueError("max_rebuild_items must be a positive integer")
    paths = paths.resolved()
    hooks = hooks or default_hooks()
    base: dict[str, object] = {
        "schema": UPDATE_RESULT_SCHEMA,
        "published": False,
        "fullRebuildPerformed": False,
    }
    try:
        previous = hooks.load_previous_manifest(paths)
        current = hooks.scan_manifest(paths)
    except UpdateBlocked as exc:
        return _blocked_result(base=base, error=exc)
    diff = compare_source_manifests(previous, current)
    base.update(
        {
            "sourceManifestFingerprint": current.fingerprint,
            "sourceChanges": _safe_source_diff(diff),
        }
    )
    if diff.is_empty:
        return {
            **base,
            "status": "cache_hit",
            "cacheHit": True,
            "reason": "source manifest is unchanged; no publication needed",
        }
    try:
        # The default check always stops here.  No lock, staging copy, WAL
        # cache copy, queue mutation, or publisher is reachable in production.
        hooks.check_capability(previous, diff)
    except UpdateBlocked as exc:
        return _blocked_result(base=base, error=exc)

    workspace: UpdateWorkspace | None = None
    workspace_is_confined = False
    try:
        with _single_writer_lock(paths.output):
            workspace = hooks.stage_snapshot(paths)
            _validate_staging_workspace(paths, workspace)
            workspace_is_confined = True
            plans = list(hooks.plan_changes(workspace, diff))
            if not plans:
                raise UpdateBlocked(
                    "EMPTY_INVALIDATION_PLAN",
                    "Changed sources produced no invalidation work.",
                    full_rebuild_required=True,
                )
            base["selectiveInvalidationPlan"] = _safe_plan_summary(plans)
            hooks.ingest_changes(workspace, diff, paths)
            base["ingest"] = {"completed": True}
            worker = _worker_payload(
                hooks.drain_worker(workspace, max_rebuild_items)
            )
            base["worker"] = {
                key: value
                for key, value in worker.items()
                if key
                in {
                    "attempted",
                    "succeeded",
                    "failed",
                    "blocked_gap",
                    "blockedGap",
                    "remaining_pending",
                    "remainingPending",
                    "remaining_running",
                    "remainingRunning",
                    "drained",
                }
                and isinstance(value, (bool, int))
            }
            blockers = _worker_blockers(worker)
            if blockers:
                raise UpdateBlocked(
                    "REBUILD_QUEUE_NOT_DRAINED",
                    ", ".join(blockers),
                    full_rebuild_required=True,
                )
            gates = hooks.run_narrow_gates(workspace)
            gate_payload = _validated_gate_payload(gates)
            base["narrowGates"] = gate_payload
            if not bool(gate_payload["passed"]):
                return {
                    **base,
                    "status": "gate_failed",
                    "cacheHit": False,
                    "reason": (
                        "narrow gates failed; snapshot not published"
                    ),
                }
            try:
                raw_publication = hooks.publish_atomic(
                    workspace,
                    paths,
                    current,
                    diff,
                )
            except Exception:
                return _uncertain_after_switch_result(
                    base=base,
                    gap_code="PUBLISHER_OUTCOME_UNCERTAIN",
                )
            try:
                publication = _safe_publication(
                    raw_publication,
                    current,
                )
            except UpdateBlocked as exc:
                return _uncertain_after_switch_result(
                    base=base,
                    gap_code=exc.gap_code,
                )
            try:
                verified = hooks.verify_publication(
                    paths,
                    str(publication["buildId"]),
                    current,
                )
            except Exception:
                verified = False
            if not verified:
                return _uncertain_after_switch_result(
                    base=base,
                    gap_code="PUBLISHED_SNAPSHOT_BINDING_NOT_VERIFIED",
                )
            return {
                **base,
                "status": "published",
                "cacheHit": False,
                "published": True,
                "publication": publication,
            }
    except UpdateBlocked as exc:
        return _blocked_result(base=base, error=exc)
    finally:
        if (
            workspace is not None
            and workspace_is_confined
            and workspace.temporary_root.exists()
        ):
            # The injected staging implementation owns only this explicit
            # temporary root.  Default production hooks never reach it.
            import shutil

            shutil.rmtree(workspace.temporary_root, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare ARK KB semantic inputs and fail closed until selective "
            "ingestion and publication are implemented."
        )
    )
    parser.add_argument("--discovery-database", type=Path, required=True)
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=PROJECT_ROOT / "captures",
    )
    parser.add_argument(
        "--native-root",
        type=Path,
        default=PROJECT_ROOT / "native_evidence",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=PROJECT_ROOT / "runtime_observations",
    )
    parser.add_argument(
        "--legacy-kb-root",
        type=Path,
        default=PROJECT_ROOT / "knowledge_base" / "db",
    )
    parser.add_argument(
        "--map-evidence-catalog",
        type=Path,
        default=(
            PROJECT_ROOT
            / "analysis"
            / "harvest_nodes"
            / "resource_node_catalog.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "knowledge_base" / "vnext",
    )
    parser.add_argument(
        "--max-rebuild-items",
        type=int,
        default=10_000,
    )
    return parser


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_incremental_update(
        UpdatePaths(
            discovery_database=_absolute(args.discovery_database),
            capture_root=_absolute(args.capture_root),
            native_root=_absolute(args.native_root),
            runtime_root=_absolute(args.runtime_root),
            legacy_kb_root=_absolute(args.legacy_kb_root),
            map_evidence_catalog=_absolute(
                args.map_evidence_catalog
            ),
            output=_absolute(args.output),
        ),
        max_rebuild_items=args.max_rebuild_items,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in {"cache_hit", "published"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
