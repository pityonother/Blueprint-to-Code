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


if __name__ == "__main__":
    unittest.main()
