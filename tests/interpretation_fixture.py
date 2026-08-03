from __future__ import annotations

from pathlib import Path
from typing import Any

from blueprint_translator.evidence_writer import write_evidence_artifacts_from_payload


def _link(
    target_node: str,
    target_pin_id: str,
    *,
    kind: str,
    status: str = "resolved_pin",
) -> dict[str, object]:
    return {
        "target_node": target_node,
        "target_pin_id": target_pin_id,
        "source": "interpretation_fixture",
        "confidence": "high" if status == "resolved_pin" else "medium",
        "resolution_status": status,
        "status": status,
        "kind": kind,
    }


def _pin(
    pin_id: str,
    name: str,
    direction: str,
    category: str,
    *,
    default: str = "",
    default_object: str = "",
    type_object: str = "",
    links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": pin_id,
        "persistent_guid": pin_id,
        "name": name,
        "direction": direction,
        "category": category,
        "subcategory": "",
        "pin_type": {
            "PinCategory": category,
            "PinSubCategoryObject": type_object,
            "ContainerType": "None",
        },
        "default": default,
        "default_object": default_object,
        "links": links or [],
        "source": "interpretation_fixture",
        "confidence": "high",
    }


def _node(
    index: int,
    name: str,
    node_type: str,
    *,
    label: str = "",
    event: str = "",
    function: str = "",
    variable: str = "",
    delegate: str = "",
    macro: str = "",
    comment: str = "",
    control_kind: str = "",
    properties: list[dict[str, object]] | None = None,
    pins: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "index": index,
        "package_index": 1000 + index,
        "export_index": 999 + index,
        "name": name,
        "label": label or event or function or variable or delegate or macro or name,
        "class_name": node_type,
        "node_type": node_type,
        "event": event,
        "function": function,
        "variable": variable,
        "delegate": delegate,
        "macro": macro,
        "comment": comment,
        "control_kind": control_kind,
        "source": "interpretation_fixture",
        "confidence": "high",
        "properties": properties or [],
        "pins": pins or [],
    }


def _graph(
    name: str,
    export_index: int,
    nodes: list[dict[str, object]],
    *,
    graph_type: str = "Function",
    status: str = "complete",
) -> dict[str, object]:
    pin_count = sum(len(node.get("pins", [])) for node in nodes)
    link_count = sum(
        len(pin.get("links", []))
        for node in nodes
        for pin in node.get("pins", [])
    )
    return {
        "graph": name,
        "graph_type": graph_type,
        "export_index": export_index,
        "status": status,
        "confidence": "high" if status == "complete" else "medium",
        "node_count": len(nodes),
        "pin_count": pin_count,
        "link_count": link_count,
        "coverage": {
            "nodesRecovered": len(nodes),
            "nodesExpected": len(nodes),
            "pinsRecovered": pin_count,
            "pinsExpected": pin_count,
        },
        "warnings": [],
        "payload": {
            "metadata": {
                "asset_name": "InterpretationFixture",
                "graph_name": name,
                "graph_type": graph_type,
                "uasset_export_index": export_index,
                "uasset_read_status": status,
                "confidence": "high" if status == "complete" else "medium",
            },
            "nodes": nodes,
        },
    }


