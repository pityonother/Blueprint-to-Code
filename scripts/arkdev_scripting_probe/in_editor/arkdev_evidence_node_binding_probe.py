"""One-shot, Evidence-guided, read-only exact Blueprint node binding probe."""

from __future__ import annotations

import json
import math
import platform
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from arkdev_scripting_probe.contracts import (  # noqa: E402
    assert_path_free,
    attach_semantic_digest,
    canonical_json,
)
from arkdev_scripting_probe.node_binding import (  # noqa: E402
    MAX_PINS_PER_NODE,
    MAX_PINS_TOTAL,
    RESULT_SCHEMA,
    validate_request,
    validate_result,
)


_HEX_GUID = re.compile(r"^[0-9A-Fa-f]{32}$")
_ZERO_GUID = "0" * 32


def _member(owner: object | None, name: str) -> object | None:
    if owner is None:
        return None
    try:
        return getattr(owner, name, None)
    except Exception:
        return None


def _read_property_result(owner: object, name: str) -> tuple[bool, object | None]:
    try:
        return True, getattr(owner, name)
    except Exception:
        pass
    getter = _member(owner, "get_editor_property")
    if not callable(getter):
        return False, None
    try:
        return True, getter(name)
    except Exception:
        return False, None


def _read_property(owner: object, name: str) -> object | None:
    available, value = _read_property_result(owner, name)
    return value if available else None


def _call_no_args(owner: object, name: str) -> object | None:
    candidate = _member(owner, name)
    if not callable(candidate):
        return None
    try:
        return candidate()
    except Exception:
        return None


def _name(owner: object) -> str:
    value = _call_no_args(owner, "get_name")
    return str(value or "")[:256]


def _path(owner: object) -> str:
    return _full_path(owner)[:4096]


def _full_path(owner: object) -> str:
    """Read an untruncated UObject path for exact identity comparisons."""

    value = _call_no_args(owner, "get_path_name")
    return str(value or "")


def _runtime_class_name(owner: object) -> str:
    class_object = _call_no_args(owner, "get_class")
    if class_object is None:
        return ""
    return _name(class_object)


def _canonical_guid(value: object) -> str:
    candidates: list[str] = []
    if value is not None:
        candidates.append(str(value).strip())
        to_string = _member(value, "to_string")
        if callable(to_string):
            try:
                candidates.append(str(to_string()).strip())
            except Exception:
                pass
    for candidate in candidates:
        compact = re.sub(r"[-{}()]", "", candidate)
        if _HEX_GUID.fullmatch(compact) and compact != _ZERO_GUID:
            return compact.upper()
    components = [_read_property(value, name) for name in ("a", "b", "c", "d")]
    if all(isinstance(item, int) and not isinstance(item, bool) for item in components):
        compact = "".join(f"{int(item) & 0xFFFFFFFF:08X}" for item in components)
        if compact != _ZERO_GUID:
            return compact
    return ""


def _read_guid(node: object) -> str:
    for name in ("node_guid", "NodeGuid"):
        available, value = _read_property_result(node, name)
        if available:
            guid = _canonical_guid(value)
            if guid:
                return guid
    return ""


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _position(library: object, node: object) -> tuple[str, int | None, int | None]:
    get_node_pos = _member(library, "get_node_pos")
    api_error = False
    if callable(get_node_pos):
        try:
            position = get_node_pos(node)
            x = _read_property(position, "x")
            y = _read_property(position, "y")
            if _number(x) and _number(y):
                return "POSITION_AVAILABLE", int(x), int(y)
            api_error = True
        except Exception:
            api_error = True
    x = _read_property(node, "node_pos_x")
    y = _read_property(node, "node_pos_y")
    if _number(x) and _number(y):
        return "POSITION_AVAILABLE", int(x), int(y)
    if api_error:
        return "POSITION_ERROR", None, None
    return "POSITION_UNAVAILABLE", None, None


