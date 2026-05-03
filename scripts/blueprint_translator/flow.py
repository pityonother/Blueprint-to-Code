"""Execution-flow, data-flow, and low-level diagnostics helpers."""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque

from .config import NODE_SEMANTICS
from .models import NodeInfo, PinInfo
from .utils import is_exec_pin, is_input_pin, is_output_pin, label_for, node_key

def build_node_indices(nodes: list[NodeInfo]) -> tuple[dict[str, NodeInfo], dict[str, tuple[NodeInfo, PinInfo]]]:
    nodes_by_name = {node.name: node for node in nodes if node.name}
    pins_by_id: dict[str, tuple[NodeInfo, PinInfo]] = {}
    for node in nodes:
        for pin in node.pins:
            if pin.id:
                pins_by_id[pin.id] = (node, pin)
    return nodes_by_name, pins_by_id


def control_kind(node: NodeInfo) -> str:
    text = " ".join([node.node_type, node.function, node.macro, node.label]).lower()
    if node.event or node.node_type in {"K2Node_FunctionEntry", "K2Node_ComponentBoundEvent"}:
        return "entry"
    if node.node_type in {"K2Node_AddDelegate", "K2Node_CreateDelegate", "K2Node_RemoveDelegate", "K2Node_ClearDelegate"}:
        return "delegate"
    if node.node_type == "K2Node_SwitchAuthority" or node.function.lower() in {"isrunningonserver", "hasauthority", "switchhasauthority"}:
        return "authority"
    if "ifthenelse" in text or "branch" in text:
        return "branch"
    if "executionsequence" in text or "sequence" in text:
        return "sequence"
    if node.function:
        return "call"
    if node.node_type == "K2Node_MacroInstance":
        macro_text = " ".join([node.macro, node.label]).lower()
        if "foreach" in macro_text or "loop" in macro_text:
            return "loop"
        if "doonce" in macro_text:
            return "doonce"
        if "gate" in macro_text:
            return "gate"
        if "delay" in macro_text:
            return "delay"
        if "timer" in macro_text:
            return "timer"
    if "switch" in text:
        return "switch"
    if "functionresult" in text or "return" in text:
        return "return"
    if "dynamiccast" in text:
        return "cast"
    return "node"


def missing_target_kind(target_node: str) -> str:
    lowered = target_node.lower()
    if "macroinstance" in lowered:
        return "macro"
    if "knot" in lowered:
        return "reroute"
    if "executionsequence" in lowered:
        return "sequence"
    if "callfunction" in lowered:
        return "function"
    if "customevent" in lowered or "_event" in lowered:
        return "event"
    if "delegate" in lowered:
        return "delegate"
    if "variableget" in lowered or "variableset" in lowered:
        return "variable"
    return "node"


def missing_target_copy_hint(target_node: str, target_kind: str) -> str:
    if target_kind == "macro":
        return f"Copy the missing macro instance node {target_node}; if it wraps a macro graph, add that macro graph capture too."
    if target_kind == "sequence":
        return f"Copy {target_node} and every exec output branch after it so BeginPlay/entry flow can be followed."
    if target_kind == "reroute":
        return f"Copy the reroute node {target_node} plus the upstream/downstream data nodes connected through it."
    if target_kind == "function":
        return f"Copy the missing function call node {target_node}; if it calls a Blueprint function, add that function graph to --asset-dir."
    return f"Find and copy the missing Blueprint node named {target_node}, or copy the full graph page that contains it."


