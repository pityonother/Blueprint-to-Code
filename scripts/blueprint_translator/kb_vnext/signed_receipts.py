"""Fail-closed Ed25519 verification for artifact-bound v2 receipts.

This module deliberately does not create reviewer identities, generate keys,
or sign production receipts.  It only validates a caller-supplied registry,
receipt envelope, detached artifact, and replay set.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import threading
from collections.abc import Mapping, MutableSet
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)


PRODUCTION = "PRODUCTION"
TEST_ONLY = "TEST_ONLY"

_TRUST_CONTEXTS = frozenset({PRODUCTION, TEST_ONLY})
_ROLES = frozenset({"REVIEWER", "ADJUDICATOR", "BURN_IN_OPERATOR"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REGISTRY_KEYS = frozenset(
    {
        "schema",
        "registryId",
        "registryVersion",
        "trustContext",
        "generatedAt",
        "reviewers",
        "registryVersionSha256",
    }
)
_REGISTRY_ENTRY_KEYS = frozenset(
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
    }
)
_ENVELOPE_KEYS = frozenset(
    {
        "schema",
        "signatureAlgorithm",
        "payload",
        "signedPayloadSha256",
        "signatureBase64",
    }
)
_PAYLOAD_KEYS = frozenset(
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
    }
)


class SignedReceiptError(ValueError):
    """Raised when a registry or signed receipt fails closed."""


class ReceiptReplayGuard:
    """Atomically de-duplicate receipts inside one bundle validation session.

    A fresh guard is intentionally valid for re-validating the same immutable
    bundle. Cross-pack/case/build reuse is rejected by the signed exact scope;
    callers must reuse one guard for every receipt counted in a single bundle.
    """

    def __init__(self) -> None:
        self._keys: set[str] = set()
        self._lock = threading.Lock()

    def claim_many(self, keys: tuple[str, ...]) -> frozenset[str]:
        """Claim all keys together or return the conflicting keys."""

        with self._lock:
            conflicts = frozenset(key for key in keys if key in self._keys)
            if conflicts:
                return conflicts
            self._keys.update(keys)
            return frozenset()

    def __len__(self) -> int:
        with self._lock:
            return len(self._keys)


@dataclass(frozen=True)
class ValidatedReviewer:
    """One validated public identity from a trusted registry."""

    reviewer_id: str
    public_key: Ed25519PublicKey
    public_key_fingerprint: str
    allowed_roles: frozenset[str]
    valid_from: datetime
    valid_until: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class ValidatedReviewerRegistry:
    """Validated registry metadata and its reviewer lookup."""

    registry_id: str
    registry_version: str
    version_sha256: str
    trust_context: str
    reviewers: Mapping[str, ValidatedReviewer]


@dataclass(frozen=True)
class VerifiedSignedReceipt:
    """Security-relevant result of a successful receipt verification.

    Consumers must use ``artifact_bytes`` rather than reopening the mutable
    audit path. The byte string is the exact content covered by
    ``artifact_sha256`` during verification.
    """

    receipt_id: str
    signer_id: str
    role: str
    nonce: str
    issued_at: datetime
    registry_version_sha256: str
    signed_payload_sha256: str
    scope: Mapping[str, object]
    claim: Mapping[str, object]
    artifact_uri: str
    artifact_path: Path
    artifact_sha256: str
    artifact_bytes: bytes


def _validate_json_value(value: object, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise SignedReceiptError(
            f"{path} must not contain floating-point values; "
            "use an integer or decimal string in signed canonical JSON"
        )
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise SignedReceiptError(
                    f"{path} must use string JSON object keys"
                )
            _validate_json_value(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_value(child, path=f"{path}[{index}]")
        return
    raise SignedReceiptError(f"{path} must be canonical JSON data")


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes for signed content."""

    _validate_json_value(value)
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SignedReceiptError(
            "content must be canonical JSON data"
        ) from error


