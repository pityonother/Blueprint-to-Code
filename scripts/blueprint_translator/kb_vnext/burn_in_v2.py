"""Read-only verification for signed, artifact-bound burn-in v2 evidence.

This module does not create identities, keys, receipts, runtime evidence, or
snapshot state.  It validates caller-supplied evidence and returns immutable
verified bytes for a later staging layer.  Snapshot cutover remains outside
this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType

from .cutover_readiness import REQUIRED_INCREMENTAL_SCENARIOS
from .signed_receipts import (
    PRODUCTION,
    ReceiptReplayGuard,
    SignedReceiptError,
    VerifiedSignedReceipt,
    canonical_json_bytes,
    verify_signed_receipt,
)


BURN_IN_ATTESTATION_V2_SCHEMA = "ark-kb-burn-in-attestation/v2"
BURN_IN_EVIDENCE_BUNDLE_V2_SCHEMA = "ark-kb-burn-in-evidence-bundle/v2"
BURN_IN_POLICY_V2 = "ark-kb-burn-in-policy/v2"
BURN_IN_OPERATOR_ROLE = "BURN_IN_OPERATOR"
MINIMUM_SEALED_BUILDS = 3
PUBLISHED_INCREMENTAL_SCENARIOS = frozenset(
    {
        "blueprintModified",
        "blueprintAdded",
        "blueprintDeleted",
        "registrationTargetChanged",
        "classParentChanged",
        "nativeEvidenceUpdated",
        "runtimeSummaryUpdated",
    }
)
NO_PUBLISH_INCREMENTAL_SCENARIOS = frozenset(
    {
        "workerCrash",
        "narrowGateFailure",
        "pointerPreSwapCrash",
    }
)
_SPECIAL_INCREMENTAL_SCENARIOS = frozenset({"concurrentReaders", "unchangedCacheHit"})
if (
    PUBLISHED_INCREMENTAL_SCENARIOS
    | NO_PUBLISH_INCREMENTAL_SCENARIOS
    | _SPECIAL_INCREMENTAL_SCENARIOS
) != frozenset(REQUIRED_INCREMENTAL_SCENARIOS):
    raise RuntimeError("burn-in v2 E4 scenario policy is incomplete")
if (
    PUBLISHED_INCREMENTAL_SCENARIOS & NO_PUBLISH_INCREMENTAL_SCENARIOS
    or PUBLISHED_INCREMENTAL_SCENARIOS & _SPECIAL_INCREMENTAL_SCENARIOS
    or NO_PUBLISH_INCREMENTAL_SCENARIOS & _SPECIAL_INCREMENTAL_SCENARIOS
):
    raise RuntimeError("burn-in v2 E4 scenario policy overlaps")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]{0,127}$")
_ATTESTATION_KEYS = frozenset(
    {
        "schema",
        "policyVersion",
        "burnInRunId",
        "candidateBuildId",
        "status",
        "evidenceBundleUri",
        "evidenceBundleSha256",
        "operatorApproval",
    }
)
_BUNDLE_KEYS = frozenset(
    {
        "schema",
        "policyVersion",
        "burnInRunId",
        "candidateBuildId",
        "previousBuildId",
        "previousManifestSha256",
        "sealedSnapshots",
        "incrementalScenarioReceipts",
        "rollbackReceipt",
        "concurrentReaderReceipt",
        "shadowDiffDispositionReceipt",
    }
)
_SNAPSHOT_RECORD_KEYS = frozenset(
    {
        "buildId",
        "manifestSha256",
        "qualityReportSha256",
        "previousBuildId",
        "previousManifestSha256",
        "qualityReportCutoverEligible",
        "sealedInSnapshotManifest",
    }
)
_PARENT_KEYS = frozenset({"buildId", "manifestSha256"})
_CURRENT_POINTER_KEYS = frozenset({"buildId", "snapshotRelativePath"})
_TOP_CLAIM_KEYS = frozenset(
    {
        "status",
        "sealedSnapshotCount",
        "incrementalScenarioCount",
        "rollbackReceiptCount",
        "concurrentReaderReceiptCount",
        "shadowDispositionReceiptCount",
    }
)
_SCENARIO_SCOPE_KEYS = frozenset(
    {
        "kind",
        "policyVersion",
        "burnInRunId",
        "candidateBuildId",
        "scenarioId",
        "baseBuildId",
        "resultBuildId",
    }
)
_SCENARIO_ARTIFACT_KEYS = frozenset(
    {
        "schema",
        "burnInRunId",
        "candidateBuildId",
        "scenarioId",
        "status",
        "baseBuildId",
        "resultBuildId",
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
    }
)
_SCENARIO_CLAIM_KEYS = frozenset(
    {
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
    }
)
_ROLLBACK_SCOPE_KEYS = frozenset(
    {
        "kind",
        "policyVersion",
        "burnInRunId",
        "candidateBuildId",
        "fromBuildId",
        "toBuildId",
    }
)
_ROLLBACK_ARTIFACT_KEYS = frozenset(
    {
        "schema",
        "burnInRunId",
        "candidateBuildId",
        "status",
        "fromBuildId",
        "toBuildId",
        "fromManifestSha256",
        "toManifestSha256",
        "pointerBeforeSha256",
        "pointerAfterSha256",
        "expectedCurrentBuildId",
        "mixedBuildObservations",
        "command",
        "toolVersion",
        "startedAt",
        "completedAt",
    }
)
_ROLLBACK_CLAIM_KEYS = frozenset(
    _ROLLBACK_ARTIFACT_KEYS
    - {
        "schema",
        "burnInRunId",
        "candidateBuildId",
        "fromBuildId",
        "toBuildId",
    }
)
_CONCURRENT_SCOPE_KEYS = frozenset(
    {
        "kind",
        "policyVersion",
        "burnInRunId",
        "candidateBuildId",
        "fromBuildId",
        "toBuildId",
    }
)
_CONCURRENT_ARTIFACT_KEYS = frozenset(
    {
        "schema",
        "burnInRunId",
        "candidateBuildId",
        "status",
        "fromBuildId",
        "toBuildId",
        "readerCount",
        "requestCount",
        "observedBuildIds",
        "mixedBuildObservations",
        "pointerSwapsExercised",
        "command",
        "toolVersion",
        "startedAt",
        "completedAt",
    }
)
_CONCURRENT_CLAIM_KEYS = frozenset(
    _CONCURRENT_ARTIFACT_KEYS
    - {
        "schema",
        "burnInRunId",
        "candidateBuildId",
        "fromBuildId",
        "toBuildId",
    }
)
_SHADOW_SCOPE_KEYS = frozenset(
    {
        "kind",
        "policyVersion",
        "burnInRunId",
        "candidateBuildId",
        "buildIds",
        "corpusId",
        "corpusSha256",
    }
)
_SHADOW_ARTIFACT_KEYS = frozenset(
    {
        "schema",
        "burnInRunId",
        "candidateBuildId",
        "status",
        "buildIds",
        "corpusId",
        "corpusSha256",
        "caseCount",
        "wrongAnswers",
        "staleLeaks",
        "candidateCompletions",
        "undispositioned",
        "command",
        "toolVersion",
        "startedAt",
        "completedAt",
        "dispositions",
    }
)
_SHADOW_CLAIM_KEYS = frozenset(
    _SHADOW_ARTIFACT_KEYS
    - {
        "schema",
        "burnInRunId",
        "candidateBuildId",
        "buildIds",
        "corpusId",
        "corpusSha256",
        "dispositions",
    }
)
_DISPOSITION_KEYS = frozenset({"caseId", "outcome", "rationale"})
_DISPOSITION_OUTCOMES = frozenset(
    {
        "MATCH",
        "ACCEPTED_DIFFERENCE",
        "WRONG_ANSWER",
        "STALE_LEAK",
        "CANDIDATE_COMPLETION",
        "UNDISPOSITIONED",
    }
)


class BurnInV2Error(ValueError):
    """Raised when burn-in v2 evidence fails closed."""


@dataclass(frozen=True)
class _SnapshotEvidence:
    build_id: str
    manifest_sha256: str
    manifest: Mapping[str, object]
    previous_build_id: str | None
    previous_manifest_sha256: str | None
    quality_report_sha256: str | None


@dataclass(frozen=True)
class ValidatedBurnInV2:
    """Immutable result of a complete read-only burn-in verification."""

    validated: bool
    production_eligible: bool
    trust_context: str
    burn_in_run_id: str
    candidate_build_id: str
    previous_build_id: str
    previous_manifest_sha256: str
    registry_version_sha256: str
    operator_signer_id: str
    operator_receipt_id: str
    evidence_bundle_uri: str
    evidence_bundle_sha256: str
    evidence_bundle_bytes: bytes
    representative_corpus_id: str
    representative_corpus_sha256: str
    representative_case_ids: tuple[str, ...]
    sealed_snapshot_build_ids: tuple[str, ...]
    receipt_ids: tuple[str, ...]
    artifact_bytes_by_uri: Mapping[str, bytes]


def _duplicate_rejecting_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise BurnInV2Error(f"duplicate JSON key is forbidden: {key}")
        value[key] = child
    return value


def _reject_json_constant(value: str) -> object:
    raise BurnInV2Error(f"non-finite JSON value is forbidden: {value}")


def strict_json_object_from_bytes(
    value: bytes,
    *,
    label: str,
) -> Mapping[str, object]:
    """Decode one UTF-8 JSON object while rejecting duplicate keys."""

    if not isinstance(value, bytes):
        raise BurnInV2Error(f"{label} must be bytes")
    try:
        decoded = value.decode("utf-8")
    except UnicodeError as error:
        raise BurnInV2Error(f"{label} is not valid UTF-8") from error
    try:
        parsed = json.loads(
            decoded,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_constant=_reject_json_constant,
        )
    except BurnInV2Error:
        raise
    except json.JSONDecodeError as error:
        raise BurnInV2Error(f"{label} is not valid JSON") from error
    except ValueError as error:
        raise BurnInV2Error(f"{label} contains an unsupported JSON integer") from error
    if not isinstance(parsed, Mapping):
        raise BurnInV2Error(f"{label} must be a JSON object")
    return parsed


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BurnInV2Error(f"{label} must be an object")
    return value


def _list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise BurnInV2Error(f"{label} must be an array")
    return value


def _exact_keys(
    value: Mapping[str, object],
    expected: frozenset[str] | set[str],
    *,
    label: str,
) -> None:
    observed = frozenset(value)
    expected_keys = frozenset(expected)
    if observed == expected_keys:
        return
    missing = sorted(expected_keys - observed)
    unexpected = sorted(observed - expected_keys)
    details: list[str] = []
    if missing:
        details.append("missing=" + ",".join(missing))
    if unexpected:
        details.append("unexpected=" + ",".join(unexpected))
    raise BurnInV2Error(f"{label} fields are invalid ({'; '.join(details)})")


def _required_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BurnInV2Error(f"{label} must be a non-empty string")
    if value != value.strip() or any(
        character in value for character in ("\x00", "\r", "\n")
    ):
        raise BurnInV2Error(f"{label} contains unsafe whitespace")
    return value


def _build_id(value: object, *, label: str) -> str:
    build_id = _required_text(value, label=label)
    if _BUILD_ID.fullmatch(build_id) is None:
        raise BurnInV2Error(f"{label} is not a safe build ID")
    return build_id


def _run_id(value: object, *, label: str) -> str:
    run_id = _required_text(value, label=label)
    if _BUILD_ID.fullmatch(run_id) is None:
        raise BurnInV2Error(f"{label} is not a safe run ID")
    return run_id


def _sha256_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BurnInV2Error(f"{label} must be a lowercase SHA-256 digest")
    return value


def _boolean(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise BurnInV2Error(f"{label} must be a boolean")
    return value


def _integer(
    value: object,
    *,
    label: str,
    minimum: int | None = None,
) -> int:
    if type(value) is not int:
        raise BurnInV2Error(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise BurnInV2Error(f"{label} must be at least {minimum}")
    return value


def _representative_case_ids(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise BurnInV2Error("expected representative case IDs must be an array")
    case_ids = tuple(
        _required_text(item, label="expected representative case ID") for item in value
    )
    if len(case_ids) < 2:
        raise BurnInV2Error("representative case set must contain at least two cases")
    if len(case_ids) != len(set(case_ids)):
        raise BurnInV2Error("expected representative case IDs must be unique")
    return case_ids


def _timestamp(value: object, *, label: str) -> datetime:
    text = _required_text(value, label=label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise BurnInV2Error(f"{label} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise BurnInV2Error(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_bytes(path: Path, *, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise BurnInV2Error(f"{label} is unavailable") from error


def _safe_relative_path(
    root: Path,
    raw_path: object,
    *,
    label: str,
) -> Path:
    text = _required_text(raw_path, label=label)
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if (
        "\\" in text
        or "%" in text
        or "?" in text
        or "#" in text
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} or ":" in part for part in posix.parts)
    ):
        raise BurnInV2Error(f"{label} must stay inside its snapshot")
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*posix.parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise BurnInV2Error(f"{label} escapes its snapshot") from error
    return candidate


def _json_plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_json_plain(child) for child in value]
    if isinstance(value, list):
        return [_json_plain(child) for child in value]
    return value


def _claim_equals(
    verified: VerifiedSignedReceipt,
    expected: Mapping[str, object],
    *,
    label: str,
) -> None:
    actual = _json_plain(verified.claim)
    if canonical_json_bytes(actual) != canonical_json_bytes(expected):
        raise BurnInV2Error(f"{label} artifact does not match signed claim")


def _validate_times(
    artifact: Mapping[str, object],
    verified: VerifiedSignedReceipt,
    *,
    label: str,
) -> None:
    started = _timestamp(artifact.get("startedAt"), label=f"{label}.startedAt")
    completed = _timestamp(
        artifact.get("completedAt"),
        label=f"{label}.completedAt",
    )
    if completed < started:
        raise BurnInV2Error(f"{label} completed before it started")
    if verified.issued_at < completed:
        raise BurnInV2Error(f"{label} was signed before completion")


def _verify_receipt(
    receipt: Mapping[str, object],
    *,
    registry: Mapping[str, object],
    expected_registry_sha256: str,
    expected_scope: Mapping[str, object],
    artifact_root: Path,
    replay_guard: ReceiptReplayGuard,
    trust_context: str,
    verification_time: datetime | None,
) -> VerifiedSignedReceipt:
    try:
        return verify_signed_receipt(
            receipt,
            registry=registry,
            expected_registry_sha256=expected_registry_sha256,
            expected_scope=expected_scope,
            expected_role=BURN_IN_OPERATOR_ROLE,
            artifact_root=artifact_root,
            replay_guard=replay_guard,
            trust_context=trust_context,
            verification_time=verification_time,
        )
    except SignedReceiptError as error:
        raise BurnInV2Error(str(error)) from error


def _receipt_scope(
    receipt: Mapping[str, object],
    *,
    expected_keys: frozenset[str],
    label: str,
) -> Mapping[str, object]:
    payload = _mapping(receipt.get("payload"), label=f"{label} payload")
    scope = _mapping(payload.get("scope"), label=f"{label} scope")
    _exact_keys(scope, expected_keys, label=f"{label} scope")
    return scope


def _read_snapshot_manifest(
    snapshot_root: Path,
    build_id: str,
) -> _SnapshotEvidence:
    snapshots_root = snapshot_root.resolve() / "snapshots"
    snapshot_dir = snapshots_root / _build_id(build_id, label="snapshot buildId")
    if not snapshot_dir.is_dir():
        raise BurnInV2Error(f"snapshot does not exist: {build_id}")
    manifest_path = snapshot_dir / "manifest.json"
    manifest_bytes = _read_bytes(
        manifest_path,
        label=f"snapshot {build_id} manifest",
    )
    manifest = strict_json_object_from_bytes(
        manifest_bytes,
        label=f"snapshot {build_id} manifest",
    )
    if (
        manifest.get("schema") != "ark-kb-vnext-snapshot/v1"
        or manifest.get("buildId") != build_id
    ):
        raise BurnInV2Error(f"snapshot {build_id} identity is invalid")
    raw_parent = manifest.get("previousSnapshot")
    previous_build_id: str | None = None
    previous_manifest_sha256: str | None = None
    if raw_parent is not None:
        parent = _mapping(
            raw_parent,
            label=f"snapshot {build_id} previousSnapshot",
        )
        _exact_keys(
            parent,
            _PARENT_KEYS,
            label=f"snapshot {build_id} previousSnapshot",
        )
        previous_build_id = _build_id(
            parent.get("buildId"),
            label=f"snapshot {build_id} previousSnapshot.buildId",
        )
        previous_manifest_sha256 = _sha256_text(
            parent.get("manifestSha256"),
            label=(f"snapshot {build_id} previousSnapshot.manifestSha256"),
        )
    quality = manifest.get("qualityGates")
    quality_report_sha256: str | None = None
    if isinstance(quality, Mapping) and "sha256" in quality:
        quality_report_sha256 = _sha256_text(
            quality.get("sha256"),
            label=f"snapshot {build_id} quality report SHA-256",
        )
    return _SnapshotEvidence(
        build_id=build_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        manifest=manifest,
        previous_build_id=previous_build_id,
        previous_manifest_sha256=previous_manifest_sha256,
        quality_report_sha256=quality_report_sha256,
    )


def _validate_shadow_snapshot_quality(
    snapshot_root: Path,
    evidence: _SnapshotEvidence,
    *,
    expected_report_sha256: str,
) -> None:
    build_id = evidence.build_id
    quality = _mapping(
        evidence.manifest.get("qualityGates"),
        label=f"snapshot {build_id} qualityGates",
    )
    cutover = _mapping(
        evidence.manifest.get("cutover"),
        label=f"snapshot {build_id} cutover",
    )
    if (
        quality.get("qualityReportCutoverEligible") is not True
        or quality.get("cutoverEligible") is not False
        or quality.get("sealedInSnapshotManifest") is not True
        or cutover.get("mode") != "shadow"
        or cutover.get("defaultQuerySource") != "legacy"
    ):
        raise BurnInV2Error(f"snapshot {build_id} is not a sealed passing shadow build")
    report_path = _safe_relative_path(
        snapshot_root.resolve() / "snapshots" / build_id,
        quality.get("reportUri"),
        label=f"snapshot {build_id} quality report URI",
    )
    report_bytes = _read_bytes(
        report_path,
        label=f"snapshot {build_id} quality report",
    )
    report_sha = _sha256_bytes(report_bytes)
    declared_sha = _sha256_text(
        quality.get("sha256"),
        label=f"snapshot {build_id} quality report SHA-256",
    )
    if report_sha != declared_sha or report_sha != expected_report_sha256:
        raise BurnInV2Error(f"snapshot {build_id} quality report SHA-256 is invalid")
    report = strict_json_object_from_bytes(
        report_bytes,
        label=f"snapshot {build_id} quality report",
    )
    summary = _mapping(
        report.get("summary"),
        label=f"snapshot {build_id} quality summary",
    )
    if (
        report.get("buildId") != build_id
        or summary.get("cutoverEligible") is not True
        or type(summary.get("failed")) is not int
        or summary.get("failed") != 0
        or type(summary.get("passed")) is not int
        or summary.get("passed") != 75
        or type(summary.get("total")) is not int
        or summary.get("total") != 75
    ):
        raise BurnInV2Error(f"snapshot {build_id} quality report is not 75/75")


def _validate_current_pointer(
    snapshot_root: Path,
    *,
    expected_build_id: str,
) -> None:
    pointer_bytes = _read_bytes(
        snapshot_root.resolve() / "current.json",
        label="current snapshot pointer",
    )
    pointer = strict_json_object_from_bytes(
        pointer_bytes,
        label="current snapshot pointer",
    )
    _exact_keys(pointer, _CURRENT_POINTER_KEYS, label="current pointer")
    if (
        pointer.get("buildId") != expected_build_id
        or pointer.get("snapshotRelativePath") != f"snapshots/{expected_build_id}"
    ):
        raise BurnInV2Error("current pointer does not match the burn-in chain tip")


def _detect_snapshot_forks(snapshot_root: Path) -> None:
    snapshots_root = snapshot_root.resolve() / "snapshots"
    children: dict[tuple[str, str], set[str]] = {}
    if not snapshots_root.is_dir():
        raise BurnInV2Error("snapshot history is unavailable")
    for manifest_path in sorted(snapshots_root.glob("*/manifest.json")):
        raw = _read_bytes(manifest_path, label="published snapshot manifest")
        manifest = strict_json_object_from_bytes(
            raw,
            label="published snapshot manifest",
        )
        build_id = _build_id(
            manifest.get("buildId"),
            label="published snapshot buildId",
        )
        if (
            manifest.get("schema") != "ark-kb-vnext-snapshot/v1"
            or build_id != manifest_path.parent.name
        ):
            raise BurnInV2Error("published snapshot history is invalid")
        raw_parent = manifest.get("previousSnapshot")
        if raw_parent is None:
            continue
        parent = _mapping(
            raw_parent,
            label=f"snapshot {build_id} previousSnapshot",
        )
        _exact_keys(
            parent,
            _PARENT_KEYS,
            label=f"snapshot {build_id} previousSnapshot",
        )
        parent_id = _build_id(
            parent.get("buildId"),
            label=f"snapshot {build_id} parent buildId",
        )
        parent_sha = _sha256_text(
            parent.get("manifestSha256"),
            label=f"snapshot {build_id} parent manifest SHA-256",
        )
        parent_evidence = _read_snapshot_manifest(
            snapshot_root,
            parent_id,
        )
        if parent_evidence.manifest_sha256 != parent_sha:
            raise BurnInV2Error(
                f"snapshot {build_id} parent manifest SHA-256 is invalid"
            )
        children.setdefault((parent_id, parent_sha), set()).add(build_id)
    forked = {
        parent: sorted(child_ids)
        for parent, child_ids in children.items()
        if len(child_ids) > 1
    }
    if forked:
        raise BurnInV2Error(f"snapshot parent chain fork detected: {forked}")


def _validate_snapshot_chain(
    bundle: Mapping[str, object],
    *,
    snapshot_root: Path,
    expected_previous_build_id: str,
    expected_previous_manifest_sha256: str,
) -> tuple[_SnapshotEvidence, ...]:
    raw_records = _list(
        bundle.get("sealedSnapshots"),
        label="sealedSnapshots",
    )
    if len(raw_records) != MINIMUM_SEALED_BUILDS:
        raise BurnInV2Error("exactly three sealedSnapshots are required")
    records: list[_SnapshotEvidence] = []
    seen_build_ids: set[str] = set()
    prior_build_id: str | None = None
    prior_manifest_sha: str | None = None
    for index, raw_record in enumerate(raw_records):
        record = _mapping(
            raw_record,
            label=f"sealedSnapshots[{index}]",
        )
        _exact_keys(
            record,
            _SNAPSHOT_RECORD_KEYS,
            label=f"sealedSnapshots[{index}]",
        )
        build_id = _build_id(
            record.get("buildId"),
            label=f"sealedSnapshots[{index}].buildId",
        )
        if build_id in seen_build_ids:
            raise BurnInV2Error("sealed snapshot build IDs must be unique")
        seen_build_ids.add(build_id)
        manifest_sha = _sha256_text(
            record.get("manifestSha256"),
            label=f"sealedSnapshots[{index}].manifestSha256",
        )
        report_sha = _sha256_text(
            record.get("qualityReportSha256"),
            label=f"sealedSnapshots[{index}].qualityReportSha256",
        )
        previous_build_id = _build_id(
            record.get("previousBuildId"),
            label=f"sealedSnapshots[{index}].previousBuildId",
        )
        previous_manifest_sha = _sha256_text(
            record.get("previousManifestSha256"),
            label=f"sealedSnapshots[{index}].previousManifestSha256",
        )
        if (
            record.get("qualityReportCutoverEligible") is not True
            or record.get("sealedInSnapshotManifest") is not True
        ):
            raise BurnInV2Error(
                f"sealedSnapshots[{index}] is not a passing sealed record"
            )
        evidence = _read_snapshot_manifest(snapshot_root, build_id)
        if evidence.manifest_sha256 != manifest_sha:
            raise BurnInV2Error(f"snapshot {build_id} manifest SHA-256 is invalid")
        if (
            evidence.previous_build_id != previous_build_id
            or evidence.previous_manifest_sha256 != previous_manifest_sha
        ):
            raise BurnInV2Error(
                f"snapshot parent chain does not match manifest: {build_id}"
            )
        if index == 0:
            anchor = _read_snapshot_manifest(
                snapshot_root,
                previous_build_id,
            )
            if anchor.manifest_sha256 != previous_manifest_sha:
                raise BurnInV2Error("snapshot parent chain anchor SHA-256 is invalid")
        elif (
            previous_build_id != prior_build_id
            or previous_manifest_sha != prior_manifest_sha
        ):
            raise BurnInV2Error(f"snapshot parent chain is broken at {build_id}")
        _validate_shadow_snapshot_quality(
            snapshot_root,
            evidence,
            expected_report_sha256=report_sha,
        )
        records.append(evidence)
        prior_build_id = build_id
        prior_manifest_sha = manifest_sha
    if (
        prior_build_id != expected_previous_build_id
        or prior_manifest_sha != expected_previous_manifest_sha256
    ):
        raise BurnInV2Error(
            "burn-in chain tip does not match the expected candidate parent"
        )
    _validate_current_pointer(
        snapshot_root,
        expected_build_id=expected_previous_build_id,
    )
    _detect_snapshot_forks(snapshot_root)
    return tuple(records)


def _snapshot_chain_to_tip(
    snapshot_root: Path,
    tip_build_id: str,
) -> tuple[_SnapshotEvidence, ...]:
    reverse_chain: list[_SnapshotEvidence] = []
    seen: set[str] = set()
    current = _read_snapshot_manifest(snapshot_root, tip_build_id)
    while True:
        if current.build_id in seen:
            raise BurnInV2Error("snapshot parent chain cycle detected")
        seen.add(current.build_id)
        reverse_chain.append(current)
        if current.previous_build_id is None:
            break
        parent = _read_snapshot_manifest(
            snapshot_root,
            current.previous_build_id,
        )
        if parent.manifest_sha256 != current.previous_manifest_sha256:
            raise BurnInV2Error(
                f"snapshot {current.build_id} parent manifest SHA-256 is invalid"
            )
        current = parent
    reverse_chain.reverse()
    return tuple(reverse_chain)


def _snapshot_for_claim(
    snapshot_root: Path,
    *,
    build_id: object,
    manifest_sha256: object,
    label: str,
) -> _SnapshotEvidence:
    safe_build_id = _build_id(build_id, label=f"{label} buildId")
    expected_sha = _sha256_text(
        manifest_sha256,
        label=f"{label} manifest SHA-256",
    )
    evidence = _read_snapshot_manifest(snapshot_root, safe_build_id)
    if evidence.manifest_sha256 != expected_sha:
        raise BurnInV2Error(f"{label} manifest SHA-256 is invalid")
    return evidence


def _is_forward_adjacent(
    build_ids: tuple[str, ...],
    from_build_id: str,
    to_build_id: str,
) -> bool:
    try:
        from_index = build_ids.index(from_build_id)
        to_index = build_ids.index(to_build_id)
    except ValueError:
        return False
    return to_index == from_index + 1


def _validate_incremental_receipt(
    scenario_id: str,
    raw_receipt: object,
    *,
    expected_burn_in_run_id: str,
    expected_candidate_build_id: str,
    sealed_snapshots: tuple[_SnapshotEvidence, ...],
    published_chain_pairs: frozenset[tuple[str, str]],
    registry: Mapping[str, object],
    expected_registry_sha256: str,
    artifact_root: Path,
    snapshot_root: Path,
    replay_guard: ReceiptReplayGuard,
    trust_context: str,
    verification_time: datetime | None,
) -> tuple[VerifiedSignedReceipt, tuple[str, str] | None]:
    label = f"incremental scenario {scenario_id}"
    if not isinstance(raw_receipt, Mapping):
        raise BurnInV2Error(f"{scenario_id} receipt must be an object")
    scope = _receipt_scope(
        raw_receipt,
        expected_keys=_SCENARIO_SCOPE_KEYS,
        label=label,
    )
    base_build_id = _build_id(
        scope.get("baseBuildId"),
        label=f"{label} baseBuildId",
    )
    result_build_id = _build_id(
        scope.get("resultBuildId"),
        label=f"{label} resultBuildId",
    )
    expected_scope = {
        "kind": "INCREMENTAL_SCENARIO",
        "policyVersion": BURN_IN_POLICY_V2,
        "burnInRunId": expected_burn_in_run_id,
        "candidateBuildId": expected_candidate_build_id,
        "scenarioId": scenario_id,
        "baseBuildId": base_build_id,
        "resultBuildId": result_build_id,
    }
    verified = _verify_receipt(
        raw_receipt,
        registry=registry,
        expected_registry_sha256=expected_registry_sha256,
        expected_scope=expected_scope,
        artifact_root=artifact_root,
        replay_guard=replay_guard,
        trust_context=trust_context,
        verification_time=verification_time,
    )
    artifact = strict_json_object_from_bytes(
        verified.artifact_bytes,
        label=f"{label} artifact",
    )
    _exact_keys(artifact, _SCENARIO_ARTIFACT_KEYS, label=f"{label} artifact")
    if (
        artifact.get("schema") != "ark-kb-incremental-scenario-result/v2"
        or artifact.get("burnInRunId") != expected_burn_in_run_id
        or artifact.get("candidateBuildId") != expected_candidate_build_id
        or artifact.get("scenarioId") != scenario_id
        or artifact.get("status") != "PASSED"
        or artifact.get("baseBuildId") != base_build_id
        or artifact.get("resultBuildId") != result_build_id
    ):
        raise BurnInV2Error(f"{label} artifact identity is invalid")
    previous_sha = _sha256_text(
        artifact.get("previousManifestSha256"),
        label=f"{label} previousManifestSha256",
    )
    result_sha = _sha256_text(
        artifact.get("resultManifestSha256"),
        label=f"{label} resultManifestSha256",
    )
    _sha256_text(
        artifact.get("sourceDiffSha256"),
        label=f"{label} sourceDiffSha256",
    )
    published = _boolean(
        artifact.get("published"),
        label=f"{label} published",
    )
    current_unchanged = _boolean(
        artifact.get("currentUnchanged"),
        label=f"{label} currentUnchanged",
    )
    cache_hit = _boolean(
        artifact.get("cacheHit"),
        label=f"{label} cacheHit",
    )
    mixed_build_observations = _integer(
        artifact.get("mixedBuildObservations"),
        label=f"{label} mixedBuildObservations",
        minimum=0,
    )
    pointer_swaps_exercised = _integer(
        artifact.get("pointerSwapsExercised"),
        label=f"{label} pointerSwapsExercised",
        minimum=0,
    )
    _required_text(artifact.get("command"), label=f"{label} command")
    _required_text(artifact.get("toolVersion"), label=f"{label} toolVersion")
    base = _snapshot_for_claim(
        snapshot_root,
        build_id=base_build_id,
        manifest_sha256=previous_sha,
        label=f"{label} base",
    )
    result = _snapshot_for_claim(
        snapshot_root,
        build_id=result_build_id,
        manifest_sha256=result_sha,
        label=f"{label} result",
    )
    transition: tuple[str, str] | None = None
    current_tip = sealed_snapshots[-1]
    sealed_build_ids = tuple(snapshot.build_id for snapshot in sealed_snapshots)
    if scenario_id in PUBLISHED_INCREMENTAL_SCENARIOS:
        if (
            base_build_id == result_build_id
            or published is not True
            or current_unchanged is not False
            or cache_hit is not False
            or mixed_build_observations != 0
            or pointer_swaps_exercised < 1
            or (base_build_id, result_build_id) not in published_chain_pairs
            or result.previous_build_id != base.build_id
            or result.previous_manifest_sha256 != base.manifest_sha256
        ):
            raise BurnInV2Error(
                f"{scenario_id} must publish a distinct direct child "
                "with a clean pointer swap"
            )
        transition = (base_build_id, result_build_id)
    elif scenario_id in NO_PUBLISH_INCREMENTAL_SCENARIOS:
        if (
            base_build_id != current_tip.build_id
            or result_build_id != current_tip.build_id
            or published is not False
            or current_unchanged is not True
            or cache_hit is not False
            or mixed_build_observations != 0
            or pointer_swaps_exercised != 0
        ):
            raise BurnInV2Error(
                f"{scenario_id} must not publish and current must remain unchanged"
            )
    elif scenario_id == "unchangedCacheHit":
        if (
            base_build_id != current_tip.build_id
            or result_build_id != current_tip.build_id
            or published is not False
            or current_unchanged is not True
            or cache_hit is not True
            or mixed_build_observations != 0
            or pointer_swaps_exercised != 0
        ):
            raise BurnInV2Error(
                "unchangedCacheHit must be a same-build cache hit with "
                "current unchanged"
            )
    elif scenario_id == "concurrentReaders":
        if not _is_forward_adjacent(
            sealed_build_ids,
            base_build_id,
            result_build_id,
        ):
            raise BurnInV2Error("concurrentReaders must use adjacent sealed snapshots")
        if (
            published is not False
            or current_unchanged is not True
            or cache_hit is not False
            or mixed_build_observations != 0
            or pointer_swaps_exercised < 1
        ):
            raise BurnInV2Error(
                "concurrentReaders must perform a controlled swap with "
                "zero mixed build observations"
            )
    else:  # pragma: no cover - import-time partition assertion owns this path
        raise BurnInV2Error(f"unclassified incremental scenario: {scenario_id}")
    claim = {key: artifact[key] for key in _SCENARIO_CLAIM_KEYS}
    _exact_keys(
        _mapping(_json_plain(verified.claim), label=f"{label} claim"),
        _SCENARIO_CLAIM_KEYS,
        label=f"{label} claim",
    )
    _claim_equals(verified, claim, label=label)
    _validate_times(artifact, verified, label=label)
    return verified, transition


def _pointer_sha256(build_id: str) -> str:
    pointer = {
        "buildId": build_id,
        "snapshotRelativePath": f"snapshots/{build_id}",
    }
    contents = (
        json.dumps(
            pointer,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return _sha256_bytes(contents)


def _validate_rollback_receipt(
    raw_receipt: object,
    *,
    expected_burn_in_run_id: str,
    expected_candidate_build_id: str,
    sealed_snapshots: tuple[_SnapshotEvidence, ...],
    registry: Mapping[str, object],
    expected_registry_sha256: str,
    artifact_root: Path,
    snapshot_root: Path,
    replay_guard: ReceiptReplayGuard,
    trust_context: str,
    verification_time: datetime | None,
) -> VerifiedSignedReceipt:
    label = "rollback drill"
    receipt = _mapping(raw_receipt, label="rollbackReceipt")
    scope = _receipt_scope(
        receipt,
        expected_keys=_ROLLBACK_SCOPE_KEYS,
        label=label,
    )
    from_build_id = _build_id(
        scope.get("fromBuildId"),
        label="rollback fromBuildId",
    )
    to_build_id = _build_id(
        scope.get("toBuildId"),
        label="rollback toBuildId",
    )
    if from_build_id == to_build_id:
        raise BurnInV2Error("rollback must change the current build")
    current_tip = sealed_snapshots[-1]
    adjacent_predecessor = sealed_snapshots[-2]
    if from_build_id != current_tip.build_id:
        raise BurnInV2Error("rollback must start at the actual current chain tip")
    if to_build_id != adjacent_predecessor.build_id:
        raise BurnInV2Error("rollback must target the adjacent predecessor")
    verified = _verify_receipt(
        receipt,
        registry=registry,
        expected_registry_sha256=expected_registry_sha256,
        expected_scope={
            "kind": "ROLLBACK_DRILL",
            "policyVersion": BURN_IN_POLICY_V2,
            "burnInRunId": expected_burn_in_run_id,
            "candidateBuildId": expected_candidate_build_id,
            "fromBuildId": from_build_id,
            "toBuildId": to_build_id,
        },
        artifact_root=artifact_root,
        replay_guard=replay_guard,
        trust_context=trust_context,
        verification_time=verification_time,
    )
    artifact = strict_json_object_from_bytes(
        verified.artifact_bytes,
        label="rollback artifact",
    )
    _exact_keys(artifact, _ROLLBACK_ARTIFACT_KEYS, label="rollback artifact")
    if (
        artifact.get("schema") != "ark-kb-rollback-drill-result/v2"
        or artifact.get("burnInRunId") != expected_burn_in_run_id
        or artifact.get("candidateBuildId") != expected_candidate_build_id
        or artifact.get("status") != "PASSED"
        or artifact.get("fromBuildId") != from_build_id
        or artifact.get("toBuildId") != to_build_id
        or artifact.get("expectedCurrentBuildId") != from_build_id
    ):
        raise BurnInV2Error("rollback artifact identity is invalid")
    _snapshot_for_claim(
        snapshot_root,
        build_id=from_build_id,
        manifest_sha256=artifact.get("fromManifestSha256"),
        label="rollback from",
    )
    _snapshot_for_claim(
        snapshot_root,
        build_id=to_build_id,
        manifest_sha256=artifact.get("toManifestSha256"),
        label="rollback to",
    )
    before = _sha256_text(
        artifact.get("pointerBeforeSha256"),
        label="rollback pointerBeforeSha256",
    )
    after = _sha256_text(
        artifact.get("pointerAfterSha256"),
        label="rollback pointerAfterSha256",
    )
    if before != _pointer_sha256(from_build_id) or after != _pointer_sha256(
        to_build_id
    ):
        raise BurnInV2Error("rollback pointer SHA-256 is invalid")
    if (
        type(artifact.get("mixedBuildObservations")) is not int
        or artifact.get("mixedBuildObservations") != 0
    ):
        raise BurnInV2Error("rollback mixedBuildObservations must be zero")
    _required_text(artifact.get("command"), label="rollback command")
    _required_text(
        artifact.get("toolVersion"),
        label="rollback toolVersion",
    )
    claim = {key: artifact[key] for key in _ROLLBACK_CLAIM_KEYS}
    _exact_keys(
        _mapping(_json_plain(verified.claim), label="rollback claim"),
        _ROLLBACK_CLAIM_KEYS,
        label="rollback claim",
    )
    _claim_equals(verified, claim, label=label)
    _validate_times(artifact, verified, label=label)
    return verified


def _validate_concurrent_reader_receipt(
    raw_receipt: object,
    *,
    expected_burn_in_run_id: str,
    expected_candidate_build_id: str,
    sealed_snapshots: tuple[_SnapshotEvidence, ...],
    registry: Mapping[str, object],
    expected_registry_sha256: str,
    artifact_root: Path,
    snapshot_root: Path,
    replay_guard: ReceiptReplayGuard,
    trust_context: str,
    verification_time: datetime | None,
) -> VerifiedSignedReceipt:
    label = "concurrent reader drill"
    receipt = _mapping(raw_receipt, label="concurrentReaderReceipt")
    scope = _receipt_scope(
        receipt,
        expected_keys=_CONCURRENT_SCOPE_KEYS,
        label=label,
    )
    from_build_id = _build_id(
        scope.get("fromBuildId"),
        label="concurrent reader fromBuildId",
    )
    to_build_id = _build_id(
        scope.get("toBuildId"),
        label="concurrent reader toBuildId",
    )
    if from_build_id == to_build_id:
        raise BurnInV2Error("concurrent reader drill must cross a pointer swap")
    sealed_build_ids = tuple(snapshot.build_id for snapshot in sealed_snapshots)
    if not _is_forward_adjacent(
        sealed_build_ids,
        from_build_id,
        to_build_id,
    ):
        raise BurnInV2Error(
            "concurrent reader drill must use adjacent sealed snapshots"
        )
    verified = _verify_receipt(
        receipt,
        registry=registry,
        expected_registry_sha256=expected_registry_sha256,
        expected_scope={
            "kind": "CONCURRENT_READER_DRILL",
            "policyVersion": BURN_IN_POLICY_V2,
            "burnInRunId": expected_burn_in_run_id,
            "candidateBuildId": expected_candidate_build_id,
            "fromBuildId": from_build_id,
            "toBuildId": to_build_id,
        },
        artifact_root=artifact_root,
        replay_guard=replay_guard,
        trust_context=trust_context,
        verification_time=verification_time,
    )
    artifact = strict_json_object_from_bytes(
        verified.artifact_bytes,
        label="concurrent reader artifact",
    )
    _exact_keys(
        artifact,
        _CONCURRENT_ARTIFACT_KEYS,
        label="concurrent reader artifact",
    )
    if (
        artifact.get("schema") != "ark-kb-concurrent-reader-drill-result/v2"
        or artifact.get("burnInRunId") != expected_burn_in_run_id
        or artifact.get("candidateBuildId") != expected_candidate_build_id
        or artifact.get("status") != "PASSED"
        or artifact.get("fromBuildId") != from_build_id
        or artifact.get("toBuildId") != to_build_id
    ):
        raise BurnInV2Error("concurrent reader artifact identity is invalid")
    _read_snapshot_manifest(snapshot_root, from_build_id)
    _read_snapshot_manifest(snapshot_root, to_build_id)
    reader_count = _integer(
        artifact.get("readerCount"),
        label="concurrent reader readerCount",
        minimum=1,
    )
    request_count = _integer(
        artifact.get("requestCount"),
        label="concurrent reader requestCount",
        minimum=1,
    )
    if request_count < reader_count:
        raise BurnInV2Error("concurrent reader requestCount is below readerCount")
    observed = _list(
        artifact.get("observedBuildIds"),
        label="concurrent reader observedBuildIds",
    )
    if (
        any(not isinstance(item, str) for item in observed)
        or len(observed) != len(set(observed))
        or set(observed) != {from_build_id, to_build_id}
    ):
        raise BurnInV2Error("concurrent reader must observe both pointer build IDs")
    if (
        type(artifact.get("mixedBuildObservations")) is not int
        or artifact.get("mixedBuildObservations") != 0
    ):
        raise BurnInV2Error("concurrent reader mixedBuildObservations must be zero")
    _integer(
        artifact.get("pointerSwapsExercised"),
        label="concurrent reader pointer swaps",
        minimum=1,
    )
    _required_text(
        artifact.get("command"),
        label="concurrent reader command",
    )
    _required_text(
        artifact.get("toolVersion"),
        label="concurrent reader toolVersion",
    )
    claim = {key: artifact[key] for key in _CONCURRENT_CLAIM_KEYS}
    _exact_keys(
        _mapping(_json_plain(verified.claim), label="concurrent reader claim"),
        _CONCURRENT_CLAIM_KEYS,
        label="concurrent reader claim",
    )
    _claim_equals(verified, claim, label=label)
    _validate_times(artifact, verified, label=label)
    return verified


def _validate_shadow_disposition_receipt(
    raw_receipt: object,
    *,
    expected_burn_in_run_id: str,
    expected_candidate_build_id: str,
    expected_build_ids: tuple[str, ...],
    expected_corpus_id: str,
    expected_corpus_sha256: str,
    expected_case_ids: tuple[str, ...],
    registry: Mapping[str, object],
    expected_registry_sha256: str,
    artifact_root: Path,
    replay_guard: ReceiptReplayGuard,
    trust_context: str,
    verification_time: datetime | None,
) -> VerifiedSignedReceipt:
    label = "shadow diff disposition"
    receipt = _mapping(raw_receipt, label="shadowDiffDispositionReceipt")
    scope = _receipt_scope(
        receipt,
        expected_keys=_SHADOW_SCOPE_KEYS,
        label=label,
    )
    raw_build_ids = _list(scope.get("buildIds"), label="shadow buildIds")
    if (
        any(not isinstance(item, str) for item in raw_build_ids)
        or tuple(raw_build_ids) != expected_build_ids
    ):
        raise BurnInV2Error("shadow receipt buildIds do not match sealed snapshots")
    corpus_id = _required_text(
        scope.get("corpusId"),
        label="shadow corpusId",
    )
    if corpus_id != expected_corpus_id:
        raise BurnInV2Error(
            "shadow corpusId does not match the out-of-band representative corpus"
        )
    corpus_sha = _sha256_text(
        scope.get("corpusSha256"),
        label="shadow corpusSha256",
    )
    if corpus_sha != expected_corpus_sha256:
        raise BurnInV2Error(
            "shadow corpusSha256 does not match the out-of-band representative corpus"
        )
    verified = _verify_receipt(
        receipt,
        registry=registry,
        expected_registry_sha256=expected_registry_sha256,
        expected_scope={
            "kind": "SHADOW_DIFF_DISPOSITION",
            "policyVersion": BURN_IN_POLICY_V2,
            "burnInRunId": expected_burn_in_run_id,
            "candidateBuildId": expected_candidate_build_id,
            "buildIds": list(expected_build_ids),
            "corpusId": expected_corpus_id,
            "corpusSha256": expected_corpus_sha256,
        },
        artifact_root=artifact_root,
        replay_guard=replay_guard,
        trust_context=trust_context,
        verification_time=verification_time,
    )
    artifact = strict_json_object_from_bytes(
        verified.artifact_bytes,
        label="shadow disposition artifact",
    )
    _exact_keys(
        artifact,
        _SHADOW_ARTIFACT_KEYS,
        label="shadow disposition artifact",
    )
    if (
        artifact.get("schema") != "ark-kb-shadow-diff-disposition/v2"
        or artifact.get("burnInRunId") != expected_burn_in_run_id
        or artifact.get("candidateBuildId") != expected_candidate_build_id
        or artifact.get("status") != "PASSED"
        or tuple(
            _list(
                artifact.get("buildIds"),
                label="shadow artifact buildIds",
            )
        )
        != expected_build_ids
        or artifact.get("corpusId") != expected_corpus_id
        or artifact.get("corpusSha256") != expected_corpus_sha256
    ):
        raise BurnInV2Error("shadow disposition artifact identity is invalid")
    dispositions = _list(
        artifact.get("dispositions"),
        label="shadow dispositions",
    )
    if not dispositions:
        raise BurnInV2Error("shadow dispositions must not be empty")
    outcomes: Counter[str] = Counter()
    case_ids: set[str] = set()
    for index, raw_row in enumerate(dispositions):
        row = _mapping(raw_row, label=f"shadow dispositions[{index}]")
        _exact_keys(
            row,
            _DISPOSITION_KEYS,
            label=f"shadow dispositions[{index}]",
        )
        case_id = _required_text(
            row.get("caseId"),
            label=f"shadow dispositions[{index}].caseId",
        )
        if case_id in case_ids:
            raise BurnInV2Error(f"duplicate shadow caseId: {case_id}")
        case_ids.add(case_id)
        outcome = _required_text(
            row.get("outcome"),
            label=f"shadow dispositions[{index}].outcome",
        )
        if outcome not in _DISPOSITION_OUTCOMES:
            raise BurnInV2Error(f"unsupported shadow outcome: {outcome}")
        _required_text(
            row.get("rationale"),
            label=f"shadow dispositions[{index}].rationale",
        )
        outcomes[outcome] += 1
    if case_ids != set(expected_case_ids):
        raise BurnInV2Error(
            "shadow dispositions do not match the out-of-band representative case set"
        )
    expected_counts = {
        "caseCount": len(dispositions),
        "wrongAnswers": outcomes["WRONG_ANSWER"],
        "staleLeaks": outcomes["STALE_LEAK"],
        "candidateCompletions": outcomes["CANDIDATE_COMPLETION"],
        "undispositioned": outcomes["UNDISPOSITIONED"],
    }
    for field, expected in expected_counts.items():
        actual = _integer(
            artifact.get(field),
            label=f"shadow {field}",
            minimum=0,
        )
        if actual != expected:
            raise BurnInV2Error(f"shadow {field} does not match disposition rows")
    for field in (
        "wrongAnswers",
        "staleLeaks",
        "candidateCompletions",
        "undispositioned",
    ):
        if expected_counts[field] != 0:
            raise BurnInV2Error(f"shadow {field} must be zero")
    _required_text(artifact.get("command"), label="shadow command")
    _required_text(artifact.get("toolVersion"), label="shadow toolVersion")
    claim = {key: artifact[key] for key in _SHADOW_CLAIM_KEYS}
    _exact_keys(
        _mapping(_json_plain(verified.claim), label="shadow claim"),
        _SHADOW_CLAIM_KEYS,
        label="shadow claim",
    )
    _claim_equals(verified, claim, label=label)
    _validate_times(artifact, verified, label=label)
    return verified


def _record_artifact(
    verified: VerifiedSignedReceipt,
    *,
    artifacts: dict[str, bytes],
    artifact_shas: set[str],
) -> None:
    if verified.artifact_uri in artifacts:
        raise BurnInV2Error(f"receipt artifact URI is reused: {verified.artifact_uri}")
    if verified.artifact_sha256 in artifact_shas:
        raise BurnInV2Error(
            "receipt artifact SHA-256 is reused across independent receipts"
        )
    artifacts[verified.artifact_uri] = verified.artifact_bytes
    artifact_shas.add(verified.artifact_sha256)


def validate_burn_in_attestation_v2(
    attestation: Mapping[str, object],
    *,
    registry: Mapping[str, object],
    expected_registry_sha256: str,
    artifact_root: Path,
    snapshot_root: Path,
    expected_burn_in_run_id: str,
    expected_candidate_build_id: str,
    expected_previous_build_id: str,
    expected_previous_manifest_sha256: str,
    expected_representative_corpus_id: str,
    expected_representative_corpus_sha256: str,
    expected_representative_case_ids: tuple[str, ...] | list[str],
    trust_context: str = PRODUCTION,
    verification_time: datetime | None = None,
) -> ValidatedBurnInV2:
    """Validate a complete v2 bundle without mutating snapshot state."""

    if not isinstance(attestation, Mapping):
        raise BurnInV2Error("burn-in attestation must be an object")
    _exact_keys(attestation, _ATTESTATION_KEYS, label="attestation")
    if (
        attestation.get("schema") != BURN_IN_ATTESTATION_V2_SCHEMA
        or attestation.get("policyVersion") != BURN_IN_POLICY_V2
        or attestation.get("status") != "PASSED"
    ):
        raise BurnInV2Error("burn-in attestation identity is invalid")
    burn_in_run_id = _run_id(
        expected_burn_in_run_id,
        label="expected burnInRunId",
    )
    candidate_build_id = _build_id(
        expected_candidate_build_id,
        label="expected candidateBuildId",
    )
    previous_build_id = _build_id(
        expected_previous_build_id,
        label="expected previousBuildId",
    )
    previous_manifest_sha = _sha256_text(
        expected_previous_manifest_sha256,
        label="expected previousManifestSha256",
    )
    registry_sha = _sha256_text(
        expected_registry_sha256,
        label="expected out-of-band registry SHA-256",
    )
    representative_corpus_id = _required_text(
        expected_representative_corpus_id,
        label="expected representative corpusId",
    )
    representative_corpus_sha = _sha256_text(
        expected_representative_corpus_sha256,
        label="expected representative corpusSha256",
    )
    representative_case_ids = _representative_case_ids(expected_representative_case_ids)
    if (
        attestation.get("burnInRunId") != burn_in_run_id
        or attestation.get("candidateBuildId") != candidate_build_id
    ):
        raise BurnInV2Error("burn-in attestation run or candidate identity is invalid")
    approval = _mapping(
        attestation.get("operatorApproval"),
        label="operatorApproval",
    )
    replay_guard = ReceiptReplayGuard()
    top = _verify_receipt(
        approval,
        registry=registry,
        expected_registry_sha256=registry_sha,
        expected_scope={
            "kind": "BURN_IN_ATTESTATION",
            "policyVersion": BURN_IN_POLICY_V2,
            "burnInRunId": burn_in_run_id,
            "candidateBuildId": candidate_build_id,
            "previousBuildId": previous_build_id,
            "previousManifestSha256": previous_manifest_sha,
        },
        artifact_root=artifact_root,
        replay_guard=replay_guard,
        trust_context=trust_context,
        verification_time=verification_time,
    )
    bundle_uri = _required_text(
        attestation.get("evidenceBundleUri"),
        label="evidenceBundleUri",
    )
    bundle_sha = _sha256_text(
        attestation.get("evidenceBundleSha256"),
        label="evidenceBundleSha256",
    )
    if top.artifact_uri != bundle_uri or top.artifact_sha256 != bundle_sha:
        raise BurnInV2Error("top approval does not bind the declared evidence bundle")
    bundle = strict_json_object_from_bytes(
        top.artifact_bytes,
        label="burn-in evidence bundle",
    )
    _exact_keys(bundle, _BUNDLE_KEYS, label="evidence bundle")
    if (
        bundle.get("schema") != BURN_IN_EVIDENCE_BUNDLE_V2_SCHEMA
        or bundle.get("policyVersion") != BURN_IN_POLICY_V2
        or bundle.get("burnInRunId") != burn_in_run_id
        or bundle.get("candidateBuildId") != candidate_build_id
        or bundle.get("previousBuildId") != previous_build_id
        or bundle.get("previousManifestSha256") != previous_manifest_sha
    ):
        raise BurnInV2Error("burn-in evidence bundle identity is invalid")
    snapshots = _validate_snapshot_chain(
        bundle,
        snapshot_root=snapshot_root,
        expected_previous_build_id=previous_build_id,
        expected_previous_manifest_sha256=previous_manifest_sha,
    )
    build_ids = tuple(snapshot.build_id for snapshot in snapshots)
    full_chain = _snapshot_chain_to_tip(snapshot_root, previous_build_id)
    published_chain_pairs = frozenset(
        (parent.build_id, child.build_id)
        for parent, child in zip(full_chain, full_chain[1:])
    )
    raw_scenarios = _mapping(
        bundle.get("incrementalScenarioReceipts"),
        label="incrementalScenarioReceipts",
    )
    _exact_keys(
        raw_scenarios,
        set(REQUIRED_INCREMENTAL_SCENARIOS),
        label="incrementalScenarioReceipts",
    )
    artifacts: dict[str, bytes] = {top.artifact_uri: top.artifact_bytes}
    artifact_shas = {top.artifact_sha256}
    verified_receipts: list[VerifiedSignedReceipt] = [top]
    published_transitions: set[tuple[str, str]] = set()
    for scenario_id in REQUIRED_INCREMENTAL_SCENARIOS:
        verified, transition = _validate_incremental_receipt(
            scenario_id,
            raw_scenarios[scenario_id],
            expected_burn_in_run_id=burn_in_run_id,
            expected_candidate_build_id=candidate_build_id,
            sealed_snapshots=snapshots,
            published_chain_pairs=published_chain_pairs,
            registry=registry,
            expected_registry_sha256=registry_sha,
            artifact_root=artifact_root,
            snapshot_root=snapshot_root,
            replay_guard=replay_guard,
            trust_context=trust_context,
            verification_time=verification_time,
        )
        if transition is not None:
            if transition in published_transitions:
                raise BurnInV2Error(
                    "published incremental scenario transition is reused"
                )
            published_transitions.add(transition)
        _record_artifact(
            verified,
            artifacts=artifacts,
            artifact_shas=artifact_shas,
        )
        verified_receipts.append(verified)
    rollback = _validate_rollback_receipt(
        bundle.get("rollbackReceipt"),
        expected_burn_in_run_id=burn_in_run_id,
        expected_candidate_build_id=candidate_build_id,
        sealed_snapshots=snapshots,
        registry=registry,
        expected_registry_sha256=registry_sha,
        artifact_root=artifact_root,
        snapshot_root=snapshot_root,
        replay_guard=replay_guard,
        trust_context=trust_context,
        verification_time=verification_time,
    )
    _record_artifact(
        rollback,
        artifacts=artifacts,
        artifact_shas=artifact_shas,
    )
    verified_receipts.append(rollback)
    concurrent = _validate_concurrent_reader_receipt(
        bundle.get("concurrentReaderReceipt"),
        expected_burn_in_run_id=burn_in_run_id,
        expected_candidate_build_id=candidate_build_id,
        sealed_snapshots=snapshots,
        registry=registry,
        expected_registry_sha256=registry_sha,
        artifact_root=artifact_root,
        snapshot_root=snapshot_root,
        replay_guard=replay_guard,
        trust_context=trust_context,
        verification_time=verification_time,
    )
    _record_artifact(
        concurrent,
        artifacts=artifacts,
        artifact_shas=artifact_shas,
    )
    verified_receipts.append(concurrent)
    shadow = _validate_shadow_disposition_receipt(
        bundle.get("shadowDiffDispositionReceipt"),
        expected_burn_in_run_id=burn_in_run_id,
        expected_candidate_build_id=candidate_build_id,
        expected_build_ids=build_ids,
        expected_corpus_id=representative_corpus_id,
        expected_corpus_sha256=representative_corpus_sha,
        expected_case_ids=representative_case_ids,
        registry=registry,
        expected_registry_sha256=registry_sha,
        artifact_root=artifact_root,
        replay_guard=replay_guard,
        trust_context=trust_context,
        verification_time=verification_time,
    )
    _record_artifact(
        shadow,
        artifacts=artifacts,
        artifact_shas=artifact_shas,
    )
    verified_receipts.append(shadow)
    top_claim = _mapping(_json_plain(top.claim), label="top approval claim")
    _exact_keys(top_claim, _TOP_CLAIM_KEYS, label="top approval claim")
    expected_top_claim = {
        "status": "PASSED",
        "sealedSnapshotCount": 3,
        "incrementalScenarioCount": len(REQUIRED_INCREMENTAL_SCENARIOS),
        "rollbackReceiptCount": 1,
        "concurrentReaderReceiptCount": 1,
        "shadowDispositionReceiptCount": 1,
    }
    _claim_equals(top, expected_top_claim, label="top approval")
    component_issued_at = [receipt.issued_at for receipt in verified_receipts[1:]]
    if component_issued_at and top.issued_at < max(component_issued_at):
        raise BurnInV2Error("top approval must not predate any component receipt")
    return ValidatedBurnInV2(
        validated=True,
        production_eligible=trust_context == PRODUCTION,
        trust_context=trust_context,
        burn_in_run_id=burn_in_run_id,
        candidate_build_id=candidate_build_id,
        previous_build_id=previous_build_id,
        previous_manifest_sha256=previous_manifest_sha,
        registry_version_sha256=registry_sha,
        operator_signer_id=top.signer_id,
        operator_receipt_id=top.receipt_id,
        evidence_bundle_uri=bundle_uri,
        evidence_bundle_sha256=bundle_sha,
        evidence_bundle_bytes=top.artifact_bytes,
        representative_corpus_id=representative_corpus_id,
        representative_corpus_sha256=representative_corpus_sha,
        representative_case_ids=representative_case_ids,
        sealed_snapshot_build_ids=build_ids,
        receipt_ids=tuple(receipt.receipt_id for receipt in verified_receipts),
        artifact_bytes_by_uri=MappingProxyType(dict(artifacts)),
    )


__all__ = [
    "BURN_IN_ATTESTATION_V2_SCHEMA",
    "BURN_IN_EVIDENCE_BUNDLE_V2_SCHEMA",
    "BURN_IN_POLICY_V2",
    "NO_PUBLISH_INCREMENTAL_SCENARIOS",
    "PUBLISHED_INCREMENTAL_SCENARIOS",
    "BurnInV2Error",
    "ValidatedBurnInV2",
    "strict_json_object_from_bytes",
    "validate_burn_in_attestation_v2",
]
