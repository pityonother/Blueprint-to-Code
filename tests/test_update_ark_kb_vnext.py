from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import update_ark_kb_vnext as update
from blueprint_translator.kb_vnext import (  # noqa: E402
    source_manifest as source_manifest_module,
)
from blueprint_translator.kb_vnext.blueprint_ingest import (  # noqa: E402
    BlueprintIngestResult,
)
from blueprint_translator.kb_vnext.native_ingest import (  # noqa: E402
    NativeEvidenceSet,
)
from blueprint_translator.kb_vnext.roles import (  # noqa: E402
    materialize_discovery_roles,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    CACHE_SCHEMA_SQL,
    FULL_CORE_SCHEMA_SQL,
)


def _revision(
    fingerprint: str,
    *,
    kind: str = "BLUEPRINT_EVIDENCE",
    uri: str = "capture://Demo",
    entity_uri: str = "/Game/Demo",
) -> update.SourceRevision:
    stable_fingerprint = hashlib.sha256(
        fingerprint.encode("utf-8")
    ).hexdigest()
    return update.SourceRevision(
        source_id=update._source_id(kind, uri),
        source_kind=kind,
        source_uri=uri,
        fingerprint=stable_fingerprint,
        size_bytes=10,
        entity_uri=entity_uri,
        revision_label=f"revision-{stable_fingerprint}",
    )


def _semantic(key: str, fingerprint: str) -> update.SourceRevision:
    return _revision(
        fingerprint,
        kind="SEMANTIC_INPUT",
        uri=f"semantic-input://{key}",
        entity_uri="",
    )


def _manifest(
    *entries: update.SourceRevision,
) -> update.SourceManifest:
    return update.SourceManifest(
        entries=tuple(entries),
        generated_at="2026-07-28T00:00:00+00:00",
    )


def _paths(tmp_path: Path) -> update.UpdatePaths:
    return update.UpdatePaths(
        discovery_database=tmp_path / "discovery.sqlite",
        capture_root=tmp_path / "captures",
        native_root=tmp_path / "native",
        runtime_root=tmp_path / "runtime",
        legacy_kb_root=tmp_path / "legacy",
        map_evidence_catalog=tmp_path / "map.json",
        output=tmp_path / "output",
    )


def _workspace(tmp_path: Path) -> update.UpdateWorkspace:
    root = tmp_path / "output" / ".incremental-staging" / "work"
    root.mkdir(parents=True, exist_ok=True)
    snapshot = root / "snapshot"
    snapshot.mkdir(exist_ok=True)
    core = snapshot / "core.sqlite"
    connection = sqlite3.connect(core)
    connection.execute(
        "CREATE TABLE phase_log(position INTEGER PRIMARY KEY, phase TEXT)"
    )
    connection.commit()
    connection.close()
    return update.UpdateWorkspace(
        temporary_root=root,
        snapshot_dir=snapshot,
        core_path=core,
        cache_path=snapshot / "cache.sqlite",
        projection_dir=snapshot / "domain_exports",
    )


def _fixture_staging_receipt(
    *,
    build_id: str = "fixture-base",
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": "ark-kb-reparse-safe-staging-receipt/v1",
        "evidenceClass": "UNSIGNED_LOCAL_REPARSE_SAFE_STAGING",
        "baseBuildId": build_id,
        "pointerSha256": "1" * 64,
        "manifestSha256": "2" * 64,
        "baseSourceManifestFingerprint": "3" * 64,
        "sourceManifestFingerprint": "4" * 64,
        "sourceDiffSha256": "5" * 64,
        "updateBaselineIdentitySha256": "6" * 64,
        "sourceTreeDigest": "7" * 64,
        "stagedTreeDigest": "7" * 64,
        "authorityDigest": "8" * 64,
        "coreFileIdentitySha256": "9" * 64,
        "sameVolume": True,
        "sourceVerifiedUnchanged": True,
        "reparsePointCount": 0,
        "hardlinkAliasCount": 0,
        "copiedAuthorityFileCount": 2,
        "copiedNonAuthorityFileCount": 1,
        "cacheDisposition": "COPIED_BUILD_BOUND_DISPOSABLE",
        "fileCount": 3,
        "totalBytes": 100,
        "createdAt": "2026-07-30T00:00:00+00:00",
        "stagingRelativePath": (
            ".incremental-staging/"
            "0123456789abcdef0123456789abcdef/snapshot"
        ),
        "published": False,
        "productionAuthority": False,
        "e4Scenario2Complete": False,
        "cutoverEligible": False,
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    proof = hashlib.sha256(
        json.dumps(
            body,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {**body, "proof": f"staging-proof://{proof}"}


QUERY_CACHE_TABLES = (
    "query_snapshots",
    "context_packs",
    "answer_plans",
    "materialized_neighborhoods",
)


def _write_query_cache_fixture(path: Path, *, label: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(CACHE_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            ("business_metadata", label),
        )
        connection.execute(
            """
            INSERT INTO query_snapshots VALUES (
                ?, ?, '{}', '{}', 'source-set', 'token',
                '2026-07-30T00:00:00Z', '2026-07-31T00:00:00Z',
                'FRESH'
            )
            """,
            (f"{label}-snapshot", f"{label}-query"),
        )
        connection.execute(
            """
            INSERT INTO context_packs VALUES (
                ?, ?, 'context', 10, 1, 0, '2026-07-30T00:00:00Z'
            )
            """,
            (f"{label}-context", f"{label}-snapshot"),
        )
        connection.execute(
            """
            INSERT INTO answer_plans VALUES (
                ?, ?, '{}', 'source-set', 'token',
                '2026-07-30T00:00:00Z', '2026-07-31T00:00:00Z'
            )
            """,
            (f"{label}-plan", f"{label}-query"),
        )
        connection.execute(
            """
            INSERT INTO materialized_neighborhoods VALUES (
                ?, 1, 1, '[]', '{}', 'source-set', 'token',
                '2026-07-30T00:00:00Z', '2026-07-31T00:00:00Z'
            )
            """,
            (f"{label}-neighborhood",),
        )
        connection.commit()
    finally:
        connection.close()


def _queue_query_snapshot(core_path: Path, *, target_id: int) -> None:
    connection = sqlite3.connect(core_path)
    try:
        connection.execute(
            """
            INSERT INTO invalidation_events VALUES (
                'event-query', 'TEST', NULL, '{}',
                '2026-07-30T00:00:00Z', 'APPLIED'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO invalidation_queue VALUES (
                'event-query', 'QUERY_SNAPSHOT', ?,
                'ADDITIVE_QUERY_CACHE', 'PENDING_REBUILD'
            )
            """,
            (target_id,),
        )
        connection.commit()
    finally:
        connection.close()


def _query_cache_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
            )
            for table in QUERY_CACHE_TABLES
        }
    finally:
        connection.close()


def _production_workspace(tmp_path: Path) -> update.UpdateWorkspace:
    staging_id = "0123456789abcdef0123456789abcdef"
    root = (
        tmp_path
        / "output"
        / ".incremental-staging"
        / staging_id
    )
    snapshot = root / "snapshot"
    projection_dir = snapshot / "domain_exports"
    projection_dir.mkdir(parents=True)
    core_path = snapshot / "core.sqlite"
    core = sqlite3.connect(core_path)
    core.execute("PRAGMA foreign_keys=ON")
    core.executescript(FULL_CORE_SCHEMA_SQL)
    core.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'discovery', 'discovery://fixture', 'discovery-sha',
            'fixture', 'v1', '2026-07-28T00:00:00Z', 'FRESH'
        )
        """
    )
    core.execute(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (
            1, '/Game/Test/Added.Added', 'BLUEPRINT_ASSET',
            'CONFIRMED', 'HIGH'
        )
        """
    )
    core.execute(
        """
        INSERT INTO classes(
            class_id, class_path, class_name, module_or_package,
            class_kind, is_native, source_revision_id, status, confidence
        ) VALUES (
            1, '/Script/Test.Fixture', 'Fixture', 'Test',
            'NATIVE', 1, 1, 'IDENTIFIED', 'HIGH'
        )
        """
    )
    core.execute(
        "INSERT INTO class_closure VALUES (1, 1, 0, 'SELF')"
    )
    core.execute(
        """
        INSERT INTO asset_class_assignments VALUES (
            1, 1, 'GENERATED_CLASS', 'fixture://class',
            'EXTRACTED', 'HIGH', 1
        )
        """
    )
    core.commit()
    core.close()
    cache_path = snapshot / "cache.sqlite"
    _write_query_cache_fixture(cache_path, label="staging")
    return update.UpdateWorkspace(
        temporary_root=root,
        snapshot_dir=snapshot,
        core_path=core_path,
        cache_path=cache_path,
        projection_dir=projection_dir,
        base_build_id="fixture-base",
        staging_receipt=_fixture_staging_receipt(),
    )


def _bind_production_staged_baseline(
    workspace: update.UpdateWorkspace,
) -> None:
    workspace.staged_baseline = update.StagedBaselineSnapshot(
        base_build_id=workspace.base_build_id,
        staging_id="0123456789abcdef0123456789abcdef",
        temporary_root=workspace.temporary_root,
        snapshot_dir=workspace.snapshot_dir,
        manifest_sha256="2" * 64,
        copied_files=3,
        receipt=workspace.staging_receipt,
        cleanup_identity=(),
    )


