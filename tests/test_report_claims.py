from __future__ import annotations

import json
import hashlib
import subprocess
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
RUNTIME_FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "runtime_observations"
    / "harvest-linear-match.json"
)
HISTORICAL_REPORT_MANIFESTS = (
    "reports/manifests/tides-of-fortune-complete-native-2026-07-26.claims.json",
    "reports/manifests/ark-player-visible-reward-model-2026-07-26.claims.json",
    "reports/manifests/ferox-force-flee-mechanism-2026-07-26.claims.json",
)
HISTORICAL_REPORTS = (
    "reports/TIDES_OF_FORTUNE_COMPLETE_NATIVE_2026-07-26.md",
    "reports/ARK_PLAYER_VISIBLE_REWARD_MODEL_DEEP_DIVE_2026-07-26.md",
    "reports/FEROX_FORCE_FLEE_MECHANISM_2026-07-26.md",
)
HISTORICAL_NATIVE_MANIFEST = (
    "reports/evidence_manifests/"
    "shooter-game-native-legacy-2026-07-26.native.json"
)


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


def _attach_runtime_fixture(
    root: Path,
    manifest: Path,
    *,
    synthetic: bool,
    declared_status: str,
) -> tuple[Path, dict[str, object]]:
    observation = json.loads(RUNTIME_FIXTURE.read_text(encoding="utf-8"))
    observation["observationSetId"] = RUNTIME_ID
    observation["synthetic"] = synthetic
    runtime_path = (
        root / "reports" / "evidence_manifests" / "fixture.runtime.json"
    )
    _write_json(runtime_path, observation)
    runtime_sha = hashlib.sha256(runtime_path.read_bytes()).hexdigest()

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["dependencies"]["runtimeObservationSets"] = [
        {
            "manifestPath": "reports/evidence_manifests/fixture.runtime.json",
            "observationSetId": RUNTIME_ID,
            "sourceSha256": runtime_sha,
        }
    ]
    payload["claims"][0]["evidenceRefs"].append(RUNTIME_ID)
    payload["claims"][0]["sourceFingerprints"][
        "runtimeObservationSha256"
    ] = runtime_sha
    payload["claims"][0]["runtimeValidation"] = {
        "status": declared_status,
        "observationRefs": [RUNTIME_ID],
    }
    _write_json(manifest, payload)
    return runtime_path, payload


