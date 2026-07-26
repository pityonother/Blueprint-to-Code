"""Build a sanitized, resumable ARK knowledge-base discovery bundle.

The discovery database is deliberately separate from the existing knowledge
databases.  It inventories local sources, preserves unknown and gap states, and
exports only derived metadata.  It never copies ARK packages, game binaries,
PDBs, Ghidra projects, full captures, or decompiler bodies into the bundle.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import zipfile
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Mapping, Sequence

from .asset_ledger import metadata_fingerprint
from .evidence_values import project_default_value
from .uasset_graphs import (
    parse_uasset_exports,
    parse_uasset_imports,
    parse_uasset_name_map,
    parse_uasset_soft_object_paths,
    parse_uasset_summary,
)


DISCOVERY_SCHEMA = "blueprint-to-code-kb-discovery/v1"
STATE_SCHEMA = "blueprint-to-code-kb-discovery-state/v1"
ARCHIVE_ROOT = "discovery_bundle"
TOOL_VERSION = "1.1.0"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"


def _extractor_cache_source_paths() -> tuple[Path, ...]:
    module_root = Path(__file__).resolve().parent
    return tuple(
        path
        for path in (
            Path(__file__).resolve(),
            module_root / "asset_ledger.py",
            module_root / "evidence_values.py",
            module_root / "uasset_graphs.py",
        )
        if path.is_file()
    )


def _compute_extractor_cache_token(
    source_paths: Sequence[Path] | None = None,
) -> str:
    digest = hashlib.sha256()
    digest.update(TOOL_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(DISCOVERY_SCHEMA.encode("utf-8"))
    paths = source_paths or _extractor_cache_source_paths()
    for path in sorted(paths, key=lambda value: value.name.casefold()):
        digest.update(b"\0")
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


EXTRACTOR_CACHE_TOKEN = _compute_extractor_cache_token()

FORBIDDEN_ARCHIVE_SUFFIXES = {
    ".uasset",
    ".uexp",
    ".ubulk",
    ".dll",
    ".pdb",
    ".gpr",
    ".rep",
}
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]"),
    re.compile(r"\\\\[^\\/\s]+[\\/]"),
    re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users|root|private|Volumes)/"),
)
SECRET_PATTERNS = (
    re.compile(r"(?i)\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
)


DISCOVERY_SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE assets (
    object_path TEXT PRIMARY KEY,
    package_path TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    asset_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    blueprint_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    generated_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    parent_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    native_parent_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    mount_point TEXT NOT NULL,
    top_folder TEXT NOT NULL,
    plugin_or_dlc TEXT NOT NULL,
    is_blueprint INTEGER DEFAULT NULL,
    is_data_only_blueprint INTEGER DEFAULT NULL,
    is_map INTEGER NOT NULL DEFAULT 0,
    is_data_asset INTEGER DEFAULT NULL,
    is_data_table INTEGER DEFAULT NULL,
    is_function_library INTEGER DEFAULT NULL,
    is_blueprint_interface INTEGER DEFAULT NULL,
    is_user_defined_struct INTEGER DEFAULT NULL,
    is_user_defined_enum INTEGER DEFAULT NULL,
    is_editor_only INTEGER DEFAULT NULL,
    has_uasset INTEGER NOT NULL DEFAULT 0,
    has_uexp INTEGER NOT NULL DEFAULT 0,
    has_ubulk INTEGER NOT NULL DEFAULT 0,
    file_size_total INTEGER NOT NULL DEFAULT 0,
    source_fingerprint TEXT NOT NULL,
    source_modified TEXT NOT NULL,
    capture_exists INTEGER NOT NULL DEFAULT 0,
    evidence_revision TEXT NOT NULL DEFAULT '',
    evidence_freshness TEXT NOT NULL DEFAULT 'NOT_AVAILABLE',
    parse_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    parse_confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
    graph_count INTEGER NOT NULL DEFAULT 0,
    function_count INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    macro_count INTEGER NOT NULL DEFAULT 0,
    variable_count INTEGER NOT NULL DEFAULT 0,
    component_count INTEGER NOT NULL DEFAULT 0,
    default_property_count INTEGER NOT NULL DEFAULT 0,
    dependency_count INTEGER NOT NULL DEFAULT 0,
    referencer_count INTEGER NOT NULL DEFAULT 0,
    hard_referencer_count INTEGER NOT NULL DEFAULT 0,
    soft_referencer_count INTEGER NOT NULL DEFAULT 0,
    direct_child_count INTEGER NOT NULL DEFAULT 0,
    descendant_count INTEGER NOT NULL DEFAULT 0,
    implemented_by_count INTEGER NOT NULL DEFAULT 0,
    map_usage_count INTEGER NOT NULL DEFAULT 0,
    registry_usage_count INTEGER NOT NULL DEFAULT 0,
    cross_domain_reference_count INTEGER NOT NULL DEFAULT 0,
    component_reuse_count INTEGER NOT NULL DEFAULT 0,
    native_call_count INTEGER NOT NULL DEFAULT 0,
    unresolved_native_call_count INTEGER NOT NULL DEFAULT 0,
    query_hit_count INTEGER DEFAULT NULL,
    existing_report_count INTEGER DEFAULT NULL,
    query_hit_status TEXT NOT NULL DEFAULT 'NOT_MEASURED',
    existing_report_status TEXT NOT NULL DEFAULT 'NOT_MEASURED',
    estimated_deep_read_cost INTEGER NOT NULL DEFAULT 0,
    provisional_tier INTEGER NOT NULL DEFAULT 0,
    provisional_reasons_json TEXT NOT NULL DEFAULT '[]',
    identity_source_kind TEXT NOT NULL DEFAULT 'filesystem_metadata',
    identity_confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
    identity_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    relative_logical_path TEXT NOT NULL,
    file_extension TEXT NOT NULL
);

CREATE TABLE class_edges (
    child_class_path TEXT NOT NULL,
    parent_class_path TEXT NOT NULL,
    edge_kind TEXT NOT NULL,
    inheritance_depth INTEGER NOT NULL DEFAULT 1,
    source_kind TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY (child_class_path, parent_class_path, edge_kind)
);

CREATE TABLE interfaces (
    owner_object_path TEXT NOT NULL,
    interface_class_path TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY (owner_object_path, interface_class_path, source_kind)
);

CREATE TABLE components (
    owner_object_path TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_class_path TEXT NOT NULL,
    component_object_path TEXT NOT NULL,
    is_inherited INTEGER NOT NULL DEFAULT 0,
    source_property TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    PRIMARY KEY (owner_object_path, component_name, component_class_path, source_kind)
);

CREATE TABLE asset_references (
    reference_id TEXT PRIMARY KEY,
    source_object_path TEXT NOT NULL,
    target_object_path TEXT NOT NULL,
    edge_kind TEXT NOT NULL,
    reference_strength TEXT NOT NULL,
    source_property TEXT NOT NULL,
    source_graph TEXT NOT NULL,
    source_function TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'UNKNOWN'
);

CREATE TABLE graphs (
    asset_object_path TEXT NOT NULL,
    graph_evidence_id TEXT PRIMARY KEY,
    graph_name TEXT NOT NULL,
    graph_type TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    node_count INTEGER NOT NULL DEFAULT 0,
    pin_count INTEGER NOT NULL DEFAULT 0,
    wire_count INTEGER NOT NULL DEFAULT 0,
    native_call_count INTEGER NOT NULL DEFAULT 0,
    external_asset_reference_count INTEGER NOT NULL DEFAULT 0,
    gap_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE blueprint_functions (
    function_id TEXT PRIMARY KEY,
    asset_object_path TEXT NOT NULL,
    function_name TEXT NOT NULL,
    function_kind TEXT NOT NULL,
    graph_evidence_id TEXT NOT NULL,
    replication_kind TEXT NOT NULL,
    is_pure INTEGER NOT NULL DEFAULT 0,
    is_override INTEGER NOT NULL DEFAULT 0,
    declaring_class_path TEXT NOT NULL,
    call_count_out INTEGER NOT NULL DEFAULT 0,
    call_count_in INTEGER NOT NULL DEFAULT 0,
    native_boundary TEXT NOT NULL,
    confidence TEXT NOT NULL,
    measurement_status TEXT NOT NULL DEFAULT 'PARTIAL'
);

CREATE TABLE default_property_surface (
    surface_id TEXT PRIMARY KEY,
    asset_object_path TEXT NOT NULL,
    property_name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    declaring_class_path TEXT NOT NULL,
    has_value INTEGER NOT NULL DEFAULT 0,
    value_status TEXT NOT NULL,
    value_fingerprint TEXT NOT NULL,
    is_object_reference INTEGER NOT NULL DEFAULT 0,
    is_array INTEGER NOT NULL DEFAULT 0,
    is_map INTEGER NOT NULL DEFAULT 0,
    is_struct INTEGER NOT NULL DEFAULT 0,
    source_evidence_id TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'UNKNOWN'
);

CREATE TABLE system_registrations (
    registration_id TEXT PRIMARY KEY,
    owner_object_path TEXT NOT NULL,
    registration_type TEXT NOT NULL,
    target_object_path TEXT NOT NULL,
    source_property TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'existing_knowledge_database'
);

CREATE TABLE native_symbols (
    native_evidence_id TEXT PRIMARY KEY,
    module_name TEXT NOT NULL,
    binary_sha256 TEXT NOT NULL,
    pdb_sha256 TEXT NOT NULL,
    pdb_guid_age TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    simple_name TEXT NOT NULL,
    owner_class TEXT NOT NULL,
    signature TEXT NOT NULL,
    rva TEXT NOT NULL,
    symbol_source TEXT NOT NULL,
    pdb_loaded INTEGER NOT NULL DEFAULT 0,
    decompile_status TEXT NOT NULL,
    caller_count INTEGER NOT NULL DEFAULT 0,
    callee_count INTEGER NOT NULL DEFAULT 0,
    field_access_count INTEGER NOT NULL DEFAULT 0,
    called_by_blueprint_count INTEGER NOT NULL DEFAULT 0,
    confidence TEXT NOT NULL,
    recipe_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_set_ids_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE blueprint_native_edges (
    edge_id TEXT PRIMARY KEY,
    blueprint_asset_path TEXT NOT NULL,
    blueprint_graph_evidence_id TEXT NOT NULL,
    blueprint_function_name TEXT NOT NULL,
    native_evidence_id TEXT NOT NULL,
    resolution_method TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNRESOLVED'
);

CREATE TABLE native_field_accesses (
    access_id TEXT PRIMARY KEY,
    native_evidence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_offset TEXT NOT NULL,
    access_kind TEXT NOT NULL,
    containing_type TEXT NOT NULL,
    source_instruction_or_slice_id TEXT NOT NULL,
    confidence TEXT NOT NULL
);

CREATE TABLE coverage (
    object_path TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    heuristic_count INTEGER NOT NULL DEFAULT 0,
    ambiguous_count INTEGER NOT NULL DEFAULT 0,
    not_recovered_count INTEGER NOT NULL DEFAULT 0,
    source_not_available_count INTEGER NOT NULL DEFAULT 0,
    stale_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    PRIMARY KEY (object_path, stage)
);

CREATE TABLE existing_knowledge_tables (
    database_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    distinct_asset_count INTEGER NOT NULL,
    source_asset_count INTEGER NOT NULL,
    stale_row_count INTEGER NOT NULL,
    duplicate_key_count INTEGER NOT NULL,
    distinct_asset_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    stale_count_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    duplicate_count_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    PRIMARY KEY (database_name, table_name)
);

CREATE TABLE query_corpus (
    query_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    source TEXT NOT NULL,
    target_audience TEXT NOT NULL,
    expected_answer_type TEXT NOT NULL,
    primary_domain TEXT NOT NULL,
    secondary_domains_json TEXT NOT NULL,
    requires_blueprint INTEGER NOT NULL DEFAULT 0,
    requires_defaults INTEGER NOT NULL DEFAULT 0,
    requires_references INTEGER NOT NULL DEFAULT 0,
    requires_map_evidence INTEGER NOT NULL DEFAULT 0,
    requires_native INTEGER NOT NULL DEFAULT 0,
    requires_runtime_validation INTEGER NOT NULL DEFAULT 0,
    existing_report_path TEXT NOT NULL
);

CREATE TABLE source_inventory (
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    generated_at TEXT NOT NULL,
    limitations_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE scan_failures (
    failure_id TEXT PRIMARY KEY,
    object_path TEXT NOT NULL,
    stage TEXT NOT NULL,
    error_code TEXT NOT NULL,
    status TEXT NOT NULL,
    detail_redacted TEXT NOT NULL
);

CREATE TABLE native_gap_summary (
    evidence_set_id TEXT NOT NULL,
    recipe_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    gap_count INTEGER NOT NULL,
    next_probe TEXT NOT NULL,
    PRIMARY KEY (evidence_set_id, status, reason_code)
);

CREATE TABLE sample_membership (
    object_path TEXT NOT NULL,
    selection_reason TEXT NOT NULL,
    source_rank INTEGER NOT NULL,
    PRIMARY KEY (object_path, selection_reason)
);

CREATE INDEX idx_assets_class ON assets(asset_class_path);
CREATE INDEX idx_assets_generated_class ON assets(generated_class_path);
CREATE INDEX idx_assets_parent ON assets(parent_class_path);
CREATE INDEX idx_assets_native_parent ON assets(native_parent_class_path);
CREATE INDEX idx_assets_tier ON assets(provisional_tier);
CREATE INDEX idx_assets_referencer ON assets(referencer_count DESC);
CREATE INDEX idx_assets_descendant ON assets(descendant_count DESC);
CREATE INDEX idx_class_edges_child ON class_edges(child_class_path);
CREATE INDEX idx_class_edges_parent ON class_edges(parent_class_path);
CREATE INDEX idx_interfaces_owner ON interfaces(owner_object_path);
CREATE INDEX idx_interfaces_class ON interfaces(interface_class_path);
CREATE INDEX idx_components_owner ON components(owner_object_path);
CREATE INDEX idx_components_class ON components(component_class_path);
CREATE INDEX idx_asset_refs_source ON asset_references(source_object_path);
CREATE INDEX idx_asset_refs_target ON asset_references(target_object_path);
CREATE INDEX idx_asset_refs_kind ON asset_references(edge_kind);
CREATE INDEX idx_asset_refs_target_kind ON asset_references(target_object_path, edge_kind);
CREATE INDEX idx_graphs_asset ON graphs(asset_object_path);
CREATE INDEX idx_functions_asset ON blueprint_functions(asset_object_path);
CREATE INDEX idx_functions_name ON blueprint_functions(function_name);
CREATE INDEX idx_defaults_asset ON default_property_surface(asset_object_path);
CREATE INDEX idx_registrations_owner ON system_registrations(owner_object_path);
CREATE INDEX idx_registrations_target ON system_registrations(target_object_path);
CREATE INDEX idx_registrations_type ON system_registrations(registration_type);
CREATE INDEX idx_native_qualified ON native_symbols(qualified_name);
CREATE INDEX idx_native_simple ON native_symbols(simple_name);
CREATE INDEX idx_native_owner ON native_symbols(owner_class);
CREATE INDEX idx_bp_native_asset ON blueprint_native_edges(blueprint_asset_path);
CREATE INDEX idx_bp_native_symbol ON blueprint_native_edges(native_evidence_id);
CREATE INDEX idx_native_fields_symbol ON native_field_accesses(native_evidence_id);
CREATE INDEX idx_coverage_status ON coverage(stage, status);
CREATE INDEX idx_query_domain ON query_corpus(primary_domain);
"""


STATE_SCHEMA_SQL = r"""
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS state_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_files (
    object_path TEXT PRIMARY KEY,
    package_path TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    local_path TEXT NOT NULL,
    file_extension TEXT NOT NULL,
    has_uasset INTEGER NOT NULL,
    has_uexp INTEGER NOT NULL,
    has_ubulk INTEGER NOT NULL,
    uasset_size INTEGER NOT NULL,
    uexp_size INTEGER NOT NULL,
    ubulk_size INTEGER NOT NULL,
    file_size_total INTEGER NOT NULL,
    source_fingerprint TEXT NOT NULL,
    source_modified TEXT NOT NULL,
    top_folder TEXT NOT NULL,
    mount_point TEXT NOT NULL,
    plugin_or_dlc TEXT NOT NULL,
    asset_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    generated_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    parent_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    blueprint_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    is_blueprint INTEGER NOT NULL DEFAULT 0,
    is_map INTEGER NOT NULL DEFAULT 0,
    is_data_asset INTEGER NOT NULL DEFAULT 0,
    is_data_table INTEGER NOT NULL DEFAULT 0,
    is_function_library INTEGER NOT NULL DEFAULT 0,
    is_blueprint_interface INTEGER NOT NULL DEFAULT 0,
    is_user_defined_struct INTEGER NOT NULL DEFAULT 0,
    is_user_defined_enum INTEGER NOT NULL DEFAULT 0,
    identity_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    identity_confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
    identity_source_kind TEXT NOT NULL DEFAULT 'filesystem_metadata',
    identity_error TEXT NOT NULL DEFAULT '',
    scan_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inventory_relative ON inventory_files(relative_path);
CREATE INDEX IF NOT EXISTS idx_inventory_scan ON inventory_files(scan_id);
CREATE INDEX IF NOT EXISTS idx_inventory_class ON inventory_files(asset_class_path);

CREATE TABLE IF NOT EXISTS source_cache (
    source_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    payload_zlib BLOB NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source_kind, source_key)
);
"""


@dataclass(frozen=True)
class NativeStoreCandidate:
    manifest_path: Path
    database_path: Path
    evidence_set_id: str
    recipe_id: str
    binary_sha256: str
    generated_at: str
    formal_validation: bool
    trust_status: str
    fingerprint: str


@dataclass(frozen=True)
class RegistryAssetStream:
    path: Path
    expected_count: int

    def __iter__(self) -> Iterator[dict[str, object]]:
        yield from _iter_registry_assets(self.path)

    def __len__(self) -> int:
        return self.expected_count


@dataclass(frozen=True)
class RegistryDependencyStream:
    path: Path
    expected_count: int

    def __iter__(self) -> Iterator[dict[str, object]]:
        yield from _iter_registry_dependencies(self.path)

    def __len__(self) -> int:
        return self.expected_count


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    return f"{prefix}{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _set_meta(connection: sqlite3.Connection, key: str, value: object) -> None:
    text = value if isinstance(value, str) else canonical_json(value)
    connection.execute(
        "INSERT INTO state_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, text),
    )


