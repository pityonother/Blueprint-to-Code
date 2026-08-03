from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_windows_portable import (  # noqa: E402
    PORTABLE_REQUIRED_FILES,
    build_portable_manifest,
    portable_asset_name,
    should_include_portable_path,
    validate_portable_entries,
    validate_required_portable_names,
    verify_python_runtime,
)


class WindowsPortablePackageTests(unittest.TestCase):
    def test_required_files_include_user_instructions_and_runtime_provenance(self):
        self.assertTrue(
            {
                "QUICK_START_zh.txt",
                "docs/USER_GUIDE_zh.md",
                "runtime/PYTHON_RUNTIME_SOURCE.txt",
                "runtime/python/LICENSE.txt",
                "runtime/python/python.exe",
                "dist/index.html",
            }.issubset(PORTABLE_REQUIRED_FILES)
        )

    def test_required_file_validator_rejects_a_missing_quick_start(self):
        names = {
            f"BlueprintToCode/{relative}"
            for relative in PORTABLE_REQUIRED_FILES
            if relative != "QUICK_START_zh.txt"
        }

        with self.assertRaisesRegex(ValueError, "QUICK_START_zh.txt"):
            validate_required_portable_names(names)

    def test_public_package_path_policy_is_data_minimal(self):
        included = (
            "START_HERE.bat",
            "DIAGNOSE.bat",
            "VERSION",
            "QUICK_START_zh.txt",
            "scripts/blueprint_tool_server.py",
            "scripts/blueprint_translator/evidence_query.py",
            "schemas/evidence_store_v3.schema.json",
            "ontology/ark_domain_ontology.v1.json",
            "devkit_plugins/BlueprintToCodeExporter/BlueprintToCodeExporter.uplugin",
            "src/main.ts",
            "public/favicon.svg",
            "docs/USER_GUIDE_zh.md",
            "docs/releases/v0.3.1.md",
            "runtime/PYTHON_RUNTIME_SOURCE.txt",
            "runtime/python/python.exe",
            "runtime/python/LICENSE.txt",
        )
        excluded = (
            "captures/ProjClusterGrenade/evidence/evidence.sqlite",
            "analysis/harvest_rankings/harvest_ranking_all_resources.full.json",
            "knowledge_base/current/core.sqlite",
            "native_evidence/evidence.sqlite",
            "runtime/downloads/python-3.13.13-embed-amd64.zip",
            "node_modules/vite/bin/vite.js",
            "release/old.zip",
            "devkit_content_root.txt",
            "devkit_path_mappings.txt",
            "tests/test_release_packaging.py",
            ".github/workflows/release-windows.yml",
            "reports/private-review.md",
            "START_GHIDRA.bat",
            "scripts/private.pem",
            "scripts/private.key",
            "scripts/.npmrc",
            "src/cache.sqlite",
            "public/installer.msi",
            "scripts/tool.whl",
        )

        self.assertTrue(all(should_include_portable_path(path) for path in included))
        self.assertTrue(
            all(not should_include_portable_path(path) for path in excluded)
        )

    def test_content_policy_rejects_any_user_path_and_binary_secret(self):
        entries = {
            "BlueprintToCode/scripts/local-note.txt": (
                "C:" + r"\Users\victim\Desktop\private.txt"
            ).encode(),
        }
        with self.assertRaisesRegex(ValueError, "absolute-path"):
            validate_portable_entries(ROOT, entries)

        binary_secret = b"\x00-----BEGIN " + b"PRIVATE KEY-----\x00"
        with self.assertRaisesRegex(ValueError, "secret-signature"):
            validate_portable_entries(
                ROOT,
                {"BlueprintToCode/scripts/opaque.bin.txt": binary_secret},
            )

    def test_bundled_runtime_matches_the_pinned_python_org_archive(self):
        result = verify_python_runtime(ROOT)

        self.assertEqual(result["version"], "3.13.13")
        self.assertEqual(result["architecture"], "x64")
        self.assertEqual(result["fileCount"], 34)
        self.assertEqual(
            result["sourceSha256"],
            "8766a8775746235e23cf5aee5027ab1060bb981d93110577adcf3508aa0cbd55",
        )
        self.assertRegex(str(result["inventorySha256"]), r"^[0-9a-f]{64}$")

    def test_manifest_declares_unzip_and_run_windows_contract(self):
        manifest = build_portable_manifest(
            repository_url="https://github.com/example/Blueprint-to-Code.git",
            commit="a" * 40,
            branch="main",
            generated_at_utc="2026-08-04T00:00:00+00:00",
            file_count=123,
            runtime={
                "version": "3.13.13",
                "architecture": "x64",
                "source": "https://www.python.org/example.zip",
                "sourceSha256": "b" * 64,
                "inventorySha256": "c" * 64,
                "fileCount": 34,
            },
            version="0.3.1",
        )

        self.assertEqual(manifest["packageType"], "windows-portable-user-release")
        self.assertEqual(manifest["platform"], "windows")
        self.assertEqual(manifest["architecture"], "x64")
        self.assertEqual(manifest["installation"], "unzip-and-run")
        self.assertTrue(manifest["userReady"])
        self.assertEqual(manifest["startup"], "START_HERE.bat")
        self.assertFalse(manifest["prerequisites"]["systemPython"])
        self.assertFalse(manifest["prerequisites"]["nodeJs"])
        self.assertTrue(manifest["prerequisites"]["arkDevKitForNewAssets"])
        self.assertEqual(
            manifest["excludedData"],
            ["analysis", "captures", "knowledge_base", "native_evidence"],
        )
        self.assertNotIn("C:" + r"\Users", str(manifest))

    def test_release_asset_name_is_stable_and_user_facing(self):
        self.assertEqual(
            portable_asset_name("0.3.1"),
            "BlueprintToCode-v0.3.1-windows-x64-portable.zip",
        )


if __name__ == "__main__":
    unittest.main()