def build_exec_flow(nodes: list[NodeInfo]) -> dict[str, object]:
    nodes_by_name, pins_by_id = build_node_indices(nodes)
    incoming: dict[str, list[dict[str, object]]] = defaultdict(list)
    outgoing: dict[str, list[dict[str, object]]] = defaultdict(list)
    edges: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []

    for node in nodes:
        for pin in node.pins:
            if not (is_exec_pin(pin) and is_output_pin(pin)):
                continue
            for link in pin.links:
                target_node = nodes_by_name.get(link.get("target_node", ""))
                target_pin = pins_by_id.get(link.get("target_pin_id", ""), (None, None))[1]
                edge = {
                    "source_node": node.name,
                    "source_index": node.index,
                    "source_label": node.label,
                    "source_pin": pin.name,
                    "source_pin_id": pin.id,
                    "target_node": link.get("target_node", ""),
                    "target_pin_id": link.get("target_pin_id", ""),
                    "target_index": target_node.index if target_node else None,
                    "target_label": target_node.label if target_node else "",
                    "target_pin": target_pin.name if target_pin else "",
                }
                edges.append(edge)
                outgoing[node.name].append(edge)
                if target_node:
                    incoming[target_node.name].append(edge)
                else:
                    unresolved.append(edge)

    roots = [
        node
        for node in nodes
        if node.event
        or node.node_type in {"K2Node_FunctionEntry", "K2Node_ComponentBoundEvent"}
        or (node.name in outgoing and node.name not in incoming)
    ]
    seen_roots: dict[str, NodeInfo] = {}
    for root in roots:
        seen_roots[root.name or str(root.index)] = root
    roots = list(seen_roots.values())

    ordered: list[NodeInfo] = []
    visited: set[str] = set()
    for root in roots:
        walk_exec(root, nodes_by_name, outgoing, visited, ordered)
    for node in nodes:
        if node.name not in visited:
            ordered.append(node)
            visited.add(node.name)

    return {
        "roots": [node_to_small_dict(node) for node in roots],
        "edges": edges,
        "unresolved_exec_links": unresolved,
        "ordered_node_names": [node.name for node in ordered],
        "ordered_node_indices": [node.index for node in ordered],
    }


def exec_pin_sort_key(edge: dict[str, object]) -> tuple[int, str]:
    pin = str(edge.get("source_pin", "")).lower()
    if pin in {"then", "true"}:
        return (0, pin)
    if pin in {"else", "false"}:
        return (1, pin)
    match = re.search(r"(\d+)$", pin)
    if match:
        return (int(match.group(1)), pin)
    return (50, pin)


def walk_exec(
    node: NodeInfo,
    nodes_by_name: dict[str, NodeInfo],
    outgoing: dict[str, list[dict[str, object]]],
    visited: set[str],
    ordered: list[NodeInfo],
) -> None:
    if not node.name or node.name in visited:
        return
    visited.add(node.name)
    ordered.append(node)
    for edge in sorted(outgoing.get(node.name, []), key=exec_pin_sort_key):
        target = nodes_by_name.get(str(edge.get("target_node", "")))
        if target:
            walk_exec(target, nodes_by_name, outgoing, visited, ordered)


def ordered_nodes_by_exec(nodes: list[NodeInfo], exec_flow: dict[str, object]) -> list[NodeInfo]:
    by_name = {node.name: node for node in nodes}
    ordered: list[NodeInfo] = []
    seen: set[str] = set()
    for name in exec_flow.get("ordered_node_names", []):
        node = by_name.get(str(name))
        if node and node.name not in seen:
            ordered.append(node)
            seen.add(node.name)
    for node in nodes:
        if node.name not in seen:
            ordered.append(node)
    return ordered


def expression_for_node(node: NodeInfo, source_pin: PinInfo | None = None, depth: int = 0) -> str:
    if depth > 4:
        return f"{node.label}(...)"
    if "VariableGet" in node.node_type:
        return node.variable or node.label
    if "VariableSet" in node.node_type:
        return node.variable or node.label
    if "Knot" in node.node_type:
        return node.label
    if "Select" in node.node_type:
        return f"Select({node.name})"
    if node.function:
        return expression_for_function(node)
    if node.macro:
        return f"{node.macro}(...)"
    if node.delegate:
        return f"delegate {node.delegate}"
    if node.event:
        return f"event {node.event}"
    return node.label


