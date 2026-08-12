"""One-shot, explicit-target, read-only Blueprint graph snapshot."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from arkdev_scripting_probe.contracts import (
    MAX_GRAPH_NODES,
    MAX_OBJECTS_SCANNED,
    REQUEST_SCHEMA,
    SNAPSHOT_SCHEMA,
    assert_path_free,
    attach_semantic_digest,
)


_HEX_GUID = re.compile(r"^[0-9A-Fa-f]{32}$")
_ZERO_GUID = "0" * 32
_COMPILE_STATUSES = (
    "BS_UP_TO_DATE_WITH_WARNINGS",
    "BS_UP_TO_DATE",
    "BS_DIRTY",
    "BS_ERROR",
    "BS_UNKNOWN",
)
_ENUMERATION_STRATEGIES = {
    "DIRECT_GRAPH_PROPERTY",
    "OBJECT_ITERATOR_EDGRAPHNODE_EXACT_OUTER",
    "OBJECT_ITERATOR_K2NODE_EXACT_OUTER",
}


def _status_map() -> dict[str, str]:
    return {
        "explicitAssetLoad": "NOT_TESTED",
        "blueprintObject": "NOT_TESTED",
        "explicitGraphFind": "NOT_TESTED",
        "graphNodeEnumeration": "NOT_TESTED",
        "nodeGuidRead": "NOT_TESTED",
        "nodePositionRead": "NOT_TESTED",
        "directGraphProperty": "NOT_TESTED",
        "objectIteratorEdGraphNode": "NOT_TESTED",
        "objectIteratorK2Node": "NOT_TESTED",
        "exactOuterNodeEnumeration": "NOT_TESTED",
        "exactTypedOuterNodeEnumeration": "NOT_TESTED",
        "objectGetOuter": "NOT_TESTED",
        "objectGetTypedOuter": "NOT_TESTED",
        "objectGetPathName": "NOT_TESTED",
        "blueprintListGraphs": "NOT_TESTED",
        "blueprintListGraphNames": "NOT_TESTED",
        "nodeGetPos": "NOT_TESTED",
        "nodeListAllPins": "NOT_TESTED",
        "nodePinRead": "NOT_TESTED",
        "blueprintStatusRead": "NOT_TESTED",
        "packageDirtyRead": "NOT_TESTED",
    }


def _target(
    request: Mapping[str, object],
    *,
    status: str = "NOT_TESTED",
) -> dict[str, object]:
    return {
        "status": status,
        "objectPath": str(request.get("objectPath") or ""),
        "graphName": str(request.get("graphName") or ""),
        "capabilities": _status_map(),
        "objectsScanned": 0,
        "matchingNodes": 0,
        "returnedNodes": 0,
        "nodesOmitted": 0,
        "scanTruncated": False,
        "enumerationStrategy": "NONE",
        "compileStatus": "BS_UNKNOWN",
        "gaps": [],
    }


def empty_explicit_target(
    *,
    status: str,
    gap: str,
    request: Mapping[str, object] | None = None,
) -> dict[str, object]:
    target = _target(request or {}, status=status)
    gaps = target["gaps"]
    assert isinstance(gaps, list)
    gaps.append(gap)
    return target


def _member(owner: object, name: str) -> object | None:
    try:
        return getattr(owner, name, None)
    except Exception:
        return None


def _call_no_args(owner: object, name: str) -> object | None:
    candidate = _member(owner, name)
    if not callable(candidate):
        return None
    try:
        return candidate()
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


def _name(owner: object) -> str:
    value = _call_no_args(owner, "get_name")
    if value is None:
        value = _read_property(owner, "name")
    return str(value or "")[:256]


def _class_name(owner: object) -> str:
    class_object = _call_no_args(owner, "get_class")
    if class_object is None:
        return type(owner).__name__[:256]
    return (_name(class_object) or type(owner).__name__)[:256]


def _full_path_name_result(owner: object) -> tuple[str, str]:
    candidate = _member(owner, "get_path_name")
    if not callable(candidate):
        return "MISSING", ""
    try:
        value = candidate()
    except Exception:
        return "ERROR", ""
    return "AVAILABLE", str(value or "")


def _full_path_name(owner: object) -> str:
    _status, value = _full_path_name_result(owner)
    return value


def _path_name(owner: object) -> str:
    return _full_path_name(owner)[:4096]


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
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _position(
    library: object,
    node: object,
) -> tuple[tuple[int, int] | None, str]:
    get_node_pos = _member(library, "get_node_pos")
    api_status = "MISSING"
    if callable(get_node_pos):
        try:
            position = get_node_pos(node)
            x = _read_property(position, "x")
            y = _read_property(position, "y")
            if _number(x) and _number(y):
                return (int(x), int(y)), "AVAILABLE"
            api_status = "ERROR"
        except Exception:
            api_status = "ERROR"
    x = _read_property(node, "node_pos_x")
    y = _read_property(node, "node_pos_y")
    if _number(x) and _number(y):
        return (int(x), int(y)), api_status
    return None, api_status


def _iterable_list(value: object) -> list[object] | None:
    if value is None or isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    if isinstance(value, Sequence):
        return list(value)
    try:
        return list(iter(value))  # type: ignore[arg-type]
    except (TypeError, RuntimeError):
        return None


def _direct_nodes(graph: object) -> tuple[str, list[object]]:
    available, value = _read_property_result(graph, "nodes")
    if not available:
        return "MISSING", []
    nodes = _iterable_list(value)
    if nodes is None:
        return "ERROR", []
    return "AVAILABLE", nodes


def _is_ed_graph(
    value: object,
    graph: object,
    ed_graph_class: object | None,
) -> bool:
    if value is graph:
        return True
    if ed_graph_class is not None:
        try:
            if isinstance(value, ed_graph_class):  # type: ignore[arg-type]
                return True
        except TypeError:
            pass
    try:
        return isinstance(value, type(graph))
    except TypeError:
        return False


def _exact_graph_object(
    value: object | None,
    graph: object,
    ed_graph_class: object | None,
) -> tuple[bool, str]:
    if value is graph:
        return True, "NOT_TESTED"
    if value is None or not _is_ed_graph(value, graph, ed_graph_class):
        return False, "NOT_TESTED"
    outer_status, outer_path = _full_path_name_result(value)
    graph_status, graph_path = _full_path_name_result(graph)
    path_status = _aggregate_states((outer_status, graph_status))
    return bool(outer_path and graph_path and outer_path == graph_path), path_status


def _membership(
    node: object,
    graph: object,
    ed_graph_class: object | None,
) -> tuple[bool, bool, str, str, str]:
    direct_exact = False
    typed_exact = False
    outer_status = "MISSING"
    typed_status = "MISSING"
    path_states: list[str] = []

    get_outer = _member(node, "get_outer")
    if callable(get_outer):
        try:
            outer = get_outer()
            outer_status = "AVAILABLE"
            direct_exact, path_status = _exact_graph_object(
                outer,
                graph,
                ed_graph_class,
            )
            path_states.append(path_status)
        except Exception:
            outer_status = "ERROR"

    get_typed_outer = _member(node, "get_typed_outer")
    if callable(get_typed_outer) and ed_graph_class is not None:
        try:
            typed_outer = get_typed_outer(ed_graph_class)
            typed_status = "AVAILABLE"
            typed_exact, path_status = _exact_graph_object(
                typed_outer,
                graph,
                ed_graph_class,
            )
            path_states.append(path_status)
        except Exception:
            typed_status = "ERROR"

    return (
        direct_exact,
        typed_exact,
        outer_status,
        typed_status,
        _aggregate_states(path_states),
    )


def _iterator_scan(
    unreal_module: object,
    *,
    class_name: str,
    graph: object,
    scan_budget: int,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "NOT_TESTED",
        "objectsScanned": 0,
        "matching": [],
        "scanTruncated": False,
        "directProof": False,
        "typedProof": False,
        "outerStates": [],
        "typedStates": [],
        "pathStates": [],
        "gaps": [],
    }
    iterator_factory = _member(unreal_module, "ObjectIterator")
    class_object = _member(unreal_module, class_name)
    if not callable(iterator_factory) or class_object is None:
        result["status"] = "MISSING"
        return result
    try:
        source_iterator = iter(iterator_factory(class_object))
    except Exception:
        result["status"] = "ERROR"
        result["gaps"] = [f"OBJECT_ITERATOR_{class_name.upper()}_ERROR"]
        return result

    matching: list[object] = []
    outer_states: list[str] = []
    typed_states: list[str] = []
    path_states: list[str] = []
    objects_scanned = 0
    while True:
        try:
            node = next(source_iterator)
        except StopIteration:
            result["status"] = "AVAILABLE"
            break
        except Exception:
            result["status"] = "ERROR"
            result["gaps"] = [f"OBJECT_ITERATOR_{class_name.upper()}_ERROR"]
            break
        if objects_scanned >= scan_budget:
            result["status"] = "ERROR"
            result["scanTruncated"] = True
            result["gaps"] = ["OBJECT_ITERATOR_SCAN_LIMIT_REACHED"]
            break
        objects_scanned += 1
        direct, typed, outer_status, typed_status, path_status = _membership(
            node,
            graph,
            _member(unreal_module, "EdGraph"),
        )
        outer_states.append(outer_status)
        typed_states.append(typed_status)
        path_states.append(path_status)
        if direct or typed:
            matching.append(node)
            result["directProof"] = bool(result["directProof"]) or direct
            result["typedProof"] = bool(result["typedProof"]) or typed

    result["objectsScanned"] = objects_scanned
    result["matching"] = matching
    result["outerStates"] = outer_states
    result["typedStates"] = typed_states
    result["pathStates"] = path_states
    return result


def _aggregate_states(states: Sequence[str]) -> str:
    tested = [state for state in states if state != "NOT_TESTED"]
    if not tested:
        return "NOT_TESTED"
    if any(state == "ERROR" for state in tested):
        return "ERROR"
    if all(state == "AVAILABLE" for state in tested):
        return "AVAILABLE"
    if all(state == "MISSING" for state in tested):
        return "MISSING"
    return "MISSING"


def _deduplicate(nodes: Sequence[object], gaps: list[str]) -> list[object]:
    unique: list[object] = []
    seen_paths: set[str] = set()
    seen_guids: set[str] = set()
    for node in nodes:
        path = _full_path_name(node)
        guid = _read_guid(node)
        duplicate = bool(
            (path and path in seen_paths)
            or (guid and guid in seen_guids)
        )
        if duplicate:
            gaps.append("DUPLICATE_OBJECT_OBSERVED")
            continue
        if path:
            seen_paths.add(path)
        if guid:
            seen_guids.add(guid)
        unique.append(node)
    return unique


def _pin_text(value: object) -> str:
    return str(value or "")[:256]


def _pin_signature(pin: object) -> dict[str, str]:
    name = _read_property(pin, "pin_name")
    if name is None:
        name = _name(pin)
    direction = _read_property(pin, "direction")
    pin_type = _read_property(pin, "pin_type")
    category = _read_property(pin_type, "pin_category")
    if category is None:
        category = _read_property(pin, "pin_category")
    subcategory = _read_property(pin_type, "pin_sub_category")
    if subcategory is None:
        subcategory = _read_property(pin_type, "pin_subcategory")
    result = {
        "name": _pin_text(name),
        "direction": _pin_text(direction),
    }
    if category is not None:
        result["category"] = _pin_text(category)
    if subcategory is not None:
        result["subcategory"] = _pin_text(subcategory)
    return result


def _pins(library: object, node: object) -> tuple[str, list[dict[str, str]] | None]:
    list_all_pins = _member(library, "list_all_pins")
    if not callable(list_all_pins):
        return "MISSING", None
    try:
        values = _iterable_list(list_all_pins(node))
    except Exception:
        return "ERROR", None
    if values is None:
        return "ERROR", None
    signatures = [_pin_signature(pin) for pin in values]
    signatures.sort(
        key=lambda pin: (
            pin["name"],
            pin["direction"],
            pin.get("category", ""),
            pin.get("subcategory", ""),
        )
    )
    return "AVAILABLE", signatures


def _compile_status(blueprint: object) -> tuple[str, str]:
    available, value = _read_property_result(blueprint, "status")
    if not available:
        return "MISSING", "BS_UNKNOWN"
    name = _read_property(value, "name")
    text = f"{name or ''} {value or ''}".upper()
    for status in _COMPILE_STATUSES:
        if status in text:
            return "AVAILABLE", status
    return "AVAILABLE", "BS_UNKNOWN"


def _package_dirty(blueprint: object) -> tuple[str, bool | None]:
    get_outermost = _member(blueprint, "get_outermost")
    if not callable(get_outermost):
        return "MISSING", None
    try:
        package = get_outermost()
    except Exception:
        return "ERROR", None
    for name in ("is_dirty", "is_package_dirty"):
        candidate = _member(package, name)
        if not callable(candidate):
            continue
        try:
            value = candidate()
        except Exception:
            return "ERROR", None
        if isinstance(value, bool):
            return "AVAILABLE", value
        return "ERROR", None
    return "MISSING", None


def _blueprint_graph_lists(
    library: object,
    blueprint: object,
) -> tuple[str, str]:
    graph_status = "MISSING"
    name_status = "MISSING"
    graphs: list[object] | None = None
    for name in ("get_all_graphs", "list_graphs", "get_graphs"):
        candidate = _member(library, name)
        if not callable(candidate):
            continue
        try:
            graphs = _iterable_list(candidate(blueprint))
        except Exception:
            graph_status = "ERROR"
        else:
            graph_status = "AVAILABLE" if graphs is not None else "ERROR"
        break
    for name in ("get_all_graph_names", "list_graph_names", "get_graph_names"):
        candidate = _member(library, name)
        if not callable(candidate):
            continue
        try:
            names = _iterable_list(candidate(blueprint))
        except Exception:
            name_status = "ERROR"
        else:
            name_status = "AVAILABLE" if names is not None else "ERROR"
        break
    if name_status == "MISSING" and graph_status == "AVAILABLE" and graphs is not None:
        for graph in graphs:
            _name(graph)
        name_status = "AVAILABLE"
    return graph_status, name_status


def _load_asset(unreal_module: object, object_path: str) -> object | None:
    load_asset = _member(unreal_module, "load_asset")
    if callable(load_asset):
        try:
            return load_asset(object_path)
        except Exception:
            return None
    library = _member(unreal_module, "EditorAssetLibrary")
    load_asset = _member(library, "load_asset") if library is not None else None
    if callable(load_asset):
        try:
            return load_asset(object_path)
        except Exception:
            return None
    return None


def _find_graph(library: object, blueprint: object, graph_name: str) -> object | None:
    find_graph = _member(library, "find_graph")
    if callable(find_graph):
        try:
            return find_graph(blueprint, graph_name)
        except Exception:
            return None
    if graph_name == "EventGraph":
        find_event_graph = _member(library, "find_event_graph")
        if callable(find_event_graph):
            try:
                return find_event_graph(blueprint)
            except Exception:
                return None
    return None


def _request_valid(request: Mapping[str, object]) -> bool:
    object_path = request.get("objectPath")
    graph_name = request.get("graphName")
    valid = bool(
        request.get("schema") == REQUEST_SCHEMA
        and isinstance(object_path, str)
        and object_path.startswith(("/Game/", "/Engine/"))
        and len(object_path) <= 4096
        and isinstance(graph_name, str)
        and 0 < len(graph_name) <= 256
    )
    if not valid:
        return False
    try:
        assert_path_free(request)
    except ValueError:
        return False
    return True


def _bounded_request_int(
    request: Mapping[str, object],
    name: str,
    maximum: int,
) -> int:
    value = request.get(name, maximum)
    if isinstance(value, bool) or not isinstance(value, int):
        return maximum
    return max(1, min(value, maximum))


def _sort_nodes(nodes: list[dict[str, object]]) -> None:
    nodes.sort(
        key=lambda node: (
            0 if "y" in node else 1,
            int(node.get("y", 0)),
            0 if "x" in node else 1,
            int(node.get("x", 0)),
            str(node.get("nodeGuid", "")),
            str(node.get("name", "")),
        )
    )


def collect_explicit_graph_snapshot(
    unreal_module: object,
    request: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Read one named graph without opening, focusing, compiling, or saving it."""

    if not _request_valid(request):
        return empty_explicit_target(status="ERROR", gap="REQUEST_INVALID"), None

    target = _target(request)
    capabilities = target["capabilities"]
    assert isinstance(capabilities, dict)
    gaps = target["gaps"]
    assert isinstance(gaps, list)

    object_path = str(request["objectPath"])
    graph_name = str(request["graphName"])
    asset = _load_asset(unreal_module, object_path)
    if asset is None:
        target["status"] = "MISSING"
        capabilities["explicitAssetLoad"] = "MISSING"
        gaps.append("EXPLICIT_ASSET_NOT_FOUND")
        return target, None
    capabilities["explicitAssetLoad"] = "AVAILABLE"

    library = _member(unreal_module, "BlueprintEditorLibrary")
    get_blueprint_asset = _member(library, "get_blueprint_asset")
    if library is None or not callable(get_blueprint_asset):
        target["status"] = "MISSING"
        capabilities["blueprintObject"] = "MISSING"
        gaps.append("BLUEPRINT_EDITOR_LIBRARY_UNAVAILABLE")
        return target, None
    try:
        blueprint = get_blueprint_asset(asset)
    except Exception:
        blueprint = None
    if blueprint is None:
        target["status"] = "MISSING"
        capabilities["blueprintObject"] = "MISSING"
        gaps.append("EXPLICIT_ASSET_IS_NOT_BLUEPRINT")
        return target, None
    capabilities["blueprintObject"] = "AVAILABLE"

    graph = _find_graph(library, blueprint, graph_name)
    if graph is None:
        target["status"] = "MISSING"
        capabilities["explicitGraphFind"] = "MISSING"
        gaps.append("EXPLICIT_GRAPH_NOT_FOUND")
        return target, None
    capabilities["explicitGraphFind"] = "AVAILABLE"

    graph_list_status, graph_name_status = _blueprint_graph_lists(library, blueprint)
    capabilities["blueprintListGraphs"] = graph_list_status
    capabilities["blueprintListGraphNames"] = graph_name_status
    compile_status_state, compile_status = _compile_status(blueprint)
    capabilities["blueprintStatusRead"] = compile_status_state
    target["compileStatus"] = compile_status
    dirty_status, package_dirty = _package_dirty(blueprint)
    capabilities["packageDirtyRead"] = dirty_status
    if package_dirty is not None:
        target["packageDirty"] = package_dirty

    direct_status, graph_nodes = _direct_nodes(graph)
    capabilities["directGraphProperty"] = direct_status
    objects_scanned = 0
    scan_truncated = False
    strategy = "NONE"
    outer_states: list[str] = []
    typed_states: list[str] = []
    path_states: list[str] = []
    typed_proof = False

    if direct_status == "AVAILABLE":
        strategy = "DIRECT_GRAPH_PROPERTY"
        capabilities["graphNodeEnumeration"] = "AVAILABLE"
    else:
        scan_budget = _bounded_request_int(
            request,
            "maxObjectsScanned",
            MAX_OBJECTS_SCANNED,
        )
        ed_scan = _iterator_scan(
            unreal_module,
            class_name="EdGraphNode",
            graph=graph,
            scan_budget=scan_budget,
        )
        capabilities["objectIteratorEdGraphNode"] = str(ed_scan["status"])
        objects_scanned += int(ed_scan["objectsScanned"])
        outer_states.extend(ed_scan["outerStates"])  # type: ignore[arg-type]
        typed_states.extend(ed_scan["typedStates"])  # type: ignore[arg-type]
        path_states.extend(ed_scan["pathStates"])  # type: ignore[arg-type]
        gaps.extend(ed_scan["gaps"])  # type: ignore[arg-type]
        scan_truncated = bool(ed_scan["scanTruncated"])
        ed_matches = list(ed_scan["matching"])  # type: ignore[arg-type]

        if scan_truncated or (ed_matches and ed_scan["status"] != "AVAILABLE"):
            capabilities["graphNodeEnumeration"] = "ERROR"
            target["status"] = "ERROR"
            graph_nodes = ed_matches
        elif ed_scan["status"] == "AVAILABLE" and ed_matches:
            graph_nodes = ed_matches
            strategy = "OBJECT_ITERATOR_EDGRAPHNODE_EXACT_OUTER"
            typed_proof = bool(ed_scan["typedProof"])
            capabilities["graphNodeEnumeration"] = "AVAILABLE"
        else:
            remaining = max(0, scan_budget - objects_scanned)
            k2_scan = _iterator_scan(
                unreal_module,
                class_name="K2Node",
                graph=graph,
                scan_budget=remaining,
            )
            capabilities["objectIteratorK2Node"] = str(k2_scan["status"])
            objects_scanned += int(k2_scan["objectsScanned"])
            outer_states.extend(k2_scan["outerStates"])  # type: ignore[arg-type]
            typed_states.extend(k2_scan["typedStates"])  # type: ignore[arg-type]
            path_states.extend(k2_scan["pathStates"])  # type: ignore[arg-type]
            gaps.extend(k2_scan["gaps"])  # type: ignore[arg-type]
            scan_truncated = bool(k2_scan["scanTruncated"])
            k2_matches = list(k2_scan["matching"])  # type: ignore[arg-type]
            if scan_truncated or k2_scan["status"] == "ERROR":
                capabilities["graphNodeEnumeration"] = "ERROR"
                target["status"] = "ERROR"
                graph_nodes = k2_matches
            elif k2_scan["status"] == "AVAILABLE" and k2_matches:
                graph_nodes = k2_matches
                strategy = "OBJECT_ITERATOR_K2NODE_EXACT_OUTER"
                typed_proof = bool(k2_scan["typedProof"])
                capabilities["graphNodeEnumeration"] = "AVAILABLE"
            else:
                graph_nodes = []
                capabilities["graphNodeEnumeration"] = "MISSING"
                target["status"] = "MISSING"
                gaps.append("GRAPH_NODE_ENUMERATION_UNAVAILABLE")

        capabilities["objectGetOuter"] = _aggregate_states(outer_states)
        capabilities["objectGetTypedOuter"] = _aggregate_states(typed_states)
        capabilities["objectGetPathName"] = _aggregate_states(path_states)
        if graph_nodes:
            capabilities["exactOuterNodeEnumeration"] = "AVAILABLE"
            capabilities["exactTypedOuterNodeEnumeration"] = (
                "AVAILABLE" if typed_proof else "MISSING"
            )
        elif scan_truncated:
            capabilities["exactOuterNodeEnumeration"] = "ERROR"
            capabilities["exactTypedOuterNodeEnumeration"] = "ERROR"
        elif capabilities["graphNodeEnumeration"] == "MISSING":
            capabilities["exactOuterNodeEnumeration"] = "MISSING"
            capabilities["exactTypedOuterNodeEnumeration"] = "MISSING"

    target.update(
        {
            "objectsScanned": objects_scanned,
            "scanTruncated": scan_truncated,
            "enumerationStrategy": strategy,
        }
    )
    if scan_truncated:
        unique_nodes = _deduplicate(graph_nodes, gaps)
        target["matchingNodes"] = len(unique_nodes)
        target["returnedNodes"] = 0
        target["nodesOmitted"] = len(unique_nodes)
        target["gaps"] = sorted(set(gaps))
        return target, None
    if capabilities["graphNodeEnumeration"] not in {"AVAILABLE"}:
        target["gaps"] = sorted(set(gaps))
        return target, None

    unique_nodes = _deduplicate(graph_nodes, gaps)
    matching_nodes = len(unique_nodes)
    observations: list[dict[str, object]] = []
    authoritative: list[dict[str, object]] = []
    position_states: list[bool] = []
    node_get_pos_states: list[str] = []
    pin_states: list[str] = []
    valid_guid_count = 0

    for source in unique_nodes:
        node: dict[str, object] = {
            "name": _name(source),
            "className": _class_name(source),
        }
        guid = _read_guid(source)
        if guid:
            node["nodeGuid"] = guid
            valid_guid_count += 1
        position, node_get_pos_status = _position(library, source)
        node_get_pos_states.append(node_get_pos_status)
        if position is not None:
            node["x"], node["y"] = position
            position_states.append(True)
        else:
            position_states.append(False)
        pin_status, pins = _pins(library, source)
        pin_states.append(pin_status)
        if pins is not None:
            node["pinCount"] = len(pins)
            node["pins"] = pins
        observations.append(node)
        if guid:
            authoritative.append(dict(node))

    if matching_nodes:
        capabilities["nodeGuidRead"] = (
            "AVAILABLE" if valid_guid_count == matching_nodes else "MISSING"
        )
        capabilities["nodePositionRead"] = (
            "AVAILABLE" if all(position_states) else "MISSING"
        )
        capabilities["nodeGetPos"] = _aggregate_states(node_get_pos_states)
        capabilities["nodeListAllPins"] = _aggregate_states(pin_states)
        capabilities["nodePinRead"] = _aggregate_states(pin_states)
    else:
        capabilities["nodeGuidRead"] = "NOT_TESTED"
        capabilities["nodePositionRead"] = "NOT_TESTED"
        capabilities["nodeGetPos"] = "NOT_TESTED"
        capabilities["nodeListAllPins"] = "NOT_TESTED"
        capabilities["nodePinRead"] = "NOT_TESTED"
    if valid_guid_count != matching_nodes:
        gaps.append("NODE_GUID_INVALID_OR_UNAVAILABLE")
    if not all(position_states):
        gaps.append("NODE_POSITION_UNAVAILABLE")
    if any(status == "ERROR" for status in pin_states):
        gaps.append("NODE_PIN_READ_FAILED")
    elif any(status == "MISSING" for status in pin_states):
        gaps.append("NODE_PIN_READ_UNAVAILABLE")

    _sort_nodes(observations)
    _sort_nodes(authoritative)
    cap = _bounded_request_int(request, "maxNodes", MAX_GRAPH_NODES)
    returned_observations = observations[:cap]
    returned_authoritative = authoritative[:cap]
    returned_nodes = len(returned_authoritative)
    nodes_omitted = matching_nodes - returned_nodes

    target.update(
        {
            "matchingNodes": matching_nodes,
            "returnedNodes": returned_nodes,
            "nodesOmitted": nodes_omitted,
        }
    )
    snapshot: dict[str, object] = {
        "schema": SNAPSHOT_SCHEMA,
        "objectPath": object_path,
        "graphName": graph_name,
        "graphPath": _path_name(graph),
        "graphClass": _class_name(graph),
        "objectsScanned": objects_scanned,
        "matchingNodes": matching_nodes,
        "returnedNodes": returned_nodes,
        "nodesOmitted": nodes_omitted,
        "scanTruncated": False,
        "enumerationStrategy": strategy,
        "compileStatus": compile_status,
        "nodeCount": matching_nodes,
        "nodesReturned": len(returned_observations),
        "nodes": returned_observations,
        "authoritativeNodes": returned_authoritative,
        "capabilities": dict(capabilities),
        "gaps": sorted(set(gaps)),
    }
    if package_dirty is not None:
        snapshot["packageDirty"] = package_dirty
    attach_semantic_digest(snapshot)
    assert_path_free(snapshot)

    full_snapshot = bool(
        strategy in _ENUMERATION_STRATEGIES
        and capabilities["graphNodeEnumeration"] == "AVAILABLE"
        and capabilities["nodeGuidRead"] == "AVAILABLE"
        and capabilities["nodePositionRead"] == "AVAILABLE"
    )
    target["status"] = "AVAILABLE" if full_snapshot else "MISSING"
    target["gaps"] = list(snapshot["gaps"])
    assert_path_free(target)
    return target, snapshot


__all__ = ["collect_explicit_graph_snapshot", "empty_explicit_target"]
