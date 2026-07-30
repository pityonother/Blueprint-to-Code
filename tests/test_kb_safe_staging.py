from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

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
    stage_snapshot_from_baseline,
)


GENERATED_AT = "2026-07-30T00:00:00+00:00"


def _source_manifest(fingerprint: str) -> SourceManifest:
    uri = "semantic-input://captures"
    return SourceManifest(
        entries=(
            SourceRevision(
                source_id=source_id("SEMANTIC_INPUT", uri),
                source_kind="SEMANTIC_INPUT",
                source_uri=uri,
                fingerprint=fingerprint,
            ),
        ),
        generated_at=GENERATED_AT,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_sqlite(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "CREATE TABLE marker(value TEXT PRIMARY KEY)"
        )
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.commit()
        assert connection.execute(
            "PRAGMA journal_mode=DELETE"
        ).fetchone() == ("delete",)
    finally:
        connection.close()


def _pointer_bytes(build_id: str) -> bytes:
    return json.dumps(
        {
            "buildId": build_id,
            "snapshotRelativePath": f"snapshots/{build_id}",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fixture(tmp_path: Path):
    root = tmp_path / "vnext"
    build_id = "build-a"
    snapshot = root / "snapshots" / build_id
    for relative in (
        "catalog.sqlite",
        "core.sqlite",
        "search.sqlite",
        "cache.sqlite",
        "domain_exports/items.sqlite",
    ):
        _write_sqlite(snapshot / relative, relative)
    reports = snapshot / "reports"
    reports.mkdir()
    (reports / "quality.json").write_bytes(b'{"sealed":true}\n')
    (reports / "benchmark.json").write_bytes(b'{"sealed":true}\n')
    (reports / "cases.jsonl").write_bytes(b'{"case":"one"}\n')
    (reports / "failures.json").write_bytes(b'{"failures":[]}\n')
    (snapshot / "notes.txt").write_text("copy me\n", encoding="utf-8")

    databases = {
        relative: {
            "bytes": (snapshot / relative).stat().st_size,
            "sha256": _sha256(snapshot / relative),
            "integrity": "ok",
            "foreignKeyViolations": 0,
        }
        for relative in (
            "catalog.sqlite",
            "core.sqlite",
            "search.sqlite",
            "cache.sqlite",
            "domain_exports/items.sqlite",
        )
    }
    base_sources = _source_manifest("1" * 64)
    manifest = {
        "schema": "ark-kb-vnext-snapshot/v1",
        "buildId": build_id,
        "generatedAt": GENERATED_AT,
        "databases": databases,
        "qualityGates": {
            "sealedInSnapshotManifest": True,
            "reportUri": "reports/quality.json",
            "sha256": _sha256(reports / "quality.json"),
            "benchmarkUri": "reports/benchmark.json",
            "benchmarkSha256": _sha256(reports / "benchmark.json"),
            "caseResultsUri": "reports/cases.jsonl",
            "caseResultsSha256": _sha256(reports / "cases.jsonl"),
            "failureMatrixUri": "reports/failures.json",
            "failureMatrixSha256": _sha256(reports / "failures.json"),
            "cutoverEligible": False,
        },
        "cutover": {
            "mode": "shadow",
            "defaultQuerySource": "legacy",
        },
        "incrementalUpdate": source_manifest_binding(base_sources),
    }
    (snapshot / "manifest.json").write_bytes(
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    root.mkdir(exist_ok=True)
    pointer = _pointer_bytes(build_id)
    (root / "current.json").write_bytes(pointer)
    baseline = build_update_baseline(
        snapshot_root=root,
        candidate_source_manifest=_source_manifest("2" * 64),
    )
    return root, snapshot, pointer, baseline


def _stage(root: Path, baseline, *, fault_injector=None):
    return stage_snapshot_from_baseline(
        baseline,
        destination=root / ".incremental-staging",
        fault_injector=fault_injector,
    )


def _assert_clean(root: Path) -> None:
    staging = root / ".incremental-staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_normal_staging_copies_the_whole_tree_and_returns_receipt(
    tmp_path: Path,
) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)

    staged = _stage(root, baseline)

    try:
        source_names = {
            path.relative_to(source).as_posix()
            for path in source.rglob("*")
            if path.is_file()
        }
        staged_names = {
            path.relative_to(staged.snapshot_dir).as_posix()
            for path in staged.snapshot_dir.rglob("*")
            if path.is_file()
        }
        assert staged_names == source_names
        assert staged.base_build_id == "build-a"
        assert (root / "current.json").read_bytes() == pointer
        assert not (root / "snapshots" / staged.staging_id).exists()
    finally:
        cleanup_staged_baseline_snapshot(
            staged,
            snapshot_root=root,
        )


def test_staging_is_on_the_same_volume(tmp_path: Path) -> None:
    root, _source, _pointer, baseline = _fixture(tmp_path)

    staged = _stage(root, baseline)

    try:
        assert staged.receipt["sameVolume"] is True
        assert staged.temporary_root.stat().st_dev == root.stat().st_dev
    finally:
        cleanup_staged_baseline_snapshot(
            staged,
            snapshot_root=root,
        )


def test_staged_files_are_not_hardlinks_or_the_same_file_id(
    tmp_path: Path,
) -> None:
    root, source, _pointer, baseline = _fixture(tmp_path)

    staged = _stage(root, baseline)

    try:
        for original in source.rglob("*"):
            if original.is_file():
                copied = staged.snapshot_dir / original.relative_to(source)
                assert not os.path.samefile(original, copied)
        assert staged.receipt["hardlinkAliasCount"] == 0
    finally:
        cleanup_staged_baseline_snapshot(
            staged,
            snapshot_root=root,
        )


def test_source_symlink_is_blocked(tmp_path: Path) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)
    backing = tmp_path / "backing.txt"
    backing.write_text("outside\n", encoding="utf-8")
    link = source / "linked.txt"
    try:
        link.symlink_to(backing)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline)

    assert caught.value.gap_code == "STAGING_REPARSE_POINT_REJECTED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_windows_source_junction_is_blocked(tmp_path: Path) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)
    backing = tmp_path / "junction-target"
    backing.mkdir()
    junction = source / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(backing)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        pytest.skip(f"junction creation unavailable: {result.stderr}")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline)

    assert caught.value.gap_code == "STAGING_REPARSE_POINT_REJECTED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_source_parent_replacement_after_enumeration_is_blocked(
    tmp_path: Path,
) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)

    def replace(phase: str, relative: str) -> None:
        del relative
        if phase == "after_source_enumeration":
            source.rename(source.with_name("replaced-source"))

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline, fault_injector=replace)

    assert caught.value.gap_code == "STAGING_SOURCE_IDENTITY_CHANGED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_destination_parent_replacement_during_creation_is_blocked(
    tmp_path: Path,
) -> None:
    root, _source, pointer, baseline = _fixture(tmp_path)

    def replace(phase: str, relative: str) -> None:
        if phase == "after_destination_created":
            created = root / Path(relative)
            created.rename(created.with_name("replaced-destination"))

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline, fault_injector=replace)

    assert caught.value.gap_code == "STAGING_DESTINATION_IDENTITY_CHANGED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_source_file_change_during_copy_is_blocked(tmp_path: Path) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)

    def mutate(phase: str, relative: str) -> None:
        del relative
        if phase == "after_copy":
            (source / "notes.txt").write_text(
                "changed\n",
                encoding="utf-8",
            )

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline, fault_injector=mutate)

    assert caught.value.gap_code == "STAGING_SOURCE_IDENTITY_CHANGED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_current_pointer_change_during_copy_is_blocked(tmp_path: Path) -> None:
    root, _source, pointer, baseline = _fixture(tmp_path)

    def mutate(phase: str, relative: str) -> None:
        del relative
        if phase == "after_copy":
            (root / "current.json").write_bytes(pointer + b"\n")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline, fault_injector=mutate)

    assert caught.value.gap_code == "STAGING_BASELINE_CHANGED"
    _assert_clean(root)