def test_production_query_backend_uses_only_staged_cache_and_worker_receipt(
    tmp_path: Path,
) -> None:
    workspace = _production_workspace(tmp_path)
    _bind_production_staged_baseline(workspace)
    target_id = 576701
    _queue_query_snapshot(workspace.core_path, target_id=target_id)
    current_cache = (
        tmp_path
        / "output"
        / "snapshots"
        / "fixture-base"
        / "cache.sqlite"
    )
    current_cache.parent.mkdir(parents=True)
    _write_query_cache_fixture(current_cache, label="current")
    current_cache_sha256 = hashlib.sha256(
        current_cache.read_bytes()
    ).hexdigest()

    report = update.drain_production_rebuilds(workspace, 1)

    assert report.succeeded == 1
    assert report.blocked_gap == 0
    assert report.failed == 0
    assert report.outcomes[0].cache_hit is False
    assert report.outcomes[0].touched_tables == tuple(
        sorted(QUERY_CACHE_TABLES)
    )
    assert _query_cache_counts(workspace.cache_path) == {
        table: 0 for table in QUERY_CACHE_TABLES
    }
    assert _query_cache_counts(current_cache) == {
        table: 1 for table in QUERY_CACHE_TABLES
    }
    assert (
        hashlib.sha256(current_cache.read_bytes()).hexdigest()
        == current_cache_sha256
    )
    cache = sqlite3.connect(workspace.cache_path)
    try:
        metadata = dict(cache.execute("SELECT key, value FROM metadata"))
    finally:
        cache.close()
    assert metadata["business_metadata"] == "staging"
    assert any(
        key.startswith("_rebuild_worker_marker:") for key in metadata
    )
    core = sqlite3.connect(workspace.core_path)
    try:
        event_payload = json.loads(
            core.execute(
                """
                SELECT payload_json
                FROM invalidation_events
                WHERE event_id='event-query'
                """
            ).fetchone()[0]
        )
    finally:
        core.close()
    receipt = event_payload["_rebuildReceipts"][
        f"QUERY_SNAPSHOT:{target_id}"
    ]
    assert receipt["downstreamKind"] == "QUERY_SNAPSHOT"
    assert receipt["downstreamId"] == target_id
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["complete"] is True
    assert receipt["cacheHit"] is False
    assert receipt["touchedTables"] == sorted(QUERY_CACHE_TABLES)
    assert receipt["verification"]["rowScope"] == {
        "mode": "EXPLICIT_WHOLE_CACHE_BATCH",
        "eventId": "event-query",
        "targetId": target_id,
        "tables": sorted(QUERY_CACHE_TABLES),
    }
    assert receipt["proof"].startswith("rebuild-proof://")


def test_production_query_backend_proves_already_empty_cache_invalidation(
    tmp_path: Path,
) -> None:
    workspace = _production_workspace(tmp_path)
    _bind_production_staged_baseline(workspace)
    cache = sqlite3.connect(workspace.cache_path)
    try:
        cache.execute("PRAGMA foreign_keys=ON")
        for table in (
            "context_packs",
            "answer_plans",
            "materialized_neighborhoods",
            "query_snapshots",
        ):
            cache.execute(f'DELETE FROM "{table}"')
        cache.commit()
    finally:
        cache.close()
    _queue_query_snapshot(workspace.core_path, target_id=576701)

    report = update.drain_production_rebuilds(workspace, 1)

    assert report.succeeded == 1
    assert report.failed == 0
    assert report.blocked_gap == 0
    assert report.outcomes[0].cache_hit is False
    assert report.outcomes[0].touched_tables == tuple(
        sorted(QUERY_CACHE_TABLES)
    )
    core = sqlite3.connect(workspace.core_path)
    try:
        payload = json.loads(
            core.execute(
                """
                SELECT payload_json
                FROM invalidation_events
                WHERE event_id='event-query'
                """
            ).fetchone()[0]
        )
    finally:
        core.close()
    receipt = payload["_rebuildReceipts"]["QUERY_SNAPSHOT:576701"]
    assert receipt["verification"]["basis"] == (
        "EXPLICIT_WHOLE_CACHE_INVALIDATION"
    )
    assert receipt["verification"]["writeOperations"] == [
        f"{table}:DELETE" for table in sorted(QUERY_CACHE_TABLES)
    ]


def test_production_query_backend_rejects_cache_outside_staged_snapshot(
    tmp_path: Path,
) -> None:
    workspace = _production_workspace(tmp_path)
    _bind_production_staged_baseline(workspace)
    outside_cache = tmp_path / "current-cache.sqlite"
    _write_query_cache_fixture(outside_cache, label="current")
    outside_sha256 = hashlib.sha256(
        outside_cache.read_bytes()
    ).hexdigest()
    workspace.cache_path = outside_cache

    with pytest.raises(update.UpdateBlocked) as caught:
        update.drain_production_rebuilds(workspace, 1)

    assert caught.value.gap_code == "QUERY_CACHE_PATH_OUTSIDE_STAGING"
    assert (
        hashlib.sha256(outside_cache.read_bytes()).hexdigest()
        == outside_sha256
    )


@pytest.mark.parametrize(
    "missing_table",
    (
        "metadata",
        "query_snapshots",
        "context_packs",
        "answer_plans",
        "materialized_neighborhoods",
    ),
)
def test_production_query_backend_rejects_incomplete_cache_schema(
    tmp_path: Path,
    missing_table: str,
) -> None:
    workspace = _production_workspace(tmp_path)
    _bind_production_staged_baseline(workspace)
    connection = sqlite3.connect(workspace.cache_path)
    try:
        connection.execute(f'DROP TABLE "{missing_table}"')
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(update.UpdateBlocked) as caught:
        update.drain_production_rebuilds(workspace, 1)

    assert caught.value.gap_code == "QUERY_CACHE_SCHEMA_INVALID"


def test_production_query_backend_rejects_missing_or_corrupt_cache(
    tmp_path: Path,
) -> None:
    missing_workspace = _production_workspace(tmp_path / "missing")
    _bind_production_staged_baseline(missing_workspace)
    missing_workspace.cache_path.unlink()

    with pytest.raises(update.UpdateBlocked) as missing:
        update.drain_production_rebuilds(missing_workspace, 1)

    assert missing.value.gap_code == "QUERY_CACHE_NOT_AVAILABLE"

    corrupt_workspace = _production_workspace(tmp_path / "corrupt")
    _bind_production_staged_baseline(corrupt_workspace)
    corrupt_workspace.cache_path.write_bytes(b"not a sqlite database")

    with pytest.raises(update.UpdateBlocked) as corrupt:
        update.drain_production_rebuilds(corrupt_workspace, 1)

    assert corrupt.value.gap_code == "QUERY_CACHE_INTEGRITY_FAILED"


def test_production_query_backend_revalidates_staging_receipt(
    tmp_path: Path,
) -> None:
    workspace = _production_workspace(tmp_path)
    _bind_production_staged_baseline(workspace)
    workspace.staging_receipt = {}

    with pytest.raises(update.UpdateBlocked) as caught:
        update.drain_production_rebuilds(workspace, 1)

    assert caught.value.gap_code == "STAGING_RECEIPT_INVALID"


def _default_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    previous: update.SourceManifest | None,
    current: update.SourceManifest,
) -> tuple[dict[str, object], list[str]]:
    calls: list[str] = []
    monkeypatch.setattr(
        update,
        "load_current_source_manifest",
        lambda paths: previous,
    )
    monkeypatch.setattr(
        update,
        "scan_source_manifest",
        lambda paths: current,
    )

    def forbidden_stage(paths: update.UpdatePaths) -> update.UpdateWorkspace:
        del paths
        calls.append("stage")
        raise AssertionError("default preflight must stop before staging")

    monkeypatch.setattr(update, "_unavailable_stage", forbidden_stage)
    result = update.run_incremental_update(_paths(tmp_path))
    return result, calls


def _fixture_ingest_receipt() -> dict[str, object]:
    body: dict[str, object] = {
        "schema": "ark-kb-additive-blueprint-ingest-receipt/v1",
        "verifiedSources": 1,
        "affectedEntities": 1,
        "materializedFacts": 1,
        "factEvidence": 1,
        "eventId": "invalidation://fixture",
        "sourceIds": ["0" * 64],
    }
    proof = "ingest-proof://" + hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **body,
        "completed": True,
        "proof": proof,
    }


