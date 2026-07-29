from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ATTESTATION_PATH = (
    PROJECT_ROOT
    / "docs"
    / "ark_kb_vnext"
    / "STAGE10_SHADOW_BASELINE_ATTESTATION.json"
)
SNAPSHOT_ROOT = PROJECT_ROOT / "knowledge_base" / "vnext"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _attestation() -> dict[str, object]:
    return json.loads(ATTESTATION_PATH.read_text(encoding="utf-8"))


class KnowledgeBaselineAttestationTests(unittest.TestCase):
    def test_attestation_is_path_safe_and_fail_closed(self) -> None:
        text = ATTESTATION_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"[A-Za-z]:[\\/]")
        payload = json.loads(text)
        snapshot = payload["snapshot"]
        self.assertEqual(snapshot["qualityGates"]["passed"], 58)
        self.assertEqual(snapshot["qualityGates"]["failed"], 17)
        self.assertFalse(snapshot["qualityGates"]["cutoverEligible"])
        self.assertEqual(snapshot["cutover"]["mode"], "shadow")
        self.assertEqual(
            snapshot["cutover"]["defaultQuerySource"],
            "legacy",
        )
        self.assertFalse(payload["status"]["readyForDefault"])

    def test_attestation_hashes_and_build_identity_are_well_formed(self) -> None:
        payload = _attestation()
        snapshot = payload["snapshot"]
        repository = payload["repository"]
        for field in (
            "sourceSha256",
            "discoverySha256",
            "currentPointerSha256",
            "manifestSha256",
            "qualityReportSha256",
            "benchmarkReportSha256",
        ):
            self.assertRegex(snapshot[field], SHA256)
        self.assertRegex(repository["commitSha"], GIT_SHA)
        self.assertEqual(
            snapshot["buildId"].rsplit("-", 1)[1],
            snapshot["sourceSha256"][:12],
        )
        self.assertEqual(
            repository["commitSha"],
            repository["remoteHeadSha"],
        )

    def test_attestation_matches_frozen_snapshot_when_available(self) -> None:
        payload = _attestation()
        snapshot = payload["snapshot"]
        manifest_path = (
            SNAPSHOT_ROOT
            / "snapshots"
            / snapshot["buildId"]
            / "manifest.json"
        )
        if not manifest_path.is_file():
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["buildId"], snapshot["buildId"])
        self.assertEqual(
            manifest["source"]["sha256"],
            snapshot["sourceSha256"],
        )
        self.assertEqual(
            manifest["source"]["inputs"]["discovery"],
            snapshot["discoverySha256"],
        )
        self.assertEqual(
            manifest["qualityGates"]["cutoverEligible"],
            snapshot["qualityGates"]["cutoverEligible"],
        )
        self.assertEqual(manifest["cutover"], snapshot["cutover"])

    def test_rebuild_and_update_blockers_do_not_claim_publication(self) -> None:
        validation = _attestation()["validation"]
        for name in ("fullRebuildAttempt", "unchangedUpdate"):
            with self.subTest(name=name):
                self.assertTrue(
                    validation[name]["result"].startswith("BLOCKED_BY_")
                )
                self.assertFalse(validation[name]["published"])
                self.assertFalse(validation[name]["currentPointerChanged"])


if __name__ == "__main__":
    unittest.main()
