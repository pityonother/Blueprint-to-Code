from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_test_windows_portable import (  # noqa: E402
    is_built_homepage,
    verify_zip_integrity,
)


WORKFLOW = ROOT / ".github" / "workflows" / "windows-portable.yml"


def _write_fixture_archive(path: Path, *, tamper_version: bool = False) -> None:
    version = b"0.3.1\n"
    manifest = (
        json.dumps(
            {
                "schema": "blueprint-to-code.windows-portable-package.v1",
                "version": "0.3.1",
                "packageType": "windows-portable-user-release",
                "platform": "windows",
                "architecture": "x64",
                "fileCount": 3,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    hashes = {
        "PACKAGE_MANIFEST.json": hashlib.sha256(manifest).hexdigest(),
        "VERSION": hashlib.sha256(version).hexdigest(),
    }
    sums = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(hashes.items())
    ).encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BlueprintToCode/PACKAGE_MANIFEST.json", manifest)
        archive.writestr("BlueprintToCode/SHA256SUMS.txt", sums)
        archive.writestr(
            "BlueprintToCode/VERSION",
            b"0.3.2\n" if tamper_version else version,
        )


class WindowsPortableWorkflowTests(unittest.TestCase):
    def test_built_homepage_accepts_html_bytes(self):
        self.assertTrue(is_built_homepage(b"<!doctype html><html><body></body></html>"))
        self.assertFalse(is_built_homepage(b'{"ok":true}'))

    def test_zip_integrity_verifier_accepts_bound_manifest_and_checksums(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "portable.zip"
            _write_fixture_archive(archive_path)

            result = verify_zip_integrity(archive_path)

        self.assertEqual(result["version"], "0.3.1")
        self.assertEqual(result["entryCount"], 3)
        self.assertRegex(str(result["sha256"]), r"^[0-9a-f]{64}$")

    def test_zip_integrity_verifier_rejects_tampered_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "portable.zip"
            _write_fixture_archive(archive_path, tamper_version=True)

            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_zip_integrity(archive_path)

    def test_workflow_builds_smokes_then_uploads_without_publishing(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        for marker in (
            "runs-on: windows-latest",
            "permissions:\n  contents: read",
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            "fetch-depth: 0",
            "npm ci",
            "npm run package:windows",
            "scripts/smoke_test_windows_portable.py",
            "BlueprintToCode-v0.3.1-windows-x64-portable.zip",
            "BlueprintToCode-v0.3.1-windows-x64-portable.zip.sha256",
            "actions/upload-artifact@v4",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, workflow)
        self.assertLess(
            workflow.index("scripts/smoke_test_windows_portable.py"),
            workflow.index("actions/upload-artifact@v4"),
        )
        for forbidden in ("gh release", "git tag", "contents: write", "secrets."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
