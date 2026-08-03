from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..evidence_publication import (
    _atomic_write,
    _lexical_absolute,
    _path_present,
    _require_plain_directory,
    _require_plain_path_chain,
    evidence_publication_lock,
)
from ..evidence_repository import (
    ResolvedEvidenceState,
    _read_bound_file_bytes,
    evidence_manifest_payload,
    resolve_asset_evidence_state,
)
from .contracts import (
    CURRENT_SCHEMA,
    INTERPRETATION_SCHEMA,
    INTERPRETER_VERSION,
    MANIFEST_SCHEMA,
    FaultInjector,
    InterpretationBuild,
    InterpretationPublicationError,
    PublishedInterpretation,
    artifact_descriptor,
    call_fault,
    canonical_json_bytes,
    sha256_bytes,
)
from .engine import build_interpretation
from .revision import (
    _evidence_refs,
    _validate_contract,
    load_current_interpretation,
    load_interpretation_revision,
)


def _evidence_identity(state: ResolvedEvidenceState) -> tuple[object, ...]:
    return (
        state.source_kind,
        state.release_authority,
        state.freshness_status,
        state.migration_required,
        state.pointer_sha256,
        state.manifest_sha256,
        state.database_sha256,
        state.database_bytes,
    )


def _require_publishable_evidence(state: ResolvedEvidenceState) -> None:
    if (
        state.source_kind != "INDEXED_V3_CURRENT"
        or not state.release_authority
        or state.migration_required
        or not state.manifest_sha256
        or not state.pointer_sha256
    ):
        raise InterpretationPublicationError(
            "EVIDENCE_NOT_AUTHORITATIVE",
            "Interpretation publication requires authoritative current v3 Evidence.",
        )
    if state.freshness_status == "STALE":
        raise InterpretationPublicationError(
            "EVIDENCE_STALE",
            "Stale Evidence can never advance Interpretation current.",
        )


def _artifact_bytes(build: InterpretationBuild) -> dict[str, bytes]:
    markdown = build.markdown if build.markdown.endswith("\n") else build.markdown + "\n"
    pseudocode = (
        build.pseudocode if build.pseudocode.endswith("\n") else build.pseudocode + "\n"
    )
    return {
        "interpretationJson": canonical_json_bytes(build.interpretation),
        "interpretationMarkdown": markdown.encode("utf-8"),
        "trace": canonical_json_bytes(build.trace),
        "gaps": canonical_json_bytes(build.gaps),
        "pseudocode": pseudocode.encode("utf-8"),
    }


def _artifact_manifest(raws: dict[str, bytes]) -> dict[str, dict[str, object]]:
    names = {
        "interpretationJson": "interpretation.json",
        "interpretationMarkdown": "interpretation.md",
        "trace": "trace.json",
        "gaps": "gaps.json",
        "pseudocode": "pseudocode.txt",
    }
    return {key: artifact_descriptor(names[key], raws[key]) for key in names}


def _revision_id(
    build: InterpretationBuild,
    artifacts: dict[str, dict[str, object]],
) -> str:
    projection = {
        "semanticDigest": build.semantic_digest,
        "interpreterVersion": INTERPRETER_VERSION,
        "schemaVersion": INTERPRETATION_SCHEMA,
        "artifacts": artifacts,
    }
    return sha256_bytes(canonical_json_bytes(projection, newline=False))[:24]


def _manifest_payload(
    build: InterpretationBuild,
    *,
    revision_id: str,
    artifacts: dict[str, dict[str, object]],
) -> dict[str, object]:
    interpretation = build.interpretation
    return {
        "schema": MANIFEST_SCHEMA,
        "revisionId": revision_id,
        "assetId": interpretation["assetId"],
        "objectPath": interpretation["objectPath"],
        "evidenceRevisionId": interpretation["evidenceRevisionId"],
        "evidenceManifestSha256": interpretation["evidenceManifestSha256"],
        "interpreterVersion": interpretation["interpreterVersion"],
        "schemaVersion": interpretation["schemaVersion"],
        "semanticDigest": interpretation["semanticDigest"],
        "generatedAt": interpretation["generatedAt"],
        "artifacts": artifacts,
    }


