"""Shared data models for parsed Blueprint nodes and pins."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

@dataclass
class PinInfo:
    id: str = ""
    name: str = ""
    direction: str = "EGPD_Input"
    category: str = ""
    subcategory: str = ""
    pin_type: dict[str, object] = field(default_factory=dict)
    default: str = ""
    default_object: str = ""
    persistent_guid: str = ""
    linked_to_raw: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    source: str = "clipboard"
    confidence: str = "high"
    warnings: list[str] = field(default_factory=list)
    raw_offsets: dict[str, int] = field(default_factory=dict)
    resolution: dict[str, object] = field(default_factory=dict)


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
    delegate: str = ""
    macro: str = ""
    comment: str = ""
    node_pos_x: int | None = None
    node_pos_y: int | None = None
    properties: dict[str, object] = field(default_factory=dict)
    semantic: dict[str, object] = field(default_factory=dict)
    pins: list[PinInfo] = field(default_factory=list)
    source: str = "clipboard"
    confidence: str = "high"
    warnings: list[str] = field(default_factory=list)
    raw_offsets: dict[str, int] = field(default_factory=dict)
    keyword_hits: Counter = field(default_factory=Counter)
    raw: str = ""

    @property
    def link_count(self) -> int:
        return sum(len(pin.links) for pin in self.pins)

    @property
    def label(self) -> str:
        for value in (self.function, self.variable, self.event, self.delegate, self.macro, self.comment, self.name, self.node_type):
            if value:
                return value
        return f"Node {self.index}"
