from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_full_env import build_package_manifest, read_project_version  # noqa: E402


class VersionConsistencyTests(unittest.TestCase):
    def test_version_is_semver_and_matches_node_manifests(self):
        version = read_project_version(ROOT)
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))

        self.assertRegex(version, r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
        self.assertEqual(package["version"], version)
        self.assertEqual(lock["version"], version)
        self.assertEqual(lock["packages"][""]["version"], version)
        self.assertEqual(version, "0.3.1")

    def test_release_manifest_reads_the_same_version_source(self):
        version = read_project_version(ROOT)

        manifest = build_package_manifest(
            repository_url="https://github.com/example/Blueprint-to-Code.git",
            commit="a" * 40,
            branch="main",
            generated_at_utc="2026-07-27T00:00:00+00:00",
            file_count=1,
            sample_asset="Fixture",
        )

        self.assertEqual(manifest["version"], version)
        self.assertTrue(re.fullmatch(r"\d+\.\d+\.\d+", str(manifest["version"])))

    def test_control_center_state_reads_the_root_version(self):
        import blueprint_tool_server

        with (
            patch.object(blueprint_tool_server, "list_assets", return_value=[]),
            patch.object(
                blueprint_tool_server,
                "knowledge_base_summary",
                return_value={},
            ),
            patch.object(
                blueprint_tool_server,
                "read_devkit_request",
                return_value="",
            ),
        ):
            state = blueprint_tool_server.api_state()

        self.assertEqual(state["version"], read_project_version(ROOT))

    def test_both_frontend_workspaces_render_the_state_version_footer(self):
        source = (ROOT / "src" / "main.ts").read_text(encoding="utf-8")

        self.assertIn("version: string;", source)
        self.assertIn("appVersion = payload.version;", source)
        self.assertGreaterEqual(
            source.count("${renderVersionFooter()}"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
