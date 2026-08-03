from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import stable_id
from .graph_algorithms import strongly_connected_components
from .source import InterpretationSource


@dataclass(frozen=True)
class ControlFlowResult:
    graphs: tuple[dict[str, Any], ...]
    gaps: tuple[dict[str, Any], ...]
    executable_node_refs: frozenset[str]


def _fold(value: object) -> str:
    return str(value or "").strip().casefold()


def _exact_pin_category(pin: dict[str, Any]) -> str:
    pin_type = pin.get("pin_type")
    categories = {
        category
        for category in (
            _fold(pin.get("category")),
            _fold(pin_type.get("PinCategory")) if isinstance(pin_type, dict) else "",
        )
        if category
    }
    return next(iter(categories)) if len(categories) == 1 else ""


def _is_exec_pin(pin: dict[str, Any]) -> bool:
    return _exact_pin_category(pin) == "exec"


def _direction_role(pin: dict[str, Any]) -> str:
    direction = _fold(pin.get("direction"))
    if direction in {"egpd_output", "output", "out"} or direction.endswith(
        "::egpd_output"
    ):
        return "output"
    if direction in {"egpd_input", "input", "in"} or direction.endswith("::egpd_input"):
        return "input"
    return "unknown"


def _observation_refs_by_edge(
    observations: Iterable[dict[str, Any]],
) -> dict[tuple[str, str, str], list[str]]:
    indexed: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for observation in observations:
        source_pin_ref = str(observation.get("source_pin_ref") or "")
        target_pin_ref = str(observation.get("target_pin_ref") or "")
        if not source_pin_ref or not target_pin_ref:
            continue
        first_pin_ref, second_pin_ref = sorted((source_pin_ref, target_pin_ref))
        key = (
            str(observation.get("graph_ref") or ""),
            first_pin_ref,
            second_pin_ref,
        )
        indexed[key].append(str(observation.get("observation_ref") or ""))
    return {key: sorted(set(refs)) for key, refs in indexed.items()}


def _edge_observation_refs(
    edge: dict[str, Any],
    observation_refs_by_edge: dict[tuple[str, str, str], list[str]],
) -> list[str]:
    first_pin_ref, second_pin_ref = sorted(
        (str(edge["source_pin_ref"]), str(edge["target_pin_ref"]))
    )
    return observation_refs_by_edge.get(
        (
            str(edge["graph_ref"]),
            first_pin_ref,
            second_pin_ref,
        ),
        [],
    )


def control_node_kind(node: dict[str, Any]) -> str:
    node_type = _fold(node.get("node_type") or node.get("class_name"))
    control_kind = _fold(node.get("control_kind"))
    macro = _fold(node.get("macro_name"))
    if (
        "functionentry" in node_type
        or node_type.endswith("_event")
        or node.get("event_name")
    ):
        return "EVENT"
    if "ifthenelse" in node_type or control_kind == "branch":
        return "BRANCH"
    if "variableset" in node_type:
        return "SET"
    if "functionresult" in node_type or control_kind == "return":
        return "RETURN"
    if "delegate" in node_type or node.get("delegate_name"):
        return "DELEGATE"
    if "macro" in node_type or node.get("macro_name"):
        return "LOOP" if "loop" in macro or "foreach" in macro else "CALL"
    return "CALL"


def _gap(
    code: str,
    *,
    graph_ref: str,
    detail: str,
    status: str = "NOT_RECOVERED",
    node_ref: str = "",
    pin_ref: str = "",
    evidence_refs: Iterable[str] = (),
    source: str = "CONTROL_FLOW",
) -> dict[str, Any]:
    evidence = sorted(
        {str(ref) for ref in evidence_refs if str(ref).startswith("bp://")}
    )
    projection = {
        "code": code,
        "graphRef": graph_ref,
        "nodeRef": node_ref,
        "pinRef": pin_ref,
        "detail": detail,
        "evidenceRefs": evidence,
        "source": source,
    }
    return {
        "id": stable_id("gap://", projection),
        "code": code,
        "status": status,
        "graphRef": graph_ref,
        "nodeRef": node_ref,
        "pinRef": pin_ref,
        "detail": detail,
        "evidenceRefs": evidence,
        "source": source,
    }


def _strongly_connected_components(
    node_refs: list[str],
    successors: dict[str, list[str]],
) -> list[list[str]]:
    return strongly_connected_components(node_refs, successors)