def _get_meta(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute(
        "SELECT value FROM state_meta WHERE key=?",
        (key,),
    ).fetchone()
    return str(row[0]) if row else default


def _open_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(STATE_SCHEMA_SQL)
    _set_meta(connection, "schema", STATE_SCHEMA)
    connection.commit()
    return connection


def _iter_asset_files(content_root: Path) -> Iterator[Path]:
    for directory, directories, filenames in os.walk(content_root):
        directories.sort(key=str.casefold)
        for filename in sorted(filenames, key=str.casefold):
            if Path(filename).suffix.casefold() in {".uasset", ".umap"}:
                yield Path(directory) / filename


def _object_path(relative: PurePosixPath) -> tuple[str, str]:
    package = "/Game/" + relative.with_suffix("").as_posix()
    return f"{package}.{relative.stem}", package


def _modified_iso(stat: os.stat_result) -> str:
    return datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()


def _companion_metadata(path: Path) -> dict[str, object]:
    base_stat = path.stat()
    extension = path.suffix.casefold()
    uexp = path.with_suffix(".uexp") if extension == ".uasset" else None
    ubulk = path.with_suffix(".ubulk") if extension == ".uasset" else None
    uexp_stat = uexp.stat() if uexp and uexp.is_file() else None
    ubulk_stat = ubulk.stat() if ubulk and ubulk.is_file() else None
    modified = _modified_iso(base_stat)
    fingerprint = metadata_fingerprint(
        uasset_size=base_stat.st_size,
        uasset_modified=modified,
        uexp_size=uexp_stat.st_size if uexp_stat else 0,
        uexp_modified=_modified_iso(uexp_stat) if uexp_stat else "",
        ubulk_size=ubulk_stat.st_size if ubulk_stat else 0,
        ubulk_modified=_modified_iso(ubulk_stat) if ubulk_stat else "",
    )
    return {
        "extension": extension,
        "has_uasset": int(extension == ".uasset"),
        "has_uexp": int(bool(uexp_stat)),
        "has_ubulk": int(bool(ubulk_stat)),
        "uasset_size": int(base_stat.st_size),
        "uexp_size": int(uexp_stat.st_size if uexp_stat else 0),
        "ubulk_size": int(ubulk_stat.st_size if ubulk_stat else 0),
        "file_size_total": int(
            base_stat.st_size
            + (uexp_stat.st_size if uexp_stat else 0)
            + (ubulk_stat.st_size if ubulk_stat else 0)
        ),
        "source_fingerprint": fingerprint,
        "source_modified": modified,
    }


def _full_ref_path(
    value: int,
    imports: Sequence[Mapping[str, object]],
    exports: Sequence[Mapping[str, object]],
) -> str:
    def row_for(index_value: int) -> Mapping[str, object] | None:
        if index_value > 0 and index_value <= len(exports):
            return exports[index_value - 1]
        if index_value < 0 and -index_value <= len(imports):
            return imports[-index_value - 1]
        return None

    leaf_row = row_for(value)
    if not leaf_row:
        return UNKNOWN
    leaf = str(leaf_row.get("object_name") or leaf_row.get("display_name") or "")
    if not leaf:
        return UNKNOWN
    current = value
    seen: set[int] = set()
    while current and current not in seen:
        seen.add(current)
        row = row_for(current)
        if not row:
            break
        name = str(row.get("object_name") or row.get("display_name") or "")
        if name.startswith(("/Game/", "/Script/", "/Engine/", "/Plugin/")):
            package = name.split(".", 1)[0]
            if leaf == package.rsplit("/", 1)[-1]:
                return package
            return f"{package}.{leaf}"
        outer = row.get("outer_index")
        if not isinstance(outer, int):
            break
        current = outer
    return leaf


def _read_uasset_header(
    path: Path, *, max_header_bytes: int = 64 * 1024 * 1024
) -> bytes:
    with path.open("rb") as handle:
        probe = handle.read(1024 * 1024)
        summary, _warnings = parse_uasset_summary(probe)
        header_size = int(summary.get("total_header_size") or 0)
        export_offset = int(summary.get("export_offset") or 0)
        export_count = int(summary.get("export_count") or 0)
        minimum = max(header_size, export_offset + export_count * 112)
        if minimum <= 0:
            raise ValueError("PACKAGE_SUMMARY_UNAVAILABLE")
        if minimum > max_header_bytes:
            raise ValueError("PACKAGE_HEADER_TOO_LARGE")
        if len(probe) >= minimum:
            return probe[:minimum]
        handle.seek(0)
        data = handle.read(minimum)
    if len(data) < minimum:
        raise ValueError("PACKAGE_HEADER_TRUNCATED")
    return data


def parse_serialized_identity(path: Path, object_path: str) -> dict[str, object]:
    data = _read_uasset_header(path)
    summary, summary_warnings = parse_uasset_summary(data)
    names, name_warnings = parse_uasset_name_map(data, summary)
    imports, import_warnings = parse_uasset_imports(data, summary, names)
    exports, export_warnings = parse_uasset_exports(data, summary, names, imports)
    warnings = [*summary_warnings, *name_warnings, *import_warnings, *export_warnings]
    stem = path.stem
    top = next(
        (
            row
            for row in exports
            if str(row.get("object_name") or "") == stem
            and int(row.get("outer_index") or 0) == 0
        ),
        None,
    )
    generated = next(
        (
            row
            for row in exports
            if str(row.get("object_name") or "") == f"{stem}_C"
            and str(row.get("class_name") or "") == "BlueprintGeneratedClass"
        ),
        None,
    )
    class_path = UNKNOWN
    if isinstance(top, Mapping):
        class_index = int(top.get("class_index") or 0)
        class_path = _full_ref_path(class_index, imports, exports)
        if class_path == UNKNOWN:
            class_name = str(top.get("class_name") or "")
            if class_name:
                class_path = class_name
    is_blueprint = bool(
        isinstance(generated, Mapping)
        or class_path.endswith((".Blueprint", "Blueprint"))
        or "Blueprint" in class_path
    )
    generated_path = (
        f"{object_path.rsplit('.', 1)[0]}.{stem}_C"
        if isinstance(generated, Mapping)
        else UNKNOWN
    )
    parent_path = UNKNOWN
    if isinstance(generated, Mapping):
        parent_path = _full_ref_path(
            int(generated.get("super_index") or 0),
            imports,
            exports,
        )
    class_name = class_path.rsplit(".", 1)[-1]
    lower_class = class_name.casefold()
    graph_exports = sum(
        1
        for row in exports
        if str(row.get("class_name") or "") in {"EdGraph", "Function"}
    )
    return {
        "asset_class_path": class_path,
        "generated_class_path": generated_path,
        "parent_class_path": parent_path,
        "blueprint_kind": class_name if is_blueprint else NOT_APPLICABLE,
        "is_blueprint": int(is_blueprint),
        "is_map": int(
            path.suffix.casefold() == ".umap" or lower_class in {"world", "map"}
        ),
        "is_data_asset": int("dataasset" in lower_class),
        "is_data_table": int(lower_class in {"datatable", "curvetable"}),
        "is_function_library": int("functionlibrary" in lower_class),
        "is_blueprint_interface": int("interface" in lower_class),
        "is_user_defined_struct": int(lower_class == "userdefinedstruct"),
        "is_user_defined_enum": int(lower_class == "userdefinedenum"),
        "identity_status": "EXTRACTED",
        "identity_confidence": "HIGH" if not warnings else "MEDIUM",
        "identity_source_kind": "serialized_package_header",
        "identity_error": "",
        "serialized_graph_export_count": graph_exports,
    }


def _plugin_or_dlc(relative: PurePosixPath) -> str:
    parts = relative.parts
    if not parts:
        return "Game"
    if parts[0].casefold() == "mods" and len(parts) > 1:
        return f"Mod:{parts[1]}"
    return parts[0] or "Game"


def scan_devkit_inventory(
    state_db: Path,
    content_root: Path,
    *,
    batch_size: int = 1000,
    max_assets: int | None = None,
    parse_identity: bool = True,
    identity_candidates: set[str] | None = None,
) -> dict[str, object]:
    """Scan package metadata in stable order and resume an interrupted pass."""

    content_root = content_root.resolve()
    if not content_root.is_dir():
        raise FileNotFoundError("ARK DevKit Content root was not found.")
    connection = _open_state(state_db)
    try:
        content_root_token = sha256_bytes(str(content_root).casefold().encode("utf-8"))
        status = _get_meta(connection, "inventory_scan_status")
        resumed = (
            status == "in_progress"
            and _get_meta(connection, "inventory_content_root_token")
            == content_root_token
        )
        if resumed:
            scan_id = _get_meta(connection, "inventory_scan_id")
            cursor = _get_meta(connection, "inventory_scan_cursor")
            added = int(_get_meta(connection, "inventory_scan_added", "0"))
            changed = int(_get_meta(connection, "inventory_scan_changed", "0"))
        else:
            scan_id = uuid.uuid4().hex
            cursor = ""
            added = 0
            changed = 0
            _set_meta(connection, "inventory_scan_status", "in_progress")
            _set_meta(connection, "inventory_scan_id", scan_id)
            _set_meta(connection, "inventory_scan_cursor", "")
            _set_meta(connection, "inventory_scan_added", "0")
            _set_meta(connection, "inventory_scan_changed", "0")
            _set_meta(
                connection,
                "inventory_content_root_token",
                content_root_token,
            )
            connection.commit()

        identity_cache_valid = (
            _get_meta(connection, "inventory_identity_extractor")
            == EXTRACTOR_CACHE_TOKEN
        )
        existing = {
            str(row["object_path"]): (
                str(row["source_fingerprint"]),
                str(row["identity_status"]),
                str(row["scan_id"]),
            )
            for row in connection.execute(
                """
                SELECT object_path, source_fingerprint, identity_status, scan_id
                FROM inventory_files
                """
            )
        }
        processed_this_call = 0
        pending = 0
        last_relative = cursor
        for path in _iter_asset_files(content_root):
            relative = PurePosixPath(path.relative_to(content_root).as_posix())
            relative_text = relative.as_posix()
            try:
                metadata = _companion_metadata(path)
            except OSError:
                continue
            object_path, package_path = _object_path(relative)
            previous = existing.get(object_path)
            # os.walk is deterministic within each directory but is not
            # globally ordered across root files and nested subdirectories.
            # Resume by the persisted scan id instead of comparing a lexical
            # cursor, otherwise an interrupted pass can silently skip a whole
            # subtree.
            if resumed and previous is not None and previous[2] == scan_id:
                continue
            if previous is None:
                added += 1
            elif previous[0] != metadata["source_fingerprint"]:
                changed += 1
            identity: dict[str, object] = {
                "asset_class_path": UNKNOWN,
                "generated_class_path": UNKNOWN,
                "parent_class_path": UNKNOWN,
                "blueprint_kind": UNKNOWN,
                "is_blueprint": 0,
                "is_map": int(path.suffix.casefold() == ".umap"),
                "is_data_asset": 0,
                "is_data_table": 0,
                "is_function_library": 0,
                "is_blueprint_interface": 0,
                "is_user_defined_struct": 0,
                "is_user_defined_enum": 0,
                "identity_status": "UNKNOWN",
                "identity_confidence": "UNKNOWN",
                "identity_source_kind": "filesystem_metadata",
                "identity_error": "",
            }
            should_parse = parse_identity and (
                identity_candidates is None
                or relative_text.casefold() in identity_candidates
                or path.suffix.casefold() == ".umap"
            )
            unchanged_identity = (
                previous is not None
                and previous[0] == metadata["source_fingerprint"]
                and previous[1] == "EXTRACTED"
                and identity_cache_valid
            )
            if should_parse and not unchanged_identity:
                try:
                    identity.update(parse_serialized_identity(path, object_path))
                except Exception as exc:
                    identity["identity_status"] = "NOT_RECOVERED"
                    identity["identity_confidence"] = "UNKNOWN"
                    identity["identity_error"] = type(exc).__name__
            elif unchanged_identity:
                row = connection.execute(
                    """
                    SELECT asset_class_path, generated_class_path, parent_class_path,
                           blueprint_kind, is_blueprint, is_map, is_data_asset,
                           is_data_table, is_function_library, is_blueprint_interface,
                           is_user_defined_struct, is_user_defined_enum,
                           identity_status, identity_confidence,
                           identity_source_kind, identity_error
                    FROM inventory_files WHERE object_path=?
                    """,
                    (object_path,),
                ).fetchone()
                if row:
                    identity.update(dict(row))
            connection.execute(
                """
                INSERT INTO inventory_files (
                    object_path, package_path, asset_name, relative_path, local_path,
                    file_extension, has_uasset, has_uexp, has_ubulk, uasset_size,
                    uexp_size, ubulk_size, file_size_total, source_fingerprint,
                    source_modified, top_folder, mount_point, plugin_or_dlc,
                    asset_class_path, generated_class_path, parent_class_path,
                    blueprint_kind, is_blueprint, is_map, is_data_asset,
                    is_data_table, is_function_library, is_blueprint_interface,
                    is_user_defined_struct, is_user_defined_enum, identity_status,
                    identity_confidence, identity_source_kind, identity_error, scan_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(object_path) DO UPDATE SET
                    package_path=excluded.package_path,
                    asset_name=excluded.asset_name,
                    relative_path=excluded.relative_path,
                    local_path=excluded.local_path,
                    file_extension=excluded.file_extension,
                    has_uasset=excluded.has_uasset,
                    has_uexp=excluded.has_uexp,
                    has_ubulk=excluded.has_ubulk,
                    uasset_size=excluded.uasset_size,
                    uexp_size=excluded.uexp_size,
                    ubulk_size=excluded.ubulk_size,
                    file_size_total=excluded.file_size_total,
                    source_fingerprint=excluded.source_fingerprint,
                    source_modified=excluded.source_modified,
                    top_folder=excluded.top_folder,
                    mount_point=excluded.mount_point,
                    plugin_or_dlc=excluded.plugin_or_dlc,
                    asset_class_path=excluded.asset_class_path,
                    generated_class_path=excluded.generated_class_path,
                    parent_class_path=excluded.parent_class_path,
                    blueprint_kind=excluded.blueprint_kind,
                    is_blueprint=excluded.is_blueprint,
                    is_map=excluded.is_map,
                    is_data_asset=excluded.is_data_asset,
                    is_data_table=excluded.is_data_table,
                    is_function_library=excluded.is_function_library,
                    is_blueprint_interface=excluded.is_blueprint_interface,
                    is_user_defined_struct=excluded.is_user_defined_struct,
                    is_user_defined_enum=excluded.is_user_defined_enum,
                    identity_status=excluded.identity_status,
                    identity_confidence=excluded.identity_confidence,
                    identity_source_kind=excluded.identity_source_kind,
                    identity_error=excluded.identity_error,
                    scan_id=excluded.scan_id
                """,
                (
                    object_path,
                    package_path,
                    path.stem,
                    relative_text,
                    str(path),
                    metadata["extension"],
                    metadata["has_uasset"],
                    metadata["has_uexp"],
                    metadata["has_ubulk"],
                    metadata["uasset_size"],
                    metadata["uexp_size"],
                    metadata["ubulk_size"],
                    metadata["file_size_total"],
                    metadata["source_fingerprint"],
                    metadata["source_modified"],
                    relative.parts[0] if relative.parts else "",
                    "/Game",
                    _plugin_or_dlc(relative),
                    identity["asset_class_path"],
                    identity["generated_class_path"],
                    identity["parent_class_path"],
                    identity["blueprint_kind"],
                    identity["is_blueprint"],
                    identity["is_map"],
                    identity["is_data_asset"],
                    identity["is_data_table"],
                    identity["is_function_library"],
                    identity["is_blueprint_interface"],
                    identity["is_user_defined_struct"],
                    identity["is_user_defined_enum"],
                    identity["identity_status"],
                    identity["identity_confidence"],
                    identity["identity_source_kind"],
                    identity["identity_error"],
                    scan_id,
                ),
            )
            processed_this_call += 1
            pending += 1
            last_relative = relative_text
            if pending >= max(1, batch_size):
                _set_meta(connection, "inventory_scan_cursor", last_relative)
                _set_meta(connection, "inventory_scan_added", str(added))
                _set_meta(connection, "inventory_scan_changed", str(changed))
                connection.commit()
                pending = 0
            if max_assets is not None and processed_this_call >= max_assets:
                _set_meta(connection, "inventory_scan_cursor", last_relative)
                _set_meta(connection, "inventory_scan_added", str(added))
                _set_meta(connection, "inventory_scan_changed", str(changed))
                connection.commit()
                counts = _inventory_counts(connection)
                return {
                    "complete": False,
                    "resumed": resumed,
                    "processedThisCall": processed_this_call,
                    "cursor": last_relative,
                    "added": added,
                    "changed": changed,
                    "deleted": 0,
                    **counts,
                }

        deleted = connection.execute(
            "SELECT COUNT(*) FROM inventory_files WHERE scan_id<>?",
            (scan_id,),
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM inventory_files WHERE scan_id<>?",
            (scan_id,),
        )
        _set_meta(connection, "inventory_scan_status", "complete")
        _set_meta(connection, "inventory_scan_cursor", "")
        _set_meta(connection, "inventory_scan_completed_at", utc_now())
        _set_meta(connection, "inventory_scan_added", str(added))
        _set_meta(connection, "inventory_scan_changed", str(changed))
        _set_meta(connection, "inventory_scan_deleted", str(deleted))
        if parse_identity:
            _set_meta(
                connection,
                "inventory_identity_extractor",
                EXTRACTOR_CACHE_TOKEN,
            )
        connection.commit()
        return {
            "complete": True,
            "resumed": resumed,
            "processedThisCall": processed_this_call,
            "cursor": "",
            "added": added,
            "changed": changed,
            "deleted": int(deleted),
            **_inventory_counts(connection),
        }
    finally:
        connection.close()


def _inventory_counts(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS asset_count,
               SUM(CASE WHEN file_extension='.uasset' THEN 1 ELSE 0 END) AS uasset_count,
               SUM(CASE WHEN file_extension='.umap' THEN 1 ELSE 0 END) AS map_count,
               SUM(CASE WHEN is_blueprint=1 THEN 1 ELSE 0 END) AS blueprint_count,
               SUM(CASE WHEN identity_status='EXTRACTED' THEN 1 ELSE 0 END) AS identity_count
        FROM inventory_files
        """
    ).fetchone()
    return {
        "assetCount": int(row["asset_count"] or 0),
        "uassetCount": int(row["uasset_count"] or 0),
        "mapCount": int(row["map_count"] or 0),
        "blueprintCount": int(row["blueprint_count"] or 0),
        "identityExtractedCount": int(row["identity_count"] or 0),
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    escaped = table.replace('"', '""')
    return {
        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{escaped}")')
    }


def _read_json(path: Path, default: object = None) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _cache_get(
    connection: sqlite3.Connection,
    source_kind: str,
    source_key: str,
    fingerprint: str,
) -> object | None:
    cache_fingerprint = sha256_bytes(
        f"{EXTRACTOR_CACHE_TOKEN}\0{source_kind}\0{fingerprint}".encode("utf-8")
    )
    row = connection.execute(
        """
        SELECT payload_zlib
        FROM source_cache
        WHERE source_kind=? AND source_key=? AND source_fingerprint=? AND status='complete'
        """,
        (source_kind, source_key, cache_fingerprint),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(zlib.decompress(bytes(row[0])).decode("utf-8"))
    except (ValueError, zlib.error, UnicodeError):
        return None


def _cache_put(
    connection: sqlite3.Connection,
    source_kind: str,
    source_key: str,
    fingerprint: str,
    payload: object,
) -> None:
    cache_fingerprint = sha256_bytes(
        f"{EXTRACTOR_CACHE_TOKEN}\0{source_kind}\0{fingerprint}".encode("utf-8")
    )
    encoded = zlib.compress(canonical_json(payload).encode("utf-8"), level=6)
    connection.execute(
        """
        INSERT INTO source_cache(
            source_kind, source_key, source_fingerprint, payload_zlib, status, updated_at
        ) VALUES (?, ?, ?, ?, 'complete', ?)
        ON CONFLICT(source_kind, source_key) DO UPDATE SET
            source_fingerprint=excluded.source_fingerprint,
            payload_zlib=excluded.payload_zlib,
            status='complete',
            updated_at=excluded.updated_at
        """,
        (
            source_kind,
            source_key,
            cache_fingerprint,
            encoded,
            utc_now(),
        ),
    )


def _database_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _safe_graph_kind(graph_type: str, graph_name: str) -> str:
    lowered = graph_type.casefold()
    if "macro" in lowered:
        return "macro"
    if "event" in lowered or graph_name == "EventGraph":
        return "event"
    if "interface" in lowered:
        return "interface"
    if "function" in lowered:
        return "function"
    return "graph"


def _reference_edge_kind(kind: str, classification: str, target_ref: str) -> str:
    lowered = f"{kind} {classification}".casefold()
    if "native" in lowered:
        return "function_call_native"
    if "function" in lowered or "call" in lowered:
        if target_ref.startswith("/Game/"):
            return "function_call_blueprint"
        if target_ref.startswith("/Script/"):
            return "function_call_native"
        return "function_call_unresolved"
    if not target_ref:
        return "reference_unresolved"
    if "default" in lowered:
        return "class_default_reference"
    if "interface" in lowered:
        return "interface_implementation"
    if target_ref.startswith("/Game/"):
        return "graph_asset_reference"
    return "package_dependency"


def _reference_strength(confidence: str, target_ref: str) -> str:
    lowered = confidence.casefold()
    if lowered in {"high", "confirmed"}:
        return "hard"
    if target_ref.startswith("/Game/") and lowered in {"medium", "moderate"}:
        return "searchable"
    return "heuristic"


def _captured_identity(
    capture_dir: Path, object_path: str, asset_name: str
) -> dict[str, object]:
    rows = _read_json(capture_dir / "uasset_exports.json", [])
    if not isinstance(rows, list):
        return {}
    top: Mapping[str, object] | None = None
    generated: Mapping[str, object] | None = None
    components: list[dict[str, object]] = []
    expected_generated = f"{asset_name}_C"
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("object_name") or "")
        class_name = str(item.get("class_name") or "")
        outer = int(item.get("outer_index") or 0)
        if name == asset_name and outer == 0:
            top = item
        if name == expected_generated and class_name == "BlueprintGeneratedClass":
            generated = item
        if (
            outer
            and str(item.get("outer_name") or "") == expected_generated
            and name.endswith("_GEN_VARIABLE")
            and "component" in class_name.casefold()
        ):
            components.append(
                {
                    "component_name": name.removesuffix("_GEN_VARIABLE"),
                    "component_class_path": class_name or UNKNOWN,
                    "component_object_path": "",
                    "is_inherited": 0,
                    "source_property": "serialized_export_map",
                    "confidence": "HIGH",
                    "source_kind": "serialized_capture_export_map",
                }
            )
    if not top and not generated:
        return {"components": components}
    class_name = str((top or {}).get("class_name") or "")
    class_path = (
        f"/Script/Engine.{class_name}"
        if class_name
        in {
            "Blueprint",
            "AnimBlueprint",
            "WidgetBlueprint",
            "DataTable",
            "CurveTable",
            "UserDefinedStruct",
            "UserDefinedEnum",
            "World",
        }
        else class_name or UNKNOWN
    )
    generated_path = (
        f"{object_path.rsplit('.', 1)[0]}.{expected_generated}"
        if generated
        else UNKNOWN
    )
    return {
        "asset_class_path": class_path,
        "generated_class_path": generated_path,
        "blueprint_kind": class_name if generated else NOT_APPLICABLE,
        "is_blueprint": int(bool(generated or "Blueprint" in class_name)),
        "is_map": int(class_name == "World"),
        "is_data_asset": int("DataAsset" in class_name),
        "is_data_table": int(class_name in {"DataTable", "CurveTable"}),
        "is_function_library": int("FunctionLibrary" in class_name),
        "is_blueprint_interface": int("Interface" in class_name),
        "is_user_defined_struct": int(class_name == "UserDefinedStruct"),
        "is_user_defined_enum": int(class_name == "UserDefinedEnum"),
        "identity_status": "EXTRACTED",
        "identity_confidence": "HIGH",
        "identity_source_kind": "serialized_capture_export_map",
        "components": components,
    }


def _extract_blueprint_store(
    manifest_path: Path,
    database_path: Path,
) -> dict[str, object]:
    manifest = _read_json(manifest_path, {})
    if not isinstance(manifest, Mapping):
        raise ValueError("BLUEPRINT_MANIFEST_INVALID")
    connection = sqlite3.connect(_database_uri(database_path), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        revision = connection.execute(
            """
            SELECT revision_id, asset_name, object_path, source_fingerprint,
                   parser_version, schema_version, generated_at
            FROM asset_revisions
            ORDER BY generated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not revision:
            raise ValueError("BLUEPRINT_REVISION_MISSING")
        object_path = str(revision["object_path"])
        asset_name = str(revision["asset_name"])
        capture_dir = manifest_path.parents[1]
        identity = _captured_identity(capture_dir, object_path, asset_name)

        graph_edge_counts: dict[str, int] = {}
        if _table_exists(connection, "edges"):
            graph_edge_counts = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT graph_ref, COUNT(*) FROM edges GROUP BY graph_ref"
                )
            }
        reference_counts: dict[str, Counter[str]] = defaultdict(Counter)
        references: list[dict[str, object]] = []
        if _table_exists(connection, "references"):
            graph_names = {
                str(row[0]): str(row[1])
                for row in connection.execute("SELECT graph_ref, name FROM graphs")
            }
            for row in connection.execute(
                """
                SELECT reference_ref, graph_ref, kind, name, target_ref,
                       classification, confidence
                FROM "references"
                ORDER BY reference_ref
                """
            ):
                graph_ref = str(row["graph_ref"])
                kind = str(row["kind"] or "")
                target = str(row["target_ref"] or "")
                classification = str(row["classification"] or "")
                edge_kind = _reference_edge_kind(kind, classification, target)
                if edge_kind == "function_call_native":
                    reference_counts[graph_ref]["native"] += 1
                if target.startswith("/Game/"):
                    reference_counts[graph_ref]["external"] += 1
                references.append(
                    {
                        "reference_id": str(row["reference_ref"]),
                        "source_object_path": object_path,
                        "target_object_path": target,
                        "edge_kind": edge_kind,
                        "reference_strength": _reference_strength(
                            str(row["confidence"] or ""),
                            target,
                        ),
                        "source_property": str(row["name"] or ""),
                        "source_graph": graph_names.get(graph_ref, ""),
                        "source_function": graph_names.get(graph_ref, ""),
                        "source_evidence_id": str(row["reference_ref"]),
                        "confidence": str(row["confidence"] or UNKNOWN).upper(),
                        "source_kind": "blueprint_evidence_store",
                    }
                )

        diagnostic_counts: dict[str, Counter[str]] = defaultdict(Counter)
        diagnostics_by_reason: Counter[str] = Counter()
        diagnostics_by_status: Counter[str] = Counter()
        if _table_exists(connection, "diagnostics"):
            for row in connection.execute(
                "SELECT scope_ref, status, reason_code FROM diagnostics"
            ):
                scope_ref = str(row["scope_ref"] or "")
                status = str(row["status"] or UNKNOWN).upper()
                reason = str(row["reason_code"] or UNKNOWN).upper()
                diagnostic_counts[scope_ref][reason] += 1
                diagnostics_by_reason[reason] += 1
                diagnostics_by_status[status] += 1

        graphs: list[dict[str, object]] = []
        functions: list[dict[str, object]] = []
        graph_statuses: Counter[str] = Counter()
        graph_confidences: Counter[str] = Counter()
        for row in connection.execute(
            """
            SELECT graph_ref, name, graph_type, status, confidence,
                   node_count, pin_count, link_observation_count
            FROM graphs
            ORDER BY graph_ref
            """
        ):
            graph_ref = str(row["graph_ref"])
            graph_name = str(row["name"] or "")
            graph_type = str(row["graph_type"] or UNKNOWN)
            status = str(row["status"] or UNKNOWN)
            confidence = str(row["confidence"] or UNKNOWN)
            gap_count = sum(diagnostic_counts[graph_ref].values())
            graphs.append(
                {
                    "asset_object_path": object_path,
                    "graph_evidence_id": graph_ref,
                    "graph_name": graph_name,
                    "graph_type": graph_type,
                    "status": status,
                    "confidence": confidence,
                    "node_count": int(row["node_count"] or 0),
                    "pin_count": int(row["pin_count"] or 0),
                    "wire_count": int(graph_edge_counts.get(graph_ref, 0)),
                    "native_call_count": int(reference_counts[graph_ref]["native"]),
                    "external_asset_reference_count": int(
                        reference_counts[graph_ref]["external"]
                    ),
                    "gap_count": int(gap_count),
                }
            )
            graph_statuses[status] += 1
            graph_confidences[confidence] += 1
            function_kind = _safe_graph_kind(graph_type, graph_name)
            if function_kind != "graph":
                functions.append(
                    {
                        "function_id": stable_id(
                            "bp-function://",
                            object_path,
                            graph_ref,
                        ),
                        "asset_object_path": object_path,
                        "function_name": graph_name,
                        "function_kind": function_kind,
                        "graph_evidence_id": graph_ref,
                        "replication_kind": UNKNOWN,
                        "is_pure": 0,
                        "is_override": 0,
                        "declaring_class_path": str(
                            identity.get("generated_class_path") or UNKNOWN
                        ),
                        "call_count_out": int(
                            reference_counts[graph_ref]["native"]
                            + reference_counts[graph_ref]["external"]
                        ),
                        "call_count_in": 0,
                        "native_boundary": (
                            "PRESENT"
                            if reference_counts[graph_ref]["native"]
                            else "NOT_OBSERVED"
                        ),
                        "confidence": confidence,
                        "measurement_status": "PARTIAL",
                    }
                )

        defaults: list[dict[str, object]] = []
        if _table_exists(connection, "class_defaults"):
            for row in connection.execute(
                """
                SELECT default_ref, name, type_name, value_json, value_codec,
                       confidence, extra_json
                FROM class_defaults
                ORDER BY default_ref
                """
            ):
                value_json = str(row["value_json"] or "")
                value_loaded = str(row["value_codec"] or "json") == "json"
                if value_loaded:
                    try:
                        decoded_value = json.loads(value_json)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        decoded_value = None
                        value_loaded = False
                else:
                    decoded_value = None
                type_name = str(row["type_name"] or UNKNOWN)
                projection = project_default_value(
                    type_name,
                    decoded_value,
                    str(row["extra_json"] or "{}"),
                    value_loaded=value_loaded,
                )
                if not value_loaded:
                    projection = {
                        "valueStatus": "NOT_RECOVERED",
                        "valueUsable": False,
                    }
                usable = projection.get("valueUsable") is True
                has_value = usable and decoded_value is not None
                value_status = str(projection.get("valueStatus") or UNKNOWN).upper()
                if value_status == "CONFIRMED":
                    value_status = (
                        "CONFIRMED_FINGERPRINT_ONLY" if has_value else "EXPLICIT_NULL"
                    )
                lowered = type_name.casefold()
                defaults.append(
                    {
                        "surface_id": str(row["default_ref"]),
                        "asset_object_path": object_path,
                        "property_name": str(row["name"] or ""),
                        "property_type": type_name,
                        "declaring_class_path": str(
                            identity.get("generated_class_path") or UNKNOWN
                        ),
                        "has_value": int(has_value),
                        "value_status": value_status,
                        "value_fingerprint": (
                            sha256_bytes(value_json.encode("utf-8"))
                            if usable and value_json
                            else ""
                        ),
                        "is_object_reference": int(
                            "object" in lowered or "class" in lowered
                        ),
                        "is_array": int("array" in lowered),
                        "is_map": int("map" in lowered),
                        "is_struct": int("struct" in lowered),
                        "source_evidence_id": str(row["default_ref"]),
                        "confidence": str(row["confidence"] or UNKNOWN).upper(),
                    }
                )

        package_binary_sha256 = ""
        package_binary_size = 0
        if _table_exists(connection, "source_manifest"):
            for row in connection.execute(
                """
                SELECT sha256, size_bytes, source_kind
                FROM source_manifest
                ORDER BY source_kind, path
                """
            ):
                if str(row["source_kind"] or "") == "package_binary":
                    package_binary_sha256 = str(row["sha256"] or "")
                    package_binary_size = int(row["size_bytes"] or 0)
                    break

        counts = {
            "graphs": len(graphs),
            "functions": len(functions),
            "defaults": len(defaults),
            "references": len(references),
            "components": len(identity.get("components") or []),
            "diagnostics": int(sum(diagnostics_by_reason.values())),
        }
        return {
            "schema": str(revision["schema_version"] or manifest.get("schema") or ""),
            "asset": {
                "object_path": object_path,
                "asset_name": asset_name,
                "revision_id": str(revision["revision_id"]),
                "source_fingerprint": str(revision["source_fingerprint"]),
                "parser_version": str(revision["parser_version"]),
                "generated_at": str(revision["generated_at"]),
                "package_binary_sha256": package_binary_sha256,
                "package_binary_size": package_binary_size,
                **{
                    key: value for key, value in identity.items() if key != "components"
                },
            },
            "components": list(identity.get("components") or []),
            "graphs": graphs,
            "functions": functions,
            "defaults": defaults,
            "references": references,
            "diagnostics_by_reason": dict(sorted(diagnostics_by_reason.items())),
            "diagnostics_by_status": dict(sorted(diagnostics_by_status.items())),
            "graph_statuses": dict(sorted(graph_statuses.items())),
            "graph_confidences": dict(sorted(graph_confidences.items())),
            "counts": counts,
        }
    finally:
        connection.close()


