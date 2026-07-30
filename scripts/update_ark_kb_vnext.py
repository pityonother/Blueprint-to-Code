"""Fail-closed incremental update diagnostics for ARK KB vNext.

Production supports one deliberately narrow slice: a bounded add-only set of
Blueprint Evidence Stores.  It stages an independent snapshot copy, validates
and ingests only the manifest-selected sources, and drains the real rebuild
queue.  Missing downstream backends remain ``BLOCKED_GAP``; narrow gates and
publication are intentionally unreachable until those gaps are implemented.
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
    CoreMaterializerRebuildBackend,
    RebuildBackend,
    RebuildQueueWorker,
)
from blueprint_translator.kb_vnext.blueprint_ingest import (  # noqa: E402
    BlueprintIngestResult,
    materialize_blueprint_defaults,
)
from blueprint_translator.kb_vnext.invalidation import (  # noqa: E402
    apply_invalidation_plan,
    plan_invalidation,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.pointer_cas import (  # noqa: E402
    CurrentSnapshotBaseline,
    PointerCASError,
    capture_current_snapshot_baseline,
    read_current_pointer_baseline,
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
from blueprint_translator.kb_vnext.update_baseline import (  # noqa: E402
    FrozenAdditiveBlueprintInput,
    StagedBaselineSnapshot,
    UpdateBaseline,
    UpdateBaselineBlockedGap,
    build_update_baseline,
    cleanup_staged_baseline_snapshot,
    freeze_additive_blueprint_input,
    stage_snapshot_from_baseline,
    validate_frozen_additive_blueprint_input,
    validate_final_source_manifest,
    validate_update_baseline_identity,
)


UPDATE_RESULT_SCHEMA: Final = "ark-kb-incremental-update/v2"
MAX_ADDITIVE_BLUEPRINT_SOURCES: Final = 1
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
        status: str = "blocked",
        residual_identifier: str = "",
    ) -> None:
        normalized = str(gap_code).strip().upper()
        if not normalized:
            raise ValueError("gap_code is required")
        super().__init__(detail)
        self.gap_code = normalized
        self.detail = str(detail)
        self.full_rebuild_required = bool(full_rebuild_required)
        self.status = str(status)
        self.residual_identifier = str(residual_identifier)


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
    staged_baseline: StagedBaselineSnapshot | None = None
    frozen_additive_input: FrozenAdditiveBlueprintInput | None = None


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
    require_locked_update_baseline: bool = False


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
    if len(added_blueprints) != 1:
        raise UpdateBlocked(
            "ADDITIVE_QUARANTINE_REQUIRES_SINGLE_BLUEPRINT",
            "The quarantine path requires exactly one add-only Blueprint.",
            full_rebuild_required=False,
        )


def stage_current_snapshot(
    paths: UpdatePaths,
    *,
    baseline: UpdateBaseline,
) -> UpdateWorkspace:
    """Stage only the exact UpdateBaseline captured under the writer lock."""

    paths = paths.resolved()
    if type(baseline) is not UpdateBaseline:
        raise TypeError("locked UpdateBaseline is required")
    if baseline.snapshot_root != paths.output:
        raise UpdateBlocked(
            "UPDATE_BASELINE_IDENTITY_CHANGED",
            "The staging output does not match the locked UpdateBaseline.",
            full_rebuild_required=True,
        )
    try:
        staged = stage_snapshot_from_baseline(
            baseline,
            destination=paths.output / ".incremental-staging",
        )
    except UpdateBaselineBlockedGap as exc:
        raise UpdateBlocked(
            exc.gap_code,
            str(exc),
            full_rebuild_required=True,
            status=(
                "uncertain"
                if exc.status == "UNCERTAIN"
                else "blocked"
            ),
            residual_identifier=exc.residual_identifier,
        ) from exc
    return UpdateWorkspace(
        temporary_root=staged.temporary_root,
        snapshot_dir=staged.snapshot_dir,
        core_path=staged.snapshot_dir / "core.sqlite",
        cache_path=staged.snapshot_dir / "cache.sqlite",
        projection_dir=staged.snapshot_dir / "domain_exports",
        base_build_id=staged.base_build_id,
        staging_receipt=staged.receipt,
        staged_baseline=staged,
    )


def _unavailable_stage(paths: UpdatePaths) -> UpdateWorkspace:
    """Injected hooks cannot stage without the locked UpdateBaseline."""

    del paths
    raise UpdateBlocked(
        "LOCKED_UPDATE_BASELINE_REQUIRED",
        "Production staging requires the locked UpdateBaseline.",
        full_rebuild_required=True,
    )


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


def _additive_blueprint_revisions(
    diff: SourceDiff,
) -> tuple[SourceRevision, ...]:
    return tuple(
        sorted(
            (
                change.current
                for change in diff.added
                if change.current is not None
                and change.current.source_kind == "BLUEPRINT_EVIDENCE"
            ),
            key=lambda revision: revision.source_id,
        )
    )


def plan_additive_blueprint_changes(
    workspace: UpdateWorkspace,
    diff: SourceDiff,
) -> Sequence[Mapping[str, object]]:
    """Bind additive manifest entries to exact entities in staged Core."""

    revisions = _additive_blueprint_revisions(diff)
    if not revisions:
        raise UpdateBlocked(
            "EMPTY_BLUEPRINT_ADDITION_PLAN",
            "No additive Blueprint Evidence sources reached planning.",
            full_rebuild_required=True,
        )
    connection = sqlite3.connect(workspace.core_path)
    try:
        entity_ids: dict[str, int] = {}
        for revision in revisions:
            rows = list(
                connection.execute(
                    """
                    SELECT entity_id
                    FROM entities
                    WHERE canonical_uri=?
                    LIMIT 2
                    """,
                    (revision.entity_uri,),
                )
            )
            if len(rows) != 1:
                raise UpdateBlocked(
                    "BLUEPRINT_ENTITY_NOT_IN_BASE_SNAPSHOT",
                    "An additive Blueprint source does not map to exactly "
                    "one entity in the base snapshot.",
                    full_rebuild_required=True,
                )
            entity_ids[revision.source_id] = int(rows[0][0])
    except sqlite3.DatabaseError as exc:
        raise UpdateBlocked(
            "STAGED_CORE_ENTITY_LOOKUP_FAILED",
            "The staged Core database cannot bind additive Blueprint "
            "entities.",
            full_rebuild_required=True,
        ) from exc
    finally:
        connection.close()
    workspace.invalidation_events.append(
        {
            "phase": "PLANNED",
            "eventKind": "ASSET",
            "sourceIds": [
                revision.source_id for revision in revisions
            ],
            "entityIds": [
                entity_ids[revision.source_id]
                for revision in revisions
            ],
        }
    )
    return (
        {
            "eventKind": "ASSET",
            "affected": len(entity_ids),
        },
    )


def _planned_additive_sources(
    workspace: UpdateWorkspace,
) -> tuple[list[str], list[int]]:
    plans = [
        value
        for value in workspace.invalidation_events
        if value.get("phase") == "PLANNED"
        and value.get("eventKind") == "ASSET"
    ]
    if len(plans) != 1:
        raise UpdateBlocked(
            "BLUEPRINT_ADDITION_PLAN_NOT_BOUND",
            "The staged additive Blueprint plan is missing or ambiguous.",
            full_rebuild_required=True,
        )
    source_ids = plans[0].get("sourceIds")
    entity_ids = plans[0].get("entityIds")
    if (
        not isinstance(source_ids, list)
        or not isinstance(entity_ids, list)
        or len(source_ids) != len(entity_ids)
        or not source_ids
        or any(
            not isinstance(source_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_id)
            for source_id in source_ids
        )
        or any(
            isinstance(entity_id, bool)
            or not isinstance(entity_id, int)
            or entity_id <= 0
            for entity_id in entity_ids
        )
    ):
        raise UpdateBlocked(
            "BLUEPRINT_ADDITION_PLAN_NOT_BOUND",
            "The staged additive Blueprint plan is malformed.",
            full_rebuild_required=True,
        )
    return source_ids, entity_ids


def _ingest_receipt(
    *,
    revisions: Sequence[SourceRevision],
    result: BlueprintIngestResult,
    event_id: str,
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": "ark-kb-additive-blueprint-ingest-receipt/v1",
        "verifiedSources": len(revisions),
        "affectedEntities": len(result.entity_ids),
        "materializedFacts": len(result.fact_ids),
        "factEvidence": int(result.counts.get("factEvidence", 0)),
        "eventId": event_id,
        "sourceIds": [
            revision.source_id for revision in revisions
        ],
    }
    proof = hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **body,
        "completed": True,
        "proof": f"ingest-proof://{proof}",
    }


def ingest_additive_blueprint_changes(
    workspace: UpdateWorkspace,
    diff: SourceDiff,
    paths: UpdatePaths,
) -> Mapping[str, object]:
    """Validate selected Evidence Stores, then create the real ASSET event."""

    revisions = _additive_blueprint_revisions(diff)
    frozen = workspace.frozen_additive_input
    if frozen is None:
        raise UpdateBlocked(
            "ADDITIVE_QUARANTINE_RECEIPT_MISSING",
            "Default Blueprint ingest requires a frozen quarantine receipt.",
            full_rebuild_required=True,
        )
    try:
        validate_frozen_additive_blueprint_input(frozen)
    except (UpdateBaselineBlockedGap, TypeError, ValueError) as exc:
        raise UpdateBlocked(
            getattr(
                exc,
                "gap_code",
                "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            ),
            "The frozen Blueprint quarantine failed pre-ingest validation.",
            full_rebuild_required=True,
        ) from exc
    if (
        len(revisions) != 1
        or frozen.source_id != revisions[0].source_id
        or frozen.entity_uri != revisions[0].entity_uri
        or frozen.revision_label != revisions[0].revision_label
        or frozen.source_fingerprint != revisions[0].fingerprint
    ):
        raise UpdateBlocked(
            "ADDITIVE_QUARANTINE_BASELINE_CHANGED",
            "The frozen Blueprint input no longer matches the source diff.",
            full_rebuild_required=True,
        )
    planned_source_ids, planned_entity_ids = _planned_additive_sources(
        workspace
    )
    if planned_source_ids != [
        revision.source_id for revision in revisions
    ]:
        raise UpdateBlocked(
            "BLUEPRINT_ADDITION_PLAN_DRIFT",
            "The additive Blueprint manifest changed after planning.",
            full_rebuild_required=True,
        )
    discovery = sqlite3.connect(
        f"file:{paths.discovery_database.as_posix()}?mode=ro",
        uri=True,
    )
    core = sqlite3.connect(workspace.core_path)
    core.execute("PRAGMA foreign_keys=ON")
    try:
        result = materialize_blueprint_defaults(
            discovery,
            core,
            capture_root=frozen.ingest_root,
            ontology=load_ontology(PROJECT_ROOT / "ontology"),
            source_revisions=revisions,
            frozen_evidence_root=True,
        )
        if (
            len(result.entity_ids) != len(revisions)
            or sorted(result.entity_ids) != sorted(planned_entity_ids)
            or int(result.counts.get("freshAssets", 0))
            != len(revisions)
        ):
            raise UpdateBlocked(
                "BLUEPRINT_ADDITION_INGEST_INCOMPLETE",
                "The explicit Blueprint subset was not fully materialized.",
                full_rebuild_required=True,
            )
        plan = plan_invalidation(
            core,
            event_kind="ASSET",
            entity_ids=result.entity_ids,
        )
        if not plan.downstream:
            raise UpdateBlocked(
                "BLUEPRINT_ADDITION_INVALIDATION_EMPTY",
                "The additive Blueprint ingest produced no rebuild tasks.",
                full_rebuild_required=True,
            )
        applied = apply_invalidation_plan(core, plan)
        event_id = str(applied.get("eventId") or "")
        receipt = _ingest_receipt(
            revisions=revisions,
            result=result,
            event_id=event_id,
        )
        workspace.invalidation_events.append(
            {
                "phase": "APPLIED",
                "eventKind": "ASSET",
                "eventId": event_id,
                "proof": receipt["proof"],
            }
        )
        return receipt
    except UpdateBlocked:
        core.rollback()
        raise
    except Exception as exc:
        core.rollback()
        raise UpdateBlocked(
            "BLUEPRINT_ADDITION_INGEST_FAILED",
            "The additive Blueprint Evidence subset failed validation or "
            "materialization.",
            full_rebuild_required=True,
        ) from exc
    finally:
        core.close()
        discovery.close()


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


def drain_production_rebuilds(
    workspace: UpdateWorkspace,
    max_items: int,
) -> object:
    """Drain with only the production materializers currently implemented."""

    try:
        return drain_with_rebuild_backend(
            workspace,
            max_items,
            backend=CoreMaterializerRebuildBackend(),
        )
    except (sqlite3.DatabaseError, RuntimeError, ValueError) as exc:
        raise UpdateBlocked(
            "PRODUCTION_REBUILD_DIAGNOSTIC_FAILED",
            "The production rebuild worker could not inspect or drain the "
            "staged queue.",
            full_rebuild_required=True,
        ) from exc


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
        plan_changes=plan_additive_blueprint_changes,
        ingest_changes=ingest_additive_blueprint_changes,
        drain_worker=drain_production_rebuilds,
        run_narrow_gates=_unavailable_gates,
        publish_atomic=_unavailable_publish,
        verify_publication=verify_current_publication,
        require_locked_update_baseline=True,
    )


def _worker_payload(report: object) -> dict[str, object]:
    if hasattr(report, "__dataclass_fields__"):
        return asdict(report)
    if isinstance(report, Mapping):
        return dict(report)
    raise TypeError("worker report must be a dataclass or mapping")


def _safe_staging_receipt(
    value: Mapping[str, object],
) -> dict[str, object]:
    body_keys = (
        "schema",
        "evidenceClass",
        "baseBuildId",
        "pointerSha256",
        "manifestSha256",
        "baseSourceManifestFingerprint",
        "sourceManifestFingerprint",
        "sourceDiffSha256",
        "updateBaselineIdentitySha256",
        "sourceTreeDigest",
        "stagedTreeDigest",
        "authorityDigest",
        "sameVolume",
        "sourceVerifiedUnchanged",
        "reparsePointCount",
        "hardlinkAliasCount",
        "copiedAuthorityFileCount",
        "copiedNonAuthorityFileCount",
        "cacheDisposition",
        "fileCount",
        "totalBytes",
        "createdAt",
        "stagingRelativePath",
        "published",
        "productionAuthority",
        "e4Scenario2Complete",
        "cutoverEligible",
        "mode",
        "defaultQuerySource",
    )
    body = {key: value.get(key) for key in body_keys}
    proof = str(value.get("proof") or "")
    try:
        expected_proof = "staging-proof://" + hashlib.sha256(
            json.dumps(
                body,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise UpdateBlocked(
            "STAGING_RECEIPT_INVALID",
            "Safe staging returned a non-canonical receipt.",
            full_rebuild_required=True,
        ) from exc
    digest_keys = (
        "pointerSha256",
        "manifestSha256",
        "baseSourceManifestFingerprint",
        "sourceManifestFingerprint",
        "sourceDiffSha256",
        "updateBaselineIdentitySha256",
        "sourceTreeDigest",
        "stagedTreeDigest",
        "authorityDigest",
    )
    count_keys = (
        "reparsePointCount",
        "hardlinkAliasCount",
        "copiedAuthorityFileCount",
        "copiedNonAuthorityFileCount",
        "fileCount",
        "totalBytes",
    )
    staging_relative = str(value.get("stagingRelativePath") or "")
    fixed = {
        "schema": "ark-kb-reparse-safe-staging-receipt/v1",
        "evidenceClass": "UNSIGNED_LOCAL_REPARSE_SAFE_STAGING",
        "sameVolume": True,
        "sourceVerifiedUnchanged": True,
        "reparsePointCount": 0,
        "hardlinkAliasCount": 0,
        "published": False,
        "productionAuthority": False,
        "e4Scenario2Complete": False,
        "cutoverEligible": False,
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    if (
        any(
            value.get(key) != expected
            for key, expected in fixed.items()
        )
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]*",
            str(value.get("baseBuildId") or ""),
        )
        or any(
            not re.fullmatch(
                r"[0-9a-f]{64}",
                str(value.get(key) or ""),
            )
            for key in digest_keys
        )
        or value.get("sourceTreeDigest")
        != value.get("stagedTreeDigest")
        or value.get("cacheDisposition")
        not in {"ABSENT", "COPIED_BUILD_BOUND_DISPOSABLE"}
        or not isinstance(value.get("createdAt"), str)
        or not value.get("createdAt")
        or not re.fullmatch(
            r"\.incremental-staging/[0-9a-f]{32}/snapshot",
            staging_relative,
        )
        or not re.fullmatch(r"staging-proof://[0-9a-f]{64}", proof)
        or proof != expected_proof
        or any(
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for count in (value.get(key) for key in count_keys)
        )
        or value.get("copiedAuthorityFileCount")
        + value.get("copiedNonAuthorityFileCount")
        != value.get("fileCount")
    ):
        raise UpdateBlocked(
            "STAGING_RECEIPT_INVALID",
            "Safe staging did not provide a valid non-publication receipt.",
            full_rebuild_required=True,
        )
    return {**body, "proof": proof}


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    return value


def _safe_quarantine_receipt(
    frozen: FrozenAdditiveBlueprintInput,
) -> dict[str, object]:
    try:
        validate_frozen_additive_blueprint_input(frozen)
    except (UpdateBaselineBlockedGap, TypeError, ValueError) as exc:
        raise UpdateBlocked(
            getattr(
                exc,
                "gap_code",
                "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            ),
            "The additive quarantine receipt is invalid.",
            full_rebuild_required=True,
        ) from exc
    receipt = _plain_json_value(frozen.receipt)
    if not isinstance(receipt, dict):
        raise UpdateBlocked(
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            "The additive quarantine receipt is not an object.",
            full_rebuild_required=True,
        )
    return receipt


def _safe_ingest_summary(
    value: Mapping[str, object],
) -> dict[str, object]:
    if value.get("schema") != (
        "ark-kb-additive-blueprint-ingest-receipt/v1"
    ):
        raise UpdateBlocked(
            "BLUEPRINT_INGEST_RECEIPT_INVALID",
            "The Blueprint ingestor returned an unknown receipt schema.",
            full_rebuild_required=True,
        )
    proof = str(value.get("proof") or "")
    metrics = {
        key: value.get(key)
        for key in (
            "verifiedSources",
            "affectedEntities",
            "materializedFacts",
            "factEvidence",
        )
    }
    source_ids = value.get("sourceIds")
    event_id = str(value.get("eventId") or "")
    body = {
        "schema": value.get("schema"),
        **metrics,
        "eventId": event_id,
        "sourceIds": source_ids,
    }
    expected_proof = "ingest-proof://" + hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        value.get("completed") is not True
        or not re.fullmatch(r"ingest-proof://[0-9a-f]{64}", proof)
        or proof != expected_proof
        or not event_id.startswith("invalidation://")
        or not isinstance(source_ids, list)
        or not source_ids
        or any(
            not isinstance(source_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_id)
            for source_id in source_ids
        )
        or any(
            isinstance(metric, bool)
            or not isinstance(metric, int)
            or metric < 0
            for metric in metrics.values()
        )
    ):
        raise UpdateBlocked(
            "BLUEPRINT_INGEST_RECEIPT_INVALID",
            "The Blueprint ingestor returned an invalid content receipt.",
            full_rebuild_required=True,
        )
    return {
        "completed": True,
        **metrics,
        "proof": proof,
    }


def _safe_worker_summary(
    payload: Mapping[str, object],
) -> dict[str, object]:
    summary = {
        key: value
        for key, value in payload.items()
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
    receipt_proofs: set[str] = set()
    succeeded_kinds: set[str] = set()
    blocked_gap_codes: set[str] = set()
    outcomes = payload.get("outcomes")
    if isinstance(outcomes, Sequence) and not isinstance(
        outcomes, (str, bytes)
    ):
        for outcome in outcomes:
            if not isinstance(outcome, Mapping):
                continue
            task = outcome.get("task")
            task_mapping = task if isinstance(task, Mapping) else {}
            kind = str(task_mapping.get("downstream_kind") or "")
            status = str(outcome.get("status") or "")
            proof = str(outcome.get("proof") or "")
            gap_code = str(outcome.get("gap_code") or "")
            if re.fullmatch(r"rebuild-proof://[0-9a-f]{64}", proof):
                receipt_proofs.add(proof)
            if status == "SUCCEEDED" and re.fullmatch(
                r"[A-Z][A-Z0-9_]*", kind
            ):
                succeeded_kinds.add(kind)
            if status == "BLOCKED_GAP" and re.fullmatch(
                r"[A-Z][A-Z0-9_]*", gap_code
            ):
                blocked_gap_codes.add(gap_code)
    return {
        **summary,
        "receiptProofs": sorted(receipt_proofs),
        "succeededKinds": sorted(succeeded_kinds),
        "blockedGapCodes": sorted(blocked_gap_codes),
    }


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
def _staging_workspace_lifecycle(
    paths: UpdatePaths,
    workspace: UpdateWorkspace,
    *,
    cleanup_injected: Callable[[], bool],
) -> Iterator[None]:
    """Clean only this staging instance and surface uncertain cleanup."""

    staged = workspace.staged_baseline
    try:
        yield
    finally:
        if staged is not None:
            try:
                cleanup_staged_baseline_snapshot(
                    staged,
                    snapshot_root=paths.output,
                )
            except UpdateBaselineBlockedGap as exc:
                raise _locked_baseline_error(exc) from exc
        elif (
            cleanup_injected()
            and workspace.temporary_root.exists()
        ):
            shutil.rmtree(workspace.temporary_root, ignore_errors=True)


@contextmanager
def _single_writer_lock(output: Path) -> Iterator[None]:
    output_preexisted = output.exists()
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
        if not output_preexisted:
            try:
                output.rmdir()
            except OSError:
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
    result = {
        **base,
        "status": error.status,
        "cacheHit": False,
        "published": False,
        "gapCodes": [error.gap_code],
        "reason": error.detail,
        "fullRebuildRequired": error.full_rebuild_required,
    }
    if error.residual_identifier:
        result["stagingResidualIdentifier"] = (
            error.residual_identifier
        )
    return result


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


def _capture_locked_current_snapshot(
    paths: UpdatePaths,
) -> CurrentSnapshotBaseline | None:
    """Capture the exact current pointer and manifest under the writer lock."""

    try:
        pointer = read_current_pointer_baseline(paths.output)
        if pointer.build_id is None:
            return None
        current_snapshot = capture_current_snapshot_baseline(paths.output)
        if current_snapshot.pointer != pointer:
            raise UpdateBlocked(
                "UPDATE_BASELINE_IDENTITY_CHANGED",
                "The expected current build or raw pointer changed while "
                "the writer lock was held.",
                full_rebuild_required=True,
            )
        return current_snapshot
    except (FileNotFoundError, OSError, PointerCASError, ValueError) as exc:
        raise UpdateBlocked(
            "UPDATE_BASELINE_IDENTITY_INVALID",
            "The current build, raw pointer, or manifest identity could not "
            "be captured safely.",
            full_rebuild_required=True,
        ) from exc


def _locked_baseline_error(error: Exception) -> UpdateBlocked:
    if isinstance(error, UpdateBaselineBlockedGap):
        return UpdateBlocked(
            error.gap_code,
            str(error),
            full_rebuild_required=True,
            status=(
                "uncertain"
                if error.status == "UNCERTAIN"
                else "blocked"
            ),
            residual_identifier=error.residual_identifier,
        )
    return UpdateBlocked(
        "UPDATE_BASELINE_IDENTITY_CHANGED",
        "The current build, raw pointer, manifest, or update baseline "
        "identity changed while the writer lock was held.",
        full_rebuild_required=True,
    )


def _build_locked_update_baseline(
    *,
    paths: UpdatePaths,
    expected_current_snapshot: CurrentSnapshotBaseline,
    candidate_source_manifest: SourceManifest,
) -> UpdateBaseline:
    """Build and independently revalidate one locked update baseline."""

    try:
        baseline = build_update_baseline(
            snapshot_root=paths.output,
            candidate_source_manifest=candidate_source_manifest,
            expected_current_snapshot=expected_current_snapshot,
        )
        return validate_update_baseline_identity(
            baseline,
            expected_current_snapshot=expected_current_snapshot,
            expected_candidate_source_manifest=(
                candidate_source_manifest
            ),
        )
    except (
        FileNotFoundError,
        OSError,
        PointerCASError,
        UpdateBaselineBlockedGap,
        ValueError,
    ) as exc:
        raise _locked_baseline_error(exc) from exc


def _validate_locked_update_state(
    *,
    paths: UpdatePaths,
    hooks: UpdateHooks,
    previous: SourceManifest | None,
    candidate: SourceManifest,
    diff: SourceDiff,
    baseline: UpdateBaseline | None,
    expected_current_snapshot: CurrentSnapshotBaseline | None,
) -> None:
    """Re-scan sources and recheck the exact baseline before using it."""

    if baseline is not None:
        if expected_current_snapshot is None:
            raise AssertionError("strict baseline requires current identity")
        try:
            validate_update_baseline_identity(
                baseline,
                expected_current_snapshot=expected_current_snapshot,
                expected_candidate_source_manifest=candidate,
            )
        except (
            FileNotFoundError,
            OSError,
            PointerCASError,
            UpdateBaselineBlockedGap,
            ValueError,
        ) as exc:
            raise _locked_baseline_error(exc) from exc
    observed_candidate = hooks.scan_manifest(paths)
    if baseline is not None:
        try:
            validate_final_source_manifest(
                baseline,
                observed_candidate,
            )
            validate_update_baseline_identity(
                baseline,
                expected_current_snapshot=expected_current_snapshot,
                expected_candidate_source_manifest=observed_candidate,
            )
        except (
            FileNotFoundError,
            OSError,
            PointerCASError,
            UpdateBaselineBlockedGap,
            ValueError,
        ) as exc:
            raise _locked_baseline_error(exc) from exc
        return
    observed_diff = compare_source_manifests(
        previous,
        observed_candidate,
    )
    if (
        observed_candidate.fingerprint != candidate.fingerprint
        or observed_diff != diff
    ):
        raise UpdateBlocked(
            "SOURCE_MANIFEST_CHANGED_DURING_UPDATE",
            "The candidate source manifest changed while the writer lock "
            "was held.",
            full_rebuild_required=True,
        )


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
    use_strict_default_baseline = hooks.require_locked_update_baseline
    base: dict[str, object] = {
        "schema": UPDATE_RESULT_SCHEMA,
        "published": False,
        "fullRebuildPerformed": False,
    }
    workspace: UpdateWorkspace | None = None
    workspace_is_confined = False
    try:
        with _single_writer_lock(paths.output):
            expected_current_snapshot = (
                _capture_locked_current_snapshot(paths)
                if use_strict_default_baseline
                else None
            )
            baseline: UpdateBaseline | None = None
            if expected_current_snapshot is None:
                previous = hooks.load_previous_manifest(paths)
                current = hooks.scan_manifest(paths)
                diff = compare_source_manifests(previous, current)
            else:
                current = hooks.scan_manifest(paths)
                baseline = _build_locked_update_baseline(
                    paths=paths,
                    expected_current_snapshot=(
                        expected_current_snapshot
                    ),
                    candidate_source_manifest=current,
                )
                previous = baseline.base_source_manifest
                diff = baseline.source_diff
            base.update(
                {
                    "sourceManifestFingerprint": current.fingerprint,
                    "sourceChanges": _safe_source_diff(diff),
                }
            )
            _validate_locked_update_state(
                paths=paths,
                hooks=hooks,
                previous=previous,
                candidate=current,
                diff=diff,
                baseline=baseline,
                expected_current_snapshot=expected_current_snapshot,
            )
            if diff.is_empty:
                return {
                    **base,
                    "status": "cache_hit",
                    "cacheHit": True,
                    "reason": (
                        "source manifest is unchanged; no publication needed"
                    ),
                }
            # The production check admits only the bounded additive Blueprint
            # diagnostic slice; every other source change stops before staging.
            hooks.check_capability(previous, diff)
            workspace = (
                stage_current_snapshot(paths, baseline=baseline)
                if baseline is not None
                else hooks.stage_snapshot(paths)
            )
            with _staging_workspace_lifecycle(
                paths,
                workspace,
                cleanup_injected=lambda: workspace_is_confined,
            ):
                _validate_staging_workspace(paths, workspace)
                workspace_is_confined = True
                if (
                    baseline is not None
                    and workspace.base_build_id
                    and workspace.base_build_id != baseline.base_build_id
                ):
                    raise UpdateBlocked(
                        "UPDATE_BASELINE_IDENTITY_CHANGED",
                        "The staged snapshot does not match the locked "
                        "base build.",
                        full_rebuild_required=True,
                    )
                if workspace.staging_receipt:
                    base["staging"] = _safe_staging_receipt(
                        workspace.staging_receipt
                    )
                if hooks.ingest_changes is ingest_additive_blueprint_changes:
                    if (
                        baseline is None
                        or workspace.staged_baseline is None
                    ):
                        raise UpdateBlocked(
                            "ADDITIVE_QUARANTINE_BASELINE_REQUIRED",
                            "Default ingest requires the locked staged "
                            "UpdateBaseline.",
                            full_rebuild_required=True,
                        )
                    try:
                        frozen = freeze_additive_blueprint_input(
                            baseline,
                            capture_root=paths.capture_root,
                            staged_snapshot=workspace.staged_baseline,
                        )
                    except UpdateBaselineBlockedGap as exc:
                        raise UpdateBlocked(
                            exc.gap_code,
                            str(exc),
                            full_rebuild_required=True,
                            status=(
                                "uncertain"
                                if exc.status == "UNCERTAIN"
                                else "blocked"
                            ),
                            residual_identifier=(
                                exc.residual_identifier
                            ),
                        ) from exc
                    workspace.frozen_additive_input = frozen
                    base["quarantine"] = _safe_quarantine_receipt(
                        frozen
                    )
                _validate_locked_update_state(
                    paths=paths,
                    hooks=hooks,
                    previous=previous,
                    candidate=current,
                    diff=diff,
                    baseline=baseline,
                    expected_current_snapshot=(
                        expected_current_snapshot
                    ),
                )
                plans = list(hooks.plan_changes(workspace, diff))
                if not plans:
                    raise UpdateBlocked(
                        "EMPTY_INVALIDATION_PLAN",
                        "Changed sources produced no invalidation work.",
                        full_rebuild_required=True,
                    )
                base["selectiveInvalidationPlan"] = _safe_plan_summary(
                    plans
                )
                ingest = hooks.ingest_changes(workspace, diff, paths)
                base["ingest"] = _safe_ingest_summary(ingest)
                worker = _worker_payload(
                    hooks.drain_worker(workspace, max_rebuild_items)
                )
                base["worker"] = _safe_worker_summary(worker)
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
                _validate_locked_update_state(
                    paths=paths,
                    hooks=hooks,
                    previous=previous,
                    candidate=current,
                    diff=diff,
                    baseline=baseline,
                    expected_current_snapshot=(
                        expected_current_snapshot
                    ),
                )
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
                        gap_code=(
                            "PUBLISHED_SNAPSHOT_BINDING_NOT_VERIFIED"
                        ),
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bounded additive Blueprint rebuild diagnostic and fail "
            "closed without publication when any backend or gate is missing."
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
