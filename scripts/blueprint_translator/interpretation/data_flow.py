from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import stable_id
from .graph_algorithms import strongly_connected_components
from .source import InterpretationSource


_UNRESOLVED_PIN_CATEGORIES = frozenset({"none", "unknown", "wildcard"})


@dataclass(frozen=True)
class DataFlowResult:
    graphs: tuple[dict[str, Any], ...]
    shared_expressions: tuple[dict[str, Any], ...]
    gaps: tuple[dict[str, Any], ...]


def _fold(value: object) -> str:
    return str(value or "").strip().casefold()


def _pin_category(pin: dict[str, Any]) -> str:
    category = str(pin.get("category") or "").strip()
    if category:
        return category
    pin_type = pin.get("pin_type")
    if isinstance(pin_type, dict):
        return str(pin_type.get("PinCategory") or "").strip()
    return ""


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


def _is_exec(pin: dict[str, Any]) -> bool:
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


def _pin_projection(pin: dict[str, Any]) -> dict[str, Any]:
    pin_type = pin.get("pin_type") if isinstance(pin.get("pin_type"), dict) else {}
    default_literal = pin.get("default_value")
    serialized_default_object = str(pin.get("default_object") or "")
    default_class = ""
    default_object = serialized_default_object
    return {
        "pinRef": str(pin["pin_ref"]),
        "nodeRef": str(pin["node_ref"]),
        "name": str(pin.get("name") or ""),
        "direction": str(pin.get("direction") or ""),
        "exactType": {
            "category": _pin_category(pin),
            "subcategory": str(pin.get("subcategory") or ""),
            "serialized": pin_type,
        },
        "default": {
            "recovered": (
                default_literal not in (None, "") or bool(serialized_default_object)
            ),
            "literal": default_literal,
            "objectRef": default_object,
            "classRef": default_class,
        },
    }


