"""Atomic parallel-snapshot builder for ARK Knowledge Base vNext."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath

from .class_hierarchy import class_hierarchy_contract_fingerprint
from .native_ingest import native_evidence_input_sha256
from .ontology import load_ontology
from .projections import (
    DOMAIN_PROJECTIONS,
    PROJECTION_SCHEMA_SQL,
    PROJECTION_SCHEMA_VERSION,
    build_domain_projections,
    compute_core_projection_content_digest,
    compute_projection_artifact_content_digest,
)
from .benchmark import (
    QUERY_CASE_RESULT_SCHEMA,
    QUERY_DIAGNOSTICS_SCHEMA,
    QUERY_FAILURE_MATRIX_SCHEMA,
    query_diagnostic_artifact_bytes,
)
from .query_planner import (
    is_valid_generic_evidence_uri,
    source_revision_is_fresh,
)
from .quality_contract import (
    BENCHMARK_SCHEMA,
    QUALITY_GATE_SCHEMA,
    validate_quality_gate_contract,
)
from .schema_capabilities import CORE_SCHEMA_VERSION
from .source_manifest import (
    SNAPSHOT_SEMANTIC_INPUT_KEYS,
    compare_source_manifests,
    scan_source_manifest,
    source_manifest_binding,
    source_manifest_from_binding,
)
from .storage import (
    CACHE_SCHEMA_SQL,
    CACHE_SCHEMA_VERSION,
    CATALOG_SCHEMA_VERSION,
    FULL_CATALOG_SCHEMA_SQL,
    FULL_CORE_SCHEMA_SQL,
    SEARCH_SCHEMA_SQL,
    SEARCH_SCHEMA_VERSION,
    build_cache_database,
    build_catalog_database,
    build_core_database,
    build_search_database,
    database_metrics,
)


DATABASE_NAMES = (
    "catalog.sqlite",
    "core.sqlite",
    "search.sqlite",
    "cache.sqlite",
)
SNAPSHOT_SCHEMA = "ark-kb-vnext-snapshot/v1"
CURRENT_POINTER_NAME = "current.json"
CURRENT_POINTER_KEYS = frozenset({"buildId", "snapshotRelativePath"})
SNAPSHOT_SOURCE_KIND = "semantic_input_set"
SNAPSHOT_SOURCE_URI = "kb-inputs://ark/vnext"
RUNTIME_HEALTH_SCHEMA = "ark-kb-runtime-health/v1"
SEMANTIC_PRODUCER_CONTRACT_SCHEMA = (
    "ark-kb-semantic-producer-contract/v1"
)
_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
)
_SAFE_BUILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

_ACTIVE_STALE_SOURCE_COUNT_QUERIES = (
    """
    SELECT COUNT(*)
    FROM packages AS package
    LEFT JOIN source_revisions AS revision
      ON revision.revision_id=package.current_revision_id
    WHERE source_revision_is_fresh(
            revision.source_kind,
            revision.source_uri,
            revision.source_fingerprint,
            revision.producer_version,
            revision.schema_version,
            revision.generated_at,
            revision.freshness_status
          )=0
    """,
    """
    SELECT COUNT(*)
    FROM knowledge_roles AS role
    LEFT JOIN source_revisions AS revision
      ON revision.revision_id=role.source_revision_id
    WHERE role.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
      AND source_revision_is_fresh(
            revision.source_kind,
            revision.source_uri,
            revision.source_fingerprint,
            revision.producer_version,
            revision.schema_version,
            revision.generated_at,
            revision.freshness_status
          )=0
    """,
    """
    SELECT COUNT(*)
    FROM domain_memberships AS membership
    LEFT JOIN source_revisions AS revision
      ON revision.revision_id=membership.source_revision_id
    WHERE membership.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
      AND source_revision_is_fresh(
            revision.source_kind,
            revision.source_uri,
            revision.source_fingerprint,
            revision.producer_version,
            revision.schema_version,
            revision.generated_at,
            revision.freshness_status
          )=0
    """,
    """
    SELECT COUNT(*)
    FROM edges AS edge
    LEFT JOIN source_revisions AS revision
      ON revision.revision_id=edge.source_revision_id
    WHERE edge.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
      AND (
        source_revision_is_fresh(
          revision.source_kind,
          revision.source_uri,
          revision.source_fingerprint,
          revision.producer_version,
          revision.schema_version,
          revision.generated_at,
          revision.freshness_status
        )=0
        OR evidence_uri_is_recovered(edge.evidence_uri)=0
      )
    """,
    """
    SELECT COUNT(*)
    FROM facts AS fact
    WHERE fact.current=1
      AND fact.status IN (
        'CONFIRMED', 'VERIFIED', 'RESOLVED', 'CONFIRMED_EMPTY'
      )
      AND NOT EXISTS (
        SELECT 1
        FROM fact_evidence AS evidence
        JOIN source_revisions AS revision
          ON revision.revision_id=evidence.source_revision_id
        WHERE evidence.fact_id=fact.fact_id
          AND evidence_uri_is_recovered(evidence.evidence_uri)=1
          AND source_revision_is_fresh(
                revision.source_kind,
                revision.source_uri,
                revision.source_fingerprint,
                revision.producer_version,
                revision.schema_version,
                revision.generated_at,
                revision.freshness_status
              )=1
      )
    """,
    """
    SELECT COUNT(*)
    FROM native_functions AS function
    LEFT JOIN source_revisions AS revision
      ON revision.revision_id=function.source_revision_id
    WHERE function.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
      AND source_revision_is_fresh(
            revision.source_kind,
            revision.source_uri,
            revision.source_fingerprint,
            revision.producer_version,
            revision.schema_version,
            revision.generated_at,
            revision.freshness_status
          )=0
    """,
    """
    SELECT COUNT(*)
    FROM native_blueprint_links AS link
    LEFT JOIN source_revisions AS graph_revision
      ON graph_revision.revision_id=link.blueprint_graph_source_revision_id
    LEFT JOIN native_functions AS function
      ON function.native_function_id=link.native_function_id
    LEFT JOIN source_revisions AS native_revision
      ON native_revision.revision_id=function.source_revision_id
    WHERE link.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
      AND (
        source_revision_is_fresh(
          graph_revision.source_kind,
          graph_revision.source_uri,
          graph_revision.source_fingerprint,
          graph_revision.producer_version,
          graph_revision.schema_version,
          graph_revision.generated_at,
          graph_revision.freshness_status
        )=0
        OR function.native_function_id IS NULL
        OR function.status NOT IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
        OR function.confidence NOT IN ('HIGH', 'CONFIRMED')
        OR source_revision_is_fresh(
             native_revision.source_kind,
             native_revision.source_uri,
             native_revision.source_fingerprint,
             native_revision.producer_version,
             native_revision.schema_version,
             native_revision.generated_at,
             native_revision.freshness_status
           )=0
      )
    """,
    """
    SELECT COUNT(*)
    FROM asset_class_assignments AS assignment
    LEFT JOIN source_revisions AS revision
      ON revision.revision_id=assignment.source_revision_id
    WHERE assignment.status IN (
        'EXTRACTED', 'CONFIRMED', 'VERIFIED', 'RESOLVED'
      )
      AND (
        source_revision_is_fresh(
          revision.source_kind,
          revision.source_uri,
          revision.source_fingerprint,
          revision.producer_version,
          revision.schema_version,
          revision.generated_at,
          revision.freshness_status
        )=0
        OR evidence_uri_is_recovered(assignment.evidence_uri)=0
      )
    """,
    """
    SELECT COUNT(*)
    FROM class_edges AS edge
    LEFT JOIN source_revisions AS revision
      ON revision.revision_id=edge.source_revision_id
    WHERE edge.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
      AND (
        source_revision_is_fresh(
          revision.source_kind,
          revision.source_uri,
          revision.source_fingerprint,
          revision.producer_version,
          revision.schema_version,
          revision.generated_at,
          revision.freshness_status
        )=0
        OR evidence_uri_is_recovered(edge.evidence_id)=0
      )
    """,
    """
    SELECT COUNT(*)
    FROM classes AS class
    LEFT JOIN source_revisions AS revision
      ON revision.revision_id=class.source_revision_id
    WHERE class.status IN (
        'IDENTIFIED', 'EXTRACTED', 'CONFIRMED', 'VERIFIED', 'RESOLVED'
      )
      AND source_revision_is_fresh(
            revision.source_kind,
            revision.source_uri,
            revision.source_fingerprint,
            revision.producer_version,
            revision.schema_version,
            revision.generated_at,
            revision.freshness_status
          )=0
    """,
)


@dataclass(frozen=True)
class CurrentSnapshot:
    """One pointer-resolved snapshot location bound to a single build."""

    root: Path
    snapshot_dir: Path
    manifest_path: Path
    pointer_path: Path
    build_id: str
    manifest: dict[str, object]
    layout: str


def _sql_source_revision_is_fresh(
    source_kind: object,
    source_uri: object,
    source_fingerprint: object,
    producer_version: object,
    schema_version: object,
    generated_at: object,
    freshness: object,
) -> int:
    return int(
        source_revision_is_fresh(
            {
                "sourceKind": source_kind,
                "sourceUri": source_uri,
                "sourceFingerprint": source_fingerprint,
                "producerVersion": producer_version,
                "schemaVersion": schema_version,
                "generatedAt": generated_at,
                "freshness": freshness,
            },
            require_revision_id=False,
        )
    )


def _register_runtime_health_functions(
    connection: sqlite3.Connection,
) -> None:
    connection.create_function(
        "source_revision_is_fresh",
        7,
        _sql_source_revision_is_fresh,
    )
    connection.create_function(
        "evidence_uri_is_recovered",
        1,
        lambda value: int(is_valid_generic_evidence_uri(value)),
    )


def active_stale_source_count(connection: sqlite3.Connection) -> int:
    """Count active semantic rows that lack recovered, fresh provenance."""

    _register_runtime_health_functions(connection)
    return sum(
        int(connection.execute(sql).fetchone()[0] or 0)
        for sql in _ACTIVE_STALE_SOURCE_COUNT_QUERIES
    )


def _seal_runtime_health_summary(
    *,
    core_path: Path,
    build_id: str,
    source_sha256: str,
) -> dict[str, object]:
    with closing(sqlite3.connect(core_path)) as core:
        active_stale_sources = active_stale_source_count(core)
        core.executemany(
            """
            INSERT INTO metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (
                ("runtime_health_schema", RUNTIME_HEALTH_SCHEMA),
                (
                    "runtime_health_active_stale_sources",
                    str(active_stale_sources),
                ),
                ("runtime_health_build_id", build_id),
                ("runtime_health_source_sha256", source_sha256),
            ),
        )
        core.commit()
    return {
        "schema": RUNTIME_HEALTH_SCHEMA,
        "buildId": build_id,
        "sourceSha256": source_sha256,
        "activeStaleSources": active_stale_sources,
        "sealedInSnapshotManifest": True,
    }


