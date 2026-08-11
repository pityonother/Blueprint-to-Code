"""One-shot, explicit-target, read-only Blueprint graph snapshot."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from arkdev_scripting_probe.contracts import (
    MAX_GRAPH_NODES,
    REQUEST_SCHEMA,
    SNAPSHOT_SCHEMA,
    assert_path_free,
    attach_semantic_digest,
)


_HEX_GUID = re.compile(r"^[0-9A-Fa-f]{32}$")
_ZERO_GUID = "0" * 32


def _status_map() -> dict[str, str]:
    return {
        "explicitAssetLoad": "NOT_TESTED",
        "blueprintObject": "NOT_TESTED",
        "explicitGraphFind": "NOT_TESTED",
        "graphNodeEnumeration": "NOT_TESTED",
        "nodeGuidRead": "NOT_TESTED",
        "nodePositionRead": "NOT_TESTED",
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
        "gaps": [],
    }


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


def _read_property(owner: object, name: str) -> object | None:
    value = _member(owner, name)
    if value is not None:
        return value
    getter = _member(owner, "get_editor_property")
    if not callable(getter):
        return None
    try:
        return getter(name)
    except Exception:
        return None


def _name(owner: object) -> str:
    value = _call_no_args(owner, "get_name")
    return str(value or "")[:256]


def _class_name(owner: object) -> str:
    class_object = _call_no_args(owner, "get_class")
    if class_object is None:
        return type(owner).__name__[:256]
    return (_name(class_object) or type(owner).__name__)[:256]


def _path_name(owner: object) -> str:
    value = _call_no_args(owner, "get_path_name")
    return str(value or "")[:4096]


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
    if all(isinstance(component, int) for component in components):
        compact = "".join(f"{int(component) & 0xFFFFFFFF:08X}" for component in components)
        if compact != _ZERO_GUID:
            return compact
    return ""


def _position(
    library: object,
    node: object,
) -> tuple[int, int] | None:
    get_node_pos = _member(library, "get_node_pos")
    if callable(get_node_pos):
        try:
            position = get_node_pos(node)
            x = _read_property(position, "x")
            y = _read_property(position, "y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return int(x), int(y)
        except Exception:
            pass
    x = _read_property(node, "node_pos_x")
    y = _read_property(node, "node_pos_y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return int(x), int(y)
    return None


def _nodes(graph: object) -> list[object] | None:
    value = _read_property(graph, "nodes")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    return list(value)


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


def collect_explicit_graph_snapshot(
    unreal_module: object,
    request: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Read one named graph without opening, focusing, compiling, or saving it."""

    if not _request_valid(request):
        target = _target({})
        gaps = target["gaps"]
        assert isinstance(gaps, list)
        target["status"] = "ERROR"
        gaps.append("REQUEST_INVALID")
        return target, None

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

    graph_nodes = _nodes(graph)
    if graph_nodes is None:
        target["status"] = "MISSING"
        capabilities["graphNodeEnumeration"] = "MISSING"
        gaps.append("GRAPH_NODE_ENUMERATION_UNAVAILABLE")
        return target, None
    capabilities["graphNodeEnumeration"] = "AVAILABLE"

    projected: list[dict[str, object]] = []
    valid_guid_count = 0
    positioned_count = 0
    for source in graph_nodes:
        node: dict[str, object] = {
            "name": _name(source),
            "className": _class_name(source),
        }
        guid = _canonical_guid(_read_property(source, "node_guid"))
        if guid:
            node["nodeGuid"] = guid
            valid_guid_count += 1
        position = _position(library, source)
        if position is not None:
            node["x"], node["y"] = position
            positioned_count += 1
        projected.append(node)

    if graph_nodes:
        capabilities["nodeGuidRead"] = (
            "AVAILABLE" if valid_guid_count == len(graph_nodes) else "MISSING"
        )
        capabilities["nodePositionRead"] = (
            "AVAILABLE" if positioned_count == len(graph_nodes) else "MISSING"
        )
    else:
        capabilities["nodeGuidRead"] = "NOT_TESTED"
        capabilities["nodePositionRead"] = "NOT_TESTED"
    if valid_guid_count != len(graph_nodes):
        gaps.append("NODE_GUID_INVALID_OR_UNAVAILABLE")
    if positioned_count != len(graph_nodes):
        gaps.append("NODE_POSITION_UNAVAILABLE")

    projected.sort(
        key=lambda node: (
            0 if "y" in node else 1,
            int(node.get("y", 0)),
            0 if "x" in node else 1,
            int(node.get("x", 0)),
            str(node.get("nodeGuid", "")),
            str(node.get("name", "")),
        )
    )
    requested_cap = request.get("maxNodes", MAX_GRAPH_NODES)
    if isinstance(requested_cap, bool) or not isinstance(requested_cap, int):
        requested_cap = MAX_GRAPH_NODES
    cap = max(1, min(int(requested_cap), MAX_GRAPH_NODES))
    returned = projected[:cap]

    snapshot: dict[str, object] = {
        "schema": SNAPSHOT_SCHEMA,
        "objectPath": object_path,
        "graphName": graph_name,
        "graphPath": _path_name(graph),
        "graphClass": _class_name(graph),
        "nodeCount": len(graph_nodes),
        "nodesReturned": len(returned),
        "nodesOmitted": len(graph_nodes) - len(returned),
        "nodes": returned,
        "capabilities": dict(capabilities),
        "gaps": sorted(set(gaps)),
    }
    attach_semantic_digest(snapshot)
    assert_path_free(snapshot)
    target["status"] = "AVAILABLE"
    target["gaps"] = list(snapshot["gaps"])
    assert_path_free(target)
    return target, snapshot


__all__ = ["collect_explicit_graph_snapshot"]
