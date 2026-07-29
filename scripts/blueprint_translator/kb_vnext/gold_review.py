"""Fail-closed review-pack and receipt validation for ARK KB gold sets.

This module validates review infrastructure only.  It never invents reviewer
identities and never writes query, registration, or role production gold.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path


PACK_SCHEMA = "ark-kb-gold-review-pack/v1"
REVIEW_SCHEMA = "ark-kb-gold-review/v1"
VALIDATION_SCHEMA = "ark-kb-gold-review-validation/v1"
QUERY_REVIEW_PROVENANCE_SCHEMA = "ark-kb-query-review-provenance/v1"
REVIEWER_REGISTRY_SCHEMA = "ark-kb-trusted-reviewer-registry/v1"
READY_TO_FREEZE = "READY_TO_FREEZE"
BLOCKED_BY_INDEPENDENT_REVIEW = "BLOCKED_BY_INDEPENDENT_REVIEW"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PACK_FIELDS = frozenset(
    {
        "schema",
        "packId",
        "kind",
        "authorId",
        "authorKeyFingerprint",
        "createdAt",
        "toolVersion",
        "seed",
        "selectionRule",
        "sourceManifestSha256",
        "candidates",
        "packSha256",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {"caseId", "payload", "candidateSha256"}
)
_REVIEW_FIELDS = frozenset(
    {
        "schema",
        "packId",
        "packSha256",
        "caseId",
        "candidateSha256",
        "reviewerId",
        "reviewerKeyFingerprint",
        "reviewerRole",
        "round",
        "reviewedAt",
        "verdict",
        "answer",
        "evidence",
        "rationale",
        "toolVersion",
        "contentSha256",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {"uri", "sourceRevisionSha256", "freshness"}
)
_REVIEWER_ROLES = frozenset({"REVIEWER", "ADJUDICATOR"})
_VERDICTS = frozenset(
    {"CONFIRMED", "REJECTED", "EXPECTED_GAP", "UNRESOLVED"}
)
_EVIDENCE_FRESHNESS = frozenset(
    {"FRESH", "STALE", "NOT_RECOVERED"}
)
_PACK_KINDS = frozenset({"query", "registration", "role"})
_LEAKAGE_PREFIXES = (
    "expected",
    "route",
    "prediction",
    "confidence",
    "knowledgeroles",
    "currentroles",
    "currentanswer",
)


class GoldReviewError(ValueError):
    """Raised when a pack or review receipt violates the review contract."""


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise GoldReviewError(
            "review content must be canonical JSON data"
        ) from error


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _required_text(
    value: object,
    *,
    field: str,
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise GoldReviewError(f"{field} must be a non-empty string")
    return normalized


def _validate_timestamp(value: object, *, field: str) -> str:
    normalized = _required_text(value, field=field)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise GoldReviewError(
            f"{field} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise GoldReviewError(f"{field} must include a timezone")
    return normalized


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _reject_prediction_leakage(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if any(
                normalized.startswith(prefix)
                for prefix in _LEAKAGE_PREFIXES
            ):
                raise GoldReviewError(
                    f"prediction leakage field {key!r} at {path}.{key}"
                )
            _reject_prediction_leakage(
                child,
                path=f"{path}.{key}",
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_prediction_leakage(
                child,
                path=f"{path}[{index}]",
            )


def _candidate_identity(candidate: Mapping[str, object]) -> dict[str, object]:
    return {
        "caseId": candidate.get("caseId"),
        "payload": candidate.get("payload"),
    }


def candidate_content_sha256(candidate: Mapping[str, object]) -> str:
    """Return the digest covering one case identity and blind payload."""

    return _sha256_json(_candidate_identity(candidate))


def _pack_identity(pack: Mapping[str, object]) -> dict[str, object]:
    candidates = pack.get("candidates")
    candidate_hashes = (
        [
            candidate.get("candidateSha256")
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ]
        if isinstance(candidates, list)
        else []
    )
    return {
        "kind": pack.get("kind"),
        "authorId": pack.get("authorId"),
        "authorKeyFingerprint": pack.get("authorKeyFingerprint"),
        "seed": pack.get("seed"),
        "selectionRule": pack.get("selectionRule"),
        "sourceManifestSha256": pack.get("sourceManifestSha256"),
        "candidateSha256s": candidate_hashes,
    }


def _pack_content(pack: Mapping[str, object]) -> dict[str, object]:
    return {
        key: copy.deepcopy(value)
        for key, value in pack.items()
        if key != "packSha256"
    }


def pack_content_sha256(pack: Mapping[str, object]) -> str:
    """Return the digest covering a complete pack except its digest field."""

    return _sha256_json(_pack_content(pack))


def review_content_sha256(review: Mapping[str, object]) -> str:
    """Return the digest covering a review receipt except its digest field."""

    return _sha256_json(
        {
            key: copy.deepcopy(value)
            for key, value in review.items()
            if key != "contentSha256"
        }
    )


def _evidence_uris(value: object) -> list[str]:
    found: set[str] = set()

    def visit(current: object) -> None:
        if isinstance(current, Mapping):
            for key, child in current.items():
                if _normalized_key(key) == "evidenceuri":
                    uri = str(child or "").strip()
                    if uri:
                        found.add(uri)
                else:
                    visit(child)
        elif isinstance(current, list):
            for child in current:
                visit(child)

    visit(value)
    return sorted(found)


def query_candidate_from_gold_case(
    raw_case: Mapping[str, object],
) -> dict[str, object]:
    """Project one fixed query case into a prediction-free review candidate."""

    if not isinstance(raw_case, Mapping):
        raise GoldReviewError("query gold case must be an object")
    case_id = _required_text(raw_case.get("id"), field="query case id")
    requirements = raw_case.get("requirements")
    expected = raw_case.get("expected")
    if not isinstance(requirements, Mapping):
        raise GoldReviewError(
            f"query case {case_id} requirements must be an object"
        )
    if not isinstance(expected, Mapping):
        raise GoldReviewError(
            f"query case {case_id} expected must be an object"
        )
    payload = {
        "question": _required_text(
            raw_case.get("question"),
            field=f"query case {case_id} question",
        ),
        "category": _required_text(
            raw_case.get("category"),
            field=f"query case {case_id} category",
        ),
        "primaryDomain": _required_text(
            raw_case.get("primaryDomain"),
            field=f"query case {case_id} primaryDomain",
        ),
        "entity": _required_text(
            raw_case.get("entity"),
            field=f"query case {case_id} entity",
        ),
        "requirements": copy.deepcopy(dict(requirements)),
        "evidenceUris": _evidence_uris(expected),
    }
    _reject_prediction_leakage(
        payload,
        path=f"queryCase[{case_id}]",
    )
    return {"caseId": case_id, "payload": payload}


def build_review_pack(
    *,
    kind: str,
    author_id: str,
    author_key_fingerprint: str,
    seed: str,
    selection_rule: str,
    source_manifest_sha256: str,
    candidates: Sequence[Mapping[str, object]],
    created_at: str,
    tool_version: str,
) -> dict[str, object]:
    """Build one deterministic prediction-free review pack."""

    normalized_kind = _required_text(kind, field="kind").casefold()
    if normalized_kind not in _PACK_KINDS:
        raise GoldReviewError(
            f"unsupported review kind: {normalized_kind}"
        )
    normalized_author = _required_text(author_id, field="authorId")
    normalized_author_key = _required_text(
        author_key_fingerprint,
        field="authorKeyFingerprint",
    )
    normalized_seed = _required_text(seed, field="seed")
    normalized_rule = _required_text(
        selection_rule,
        field="selectionRule",
    )
    normalized_tool = _required_text(
        tool_version,
        field="toolVersion",
    )
    normalized_created_at = _validate_timestamp(
        created_at,
        field="createdAt",
    )
    if not _is_sha256(source_manifest_sha256):
        raise GoldReviewError(
            "sourceManifestSha256 must be 64 lowercase hex digits"
        )
    if not candidates:
        raise GoldReviewError("review pack requires at least one candidate")

    normalized_candidates: list[dict[str, object]] = []
    case_ids: set[str] = set()
    for index, raw_candidate in enumerate(candidates):
        if not isinstance(raw_candidate, Mapping):
            raise GoldReviewError(
                f"candidate {index + 1} must be an object"
            )
        if set(raw_candidate) != {"caseId", "payload"}:
            raise GoldReviewError(
                "candidate input must contain only caseId and payload"
            )
        case_id = _required_text(
            raw_candidate.get("caseId"),
            field=f"candidate {index + 1} caseId",
        )
        if case_id in case_ids:
            raise GoldReviewError(f"duplicate caseId: {case_id}")
        payload = raw_candidate.get("payload")
        if not isinstance(payload, Mapping):
            raise GoldReviewError(
                f"candidate {case_id} payload must be an object"
            )
        _reject_prediction_leakage(
            payload,
            path=f"candidates[{index}].payload",
        )
        candidate = {
            "caseId": case_id,
            "payload": copy.deepcopy(dict(payload)),
        }
        candidate["candidateSha256"] = candidate_content_sha256(candidate)
        normalized_candidates.append(candidate)
        case_ids.add(case_id)

    pack: dict[str, object] = {
        "schema": PACK_SCHEMA,
        "kind": normalized_kind,
        "authorId": normalized_author,
        "authorKeyFingerprint": normalized_author_key,
        "createdAt": normalized_created_at,
        "toolVersion": normalized_tool,
        "seed": normalized_seed,
        "selectionRule": normalized_rule,
        "sourceManifestSha256": source_manifest_sha256,
        "candidates": normalized_candidates,
    }
    pack["packId"] = (
        f"{normalized_kind}-{_sha256_json(_pack_identity(pack))[:16]}"
    )
    pack["packSha256"] = pack_content_sha256(pack)
    validate_review_pack(pack)
    return pack


def build_query_review_pack(
    *,
    gold_set_path: Path,
    author_id: str,
    author_key_fingerprint: str,
    seed: str,
    created_at: str,
    tool_version: str,
) -> dict[str, object]:
    """Export every manually fixed query as a deterministic blind candidate."""

    try:
        source_bytes = gold_set_path.read_bytes()
        raw = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GoldReviewError(
            f"cannot read query gold set: {gold_set_path}"
        ) from error
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema") != "ark-kb-query-gold-set/v1"
        or raw.get("selectionMode") != "MANUAL_FIXED"
        or raw.get("generatedFromCore") is not False
    ):
        raise GoldReviewError(
            "query review export requires the manually fixed gold corpus"
        )
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise GoldReviewError("query gold corpus requires cases")
    candidates = [
        query_candidate_from_gold_case(raw_case)
        for raw_case in raw_cases
        if isinstance(raw_case, Mapping)
    ]
    if len(candidates) != len(raw_cases):
        raise GoldReviewError("query gold corpus contains a malformed case")
    candidates.sort(
        key=lambda candidate: (
            hashlib.sha256(
                (
                    f"{seed}\0{candidate['caseId']}"
                ).encode("utf-8")
            ).hexdigest(),
            str(candidate["caseId"]),
        )
    )
    return build_review_pack(
        kind="query",
        author_id=author_id,
        author_key_fingerprint=author_key_fingerprint,
        seed=seed,
        selection_rule="MANUAL_FIXED_ALL_CASES",
        source_manifest_sha256=hashlib.sha256(source_bytes).hexdigest(),
        candidates=candidates,
        created_at=created_at,
        tool_version=tool_version,
    )


def validate_review_pack(
    pack: Mapping[str, object],
) -> dict[str, object]:
    """Validate pack shape, blindness, identity, and content hashes."""

    if not isinstance(pack, Mapping):
        raise GoldReviewError("review pack must be an object")
    if set(pack) != _PACK_FIELDS:
        raise GoldReviewError("review pack fields do not match v1 contract")
    if pack.get("schema") != PACK_SCHEMA:
        raise GoldReviewError("unexpected review pack schema")
    kind = _required_text(pack.get("kind"), field="kind").casefold()
    if kind not in _PACK_KINDS:
        raise GoldReviewError(f"unsupported review kind: {kind}")
    _required_text(pack.get("authorId"), field="authorId")
    _required_text(
        pack.get("authorKeyFingerprint"),
        field="authorKeyFingerprint",
    )
    _required_text(pack.get("seed"), field="seed")
    _required_text(pack.get("selectionRule"), field="selectionRule")
    _required_text(pack.get("toolVersion"), field="toolVersion")
    _validate_timestamp(pack.get("createdAt"), field="createdAt")
    if not _is_sha256(pack.get("sourceManifestSha256")):
        raise GoldReviewError(
            "sourceManifestSha256 must be 64 lowercase hex digits"
        )
    candidates = pack.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise GoldReviewError("review pack requires candidates")

    case_ids: set[str] = set()
    candidate_hashes: set[str] = set()
    for index, candidate in enumerate(candidates):
        if (
            not isinstance(candidate, Mapping)
            or set(candidate) != _CANDIDATE_FIELDS
        ):
            raise GoldReviewError(
                f"candidate {index + 1} fields do not match v1 contract"
            )
        case_id = _required_text(
            candidate.get("caseId"),
            field=f"candidate {index + 1} caseId",
        )
        if case_id in case_ids:
            raise GoldReviewError(f"duplicate caseId: {case_id}")
        payload = candidate.get("payload")
        if not isinstance(payload, Mapping):
            raise GoldReviewError(
                f"candidate {case_id} payload must be an object"
            )
        _reject_prediction_leakage(
            payload,
            path=f"candidates[{index}].payload",
        )
        observed_hash = candidate.get("candidateSha256")
        expected_hash = candidate_content_sha256(candidate)
        if observed_hash != expected_hash:
            raise GoldReviewError(
                f"candidate SHA-256 mismatch for {case_id}"
            )
        if observed_hash in candidate_hashes:
            raise GoldReviewError(
                f"duplicate candidate SHA-256 for {case_id}"
            )
        case_ids.add(case_id)
        candidate_hashes.add(str(observed_hash))

    expected_pack_id = (
        f"{kind}-{_sha256_json(_pack_identity(pack))[:16]}"
    )
    if pack.get("packId") != expected_pack_id:
        raise GoldReviewError("review pack ID does not match candidates")
    if pack.get("packSha256") != pack_content_sha256(pack):
        raise GoldReviewError("review pack SHA-256 mismatch")
    return copy.deepcopy(dict(pack))


def load_trusted_reviewer_registry(path: Path) -> dict[str, str]:
    """Load a human-managed reviewer ID to key-fingerprint registry."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GoldReviewError(
            f"cannot read trusted reviewer registry: {path}"
        ) from error
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"schema", "reviewers"}
        or raw.get("schema") != REVIEWER_REGISTRY_SCHEMA
        or not isinstance(raw.get("reviewers"), list)
    ):
        raise GoldReviewError("trusted reviewer registry is malformed")
    reviewers: dict[str, str] = {}
    key_owners: dict[str, str] = {}
    for item in raw["reviewers"]:
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {"reviewerId", "reviewerKeyFingerprint"}
        ):
            raise GoldReviewError(
                "trusted reviewer registry entry is malformed"
            )
        reviewer_id = _required_text(
            item.get("reviewerId"),
            field="trusted reviewerId",
        )
        reviewer_key = _required_text(
            item.get("reviewerKeyFingerprint"),
            field="trusted reviewerKeyFingerprint",
        )
        if reviewer_id in reviewers:
            raise GoldReviewError(
                f"duplicate trusted reviewerId: {reviewer_id}"
            )
        if reviewer_key in key_owners:
            raise GoldReviewError(
                "trusted reviewer key fingerprint is not unique"
            )
        reviewers[reviewer_id] = reviewer_key
        key_owners[reviewer_key] = reviewer_id
    if not reviewers:
        raise GoldReviewError("trusted reviewer registry is empty")
    return reviewers