def _basic_blocks(
    node_refs: list[str],
    entries: list[str],
    successors: dict[str, list[str]],
    predecessors: dict[str, list[str]],
    kind_by_node: dict[str, str],
) -> list[dict[str, Any]]:
    boundaries = set(entries)
    for node_ref in node_refs:
        if len(predecessors.get(node_ref, [])) != 1:
            boundaries.add(node_ref)
        if (
            len(successors.get(node_ref, [])) != 1
            or kind_by_node.get(node_ref) == "BRANCH"
        ):
            boundaries.add(node_ref)
        for target_ref in successors.get(node_ref, []):
            if len(successors.get(node_ref, [])) != 1:
                boundaries.add(target_ref)

    assigned: set[str] = set()
    blocks: list[dict[str, Any]] = []
    ordered_starts = sorted(node_refs, key=lambda ref: (ref not in boundaries, ref))
    for start_ref in ordered_starts:
        if start_ref in assigned:
            continue
        members: list[str] = []
        member_refs: set[str] = set()
        current = start_ref
        while current not in assigned:
            assigned.add(current)
            members.append(current)
            member_refs.add(current)
            next_refs = successors.get(current, [])
            if len(next_refs) != 1:
                break
            next_ref = next_refs[0]
            if (
                next_ref in assigned
                or next_ref in boundaries
                or len(predecessors.get(next_ref, [])) != 1
            ):
                break
            current = next_ref
        block_id = stable_id("block://", {"nodes": members})
        target_blocks: list[str] = []
        blocks.append(
            {
                "id": block_id,
                "label": f"L_{block_id.removeprefix('block://')[:10]}",
                "nodeRefs": members,
                "successorNodeRefs": sorted(
                    {
                        target
                        for member in members
                        for target in successors.get(member, [])
                        if target not in member_refs
                    }
                ),
                "successorBlockIds": target_blocks,
            }
        )
    node_to_block = {
        node_ref: str(block["id"]) for block in blocks for node_ref in block["nodeRefs"]
    }
    for block in blocks:
        block["successorBlockIds"] = sorted(
            {
                node_to_block[target]
                for target in block.pop("successorNodeRefs")
                if target in node_to_block and node_to_block[target] != block["id"]
            }
        )
    return sorted(blocks, key=lambda block: str(block["id"]))