def _deep_freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return tuple(_deep_freeze_json(child) for child in value)
    return value


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _without(
    value: Mapping[str, object],
    field: str,
) -> dict[str, object]:
    return {key: child for key, child in value.items() if key != field}


def registry_entry_sha256(entry: Mapping[str, object]) -> str:
    """Hash an identity entry, excluding its self-referential hash field."""

    return _sha256_json(_without(entry, "registryEntrySha256"))


def registry_version_sha256(registry: Mapping[str, object]) -> str:
    """Hash a registry version, excluding its self-referential hash field."""

    return _sha256_json(_without(registry, "registryVersionSha256"))


def signed_payload_sha256(payload: Mapping[str, object]) -> str:
    """Hash exactly the payload covered by the Ed25519 signature."""

    return _sha256_json(payload)


def public_key_fingerprint(public_key_bytes: bytes) -> str:
    """Return the SHA-256 fingerprint of a raw Ed25519 public key."""

    if not isinstance(public_key_bytes, bytes) or len(public_key_bytes) != 32:
        raise SignedReceiptError(
            "Ed25519 public key must be exactly 32 raw bytes"
        )
    return hashlib.sha256(public_key_bytes).hexdigest()


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SignedReceiptError(f"{field} must be a non-empty string")
    if value != value.strip() or any(
        character in value for character in ("\x00", "\r", "\n")
    ):
        raise SignedReceiptError(
            f"{field} must not contain surrounding or control whitespace"
        )
    return value


def _required_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise SignedReceiptError(
            f"{field} must be a lowercase SHA-256 hex digest"
        )
    return value


