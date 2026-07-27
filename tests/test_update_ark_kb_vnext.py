from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import update_ark_kb_vnext as update
from blueprint_translator.kb_vnext import (  # noqa: E402
    source_manifest as source_manifest_module,
)
from blueprint_translator.kb_vnext.native_ingest import (  # noqa: E402
    NativeEvidenceSet,
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
        return {"verifiedSources": 1}

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


def test_default_first_run_fails_before_lock_stage_or_output_creation(
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

    assert result["gapCodes"] == [
        "DELETED_SOURCE_FULL_REBUILD_REQUIRED"
    ]
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

    assert result["gapCodes"] == [
        "SELECTIVE_UPDATE_CAPABILITY_UNAVAILABLE"
    ]
    assert result["fullRebuildRequired"] is True
    assert calls == []
    assert not _paths(tmp_path).output.exists()


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