def build_control_flow(source: InterpretationSource) -> ControlFlowResult:
    pin_by_ref = {str(row["pin_ref"]): row for row in source.pins}
    pins_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pin in source.pins:
        pins_by_node[str(pin["node_ref"])].append(pin)
    nodes_by_graph: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in source.nodes:
        nodes_by_graph[str(node["graph_ref"])].append(node)

    observation_refs_by_edge = _observation_refs_by_edge(source.observations)
    edge_rows_by_graph: dict[str, list[dict[str, Any]]] = defaultdict(list)
    direction_gaps: list[dict[str, Any]] = []
    for edge in source.edges:
        if _fold(edge.get("resolution_status")) != "resolved_pin":
            continue
        source_pin = pin_by_ref.get(str(edge["source_pin_ref"]))
        target_pin = pin_by_ref.get(str(edge["target_pin_ref"]))
        edge_kind = _fold(edge.get("kind"))
        source_category = (
            _exact_pin_category(source_pin) if source_pin is not None else ""
        )
        target_category = (
            _exact_pin_category(target_pin) if target_pin is not None else ""
        )
        if edge_kind != "exec":
            if edge_kind != "data" and "exec" in {
                source_category,
                target_category,
            }:
                source_pin_ref = str(edge["source_pin_ref"])
                target_pin_ref = str(edge["target_pin_ref"])
                source_node_ref = (
                    str(source_pin.get("node_ref") or "") if source_pin else ""
                )
                direction_gaps.append(
                    _gap(
                        "AMBIGUOUS_EXEC_EDGE",
                        graph_ref=str(edge["graph_ref"]),
                        node_ref=source_node_ref,
                        pin_ref=source_pin_ref,
                        detail=(
                            "A resolved edge touching an exact exec Pin was rejected "
                            "because its edge kind is not exactly exec or data: "
                            f"kind={edge_kind or 'unknown'}."
                        ),
                        status="AMBIGUOUS",
                        evidence_refs=[
                            str(edge["edge_ref"]),
                            source_pin_ref,
                            target_pin_ref,
                            *_edge_observation_refs(edge, observation_refs_by_edge),
                        ],
                        source="EDGE_KIND",
                    )
                )
            continue
        if source_category != "exec" or target_category != "exec":
            category_is_ambiguous = not source_category or not target_category
            source_pin_ref = str(edge["source_pin_ref"])
            target_pin_ref = str(edge["target_pin_ref"])
            source_node_ref = (
                str(source_pin.get("node_ref") or "") if source_pin else ""
            )
            direction_gaps.append(
                _gap(
                    (
                        "AMBIGUOUS_EXEC_EDGE"
                        if category_is_ambiguous
                        else "UNRESOLVED_EXEC_EDGE"
                    ),
                    graph_ref=str(edge["graph_ref"]),
                    node_ref=source_node_ref,
                    pin_ref=source_pin_ref,
                    detail=(
                        "A resolved executable edge was rejected because both exact "
                        "Pin categories must be exec: "
                        f"source={source_category or 'unknown'}, "
                        f"target={target_category or 'unknown'}."
                    ),
                    status=("AMBIGUOUS" if category_is_ambiguous else "NOT_RECOVERED"),
                    evidence_refs=[
                        str(edge["edge_ref"]),
                        source_pin_ref,
                        target_pin_ref,
                        *_edge_observation_refs(edge, observation_refs_by_edge),
                    ],
                    source="EDGE_CATEGORY",
                )
            )
            continue
        source_role = (
            _direction_role(source_pin) if source_pin is not None else "unknown"
        )
        target_role = (
            _direction_role(target_pin) if target_pin is not None else "unknown"
        )
        if source_role != "output" or target_role != "input":
            direction_is_ambiguous = "unknown" in {source_role, target_role}
            source_pin_ref = str(edge["source_pin_ref"])
            target_pin_ref = str(edge["target_pin_ref"])
            source_node_ref = (
                str(source_pin.get("node_ref") or "") if source_pin else ""
            )
            direction_gaps.append(
                _gap(
                    (
                        "AMBIGUOUS_EXEC_EDGE"
                        if direction_is_ambiguous
                        else "UNRESOLVED_EXEC_EDGE"
                    ),
                    graph_ref=str(edge["graph_ref"]),
                    node_ref=source_node_ref,
                    pin_ref=source_pin_ref,
                    detail=(
                        "A resolved executable edge was rejected because its exact Pin "
                        "directions are not output-to-input: "
                        f"source={source_role}, target={target_role}."
                    ),
                    status="AMBIGUOUS" if direction_is_ambiguous else "NOT_RECOVERED",
                    evidence_refs=[
                        str(edge["edge_ref"]),
                        source_pin_ref,
                        target_pin_ref,
                        *_edge_observation_refs(edge, observation_refs_by_edge),
                    ],
                    source="EDGE_DIRECTION",
                )
            )
            continue
        edge_rows_by_graph[str(edge["graph_ref"])].append(edge)

    graph_results: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = list(direction_gaps)
    executable_refs: set[str] = set()
    for graph in source.graphs:
        graph_ref = str(graph["graph_ref"])
        graph_nodes = nodes_by_graph.get(graph_ref, [])
        graph_node_refs = {str(row["node_ref"]) for row in graph_nodes}
        graph_exec_edges = edge_rows_by_graph.get(graph_ref, [])
        successors: dict[str, list[str]] = defaultdict(list)
        predecessors: dict[str, list[str]] = defaultdict(list)
        successor_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in graph_exec_edges:
            source_pin = pin_by_ref[str(edge["source_pin_ref"])]
            target_pin = pin_by_ref[str(edge["target_pin_ref"])]
            source_node_ref = str(source_pin["node_ref"])
            target_node_ref = str(target_pin["node_ref"])
            if (
                source_node_ref not in graph_node_refs
                or target_node_ref not in graph_node_refs
            ):
                continue
            successors[source_node_ref].append(target_node_ref)
            predecessors[target_node_ref].append(source_node_ref)
            successor_rows[source_node_ref].append(
                {
                    "edgeRef": str(edge["edge_ref"]),
                    "sourcePinRef": str(source_pin["pin_ref"]),
                    "sourcePinName": str(source_pin.get("name") or ""),
                    "targetPinRef": str(target_pin["pin_ref"]),
                    "targetPinName": str(target_pin.get("name") or ""),
                    "targetNodeRef": target_node_ref,
                    "status": str(edge.get("resolution_status") or "resolved_pin"),
                }
            )
        for mapping in (successors, predecessors):
            for node_ref in mapping:
                mapping[node_ref] = sorted(set(mapping[node_ref]))
        for node_ref in successor_rows:
            successor_rows[node_ref] = sorted(
                successor_rows[node_ref],
                key=lambda row: (
                    str(row["sourcePinRef"]),
                    str(row["targetPinRef"]),
                ),
            )

        executable_nodes = [
            node
            for node in graph_nodes
            if any(
                _is_exec_pin(pin) for pin in pins_by_node.get(str(node["node_ref"]), [])
            )
            or control_node_kind(node) in {"EVENT", "RETURN"}
        ]
        executable_node_refs = sorted(
            str(node["node_ref"]) for node in executable_nodes
        )
        executable_refs.update(executable_node_refs)
        entries = sorted(
            str(node["node_ref"])
            for node in executable_nodes
            if control_node_kind(node) == "EVENT"
        )
        root_candidates = sorted(
            node_ref
            for node_ref in executable_node_refs
            if not predecessors.get(node_ref)
        )
        if executable_node_refs and not entries:
            gaps.append(
                _gap(
                    "NO_ENTRY_POINT",
                    graph_ref=graph_ref,
                    detail="No exact executable entry point was recovered.",
                    evidence_refs=[graph_ref],
                )
            )
        if len(entries) > 1:
            gaps.append(
                _gap(
                    "MULTIPLE_ENTRY_POINTS",
                    graph_ref=graph_ref,
                    detail=f"{len(entries)} executable entry points were recovered.",
                    status="AMBIGUOUS",
                    evidence_refs=[graph_ref, *entries],
                )
            )

        kind_by_node = {
            str(node["node_ref"]): control_node_kind(node) for node in executable_nodes
        }
        components = _strongly_connected_components(executable_node_refs, successors)
        cycles = [
            component
            for component in components
            if len(component) > 1
            or (
                len(component) == 1 and component[0] in successors.get(component[0], [])
            )
        ]
        cycle_index_by_node = {
            node_ref: index
            for index, component in enumerate(cycles)
            for node_ref in component
        }
        cycle_edge_refs_by_index: dict[int, list[str]] = defaultdict(list)
        for edge in graph_exec_edges:
            source_node_ref = str(pin_by_ref[str(edge["source_pin_ref"])]["node_ref"])
            target_node_ref = str(pin_by_ref[str(edge["target_pin_ref"])]["node_ref"])
            cycle_index = cycle_index_by_node.get(source_node_ref)
            if (
                cycle_index is not None
                and cycle_index_by_node.get(target_node_ref) == cycle_index
            ):
                cycle_edge_refs_by_index[cycle_index].append(str(edge["edge_ref"]))
        for cycle_index, component in enumerate(cycles):
            cycle_edge_refs = sorted(set(cycle_edge_refs_by_index.get(cycle_index, [])))
            gaps.append(
                _gap(
                    "UNSTRUCTURED_CYCLE",
                    graph_ref=graph_ref,
                    node_ref=component[0],
                    detail="An executable cycle exists, but its structured loop shape is not proven.",
                    evidence_refs=[graph_ref, *component, *cycle_edge_refs],
                )
            )

        node_results = []
        for node in executable_nodes:
            node_ref = str(node["node_ref"])
            node_results.append(
                {
                    "nodeRef": node_ref,
                    "kind": kind_by_node[node_ref],
                    "nodeType": str(
                        node.get("node_type") or node.get("class_name") or ""
                    ),
                    "label": str(node.get("label") or node.get("name") or ""),
                    "successors": successor_rows.get(node_ref, []),
                    "predecessorNodeRefs": predecessors.get(node_ref, []),
                }
            )

        blocks = _basic_blocks(
            executable_node_refs,
            entries,
            successors,
            predecessors,
            kind_by_node,
        )
        graph_results.append(
            {
                "graphRef": graph_ref,
                "name": str(graph.get("name") or ""),
                "graphType": str(graph.get("graph_type") or ""),
                "status": str(graph.get("status") or ""),
                "entryNodeRefs": entries,
                "rootCandidateNodeRefs": root_candidates,
                "nodes": sorted(node_results, key=lambda row: str(row["nodeRef"])),
                "basicBlocks": blocks,
                "cycles": cycles,
                "mergeAnalysis": {
                    "proven": False,
                    "reason": "Only exact edges are emitted; uncertain merges remain labeled blocks.",
                },
            }
        )

    for observation in source.observations:
        if _fold(observation.get("kind")) != "exec":
            continue
        resolution = _fold(
            observation.get("resolution_status") or observation.get("status")
        )
        if resolution == "resolved_pin" and observation.get("target_pin_ref"):
            continue
        code = (
            "AMBIGUOUS_EXEC_EDGE"
            if "ambiguous" in resolution
            else "UNRESOLVED_EXEC_EDGE"
        )
        status = "AMBIGUOUS" if code.startswith("AMBIGUOUS") else "NOT_RECOVERED"
        evidence_refs = [
            str(observation.get("observation_ref") or ""),
            str(observation.get("source_node_ref") or ""),
            str(observation.get("source_pin_ref") or ""),
        ]
        gaps.append(
            _gap(
                code,
                graph_ref=str(observation["graph_ref"]),
                node_ref=str(observation.get("source_node_ref") or ""),
                pin_ref=str(observation.get("source_pin_ref") or ""),
                detail=(
                    "An executable edge observation has no exact target Pin. "
                    f"resolution={resolution or 'not_recovered'}"
                ),
                status=status,
                evidence_refs=evidence_refs,
                source="EDGE_OBSERVATION",
            )
        )

    return ControlFlowResult(
        graphs=tuple(sorted(graph_results, key=lambda row: str(row["graphRef"]))),
        gaps=tuple(sorted(gaps, key=lambda row: str(row["id"]))),
        executable_node_refs=frozenset(executable_refs),
    )


__all__ = ["ControlFlowResult", "build_control_flow", "control_node_kind"]