def _gap(
    code: str,
    *,
    graph_ref: str,
    detail: str,
    status: str = "NOT_RECOVERED",
    node_ref: str = "",
    pin_ref: str = "",
    evidence_refs: Iterable[str] = (),
    source: str = "DATA_FLOW",
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


def _cycles(nodes: set[str], successors: dict[str, set[str]]) -> list[list[str]]:
    components = strongly_connected_components(nodes, successors)
    return [
        component
        for component in components
        if len(component) > 1
        or (len(component) == 1 and component[0] in successors.get(component[0], set()))
    ]


def build_data_flow(source: InterpretationSource) -> DataFlowResult:
    node_by_ref = {str(row["node_ref"]): row for row in source.nodes}
    graph_by_node = {
        str(row["node_ref"]): str(row["graph_ref"]) for row in source.nodes
    }
    pin_by_ref = {str(row["pin_ref"]): row for row in source.pins}
    pins_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pin in source.pins:
        pins_by_node[str(pin["node_ref"])].append(pin)
    pins_by_graph: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pin in source.pins:
        pins_by_graph[graph_by_node.get(str(pin["node_ref"]), "")].append(pin)

    outgoing_by_pin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incoming_by_pin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    successors_by_graph: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    incoming_nodes_by_graph: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    data_edges_by_graph: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observation_refs_by_edge = _observation_refs_by_edge(source.observations)
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
        if edge_kind != "data":
            if edge_kind != "exec" and "exec" not in {
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
                        "AMBIGUOUS_DATA_EDGE",
                        graph_ref=str(edge["graph_ref"]),
                        node_ref=source_node_ref,
                        pin_ref=source_pin_ref,
                        detail=(
                            "A resolved edge without an exact exec Pin was rejected "
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
        categories_are_ambiguous = (
            not source_category
            or not target_category
            or source_category in _UNRESOLVED_PIN_CATEGORIES
            or target_category in _UNRESOLVED_PIN_CATEGORIES
        )
        categories_are_compatible = (
            not categories_are_ambiguous
            and source_category != "exec"
            and target_category != "exec"
            and source_category == target_category
        )
        if not categories_are_compatible:
            source_pin_ref = str(edge["source_pin_ref"])
            target_pin_ref = str(edge["target_pin_ref"])
            source_node_ref = (
                str(source_pin.get("node_ref") or "") if source_pin else ""
            )
            direction_gaps.append(
                _gap(
                    (
                        "AMBIGUOUS_DATA_EDGE"
                        if categories_are_ambiguous
                        else "UNRESOLVED_DATA_EDGE"
                    ),
                    graph_ref=str(edge["graph_ref"]),
                    node_ref=source_node_ref,
                    pin_ref=source_pin_ref,
                    detail=(
                        "A resolved data edge was rejected because both exact Pin "
                        "categories must be compatible non-exec categories: "
                        f"source={source_category or 'unknown'}, "
                        f"target={target_category or 'unknown'}."
                    ),
                    status=(
                        "AMBIGUOUS" if categories_are_ambiguous else "NOT_RECOVERED"
                    ),
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
                        "AMBIGUOUS_DATA_EDGE"
                        if direction_is_ambiguous
                        else "UNRESOLVED_DATA_EDGE"
                    ),
                    graph_ref=str(edge["graph_ref"]),
                    node_ref=source_node_ref,
                    pin_ref=source_pin_ref,
                    detail=(
                        "A resolved data edge was rejected because its exact Pin "
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
        if source_pin is None or target_pin is None:
            continue
        source_node_ref = str(source_pin["node_ref"])
        target_node_ref = str(target_pin["node_ref"])
        graph_ref = str(edge["graph_ref"])
        row = {
            "edgeRef": str(edge["edge_ref"]),
            "sourceNodeRef": source_node_ref,
            "sourcePinRef": str(source_pin["pin_ref"]),
            "sourcePinName": str(source_pin.get("name") or ""),
            "sourceDirection": str(source_pin.get("direction") or ""),
            "sourceType": {
                "category": _pin_category(source_pin),
                "subcategory": str(source_pin.get("subcategory") or ""),
                "exact": source_pin.get("pin_type") or {},
            },
            "targetNodeRef": target_node_ref,
            "targetPinRef": str(target_pin["pin_ref"]),
            "targetPinName": str(target_pin.get("name") or ""),
            "targetDirection": str(target_pin.get("direction") or ""),
            "targetType": {
                "category": _pin_category(target_pin),
                "subcategory": str(target_pin.get("subcategory") or ""),
                "exact": target_pin.get("pin_type") or {},
            },
            "status": str(edge.get("resolution_status") or "resolved_pin"),
        }
        projected_edge = {"graphRef": graph_ref, **row}
        data_edges_by_graph[graph_ref].append(projected_edge)
        outgoing_by_pin[str(source_pin["pin_ref"])].append(row)
        incoming_by_pin[str(target_pin["pin_ref"])].append(row)
        successors_by_graph[graph_ref][source_node_ref].add(target_node_ref)
        incoming_nodes_by_graph[graph_ref][target_node_ref].add(source_node_ref)

    gaps: list[dict[str, Any]] = list(direction_gaps)
    for pin in source.pins:
        if _is_exec(pin):
            continue
        pin_ref = str(pin["pin_ref"])
        node_ref = str(pin["node_ref"])
        graph_ref = graph_by_node.get(node_ref, "")
        category = _pin_category(pin)
        if not category:
            gaps.append(
                _gap(
                    "TYPE_NOT_RECOVERED",
                    graph_ref=graph_ref,
                    node_ref=node_ref,
                    pin_ref=pin_ref,
                    detail="The exact Pin type was not recovered.",
                    evidence_refs=[pin_ref, node_ref],
                )
            )
        direction = _fold(pin.get("direction"))
        is_input = direction in {"egpd_input", "input", "in"}
        if is_input and not incoming_by_pin.get(pin_ref):
            default_value = pin.get("default_value")
            default_object = str(pin.get("default_object") or "")
            has_default = default_object != "" or default_value not in (None, "")
            if not has_default:
                gaps.append(
                    _gap(
                        "SOURCE_PIN_NOT_RECOVERED",
                        graph_ref=graph_ref,
                        node_ref=node_ref,
                        pin_ref=pin_ref,
                        detail="The input Pin has no exact producer edge.",
                        evidence_refs=[pin_ref, node_ref],
                    )
                )
                gaps.append(
                    _gap(
                        "DEFAULT_NOT_RECOVERED",
                        graph_ref=graph_ref,
                        node_ref=node_ref,
                        pin_ref=pin_ref,
                        detail="No producer or recovered default value exists for the input Pin.",
                        evidence_refs=[pin_ref, node_ref],
                    )
                )

    for observation in source.observations:
        if _fold(observation.get("kind")) == "exec":
            continue
        resolution = _fold(
            observation.get("resolution_status") or observation.get("status")
        )
        if resolution == "resolved_pin" and observation.get("target_pin_ref"):
            continue
        code = (
            "AMBIGUOUS_DATA_EDGE"
            if "ambiguous" in resolution
            else "UNRESOLVED_DATA_EDGE"
        )
        status = "AMBIGUOUS" if code.startswith("AMBIGUOUS") else "NOT_RECOVERED"
        source_node_ref = str(observation.get("source_node_ref") or "")
        source_pin_ref = str(observation.get("source_pin_ref") or "")
        gaps.append(
            _gap(
                code,
                graph_ref=str(observation["graph_ref"]),
                node_ref=source_node_ref,
                pin_ref=source_pin_ref,
                detail=(
                    "A data edge observation has no exact target Pin. "
                    f"resolution={resolution or 'not_recovered'}"
                ),
                status=status,
                evidence_refs=[
                    str(observation.get("observation_ref") or ""),
                    source_node_ref,
                    source_pin_ref,
                ],
                source="EDGE_OBSERVATION",
            )
        )

    native_pin_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pin in source.pins:
        native_id = str(pin.get("native_pin_id") or "")
        if not native_id:
            continue
        node_ref = str(pin["node_ref"])
        native_pin_groups[(graph_by_node.get(node_ref, ""), native_id)].append(
            str(pin["pin_ref"])
        )
    for (graph_ref, native_id), pin_refs in sorted(native_pin_groups.items()):
        if len(pin_refs) <= 1:
            continue
        gaps.append(
            _gap(
                "AMBIGUOUS_DATA_EDGE",
                graph_ref=graph_ref,
                pin_ref=pin_refs[0],
                detail=f"Native Pin identity {native_id!r} appears {len(pin_refs)} times.",
                status="AMBIGUOUS",
                evidence_refs=pin_refs,
            )
        )

    pure_node_refs = {
        node_ref
        for node_ref, pins in pins_by_node.items()
        if not any(_is_exec(pin) for pin in pins)
    }
    shared_expressions = [
        {
            "sourcePinRef": source_pin_ref,
            "sourceNodeRef": str(pin_by_ref[source_pin_ref]["node_ref"]),
            "consumerPinRefs": sorted(str(row["targetPinRef"]) for row in rows),
            "edgeRefs": sorted(str(row["edgeRef"]) for row in rows),
        }
        for source_pin_ref, rows in sorted(outgoing_by_pin.items())
        if len(rows) > 1
        and str(pin_by_ref[source_pin_ref]["node_ref"]) in pure_node_refs
    ]

    graph_results: list[dict[str, Any]] = []
    for graph in source.graphs:
        graph_ref = str(graph["graph_ref"])
        graph_edges = data_edges_by_graph.get(graph_ref, [])
        graph_nodes = {str(row["sourceNodeRef"]) for row in graph_edges} | {
            str(row["targetNodeRef"]) for row in graph_edges
        }
        cycles = _cycles(graph_nodes, successors_by_graph.get(graph_ref, {}))
        cycle_index_by_node = {
            node_ref: index
            for index, component in enumerate(cycles)
            for node_ref in component
        }
        cycle_edge_refs_by_index: dict[int, list[str]] = defaultdict(list)
        for row in graph_edges:
            source_node_ref = str(row["sourceNodeRef"])
            target_node_ref = str(row["targetNodeRef"])
            cycle_index = cycle_index_by_node.get(source_node_ref)
            if (
                cycle_index is not None
                and cycle_index_by_node.get(target_node_ref) == cycle_index
            ):
                cycle_edge_refs_by_index[cycle_index].append(str(row["edgeRef"]))
        for cycle_index, component in enumerate(cycles):
            cycle_edge_refs = sorted(set(cycle_edge_refs_by_index.get(cycle_index, [])))
            gaps.append(
                _gap(
                    "DATA_CYCLE",
                    graph_ref=graph_ref,
                    node_ref=component[0],
                    detail="A cycle exists in exact data dependencies.",
                    evidence_refs=[graph_ref, *component, *cycle_edge_refs],
                )
            )
        pure_dependencies = []
        for node_ref in sorted(graph_nodes):
            pins = pins_by_node.get(node_ref, [])
            if any(_is_exec(pin) for pin in pins):
                continue
            incoming_nodes = sorted(
                incoming_nodes_by_graph.get(graph_ref, {}).get(node_ref, set())
            )
            pure_dependencies.append(
                {
                    "nodeRef": node_ref,
                    "nodeType": str(
                        node_by_ref.get(node_ref, {}).get("node_type") or ""
                    ),
                    "dependencyNodeRefs": incoming_nodes,
                }
            )
        graph_results.append(
            {
                "graphRef": graph_ref,
                "name": str(graph.get("name") or ""),
                "pins": [
                    _pin_projection(pin)
                    for pin in pins_by_graph.get(graph_ref, [])
                    if not _is_exec(pin)
                ],
                "edges": sorted(
                    [
                        {key: value for key, value in row.items() if key != "graphRef"}
                        for row in graph_edges
                    ],
                    key=lambda row: (
                        str(row["sourcePinRef"]),
                        str(row["targetPinRef"]),
                    ),
                ),
                "pureDependencies": pure_dependencies,
                "cycles": cycles,
            }
        )

    return DataFlowResult(
        graphs=tuple(sorted(graph_results, key=lambda row: str(row["graphRef"]))),
        shared_expressions=tuple(shared_expressions),
        gaps=tuple(sorted(gaps, key=lambda row: str(row["id"]))),
    )


__all__ = ["DataFlowResult", "build_data_flow"]