def _as_list(value: object) -> list[object] | None:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    try:
        return list(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _pin_scalar(value: object) -> object | None:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _pin_projection(pin: object, ordinal: int) -> dict[str, object] | None:
    name = _read_property(pin, "pin_name")
    if name is None:
        name = _name(pin)
    direction = _read_property(pin, "direction")
    pin_type = _read_property(pin, "pin_type")
    category = _read_property(pin_type, "pin_category") if pin_type is not None else None
    if category is None:
        category = _read_property(pin, "pin_category")
    subcategory = None
    if pin_type is not None:
        subcategory = _read_property(pin_type, "pin_sub_category")
        if subcategory is None:
            subcategory = _read_property(pin_type, "pin_subcategory")
    normalized_name = str(name or "")[:256]
    normalized_direction = str(direction or "")[:256]
    if not normalized_name or not normalized_direction:
        return None
    projected: dict[str, object] = {
        "name": normalized_name,
        "direction": normalized_direction,
        "ordinal": ordinal,
    }
    if category is not None:
        projected["category"] = str(category)[:256]
    if subcategory is not None:
        projected["subcategory"] = str(subcategory)[:256]
    for property_name in ("default_value", "default_text_value", "default_object"):
        available, value = _read_property_result(pin, property_name)
        if not available:
            continue
        scalar = _pin_scalar(value)
        if scalar is not None:
            projected["default"] = scalar
            break
    return projected


def _pin_values(library: object, node: object) -> tuple[list[object] | None, bool]:
    observed_error = False
    library_method = _member(library, "list_all_pins")
    if callable(library_method):
        try:
            values = _as_list(library_method(node))
        except Exception:
            observed_error = True
        else:
            if values is not None:
                return values, observed_error
            observed_error = True

    node_method = _member(node, "list_all_pins")
    if callable(node_method):
        try:
            values = _as_list(node_method())
        except Exception:
            observed_error = True
        else:
            if values is not None:
                return values, observed_error
            observed_error = True

    split_methods = [
        _member(node, method_name)
        for method_name in ("list_input_pins", "list_output_pins")
    ]
    if any(callable(method) for method in split_methods):
        if not all(callable(method) for method in split_methods):
            observed_error = True
        else:
            split_values: list[object] = []
            split_complete = True
            for method in split_methods:
                try:
                    values = _as_list(method())  # type: ignore[operator]
                except Exception:
                    observed_error = True
                    split_complete = False
                    break
                if values is None:
                    observed_error = True
                    split_complete = False
                    break
                split_values.extend(values)
            if split_complete:
                return split_values, observed_error

    available, raw_pins = _read_property_result(node, "pins")
    if available:
        values = _as_list(raw_pins)
        if values is not None:
            return values, observed_error
        observed_error = True
    return None, observed_error


def _pin_read(
    library: object,
    node: object,
    request_node: Mapping[str, object],
    *,
    remaining_total: int,
) -> tuple[dict[str, object], list[str], int]:
    values, observed_error = _pin_values(library, node)
    if values is None:
        gaps = ["PIN_READ_ERROR"] if observed_error else []
        return {
            "pinRead": "UNAVAILABLE",
            "pinBindingStatus": "UNAVAILABLE",
        }, gaps, 0

    per_node_returned = min(len(values), MAX_PINS_PER_NODE)
    allowed_count = min(per_node_returned, max(remaining_total, 0))
    projected: list[dict[str, object]] = []
    projection_incomplete = False
    for ordinal, pin in enumerate(values[:allowed_count]):
        item = _pin_projection(pin, ordinal)
        if item is None:
            projection_incomplete = True
            break
        projected.append(item)
    returned_count = len(projected)
    omitted = len(values) - returned_count
    partial = omitted > 0
    gaps: list[str] = []
    if len(values) > MAX_PINS_PER_NODE:
        gaps.append("PIN_PER_NODE_LIMIT_REACHED")
    if per_node_returned > remaining_total:
        gaps.append("PIN_TOTAL_LIMIT_REACHED")
    if projection_incomplete:
        gaps.append("PIN_PROJECTION_INCOMPLETE")
    result: dict[str, object] = {
        "pinRead": "PARTIAL" if partial else "PASS",
        "pinBindingStatus": "UNAVAILABLE",
        "pins": projected,
        "pinsReturned": returned_count,
        "pinsOmitted": omitted,
        "pinsTruncated": partial,
    }
    evidence_pins = request_node.get("evidencePins")
    if not partial and isinstance(evidence_pins, Sequence) and not isinstance(
        evidence_pins, (str, bytes, bytearray)
    ):
        live_signature = [
            (pin.get("name"), pin.get("direction"), pin.get("ordinal"))
            for pin in projected
        ]
        evidence_signature = [
            (pin.get("name"), pin.get("direction"), pin.get("ordinal"))
            for pin in evidence_pins
            if isinstance(pin, Mapping)
        ]
        matches = live_signature == evidence_signature
        result["pinSignatureMatches"] = matches
        result["pinBindingStatus"] = "SIGNATURE_ONLY"
    return result, gaps, returned_count


def _load_asset(unreal_module: object, object_path: str) -> tuple[object | None, bool]:
    completed = False
    editor_assets = _member(unreal_module, "EditorAssetLibrary")
    for owner in (unreal_module, editor_assets):
        load_asset = _member(owner, "load_asset")
        if not callable(load_asset):
            continue
        try:
            asset = load_asset(object_path)
        except Exception:
            continue
        completed = True
        if asset is not None:
            return asset, True
    return None, completed


def _blueprint(library: object, asset: object) -> tuple[object | None, bool]:
    getter = _member(library, "get_blueprint_asset")
    if not callable(getter):
        return None, False
    try:
        return getter(asset), True
    except Exception:
        return None, False


def _graph(
    library: object,
    blueprint: object,
    graph_name: str,
) -> tuple[object | None, bool]:
    completed = False
    finder = _member(library, "find_graph")
    if callable(finder):
        try:
            graph = finder(blueprint, graph_name)
        except Exception:
            graph = None
        else:
            completed = True
        if graph is not None:
            return graph, True
    if graph_name == "EventGraph":
        finder = _member(library, "find_event_graph")
        if callable(finder):
            try:
                graph = finder(blueprint)
            except Exception:
                graph = None
            else:
                completed = True
            if graph is not None:
                return graph, True
    return None, completed


def _lookup_call(
    callable_object: object,
    graph: object,
    object_name: str,
    node_class: object | None,
) -> tuple[object | None, bool]:
    if not callable(callable_object):
        return None, False
    if node_class is not None:
        try:
            return callable_object(graph, object_name, node_class), True
        except TypeError:
            pass
        except Exception:
            return None, False
    try:
        return callable_object(graph, object_name), True
    except Exception:
        return None, False


def _exact_outer(unreal_module: object, node: object, graph: object) -> tuple[bool, str]:
    outer_getter = _member(node, "get_outer")
    if not callable(outer_getter):
        return False, ""
    try:
        outer = outer_getter()
    except Exception:
        return False, ""
    outer_full_path = _full_path(outer) if outer is not None else ""
    outer_path = outer_full_path if len(outer_full_path) <= 4096 else ""
    if outer is graph:
        return True, outer_path
    ed_graph = _member(unreal_module, "EdGraph")
    if (
        not isinstance(ed_graph, type)
        or outer is None
        or not isinstance(outer, ed_graph)
        or not isinstance(graph, ed_graph)
    ):
        return False, outer_path
    graph_full_path = _full_path(graph)
    return (
        bool(
            outer_full_path
            and graph_full_path
            and outer_full_path == graph_full_path
        ),
        outer_path,
    )


def _empty_node(request_node: Mapping[str, object], gap: str) -> dict[str, object]:
    result: dict[str, object] = {
        "nodeRef": request_node["nodeRef"],
        "objectName": request_node["objectName"],
        "expectedClassName": request_node["expectedClassName"],
        "expectedNodeGuid": request_node["expectedNodeGuid"],
        "lookupMethod": "NONE",
        "bindingStatus": "NOT_FOUND",
        "positionStatus": "NOT_TESTED",
        "pinRead": "NOT_TESTED",
        "pinBindingStatus": "UNAVAILABLE",
        "gaps": [gap],
    }
    if "evidenceX" in request_node:
        result["evidenceX"] = request_node["evidenceX"]
        result["evidenceY"] = request_node["evidenceY"]
    return result


def _engine_version(unreal_module: object | None) -> str:
    system = _member(unreal_module, "SystemLibrary")
    getter = _member(system, "get_engine_version")
    if callable(getter):
        try:
            value = str(getter() or "")
        except Exception:
            value = ""
        if value:
            return value[:128]
    return "UNKNOWN"


def _route(
    asset_status: str,
    graph_status: str,
    nodes: Sequence[Mapping[str, object]],
) -> str:
    exact = sum(node.get("bindingStatus") == "EXACT" for node in nodes)
    if asset_status != "EXACT" or graph_status != "EXACT" or exact == 0:
        return "NODE_BINDING_UNAVAILABLE"
    if exact != len(nodes):
        return "PARTIAL_NODE_BINDING"
    if all(node.get("pinRead") in {"UNAVAILABLE", "NOT_TESTED"} for node in nodes):
        return "EXACT_NODE_BINDING_PIN_UNAVAILABLE"
    # Phase 3C can compare Pin signatures but cannot bind live Pin identity.
    # EXACT_NODE_AND_PIN_BINDING remains reserved for a future identity adapter.
    return "EXACT_NODE_BINDING_PIN_PARTIAL"


def collect_node_binding_result(
    unreal_module: object | None,
    request: Mapping[str, object],
    *,
    generated_at: str,
    python_version: str,
) -> dict[str, object]:
    """Resolve only the request's existing nodes; never enumerate or mutate."""

    validated = validate_request(request)
    request_nodes = validated["nodes"]
    assert isinstance(request_nodes, Sequence)
    object_path = str(validated["asset"]["objectPath"])
    graph_name = str(validated["graph"]["name"])
    find_object = _member(unreal_module, "find_object")
    load_object = _member(unreal_module, "load_object")
    ed_graph_node = _member(unreal_module, "EdGraphNode")
    k2_node = _member(unreal_module, "K2Node")
    runtime: dict[str, object] = {
        "engineVersion": _engine_version(unreal_module),
        "pythonVersion": str(python_version or "UNKNOWN")[:64],
        "edGraphNodeClassAvailable": ed_graph_node is not None,
        "k2NodeClassAvailable": k2_node is not None,
    }
    top_gaps: list[str] = []
    asset_status = "NOT_FOUND"
    graph_status = "NOT_FOUND"
    result_nodes: list[dict[str, object]] = []
    asset_path = ""
    graph_path = ""

    if unreal_module is None:
        asset, asset_read_completed = None, False
    else:
        asset, asset_read_completed = _load_asset(unreal_module, object_path)
    if asset is None:
        asset_status = "NOT_FOUND" if asset_read_completed else "ERROR"
        asset_gap = (
            "EXPLICIT_ASSET_NOT_FOUND"
            if asset_read_completed
            else "EXPLICIT_ASSET_LOAD_ERROR"
        )
        top_gaps.append(asset_gap)
        result_nodes = [
            _empty_node(node, asset_gap)
            for node in request_nodes
            if isinstance(node, Mapping)
        ]
    else:
        asset_path = _full_path(asset)
        if not asset_path or asset_path != object_path:
            asset_status = "ERROR"
            top_gaps.append("EXPLICIT_ASSET_PATH_MISMATCH")
            result_nodes = [
                _empty_node(node, "EXPLICIT_ASSET_PATH_MISMATCH")
                for node in request_nodes
                if isinstance(node, Mapping)
            ]
        else:
            asset_status = "EXACT"
            library = _member(unreal_module, "BlueprintEditorLibrary")
            blueprint, blueprint_read_completed = _blueprint(library, asset)
            if blueprint is None:
                graph_status = "NOT_FOUND" if blueprint_read_completed else "ERROR"
                graph_gap = (
                    "EXPLICIT_BLUEPRINT_NOT_FOUND"
                    if blueprint_read_completed
                    else "EXPLICIT_BLUEPRINT_READ_ERROR"
                )
                top_gaps.append(graph_gap)
                result_nodes = [
                    _empty_node(node, graph_gap)
                    for node in request_nodes
                    if isinstance(node, Mapping)
                ]
            else:
                graph, graph_read_completed = _graph(library, blueprint, graph_name)
                if graph is None:
                    graph_status = "NOT_FOUND" if graph_read_completed else "ERROR"
                    graph_gap = (
                        "EXPLICIT_GRAPH_NOT_FOUND"
                        if graph_read_completed
                        else "EXPLICIT_GRAPH_READ_ERROR"
                    )
                    top_gaps.append(graph_gap)
                    result_nodes = [
                        _empty_node(node, graph_gap)
                        for node in request_nodes
                        if isinstance(node, Mapping)
                    ]
                else:
                    graph_path = _full_path(graph)
                    if (
                        _name(graph) != graph_name
                        or not graph_path
                        or len(graph_path) > 4096
                    ):
                        graph_status = "ERROR"
                        top_gaps.append("EXPLICIT_GRAPH_IDENTITY_MISMATCH")
                        result_nodes = [
                            _empty_node(node, "EXPLICIT_GRAPH_IDENTITY_MISMATCH")
                            for node in request_nodes
                            if isinstance(node, Mapping)
                        ]
                    else:
                        graph_status = "EXACT"
                        pins_returned_total = 0
                        for request_node in request_nodes:
                            assert isinstance(request_node, Mapping)
                            node_result = _empty_node(request_node, "NODE_NOT_FOUND")
                            node_result["gaps"] = []
                            if ed_graph_node is None:
                                node_result["gaps"].append(
                                    "ED_GRAPH_NODE_CLASS_UNAVAILABLE"
                                )
                                top_gaps.extend(node_result["gaps"])
                                result_nodes.append(node_result)
                                continue

                            object_name = str(request_node["objectName"])
                            runtime.setdefault("findObjectAvailable", False)
                            if callable(find_object):
                                node_result["lookupMethod"] = "FIND_OBJECT"
                            candidate, find_completed = _lookup_call(
                                find_object, graph, object_name, ed_graph_node
                            )
                            runtime["findObjectAvailable"] = bool(
                                runtime["findObjectAvailable"]
                            ) or find_completed
                            if candidate is None and find_completed:
                                runtime.setdefault("loadObjectAvailable", False)
                                if callable(load_object):
                                    node_result["lookupMethod"] = "LOAD_OBJECT"
                                candidate, load_completed = _lookup_call(
                                    load_object, graph, object_name, ed_graph_node
                                )
                                runtime["loadObjectAvailable"] = bool(
                                    runtime["loadObjectAvailable"]
                                ) or load_completed
                                if candidate is None and not load_completed:
                                    node_result["gaps"].append("LOAD_OBJECT_ERROR")
                            elif candidate is None:
                                node_result["gaps"].append("FIND_OBJECT_ERROR")

                            if candidate is None:
                                node_result["gaps"].append("NODE_NOT_FOUND")
                                node_result["gaps"] = sorted(
                                    set(node_result["gaps"])
                                )
                                top_gaps.extend(node_result["gaps"])
                                result_nodes.append(node_result)
                                continue

                            actual_name = _name(candidate)
                            actual_path = _path(candidate)
                            actual_class = _runtime_class_name(candidate)
                            actual_guid = _read_guid(candidate)
                            outer_matches, outer_path = _exact_outer(
                                unreal_module, candidate, graph
                            )
                            if actual_name:
                                node_result["actualObjectName"] = actual_name
                            if actual_path:
                                node_result["actualObjectPath"] = actual_path
                            if actual_class:
                                node_result["actualClassName"] = actual_class
                            if actual_guid:
                                node_result["actualNodeGuid"] = actual_guid
                            if outer_path:
                                node_result["outerPath"] = outer_path
                            node_result["outerMatches"] = outer_matches

                            if not outer_matches:
                                node_result["bindingStatus"] = "OUTER_MISMATCH"
                                node_result["gaps"].append("OUTER_MISMATCH")
                            elif actual_class != request_node["expectedClassName"]:
                                node_result["bindingStatus"] = "CLASS_MISMATCH"
                                node_result["gaps"].append("CLASS_MISMATCH")
                            elif actual_guid != request_node["expectedNodeGuid"]:
                                node_result["bindingStatus"] = "GUID_MISMATCH"
                                node_result["gaps"].append("GUID_MISMATCH")
                            else:
                                node_result["bindingStatus"] = "EXACT"
                                position_status, live_x, live_y = _position(
                                    library, candidate
                                )
                                node_result["positionStatus"] = position_status
                                if live_x is not None and live_y is not None:
                                    node_result["liveX"] = live_x
                                    node_result["liveY"] = live_y
                                    if "evidenceX" in request_node:
                                        node_result["positionMatches"] = (
                                            live_x == request_node["evidenceX"]
                                            and live_y == request_node["evidenceY"]
                                        )
                                elif position_status == "POSITION_ERROR":
                                    node_result["gaps"].append("POSITION_READ_ERROR")
                                else:
                                    node_result["gaps"].append(
                                        "POSITION_UNAVAILABLE"
                                    )
                                pin_result, pin_gaps, returned = _pin_read(
                                    library,
                                    candidate,
                                    request_node,
                                    remaining_total=(
                                        MAX_PINS_TOTAL - pins_returned_total
                                    ),
                                )
                                pins_returned_total += returned
                                node_result.update(pin_result)
                                node_result["gaps"].extend(pin_gaps)
                            node_result["gaps"] = sorted(
                                set(node_result["gaps"])
                            )
                            top_gaps.extend(node_result["gaps"])
                            result_nodes.append(node_result)

    summary = {
        "requested": len(result_nodes),
        "exact": sum(node["bindingStatus"] == "EXACT" for node in result_nodes),
        "notFound": sum(node["bindingStatus"] == "NOT_FOUND" for node in result_nodes),
        "outerMismatch": sum(
            node["bindingStatus"] == "OUTER_MISMATCH" for node in result_nodes
        ),
        "classMismatch": sum(
            node["bindingStatus"] == "CLASS_MISMATCH" for node in result_nodes
        ),
        "guidMismatch": sum(
            node["bindingStatus"] == "GUID_MISMATCH" for node in result_nodes
        ),
        "positionAvailable": sum(
            node["positionStatus"] == "POSITION_AVAILABLE" for node in result_nodes
        ),
        "pinReadable": sum(
            node["pinRead"] in {"PASS", "PARTIAL"} for node in result_nodes
        ),
    }
    result: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "requestId": validated["requestId"],
        "requestSemanticDigest": validated["semanticDigest"],
        "generatedAt": generated_at,
        "runtime": runtime,
        "assetStatus": asset_status,
        "graphStatus": graph_status,
        "nodes": result_nodes,
        "summary": summary,
        "route": _route(asset_status, graph_status, result_nodes),
        "mutationReady": False,
        "objectIteratorCalled": False,
        "mutationApiCalled": False,
        "gaps": sorted(set(top_gaps)),
    }
    if asset_status == "EXACT":
        result["assetObjectPath"] = asset_path
    if graph_status == "EXACT":
        result["graphPath"] = graph_path
    attach_semantic_digest(result)
    assert_path_free(result)
    validate_result(result, request=validated)
    return result


def _load_json(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, Mapping):
        raise ValueError("request must be a JSON object")
    return value


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    output_root = repo_root / ".arkdev-probe"
    request_path = output_root / "node-binding-request.json"
    result_path = output_root / "node-binding-result.json"
    try:
        if result_path.is_file():
            result_path.unlink()
        import unreal  # type: ignore[import-not-found]

        request = _load_json(request_path)
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result = collect_node_binding_result(
            unreal,
            request,
            generated_at=generated_at,
            python_version=platform.python_version(),
        )
        output_root.mkdir(parents=True, exist_ok=True)
        result_path.write_text(canonical_json(result) + "\n", encoding="utf-8")
    except Exception:
        print("ARKDEV_EVIDENCE_NODE_BINDING=ERROR")
        return 2
    print("ARKDEV_EVIDENCE_NODE_BINDING=COMPLETE")
    print(f"ASSET_STATUS={result['assetStatus']}")
    print(f"GRAPH_STATUS={result['graphStatus']}")
    print(f"NODE_BINDING_ROUTE={result['route']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["collect_node_binding_result", "main"]
