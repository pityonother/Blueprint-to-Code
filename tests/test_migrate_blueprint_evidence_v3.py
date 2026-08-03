import io
import json
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

core_stub = types.ModuleType("blueprint_translator.evidence_publication")
core_stub.migrate_v2_evidence_to_v3 = mock.Mock()
with mock.patch.dict(
    sys.modules,
    {"blueprint_translator.evidence_publication": core_stub},
):
    import migrate_blueprint_evidence_v3 as migration_cli  # noqa: E402


class BlueprintEvidenceV3MigrationCliTests(unittest.TestCase):
    def test_asset_dir_is_required(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "2"):
                migration_cli.parse_args([])

    def test_default_migration_preserves_v2_and_writes_json_stdout(self):
        asset_dir = ROOT / "capture" / "Example"
        expected = {
            "asset_dir": str(asset_dir),
            "pruned_v2": False,
            "schema": "blueprint-translator.evidence-publication.v3",
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.object(
                migration_cli,
                "migrate_v2_evidence_to_v3",
                return_value=expected,
            ) as migrate,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = migration_cli.main(["--asset-dir", str(asset_dir)])

        self.assertEqual(exit_code, 0)
        migrate.assert_called_once_with(asset_dir, prune_v2=False)
        self.assertEqual(json.loads(stdout.getvalue()), expected)
        self.assertEqual(stderr.getvalue(), "")

    def test_prune_v2_is_forwarded_only_when_explicitly_requested(self):
        asset_dir = ROOT / "capture" / "Example"
        expected = {"pruned_v2": True}
        stdout = io.StringIO()

        with (
            mock.patch.object(
                migration_cli,
                "migrate_v2_evidence_to_v3",
                return_value=expected,
            ) as migrate,
            redirect_stdout(stdout),
        ):
            exit_code = migration_cli.main(
                ["--asset-dir", str(asset_dir), "--prune-v2"]
            )

        self.assertEqual(exit_code, 0)
        migrate.assert_called_once_with(asset_dir, prune_v2=True)
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_core_failure_writes_stderr_and_returns_exit_two(self):
        asset_dir = ROOT / "capture" / "Example"
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.object(
                migration_cli,
                "migrate_v2_evidence_to_v3",
                side_effect=ValueError("v3 migration failed"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = migration_cli.main(["--asset-dir", str(asset_dir)])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("v3 migration failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
