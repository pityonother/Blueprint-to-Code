from __future__ import annotations

import base64
import concurrent.futures
import copy
import hashlib
import json
import math
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

from blueprint_translator.kb_vnext.signed_receipts import (  # noqa: E402
    PRODUCTION,
    TEST_ONLY,
    ReceiptReplayGuard,
    SignedReceiptError,
    canonical_json_bytes,
    public_key_fingerprint,
    registry_entry_sha256,
    registry_version_sha256,
    signed_payload_sha256,
    validate_reviewer_registry,
    verify_signed_receipt,
)


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
EXPECTED_SCOPE = {
    "receiptType": "GOLD_REVIEW",
    "packId": "pack-001",
    "caseId": "case-001",
    "buildId": "build-001",
}


def _public_key_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _registry_entry(
    private_key: Ed25519PrivateKey,
    *,
    reviewer_id: str = "test-reviewer-001",
    allowed_roles: tuple[str, ...] = (
        "REVIEWER",
        "ADJUDICATOR",
        "BURN_IN_OPERATOR",
    ),
    valid_from: str = "2026-07-01T00:00:00Z",
    valid_until: str = "2026-08-31T00:00:00Z",
    revoked_at: str | None = None,
) -> dict[str, object]:
    public_key = _public_key_bytes(private_key)
    entry: dict[str, object] = {
        "reviewerId": reviewer_id,
        "publicKeyAlgorithm": "Ed25519",
        "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
        "publicKeyFingerprint": public_key_fingerprint(public_key),
        "allowedRoles": list(allowed_roles),
        "validFrom": valid_from,
        "validUntil": valid_until,
        "revokedAt": revoked_at,
    }
    entry["registryEntrySha256"] = registry_entry_sha256(entry)
    return entry


def _registry(
    private_key: Ed25519PrivateKey,
    *,
    reviewers: list[dict[str, object]] | None = None,
    trust_context: str = TEST_ONLY,
) -> dict[str, object]:
    registry: dict[str, object] = {
        "schema": "ark-kb-trusted-reviewer-registry/v2",
        "registryId": "ark-kb-test-reviewers",
        "registryVersion": "test-v1",
        "trustContext": trust_context,
        "generatedAt": "2026-07-29T10:00:00Z",
        "reviewers": (
            reviewers
            if reviewers is not None
            else [_registry_entry(private_key)]
        ),
    }
    registry["registryVersionSha256"] = registry_version_sha256(registry)
    return registry


def _receipt(
    private_key: Ed25519PrivateKey,
    registry: dict[str, object],
    *,
    artifact_uri: str,
    artifact_sha256: str,
    receipt_id: str = "receipt-test-001",
    nonce: str = "nonce-test-001",
    role: str = "REVIEWER",
    scope: dict[str, object] | None = None,
    signer_id: str = "test-reviewer-001",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "ark-kb-signed-receipt-payload/v2",
        "receiptId": receipt_id,
        "registryVersionSha256": registry["registryVersionSha256"],
        "trustContext": TEST_ONLY,
        "signerId": signer_id,
        "role": role,
        "issuedAt": "2026-07-29T11:00:00Z",
        "nonce": nonce,
        "scope": copy.deepcopy(scope or EXPECTED_SCOPE),
        "artifactUri": artifact_uri,
        "artifactSha256": artifact_sha256,
        "claim": {
            "decision": "CONFIRMED",
            "rationale": "Verified from the bound test-only artifact.",
        },
    }
    signature = private_key.sign(canonical_json_bytes(payload))
    return {
        "schema": "ark-kb-signed-receipt-envelope/v2",
        "signatureAlgorithm": "Ed25519",
        "payload": payload,
        "signedPayloadSha256": signed_payload_sha256(payload),
        "signatureBase64": base64.b64encode(signature).decode("ascii"),
    }


class SignedReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        # Ephemeral TEST_ONLY keys are generated in memory and never persisted.
        self.private_key = Ed25519PrivateKey.generate()
        self.registry = _registry(self.private_key)

    def _artifact(self, root: Path) -> tuple[str, str]:
        artifact_path = root / "proofs" / "review.json"
        artifact_path.parent.mkdir(parents=True)
        artifact_bytes = b'{"result":"test-only"}\n'
        artifact_path.write_bytes(artifact_bytes)
        return (
            "artifact://proofs/review.json",
            hashlib.sha256(artifact_bytes).hexdigest(),
        )

    def test_canonical_json_is_stable_and_rejects_floats(self) -> None:
        left = {"z": "雪", "a": [3, {"x": True}]}
        right = {"a": [3, {"x": True}], "z": "雪"}

        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(
            canonical_json_bytes(left),
            '{"a":[3,{"x":true}],"z":"雪"}'.encode(),
        )
        for value in (1.0, 1e-7, -0.0, math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(
                SignedReceiptError,
                "floating-point",
            ):
                canonical_json_bytes({"notCanonical": value})

    def test_registry_schema_declares_v2_identity_contract(self) -> None:
        schema_path = (
            PROJECT_ROOT
            / "schemas"
            / "kb_trusted_reviewer_registry_v2.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(
            schema["properties"]["schema"]["const"],
            "ark-kb-trusted-reviewer-registry/v2",
        )
        required = set(schema["$defs"]["reviewer"]["required"])
        self.assertTrue(
            {
                "reviewerId",
                "publicKeyAlgorithm",
                "publicKeyBase64",
                "publicKeyFingerprint",
                "allowedRoles",
                "validFrom",
                "validUntil",
                "revokedAt",
                "registryEntrySha256",
            }.issubset(required)
        )

    def test_test_only_registry_requires_explicit_test_context(self) -> None:
        expected_sha = self.registry["registryVersionSha256"]

        with self.assertRaisesRegex(
            SignedReceiptError,
            "TEST_ONLY registry is not valid in PRODUCTION",
        ):
            validate_reviewer_registry(
                self.registry,
                expected_registry_sha256=expected_sha,
            )

        validated = validate_reviewer_registry(
            self.registry,
            expected_registry_sha256=expected_sha,
            trust_context=TEST_ONLY,
            verification_time=NOW,
        )
        self.assertEqual(validated.version_sha256, expected_sha)
        self.assertEqual(validated.trust_context, TEST_ONLY)

    def test_registry_sha_must_be_supplied_out_of_band(self) -> None:
        with self.assertRaises(TypeError):
            validate_reviewer_registry(  # type: ignore[call-arg]
                self.registry,
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

        with self.assertRaisesRegex(
            SignedReceiptError,
            "out-of-band registry SHA-256",
        ):
            validate_reviewer_registry(
                self.registry,
                expected_registry_sha256="f" * 64,
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

    def test_registry_rejects_entry_or_version_hash_tampering(self) -> None:
        tampered_entry = copy.deepcopy(self.registry)
        tampered_entry["reviewers"][0]["allowedRoles"] = ["REVIEWER"]
        with self.assertRaisesRegex(
            SignedReceiptError,
            "registryEntrySha256",
        ):
            validate_reviewer_registry(
                tampered_entry,
                expected_registry_sha256=self.registry[
                    "registryVersionSha256"
                ],
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

        tampered_version = copy.deepcopy(self.registry)
        tampered_version["registryVersion"] = "test-v2"
        with self.assertRaisesRegex(
            SignedReceiptError,
            "registryVersionSha256",
        ):
            validate_reviewer_registry(
                tampered_version,
                expected_registry_sha256=self.registry[
                    "registryVersionSha256"
                ],
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

    def test_registry_rejects_duplicate_id_and_key_alias(self) -> None:
        duplicate_id_key = Ed25519PrivateKey.generate()
        duplicate_id = _registry_entry(
            duplicate_id_key,
            reviewer_id="test-reviewer-001",
        )
        registry = _registry(
            self.private_key,
            reviewers=[
                _registry_entry(self.private_key),
                duplicate_id,
            ],
        )
        with self.assertRaisesRegex(
            SignedReceiptError,
            "duplicate reviewerId",
        ):
            validate_reviewer_registry(
                registry,
                expected_registry_sha256=registry[
                    "registryVersionSha256"
                ],
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

        key_alias = _registry_entry(
            self.private_key,
            reviewer_id="test-reviewer-alias",
        )
        registry = _registry(
            self.private_key,
            reviewers=[
                _registry_entry(self.private_key),
                key_alias,
            ],
        )
        with self.assertRaisesRegex(
            SignedReceiptError,
            "public key alias",
        ):
            validate_reviewer_registry(
                registry,
                expected_registry_sha256=registry[
                    "registryVersionSha256"
                ],
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

    def test_registry_rejects_fingerprint_mismatch_and_bad_validity(self) -> None:
        fingerprint_mismatch = copy.deepcopy(self.registry)
        fingerprint_mismatch["reviewers"][0][
            "publicKeyFingerprint"
        ] = "0" * 64
        fingerprint_mismatch["reviewers"][0][
            "registryEntrySha256"
        ] = registry_entry_sha256(fingerprint_mismatch["reviewers"][0])
        fingerprint_mismatch[
            "registryVersionSha256"
        ] = registry_version_sha256(fingerprint_mismatch)
        with self.assertRaisesRegex(
            SignedReceiptError,
            "publicKeyFingerprint",
        ):
            validate_reviewer_registry(
                fingerprint_mismatch,
                expected_registry_sha256=fingerprint_mismatch[
                    "registryVersionSha256"
                ],
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

        invalid_window_entry = _registry_entry(
            self.private_key,
            valid_from="2026-09-01T00:00:00Z",
            valid_until="2026-08-01T00:00:00Z",
        )
        invalid_window = _registry(
            self.private_key,
            reviewers=[invalid_window_entry],
        )
        with self.assertRaisesRegex(
            SignedReceiptError,
            "validUntil must be later",
        ):
            validate_reviewer_registry(
                invalid_window,
                expected_registry_sha256=invalid_window[
                    "registryVersionSha256"
                ],
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

    def test_valid_receipt_verifies_signature_scope_artifact_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)
            receipt = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
            )
            replay_guard: set[str] = set()

            verified = verify_signed_receipt(
                receipt,
                registry=self.registry,
                expected_registry_sha256=self.registry[
                    "registryVersionSha256"
                ],
                expected_scope=EXPECTED_SCOPE,
                expected_role="REVIEWER",
                artifact_root=artifact_root,
                replay_guard=replay_guard,
                trust_context=TEST_ONLY,
                verification_time=NOW,
            )

            self.assertEqual(verified.receipt_id, "receipt-test-001")
            self.assertEqual(verified.signer_id, "test-reviewer-001")
            self.assertEqual(
                verified.public_key_fingerprint,
                self.registry["reviewers"][0]["publicKeyFingerprint"],
            )
            self.assertEqual(verified.role, "REVIEWER")
            self.assertEqual(verified.claim["decision"], "CONFIRMED")
            self.assertEqual(verified.artifact_sha256, artifact_sha)
            self.assertEqual(
                hashlib.sha256(verified.artifact_bytes).hexdigest(),
                verified.artifact_sha256,
            )
            self.assertEqual(len(replay_guard), 2)

            verified.artifact_path.write_bytes(b"mutated-after-verification")
            self.assertEqual(
                hashlib.sha256(verified.artifact_bytes).hexdigest(),
                verified.artifact_sha256,
            )
            self.assertNotEqual(
                hashlib.sha256(verified.artifact_path.read_bytes()).hexdigest(),
                verified.artifact_sha256,
            )
            receipt["payload"]["claim"]["decision"] = "REJECTED"
            self.assertEqual(verified.claim["decision"], "CONFIRMED")
            with self.assertRaises(TypeError):
                verified.claim["decision"] = "REJECTED"

    def test_receipt_rejects_tampering_even_if_payload_hash_is_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)
            receipt = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
            )
            receipt["payload"]["claim"]["decision"] = "REJECTED"
            receipt["signedPayloadSha256"] = signed_payload_sha256(
                receipt["payload"]
            )

            with self.assertRaisesRegex(
                SignedReceiptError,
                "Ed25519 signature",
            ):
                verify_signed_receipt(
                    receipt,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_receipt_rejects_noncanonical_base64_pad_bits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)
            receipt = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
            )
            signature = receipt["signatureBase64"]
            self.assertIsInstance(signature, str)
            assert isinstance(signature, str)
            alphabet = (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz"
                "0123456789+/"
            )
            significant_index = len(signature.rstrip("=")) - 1
            old_value = alphabet.index(signature[significant_index])
            new_value = (old_value & 0b110000) | ((old_value + 1) & 0b001111)
            malleable_signature = (
                signature[:significant_index]
                + alphabet[new_value]
                + signature[significant_index + 1 :]
            )
            self.assertNotEqual(signature, malleable_signature)
            self.assertEqual(
                base64.b64decode(signature),
                base64.b64decode(malleable_signature),
            )
            receipt["signatureBase64"] = malleable_signature

            with self.assertRaisesRegex(
                SignedReceiptError,
                "canonical base64",
            ):
                verify_signed_receipt(
                    receipt,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_receipt_rejects_hash_mismatch_and_unknown_signer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)
            receipt = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
            )
            receipt["signedPayloadSha256"] = "0" * 64
            with self.assertRaisesRegex(
                SignedReceiptError,
                "signedPayloadSha256",
            ):
                verify_signed_receipt(
                    receipt,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_receipt_payload_requires_signed_v2_domain_separator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)
            receipt = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
            )
            receipt["payload"]["schema"] = (
                "untrusted-cross-protocol-payload/v1"
            )
            receipt["signedPayloadSha256"] = signed_payload_sha256(
                receipt["payload"]
            )
            receipt["signatureBase64"] = base64.b64encode(
                self.private_key.sign(
                    canonical_json_bytes(receipt["payload"])
                )
            ).decode("ascii")

            with self.assertRaisesRegex(
                SignedReceiptError,
                "payload schema must be v2",
            ):
                verify_signed_receipt(
                    receipt,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

            unknown = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
                signer_id="unregistered-alias",
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "unregistered signerId",
            ):
                verify_signed_receipt(
                    unknown,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_receipt_rejects_disallowed_or_unexpected_role(self) -> None:
        reviewer_only = _registry_entry(
            self.private_key,
            allowed_roles=("REVIEWER",),
        )
        registry = _registry(
            self.private_key,
            reviewers=[reviewer_only],
        )
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)
            receipt = _receipt(
                self.private_key,
                registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
                role="BURN_IN_OPERATOR",
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "role is not allowed",
            ):
                verify_signed_receipt(
                    receipt,
                    registry=registry,
                    expected_registry_sha256=registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="BURN_IN_OPERATOR",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

            reviewer_receipt = _receipt(
                self.private_key,
                registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "does not match expected role",
            ):
                verify_signed_receipt(
                    reviewer_receipt,
                    registry=registry,
                    expected_registry_sha256=registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="ADJUDICATOR",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_receipt_rejects_expired_or_revoked_key(self) -> None:
        cases = (
            (
                _registry_entry(
                    self.private_key,
                    valid_until="2026-07-28T00:00:00Z",
                ),
                "expired",
            ),
            (
                _registry_entry(
                    self.private_key,
                    revoked_at="2026-07-29T10:30:00Z",
                ),
                "revoked",
            ),
        )
        for entry, expected_message in cases:
            with self.subTest(expected_message):
                registry = _registry(
                    self.private_key,
                    reviewers=[entry],
                )
                with tempfile.TemporaryDirectory() as temporary:
                    artifact_root = Path(temporary)
                    artifact_uri, artifact_sha = self._artifact(artifact_root)
                    receipt = _receipt(
                        self.private_key,
                        registry,
                        artifact_uri=artifact_uri,
                        artifact_sha256=artifact_sha,
                    )
                    with self.assertRaisesRegex(
                        SignedReceiptError,
                        expected_message,
                    ):
                        verify_signed_receipt(
                            receipt,
                            registry=registry,
                            expected_registry_sha256=registry[
                                "registryVersionSha256"
                            ],
                            expected_scope=EXPECTED_SCOPE,
                            expected_role="REVIEWER",
                            artifact_root=artifact_root,
                            replay_guard=set(),
                            trust_context=TEST_ONLY,
                            verification_time=NOW,
                        )

    def test_receipt_rejects_scope_replay_and_duplicate_nonce_or_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)
            receipt = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "scope does not match",
            ):
                verify_signed_receipt(
                    receipt,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope={
                        **EXPECTED_SCOPE,
                        "caseId": "different-case",
                    },
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

            replay_guard: set[str] = set()
            common = {
                "registry": self.registry,
                "expected_registry_sha256": self.registry[
                    "registryVersionSha256"
                ],
                "expected_scope": EXPECTED_SCOPE,
                "expected_role": "REVIEWER",
                "artifact_root": artifact_root,
                "replay_guard": replay_guard,
                "trust_context": TEST_ONLY,
                "verification_time": NOW,
            }
            verify_signed_receipt(receipt, **common)
            with self.assertRaisesRegex(
                SignedReceiptError,
                "receipt replay",
            ):
                verify_signed_receipt(receipt, **common)

            same_nonce = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
                receipt_id="receipt-test-002",
                nonce="nonce-test-001",
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "nonce replay",
            ):
                verify_signed_receipt(same_nonce, **common)

            same_id = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
                receipt_id="receipt-test-001",
                nonce="nonce-test-002",
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "receipt replay",
            ):
                verify_signed_receipt(same_id, **common)

    def test_receipt_rejects_missing_changed_or_unsafe_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)

            missing = _receipt(
                self.private_key,
                self.registry,
                artifact_uri="artifact://proofs/missing.json",
                artifact_sha256=artifact_sha,
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "artifact does not exist",
            ):
                verify_signed_receipt(
                    missing,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

            changed = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256="0" * 64,
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "artifactSha256",
            ):
                verify_signed_receipt(
                    changed,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

            unsafe = _receipt(
                self.private_key,
                self.registry,
                artifact_uri="artifact://../outside.json",
                artifact_sha256=artifact_sha,
            )
            with self.assertRaisesRegex(
                SignedReceiptError,
                "safe relative artifact URI",
            ):
                verify_signed_receipt(
                    unsafe,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=set(),
                    trust_context=TEST_ONLY,
                    verification_time=NOW,
                )

    def test_production_context_does_not_accept_test_only_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            artifact_uri, artifact_sha = self._artifact(artifact_root)
            receipt = _receipt(
                self.private_key,
                self.registry,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
            )

            with self.assertRaisesRegex(
                SignedReceiptError,
                "TEST_ONLY registry is not valid in PRODUCTION",
            ):
                verify_signed_receipt(
                    receipt,
                    registry=self.registry,
                    expected_registry_sha256=self.registry[
                        "registryVersionSha256"
                    ],
                    expected_scope=EXPECTED_SCOPE,
                    expected_role="REVIEWER",
                    artifact_root=artifact_root,
                    replay_guard=ReceiptReplayGuard(),
                    verification_time=NOW,
                )

    def test_production_requires_atomic_replay_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                SignedReceiptError,
                "production replay_guard",
            ):
                verify_signed_receipt(
                    {},
                    registry={},
                    expected_registry_sha256="0" * 64,
                    expected_scope={},
                    expected_role="REVIEWER",
                    artifact_root=Path(temporary),
                    replay_guard=set(),
                    trust_context=PRODUCTION,
                    verification_time=NOW,
                )

        guard = ReceiptReplayGuard()
        keys = ("receipt:test-bundle:receipt-001", "nonce:test-key:nonce-001")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(guard.claim_many, (keys, keys)))
        self.assertEqual(results.count(frozenset()), 1)
        self.assertEqual(results.count(frozenset(keys)), 1)
        self.assertEqual(len(guard), 2)


if __name__ == "__main__":
    unittest.main()
