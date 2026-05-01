"""General utilities for the Blueprint translator."""

from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Iterable

from .config import KEYWORD_GROUPS, PROFILE_CONFIG
from .models import NodeInfo, PinInfo

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
        parts = [node.node_type, node.function, node.variable, node.event, node.delegate, node.macro, node.name]
    else:
        guid = str(node.get("node_guid", ""))
        parts = [
            str(node.get("node_type", "")),
            str(node.get("function", "")),
            str(node.get("variable", "")),
            str(node.get("event", "")),
            str(node.get("delegate", "")),
            str(node.get("macro", "")),
            str(node.get("name", "")),
        ]
    if guid:
        return f"guid:{guid}"
    return "sig:" + " | ".join(part for part in parts if part)


def label_for(node: NodeInfo | dict[str, object]) -> str:
    if isinstance(node, NodeInfo):
        return node.label
    for field in ("function", "variable", "event", "delegate", "macro", "comment", "name", "node_type"):
        value = str(node.get(field, ""))
        if value:
            return value
    return "Node"


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._")
    return cleaned or fallback