def validate_snapshot_runtime_health_summary(
    *,
    manifest: Mapping[str, object],
    core_metadata: Mapping[str, object],
) -> int:
    """Bind the sealed runtime-health count to manifest and core metadata."""

    summary = manifest.get("runtimeHealth")
    expected_keys = {
        "schema",
        "buildId",
        "sourceSha256",
        "activeStaleSources",
        "sealedInSnapshotManifest",
    }
    source = manifest.get("source")
    source_mapping = source if isinstance(source, Mapping) else {}
    if not isinstance(summary, Mapping) or set(summary) != expected_keys:
        raise ValueError("sealed runtime health summary is missing or invalid")
    active_stale_sources = summary.get("activeStaleSources")
    if (
        summary.get("schema") != RUNTIME_HEALTH_SCHEMA
        or summary.get("buildId") != manifest.get("buildId")
        or summary.get("sourceSha256") != source_mapping.get("sha256")
        or isinstance(active_stale_sources, bool)
        or not isinstance(active_stale_sources, int)
        or active_stale_sources < 0
        or summary.get("sealedInSnapshotManifest") is not True
    ):
        raise ValueError("sealed runtime health summary identity is invalid")
    expected_metadata = {
        "runtime_health_schema": RUNTIME_HEALTH_SCHEMA,
        "runtime_health_active_stale_sources": str(active_stale_sources),
        "runtime_health_build_id": str(manifest.get("buildId") or ""),
        "runtime_health_source_sha256": str(
            source_mapping.get("sha256") or ""
        ),
    }
    if any(
        str(core_metadata.get(key) or "") != value
        for key, value in expected_metadata.items()
    ):
        raise ValueError(
            "sealed runtime health summary does not match core metadata"
        )
    return active_stale_sources


def _read_json_object(
    path: Path,
    *,
    label: str,
    transient_retries: int = 0,
) -> dict[str, object]:
    payload: object = None
    last_error: OSError | json.JSONDecodeError | None = None
    for attempt in range(transient_retries + 1):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            last_error = None
            break
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < transient_retries:
                time.sleep(0.002)
    if last_error is not None:
        raise ValueError(f"{label} is unreadable: {path}") from last_error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _safe_build_id(value: object) -> str:
    build_id = str(value or "").strip()
    if (
        not build_id
        or not _SAFE_BUILD_ID.fullmatch(build_id)
        or build_id in {".", ".."}
    ):
        raise ValueError("snapshot buildId is missing or unsafe")
    return build_id


def resolve_current_snapshot(
    root: Path,
    *,
    allow_legacy: bool = True,
) -> CurrentSnapshot:
    """Resolve one current pointer without ever combining snapshot roots."""

    root = root.resolve()
    pointer_path = root / CURRENT_POINTER_NAME
    if pointer_path.is_file():
        pointer = _read_json_object(
            pointer_path,
            label="current snapshot pointer",
            transient_retries=8,
        )
        if set(pointer) != CURRENT_POINTER_KEYS:
            raise ValueError(
                "current snapshot pointer must contain only buildId and "
                "snapshotRelativePath"
            )
        build_id = _safe_build_id(pointer.get("buildId"))
        relative_text = str(
            pointer.get("snapshotRelativePath") or ""
        ).strip()
        if (
            not relative_text
            or "\\" in relative_text
            or PureWindowsPath(relative_text).is_absolute()
        ):
            raise ValueError("snapshotRelativePath must be a POSIX path")
        relative = PurePosixPath(relative_text)
        expected = PurePosixPath("snapshots") / build_id
        if (
            relative.is_absolute()
            or relative != expected
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError(
                "snapshotRelativePath must equal snapshots/<buildId>"
            )
        snapshot_dir = (
            root.joinpath(*relative.parts).resolve()
        )
        snapshots_root = (root / "snapshots").resolve()
        if (
            not snapshot_dir.is_relative_to(snapshots_root)
            or snapshot_dir.parent != snapshots_root
        ):
            raise ValueError("snapshot pointer escapes the snapshots root")
        manifest_path = snapshot_dir / "manifest.json"
        manifest = _read_json_object(
            manifest_path,
            label="immutable snapshot manifest",
        )
        if (
            manifest.get("schema") != SNAPSHOT_SCHEMA
            or str(manifest.get("buildId") or "") != build_id
        ):
            raise ValueError(
                "current pointer and immutable manifest do not match"
            )
        return CurrentSnapshot(
            root=root,
            snapshot_dir=snapshot_dir,
            manifest_path=manifest_path,
            pointer_path=pointer_path,
            build_id=build_id,
            manifest=manifest,
            layout="immutable-v2",
        )

    legacy_path = root / "manifests" / CURRENT_POINTER_NAME
    if allow_legacy and legacy_path.is_file():
        manifest = _read_json_object(
            legacy_path,
            label="legacy current snapshot manifest",
        )
        build_id = _safe_build_id(manifest.get("buildId"))
        if manifest.get("schema") != SNAPSHOT_SCHEMA:
            raise ValueError("legacy snapshot manifest schema is unknown")
        return CurrentSnapshot(
            root=root,
            snapshot_dir=root,
            manifest_path=legacy_path,
            pointer_path=legacy_path,
            build_id=build_id,
            manifest=manifest,
            layout="legacy-v1",
        )
    raise FileNotFoundError(
        f"current snapshot pointer is not available under {root}"
    )


def normalize_snapshot_generated_at(generated_at: str) -> str:
    """Validate RFC3339 and return one canonical UTC representation."""

    if not _RFC3339_TIMESTAMP.fullmatch(generated_at):
        raise ValueError(
            "snapshot generated_at must be an RFC3339 timestamp with "
            "a UTC designator or numeric offset"
        )
    parseable = (
        generated_at[:-1] + "+00:00"
        if generated_at.endswith("Z")
        else generated_at
    )
    try:
        parsed = datetime.fromisoformat(parseable)
        if parsed.utcoffset() is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "snapshot generated_at must be a valid RFC3339 timestamp"
        ) from exc
    normalized = parsed.astimezone(UTC)
    return normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )


def snapshot_build_id(
    generated_at: str,
    semantic_inputs_sha256: str,
) -> str:
    normalized_generated_at = normalize_snapshot_generated_at(generated_at)
    return (
        normalized_generated_at.removesuffix("+00:00")
        .replace("-", "")
        .replace(":", "")
        + "-"
        + semantic_inputs_sha256[:12]
    )