def load_blueprint_evidence(
    state_db: Path,
    captures_root: Path,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    connection = _open_state(state_db)
    payloads: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    cache_hits = 0
    rebuilt = 0
    failures = 0
    try:
        manifests = sorted(
            captures_root.glob("*/evidence/manifest.json"),
            key=lambda item: item.as_posix().casefold(),
        )
        for manifest_path in manifests:
            manifest = _read_json(manifest_path, {})
            if not isinstance(manifest, Mapping):
                failures += 1
                continue
            database_name = str(manifest.get("database") or "evidence.sqlite")
            database_path = manifest_path.parent / database_name
            if not database_path.is_file():
                failures += 1
                continue
            source_key = str(
                manifest.get("object_path") or manifest_path.parent.parent.name
            )
            stat = database_path.stat()
            fingerprint = sha256_bytes(
                manifest_path.read_bytes()
                + f"\0{stat.st_size}\0{stat.st_mtime_ns}".encode("ascii")
            )
            seen_keys.add(source_key)
            cached = _cache_get(
                connection,
                "blueprint_evidence",
                source_key,
                fingerprint,
            )
            if isinstance(cached, Mapping):
                payloads.append(dict(cached))
                cache_hits += 1
                continue
            try:
                payload = _extract_blueprint_store(manifest_path, database_path)
            except Exception:
                failures += 1
                continue
            _cache_put(
                connection,
                "blueprint_evidence",
                source_key,
                fingerprint,
                payload,
            )
            connection.commit()
            payloads.append(payload)
            rebuilt += 1
        if seen_keys:
            placeholders = ",".join("?" for _ in seen_keys)
            connection.execute(
                f"""
                DELETE FROM source_cache
                WHERE source_kind='blueprint_evidence'
                  AND source_key NOT IN ({placeholders})
                """,
                tuple(sorted(seen_keys)),
            )
        else:
            connection.execute(
                "DELETE FROM source_cache WHERE source_kind='blueprint_evidence'"
            )
        connection.commit()
    finally:
        connection.close()
    payloads.sort(
        key=lambda item: str(
            (item.get("asset") or {}).get("object_path") or ""
        ).casefold()
    )
    return payloads, {
        "discovered": len(payloads),
        "cacheHits": cache_hits,
        "rebuilt": rebuilt,
        "failures": failures,
    }


def _native_candidate(manifest_path: Path) -> NativeStoreCandidate | None:
    manifest = _read_json(manifest_path, {})
    if not isinstance(manifest, Mapping):
        return None
    sqlite_info = manifest.get("sqlite")
    if not isinstance(sqlite_info, Mapping):
        return None
    database_path = manifest_path.parent / str(
        sqlite_info.get("path") or "evidence.sqlite"
    )
    if not database_path.is_file():
        return None
    connection = sqlite3.connect(_database_uri(database_path), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT evidence_set_id, recipe_id, generated_at_utc,
                   provenance_status, payload_json
            FROM native_evidence_sets
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        connection.close()
    if not row:
        return None
    payload = {}
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        pass
    binary = payload.get("binary") if isinstance(payload, Mapping) else {}
    binary_sha = str(binary.get("sha256") or "") if isinstance(binary, Mapping) else ""
    if not binary_sha:
        evidence_set_id = str(row["evidence_set_id"] or "")
        parts = evidence_set_id.removeprefix("native-set://").split("/", 1)
        binary_sha = parts[0] if parts else ""
    trust = manifest.get("trust")
    trust = trust if isinstance(trust, Mapping) else {}
    stat = database_path.stat()
    fingerprint = sha256_bytes(
        manifest_path.read_bytes()
        + f"\0{stat.st_size}\0{stat.st_mtime_ns}".encode("ascii")
    )
    return NativeStoreCandidate(
        manifest_path=manifest_path,
        database_path=database_path,
        evidence_set_id=str(row["evidence_set_id"]),
        recipe_id=str(row["recipe_id"] or UNKNOWN),
        binary_sha256=binary_sha,
        generated_at=str(row["generated_at_utc"] or ""),
        formal_validation=bool(trust.get("formalValidation")),
        trust_status=str(
            trust.get("status") or row["provenance_status"] or UNKNOWN
        ).upper(),
        fingerprint=fingerprint,
    )


def _extract_native_store(candidate: NativeStoreCandidate) -> dict[str, object]:
    connection = sqlite3.connect(_database_uri(candidate.database_path), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        evidence_set = connection.execute(
            "SELECT * FROM native_evidence_sets LIMIT 1"
        ).fetchone()
        if not evidence_set:
            raise ValueError("NATIVE_EVIDENCE_SET_MISSING")
        payload: dict[str, object] = {}
        try:
            payload = json.loads(str(evidence_set["payload_json"] or "{}"))
        except json.JSONDecodeError:
            pass
        binary = (
            payload.get("binary") if isinstance(payload.get("binary"), Mapping) else {}
        )
        pdb = payload.get("pdb") if isinstance(payload.get("pdb"), Mapping) else {}
        ghidra = (
            payload.get("ghidra") if isinstance(payload.get("ghidra"), Mapping) else {}
        )
        java = payload.get("java") if isinstance(payload.get("java"), Mapping) else {}
        pdb_sha = str(
            evidence_set["pdb_sha256"]
            if "pdb_sha256" in evidence_set.keys()
            else pdb.get("sha256") or ""
        )
        pdb_guid = str(
            evidence_set["pdb_guid"]
            if "pdb_guid" in evidence_set.keys()
            else pdb.get("guid") or ""
        )
        pdb_age = int(
            evidence_set["pdb_age"]
            if "pdb_age" in evidence_set.keys()
            else pdb.get("age") or 0
        )
        pdb_matched = bool(
            evidence_set["pdb_matched"]
            if "pdb_matched" in evidence_set.keys()
            else pdb.get("matchesBinary")
        )
        module_name = str(binary.get("module") or "")
        binary_sha = str(binary.get("sha256") or candidate.binary_sha256)

        callers: Counter[str] = Counter()
        callees: Counter[str] = Counter()
        if _table_exists(connection, "native_call_edges"):
            for row in connection.execute(
                "SELECT caller_evidence_id, callee_evidence_id FROM native_call_edges"
            ):
                callers[str(row["callee_evidence_id"])] += 1
                callees[str(row["caller_evidence_id"])] += 1
        field_counts: Counter[str] = Counter()
        field_accesses: list[dict[str, object]] = []
        if _table_exists(connection, "native_field_accesses"):
            for row in connection.execute(
                """
                SELECT field_access_id, function_evidence_id, owner_type,
                       field_name, field_offset, access_kind, confidence
                FROM native_field_accesses
                ORDER BY field_access_id
                """
            ):
                evidence_id = str(row["function_evidence_id"])
                field_counts[evidence_id] += 1
                field_accesses.append(
                    {
                        "access_id": str(row["field_access_id"]),
                        "native_evidence_id": evidence_id,
                        "field_name": str(row["field_name"] or UNKNOWN),
                        "field_offset": str(row["field_offset"] or UNKNOWN),
                        "access_kind": str(row["access_kind"] or UNKNOWN),
                        "containing_type": str(row["owner_type"] or UNKNOWN),
                        "source_instruction_or_slice_id": str(row["field_access_id"]),
                        "confidence": str(row["confidence"] or UNKNOWN).upper(),
                    }
                )

        functions: list[dict[str, object]] = []
        for row in connection.execute(
            """
            SELECT evidence_id, module, binary_sha256, rva, name,
                   qualified_name, owner, signature, status, confidence,
                   source,
                   CASE WHEN LENGTH(decompiled_c) > 0 THEN 1 ELSE 0 END
                       AS has_decompile
            FROM native_functions
            ORDER BY evidence_id
            """
        ):
            evidence_id = str(row["evidence_id"])
            functions.append(
                {
                    "native_evidence_id": evidence_id,
                    "module_name": str(row["module"] or module_name or UNKNOWN),
                    "binary_sha256": str(row["binary_sha256"] or binary_sha),
                    "pdb_sha256": pdb_sha,
                    "pdb_guid_age": (f"{pdb_guid}/{pdb_age}" if pdb_guid else UNKNOWN),
                    "qualified_name": str(row["qualified_name"] or row["name"] or ""),
                    "simple_name": str(row["name"] or ""),
                    "owner_class": str(row["owner"] or UNKNOWN),
                    "signature": str(row["signature"] or ""),
                    "rva": str(row["rva"] or ""),
                    "symbol_source": str(row["source"] or UNKNOWN),
                    "pdb_loaded": int(pdb_matched),
                    "decompile_status": (
                        "AVAILABLE_NOT_EXPORTED"
                        if row["has_decompile"]
                        else "NOT_AVAILABLE"
                    ),
                    "caller_count": int(callers[evidence_id]),
                    "callee_count": int(callees[evidence_id]),
                    "field_access_count": int(field_counts[evidence_id]),
                    "called_by_blueprint_count": 0,
                    "confidence": str(row["confidence"] or UNKNOWN).upper(),
                    "recipe_ids_json": [candidate.recipe_id],
                    "evidence_set_ids_json": [candidate.evidence_set_id],
                }
            )

        targets: list[dict[str, object]] = []
        if _table_exists(connection, "native_recipe_targets"):
            for row in connection.execute(
                """
                SELECT target_id, expected_count, status, selector_json,
                       resolved_evidence_ids_json
                FROM native_recipe_targets
                ORDER BY target_id
                """
            ):
                try:
                    selector = json.loads(str(row["selector_json"] or "{}"))
                except json.JSONDecodeError:
                    selector = {}
                try:
                    resolved = json.loads(
                        str(row["resolved_evidence_ids_json"] or "[]")
                    )
                except json.JSONDecodeError:
                    resolved = []
                targets.append(
                    {
                        "target_id": str(row["target_id"]),
                        "expected_count": int(row["expected_count"] or 0),
                        "status": str(row["status"] or UNKNOWN).upper(),
                        "selector": selector,
                        "resolved_evidence_ids": resolved,
                    }
                )

        gaps: list[dict[str, object]] = []
        gap_counts: Counter[tuple[str, str, str]] = Counter()
        if _table_exists(connection, "native_gaps"):
            for row in connection.execute(
                """
                SELECT gap_id, function_evidence_id, status, reason_code,
                       next_probe
                FROM native_gaps
                ORDER BY gap_id
                """
            ):
                status = str(row["status"] or UNKNOWN).upper()
                reason = str(row["reason_code"] or UNKNOWN).upper()
                next_probe = str(row["next_probe"] or "")
                gaps.append(
                    {
                        "gap_id": str(row["gap_id"]),
                        "function_evidence_id": str(row["function_evidence_id"] or ""),
                        "status": status,
                        "reason_code": reason,
                        "next_probe": next_probe,
                    }
                )
                gap_counts[(status, reason, next_probe)] += 1

        blueprint_links: list[dict[str, object]] = []
        if _table_exists(connection, "native_blueprint_links"):
            for row in connection.execute(
                """
                SELECT edge_id, source_id, relation, target_id, status
                FROM native_blueprint_links
                ORDER BY edge_id
                """
            ):
                blueprint_links.append(
                    {
                        "edge_id": str(row["edge_id"]),
                        "source_id": str(row["source_id"] or ""),
                        "relation": str(row["relation"] or ""),
                        "target_id": str(row["target_id"] or ""),
                        "status": str(row["status"] or UNKNOWN).upper(),
                    }
                )
        return {
            "schema": "blueprint-to-code-native-discovery-summary/v1",
            "evidence_set_id": candidate.evidence_set_id,
            "recipe_id": candidate.recipe_id,
            "binary_sha256": binary_sha,
            "module_name": module_name,
            "generated_at": candidate.generated_at,
            "formal_validation": candidate.formal_validation,
            "trust_status": candidate.trust_status,
            "pdb_sha256": pdb_sha,
            "pdb_guid_age": f"{pdb_guid}/{pdb_age}" if pdb_guid else UNKNOWN,
            "pdb_matched": pdb_matched,
            "ghidra_version": str(ghidra.get("version") or UNKNOWN),
            "java_version": str(java.get("version") or UNKNOWN),
            "functions": functions,
            "targets": targets,
            "field_accesses": field_accesses,
            "gaps": gaps,
            "gap_summary": [
                {
                    "status": status,
                    "reason_code": reason,
                    "next_probe": next_probe,
                    "gap_count": count,
                }
                for (status, reason, next_probe), count in sorted(gap_counts.items())
            ],
            "blueprint_links": blueprint_links,
            "counts": {
                "functions": len(functions),
                "targets": len(targets),
                "field_accesses": len(field_accesses),
                "gaps": len(gaps),
                "blueprint_links": len(blueprint_links),
                "call_edges": int(sum(callees.values())),
            },
        }
    finally:
        connection.close()


def load_native_evidence(
    state_db: Path,
    native_root: Path,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    candidates = [
        item
        for item in (
            _native_candidate(path)
            for path in sorted(
                native_root.glob("stores/*/*/evidence.manifest.json"),
                key=lambda value: value.as_posix().casefold(),
            )
        )
        if item is not None
    ]
    selected: dict[tuple[str, str], NativeStoreCandidate] = {}
    fixture_count = 0
    for candidate in candidates:
        # Test-only recipes are never discovery evidence.  A module name can
        # legitimately contain "fixture" in an isolated acceptance fixture,
        # so module naming alone is not a safe production filter.
        if candidate.recipe_id.casefold().startswith("test-"):
            fixture_count += 1
            continue
        key = (candidate.binary_sha256, candidate.recipe_id)
        current = selected.get(key)
        candidate_rank = (
            int(candidate.formal_validation),
            int(candidate.trust_status == "VERIFIED"),
            candidate.generated_at,
        )
        current_rank = (
            (
                int(current.formal_validation),
                int(current.trust_status == "VERIFIED"),
                current.generated_at,
            )
            if current
            else None
        )
        if current is None or candidate_rank > current_rank:
            selected[key] = candidate

    connection = _open_state(state_db)
    payloads: list[dict[str, object]] = []
    cache_hits = 0
    rebuilt = 0
    failures = 0
    seen_keys: set[str] = set()
    try:
        for candidate in sorted(
            selected.values(),
            key=lambda item: (item.binary_sha256, item.recipe_id),
        ):
            source_key = f"{candidate.binary_sha256}/{candidate.recipe_id}"
            seen_keys.add(source_key)
            cached = _cache_get(
                connection,
                "native_evidence",
                source_key,
                candidate.fingerprint,
            )
            if isinstance(cached, Mapping):
                payloads.append(dict(cached))
                cache_hits += 1
                continue
            try:
                payload = _extract_native_store(candidate)
            except Exception:
                failures += 1
                continue
            _cache_put(
                connection,
                "native_evidence",
                source_key,
                candidate.fingerprint,
                payload,
            )
            connection.commit()
            payloads.append(payload)
            rebuilt += 1
        if seen_keys:
            placeholders = ",".join("?" for _ in seen_keys)
            connection.execute(
                f"""
                DELETE FROM source_cache
                WHERE source_kind='native_evidence'
                  AND source_key NOT IN ({placeholders})
                """,
                tuple(sorted(seen_keys)),
            )
        else:
            connection.execute(
                "DELETE FROM source_cache WHERE source_kind='native_evidence'"
            )
        connection.commit()
    finally:
        connection.close()
    return payloads, {
        "candidateStores": len(candidates),
        "selectedStores": len(payloads),
        "filteredFixtureOrDuplicateStores": (len(candidates) - len(selected)),
        "fixtureStores": fixture_count,
        "cacheHits": cache_hits,
        "rebuilt": rebuilt,
        "failures": failures,
    }


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def inventory_existing_knowledge(
    knowledge_db_dir: Path,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
]:
    tables: list[dict[str, object]] = []
    registrations: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    if not knowledge_db_dir.is_dir():
        return (
            tables,
            registrations,
            {
                "status": "SOURCE_NOT_AVAILABLE",
                "fingerprint": "",
                "snapshots": [],
            },
        )
    for database in sorted(
        knowledge_db_dir.glob("*.sqlite"), key=lambda p: p.name.casefold()
    ):
        connection = sqlite3.connect(_database_uri(database), uri=True)
        connection.row_factory = sqlite3.Row
        try:
            snapshots.append(
                {
                    "database": database.name,
                    "sha256": sha256_file(database),
                    "sizeBytes": database.stat().st_size,
                    "userVersion": int(
                        connection.execute("PRAGMA user_version").fetchone()[0]
                    ),
                    "integrity": str(
                        connection.execute("PRAGMA quick_check").fetchone()[0]
                    ),
                }
            )
            source_assets = 0
            if _table_exists(connection, "read_sources"):
                columns = _table_columns(connection, "read_sources")
                if "object_path" in columns:
                    source_assets = int(
                        connection.execute(
                            "SELECT COUNT(DISTINCT object_path) FROM read_sources"
                        ).fetchone()[0]
                    )
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
            for table in table_names:
                identifier = _quote_identifier(table)
                columns = _table_columns(connection, table)
                row_count = int(
                    connection.execute(f"SELECT COUNT(*) FROM {identifier}").fetchone()[
                        0
                    ]
                )
                asset_column = next(
                    (
                        name
                        for name in (
                            "object_path",
                            "asset_object_path",
                            "owner_object_path",
                            "source_object_path",
                            "blueprint_asset_path",
                        )
                        if name in columns
                    ),
                    "",
                )
                if asset_column:
                    distinct_asset_count = int(
                        connection.execute(
                            f"SELECT COUNT(DISTINCT {_quote_identifier(asset_column)}) "
                            f"FROM {identifier}"
                        ).fetchone()[0]
                    )
                    distinct_status = "MEASURED"
                else:
                    distinct_asset_count = -1
                    distinct_status = "NOT_MEASURABLE"
                primary_key_columns = [
                    str(info[1])
                    for info in sorted(
                        connection.execute(f"PRAGMA table_info({identifier})"),
                        key=lambda item: int(item[5] or 0),
                    )
                    if int(info[5] or 0) > 0
                ]
                if primary_key_columns:
                    grouped = ", ".join(
                        _quote_identifier(column) for column in primary_key_columns
                    )
                    duplicate_count = int(
                        connection.execute(
                            f"""
                            SELECT COUNT(*) FROM (
                                SELECT {grouped}
                                FROM {identifier}
                                GROUP BY {grouped}
                                HAVING COUNT(*) > 1
                            )
                            """
                        ).fetchone()[0]
                    )
                    duplicate_status = "PRIMARY_KEY_MEASURED"
                else:
                    duplicate_count = -1
                    duplicate_status = "NOT_MEASURABLE"
                if "processed_current" in columns:
                    stale_count = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {identifier} "
                            "WHERE COALESCE(processed_current, 0)=0"
                        ).fetchone()[0]
                    )
                    stale_status = "PROXY_PROCESSED_CURRENT"
                elif "stale" in columns:
                    stale_count = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {identifier} "
                            "WHERE COALESCE(stale, 0)<>0"
                        ).fetchone()[0]
                    )
                    stale_status = "MEASURED"
                else:
                    stale_count = -1
                    stale_status = "NOT_MEASURABLE"
                tables.append(
                    {
                        "database_name": database.name,
                        "table_name": table,
                        "row_count": row_count,
                        "distinct_asset_count": distinct_asset_count,
                        "source_asset_count": source_assets,
                        "stale_row_count": stale_count,
                        "duplicate_key_count": duplicate_count,
                        "distinct_asset_status": distinct_status,
                        "stale_count_status": stale_status,
                        "duplicate_count_status": duplicate_status,
                    }
                )

            registration_specs = {
                "registered_creatures": ("creature_registration", "creature_path"),
                "registered_items": ("item_registration", "item_path"),
                "registered_buffs": ("buff_registration", "buff_path"),
                "registered_loot": ("loot_registration", "loot_path"),
                "remaps": ("remap_registration", "target_path"),
            }
            for table, (registration_type, target_column) in registration_specs.items():
                columns = _table_columns(connection, table)
                if not columns or "object_path" not in columns:
                    continue
                actual_target = next(
                    (
                        candidate
                        for candidate in (
                            target_column,
                            "target_object_path",
                            "target_path",
                            "creature_path",
                            "item_path",
                            "buff_path",
                            "loot_path",
                        )
                        if candidate in columns
                    ),
                    "",
                )
                if not actual_target:
                    continue
                source_property = (
                    "source_property" if "source_property" in columns else "''"
                )
                source_expression = (
                    _quote_identifier(source_property)
                    if source_property != "''"
                    else "''"
                )
                query = (
                    f"SELECT object_path, {_quote_identifier(actual_target)} AS target, "
                    f"{source_expression} AS source_property FROM {_quote_identifier(table)}"
                )
                for row in connection.execute(query):
                    owner = str(row["object_path"] or "")
                    target = str(row["target"] or "")
                    if not owner or not target:
                        continue
                    registrations.append(
                        {
                            "registration_id": stable_id(
                                "registration://",
                                database.name,
                                table,
                                owner,
                                target,
                            ),
                            "owner_object_path": owner,
                            "registration_type": registration_type,
                            "target_object_path": target,
                            "source_property": str(row["source_property"] or table),
                            "source_evidence_id": (
                                f"existing-kb://{database.stem}/{table}"
                            ),
                            "confidence": "MEDIUM",
                            "source_kind": "existing_knowledge_database",
                        }
                    )
            generic_reference_table = (
                "game_data_references"
                if _table_columns(connection, "game_data_references")
                >= {
                    "object_path",
                    "reference_path",
                    "reference_type",
                    "source_property",
                    "confidence",
                }
                else ""
            )
            if generic_reference_table:
                type_map = {
                    "creature": "creature_registration",
                    "item": "item_registration",
                    "buff": "buff_registration",
                    "loot": "loot_registration",
                    "game_data": "global_entry_reference",
                    "asset": "global_asset_reference",
                }
                for row in connection.execute(
                    """
                    SELECT object_path, reference_path, reference_type,
                           source_property, confidence
                    FROM game_data_references
                    ORDER BY object_path, source_property, reference_path
                    """
                ):
                    owner = str(row["object_path"] or "")
                    target = str(row["reference_path"] or "")
                    reference_type = str(row["reference_type"] or "asset").casefold()
                    if not owner or not target or ":" in target.rsplit(".", 1)[-1]:
                        continue
                    registrations.append(
                        {
                            "registration_id": stable_id(
                                "registration://",
                                database.name,
                                "game_data_references",
                                owner,
                                target,
                                row["source_property"],
                            ),
                            "owner_object_path": owner,
                            "registration_type": type_map.get(
                                reference_type,
                                "global_asset_reference",
                            ),
                            "target_object_path": target,
                            "source_property": str(row["source_property"] or ""),
                            "source_evidence_id": (
                                f"existing-kb://{database.stem}/game_data_references"
                            ),
                            "confidence": str(row["confidence"] or UNKNOWN).upper(),
                            "source_kind": "existing_knowledge_database",
                        }
                    )
        finally:
            connection.close()
    registration_map: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in registrations:
        key = (
            str(row["owner_object_path"]),
            str(row["target_object_path"]),
            str(row["source_property"]),
        )
        current = registration_map.get(key)
        current_generic = current is not None and str(
            current["registration_type"]
        ).startswith("global_")
        row_generic = str(row["registration_type"]).startswith("global_")
        if current is None or (current_generic and not row_generic):
            registration_map[key] = row
    snapshots.sort(key=lambda row: str(row["database"]).casefold())
    return (
        tables,
        list(registration_map.values()),
        {
            "status": (
                "COMPLETE"
                if snapshots and all(row["integrity"] == "ok" for row in snapshots)
                else ("PARTIAL" if snapshots else "SOURCE_NOT_AVAILABLE")
            ),
            "fingerprint": sha256_bytes(canonical_json(snapshots).encode("utf-8"))
            if snapshots
            else "",
            "snapshots": snapshots,
        },
    )


REGISTRY_TAGS = (
    "GeneratedClass",
    "ParentClass",
    "NativeParentClass",
    "BlueprintType",
    "IsDataOnly",
    "DataOnly",
    "ImplementedInterfaces",
)


def _iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"INVALID_JSONL:{path.name}:{line_number}") from exc
            if isinstance(value, Mapping):
                yield dict(value)


def _snapshot_file_metrics(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"REGISTRY_OUTPUT_MISSING:{path.name}")
    digest = hashlib.sha256()
    byte_count = 0
    row_count = 0
    with path.open("rb") as handle:
        for line in handle:
            digest.update(line)
            byte_count += len(line)
            if line.strip():
                row_count += 1
    return {
        "sha256": digest.hexdigest(),
        "bytes": byte_count,
        "rows": row_count,
    }


def _registry_output_path(
    snapshot_dir: Path,
    manifest: Mapping[str, object],
    key: str,
    default_name: str,
) -> Path:
    outputs = manifest.get("outputs")
    outputs = outputs if isinstance(outputs, Mapping) else {}
    relative = str(outputs.get(key) or default_name)
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ValueError(f"REGISTRY_OUTPUT_PATH_NOT_RELATIVE:{key}")
    root = snapshot_dir.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"REGISTRY_OUTPUT_PATH_ESCAPES_ROOT:{key}") from exc
    return resolved


def _normalize_object_path(value: object) -> str:
    """Normalize Unreal class/object reference strings without guessing identity."""

    text = str(value or "").strip()
    if not text:
        return UNKNOWN
    if "'" in text and text.endswith("'"):
        text = text.split("'", 1)[1][:-1]
    if text.startswith(("BlueprintGeneratedClass ", "Class ")):
        text = text.split(" ", 1)[1].strip("'\"")
    return text or UNKNOWN


def _registry_identity(row: Mapping[str, object]) -> dict[str, object]:
    tags = row.get("tags")
    tags = tags if isinstance(tags, Mapping) else {}
    asset_class = _normalize_object_path(row.get("asset_class_path"))
    generated = _normalize_object_path(tags.get("GeneratedClass"))
    parent = _normalize_object_path(tags.get("ParentClass"))
    native_parent = _normalize_object_path(tags.get("NativeParentClass"))
    blueprint_type = str(tags.get("BlueprintType") or UNKNOWN)
    data_only_text = str(
        tags.get("IsDataOnly") or tags.get("DataOnly") or ""
    ).casefold()
    class_lower = asset_class.casefold()
    class_name = class_lower.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
    blueprint_type_lower = blueprint_type.casefold()
    is_blueprint = int(
        generated != UNKNOWN
        or class_name
        in {
            "blueprint",
            "animblueprint",
            "widgetblueprint",
            "levelscriptblueprint",
            "editorutilityblueprint",
        }
        or blueprint_type not in {"", UNKNOWN}
    )
    interface_values = (
        list(row.get("implemented_interfaces") or [])
        if isinstance(row.get("implemented_interfaces"), list)
        else []
    )
    if not interface_values and tags.get("ImplementedInterfaces"):
        interface_text = str(tags.get("ImplementedInterfaces") or "")
        # UE exports interface tags as nested struct text whose commas also
        # delimit fields inside each item.  Extract object paths directly
        # instead of splitting that structure on punctuation.
        interface_values = list(
            dict.fromkeys(
                match.rstrip(".:")
                for match in re.findall(
                    r"/[A-Za-z0-9_]+/[A-Za-z0-9_./:-]+",
                    interface_text,
                )
            )
        )
    return {
        "asset_class_path": asset_class,
        "generated_class_path": generated,
        "parent_class_path": parent,
        "native_parent_class_path": native_parent,
        "blueprint_kind": blueprint_type,
        "is_blueprint": is_blueprint,
        "is_data_only_blueprint": int(data_only_text in {"true", "1", "yes"}),
        "is_map": int(
            class_lower.endswith(".world")
            or class_lower == "world"
            or str(row.get("package_flags") or "").casefold().find("map") >= 0
        ),
        # These flags use authoritative registry class/BlueprintType enums only.
        # Generated object names and parent object paths are useful evidence, but
        # substring matches there must never turn a type hypothesis into a fact.
        "is_data_asset": int(class_name in {"dataasset", "primarydataasset"}),
        "is_data_table": int(class_name in {"datatable", "curvetable"}),
        "is_function_library": int(
            class_name == "blueprintfunctionlibrary"
            or blueprint_type_lower == "bptype_functionlibrary"
        ),
        "is_blueprint_interface": int(blueprint_type_lower == "bptype_interface"),
        "is_user_defined_struct": int(class_name == "userdefinedstruct"),
        "is_user_defined_enum": int(class_name == "userdefinedenum"),
        # The current exporter does not expose an authoritative editor-only
        # flag.  Preserve SQL NULL instead of turning absence into a false fact.
        "is_editor_only": (
            int(bool(row.get("is_editor_only"))) if "is_editor_only" in row else None
        ),
        "identity_status": "EXTRACTED" if asset_class != UNKNOWN else "UNKNOWN",
        "identity_confidence": "HIGH" if asset_class != UNKNOWN else "UNKNOWN",
        "identity_source_kind": "unreal_asset_registry",
        "interfaces": [
            _normalize_object_path(value)
            for value in interface_values
            if _normalize_object_path(value) != UNKNOWN
        ],
    }


