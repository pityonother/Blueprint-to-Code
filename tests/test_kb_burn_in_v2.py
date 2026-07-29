from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.burn_in_v2 import (  # noqa: E402
    BURN_IN_ATTESTATION_V2_SCHEMA,
    BURN_IN_EVIDENCE_BUNDLE_V2_SCHEMA,
    BURN_IN_POLICY_V2,
    BurnInV2Error,
    NO_PUBLISH_INCREMENTAL_SCENARIOS,
    PUBLISHED_INCREMENTAL_SCENARIOS,
    validate_burn_in_attestation_v2,
)
from blueprint_translator.kb_vnext.cutover_readiness import (  # noqa: E402
    REQUIRED_INCREMENTAL_SCENARIOS,
)
from blueprint_translator.kb_vnext.signed_receipts import (  # noqa: E402
    TEST_ONLY,
    canonical_json_bytes,
    public_key_fingerprint,
    registry_entry_sha256,
    registry_version_sha256,
    signed_payload_sha256,
)


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
AUTOMATED_SIGNER_ID = "automated-contract-fixture"
BURN_IN_RUN_ID = "fixture-burn-in-run-001"
REPRESENTATIVE_CORPUS_ID = "fixture-representative-corpus-v1"
REPRESENTATIVE_CASE_IDS = (
    "fixture-shadow-001",
    "fixture-shadow-002",
)
REPRESENTATIVE_CORPUS_SHA256 = hashlib.sha256(
    b"fixture-representative-corpus-v1"
).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> bytes:
    payload = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _raw_public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _registry(
    private_key: Ed25519PrivateKey,
    *,
    valid_until: str = "2027-07-29T00:00:00Z",
    revoked_at: str | None = None,
) -> dict[str, object]:
    public_key = _raw_public_key(private_key)
    entry: dict[str, object] = {
        "reviewerId": AUTOMATED_SIGNER_ID,
        "publicKeyAlgorithm": "Ed25519",
        "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
        "publicKeyFingerprint": public_key_fingerprint(public_key),
        "allowedRoles": ["BURN_IN_OPERATOR"],
        "validFrom": "2026-01-01T00:00:00Z",
        "validUntil": valid_until,
        "revokedAt": revoked_at,
        "registryEntrySha256": "",
    }
    entry["registryEntrySha256"] = registry_entry_sha256(entry)
    value: dict[str, object] = {
        "schema": "ark-kb-trusted-reviewer-registry/v2",
        "registryId": "test-only-automated-contract",
        "registryVersion": "fixture-v1",
        "trustContext": TEST_ONLY,
        "generatedAt": "2026-07-29T09:00:00Z",
        "reviewers": [entry],
        "registryVersionSha256": "",
    }
    value["registryVersionSha256"] = registry_version_sha256(value)
    return value


def _receipt(
    private_key: Ed25519PrivateKey,
    registry: Mapping[str, object],
    *,
    receipt_id: str,
    issued_at: str,
    nonce: str,
    scope: Mapping[str, object],
    claim: Mapping[str, object],
    artifact_uri: str,
    artifact_sha256: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "ark-kb-signed-receipt-payload/v2",
        "receiptId": receipt_id,
        "registryVersionSha256": registry["registryVersionSha256"],
        "trustContext": TEST_ONLY,
        "signerId": AUTOMATED_SIGNER_ID,
        "role": "BURN_IN_OPERATOR",
        "issuedAt": issued_at,
        "nonce": nonce,
        "scope": dict(scope),
        "artifactUri": artifact_uri,
        "artifactSha256": artifact_sha256,
        "claim": dict(claim),
    }
    signature = private_key.sign(canonical_json_bytes(payload))
    return {
        "schema": "ark-kb-signed-receipt-envelope/v2",
        "signatureAlgorithm": "Ed25519",
        "payload": payload,
        "signedPayloadSha256": signed_payload_sha256(payload),
        "signatureBase64": base64.b64encode(signature).decode("ascii"),
    }


def _resign(
    receipt: dict[str, object],
    private_key: Ed25519PrivateKey,
) -> None:
    payload = receipt["payload"]
    assert isinstance(payload, dict)
    receipt["signedPayloadSha256"] = signed_payload_sha256(payload)
    receipt["signatureBase64"] = base64.b64encode(
        private_key.sign(canonical_json_bytes(payload))
    ).decode("ascii")


def _receipt_payload(receipt: dict[str, object]) -> dict[str, object]:
    payload = receipt["payload"]
    assert isinstance(payload, dict)
    return payload


