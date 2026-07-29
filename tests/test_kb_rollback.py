from __future__ import annotations

import io
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext import snapshot as snapshot_module  # noqa: E402
import rollback_ark_kb_vnext as rollback_cli  # noqa: E402


def _snapshot(root: Path, build_id: str) -> Path:
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
    return target


def _set_previous_snapshot(
    current: Path,
    previous: Path,
) -> None:
    current_manifest_path = current / "manifest.json"
    current_manifest = json.loads(
        current_manifest_path.read_text(encoding="utf-8")
    )
    previous_manifest_bytes = (previous / "manifest.json").read_bytes()
    current_manifest["previousSnapshot"] = {
        "buildId": previous.name,
        "manifestSha256": hashlib.sha256(
            previous_manifest_bytes
        ).hexdigest(),
    }
    current_manifest_path.write_text(
        json.dumps(current_manifest),
        encoding="utf-8",
    )


def _current(root: Path, build_id: str) -> None:
    (root / "current.json").write_text(
        json.dumps(
            {
                "buildId": build_id,
                "snapshotRelativePath": f"snapshots/{build_id}",
            }
        ),
        encoding="utf-8",
    )


class KnowledgeRollbackTests(unittest.TestCase):
    def test_explicit_validated_rollback_only_swaps_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = _snapshot(root, "build-a")
            target = _snapshot(root, "build-b")
            _set_previous_snapshot(current, target)
            _current(root, "build-a")

            with patch.object(
                snapshot_module,
                "_validate_staged_snapshot_for_promotion",
            ) as validate:
                result = snapshot_module.rollback_current_snapshot(
                    output_dir=root,
                    target_build_id="build-b",
                    expected_current_build_id="build-a",
                )

            validate.assert_called_once()
            self.assertEqual(
                validate.call_args.kwargs["staging"],
                target,
            )
            self.assertEqual(result["fromBuildId"], "build-a")
            self.assertEqual(result["toBuildId"], "build-b")
            self.assertEqual(
                result["evidenceClass"],
                "UNSIGNED_LOCAL_WRITE_FACT",
            )
            self.assertTrue(result["pointerUpdated"])
            self.assertEqual(
                result["pointerBeforeSha256"],
                hashlib.sha256(
                    json.dumps(
                        {
                            "buildId": "build-a",
                            "snapshotRelativePath": "snapshots/build-a",
                        }
                    ).encode("utf-8")
                ).hexdigest(),
            )
            self.assertEqual(
                result["pointerAfterSha256"],
                hashlib.sha256(
                    (root / "current.json").read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                json.loads(
                    (root / "current.json").read_text(encoding="utf-8")
                )["buildId"],
                "build-b",
            )
            self.assertTrue((root / "snapshots" / "build-a").is_dir())
            self.assertTrue((root / "snapshots" / "build-b").is_dir())

    def test_expected_current_mismatch_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            _current(root, "build-a")

            with self.assertRaisesRegex(
                ValueError,
                "expected current build",
            ):
                snapshot_module.rollback_current_snapshot(
                    output_dir=root,
                    target_build_id="build-b",
                    expected_current_build_id="unexpected-build",
                )

            self.assertEqual(
                json.loads(
                    (root / "current.json").read_text(encoding="utf-8")
                )["buildId"],
                "build-a",
            )

    def test_validation_failure_preserves_current_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            _current(root, "build-a")

            with patch.object(
                snapshot_module,
                "_validate_staged_snapshot_for_promotion",
                side_effect=ValueError("tampered target"),
            ):
                with self.assertRaisesRegex(ValueError, "tampered target"):
                    snapshot_module.rollback_current_snapshot(
                        output_dir=root,
                        target_build_id="build-b",
                        expected_current_build_id="build-a",
                    )

            self.assertEqual(
                json.loads(
                    (root / "current.json").read_text(encoding="utf-8")
                )["buildId"],
                "build-a",
            )

    def test_dry_run_validates_without_swapping_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = _snapshot(root, "build-a")
            target = _snapshot(root, "build-b")
            _set_previous_snapshot(current, target)
            _current(root, "build-a")

            with patch.object(
                snapshot_module,
                "_validate_staged_snapshot_for_promotion",
            ) as validate:
                result = snapshot_module.rollback_current_snapshot(
                    output_dir=root,
                    target_build_id="build-b",
                    expected_current_build_id="build-a",
                    dry_run=True,
                )

            validate.assert_called_once()
            self.assertFalse(result["pointerUpdated"])
            self.assertTrue(result["dryRun"])
            self.assertEqual(
                json.loads(
                    (root / "current.json").read_text(encoding="utf-8")
                )["buildId"],
                "build-a",
            )

    def test_dry_run_rechecks_target_manifest_under_shared_lock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = _snapshot(root, "build-a")
            target = _snapshot(root, "build-b")
            _set_previous_snapshot(current, target)
            _current(root, "build-a")
            target_manifest = target / "manifest.json"

            def mutate_target(**_kwargs: object) -> None:
                payload = json.loads(
                    target_manifest.read_text(encoding="utf-8")
                )
                payload["tamperedAfterValidationStarted"] = True
                target_manifest.write_text(
                    json.dumps(payload),
                    encoding="utf-8",
                )

            with patch.object(
                snapshot_module,
                "_validate_staged_snapshot_for_promotion",
                side_effect=mutate_target,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "manifest SHA-256 changed",
                ):
                    snapshot_module.rollback_current_snapshot(
                        output_dir=root,
                        target_build_id="build-b",
                        expected_current_build_id="build-a",
                        dry_run=True,
                    )

    def test_rollback_rejects_non_adjacent_target_when_chain_is_declared(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_a = _snapshot(root, "build-a")
            build_b = _snapshot(root, "build-b")
            build_c = _snapshot(root, "build-c")
            _set_previous_snapshot(build_b, build_a)
            _set_previous_snapshot(build_c, build_b)
            _current(root, "build-c")

            with patch.object(
                snapshot_module,
                "_validate_staged_snapshot_for_promotion",
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "adjacent predecessor",
                ):
                    snapshot_module.rollback_current_snapshot(
                        output_dir=root,
                        target_build_id="build-a",
                        expected_current_build_id="build-c",
                    )

            self.assertEqual(
                json.loads(
                    (root / "current.json").read_text(encoding="utf-8")
                )["buildId"],
                "build-c",
            )

    def test_rollback_without_lineage_cannot_change_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
            _current(root, "build-a")

            with patch.object(
                snapshot_module,
                "_validate_staged_snapshot_for_promotion",
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "adjacent predecessor",
                ):
                    snapshot_module.rollback_current_snapshot(
                        output_dir=root,
                        target_build_id="build-b",
                        expected_current_build_id="build-a",
                    )

            self.assertEqual(
                json.loads(
                    (root / "current.json").read_text(encoding="utf-8")
                )["buildId"],
                "build-a",
            )

    def test_rollback_accepts_declared_adjacent_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_a = _snapshot(root, "build-a")
            build_b = _snapshot(root, "build-b")
            _set_previous_snapshot(build_b, build_a)
            _current(root, "build-b")

            with patch.object(
                snapshot_module,
                "_validate_staged_snapshot_for_promotion",
            ):
                result = snapshot_module.rollback_current_snapshot(
                    output_dir=root,
                    target_build_id="build-a",
                    expected_current_build_id="build-b",
                )

            self.assertTrue(result["pointerUpdated"])
            self.assertEqual(
                json.loads(
                    (root / "current.json").read_text(encoding="utf-8")
                )["buildId"],
                "build-a",
            )

    def test_pointer_byte_change_during_validation_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = _snapshot(root, "build-a")
            target = _snapshot(root, "build-b")
            _set_previous_snapshot(current, target)
            _current(root, "build-a")
            changed_pointer = (
                json.dumps(
                    {
                        "buildId": "build-a",
                        "snapshotRelativePath": "snapshots/build-a",
                    },
                    indent=4,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")

            def change_pointer_bytes(**_kwargs: object) -> None:
                (root / "current.json").write_bytes(changed_pointer)

            with patch.object(
                snapshot_module,
                "_validate_staged_snapshot_for_promotion",
                side_effect=change_pointer_bytes,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "pointer.*changed|CAS",
                ):
                    snapshot_module.rollback_current_snapshot(
                        output_dir=root,
                        target_build_id="build-b",
                        expected_current_build_id="build-a",
                    )

            self.assertEqual(
                (root / "current.json").read_bytes(),
                changed_pointer,
            )

    def test_cli_reports_blocked_without_claiming_pointer_update(self) -> None:
        stderr = io.StringIO()
        with patch.object(
            rollback_cli,
            "rollback_current_snapshot",
            side_effect=ValueError("expected current build mismatch"),
        ):
            with redirect_stderr(stderr):
                exit_code = rollback_cli.main(
                    [
                        "--snapshot-root",
                        ".",
                        "--to-build-id",
                        "build-b",
                        "--expected-current-build-id",
                        "build-a",
                    ]
                )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertFalse(payload["pointerUpdated"])

    def test_cli_prints_validated_dry_run_receipt(self) -> None:
        stdout = io.StringIO()
        receipt = {
            "schema": "ark-kb-vnext-rollback/v1",
            "status": "VALIDATED",
            "dryRun": True,
            "pointerUpdated": False,
        }
        with patch.object(
            rollback_cli,
            "rollback_current_snapshot",
            return_value=receipt,
        ) as rollback:
            with redirect_stdout(stdout):
                exit_code = rollback_cli.main(
                    [
                        "--snapshot-root",
                        ".",
                        "--to-build-id",
                        "build-b",
                        "--expected-current-build-id",
                        "build-a",
                        "--dry-run",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), receipt)
        self.assertTrue(rollback.call_args.kwargs["dry_run"])

    def test_cli_does_not_claim_rollback_after_uncertain_replace(
        self,
    ) -> None:
        stderr = io.StringIO()
        uncertain = rollback_cli.PointerCASUncertainStateError(
            "pointer state is uncertain",
            receipt={
                "schema": "ark-kb-current-pointer-cas-receipt/v1",
                "status": "UNCERTAIN_AFTER_REPLACE_ATTEMPT",
                "pointerUpdated": None,
                "observedBuildId": "build-b",
            },
        )
        with patch.object(
            rollback_cli,
            "rollback_current_snapshot",
            side_effect=uncertain,
        ):
            with redirect_stderr(stderr):
                exit_code = rollback_cli.main(
                    [
                        "--snapshot-root",
                        ".",
                        "--to-build-id",
                        "build-b",
                        "--expected-current-build-id",
                        "build-a",
                    ]
                )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "UNCERTAIN")
        self.assertIsNone(payload["pointerUpdated"])
        self.assertEqual(
            payload["pointerCAS"]["observedBuildId"],
            "build-b",
        )


if __name__ == "__main__":
    unittest.main()
