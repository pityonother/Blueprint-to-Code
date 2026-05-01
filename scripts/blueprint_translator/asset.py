"""Asset-directory workflows spanning multiple Blueprint graph pages."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
from pathlib import Path

from .context import context_from_args, parse_components_context, parse_defaults_context
from .core import parse_blueprint_text
from .diagnostics import build_diagnostic_findings, diagnostic_counts, diagnostic_finding, render_diagnostics_report
from .output import resolve_output_paths, write_glossary
from .quality import (
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
            item = {
                "source_graph": graph.get("graph_name", ""),
                "source_node": node.get("label") or node.get("name") or "",
                "function": function_name,
                "target_graph": target.get("graph_name") if target else "",
                "call_kind": call_kind,
            }
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
        },
        "context": context,
        "class_defaults": defaults_context,
        "component_defaults": components_context,
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


def write_asset_graph_outputs(asset_payload: dict[str, object], graph_reports_dir: Path) -> None:
    graph_reports_dir.mkdir(parents=True, exist_ok=True)
    index_lines = ["# Blueprint Asset Graph Reports", ""]
    for index, graph in enumerate(asset_payload.get("graphs", []), start=1):
        if not isinstance(graph, dict):
            continue
        base = f"{index:02d}_{safe_filename(str(graph.get('graph_name', '')), 'graph')}"
        report_path = graph_reports_dir / f"{base}_report.md"
        json_path = graph_reports_dir / f"{base}.json"
        diagnostics_path = graph_reports_dir / f"{base}_diagnostics.md"
        payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
        report_path.write_text(render_graph_payload_report(graph), encoding="utf-8")
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        diagnostics_path.write_text(render_diagnostics_report(payload), encoding="utf-8")
        index_lines.append(f"- [{graph.get('graph_name', base)}]({report_path.name})")
    (graph_reports_dir / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


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
    paths["asset_report"].write_text(render_asset_report(asset_payload), encoding="utf-8")
    paths["report"].write_text(render_asset_report(asset_payload), encoding="utf-8")
    paths["asset_json"].write_text(json.dumps(asset_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["diagnostics_report"].write_text(render_asset_diagnostics_report(asset_payload), encoding="utf-8")
    paths["diagnostics_json"].write_text(json.dumps({"metadata": asset_payload.get("metadata", {}), "diagnostics": asset_payload.get("diagnostics", {})}, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["call_graph"].write_text(render_call_graph(asset_payload), encoding="utf-8")
    paths["capture_quality_report"].write_text(render_capture_quality_report(asset_payload), encoding="utf-8")
    paths["capture_quality_json"].write_text(json.dumps(collect_asset_quality(asset_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    paths["defaults_suggestions"].write_text(json.dumps(build_defaults_suggestions(asset_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    paths["components_suggestions"].write_text(json.dumps(build_components_suggestions(asset_payload), ensure_ascii=False, indent=2), encoding="utf-8")
    paths["next_actions"].write_text(render_next_actions(asset_payload), encoding="utf-8-sig")
    write_asset_graph_outputs(asset_payload, paths["graph_reports"])
    write_glossary(paths["dir"])
    print(f"Wrote asset output directory: {paths['dir']}")
    for label in (
        "asset_report",
        "report",
        "asset_json",
        "diagnostics_report",
        "diagnostics_json",
        "call_graph",
        "capture_quality_report",
        "capture_quality_json",
        "defaults_suggestions",
        "components_suggestions",
        "next_actions",
    ):
        print(f"- {label}: {paths[label]}")
    print(f"- graph_reports: {paths['graph_reports']}")
    print(f"Parsed graphs: {asset_payload['metadata']['graph_count']}")
    print(f"Parsed nodes: {asset_payload['metadata']['node_count']}")
    print(f"Confidence: {asset_payload['diagnostics']['confidence_level']}")
    return 0