class BurnInFixture:
    def __init__(
        self,
        root: Path,
        *,
        valid_until: str = "2027-07-29T00:00:00Z",
        revoked_at: str | None = None,
    ) -> None:
        self.root = root
        self.artifact_root = root / "artifacts"
        self.snapshot_root = root / "vnext"
        self.private_key = Ed25519PrivateKey.generate()
        self.registry = _registry(
            self.private_key,
            valid_until=valid_until,
            revoked_at=revoked_at,
        )
        self.burn_in_run_id = BURN_IN_RUN_ID
        self.candidate_build_id = "fixture-ready-candidate"
        (
            self.snapshot_records,
            self.success_transitions,
        ) = self._build_snapshot_chain()
        self.previous_build_id = str(self.snapshot_records[-1]["buildId"])
        self.previous_manifest_sha256 = str(self.snapshot_records[-1]["manifestSha256"])
        self.scenario_receipts = self._scenario_receipts()
        self.rollback_receipt = self._rollback_receipt()
        self.concurrent_receipt = self._concurrent_receipt()
        self.shadow_receipt = self._shadow_receipt()
        self.bundle: dict[str, object] = {
            "schema": BURN_IN_EVIDENCE_BUNDLE_V2_SCHEMA,
            "policyVersion": BURN_IN_POLICY_V2,
            "burnInRunId": self.burn_in_run_id,
            "candidateBuildId": self.candidate_build_id,
            "previousBuildId": self.previous_build_id,
            "previousManifestSha256": self.previous_manifest_sha256,
            "sealedSnapshots": self.snapshot_records,
            "incrementalScenarioReceipts": self.scenario_receipts,
            "rollbackReceipt": self.rollback_receipt,
            "concurrentReaderReceipt": self.concurrent_receipt,
            "shadowDiffDispositionReceipt": self.shadow_receipt,
        }
        self.attestation: dict[str, object] = {}
        self.refresh_top_approval()

    def _build_snapshot_chain(
        self,
    ) -> tuple[
        list[dict[str, object]],
        dict[str, tuple[str, str, str, str]],
    ]:
        snapshots = self.snapshot_root / "snapshots"
        anchor_id = "fixture-anchor"
        anchor_manifest = {
            "schema": "ark-kb-vnext-snapshot/v1",
            "buildId": anchor_id,
            "generatedAt": "2026-07-29T09:00:00Z",
            "cutover": {
                "mode": "shadow",
                "defaultQuerySource": "legacy",
            },
        }
        anchor_bytes = _write_json(
            snapshots / anchor_id / "manifest.json",
            anchor_manifest,
        )
        previous_id = anchor_id
        previous_sha = _sha256(anchor_bytes)
        transitions: dict[str, tuple[str, str, str, str]] = {}
        for index, scenario_id in enumerate(
            sorted(PUBLISHED_INCREMENTAL_SCENARIOS),
            start=1,
        ):
            build_id = f"fixture-e4-{index:02d}"
            manifest = {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": build_id,
                "generatedAt": f"2026-07-29T09:{index:02d}:00Z",
                "previousSnapshot": {
                    "buildId": previous_id,
                    "manifestSha256": previous_sha,
                },
                "cutover": {
                    "mode": "shadow",
                    "defaultQuerySource": "legacy",
                },
            }
            manifest_bytes = _write_json(
                snapshots / build_id / "manifest.json",
                manifest,
            )
            manifest_sha = _sha256(manifest_bytes)
            transitions[scenario_id] = (
                previous_id,
                previous_sha,
                build_id,
                manifest_sha,
            )
            previous_id = build_id
            previous_sha = manifest_sha
        records: list[dict[str, object]] = []
        for index in range(1, 4):
            build_id = f"fixture-burn-{index}"
            report = {
                "schema": "ark-kb-vnext-quality-gates/v1",
                "buildId": build_id,
                "summary": {
                    "total": 75,
                    "passed": 75,
                    "failed": 0,
                    "cutoverEligible": True,
                    "recommendation": "ready_for_default",
                },
            }
            snapshot_dir = snapshots / build_id
            report_bytes = _write_json(
                snapshot_dir / "reports" / "quality_gates.json",
                report,
            )
            report_sha = _sha256(report_bytes)
            manifest = {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": build_id,
                "generatedAt": f"2026-07-29T0{9 + index}:00:00Z",
                "previousSnapshot": {
                    "buildId": previous_id,
                    "manifestSha256": previous_sha,
                },
                "qualityGates": {
                    "reportUri": "reports/quality_gates.json",
                    "sha256": report_sha,
                    "qualityReportCutoverEligible": True,
                    "cutoverEligible": False,
                    "sealedInSnapshotManifest": True,
                },
                "cutover": {
                    "mode": "shadow",
                    "defaultQuerySource": "legacy",
                },
            }
            manifest_bytes = _write_json(
                snapshot_dir / "manifest.json",
                manifest,
            )
            manifest_sha = _sha256(manifest_bytes)
            records.append(
                {
                    "buildId": build_id,
                    "manifestSha256": manifest_sha,
                    "qualityReportSha256": report_sha,
                    "previousBuildId": previous_id,
                    "previousManifestSha256": previous_sha,
                    "qualityReportCutoverEligible": True,
                    "sealedInSnapshotManifest": True,
                }
            )
            previous_id = build_id
            previous_sha = manifest_sha
        _write_json(
            self.snapshot_root / "current.json",
            {
                "buildId": previous_id,
                "snapshotRelativePath": f"snapshots/{previous_id}",
            },
        )
        return records, transitions

    def _write_artifact(
        self,
        relative_path: str,
        value: object,
    ) -> tuple[str, str]:
        artifact_bytes = _write_json(self.artifact_root / relative_path, value)
        return f"artifact://{relative_path}", _sha256(artifact_bytes)

    def _scenario_receipts(self) -> dict[str, dict[str, object]]:
        chain_tip = self.snapshot_records[-1]
        concurrent_base = self.snapshot_records[-2]
        concurrent_result = self.snapshot_records[-1]
        receipts: dict[str, dict[str, object]] = {}
        for index, scenario_id in enumerate(REQUIRED_INCREMENTAL_SCENARIOS):
            if scenario_id in PUBLISHED_INCREMENTAL_SCENARIOS:
                (
                    base_build_id,
                    previous_manifest_sha256,
                    result_build_id,
                    result_manifest_sha256,
                ) = self.success_transitions[scenario_id]
                published = True
                current_unchanged = False
                cache_hit = False
                mixed_build_observations = 0
                pointer_swaps_exercised = 1
            elif scenario_id in NO_PUBLISH_INCREMENTAL_SCENARIOS:
                base_build_id = str(chain_tip["buildId"])
                previous_manifest_sha256 = str(chain_tip["manifestSha256"])
                result_build_id = base_build_id
                result_manifest_sha256 = previous_manifest_sha256
                published = False
                current_unchanged = True
                cache_hit = False
                mixed_build_observations = 0
                pointer_swaps_exercised = 0
            elif scenario_id == "unchangedCacheHit":
                base_build_id = str(chain_tip["buildId"])
                previous_manifest_sha256 = str(chain_tip["manifestSha256"])
                result_build_id = base_build_id
                result_manifest_sha256 = previous_manifest_sha256
                published = False
                current_unchanged = True
                cache_hit = True
                mixed_build_observations = 0
                pointer_swaps_exercised = 0
            elif scenario_id == "concurrentReaders":
                base_build_id = str(concurrent_base["buildId"])
                previous_manifest_sha256 = str(concurrent_base["manifestSha256"])
                result_build_id = str(concurrent_result["buildId"])
                result_manifest_sha256 = str(concurrent_result["manifestSha256"])
                published = False
                current_unchanged = True
                cache_hit = False
                mixed_build_observations = 0
                pointer_swaps_exercised = 2
            else:  # pragma: no cover - policy partition is asserted by production
                raise AssertionError(f"unclassified E4 scenario: {scenario_id}")
            artifact = {
                "schema": "ark-kb-incremental-scenario-result/v2",
                "burnInRunId": self.burn_in_run_id,
                "candidateBuildId": self.candidate_build_id,
                "scenarioId": scenario_id,
                "status": "PASSED",
                "baseBuildId": base_build_id,
                "resultBuildId": result_build_id,
                "previousManifestSha256": previous_manifest_sha256,
                "resultManifestSha256": result_manifest_sha256,
                "sourceDiffSha256": hashlib.sha256(
                    scenario_id.encode("utf-8")
                ).hexdigest(),
                "published": published,
                "currentUnchanged": current_unchanged,
                "cacheHit": cache_hit,
                "mixedBuildObservations": mixed_build_observations,
                "pointerSwapsExercised": pointer_swaps_exercised,
                "command": f"fixture-run {scenario_id}",
                "toolVersion": "automated-contract-fixture/v2",
                "startedAt": "2026-07-29T10:10:00Z",
                "completedAt": "2026-07-29T10:11:00Z",
            }
            uri, artifact_sha = self._write_artifact(
                f"scenario/{index:02d}-{scenario_id}.json",
                artifact,
            )
            claim = {
                key: artifact[key]
                for key in (
                    "status",
                    "previousManifestSha256",
                    "resultManifestSha256",
                    "sourceDiffSha256",
                    "published",
                    "currentUnchanged",
                    "cacheHit",
                    "mixedBuildObservations",
                    "pointerSwapsExercised",
                    "command",
                    "toolVersion",
                    "startedAt",
                    "completedAt",
                )
            }
            receipts[scenario_id] = _receipt(
                self.private_key,
                self.registry,
                receipt_id=f"scenario-{index:02d}",
                issued_at="2026-07-29T10:12:00Z",
                nonce=f"scenario-{index:02d}-nonce",
                scope={
                    "kind": "INCREMENTAL_SCENARIO",
                    "policyVersion": BURN_IN_POLICY_V2,
                    "burnInRunId": self.burn_in_run_id,
                    "candidateBuildId": self.candidate_build_id,
                    "scenarioId": scenario_id,
                    "baseBuildId": base_build_id,
                    "resultBuildId": result_build_id,
                },
                claim=claim,
                artifact_uri=uri,
                artifact_sha256=artifact_sha,
            )
        return receipts

    def _rollback_receipt(self) -> dict[str, object]:
        from_snapshot = self.snapshot_records[2]
        to_snapshot = self.snapshot_records[1]
        from_build = str(from_snapshot["buildId"])
        to_build = str(to_snapshot["buildId"])
        artifact = {
            "schema": "ark-kb-rollback-drill-result/v2",
            "burnInRunId": self.burn_in_run_id,
            "candidateBuildId": self.candidate_build_id,
            "status": "PASSED",
            "fromBuildId": from_build,
            "toBuildId": to_build,
            "fromManifestSha256": from_snapshot["manifestSha256"],
            "toManifestSha256": to_snapshot["manifestSha256"],
            "pointerBeforeSha256": _sha256(
                _json_bytes(
                    {
                        "buildId": from_build,
                        "snapshotRelativePath": f"snapshots/{from_build}",
                    }
                )
            ),
            "pointerAfterSha256": _sha256(
                _json_bytes(
                    {
                        "buildId": to_build,
                        "snapshotRelativePath": f"snapshots/{to_build}",
                    }
                )
            ),
            "expectedCurrentBuildId": from_build,
            "mixedBuildObservations": 0,
            "command": "fixture-rollback-drill",
            "toolVersion": "automated-contract-fixture/v2",
            "startedAt": "2026-07-29T10:20:00Z",
            "completedAt": "2026-07-29T10:21:00Z",
        }
        uri, artifact_sha = self._write_artifact(
            "drill/rollback.json",
            artifact,
        )
        claim = {
            key: artifact[key]
            for key in artifact
            if key
            not in {
                "schema",
                "burnInRunId",
                "candidateBuildId",
                "fromBuildId",
                "toBuildId",
            }
        }
        return _receipt(
            self.private_key,
            self.registry,
            receipt_id="rollback-drill",
            issued_at="2026-07-29T10:22:00Z",
            nonce="rollback-drill-nonce",
            scope={
                "kind": "ROLLBACK_DRILL",
                "policyVersion": BURN_IN_POLICY_V2,
                "burnInRunId": self.burn_in_run_id,
                "candidateBuildId": self.candidate_build_id,
                "fromBuildId": from_build,
                "toBuildId": to_build,
            },
            claim=claim,
            artifact_uri=uri,
            artifact_sha256=artifact_sha,
        )

    def _concurrent_receipt(self) -> dict[str, object]:
        from_build = str(self.snapshot_records[1]["buildId"])
        to_build = str(self.snapshot_records[2]["buildId"])
        artifact = {
            "schema": "ark-kb-concurrent-reader-drill-result/v2",
            "burnInRunId": self.burn_in_run_id,
            "candidateBuildId": self.candidate_build_id,
            "status": "PASSED",
            "fromBuildId": from_build,
            "toBuildId": to_build,
            "readerCount": 4,
            "requestCount": 40,
            "observedBuildIds": [from_build, to_build],
            "mixedBuildObservations": 0,
            "pointerSwapsExercised": 2,
            "command": "fixture-concurrent-reader-drill",
            "toolVersion": "automated-contract-fixture/v2",
            "startedAt": "2026-07-29T10:30:00Z",
            "completedAt": "2026-07-29T10:31:00Z",
        }
        uri, artifact_sha = self._write_artifact(
            "drill/concurrent-readers.json",
            artifact,
        )
        claim = {
            key: artifact[key]
            for key in artifact
            if key
            not in {
                "schema",
                "burnInRunId",
                "candidateBuildId",
                "fromBuildId",
                "toBuildId",
            }
        }
        return _receipt(
            self.private_key,
            self.registry,
            receipt_id="concurrent-reader-drill",
            issued_at="2026-07-29T10:32:00Z",
            nonce="concurrent-reader-drill-nonce",
            scope={
                "kind": "CONCURRENT_READER_DRILL",
                "policyVersion": BURN_IN_POLICY_V2,
                "burnInRunId": self.burn_in_run_id,
                "candidateBuildId": self.candidate_build_id,
                "fromBuildId": from_build,
                "toBuildId": to_build,
            },
            claim=claim,
            artifact_uri=uri,
            artifact_sha256=artifact_sha,
        )

    def _shadow_receipt(self) -> dict[str, object]:
        build_ids = [str(snapshot["buildId"]) for snapshot in self.snapshot_records]
        artifact = {
            "schema": "ark-kb-shadow-diff-disposition/v2",
            "burnInRunId": self.burn_in_run_id,
            "candidateBuildId": self.candidate_build_id,
            "status": "PASSED",
            "buildIds": build_ids,
            "corpusId": REPRESENTATIVE_CORPUS_ID,
            "corpusSha256": REPRESENTATIVE_CORPUS_SHA256,
            "caseCount": 2,
            "wrongAnswers": 0,
            "staleLeaks": 0,
            "candidateCompletions": 0,
            "undispositioned": 0,
            "command": "fixture-shadow-diff",
            "toolVersion": "automated-contract-fixture/v2",
            "startedAt": "2026-07-29T10:40:00Z",
            "completedAt": "2026-07-29T10:41:00Z",
            "dispositions": [
                {
                    "caseId": "fixture-shadow-001",
                    "outcome": "MATCH",
                    "rationale": "Test-only matching result.",
                },
                {
                    "caseId": "fixture-shadow-002",
                    "outcome": "ACCEPTED_DIFFERENCE",
                    "rationale": "Test-only reviewed difference.",
                },
            ],
        }
        uri, artifact_sha = self._write_artifact(
            "shadow/dispositions.json",
            artifact,
        )
        claim = {
            key: artifact[key]
            for key in (
                "status",
                "caseCount",
                "wrongAnswers",
                "staleLeaks",
                "candidateCompletions",
                "undispositioned",
                "command",
                "toolVersion",
                "startedAt",
                "completedAt",
            )
        }
        return _receipt(
            self.private_key,
            self.registry,
            receipt_id="shadow-diff-disposition",
            issued_at="2026-07-29T10:42:00Z",
            nonce="shadow-diff-disposition-nonce",
            scope={
                "kind": "SHADOW_DIFF_DISPOSITION",
                "policyVersion": BURN_IN_POLICY_V2,
                "burnInRunId": self.burn_in_run_id,
                "candidateBuildId": self.candidate_build_id,
                "buildIds": build_ids,
                "corpusId": REPRESENTATIVE_CORPUS_ID,
                "corpusSha256": REPRESENTATIVE_CORPUS_SHA256,
            },
            claim=claim,
            artifact_uri=uri,
            artifact_sha256=artifact_sha,
        )

    def refresh_top_approval(
        self,
        *,
        issued_at: str = "2026-07-29T11:00:00Z",
    ) -> None:
        bundle_uri = "artifact://bundle/burn-in-evidence.json"
        bundle_bytes = _write_json(
            self.artifact_root / "bundle" / "burn-in-evidence.json",
            self.bundle,
        )
        bundle_sha = _sha256(bundle_bytes)
        approval = _receipt(
            self.private_key,
            self.registry,
            receipt_id="burn-in-top-approval",
            issued_at=issued_at,
            nonce="burn-in-top-approval-nonce",
            scope={
                "kind": "BURN_IN_ATTESTATION",
                "policyVersion": BURN_IN_POLICY_V2,
                "burnInRunId": self.burn_in_run_id,
                "candidateBuildId": self.candidate_build_id,
                "previousBuildId": self.previous_build_id,
                "previousManifestSha256": self.previous_manifest_sha256,
            },
            claim={
                "status": "PASSED",
                "sealedSnapshotCount": 3,
                "incrementalScenarioCount": 12,
                "rollbackReceiptCount": 1,
                "concurrentReaderReceiptCount": 1,
                "shadowDispositionReceiptCount": 1,
            },
            artifact_uri=bundle_uri,
            artifact_sha256=bundle_sha,
        )
        self.attestation = {
            "schema": BURN_IN_ATTESTATION_V2_SCHEMA,
            "policyVersion": BURN_IN_POLICY_V2,
            "burnInRunId": self.burn_in_run_id,
            "candidateBuildId": self.candidate_build_id,
            "status": "PASSED",
            "evidenceBundleUri": bundle_uri,
            "evidenceBundleSha256": bundle_sha,
            "operatorApproval": approval,
        }

    def rewrite_receipt_artifact(
        self,
        receipt: dict[str, object],
        artifact: dict[str, object],
        *,
        claim_updates: Mapping[str, object] | None = None,
    ) -> None:
        payload = _receipt_payload(receipt)
        uri = str(payload["artifactUri"])
        assert uri.startswith("artifact://")
        artifact_bytes = _write_json(
            self.artifact_root / uri.removeprefix("artifact://"),
            artifact,
        )
        payload["artifactSha256"] = _sha256(artifact_bytes)
        if claim_updates:
            claim = payload["claim"]
            assert isinstance(claim, dict)
            claim.update(claim_updates)
        _resign(receipt, self.private_key)
        self.refresh_top_approval()

    def validate(self, **overrides: object):
        arguments: dict[str, object] = {
            "registry": self.registry,
            "expected_registry_sha256": self.registry["registryVersionSha256"],
            "artifact_root": self.artifact_root,
            "snapshot_root": self.snapshot_root,
            "expected_burn_in_run_id": self.burn_in_run_id,
            "expected_candidate_build_id": self.candidate_build_id,
            "expected_previous_build_id": self.previous_build_id,
            "expected_previous_manifest_sha256": (self.previous_manifest_sha256),
            "expected_representative_corpus_id": REPRESENTATIVE_CORPUS_ID,
            "expected_representative_corpus_sha256": (REPRESENTATIVE_CORPUS_SHA256),
            "expected_representative_case_ids": REPRESENTATIVE_CASE_IDS,
            "trust_context": TEST_ONLY,
            "verification_time": NOW,
        }
        arguments.update(overrides)
        return validate_burn_in_attestation_v2(
            self.attestation,
            **arguments,
        )


