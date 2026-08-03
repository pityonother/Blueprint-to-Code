from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseV031DocumentationTests(unittest.TestCase):
    def test_release_notes_define_the_windows_portable_user_contract(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_path = ROOT / "docs" / "releases" / "v0.3.1.md"

        self.assertTrue(release_path.is_file())
        self.assertIn(
            "[v0.3.1 Release notes](docs/releases/v0.3.1.md)",
            readme,
        )
        release = release_path.read_text(encoding="utf-8")
        for marker in (
            "ENGINEERING_PREVIEW_SOURCE_AND_WINDOWS_PORTABLE",
            "BlueprintToCode-v0.3.1-windows-x64-portable.zip",
            "不要下载 `Source code (zip)`",
            "解压",
            "START_HERE.bat",
            "不需要安装 Python",
            "不需要安装 Node.js",
            "ARK DevKit",
            "127.0.0.1:8765",
            "SHA-256",
            "不包含 ARK 数据",
            "不包含 ARK/ShooterGame DLL/PDB",
            "Python runtime DLL/PYD",
            "不包含 Capture/Evidence DB",
            "mode=shadow",
            "defaultQuerySource=legacy",
            "cutoverEligible=false",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, release)

    def test_quick_start_is_written_for_zip_users(self):
        quick_start = (ROOT / "QUICK_START_zh.txt").read_text(encoding="utf-8")

        for marker in (
            "BlueprintToCode-v0.3.1-windows-x64-portable.zip",
            "完整解压",
            "双击 START_HERE.bat",
            "保持命令窗口开启",
            "DIAGNOSE.bat",
            "ARK DevKit",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, quick_start)

    def test_package_json_exposes_the_portable_build_command(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(
            package["scripts"]["package:windows"],
            r"runtime\python\python.exe scripts\package_windows_portable.py",
        )

    @unittest.skipUnless(os.name == "nt", "Windows npm command contract")
    def test_package_windows_npm_command_reaches_the_packager(self):
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        self.assertIsNotNone(npm)

        process = subprocess.run(
            [str(npm), "run", "package:windows", "--", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertIn("Build the public Blueprint to Code Windows x64 portable ZIP", process.stdout)

    def test_changelog_records_the_downloadable_portable_release(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertIn("## [0.3.1] - 2026-08-04", changelog)
        versioned = changelog.split("## [0.3.1] - 2026-08-04", maxsplit=1)[1]
        versioned = versioned.split("## [0.3.0]", maxsplit=1)[0]
        for marker in (
            "Windows x64 portable",
            "bundled Python",
            "SHA-256",
            "clean-extract",
            "Source code",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, versioned)


if __name__ == "__main__":
    unittest.main()
