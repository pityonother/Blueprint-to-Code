"""Fail-closed burn-in evidence required in addition to quality gates."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
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


def _timestamp(value: object, *, label: str) -> datetime:
    text = _nonempty(value, label=label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


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


def _read_json(path: Path, *, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    return _mapping(value, label=label)


def _sealed_report_path(
    snapshot_dir: Path,
    report_uri: object,
) -> Path:
    text = _nonempty(report_uri, label="quality report URI")
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ValueError("quality report URI must stay inside the snapshot")
    root = snapshot_dir.resolve()
    path = (root / Path(*posix.parts)).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("quality report URI escapes the snapshot")
    return path


def validate_burn_in_snapshot_history(
    attestation: Mapping[str, object],
    *,
    snapshot_root: Path,
) -> None:
    """Bind burn-in claims to the latest consecutive immutable snapshots."""

    validate_burn_in_attestation(attestation)
    snapshots_root = snapshot_root.resolve() / "snapshots"
    if not snapshots_root.is_dir():
        raise ValueError("burn-in snapshot history is unavailable")

    published: list[tuple[datetime, str, Path, Mapping[str, object]]] = []
    for manifest_path in sorted(snapshots_root.glob("*/manifest.json")):
        manifest = _read_json(
            manifest_path,
            label="published snapshot manifest",
        )
        build_id = str(manifest.get("buildId") or "")
        if (
            manifest.get("schema") != "ark-kb-vnext-snapshot/v1"
            or build_id != manifest_path.parent.name
            or not _BUILD_ID.fullmatch(build_id)
        ):
            raise ValueError("published snapshot history is invalid")
        generated_at = _timestamp(
            manifest.get("generatedAt"),
            label=f"published snapshot {build_id} generatedAt",
        )
        published.append(
            (generated_at, build_id, manifest_path.parent, manifest)
        )

    raw_snapshots = attestation["sealedSnapshots"]
    if len(published) < len(raw_snapshots):
        raise ValueError("burn-in snapshot history is incomplete")
    published.sort(key=lambda item: (item[0], item[1]))
    expected_ids = [
        build_id
        for _generated_at, build_id, _path, _manifest in published[
            -len(raw_snapshots) :
        ]
    ]
    attested_ids = [
        str(snapshot["buildId"])
        for snapshot in raw_snapshots
    ]
    if attested_ids != expected_ids:
        raise ValueError(
            "burn-in snapshots must be the latest consecutive builds"
        )

    by_id = {item[1]: item for item in published}
    for raw_snapshot in raw_snapshots:
        build_id = str(raw_snapshot["buildId"])
        _generated_at, _ignored, snapshot_dir, manifest = by_id[build_id]
        quality = _mapping(
            manifest.get("qualityGates"),
            label=f"published snapshot {build_id} qualityGates",
        )
        if (
            quality.get("qualityReportCutoverEligible") is not True
            or quality.get("sealedInSnapshotManifest") is not True
        ):
            raise ValueError(
                "burn-in snapshot quality was not sealed as passing"
            )
        report_path = _sealed_report_path(
            snapshot_dir,
            quality.get("reportUri"),
        )
        try:
            report_bytes = report_path.read_bytes()
        except OSError as exc:
            raise ValueError("burn-in quality report is unavailable") from exc
        report_sha = hashlib.sha256(report_bytes).hexdigest()
        if (
            report_sha != quality.get("sha256")
            or report_sha != raw_snapshot["qualityReportSha256"]
        ):
            raise ValueError("burn-in quality report SHA-256 is invalid")
        report = _read_json(
            report_path,
            label=f"published snapshot {build_id} quality report",
        )
        summary = _mapping(
            report.get("summary"),
            label=f"published snapshot {build_id} quality summary",
        )
        if (
            str(report.get("buildId") or "") != build_id
            or summary.get("cutoverEligible") is not True
            or int(summary.get("failed") or 0) != 0
        ):
            raise ValueError(
                "burn-in quality report is not a sealed passing result"
            )
