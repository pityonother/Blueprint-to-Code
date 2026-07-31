"""Reseal and publish one verified additive shadow snapshot.

This module never grants production authority or cutover eligibility.  It
reuses the full-snapshot validators and keeps the only reader-visible write in
the existing current-pointer compare-and-swap implementation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import re
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .projections import (
    DOMAIN_PROJECTIONS,
    PROJECTION_SCHEMA_VERSION,
    compute_projection_artifact_content_digest,
)
from .schema_capabilities import CORE_SCHEMA_VERSION
from .snapshot import (
    CACHE_SCHEMA_VERSION,
    CATALOG_SCHEMA_VERSION,
    DATABASE_NAMES,
    SEARCH_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA,
    SNAPSHOT_SOURCE_KIND,
    SNAPSHOT_SOURCE_URI,
    _evaluate_staged_quality_gates,
    _finalize_staged_database_journals,
    _seal_runtime_health_summary,
    _seal_staged_quality_report,
    _promote_snapshot,
    _validate_staged_snapshot_for_promotion,
    _write_bytes,
    _write_json,
    database_metrics,
    normalize_snapshot_generated_at,
    resolve_current_snapshot,
    semantic_inputs_sha256,
    snapshot_build_id,
)
from .pointer_cas import (
    CurrentPointerBaseline,
    read_current_pointer_baseline,
)
from .narrow_gates import (
    UpdateBaseline as NarrowGateUpdateBaseline,
    parse_and_validate_narrow_gate_diagnostic_report_bytes,
)
from .source_manifest import (
    SNAPSHOT_SEMANTIC_INPUT_KEYS,
    SourceManifest,
    source_manifest_binding,
    source_manifest_from_binding,
)
from .incremental_delta import logical_database_state


@dataclass(frozen=True, slots=True)
class ResealedIncrementalCandidate:
    """A complete staged candidate plus identity-only truth proofs."""

    manifest: dict[str, object]
    identity_only_databases: Mapping[str, str]


class IncrementalPublicationError(RuntimeError):
    """A pointer outcome that callers must not reinterpret."""

    status: str

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(detail)


class IncrementalPublicationNotReplaced(IncrementalPublicationError):
    def __init__(self, detail: str) -> None:
        super().__init__("NOT_REPLACED", detail)


class IncrementalPublicationUncertain(IncrementalPublicationError):
    def __init__(self, detail: str) -> None:
        super().__init__("UNCERTAIN", detail)


def _publication_proof(body: Mapping[str, object]) -> str:
    return "publication-proof://" + hashlib.sha256(
        json.dumps(
            body,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _validate_incremental_binding(
    *,
    snapshot_dir: Path,
    manifest: Mapping[str, object],
    expected_update_baseline: NarrowGateUpdateBaseline,
    expected_candidate_source_manifest: SourceManifest,
) -> None:
    binding = manifest.get("incrementalPublication")
    previous = manifest.get("previousSnapshot")
    cutover = manifest.get("cutover")
    quality = manifest.get("qualityGates")
    if not isinstance(binding, Mapping):
        raise ValueError("incremental publication binding is missing")
    expected_fields = {
        "schema",
        "candidateSourceManifestFingerprint",
        "deltaReceiptSha256",
        "narrowGateReportUri",
        "narrowGateReportSha256",
        "narrowGateProof",
        "published",
        "productionAuthority",
        "cutoverEligible",
        "mode",
        "defaultQuerySource",
    }
    report_sha256 = str(binding.get("narrowGateReportSha256") or "")
    report_path = snapshot_dir / "reports" / "incremental_narrow_gates.json"
    if (
        set(binding) != expected_fields
        or binding.get("schema")
        != "ark-kb-incremental-publication-binding/v1"
        or binding.get("candidateSourceManifestFingerprint")
        != expected_update_baseline.candidate_source_manifest_fingerprint
        or binding.get("deltaReceiptSha256")
        != expected_update_baseline.delta_receipt_sha256
        or binding.get("narrowGateReportUri")
        != "reports/incremental_narrow_gates.json"
        or not re.fullmatch(r"[0-9a-f]{64}", report_sha256)
        or any(
            binding.get(field) is not False
            for field in ("published", "productionAuthority", "cutoverEligible")
        )
        or binding.get("mode") != "shadow"
        or binding.get("defaultQuerySource") != "legacy"
        or not isinstance(previous, Mapping)
        or previous.get("buildId") != expected_update_baseline.base_build_id
        or previous.get("manifestSha256")
        != expected_update_baseline.base_manifest_sha256
        or not isinstance(cutover, Mapping)
        or cutover.get("mode") != "shadow"
        or cutover.get("defaultQuerySource") != "legacy"
        or not isinstance(quality, Mapping)
        or quality.get("cutoverEligible") is not False
        or report_path.is_symlink()
        or not report_path.is_file()
    ):
        raise ValueError("incremental shadow publication binding is invalid")
    report_bytes = report_path.read_bytes()
    if hashlib.sha256(report_bytes).hexdigest() != report_sha256:
        raise ValueError("incremental narrow-gate artifact hash is invalid")
    validated = parse_and_validate_narrow_gate_diagnostic_report_bytes(
        report_bytes,
        expected_report_sha256=report_sha256,
        expected_update_baseline=expected_update_baseline,
    )
    if binding.get("narrowGateProof") != validated.get("proof"):
        raise ValueError("incremental narrow-gate proof binding is invalid")
    bound_source = source_manifest_from_binding(manifest.get("incrementalUpdate"))
    if (
        bound_source != expected_candidate_source_manifest
        or bound_source.fingerprint
        != expected_update_baseline.candidate_source_manifest_fingerprint
    ):
        raise ValueError("candidate Source Manifest binding is invalid")


def verify_incremental_shadow_publication(
    *,
    output_dir: Path,
    build_id: str,
    expected_update_baseline: NarrowGateUpdateBaseline,
    expected_candidate_source_manifest: SourceManifest,
) -> bool:
    """Independently reopen current -> manifest -> databases and bindings."""

    try:
        current = resolve_current_snapshot(output_dir, allow_legacy=False)
        if current.build_id != build_id:
            return False
        _validate_staged_snapshot_for_promotion(
            staging=current.snapshot_dir,
            manifest=current.manifest,
        )
        _validate_incremental_binding(
            snapshot_dir=current.snapshot_dir,
            manifest=current.manifest,
            expected_update_baseline=expected_update_baseline,
            expected_candidate_source_manifest=(
                expected_candidate_source_manifest
            ),
        )
        return True
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError):
        return False


def publish_incremental_shadow_snapshot(
    *,
    staging: Path,
    output_dir: Path,
    manifest: dict[str, object],
    expected_current_pointer: CurrentPointerBaseline,
    expected_current_manifest_sha256: str,
    expected_update_baseline: NarrowGateUpdateBaseline,
    expected_candidate_source_manifest: SourceManifest,
    before_pointer_cas: Callable[[], None] | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Atomically install one local shadow snapshot and CAS its pointer."""

    if staging.is_symlink():
        raise IncrementalPublicationNotReplaced(
            "candidate staging identity is unsafe"
        )
    output_dir = output_dir.resolve()
    staging = staging.resolve(strict=True)
    try:
        relative = staging.relative_to(output_dir / ".incremental-staging")
    except ValueError as exc:
        raise IncrementalPublicationNotReplaced(
            "candidate is outside reserved incremental staging"
        ) from exc
    if (
        len(relative.parts) != 2
        or not re.fullmatch(r"[0-9a-f]{32}", relative.parts[0])
        or relative.parts[1] != "snapshot"
        or not staging.is_dir()
    ):
        raise IncrementalPublicationNotReplaced(
            "candidate staging identity is unsafe"
        )
    build_id = str(manifest.get("buildId") or "")
    if (
        expected_current_pointer.build_id
        != expected_update_baseline.base_build_id
        or expected_current_pointer.pointer_sha256
        != expected_update_baseline.base_pointer_sha256
        or expected_current_manifest_sha256
        != expected_update_baseline.base_manifest_sha256
    ):
        raise IncrementalPublicationNotReplaced(
            "publisher baseline does not match the narrow-gate baseline"
        )
    recovered_after_failure = False
    try:
        snapshots = output_dir / "snapshots"
        if snapshots.is_symlink():
            raise IncrementalPublicationNotReplaced(
                "immutable snapshot root is unsafe"
            )
        snapshots.mkdir(parents=True, exist_ok=True)
        if staging.stat().st_dev != snapshots.stat().st_dev:
            raise IncrementalPublicationNotReplaced(
                "candidate and immutable snapshots are on different volumes"
            )
        if read_current_pointer_baseline(output_dir) != expected_current_pointer:
            raise IncrementalPublicationNotReplaced(
                "current pointer changed before publication"
            )
        _validate_incremental_binding(
            snapshot_dir=staging,
            manifest=manifest,
            expected_update_baseline=expected_update_baseline,
            expected_candidate_source_manifest=(
                expected_candidate_source_manifest
            ),
        )
        _validate_staged_snapshot_for_promotion(
            staging=staging,
            manifest=manifest,
        )
        if fault_injector is not None:
            fault_injector("BEFORE_RENAME")
        pointer_receipt = _promote_snapshot(
            staging=staging,
            output_dir=output_dir,
            manifest=manifest,
            expected_current_pointer=expected_current_pointer,
            expected_current_manifest_sha256=(
                expected_current_manifest_sha256
            ),
            operation="INCREMENTAL_SHADOW_PUBLICATION",
            before_pointer_cas=before_pointer_cas,
        )
        if (
            pointer_receipt.get("status") != "VERIFIED"
            or pointer_receipt.get("operation")
            != "INCREMENTAL_SHADOW_PUBLICATION"
            or pointer_receipt.get("beforeBuildId")
            != expected_current_pointer.build_id
            or pointer_receipt.get("afterBuildId") != build_id
            or pointer_receipt.get("beforePointerSha256")
            != expected_current_pointer.pointer_sha256
            or pointer_receipt.get("pointerUpdated") is not True
            or pointer_receipt.get("verifiedAfterReplace") is not True
            or pointer_receipt.get("verifiedUnderLock") is not True
        ):
            raise ValueError("pointer CAS receipt is invalid")
        if fault_injector is not None:
            fault_injector("AFTER_POINTER_CAS")
    except IncrementalPublicationError:
        raise
    except Exception as exc:
        try:
            observed = read_current_pointer_baseline(output_dir)
        except Exception as read_error:
            raise IncrementalPublicationUncertain(
                "current pointer could not be read after publisher failure"
            ) from read_error
        if observed == expected_current_pointer:
            raise IncrementalPublicationNotReplaced(
                "publisher failed before replacing current"
            ) from exc
        if (
            observed.build_id == build_id
            and verify_incremental_shadow_publication(
                output_dir=output_dir,
                build_id=build_id,
                expected_update_baseline=expected_update_baseline,
                expected_candidate_source_manifest=(
                    expected_candidate_source_manifest
                ),
            )
        ):
            recovered_after_failure = True
        else:
            raise IncrementalPublicationUncertain(
                "pointer changed but target state is not independently verifiable"
            ) from exc
    try:
        observed = read_current_pointer_baseline(output_dir)
    except Exception as exc:
        raise IncrementalPublicationUncertain(
            "current pointer could not be re-read after publication"
        ) from exc
    if (
        observed.build_id != build_id
        or not verify_incremental_shadow_publication(
            output_dir=output_dir,
            build_id=build_id,
            expected_update_baseline=expected_update_baseline,
            expected_candidate_source_manifest=(
                expected_candidate_source_manifest
            ),
        )
    ):
        raise IncrementalPublicationUncertain(
            "published target failed independent verification"
        )
    pointer = {
        "operation": "INCREMENTAL_SHADOW_PUBLICATION",
        "beforeBuildId": expected_current_pointer.build_id,
        "afterBuildId": build_id,
        "beforePointerSha256": expected_current_pointer.pointer_sha256,
        "afterPointerSha256": observed.pointer_sha256,
        "pointerUpdated": True,
        "independentlyVerified": True,
        "recoveredAfterFailure": recovered_after_failure,
    }
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise IncrementalPublicationUncertain("published source identity is missing")
    body: dict[str, object] = {
        "schema": "ark-kb-incremental-shadow-publication-receipt/v1",
        "evidenceClass": "UNSIGNED_LOCAL_WRITE_FACT",
        "status": "REPLACED",
        "buildId": build_id,
        "sourceSha256": str(source.get("sha256") or ""),
        "sourceManifestFingerprint": (
            expected_candidate_source_manifest.fingerprint
        ),
        "previousBuildId": expected_update_baseline.base_build_id,
        "previousManifestSha256": (
            expected_update_baseline.base_manifest_sha256
        ),
        "narrowGateReportSha256": str(
            (manifest.get("incrementalPublication") or {}).get(
                "narrowGateReportSha256"
            )
        ),
        "pointerCAS": pointer,
        "atomicSourceManifestBound": True,
        "published": True,
        "productionAuthority": False,
        "cutoverEligible": False,
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    return {**body, "proof": _publication_proof(body)}


def seal_incremental_narrow_gate_report(
    *,
    staging: Path,
    manifest: Mapping[str, object],
    report_bytes: bytes,
    report_sha256: str,
    update_baseline: NarrowGateUpdateBaseline,
) -> dict[str, object]:
    """Seal one independently hashed diagnostic report into the candidate."""

    validated = parse_and_validate_narrow_gate_diagnostic_report_bytes(
        report_bytes,
        expected_report_sha256=report_sha256,
        expected_update_baseline=update_baseline,
    )
    previous = manifest.get("previousSnapshot")
    cutover = manifest.get("cutover")
    quality = manifest.get("qualityGates")
    if (
        not isinstance(previous, Mapping)
        or previous.get("buildId") != update_baseline.base_build_id
        or previous.get("manifestSha256")
        != update_baseline.base_manifest_sha256
        or not isinstance(cutover, Mapping)
        or cutover.get("mode") != "shadow"
        or cutover.get("defaultQuerySource") != "legacy"
        or not isinstance(quality, Mapping)
        or quality.get("cutoverEligible") is not False
        or validated.get("productionAuthority") is not False
    ):
        raise ValueError("narrow-gate report is not bound to a shadow candidate")
    reports = staging / "reports"
    if reports.is_symlink() or not reports.is_dir():
        raise ValueError("candidate reports directory is missing or unsafe")
    report_path = reports / "incremental_narrow_gates.json"
    _write_bytes(report_path, report_bytes)
    if hashlib.sha256(report_path.read_bytes()).hexdigest() != report_sha256:
        raise ValueError("sealed narrow-gate report hash is invalid")
    sealed = dict(manifest)
    sealed["incrementalPublication"] = {
        "schema": "ark-kb-incremental-publication-binding/v1",
        "candidateSourceManifestFingerprint": (
            update_baseline.candidate_source_manifest_fingerprint
        ),
        "deltaReceiptSha256": update_baseline.delta_receipt_sha256,
        "narrowGateReportUri": "reports/incremental_narrow_gates.json",
        "narrowGateReportSha256": report_sha256,
        "narrowGateProof": validated.get("proof"),
        "published": False,
        "productionAuthority": False,
        "cutoverEligible": False,
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    _write_json(staging / "manifest.json", sealed)
    _validate_staged_snapshot_for_promotion(
        staging=staging,
        manifest=sealed,
    )
    return sealed


def candidate_semantic_inputs(
    source_manifest: SourceManifest,
) -> dict[str, str]:
    if type(source_manifest) is not SourceManifest:
        raise TypeError("candidate Source Manifest is required")
    values: dict[str, str] = {}
    for revision in source_manifest.entries:
        prefix = "semantic-input://"
        if (
            revision.source_kind == "SEMANTIC_INPUT"
            and revision.source_uri.startswith(prefix)
        ):
            key = revision.source_uri.removeprefix(prefix)
            if key in SNAPSHOT_SEMANTIC_INPUT_KEYS:
                if key in values:
                    raise ValueError("candidate semantic input is duplicated")
                values[key] = revision.fingerprint
    if set(values) != SNAPSHOT_SEMANTIC_INPUT_KEYS:
        raise ValueError("candidate semantic input set is incomplete")
    return dict(sorted(values.items()))


def _truth_digest(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        state = logical_database_state(connection)
    payload = {
        "schemaSha256": state.schema_sha256,
        "tables": {
            table: digest
            for table, digest in sorted(state.table_sha256.items())
            if table != "metadata"
        },
        "rowCounts": {
            table: count
            for table, count in sorted(state.table_row_counts.items())
            if table != "metadata"
        },
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _replace_metadata(path: Path, values: Mapping[str, str]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"staged database is missing or unsafe: {path.name}")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """
            INSERT INTO metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            tuple(sorted(values.items())),
        )
        connection.commit()


def _projection_declaration(path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(path)) as projection:
        metadata = dict(projection.execute("SELECT key, value FROM metadata"))
        content_digest = compute_projection_artifact_content_digest(projection)
        table_counts = {
            str(table): int(count)
            for table, count in (
                (table, projection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in (
                    "projection_rows",
                    "projection_evidence",
                    "projection_lineage",
                    "projection_reviews",
                )
            )
        }
    if metadata.get("content_digest") != content_digest:
        raise ValueError(f"projection content digest drifted: {path.name}")
    return {
        **database_metrics(path),
        "schemaVersion": str(metadata.get("schema_version") or ""),
        "projectionVersion": str(metadata.get("projection_version") or ""),
        "ontologyVersion": str(metadata.get("ontology_version") or ""),
        "contentDigest": content_digest,
        "reviewConfigSha256": str(
            metadata.get("review_config_sha256") or ""
        ),
        "sourceRevisionSetHash": str(
            metadata.get("source_revision_set_hash") or ""
        ),
        "validationStatus": "VALID",
        "tableCounts": table_counts,
    }


def reseal_incremental_snapshot_candidate(
    *,
    staging: Path,
    base_manifest: Mapping[str, object],
    base_manifest_sha256: str,
    candidate_source_manifest: SourceManifest,
    project_root: Path,
    discovery_database: Path,
) -> ResealedIncrementalCandidate:
    """Bind every staged artifact to one new immutable snapshot identity."""

    if staging.is_symlink():
        raise ValueError("incremental candidate staging is missing or unsafe")
    staging = staging.resolve(strict=True)
    if not staging.is_dir():
        raise ValueError("incremental candidate staging is missing or unsafe")
    if not isinstance(base_manifest, Mapping):
        raise TypeError("base snapshot manifest is required")
    if not isinstance(base_manifest_sha256, str) or len(base_manifest_sha256) != 64:
        raise ValueError("base manifest SHA-256 is invalid")
    generated_at = normalize_snapshot_generated_at(
        candidate_source_manifest.generated_at
    )
    if generated_at != candidate_source_manifest.generated_at:
        raise ValueError("candidate Source Manifest time is not canonical UTC")
    semantic_inputs = candidate_semantic_inputs(candidate_source_manifest)
    source_sha256 = semantic_inputs_sha256(semantic_inputs)
    build_id = snapshot_build_id(generated_at, source_sha256)
    base_build_id = str(base_manifest.get("buildId") or "")
    ontology_version = str(base_manifest.get("ontologyVersion") or "")
    if not base_build_id or not ontology_version:
        raise ValueError("base snapshot identity is incomplete")

    before_identity_truth = {
        name: _truth_digest(staging / name)
        for name in ("catalog.sqlite", "search.sqlite")
    }
    main_metadata = {
        "catalog.sqlite": {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "source_fingerprint": semantic_inputs["discovery"],
        },
        "core.sqlite": {
            "schema_version": CORE_SCHEMA_VERSION,
            "source_fingerprint": semantic_inputs["discovery"],
        },
        "search.sqlite": {
            "schema_version": SEARCH_SCHEMA_VERSION,
            "source_fingerprint": source_sha256,
        },
        "cache.sqlite": {
            "schema_version": CACHE_SCHEMA_VERSION,
            "source_fingerprint": source_sha256,
            "disposable": "true",
        },
    }
    for name, values in main_metadata.items():
        _replace_metadata(
            staging / name,
            {
                **values,
                "generated_at": generated_at,
                "snapshot_build_id": build_id,
                "snapshot_source_fingerprint": source_sha256,
            },
        )

    core_path = staging / "core.sqlite"
    with closing(sqlite3.connect(core_path)) as core:
        core.execute("BEGIN IMMEDIATE")
        core.execute(
            "UPDATE projection_runs SET built_at=?",
            (generated_at,),
        )
        core.commit()
    for name in DOMAIN_PROJECTIONS:
        _replace_metadata(
            staging / "domain_exports" / f"{name}.sqlite",
            {
                "schema_version": PROJECTION_SCHEMA_VERSION,
                "built_at": generated_at,
                "snapshot_build_id": build_id,
                "snapshot_source_fingerprint": source_sha256,
            },
        )

    runtime_health = _seal_runtime_health_summary(
        core_path=core_path,
        build_id=build_id,
        source_sha256=source_sha256,
    )
    _finalize_staged_database_journals(staging)
    after_identity_truth = {
        name: _truth_digest(staging / name)
        for name in ("catalog.sqlite", "search.sqlite")
    }
    if before_identity_truth != after_identity_truth:
        raise ValueError("identity-only database truth changed during reseal")

    databases = {
        name: database_metrics(staging / name)
        for name in DATABASE_NAMES
    }
    databases.update(
        {
            f"domain_exports/{name}.sqlite": _projection_declaration(
                staging / "domain_exports" / f"{name}.sqlite"
            )
            for name in DOMAIN_PROJECTIONS
        }
    )
    manifest: dict[str, object] = {
        "schema": SNAPSHOT_SCHEMA,
        "buildId": build_id,
        "generatedAt": generated_at,
        "source": {
            "kind": SNAPSHOT_SOURCE_KIND,
            "uri": SNAPSHOT_SOURCE_URI,
            "sha256": source_sha256,
            "inputs": semantic_inputs,
        },
        "ontologyVersion": ontology_version,
        "databases": databases,
        "runtimeHealth": runtime_health,
        "cutover": {
            "mode": "shadow",
            "defaultQuerySource": "legacy",
            "reason": "quality gates have not been resealed yet",
        },
        "incrementalUpdate": source_manifest_binding(
            candidate_source_manifest
        ),
        "previousSnapshot": {
            "buildId": base_build_id,
            "manifestSha256": base_manifest_sha256,
        },
    }
    _write_json(staging / "manifest.json", manifest)
    quality = _evaluate_staged_quality_gates(
        project_root=project_root.resolve(),
        staging=staging,
        discovery_database=discovery_database.resolve(),
        generated_at=generated_at,
        allow_unsealed_snapshot=True,
    )
    manifest = _seal_staged_quality_report(
        staging=staging,
        manifest=manifest,
        report=quality,
    )
    quality = _evaluate_staged_quality_gates(
        project_root=project_root.resolve(),
        staging=staging,
        discovery_database=discovery_database.resolve(),
        generated_at=generated_at,
    )
    manifest = _seal_staged_quality_report(
        staging=staging,
        manifest=manifest,
        report=quality,
    )
    cutover = manifest.get("cutover")
    quality_binding = manifest.get("qualityGates")
    if (
        not isinstance(cutover, Mapping)
        or cutover.get("mode") != "shadow"
        or cutover.get("defaultQuerySource") != "legacy"
        or not isinstance(quality_binding, Mapping)
        or quality_binding.get("cutoverEligible") is not False
    ):
        raise ValueError("incremental candidate escaped shadow/legacy policy")
    _validate_staged_snapshot_for_promotion(
        staging=staging,
        manifest=manifest,
    )
    return ResealedIncrementalCandidate(
        manifest=manifest,
        identity_only_databases=before_identity_truth,
    )


__all__ = [
    "IncrementalPublicationError",
    "IncrementalPublicationNotReplaced",
    "IncrementalPublicationUncertain",
    "ResealedIncrementalCandidate",
    "candidate_semantic_inputs",
    "reseal_incremental_snapshot_candidate",
    "seal_incremental_narrow_gate_report",
    "publish_incremental_shadow_snapshot",
    "verify_incremental_shadow_publication",
]
