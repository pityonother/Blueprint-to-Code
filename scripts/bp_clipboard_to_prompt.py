#!/usr/bin/env python3
"""ARK DevKit / Unreal Blueprint Translator.

This tool reads copied Blueprint text from the Windows clipboard or a .txt file,
parses nodes/pins/links, reconstructs approximate execution and data flow, and
writes human reports plus AI-ready prompts.

Examples:
  python scripts/bp_clipboard_to_prompt.py
  python scripts/bp_clipboard_to_prompt.py --input graph.txt --asset-name MyBP --graph-name EventGraph
  python scripts/bp_clipboard_to_prompt.py --mode pseudocode --profile dino --output-dir out
  python scripts/bp_clipboard_to_prompt.py --compare old.json new.json --output-dir diff
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


KEYWORD_GROUPS: dict[str, list[str]] = {
    "Unreal common": [
        "BeginPlay",
        "Tick",
        "ConstructionScript",
        "Event",
        "Function",
        "Macro",
        "Branch",
        "Sequence",
        "Timeline",
        "Delay",
        "Timer",
        "Interface",
        "Cast",
        "Replication",
        "Server",
        "Client",
        "Multicast",
        "Authority",
        "Owner",
        "Instigator",
    ],
    "ARK/ASA common": [
        "Primal",
        "Shooter",
        "Dino",
        "Character",
        "Structure",
        "Inventory",
        "Item",
        "Buff",
        "Status",
        "Tribe",
        "Team",
        "Targeting",
        "Tamed",
        "Baby",
        "Mating",
        "Food",
        "Water",
        "Fuel",
        "Crafting",
        "MultiUse",
        "Radial",
        "Stasis",
        "Octree",
    ],
    "Range/detection": [
        "Radius",
        "Range",
        "Sphere",
        "Collision",
        "Overlap",
        "Trace",
        "Nearby",
        "Register",
        "Unregister",
        "Refresh",
        "Query",
        "Container",
    ],
    "Network/save": [
        "RepNotify",
        "SaveGame",
        "Net",
        "Dormancy",
        "Stasis",
        "DedicatedServer",
    ],
}

PROFILE_CONFIG: dict[str, dict[str, object]] = {
    "unreal": {
        "groups": ["Unreal common", "Network/save"],
        "extra": ["Blueprint", "Kismet", "Actor", "Component", "WorldContextObject"],
        "focus": "General Unreal Blueprint behavior, control flow, data dependencies, and engine semantics.",
    },
    "ark": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["BlueprintGeneratedClass", "ShooterGame", "PrimalGameData"],
        "focus": "General ARK/ASA DevKit gameplay logic, Blueprint inheritance, inventory, networking, and stasis behavior.",
    },
    "structure": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["PrimalStructure", "Structure", "Foundation", "Placement", "Snap", "Demolish", "Power", "Fuel"],
        "focus": "Structure runtime behavior, placement, inventory, fuel, power, radial menu, and range checks.",
    },
    "dino": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["PrimalDinoCharacter", "Dino", "Tamed", "Baby", "Mating", "TargetingTeam", "AIController"],
        "focus": "Dino character behavior, inventory, baby/mating state, targeting, AI, and server-side runtime logic.",
    },
    "inventory": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["PrimalInventory", "InventoryRefresh", "ItemQuantity", "Crafting", "RemoteInventory", "DefaultInventoryItems"],
        "focus": "Inventory refresh, item transfer, crafting, default inventory, and UI/runtime inventory state.",
    },
    "buff": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["PrimalBuff", "Buff", "StatusComponent", "AddBuff", "RemoveBuff", "BuffTick", "DamageType"],
        "focus": "Buff lifecycle, status modifiers, timers, server authority, and replication side effects.",
    },
    "ui": {
        "groups": ["Unreal common", "ARK/ASA common"],
        "extra": ["Widget", "HUD", "UI", "InventoryUI", "Button", "Text", "Canvas", "OnClicked"],
        "focus": "UI/widget behavior, user interaction, inventory views, and client-only presentation logic.",
    },
    "networking": {
        "groups": ["Unreal common", "Network/save", "ARK/ASA common"],
        "extra": ["Authority", "Role", "RemoteRole", "Replicated", "RPC", "Server", "Client", "Multicast", "Dormancy"],
        "focus": "Server/client authority, RPCs, replication, dormancy, save-game state, and multiplayer correctness.",
    },
    "feeding": {
        "groups": list(KEYWORD_GROUPS),
        "extra": [
            "Feeding",
            "FeedingTrough",
            "Trough",
            "Maewing",
            "FoodContainer",
            "DinoFoodContainer",
            "BabyFood",
            "TamedDino",
            "NearbyDino",
            "FeedRadius",
        ],
        "focus": "Food delivery, feeding range, visual radius, inventory containers, baby/tamed dino checks, and stasis/octree registration.",
    },
}

NODE_SEMANTICS: dict[str, str] = {
    "K2Node_CallFunction": "Calls a Blueprint or native function. Exec pins control when it runs; input pins are parameters; output pins are return values.",
    "K2Node_VariableGet": "Reads a variable and supplies its value to connected data pins.",
    "K2Node_VariableSet": "Writes a variable. The value input should be traced through data flow.",
    "K2Node_Event": "Entry point fired by an engine or Blueprint event.",
    "K2Node_CustomEvent": "User-defined event entry point that can be called or bound as a delegate.",
    "K2Node_FunctionEntry": "Function graph entry point.",
    "K2Node_FunctionResult": "Function return node.",
    "K2Node_IfThenElse": "Branch node. The Condition input controls then/else exec outputs.",
    "K2Node_ExecutionSequence": "Runs multiple exec outputs in order.",
    "K2Node_DynamicCast": "Runtime type check/cast with success and failure execution paths.",
    "K2Node_MacroInstance": "Expands a Blueprint macro such as IsValid, ForEachLoop, DoOnce, Gate, or Delay-like control.",
    "K2Node_Timeline": "Timeline update/finished flow for time-based curves.",
    "K2Node_SpawnActorFromClass": "Spawns an actor instance at runtime.",
    "K2Node_ConstructObjectFromClass": "Constructs a UObject instance at runtime.",
    "K2Node_CreateDelegate": "Creates a delegate binding.",
    "K2Node_ComponentBoundEvent": "Entry point fired by a component delegate/event.",
    "K2Node_AddComponent": "Adds or creates an actor component.",
    "K2Node_SwitchEnum": "Exec switch based on enum value.",
    "K2Node_SwitchName": "Exec switch based on Name value.",
    "K2Node_SwitchString": "Exec switch based on String value.",
    "K2Node_Select": "Selects a value from multiple data inputs based on an index/condition.",
    "K2Node_Knot": "Reroute node used for graph layout; it forwards exec or data.",
    "K2Node_PromotableOperator": "Math or comparison operator that may be pure data-flow logic.",
    "K2Node_CommutativeAssociativeBinaryOperator": "Math/boolean operator combining multiple inputs.",
}

ARK_GLOSSARY: dict[str, str] = {
    "PrimalItem": "ARK item Blueprint/class used for inventory items, consumables, resources, structures, and crafted objects.",
    "PrimalInventory": "Inventory component/class that owns item lists, crafting, remote inventory access, and refresh behavior.",
    "PrimalInventoryComponent": "Runtime component that stores and manages items for characters, structures, and containers.",
    "PrimalStructure": "Base class family for placeable structures, including ownership, placement, multi-use, inventory, and network state.",
    "PrimalDinoCharacter": "Base dino character class with tame, baby, mating, AI, targeting, food, and status behavior.",
    "ShooterCharacter": "Player character class family used by ARK/ShooterGame.",
    "Buff": "Status effect object that can apply timed or persistent behavior to characters, dinos, items, or structures.",
    "StatusComponent": "Component that stores character/dino status values such as health, stamina, food, water, and torpor.",
    "MultiUse": "ARK radial menu / interaction system for context actions on actors and structures.",
    "Tribe": "Player group ownership and permission system.",
    "TargetingTeam": "Team/faction identifier used for targeting, AI, tribe ownership, and friendly/hostile checks.",
    "Stasis": "ARK optimization state where actors outside active relevance may be paused or reduced in simulation.",
    "Octree": "Spatial indexing structure commonly used for fast nearby/range queries.",
    "Replication": "Unreal network synchronization from server to clients.",
    "NetDormancy": "Unreal networking mode that reduces replication frequency for actors with stable state.",
}

CONTEXT_TEMPLATE = """# Blueprint Translator Sidecar Context