def test_manifest_change_during_copy_is_blocked(tmp_path: Path) -> None:
    root, source, _pointer, baseline = _fixture(tmp_path)
    manifest_path = source / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()

    def mutate(phase: str, relative: str) -> None:
        del relative
        if phase == "after_copy":
            manifest_path.write_bytes(manifest_bytes + b"\n")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline, fault_injector=mutate)

    assert caught.value.gap_code == "STAGING_BASELINE_CHANGED"
    _assert_clean(root)


def test_source_manifest_fingerprint_change_is_blocked(
    tmp_path: Path,
) -> None:
    root, _source, pointer, baseline = _fixture(tmp_path)

    def mutate(phase: str, relative: str) -> None:
        del relative
        if phase == "after_copy":
            object.__setattr__(
                baseline,
                "candidate_source_manifest",
                _source_manifest("9" * 64),
            )

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline, fault_injector=mutate)

    assert caught.value.gap_code == "STAGING_BASELINE_CHANGED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_update_baseline_identity_change_is_blocked(tmp_path: Path) -> None:
    root, _source, pointer, baseline = _fixture(tmp_path)

    def mutate(phase: str, relative: str) -> None:
        del relative
        if phase == "after_copy":
            object.__setattr__(baseline, "source_diff_sha256", "9" * 64)

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline, fault_injector=mutate)

    assert caught.value.gap_code == "STAGING_BASELINE_CHANGED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX special-file contract")
