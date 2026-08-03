from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class CiContractTests(unittest.TestCase):
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
            "git diff --name-only --diff-filter=ACMR -z",
            "Changed-file safety scan passed",
            "local_path_patterns",
            "known_secret_patterns",
            "generated_prefixes",
            '".sqlite"',
            '".uasset"',
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


if __name__ == "__main__":
    unittest.main()
