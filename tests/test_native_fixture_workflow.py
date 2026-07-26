from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "native-fixture.yml"
TOOLCHAIN = ROOT / "scripts" / "native_analysis" / "toolchain.json"
RUNNER = ROOT / "scripts" / "native_analysis" / "Run-NativeRecipe.ps1"
COMMON = ROOT / "scripts" / "native_analysis" / "NativeAnalysis.Common.ps1"
EXPORTER = (
    ROOT
    / "scripts"
    / "native_analysis"
    / "ghidra"
    / "ExportNativeRecipe.java"
)
FIXTURE_SOURCE = ROOT / "tests" / "native_fixture" / "fixture.cpp"
FIXTURE_RECIPE = (
    ROOT
    / "scripts"
    / "native_analysis"
    / "recipes"
    / "test-native-fixture.v1.json"
)


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

    def test_local_toolchain_critical_files_are_individually_pinned(self) -> None:
        toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
        self.assertEqual(
            toolchain["ghidra"]["installationFiles"],
            {
                "ghidraRun.bat": (
                    "9374c936fc8c2e4f59bd85760c7b32ca"
                    "5498cad6852672dbd68162823cdb1357"
                ),
                "support/analyzeHeadless.bat": (
                    "dd7b9d17d32ed70a71df82a43a21cdae"
                    "d6c4ce67064e30f8642c149f81c2ae07"
                ),
                "Ghidra/application.properties": (
                    "80890f309379ef60ecbb376a95448bd79"
                    "e874145544ffcfabb5ba1835ac8a2cf"
                ),
            },
        )
        self.assertEqual(
            toolchain["java"]["installationFiles"],
            {
                "bin/java.exe": (
                    "5e0fab9f07952ceb6e71eb9fd33e1ed6"
                    "9959904ca00cf70869b7baf516a98016"
                )
            },
        )

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

    def test_runner_fails_closed_on_hash_bypass_timeout_and_name_collisions(
        self,
    ) -> None:
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn(
            "if ($AllowHashMismatch -and -not $Experimental)",
            runner,
        )
        self.assertIn("NATIVE_HASH_BYPASS_REQUIRES_EXPERIMENTAL", runner)
        self.assertIn("REPORT: Analysis timed out", runner)
        self.assertIn("NATIVE_ANALYSIS_TIMEOUT", runner)
        self.assertIn('[Guid]::NewGuid().ToString("N")', runner)
        self.assertIn(
            "New-Item -ItemType Directory -Path $runRoot "
            "-ErrorAction Stop",
            runner,
        )
        self.assertNotIn(
            "New-Item -ItemType Directory -Force -Path $runRoot",
            runner,
        )
        self.assertIn("$runRootCreated = $false", runner)
        self.assertIn("$runRootCreated = $true", runner)
        self.assertIn("if ($runRootCreated)", runner)
        for artifact in (
            "$rawEvidencePath",
            "$evidencePath",
            "$logPath",
            "$scriptLogPath",
            "$storeDir",
        ):
            with self.subTest(artifact=artifact):
                self.assertRegex(
                    runner,
                    re.escape(artifact) + r"[\s\S]{0,350}\$runNonce",
                )

    def test_cleanup_rejects_a_regular_file_in_place_of_the_run_directory(
        self,
    ) -> None:
        common = COMMON.read_text(encoding="utf-8")

        self.assertRegex(
            common,
            r"Test-Path\s+-LiteralPath\s+\$resolvedRunRoot\s+"
            r"-PathType\s+Leaf",
        )
        self.assertIn("NATIVE_TEMP_PATH_INVALID", common)

    def test_exporter_filters_external_functions_and_uses_exact_queries(
        self,
    ) -> None:
        exporter = EXPORTER.read_text(encoding="utf-8")

        self.assertIn("isExportableFunction", exporter)
        self.assertIn("isMemoryAddress()", exporter)
        self.assertIn("isExternal()", exporter)
        self.assertIn("entryPoint.getAddressSpace().equals(", exporter)
        self.assertIn(
            "currentProgram.getImageBase().getAddressSpace()",
            exporter,
        )
        self.assertIn("expectedVftableName", exporter)
        self.assertIn(".equals(expectedVftableName)", exporter)
        self.assertIn("getDefaultOperandRepresentation", exporter)
        self.assertRegex(exporter, r"RSP\|RBP")
        self.assertIn("decompilerReferencesField", exporter)
        self.assertIn('result.add("exports"', exporter)
        self.assertIn("functionRva", exporter)
        self.assertIn("functionGapOrdinal", exporter)
        self.assertIn(
            '"native-gap://recipe/function/" + functionRva',
            exporter,
        )

    def test_public_fixture_exercises_an_imported_external_callee(self) -> None:
        source = FIXTURE_SOURCE.read_text(encoding="utf-8")
        recipe = json.loads(FIXTURE_RECIPE.read_text(encoding="utf-8"))
        quality_leaf = next(
            target
            for target in recipe["targets"]
            if target["id"] == "quality-leaf"
        )

        self.assertIn("#include <Windows.h>", source)
        self.assertIn("GetCurrentProcessId()", source)
        self.assertGreaterEqual(
            quality_leaf["exports"]["calleesDepth"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
