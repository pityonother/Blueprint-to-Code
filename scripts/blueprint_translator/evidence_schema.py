"""Schema and stable identifiers for normalized Blueprint evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from urllib.parse import quote, unquote, urlsplit


EVIDENCE_SCHEMA_VERSION = "ark.blueprint.evidence.v2"
EVIDENCE_SCHEMA_USER_VERSION = 2
# Parser/normalizer versions participate in the revision fingerprint.  Keep
# them separate from the SQLite schema version: normalization can change while
# the public v2 schema remains compatible, and such a change must not reuse an
# older revision ID.
LEGACY_CAPTURE_PARSER_VERSION = "legacy-capture-evidence-v3"


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_asset_id(object_path: str) -> str:
    normalized = str(object_path or "").strip().replace("\\", "/").casefold()
    if not normalized:
        raise ValueError("object_path is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def make_revision_id(
    source_hashes: Mapping[str, str],
    *,
    parser_version: str,
    schema_version: str,
) -> str:
    if not source_hashes:
        raise ValueError("source_hashes must not be empty")
    payload = {
        "sources": sorted((str(path).replace("\\", "/"), str(digest)) for path, digest in source_hashes.items()),
        "parser_version": str(parser_version),
        "schema_version": str(schema_version),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:24]


def make_graph_ref(asset_id: str, revision_id: str, export_index: int) -> str:
    if not asset_id or not revision_id:
        raise ValueError("asset_id and revision_id are required")
    try:
        index = int(export_index)
    except (TypeError, ValueError) as exc:
        raise ValueError("graph export_index must be an integer") from exc
    return f"bp://{asset_id}@{revision_id}/g/{index}"


def make_node_ref(graph_ref: str, node_export_or_local_index: object) -> str:
    parsed = parse_evidence_ref(graph_ref)
    if parsed["kind"] != "graph":
        raise ValueError("graph_ref must identify a graph")
    node_id = str(node_export_or_local_index)
    if not node_id:
        raise ValueError("node id is required")
    return f"{graph_ref}/n/{quote(node_id, safe='')}"


def make_pin_ref(node_ref: str, ordinal: int) -> str:
    parsed = parse_evidence_ref(node_ref)
    if parsed["kind"] != "node":
        raise ValueError("node_ref must identify a node")
    try:
        pin_ordinal = int(ordinal)
    except (TypeError, ValueError) as exc:
        raise ValueError("pin ordinal must be an integer") from exc
    if pin_ordinal < 0:
        raise ValueError("pin ordinal must be non-negative")
    return f"{node_ref}/p/{pin_ordinal}"


def make_default_ref(asset_id: str, revision_id: str, property_path: str) -> str:
    if not asset_id or not revision_id or not str(property_path):
        raise ValueError("asset_id, revision_id, and property_path are required")
    return f"bp://{asset_id}@{revision_id}/default/{quote(str(property_path), safe='')}"


def parse_evidence_ref(ref: str) -> dict[str, object]:
    parsed = urlsplit(str(ref or ""))
    if parsed.scheme != "bp" or not parsed.netloc or "@" not in parsed.netloc:
        raise ValueError("invalid Blueprint evidence ref")
    asset_id, revision_id = parsed.netloc.split("@", 1)
    if not asset_id or not revision_id or parsed.query or parsed.fragment:
        raise ValueError("invalid Blueprint evidence ref")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    result: dict[str, object] = {"asset_id": asset_id, "revision_id": revision_id}
    if len(parts) == 2 and parts[0] == "default" and parts[1]:
        return {**result, "kind": "default", "property_path": parts[1]}
    if len(parts) not in {2, 4, 6} or parts[0] != "g":
        raise ValueError("invalid Blueprint evidence ref")
    try:
        graph_export_index = int(parts[1])
    except ValueError as exc:
        raise ValueError("graph export index must be an integer") from exc
    result["graph_export_index"] = graph_export_index
    if len(parts) == 2:
        return {**result, "kind": "graph"}
    if parts[2] != "n" or not parts[3]:
        raise ValueError("invalid Blueprint evidence ref")
    result["node_id"] = parts[3]
    if len(parts) == 4:
        return {**result, "kind": "node"}
    if parts[4] != "p":
        raise ValueError("invalid Blueprint evidence ref")
    try:
        pin_ordinal = int(parts[5])
    except ValueError as exc:
        raise ValueError("pin ordinal must be an integer") from exc
    if pin_ordinal < 0:
        raise ValueError("pin ordinal must be non-negative")
    return {**result, "kind": "pin", "pin_ordinal": pin_ordinal}


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS asset_revisions (
    revision_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    object_path TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    uasset_path TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS graphs (
    graph_ref TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    export_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    graph_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '',
    node_count INTEGER NOT NULL DEFAULT 0,
    pin_count INTEGER NOT NULL DEFAULT 0,
    link_observation_count INTEGER NOT NULL DEFAULT 0,
    coverage_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS nodes (
    node_ref TEXT PRIMARY KEY,
    graph_ref TEXT NOT NULL REFERENCES graphs(graph_ref) ON DELETE CASCADE,
    local_index INTEGER NOT NULL,
    node_identity TEXT NOT NULL,
    package_index INTEGER,
    export_index INTEGER,
    name TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    class_name TEXT NOT NULL DEFAULT '',
    node_type TEXT NOT NULL DEFAULT '',
    control_kind TEXT NOT NULL DEFAULT '',
    function_name TEXT NOT NULL DEFAULT '',
    variable_name TEXT NOT NULL DEFAULT '',
    event_name TEXT NOT NULL DEFAULT '',
    delegate_name TEXT NOT NULL DEFAULT '',
    macro_name TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    x INTEGER,
    y INTEGER,
    source TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '',
    semantic_json TEXT NOT NULL DEFAULT '{}',
    raw_offsets_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    extra_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(graph_ref, node_identity)
);

CREATE TABLE IF NOT EXISTS pins (
    pin_ref TEXT PRIMARY KEY,
    node_ref TEXT NOT NULL REFERENCES nodes(node_ref) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    native_pin_id TEXT NOT NULL DEFAULT '',
    persistent_guid TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    subcategory TEXT NOT NULL DEFAULT '',
    default_value_json TEXT NOT NULL DEFAULT '""',
    default_object TEXT NOT NULL DEFAULT '',
    linked_to_raw TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '',
    pin_type_json TEXT NOT NULL DEFAULT '{}',
    resolution_json TEXT NOT NULL DEFAULT '{}',
    raw_offsets_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    extra_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(node_ref, ordinal)
);

CREATE TABLE IF NOT EXISTS edges (
    edge_ref TEXT PRIMARY KEY,
    graph_ref TEXT NOT NULL REFERENCES graphs(graph_ref) ON DELETE CASCADE,
    source_pin_ref TEXT NOT NULL REFERENCES pins(pin_ref) ON DELETE CASCADE,
    target_pin_ref TEXT NOT NULL REFERENCES pins(pin_ref) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'data',
    confidence TEXT NOT NULL DEFAULT '',
    resolution_status TEXT NOT NULL DEFAULT '',
    UNIQUE(graph_ref, source_pin_ref, target_pin_ref, kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_pin_ref);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_pin_ref);

CREATE TABLE IF NOT EXISTS edge_observations (
    observation_id INTEGER PRIMARY KEY,
    observation_ref TEXT NOT NULL UNIQUE,
    graph_ref TEXT NOT NULL REFERENCES graphs(graph_ref) ON DELETE CASCADE,
    source_node_ref TEXT REFERENCES nodes(node_ref) ON DELETE CASCADE,
    source_pin_ref TEXT REFERENCES pins(pin_ref) ON DELETE CASCADE,
    target_node_ref TEXT REFERENCES nodes(node_ref) ON DELETE SET NULL,
    target_pin_ref TEXT REFERENCES pins(pin_ref) ON DELETE SET NULL,
    target_node_name TEXT NOT NULL DEFAULT '',
    target_native_pin_id TEXT NOT NULL DEFAULT '',
    target_pin_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'data',
    status TEXT NOT NULL DEFAULT '',
    resolution_status TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS candidate_dictionary (
    dictionary_id INTEGER PRIMARY KEY CHECK (dictionary_id = 1),
    codec TEXT NOT NULL,
    values_blob BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS edge_candidate_sets (
    observation_id INTEGER PRIMARY KEY REFERENCES edge_observations(observation_id) ON DELETE CASCADE,
    candidates_json TEXT NOT NULL DEFAULT '[]'
) WITHOUT ROWID;

CREATE VIEW IF NOT EXISTS edge_candidates AS
SELECT
    sets.observation_id AS observation_id,
    CAST(items.key AS INTEGER) AS candidate_ordinal,
    CAST(json_extract(items.value, '$[0]') AS INTEGER) AS candidate_symbol_id,
    json_extract(items.value, '$[1]') AS candidate_pin_ref
FROM edge_candidate_sets AS sets
JOIN json_each(sets.candidates_json) AS items;

CREATE TABLE IF NOT EXISTS properties (
    property_ref TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    owner_kind TEXT NOT NULL,
    owner_ref TEXT NOT NULL,
    name TEXT NOT NULL,
    type_name TEXT NOT NULL DEFAULT '',
    value_json TEXT NOT NULL DEFAULT 'null',
    value_codec TEXT NOT NULL DEFAULT 'json',
    value_blob BLOB,
    confidence TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    raw_offsets_json TEXT NOT NULL DEFAULT '{}',
    extra_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(owner_ref, name)
);

CREATE TABLE IF NOT EXISTS class_defaults (
    default_ref TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type_name TEXT NOT NULL DEFAULT '',
    value_json TEXT NOT NULL DEFAULT 'null',
    value_codec TEXT NOT NULL DEFAULT 'json',
    value_blob BLOB,
    confidence TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    extra_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(revision_id, name)
);

CREATE TABLE IF NOT EXISTS diagnostics (
    diagnostic_ref TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL,
    scope_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    next_probe TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS coverage (
    scope_ref TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '',
    metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS "references" (
    reference_ref TEXT PRIMARY KEY,
    graph_ref TEXT NOT NULL REFERENCES graphs(graph_ref) ON DELETE CASCADE,
    node_ref TEXT REFERENCES nodes(node_ref) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    target_ref TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS derived_claims (
    claim_ref TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    subject_ref TEXT NOT NULL DEFAULT '',
    statement TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_ref TEXT NOT NULL REFERENCES derived_claims(claim_ref) ON DELETE CASCADE,
    evidence_ref TEXT NOT NULL,
    PRIMARY KEY (claim_ref, evidence_ref)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS search_entities (
    ref TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    graph_ref TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_search_entities_revision_kind
ON search_entities(revision_id, kind);

CREATE TABLE IF NOT EXISTS search_materialization (
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    is_complete INTEGER NOT NULL DEFAULT 0 CHECK (is_complete IN (0, 1)),
    PRIMARY KEY (revision_id, kind)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS source_manifest (
    revision_id TEXT NOT NULL REFERENCES asset_revisions(revision_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_kind TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (revision_id, path)
) WITHOUT ROWID;
"""


def ensure_evidence_schema(connection: sqlite3.Connection, *, enable_fts: bool = False) -> None:
    connection.executescript(SCHEMA_SQL)
    if enable_fts:
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts "
                "USING fts5(ref UNINDEXED, kind UNINDEXED, name, search_text, tokenize='unicode61')"
            )
        except sqlite3.OperationalError:
            # Exact-name and LIKE search stay available on Python builds without FTS5.
            pass
    connection.execute(f"PRAGMA user_version = {EVIDENCE_SCHEMA_USER_VERSION}")
