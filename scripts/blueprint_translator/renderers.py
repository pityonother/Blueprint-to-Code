"""Markdown, prompt, pseudocode, and C++ reference renderers."""

from __future__ import annotations

import json
import re
import datetime as _dt
from collections import defaultdict
from typing import Iterable

from .config import ARK_GLOSSARY, KEYWORD_GROUPS, NODE_SEMANTICS, PROFILE_CONFIG
from .context import render_context_section
from .diagnostics import render_diagnostics
from .flow import control_kind, exec_pin_sort_key, ordered_nodes_by_exec, source_expression_for_pin
from .models import NodeInfo
from .utils import is_exec_pin, is_output_pin, label_for, table_row, truncate_lines

def collect_keyword_contexts(text: str, keywords: Iterable[str], limit: int = 80) -> list[tuple[int, str, str]]:
    contexts: list[tuple[int, str, str]] = []
    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        lowered = line.lower()
        for keyword in keywords:
            if keyword.lower() in lowered:
                contexts.append((line_no, keyword, line.strip()))
                break
        if len(contexts) >= limit:
            break
    return contexts


def summarize_execution(nodes: list[NodeInfo], data_flow: dict[str, object] | None = None, limit: int = 140) -> list[str]:
    branch_sources = {item["node"]: item["source"] for item in (data_flow or {}).get("branch_conditions", [])}
    set_sources = {(item["node"], item["pin"]): item["source"] for item in (data_flow or {}).get("set_values", [])}
    nodes_by_name = {node.name: node for node in nodes if node.name}
    lines: list[str] = []
    for node in nodes[:limit]:
        prefix = f"{node.index:03d}"
        kind = control_kind(node)
        if node.event:
            lines.append(f"{prefix}: Event {node.event}")
        elif node.node_type == "K2Node_FunctionEntry":
            lines.append(f"{prefix}: Function entry {node.label}")
        elif kind == "branch":
            lines.append(f"{prefix}: Branch if {branch_sources.get(node.name, '<condition>')}")
        elif kind == "sequence":
            outputs = [pin.name for pin in node.pins if is_exec_pin(pin) and is_output_pin(pin)]
            lines.append(f"{prefix}: Sequence outputs {', '.join(outputs) if outputs else '<unknown>'}")
        elif node.node_type == "K2Node_MacroInstance":
            lines.append(f"{prefix}: Macro {node.macro or node.label} ({macro_kind(node)})")
        elif kind in {"loop", "doonce", "gate", "delay", "timer", "switch", "return", "cast"}:
            lines.append(f"{prefix}: {kind.title()} {node.label}")
        elif kind == "delegate":
            lines.append(f"{prefix}: Delegate {delegate_binding_summary(node, nodes_by_name)}")
        elif kind == "authority":
            outputs = [pin.name for pin in node.pins if is_exec_pin(pin) and is_output_pin(pin)]
            lines.append(f"{prefix}: Authority/server check {node.label} outputs {', '.join(outputs) if outputs else '<unknown>'}")
        elif node.function:
            params = function_param_summary(node, data_flow or {})
            lines.append(f"{prefix}: Call {node.function}({params})")
        elif "VariableSet" in node.node_type:
            value = "<value>"
            for (node_name, _pin), source in set_sources.items():
                if node_name == node.name:
                    value = source
                    break
            lines.append(f"{prefix}: Set {node.variable or node.label} = {value}")
        elif "VariableGet" in node.node_type:
            lines.append(f"{prefix}: Get {node.variable or node.label}")
        elif node.macro:
            lines.append(f"{prefix}: Macro {node.macro}")
        elif node.comment:
            lines.append(f"{prefix}: Comment {node.comment}")
        else:
            lines.append(f"{prefix}: {node.label} [{node.node_type or node.class_name}]")
    return lines


def function_param_summary(node: NodeInfo, data_flow: dict[str, object]) -> str:
    params = []
    for item in data_flow.get("call_parameters", []):
        if item.get("node") == node.name:
            params.append(f"{item.get('pin')}={item.get('source')}")
    return ", ".join(params[:6])


