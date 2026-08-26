from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseV032DocumentationTests(unittest.TestCase):
    def test_release_notes_define_current_portable_and_feature_contracts(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_path = ROOT / "docs" / "releases" / "v0.3.2.md"

        self.assertTrue(release_path.is_file())
        self.assertTrue((ROOT / "docs" / "releases" / "v0.3.1.md").is_file())
        self.assertIn(
            "[v0.3.2 Release notes](docs/releases/v0.3.2.md)",
            readme,
        )
        release = release_path.read_text(encoding="utf-8")
        for marker in (
            "ENGINEERING_PREVIEW_SOURCE_AND_WINDOWS_PORTABLE",
            "BlueprintToCode-v0.3.2-windows-x64-portable.zip",
            "不要下载 `Source code (zip)`",
            "START_HERE.bat",
            "不需要安装 Python",
            "不需要安装 Node.js",
            "ARK DevKit",
            "92.35.288.826107",
            "/Script/ShooterGame.ShooterCharacter",
            "bIsCrouched",
            "CLASS_DEFAULT_OBJECT_ONLY",
            "runtimeStateAvailable=false",
            "requirement solver",
            "authoritative Evidence",
            "runtime loot",
            "exact pin link",
            "Editor Bridge",
            "source-contract preview",
            "mode=shadow",
            "defaultQuerySource=legacy",
            "cutoverEligible=false",
            "SHA-256",
            "不包含 ARK/ShooterGame DLL/PDB",
            "不包含 Capture/Evidence DB",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, release)

    def test_quick_start_is_written_for_v032_zip_users(self) -> None:
        quick_start = (ROOT / "QUICK_START_zh.txt").read_text(encoding="utf-8")

        for marker in (
            "BlueprintToCode-v0.3.2-windows-x64-portable.zip",
            "完整解压",
            "双击 START_HERE.bat",
            "保持命令窗口开启",
            "DIAGNOSE.bat",
            "ARK DevKit",
            "/Script/ShooterGame.ShooterCharacter",
            "类默认对象",
            "不是在线玩家实时值",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, quick_start)

    def test_package_json_exposes_the_portable_build_command(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(
            package["scripts"]["package:windows"],
            r"runtime\python\python.exe scripts\package_windows_portable.py",
        )

    @unittest.skipUnless(os.name == "nt", "Windows npm command contract")
    def test_package_windows_npm_command_reaches_the_packager(self) -> None:
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
        self.assertIn(
            "Build the public Blueprint to Code Windows x64 portable ZIP",
            process.stdout,
        )

    def test_changelog_records_the_v032_release_scope(self) -> None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertIn("## [0.3.2] - 2026-08-26", changelog)
        versioned = changelog.split("## [0.3.2] - 2026-08-26", maxsplit=1)[1]
        versioned = versioned.split("## [0.3.1]", maxsplit=1)[0]
        for marker in (
            "ShooterCharacter",
            "authoritative Evidence",
            "requirement solver",
            "runtime loot",
            "Editor Bridge",
            "Windows x64 portable",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, versioned)


if __name__ == "__main__":
    unittest.main()
