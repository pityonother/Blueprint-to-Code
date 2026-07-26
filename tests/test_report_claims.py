from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_report_claims import validate_claim_manifests  # noqa: E402


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
NATIVE_ID = f"native://{SHA_A}/fixture.dll/0x1000"
EVIDENCE_SET_ID = f"native-set://{SHA_A}/{SHA_C}"
BP_ID = "bp://111111111111111111111111@222222222222222222222222/g/1/n/10"
RUNTIME_ID = "runtime://fixture/quality-v1"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _valid_tree(root: Path, *, trust_status: str = "VERIFIED") -> Path:
    report_path = root / "reports" / "fixture.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Fixture report\n\nQuality result is bounded.\n", encoding="utf-8")

    evidence_manifest = {
        "schema": "blueprint-to-code-sanitized-native-evidence/v1",
        "evidenceSetId": EVIDENCE_SET_ID,
        "localEvidenceRelativePath": f"native_evidence/{SHA_A}/fixture",
        "trust": {"status": trust_status},
        "provenance": {
            "binary": {"module": "fixture.dll", "sha256": SHA_A},
            "pdb": {
                "sha256": SHA_B,
                "guid": "12345678-1234-5678-90ab-cdef12345678",
                "age": 1,
                "loaded": True,
                "matchesBinary": True,
            },
            "generator": {
                "repositoryDirty": False,
                "recipeId": "test-native-fixture/v1",
                "recipeSha256": SHA_C,
                "scriptSha256": {"runner": SHA_D},
            },
        },
        "targets": [
            {
                "evidenceId": NATIVE_ID,
                "qualifiedName": "Fixture::ComputeQuality",
                "rva": "0x1000",
            }
        ],
        "gaps": [],
    }
    evidence_path = root / "reports" / "evidence_manifests" / "fixture.native.json"
    _write_json(evidence_path, evidence_manifest)

    claims = {
        "schema": "blueprint-to-code-report-claims/v1",
        "reportId": "fixture-report",
        "reportPath": "reports/fixture.md",
        "generatedAtUtc": "2026-07-27T00:00:00Z",
        "generator": {"repositoryCommit": "fixture", "dirty": False},
        "dependencies": {
            "blueprintAssets": [],
            "nativeEvidenceSets": [
                {
                    "manifestPath": "reports/evidence_manifests/fixture.native.json",
                    "evidenceSetId": EVIDENCE_SET_ID,
                    "binarySha256": SHA_A,
                    "pdbSha256": SHA_B,
                    "recipeId": "test-native-fixture/v1",
                    "recipeSha256": SHA_C,
                    "generatorScriptSha256": {"runner": SHA_D},
                }
            ],
            "runtimeObservationSets": [],
        },
        "claims": [
            {
                "claimId": "claim://fixture/quality-result",
                "summary": "Quality result is bounded.",
                "status": "STATIC_REVERSED",
                "confidence": "HIGH",
                "evidenceRefs": [NATIVE_ID],
                "assumptions": ["Synthetic fixture mirrors the supported branch."],
                "sourceFingerprints": {
                    "nativeEvidenceSetId": EVIDENCE_SET_ID,
                    "binarySha256": SHA_A,
                    "recipeSha256": SHA_C,
                },
                "invalidationConditions": [
                    "binary sha changed",
                    "recipe changed",
                    "generator changed",
                ],
                "reportMarkers": ["Quality result is bounded."],
                "runtimeValidation": {
                    "status": "NOT_RUN",
                    "observationRefs": [],
                },
            }
        ],
    }
    manifest_path = root / "reports" / "manifests" / "fixture.claims.json"
    _write_json(manifest_path, claims)
    return manifest_path