def _pointer_payload(
    *,
    revision_id: str,
    manifest_sha256: str,
    evidence_revision_id: str,
    evidence_manifest_sha256: str,
) -> dict[str, str]:
    return {
        "schema": CURRENT_SCHEMA,
        "revisionId": revision_id,
        "manifest": f"revisions/{revision_id}/manifest.json",
        "manifestSha256": manifest_sha256,
        "evidenceRevisionId": evidence_revision_id,
        "evidenceManifestSha256": evidence_manifest_sha256,
    }


def _read_optional_pointer(path: Path) -> bytes | None:
    try:
        return _read_bound_file_bytes(
            path,
            label="interpretation current pointer",
            maximum_size=64 * 1024,
        )
    except FileNotFoundError:
        return None


def _stable_directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    # Link count and timestamps change when children are created or renamed.
    # Windows also allows unrelated directory attributes such as ARCHIVE and
    # NOT_CONTENT_INDEXED to change asynchronously.  Only the reparse bit has
    # path-binding meaning; the plain-path checks reject it at every sample.
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400))
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(getattr(metadata, "st_file_attributes", 0)) & reparse_flag,
    )


def _directory_identity(path: Path, *, label: str) -> tuple[int, ...]:
    _require_plain_path_chain(path, label=label)
    _require_plain_directory(path, label=label)
    return _stable_directory_identity(path.lstat())


def _require_directory_binding(
    path: Path,
    expected: tuple[int, ...],
    *,
    label: str,
) -> None:
    try:
        observed = _directory_identity(path, label=label)
    except (OSError, ValueError) as exc:
        raise InterpretationPublicationError(
            "INTERPRETATION_DIRECTORY_CHANGED",
            f"{label} is no longer a plain bound directory.",
        ) from exc
    if observed != expected:
        raise InterpretationPublicationError(
            "INTERPRETATION_DIRECTORY_CHANGED",
            f"{label} changed during publication.",
        )


def _write_staged_revision(
    staging_dir: Path,
    *,
    raws: dict[str, bytes],
    manifest_raw: bytes,
) -> None:
    filenames = {
        "interpretationJson": "interpretation.json",
        "interpretationMarkdown": "interpretation.md",
        "trace": "trace.json",
        "gaps": "gaps.json",
        "pseudocode": "pseudocode.txt",
    }
    for key, filename in filenames.items():
        path = staging_dir / filename
        path.write_bytes(raws[key])
        if path.read_bytes() != raws[key]:
            raise InterpretationPublicationError(
                "INTERPRETATION_STAGE_VERIFY_FAILED",
                f"Staged artifact {filename} did not round-trip exactly.",
            )
    (staging_dir / "manifest.json").write_bytes(manifest_raw)
    if (staging_dir / "manifest.json").read_bytes() != manifest_raw:
        raise InterpretationPublicationError(
            "INTERPRETATION_STAGE_VERIFY_FAILED",
            "Staged Interpretation manifest did not round-trip exactly.",
        )


def _restore_pointer(
    pointer_path: Path,
    *,
    expected_current: bytes,
    previous: bytes | None,
    parent_identity: tuple[int, ...],
) -> None:
    _require_directory_binding(
        pointer_path.parent,
        parent_identity,
        label="interpretation root",
    )
    if _read_optional_pointer(pointer_path) != expected_current:
        raise InterpretationPublicationError(
            "INTERPRETATION_PUBLICATION_UNCERTAIN",
            "Interpretation current changed before rollback could be verified.",
        )
    if previous is None:
        pointer_path.unlink(missing_ok=True)
    else:
        _atomic_write(pointer_path, previous)
    _require_directory_binding(
        pointer_path.parent,
        parent_identity,
        label="interpretation root",
    )
    if _read_optional_pointer(pointer_path) != previous:
        raise InterpretationPublicationError(
            "INTERPRETATION_PUBLICATION_UNCERTAIN",
            "Interpretation current rollback could not be verified.",
        )