class BurnInV2Tests(unittest.TestCase):
    def test_schema_contracts_are_strict_and_cover_all_scenarios(self) -> None:
        attestation = json.loads(
            (
                PROJECT_ROOT / "schemas" / "kb_burn_in_attestation_v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        bundle = json.loads(
            (
                PROJECT_ROOT / "schemas" / "kb_burn_in_evidence_bundle_v2.schema.json"
            ).read_text(encoding="utf-8")
        )

        self.assertFalse(attestation["additionalProperties"])
        self.assertEqual(
            attestation["properties"]["schema"]["const"],
            BURN_IN_ATTESTATION_V2_SCHEMA,
        )
        self.assertIn("burnInRunId", attestation["required"])
        self.assertIn("candidateBuildId", attestation["required"])
        self.assertIn(
            "burnInRunId",
            attestation["$defs"]["topScope"]["required"],
        )
        self.assertFalse(bundle["additionalProperties"])
        self.assertIn("burnInRunId", bundle["required"])
        common_scope = bundle["$defs"]["signedReceiptPayload"]["properties"]["scope"]
        self.assertTrue(
            {"burnInRunId", "candidateBuildId"}.issubset(set(common_scope["required"]))
        )
        scenarios = bundle["properties"]["incrementalScenarioReceipts"]
        self.assertFalse(scenarios["additionalProperties"])
        self.assertEqual(
            set(scenarios["required"]),
            set(REQUIRED_INCREMENTAL_SCENARIOS),
        )
        self.assertEqual(
            bundle["properties"]["sealedSnapshots"]["minItems"],
            3,
        )
        self.assertEqual(
            bundle["properties"]["sealedSnapshots"]["maxItems"],
            3,
        )
        self.assertEqual(
            set(REQUIRED_INCREMENTAL_SCENARIOS),
            (
                set(PUBLISHED_INCREMENTAL_SCENARIOS)
                | set(NO_PUBLISH_INCREMENTAL_SCENARIOS)
                | {"concurrentReaders", "unchangedCacheHit"}
            ),
        )
        self.assertFalse(
            set(PUBLISHED_INCREMENTAL_SCENARIOS) & set(NO_PUBLISH_INCREMENTAL_SCENARIOS)
        )

    def test_test_only_bundle_validates_but_is_never_production_eligible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))

            result = fixture.validate()

        self.assertTrue(result.validated)
        self.assertFalse(result.production_eligible)
        self.assertEqual(result.trust_context, TEST_ONLY)
        self.assertEqual(result.burn_in_run_id, BURN_IN_RUN_ID)
        self.assertEqual(result.candidate_build_id, "fixture-ready-candidate")
        self.assertEqual(
            result.representative_corpus_id,
            REPRESENTATIVE_CORPUS_ID,
        )
        self.assertEqual(
            result.representative_corpus_sha256,
            REPRESENTATIVE_CORPUS_SHA256,
        )
        self.assertEqual(
            result.representative_case_ids,
            REPRESENTATIVE_CASE_IDS,
        )
        self.assertEqual(len(result.receipt_ids), 16)
        self.assertEqual(len(result.artifact_bytes_by_uri), 16)

    def test_test_only_registry_cannot_enter_production_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))

            with self.assertRaisesRegex(
                BurnInV2Error,
                "TEST_ONLY registry is not valid in PRODUCTION",
            ):
                fixture.validate(trust_context="PRODUCTION")

    def test_top_approval_requires_real_signature_after_payload_rehash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            approval = fixture.attestation["operatorApproval"]
            assert isinstance(approval, dict)
            payload = _receipt_payload(approval)
            claim = payload["claim"]
            assert isinstance(claim, dict)
            claim["sealedSnapshotCount"] = 4
            approval["signedPayloadSha256"] = signed_payload_sha256(payload)

            with self.assertRaisesRegex(
                BurnInV2Error,
                "signature verification failed",
            ):
                fixture.validate()

    def test_boolean_scenario_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            fixture.scenario_receipts["blueprintAdded"] = True  # type: ignore[assignment]
            fixture.refresh_top_approval()

            with self.assertRaisesRegex(
                BurnInV2Error,
                "blueprintAdded receipt must be an object",
            ):
                fixture.validate()

    def test_component_artifact_must_match_signed_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.scenario_receipts["blueprintAdded"]
            payload = _receipt_payload(receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["toolVersion"] = "different-but-signed-artifact/v2"
            fixture.rewrite_receipt_artifact(receipt, artifact)

            with self.assertRaisesRegex(
                BurnInV2Error,
                "artifact does not match signed claim",
            ):
                fixture.validate()

    def test_missing_detached_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            payload = _receipt_payload(fixture.scenario_receipts["blueprintDeleted"])
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            (fixture.artifact_root / uri).unlink()

            with self.assertRaisesRegex(
                BurnInV2Error,
                "artifact does not exist",
            ):
                fixture.validate()

    def test_receipt_id_or_nonce_replay_inside_bundle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            first = _receipt_payload(fixture.scenario_receipts["blueprintAdded"])
            second_receipt = fixture.scenario_receipts["blueprintDeleted"]
            second = _receipt_payload(second_receipt)
            second["receiptId"] = first["receiptId"]
            second["nonce"] = first["nonce"]
            _resign(second_receipt, fixture.private_key)
            fixture.refresh_top_approval()

            with self.assertRaisesRegex(
                BurnInV2Error,
                "receipt replay|nonce replay",
            ):
                fixture.validate()

    def test_top_scope_cannot_be_replayed_to_another_candidate_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))

            with self.assertRaisesRegex(
                BurnInV2Error,
                "run or candidate identity",
            ):
                fixture.validate(expected_candidate_build_id="different-candidate")

    def test_top_scope_cannot_be_replayed_to_another_burn_in_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))

            with self.assertRaisesRegex(
                BurnInV2Error,
                "run or candidate identity",
            ):
                fixture.validate(expected_burn_in_run_id="different-run")

    def test_component_receipt_cannot_cross_run_or_candidate(self) -> None:
        for identity_field, replacement in (
            ("burnInRunId", "different-run"),
            ("candidateBuildId", "different-candidate"),
        ):
            with self.subTest(identity_field=identity_field):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = BurnInFixture(Path(temporary))
                    receipt = fixture.scenario_receipts["blueprintAdded"]
                    payload = _receipt_payload(receipt)
                    scope = payload["scope"]
                    assert isinstance(scope, dict)
                    scope[identity_field] = replacement
                    _resign(receipt, fixture.private_key)
                    fixture.refresh_top_approval()

                    with self.assertRaisesRegex(
                        BurnInV2Error,
                        "scope does not match expected scope",
                    ):
                        fixture.validate()

    def test_component_artifact_cannot_cross_run_or_candidate(self) -> None:
        for identity_field, replacement in (
            ("burnInRunId", "different-run"),
            ("candidateBuildId", "different-candidate"),
        ):
            with self.subTest(identity_field=identity_field):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = BurnInFixture(Path(temporary))
                    receipt = fixture.scenario_receipts["blueprintAdded"]
                    payload = _receipt_payload(receipt)
                    uri = str(payload["artifactUri"]).removeprefix("artifact://")
                    artifact = json.loads(
                        (fixture.artifact_root / uri).read_text(encoding="utf-8")
                    )
                    artifact[identity_field] = replacement
                    fixture.rewrite_receipt_artifact(receipt, artifact)

                    with self.assertRaisesRegex(
                        BurnInV2Error,
                        "artifact identity is invalid",
                    ):
                        fixture.validate()

    def test_published_scenarios_require_unique_direct_child_transitions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            first_payload = _receipt_payload(
                fixture.scenario_receipts["blueprintModified"]
            )
            first_scope = first_payload["scope"]
            assert isinstance(first_scope, dict)
            receipt = fixture.scenario_receipts["blueprintAdded"]
            payload = _receipt_payload(receipt)
            scope = payload["scope"]
            assert isinstance(scope, dict)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            for field in ("baseBuildId", "resultBuildId"):
                scope[field] = first_scope[field]
                artifact[field] = first_scope[field]
            (
                _,
                previous_manifest_sha256,
                _,
                result_manifest_sha256,
            ) = fixture.success_transitions["blueprintModified"]
            artifact["previousManifestSha256"] = previous_manifest_sha256
            artifact["resultManifestSha256"] = result_manifest_sha256
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={
                    "previousManifestSha256": previous_manifest_sha256,
                    "resultManifestSha256": result_manifest_sha256,
                },
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "transition is reused",
            ):
                fixture.validate()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.scenario_receipts["blueprintAdded"]
            payload = _receipt_payload(receipt)
            scope = payload["scope"]
            assert isinstance(scope, dict)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            scope["resultBuildId"] = scope["baseBuildId"]
            artifact["resultBuildId"] = artifact["baseBuildId"]
            artifact["resultManifestSha256"] = artifact["previousManifestSha256"]
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={
                    "resultManifestSha256": artifact["resultManifestSha256"]
                },
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "must publish a distinct direct child",
            ):
                fixture.validate()

    def test_published_scenario_rejects_orphan_direct_child_transition(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            snapshots = fixture.snapshot_root / "snapshots"
            detached_base = {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": "detached-base",
                "generatedAt": "2026-07-29T09:50:00Z",
                "cutover": {
                    "mode": "shadow",
                    "defaultQuerySource": "legacy",
                },
            }
            detached_base_bytes = _write_json(
                snapshots / "detached-base" / "manifest.json",
                detached_base,
            )
            detached_base_sha = _sha256(detached_base_bytes)
            detached_result = {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": "detached-result",
                "generatedAt": "2026-07-29T09:51:00Z",
                "previousSnapshot": {
                    "buildId": "detached-base",
                    "manifestSha256": detached_base_sha,
                },
                "cutover": {
                    "mode": "shadow",
                    "defaultQuerySource": "legacy",
                },
            }
            detached_result_bytes = _write_json(
                snapshots / "detached-result" / "manifest.json",
                detached_result,
            )
            receipt = fixture.scenario_receipts["blueprintAdded"]
            payload = _receipt_payload(receipt)
            scope = payload["scope"]
            assert isinstance(scope, dict)
            scope["baseBuildId"] = "detached-base"
            scope["resultBuildId"] = "detached-result"
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["baseBuildId"] = "detached-base"
            artifact["resultBuildId"] = "detached-result"
            artifact["previousManifestSha256"] = detached_base_sha
            artifact["resultManifestSha256"] = _sha256(detached_result_bytes)
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={
                    "previousManifestSha256": detached_base_sha,
                    "resultManifestSha256": artifact["resultManifestSha256"],
                },
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "must publish a distinct direct child",
            ):
                fixture.validate()

    def test_no_publish_scenarios_must_leave_current_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.scenario_receipts["workerCrash"]
            payload = _receipt_payload(receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["published"] = True
            artifact["currentUnchanged"] = False
            artifact["pointerSwapsExercised"] = 1
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={
                    "published": True,
                    "currentUnchanged": False,
                    "pointerSwapsExercised": 1,
                },
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "workerCrash must not publish",
            ):
                fixture.validate()

    def test_unchanged_cache_hit_requires_same_build_and_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.scenario_receipts["unchangedCacheHit"]
            payload = _receipt_payload(receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["cacheHit"] = False
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={"cacheHit": False},
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "same-build cache hit",
            ):
                fixture.validate()

    def test_concurrent_readers_scenario_requires_adjacent_controlled_swap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.scenario_receipts["concurrentReaders"]
            payload = _receipt_payload(receipt)
            scope = payload["scope"]
            assert isinstance(scope, dict)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            first = fixture.snapshot_records[0]
            last = fixture.snapshot_records[2]
            scope["baseBuildId"] = first["buildId"]
            scope["resultBuildId"] = last["buildId"]
            artifact["baseBuildId"] = first["buildId"]
            artifact["resultBuildId"] = last["buildId"]
            artifact["previousManifestSha256"] = first["manifestSha256"]
            artifact["resultManifestSha256"] = last["manifestSha256"]
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={
                    "previousManifestSha256": first["manifestSha256"],
                    "resultManifestSha256": last["manifestSha256"],
                },
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "adjacent sealed snapshots",
            ):
                fixture.validate()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.scenario_receipts["concurrentReaders"]
            payload = _receipt_payload(receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["mixedBuildObservations"] = 1
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={"mixedBuildObservations": 1},
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "zero mixed",
            ):
                fixture.validate()

    def test_manifest_parent_chain_break_and_fork_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            broken = fixture.bundle["sealedSnapshots"]
            assert isinstance(broken, list)
            broken_entry = broken[1]
            assert isinstance(broken_entry, dict)
            broken_entry["previousManifestSha256"] = "0" * 64
            fixture.refresh_top_approval()
            with self.assertRaisesRegex(
                BurnInV2Error,
                "snapshot parent chain",
            ):
                fixture.validate()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            parent = fixture.snapshot_records[0]
            _write_json(
                fixture.snapshot_root / "snapshots" / "fixture-fork" / "manifest.json",
                {
                    "schema": "ark-kb-vnext-snapshot/v1",
                    "buildId": "fixture-fork",
                    "generatedAt": "2026-07-29T10:30:00Z",
                    "previousSnapshot": {
                        "buildId": parent["buildId"],
                        "manifestSha256": parent["manifestSha256"],
                    },
                },
            )
            with self.assertRaisesRegex(BurnInV2Error, "fork"):
                fixture.validate()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            _write_json(
                fixture.snapshot_root
                / "snapshots"
                / "fixture-invalid-parent"
                / "manifest.json",
                {
                    "schema": "ark-kb-vnext-snapshot/v1",
                    "buildId": "fixture-invalid-parent",
                    "generatedAt": "2026-07-29T10:30:00Z",
                    "previousSnapshot": {
                        "buildId": fixture.snapshot_records[0]["buildId"],
                        "manifestSha256": "0" * 64,
                    },
                },
            )
            with self.assertRaisesRegex(
                BurnInV2Error,
                "parent manifest SHA-256 is invalid",
            ):
                fixture.validate()

    def test_quality_report_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            build_id = str(fixture.snapshot_records[1]["buildId"])
            report = (
                fixture.snapshot_root
                / "snapshots"
                / build_id
                / "reports"
                / "quality_gates.json"
            )
            report.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(
                BurnInV2Error,
                "quality report SHA-256",
            ):
                fixture.validate()

    def test_rollback_pointer_claim_is_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            payload = _receipt_payload(fixture.rollback_receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["pointerAfterSha256"] = "0" * 64
            fixture.rewrite_receipt_artifact(
                fixture.rollback_receipt,
                artifact,
                claim_updates={"pointerAfterSha256": "0" * 64},
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "rollback pointer",
            ):
                fixture.validate()

    def test_rollback_must_start_at_tip_and_target_adjacent_predecessor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.rollback_receipt
            payload = _receipt_payload(receipt)
            scope = payload["scope"]
            assert isinstance(scope, dict)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            first = fixture.snapshot_records[0]
            second = fixture.snapshot_records[1]
            scope["fromBuildId"] = second["buildId"]
            scope["toBuildId"] = first["buildId"]
            artifact["fromBuildId"] = second["buildId"]
            artifact["toBuildId"] = first["buildId"]
            artifact["fromManifestSha256"] = second["manifestSha256"]
            artifact["toManifestSha256"] = first["manifestSha256"]
            artifact["pointerBeforeSha256"] = _sha256(
                _json_bytes(
                    {
                        "buildId": second["buildId"],
                        "snapshotRelativePath": (f"snapshots/{second['buildId']}"),
                    }
                )
            )
            artifact["pointerAfterSha256"] = _sha256(
                _json_bytes(
                    {
                        "buildId": first["buildId"],
                        "snapshotRelativePath": f"snapshots/{first['buildId']}",
                    }
                )
            )
            artifact["expectedCurrentBuildId"] = second["buildId"]
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={
                    field: artifact[field]
                    for field in (
                        "fromManifestSha256",
                        "toManifestSha256",
                        "pointerBeforeSha256",
                        "pointerAfterSha256",
                        "expectedCurrentBuildId",
                    )
                },
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "current chain tip",
            ):
                fixture.validate()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.rollback_receipt
            payload = _receipt_payload(receipt)
            scope = payload["scope"]
            assert isinstance(scope, dict)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            first = fixture.snapshot_records[0]
            scope["toBuildId"] = first["buildId"]
            artifact["toBuildId"] = first["buildId"]
            artifact["toManifestSha256"] = first["manifestSha256"]
            artifact["pointerAfterSha256"] = _sha256(
                _json_bytes(
                    {
                        "buildId": first["buildId"],
                        "snapshotRelativePath": f"snapshots/{first['buildId']}",
                    }
                )
            )
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={
                    "toManifestSha256": first["manifestSha256"],
                    "pointerAfterSha256": artifact["pointerAfterSha256"],
                },
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "adjacent predecessor",
            ):
                fixture.validate()

    def test_concurrent_reader_requires_real_swaps_and_no_mixed_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            payload = _receipt_payload(fixture.concurrent_receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["pointerSwapsExercised"] = 0
            fixture.rewrite_receipt_artifact(
                fixture.concurrent_receipt,
                artifact,
                claim_updates={"pointerSwapsExercised": 0},
            )
            with self.assertRaisesRegex(
                BurnInV2Error,
                "pointer swap",
            ):
                fixture.validate()

    def test_concurrent_reader_drill_requires_adjacent_sealed_builds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.concurrent_receipt
            payload = _receipt_payload(receipt)
            scope = payload["scope"]
            assert isinstance(scope, dict)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            first = fixture.snapshot_records[0]
            last = fixture.snapshot_records[2]
            scope["fromBuildId"] = first["buildId"]
            scope["toBuildId"] = last["buildId"]
            artifact["fromBuildId"] = first["buildId"]
            artifact["toBuildId"] = last["buildId"]
            artifact["observedBuildIds"] = [
                first["buildId"],
                last["buildId"],
            ]
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={
                    "observedBuildIds": artifact["observedBuildIds"],
                },
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "adjacent sealed snapshots",
            ):
                fixture.validate()

        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            payload = _receipt_payload(fixture.concurrent_receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["mixedBuildObservations"] = 1
            fixture.rewrite_receipt_artifact(
                fixture.concurrent_receipt,
                artifact,
                claim_updates={"mixedBuildObservations": 1},
            )
            with self.assertRaisesRegex(
                BurnInV2Error,
                "mixedBuildObservations",
            ):
                fixture.validate()

    def test_shadow_disposition_counts_are_recomputed_from_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            payload = _receipt_payload(fixture.shadow_receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["dispositions"][0]["outcome"] = "WRONG_ANSWER"
            artifact["wrongAnswers"] = 1
            fixture.rewrite_receipt_artifact(
                fixture.shadow_receipt,
                artifact,
                claim_updates={"wrongAnswers": 1},
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "wrongAnswers must be zero",
            ):
                fixture.validate()

    def test_shadow_corpus_is_bound_to_out_of_band_identity_and_sha(self) -> None:
        for overrides, message in (
            (
                {
                    "expected_representative_corpus_id": (
                        "different-representative-corpus"
                    )
                },
                "corpusId",
            ),
            (
                {"expected_representative_corpus_sha256": "0" * 64},
                "corpusSha256",
            ),
        ):
            with self.subTest(message=message):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = BurnInFixture(Path(temporary))

                    with self.assertRaisesRegex(BurnInV2Error, message):
                        fixture.validate(**overrides)

    def test_shadow_requires_exact_external_case_set_and_multiple_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))

            with self.assertRaisesRegex(
                BurnInV2Error,
                "at least two",
            ):
                fixture.validate(
                    expected_representative_case_ids=("fixture-shadow-001",)
                )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))

            with self.assertRaisesRegex(
                BurnInV2Error,
                "representative case set",
            ):
                fixture.validate(
                    expected_representative_case_ids=(
                        "fixture-shadow-001",
                        "unexpected-case",
                    )
                )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.shadow_receipt
            payload = _receipt_payload(receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            artifact["dispositions"] = artifact["dispositions"][:1]
            artifact["caseCount"] = 1
            fixture.rewrite_receipt_artifact(
                receipt,
                artifact,
                claim_updates={"caseCount": 1},
            )

            with self.assertRaisesRegex(
                BurnInV2Error,
                "representative case set",
            ):
                fixture.validate()

    def test_shadow_receipt_must_cover_all_three_sealed_builds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.shadow_receipt
            payload = _receipt_payload(receipt)
            scope = payload["scope"]
            assert isinstance(scope, dict)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact = json.loads(
                (fixture.artifact_root / uri).read_text(encoding="utf-8")
            )
            scope["buildIds"] = scope["buildIds"][:2]
            artifact["buildIds"] = artifact["buildIds"][:2]
            fixture.rewrite_receipt_artifact(receipt, artifact)

            with self.assertRaisesRegex(
                BurnInV2Error,
                "buildIds do not match sealed snapshots",
            ):
                fixture.validate()

    def test_component_receipts_must_predate_top_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            fixture.refresh_top_approval(issued_at="2026-07-29T10:00:00Z")

            with self.assertRaisesRegex(
                BurnInV2Error,
                "top approval must not predate",
            ):
                fixture.validate()

    def test_expired_or_revoked_test_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expired = BurnInFixture(
                Path(temporary),
                valid_until="2026-07-29T11:30:00Z",
            )
            with self.assertRaisesRegex(BurnInV2Error, "expired"):
                expired.validate()

        with tempfile.TemporaryDirectory() as temporary:
            revoked = BurnInFixture(
                Path(temporary),
                revoked_at="2026-07-29T11:30:00Z",
            )
            with self.assertRaisesRegex(BurnInV2Error, "revoked"):
                revoked.validate()

    def test_attestation_rejects_duplicate_json_keys(self) -> None:
        payload = b'{"schema":"one","schema":"two"}'

        with self.assertRaisesRegex(BurnInV2Error, "duplicate JSON key"):
            from blueprint_translator.kb_vnext.burn_in_v2 import (
                strict_json_object_from_bytes,
            )

            strict_json_object_from_bytes(payload, label="fixture")

    def test_test_only_oversized_json_integer_is_wrapped_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            receipt = fixture.scenario_receipts["blueprintAdded"]
            payload = _receipt_payload(receipt)
            uri = str(payload["artifactUri"]).removeprefix("artifact://")
            artifact_path = fixture.artifact_root / uri
            artifact_bytes = artifact_path.read_bytes()
            oversized = artifact_bytes.replace(
                b'"pointerSwapsExercised": 1',
                b'"pointerSwapsExercised": ' + (b"9" * 5000),
            )
            self.assertNotEqual(oversized, artifact_bytes)
            artifact_path.write_bytes(oversized)
            payload["artifactSha256"] = _sha256(oversized)
            _resign(receipt, fixture.private_key)
            fixture.refresh_top_approval()

            with self.assertRaisesRegex(
                BurnInV2Error,
                "unsupported JSON integer",
            ):
                fixture.validate()

    def test_plain_valid_v2_status_mapping_has_no_eligibility_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            fake = {
                "schema": BURN_IN_ATTESTATION_V2_SCHEMA,
                "policyVersion": BURN_IN_POLICY_V2,
                "status": "VALID_V2",
            }

            with self.assertRaisesRegex(
                BurnInV2Error,
                "attestation fields",
            ):
                validate_burn_in_attestation_v2(
                    fake,
                    registry=fixture.registry,
                    expected_registry_sha256=fixture.registry["registryVersionSha256"],
                    artifact_root=fixture.artifact_root,
                    snapshot_root=fixture.snapshot_root,
                    expected_burn_in_run_id=fixture.burn_in_run_id,
                    expected_candidate_build_id=fixture.candidate_build_id,
                    expected_previous_build_id=fixture.previous_build_id,
                    expected_previous_manifest_sha256=(
                        fixture.previous_manifest_sha256
                    ),
                    expected_representative_corpus_id=(REPRESENTATIVE_CORPUS_ID),
                    expected_representative_corpus_sha256=(
                        REPRESENTATIVE_CORPUS_SHA256
                    ),
                    expected_representative_case_ids=(REPRESENTATIVE_CASE_IDS),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_contract_inputs_are_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BurnInFixture(Path(temporary))
            before = deepcopy(fixture.attestation)

            fixture.validate()

            self.assertEqual(fixture.attestation, before)


if __name__ == "__main__":
    unittest.main()
