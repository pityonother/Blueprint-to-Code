from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext import safe_staging  # noqa: E402
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SourceManifest,
    SourceRevision,
    source_id,
    source_manifest_binding,
)
from blueprint_translator.kb_vnext.update_baseline import (  # noqa: E402
    UpdateBaselineBlockedGap,
    build_update_baseline,
    cleanup_staged_baseline_snapshot,
    freeze_additive_blueprint_input,
    stage_snapshot_from_baseline,
    validate_frozen_additive_blueprint_input,
)
from scripts import update_ark_kb_vnext as update  # noqa: E402


GENERATED_AT = "2026-07-30T00:00:00+00:00"
ENTITY_URI = "/Game/Test/Added.Added"
REVISION_LABEL = "revision-added"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _thaw_json(value):
    if hasattr(value, "items"):
        return {
            str(key): _thaw_json(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _write_sqlite(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE marker(value TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.commit()
        assert connection.execute(
            "PRAGMA journal_mode=DELETE"
        ).fetchone() == ("delete",)
    finally:
        connection.close()


def _semantic(fingerprint: str) -> SourceRevision:
    uri = "semantic-input://captures"
    return SourceRevision(
        source_id=source_id("SEMANTIC_INPUT", uri),
        source_kind="SEMANTIC_INPUT",
        source_uri=uri,
        fingerprint=fingerprint,
    )


def _manifest(*entries: SourceRevision) -> SourceManifest:
    return SourceManifest(entries=tuple(entries), generated_at=GENERATED_AT)


def _write_evidence_bundle(
    capture_root: Path,
    *,
    asset_name: str = "Added",
    entity_uri: str = ENTITY_URI,
    revision_label: str = REVISION_LABEL,
    manifest_bytes: bytes | None = b'{"bundle":"fixture"}\n',
) -> tuple[SourceRevision, Path, Path | None]:
    evidence_dir = capture_root / asset_name / "evidence"
    evidence_dir.mkdir(parents=True)
    database = evidence_dir / "evidence.sqlite"
    connection = sqlite3.connect(database)
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
                ?, 'fixture-asset', ?, ?, ?,
                'fixture-parser', 'fixture-schema',
                '2026-07-30T00:00:00Z', 'Added.uasset'
            )
            """,
            (
                revision_label,
                asset_name,
                entity_uri,
                "c" * 64,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    adjacent = evidence_dir / "manifest.json"
    if manifest_bytes is not None:
        adjacent.write_bytes(manifest_bytes)
    aggregate = hashlib.sha256()
    aggregate.update(b"evidence.sqlite\0")
    aggregate.update(database.read_bytes())
    aggregate.update(b"\n")
    if manifest_bytes is not None:
        aggregate.update(b"manifest.json\0")
        aggregate.update(manifest_bytes)
        aggregate.update(b"\n")
    uri = f"capture://{asset_name}"
    revision = SourceRevision(
        source_id=source_id("BLUEPRINT_EVIDENCE", uri),
        source_kind="BLUEPRINT_EVIDENCE",
        source_uri=uri,
        fingerprint=aggregate.hexdigest(),
        size_bytes=database.stat().st_size,
        entity_uri=entity_uri,
        revision_label=revision_label,
    )
    return revision, database, adjacent if manifest_bytes is not None else None


def _pointer_bytes(build_id: str) -> bytes:
    return json.dumps(
        {
            "buildId": build_id,
            "snapshotRelativePath": f"snapshots/{build_id}",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fixture(
    tmp_path: Path,
    *,
    manifest_bytes: bytes | None = b'{"bundle":"fixture"}\n',
):
    root = tmp_path / "vnext"
    snapshot = root / "snapshots" / "build-a"
    _write_sqlite(snapshot / "core.sqlite", "core")
    _write_sqlite(snapshot / "cache.sqlite", "cache")
    base = _manifest(_semantic("1" * 64))
    manifest = {
        "schema": "ark-kb-vnext-snapshot/v1",
        "buildId": "build-a",
        "databases": {
            name: {
                "bytes": (snapshot / name).stat().st_size,
                "sha256": _sha256(snapshot / name),
            }
            for name in ("core.sqlite", "cache.sqlite")
        },
        "qualityGates": {
            "sealedInSnapshotManifest": True,
            "cutoverEligible": False,
        },
        "cutover": {
            "mode": "shadow",
            "defaultQuerySource": "legacy",
        },
        "incrementalUpdate": source_manifest_binding(base),
    }
    (snapshot / "manifest.json").write_bytes(
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    root.mkdir(exist_ok=True)
    pointer = _pointer_bytes("build-a")
    (root / "current.json").write_bytes(pointer)
    capture_root = tmp_path / "captures"
    revision, database, adjacent = _write_evidence_bundle(
        capture_root,
        manifest_bytes=manifest_bytes,
    )
    candidate = _manifest(_semantic("2" * 64), revision)
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=candidate,
    )
    staged = stage_snapshot_from_baseline(
        baseline,
        destination=root / ".incremental-staging",
    )
    return {
        "root": root,
        "pointer": pointer,
        "baseline": baseline,
        "staged": staged,
        "capture_root": capture_root,
        "revision": revision,
        "database": database,
        "manifest": adjacent,
    }


def _freeze(fixture: dict[str, object], *, fault_injector=None):
    return freeze_additive_blueprint_input(
        fixture["baseline"],
        capture_root=fixture["capture_root"],
        staged_snapshot=fixture["staged"],
        fault_injector=fault_injector,
    )


def _cleanup(fixture: dict[str, object]) -> None:
    cleanup_staged_baseline_snapshot(
        fixture["staged"],
        snapshot_root=fixture["root"],
    )


@pytest.mark.parametrize(
    "manifest_bytes",
    [b'{"bundle":"fixture"}\n', None],
)
def test_single_additive_blueprint_is_frozen_outside_snapshot(
    tmp_path: Path,
    manifest_bytes: bytes | None,
) -> None:
    fixture = _fixture(tmp_path, manifest_bytes=manifest_bytes)

    frozen = _freeze(fixture)

    try:
        source_id_value = fixture["revision"].source_id
        expected_ingest = (
            fixture["staged"].temporary_root
            / "quarantine"
            / source_id_value
        )
        assert frozen.ingest_root == expected_ingest
        assert frozen.quarantine_root == fixture["staged"].temporary_root / (
            "quarantine"
        )
        assert not frozen.ingest_root.is_relative_to(
            fixture["staged"].snapshot_dir
        )
        expected_names = {"evidence.sqlite"}
        if manifest_bytes is not None:
            expected_names.add("manifest.json")
        assert {path.name for path in frozen.ingest_root.iterdir()} == (
            expected_names
        )
        assert not os.path.samefile(
            fixture["database"],
            frozen.ingest_root / "evidence.sqlite",
        )
        if manifest_bytes is None:
            assert frozen.receipt["manifestArtifact"] is None
        else:
            assert frozen.receipt["manifestArtifact"] is not None
        assert frozen.receipt["published"] is False
        assert frozen.receipt["productionAuthority"] is False
        assert frozen.receipt["e4Scenario2Complete"] is False
        assert frozen.receipt["cutoverEligible"] is False
        assert frozen.receipt["mode"] == "shadow"
        assert frozen.receipt["defaultQuerySource"] == "legacy"
        assert str(tmp_path) not in json.dumps(
            dict(frozen.receipt),
            sort_keys=True,
            default=lambda value: dict(value),
        )
        validate_frozen_additive_blueprint_input(frozen)
    finally:
        _cleanup(fixture)


def test_freeze_rejects_more_than_one_added_blueprint(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    second, _database, _manifest_path = _write_evidence_bundle(
        fixture["capture_root"],
        asset_name="Second",
        entity_uri="/Game/Test/Second.Second",
        revision_label="revision-second",
    )
    candidate = _manifest(
        _semantic("2" * 64),
        fixture["revision"],
        second,
    )
    fixture["baseline"] = build_update_baseline(
        snapshot_root=fixture["root"],
        candidate_source_manifest=candidate,
    )

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_REQUIRES_SINGLE_BLUEPRINT"
        )
        assert not (
            fixture["staged"].temporary_root / "quarantine"
        ).exists()
    finally:
        _cleanup(fixture)


@pytest.mark.parametrize(
    ("field_name", "replacement_value", "expected_gap"),
    (
        (
            "fingerprint",
            "f" * 64,
            "ADDITIVE_QUARANTINE_AGGREGATE_MISMATCH",
        ),
        (
            "size_bytes",
            1,
            "ADDITIVE_QUARANTINE_AGGREGATE_MISMATCH",
        ),
        (
            "entity_uri",
            "/Game/Test/Other.Other",
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
        ),
        (
            "revision_label",
            "revision-other",
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
        ),
    ),
)
def test_source_revision_identity_mismatch_is_rejected(
    tmp_path: Path,
    field_name: str,
    replacement_value: object,
    expected_gap: str,
) -> None:
    fixture = _fixture(tmp_path)
    _cleanup(fixture)
    revision = fixture["revision"]
    if field_name == "size_bytes":
        replacement_value = revision.size_bytes + 1
    mismatched = replace(
        revision,
        **{field_name: replacement_value},
    )
    candidate = _manifest(_semantic("2" * 64), mismatched)
    fixture["baseline"] = build_update_baseline(
        snapshot_root=fixture["root"],
        candidate_source_manifest=candidate,
    )
    fixture["staged"] = stage_snapshot_from_baseline(
        fixture["baseline"],
        destination=(
            fixture["root"] / ".incremental-staging"
        ),
    )

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture)

    try:
        assert caught.value.gap_code == expected_gap
    finally:
        _cleanup(fixture)


def test_evidence_change_during_copy_is_blocked_and_quarantine_removed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate(phase: str, relative: str) -> None:
        del relative
        if phase == "after_copy":
            fixture["database"].write_bytes(
                fixture["database"].read_bytes() + b"changed"
            )

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture, fault_injector=mutate)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED"
        )
        assert not (
            fixture["staged"].temporary_root / "quarantine"
        ).exists()
        assert (fixture["root"] / "current.json").read_bytes() == (
            fixture["pointer"]
        )
    finally:
        _cleanup(fixture)


def test_live_evidence_change_after_quarantine_is_blocked(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate(phase: str, relative: str) -> None:
        del relative
        if phase == "before_receipt":
            fixture["database"].write_bytes(
                fixture["database"].read_bytes() + b"changed-late"
            )

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture, fault_injector=mutate)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED"
        )
        assert not (
            fixture["staged"].temporary_root / "quarantine"
        ).exists()
    finally:
        _cleanup(fixture)


def test_adjacent_manifest_change_during_copy_is_blocked(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate(phase: str, relative: str) -> None:
        del relative
        if phase == "after_copy":
            fixture["manifest"].write_bytes(b'{"changed":true}\n')

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture, fault_injector=mutate)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED"
        )
        assert not (
            fixture["staged"].temporary_root / "quarantine"
        ).exists()
    finally:
        _cleanup(fixture)


def test_source_symlink_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    database = fixture["database"]
    backing = database.with_name("backing.sqlite")
    database.rename(backing)
    try:
        database.symlink_to(backing)
    except OSError as exc:
        backing.rename(database)
        _cleanup(fixture)
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_REPARSE_POINT_REJECTED"
        )
    finally:
        _cleanup(fixture)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_source_windows_junction_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    asset_root = fixture["database"].parents[1]
    backing = asset_root.with_name("Added-backing")
    asset_root.rename(backing)
    result = subprocess.run(
        [
            "cmd",
            "/c",
            "mklink",
            "/J",
            str(asset_root),
            str(backing),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        backing.rename(asset_root)
        _cleanup(fixture)
        pytest.skip("junction creation unavailable")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_REPARSE_POINT_REJECTED"
        )
    finally:
        _cleanup(fixture)


@pytest.mark.skipif(os.name == "nt", reason="POSIX special-file test")
def test_source_posix_special_file_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    database = fixture["database"]
    database.unlink()
    os.mkfifo(database)

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_SPECIAL_FILE_REJECTED"
        )
    finally:
        _cleanup(fixture)


def test_source_hardlink_alias_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    alias = fixture["database"].with_name("alias.sqlite")
    try:
        os.link(fixture["database"], alias)
    except OSError as exc:
        _cleanup(fixture)
        pytest.skip(f"hardlink creation unavailable: {exc}")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_HARDLINK_ALIAS_REJECTED"
        )
    finally:
        _cleanup(fixture)


def test_extra_quarantine_file_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def inject_extra(phase: str, relative: str) -> None:
        del relative
        if phase == "after_copy":
            target = (
                fixture["staged"].temporary_root
                / "quarantine"
                / fixture["revision"].source_id
                / "extra.txt"
            )
            target.write_text("unexpected", encoding="utf-8")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture, fault_injector=inject_extra)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_ARTIFACT_SET_MISMATCH"
        )
    finally:
        _cleanup(fixture)


def test_tampered_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    frozen = _freeze(fixture)
    tampered = dict(frozen.receipt)
    tampered["published"] = True

    try:
        with pytest.raises(ValueError, match="receipt"):
            replace(frozen, receipt=tampered)
    finally:
        _cleanup(fixture)


def test_recomputed_proof_cannot_hide_receipt_tampering(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    frozen = _freeze(fixture)
    tampered = _thaw_json(frozen.receipt)
    tampered["pointerSha256"] = "f" * 64
    body = {
        key: value
        for key, value in tampered.items()
        if key != "proof"
    }
    tampered["proof"] = "quarantine-proof://" + hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    try:
        with pytest.raises(ValueError, match="receipt"):
            replace(frozen, receipt=tampered)
    finally:
        _cleanup(fixture)


def test_source_parent_replacement_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    asset_root = fixture["database"].parents[1]
    moved = asset_root.with_name("Added-moved")

    def replace_parent(phase: str, relative: str) -> None:
        del relative
        if phase == "after_source_enumeration":
            asset_root.rename(moved)

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture, fault_injector=replace_parent)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED"
        )
    finally:
        _cleanup(fixture)


def test_destination_parent_replacement_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    staging_parent = fixture["staged"].temporary_root.parent
    moved = staging_parent.with_name(".incremental-staging-moved")

    def replace_parent(phase: str, relative: str) -> None:
        del relative
        if phase == "after_destination_created":
            staging_parent.rename(moved)

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture, fault_injector=replace_parent)

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED"
        )
    finally:
        if moved.exists() and not staging_parent.exists():
            moved.rename(staging_parent)
        _cleanup(fixture)


def test_missing_quarantine_blocks_before_staged_core_write(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    workspace = update.UpdateWorkspace(
        temporary_root=fixture["staged"].temporary_root,
        snapshot_dir=fixture["staged"].snapshot_dir,
        core_path=fixture["staged"].snapshot_dir / "core.sqlite",
        cache_path=fixture["staged"].snapshot_dir / "cache.sqlite",
        projection_dir=(
            fixture["staged"].snapshot_dir / "domain_exports"
        ),
    )
    before = workspace.core_path.read_bytes()
    paths = update.UpdatePaths(
        discovery_database=tmp_path / "discovery.sqlite",
        capture_root=fixture["capture_root"],
        native_root=tmp_path / "native",
        runtime_root=tmp_path / "runtime",
        legacy_kb_root=tmp_path / "legacy",
        map_evidence_catalog=tmp_path / "map.json",
        output=fixture["root"],
    )

    with pytest.raises(update.UpdateBlocked) as caught:
        update.ingest_additive_blueprint_changes(
            workspace,
            fixture["baseline"].source_diff,
            paths,
        )

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_RECEIPT_MISSING"
        )
        assert workspace.core_path.read_bytes() == before
        assert workspace.invalidation_events == []
    finally:
        _cleanup(fixture)


def test_tampered_quarantine_blocks_before_staged_core_write(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    frozen = _freeze(fixture)
    workspace = update.UpdateWorkspace(
        temporary_root=fixture["staged"].temporary_root,
        snapshot_dir=fixture["staged"].snapshot_dir,
        core_path=fixture["staged"].snapshot_dir / "core.sqlite",
        cache_path=fixture["staged"].snapshot_dir / "cache.sqlite",
        projection_dir=(
            fixture["staged"].snapshot_dir / "domain_exports"
        ),
        frozen_additive_input=frozen,
    )
    before = workspace.core_path.read_bytes()
    (frozen.ingest_root / "evidence.sqlite").write_bytes(
        (frozen.ingest_root / "evidence.sqlite").read_bytes()
        + b"tampered"
    )
    paths = update.UpdatePaths(
        discovery_database=tmp_path / "discovery.sqlite",
        capture_root=fixture["capture_root"],
        native_root=tmp_path / "native",
        runtime_root=tmp_path / "runtime",
        legacy_kb_root=tmp_path / "legacy",
        map_evidence_catalog=tmp_path / "map.json",
        output=fixture["root"],
    )

    with pytest.raises(update.UpdateBlocked) as caught:
        update.ingest_additive_blueprint_changes(
            workspace,
            fixture["baseline"].source_diff,
            paths,
        )

    try:
        assert caught.value.gap_code == (
            "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED"
        )
        assert workspace.core_path.read_bytes() == before
        assert workspace.invalidation_events == []
    finally:
        _cleanup(fixture)


def test_ingest_reads_frozen_quarantine_not_live_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    frozen = _freeze(fixture)
    workspace = update.UpdateWorkspace(
        temporary_root=fixture["staged"].temporary_root,
        snapshot_dir=fixture["staged"].snapshot_dir,
        core_path=fixture["staged"].snapshot_dir / "core.sqlite",
        cache_path=fixture["staged"].snapshot_dir / "cache.sqlite",
        projection_dir=(
            fixture["staged"].snapshot_dir / "domain_exports"
        ),
        invalidation_events=[
            {
                "phase": "PLANNED",
                "eventKind": "ASSET",
                "sourceIds": [frozen.source_id],
                "entityIds": [1],
            }
        ],
        staged_baseline=fixture["staged"],
        update_baseline=fixture["baseline"],
        frozen_additive_input=frozen,
    )
    sqlite3.connect(tmp_path / "discovery.sqlite").close()
    fixture["database"].write_bytes(
        fixture["database"].read_bytes() + b"live-changed"
    )
    observed_roots: list[Path] = []

    def materialize(
        discovery: sqlite3.Connection,
        core: sqlite3.Connection,
        *,
        capture_root: Path,
        ontology: object,
        source_revisions,
        frozen_evidence_root: bool,
    ):
        del discovery, core, ontology, source_revisions
        observed_roots.append(capture_root)
        assert frozen_evidence_root is True
        return SimpleNamespace(
            counts={"freshAssets": 1, "factEvidence": 0},
            entity_ids=frozenset({1}),
            fact_ids=frozenset(),
        )

    monkeypatch.setattr(
        update,
        "materialize_blueprint_defaults",
        materialize,
    )
    monkeypatch.setattr(update, "load_ontology", lambda path: object())

    def verified_delta_scope(*args, **kwargs):
        del args, kwargs
        core = sqlite3.connect(workspace.core_path)
        try:
            after_database_sha256 = update.logical_database_state(
                core
            ).database_sha256
        finally:
            core.close()
        return SimpleNamespace(
            source_revision_ids=(1,),
            entity_ids=(1,),
            fact_ids=(1,),
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
        verified_delta_scope,
    )
    monkeypatch.setattr(
        update,
        "materialize_additive_asset_dependency_scope",
        lambda *args, **kwargs: SimpleNamespace(downstream=(1,)),
    )
    monkeypatch.setattr(
        update,
        "apply_invalidation_plan",
        lambda *args, **kwargs: {"eventId": "event-1"},
    )
    paths = update.UpdatePaths(
        discovery_database=tmp_path / "discovery.sqlite",
        capture_root=fixture["capture_root"],
        native_root=tmp_path / "native",
        runtime_root=tmp_path / "runtime",
        legacy_kb_root=tmp_path / "legacy",
        map_evidence_catalog=tmp_path / "map.json",
        output=fixture["root"],
    )

    try:
        receipt = update.ingest_additive_blueprint_changes(
            workspace,
            fixture["baseline"].source_diff,
            paths,
        )
        assert receipt["completed"] is True
        assert observed_roots == [frozen.ingest_root]
        assert observed_roots[0] != paths.capture_root
    finally:
        _cleanup(fixture)


def test_cleanup_uncertainty_returns_controlled_residual(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def fail(phase: str, relative: str) -> None:
        del relative
        if phase == "before_copy":
            raise OSError("injected copy failure")

    monkeypatch.setattr(
        safe_staging,
        "_cleanup_staging",
        lambda state: (_ for _ in ()).throw(
            OSError("injected cleanup failure")
        ),
    )
    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _freeze(fixture, fault_injector=fail)

    assert caught.value.gap_code == (
        "ADDITIVE_QUARANTINE_CLEANUP_UNCERTAIN"
    )
    assert caught.value.status == "UNCERTAIN"
    assert caught.value.residual_identifier == (
        f".incremental-staging/{fixture['staged'].staging_id}/quarantine"
    )
    assert not Path(caught.value.residual_identifier).is_absolute()
    monkeypatch.undo()
    _cleanup(fixture)
