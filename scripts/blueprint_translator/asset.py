"""Asset-directory workflows spanning multiple Blueprint graph pages."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
from pathlib import Path
from typing import Iterable

from .context import context_from_args, function_note_for_name, parse_components_context, parse_defaults_context, parse_notes_context
from .core import parse_blueprint_text
from .diagnostics import build_diagnostic_findings, diagnostic_counts, diagnostic_finding, render_diagnostics_report
from .output import resolve_output_paths, write_glossary
from .quality import (
    behavior_area,
    build_components_suggestions,
    build_defaults_suggestions,
    classify_function_call,
    collect_asset_quality,
    infer_asset_graph_type,
    render_capture_quality_report,
    render_next_actions,
)
from .renderers import format_component_refs, format_default_refs, render_report
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
    "notes_todo",
    "defaults_suggestions",
    "components_suggestions",
    "next_actions",
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


def asset_context_from_args(args: argparse.Namespace, asset_dir: Path, manifest: dict[str, object]) -> dict[str, object]:
    context = context_from_args(args)
    context["parent_class"] = args.parent_class or str(manifest.get("parent_class", ""))
    context["interfaces"] = split_csvish(args.interfaces) or [str(item) for item in manifest.get("interfaces", [])]
    context["tags"] = split_csvish(args.tags) or [str(item) for item in manifest.get("tags", [])]

    defaults_path = Path(os.path.expandvars(args.defaults_file)).expanduser() if args.defaults_file else first_existing_path(
        asset_dir / name for name in ("defaults.json", "defaults.md", "defaults.txt", "class_defaults.json", "class_defaults.md", "class_defaults.txt")
    )
    components_path = Path(os.path.expandvars(args.components_file)).expanduser() if args.components_file else first_existing_path(
        asset_dir / name for name in ("components.json", "components.md", "components.txt")
    )
    notes_path = Path(os.path.expandvars(args.notes_file)).expanduser() if args.notes_file else first_existing_path(
        asset_dir / name for name in ("notes.md", "notes.txt")
    )

    context["defaults_text"] = read_sidecar_text(defaults_path)
    context["components_text"] = read_sidecar_text(components_path)
    context["notes_text"] = read_sidecar_text(notes_path)
    context["defaults_source"] = str(defaults_path) if defaults_path else ""
    context["components_source"] = str(components_path) if components_path else ""
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


def discover_asset_graphs(asset_dir: Path, manifest: dict[str, object]) -> list[dict[str, str]]:
    graph_items = manifest.get("graphs", [])
    if isinstance(graph_items, dict):
        graph_items = [{"name": name, **value} if isinstance(value, dict) else {"name": name, "path": value} for name, value in graph_items.items()]
    records = [graph_record_from_manifest_item(asset_dir, item) for item in graph_items] if isinstance(graph_items, list) else []
    records = [record for record in records if record.get("path")]
    if records:
        return records

    graphs_dir = asset_dir / "graphs"
    candidates = sorted(graphs_dir.glob("*.txt")) if graphs_dir.exists() else []
    if not candidates:
        skip_names = {"defaults.txt", "components.txt", "notes.txt", "readme.txt"}
        candidates = [path for path in sorted(asset_dir.glob("*.txt")) if path.name.lower() not in skip_names]
    return [{"graph_name": path.stem, "graph_type": "Unknown", "path": str(path)} for path in candidates]


def normalize_graph_lookup(value: str) -> str:
    lowered = value.lower().strip()
    for prefix in ("function_", "func_", "macro_", "event_", "graph_"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
    return re.sub(r"[^a-z0-9_]+", "", lowered)


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
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        graph_name = record.get("graph_name") or path.stem
        graph_type = record.get("graph_type") or "Unknown"
        graph_context = dict(context)
        graph_context["graph_type"] = graph_type
        cleaned, nodes, payload = parse_blueprint_text(
            text=raw,
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
        graphs.append(
            {
                "graph_name": graph_name,
                "graph_type": graph_type,
                "inferred_graph_type": inferred_graph_type,
                "source": str(path),
                "cleaned_characters": len(cleaned),
                "node_count": len(nodes),
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
    asset_payload["call_graph"] = build_asset_call_graph(asset_payload)
    asset_payload["diagnostics"] = build_asset_diagnostics(asset_payload)
    return asset_payload


def build_asset_diagnostics(asset_payload: dict[str, object]) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    metadata = asset_payload.get("metadata", {})
    graphs = list(asset_payload.get("graphs", []))
    context = asset_payload.get("context", {})
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
    elif len(graphs) == 1:
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
    if not str(context.get("components_text", "")).strip():
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
    else:
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


def graph_variable_counters(graph: dict[str, object]) -> tuple[dict[str, int], dict[str, int]]:
    payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
    reads: dict[str, int] = {}
    writes: dict[str, int] = {}
    for item in payload.get("variable_gets", []):
        if isinstance(item, dict):
            name = str(item.get("variable") or item.get("label") or "")
            if name:
                reads[name] = reads.get(name, 0) + 1
    for item in payload.get("variable_sets", []):
        if isinstance(item, dict):
            name = str(item.get("variable") or item.get("label") or "")
            if name:
                writes[name] = writes.get(name, 0) + 1
    return reads, writes


def top_counter_items(counter: dict[str, int], limit: int = 10) -> str:
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return ", ".join(f"{name}({count})" for name, count in items) if items else "-"


def behavior_area_rollup(area_items: list[dict[str, object]], calls: list[dict[str, object]], quality: dict[str, object]) -> dict[str, object]:
    graph_names = {str(graph.get("graph_name", "")) for graph in area_items}
    reads: dict[str, int] = {}
    writes: dict[str, int] = {}
    for graph in area_items:
        graph_reads, graph_writes = graph_variable_counters(graph)
        for name, count in graph_reads.items():
            reads[name] = reads.get(name, 0) + count
        for name, count in graph_writes.items():
            writes[name] = writes.get(name, 0) + count
    local_calls = unique_call_rows(
        [item for item in calls if str(item.get("source_graph", "")) in graph_names and item.get("call_kind") == "local_blueprint_graph"],
        ("source_graph", "function", "target_graph"),
    )
    missing_calls = unique_call_rows(
        [item for item in quality.get("blueprint_missing_candidates", []) if isinstance(item, dict) and str(item.get("source_graph", "")) in graph_names],
        ("source_graph", "function"),
    )
    return {
        "graph_names": graph_names,
        "reads": reads,
        "writes": writes,
        "local_calls": local_calls,
        "missing_calls": missing_calls,
    }


def infer_behavior_lines(area: str, rollup: dict[str, object]) -> list[str]:
    reads = rollup.get("reads", {}) if isinstance(rollup.get("reads", {}), dict) else {}
    writes = rollup.get("writes", {}) if isinstance(rollup.get("writes", {}), dict) else {}
    local_calls = rollup.get("local_calls", []) if isinstance(rollup.get("local_calls", []), list) else []
    missing_calls = rollup.get("missing_calls", []) if isinstance(rollup.get("missing_calls", []), list) else []
    graphs = sorted(str(name) for name in rollup.get("graph_names", set()))
    graph_text = ", ".join(graphs[:5]) if graphs else "-"
    lines: list[str] = []
    if area == "Glide":
        lines.append(f"- Glide logic appears to be split across {graph_text}; start/update checks are tied together through local graph calls.")
        lines.append("- Watch speed, pitch, stamina, and parachute state variables before changing glide feel.")
    elif area == "Sliding":
        lines.append(f"- Sliding logic appears to manage start/clear/decay state across {graph_text}.")
        lines.append("- Variables around slide multiplier, slope decay, replicated slide transform, and stamina are likely behavior-critical.")
    elif area == "Nursing":
        lines.append(f"- Nursing logic appears to combine server state, allied/team checks, trough visuals, and replicated effectiveness values.")
        lines.append("- Check component visibility/audio/FX references before changing nursing range or visuals.")
    elif area == "MultiUse":
        lines.append("- MultiUse logic likely controls player interaction entries and execution branches.")
        lines.append("- Treat menu entry conditions, team checks, saddle/rider state, and baby-passenger checks as user-facing behavior.")
    elif area == "Damage":
        lines.append("- Damage logic likely adjusts incoming or outgoing damage and may gate baby/passenger stealing behavior.")
        lines.append("- Recheck target team, rider/passenger state, cooldown, and attack-index variables before edits.")
    elif area == "Replication":
        lines.append("- Replication/timer logic appears to coordinate server-owned state updates and client-visible movement changes.")
        lines.append("- Server/client ownership and replicated variables should be reviewed before changing call order.")
    elif area == "Movement":
        lines.append("- Movement logic appears to bridge jump, movement mode, swim/fall state, and animation transitions.")
        lines.append("- Treat movement mode branches and CharacterMovement references as high-impact.")
    elif area == "Parachute":
        lines.append("- Parachute logic appears to manage replicated parachute intent, animation/audio cues, and cooldown timing.")
        lines.append("- Changes to bWantsToParachute, timers, or force duration likely affect both feel and replication.")
    elif area == "HUD":
        lines.append("- HUD logic appears to render status feedback from gameplay variables rather than owning core state.")
        lines.append("- Confirm display-only changes do not hide gameplay-critical warnings or range indicators.")
    elif area == "Passenger":
        lines.append("- Passenger logic appears to compute seat/name-tag offsets and related passenger positioning data.")
        lines.append("- Offset arrays and seat index functions are likely the main risk points.")
    else:
        lines.append(f"- This group does not match a specific ARK behavior area yet; inspect {graph_text} for shared state or parent/native calls.")
    if reads:
        lines.append(f"- Most-read signals: {top_counter_items(reads, 5)}")
    if writes:
        lines.append(f"- Most-written state: {top_counter_items(writes, 5)}")
    if local_calls:
        call_text = ", ".join(f"{item.get('source_graph')} -> {item.get('target_graph')}" for item in local_calls[:5])
        lines.append(f"- Local graph dependencies: {call_text}")
    if missing_calls:
        missing_names = list(dict.fromkeys(str(item.get("function", "")) for item in missing_calls if item.get("function")))
        missing_text = ", ".join(missing_names[:8])
        lines.append(f"- Needs confirmation/capture for: {missing_text}")
    return lines


BEHAVIOR_RULES: dict[str, dict[str, object]] = {
    "Glide": {
        "signals": ("bCanGlide", "bOverrideNewFallVelocity", "StartGlideLocation", "GlidingPullUpMultiplier", "WingTrail", "FlyerForce"),
        "components": ("CharacterMovement", "WingTrail", "ParaAudio"),
        "focus": "Confirm start checks, server/client tick split, fall-velocity override, pull-up modifiers, and visual/audio cues.",
    },
    "Sliding": {
        "signals": ("replicatedSlideLocation", "replicatedSlideRotation", "SlidingAngle", "NewSlideMulti", "TempSlide", "NS_Sliding_VFX"),
        "components": ("CharacterMovement", "Sliding", "VFX"),
        "focus": "Confirm enter/clear paths, slope multiplier changes, replicated transform writes, and client presentation.",
    },
    "Nursing": {
        "signals": ("bIsNursing", "bNurseVisualActive", "BaseNursingRange", "ReplicatedNursingTroughEffectiveness", "NursingTroughFoodEffectiveness"),
        "components": ("TroughVisual", "Nursing", "Status"),
        "focus": "Confirm team checks, enable/disable authority, trough visibility, and replicated effectiveness defaults.",
    },
    "MultiUse": {
        "signals": ("MultiUse", "UseEntries", "BPTryMultiUse", "BPGetMultiUseEntries", "Team", "Rider"),
        "components": ("Inventory", "Status"),
        "focus": "Confirm menu entry availability, use execution branches, team/rider gates, and user-facing text/icon defaults.",
    },
    "Replication": {
        "signals": ("OnRep", "Server", "Client", "Replicated", "Timer", "Authority"),
        "components": ("CharacterMovement", "Status"),
        "focus": "Confirm replicated variables, RepNotify ordering, server-owned writes, and client-only visual updates.",
    },
    "Damage": {
        "signals": ("Damage", "Attack", "Team", "Rider", "Passenger"),
        "components": ("Status",),
        "focus": "Confirm damage adjustment inputs, attacker/target checks, passenger or baby side effects, and return value writes.",
    },
}


def match_rule_terms(terms: Iterable[object], names: Iterable[object]) -> list[str]:
    name_values = [str(name) for name in names if str(name)]
    matches: list[str] = []
    for term in terms:
        lowered = str(term).lower()
        for name in name_values:
            if lowered and lowered in name.lower():
                matches.append(name)
                break
    return list(dict.fromkeys(matches))


def render_behavior_rule_checks(
    sorted_area_items: list[tuple[str, list[dict[str, object]]]],
    rollups: dict[str, dict[str, object]],
    known_defaults: set[str],
    known_components: set[str],
) -> list[str]:
    lines = ["", "## Behavior Rule Checks", ""]
    lines.append(table_row(["Area", "Observed Signals", "Known Defaults", "Known Components", "Review Focus"]))
    lines.append(table_row(["---", "---", "---", "---", "---"]))
    for area, _area_items in sorted_area_items:
        rule = BEHAVIOR_RULES.get(area)
        if not rule:
            continue
        rollup = rollups.get(area, {})
        reads = rollup.get("reads", {}) if isinstance(rollup.get("reads", {}), dict) else {}
        writes = rollup.get("writes", {}) if isinstance(rollup.get("writes", {}), dict) else {}
        graph_names = rollup.get("graph_names", set()) if isinstance(rollup.get("graph_names", set()), set) else set()
        signal_names = set(reads) | set(writes) | set(str(name) for name in graph_names)
        observed = match_rule_terms(rule.get("signals", ()), signal_names)
        defaults = match_rule_terms(rule.get("signals", ()), known_defaults)
        components = match_rule_terms(rule.get("components", ()), known_components)
        lines.append(
            table_row(
                [
                    area,
                    ", ".join(observed[:8]) if observed else "-",
                    ", ".join(defaults[:8]) if defaults else "-",
                    ", ".join(components[:8]) if components else "-",
                    rule.get("focus", ""),
                ]
            )
        )
    lines.append("")
    return lines


def render_behavior_summary(asset_payload: dict[str, object]) -> str:
    metadata = asset_payload.get("metadata", {}) if isinstance(asset_payload.get("metadata", {}), dict) else {}
    graphs = [graph for graph in asset_payload.get("graphs", []) if isinstance(graph, dict)]
    call_graph = asset_payload.get("call_graph", {}) if isinstance(asset_payload.get("call_graph", {}), dict) else {}
    calls = [item for item in call_graph.get("calls", []) if isinstance(item, dict)]
    quality = collect_asset_quality(asset_payload)
    area_graphs: dict[str, list[dict[str, object]]] = {}
    for graph in graphs:
        area = behavior_area(str(graph.get("graph_name", "")))
        area_graphs.setdefault(area, []).append(graph)

    lines = [
        "# Blueprint Behavior Summary",
        "",
        "## Summary",
        "",
        f"- Asset: {metadata.get('asset_name', '-')}",
        f"- Graphs: {metadata.get('graph_count', 0)}",
        f"- Nodes: {metadata.get('node_count', 0)}",
        f"- Note function overrides: {metadata.get('note_function_count', 0)}",
        f"- Confidence: {asset_payload.get('diagnostics', {}).get('confidence_level', '-') if isinstance(asset_payload.get('diagnostics', {}), dict) else '-'}",
        "",
        "## Behavior Areas",
        "",
    ]
    lines.append(table_row(["Area", "Graphs", "Nodes", "Key Graphs"]))
    lines.append(table_row(["---", "---", "---", "---"]))
    for area, area_items in sorted(area_graphs.items(), key=lambda item: (item[0] == "Other", item[0])):
        key_graphs = ", ".join(str(graph.get("graph_name", "")) for graph in area_items[:5])
        nodes = sum(int(graph.get("node_count") or 0) for graph in area_items)
        lines.append(table_row([area, len(area_items), nodes, key_graphs]))

    known_defaults = set()
    defaults = asset_payload.get("class_defaults", {})
    if isinstance(defaults, dict):
        variables = defaults.get("variables", {})
        if isinstance(variables, dict):
            known_defaults.update(str(name) for name in variables)
    known_components = set()
    components = asset_payload.get("component_defaults", {})
    if isinstance(components, dict):
        for component in components.get("components", []):
            if isinstance(component, dict) and component.get("name"):
                known_components.add(str(component.get("name")))

    sorted_area_items = sorted(area_graphs.items(), key=lambda item: (item[0] == "Other", item[0]))
    rollups = {area: behavior_area_rollup(area_items, calls, quality) for area, area_items in sorted_area_items}

    lines.extend(["", "## Inferred Behavior", ""])
    for area, _area_items in sorted_area_items:
        lines.extend([f"### {area}", ""])
        lines.extend(infer_behavior_lines(area, rollups[area]))
        lines.append("")

    lines.extend(render_behavior_rule_checks(sorted_area_items, rollups, known_defaults, known_components))

    lines.extend(["", "## Area Details", ""])
    for area, _area_items in sorted_area_items:
        rollup = rollups[area]
        graph_names = rollup.get("graph_names", set()) if isinstance(rollup.get("graph_names", set()), set) else set()
        area_reads = rollup.get("reads", {}) if isinstance(rollup.get("reads", {}), dict) else {}
        area_writes = rollup.get("writes", {}) if isinstance(rollup.get("writes", {}), dict) else {}
        local_calls = rollup.get("local_calls", []) if isinstance(rollup.get("local_calls", []), list) else []
        missing_calls = rollup.get("missing_calls", []) if isinstance(rollup.get("missing_calls", []), list) else []
        referenced_defaults = {name: area_reads.get(name, 0) + area_writes.get(name, 0) for name in set(area_reads) | set(area_writes) if name in known_defaults}
        referenced_components = {name: area_reads.get(name, 0) + area_writes.get(name, 0) for name in set(area_reads) | set(area_writes) if name in known_components}
        lines.extend([f"### {area}", ""])
        lines.append(f"- Graphs: {', '.join(sorted(graph_names))}")
        lines.append(f"- Top reads: {top_counter_items(area_reads)}")
        lines.append(f"- Top writes: {top_counter_items(area_writes)}")
        lines.append(f"- Class defaults referenced: {top_counter_items(referenced_defaults)}")
        lines.append(f"- Components referenced: {top_counter_items(referenced_components)}")
        if local_calls:
            call_text = ", ".join(f"{item.get('source_graph')} -> {item.get('target_graph')}" for item in local_calls[:12])
            lines.append(f"- Local graph calls: {call_text}")
        else:
            lines.append("- Local graph calls: -")
        if missing_calls:
            missing_text = ", ".join(f"{item.get('source_graph')} -> {item.get('function')}" for item in missing_calls[:12])
            lines.append(f"- Still unresolved graph-like calls: {missing_text}")
        else:
            lines.append("- Still unresolved graph-like calls: -")
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
        "## Graphs",
        "",
    ]
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
        print(f"No graph .txt files found in asset directory: {asset_dir}", file=sys.stderr)
        return 2
    paths = resolve_output_paths(args)
    keywords = profile_keywords(args.profile, args.keyword)
    context = asset_context_from_args(args, asset_dir, manifest)
    asset_payload = build_asset_payload(args, asset_dir, manifest, graph_records, context, keywords)
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
