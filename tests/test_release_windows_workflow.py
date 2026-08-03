from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-windows.yml"


class ReleaseWindowsWorkflowContractTests(unittest.TestCase):
    def workflow(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_read_only_bounded_and_runs_for_release_candidates(self) -> None:
        workflow = self.workflow()

        self.assertIn("runs-on: windows-latest", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("push:", workflow)
        self.assertRegex(workflow, r"(?m)^\s+timeout-minutes:\s+\d+\s*$")
        self.assertRegex(workflow, r"(?m)^permissions:\s*\n\s+contents:\s+read\s*$")
        self.assertNotRegex(workflow, r"(?m)^\s+\w[\w-]*:\s+write\s*$")
        self.assertIn("actions/checkout@v5", workflow)
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            workflow,
        )
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("actions/setup-python@v6", workflow)

    def test_workflow_covers_every_required_windows_release_gate(self) -> None:
        workflow = self.workflow()
        required = (
            "scripts/check_release_content.py --git-ref HEAD",
            "tests/test_release_source_archive.py",
            "tests/test_evidence_publication_v3.py",
            "test_prune_rename_failure_rolls_back_all_canonical_v2_artifacts",
            "test_publisher_rejects_a_windows_junction_asset_directory",
            "test_windows_source_junction_is_blocked",
            "tests/test_tool_server_security.py",
            "tests/test_harvest_http.py",
            "tests/test_blueprint_interpretation_cli.py",
            "tests/test_diagnose_blueprint_tool.py",
            "Remove-Item Env:BLUEPRINT_TO_CODE_ROOT",
            "Remove-Item Env:ARK_DEVKIT_ROOT",
            "git status --porcelain=v1 --untracked-files=all",
        )
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, workflow)

    def test_workflow_does_not_publish_or_require_proprietary_inputs(self) -> None:
        workflow = self.workflow()

        forbidden = (
            "actions/upload-artifact",
            "gh release",
            "git tag",
            "create-release",
            "deploy",
            "ShooterGameEditor-ShooterGame",
            "secrets.",
        )
        for fragment in forbidden:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, workflow)
        self.assertNotRegex(workflow, re.compile(r"(?i)DevKitRoot|auth-token|password"))


if __name__ == "__main__":
    unittest.main()
