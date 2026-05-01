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
    delegate: str = ""
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
        for value in (self.function, self.variable, self.event, self.delegate, self.macro, self.comment, self.name, self.node_type):
            if value:
                return value
        return f"Node {self.index}"