def test_special_file_is_blocked(tmp_path: Path) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)
    os.mkfifo(source / "fifo")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline)

    assert caught.value.gap_code == "STAGING_SPECIAL_FILE_REJECTED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


@pytest.mark.parametrize("sidecar", ["core.sqlite-wal", "core.sqlite-shm"])
def test_sqlite_wal_and_shm_sidecars_are_blocked(
    tmp_path: Path,
    sidecar: str,
) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)
    (source / sidecar).write_bytes(b"unsafe")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline)

    assert caught.value.gap_code == "STAGING_SQLITE_VALIDATION_FAILED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_sqlite_integrity_failure_is_blocked(tmp_path: Path) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)
    core = source / "core.sqlite"
    damaged = bytearray(core.read_bytes())
    damaged[:32] = b"x" * 32
    core.write_bytes(damaged)

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline)

    assert caught.value.gap_code == "STAGING_SQLITE_VALIDATION_FAILED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_staging_on_a_different_volume_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, _source, pointer, baseline = _fixture(tmp_path)
    real_identity = safe_staging._volume_identity
    calls = 0

    def mismatch(handle_or_fd):
        nonlocal calls
        calls += 1
        value = real_identity(handle_or_fd)
        return value if calls == 1 else f"different-{value}"

    monkeypatch.setattr(safe_staging, "_volume_identity", mismatch)

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline)

    assert caught.value.gap_code == "STAGING_NOT_ON_TARGET_VOLUME"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_copy_exception_cleans_staging(
    tmp_path: Path,
) -> None:
    root, _source, pointer, baseline = _fixture(tmp_path)

    def fail(phase: str, relative: str) -> None:
        del relative
        if phase == "before_copy":
            raise OSError("injected copy failure")

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        _stage(root, baseline, fault_injector=fail)

    assert caught.value.gap_code == "STAGING_COPY_FAILED"
    assert (root / "current.json").read_bytes() == pointer
    _assert_clean(root)


def test_cleanup_uncertainty_returns_uncertain_and_residual_identifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, _source, pointer, baseline = _fixture(tmp_path)

    def fail_copy(phase: str, relative: str) -> None:
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
        _stage(root, baseline, fault_injector=fail_copy)

    assert caught.value.gap_code == "STAGING_CLEANUP_UNCERTAIN"
    assert caught.value.status == "UNCERTAIN"
    assert caught.value.residual_identifier.startswith(
        ".incremental-staging/"
    )
    assert not Path(caught.value.residual_identifier).is_absolute()
    assert (root / "current.json").read_bytes() == pointer