def _validate_evidence(
    evidence: object,
    *,
    verdict: str,
) -> None:
    if not isinstance(evidence, list) or not evidence:
        raise GoldReviewError("review requires at least one evidence item")
    freshness_values: list[str] = []
    for index, item in enumerate(evidence):
        if (
            not isinstance(item, Mapping)
            or set(item) != _EVIDENCE_FIELDS
        ):
            raise GoldReviewError(
                f"evidence {index + 1} fields do not match v1 contract"
            )
        _required_text(item.get("uri"), field=f"evidence {index + 1} uri")
        if not _is_sha256(item.get("sourceRevisionSha256")):
            raise GoldReviewError(
                "evidence sourceRevisionSha256 must be 64 lowercase "
                "hex digits"
            )
        freshness = str(item.get("freshness") or "").upper()
        if freshness not in _EVIDENCE_FRESHNESS:
            raise GoldReviewError("unsupported evidence freshness")
        freshness_values.append(freshness)
    if verdict == "CONFIRMED" and any(
        value != "FRESH" for value in freshness_values
    ):
        raise GoldReviewError(
            "confirmed review requires fresh evidence"
        )


def _validate_review_receipt(
    pack: Mapping[str, object],
    review: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(review, Mapping) or set(review) != _REVIEW_FIELDS:
        raise GoldReviewError(
            "review receipt fields do not match v1 contract"
        )
    if review.get("schema") != REVIEW_SCHEMA:
        raise GoldReviewError("unexpected review receipt schema")
    if (
        review.get("packId") != pack.get("packId")
        or review.get("packSha256") != pack.get("packSha256")
    ):
        raise GoldReviewError("review receipt pack identity mismatch")

    candidates = {
        str(candidate["caseId"]): candidate
        for candidate in pack["candidates"]
        if isinstance(candidate, Mapping)
    }
    case_id = _required_text(review.get("caseId"), field="caseId")
    candidate = candidates.get(case_id)
    if candidate is None:
        raise GoldReviewError(f"unknown review caseId: {case_id}")
    if review.get("candidateSha256") != candidate.get(
        "candidateSha256"
    ):
        raise GoldReviewError("review candidate SHA-256 mismatch")

    reviewer_id = _required_text(
        review.get("reviewerId"),
        field="reviewerId",
    )
    reviewer_key = _required_text(
        review.get("reviewerKeyFingerprint"),
        field="reviewerKeyFingerprint",
    )
    if (
        reviewer_id == pack.get("authorId")
        or reviewer_key == pack.get("authorKeyFingerprint")
    ):
        raise GoldReviewError("author cannot review own case")
    role = str(review.get("reviewerRole") or "").upper()
    if role not in _REVIEWER_ROLES:
        raise GoldReviewError("unsupported reviewer role")
    round_number = review.get("round")
    if (
        isinstance(round_number, bool)
        or not isinstance(round_number, int)
        or round_number < 1
    ):
        raise GoldReviewError("review round must be a positive integer")
    _validate_timestamp(review.get("reviewedAt"), field="reviewedAt")
    verdict = str(review.get("verdict") or "").upper()
    if verdict not in _VERDICTS:
        raise GoldReviewError("unsupported review verdict")
    if not isinstance(review.get("answer"), Mapping):
        raise GoldReviewError("review answer must be an object")
    _validate_evidence(review.get("evidence"), verdict=verdict)
    _required_text(review.get("rationale"), field="rationale")
    _required_text(review.get("toolVersion"), field="toolVersion")
    if review.get("contentSha256") != review_content_sha256(review):
        raise GoldReviewError("review content SHA-256 mismatch")
    return copy.deepcopy(dict(review))


def _review_answer_identity(review: Mapping[str, object]) -> str:
    return _sha256_json(
        {
            "verdict": str(review.get("verdict") or "").upper(),
            "answer": review.get("answer"),
        }
    )


def validate_review_set(
    pack: Mapping[str, object],
    reviews: Sequence[Mapping[str, object]],
    *,
    trusted_reviewers: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Validate independent review rounds without creating production gold."""

    normalized_pack = validate_review_pack(pack)
    normalized_reviews = [
        _validate_review_receipt(normalized_pack, review)
        for review in reviews
    ]

    receipt_keys: set[tuple[str, str, str, int]] = set()
    reviewer_keys: dict[str, str] = {}
    key_reviewers: dict[str, str] = {}
    for review in normalized_reviews:
        reviewer_id = str(review["reviewerId"])
        reviewer_key = str(review["reviewerKeyFingerprint"])
        receipt_key = (
            str(review["caseId"]),
            reviewer_id,
            str(review["reviewerRole"]),
            int(review["round"]),
        )
        if receipt_key in receipt_keys:
            raise GoldReviewError("duplicate review receipt")
        receipt_keys.add(receipt_key)
        previous_key = reviewer_keys.setdefault(reviewer_id, reviewer_key)
        if previous_key != reviewer_key:
            raise GoldReviewError(
                "reviewer ID uses multiple key fingerprints"
            )
        previous_reviewer = key_reviewers.setdefault(
            reviewer_key,
            reviewer_id,
        )
        if previous_reviewer != reviewer_id:
            raise GoldReviewError(
                "reviewer key fingerprint reused by multiple reviewer IDs"
            )
        if trusted_reviewers is not None:
            trusted_key = trusted_reviewers.get(reviewer_id)
            if trusted_key != reviewer_key:
                raise GoldReviewError(
                    f"untrusted reviewer identity: {reviewer_id}"
                )

    by_case: dict[str, list[dict[str, object]]] = defaultdict(list)
    for review in normalized_reviews:
        by_case[str(review["caseId"])].append(review)

    gaps: set[str] = set()
    reviewed_cases = 0
    if trusted_reviewers is None:
        gaps.add("TRUSTED_REVIEWER_REGISTRY_REQUIRED")

    for candidate in normalized_pack["candidates"]:
        case_id = str(candidate["caseId"])
        case_reviews = by_case.get(case_id, [])
        primary = [
            review
            for review in case_reviews
            if review["reviewerRole"] == "REVIEWER"
        ]
        reviewer_ids = {str(review["reviewerId"]) for review in primary}
        reviewer_keys_for_case = {
            str(review["reviewerKeyFingerprint"]) for review in primary
        }
        rounds = [int(review["round"]) for review in primary]
        if (
            len(primary) != len(reviewer_ids)
            or len(primary) != len(reviewer_keys_for_case)
        ):
            raise GoldReviewError(
                f"duplicate reviewer for {case_id}"
            )
        if len(rounds) != len(set(rounds)):
            raise GoldReviewError(
                f"duplicate review round for {case_id}"
            )
        if (
            len(primary) < 2
            or len(reviewer_ids) < 2
            or len(reviewer_keys_for_case) < 2
            or len(set(rounds)) < 2
        ):
            gaps.add(f"TWO_INDEPENDENT_REVIEWS_REQUIRED:{case_id}")
            continue

        answers = {_review_answer_identity(review) for review in primary}
        if len(answers) > 1:
            adjudicators = [
                review
                for review in case_reviews
                if review["reviewerRole"] == "ADJUDICATOR"
            ]
            if not adjudicators:
                gaps.add(
                    f"INDEPENDENT_ADJUDICATION_REQUIRED:{case_id}"
                )
                continue
            if len(adjudicators) != 1:
                raise GoldReviewError(
                    f"exactly one adjudicator is required for {case_id}"
                )
            adjudicator = adjudicators[0]
            if (
                str(adjudicator["reviewerId"]) in reviewer_ids
                or str(adjudicator["reviewerKeyFingerprint"])
                in reviewer_keys_for_case
            ):
                raise GoldReviewError(
                    "adjudicator must be independent from both reviewers"
                )
        reviewed_cases += 1

    status = READY_TO_FREEZE if not gaps else BLOCKED_BY_INDEPENDENT_REVIEW
    return {
        "schema": VALIDATION_SCHEMA,
        "packId": normalized_pack["packId"],
        "packSha256": normalized_pack["packSha256"],
        "kind": normalized_pack["kind"],
        "candidateCases": len(normalized_pack["candidates"]),
        "reviewedCases": reviewed_cases,
        "reviewCount": len(normalized_reviews),
        "status": status,
        "gaps": sorted(gaps),
    }


def validate_query_review_provenance(
    raw_case: Mapping[str, object],
    provenance: Mapping[str, object],
    *,
    trusted_reviewers: Mapping[str, str],
) -> dict[str, object]:
    """Bind one EMPIRICAL query answer to its blind pack and reviews."""

    if (
        not isinstance(provenance, Mapping)
        or set(provenance) != {"schema", "pack", "reviews"}
        or provenance.get("schema") != QUERY_REVIEW_PROVENANCE_SCHEMA
        or not isinstance(provenance.get("pack"), Mapping)
        or not isinstance(provenance.get("reviews"), list)
    ):
        raise GoldReviewError(
            "EMPIRICAL requires validated review provenance"
        )
    pack = provenance["pack"]
    reviews = provenance["reviews"]
    validation = validate_review_set(
        pack,
        reviews,
        trusted_reviewers=trusted_reviewers,
    )
    if (
        validation["status"] != READY_TO_FREEZE
        or pack.get("kind") != "query"
    ):
        raise GoldReviewError(
            "EMPIRICAL requires validated review provenance"
        )

    expected_candidate = query_candidate_from_gold_case(raw_case)
    case_id = str(expected_candidate["caseId"])
    candidate = next(
        (
            item
            for item in pack["candidates"]
            if isinstance(item, Mapping)
            and item.get("caseId") == case_id
        ),
        None,
    )
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("candidateSha256")
        != candidate_content_sha256(expected_candidate)
    ):
        raise GoldReviewError(
            "query review candidate does not match the gold case"
        )

    case_reviews = [
        review
        for review in reviews
        if isinstance(review, Mapping)
        and review.get("caseId") == case_id
    ]
    primary = [
        review
        for review in case_reviews
        if review.get("reviewerRole") == "REVIEWER"
    ]
    primary_answers = {
        _review_answer_identity(review): review.get("answer")
        for review in primary
    }
    if len(primary_answers) == 1:
        resolved_answer = next(iter(primary_answers.values()))
    else:
        adjudicators = [
            review
            for review in case_reviews
            if review.get("reviewerRole") == "ADJUDICATOR"
        ]
        resolved_answer = (
            adjudicators[0].get("answer")
            if len(adjudicators) == 1
            else None
        )
    if _sha256_json(resolved_answer) != _sha256_json(
        raw_case.get("expected")
    ):
        raise GoldReviewError(
            "reviewed query answer does not match expected gold"
        )

    empirical_evidence = [
        item
        for review in case_reviews
        for item in review.get("evidence", [])
        if isinstance(item, Mapping)
        and str(item.get("freshness") or "").upper() == "FRESH"
        and str(item.get("uri") or "").startswith(
            ("runtime://", "empirical://")
        )
    ]
    if not empirical_evidence:
        raise GoldReviewError(
            "EMPIRICAL review requires fresh runtime evidence"
        )
    return validation


__all__ = [
    "BLOCKED_BY_INDEPENDENT_REVIEW",
    "PACK_SCHEMA",
    "QUERY_REVIEW_PROVENANCE_SCHEMA",
    "READY_TO_FREEZE",
    "REVIEW_SCHEMA",
    "REVIEWER_REGISTRY_SCHEMA",
    "VALIDATION_SCHEMA",
    "GoldReviewError",
    "build_query_review_pack",
    "build_review_pack",
    "candidate_content_sha256",
    "load_trusted_reviewer_registry",
    "pack_content_sha256",
    "query_candidate_from_gold_case",
    "review_content_sha256",
    "validate_query_review_provenance",
    "validate_review_pack",
    "validate_review_set",
]
