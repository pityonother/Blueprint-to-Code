from __future__ import annotations

from collections.abc import Iterable, Mapping


def strongly_connected_components(
    node_refs: Iterable[str],
    successors: Mapping[str, Iterable[str]],
) -> list[list[str]]:
    """Return stable SCCs without recursion, including very deep Blueprint graphs."""

    nodes = sorted(set(node_refs))
    node_set = set(nodes)
    adjacency = {
        node: sorted({target for target in successors.get(node, ()) if target in node_set})
        for node in nodes
    }
    visited: set[str] = set()
    finish_order: list[str] = []
    for start in nodes:
        if start in visited:
            continue
        visited.add(start)
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, offset = stack[-1]
            neighbors = adjacency[node]
            if offset >= len(neighbors):
                stack.pop()
                finish_order.append(node)
                continue
            target = neighbors[offset]
            stack[-1] = (node, offset + 1)
            if target not in visited:
                visited.add(target)
                stack.append((target, 0))

    reverse: dict[str, list[str]] = {node: [] for node in nodes}
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].append(source)
    for node in reverse:
        reverse[node].sort()

    assigned: set[str] = set()
    components: list[list[str]] = []
    for start in reversed(finish_order):
        if start in assigned:
            continue
        assigned.add(start)
        component: list[str] = []
        stack = [(start, 0)]
        while stack:
            node, offset = stack[-1]
            if offset == 0:
                component.append(node)
            neighbors = reverse[node]
            if offset >= len(neighbors):
                stack.pop()
                continue
            target = neighbors[offset]
            stack[-1] = (node, offset + 1)
            if target not in assigned:
                assigned.add(target)
                stack.append((target, 0))
        components.append(sorted(component))
    return sorted(components, key=lambda component: component[0] if component else "")


__all__ = ["strongly_connected_components"]