def test_returned_staging_cleanup_rejects_replaced_identity(
    tmp_path: Path,
) -> None:
    root, _source, pointer, baseline = _fixture(tmp_path)
    staged = _stage(root, baseline)
    moved = staged.temporary_root.with_name("moved-staging")
    staged.temporary_root.rename(moved)
    staged.temporary_root.mkdir()

    with pytest.raises(UpdateBaselineBlockedGap) as caught:
        cleanup_staged_baseline_snapshot(
            staged,
            snapshot_root=root,
        )

    assert caught.value.gap_code == "STAGING_CLEANUP_UNCERTAIN"
    assert caught.value.status == "UNCERTAIN"
    assert caught.value.residual_identifier == (
        f".incremental-staging/{staged.staging_id}"
    )
    assert (root / "current.json").read_bytes() == pointer
    shutil.rmtree(staged.temporary_root)
    shutil.rmtree(moved)


@pytest.mark.parametrize(
    "failure",
    ["reparse", "copy", "baseline"],
)
def test_every_failure_preserves_current_raw_bytes(
    tmp_path: Path,
    failure: str,
) -> None:
    root, source, pointer, baseline = _fixture(tmp_path)

    def inject(phase: str, relative: str) -> None:
        del relative
        if failure == "copy" and phase == "before_copy":
            raise OSError("copy")
        if failure == "baseline" and phase == "after_copy":
            (root / "current.json").write_bytes(pointer + b"\n")

    if failure == "reparse":
        target = tmp_path / "target"
        target.write_text("target\n", encoding="utf-8")
        try:
            (source / "link").symlink_to(target)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(UpdateBaselineBlockedGap):
        _stage(root, baseline, fault_injector=inject)

    if failure != "baseline":
        assert (root / "current.json").read_bytes() == pointer


def test_success_receipt_is_explicitly_unpublished_shadow_legacy(
    tmp_path: Path,
) -> None:
    root, _source, _pointer, baseline = _fixture(tmp_path)

    staged = _stage(root, baseline)

    try:
        receipt = staged.receipt
        assert receipt["schema"] == (
            "ark-kb-reparse-safe-staging-receipt/v1"
        )
        assert receipt["evidenceClass"] == (
            "UNSIGNED_LOCAL_REPARSE_SAFE_STAGING"
        )
        assert receipt["baseBuildId"] == baseline.base_build_id
        assert receipt["pointerSha256"] == baseline.base_pointer_sha256
        assert receipt["manifestSha256"] == baseline.base_manifest_sha256
        assert receipt["sourceManifestFingerprint"] == (
            baseline.candidate_source_manifest_fingerprint
        )
        assert receipt["sourceDiffSha256"] == baseline.source_diff_sha256
        assert receipt["sourceTreeDigest"] == receipt["stagedTreeDigest"]
        assert receipt["sameVolume"] is True
        assert receipt["sourceVerifiedUnchanged"] is True
        assert receipt["reparsePointCount"] == 0
        assert receipt["hardlinkAliasCount"] == 0
        assert receipt["copiedAuthorityFileCount"] > 0
        assert receipt["copiedNonAuthorityFileCount"] > 0
        assert receipt["cacheDisposition"] == (
            "COPIED_BUILD_BOUND_DISPOSABLE"
        )
        assert receipt["totalBytes"] > 0
        assert receipt["createdAt"].endswith("+00:00")
        assert receipt["stagingRelativePath"].startswith(
            ".incremental-staging/"
        )
        assert receipt["published"] is False
        assert receipt["productionAuthority"] is False
        assert receipt["e4Scenario2Complete"] is False
        assert receipt["cutoverEligible"] is False
        assert receipt["mode"] == "shadow"
        assert receipt["defaultQuerySource"] == "legacy"
        assert str(receipt["proof"]).startswith("staging-proof://")
    finally:
        cleanup_staged_baseline_snapshot(
            staged,
            snapshot_root=root,
        )