def publish_interpretation(
    asset_dir: str | Path,
    *,
    budget: int = 20_000,
    fail_on_gap: bool = False,
    allow_stale: bool = False,
    allow_legacy_fallback: bool = False,
    generated_at: str | None = None,
    expected_semantic_digest: str | None = None,
    fault_injector: FaultInjector | None = None,
) -> PublishedInterpretation:
    root = _lexical_absolute(asset_dir)
    _require_plain_path_chain(root, label="asset directory")
    _require_plain_directory(root, label="asset directory")
    build = build_interpretation(
        root,
        budget=budget,
        allow_stale=allow_stale,
        allow_legacy_fallback=allow_legacy_fallback,
    )
    _require_publishable_evidence(build.evidence_state)
    if (
        expected_semantic_digest is not None
        and build.semantic_digest != str(expected_semantic_digest)
    ):
        raise InterpretationPublicationError(
            "INTERPRETATION_PREVIEW_CHANGED",
            "Interpretation changed after CLI preflight and before publication.",
        )
    if allow_stale or allow_legacy_fallback:
        raise InterpretationPublicationError(
            "INTERPRETATION_PREVIEW_ONLY",
            "Relaxed Evidence flags are preview-only and cannot publish current.",
        )
    if generated_at is not None:
        value = str(generated_at).strip()
        if not value or len(value) > 128:
            raise InterpretationPublicationError(
                "INTERPRETATION_GENERATED_AT_INVALID",
                "generated_at must be bounded non-empty text.",
            )
        build = replace(
            build,
            interpretation={**build.interpretation, "generatedAt": value},
        )
    if fail_on_gap and build.gaps.get("items"):
        raise InterpretationPublicationError(
            "INTERPRETATION_GAPS_PRESENT",
            "--fail-on-gap rejected an Interpretation containing explicit gaps.",
        )

    raws = _artifact_bytes(build)
    artifact_manifest = _artifact_manifest(raws)
    revision_id = _revision_id(build, artifact_manifest)
    manifest = _manifest_payload(
        build,
        revision_id=revision_id,
        artifacts=artifact_manifest,
    )
    manifest_raw = canonical_json_bytes(manifest)
    manifest_sha = sha256_bytes(manifest_raw)
    pointer = _pointer_payload(
        revision_id=revision_id,
        manifest_sha256=manifest_sha,
        evidence_revision_id=str(build.interpretation["evidenceRevisionId"]),
        evidence_manifest_sha256=str(
            build.interpretation["evidenceManifestSha256"]
        ),
    )
    pointer_raw = canonical_json_bytes(pointer)

    interpretation_root = root / "interpretation"
    revisions_root = interpretation_root / "revisions"
    pointer_path = interpretation_root / "current.json"
    created = False
    reused = False
    staging_dir: Path | None = None
    with evidence_publication_lock(root):
        root_identity = _directory_identity(root, label="asset directory")
        interpretation_root.mkdir(exist_ok=True)
        revisions_root.mkdir(exist_ok=True)
        _require_plain_path_chain(interpretation_root, label="interpretation root")
        _require_plain_path_chain(
            revisions_root, label="interpretation revisions root"
        )
        _require_plain_directory(interpretation_root, label="interpretation root")
        _require_plain_directory(
            revisions_root, label="interpretation revisions root"
        )
        interpretation_identity = _directory_identity(
            interpretation_root, label="interpretation root"
        )
        revisions_identity = _directory_identity(
            revisions_root, label="interpretation revisions root"
        )
        current_evidence = resolve_asset_evidence_state(root)
        if _evidence_identity(current_evidence) != _evidence_identity(build.evidence_state):
            raise InterpretationPublicationError(
                "EVIDENCE_REVISION_CHANGED",
                "Evidence current changed while Interpretation was generated.",
            )
        _require_publishable_evidence(current_evidence)
        _validate_contract(
            manifest=manifest,
            interpretation=build.interpretation,
            trace=build.trace,
            gaps=build.gaps,
            pseudocode=build.pseudocode,
            markdown=build.markdown,
            evidence_refs=_evidence_refs(current_evidence),
        )
        _require_directory_binding(
            interpretation_root,
            interpretation_identity,
            label="interpretation root",
        )
        _require_directory_binding(
            revisions_root,
            revisions_identity,
            label="interpretation revisions root",
        )
        pointer_before = _read_optional_pointer(pointer_path)
        _require_directory_binding(
            interpretation_root,
            interpretation_identity,
            label="interpretation root",
        )
        _require_directory_binding(
            revisions_root,
            revisions_identity,
            label="interpretation revisions root",
        )
        revision_dir = revisions_root / revision_id
        if _path_present(revision_dir):
            loaded = load_interpretation_revision(
                root,
                revision_id,
                expected_manifest_sha256=manifest_sha,
                evidence_state=current_evidence,
            )
            if loaded.manifest.get("semanticDigest") != build.semantic_digest:
                raise InterpretationPublicationError(
                    "INTERPRETATION_REVISION_COLLISION",
                    "An existing revision id has different semantic content.",
                )
            reused = True
        else:
            _require_directory_binding(root, root_identity, label="asset directory")
            staging_dir = Path(
                tempfile.mkdtemp(prefix=".interpretation-staging-", dir=root)
            )
            try:
                staging_identity = _directory_identity(
                    staging_dir, label="interpretation staging directory"
                )
                _write_staged_revision(
                    staging_dir,
                    raws=raws,
                    manifest_raw=manifest_raw,
                )
                _require_directory_binding(
                    staging_dir,
                    staging_identity,
                    label="interpretation staging directory",
                )
                call_fault(fault_injector, "after_stage_validate")
                _require_directory_binding(
                    revisions_root,
                    revisions_identity,
                    label="interpretation revisions root",
                )
                os.replace(staging_dir, revision_dir)
                staging_dir = None
                _require_directory_binding(
                    revisions_root,
                    revisions_identity,
                    label="interpretation revisions root",
                )
                _require_directory_binding(
                    revision_dir,
                    staging_identity,
                    label="interpretation revision",
                )
                created = True
                call_fault(fault_injector, "after_revision_rename")
            finally:
                if staging_dir is not None and staging_dir.exists():
                    shutil.rmtree(staging_dir)

        loaded_revision = load_interpretation_revision(
            root,
            revision_id,
            expected_manifest_sha256=manifest_sha,
            evidence_state=current_evidence,
        )
        if loaded_revision.manifest.get("semanticDigest") != build.semantic_digest:
            raise InterpretationPublicationError(
                "INTERPRETATION_REVISION_COLLISION",
                "The immutable revision differs from the generated semantic content.",
            )

        call_fault(fault_injector, "before_pointer_cas")
        _require_directory_binding(
            interpretation_root,
            interpretation_identity,
            label="interpretation root",
        )
        if _read_optional_pointer(pointer_path) != pointer_before:
            raise InterpretationPublicationError(
                "INTERPRETATION_POINTER_CONFLICT",
                "Interpretation current changed before pointer CAS.",
            )
        _require_directory_binding(
            interpretation_root,
            interpretation_identity,
            label="interpretation root",
        )
        _atomic_write(pointer_path, pointer_raw)
        _require_directory_binding(
            interpretation_root,
            interpretation_identity,
            label="interpretation root",
        )
        try:
            call_fault(fault_injector, "after_pointer_cas")
            final_evidence = resolve_asset_evidence_state(root)
            if _evidence_identity(final_evidence) != _evidence_identity(current_evidence):
                raise InterpretationPublicationError(
                    "EVIDENCE_REVISION_CHANGED",
                    "Evidence current changed during Interpretation publication.",
                )
            loaded = load_current_interpretation(root)
            if loaded.revision_id != revision_id or loaded.manifest_sha256 != manifest_sha:
                raise InterpretationPublicationError(
                    "INTERPRETATION_PUBLICATION_VERIFY_FAILED",
                    "Published Interpretation current did not validate to the staged revision.",
                )
        except Exception as exc:
            try:
                _restore_pointer(
                    pointer_path,
                    expected_current=pointer_raw,
                    previous=pointer_before,
                    parent_identity=interpretation_identity,
                )
            except Exception as rollback_exc:
                if isinstance(rollback_exc, InterpretationPublicationError):
                    raise rollback_exc from exc
                raise InterpretationPublicationError(
                    "INTERPRETATION_PUBLICATION_UNCERTAIN",
                    "Interpretation current could not be rolled back safely.",
                ) from rollback_exc
            if isinstance(exc, InterpretationPublicationError):
                raise
            raise InterpretationPublicationError(
                "INTERPRETATION_PUBLICATION_ROLLED_BACK",
                "Interpretation pointer validation failed and the prior pointer was restored.",
            ) from exc
    return PublishedInterpretation(
        asset_dir=root,
        revision_dir=revisions_root / revision_id,
        pointer_path=pointer_path,
        revision_id=revision_id,
        manifest_sha256=manifest_sha,
        pointer_sha256=sha256_bytes(pointer_raw),
        semantic_digest=build.semantic_digest,
        evidence_revision_id=str(build.interpretation["evidenceRevisionId"]),
        evidence_manifest_sha256=str(
            build.interpretation["evidenceManifestSha256"]
        ),
        created=created,
        reused=reused,
    )


