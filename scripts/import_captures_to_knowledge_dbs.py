from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from blueprint_translator.evidence_values import downstream_default_metadata, default_value_is_usable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_DIR = PROJECT_ROOT / "knowledge_base" / "db"
DEFAULT_CAPTURE_ROOT = PROJECT_ROOT / "captures"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "knowledge_base" / "imports"

REFERENCE_RE = re.compile(r"/Game/[A-Za-z0-9_./:'-]+")

CATEGORY_DATABASES: dict[str, dict[str, Any]] = {
    "primal_game_data": {
        "filename": "primal_game_data.sqlite",
        "asset_table": "game_data_assets",
        "reference_table": "game_data_references",
        "tables": {
            "rules": "game_data_rules",
            "creatures": "registered_creatures",
            "items": "registered_items",
            "buffs": "registered_buffs",
            "loot": "registered_loot",
            "remaps": "remaps",
        },
    },
    "status_component_blueprint": {
        "filename": "status_components.sqlite",
        "asset_table": "status_assets",
        "reference_table": None,
        "tables": {
            "values": "status_values",
            "leveling": "leveling_rules",
            "growth": "growth_rules",
            "taming": "taming_status_rules",
        },
    },
    "primal_item_blueprint": {
        "filename": "primal_items.sqlite",
        "asset_table": "item_assets",
        "reference_table": "item_references",
        "tables": {
            "display": "item_display",
            "properties": "item_properties",
            "logic": "item_use_logic",
            "grants": "item_grants",
        },
    },
    "buff_blueprint": {
        "filename": "buffs.sqlite",
        "asset_table": "buff_assets",
        "reference_table": "buff_references",
        "tables": {
            "effects": "buff_effects",
            "triggers": "buff_triggers",
            "conditions": "buff_conditions",
            "stacks": "buff_stacks",
            "stat_modifiers": "buff_stat_modifiers",
        },
    },
    "loot_or_supply_crate": {
        "filename": "loot.sqlite",
        "asset_table": "loot_assets",
        "reference_table": "loot_references",
        "tables": {
            "crates": "loot_crates",
            "item_sets": "loot_item_sets",
            "entries": "loot_entries",
            "conditions": "loot_conditions",
            "rewards": "loot_rewards",
        },
    },
}

COMMON_IMPORT_TABLES = {
    "read_sources",
    "unresolved_work",
    "asset_references",
    "formula_candidates",
    "unresolved_formulas",
}

BENIGN_UNKNOWN_PROPERTIES = {
    "AdvancedPinDisplay",
    "BlueprintInternalUseOnly",
    "BlueprintProtected",
    "BlueprintReadOnly",
    "BlueprintReadWrite",
    "BlueprintType",
    "Category",
    "DisplayName",
    "ExposeOnSpawn",
    "Nodes",
    "MacroGraphReference",
    "RepNotifyFunc",
    "Schema",
    "ToolTip",
    "bCanToggleVisibility",
    "bCalculateAsPercent",
    "bCommentBubblePinned",
    "bEnforceConstCorrectness",
    "bForceKeepSynced",
    "bForceTickPoseAndServerUpdateMesh",
    "bIsMarkedForAdvancedDisplay",
    "bIsPureFunc",
    "bMadeAfterOverridePinRemoval",
    "bPropertyIsCustomized",
}
BENIGN_UNKNOWN_PREFIXES = (
    "CallFunc_",
    "K2Node_",
    "/Game/",
)
BENIGN_UNKNOWN_TYPES = {
    "Guid",
    "BPVariableDescription",
}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._")
    return cleaned or fallback


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_parse_error": str(exc), "_path": str(path)}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def value_type(info: dict[str, Any]) -> str:
    explicit = str(info.get("type") or "")
    if explicit:
        return explicit
    value = info.get("value")
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def source_payload(source: str, key: str, info: dict[str, Any]) -> str:
    payload = {
        "source": source,
        "key": key,
        "type": info.get("type", ""),
        "confidence": info.get("confidence", "unknown"),
        "raw": info,
    }
    return json_dumps(payload)


def clean_reference(value: str) -> str:
    return value.rstrip(".,;)]}\"'")


def reference_name(path: str) -> str:
    tail = path.rsplit(".", 1)[-1] if "." in path else path.rsplit("/", 1)[-1]
    return tail.removesuffix("_C")


def infer_reference_type(path: str, source_property: str = "") -> str:
    text = f"{path} {source_property}".lower()
    if "primalitem" in text or "/items/" in text:
        return "item"
    if "buff" in text:
        return "buff"
    if "supplycrate" in text or "loot" in text or "drop" in text:
        return "loot"
    if "character_bp" in text or "/dinos/" in text or "dino" in text:
        return "creature"
    if "statuscomponent" in text:
        return "status_component"
    if "primalgamedata" in text:
        return "game_data"
    return "asset"


