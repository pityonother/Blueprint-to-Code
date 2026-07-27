from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOC_ROOT = PROJECT_ROOT / "docs" / "ark_kb_vnext"
CURRENT_MANIFEST = (
    PROJECT_ROOT
    / "knowledge_base"
    / "vnext"
    / "manifests"
    / "current.json"
)

REPORT_PATHS = (
    DOC_ROOT / "COMPLETION_REPORT.md",
    DOC_ROOT / "COVERAGE_AND_CUTOVER.md",
)
SHA256_PATTERN = re.compile(
    r"(?:Source|Discovery) SHA-256\s*(?:：|\|)\s*`([0-9a-f]+)`"
)
BUILD_ID_PATTERN = re.compile(
    r"`(\d{8}T\d{6}\+0000)-([0-9a-f]{12})`"
)


def _report_identity(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    sha_match = SHA256_PATTERN.search(text)
    build_match = BUILD_ID_PATTERN.search(text)
    if sha_match is None:
        raise AssertionError(f"{path.name} has no labelled SHA-256")
    if build_match is None:
        raise AssertionError(f"{path.name} has no build ID")
    return build_match.group(0).strip("`"), sha_match.group(1)


class KnowledgeDocumentIdentityTests(unittest.TestCase):
    def test_report_sha256_values_are_exactly_64_lowercase_hex_digits(self):
        for path in REPORT_PATHS:
            with self.subTest(path=path.name):
                _, sha256 = _report_identity(path)
                self.assertRegex(sha256, r"^[0-9a-f]{64}$")

    def test_report_build_ids_use_the_documented_source_hash_prefix(self):
        for path in REPORT_PATHS:
            with self.subTest(path=path.name):
                build_id, sha256 = _report_identity(path)
                self.assertEqual(build_id.rsplit("-", 1)[1], sha256[:12])

    def test_reports_match_each_other_and_current_manifest_when_available(self):
        identities = [_report_identity(path) for path in REPORT_PATHS]
        self.assertEqual(len(set(identities)), 1)

        if not CURRENT_MANIFEST.is_file():
            return

        manifest = json.loads(CURRENT_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(identities[0][0], manifest["buildId"])
        self.assertEqual(identities[0][1], manifest["source"]["sha256"])


if __name__ == "__main__":
    unittest.main()
