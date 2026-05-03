"""Utilities for Blueprint graph capture queues."""

from __future__ import annotations

import re
from dataclasses import dataclass


GRAPH_QUEUE_TIERS = ("recommended", "optional", "deferred")
GRAPH_QUEUE_MODES = ("compact", "recommended", "focused", "all", "optional", "deferred")

LOW_VALUE_PREFIXES = (
    "get ",
    "is ",
    "can ",
    "has ",
    "should ",
    "does ",
)

DEFERRED_PREFIXES = (
    "collapsed ",
    "collapsegraph",
)

ALWAYS_RECOMMENDED_PREFIXES = (
    "bp",
    "blueprint",
    "receive",
    "onrep",
    "server ",
    "client ",
    "net ",
    "multicast ",
    "execute",
)

ACTIVE_PREFIXES = (
    "add ",
    "allow ",
    "apply ",
    "check ",
    "clear ",
    "disable ",
    "do ",
    "enable ",
    "force ",
    "init",
    "play ",
    "prevent ",
    "remove ",
    "set ",
    "setup ",
    "start ",
    "stop ",
    "tick ",
    "try ",
    "update ",
)

BEHAVIOR_KEYWORDS = (
    "attack",
    "baby",
    "buff",
    "camo",
    "cloak",
    "cryo",
    "damage",
    "death",
    "fall",
    "fish",
    "glide",
    "hud",
    "jump",
    "killed",
    "leap",
    "level",
    "multiuse",
    "movement",
    "passenger",
    "parachute",
    "rider",
    "riding",
    "roar",
    "sleep",
    "slide",
    "sliding",
    "stamina",
    "target",
    "taming",
    "teleport",
    "tick",
    "timer",
    "torpidity",
    "wake",
    "nurse",
    "nursing",
)


@dataclass(frozen=True)
class GraphQueueItem:
    name: str
    type: str
    line: str
    tier: str
    reason: str


def normalize_graph_type(value: str) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered in {"eventgraph", "event graph", "event", "事件图"}:
        return "EventGraph"
    if lowered in {"function", "func", "函数"}:
        return "Function"
    if lowered in {"macro", "宏"}:
        return "Macro"
    if lowered in {"constructionscript", "construction script", "construction", "构造脚本"}:
        return "ConstructionScript"
    if text in {"EventGraph", "Function", "Macro", "ConstructionScript", "Unknown"}:
        return text
    return "Unknown"


def graph_name_key(name: str) -> str:
    text = str(name or "").replace("_", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"\((?:pure|const|pure,\s*const|const,\s*pure)\)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text)
    return text.casefold()


def parse_graph_queue_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    name = stripped
    graph_type = "Unknown"
    pipe_parts = [part.strip() for part in stripped.split("|") if part.strip()]
    if len(pipe_parts) >= 2:
        name = pipe_parts[0]
        graph_type = normalize_graph_type(pipe_parts[1])
    else:
        tab_parts = [part.strip() for part in stripped.replace("\t", ",").split(",") if part.strip()]
        if len(tab_parts) >= 2 and normalize_graph_type(tab_parts[-1]) != "Unknown":
            name = " ".join(tab_parts[:-1])
            graph_type = normalize_graph_type(tab_parts[-1])
    name = name.strip()
    if not name:
        return None
    return name, graph_type


def classify_graph_queue_item(name: str, graph_type: str = "Unknown") -> tuple[str, str]:
    spaced_name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name or "").replace("_", " "))
    lowered = " ".join(spaced_name.strip().lower().split())
    graph_type = normalize_graph_type(graph_type)
    if not lowered:
        return "deferred", "空名称"
    if graph_type in {"EventGraph", "ConstructionScript"}:
        return "recommended", "核心事件图或构造脚本"
    if lowered.startswith(DEFERRED_PREFIXES):
        return "deferred", "折叠图通常由外层图调用，先不用手动采集"
    if lowered.startswith(ALWAYS_RECOMMENDED_PREFIXES):
        return "recommended", "蓝图事件覆写、复制通知或网络入口"
    has_behavior_keyword = any(keyword in lowered for keyword in BEHAVIOR_KEYWORDS)
    if lowered.startswith(LOW_VALUE_PREFIXES):
        if has_behavior_keyword:
            return "optional", "判断/Getter 图页，通常用于补上下文"
        return "deferred", "低价值判断/Getter，优先级较低"
    if lowered.startswith(ACTIVE_PREFIXES):
        if has_behavior_keyword:
            return "recommended", "会改变状态或触发关键 ARK 行为"
        return "optional", "主动逻辑函数，但行为关键词不强"
    if has_behavior_keyword:
        return "optional", "包含关键行为关键词，建议按需要补采集"
    if graph_type == "Macro":
        return "optional", "宏图可能影响展开逻辑"
    return "deferred", "未命中关键行为规则"


def parse_graph_queue(text: str) -> list[GraphQueueItem]:
    items: list[GraphQueueItem] = []
    seen: set[str] = set()
    for line in str(text or "").splitlines():
        parsed = parse_graph_queue_line(line)
        if parsed is None:
            continue
        name, graph_type = parsed
        key = graph_name_key(name)
        if key in seen:
            continue
        seen.add(key)
        tier, reason = classify_graph_queue_item(name, graph_type)
        items.append(
            GraphQueueItem(
                name=name,
                type=graph_type,
                line=f"{name} | {graph_type}",
                tier=tier,
                reason=reason,
            )
        )
    return items


def graph_queue_summary(text: str) -> dict[str, object]:
    items = parse_graph_queue(text)
    counts = {tier: 0 for tier in GRAPH_QUEUE_TIERS}
    for item in items:
        counts[item.tier] = counts.get(item.tier, 0) + 1
    return {
        "total": len(items),
        "recommended": counts.get("recommended", 0),
        "compact": counts.get("recommended", 0),
        "optional": counts.get("optional", 0),
        "deferred": counts.get("deferred", 0),
        "focused": counts.get("recommended", 0) + counts.get("optional", 0),
        "items": [
            {
                "name": item.name,
                "type": item.type,
                "line": item.line,
                "tier": item.tier,
                "reason": item.reason,
            }
            for item in items
        ],
    }


def graph_queue_text_for_mode(text: str, mode: str) -> str:
    mode = mode if mode in GRAPH_QUEUE_MODES else "all"
    items = parse_graph_queue(text)
    if mode in {"compact", "recommended"}:
        selected = [item for item in items if item.tier == "recommended"]
    elif mode == "focused":
        selected = [item for item in items if item.tier in {"recommended", "optional"}]
    elif mode == "optional":
        selected = [item for item in items if item.tier == "optional"]
    elif mode == "deferred":
        selected = [item for item in items if item.tier == "deferred"]
    else:
        selected = items
    return "\n".join(item.line for item in selected) + ("\n" if selected else "")
