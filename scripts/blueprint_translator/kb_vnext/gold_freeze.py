"""Proposal-only Gold freeze built on signed review v2 validation.

This module never writes production Gold, creates reviewer evidence, or
approves a freeze. The CLI is the only supported orchestration entry point;
pure helpers are exposed so deterministic diffs can be tested without
simulating production reviewers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from .benchmark import validate_benchmark_gold_payload
from .gold_review import (
    GoldReviewError,
    query_candidate_from_gold_case,
    validate_review_pack,
)
from .gold_review_v2 import (
    GoldReviewV2Error,
    GoldReviewValidation,
    VerifiedGoldReview,
    parse_strict_json_bytes,
    validate_gold_review_set_v2,
)
from .signed_receipts import PRODUCTION


FREEZE_PROPOSAL_SCHEMA = "ark-kb-gold-freeze-proposal/v1"
FREEZE_VALIDATION_SCHEMA = "ark-kb-gold-freeze-validation/v1"
FREEZE_PROVENANCE_SCHEMA = "ark-kb-gold-freeze-provenance/v1"
PROPOSAL_READY = "PROPOSAL_READY"
BLOCKED_BY_SIGNED_FREEZE_APPROVAL = (
    "BLOCKED_BY_SIGNED_FREEZE_APPROVAL"
)
BLOCKED_UNSUPPORTED_GOLD_FREEZE_KIND = (
    "BLOCKED_UNSUPPORTED_GOLD_FREEZE_KIND"
)
SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED = (
    "SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED"
)
QUERY_GOLD_TARGET_RELATIVE_PATH = (
    "tests/fixtures/kb_query_gold_set.v1.json"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUERY_EXPECTED_REQUIRED = frozenset(
    {
        "route",
        "identityUri",
        "facts",
        "relationships",
        "gapCodes",
        "mustContainEvidence",
        "semanticExpectation",
    }
)
_QUERY_EXPECTED_OPTIONAL = frozenset(
    {
        "status",
        "identityStatus",
        "identityConfidence",
        "identityEvidence",
    }
)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(child) for child in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _plain_json(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise GoldReviewV2Error(
            "Gold freeze content must be finite JSON data"
        ) from error


def _json_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _required_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise GoldReviewV2Error(
            f"{field} must be a lowercase SHA-256 hex digest"
        )
    return value


def _validated_pack(pack: Mapping[str, object]) -> dict[str, object]:
    try:
        return validate_review_pack(pack)
    except GoldReviewError as error:
        raise GoldReviewV2Error(str(error)) from error


def _blocked_result(
    validation: GoldReviewValidation,
    *,
    status: str | None = None,
    extra_gaps: Sequence[str] = (),
) -> dict[str, object]:
    gaps = sorted(set((*validation.gaps, *extra_gaps)))
    adjudication = [gap for gap in gaps if "ADJUDICATION" in gap]
    unresolved = [gap for gap in gaps if gap not in adjudication]
    return {
        "schema": FREEZE_VALIDATION_SCHEMA,
        "status": status or validation.status,
        "proposalReady": False,
        "packId": validation.pack_id,
        "packSha256": validation.pack_sha256,
        "kind": validation.kind,
        "trustContext": validation.trust_context,
        "reviewReceiptSetSha256": validation.receipt_set_sha256,
        "unresolvedGaps": unresolved,
        "adjudicationGaps": adjudication,
        "applyAllowed": False,
        "productionGoldWritten": False,
    }


def signed_freeze_approval_blocker() -> dict[str, object]:
    """Return the permanent Stage13C apply blocker."""

    return {
        "schema": FREEZE_VALIDATION_SCHEMA,
        "status": BLOCKED_BY_SIGNED_FREEZE_APPROVAL,
        "proposalReady": False,
        "unresolvedGaps": ["SIGNED_FREEZE_APPROVAL_REQUIRED"],
        "adjudicationGaps": [],
        "applyAllowed": False,
        "productionGoldWritten": False,
    }


def _query_cases(target: Mapping[str, object]) -> list[Mapping[str, object]]:
    if target.get("schema") != "ark-kb-query-gold-set/v1":
        raise GoldReviewV2Error(
            "Gold freeze target must be ark-kb-query-gold-set/v1"
        )
    raw_cases = target.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise GoldReviewV2Error("Gold freeze target requires cases")
    cases: list[Mapping[str, object]] = []
    identifiers: set[str] = set()
    for index, case in enumerate(raw_cases):
        if not isinstance(case, Mapping):
            raise GoldReviewV2Error(
                f"Gold freeze target case {index + 1} must be an object"
            )
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise GoldReviewV2Error(
                f"Gold freeze target case {index + 1} requires id"
            )
        if case_id in identifiers:
            raise GoldReviewV2Error(
                f"Gold freeze target has duplicate case id: {case_id}"
            )
        identifiers.add(case_id)
        cases.append(case)
    return cases


def validate_freeze_bindings(
    pack: Mapping[str, object],
    *,
    source_manifest_bytes: bytes,
    expected_source_manifest_sha256: str,
    target_bytes: bytes,
    expected_target_sha256: str,
) -> Mapping[str, object]:
    """Bind the exact review source and tracked target byte snapshots."""

    normalized_pack = _validated_pack(pack)
    if normalized_pack["kind"] != "query":
        raise GoldReviewV2Error(BLOCKED_UNSUPPORTED_GOLD_FREEZE_KIND)
    if normalized_pack["selectionRule"] != "MANUAL_FIXED_ALL_CASES":
        raise GoldReviewV2Error(
            "query Gold freeze requires MANUAL_FIXED_ALL_CASES"
        )
    pack_source_sha = _required_sha256(
        normalized_pack.get("sourceManifestSha256"),
        field="pack sourceManifestSha256",
    )
    expected_source_sha = _required_sha256(
        expected_source_manifest_sha256,
        field="expected source manifest SHA-256",
    )
    expected_target_sha = _required_sha256(
        expected_target_sha256,
        field="expected target SHA-256",
    )
    source_sha = hashlib.sha256(source_manifest_bytes).hexdigest()
    target_sha = hashlib.sha256(target_bytes).hexdigest()
    if expected_source_sha != pack_source_sha or source_sha != pack_source_sha:
        raise GoldReviewV2Error(
            "source manifest raw bytes do not match pack "
            "sourceManifestSha256"
        )
    if target_sha != expected_target_sha:
        raise GoldReviewV2Error(
            "Gold target raw bytes do not match expected target SHA-256"
        )
    if expected_target_sha != pack_source_sha:
        raise GoldReviewV2Error(
            "Gold target changed since review pack export"
        )

    source = parse_strict_json_bytes(
        source_manifest_bytes,
        field="Gold freeze source manifest",
    )
    target = (
        source
        if source_manifest_bytes == target_bytes
        else parse_strict_json_bytes(
            target_bytes,
            field="Gold freeze target",
        )
    )
    if not isinstance(source, Mapping) or not isinstance(target, Mapping):
        raise GoldReviewV2Error(
            "Gold freeze source and target must be JSON objects"
        )
    source_cases = _query_cases(source)
    target_cases = _query_cases(target)
    source_ids = [str(case["id"]) for case in source_cases]
    target_ids = [str(case["id"]) for case in target_cases]
    if source_ids != target_ids:
        raise GoldReviewV2Error(
            "Gold freeze source and target case order differs"
        )

    expected_candidates = {
        str(candidate["caseId"]): candidate
        for candidate in (
            query_candidate_from_gold_case(case) for case in target_cases
        )
    }
    pack_candidates = normalized_pack.get("candidates")
    if not isinstance(pack_candidates, list):
        raise GoldReviewV2Error("review pack requires candidates")
    observed_candidates = {
        str(candidate["caseId"]): {
            "caseId": candidate["caseId"],
            "payload": candidate["payload"],
        }
        for candidate in pack_candidates
        if isinstance(candidate, Mapping)
    }
    if observed_candidates != expected_candidates:
        raise GoldReviewV2Error(
            "review pack candidates do not match bound Gold target"
        )
    return target


def _query_expected_from_artifact(
    artifact: Mapping[str, object],
    *,
    case_id: str,
) -> dict[str, object]:
    if artifact.get("schema") != "ark-kb-gold-review-artifact/v2":
        raise GoldReviewV2Error(
            f"{case_id}: resolved artifact schema must be v2"
        )
    if artifact.get("caseId") != case_id:
        raise GoldReviewV2Error(
            f"{case_id}: resolved artifact caseId mismatch"
        )
    verdict = artifact.get("verdict")
    if verdict not in {"CONFIRMED", "EXPECTED_GAP"}:
        raise GoldReviewV2Error(
            f"{case_id}: resolved artifact verdict is not freezeable"
        )
    answer = artifact.get("answer")
    if not isinstance(answer, Mapping) or set(answer) != {"queryExpected"}:
        raise GoldReviewV2Error(
            f"{case_id}: signed answer must contain only queryExpected"
        )
    expected = answer.get("queryExpected")
    if not isinstance(expected, Mapping):
        raise GoldReviewV2Error(
            f"{case_id}: queryExpected must be an object"
        )
    fields = frozenset(expected)
    missing = sorted(_QUERY_EXPECTED_REQUIRED - fields)
    unexpected = sorted(
        fields - _QUERY_EXPECTED_REQUIRED - _QUERY_EXPECTED_OPTIONAL
    )
    if missing or unexpected:
        raise GoldReviewV2Error(
            f"{case_id}: queryExpected fields are invalid "
            f"(missing={missing}; unexpected={unexpected})"
        )
    if (
        not isinstance(expected.get("route"), str)
        or not expected["route"]
        or (
            expected.get("identityUri") is not None
            and (
                not isinstance(expected.get("identityUri"), str)
                or not expected["identityUri"]
            )
        )
        or not isinstance(expected.get("facts"), list)
        or any(not isinstance(fact, Mapping) for fact in expected["facts"])
        or not isinstance(expected.get("relationships"), list)
        or any(
            not isinstance(relationship, Mapping)
            for relationship in expected["relationships"]
        )
        or not isinstance(expected.get("gapCodes"), list)
        or any(
            not isinstance(gap, str) or not gap
            for gap in expected["gapCodes"]
        )
        or len(expected["gapCodes"]) != len(set(expected["gapCodes"]))
        or not isinstance(expected.get("mustContainEvidence"), bool)
        or expected.get("semanticExpectation")
        not in {"EXACT", "GAP_ONLY", "IDENTITY_ONLY"}
        or any(
            field in expected
            and (
                not isinstance(expected[field], str)
                or not expected[field]
            )
            for field in ("status", "identityStatus", "identityConfidence")
        )
        or (
            "identityEvidence" in expected
            and not isinstance(expected["identityEvidence"], Mapping)
        )
    ):
        raise GoldReviewV2Error(
            f"{case_id}: queryExpected has invalid typed fields"
        )
    if (
        verdict == "EXPECTED_GAP"
        and expected.get("semanticExpectation") != "GAP_ONLY"
    ):
        raise GoldReviewV2Error(
            f"{case_id}: EXPECTED_GAP requires GAP_ONLY queryExpected"
        )
    plain = _plain_json(expected)
    if not isinstance(plain, dict):
        raise AssertionError("validated queryExpected must remain an object")
    _canonical_json_bytes(plain)
    return plain


def build_deterministic_query_diff(
    target_bytes: bytes,
    resolved_artifact_bytes: Mapping[str, bytes],
) -> dict[str, object]:
    """Build a deterministic logical patch from immutable artifact bytes."""

    raw_target = parse_strict_json_bytes(
        target_bytes,
        field="Gold freeze target",
    )
    if not isinstance(raw_target, Mapping):
        raise GoldReviewV2Error("Gold freeze target must be a JSON object")
    cases = _query_cases(raw_target)
    proposed_payload = _plain_json(raw_target)
    if not isinstance(proposed_payload, dict):
        raise AssertionError("Gold freeze target must remain an object")
    proposed_cases = proposed_payload.get("cases")
    if not isinstance(proposed_cases, list):
        raise AssertionError("Gold freeze cases must remain a list")
    by_id = {str(case["id"]): (index, case) for index, case in enumerate(cases)}
    if set(resolved_artifact_bytes) != set(by_id):
        raise GoldReviewV2Error(
            "resolved artifact set must exactly cover target cases"
        )

    changes: list[dict[str, object]] = []
    patch: list[dict[str, object]] = []
    for case_id in sorted(by_id):
        index, old_case = by_id[case_id]
        artifact_bytes = resolved_artifact_bytes[case_id]
        if not isinstance(artifact_bytes, bytes):
            raise GoldReviewV2Error(
                f"{case_id}: resolved artifact bytes are required"
            )
        raw_artifact = parse_strict_json_bytes(
            artifact_bytes,
            field=f"resolved Gold review artifact {case_id}",
        )
        if not isinstance(raw_artifact, Mapping):
            raise GoldReviewV2Error(
                f"{case_id}: resolved artifact must be an object"
            )
        new_expected = _query_expected_from_artifact(
            raw_artifact,
            case_id=case_id,
        )
        new_case = dict(_plain_json(old_case))
        old_status = str(old_case.get("reviewStatus") or "")
        new_case["expected"] = new_expected
        proposed_cases[index] = new_case
        if _canonical_json_bytes(old_case.get("expected")) == (
            _canonical_json_bytes(new_expected)
        ):
            continue
        changes.append(
            {
                "caseId": case_id,
                "caseIndex": index,
                "verdict": raw_artifact["verdict"],
                "artifactSha256": hashlib.sha256(
                    artifact_bytes
                ).hexdigest(),
                "oldCaseSha256": _json_sha256(old_case),
                "newCaseSha256": _json_sha256(new_case),
                "reviewStatus": old_status,
                "reviewStatusPreserved": True,
            }
        )
        patch.append(
            {
                "op": "replace",
                "path": f"/cases/{index}/expected",
                "value": new_expected,
            }
        )
    try:
        validate_benchmark_gold_payload(proposed_payload)
    except ValueError as error:
        raise GoldReviewV2Error(
            f"proposed Gold payload is not canonical: {error}"
        ) from error
    return {
        "caseChanges": changes,
        "jsonPatch": patch,
        "expectedGateDelta": {
            "changedCaseCount": len(changes),
            "signedV2ReviewedCasesDelta": 0,
            "signedV2ProvenanceCaseCount": len(by_id),
            "fixtureExactCasesDelta": 0,
            "qualityGateEvaluation": "PENDING_FULL_SNAPSHOT_REBUILD",
            "cutoverEligibleClaimed": False,
        },
    }


def validate_and_propose_gold_freeze(
    pack: Mapping[str, object],
    receipts: Sequence[Mapping[str, object]],
    *,
    registry: Mapping[str, object] | None,
    expected_registry_sha256: str | None,
    expected_pack_author_key_fingerprint: str | None,
    artifact_root: Path | None,
    source_manifest_bytes: bytes,
    expected_source_manifest_sha256: str,
    target_bytes: bytes,
    expected_target_sha256: str,
    target_relative_path: str,
    trust_context: str = PRODUCTION,
) -> dict[str, object]:
    """Validate raw signed inputs, then create a non-writing proposal."""

    validation = validate_gold_review_set_v2(
        pack,
        receipts,
        registry=registry,
        expected_registry_sha256=expected_registry_sha256,
        expected_pack_author_key_fingerprint=(
            expected_pack_author_key_fingerprint
        ),
        artifact_root=artifact_root,
        required_case_ids=None,
        trust_context=trust_context,
    )

    if (
        not validation.production_gold_eligible
        or validation.trust_context != PRODUCTION
    ):
        validate_freeze_bindings(
            pack,
            source_manifest_bytes=source_manifest_bytes,
            expected_source_manifest_sha256=(
                expected_source_manifest_sha256
            ),
            target_bytes=target_bytes,
            expected_target_sha256=expected_target_sha256,
        )
        return _blocked_result(validation)
    normalized_pack = _validated_pack(pack)
    if (
        validation.pack_id != normalized_pack["packId"]
        or validation.pack_sha256 != normalized_pack["packSha256"]
        or validation.kind != normalized_pack["kind"]
    ):
        raise GoldReviewV2Error(
            "Gold freeze validation does not match review pack"
        )
    if (
        not validation.contract_complete
        or validation.required_cases != validation.candidate_cases
        or validation.reviewed_cases != validation.candidate_cases
    ):
        return _blocked_result(
            validation,
            extra_gaps=("FULL_PACK_REVIEW_REQUIRED",),
        )
    if normalized_pack["kind"] != "query":
        return _blocked_result(
            validation,
            status=BLOCKED_UNSUPPORTED_GOLD_FREEZE_KIND,
            extra_gaps=(BLOCKED_UNSUPPORTED_GOLD_FREEZE_KIND,),
        )
    if target_relative_path != QUERY_GOLD_TARGET_RELATIVE_PATH:
        raise GoldReviewV2Error(
            "query Gold freeze targetRelativePath is not allowlisted"
        )
    receipt_set_sha = _required_sha256(
        validation.receipt_set_sha256,
        field="review receipt set SHA-256",
    )
    validate_freeze_bindings(
        normalized_pack,
        source_manifest_bytes=source_manifest_bytes,
        expected_source_manifest_sha256=(
            expected_source_manifest_sha256
        ),
        target_bytes=target_bytes,
        expected_target_sha256=expected_target_sha256,
    )

    artifact_bytes: dict[str, bytes] = {}
    artifact_bindings: list[dict[str, object]] = []
    registry_digests: set[str] = set()
    for case_id in sorted(validation.resolved_reviews):
        review = validation.resolved_reviews[case_id]
        if not isinstance(review, VerifiedGoldReview):
            raise GoldReviewV2Error(
                f"{case_id}: verified resolved review is required"
            )
        observed_artifact_sha = hashlib.sha256(
            review.artifact_bytes
        ).hexdigest()
        if observed_artifact_sha != review.artifact_sha256:
            raise GoldReviewV2Error(
                f"{case_id}: immutable resolved artifact SHA mismatch"
            )
        artifact_bytes[case_id] = review.artifact_bytes
        registry_digests.add(
            _required_sha256(
                review.registry_version_sha256,
                field=f"{case_id} registry version SHA-256",
            )
        )
        artifact_bindings.append(
            {
                "caseId": case_id,
                "receiptId": review.receipt_id,
                "artifactUri": review.artifact_uri,
                "artifactSha256": review.artifact_sha256,
                "signedPayloadSha256": review.signed_payload_sha256,
                "reviewerKeyFingerprint": (
                    review.public_key_fingerprint
                ),
                "resolutionRound": review.round_number,
            }
        )
    if len(registry_digests) != 1:
        raise GoldReviewV2Error(
            "resolved reviews must bind one trusted reviewer registry"
        )
    diff = build_deterministic_query_diff(target_bytes, artifact_bytes)
    provenance = {
        "schema": FREEZE_PROVENANCE_SCHEMA,
        "packId": validation.pack_id,
        "packSha256": validation.pack_sha256,
        "sourceManifestSha256": normalized_pack["sourceManifestSha256"],
        "sourceManifestRawSha256": hashlib.sha256(
            source_manifest_bytes
        ).hexdigest(),
        "targetRelativePath": target_relative_path,
        "targetRawSha256": hashlib.sha256(target_bytes).hexdigest(),
        "expectedTargetRawSha256": expected_target_sha256,
        "registryVersionSha256": next(iter(registry_digests)),
        "reviewReceiptSetSha256": receipt_set_sha,
        "signedV2ProvenanceCaseCount": len(artifact_bindings),
        "caseArtifactBindings": artifact_bindings,
    }
    proposal_content: dict[str, object] = {
        "schema": FREEZE_PROPOSAL_SCHEMA,
        "status": PROPOSAL_READY,
        "proposalReady": True,
        "packId": validation.pack_id,
        "packSha256": validation.pack_sha256,
        "kind": "query",
        "sourceManifestSha256": normalized_pack["sourceManifestSha256"],
        "reviewReceiptSetSha256": receipt_set_sha,
        **diff,
        "unresolvedGaps": [],
        "adjudicationGaps": [],
        "provenance": provenance,
        "applyBlockers": [
            SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED
        ],
        "applyAllowed": False,
        "productionGoldWritten": False,
    }
    proposal_content["proposalId"] = _json_sha256(proposal_content)
    return proposal_content


__all__ = [
    "BLOCKED_BY_SIGNED_FREEZE_APPROVAL",
    "BLOCKED_UNSUPPORTED_GOLD_FREEZE_KIND",
    "FREEZE_PROPOSAL_SCHEMA",
    "FREEZE_PROVENANCE_SCHEMA",
    "FREEZE_VALIDATION_SCHEMA",
    "PROPOSAL_READY",
    "QUERY_GOLD_TARGET_RELATIVE_PATH",
    "SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED",
    "build_deterministic_query_diff",
    "signed_freeze_approval_blocker",
    "validate_and_propose_gold_freeze",
    "validate_freeze_bindings",
]
