from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..evidence_repository import (
    ResolvedEvidenceState,
    evidence_manifest_payload,
    open_bound_evidence_database,
    resolve_asset_evidence_state,
)
from .contracts import InterpretationPublicationError


@dataclass(frozen=True)
class InterpretationSource:
    state: ResolvedEvidenceState
    evidence_manifest: dict[str, Any]
    identity: dict[str, Any]
    graphs: tuple[dict[str, Any], ...]
    nodes: tuple[dict[str, Any], ...]
    pins: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    observations: tuple[dict[str, Any], ...]
    references: tuple[dict[str, Any], ...]
    defaults: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, Any], ...]
    coverage: tuple[dict[str, Any], ...]
    evidence_refs: frozenset[str]


def _decode_json(raw: object, fallback: object) -> object:
    if raw is None or raw == "":
        return fallback
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InterpretationPublicationError(
            "EVIDENCE_JSON_INVALID",
            "The bound Evidence database contains invalid structured JSON.",
        ) from exc
    return value


def _rows(connection: sqlite3.Connection, query: str) -> tuple[dict[str, Any], ...]:
    return tuple({key: row[key] for key in row.keys()} for row in connection.execute(query))


def _load_rows(
    connection: sqlite3.Connection,
) -> tuple[
    dict[str, Any],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
]:
    identity_row = connection.execute(
        "SELECT revision_id, asset_id, asset_name, object_path, source_fingerprint, "
        "parser_version, schema_version, generated_at "
        "FROM asset_revisions ORDER BY revision_id LIMIT 1"
    ).fetchone()
    if identity_row is None:
        raise InterpretationPublicationError(
            "EVIDENCE_IDENTITY_MISSING",
            "The bound Evidence database has no asset identity.",
        )
    identity = {key: identity_row[key] for key in identity_row.keys()}
    graphs = _rows(
        connection,
        "SELECT graph_ref, export_index, name, graph_type, status, confidence, node_count, "
        "pin_count, link_observation_count, coverage_json, warnings_json, metadata_json "
        "FROM graphs ORDER BY export_index, graph_ref",
    )
    nodes = _rows(
        connection,
        "SELECT node_ref, graph_ref, local_index, node_identity, package_index, export_index, "
        "name, label, class_name, node_type, control_kind, function_name, variable_name, "
        "event_name, delegate_name, macro_name, comment, x, y, source, confidence, "
        "semantic_json, warnings_json, extra_json "
        "FROM nodes ORDER BY graph_ref, local_index, node_ref",
    )
    pins = _rows(
        connection,
        "SELECT pin_ref, node_ref, ordinal, native_pin_id, persistent_guid, name, direction, "
        "category, subcategory, default_value_json, default_object, source, confidence, "
        "pin_type_json, resolution_json, warnings_json, extra_json "
        "FROM pins ORDER BY node_ref, ordinal, pin_ref",
    )
    edges = _rows(
        connection,
        "SELECT edge_ref, graph_ref, source_pin_ref, target_pin_ref, kind, confidence, "
        "resolution_status FROM edges "
        "ORDER BY graph_ref, source_pin_ref, target_pin_ref, kind, edge_ref",
    )
    observations = _rows(
        connection,
        "SELECT observation_ref, graph_ref, source_node_ref, source_pin_ref, target_node_ref, "
        "target_pin_ref, target_node_name, target_native_pin_id, target_pin_name, kind, status, "
        "resolution_status, source, confidence, raw_json FROM edge_observations "
        "ORDER BY graph_ref, observation_ref",
    )
    references = _rows(
        connection,
        'SELECT reference_ref, graph_ref, node_ref, kind, name, target_ref, classification, '
        'confidence FROM "references" ORDER BY graph_ref, node_ref, kind, reference_ref',
    )
    defaults = _rows(
        connection,
        "SELECT default_ref, name, type_name, value_json, confidence, source, extra_json "
        "FROM class_defaults ORDER BY default_ref",
    )
    diagnostics = _rows(
        connection,
        "SELECT diagnostic_ref, scope_kind, scope_ref, status, reason_code, severity, title, "
        "detail, next_probe, evidence_json, raw_json FROM diagnostics "
        "ORDER BY scope_ref, reason_code, diagnostic_ref",
    )
    coverage = _rows(
        connection,
        "SELECT scope_ref, scope_kind, status, confidence, metrics_json FROM coverage "
        "ORDER BY scope_ref",
    )
    return (
        identity,
        graphs,
        nodes,
        pins,
        edges,
        observations,
        references,
        defaults,
        diagnostics,
        coverage,
    )