def delegate_binding_summary(node: NodeInfo, nodes_by_name: dict[str, NodeInfo]) -> str:
    delegate_name = node.delegate or node.label
    handlers: list[str] = []
    for pin in node.pins:
        if pin.name.lower() not in {"delegate", "event"}:
            continue
        for link in pin.links:
            target = nodes_by_name.get(link.get("target_node", ""))
            if target:
                handlers.append(target.event or target.delegate or target.label)
            elif link.get("target_node"):
                handlers.append(str(link.get("target_node")))
    handler_text = ", ".join(dict.fromkeys(handlers)) if handlers else "<missing handler>"
    action = {
        "K2Node_AddDelegate": "bind",
        "K2Node_CreateDelegate": "create delegate",
        "K2Node_RemoveDelegate": "unbind",
        "K2Node_ClearDelegate": "clear delegate",
    }.get(node.node_type, "delegate")
    return f"{action} {delegate_name} -> {handler_text}"


def normalized_pin_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def macro_kind(node: NodeInfo) -> str:
    text = normalized_pin_name(f"{node.macro} {node.label} {node.name}")
    if "foreach" in text:
        return "foreach"
    if "isvalid" in text:
        return "isvalid"
    if "doonce" in text:
        return "doonce"
    if "gate" in text:
        return "gate"
    if "delay" in text:
        return "delay"
    if "timer" in text:
        return "timer"
    return "macro"


def macro_input_source(node: NodeInfo, data_flow: dict[str, object], pin_names: set[str], fallback: str = "<value>") -> str:
    wanted = {normalized_pin_name(name) for name in pin_names}
    for item in data_flow.get("dependencies", []):
        if item.get("node") != node.name:
            continue
        if normalized_pin_name(item.get("pin", "")) in wanted:
            return str(item.get("source") or fallback)
    return fallback


def macro_output_label(node: NodeInfo, pin_names: set[str], fallback: str) -> str:
    wanted = {normalized_pin_name(name) for name in pin_names}
    for pin in node.pins:
        if is_exec_pin(pin) or not is_output_pin(pin):
            continue
        if normalized_pin_name(pin.name) in wanted:
            return pin.name
    return fallback


def exec_edges_for_pin_names(node: NodeInfo, outgoing: dict[str, list[dict[str, object]]], names: set[str]) -> list[dict[str, object]]:
    wanted = {normalized_pin_name(name) for name in names}
    return [edge for edge in outgoing.get(node.name, []) if normalized_pin_name(edge.get("source_pin", "")) in wanted]


def render_exec_flow(nodes: list[NodeInfo], exec_flow: dict[str, object], data_flow: dict[str, object]) -> str:
    ordered = ordered_nodes_by_exec(nodes, exec_flow)
    lines = ["# Execution Flow", ""]
    roots = exec_flow.get("roots", [])
    lines.append("## Entry Points")
    lines.append("")
    if roots:
        for root in roots:
            lines.append(f"- #{root.get('index')} {root.get('label')} ({root.get('node_type')})")
    else:
        lines.append("- none detected")
    lines.append("")
    lines.append("## Ordered Flow")
    lines.append("")
    lines.append("```text")
    lines.extend(summarize_execution(ordered, data_flow))
    lines.append("```")
    lines.append("")
    lines.append("## Exec Edges")
    lines.append("")
    lines.append(table_row(["From", "Pin", "To", "Target Pin"]))
    lines.append(table_row(["---", "---", "---", "---"]))
    for edge in exec_flow.get("edges", []):
        lines.append(table_row([edge.get("source_label"), edge.get("source_pin"), edge.get("target_label") or edge.get("target_node"), edge.get("target_pin") or edge.get("target_pin_id")]))
    lines.append("")
    return "\n".join(lines)


