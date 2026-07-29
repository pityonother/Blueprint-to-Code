from __future__ import annotations

import json
import tempfile
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.quality_contract import (  # noqa: E402
    BENCHMARK_SCHEMA,
    QUALITY_GATE_CONTRACT,
    QUALITY_GATE_SCHEMA,
)
from blueprint_translator.kb_vnext.snapshot import (  # noqa: E402
    _seal_staged_quality_report,
    _stage_burn_in_attestation,
    validate_sealed_snapshot_quality,
)


def _passing_report() -> dict[str, object]:
    benchmark = {
        "schema": BENCHMARK_SCHEMA,
        "total": 130,
    }
    gates = [
        {
            "id": gate_id,
            "category": category,
            "critical": critical,
            "passed": True,
            "target": True,
            "actual": True,
            "detail": "fixture-only passing gate",
        }
        for gate_id, category, critical in sorted(QUALITY_GATE_CONTRACT)
    ]
    return {
        "schema": QUALITY_GATE_SCHEMA,
        "buildId": "fixture-build",
        "summary": {
            "total": len(gates),
            "passed": len(gates),
            "failed": 0,
            "cutoverEligible": True,
            "recommendation": "ready_for_default",
        },
        "gates": gates,
        "benchmark": benchmark,
    }


def _valid_fixture_attestation() -> dict[str, object]:
    scenarios = {
        "blueprintModified": True,
        "blueprintAdded": True,
        "blueprintDeleted": True,
        "registrationTargetChanged": True,
        "classParentChanged": True,
        "nativeEvidenceUpdated": True,
        "runtimeSummaryUpdated": True,
        "workerCrash": True,
        "narrowGateFailure": True,
        "pointerPreSwapCrash": True,
        "concurrentReaders": True,
        "unchangedCacheHit": True,
    }
    snapshots = [
        {
            "buildId": f"fixture-pass-{index}",
            "qualityReportSha256": str(index) * 64,
            "passedAt": f"2026-07-2{index}T00:00:00Z",
            "qualityReportCutoverEligible": True,
            "sealedInSnapshotManifest": True,
        }
        for index in range(1, 4)
    ]
    return {
        "schema": "ark-kb-burn-in-attestation/v1",
        "policyVersion": "ark-kb-burn-in-policy/v1",
        "status": "PASSED",
        "attestedAt": "2026-07-29T00:00:00Z",
        "toolVersion": "fixture-only/v1",
        "review": {
            "reviewerType": "HUMAN_OPERATOR",
            "reviewerId": "fixture-human-reviewer",
            "reviewedAt": "2026-07-29T00:00:00Z",
            "decision": "APPROVED",
        },
        "sealedSnapshots": snapshots,
        "legacyVnextDiffDisposition": {
            "complete": True,
            "undispositioned": 0,
            "wrongAnswers": 0,
            "staleLeaks": 0,
            "candidateCompletions": 0,
        },
        "rollbackDrill": {
            "passed": True,
            "fromBuildId": "fixture-pass-3",
            "toBuildId": "fixture-pass-2",
            "completedAt": "2026-07-29T00:00:00Z",
        },
        "concurrentReaderDrill": {
            "passed": True,
            "mixedBuildObservations": 0,
            "completedAt": "2026-07-29T00:00:00Z",
        },
        "incrementalProduction": {
            "passed": True,
            "scenarios": scenarios,
        },
    }


class KnowledgeCutoverBurnInTests(unittest.TestCase):
    def test_all_quality_gates_pass_but_burn_in_missing_stays_shadow_legacy(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            manifest = {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": "fixture-build",
            }
            report = _passing_report()

            sealed = _seal_staged_quality_report(
                staging=staging,
                manifest=manifest,
                report=report,
            )

        self.assertTrue(
            sealed["qualityGates"]["qualityReportCutoverEligible"]
        )
        self.assertFalse(sealed["qualityGates"]["cutoverEligible"])
        self.assertEqual(sealed["burnIn"]["status"], "MISSING")
        self.assertEqual(sealed["cutover"]["mode"], "shadow")
        self.assertEqual(
            sealed["cutover"]["defaultQuerySource"],
            "legacy",
        )

    def test_valid_burn_in_is_hash_bound_before_ready_cutover(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            staging.mkdir()
            source = root / "burn-in.json"
            source.write_text(
                json.dumps(_valid_fixture_attestation()),
                encoding="utf-8",
            )
            burn_in = _stage_burn_in_attestation(
                staging=staging,
                source_path=source,
            )
            sealed = _seal_staged_quality_report(
                staging=staging,
                manifest={
                    "schema": "ark-kb-vnext-snapshot/v1",
                    "buildId": "fixture-build",
                },
                report=_passing_report(),
                burn_in=burn_in,
            )

            validate_sealed_snapshot_quality(
                snapshot_dir=staging,
                manifest=sealed,
            )

            self.assertTrue(sealed["qualityGates"]["cutoverEligible"])
            self.assertEqual(sealed["burnIn"]["status"], "VALID")
            self.assertEqual(sealed["cutover"]["mode"], "ready")
            self.assertEqual(
                sealed["cutover"]["defaultQuerySource"],
                "vnext",
            )

            burn_in_path = staging / "reports" / "burn_in_attestation.json"
            tampered = json.loads(burn_in_path.read_text(encoding="utf-8"))
            tampered["review"]["reviewerId"] = "tampered"
            burn_in_path.write_text(
                json.dumps(tampered),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "burn-in binding is invalid",
            ):
                validate_sealed_snapshot_quality(
                    snapshot_dir=staging,
                    manifest=sealed,
                )

    def test_incomplete_incremental_scenarios_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "burn-in.json"
            attestation = _valid_fixture_attestation()
            attestation["incrementalProduction"]["scenarios"][
                "blueprintDeleted"
            ] = False
            source.write_text(
                json.dumps(attestation),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "incremental production scenarios are incomplete",
            ):
                _stage_burn_in_attestation(
                    staging=root / "staging",
                    source_path=source,
                )

    def test_burn_in_uses_pre_cutover_quality_not_circular_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "burn-in.json"
            attestation = _valid_fixture_attestation()
            snapshot = attestation["sealedSnapshots"][0]
            snapshot["cutoverEligible"] = snapshot.pop(
                "qualityReportCutoverEligible"
            )
            source.write_text(
                json.dumps(attestation),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                r"sealedSnapshots\[0\] fields are invalid",
            ):
                _stage_burn_in_attestation(
                    staging=root / "staging",
                    source_path=source,
                )

    def test_unrecorded_attestation_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "burn-in.json"
            attestation = _valid_fixture_attestation()
            attestation["unrecordedOverride"] = True
            source.write_text(
                json.dumps(attestation),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "burn-in attestation fields are invalid",
            ):
                _stage_burn_in_attestation(
                    staging=root / "staging",
                    source_path=source,
                )


if __name__ == "__main__":
    unittest.main()
