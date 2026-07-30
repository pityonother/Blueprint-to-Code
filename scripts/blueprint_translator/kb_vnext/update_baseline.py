"""Immutable baseline and receipt inspection for one additive update.

This module does not run narrow gates, publish a snapshot, or switch the
current pointer.  Production artifact authorization is deliberately absent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .incremental_delta import (
    AddOnlyDeltaBlockedGap,
    validate_add_only_delta_receipt,
)
from .pointer_cas import (
    SNAPSHOT_SCHEMA,
    CurrentSnapshotBaseline,
    capture_current_snapshot_baseline,
    validate_current_snapshot_baseline,
)
from .source_manifest import (
    SourceDiff,
    SourceManifest,
    canonical_source_diff_bytes,
    compare_source_manifests,
    source_diff_sha256,
    source_manifest_from_binding,
)


UPDATE_BASELINE_SCHEMA: Final = "ark-kb-update-baseline/v1"
PREPUBLICATION_DELTA_INSPECTION_SCHEMA: Final = (
    "ark-kb-prepublication-delta-inspection/v1"
)
MAX_DELTA_RECEIPT_BYTES: Final = 4 * 1024 * 1024
_MAX_SNAPSHOT_MANIFEST_BYTES: Final = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DELTA_PROOF = re.compile(r"^delta-proof://([0-9a-f]{64})$")


class UpdateBaselineBlockedGap(ValueError):
    """One stable pre-publication blocker with no pointer mutation."""

    status = "BLOCKED_GAP"

    def __init__(
        self,
        gap_code: str,
        message: str,
        *,
        status: str = "BLOCKED_GAP",
        residual_identifier: str = "",
    ) -> None:
        super().__init__(message)
        self.gap_code = gap_code
        self.status = status
        self.residual_identifier = residual_identifier


def _gap(code: str, message: str) -> UpdateBaselineBlockedGap:
    return UpdateBaselineBlockedGap(code, message)


def _sha256(value: object, *, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise _gap(
            "UPDATE_BASELINE_CONTRACT_INVALID",
            f"{label} must be lowercase hexadecimal SHA-256",
        )
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _reject_json_float(value: str) -> object:
    raise ValueError(f"floating JSON number is forbidden: {value}")


def _strict_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _strict_json_object(
    raw: bytes,
    *,
    label: str,
    maximum_bytes: int,
) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > maximum_bytes:
        raise ValueError(f"{label} size is invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_float=_reject_json_float,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _base_source_manifest_from_snapshot(
    current_snapshot: CurrentSnapshotBaseline,
) -> SourceManifest:
    manifest = _strict_json_object(
        current_snapshot.manifest_bytes,
        label="base snapshot manifest",
        maximum_bytes=_MAX_SNAPSHOT_MANIFEST_BYTES,
    )
    if (
        manifest.get("schema") != SNAPSHOT_SCHEMA
        or manifest.get("buildId")
        != current_snapshot.pointer.build_id
    ):
        raise ValueError("base snapshot manifest identity is invalid")
    return source_manifest_from_binding(manifest.get("incrementalUpdate"))


@dataclass(frozen=True)
class UpdateBaseline:
    """Exact base/candidate identity recomputed from immutable inputs."""

    snapshot_root: Path
    current_snapshot: CurrentSnapshotBaseline
    base_source_manifest: SourceManifest
    candidate_source_manifest: SourceManifest
    source_diff: SourceDiff
    source_diff_bytes: bytes
    source_diff_sha256: str
    schema: str = UPDATE_BASELINE_SCHEMA
    evidence_class: str = "UNSIGNED_LOCAL_UPDATE_BASELINE"
    tree_validated: bool = False
    production_authority: bool = False
    published: bool = False
    e4_scenario_2_complete: bool = False

    def __post_init__(self) -> None:
        if (
            self.schema != UPDATE_BASELINE_SCHEMA
            or self.evidence_class != "UNSIGNED_LOCAL_UPDATE_BASELINE"
            or self.tree_validated is not False
            or self.production_authority is not False
            or self.published is not False
            or self.e4_scenario_2_complete is not False
            or not isinstance(self.snapshot_root, Path)
            or type(self.current_snapshot) is not CurrentSnapshotBaseline
            or type(self.base_source_manifest) is not SourceManifest
            or type(self.candidate_source_manifest) is not SourceManifest
            or type(self.source_diff) is not SourceDiff
            or type(self.source_diff_bytes) is not bytes
        ):
            raise ValueError("update baseline contract is invalid")
        validate_current_snapshot_baseline(
            snapshot_root=self.snapshot_root,
            baseline=self.current_snapshot,
        )
        observed_diff = compare_source_manifests(
            self.base_source_manifest,
            self.candidate_source_manifest,
        )
        observed_bytes = canonical_source_diff_bytes(observed_diff)
        observed_sha256 = hashlib.sha256(observed_bytes).hexdigest()
        if (
            _base_source_manifest_from_snapshot(self.current_snapshot)
            != self.base_source_manifest
            or self.source_diff != observed_diff
            or self.source_diff_bytes != observed_bytes
            or self.source_diff_sha256 != observed_sha256
        ):
            raise ValueError(
                "update baseline source diff does not match manifests"
            )

    @property
    def base_build_id(self) -> str:
        return str(self.current_snapshot.pointer.build_id)

    @property
    def base_pointer_sha256(self) -> str:
        return str(self.current_snapshot.pointer.pointer_sha256)

    @property
    def base_manifest_sha256(self) -> str:
        return self.current_snapshot.manifest_sha256

    @property
    def base_source_manifest_fingerprint(self) -> str:
        return self.base_source_manifest.fingerprint

    @property
    def candidate_source_manifest_fingerprint(self) -> str:
        return self.candidate_source_manifest.fingerprint

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "evidenceClass": self.evidence_class,
            "baseBuildId": self.base_build_id,
            "basePointerSha256": self.base_pointer_sha256,
            "baseManifestSha256": self.base_manifest_sha256,
            "baseSourceManifestFingerprint": (
                self.base_source_manifest_fingerprint
            ),
            "candidateSourceManifestFingerprint": (
                self.candidate_source_manifest_fingerprint
            ),
            "sourceDiffSha256": self.source_diff_sha256,
            "treeValidated": False,
            "productionAuthority": False,
            "published": False,
            "e4Scenario2Complete": False,
        }


def build_update_baseline(
    *,
    snapshot_root: Path,
    candidate_source_manifest: SourceManifest,
    expected_current_snapshot: CurrentSnapshotBaseline | None = None,
) -> UpdateBaseline:
    """Capture and bind a candidate to one strict raw current manifest."""

    if not isinstance(snapshot_root, Path):
        raise TypeError("snapshot root must be a Path")
    if type(candidate_source_manifest) is not SourceManifest:
        raise TypeError("candidate source manifest is required")
    if (
        expected_current_snapshot is not None
        and type(expected_current_snapshot) is not CurrentSnapshotBaseline
    ):
        raise TypeError("expected current snapshot baseline is invalid")
    root = snapshot_root.resolve()
    current_snapshot = capture_current_snapshot_baseline(root)
    if (
        expected_current_snapshot is not None
        and current_snapshot != expected_current_snapshot
    ):
        raise _gap(
            "UPDATE_BASELINE_IDENTITY_CHANGED",
            "current build, raw pointer, or manifest identity changed "
            "while the update baseline was captured",
        )
    try:
        base_source_manifest = _base_source_manifest_from_snapshot(
            current_snapshot
        )
    except (TypeError, ValueError) as exc:
        raise _gap(
            "BASE_SOURCE_MANIFEST_INVALID",
            "base snapshot has no valid incremental source binding",
        ) from exc
    diff = compare_source_manifests(
        base_source_manifest,
        candidate_source_manifest,
    )
    encoded = canonical_source_diff_bytes(diff)
    return UpdateBaseline(
        snapshot_root=root,
        current_snapshot=current_snapshot,
        base_source_manifest=base_source_manifest,
        candidate_source_manifest=candidate_source_manifest,
        source_diff=diff,
        source_diff_bytes=encoded,
        source_diff_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def validate_update_baseline_identity(
    baseline: UpdateBaseline,
    *,
    expected_current_snapshot: CurrentSnapshotBaseline,
    expected_candidate_source_manifest: SourceManifest,
) -> UpdateBaseline:
    """Revalidate one exact pointer, manifest, candidate, and source diff."""

    if type(baseline) is not UpdateBaseline:
        raise TypeError("update baseline is required")
    if type(expected_current_snapshot) is not CurrentSnapshotBaseline:
        raise TypeError("expected current snapshot baseline is required")
    if type(expected_candidate_source_manifest) is not SourceManifest:
        raise TypeError("expected candidate source manifest is required")
    validate_current_snapshot_baseline(
        snapshot_root=baseline.snapshot_root,
        baseline=expected_current_snapshot,
    )
    observed_diff = compare_source_manifests(
        baseline.base_source_manifest,
        expected_candidate_source_manifest,
    )
    if (
        baseline.current_snapshot != expected_current_snapshot
        or baseline.candidate_source_manifest.fingerprint
        != expected_candidate_source_manifest.fingerprint
        or _base_source_manifest_from_snapshot(
            expected_current_snapshot
        )
        != baseline.base_source_manifest
        or observed_diff != baseline.source_diff
        or canonical_source_diff_bytes(observed_diff)
        != baseline.source_diff_bytes
        or source_diff_sha256(observed_diff)
        != baseline.source_diff_sha256
    ):
        raise _gap(
            "UPDATE_BASELINE_IDENTITY_CHANGED",
            "current build, raw pointer, manifest, candidate, or source diff "
            "no longer matches the locked update baseline",
        )
    return baseline


def validate_final_source_manifest(
    baseline: UpdateBaseline,
    observed_candidate: SourceManifest,
) -> SourceManifest:
    """Require the final live scan to keep the initial source identity."""

    if type(baseline) is not UpdateBaseline:
        raise TypeError("update baseline is required")
    if type(observed_candidate) is not SourceManifest:
        raise TypeError("observed source manifest is required")
    observed_diff = compare_source_manifests(
        baseline.base_source_manifest,
        observed_candidate,
    )
    if (
        observed_candidate.fingerprint
        != baseline.candidate_source_manifest.fingerprint
        or canonical_source_diff_bytes(observed_diff)
        != baseline.source_diff_bytes
        or source_diff_sha256(observed_diff)
        != baseline.source_diff_sha256
    ):
        raise _gap(
            "SOURCE_MANIFEST_CHANGED_DURING_UPDATE",
            "candidate source manifest changed during the update",
        )
    return observed_candidate


@dataclass(frozen=True)
class StagedBaselineSnapshot:
    """One independent unpublished copy of a verified baseline snapshot."""

    base_build_id: str
    staging_id: str
    temporary_root: Path
    snapshot_dir: Path
    manifest_sha256: str
    copied_files: int
    receipt: dict[str, object]
    cleanup_identity: tuple[object, ...]


def stage_snapshot_from_baseline(
    baseline: UpdateBaseline,
    *,
    destination: Path,
    fault_injector: Callable[[str, str], None] | None = None,
) -> StagedBaselineSnapshot:
    """Stage one exact whole tree through no-follow parent handles."""

    if type(baseline) is not UpdateBaseline:
        raise TypeError("update baseline is required")
    if not isinstance(destination, Path):
        raise TypeError("staging destination must be a Path")
    if fault_injector is not None and not callable(fault_injector):
        raise TypeError("staging fault injector must be callable")

    from .safe_staging import SafeStagingError, stage_snapshot_tree

    def revalidate() -> None:
        validate_update_baseline_identity(
            baseline,
            expected_current_snapshot=baseline.current_snapshot,
            expected_candidate_source_manifest=(
                baseline.candidate_source_manifest
            ),
        )

    try:
        staged = stage_snapshot_tree(
            baseline,
            staging_root=destination,
            validate_baseline=revalidate,
            fault_injector=fault_injector,
        )
    except SafeStagingError as exc:
        raise UpdateBaselineBlockedGap(
            exc.gap_code,
            str(exc),
            status=exc.status,
            residual_identifier=exc.residual_identifier,
        ) from exc
    return StagedBaselineSnapshot(
        base_build_id=staged.base_build_id,
        staging_id=staged.staging_id,
        temporary_root=staged.temporary_root,
        snapshot_dir=staged.snapshot_dir,
        manifest_sha256=staged.manifest_sha256,
        copied_files=staged.copied_files,
        receipt=staged.receipt,
        cleanup_identity=staged.cleanup_identity,
    )


def cleanup_staged_baseline_snapshot(
    staged: StagedBaselineSnapshot,
    *,
    snapshot_root: Path,
) -> None:
    """Remove one returned staging tree or report cleanup uncertainty."""

    if type(staged) is not StagedBaselineSnapshot:
        raise TypeError("staged baseline snapshot is required")
    if not isinstance(snapshot_root, Path):
        raise TypeError("snapshot root must be a Path")
    from .safe_staging import SafeStagingError, cleanup_staged_snapshot

    try:
        cleanup_staged_snapshot(
            snapshot_root=snapshot_root,
            staging_id=staged.staging_id,
            expected_identity=staged.cleanup_identity,
        )
    except SafeStagingError as exc:
        raise UpdateBaselineBlockedGap(
            exc.gap_code,
            str(exc),
            status=exc.status,
            residual_identifier=exc.residual_identifier,
        ) from exc


def freeze_additive_blueprint_input(
    baseline: UpdateBaseline,
    *,
    capture_root: Path,
    quarantine_root: Path,
) -> None:
    """Fail closed until reparse-safe additive quarantine is available."""

    if type(baseline) is not UpdateBaseline:
        raise TypeError("update baseline is required")
    if not isinstance(capture_root, Path) or not isinstance(
        quarantine_root,
        Path,
    ):
        raise TypeError("capture and quarantine roots must be Paths")
    raise _gap(
        "REPARSE_SAFE_ADDITIVE_QUARANTINE_UNAVAILABLE",
        "additive quarantine is unavailable until source and destination "
        "directories can be pinned against path replacement",
    )


@dataclass(frozen=True)
class PrepublicationDeltaInspection:
    """Unsigned receipt inspection with no base or production authority."""

    source_diff_sha256: str
    expected_receipt_raw_sha256: str
    receipt_artifact_sha256: str
    receipt_content_sha256: str
    trust_context: str
    schema: str = PREPUBLICATION_DELTA_INSPECTION_SCHEMA
    evidence_class: str = "UNSIGNED_LOCAL_PREPUBLICATION_INSPECTION"
    base_binding_verified: bool = False
    production_authority: bool = False
    published: bool = False
    e4_scenario_2_complete: bool = False

    def __post_init__(self) -> None:
        if (
            self.schema != PREPUBLICATION_DELTA_INSPECTION_SCHEMA
            or self.evidence_class
            != "UNSIGNED_LOCAL_PREPUBLICATION_INSPECTION"
            or self.trust_context != "TEST_ONLY"
            or self.base_binding_verified is not False
            or self.production_authority is not False
            or self.published is not False
            or self.e4_scenario_2_complete is not False
        ):
            raise ValueError(
                "prepublication delta inspection contract is invalid"
            )
        for label, value in (
            ("sourceDiffSha256", self.source_diff_sha256),
            (
                "expectedReceiptRawSha256",
                self.expected_receipt_raw_sha256,
            ),
            (
                "receiptArtifactSha256",
                self.receipt_artifact_sha256,
            ),
            ("receiptContentSha256", self.receipt_content_sha256),
        ):
            _sha256(value, label=label)
        if (
            self.expected_receipt_raw_sha256
            != self.receipt_artifact_sha256
        ):
            raise ValueError(
                "prepublication delta inspection identity is invalid"
            )

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "evidenceClass": self.evidence_class,
            "sourceDiffSha256": self.source_diff_sha256,
            "expectedReceiptRawSha256": (
                self.expected_receipt_raw_sha256
            ),
            "receiptArtifactSha256": self.receipt_artifact_sha256,
            "receiptContentSha256": self.receipt_content_sha256,
            "trustContext": self.trust_context,
            "baseBindingVerified": False,
            "productionAuthority": False,
            "published": False,
            "e4Scenario2Complete": False,
        }


def inspect_prepublication_delta_receipt(
    baseline: UpdateBaseline,
    *,
    receipt_bytes: bytes,
    expected_receipt_raw_sha256: str,
    production: bool = False,
) -> PrepublicationDeltaInspection:
    """Inspect authenticated TEST_ONLY receipt bytes without base binding."""

    if type(baseline) is not UpdateBaseline:
        raise TypeError("update baseline is required")
    if type(production) is not bool:
        raise _gap(
            "UPDATE_BASELINE_CONTRACT_INVALID",
            "production must be an explicit boolean",
        )
    if production:
        raise _gap(
            "PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED",
            "no signed production artifact authorization contract exists",
        )
    if not expected_receipt_raw_sha256:
        raise _gap(
            "MISSING_OUT_OF_BAND_DELTA_RECEIPT_SHA256",
            "expected raw delta receipt SHA-256 is required out of band",
        )
    try:
        expected_raw = _sha256(
            expected_receipt_raw_sha256,
            label="expected raw delta receipt SHA-256",
        )
    except UpdateBaselineBlockedGap as exc:
        raise _gap(
            "OUT_OF_BAND_DELTA_RECEIPT_SHA256_INVALID",
            "expected raw delta receipt SHA-256 is invalid",
        ) from exc
    if (
        type(receipt_bytes) is not bytes
        or not receipt_bytes
        or len(receipt_bytes) > MAX_DELTA_RECEIPT_BYTES
    ):
        raise _gap(
            "DELTA_RECEIPT_ARTIFACT_INVALID",
            "delta receipt artifact size is invalid",
        )
    observed_raw = hashlib.sha256(receipt_bytes).hexdigest()
    if observed_raw != expected_raw:
        raise _gap(
            "OUT_OF_BAND_DELTA_RECEIPT_SHA256_MISMATCH",
            "delta receipt raw bytes do not match the OOB SHA-256",
        )
    try:
        receipt = _strict_json_object(
            receipt_bytes,
            label="delta receipt artifact",
            maximum_bytes=MAX_DELTA_RECEIPT_BYTES,
        )
        proof = receipt.get("proof")
        match = (
            _DELTA_PROOF.fullmatch(proof)
            if type(proof) is str
            else None
        )
        if match is None:
            raise ValueError("delta receipt proof is invalid")
        content_sha256 = match.group(1)
        validated = validate_add_only_delta_receipt(
            receipt,
            expected_receipt_sha256=content_sha256,
        )
    except (
        AddOnlyDeltaBlockedGap,
        TypeError,
        ValueError,
    ) as exc:
        raise _gap(
            "DELTA_RECEIPT_ARTIFACT_INVALID",
            "OOB-authenticated delta receipt semantics are invalid",
        ) from exc
    if validated.get("sourceDiffSha256") != baseline.source_diff_sha256:
        raise _gap(
            "DELTA_RECEIPT_SOURCE_DIFF_MISMATCH",
            "delta receipt is replayed from a different source diff",
        )
    if (
        validated.get("trustContext") != "TEST_ONLY"
        or validated.get("published") is not False
        or validated.get("e4Scenario2Complete") is not False
    ):
        raise _gap(
            "DELTA_RECEIPT_TRUST_CONTEXT_INVALID",
            "prepublication receipt must remain TEST_ONLY and unpublished",
        )
    return PrepublicationDeltaInspection(
        source_diff_sha256=baseline.source_diff_sha256,
        expected_receipt_raw_sha256=expected_raw,
        receipt_artifact_sha256=observed_raw,
        receipt_content_sha256=content_sha256,
        trust_context="TEST_ONLY",
    )


__all__ = [
    "MAX_DELTA_RECEIPT_BYTES",
    "PREPUBLICATION_DELTA_INSPECTION_SCHEMA",
    "UPDATE_BASELINE_SCHEMA",
    "PrepublicationDeltaInspection",
    "StagedBaselineSnapshot",
    "UpdateBaseline",
    "UpdateBaselineBlockedGap",
    "build_update_baseline",
    "freeze_additive_blueprint_input",
    "inspect_prepublication_delta_receipt",
    "stage_snapshot_from_baseline",
    "validate_final_source_manifest",
    "validate_update_baseline_identity",
]
