from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.pointer_cas import (  # noqa: E402
    CurrentPointerBaseline,
    CurrentSnapshotBaseline,
    PointerCASConflictError,
    PointerCASDestinationError,
    PointerCASUncertainStateError,
    PointerCASWriteError,
    capture_current_snapshot_baseline,
    compare_and_swap_current_pointer,
    read_current_pointer_baseline,
    validate_current_snapshot_baseline,
)
from blueprint_translator.kb_vnext import pointer_cas as pointer_module  # noqa: E402
from blueprint_translator.kb_vnext import snapshot as snapshot_module  # noqa: E402
import build_ark_kb_vnext as build_cli  # noqa: E402


def _snapshot(root: Path, build_id: str) -> None:
    target = root / "snapshots" / build_id
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": build_id,
            }
        ),
        encoding="utf-8",
    )


def _pointer_bytes(build_id: str, *, indent: int | None = None) -> bytes:
    return (
        json.dumps(
            {
                "buildId": build_id,
                "snapshotRelativePath": f"snapshots/{build_id}",
            },
            indent=indent,
            sort_keys=True,
        )
        + ("\n" if indent is not None else "")
    ).encode("utf-8")


def _process_cas_writer(
    root_text: str,
    target_build_id: str,
    expected_build_id: str,
    expected_sha256: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    start.wait(timeout=5)
    try:
        compare_and_swap_current_pointer(
            snapshot_root=Path(root_text),
            target_build_id=target_build_id,
            expected=CurrentPointerBaseline(
                build_id=expected_build_id,
                pointer_sha256=expected_sha256,
            ),
            lock_timeout_seconds=5,
        )
    except PointerCASConflictError:
        results.put("CONFLICT")
    except Exception as exc:  # pragma: no cover - reported to parent
        results.put(f"ERROR:{type(exc).__name__}:{exc}")
    else:
        results.put(target_build_id)


class CurrentPointerCASTests(unittest.TestCase):
    def test_missing_reparse_pointer_is_not_treated_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_detector = (
                pointer_module._is_link_junction_or_reparse
            )

            with patch.object(
                pointer_module,
                "_is_link_junction_or_reparse",
                side_effect=lambda path: (
                    path.name == "current.json"
                    or real_detector(path)
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "not a regular file",
                ):
                    read_current_pointer_baseline(root)

    def test_capture_current_snapshot_freezes_raw_pointer_and_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            pointer_bytes = _pointer_bytes("build-a", indent=4)
            (root / "current.json").write_bytes(pointer_bytes)
            manifest_path = (
                root / "snapshots" / "build-a" / "manifest.json"
            )
            manifest_bytes = manifest_path.read_bytes()

            baseline = capture_current_snapshot_baseline(root)

            self.assertIsInstance(baseline, CurrentSnapshotBaseline)
            self.assertEqual(baseline.pointer.build_id, "build-a")
            self.assertEqual(
                baseline.pointer.pointer_sha256,
                hashlib.sha256(pointer_bytes).hexdigest(),
            )
            self.assertEqual(
                baseline.snapshot_dir,
                (root / "snapshots" / "build-a").resolve(),
            )
            self.assertEqual(baseline.manifest_bytes, manifest_bytes)
            self.assertEqual(
                baseline.manifest_sha256,
                hashlib.sha256(manifest_bytes).hexdigest(),
            )
            self.assertFalse(baseline.tree_validated)
            receipt = validate_current_snapshot_baseline(
                snapshot_root=root,
                baseline=baseline,
            )
            self.assertEqual(receipt["status"], "VERIFIED_NOOP")
            self.assertFalse(receipt["pointerUpdated"])

    def test_capture_rejects_pointer_race_while_manifest_is_read(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )
            real_reader = (
                pointer_module._read_validated_destination_snapshot
            )

            def switch_pointer(
                snapshot_root: Path,
                target_build_id: str,
                *,
                expected_manifest_sha256: str | None = None,
            ) -> tuple[Path, bytes, str]:
                frozen = real_reader(
                    snapshot_root,
                    target_build_id,
                    expected_manifest_sha256=(
                        expected_manifest_sha256
                    ),
                )
                (root / "current.json").write_bytes(
                    _pointer_bytes("build-b")
                )
                return frozen

            with patch.object(
                pointer_module,
                "_read_validated_destination_snapshot",
                side_effect=switch_pointer,
            ):
                with self.assertRaises(PointerCASConflictError):
                    capture_current_snapshot_baseline(root)

    def test_capture_rejects_manifest_race_under_shared_lock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )
            manifest_path = (
                root / "snapshots" / "build-a" / "manifest.json"
            )
            real_reader = (
                pointer_module._read_validated_destination_snapshot
            )
            reads = 0

            def replace_manifest(
                snapshot_root: Path,
                target_build_id: str,
                *,
                expected_manifest_sha256: str | None = None,
            ) -> tuple[Path, bytes, str]:
                nonlocal reads
                frozen = real_reader(
                    snapshot_root,
                    target_build_id,
                    expected_manifest_sha256=(
                        expected_manifest_sha256
                    ),
                )
                reads += 1
                if reads == 1:
                    manifest_path.write_bytes(
                        manifest_path.read_bytes() + b" "
                    )
                return frozen

            with patch.object(
                pointer_module,
                "_read_validated_destination_snapshot",
                side_effect=replace_manifest,
            ):
                with self.assertRaises(PointerCASDestinationError):
                    capture_current_snapshot_baseline(root)

    def test_capture_rejects_reparse_current_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )
            real_detector = (
                pointer_module._is_link_junction_or_reparse
            )

            with patch.object(
                pointer_module,
                "_is_link_junction_or_reparse",
                side_effect=lambda path: (
                    path.name == "current.json"
                    or real_detector(path)
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "not a regular file",
                ):
                    capture_current_snapshot_baseline(root)

    def test_snapshot_baseline_detects_whitespace_and_manifest_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )
            baseline = capture_current_snapshot_baseline(root)

            (root / "current.json").write_bytes(
                _pointer_bytes("build-a", indent=2)
            )
            with self.assertRaises(PointerCASConflictError):
                validate_current_snapshot_baseline(
                    snapshot_root=root,
                    baseline=baseline,
                )

            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )
            manifest_path = (
                root / "snapshots" / "build-a" / "manifest.json"
            )
            manifest = json.loads(manifest_path.read_text("utf-8"))
            manifest["unexpected"] = True
            manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            with self.assertRaises(PointerCASDestinationError):
                validate_current_snapshot_baseline(
                    snapshot_root=root,
                    baseline=baseline,
                )

    def test_bounded_read_rejects_path_replacement_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pointer_path = root / "current.json"
            pointer_path.write_bytes(_pointer_bytes("build-a"))
            replacement = root / "replacement.json"
            replacement.write_bytes(_pointer_bytes("build-a"))
            real_open = Path.open
            replaced = False

            def replace_before_open(
                path: Path,
                *args: object,
                **kwargs: object,
            ):
                nonlocal replaced
                if path == pointer_path and not replaced:
                    replacement.replace(pointer_path)
                    replaced = True
                return real_open(path, *args, **kwargs)

            with patch.object(Path, "open", replace_before_open):
                with self.assertRaisesRegex(ValueError, "changed before open"):
                    read_current_pointer_baseline(root)

    def test_expected_baseline_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )

            with self.assertRaises(TypeError):
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-a",
                )

    def test_build_id_rejects_non_string_and_noncanonical_text(self) -> None:
        invalid_values = (True, 7, "", " build-a", "build-a ")
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    CurrentPointerBaseline(
                        build_id=invalid,  # type: ignore[arg-type]
                        pointer_sha256="a" * 64,
                    )
                with self.assertRaises(ValueError):
                    pointer_module._safe_build_id(invalid)
                with self.assertRaises(ValueError):
                    snapshot_module._safe_build_id(invalid)

    def test_pointer_build_id_cannot_be_coerced_or_trimmed(self) -> None:
        cases = (
            (
                True,
                "snapshots/True",
            ),
            (
                7,
                "snapshots/7",
            ),
            (
                " build-a ",
                "snapshots/build-a",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for build_id, relative in cases:
                with self.subTest(build_id=build_id):
                    (root / "current.json").write_text(
                        json.dumps(
                            {
                                "buildId": build_id,
                                "snapshotRelativePath": relative,
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ValueError):
                        read_current_pointer_baseline(root)

    def test_pointer_and_manifest_use_separate_size_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            target = root / "snapshots" / "build-b"
            target.mkdir(parents=True)
            target_manifest = {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": "build-b",
                "databases": {
                    f"domain_exports/projection_{index:04d}.sqlite": {
                        "sha256": hashlib.sha256(
                            str(index).encode("ascii")
                        ).hexdigest(),
                        "sizeBytes": 1000 + index,
                        "integrity": "ok",
                        "foreignKeyViolations": 0,
                    }
                    for index in range(900)
                },
            }
            manifest_bytes = json.dumps(target_manifest).encode("utf-8")
            self.assertGreater(len(manifest_bytes), 150 * 1024)
            self.assertLess(len(manifest_bytes), 170 * 1024)
            (target / "manifest.json").write_bytes(manifest_bytes)
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )

            receipt = compare_and_swap_current_pointer(
                snapshot_root=root,
                target_build_id="build-b",
                expected=read_current_pointer_baseline(root),
            )

            self.assertTrue(receipt["pointerUpdated"])

            oversized_pointer = _pointer_bytes(
                "build-b"
            ) + (b" " * (20 * 1024))
            (root / "current.json").write_bytes(oversized_pointer)
            with self.assertRaisesRegex(ValueError, "size"):
                read_current_pointer_baseline(root)

    def test_build_cli_reports_uncertain_pointer_state(self) -> None:
        stderr = io.StringIO()
        uncertain = PointerCASUncertainStateError(
            "pointer state is uncertain",
            receipt={
                "schema": "ark-kb-current-pointer-cas-receipt/v1",
                "status": "UNCERTAIN_AFTER_REPLACE_ATTEMPT",
                "pointerUpdated": None,
            },
        )
        with patch.object(
            build_cli,
            "build_vnext_snapshot",
            side_effect=uncertain,
        ):
            with redirect_stderr(stderr):
                exit_code = build_cli.main(
                    [
                        "--discovery-database",
                        "fixture.sqlite",
                        "--full-snapshot",
                    ]
                )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "UNCERTAIN")
        self.assertIsNone(payload["pointerUpdated"])

    def test_receipt_hashes_are_recomputable_from_raw_pointer_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            before_bytes = _pointer_bytes("build-a", indent=4)
            (root / "current.json").write_bytes(before_bytes)
            expected = read_current_pointer_baseline(root)

            receipt = compare_and_swap_current_pointer(
                snapshot_root=root,
                target_build_id="build-b",
                expected=expected,
                operation="TEST_POINTER_CAS",
            )

            after_bytes = (root / "current.json").read_bytes()
            self.assertEqual(
                receipt["beforePointerSha256"],
                hashlib.sha256(before_bytes).hexdigest(),
            )
            self.assertEqual(
                receipt["afterPointerSha256"],
                hashlib.sha256(after_bytes).hexdigest(),
            )
            self.assertEqual(receipt["beforeBuildId"], "build-a")
            self.assertEqual(receipt["afterBuildId"], "build-b")
            self.assertTrue(receipt["pointerUpdated"])
            self.assertTrue(receipt["verifiedAfterReplace"])
            self.assertEqual(
                json.loads(after_bytes)["buildId"],
                "build-b",
            )

    def test_whitespace_only_pointer_change_conflicts_with_raw_bytes_cas(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )
            expected = read_current_pointer_baseline(root)
            changed_bytes = _pointer_bytes("build-a", indent=2)
            (root / "current.json").write_bytes(changed_bytes)

            with self.assertRaises(PointerCASConflictError):
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=expected,
                )

            self.assertEqual(
                (root / "current.json").read_bytes(),
                changed_bytes,
            )

    def test_competing_processes_with_one_baseline_allow_exactly_one_swap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for build_id in ("build-a", "build-b", "build-c"):
                _snapshot(root, build_id)
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )
            expected = read_current_pointer_baseline(root)
            self.assertIsNotNone(expected.build_id)
            self.assertIsNotNone(expected.pointer_sha256)
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            result_queue = context.Queue()
            processes = [
                context.Process(
                    target=_process_cas_writer,
                    args=(
                        str(root),
                        build_id,
                        str(expected.build_id),
                        str(expected.pointer_sha256),
                        start,
                        result_queue,
                    ),
                )
                for build_id in ("build-b", "build-c")
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(timeout=10)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
            results = [
                result_queue.get(timeout=2)
                for _process in processes
            ]
            result_queue.close()

            self.assertEqual(results.count("CONFLICT"), 1)
            winners = [item for item in results if item != "CONFLICT"]
            self.assertEqual(len(winners), 1)
            self.assertEqual(
                json.loads(
                    (root / "current.json").read_bytes()
                )["buildId"],
                winners[0],
            )

    def test_invalid_destination_is_rejected_without_pointer_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            invalid = root / "snapshots" / "build-b"
            invalid.mkdir(parents=True)
            (invalid / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "ark-kb-vnext-snapshot/v1",
                        "buildId": "different-build",
                    }
                ),
                encoding="utf-8",
            )
            before = _pointer_bytes("build-a")
            (root / "current.json").write_bytes(before)

            with self.assertRaises(PointerCASDestinationError):
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=read_current_pointer_baseline(root),
                )

            self.assertEqual((root / "current.json").read_bytes(), before)

    def test_linked_destination_is_rejected_without_pointer_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            backing = root / "snapshots" / "backing"
            backing.mkdir(parents=True)
            (backing / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "ark-kb-vnext-snapshot/v1",
                        "buildId": "build-b",
                    }
                ),
                encoding="utf-8",
            )
            destination = root / "snapshots" / "build-b"
            try:
                destination.symlink_to(
                    backing,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            before = _pointer_bytes("build-a")
            (root / "current.json").write_bytes(before)

            with self.assertRaises(PointerCASDestinationError):
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=read_current_pointer_baseline(root),
                )

            self.assertEqual((root / "current.json").read_bytes(), before)

    def test_linked_snapshots_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            external = base / "external-snapshots"
            external.mkdir()
            for build_id in ("build-a", "build-b"):
                target = external / build_id
                target.mkdir()
                (target / "manifest.json").write_text(
                    json.dumps(
                        {
                            "schema": "ark-kb-vnext-snapshot/v1",
                            "buildId": build_id,
                        }
                    ),
                    encoding="utf-8",
                )
            try:
                (root / "snapshots").symlink_to(
                    external,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            before = _pointer_bytes("build-a")
            (root / "current.json").write_bytes(before)

            with self.assertRaises(PointerCASDestinationError):
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=read_current_pointer_baseline(root),
                )

            self.assertEqual((root / "current.json").read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows junction contract")
    def test_windows_junction_snapshots_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            external = base / "external-snapshots"
            external.mkdir()
            for build_id in ("build-a", "build-b"):
                target = external / build_id
                target.mkdir()
                (target / "manifest.json").write_text(
                    json.dumps(
                        {
                            "schema": "ark-kb-vnext-snapshot/v1",
                            "buildId": build_id,
                        }
                    ),
                    encoding="utf-8",
                )
            junction = root / "snapshots"
            created = subprocess.run(
                (
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(junction),
                    str(external),
                ),
                check=False,
                capture_output=True,
                text=True,
            )
            if created.returncode != 0:
                self.skipTest(
                    "junction creation unavailable: "
                    + created.stderr.strip()
                )
            before = _pointer_bytes("build-a")
            (root / "current.json").write_bytes(before)

            with self.assertRaises(PointerCASDestinationError):
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=read_current_pointer_baseline(root),
                )

            self.assertEqual((root / "current.json").read_bytes(), before)

    def test_nonfinite_lock_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )

            for invalid in (float("nan"), float("inf")):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        compare_and_swap_current_pointer(
                            snapshot_root=root,
                            target_build_id="build-a",
                            expected=read_current_pointer_baseline(root),
                            lock_timeout_seconds=invalid,
                        )

    def test_noop_receipt_does_not_claim_post_replace_verification(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )

            receipt = compare_and_swap_current_pointer(
                snapshot_root=root,
                target_build_id="build-a",
                expected=read_current_pointer_baseline(root),
            )

            self.assertEqual(receipt["status"], "VERIFIED_NOOP")
            self.assertFalse(receipt["pointerUpdated"])
            self.assertFalse(receipt["verifiedAfterReplace"])
            self.assertTrue(receipt["verifiedUnderLock"])

    def test_transient_sharing_violation_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )
            real_replace = pointer_module.os.replace
            attempts = 0

            def transient_replace(source: Path, destination: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("injected sharing violation")
                real_replace(source, destination)

            with patch.object(
                pointer_module.os,
                "replace",
                side_effect=transient_replace,
            ):
                receipt = compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=read_current_pointer_baseline(root),
                )

            self.assertEqual(attempts, 3)
            self.assertTrue(receipt["pointerUpdated"])

    def test_target_manifest_change_before_replace_preserves_pointer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            before = _pointer_bytes("build-a")
            (root / "current.json").write_bytes(before)
            target_manifest = (
                root / "snapshots" / "build-b" / "manifest.json"
            )
            target_sha256 = hashlib.sha256(
                target_manifest.read_bytes()
            ).hexdigest()

            def mutate_target(phase: str) -> None:
                if phase == "before_replace":
                    target_manifest.write_text(
                        json.dumps(
                            {
                                "schema": "ark-kb-vnext-snapshot/v1",
                                "buildId": "build-b",
                                "tampered": True,
                            }
                        ),
                        encoding="utf-8",
                    )

            with self.assertRaisesRegex(
                PointerCASDestinationError,
                "SHA-256 changed",
            ):
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=read_current_pointer_baseline(root),
                    expected_target_manifest_sha256=target_sha256,
                    fault_injector=mutate_target,
                )

            self.assertEqual((root / "current.json").read_bytes(), before)

    def test_failure_before_replace_is_typed_and_preserves_pointer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            before = _pointer_bytes("build-a")
            (root / "current.json").write_bytes(before)

            def fail_before_replace(phase: str) -> None:
                if phase == "before_replace":
                    raise OSError("injected pre-replace failure")

            with self.assertRaises(PointerCASWriteError) as raised:
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=read_current_pointer_baseline(root),
                    fault_injector=fail_before_replace,
                )

            self.assertEqual((root / "current.json").read_bytes(), before)
            self.assertEqual(
                raised.exception.receipt["status"],
                "NOT_REPLACED",
            )
            self.assertFalse(
                raised.exception.receipt["pointerUpdated"]
            )

    def test_failure_after_replace_reports_uncertain_observed_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            (root / "current.json").write_bytes(
                _pointer_bytes("build-a")
            )

            def fail_after_replace(phase: str) -> None:
                if phase == "after_replace":
                    raise OSError("injected post-replace failure")

            with self.assertRaises(
                PointerCASUncertainStateError
            ) as raised:
                compare_and_swap_current_pointer(
                    snapshot_root=root,
                    target_build_id="build-b",
                    expected=read_current_pointer_baseline(root),
                    fault_injector=fail_after_replace,
                )

            receipt = raised.exception.receipt
            observed_bytes = (root / "current.json").read_bytes()
            self.assertEqual(receipt["pointerUpdated"], None)
            self.assertEqual(
                receipt["status"],
                "UNCERTAIN_AFTER_REPLACE_ATTEMPT",
            )
            self.assertEqual(receipt["observedBuildId"], "build-b")
            self.assertEqual(
                receipt["observedPointerSha256"],
                hashlib.sha256(observed_bytes).hexdigest(),
            )
            self.assertTrue(receipt["observedMatchesIntended"])


if __name__ == "__main__":
    unittest.main()
