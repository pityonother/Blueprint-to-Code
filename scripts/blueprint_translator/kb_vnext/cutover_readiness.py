"""Fail-closed burn-in evidence required in addition to quality gates."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Mapping


BURN_IN_ATTESTATION_SCHEMA = "ark-kb-burn-in-attestation/v1"
BURN_IN_POLICY_VERSION = "ark-kb-burn-in-policy/v1"
MINIMUM_SEALED_BUILDS = 3
REQUIRED_INCREMENTAL_SCENARIOS = (
    "blueprintModified",
    "blueprintAdded",
    "blueprintDeleted",
    "registrationTargetChanged",
    "classParentChanged",
    "nativeEvidenceUpdated",
    "runtimeSummaryUpdated",
    "workerCrash",
    "narrowGateFailure",
    "pointerPreSwapCrash",
    "concurrentReaders",
    "unchangedCacheHit",
)

_BUILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _nonempty(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _timestamp(value: object, *, label: str) -> str:
    text = _nonempty(value, label=label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return text


def _passed(value: object, *, label: str) -> None:
    if value is not True:
        raise ValueError(f"{label} must be true")


def _exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    *,
    label: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields are invalid")


def validate_burn_in_attestation(
    value: object,
) -> Mapping[str, object]:
    """Validate operational cutover evidence without inventing its contents."""

    attestation = _mapping(value, label="burn-in attestation")
    _exact_keys(
        attestation,
        {
            "schema",
            "policyVersion",
            "status",
            "attestedAt",
            "toolVersion",
            "review",
            "sealedSnapshots",
            "legacyVnextDiffDisposition",
            "rollbackDrill",
            "concurrentReaderDrill",
            "incrementalProduction",
        },
        label="burn-in attestation",
    )
    if attestation.get("schema") != BURN_IN_ATTESTATION_SCHEMA:
        raise ValueError("burn-in attestation schema is invalid")
    if attestation.get("policyVersion") != BURN_IN_POLICY_VERSION:
        raise ValueError("burn-in policy version is invalid")
    if attestation.get("status") != "PASSED":
        raise ValueError("burn-in attestation status must be PASSED")
    _timestamp(attestation.get("attestedAt"), label="attestedAt")
    _nonempty(attestation.get("toolVersion"), label="toolVersion")

    review = _mapping(attestation.get("review"), label="review")
    _exact_keys(
        review,
        {"reviewerType", "reviewerId", "reviewedAt", "decision"},
        label="review",
    )
    if review.get("reviewerType") != "HUMAN_OPERATOR":
        raise ValueError("burn-in review requires a human operator")
    _nonempty(review.get("reviewerId"), label="review.reviewerId")
    _timestamp(review.get("reviewedAt"), label="review.reviewedAt")
    if review.get("decision") != "APPROVED":
        raise ValueError("burn-in review decision must be APPROVED")

    raw_snapshots = attestation.get("sealedSnapshots")
    if not isinstance(raw_snapshots, list):
        raise ValueError("sealedSnapshots must be an array")
    if len(raw_snapshots) < MINIMUM_SEALED_BUILDS:
        raise ValueError("at least three sealed snapshots are required")
    build_ids: set[str] = set()
    report_hashes: set[str] = set()
    for index, raw_snapshot in enumerate(raw_snapshots):
        snapshot = _mapping(
            raw_snapshot,
            label=f"sealedSnapshots[{index}]",
        )
        _exact_keys(
            snapshot,
            {
                "buildId",
                "qualityReportSha256",
                "passedAt",
                "qualityReportCutoverEligible",
                "sealedInSnapshotManifest",
            },
            label=f"sealedSnapshots[{index}]",
        )
        build_id = _nonempty(
            snapshot.get("buildId"),
            label=f"sealedSnapshots[{index}].buildId",
        )
        if not _BUILD_ID.fullmatch(build_id) or build_id in build_ids:
            raise ValueError("sealed snapshot build IDs must be valid and unique")
        build_ids.add(build_id)
        report_sha = str(snapshot.get("qualityReportSha256") or "").lower()
        if (
            not _SHA256.fullmatch(report_sha)
            or report_sha in report_hashes
        ):
            raise ValueError("sealed snapshot quality report SHA-256 is invalid")
        report_hashes.add(report_sha)
        _timestamp(
            snapshot.get("passedAt"),
            label=f"sealedSnapshots[{index}].passedAt",
        )
        _passed(
            snapshot.get("qualityReportCutoverEligible"),
            label=(
                f"sealedSnapshots[{index}]"
                ".qualityReportCutoverEligible"
            ),
        )
        _passed(
            snapshot.get("sealedInSnapshotManifest"),
            label=(
                f"sealedSnapshots[{index}].sealedInSnapshotManifest"
            ),
        )

    dispositions = _mapping(
        attestation.get("legacyVnextDiffDisposition"),
        label="legacyVnextDiffDisposition",
    )
    _exact_keys(
        dispositions,
        {
            "complete",
            "undispositioned",
            "wrongAnswers",
            "staleLeaks",
            "candidateCompletions",
        },
        label="legacyVnextDiffDisposition",
    )
    _passed(
        dispositions.get("complete"),
        label="legacyVnextDiffDisposition.complete",
    )
    for field in (
        "undispositioned",
        "wrongAnswers",
        "staleLeaks",
        "candidateCompletions",
    ):
        if type(dispositions.get(field)) is not int or dispositions[field] != 0:
            raise ValueError(
                f"legacyVnextDiffDisposition.{field} must be zero"
            )

    rollback = _mapping(
        attestation.get("rollbackDrill"),
        label="rollbackDrill",
    )
    _exact_keys(
        rollback,
        {"passed", "fromBuildId", "toBuildId", "completedAt"},
        label="rollbackDrill",
    )
    _passed(rollback.get("passed"), label="rollbackDrill.passed")
    from_build = _nonempty(
        rollback.get("fromBuildId"),
        label="rollbackDrill.fromBuildId",
    )
    to_build = _nonempty(
        rollback.get("toBuildId"),
        label="rollbackDrill.toBuildId",
    )
    if (
        not _BUILD_ID.fullmatch(from_build)
        or not _BUILD_ID.fullmatch(to_build)
        or from_build == to_build
        or from_build not in build_ids
        or to_build not in build_ids
    ):
        raise ValueError("rollback drill build IDs are invalid")
    _timestamp(
        rollback.get("completedAt"),
        label="rollbackDrill.completedAt",
    )

    readers = _mapping(
        attestation.get("concurrentReaderDrill"),
        label="concurrentReaderDrill",
    )
    _exact_keys(
        readers,
        {"passed", "mixedBuildObservations", "completedAt"},
        label="concurrentReaderDrill",
    )
    _passed(
        readers.get("passed"),
        label="concurrentReaderDrill.passed",
    )
    if (
        type(readers.get("mixedBuildObservations")) is not int
        or readers["mixedBuildObservations"] != 0
    ):
        raise ValueError(
            "concurrentReaderDrill.mixedBuildObservations must be zero"
        )
    _timestamp(
        readers.get("completedAt"),
        label="concurrentReaderDrill.completedAt",
    )

    incremental = _mapping(
        attestation.get("incrementalProduction"),
        label="incrementalProduction",
    )
    _exact_keys(
        incremental,
        {"passed", "scenarios"},
        label="incrementalProduction",
    )
    _passed(
        incremental.get("passed"),
        label="incrementalProduction.passed",
    )
    scenarios = _mapping(
        incremental.get("scenarios"),
        label="incrementalProduction.scenarios",
    )
    _exact_keys(
        scenarios,
        set(REQUIRED_INCREMENTAL_SCENARIOS),
        label="incrementalProduction.scenarios",
    )
    missing = [
        name
        for name in REQUIRED_INCREMENTAL_SCENARIOS
        if scenarios.get(name) is not True
    ]
    if missing:
        raise ValueError(
            "incremental production scenarios are incomplete: "
            + ", ".join(missing)
        )
    return attestation