def extract_references(value: Any) -> list[str]:
    text = value_to_text(value)
    refs: list[str] = []
    seen: set[str] = set()
    for match in REFERENCE_RE.finditer(text):
        ref = clean_reference(match.group(0))
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def capture_dir_for_asset(asset: dict[str, Any], capture_root: Path) -> Path:
    capture_dir = str(asset.get("capture_dir") or "").strip()
    if capture_dir:
        path = Path(capture_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return capture_root / safe_filename(str(asset.get("asset_name") or "BlueprintAsset"), "BlueprintAsset")


def load_capture(capture_dir: Path) -> dict[str, Any]:
    paths = {
        "package": capture_dir / "uasset_package.json",
        "graph_nodes": capture_dir / "uasset_graph_nodes.json",
        "class_defaults": capture_dir / "uasset_class_defaults.json",
        "failed_graphs": capture_dir / "uasset_failed_graph_queue.json",
        "partial_graphs": capture_dir / "uasset_partial_graph_triage.json",
        "unknown_properties": capture_dir / "uasset_unknown_properties.json",
        "formula_candidates": capture_dir / "output" / "formula_candidates.json",
        "asset_memory_card": capture_dir / "output" / "asset_memory_card.json",
        "context_pack": capture_dir / "output" / "context_pack.json",
        "evidence_store": capture_dir / "evidence" / "evidence.sqlite",
        "agent_index": capture_dir / "output" / "agent_index.md",
    }
    if paths["evidence_store"].is_file():
        from blueprint_translator.evidence_repository import open_asset_repository

        with open_asset_repository(capture_dir) as repository:
            overview = repository.query({"operation": "overview", "budgetTokens": 800})
            identity = repository.identity()
            graph_rows = repository.graph_summaries()
            node_rows = repository.node_summaries()
            defaults = repository.default_summaries(include_values=True)
            gaps = repository.gap_summaries()
        nodes_by_graph: dict[str, list[dict[str, object]]] = {}
        for node in node_rows:
            nodes_by_graph.setdefault(str(node.get("graph_ref") or ""), []).append(node)
        graphs = [
            {
                "graph": row.get("name"),
                "graph_type": row.get("graph_type"),
                "export_index": row.get("export_index"),
                "status": row.get("status"),
                "confidence": row.get("confidence"),
                "node_count": row.get("node_count"),
                "pin_count": row.get("pin_count"),
                "link_count": row.get("link_count"),
                "coverage": row.get("coverage", {}),
                "nodes": nodes_by_graph.get(str(row.get("ref") or ""), []),
                "evidence_ref": row.get("ref"),
            }
            for row in graph_rows
        ]
        summary = overview.get("summary", {})
        graph_nodes = {
            "asset_name": identity.get("asset_name"),
            "asset_path": identity.get("object_path"),
            "uasset_path": identity.get("uasset_path"),
            "revision_id": identity.get("revision_id"),
            "graph_count": summary.get("graphCount", 0),
            "node_count": summary.get("nodeCount", 0),
            "pin_count": summary.get("pinCount", 0),
            "link_count": summary.get("linkObservationCount", 0),
            "graphs": graphs,
        }
        class_defaults = {
            "asset_name": identity.get("asset_name"),
            "variables": {
                str(row["name"]): {
                    "value": row.get("value"),
                    "type": row.get("type"),
                    "source": row.get("source"),
                    "confidence": row.get("confidence"),
                    "evidence_ref": row.get("ref"),
                    **downstream_default_metadata(row),
                }
                for row in defaults
            },
        }
        partial_graphs = {
            "graphs": [
                {
                    "evidence_ref": row.get("ref"),
                    "scope_kind": row.get("scope_kind"),
                    "scope_ref": row.get("scope_ref"),
                    "name": row.get("name"),
                    "status": row.get("status"),
                    "primary_reason": row.get("reason_code"),
                    "reasons": [row.get("reason_code")],
                    "detail": row.get("detail"),
                    "next_probe": row.get("next_probe"),
                    "next_action": row.get("next_probe"),
                }
                for row in gaps
            ]
        }
        return {
            "capture_dir": capture_dir,
            "paths": paths,
            "package": {
                "uasset_path": identity.get("uasset_path"),
                "summary": {"package_name": str(identity.get("object_path") or "").split(".", 1)[0]},
            },
            "graph_nodes": graph_nodes,
            "class_defaults": class_defaults,
            # A structured evidence gap is one fact, not both a failed and a
            # partial graph. Keep it in the partial/triage channel so downstream
            # imports do not create two contradictory rows for the same ref.
            "failed_graphs": {"graphs": []},
            "partial_graphs": partial_graphs,
            "unknown_properties": {},
            "formula_candidates": read_json(paths["formula_candidates"], {}),
            "asset_memory_card": read_json(paths["asset_memory_card"], {}),
            "context_pack": read_json(paths["context_pack"], {}),
        }
    return {
        "capture_dir": capture_dir,
        "paths": paths,
        "package": read_json(paths["package"], {}),
        "graph_nodes": read_json(paths["graph_nodes"], {}),
        "class_defaults": read_json(paths["class_defaults"], {}),
        "failed_graphs": read_json(paths["failed_graphs"], {}),
        "partial_graphs": read_json(paths["partial_graphs"], {}),
        "unknown_properties": read_json(paths["unknown_properties"], {}),
        "formula_candidates": read_json(paths["formula_candidates"], {}),
        "asset_memory_card": read_json(paths["asset_memory_card"], {}),
        "context_pack": read_json(paths["context_pack"], {}),
    }


def variables_from_capture(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = capture.get("class_defaults") or {}
    variables = payload.get("variables") or {}
    if not isinstance(variables, dict):
        return {}
    return {
        str(key): value
        for key, value in variables.items()
        if isinstance(value, dict) and default_value_is_usable(value)
    }


def has_unresolved_payload(capture: dict[str, Any]) -> bool:
    """Return whether importing this capture would preserve actionable gaps."""

    for section_name in ("failed_graphs", "partial_graphs"):
        section = capture.get(section_name) or {}
        if isinstance(section, dict) and any(
            isinstance(row, dict) for row in section.get("graphs") or []
        ):
            return True
    unknown = capture.get("unknown_properties") or {}
    unknown_items = (
        unknown.get("unknown_properties") or unknown.get("items") or []
        if isinstance(unknown, dict)
        else []
    )
    return any(
        isinstance(item, dict)
        and not is_benign_unknown_property(
            item,
            str(item.get("name") or item.get("property") or item.get("class") or ""),
        )
        for item in unknown_items
    )


def iter_graphs(capture: dict[str, Any]) -> list[dict[str, Any]]:
    payload = capture.get("graph_nodes") or {}
    graphs = payload.get("graphs") or []
    return [graph for graph in graphs if isinstance(graph, dict)]


def iter_nodes(capture: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for graph in iter_graphs(capture):
        for node in graph.get("nodes") or []:
            if isinstance(node, dict):
                rows.append((graph, node))
    return rows


def iter_reference_rows(variables: dict[str, dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key, info in variables.items():
        confidence = str(info.get("confidence") or "unknown")
        for ref in extract_references(info.get("value")):
            marker = (key, ref)
            if marker in seen:
                continue
            seen.add(marker)
            rows.append((ref, infer_reference_type(ref, key), key, confidence))
    return rows


def source_files(capture: dict[str, Any]) -> dict[str, str]:
    paths = capture.get("paths") or {}
    return {
        key: str(path)
        for key, path in paths.items()
        if isinstance(path, Path) and path.is_file()
    }


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    cursor = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    return cursor.fetchone() is not None


def clear_import_tables(connection: sqlite3.Connection, config: dict[str, Any]) -> None:
    table_names = set(COMMON_IMPORT_TABLES)
    reference_table = config.get("reference_table")
    if reference_table:
        table_names.add(str(reference_table))
    for table_name in (config.get("tables") or {}).values():
        if table_name == "deferred_creature_status":
            continue
        table_names.add(str(table_name))
    for table_name in sorted(table_names):
        if table_exists(connection, table_name):
            connection.execute(f"DELETE FROM {table_name}")


def read_asset_rows(connection: sqlite3.Connection, asset_table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f"SELECT * FROM {asset_table}")
    columns = [item[0] for item in cursor.description or []]
    rows: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        payload = dict(zip(columns, row))
        capture_hint = str(payload.get("capture_dir") or "")
        if payload.get("processed_current") or payload.get("captured") or capture_hint:
            rows.append(payload)
    return rows


def insert_read_sources(
    connection: sqlite3.Connection,
    object_path: str,
    asset: dict[str, Any],
    capture: dict[str, Any],
) -> None:
    paths = source_files(capture)
    connection.execute(
        """
        INSERT OR REPLACE INTO read_sources (
            object_path, capture_dir, package_json, graph_nodes_json,
            class_defaults_json, last_read_at, read_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            object_path,
            str(capture.get("capture_dir") or ""),
            paths.get("package", ""),
            paths.get("graph_nodes", ""),
            paths.get("class_defaults", ""),
            str(asset.get("last_read_at") or ""),
            str(asset.get("read_status") or ""),
        ),
    )


def insert_common_references(
    connection: sqlite3.Connection,
    object_path: str,
    variables: dict[str, dict[str, Any]],
    reference_table: str | None,
) -> int:
    rows = iter_reference_rows(variables)
    for ref, ref_type, source_property, confidence in rows:
        connection.execute(
            """
            INSERT INTO asset_references (
                object_path, reference_path, reference_type, source_property, confidence
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (object_path, ref, ref_type, source_property, confidence),
        )
        if reference_table:
            connection.execute(
                f"""
                INSERT INTO {reference_table} (
                    object_path, reference_path, reference_type, source_property, confidence
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_path, ref, ref_type, source_property, confidence),
            )
    return len(rows)


def insert_unresolved_work(connection: sqlite3.Connection, object_path: str, capture: dict[str, Any]) -> int:
    count = 0
    failed = capture.get("failed_graphs") or {}
    for graph in failed.get("graphs") or []:
        if not isinstance(graph, dict):
            continue
        detail = str(graph.get("graph") or "")
        category = str(graph.get("primary_category") or ",".join(graph.get("failure_categories") or []) or "unknown")
        connection.execute(
            """
            INSERT INTO unresolved_work (object_path, work_type, detail, source_json, status)
            VALUES (?, ?, ?, ?, 'open')
            """,
            (object_path, "failed_graph", f"{detail}: {category}", json_dumps(graph)),
        )
        count += 1

    partial = capture.get("partial_graphs") or {}
    for graph in partial.get("graphs") or []:
        if not isinstance(graph, dict):
            continue
        scope_kind = str(graph.get("scope_kind") or "graph")
        detail = str(graph.get("graph") or graph.get("name") or graph.get("detail") or graph.get("scope_ref") or "")
        reason = str(graph.get("primary_reason") or ",".join(graph.get("reasons") or []) or "unknown")
        connection.execute(
            """
            INSERT INTO unresolved_work (object_path, work_type, detail, source_json, status)
            VALUES (?, ?, ?, ?, 'open')
            """,
            (
                object_path,
                "default_value_gap" if scope_kind == "default" else "partial_graph",
                f"{detail}: {reason}",
                json_dumps(graph),
            ),
        )
        count += 1

    unknown = capture.get("unknown_properties") or {}
    unknown_items = unknown.get("unknown_properties") or unknown.get("items") or []
    for item in unknown_items:
        if not isinstance(item, dict):
            continue
        detail = str(item.get("name") or item.get("property") or item.get("class") or "unknown_property")
        if is_benign_unknown_property(item, detail):
            continue
        connection.execute(
            """
            INSERT INTO unresolved_work (object_path, work_type, detail, source_json, status)
            VALUES (?, ?, ?, ?, 'open')
            """,
            (object_path, "unknown_property", detail, json_dumps(item)),
        )
        count += 1
    return count


def object_row_count(connection: sqlite3.Connection, table: str, object_path: str) -> int:
    if not table or not table_exists(connection, table):
        return 0
    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE object_path = ?",
            (object_path,),
        ).fetchone()[0]
    )


def property_parser_blockers(capture: dict[str, Any]) -> list[dict[str, Any]]:
    payload = capture.get("class_defaults") or {}
    properties = payload.get("properties") or []
    blockers: list[dict[str, Any]] = []
    if not isinstance(properties, list):
        return blockers
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        type_name = str(prop.get("type") or "")
        name = str(prop.get("name") or "")
        if type_name == "ArrayProperty":
            array_parse = prop.get("array_parse", {})
            parsed = bool(isinstance(array_parse, dict) and array_parse.get("parsed"))
            if not parsed:
                blockers.append(
                    {
                        "name": name,
                        "type": type_name,
                        "parse": array_parse if isinstance(array_parse, dict) else {},
                        "reason": "ArrayProperty did not resolve element references.",
                        "source_property": prop,
                    }
                )
        elif type_name == "StructProperty":
            struct_parse = prop.get("struct_parse", {})
            value = prop.get("value")
            parsed = bool(isinstance(struct_parse, dict) and struct_parse.get("parsed"))
            if not parsed or (isinstance(value, dict) and value.get("parsed") is False):
                blockers.append(
                    {
                        "name": name,
                        "type": type_name,
                        "parse": struct_parse if isinstance(struct_parse, dict) else {},
                        "reason": "StructProperty is present but not decoded into usable fields.",
                        "source_property": prop,
                    }
                )
        elif type_name == "MapProperty":
            blockers.append(
                {
                    "name": name,
                    "type": type_name,
                    "parse": prop.get("value") if isinstance(prop.get("value"), dict) else {},
                    "reason": "MapProperty parser is not implemented for this field.",
                    "source_property": prop,
                }
            )
    return blockers


def insert_loot_property_parser_needed(
    connection: sqlite3.Connection,
    object_path: str,
    capture: dict[str, Any],
    config: dict[str, Any],
) -> int:
    tables = config.get("tables") or {}
    item_set_count = object_row_count(connection, str(tables.get("item_sets") or ""), object_path)
    entry_count = object_row_count(connection, str(tables.get("entries") or ""), object_path)
    if item_set_count or entry_count or not table_exists(connection, "unresolved_work"):
        return 0

    blockers = property_parser_blockers(capture)
    count = 0
    for blocker in blockers:
        detail = (
            f"ArrayProperty/StructProperty for loot item sets: "
            f"{blocker.get('name') or 'unknown'} ({blocker.get('type') or 'unknown'})"
        )
        connection.execute(
            """
            INSERT INTO unresolved_work (object_path, work_type, detail, source_json, status)
            VALUES (?, 'property_parser_needed', ?, ?, 'open')
            """,
            (object_path, detail, json_dumps(blocker)),
        )
        count += 1
    return count


def insert_formula_candidates(
    connection: sqlite3.Connection,
    object_path: str,
    asset: dict[str, Any],
    capture: dict[str, Any],
) -> int:
    if not table_exists(connection, "formula_candidates"):
        return 0
    payload = capture.get("formula_candidates") or {}
    if not isinstance(payload, dict):
        return 0
    candidates = [item for item in payload.get("candidates", []) if isinstance(item, dict)]
    generated_at = str(payload.get("generated_at") or now_iso())
    source_capture = str(capture.get("capture_dir") or "")
    asset_name = str(payload.get("asset_name") or asset.get("asset_name") or "")
    asset_type = str(payload.get("asset_type") or asset.get("asset_type") or "")
    count = 0
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate.get("id") or f"{asset_name}_{index}_formula_candidate")
        connection.execute(
            """
            INSERT OR REPLACE INTO formula_candidates (
                id, object_path, asset_name, asset_type, domain, mechanism_type,
                mechanism, player_meaning, graph, visible_rule, formula_text,
                formula_ast_json, inputs_json, outputs_json, conditions_json,
                math_nodes_json, evidence_json, link_quality_json,
                external_dependencies_json, missing_evidence_json, next_probe_json,
                confidence, status, source_capture, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                object_path,
                asset_name,
                asset_type,
                str(candidate.get("domain") or ""),
                str(candidate.get("mechanism_type") or ""),
                str(candidate.get("mechanism") or ""),
                str(candidate.get("player_meaning") or ""),
                str(candidate.get("graph") or ""),
                str(candidate.get("visible_rule") or ""),
                str(candidate.get("formula_text") or ""),
                json_dumps(candidate.get("formula_ast") or {}),
                json_dumps(candidate.get("inputs") or []),
                json_dumps(candidate.get("outputs") or []),
                json_dumps(candidate.get("conditions") or []),
                json_dumps(candidate.get("math_nodes") or []),
                json_dumps(candidate.get("evidence") or []),
                json_dumps(candidate.get("link_quality") or {}),
                json_dumps(candidate.get("external_dependencies") or []),
                json_dumps(candidate.get("missing_evidence") or []),
                json_dumps(candidate.get("next_probe") or []),
                str(candidate.get("confidence") or "unknown"),
                str(candidate.get("status") or "candidate"),
                source_capture,
                generated_at,
                now_iso(),
            ),
        )
        count += 1
    return count


def insert_unresolved_formulas(
    connection: sqlite3.Connection,
    object_path: str,
    asset: dict[str, Any],
    capture: dict[str, Any],
) -> int:
    if not table_exists(connection, "unresolved_formulas"):
        return 0
    payload = capture.get("formula_candidates") or {}
    if not isinstance(payload, dict):
        return 0
    unresolved = [item for item in payload.get("unresolved_formulas", []) if isinstance(item, dict)]
    generated_at = str(payload.get("generated_at") or now_iso())
    source_capture = str(capture.get("capture_dir") or "")
    asset_name = str(payload.get("asset_name") or asset.get("asset_name") or "")
    asset_type = str(payload.get("asset_type") or asset.get("asset_type") or "")
    count = 0
    for index, item in enumerate(unresolved, start=1):
        unresolved_id = str(item.get("id") or f"{asset_name}_{index}_unresolved_formula")
        mechanism_type = str(item.get("mechanism_type") or "")
        mechanism = str(item.get("mechanism") or "")
        connection.execute(
            """
            INSERT OR REPLACE INTO unresolved_formulas (
                id, candidate_id, object_path, asset_name, asset_type, domain,
                mechanism_type, mechanism, known_visible_part, blocked_by_json,
                missing_evidence_json, required_next_probe_json, priority, status,
                confidence, source_capture, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unresolved_id,
                str(item.get("candidate_id") or ""),
                object_path,
                asset_name,
                asset_type,
                str(item.get("domain") or ""),
                mechanism_type,
                mechanism,
                str(item.get("known_visible_part") or ""),
                json_dumps(item.get("blocked_by") or []),
                json_dumps(item.get("missing_evidence") or []),
                json_dumps(item.get("required_next_probe") or []),
                int(item.get("priority") or 50),
                str(item.get("status") or "open"),
                str(item.get("confidence") or "unresolved_formula"),
                source_capture,
                generated_at,
                now_iso(),
            ),
        )
        if table_exists(connection, "unresolved_work"):
            detail = f"{mechanism_type}: {mechanism}".strip(": ")
            connection.execute(
                """
                INSERT INTO unresolved_work (object_path, work_type, detail, source_json, status)
                VALUES (?, 'formula_unresolved_dependency', ?, ?, 'open')
                """,
                (object_path, detail, json_dumps(item)),
            )
        count += 1
    return count


def is_benign_unknown_property(item: dict[str, Any], detail: str) -> bool:
    property_name = str(item.get("property") or item.get("name") or detail or "")
    type_name = str(item.get("type") or "")
    error = str(item.get("error") or "")
    if error:
        return False
    if property_name in BENIGN_UNKNOWN_PROPERTIES:
        return True
    if type_name in BENIGN_UNKNOWN_TYPES and property_name in BENIGN_UNKNOWN_PROPERTIES:
        return True
    if any(property_name.startswith(prefix) for prefix in BENIGN_UNKNOWN_PREFIXES):
        return True
    if property_name.startswith("b") and property_name in BENIGN_UNKNOWN_PROPERTIES:
        return True
    return False


def upsert_metadata(connection: sqlite3.Connection, summary: dict[str, Any]) -> None:
    if not table_exists(connection, "metadata"):
        return
    rows = {
        "capture_import_generated": summary.get("generated", ""),
        "capture_import_assets": str(summary.get("assets_imported", 0)),
        "capture_import_variables": str(summary.get("variables_imported", 0)),
        "capture_import_references": str(summary.get("references_imported", 0)),
        "capture_import_unresolved": str(summary.get("unresolved_imported", 0)),
        "capture_import_formula_candidates": str(summary.get("formula_candidates_imported", 0)),
        "capture_import_unresolved_formulas": str(summary.get("unresolved_formulas_imported", 0)),
    }
    connection.executemany("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", rows.items())


def looks_like_status_value(name: str) -> bool:
    lowered = name.lower()
    return any(
        word in lowered
        for word in (
            "status",
            "health",
            "stamina",
            "oxygen",
            "food",
            "water",
            "weight",
            "torpor",
            "torpidity",
            "fortitude",
            "speed",
            "damage",
            "experience",
            "xp",
            "level",
        )
    )


def is_level_rule(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in ("level", "experience", "xp", "points"))


def is_growth_rule(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in ("baby", "mature", "maturation", "growth", "imprint"))


def is_taming_rule(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in ("tame", "taming", "torpor", "wild"))


def import_status_asset(connection: sqlite3.Connection, object_path: str, variables: dict[str, dict[str, Any]]) -> int:
    count = 0
    for key, info in variables.items():
        if looks_like_status_value(key) or not variables:
            connection.execute(
                """
                INSERT INTO status_values (
                    object_path, stat_name, base_value, per_level_value,
                    value_type, confidence, source_json
                )
                VALUES (?, ?, ?, '', ?, ?, ?)
                """,
                (
                    object_path,
                    key,
                    value_to_text(info.get("value")),
                    value_type(info),
                    str(info.get("confidence") or "unknown"),
                    source_payload("uasset_class_defaults", key, info),
                ),
            )
            count += 1
        if is_level_rule(key):
            connection.execute(
                """
                INSERT INTO leveling_rules (object_path, rule_key, rule_value, confidence, source_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_path, key, value_to_text(info.get("value")), str(info.get("confidence") or "unknown"), source_payload("uasset_class_defaults", key, info)),
            )
            count += 1
        if is_growth_rule(key):
            connection.execute(
                """
                INSERT INTO growth_rules (object_path, rule_key, rule_value, confidence, source_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_path, key, value_to_text(info.get("value")), str(info.get("confidence") or "unknown"), source_payload("uasset_class_defaults", key, info)),
            )
            count += 1
        if is_taming_rule(key):
            connection.execute(
                """
                INSERT INTO taming_status_rules (object_path, rule_key, rule_value, confidence, source_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_path, key, value_to_text(info.get("value")), str(info.get("confidence") or "unknown"), source_payload("uasset_class_defaults", key, info)),
            )
            count += 1
    return count


def import_item_asset(
    connection: sqlite3.Connection,
    object_path: str,
    variables: dict[str, dict[str, Any]],
    capture: dict[str, Any],
) -> int:
    count = 0
    name = value_to_text((variables.get("DescriptiveNameBase") or variables.get("ItemName") or {}).get("value"))
    description = value_to_text((variables.get("ItemDescription") or variables.get("Description") or {}).get("value"))
    category = value_to_text((variables.get("ItemType") or variables.get("ItemCategory") or {}).get("value"))
    icon = value_to_text((variables.get("ItemIcon") or variables.get("SoftTexture") or variables.get("Icon") or {}).get("value"))
    if name or description or category or icon:
        connection.execute(
            """
            INSERT OR REPLACE INTO item_display (
                object_path, item_name, description, category, icon_path, confidence
            )
            VALUES (?, ?, ?, ?, ?, 'medium')
            """,
            (object_path, name, description, category, icon),
        )
        count += 1

    for key, info in variables.items():
        confidence = str(info.get("confidence") or "unknown")
        connection.execute(
            """
            INSERT INTO item_properties (
                object_path, property_name, property_value, value_type, confidence, source_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (object_path, key, value_to_text(info.get("value")), value_type(info), confidence, source_payload("uasset_class_defaults", key, info)),
        )
        count += 1
        for ref in extract_references(info.get("value")):
            ref_type = infer_reference_type(ref, key)
            if ref_type in {"buff", "item", "loot", "creature"} or any(word in key.lower() for word in ("grant", "class", "buff", "reward", "poi")):
                connection.execute(
                    """
                    INSERT INTO item_grants (
                        object_path, grant_type, grant_path, grant_value, confidence
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (object_path, ref_type, ref, value_to_text(info.get("value")), confidence),
                )
                count += 1

    for graph, node in iter_nodes(capture):
        trigger = str(node.get("event") or node.get("function") or node.get("variable") or node.get("name") or "")
        node_class = str(node.get("class") or node.get("class_name") or node.get("node_type") or "")
        if not trigger or node_class not in {"K2Node_Event", "K2Node_CustomEvent", "K2Node_CallFunction", "K2Node_CallParentFunction", "K2Node_FunctionEntry"}:
            continue
        connection.execute(
            """
            INSERT INTO item_use_logic (
                object_path, trigger_name, effect_summary, source_graph, confidence
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (object_path, trigger, node_class, str(graph.get("graph") or ""), str(node.get("confidence") or graph.get("confidence") or "unknown")),
        )
        count += 1
    return count


def is_buff_condition(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("b") or any(word in lowered for word in ("prevent", "allow", "require", "can", "min", "max", "threshold"))


def is_stack_rule(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in ("stack", "bufflevel", "level"))


def is_stat_modifier(name: str) -> bool:
    lowered = name.lower()
    return any(
        word in lowered
        for word in (
            "damage",
            "resistance",
            "health",
            "stamina",
            "food",
            "water",
            "speed",
            "movement",
            "melee",
            "experience",
            "xp",
            "multiplier",
        )
    )


def import_buff_asset(
    connection: sqlite3.Connection,
    object_path: str,
    variables: dict[str, dict[str, Any]],
    capture: dict[str, Any],
) -> int:
    count = 0
    for key, info in variables.items():
        confidence = str(info.get("confidence") or "unknown")
        value = value_to_text(info.get("value"))
        duration = value if any(word in key.lower() for word in ("duration", "deactivateaftertime")) else ""
        interval = value if "interval" in key.lower() or "tick" in key.lower() else ""
        connection.execute(
            """
            INSERT INTO buff_effects (
                object_path, effect_key, effect_value, duration, interval, confidence, source_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (object_path, key, value, duration, interval, confidence, source_payload("uasset_class_defaults", key, info)),
        )
        count += 1
        if is_buff_condition(key):
            connection.execute(
                """
                INSERT INTO buff_conditions (
                    object_path, condition_key, condition_value, confidence, source_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_path, key, value, confidence, source_payload("uasset_class_defaults", key, info)),
            )
            count += 1
        if is_stack_rule(key):
            connection.execute(
                """
                INSERT INTO buff_stacks (
                    object_path, stack_key, stack_value, confidence, source_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_path, key, value, confidence, source_payload("uasset_class_defaults", key, info)),
            )
            count += 1
        if is_stat_modifier(key):
            operation = "multiplier" if "multiplier" in key.lower() else "value"
            connection.execute(
                """
                INSERT INTO buff_stat_modifiers (
                    object_path, stat_name, operation, value, confidence, source_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (object_path, key, operation, value, confidence, source_payload("uasset_class_defaults", key, info)),
            )
            count += 1

    trigger_classes = {
        "K2Node_Event",
        "K2Node_CustomEvent",
        "K2Node_ComponentBoundEvent",
        "K2Node_FunctionEntry",
        "K2Node_CallFunction",
        "K2Node_CallParentFunction",
    }
    for graph, node in iter_nodes(capture):
        node_class = str(node.get("class") or node.get("class_name") or node.get("node_type") or "")
        if node_class not in trigger_classes:
            continue
        trigger_name = str(node.get("event") or node.get("function") or node.get("delegate") or node.get("name") or "")
        if not trigger_name:
            continue
        connection.execute(
            """
            INSERT INTO buff_triggers (
                object_path, trigger_name, graph_name, function_name, confidence
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                object_path,
                trigger_name,
                str(graph.get("graph") or ""),
                str(node.get("function") or ""),
                str(node.get("confidence") or graph.get("confidence") or "unknown"),
            ),
        )
        count += 1
    return count


def import_game_data_asset(connection: sqlite3.Connection, object_path: str, variables: dict[str, dict[str, Any]]) -> int:
    count = 0
    for key, info in variables.items():
        confidence = str(info.get("confidence") or "unknown")
        connection.execute(
            """
            INSERT INTO game_data_rules (
                object_path, rule_key, rule_value, value_type, confidence, source_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (object_path, key, value_to_text(info.get("value")), value_type(info), confidence, source_payload("uasset_class_defaults", key, info)),
        )
        count += 1
        for ref in extract_references(info.get("value")):
            ref_type = infer_reference_type(ref, key)
            if "remap" in key.lower():
                connection.execute(
                    """
                    INSERT INTO remaps (
                        object_path, remap_type, from_path, to_path, source_property, confidence
                    )
                    VALUES (?, ?, '', ?, ?, ?)
                    """,
                    (object_path, ref_type, ref, key, confidence),
                )
                count += 1
            elif ref_type == "creature":
                connection.execute(
                    """
                    INSERT INTO registered_creatures (
                        object_path, creature_path, creature_name, source_property, confidence
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (object_path, ref, reference_name(ref), key, confidence),
                )
                count += 1
            elif ref_type == "item":
                connection.execute(
                    """
                    INSERT INTO registered_items (
                        object_path, item_path, item_name, source_property, confidence
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (object_path, ref, reference_name(ref), key, confidence),
                )
                count += 1
            elif ref_type == "buff":
                connection.execute(
                    """
                    INSERT INTO registered_buffs (
                        object_path, buff_path, buff_name, source_property, confidence
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (object_path, ref, reference_name(ref), key, confidence),
                )
                count += 1
            elif ref_type == "loot":
                connection.execute(
                    """
                    INSERT INTO registered_loot (
                        object_path, loot_path, loot_name, source_property, confidence
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (object_path, ref, reference_name(ref), key, confidence),
                )
                count += 1
    return count


def vector_min_max(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        return value_to_text(value.get("x")), value_to_text(value.get("y"))
    return "", ""


def import_loot_asset(connection: sqlite3.Connection, object_path: str, variables: dict[str, dict[str, Any]]) -> int:
    count = 0
    crate_type = ""
    quality_min = ""
    quality_max = ""
    level_requirement = ""
    for key, info in variables.items():
        lowered = key.lower()
        if not crate_type and ("crate" in lowered or "loot" in lowered):
            crate_type = key
        if "quality" in lowered and ("minmax" in lowered or "range" in lowered):
            quality_min, quality_max = vector_min_max(info.get("value"))
        if "level" in lowered and ("require" in lowered or "min" in lowered):
            level_requirement = value_to_text(info.get("value"))
    if crate_type or quality_min or quality_max or level_requirement:
        connection.execute(
            """
            INSERT OR REPLACE INTO loot_crates (
                object_path, crate_type, quality_min, quality_max, level_requirement, confidence
            )
            VALUES (?, ?, ?, ?, ?, 'medium')
            """,
            (object_path, crate_type, quality_min, quality_max, level_requirement),
        )
        count += 1

    for key, info in variables.items():
        confidence = str(info.get("confidence") or "unknown")
        value = value_to_text(info.get("value"))
        lowered = key.lower()
        if "itemset" in lowered or "lootset" in lowered or lowered.endswith("sets"):
            connection.execute(
                """
                INSERT INTO loot_item_sets (
                    object_path, set_name, set_weight, confidence, source_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_path, key, value, confidence, source_payload("uasset_class_defaults", key, info)),
            )
            count += 1
        if any(word in lowered for word in ("reward", "loot", "quality", "experience", "xp", "item", "crate")):
            connection.execute(
                """
                INSERT INTO loot_rewards (
                    object_path, reward_type, reward_value, confidence, source_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_path, key, value, confidence, source_payload("uasset_class_defaults", key, info)),
            )
            count += 1
        if any(word in lowered for word in ("min", "max", "require", "allow", "prevent", "chance", "level")):
            connection.execute(
                """
                INSERT INTO loot_conditions (
                    object_path, condition_key, condition_value, confidence
                )
                VALUES (?, ?, ?, ?)
                """,
                (object_path, key, value, confidence),
            )
            count += 1
        for ref in extract_references(info.get("value")):
            connection.execute(
                """
                INSERT INTO loot_entries (
                    object_path, item_path, entry_weight, quantity_min,
                    quantity_max, quality_min, quality_max, blueprint_chance, confidence
                )
                VALUES (?, ?, '', '', '', '', '', '', ?)
                """,
                (object_path, ref, confidence),
            )
            count += 1
    return count


def import_category_asset(
    connection: sqlite3.Connection,
    group_id: str,
    object_path: str,
    variables: dict[str, dict[str, Any]],
    capture: dict[str, Any],
) -> int:
    if group_id == "primal_game_data":
        return import_game_data_asset(connection, object_path, variables)
    if group_id == "status_component_blueprint":
        return import_status_asset(connection, object_path, variables)
    if group_id == "primal_item_blueprint":
        return import_item_asset(connection, object_path, variables, capture)
    if group_id == "buff_blueprint":
        return import_buff_asset(connection, object_path, variables, capture)
    if group_id == "loot_or_supply_crate":
        return import_loot_asset(connection, object_path, variables)
    return 0


def import_business_database(
    db_path: Path,
    group_id: str,
    config: dict[str, Any],
    capture_root: Path,
    *,
    clear_existing: bool = True,
) -> dict[str, Any]:
    summary = {
        "group_id": group_id,
        "database": str(db_path),
        "assets_seen": 0,
        "assets_imported": 0,
        "variables_imported": 0,
        "references_imported": 0,
        "unresolved_imported": 0,
        "formula_candidates_imported": 0,
        "unresolved_formulas_imported": 0,
        "semantic_rows_imported": 0,
        "skipped_no_capture": 0,
        "skipped_no_payload": 0,
    }
    if not db_path.is_file():
        summary["missing_database"] = True
        return summary

    connection = sqlite3.connect(db_path)
    try:
        if clear_existing:
            clear_import_tables(connection, config)
        asset_rows = read_asset_rows(connection, str(config["asset_table"]))
        summary["assets_seen"] = len(asset_rows)
        for asset in asset_rows:
            object_path = str(asset.get("object_path") or "")
            if not object_path:
                continue
            capture_dir = capture_dir_for_asset(asset, capture_root)
            if not capture_dir.is_dir():
                summary["skipped_no_capture"] += 1
                continue
            capture = load_capture(capture_dir)
            variables = variables_from_capture(capture)
            has_graph_nodes = bool((capture.get("graph_nodes") or {}).get("graphs"))
            formula_payload = capture.get("formula_candidates") or {}
            has_formula_payload = bool(
                formula_payload.get("candidates") or formula_payload.get("unresolved_formulas")
            ) if isinstance(formula_payload, dict) else False
            has_unresolved = has_unresolved_payload(capture)
            if not variables and not has_graph_nodes and not has_formula_payload and not has_unresolved:
                summary["skipped_no_payload"] += 1
                continue

            insert_read_sources(connection, object_path, asset, capture)
            ref_count = insert_common_references(connection, object_path, variables, config.get("reference_table"))
            unresolved_count = insert_unresolved_work(connection, object_path, capture)
            formula_count = insert_formula_candidates(connection, object_path, asset, capture)
            unresolved_formula_count = insert_unresolved_formulas(connection, object_path, asset, capture)
            semantic_count = import_category_asset(connection, group_id, object_path, variables, capture)
            parser_needed_count = 0
            if group_id == "loot_or_supply_crate":
                parser_needed_count = insert_loot_property_parser_needed(connection, object_path, capture, config)
            summary["assets_imported"] += 1
            summary["variables_imported"] += len(variables)
            summary["references_imported"] += ref_count
            summary["unresolved_imported"] += unresolved_count + unresolved_formula_count + parser_needed_count
            summary["formula_candidates_imported"] += formula_count
            summary["unresolved_formulas_imported"] += unresolved_formula_count
            summary["semantic_rows_imported"] += semantic_count
        summary["generated"] = now_iso()
        upsert_metadata(connection, summary)
        connection.commit()
    finally:
        connection.close()
    return summary


def import_captures_to_business_databases(
    db_dir: Path = DEFAULT_DB_DIR,
    capture_root: Path = DEFAULT_CAPTURE_ROOT,
    report_dir: Path | None = DEFAULT_REPORT_DIR,
    *,
    clear_existing: bool = True,
) -> dict[str, Any]:
    started = now_iso()
    category_summaries: dict[str, Any] = {}
    totals = Counter()
    for group_id, config in CATEGORY_DATABASES.items():
        db_path = db_dir / str(config["filename"])
        summary = import_business_database(
            db_path,
            group_id,
            config,
            capture_root,
            clear_existing=clear_existing,
        )
        category_summaries[group_id] = summary
        for key in (
            "assets_seen",
            "assets_imported",
            "variables_imported",
            "references_imported",
            "unresolved_imported",
            "formula_candidates_imported",
            "unresolved_formulas_imported",
            "semantic_rows_imported",
            "skipped_no_capture",
            "skipped_no_payload",
        ):
            totals[key] += int(summary.get(key) or 0)

    payload = {
        "schema": "ark-devkit-knowledge.capture-import.v1",
        "generated": now_iso(),
        "started": started,
        "db_dir": str(db_dir),
        "capture_root": str(capture_root),
        "totals": dict(totals),
        "categories": category_summaries,
    }
    if report_dir is not None:
        write_json(report_dir / "capture_import_report.json", payload)
        write_text(report_dir / "capture_import_report.md", render_import_report(payload))
    return payload


def render_import_report(payload: dict[str, Any]) -> str:
    totals = payload.get("totals") or {}
    lines = [
        "# Capture Import Report",
        "",
        f"- Generated: `{payload.get('generated', '')}`",
        f"- Capture root: `{payload.get('capture_root', '')}`",
        f"- Databases: `{payload.get('db_dir', '')}`",
        f"- Imported assets: {totals.get('assets_imported', 0)}",
        f"- Imported variables: {totals.get('variables_imported', 0)}",
        f"- Imported references: {totals.get('references_imported', 0)}",
        f"- Imported unresolved work: {totals.get('unresolved_imported', 0)}",
        f"- Imported formula candidates: {totals.get('formula_candidates_imported', 0)}",
        f"- Imported unresolved formulas: {totals.get('unresolved_formulas_imported', 0)}",
        f"- Semantic rows: {totals.get('semantic_rows_imported', 0)}",
        "",
        "| Category | Seen | Imported | Variables | References | Unresolved | Formulas | Unresolved formulas | Semantic rows | Skipped |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group_id, summary in (payload.get("categories") or {}).items():
        skipped = int(summary.get("skipped_no_capture") or 0) + int(summary.get("skipped_no_payload") or 0)
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                group_id,
                summary.get("assets_seen", 0),
                summary.get("assets_imported", 0),
                summary.get("variables_imported", 0),
                summary.get("references_imported", 0),
                summary.get("unresolved_imported", 0),
                summary.get("formula_candidates_imported", 0),
                summary.get("unresolved_formulas_imported", 0),
                summary.get("semantic_rows_imported", 0),
                skipped,
            )
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import captured Blueprint data into ARK knowledge business databases.")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--keep-existing", action="store_true", help="Append rows instead of clearing previous imported rows.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = import_captures_to_business_databases(
        args.db_dir,
        args.capture_root,
        args.report_dir,
        clear_existing=not args.keep_existing,
    )
    totals = payload.get("totals") or {}
    print(
        "imported assets={assets} variables={variables} references={references} unresolved={unresolved}".format(
            assets=totals.get("assets_imported", 0),
            variables=totals.get("variables_imported", 0),
            references=totals.get("references_imported", 0),
            unresolved=totals.get("unresolved_imported", 0),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
