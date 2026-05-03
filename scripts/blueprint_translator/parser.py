"""Parser for copied Unreal Blueprint text exports."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .models import NodeInfo, PinInfo
from .patterns import (
    CLASS_RE,
    COMMENT_RE,
    CUSTOM_FUNCTION_RE,
    DEFAULT_VALUE_RE,
    DELEGATE_MEMBER_RE,
    DIRECTION_RE,
    EXPORT_RE,
    GRAPH_GUID_RE,
    GUID_RE,
    LINKED_TO_RE,
    MACRO_FALLBACK_RE,
    MACRO_RE,
    MEMBER_NAME_RE,
    NAME_RE,
    NODE_GUID_RE,
    NOISE_FIELDS,
    PERSISTENT_GUID_RE,
    PIN_CATEGORY_RE,
    PIN_ID_RE,
    PIN_NAME_RE,
    PIN_SUBCATEGORY_RE,
    VAR_MEMBER_RE,
)
from .utils import first_match, node_type_from_class, strip_quotes

NODE_POS_X_RE = re.compile(r"\bNodePosX=(?P<value>-?\d+)")
NODE_POS_Y_RE = re.compile(r"\bNodePosY=(?P<value>-?\d+)")

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


def macro_name_from_block(block: str) -> str:
    macro = first_match(MACRO_RE, block, "macro")
    if macro:
        return macro.rsplit(":", 1)[-1].rsplit(".", 1)[-1].strip("'\"")
    match = MACRO_FALLBACK_RE.search(block)
    if not match:
        return ""
    path = match.group("path").strip("'\"")
    for separator in (":", "."):
        if separator in path:
            path = path.rsplit(separator, 1)[-1]
    return path.strip("'\"")


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
    delegate = first_match(DELEGATE_MEMBER_RE, block) if "DelegateReference=" in block else ""
    if not event and "K2Node_CustomEvent" in block:
        event = first_match(CUSTOM_FUNCTION_RE, block)
    pos_x = first_match(NODE_POS_X_RE, block, "value")
    pos_y = first_match(NODE_POS_Y_RE, block, "value")

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
        delegate=delegate,
        macro=macro_name_from_block(block) if "MacroGraphReference=" in block or "K2Node_MacroInstance" in block else "",
        comment=first_match(COMMENT_RE, block, "comment"),
        node_pos_x=int(pos_x) if pos_x else None,
        node_pos_y=int(pos_y) if pos_y else None,
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
