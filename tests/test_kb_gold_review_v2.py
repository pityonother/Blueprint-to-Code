from __future__ import annotations

import base64
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.gold_review import (  # noqa: E402
    build_review_pack,
)
from blueprint_translator.kb_vnext.gold_review_v2 import (  # noqa: E402
    BLOCKED_BY_INDEPENDENT_REVIEW,
    FULL_PACK_REVIEW_REQUIRED,
    SIGNED_V2_RECEIPTS_REQUIRED,
    GoldReviewV2Error,
    gold_review_scope_v2,
    validate_gold_review_set_v2,
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
CASE_ID = "automated-contract-case-001"
CASE_ID_2 = "automated-contract-case-002"
SOURCE_SHA256 = "a" * 64
REVISION_SHA256 = "b" * 64
# Synthetic TEST_ONLY binding; this is not a production author identity.
TEST_AUTHOR_FINGERPRINT = hashlib.sha256(
    b"automated-contract-author-test-only"
).hexdigest()
VALIDATE_CLI = PROJECT_ROOT / "scripts" / "validate_ark_kb_gold_reviews_v2.py"


def _public_key_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _registry_entry(
    private_key: Ed25519PrivateKey,
    *,
    signer_id: str,
    roles: tuple[str, ...],
) -> dict[str, object]:
    public_key = _public_key_bytes(private_key)
    entry: dict[str, object] = {
        "reviewerId": signer_id,
        "publicKeyAlgorithm": "Ed25519",
        "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
        "publicKeyFingerprint": public_key_fingerprint(public_key),
        "allowedRoles": list(roles),
        "validFrom": "2026-07-01T00:00:00Z",
        "validUntil": "2026-08-31T00:00:00Z",
        "revokedAt": None,
    }
    entry["registryEntrySha256"] = registry_entry_sha256(entry)
    return entry


def _registry(
    entries: list[dict[str, object]],
) -> dict[str, object]:
    registry: dict[str, object] = {
        "schema": "ark-kb-trusted-reviewer-registry/v2",
        "registryId": "ark-kb-automated-contract-fixtures",
        "registryVersion": "automated-contract-v1",
        "trustContext": TEST_ONLY,
        "generatedAt": "2026-07-29T10:00:00Z",
        "reviewers": entries,
    }
    registry["registryVersionSha256"] = registry_version_sha256(registry)
    return registry


def _pack(
    *,
    author_id: str = "automated-contract-pack-author",
    author_key_fingerprint: str = TEST_AUTHOR_FINGERPRINT,
    case_ids: tuple[str, ...] = (CASE_ID,),
) -> dict[str, object]:
    return build_review_pack(
        kind="query",
        author_id=author_id,
        author_key_fingerprint=author_key_fingerprint,
        seed="automated-contract-seed",
        selection_rule="AUTOMATED_CONTRACT_ONLY",
        source_manifest_sha256=SOURCE_SHA256,
        candidates=[
            {
                "caseId": case_id,
                "payload": {
                    "question": "What is the independently reviewed result?",
                    "entity": "/Game/Test/Asset.Asset",
                    "category": "FACT",
                    "requirements": {
                        "answerMode": "FACT",
                        "factTypes": ["ITEM_PROPERTY"],
                        "factNames": ["BaseItemWeight"],
                        "edgeTypes": [],
                        "requiresNative": False,
                        "requiresRuntime": False,
                        "requiresMapEvidence": False,
                    },
                    "evidenceUris": [
                        "bp://automated-contract/default/BaseItemWeight"
                    ],
                },
            }
            for case_id in case_ids
        ],
        created_at="2026-07-29T10:00:00Z",
        tool_version="ark-kb-gold-review-export/v1",
    )


def _write_artifact(
    root: Path,
    pack: dict[str, object],
    *,
    signer_id: str,
    role: str,
    round_number: int,
    reviewed_at: str,
    answer_value: int,
    name: str,
) -> tuple[str, str]:
    candidate = pack["candidates"][0]
    artifact = {
        "schema": "ark-kb-gold-review-artifact/v2",
        "packId": pack["packId"],
        "packSha256": pack["packSha256"],
        "caseId": candidate["caseId"],
        "candidateSha256": candidate["candidateSha256"],
        "reviewerId": signer_id,
        "reviewerRole": role,
        "round": round_number,
        "reviewedAt": reviewed_at,
        "verdict": "CONFIRMED",
        "answer": {
            "facts": [
                {
                    "factType": "ITEM_PROPERTY",
                    "factName": "BaseItemWeight",
                    "value": answer_value,
                }
            ]
        },
        "evidence": [
            {
                "uri": "bp://automated-contract/default/BaseItemWeight",
                "sourceRevisionSha256": REVISION_SHA256,
                "freshness": "FRESH",
            }
        ],
        "rationale": "Validated only as an automated TEST_ONLY contract.",
        "toolVersion": "automated-contract-review/v2",
    }
    artifact_bytes = (
        json.dumps(
            artifact,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    path = root / "reviews" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(artifact_bytes)
    return (
        f"artifact://reviews/{name}.json",
        hashlib.sha256(artifact_bytes).hexdigest(),
    )


def _receipt(
    private_key: Ed25519PrivateKey,
    registry: dict[str, object],
    pack: dict[str, object],
    *,
    signer_id: str,
    role: str,
    round_number: int,
    artifact_uri: str,
    artifact_sha256: str,
    reviewed_at: str,
    name: str,
    scope: dict[str, object] | None = None,
) -> dict[str, object]:
    candidate = pack["candidates"][0]
    payload: dict[str, object] = {
        "schema": "ark-kb-signed-receipt-payload/v2",
        "receiptId": f"automated-contract-receipt-{name}",
        "registryVersionSha256": registry["registryVersionSha256"],
        "trustContext": TEST_ONLY,
        "signerId": signer_id,
        "role": role,
        "issuedAt": reviewed_at,
        "nonce": f"automated-contract-nonce-{name}",
        "scope": copy.deepcopy(
            scope
            or gold_review_scope_v2(
                pack,
                candidate,
                round_number=round_number,
            )
        ),
        "artifactUri": artifact_uri,
        "artifactSha256": artifact_sha256,
        "claim": {
            "schema": "ark-kb-gold-review-claim/v2",
            "artifactSchema": "ark-kb-gold-review-artifact/v2",
        },
    }
    return {
        "schema": "ark-kb-signed-receipt-envelope/v2",
        "signatureAlgorithm": "Ed25519",
        "payload": payload,
        "signedPayloadSha256": signed_payload_sha256(payload),
        "signatureBase64": base64.b64encode(
            private_key.sign(canonical_json_bytes(payload))
        ).decode("ascii"),
    }


class GoldReviewV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        # All keys are ephemeral TEST_ONLY automated contract fixtures.
        self.key_a = Ed25519PrivateKey.generate()
        self.key_b = Ed25519PrivateKey.generate()
        self.key_c = Ed25519PrivateKey.generate()
        self.signer_a = "automated-contract-reviewer-a"
        self.signer_b = "automated-contract-reviewer-b"
        self.signer_c = "automated-contract-adjudicator-c"
        self.registry = _registry(
            [
                _registry_entry(
                    self.key_a,
                    signer_id=self.signer_a,
                    roles=("REVIEWER", "ADJUDICATOR"),
                ),
                _registry_entry(
                    self.key_b,
                    signer_id=self.signer_b,
                    roles=("REVIEWER",),
                ),
                _registry_entry(
                    self.key_c,
                    signer_id=self.signer_c,
                    roles=("ADJUDICATOR",),
                ),
            ]
        )
        self.pack = _pack()

    def _signed_review(
        self,
        root: Path,
        private_key: Ed25519PrivateKey,
        *,
        signer_id: str,
        role: str,
        round_number: int,
        answer_value: int,
        name: str,
        scope: dict[str, object] | None = None,
    ) -> dict[str, object]:
        reviewed_at = (
            f"2026-07-29T10:{round_number * 10:02d}:00Z"
        )
        artifact_uri, artifact_sha = _write_artifact(
            root,
            self.pack,
            signer_id=signer_id,
            role=role,
            round_number=round_number,
            reviewed_at=reviewed_at,
            answer_value=answer_value,
            name=name,
        )
        return _receipt(
            private_key,
            self.registry,
            self.pack,
            signer_id=signer_id,
            role=role,
            round_number=round_number,
            artifact_uri=artifact_uri,
            artifact_sha256=artifact_sha,
            reviewed_at=reviewed_at,
            name=name,
            scope=scope,
        )

    def _validate(
        self,
        receipts: list[dict[str, object]],
        root: Path,
        *,
        expected_registry_sha256: str | None = None,
        required_case_ids: tuple[str, ...] | None = None,
    ):
        return validate_gold_review_set_v2(
            self.pack,
            receipts,
            registry=self.registry,
            expected_registry_sha256=(
                expected_registry_sha256
                or str(self.registry["registryVersionSha256"])
            ),
            artifact_root=root,
            expected_pack_author_key_fingerprint=str(
                self.pack["authorKeyFingerprint"]
            ),
            required_case_ids=required_case_ids,
            trust_context=TEST_ONLY,
            verification_time=NOW,
        )

    def test_schemas_separate_receipt_envelope_and_artifact_contracts(
        self,
    ) -> None:
        receipt_schema = json.loads(
            (
                PROJECT_ROOT
                / "schemas"
                / "kb_gold_review_receipt_v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        artifact_schema = json.loads(
            (
                PROJECT_ROOT
                / "schemas"
                / "kb_gold_review_artifact_v2.schema.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            receipt_schema["properties"]["schema"]["const"],
            "ark-kb-signed-receipt-envelope/v2",
        )
        self.assertFalse(receipt_schema["additionalProperties"])
        self.assertEqual(
            set(receipt_schema["required"]),
            {
                "schema",
                "signatureAlgorithm",
                "payload",
                "signedPayloadSha256",
                "signatureBase64",
            },
        )
        self.assertEqual(
            receipt_schema["properties"]["payload"]["$ref"],
            "#/$defs/payload",
        )
        signature = receipt_schema["properties"]["signatureBase64"]
        self.assertEqual(signature["minLength"], 88)
        self.assertEqual(signature["maxLength"], 88)
        payload = receipt_schema["$defs"]["payload"]
        self.assertFalse(payload["additionalProperties"])
        self.assertEqual(
            set(payload["required"]),
            {
                "schema",
                "receiptId",
                "registryVersionSha256",
                "trustContext",
                "signerId",
                "role",
                "issuedAt",
                "nonce",
                "scope",
                "artifactUri",
                "artifactSha256",
                "claim",
            },
        )
        self.assertEqual(
            payload["properties"]["schema"]["const"],
            "ark-kb-signed-receipt-payload/v2",
        )
        scope = receipt_schema["$defs"]["signedScope"]
        self.assertFalse(scope["additionalProperties"])
        self.assertEqual(
            scope["properties"]["contract"]["const"],
            "ark-kb-gold-review/v2",
        )
        self.assertEqual(
            receipt_schema["$defs"]["signedClaim"]["properties"]["schema"][
                "const"
            ],
            "ark-kb-gold-review-claim/v2",
        )

        self.assertEqual(
            artifact_schema["properties"]["schema"]["const"],
            "ark-kb-gold-review-artifact/v2",
        )
        self.assertFalse(artifact_schema["additionalProperties"])
        self.assertEqual(
            set(artifact_schema["required"]),
            {
                "schema",
                "packId",
                "packSha256",
                "caseId",
                "candidateSha256",
                "reviewerId",
                "reviewerRole",
                "round",
                "reviewedAt",
                "verdict",
                "answer",
                "evidence",
                "rationale",
                "toolVersion",
            },
        )

    def test_missing_receipts_stays_blocked_without_inventing_reviewers(
        self,
    ) -> None:
        result = validate_gold_review_set_v2(
            self.pack,
            [],
            registry=None,
            expected_registry_sha256=None,
            artifact_root=None,
            trust_context=TEST_ONLY,
            verification_time=NOW,
        )

        self.assertEqual(result.status, BLOCKED_BY_INDEPENDENT_REVIEW)
        self.assertFalse(result.contract_complete)
        self.assertFalse(result.production_gold_eligible)
        self.assertEqual(result.review_count, 0)
        self.assertIn(
            f"TWO_INDEPENDENT_REVIEWS_REQUIRED:{CASE_ID}",
            result.gaps,
        )

    def test_v1_receipt_is_diagnostic_only_and_requires_signed_v2(
        self,
    ) -> None:
        author_id = "automated-contract-legacy-v1-pack-author"
        self.pack = _pack(
            author_id=author_id,
            author_key_fingerprint=f"automation:{author_id}",
        )
        result = validate_gold_review_set_v2(
            self.pack,
            [{"schema": "ark-kb-gold-review/v1"}],
            registry=None,
            expected_registry_sha256=None,
            artifact_root=None,
            trust_context=TEST_ONLY,
            verification_time=NOW,
        )

        self.assertEqual(result.status, SIGNED_V2_RECEIPTS_REQUIRED)
        self.assertFalse(result.contract_complete)
        self.assertFalse(result.production_gold_eligible)
        self.assertEqual(result.gaps, (SIGNED_V2_RECEIPTS_REQUIRED,))

    def test_two_independent_test_only_signers_complete_contract_but_not_gold(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipts = [
                self._signed_review(
                    root,
                    self.key_a,
                    signer_id=self.signer_a,
                    role="REVIEWER",
                    round_number=1,
                    answer_value=5,
                    name="a",
                ),
                self._signed_review(
                    root,
                    self.key_b,
                    signer_id=self.signer_b,
                    role="REVIEWER",
                    round_number=2,
                    answer_value=5,
                    name="b",
                ),
            ]

            first = self._validate(receipts, root)
            second = self._validate(list(reversed(receipts)), root)

        self.assertTrue(first.contract_complete)
        self.assertFalse(first.production_gold_eligible)
        self.assertEqual(first.status, BLOCKED_BY_INDEPENDENT_REVIEW)
        self.assertEqual(
            first.gaps,
            ("PRODUCTION_SIGNED_RECEIPTS_REQUIRED",),
        )
        self.assertEqual(first.reviewed_cases, 1)
        self.assertEqual(first.receipt_set_sha256, second.receipt_set_sha256)
        self.assertEqual(
            first.resolved_reviews[CASE_ID].public_key_fingerprint,
            public_key_fingerprint(_public_key_bytes(self.key_a)),
        )
        self.assertEqual(
            hashlib.sha256(
                first.resolved_reviews[CASE_ID].artifact_bytes
            ).hexdigest(),
            first.resolved_reviews[CASE_ID].artifact_sha256,
        )

    def test_required_case_subset_is_diagnostic_even_when_case_is_resolved(
        self,
    ) -> None:
        self.pack = _pack(case_ids=(CASE_ID, CASE_ID_2))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipts = [
                self._signed_review(
                    root,
                    self.key_a,
                    signer_id=self.signer_a,
                    role="REVIEWER",
                    round_number=1,
                    answer_value=5,
                    name="subset-a",
                ),
                self._signed_review(
                    root,
                    self.key_b,
                    signer_id=self.signer_b,
                    role="REVIEWER",
                    round_number=2,
                    answer_value=5,
                    name="subset-b",
                ),
            ]

            result = self._validate(
                receipts,
                root,
                required_case_ids=(CASE_ID,),
            )

        self.assertEqual(result.candidate_cases, 2)
        self.assertEqual(result.required_cases, 1)
        self.assertEqual(result.reviewed_cases, 1)
        self.assertFalse(result.contract_complete)
        self.assertFalse(result.production_gold_eligible)
        self.assertEqual(result.status, BLOCKED_BY_INDEPENDENT_REVIEW)
        self.assertIn(FULL_PACK_REVIEW_REQUIRED, result.gaps)

    def test_pack_author_cannot_supply_a_review_round(self) -> None:
        self.pack = _pack(author_id=self.signer_a.upper())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = self._signed_review(
                root,
                self.key_a,
                signer_id=self.signer_a,
                role="REVIEWER",
                round_number=1,
                answer_value=5,
                name="author",
            )

            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "author cannot review own case",
            ):
                self._validate([receipt], root)

    def test_pack_author_key_alias_cannot_supply_a_review_round(
        self,
    ) -> None:
        self.pack = _pack(
            author_id="automated-contract-distinct-author-id",
            author_key_fingerprint=public_key_fingerprint(
                _public_key_bytes(self.key_a)
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = self._signed_review(
                root,
                self.key_a,
                signer_id=self.signer_a,
                role="REVIEWER",
                round_number=1,
                answer_value=5,
                name="author-key-alias",
            )

            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "author cannot review own case",
            ):
                self._validate([receipt], root)

    def test_v2_receipts_require_out_of_band_pack_author_fingerprint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = self._signed_review(
                root,
                self.key_a,
                signer_id=self.signer_a,
                role="REVIEWER",
                round_number=1,
                answer_value=5,
                name="missing-author-key",
            )

            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "expected_pack_author_key_fingerprint is required out of band",
            ):
                validate_gold_review_set_v2(
                    self.pack,
                    [receipt],
                    registry=self.registry,
                    expected_registry_sha256=str(
                        self.registry["registryVersionSha256"]
                    ),
                    artifact_root=root,
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_out_of_band_author_fingerprint_must_be_strict_and_match_pack(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = self._signed_review(
                root,
                self.key_a,
                signer_id=self.signer_a,
                role="REVIEWER",
                round_number=1,
                answer_value=5,
                name="author-key-mismatch",
            )
            for fingerprint, error in (
                ("A" * 64, "lowercase SHA-256"),
                ("0" * 64, "does not match pack authorKeyFingerprint"),
            ):
                with self.subTest(fingerprint=fingerprint):
                    with self.assertRaisesRegex(GoldReviewV2Error, error):
                        validate_gold_review_set_v2(
                            self.pack,
                            [receipt],
                            registry=self.registry,
                            expected_registry_sha256=str(
                                self.registry["registryVersionSha256"]
                            ),
                            artifact_root=root,
                            expected_pack_author_key_fingerprint=fingerprint,
                            trust_context=TEST_ONLY,
                            verification_time=NOW,
                        )

    def test_legacy_automation_author_pack_is_diagnostic_only(
        self,
    ) -> None:
        author_id = "automated-contract-legacy-pack-author"
        self.pack = _pack(
            author_id=author_id,
            author_key_fingerprint=f"automation:{author_id}",
        )
        blocked = validate_gold_review_set_v2(
            self.pack,
            [],
            registry=None,
            expected_registry_sha256=None,
            artifact_root=None,
            trust_context=TEST_ONLY,
            verification_time=NOW,
        )
        self.assertEqual(blocked.status, BLOCKED_BY_INDEPENDENT_REVIEW)
        self.assertFalse(blocked.contract_complete)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = self._signed_review(
                root,
                self.key_a,
                signer_id=self.signer_a,
                role="REVIEWER",
                round_number=1,
                answer_value=5,
                name="legacy-author-placeholder",
            )
            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "pack authorKeyFingerprint must be a lowercase SHA-256",
            ):
                validate_gold_review_set_v2(
                    self.pack,
                    [receipt],
                    registry=self.registry,
                    expected_registry_sha256=str(
                        self.registry["registryVersionSha256"]
                    ),
                    artifact_root=root,
                    expected_pack_author_key_fingerprint=(
                        TEST_AUTHOR_FINGERPRINT
                    ),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_same_verified_signer_cannot_supply_both_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipts = [
                self._signed_review(
                    root,
                    self.key_a,
                    signer_id=self.signer_a,
                    role="REVIEWER",
                    round_number=1,
                    answer_value=5,
                    name="a1",
                ),
                self._signed_review(
                    root,
                    self.key_a,
                    signer_id=self.signer_a,
                    role="REVIEWER",
                    round_number=2,
                    answer_value=5,
                    name="a2",
                ),
            ]

            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "independent verified signer keys",
            ):
                self._validate(receipts, root)

    def test_disagreement_requires_distinct_signed_adjudicator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = [
                self._signed_review(
                    root,
                    self.key_a,
                    signer_id=self.signer_a,
                    role="REVIEWER",
                    round_number=1,
                    answer_value=5,
                    name="a",
                ),
                self._signed_review(
                    root,
                    self.key_b,
                    signer_id=self.signer_b,
                    role="REVIEWER",
                    round_number=2,
                    answer_value=7,
                    name="b",
                ),
            ]
            blocked = self._validate(primary, root)
            adjudicator = self._signed_review(
                root,
                self.key_c,
                signer_id=self.signer_c,
                role="ADJUDICATOR",
                round_number=3,
                answer_value=5,
                name="c",
            )
            complete = self._validate([*primary, adjudicator], root)

        self.assertFalse(blocked.contract_complete)
        self.assertIn(
            f"INDEPENDENT_ADJUDICATION_REQUIRED:{CASE_ID}",
            blocked.gaps,
        )
        self.assertTrue(complete.contract_complete)
        self.assertEqual(
            complete.resolved_reviews[CASE_ID].reviewer_id,
            self.signer_c,
        )

    def test_adjudicator_cannot_reuse_a_reviewer_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipts = [
                self._signed_review(
                    root,
                    self.key_a,
                    signer_id=self.signer_a,
                    role="REVIEWER",
                    round_number=1,
                    answer_value=5,
                    name="a",
                ),
                self._signed_review(
                    root,
                    self.key_b,
                    signer_id=self.signer_b,
                    role="REVIEWER",
                    round_number=2,
                    answer_value=7,
                    name="b",
                ),
                self._signed_review(
                    root,
                    self.key_a,
                    signer_id=self.signer_a,
                    role="ADJUDICATOR",
                    round_number=3,
                    answer_value=5,
                    name="a-adjudication",
                ),
            ]

            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "adjudicator must use a distinct verified signer key",
            ):
                self._validate(receipts, root)

    def test_scope_is_exactly_bound_to_pack_case_and_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self.pack["candidates"][0]
            wrong_scope = gold_review_scope_v2(
                self.pack,
                candidate,
                round_number=1,
            )
            wrong_scope["candidateSha256"] = "0" * 64
            receipts = [
                self._signed_review(
                    root,
                    self.key_a,
                    signer_id=self.signer_a,
                    role="REVIEWER",
                    round_number=1,
                    answer_value=5,
                    name="wrong-scope",
                    scope=wrong_scope,
                )
            ]

            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "scope does not match",
            ):
                self._validate(receipts, root)

    def test_artifact_tamper_and_rehashed_payload_without_resign_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = self._signed_review(
                root,
                self.key_a,
                signer_id=self.signer_a,
                role="REVIEWER",
                round_number=1,
                answer_value=5,
                name="tamper",
            )
            artifact_path = root / "reviews" / "tamper.json"
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            artifact["answer"]["facts"][0]["value"] = 999
            artifact_path.write_text(
                json.dumps(artifact, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            receipt["payload"]["artifactSha256"] = hashlib.sha256(
                artifact_path.read_bytes()
            ).hexdigest()
            receipt["signedPayloadSha256"] = signed_payload_sha256(
                receipt["payload"]
            )

            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "signature verification failed",
            ):
                self._validate([receipt], root)

    def test_expected_registry_sha_is_required_and_out_of_band(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = self._signed_review(
                root,
                self.key_a,
                signer_id=self.signer_a,
                role="REVIEWER",
                round_number=1,
                answer_value=5,
                name="registry-sha",
            )
            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "expected_registry_sha256 is required out of band",
            ):
                validate_gold_review_set_v2(
                    self.pack,
                    [receipt],
                    registry=self.registry,
                    expected_registry_sha256=None,
                    artifact_root=root,
                    expected_pack_author_key_fingerprint=(
                        TEST_AUTHOR_FINGERPRINT
                    ),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )
            with self.assertRaisesRegex(
                GoldReviewV2Error,
                "out-of-band registry SHA-256",
            ):
                self._validate(
                    [receipt],
                    root,
                    expected_registry_sha256="0" * 64,
                )

    def test_cli_without_receipts_reports_blocker_and_writes_no_gold(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "review_pack.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            before = hashlib.sha256(pack_path.read_bytes()).hexdigest()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_CLI),
                    "--pack",
                    str(pack_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

            after = hashlib.sha256(pack_path.read_bytes()).hexdigest()
        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], BLOCKED_BY_INDEPENDENT_REVIEW)
        self.assertFalse(result["productionGoldEligible"])
        self.assertFalse(result["productionGoldWritten"])
        self.assertEqual(before, after)
        self.assertNotIn("expected", completed.stdout.casefold())
        self.assertNotIn(
            "what is the independently reviewed result",
            completed.stdout.casefold(),
        )

    def test_cli_case_subset_is_diagnostic_and_cannot_exit_zero(self) -> None:
        subset_pack = _pack(case_ids=(CASE_ID, CASE_ID_2))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "review_pack.json"
            pack_path.write_text(
                json.dumps(subset_pack, ensure_ascii=False),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_CLI),
                    "--pack",
                    str(pack_path),
                    "--case-id",
                    CASE_ID,
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], BLOCKED_BY_INDEPENDENT_REVIEW)
        self.assertFalse(result["contractComplete"])
        self.assertFalse(result["productionGoldEligible"])
        self.assertIn(FULL_PACK_REVIEW_REQUIRED, result["gaps"])

    def test_cli_reports_v1_as_signed_v2_receipts_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "review_pack.json"
            receipt_path = root / "v1-review.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            receipt_path.write_text(
                json.dumps({"schema": "ark-kb-gold-review/v1"}),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_CLI),
                    "--pack",
                    str(pack_path),
                    "--receipts",
                    str(receipt_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], SIGNED_V2_RECEIPTS_REQUIRED)
        self.assertFalse(result["productionGoldEligible"])
        self.assertFalse(result["productionGoldWritten"])

    def test_cli_requires_registry_sha_as_an_explicit_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "review_pack.json"
            receipt_path = root / "v2-envelope.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            receipt_path.write_text(
                json.dumps(
                    {
                        "schema": "ark-kb-signed-receipt-envelope/v2",
                        "payload": {},
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_CLI),
                    "--pack",
                    str(pack_path),
                    "--receipts",
                    str(receipt_path),
                    "--registry-v2",
                    str(root / "external-registry.json"),
                    "--artifact-root",
                    str(root),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stderr)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("--expected-registry-sha256", result["error"])

    def test_cli_requires_pack_author_fingerprint_out_of_band(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "review_pack.json"
            receipt_path = root / "v2-envelope.json"
            registry_path = root / "test-only-registry.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            receipt_path.write_text(
                json.dumps(
                    {
                        "schema": "ark-kb-signed-receipt-envelope/v2",
                        "payload": {},
                    }
                ),
                encoding="utf-8",
            )
            registry_path.write_text(
                json.dumps(self.registry, ensure_ascii=False),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_CLI),
                    "--pack",
                    str(pack_path),
                    "--receipts",
                    str(receipt_path),
                    "--registry-v2",
                    str(registry_path),
                    "--expected-registry-sha256",
                    str(self.registry["registryVersionSha256"]),
                    "--artifact-root",
                    str(root),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stderr)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn(
            "--expected-pack-author-key-fingerprint",
            result["error"],
        )

    def test_cli_strict_loader_rejects_duplicate_pack_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "duplicate-pack.json"
            pack_path.write_text(
                '{"schema":"first","schema":"second"}',
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_CLI),
                    "--pack",
                    str(pack_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stderr)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("duplicate JSON key", result["error"])

    def test_cli_strict_loader_rejects_nonfinite_registry_number(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "review-pack.json"
            receipt_path = root / "receipt.json"
            registry_path = root / "nonfinite-registry.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            receipt_path.write_text(
                json.dumps(
                    {
                        "schema": "ark-kb-signed-receipt-envelope/v2",
                        "payload": {},
                    }
                ),
                encoding="utf-8",
            )
            registry_path.write_text(
                '{"schema":"ark-kb-trusted-reviewer-registry/v2",'
                '"generatedAt":NaN}',
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_CLI),
                    "--pack",
                    str(pack_path),
                    "--receipts",
                    str(receipt_path),
                    "--registry-v2",
                    str(registry_path),
                    "--expected-registry-sha256",
                    "0" * 64,
                    "--expected-pack-author-key-fingerprint",
                    TEST_AUTHOR_FINGERPRINT,
                    "--artifact-root",
                    str(root),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stderr)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("non-finite JSON number", result["error"])

    def test_cli_strict_loader_wraps_oversized_receipt_integer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "review-pack.json"
            receipt_path = root / "oversized-integer-receipt.json"
            pack_path.write_text(
                json.dumps(self.pack, ensure_ascii=False),
                encoding="utf-8",
            )
            receipt_path.write_text(
                '{"schema":"ark-kb-signed-receipt-envelope/v2",'
                '"payload":{"round":'
                + ("9" * 5000)
                + "}}",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_CLI),
                    "--pack",
                    str(pack_path),
                    "--receipts",
                    str(receipt_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stderr)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("strict UTF-8 JSON", result["error"])


if __name__ == "__main__":
    unittest.main()
