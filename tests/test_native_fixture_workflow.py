from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "native-fixture.yml"
TOOLCHAIN = ROOT / "scripts" / "native_analysis" / "toolchain.json"


class NativeFixtureWorkflowContractTests(unittest.TestCase):
    def workflow(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_windows_nightly_and_native_path_triggers_are_bounded(self) -> None:
        workflow = self.workflow()

        self.assertIn("runs-on: windows-latest", workflow)
        self.assertRegex(workflow, r"(?m)^\s+timeout-minutes:\s+\d+\s*$")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("cron:", workflow)
        self.assertIn("push:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn('"scripts/native_analysis/**"', workflow)
        self.assertIn('"tests/native_fixture/**"', workflow)
        self.assertIn('".github/workflows/native-fixture.yml"', workflow)

    def test_actions_permissions_and_downloads_are_pinned_fail_closed(self) -> None:
        workflow = self.workflow()
        toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))

        self.assertIn("actions/checkout@v5", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertRegex(
            workflow,
            r"(?m)^permissions:\s*\n\s+contents:\s+read\s*$",
        )
        self.assertNotRegex(workflow, r"(?m)^\s+\w[\w-]*:\s+write\s*$")
        for expression in (
            "$toolchain.ghidra.releaseUrl",
            "$toolchain.ghidra.sha256",
            "$toolchain.java.releaseUrl",
            "$toolchain.java.sha256",
        ):
            with self.subTest(expression=expression):
                self.assertIn(expression, workflow)
        self.assertGreaterEqual(workflow.count("Invoke-WebRequest"), 2)
        self.assertGreaterEqual(workflow.count("Get-FileHash"), 3)
        self.assertIn("ToLowerInvariant()", workflow)
        self.assertRegex(
            workflow,
            r"(?s)if\s*\([^)]+-ne[^)]+\)\s*\{\s*throw",
        )
        for section in ("ghidra", "java"):
            with self.subTest(pin=section):
                self.assertTrue(
                    toolchain[section]["releaseUrl"].startswith("https://")
                )
                self.assertRegex(
                    toolchain[section]["sha256"],
                    r"^[0-9a-f]{64}$",
                )
                self.assertNotIn(toolchain[section]["releaseUrl"], workflow)
                self.assertNotIn(toolchain[section]["sha256"], workflow)

    def test_public_fixture_runs_formally_with_isolated_mutable_outputs(self) -> None:
        workflow = self.workflow()

        required = (
            "$env:RUNNER_TEMP",
            "tests/native_fixture/build.ps1",
            "scripts/native_analysis/Run-NativeRecipe.ps1",
            "-Recipe",
            "scripts/native_analysis/recipes/test-native-fixture.v1.json",
            "-DllPath",
            "blueprint_native_fixture.dll",
            "-PdbPath",
            "blueprint_native_fixture.pdb",
            "-ToolsRoot",
            "-WorkspaceRoot",
            "-EvidenceDir",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, workflow)
        self.assertNotIn("AllowHashMismatch", workflow)
        self.assertNotIn("Experimental", workflow)

    def test_post_run_gate_checks_semantics_before_bounded_queries(self) -> None:
        workflow = self.workflow()

        required = (
            "evidence.full.json",
            "evidence.manifest.json",
            "recipeTargets",
            "resolvedEvidenceIds",
            "compute-quality-int",
            "compute-quality-double",
            "native_recipe_targets",
            "native_call_edges",
            "native_field_accesses",
            "native_constants",
            "native_branches",
            "native_vtable_slots",
            "expectedConstants = @(42, 100, 1.25)",
            "provenance.pdb.loaded",
            "provenance.pdb.matchesBinary",
            "provenance.binary.sha256",
            "scripts/validate_native_evidence.py",
            "scripts/query_native_evidence.py",
            "overview",
            "search",
            "ComputeQuality",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, workflow)
        self.assertRegex(
            workflow,
            r"(?m)^\s+if\s*\(\s*@\(\$evidence\.recipeTargets\)\.Count\s+-ne\s+8\s*\)",
        )
        self.assertIn("$intId -eq $doubleId", workflow)
        self.assertRegex(
            workflow,
            r"provenance\.pdb\.loaded\s+-ne\s+\$true",
        )
        self.assertRegex(
            workflow,
            r"provenance\.pdb\.matchesBinary\s+-ne\s+\$true",
        )
        self.assertRegex(
            workflow,
            r"provenance\.binary\.sha256\s+-ne\s+\$dllHash",
        )
        self.assertIn("$manifest.counts.$name -le 0", workflow)
        self.assertIn('"VERIFIED"', workflow)

    def test_related_unit_and_claim_fixture_gates_are_present(self) -> None:
        workflow = self.workflow()

        for command in (
            "python tests/test_native_recipe.py",
            "python tests/test_native_identity.py",
            "python tests/test_native_evidence_store.py",
            "python scripts/validate_report_claims.py",
            "tests/fixtures/report_claims",
            "--formal",
        ):
            with self.subTest(command=command):
                self.assertIn(command, workflow)

    def test_workflow_contains_no_proprietary_inputs_or_secrets(self) -> None:
        workflow = self.workflow()

        self.assertNotRegex(
            workflow,
            re.compile(
                r"(?i)(ShooterGameEditor-ShooterGame|ARKDevkit|"
                r"DevKitRoot|secrets\.|auth-token|password)"
            ),
        )
        self.assertNotRegex(workflow, r"https?://")


if __name__ == "__main__":
    unittest.main()