def _normalize_structured_rows(
    graphs: tuple[dict[str, Any], ...],
    nodes: tuple[dict[str, Any], ...],
    pins: tuple[dict[str, Any], ...],
    observations: tuple[dict[str, Any], ...],
    defaults: tuple[dict[str, Any], ...],
    diagnostics: tuple[dict[str, Any], ...],
    coverage: tuple[dict[str, Any], ...],
) -> None:
    for row in graphs:
        row["coverage"] = _decode_json(row.pop("coverage_json"), {})
        row["warnings"] = _decode_json(row.pop("warnings_json"), [])
        row["metadata"] = _decode_json(row.pop("metadata_json"), {})
    for row in nodes:
        row["semantic"] = _decode_json(row.pop("semantic_json"), {})
        row["warnings"] = _decode_json(row.pop("warnings_json"), [])
        row["extra"] = _decode_json(row.pop("extra_json"), {})
    for row in pins:
        row["default_value"] = _decode_json(row.pop("default_value_json"), "")
        row["pin_type"] = _decode_json(row.pop("pin_type_json"), {})
        row["resolution"] = _decode_json(row.pop("resolution_json"), {})
        row["warnings"] = _decode_json(row.pop("warnings_json"), [])
        row["extra"] = _decode_json(row.pop("extra_json"), {})
    for row in observations:
        row["raw"] = _decode_json(row.pop("raw_json"), {})
    for row in defaults:
        row["value"] = _decode_json(row.pop("value_json"), None)
        row["extra"] = _decode_json(row.pop("extra_json"), {})
    for row in diagnostics:
        row["evidence"] = _decode_json(row.pop("evidence_json"), [])
        row["raw"] = _decode_json(row.pop("raw_json"), {})
    for row in coverage:
        row["metrics"] = _decode_json(row.pop("metrics_json"), {})


def load_interpretation_source(
    asset_dir: str | Path,
    *,
    allow_stale: bool = False,
    allow_legacy_fallback: bool = False,
    evidence_state: ResolvedEvidenceState | None = None,
) -> InterpretationSource:
    state = evidence_state or resolve_asset_evidence_state(
        asset_dir,
        allow_stale=allow_stale,
    )
    if state.source_kind != "INDEXED_V3_CURRENT" and not allow_legacy_fallback:
        raise InterpretationPublicationError(
            "EVIDENCE_V3_REQUIRED",
            "Interpretation requires the current v3 Evidence revision.",
        )
    if state.freshness_status == "STALE" and not allow_stale:
        raise InterpretationPublicationError(
            "EVIDENCE_STALE",
            "Interpretation refuses stale Evidence by default.",
        )
    if state.source_kind == "INDEXED_V3_CURRENT" and not state.release_authority:
        raise InterpretationPublicationError(
            "EVIDENCE_NOT_AUTHORITATIVE",
            "The current Evidence revision is not release authority.",
        )
    manifest = evidence_manifest_payload(state)
    with open_bound_evidence_database(state) as connection:
        loaded = _load_rows(connection)
    (
        identity,
        graphs,
        nodes,
        pins,
        edges,
        observations,
        references,
        defaults,
        diagnostics,
        coverage,
    ) = loaded
    _normalize_structured_rows(
        graphs,
        nodes,
        pins,
        observations,
        defaults,
        diagnostics,
        coverage,
    )
    revision_id = str(identity["revision_id"])
    manifest_revision = str(manifest.get("revisionId") or revision_id)
    if manifest_revision != revision_id:
        raise InterpretationPublicationError(
            "EVIDENCE_IDENTITY_MISMATCH",
            "Evidence manifest and database revision identities differ.",
        )
    if state.manifest_sha256 is None and state.source_kind == "INDEXED_V3_CURRENT":
        raise InterpretationPublicationError(
            "EVIDENCE_MANIFEST_HASH_MISSING",
            "The current v3 Evidence revision has no bound manifest hash.",
        )

    groups = (
        (graphs, "graph_ref"),
        (nodes, "node_ref"),
        (pins, "pin_ref"),
        (edges, "edge_ref"),
        (observations, "observation_ref"),
        (defaults, "default_ref"),
        (diagnostics, "diagnostic_ref"),
        (references, "reference_ref"),
    )
    evidence_refs = [str(row[column]) for rows, column in groups for row in rows]
    if len(evidence_refs) != len(set(evidence_refs)):
        raise InterpretationPublicationError(
            "EVIDENCE_IDENTITY_DUPLICATE",
            "Evidence contains duplicate exact reference identities.",
        )
    return InterpretationSource(
        state=state,
        evidence_manifest=manifest,
        identity=identity,
        graphs=graphs,
        nodes=nodes,
        pins=pins,
        edges=edges,
        observations=observations,
        references=references,
        defaults=defaults,
        diagnostics=diagnostics,
        coverage=coverage,
        evidence_refs=frozenset(evidence_refs),
    )


__all__ = ["InterpretationSource", "load_interpretation_source"]
