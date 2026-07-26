from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