def expression_for_node_resolved(
    node: NodeInfo,
    source_pin: PinInfo | None,
    nodes_by_name: dict[str, NodeInfo],
    pins_by_id: dict[str, tuple[NodeInfo, PinInfo]],
    depth: int = 0,
    seen: set[tuple[str, str]] | None = None,
) -> str:
    if depth > 8:
        return f"{node.label}(...)"
    seen = seen or set()
    pin_id = source_pin.id if source_pin else ""
    marker = (node.name, pin_id)
    if marker in seen:
        return node.label
    seen.add(marker)
    if "VariableGet" in node.node_type:
        return node.variable or node.label
    if "VariableSet" in node.node_type:
        return node.variable or node.label
    if "Knot" in node.node_type:
        input_pin = next((pin for pin in node.pins if is_input_pin(pin) and not is_exec_pin(pin)), None)
        if input_pin:
            source, _ = source_expression_for_pin(node, input_pin, nodes_by_name, pins_by_id, depth + 1, seen)
            return source
        return node.label
    if node.function:
        return expression_for_function_resolved(node, nodes_by_name, pins_by_id, depth + 1, seen)
    if node.macro:
        return f"{node.macro}(...)"
    if node.delegate:
        return f"delegate {node.delegate}"
    if node.event:
        return f"event {node.event}"
    return expression_for_node(node, source_pin, depth)


def expression_for_function(node: NodeInfo) -> str:
    name = node.function
    input_names = [pin.name for pin in node.pins if not is_exec_pin(pin) and is_input_pin(pin) and pin.name not in {"self", "WorldContextObject"}]
    args = ", ".join(input_names[:4])
    if node.node_type == "K2Node_PromotableOperator":
        if name.startswith("Subtract") and len(input_names) >= 2:
            return f"({input_names[0]} - {input_names[1]})"
        if name.startswith("Add") and len(input_names) >= 2:
            return f"({input_names[0]} + {input_names[1]})"
        if name.startswith("Multiply") and len(input_names) >= 2:
            return f"({input_names[0]} * {input_names[1]})"
        if name.startswith("Divide") and len(input_names) >= 2:
            return f"({input_names[0]} / {input_names[1]})"
        if any(token in name for token in ("Greater", "Less", "Equal", "NotEqual")):
            return f"{name}({args})"
    if name.startswith("Make"):
        return f"{name}({args})"
    if name.startswith("Break"):
        return f"{name}({args})"
    return f"{name}({args})" if args else f"{name}()"


def expression_for_function_resolved(
    node: NodeInfo,
    nodes_by_name: dict[str, NodeInfo],
    pins_by_id: dict[str, tuple[NodeInfo, PinInfo]],
    depth: int,
    seen: set[tuple[str, str]],
) -> str:
    name = node.function
    arg_items: list[tuple[str, str]] = []
    for pin in node.pins:
        if is_exec_pin(pin) or is_output_pin(pin) or pin.name in {"self", "WorldContextObject"}:
            continue
        source, _ = source_expression_for_pin(node, pin, nodes_by_name, pins_by_id, depth + 1, seen)
        arg_items.append((pin.name, source))
    arg_values = [value for _, value in arg_items]
    if node.node_type in {"K2Node_PromotableOperator", "K2Node_CommutativeAssociativeBinaryOperator"}:
        symbol = operator_symbol(name)
        if symbol and len(arg_values) >= 2:
            return f"{arg_values[0]} {symbol} {arg_values[1]}"
    if name.startswith("Make"):
        return f"{name}({', '.join(arg_values)})"
    if name.startswith("Break"):
        return f"{name}({', '.join(arg_values)})"
    return f"{name}({', '.join(arg_values)})" if arg_values else f"{name}()"


