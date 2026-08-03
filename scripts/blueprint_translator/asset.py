"""Asset-directory workflows spanning multiple Blueprint graph pages."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import Iterable

from .artifact_modes import normalize_artifact_mode
from .behavior_report import render_behavior_summary
from .context import context_from_args, function_note_for_name, parse_components_context, parse_defaults_context, parse_notes_context
from .context_pack import (
    build_asset_memory_card,
    build_default_context_pack,
    render_asset_memory_card,
    render_context_pack,
)
from .context_review import build_context_review, render_context_review
from .core import parse_blueprint_text
from .diagnostics import (
    asset_likely_needs_component_context,
    build_diagnostic_findings,
    diagnostic_counts,
    diagnostic_finding,
    render_diagnostics_report,
)
from .formulas import build_formula_candidates, render_formula_candidates
from .output import resolve_output_paths, write_glossary
from .evidence_publication import evidence_publication_lock
from .evidence_repository import resolve_asset_evidence_state
from .quality import (
    behavior_area,
    build_components_suggestions,
    build_defaults_suggestions,
    classify_function_call,
    component_class_hint,
    collect_asset_quality,
    infer_asset_graph_type,
    render_capture_quality_report,
    render_next_actions,
)
from .renderers import format_component_refs, format_default_refs
from .uasset_graphs import (
    compare_uasset_with_clipboard,
    current_uasset_graph_payload_files,
    object_path_to_uasset_path,
    read_uasset_graph_content,
    write_uasset_clipboard_compare_files,
    write_uasset_graph_read_files,
)
from .utils import profile_keywords, safe_filename, split_csvish, table_row

ASSET_OUTPUT_FILE_KEYS = (
    "asset_report",
    "report",
    "asset_json",
    "diagnostics_report",
    "diagnostics_json",
    "call_graph",
    "call_graph_summary",
    "capture_quality_report",
    "capture_quality_json",
    "behavior_summary",
    "context_review",
    "context_review_json",
    "formula_candidates",
    "formula_candidates_json",
    "asset_memory_card",
    "asset_memory_card_json",
    "context_pack",
    "context_pack_json",
    "notes_todo",
    "defaults_suggestions",
    "components_suggestions",
    "next_actions",
)

UASSET_LEGACY_FILE_NAMES = (
    "uasset_package.json",
    "uasset_exports.json",
    "uasset_structure.json",
    "uasset_structure_report.md",
    "uasset_class_defaults.json",
    "uasset_class_defaults_report.md",
    "uasset_properties.json",
    "uasset_unknown_properties.json",
    "uasset_property_parse_report.md",
    "uasset_pin_links.json",
    "uasset_link_resolution_report.md",
    "uasset_partial_graph_triage.json",
    "uasset_partial_graph_triage.md",
    "uasset_quality_gates.json",
    "uasset_quality_gates.md",
    "uasset_graph_nodes.json",
    "uasset_graph_read_report.md",
    "uasset_failed_graph_queue.txt",
    "uasset_failed_graph_queue.json",
    "graphs_from_uasset_manifest.json",
    "uasset_compare_matrix.json",
    "uasset_vs_clipboard_compare.md",
)

def load_manifest(asset_dir: Path) -> dict[str, object]:
    manifest_path = asset_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8-sig"))


def first_existing_path(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists() and path.is_file():
            return path
    return None


def read_sidecar_text(path: Path | None) -> str:
    if not path:
        return ""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def defaults_sidecar_has_values(path: Path | None) -> bool:
    if not path or not path.is_file():
        return False
    parsed = parse_defaults_context({"defaults_text": read_sidecar_text(path)})
    variables = parsed.get("variables", {})
    class_defaults = parsed.get("class_defaults", {})
    return bool(isinstance(variables, dict) and variables) or bool(isinstance(class_defaults, dict) and class_defaults)


def looks_like_component_name(name: str) -> bool:
    lowered = name.lower()
    exact_names = {"charactermovement", "mycharacterstatuscomponent", "myinventorycomponent", "mesh", "rootcomponent", "capsulecomponent"}
    terms = ("component", "camera", "mesh", "movement", "inventory", "status", "niagara", "particle", "audio")
    return lowered in exact_names or lowered.endswith("component") or any(term in lowered for term in terms)


def synthesize_components_text_from_defaults(defaults_text: str) -> str:
    if not defaults_text.strip():
        return ""
    try:
        data = json.loads(defaults_text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    variables = data.get("variables", {})
    if not isinstance(variables, dict):
        return ""
    components = []
    for name, entry in variables.items():
        component_name = str(name)
        if not looks_like_component_name(component_name):
            continue
        metadata = entry if isinstance(entry, dict) else {}
        components.append(
            {
                "name": component_name,
                "class": component_class_hint(component_name),
                "defaults": {"object_ref": metadata.get("value", entry)},
                "purpose": "Synthesized from uasset class defaults; verify exact component class in ARK DevKit.",
            }
        )
    if not components:
        return ""
    return json.dumps(
        {
            "source": "uasset_class_defaults component-like ObjectProperty names",
            "components": components,
        },
        ensure_ascii=False,
        indent=2,
    )


def asset_context_from_args(args: argparse.Namespace, asset_dir: Path, manifest: dict[str, object]) -> dict[str, object]:
    context = context_from_args(args)
    context["parent_class"] = args.parent_class or str(manifest.get("parent_class", ""))
    context["interfaces"] = split_csvish(args.interfaces) or [str(item) for item in manifest.get("interfaces", [])]
    context["tags"] = split_csvish(args.tags) or [str(item) for item in manifest.get("tags", [])]

    defaults_path = Path(os.path.expandvars(args.defaults_file)).expanduser() if args.defaults_file else first_existing_path(
        asset_dir / name for name in ("defaults.json", "defaults.md", "defaults.txt", "class_defaults.json", "class_defaults.md", "class_defaults.txt")
    )
    uasset_defaults_path = asset_dir / "uasset_class_defaults.json"
    if not args.defaults_file and uasset_defaults_path.is_file() and (not defaults_path or not defaults_sidecar_has_values(defaults_path)):
        defaults_path = uasset_defaults_path
    components_path = Path(os.path.expandvars(args.components_file)).expanduser() if args.components_file else first_existing_path(
        asset_dir / name for name in ("components.json", "components.md", "components.txt")
    )
    notes_path = Path(os.path.expandvars(args.notes_file)).expanduser() if args.notes_file else first_existing_path(
        asset_dir / name for name in ("notes.md", "notes.txt")
    )

    defaults_text = read_sidecar_text(defaults_path)
    components_text = read_sidecar_text(components_path)
    components_source = str(components_path) if components_path else ""
    if not components_text and defaults_path == uasset_defaults_path:
        components_text = synthesize_components_text_from_defaults(defaults_text)
        if components_text:
            components_source = f"{uasset_defaults_path} (component-like defaults synthesized)"
    context["defaults_text"] = defaults_text
    context["components_text"] = components_text
    context["notes_text"] = read_sidecar_text(notes_path)
    context["defaults_source"] = str(defaults_path) if defaults_path else ""
    context["components_source"] = components_source
    context["notes_source"] = str(notes_path) if notes_path else ""
    return context


def graph_record_from_manifest_item(asset_dir: Path, item: object) -> dict[str, str]:
    if isinstance(item, str):
        path = Path(item)
        source = path if path.is_absolute() else asset_dir / path
        return {"graph_name": source.stem, "graph_type": "Unknown", "path": str(source)}
    if isinstance(item, dict):
        path_text = str(item.get("path") or item.get("file") or "")
        source = Path(path_text)
        if not source.is_absolute():
            source = asset_dir / source
        return {
            "graph_name": str(item.get("name") or item.get("graph_name") or source.stem),
            "graph_type": str(item.get("type") or item.get("graph_type") or "Unknown"),
            "path": str(source),
        }
    return {"graph_name": "", "graph_type": "Unknown", "path": ""}


def graph_record_key(value: str) -> str:
    lowered = value.lower().strip()
    for prefix in ("function_", "func_", "macro_", "event_", "graph_"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
    return re.sub(r"[^a-z0-9_]+", "", lowered)


def uasset_graph_record_from_path(path: Path) -> dict[str, str]:
    return {
        "graph_name": path.stem.rsplit("_", 1)[0],
        "graph_type": "Unknown",
        "path": str(path),
        "source_kind": "uasset_binary",
    }


def discover_asset_graphs(asset_dir: Path, manifest: dict[str, object]) -> list[dict[str, str]]:
    graph_items = manifest.get("graphs", [])
    if isinstance(graph_items, dict):
        graph_items = [{"name": name, **value} if isinstance(value, dict) else {"name": name, "path": value} for name, value in graph_items.items()]
    records = [graph_record_from_manifest_item(asset_dir, item) for item in graph_items] if isinstance(graph_items, list) else []
    records = [record for record in records if record.get("path")]

    graphs_dir = asset_dir / "graphs"
    candidates: list[Path] = []
    if not records:
        candidates = sorted(graphs_dir.glob("*.txt")) if graphs_dir.exists() else []
        records = [{"graph_name": path.stem, "graph_type": "Unknown", "path": str(path)} for path in candidates]
    if not records:
        skip_names = {
            "defaults.txt",
            "components.txt",
            "notes.txt",
            "readme.txt",
            "graph_candidates_uasset.txt",
            "graph_queue.txt",
            "uasset_failed_graph_queue.txt",
        }
        candidates = [path for path in sorted(asset_dir.glob("*.txt")) if path.name.lower() not in skip_names]
        records = [{"graph_name": path.stem, "graph_type": "Unknown", "path": str(path)} for path in candidates]

    seen = {graph_record_key(str(record.get("graph_name") or Path(record.get("path", "")).stem)) for record in records}
    uasset_candidates = current_uasset_graph_payload_files(asset_dir)
    for path in uasset_candidates:
        record = uasset_graph_record_from_path(path)
        key = graph_record_key(record["graph_name"])
        if key and key not in seen:
            records.append(record)
            seen.add(key)
    return records


def normalize_graph_lookup(value: str) -> str:
    return graph_record_key(value)


def build_asset_call_graph(asset_payload: dict[str, object]) -> dict[str, object]:
    graphs = list(asset_payload.get("graphs", []))
    notes_context = asset_payload.get("notes", {})
    graph_by_key: dict[str, dict[str, object]] = {}
    for graph in graphs:
        graph_by_key[normalize_graph_lookup(str(graph.get("graph_name", "")))] = graph

    calls: list[dict[str, object]] = []
    missing_targets: list[dict[str, object]] = []
    native_or_inherited_calls: list[dict[str, object]] = []
    delegate_bindings: list[dict[str, object]] = []
    macro_usages: list[dict[str, object]] = []
    missing_macro_links: list[dict[str, object]] = []
    for graph in graphs:
        payload = graph.get("payload", {})
        if not isinstance(payload, dict):
            continue
        nodes_by_name = {str(node.get("name", "")): node for node in payload.get("nodes", []) if isinstance(node, dict)}
        for node in payload.get("function_calls", []):
            node_type = str(node.get("node_type", ""))
            if "FunctionEntry" in node_type or "FunctionResult" in node_type:
                continue
            function_name = str(node.get("function") or node.get("label") or "")
            target = graph_by_key.get(normalize_graph_lookup(function_name))
            same_graph = bool(target and str(target.get("graph_name", "")) == str(graph.get("graph_name", "")))
            if same_graph:
                target = None
                call_kind = "self_graph"
            elif target:
                call_kind = "local_blueprint_graph"
            else:
                call_kind = classify_function_call(function_name)
                note = function_note_for_name(notes_context if isinstance(notes_context, dict) else {}, function_name)
                if note and note.get("kind") in {"noted_native_or_inherited", "noted_ignored"}:
                    call_kind = str(note.get("kind"))
            item = {
                "source_graph": graph.get("graph_name", ""),
                "source_node": node.get("label") or node.get("name") or "",
                "function": function_name,
                "target_graph": target.get("graph_name") if target else "",
                "call_kind": call_kind,
            }
            note = function_note_for_name(notes_context if isinstance(notes_context, dict) else {}, function_name)
            if note:
                item["note"] = note
            calls.append(item)
            if not target and call_kind == "blueprint_graph_candidate":
                missing_targets.append(item)
            elif not target and call_kind != "self_graph":
                native_or_inherited_calls.append(item)
        for node in payload.get("delegates", []):
            if not isinstance(node, dict) or "AddDelegate" not in str(node.get("node_type", "")):
                continue
            handlers: list[str] = []
            for pin in node.get("pins", []):
                if not isinstance(pin, dict) or str(pin.get("name", "")).lower() not in {"delegate", "event"}:
                    continue
                for link in pin.get("links", []):
                    if not isinstance(link, dict):
                        continue
                    target_node = nodes_by_name.get(str(link.get("target_node", "")))
                    if target_node:
                        handlers.append(str(target_node.get("event") or target_node.get("delegate") or target_node.get("label") or target_node.get("name") or ""))
                    elif link.get("target_node"):
                        handlers.append(str(link.get("target_node")))
            for handler in dict.fromkeys(value for value in handlers if value):
                target = graph_by_key.get(normalize_graph_lookup(handler))
                delegate_bindings.append(
                    {
                        "source_graph": graph.get("graph_name", ""),
                        "source_node": node.get("label") or node.get("name") or "",
                        "delegate": node.get("delegate") or node.get("label") or "",
                        "handler": handler,
                        "handler_graph": target.get("graph_name") if target else graph.get("graph_name", ""),
                    }
                )
        for node in payload.get("macros", []):
            if not isinstance(node, dict) or "MacroInstance" not in str(node.get("node_type", "")):
                continue
            macro_name = str(node.get("macro") or node.get("label") or "")
            target = graph_by_key.get(normalize_graph_lookup(macro_name))
            if target and str(target.get("graph_name", "")) == str(graph.get("graph_name", "")):
                target = None
            macro_usages.append(
                {
                    "source_graph": graph.get("graph_name", ""),
                    "source_node": node.get("label") or node.get("name") or "",
                    "macro": macro_name,
                    "macro_graph": target.get("graph_name") if target else "",
                }
            )
        for missing in payload.get("diagnostics", {}).get("missing_link_map", []):
            if not isinstance(missing, dict) or missing.get("target_kind") != "macro":
                continue
            for ref in missing.get("references", []) or [{}]:
                if not isinstance(ref, dict):
                    continue
                missing_macro_links.append(
                    {
                        "source_graph": graph.get("graph_name", ""),
                        "missing_macro_node": missing.get("target_node", ""),
                        "referenced_from": f"{ref.get('source_label', '')}.{ref.get('source_pin', '')}".strip("."),
                        "impact": ref.get("impact") or "; ".join(str(value) for value in missing.get("impact", [])[:2]),
                    }
                )
    return {
        "calls": calls,
        "missing_targets": missing_targets,
        "native_or_inherited_calls": native_or_inherited_calls,
        "delegate_bindings": delegate_bindings,
        "macro_usages": macro_usages,
        "missing_macro_links": missing_macro_links,
    }


def worst_confidence(levels: Iterable[str]) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    values = [level for level in levels if level]
    if not values:
        return "low"
    return min(values, key=lambda level: rank.get(level, 0))


def build_asset_payload(args: argparse.Namespace, asset_dir: Path, manifest: dict[str, object], graph_records: list[dict[str, str]], context: dict[str, object], keywords: list[str]) -> dict[str, object]:
    defaults_context = parse_defaults_context(context)
    components_context = parse_components_context(context)
    notes_context = parse_notes_context(context)
    graphs: list[dict[str, object]] = []
    for record in graph_records:
        path = Path(record["path"])
        graph_name = record.get("graph_name") or path.stem
        graph_type = record.get("graph_type") or "Unknown"
        if path.suffix.lower() == ".json" or str(record.get("source_kind") or "") == "uasset_binary":
            graph_payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(graph_payload, dict):
                graph_payload = {}
            metadata = graph_payload.get("metadata", {}) if isinstance(graph_payload.get("metadata", {}), dict) else {}
            graph_name = str(metadata.get("graph_name") or graph_name)
            graph_type = str(metadata.get("graph_type") or graph_type)
            inferred_graph_type = infer_asset_graph_type(graph_name, graph_payload)
            if graph_type in {"", "Unknown"}:
                graph_type = inferred_graph_type
            graph_payload["context"] = context
            graph_payload["class_defaults"] = defaults_context
            graph_payload["component_defaults"] = components_context
            graph_payload["notes"] = notes_context
            graphs.append(
                {
                    "graph_name": graph_name,
                    "graph_type": graph_type,
                    "inferred_graph_type": inferred_graph_type,
                    "source": str(path),
                    "source_kind": "uasset_binary",
                    "cleaned_characters": int(metadata.get("cleaned_characters") or 0),
                    "node_count": int(metadata.get("node_count") or len(graph_payload.get("nodes", []))),
                    "payload": graph_payload,
                }
            )
            continue
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        is_empty_manual_text = raw.strip() in {"", graph_name} and "Begin Object" not in raw
        graph_context = dict(context)
        graph_context["graph_type"] = graph_type
        cleaned, nodes, payload = parse_blueprint_text(
            text="" if is_empty_manual_text else raw,
            source=str(path),
            asset_name=args.asset_name or str(manifest.get("asset_name") or asset_dir.name),
            graph_name=graph_name,
            keywords=keywords,
            keep_guids=args.keep_guids,
            include_raw=args.include_raw,
            context=graph_context,
        )
        inferred_graph_type = infer_asset_graph_type(graph_name, payload)
        if graph_type in {"", "Unknown"}:
            graph_type = inferred_graph_type
        is_empty_manual_graph = is_empty_manual_text and not nodes
        if is_empty_manual_graph:
            metadata = payload.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["empty_graph"] = True
                metadata["empty_reason"] = "manual_capture_confirmed_empty"
        graphs.append(
            {
                "graph_name": graph_name,
                "graph_type": graph_type,
                "inferred_graph_type": inferred_graph_type,
                "source": str(path),
                "cleaned_characters": len(cleaned),
                "node_count": len(nodes),
                "empty_graph": is_empty_manual_graph,
                "payload": payload,
            }
        )

    asset_payload: dict[str, object] = {
        "metadata": {
            "generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "asset_dir": str(asset_dir),
            "asset_name": args.asset_name or str(manifest.get("asset_name") or asset_dir.name),
            "graph_count": len(graphs),
            "node_count": sum(int(graph.get("node_count") or 0) for graph in graphs),
            "defaults_present": bool(str(context.get("defaults_text", "")).strip()),
            "components_present": bool(str(context.get("components_text", "")).strip()),
            "default_variable_count": len(defaults_context.get("variables", {})) if isinstance(defaults_context.get("variables", {}), dict) else 0,
            "component_count": len(components_context.get("components", [])) if isinstance(components_context.get("components", []), list) else 0,
            "note_function_count": len(notes_context.get("functions", {})) if isinstance(notes_context.get("functions", {}), dict) else 0,
        },
        "context": context,
        "class_defaults": defaults_context,
        "component_defaults": components_context,
        "notes": notes_context,
        "graphs": graphs,
    }
    asset_payload["uasset_binary"] = load_uasset_binary_quality(asset_dir)
    asset_payload["call_graph"] = build_asset_call_graph(asset_payload)
    asset_payload["diagnostics"] = build_asset_diagnostics(asset_payload)
    return asset_payload


def load_json_sidecar(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def load_uasset_binary_quality(asset_dir: Path) -> dict[str, object]:
    graph_nodes = load_json_sidecar(asset_dir / "uasset_graph_nodes.json")
    pin_links = load_json_sidecar(asset_dir / "uasset_pin_links.json")
    compare = load_json_sidecar(asset_dir / "uasset_compare_matrix.json")
    failed_queue = load_json_sidecar(asset_dir / "uasset_failed_graph_queue.json")
    unknown_properties = load_json_sidecar(asset_dir / "uasset_unknown_properties.json")
    partial_triage = load_json_sidecar(asset_dir / "uasset_partial_graph_triage.json")
    quality_gates = load_json_sidecar(asset_dir / "uasset_quality_gates.json")
    class_defaults = load_json_sidecar(asset_dir / "uasset_class_defaults.json")
    if not graph_nodes:
        return {"present": False}
    return {
        "present": True,
        "asset_path": str(graph_nodes.get("asset_path") or ""),
        "uasset_path": str(graph_nodes.get("uasset_path") or ""),
        "asset_name": str(graph_nodes.get("asset_name") or class_defaults.get("asset_name") or ""),
        "graph_count": int(graph_nodes.get("graph_count") or 0),
        "node_count": int(graph_nodes.get("node_count") or 0),
        "pin_count": int(graph_nodes.get("pin_count") or 0),
        "link_count": int(graph_nodes.get("link_count") or 0),
        "status_counts": graph_nodes.get("status_counts", {}),
        "confidence_counts": graph_nodes.get("confidence_counts", {}),
        "failure_category_counts": graph_nodes.get("failure_category_counts", {}),
        "failed_graphs": failed_queue.get("graphs", []) if isinstance(failed_queue.get("graphs", []), list) else [],
        "unknown_property_count": len(unknown_properties.get("unknown_properties", [])) if isinstance(unknown_properties.get("unknown_properties", []), list) else 0,
        "pin_link_summary": pin_links.get("summary", {}) if isinstance(pin_links.get("summary", {}), dict) else {},
        "partial_triage": partial_triage,
        "quality_gates": quality_gates,
        "class_default_count": int(class_defaults.get("variable_count") or 0),
        "class_defaults": class_defaults,
        "compare": compare,
    }


def build_asset_diagnostics(asset_payload: dict[str, object]) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    metadata = asset_payload.get("metadata", {})
    graphs = list(asset_payload.get("graphs", []))
    context = asset_payload.get("context", {})
    def complete_empty_uasset_graph(graph: dict[str, object]) -> bool:
        payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
        return (
            str(graph.get("source_kind") or metadata.get("source_kind") or "") == "uasset_binary"
            and str(metadata.get("uasset_read_status") or "") in {"complete", "complete_empty"}
            and int(metadata.get("node_count") or 0) == 0
        )

    if not graphs:
        findings.append(
            diagnostic_finding(
                "ASSET000",
                "error",
                "No graph files were discovered",
                "The asset directory did not contain manifest graph entries or .txt files under graphs/.",
                [str(metadata.get("asset_dir", ""))],
                "Create graphs/*.txt files or add manifest.json with graph entries.",
            )
        )
    elif len(graphs) == 1 and not complete_empty_uasset_graph(graphs[0]):
        findings.append(
            diagnostic_finding(
                "ASSET010",
                "info",
                "Only one graph was supplied",
                "This analysis still behaves like a single-page graph review until more event/function/macro graph pages are added.",
                [str(graphs[0].get("graph_name", ""))],
                "Add other EventGraph/function/macro/construction graph captures to the asset directory.",
            )
        )

    uasset_quality = asset_payload.get("uasset_binary", {})
    if isinstance(uasset_quality, dict) and uasset_quality.get("present"):
        manual_graph_keys = {
            normalize_graph_lookup(str(graph.get("graph_name") or ""))
            for graph in graphs
            if isinstance(graph, dict) and str(graph.get("source_kind") or "") != "uasset_binary"
        }
        failed_graphs = [
            item
            for item in uasset_quality.get("failed_graphs", [])
            if isinstance(item, dict) and normalize_graph_lookup(str(item.get("graph") or "")) not in manual_graph_keys
        ]
        failure_counts = uasset_quality.get("failure_category_counts", {})
        pin_summary = uasset_quality.get("pin_link_summary", {})
        resolution_counts = pin_summary.get("resolution_counts", {}) if isinstance(pin_summary, dict) else {}
        if failed_graphs:
            evidence = [
                f"{item.get('graph')} [{item.get('status')}/{item.get('confidence')}] {', '.join(str(value) for value in item.get('failure_categories', []))}"
                for item in failed_graphs[:60]
            ]
            findings.append(
                diagnostic_finding(
                    "UASSET010",
                    "warning",
                    "Some binary-read graphs still need targeted rules or manual supplement",
                    "The .uasset reader recovered useful graph content, but these pages did not reach complete status.",
                    evidence,
                    "Use uasset_failed_graph_queue.json to decide whether to add pin-layout rules, node readers, cross-graph resolving, or manual clipboard captures.",
                )
            )
        if isinstance(resolution_counts, dict) and int(resolution_counts.get("node_resolved_pin_unknown") or 0) > 0:
            findings.append(
                diagnostic_finding(
                    "UASSET020",
                    "info",
                    "Some binary links resolve only to target nodes",
                    "LinkedTo package-index scanning identified destination nodes, but target PinId fields still need a stronger structural decoder.",
                    [f"node_resolved_pin_unknown={resolution_counts.get('node_resolved_pin_unknown')}"],
                    "Prioritize FEdGraphPinReference / LinkedTo array decoding before treating pin-level diffs as exact.",
                )
            )
        if isinstance(resolution_counts, dict) and int(resolution_counts.get("resolved_pin_heuristic") or 0) > 0:
            findings.append(
                diagnostic_finding(
                    "UASSET021",
                    "info",
                    "Some target pins were inferred by direction/category",
                    "The binary reader resolved target nodes and selected likely target pins, but these links should be treated as pin-level heuristic until LinkedTo PinId bytes are decoded exactly.",
                    [f"resolved_pin_heuristic={resolution_counts.get('resolved_pin_heuristic')}"],
                    "Use uasset_link_resolution_report.md and clipboard compare fixtures to validate exact pin-level behavior.",
                )
            )
        if isinstance(failure_counts, dict) and failure_counts.get("need_node_reader"):
            findings.append(
                diagnostic_finding(
                    "UASSET030",
                    "info",
                    "Additional node semantic readers would improve explanations",
                    "The binary reader can load the nodes structurally, but some classes are still interpreted by the generic fallback.",
                    [f"need_node_reader={failure_counts.get('need_node_reader')}"],
                    "Add dedicated semantic readers for the most frequent unknown classes in uasset_graph_read_report.md.",
                )
            )

    if not str(context.get("defaults_text", "")).strip():
        findings.append(
            diagnostic_finding(
                "ASSET020",
                "warning",
                "Class defaults sidecar is missing",
                "Blueprint variable reads cannot be reconciled with Class Defaults, so default-driven behavior remains uncertain.",
                [],
                "Add defaults.json, defaults.md, or defaults.txt to the asset directory.",
            )
        )
    needs_components = asset_likely_needs_component_context(str(metadata.get("asset_name") or ""))
    components_text_present = bool(str(context.get("components_text", "")).strip())
    if not components_text_present:
        if needs_components:
            findings.append(
                diagnostic_finding(
                    "ASSET021",
                    "warning",
                    "Components sidecar is missing",
                    "ARK behavior often depends on component defaults, but no component context was supplied.",
                    [],
                    "Add components.json, components.md, or components.txt to the asset directory.",
                )
            )
    elif needs_components:
        component_context = asset_payload.get("component_defaults", {})
        component_parse_error = str(component_context.get("parse_error", "")).strip() if isinstance(component_context, dict) else ""
        components = component_context.get("components", []) if isinstance(component_context, dict) else []
        if component_parse_error:
            findings.append(
                diagnostic_finding(
                    "ASSET022",
                    "warning",
                    "Components sidecar could not be fully parsed",
                    "The asset has a component sidecar, but it was not valid JSON and only simple text patterns could be recovered.",
                    [component_parse_error],
                    "Prefer components.json with a components list or object.",
                )
            )
        if not components:
            findings.append(
                diagnostic_finding(
                    "ASSET023",
                    "warning",
                    "Components sidecar has no recognized components",
                    "The component file is present, but no component names/classes/defaults were parsed.",
                    [],
                    "Use entries such as {\"components\":[{\"name\":\"Inventory\",\"class\":\"PrimalInventoryComponent\",\"defaults\":{}}]}.",
                )
            )

    missing_targets = list(asset_payload.get("call_graph", {}).get("missing_targets", []))
    if missing_targets:
        evidence = [f"{item.get('source_graph')} -> {item.get('function')}" for item in missing_targets[:50]]
        findings.append(
            diagnostic_finding(
                "ASSET030",
                "info",
                "Some likely Blueprint function calls do not have matching graph captures",
                "The call graph found calls whose names look like user Blueprint graph pages, but no matching graph capture exists.",
                evidence,
                "Add matching function graph captures where available, or document that these functions are inherited/native in notes.",
            )
        )
    native_calls = list(asset_payload.get("call_graph", {}).get("native_or_inherited_calls", []))
    if native_calls:
        evidence = [f"{item.get('source_graph')} -> {item.get('function')} [{item.get('call_kind')}]" for item in native_calls[:50]]
        findings.append(
            diagnostic_finding(
                "ASSET032",
                "info",
                "Native/Kismet/inherited calls were classified and de-noised",
                "These calls do not have local graph captures, but they look like engine, Kismet, component, ARK parent, or RPC functions rather than missing user graph pages.",
                evidence,
                "Review only if one of these functions is actually implemented as a local Blueprint graph in this asset.",
            )
        )

    missing_macro_links = list(asset_payload.get("call_graph", {}).get("missing_macro_links", []))
    if missing_macro_links:
        evidence = [f"{item.get('source_graph')}: {item.get('referenced_from')} -> {item.get('missing_macro_node')}" for item in missing_macro_links[:50]]
        findings.append(
            diagnostic_finding(
                "ASSET031",
                "warning",
                "Some execution paths enter missing macro instances",
                "A copied graph references macro instance nodes that were not included, so expanded macro behavior is unknown.",
                evidence,
                "Copy the missing K2Node_MacroInstance nodes and, when possible, add the referenced macro graph capture.",
            )
        )

    graph_levels: list[str] = []
    for graph in graphs:
        payload = graph.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if bool(graph.get("empty_graph")):
            continue
        graph_diag = payload.get("diagnostics", {})
        graph_levels.append(str(graph_diag.get("confidence_level", "")))
        for item in build_diagnostic_findings(payload):
            copied = dict(item)
            copied["code"] = f"GRAPH:{graph.get('graph_name')}:{copied.get('code')}"
            copied["title"] = f"{graph.get('graph_name')}: {copied.get('title')}"
            findings.append(copied)

    counts = diagnostic_counts(findings)
    return {
        "confidence_level": worst_confidence(graph_levels),
        "counts": counts,
        "findings": findings,
    }


def render_call_graph(asset_payload: dict[str, object]) -> str:
    call_graph = asset_payload.get("call_graph", {})
    lines = ["# Blueprint Asset Call Graph", ""]
    calls = list(call_graph.get("calls", []))
    lines.append("## Function Calls")
    lines.append("")
    if calls:
        lines.append(table_row(["Source Graph", "Function", "Target Graph", "Classification"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in calls:
            lines.append(table_row([item.get("source_graph", ""), item.get("function", ""), item.get("target_graph", "") or "missing/native/inherited", item.get("call_kind", "")]))
    else:
        lines.append("- No function call nodes were parsed.")
    lines.append("")

    delegate_bindings = list(call_graph.get("delegate_bindings", []))
    lines.append("## Delegate Bindings")
    lines.append("")
    if delegate_bindings:
        lines.append(table_row(["Source Graph", "Delegate", "Handler", "Handler Graph"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in delegate_bindings:
            lines.append(table_row([item.get("source_graph", ""), item.get("delegate", ""), item.get("handler", ""), item.get("handler_graph", "") or "same/missing"]))
    else:
        lines.append("- No delegate bindings were parsed.")
    lines.append("")

    macro_usages = list(call_graph.get("macro_usages", []))
    missing_macro_links = list(call_graph.get("missing_macro_links", []))
    lines.append("## Macro Usages")
    lines.append("")
    if macro_usages:
        lines.append(table_row(["Source Graph", "Macro", "Macro Graph"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in macro_usages:
            lines.append(table_row([item.get("source_graph", ""), item.get("macro", ""), item.get("macro_graph", "") or "inline/native/missing"]))
    else:
        lines.append("- No copied macro instance nodes were parsed.")
    if missing_macro_links:
        lines.append("")
        lines.append("### Missing Macro Links")
        lines.append("")
        lines.append(table_row(["Source Graph", "Missing Macro Node", "Referenced From", "Impact"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in missing_macro_links:
            lines.append(table_row([item.get("source_graph", ""), item.get("missing_macro_node", ""), item.get("referenced_from", ""), item.get("impact", "")]))
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def unique_call_rows(items: Iterable[dict[str, object]], fields: tuple[str, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        marker = tuple(str(item.get(field, "")) for field in fields)
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(item)
    return rows


def render_call_graph_summary(asset_payload: dict[str, object]) -> str:
    call_graph = asset_payload.get("call_graph", {})
    if not isinstance(call_graph, dict):
        call_graph = {}
    quality = collect_asset_quality(asset_payload)
    calls = [item for item in call_graph.get("calls", []) if isinstance(item, dict)]
    local_calls = unique_call_rows(
        [item for item in calls if item.get("call_kind") == "local_blueprint_graph"],
        ("source_graph", "function", "target_graph"),
    )
    missing = unique_call_rows(
        [item for item in quality.get("blueprint_missing_candidates", []) if isinstance(item, dict)],
        ("source_graph", "function"),
    )
    delegate_bindings = [item for item in call_graph.get("delegate_bindings", []) if isinstance(item, dict)]
    missing_macro_links = [item for item in call_graph.get("missing_macro_links", []) if isinstance(item, dict)]
    noted_calls = unique_call_rows(
        [item for item in calls if isinstance(item.get("note"), dict)],
        ("source_graph", "function", "call_kind"),
    )

    lines = ["# Blueprint Asset Call Graph Summary", "", "## Classification Counts", ""]
    counts = quality.get("call_classification_counts", {})
    if isinstance(counts, dict) and counts:
        lines.append(table_row(["Classification", "Count"]))
        lines.append(table_row(["---", "---"]))
        for name, count in sorted(counts.items()):
            lines.append(table_row([name, count]))
    else:
        lines.append("- none")

    lines.extend(["", "## Local Blueprint Calls", ""])
    if local_calls:
        lines.append(table_row(["Source Graph", "Function", "Target Graph"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in local_calls[:120]:
            lines.append(table_row([item.get("source_graph"), item.get("function"), item.get("target_graph")]))
    else:
        lines.append("- none")

    lines.extend(["", "## Likely Missing Blueprint Graphs", ""])
    if missing:
        lines.append(table_row(["Source Graph", "Function"]))
        lines.append(table_row(["---", "---"]))
        for item in missing[:80]:
            lines.append(table_row([item.get("source_graph"), item.get("function")]))
    else:
        lines.append("- none")

    lines.extend(["", "## Delegate Bindings", ""])
    if delegate_bindings:
        lines.append(table_row(["Source Graph", "Delegate", "Handler"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in delegate_bindings[:80]:
            lines.append(table_row([item.get("source_graph"), item.get("delegate"), item.get("handler")]))
    else:
        lines.append("- none")

    lines.extend(["", "## Notes Overrides", ""])
    if noted_calls:
        lines.append(table_row(["Source Graph", "Function", "Kind", "Reason"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in noted_calls[:80]:
            note = item.get("note", {}) if isinstance(item.get("note", {}), dict) else {}
            lines.append(table_row([item.get("source_graph"), item.get("function"), item.get("call_kind"), note.get("reason", "")]))
    else:
        lines.append("- none")

    lines.extend(["", "## Missing Macro Links", ""])
    if missing_macro_links:
        lines.append(table_row(["Source Graph", "Macro Node", "Missing Node"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in missing_macro_links[:80]:
            lines.append(table_row([item.get("source_graph"), item.get("referenced_from"), item.get("missing_macro_node")]))
    else:
        lines.append("- none")

    lines.append("")
    return "\n".join(lines)


def render_notes_todo(asset_payload: dict[str, object]) -> str:
    metadata = asset_payload.get("metadata", {}) if isinstance(asset_payload.get("metadata", {}), dict) else {}
    quality = collect_asset_quality(asset_payload)
    missing = [item for item in quality.get("blueprint_missing_candidates", []) if isinstance(item, dict)]
    notes = asset_payload.get("notes", {}) if isinstance(asset_payload.get("notes", {}), dict) else {}
    functions = notes.get("functions", {}) if isinstance(notes.get("functions", {}), dict) else {}
    by_function: dict[str, dict[str, object]] = {}
    for item in missing:
        name = str(item.get("function", "")).strip()
        if not name:
            continue
        row = by_function.setdefault(name, {"function": name, "sources": set(), "areas": set()})
        source = str(item.get("source_graph", "")).strip()
        if source:
            row_sources = row.get("sources")
            if isinstance(row_sources, set):
                row_sources.add(source)
            row_areas = row.get("areas")
            if isinstance(row_areas, set):
                row_areas.add(behavior_area(source))

    sorted_rows = sorted(by_function.values(), key=lambda row: (-len(row.get("sources", set())), str(row.get("function", ""))))
    function_names = [str(row.get("function", "")) for row in sorted_rows]
    inherited_line = "inherited: " + ", ".join(function_names) if function_names else "inherited: "
    ignore_line = "ignore missing graph: " + ", ".join(function_names) if function_names else "ignore missing graph: "

    lines = [
        "# Blueprint Notes Todo",
        "",
        "Use this file as a review queue. After you verify a function in ARK DevKit, copy one of the suggested lines into `notes.md`, then rerun the analyzer.",
        "",
        "## Summary",
        "",
        f"- Asset: {metadata.get('asset_name', '-')}",
        f"- Unique missing graph functions to verify: {len(sorted_rows)}",
        f"- Current note function overrides: {len(functions)}",
        "",
        "## Copy/Paste Templates",
        "",
        "If these functions are implemented by a parent Blueprint or native/ARK code after you verify them:",
        "",
        "```text",
        inherited_line,
        "```",
        "",
        "If you intentionally want to suppress them without assigning parent/native ownership:",
        "",
        "```text",
        ignore_line,
        "```",
        "",
        "## Candidates To Verify",
        "",
    ]
    if sorted_rows:
        lines.append(table_row(["Function", "Source Graphs", "Behavior Areas", "Suggested notes.md entry"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for row in sorted_rows:
            sources = sorted(str(value) for value in row.get("sources", set()))
            areas = sorted(str(value) for value in row.get("areas", set()))
            function = str(row.get("function", ""))
            lines.append(table_row([function, ", ".join(sources), ", ".join(areas), f"inherited: {function}"]))
    else:
        lines.append("- No unresolved graph-like calls need notes review.")

    lines.extend(["", "## Existing Notes Overrides", ""])
    if functions:
        lines.append(table_row(["Function", "Kind", "Reason"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in sorted(functions.values(), key=lambda value: str(value.get("name", "")) if isinstance(value, dict) else ""):
            if not isinstance(item, dict):
                continue
            lines.append(table_row([item.get("name"), item.get("kind"), item.get("reason", "")]))
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def collect_asset_missing_link_rows(asset_payload: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
        diagnostics = payload.get("diagnostics", {}) if isinstance(payload, dict) else {}
        for item in diagnostics.get("missing_link_map", []):
            if not isinstance(item, dict):
                continue
            references = []
            for ref in item.get("references", [])[:6]:
                if isinstance(ref, dict):
                    references.append(f"{ref.get('source_label')}.{ref.get('source_pin')}")
            rows.append(
                {
                    "graph": graph.get("graph_name", ""),
                    "target_node": item.get("target_node", ""),
                    "target_kind": item.get("target_kind", "node"),
                    "link_types": ", ".join(str(value) for value in item.get("link_types", [])),
                    "referenced_from": "; ".join(references),
                    "impact": "; ".join(str(value) for value in item.get("impact", [])[:4]),
                    "copy_hint": item.get("copy_hint", ""),
                }
            )
    return rows


def render_asset_report(asset_payload: dict[str, object]) -> str:
    metadata = asset_payload.get("metadata", {})
    diagnostics = asset_payload.get("diagnostics", {})
    lines = [
        "# Blueprint Asset Report",
        "",
        "## Summary",
        "",
        f"- Asset: {metadata.get('asset_name', '-')}",
        f"- Asset dir: {metadata.get('asset_dir', '-')}",
        f"- Graphs: {metadata.get('graph_count', 0)}",
        f"- Nodes: {metadata.get('node_count', 0)}",
        f"- Defaults sidecar: {'yes' if metadata.get('defaults_present') else 'no'}",
        f"- Components sidecar: {'yes' if metadata.get('components_present') else 'no'}",
        f"- Parsed default variables: {metadata.get('default_variable_count', 0)}",
        f"- Parsed components: {metadata.get('component_count', 0)}",
        f"- Parsed note function overrides: {metadata.get('note_function_count', 0)}",
        f"- Confidence: {diagnostics.get('confidence_level', '-')}",
        "",
        "## Binary Graph Coverage",
        "",
    ]
    uasset_quality = asset_payload.get("uasset_binary", {})
    if isinstance(uasset_quality, dict) and uasset_quality.get("present"):
        lines.extend(
            [
                f"- Binary graphs: {uasset_quality.get('graph_count', 0)}",
                f"- Binary nodes: {uasset_quality.get('node_count', 0)}",
                f"- Binary pins: {uasset_quality.get('pin_count', 0)}",
                f"- Binary links: {uasset_quality.get('link_count', 0)}",
                f"- Binary class defaults: {uasset_quality.get('class_default_count', 0)}",
                f"- Unknown/raw properties: {uasset_quality.get('unknown_property_count', 0)}",
                "",
                "### Read Status",
                "",
                table_row(["Status", "Count"]),
                table_row(["---", "---:"]),
            ]
        )
        status_counts = uasset_quality.get("status_counts", {})
        if isinstance(status_counts, dict) and status_counts:
            for status, count in sorted(status_counts.items()):
                lines.append(table_row([status, count]))
        else:
            lines.append(table_row(["none", 0]))
        lines.extend(["", "### Link Resolution", "", table_row(["Status", "Count"]), table_row(["---", "---:"])])
        pin_summary = uasset_quality.get("pin_link_summary", {})
        resolution_counts = pin_summary.get("resolution_counts", {}) if isinstance(pin_summary, dict) else {}
        if isinstance(resolution_counts, dict) and resolution_counts:
            for status, count in sorted(resolution_counts.items()):
                lines.append(table_row([status, count]))
        else:
            lines.append(table_row(["none", 0]))
        compare = uasset_quality.get("compare", {})
        if isinstance(compare, dict) and compare.get("matched_graph_count"):
            averages = compare.get("averages", {}) if isinstance(compare.get("averages", {}), dict) else {}
            lines.extend(
                [
                    "",
                    "### Clipboard Validation",
                    "",
                    f"- Matched graphs: {compare.get('matched_graph_count', 0)}",
                    f"- Average node match: {averages.get('node_match_ratio', 0)}",
                    f"- Average pin recovery: {averages.get('pin_recovery_ratio', 0)}",
                    f"- Average link recovery: {averages.get('link_recovery_ratio', 0)}",
                ]
            )
        quality_gates = uasset_quality.get("quality_gates", {})
        if isinstance(quality_gates, dict) and quality_gates:
            lines.extend(
                [
                    "",
                    "### Quality Gates",
                    "",
                    f"- Overall: {'PASS' if quality_gates.get('passed') else 'NEEDS WORK'}",
                ]
            )
            for gate in quality_gates.get("gates", [])[:12]:
                if isinstance(gate, dict):
                    lines.append(
                        f"- {gate.get('metric')}: {gate.get('actual')} ({gate.get('operator')} {gate.get('target')}) {'PASS' if gate.get('passed') else 'FAIL'}"
                    )
    else:
        lines.append("- No .uasset graph read files were found for this asset.")
    lines.extend(
        [
            "",
            "## Graphs",
            "",
        ]
    )
    graphs = list(asset_payload.get("graphs", []))
    if graphs:
        lines.append(table_row(["Graph", "Type", "Nodes", "Confidence", "Source"]))
        lines.append(table_row(["---", "---", "---", "---", "---"]))
        for graph in graphs:
            payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
            graph_diag = payload.get("diagnostics", {}) if isinstance(payload, dict) else {}
            lines.append(table_row([graph.get("graph_name", ""), graph.get("graph_type", ""), graph.get("node_count", 0), graph_diag.get("confidence_level", ""), graph.get("source", "")]))
    else:
        lines.append("- none")
    lines.extend(["", "## Call Graph", ""])
    lines.append(render_call_graph(asset_payload).replace("# Blueprint Asset Call Graph\n\n", "").strip())
    missing_rows = collect_asset_missing_link_rows(asset_payload)
    lines.extend(["", "## Missing LinkedTo Targets", ""])
    if missing_rows:
        lines.append(table_row(["Graph", "Missing Target", "Kind", "Type", "Referenced From", "Impact"]))
        lines.append(table_row(["---", "---", "---", "---", "---", "---"]))
        for item in missing_rows[:120]:
            lines.append(table_row([item.get("graph"), item.get("target_node"), item.get("target_kind"), item.get("link_types"), item.get("referenced_from"), item.get("impact")]))
    else:
        lines.append("- none")
    lines.extend(["", "## Diagnostics", ""])
    findings = list(diagnostics.get("findings", []))
    if findings:
        for item in findings[:80]:
            lines.append(f"- [{str(item.get('severity', 'info')).upper()}] {item.get('code')}: {item.get('title')}")
    else:
        lines.append("- No diagnostic findings.")
    lines.append("")
    return "\n".join(lines)


def render_asset_diagnostics_report(asset_payload: dict[str, object]) -> str:
    metadata = asset_payload.get("metadata", {})
    diagnostics = asset_payload.get("diagnostics", {})
    counts = diagnostics.get("counts", {})
    lines = [
        "# Blueprint Asset Diagnostics Report",
        "",
        "## Summary",
        "",
        f"- Asset: {metadata.get('asset_name', '-')}",
        f"- Asset dir: {metadata.get('asset_dir', '-')}",
        f"- Graphs: {metadata.get('graph_count', 0)}",
        f"- Nodes: {metadata.get('node_count', 0)}",
        f"- Parsed default variables: {metadata.get('default_variable_count', 0)}",
        f"- Parsed components: {metadata.get('component_count', 0)}",
        f"- Confidence: {diagnostics.get('confidence_level', '-')}",
        f"- Findings: {counts.get('error', 0)} error, {counts.get('warning', 0)} warning, {counts.get('info', 0)} info",
        "",
        "## Findings",
        "",
    ]
    findings = list(diagnostics.get("findings", []))
    if not findings:
        lines.append("- No diagnostic findings.")
    for item in findings:
        lines.append(f"### [{str(item.get('severity', 'info')).upper()}] {item.get('code')} - {item.get('title')}")
        lines.append("")
        lines.append(str(item.get("detail", "")))
        evidence = list(item.get("evidence", []))
        if evidence:
            lines.append("")
            lines.append("Evidence:")
            lines.extend(f"- {value}" for value in evidence[:50])
        next_action = str(item.get("next_action", "")).strip()
        if next_action:
            lines.append("")
            lines.append(f"Next action: {next_action}")
        lines.append("")
    return "\n".join(lines)


def render_graph_payload_report(graph: dict[str, object]) -> str:
    payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    diagnostics = payload.get("diagnostics", {}) if isinstance(payload, dict) else {}
    lines = [
        f"# Blueprint Graph Report: {graph.get('graph_name', '-')}",
        "",
        "## Summary",
        "",
        f"- Asset: {metadata.get('asset_name') or '-'}",
        f"- Graph: {metadata.get('graph_name') or graph.get('graph_name') or '-'}",
        f"- Type: {graph.get('graph_type') or '-'}",
        f"- Source: {graph.get('source') or metadata.get('source') or '-'}",
        f"- Nodes: {metadata.get('node_count', 0)}",
        f"- Pins: {metadata.get('pin_count', 0)}",
        f"- Links: {metadata.get('link_count', 0)}",
        f"- Confidence: {diagnostics.get('confidence_level', '-')}",
        "",
        "## Entry Points",
        "",
    ]
    roots = payload.get("exec_flow", {}).get("roots", []) if isinstance(payload, dict) else []
    lines.extend(f"- #{root.get('index')} {root.get('label')} ({root.get('node_type')})" for root in roots) if roots else lines.append("- none")
    lines.extend(["", "## Function Calls", ""])
    function_calls = payload.get("function_calls", []) if isinstance(payload, dict) else []
    if function_calls:
        lines.append(table_row(["#", "Function", "Node Type"]))
        lines.append(table_row(["---", "---", "---"]))
        for node in function_calls[:120]:
            lines.append(table_row([node.get("index"), node.get("function") or node.get("label"), node.get("node_type")]))
    else:
        lines.append("- none")
    lines.extend(["", "## Variables", ""])
    variable_nodes = (payload.get("variable_gets", []) if isinstance(payload, dict) else []) + (payload.get("variable_sets", []) if isinstance(payload, dict) else [])
    if variable_nodes:
        lines.append(table_row(["#", "Variable", "Node Type"]))
        lines.append(table_row(["---", "---", "---"]))
        for node in variable_nodes[:120]:
            lines.append(table_row([node.get("index"), node.get("variable") or node.get("label"), node.get("node_type")]))
    else:
        lines.append("- none")
    lines.extend(["", "## Missing LinkedTo Targets", ""])
    missing_map = payload.get("diagnostics", {}).get("missing_link_map", []) if isinstance(payload, dict) else []
    if missing_map:
        lines.append(table_row(["Missing Target", "Kind", "Type", "Referenced From", "Impact"]))
        lines.append(table_row(["---", "---", "---", "---", "---"]))
        for item in missing_map[:120]:
            if not isinstance(item, dict):
                continue
            references = []
            for ref in item.get("references", [])[:6]:
                if isinstance(ref, dict):
                    references.append(f"{ref.get('source_label')}.{ref.get('source_pin')}")
            lines.append(
                table_row(
                    [
                        item.get("target_node", ""),
                        item.get("target_kind", "node"),
                        ", ".join(str(value) for value in item.get("link_types", [])),
                        "; ".join(references),
                        "; ".join(str(value) for value in item.get("impact", [])[:4]),
                    ]
                )
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Branches And Parameters", ""])
    rows = list(payload.get("data_flow", {}).get("branch_conditions", [])) + list(payload.get("data_flow", {}).get("call_parameters", [])) if isinstance(payload, dict) else []
    if rows:
        lines.append(table_row(["Node", "Pin", "Source", "Class Defaults", "Component Defaults"]))
        lines.append(table_row(["---", "---", "---", "---", "---"]))
        for item in rows[:160]:
            lines.append(
                table_row(
                    [
                        item.get("node_label"),
                        item.get("pin"),
                        item.get("source"),
                        format_default_refs(item.get("class_default_refs", [])),
                        format_component_refs(item.get("component_refs", [])),
                    ]
                )
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Diagnostics", ""])
    findings = build_diagnostic_findings(payload) if isinstance(payload, dict) else []
    if findings:
        lines.extend(f"- [{str(item.get('severity', 'info')).upper()}] {item.get('code')}: {item.get('title')}" for item in findings[:80])
    else:
        lines.append("- No diagnostic findings.")
    lines.append("")
    return "\n".join(lines)


def attention_graph_names(asset_payload: dict[str, object]) -> set[str]:
    quality = collect_asset_quality(asset_payload)
    names: set[str] = set()
    for item in quality.get("attention_graphs", []):
        if isinstance(item, dict):
            names.add(str(item.get("graph", "")))
    return names


def write_asset_graph_outputs(
    asset_payload: dict[str, object],
    graph_reports_dir: Path,
    *,
    mode: str = "attention",
    include_json: bool = False,
    include_diagnostics: bool = False,
) -> None:
    graph_reports_dir.mkdir(parents=True, exist_ok=True)
    index_lines = ["# Blueprint Asset Graph Reports", ""]
    selected_names = attention_graph_names(asset_payload) if mode == "attention" else set()
    written = 0
    for index, graph in enumerate(asset_payload.get("graphs", []), start=1):
        if not isinstance(graph, dict):
            continue
        graph_name = str(graph.get("graph_name", ""))
        if mode == "attention" and graph_name not in selected_names:
            continue
        base = f"{index:02d}_{safe_filename(str(graph.get('graph_name', '')), 'graph')}"
        report_path = graph_reports_dir / f"{base}_report.md"
        json_path = graph_reports_dir / f"{base}.json"
        diagnostics_path = graph_reports_dir / f"{base}_diagnostics.md"
        payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
        report_path.write_text(render_graph_payload_report(graph), encoding="utf-8")
        if include_json:
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if include_diagnostics:
            diagnostics_path.write_text(render_diagnostics_report(payload), encoding="utf-8")
        index_lines.append(f"- [{graph.get('graph_name', base)}]({report_path.name})")
        written += 1
    if written == 0:
        index_lines.append("- No graph-level reports were written for this report level.")
    (graph_reports_dir / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


def clean_asset_outputs(paths: dict[str, Path]) -> None:
    for key in ASSET_OUTPUT_FILE_KEYS:
        path = paths.get(key)
        if path and path.exists() and path.is_file():
            path.unlink()
    glossary_path = paths["dir"] / "ark_glossary.json"
    if glossary_path.exists() and glossary_path.is_file():
        glossary_path.unlink()
    graph_reports = paths.get("graph_reports")
    if graph_reports and graph_reports.exists() and graph_reports.is_dir():
        shutil.rmtree(graph_reports)


def prune_legacy_uasset_outputs(asset_dir: Path) -> list[str]:
    """Delete only known generated legacy artifacts after explicit opt-in."""

    with evidence_publication_lock(asset_dir) as root:
        try:
            state = resolve_asset_evidence_state(root, allow_stale=False)
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(
                "refusing to prune without fresh manifest-bound v3 evidence"
            ) from exc
        if (
            state.source_kind != "INDEXED_V3_CURRENT"
            or not state.release_authority
            or state.freshness_status != "FRESH"
        ):
            raise ValueError(
                "refusing to prune without fresh v3 release-authority evidence"
            )

        removed: list[str] = []
        for name in UASSET_LEGACY_FILE_NAMES:
            path = root / name
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            reparse_flag = int(
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
            )
            if (
                path.is_symlink()
                or bool(int(getattr(metadata, "st_file_attributes", 0)) & reparse_flag)
                or not stat.S_ISREG(metadata.st_mode)
            ):
                raise ValueError(f"unsafe legacy artifact path: {path.name}")
            path.unlink()
            removed.append(name)

        graphs_dir = root / "graphs_from_uasset"
        try:
            metadata = graphs_dir.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            reparse_flag = int(
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
            )
            if (
                graphs_dir.is_symlink()
                or bool(int(getattr(metadata, "st_file_attributes", 0)) & reparse_flag)
                or not stat.S_ISDIR(metadata.st_mode)
            ):
                raise ValueError("unsafe legacy graph directory")
            shutil.rmtree(graphs_dir)
            removed.append("graphs_from_uasset/")
        return removed


def run_asset_binary_translate(args: argparse.Namespace) -> int:
    asset_path = str(getattr(args, "asset_binary", "") or "").strip()
    max_graphs = int(getattr(args, "uasset_max_graphs", 0) or 0)
    prune_requested = bool(getattr(args, "prune_legacy", False))
    artifact_mode = normalize_artifact_mode(getattr(args, "artifact_mode", None))
    if prune_requested and artifact_mode == "legacy":
        print("--prune-legacy requires dual or indexed mode.", file=sys.stderr)
        return 2
    if prune_requested and max_graphs > 0:
        print("--prune-legacy cannot be combined with --uasset-max-graphs.", file=sys.stderr)
        return 2
    uasset_path, attempted = object_path_to_uasset_path(asset_path, getattr(args, "content_root", []))
    if uasset_path is None:
        print(f"Could not resolve local .uasset for: {asset_path}", file=sys.stderr)
        for item in attempted:
            print(f"- attempted: {item}", file=sys.stderr)
        return 2
    payload = read_uasset_graph_content(asset_path, uasset_path, max_graphs=max_graphs)
    if prune_requested:
        if not bool(payload.get("loaded")):
            print("Refusing to prune because the source asset did not load completely.", file=sys.stderr)
            return 2
        structure = payload.get("structure")
        if isinstance(structure, dict) and "graph_exports_count" in structure:
            expected_graphs = int(structure.get("graph_exports_count") or 0)
            if int(payload.get("graph_count") or 0) != expected_graphs:
                print("Refusing to prune because the current graph read is incomplete.", file=sys.stderr)
                return 2
        status_counts = payload.get("status_counts")
        if isinstance(status_counts, dict) and any(
            int(count or 0) > 0 and str(status) not in {"complete", "complete_empty"}
            for status, count in status_counts.items()
        ):
            print("Refusing to prune because one or more graphs are not completely recovered.", file=sys.stderr)
            return 2
    capture_root = Path(os.path.expandvars(getattr(args, "capture_root", "") or "captures")).expanduser()
    paths = write_uasset_graph_read_files(
        asset_path,
        capture_root,
        payload,
        artifact_mode=artifact_mode,
    )
    print(f"Wrote uasset graph read directory: {paths['asset_dir']}")
    if paths.get("graph_report"):
        print(f"- graph report: {paths['graph_report']}")
    if paths.get("graph_nodes_json"):
        print(f"- graph nodes: {paths['graph_nodes_json']}")
    if paths.get("pin_links_json"):
        print(f"- pin links: {paths['pin_links_json']}")
    if paths.get("evidence_database"):
        print(f"- evidence database: {paths['evidence_database']}")
        print(f"- agent index: {paths.get('agent_index', '')}")
    if paths.get("compare_report"):
        print(f"- uasset vs clipboard compare: {paths['compare_report']}")
    if paths.get("graphs_dir"):
        print(f"- graph payloads: {paths['graphs_dir']}")
    print(f"Read graphs: {payload.get('graph_count', 0)}")
    print(f"Read nodes: {payload.get('node_count', 0)}")
    print(f"Recovered pins: {payload.get('pin_count', 0)}")
    print(f"Recovered links: {payload.get('link_count', 0)}")
    if prune_requested:
        removed = prune_legacy_uasset_outputs(Path(paths["asset_dir"]))
        print(f"Explicitly pruned legacy artifacts: {len(removed)}")
        return 0
    if bool(getattr(args, "asset_binary_no_report", False)) or artifact_mode == "indexed":
        return 0
    args.asset_dir = paths["asset_dir"]
    if not getattr(args, "output_dir", None):
        args.output_dir = str(Path(paths["asset_dir"]) / "output")
    if not getattr(args, "asset_name", None):
        args.asset_name = str(payload.get("asset_name") or "")
    args.keep_stale_output = True
    return run_asset_translate(args)


def suggestion_has_items(suggestions: dict[str, object], key: str) -> bool:
    value = suggestions.get(key)
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return False


def run_asset_translate(args: argparse.Namespace) -> int:
    asset_dir = Path(os.path.expandvars(args.asset_dir)).expanduser()
    if not asset_dir.exists() or not asset_dir.is_dir():
        print(f"Asset directory not found: {asset_dir}", file=sys.stderr)
        return 2
    manifest = load_manifest(asset_dir)
    graph_records = discover_asset_graphs(asset_dir, manifest)
    if not graph_records:
        indexed_declared = False
        for marker in (
            asset_dir / "evidence" / "current.json",
            asset_dir / "evidence" / "evidence.sqlite",
        ):
            try:
                marker.lstat()
            except FileNotFoundError:
                continue
            indexed_declared = True
            break
        if indexed_declared:
            state = resolve_asset_evidence_state(asset_dir)
            print(f"Indexed evidence is ready: {state.database_path}")
            print(f"Agent index: {state.agent_index_path}")
            print("Use scripts/query_blueprint_evidence.py for bounded analysis; no legacy reports were generated.")
            return 0
        print(f"No graph .txt files found in asset directory: {asset_dir}", file=sys.stderr)
        return 2
    paths = resolve_output_paths(args)
    keywords = profile_keywords(args.profile, args.keyword)
    context = asset_context_from_args(args, asset_dir, manifest)
    asset_payload = build_asset_payload(args, asset_dir, manifest, graph_records, context, keywords)
    if (asset_dir / "graphs").is_dir() and (asset_dir / "graphs_from_uasset").is_dir():
        compare_payload = compare_uasset_with_clipboard(asset_dir, keywords=keywords)
        write_uasset_clipboard_compare_files(asset_dir, compare_payload)
        asset_payload["uasset_binary"] = load_uasset_binary_quality(asset_dir)
        asset_payload["diagnostics"] = build_asset_diagnostics(asset_payload)
    report_level = getattr(args, "report_level", "standard")
    if not getattr(args, "keep_stale_output", False):
        clean_asset_outputs(paths)

    written: list[str] = []

    def write_output(label: str, text: str, *, encoding: str = "utf-8") -> None:
        paths[label].write_text(text, encoding=encoding)
        written.append(label)

    write_output("diagnostics_report", render_asset_diagnostics_report(asset_payload))
    write_output("capture_quality_report", render_capture_quality_report(asset_payload))
    write_output("behavior_summary", render_behavior_summary(asset_payload))
    write_output("context_review", render_context_review(asset_payload))
    formula_payload = build_formula_candidates(asset_payload)
    write_output(
        "formula_candidates_json",
        json.dumps(formula_payload, ensure_ascii=False, indent=2, default=list),
    )
    write_output("formula_candidates", render_formula_candidates(formula_payload))
    memory_card = build_asset_memory_card(asset_payload, formula_payload)
    write_output(
        "asset_memory_card_json",
        json.dumps(memory_card, ensure_ascii=False, indent=2, default=list),
    )
    write_output("asset_memory_card", render_asset_memory_card(memory_card))
    context_pack = build_default_context_pack(asset_payload, formula_payload, memory_card)
    write_output(
        "context_pack_json",
        json.dumps(context_pack, ensure_ascii=False, indent=2, default=list),
    )
    write_output("context_pack", render_context_pack(context_pack))
    if report_level in {"standard", "debug"}:
        write_output("context_review_json", json.dumps(build_context_review(asset_payload), ensure_ascii=False, indent=2, default=list))
    write_output("next_actions", render_next_actions(asset_payload), encoding="utf-8-sig")
    write_output("notes_todo", render_notes_todo(asset_payload))

    asset_report_text = ""
    legacy_output = bool(getattr(args, "output", None))
    if report_level in {"standard", "debug"} or legacy_output:
        asset_report_text = render_asset_report(asset_payload)
    if report_level in {"standard", "debug"}:
        write_output("asset_report", asset_report_text)
        write_output("call_graph_summary", render_call_graph_summary(asset_payload))
        defaults_suggestions = build_defaults_suggestions(asset_payload)
        components_suggestions = build_components_suggestions(asset_payload)
        if report_level == "debug" or suggestion_has_items(defaults_suggestions, "variables"):
            write_output("defaults_suggestions", json.dumps(defaults_suggestions, ensure_ascii=False, indent=2))
        if report_level == "debug" or suggestion_has_items(components_suggestions, "components"):
            write_output("components_suggestions", json.dumps(components_suggestions, ensure_ascii=False, indent=2))
        write_asset_graph_outputs(
            asset_payload,
            paths["graph_reports"],
            mode="all" if report_level == "debug" else "attention",
            include_json=report_level == "debug",
            include_diagnostics=report_level == "debug",
        )
        written.append("graph_reports")
    if legacy_output:
        write_output("report", asset_report_text or render_asset_report(asset_payload))
    if report_level == "debug":
        write_output("asset_json", json.dumps(asset_payload, ensure_ascii=False, indent=2))
        write_output("diagnostics_json", json.dumps({"metadata": asset_payload.get("metadata", {}), "diagnostics": asset_payload.get("diagnostics", {})}, ensure_ascii=False, indent=2))
        write_output("call_graph", render_call_graph(asset_payload))
        write_output("capture_quality_json", json.dumps(collect_asset_quality(asset_payload), ensure_ascii=False, indent=2))
        write_glossary(paths["dir"])
        written.append("ark_glossary")
    print(f"Wrote asset output directory: {paths['dir']}")
    print(f"Report level: {report_level}")
    for label in written:
        if label == "ark_glossary":
            print(f"- {label}: {paths['dir'] / 'ark_glossary.json'}")
        else:
            print(f"- {label}: {paths[label]}")
    print(f"Parsed graphs: {asset_payload['metadata']['graph_count']}")
    print(f"Parsed nodes: {asset_payload['metadata']['node_count']}")
    print(f"Confidence: {asset_payload['diagnostics']['confidence_level']}")
    return 0
