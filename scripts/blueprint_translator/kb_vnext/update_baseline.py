"""Immutable baseline and receipt inspection for one additive update.

This module does not run narrow gates, publish a snapshot, or switch the
current pointer.  Production artifact authorization is deliberately absent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final
from urllib.parse import quote, unquote

from .incremental_delta import (
    AddOnlyDeltaBlockedGap,
    BlueprintEvidenceBundleInspection,
    inspect_blueprint_evidence_bundle,
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
    SourceRevision,
    canonical_source_diff_bytes,
    compare_source_manifests,
    source_diff_sha256,
    source_manifest_from_binding,
)


UPDATE_BASELINE_SCHEMA: Final = "ark-kb-update-baseline/v1"
PREPUBLICATION_DELTA_INSPECTION_SCHEMA: Final = (
    "ark-kb-prepublication-delta-inspection/v1"
)
ADDITIVE_QUARANTINE_RECEIPT_SCHEMA: Final = (
    "ark-kb-reparse-safe-additive-quarantine-receipt/v1"
)
ADDITIVE_QUARANTINE_EVIDENCE_CLASS: Final = (
    "UNSIGNED_LOCAL_REPARSE_SAFE_ADDITIVE_QUARANTINE"
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


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(child)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ValueError("quarantine receipt contains a non-JSON value")


def _freeze_json(value: object) -> object:
    normalized = _json_value(value)
    if isinstance(normalized, dict):
        return MappingProxyType(
            {
                key: _freeze_json(child)
                for key, child in normalized.items()
            }
        )
    if isinstance(normalized, list):
        return tuple(_freeze_json(child) for child in normalized)
    return normalized


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _json_value(value),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _staging_baseline_identity_sha256(
    baseline: UpdateBaseline,
) -> str:
    return hashlib.sha256(
        json.dumps(
            baseline.payload(),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class FrozenQuarantineArtifact:
    artifact_uri: str
    artifact_sha256: str
    artifact_bytes: int

    def __post_init__(self) -> None:
        relative = (
            self.artifact_uri[len("artifact://") :]
            if self.artifact_uri.startswith("artifact://")
            else ""
        )
        path = PurePosixPath(relative)
        if (
            not relative
            or path.is_absolute()
            or "\\" in relative
            or any(
                part in {"", ".", ".."} or ":" in part
                for part in path.parts
            )
            or not _SHA256.fullmatch(self.artifact_sha256)
            or isinstance(self.artifact_bytes, bool)
            or not isinstance(self.artifact_bytes, int)
            or self.artifact_bytes < 0
        ):
            raise ValueError("frozen quarantine artifact is invalid")

    def payload(self) -> dict[str, object]:
        return {
            "artifactUri": self.artifact_uri,
            "artifactSha256": self.artifact_sha256,
            "artifactBytes": self.artifact_bytes,
        }


@dataclass(frozen=True)
class FrozenArtifactBinding:
    source_id: str
    source_fingerprint: str
    evidence: FrozenQuarantineArtifact
    manifest: FrozenQuarantineArtifact | None

    def __post_init__(self) -> None:
        if (
            not _SHA256.fullmatch(self.source_id)
            or not _SHA256.fullmatch(self.source_fingerprint)
            or type(self.evidence) is not FrozenQuarantineArtifact
            or (
                self.manifest is not None
                and type(self.manifest) is not FrozenQuarantineArtifact
            )
        ):
            raise ValueError("frozen artifact binding is invalid")


_QUARANTINE_RECEIPT_FIELDS: Final = {
    "schema",
    "evidenceClass",
    "baseBuildId",
    "pointerSha256",
    "manifestSha256",
    "baseSourceManifestFingerprint",
    "candidateSourceManifestFingerprint",
    "sourceDiffSha256",
    "updateBaselineIdentitySha256",
    "stagingReceiptProof",
    "stagingTreeDigest",
    "sourceId",
    "entityUri",
    "revisionLabel",
    "sourceFingerprint",
    "artifactUri",
    "artifactSha256",
    "artifactBytes",
    "manifestArtifact",
    "sourceAggregateSha256",
    "quarantineTreeDigest",
    "quarantineRelativePath",
    "sameVolume",
    "sourceVerifiedUnchanged",
    "exactArtifactSet",
    "reparsePointCount",
    "hardlinkAliasCount",
    "createdAt",
    "published",
    "productionAuthority",
    "e4Scenario2Complete",
    "cutoverEligible",
    "mode",
    "defaultQuerySource",
    "proof",
}


def _validate_quarantine_receipt(
    receipt: Mapping[str, object],
    *,
    binding: FrozenArtifactBinding,
    base_build_id: str,
    staging_id: str,
    entity_uri: str,
    revision_label: str,
    pointer_sha256: str,
    manifest_sha256: str,
    base_source_manifest_fingerprint: str,
    candidate_source_manifest_fingerprint: str,
    source_diff_sha256_value: str,
    update_baseline_identity_sha256: str,
    staging_receipt_proof: str,
    staging_tree_digest: str,
    quarantine_tree_digest: str,
    created_at: str,
) -> None:
    if set(receipt) != _QUARANTINE_RECEIPT_FIELDS:
        raise _gap(
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            "quarantine receipt fields are invalid",
        )
    body = {
        key: _json_value(value)
        for key, value in receipt.items()
        if key != "proof"
    }
    proof = receipt.get("proof")
    expected_proof = "quarantine-proof://" + _canonical_sha256(body)
    manifest_artifact = receipt.get("manifestArtifact")
    expected_manifest = (
        binding.manifest.payload()
        if binding.manifest is not None
        else None
    )
    fixed = {
        "schema": ADDITIVE_QUARANTINE_RECEIPT_SCHEMA,
        "evidenceClass": ADDITIVE_QUARANTINE_EVIDENCE_CLASS,
        "baseBuildId": base_build_id,
        "pointerSha256": pointer_sha256,
        "manifestSha256": manifest_sha256,
        "baseSourceManifestFingerprint": (
            base_source_manifest_fingerprint
        ),
        "candidateSourceManifestFingerprint": (
            candidate_source_manifest_fingerprint
        ),
        "sourceDiffSha256": source_diff_sha256_value,
        "updateBaselineIdentitySha256": (
            update_baseline_identity_sha256
        ),
        "stagingReceiptProof": staging_receipt_proof,
        "stagingTreeDigest": staging_tree_digest,
        "sourceId": binding.source_id,
        "entityUri": entity_uri,
        "revisionLabel": revision_label,
        "sourceFingerprint": binding.source_fingerprint,
        "artifactUri": binding.evidence.artifact_uri,
        "artifactSha256": binding.evidence.artifact_sha256,
        "artifactBytes": binding.evidence.artifact_bytes,
        "manifestArtifact": expected_manifest,
        "sourceAggregateSha256": binding.source_fingerprint,
        "quarantineTreeDigest": quarantine_tree_digest,
        "sameVolume": True,
        "sourceVerifiedUnchanged": True,
        "exactArtifactSet": True,
        "reparsePointCount": 0,
        "hardlinkAliasCount": 0,
        "createdAt": created_at,
        "published": False,
        "productionAuthority": False,
        "e4Scenario2Complete": False,
        "cutoverEligible": False,
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    relative = str(receipt.get("quarantineRelativePath") or "")
    if (
        any(receipt.get(key) != value for key, value in fixed.items())
        or manifest_artifact != expected_manifest
        or relative
        != (
            f".incremental-staging/{staging_id}/quarantine/"
            f"{binding.source_id}"
        )
        or proof != expected_proof
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            "quarantine receipt identity or proof is invalid",
        )


@dataclass(frozen=True)
class FrozenAdditiveBlueprintInput:
    base_build_id: str
    staging_id: str
    source_id: str
    entity_uri: str
    revision_label: str
    source_fingerprint: str
    pointer_sha256: str
    manifest_sha256: str
    base_source_manifest_fingerprint: str
    candidate_source_manifest_fingerprint: str
    source_diff_sha256: str
    update_baseline_identity_sha256: str
    staging_receipt_proof: str
    staging_tree_digest: str
    quarantine_tree_digest: str
    created_at: str
    quarantine_root: Path
    ingest_root: Path
    artifact_bindings: tuple[FrozenArtifactBinding, ...]
    receipt: Mapping[str, object]
    cleanup_identity: tuple[object, ...]
    safe_bundle: object

    def __post_init__(self) -> None:
        from .safe_staging import SafeFrozenBlueprintBundle

        if not isinstance(self.receipt, MappingProxyType):
            raise ValueError(
                "frozen additive Blueprint receipt must be immutable"
            )
        if (
            not self.base_build_id
            or not re.fullmatch(r"[0-9a-f]{32}", self.staging_id)
            or not _SHA256.fullmatch(self.source_id)
            or not self.entity_uri
            or not self.revision_label
            or not _SHA256.fullmatch(self.source_fingerprint)
            or not _SHA256.fullmatch(self.pointer_sha256)
            or not _SHA256.fullmatch(self.manifest_sha256)
            or not _SHA256.fullmatch(
                self.base_source_manifest_fingerprint
            )
            or not _SHA256.fullmatch(
                self.candidate_source_manifest_fingerprint
            )
            or not _SHA256.fullmatch(self.source_diff_sha256)
            or not _SHA256.fullmatch(
                self.update_baseline_identity_sha256
            )
            or not re.fullmatch(
                r"staging-proof://[0-9a-f]{64}",
                self.staging_receipt_proof,
            )
            or not _SHA256.fullmatch(self.staging_tree_digest)
            or not _SHA256.fullmatch(self.quarantine_tree_digest)
            or not self.created_at.endswith("+00:00")
            or not isinstance(self.quarantine_root, Path)
            or self.ingest_root
            != self.quarantine_root / self.source_id
            or type(self.artifact_bindings) is not tuple
            or len(self.artifact_bindings) != 1
            or self.artifact_bindings[0].source_id != self.source_id
            or self.artifact_bindings[0].source_fingerprint
            != self.source_fingerprint
            or not isinstance(self.cleanup_identity, tuple)
            or not self.cleanup_identity
            or type(self.safe_bundle) is not SafeFrozenBlueprintBundle
        ):
            raise ValueError("frozen additive Blueprint input is invalid")
        _validate_quarantine_receipt(
            self.receipt,
            binding=self.artifact_bindings[0],
            base_build_id=self.base_build_id,
            staging_id=self.staging_id,
            entity_uri=self.entity_uri,
            revision_label=self.revision_label,
            pointer_sha256=self.pointer_sha256,
            manifest_sha256=self.manifest_sha256,
            base_source_manifest_fingerprint=(
                self.base_source_manifest_fingerprint
            ),
            candidate_source_manifest_fingerprint=(
                self.candidate_source_manifest_fingerprint
            ),
            source_diff_sha256_value=self.source_diff_sha256,
            update_baseline_identity_sha256=(
                self.update_baseline_identity_sha256
            ),
            staging_receipt_proof=self.staging_receipt_proof,
            staging_tree_digest=self.staging_tree_digest,
            quarantine_tree_digest=self.quarantine_tree_digest,
            created_at=self.created_at,
        )


def _single_additive_blueprint_revision(
    baseline: UpdateBaseline,
) -> SourceRevision:
    changes = baseline.source_diff
    captures = [
        change
        for change in changes.changed
        if (
            change.previous is not None
            and change.current is not None
            and change.previous.source_kind
            == change.current.source_kind
            == "SEMANTIC_INPUT"
            and change.previous.source_uri
            == change.current.source_uri
            == "semantic-input://captures"
        )
    ]
    additions = [
        change.current
        for change in changes.added
        if (
            change.previous is None
            and change.current is not None
            and change.current.source_kind == "BLUEPRINT_EVIDENCE"
        )
    ]
    if (
        len(additions) != 1
        or len(captures) != 1
        or len(changes.added) != 1
        or len(changes.changed) != 1
        or changes.deleted
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_REQUIRES_SINGLE_BLUEPRINT",
            "quarantine requires exactly one add-only Blueprint Evidence "
            "revision and the captures aggregate change",
        )
    revision = additions[0]
    if (
        revision.source_id != changes.added[0].source_id
        or revision not in baseline.candidate_source_manifest.entries
        or not revision.entity_uri
        or not revision.revision_label
        or revision.size_bytes < 1
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_BASELINE_CHANGED",
            "the additive Blueprint revision is not bound to the candidate",
        )
    return revision


def _capture_bundle_directory(revision: SourceRevision) -> str:
    prefix = "capture://"
    encoded = (
        revision.source_uri[len(prefix) :]
        if revision.source_uri.startswith(prefix)
        else ""
    )
    decoded = unquote(encoded)
    path = PurePosixPath(decoded)
    if (
        not encoded
        or not decoded
        or "\\" in decoded
        or path.is_absolute()
        or len(path.parts) != 1
        or any(part in {"", ".", ".."} for part in path.parts)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", decoded)
        or quote(decoded, safe="._-") != encoded
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_ARTIFACT_SET_MISMATCH",
            "the additive Blueprint source URI is not canonical",
        )
    return f"{path.as_posix()}/evidence"


def _validate_staged_quarantine_binding(
    baseline: UpdateBaseline,
    staged: StagedBaselineSnapshot,
) -> tuple[str, str]:
    if (
        type(staged) is not StagedBaselineSnapshot
        or staged.base_build_id != baseline.base_build_id
        or not re.fullmatch(r"[0-9a-f]{32}", staged.staging_id)
        or staged.temporary_root
        != (
            baseline.snapshot_root
            / ".incremental-staging"
            / staged.staging_id
        )
        or staged.snapshot_dir != staged.temporary_root / "snapshot"
        or not isinstance(staged.receipt, Mapping)
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_BASELINE_CHANGED",
            "the staging workspace is not bound to the UpdateBaseline",
        )
    receipt_body = dict(staged.receipt)
    proof = str(receipt_body.pop("proof", ""))
    expected_proof = "staging-proof://" + _canonical_sha256(
        receipt_body
    )
    staging_tree_digest = str(
        staged.receipt.get("stagedTreeDigest") or ""
    )
    expected_baseline_identity = _staging_baseline_identity_sha256(
        baseline
    )
    if (
        proof != expected_proof
        or staged.receipt.get("baseBuildId") != baseline.base_build_id
        or staged.receipt.get("pointerSha256")
        != baseline.base_pointer_sha256
        or staged.receipt.get("manifestSha256")
        != baseline.base_manifest_sha256
        or staged.receipt.get("sourceDiffSha256")
        != baseline.source_diff_sha256
        or staged.receipt.get("sourceManifestFingerprint")
        != baseline.candidate_source_manifest_fingerprint
        or staged.receipt.get("updateBaselineIdentitySha256")
        != expected_baseline_identity
        or not _SHA256.fullmatch(staging_tree_digest)
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_BASELINE_CHANGED",
            "the safe staging receipt does not match the UpdateBaseline",
        )
    return proof, staging_tree_digest


def _inspection_matches_revision(
    inspection: BlueprintEvidenceBundleInspection,
    revision: SourceRevision,
) -> None:
    if inspection.evidence_bytes != revision.size_bytes:
        raise _gap(
            "ADDITIVE_QUARANTINE_AGGREGATE_MISMATCH",
            "Evidence SQLite bytes differ from SourceRevision.sizeBytes",
        )
    if (
        inspection.source_revision_label != revision.revision_label
        or inspection.entity_uri != revision.entity_uri
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
            "Evidence entity or revision differs from SourceRevision",
        )
    if inspection.aggregate_sha256 != revision.fingerprint:
        raise _gap(
            "ADDITIVE_QUARANTINE_AGGREGATE_MISMATCH",
            "Evidence aggregate differs from SourceRevision fingerprint",
        )


def freeze_additive_blueprint_input(
    baseline: UpdateBaseline,
    *,
    capture_root: Path,
    staged_snapshot: StagedBaselineSnapshot,
    fault_injector: Callable[[str, str], None] | None = None,
) -> FrozenAdditiveBlueprintInput:
    """Freeze the exact candidate-bound Evidence bundle beside staging."""

    if type(baseline) is not UpdateBaseline:
        raise TypeError("update baseline is required")
    if not isinstance(capture_root, Path):
        raise TypeError("capture root must be a Path")
    if fault_injector is not None and not callable(fault_injector):
        raise TypeError("quarantine fault injector must be callable")
    revision = _single_additive_blueprint_revision(baseline)
    staging_proof, staging_tree_digest = (
        _validate_staged_quarantine_binding(
            baseline,
            staged_snapshot,
        )
    )
    validate_update_baseline_identity(
        baseline,
        expected_current_snapshot=baseline.current_snapshot,
        expected_candidate_source_manifest=(
            baseline.candidate_source_manifest
        ),
    )
    from .safe_staging import (
        SafeStagingError,
        freeze_blueprint_evidence_bundle,
        validate_frozen_blueprint_bundle,
    )

    try:
        safe_bundle = freeze_blueprint_evidence_bundle(
            source_root=capture_root,
            source_relative_directory=_capture_bundle_directory(
                revision
            ),
            temporary_root=staged_snapshot.temporary_root,
            staging_id=staged_snapshot.staging_id,
            staging_identity=staged_snapshot.cleanup_identity,
            source_id=revision.source_id,
            fault_injector=fault_injector,
        )
        validated = validate_frozen_blueprint_bundle(
            safe_bundle,
            temporary_root=staged_snapshot.temporary_root,
            staging_identity=staged_snapshot.cleanup_identity,
        )
    except SafeStagingError as exc:
        raise UpdateBaselineBlockedGap(
            exc.gap_code,
            str(exc),
            status=exc.status,
            residual_identifier=exc.residual_identifier,
        ) from exc
    artifacts = dict(validated.artifacts)
    if set(artifacts) not in (
        {"evidence.sqlite"},
        {"evidence.sqlite", "manifest.json"},
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_ARTIFACT_SET_MISMATCH",
            "quarantine does not contain the exact Evidence bundle",
        )
    try:
        inspection = inspect_blueprint_evidence_bundle(
            artifacts["evidence.sqlite"],
            artifacts.get("manifest.json"),
        )
    except AddOnlyDeltaBlockedGap as exc:
        raise _gap(
            "ADDITIVE_QUARANTINE_SOURCE_IDENTITY_CHANGED",
            "quarantined Evidence SQLite identity is invalid",
        ) from exc
    _inspection_matches_revision(inspection, revision)
    by_name = {
        artifact.relative: artifact
        for artifact in safe_bundle.artifacts
    }
    evidence_artifact = FrozenQuarantineArtifact(
        artifact_uri=(
            f"artifact://{revision.source_id}/evidence.sqlite"
        ),
        artifact_sha256=inspection.evidence_sha256,
        artifact_bytes=inspection.evidence_bytes,
    )
    if (
        by_name["evidence.sqlite"].sha256
        != evidence_artifact.artifact_sha256
        or by_name["evidence.sqlite"].size_bytes
        != evidence_artifact.artifact_bytes
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
            "quarantine Evidence identity changed after copying",
        )
    manifest_artifact = (
        FrozenQuarantineArtifact(
            artifact_uri=(
                f"artifact://{revision.source_id}/manifest.json"
            ),
            artifact_sha256=str(inspection.manifest_sha256),
            artifact_bytes=int(inspection.manifest_bytes),
        )
        if inspection.manifest_sha256 is not None
        and inspection.manifest_bytes is not None
        else None
    )
    if manifest_artifact is not None and (
        by_name["manifest.json"].sha256
        != manifest_artifact.artifact_sha256
        or by_name["manifest.json"].size_bytes
        != manifest_artifact.artifact_bytes
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_DESTINATION_IDENTITY_CHANGED",
            "quarantine manifest identity changed after copying",
        )
    binding = FrozenArtifactBinding(
        source_id=revision.source_id,
        source_fingerprint=revision.fingerprint,
        evidence=evidence_artifact,
        manifest=manifest_artifact,
    )
    update_baseline_identity_sha256 = (
        _staging_baseline_identity_sha256(baseline)
    )
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    body: dict[str, object] = {
        "schema": ADDITIVE_QUARANTINE_RECEIPT_SCHEMA,
        "evidenceClass": ADDITIVE_QUARANTINE_EVIDENCE_CLASS,
        "baseBuildId": baseline.base_build_id,
        "pointerSha256": baseline.base_pointer_sha256,
        "manifestSha256": baseline.base_manifest_sha256,
        "baseSourceManifestFingerprint": (
            baseline.base_source_manifest_fingerprint
        ),
        "candidateSourceManifestFingerprint": (
            baseline.candidate_source_manifest_fingerprint
        ),
        "sourceDiffSha256": baseline.source_diff_sha256,
        "updateBaselineIdentitySha256": (
            update_baseline_identity_sha256
        ),
        "stagingReceiptProof": staging_proof,
        "stagingTreeDigest": staging_tree_digest,
        "sourceId": revision.source_id,
        "entityUri": revision.entity_uri,
        "revisionLabel": revision.revision_label,
        "sourceFingerprint": revision.fingerprint,
        **evidence_artifact.payload(),
        "manifestArtifact": (
            manifest_artifact.payload()
            if manifest_artifact is not None
            else None
        ),
        "sourceAggregateSha256": inspection.aggregate_sha256,
        "quarantineTreeDigest": (
            safe_bundle.quarantine_tree_digest
        ),
        "quarantineRelativePath": (
            f".incremental-staging/{staged_snapshot.staging_id}/"
            f"quarantine/{revision.source_id}"
        ),
        "sameVolume": True,
        "sourceVerifiedUnchanged": True,
        "exactArtifactSet": True,
        "reparsePointCount": 0,
        "hardlinkAliasCount": 0,
        "createdAt": created_at,
        "published": False,
        "productionAuthority": False,
        "e4Scenario2Complete": False,
        "cutoverEligible": False,
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    receipt = {
        **body,
        "proof": "quarantine-proof://" + _canonical_sha256(body),
    }
    frozen_receipt = _freeze_json(receipt)
    if not isinstance(frozen_receipt, Mapping):
        raise AssertionError("quarantine receipt must remain an object")
    return FrozenAdditiveBlueprintInput(
        base_build_id=baseline.base_build_id,
        staging_id=staged_snapshot.staging_id,
        source_id=revision.source_id,
        entity_uri=revision.entity_uri,
        revision_label=revision.revision_label,
        source_fingerprint=revision.fingerprint,
        pointer_sha256=baseline.base_pointer_sha256,
        manifest_sha256=baseline.base_manifest_sha256,
        base_source_manifest_fingerprint=(
            baseline.base_source_manifest_fingerprint
        ),
        candidate_source_manifest_fingerprint=(
            baseline.candidate_source_manifest_fingerprint
        ),
        source_diff_sha256=baseline.source_diff_sha256,
        update_baseline_identity_sha256=(
            update_baseline_identity_sha256
        ),
        staging_receipt_proof=staging_proof,
        staging_tree_digest=staging_tree_digest,
        quarantine_tree_digest=(
            safe_bundle.quarantine_tree_digest
        ),
        created_at=created_at,
        quarantine_root=safe_bundle.quarantine_root,
        ingest_root=safe_bundle.bundle_root,
        artifact_bindings=(binding,),
        receipt=frozen_receipt,
        cleanup_identity=staged_snapshot.cleanup_identity,
        safe_bundle=safe_bundle,
    )


def validate_frozen_additive_blueprint_input(
    frozen: FrozenAdditiveBlueprintInput,
) -> FrozenAdditiveBlueprintInput:
    """Revalidate receipt, paths, bytes, and SQLite identity before ingest."""

    if type(frozen) is not FrozenAdditiveBlueprintInput:
        raise TypeError("frozen additive Blueprint input is required")
    frozen.__post_init__()
    from .safe_staging import (
        SafeStagingError,
        SafeFrozenBlueprintBundle,
        validate_frozen_blueprint_bundle,
    )

    if type(frozen.safe_bundle) is not SafeFrozenBlueprintBundle:
        raise _gap(
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            "the safe quarantine identity is missing",
        )
    try:
        validated = validate_frozen_blueprint_bundle(
            frozen.safe_bundle,
            temporary_root=frozen.quarantine_root.parent,
            staging_identity=frozen.cleanup_identity,
        )
    except SafeStagingError as exc:
        code = (
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID"
            if exc.gap_code
            == "REPARSE_SAFE_ADDITIVE_QUARANTINE_UNAVAILABLE"
            else exc.gap_code
        )
        raise UpdateBaselineBlockedGap(
            code,
            str(exc),
            status=exc.status,
            residual_identifier=exc.residual_identifier,
        ) from exc
    if validated.quarantine_tree_digest != frozen.receipt.get(
        "quarantineTreeDigest"
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            "quarantine tree digest differs from its receipt",
        )
    artifacts = dict(validated.artifacts)
    try:
        inspection = inspect_blueprint_evidence_bundle(
            artifacts["evidence.sqlite"],
            artifacts.get("manifest.json"),
        )
    except (AddOnlyDeltaBlockedGap, KeyError) as exc:
        raise _gap(
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            "quarantine Evidence identity is invalid",
        ) from exc
    binding = frozen.artifact_bindings[0]
    if (
        inspection.evidence_sha256
        != binding.evidence.artifact_sha256
        or inspection.evidence_bytes
        != binding.evidence.artifact_bytes
        or inspection.manifest_sha256
        != (
            binding.manifest.artifact_sha256
            if binding.manifest is not None
            else None
        )
        or inspection.manifest_bytes
        != (
            binding.manifest.artifact_bytes
            if binding.manifest is not None
            else None
        )
        or inspection.aggregate_sha256 != frozen.source_fingerprint
        or inspection.entity_uri != frozen.entity_uri
        or inspection.source_revision_label != frozen.revision_label
    ):
        raise _gap(
            "ADDITIVE_QUARANTINE_RECEIPT_INVALID",
            "quarantine bytes differ from the frozen receipt",
        )
    return frozen


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