class ReportClaimValidationTests(unittest.TestCase):
    def test_verified_sanitized_manifest_is_sufficient_when_local_full_evidence_is_absent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)

            result = validate_claim_manifests(root, [manifest], formal=True)

            self.assertTrue(result["ok"])
            self.assertEqual(result["summary"]["manifests"], 1)
            self.assertEqual(result["summary"]["claims"], 1)
            self.assertEqual(result["summary"]["errors"], 0)
            self.assertIn(
                "LOCAL_EVIDENCE_REQUIRED",
                {issue["code"] for issue in result["issues"]},
            )

    def test_unknown_native_evidence_ref_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["claims"][0]["evidenceRefs"] = [
                f"native://{SHA_A}/fixture.dll/0x9999"
            ]
            _write_json(manifest, payload)

            result = validate_claim_manifests(root, [manifest])

            self.assertFalse(result["ok"])
            self.assertIn(
                "EVIDENCE_REF_NOT_FOUND",
                {issue["code"] for issue in result["issues"]},
            )

    def test_recipe_fingerprint_drift_is_reported_as_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["dependencies"]["nativeEvidenceSets"][0]["recipeSha256"] = "e" * 64
            _write_json(manifest, payload)

            result = validate_claim_manifests(root, [manifest])

            self.assertFalse(result["ok"])
            self.assertIn(
                "STALE_RECIPE",
                {issue["code"] for issue in result["issues"]},
            )

    def test_missing_report_marker_detects_report_manifest_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            report = root / "reports" / "fixture.md"
            report.write_text("# Fixture report\n\nDifferent text.\n", encoding="utf-8")

            result = validate_claim_manifests(root, [manifest])

            self.assertFalse(result["ok"])
            self.assertIn(
                "REPORT_CLAIM_MARKER_MISSING",
                {issue["code"] for issue in result["issues"]},
            )

    def test_incomplete_legacy_provenance_is_rejected_in_formal_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root, trust_status="PROVENANCE_INCOMPLETE")

            result = validate_claim_manifests(root, [manifest], formal=True)

            self.assertFalse(result["ok"])
            self.assertIn(
                "PROVENANCE_UNVERIFIED",
                {issue["code"] for issue in result["issues"]},
            )

    def test_duplicate_claim_ids_fail_across_manifests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = _valid_tree(root)
            second = root / "reports" / "manifests" / "duplicate.claims.json"
            payload = json.loads(first.read_text(encoding="utf-8"))
            payload["reportId"] = "duplicate-report"
            _write_json(second, payload)

            result = validate_claim_manifests(root, [first, second])

            self.assertFalse(result["ok"])
            self.assertIn(
                "DUPLICATE_CLAIM_ID",
                {issue["code"] for issue in result["issues"]},
            )

    def test_blueprint_dependency_fingerprint_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            blueprint_manifest_path = (
                root / "reports" / "evidence_manifests" / "fixture.blueprint.json"
            )
            _write_json(
                blueprint_manifest_path,
                {
                    "schema": "blueprint-to-code-sanitized-blueprint-evidence/v1",
                    "assetId": "/Game/Fixture/Quality_BP",
                    "revisionId": "222222222222222222222222",
                    "sourceFingerprint": "e" * 64,
                    "trust": {"status": "VERIFIED"},
                    "evidenceRefs": [BP_ID],
                },
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["dependencies"]["blueprintAssets"] = [
                {
                    "manifestPath": (
                        "reports/evidence_manifests/fixture.blueprint.json"
                    ),
                    "assetId": "/Game/Fixture/Quality_BP",
                    "revisionId": "222222222222222222222222",
                    "sourceFingerprint": "f" * 64,
                }
            ]
            payload["claims"][0]["evidenceRefs"].append(BP_ID)
            payload["claims"][0]["sourceFingerprints"][
                "blueprintSourceFingerprint"
            ] = "f" * 64
            _write_json(manifest, payload)

            result = validate_claim_manifests(root, [manifest], formal=True)

            self.assertFalse(result["ok"])
            self.assertIn(
                "STALE_SOURCE",
                {issue["code"] for issue in result["issues"]},
            )

    def test_runtime_observation_hash_and_reference_are_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            runtime_path = (
                root / "reports" / "evidence_manifests" / "fixture.runtime.json"
            )
            _write_json(
                runtime_path,
                {
                    "schema": "blueprint-to-code-runtime-observation-set/v1",
                    "observationSetId": RUNTIME_ID,
                    "synthetic": True,
                    "environment": {},
                    "subject": {},
                    "staticModel": {},
                    "policy": {},
                    "unsupportedDynamicBranches": [],
                    "trials": [],
                },
            )
            runtime_sha = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["dependencies"]["runtimeObservationSets"] = [
                {
                    "manifestPath": (
                        "reports/evidence_manifests/fixture.runtime.json"
                    ),
                    "observationSetId": RUNTIME_ID,
                    "sourceSha256": runtime_sha,
                }
            ]
            payload["claims"][0]["evidenceRefs"].append(RUNTIME_ID)
            payload["claims"][0]["runtimeValidation"] = {
                "status": "RUNTIME_CALIBRATED",
                "observationRefs": [RUNTIME_ID],
            }
            _write_json(manifest, payload)

            valid = validate_claim_manifests(root, [manifest], formal=True)
            self.assertTrue(valid["ok"], valid["issues"])

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["dependencies"]["runtimeObservationSets"][0][
                "sourceSha256"
            ] = "0" * 64
            _write_json(manifest, payload)
            stale = validate_claim_manifests(root, [manifest], formal=True)
            self.assertFalse(stale["ok"])
            self.assertIn(
                "STALE_RUNTIME_OBSERVATION",
                {issue["code"] for issue in stale["issues"]},
            )


if __name__ == "__main__":
    unittest.main()