def _timestamp(value: object, *, field: str) -> datetime:
    text = _required_text(value, field=field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SignedReceiptError(
            f"{field} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise SignedReceiptError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _verification_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise SignedReceiptError(
            "verification_time must include a timezone"
        )
    return value.astimezone(timezone.utc)


def _exact_keys(
    value: Mapping[str, object],
    expected: frozenset[str],
    *,
    field: str,
) -> None:
    observed = frozenset(value)
    if observed == expected:
        return
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    details: list[str] = []
    if missing:
        details.append("missing=" + ",".join(missing))
    if unexpected:
        details.append("unexpected=" + ",".join(unexpected))
    raise SignedReceiptError(
        f"{field} fields are invalid ({'; '.join(details)})"
    )


def _decode_base64(
    value: object,
    *,
    field: str,
    expected_length: int,
) -> bytes:
    text = _required_text(value, field=field)
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SignedReceiptError(f"{field} must be valid base64") from error
    if len(decoded) != expected_length:
        raise SignedReceiptError(
            f"{field} must decode to exactly {expected_length} bytes"
        )
    if base64.b64encode(decoded).decode("ascii") != text:
        raise SignedReceiptError(f"{field} must use canonical base64 encoding")
    return decoded


def _trust_context(value: object, *, field: str) -> str:
    context = _required_text(value, field=field)
    if context not in _TRUST_CONTEXTS:
        raise SignedReceiptError(
            f"{field} must be PRODUCTION or TEST_ONLY"
        )
    return context


def validate_reviewer_registry(
    registry: Mapping[str, object],
    *,
    expected_registry_sha256: str,
    trust_context: str = PRODUCTION,
    verification_time: datetime | None = None,
) -> ValidatedReviewerRegistry:
    """Validate a registry against an out-of-band version digest.

    ``TEST_ONLY`` registries are rejected by the default production context.
    The expected registry digest has no default so callers cannot trust the
    in-band digest by accident.
    """

    if not isinstance(registry, Mapping):
        raise SignedReceiptError("reviewer registry must be a JSON object")
    requested_context = _trust_context(
        trust_context,
        field="trust_context",
    )
    _exact_keys(registry, _REGISTRY_KEYS, field="reviewer registry")
    if registry.get("schema") != "ark-kb-trusted-reviewer-registry/v2":
        raise SignedReceiptError("reviewer registry schema must be v2")
    registry_id = _required_text(
        registry.get("registryId"),
        field="registryId",
    )
    registry_version = _required_text(
        registry.get("registryVersion"),
        field="registryVersion",
    )
    registry_context = _trust_context(
        registry.get("trustContext"),
        field="registry trustContext",
    )
    if registry_context != requested_context:
        if (
            registry_context == TEST_ONLY
            and requested_context == PRODUCTION
        ):
            raise SignedReceiptError(
                "TEST_ONLY registry is not valid in PRODUCTION"
            )
        raise SignedReceiptError(
            "registry trustContext does not match requested context"
        )
    now = _verification_time(verification_time)
    generated_at = _timestamp(
        registry.get("generatedAt"),
        field="registry generatedAt",
    )
    if generated_at > now:
        raise SignedReceiptError(
            "registry generatedAt must not be in the future"
        )

    raw_reviewers = registry.get("reviewers")
    if not isinstance(raw_reviewers, list) or not raw_reviewers:
        raise SignedReceiptError(
            "reviewers must be a non-empty JSON array"
        )
    reviewers: dict[str, ValidatedReviewer] = {}
    fingerprint_owners: dict[str, str] = {}
    for index, raw_entry in enumerate(raw_reviewers):
        field = f"reviewers[{index}]"
        if not isinstance(raw_entry, Mapping):
            raise SignedReceiptError(f"{field} must be a JSON object")
        _exact_keys(raw_entry, _REGISTRY_ENTRY_KEYS, field=field)
        reviewer_id = _required_text(
            raw_entry.get("reviewerId"),
            field=f"{field}.reviewerId",
        )
        if reviewer_id in reviewers:
            raise SignedReceiptError(
                f"duplicate reviewerId: {reviewer_id}"
            )
        if raw_entry.get("publicKeyAlgorithm") != "Ed25519":
            raise SignedReceiptError(
                f"{field}.publicKeyAlgorithm must be Ed25519"
            )
        public_key_bytes = _decode_base64(
            raw_entry.get("publicKeyBase64"),
            field=f"{field}.publicKeyBase64",
            expected_length=32,
        )
        fingerprint = _required_sha256(
            raw_entry.get("publicKeyFingerprint"),
            field=f"{field}.publicKeyFingerprint",
        )
        observed_fingerprint = public_key_fingerprint(public_key_bytes)
        if fingerprint != observed_fingerprint:
            raise SignedReceiptError(
                f"{field}.publicKeyFingerprint does not match public key"
            )
        alias_owner = fingerprint_owners.get(fingerprint)
        if alias_owner is not None:
            raise SignedReceiptError(
                "public key alias is forbidden: "
                f"{alias_owner} and {reviewer_id}"
            )

        raw_roles = raw_entry.get("allowedRoles")
        if not isinstance(raw_roles, list) or not raw_roles:
            raise SignedReceiptError(
                f"{field}.allowedRoles must be a non-empty JSON array"
            )
        if any(not isinstance(role, str) for role in raw_roles):
            raise SignedReceiptError(
                f"{field}.allowedRoles must contain only strings"
            )
        roles = frozenset(raw_roles)
        if len(roles) != len(raw_roles):
            raise SignedReceiptError(
                f"{field}.allowedRoles must be unique"
            )
        unsupported_roles = sorted(roles - _ROLES)
        if unsupported_roles:
            raise SignedReceiptError(
                f"{field}.allowedRoles contains unsupported roles: "
                + ",".join(unsupported_roles)
            )

        valid_from = _timestamp(
            raw_entry.get("validFrom"),
            field=f"{field}.validFrom",
        )
        valid_until = _timestamp(
            raw_entry.get("validUntil"),
            field=f"{field}.validUntil",
        )
        if valid_until <= valid_from:
            raise SignedReceiptError(
                f"{field}.validUntil must be later than validFrom"
            )
        raw_revoked_at = raw_entry.get("revokedAt")
        revoked_at = (
            None
            if raw_revoked_at is None
            else _timestamp(
                raw_revoked_at,
                field=f"{field}.revokedAt",
            )
        )
        if revoked_at is not None and revoked_at < valid_from:
            raise SignedReceiptError(
                f"{field}.revokedAt must not predate validFrom"
            )

        entry_sha = _required_sha256(
            raw_entry.get("registryEntrySha256"),
            field=f"{field}.registryEntrySha256",
        )
        observed_entry_sha = registry_entry_sha256(raw_entry)
        if entry_sha != observed_entry_sha:
            raise SignedReceiptError(
                f"{field}.registryEntrySha256 does not match entry"
            )
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                public_key_bytes
            )
        except ValueError as error:
            raise SignedReceiptError(
                f"{field}.publicKeyBase64 is not an Ed25519 public key"
            ) from error

        reviewers[reviewer_id] = ValidatedReviewer(
            reviewer_id=reviewer_id,
            public_key=public_key,
            public_key_fingerprint=fingerprint,
            allowed_roles=roles,
            valid_from=valid_from,
            valid_until=valid_until,
            revoked_at=revoked_at,
        )
        fingerprint_owners[fingerprint] = reviewer_id

    version_sha = _required_sha256(
        registry.get("registryVersionSha256"),
        field="registryVersionSha256",
    )
    observed_version_sha = registry_version_sha256(registry)
    if version_sha != observed_version_sha:
        raise SignedReceiptError(
            "registryVersionSha256 does not match registry content"
        )
    expected_sha = _required_sha256(
        expected_registry_sha256,
        field="expected out-of-band registry SHA-256",
    )
    if expected_sha != observed_version_sha:
        raise SignedReceiptError(
            "out-of-band registry SHA-256 does not match registry"
        )
    return ValidatedReviewerRegistry(
        registry_id=registry_id,
        registry_version=registry_version,
        version_sha256=version_sha,
        trust_context=registry_context,
        reviewers=MappingProxyType(reviewers),
    )