Asset name:
Parent class:
Interfaces:
Tags:

## Components
- Component name:
  - Class:
  - Purpose:

## Class Defaults
- Replication:
- Inventory:
- Stasis:
- Octree:
- Radius:
- Range:
- Food:
- Buff:
- MultiUse:

## Test Observations
- What was observed in PIE / editor / live game:
- What changed after edits:
- Remaining uncertainty:
"""

NOISE_FIELDS = {"NodePosX", "NodePosY", "NodeWidth", "NodeHeight", "ErrorType", "ErrorMsg"}

GUID_RE = re.compile(r"\b[0-9A-Fa-f]{32}\b")
CLASS_RE = re.compile(r"\bClass=(?:\"(?P<quoted>[^\"]+)\"|(?P<bare>\S+))")
NAME_RE = re.compile(r"\bName=\"(?P<name>[^\"]+)\"")
EXPORT_RE = re.compile(r"\bExportPath=\"(?P<path>[^\"]+)\"")
MEMBER_NAME_RE = re.compile(r"\bMemberName=\"(?P<name>[^\"]+)\"")
VAR_MEMBER_RE = re.compile(r"VariableReference=.*?\bMemberName=\"(?P<name>[^\"]+)\"")
CUSTOM_FUNCTION_RE = re.compile(r"\bCustomFunctionName=\"(?P<name>[^\"]+)\"")
NODE_GUID_RE = re.compile(r"\bNodeGuid=(?P<guid>[A-Za-z0-9_]+)")
GRAPH_GUID_RE = re.compile(r"\bGraphGuid=(?P<guid>[A-Za-z0-9_]+)")
PIN_ID_RE = re.compile(r"\bPinId=(?P<id>[A-Za-z0-9_]+)")
PIN_NAME_RE = re.compile(r"\bPinName=\"(?P<name>[^\"]*)\"")
PIN_CATEGORY_RE = re.compile(r"\bPinType\.PinCategory=\"(?P<category>[^\"]*)\"")
PIN_SUBCATEGORY_RE = re.compile(r"\bPinType\.PinSubCategory=\"(?P<subcategory>[^\"]*)\"")
DIRECTION_RE = re.compile(r"\bDirection=\"?(?P<direction>EGPD_[A-Za-z]+)\"?")
DEFAULT_VALUE_RE = re.compile(r"\bDefault(?:Value|Object|TextValue)=(?P<value>\"[^\"]*\"|[^,\)]+)")
LINKED_TO_RE = re.compile(r"\bLinkedTo=\((?P<linked>.*?)\)")
PERSISTENT_GUID_RE = re.compile(r"\bPersistentGuid=(?P<guid>[A-Za-z0-9_]+)")
COMMENT_RE = re.compile(r"\b(?:NodeComment|CommentText)=\"(?P<comment>[^\"]*)\"")
MACRO_RE = re.compile(r"\bMacroGraph=\"[^\"]*:(?P<macro>[^'\"]+)")


@dataclass
class PinInfo:
    id: str = ""
    name: str = ""
    direction: str = "EGPD_Input"
    category: str = ""
    subcategory: str = ""
    default: str = ""
    persistent_guid: str = ""
    linked_to_raw: str = ""
    links: list[dict[str, str]] = field(default_factory=list)


@dataclass
class NodeInfo:
    index: int
    class_name: str = ""
    node_type: str = ""
    name: str = ""
    export_path: str = ""
    node_guid: str = ""
    graph_guid: str = ""
    function: str = ""
    variable: str = ""
    event: str = ""
    macro: str = ""
    comment: str = ""
    pins: list[PinInfo] = field(default_factory=list)
    keyword_hits: Counter = field(default_factory=Counter)
    raw: str = ""

    @property
    def link_count(self) -> int:
        return sum(len(pin.links) for pin in self.pins)

    @property
    def label(self) -> str:
        for value in (self.function, self.variable, self.event, self.macro, self.comment, self.name, self.node_type):
            if value:
                return value
        return f"Node {self.index}"


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def read_clipboard() -> str:
    try:
        import tkinter as tk
    except Exception as exc:  # pragma: no cover - depends on local Python install
        raise RuntimeError("tkinter is not available; pass --input instead.") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    finally:
        root.destroy()


def read_text(input_path: str | None) -> tuple[str, str]:
    if input_path:
        path = Path(os.path.expandvars(input_path)).expanduser()
        return path.read_text(encoding="utf-8-sig", errors="replace"), str(path)
    return read_clipboard().lstrip("\ufeff"), "Windows clipboard"


def read_optional_text(path_text: str | None) -> str:
    if not path_text:
        return ""
    path = Path(os.path.expandvars(path_text)).expanduser()
    return path.read_text(encoding="utf-8-sig", errors="replace")


def split_csvish(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]


def profile_keywords(profile: str, extra_keywords: Iterable[str] = ()) -> list[str]:
    config = PROFILE_CONFIG.get(profile, PROFILE_CONFIG["ark"])
    keywords: list[str] = []
    for group in config.get("groups", []):
        keywords.extend(KEYWORD_GROUPS.get(str(group), []))
    keywords.extend(str(item) for item in config.get("extra", []))
    keywords.extend(extra_keywords)
    return list(dict.fromkeys(keyword for keyword in keywords if keyword))


def split_node_blocks(text: str) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] = []
    depth = 0
    for line in text.splitlines():
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith("Begin Object"):
            if depth == 0:
                current = []
            depth += 1
        if depth > 0:
            current.append(line)
        if stripped == "End Object" and depth > 0:
            depth -= 1
            if depth == 0 and current:
                blocks.append(current)
                current = []
    if not blocks and text.strip():
        return [text]
    return ["\n".join(block) for block in blocks]


def first_match(pattern: re.Pattern[str], text: str, group: str = "name") -> str:
    match = pattern.search(text)
    return match.group(group).strip() if match else ""


def node_type_from_class(class_name: str) -> str:
    if not class_name:
        return ""
    tail = class_name.rsplit(".", 1)[-1]
    return tail.rsplit("/", 1)[-1]


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def parse_links(value: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for item in re.finditer(r"([A-Za-z0-9_]+)\s+([A-Za-z0-9_]+)", value):
        links.append({"target_node": item.group(1), "target_pin_id": item.group(2)})
    if not links and value.strip():
        links.append({"target_node": value.strip(), "target_pin_id": ""})
    return links


def parse_pin(line: str) -> PinInfo:
    default_match = DEFAULT_VALUE_RE.search(line)
    linked_match = LINKED_TO_RE.search(line)
    linked_raw = linked_match.group("linked") if linked_match else ""
    return PinInfo(
        id=first_match(PIN_ID_RE, line, "id"),
        name=first_match(PIN_NAME_RE, line),
        direction=first_match(DIRECTION_RE, line, "direction") or "EGPD_Input",
        category=first_match(PIN_CATEGORY_RE, line, "category"),
        subcategory=first_match(PIN_SUBCATEGORY_RE, line, "subcategory"),
        default=strip_quotes(default_match.group("value")) if default_match else "",
        persistent_guid=first_match(PERSISTENT_GUID_RE, line, "guid"),
        linked_to_raw=linked_raw,
        links=parse_links(linked_raw) if linked_raw else [],
    )


def keyword_counter(text: str, keywords: Iterable[str]) -> Counter:
    lowered = text.lower()
    hits: Counter = Counter()
    for keyword in keywords:
        count = lowered.count(keyword.lower())
        if count:
            hits[keyword] = count
    return hits


def parse_node(block: str, index: int, keywords: Iterable[str]) -> NodeInfo:
    class_match = CLASS_RE.search(block)
    class_name = ""
    if class_match:
        class_name = (class_match.group("quoted") or class_match.group("bare") or "").strip()
    node_type = node_type_from_class(class_name)

    function = first_match(MEMBER_NAME_RE, block) if "FunctionReference=" in block else ""
    variable = first_match(VAR_MEMBER_RE, block) if "VariableReference=" in block else ""
    event = first_match(MEMBER_NAME_RE, block) if "EventReference=" in block else ""
    if not event and "K2Node_CustomEvent" in block:
        event = first_match(CUSTOM_FUNCTION_RE, block)

    pins = [parse_pin(line) for line in block.splitlines() if "CustomProperties Pin" in line]
    return NodeInfo(
        index=index,
        class_name=class_name,
        node_type=node_type,
        name=first_match(NAME_RE, block),
        export_path=first_match(EXPORT_RE, block, "path"),
        node_guid=first_match(NODE_GUID_RE, block, "guid"),
        graph_guid=first_match(GRAPH_GUID_RE, block, "guid"),
        function=function,
        variable=variable,
        event=event,
        macro=first_match(MACRO_RE, block, "macro") if "MacroGraphReference=" in block else "",
        comment=first_match(COMMENT_RE, block, "comment"),
        pins=pins,
        keyword_hits=keyword_counter(block, keywords),
        raw=block,
    )


def remove_noise_line(line: str) -> bool:
    stripped = line.strip()
    for field_name in NOISE_FIELDS:
        if stripped.startswith(f"{field_name}="):
            return True
    return False


def normalize_guids(text: str, keep_guids: bool = False) -> str:
    if keep_guids:
        return text
    mapping: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        value = match.group(0).upper()
        if value not in mapping:
            mapping[value] = f"GUID_{len(mapping) + 1:04d}"
        return mapping[value]

    return GUID_RE.sub(replace, text)


def clean_blueprint_text(text: str, keep_guids: bool = False) -> str:
    kept_lines = [line for line in text.splitlines() if not remove_noise_line(line)]
    return normalize_guids("\n".join(kept_lines).strip(), keep_guids=keep_guids)


def is_exec_pin(pin: PinInfo) -> bool:
    return pin.category == "exec"


def is_output_pin(pin: PinInfo) -> bool:
    return pin.direction == "EGPD_Output"


def is_input_pin(pin: PinInfo) -> bool:
    return pin.direction != "EGPD_Output"


def table_row(values: Iterable[object]) -> str:
    escaped = []
    for value in values:
        text = str(value).replace("|", "\\|").replace("\n", " ").strip()
        escaped.append(text if text else "-")
    return "| " + " | ".join(escaped) + " |"


def truncate_lines(text: str, max_lines: int) -> tuple[str, int]:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def node_key(node: NodeInfo | dict[str, object]) -> str:
    if isinstance(node, NodeInfo):
        guid = node.node_guid
        parts = [node.node_type, node.function, node.variable, node.event, node.macro, node.name]
    else:
        guid = str(node.get("node_guid", ""))
        parts = [
            str(node.get("node_type", "")),
            str(node.get("function", "")),
            str(node.get("variable", "")),
            str(node.get("event", "")),
            str(node.get("macro", "")),
            str(node.get("name", "")),
        ]
    if guid:
        return f"guid:{guid}"
    return "sig:" + " | ".join(part for part in parts if part)


def label_for(node: NodeInfo | dict[str, object]) -> str:
    if isinstance(node, NodeInfo):
        return node.label
    for field in ("function", "variable", "event", "macro", "comment", "name", "node_type"):
        value = str(node.get(field, ""))
        if value:
            return value
    return "Node"


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
    if "ifthenelse" in text or "branch" in text:
        return "branch"
    if "executionsequence" in text or "sequence" in text:
        return "sequence"
    if "foreach" in text or "loop" in text:
        return "loop"
    if "doonce" in text:
        return "doonce"
    if "gate" in text:
        return "gate"
    if "delay" in text:
        return "delay"
    if "timer" in text:
        return "timer"
    if "switch" in text:
        return "switch"
    if "functionresult" in text or "return" in text:
        return "return"
    if "dynamiccast" in text:
        return "cast"
    return "call" if node.function else "node"


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
        "macro": node.macro,
    }


def pin_to_dict(pin: PinInfo) -> dict[str, object]:
    return {
        "id": pin.id,
        "name": pin.name,
        "direction": pin.direction,
        "category": pin.category,
        "subcategory": pin.subcategory,
        "default": pin.default,
        "persistent_guid": pin.persistent_guid,
        "linked_to_raw": pin.linked_to_raw,
        "links": pin.links,
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
        "function": node.function,
        "variable": node.variable,
        "event": node.event,
        "macro": node.macro,
        "comment": node.comment,
        "control_kind": control_kind(node),
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
                    }
                )
    return links


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
        "orphan_nodes": orphan_nodes,
        "missing_entry_points": missing_entry_points,
        "pins_with_unknown_source": pins_unknown,
        "assumptions": assumptions,
        "warnings": warnings,
    }


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
    exec_flow = build_exec_flow(nodes)
    data_flow = build_data_flow(nodes)
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
            "raw_characters": len(text),
            "cleaned_characters": len(cleaned),
            "node_count": len(nodes),
            "pin_count": len(flat_pins),
            "link_count": len(all_links(nodes)),
        },
        "context": context or {},
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
        "macros": [node_to_dict(node) for node in nodes if node.macro or "Macro" in node.node_type],
        "comments": [node_to_dict(node) for node in nodes if node.comment or "Comment" in node.node_type],
        "exec_flow": exec_flow,
        "data_flow": data_flow,
    }
    payload["diagnostics"] = diagnostics_for(nodes, exec_flow, data_flow)
    return cleaned, nodes, payload


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
        elif kind in {"loop", "doonce", "gate", "delay", "timer", "switch", "return", "cast"}:
            lines.append(f"{prefix}: {kind.title()} {node.label}")
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
            lines.append(table_row(["Node", "Pin", "Source", "Default"]))
            lines.append(table_row(["---", "---", "---", "---"]))
            for item in values:
                lines.append(table_row([item.get("node_label"), item.get("pin"), item.get("source"), item.get("default")]))
        else:
            lines.append("- none")
        lines.append("")
    return "\n".join(lines)


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
        if node.event or node.function or "VariableSet" in node.node_type or node.macro:
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
    edges = [edge for edge in outgoing.get(node.name, []) if str(edge.get("source_pin", "")).lower() in names]
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


def context_from_args(args: argparse.Namespace) -> dict[str, object]:
    return {
        "parent_class": args.parent_class or "",
        "interfaces": split_csvish(args.interfaces),
        "tags": split_csvish(args.tags),
        "defaults_text": read_optional_text(args.defaults_file),
        "components_text": read_optional_text(args.components_file),
        "notes_text": read_optional_text(args.notes_file),
    }


def render_context_section(context: dict[str, object]) -> str:
    if not any(context.values()):
        return ""
    lines = ["## Sidecar Context", ""]
    for key in ("parent_class", "interfaces", "tags"):
        value = context.get(key)
        if value:
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    for title, key in [("Components", "components_text"), ("Class Defaults", "defaults_text"), ("Notes", "notes_text")]:
        text = str(context.get(key, "")).strip()
        if text:
            lines.append("")
            lines.append(f"### {title}")
            lines.append("")
            lines.append("```text")
            lines.append(text[:8000])
            lines.append("```")
    lines.append("")
    return "\n".join(lines)


def render_diagnostics(payload: dict[str, object]) -> str:
    diagnostics = payload["diagnostics"]
    lines = ["## Confidence And Uncertainty", ""]
    lines.append(f"- confidence_level: {diagnostics['confidence_level']}")
    for key in ("unsupported_node_types", "unresolved_links", "orphan_nodes", "missing_entry_points", "pins_with_unknown_source", "assumptions", "warnings"):
        value = diagnostics.get(key)
        lines.append(f"- {key}:")
        if isinstance(value, list):
            if value:
                for item in value[:30]:
                    lines.append(f"  - {item}")
            else:
                lines.append("  - none")
        else:
            lines.append(f"  - {value}")
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
    for item in payload["data_flow"].get("set_values", [])[:20]:
        lines.append(f"- Set {item.get('node_label')}.{item.get('pin')}: value <- {item.get('source')}")
    for item in payload["data_flow"].get("call_parameters", [])[:30]:
        lines.append(f"- Call {item.get('node_label')}.{item.get('pin')}: param <- {item.get('source')}")
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


def default_output_dir(prefix: str = "blueprint_translation") -> Path:
    return Path.home() / "Desktop" / f"{prefix}_{now_stamp()}"


def resolve_output_paths(args: argparse.Namespace, compare: bool = False) -> dict[str, Path]:
    if args.output_dir:
        out_dir = Path(os.path.expandvars(args.output_dir)).expanduser()
    elif args.output:
        out_dir = Path(os.path.expandvars(args.output)).expanduser().parent
    else:
        out_dir = default_output_dir("blueprint_compare" if compare else "blueprint_translation")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_name = "compare_report.md" if compare else "report.md"
    report = Path(os.path.expandvars(args.output)).expanduser() if args.output and not compare else out_dir / report_name
    return {
        "dir": out_dir,
        "report": report,
        "prompt": out_dir / ("compare_prompt.md" if compare else "prompt.md"),
        "json": out_dir / ("compare.json" if compare else "parsed.json"),
        "compact": out_dir / "compact.txt",
        "exec_flow": out_dir / "exec_flow.md",
        "data_flow": out_dir / "data_flow.md",
        "pseudocode": out_dir / "pseudocode.md",
        "cpp": out_dir / "cpp_reference.md",
        "compare_summary": out_dir / "compare_summary.md",
    }


def write_glossary(out_dir: Path) -> None:
    (out_dir / "ark_glossary.json").write_text(json.dumps(ARK_GLOSSARY, ensure_ascii=False, indent=2), encoding="utf-8")


def maybe_write_context_template(args: argparse.Namespace, out_dir: Path) -> bool:
    if not args.make_context_template:
        return False
    target = out_dir / "context_template.md"
    target.write_text(CONTEXT_TEMPLATE, encoding="utf-8")
    print(f"Wrote context template: {target}")
    return True


def chunk_nodes(nodes: list[NodeInfo], payload: dict[str, object], chunk_by: str, max_chars: int) -> list[list[NodeInfo]]:
    if not nodes:
        return []
    if chunk_by == "exec-root":
        ordered = ordered_nodes_by_exec(nodes, payload["exec_flow"])
        return split_node_groups_by_chars([ordered], max_chars)
    if chunk_by == "connected-component":
        groups = connected_components(nodes)
        return split_node_groups_by_chars(groups, max_chars)
    groups: list[list[NodeInfo]] = []
    current: list[NodeInfo] = []
    current_chars = 0
    for node in nodes:
        if current and current_chars + len(node.raw) > max_chars:
            groups.append(current)
            current = []
            current_chars = 0
        current.append(node)
        current_chars += len(node.raw)
    if current:
        groups.append(current)
    return groups


def connected_components(nodes: list[NodeInfo]) -> list[list[NodeInfo]]:
    by_name = {node.name: node for node in nodes}
    graph: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        graph[node.name]
        for pin in node.pins:
            for link in pin.links:
                target = link.get("target_node", "")
                if target in by_name:
                    graph[node.name].add(target)
                    graph[target].add(node.name)
    groups: list[list[NodeInfo]] = []
    seen: set[str] = set()
    for node in nodes:
        if node.name in seen:
            continue
        queue = deque([node.name])
        seen.add(node.name)
        names: list[str] = []
        while queue:
            name = queue.popleft()
            names.append(name)
            for next_name in graph[name]:
                if next_name not in seen:
                    seen.add(next_name)
                    queue.append(next_name)
        groups.append([by_name[name] for name in names if name in by_name])
    return groups


def split_node_groups_by_chars(groups: list[list[NodeInfo]], max_chars: int) -> list[list[NodeInfo]]:
    chunks: list[list[NodeInfo]] = []
    for group in groups:
        current: list[NodeInfo] = []
        current_chars = 0
        for node in group:
            if current and current_chars + len(node.raw) > max_chars:
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(node)
            current_chars += len(node.raw)
        if current:
            chunks.append(current)
    return chunks


def write_chunks(args: argparse.Namespace, out_dir: Path, nodes: list[NodeInfo], payload: dict[str, object], keywords: list[str], asset_name: str, graph_name: str, profile: str, provider: str) -> None:
    chunks = chunk_nodes(nodes, payload, args.chunk_by, max(args.max_chars, 1000))
    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    index_lines = ["# Blueprint Chunks", "", f"- Chunk by: {args.chunk_by}", f"- Max chars: {args.max_chars}", f"- Total chunks: {len(chunks)}", ""]
    for i, group in enumerate(chunks, start=1):
        raw = "\n".join(node.raw for node in group)
        cleaned, group_nodes, group_payload = parse_blueprint_text(text=raw, source=f"chunk {i}", asset_name=asset_name, graph_name=graph_name, keywords=keywords, keep_guids=args.keep_guids, include_raw=args.include_raw, context=payload.get("context", {}))
        base = f"chunk_{i:03d}"
        report_path = chunks_dir / f"{base}_report.md"
        prompt_path = chunks_dir / f"{base}_prompt.md"
        report_path.write_text(render_report(mode="summary", source=f"chunk {i}", raw_text=raw, cleaned_text=cleaned, nodes=group_nodes, payload=group_payload, keywords=keywords, asset_name=asset_name, graph_name=graph_name, ask=args.ask or "", profile=profile, provider=provider, max_cleaned_lines=args.max_cleaned_lines), encoding="utf-8")
        prompt_path.write_text(render_prompt_file(group_nodes, group_payload, keywords, cleaned, asset_name, graph_name, args.ask or "", profile, provider, args.max_cleaned_lines), encoding="utf-8")
        index_lines.append(f"- [{base}_report.md]({base}_report.md): {len(group)} nodes")
    (chunks_dir / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


def run_translate(args: argparse.Namespace) -> int:
    paths = resolve_output_paths(args)
    if maybe_write_context_template(args, paths["dir"]) and not args.input and not clipboard_has_text_request(args):
        return 0
    keywords = profile_keywords(args.profile, args.keyword)
    raw_text, source = read_text(args.input)
    if not raw_text.strip():
        print("No Blueprint text found. Copy nodes in Unreal with Ctrl+C or pass --input.", file=sys.stderr)
        return 2
    context = context_from_args(args)
    cleaned, nodes, payload = parse_blueprint_text(text=raw_text, source=source, asset_name=args.asset_name or "", graph_name=args.graph_name or "", keywords=keywords, keep_guids=args.keep_guids, include_raw=args.include_raw, context=context)
    paths["report"].write_text(render_report(mode=args.mode, source=source, raw_text=raw_text, cleaned_text=cleaned, nodes=nodes, payload=payload, keywords=keywords, asset_name=args.asset_name or "", graph_name=args.graph_name or "", ask=args.ask or "", profile=args.profile, provider=args.provider, max_cleaned_lines=max(args.max_cleaned_lines, 50)), encoding="utf-8")
    paths["prompt"].write_text(render_prompt_file(nodes, payload, keywords, cleaned, args.asset_name or "", args.graph_name or "", args.ask or "", args.profile, args.provider, max(args.max_cleaned_lines, 50)), encoding="utf-8")
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["compact"].write_text(render_compact(payload, nodes, payload["data_flow"], args.asset_name or "", args.graph_name or "", args.ask or ""), encoding="utf-8")
    paths["exec_flow"].write_text(render_exec_flow(nodes, payload["exec_flow"], payload["data_flow"]), encoding="utf-8")
    paths["data_flow"].write_text(render_data_flow(payload["data_flow"]), encoding="utf-8")
    paths["pseudocode"].write_text(render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"]), encoding="utf-8")
    paths["cpp"].write_text(render_cpp_reference(nodes, payload["exec_flow"], payload["data_flow"], args.asset_name or "", args.graph_name or ""), encoding="utf-8")
    write_glossary(paths["dir"])
    if args.chunk:
        write_chunks(args, paths["dir"], nodes, payload, keywords, args.asset_name or "", args.graph_name or "", args.profile, args.provider)
    if args.provider != "none":
        print(f"Provider '{args.provider}' is reserved. No model call was made; prompt.md was generated.")
    print(f"Wrote output directory: {paths['dir']}")
    for label in ("report", "prompt", "json", "compact", "exec_flow", "data_flow", "pseudocode", "cpp"):
        print(f"- {label}: {paths[label]}")
    print(f"Parsed nodes: {len(nodes)}")
    print(f"Confidence: {payload['diagnostics']['confidence_level']}")
    return 0


def clipboard_has_text_request(args: argparse.Namespace) -> bool:
    return bool(args.input or args.ask or args.asset_name or args.graph_name)


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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate ARK DevKit / Unreal Blueprint clipboard text into reports, flow graphs, prompts, JSON, and diffs.")
    parser.add_argument("--input", "-i", help="Optional .txt file. If omitted, read Windows clipboard.")
    parser.add_argument("--output", "-o", help="Legacy report.md output path. Other files go beside it.")
    parser.add_argument("--output-dir", help="Directory for generated files.")
    parser.add_argument("--asset-name", help="Blueprint asset name/path label.")
    parser.add_argument("--graph-name", help="Graph/function/event graph label.")
    parser.add_argument("--ask", help="Question to place at the top of prompt.md.")
    parser.add_argument("--mode", choices=["summary", "pseudocode", "cpp", "prompt", "all"], default="all")
    parser.add_argument("--json", action="store_true", help="Accepted for compatibility; parsed.json is always written.")
    parser.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"), help="Compare two parsed JSON files or two Blueprint .txt files.")
    parser.add_argument("--keyword", action="append", default=[], help="Extra keyword to search for. Can be passed multiple times.")
    parser.add_argument("--keep-guids", action="store_true", help="Keep raw GUIDs in cleaned Blueprint text.")
    parser.add_argument("--include-raw", action="store_true", help="Include raw node text inside parsed.json.")
    parser.add_argument("--max-cleaned-lines", type=int, default=900, help="Maximum cleaned Blueprint lines in report/prompt.")
    parser.add_argument("--chunk", action="store_true", help="Write chunked reports/prompts under chunks/.")
    parser.add_argument("--max-chars", type=int, default=20000, help="Maximum raw node characters per chunk.")
    parser.add_argument("--chunk-by", choices=["exec-root", "connected-component", "comment"], default="exec-root")
    parser.add_argument("--defaults-file", help="Optional sidecar file with class defaults.")
    parser.add_argument("--components-file", help="Optional sidecar file with component list/details.")
    parser.add_argument("--notes-file", help="Optional sidecar notes or test observations.")
    parser.add_argument("--parent-class", help="Parent class context.")
    parser.add_argument("--interfaces", help="Comma/semicolon separated interface names.")
    parser.add_argument("--tags", help="Comma/semicolon separated tags.")
    parser.add_argument("--make-context-template", action="store_true", help="Write context_template.md to the output directory.")
    parser.add_argument("--profile", choices=sorted(PROFILE_CONFIG), default="ark")
    parser.add_argument("--provider", choices=["none", "ollama", "lmstudio", "openai", "anthropic"], default="none")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.compare:
        return run_compare(args)
    return run_translate(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
