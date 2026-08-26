"""Reconstruct event-receiver-to-spawn routes over authoritative exec edges."""

from __future__ import annotations

import sqlite3
from collections import deque

from .runtime_signals import discover_runtime_signals


def _receiver_rows(
    connection: sqlite3.Connection,
    event_name: str,
) -> list[sqlite3.Row]:
    expected = event_name.casefold()
    return [
        row
        for row in connection.execute(
            "SELECT n.node_ref, n.graph_ref, n.name AS node_name, n.event_name, "
            "n.package_index, g.name AS graph_name "
            "FROM nodes n JOIN graphs g ON g.graph_ref = n.graph_ref "
            "WHERE n.class_name = 'K2Node_CustomEvent' "
            "ORDER BY n.graph_ref, n.node_ref"
        ).fetchall()
        if str(row["event_name"] or "").casefold() == expected
    ]


def _exec_adjacency(
    connection: sqlite3.Connection,
    graph_ref: str,
) -> dict[str, list[tuple[str, str]]]:
    adjacency: dict[str, list[tuple[str, str]]] = {}
    rows = connection.execute(
        "SELECT e.edge_ref, source_node.node_ref AS source_node_ref, "
        "target_node.node_ref AS target_node_ref "
        "FROM edges e "
        "JOIN pins source_pin ON source_pin.pin_ref = e.source_pin_ref "
        "JOIN nodes source_node ON source_node.node_ref = source_pin.node_ref "
        "JOIN pins target_pin ON target_pin.pin_ref = e.target_pin_ref "
        "JOIN nodes target_node ON target_node.node_ref = target_pin.node_ref "
        "WHERE e.graph_ref = ? AND lower(e.kind) = 'exec' "
        "ORDER BY e.edge_ref",
        (graph_ref,),
    ).fetchall()
    for row in rows:
        adjacency.setdefault(str(row["source_node_ref"]), []).append(
            (str(row["target_node_ref"]), str(row["edge_ref"]))
        )
    return adjacency


def _reachable_paths(
    receiver_ref: str,
    adjacency: dict[str, list[tuple[str, str]]],
) -> dict[str, dict[str, list[str]]]:
    paths: dict[str, dict[str, list[str]]] = {
        receiver_ref: {"nodeRefs": [receiver_ref], "edgeRefs": []}
    }
    queue: deque[str] = deque([receiver_ref])
    while queue:
        source_ref = queue.popleft()
        source_path = paths[source_ref]
        for target_ref, edge_ref in adjacency.get(source_ref, []):
            if target_ref in paths:
                continue
            paths[target_ref] = {
                "nodeRefs": [*source_path["nodeRefs"], target_ref],
                "edgeRefs": [*source_path["edgeRefs"], edge_ref],
            }
            queue.append(target_ref)
    return paths


