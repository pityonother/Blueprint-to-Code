from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_report_registry import (  # noqa: E402
    ACTIVE_FORMAL,
    DIAGNOSTIC,
    HISTORICAL_PROVENANCE_INCOMPLETE,
    validate_report_registry,
)


HISTORICAL_REPORTS = {
    "reports/ARK_PLAYER_VISIBLE_REWARD_MODEL_DEEP_DIVE_2026-07-26.md",
    "reports/FEROX_FORCE_FLEE_MECHANISM_2026-07-26.md",
    "reports/TIDES_OF_FORTUNE_COMPLETE_NATIVE_2026-07-26.md",
}


def _copy_reports(destination: Path) -> None:
    shutil.copytree(ROOT / "reports", destination / "reports")


def _registry_payload(root: Path) -> dict[str, object]:
    return json.loads(
        (root / "reports" / "report_registry.json").read_text(encoding="utf-8")
    )


def _write_registry(root: Path, payload: object) -> None:
    (root / "reports" / "report_registry.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class ReportRegistryTests(unittest.TestCase):
    def test_registry_matches_its_strict_json_schema(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "report_registry_v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        payload = _registry_payload(ROOT)

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)

    def test_committed_registry_separates_active_and_historical_errors(self) -> None:
        result = validate_report_registry(ROOT)

        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["gate"]["activeFormalErrors"], 0)
        self.assertTrue(result["gate"]["passed"])
        self.assertEqual(result["summary"]["reports"], 9)
        self.assertEqual(result["summary"]["activeFormalReports"], 0)
        self.assertEqual(result["summary"]["historicalReports"], 3)
        self.assertEqual(result["summary"]["diagnosticReports"], 6)
        self.assertEqual(result["summary"]["historicalErrors"], 3)
        self.assertEqual(result["summary"]["historicalWarnings"], 3)
        self.assertEqual(result["summary"]["activeErrors"], 0)
        self.assertEqual(result["summary"]["registryErrors"], 0)
        self.assertEqual(
            {
                issue["registryStatus"]
                for issue in result["issues"]
                if issue["severity"] == "ERROR"
            },
            {HISTORICAL_PROVENANCE_INCOMPLETE},
        )
        self.assertEqual(
            {
                issue["code"]
                for issue in result["issues"]
                if issue["severity"] == "ERROR"
            },
            {"PROVENANCE_UNVERIFIED"},
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(str(ROOT), serialized)

    def test_registry_has_exact_historical_and_diagnostic_report_sets(self) -> None:
        payload = _registry_payload(ROOT)
        by_status: dict[str, set[str]] = {
            ACTIVE_FORMAL: set(),
            HISTORICAL_PROVENANCE_INCOMPLETE: set(),
            DIAGNOSTIC: set(),
        }
        for entry in payload["reports"]:
            by_status[entry["status"]].add(entry["reportPath"])

        self.assertEqual(by_status[ACTIVE_FORMAL], set())
        self.assertEqual(
            by_status[HISTORICAL_PROVENANCE_INCOMPLETE],
            HISTORICAL_REPORTS,
        )
        self.assertEqual(len(by_status[DIAGNOSTIC]), 6)

    def test_unregistered_report_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_reports(root)
            payload = _registry_payload(root)
            payload["reports"].pop()
            _write_registry(root, payload)

            result = validate_report_registry(root)

        self.assertFalse(result["ok"])
        self.assertGreater(result["summary"]["registryErrors"], 0)
        self.assertIn(
            "REPORT_REGISTRY_REPORT_UNREGISTERED",
            {issue["code"] for issue in result["issues"]},
        )

    def test_reclassifying_incomplete_history_as_active_fails_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_reports(root)
            payload = _registry_payload(root)
            historical = next(
                entry
                for entry in payload["reports"]
                if entry["status"] == HISTORICAL_PROVENANCE_INCOMPLETE
            )
            historical["status"] = ACTIVE_FORMAL
            _write_registry(root, payload)

            result = validate_report_registry(root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["summary"]["activeFormalReports"], 1)
        self.assertEqual(result["summary"]["activeErrors"], 1)
        self.assertEqual(result["summary"]["historicalErrors"], 2)

    def test_diagnostic_status_cannot_silently_hide_a_claim_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_reports(root)
            payload = _registry_payload(root)
            historical = next(
                entry
                for entry in payload["reports"]
                if entry["status"] == HISTORICAL_PROVENANCE_INCOMPLETE
            )
            historical["status"] = DIAGNOSTIC
            _write_registry(root, payload)

            result = validate_report_registry(root)

        self.assertFalse(result["ok"])
        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("REPORT_REGISTRY_ENTRY_INVALID", codes)
        self.assertIn("REPORT_REGISTRY_MANIFEST_UNREGISTERED", codes)

    def test_cli_returns_success_while_preserving_historical_error_counts(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "validate_report_registry.py"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["activeErrors"], 0)
        self.assertEqual(payload["summary"]["historicalErrors"], 3)

    def test_cli_missing_registry_fails_without_exposing_repository_path(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "validate_report_registry.py"),
                "--registry",
                "reports/does-not-exist.json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertNotIn(str(ROOT), completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_invalid_entry_paths_are_redacted(self) -> None:
        private_path = "C" + ":/" + "/".join(
            ("Users", "private-user", "secret-report.md")
        )
        token = "gh" + "p_" + ("R" * 36)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_reports(root)
            payload = _registry_payload(root)
            payload["reports"][0]["reportPath"] = private_path
            payload["reports"][1]["claimManifest"] = token
            _write_registry(root, payload)

            result = validate_report_registry(root)

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertFalse(result["ok"])
        self.assertNotIn(private_path, serialized)
        self.assertNotIn(token, serialized)
        self.assertIn("<redacted-registry-path>", serialized)


if __name__ == "__main__":
    unittest.main()
