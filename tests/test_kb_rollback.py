from __future__ import annotations

import io
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
            _snapshot(root, "build-a")
            target = _snapshot(root, "build-b")
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
            self.assertTrue(result["pointerUpdated"])
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
            _snapshot(root, "build-a")
            _snapshot(root, "build-b")
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


if __name__ == "__main__":
    unittest.main()