def _iter_registry_assets(path: Path) -> Iterator[dict[str, object]]:
    for row_number, row in enumerate(_iter_jsonl(path), start=1):
        row_schema = str(row.get("schema") or "")
        if row_schema and row_schema != "ark.kb.registry-asset.v1":
            raise ValueError(f"REGISTRY_ASSET_SCHEMA_INVALID:{row_number}")
        object_path = str(row.get("object_path") or "")
        package_name = str(row.get("package_name") or "")
        asset_name = str(row.get("asset_name") or "")
        if not object_path and package_name and asset_name:
            object_path = f"{package_name}.{asset_name}"
        if not object_path.startswith("/") or not package_name.startswith("/"):
            raise ValueError(f"REGISTRY_ASSET_ROW_INVALID:{row_number}")
        normalized = {
            "object_path": object_path,
            "package_path": package_name,
            "package_folder": str(row.get("package_path") or ""),
            "asset_name": asset_name or object_path.rsplit(".", 1)[-1],
            "asset_class_path": str(row.get("asset_class_path") or UNKNOWN),
            "package_flags": str(row.get("package_flags") or ""),
            "tags": {
                key: value
                for key, value in (
                    row.get("tags").items()
                    if isinstance(row.get("tags"), Mapping)
                    else []
                )
                if key in REGISTRY_TAGS
            },
            "implemented_interfaces": (
                list(row.get("implemented_interfaces") or [])
                if isinstance(row.get("implemented_interfaces"), list)
                else []
            ),
            "registry_fingerprint": str(
                row.get("registry_fingerprint")
                or sha256_bytes(canonical_json(row).encode("utf-8"))
            ),
        }
        normalized.update(_registry_identity(normalized))
        yield normalized


def _iter_registry_dependencies(
    dependencies_path: Path,
) -> Iterator[dict[str, object]]:
    allowed_strengths = {
        "hard",
        "soft",
        "searchable",
        "hard_manage",
        "soft_manage",
    }
    for row_number, row in enumerate(
        _iter_jsonl(dependencies_path),
        start=1,
    ):
        row_schema = str(row.get("schema") or "")
        if row_schema and row_schema != "ark.kb.registry-dependency.v1":
            raise ValueError(f"REGISTRY_DEPENDENCY_SCHEMA_INVALID:{row_number}")
        source_package = str(
            row.get("source_package") or row.get("source_package_name") or ""
        )
        target_package = str(
            row.get("target_package") or row.get("target_package_name") or ""
        )
        strength = str(
            row.get("reference_strength") or row.get("dependency_type") or ""
        ).casefold()
        strength = {
            "hard_package": "hard",
            "soft_package": "soft",
            "searchable_name": "searchable",
            "hard_management": "hard_manage",
            "soft_management": "soft_manage",
        }.get(strength, strength)
        if not (
            source_package.startswith("/")
            and target_package.startswith("/")
            and strength in allowed_strengths
        ):
            raise ValueError(f"REGISTRY_DEPENDENCY_ROW_INVALID:{row_number}")
        yield {
            "source_package": source_package,
            "target_package": target_package,
            "reference_strength": strength,
            "edge_kind": "package_dependency",
            "confidence": "HIGH",
            "source_kind": "unreal_asset_registry",
        }


