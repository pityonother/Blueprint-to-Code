"""Core Blueprint text translation into structured payloads."""

from __future__ import annotations

import datetime as _dt

from .config import ARK_GLOSSARY, KEYWORD_GROUPS, NODE_SEMANTICS
from .context import (
    apply_component_context_to_data_flow,
    apply_default_context_to_data_flow,
    parse_components_context,
    parse_defaults_context,
)
from .flow import (
    all_links,
    build_data_flow,
    build_exec_flow,
    diagnostics_for,
    keyword_totals,
    node_to_dict,
    pin_to_dict,
)
from .models import NodeInfo
from .parser import clean_blueprint_text, parse_node, split_node_blocks

def build_blueprint_payload_from_nodes(
    *,
    nodes: list[NodeInfo],
    raw_text: str,
    cleaned_text: str,
    text: str,
    source: str,
    asset_name: str,
    graph_name: str,
    keywords: list[str],
    include_raw: bool = False,
    context: dict[str, object] | None = None,
) -> dict[str, object]:
    exec_flow = build_exec_flow(nodes)
    data_flow = build_data_flow(nodes)
    defaults_context = parse_defaults_context(context or {})
    components_context = parse_components_context(context or {})
    data_flow = apply_default_context_to_data_flow(data_flow, defaults_context)
    data_flow = apply_component_context_to_data_flow(data_flow, components_context)
    totals = keyword_totals(nodes)
    flat_pins: list[dict[str, object]] = []
    for node in nodes:
        for pin in node.pins:
            pin_data = pin_to_dict(pin)
            pin_data.update({"node_index": node.index, "node_name": node.name, "node_guid": node.node_guid, "node_label": node.label})
            flat_pins.append(pin_data)
    payload: dict[str, object] = {
        "metadata": {
            "generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "asset_name": asset_name,
            "graph_name": graph_name,
            "raw_characters": len(raw_text),
            "cleaned_characters": len(cleaned_text),
            "node_count": len(nodes),
            "pin_count": len(flat_pins),
            "link_count": len(all_links(nodes)),
        },
        "context": context or {},
        "class_defaults": defaults_context,
        "component_defaults": components_context,
        "profile_keyword_groups": KEYWORD_GROUPS,
        "node_semantics": NODE_SEMANTICS,
        "ark_glossary": ARK_GLOSSARY,
        "keyword_hits": {keyword: totals.get(keyword, 0) for keyword in keywords},
        "nodes": [node_to_dict(node, include_raw=include_raw) for node in nodes],
        "pins": flat_pins,
        "links": all_links(nodes),
        "function_calls": [node_to_dict(node) for node in nodes if node.function],
        "variable_gets": [node_to_dict(node) for node in nodes if "VariableGet" in node.node_type],
        "variable_sets": [node_to_dict(node) for node in nodes if "VariableSet" in node.node_type],
        "events": [node_to_dict(node) for node in nodes if node.event or "Event" in node.node_type or "FunctionEntry" in node.node_type],
        "delegates": [node_to_dict(node) for node in nodes if node.delegate or "Delegate" in node.node_type],
        "macros": [node_to_dict(node) for node in nodes if node.macro or "Macro" in node.node_type],
        "comments": [node_to_dict(node) for node in nodes if node.comment or "Comment" in node.node_type],
        "exec_flow": exec_flow,
        "data_flow": data_flow,
    }
    payload["diagnostics"] = diagnostics_for(nodes, exec_flow, data_flow)
    return payload


def parse_blueprint_text(
    *,
    text: str,
    source: str,
    asset_name: str,
    graph_name: str,
    keywords: list[str],
    keep_guids: bool = False,
    include_raw: bool = False,
    context: dict[str, object] | None = None,
) -> tuple[str, list[NodeInfo], dict[str, object]]:
    cleaned = clean_blueprint_text(text, keep_guids=keep_guids)
    blocks = split_node_blocks(text)
    nodes = [parse_node(block, index + 1, keywords) for index, block in enumerate(blocks)]
    payload = build_blueprint_payload_from_nodes(
        nodes=nodes,
        raw_text=text,
        cleaned_text=cleaned,
        text=text,
        source=source,
        asset_name=asset_name,
        graph_name=graph_name,
        keywords=keywords,
        include_raw=include_raw,
        context=context,
    )
    return cleaned, nodes, payload
