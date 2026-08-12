"""Bounded, explicit-target ARK DevKit ``wc_*`` Graph identity snapshot.

This module is intentionally read-only.  It does not enumerate global Unreal
objects, inspect editor focus, open assets, compile Blueprints, or save packages.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence

from arkdev_scripting_probe.contracts import (
    assert_path_free,
    attach_semantic_digest,
    semantic_digest,
)
from devkit_exporters.arkdev_wc_properties import (
    wc_get_all_property_names,
    wc_get_property_value_detailed,
)


REQUEST_SCHEMA = "blueprint-to-code.arkdev-wc-graph-snapshot-request/v1"
SNAPSHOT_SCHEMA = "blueprint-to-code.arkdev-wc-graph-snapshot/v1"
RESULT_SCHEMA = "blueprint-to-code.arkdev-wc-graph-snapshot-result/v1"
SOURCE = "ARKDEV_WC_REFLECTION"

MAX_NODES = 500
MAX_PINS_PER_NODE = 128
MAX_IDENTITY_LOCATORS = 12
MIN_IDENTITY_LOCATORS = 5
MAX_PROPERTY_DIAGNOSTICS = 64
MAX_LINKS = MAX_NODES * MAX_PINS_PER_NODE

_HEX_GUID = re.compile(r"^[0-9A-Fa-f]{32}$")
_ZERO_GUID = "0" * 32
_CAPABILITY_STATUSES = {
    "PASS",
    "PARTIAL",
    "UNAVAILABLE",
    "ERROR",
    "NOT_TESTED",
}
_RESULT_STATUSES = {"PASS", "PARTIAL", "FAIL", "ERROR"}


def _member(owner: object | None, name: str) -> object | None:
    if owner is None:
        return None
    try:
        return getattr(owner, name, None)
    except Exception:
        return None


def _call_no_args(owner: object | None, name: str) -> object | None:
    method = _member(owner, name)
    if not callable(method):
        return None
    try:
        return method()
    except Exception:
        return None


def _text(value: object, maximum: int = 4096) -> str:
    if value is None:
        return ""
    try:
        return str(value)[:maximum]
    except Exception:
        return ""


def _name(value: object | None) -> tuple[str, str]:
    method = _member(value, "get_name")
    if callable(method):
        try:
            return _text(method(), 256), "get_name"
        except Exception:
            pass
    try:
        return _text(getattr(value, "name"), 256), "getattr:name"
    except Exception:
        return "", ""


def _object_path(value: object | None) -> tuple[str, str]:
    path, method = _exact_object_path(value)
    return _text(path), method


def _exact_object_path(value: object | None) -> tuple[str, str]:
    method = _member(value, "get_path_name")
    if callable(method):
        try:
            return str(method()), "get_path_name"
        except Exception:
            pass
    return "", ""


def _raw_class_name(value: object | None) -> tuple[str, str]:
    class_object = _call_no_args(value, "get_class")
    if class_object is None:
        return "", ""
    path, method = _object_path(class_object)
    if path:
        return path, f"get_class.{method}"
    name, method = _name(class_object)
    return name, f"get_class.{method}" if method else ""


def canonical_class_name(raw: object) -> str:
    """Normalize only the deterministic leaf; preserve ``raw`` separately."""

    text = _text(raw, 256).strip().replace("\\", "/")
    if not text:
        return ""
    if " " in text:
        text = text.rsplit(" ", 1)[-1]
    return text.rsplit("/", 1)[-1].rsplit(".", 1)[-1].rsplit(":", 1)[-1]


def _canonical_guid(value: object) -> str:
    candidates: list[str] = []
    if value is not None:
        candidates.append(_text(value, 128).strip())
        to_string = _member(value, "to_string")
        if callable(to_string):
            try:
                candidates.append(_text(to_string(), 128).strip())
            except Exception:
                pass
    for candidate in candidates:
        compact = re.sub(r"[-{}()]", "", candidate)
        if _HEX_GUID.fullmatch(compact) and compact != _ZERO_GUID:
            return compact.upper()

    components: list[object] = []
    for component in ("a", "b", "c", "d"):
        try:
            components.append(getattr(value, component))
        except Exception:
            components.append(None)
    if all(isinstance(item, int) and not isinstance(item, bool) for item in components):
        compact = "".join(f"{int(item) & 0xFFFFFFFF:08X}" for item in components)
        return compact if compact != _ZERO_GUID else ""
    return ""


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _read_field(
    owner: object,
    name: str,
    *,
    unreal_module: object,
) -> tuple[bool, object | None, str]:
    ok, value, method = wc_get_property_value_detailed(
        owner,
        name,
        unreal_module=unreal_module,
    )
    if ok:
        return True, value, method

    getter = _member(owner, "get_editor_property")
    if callable(getter):
        for candidate in (name, _snake_case(name)):
            try:
                return True, getter(candidate), f"get_editor_property:{candidate}"
            except Exception:
                continue

    for candidate in (name, _snake_case(name)):
        try:
            return True, getattr(owner, candidate), f"getattr:{candidate}"
        except Exception:
            continue
    return False, None, "UNAVAILABLE"


def _bounded_iterable(
    value: object,
    maximum: int,
) -> tuple[bool, list[object], bool]:
    if value is None or isinstance(value, (str, bytes, bytearray, Mapping)):
        return False, [], False
    try:
        iterator = iter(value)  # type: ignore[arg-type]
    except (TypeError, RuntimeError):
        return False, [], False
    items: list[object] = []
    try:
        for item in iterator:
            if len(items) >= maximum:
                return True, items, True
            items.append(item)
    except Exception:
        return False, items, False
    return True, items, False


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coverage(recovered: int, total: int) -> float:
    return round((recovered * 100.0 / total), 2) if total else 0.0


def _capability(recovered: int, total: int, *, error: bool = False) -> str:
    if error:
        return "ERROR"
    if total == 0:
        return "NOT_TESTED"
    if recovered == total:
        return "PASS"
    if recovered:
        return "PARTIAL"
    return "UNAVAILABLE"


def _request_copy(request: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema": REQUEST_SCHEMA,
        "assetObjectPath": str(request["assetObjectPath"]),
        "graphName": str(request["graphName"]),
        "maxNodes": int(request["maxNodes"]),
        "maxPinsPerNode": int(request["maxPinsPerNode"]),
    }


def validate_wc_snapshot_request(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("request must be an object")
    if set(value) != {
        "schema",
        "assetObjectPath",
        "graphName",
        "maxNodes",
        "maxPinsPerNode",
    }:
        raise ValueError("request fields do not match the bounded contract")
    if value.get("schema") != REQUEST_SCHEMA:
        raise ValueError("request schema mismatch")
    object_path = value.get("assetObjectPath")
    graph_name = value.get("graphName")
    max_nodes = value.get("maxNodes")
    max_pins = value.get("maxPinsPerNode")
    if (
        not isinstance(object_path, str)
        or not object_path.startswith(("/Game/", "/Engine/"))
        or len(object_path) > 4096
    ):
        raise ValueError("invalid explicit asset object path")
    if not isinstance(graph_name, str) or not 0 < len(graph_name) <= 256:
        raise ValueError("invalid explicit graph name")
    if (
        not isinstance(max_nodes, int)
        or isinstance(max_nodes, bool)
        or not 1 <= max_nodes <= MAX_NODES
    ):
        raise ValueError("maxNodes exceeds the hard bound")
    if (
        not isinstance(max_pins, int)
        or isinstance(max_pins, bool)
        or not 1 <= max_pins <= MAX_PINS_PER_NODE
    ):
        raise ValueError("maxPinsPerNode exceeds the hard bound")
    request = _request_copy(value)
    assert_path_free(request)
    return request


def _load_asset(unreal_module: object, object_path: str) -> tuple[object | None, str]:
    loader = _member(unreal_module, "load_asset")
    if callable(loader):
        try:
            return loader(object_path), "unreal.load_asset"
        except Exception:
            pass
    library = _member(unreal_module, "EditorAssetLibrary")
    loader = _member(library, "load_asset")
    if callable(loader):
        try:
            return loader(object_path), "EditorAssetLibrary.load_asset"
        except Exception:
            pass
    return None, ""


def _blueprint_asset(unreal_module: object, asset: object) -> tuple[object | None, str]:
    library = _member(unreal_module, "BlueprintEditorLibrary")
    getter = _member(library, "get_blueprint_asset")
    if not callable(getter):
        return None, ""
    try:
        return getter(asset), "BlueprintEditorLibrary.get_blueprint_asset"
    except Exception:
        return None, ""


def _find_graph(
    unreal_module: object,
    blueprint: object,
    graph_name: str,
) -> tuple[object | None, str]:
    library = _member(unreal_module, "BlueprintEditorLibrary")
    finder = _member(library, "find_graph")
    if callable(finder):
        try:
            graph = finder(blueprint, graph_name)
        except Exception:
            graph = None
        if graph is not None:
            return graph, "BlueprintEditorLibrary.find_graph"
    if graph_name == "EventGraph":
        finder = _member(library, "find_event_graph")
        if callable(finder):
            try:
                return finder(blueprint), "BlueprintEditorLibrary.find_event_graph"
            except Exception:
                pass
    return None, ""


def _type_match(
    value: object,
    unreal_module: object,
    class_names: Sequence[str],
) -> tuple[bool, str]:
    tested = False
    for class_name in class_names:
        class_object = _member(unreal_module, class_name)
        if class_object is None:
            continue
        tested = True
        try:
            if isinstance(value, class_object):  # type: ignore[arg-type]
                return True, f"isinstance:{class_name}"
        except TypeError:
            continue
    return False, "TYPE_UNAVAILABLE" if not tested else "TYPE_MISMATCH"


def _outer_match(
    node: object,
    graph: object,
    unreal_module: object,
) -> tuple[bool, str]:
    getter = _member(node, "get_outer")
    if not callable(getter):
        return False, "UNAVAILABLE"
    try:
        outer = getter()
    except Exception:
        return False, "ERROR"
    if outer is graph:
        return True, "get_outer:identity"
    outer_typed, _type_method = _type_match(
        outer,
        unreal_module,
        ("EdGraph",),
    )
    if not outer_typed:
        return False, "get_outer:type_mismatch"
    outer_path, _outer_method = _exact_object_path(outer)
    graph_path, _graph_method = _exact_object_path(graph)
    if outer_path and graph_path and outer_path == graph_path:
        return True, "get_outer:exact_path"
    return False, "get_outer:mismatch"


def _stable_object_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return _text(value)
    path, _method = _object_path(value)
    if path:
        return path
    name, _method = _name(value)
    return name


def _pin_type_text(value: object, unreal_module: object) -> str:
    direct = _stable_object_text(value)
    if direct:
        return direct
    parts: list[str] = []
    for field in ("PinCategory", "PinSubCategory", "ContainerType"):
        ok, item, _method = _read_field(value, field, unreal_module=unreal_module)
        if ok and item is not None:
            parts.append(f"{field}={_text(item, 256)}")
    return "|".join(parts)[:4096]


def _pin_owner(pin: object) -> object | None:
    for method_name in ("get_owning_node", "get_outer"):
        method = _member(pin, method_name)
        if callable(method):
            try:
                owner = method()
            except Exception:
                continue
            if owner is not None:
                return owner
    return None


def _read_link(
    source_node: Mapping[str, object],
    source_pin: Mapping[str, object],
    target_pin: object,
    unreal_module: object,
) -> dict[str, str]:
    target_owner = _pin_owner(target_pin)
    target_node_name, _name_method = _name(target_owner)
    target_node_path, _path_method = _object_path(target_owner)
    ok, raw_target_id, _method = _read_field(
        target_pin,
        "PinId",
        unreal_module=unreal_module,
    )
    target_pin_id = _canonical_guid(raw_target_id) if ok else ""
    ok, raw_target_name, _method = _read_field(
        target_pin,
        "PinName",
        unreal_module=unreal_module,
    )
    target_pin_name = _text(raw_target_name, 256) if ok else ""
    ok, raw_target_guid, _method = (
        _read_field(target_owner, "NodeGuid", unreal_module=unreal_module)
        if target_owner is not None
        else (False, None, "")
    )
    return {
        "sourceNodeGuid": str(source_node.get("nodeGuid") or ""),
        "sourceNodeName": str(source_node.get("name") or ""),
        "sourcePinIdObserved": str(source_pin.get("pinId") or ""),
        "sourcePinName": str(source_pin.get("pinName") or ""),
        "targetNodeGuid": _canonical_guid(raw_target_guid) if ok else "",
        "targetNodeName": target_node_name,
        "targetNodePath": target_node_path,
        "targetPinIdObserved": target_pin_id,
        "targetPinName": target_pin_name,
    }


def _base_snapshot(
    request: Mapping[str, object],
    unreal_module: object,
) -> dict[str, object]:
    engine_version = ""
    system_library = _member(unreal_module, "SystemLibrary")
    getter = _member(system_library, "get_engine_version")
    if callable(getter):
        try:
            engine_version = _text(getter(), 128)
        except Exception:
            pass
    return {
        "schema": SNAPSHOT_SCHEMA,
        "source": SOURCE,
        "readOnly": True,
        "devkitBuildFingerprint": {
            "engineVersion": engine_version,
            "buildVersion": engine_version,
        },
        "asset": {
            "objectPath": request["assetObjectPath"],
            "observedObjectPath": "",
            "name": "",
            "rawClassName": "",
            "readMethod": "",
        },
        "graph": {
            "name": request["graphName"],
            "observedName": "",
            "path": "",
            "rawClassName": "",
            "canonicalClassName": "",
            "typedGraph": False,
            "readMethod": "",
        },
        "requestBounds": {
            "maxNodes": request["maxNodes"],
            "maxPinsPerNode": request["maxPinsPerNode"],
            "maxIdentityLocators": MAX_IDENTITY_LOCATORS,
            "maxLinks": MAX_LINKS,
        },
        "capabilityMatrix": {
            "explicitAssetLoad": "NOT_TESTED",
            "exactAssetObjectPath": "NOT_TESTED",
            "blueprintObject": "NOT_TESTED",
            "explicitGraphFind": "NOT_TESTED",
            "typedGraph": "NOT_TESTED",
            "exactGraphIdentity": "NOT_TESTED",
            "wcNodesRead": "NOT_TESTED",
            "wcPropertyNames": "NOT_TESTED",
            "typedNode": "NOT_TESTED",
            "exactOuter": "NOT_TESTED",
            "rawClassRead": "NOT_TESTED",
            "wcNodeGuidRead": "NOT_TESTED",
            "nodeGuidRead": "NOT_TESTED",
            "nodePositionRead": "NOT_TESTED",
            "nodePinsRead": "NOT_TESTED",
            "wcNodePinsRead": "NOT_TESTED",
            "pinIdRead": "NOT_TESTED",
            "persistentGuidRead": "NOT_TESTED",
            "linksRead": "NOT_TESTED",
        },
        "counts": {
            "nodeCountObserved": 0,
            "nodeCountReturned": 0,
            "nodesOmitted": 0,
            "nodeGuidRecovered": 0,
            "wcNodeGuidRecovered": 0,
            "nodePositionRecovered": 0,
            "identityLocatorCount": 0,
            "pinCount": 0,
            "pinsOmitted": 0,
            "pinSignatureRecovered": 0,
            "pinIdObserved": 0,
            "persistentGuidObserved": 0,
            "nativePinIdRecovered": 0,
            "persistentGuidRecovered": 0,
            "linkCount": 0,
        },
        "coverage": {
            "nodeGuidPercent": 0.0,
            "wcNodeGuidPercent": 0.0,
            "nodePositionPercent": 0.0,
            "exactLocatorPercent": 0.0,
            "pinSignaturePercent": 0.0,
            "observedPinIdPercent": 0.0,
            "observedPersistentGuidPercent": 0.0,
            "nativePinIdPercent": 0.0,
            "persistentGuidPercent": 0.0,
        },
        "pinIdentity": "PIN_UNAVAILABLE",
        "identityLocators": [],
        "nodes": [],
        "links": [],
        "gaps": [],
    }


def _finalize_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    gaps = snapshot["gaps"]
    assert isinstance(gaps, list)
    snapshot["gaps"] = sorted(set(str(item) for item in gaps))
    attach_semantic_digest(snapshot)
    assert_path_free(snapshot)
    return snapshot


def collect_wc_graph_snapshot(
    unreal_module: object,
    request_value: object,
) -> dict[str, object]:
    """Collect one bounded named Graph through ARK Wildcard reflection."""

    request = validate_wc_snapshot_request(request_value)
    snapshot = _base_snapshot(request, unreal_module)
    capabilities = snapshot["capabilityMatrix"]
    counts = snapshot["counts"]
    coverage = snapshot["coverage"]
    gaps = snapshot["gaps"]
    assert isinstance(capabilities, dict)
    assert isinstance(counts, dict)
    assert isinstance(coverage, dict)
    assert isinstance(gaps, list)

    object_path = str(request["assetObjectPath"])
    graph_name = str(request["graphName"])
    max_nodes = int(request["maxNodes"])
    max_pins = int(request["maxPinsPerNode"])

    asset, load_method = _load_asset(unreal_module, object_path)
    capabilities["explicitAssetLoad"] = "PASS" if asset is not None else "UNAVAILABLE"
    if asset is None:
        gaps.append("EXPLICIT_ASSET_LOAD_UNAVAILABLE")
        return _finalize_snapshot(snapshot)

    asset_name, _asset_name_method = _name(asset)
    loaded_asset_path_full, _loaded_asset_path_method = _exact_object_path(asset)
    loaded_asset_path = _text(loaded_asset_path_full)
    asset_class, _asset_class_method = _raw_class_name(asset)
    exact_asset_path = loaded_asset_path_full == object_path
    capabilities["exactAssetObjectPath"] = "PASS" if exact_asset_path else "UNAVAILABLE"
    if not exact_asset_path:
        gaps.append("ASSET_OBJECT_PATH_MISMATCH_OR_UNAVAILABLE")
    snapshot["asset"] = {
        "objectPath": object_path,
        "observedObjectPath": loaded_asset_path,
        "name": asset_name,
        "rawClassName": asset_class,
        "readMethod": load_method,
    }

    blueprint, blueprint_method = _blueprint_asset(unreal_module, asset)
    capabilities["blueprintObject"] = "PASS" if blueprint is not None else "UNAVAILABLE"
    if blueprint is None:
        gaps.append("BLUEPRINT_OBJECT_UNAVAILABLE")
        return _finalize_snapshot(snapshot)

    graph, graph_method = _find_graph(unreal_module, blueprint, graph_name)
    capabilities["explicitGraphFind"] = "PASS" if graph is not None else "UNAVAILABLE"
    if graph is None:
        gaps.append("EXPLICIT_GRAPH_FIND_UNAVAILABLE")
        return _finalize_snapshot(snapshot)

    actual_graph_name, _graph_name_method = _name(graph)
    graph_path, _graph_path_method = _object_path(graph)
    graph_class, _graph_class_method = _raw_class_name(graph)
    graph_typed, _graph_type_method = _type_match(graph, unreal_module, ("EdGraph",))
    capabilities["typedGraph"] = "PASS" if graph_typed else "UNAVAILABLE"
    exact_graph_identity = bool(
        actual_graph_name == graph_name and graph_path and graph_class and graph_typed
    )
    capabilities["exactGraphIdentity"] = (
        "PASS" if exact_graph_identity else "UNAVAILABLE"
    )
    if actual_graph_name != graph_name:
        gaps.append("GRAPH_NAME_MISMATCH")
    if not graph_typed:
        gaps.append("GRAPH_TYPE_UNVERIFIED")
    snapshot["graph"] = {
        "name": graph_name,
        "observedName": actual_graph_name,
        "path": graph_path,
        "rawClassName": graph_class,
        "canonicalClassName": canonical_class_name(graph_class),
        "typedGraph": graph_typed,
        "readMethod": f"{blueprint_method}|{graph_method}",
    }

    nodes_ok, raw_nodes, nodes_method = wc_get_property_value_detailed(
        graph,
        "Nodes",
        unreal_module=unreal_module,
    )
    if not nodes_ok:
        capabilities["wcNodesRead"] = "UNAVAILABLE"
        names = wc_get_all_property_names(graph, limit=MAX_PROPERTY_DIAGNOSTICS)
        capabilities["wcPropertyNames"] = "PASS" if names else "UNAVAILABLE"
        gaps.append("WC_NODES_READ_UNAVAILABLE")
        if names and "Nodes" not in names:
            gaps.append("WC_NODES_PROPERTY_NOT_LISTED")
        return _finalize_snapshot(snapshot)

    iterable, node_sources, nodes_truncated = _bounded_iterable(raw_nodes, max_nodes)
    if not iterable:
        capabilities["wcNodesRead"] = "ERROR"
        gaps.append("WC_NODES_NOT_ITERABLE")
        return _finalize_snapshot(snapshot)
    capabilities["wcNodesRead"] = "PASS"
    capabilities["wcPropertyNames"] = "NOT_TESTED"
    if nodes_truncated:
        gaps.append("NODE_LIMIT_REACHED")

    counts["nodeCountObserved"] = len(node_sources) + (1 if nodes_truncated else 0)
    counts["nodeCountReturned"] = len(node_sources)
    counts["nodesOmitted"] = 1 if nodes_truncated else 0

    records: list[tuple[dict[str, object], object]] = []
    typed_count = 0
    outer_count = 0
    class_count = 0
    position_count = 0
    wc_pins_count = 0
    pins_read_count = 0

    for node in node_sources:
        node_name, name_method = _name(node)
        node_path, path_method = _object_path(node)
        raw_class, class_method = _raw_class_name(node)
        canonical_class = canonical_class_name(raw_class)
        node_typed, type_method = _type_match(
            node,
            unreal_module,
            ("EdGraphNode", "K2Node"),
        )
        exact_outer, outer_method = _outer_match(node, graph, unreal_module)
        typed_count += int(node_typed)
        outer_count += int(exact_outer)
        class_count += int(bool(raw_class and canonical_class))

        guid_ok, raw_guid, guid_method = _read_field(
            node,
            "NodeGuid",
            unreal_module=unreal_module,
        )
        node_guid = _canonical_guid(raw_guid) if guid_ok else ""

        x_ok, raw_x, x_method = _read_field(
            node,
            "NodePosX",
            unreal_module=unreal_module,
        )
        y_ok, raw_y, y_method = _read_field(
            node,
            "NodePosY",
            unreal_module=unreal_module,
        )
        position_ok = x_ok and y_ok and _number(raw_x) and _number(raw_y)
        position_count += int(position_ok)

        pins_ok, raw_pins, pins_method = _read_field(
            node,
            "Pins",
            unreal_module=unreal_module,
        )
        pins_iterable, pin_sources, pins_truncated = (
            _bounded_iterable(raw_pins, max_pins) if pins_ok else (False, [], False)
        )
        if pins_iterable:
            pins_read_count += 1
            wc_pins_count += int(pins_method.startswith("wc_get_property_value:"))
        if pins_truncated:
            gaps.append("PIN_LIMIT_REACHED")

        record: dict[str, object] = {
            "name": node_name,
            "objectPath": node_path,
            "rawClassName": raw_class,
            "canonicalClassName": canonical_class,
            "typedNode": node_typed,
            "outerGraphMatch": exact_outer,
            "readMethods": {
                "name": name_method or "UNAVAILABLE",
                "objectPath": path_method or "UNAVAILABLE",
                "class": class_method or "UNAVAILABLE",
                "type": type_method,
                "outer": outer_method,
                "nodeGuid": guid_method,
                "x": x_method,
                "y": y_method,
                "pins": pins_method,
                "graphNodes": nodes_method,
            },
            "pins": [],
            "pinsOmitted": 1 if pins_truncated else 0,
        }
        if node_guid:
            record["nodeGuid"] = node_guid
        if position_ok:
            record["x"] = int(raw_x)
            record["y"] = int(raw_y)
        record["_pinSources"] = pin_sources if pins_iterable else []
        records.append((record, node))

    records.sort(
        key=lambda item: (
            0 if "y" in item[0] else 1,
            int(item[0].get("y", 0)),
            0 if "x" in item[0] else 1,
            int(item[0].get("x", 0)),
            str(item[0].get("nodeGuid", "")),
            str(item[0].get("name", "")),
        )
    )

    links: list[dict[str, str]] = []
    pin_count = 0
    pins_omitted = 0
    pin_signature_count = 0
    pin_id_count = 0
    pin_id_occurrences: Counter[str] = Counter()
    persistent_guid_count = 0
    linked_to_read_count = 0
    linked_to_total = 0

    for record, _node_source in records:
        pin_sources = record.pop("_pinSources")
        assert isinstance(pin_sources, list)
        pins: list[dict[str, object]] = []
        pins_omitted += int(record["pinsOmitted"])
        for index, pin in enumerate(pin_sources):
            read_methods: dict[str, str] = {}
            values: dict[str, object] = {}
            for field in (
                "PinId",
                "PersistentGuid",
                "PinName",
                "Direction",
                "PinType",
                "DefaultValue",
                "DefaultObject",
                "DefaultTextValue",
                "LinkedTo",
            ):
                ok, value, method = _read_field(
                    pin,
                    field,
                    unreal_module=unreal_module,
                )
                read_methods[field] = method
                if ok:
                    values[field] = value

            pin_id = _canonical_guid(values.get("PinId"))
            persistent_guid = _canonical_guid(values.get("PersistentGuid"))
            pin_id_count += int(bool(pin_id))
            if pin_id:
                pin_id_occurrences[pin_id] += 1
            persistent_guid_count += int(bool(persistent_guid))
            pin_name = _text(values.get("PinName"), 256)
            direction = _text(values.get("Direction"), 256)
            pin_type = _pin_type_text(values.get("PinType"), unreal_module)
            pin_signature_count += int(bool(pin_name or direction or pin_type))
            pin_record: dict[str, object] = {
                "index": index,
                "pinName": pin_name,
                "direction": direction,
                "pinType": pin_type,
                "defaultValue": _text(values.get("DefaultValue")),
                "defaultObject": _stable_object_text(values.get("DefaultObject")),
                "defaultTextValue": _text(values.get("DefaultTextValue")),
                "readMethods": read_methods,
            }
            if pin_id:
                pin_record["pinId"] = pin_id
            if persistent_guid:
                pin_record["persistentGuid"] = persistent_guid

            linked_to_total += 1
            linked_raw = values.get("LinkedTo")
            if "LinkedTo" in values and len(links) >= MAX_LINKS:
                linked_to_read_count += 1
                gaps.append("LINK_LIMIT_REACHED")
                pins.append(pin_record)
                continue
            link_iterable, targets, links_truncated = _bounded_iterable(
                linked_raw,
                max_pins,
            )
            if "LinkedTo" in values and link_iterable:
                linked_to_read_count += 1
                for target_pin in targets:
                    if len(links) >= MAX_LINKS:
                        gaps.append("LINK_LIMIT_REACHED")
                        break
                    links.append(
                        _read_link(record, pin_record, target_pin, unreal_module)
                    )
                if links_truncated:
                    gaps.append("LINK_TARGET_LIMIT_REACHED")
            pins.append(pin_record)
        pins.sort(
            key=lambda pin: (
                str(pin.get("pinName", "")),
                str(pin.get("direction", "")),
                str(pin.get("pinId", "")),
                int(pin.get("index", 0)),
            )
        )
        record["pins"] = pins
        pin_count += len(pins)

    node_guid_occurrences = Counter(
        str(record["nodeGuid"]) for record, _source in records if record.get("nodeGuid")
    )
    duplicate_node_guids = {
        guid for guid, occurrences in node_guid_occurrences.items() if occurrences > 1
    }
    if duplicate_node_guids:
        gaps.append("NODE_GUID_DUPLICATE")
    guid_count = sum(
        1
        for record, _source in records
        if record.get("nodeGuid")
        and node_guid_occurrences[str(record["nodeGuid"])] == 1
    )
    wc_guid_count = sum(
        1
        for record, _source in records
        if record.get("nodeGuid")
        and node_guid_occurrences[str(record["nodeGuid"])] == 1
        and isinstance(record.get("readMethods"), Mapping)
        and str(record["readMethods"].get("nodeGuid", "")).startswith(
            "wc_get_property_value:"
        )
    )
    duplicate_pin_ids = {
        pin_id for pin_id, occurrences in pin_id_occurrences.items() if occurrences > 1
    }
    if duplicate_pin_ids:
        gaps.append("PIN_ID_DUPLICATE")
    native_pin_id_count = sum(
        1 for occurrences in pin_id_occurrences.values() if occurrences == 1
    )

    node_total = len(records)
    capabilities["typedNode"] = _capability(typed_count, node_total)
    capabilities["exactOuter"] = _capability(outer_count, node_total)
    capabilities["rawClassRead"] = _capability(class_count, node_total)
    capabilities["nodeGuidRead"] = _capability(guid_count, node_total)
    capabilities["wcNodeGuidRead"] = _capability(wc_guid_count, node_total)
    capabilities["nodePositionRead"] = _capability(position_count, node_total)
    capabilities["nodePinsRead"] = _capability(pins_read_count, node_total)
    capabilities["wcNodePinsRead"] = _capability(wc_pins_count, node_total)
    capabilities["pinIdRead"] = _capability(pin_id_count, pin_count)
    capabilities["persistentGuidRead"] = _capability(
        persistent_guid_count,
        pin_count,
    )
    capabilities["linksRead"] = _capability(linked_to_read_count, linked_to_total)

    locators: list[dict[str, object]] = []
    exact_locator_count = 0
    for record, _source in records:
        methods = record["readMethods"]
        assert isinstance(methods, Mapping)
        exact = bool(
            record.get("nodeGuid")
            and str(record["nodeGuid"]) not in duplicate_node_guids
            and record.get("name")
            and record.get("rawClassName")
            and record.get("canonicalClassName")
            and record.get("typedNode") is True
            and record.get("outerGraphMatch") is True
            and str(methods.get("nodeGuid", "")).startswith("wc_get_property_value:")
        )
        exact_locator_count += int(exact)
        if exact and len(locators) < MAX_IDENTITY_LOCATORS:
            locator: dict[str, object] = {
                "nodeGuid": record["nodeGuid"],
                "nodeObjectName": record["name"],
                "nodeObjectPath": record["objectPath"],
                "rawClassName": record["rawClassName"],
                "canonicalClassName": record["canonicalClassName"],
                "outerGraphPath": graph_path,
                "outerGraphMatch": True,
            }
            if "x" in record and "y" in record:
                locator["x"] = record["x"]
                locator["y"] = record["y"]
            locators.append(locator)

    counts.update(
        {
            "nodeGuidRecovered": guid_count,
            "wcNodeGuidRecovered": wc_guid_count,
            "nodePositionRecovered": position_count,
            "identityLocatorCount": exact_locator_count,
            "pinCount": pin_count,
            "pinsOmitted": pins_omitted,
            "pinSignatureRecovered": pin_signature_count,
            "pinIdObserved": pin_id_count,
            "persistentGuidObserved": persistent_guid_count,
            "nativePinIdRecovered": native_pin_id_count,
            "persistentGuidRecovered": persistent_guid_count,
            "linkCount": len(links),
        }
    )
    coverage.update(
        {
            "nodeGuidPercent": _coverage(guid_count, node_total),
            "wcNodeGuidPercent": _coverage(wc_guid_count, node_total),
            "nodePositionPercent": _coverage(position_count, node_total),
            "exactLocatorPercent": _coverage(exact_locator_count, node_total),
            "pinSignaturePercent": _coverage(pin_signature_count, pin_count),
            "observedPinIdPercent": _coverage(pin_id_count, pin_count),
            "observedPersistentGuidPercent": _coverage(
                persistent_guid_count,
                pin_count,
            ),
            "nativePinIdPercent": _coverage(native_pin_id_count, pin_count),
            "persistentGuidPercent": _coverage(
                persistent_guid_count,
                pin_count,
            ),
        }
    )

    if pin_count and pins_omitted == 0 and native_pin_id_count == pin_count:
        snapshot["pinIdentity"] = "PIN_IDENTITY_PASS"
    elif pin_count and pin_signature_count:
        snapshot["pinIdentity"] = "PIN_SIGNATURE_ONLY"
    else:
        snapshot["pinIdentity"] = "PIN_UNAVAILABLE"

    for record, _source in records:
        if not record.get("nodeGuid"):
            gaps.append("NODE_GUID_INVALID_OR_UNAVAILABLE")
        if "x" not in record or "y" not in record:
            gaps.append("NODE_POSITION_UNAVAILABLE")
        if record.get("outerGraphMatch") is not True:
            gaps.append("NODE_OUTER_MISMATCH_OR_UNAVAILABLE")
        if record.get("typedNode") is not True:
            gaps.append("NODE_TYPE_UNVERIFIED")

    snapshot["identityLocators"] = locators
    snapshot["nodes"] = [record for record, _source in records]
    links.sort(
        key=lambda link: (
            link["sourceNodeGuid"],
            link["sourceNodeName"],
            link["sourcePinIdObserved"],
            link["sourcePinName"],
            link["targetNodeGuid"],
            link["targetNodeName"],
            link["targetPinIdObserved"],
            link["targetPinName"],
        )
    )
    snapshot["links"] = links
    return _finalize_snapshot(snapshot)


def _gate_from_snapshot(snapshot: Mapping[str, object]) -> dict[str, str]:
    capabilities = snapshot.get("capabilityMatrix")
    counts = snapshot.get("counts")
    coverage = snapshot.get("coverage")
    if not isinstance(capabilities, Mapping):
        raise ValueError("snapshot capability matrix missing")
    if not isinstance(counts, Mapping) or not isinstance(coverage, Mapping):
        raise ValueError("snapshot coverage missing")
    node_count = int(counts.get("nodeCountReturned", 0))
    nodes_omitted = int(counts.get("nodesOmitted", 0))
    locator_count = int(counts.get("identityLocatorCount", 0))
    node_guid_percent = float(coverage.get("wcNodeGuidPercent", 0.0))
    position_percent = float(coverage.get("nodePositionPercent", 0.0))
    exact_percent = float(coverage.get("exactLocatorPercent", 0.0))
    nodes = snapshot.get("nodes")
    node_guids = (
        [
            str(node.get("nodeGuid"))
            for node in nodes
            if isinstance(node, Mapping) and node.get("nodeGuid")
        ]
        if isinstance(nodes, Sequence)
        and not isinstance(
            nodes,
            (str, bytes, bytearray),
        )
        else []
    )
    duplicate_node_guid = len(node_guids) != len(set(node_guids))
    node_pass = bool(
        capabilities.get("wcNodesRead") == "PASS"
        and capabilities.get("explicitAssetLoad") == "PASS"
        and capabilities.get("exactAssetObjectPath") == "PASS"
        and capabilities.get("blueprintObject") == "PASS"
        and capabilities.get("explicitGraphFind") == "PASS"
        and capabilities.get("typedGraph") == "PASS"
        and capabilities.get("exactGraphIdentity") == "PASS"
        and nodes_omitted == 0
        and node_count >= MIN_IDENTITY_LOCATORS
        and locator_count >= MIN_IDENTITY_LOCATORS
        and node_guid_percent >= 90.0
        and position_percent >= 90.0
        and exact_percent >= 90.0
        and not duplicate_node_guid
    )
    return {
        "wcNodesRead": "PASS"
        if capabilities.get("wcNodesRead") == "PASS"
        else str(capabilities.get("wcNodesRead", "UNAVAILABLE")),
        "wcNodeGuidRead": "PASS"
        if node_guid_percent >= 90.0
        else str(capabilities.get("wcNodeGuidRead", "UNAVAILABLE")),
        "wcPositionRead": "PASS"
        if position_percent >= 90.0
        else str(capabilities.get("nodePositionRead", "UNAVAILABLE")),
        "wcPinsRead": str(capabilities.get("nodePinsRead", "UNAVAILABLE")),
        "wcLinksRead": str(capabilities.get("linksRead", "UNAVAILABLE")),
        "nodeIdentity": "PASS" if node_pass else "UNAVAILABLE",
    }


def build_wc_graph_snapshot_result(
    unreal_module: object,
    request_value: object,
    *,
    generated_at: str,
) -> dict[str, object]:
    """Build an always-path-free contract result for the one manual run."""

    try:
        request = validate_wc_snapshot_request(request_value)
        snapshot = collect_wc_graph_snapshot(unreal_module, request)
        gate = _gate_from_snapshot(snapshot)
        node_pass = gate["nodeIdentity"] == "PASS"
        status = "PASS" if node_pass else "FAIL"
        source = "WC_REFLECTION" if node_pass else "CLIPBOARD_REQUIRED"
        gaps = list(snapshot.get("gaps", []))
    except Exception:
        request = None
        snapshot = None
        gate = {
            "wcNodesRead": "ERROR",
            "wcNodeGuidRead": "ERROR",
            "wcPositionRead": "ERROR",
            "wcPinsRead": "ERROR",
            "wcLinksRead": "ERROR",
            "nodeIdentity": "ERROR",
        }
        status = "ERROR"
        source = "CLIPBOARD_REQUIRED"
        gaps = ["WC_GRAPH_SNAPSHOT_PROBE_ERROR"]

    result: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "generatedAt": generated_at,
        "status": status,
        "request": request,
        "snapshot": snapshot,
        "gate": gate,
        "arkGraphIdentitySource": source,
        "readyForWcEvidenceAdapter": status == "PASS",
        "readyToRetryPr45": False,
        "safety": {
            "readOnly": True,
            "mutationApiCalled": False,
            "objectIteratorCalled": False,
        },
        "gaps": sorted(set(str(item) for item in gaps)),
    }
    attach_semantic_digest(result)
    assert_path_free(result)
    return result


def _require_int(value: object, *, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("expected non-negative integer")
    if maximum is not None and value > maximum:
        raise ValueError("integer exceeds bound")
    return value


def _require_fields(
    value: Mapping[object, object],
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> None:
    required_fields = set(required)
    allowed_fields = required_fields | set(optional)
    actual_fields = set(value)
    if not required_fields.issubset(actual_fields):
        raise ValueError("required contract field missing")
    if not actual_fields.issubset(allowed_fields):
        raise ValueError("additional contract field is not allowed")


def _require_text(value: object, *, maximum: int, minimum: int = 0) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ValueError("invalid contract text")
    return value


def _require_unreal_path(value: object, *, allow_empty: bool = True) -> str:
    text = _require_text(value, maximum=4096)
    if not text and allow_empty:
        return text
    if not text.startswith(("/Game/", "/Engine/", "/Script/")):
        raise ValueError("invalid Unreal object path")
    return text


def _require_guid_text(value: object, *, allow_empty: bool = False) -> str:
    text = _require_text(value, maximum=32)
    if not text and allow_empty:
        return text
    if _canonical_guid(text) != text:
        raise ValueError("invalid canonical Guid")
    return text


def _require_sequence(value: object, maximum: int) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError("expected array")
    if len(value) > maximum:
        raise ValueError("array exceeds bound")
    return value


def validate_wc_graph_snapshot(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("snapshot must be an object")
    snapshot = dict(value)
    _require_fields(
        snapshot,
        required=(
            "schema",
            "source",
            "readOnly",
            "devkitBuildFingerprint",
            "asset",
            "graph",
            "requestBounds",
            "capabilityMatrix",
            "counts",
            "coverage",
            "pinIdentity",
            "identityLocators",
            "nodes",
            "links",
            "gaps",
            "semanticDigest",
        ),
    )
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("snapshot schema mismatch")
    if snapshot.get("source") != SOURCE or snapshot.get("readOnly") is not True:
        raise ValueError("snapshot source/read-only contract mismatch")
    capabilities = snapshot.get("capabilityMatrix")
    counts = snapshot.get("counts")
    coverage = snapshot.get("coverage")
    bounds = snapshot.get("requestBounds")
    if not all(
        isinstance(group, Mapping) for group in (capabilities, counts, coverage, bounds)
    ):
        raise ValueError("snapshot contract group missing")
    assert isinstance(capabilities, Mapping)
    assert isinstance(counts, Mapping)
    assert isinstance(coverage, Mapping)
    assert isinstance(bounds, Mapping)
    _require_fields(
        capabilities,
        required=(
            "explicitAssetLoad",
            "exactAssetObjectPath",
            "blueprintObject",
            "explicitGraphFind",
            "typedGraph",
            "exactGraphIdentity",
            "wcNodesRead",
            "wcPropertyNames",
            "typedNode",
            "exactOuter",
            "rawClassRead",
            "wcNodeGuidRead",
            "nodeGuidRead",
            "nodePositionRead",
            "nodePinsRead",
            "wcNodePinsRead",
            "pinIdRead",
            "persistentGuidRead",
            "linksRead",
        ),
    )
    _require_fields(
        counts,
        required=(
            "nodeCountObserved",
            "nodeCountReturned",
            "nodesOmitted",
            "nodeGuidRecovered",
            "wcNodeGuidRecovered",
            "nodePositionRecovered",
            "identityLocatorCount",
            "pinCount",
            "pinsOmitted",
            "pinSignatureRecovered",
            "pinIdObserved",
            "persistentGuidObserved",
            "nativePinIdRecovered",
            "persistentGuidRecovered",
            "linkCount",
        ),
    )
    _require_fields(
        coverage,
        required=(
            "nodeGuidPercent",
            "wcNodeGuidPercent",
            "nodePositionPercent",
            "exactLocatorPercent",
            "pinSignaturePercent",
            "observedPinIdPercent",
            "observedPersistentGuidPercent",
            "nativePinIdPercent",
            "persistentGuidPercent",
        ),
    )
    _require_fields(
        bounds,
        required=(
            "maxNodes",
            "maxPinsPerNode",
            "maxIdentityLocators",
            "maxLinks",
        ),
    )
    fingerprint = snapshot.get("devkitBuildFingerprint")
    if not isinstance(fingerprint, Mapping):
        raise ValueError("snapshot build fingerprint missing")
    _require_fields(fingerprint, required=("engineVersion", "buildVersion"))
    _require_text(fingerprint.get("engineVersion"), maximum=128)
    _require_text(fingerprint.get("buildVersion"), maximum=128)
    asset = snapshot.get("asset")
    graph = snapshot.get("graph")
    if not isinstance(asset, Mapping) or not isinstance(graph, Mapping):
        raise ValueError("snapshot target identity missing")
    _require_fields(
        asset,
        required=(
            "objectPath",
            "observedObjectPath",
            "name",
            "rawClassName",
            "readMethod",
        ),
    )
    _require_fields(
        graph,
        required=(
            "name",
            "observedName",
            "path",
            "rawClassName",
            "canonicalClassName",
            "typedGraph",
            "readMethod",
        ),
    )
    if any(status not in _CAPABILITY_STATUSES for status in capabilities.values()):
        raise ValueError("invalid capability status")
    exact_asset_path = bool(
        asset.get("objectPath")
        and asset.get("observedObjectPath") == asset.get("objectPath")
    )
    expected_exact_asset = (
        ("PASS" if exact_asset_path else "UNAVAILABLE")
        if capabilities.get("explicitAssetLoad") == "PASS"
        else "NOT_TESTED"
    )
    if capabilities.get("exactAssetObjectPath") != expected_exact_asset:
        raise ValueError("asset path capability overstates the loaded object")
    exact_graph = bool(
        graph.get("name")
        and graph.get("observedName") == graph.get("name")
        and graph.get("path")
        and graph.get("rawClassName")
        and graph.get("typedGraph") is True
    )
    expected_typed_graph = (
        ("PASS" if graph.get("typedGraph") is True else "UNAVAILABLE")
        if capabilities.get("explicitGraphFind") == "PASS"
        else "NOT_TESTED"
    )
    if capabilities.get("typedGraph") != expected_typed_graph:
        raise ValueError("typed Graph capability mismatch")
    expected_exact_graph = (
        ("PASS" if exact_graph else "UNAVAILABLE")
        if capabilities.get("explicitGraphFind") == "PASS"
        else "NOT_TESTED"
    )
    if capabilities.get("exactGraphIdentity") != expected_exact_graph:
        raise ValueError("Graph identity capability mismatch")
    max_nodes = _require_int(bounds.get("maxNodes"), maximum=MAX_NODES)
    max_pins = _require_int(
        bounds.get("maxPinsPerNode"),
        maximum=MAX_PINS_PER_NODE,
    )
    if not max_nodes or not max_pins:
        raise ValueError("snapshot bounds must be positive")
    if bounds.get("maxIdentityLocators") != MAX_IDENTITY_LOCATORS:
        raise ValueError("identity locator bound mismatch")
    if bounds.get("maxLinks") != MAX_LINKS:
        raise ValueError("link bound mismatch")
    node_count = _require_int(counts.get("nodeCountReturned"), maximum=max_nodes)
    pin_count = _require_int(
        counts.get("pinCount"),
        maximum=max_nodes * max_pins,
    )
    nodes = _require_sequence(snapshot.get("nodes"), max_nodes)
    locators = _require_sequence(
        snapshot.get("identityLocators"),
        MAX_IDENTITY_LOCATORS,
    )
    links = _require_sequence(snapshot.get("links"), MAX_LINKS)
    if len(nodes) != node_count:
        raise ValueError("node count mismatch")
    nodes_omitted = _require_int(counts.get("nodesOmitted"))
    if _require_int(counts.get("nodeCountObserved")) != node_count + nodes_omitted:
        raise ValueError("observed node count mismatch")

    node_guid_count = 0
    wc_node_guid_count = 0
    position_count = 0
    exact_locator_count = 0
    typed_count = 0
    outer_count = 0
    class_count = 0
    pins_read_count = 0
    wc_pins_read_count = 0
    calculated_pin_count = 0
    calculated_pins_omitted = 0
    pin_signature_count = 0
    pin_id_observed = 0
    pin_id_occurrences: Counter[str] = Counter()
    persistent_guid_observed = 0
    linked_to_read = 0

    node_guid_occurrences: Counter[str] = Counter()
    for raw_node in nodes:
        if not isinstance(raw_node, Mapping):
            raise ValueError("invalid node record")
        _require_fields(
            raw_node,
            required=(
                "name",
                "objectPath",
                "rawClassName",
                "canonicalClassName",
                "typedNode",
                "outerGraphMatch",
                "readMethods",
                "pins",
                "pinsOmitted",
            ),
            optional=("nodeGuid", "x", "y"),
        )
        guid = raw_node.get("nodeGuid")
        if isinstance(guid, str) and _canonical_guid(guid) == guid:
            node_guid_occurrences[guid] += 1
    duplicate_node_guids = {
        guid for guid, occurrences in node_guid_occurrences.items() if occurrences > 1
    }

    exact_node_keys: set[tuple[str, str, str, str, str]] = set()
    for raw_node in nodes:
        assert isinstance(raw_node, Mapping)
        methods = raw_node.get("readMethods")
        if not isinstance(methods, Mapping):
            raise ValueError("node read methods missing")
        pins = _require_sequence(raw_node.get("pins"), max_pins)
        calculated_pin_count += len(pins)
        calculated_pins_omitted += _require_int(raw_node.get("pinsOmitted"))
        pins_method = str(methods.get("pins", ""))
        pins_available = pins_method not in {"", "UNAVAILABLE"}
        pins_read_count += int(pins_available)
        wc_pins_read_count += int(
            pins_available and pins_method.startswith("wc_get_property_value:")
        )

        guid = raw_node.get("nodeGuid")
        valid_guid = isinstance(guid, str) and _canonical_guid(guid) == guid
        unique_guid = valid_guid and str(guid) not in duplicate_node_guids
        node_guid_count += int(unique_guid)
        guid_method = str(methods.get("nodeGuid", ""))
        wc_guid = unique_guid and guid_method.startswith("wc_get_property_value:")
        wc_node_guid_count += int(wc_guid)
        has_position = all(
            isinstance(raw_node.get(key), int)
            and not isinstance(raw_node.get(key), bool)
            for key in ("x", "y")
        )
        position_count += int(has_position)
        typed = raw_node.get("typedNode") is True
        outer = raw_node.get("outerGraphMatch") is True
        raw_class = str(raw_node.get("rawClassName", ""))
        canonical_class = str(raw_node.get("canonicalClassName", ""))
        class_ok = bool(
            raw_class
            and canonical_class
            and canonical_class_name(raw_class) == canonical_class
        )
        typed_count += int(typed)
        outer_count += int(outer)
        class_count += int(class_ok)
        exact = bool(
            unique_guid
            and wc_guid
            and raw_node.get("name")
            and typed
            and outer
            and class_ok
        )
        exact_locator_count += int(exact)
        if exact:
            exact_node_keys.add(
                (
                    str(guid),
                    str(raw_node.get("name", "")),
                    str(raw_node.get("objectPath", "")),
                    raw_class,
                    canonical_class,
                )
            )

        for raw_pin in pins:
            if not isinstance(raw_pin, Mapping):
                raise ValueError("invalid Pin record")
            _require_fields(
                raw_pin,
                required=(
                    "index",
                    "pinName",
                    "direction",
                    "pinType",
                    "defaultValue",
                    "defaultObject",
                    "defaultTextValue",
                    "readMethods",
                ),
                optional=("pinId", "persistentGuid"),
            )
            signature = bool(
                raw_pin.get("pinName")
                or raw_pin.get("direction")
                or raw_pin.get("pinType")
            )
            pin_signature_count += int(signature)
            observed_id = raw_pin.get("pinId")
            observed_persistent = raw_pin.get("persistentGuid")
            if observed_id is not None:
                if (
                    not isinstance(observed_id, str)
                    or _canonical_guid(observed_id) != observed_id
                ):
                    raise ValueError("invalid observed PinId")
                pin_id_observed += 1
                pin_id_occurrences[observed_id] += 1
            if observed_persistent is not None:
                if (
                    not isinstance(observed_persistent, str)
                    or _canonical_guid(observed_persistent) != observed_persistent
                ):
                    raise ValueError("invalid observed PersistentGuid")
                persistent_guid_observed += 1
            pin_methods = raw_pin.get("readMethods")
            if not isinstance(pin_methods, Mapping):
                raise ValueError("Pin read methods missing")
            linked_to_read += int(
                str(pin_methods.get("LinkedTo", "")) not in {"", "UNAVAILABLE"}
            )

    if calculated_pin_count != pin_count:
        raise ValueError("pin count mismatch")
    if _require_int(counts.get("pinsOmitted")) != calculated_pins_omitted:
        raise ValueError("omitted Pin count mismatch")
    if len(links) != _require_int(counts.get("linkCount"), maximum=MAX_LINKS):
        raise ValueError("link count mismatch")
    for raw_link in links:
        if not isinstance(raw_link, Mapping):
            raise ValueError("invalid Link record")
        _require_fields(
            raw_link,
            required=(
                "sourceNodeGuid",
                "sourceNodeName",
                "sourcePinIdObserved",
                "sourcePinName",
                "targetNodeGuid",
                "targetNodeName",
                "targetNodePath",
                "targetPinIdObserved",
                "targetPinName",
            ),
        )
        for key in (
            "sourceNodeGuid",
            "sourcePinIdObserved",
            "targetNodeGuid",
            "targetPinIdObserved",
        ):
            _require_guid_text(raw_link.get(key), allow_empty=True)
        for key in (
            "sourceNodeName",
            "sourcePinName",
            "targetNodeName",
            "targetPinName",
        ):
            _require_text(raw_link.get(key), maximum=256)
        _require_unreal_path(raw_link.get("targetNodePath"))
    if _require_int(counts.get("identityLocatorCount")) != exact_locator_count:
        raise ValueError("locator count mismatch")
    if len(locators) != min(exact_locator_count, MAX_IDENTITY_LOCATORS):
        raise ValueError("bounded locator projection mismatch")
    for raw_locator in locators:
        if not isinstance(raw_locator, Mapping):
            raise ValueError("invalid identity locator")
        _require_fields(
            raw_locator,
            required=(
                "nodeGuid",
                "nodeObjectName",
                "nodeObjectPath",
                "rawClassName",
                "canonicalClassName",
                "outerGraphPath",
                "outerGraphMatch",
            ),
            optional=("x", "y"),
        )
        key = (
            str(raw_locator.get("nodeGuid", "")),
            str(raw_locator.get("nodeObjectName", "")),
            str(raw_locator.get("nodeObjectPath", "")),
            str(raw_locator.get("rawClassName", "")),
            str(raw_locator.get("canonicalClassName", "")),
        )
        if raw_locator.get("outerGraphMatch") is not True or key not in exact_node_keys:
            raise ValueError("locator is not backed by an exact node")

    native_pin_id_count = sum(
        1 for occurrences in pin_id_occurrences.values() if occurrences == 1
    )
    expected_counts = {
        "nodeGuidRecovered": node_guid_count,
        "wcNodeGuidRecovered": wc_node_guid_count,
        "nodePositionRecovered": position_count,
        "pinSignatureRecovered": pin_signature_count,
        "pinIdObserved": pin_id_observed,
        "persistentGuidObserved": persistent_guid_observed,
        "nativePinIdRecovered": native_pin_id_count,
        "persistentGuidRecovered": persistent_guid_observed,
    }
    for key, expected in expected_counts.items():
        if _require_int(counts.get(key)) != expected:
            raise ValueError(f"{key} count mismatch")

    expected_coverage = {
        "nodeGuidPercent": _coverage(node_guid_count, node_count),
        "wcNodeGuidPercent": _coverage(wc_node_guid_count, node_count),
        "nodePositionPercent": _coverage(position_count, node_count),
        "exactLocatorPercent": _coverage(exact_locator_count, node_count),
        "pinSignaturePercent": _coverage(pin_signature_count, pin_count),
        "observedPinIdPercent": _coverage(pin_id_observed, pin_count),
        "observedPersistentGuidPercent": _coverage(
            persistent_guid_observed,
            pin_count,
        ),
        "nativePinIdPercent": _coverage(native_pin_id_count, pin_count),
        "persistentGuidPercent": _coverage(
            persistent_guid_observed,
            pin_count,
        ),
    }
    if dict(coverage) != expected_coverage:
        raise ValueError("coverage projection mismatch")

    expected_capabilities = {
        "typedNode": _capability(typed_count, node_count),
        "exactOuter": _capability(outer_count, node_count),
        "rawClassRead": _capability(class_count, node_count),
        "nodeGuidRead": _capability(node_guid_count, node_count),
        "wcNodeGuidRead": _capability(wc_node_guid_count, node_count),
        "nodePositionRead": _capability(position_count, node_count),
        "nodePinsRead": _capability(pins_read_count, node_count),
        "wcNodePinsRead": _capability(wc_pins_read_count, node_count),
        "pinIdRead": _capability(pin_id_observed, pin_count),
        "persistentGuidRead": _capability(persistent_guid_observed, pin_count),
        "linksRead": _capability(linked_to_read, pin_count),
    }
    for key, expected in expected_capabilities.items():
        if capabilities.get(key) != expected:
            raise ValueError(f"{key} capability overstates observed data")

    for percent in coverage.values():
        if (
            not isinstance(percent, (int, float))
            or isinstance(percent, bool)
            or not 0.0 <= float(percent) <= 100.0
        ):
            raise ValueError("invalid coverage percent")
    if (
        pin_count
        and calculated_pins_omitted == 0
        and native_pin_id_count == pin_count
    ):
        expected_pin_identity = "PIN_IDENTITY_PASS"
    elif pin_count and pin_signature_count:
        expected_pin_identity = "PIN_SIGNATURE_ONLY"
    else:
        expected_pin_identity = "PIN_UNAVAILABLE"
    if snapshot.get("pinIdentity") != expected_pin_identity:
        raise ValueError("invalid Pin identity level")
    digest = snapshot.get("semanticDigest")
    if not isinstance(digest, str) or digest != semantic_digest(snapshot):
        raise ValueError("snapshot semantic digest mismatch")
    assert_path_free(snapshot)
    return snapshot


def validate_wc_graph_snapshot_result(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("result must be an object")
    result = dict(value)
    _require_fields(
        result,
        required=(
            "schema",
            "generatedAt",
            "status",
            "request",
            "snapshot",
            "gate",
            "arkGraphIdentitySource",
            "readyForWcEvidenceAdapter",
            "readyToRetryPr45",
            "safety",
            "gaps",
            "semanticDigest",
        ),
    )
    if result.get("schema") != RESULT_SCHEMA:
        raise ValueError("result schema mismatch")
    if result.get("status") not in _RESULT_STATUSES:
        raise ValueError("invalid result status")
    generated_at = result.get("generatedAt")
    if not isinstance(generated_at, str) or len(generated_at) > 64:
        raise ValueError("invalid generated timestamp")
    safety = result.get("safety")
    if not isinstance(safety, Mapping) or safety != {
        "readOnly": True,
        "mutationApiCalled": False,
        "objectIteratorCalled": False,
    }:
        raise ValueError("unsafe result")
    gate_value = result.get("gate")
    if not isinstance(gate_value, Mapping):
        raise ValueError("result Gate missing")
    _require_fields(
        gate_value,
        required=(
            "wcNodesRead",
            "wcNodeGuidRead",
            "wcPositionRead",
            "wcPinsRead",
            "wcLinksRead",
            "nodeIdentity",
        ),
    )
    snapshot_value = result.get("snapshot")
    if snapshot_value is None:
        if result.get("status") != "ERROR":
            raise ValueError("non-error result lacks snapshot")
        if result.get("request") is not None:
            raise ValueError("error result must not bind an unvalidated request")
        if result.get("arkGraphIdentitySource") != "CLIPBOARD_REQUIRED":
            raise ValueError("error result source mismatch")
        if result.get("readyForWcEvidenceAdapter") is not False:
            raise ValueError("error result cannot enable the adapter")
    else:
        request = validate_wc_snapshot_request(result.get("request"))
        snapshot = validate_wc_graph_snapshot(snapshot_value)
        asset = snapshot.get("asset")
        graph = snapshot.get("graph")
        bounds = snapshot.get("requestBounds")
        if not all(isinstance(item, Mapping) for item in (asset, graph, bounds)):
            raise ValueError("snapshot target binding missing")
        assert isinstance(asset, Mapping)
        assert isinstance(graph, Mapping)
        assert isinstance(bounds, Mapping)
        if asset.get("objectPath") != request["assetObjectPath"]:
            raise ValueError("asset target binding mismatch")
        if graph.get("name") != request["graphName"]:
            raise ValueError("graph target binding mismatch")
        if (
            bounds.get("maxNodes") != request["maxNodes"]
            or bounds.get("maxPinsPerNode") != request["maxPinsPerNode"]
        ):
            raise ValueError("request bound binding mismatch")
        expected_gate = _gate_from_snapshot(snapshot)
        if result.get("gate") != expected_gate:
            raise ValueError("Gate result overstates snapshot evidence")
        node_pass = expected_gate["nodeIdentity"] == "PASS"
        expected_status = "PASS" if node_pass else "FAIL"
        if result.get("status") != expected_status:
            raise ValueError("result status overstates Node identity")
        expected_source = "WC_REFLECTION" if node_pass else "CLIPBOARD_REQUIRED"
        if result.get("arkGraphIdentitySource") != expected_source:
            raise ValueError("identity source mismatch")
        if result.get("readyForWcEvidenceAdapter") is not node_pass:
            raise ValueError("adapter readiness mismatch")
    if result.get("readyToRetryPr45") is not False:
        raise ValueError("Phase 3C must not retry PR45")
    digest = result.get("semanticDigest")
    if not isinstance(digest, str) or digest != semantic_digest(result):
        raise ValueError("result semantic digest mismatch")
    assert_path_free(result)
    return result


def validator_summary(result: Mapping[str, object]) -> dict[str, str]:
    validated = validate_wc_graph_snapshot_result(result)
    gate = validated.get("gate")
    assert isinstance(gate, Mapping)
    safety = validated["safety"]
    assert isinstance(safety, Mapping)
    return {
        "GATE_B_RESULT_CONTRACT": "PASS",
        "WC_NODES_READ": str(gate.get("wcNodesRead", "ERROR")),
        "WC_NODE_GUID_READ": str(gate.get("wcNodeGuidRead", "ERROR")),
        "WC_POSITION_READ": str(gate.get("wcPositionRead", "ERROR")),
        "WC_PINS_READ": str(gate.get("wcPinsRead", "ERROR")),
        "WC_LINKS_READ": str(gate.get("wcLinksRead", "ERROR")),
        "ARK_GRAPH_IDENTITY_SOURCE": str(validated["arkGraphIdentitySource"]),
        "MUTATION_API_CALLED": str(safety["mutationApiCalled"]).lower(),
        "OBJECT_ITERATOR_CALLED": str(safety["objectIteratorCalled"]).lower(),
        "READY_TO_RETRY_PR45": str(validated["readyToRetryPr45"]).lower(),
        "READY_FOR_WC_EVIDENCE_ADAPTER": str(
            validated["readyForWcEvidenceAdapter"]
        ).lower(),
    }


__all__ = [
    "MAX_IDENTITY_LOCATORS",
    "MAX_NODES",
    "MAX_PINS_PER_NODE",
    "REQUEST_SCHEMA",
    "RESULT_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "SOURCE",
    "build_wc_graph_snapshot_result",
    "canonical_class_name",
    "collect_wc_graph_snapshot",
    "validate_wc_graph_snapshot",
    "validate_wc_graph_snapshot_result",
    "validate_wc_snapshot_request",
    "validator_summary",
]
