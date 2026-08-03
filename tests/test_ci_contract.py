from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class CiContractTests(unittest.TestCase):
    @staticmethod
    def _changed_file_scanner_source() -> str:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        start_marker = "          python - <<'PY'\n"
        end_marker = "\n          PY"
        start = workflow.index(start_marker) + len(start_marker)
        end = workflow.index(end_marker, start)
        return textwrap.dedent(workflow[start:end])

    @staticmethod
    def _write_files(root: Path, files: dict[str, str | bytes]) -> None:
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")

    def _run_changed_file_scanner(
        self,
        *,
        baseline: dict[str, str | bytes],
        changed: dict[str, str | bytes],
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "ci-contract@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "CI Contract"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "core.autocrlf", "false"],
                cwd=root,
                check=True,
            )
            self._write_files(root, baseline)
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "baseline"],
                cwd=root,
                check=True,
            )
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            self._write_files(root, changed)
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "changed"],
                cwd=root,
                check=True,
            )
            environment = os.environ.copy()
            environment["DIFF_BASE"] = base
            return subprocess.run(
                [sys.executable, "-c", self._changed_file_scanner_source()],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_ci_uses_current_official_actions_and_read_only_permissions(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("actions/checkout@v5", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertIn("actions/setup-node@v6", workflow)
        self.assertRegex(workflow, r"(?m)^permissions:\s*\n\s+contents:\s+read\s*$")
        self.assertNotRegex(workflow, r"(?m)^\s+\w[\w-]*:\s+write\s*$")

    def test_ci_runs_python_frontend_claim_and_release_gates(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        required_commands = (
            "python -m pip install pytest==9.0.3",
            "python -m pytest -q",
            "npm ci",
            "npm run build",
            "node tests/api_frontend_contract.mjs",
            "node tests/blueprint_frontend_contract.mjs",
            "node tests/frontend_core_contract.mjs",
            "node tests/harvest_frontend_contract.mjs",
            "python scripts/validate_report_claims.py",
            "python tests/test_release_readiness.py",
            "python tests/test_release_packaging.py",
            "python tests/test_version_consistency.py",
            "python tests/test_documentation_consistency.py",
        )
        for command in required_commands:
            with self.subTest(command=command):
                self.assertIn(command, workflow)
        self.assertGreaterEqual(
            workflow.count("scripts/validate_report_claims.py"),
            2,
        )
        self.assertIn("--formal", workflow)
        self.assertIn("git diff --check", workflow)
        self.assertNotIn("tests.test_release_readiness", workflow)

    def test_ci_does_not_embed_a_native_fixture_runner_or_secret(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertNotIn("native-fixture.yml", workflow)
        self.assertNotRegex(
            workflow,
            re.compile(r"(?i)(token|password|secret)\s*:\s*['\"][^$]"),
        )

    def test_ci_runs_harvest_closeout_and_changed_content_gates(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        required_fragments = (
            "ruff==0.15.20",
            "python -m ruff check --",
            "python -m pytest -q tests/test_*harvest*.py",
            "node tests/knowledge_frontend_contract.mjs",
            "npm audit --audit-level=high",
            "git diff --name-only --diff-filter=ACMRT -z",
            "Changed-file safety scan passed",
            "local_path_patterns",
            "known_secret_patterns",
            "generated_prefixes",
            '".sqlite"',
            '".uasset"',
            "changed symbolic link is forbidden",
            "changed binary file is forbidden",
            "binary diff is forbidden",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, workflow)
        self.assertRegex(
            workflow,
            r"pull_request:\s*\n\s+branches:\s*\n\s+- main",
        )

    def test_linux_wasm_peer_is_explicit_in_the_lock_contract(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads(
            (ROOT / "package-lock.json").read_text(encoding="utf-8")
        )

        expected = package["devDependencies"]["@emnapi/runtime"]
        runtime = lock["packages"]["node_modules/@emnapi/runtime"]

        self.assertEqual(expected, "1.11.3")
        self.assertEqual(runtime["version"], expected)
        self.assertTrue(runtime["dev"])

    def test_changed_file_scanner_accepts_safe_text(self):
        result = self._run_changed_file_scanner(
            baseline={"notes.txt": "baseline\n"},
            changed={"notes.txt": "baseline\nsafe release note\n"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Changed-file safety scan passed", result.stdout)

    def test_changed_file_scanner_rejects_namespaced_unquoted_secrets(self):
        assignments = (
            "AWS" + "_SECRET_ACCESS_KEY=live-value-123456",
            "OPENAI" + "_API_KEY=live-value-123456",
            "GITHUB" + "_ACCESS_TOKEN=live-value-123456",
        )
        for assignment in assignments:
            with self.subTest(name=assignment.partition("=")[0]):
                result = self._run_changed_file_scanner(
                    baseline={"settings.env": "SAFE_SETTING=enabled\n"},
                    changed={"settings.env": assignment + "\n"},
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("hard-coded secret assignment", result.stderr)

    def test_changed_file_scanner_rejects_new_duplicate_findings(self):
        windows_path = "C:" + "\\" + "Users\\alice\\project\\file.txt"
        assignment = "SERVICE" + "_PASSWORD=live-value-123456"
        token = "gh" + "o_" + ("A" * 24)
        cases = (
            ("path.txt", windows_path, "local absolute path content"),
            ("settings.env", assignment, "hard-coded secret assignment"),
            ("token.txt", token, "known secret signature"),
        )
        for filename, finding, expected in cases:
            with self.subTest(filename=filename):
                result = self._run_changed_file_scanner(
                    baseline={filename: finding + "\n"},
                    changed={filename: finding + "\n" + finding + "\n"},
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_changed_file_scanner_rejects_binary_content_and_attributes(self):
        nul_result = self._run_changed_file_scanner(
            baseline={"payload.txt": "safe\n"},
            changed={"payload.txt": b"safe\0binary\n"},
        )
        self.assertNotEqual(nul_result.returncode, 0)
        self.assertIn("changed binary file is forbidden", nul_result.stderr)

        token = "gh" + "o_" + ("B" * 24)
        attributed_result = self._run_changed_file_scanner(
            baseline={"payload.safe": "safe\n"},
            changed={
                ".gitattributes": "*.safe binary\n",
                "payload.safe": token + "\n",
            },
        )
        self.assertNotEqual(attributed_result.returncode, 0)
        self.assertIn("binary diff is forbidden", attributed_result.stderr)


if __name__ == "__main__":
    unittest.main()