class ReportClaimValidationTests(unittest.TestCase):
    def test_cli_runs_from_repository_root_with_bundled_python(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "validate_report_claims.py"),
                "--help",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Validate report claim manifests", result.stdout)

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

    def test_runtime_validation_schema_limits_status_and_reference_shape(self):
        schema = json.loads(
            (ROOT / "schemas" / "report_claim_manifest_v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        runtime_schema = schema["properties"]["claims"]["items"]["properties"][
            "runtimeValidation"
        ]
        self.assertEqual(
            set(runtime_schema["properties"]["status"]["enum"]),
            {
                "NOT_RUN",
                "STATIC_REVERSED",
                "INSUFFICIENT_OBSERVATIONS",
                "RUNTIME_CALIBRATED",
                "RUNTIME_CONFIRMED",
                "RUNTIME_DIVERGED",
                "UNSUPPORTED_DYNAMIC_BRANCH",
            },
        )
        self.assertEqual(len(runtime_schema["oneOf"]), 2)

    def test_malformed_runtime_observation_is_rejected(self):
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

            result = validate_claim_manifests(root, [manifest], formal=True)
            self.assertFalse(result["ok"])
            self.assertIn(
                "RUNTIME_OBSERVATION_INVALID",
                {issue["code"] for issue in result["issues"]},
            )

    def test_synthetic_runtime_observation_cannot_confirm_a_formal_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            _attach_runtime_fixture(
                root,
                manifest,
                synthetic=True,
                declared_status="RUNTIME_CONFIRMED",
            )

            result = validate_claim_manifests(root, [manifest], formal=True)

            self.assertFalse(result["ok"])
            self.assertIn(
                "RUNTIME_SYNTHETIC_NOT_ALLOWED",
                {issue["code"] for issue in result["issues"]},
            )

    def test_declared_runtime_status_must_match_recomputed_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            _attach_runtime_fixture(
                root,
                manifest,
                synthetic=False,
                declared_status="RUNTIME_CALIBRATED",
            )

            result = validate_claim_manifests(root, [manifest], formal=True)

            self.assertFalse(result["ok"])
            self.assertIn(
                "RUNTIME_STATUS_MISMATCH",
                {issue["code"] for issue in result["issues"]},
            )

    def test_runtime_status_reference_rules_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["claims"][0]["runtimeValidation"] = {
                "status": "RUNTIME_CONFIRMED",
                "observationRefs": [],
            }
            _write_json(manifest, payload)

            missing = validate_claim_manifests(root, [manifest], formal=True)
            self.assertFalse(missing["ok"])
            self.assertIn(
                "RUNTIME_VALIDATION_INVALID",
                {issue["code"] for issue in missing["issues"]},
            )

            _attach_runtime_fixture(
                root,
                manifest,
                synthetic=False,
                declared_status="NOT_RUN",
            )
            unexpected = validate_claim_manifests(root, [manifest], formal=True)
            self.assertFalse(unexpected["ok"])
            self.assertIn(
                "RUNTIME_VALIDATION_INVALID",
                {issue["code"] for issue in unexpected["issues"]},
            )

    def test_real_runtime_observation_can_confirm_and_hash_drift_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = _valid_tree(root)
            _runtime_path, payload = _attach_runtime_fixture(
                root,
                manifest,
                synthetic=False,
                declared_status="RUNTIME_CONFIRMED",
            )

            valid = validate_claim_manifests(root, [manifest], formal=True)
            self.assertTrue(valid["ok"], valid["issues"])

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


class HistoricalReportClaimMigrationTests(unittest.TestCase):
    def test_committed_formal_fixture_remains_a_verified_release_gate(self):
        fixture_root = ROOT / "tests" / "fixtures" / "report_claims"
        fixture_manifest = (
            fixture_root
            / "reports"
            / "manifests"
            / "fixture.claims.json"
        )

        result = validate_claim_manifests(
            fixture_root,
            [fixture_manifest],
            formal=True,
        )

        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["summary"]["claims"], 1)
        self.assertEqual(result["summary"]["errors"], 0)

    def test_historical_migration_passes_default_with_explicit_provenance_warnings(self):
        manifests = [ROOT / path for path in HISTORICAL_REPORT_MANIFESTS]

        result = validate_claim_manifests(ROOT, manifests)

        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["summary"]["manifests"], 3)
        self.assertEqual(result["summary"]["claims"], 10)
        warning_codes = {
            issue["code"]
            for issue in result["issues"]
            if issue["severity"] == "WARNING"
        }
        self.assertIn("PROVENANCE_UNVERIFIED", warning_codes)
        self.assertIn("LOCAL_EVIDENCE_REQUIRED", warning_codes)

    def test_historical_migration_fails_closed_in_formal_mode(self):
        manifests = [ROOT / path for path in HISTORICAL_REPORT_MANIFESTS]

        result = validate_claim_manifests(ROOT, manifests, formal=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["summary"]["claims"], 10)
        self.assertIn(
            "PROVENANCE_UNVERIFIED",
            {
                issue["code"]
                for issue in result["issues"]
                if issue["severity"] == "ERROR"
            },
        )

    def test_migrated_reports_link_only_committed_claim_and_sanitized_evidence(self):
        for report_path in HISTORICAL_REPORTS:
            report = ROOT / report_path
            text = report.read_text(encoding="utf-8")
            with self.subTest(report=report_path):
                self.assertNotIn("../native_evidence/", text)
                self.assertNotIn("native_evidence/shooter-game-native-", text)
                self.assertIn("./manifests/", text)
                self.assertIn("./evidence_manifests/", text)

    def test_historical_sanitized_manifest_contains_only_bounded_public_metadata(self):
        manifest_path = ROOT / HISTORICAL_NATIVE_MANIFEST
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(
            payload["schema"],
            "blueprint-to-code-sanitized-native-evidence/v1",
        )
        self.assertEqual(payload["trust"]["status"], "PROVENANCE_INCOMPLETE")
        self.assertEqual(len(payload["targets"]), 23)
        self.assertEqual(
            len({target["evidenceId"] for target in payload["targets"]}),
            23,
        )
        self.assertNotIn("decompiledC", serialized)
        self.assertNotIn("executablePath", serialized)
        self.assertNotIn("C:\\", serialized)

    def test_cross_source_historical_claims_bind_verified_blueprint_evidence(self):
        tides_path = ROOT / HISTORICAL_REPORT_MANIFESTS[0]
        reward_path = ROOT / HISTORICAL_REPORT_MANIFESTS[1]
        ferox_path = ROOT / HISTORICAL_REPORT_MANIFESTS[2]
        tides = json.loads(tides_path.read_text(encoding="utf-8"))
        reward = json.loads(reward_path.read_text(encoding="utf-8"))
        ferox = json.loads(ferox_path.read_text(encoding="utf-8"))

        claims = {
            claim["claimId"]: claim
            for manifest in (tides, reward, ferox)
            for claim in manifest["claims"]
        }
        cross_source_ids = {
            "claim://tides-of-fortune/item-set-count-distribution",
            "claim://tides-of-fortune/effective-crate-quality-range",
            "claim://ferox-force-flee/native-refresh-while-blueprint-gate-is-true",
            "claim://ferox-force-flee/ally-witness-chain",
        }
        for claim_id in cross_source_ids:
            refs = claims[claim_id]["evidenceRefs"]
            with self.subTest(claim_id=claim_id):
                self.assertTrue(any(ref.startswith("bp://") for ref in refs))
                self.assertTrue(any(ref.startswith("native://") for ref in refs))

        for claim in reward["claims"]:
            self.assertTrue(
                all(ref.startswith("native://") for ref in claim["evidenceRefs"]),
                claim["claimId"],
            )

        blueprint_dependencies = (
            tides["dependencies"]["blueprintAssets"]
            + ferox["dependencies"]["blueprintAssets"]
        )
        self.assertEqual(len(blueprint_dependencies), 8)
        for dependency in blueprint_dependencies:
            manifest_path = ROOT / dependency["manifestPath"]
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            serialized = json.dumps(payload, ensure_ascii=False)
            with self.subTest(manifest=dependency["manifestPath"]):
                self.assertEqual(
                    payload["schema"],
                    "blueprint-to-code-sanitized-blueprint-evidence/v1",
                )
                self.assertEqual(payload["trust"]["status"], "VERIFIED")
                self.assertNotIn("C:\\", serialized)


if __name__ == "__main__":
    unittest.main()