def _success_hooks(
    tmp_path: Path,
    previous: update.SourceManifest,
    current: update.SourceManifest,
    phases: list[str],
    *,
    gate_passed: bool = True,
    worker_blocked: bool = False,
) -> update.UpdateHooks:
    registry = tmp_path / "atomic-publication.sqlite"

    def stage(paths: update.UpdatePaths) -> update.UpdateWorkspace:
        del paths
        phases.append("stage")
        return _workspace(tmp_path)

    def plan(
        workspace: update.UpdateWorkspace,
        diff: update.SourceDiff,
    ) -> list[dict[str, object]]:
        del diff
        phases.append("plan")
        connection = sqlite3.connect(workspace.core_path)
        connection.execute(
            "INSERT INTO phase_log(phase) VALUES ('planned')"
        )
        connection.commit()
        connection.close()
        return [{"eventKind": "ASSET", "affected": 1}]

    def ingest(
        workspace: update.UpdateWorkspace,
        diff: update.SourceDiff,
        paths: update.UpdatePaths,
    ) -> dict[str, object]:
        del diff, paths
        phases.append("ingest")
        connection = sqlite3.connect(workspace.core_path)
        assert connection.execute(
            "SELECT phase FROM phase_log"
        ).fetchall() == [("planned",)]
        connection.close()
        return _fixture_ingest_receipt()

    def drain(
        workspace: update.UpdateWorkspace,
        max_items: int,
    ) -> dict[str, object]:
        del workspace
        assert max_items == 10_000
        phases.append("worker")
        return {
            "attempted": 1,
            "succeeded": 0 if worker_blocked else 1,
            "failed": 0,
            "blocked_gap": 1 if worker_blocked else 0,
            "remaining_pending": 0,
            "remaining_running": 0,
        }

    def gates(workspace: update.UpdateWorkspace) -> update.GateResult:
        del workspace
        phases.append("gates")
        return update.GateResult(
            passed=gate_passed,
            checks=(
                {
                    "id": "fixture.narrow",
                    "passed": gate_passed,
                    "detail": "fixture",
                },
            ),
        )

    def publish(
        workspace: update.UpdateWorkspace,
        paths: update.UpdatePaths,
        manifest: update.SourceManifest,
        diff: update.SourceDiff,
    ) -> dict[str, object]:
        del workspace, paths, diff
        phases.append("publish")
        connection = sqlite3.connect(registry)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS current_snapshot(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    build_id TEXT NOT NULL,
                    source_manifest_json TEXT NOT NULL,
                    source_manifest_fingerprint TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO current_snapshot
                VALUES (1, ?, ?, ?)
                """,
                (
                    "build-new",
                    json.dumps(
                        manifest.payload(),
                        sort_keys=True,
                    ),
                    manifest.fingerprint,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return {
            "buildId": "build-new",
            "sourceManifestFingerprint": manifest.fingerprint,
            "atomicSourceManifestBound": True,
        }

    def verify(
        paths: update.UpdatePaths,
        build_id: str,
        manifest: update.SourceManifest,
    ) -> bool:
        del paths
        phases.append("verify")
        connection = sqlite3.connect(registry)
        try:
            row = connection.execute(
                """
                SELECT build_id, source_manifest_fingerprint
                FROM current_snapshot
                WHERE singleton=1
                """
            ).fetchone()
        finally:
            connection.close()
        return row == (build_id, manifest.fingerprint)

    return update.UpdateHooks(
        load_previous_manifest=lambda paths: previous,
        scan_manifest=lambda paths: current,
        check_capability=lambda old, diff: None,
        stage_snapshot=stage,
        plan_changes=plan,
        ingest_changes=ingest,
        drain_worker=drain,
        run_narrow_gates=gates,
        publish_atomic=publish,
        verify_publication=verify,
    )


def test_diff_precisely_lists_added_changed_and_deleted() -> None:
    same = _revision("same", uri="capture://Same")
    changed_old = _revision("old", uri="capture://Changed")
    changed_new = _revision("new", uri="capture://Changed")
    deleted = _revision("gone", uri="capture://Deleted")
    added = _revision("added", uri="capture://Added")

    diff = update.compare_source_manifests(
        _manifest(same, changed_old, deleted),
        _manifest(same, changed_new, added),
    )

    assert [item.source_id for item in diff.added] == [added.source_id]
    assert [item.source_id for item in diff.changed] == [
        changed_new.source_id
    ]
    assert [item.source_id for item in diff.deleted] == [
        deleted.source_id
    ]
    assert diff.changed[0].previous == changed_old
    assert diff.changed[0].current == changed_new


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sourceId", "a" * 63),
        ("fingerprint", "z" * 64),
        ("sourceUri", "C:/Users/ac/native.json"),
        ("sourceUri", "file:///C:/Users/ac/native.json"),
        ("sourceUri", "https://example.invalid/input"),
        ("sourceUri", "capture://Demo Asset"),
        ("sourceUri", "capture://Demo/home/ac/private.json"),
        ("entityUri", "C:\\Users\\ac\\capture"),
        ("entityUri", "file:///home/ac/private"),
        ("entityUri", "bp://fixture/C%3A%5CUsers%5Cac%5Cprivate"),
        ("revisionLabel", "/home/ac/private/revision"),
        ("sourceKind", "\\\\server\\share\\kind"),
    ],
)
def test_source_manifest_payload_rejects_malformed_or_host_path_identity(
    field: str,
    value: str,
) -> None:
    payload = _manifest(_revision("valid")).payload()
    payload["entries"][0][field] = value
    if field == "sourceUri":
        payload["entries"][0]["sourceId"] = update._source_id(
            payload["entries"][0]["sourceKind"],
            value,
        )

    with pytest.raises(ValueError):
        update.source_manifest_from_payload(payload)


@pytest.mark.parametrize(
    "generated_at",
    ["", "2026-07-28T00:00:00", "not-a-timestamp"],
)
def test_source_manifest_payload_requires_timezone_aware_generated_at(
    generated_at: str,
) -> None:
    payload = _manifest(_revision("valid")).payload()
    payload["generatedAt"] = generated_at

    with pytest.raises(ValueError):
        update.source_manifest_from_payload(payload)


def test_source_manifest_binding_rejects_mismatched_fingerprint() -> None:
    binding = update.source_manifest_binding(
        _manifest(_revision("valid"))
    )
    binding["sourceManifestFingerprint"] = "0" * 64

    with pytest.raises(ValueError):
        update.source_manifest_from_binding(binding)


def test_default_first_run_locks_scan_but_does_not_stage_or_leave_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result, calls = _default_run(
        monkeypatch,
        tmp_path,
        previous=None,
        current=_manifest(_revision("new")),
    )

    assert result["gapCodes"] == ["INITIAL_FULL_REBUILD_REQUIRED"]
    assert result["fullRebuildRequired"] is True
    assert result["published"] is False
    assert calls == []
    assert not _paths(tmp_path).output.exists()


@pytest.mark.parametrize(
    "semantic_key",
    [
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
    ],
)
def test_default_nonselective_semantic_change_requires_full_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    semantic_key: str,
) -> None:
    previous = _manifest(_semantic(semantic_key, "old"))
    current = _manifest(_semantic(semantic_key, "new"))

    result, calls = _default_run(
        monkeypatch,
        tmp_path,
        previous=previous,
        current=current,
    )

    assert result["gapCodes"] == [
        "NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED"
    ]
    assert result["fullRebuildRequired"] is True
    assert calls == []
    assert not _paths(tmp_path).output.exists()


def test_default_deleted_source_requires_full_rebuild_before_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("gone"))
    result, calls = _default_run(
        monkeypatch,
        tmp_path,
        previous=previous,
        current=_manifest(),
    )

    assert result["gapCodes"] == ["BLUEPRINT_DELETE_NOT_SUPPORTED"]
    assert result["fullRebuildRequired"] is True
    assert len(result["sourceChanges"]["deleted"]) == 1
    assert calls == []


def test_default_selective_asset_change_reports_missing_capability_pre_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))

    result, calls = _default_run(
        monkeypatch,
        tmp_path,
        previous=previous,
        current=current,
    )

    assert result["gapCodes"] == ["BLUEPRINT_UPDATE_NOT_SUPPORTED"]
    assert result["fullRebuildRequired"] is True
    assert calls == []
    assert not _paths(tmp_path).output.exists()


def test_production_preflight_allows_only_bounded_additive_blueprint_diff(
) -> None:
    capture_old = _semantic("captures", "old")
    capture_new = _semantic("captures", "new")
    added = _revision(
        "added",
        uri="capture://Added",
        entity_uri="/Game/Test/Added.Added",
    )
    previous = _manifest(capture_old)
    current = _manifest(capture_new, added)
    diff = update.compare_source_manifests(previous, current)

    update.production_capability_check(previous, diff)


def test_production_preflight_rejects_rename_with_specific_gap() -> None:
    previous_source = _revision(
        "old",
        uri="capture://OldName",
        entity_uri="/Game/Test/Same.Same",
    )
    current_source = _revision(
        "new",
        uri="capture://NewName",
        entity_uri="/Game/Test/Same.Same",
    )
    previous = _manifest(
        _semantic("captures", "old"),
        previous_source,
    )
    current = _manifest(
        _semantic("captures", "new"),
        current_source,
    )
    diff = update.compare_source_manifests(previous, current)

    with pytest.raises(update.UpdateBlocked) as caught:
        update.production_capability_check(previous, diff)

    assert caught.value.gap_code == "BLUEPRINT_RENAME_NOT_SUPPORTED"


def test_production_preflight_rejects_addition_without_capture_aggregate(
) -> None:
    stable_capture = _semantic("captures", "same")
    previous = _manifest(stable_capture)
    current = _manifest(
        stable_capture,
        _revision(
            "new",
            uri="capture://Added",
            entity_uri="/Game/Test/Added.Added",
        ),
    )
    diff = update.compare_source_manifests(previous, current)

    with pytest.raises(update.UpdateBlocked) as caught:
        update.production_capability_check(previous, diff)

    assert (
        caught.value.gap_code
        == "BLUEPRINT_CAPTURE_AGGREGATE_BINDING_REQUIRED"
    )


def test_production_preflight_rejects_blueprint_batch_above_bound() -> None:
    previous = _manifest(_semantic("captures", "old"))
    additions = tuple(
        _revision(
            f"added-{index}",
            uri=f"capture://Added{index}",
            entity_uri=f"/Game/Test/Added{index}.Added{index}",
        )
        for index in range(
            update.MAX_ADDITIVE_BLUEPRINT_SOURCES + 1
        )
    )
    current = _manifest(
        _semantic("captures", "new"),
        *additions,
    )
    diff = update.compare_source_manifests(previous, current)

    with pytest.raises(update.UpdateBlocked) as caught:
        update.production_capability_check(previous, diff)

    assert caught.value.gap_code == (
        "ADDITIVE_QUARANTINE_REQUIRES_SINGLE_BLUEPRINT"
    )


@pytest.mark.parametrize("receipt_invalid", [False, True])
def test_default_additive_pipeline_returns_real_fact_receipt_and_blocks_gaps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    receipt_invalid: bool,
) -> None:
    previous = _manifest(_semantic("captures", "old"))
    added = _revision(
        "added",
        uri="capture://Added",
        entity_uri="/Game/Test/Added.Added",
    )
    current = _manifest(_semantic("captures", "new"), added)
    paths = _paths(tmp_path)
    sqlite3.connect(paths.discovery_database).close()
    workspace = _production_workspace(tmp_path)
    _, pointer_bytes, _ = _write_bound_current_snapshot(
        paths,
        previous,
    )
    monkeypatch.setattr(
        update,
        "scan_source_manifest",
        lambda candidate_paths: current,
    )
    workspace.base_build_id = "build-current"
    workspace.staging_receipt = _fixture_staging_receipt(
        build_id="build-current"
    )
    _bind_production_staged_baseline(workspace)
    workspace.update_baseline = object()
    monkeypatch.setattr(
        update,
        "cleanup_staged_baseline_snapshot",
        lambda staged, snapshot_root: shutil.rmtree(
            workspace.temporary_root,
            ignore_errors=True,
        ),
    )
    monkeypatch.setattr(
        update,
        "stage_current_snapshot",
        lambda candidate_paths, baseline: workspace,
    )
    frozen = SimpleNamespace(
        source_id=added.source_id,
        entity_uri=added.entity_uri,
        revision_label=added.revision_label,
        source_fingerprint=added.fingerprint,
        ingest_root=tmp_path / "quarantine",
    )
    monkeypatch.setattr(
        update,
        "freeze_additive_blueprint_input",
        lambda *args, **kwargs: frozen,
    )
    monkeypatch.setattr(
        update,
        "_safe_quarantine_receipt",
        lambda value: {
            "published": False,
            "productionAuthority": False,
        },
    )
    monkeypatch.setattr(
        update,
        "validate_frozen_additive_blueprint_input",
        lambda value: value,
    )

    def ingest_fixture(
        discovery: sqlite3.Connection,
        core: sqlite3.Connection,
        *,
        capture_root: Path,
        ontology: object,
        source_revisions: tuple[update.SourceRevision, ...],
        frozen_evidence_root: bool,
    ) -> BlueprintIngestResult:
        del discovery, capture_root, ontology
        assert source_revisions == (added,)
        assert frozen_evidence_root is True
        core.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'blueprint_evidence', 'bp://asset@revision',
                'blueprint-sha', 'uasset-graph-reader-evidence-v3',
                'ark.blueprint.evidence.v2',
                '2026-07-28T00:00:00Z', 'FRESH'
            )
            """
        )
        core.execute(
            """
            INSERT INTO facts(
                fact_id, subject_entity_id, fact_type, fact_name,
                scope_kind, declared_on_entity_id, value_kind,
                value_integer, status, confidence, ontology_version,
                current, canonical_fact_key
            ) VALUES (
                1, 1, 'DECLARED_DEFAULT', 'Rate', 'DECLARED', 1,
                'INTEGER', 7, 'CONFIRMED', 'HIGH', 'fixture-ontology',
                1, 'fact://fixture/rate'
            )
            """
        )
        core.execute(
            """
            INSERT INTO facts(
                fact_id, subject_entity_id, fact_type, fact_name,
                scope_kind, declared_on_entity_id, value_kind,
                value_integer, status, confidence, ontology_version,
                current, canonical_fact_key
            ) VALUES (
                2, 1, 'DECLARED_DEFAULT', 'RequiredEngramPoints',
                'DECLARED', 1, 'INTEGER', 0, 'CONFIRMED', 'HIGH',
                'fixture-ontology', 1, 'fact://fixture/engram-points'
            )
            """
        )
        core.execute(
            """
            INSERT INTO fact_evidence VALUES
                (
                    1, 2, 'bp://asset@revision/default/Rate',
                    'DEFAULT_VALUE_ACTUAL'
                ),
                (
                    2, 2,
                    'bp://asset@revision/default/RequiredEngramPoints',
                    'DEFAULT_VALUE_ACTUAL'
                )
            """
        )
        core.commit()
        return BlueprintIngestResult(
            counts={
                "freshAssets": 1,
                "declaredFacts": 2,
                "factEvidence": 2,
            },
            covered_properties=frozenset(
                {
                    ("/Game/Test/Added.Added", "Rate"),
                    (
                        "/Game/Test/Added.Added",
                        "RequiredEngramPoints",
                    ),
                }
            ),
            freshness_gap_assets=frozenset(),
            untrusted_assets=frozenset(),
            fact_ids=frozenset({1, 2}),
            entity_ids=frozenset({1}),
        )

    monkeypatch.setattr(
        update,
        "materialize_blueprint_defaults",
        ingest_fixture,
    )
    verified_scope_calls: list[tuple[object, object, object]] = []

    def verify_delta_scope(
        baseline: object,
        *,
        staged_snapshot: object,
        frozen_input: object,
        ingest_result: BlueprintIngestResult,
    ) -> SimpleNamespace:
        verified_scope_calls.append(
            (staged_snapshot, frozen_input, ingest_result)
        )
        core = sqlite3.connect(workspace.core_path)
        try:
            after_database_sha256 = update.logical_database_state(
                core
            ).database_sha256
        finally:
            core.close()
        return SimpleNamespace(
            source_revision_ids=(2,),
            entity_ids=(1,),
            fact_ids=(1, 2),
            changed_tables=(
                "fact_evidence",
                "facts",
                "source_revisions",
            ),
            after_database_sha256=after_database_sha256,
        )

    monkeypatch.setattr(
        update,
        "verify_base_bound_add_only_blueprint_delta_scope",
        verify_delta_scope,
    )

    monkeypatch.setattr(
        update,
        "compute_additive_role_dependency_scope",
        lambda *args, **kwargs: (
            (1,),
            {
                "schema": "ark-kb-additive-role-dependency-scope/v1",
                "classifierVersion": "fixture-role/v1",
                "sourceRevisionId": 2,
                "changedEntityIds": [1],
                "roleEntityIds": [1],
                "transitions": [],
                "proof": "role-scope://fixture",
            },
        ),
    )
    strict_scope_calls: list[
        tuple[
            tuple[int, ...],
            tuple[int, ...],
            tuple[int, ...],
            tuple[str, ...],
        ]
    ] = []
    production_materializer = (
        update.materialize_additive_asset_dependency_scope
    )

    def materialize_strict_scope(
        connection: sqlite3.Connection,
        *,
        source_revision_ids: tuple[int, ...],
        entity_ids: tuple[int, ...],
        fact_ids: tuple[int, ...],
        actual_write_tables: tuple[str, ...],
        role_entity_ids: tuple[int, ...],
        role_scope_proof: dict[str, object],
    ) -> update.InvalidationPlan:
        strict_scope_calls.append(
            (
                source_revision_ids,
                entity_ids,
                fact_ids,
                actual_write_tables,
            )
        )
        return production_materializer(
            connection,
            source_revision_ids=source_revision_ids,
            entity_ids=entity_ids,
            fact_ids=fact_ids,
            actual_write_tables=actual_write_tables,
            role_entity_ids=role_entity_ids,
            role_scope_proof=role_scope_proof,
        )

    monkeypatch.setattr(
        update,
        "materialize_additive_asset_dependency_scope",
        materialize_strict_scope,
    )

    def forbidden_generic_asset_plan(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError(
            "default additive runner must not use generic ASSET planning"
        )

    monkeypatch.setattr(
        update,
        "plan_invalidation",
        forbidden_generic_asset_plan,
        raising=False,
    )

    def build_receipt(*args, **kwargs):
        del args, kwargs
        if receipt_invalid:
            raise update.UpdateBaselineBlockedGap(
                "DELTA_RECEIPT_BASE_BINDING_MISMATCH",
                "fixture receipt mismatch",
            )
        return {"status": "BLOCKED_GAP"}

    monkeypatch.setattr(
        update,
        "build_base_bound_add_only_delta_receipt",
        build_receipt,
    )
    monkeypatch.setattr(
        update,
        "inspect_base_bound_prepublication_delta_receipt",
        lambda *args, **kwargs: SimpleNamespace(
            payload=lambda: {
                "schema": (
                    "ark-kb-prepublication-delta-inspection/v2"
                ),
                "status": "BLOCKED_GAP",
                "baseBindingVerified": True,
                "receiptArtifactSha256": "1" * 64,
                "receiptContentSha256": "2" * 64,
                "baseBuildId": "build-current",
                "sourceDiffSha256": "3" * 64,
                "blockedGapCount": 1,
            }
        ),
    )

    result = update.run_incremental_update(paths)

    assert result["status"] == "blocked"
    assert result["published"] is False
    assert result["gapCodes"] == [
        (
            "DELTA_RECEIPT_BASE_BINDING_MISMATCH"
            if receipt_invalid
            else "REBUILD_QUEUE_NOT_DRAINED"
        )
    ]
    assert "FACT" in result["worker"]["succeededKinds"]
    assert "QUERY_SNAPSHOT" in result["worker"]["succeededKinds"]
    assert "BACKEND_NOT_CONFIGURED_QUERY_SNAPSHOT" not in (
        result["worker"]["blockedGapCodes"]
    )
    assert "BACKEND_NOT_CONFIGURED_EDGE_ENTITY" not in (
        result["worker"]["blockedGapCodes"]
    )
    assert result["worker"]["attempted"] == 12
    assert result["worker"]["succeeded"] == 4
    assert result["worker"]["blocked_gap"] == 8
    assert result["worker"]["failed"] == 0
    assert len(verified_scope_calls) == 1
    assert strict_scope_calls == [
        (
            (2,),
            (1,),
            (1, 2),
            ("fact_evidence", "facts", "source_revisions"),
        )
    ]
    assert any(
        proof.startswith("rebuild-proof://")
        for proof in result["worker"]["receiptProofs"]
    )
    if receipt_invalid:
        assert "deltaReceipt" not in result
        assert "narrowGates" not in result
        assert "publication" not in result
    else:
        assert result["deltaReceipt"] == {
            "schema": "ark-kb-prepublication-delta-inspection/v2",
            "status": "BLOCKED_GAP",
            "baseBindingVerified": True,
            "receiptRawSha256": "1" * 64,
            "receiptContentSha256": "2" * 64,
            "baseBuildId": "build-current",
            "sourceDiffSha256": "3" * 64,
            "blockedGapCount": 1,
        }
    assert result["ingest"]["verifiedSources"] == 1
    assert str(result["ingest"]["proof"]).startswith("ingest-proof://")
    assert result["staging"]["sourceVerifiedUnchanged"] is True
    assert not workspace.temporary_root.exists()
    assert (paths.output / "current.json").read_bytes() == pointer_bytes


def _write_role_discovery_fixture(path: Path) -> None:
    discovery = sqlite3.connect(path)
    try:
        discovery.executescript(
            """
            CREATE TABLE assets(
                object_path TEXT PRIMARY KEY,
                asset_class_path TEXT NOT NULL,
                generated_class_path TEXT NOT NULL,
                parent_class_path TEXT NOT NULL,
                native_parent_class_path TEXT NOT NULL,
                identity_status TEXT NOT NULL,
                identity_confidence TEXT NOT NULL,
                is_blueprint INTEGER,
                is_data_asset INTEGER,
                is_data_table INTEGER,
                is_function_library INTEGER,
                is_blueprint_interface INTEGER,
                is_map INTEGER,
                capture_exists INTEGER,
                evidence_freshness TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                descendant_count INTEGER NOT NULL,
                referencer_count INTEGER NOT NULL,
                component_reuse_count INTEGER NOT NULL,
                cross_domain_reference_count INTEGER NOT NULL,
                registry_usage_count INTEGER NOT NULL,
                query_hit_count INTEGER,
                query_hit_status TEXT NOT NULL,
                existing_report_count INTEGER,
                existing_report_status TEXT NOT NULL,
                graph_count INTEGER NOT NULL,
                default_property_count INTEGER NOT NULL
            );
            INSERT INTO assets VALUES (
                '/Game/Test/Added.Added', '/Script/Engine.Blueprint',
                '/Game/Test/Added.Added_C', '/Script/Test.Fixture',
                '/Script/Test.Fixture', 'CONFIRMED', 'HIGH',
                1, 0, 0, 0, 0, 0, 1, 'FRESH', 'CONFIRMED',
                0, 1, 0, 0, 0, NULL, 'NOT_MEASURED',
                NULL, 'NOT_MEASURED', 1, 2
            );
            """
        )
        discovery.commit()
    finally:
        discovery.close()


def test_production_shaped_additive_backends_drain_exact_12_of_12(
    tmp_path: Path,
) -> None:
    workspace = _production_workspace(tmp_path)
    _bind_production_staged_baseline(workspace)
    discovery_path = tmp_path / "Discovery.sqlite"
    _write_role_discovery_fixture(discovery_path)
    workspace.discovery_path = discovery_path
    workspace.candidate_build_id = "20260731T000000-candidate123"
    workspace.candidate_source_fingerprint = "a" * 64
    workspace.candidate_generated_at = "2026-07-31T00:00:00+00:00"
    ontology = update.load_ontology(update.PROJECT_ROOT / "ontology")

    discovery = sqlite3.connect(discovery_path)
    core = sqlite3.connect(workspace.core_path)
    core.execute("PRAGMA foreign_keys=ON")
    try:
        core.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'blueprint_evidence', 'bp://asset@revision',
                'blueprint-sha', 'fixture', 'v2',
                '2026-07-31T00:00:00+00:00', 'FRESH'
            )
            """
        )
        core.execute(
            """
            INSERT INTO source_revisions VALUES (
                3, 'ontology', ?, 'ontology-sha', ?, 'v1',
                '2026-07-31T00:00:00+00:00', 'FRESH'
            )
            """,
            (f"ontology://{ontology.version}", ontology.version),
        )
        fact_rows = (
            (1, "Rate", 7, "fact://fixture/rate"),
            (2, "RequiredEngramPoints", 0, "fact://fixture/engram-points"),
        )
        core.executemany(
            """
            INSERT INTO facts(
                fact_id, subject_entity_id, fact_type, fact_name,
                scope_kind, declared_on_entity_id, value_kind,
                value_integer, status, confidence, ontology_version,
                current, canonical_fact_key
            ) VALUES (
                ?, 1, 'DECLARED_DEFAULT', ?, 'DECLARED', 1,
                'INTEGER', ?, 'CONFIRMED', 'HIGH', ?, 1, ?
            )
            """,
            [
                (fact_id, name, value, ontology.version, key)
                for fact_id, name, value, key in fact_rows
            ],
        )
        core.executemany(
            "INSERT INTO fact_evidence VALUES (?, 2, ?, 'DEFAULT_VALUE_ACTUAL')",
            (
                (1, "bp://asset@revision/default/Rate"),
                (2, "bp://asset@revision/default/RequiredEngramPoints"),
            ),
        )
        materialize_discovery_roles(
            discovery,
            core,
            source_revision_id=1,
        )
        role_ids, role_proof = update.compute_additive_role_dependency_scope(
            discovery,
            core,
            changed_entity_ids=(1,),
            source_revision_id=2,
        )
        assert role_ids == (1,)
        plan = update.InvalidationPlan(
            event_kind="ASSET",
            upstream_revision_id=None,
            downstream={
                "FACT": (1, 2),
                "EFFECTIVE_ENTITY": (1,),
                "ROLE_ENTITY": role_ids,
                "DOMAIN_ENTITY": (1,),
                "PROJECTION": tuple(range(1, 7)),
                "QUERY_SNAPSHOT": (2,),
            },
            reasons={
                "FACT": "ADDED_BLUEPRINT_FACT_EVIDENCE",
                "EFFECTIVE_ENTITY": "ADDED_DECLARED_DEFAULT_OR_PARENT",
                "ROLE_ENTITY": "ADDITIVE_ROLE_INPUT",
                "DOMAIN_ENTITY": "ADDITIVE_DOMAIN_INPUT",
                "PROJECTION": "ADDITIVE_FACT_PROJECTION",
                "QUERY_SNAPSHOT": "ADDITIVE_QUERY_CACHE",
            },
            role_scope_proof=role_proof,
        )
        update.apply_invalidation_plan(
            core,
            plan,
            created_at="2026-07-31T00:00:01+00:00",
        )
    finally:
        core.close()
        discovery.close()

    report = update.drain_production_rebuilds(workspace, 12)

    assert report.attempted == 12
    assert report.succeeded == 12
    assert report.blocked_gap == 0
    assert report.failed == 0
    assert report.remaining_pending == 0
    assert report.remaining_running == 0
    assert report.drained is True
    assert [outcome.task.downstream_kind for outcome in report.outcomes] == [
        "FACT",
        "FACT",
        "EFFECTIVE_ENTITY",
        "ROLE_ENTITY",
        "DOMAIN_ENTITY",
        *("PROJECTION" for _ in range(6)),
        "QUERY_SNAPSHOT",
    ]


def test_default_unchanged_manifest_is_cache_hit_without_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = _manifest(_semantic("ontology", "same"))
    result, calls = _default_run(
        monkeypatch,
        tmp_path,
        previous=manifest,
        current=manifest,
    )

    assert result["status"] == "cache_hit"
    assert result["cacheHit"] is True
    assert result["published"] is False
    assert calls == []
    assert not _paths(tmp_path).output.exists()


def test_previous_manifest_is_loaded_only_from_current_immutable_snapshot(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    build_id = "build-one"
    snapshot = paths.output / "snapshots" / build_id
    snapshot.mkdir(parents=True)
    expected = _manifest(_semantic("ontology", "bound"))
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": build_id,
                "incrementalUpdate": update.source_manifest_binding(expected),
            }
        ),
        encoding="utf-8",
    )
    (paths.output / "current.json").write_text(
        json.dumps(
            {
                "buildId": build_id,
                "snapshotRelativePath": f"snapshots/{build_id}",
            }
        ),
        encoding="utf-8",
    )

    loaded = update.load_current_source_manifest(paths)

    assert loaded == expected


def _write_bound_current_snapshot(
    paths: update.UpdatePaths,
    source_manifest: update.SourceManifest,
    *,
    build_id: str = "build-current",
) -> tuple[Path, bytes, bytes]:
    snapshot = paths.output / "snapshots" / build_id
    snapshot.mkdir(parents=True)
    manifest_bytes = json.dumps(
        {
            "schema": "ark-kb-vnext-snapshot/v1",
            "buildId": build_id,
            "incrementalUpdate": update.source_manifest_binding(
                source_manifest
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    pointer_bytes = json.dumps(
        {
            "buildId": build_id,
            "snapshotRelativePath": f"snapshots/{build_id}",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    (snapshot / "manifest.json").write_bytes(manifest_bytes)
    (paths.output / "current.json").write_bytes(pointer_bytes)
    return snapshot, pointer_bytes, manifest_bytes


def test_default_candidate_scan_runs_under_incremental_writer_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    previous = _manifest(_semantic("ontology", "old"))
    candidate = _manifest(_semantic("ontology", "new"))
    _write_bound_current_snapshot(paths, previous)
    scan_lock_states: list[bool] = []

    def scan(locked_paths: update.UpdatePaths) -> update.SourceManifest:
        scan_lock_states.append(
            (
                locked_paths.output / ".incremental-update.lock"
            ).is_file()
        )
        assert scan_lock_states[-1] is True
        return candidate

    monkeypatch.setattr(update, "scan_source_manifest", scan)

    result = update.run_incremental_update(paths)

    assert result["gapCodes"] == [
        "NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED"
    ]
    assert scan_lock_states == [True, True]


def test_default_staging_uses_locked_update_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    previous = _manifest(_semantic("captures", "old"))
    candidate = _manifest(
        _semantic("captures", "new"),
        _revision("added", uri="capture://Added"),
    )
    _write_bound_current_snapshot(paths, previous)
    monkeypatch.setattr(
        update,
        "scan_source_manifest",
        lambda locked_paths: candidate,
    )
    observed: list[update.UpdateBaseline] = []

    def stage(
        locked_paths: update.UpdatePaths,
        *,
        baseline: update.UpdateBaseline,
    ) -> update.UpdateWorkspace:
        assert (
            locked_paths.output / ".incremental-update.lock"
        ).is_file()
        observed.append(baseline)
        raise update.UpdateBlocked(
            "STAGING_TEST_SENTINEL",
            "stop after proving default staging wiring",
            full_rebuild_required=True,
        )

    monkeypatch.setattr(update, "stage_current_snapshot", stage)

    result = update.run_incremental_update(paths)

    assert result["gapCodes"] == ["STAGING_TEST_SENTINEL"]
    assert len(observed) == 1
    assert observed[0].candidate_source_manifest == candidate
    assert observed[0].base_pointer_sha256 == hashlib.sha256(
        (paths.output / "current.json").read_bytes()
    ).hexdigest()


def test_default_runner_rescans_sources_after_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    previous = _manifest(_semantic("captures", "old"))
    candidate = _manifest(
        _semantic("captures", "new"),
        _revision("added", uri="capture://Added"),
    )
    changed = _manifest(
        _semantic("captures", "changed-again"),
        _revision("added", uri="capture://Added"),
    )
    _write_bound_current_snapshot(paths, previous)
    observed = iter((candidate, candidate, changed))
    sequence: list[str] = []

    def scan_after_stage(
        locked_paths: update.UpdatePaths,
    ) -> update.SourceManifest:
        del locked_paths
        sequence.append("scan")
        return next(observed)

    monkeypatch.setattr(
        update,
        "scan_source_manifest",
        scan_after_stage,
    )
    workspace = _workspace(tmp_path)
    workspace.base_build_id = "build-current"
    workspace.staged_baseline = object()
    monkeypatch.setattr(
        update,
        "cleanup_staged_baseline_snapshot",
        lambda staged, snapshot_root: shutil.rmtree(
            workspace.temporary_root,
            ignore_errors=True,
        ),
    )
    monkeypatch.setattr(
        update,
        "stage_current_snapshot",
        lambda locked_paths, baseline: workspace,
    )
    monkeypatch.setattr(
        update,
        "freeze_additive_blueprint_input",
        lambda *args, **kwargs: (
            sequence.append("freeze"),
            setattr(workspace, "staged_baseline", None),
            object(),
        )[2],
    )
    monkeypatch.setattr(
        update,
        "_safe_quarantine_receipt",
        lambda frozen: {"published": False},
    )

    result = update.run_incremental_update(paths)

    assert result["gapCodes"] == [
        "SOURCE_MANIFEST_CHANGED_DURING_UPDATE"
    ]
    assert result["published"] is False
    assert sequence == ["scan", "scan", "freeze", "scan"]


def test_default_runner_rechecks_pointer_after_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    previous = _manifest(_semantic("captures", "old"))
    candidate = _manifest(
        _semantic("captures", "new"),
        _revision("added", uri="capture://Added"),
    )
    _, pointer_bytes, _ = _write_bound_current_snapshot(paths, previous)
    monkeypatch.setattr(
        update,
        "scan_source_manifest",
        lambda locked_paths: candidate,
    )
    workspace = _workspace(tmp_path)
    workspace.base_build_id = "build-current"
    workspace.staged_baseline = object()
    monkeypatch.setattr(
        update,
        "cleanup_staged_baseline_snapshot",
        lambda staged, snapshot_root: shutil.rmtree(
            workspace.temporary_root,
            ignore_errors=True,
        ),
    )

    def stage(
        locked_paths: update.UpdatePaths,
        *,
        baseline: update.UpdateBaseline,
    ) -> update.UpdateWorkspace:
        del baseline
        locked_paths.output.joinpath("current.json").write_bytes(
            json.dumps(
                json.loads(pointer_bytes),
                indent=2,
            ).encode("utf-8")
        )
        return workspace

    monkeypatch.setattr(update, "stage_current_snapshot", stage)
    monkeypatch.setattr(
        update,
        "freeze_additive_blueprint_input",
        lambda *args, **kwargs: (
            setattr(workspace, "staged_baseline", None),
            object(),
        )[1],
    )
    monkeypatch.setattr(
        update,
        "_safe_quarantine_receipt",
        lambda frozen: {"published": False},
    )

    result = update.run_incremental_update(paths)

    assert result["gapCodes"] == ["UPDATE_BASELINE_IDENTITY_CHANGED"]
    assert result["published"] is False


def test_default_runner_fails_closed_on_raw_pointer_change_during_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    previous = _manifest(_semantic("ontology", "old"))
    candidate = _manifest(_semantic("ontology", "new"))
    _, pointer_bytes, _ = _write_bound_current_snapshot(paths, previous)
    changed_pointer_bytes = json.dumps(
        json.loads(pointer_bytes),
        indent=2,
    ).encode("utf-8")
    assert hashlib.sha256(changed_pointer_bytes).digest() != (
        hashlib.sha256(pointer_bytes).digest()
    )
    scans = 0

    def scan(locked_paths: update.UpdatePaths) -> update.SourceManifest:
        nonlocal scans
        scans += 1
        if scans == 1:
            (locked_paths.output / "current.json").write_bytes(
                changed_pointer_bytes
            )
        return candidate

    monkeypatch.setattr(update, "scan_source_manifest", scan)

    result = update.run_incremental_update(paths)

    assert result["gapCodes"] == ["UPDATE_BASELINE_IDENTITY_CHANGED"]
    assert result["published"] is False


def test_default_runner_fails_closed_on_build_id_change_during_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    previous = _manifest(_semantic("ontology", "old"))
    candidate = _manifest(_semantic("ontology", "new"))
    _, build_b_pointer, _ = _write_bound_current_snapshot(
        paths,
        previous,
        build_id="build-b",
    )
    _write_bound_current_snapshot(
        paths,
        previous,
        build_id="build-a",
    )
    scans = 0

    def scan(locked_paths: update.UpdatePaths) -> update.SourceManifest:
        nonlocal scans
        scans += 1
        if scans == 1:
            (locked_paths.output / "current.json").write_bytes(
                build_b_pointer
            )
        return candidate

    monkeypatch.setattr(update, "scan_source_manifest", scan)

    result = update.run_incremental_update(paths)

    assert result["gapCodes"] == ["UPDATE_BASELINE_IDENTITY_CHANGED"]
    assert result["published"] is False


def test_default_runner_fails_closed_on_manifest_change_during_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    previous = _manifest(_semantic("ontology", "old"))
    candidate = _manifest(_semantic("ontology", "new"))
    snapshot, _, manifest_bytes = _write_bound_current_snapshot(
        paths,
        previous,
    )
    scans = 0

    def scan(locked_paths: update.UpdatePaths) -> update.SourceManifest:
        del locked_paths
        nonlocal scans
        scans += 1
        if scans == 1:
            (snapshot / "manifest.json").write_bytes(
                manifest_bytes + b"\n"
            )
        return candidate

    monkeypatch.setattr(update, "scan_source_manifest", scan)

    result = update.run_incremental_update(paths)

    assert result["gapCodes"] == ["UPDATE_BASELINE_IDENTITY_CHANGED"]
    assert result["published"] is False


def test_default_runner_fails_closed_on_source_change_after_locked_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    previous = _manifest(_semantic("ontology", "old"))
    candidate = _manifest(_semantic("ontology", "new"))
    changed_candidate = _manifest(_semantic("ontology", "changed-again"))
    _write_bound_current_snapshot(paths, previous)
    observed = iter((candidate, changed_candidate))

    monkeypatch.setattr(
        update,
        "scan_source_manifest",
        lambda locked_paths: next(observed),
    )

    result = update.run_incremental_update(paths)

    assert result["gapCodes"] == [
        "SOURCE_MANIFEST_CHANGED_DURING_UPDATE"
    ]
    assert result["published"] is False


def test_stage_current_snapshot_requires_locked_update_baseline(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="locked UpdateBaseline"):
        update.stage_current_snapshot(  # type: ignore[arg-type]
            _paths(tmp_path),
            baseline=object(),
        )


def test_safe_staging_cleanup_uncertainty_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    workspace = _workspace(tmp_path)
    workspace.staged_baseline = SimpleNamespace()  # type: ignore[assignment]
    monkeypatch.setattr(
        update,
        "cleanup_staged_baseline_snapshot",
        lambda staged, snapshot_root: (_ for _ in ()).throw(
            update.UpdateBaselineBlockedGap(
                "STAGING_CLEANUP_UNCERTAIN",
                "cleanup identity could not be verified",
                status="UNCERTAIN",
                residual_identifier=(
                    ".incremental-staging/"
                    "0123456789abcdef0123456789abcdef"
                ),
            )
        ),
    )

    with pytest.raises(update.UpdateBlocked) as caught:
        with update._staging_workspace_lifecycle(
            paths,
            workspace,
            cleanup_injected=lambda: False,
        ):
            pass

    result = update._blocked_result(base={}, error=caught.value)
    assert result["status"] == "uncertain"
    assert result["gapCodes"] == ["STAGING_CLEANUP_UNCERTAIN"]
    assert result["stagingResidualIdentifier"] == (
        ".incremental-staging/0123456789abcdef0123456789abcdef"
    )


def test_scan_covers_snapshot_ten_inputs_and_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hashes = {
        key: hashlib.sha256(key.encode("utf-8")).hexdigest()
        for key in update.SNAPSHOT_SEMANTIC_INPUT_KEYS
    }
    monkeypatch.setattr(
        update,
        "_snapshot_semantic_input_hashes",
        lambda **kwargs: hashes,
    )
    paths = _paths(tmp_path)
    paths.runtime_root.mkdir()
    (paths.runtime_root / "observation.json").write_text(
        "{}",
        encoding="utf-8",
    )

    manifest = update.scan_source_manifest(paths)

    semantic_uris = {
        item.source_uri
        for item in manifest.entries
        if item.source_kind == "SEMANTIC_INPUT"
    }
    assert semantic_uris == {
        *(f"semantic-input://{key}" for key in hashes),
        "semantic-input://runtimeObservations",
    }


def test_shared_scan_adds_path_free_native_set_and_ignores_generated_at(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence_set = NativeEvidenceSet(
        evidence_set_id="native-set://recipe-fixture/set-a",
        recipe_id="recipe-fixture",
        recipe_sha256="1" * 64,
        source_sha256="2" * 64,
        sqlite_sha256="3" * 64,
        generated_at="2026-07-28T00:00:00Z",
        generator_commit="fixture-commit",
        binary_sha256="4" * 64,
        module="ShooterGameEditor-ShooterGame.dll",
        pdb_sha256="5" * 64,
        pdb_guid="AABBCCDD",
        pdb_age=1,
        pdb_loaded=True,
        pdb_matches_binary=True,
        trust_status="VERIFIED",
        formal_validation=True,
        symbol_source="PDB",
        symbol_status="CONFIRMED",
        symbol_confidence="HIGH",
    )
    monkeypatch.setattr(
        source_manifest_module,
        "load_native_evidence_corpus",
        lambda root: SimpleNamespace(evidence_sets=(evidence_set,)),
    )
    hashes = {
        key: hashlib.sha256(key.encode("utf-8")).hexdigest()
        for key in source_manifest_module.SNAPSHOT_SEMANTIC_INPUT_KEYS
    }
    common = {
        "semantic_input_hashes": hashes,
        "capture_root": tmp_path / "captures",
        "native_root": tmp_path / "native",
        "runtime_root": tmp_path / "runtime",
    }

    first = source_manifest_module.scan_source_manifest(
        generated_at="2026-07-28T00:00:00+00:00",
        **common,
    )
    second = source_manifest_module.scan_source_manifest(
        generated_at="2026-07-29T00:00:00+00:00",
        **common,
    )

    native_entries = [
        entry
        for entry in first.entries
        if entry.source_kind == "NATIVE_EVIDENCE_SET"
    ]
    assert [entry.source_uri for entry in native_entries] == [
        evidence_set.evidence_set_id
    ]
    assert first.fingerprint == second.fingerprint
    encoded = json.dumps(first.payload(), sort_keys=True)
    assert str(tmp_path) not in encoded
    assert "C:\\" not in encoded


def test_injected_success_atomically_binds_manifest_before_return(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=_success_hooks(
            tmp_path,
            previous,
            current,
            phases,
        ),
    )

    assert result["status"] == "published"
    assert result["published"] is True
    assert phases == [
        "stage",
        "plan",
        "ingest",
        "worker",
        "gates",
        "publish",
        "verify",
    ]
    registry = sqlite3.connect(
        tmp_path / "atomic-publication.sqlite"
    )
    row = registry.execute(
        """
        SELECT build_id, source_manifest_json,
               source_manifest_fingerprint
        FROM current_snapshot
        """
    ).fetchone()
    registry.close()
    assert row[0] == "build-new"
    assert json.loads(row[1]) == current.payload()
    assert row[2] == current.fingerprint
    assert not (
        _paths(tmp_path).output / ".incremental-update.lock"
    ).exists()
    assert str(tmp_path) not in json.dumps(result, sort_keys=True)


def test_single_writer_lock_blocks_before_staging(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    paths = _paths(tmp_path)
    paths.output.mkdir(parents=True)
    (paths.output / ".incremental-update.lock").write_text(
        "busy",
        encoding="utf-8",
    )

    result = update.run_incremental_update(
        paths,
        hooks=_success_hooks(
            tmp_path,
            previous,
            current,
            phases,
        ),
    )

    assert result["gapCodes"] == [
        "INCREMENTAL_UPDATE_ALREADY_RUNNING"
    ]
    assert result["fullRebuildRequired"] is False
    assert phases == []


def test_invalid_atomic_publication_receipt_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    hooks = _success_hooks(
        tmp_path,
        previous,
        current,
        phases,
    )
    hooks = update.UpdateHooks(
        load_previous_manifest=hooks.load_previous_manifest,
        scan_manifest=hooks.scan_manifest,
        check_capability=hooks.check_capability,
        stage_snapshot=hooks.stage_snapshot,
        plan_changes=hooks.plan_changes,
        ingest_changes=hooks.ingest_changes,
        drain_worker=hooks.drain_worker,
        run_narrow_gates=hooks.run_narrow_gates,
        publish_atomic=lambda workspace, paths, manifest, diff: {
            "buildId": "bad",
            "sourceManifestFingerprint": "wrong",
            "atomicSourceManifestBound": False,
        },
        verify_publication=hooks.verify_publication,
    )

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=hooks,
    )

    assert result["status"] == "uncertain_after_switch"
    assert result["gapCodes"] == [
        "ATOMIC_PUBLICATION_RECEIPT_INVALID"
    ]
    assert result["published"] is None


def test_self_attested_receipt_without_independent_binding_is_rejected(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    hooks = _success_hooks(
        tmp_path,
        previous,
        current,
        phases,
    )
    hooks = update.UpdateHooks(
        load_previous_manifest=hooks.load_previous_manifest,
        scan_manifest=hooks.scan_manifest,
        check_capability=hooks.check_capability,
        stage_snapshot=hooks.stage_snapshot,
        plan_changes=hooks.plan_changes,
        ingest_changes=hooks.ingest_changes,
        drain_worker=hooks.drain_worker,
        run_narrow_gates=hooks.run_narrow_gates,
        publish_atomic=hooks.publish_atomic,
        verify_publication=lambda paths, build_id, manifest: False,
    )

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=hooks,
    )

    assert result["gapCodes"] == [
        "PUBLISHED_SNAPSHOT_BINDING_NOT_VERIFIED"
    ]
    assert result["status"] == "uncertain_after_switch"
    assert result["published"] is None


def test_unknown_ingest_receipt_schema_cannot_self_attest(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    hooks = _success_hooks(tmp_path, previous, current, phases)

    def unknown_ingest(
        workspace: update.UpdateWorkspace,
        diff: update.SourceDiff,
        paths: update.UpdatePaths,
    ) -> dict[str, object]:
        del workspace, diff, paths
        phases.append("ingest")
        return {
            "schema": "unknown-ingest-receipt/v1",
            "completed": True,
        }

    unknown_hooks = update.UpdateHooks(
        load_previous_manifest=hooks.load_previous_manifest,
        scan_manifest=hooks.scan_manifest,
        check_capability=hooks.check_capability,
        stage_snapshot=hooks.stage_snapshot,
        plan_changes=hooks.plan_changes,
        ingest_changes=unknown_ingest,
        drain_worker=hooks.drain_worker,
        run_narrow_gates=hooks.run_narrow_gates,
        publish_atomic=hooks.publish_atomic,
        verify_publication=hooks.verify_publication,
    )

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=unknown_hooks,
    )

    assert result["status"] == "blocked"
    assert result["gapCodes"] == [
        "BLUEPRINT_INGEST_RECEIPT_INVALID"
    ]
    assert result["published"] is False
    assert phases == ["stage", "plan", "ingest"]


def test_gate_failure_and_worker_block_never_publish(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    gate_phases: list[str] = []
    gate_result = update.run_incremental_update(
        _paths(tmp_path / "gate"),
        hooks=_success_hooks(
            tmp_path / "gate",
            previous,
            current,
            gate_phases,
            gate_passed=False,
        ),
    )
    worker_phases: list[str] = []
    worker_result = update.run_incremental_update(
        _paths(tmp_path / "worker"),
        hooks=_success_hooks(
            tmp_path / "worker",
            previous,
            current,
            worker_phases,
            worker_blocked=True,
        ),
    )

    assert gate_result["status"] == "gate_failed"
    assert gate_phases == [
        "stage",
        "plan",
        "ingest",
        "worker",
        "gates",
    ]
    assert worker_result["gapCodes"] == ["REBUILD_QUEUE_NOT_DRAINED"]
    assert worker_phases == ["stage", "plan", "ingest", "worker"]


@pytest.mark.parametrize(
    "gate_result",
    [
        update.GateResult(passed=True, checks=()),
        update.GateResult(
            passed=True,
            checks=(
                {
                    "id": "fixture.narrow",
                    "passed": False,
                    "detail": "contradictory",
                },
            ),
        ),
    ],
)
def test_narrow_gate_result_cannot_self_attest(
    tmp_path: Path,
    gate_result: update.GateResult,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    hooks = _success_hooks(tmp_path, previous, current, phases)

    def invalid_gates(
        workspace: update.UpdateWorkspace,
    ) -> update.GateResult:
        del workspace
        phases.append("gates")
        return gate_result

    invalid_hooks = update.UpdateHooks(
        load_previous_manifest=hooks.load_previous_manifest,
        scan_manifest=hooks.scan_manifest,
        check_capability=hooks.check_capability,
        stage_snapshot=hooks.stage_snapshot,
        plan_changes=hooks.plan_changes,
        ingest_changes=hooks.ingest_changes,
        drain_worker=hooks.drain_worker,
        run_narrow_gates=invalid_gates,
        publish_atomic=hooks.publish_atomic,
        verify_publication=hooks.verify_publication,
    )

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=invalid_hooks,
    )

    assert result["gapCodes"] == ["NARROW_GATE_RESULT_INVALID"]
    assert result["status"] == "blocked"
    assert phases == ["stage", "plan", "ingest", "worker", "gates"]


def test_empty_worker_report_cannot_self_attest_queue_drain(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    hooks = _success_hooks(tmp_path, previous, current, phases)
    empty_worker_hooks = update.UpdateHooks(
        load_previous_manifest=hooks.load_previous_manifest,
        scan_manifest=hooks.scan_manifest,
        check_capability=hooks.check_capability,
        stage_snapshot=hooks.stage_snapshot,
        plan_changes=hooks.plan_changes,
        ingest_changes=hooks.ingest_changes,
        drain_worker=lambda workspace, max_items: {},
        run_narrow_gates=hooks.run_narrow_gates,
        publish_atomic=hooks.publish_atomic,
        verify_publication=hooks.verify_publication,
    )

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=empty_worker_hooks,
    )

    assert result["status"] == "blocked"
    assert result["gapCodes"] == ["REBUILD_QUEUE_NOT_DRAINED"]
    assert result["published"] is False


def test_zero_worker_report_cannot_self_attest_queue_drain(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    hooks = _success_hooks(tmp_path, previous, current, phases)
    zero_worker_hooks = update.UpdateHooks(
        load_previous_manifest=hooks.load_previous_manifest,
        scan_manifest=hooks.scan_manifest,
        check_capability=hooks.check_capability,
        stage_snapshot=hooks.stage_snapshot,
        plan_changes=hooks.plan_changes,
        ingest_changes=hooks.ingest_changes,
        drain_worker=lambda workspace, max_items: {
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "blocked_gap": 0,
            "remaining_pending": 0,
            "remaining_running": 0,
        },
        run_narrow_gates=hooks.run_narrow_gates,
        publish_atomic=hooks.publish_atomic,
        verify_publication=hooks.verify_publication,
    )

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=zero_worker_hooks,
    )

    assert result["status"] == "blocked"
    assert result["gapCodes"] == ["REBUILD_QUEUE_NOT_DRAINED"]
    assert result["published"] is False
    assert phases == ["stage", "plan", "ingest"]


def test_source_diff_and_plan_output_cannot_leak_injected_host_paths(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    hooks = _success_hooks(tmp_path, previous, current, phases)

    def path_plan(
        workspace: update.UpdateWorkspace,
        diff: update.SourceDiff,
    ) -> list[dict[str, object]]:
        plan = dict(hooks.plan_changes(workspace, diff)[0])
        plan["eventKind"] = str(tmp_path / "private")
        plan["hostPath"] = str(tmp_path / "secret.txt")
        return [plan]

    path_hooks = update.UpdateHooks(
        load_previous_manifest=hooks.load_previous_manifest,
        scan_manifest=hooks.scan_manifest,
        check_capability=hooks.check_capability,
        stage_snapshot=hooks.stage_snapshot,
        plan_changes=path_plan,
        ingest_changes=hooks.ingest_changes,
        drain_worker=hooks.drain_worker,
        run_narrow_gates=hooks.run_narrow_gates,
        publish_atomic=hooks.publish_atomic,
        verify_publication=hooks.verify_publication,
    )

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=path_hooks,
    )

    encoded = json.dumps(result, sort_keys=True)
    assert result["status"] == "published"
    assert str(tmp_path) not in encoded
    assert result["selectiveInvalidationPlan"]["eventKinds"] == []


def test_result_and_scan_error_do_not_expose_absolute_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        update,
        "load_current_source_manifest",
        lambda paths: None,
    )

    def blocked_scan(paths: update.UpdatePaths) -> update.SourceManifest:
        del paths
        raise update.UpdateBlocked(
            "SEMANTIC_INPUT_SCAN_FAILED",
            "One or more semantic inputs are missing or unreadable.",
            full_rebuild_required=True,
        )

    monkeypatch.setattr(update, "scan_source_manifest", blocked_scan)
    result = update.run_incremental_update(_paths(tmp_path))

    encoded = json.dumps(result, sort_keys=True)
    assert str(tmp_path) not in encoded
    assert "C:\\" not in encoded
    assert result["gapCodes"] == ["SEMANTIC_INPUT_SCAN_FAILED"]


def test_out_of_scope_staging_workspace_is_blocked_without_cleanup(
    tmp_path: Path,
) -> None:
    previous = _manifest(_revision("old"))
    current = _manifest(_revision("new"))
    phases: list[str] = []
    hooks = _success_hooks(
        tmp_path,
        previous,
        current,
        phases,
    )
    unsafe_root = tmp_path / "must-survive"
    unsafe_root.mkdir()
    marker = unsafe_root / "user-data.txt"
    marker.write_text("preserve", encoding="utf-8")

    unsafe_hooks = update.UpdateHooks(
        load_previous_manifest=hooks.load_previous_manifest,
        scan_manifest=hooks.scan_manifest,
        check_capability=hooks.check_capability,
        stage_snapshot=lambda paths: update.UpdateWorkspace(
            temporary_root=unsafe_root,
            snapshot_dir=unsafe_root / "snapshot",
            core_path=unsafe_root / "core.sqlite",
            cache_path=unsafe_root / "cache.sqlite",
            projection_dir=unsafe_root / "exports",
        ),
        plan_changes=hooks.plan_changes,
        ingest_changes=hooks.ingest_changes,
        drain_worker=hooks.drain_worker,
        run_narrow_gates=hooks.run_narrow_gates,
        publish_atomic=hooks.publish_atomic,
        verify_publication=hooks.verify_publication,
    )

    result = update.run_incremental_update(
        _paths(tmp_path),
        hooks=unsafe_hooks,
    )

    assert result["status"] == "blocked"
    assert result["gapCodes"] == ["STAGING_ROOT_OUT_OF_SCOPE"]
    assert marker.read_text(encoding="utf-8") == "preserve"
