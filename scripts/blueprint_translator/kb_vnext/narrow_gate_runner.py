"""Production observation runner for the fixed Stage 14 narrow gates."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from .invalidation import validate_effective_resolution_dependencies
from .narrow_gates import (
    NARROW_GATE_CHECK_IDS,
    NarrowGateObservation,
    UpdateBaseline as NarrowGateUpdateBaseline,
    build_narrow_gate_diagnostic_report,
    narrow_gate_diagnostic_report_sha256,
    parse_and_validate_narrow_gate_diagnostic_report_bytes,
)
from .query_planner import source_revision_is_fresh
from .registrations import is_valid_registration_evidence_uri
from .signed_receipts import canonical_json_bytes
from .snapshot import (
    _validate_staged_snapshot_for_promotion,
    active_stale_source_count,
    validate_snapshot_database_schemas,
    validate_snapshot_journal_safety,
    validate_snapshot_projection_bindings,
)
from .source_manifest import SourceManifest, source_manifest_from_binding
from .update_baseline import (
    FrozenAdditiveBlueprintInput,
    StagedBaselineSnapshot,
    UpdateBaseline,
    inspect_base_bound_prepublication_delta_receipt,
    validate_final_source_manifest,
    validate_update_baseline_identity,
)
from .incremental_publisher import _truth_digest


class ProductionNarrowGateError(ValueError):
    """One computed observation failed before a success report existed."""

    def __init__(self, gate_id: str, detail: str) -> None:
        self.gate_id = gate_id
        super().__init__(f"{gate_id}: {detail}")


@dataclass(frozen=True, slots=True)
class ProductionNarrowGateInputs:
    baseline: UpdateBaseline
    staged_snapshot: StagedBaselineSnapshot
    frozen_input: FrozenAdditiveBlueprintInput
    candidate_source_manifest: SourceManifest
    candidate_manifest: Mapping[str, object]
    delta_receipt_bytes: bytes
    delta_receipt_sha256: str
    worker_report: object
    changed_source_revision_ids: tuple[int, ...]
    affected_entity_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ProductionNarrowGateRun:
    report: dict[str, object]
    report_bytes: bytes
    report_sha256: str
    observations: tuple[NarrowGateObservation, ...]


def _fail(gate_id: str, detail: str) -> ProductionNarrowGateError:
    return ProductionNarrowGateError(gate_id, detail)


def _evidence(
    gate_id: str,
    payload: Mapping[str, object],
    *,
    observation_count: int,
) -> NarrowGateObservation:
    envelope = {
        "schema": "ark-kb-production-narrow-gate-evidence/v1",
        "gateId": gate_id,
        "observation": dict(payload),
    }
    return NarrowGateObservation(
        gate_id=gate_id,
        observation_count=observation_count,
        evidence_sha256=hashlib.sha256(
            canonical_json_bytes(envelope)
        ).hexdigest(),
    )


def _worker_payload(value: object) -> dict[str, object]:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if not isinstance(value, Mapping):
        raise TypeError("worker report must be a dataclass or mapping")

    def normalize(child: object) -> object:
        if isinstance(child, Mapping):
            if any(type(key) is not str for key in child):
                raise TypeError("worker report keys must be strings")
            return {key: normalize(item) for key, item in child.items()}
        if isinstance(child, (list, tuple)):
            return [normalize(item) for item in child]
        return child

    normalized = normalize(value)
    if not isinstance(normalized, dict):
        raise AssertionError("worker report normalization failed")
    return normalized


def _integer_tuple(value: Sequence[int], *, label: str) -> tuple[int, ...]:
    normalized = tuple(value)
    if (
        not normalized
        or normalized != tuple(sorted(set(normalized)))
        or any(type(item) is not int or item < 1 for item in normalized)
    ):
        raise ValueError(f"{label} must be canonical positive integers")
    return normalized


def _read_only(path: Path) -> sqlite3.Connection:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"candidate database is missing or unsafe: {path.name}")
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def _changed_revisions_evidence(
    core: sqlite3.Connection,
    revision_ids: tuple[int, ...],
) -> dict[str, object]:
    placeholders = ",".join("?" for _ in revision_ids)
    rows = list(
        core.execute(
            f"""
            SELECT revision_id, source_kind, source_uri, source_fingerprint,
                   producer_version, schema_version, generated_at,
                   freshness_status
            FROM source_revisions
            WHERE revision_id IN ({placeholders})
            ORDER BY revision_id
            """,
            revision_ids,
        )
    )
    if [int(row[0]) for row in rows] != list(revision_ids) or any(
        not source_revision_is_fresh(
            {
                "sourceKind": row[1],
                "sourceUri": row[2],
                "sourceFingerprint": row[3],
                "producerVersion": row[4],
                "schemaVersion": row[5],
                "generatedAt": row[6],
                "freshnessStatus": row[7],
            },
            require_revision_id=False,
        )
        for row in rows
    ):
        raise _fail(NARROW_GATE_CHECK_IDS[1], "changed revision is not fresh")
    return {
        "revisionIds": list(revision_ids),
        "revisionIdentitySha256": hashlib.sha256(
            canonical_json_bytes([list(row) for row in rows])
        ).hexdigest(),
    }


def _orphan_evidence(core: sqlite3.Connection) -> dict[str, object]:
    violations = [list(row) for row in core.execute("PRAGMA foreign_key_check")]
    if violations:
        raise _fail(NARROW_GATE_CHECK_IDS[3], "Core has orphan rows")
    counts = {
        table: int(core.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in (
            "facts",
            "fact_evidence",
            "edges",
            "knowledge_roles",
            "domain_memberships",
        )
    }
    return {"foreignKeyViolations": 0, "tableCounts": counts}


def _registration_evidence(core: sqlite3.Connection) -> dict[str, object]:
    rows = list(
        core.execute(
            """
            SELECT registration.owner_uri, registration.target_uri,
                   registration.source_property, registration.evidence_uri,
                   revision.source_kind, revision.source_uri,
                   revision.source_fingerprint, revision.producer_version,
                   revision.schema_version, revision.generated_at,
                   revision.freshness_status
            FROM typed_registrations AS registration
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=registration.source_revision_id
            ORDER BY registration.registration_id
            """
        )
    )
    complete = [
        row
        for row in rows
        if all(str(value or "").strip() for value in row[:3])
        and is_valid_registration_evidence_uri(row[3])
        and source_revision_is_fresh(
            {
                "sourceKind": row[4],
                "sourceUri": row[5],
                "sourceFingerprint": row[6],
                "producerVersion": row[7],
                "schemaVersion": row[8],
                "generatedAt": row[9],
                "freshnessStatus": row[10],
            },
            require_revision_id=False,
        )
    ]
    if len(complete) != len(rows):
        raise _fail(
            NARROW_GATE_CHECK_IDS[5],
            "registration owner, target, Evidence, or revision is unresolved",
        )
    return {
        "registrations": len(rows),
        "complete": len(complete),
        "lineageSha256": hashlib.sha256(
            canonical_json_bytes([list(row) for row in rows])
        ).hexdigest(),
    }


def _projection_evidence(
    staging: Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    validate_snapshot_projection_bindings(
        snapshot_dir=staging,
        manifest=manifest,
    )
    databases = manifest.get("databases")
    if not isinstance(databases, Mapping):
        raise _fail(NARROW_GATE_CHECK_IDS[6], "projection manifest is missing")
    digests = {
        name: str(
            (databases.get(f"domain_exports/{name}.sqlite") or {}).get(
                "contentDigest"
            )
        )
        for name in sorted(
            path.stem
            for path in (staging / "domain_exports").glob("*.sqlite")
        )
    }
    if len(digests) != 6 or any(len(value) != 64 for value in digests.values()):
        raise _fail(NARROW_GATE_CHECK_IDS[6], "projection digest set is incomplete")
    return {"projectionCount": len(digests), "contentDigests": digests}


def _search_evidence(
    *,
    base_search_path: Path,
    staged_search_path: Path,
    core: sqlite3.Connection,
    entity_ids: tuple[int, ...],
) -> dict[str, object]:
    if _truth_digest(base_search_path) != _truth_digest(staged_search_path):
        raise _fail(NARROW_GATE_CHECK_IDS[7], "search truth changed out of scope")
    search = _read_only(staged_search_path)
    try:
        observations: list[object] = []
        for entity_id in entity_ids:
            core_row = core.execute(
                """
                SELECT entity_id, canonical_uri, entity_kind,
                       COALESCE(display_name, ''), COALESCE(internal_name, ''),
                       status
                FROM entities WHERE entity_id=?
                """,
                (entity_id,),
            ).fetchone()
            search_row = search.execute(
                "SELECT * FROM entity_search_meta WHERE entity_id=?",
                (entity_id,),
            ).fetchone()
            core_aliases = list(
                core.execute(
                    """
                    SELECT alias, entity_id, alias_kind, language, confidence
                    FROM aliases WHERE entity_id=?
                    ORDER BY alias, entity_id, alias_kind
                    """,
                    (entity_id,),
                )
            )
            search_aliases = list(
                search.execute(
                    """
                    SELECT alias, entity_id, alias_kind, language, confidence
                    FROM search_aliases WHERE entity_id=?
                    ORDER BY alias, entity_id, alias_kind
                    """,
                    (entity_id,),
                )
            )
            fts_count = int(
                search.execute(
                    """
                    SELECT COUNT(*) FROM entities_fts
                    WHERE CAST(entity_id AS INTEGER)=?
                    """,
                    (entity_id,),
                ).fetchone()[0]
            )
            if core_row is None or search_row != core_row or search_aliases != core_aliases or fts_count != 1:
                raise _fail(
                    NARROW_GATE_CHECK_IDS[7],
                    "affected entity search identity is not exact",
                )
            observations.append(
                [list(core_row), [list(row) for row in core_aliases], fts_count]
            )
    finally:
        search.close()
    return {
        "entityIds": list(entity_ids),
        "entitySearchSha256": hashlib.sha256(
            canonical_json_bytes(observations)
        ).hexdigest(),
        "baseTruthSha256": _truth_digest(base_search_path),
    }


def _cache_evidence(
    cache_path: Path,
    *,
    build_id: str,
    source_sha256: str,
) -> dict[str, object]:
    cache = _read_only(cache_path)
    try:
        tables = (
            "query_snapshots",
            "context_packs",
            "answer_plans",
            "materialized_neighborhoods",
        )
        counts = {
            table: int(
                cache.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in tables
        }
        metadata = dict(cache.execute("SELECT key, value FROM metadata"))
    finally:
        cache.close()
    if (
        any(counts.values())
        or metadata.get("disposable") != "true"
        or metadata.get("snapshot_build_id") != build_id
        or metadata.get("snapshot_source_fingerprint") != source_sha256
    ):
        raise _fail(NARROW_GATE_CHECK_IDS[8], "old cache state survived")
    return {
        "tableCounts": counts,
        "metadataIdentity": {
            "disposable": metadata.get("disposable"),
            "snapshotBuildId": metadata.get("snapshot_build_id"),
            "snapshotSourceFingerprint": metadata.get(
                "snapshot_source_fingerprint"
            ),
        },
    }


def run_production_narrow_gates(
    inputs: ProductionNarrowGateInputs,
) -> ProductionNarrowGateRun:
    """Compute all fixed checks; no report exists if one observation fails."""

    if type(inputs) is not ProductionNarrowGateInputs:
        raise TypeError("production narrow-gate inputs are required")
    revision_ids = _integer_tuple(
        inputs.changed_source_revision_ids,
        label="changed source revision IDs",
    )
    entity_ids = _integer_tuple(
        inputs.affected_entity_ids,
        label="affected entity IDs",
    )
    baseline = inputs.baseline
    staged = inputs.staged_snapshot
    if staged.snapshot_dir.is_symlink():
        raise ValueError("staged snapshot path is unsafe")
    staging = staged.snapshot_dir.resolve(strict=True)
    if staging != (staged.temporary_root / "snapshot").resolve():
        raise ValueError("staged snapshot path is not confined")
    validate_final_source_manifest(baseline, inputs.candidate_source_manifest)
    try:
        bound_candidate = source_manifest_from_binding(
            inputs.candidate_manifest.get("incrementalUpdate")
        )
    except ValueError as exc:
        raise _fail(
            NARROW_GATE_CHECK_IDS[10],
            "candidate Source Manifest binding is invalid",
        ) from exc
    previous = inputs.candidate_manifest.get("previousSnapshot")
    if (
        bound_candidate != inputs.candidate_source_manifest
        or not isinstance(previous, Mapping)
        or set(previous) != {"buildId", "manifestSha256"}
        or previous.get("buildId") != baseline.base_build_id
        or previous.get("manifestSha256") != baseline.base_manifest_sha256
    ):
        raise _fail(
            NARROW_GATE_CHECK_IDS[10],
            "candidate lineage does not match the exact update baseline",
        )
    inspection = inspect_base_bound_prepublication_delta_receipt(
        baseline,
        staged_snapshot=staged,
        frozen_input=inputs.frozen_input,
        receipt_bytes=inputs.delta_receipt_bytes,
        expected_receipt_raw_sha256=inputs.delta_receipt_sha256,
    ).payload()
    worker = _worker_payload(inputs.worker_report)
    outcomes = worker.get("outcomes")
    if (
        inspection.get("status") != "FOUNDATION_VERIFIED"
        or inspection.get("baseBindingVerified") is not True
        or inspection.get("blockedGapCount") != 0
        or worker.get("attempted") != worker.get("succeeded")
        or worker.get("failed") != 0
        or worker.get("blocked_gap", worker.get("blockedGap")) != 0
        or worker.get("remaining_pending", worker.get("remainingPending")) != 0
        or worker.get("remaining_running", worker.get("remainingRunning")) != 0
        or worker.get("drained") is not True
        or not isinstance(outcomes, (list, tuple))
        or len(outcomes) != worker.get("succeeded")
        or any(
            not isinstance(outcome, Mapping)
            or outcome.get("status") != "SUCCEEDED"
            or not re.fullmatch(
                r"rebuild-proof://[0-9a-f]{64}",
                str(outcome.get("proof") or ""),
            )
            for outcome in outcomes
        )
    ):
        raise _fail(NARROW_GATE_CHECK_IDS[0], "delta or worker scope is incomplete")
    observations = [
        _evidence(
            NARROW_GATE_CHECK_IDS[0],
            {
                "sourceDiffSha256": baseline.source_diff_sha256,
                "deltaReceiptSha256": inputs.delta_receipt_sha256,
                "attempted": worker.get("attempted"),
                "succeeded": worker.get("succeeded"),
                "workerReceiptSetSha256": hashlib.sha256(
                    canonical_json_bytes(worker)
                ).hexdigest(),
            },
            observation_count=int(worker["succeeded"]),
        )
    ]

    core = _read_only(staging / "core.sqlite")
    try:
        revisions = _changed_revisions_evidence(core, revision_ids)
        observations.append(
            _evidence(
                NARROW_GATE_CHECK_IDS[1],
                revisions,
                observation_count=len(revision_ids),
            )
        )
        stale = active_stale_source_count(core)
        if stale != 0:
            raise _fail(
                NARROW_GATE_CHECK_IDS[2],
                "active stale/candidate/legacy provenance remains",
            )
        observations.append(
            _evidence(
                NARROW_GATE_CHECK_IDS[2],
                {"activeStaleSources": stale},
                observation_count=stale,
            )
        )
        orphan = _orphan_evidence(core)
        observations.append(
            _evidence(
                NARROW_GATE_CHECK_IDS[3],
                orphan,
                observation_count=sum(orphan["tableCounts"].values()),
            )
        )
        dependencies = sorted(validate_effective_resolution_dependencies(core))
        observations.append(
            _evidence(
                NARROW_GATE_CHECK_IDS[4],
                {
                    "dependencyRows": len(dependencies),
                    "dependencySha256": hashlib.sha256(
                        canonical_json_bytes([list(row) for row in dependencies])
                    ).hexdigest(),
                },
                observation_count=len(dependencies),
            )
        )
        registrations = _registration_evidence(core)
        observations.append(
            _evidence(
                NARROW_GATE_CHECK_IDS[5],
                registrations,
                observation_count=int(registrations["registrations"]),
            )
        )
        projections = _projection_evidence(staging, inputs.candidate_manifest)
        observations.append(
            _evidence(
                NARROW_GATE_CHECK_IDS[6],
                projections,
                observation_count=int(projections["projectionCount"]),
            )
        )
        search = _search_evidence(
            base_search_path=(
                baseline.current_snapshot.snapshot_dir / "search.sqlite"
            ),
            staged_search_path=staging / "search.sqlite",
            core=core,
            entity_ids=entity_ids,
        )
        observations.append(
            _evidence(
                NARROW_GATE_CHECK_IDS[7],
                search,
                observation_count=len(entity_ids),
            )
        )
    finally:
        core.close()

    source = inputs.candidate_manifest.get("source")
    if not isinstance(source, Mapping):
        raise _fail(NARROW_GATE_CHECK_IDS[8], "candidate source is missing")
    cache = _cache_evidence(
        staging / "cache.sqlite",
        build_id=str(inputs.candidate_manifest.get("buildId") or ""),
        source_sha256=str(source.get("sha256") or ""),
    )
    observations.append(
        _evidence(
            NARROW_GATE_CHECK_IDS[8],
            cache,
            observation_count=4,
        )
    )
    try:
        validate_snapshot_journal_safety(staging, require_delete=True)
        validate_snapshot_database_schemas(staging)
        _validate_staged_snapshot_for_promotion(
            staging=staging,
            manifest=inputs.candidate_manifest,
        )
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        raise _fail(NARROW_GATE_CHECK_IDS[9], "candidate SQLite seal is invalid") from exc
    observations.append(
        _evidence(
            NARROW_GATE_CHECK_IDS[9],
            {
                "databaseCount": 10,
                "journalMode": "delete",
                "sidecars": 0,
                "integrity": "ok",
                "foreignKeyViolations": 0,
            },
            observation_count=10,
        )
    )
    try:
        validate_update_baseline_identity(
            baseline,
            expected_current_snapshot=baseline.current_snapshot,
            expected_candidate_source_manifest=inputs.candidate_source_manifest,
        )
    except (OSError, ValueError) as exc:
        raise _fail(NARROW_GATE_CHECK_IDS[10], "current base changed") from exc
    observations.append(
        _evidence(
            NARROW_GATE_CHECK_IDS[10],
            {
                "baseBuildId": baseline.base_build_id,
                "basePointerSha256": baseline.base_pointer_sha256,
                "baseManifestSha256": baseline.base_manifest_sha256,
                "candidateBuildId": inputs.candidate_manifest.get("buildId"),
                "candidateSourceManifestFingerprint": (
                    bound_candidate.fingerprint
                ),
            },
            observation_count=1,
        )
    )

    narrow_baseline = NarrowGateUpdateBaseline(
        base_build_id=baseline.base_build_id,
        base_pointer_sha256=baseline.base_pointer_sha256,
        base_manifest_sha256=baseline.base_manifest_sha256,
        base_source_manifest_fingerprint=(
            baseline.base_source_manifest_fingerprint
        ),
        candidate_source_manifest_fingerprint=(
            baseline.candidate_source_manifest_fingerprint
        ),
        source_diff_sha256=baseline.source_diff_sha256,
        delta_receipt_sha256=inputs.delta_receipt_sha256,
    )
    report = build_narrow_gate_diagnostic_report(
        update_baseline=narrow_baseline,
        observations=tuple(observations),
    )
    report_bytes = canonical_json_bytes(report)
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    if report_sha256 != narrow_gate_diagnostic_report_sha256(report):
        raise AssertionError("narrow-gate artifact hash disagreement")
    parse_and_validate_narrow_gate_diagnostic_report_bytes(
        report_bytes,
        expected_report_sha256=report_sha256,
        expected_update_baseline=narrow_baseline,
    )
    return ProductionNarrowGateRun(
        report=report,
        report_bytes=report_bytes,
        report_sha256=report_sha256,
        observations=tuple(observations),
    )


__all__ = [
    "ProductionNarrowGateError",
    "ProductionNarrowGateInputs",
    "ProductionNarrowGateRun",
    "run_production_narrow_gates",
]