def load_registry_snapshot(
    snapshot_dir: Path,
) -> tuple[
    RegistryAssetStream,
    RegistryDependencyStream,
    dict[str, object],
]:
    """Load a sanitized JSONL snapshot produced inside the ARK DevKit."""

    manifest_path = snapshot_dir / "registry_manifest.json"
    manifest = _read_json(manifest_path, {})
    if not isinstance(manifest, Mapping):
        raise ValueError("REGISTRY_MANIFEST_INVALID")
    manifest_schema = str(manifest.get("schema") or "")
    if manifest_schema not in {
        "ark.kb.registry-snapshot.v1",
        "ark.kb.registry-snapshot.v2",
    }:
        raise ValueError("REGISTRY_MANIFEST_SCHEMA_INVALID")
    if str(manifest.get("status") or "").upper() not in {
        "COMPLETE",
        "COMPLETE_WITH_WARNINGS",
    }:
        raise ValueError("REGISTRY_MANIFEST_NOT_COMPLETE")

    assets_path = _registry_output_path(
        snapshot_dir,
        manifest,
        "assets",
        "registry_assets.jsonl",
    )
    dependencies_path = _registry_output_path(
        snapshot_dir,
        manifest,
        "dependencies",
        "registry_dependencies.jsonl",
    )
    checkpoint_path = _registry_output_path(
        snapshot_dir,
        manifest,
        "checkpoint",
        "registry_checkpoint.json",
    )
    integrity = manifest.get("output_integrity")
    if not isinstance(integrity, Mapping):
        raise ValueError("REGISTRY_OUTPUT_INTEGRITY_MISSING")
    asset_metrics = _snapshot_file_metrics(assets_path)
    dependency_metrics = _snapshot_file_metrics(dependencies_path)
    expected_metrics = (
        (
            "assets",
            asset_metrics,
            int(manifest.get("asset_count") or 0),
        ),
        (
            "dependencies",
            dependency_metrics,
            int(manifest.get("dependency_count") or 0),
        ),
    )
    for prefix, actual, expected_rows in expected_metrics:
        expected_hash = str(integrity.get(f"{prefix}_sha256") or "")
        expected_bytes = int(integrity.get(f"{prefix}_bytes") or -1)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
            raise ValueError(f"REGISTRY_{prefix.upper()}_SHA256_MISSING")
        if actual["sha256"].casefold() != expected_hash.casefold():
            raise ValueError(f"REGISTRY_{prefix.upper()}_SHA256_MISMATCH")
        if actual["bytes"] != expected_bytes:
            raise ValueError(f"REGISTRY_{prefix.upper()}_BYTES_MISMATCH")
        if actual["rows"] != expected_rows:
            raise ValueError(f"REGISTRY_{prefix.upper()}_COUNT_MISMATCH")

    if manifest_schema.endswith(".v2"):
        generation_id = str(manifest.get("generation_id") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", generation_id):
            raise ValueError("REGISTRY_GENERATION_ID_INVALID")
        publication = manifest.get("publication")
        if (
            not isinstance(publication, Mapping)
            or publication.get("mode") != "immutable_generation_manifest_commit"
        ):
            raise ValueError("REGISTRY_PUBLICATION_MODE_INVALID")
        producer = manifest.get("producer")
        if (
            not isinstance(producer, Mapping)
            or producer.get("script") != "export_kb_registry_snapshot.py"
            or not re.fullmatch(
                r"[0-9a-fA-F]{64}",
                str(producer.get("source_sha256") or ""),
            )
        ):
            raise ValueError("REGISTRY_PRODUCER_IDENTITY_INVALID")
        outputs = manifest.get("outputs")
        files = manifest.get("files")
        if not isinstance(outputs, Mapping) or not isinstance(files, Mapping):
            raise ValueError("REGISTRY_GENERATION_FILES_MISSING")
        prefix = f"generations/{generation_id}/"
        for key, actual, expected_rows, row_schema in (
            (
                "assets",
                asset_metrics,
                int(manifest.get("asset_count") or 0),
                "ark.kb.registry-asset.v1",
            ),
            (
                "dependencies",
                dependency_metrics,
                int(manifest.get("dependency_count") or 0),
                "ark.kb.registry-dependency.v1",
            ),
        ):
            relative = str(outputs.get(key) or "").replace("\\", "/")
            metadata = files.get(key)
            if not relative.startswith(prefix) or not isinstance(
                metadata,
                Mapping,
            ):
                raise ValueError(f"REGISTRY_{key.upper()}_GENERATION_INVALID")
            if (
                str(metadata.get("path") or "").replace("\\", "/") != relative
                or str(metadata.get("sha256") or "").casefold()
                != str(actual["sha256"]).casefold()
                or int(metadata.get("bytes") or -1) != actual["bytes"]
                or int(metadata.get("lines") or -1) != actual["rows"]
                or int(metadata.get("record_count") or -1) != expected_rows
                or str(metadata.get("row_schema") or "") != row_schema
            ):
                raise ValueError(f"REGISTRY_{key.upper()}_GENERATION_METADATA_MISMATCH")
        checkpoint_metadata = files.get("checkpoint")
        if not isinstance(checkpoint_metadata, Mapping):
            raise ValueError("REGISTRY_CHECKPOINT_METADATA_MISSING")
        checkpoint_metrics = _snapshot_file_metrics(checkpoint_path)
        if (
            str(checkpoint_metadata.get("sha256") or "").casefold()
            != str(checkpoint_metrics["sha256"]).casefold()
            or int(checkpoint_metadata.get("bytes") or -1)
            != checkpoint_metrics["bytes"]
        ):
            raise ValueError("REGISTRY_CHECKPOINT_INTEGRITY_MISMATCH")
        checkpoint = _read_json(checkpoint_path, {})
        if (
            not isinstance(checkpoint, Mapping)
            or checkpoint.get("schema") != "ark.kb.registry-checkpoint.v2"
            or checkpoint.get("generation_id") != generation_id
            or checkpoint.get("status") != manifest.get("status")
        ):
            raise ValueError("REGISTRY_CHECKPOINT_STATE_MISMATCH")

    seen_object_paths: set[str] = set()
    for row_number, normalized in enumerate(
        _iter_registry_assets(assets_path),
        start=1,
    ):
        object_path = str(normalized["object_path"])
        if object_path in seen_object_paths:
            raise ValueError(f"REGISTRY_ASSET_DUPLICATE:{row_number}")
        seen_object_paths.add(object_path)
    assets = RegistryAssetStream(
        path=assets_path,
        expected_count=int(manifest.get("asset_count") or 0),
    )
    dependencies = RegistryDependencyStream(
        path=dependencies_path,
        expected_count=int(manifest.get("dependency_count") or 0),
    )
    return assets, dependencies, dict(manifest)


def run_devkit_registry_export(
    *,
    project_root: Path,
    content_root: Path,
    snapshot_dir: Path,
    include_dependencies: bool = True,
    timeout_seconds: int | None = None,
) -> dict[str, object]:
    """Run the registry exporter in ShooterGameEditor-Cmd.

    The working snapshot is intentionally outside the final bundle and may be
    reused by later incremental runs.
    """

    devkit_root = content_root.resolve().parents[2]
    editor = devkit_root / "Engine" / "Binaries" / "Win64" / "ShooterGameEditor-Cmd.exe"
    uproject_candidates = sorted(content_root.resolve().parent.glob("*.uproject"))
    exporter = (
        project_root.resolve()
        / "scripts"
        / "devkit_exporters"
        / "export_kb_registry_snapshot.py"
    )
    if not editor.is_file():
        return {"status": "SOURCE_NOT_AVAILABLE", "reason": "EDITOR_CMD_NOT_FOUND"}
    if not uproject_candidates:
        return {"status": "SOURCE_NOT_AVAILABLE", "reason": "UPROJECT_NOT_FOUND"}
    if not exporter.is_file():
        return {"status": "SOURCE_NOT_AVAILABLE", "reason": "EXPORTER_NOT_FOUND"}
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["BTC_KB_REGISTRY_OUTPUT"] = str(snapshot_dir.resolve())
    environment["BTC_KB_REGISTRY_DEPENDENCIES"] = "1" if include_dependencies else "0"
    command = [
        str(editor),
        str(uproject_candidates[0]),
        "-run=pythonscript",
        f"-script={exporter}",
        "-unattended",
        "-nop4",
        "-nosplash",
        "-nullrhi",
        "-stdout",
        "-FullStdOutLogOutput",
    ]
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    manifest = _read_json(snapshot_dir / "registry_manifest.json", {})
    if completed.returncode != 0 or not isinstance(manifest, Mapping):
        return {
            "status": "NOT_RECOVERED",
            "reason": f"EDITOR_EXIT_{completed.returncode}",
            "stdoutTail": completed.stdout[-2000:],
            "stderrTail": completed.stderr[-2000:],
        }
    return {
        "status": str(manifest.get("status") or "complete").upper(),
        "assetCount": int(manifest.get("asset_count") or 0),
        "dependencyCount": int(manifest.get("dependency_count") or 0),
        "resumed": bool(
            (manifest.get("checkpoint") or {}).get("resumable")
            if isinstance(manifest.get("checkpoint"), Mapping)
            else False
        ),
    }


def built_in_query_corpus() -> list[dict[str, object]]:
    """Return a stable, cross-domain query corpus for scope discovery."""

    rows = [
        (
            "鱼篓能捕获或拒绝哪些生物，完整资格条件是什么？",
            "creature_item_use",
            ["inventory", "taming"],
            1,
            1,
            1,
            0,
            1,
            0,
            "reports/ARK_FISH_BASKET_CAPTURABLE_CREATURES_2026-07-26_zh.md",
        ),
        (
            "装满鱼篓保存哪些生物数据、有效期如何计算、释放时恢复哪些状态？",
            "inventory_crafting",
            ["runtime_state"],
            1,
            1,
            1,
            0,
            1,
            1,
            "reports/ARK_FISH_BASKET_CAPTURABLE_CREATURES_2026-07-26_zh.md",
        ),
        (
            "Bonding Feather 与 Sanguine Elixir 的驯服、留痕和永久标记为何不同？",
            "taming_breeding_genetics",
            ["buff"],
            1,
            1,
            1,
            0,
            0,
            1,
            "reports/tof_feather_vs_sanguine/conclusion_zh.md",
        ),
        (
            "Gigantoraptor Feather 的显示和继承权重公式来自哪些层？",
            "taming_breeding_genetics",
            ["item_engram", "evidence_boundary"],
            1,
            1,
            1,
            0,
            1,
            1,
            "docs/GPT_PRO_REPORT_OUTPUT_EXAMPLES_zh.md",
        ),
        (
            "Ferox 吃掉元素但驯服进度不增长时是哪一条件阻断？",
            "taming_breeding_genetics",
            ["runtime_state"],
            1,
            1,
            1,
            0,
            0,
            1,
            "reports/FEROX_ELEMENT_CONSUMED_NO_TAMING_PROGRESS_2026-07-26.md",
        ),
        (
            "小 Ferox 受伤后持续逃跑的 Blueprint 与 native 职责边界是什么？",
            "ai_combat_riding",
            ["taming_breeding_genetics"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/Shapeshifter_Small_Character_BP/evidence/manifest.json",
        ),
        (
            "Ferox 成瘾度如何影响伤害、抗性、变身与基因词条？",
            "taming_breeding_genetics",
            ["damage_status"],
            1,
            1,
            1,
            0,
            1,
            1,
            "reports/FEROX_COMPLETE_GAMEPLAY_AND_GENE_TRAITS_2026-07-26.md",
        ),
        (
            "Astrocetus 的击倒武器、命中部位、效率和食物条件是什么？",
            "taming_breeding_genetics",
            ["damage_status"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/SpaceWhale_Character_BP/evidence/manifest.json",
        ),
        (
            "Megachelon 的资源生产公式、深水条件和地图交配限制是什么？",
            "taming_breeding_genetics",
            ["harvest", "map_world"],
            1,
            1,
            1,
            1,
            1,
            1,
            "captures/GiantTurtle_Character_BP/output/context_pack.json",
        ),
        (
            "Axolotl 治疗 Buff 的治疗量、频率、持续、冷却、范围和叠加上限是什么？",
            "buff",
            ["damage_status"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/Buff_AxolotlEnhancedRegen/evidence/manifest.json",
        ),
        (
            "通用 Buff 的启用、刷新、叠加、移除和存档恢复由哪些类负责？",
            "buff",
            ["status_component", "evidence_boundary"],
            1,
            1,
            1,
            0,
            1,
            0,
            "captures/Buff_Base/evidence/manifest.json",
        ),
        (
            "Bleeding、Gashed、Gnashed 的每跳伤害、层数、刷新和终止条件有何差异？",
            "damage_status",
            ["buff"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/Buff_Bleeding/output/context_pack.json",
        ),
        (
            "Yi Ling 的毒、护甲削减、冷却 Buff 如何串联并清除？",
            "damage_status",
            ["buff", "ai_combat_riding"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/Buff_YiLingArmorReduction/output/context_pack.json",
        ),
        (
            "Tek Strider 骇入小游戏的节奏窗口、成功、失败和超时如何组成状态机？",
            "buff",
            ["ui_runtime"],
            1,
            1,
            1,
            0,
            0,
            1,
            "captures/Buff_StriderHackingParent/output/context_pack.json",
        ),
        (
            "Shoulder Dragon 的 XP 驯服、POI、骑乘与宝箱奖励由哪些资产连接？",
            "taming_breeding_genetics",
            ["loot_quality_reward", "map_world"],
            1,
            1,
            1,
            1,
            0,
            1,
            "captures/Buff_ShoulderDragonXPTaming/output/context_pack.json",
        ),
        (
            "DamageType、抗性、StatusComponent 与 Buff 按什么顺序决定最终伤害？",
            "damage_status",
            ["status_component"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/DinoCharacterStatusComponent_BP/output/context_pack.json",
        ),
        (
            "三类 Wyvern 攻击的弹体、DamageType、持续伤害和抗性有哪些继承差异？",
            "damage_status",
            ["projectile_weapon"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/Wyvern_Character_BP_Fire/output/context_pack.json",
        ),
        (
            "生物攻击资源节点时，采集产量如何组合攻击、DamageType、HarvestComponent 与 native 修正？",
            "harvest",
            ["damage_status", "evidence_boundary"],
            1,
            1,
            1,
            0,
            1,
            1,
            "docs/ARK_HARVEST_RANKING_SYSTEM_zh.md",
        ),
        (
            "资源在地图是否真实存在，如何区分直接引用、PCG、World Partition 和仅有定义？",
            "map_world",
            ["harvest", "pcg_world_partition"],
            1,
            0,
            1,
            1,
            0,
            1,
            "docs/ARK_RESOURCE_NODE_EXPLORER_MVP_zh.md",
        ),
        (
            "静态采集排行与游戏实测为何不同，需记录哪些逐击数据？",
            "harvest",
            ["runtime_validation"],
            1,
            1,
            1,
            1,
            1,
            1,
            "docs/HARVEST_RUNTIME_TEST_PROTOCOL_zh.md",
        ),
        (
            "某生物为什么能或不能使用某物品，限制来自哪个类或 native 判定？",
            "inventory_crafting",
            ["creature_item_use"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/PrimalItemWeaponGeneric/output/context_pack.json",
        ),
        (
            "Archelon Algae 由谁生产、进入哪个库存、谁能消耗、施加什么效果？",
            "inventory_crafting",
            ["buff"],
            1,
            1,
            1,
            0,
            0,
            1,
            "captures/PrimalItemConsumable_Veggie_TurtleAlgae_ASA/output/context_pack.json",
        ),
        (
            "物品、Engram、结构与 remap 如何从 PrimalGameData 注册到制作内容？",
            "global_registration",
            ["inventory_crafting", "freshness_invalidation"],
            1,
            1,
            1,
            0,
            0,
            0,
            "captures/PrimalGameData_BP/output/context_pack.json",
        ),
        (
            "漂流瓶或藏宝图从品质档位、SupplyCrate、ItemSet 到物品的抽取流程是什么？",
            "loot_quality_reward",
            ["inventory_crafting"],
            1,
            1,
            1,
            0,
            1,
            0,
            "reports/TIDES_OF_FORTUNE_COMPLETE_NATIVE_2026-07-26.md",
        ),
        (
            "SetWeight、EntryWeight、物品权重、数量范围怎样决定开箱结果？",
            "loot_quality_reward",
            ["formula"],
            1,
            1,
            1,
            0,
            1,
            1,
            "reports/tides_of_fortune_loot_flow_player_guide_2026-07-25.md",
        ),
        (
            "ItemRating 如何映射品质颜色与档位，阈值来自 Blueprint 还是 native？",
            "loot_quality_reward",
            ["evidence_boundary"],
            1,
            1,
            1,
            0,
            1,
            1,
            "reports/ARK_PLAYER_VISIBLE_REWARD_MODEL_DEEP_DIVE_2026-07-26.md",
        ),
        (
            "装备蓝图制作成本如何由基础成本、ItemRating 和品质倍率计算取整？",
            "loot_quality_reward",
            ["inventory_crafting"],
            1,
            1,
            1,
            0,
            1,
            1,
            "reports/ARK_PLAYER_VISIBLE_REWARD_MODEL_DEEP_DIVE_2026-07-26.md",
        ),
        (
            "任务奖励中固定物品、随机装备、蓝图和数量奖励如何区分？",
            "mission_world_event",
            ["loot_quality_reward"],
            1,
            1,
            1,
            1,
            1,
            0,
            "reports/ARK_PLAYER_VISIBLE_REWARD_MODEL_DEEP_DIVE_2026-07-26.md",
        ),
        (
            "六档海盗宝箱的池、权重和品质参数有哪些共同点与叶子差异？",
            "loot_quality_reward",
            ["leaf_variants"],
            1,
            1,
            1,
            0,
            0,
            0,
            "reports/tides_of_fortune_exact_loot_2026-07-25.md",
        ),
        (
            "大形态 Ferox 的攻击、移动、狂暴、耐力和冷却如何构成战斗套件？",
            "ai_combat_riding",
            ["damage_status"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/Shapeshifter_Large_Character_BP/evidence/manifest.json",
        ),
        (
            "Lionfish Lion 的睡眠债、昼夜、隐身、传送与骑乘输入如何分工？",
            "ai_combat_riding",
            ["runtime_state"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/LionfishLion_Character_BP/output/context_pack.json",
        ),
        (
            "VR Battle 最终阶段的小怪生成与 CodeKey 缺失如何重建状态？",
            "mission_world_event",
            ["map_world"],
            1,
            1,
            1,
            1,
            0,
            1,
            "captures/MissionType_VRBattle_FinalStage/output/context_pack.json",
        ),
        (
            "Tides of Fortune 的任务阶段、世界事件和奖励池如何连接全局注册器？",
            "mission_world_event",
            ["global_registration", "map_world"],
            1,
            1,
            1,
            1,
            0,
            0,
            "reports/tides_of_fortune_2026-07-25.md",
        ),
        (
            "如何证明一个公式来自当前 Blueprint、父类、组件、Data Asset 或 native？",
            "evidence_boundary",
            ["coverage"],
            1,
            1,
            1,
            0,
            1,
            0,
            "docs/HYBRID_EVIDENCE_LINKING_zh.md",
        ),
        (
            "DevKit、DLL、PDB 或 Evidence revision 更新后哪些答案和关系必须失效？",
            "freshness_invalidation",
            ["coverage"],
            1,
            0,
            1,
            1,
            1,
            0,
            "docs/REPORT_CLAIM_MANIFEST_zh.md",
        ),
        (
            "BASE、CORE、DLC PrimalGameData 分别注册哪些生物、物品、Buff 与地图入口？",
            "global_registration",
            ["freshness_invalidation"],
            1,
            1,
            1,
            1,
            0,
            0,
            "captures/PrimalGameData_BP/output/context_pack.json",
        ),
        (
            "五槽 Gene Trait 的定义、适用对象、继承、冲突和属性修正来自哪里？",
            "taming_breeding_genetics",
            ["global_registration"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/GeneTraitDefinitions_Core/evidence/manifest.json",
        ),
        (
            "小 Ferox AIController 如何选择追逐、逃跑、吃元素与恢复行为？",
            "ai_combat_riding",
            ["evidence_boundary"],
            1,
            1,
            1,
            0,
            1,
            1,
            "captures/Shapeshifter_Small_AIController_BP/evidence/manifest.json",
        ),
    ]
    result: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        (
            question,
            primary,
            secondary,
            needs_blueprint,
            needs_defaults,
            needs_refs,
            needs_map,
            needs_native,
            needs_runtime,
            report_path,
        ) = row
        result.append(
            {
                "query_id": f"kbq_{index:03d}",
                "question": question,
                "source": "normalized_from_existing_evidence",
                "target_audience": (
                    "mechanics_analyst"
                    if needs_native or primary == "evidence_boundary"
                    else "player"
                ),
                "expected_answer_type": "evidence_backed_mechanism",
                "primary_domain": primary,
                "secondary_domains_json": secondary,
                "requires_blueprint": needs_blueprint,
                "requires_defaults": needs_defaults,
                "requires_references": needs_refs,
                "requires_map_evidence": needs_map,
                "requires_native": needs_native,
                "requires_runtime_validation": needs_runtime,
                "existing_report_path": report_path,
            }
        )
    return result


def _table_column_order(
    connection: sqlite3.Connection,
    table: str,
) -> list[str]:
    escaped = table.replace('"', '""')
    return [
        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{escaped}")')
    ]


def _insert_dict_rows(
    connection: sqlite3.Connection,
    table: str,
    rows: Iterable[Mapping[str, object]],
    *,
    replace: bool = False,
) -> int:
    available = _table_column_order(connection, table)
    count = 0
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    for row in rows:
        columns = [column for column in available if column in row]
        if not columns:
            continue
        quoted = ", ".join(_quote_identifier(column) for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        values = []
        for column in columns:
            value = row[column]
            if isinstance(value, (list, dict)):
                value = canonical_json(value)
            if isinstance(value, str) and _unsafe_text_reasons(value):
                value = "LOCAL_PATH_OR_SECRET_REDACTED"
            values.append(value)
        connection.execute(
            f"{verb} INTO {_quote_identifier(table)} ({quoted}) "
            f"VALUES ({placeholders})",
            values,
        )
        count += 1
    return count


def _unknown_asset(
    object_path: str,
    package_path: str | None = None,
    asset_name: str | None = None,
) -> dict[str, object]:
    package = package_path or object_path.rsplit(".", 1)[0]
    name = asset_name or object_path.rsplit(".", 1)[-1]
    mount = "/" + package.lstrip("/").split("/", 1)[0]
    remainder = package[len(mount) :].lstrip("/")
    top = remainder.split("/", 1)[0] if remainder else ""
    return {
        "object_path": object_path,
        "package_path": package,
        "asset_name": name,
        "asset_class_path": UNKNOWN,
        "blueprint_kind": UNKNOWN,
        "generated_class_path": UNKNOWN,
        "parent_class_path": UNKNOWN,
        "native_parent_class_path": UNKNOWN,
        "mount_point": mount,
        "top_folder": top,
        "plugin_or_dlc": top or mount.lstrip("/") or "UNKNOWN",
        "source_fingerprint": sha256_bytes(object_path.encode("utf-8")),
        "source_modified": "",
        "parse_status": "UNKNOWN",
        "parse_confidence": "UNKNOWN",
        "identity_source_kind": "unresolved_object_identity",
        "identity_confidence": "UNKNOWN",
        "identity_status": "UNKNOWN",
        "is_blueprint": None,
        "is_data_only_blueprint": None,
        "is_map": 0,
        "is_data_asset": None,
        "is_data_table": None,
        "is_function_library": None,
        "is_blueprint_interface": None,
        "is_user_defined_struct": None,
        "is_user_defined_enum": None,
        "is_editor_only": None,
        "relative_logical_path": package.lstrip("/"),
        "file_extension": "",
    }


def _inventory_assets(
    state_db: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    connection = _open_state(state_db)
    assets: dict[str, dict[str, object]] = {}
    local_paths: dict[str, str] = {}
    try:
        for row in connection.execute(
            "SELECT * FROM inventory_files ORDER BY object_path"
        ):
            item = dict(row)
            object_path = str(item["object_path"])
            identity_confirmed = (
                str(item["identity_status"] or "").upper() == "EXTRACTED"
                and str(item["asset_class_path"] or UNKNOWN) != UNKNOWN
            )
            local_paths[str(item["package_path"])] = str(item["local_path"])
            assets[object_path] = {
                "object_path": object_path,
                "package_path": str(item["package_path"]),
                "asset_name": str(item["asset_name"]),
                "asset_class_path": str(item["asset_class_path"] or UNKNOWN),
                "blueprint_kind": str(item["blueprint_kind"] or UNKNOWN),
                "generated_class_path": str(item["generated_class_path"] or UNKNOWN),
                "parent_class_path": str(item["parent_class_path"] or UNKNOWN),
                "native_parent_class_path": UNKNOWN,
                "mount_point": str(item["mount_point"]),
                "top_folder": str(item["top_folder"]),
                "plugin_or_dlc": str(item["plugin_or_dlc"]),
                "is_blueprint": (
                    int(item["is_blueprint"] or 0) if identity_confirmed else None
                ),
                "is_data_only_blueprint": None,
                "is_map": int(item["is_map"] or 0),
                "is_data_asset": (
                    int(item["is_data_asset"] or 0) if identity_confirmed else None
                ),
                "is_data_table": (
                    int(item["is_data_table"] or 0) if identity_confirmed else None
                ),
                "is_function_library": (
                    int(item["is_function_library"] or 0)
                    if identity_confirmed
                    else None
                ),
                "is_blueprint_interface": (
                    int(item["is_blueprint_interface"] or 0)
                    if identity_confirmed
                    else None
                ),
                "is_user_defined_struct": (
                    int(item["is_user_defined_struct"] or 0)
                    if identity_confirmed
                    else None
                ),
                "is_user_defined_enum": (
                    int(item["is_user_defined_enum"] or 0)
                    if identity_confirmed
                    else None
                ),
                "is_editor_only": None,
                "has_uasset": int(item["has_uasset"] or 0),
                "has_uexp": int(item["has_uexp"] or 0),
                "has_ubulk": int(item["has_ubulk"] or 0),
                "file_size_total": int(item["file_size_total"] or 0),
                "source_fingerprint": str(item["source_fingerprint"]),
                "source_modified": str(item["source_modified"]),
                "parse_status": str(item["identity_status"] or UNKNOWN),
                "parse_confidence": str(item["identity_confidence"] or UNKNOWN),
                "identity_source_kind": str(item["identity_source_kind"]),
                "identity_confidence": str(item["identity_confidence"] or UNKNOWN),
                "identity_status": str(item["identity_status"] or UNKNOWN),
                "_identity_error": str(item["identity_error"] or ""),
                "relative_logical_path": str(item["relative_path"]),
                "file_extension": str(item["file_extension"]),
            }
    finally:
        connection.close()
    return assets, local_paths


def _apply_registry_assets(
    assets: dict[str, dict[str, object]],
    registry_assets: Iterable[Mapping[str, object]],
    local_paths: dict[str, str] | None = None,
) -> None:
    by_package: dict[str, dict[str, object]] = {}
    by_relative_package: dict[str, dict[str, object]] = {}
    for value in assets.values():
        by_package.setdefault(str(value["package_path"]), value)
        relative = (
            PurePosixPath(str(value.get("relative_logical_path") or ""))
            .with_suffix("")
            .as_posix()
        )
        if relative:
            by_relative_package.setdefault(relative.casefold(), value)
    for registry in registry_assets:
        object_path = str(registry["object_path"])
        package = str(registry["package_path"])
        relative_candidates = [package.lstrip("/")]
        if package.startswith("/Game/"):
            relative_candidates.insert(0, package.removeprefix("/Game/"))
        source_base = by_package.get(package)
        if source_base is None:
            for candidate in relative_candidates:
                source_base = by_relative_package.get(candidate.casefold())
                if source_base is not None:
                    break
        base = dict(source_base or _unknown_asset(object_path, package))
        original_package = str(base.get("package_path") or "")
        if source_base is not None:
            assets.pop(str(source_base["object_path"]), None)
        base["object_path"] = object_path
        base["package_path"] = package
        base["asset_name"] = str(registry["asset_name"])
        mount = "/" + package.lstrip("/").split("/", 1)[0]
        remainder = package[len(mount) :].lstrip("/")
        base["mount_point"] = mount
        base["top_folder"] = remainder.split("/", 1)[0] if remainder else ""
        base["plugin_or_dlc"] = mount.lstrip("/") or "UNKNOWN"
        if local_paths is not None:
            if original_package in local_paths:
                local_paths[package] = local_paths[original_package]
        for key in (
            "asset_class_path",
            "generated_class_path",
            "parent_class_path",
            "native_parent_class_path",
            "blueprint_kind",
            "is_blueprint",
            "is_data_only_blueprint",
            "is_map",
            "is_data_asset",
            "is_data_table",
            "is_function_library",
            "is_blueprint_interface",
            "is_user_defined_struct",
            "is_user_defined_enum",
            "is_editor_only",
            "identity_status",
            "identity_confidence",
            "identity_source_kind",
        ):
            if key in registry:
                base[key] = registry[key]
        base["parse_status"] = base["identity_status"]
        base["parse_confidence"] = base["identity_confidence"]
        base["source_fingerprint"] = sha256_bytes(
            (
                str(base.get("source_fingerprint") or "")
                + "\0"
                + str(registry.get("registry_fingerprint") or "")
            ).encode("utf-8")
        )
        base["_interfaces"] = list(registry.get("interfaces") or [])
        assets[object_path] = base


def _merge_blueprint_assets(
    assets: dict[str, dict[str, object]],
    payloads: Sequence[Mapping[str, object]],
    local_paths: Mapping[str, str],
) -> None:
    for payload in payloads:
        evidence_asset = payload.get("asset")
        if not isinstance(evidence_asset, Mapping):
            continue
        object_path = str(evidence_asset.get("object_path") or "")
        if not object_path:
            continue
        target = assets.setdefault(object_path, _unknown_asset(object_path))
        registry_owned = target.get("identity_source_kind") == "unreal_asset_registry"
        if not registry_owned:
            for key in (
                "asset_class_path",
                "generated_class_path",
                "parent_class_path",
                "blueprint_kind",
                "is_blueprint",
                "is_map",
                "is_data_asset",
                "is_data_table",
                "is_function_library",
                "is_blueprint_interface",
                "is_user_defined_struct",
                "is_user_defined_enum",
                "identity_status",
                "identity_confidence",
                "identity_source_kind",
            ):
                value = evidence_asset.get(key)
                if value not in (None, "", UNKNOWN):
                    target[key] = value
            target["parse_status"] = target.get("identity_status", UNKNOWN)
            target["parse_confidence"] = target.get("identity_confidence", UNKNOWN)
        counts = payload.get("counts")
        counts = counts if isinstance(counts, Mapping) else {}
        graphs = payload.get("graphs")
        graphs = graphs if isinstance(graphs, list) else []
        target.update(
            {
                "capture_exists": 1,
                "evidence_revision": str(evidence_asset.get("revision_id") or ""),
                "graph_count": int(counts.get("graphs") or 0),
                "function_count": int(counts.get("functions") or 0),
                "event_count": sum(
                    1
                    for row in graphs
                    if str(row.get("graph_type") or "").casefold().find("event") >= 0
                ),
                "macro_count": sum(
                    1
                    for row in graphs
                    if str(row.get("graph_type") or "").casefold().find("macro") >= 0
                ),
                "component_count": int(counts.get("components") or 0),
                "default_property_count": int(counts.get("defaults") or 0),
                # Graph-level native markers are boundary observations, not
                # resolved BP→native links.  Confirmed asset counts are filled
                # from blueprint_native_edges after symbol resolution.
                "native_call_count": 0,
            }
        )
        expected_hash = str(evidence_asset.get("package_binary_sha256") or "")
        expected_size = int(evidence_asset.get("package_binary_size") or 0)
        local_path = local_paths.get(str(target["package_path"]))
        if not local_path:
            freshness = "SOURCE_NOT_AVAILABLE"
        elif not expected_hash:
            freshness = "UNKNOWN"
        else:
            path = Path(local_path)
            try:
                freshness = (
                    "FRESH"
                    if path.stat().st_size == expected_size
                    and sha256_file(path) == expected_hash
                    else "STALE"
                )
            except OSError:
                freshness = "SOURCE_NOT_AVAILABLE"
        target["evidence_freshness"] = freshness


def _extract_serialized_reference_surface(
    path: Path,
    object_path: str,
) -> dict[str, object]:
    data = _read_uasset_header(path)
    summary, summary_warnings = parse_uasset_summary(data)
    names, name_warnings = parse_uasset_name_map(data, summary)
    imports, import_warnings = parse_uasset_imports(data, summary, names)
    soft_paths, soft_warnings = parse_uasset_soft_object_paths(
        data,
        summary,
        names,
    )
    references: dict[tuple[str, str], dict[str, object]] = {}
    for index, row in enumerate(imports):
        target = _full_ref_path(-(index + 1), imports, [])
        if not target.startswith("/"):
            continue
        key = ("serialized_import", target)
        references[key] = {
            "reference_id": stable_id("serialized-import://", object_path, target),
            "source_object_path": object_path,
            "target_object_path": target,
            "edge_kind": "package_dependency",
            "reference_strength": "hard",
            "source_property": str(row.get("class_name") or "ImportMap"),
            "source_graph": "",
            "source_function": "",
            "source_evidence_id": stable_id(
                "serialized-import-evidence://",
                object_path,
                index,
                target,
            ),
            "confidence": "HIGH",
            "source_kind": "serialized_package_import_map",
        }
    for row in soft_paths:
        target = str(row.get("object_path") or "").split(":", 1)[0]
        if not target.startswith("/"):
            continue
        key = ("serialized_soft_path", target)
        references[key] = {
            "reference_id": stable_id("serialized-soft-path://", object_path, target),
            "source_object_path": object_path,
            "target_object_path": target,
            "edge_kind": "graph_asset_reference",
            "reference_strength": "soft",
            "source_property": "SoftObjectPathMap",
            "source_graph": "",
            "source_function": "",
            "source_evidence_id": stable_id(
                "serialized-soft-path-evidence://",
                object_path,
                row.get("index"),
                target,
            ),
            "confidence": "HIGH",
            "source_kind": "serialized_package_soft_object_path",
        }
    warnings = [
        *summary_warnings,
        *name_warnings,
        *import_warnings,
        *soft_warnings,
    ]
    return {
        "references": list(references.values()),
        "warnings": warnings[:50],
        "status": "EXTRACTED" if not warnings else "PARTIAL",
    }


def add_serialized_reference_surfaces(
    state_db: Path,
    assets: Mapping[str, Mapping[str, object]],
    blueprints: Sequence[dict[str, object]],
    local_paths: Mapping[str, str],
) -> dict[str, object]:
    connection = _open_state(state_db)
    extracted = 0
    cache_hits = 0
    failures = 0
    reference_count = 0
    failure_rows: list[dict[str, str]] = []
    try:
        for payload in blueprints:
            evidence_asset = payload.get("asset")
            if not isinstance(evidence_asset, Mapping):
                continue
            object_path = str(evidence_asset.get("object_path") or "")
            asset = assets.get(object_path)
            if not asset:
                continue
            local_path = local_paths.get(str(asset.get("package_path") or ""))
            if not local_path:
                failures += 1
                failure_rows.append(
                    {
                        "object_path": object_path,
                        "error_code": "PACKAGE_NOT_IN_CURRENT_DEVKIT",
                    }
                )
                continue
            path = Path(local_path)
            try:
                stat = path.stat()
            except OSError:
                failures += 1
                failure_rows.append(
                    {
                        "object_path": object_path,
                        "error_code": "PACKAGE_NOT_READABLE",
                    }
                )
                continue
            fingerprint = sha256_bytes(
                f"{stat.st_size}\0{stat.st_mtime_ns}".encode("ascii")
            )
            cached = _cache_get(
                connection,
                "serialized_reference_surface",
                object_path,
                fingerprint,
            )
            if isinstance(cached, Mapping):
                surface = dict(cached)
                cache_hits += 1
            else:
                try:
                    surface = _extract_serialized_reference_surface(
                        path,
                        object_path,
                    )
                except Exception as exc:
                    failures += 1
                    failure_rows.append(
                        {
                            "object_path": object_path,
                            "error_code": type(exc).__name__,
                        }
                    )
                    continue
                _cache_put(
                    connection,
                    "serialized_reference_surface",
                    object_path,
                    fingerprint,
                    surface,
                )
                connection.commit()
                extracted += 1
            rows = [
                dict(row)
                for row in surface.get("references") or []
                if isinstance(row, Mapping)
            ]
            payload.setdefault("references", []).extend(rows)
            reference_count += len(rows)
        connection.commit()
    finally:
        connection.close()
    return {
        "extracted": extracted,
        "cacheHits": cache_hits,
        "failures": failures,
        "references": reference_count,
        "failureRows": failure_rows,
    }


def _package_object_map(
    assets: Mapping[str, Mapping[str, object]],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for object_path, row in assets.items():
        result[str(row["package_path"])].append(object_path)
    for values in result.values():
        values.sort(key=str.casefold)
    return result


def _build_reference_rows(
    assets: Mapping[str, Mapping[str, object]],
    blueprints: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for payload in blueprints:
        for reference in payload.get("references") or []:
            if not isinstance(reference, Mapping):
                continue
            reference_id = str(
                reference.get("reference_id")
                or stable_id(
                    "blueprint-reference://",
                    reference.get("source_object_path"),
                    reference.get("target_object_path"),
                    reference.get("source_property"),
                )
            )
            rows[reference_id] = dict(reference, reference_id=reference_id)
    return sorted(
        rows.values(),
        key=lambda row: (
            str(row["source_object_path"]).casefold(),
            str(row["target_object_path"]).casefold(),
            str(row["reference_id"]),
        ),
    )


def _insert_registry_reference_rows(
    connection: sqlite3.Connection,
    dependencies: Iterable[Mapping[str, object]],
    *,
    batch_size: int = 5000,
) -> int:
    sql = """
        INSERT OR IGNORE INTO asset_references(
            reference_id, source_object_path, target_object_path,
            edge_kind, reference_strength, source_property,
            source_graph, source_function, source_evidence_id,
            confidence, source_kind
        ) VALUES (?, ?, ?, 'package_dependency', ?, 'AssetRegistryDependency',
                  '', '', ?, 'HIGH', 'unreal_asset_registry')
    """
    batch: list[tuple[str, str, str, str, str]] = []
    inserted = 0
    for dependency in dependencies:
        source = str(dependency["source_package"])
        target = str(dependency["target_package"])
        reference_id = stable_id(
            "registry-reference://",
            source,
            target,
            dependency["reference_strength"],
        )
        # Asset Registry dependencies are package-level facts.  Keeping the
        # package paths as endpoints avoids inventing a source×target object
        # Cartesian product for packages that contain multiple assets.
        batch.append(
            (
                reference_id,
                source,
                target,
                str(dependency["reference_strength"]),
                reference_id,
            )
        )
        if len(batch) >= batch_size:
            connection.executemany(sql, batch)
            inserted += len(batch)
            batch.clear()
    if batch:
        connection.executemany(sql, batch)
        inserted += len(batch)
    return inserted


def _build_class_and_interface_rows(
    assets: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    class_rows: dict[tuple[str, str, str], dict[str, object]] = {}
    interface_rows: list[dict[str, object]] = []
    for object_path, asset in assets.items():
        child = str(asset.get("generated_class_path") or UNKNOWN)
        parent = str(asset.get("parent_class_path") or UNKNOWN)
        native_parent = str(asset.get("native_parent_class_path") or UNKNOWN)
        source_kind = str(asset.get("identity_source_kind") or UNKNOWN)
        confidence = str(asset.get("identity_confidence") or UNKNOWN).upper()
        if child != UNKNOWN and parent != UNKNOWN:
            kind = (
                "native_parent" if parent.startswith("/Script/") else "blueprint_parent"
            )
            class_rows[(child, parent, kind)] = {
                "child_class_path": child,
                "parent_class_path": parent,
                "edge_kind": kind,
                "inheritance_depth": 1,
                "source_kind": source_kind,
                "confidence": confidence,
            }
        if child != UNKNOWN and native_parent != UNKNOWN and native_parent != parent:
            class_rows[(child, native_parent, "native_parent")] = {
                "child_class_path": child,
                "parent_class_path": native_parent,
                "edge_kind": "native_parent",
                "inheritance_depth": 1,
                "source_kind": source_kind,
                "confidence": confidence,
            }
        for interface in asset.get("_interfaces") or []:
            interface_rows.append(
                {
                    "owner_object_path": object_path,
                    "interface_class_path": interface,
                    "source_kind": source_kind,
                    "confidence": confidence,
                }
            )
    return list(class_rows.values()), interface_rows


def _dedupe_native_payloads(
    payloads: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    symbols: dict[str, dict[str, object]] = {}
    accesses: dict[str, dict[str, object]] = {}
    gaps: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for payload in payloads:
        recipe_id = str(payload.get("recipe_id") or UNKNOWN)
        evidence_set_id = str(payload.get("evidence_set_id") or UNKNOWN)
        for row in payload.get("functions") or []:
            if not isinstance(row, Mapping):
                continue
            evidence_id = str(row.get("native_evidence_id") or "")
            if not evidence_id:
                continue
            current = symbols.get(evidence_id)
            if current is None:
                current = dict(row)
                current["recipe_ids_json"] = []
                current["evidence_set_ids_json"] = []
                symbols[evidence_id] = current
            current["recipe_ids_json"] = sorted(
                set(current["recipe_ids_json"]) | {recipe_id}
            )
            current["evidence_set_ids_json"] = sorted(
                set(current["evidence_set_ids_json"]) | {evidence_set_id}
            )
        for row in payload.get("field_accesses") or []:
            if isinstance(row, Mapping) and row.get("access_id"):
                accesses[str(row["access_id"])] = dict(row)
        for row in payload.get("gap_summary") or []:
            if not isinstance(row, Mapping):
                continue
            key = (
                evidence_set_id,
                recipe_id,
                str(row.get("status") or UNKNOWN),
                str(row.get("reason_code") or UNKNOWN),
            )
            item = gaps.setdefault(
                key,
                {
                    "evidence_set_id": evidence_set_id,
                    "recipe_id": recipe_id,
                    "status": key[2],
                    "reason_code": key[3],
                    "gap_count": 0,
                    "next_probe": str(row.get("next_probe") or ""),
                },
            )
            item["gap_count"] += int(row.get("gap_count") or 0)
    return list(symbols.values()), list(accesses.values()), list(gaps.values())


def _build_blueprint_native_edges(
    blueprints: Sequence[Mapping[str, object]],
    native_payloads: Sequence[Mapping[str, object]],
    symbols: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    symbol_by_name: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for symbol in symbols:
        symbol_by_name[str(symbol.get("simple_name") or "").casefold()].append(symbol)
    for candidates in symbol_by_name.values():
        candidates.sort(key=lambda row: str(row.get("native_evidence_id") or ""))
    for payload in native_payloads:
        for link in payload.get("blueprint_links") or []:
            if not isinstance(link, Mapping):
                continue
            source = str(link.get("source_id") or "")
            target = str(link.get("target_id") or "")
            if not source.startswith("/Game/") or not target:
                continue
            edge_id = str(
                link.get("edge_id") or stable_id("bp-native://", source, target)
            )
            result[edge_id] = {
                "edge_id": edge_id,
                "blueprint_asset_path": source,
                "blueprint_graph_evidence_id": "",
                "blueprint_function_name": "",
                "native_evidence_id": target,
                "resolution_method": "native_evidence_canonical_link",
                "confidence": "HIGH",
                "status": str(link.get("status") or "CONFIRMED").upper(),
            }
    for payload in blueprints:
        for reference in payload.get("references") or []:
            if not isinstance(reference, Mapping):
                continue
            if str(reference.get("edge_kind")) not in {
                "function_call_native",
                "function_call_unresolved",
            }:
                continue
            name = str(reference.get("source_property") or "")
            candidates = symbol_by_name.get(name.casefold(), [])
            if len(candidates) == 1:
                symbol = candidates[0]
                edge_id = stable_id(
                    "bp-native-candidate://",
                    reference.get("source_object_path"),
                    reference.get("source_evidence_id"),
                    symbol.get("native_evidence_id"),
                )
                result[edge_id] = {
                    "edge_id": edge_id,
                    "blueprint_asset_path": str(
                        reference.get("source_object_path") or ""
                    ),
                    "blueprint_graph_evidence_id": str(
                        reference.get("source_graph") or ""
                    ),
                    "blueprint_function_name": name,
                    "native_evidence_id": str(symbol.get("native_evidence_id") or ""),
                    "resolution_method": "exact_simple_name_candidate",
                    "confidence": "LOW",
                    "status": "NAME_ONLY_CANDIDATE",
                }
            elif candidates:
                for symbol in candidates:
                    native_id = str(symbol.get("native_evidence_id") or "")
                    edge_id = stable_id(
                        "bp-native-ambiguous://",
                        reference.get("source_object_path"),
                        reference.get("source_evidence_id"),
                        name,
                        native_id,
                    )
                    result[edge_id] = {
                        "edge_id": edge_id,
                        "blueprint_asset_path": str(
                            reference.get("source_object_path") or ""
                        ),
                        "blueprint_graph_evidence_id": str(
                            reference.get("source_graph") or ""
                        ),
                        "blueprint_function_name": name,
                        "native_evidence_id": native_id,
                        "resolution_method": ("ambiguous_exact_simple_name_candidate"),
                        "confidence": "LOW",
                        "status": "AMBIGUOUS",
                    }
            elif str(reference.get("edge_kind")) == "function_call_native":
                edge_id = stable_id(
                    "bp-native-unresolved://",
                    reference.get("source_object_path"),
                    reference.get("source_evidence_id"),
                    name,
                )
                result[edge_id] = {
                    "edge_id": edge_id,
                    "blueprint_asset_path": str(
                        reference.get("source_object_path") or ""
                    ),
                    "blueprint_graph_evidence_id": str(
                        reference.get("source_graph") or ""
                    ),
                    "blueprint_function_name": name,
                    "native_evidence_id": "native://UNRESOLVED",
                    "resolution_method": "explicit_native_without_symbol_match",
                    "confidence": "UNKNOWN",
                    "status": "UNRESOLVED",
                }
    return list(result.values())


def _compute_asset_features(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        UPDATE assets
        SET dependency_count = (
                SELECT COUNT(*) FROM asset_references r
                WHERE r.source_object_path IN (
                          assets.object_path, assets.package_path
                      )
                  AND r.edge_kind NOT IN (
                      'reference_unresolved', 'function_call_unresolved'
                  )
            ),
            referencer_count = (
                SELECT COUNT(*) FROM asset_references r
                WHERE r.target_object_path IN (
                    assets.object_path, assets.package_path
                )
            ),
            hard_referencer_count = (
                SELECT COUNT(*) FROM asset_references r
                WHERE r.target_object_path IN (
                    assets.object_path, assets.package_path
                )
                  AND r.reference_strength IN ('hard', 'hard_manage')
            ),
            soft_referencer_count = (
                SELECT COUNT(*) FROM asset_references r
                WHERE r.target_object_path IN (
                    assets.object_path, assets.package_path
                )
                  AND r.reference_strength IN ('soft', 'soft_manage', 'searchable')
            ),
            implemented_by_count = (
                SELECT COUNT(*) FROM interfaces i
                WHERE i.interface_class_path IN (
                    assets.generated_class_path, assets.asset_class_path
                )
            ),
            registry_usage_count = (
                SELECT COUNT(*) FROM system_registrations s
                WHERE s.target_object_path = assets.object_path
            ),
            native_call_count = (
                SELECT COUNT(*) FROM blueprint_native_edges b
                WHERE b.blueprint_asset_path = assets.object_path
                  AND b.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
            ),
            unresolved_native_call_count = (
                SELECT COUNT(*) FROM blueprint_native_edges b
                WHERE b.blueprint_asset_path = assets.object_path
                  AND b.status IN (
                      'UNRESOLVED', 'AMBIGUOUS', 'NAME_ONLY_CANDIDATE'
                  )
            );
        """
    )
    generated_to_object = {
        str(row[0]): str(row[1])
        for row in connection.execute(
            """
            SELECT generated_class_path, object_path
            FROM assets
            WHERE generated_class_path <> 'UNKNOWN'
            """
        )
    }
    children: dict[str, set[str]] = defaultdict(set)
    for child, parent in connection.execute(
        "SELECT child_class_path, parent_class_path FROM class_edges"
    ):
        children[str(parent)].add(str(child))

    def descendants(root: str) -> tuple[int, bool]:
        seen: set[str] = set()
        stack = list(children.get(root, set()))
        cycle = False
        while stack:
            value = stack.pop()
            if value == root:
                cycle = True
                continue
            if value in seen:
                continue
            seen.add(value)
            stack.extend(children.get(value, set()))
        return len(seen), cycle

    for generated_class, object_path in generated_to_object.items():
        descendant_count, cycle = descendants(generated_class)
        connection.execute(
            """
            UPDATE assets
            SET direct_child_count=?, descendant_count=?
            WHERE object_path=?
            """,
            (len(children.get(generated_class, set())), descendant_count, object_path),
        )
        if cycle:
            connection.execute(
                """
                INSERT OR REPLACE INTO coverage(
                    object_path, stage, status, confirmed_count,
                    heuristic_count, ambiguous_count, not_recovered_count,
                    source_not_available_count, stale_count, last_attempt_at,
                    failure_reason
                ) VALUES (?, 'inheritance_graph', 'AMBIGUOUS', 0, 0, 1, 0, 0, 0, ?, ?)
                """,
                (object_path, utc_now(), "INHERITANCE_CYCLE"),
            )

    source_top: dict[str, str] = {}
    for object_path, package_path, top_folder in connection.execute(
        "SELECT object_path, package_path, top_folder FROM assets"
    ):
        source_top[str(object_path)] = str(top_folder)
        source_top.setdefault(str(package_path), str(top_folder))
    map_assets = {
        value
        for row in connection.execute(
            "SELECT object_path, package_path FROM assets WHERE is_map=1"
        )
        for value in (str(row[0]), str(row[1]))
    }
    domains_by_target: dict[str, set[str]] = defaultdict(set)
    map_usage: Counter[str] = Counter()
    for source, target in connection.execute(
        "SELECT source_object_path, target_object_path FROM asset_references"
    ):
        source_text = str(source)
        target_text = str(target)
        domains_by_target[target_text].add(
            source_top.get(
                source_text,
                source_text.lstrip("/").split("/", 1)[0]
                if "/" in source_text
                else "UNKNOWN",
            )
        )
        if source_text in map_assets:
            map_usage[target_text] += 1
    for target, domains in domains_by_target.items():
        connection.execute(
            """
            UPDATE assets
            SET cross_domain_reference_count=?, map_usage_count=?
            WHERE object_path=? OR package_path=?
            """,
            (len(domains), int(map_usage[target]), target, target),
        )

    component_reuse: Counter[str] = Counter(
        str(row[0])
        for row in connection.execute("SELECT component_class_path FROM components")
        if str(row[0]) not in {"", UNKNOWN}
    )
    for class_path, count in component_reuse.items():
        connection.execute(
            """
            UPDATE assets SET component_reuse_count=?
            WHERE generated_class_path=? OR asset_class_path=?
            """,
            (count, class_path, class_path),
        )

    for row in connection.execute(
        """
        SELECT object_path, asset_class_path, asset_name, capture_exists,
               descendant_count, referencer_count, registry_usage_count,
               component_reuse_count, cross_domain_reference_count,
               native_call_count, unresolved_native_call_count,
               graph_count, function_count, default_property_count,
               file_size_total, is_map
        FROM assets
        """
    ):
        reasons: list[str] = []
        centrality = (
            int(row["descendant_count"])
            + int(row["referencer_count"])
            + int(row["registry_usage_count"]) * 5
            + int(row["component_reuse_count"]) * 2
            + int(row["cross_domain_reference_count"]) * 3
            + int(row["native_call_count"]) * 2
        )
        has_structural_signal = bool(centrality)
        name_class = (
            str(row["asset_name"]) + " " + str(row["asset_class_path"])
        ).casefold()
        if row["registry_usage_count"]:
            reasons.append("global_or_system_registration")
        if row["descendant_count"]:
            reasons.append("has_descendants")
        if row["referencer_count"]:
            reasons.append("referenced_by_assets")
        if row["component_reuse_count"]:
            reasons.append("reused_component_class")
        if row["cross_domain_reference_count"] > 1:
            reasons.append("cross_domain_references")
        if row["native_call_count"] or row["unresolved_native_call_count"]:
            reasons.append("blueprint_native_boundary")
        if any(
            token in name_class
            for token in (
                "primalgamedata",
                "gamemode",
                "gamestate",
                "playercontroller",
                "functionlibrary",
                "worldsettings",
            )
        ):
            centrality += 5
            reasons.append("global_entry_name_hint_low_confidence")
        if centrality >= 20 and has_structural_signal:
            tier = 1
        elif row["capture_exists"] and (
            row["graph_count"]
            or row["default_property_count"]
            or row["native_call_count"]
        ):
            tier = 2
            reasons.append("captured_domain_evidence")
        elif row["capture_exists"] or any(
            token in name_class
            for token in (
                "buff",
                "inventory",
                "statuscomponent",
                "harvest",
                "damagetype",
                "supplycrate",
                "mission",
                "aicontroller",
                "datatable",
                "dataasset",
            )
        ):
            tier = 3
            reasons.append("entity_or_domain_candidate")
        else:
            tier = 0
            reasons.append("lightweight_catalog_only")
        cost = (
            1
            + int(row["graph_count"]) * 2
            + int(row["function_count"])
            + int(row["default_property_count"]) // 10
            + int(row["file_size_total"]) // (4 * 1024 * 1024)
        )
        connection.execute(
            """
            UPDATE assets
            SET estimated_deep_read_cost=?, provisional_tier=?,
                provisional_reasons_json=?
            WHERE object_path=?
            """,
            (cost, tier, canonical_json(reasons), row["object_path"]),
        )


def _populate_coverage(
    connection: sqlite3.Connection,
    assets: Mapping[str, Mapping[str, object]],
    blueprints: Sequence[Mapping[str, object]],
    generated_at: str,
) -> None:
    blueprint_by_path = {
        str((payload.get("asset") or {}).get("object_path") or ""): payload
        for payload in blueprints
        if isinstance(payload.get("asset"), Mapping)
    }

    def rows() -> Iterator[dict[str, object]]:
        for object_path, asset in assets.items():
            identity_status = str(asset.get("identity_status") or UNKNOWN).upper()
            confirmed = int(asset.get("asset_class_path") not in (None, "", UNKNOWN))
            heuristic = int(asset.get("identity_source_kind") == "filesystem_metadata")
            yield {
                "object_path": object_path,
                "stage": "asset_identity",
                "status": identity_status,
                "confirmed_count": confirmed,
                "heuristic_count": heuristic,
                "ambiguous_count": int(identity_status == "AMBIGUOUS"),
                "not_recovered_count": int(identity_status == "NOT_RECOVERED"),
                "source_not_available_count": 0,
                "stale_count": 0,
                "last_attempt_at": generated_at,
                "failure_reason": ("" if confirmed else "ASSET_CLASS_NOT_RECOVERED"),
            }
            payload = blueprint_by_path.get(object_path)
            if payload:
                diagnostics_status = payload.get("diagnostics_by_status")
                diagnostics_status = (
                    diagnostics_status
                    if isinstance(diagnostics_status, Mapping)
                    else {}
                )
                freshness = str(asset.get("evidence_freshness") or UNKNOWN)
                yield {
                    "object_path": object_path,
                    "stage": "blueprint_evidence",
                    "status": freshness,
                    "confirmed_count": int(
                        (payload.get("counts") or {}).get("graphs") or 0
                    ),
                    "heuristic_count": int(
                        (payload.get("graph_statuses") or {}).get("heuristic", 0)
                    ),
                    "ambiguous_count": int(diagnostics_status.get("AMBIGUOUS", 0)),
                    "not_recovered_count": int(
                        diagnostics_status.get("NOT_RECOVERED", 0)
                    ),
                    "source_not_available_count": int(
                        diagnostics_status.get("SOURCE_NOT_AVAILABLE", 0)
                    ),
                    "stale_count": int(freshness == "STALE"),
                    "last_attempt_at": generated_at,
                    "failure_reason": (
                        "PACKAGE_SHA256_MISMATCH" if freshness == "STALE" else ""
                    ),
                }
                continue
            blueprint_state = asset.get("is_blueprint")
            if blueprint_state == 1:
                status = "NOT_MEASURED"
                failure_reason = "NO_EVIDENCE_STORE"
            elif blueprint_state == 0 and confirmed:
                status = "NOT_APPLICABLE"
                failure_reason = "ASSET_NOT_BLUEPRINT"
            else:
                status = "NOT_MEASURED"
                failure_reason = "BLUEPRINT_APPLICABILITY_UNKNOWN"
            yield {
                "object_path": object_path,
                "stage": "blueprint_evidence",
                "status": status,
                "confirmed_count": 0,
                "heuristic_count": 0,
                "ambiguous_count": 0,
                "not_recovered_count": 0,
                "source_not_available_count": 0,
                "stale_count": 0,
                "last_attempt_at": generated_at,
                "failure_reason": failure_reason,
            }

    _insert_dict_rows(connection, "coverage", rows(), replace=True)


def _materialize_database(
    *,
    database_path: Path,
    assets: dict[str, dict[str, object]],
    registry_assets: RegistryAssetStream | Sequence[Mapping[str, object]],
    registry_dependencies: RegistryDependencyStream | Sequence[Mapping[str, object]],
    registry_manifest: Mapping[str, object],
    blueprints: Sequence[Mapping[str, object]],
    blueprint_stats: Mapping[str, object],
    native_payloads: Sequence[Mapping[str, object]],
    native_stats: Mapping[str, int],
    existing_tables: Sequence[Mapping[str, object]],
    existing_source: Mapping[str, object],
    registrations: Sequence[Mapping[str, object]],
    generated_at: str,
) -> dict[str, int]:
    if database_path.exists():
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(DISCOVERY_SCHEMA_SQL)
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("schema", DISCOVERY_SCHEMA),
                ("generated_at_utc", generated_at),
                ("tool_version", TOOL_VERSION),
                (
                    "blueprint_store_discovered",
                    str(blueprint_stats.get("discovered") or 0),
                ),
                (
                    "blueprint_store_failures",
                    str(blueprint_stats.get("failures") or 0),
                ),
                (
                    "serialized_reference_failures",
                    str(blueprint_stats.get("serializedReferenceFailures") or 0),
                ),
                (
                    "native_store_candidates",
                    str(native_stats.get("candidateStores") or 0),
                ),
                (
                    "native_store_selected",
                    str(native_stats.get("selectedStores") or 0),
                ),
                (
                    "native_store_failures",
                    str(native_stats.get("failures") or 0),
                ),
            ),
        )
        _insert_dict_rows(connection, "assets", assets.values())

        class_rows, interface_rows = _build_class_and_interface_rows(assets)
        _insert_dict_rows(connection, "class_edges", class_rows)
        _insert_dict_rows(connection, "interfaces", interface_rows)

        component_rows: list[dict[str, object]] = []
        graph_rows: list[dict[str, object]] = []
        function_rows: list[dict[str, object]] = []
        default_rows: list[dict[str, object]] = []
        for payload in blueprints:
            evidence_asset = payload.get("asset")
            evidence_asset = (
                evidence_asset if isinstance(evidence_asset, Mapping) else {}
            )
            object_path = str(evidence_asset.get("object_path") or "")
            for component in payload.get("components") or []:
                if isinstance(component, Mapping):
                    component_rows.append(
                        dict(component, owner_object_path=object_path)
                    )
            graph_rows.extend(
                dict(row)
                for row in payload.get("graphs") or []
                if isinstance(row, Mapping)
            )
            function_rows.extend(
                dict(row)
                for row in payload.get("functions") or []
                if isinstance(row, Mapping)
            )
            default_rows.extend(
                dict(row)
                for row in payload.get("defaults") or []
                if isinstance(row, Mapping)
            )
        _insert_dict_rows(connection, "components", component_rows)
        _insert_registry_reference_rows(
            connection,
            registry_dependencies,
        )
        reference_rows = _build_reference_rows(
            assets,
            blueprints,
        )
        for registration in registrations:
            registration_id = str(registration["registration_id"])
            registration_type = str(registration["registration_type"])
            edge_kind = {
                "creature_registration": "spawn_registration",
                "item_registration": "engram_registration",
                "buff_registration": "buff_application",
                "loot_registration": "loot_entry",
            }.get(registration_type, "registry_entry")
            reference_rows.append(
                {
                    "reference_id": stable_id(
                        "registration-reference://", registration_id
                    ),
                    "source_object_path": registration["owner_object_path"],
                    "target_object_path": registration["target_object_path"],
                    "edge_kind": edge_kind,
                    "reference_strength": "hard",
                    "source_property": registration["source_property"],
                    "source_graph": "",
                    "source_function": "",
                    "source_evidence_id": registration["source_evidence_id"],
                    "confidence": registration["confidence"],
                    "source_kind": registration["source_kind"],
                }
            )
        _insert_dict_rows(connection, "asset_references", reference_rows)
        _insert_dict_rows(connection, "graphs", graph_rows)
        _insert_dict_rows(connection, "blueprint_functions", function_rows)
        _insert_dict_rows(connection, "default_property_surface", default_rows)
        _insert_dict_rows(connection, "system_registrations", registrations)

        native_symbols, field_accesses, gap_rows = _dedupe_native_payloads(
            native_payloads
        )
        _insert_dict_rows(connection, "native_symbols", native_symbols)
        _insert_dict_rows(connection, "native_field_accesses", field_accesses)
        _insert_dict_rows(connection, "native_gap_summary", gap_rows)
        bp_native_edges = _build_blueprint_native_edges(
            blueprints,
            native_payloads,
            native_symbols,
        )
        _insert_dict_rows(
            connection,
            "blueprint_native_edges",
            bp_native_edges,
        )
        connection.execute(
            """
            UPDATE native_symbols
            SET called_by_blueprint_count = (
                SELECT COUNT(DISTINCT b.blueprint_asset_path)
                FROM blueprint_native_edges b
                WHERE b.native_evidence_id =
                          native_symbols.native_evidence_id
                  AND b.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
            )
            """
        )

        _insert_dict_rows(
            connection,
            "existing_knowledge_tables",
            existing_tables,
        )
        query_rows = built_in_query_corpus()
        _insert_dict_rows(connection, "query_corpus", query_rows)
        source_rows = [
            {
                "source_id": "source://filesystem-inventory",
                "source_kind": "filesystem_inventory",
                "schema_version": STATE_SCHEMA,
                "source_fingerprint": _content_fingerprint(assets),
                "status": "COMPLETE",
                "confidence": "HIGH",
                "record_count": len(assets),
                "generated_at": generated_at,
                "limitations_json": [
                    "Package file inventory is authoritative for file presence, not asset class."
                ],
            },
            {
                "source_id": "source://unreal-asset-registry",
                "source_kind": "unreal_asset_registry",
                "schema_version": str(registry_manifest.get("schema") or UNKNOWN),
                "source_fingerprint": str(
                    registry_manifest.get("inventory_signature")
                    or registry_manifest.get("content_fingerprint")
                    or registry_manifest.get("snapshot_fingerprint")
                    or ""
                ),
                "status": (
                    str(registry_manifest.get("status") or "").upper()
                    if registry_assets
                    else "SOURCE_NOT_AVAILABLE"
                ),
                "confidence": "HIGH" if registry_assets else "UNKNOWN",
                "record_count": len(registry_assets),
                "generated_at": str(
                    registry_manifest.get("generated_at_utc")
                    or registry_manifest.get("generated_at")
                    or generated_at
                ),
                "limitations_json": (
                    []
                    if registry_assets
                    else ["Asset Registry snapshot was not available."]
                ),
            },
            {
                "source_id": "source://blueprint-evidence-stores",
                "source_kind": "blueprint_evidence_store",
                "schema_version": "ark.blueprint.evidence.v2",
                "source_fingerprint": sha256_bytes(
                    canonical_json(
                        sorted(
                            (
                                str(
                                    (payload.get("asset") or {}).get("revision_id", "")
                                ),
                                str(
                                    (payload.get("asset") or {}).get(
                                        "source_fingerprint", ""
                                    )
                                ),
                            )
                            for payload in blueprints
                        )
                    ).encode("utf-8")
                ),
                "status": ("PARTIAL" if blueprints else "SOURCE_NOT_AVAILABLE"),
                "confidence": "HIGH" if blueprints else "UNKNOWN",
                "record_count": len(blueprints),
                "generated_at": generated_at,
                "limitations_json": [
                    "Only already captured assets have graph/default summaries."
                ],
            },
            {
                "source_id": "source://existing-knowledge-databases",
                "source_kind": "existing_knowledge_database",
                "schema_version": "sqlite-snapshot-inventory/v1",
                "source_fingerprint": str(existing_source.get("fingerprint") or ""),
                "status": str(existing_source.get("status") or "SOURCE_NOT_AVAILABLE"),
                "confidence": (
                    "HIGH" if existing_source.get("status") == "COMPLETE" else "UNKNOWN"
                ),
                "record_count": sum(
                    int(row.get("row_count") or 0) for row in existing_tables
                ),
                "generated_at": generated_at,
                "limitations_json": {
                    "databaseSnapshots": list(existing_source.get("snapshots") or []),
                    "note": (
                        "Database basenames and hashes identify the read-only "
                        "snapshot; local directories are omitted."
                    ),
                },
            },
            {
                "source_id": "source://native-evidence-stores",
                "source_kind": "bounded_native_evidence",
                "schema_version": "blueprint-to-code-native-evidence/v1",
                "source_fingerprint": sha256_bytes(
                    canonical_json(
                        sorted(
                            (
                                payload.get("binary_sha256"),
                                payload.get("recipe_id"),
                                payload.get("evidence_set_id"),
                            )
                            for payload in native_payloads
                        )
                    ).encode("utf-8")
                ),
                "status": (
                    "COMPLETE"
                    if native_payloads and not native_stats.get("failures")
                    else ("PARTIAL" if native_payloads else "SOURCE_NOT_AVAILABLE")
                ),
                "confidence": "HIGH" if native_payloads else "UNKNOWN",
                "record_count": len(native_symbols),
                "generated_at": generated_at,
                "limitations_json": [
                    "Decompiler bodies are deliberately not exported.",
                    "Name-only Blueprint/native joins remain candidates.",
                ],
            },
        ]
        _insert_dict_rows(connection, "source_inventory", source_rows)
        _populate_coverage(connection, assets, blueprints, generated_at)
        failure_rows: list[dict[str, object]] = []
        failure_coverage: list[dict[str, object]] = []
        for object_path, asset in assets.items():
            error_code = str(asset.get("_identity_error") or "")
            if not error_code:
                continue
            failure_rows.append(
                {
                    "failure_id": stable_id(
                        "scan-failure://",
                        object_path,
                        "serialized_identity",
                        error_code,
                    ),
                    "object_path": object_path,
                    "stage": "serialized_identity",
                    "error_code": error_code,
                    "status": "NOT_RECOVERED",
                    "detail_redacted": error_code,
                }
            )
        serialized_failures = blueprint_stats.get("serializedReferenceFailureRows")
        if isinstance(serialized_failures, list):
            for row in serialized_failures:
                if not isinstance(row, Mapping):
                    continue
                object_path = str(row.get("object_path") or "")
                error_code = str(row.get("error_code") or "NOT_RECOVERED")
                if not object_path:
                    continue
                status = (
                    "SOURCE_NOT_AVAILABLE"
                    if error_code.startswith("PACKAGE_")
                    else "NOT_RECOVERED"
                )
                failure_rows.append(
                    {
                        "failure_id": stable_id(
                            "scan-failure://",
                            object_path,
                            "serialized_reference_surface",
                            error_code,
                        ),
                        "object_path": object_path,
                        "stage": "serialized_reference_surface",
                        "error_code": error_code,
                        "status": status,
                        "detail_redacted": error_code,
                    }
                )
                failure_coverage.append(
                    {
                        "object_path": object_path,
                        "stage": "serialized_reference_surface",
                        "status": status,
                        "confirmed_count": 0,
                        "heuristic_count": 0,
                        "ambiguous_count": 0,
                        "not_recovered_count": int(status == "NOT_RECOVERED"),
                        "source_not_available_count": int(
                            status == "SOURCE_NOT_AVAILABLE"
                        ),
                        "stale_count": 0,
                        "last_attempt_at": generated_at,
                        "failure_reason": error_code,
                    }
                )
        _insert_dict_rows(connection, "scan_failures", failure_rows)
        _insert_dict_rows(
            connection,
            "coverage",
            failure_coverage,
            replace=True,
        )
        _compute_asset_features(connection)
        connection.commit()
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise RuntimeError(f"SQLITE_INTEGRITY_FAILED:{integrity}")
        counts = {
            table: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                ).fetchone()[0]
            )
            for table in (
                "assets",
                "class_edges",
                "interfaces",
                "components",
                "asset_references",
                "graphs",
                "blueprint_functions",
                "default_property_surface",
                "system_registrations",
                "native_symbols",
                "blueprint_native_edges",
                "native_field_accesses",
                "coverage",
                "existing_knowledge_tables",
                "query_corpus",
                "scan_failures",
            )
        }
        counts["confirmed_blueprint_native_edges"] = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM blueprint_native_edges
                WHERE status='CONFIRMED'
                """
            ).fetchone()[0]
        )
        return counts
    finally:
        connection.close()


def _write_csv_rows(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        canonical_json(value)
                        if isinstance(value, (list, dict))
                        else value
                    )
                    for key, value in row.items()
                }
            )


def _query_dicts(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Sequence[object] = (),
) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(sql, parameters)]


def _write_csv_exports(bundle_dir: Path, database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        inventory_columns = [
            "object_path",
            "package_path",
            "asset_name",
            "asset_class_path",
            "blueprint_kind",
            "generated_class_path",
            "parent_class_path",
            "native_parent_class_path",
            "mount_point",
            "top_folder",
            "plugin_or_dlc",
            "is_blueprint",
            "is_map",
            "is_data_asset",
            "is_data_table",
            "file_size_total",
            "source_fingerprint",
            "capture_exists",
            "evidence_freshness",
            "parse_status",
            "parse_confidence",
            "identity_source_kind",
        ]
        preview_rows = _query_dicts(
            connection,
            f"""
            SELECT {", ".join(inventory_columns)}
            FROM assets
            ORDER BY
                CASE WHEN capture_exists=1 THEN 0 ELSE 1 END,
                provisional_tier,
                object_path
            LIMIT 5000
            """,
        )
        _write_csv_rows(
            bundle_dir / "asset_inventory_preview.csv",
            inventory_columns,
            preview_rows,
        )

        candidate_columns = [
            "object_path",
            "asset_class_path",
            "parent_class_path",
            "native_parent_class_path",
            "descendant_count",
            "referencer_count",
            "hard_referencer_count",
            "soft_referencer_count",
            "dependency_count",
            "cross_domain_reference_count",
            "component_reuse_count",
            "registry_usage_count",
            "map_usage_count",
            "native_call_count",
            "unresolved_native_call_count",
            "graph_count",
            "function_count",
            "default_property_count",
            "query_hit_count",
            "existing_report_count",
            "estimated_deep_read_cost",
            "provisional_tier",
            "reasons",
        ]
        candidate_rows = _query_dicts(
            connection,
            """
            SELECT object_path, asset_class_path, parent_class_path,
                   native_parent_class_path, descendant_count,
                   referencer_count, hard_referencer_count,
                   soft_referencer_count, dependency_count,
                   cross_domain_reference_count, component_reuse_count,
                   registry_usage_count, map_usage_count, native_call_count,
                   unresolved_native_call_count, graph_count, function_count,
                   default_property_count, query_hit_count,
                   existing_report_count, estimated_deep_read_cost,
                   provisional_tier, provisional_reasons_json AS reasons
            FROM assets
            ORDER BY
                CASE provisional_tier
                    WHEN 1 THEN 0 WHEN 2 THEN 1 WHEN 3 THEN 2 ELSE 3
                END,
                registry_usage_count DESC,
                descendant_count DESC,
                referencer_count DESC,
                cross_domain_reference_count DESC,
                native_call_count DESC,
                object_path
            LIMIT 1000
            """,
        )
        _write_csv_rows(
            bundle_dir / "top_background_candidates.csv",
            candidate_columns,
            candidate_rows,
        )

        unknown_columns = [
            "object_path",
            "stage",
            "status",
            "failure_reason",
            "ambiguous_count",
            "not_recovered_count",
            "source_not_available_count",
            "stale_count",
        ]
        unknown_rows = _query_dicts(
            connection,
            """
            SELECT object_path, stage, status, failure_reason,
                   ambiguous_count, not_recovered_count,
                   source_not_available_count, stale_count
            FROM coverage
            WHERE status IN (
                    'UNKNOWN', 'AMBIGUOUS', 'NOT_RECOVERED',
                    'SOURCE_NOT_AVAILABLE', 'NOT_MEASURED', 'STALE'
                  )
               OR ambiguous_count > 0
               OR not_recovered_count > 0
               OR source_not_available_count > 0
               OR stale_count > 0
            ORDER BY stage, status, object_path
            LIMIT 100000
            """,
        )
        _write_csv_rows(
            bundle_dir / "unresolved_and_unknown.csv",
            unknown_columns,
            unknown_rows,
        )
    finally:
        connection.close()


DOMAIN_PATTERNS: dict[str, tuple[str, ...]] = {
    "global_registration": ("primalgamedata", "gamemode", "worldsettings"),
    "taming_breeding_genetics": ("taming", "baby", "gene", "mating", "dino"),
    "buff": ("buff",),
    "damage_status": ("damage", "statuscomponent", "resistance"),
    "harvest": ("harvest", "resource"),
    "inventory_crafting": ("inventory", "primalitem", "engram"),
    "loot_quality_reward": ("supplycrate", "loot", "reward", "itemset"),
    "ai_combat_riding": ("aicontroller", "behavior", "riding", "attack"),
    "mission_world_event": ("mission", "quest", "worldevent"),
    "map_world": ("map", "worldpartition", "pcg", "biome"),
    "projectile_weapon": ("projectile", "weapon"),
    "structure": ("structure",),
}


def _asset_domain(row: Mapping[str, object]) -> str:
    haystack = (
        str(row.get("object_path") or "")
        + " "
        + str(row.get("asset_class_path") or "")
        + " "
        + str(row.get("parent_class_path") or "")
    ).casefold()
    for domain, patterns in DOMAIN_PATTERNS.items():
        if any(pattern in haystack for pattern in patterns):
            return domain
    return "other"


def _select_representative_samples(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    selected: dict[str, dict[str, object]] = {}
    selected_order: list[str] = []

    def add(rows: Sequence[Mapping[str, object]], reason: str) -> None:
        for rank, row in enumerate(rows, start=1):
            object_path = str(row["object_path"])
            if object_path not in selected:
                selected_order.append(object_path)
            item = selected.setdefault(
                object_path,
                {
                    "object_path": object_path,
                    "selection_reasons": [],
                },
            )
            item["selection_reasons"].append({"reason": reason, "rank": rank})

    base_columns = """
        object_path, asset_class_path, parent_class_path,
        native_parent_class_path, descendant_count, referencer_count,
        cross_domain_reference_count, registry_usage_count,
        native_call_count, unresolved_native_call_count, capture_exists,
        evidence_freshness, graph_count, function_count,
        component_count, default_property_count, provisional_tier,
        provisional_reasons_json, identity_source_kind,
        identity_confidence, top_folder, asset_name
    """
    for field, reason in (
        ("descendant_count", "top_descendant_count"),
        ("referencer_count", "top_referencer_count"),
        ("cross_domain_reference_count", "top_cross_domain_reference"),
        (
            "native_call_count + unresolved_native_call_count",
            "top_blueprint_native_boundary",
        ),
    ):
        add(
            _query_dicts(
                connection,
                f"""
                SELECT {base_columns}
                FROM assets
                WHERE {field} > 0
                ORDER BY {field} DESC, object_path
                LIMIT 10
                """,
            ),
            reason,
        )
    add(
        _query_dicts(
            connection,
            f"""
            SELECT {base_columns}
            FROM assets
            WHERE registry_usage_count > 0
               OR lower(asset_name) LIKE '%primalgamedata%'
               OR lower(asset_name) LIKE '%gamemode%'
               OR lower(asset_name) LIKE '%worldsettings%'
            ORDER BY registry_usage_count DESC, referencer_count DESC,
                     object_path
            LIMIT 20
            """,
        ),
        "global_entry_or_registration",
    )

    all_rows = _query_dicts(
        connection,
        f"""
        SELECT {base_columns}
        FROM assets
        WHERE provisional_tier IN (1, 2, 3)
           OR capture_exists=1
        ORDER BY
            descendant_count + referencer_count
                + cross_domain_reference_count DESC,
            object_path
        LIMIT 30000
        """,
    )
    by_domain: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in all_rows:
        by_domain[_asset_domain(row)].append(row)
    for domain in DOMAIN_PATTERNS:
        candidates = by_domain.get(domain, [])
        if not candidates:
            continue
        domain_rows = [candidates[0]]
        if len(candidates) > 1:
            domain_rows.append(candidates[len(candidates) // 2])
        add(domain_rows, f"domain_{domain}")

    add(
        _query_dicts(
            connection,
            f"""
            SELECT {base_columns}
            FROM assets
            WHERE descendant_count=0
              AND referencer_count=0
              AND registry_usage_count=0
              AND native_call_count=0
              AND unresolved_native_call_count=0
            ORDER BY substr(hex(object_path), 1, 16), object_path
            LIMIT 10
            """,
        ),
        "low_centrality_leaf_counterexample",
    )
    add(
        _query_dicts(
            connection,
            f"""
            SELECT {base_columns}
            FROM assets
            WHERE identity_source_kind='unreal_asset_registry'
              AND identity_confidence='HIGH'
              AND asset_class_path <> 'UNKNOWN'
              AND (
                  is_blueprint=0
                  OR parent_class_path <> 'UNKNOWN'
                  OR native_parent_class_path <> 'UNKNOWN'
              )
              AND (
                  (lower(asset_name) LIKE '%buff%'
                   AND lower(
                       asset_class_path || ' ' || parent_class_path || ' '
                       || native_parent_class_path || ' ' || blueprint_kind
                   ) NOT LIKE '%buff%')
               OR (lower(asset_name) LIKE '%item%'
                   AND lower(
                       asset_class_path || ' ' || parent_class_path || ' '
                       || native_parent_class_path || ' ' || blueprint_kind
                   ) NOT LIKE '%item%')
               OR (lower(asset_name) LIKE '%map%'
                   AND lower(
                       asset_class_path || ' ' || parent_class_path || ' '
                       || native_parent_class_path || ' ' || blueprint_kind
                   ) NOT LIKE '%world%')
              )
            ORDER BY object_path
            LIMIT 5
            """,
        ),
        "name_or_folder_misleading",
    )
    add(
        _query_dicts(
            connection,
            f"""
            SELECT {base_columns}
            FROM assets a
            WHERE capture_exists=1
              AND evidence_freshness='FRESH'
              AND graph_count > 0
              AND default_property_count > 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM coverage c
                  WHERE c.object_path=a.object_path
                    AND (
                        c.ambiguous_count + c.not_recovered_count
                            + c.source_not_available_count + c.stale_count > 0
                        OR c.status IN (
                            'UNKNOWN', 'AMBIGUOUS', 'NOT_RECOVERED',
                            'SOURCE_NOT_AVAILABLE', 'STALE', 'NOT_MEASURED'
                        )
                    )
              )
            ORDER BY object_path
            LIMIT 5
            """,
        ),
        "complete_fresh_evidence",
    )
    add(
        _query_dicts(
            connection,
            f"""
            SELECT {base_columns}
            FROM assets a
            WHERE capture_exists=1
              AND EXISTS (
                  SELECT 1
                  FROM coverage c
                  WHERE c.object_path=a.object_path
                    AND (
                        c.ambiguous_count + c.not_recovered_count
                            + c.source_not_available_count + c.stale_count > 0
                        OR c.status IN (
                            'UNKNOWN', 'AMBIGUOUS', 'NOT_RECOVERED',
                            'SOURCE_NOT_AVAILABLE', 'STALE', 'NOT_MEASURED'
                        )
                    )
              )
            ORDER BY (
                SELECT COALESCE(SUM(
                    ambiguous_count + not_recovered_count
                    + source_not_available_count + stale_count
                ), 0)
                FROM coverage c WHERE c.object_path=a.object_path
            ) DESC, object_path
            LIMIT 5
            """,
        ),
        "high_gap_or_stale_evidence",
    )
    if not selected:
        add(
            _query_dicts(
                connection,
                f"SELECT {base_columns} FROM assets ORDER BY object_path LIMIT 1",
            ),
            "minimum_identity_sample",
        )
    rule_targets = {
        "top_descendant_count": 10,
        "top_referencer_count": 10,
        "top_cross_domain_reference": 10,
        "top_blueprint_native_boundary": 10,
        "global_entry_or_registration": 20,
        "low_centrality_leaf_counterexample": 10,
        "name_or_folder_misleading": 5,
        "complete_fresh_evidence": 5,
        "high_gap_or_stale_evidence": 5,
        **{f"domain_{domain}": 2 for domain in DOMAIN_PATTERNS},
    }
    return {
        "samples": [selected[key] for key in selected_order[:120]],
        "ruleTargets": rule_targets,
    }


def _write_representative_samples(
    bundle_dir: Path,
    database_path: Path,
) -> dict[str, object]:
    sample_root = bundle_dir / "representative_samples"
    sample_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        selection = _select_representative_samples(connection)
        samples: list[dict[str, object]] = []
        for selected in selection["samples"]:
            object_path = str(selected["object_path"])
            asset = connection.execute(
                "SELECT * FROM assets WHERE object_path=?",
                (object_path,),
            ).fetchone()
            if not asset:
                continue
            sample_id = hashlib.sha256(object_path.encode("utf-8")).hexdigest()[:16]
            sample_dir = sample_root / sample_id
            sample_dir.mkdir()
            graphs = _query_dicts(
                connection,
                """
                SELECT graph_evidence_id, graph_name, graph_type, status,
                       confidence, node_count, pin_count, wire_count,
                       native_call_count, external_asset_reference_count,
                       gap_count
                FROM graphs
                WHERE asset_object_path=?
                ORDER BY graph_name
                LIMIT 30
                """,
                (object_path,),
            )
            functions = _query_dicts(
                connection,
                """
                SELECT function_name, function_kind, graph_evidence_id,
                       replication_kind, is_pure, is_override,
                       declaring_class_path, call_count_out, call_count_in,
                       native_boundary, confidence, measurement_status
                FROM blueprint_functions
                WHERE asset_object_path=?
                ORDER BY function_name
                LIMIT 30
                """,
                (object_path,),
            )
            defaults = _query_dicts(
                connection,
                """
                SELECT property_name, property_type, declaring_class_path,
                       has_value, value_status, value_fingerprint,
                       is_object_reference, is_array, is_map, is_struct,
                       confidence
                FROM default_property_surface
                WHERE asset_object_path=?
                ORDER BY property_name
                LIMIT 100
                """,
                (object_path,),
            )
            components = _query_dicts(
                connection,
                """
                SELECT component_name, component_class_path,
                       component_object_path, is_inherited, source_property,
                       confidence, source_kind
                FROM components
                WHERE owner_object_path=?
                ORDER BY component_name
                LIMIT 50
                """,
                (object_path,),
            )
            references = _query_dicts(
                connection,
                """
                SELECT source_object_path, target_object_path, edge_kind,
                       reference_strength, source_property, source_graph,
                       source_function, source_evidence_id, confidence,
                       source_kind
                FROM asset_references
                WHERE source_object_path IN (?, ?)
                   OR target_object_path IN (?, ?)
                ORDER BY edge_kind, source_object_path, target_object_path
                LIMIT 100
                """,
                (
                    object_path,
                    str(asset["package_path"]),
                    object_path,
                    str(asset["package_path"]),
                ),
            )
            native_edges = _query_dicts(
                connection,
                """
                SELECT blueprint_function_name, native_evidence_id,
                       resolution_method, confidence, status
                FROM blueprint_native_edges
                WHERE blueprint_asset_path=?
                ORDER BY status, blueprint_function_name
                LIMIT 20
                """,
                (object_path,),
            )
            native_symbols: list[dict[str, object]] = []
            for native_id in dict.fromkeys(
                str(row.get("native_evidence_id") or "")
                for row in native_edges
                if str(row.get("native_evidence_id") or "").startswith("native://")
                and str(row.get("native_evidence_id")) != "native://UNRESOLVED"
            ):
                symbol = connection.execute(
                    """
                    SELECT native_evidence_id, module_name, binary_sha256,
                           qualified_name, simple_name, owner_class,
                           signature, rva, symbol_source, pdb_loaded,
                           decompile_status, confidence
                    FROM native_symbols
                    WHERE native_evidence_id=?
                    """,
                    (native_id,),
                ).fetchone()
                if symbol:
                    native_symbols.append(dict(symbol))
            coverage = _query_dicts(
                connection,
                """
                SELECT stage, status, confirmed_count, heuristic_count,
                       ambiguous_count, not_recovered_count,
                       source_not_available_count, stale_count, failure_reason
                FROM coverage
                WHERE object_path=?
                ORDER BY stage
                """,
                (object_path,),
            )
            identity = {
                key: value
                for key, value in dict(asset).items()
                if key
                in {
                    "object_path",
                    "package_path",
                    "asset_name",
                    "asset_class_path",
                    "blueprint_kind",
                    "generated_class_path",
                    "parent_class_path",
                    "native_parent_class_path",
                    "mount_point",
                    "top_folder",
                    "plugin_or_dlc",
                    "source_fingerprint",
                    "capture_exists",
                    "evidence_revision",
                    "evidence_freshness",
                    "parse_status",
                    "parse_confidence",
                    "provisional_tier",
                    "provisional_reasons_json",
                    "identity_source_kind",
                    "identity_confidence",
                    "identity_status",
                }
            }
            sample_payload = {
                "schema": "blueprint-to-code-kb-discovery-sample/v1",
                "sampleId": sample_id,
                "identity": identity,
                "selection": selected["selection_reasons"],
                "boundedEvidence": {
                    "graphs": graphs,
                    "functions": functions,
                    "defaults": defaults,
                    "components": components,
                    "references1Hop": references,
                    "blueprintNativeEdges": native_edges,
                    "nativeSymbols": native_symbols,
                    "coverage": coverage,
                },
                "bounds": {
                    "graphs": 30,
                    "functions": 30,
                    "defaults": 100,
                    "components": 50,
                    "references1Hop": 100,
                    "nativeEdges": 20,
                    "nativeSymbols": 20,
                },
            }
            (sample_dir / "sample.json").write_text(
                pretty_json(sample_payload),
                encoding="utf-8",
            )
            (sample_dir / "overview.md").write_text(
                "\n".join(
                    [
                        f"# Representative sample {sample_id}",
                        "",
                        f"- Object path: `{object_path}`",
                        f"- Class: `{identity.get('asset_class_path', UNKNOWN)}`",
                        f"- Evidence freshness: `{identity.get('evidence_freshness', 'NOT_AVAILABLE')}`",
                        f"- Selection reasons: `{', '.join(item['reason'] for item in selected['selection_reasons'])}`",
                        "",
                        "This file is a bounded derived summary. It does not contain an ARK package or a decompiler body.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            samples.append(
                {
                    "sampleId": sample_id,
                    "objectPath": object_path,
                    "files": [
                        f"{sample_id}/sample.json",
                        f"{sample_id}/overview.md",
                    ],
                    "selectionReasons": selected["selection_reasons"],
                }
            )
        rule_counts: Counter[str] = Counter(
            str(reason["reason"])
            for sample in samples
            for reason in sample["selectionReasons"]
        )
        rule_targets = selection["ruleTargets"]
        shortages = {
            reason: max(0, int(target) - int(rule_counts.get(reason, 0)))
            for reason, target in rule_targets.items()
        }
        connection.execute("DELETE FROM sample_membership")
        for sample in samples:
            for reason in sample["selectionReasons"]:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO sample_membership(
                        object_path, selection_reason, source_rank
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        sample["objectPath"],
                        reason["reason"],
                        reason["rank"],
                    ),
                )
        connection.commit()
        manifest = {
            "schema": "blueprint-to-code-kb-discovery-samples/v1",
            "selectionVersion": "1.1.0",
            "sampleCount": len(samples),
            "maximumUniqueAssets": 120,
            "ruleTargets": rule_targets,
            "ruleCounts": dict(sorted(rule_counts.items())),
            "shortages": shortages,
            "selectionRules": [
                "top 10 descendants",
                "top 10 referencers",
                "top 10 cross-domain references",
                "top 10 Blueprint/native boundaries",
                "global entries and registrations",
                "two representatives per major domain when available",
                "10 low-centrality leaves",
                "5 name/folder classification conflicts when proven by Registry",
                "5 complete fresh Evidence Stores",
                "5 high-gap or stale Evidence Stores",
            ],
            "samples": samples,
        }
        (sample_root / "sample_manifest.json").write_text(
            pretty_json(manifest),
            encoding="utf-8",
        )
        return manifest
    finally:
        connection.close()


def _database_report_metrics(database_path: Path) -> dict[str, object]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        asset = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(is_blueprint) AS blueprints,
                   SUM(is_data_asset) AS data_assets,
                   SUM(is_data_table) AS data_tables,
                   SUM(is_map) AS maps,
                   SUM(CASE WHEN asset_class_path<>'UNKNOWN' THEN 1 ELSE 0 END)
                       AS real_class,
                   SUM(CASE WHEN parent_class_path<>'UNKNOWN' THEN 1 ELSE 0 END)
                       AS parent_class,
                   SUM(CASE WHEN native_parent_class_path<>'UNKNOWN' THEN 1 ELSE 0 END)
                       AS native_parent,
                   SUM(CASE WHEN identity_source_kind='filesystem_metadata'
                       THEN 1 ELSE 0 END) AS filesystem_only
            FROM assets
            """
        ).fetchone()
        total = int(asset["total"] or 0)
        edge_distribution = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT edge_kind, COUNT(*)
                FROM asset_references
                GROUP BY edge_kind
                ORDER BY COUNT(*) DESC, edge_kind
                """
            )
        }
        top_descendants = _query_dicts(
            connection,
            """
            SELECT object_path, descendant_count
            FROM assets
            WHERE descendant_count>0
            ORDER BY descendant_count DESC, object_path
            LIMIT 10
            """,
        )
        top_referencers = _query_dicts(
            connection,
            """
            SELECT object_path, referencer_count
            FROM assets
            WHERE referencer_count>0
            ORDER BY referencer_count DESC, object_path
            LIMIT 10
            """,
        )
        top_cross_domain = _query_dicts(
            connection,
            """
            SELECT object_path, cross_domain_reference_count
            FROM assets
            WHERE cross_domain_reference_count>0
            ORDER BY cross_domain_reference_count DESC, object_path
            LIMIT 10
            """,
        )
        native_class_counts = _query_dicts(
            connection,
            """
            SELECT owner_class, COUNT(*) AS symbol_count
            FROM native_symbols
            GROUP BY owner_class
            ORDER BY symbol_count DESC, owner_class
            LIMIT 10
            """,
        )
        native_function_counts = _query_dicts(
            connection,
            """
            SELECT n.owner_class, n.simple_name,
                   COUNT(b.edge_id) AS boundary_count,
                   SUM(CASE WHEN b.status IN (
                       'CONFIRMED', 'VERIFIED', 'RESOLVED'
                   ) THEN 1 ELSE 0 END) AS confirmed_count,
                   SUM(CASE WHEN b.status IN (
                       'NAME_ONLY_CANDIDATE', 'AMBIGUOUS', 'UNRESOLVED'
                   ) THEN 1 ELSE 0 END) AS unresolved_count
            FROM native_symbols n
            JOIN blueprint_native_edges b
              ON b.native_evidence_id=n.native_evidence_id
            GROUP BY n.owner_class, n.simple_name
            ORDER BY boundary_count DESC, n.owner_class, n.simple_name
            LIMIT 15
            """,
        )
        registration_types = _query_dicts(
            connection,
            """
            SELECT registration_type, COUNT(*) AS registration_count,
                   COUNT(DISTINCT owner_object_path) AS owner_count
            FROM system_registrations
            GROUP BY registration_type
            ORDER BY registration_count DESC, registration_type
            """,
        )
        registration_owners = _query_dicts(
            connection,
            """
            SELECT owner_object_path, COUNT(*) AS registration_count
            FROM system_registrations
            GROUP BY owner_object_path
            ORDER BY registration_count DESC, owner_object_path
            LIMIT 15
            """,
        )
        freshness = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT evidence_freshness, COUNT(*)
                FROM assets
                WHERE capture_exists=1
                GROUP BY evidence_freshness
                ORDER BY evidence_freshness
                """
            )
        }
        tiers = {
            int(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT provisional_tier, COUNT(*)
                FROM assets
                GROUP BY provisional_tier
                ORDER BY provisional_tier
                """
            )
        }
        graph_statuses = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT status, COUNT(*) FROM graphs
                GROUP BY status ORDER BY status
                """
            )
        }
        blueprint_coverage_statuses = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT status, COUNT(*) FROM coverage
                WHERE stage='blueprint_evidence'
                GROUP BY status ORDER BY status
                """
            )
        }
        table_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT database_name, COUNT(*)
                FROM existing_knowledge_tables
                GROUP BY database_name
                ORDER BY database_name
                """
            )
        }
        existing_database_details = _query_dicts(
            connection,
            """
            SELECT database_name, COUNT(*) AS table_count,
                   SUM(row_count) AS row_count,
                   MAX(source_asset_count) AS source_asset_count,
                   MAX(CASE WHEN distinct_asset_count >= 0
                       THEN distinct_asset_count ELSE NULL END)
                       AS conservative_asset_coverage,
                   SUM(CASE WHEN distinct_asset_count >= 0
                       THEN distinct_asset_count ELSE 0 END)
                       AS measured_table_asset_rows,
                   SUM(CASE WHEN distinct_asset_count >= 0
                       THEN 1 ELSE 0 END) AS measurable_tables
            FROM existing_knowledge_tables
            GROUP BY database_name
            ORDER BY database_name
            """,
        )
        existing_rows = int(
            connection.execute(
                "SELECT COALESCE(SUM(row_count), 0) FROM existing_knowledge_tables"
            ).fetchone()[0]
        )
        class_nodes = {
            str(row[0])
            for row in connection.execute("SELECT child_class_path FROM class_edges")
        }
        broken = 0
        unknown_roots = 0
        for row in connection.execute(
            "SELECT DISTINCT parent_class_path FROM class_edges"
        ):
            parent = str(row[0])
            if parent.startswith("/Script/"):
                continue
            if parent not in class_nodes:
                broken += 1
                unknown_roots += 1
        coverage_gaps = connection.execute(
            """
            SELECT COALESCE(SUM(ambiguous_count), 0),
                   COALESCE(SUM(not_recovered_count), 0),
                   COALESCE(SUM(source_not_available_count), 0),
                   COALESCE(SUM(stale_count), 0)
            FROM coverage
            """
        ).fetchone()
        execution_stats = {
            str(row[0]): int(row[1] or 0)
            for row in connection.execute(
                """
                SELECT key, value FROM metadata
                WHERE key IN (
                    'blueprint_store_discovered',
                    'blueprint_store_failures',
                    'serialized_reference_failures',
                    'native_store_candidates',
                    'native_store_selected',
                    'native_store_failures'
                )
                """
            )
        }
        return {
            "assets": {
                "total": total,
                "blueprints": int(asset["blueprints"] or 0),
                "dataAssets": int(asset["data_assets"] or 0),
                "dataTables": int(asset["data_tables"] or 0),
                "maps": int(asset["maps"] or 0),
                "realClass": int(asset["real_class"] or 0),
                "parentClass": int(asset["parent_class"] or 0),
                "nativeParent": int(asset["native_parent"] or 0),
                "filesystemOnly": int(asset["filesystem_only"] or 0),
            },
            "classEdges": int(
                connection.execute("SELECT COUNT(*) FROM class_edges").fetchone()[0]
            ),
            "brokenClassLinks": broken,
            "unknownClassRoots": unknown_roots,
            "inheritanceCycles": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM coverage
                    WHERE stage='inheritance_graph'
                      AND failure_reason='INHERITANCE_CYCLE'
                    """
                ).fetchone()[0]
            ),
            "referenceEdges": int(
                connection.execute("SELECT COUNT(*) FROM asset_references").fetchone()[
                    0
                ]
            ),
            "referenceDistribution": edge_distribution,
            "registrations": int(
                connection.execute(
                    "SELECT COUNT(*) FROM system_registrations"
                ).fetchone()[0]
            ),
            "registrationTypes": registration_types,
            "registrationOwners": registration_owners,
            "nativeSymbols": int(
                connection.execute("SELECT COUNT(*) FROM native_symbols").fetchone()[0]
            ),
            "blueprintNativeEdges": int(
                connection.execute(
                    "SELECT COUNT(*) FROM blueprint_native_edges"
                ).fetchone()[0]
            ),
            "confirmedBlueprintNativeEdges": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM blueprint_native_edges
                    WHERE status='CONFIRMED'
                    """
                ).fetchone()[0]
            ),
            "nativeFieldAccesses": int(
                connection.execute(
                    "SELECT COUNT(*) FROM native_field_accesses"
                ).fetchone()[0]
            ),
            "topDescendants": top_descendants,
            "topReferencers": top_referencers,
            "topCrossDomain": top_cross_domain,
            "topNativeClasses": native_class_counts,
            "topNativeFunctions": native_function_counts,
            "freshness": freshness,
            "graphStatuses": graph_statuses,
            "blueprintCoverageStatuses": blueprint_coverage_statuses,
            "existingDatabases": len(table_counts),
            "existingDatabaseTableCounts": table_counts,
            "existingDatabaseDetails": existing_database_details,
            "existingRows": existing_rows,
            "querySnapshots": int(
                connection.execute("SELECT COUNT(*) FROM query_corpus").fetchone()[0]
            ),
            "tiers": tiers,
            "coverageGaps": {
                "ambiguous": int(coverage_gaps[0]),
                "notRecovered": int(coverage_gaps[1]),
                "sourceNotAvailable": int(coverage_gaps[2]),
                "stale": int(coverage_gaps[3]),
            },
            "executionStats": execution_stats,
        }
    finally:
        connection.close()


def _percent(numerator: int, denominator: int) -> str:
    if not denominator:
        return "0.000%"
    return f"{numerator * 100.0 / denominator:.3f}%"


def _format_ranked(
    rows: Sequence[Mapping[str, object]],
    metric: str,
) -> str:
    if not rows:
        return "- 当前没有可确认记录。"
    return "\n".join(
        f"- `{row['object_path']}` — {metric}={row[metric]}" for row in rows
    )


def _write_discovery_report(
    bundle_dir: Path,
    metrics: Mapping[str, object],
    *,
    registry_available: bool,
    registry_dependency_available: bool,
) -> None:
    assets = metrics["assets"]
    reference_distribution = metrics["referenceDistribution"]
    distribution_text = (
        ", ".join(f"`{kind}`={count}" for kind, count in reference_distribution.items())
        or "当前没有可恢复引用边"
    )
    tier_text = ", ".join(
        f"Tier {tier}={count}" for tier, count in metrics["tiers"].items()
    )
    freshness_text = (
        ", ".join(
            f"`{status}`={count}" for status, count in metrics["freshness"].items()
        )
        or "无 capture"
    )
    database_text = (
        ", ".join(
            f"`{database}`={table_count} tables"
            for database, table_count in metrics["existingDatabaseTableCounts"].items()
        )
        or "未发现"
    )
    registration_type_text = (
        "\n".join(
            "- `{registration_type}` — 关系={registration_count}，owner={owner_count}".format(
                **row
            )
            for row in metrics["registrationTypes"]
        )
        or "- 当前没有可确认注册关系。"
    )
    registration_owner_text = (
        "\n".join(
            f"- `{row['owner_object_path']}` — registrations={row['registration_count']}"
            for row in metrics["registrationOwners"]
        )
        or "- 当前没有可确认全局入口。"
    )
    native_class_text = (
        "\n".join(
            f"- `{row['owner_class']}` — bounded symbols={row['symbol_count']}"
            for row in metrics["topNativeClasses"]
        )
        or "- 当前没有 bounded native class 记录。"
    )
    native_function_text = (
        "\n".join(
            "- `{owner_class}::{simple_name}` — 边界={boundary_count}，"
            "confirmed={confirmed_count}，candidate/unresolved={unresolved_count}".format(
                **row
            )
            for row in metrics["topNativeFunctions"]
        )
        or "- 当前没有 Blueprint→native 函数候选。"
    )
    database_detail_text = (
        "\n".join(
            "- `{database_name}` — tables={table_count}，rows={row_count}，"
            "read-source assets={source_asset_count}（占全量 {coverage}），"
            "保守可测资产覆盖={conservative_asset_coverage}（占全量 {conservative_coverage}），"
            "可测 distinct-asset 表={measurable_tables}".format(
                coverage=_percent(
                    int(row["source_asset_count"]),
                    int(assets["total"]),
                ),
                conservative_coverage=_percent(
                    int(row["conservative_asset_coverage"] or 0),
                    int(assets["total"]),
                ),
                **row,
            )
            for row in metrics["existingDatabaseDetails"]
        )
        or "- 当前没有可枚举的现有知识库。"
    )
    execution = metrics["executionStats"]
    lines = [
        "# ARK Knowledge Base Discovery Report",
        "",
        "本报告描述可复现的范围发现结果，不执行现有知识库迁移，也不把 provisional tier 当成最终分类。",
        "",
        "## 1. 全量目录规模",
        "",
        f"- 总资产：{assets['total']}",
        f"- Blueprint：{assets['blueprints']}",
        f"- Data Asset：{assets['dataAssets']}",
        f"- Data Table：{assets['dataTables']}",
        f"- Map / World：{assets['maps']}",
        "",
        "## 2. 真实身份与父类覆盖",
        "",
        f"- 真实 AssetClass：{assets['realClass']} / {assets['total']} ({_percent(assets['realClass'], assets['total'])})",
        f"- ParentClass：{assets['parentClass']} / {assets['total']} ({_percent(assets['parentClass'], assets['total'])})",
        f"- NativeParent：{assets['nativeParent']} / {assets['total']} ({_percent(assets['nativeParent'], assets['total'])})",
        f"- Asset Registry 状态：{'可用，作为权威身份来源' if registry_available else 'SOURCE_NOT_AVAILABLE；序列化证据与显式 UNKNOWN 为后备'}",
        "",
        "## 3. 名称/目录启发式比例",
        "",
        f"- 仅有 filesystem package identity、没有真实类型来源：{assets['filesystemOnly']} / {assets['total']} ({_percent(assets['filesystemOnly'], assets['total'])})。",
        "- 名称和目录没有被提升为确认的 `asset_class_path`；对应字段保持 `UNKNOWN`，并在 `coverage` 记录。",
        "",
        "## 4. 继承图质量",
        "",
        f"- 类边：{metrics['classEdges']}",
        f"- 断链：{metrics['brokenClassLinks']}",
        f"- 循环：{metrics['inheritanceCycles']}",
        f"- 未知根：{metrics['unknownClassRoots']}",
        "",
        "## 5. 引用图",
        "",
        f"- 总引用边：{metrics['referenceEdges']}",
        f"- Asset Registry dependency 状态：{'完整快照已导入' if registry_dependency_available else 'SOURCE_NOT_AVAILABLE 或未完成；现有 Evidence Store 引用仍保留'}",
        "- Registry 依赖保持 package→package 粒度；不会把一包多资产展开成虚假的对象笛卡尔积。",
        f"- 类型分布：{distribution_text}",
        "",
        "## 6. 最常被继承、引用与跨领域资产",
        "",
        "### 后代数",
        "",
        _format_ranked(metrics["topDescendants"], "descendant_count"),
        "",
        "### Referencer 数",
        "",
        _format_ranked(metrics["topReferencers"], "referencer_count"),
        "",
        "### 跨领域引用",
        "",
        _format_ranked(
            metrics["topCrossDomain"],
            "cross_domain_reference_count",
        ),
        "",
        "## 7. 全局入口和系统注册器",
        "",
        f"- `system_registrations`：{metrics['registrations']} 条。",
        "- 注册关系来自现有知识库的可验证派生表；Registry 依赖本身没有被误标为业务注册。",
        "",
        "### 注册类型",
        "",
        registration_type_text,
        "",
        "### 主要 owner / 全局入口",
        "",
        registration_owner_text,
        "",
        "## 8. Blueprint → native 边界",
        "",
        f"- bounded native symbols：{metrics['nativeSymbols']}",
        f"- Blueprint→native 边（含候选与 unresolved）：{metrics['blueprintNativeEdges']}",
        f"- confirmed 边：{metrics['confirmedBlueprintNativeEdges']}",
        f"- native field accesses：{metrics['nativeFieldAccesses']}",
        "- `exact_simple_name_candidate` 仅为 LOW/name-only candidate；没有 owner/signature/callsite 闭合时不会标成 confirmed。",
        "",
        "### bounded native 类",
        "",
        native_class_text,
        "",
        "### 最常见函数边界",
        "",
        native_function_text,
        "",
        "## 9. 现有知识库覆盖",
        "",
        f"- 数据库：{metrics['existingDatabases']}；表：{sum(metrics['existingDatabaseTableCounts'].values())}；总行：{metrics['existingRows']}。",
        f"- 明细：{database_text}",
        "- 无法从现有 schema 测量 distinct asset、stale 或 duplicate 时，状态列保持 `UNKNOWN`，不会把缺失测量写成事实零。",
        "",
        database_detail_text,
        "",
        "## 10. Evidence Store 新鲜度与缺口",
        "",
        f"- Blueprint freshness：{freshness_text}",
        f"- Blueprint coverage：{', '.join(f'`{key}`={value}' for key, value in metrics['blueprintCoverageStatuses'].items()) or '无记录'}",
        f"- 图状态：{', '.join(f'`{key}`={value}' for key, value in metrics['graphStatuses'].items()) or '无图'}",
        f"- AMBIGUOUS={metrics['coverageGaps']['ambiguous']}，NOT_RECOVERED={metrics['coverageGaps']['notRecovered']}，SOURCE_NOT_AVAILABLE={metrics['coverageGaps']['sourceNotAvailable']}，STALE={metrics['coverageGaps']['stale']}。",
        f"- Blueprint stores：发现={execution.get('blueprint_store_discovered', 0)}，加载失败={execution.get('blueprint_store_failures', 0)}；serialized reference 失败={execution.get('serialized_reference_failures', 0)}。",
        f"- Native stores：候选={execution.get('native_store_candidates', 0)}，选用={execution.get('native_store_selected', 0)}，失败={execution.get('native_store_failures', 0)}。",
        "",
        "## 11. 当前分类明显遗漏的资产类型",
        "",
        "- 旧分类以主题关键词为中心，无法可靠区分 BlueprintGeneratedClass、接口、函数库、Data Asset/Table、World/PCG、表现资产与一包多资产。",
        "- Registry 缺失时，材质、动画、声音、网格等仍在 Tier 0 文件目录，但类型显式 UNKNOWN；不会用扩展名外的文件名猜测。",
        "",
        "## 12. 硬编码主题偏差",
        "",
        "- 仅围绕熟悉的生物、Buff、采集或掉落关键词会高估已研究叶子资产，并漏掉高复用父类、组件、接口、全局注册器和地图入口。",
        "- 本 Bundle 另存后代数、referencer、跨领域、注册使用、native 边界、查询命中和深读成本等原始特征，供后续重新定权。",
        "- 名称命中只能增加 LOW-confidence hint，不能在没有结构证据时单独把资产提升到 Tier 1。",
        "",
        "## 13. Provisional Tier 0–4",
        "",
        f"- 当前建议：{tier_text}；Tier 4 是可失效的查询快照，资产表中通常不直接分配。",
        f"- Tier 4 查询快照候选：{metrics['querySnapshots']} 条 `query_corpus` 问题；它们是可重建范围输入，不是唯一事实来源。",
        "- 这是 discovery 建议，不迁移、不删除、不合并任何现有数据库。",
        "",
        "## 14. 发现工具盲区与下一步",
        "",
        "- Asset Registry 只能提供身份、标签与 package 依赖，不能替代 Blueprint 图、组件/default 深读。",
        "- 已有 capture 之外的图、默认值、组件和业务注册关系尚未全量深读。",
        "- map direct、PCG、World Partition、runtime-only 状态仍需要专门 DevKit API 或按需解析。",
        "- native 仅覆盖既有 bounded recipes；完整反编译文本故意不进入 Bundle。",
        "- confirmed Blueprint→native 仍需 callsite、owner/signature、PDB/Ghidra 联合闭合。",
        "- `unresolved_and_unknown.csv` 是有界预览；完整逐资产状态在 SQLite `assets` 与 `coverage` 中。",
        "",
    ]
    (bundle_dir / "discovery_report.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def _git_value(project_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return UNKNOWN
    return completed.stdout.strip() if completed.returncode == 0 else UNKNOWN


def _content_fingerprint(
    assets: Mapping[str, Mapping[str, object]],
) -> str:
    digest = hashlib.sha256()
    for object_path in sorted(assets, key=str.casefold):
        digest.update(object_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(
            str(assets[object_path].get("source_fingerprint") or "").encode(
                "ascii",
                errors="ignore",
            )
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _select_current_native_provenance(
    current_binary_sha: str,
    current_pdb_sha: str,
    native_payloads: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], str, bool]:
    matching = next(
        (
            dict(payload)
            for payload in native_payloads
            if current_binary_sha
            and str(payload.get("binary_sha256") or "").casefold()
            == current_binary_sha.casefold()
        ),
        None,
    )
    if matching is None:
        return (
            {},
            (
                "CURRENT_BINARY_WITHOUT_MATCHING_EVIDENCE"
                if current_binary_sha
                else "CURRENT_BINARY_NOT_AVAILABLE"
            ),
            False,
        )
    pdb_matches = bool(
        current_pdb_sha
        and str(matching.get("pdb_sha256") or "").casefold()
        == current_pdb_sha.casefold()
    )
    return (
        matching,
        (
            "CURRENT_BINARY_AND_PDB_MATCHED"
            if pdb_matches
            else "CURRENT_BINARY_MATCHED_PDB_NOT_MATCHED"
        ),
        pdb_matches,
    )


def _write_manifest_and_readme(
    *,
    project_root: Path,
    content_root: Path,
    bundle_dir: Path,
    assets: Mapping[str, Mapping[str, object]],
    database_counts: Mapping[str, int],
    scan_result: Mapping[str, object],
    registry_assets: RegistryAssetStream | Sequence[Mapping[str, object]],
    registry_dependencies: RegistryDependencyStream | Sequence[Mapping[str, object]],
    registry_manifest: Mapping[str, object],
    blueprint_stats: Mapping[str, object],
    native_payloads: Sequence[Mapping[str, object]],
    native_stats: Mapping[str, int],
    sample_manifest: Mapping[str, object],
    generated_at: str,
) -> dict[str, object]:
    devkit_root = content_root.resolve().parents[2]
    native_binary = (
        devkit_root
        / "Engine"
        / "Binaries"
        / "Win64"
        / "ShooterGameEditor-ShooterGame.dll"
    )
    native_pdb = native_binary.with_suffix(".pdb")
    current_binary_sha = sha256_file(native_binary) if native_binary.is_file() else ""
    current_pdb_sha = sha256_file(native_pdb) if native_pdb.is_file() else ""
    (
        primary_native,
        native_match_status,
        pdb_matches_current,
    ) = _select_current_native_provenance(
        current_binary_sha,
        current_pdb_sha,
        native_payloads,
    )
    cache_source_paths = _extractor_cache_source_paths()
    cache_source_path_set = set(cache_source_paths)
    extractor_paths = [
        *cache_source_paths,
        project_root
        / "scripts"
        / "devkit_exporters"
        / "export_kb_registry_snapshot.py",
        project_root / "scripts" / "export_kb_discovery_bundle.py",
    ]
    unique_extractor_paths = list(
        dict.fromkeys(path.resolve() for path in extractor_paths if path.is_file())
    )
    extractors = [
        {
            "name": path.name,
            "version": TOOL_VERSION,
            "sourceSha256": sha256_file(path),
            "cacheIdentityContributor": path in cache_source_path_set,
        }
        for path in unique_extractor_paths
    ]
    limitations = [
        "No ARK package, DLL, PDB, Ghidra project, full capture, or decompiler body is included.",
        "Blueprint graphs/defaults/components are summarized only for existing Evidence Stores.",
        "Native evidence is bounded by existing recipes; name-only joins are not confirmed.",
    ]
    if not registry_assets:
        limitations.append(
            "Unreal Asset Registry snapshot was unavailable; filesystem and serialized evidence are explicit fallbacks."
        )
    if not registry_dependencies:
        limitations.append(
            "Full Asset Registry package dependency graph was unavailable."
        )
    if native_payloads and not primary_native:
        limitations.append(
            "Native Evidence Stores exist, but none match the current ShooterGame DLL; "
            "their PDB/Ghidra/Java provenance is not projected onto the current DevKit snapshot."
        )
    manifest = {
        "schema": DISCOVERY_SCHEMA,
        "generatedAtUtc": generated_at,
        "repositoryCommit": _git_value(project_root, "rev-parse", "HEAD"),
        "repositoryBranch": _git_value(project_root, "branch", "--show-current"),
        "repositoryDirty": bool(
            _git_value(project_root, "status", "--porcelain") not in {"", UNKNOWN}
        ),
        "devkitSnapshot": {
            "contentRootRedacted": True,
            "assetCount": len(assets),
            "uassetCount": int(scan_result.get("uassetCount") or 0),
            "mapCount": int(scan_result.get("mapCount") or 0),
            "contentFingerprint": _content_fingerprint(assets),
            "registryAssetCount": len(registry_assets),
            "registryDependencyCount": len(registry_dependencies),
            "registrySnapshotFingerprint": str(
                registry_manifest.get("inventory_signature")
                or registry_manifest.get("content_fingerprint")
                or UNKNOWN
            ),
            "registrySnapshotProducerSourceSha256": str(
                (
                    registry_manifest.get("producer")
                    if isinstance(
                        registry_manifest.get("producer"),
                        Mapping,
                    )
                    else {}
                ).get("source_sha256")
                or UNKNOWN
            ),
            "engineVersion": str(
                (
                    registry_manifest.get("source")
                    if isinstance(registry_manifest.get("source"), Mapping)
                    else {}
                ).get("engine_version")
                or UNKNOWN
            ),
            "shooterGameDllSha256": str(current_binary_sha or UNKNOWN),
            "shooterGamePdbSha256": str(current_pdb_sha or UNKNOWN),
            "pdbGuidAge": str(
                (primary_native.get("pdb_guid_age") if pdb_matches_current else "")
                or UNKNOWN
            ),
            "ghidraVersion": str(primary_native.get("ghidra_version") or UNKNOWN),
            "javaVersion": str(primary_native.get("java_version") or UNKNOWN),
            "nativeEvidenceMatchStatus": native_match_status,
        },
        "extractors": extractors,
        "counts": {
            **database_counts,
            "representativeSamples": int(sample_manifest.get("sampleCount") or 0),
            "blueprintEvidenceStores": int(blueprint_stats.get("discovered") or 0),
            "serializedBlueprintReferenceEdges": int(
                blueprint_stats.get("serializedReferenceCount") or 0
            ),
            "nativeEvidenceStores": int(native_stats.get("selectedStores") or 0),
        },
        "incrementalRun": {
            "resumed": bool(scan_result.get("resumed")),
            "added": int(scan_result.get("added") or 0),
            "changed": int(scan_result.get("changed") or 0),
            "deleted": int(scan_result.get("deleted") or 0),
            "blueprintCacheHits": int(blueprint_stats.get("cacheHits") or 0),
            "serializedReferenceCacheHits": int(
                blueprint_stats.get("serializedReferenceCacheHits") or 0
            ),
            "nativeCacheHits": int(native_stats.get("cacheHits") or 0),
        },
        "knownLimitations": limitations,
    }
    (bundle_dir / "discovery_manifest.json").write_text(
        pretty_json(manifest),
        encoding="utf-8",
    )
    readme = [
        "# Knowledge Base Discovery Bundle",
        "",
        "这是范围发现包，不是最终知识库迁移结果。它用于判断哪些 ARK 背景事实应持久化到什么深度，以及何时需要重新调用解析器。",
        "",
        "## 阅读顺序",
        "",
        "1. 先读 `discovery_report.md`，了解覆盖、缺口与 provisional tier。",
        "2. 用 `kb_discovery.sqlite` 做结构化分析；schema 在 `kb_discovery_schema.sql`。",
        "3. 用 `top_background_candidates.csv` 检视可解释的中心性特征。",
        "4. 用 `query_corpus.jsonl` 检查未来问题对证据层的需求。",
        "5. 用 `representative_samples/sample_manifest.json` 进入有界样例。",
        "",
        "## 关键语义",
        "",
        "- `UNKNOWN`、`AMBIGUOUS`、`NOT_RECOVERED` 和 `SOURCE_NOT_AVAILABLE` 是证据状态，不等于 false 或 0。",
        "- `provisional_tier` 仅为候选建议，不可用作自动迁移决定。",
        "- `NAME_ONLY_CANDIDATE` 不是 confirmed Blueprint→native 边。",
        "- 完整资产目录、关系与覆盖状态在 SQLite；CSV 是便于人工审阅的有界视图。",
        "",
        "## 完整性",
        "",
        "- `SHA256SUMS.txt` 覆盖 Bundle 内除其自身外的每个文件。",
        "- ZIP 使用单一根目录 `discovery_bundle/`。",
        "- SQLite 可能超过 4 GiB；传输和解压需使用支持 ZIP64 的工具、支持大文件的文件系统（不要使用 FAT32），并预留至少约两倍解压空间。",
        "- Bundle 不含 ARK 原始包、游戏二进制、符号文件、Ghidra 工程、完整 capture 或完整伪 C。",
        "",
        "## 重建命令",
        "",
        "```powershell",
        "runtime\\python\\python.exe scripts\\export_kb_discovery_bundle.py --output knowledge_base\\discovery_bundle --include-existing-evidence --include-native-boundaries --build-zip",
        "```",
        "",
    ]
    (bundle_dir / "README_FOR_REVIEW.md").write_text(
        "\n".join(readme),
        encoding="utf-8",
    )
    return manifest


def _unsafe_text_reasons(value: str) -> list[str]:
    reasons: list[str] = []
    for pattern in ABSOLUTE_PATH_PATTERNS:
        if pattern.search(value):
            reasons.append("absolute_path")
            break
    for pattern in SECRET_PATTERNS:
        if pattern.search(value):
            reasons.append("secret_pattern")
            break
    return reasons


def write_sha256sums(bundle_dir: Path) -> Path:
    sums_path = bundle_dir / "SHA256SUMS.txt"
    files = sorted(
        (
            path
            for path in bundle_dir.rglob("*")
            if path.is_file() and path != sums_path
        ),
        key=lambda path: path.relative_to(bundle_dir).as_posix(),
    )
    lines = [
        f"{sha256_file(path)}  {path.relative_to(bundle_dir).as_posix()}"
        for path in files
    ]
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums_path


def _build_zip(bundle_dir: Path, zip_path: Path) -> None:
    temporary = zip_path.with_suffix(zip_path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for path in sorted(
            (item for item in bundle_dir.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(bundle_dir).as_posix(),
        ):
            relative = path.relative_to(bundle_dir).as_posix()
            archive.write(path, f"{ARCHIVE_ROOT}/{relative}")
    os.replace(temporary, zip_path)


def _verify_sqlite_privacy(database_path: Path) -> list[str]:
    errors: list[str] = []
    connection = sqlite3.connect(database_path)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            errors.append(f"sqlite_integrity:{integrity}")
        connection.create_function(
            "HAS_UNSAFE_TEXT",
            -1,
            lambda *values: int(
                any(_unsafe_text_reasons(str(value or "")) for value in values)
            ),
        )
        tables = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        for table in tables:
            columns = [
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({_quote_identifier(table)})"
                )
                if str(row[2]).upper().startswith("TEXT")
            ]
            if not columns:
                continue
            selected_columns = ", ".join(
                _quote_identifier(column) for column in columns
            )
            row = connection.execute(
                f"""
                SELECT {selected_columns}
                FROM {_quote_identifier(table)}
                WHERE HAS_UNSAFE_TEXT({selected_columns})
                LIMIT 1
                """
            ).fetchone()
            if row:
                for column, value in zip(columns, row):
                    if _unsafe_text_reasons(str(value or "")):
                        errors.append(f"sqlite_sensitive_text:{table}.{column}")
    finally:
        connection.close()
    return errors


def verify_discovery_bundle(
    bundle_dir: Path,
    *,
    zip_path: Path | None = None,
) -> dict[str, object]:
    bundle_dir = bundle_dir.resolve()
    errors: list[str] = []
    required = {
        "README_FOR_REVIEW.md",
        "discovery_manifest.json",
        "kb_discovery.sqlite",
        "kb_discovery_schema.sql",
        "discovery_report.md",
        "asset_inventory_preview.csv",
        "top_background_candidates.csv",
        "unresolved_and_unknown.csv",
        "query_corpus.jsonl",
        "representative_samples/sample_manifest.json",
        "SHA256SUMS.txt",
    }
    files = {
        path.relative_to(bundle_dir).as_posix(): path
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }
    missing = sorted(required - set(files))
    errors.extend(f"missing:{name}" for name in missing)
    for relative, path in files.items():
        if path.suffix.casefold() in FORBIDDEN_ARCHIVE_SUFFIXES:
            errors.append(f"forbidden_file:{relative}")
        if path.suffix.casefold() == ".sqlite":
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError):
            errors.append(f"non_text_payload:{relative}")
            continue
        reasons = _unsafe_text_reasons(text)
        errors.extend(f"{reason}:{relative}" for reason in reasons)

    sums_path = bundle_dir / "SHA256SUMS.txt"
    expected_sums: dict[str, str] = {}
    if sums_path.is_file():
        for line in sums_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or "  " not in line:
                errors.append("invalid_sha256sums_line")
                continue
            digest, relative = line.split("  ", 1)
            expected_sums[relative] = digest
        expected_members = set(files) - {"SHA256SUMS.txt"}
        if set(expected_sums) != expected_members:
            errors.append("sha256_member_set_mismatch")
        for relative, digest in expected_sums.items():
            path = files.get(relative)
            if path and sha256_file(path) != digest:
                errors.append(f"sha256_mismatch:{relative}")
    if (bundle_dir / "kb_discovery.sqlite").is_file():
        errors.extend(_verify_sqlite_privacy(bundle_dir / "kb_discovery.sqlite"))
    zip_verified = zip_path is None
    zip64_members: list[dict[str, object]] = []
    if zip_path is not None:
        if not zip_path.is_file():
            errors.append("zip_missing")
        else:
            with zipfile.ZipFile(zip_path) as archive:
                archive_files = {
                    name for name in archive.namelist() if not name.endswith("/")
                }
                expected_archive = {f"{ARCHIVE_ROOT}/{relative}" for relative in files}
                if archive_files != expected_archive:
                    errors.append("zip_member_set_mismatch")
                archive_sums: dict[str, str] = {}
                sums_member = f"{ARCHIVE_ROOT}/SHA256SUMS.txt"
                if sums_member in archive_files:
                    try:
                        sums_text = archive.read(sums_member).decode("utf-8")
                        for line in sums_text.splitlines():
                            if "  " not in line:
                                errors.append("zip_invalid_sha256sums_line")
                                continue
                            digest, relative = line.split("  ", 1)
                            archive_sums[relative] = digest
                    except (KeyError, UnicodeError, zipfile.BadZipFile):
                        errors.append("zip_sha256sums_unreadable")
                if archive_sums != expected_sums:
                    errors.append("zip_sha256sums_mismatch")
                for name in archive_files:
                    suffix = Path(name).suffix.casefold()
                    if suffix in FORBIDDEN_ARCHIVE_SUFFIXES:
                        errors.append(f"zip_forbidden_file:{name}")
                    relative = name.removeprefix(f"{ARCHIVE_ROOT}/")
                    local_path = files.get(relative)
                    info = archive.getinfo(name)
                    if local_path and info.file_size != local_path.stat().st_size:
                        errors.append(f"zip_size_mismatch:{name}")
                    if (
                        info.file_size > zipfile.ZIP64_LIMIT
                        and info.extract_version < 45
                    ):
                        errors.append(f"zip64_required:{name}")
                    if info.file_size > zipfile.ZIP64_LIMIT:
                        zip64_members.append(
                            {
                                "name": name,
                                "fileSize": info.file_size,
                                "compressedSize": info.compress_size,
                                "extractVersion": info.extract_version,
                            }
                        )
                    digest = hashlib.sha256()
                    try:
                        with archive.open(info) as handle:
                            for chunk in iter(
                                lambda: handle.read(4 * 1024 * 1024),
                                b"",
                            ):
                                digest.update(chunk)
                    except (OSError, EOFError, zipfile.BadZipFile):
                        errors.append(f"zip_crc_or_read:{name}")
                        continue
                    expected_digest = (
                        sha256_file(local_path)
                        if relative == "SHA256SUMS.txt" and local_path
                        else expected_sums.get(relative)
                    )
                    if expected_digest and digest.hexdigest() != expected_digest:
                        errors.append(f"zip_sha256_mismatch:{name}")
            zip_verified = not any(error.startswith("zip_") for error in errors)
    return {
        "passed": not errors,
        "errors": errors,
        "fileCount": len(files),
        "sqliteIntegrity": not any(
            error.startswith("sqlite_integrity") for error in errors
        ),
        "pathRedaction": not any(
            error.startswith(("absolute_path", "sqlite_sensitive_text"))
            for error in errors
        ),
        "zipVerified": zip_verified,
        "zip64Members": zip64_members,
    }


def _safe_replace_directory(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target or target.parent == target:
        raise ValueError("UNSAFE_OUTPUT_DIRECTORY")
    backup = target.with_name(target.name + ".previous")
    if backup.exists():
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(source, target)
    except Exception:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()


def build_discovery_bundle(
    *,
    project_root: Path,
    output_dir: Path,
    content_root: Path,
    captures_root: Path | None = None,
    native_root: Path | None = None,
    knowledge_db_dir: Path | None = None,
    registry_snapshot_dir: Path | None = None,
    include_existing_evidence: bool = True,
    include_native_boundaries: bool = True,
    build_zip: bool = False,
    parse_identity: bool = False,
    generated_at: str | None = None,
) -> dict[str, object]:
    """Build a separate, sanitized discovery bundle from local evidence."""

    project_root = project_root.resolve()
    output_dir = output_dir.resolve()
    content_root = content_root.resolve()
    generated_at = generated_at or utc_now()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    state_root = output_dir.parent / ".kb_discovery_work"
    state_root.mkdir(parents=True, exist_ok=True)
    state_db = state_root / "discovery_state.sqlite"
    scan_result = scan_devkit_inventory(
        state_db,
        content_root,
        parse_identity=parse_identity,
    )
    if not scan_result.get("complete"):
        return {
            "status": "in_progress",
            "scan": scan_result,
            "stateDatabase": str(state_db),
        }

    assets, local_paths = _inventory_assets(state_db)
    registry_assets: RegistryAssetStream | Sequence[Mapping[str, object]] = ()
    registry_dependencies: list[dict[str, object]] = []
    registry_manifest: dict[str, object] = {}
    snapshot = registry_snapshot_dir or state_root / "registry_snapshot"
    if (snapshot / "registry_manifest.json").is_file():
        (
            registry_assets,
            registry_dependencies,
            registry_manifest,
        ) = load_registry_snapshot(snapshot)
        _apply_registry_assets(assets, registry_assets, local_paths)

    blueprints: list[dict[str, object]] = []
    blueprint_stats: dict[str, object] = {
        "discovered": 0,
        "cacheHits": 0,
        "rebuilt": 0,
        "failures": 0,
    }
    if (
        include_existing_evidence
        and captures_root is not None
        and captures_root.is_dir()
    ):
        blueprints, blueprint_stats = load_blueprint_evidence(
            state_db,
            captures_root,
        )
        _merge_blueprint_assets(assets, blueprints, local_paths)
        serialized_stats = add_serialized_reference_surfaces(
            state_db,
            assets,
            blueprints,
            local_paths,
        )
        blueprint_stats.update(
            {
                "serializedReferenceExtracted": serialized_stats["extracted"],
                "serializedReferenceCacheHits": serialized_stats["cacheHits"],
                "serializedReferenceFailures": serialized_stats["failures"],
                "serializedReferenceCount": serialized_stats["references"],
                "serializedReferenceFailureRows": serialized_stats["failureRows"],
            }
        )

    native_payloads: list[dict[str, object]] = []
    native_stats: dict[str, int] = {
        "candidateStores": 0,
        "selectedStores": 0,
        "cacheHits": 0,
        "rebuilt": 0,
        "failures": 0,
    }
    if include_native_boundaries and native_root is not None and native_root.is_dir():
        native_payloads, native_stats = load_native_evidence(
            state_db,
            native_root,
        )
    existing_tables: list[dict[str, object]] = []
    registrations: list[dict[str, object]] = []
    existing_source: dict[str, object] = {
        "status": "SOURCE_NOT_AVAILABLE",
        "fingerprint": "",
        "snapshots": [],
    }
    if knowledge_db_dir is not None and knowledge_db_dir.is_dir():
        (
            existing_tables,
            registrations,
            existing_source,
        ) = inventory_existing_knowledge(knowledge_db_dir)

    temporary_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.building-",
            dir=output_dir.parent,
        )
    )
    try:
        database_path = temporary_dir / "kb_discovery.sqlite"
        database_counts = _materialize_database(
            database_path=database_path,
            assets=assets,
            registry_assets=registry_assets,
            registry_dependencies=registry_dependencies,
            registry_manifest=registry_manifest,
            blueprints=blueprints,
            blueprint_stats=blueprint_stats,
            native_payloads=native_payloads,
            native_stats=native_stats,
            existing_tables=existing_tables,
            existing_source=existing_source,
            registrations=registrations,
            generated_at=generated_at,
        )
        (temporary_dir / "kb_discovery_schema.sql").write_text(
            DISCOVERY_SCHEMA_SQL.strip() + "\n",
            encoding="utf-8",
        )
        with (temporary_dir / "query_corpus.jsonl").open(
            "w", encoding="utf-8"
        ) as handle:
            for row in built_in_query_corpus():
                handle.write(canonical_json(row) + "\n")
        _write_csv_exports(temporary_dir, database_path)
        sample_manifest = _write_representative_samples(
            temporary_dir,
            database_path,
        )
        metrics = _database_report_metrics(database_path)
        _write_discovery_report(
            temporary_dir,
            metrics,
            registry_available=bool(registry_assets),
            registry_dependency_available=bool(registry_dependencies),
        )
        manifest = _write_manifest_and_readme(
            project_root=project_root,
            content_root=content_root,
            bundle_dir=temporary_dir,
            assets=assets,
            database_counts=database_counts,
            scan_result=scan_result,
            registry_assets=registry_assets,
            registry_dependencies=registry_dependencies,
            registry_manifest=registry_manifest,
            blueprint_stats=blueprint_stats,
            native_payloads=native_payloads,
            native_stats=native_stats,
            sample_manifest=sample_manifest,
            generated_at=generated_at,
        )
        write_sha256sums(temporary_dir)
        preflight = verify_discovery_bundle(temporary_dir)
        if not preflight["passed"]:
            raise RuntimeError(
                "DISCOVERY_BUNDLE_PREFLIGHT_FAILED:"
                + canonical_json(preflight["errors"])
            )
        _safe_replace_directory(temporary_dir, output_dir)
        zip_path = output_dir.with_suffix(".zip")
        if build_zip:
            _build_zip(output_dir, zip_path)
        audit = verify_discovery_bundle(
            output_dir,
            zip_path=zip_path if build_zip else None,
        )
        if not audit["passed"]:
            raise RuntimeError(
                "DISCOVERY_BUNDLE_VERIFY_FAILED:" + canonical_json(audit["errors"])
            )
        return {
            "status": "complete",
            "outputDirectory": str(output_dir),
            "zipPath": str(zip_path) if build_zip else "",
            "manifest": manifest,
            "counts": database_counts,
            "scan": scan_result,
            "blueprintEvidence": blueprint_stats,
            "nativeEvidence": native_stats,
            "audit": audit,
        }
    except Exception:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
        raise