def semantic_inputs_sha256(inputs: Mapping[str, object]) -> str:
    normalized = {
        str(key): str(value or "").lower()
        for key, value in inputs.items()
    }
    return hashlib.sha256(
        json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _query_diagnostics_binding_sha256(
    *,
    build_id: str,
    corpus_sha256: str,
    quality_report_sha256: str,
    benchmark_report_sha256: str,
    case_results_sha256: str,
    failure_matrix_sha256: str,
) -> str:
    payload = {
        "schema": QUERY_DIAGNOSTICS_SCHEMA,
        "buildId": build_id,
        "corpusSha256": corpus_sha256,
        "qualityReportSha256": quality_report_sha256,
        "benchmarkReportSha256": benchmark_report_sha256,
        "caseResultsSha256": case_results_sha256,
        "failureMatrixSha256": failure_matrix_sha256,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256_file_set(
    root: Path,
    paths: list[Path],
) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _canonical_contract_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_contract_value(
                getattr(value, field.name)
            )
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _canonical_contract_value(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }
    if isinstance(value, (set, frozenset)):
        normalized = [
            _canonical_contract_value(item) for item in value
        ]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if isinstance(value, (tuple, list)):
        return [_canonical_contract_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(
        f"Unsupported semantic contract value: {type(value)!r}"
    )


def _semantic_producer_contract_fingerprint() -> str:
    """Hash runtime rule registries plus all snapshot-producer source code."""

    package_name = __package__ or "blueprint_translator.kb_vnext"
    registrations = importlib.import_module(
        f"{package_name}.registrations"
    )
    roles = importlib.import_module(f"{package_name}.roles")
    map_usage = importlib.import_module(f"{package_name}.map_usage")
    native_gold = importlib.import_module(
        f"{package_name}.native_gold_set"
    )
    adapters = importlib.import_module(f"{package_name}.adapters")
    adapter_runner = importlib.import_module(
        f"{package_name}.adapters.runner"
    )
    package_root = Path(__file__).resolve().parent
    producer_code = _sha256_file_set(
        package_root,
        list(package_root.rglob("*.py")),
    )
    payload = {
        "schema": SEMANTIC_PRODUCER_CONTRACT_SCHEMA,
        "producerCodeSha256": producer_code,
        "registrations": {
            "version": registrations.REGISTRATION_EXTRACTOR_VERSION,
            "rules": registrations.REGISTRATION_RULES,
        },
        "roles": {
            "version": roles.ROLE_CLASSIFIER_VERSION,
            "knowledgeRoles": roles.KNOWLEDGE_ROLES,
            "depthPolicies": roles.DEPTH_POLICIES,
            "percentileMetrics": roles.PERCENTILE_METRICS,
            "openStates": roles.OPEN_STATES,
            "visualClassNames": roles.VISUAL_CLASS_NAMES,
        },
        "mapUsage": {
            "version": map_usage.MAP_USAGE_EXTRACTOR_VERSION,
            "catalogSchema": map_usage.RESOURCE_NODE_CATALOG_SCHEMA,
            "edgeTypes": map_usage.MAP_USAGE_EDGE_TYPES,
            "confirmedStatuses": map_usage._CONFIRMED_STATUSES,
            "confirmedConfidence": map_usage._CONFIRMED_CONFIDENCE,
            "knownUsageStatuses": map_usage._KNOWN_USAGE_STATUSES,
            "identityStatuses": map_usage._IDENTITY_STATUSES,
            "catalogRelations": map_usage._CATALOG_RELATIONS,
        },
        "nativeGold": {
            "schema": native_gold.NATIVE_GOLD_SCHEMA,
            "confirmedEdgeMethods": (
                native_gold.CONFIRMED_EDGE_METHODS
            ),
            "confirmedInputConfidence": (
                native_gold.CONFIRMED_INPUT_CONFIDENCE
            ),
        },
        "semanticAdapters": {
            "adapterSpecs": adapters.ADAPTER_SPECS,
            "blueprintEvidenceKind": (
                adapter_runner.BLUEPRINT_EVIDENCE_KIND
            ),
            "blueprintEvidenceSchema": (
                adapter_runner.BLUEPRINT_EVIDENCE_SCHEMA
            ),
            "defaultEvidenceRole": (
                adapter_runner.DEFAULT_VALUE_EVIDENCE_ROLE
            ),
            "confidenceRank": adapter_runner.CONFIDENCE_RANK,
            "usableSourceStatuses": (
                adapter_runner.USABLE_SOURCE_STATUSES
            ),
            "directSourceMode": adapter_runner.DIRECT_SOURCE_MODE,
            "legacySourceMode": adapter_runner.LEGACY_SOURCE_MODE,
        },
    }
    encoded = json.dumps(
        _canonical_contract_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_named_file_set(
    inputs: list[tuple[str, Path]],
    *,
    digest_overrides: dict[Path, str] | None = None,
) -> str:
    """Hash files by portable logical name, including explicit missing inputs."""

    digest = hashlib.sha256()
    seen: set[str] = set()
    file_hashes = {
        path.resolve(): value
        for path, value in (digest_overrides or {}).items()
    }
    for logical_name, path in sorted(inputs, key=lambda item: item[0]):
        normalized_name = logical_name.replace("\\", "/").strip("/")
        if not normalized_name or normalized_name in seen:
            raise ValueError(
                f"Duplicate or empty semantic input name: {logical_name!r}"
            )
        seen.add(normalized_name)
        digest.update(normalized_name.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            resolved_path = path.resolve()
            if resolved_path not in file_hashes:
                file_hashes[resolved_path] = _sha256_file(resolved_path)
            digest.update(b"FILE\0")
            digest.update(file_hashes[resolved_path].encode("ascii"))
        else:
            digest.update(b"MISSING\0")
        digest.update(b"\0")
    return digest.hexdigest()


def _update_digest(digest: object, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _sqlite_value_bytes(value: object) -> bytes:
    if value is None:
        return b"NULL"
    if isinstance(value, int):
        return b"INTEGER\0" + str(value).encode("ascii")
    if isinstance(value, float):
        return b"REAL\0" + value.hex().encode("ascii")
    if isinstance(value, str):
        return b"TEXT\0" + value.encode("utf-8")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return b"BLOB\0" + bytes(value)
    raise TypeError(f"Unsupported SQLite value type: {type(value)!r}")


def _portable_package_name(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.replace("\\", "/")
    if (
        PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        return normalized.rsplit("/", 1)[-1]
    return normalized


def _evidence_database_semantic_sha256(path: Path) -> str:
    """Hash Evidence schema and typed rows independent of SQLite layout.

    ``asset_revisions.uasset_path`` is a machine-local locator.  Its portable
    semantic identity is the package filename; the referenced package and
    sidecar bytes are hashed separately by the capture digest.
    """

    digest = hashlib.sha256()
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            for pragma_name in ("application_id", "user_version"):
                value = connection.execute(
                    f"PRAGMA {pragma_name}"
                ).fetchone()
                _update_digest(
                    digest,
                    pragma_name.encode("ascii"),
                )
                _update_digest(
                    digest,
                    _sqlite_value_bytes(value[0] if value else None),
                )

            schema_rows = list(
                connection.execute(
                    """
                    SELECT type, name, tbl_name, COALESCE(sql, '')
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name, tbl_name
                    """
                )
            )
            for schema_row in schema_rows:
                _update_digest(digest, b"SCHEMA_OBJECT")
                for value in schema_row:
                    _update_digest(digest, _sqlite_value_bytes(value))

            table_names = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            ]
            for table_name in table_names:
                escaped_table = table_name.replace('"', '""')
                column_rows = list(
                    connection.execute(
                        f'PRAGMA table_info("{escaped_table}")'
                    )
                )
                columns = [str(row[1]) for row in column_rows]
                _update_digest(digest, b"TABLE")
                _update_digest(
                    digest,
                    table_name.encode("utf-8"),
                )
                for column_row in column_rows:
                    _update_digest(digest, b"COLUMN")
                    for value in column_row:
                        _update_digest(
                            digest,
                            _sqlite_value_bytes(value),
                        )

                projection = ", ".join(
                    f'"{column.replace(chr(34), chr(34) * 2)}"'
                    for column in columns
                )
                row_hashes: list[bytes] = []
                for row in connection.execute(
                    f'SELECT {projection} FROM "{escaped_table}"'
                ):
                    row_digest = hashlib.sha256()
                    for column, value in zip(columns, row, strict=True):
                        if (
                            table_name.casefold() == "asset_revisions"
                            and column.casefold() == "uasset_path"
                        ):
                            value = _portable_package_name(value)
                        _update_digest(
                            row_digest,
                            _sqlite_value_bytes(value),
                        )
                    row_hashes.append(row_digest.digest())
                _update_digest(
                    digest,
                    len(row_hashes).to_bytes(8, "big"),
                )
                for row_hash in sorted(row_hashes):
                    _update_digest(digest, row_hash)
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        # Malformed/unsupported Evidence stores are rejected by ingestion.
        # Their raw bytes still need a stable identity before that happens.
        return _sha256_file(path)
    return digest.hexdigest()


def _capture_semantic_inputs_sha256(capture_root: Path) -> str:
    """Hash every capture artifact that Blueprint ingestion can consume."""

    inputs: list[tuple[str, Path]] = []
    evidence_database_hashes: dict[Path, str] = {}
    evidence_databases = (
        list(capture_root.glob("*/evidence/evidence.sqlite"))
        if capture_root.is_dir()
        else []
    )
    for evidence_path in sorted(
        evidence_databases,
        key=lambda path: path.relative_to(capture_root).as_posix(),
    ):
        asset_root = evidence_path.parent.parent
        asset_name = asset_root.relative_to(capture_root).as_posix()
        prefix = f"captures/{asset_name}"
        evidence_database_hashes[evidence_path.resolve()] = (
            _evidence_database_semantic_sha256(evidence_path)
        )
        inputs.extend(
            [
                (
                    f"{prefix}/evidence/evidence.sqlite",
                    evidence_path,
                ),
                (
                    f"{prefix}/evidence/manifest.json",
                    evidence_path.with_name("manifest.json"),
                ),
            ]
        )

        revision_rows: list[tuple[str, str]] = []
        package_manifest_rows: list[tuple[str, str]] = []
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"file:{evidence_path.resolve().as_posix()}?mode=ro",
                uri=True,
            )
            revision_rows = [
                (str(row[0] or ""), str(row[1] or ""))
                for row in connection.execute(
                    """
                    SELECT revision_id, uasset_path
                    FROM asset_revisions
                    ORDER BY revision_id, uasset_path
                    """
                )
            ]
            package_manifest_rows = [
                (str(row[0] or ""), str(row[1] or ""))
                for row in connection.execute(
                    """
                    SELECT revision_id, path
                    FROM source_manifest
                    WHERE source_kind='package_binary'
                    ORDER BY revision_id, path
                    """
                )
            ]
        except sqlite3.DatabaseError:
            # The Evidence database itself remains part of the digest.  A
            # malformed schema is rejected later by the bounded importer.
            revision_rows = []
            package_manifest_rows = []
        finally:
            if connection is not None:
                connection.close()

        manifest_paths_by_revision: dict[str, list[str]] = {}
        for revision_id, manifest_path in package_manifest_rows:
            manifest_paths_by_revision.setdefault(revision_id, []).append(
                manifest_path
            )
        for revision_index, (revision_id, raw_path) in enumerate(
            revision_rows
        ):
            package_path = Path(raw_path)
            if not package_path.is_absolute():
                package_path = (asset_root / package_path).resolve()
            package_prefix = (
                f"{prefix}/package/revision-{revision_index:04d}"
            )
            inputs.append(
                (
                    f"{package_prefix}/primary-{package_path.name}",
                    package_path,
                )
            )
            if package_path.suffix.casefold() == ".uasset":
                for suffix in (".uexp", ".ubulk"):
                    inputs.append(
                        (
                            f"{package_prefix}/sidecar-{suffix[1:]}",
                            package_path.with_suffix(suffix),
                        )
                    )
            for manifest_index, raw_manifest_path in enumerate(
                manifest_paths_by_revision.get(revision_id, [])
            ):
                expected_path = package_path.with_name(
                    Path(raw_manifest_path).name
                )
                inputs.append(
                    (
                        f"{package_prefix}/manifest-"
                        f"{manifest_index:04d}-{expected_path.name}",
                        expected_path,
                    )
                )
    return _sha256_named_file_set(
        inputs,
        digest_overrides=evidence_database_hashes,
    )


def _snapshot_semantic_input_hashes(
    *,
    project_root: Path,
    discovery_database: Path,
    legacy_kb_root: Path,
    capture_root: Path,
    native_root: Path,
    map_evidence_path: Path | None,
) -> dict[str, str]:
    """Fingerprint every source family that can affect snapshot bytes."""

    ontology_root = project_root / "ontology"
    ontology_paths = [
        ontology_root / name
        for name in (
            "ark_domains.v1.json",
            "ark_roles.v1.json",
            "ark_edge_types.v2.json",
            "ark_fact_types.v2.json",
            "native_gold_set.v1.json",
            "projection_review.v1.json",
        )
        if (ontology_root / name).is_file()
    ]
    benchmark_gold_set_path = (
        project_root / "tests" / "fixtures" / "kb_query_gold_set.v1.json"
    )
    quality_gold_paths = [
        path
        for path in (
            project_root
            / "tests"
            / "fixtures"
            / "kb_registration_gold_set.json",
            project_root / "tests" / "fixtures" / "kb_role_gold_set.json",
        )
        if path.is_file()
    ]
    if not benchmark_gold_set_path.is_file():
        raise FileNotFoundError(benchmark_gold_set_path)
    hashes = {
        "discovery": _sha256_file(discovery_database),
        "captures": _capture_semantic_inputs_sha256(capture_root),
        "classHierarchyContract": class_hierarchy_contract_fingerprint(),
        "semanticProducerContract": (
            _semantic_producer_contract_fingerprint()
        ),
        "legacy": _sha256_file_set(
            legacy_kb_root,
            (
                list(legacy_kb_root.glob("*.sqlite"))
                if legacy_kb_root.is_dir()
                else []
            ),
        ),
        "ontology": _sha256_file_set(ontology_root, ontology_paths),
        "benchmarkGold": _sha256_file(benchmark_gold_set_path),
        "qualityGold": _sha256_file_set(
            project_root,
            quality_gold_paths,
        ),
        "mapEvidence": (
            _sha256_file(map_evidence_path)
            if map_evidence_path is not None
            and map_evidence_path.is_file()
            else hashlib.sha256(
                b"MAP_EVIDENCE_NOT_AVAILABLE"
            ).hexdigest()
        ),
        "nativeEvidence": native_evidence_input_sha256(native_root),
    }
    if set(hashes) != SNAPSHOT_SEMANTIC_INPUT_KEYS:
        raise AssertionError("snapshot semantic input registry is incomplete")
    return hashes


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    contents = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _write_current_pointer(output_dir: Path, build_id: str) -> None:
    _write_json(
        output_dir / CURRENT_POINTER_NAME,
        {
            "buildId": build_id,
            "snapshotRelativePath": f"snapshots/{build_id}",
        },
    )


def _evaluate_staged_quality_gates(
    *,
    project_root: Path,
    staging: Path,
    discovery_database: Path,
    generated_at: str,
    allow_unsealed_snapshot: bool = False,
) -> dict[str, object]:
    """Run gates against the complete candidate before it becomes current."""

    from .quality_gates import evaluate_quality_gates

    return evaluate_quality_gates(
        project_root=project_root,
        snapshot_root=staging,
        discovery_database=discovery_database,
        generated_at=generated_at,
        allow_unsealed_snapshot=allow_unsealed_snapshot,
    )


def _seal_staged_quality_report(
    *,
    staging: Path,
    manifest: dict[str, object],
    report: Mapping[str, object],
) -> dict[str, object]:
    if str(report.get("buildId") or "") != str(
        manifest.get("buildId") or ""
    ):
        raise ValueError("quality report buildId does not match the snapshot")
    summary = report.get("summary")
    benchmark = report.get("benchmark")
    if not isinstance(summary, Mapping) or not isinstance(
        benchmark, Mapping
    ):
        raise ValueError("quality report summary or benchmark is missing")
    reports = staging / "reports"
    gate_path = reports / "quality_gates.json"
    benchmark_path = reports / "query_benchmark.json"
    _write_json(gate_path, report)
    _write_json(benchmark_path, benchmark)
    gate_sha = _sha256_file(gate_path)
    benchmark_sha = _sha256_file(benchmark_path)
    diagnostic_quality: dict[str, object] = {}
    diagnostics = benchmark.get("diagnosticArtifacts")
    if isinstance(diagnostics, Mapping):
        if diagnostics.get("buildBinding") != "SNAPSHOT_METADATA":
            raise ValueError(
                "sealed query diagnostics are not snapshot-bound"
            )
        case_bytes, matrix_bytes = query_diagnostic_artifact_bytes(
            benchmark,
            expected_build_id=str(manifest.get("buildId") or ""),
        )
        case_path = reports / "query_case_results.jsonl"
        matrix_path = reports / "query_failure_matrix.json"
        _write_bytes(case_path, case_bytes)
        _write_bytes(matrix_path, matrix_bytes)
        diagnostic_quality = {
            "diagnosticsSchema": QUERY_DIAGNOSTICS_SCHEMA,
            "caseResultsUri": (
                "reports/query_case_results.jsonl"
            ),
            "caseResultsSha256": _sha256_file(case_path),
            "failureMatrixUri": (
                "reports/query_failure_matrix.json"
            ),
            "failureMatrixSha256": _sha256_file(matrix_path),
        }
        diagnostic_quality["diagnosticsBindingSha256"] = (
            _query_diagnostics_binding_sha256(
                build_id=str(manifest.get("buildId") or ""),
                corpus_sha256=str(
                    diagnostics.get("corpusSha256") or ""
                ),
                quality_report_sha256=gate_sha,
                benchmark_report_sha256=benchmark_sha,
                case_results_sha256=str(
                    diagnostic_quality["caseResultsSha256"]
                ),
                failure_matrix_sha256=str(
                    diagnostic_quality["failureMatrixSha256"]
                ),
            )
        )
    eligible = bool(summary.get("cutoverEligible"))
    failed = int(summary.get("failed") or 0)
    sealed = dict(manifest)
    sealed["qualityGates"] = {
        "schema": str(report.get("schema") or ""),
        "reportUri": "reports/quality_gates.json",
        "sha256": gate_sha,
        "benchmarkUri": "reports/query_benchmark.json",
        "benchmarkSha256": benchmark_sha,
        "passed": int(summary.get("passed") or 0),
        "failed": failed,
        "cutoverEligible": eligible,
        "sealedInSnapshotManifest": True,
        **diagnostic_quality,
    }
    sealed["cutover"] = {
        "mode": "ready" if eligible else "shadow",
        "defaultQuerySource": "vnext" if eligible else "legacy",
        "reason": (
            "all critical quality gates passed before publication"
            if eligible
            else f"{failed} critical quality gates remain open"
        ),
    }
    _write_json(staging / "manifest.json", sealed)
    return sealed


def _validate_output_root(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    existing = [item for item in output_dir.iterdir() if item.name != ".build"]
    if not existing:
        return
    pointer = output_dir / CURRENT_POINTER_NAME
    legacy_marker = output_dir / "manifests" / CURRENT_POINTER_NAME
    if not pointer.is_file() and not legacy_marker.is_file():
        raise ValueError(
            "Refusing to modify non-vNext directory without a current "
            f"pointer or legacy manifest: {output_dir}"
        )
    resolve_current_snapshot(output_dir)


def _staged_relative_path(
    staging: Path,
    value: object,
    *,
    label: str,
) -> Path:
    text = str(value or "").strip()
    if (
        not text
        or "\\" in text
        or PureWindowsPath(text).is_absolute()
    ):
        raise ValueError(f"{label} must be a relative POSIX path")
    relative = PurePosixPath(text)
    if (
        relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} is unsafe")
    path = staging.joinpath(*relative.parts).resolve()
    root = staging.resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"{label} escapes the staged snapshot")
    return path


def _validate_staged_database(
    *,
    staging: Path,
    relative_name: str,
    declared: Mapping[str, object],
    build_id: str,
    source_sha256: str,
    require_snapshot_identity: bool,
    expected_metadata: Mapping[str, str] | None = None,
) -> dict[str, str]:
    path = _staged_relative_path(
        staging,
        relative_name,
        label="database artifact",
    )
    if not path.is_file():
        raise ValueError(f"staged database is missing: {relative_name}")
    declared_sha = str(declared.get("sha256") or "").lower()
    declared_bytes = declared.get("bytes")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", declared_sha)
        or isinstance(declared_bytes, bool)
        or not isinstance(declared_bytes, int)
        or declared_bytes < 0
        or declared_sha != _sha256_file(path)
        or declared_bytes != path.stat().st_size
        or str(declared.get("integrity") or "") != "ok"
        or int(declared.get("foreignKeyViolations") or 0) != 0
    ):
        raise ValueError(
            f"staged database manifest mismatch: {relative_name}"
        )
    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            integrity = str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            foreign_keys = list(
                connection.execute("PRAGMA foreign_key_check")
            )
            if integrity != "ok" or foreign_keys:
                raise ValueError(
                    f"staged database validation failed: {relative_name}"
                )
            if require_snapshot_identity:
                metadata = dict(
                    connection.execute("SELECT key, value FROM metadata")
                )
                if (
                    metadata.get("snapshot_build_id") != build_id
                    or metadata.get("snapshot_source_fingerprint")
                    != source_sha256
                ):
                    raise ValueError(
                        "staged database snapshot identity mismatch: "
                        + relative_name
                    )
            else:
                metadata = dict(
                    connection.execute("SELECT key, value FROM metadata")
                )
            normalized_metadata = {
                str(key): str(value)
                for key, value in metadata.items()
            }
            if expected_metadata is not None and any(
                normalized_metadata.get(key) != value
                for key, value in expected_metadata.items()
            ):
                raise ValueError(
                    "staged database metadata mismatch: "
                    + relative_name
                )
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(
            f"staged artifact is not a valid SQLite database: {relative_name}"
        ) from exc
    return normalized_metadata


def _is_sha256(value: object) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "").lower()))


def _is_ontology_version(value: object) -> bool:
    normalized = str(value or "")
    parts = normalized.split("|")
    return (
        bool(normalized)
        and "ark-fact-types/v2" in parts
        and all(
            part.startswith("ark-")
            and "/v" in part
            and part.rsplit("/v", 1)[1].isdigit()
            for part in parts
        )
    )


def _snapshot_database_relative_names(
    *,
    include_cache: bool,
) -> tuple[str, ...]:
    main_names = tuple(
        name
        for name in DATABASE_NAMES
        if include_cache or name != "cache.sqlite"
    )
    return (
        *main_names,
        *(
            f"domain_exports/{name}.sqlite"
            for name in DOMAIN_PROJECTIONS
        ),
    )


def validate_snapshot_journal_safety(
    snapshot_dir: Path,
    *,
    require_delete: bool,
    include_cache: bool = True,
) -> None:
    """Reject logical SQLite content that is not sealed in the main file."""

    for relative_name in _snapshot_database_relative_names(
        include_cache=include_cache,
    ):
        path = snapshot_dir / relative_name
        wal_path = Path(f"{path}-wal")
        shm_path = Path(f"{path}-shm")
        try:
            wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        except OSError as exc:
            raise ValueError(
                f"{relative_name} WAL sidecar is unreadable"
            ) from exc
        if wal_bytes:
            raise ValueError(
                f"{relative_name} has a non-empty WAL sidecar"
            )
        if require_delete and (wal_path.exists() or shm_path.exists()):
            raise ValueError(
                f"{relative_name} has an unsealed SQLite sidecar"
            )
        if not require_delete or not path.is_file():
            continue
        try:
            with closing(
                sqlite3.connect(
                    f"file:{path.resolve().as_posix()}?mode=ro",
                    uri=True,
                )
            ) as connection:
                journal_mode = str(
                    connection.execute(
                        "PRAGMA journal_mode"
                    ).fetchone()[0]
                ).lower()
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ValueError(
                f"{relative_name} journal mode is unreadable"
            ) from exc
        if journal_mode != "delete":
            raise ValueError(
                f"{relative_name} journal mode is not sealed"
            )


def _finalize_staged_database_journals(staging: Path) -> None:
    """Checkpoint build-time WAL files and publish main-file-only stores."""

    for relative_name in DATABASE_NAMES:
        path = staging / relative_name
        if not path.is_file():
            raise FileNotFoundError(path)
        connection = sqlite3.connect(path)
        try:
            mode = str(
                connection.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
            ).lower()
            if mode == "wal":
                checkpoint = tuple(
                    int(value)
                    for value in connection.execute(
                        "PRAGMA wal_checkpoint(TRUNCATE)"
                    ).fetchone()
                )
                if checkpoint[0] != 0:
                    raise ValueError(
                        f"{relative_name} WAL checkpoint is busy"
                    )
            sealed_mode = str(
                connection.execute(
                    "PRAGMA journal_mode=DELETE"
                ).fetchone()[0]
            ).lower()
            if sealed_mode != "delete":
                raise ValueError(
                    f"{relative_name} journal mode could not be sealed"
                )
        finally:
            connection.close()
        wal_path = Path(f"{path}-wal")
        shm_path = Path(f"{path}-shm")
        if wal_path.exists() and wal_path.stat().st_size:
            raise ValueError(
                f"{relative_name} WAL remained non-empty after checkpoint"
            )
        wal_path.unlink(missing_ok=True)
        shm_path.unlink(missing_ok=True)
    validate_snapshot_journal_safety(
        staging,
        require_delete=True,
    )


def validate_snapshot_source_identity(
    manifest: Mapping[str, object],
) -> dict[str, str]:
    """Validate the path-free semantic identity shared by build and read."""

    if manifest.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("snapshot manifest schema is unknown")
    build_id = _safe_build_id(manifest.get("buildId"))
    generated_at = str(manifest.get("generatedAt") or "")
    try:
        normalized_generated_at = normalize_snapshot_generated_at(
            generated_at
        )
    except ValueError as exc:
        raise ValueError("snapshot generatedAt is invalid") from exc
    if generated_at != normalized_generated_at:
        raise ValueError("snapshot generatedAt is not canonical UTC")
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("snapshot source identity is missing")
    inputs = source.get("inputs")
    if (
        set(source) != {"kind", "uri", "sha256", "inputs"}
        or str(source.get("kind") or "") != SNAPSHOT_SOURCE_KIND
        or str(source.get("uri") or "") != SNAPSHOT_SOURCE_URI
        or not isinstance(inputs, Mapping)
        or set(inputs) != SNAPSHOT_SEMANTIC_INPUT_KEYS
        or any(not _is_sha256(value) for value in inputs.values())
    ):
        raise ValueError("snapshot semantic input identity is incomplete")
    source_sha256 = str(source.get("sha256") or "").lower()
    normalized_inputs = {
        str(key): str(value).lower()
        for key, value in inputs.items()
    }
    if (
        not _is_sha256(source_sha256)
        or semantic_inputs_sha256(normalized_inputs) != source_sha256
        or snapshot_build_id(generated_at, source_sha256) != build_id
    ):
        raise ValueError("snapshot source fingerprint does not match buildId")
    ontology_version = str(manifest.get("ontologyVersion") or "")
    if not _is_ontology_version(ontology_version):
        raise ValueError("snapshot ontology version is invalid")
    try:
        bound_manifest = source_manifest_from_binding(
            manifest.get("incrementalUpdate")
        )
    except ValueError as exc:
        raise ValueError(
            "snapshot source manifest binding is invalid"
        ) from exc
    if (
        not bound_manifest.entries
        or bound_manifest.generated_at != generated_at
    ):
        raise ValueError(
            "snapshot source manifest binding is empty or has wrong time"
        )
    semantic_entries = {
        entry.source_uri.removeprefix("semantic-input://"): entry.fingerprint
        for entry in bound_manifest.entries
        if entry.source_kind == "SEMANTIC_INPUT"
        and entry.source_uri.startswith("semantic-input://")
    }
    expected_semantic_entries = (
        set(SNAPSHOT_SEMANTIC_INPUT_KEYS) | {"runtimeObservations"}
    )
    if (
        set(semantic_entries) != expected_semantic_entries
        or any(
            semantic_entries.get(key) != fingerprint
            for key, fingerprint in normalized_inputs.items()
        )
        or not _is_sha256(
            semantic_entries.get("runtimeObservations")
        )
    ):
        raise ValueError(
            "snapshot source manifest does not bind semantic inputs"
        )
    return {
        "buildId": build_id,
        "generatedAt": generated_at,
        "sourceSha256": source_sha256,
        "discoverySha256": normalized_inputs["discovery"],
        "ontologyVersion": ontology_version,
    }


def _normalized_schema_sql(value: object) -> str:
    return " ".join(str(value or "").split())


@lru_cache(maxsize=None)
def _schema_contract(
    schema_sql: str,
) -> frozenset[tuple[str, str, str, str]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_sql)
        return frozenset(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                _normalized_schema_sql(row[3]),
            )
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
                """
            )
        )
    finally:
        connection.close()


def _validate_database_schema_contract(
    path: Path,
    *,
    schema_sql: str,
    label: str,
) -> None:
    expected = _schema_contract(schema_sql)
    try:
        with closing(
            sqlite3.connect(
                f"file:{path.resolve().as_posix()}?mode=ro",
                uri=True,
            )
        ) as connection:
            actual = frozenset(
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    _normalized_schema_sql(row[3]),
                )
                for row in connection.execute(
                    """
                    SELECT type, name, tbl_name, sql
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
                    """
                )
            )
    except (OSError, sqlite3.DatabaseError) as exc:
        raise ValueError(f"{label} schema is unreadable") from exc
    if not expected <= actual:
        missing = sorted(
            f"{kind}:{name}"
            for kind, name, _table, _sql in expected - actual
        )
        raise ValueError(
            f"{label} schema contract is incomplete: {missing}"
        )


def validate_snapshot_database_schemas(snapshot_dir: Path) -> None:
    """Require every published store to implement its full schema contract."""

    main_contracts = {
        "catalog.sqlite": FULL_CATALOG_SCHEMA_SQL,
        "core.sqlite": FULL_CORE_SCHEMA_SQL,
        "search.sqlite": SEARCH_SCHEMA_SQL,
        "cache.sqlite": CACHE_SCHEMA_SQL,
    }
    for name, schema_sql in main_contracts.items():
        _validate_database_schema_contract(
            snapshot_dir / name,
            schema_sql=schema_sql,
            label=name,
        )
    for projection_name in DOMAIN_PROJECTIONS:
        relative_name = (
            f"domain_exports/{projection_name}.sqlite"
        )
        _validate_database_schema_contract(
            snapshot_dir / relative_name,
            schema_sql=PROJECTION_SCHEMA_SQL,
            label=relative_name,
        )


def validate_snapshot_projection_bindings(
    *,
    snapshot_dir: Path,
    manifest: Mapping[str, object],
) -> None:
    """Bind every disposable projection to this build and its Core truth."""

    identity = validate_snapshot_source_identity(manifest)
    databases = manifest.get("databases")
    if not isinstance(databases, Mapping):
        raise ValueError("snapshot database manifest is missing")
    core_path = snapshot_dir / "core.sqlite"
    try:
        core = sqlite3.connect(
            f"file:{core_path.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        projection_runs = {
            str(row[0]): {
                "projectionVersion": str(row[1]),
                "sourceRevisionSetHash": str(row[2]),
                "ontologyVersion": str(row[3]),
                "builtAt": str(row[4]),
                "rowCount": int(row[5]),
                "validationStatus": str(row[6]),
            }
            for row in core.execute(
                """
                SELECT
                    projection_name,
                    projection_version,
                    source_revision_set_hash,
                    ontology_version,
                    built_at,
                    row_count,
                    validation_status
                FROM projection_runs
                """
            )
        }
    except (OSError, sqlite3.DatabaseError) as exc:
        raise ValueError("core projection runs are unreadable") from exc
    try:
        if set(projection_runs) != set(DOMAIN_PROJECTIONS):
            raise ValueError(
                "core projection run contract is incomplete"
            )
        for projection_name, fact_types in DOMAIN_PROJECTIONS.items():
            relative_name = (
                f"domain_exports/{projection_name}.sqlite"
            )
            declared = databases.get(relative_name)
            if not isinstance(declared, Mapping):
                raise ValueError(
                    f"{relative_name} is not declared"
                )
            path = snapshot_dir / relative_name
            with closing(
                sqlite3.connect(
                    f"file:{path.resolve().as_posix()}?mode=ro",
                    uri=True,
                )
            ) as projection:
                metadata = {
                    str(key): str(value)
                    for key, value in projection.execute(
                        "SELECT key, value FROM metadata"
                    )
                }
                row_count = int(
                    projection.execute(
                        "SELECT COUNT(*) FROM projection_rows"
                    ).fetchone()[0]
                )
                review_rows = [
                    tuple(row)
                    for row in projection.execute(
                        """
                        SELECT
                            review_id,
                            fact_id,
                            review_status,
                            evidence_uri,
                            review_version
                        FROM projection_reviews
                        ORDER BY review_id
                        """
                    )
                ]
                artifact_digest = (
                    compute_projection_artifact_content_digest(
                        projection
                    )
                )
            run = projection_runs[projection_name]
            declared_source_hash = str(
                declared.get("sourceRevisionSetHash") or ""
            )
            declared_digest = str(
                declared.get("contentDigest") or ""
            )
            if (
                metadata.get("snapshot_build_id")
                != identity["buildId"]
                or metadata.get("snapshot_source_fingerprint")
                != identity["sourceSha256"]
                or metadata.get("source_revision_set_hash")
                != declared_source_hash
                or metadata.get("content_digest") != declared_digest
                or artifact_digest != declared_digest
                or run["projectionVersion"] != "v2"
                or run["sourceRevisionSetHash"]
                != declared_source_hash
                or run["ontologyVersion"]
                != identity["ontologyVersion"]
                or run["builtAt"] != identity["generatedAt"]
                or run["rowCount"] != row_count
                or run["validationStatus"] != "VALID"
            ):
                raise ValueError(
                    "domain projection is not bound to Core snapshot: "
                    + relative_name
                )
            core_digest = compute_core_projection_content_digest(
                core,
                projection_name=projection_name,
                fact_types=fact_types,
                ontology_version=identity["ontologyVersion"],
                matched_review_rows=review_rows,
            )
            if core_digest != declared_digest:
                raise ValueError(
                    "domain projection content differs from Core: "
                    + relative_name
                )
    finally:
        core.close()


def validate_sealed_snapshot_quality(
    *,
    snapshot_dir: Path,
    manifest: Mapping[str, object],
) -> None:
    """Validate the reports and cutover decision sealed into a snapshot."""

    build_id = _safe_build_id(manifest.get("buildId"))
    quality = manifest.get("qualityGates")
    cutover = manifest.get("cutover")
    if (
        not isinstance(quality, Mapping)
        or quality.get("sealedInSnapshotManifest") is not True
        or not isinstance(cutover, Mapping)
    ):
        raise ValueError("snapshot quality gates are not sealed")
    report_path = _staged_relative_path(
        snapshot_dir,
        quality.get("reportUri"),
        label="quality report",
    )
    benchmark_path = _staged_relative_path(
        snapshot_dir,
        quality.get("benchmarkUri"),
        label="benchmark report",
    )
    if (
        not report_path.is_file()
        or not benchmark_path.is_file()
        or str(quality.get("sha256") or "").lower()
        != _sha256_file(report_path)
        or str(quality.get("benchmarkSha256") or "").lower()
        != _sha256_file(benchmark_path)
    ):
        raise ValueError("sealed quality report hash is invalid")
    report = _read_json_object(
        report_path,
        label="sealed quality report",
    )
    benchmark = _read_json_object(
        benchmark_path,
        label="sealed benchmark report",
    )
    diagnostics = benchmark.get("diagnosticArtifacts")
    diagnostic_quality_keys = {
        "diagnosticsSchema",
        "caseResultsUri",
        "caseResultsSha256",
        "failureMatrixUri",
        "failureMatrixSha256",
        "diagnosticsBindingSha256",
    }
    declared_quality_keys = diagnostic_quality_keys & set(quality)
    if isinstance(diagnostics, Mapping) or declared_quality_keys:
        if (
            not isinstance(diagnostics, Mapping)
            or declared_quality_keys != diagnostic_quality_keys
            or quality.get("diagnosticsSchema")
            != QUERY_DIAGNOSTICS_SCHEMA
            or diagnostics.get("buildBinding")
            != "SNAPSHOT_METADATA"
        ):
            raise ValueError(
                "sealed query diagnostic binding is incomplete"
            )
        case_path = _staged_relative_path(
            snapshot_dir,
            quality.get("caseResultsUri"),
            label="query case results",
        )
        matrix_path = _staged_relative_path(
            snapshot_dir,
            quality.get("failureMatrixUri"),
            label="query failure matrix",
        )
        if (
            not case_path.is_file()
            or not matrix_path.is_file()
            or str(
                quality.get("caseResultsSha256") or ""
            ).lower()
            != _sha256_file(case_path)
            or str(
                quality.get("failureMatrixSha256") or ""
            ).lower()
            != _sha256_file(matrix_path)
        ):
            raise ValueError(
                "sealed query diagnostic report hash is invalid"
            )
        expected_binding_sha256 = (
            _query_diagnostics_binding_sha256(
                build_id=build_id,
                corpus_sha256=str(
                    diagnostics.get("corpusSha256") or ""
                ),
                quality_report_sha256=str(
                    quality.get("sha256") or ""
                ),
                benchmark_report_sha256=str(
                    quality.get("benchmarkSha256") or ""
                ),
                case_results_sha256=str(
                    quality.get("caseResultsSha256") or ""
                ),
                failure_matrix_sha256=str(
                    quality.get("failureMatrixSha256") or ""
                ),
            )
        )
        if (
            str(
                quality.get("diagnosticsBindingSha256") or ""
            ).lower()
            != expected_binding_sha256
        ):
            raise ValueError(
                "sealed query diagnostic report binding is invalid"
            )
        expected_case_bytes, expected_matrix_bytes = (
            query_diagnostic_artifact_bytes(
                benchmark,
                expected_build_id=build_id,
            )
        )
        if (
            case_path.read_bytes() != expected_case_bytes
            or matrix_path.read_bytes() != expected_matrix_bytes
        ):
            raise ValueError(
                "sealed query diagnostic artifact content is invalid"
            )
    summary = report.get("summary")
    gates = report.get("gates")
    if not isinstance(gates, list) or not gates:
        raise ValueError("sealed quality report has no gate results")
    gate_ids: set[str] = set()
    normalized_gates: list[Mapping[str, object]] = []
    for gate in gates:
        if not isinstance(gate, Mapping):
            raise ValueError("sealed quality report gate is invalid")
        gate_id = str(gate.get("id") or "")
        category = str(gate.get("category") or "")
        if (
            not gate_id
            or gate_id in gate_ids
            or not category
            or type(gate.get("critical")) is not bool
            or type(gate.get("passed")) is not bool
            or "target" not in gate
            or "actual" not in gate
            or not str(gate.get("detail") or "")
        ):
            raise ValueError("sealed quality report gate is invalid")
        gate_ids.add(gate_id)
        normalized_gates.append(gate)
    validate_quality_gate_contract(normalized_gates)
    passed_count = sum(bool(gate["passed"]) for gate in normalized_gates)
    failed_count = sum(
        bool(gate["critical"]) and not bool(gate["passed"])
        for gate in normalized_gates
    )
    eligible = failed_count == 0
    expected_recommendation = (
        "ready_for_default" if eligible else "keep_legacy_shadow"
    )
    if (
        str(report.get("buildId") or "") != build_id
        or str(report.get("schema") or "") != QUALITY_GATE_SCHEMA
        or str(quality.get("schema") or "") != QUALITY_GATE_SCHEMA
        or str(benchmark.get("schema") or "") != BENCHMARK_SCHEMA
        or not isinstance(summary, Mapping)
        or not isinstance(report.get("benchmark"), Mapping)
        or benchmark != report.get("benchmark")
        or int(summary.get("total") or 0) != len(normalized_gates)
        or int(summary.get("passed") or 0) != passed_count
        or int(summary.get("failed") or 0) != failed_count
        or bool(summary.get("cutoverEligible")) != eligible
        or str(summary.get("recommendation") or "")
        != expected_recommendation
        or int(summary.get("passed") or 0)
        != int(quality.get("passed") or 0)
        or int(summary.get("failed") or 0)
        != int(quality.get("failed") or 0)
        or bool(summary.get("cutoverEligible"))
        != bool(quality.get("cutoverEligible"))
    ):
        raise ValueError("sealed quality report identity is invalid")
    if (
        str(cutover.get("mode") or "")
        != ("ready" if eligible else "shadow")
        or str(cutover.get("defaultQuerySource") or "")
        != ("vnext" if eligible else "legacy")
    ):
        raise ValueError("snapshot cutover state contradicts quality gates")


def _validate_staged_snapshot_for_promotion(
    *,
    staging: Path,
    manifest: Mapping[str, object],
) -> None:
    """Re-verify the immutable publication boundary before pointer swap."""

    identity = validate_snapshot_source_identity(manifest)
    build_id = identity["buildId"]
    generated_at = identity["generatedAt"]
    source_sha256 = identity["sourceSha256"]
    discovery_sha256 = identity["discoverySha256"]
    ontology_version = identity["ontologyVersion"]

    databases = manifest.get("databases")
    if not isinstance(databases, Mapping):
        raise ValueError("snapshot database manifest is missing")
    exports_root = staging / "domain_exports"
    if not exports_root.is_dir():
        raise ValueError("staged snapshot is missing domain_exports")
    export_names = {
        path.relative_to(staging).as_posix()
        for path in exports_root.rglob("*.sqlite")
        if path.is_file()
    }
    expected_exports = {
        f"domain_exports/{name}.sqlite"
        for name in DOMAIN_PROJECTIONS
    }
    expected_names = {*DATABASE_NAMES, *expected_exports}
    if (
        set(databases) != expected_names
        or export_names != expected_exports
    ):
        raise ValueError(
            "snapshot database manifest does not exactly cover artifacts"
        )
    main_metadata = {
        "catalog.sqlite": {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "source_fingerprint": discovery_sha256,
            "generated_at": generated_at,
            "snapshot_build_id": build_id,
            "snapshot_source_fingerprint": source_sha256,
        },
        "core.sqlite": {
            "schema_version": CORE_SCHEMA_VERSION,
            "source_fingerprint": discovery_sha256,
            "generated_at": generated_at,
            "snapshot_build_id": build_id,
            "snapshot_source_fingerprint": source_sha256,
        },
        "search.sqlite": {
            "schema_version": SEARCH_SCHEMA_VERSION,
            "source_fingerprint": source_sha256,
            "generated_at": generated_at,
            "snapshot_build_id": build_id,
            "snapshot_source_fingerprint": source_sha256,
        },
        "cache.sqlite": {
            "schema_version": CACHE_SCHEMA_VERSION,
            "source_fingerprint": source_sha256,
            "generated_at": generated_at,
            "snapshot_build_id": build_id,
            "snapshot_source_fingerprint": source_sha256,
        },
    }
    validated_main_metadata: dict[str, dict[str, str]] = {}
    for relative_name in DATABASE_NAMES:
        declared = databases.get(relative_name)
        if not isinstance(declared, Mapping):
            raise ValueError(
                f"database metrics are missing: {relative_name}"
            )
        validated_main_metadata[relative_name] = _validate_staged_database(
            staging=staging,
            relative_name=relative_name,
            declared=declared,
            build_id=build_id,
            source_sha256=source_sha256,
            require_snapshot_identity=True,
            expected_metadata=main_metadata[relative_name],
        )
    active_stale_sources = validate_snapshot_runtime_health_summary(
        manifest=manifest,
        core_metadata=validated_main_metadata["core.sqlite"],
    )
    for relative_name in sorted(expected_exports):
        declared = databases.get(relative_name)
        if not isinstance(declared, Mapping):
            raise ValueError(
                f"database metrics are missing: {relative_name}"
            )
        projection_name = Path(relative_name).stem
        content_digest = str(declared.get("contentDigest") or "").lower()
        review_config_sha256 = str(
            declared.get("reviewConfigSha256") or ""
        ).lower()
        source_revision_set_hash = str(
            declared.get("sourceRevisionSetHash") or ""
        ).lower()
        if (
            str(declared.get("schemaVersion") or "")
            != PROJECTION_SCHEMA_VERSION
            or str(declared.get("projectionVersion") or "") != "v2"
            or str(declared.get("ontologyVersion") or "")
            != ontology_version
            or str(declared.get("validationStatus") or "") != "VALID"
            or not _is_sha256(content_digest)
            or not _is_sha256(review_config_sha256)
            or not _is_sha256(source_revision_set_hash)
        ):
            raise ValueError(
                "domain projection declaration is invalid: "
                + relative_name
            )
        metadata = _validate_staged_database(
            staging=staging,
            relative_name=relative_name,
            declared=declared,
            build_id=build_id,
            source_sha256=source_sha256,
            require_snapshot_identity=False,
            expected_metadata={
                "schema_version": PROJECTION_SCHEMA_VERSION,
                "projection_name": projection_name,
                "projection_version": "v2",
                "source_revision_set_hash": source_revision_set_hash,
                "ontology_version": ontology_version,
                "built_at": generated_at,
                "truth_source": "core.sqlite",
                "review_config_sha256": review_config_sha256,
                "content_digest": content_digest,
                "snapshot_build_id": build_id,
                "snapshot_source_fingerprint": source_sha256,
            },
        )
        artifact_path = _staged_relative_path(
            staging,
            relative_name,
            label="domain projection",
        )
        with closing(
            sqlite3.connect(
                f"file:{artifact_path.as_posix()}?mode=ro",
                uri=True,
            )
        ) as projection:
            computed_content_digest = (
                compute_projection_artifact_content_digest(projection)
            )
        if (
            metadata.get("content_digest")
            != computed_content_digest
        ):
            raise ValueError(
                "domain projection content digest is invalid: "
                + relative_name
            )

    validate_snapshot_journal_safety(
        staging,
        require_delete=True,
    )
    validate_snapshot_database_schemas(staging)
    validate_snapshot_projection_bindings(
        snapshot_dir=staging,
        manifest=manifest,
    )
    validate_sealed_snapshot_quality(
        snapshot_dir=staging,
        manifest=manifest,
    )
    quality = manifest.get("qualityGates")
    cutover = manifest.get("cutover")
    if (
        active_stale_sources > 0
        and isinstance(quality, Mapping)
        and isinstance(cutover, Mapping)
        and (
            bool(quality.get("cutoverEligible"))
            or str(cutover.get("mode") or "") == "ready"
            or str(cutover.get("defaultQuerySource") or "") == "vnext"
        )
    ):
        raise ValueError(
            "snapshot with active stale sources cannot be promoted as ready"
        )


def _promote_snapshot(
    *,
    staging: Path,
    output_dir: Path,
    manifest: dict[str, object],
) -> None:
    build_id = _safe_build_id(manifest.get("buildId"))
    if manifest.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("snapshot manifest schema is unknown")
    if not staging.is_dir():
        raise FileNotFoundError(staging)

    snapshots = output_dir / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    destination = snapshots / build_id
    if destination.exists():
        raise FileExistsError(
            f"immutable snapshot already exists: {destination}"
        )
    _validate_staged_snapshot_for_promotion(
        staging=staging,
        manifest=manifest,
    )
    _write_json(staging / "manifest.json", manifest)

    # A same-volume directory rename publishes every immutable artifact before
    # the only reader-visible mutation: replacing the small current pointer.
    os.replace(staging, destination)

    manifests = output_dir / "manifests"
    _write_text(
        manifests / "catalog_schema.sql",
        FULL_CATALOG_SCHEMA_SQL,
    )
    _write_text(manifests / "core_schema.sql", FULL_CORE_SCHEMA_SQL)
    _write_text(manifests / "search_schema.sql", SEARCH_SCHEMA_SQL)
    _write_text(manifests / "cache_schema.sql", CACHE_SCHEMA_SQL)
    _write_current_pointer(output_dir, build_id)


def build_vnext_snapshot(
    *,
    project_root: Path,
    discovery_database: Path,
    legacy_kb_root: Path,
    capture_root: Path,
    native_root: Path,
    output_dir: Path,
    full_snapshot: bool = False,
    generated_at: str | None = None,
    map_evidence_path: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, object]:
    """Build all four stores in staging, validate, then atomically promote."""

    project_root = project_root.resolve()
    discovery_database = discovery_database.resolve()
    legacy_kb_root = legacy_kb_root.resolve()
    capture_root = capture_root.resolve()
    native_root = native_root.resolve()
    runtime_root = (
        runtime_root.resolve()
        if runtime_root is not None
        else (project_root / "runtime_observations").resolve()
    )
    map_evidence_path = (
        map_evidence_path.resolve()
        if map_evidence_path is not None
        else None
    )
    output_dir = output_dir.resolve()
    if not full_snapshot:
        raise ValueError("--full-snapshot is required for the first vNext build")
    if not discovery_database.is_file():
        raise FileNotFoundError(discovery_database)
    _validate_output_root(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = output_dir / ".build"
    work_root.mkdir(parents=True, exist_ok=True)
    if generated_at is None:
        generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    generated_at = normalize_snapshot_generated_at(generated_at)
    benchmark_gold_set_path = (
        project_root / "tests" / "fixtures" / "kb_query_gold_set.v1.json"
    )
    semantic_input_hashes = _snapshot_semantic_input_hashes(
        project_root=project_root,
        discovery_database=discovery_database,
        legacy_kb_root=legacy_kb_root,
        capture_root=capture_root,
        native_root=native_root,
        map_evidence_path=map_evidence_path,
    )
    source_manifest = scan_source_manifest(
        semantic_input_hashes=semantic_input_hashes,
        capture_root=capture_root,
        native_root=native_root,
        runtime_root=runtime_root,
        generated_at=generated_at,
    )
    discovery_sha = semantic_input_hashes["discovery"]
    semantic_inputs_sha = semantic_inputs_sha256(
        semantic_input_hashes
    )
    build_id = snapshot_build_id(
        generated_at,
        semantic_inputs_sha,
    )
    staging = Path(
        tempfile.mkdtemp(prefix=f"{build_id}.", dir=work_root)
    )
    ontology = load_ontology(project_root / "ontology")
    try:
        catalog_counts = build_catalog_database(
            discovery_path=discovery_database,
            output_path=staging / "catalog.sqlite",
            source_fingerprint=discovery_sha,
            generated_at=generated_at,
            snapshot_build_id=build_id,
            snapshot_source_fingerprint=semantic_inputs_sha,
        )
        core_counts = build_core_database(
            discovery_path=discovery_database,
            capture_root=capture_root,
            output_path=staging / "core.sqlite",
            source_fingerprint=discovery_sha,
            generated_at=generated_at,
            ontology=ontology,
            legacy_kb_root=legacy_kb_root,
            native_gold_set_path=(
                project_root / "ontology" / "native_gold_set.v1.json"
            ),
            benchmark_gold_set_path=benchmark_gold_set_path,
            projection_review_path=(
                project_root / "ontology" / "projection_review.v1.json"
            ),
            map_evidence_path=map_evidence_path,
            native_root=native_root,
            snapshot_build_id=build_id,
            snapshot_source_fingerprint=semantic_inputs_sha,
        )
        runtime_health_summary = _seal_runtime_health_summary(
            core_path=staging / "core.sqlite",
            build_id=build_id,
            source_sha256=semantic_inputs_sha,
        )
        projection_counts = build_domain_projections(
            core_path=staging / "core.sqlite",
            output_dir=staging / "domain_exports",
            generated_at=generated_at,
            ontology_version=ontology.version,
            review_path=(
                project_root / "ontology" / "projection_review.v1.json"
            ),
            snapshot_build_id=build_id,
            snapshot_source_fingerprint=semantic_inputs_sha,
        )
        search_counts = build_search_database(
            core_path=staging / "core.sqlite",
            output_path=staging / "search.sqlite",
            source_fingerprint=semantic_inputs_sha,
            generated_at=generated_at,
            snapshot_build_id=build_id,
            snapshot_source_fingerprint=semantic_inputs_sha,
        )
        cache_counts = build_cache_database(
            output_path=staging / "cache.sqlite",
            source_fingerprint=semantic_inputs_sha,
            generated_at=generated_at,
            snapshot_build_id=build_id,
            snapshot_source_fingerprint=semantic_inputs_sha,
        )
        _finalize_staged_database_journals(staging)
        metrics = {
            name: database_metrics(staging / name)
            for name in DATABASE_NAMES
        }
        projection_metrics = {
            f"domain_exports/{value['path']}": {
                "bytes": value["bytes"],
                "sha256": value["sha256"],
                "integrity": value["integrity"],
                "foreignKeyViolations": value["foreignKeyViolations"],
                "schemaVersion": value["schemaVersion"],
                "projectionVersion": value["projectionVersion"],
                "ontologyVersion": value["ontologyVersion"],
                "contentDigest": value["contentDigest"],
                "reviewConfigSha256": value["reviewConfigSha256"],
                "sourceRevisionSetHash": value[
                    "sourceRevisionSetHash"
                ],
                "validationStatus": value["validationStatus"],
                "tableCounts": value["tableCounts"],
            }
            for value in projection_counts.values()
        }
        published_metrics = {**metrics, **projection_metrics}
        failures = {
            name: value
            for name, value in published_metrics.items()
            if value["integrity"] != "ok"
            or int(value["foreignKeyViolations"]) != 0
        }
        if failures:
            raise ValueError(f"vNext database validation failed: {failures}")
        manifest: dict[str, object] = {
            "schema": SNAPSHOT_SCHEMA,
            "buildId": build_id,
            "generatedAt": generated_at,
            "source": {
                "kind": SNAPSHOT_SOURCE_KIND,
                "uri": SNAPSHOT_SOURCE_URI,
                "sha256": semantic_inputs_sha,
                "inputs": semantic_input_hashes,
            },
            "ontologyVersion": ontology.version,
            "counts": {
                "catalog": catalog_counts,
                "core": core_counts,
                "search": search_counts,
                "cache": cache_counts,
                "domainProjections": projection_counts,
            },
            "databases": published_metrics,
            "runtimeHealth": runtime_health_summary,
            "cutover": {
                "mode": "shadow",
                "defaultQuerySource": "legacy",
                "reason": "quality gates have not run yet",
            },
            "incrementalUpdate": source_manifest_binding(source_manifest),
        }
        _write_json(staging / "manifest.json", manifest)
        quality_report = _evaluate_staged_quality_gates(
            project_root=project_root,
            staging=staging,
            discovery_database=discovery_database,
            generated_at=generated_at,
            allow_unsealed_snapshot=True,
        )
        manifest = _seal_staged_quality_report(
            staging=staging,
            manifest=manifest,
            report=quality_report,
        )
        # The storage benchmark requires a strictly sealed immutable candidate.
        # Re-evaluate after the provisional seal, then replace it atomically.
        quality_report = _evaluate_staged_quality_gates(
            project_root=project_root,
            staging=staging,
            discovery_database=discovery_database,
            generated_at=generated_at,
        )
        manifest = _seal_staged_quality_report(
            staging=staging,
            manifest=manifest,
            report=quality_report,
        )
        final_input_hashes = _snapshot_semantic_input_hashes(
            project_root=project_root,
            discovery_database=discovery_database,
            legacy_kb_root=legacy_kb_root,
            capture_root=capture_root,
            native_root=native_root,
            map_evidence_path=map_evidence_path,
        )
        if final_input_hashes != semantic_input_hashes:
            changed_inputs = sorted(
                key
                for key in SNAPSHOT_SEMANTIC_INPUT_KEYS
                if final_input_hashes.get(key)
                != semantic_input_hashes.get(key)
            )
            raise RuntimeError(
                "Snapshot semantic inputs changed during build: "
                + ", ".join(changed_inputs)
            )
        final_source_manifest = scan_source_manifest(
            semantic_input_hashes=final_input_hashes,
            capture_root=capture_root,
            native_root=native_root,
            runtime_root=runtime_root,
            generated_at=generated_at,
        )
        if final_source_manifest.fingerprint != source_manifest.fingerprint:
            changed_sources = compare_source_manifests(
                source_manifest,
                final_source_manifest,
            )
            changed_uris = sorted(
                {
                    (
                        change.current.source_uri
                        if change.current is not None
                        else change.previous.source_uri
                    )
                    for change in changed_sources.all_changes
                    if change.current is not None
                    or change.previous is not None
                }
            )
            raise RuntimeError(
                "Snapshot source manifest changed during build: "
                + ", ".join(changed_uris)
            )
        _promote_snapshot(
            staging=staging,
            output_dir=output_dir,
            manifest=manifest,
        )
        return {
            "status": "complete",
            "buildId": build_id,
            "output": str(output_dir),
            "sourceSha256": semantic_inputs_sha,
            "discoverySha256": discovery_sha,
            "sourceManifestFingerprint": source_manifest.fingerprint,
            "counts": manifest["counts"],
            "databases": published_metrics,
            "cutover": manifest["cutover"],
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)
