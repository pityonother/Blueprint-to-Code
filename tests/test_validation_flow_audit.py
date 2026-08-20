from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_validation_flow.py"


class ValidationFlowAuditTests(unittest.TestCase):
    def test_audit_reports_duplicate_validation_and_frozen_btc_counts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflow = root / "ci.yml"
            workflow.write_text(
                "\n".join(
                    (
                        "- name: Lint changed Python files",
                        "  run: python -m ruff check -- changed.py",
                        "- name: Lint full Python tree",
                        "  run: python -m ruff check scripts tests",
                        "- name: Run full Python suite",
                        "  run: python -m pytest -q",
                        "- name: Run focused suite",
                        "  run: python -m pytest -q tests/test_focus.py",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            source_root = root / "scripts"
            source_root.mkdir()
            (source_root / "one.py").write_text(
                "def _sha256_file(path):\n    return path\n",
                encoding="utf-8",
            )
            (source_root / "two.py").write_text(
                "def sha256_file(path):\n    return path\n",
                encoding="utf-8",
            )
            result = root / "result.json"
            result.write_text(
                json.dumps({"counts": {"samples": 62, "ready": 62}}),
                encoding="utf-8",
            )
            bindings = root / "bindings.json"
            bindings.write_text(
                json.dumps({"assetSourceInputs": [{} for _ in range(18)]}),
                encoding="utf-8",
            )
            matrix = root / "matrix.json"
            matrix.write_text(
                json.dumps({"rows": [{} for _ in range(14)]}),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--workflow",
                    str(workflow),
                    "--source-root",
                    str(source_root),
                    "--btc-result",
                    str(result),
                    "--input-bindings",
                    str(bindings),
                    "--query-matrix",
                    str(matrix),
                    "--duration",
                    "related-tests=18.581",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(
                payload["schema"],
                "blueprint-to-code.validation-flow-audit.v1",
            )
            self.assertEqual(payload["btcBaseline"]["samples"], 62)
            self.assertEqual(payload["btcBaseline"]["formallyQueryable"], 62)
            self.assertEqual(payload["btcBaseline"]["physicalAssetInputs"], 18)
            self.assertEqual(payload["btcBaseline"]["realQueries"], 14)
            self.assertEqual(payload["durationsSeconds"]["related-tests"], 18.581)
            self.assertEqual(payload["workflow"]["pytestInvocationCount"], 2)
            self.assertEqual(payload["workflow"]["redundantPytestInvocationCount"], 1)
            self.assertTrue(payload["workflow"]["duplicatesFullTreeRuff"])
            self.assertEqual(payload["hashHelpers"]["definitionCount"], 2)
            self.assertEqual(payload["hashHelpers"]["fileCount"], 2)

    def test_audit_rejects_malformed_duration_and_non_object_json(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workflow = root / "ci.yml"
            workflow.write_text("name: CI\n", encoding="utf-8")
            bad_json = root / "result.json"
            bad_json.write_text("[]\n", encoding="utf-8")

            bad_duration = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--workflow",
                    str(workflow),
                    "--duration",
                    "not-a-pair",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(bad_duration.returncode, 0)
            self.assertIn("NAME=SECONDS", bad_duration.stderr)

            bad_payload = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--workflow",
                    str(workflow),
                    "--btc-result",
                    str(bad_json),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(bad_payload.returncode, 0)
            self.assertIn("JSON object", bad_payload.stderr)


if __name__ == "__main__":
    unittest.main()