def _route_gap(
    receiver: sqlite3.Row,
    *,
    reason_code: str,
    detail: str,
    spawn_signal: dict[str, object] | None = None,
    exec_path: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    receiver_ref = str(receiver["node_ref"])
    spawn_ref = str((spawn_signal or {}).get("nodeRef") or "")
    suffix = spawn_ref.rsplit("/", 1)[-1] if spawn_ref else "no-spawn"
    edge_refs = list((exec_path or {}).get("edgeRefs", []))
    evidence_refs = [receiver_ref, *edge_refs]
    if spawn_ref:
        evidence_refs.append(spawn_ref)
    class_pin_ref = str((spawn_signal or {}).get("valuePinRef") or "")
    if class_pin_ref:
        evidence_refs.append(class_pin_ref)
    item: dict[str, object] = {
        "ref": f"{receiver_ref}/runtime-route/{suffix}",
        "kind": "runtimeRouteGap",
        "routeKind": "global_event_receiver_to_spawn",
        "status": "NOT_RECOVERED",
        "reasonCode": reason_code,
        "detail": detail,
        "eventName": str(receiver["event_name"]),
        "graphRef": str(receiver["graph_ref"]),
        "graphName": str(receiver["graph_name"]),
        "receiverNodeRef": receiver_ref,
        "evidenceRefs": evidence_refs,
    }
    if spawn_ref:
        item["spawnNodeRef"] = spawn_ref
    if exec_path is not None:
        item["execPath"] = exec_path
        item["pathAuthority"] = "EXACT_NORMALIZED_EXEC_EDGES"
    return item


def _confirmed_route(
    receiver: sqlite3.Row,
    spawn_signal: dict[str, object],
    exec_path: dict[str, list[str]],
) -> dict[str, object]:
    receiver_ref = str(receiver["node_ref"])
    spawn_ref = str(spawn_signal["nodeRef"])
    class_pin_ref = str(spawn_signal["valuePinRef"])
    edge_refs = list(exec_path["edgeRefs"])
    return {
        "ref": f"{receiver_ref}/runtime-route/{spawn_ref.rsplit('/', 1)[-1]}",
        "kind": "runtimeRoute",
        "routeKind": "global_event_receiver_to_spawn",
        "status": "CONFIRMED",
        "eventName": str(receiver["event_name"]),
        "receiverBinding": {
            "mechanism": "CUSTOM_EVENT_NAME",
            "nameAuthority": "EXACT_SERIALIZED_EVENT_NAME",
        },
        "graphRef": str(receiver["graph_ref"]),
        "graphName": str(receiver["graph_name"]),
        "receiverNodeRef": receiver_ref,
        "spawnNodeRef": spawn_ref,
        "classPinRef": class_pin_ref,
        "actorClass": str(spawn_signal["actorClass"]),
        "execPath": exec_path,
        "pathAuthority": "EXACT_NORMALIZED_EXEC_EDGES",
        "evidenceRefs": [
            receiver_ref,
            *edge_refs,
            spawn_ref,
            class_pin_ref,
        ],
    }


def discover_runtime_routes(
    connection: sqlite3.Connection,
    event_name: str,
) -> dict[str, object]:
    """Find exact custom-event receivers and exec-reachable SpawnActor nodes."""

    receivers = _receiver_rows(connection, event_name)
    spawn_signals = {
        str(item.get("nodeRef")): item
        for item in discover_runtime_signals(connection)
        if item.get("signalKind") == "spawn_actor"
    }
    spawn_rows = connection.execute(
        "SELECT node_ref, graph_ref FROM nodes "
        "WHERE class_name = 'K2Node_SpawnActorFromClass' "
        "ORDER BY graph_ref, node_ref"
    ).fetchall()
    spawn_refs_by_graph: dict[str, set[str]] = {}
    for row in spawn_rows:
        spawn_refs_by_graph.setdefault(str(row["graph_ref"]), set()).add(
            str(row["node_ref"])
        )
    items: list[dict[str, object]] = []
    adjacency_by_graph: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for receiver in receivers:
        if receiver["package_index"] is None:
            items.append(
                _route_gap(
                    receiver,
                    reason_code="RECEIVER_NODE_IDENTITY_UNAVAILABLE",
                    detail="The matching Custom Event lacks exact package-node identity.",
                )
            )
            continue
        graph_ref = str(receiver["graph_ref"])
        adjacency = adjacency_by_graph.get(graph_ref)
        if adjacency is None:
            adjacency = _exec_adjacency(connection, graph_ref)
            adjacency_by_graph[graph_ref] = adjacency
        paths = _reachable_paths(str(receiver["node_ref"]), adjacency)
        reachable_spawns = sorted(
            spawn_refs_by_graph.get(graph_ref, set()).intersection(paths)
        )
        if not reachable_spawns:
            items.append(
                _route_gap(
                    receiver,
                    reason_code="EVENT_RECEIVER_SPAWN_NOT_RECOVERED",
                    detail="No SpawnActor node is reachable over authoritative exec edges in this graph.",
                )
            )
            continue
        for spawn_ref in reachable_spawns:
            signal = spawn_signals[spawn_ref]
            path = paths[spawn_ref]
            if signal.get("status") == "CONFIRMED":
                items.append(_confirmed_route(receiver, signal, path))
            else:
                items.append(
                    _route_gap(
                        receiver,
                        reason_code=str(
                            signal.get("reasonCode")
                            or "SPAWN_CLASS_NOT_RECOVERED"
                        ),
                        detail=str(
                            signal.get("detail")
                            or "The reachable SpawnActor class is not authoritative."
                        ),
                        spawn_signal=signal,
                        exec_path=path,
                    )
                )
    items.sort(key=lambda item: str(item["ref"]))
    return {
        "items": items,
        "receiverNodes": len(receivers),
        "confirmedReceiverNodes": sum(
            1 for receiver in receivers if receiver["package_index"] is not None
        ),
        "confirmedRoutes": sum(
            1 for item in items if item.get("status") == "CONFIRMED"
        ),
        "routeGaps": sum(
            1 for item in items if item.get("status") != "CONFIRMED"
        ),
    }


__all__ = ["discover_runtime_routes"]