def inspect_interpretation_health(asset_dir: str | Path) -> dict[str, Any]:
    root = _lexical_absolute(asset_dir)
    result: dict[str, Any] = {
        "status": "MISSING",
        "asset": {"name": root.name},
        "evidence": {},
        "interpretation": {},
    }
    try:
        state = resolve_asset_evidence_state(root, allow_stale=True)
        manifest = evidence_manifest_payload(state)
        evidence_revision = str(manifest.get("revisionId") or "")
        result["asset"] = {
            "name": root.name,
            "assetId": str(manifest.get("assetId") or ""),
            "objectPath": str(manifest.get("objectPath") or ""),
        }
        result["evidence"] = {
            "revisionId": evidence_revision,
            "manifestSha256": state.manifest_sha256,
            "pointerSha256": state.pointer_sha256,
            "freshnessStatus": state.freshness_status,
            "releaseAuthority": state.release_authority,
            "migrationRequired": state.migration_required,
        }
        if state.freshness_status == "STALE":
            result["status"] = "STALE"
            return result
        if state.migration_required or not state.release_authority:
            result["status"] = "MIGRATION_REQUIRED"
            return result
        loaded = load_current_interpretation(root)
        result["status"] = "READY"
        result["interpretation"] = {
            "status": "CURRENT",
            "revisionId": loaded.revision_id,
            "manifestSha256": loaded.manifest_sha256,
            "pointerSha256": loaded.pointer_sha256,
            "semanticDigest": loaded.manifest["semanticDigest"],
            "interpreterVersion": loaded.manifest["interpreterVersion"],
            "schemaVersion": loaded.manifest["schemaVersion"],
            "generatedAt": loaded.manifest["generatedAt"],
        }
        return result
    except FileNotFoundError as exc:
        code = str(exc)
        if "EVIDENCE" in code:
            result["status"] = "MISSING"
            result["reasonCode"] = "BLUEPRINT_EVIDENCE_NOT_FOUND"
        else:
            result["status"] = "MISSING"
            result["interpretation"] = {"status": "MISSING"}
            result["reasonCode"] = "BLUEPRINT_INTERPRETATION_NOT_FOUND"
        return result
    except InterpretationPublicationError as exc:
        result["status"] = (
            "STALE" if exc.code == "INTERPRETATION_STALE_EVIDENCE" else "INVALID"
        )
        result["reasonCode"] = exc.code
        return result
    except (OSError, ValueError):
        result["status"] = "INVALID"
        result["reasonCode"] = "BLUEPRINT_EVIDENCE_INVALID"
        return result


__all__ = [
    "inspect_interpretation_health",
    "load_current_interpretation",
    "publish_interpretation",
]
