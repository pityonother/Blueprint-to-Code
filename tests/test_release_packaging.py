import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class ReleasePackagingTests(unittest.TestCase):
    def test_archive_paths_reject_escape_absolute_and_drive_paths(self):
        from package_full_env import is_safe_archive_path

        self.assertTrue(is_safe_archive_path("BlueprintToCode/scripts/tool.py"))
        for unsafe in (
            "../secret.txt",
            "BlueprintToCode/../../secret.txt",
            "/absolute/file.txt",
            r"C:\Users\someone\secret.txt",
        ):
            with self.subTest(path=unsafe):
                self.assertFalse(is_safe_archive_path(unsafe))

    def test_release_allowlist_excludes_local_generated_and_internal_files(self):
        from package_full_env import should_include_tracked

        included = (
            ".gitignore",
            "README.md",
            "scripts/query_blueprint_evidence.py",
            "runtime/python/python.exe",
            "docs/BLUEPRINT_EVIDENCE_STORE_V2_SPEC_zh.md",
        )
        excluded = (
            "release/old.zip",
            "runtime/downloads/python.zip",
            "analysis/harvest_rankings/report.json",
            "captures/Asset/evidence/evidence.sqlite",
            "devkit_content_root.txt",
            "devkit_path_mappings.txt",
            "docs/GPT_PRO_PROJECT_REPORT_zh.md",
            "docs/SESSION_HANDOFF_zh.md",
            "docs/NEXT_CHAT_HANDOFF_zh.md",
        )
        self.assertTrue(all(should_include_tracked(path) for path in included))
        self.assertTrue(all(not should_include_tracked(path) for path in excluded))

    def test_dotfile_name_is_preserved_when_packaged(self):
        from package_full_env import _normalized_relative

        self.assertEqual(_normalized_relative(".gitignore"), ".gitignore")
        self.assertEqual(_normalized_relative("./README.md"), "README.md")

    def test_windows_npm_command_is_resolved_explicitly(self):
        from package_full_env import resolve_npm_executable

        with patch("package_full_env.shutil.which", side_effect=lambda name: "C:/node/npm.cmd" if name == "npm.cmd" else None):
            self.assertEqual(resolve_npm_executable(), "C:/node/npm.cmd")

    def test_repository_url_strips_http_userinfo(self):
        from package_full_env import sanitize_repository_url

        self.assertEqual(
            sanitize_repository_url("https://secret-token@github.com/example/project.git"),
            "https://github.com/example/project.git",
        )
        self.assertEqual(
            sanitize_repository_url("git@github.com:example/project.git"),
            "git@github.com:example/project.git",
        )

    def test_harvest_report_discovery_requires_complete_nonempty_triplets(self):
        from package_full_env import discover_harvest_reports

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "harvest_rankings"
            root.mkdir()
            for suffix in (".ai.json", ".full.json", ".md"):
                (root / f"harvest_ranking_metal{suffix}").write_text("{}", encoding="utf-8")
            reports = discover_harvest_reports(root)
            self.assertEqual([item[0] for item in reports], ["harvest_ranking_metal"])

            (root / "harvest_ranking_metal.md").unlink()
            with self.assertRaises(FileNotFoundError):
                discover_harvest_reports(root)

    def test_package_manifest_records_commit_without_local_source_path(self):
        from package_full_env import build_package_manifest

        manifest = build_package_manifest(
            repository_url="https://github.com/example/Blueprint-to-Code.git",
            commit="a" * 40,
            branch="main",
            generated_at_utc="2026-07-20T00:00:00+00:00",
            file_count=123,
            sample_asset="Buff_StriderHackingParent",
        )

        self.assertFalse(manifest["dirty"])
        self.assertEqual(manifest["commit"], "a" * 40)
        self.assertNotIn("source", manifest)
        self.assertNotIn("C:\\Users", str(manifest))


if __name__ == "__main__":
    unittest.main()
