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
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from .class_hierarchy import class_hierarchy_contract_fingerprint
from .ontology import load_ontology
from .projections import build_domain_projections
from .storage import (
    CACHE_SCHEMA_SQL,
    FULL_CATALOG_SCHEMA_SQL,
    FULL_CORE_SCHEMA_SQL,
    SEARCH_SCHEMA_SQL,
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
SNAPSHOT_SOURCE_KIND = "semantic_input_set"
SNAPSHOT_SOURCE_URI = "kb-inputs://ark/vnext"
SEMANTIC_PRODUCER_CONTRACT_SCHEMA = (
    "ark-kb-semantic-producer-contract/v1"
)
SNAPSHOT_SEMANTIC_INPUT_KEYS = frozenset(
    {
        "discovery",
        "captures",
        "classHierarchyContract",
        "semanticProducerContract",
        "legacy",
        "ontology",
        "benchmarkGold",
        "qualityGold",
        "mapEvidence",
    }
)
_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
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
    }
    if set(hashes) != SNAPSHOT_SEMANTIC_INPUT_KEYS:
        raise AssertionError("snapshot semantic input registry is incomplete")
    return hashes


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _validate_output_root(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    existing = [item for item in output_dir.iterdir() if item.name != ".build"]
    if not existing:
        return
    marker = output_dir / "manifests" / "current.json"
    if not marker.is_file():
        raise ValueError(
            f"Refusing to modify non-vNext directory without manifest: {output_dir}"
        )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError(f"Unknown vNext manifest schema in {output_dir}")


def _promote_snapshot(
    *,
    staging: Path,
    output_dir: Path,
    manifest: dict[str, object],
) -> None:
    manifests = output_dir / "manifests"
    snapshots = output_dir / "snapshots"
    domain_exports = output_dir / "domain_exports"
    manifests.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)
    domain_exports.mkdir(parents=True, exist_ok=True)
    current = manifests / "current.json"
    if current.is_file():
        previous = json.loads(current.read_text(encoding="utf-8"))
        previous_id = str(previous.get("buildId") or "unknown")
        archive = snapshots / previous_id
        archive.mkdir(parents=True, exist_ok=True)
        for name in DATABASE_NAMES:
            source = output_dir / name
            if source.is_file():
                os.replace(source, archive / name)
        previous_exports = output_dir / "domain_exports"
        if previous_exports.is_dir():
            os.replace(previous_exports, archive / "domain_exports")
            domain_exports.mkdir(parents=True, exist_ok=True)
        _write_json(archive / "manifest.json", previous)
    for name in DATABASE_NAMES:
        os.replace(staging / name, output_dir / name)
    staged_exports = staging / "domain_exports"
    if domain_exports.exists():
        shutil.rmtree(domain_exports)
    os.replace(staged_exports, domain_exports)
    build_id = str(manifest["buildId"])
    _write_json(manifests / f"{build_id}.json", manifest)
    _write_json(current, manifest)
    _write_text(
        manifests / "catalog_schema.sql",
        FULL_CATALOG_SCHEMA_SQL,
    )
    _write_text(manifests / "core_schema.sql", FULL_CORE_SCHEMA_SQL)
    _write_text(manifests / "search_schema.sql", SEARCH_SCHEMA_SQL)
    _write_text(manifests / "cache_schema.sql", CACHE_SCHEMA_SQL)


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
) -> dict[str, object]:
    """Build all four stores in staging, validate, then atomically promote."""

    project_root = project_root.resolve()
    discovery_database = discovery_database.resolve()
    legacy_kb_root = legacy_kb_root.resolve()
    capture_root = capture_root.resolve()
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
        map_evidence_path=map_evidence_path,
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
            snapshot_build_id=build_id,
            snapshot_source_fingerprint=semantic_inputs_sha,
        )
        projection_counts = build_domain_projections(
            core_path=staging / "core.sqlite",
            output_dir=staging / "domain_exports",
            generated_at=generated_at,
            ontology_version=ontology.version,
            review_path=(
                project_root / "ontology" / "projection_review.v1.json"
            ),
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
            "cutover": {
                "mode": "shadow",
                "defaultQuerySource": "legacy",
                "reason": "quality gates have not run yet",
            },
        }
        final_input_hashes = _snapshot_semantic_input_hashes(
            project_root=project_root,
            discovery_database=discovery_database,
            legacy_kb_root=legacy_kb_root,
            capture_root=capture_root,
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
            "counts": manifest["counts"],
            "databases": published_metrics,
            "cutover": manifest["cutover"],
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)