def render_data_flow(data_flow: dict[str, object]) -> str:
    lines = ["# Data Flow", ""]
    for title, key in [
        ("Branch Conditions", "branch_conditions"),
        ("Set Variable Sources", "set_values"),
        ("Call Function Parameters", "call_parameters"),
        ("All Data Dependencies", "dependencies"),
    ]:
        lines.append(f"## {title}")
        lines.append("")
        values = data_flow.get(key, [])
        if values:
            lines.append(table_row(["Node", "Pin", "Source", "Default", "Class Default Refs", "Component Refs"]))
            lines.append(table_row(["---", "---", "---", "---", "---", "---"]))
            for item in values:
                refs = format_default_refs(item.get("class_default_refs", []))
                component_refs = format_component_refs(item.get("component_refs", []))
                lines.append(table_row([item.get("node_label"), item.get("pin"), item.get("source"), item.get("default"), refs, component_refs]))
        else:
            lines.append("- none")
        lines.append("")
    return "\n".join(lines)


def format_default_refs(refs: object) -> str:
    if not isinstance(refs, list):
        return ""
    return ", ".join(f"{ref.get('name')}={ref.get('value')}" for ref in refs if isinstance(ref, dict))


def format_component_refs(refs: object) -> str:
    if not isinstance(refs, list):
        return ""
    parts: list[str] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        name = str(ref.get("name") or "component")
        class_name = str(ref.get("class") or "")
        prop = str(ref.get("property") or "")
        if prop:
            parts.append(f"{name}.{prop}={ref.get('value')}")
        elif class_name:
            parts.append(f"{name}({class_name})")
        else:
            parts.append(name)
    return ", ".join(parts)


def render_pseudocode(nodes: list[NodeInfo], exec_flow: dict[str, object], data_flow: dict[str, object]) -> str:
    lines = ["# Pseudocode", "", "```text"]
    nodes_by_name = {node.name: node for node in nodes if node.name}
    outgoing: dict[str, list[dict[str, object]]] = defaultdict(list)
    for edge in exec_flow.get("edges", []):
        outgoing[str(edge.get("source_node", ""))].append(edge)
    roots = [nodes_by_name[root["name"]] for root in exec_flow.get("roots", []) if root.get("name") in nodes_by_name]
    visited_edges: set[tuple[str, str, str]] = set()
    rendered_nodes: set[str] = set()

    for root in roots:
        render_pseudocode_node(root, nodes_by_name, outgoing, data_flow, lines, indent=0, visited_edges=visited_edges, rendered_nodes=rendered_nodes)

    for node in ordered_nodes_by_exec(nodes, exec_flow):
        if node.name in rendered_nodes or is_pure_data_node(node):
            continue
        if node.event or node.function or "VariableSet" in node.node_type or node.delegate or node.macro:
            render_pseudocode_node(node, nodes_by_name, outgoing, data_flow, lines, indent=0, visited_edges=visited_edges, rendered_nodes=rendered_nodes)
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def render_pseudocode_node(
    node: NodeInfo,
    nodes_by_name: dict[str, NodeInfo],
    outgoing: dict[str, list[dict[str, object]]],
    data_flow: dict[str, object],
    lines: list[str],
    indent: int,
    visited_edges: set[tuple[str, str, str]],
    rendered_nodes: set[str],
) -> None:
    rendered_nodes.add(node.name)
    pad = "  " * indent
    kind = control_kind(node)
    branch_sources = {item["node"]: item["source"] for item in data_flow.get("branch_conditions", [])}
    if node.event:
        lines.append(f"{pad}on {node.event}:")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
    elif node.node_type == "K2Node_FunctionEntry":
        lines.append(f"{pad}function {node.label}():")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
    elif node.node_type == "K2Node_MacroInstance":
        render_macro_instance_node(node, nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)
    elif kind == "branch":
        lines.append(f"{pad}if {branch_sources.get(node.name, '<condition>')}:")
        traverse_named_exec_outputs(node, {"then", "true"}, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        else_edges = [edge for edge in outgoing.get(node.name, []) if str(edge.get("source_pin", "")).lower() in {"else", "false"}]
        if else_edges:
            lines.append(f"{pad}else:")
            traverse_edges(else_edges, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
    elif kind == "sequence":
        lines.append(f"{pad}sequence:")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
    elif kind in {"loop", "doonce", "gate", "delay", "timer", "switch", "return", "cast"}:
        lines.append(f"{pad}{kind} {node.label}")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)
    elif kind == "delegate":
        lines.append(f"{pad}{delegate_binding_summary(node, nodes_by_name)}")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)
    elif kind == "authority":
        lines.append(f"{pad}if running on server/authority:")
        traverse_named_exec_outputs(node, {"yes", "authority", "then", "true"}, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        remote_edges = [edge for edge in outgoing.get(node.name, []) if str(edge.get("source_pin", "")).lower() in {"no", "remote", "client", "false"}]
        if remote_edges:
            lines.append(f"{pad}else:")
            traverse_edges(remote_edges, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
    elif node.function:
        lines.append(f"{pad}{node.function}({function_param_summary(node, data_flow)})")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)
    elif "VariableSet" in node.node_type:
        value = value_source_for_set(node, data_flow)
        lines.append(f"{pad}{node.variable or node.label} = {value}")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)
    elif node.macro:
        lines.append(f"{pad}macro {node.macro}")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)


