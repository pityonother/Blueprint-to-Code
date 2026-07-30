from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.blueprint_ingest import (  # noqa: E402
    BlueprintIngestResult,
)
from blueprint_translator.kb_vnext.incremental_delta import (  # noqa: E402
    BLOCKED_GAP,
    TEST_ONLY,
)
from blueprint_translator.kb_vnext.invalidation import (  # noqa: E402
    InvalidationPlan,
    plan_additive_asset_invalidation,
)
from blueprint_translator.kb_vnext.projections import (  # noqa: E402
    DOMAIN_PROJECTIONS,
)
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SourceManifest,
    SourceRevision,
    source_id,
    source_manifest_binding,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)
from blueprint_translator.kb_vnext.update_baseline import (  # noqa: E402
    UpdateBaselineBlockedGap,
    build_base_bound_add_only_delta_receipt,
    build_update_baseline,
    cleanup_staged_baseline_snapshot,
    freeze_additive_blueprint_input,
    inspect_base_bound_prepublication_delta_receipt,
    stage_snapshot_from_baseline,
)


GENERATED_AT = "2026-07-30T00:00:00+00:00"
ENTITY_URI = "/Game/Test/Added.Added"
CORE_SOURCE_FINGERPRINT = "c" * 64
EVENT_ID = "test-only-additive-event"
_TOUCHED_TABLE = {
    "FACT": "facts",
    "EFFECTIVE_ENTITY": "effective_facts",
    "ROLE_ENTITY": "knowledge_roles",
    "DOMAIN_ENTITY": "domain_memberships",
    "PROJECTION": "projection_runs",
    "QUERY_SNAPSHOT": "query_snapshots",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pointer_bytes(build_id: str) -> bytes:
    return _canonical_bytes(
        {
            "buildId": build_id,
            "snapshotRelativePath": f"snapshots/{build_id}",
        }
    )


def _semantic(fingerprint: str) -> SourceRevision:
    uri = "semantic-input://captures"
    return SourceRevision(
        source_id=source_id("SEMANTIC_INPUT", uri),
        source_kind="SEMANTIC_INPUT",
        source_uri=uri,
        fingerprint=fingerprint,
    )


def _manifest(*entries: SourceRevision) -> SourceManifest:
    return SourceManifest(
        entries=tuple(entries),
        generated_at=GENERATED_AT,
    )


def _write_core(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(FULL_CORE_SCHEMA_SQL)
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                1, 'discovery', 'discovery://test-only',
                'test-only-discovery-sha', 'test-only-producer',
                'test-only-schema', '2026-07-29T00:00:00Z', 'FRESH'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO entities(
                entity_id, canonical_uri, entity_kind, status, confidence
            ) VALUES (
                1, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH'
            )
            """,
            (ENTITY_URI,),
        )
        connection.commit()
        assert connection.execute(
            "PRAGMA journal_mode=DELETE"
        ).fetchone() == ("delete",)
    finally:
        connection.close()


def _write_evidence(capture_root: Path) -> SourceRevision:
    evidence_dir = capture_root / "Added" / "evidence"
    evidence_dir.mkdir(parents=True)
    evidence_path = evidence_dir / "evidence.sqlite"
    connection = sqlite3.connect(evidence_path)
    try:
        connection.execute(
            """
            CREATE TABLE asset_revisions(
                revision_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                object_path TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                uasset_path TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO asset_revisions VALUES (
                'test-revision', 'test-asset', 'Added', ?, ?,
                'test-only-parser', 'test-only-evidence-schema',
                '2026-07-29T01:00:00Z', 'Added.uasset'
            )
            """,
            (ENTITY_URI, CORE_SOURCE_FINGERPRINT),
        )
        connection.commit()
    finally:
        connection.close()
    manifest_bytes = b'{"bundle":"fixture"}\n'
    (evidence_dir / "manifest.json").write_bytes(manifest_bytes)
    aggregate = hashlib.sha256()
    aggregate.update(b"evidence.sqlite\0")
    aggregate.update(evidence_path.read_bytes())
    aggregate.update(b"\nmanifest.json\0")
    aggregate.update(manifest_bytes)
    aggregate.update(b"\n")
    uri = "capture://Added"
    return SourceRevision(
        source_id=source_id("BLUEPRINT_EVIDENCE", uri),
        source_kind="BLUEPRINT_EVIDENCE",
        source_uri=uri,
        fingerprint=aggregate.hexdigest(),
        size_bytes=evidence_path.stat().st_size,
        entity_uri=ENTITY_URI,
        revision_label="test-revision",
    )


def _materialize_truth(core_path: Path) -> BlueprintIngestResult:
    connection = sqlite3.connect(core_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'blueprint_evidence',
                'bp://test-asset@test-revision', ?,
                'test-only-parser', 'test-only-evidence-schema',
                '2026-07-29T01:00:00Z', 'FRESH'
            )
            """,
            (CORE_SOURCE_FINGERPRINT,),
        )
        connection.execute(
            """
            INSERT INTO facts(
                fact_id, subject_entity_id, fact_type, fact_name,
                scope_kind, declared_on_entity_id, value_kind,
                value_integer, status, confidence, ontology_version,
                current, canonical_fact_key
            ) VALUES (
                101, 1, 'DECLARED_DEFAULT', 'Rate', 'DECLARED', 1,
                'INTEGER', 7, 'CONFIRMED', 'HIGH',
                'test-only-ontology', 1, 'fact://test-only/rate'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO fact_evidence VALUES (
                101, 2, 'bp://test-asset@test-revision/default/Rate',
                'DEFAULT_VALUE_ACTUAL'
            )
            """
        )
        connection.commit()
    finally:
        connection.close()
    return BlueprintIngestResult(
        counts={
            "freshAssets": 1,
            "sourceRevisions": 1,
            "declaredFacts": 1,
            "factEvidence": 1,
        },
        covered_properties=frozenset({(ENTITY_URI, "Rate")}),
        freshness_gap_assets=frozenset(),
        untrusted_assets=frozenset(),
        fact_ids=frozenset({101}),
        entity_ids=frozenset({1}),
    )


def _terminal_receipt(
    *,
    kind: str,
    target_id: int,
    reason: str,
    status: str = "SUCCEEDED",
    gap_code: str = "",
) -> dict[str, object]:
    touched = [] if status == BLOCKED_GAP else [_TOUCHED_TABLE[kind]]
    if kind == "QUERY_SNAPSHOT":
        row_scope: dict[str, object] = {
            "mode": "EXPLICIT_WHOLE_CACHE_BATCH",
            "eventId": EVENT_ID,
            "targetId": target_id,
            "tables": sorted(
                {
                    "query_snapshots",
                    "context_packs",
                    "answer_plans",
                    "materialized_neighborhoods",
                }
            ),
        }
    elif kind == "PROJECTION":
        row_scope = {
            "mode": "EXPLICIT_PROJECTION_BATCH",
            "eventId": EVENT_ID,
            "targetId": target_id,
            "projectionNames": list(DOMAIN_PROJECTIONS),
        }
    else:
        row_scope = {"mode": "TASK_TARGET_ID", "targetId": target_id}
    before = _sha256_bytes(f"{kind}:{target_id}:before".encode())
    after = (
        before
        if status == BLOCKED_GAP
        else _sha256_bytes(f"{kind}:{target_id}:after".encode())
    )
    body: dict[str, object] = {
        "schema": "ark-kb-rebuild-receipt/v1",
        "eventId": EVENT_ID,
        "downstreamKind": kind,
        "downstreamId": target_id,
        "dependencyReason": reason,
        "status": status,
        "beforeDigest": before,
        "afterDigest": after,
        "complete": status == "SUCCEEDED",
        "gapCode": gap_code,
        "detail": "",
        "touchedTables": touched,
        "recovered": False,
        "cacheHit": False,
        "projectionBatch": {},
        "verification": (
            {
                "basis": "TARGET_STATE_CHANGED",
                "coreWriteChanges": 1,
                "writeOperations": [f"{touched[0]}:INSERT"],
                "rowScope": row_scope,
            }
            if touched
            else {}
        ),
    }
    return {
        **body,
        "proof": "rebuild-proof://" + _sha256_bytes(_canonical_bytes(body)),
    }


def _persist_plan(
    core_path: Path,
    *,
    blocked_gap: bool = False,
) -> InvalidationPlan:
    connection = sqlite3.connect(core_path)
    try:
        rows: list[tuple[int, str, int, str]] = []
        for kind, values, reason in (
            ("ROLE_ENTITY", (1,), "ADDITIVE_ROLE_INPUT"),
            ("DOMAIN_ENTITY", (1,), "ADDITIVE_DOMAIN_INPUT"),
            (
                "PROJECTION",
                tuple(range(1, len(DOMAIN_PROJECTIONS) + 1)),
                "ADDITIVE_FACT_PROJECTION",
            ),
            ("QUERY_SNAPSHOT", (2,), "ADDITIVE_QUERY_CACHE"),
        ):
            rows.extend((2, kind, value, reason) for value in values)
        connection.executemany(
            """
            INSERT INTO invalidation_dependencies(
                upstream_revision_id, downstream_kind,
                downstream_id, dependency_reason
            ) VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
        plan = plan_additive_asset_invalidation(
            connection,
            fact_ids=(101,),
            entity_ids=(1,),
            source_revision_ids=(2,),
            actual_write_tables=(
                "fact_evidence",
                "facts",
                "source_revisions",
            ),
        )
        receipts = [
            _terminal_receipt(
                kind=kind,
                target_id=target_id,
                reason=plan.reasons[kind],
                status=(
                    BLOCKED_GAP
                    if blocked_gap and kind == "QUERY_SNAPSHOT"
                    else "SUCCEEDED"
                ),
                gap_code=(
                    "QUERY_BACKEND_UNAVAILABLE"
                    if blocked_gap and kind == "QUERY_SNAPSHOT"
                    else ""
                ),
            )
            for kind, values in sorted(plan.downstream.items())
            for target_id in values
        ]
        payload = {
            **{
                kind: list(values)
                for kind, values in sorted(plan.downstream.items())
            },
            "_rebuildReceipts": {
                f"{receipt['downstreamKind']}:{receipt['downstreamId']}": (
                    receipt
                )
                for receipt in receipts
            },
        }
        queue_rows = [
            (
                EVENT_ID,
                str(receipt["downstreamKind"]),
                int(receipt["downstreamId"]),
                str(receipt["dependencyReason"]),
                str(receipt["status"]),
            )
            for receipt in receipts
        ]
        event_status = BLOCKED_GAP if blocked_gap else "SUCCEEDED"
        connection.execute(
            """
            INSERT INTO invalidation_events(
                event_id, event_kind, upstream_revision_id,
                payload_json, created_at, status
            ) VALUES (?, 'ASSET', NULL, ?, ?, ?)
            """,
            (
                EVENT_ID,
                _canonical_bytes(payload).decode("utf-8"),
                "2026-07-29T02:00:00Z",
                event_status,
            ),
        )
        connection.executemany(
            """
            INSERT INTO invalidation_queue(
                event_id, downstream_kind, downstream_id,
                dependency_reason, status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            queue_rows,
        )
        connection.commit()
        return plan
    finally:
        connection.close()


def _fixture(
    tmp_path: Path,
    *,
    build_id: str = "build-a",
    blocked_gap: bool = False,
) -> dict[str, object]:
    root = tmp_path / "vnext"
    snapshot = root / "snapshots" / build_id
    core_path = snapshot / "core.sqlite"
    _write_core(core_path)
    base_manifest = _manifest(_semantic("1" * 64))
    manifest = {
        "schema": "ark-kb-vnext-snapshot/v1",
        "buildId": build_id,
        "databases": {
            "core.sqlite": {
                "bytes": core_path.stat().st_size,
                "sha256": _sha256_bytes(core_path.read_bytes()),
            }
        },
        "qualityGates": {
            "sealedInSnapshotManifest": True,
            "cutoverEligible": False,
        },
        "cutover": {
            "mode": "shadow",
            "defaultQuerySource": "legacy",
        },
        "incrementalUpdate": source_manifest_binding(base_manifest),
    }
    (snapshot / "manifest.json").write_bytes(_canonical_bytes(manifest))
    root.mkdir(exist_ok=True)
    (root / "current.json").write_bytes(_pointer_bytes(build_id))
    capture_root = tmp_path / "captures"
    revision = _write_evidence(capture_root)
    candidate = _manifest(_semantic("2" * 64), revision)
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=candidate,
    )
    staged = stage_snapshot_from_baseline(
        baseline,
        destination=root / ".incremental-staging",
    )
    frozen = freeze_additive_blueprint_input(
        baseline,
        capture_root=capture_root,
        staged_snapshot=staged,
    )
    result = _materialize_truth(staged.snapshot_dir / "core.sqlite")
    plan = _persist_plan(
        staged.snapshot_dir / "core.sqlite",
        blocked_gap=blocked_gap,
    )
    return {
        "root": root,
        "baseline": baseline,
        "staged": staged,
        "frozen": frozen,
        "result": result,
        "plan": plan,
    }


def _build(fixture: dict[str, object]) -> dict[str, object]:
    return build_base_bound_add_only_delta_receipt(
        fixture["baseline"],
        staged_snapshot=fixture["staged"],
        frozen_input=fixture["frozen"],
        ingest_result=fixture["result"],
        invalidation_plan=fixture["plan"],
        backend_event_id=EVENT_ID,
    )


def _cleanup(fixture: dict[str, object]) -> None:
    cleanup_staged_baseline_snapshot(
        fixture["staged"],
        snapshot_root=fixture["root"],
    )


def _mutated_receipt(
    receipt: dict[str, object],
    path: str,
    value: object,
) -> dict[str, object]:
    mutated = copy.deepcopy(receipt)
    cursor: dict[str, object] = mutated
    parts = path.split(".")
    for part in parts[:-1]:
        child = cursor[part]
        assert isinstance(child, dict)
        cursor = child
    cursor[parts[-1]] = value
    body = dict(mutated)
    body.pop("proof")
    mutated["proof"] = "delta-proof://" + _sha256_bytes(
        _canonical_bytes(body)
    )
    return mutated


def test_builds_and_inspects_v3_receipt_bound_to_live_base_and_staging(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        receipt = _build(fixture)
        raw = _canonical_bytes(receipt)
        inspection = inspect_base_bound_prepublication_delta_receipt(
            fixture["baseline"],
            staged_snapshot=fixture["staged"],
            frozen_input=fixture["frozen"],
            receipt_bytes=raw,
            expected_receipt_raw_sha256=_sha256_bytes(raw),
        )

        assert receipt["schema"] == (
            "ark-kb-add-only-blueprint-delta-receipt/v3"
        )
        assert inspection.schema == (
            "ark-kb-prepublication-delta-inspection/v2"
        )
        assert inspection.base_binding_verified is True
        assert inspection.production_authority is False
        assert inspection.published is False
        assert inspection.e4_scenario_2_complete is False
    finally:
        _cleanup(fixture)


def test_v3_receipt_has_no_absolute_paths_or_publication_authority(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        receipt = _build(fixture)
        serialized = _canonical_bytes(receipt).decode("utf-8")

        assert str(tmp_path) not in serialized
        assert receipt["trustContext"] == TEST_ONLY
        assert receipt["productionAuthority"] is False
        assert receipt["published"] is False
        assert receipt["e4Scenario2Complete"] is False
        assert receipt["cutoverEligible"] is False
        assert receipt["mode"] == "shadow"
        assert receipt["defaultQuerySource"] == "legacy"
    finally:
        _cleanup(fixture)


def test_strict_inspector_rejects_v2_schema_even_with_recomputed_proof(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        receipt = _build(fixture)
        body = dict(receipt)
        body.pop("proof")
        body["schema"] = "ark-kb-add-only-blueprint-delta-receipt/v2"
        forged = {
            **body,
            "proof": "delta-proof://" + _sha256_bytes(
                _canonical_bytes(body)
            ),
        }
        raw = _canonical_bytes(forged)

        with pytest.raises(UpdateBaselineBlockedGap) as caught:
            inspect_base_bound_prepublication_delta_receipt(
                fixture["baseline"],
                staged_snapshot=fixture["staged"],
                frozen_input=fixture["frozen"],
                receipt_bytes=raw,
                expected_receipt_raw_sha256=_sha256_bytes(raw),
            )

        assert caught.value.gap_code == "DELTA_RECEIPT_ARTIFACT_INVALID"
    finally:
        _cleanup(fixture)


def test_same_source_diff_receipt_cannot_replay_on_another_base(
    tmp_path: Path,
) -> None:
    first = _fixture(tmp_path / "first", build_id="build-a")
    second = _fixture(tmp_path / "second", build_id="build-b")
    try:
        receipt = _build(first)
        raw = _canonical_bytes(receipt)

        with pytest.raises(UpdateBaselineBlockedGap) as caught:
            inspect_base_bound_prepublication_delta_receipt(
                second["baseline"],
                staged_snapshot=second["staged"],
                frozen_input=second["frozen"],
                receipt_bytes=raw,
                expected_receipt_raw_sha256=_sha256_bytes(raw),
            )

        assert caught.value.gap_code == "DELTA_RECEIPT_BASE_BINDING_MISMATCH"
    finally:
        _cleanup(first)
        _cleanup(second)


def test_oob_raw_hash_is_checked_before_receipt_semantics(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        raw = b'{"schema":"not-a-receipt"}'

        with pytest.raises(UpdateBaselineBlockedGap) as caught:
            inspect_base_bound_prepublication_delta_receipt(
                fixture["baseline"],
                staged_snapshot=fixture["staged"],
                frozen_input=fixture["frozen"],
                receipt_bytes=raw,
                expected_receipt_raw_sha256="0" * 64,
            )

        assert caught.value.gap_code == (
            "OUT_OF_BAND_DELTA_RECEIPT_SHA256_MISMATCH"
        )
    finally:
        _cleanup(fixture)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("baseBinding.baseBuildId", "build-replayed"),
        ("baseBinding.pointerSha256", "0" * 64),
        ("baseBinding.manifestSha256", "0" * 64),
        ("baseBinding.manifestBytes", 1),
        ("baseBinding.baseSourceManifestFingerprint", "0" * 64),
        ("baseBinding.candidateSourceManifestFingerprint", "0" * 64),
        ("baseBinding.sourceDiffSha256", "0" * 64),
        ("baseBinding.updateBaselineIdentitySha256", "0" * 64),
        (
            "baseBinding.baseCore.relativePath",
            "snapshots/replayed/core.sqlite",
        ),
        ("baseBinding.baseCore.manifestSha256", "0" * 64),
        ("baseBinding.baseCore.manifestBytes", 1),
        ("baseBinding.baseCore.observedRawSha256", "0" * 64),
        ("baseBinding.baseCore.logicalDatabaseSha256", "0" * 64),
        ("baseBinding.baseCore.fileIdentitySha256", "0" * 64),
        ("baseBinding.staging.stagingId", "0" * 32),
        (
            "baseBinding.staging.receiptProof",
            "staging-proof://" + "0" * 64,
        ),
        ("baseBinding.staging.sourceTreeDigest", "0" * 64),
        ("baseBinding.staging.stagedTreeDigest", "0" * 64),
        ("baseBinding.staging.authorityDigest", "0" * 64),
        (
            "baseBinding.staging.coreRelativePath",
            ".incremental-staging/000/snapshot/core.sqlite",
        ),
        ("baseBinding.staging.coreRawSha256", "0" * 64),
        ("baseBinding.staging.coreFileIdentitySha256", "0" * 64),
        ("baseBinding.staging.coreLogicalDatabaseSha256", "0" * 64),
        ("baseBinding.staging.physicallyIndependent", False),
        ("baseBinding.staging.sameVolume", False),
        (
            "baseBinding.quarantine.receiptProof",
            "quarantine-proof://" + "0" * 64,
        ),
        ("baseBinding.quarantine.treeDigest", "0" * 64),
        ("baseBinding.quarantine.sourceId", "0" * 64),
        ("baseBinding.quarantine.entityUri", "/Game/Replay.Replay"),
        ("baseBinding.quarantine.revisionLabel", "replayed"),
        ("baseBinding.quarantine.sourceFingerprint", "0" * 64),
        ("baseBinding.quarantine.sourceAggregateSha256", "0" * 64),
        (
            "baseBinding.quarantine.artifact.artifactUri",
            "artifact://replayed/evidence.sqlite",
        ),
        ("baseBinding.quarantine.artifact.artifactSha256", "0" * 64),
        ("baseBinding.quarantine.artifact.artifactBytes", 1),
        (
            "baseBinding.quarantine.artifact.fileIdentitySha256",
            "0" * 64,
        ),
        (
            "baseBinding.quarantine.manifestArtifact.artifactSha256",
            "0" * 64,
        ),
        (
            "baseBinding.quarantine.manifestArtifact.fileIdentitySha256",
            "0" * 64,
        ),
        ("sourceDiffSha256", "0" * 64),
        ("beforeDatabaseSha256", "0" * 64),
        ("afterDatabaseSha256", "0" * 64),
        ("receiptDatabaseSha256", "0" * 64),
        ("protectedTableSha256.facts", "0" * 64),
        ("backendEvent.eventId", "replayed-event"),
        ("backendEvent.eventPayloadSha256", "0" * 64),
    ],
)
def test_strict_inspector_rejects_replayed_binding_component(
    tmp_path: Path,
    path: str,
    value: object,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        receipt = _mutated_receipt(_build(fixture), path, value)
        raw = _canonical_bytes(receipt)

        with pytest.raises(UpdateBaselineBlockedGap):
            inspect_base_bound_prepublication_delta_receipt(
                fixture["baseline"],
                staged_snapshot=fixture["staged"],
                frozen_input=fixture["frozen"],
                receipt_bytes=raw,
                expected_receipt_raw_sha256=_sha256_bytes(raw),
            )
    finally:
        _cleanup(fixture)


def test_blocked_gap_still_gets_valid_base_bound_receipt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, blocked_gap=True)
    try:
        receipt = _build(fixture)
        raw = _canonical_bytes(receipt)
        inspection = inspect_base_bound_prepublication_delta_receipt(
            fixture["baseline"],
            staged_snapshot=fixture["staged"],
            frozen_input=fixture["frozen"],
            receipt_bytes=raw,
            expected_receipt_raw_sha256=_sha256_bytes(raw),
        )

        assert receipt["status"] == BLOCKED_GAP
        assert receipt["blockedGaps"] == ["QUERY_BACKEND_UNAVAILABLE"]
        assert inspection.status == BLOCKED_GAP
        assert inspection.blocked_gap_count == 1
        assert inspection.base_binding_verified is True
    finally:
        _cleanup(fixture)


def test_same_bytes_at_replaced_staged_core_identity_are_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    core_path = fixture["staged"].snapshot_dir / "core.sqlite"
    replacement = core_path.with_suffix(".replacement")
    replacement.write_bytes(core_path.read_bytes())
    os.replace(replacement, core_path)
    try:
        with pytest.raises(UpdateBaselineBlockedGap) as caught:
            _build(fixture)

        assert caught.value.gap_code == (
            "DELTA_STAGED_CORE_IDENTITY_INVALID"
        )
    finally:
        _cleanup(fixture)


def test_same_bytes_at_replaced_quarantine_identity_are_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    evidence = fixture["frozen"].ingest_root / "evidence.sqlite"
    replacement = evidence.with_suffix(".replacement")
    replacement.write_bytes(evidence.read_bytes())
    os.replace(replacement, evidence)
    try:
        with pytest.raises(UpdateBaselineBlockedGap):
            _build(fixture)
    finally:
        _cleanup(fixture)


def test_pointer_mutation_during_receipt_build_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate(phase: str) -> None:
        if phase == "before_final_file_observation":
            (fixture["root"] / "current.json").write_bytes(
                _pointer_bytes("build-replayed")
            )

    try:
        with pytest.raises(
            (UpdateBaselineBlockedGap, ValueError)
        ):
            build_base_bound_add_only_delta_receipt(
                fixture["baseline"],
                staged_snapshot=fixture["staged"],
                frozen_input=fixture["frozen"],
                ingest_result=fixture["result"],
                invalidation_plan=fixture["plan"],
                backend_event_id=EVENT_ID,
                fault_injector=mutate,
            )
    finally:
        _cleanup(fixture)


@pytest.mark.parametrize("target", ["base", "staged"])
def test_core_mutation_during_receipt_build_is_rejected(
    tmp_path: Path,
    target: str,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate(phase: str) -> None:
        if phase != "after_initial_file_observation":
            return
        path = (
            fixture["baseline"].current_snapshot.snapshot_dir
            if target == "base"
            else fixture["staged"].snapshot_dir
        ) / "core.sqlite"
        connection = sqlite3.connect(path)
        try:
            if target == "base":
                connection.execute(
                    """
                    UPDATE entities SET confidence='MEDIUM'
                    WHERE entity_id=1
                    """
                )
            else:
                connection.execute(
                    """
                    UPDATE facts SET value_integer=8
                    WHERE fact_id=101
                    """
                )
            connection.commit()
        finally:
            connection.close()

    try:
        with pytest.raises(UpdateBaselineBlockedGap):
            build_base_bound_add_only_delta_receipt(
                fixture["baseline"],
                staged_snapshot=fixture["staged"],
                frozen_input=fixture["frozen"],
                ingest_result=fixture["result"],
                invalidation_plan=fixture["plan"],
                backend_event_id=EVENT_ID,
                fault_injector=mutate,
            )
    finally:
        _cleanup(fixture)


def test_staged_database_mutation_after_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    try:
        receipt = _build(fixture)
        connection = sqlite3.connect(
            fixture["staged"].snapshot_dir / "core.sqlite"
        )
        try:
            connection.execute(
                "UPDATE facts SET value_integer=8 WHERE fact_id=101"
            )
            connection.commit()
        finally:
            connection.close()
        raw = _canonical_bytes(receipt)

        with pytest.raises(UpdateBaselineBlockedGap):
            inspect_base_bound_prepublication_delta_receipt(
                fixture["baseline"],
                staged_snapshot=fixture["staged"],
                frozen_input=fixture["frozen"],
                receipt_bytes=raw,
                expected_receipt_raw_sha256=_sha256_bytes(raw),
            )
    finally:
        _cleanup(fixture)
