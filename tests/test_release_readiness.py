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


if __name__ == "__main__":
    unittest.main()
