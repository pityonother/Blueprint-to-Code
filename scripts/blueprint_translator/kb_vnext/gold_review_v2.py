"""Signed, artifact-bound Gold review validation.

This module verifies review evidence supplied by external reviewers. It does
not create identities, generate keys, sign receipts, resolve unanswered cases,
or write production Gold.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from .gold_review import GoldReviewError, validate_review_pack
from .signed_receipts import (
    PRODUCTION,
    TEST_ONLY,
    ReceiptReplayGuard,
    SignedReceiptError,
    canonical_json_bytes,
    verify_signed_receipt,
)


REVIEW_ARTIFACT_SCHEMA = "ark-kb-gold-review-artifact/v2"
REVIEW_CLAIM_SCHEMA = "ark-kb-gold-review-claim/v2"
REVIEW_SCOPE_CONTRACT = "ark-kb-gold-review/v2"
VALIDATION_SCHEMA = "ark-kb-gold-review-validation/v2"

READY_TO_PROPOSE = "READY_TO_PROPOSE"
BLOCKED_BY_INDEPENDENT_REVIEW = "BLOCKED_BY_INDEPENDENT_REVIEW"
SIGNED_V2_RECEIPTS_REQUIRED = "SIGNED_V2_RECEIPTS_REQUIRED"
FULL_PACK_REVIEW_REQUIRED = "FULL_PACK_REVIEW_REQUIRED"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVIEW_ROLES = frozenset({"REVIEWER", "ADJUDICATOR"})
_VERDICTS = frozenset(
    {"CONFIRMED", "REJECTED", "EXPECTED_GAP", "UNRESOLVED"}
)
_RESOLVED_VERDICTS = frozenset({"CONFIRMED", "EXPECTED_GAP"})
_FRESHNESS = frozenset({"FRESH", "STALE", "NOT_RECOVERED"})
_ARTIFACT_FIELDS = frozenset(
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
    }
)
_EVIDENCE_FIELDS = frozenset(
    {"uri", "sourceRevisionSha256", "freshness"}
)
_CLAIM = {
    "schema": REVIEW_CLAIM_SCHEMA,
    "artifactSchema": REVIEW_ARTIFACT_SCHEMA,
}


class GoldReviewV2Error(ValueError):
    """Raised when signed Gold review input fails closed."""


@dataclass(frozen=True)
class VerifiedGoldReview:
    """One verified review whose exact artifact bytes were signature-bound."""

    receipt_id: str
    case_id: str
    reviewer_id: str
    public_key_fingerprint: str
    reviewer_role: str
    round_number: int
    reviewed_at: str
    verdict: str
    answer: Mapping[str, object]
    evidence: tuple[Mapping[str, object], ...]
    rationale: str
    tool_version: str
    registry_version_sha256: str
    signed_payload_sha256: str
    artifact_uri: str
    artifact_sha256: str
    artifact_bytes: bytes


@dataclass(frozen=True)
class GoldReviewValidation:
    """Validated review-set result without any production write capability."""

    pack_id: str
    pack_sha256: str
    kind: str
    trust_context: str
    candidate_cases: int
    required_cases: int
    reviewed_cases: int
    review_count: int
    status: str
    gaps: tuple[str, ...]
    contract_complete: bool
    production_gold_eligible: bool
    receipt_set_sha256: str | None
    verified_reviews: tuple[VerifiedGoldReview, ...]
    resolved_reviews: Mapping[str, VerifiedGoldReview]

    def to_summary(self) -> dict[str, object]:
        """Return a JSON-safe summary that does not expose review answers."""

        return {
            "schema": VALIDATION_SCHEMA,
            "packId": self.pack_id,
            "packSha256": self.pack_sha256,
            "kind": self.kind,
            "trustContext": self.trust_context,
            "candidateCases": self.candidate_cases,
            "requiredCases": self.required_cases,
            "reviewedCases": self.reviewed_cases,
            "reviewCount": self.review_count,
            "status": self.status,
            "gaps": list(self.gaps),
            "contractComplete": self.contract_complete,
            "productionGoldEligible": self.production_gold_eligible,
            "productionGoldWritten": False,
            "reviewReceiptSetSha256": self.receipt_set_sha256,
        }


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldReviewV2Error(f"{field} must be a non-empty string")
    if value != value.strip() or any(
        character in value for character in ("\x00", "\r", "\n")
    ):
        raise GoldReviewV2Error(
            f"{field} must not contain surrounding or control whitespace"
        )
    return value


def _required_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise GoldReviewV2Error(
            f"{field} must be a lowercase SHA-256 hex digest"
        )
    return value


def _positive_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GoldReviewV2Error(f"{field} must be a positive integer")
    return value


def _timestamp(value: object, *, field: str) -> str:
    text = _required_text(value, field=field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise GoldReviewV2Error(
            f"{field} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise GoldReviewV2Error(f"{field} must include a timezone")
    return text


def _exact_fields(
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
    raise GoldReviewV2Error(
        f"{field} fields are invalid ({'; '.join(details)})"
    )


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return value


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(child) for child in value]
    return value


def _strict_json_bytes(value: bytes, *, field: str) -> object:
    def object_pairs(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, child in pairs:
            if key in result:
                raise GoldReviewV2Error(
                    f"{field} contains duplicate JSON key: {key}"
                )
            result[key] = child
        return result

    def reject_constant(constant: str) -> object:
        raise GoldReviewV2Error(
            f"{field} contains non-finite JSON number: {constant}"
        )

    try:
        text = value.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except GoldReviewV2Error:
        raise
    except ValueError as error:
        raise GoldReviewV2Error(
            f"{field} must contain strict UTF-8 JSON"
        ) from error


def parse_strict_json_bytes(value: bytes, *, field: str) -> object:
    """Parse untrusted JSON bytes with duplicate and number checks."""

    return _strict_json_bytes(value, field=field)


def _json_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            _plain_json(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as error:
        raise GoldReviewV2Error(
            "review answer must contain finite JSON data"
        ) from error
    return hashlib.sha256(encoded).hexdigest()


def _validated_pack(
    pack: Mapping[str, object],
) -> dict[str, object]:
    try:
        return validate_review_pack(pack)
    except GoldReviewError as error:
        raise GoldReviewV2Error(str(error)) from error


def _candidate_map(
    pack: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    candidates = pack.get("candidates")
    if not isinstance(candidates, list):
        raise GoldReviewV2Error("review pack requires candidates")
    return {
        str(candidate["caseId"]): candidate
        for candidate in candidates
        if isinstance(candidate, Mapping)
    }


def gold_review_scope_v2(
    pack: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    round_number: int,
) -> dict[str, object]:
    """Build the exact scope every Gold review v2 signature must cover."""

    return {
        "contract": REVIEW_SCOPE_CONTRACT,
        "packId": _required_text(pack.get("packId"), field="packId"),
        "packSha256": _required_sha256(
            pack.get("packSha256"),
            field="packSha256",
        ),
        "kind": _required_text(pack.get("kind"), field="kind"),
        "sourceManifestSha256": _required_sha256(
            pack.get("sourceManifestSha256"),
            field="sourceManifestSha256",
        ),
        "caseId": _required_text(
            candidate.get("caseId"),
            field="caseId",
        ),
        "candidateSha256": _required_sha256(
            candidate.get("candidateSha256"),
            field="candidateSha256",
        ),
        "round": _positive_integer(round_number, field="round"),
    }


def _validate_artifact(
    raw: object,
    *,
    verified_receipt: object,
    pack: Mapping[str, object],
    candidate: Mapping[str, object],
) -> VerifiedGoldReview:
    from .signed_receipts import VerifiedSignedReceipt

    if not isinstance(verified_receipt, VerifiedSignedReceipt):
        raise AssertionError("receipt verification result is invalid")
    if not isinstance(raw, Mapping):
        raise GoldReviewV2Error("review artifact must be a JSON object")
    _exact_fields(raw, _ARTIFACT_FIELDS, field="review artifact")
    if raw.get("schema") != REVIEW_ARTIFACT_SCHEMA:
        raise GoldReviewV2Error("review artifact schema must be v2")

    expected_pairs = {
        "packId": pack.get("packId"),
        "packSha256": pack.get("packSha256"),
        "caseId": candidate.get("caseId"),
        "candidateSha256": candidate.get("candidateSha256"),
        "reviewerId": verified_receipt.signer_id,
        "reviewerRole": verified_receipt.role,
    }
    for field, expected in expected_pairs.items():
        if raw.get(field) != expected:
            raise GoldReviewV2Error(
                f"review artifact {field} does not match signed scope"
            )

    round_number = _positive_integer(raw.get("round"), field="round")
    if round_number != verified_receipt.scope.get("round"):
        raise GoldReviewV2Error(
            "review artifact round does not match signed scope"
        )
    reviewed_at = _timestamp(raw.get("reviewedAt"), field="reviewedAt")

    role = str(raw.get("reviewerRole") or "")
    if role not in _REVIEW_ROLES:
        raise GoldReviewV2Error("unsupported Gold review role")
    verdict = raw.get("verdict")
    if not isinstance(verdict, str) or verdict not in _VERDICTS:
        raise GoldReviewV2Error("unsupported Gold review verdict")
    answer = raw.get("answer")
    if not isinstance(answer, Mapping):
        raise GoldReviewV2Error("review answer must be a JSON object")
    _json_sha256(answer)

    raw_evidence = raw.get("evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise GoldReviewV2Error(
            "review requires at least one evidence item"
        )
    evidence: list[Mapping[str, object]] = []
    freshness_values: list[str] = []
    for index, item in enumerate(raw_evidence):
        if not isinstance(item, Mapping):
            raise GoldReviewV2Error(
                f"evidence {index + 1} must be a JSON object"
            )
        _exact_fields(
            item,
            _EVIDENCE_FIELDS,
            field=f"evidence {index + 1}",
        )
        _required_text(item.get("uri"), field=f"evidence {index + 1} uri")
        _required_sha256(
            item.get("sourceRevisionSha256"),
            field=f"evidence {index + 1} sourceRevisionSha256",
        )
        freshness = item.get("freshness")
        if not isinstance(freshness, str) or freshness not in _FRESHNESS:
            raise GoldReviewV2Error("unsupported evidence freshness")
        freshness_values.append(freshness)
        frozen = _freeze_json(item)
        if not isinstance(frozen, Mapping):
            raise AssertionError("validated evidence must remain a mapping")
        evidence.append(frozen)
    if verdict == "CONFIRMED" and any(
        freshness != "FRESH" for freshness in freshness_values
    ):
        raise GoldReviewV2Error(
            "confirmed review requires fresh evidence"
        )

    rationale = _required_text(raw.get("rationale"), field="rationale")
    tool_version = _required_text(
        raw.get("toolVersion"),
        field="toolVersion",
    )
    frozen_answer = _freeze_json(answer)
    if not isinstance(frozen_answer, Mapping):
        raise AssertionError("validated answer must remain a mapping")
    return VerifiedGoldReview(
        receipt_id=verified_receipt.receipt_id,
        case_id=str(candidate["caseId"]),
        reviewer_id=verified_receipt.signer_id,
        public_key_fingerprint=(
            verified_receipt.public_key_fingerprint
        ),
        reviewer_role=role,
        round_number=round_number,
        reviewed_at=reviewed_at,
        verdict=verdict,
        answer=frozen_answer,
        evidence=tuple(evidence),
        rationale=rationale,
        tool_version=tool_version,
        registry_version_sha256=(
            verified_receipt.registry_version_sha256
        ),
        signed_payload_sha256=(
            verified_receipt.signed_payload_sha256
        ),
        artifact_uri=verified_receipt.artifact_uri,
        artifact_sha256=verified_receipt.artifact_sha256,
        artifact_bytes=verified_receipt.artifact_bytes,
    )


def verify_gold_review_receipt_v2(
    pack: Mapping[str, object],
    receipt: Mapping[str, object],
    *,
    registry: Mapping[str, object],
    expected_registry_sha256: str,
    expected_pack_author_key_fingerprint: str | None,
    artifact_root: Path,
    replay_guard: ReceiptReplayGuard,
    trust_context: str = PRODUCTION,
    verification_time: datetime | None = None,
) -> VerifiedGoldReview:
    """Verify one signed receipt against its exact pack/case artifact."""

    normalized_pack = _validated_pack(pack)
    if receipt.get("schema") == "ark-kb-gold-review/v1":
        raise GoldReviewV2Error(SIGNED_V2_RECEIPTS_REQUIRED)
    pack_author_key_fingerprint = _pack_author_key_fingerprint(
        normalized_pack,
        expected_pack_author_key_fingerprint,
    )
    payload = receipt.get("payload")
    if not isinstance(payload, Mapping):
        raise GoldReviewV2Error(
            "signed Gold review receipt requires a v2 payload"
        )
    scope = payload.get("scope")
    if not isinstance(scope, Mapping):
        raise GoldReviewV2Error(
            "signed Gold review receipt requires an exact scope"
        )
    case_id = _required_text(scope.get("caseId"), field="scope caseId")
    candidates = _candidate_map(normalized_pack)
    candidate = candidates.get(case_id)
    if candidate is None:
        raise GoldReviewV2Error(f"unknown review caseId: {case_id}")
    round_number = _positive_integer(
        scope.get("round"),
        field="scope round",
    )
    role = _required_text(payload.get("role"), field="receipt role")
    if role not in _REVIEW_ROLES:
        raise GoldReviewV2Error("unsupported Gold review role")
    expected_scope = gold_review_scope_v2(
        normalized_pack,
        candidate,
        round_number=round_number,
    )
    try:
        verified = verify_signed_receipt(
            receipt,
            registry=registry,
            expected_registry_sha256=expected_registry_sha256,
            expected_scope=expected_scope,
            expected_role=role,
            artifact_root=artifact_root,
            replay_guard=replay_guard,
            trust_context=trust_context,
            verification_time=verification_time,
        )
    except SignedReceiptError as error:
        raise GoldReviewV2Error(str(error)) from error
    if canonical_json_bytes(_plain_json(verified.claim)) != (
        canonical_json_bytes(_CLAIM)
    ):
        raise GoldReviewV2Error(
            "signed Gold review claim does not match v2 contract"
        )

    artifact = _strict_json_bytes(
        verified.artifact_bytes,
        field="review artifact",
    )
    review = _validate_artifact(
        artifact,
        verified_receipt=verified,
        pack=normalized_pack,
        candidate=candidate,
    )
    payload_issued_at = _required_text(
        payload.get("issuedAt"),
        field="issuedAt",
    )
    if review.reviewed_at != payload_issued_at:
        raise GoldReviewV2Error(
            "review artifact reviewedAt does not match signed issuedAt"
        )
    pack_author_id = _required_text(
        normalized_pack.get("authorId"),
        field="pack authorId",
    )
    if (
        review.reviewer_id.casefold() == pack_author_id.casefold()
        or review.public_key_fingerprint == pack_author_key_fingerprint
    ):
        raise GoldReviewV2Error("review pack author cannot review own case")
    return review


def _answer_identity(review: VerifiedGoldReview) -> str:
    return _json_sha256(
        {"verdict": review.verdict, "answer": review.answer}
    )


def gold_review_receipt_set_sha256(
    reviews: Sequence[VerifiedGoldReview],
) -> str:
    """Hash a stable inventory of verified signed receipt identities."""

    inventory = sorted(
        (
            {
                "caseId": review.case_id,
                "role": review.reviewer_role,
                "round": review.round_number,
                "reviewerId": review.reviewer_id,
                "publicKeyFingerprint": review.public_key_fingerprint,
                "receiptId": review.receipt_id,
                "signedPayloadSha256": review.signed_payload_sha256,
                "artifactSha256": review.artifact_sha256,
            }
            for review in reviews
        ),
        key=lambda item: (
            str(item["caseId"]),
            str(item["role"]),
            int(item["round"]),
            str(item["publicKeyFingerprint"]),
            str(item["receiptId"]),
        ),
    )
    return hashlib.sha256(canonical_json_bytes(inventory)).hexdigest()


def _review_requirements(
    pack: Mapping[str, object],
    required_case_ids: Sequence[str] | None,
) -> tuple[str, ...]:
    candidates = _candidate_map(pack)
    if required_case_ids is None:
        return tuple(candidates)
    required = tuple(
        _required_text(case_id, field="required caseId")
        for case_id in required_case_ids
    )
    if not required:
        raise GoldReviewV2Error("required_case_ids must not be empty")
    if len(required) != len(set(required)):
        raise GoldReviewV2Error("required_case_ids must be unique")
    unknown = sorted(set(required) - set(candidates))
    if unknown:
        raise GoldReviewV2Error(
            "required case is absent from pack: " + ",".join(unknown)
        )
    return required


def _pack_author_key_fingerprint(
    pack: Mapping[str, object],
    expected_pack_author_key_fingerprint: str | None,
) -> str:
    if expected_pack_author_key_fingerprint is None:
        raise GoldReviewV2Error(
            "expected_pack_author_key_fingerprint is required out of band"
        )
    expected = _required_sha256(
        expected_pack_author_key_fingerprint,
        field="expected_pack_author_key_fingerprint",
    )
    observed = pack.get("authorKeyFingerprint")
    if not isinstance(observed, str) or _SHA256.fullmatch(observed) is None:
        raise GoldReviewV2Error(
            "pack authorKeyFingerprint must be a lowercase SHA-256 "
            "fingerprint for signed v2 review"
        )
    if observed != expected:
        raise GoldReviewV2Error(
            "expected_pack_author_key_fingerprint does not match pack "
            "authorKeyFingerprint"
        )
    return observed


def _result(
    *,
    pack: Mapping[str, object],
    trust_context: str,
    required: Sequence[str],
    reviews: Sequence[VerifiedGoldReview],
    resolved: Mapping[str, VerifiedGoldReview],
    status: str,
    gaps: Sequence[str],
    complete: bool,
    eligible: bool,
) -> GoldReviewValidation:
    return GoldReviewValidation(
        pack_id=str(pack["packId"]),
        pack_sha256=str(pack["packSha256"]),
        kind=str(pack["kind"]),
        trust_context=trust_context,
        candidate_cases=len(_candidate_map(pack)),
        required_cases=len(required),
        reviewed_cases=len(resolved),
        review_count=len(reviews),
        status=status,
        gaps=tuple(sorted(set(gaps))),
        contract_complete=complete,
        production_gold_eligible=eligible,
        receipt_set_sha256=(
            gold_review_receipt_set_sha256(reviews) if reviews else None
        ),
        verified_reviews=tuple(reviews),
        resolved_reviews=MappingProxyType(dict(resolved)),
    )


def validate_gold_review_set_v2(
    pack: Mapping[str, object],
    receipts: Sequence[Mapping[str, object]],
    *,
    registry: Mapping[str, object] | None,
    expected_registry_sha256: str | None,
    artifact_root: Path | None,
    expected_pack_author_key_fingerprint: str | None = None,
    required_case_ids: Sequence[str] | None = None,
    trust_context: str = PRODUCTION,
    verification_time: datetime | None = None,
) -> GoldReviewValidation:
    """Validate independent signed reviews without writing production Gold."""

    normalized_pack = _validated_pack(pack)
    if trust_context not in {PRODUCTION, TEST_ONLY}:
        raise GoldReviewV2Error(
            "trust_context must be PRODUCTION or TEST_ONLY"
        )
    required = _review_requirements(
        normalized_pack,
        required_case_ids,
    )
    full_pack_review = set(required) == set(
        _candidate_map(normalized_pack)
    )
    v1_receipts = [
        receipt
        for receipt in receipts
        if isinstance(receipt, Mapping)
        and receipt.get("schema") == "ark-kb-gold-review/v1"
    ]
    if v1_receipts and len(v1_receipts) != len(receipts):
        raise GoldReviewV2Error(
            "mixed v1 and signed v2 Gold review receipts are not allowed"
        )
    if v1_receipts:
        gaps = [SIGNED_V2_RECEIPTS_REQUIRED]
        if not full_pack_review:
            gaps.append(FULL_PACK_REVIEW_REQUIRED)
        return _result(
            pack=normalized_pack,
            trust_context=trust_context,
            required=required,
            reviews=(),
            resolved={},
            status=(
                SIGNED_V2_RECEIPTS_REQUIRED
                if full_pack_review
                else BLOCKED_BY_INDEPENDENT_REVIEW
            ),
            gaps=gaps,
            complete=False,
            eligible=False,
        )
    if not receipts:
        gaps = [
            f"TWO_INDEPENDENT_REVIEWS_REQUIRED:{case_id}"
            for case_id in required
        ]
        if not full_pack_review:
            gaps.append(FULL_PACK_REVIEW_REQUIRED)
        return _result(
            pack=normalized_pack,
            trust_context=trust_context,
            required=required,
            reviews=(),
            resolved={},
            status=BLOCKED_BY_INDEPENDENT_REVIEW,
            gaps=gaps,
            complete=False,
            eligible=False,
        )
    pack_author_key_fingerprint = _pack_author_key_fingerprint(
        normalized_pack,
        expected_pack_author_key_fingerprint,
    )
    if registry is None:
        raise GoldReviewV2Error("reviewer registry v2 is required")
    if expected_registry_sha256 is None:
        raise GoldReviewV2Error(
            "expected_registry_sha256 is required out of band"
        )
    if artifact_root is None:
        raise GoldReviewV2Error("artifact_root is required")

    replay_guard = ReceiptReplayGuard()
    verified: list[VerifiedGoldReview] = []
    required_set = set(required)
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            raise GoldReviewV2Error(
                "signed receipt collection must contain JSON objects"
            )
        review = verify_gold_review_receipt_v2(
            normalized_pack,
            receipt,
            registry=registry,
            expected_registry_sha256=expected_registry_sha256,
            expected_pack_author_key_fingerprint=(
                pack_author_key_fingerprint
            ),
            artifact_root=artifact_root,
            replay_guard=replay_guard,
            trust_context=trust_context,
            verification_time=verification_time,
        )
        if review.case_id not in required_set:
            raise GoldReviewV2Error(
                f"receipt is outside required case set: {review.case_id}"
            )
        verified.append(review)

    by_case: dict[str, list[VerifiedGoldReview]] = defaultdict(list)
    for review in verified:
        by_case[review.case_id].append(review)
    for reviews in by_case.values():
        reviews.sort(
            key=lambda review: (
                review.round_number,
                review.reviewer_role,
                review.public_key_fingerprint,
            )
        )

    gaps: list[str] = []
    resolved: dict[str, VerifiedGoldReview] = {}
    for case_id in required:
        case_reviews = by_case.get(case_id, [])
        primary = [
            review
            for review in case_reviews
            if review.reviewer_role == "REVIEWER"
        ]
        adjudicators = [
            review
            for review in case_reviews
            if review.reviewer_role == "ADJUDICATOR"
        ]
        if len(primary) > 2:
            raise GoldReviewV2Error(
                f"exactly two reviewer receipts are allowed: {case_id}"
            )
        if len(adjudicators) > 1:
            raise GoldReviewV2Error(
                f"exactly one adjudicator is allowed: {case_id}"
            )
        if len(primary) < 2:
            gaps.append(
                f"TWO_INDEPENDENT_REVIEWS_REQUIRED:{case_id}"
            )
            continue
        if {review.round_number for review in primary} != {1, 2}:
            raise GoldReviewV2Error(
                f"reviewer rounds must be exactly 1 and 2: {case_id}"
            )
        reviewer_ids = {review.reviewer_id for review in primary}
        reviewer_keys = {
            review.public_key_fingerprint for review in primary
        }
        if len(reviewer_ids) != 2 or len(reviewer_keys) != 2:
            raise GoldReviewV2Error(
                "two review rounds require independent verified signer keys"
            )

        answers = {_answer_identity(review) for review in primary}
        if len(answers) == 1:
            if adjudicators:
                raise GoldReviewV2Error(
                    f"adjudication is not allowed without disagreement: "
                    f"{case_id}"
                )
            resolution = primary[0]
        else:
            if not adjudicators:
                gaps.append(
                    f"INDEPENDENT_ADJUDICATION_REQUIRED:{case_id}"
                )
                continue
            adjudicator = adjudicators[0]
            if adjudicator.round_number != 3:
                raise GoldReviewV2Error(
                    f"adjudicator round must be exactly 3: {case_id}"
                )
            if (
                adjudicator.reviewer_id in reviewer_ids
                or adjudicator.public_key_fingerprint in reviewer_keys
            ):
                raise GoldReviewV2Error(
                    "adjudicator must use a distinct verified signer key"
                )
            resolution = adjudicator
        if resolution.verdict not in _RESOLVED_VERDICTS:
            gaps.append(f"RESOLVED_VERDICT_REQUIRED:{case_id}")
            continue
        resolved[case_id] = resolution

    if not full_pack_review:
        gaps.append(FULL_PACK_REVIEW_REQUIRED)
    complete = len(resolved) == len(required) and not gaps
    eligible = complete and trust_context == PRODUCTION
    if complete and not eligible:
        gaps.append("PRODUCTION_SIGNED_RECEIPTS_REQUIRED")
    return _result(
        pack=normalized_pack,
        trust_context=trust_context,
        required=required,
        reviews=verified,
        resolved=resolved,
        status=(
            READY_TO_PROPOSE
            if eligible
            else BLOCKED_BY_INDEPENDENT_REVIEW
        ),
        gaps=gaps,
        complete=complete,
        eligible=eligible,
    )


__all__ = [
    "BLOCKED_BY_INDEPENDENT_REVIEW",
    "FULL_PACK_REVIEW_REQUIRED",
    "READY_TO_PROPOSE",
    "REVIEW_ARTIFACT_SCHEMA",
    "REVIEW_CLAIM_SCHEMA",
    "REVIEW_SCOPE_CONTRACT",
    "SIGNED_V2_RECEIPTS_REQUIRED",
    "VALIDATION_SCHEMA",
    "GoldReviewV2Error",
    "GoldReviewValidation",
    "VerifiedGoldReview",
    "gold_review_receipt_set_sha256",
    "gold_review_scope_v2",
    "parse_strict_json_bytes",
    "validate_gold_review_set_v2",
    "verify_gold_review_receipt_v2",
]
