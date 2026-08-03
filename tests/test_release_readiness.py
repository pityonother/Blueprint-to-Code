import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseReadinessTests(unittest.TestCase):
    def test_devkit_content_root_template_is_readable_by_start_here_first_line(self):
        template = ROOT / "devkit_content_root.example.txt"
        lines = template.read_text(encoding="utf-8-sig").splitlines()

        self.assertGreaterEqual(len(lines), 1)
        self.assertEqual(
            lines[0],
            r"C:\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content",
        )

    def test_local_configuration_and_generated_analysis_are_git_ignored(self):
        candidates = (
            "devkit_content_root.txt",
            "devkit_path_mappings.txt",
            ".playwright-cli/session.json",
            "analysis/generated.json",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir)
            shutil.copyfile(ROOT / ".gitignore", repository / ".gitignore")
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=repository,
                capture_output=True,
                check=True,
            )
            for candidate in candidates:
                with self.subTest(candidate=candidate):
                    process = subprocess.run(
                        ["git", "check-ignore", "-q", "--", candidate],
                        cwd=repository,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))

    def test_start_here_does_not_expand_content_root_in_cmd_output(self):
        launcher = (ROOT / "START_HERE.bat").read_text(encoding="utf-8")

        self.assertIn(
            "if defined BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT echo DevKit Content root configured.",
            launcher,
        )
        self.assertNotIn(
            "echo DevKit Content root: %BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT%",
            launcher,
        )

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher contract")
    def test_start_here_passes_its_root_to_powershell_without_code_interpolation(self):
        launcher = (ROOT / "START_HERE.bat").read_text(encoding="utf-8")

        self.assertIn(
            'set "BLUEPRINT_TO_CODE_LAUNCH_ROOT=%~dp0"',
            launcher,
        )
        self.assertIn(
            "Resolve-Path -LiteralPath $env:BLUEPRINT_TO_CODE_LAUNCH_ROOT",
            launcher,
        )
        self.assertNotIn("Resolve-Path '%~dp0'", launcher)

        with tempfile.TemporaryDirectory() as temp_dir:
            special_root = Path(temp_dir) / "O'Hare & portable"
            special_root.mkdir()
            environment = dict(os.environ)
            environment["BLUEPRINT_TO_CODE_LAUNCH_ROOT"] = str(special_root)
            process = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "$root=(Resolve-Path -LiteralPath "
                        "$env:BLUEPRINT_TO_CODE_LAUNCH_ROOT).Path; "
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                        "Write-Output $root"
                    ),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                check=False,
            )

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), str(special_root.resolve()))

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher contract")
    def test_start_here_matches_only_its_exact_server_script_path(self):
        launcher = (ROOT / "START_HERE.bat").read_text(encoding="utf-8")

        self.assertNotIn("-like ('*' + $root + '*')", launcher)
        self.assertIn(
            "$_.CommandLine.IndexOf($serverScript, "
            "[StringComparison]::OrdinalIgnoreCase) -ge 0",
            launcher,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            special_root = Path(temp_dir) / "Blueprint[1]"
            wildcard_sibling_root = Path(temp_dir) / "Blueprint1"
            prefix_sibling_root = Path(temp_dir) / "Blueprint[1]-old"
            special_root.mkdir()
            wildcard_sibling_root.mkdir()
            prefix_sibling_root.mkdir()
            environment = dict(os.environ)
            environment["BLUEPRINT_TO_CODE_LAUNCH_ROOT"] = str(special_root)
            environment["BLUEPRINT_TO_CODE_OWN_COMMAND"] = str(
                special_root / "scripts" / "blueprint_tool_server.py"
            )
            environment["BLUEPRINT_TO_CODE_WILDCARD_SIBLING_COMMAND"] = str(
                wildcard_sibling_root / "scripts" / "blueprint_tool_server.py"
            )
            environment["BLUEPRINT_TO_CODE_PREFIX_SIBLING_COMMAND"] = str(
                prefix_sibling_root / "scripts" / "blueprint_tool_server.py"
            )
            process = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "$root=(Resolve-Path -LiteralPath "
                        "$env:BLUEPRINT_TO_CODE_LAUNCH_ROOT).Path; "
                        "$serverScript=Join-Path $root "
                        "'scripts\\blueprint_tool_server.py'; "
                        "$comparison=[StringComparison]::OrdinalIgnoreCase; "
                        "$own=$env:BLUEPRINT_TO_CODE_OWN_COMMAND; "
                        "$wildcardSibling="
                        "$env:BLUEPRINT_TO_CODE_WILDCARD_SIBLING_COMMAND; "
                        "$prefixSibling="
                        "$env:BLUEPRINT_TO_CODE_PREFIX_SIBLING_COMMAND; "
                        "Write-Output ($own.IndexOf($serverScript, $comparison) -ge 0); "
                        "Write-Output ($wildcardSibling.IndexOf($serverScript, "
                        "$comparison) -ge 0); "
                        "Write-Output ($prefixSibling.IndexOf($serverScript, "
                        "$comparison) -ge 0)"
                    ),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                check=False,
            )

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.splitlines(), ["True", "False", "False"])

    def test_start_here_has_a_noninteractive_browser_suppression_for_smoke_tests(self):
        launcher = (ROOT / "START_HERE.bat").read_text(encoding="utf-8")

        self.assertIn("if defined BLUEPRINT_TO_CODE_NO_OPEN", launcher)
        self.assertIn(
            '"%PYTHON_EXE%" "%~dp0scripts\\blueprint_tool_server.py" --port 8765',
            launcher,
        )
        self.assertIn(
            '"%PYTHON_EXE%" "%~dp0scripts\\blueprint_tool_server.py" --port 8765 --open',
            launcher,
        )


if __name__ == "__main__":
    unittest.main()