def _artifact_path(
    artifact_root: Path,
    artifact_uri: object,
) -> tuple[str, Path]:
    uri = _required_text(artifact_uri, field="artifactUri")
    prefix = "artifact://"
    relative_text = uri[len(prefix) :] if uri.startswith(prefix) else ""
    if (
        not relative_text
        or "\\" in relative_text
        or "%" in relative_text
        or "?" in relative_text
        or "#" in relative_text
        or "\x00" in relative_text
    ):
        raise SignedReceiptError(
            "artifactUri must be a safe relative artifact URI"
        )
    relative_path = PurePosixPath(relative_text)
    if (
        relative_path.is_absolute()
        or any(
            part in {"", ".", ".."} or ":" in part
            for part in relative_path.parts
        )
    ):
        raise SignedReceiptError(
            "artifactUri must be a safe relative artifact URI"
        )
    try:
        resolved_root = artifact_root.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise SignedReceiptError("artifact root does not exist") from error
    if not resolved_root.is_dir():
        raise SignedReceiptError("artifact root must be a directory")
    candidate = resolved_root.joinpath(*relative_path.parts)
    try:
        resolved_candidate = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise SignedReceiptError(
            f"artifact does not exist: {uri}"
        ) from error
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise SignedReceiptError(
            "artifactUri must be a safe relative artifact URI"
        ) from error
    if not resolved_candidate.is_file():
        raise SignedReceiptError(f"artifact is not a file: {uri}")
    return uri, resolved_candidate