def interpretation_payload(name: str = "InterpretationFixture") -> dict[str, object]:
    event_graph = [
        _node(
            1,
            "EventEntry",
            "K2Node_Event",
            event="ReceiveBeginPlay",
            pins=[
                _pin(
                    "P_EVENT_THEN",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("Sequence", "P_SEQUENCE_EXEC", kind="exec")],
                )
            ],
        ),
        _node(
            2,
            "Sequence",
            "K2Node_ExecutionSequence",
            control_kind="sequence",
            pins=[
                _pin("P_SEQUENCE_EXEC", "execute", "EGPD_Input", "exec"),
                _pin(
                    "P_SEQUENCE_0",
                    "Then 0",
                    "EGPD_Output",
                    "exec",
                    links=[_link("Branch", "P_BRANCH_EXEC", kind="exec")],
                ),
                _pin(
                    "P_SEQUENCE_1",
                    "Then 1",
                    "EGPD_Output",
                    "exec",
                    links=[_link("MacroCall", "P_MACRO_EXEC", kind="exec")],
                ),
            ],
        ),
        _node(
            3,
            "SharedValue",
            "K2Node_VariableGet",
            variable="bShouldRun",
            pins=[
                _pin(
                    "P_SHARED_VALUE",
                    "bShouldRun",
                    "EGPD_Output",
                    "bool",
                    links=[
                        _link("Branch", "P_BRANCH_CONDITION", kind="data"),
                        _link("SetFlag", "P_SET_VALUE", kind="data"),
                        _link(
                            "HeuristicConsumer",
                            "P_HEURISTIC_INPUT",
                            kind="data",
                            status="resolved_pin_heuristic",
                        ),
                        _link(
                            "DuplicateReroute",
                            "P_DUPLICATE_PIN",
                            kind="data",
                            status="ambiguous_target_pin",
                        ),
                        _link(
                            "MissingConsumer",
                            "P_MISSING_INPUT",
                            kind="data",
                            status="cross_graph_or_missing_node",
                        ),
                    ],
                )
            ],
        ),
        _node(
            4,
            "Branch",
            "K2Node_IfThenElse",
            control_kind="branch",
            pins=[
                _pin("P_BRANCH_EXEC", "execute", "EGPD_Input", "exec"),
                _pin("P_BRANCH_CONDITION", "Condition", "EGPD_Input", "bool"),
                _pin(
                    "P_BRANCH_TRUE",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("SetFlag", "P_SET_EXEC", kind="exec")],
                ),
                _pin(
                    "P_BRANCH_FALSE",
                    "else",
                    "EGPD_Output",
                    "exec",
                    links=[_link("Return", "P_RETURN_EXEC", kind="exec")],
                ),
            ],
        ),
        _node(
            5,
            "SetFlag",
            "K2Node_VariableSet",
            variable="bWasRun",
            pins=[
                _pin("P_SET_EXEC", "execute", "EGPD_Input", "exec"),
                _pin(
                    "P_SET_THEN",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("DynamicCast", "P_CAST_EXEC", kind="exec")],
                ),
                _pin("P_SET_VALUE", "bWasRun", "EGPD_Input", "bool", default="false"),
            ],
        ),
        _node(
            6,
            "DynamicCast",
            "K2Node_DynamicCast",
            control_kind="cast",
            pins=[
                _pin("P_CAST_EXEC", "execute", "EGPD_Input", "exec"),
                _pin(
                    "P_CAST_SUCCESS",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("UnsafeCall", "P_CALL_EXEC", kind="exec")],
                ),
            ],
        ),
        _node(
            7,
            "UnsafeCall",
            "K2Node_CallFunction",
            function="<script>alert(1)</script>; $(not-a-command)",
            comment="Glide Damage <b>review only</b>",
            pins=[
                _pin("P_CALL_EXEC", "execute", "EGPD_Input", "exec"),
                _pin(
                    "P_CALL_TYPED_OBJECT",
                    "TypedObjectWithoutDefault",
                    "EGPD_Input",
                    "object",
                    type_object="/Script/Engine.Actor",
                ),
                _pin(
                    "P_CALL_DEFAULT_OBJECT",
                    "ObjectWithDefault",
                    "EGPD_Input",
                    "object",
                    default_object="/Game/Test/DefaultObject.DefaultObject",
                    type_object="/Script/Engine.Actor",
                ),
                _pin(
                    "P_CALL_IMPURE_RESULT",
                    "ImpureResult",
                    "EGPD_Output",
                    "bool",
                    links=[
                        _link("ImpureConsumerA", "P_IMPURE_A", kind="data"),
                        _link("ImpureConsumerB", "P_IMPURE_B", kind="data"),
                    ],
                ),
                _pin(
                    "P_CALL_THEN",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("Delegate", "P_DELEGATE_EXEC", kind="exec")],
                ),
            ],
        ),
        _node(
            8,
            "Delegate",
            "K2Node_CallDelegate",
            delegate="OnInterpretationReady_准备🔥",
            pins=[
                _pin("P_DELEGATE_EXEC", "execute", "EGPD_Input", "exec"),
                _pin(
                    "P_DELEGATE_THEN",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[
                        _link("LocalHelperCall", "P_LOCAL_CALL_EXEC", kind="exec")
                    ],
                ),
            ],
        ),
        _node(
            11,
            "LocalHelperCall",
            "K2Node_CallFunction",
            function="LocalHelper",
            properties=[
                {
                    "name": "FunctionReference",
                    "member_parent_object_path": f"/Game/Test/{name}.{name}",
                    "member_graph_export_index": 8,
                    "confidence": "high",
                }
            ],
            pins=[
                _pin("P_LOCAL_CALL_EXEC", "execute", "EGPD_Input", "exec"),
                _pin(
                    "P_LOCAL_CALL_THEN",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("Return", "P_RETURN_EXEC", kind="exec")],
                ),
            ],
        ),
        _node(
            9,
            "MacroCall",
            "K2Node_MacroInstance",
            macro="ForEachLoop",
            pins=[
                _pin("P_MACRO_EXEC", "execute", "EGPD_Input", "exec"),
                _pin(
                    "P_MACRO_COMPLETED",
                    "Completed",
                    "EGPD_Output",
                    "exec",
                    links=[_link("Return", "P_RETURN_EXEC", kind="exec")],
                ),
            ],
        ),
        _node(
            10,
            "Return",
            "K2Node_FunctionResult",
            control_kind="return",
            pins=[_pin("P_RETURN_EXEC", "execute", "EGPD_Input", "exec")],
        ),
        _node(
            12,
            "DuplicateReroute",
            "K2Node_Knot",
            label="重定向节点一 🔥 <tag>",
            comment="**Markdown** $(shell) is inert evidence text",
            pins=[
                _pin(
                    "P_DUPLICATE_PIN",
                    "Input",
                    "EGPD_Input",
                    "bool",
                )
            ],
        ),
        _node(
            13,
            "DuplicateReroute",
            "K2Node_Knot",
            label="重定向节点二",
            pins=[
                _pin(
                    "P_DUPLICATE_PIN",
                    "Input",
                    "EGPD_Input",
                    "bool",
                )
            ],
        ),
        _node(
            14,
            "HeuristicConsumer",
            "K2Node_Knot",
            pins=[
                _pin(
                    "P_HEURISTIC_INPUT",
                    "Input",
                    "EGPD_Input",
                    "bool",
                )
            ],
        ),
        _node(
            15,
            "ImpureConsumerA",
            "K2Node_Knot",
            pins=[_pin("P_IMPURE_A", "Input", "EGPD_Input", "bool")],
        ),
        _node(
            16,
            "ImpureConsumerB",
            "K2Node_Knot",
            pins=[_pin("P_IMPURE_B", "Input", "EGPD_Input", "bool")],
        ),
        _node(
            17,
            "UnknownCallWithoutName",
            "K2Node_CallFunction",
            pins=[
                _pin("P_UNKNOWN_CALL_EXEC", "execute", "EGPD_Input", "exec"),
                _pin("P_UNKNOWN_CALL_THEN", "then", "EGPD_Output", "exec"),
            ],
        ),
    ]

    local_helper = [
        _node(
            20,
            "HelperEntry",
            "K2Node_FunctionEntry",
            event="LocalHelper",
            pins=[
                _pin(
                    "P_HELPER_THEN",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("HelperReturn", "P_HELPER_RETURN", kind="exec")],
                )
            ],
        ),
        _node(
            21,
            "HelperReturn",
            "K2Node_FunctionResult",
            pins=[_pin("P_HELPER_RETURN", "execute", "EGPD_Input", "exec")],
        ),
    ]

    cycle_graph = [
        _node(
            30,
            "CycleEntry",
            "K2Node_FunctionEntry",
            event="CycleEntry",
            pins=[
                _pin(
                    "P_CYCLE_ENTRY",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("CycleBody", "P_CYCLE_IN", kind="exec")],
                )
            ],
        ),
        _node(
            31,
            "CycleBody",
            "K2Node_CallFunction",
            function="TickCycle",
            pins=[
                _pin("P_CYCLE_IN", "execute", "EGPD_Input", "exec"),
                _pin(
                    "P_CYCLE_OUT",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[_link("CycleBody", "P_CYCLE_IN", kind="exec")],
                ),
            ],
        ),
    ]

    collapsed_graph = [
        _node(
            40,
            "CollapsedGraphNode",
            "K2Node_Composite",
            label="Collapsed <Graph>",
            comment="body is intentionally unavailable",
        )
    ]

    missing_entry_graph = [
        _node(
            50,
            "OrphanCall",
            "K2Node_CallFunction",
            function="ExternalWithoutEntry",
            pins=[
                _pin(
                    "P_ORPHAN_THEN",
                    "then",
                    "EGPD_Output",
                    "exec",
                    links=[
                        _link(
                            "OrphanReturn",
                            "P_ORPHAN_RETURN",
                            kind="exec",
                            status="resolved_pin_heuristic",
                        )
                    ],
                )
            ],
        ),
        _node(
            51,
            "OrphanReturn",
            "K2Node_FunctionResult",
            pins=[_pin("P_ORPHAN_RETURN", "execute", "EGPD_Input", "exec")],
        ),
    ]

    return {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": [
            _graph("EventGraph", 7, event_graph, graph_type="EventGraph"),
            _graph("LocalHelper", 8, local_helper),
            _graph("CycleGraph", 9, cycle_graph),
            _graph("CollapsedGraph", 10, collapsed_graph, status="partial"),
            _graph("MissingEntryGraph", 11, missing_entry_graph, status="partial"),
        ],
        "class_defaults": {
            "variables": {
                "DefaultThreshold": {
                    "value": 2.5,
                    "type": "FloatProperty",
                    "source": "interpretation_fixture",
                    "confidence": "high",
                }
            }
        },
    }


