"""Single-graph and asset-level Blueprint comparison workflows."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .asset import (
    asset_context_from_args,
    build_asset_payload,
    collect_asset_missing_link_rows,
    discover_asset_graphs,
    load_manifest,
    normalize_graph_lookup,
)
from .core import parse_blueprint_text
from .output import resolve_output_paths, write_glossary
from .quality import behavior_area
from .utils import label_for, node_key, profile_keywords, table_row

def load_compare_input(path_text: str, keywords: list[str]) -> dict[str, object]:
    path = Path(os.path.expandvars(path_text)).expanduser()
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8-sig"))
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    _, _, payload = parse_blueprint_text(text=raw, source=str(path), asset_name=path.stem, graph_name="", keywords=keywords)
    return payload


def count_nodes_by(payload: dict[str, object], field: str) -> Counter:
    return Counter(str(node.get(field, "")) for node in payload.get("nodes", []) if node.get(field))


def keyed_nodes(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for node in payload.get("nodes", []):
        key = str(node.get("key") or node_key(node))
        if key in result:
            key = f"{key}#{node.get('index')}"
        result[key] = node
    return result


def node_signature_for_compare(node: dict[str, object]) -> str:
    fields = [
        str(node.get("node_type", "")),
        str(node.get("function", "")),
        str(node.get("variable", "")),
        str(node.get("event", "")),
        str(node.get("macro", "")),
        str(node.get("control_kind", "")),
    ]
    pin_names = sorted(str(pin.get("name", "")) for pin in node.get("pins", []) if pin.get("name"))
    fields.extend(pin_names)
    return " | ".join(field for field in fields if field)


def node_fuzzy_signature(node: dict[str, object]) -> str:
    fields = [
        str(node.get("node_type", "")),
        str(node.get("function") or node.get("variable") or node.get("event") or node.get("macro") or node.get("label") or ""),
    ]
    return " | ".join(field for field in fields if field)


def match_compare_nodes(old: dict[str, object], new: dict[str, object]) -> dict[str, object]:
    old_nodes = list(old.get("nodes", []))
    new_nodes = list(new.get("nodes", []))
    unmatched_old = {i for i in range(len(old_nodes))}
    unmatched_new = {i for i in range(len(new_nodes))}
    matched_by_guid: list[dict[str, object]] = []
    matched_by_signature: list[dict[str, object]] = []
    matched_by_fuzzy: list[dict[str, object]] = []

    new_by_guid: dict[str, int] = {}
    for i, node in enumerate(new_nodes):
        guid = str(node.get("node_guid", ""))
        if guid:
            new_by_guid.setdefault(guid, i)
    for oi, old_node in enumerate(old_nodes):
        guid = str(old_node.get("node_guid", ""))
        ni = new_by_guid.get(guid)
        if guid and ni is not None and ni in unmatched_new:
            unmatched_old.discard(oi)
            unmatched_new.discard(ni)
            matched_by_guid.append({"old": describe_compare_node(old_node), "new": describe_compare_node(new_nodes[ni]), "guid": guid})

    def match_by_signature(kind: str, signature_fn, bucket: list[dict[str, object]]) -> None:
        new_by_sig: dict[str, list[int]] = defaultdict(list)
        for ni in unmatched_new:
            new_by_sig[signature_fn(new_nodes[ni])].append(ni)
        for oi in list(unmatched_old):
            sig = signature_fn(old_nodes[oi])
            candidates = new_by_sig.get(sig, [])
            while candidates and candidates[0] not in unmatched_new:
                candidates.pop(0)
            if candidates:
                ni = candidates.pop(0)
                unmatched_old.discard(oi)
                unmatched_new.discard(ni)
                bucket.append({"old": describe_compare_node(old_nodes[oi]), "new": describe_compare_node(new_nodes[ni]), kind: sig})

    match_by_signature("signature", node_signature_for_compare, matched_by_signature)
    match_by_signature("fuzzy", node_fuzzy_signature, matched_by_fuzzy)
    return {
        "old_nodes": old_nodes,
        "new_nodes": new_nodes,
        "unmatched_old": unmatched_old,
        "unmatched_new": unmatched_new,
        "matched_by_guid": matched_by_guid,
        "matched_by_signature": matched_by_signature,
        "matched_by_fuzzy": matched_by_fuzzy,
    }


def pin_default_map(payload: dict[str, object]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for node in payload.get("nodes", []):
        nkey = str(node.get("key") or node_key(node))
        for pin in node.get("pins", []):
            pkey = str(pin.get("id") or pin.get("name") or "")
            mapping[f"{nkey}::{pkey}"] = str(pin.get("default", ""))
    return mapping


def linked_to_set(payload: dict[str, object]) -> set[tuple[str, str, str, str]]:
    values: set[tuple[str, str, str, str]] = set()
    for link in payload.get("links", []):
        source = str(link.get("source_node_guid") or link.get("source_node") or "")
        values.add((source, str(link.get("source_pin_id", "")), str(link.get("target_node", "")), str(link.get("target_pin_id", ""))))
    return values


def flow_edge_set(payload: dict[str, object], flow: str) -> set[str]:
    if flow == "exec":
        return {f"{edge.get('source_node')}:{edge.get('source_pin')}->{edge.get('target_node')}:{edge.get('target_pin_id')}" for edge in payload.get("exec_flow", {}).get("edges", [])}
    return {f"{item.get('node')}:{item.get('pin')}<-{item.get('source')}" for item in payload.get("data_flow", {}).get("dependencies", [])}


def compare_payloads(old: dict[str, object], new: dict[str, object]) -> dict[str, object]:
    matches = match_compare_nodes(old, new)
    old_nodes = matches["old_nodes"]
    new_nodes = matches["new_nodes"]
    old_pin_defaults = pin_default_map(old)
    new_pin_defaults = pin_default_map(new)
    old_links = linked_to_set(old)
    new_links = linked_to_set(new)
    old_exec = flow_edge_set(old, "exec")
    new_exec = flow_edge_set(new, "exec")
    old_data = flow_edge_set(old, "data")
    new_data = flow_edge_set(new, "data")
    old_keywords = Counter(old.get("keyword_hits", {}))
    new_keywords = Counter(new.get("keyword_hits", {}))

    changed_defaults = []
    for key in sorted(set(old_pin_defaults) & set(new_pin_defaults)):
        if old_pin_defaults[key] != new_pin_defaults[key]:
            changed_defaults.append({"pin": key, "old": old_pin_defaults[key], "new": new_pin_defaults[key]})

    keyword_delta = {
        key: new_keywords.get(key, 0) - old_keywords.get(key, 0)
        for key in sorted(set(old_keywords) | set(new_keywords))
        if new_keywords.get(key, 0) != old_keywords.get(key, 0)
    }
    diff: dict[str, object] = {
        "metadata": {
            "generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "old_source": old.get("metadata", {}).get("source", ""),
            "new_source": new.get("metadata", {}).get("source", ""),
        },
        "node_count": {"old": old.get("metadata", {}).get("node_count", 0), "new": new.get("metadata", {}).get("node_count", 0)},
        "node_type_delta": counter_delta(count_nodes_by(old, "node_type"), count_nodes_by(new, "node_type")),
        "matched_by_guid": matches["matched_by_guid"],
        "matched_by_signature": matches["matched_by_signature"],
        "matched_by_fuzzy": matches["matched_by_fuzzy"],
        "added_nodes": [describe_compare_node(new_nodes[index]) for index in sorted(matches["unmatched_new"])],
        "removed_nodes": [describe_compare_node(old_nodes[index]) for index in sorted(matches["unmatched_old"])],
        "function_call_delta": counter_delta(count_nodes_by(old, "function"), count_nodes_by(new, "function")),
        "variable_get_delta": counter_delta(Counter(str(node.get("variable", "")) for node in old.get("variable_gets", []) if node.get("variable")), Counter(str(node.get("variable", "")) for node in new.get("variable_gets", []) if node.get("variable"))),
        "variable_set_delta": counter_delta(Counter(str(node.get("variable", "")) for node in old.get("variable_sets", []) if node.get("variable")), Counter(str(node.get("variable", "")) for node in new.get("variable_sets", []) if node.get("variable"))),
        "event_delta": counter_delta(count_nodes_by(old, "event"), count_nodes_by(new, "event")),
        "macro_delta": counter_delta(count_nodes_by(old, "macro"), count_nodes_by(new, "macro")),
        "changed_pin_defaults": changed_defaults,
        "linked_to_delta": {"added": sorted(new_links - old_links), "removed": sorted(old_links - new_links)},
        "exec_flow_delta": {"added": sorted(new_exec - old_exec), "removed": sorted(old_exec - new_exec)},
        "data_flow_delta": {"added": sorted(new_data - old_data), "removed": sorted(old_data - new_data)},
        "keyword_delta": keyword_delta,
    }
    diff.update(classify_changes(diff))
    return diff


def counter_delta(old: Counter, new: Counter) -> dict[str, int]:
    keys = sorted(set(old) | set(new))
    return {key: new.get(key, 0) - old.get(key, 0) for key in keys if new.get(key, 0) != old.get(key, 0)}


def describe_compare_node(node: dict[str, object]) -> str:
    return f"{node.get('node_type')} | {label_for(node)} | guid={node.get('node_guid') or '-'}"


def classify_changes(diff: dict[str, object]) -> dict[str, list[str]]:
    likely_logic: list[str] = []
    likely_equiv: list[str] = []
    unknown: list[str] = []
    if diff.get("function_call_delta"):
        likely_logic.append("Function call set changed.")
    if diff.get("variable_set_delta"):
        likely_logic.append("Variable writes changed.")
    if diff.get("changed_pin_defaults"):
        likely_logic.append("Pin default values changed.")
    if diff.get("linked_to_delta", {}).get("added") or diff.get("linked_to_delta", {}).get("removed"):
        likely_logic.append("Pin LinkedTo wiring changed.")
    if diff.get("exec_flow_delta", {}).get("added") or diff.get("exec_flow_delta", {}).get("removed"):
        likely_logic.append("Execution flow changed.")
    if diff.get("data_flow_delta", {}).get("added") or diff.get("data_flow_delta", {}).get("removed"):
        likely_logic.append("Data flow changed.")
    important_keywords = {"Radius", "Range", "Overlap", "Trace", "Register", "Unregister", "Refresh", "Inventory", "Stasis", "Octree", "Server", "Client", "Multicast"}
    for keyword, delta in diff.get("keyword_delta", {}).items():
        if keyword in important_keywords:
            likely_logic.append(f"Keyword {keyword} changed by {delta}.")
    if diff.get("added_nodes") or diff.get("removed_nodes"):
        unknown.append("Nodes were added or removed; review whether they are layout/comment nodes or runtime nodes.")
    if not likely_logic and not unknown:
        likely_equiv.append("No parsed logic differences detected; changes are likely equivalent or outside copied text.")
    return {
        "likely_equivalent_changes": likely_equiv,
        "likely_logic_changes": sorted(set(likely_logic)),
        "unknown_changes": unknown,
    }


def render_compare_report(diff: dict[str, object]) -> str:
    lines = ["# Blueprint Compare Report", ""]
    meta = diff["metadata"]
    lines.append(f"- Generated: {meta.get('generated')}")
    lines.append(f"- Old: {meta.get('old_source')}")
    lines.append(f"- New: {meta.get('new_source')}")
    lines.append(f"- Node count: {diff['node_count']['old']} -> {diff['node_count']['new']}")
    lines.append("")
    for title, key in [
        ("Likely Equivalent Changes", "likely_equivalent_changes"),
        ("Likely Logic Changes", "likely_logic_changes"),
        ("Unknown Changes", "unknown_changes"),
        ("Matched By GUID", "matched_by_guid"),
        ("Matched By Signature", "matched_by_signature"),
        ("Matched By Fuzzy", "matched_by_fuzzy"),
        ("Added Nodes", "added_nodes"),
        ("Removed Nodes", "removed_nodes"),
    ]:
        lines.append(f"## {title}")
        lines.append("")
        values = diff.get(key, [])
        lines.extend(f"- {value}" for value in values) if values else lines.append("- none")
        lines.append("")
    for title, key in [
        ("Node Type Delta", "node_type_delta"),
        ("Function Call Delta", "function_call_delta"),
        ("Variable Get Delta", "variable_get_delta"),
        ("Variable Set Delta", "variable_set_delta"),
        ("Event Delta", "event_delta"),
        ("Macro Delta", "macro_delta"),
        ("Keyword Delta", "keyword_delta"),
    ]:
        lines.append(f"## {title}")
        lines.append("")
        values = diff.get(key, {})
        if values:
            lines.append(table_row(["Name", "Delta"]))
            lines.append(table_row(["---", "---"]))
            for name, delta in values.items():
                lines.append(table_row([name, delta]))
        else:
            lines.append("- none")
        lines.append("")
    lines.append("## Pin Default Value Differences")
    lines.append("")
    defaults = diff.get("changed_pin_defaults", [])
    if defaults:
        lines.append(table_row(["Pin", "Old", "New"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in defaults:
            lines.append(table_row([item["pin"], item["old"], item["new"]]))
    else:
        lines.append("- none")
    lines.append("")
    for title, key in [("LinkedTo Delta", "linked_to_delta"), ("Exec Flow Delta", "exec_flow_delta"), ("Data Flow Delta", "data_flow_delta")]:
        lines.append(f"## {title}")
        lines.append("")
        delta = diff.get(key, {})
        for side in ("added", "removed"):
            values = delta.get(side, [])
            lines.append(f"### {side.title()}")
            lines.extend(f"- {value}" for value in values[:200]) if values else lines.append("- none")
            lines.append("")
    return "\n".join(lines)


def render_compare_summary(diff: dict[str, object]) -> str:
    lines = ["# Blueprint Compare Summary", ""]
    lines.append(f"- Node count: {diff['node_count']['old']} -> {diff['node_count']['new']}")
    lines.append(f"- Likely logic changes: {len(diff.get('likely_logic_changes', []))}")
    lines.append(f"- Unknown changes: {len(diff.get('unknown_changes', []))}")
    lines.append("")
    for note in diff.get("likely_logic_changes", []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def render_compare_prompt(diff: dict[str, object]) -> str:
    return "\n".join(
        [
            "Please review this ARK/Unreal Blueprint diff.",
            "Classify which changes are runtime logic changes, which are likely equivalent/layout changes, and what should be manually inspected.",
            "Do not overstate certainty when native C++ or missing Blueprint context is required.",
            "",
            json.dumps(diff, ensure_ascii=False, indent=2)[:60000],
        ]
    ) + "\n"


def render_compare_compact(diff: dict[str, object]) -> str:
    lines = ["Blueprint compare compact", ""]
    lines.append(f"node_count: {diff['node_count']['old']} -> {diff['node_count']['new']}")
    lines.append(f"logic_changes: {len(diff.get('likely_logic_changes', []))}")
    lines.append(f"unknown_changes: {len(diff.get('unknown_changes', []))}")
    lines.append("")
    lines.extend(f"- {note}" for note in diff.get("likely_logic_changes", []))
    return "\n".join(lines) + "\n"


def load_asset_payload_input(args: argparse.Namespace, asset_dir_text: str, keywords: list[str]) -> dict[str, object]:
    asset_dir = Path(os.path.expandvars(asset_dir_text)).expanduser()
    if not asset_dir.exists() or not asset_dir.is_dir():
        raise FileNotFoundError(f"Asset directory not found: {asset_dir}")
    manifest = load_manifest(asset_dir)
    graph_records = discover_asset_graphs(asset_dir, manifest)
    if not graph_records:
        raise FileNotFoundError(f"No graph .txt files found in asset directory: {asset_dir}")
    context = asset_context_from_args(args, asset_dir, manifest)
    return build_asset_payload(args, asset_dir, manifest, graph_records, context, keywords)


def graph_lookup_from_asset(asset_payload: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        key = normalize_graph_lookup(str(graph.get("graph_name", "")))
        if key:
            result[key] = graph
    return result


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def dict_value_delta(old: dict[str, object], new: dict[str, object]) -> dict[str, object]:
    added = {key: new[key] for key in sorted(set(new) - set(old))}
    removed = {key: old[key] for key in sorted(set(old) - set(new))}
    changed = {
        key: {"old": old[key], "new": new[key]}
        for key in sorted(set(old) & set(new))
        if stable_json(old[key]) != stable_json(new[key])
    }
    return {"added": added, "removed": removed, "changed": changed}


def component_defaults_by_name(asset_payload: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    component_context = asset_payload.get("component_defaults", {})
    components = component_context.get("components", []) if isinstance(component_context, dict) else []
    for component in components:
        if not isinstance(component, dict):
            continue
        name = str(component.get("name") or component.get("class") or "")
        if name:
            result[name] = {
                "class": component.get("class", ""),
                "defaults": component.get("defaults", {}),
                "purpose": component.get("purpose", ""),
            }
    return result


def relation_set(asset_payload: dict[str, object], key: str) -> set[str]:
    call_graph = asset_payload.get("call_graph", {})
    values: set[str] = set()
    for item in call_graph.get(key, []):
        if not isinstance(item, dict):
            continue
        if key == "calls":
            values.add(f"{item.get('source_graph')} -> call {item.get('function')} -> {item.get('target_graph') or 'missing/native/inherited'}")
        elif key == "delegate_bindings":
            values.add(f"{item.get('source_graph')} -> delegate {item.get('delegate')} -> {item.get('handler')} ({item.get('handler_graph') or 'same/missing'})")
        elif key == "macro_usages":
            values.add(f"{item.get('source_graph')} -> macro {item.get('macro')} -> {item.get('macro_graph') or 'inline/native/missing'}")
        elif key == "missing_macro_links":
            values.add(f"{item.get('source_graph')} -> missing macro {item.get('missing_macro_node')} from {item.get('referenced_from')}")
    return values


def missing_link_set(asset_payload: dict[str, object]) -> set[str]:
    values: set[str] = set()
    for item in collect_asset_missing_link_rows(asset_payload):
        values.add(f"{item.get('graph')} -> {item.get('target_node')} [{item.get('target_kind')}] from {item.get('referenced_from')}")
    return values


def set_delta(old: set[str], new: set[str]) -> dict[str, list[str]]:
    return {"added": sorted(new - old), "removed": sorted(old - new)}


def has_delta(delta: dict[str, object]) -> bool:
    return any(bool(delta.get(key)) for key in ("added", "removed", "changed"))


def compare_asset_payloads(old: dict[str, object], new: dict[str, object]) -> dict[str, object]:
    old_meta = old.get("metadata", {})
    new_meta = new.get("metadata", {})
    old_graphs = graph_lookup_from_asset(old)
    new_graphs = graph_lookup_from_asset(new)
    old_graph_keys = set(old_graphs)
    new_graph_keys = set(new_graphs)
    added_graphs = sorted(str(new_graphs[key].get("graph_name", key)) for key in new_graph_keys - old_graph_keys)
    removed_graphs = sorted(str(old_graphs[key].get("graph_name", key)) for key in old_graph_keys - new_graph_keys)

    graph_diffs: list[dict[str, object]] = []
    likely_behavior_changes: list[str] = []
    unknown_changes: list[str] = []
    for key in sorted(old_graph_keys & new_graph_keys):
        old_graph = old_graphs[key]
        new_graph = new_graphs[key]
        old_payload = old_graph.get("payload", {}) if isinstance(old_graph.get("payload", {}), dict) else {}
        new_payload = new_graph.get("payload", {}) if isinstance(new_graph.get("payload", {}), dict) else {}
        graph_diff = compare_payloads(old_payload, new_payload)
        graph_name = str(new_graph.get("graph_name") or old_graph.get("graph_name") or key)
        graph_diffs.append(
            {
                "graph": graph_name,
                "old_type": old_graph.get("graph_type", ""),
                "new_type": new_graph.get("graph_type", ""),
                "node_count": graph_diff.get("node_count", {}),
                "likely_logic_changes": graph_diff.get("likely_logic_changes", []),
                "unknown_changes": graph_diff.get("unknown_changes", []),
                "added_nodes": graph_diff.get("added_nodes", []),
                "removed_nodes": graph_diff.get("removed_nodes", []),
                "diff": graph_diff,
            }
        )
        for note in graph_diff.get("likely_logic_changes", []):
            likely_behavior_changes.append(f"{graph_name}: {note}")
        for note in graph_diff.get("unknown_changes", []):
            unknown_changes.append(f"{graph_name}: {note}")

    if added_graphs:
        likely_behavior_changes.append(f"Graphs added: {', '.join(added_graphs)}")
    if removed_graphs:
        likely_behavior_changes.append(f"Graphs removed: {', '.join(removed_graphs)}")

    old_defaults = old.get("class_defaults", {}).get("variables", {}) if isinstance(old.get("class_defaults", {}), dict) else {}
    new_defaults = new.get("class_defaults", {}).get("variables", {}) if isinstance(new.get("class_defaults", {}), dict) else {}
    defaults_delta = dict_value_delta(old_defaults if isinstance(old_defaults, dict) else {}, new_defaults if isinstance(new_defaults, dict) else {})
    if has_delta(defaults_delta):
        likely_behavior_changes.append("Class default variables changed.")

    components_delta = dict_value_delta(component_defaults_by_name(old), component_defaults_by_name(new))
    if has_delta(components_delta):
        likely_behavior_changes.append("Component defaults or component classes changed.")

    relation_deltas = {
        "function_calls": set_delta(relation_set(old, "calls"), relation_set(new, "calls")),
        "delegate_bindings": set_delta(relation_set(old, "delegate_bindings"), relation_set(new, "delegate_bindings")),
        "macro_usages": set_delta(relation_set(old, "macro_usages"), relation_set(new, "macro_usages")),
        "missing_macro_links": set_delta(relation_set(old, "missing_macro_links"), relation_set(new, "missing_macro_links")),
        "missing_link_targets": set_delta(missing_link_set(old), missing_link_set(new)),
    }
    for title, delta in relation_deltas.items():
        if delta.get("added") or delta.get("removed"):
            likely_behavior_changes.append(f"{title.replace('_', ' ').title()} changed.")

    if not likely_behavior_changes and not unknown_changes:
        likely_equivalent_changes = ["No parsed asset-level behavior differences detected."]
    else:
        likely_equivalent_changes = []

    diff = {
        "metadata": {
            "generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "old_asset_dir": old_meta.get("asset_dir", ""),
            "new_asset_dir": new_meta.get("asset_dir", ""),
            "old_asset_name": old_meta.get("asset_name", ""),
            "new_asset_name": new_meta.get("asset_name", ""),
        },
        "node_count": {"old": old_meta.get("node_count", 0), "new": new_meta.get("node_count", 0)},
        "graph_count": {"old": old_meta.get("graph_count", 0), "new": new_meta.get("graph_count", 0)},
        "added_graphs": added_graphs,
        "removed_graphs": removed_graphs,
        "matched_graphs": [str(new_graphs[key].get("graph_name", key)) for key in sorted(old_graph_keys & new_graph_keys)],
        "defaults_delta": defaults_delta,
        "components_delta": components_delta,
        "relation_deltas": relation_deltas,
        "graph_diffs": graph_diffs,
        "likely_equivalent_changes": likely_equivalent_changes,
        "likely_behavior_changes": sorted(set(likely_behavior_changes)),
        "unknown_changes": sorted(set(unknown_changes)),
        "old_asset": old,
        "new_asset": new,
    }
    diff["behavior_impacts"] = build_behavior_impact_rows(diff)
    return diff


IMPACT_RULES: dict[str, dict[str, object]] = {
    "Glide": {
        "keywords": ("glide", "gliding", "fallvelocity", "flyer", "wingtrail", "pullup", "parachute", "startglide"),
        "impact": "May change glide entry conditions, air speed/pitch feel, pull-up logic, or glide visual feedback.",
        "inspect": "Inspect StartGlide, CanGlide, Client/Server Tick Gliding, BPOverrideCharacterNewFallVelocity, and related defaults.",
    },
    "Sliding": {
        "keywords": ("slide", "sliding", "slope", "replicatedslide", "clearsliding"),
        "impact": "May change slide entry/exit, slope acceleration, server-synced position, or client presentation.",
        "inspect": "Inspect Client Tick Sliding, Server Tick Sliding, Clear Sliding, replicatedSlideLocation, and replicatedSlideRotation.",
    },
    "Nursing": {
        "keywords": ("nurs", "trough", "baby", "effectiveness", "disable nursing", "enable nursing"),
        "impact": "May change nursing enablement, trough/range visuals, team checks, or food-effectiveness replication.",
        "inspect": "Inspect EnableNursing, Disable Nursing, CanNurseDino, and Check Team and Toggle Trough Visibility.",
    },
    "MultiUse": {
        "keywords": ("multiuse", "useentries", "trymultiuse", "entry", "menu"),
        "impact": "May change player interaction entries, availability conditions, or use execution results.",
        "inspect": "Inspect BPGetMultiUseEntries, BPTryMultiUse, and team/rider/state branches.",
    },
    "Damage": {
        "keywords": ("damage", "attack", "hit", "steal"),
        "impact": "May change damage adjustment, attack gating, or passive triggered behavior.",
        "inspect": "Inspect BlueprintAdjustOutputDamage plus attacker/target team, passenger, and baby-related conditions.",
    },
    "Replication": {
        "keywords": ("server", "client", "onrep", "replicated", "timer", "rpc", "authority"),
        "impact": "May change server-owned state, client-visible state, RepNotify behavior, or timer-driven sync cadence.",
        "inspect": "Inspect OnRep graphs, Server/Client Tick graphs, BPTimerServer/BPTimerNonDedicated, and authority branches.",
    },
    "Passenger": {
        "keywords": ("passenger", "seat", "offset", "rider"),
        "impact": "May change passenger seats, offsets, rider state, or passenger display.",
        "inspect": "Inspect BPGetPassengerDinoAdditionalOffset, PassengerOffsets, and seat-index functions.",
    },
    "HUD": {
        "keywords": ("hud", "icon", "draw", "floating"),
        "impact": "May change HUD output, range hints, or status icons shown to the player.",
        "inspect": "Inspect BlueprintDrawFloatingHUD and HideRangeIcon/ShowRangeIcon references.",
    },
    "Movement": {
        "keywords": ("movement", "jump", "run", "velocity", "correction", "fall"),
        "impact": "May change jump, run, movement mode, server correction, or animation-state transitions.",
        "inspect": "Inspect ExecuteJump, BPOnMovementModeChangedNotify, and BPAcknowledgeServerCorrection.",
    },
}


def behavior_area_from_text(text: str) -> str:
    lowered = text.lower()
    for area, rule in IMPACT_RULES.items():
        if any(str(keyword) in lowered for keyword in rule.get("keywords", ())) :
            return area
    return behavior_area(text)


def collect_behavior_evidence(diff: dict[str, object]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = defaultdict(list)

    for graph_name in diff.get("added_graphs", []):
        area = behavior_area_from_text(str(graph_name))
        evidence[area].append(f"graph added: {graph_name}")
    for graph_name in diff.get("removed_graphs", []):
        area = behavior_area_from_text(str(graph_name))
        evidence[area].append(f"graph removed: {graph_name}")

    for graph_diff in diff.get("graph_diffs", []):
        if not isinstance(graph_diff, dict):
            continue
        graph_name = str(graph_diff.get("graph", ""))
        area = behavior_area_from_text(graph_name)
        logic_notes = graph_diff.get("likely_logic_changes", [])
        unknown_notes = graph_diff.get("unknown_changes", [])
        added_nodes = graph_diff.get("added_nodes", [])
        removed_nodes = graph_diff.get("removed_nodes", [])
        if logic_notes:
            evidence[area].append(f"{graph_name}: {len(logic_notes)} parsed logic change(s)")
        if unknown_notes:
            evidence[area].append(f"{graph_name}: {len(unknown_notes)} unknown change(s) need review")
        if added_nodes or removed_nodes:
            evidence[area].append(f"{graph_name}: added nodes {len(added_nodes)} / removed nodes {len(removed_nodes)}")

    defaults_delta = diff.get("defaults_delta", {})
    if isinstance(defaults_delta, dict):
        for side in ("added", "removed", "changed"):
            values = defaults_delta.get(side, {})
            if isinstance(values, dict):
                for name in values:
                    area = behavior_area_from_text(str(name))
                    evidence[area].append(f"default {side}: {name}")

    components_delta = diff.get("components_delta", {})
    if isinstance(components_delta, dict):
        for side in ("added", "removed", "changed"):
            values = components_delta.get(side, {})
            if isinstance(values, dict):
                for name in values:
                    area = behavior_area_from_text(str(name))
                    evidence[area].append(f"component {side}: {name}")

    relation_deltas = diff.get("relation_deltas", {})
    if isinstance(relation_deltas, dict):
        for relation_name, delta in relation_deltas.items():
            if not isinstance(delta, dict):
                continue
            for side in ("added", "removed"):
                for item in delta.get(side, [])[:100]:
                    area = behavior_area_from_text(str(item))
                    evidence[area].append(f"{relation_name} {side}: {item}")
    return evidence


def impact_risk(area: str, items: list[str]) -> str:
    text = "\n".join(items).lower()
    if area in {"Replication", "Glide", "Sliding", "Nursing", "MultiUse"} and len(items) >= 3:
        return "high"
    if "removed" in text or "execution flow" in text or "linkedto" in text:
        return "high"
    if len(items) >= 2:
        return "medium"
    return "low"


def build_behavior_impact_rows(diff: dict[str, object]) -> list[dict[str, object]]:
    evidence = collect_behavior_evidence(diff)
    rows: list[dict[str, object]] = []
    for area, items in sorted(evidence.items(), key=lambda item: (item[0] == "Other", item[0])):
        unique_items = list(dict.fromkeys(items))
        rule = IMPACT_RULES.get(area, {})
        rows.append(
            {
                "area": area,
                "risk": impact_risk(area, unique_items),
                "impact": rule.get("impact") or "May change runtime Blueprint behavior in this area; confirm with the graph and defaults.",
                "inspect": rule.get("inspect") or "Inspect related graphs, variable writes, default changes, and unresolved links.",
                "evidence": unique_items[:30],
            }
        )
    return rows


def render_behavior_impact_report(diff: dict[str, object]) -> str:
    meta = diff.get("metadata", {})
    impacts = [item for item in diff.get("behavior_impacts", []) if isinstance(item, dict)]
    lines = [
        "# Blueprint Behavior Impact Report",
        "",
        "This report translates the asset diff into ARK behavior areas. It is heuristic, but it is intended to answer which gameplay behavior may change.",
        "",
        "## Summary",
        "",
        f"- Old asset: {meta.get('old_asset_name') or '-'}",
        f"- New asset: {meta.get('new_asset_name') or '-'}",
        f"- Graph count: {diff.get('graph_count', {}).get('old', 0)} -> {diff.get('graph_count', {}).get('new', 0)}",
        f"- Node count: {diff.get('node_count', {}).get('old', 0)} -> {diff.get('node_count', {}).get('new', 0)}",
        f"- Impact areas: {len(impacts)}",
        "",
    ]
    if impacts:
        lines.append(table_row(["Area", "Risk", "Likely Impact", "Inspect First"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in impacts:
            lines.append(table_row([item.get("area"), item.get("risk"), item.get("impact"), item.get("inspect")]))
    else:
        lines.append("- No behavior impact areas detected from parsed asset diff.")

    lines.extend(["", "## Evidence By Area", ""])
    for item in impacts:
        lines.extend([f"### {item.get('area')} ({item.get('risk')})", ""])
        lines.append(f"- Likely impact: {item.get('impact')}")
        lines.append(f"- Inspect first: {item.get('inspect')}")
        lines.append("- Evidence:")
        for evidence_item in item.get("evidence", []):
            lines.append(f"  - {evidence_item}")
        if not item.get("evidence"):
            lines.append("  - none")
        lines.append("")
    return "\n".join(lines)


def render_asset_compare_report(diff: dict[str, object]) -> str:
    meta = diff.get("metadata", {})
    lines = ["# Blueprint Asset Compare Report", ""]
    lines.extend(
        [
            "## Summary",
            "",
            f"- Old asset: {meta.get('old_asset_name') or '-'}",
            f"- New asset: {meta.get('new_asset_name') or '-'}",
            f"- Old dir: {meta.get('old_asset_dir') or '-'}",
            f"- New dir: {meta.get('new_asset_dir') or '-'}",
            f"- Graph count: {diff.get('graph_count', {}).get('old', 0)} -> {diff.get('graph_count', {}).get('new', 0)}",
            f"- Node count: {diff.get('node_count', {}).get('old', 0)} -> {diff.get('node_count', {}).get('new', 0)}",
            f"- Behavior-relevant changes: {len(diff.get('likely_behavior_changes', []))}",
            f"- Unknown changes: {len(diff.get('unknown_changes', []))}",
            "",
        ]
    )
    for title, key in [
        ("Behavior-Relevant Changes", "likely_behavior_changes"),
        ("Unknown Changes", "unknown_changes"),
        ("Likely Equivalent Changes", "likely_equivalent_changes"),
        ("Added Graphs", "added_graphs"),
        ("Removed Graphs", "removed_graphs"),
    ]:
        lines.extend([f"## {title}", ""])
        values = diff.get(key, [])
        lines.extend(f"- {value}" for value in values) if values else lines.append("- none")
        lines.append("")

    lines.extend(["## Graph Diffs", ""])
    graph_diffs = list(diff.get("graph_diffs", []))
    if graph_diffs:
        lines.append(table_row(["Graph", "Nodes", "Logic Notes", "Unknown Notes", "Added Nodes", "Removed Nodes"]))
        lines.append(table_row(["---", "---", "---", "---", "---", "---"]))
        for item in graph_diffs:
            node_count = item.get("node_count", {})
            lines.append(
                table_row(
                    [
                        item.get("graph", ""),
                        f"{node_count.get('old', 0)} -> {node_count.get('new', 0)}",
                        len(item.get("likely_logic_changes", [])),
                        len(item.get("unknown_changes", [])),
                        len(item.get("added_nodes", [])),
                        len(item.get("removed_nodes", [])),
                    ]
                )
            )
    else:
        lines.append("- none")
    lines.append("")

    for title, key in [("Class Default Variable Delta", "defaults_delta"), ("Component Delta", "components_delta")]:
        lines.extend([f"## {title}", ""])
        delta = diff.get(key, {})
        for side in ("added", "removed", "changed"):
            values = delta.get(side, {}) if isinstance(delta, dict) else {}
            lines.append(f"### {side.title()}")
            if values:
                if isinstance(values, dict):
                    for name, value in values.items():
                        lines.append(f"- {name}: {value}")
                else:
                    lines.extend(f"- {value}" for value in values)
            else:
                lines.append("- none")
            lines.append("")

    lines.extend(["## Relationship Deltas", ""])
    relation_deltas = diff.get("relation_deltas", {})
    for relation_name, delta in relation_deltas.items():
        lines.append(f"### {relation_name.replace('_', ' ').title()}")
        if not isinstance(delta, dict):
            lines.append("- none")
            lines.append("")
            continue
        for side in ("added", "removed"):
            values = delta.get(side, [])
            lines.append(f"{side.title()}:")
            lines.extend(f"- {value}" for value in values[:120]) if values else lines.append("- none")
        lines.append("")

    lines.extend(["## Per-Graph Details", ""])
    for item in graph_diffs:
        lines.append(f"### {item.get('graph')}")
        details = item.get("diff", {})
        for title, key in [
            ("Likely Logic Changes", "likely_logic_changes"),
            ("Unknown Changes", "unknown_changes"),
            ("Added Nodes", "added_nodes"),
            ("Removed Nodes", "removed_nodes"),
        ]:
            values = details.get(key, []) if isinstance(details, dict) else []
            lines.append(f"{title}:")
            lines.extend(f"- {value}" for value in values[:80]) if values else lines.append("- none")
        lines.append("")
    return "\n".join(lines)


def render_asset_compare_summary(diff: dict[str, object]) -> str:
    lines = ["# Blueprint Asset Compare Summary", ""]
    lines.append(f"- Graph count: {diff.get('graph_count', {}).get('old', 0)} -> {diff.get('graph_count', {}).get('new', 0)}")
    lines.append(f"- Node count: {diff.get('node_count', {}).get('old', 0)} -> {diff.get('node_count', {}).get('new', 0)}")
    lines.append(f"- Behavior-relevant changes: {len(diff.get('likely_behavior_changes', []))}")
    lines.append(f"- Unknown changes: {len(diff.get('unknown_changes', []))}")
    lines.append("")
    for note in diff.get("likely_behavior_changes", [])[:80]:
        lines.append(f"- {note}")
    if not diff.get("likely_behavior_changes"):
        lines.append("- No parsed behavior-relevant changes detected.")
    return "\n".join(lines) + "\n"


def render_asset_compare_prompt(diff: dict[str, object]) -> str:
    compact = {key: value for key, value in diff.items() if key not in {"old_asset", "new_asset"}}
    return "\n".join(
        [
            "Please review this ARK/Unreal Blueprint asset-level diff.",
            "Focus on behavior changes across graphs, class defaults, components, delegates, macros, missing links, and server-authority flow.",
            "Do not overstate certainty when native C++ or missing Blueprint context is required.",
            "",
            json.dumps(compact, ensure_ascii=False, indent=2)[:60000],
        ]
    ) + "\n"


def render_asset_compare_compact(diff: dict[str, object]) -> str:
    lines = ["Blueprint asset compare compact", ""]
    lines.append(f"graphs: {diff.get('graph_count', {}).get('old', 0)} -> {diff.get('graph_count', {}).get('new', 0)}")
    lines.append(f"nodes: {diff.get('node_count', {}).get('old', 0)} -> {diff.get('node_count', {}).get('new', 0)}")
    lines.append(f"behavior_changes: {len(diff.get('likely_behavior_changes', []))}")
    lines.append(f"unknown_changes: {len(diff.get('unknown_changes', []))}")
    lines.append("")
    lines.extend(f"- {note}" for note in diff.get("likely_behavior_changes", [])[:80])
    return "\n".join(lines) + "\n"


def run_asset_compare(args: argparse.Namespace) -> int:
    keywords = profile_keywords(args.profile, args.keyword)
    try:
        old_asset = load_asset_payload_input(args, args.compare_asset[0], keywords)
        new_asset = load_asset_payload_input(args, args.compare_asset[1], keywords)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    diff = compare_asset_payloads(old_asset, new_asset)
    paths = resolve_output_paths(args, compare=True)
    paths["report"].write_text(render_asset_compare_report(diff), encoding="utf-8")
    paths["compare_summary"].write_text(render_asset_compare_summary(diff), encoding="utf-8")
    paths["behavior_impact_report"].write_text(render_behavior_impact_report(diff), encoding="utf-8")
    paths["prompt"].write_text(render_asset_compare_prompt(diff), encoding="utf-8")
    paths["json"].write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["compact"].write_text(render_asset_compare_compact(diff), encoding="utf-8")
    write_glossary(paths["dir"])
    print(f"Wrote asset compare output directory: {paths['dir']}")
    print(f"- report: {paths['report']}")
    print(f"- summary: {paths['compare_summary']}")
    print(f"- behavior impact: {paths['behavior_impact_report']}")
    print(f"- prompt: {paths['prompt']}")
    print(f"- compare json: {paths['json']}")
    print(f"Compared graphs: {len(diff.get('matched_graphs', []))}")
    print(f"Behavior-relevant changes: {len(diff.get('likely_behavior_changes', []))}")
    return 0


def run_compare(args: argparse.Namespace) -> int:
    keywords = profile_keywords(args.profile, args.keyword)
    old_payload = load_compare_input(args.compare[0], keywords)
    new_payload = load_compare_input(args.compare[1], keywords)
    diff = compare_payloads(old_payload, new_payload)
    paths = resolve_output_paths(args, compare=True)
    paths["report"].write_text(render_compare_report(diff), encoding="utf-8")
    paths["compare_summary"].write_text(render_compare_summary(diff), encoding="utf-8")
    paths["prompt"].write_text(render_compare_prompt(diff), encoding="utf-8")
    paths["json"].write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["compact"].write_text(render_compare_compact(diff), encoding="utf-8")
    print(f"Wrote compare output directory: {paths['dir']}")
    print(f"- report: {paths['report']}")
    print(f"- summary: {paths['compare_summary']}")
    print(f"- prompt: {paths['prompt']}")
    print(f"- compare json: {paths['json']}")
    return 0