def render_macro_instance_node(
    node: NodeInfo,
    nodes_by_name: dict[str, NodeInfo],
    outgoing: dict[str, list[dict[str, object]]],
    data_flow: dict[str, object],
    lines: list[str],
    indent: int,
    visited_edges: set[tuple[str, str, str]],
    rendered_nodes: set[str],
) -> None:
    pad = "  " * indent
    kind = macro_kind(node)
    name = node.macro or node.label
    if kind == "foreach":
        array_source = macro_input_source(node, data_flow, {"Array", "TargetArray", "Target Array"}, "<array>")
        element_name = macro_output_label(node, {"Array Element", "Element", "Item"}, "item")
        lines.append(f"{pad}for each {element_name} in {array_source}:")
        traverse_named_exec_outputs(node, {"Loop Body", "LoopBody", "Body"}, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        completed_edges = exec_edges_for_pin_names(node, outgoing, {"Completed", "Done"})
        if completed_edges:
            lines.append(f"{pad}after loop:")
            traverse_edges(completed_edges, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        return
    if kind == "isvalid":
        object_source = macro_input_source(node, data_flow, {"InputObject", "Input Object", "Object", "Target", "self"}, "<object>")
        lines.append(f"{pad}if IsValid({object_source}):")
        traverse_named_exec_outputs(node, {"Is Valid", "Valid", "Then", "True"}, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        invalid_edges = exec_edges_for_pin_names(node, outgoing, {"Is Not Valid", "Not Valid", "Invalid", "False"})
        if invalid_edges:
            lines.append(f"{pad}else:")
            traverse_edges(invalid_edges, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        return
    if kind == "doonce":
        lines.append(f"{pad}do once:")
        traverse_named_exec_outputs(node, {"Completed", "Then", "Exit"}, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        return
    if kind == "gate":
        lines.append(f"{pad}gate {name}:")
        traverse_named_exec_outputs(node, {"Exit", "Then"}, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        return
    if kind == "delay":
        duration = macro_input_source(node, data_flow, {"Duration", "Delay", "Time"}, "<duration>")
        lines.append(f"{pad}delay {duration}:")
        traverse_named_exec_outputs(node, {"Completed", "Then"}, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        return
    if kind == "timer":
        duration = macro_input_source(node, data_flow, {"Time", "Rate", "Duration"}, "<time>")
        lines.append(f"{pad}timer {name} every {duration}:")
        traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent + 1, visited_edges, rendered_nodes)
        return
    lines.append(f"{pad}macro {name}")
    traverse_exec_outputs(node, nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)


def traverse_exec_outputs(
    node: NodeInfo,
    nodes_by_name: dict[str, NodeInfo],
    outgoing: dict[str, list[dict[str, object]]],
    data_flow: dict[str, object],
    lines: list[str],
    indent: int,
    visited_edges: set[tuple[str, str, str]],
    rendered_nodes: set[str],
) -> None:
    traverse_edges(sorted(outgoing.get(node.name, []), key=exec_pin_sort_key), nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)


def traverse_named_exec_outputs(
    node: NodeInfo,
    names: set[str],
    nodes_by_name: dict[str, NodeInfo],
    outgoing: dict[str, list[dict[str, object]]],
    data_flow: dict[str, object],
    lines: list[str],
    indent: int,
    visited_edges: set[tuple[str, str, str]],
    rendered_nodes: set[str],
) -> None:
    edges = exec_edges_for_pin_names(node, outgoing, names)
    traverse_edges(sorted(edges, key=exec_pin_sort_key), nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)


def traverse_edges(
    edges: list[dict[str, object]],
    nodes_by_name: dict[str, NodeInfo],
    outgoing: dict[str, list[dict[str, object]]],
    data_flow: dict[str, object],
    lines: list[str],
    indent: int,
    visited_edges: set[tuple[str, str, str]],
    rendered_nodes: set[str],
) -> None:
    for edge in edges:
        marker = (str(edge.get("source_node", "")), str(edge.get("source_pin_id", "")), str(edge.get("target_node", "")))
        if marker in visited_edges:
            continue
        visited_edges.add(marker)
        target = nodes_by_name.get(str(edge.get("target_node", "")))
        if target and not is_pure_data_node(target):
            render_pseudocode_node(target, nodes_by_name, outgoing, data_flow, lines, indent, visited_edges, rendered_nodes)
        elif not target and edge.get("target_node"):
            pad = "  " * indent
            lines.append(f"{pad}<missing linked node {edge.get('target_node')} from {edge.get('source_label')}.{edge.get('source_pin')}>")


def is_pure_data_node(node: NodeInfo) -> bool:
    if "VariableGet" in node.node_type:
        return True
    if node.function and not any(is_exec_pin(pin) for pin in node.pins):
        return True
    return False


def value_source_for_set(node: NodeInfo, data_flow: dict[str, object]) -> str:
    for item in data_flow.get("set_values", []):
        if item.get("node") == node.name:
            return str(item.get("source") or "<value>")
    return "<value>"


def sanitize_identifier(value: str) -> str:
    value = re.sub(r"\W+", "_", value.strip()).strip("_")
    if not value:
        return "TranslatedBlueprintGraph"
    if value[0].isdigit():
        value = "_" + value
    return value


def cpp_literal(value: str) -> str:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered
    if re.fullmatch(r"-?\d+(\.\d+)?", value):
        return value
    return json.dumps(value)


def render_cpp_reference(nodes: list[NodeInfo], exec_flow: dict[str, object], data_flow: dict[str, object], asset_name: str, graph_name: str) -> str:
    ordered = ordered_nodes_by_exec(nodes, exec_flow)
    function_name = sanitize_identifier(graph_name or asset_name or "TranslatedBlueprintGraph")
    branch_sources = {item["node"]: item["source"] for item in data_flow.get("branch_conditions", [])}
    set_sources = {(item["node"], item["pin"]): item["source"] for item in data_flow.get("set_values", [])}
    lines = [
        "# C++ Style Reference",
        "",
        "```cpp",
        "// Reference-only pseudocode generated from copied Blueprint nodes.",
        "// It is not expected to compile without ARK/Unreal type adaptation.",
        f"void {function_name}()",
        "{",
    ]
    for node in ordered:
        kind = control_kind(node)
        if node.event:
            lines.append(f"    // Event: {node.event}")
        elif kind == "branch":
            lines.append(f"    if ({cpp_comment_expr(branch_sources.get(node.name, 'condition'))})")
            lines.append("    {")
            lines.append("        // then")
            lines.append("    }")
            lines.append("    else")
            lines.append("    {")
            lines.append("        // else")
            lines.append("    }")
        elif kind == "sequence":
            lines.append("    // Sequence")
        elif node.function:
            lines.append(f"    {node.function}(); // params: {function_param_summary(node, data_flow) or 'none visible'}")
        elif "VariableSet" in node.node_type:
            value = "/* value */"
            for (node_name, _pin), source in set_sources.items():
                if node_name == node.name:
                    value = cpp_literal(source) if source and source != "<unknown>" else "/* unknown */"
                    break
            lines.append(f"    {sanitize_identifier(node.variable or node.label)} = {value};")
        elif "VariableGet" in node.node_type:
            lines.append(f"    auto {sanitize_identifier(node.variable or node.label)}Value = {sanitize_identifier(node.variable or node.label)};")
        elif node.macro:
            lines.append(f"    // Macro: {node.macro}")
    lines.append("}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def cpp_comment_expr(expr: str) -> str:
    if not expr or expr == "<unknown>":
        return "/* condition */"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
        return expr
    return f"/* {expr} */"


def render_compact(payload: dict[str, object], nodes: list[NodeInfo], data_flow: dict[str, object], asset_name: str, graph_name: str, ask: str) -> str:
    meta = payload["metadata"]
    totals = payload["keyword_hits"]
    hot_keywords = [f"{key}:{value}" for key, value in totals.items() if value]
    ordered = ordered_nodes_by_exec(nodes, payload["exec_flow"])
    lines = ["ARK DevKit Blueprint Compact Translation"]
    if ask:
        lines.append(f"Question: {ask}")
    lines.append(f"Asset: {asset_name or '<unknown>'}")
    lines.append(f"Graph: {graph_name or '<unknown>'}")
    lines.append(f"Confidence: {payload['diagnostics']['confidence_level']}")
    lines.append(f"Nodes/Pins/Links: {meta['node_count']}/{meta['pin_count']}/{meta['link_count']}")
    lines.append(f"Keyword hits: {', '.join(hot_keywords) if hot_keywords else 'none'}")
    lines.append("")
    lines.append("Execution outline:")
    lines.extend(summarize_execution(ordered, data_flow, limit=100))
    lines.append("")
    lines.append("Important functions:")
    functions = [node.function for node in nodes if node.function]
    lines.append(", ".join(dict.fromkeys(functions)) if functions else "none")
    lines.append("")
    lines.append("Variables:")
    variables = [node.variable for node in nodes if node.variable]
    lines.append(", ".join(dict.fromkeys(variables)) if variables else "none")
    return "\n".join(lines) + "\n"


def render_summary_section(payload: dict[str, object], cleaned_text: str, keywords: list[str]) -> str:
    meta = payload["metadata"]
    diagnostics = payload["diagnostics"]
    lines = ["## Summary", ""]
    for key in ("asset_name", "graph_name", "source", "node_count", "pin_count", "link_count"):
        value = meta.get(key, "")
        if value != "":
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.append(f"- Confidence Level: {diagnostics['confidence_level']}")
    lines.append("")
    if diagnostics.get("warnings"):
        lines.append("## Warnings")
        lines.append("")
        lines.extend(f"- {warning}" for warning in diagnostics["warnings"])
        lines.append("")
    lines.append("## Keyword Hits")
    lines.append("")
    lines.append(table_row(["Group", "Keyword", "Count"]))
    lines.append(table_row(["---", "---", "---"]))
    for group, group_keywords in KEYWORD_GROUPS.items():
        for keyword in group_keywords:
            lines.append(table_row([group, keyword, payload["keyword_hits"].get(keyword, 0)]))
    lines.append("")
    contexts = collect_keyword_contexts(cleaned_text, keywords)
    if contexts:
        lines.append("## Keyword Contexts")
        lines.append("")
        lines.append(table_row(["Line", "Keyword", "Context"]))
        lines.append(table_row(["---", "---", "---"]))
        for line_no, keyword, context in contexts:
            lines.append(table_row([line_no, keyword, context[:240]]))
        lines.append("")
    lines.append("## Important Nodes")
    lines.append("")
    lines.append(table_row(["#", "Label", "Type", "Control", "Pins", "Links", "Keyword hits"]))
    lines.append(table_row(["---", "---", "---", "---", "---", "---", "---"]))
    ranked = sorted(payload["nodes"], key=lambda item: sum(item.get("keyword_hits", {}).values()) * 10 + len(item.get("pins", [])), reverse=True)
    for node in ranked[:100]:
        hits = ", ".join(f"{key}:{value}" for key, value in node.get("keyword_hits", {}).items())
        link_count = sum(len(pin.get("links", [])) for pin in node.get("pins", []))
        lines.append(table_row([node["index"], node["label"], node["node_type"], node["control_kind"], len(node.get("pins", [])), link_count, hits]))
    lines.append("")
    return "\n".join(lines)


def build_ai_prompt(
    *,
    nodes: list[NodeInfo],
    payload: dict[str, object],
    keywords: list[str],
    cleaned_excerpt: str,
    asset_name: str,
    graph_name: str,
    ask: str,
    profile: str,
    provider: str,
) -> str:
    ordered = ordered_nodes_by_exec(nodes, payload["exec_flow"])
    important = sorted(nodes, key=lambda node: sum(node.keyword_hits.values()) * 10 + node.link_count, reverse=True)[:35]
    focus = PROFILE_CONFIG.get(profile, PROFILE_CONFIG["ark"]).get("focus", "")
    lines = [
        "User question:",
        ask.strip() if ask else "Analyze what this ARK DevKit / Unreal Blueprint graph does and identify gameplay-relevant logic.",
        "",
        "Context:",
        f"- Asset: {asset_name or '<unknown>'}",
        f"- Graph: {graph_name or '<unknown>'}",
        f"- Profile: {profile}",
        f"- Profile focus: {focus}",
        f"- Provider mode: {provider} (prompt generation only unless external caller invokes a model)",
        f"- Confidence from parser: {payload['diagnostics']['confidence_level']}",
        "",
        "Important instruction:",
        "- Do not state uncertain behavior as fact. Mark native C++ calls, missing nodes, unresolved links, and inherited/default data as assumptions.",
        "- Explain in Chinese unless exact Blueprint/API names need English.",
        "- Separate gameplay logic, visual/editor-only logic, networking/server logic, inventory logic, save/stasis logic, and data dependencies.",
        "- Use the execution flow first, then use data flow to explain conditions and parameters.",
        "",
        "High-priority nodes:",
    ]
    for node in important:
        hits = ", ".join(node.keyword_hits.keys()) if node.keyword_hits else "-"
        lines.append(f"- #{node.index} {node.label} | type={node.node_type or '-'} | control={control_kind(node)} | pins={len(node.pins)} | links={node.link_count} | hits={hits}")
    lines.extend(["", "Execution outline:"])
    lines.extend(summarize_execution(ordered, payload["data_flow"], limit=120))
    lines.extend(["", "Data flow highlights:"])
    for item in payload["data_flow"].get("branch_conditions", [])[:20]:
        lines.append(f"- Branch {item.get('node_label')}: condition <- {item.get('source')}")
        refs = format_default_refs(item.get("class_default_refs", []))
        if refs:
            lines.append(f"  class defaults: {refs}")
        component_refs = format_component_refs(item.get("component_refs", []))
        if component_refs:
            lines.append(f"  component defaults: {component_refs}")
    for item in payload["data_flow"].get("set_values", [])[:20]:
        lines.append(f"- Set {item.get('node_label')}.{item.get('pin')}: value <- {item.get('source')}")
        refs = format_default_refs(item.get("class_default_refs", []))
        if refs:
            lines.append(f"  class defaults: {refs}")
        component_refs = format_component_refs(item.get("component_refs", []))
        if component_refs:
            lines.append(f"  component defaults: {component_refs}")
    for item in payload["data_flow"].get("call_parameters", [])[:30]:
        lines.append(f"- Call {item.get('node_label')}.{item.get('pin')}: param <- {item.get('source')}")
        refs = format_default_refs(item.get("class_default_refs", []))
        if refs:
            lines.append(f"  class defaults: {refs}")
        component_refs = format_component_refs(item.get("component_refs", []))
        if component_refs:
            lines.append(f"  component defaults: {component_refs}")
    lines.extend(["", "Keyword groups:"])
    for group, group_keywords in KEYWORD_GROUPS.items():
        lines.append(f"- {group}: {', '.join(group_keywords)}")
    lines.extend(["", "Relevant glossary terms:"])
    for term, meaning in ARK_GLOSSARY.items():
        if any(term.lower() in node.raw.lower() for node in nodes) or term in {"Stasis", "Octree", "Replication", "NetDormancy"}:
            lines.append(f"- {term}: {meaning}")
    sidecar = render_context_section(payload.get("context", {}))
    if sidecar:
        lines.extend(["", sidecar])
    lines.extend(["", "Cleaned Blueprint text excerpt:", "```text", cleaned_excerpt, "```"])
    return "\n".join(lines)


def render_report(
    *,
    mode: str,
    source: str,
    raw_text: str,
    cleaned_text: str,
    nodes: list[NodeInfo],
    payload: dict[str, object],
    keywords: list[str],
    asset_name: str,
    graph_name: str,
    ask: str,
    profile: str,
    provider: str,
    max_cleaned_lines: int,
) -> str:
    cleaned_excerpt, omitted_lines = truncate_lines(cleaned_text, max_cleaned_lines)
    lines = [
        "# ARK DevKit Blueprint Translator Report",
        "",
        f"- Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Mode: {mode}",
        f"- Profile: {profile}",
        f"- Provider: {provider}",
        f"- Source: {source}",
    ]
    if asset_name:
        lines.append(f"- Asset: {asset_name}")
    if graph_name:
        lines.append(f"- Graph: {graph_name}")
    if ask:
        lines.append(f"- User question: {ask}")
    lines.extend([f"- Raw characters: {len(raw_text)}", f"- Cleaned characters: {len(cleaned_text)}", ""])
    lines.append(render_context_section(payload.get("context", {})))
    if mode in {"summary", "all"}:
        lines.append(render_summary_section(payload, cleaned_text, keywords))
    if mode in {"pseudocode", "all"}:
        lines.append(render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"]))
    if mode in {"cpp", "all"}:
        lines.append(render_cpp_reference(nodes, payload["exec_flow"], payload["data_flow"], asset_name, graph_name))
    if mode in {"prompt", "all"}:
        prompt = build_ai_prompt(nodes=nodes, payload=payload, keywords=keywords, cleaned_excerpt=cleaned_excerpt, asset_name=asset_name, graph_name=graph_name, ask=ask, profile=profile, provider=provider)
        lines.extend(["## AI Analysis Prompt", "", "```text", prompt, "```", ""])
    lines.append(render_diagnostics(payload))
    lines.extend(["## Cleaned Blueprint Text", "", "```text", cleaned_excerpt])
    if omitted_lines:
        lines.append(f"... omitted {omitted_lines} additional cleaned lines ...")
    lines.extend(["```", ""])
    return "\n".join(part for part in lines if part is not None)


def render_prompt_file(nodes: list[NodeInfo], payload: dict[str, object], keywords: list[str], cleaned_text: str, asset_name: str, graph_name: str, ask: str, profile: str, provider: str, max_cleaned_lines: int) -> str:
    cleaned_excerpt, omitted_lines = truncate_lines(cleaned_text, max_cleaned_lines)
    prompt = build_ai_prompt(nodes=nodes, payload=payload, keywords=keywords, cleaned_excerpt=cleaned_excerpt, asset_name=asset_name, graph_name=graph_name, ask=ask, profile=profile, provider=provider)
    if omitted_lines:
        prompt += f"\n\nNote: {omitted_lines} additional cleaned Blueprint lines were omitted from this prompt."
    return prompt + "\n"