def large_interpretation_payload(
    name: str = "LargeInterpretationFixture",
    *,
    node_count: int = 100,
) -> dict[str, object]:
    nodes = [
        _node(
            10_000 + index,
            f"LargePureNode_{index:04d}",
            "K2Node_Knot",
            pins=[
                _pin(
                    f"P_LARGE_{index:04d}",
                    "Input",
                    "EGPD_Input",
                    "bool",
                )
            ],
        )
        for index in range(node_count)
    ]
    return {
        "asset_name": name,
        "asset_path": f"/Game/Test/{name}.{name}",
        "graphs": [_graph("LargePureGraph", 70, nodes)],
        "class_defaults": {"variables": {}},
    }


def publish_interpretation_fixture(
    root: Path,
    *,
    name: str = "InterpretationFixture",
    payload: dict[str, Any] | None = None,
) -> tuple[Path, Path, dict[str, object]]:
    asset_dir = root / name
    source_path = asset_dir / "source" / f"{name}.uasset"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(("fixture-package:" + name).encode("utf-8"))
    selected_payload = payload or interpretation_payload(name)
    write_evidence_artifacts_from_payload(
        str(selected_payload["asset_path"]),
        source_path,
        selected_payload,
        asset_dir,
        publish_v3=True,
    )
    return asset_dir, source_path, selected_payload