def operator_symbol(name: str) -> str:
    lowered = name.lower()
    if "greater" in lowered or lowered.startswith(">"):
        return ">"
    if "less" in lowered or lowered.startswith("<"):
        return "<"
    if "notequal" in lowered or "not_equal" in lowered:
        return "!="
    if "equal" in lowered:
        return "=="
    if "subtract" in lowered:
        return "-"
    if "add" in lowered:
        return "+"
    if "multiply" in lowered:
        return "*"
    if "divide" in lowered:
        return "/"
    if "booleanand" in lowered or lowered.endswith("and"):
        return "&&"
    if "booleanor" in lowered or lowered.endswith("or"):
        return "||"
    return ""


def source_expression_for_pin(
    node: NodeInfo,
    pin: PinInfo,
    nodes_by_name: dict[str, NodeInfo],
    pins_by_id: dict[str, tuple[NodeInfo, PinInfo]],
    depth: int = 0,
    seen: set[tuple[str, str]] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    sources: list[str] = []
    unknown: list[dict[str, str]] = []
    seen = seen or set()
    for link in pin.links:
        target_node = nodes_by_name.get(link.get("target_node", ""))
        target_pair = pins_by_id.get(link.get("target_pin_id", ""))
        target_pin = target_pair[1] if target_pair else None
        if target_node:
            sources.append(expression_for_node_resolved(target_node, target_pin, nodes_by_name, pins_by_id, depth + 1, seen))
        else:
            unknown.append({"node": node.name, "pin": pin.name, "target_node": link.get("target_node", ""), "target_pin_id": link.get("target_pin_id", "")})
    if sources:
        return " | ".join(dict.fromkeys(sources)), unknown
    if pin.default:
        return pin.default, unknown
    return "<unknown>", unknown


def build_data_flow(nodes: list[NodeInfo]) -> dict[str, object]:
    nodes_by_name, pins_by_id = build_node_indices(nodes)
    dependencies: list[dict[str, object]] = []
    branch_conditions: list[dict[str, object]] = []
    set_values: list[dict[str, object]] = []
    call_parameters: list[dict[str, object]] = []
    unresolved: list[dict[str, str]] = []
    pins_with_unknown_source: list[dict[str, str]] = []

    for node in nodes:
        for pin in node.pins:
            if is_exec_pin(pin) or is_output_pin(pin):
                continue
            source, unknown = source_expression_for_pin(node, pin, nodes_by_name, pins_by_id)
            unresolved.extend(unknown)
            dep = {
                "node": node.name,
                "node_index": node.index,
                "node_label": node.label,
                "node_type": node.node_type,
                "pin": pin.name,
                "pin_id": pin.id,
                "default": pin.default,
                "source": source,
                "linked_to": pin.links,
            }
            dependencies.append(dep)
            if source == "<unknown>" and pin.name not in {"self", "WorldContextObject"}:
                pins_with_unknown_source.append({"node": node.name, "node_label": node.label, "pin": pin.name, "pin_id": pin.id})
            if node.node_type == "K2Node_IfThenElse" and pin.name == "Condition":
                branch_conditions.append(dep)
            if "VariableSet" in node.node_type and pin.name not in {"execute", "then", "self"}:
                set_values.append(dep)
            if node.function and pin.name not in {"execute", "then", "self", "WorldContextObject"}:
                call_parameters.append(dep)

    return {
        "dependencies": dependencies,
        "branch_conditions": branch_conditions,
        "set_values": set_values,
        "call_parameters": call_parameters,
        "unresolved_data_links": unresolved,
        "pins_with_unknown_source": pins_with_unknown_source,
    }


def keyword_totals(nodes: list[NodeInfo]) -> Counter:
    totals: Counter = Counter()
    for node in nodes:
        totals.update(node.keyword_hits)
    return totals


def node_to_small_dict(node: NodeInfo) -> dict[str, object]:
    return {
        "index": node.index,
        "name": node.name,
        "node_guid": node.node_guid,
        "label": node.label,
        "node_type": node.node_type,
        "function": node.function,
        "variable": node.variable,
        "event": node.event,
        "delegate": node.delegate,
        "macro": node.macro,
    }


def pin_to_dict(pin: PinInfo) -> dict[str, object]:
    return {
        "id": pin.id,
        "name": pin.name,
        "direction": pin.direction,
        "category": pin.category,
        "subcategory": pin.subcategory,
        "pin_type": pin.pin_type,
        "default": pin.default,
        "default_object": pin.default_object,
        "persistent_guid": pin.persistent_guid,
        "linked_to_raw": pin.linked_to_raw,
        "links": pin.links,
        "source": pin.source,
        "confidence": pin.confidence,
        "warnings": pin.warnings,
        "raw_offsets": pin.raw_offsets,
        "resolution": pin.resolution,
    }


def node_to_dict(node: NodeInfo, include_raw: bool = False) -> dict[str, object]:
    data: dict[str, object] = {
        "index": node.index,
        "key": node_key(node),
        "label": node.label,
        "name": node.name,
        "class_name": node.class_name,
        "node_type": node.node_type,
        "semantic": NODE_SEMANTICS.get(node.node_type, ""),
        "export_path": node.export_path,
        "node_guid": node.node_guid,
        "graph_guid": node.graph_guid,
        "x": node.node_pos_x,
        "y": node.node_pos_y,
        "function": node.function,
        "variable": node.variable,
        "event": node.event,
        "delegate": node.delegate,
        "macro": node.macro,
        "comment": node.comment,
        "control_kind": control_kind(node),
        "properties": node.properties,
        "uasset_semantic": node.semantic,
        "source": node.source,
        "confidence": node.confidence,
        "warnings": node.warnings,
        "raw_offsets": node.raw_offsets,
        "pins": [pin_to_dict(pin) for pin in node.pins],
        "keyword_hits": dict(node.keyword_hits),
    }
    if include_raw:
        data["raw"] = node.raw
    return data


def all_links(nodes: list[NodeInfo]) -> list[dict[str, object]]:
    links: list[dict[str, object]] = []
    for node in nodes:
        for pin in node.pins:
            for link in pin.links:
                links.append(
                    {
                        "source_node_index": node.index,
                        "source_node": node.name,
                        "source_node_guid": node.node_guid,
                        "source_label": node.label,
                        "source_pin_id": pin.id,
                        "source_pin": pin.name,
                        "source_pin_category": pin.category,
                        "source_pin_direction": pin.direction,
                        "target_node": link.get("target_node", ""),
                        "target_pin_id": link.get("target_pin_id", ""),
                        "target_package_index": link.get("target_package_index", ""),
                        "target_node_guid": link.get("target_node_guid", ""),
                        "link_source": link.get("source", ""),
                        "link_confidence": link.get("confidence", ""),
                        "resolution_status": link.get("resolution_status", ""),
                    }
                )
    return links


def build_missing_link_map(nodes: list[NodeInfo], exec_flow: dict[str, object], data_flow: dict[str, object]) -> list[dict[str, object]]:
    nodes_by_name = {node.name: node for node in nodes if node.name}
    by_target: dict[str, dict[str, object]] = {}

    def add_reference(link_type: str, item: dict[str, object]) -> None:
        target_node = str(item.get("target_node", "")).strip()
        if not target_node:
            return
        target_kind = missing_target_kind(target_node)
        bucket = by_target.setdefault(
            target_node,
            {
                "target_node": target_node,
                "target_kind": target_kind,
                "target_pin_ids": [],
                "link_types": [],
                "references": [],
                "impact": [],
                "copy_hint": missing_target_copy_hint(target_node, target_kind),
            },
        )
        target_pin_id = str(item.get("target_pin_id", "")).strip()
        if target_pin_id and target_pin_id not in bucket["target_pin_ids"]:
            bucket["target_pin_ids"].append(target_pin_id)
        if link_type not in bucket["link_types"]:
            bucket["link_types"].append(link_type)

        source_name = str(item.get("source_node") or item.get("node") or "")
        source = nodes_by_name.get(source_name)
        source_label = str(item.get("source_label") or item.get("node_label") or (source.label if source else source_name))
        source_pin = str(item.get("source_pin") or item.get("pin") or "")
        if link_type == "exec" and target_kind == "macro":
            impact = f"Execution enters missing macro {target_node} from {source_label}.{source_pin}"
        elif link_type == "exec" and target_kind == "sequence":
            impact = f"Execution sequence is missing after {source_label}.{source_pin}"
        elif link_type == "data" and target_kind == "reroute":
            impact = f"Data reroute is missing for {source_label}.{source_pin}"
        elif link_type == "exec":
            impact = f"Execution cannot be followed from {source_label}.{source_pin}"
        else:
            impact = f"Data source is unknown for {source_label}.{source_pin}"
        if impact not in bucket["impact"]:
            bucket["impact"].append(impact)
        bucket["references"].append(
            {
                "link_type": link_type,
                "source_node": source_name,
                "source_label": source_label,
                "source_pin": source_pin,
                "target_pin_id": target_pin_id,
                "impact": impact,
            }
        )

    for item in exec_flow.get("unresolved_exec_links", []):
        if isinstance(item, dict):
            add_reference("exec", item)
    for item in data_flow.get("unresolved_data_links", []):
        if isinstance(item, dict):
            add_reference("data", item)

    result = list(by_target.values())
    result.sort(key=lambda item: (str(item.get("target_node", "")), str(item.get("link_types", ""))))
    return result


def diagnostics_for(nodes: list[NodeInfo], exec_flow: dict[str, object], data_flow: dict[str, object]) -> dict[str, object]:
    supported = set(NODE_SEMANTICS)
    unsupported = sorted({node.node_type for node in nodes if node.node_type and node.node_type not in supported})
    link_counts: Counter = Counter()
    for link in all_links(nodes):
        link_counts[str(link["source_node"])] += 1
        link_counts[str(link["target_node"])] += 1
    orphan_nodes = [node_to_small_dict(node) for node in nodes if node.name and link_counts[node.name] == 0 and not node.event]
    missing_entry_points = not exec_flow.get("roots")
    unresolved_links = list(exec_flow.get("unresolved_exec_links", [])) + list(data_flow.get("unresolved_data_links", []))
    missing_link_map = build_missing_link_map(nodes, exec_flow, data_flow)
    pins_unknown = list(data_flow.get("pins_with_unknown_source", []))

    warnings: list[str] = []
    assumptions: list[str] = [
        "Blueprint clipboard text does not include all class defaults, components, inherited graph logic, or native C++ function bodies.",
        "Execution flow is reconstructed from exec pins only; latent actions, delegates, timers, and macro internals may need manual confirmation.",
        "Data flow expressions are best-effort summaries of pin links and defaults, not a complete Blueprint compiler.",
    ]
    if unsupported:
        warnings.append("Some node types are not in the built-in semantic dictionary.")
    if missing_entry_points:
        warnings.append("No clear Event / Custom Event / Function Entry node was found.")
    if unresolved_links:
        warnings.append("Some LinkedTo targets were not present in the copied selection.")
    if pins_unknown:
        warnings.append("Some input pins have no visible default or source in the copied selection.")

    penalty = len(unsupported) + len(unresolved_links) + len(pins_unknown)
    if missing_entry_points:
        confidence = "low"
    elif penalty == 0:
        confidence = "high"
    elif penalty <= max(3, len(nodes) // 3):
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "confidence_level": confidence,
        "unsupported_node_types": unsupported,
        "unresolved_links": unresolved_links,
        "missing_link_map": missing_link_map,
        "orphan_nodes": orphan_nodes,
        "missing_entry_points": missing_entry_points,
        "pins_with_unknown_source": pins_unknown,
        "assumptions": assumptions,
        "warnings": warnings,
    }
