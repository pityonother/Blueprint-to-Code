from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOC_ROOT = PROJECT_ROOT / "docs" / "ark_kb_vnext"
SNAPSHOT_ROOT = PROJECT_ROOT / "knowledge_base" / "vnext"

REPORT_PATHS = (
    DOC_ROOT / "COMPLETION_REPORT.md",
    DOC_ROOT / "COVERAGE_AND_CUTOVER.md",
)
SHA256_PATTERN = re.compile(
    r"(?:Source|Discovery) SHA-256\s*(?:：|\|)\s*`([0-9a-f]+)`"
)
BUILD_ID_PATTERN = re.compile(
    r"`(\d{8}T\d{6}(?:\+0000)?)-([0-9a-f]{12})`"
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


def _current_manifest() -> dict[str, object] | None:
    pointer_path = SNAPSHOT_ROOT / "current.json"
    if pointer_path.is_file():
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        build_id = str(pointer["buildId"])
        expected_relative = f"snapshots/{build_id}"
        if pointer.get("snapshotRelativePath") != expected_relative:
            raise AssertionError(
                "current pointer does not use snapshots/<buildId>"
            )
        manifest_path = (
            SNAPSHOT_ROOT / "snapshots" / build_id / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("buildId") != build_id:
            raise AssertionError(
                "current pointer and immutable manifest build IDs differ"
            )
        return manifest

    legacy_path = SNAPSHOT_ROOT / "manifests" / "current.json"
    if legacy_path.is_file():
        return json.loads(legacy_path.read_text(encoding="utf-8"))
    return None


class KnowledgeDocumentIdentityTests(unittest.TestCase):
    def test_report_sha256_values_are_exactly_64_lowercase_hex_digits(self):
        for path in REPORT_PATHS:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                values = SHA256_PATTERN.findall(text)
                self.assertTrue(values)
                for sha256 in values:
                    self.assertRegex(sha256, r"^[0-9a-f]{64}$")

    def test_report_build_ids_use_the_documented_source_hash_prefix(self):
        for path in REPORT_PATHS:
            with self.subTest(path=path.name):
                build_id, sha256 = _report_identity(path)
                self.assertEqual(build_id.rsplit("-", 1)[1], sha256[:12])

    def test_reports_match_each_other_and_current_manifest_when_available(self):
        identities = [_report_identity(path) for path in REPORT_PATHS]
        self.assertEqual(len(set(identities)), 1)

        manifest = _current_manifest()
        if manifest is None:
            return

        self.assertEqual(identities[0][0], manifest["buildId"])
        source = manifest.get("source")
        self.assertIsInstance(source, dict)
        self.assertEqual(identities[0][1], source["sha256"])

    def test_reports_preserve_fail_closed_gold_and_cutover_state(self):
        for path in REPORT_PATHS:
            with self.subTest(path=path.name):
                normalized = re.sub(
                    r"\s+",
                    "",
                    path.read_text(encoding="utf-8"),
                )
                self.assertIn("5/130", normalized)
                self.assertIn("0/100", normalized)
                self.assertIn("0/300", normalized)
                self.assertIn("shadow", normalized)
                self.assertIn("legacy", normalized)

    def test_architecture_documents_the_immutable_pointer_contract(self):
        text = (DOC_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("current.json", text)
        self.assertIn("snapshots/<buildId>", text)
        self.assertIn("sealedInSnapshotManifest=true", text)
        self.assertIn("不能修改 snapshot manifest", text)


if __name__ == "__main__":
    unittest.main()