def verify_signed_receipt(
    receipt: Mapping[str, object],
    *,
    registry: Mapping[str, object],
    expected_registry_sha256: str,
    expected_scope: Mapping[str, object],
    expected_role: str,
    artifact_root: Path,
    replay_guard: ReceiptReplayGuard | MutableSet[str],
    trust_context: str = PRODUCTION,
    verification_time: datetime | None = None,
) -> VerifiedSignedReceipt:
    """Verify a signed v2 receipt and claim its bundle replay keys.

    Production callers must provide :class:`ReceiptReplayGuard`. A plain
    mutable set remains available only for explicit ``TEST_ONLY`` compatibility.
    """

    if not isinstance(receipt, Mapping):
        raise SignedReceiptError("signed receipt must be a JSON object")
    context = _trust_context(trust_context, field="trust_context")
    if context == PRODUCTION and not isinstance(
        replay_guard,
        ReceiptReplayGuard,
    ):
        raise SignedReceiptError(
            "production replay_guard must be ReceiptReplayGuard"
        )
    validated_registry = validate_reviewer_registry(
        registry,
        expected_registry_sha256=expected_registry_sha256,
        trust_context=context,
        verification_time=verification_time,
    )
    _exact_keys(receipt, _ENVELOPE_KEYS, field="signed receipt envelope")
    if receipt.get("schema") != "ark-kb-signed-receipt-envelope/v2":
        raise SignedReceiptError("signed receipt envelope schema must be v2")
    if receipt.get("signatureAlgorithm") != "Ed25519":
        raise SignedReceiptError("signatureAlgorithm must be Ed25519")
    raw_payload = receipt.get("payload")
    if not isinstance(raw_payload, Mapping):
        raise SignedReceiptError("receipt payload must be a JSON object")
    _exact_keys(raw_payload, _PAYLOAD_KEYS, field="receipt payload")
    if raw_payload.get("schema") != "ark-kb-signed-receipt-payload/v2":
        raise SignedReceiptError("receipt payload schema must be v2")

    receipt_id = _required_text(
        raw_payload.get("receiptId"),
        field="receiptId",
    )
    signer_id = _required_text(
        raw_payload.get("signerId"),
        field="signerId",
    )
    role = _required_text(raw_payload.get("role"), field="role")
    if role not in _ROLES:
        raise SignedReceiptError(f"unsupported receipt role: {role}")
    required_role = _required_text(expected_role, field="expected_role")
    if required_role not in _ROLES:
        raise SignedReceiptError(
            f"unsupported expected role: {required_role}"
        )
    if role != required_role:
        raise SignedReceiptError(
            "receipt role does not match expected role"
        )
    nonce = _required_text(raw_payload.get("nonce"), field="nonce")
    if len(nonce) < 8:
        raise SignedReceiptError("nonce must contain at least 8 characters")

    payload_registry_sha = _required_sha256(
        raw_payload.get("registryVersionSha256"),
        field="payload registryVersionSha256",
    )
    if payload_registry_sha != validated_registry.version_sha256:
        raise SignedReceiptError(
            "payload registryVersionSha256 does not match registry"
        )
    payload_context = _trust_context(
        raw_payload.get("trustContext"),
        field="receipt trustContext",
    )
    if payload_context != context:
        raise SignedReceiptError(
            "receipt trustContext does not match verification context"
        )

    if not isinstance(expected_scope, Mapping):
        raise SignedReceiptError("expected_scope must be a JSON object")
    raw_scope = raw_payload.get("scope")
    if not isinstance(raw_scope, Mapping):
        raise SignedReceiptError("receipt scope must be a JSON object")
    if canonical_json_bytes(raw_scope) != canonical_json_bytes(
        expected_scope
    ):
        raise SignedReceiptError(
            "receipt scope does not match expected scope"
        )
    raw_claim = raw_payload.get("claim")
    if not isinstance(raw_claim, Mapping):
        raise SignedReceiptError("receipt claim must be a JSON object")
    canonical_json_bytes(raw_claim)

    signer = validated_registry.reviewers.get(signer_id)
    if signer is None:
        raise SignedReceiptError(f"unregistered signerId: {signer_id}")
    if role not in signer.allowed_roles:
        raise SignedReceiptError(
            f"receipt role is not allowed for signerId: {signer_id}"
        )
    now = _verification_time(verification_time)
    issued_at = _timestamp(
        raw_payload.get("issuedAt"),
        field="issuedAt",
    )
    if issued_at > now:
        raise SignedReceiptError("issuedAt must not be in the future")
    if now > signer.valid_until or issued_at > signer.valid_until:
        raise SignedReceiptError("signing key is expired")
    if issued_at < signer.valid_from:
        raise SignedReceiptError(
            "receipt predates signing key validity"
        )
    if signer.revoked_at is not None and now >= signer.revoked_at:
        raise SignedReceiptError("signing key is revoked")

    observed_payload_sha = signed_payload_sha256(raw_payload)
    claimed_payload_sha = _required_sha256(
        receipt.get("signedPayloadSha256"),
        field="signedPayloadSha256",
    )
    if claimed_payload_sha != observed_payload_sha:
        raise SignedReceiptError(
            "signedPayloadSha256 does not match receipt payload"
        )
    signature = _decode_base64(
        receipt.get("signatureBase64"),
        field="signatureBase64",
        expected_length=64,
    )
    try:
        signer.public_key.verify(
            signature,
            canonical_json_bytes(raw_payload),
        )
    except InvalidSignature as error:
        raise SignedReceiptError(
            "Ed25519 signature verification failed"
        ) from error

    artifact_sha = _required_sha256(
        raw_payload.get("artifactSha256"),
        field="artifactSha256",
    )
    artifact_uri, artifact_path = _artifact_path(
        artifact_root,
        raw_payload.get("artifactUri"),
    )
    artifact_bytes = artifact_path.read_bytes()
    observed_artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
    if artifact_sha != observed_artifact_sha:
        raise SignedReceiptError(
            "artifactSha256 does not match artifact bytes"
        )

    receipt_replay_key = (
        "receipt:"
        + validated_registry.version_sha256
        + ":"
        + receipt_id
    )
    nonce_replay_key = (
        "nonce:"
        + signer.public_key_fingerprint
        + ":"
        + nonce
    )
    replay_keys = (receipt_replay_key, nonce_replay_key)
    if isinstance(replay_guard, ReceiptReplayGuard):
        conflicts = replay_guard.claim_many(replay_keys)
    elif context == TEST_ONLY and isinstance(replay_guard, MutableSet):
        conflicts = frozenset(
            key for key in replay_keys if key in replay_guard
        )
        if not conflicts:
            replay_guard.update(replay_keys)
    else:
        raise AssertionError("validated replay_guard policy was not preserved")
    if receipt_replay_key in conflicts:
        raise SignedReceiptError(f"receipt replay detected: {receipt_id}")
    if nonce_replay_key in conflicts:
        raise SignedReceiptError(f"nonce replay detected: {nonce}")

    scope_copy = _deep_freeze_json(
        json.loads(canonical_json_bytes(raw_scope))
    )
    if not isinstance(scope_copy, Mapping):
        raise AssertionError("validated receipt scope must remain a mapping")
    claim_copy = _deep_freeze_json(
        json.loads(canonical_json_bytes(raw_claim))
    )
    if not isinstance(claim_copy, Mapping):
        raise AssertionError("validated receipt claim must remain a mapping")
    return VerifiedSignedReceipt(
        receipt_id=receipt_id,
        signer_id=signer_id,
        role=role,
        nonce=nonce,
        issued_at=issued_at,
        registry_version_sha256=validated_registry.version_sha256,
        signed_payload_sha256=observed_payload_sha,
        scope=scope_copy,
        claim=claim_copy,
        artifact_uri=artifact_uri,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha,
        artifact_bytes=artifact_bytes,
    )
