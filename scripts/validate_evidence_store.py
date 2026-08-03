"""Independently reconcile legacy Blueprint captures with Evidence Store v2.

The validator deliberately does not call migration or reuse writer internals.  It
trusts only graph JSON files referenced by ``graphs_from_uasset_manifest.json``,
reconstructs their normalized associations, and compares those associations to
the published SQLite database.  It also proves that names needed by an agent are
reachable through :class:`EvidenceQueryService` rather than merely present in a
table.

Programmatic API::

    report = validate_asset(Path("captures/MyBlueprint"))

Command line::

    python scripts/validate_evidence_store.py --asset-dir captures/A
    python scripts/validate_evidence_store.py --capture-root captures --all

The CLI writes one JSON document to stdout and returns non-zero when any hard
check fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
import zlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.evidence_query import EvidenceQueryService  # noqa: E402
from blueprint_translator.evidence_repository import (  # noqa: E402
    evidence_agent_index_text,
    evidence_manifest_payload,
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_publication import (  # noqa: E402
    _lexical_absolute,
    _require_plain_directory,
    _require_plain_path_chain,
)
from blueprint_translator.evidence_schema import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    LEGACY_CAPTURE_PARSER_VERSION,
    make_revision_id,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    DIRECT_PAYLOAD_PARSER_VERSION,
)


DEFAULT_MAX_SIZE_RATIO = 0.50
TARGET_SIZE_RATIO = 0.40
DEFAULT_MAX_SEARCH_P95_MS = 100.0
DEFAULT_MAX_TWO_HOP_P95_MS = 200.0
DEFAULT_BENCHMARK_ITERATIONS = 25

_ASSOCIATION_NAMES = (
    "graphs",
    "nodes",
    "pins",
    "edges",
    "edge_observations",
    "properties",
    "class_defaults",
    "references",
    "edge_candidates",
)
_SIGNAL_FIELDS = (
    ("function", "function", "function_name"),
    ("variable", "variable", "variable_name"),
    ("event", "event", "event_name"),
)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _first_text(*values: object) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _as_int(value: object, default: int | None = None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _property_rows(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        for name, raw in value.items():
            rows.append(
                {"name": str(name), **raw}
                if isinstance(raw, dict)
                else {"name": str(name), "value": raw}
            )
        return rows
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _node_identity(
    node: Mapping[str, object],
    metadata_ref: object,
    ordinal: int,
    used: set[str],
) -> str:
    if metadata_ref not in (None, ""):
        base = f"package:{metadata_ref}"
    elif node.get("package_index") not in (None, ""):
        base = f"package:{node['package_index']}"
    elif node.get("export_index") not in (None, ""):
        base = f"export:{node['export_index']}"
    elif node.get("index") not in (None, ""):
        base = f"local:{node['index']}"
    else:
        base = f"ordinal:{ordinal}"
    identity = base
    collision = 1
    while identity in used:
        collision += 1
        identity = f"{base}#{collision}"
    used.add(identity)
    return identity


def _direction_role(direction: object) -> str:
    folded = str(direction or "").casefold()
    if "output" in folded:
        return "output"
    if "input" in folded:
        return "input"
    return "unknown"


def _canonical_endpoints(
    source: Mapping[str, object], target: Mapping[str, object]
) -> tuple[tuple[str, int], tuple[str, int]]:
    source_key = (str(source["node_identity"]), int(source["ordinal"]))
    target_key = (str(target["node_identity"]), int(target["ordinal"]))
    source_role = _direction_role(source.get("direction"))
    target_role = _direction_role(target.get("direction"))
    if source_role == "output" and target_role == "input":
        return source_key, target_key
    if source_role == "input" and target_role == "output":
        return target_key, source_key
    # The writer sorts full bp:// Pin refs when directions are unknown.  Their
    # graph prefix is identical, so reproducing the URL-quoted suffix preserves
    # the same ordering (including lexical multi-digit Pin ordinals).
    return tuple(  # type: ignore[return-value]
        sorted(
            (source_key, target_key),
            key=lambda endpoint: f"/n/{quote(endpoint[0], safe='')}/p/{endpoint[1]}",
        )
    )


def _find_target_node(
    link: Mapping[str, object],
    by_package: Mapping[int, list[dict[str, Any]]],
    by_name: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    def unique_nodes(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return list({str(record["identity"]): record for record in records}.values())

    candidates: list[dict[str, Any]] = []
    for key in ("target_package_index", "target_node_package_index", "target_export_index"):
        value = _as_int(link.get(key))
        if value is not None and by_package.get(value):
            candidates = unique_nodes(by_package[value])
            break
    name = _first_text(link.get("target_node"), link.get("target_node_name"))
    if not candidates:
        candidates = unique_nodes(by_name.get(name, []))
    elif name:
        named_candidates = [node for node in candidates if node["name"] == name]
        if named_candidates:
            candidates = named_candidates
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    native_pin_id = _first_text(link.get("target_pin_id"), link.get("target_native_pin_id"))
    pin_name = _first_text(link.get("target_pin"), link.get("target_pin_name"))
    if native_pin_id:
        id_matches = unique_nodes(
            node
            for node in candidates
            if any(pin["native_pin_id"] == native_pin_id for pin in node["pins"])
        )
        if len(id_matches) == 1:
            return id_matches[0]
        if len(id_matches) > 1:
            if pin_name:
                named_id_matches = unique_nodes(
                    node
                    for node in id_matches
                    if any(pin["name"] == pin_name for pin in node["pins"])
                )
                if len(named_id_matches) == 1:
                    return named_id_matches[0]
            return None
    if pin_name:
        name_matches = unique_nodes(
            node
            for node in candidates
            if any(pin["name"] == pin_name for pin in node["pins"])
        )
        if len(name_matches) == 1:
            return name_matches[0]
    # Multiple same-name nodes without a unique Pin discriminator are
    # ambiguous evidence.  Never invent a target by taking the first row.
    return None


def _find_target_pin(
    link: Mapping[str, object], target_node: Mapping[str, object] | None
) -> dict[str, Any] | None:
    if target_node is None:
        return None

    def unique_pins(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(
            {
                (str(record["node_identity"]), int(record["ordinal"])): record
                for record in records
            }.values()
        )

    native_pin_id = _first_text(link.get("target_pin_id"), link.get("target_native_pin_id"))
    if native_pin_id:
        matches = unique_pins(
            pin
            for pin in target_node["pins"]  # type: ignore[index]
            if pin["native_pin_id"] == native_pin_id
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            pin_name = _first_text(link.get("target_pin"), link.get("target_pin_name"))
            if pin_name:
                named_matches = [pin for pin in matches if pin["name"] == pin_name]
                if len(named_matches) == 1:
                    return named_matches[0]
            return None
    pin_name = _first_text(link.get("target_pin"), link.get("target_pin_name"))
    if pin_name:
        matches = unique_pins(
            pin
            for pin in target_node["pins"]  # type: ignore[index]
            if pin["name"] == pin_name
        )
        if len(matches) == 1:
            return matches[0]
    return None


def _empty_associations() -> dict[str, Counter[tuple[object, ...]]]:
    return {name: Counter() for name in _ASSOCIATION_NAMES}


def _manifest_graph_rows(
    asset_dir: Path, manifest: Mapping[str, object]
) -> list[tuple[dict[str, Any], Path, str]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("graphs_from_uasset_manifest.json must contain a files array")
    result: list[tuple[dict[str, Any], Path, str]] = []
    seen: set[str] = set()
    for ordinal, raw in enumerate(files):
        if not isinstance(raw, dict):
            raise ValueError(f"manifest files[{ordinal}] must be an object")
        relative = str(raw.get("path") or "").replace("\\", "/").strip("/")
        if not relative:
            raise ValueError(f"manifest files[{ordinal}] is missing path")
        path = asset_dir / Path(relative)
        if not _inside(asset_dir, path):
            raise ValueError(f"manifest graph path escapes asset directory: {relative}")
        normalized = path.resolve().relative_to(asset_dir.resolve()).as_posix()
        if normalized in seen:
            raise ValueError(f"duplicate graph path in manifest: {normalized}")
        if not path.is_file():
            raise FileNotFoundError(path)
        seen.add(normalized)
        result.append((raw, path, normalized))
    return result


def _legacy_model(asset_dir: Path) -> dict[str, Any]:
    manifest_path = asset_dir / "graphs_from_uasset_manifest.json"
    manifest = _read_json_object(manifest_path)
    graph_rows = _manifest_graph_rows(asset_dir, manifest)
    associations = _empty_associations()
    recall: list[dict[str, object]] = []
    graph_bytes = 0

    for graph_row, graph_path, _relative in graph_rows:
        graph_bytes += graph_path.stat().st_size
        payload = _read_json_object(graph_path)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        export_index = _as_int(
            graph_row.get("export_index"),
            _as_int(metadata.get("uasset_export_index"), _as_int(metadata.get("export_index"), 0)),
        )
        if export_index is None:
            raise ValueError(f"graph export index is required: {graph_path}")
        graph_name = _first_text(
            graph_row.get("graph"), metadata.get("graph_name"), f"Graph_{export_index}"
        )
        graph_type = _first_text(graph_row.get("graph_type"), metadata.get("graph_type"))
        associations["graphs"][(export_index, graph_name, graph_type)] += 1

        raw_nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
        metadata_refs = (
            metadata.get("uasset_node_refs")
            if isinstance(metadata.get("uasset_node_refs"), list)
            else []
        )
        used_identities: set[str] = set()
        node_records: list[dict[str, Any]] = []
        by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_package: dict[int, list[dict[str, Any]]] = defaultdict(list)

        for ordinal, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, dict):
                continue
            metadata_ref = metadata_refs[ordinal] if ordinal < len(metadata_refs) else None
            identity = _node_identity(raw_node, metadata_ref, ordinal, used_identities)
            name = _first_text(raw_node.get("name"), raw_node.get("label"), f"Node_{ordinal}")
            associations["nodes"][(export_index, identity, name)] += 1
            record: dict[str, Any] = {
                "identity": identity,
                "name": name,
                "pins": [],
                "raw": raw_node,
                "package_index": _as_int(raw_node.get("package_index"), _as_int(metadata_ref)),
            }
            node_records.append(record)
            by_name[name].append(record)
            for package_index in {record["package_index"], _as_int(raw_node.get("index"))}:
                if package_index is not None:
                    by_package[package_index].append(record)

            property_names: set[str] = set()
            for property_ordinal, prop in enumerate(_property_rows(raw_node.get("properties"))):
                prop_name = _first_text(prop.get("name"), f"property_{property_ordinal}")
                property_names.add(prop_name)
            for prop_name in property_names:
                associations["properties"][(export_index, identity, prop_name)] += 1

            for kind, primary_key, fallback_key in _SIGNAL_FIELDS:
                signal_name = _first_text(raw_node.get(primary_key), raw_node.get(fallback_key))
                if not signal_name:
                    continue
                associations["references"][(export_index, identity, kind, signal_name)] += 1
                recall.append(
                    {
                        "kind": kind,
                        "name": signal_name,
                        "graphExportIndex": export_index,
                        "nodeIdentity": identity,
                    }
                )

            raw_pins = raw_node.get("pins") if isinstance(raw_node.get("pins"), list) else []
            for pin_ordinal, raw_pin in enumerate(raw_pins):
                if not isinstance(raw_pin, dict):
                    continue
                pin = {
                    "node_identity": identity,
                    "ordinal": pin_ordinal,
                    "native_pin_id": _first_text(raw_pin.get("id"), raw_pin.get("native_pin_id")),
                    "name": _first_text(raw_pin.get("name")),
                    "direction": _first_text(raw_pin.get("direction")),
                    "category": _first_text(raw_pin.get("category")),
                    "raw": raw_pin,
                }
                record["pins"].append(pin)
                associations["pins"][
                    (
                        export_index,
                        identity,
                        pin_ordinal,
                        pin["native_pin_id"],
                        pin["name"],
                        pin["direction"],
                    )
                ] += 1

        graph_edges: set[tuple[object, ...]] = set()
        for node in node_records:
            for source_pin in node["pins"]:
                links = source_pin["raw"].get("links")
                if not isinstance(links, list):
                    continue
                for raw_link in links:
                    if not isinstance(raw_link, dict):
                        continue
                    target_node = _find_target_node(raw_link, by_package, by_name)
                    target_pin = _find_target_pin(raw_link, target_node)
                    target_node_name = _first_text(
                        raw_link.get("target_node"), raw_link.get("target_node_name")
                    )
                    target_native_pin_id = _first_text(
                        raw_link.get("target_pin_id"), raw_link.get("target_native_pin_id")
                    )
                    target_pin_name = _first_text(
                        raw_link.get("target_pin"), raw_link.get("target_pin_name")
                    )
                    kind = _first_text(raw_link.get("kind"))
                    if not kind:
                        kind = "exec" if source_pin["category"].casefold() == "exec" else "data"
                    observation_key = (
                        export_index,
                        node["identity"],
                        source_pin["ordinal"],
                        target_node["identity"] if target_node else "",
                        target_pin["ordinal"] if target_pin else -1,
                        target_node_name,
                        target_native_pin_id,
                        target_pin_name,
                        kind,
                    )
                    associations["edge_observations"][observation_key] += 1
                    candidates = raw_link.get("target_pin_id_candidates")
                    if not isinstance(candidates, list):
                        candidates = (
                            raw_link.get("candidate_pin_ids")
                            if isinstance(raw_link.get("candidate_pin_ids"), list)
                            else []
                        )
                    for candidate_ordinal, candidate in enumerate(candidates):
                        native_id = (
                            _first_text(candidate.get("id"), candidate.get("native_pin_id"))
                            if isinstance(candidate, dict)
                            else str(candidate)
                        )
                        candidate_pin = None
                        if target_node is not None:
                            candidate_matches = {
                                (str(pin["node_identity"]), int(pin["ordinal"])): pin
                                for pin in target_node["pins"]
                                if pin["native_pin_id"] == native_id
                            }
                            if len(candidate_matches) == 1:
                                candidate_pin = next(iter(candidate_matches.values()))
                        associations["edge_candidates"][
                            (
                                *observation_key,
                                candidate_ordinal,
                                native_id,
                                target_node["identity"] if candidate_pin and target_node else "",
                                candidate_pin["ordinal"] if candidate_pin else -1,
                            )
                        ] += 1
                    if target_pin is not None:
                        source_key, target_key = _canonical_endpoints(source_pin, target_pin)
                        graph_edges.add((export_index, *source_key, *target_key, kind))
        associations["edges"].update(graph_edges)

    defaults_path = asset_dir / "uasset_class_defaults.json"
    if defaults_path.is_file():
        defaults = _read_json_object(defaults_path)
        merged: dict[str, dict[str, Any]] = {}
        variables = defaults.get("variables")
        if isinstance(variables, dict):
            for name, raw in variables.items():
                merged[str(name)] = {
                    "name": str(name),
                    **(raw if isinstance(raw, dict) else {"value": raw}),
                }
        for row in _property_rows(defaults.get("properties")):
            name = _first_text(row.get("name"))
            if name:
                merged[name] = {**merged.get(name, {"name": name}), **row}
        for name in sorted(merged):
            associations["class_defaults"][(name,)] += 1
            recall.append({"kind": "default", "name": name})

    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "graph_paths": [relative for _row, _path, relative in graph_rows],
        "graph_bytes": graph_bytes,
        "associations": associations,
        "recall": recall,
    }


def _candidate_dictionary(connection: sqlite3.Connection) -> list[str]:
    row = connection.execute(
        "SELECT codec, values_blob FROM candidate_dictionary WHERE dictionary_id = 1"
    ).fetchone()
    if row is None:
        return []
    if str(row[0]) != "zlib-json-utf8":
        raise ValueError(f"unsupported candidate dictionary codec: {row[0]}")
    try:
        value = json.loads(zlib.decompress(bytes(row[1])).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError, zlib.error) as exc:
        raise ValueError("candidate dictionary is corrupt") from exc
    if not isinstance(value, list):
        raise ValueError("candidate dictionary is not an array")
    return [str(item) for item in value]


def _database_model(connection: sqlite3.Connection) -> dict[str, Any]:
    associations = _empty_associations()
    revision_row = connection.execute(
        "SELECT asset_id, asset_name, object_path, revision_id, source_fingerprint, "
        "parser_version, schema_version, uasset_path "
        "FROM asset_revisions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if revision_row is None:
        raise ValueError("asset_revisions is empty")
    identity = {
        "assetId": str(revision_row[0]),
        "assetName": str(revision_row[1]),
        "objectPath": str(revision_row[2]),
        "revisionId": str(revision_row[3]),
        "sourceFingerprint": str(revision_row[4]),
        "parserVersion": str(revision_row[5]),
        "schemaVersion": str(revision_row[6]),
        "uassetPath": str(revision_row[7]),
    }

    source_manifest = [
        {
            "path": str(row[0]),
            "sha256": str(row[1]),
            "sizeBytes": int(row[2]),
            "sourceKind": str(row[3]),
        }
        for row in connection.execute(
            "SELECT path, sha256, size_bytes, source_kind FROM source_manifest ORDER BY path"
        )
    ]

    node_refs: dict[tuple[int, str], str] = {}
    default_refs: dict[str, str] = {}
    search_names: list[str] = []
    for row in connection.execute("SELECT export_index, name, graph_type FROM graphs"):
        associations["graphs"][(int(row[0]), str(row[1]), str(row[2]))] += 1
    for row in connection.execute(
        "SELECT g.export_index, n.node_identity, n.name, n.node_ref "
        "FROM nodes n JOIN graphs g ON g.graph_ref = n.graph_ref"
    ):
        key = (int(row[0]), str(row[1]), str(row[2]))
        associations["nodes"][key] += 1
        node_refs[(int(row[0]), str(row[1]))] = str(row[3])
        if str(row[2]):
            search_names.append(str(row[2]))
    for row in connection.execute(
        "SELECT g.export_index, n.node_identity, p.ordinal, p.native_pin_id, p.name, p.direction "
        "FROM pins p JOIN nodes n ON n.node_ref = p.node_ref "
        "JOIN graphs g ON g.graph_ref = n.graph_ref"
    ):
        associations["pins"][
            (int(row[0]), str(row[1]), int(row[2]), str(row[3]), str(row[4]), str(row[5]))
        ] += 1
    for row in connection.execute(
        "SELECT g.export_index, sn.node_identity, sp.ordinal, tn.node_identity, tp.ordinal, "
        "o.target_node_name, o.target_native_pin_id, o.target_pin_name, o.kind "
        "FROM edge_observations o "
        "JOIN graphs g ON g.graph_ref = o.graph_ref "
        "LEFT JOIN nodes sn ON sn.node_ref = o.source_node_ref "
        "LEFT JOIN pins sp ON sp.pin_ref = o.source_pin_ref "
        "LEFT JOIN nodes tn ON tn.node_ref = o.target_node_ref "
        "LEFT JOIN pins tp ON tp.pin_ref = o.target_pin_ref"
    ):
        associations["edge_observations"][
            (
                int(row[0]),
                str(row[1] or ""),
                int(row[2]) if row[2] is not None else -1,
                str(row[3] or ""),
                int(row[4]) if row[4] is not None else -1,
                str(row[5] or ""),
                str(row[6] or ""),
                str(row[7] or ""),
                str(row[8] or ""),
            )
        ] += 1
    for row in connection.execute(
        "SELECT g.export_index, sn.node_identity, sp.ordinal, tn.node_identity, tp.ordinal, e.kind "
        "FROM edges e JOIN graphs g ON g.graph_ref = e.graph_ref "
        "JOIN pins sp ON sp.pin_ref = e.source_pin_ref "
        "JOIN nodes sn ON sn.node_ref = sp.node_ref "
        "JOIN pins tp ON tp.pin_ref = e.target_pin_ref "
        "JOIN nodes tn ON tn.node_ref = tp.node_ref"
    ):
        associations["edges"][
            (int(row[0]), str(row[1]), int(row[2]), str(row[3]), int(row[4]), str(row[5]))
        ] += 1
    for row in connection.execute(
        "SELECT g.export_index, n.node_identity, p.name "
        "FROM properties p JOIN nodes n ON n.node_ref = p.owner_ref "
        "JOIN graphs g ON g.graph_ref = n.graph_ref WHERE p.owner_kind = 'node'"
    ):
        associations["properties"][(int(row[0]), str(row[1]), str(row[2]))] += 1
    for row in connection.execute("SELECT name, default_ref FROM class_defaults"):
        associations["class_defaults"][(str(row[0]),)] += 1
        default_refs[str(row[0])] = str(row[1])
    for row in connection.execute(
        "SELECT g.export_index, n.node_identity, r.kind, r.name "
        "FROM \"references\" r JOIN nodes n ON n.node_ref = r.node_ref "
        "JOIN graphs g ON g.graph_ref = n.graph_ref "
        "WHERE r.kind IN ('function', 'variable', 'event')"
    ):
        associations["references"][(int(row[0]), str(row[1]), str(row[2]), str(row[3]))] += 1

    dictionary = _candidate_dictionary(connection)
    for row in connection.execute(
        "SELECT g.export_index, sn.node_identity, sp.ordinal, tn.node_identity, tp.ordinal, "
        "o.target_node_name, o.target_native_pin_id, o.target_pin_name, o.kind, "
        "c.candidate_ordinal, c.candidate_symbol_id, cn.node_identity, cp.ordinal "
        "FROM edge_candidates c "
        "JOIN edge_observations o ON o.observation_id = c.observation_id "
        "JOIN graphs g ON g.graph_ref = o.graph_ref "
        "LEFT JOIN nodes sn ON sn.node_ref = o.source_node_ref "
        "LEFT JOIN pins sp ON sp.pin_ref = o.source_pin_ref "
        "LEFT JOIN nodes tn ON tn.node_ref = o.target_node_ref "
        "LEFT JOIN pins tp ON tp.pin_ref = o.target_pin_ref "
        "LEFT JOIN pins cp ON cp.pin_ref = c.candidate_pin_ref "
        "LEFT JOIN nodes cn ON cn.node_ref = cp.node_ref"
    ):
        symbol_id = int(row[10])
        native_id = dictionary[symbol_id - 1] if 0 < symbol_id <= len(dictionary) else ""
        associations["edge_candidates"][
            (
                int(row[0]),
                str(row[1] or ""),
                int(row[2]) if row[2] is not None else -1,
                str(row[3] or ""),
                int(row[4]) if row[4] is not None else -1,
                str(row[5] or ""),
                str(row[6] or ""),
                str(row[7] or ""),
                str(row[8] or ""),
                int(row[9]),
                native_id,
                str(row[11] or ""),
                int(row[12]) if row[12] is not None else -1,
            )
        ] += 1

    return {
        "identity": identity,
        "associations": associations,
        "node_refs": node_refs,
        "default_refs": default_refs,
        "search_names": search_names,
        "source_manifest": source_manifest,
    }


def _counter_examples(counter: Counter[tuple[object, ...]], limit: int = 5) -> list[dict[str, object]]:
    return [
        {"association": list(key), "count": count}
        for key, count in sorted(counter.items(), key=lambda item: repr(item[0]))[:limit]
    ]


def _reconciliation_check(
    expected: Mapping[str, Counter[tuple[object, ...]]],
    actual: Mapping[str, Counter[tuple[object, ...]]],
) -> dict[str, object]:
    mismatches: dict[str, object] = {}
    expected_counts: dict[str, int] = {}
    actual_counts: dict[str, int] = {}
    for name in _ASSOCIATION_NAMES:
        expected_counter = expected[name]
        actual_counter = actual[name]
        expected_counts[name] = sum(expected_counter.values())
        actual_counts[name] = sum(actual_counter.values())
        if expected_counter != actual_counter:
            missing = expected_counter - actual_counter
            unexpected = actual_counter - expected_counter
            mismatches[name] = {
                "expectedCount": expected_counts[name],
                "actualCount": actual_counts[name],
                "missing": _counter_examples(missing),
                "unexpected": _counter_examples(unexpected),
            }
    return {
        "ok": not mismatches,
        "expectedCounts": expected_counts,
        "actualCounts": actual_counts,
        "mismatches": mismatches,
    }


def _sqlite_check(connection: sqlite3.Connection) -> dict[str, object]:
    integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    foreign_key_rows = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    return {
        "ok": integrity_rows == ["ok"] and not foreign_key_rows,
        "integrity": integrity_rows,
        "foreignKeyErrors": foreign_key_rows,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _versions_check(
    identity: Mapping[str, object], expected_parser_version: str
) -> dict[str, object]:
    errors: list[str] = []
    parser_version = str(identity.get("parserVersion") or "")
    schema_version = str(identity.get("schemaVersion") or "")
    if parser_version != expected_parser_version:
        errors.append(
            "parser_version does not match the current implementation: "
            f"expected {expected_parser_version!r}, got {parser_version!r}"
        )
    if schema_version != EVIDENCE_SCHEMA_VERSION:
        errors.append(
            "schema_version does not match the current schema: "
            f"expected {EVIDENCE_SCHEMA_VERSION!r}, got {schema_version!r}"
        )
    return {
        "ok": not errors,
        "expectedParserVersion": expected_parser_version,
        "actualParserVersion": parser_version,
        "expectedSchemaVersion": EVIDENCE_SCHEMA_VERSION,
        "actualSchemaVersion": schema_version,
        "errors": errors,
    }


def _source_manifest_check(
    asset_dir: Path,
    database_model: Mapping[str, object],
    *,
    mode: str,
) -> dict[str, object]:
    identity = database_model.get("identity")
    rows_value = database_model.get("source_manifest")
    if not isinstance(identity, Mapping) or not isinstance(rows_value, list):
        return _failed_check("database model does not contain source_manifest metadata")

    rows = [row for row in rows_value if isinstance(row, Mapping)]
    by_path = {str(row.get("path") or ""): row for row in rows}
    errors: list[str] = []
    checked_files: list[dict[str, object]] = []
    skipped_files: list[str] = []
    if not rows:
        errors.append("source_manifest is empty")

    source_hashes = {
        str(row.get("path") or ""): str(row.get("sha256") or "") for row in rows
    }
    if source_hashes:
        compact = json.dumps(
            sorted(source_hashes.items()),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = hashlib.sha256(compact).hexdigest()
        if fingerprint != str(identity.get("sourceFingerprint") or ""):
            errors.append("source_fingerprint does not match source_manifest hashes")
        expected_revision = make_revision_id(
            source_hashes,
            parser_version=str(identity.get("parserVersion") or ""),
            schema_version=str(identity.get("schemaVersion") or ""),
        )
        if expected_revision != str(identity.get("revisionId") or ""):
            errors.append("revision_id does not match source_manifest and version metadata")

    def check_file(path: Path, logical_path: str) -> None:
        row = by_path.get(logical_path)
        if row is None:
            errors.append(f"source_manifest is missing {logical_path}")
            return
        actual_size = path.stat().st_size
        expected_size = int(row.get("sizeBytes") or 0)
        actual_sha256 = _sha256_file(path)
        expected_sha256 = str(row.get("sha256") or "")
        if actual_size != expected_size:
            errors.append(
                f"size_bytes mismatch for {logical_path}: expected {expected_size}, got {actual_size}"
            )
        if actual_sha256 != expected_sha256:
            errors.append(
                f"sha256 mismatch for {logical_path}: expected {expected_sha256}, got {actual_sha256}"
            )
        checked_files.append(
            {
                "path": str(path),
                "logicalPath": logical_path,
                "sizeBytes": actual_size,
                "sha256": actual_sha256,
            }
        )

    if mode == "direct":
        memory_row = by_path.get("@memory/normalized_graph_facts")
        if memory_row is None or str(memory_row.get("sourceKind") or "") != "in_memory_capture":
            errors.append(
                "source_manifest is missing the normalized in-memory graph-facts source"
            )
        raw_uasset_path = str(identity.get("uassetPath") or "")
        if raw_uasset_path:
            uasset_path = Path(raw_uasset_path).expanduser()
            if not uasset_path.is_absolute():
                uasset_path = asset_dir / uasset_path
            binary_candidates = [uasset_path]
            binary_candidates.extend(
                uasset_path.with_suffix(suffix) for suffix in (".uexp", ".ubulk")
            )
            for candidate in binary_candidates:
                if candidate.is_file():
                    check_file(candidate, f"binary/{candidate.name}")
                else:
                    skipped_files.append(str(candidate))
    else:
        for row in rows:
            logical_path = str(row.get("path") or "")
            if not logical_path or logical_path.startswith("@"):
                continue
            if str(row.get("sourceKind") or "") == "package_binary" or logical_path.startswith(
                "binary/"
            ):
                raw_uasset_path = str(identity.get("uassetPath") or "")
                if not raw_uasset_path:
                    skipped_files.append(logical_path)
                    continue
                uasset_path = Path(raw_uasset_path).expanduser()
                if not uasset_path.is_absolute():
                    uasset_path = asset_dir / uasset_path
                candidate = uasset_path.with_name(Path(logical_path).name)
                if candidate.is_file():
                    check_file(candidate, logical_path)
                else:
                    # Legacy capture bundles intentionally do not duplicate the
                    # DevKit package binary.  Validate it when the recorded
                    # external path is mounted, otherwise keep the skip visible.
                    skipped_files.append(str(candidate))
                continue
            candidate = asset_dir / Path(logical_path)
            if not _inside(asset_dir, candidate):
                errors.append(f"source_manifest path escapes asset directory: {logical_path}")
            elif not candidate.is_file():
                errors.append(f"source_manifest file is missing: {logical_path}")
            else:
                check_file(candidate, logical_path)

    return {
        "ok": not errors,
        "mode": mode,
        "entryCount": len(rows),
        "checkedFiles": checked_files,
        "skippedMissingBinaryPaths": skipped_files,
        "errors": errors,
    }


def _is_temp_or_stale(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name.endswith((".tmp", ".bak", ".stale", "-wal", "-shm"))
        or name.startswith(".agent_index")
        or name.startswith(".evidence-")
    )


def _artifact_check(
    asset_dir: Path,
    evidence_manifest: Mapping[str, object],
    database_model: Mapping[str, object],
) -> dict[str, object]:
    evidence_dir = asset_dir / "evidence"
    output_dir = asset_dir / "output"
    expected_evidence = {"evidence.sqlite", "manifest.json"}
    actual_evidence = {
        path.relative_to(evidence_dir).as_posix()
        for path in evidence_dir.rglob("*")
        if path.is_file()
    } if evidence_dir.is_dir() else set()
    unexpected_evidence = sorted(actual_evidence - expected_evidence)
    unexpected_evidence_entries = sorted(
        path.relative_to(evidence_dir).as_posix() + "/"
        for path in evidence_dir.rglob("*")
        if path.is_dir()
    ) if evidence_dir.is_dir() else []
    missing_evidence = sorted(expected_evidence - actual_evidence)
    stale_output = sorted(
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if _is_temp_or_stale(path)
    ) if output_dir.is_dir() else []
    staging_entries = sorted(
        path.name
        for path in asset_dir.iterdir()
        if path.name.startswith((".evidence-migration-", ".evidence-direct-"))
    )
    index_path = output_dir / "agent_index.md"
    manifest_counts = evidence_manifest.get("counts")
    associations = database_model["associations"]
    assert isinstance(associations, Mapping)
    expected_manifest_counts = {
        name: sum(associations[name].values())
        for name in ("graphs", "nodes", "pins", "edges", "edge_observations")
    }
    identity = database_model["identity"]
    assert isinstance(identity, Mapping)
    contract_errors: list[str] = []
    if evidence_manifest.get("database") != "evidence.sqlite":
        contract_errors.append("manifest database must be evidence.sqlite")
    if evidence_manifest.get("agent_index") != "../output/agent_index.md":
        contract_errors.append("manifest agent_index must be ../output/agent_index.md")
    if evidence_manifest.get("revision_id") != identity.get("revisionId"):
        contract_errors.append("manifest revision_id does not match SQLite")
    if evidence_manifest.get("parser_version") != identity.get("parserVersion"):
        contract_errors.append("manifest parser_version does not match SQLite")
    if evidence_manifest.get("schema") != identity.get("schemaVersion"):
        contract_errors.append("manifest schema does not match SQLite")
    if manifest_counts != expected_manifest_counts:
        contract_errors.append("manifest counts do not match SQLite")
    if not index_path.is_file():
        contract_errors.append("output/agent_index.md is missing")
    ok = not (
        unexpected_evidence
        or unexpected_evidence_entries
        or missing_evidence
        or stale_output
        or staging_entries
        or contract_errors
    )
    return {
        "ok": ok,
        "expectedEvidenceFiles": sorted(expected_evidence),
        "actualEvidenceFiles": sorted(actual_evidence),
        "unexpectedEvidenceFiles": unexpected_evidence,
        "unexpectedEvidenceEntries": unexpected_evidence_entries,
        "missingEvidenceFiles": missing_evidence,
        "staleOutputFiles": stale_output,
        "stagingEntries": staging_entries,
        "contractErrors": contract_errors,
    }


_INDEX_COUNT_PATTERNS: dict[str, tuple[str, ...]] = {
    "graphCount": (r"(?im)(?:^-\s*|;\s*)Graphs\s*[:=]\s*(\d+)",),
    "nodeCount": (r"(?im)(?:^-\s*|;\s*)Nodes\s*[:=]\s*(\d+)",),
    "pinCount": (r"(?im)(?:^-\s*|;\s*)Pins\s*[:=]\s*(\d+)",),
    "wireCount": (r"(?im)(?:^-\s*|;\s*)Wires\s*[:=]\s*(\d+)",),
    "linkObservationCount": (r"(?im)(?:^-\s*|;\s*)Link observations\s*[:=]\s*(\d+)",),
    "defaultCount": (
        r"(?im)^-\s*Class defaults\s*[:=]\s*(\d+)",
        r"(?im)(?:^-\s*|;\s*)Defaults\s*=\s*(\d+)",
    ),
    "gapCount": (
        r"(?im)^-\s*Evidence gaps\s*[:=]\s*(\d+)",
        r"(?im)(?:^-\s*|;\s*)Gaps\s*=\s*(\d+)",
    ),
}


def _agent_index_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, patterns in _INDEX_COUNT_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text)
            if match is not None:
                counts[key] = int(match.group(1))
                break
    return counts


def _query_index_counts(
    database_path: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> dict[str, int]:
    with EvidenceQueryService.open(
        database_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    ) as service:
        overview = service.query({"operation": "overview", "budgetTokens": 8000})
    summary = overview.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("overview query did not return a summary")
    return {
        key: int(summary.get(key) or 0)
        for key in _INDEX_COUNT_PATTERNS
    }


def _agent_index_check(
    asset_dir: Path,
    revision_id: str,
    database_path: Path,
    *,
    index_path: Path | None = None,
    index_text: str | None = None,
    index_bytes: int | None = None,
    expected_database_sha256: str | None = None,
    expected_database_size: int | None = None,
) -> dict[str, object]:
    path = index_path if index_path is not None else asset_dir / "output" / "agent_index.md"
    if index_text is None:
        if not path.is_file():
            return {"ok": False, "estimatedTokens": 0, "limit": 1500, "errors": ["file is missing"]}
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            return {"ok": False, "estimatedTokens": 0, "limit": 1500, "errors": [str(exc)]}
        observed_bytes = path.stat().st_size
    else:
        text = index_text
        observed_bytes = (
            int(index_bytes)
            if index_bytes is not None
            else len(text.encode("utf-8"))
        )
    tokens = estimate_tokens(text)
    errors: list[str] = []
    if tokens > 1500:
        errors.append(f"estimated token count {tokens} exceeds 1500")
    if revision_id and revision_id not in text:
        errors.append("SQLite revision is absent from agent_index.md")
    index_counts = _agent_index_counts(text)
    try:
        query_counts = _query_index_counts(
            database_path,
            expected_sha256=expected_database_sha256,
            expected_size=expected_database_size,
        )
    except Exception as exc:
        query_counts = {}
        errors.append(f"could not query SQLite coverage: {type(exc).__name__}: {exc}")
    for key in _INDEX_COUNT_PATTERNS:
        if key not in index_counts:
            errors.append(f"agent_index.md is missing {key}")
            continue
        if key in query_counts and index_counts[key] != query_counts[key]:
            errors.append(
                f"agent_index.md {key}={index_counts[key]} differs from SQLite query {key}={query_counts[key]}"
            )
    return {
        "ok": not errors,
        "estimatedTokens": tokens,
        "limit": 1500,
        "bytes": observed_bytes,
        "indexCounts": index_counts,
        "queryCounts": query_counts,
        "errors": errors,
    }


def _size_ratio_check(
    asset_dir: Path,
    graph_bytes: int,
    max_size_ratio: float | None,
) -> dict[str, object]:
    paths = [
        asset_dir / "evidence" / "evidence.sqlite",
        asset_dir / "evidence" / "manifest.json",
        asset_dir / "output" / "agent_index.md",
    ]
    v2_bytes = sum(path.stat().st_size for path in paths if path.is_file())
    ratio = v2_bytes / graph_bytes if graph_bytes else None
    within_max_ratio = (
        None
        if ratio is None or max_size_ratio is None
        else ratio <= max_size_ratio
    )
    notes: list[str] = []
    if graph_bytes <= 0:
        notes.append("no positive legacy graph-JSON denominator for this asset")
    elif within_max_ratio is False:
        notes.append(
            f"per-asset ratio {ratio:.6f} exceeds {max_size_ratio:.6f}; "
            "the hard gate is evaluated only on the full legacy aggregate"
        )
    return {
        # A single tiny capture has a distorted SQLite fixed-cost ratio.  Keep
        # the number visible, but enforce the threshold only after aggregation.
        "ok": True,
        "applicable": graph_bytes > 0,
        "legacyGraphJsonBytes": graph_bytes,
        "v2Bytes": v2_bytes,
        "ratio": ratio,
        "maxRatio": max_size_ratio,
        "withinMaxRatio": within_max_ratio,
        "targetRatio": TARGET_SIZE_RATIO,
        "target40Met": ratio is not None and ratio <= TARGET_SIZE_RATIO,
        "notes": notes,
        "errors": [],
    }


def _aggregate_size_check(
    reports: Iterable[Mapping[str, object]],
    max_size_ratio: float | None,
    *,
    enforce: bool = True,
) -> dict[str, object]:
    legacy_graph_bytes = 0
    v2_bytes = 0
    legacy_asset_count = 0
    direct_asset_count = 0
    zero_denominator_count = 0
    per_asset_over_max: list[dict[str, object]] = []

    for report in reports:
        source = report.get("source")
        source_mode = str(source.get("mode") or "") if isinstance(source, Mapping) else ""
        if source_mode == "direct":
            direct_asset_count += 1
            continue
        if source_mode != "legacy":
            continue
        legacy_asset_count += 1
        checks = report.get("checks")
        size = checks.get("sizeRatio") if isinstance(checks, Mapping) else None
        if not isinstance(size, Mapping):
            continue
        denominator = max(0, int(size.get("legacyGraphJsonBytes") or 0))
        numerator = max(0, int(size.get("v2Bytes") or 0))
        legacy_graph_bytes += denominator
        v2_bytes += numerator
        if denominator == 0:
            zero_denominator_count += 1
        ratio = numerator / denominator if denominator else None
        if ratio is not None and max_size_ratio is not None and ratio > max_size_ratio:
            per_asset_over_max.append(
                {
                    "assetDir": str(report.get("assetDir") or ""),
                    "ratio": ratio,
                }
            )

    ratio = v2_bytes / legacy_graph_bytes if legacy_graph_bytes else None
    within_max_ratio = (
        None
        if ratio is None or max_size_ratio is None
        else ratio <= max_size_ratio
    )
    errors: list[str] = []
    if enforce and within_max_ratio is False:
        errors.append(
            f"aggregate v2 size ratio {ratio:.6f} exceeds {max_size_ratio:.6f}"
        )
    return {
        "ok": not errors,
        "enforced": enforce,
        "applicable": legacy_graph_bytes > 0,
        "legacyAssetCount": legacy_asset_count,
        "directAssetCount": direct_asset_count,
        "zeroDenominatorAssetCount": zero_denominator_count,
        "legacyGraphJsonBytes": legacy_graph_bytes,
        "v2Bytes": v2_bytes,
        "ratio": ratio,
        "maxRatio": max_size_ratio,
        "withinMaxRatio": within_max_ratio,
        "targetRatio": TARGET_SIZE_RATIO,
        "target40Met": ratio is not None and ratio <= TARGET_SIZE_RATIO,
        "perAssetOverMax": per_asset_over_max,
        "errors": errors,
    }


def _search_all(service: EvidenceQueryService, name: str, kind: str) -> set[str]:
    kinds = ["default"] if kind == "default" else ["node"]
    cursor: str | None = None
    refs: set[str] = set()
    seen_cursors: set[str] = set()
    while True:
        request: dict[str, object] = {
            "operation": "search",
            "query": name,
            "kinds": kinds,
            "pageSize": 100,
            "budgetTokens": 8000,
        }
        if cursor:
            request["cursor"] = cursor
        response = service.query(request)
        items = response.get("items") if isinstance(response.get("items"), list) else []
        refs.update(str(item.get("ref")) for item in items if isinstance(item, dict) and item.get("ref"))
        page = response.get("page") if isinstance(response.get("page"), dict) else {}
        next_cursor = str(page.get("nextCursor") or "")
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            raise ValueError("search cursor repeated without making progress")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return refs


def _recall_check(
    database_path: Path,
    recall_rows: Iterable[Mapping[str, object]],
    node_refs: Mapping[tuple[int, str], str],
    default_refs: Mapping[str, str],
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> dict[str, object]:
    rows = list(recall_rows)
    missing: list[dict[str, object]] = []
    with EvidenceQueryService.open(
        database_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    ) as service:
        for row in rows:
            kind = str(row["kind"])
            name = str(row["name"])
            if kind == "default":
                expected_ref = default_refs.get(name, "")
            else:
                expected_ref = node_refs.get(
                    (int(row["graphExportIndex"]), str(row["nodeIdentity"])), ""
                )
            failure: dict[str, object] | None = None
            try:
                search_refs = _search_all(service, name, kind)
                if not expected_ref or expected_ref not in search_refs:
                    failure = {
                        "kind": kind,
                        "name": name,
                        "expectedRef": expected_ref,
                        "reason": "search did not return the expected entity",
                    }
                else:
                    entity = service.query(
                        {
                            "operation": "entity",
                            "selector": {"ref": expected_ref},
                            "budgetTokens": 8000,
                        }
                    )
                    items = entity.get("items") if isinstance(entity.get("items"), list) else []
                    item = items[0] if items and isinstance(items[0], dict) else {}
                    if kind == "default":
                        exact = item.get("kind") == "default" and item.get("name") == name
                    else:
                        signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
                        exact = signals.get(kind) == name
                    if not exact:
                        failure = {
                            "kind": kind,
                            "name": name,
                            "expectedRef": expected_ref,
                            "reason": "entity did not preserve the exact name",
                        }
            except Exception as exc:
                failure = {
                    "kind": kind,
                    "name": name,
                    "expectedRef": expected_ref,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            if failure is not None:
                missing.append(failure)
    by_kind = Counter(str(row["kind"]) for row in rows)
    return {
        "ok": not missing,
        "requested": len(rows),
        "recalled": len(rows) - len(missing),
        "requestedByKind": dict(sorted(by_kind.items())),
        "missing": missing,
    }


def _percentile95(samples: list[float]) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _benchmark_check(
    database_path: Path,
    *,
    iterations: int,
    search_name: str,
    node_ref: str,
    max_search_p95_ms: float,
    max_two_hop_p95_ms: float,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> dict[str, object]:
    if iterations <= 0:
        raise ValueError("benchmark_iterations must be positive")
    search_samples: list[float] = []
    hop_samples: list[float] = []
    with EvidenceQueryService.open(
        database_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    ) as service:
        search_request = {
            "operation": "search",
            "query": search_name or "*",
            "pageSize": 25,
            "budgetTokens": 8000,
        }
        service.query(search_request)
        for _ in range(iterations):
            started = time.perf_counter()
            service.query(search_request)
            search_samples.append((time.perf_counter() - started) * 1000.0)
        if node_ref:
            hop_request = {
                "operation": "neighborhood",
                "selector": {"ref": node_ref},
                "traversal": {
                    "maxHops": 2,
                    "direction": "both",
                    "edgeKinds": ["exec", "data"],
                },
                "budgetTokens": 8000,
            }
            service.query(hop_request)
            for _ in range(iterations):
                started = time.perf_counter()
                service.query(hop_request)
                hop_samples.append((time.perf_counter() - started) * 1000.0)
    search_p95 = _percentile95(search_samples)
    two_hop_p95 = _percentile95(hop_samples)
    errors: list[str] = []
    if search_p95 is not None and search_p95 > max_search_p95_ms:
        errors.append(f"search p95 {search_p95:.3f}ms exceeds {max_search_p95_ms:.3f}ms")
    if two_hop_p95 is not None and two_hop_p95 > max_two_hop_p95_ms:
        errors.append(f"2-hop p95 {two_hop_p95:.3f}ms exceeds {max_two_hop_p95_ms:.3f}ms")
    return {
        "ok": not errors,
        "enabled": True,
        "iterations": iterations,
        "searchP95Ms": search_p95,
        "twoHopP95Ms": two_hop_p95,
        "maxSearchP95Ms": max_search_p95_ms,
        "maxTwoHopP95Ms": max_two_hop_p95_ms,
        "errors": errors,
    }


def _failed_check(error: Exception | str) -> dict[str, object]:
    text = str(error)
    return {"ok": False, "errors": [text]}


def validate_asset(
    asset_dir: str | Path,
    *,
    max_size_ratio: float | None = DEFAULT_MAX_SIZE_RATIO,
    benchmark: bool = False,
    benchmark_iterations: int = DEFAULT_BENCHMARK_ITERATIONS,
    max_search_p95_ms: float = DEFAULT_MAX_SEARCH_P95_MS,
    max_two_hop_p95_ms: float = DEFAULT_MAX_TWO_HOP_P95_MS,
) -> dict[str, object]:
    """Validate one legacy-migrated or direct-capture evidence asset."""

    root = _lexical_absolute(asset_dir)
    report: dict[str, object] = {
        "assetDir": str(root),
        "ok": False,
        "source": {},
        "identity": {},
        "checks": {},
        "hardFailures": [],
    }
    checks: dict[str, object] = report["checks"]  # type: ignore[assignment]
    hard_failures: list[str] = report["hardFailures"]  # type: ignore[assignment]
    try:
        _require_plain_path_chain(root, label="asset directory")
        _require_plain_directory(root, label="asset directory")
    except (OSError, ValueError) as exc:
        checks["source"] = _failed_check(f"{type(exc).__name__}: {exc}")
        hard_failures.append("source: asset directory path is unsafe")
        return report
    if not root.is_dir():
        checks["source"] = _failed_check(f"asset directory does not exist: {root}")
        hard_failures.append("source: asset directory is unavailable")
        return report

    legacy: dict[str, Any] | None = None
    legacy_manifest_path = root / "graphs_from_uasset_manifest.json"
    if legacy_manifest_path.exists():
        try:
            legacy = _legacy_model(root)
            report["source"] = {
                "mode": "legacy",
                "manifest": str(legacy["manifest_path"]),
                "manifestGraphCount": len(legacy["graph_paths"]),
                "manifestGraphFiles": legacy["graph_paths"],
                "validGraphJsonBytes": legacy["graph_bytes"],
            }
            checks["source"] = {"ok": True, "mode": "legacy"}
        except Exception as exc:
            checks["source"] = _failed_check(f"{type(exc).__name__}: {exc}")
            hard_failures.append(
                "source: manifest-referenced legacy evidence could not be read"
            )
            return report
    else:
        report["source"] = {"mode": "direct"}
        checks["source"] = {"ok": True, "mode": "direct"}

    try:
        evidence_state = resolve_asset_evidence_state(root, allow_stale=True)
        evidence_manifest = evidence_manifest_payload(evidence_state)
        agent_index_text = evidence_agent_index_text(evidence_state)
    except Exception as exc:
        checks["artifacts"] = _failed_check(f"{type(exc).__name__}: {exc}")
        hard_failures.append("artifacts: indexed evidence could not be validated")
        return report
    database_path = evidence_state.database_path
    agent_index_path = evidence_state.agent_index_path
    try:
        with EvidenceQueryService.open(
            database_path,
            expected_sha256=evidence_state.database_sha256,
            expected_size=evidence_state.database_bytes,
        ) as service:
            connection = service._connection  # noqa: SLF001 - validator owns service lifetime
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA query_only = ON")
            sqlite_result = _sqlite_check(connection)
            checks["sqlite"] = sqlite_result
            database = _database_model(connection)
        report["identity"] = database["identity"]
    except Exception as exc:
        checks.setdefault("sqlite", _failed_check(f"{type(exc).__name__}: {exc}"))
        hard_failures.append("sqlite: database could not be validated")
        return report

    if not checks["sqlite"]["ok"]:  # type: ignore[index]
        hard_failures.append("sqlite: integrity or foreign-key check failed")

    source_mode = "legacy" if legacy is not None else "direct"
    if source_mode == "direct":
        identity = database["identity"]
        report["source"] = {
            "mode": "direct",
            "uassetPath": str(identity.get("uassetPath") or ""),
            "sourceManifestEntries": len(database.get("source_manifest") or []),
        }

    expected_parser_version = (
        LEGACY_CAPTURE_PARSER_VERSION
        if source_mode == "legacy"
        else DIRECT_PAYLOAD_PARSER_VERSION
    )
    versions = _versions_check(database["identity"], expected_parser_version)
    checks["versions"] = versions
    if not versions["ok"]:
        hard_failures.append("versions: parser or schema version is stale")

    try:
        source_manifest = _source_manifest_check(root, database, mode=source_mode)
    except Exception as exc:
        source_manifest = _failed_check(f"{type(exc).__name__}: {exc}")
        source_manifest["mode"] = source_mode
    checks["sourceManifest"] = source_manifest
    if not source_manifest["ok"]:
        hard_failures.append(
            "sourceManifest: database sources do not match the current capture"
        )

    if legacy is not None:
        reconciliation = _reconciliation_check(
            legacy["associations"], database["associations"]
        )
        checks["legacyReconciliation"] = reconciliation
        if not reconciliation["ok"]:
            hard_failures.append(
                "legacyReconciliation: source and v2 associations differ"
            )
    else:
        checks["legacyReconciliation"] = {
            "ok": True,
            "enabled": False,
            "reason": "direct capture has no legacy graph JSON to reconcile",
        }

    try:
        if evidence_state.source_kind == "INDEXED_V3_CURRENT":
            output_dir = root / "output"
            stale_output = sorted(
                path.relative_to(output_dir).as_posix()
                for path in output_dir.rglob("*")
                if _is_temp_or_stale(path)
            ) if output_dir.is_dir() else []
            staging_entries = sorted(
                path.relative_to(root).as_posix()
                for path in (root / "evidence").glob(".evidence-v3-stage-*")
            )
            artifacts = {
                "ok": not stale_output and not staging_entries,
                "mode": "v3-current",
                "revisionId": str(database["identity"].get("revisionId") or ""),
                "manifestSha256": evidence_state.manifest_sha256,
                "pointerSha256": evidence_state.pointer_sha256,
                "freshnessStatus": evidence_state.freshness_status,
                "releaseAuthority": evidence_state.release_authority,
                "staleOutputFiles": stale_output,
                "stagingEntries": staging_entries,
                "errors": (
                    (["stale compatibility output exists"] if stale_output else [])
                    + (["unfinished evidence v3 staging directory exists"] if staging_entries else [])
                ),
            }
        else:
            artifacts = _artifact_check(root, evidence_manifest, database)
    except Exception as exc:
        artifacts = _failed_check(f"{type(exc).__name__}: {exc}")
    checks["artifacts"] = artifacts
    if not artifacts["ok"]:
        hard_failures.append("artifacts: expected v2 files or manifest contract failed")

    revision_id = str(database["identity"].get("revisionId") or "")
    agent_index = _agent_index_check(
        root,
        revision_id,
        database_path,
        index_path=agent_index_path,
        index_text=agent_index_text,
        index_bytes=evidence_state.agent_index_bytes,
        expected_database_sha256=evidence_state.database_sha256,
        expected_database_size=evidence_state.database_bytes,
    )
    checks["agentIndex"] = agent_index
    if not agent_index["ok"]:
        hard_failures.append("agentIndex: bounded index check failed")

    graph_bytes = int(legacy["graph_bytes"]) if legacy is not None else 0
    size_ratio = _size_ratio_check(root, graph_bytes, max_size_ratio)
    checks["sizeRatio"] = size_ratio

    if legacy is not None:
        try:
            recall = _recall_check(
                database_path,
                legacy["recall"],
                database["node_refs"],
                database["default_refs"],
                expected_sha256=evidence_state.database_sha256,
                expected_size=evidence_state.database_bytes,
            )
        except Exception as exc:
            recall = _failed_check(f"{type(exc).__name__}: {exc}")
        checks["recall"] = recall
        if not recall["ok"]:
            hard_failures.append("recall: exact search plus entity retrieval failed")
    else:
        checks["recall"] = {
            "ok": True,
            "enabled": False,
            "reason": "direct capture integrity is validated without a legacy oracle",
        }

    if benchmark:
        first_recall = next(iter(legacy["recall"]), {}) if legacy is not None else {}
        first_search_name = next(iter(database.get("search_names") or []), "")
        search_name = str(first_recall.get("name") or first_search_name or "*")
        first_node_ref = next(iter(database["node_refs"].values()), "")
        try:
            benchmark_result = _benchmark_check(
                database_path,
                iterations=benchmark_iterations,
                search_name=search_name,
                node_ref=first_node_ref,
                max_search_p95_ms=max_search_p95_ms,
                max_two_hop_p95_ms=max_two_hop_p95_ms,
                expected_sha256=evidence_state.database_sha256,
                expected_size=evidence_state.database_bytes,
            )
        except Exception as exc:
            benchmark_result = _failed_check(f"{type(exc).__name__}: {exc}")
            benchmark_result["enabled"] = True
        checks["benchmark"] = benchmark_result
        if not benchmark_result["ok"]:
            hard_failures.append("benchmark: latency target failed")
    else:
        checks["benchmark"] = {"ok": True, "enabled": False}

    report["ok"] = not hard_failures
    return report


def validate_index_consistency(asset_dir: str | Path) -> dict[str, object]:
    """Validate only SQLite integrity and the bounded index projection.

    This deliberately does not inspect the current DevKit binary or legacy
    capture inputs.  It is the narrow release gate for proving that an
    ``agent_index.md`` still describes the SQLite revision beside it.  Full
    source freshness remains the responsibility of :func:`validate_asset`.
    """

    root = _lexical_absolute(asset_dir)
    try:
        evidence_state = resolve_asset_evidence_state(root, allow_stale=True)
        agent_index_text = evidence_agent_index_text(evidence_state)
    except Exception as exc:
        return {
            "assetDir": str(root),
            "mode": "index-only",
            "ok": False,
            "identity": {},
            "checks": {"artifacts": _failed_check(f"{type(exc).__name__}: {exc}")},
            "hardFailures": ["artifacts: indexed evidence could not be validated"],
        }
    database_path = evidence_state.database_path
    agent_index_path = evidence_state.agent_index_path
    report: dict[str, object] = {
        "assetDir": str(root),
        "mode": "index-only",
        "ok": False,
        "identity": {},
        "checks": {},
        "hardFailures": [],
    }
    checks: dict[str, object] = report["checks"]  # type: ignore[assignment]
    hard_failures: list[str] = report["hardFailures"]  # type: ignore[assignment]
    try:
        _require_plain_path_chain(root, label="asset directory")
        _require_plain_directory(root, label="asset directory")
    except (OSError, ValueError) as exc:
        checks["source"] = _failed_check(f"{type(exc).__name__}: {exc}")
        hard_failures.append("source: asset directory path is unsafe")
        return report
    try:
        with EvidenceQueryService.open(
            database_path,
            expected_sha256=evidence_state.database_sha256,
            expected_size=evidence_state.database_bytes,
        ) as service:
            connection = service._connection  # noqa: SLF001 - validator owns service lifetime
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA query_only = ON")
            sqlite_result = _sqlite_check(connection)
            database = _database_model(connection)
        checks["sqlite"] = sqlite_result
        report["identity"] = database["identity"]
    except Exception as exc:
        checks["sqlite"] = _failed_check(f"{type(exc).__name__}: {exc}")
        hard_failures.append("sqlite: database could not be validated")
        return report

    if not checks["sqlite"]["ok"]:  # type: ignore[index]
        hard_failures.append("sqlite: integrity or foreign-key check failed")
    revision_id = str(database["identity"].get("revisionId") or "")
    agent_index = _agent_index_check(
        root,
        revision_id,
        database_path,
        index_path=agent_index_path,
        index_text=agent_index_text,
        index_bytes=evidence_state.agent_index_bytes,
        expected_database_sha256=evidence_state.database_sha256,
        expected_database_size=evidence_state.database_bytes,
    )
    checks["agentIndex"] = agent_index
    if not agent_index["ok"]:
        hard_failures.append("agentIndex: bounded index differs from SQLite query contract")
    report["ok"] = not hard_failures
    return report


def discover_asset_dirs(capture_root: str | Path) -> list[Path]:
    """Return assets with a v3 current pointer or a complete v2 compatibility DB."""

    root = _lexical_absolute(capture_root)
    _require_plain_path_chain(root, label="capture root")
    _require_plain_directory(root, label="capture root")
    if not root.is_dir():
        raise FileNotFoundError(root)
    assets = {
        Path(os.path.abspath(os.fspath(path.parent.parent)))
        for path in root.rglob("current.json")
        if path.parent.name.casefold() == "evidence"
    }
    assets.update(
        Path(os.path.abspath(os.fspath(path.parent.parent)))
        for path in root.rglob("evidence.sqlite")
        if path.parent.name.casefold() == "evidence"
    )
    return sorted(
        assets,
        key=lambda path: str(path).casefold(),
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile legacy Blueprint evidence with the normalized v2 store."
    )
    parser.add_argument("--asset-dir", action="append", type=Path, default=[])
    parser.add_argument("--capture-root", type=Path)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate every evidence/evidence.sqlite asset below --capture-root.",
    )
    parser.add_argument(
        "--expected-asset-count",
        type=int,
        help="Fail when discovery/selection does not contain exactly this many assets.",
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help=(
            "Check SQLite integrity plus agent_index revision/count parity only; "
            "do not evaluate current source-capture freshness."
        ),
    )
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--benchmark-iterations", type=int, default=DEFAULT_BENCHMARK_ITERATIONS)
    parser.add_argument("--max-search-p95-ms", type=float, default=DEFAULT_MAX_SEARCH_P95_MS)
    parser.add_argument("--max-two-hop-p95-ms", type=float, default=DEFAULT_MAX_TWO_HOP_P95_MS)
    parser.add_argument("--max-size-ratio", type=float, default=DEFAULT_MAX_SIZE_RATIO)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    using_assets = bool(args.asset_dir)
    using_all = bool(args.capture_root and args.all)
    if using_assets == using_all:
        parser.error("use repeated --asset-dir or --capture-root ... --all, but not both")
    if args.all and not args.capture_root:
        parser.error("--all requires --capture-root")
    if args.capture_root and not args.all:
        parser.error("--capture-root requires --all")
    if args.max_size_ratio < 0:
        parser.error("--max-size-ratio must be non-negative")
    if args.expected_asset_count is not None and args.expected_asset_count < 0:
        parser.error("--expected-asset-count must be non-negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        asset_dirs = (
            [_lexical_absolute(path) for path in args.asset_dir]
            if args.asset_dir
            else discover_asset_dirs(args.capture_root)
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "assetCount": 0,
            "passed": 0,
            "failed": 0,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "reports": [],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2
    if args.index_only:
        reports = [validate_index_consistency(path) for path in asset_dirs]
    else:
        reports = [
            validate_asset(
                path,
                max_size_ratio=args.max_size_ratio,
                benchmark=args.benchmark,
                benchmark_iterations=args.benchmark_iterations,
                max_search_p95_ms=args.max_search_p95_ms,
                max_two_hop_p95_ms=args.max_two_hop_p95_ms,
            )
            for path in asset_dirs
        ]
    passed = sum(bool(report.get("ok")) for report in reports)
    asset_count_check = {
        "ok": (
            args.expected_asset_count is None
            or len(reports) == args.expected_asset_count
        ),
        "enabled": args.expected_asset_count is not None,
        "expected": args.expected_asset_count,
        "actual": len(reports),
        "errors": (
            []
            if args.expected_asset_count is None
            or len(reports) == args.expected_asset_count
            else [
                "asset count mismatch: "
                f"expected {args.expected_asset_count}, discovered {len(reports)}"
            ]
        ),
    }
    # The size contract is an aggregate gate over the complete legacy corpus.
    # A targeted --asset-dir validation keeps the ratio visible but must not
    # turn SQLite fixed overhead on one asset into a false hard failure.
    aggregate_size = (
        {
            "ok": True,
            "applicable": False,
            "enforced": False,
            "reason": "index-only mode does not evaluate storage size",
        }
        if args.index_only
        else _aggregate_size_check(
            reports,
            args.max_size_ratio,
            enforce=bool(args.all),
        )
    )
    payload = {
        "ok": (
            bool(reports)
            and passed == len(reports)
            and bool(asset_count_check["ok"])
            and bool(aggregate_size["ok"])
        ),
        "assetCount": len(reports),
        "passed": passed,
        "failed": len(reports) - passed,
        "mode": "index-only" if args.index_only else "full",
        "checks": {
            "assetCount": asset_count_check,
            "aggregateSize": aggregate_size,
        },
        "reports": reports,
    }
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
