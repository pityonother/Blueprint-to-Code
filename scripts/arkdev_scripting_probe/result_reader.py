"""Fail-closed readers and path-free summaries for live probe results."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from arkdev_scripting_probe.contracts import (
    MAX_GRAPH_NODES,
    MAX_OBJECTS_SCANNED,
    PROBE_SCHEMA,
    PROBE_VERSION,
    SNAPSHOT_SCHEMA,
    assert_path_free,
    require_digest,
    require_status,
    semantic_digest,
    status_to_validator,
)


SUMMARY_ORDER = (
    "PYTHON_RUNTIME",
    "UNREAL_IMPORT",
    "ASSET_EDITOR_SUBSYSTEM",
    "BLUEPRINT_EDITOR_LIBRARY",
    "EDITOR_UTILITY_SUBSYSTEM",
    "OBJECT_ITERATOR",
    "ED_GRAPH_NODE_CLASS",
    "K2_NODE_CLASS",
    "BLUEPRINT_GRAPH_EDITOR_CLASS",
    "BLUEPRINT_GRAPH_PIN_LIBRARY_CLASS",
    "OBJECT_GET_OUTER",
    "OBJECT_GET_TYPED_OUTER",
    "OBJECT_GET_PATH_NAME",
    "EXPLICIT_ASSET_LOAD",
    "EXPLICIT_GRAPH_FIND",
    "DIRECT_GRAPH_PROPERTY",
    "OBJECT_ITERATOR_EDGRAPHNODE",
    "OBJECT_ITERATOR_K2NODE",
    "EXACT_OUTER_NODE_ENUMERATION",
    "EXACT_TYPED_OUTER_NODE_ENUMERATION",
    "BLUEPRINT_LIST_GRAPHS",
    "BLUEPRINT_LIST_GRAPH_NAMES",
    "GRAPH_NODE_ENUMERATION",
    "NODE_GUID_READ",
    "NODE_POSITION_READ",
    "NODE_GET_POS",
    "NODE_LIST_ALL_PINS",
    "NODE_PIN_READ",
    "BLUEPRINT_STATUS_READ",
    "PACKAGE_DIRTY_READ",
    "ACTIVE_ASSET_READ",
    "FOCUSED_GRAPH_READ",
    "SELECTION_READ",
    "DIRTY_STATE_READ",
    "COMPILE_STATE_READ",
    "EXPLICIT_GRAPH_SNAPSHOT",
)

_REQUIRED_CLASSES = (
    "ObjectIterator",
    "EdGraphNode",
    "K2Node",
    "BlueprintGraphEditor",
    "BlueprintGraphPinLibrary",
)
_REQUIRED_METHODS = (
    "Object.get_outer",
    "Object.get_typed_outer",
    "Object.get_path_name",
)
_REQUIRED_TARGET_CAPABILITIES = (
    "explicitAssetLoad",
    "blueprintObject",
    "explicitGraphFind",
    "graphNodeEnumeration",
    "nodeGuidRead",
    "nodePositionRead",
    "directGraphProperty",
    "objectIteratorEdGraphNode",
    "objectIteratorK2Node",
    "exactOuterNodeEnumeration",
    "exactTypedOuterNodeEnumeration",
    "objectGetOuter",
    "objectGetTypedOuter",
    "objectGetPathName",
    "blueprintListGraphs",
    "blueprintListGraphNames",
    "nodeGetPos",
    "nodeListAllPins",
    "nodePinRead",
    "blueprintStatusRead",
    "packageDirtyRead",
)
_SUCCESS_STRATEGIES = {
    "DIRECT_GRAPH_PROPERTY",
    "OBJECT_ITERATOR_EDGRAPHNODE_EXACT_OUTER",
    "OBJECT_ITERATOR_K2NODE_EXACT_OUTER",
}
_COMPILE_STATUSES = {
    "BS_UP_TO_DATE",
    "BS_UP_TO_DATE_WITH_WARNINGS",
    "BS_DIRTY",
    "BS_ERROR",
    "BS_UNKNOWN",
}


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("expected object")
    return value


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("expected array")
    return value


def _non_negative_int(value: object, *, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("expected non-negative integer")
    if maximum is not None and value > maximum:
        raise ValueError("integer exceeds bound")
    return value


def _digest_matches(value: Mapping[str, object]) -> None:
    digest = require_digest(value.get("semanticDigest"))
    if digest != semantic_digest(value):
        raise ValueError("semantic digest mismatch")


def _require_keys(group: Mapping[str, object], names: Sequence[str]) -> None:
    if any(name not in group for name in names):
        raise ValueError("required capability is missing")


def _validate_target(target: Mapping[str, object]) -> None:
    require_status(target.get("status"))
    for key in ("objectPath", "graphName"):
        if not isinstance(target.get(key), str):
            raise ValueError("invalid explicit target identity")
    capabilities = _mapping(target.get("capabilities"))
    _require_keys(capabilities, _REQUIRED_TARGET_CAPABILITIES)
    for status in capabilities.values():
        require_status(status)
    scanned = _non_negative_int(
        target.get("objectsScanned"),
        maximum=MAX_OBJECTS_SCANNED,
    )
    matching = _non_negative_int(target.get("matchingNodes"))
    returned = _non_negative_int(
        target.get("returnedNodes"),
        maximum=MAX_GRAPH_NODES,
    )
    omitted = _non_negative_int(target.get("nodesOmitted"))
    if matching != returned + omitted:
        raise ValueError("invalid target node counts")
    if not isinstance(target.get("scanTruncated"), bool):
        raise ValueError("invalid scan truncation flag")
    strategy = target.get("enumerationStrategy")
    if strategy not in _SUCCESS_STRATEGIES | {"NONE"}:
        raise ValueError("invalid enumeration strategy")
    if target.get("compileStatus") not in _COMPILE_STATUSES:
        raise ValueError("invalid compile status")
    if "packageDirty" in target and not isinstance(target["packageDirty"], bool):
        raise ValueError("invalid package dirty flag")
    _sequence(target.get("gaps"))
    if scanned == 0 and str(strategy).startswith("OBJECT_ITERATOR_"):
        raise ValueError("iterator strategy without a scan")


def validate_probe_result(value: object) -> dict[str, object]:
    payload = dict(_mapping(value))
    if payload.get("schema") != PROBE_SCHEMA:
        raise ValueError("probe schema mismatch")
    if payload.get("probeVersion") != PROBE_VERSION:
        raise ValueError("probe version mismatch")
    python = _mapping(payload.get("python"))
    if not all(
        isinstance(python.get(key), bool)
        for key in ("available", "unrealImport", "pluginDetected")
    ):
        raise ValueError("invalid Python capability flags")
    if not isinstance(python.get("pythonVersion"), str):
        raise ValueError("invalid Python version")
    classes = _mapping(payload.get("classes"))
    methods = _mapping(payload.get("methods"))
    _require_keys(classes, _REQUIRED_CLASSES)
    _require_keys(methods, _REQUIRED_METHODS)
    for group_name in ("classes", "methods", "activeEditor", "editorUtility"):
        group = _mapping(payload.get(group_name))
        for status in group.values():
            require_status(status)
    visible = _mapping(payload.get("visibleMethods"))
    for names in visible.values():
        _sequence(names)
    _validate_target(_mapping(payload.get("explicitTarget")))
    for item in _sequence(payload.get("mutationApisObserved")):
        mutation = _mapping(item)
        if mutation.get("status") != "PRESENT_BUT_NOT_USED":
            raise ValueError("mutation API status is unsafe")
    _sequence(payload.get("gaps"))
    _digest_matches(payload)
    assert_path_free(payload)
    return payload


def _validate_pin(value: object) -> None:
    pin = _mapping(value)
    for key in ("name", "direction"):
        if not isinstance(pin.get(key), str):
            raise ValueError("invalid pin signature")
    for key in ("category", "subcategory"):
        if key in pin and not isinstance(pin[key], str):
            raise ValueError("invalid pin type signature")


def _validate_node(value: object, *, authoritative: bool) -> None:
    node = _mapping(value)
    for key in ("name", "className"):
        if not isinstance(node.get(key), str):
            raise ValueError("invalid node display field")
    guid = node.get("nodeGuid")
    if authoritative and guid is None:
        raise ValueError("authoritative node lacks GUID")
    if guid is not None and (
        not isinstance(guid, str)
        or len(guid) != 32
        or guid == "0" * 32
        or any(char not in "0123456789ABCDEF" for char in guid)
    ):
        raise ValueError("invalid node GUID")
    for key in ("x", "y"):
        if key in node and (
            not isinstance(node[key], int) or isinstance(node[key], bool)
        ):
            raise ValueError("invalid node position")
    has_pin_count = "pinCount" in node
    has_pins = "pins" in node
    if has_pin_count != has_pins:
        raise ValueError("incomplete node pin result")
    if has_pins:
        pin_count = _non_negative_int(node["pinCount"])
        pins = _sequence(node["pins"])
        if pin_count != len(pins):
            raise ValueError("invalid node pin count")
        for pin in pins:
            _validate_pin(pin)


def validate_snapshot_result(value: object) -> dict[str, object]:
    payload = dict(_mapping(value))
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("snapshot schema mismatch")
    object_path = payload.get("objectPath")
    if not isinstance(object_path, str) or not object_path.startswith(
        ("/Game/", "/Engine/")
    ):
        raise ValueError("invalid object path")
    for key in ("graphName", "graphPath", "graphClass"):
        if not isinstance(payload.get(key), str):
            raise ValueError("invalid graph identity")
    objects_scanned = _non_negative_int(
        payload.get("objectsScanned"),
        maximum=MAX_OBJECTS_SCANNED,
    )
    matching = _non_negative_int(payload.get("matchingNodes"))
    returned = _non_negative_int(
        payload.get("returnedNodes"),
        maximum=MAX_GRAPH_NODES,
    )
    omitted = _non_negative_int(payload.get("nodesOmitted"))
    node_count = _non_negative_int(payload.get("nodeCount"))
    legacy_returned = _non_negative_int(
        payload.get("nodesReturned"),
        maximum=MAX_GRAPH_NODES,
    )
    if matching != returned + omitted or node_count != matching:
        raise ValueError("invalid snapshot node counts")
    if payload.get("scanTruncated") is not False:
        raise ValueError("authoritative snapshot cannot be truncated")
    strategy = payload.get("enumerationStrategy")
    if strategy not in _SUCCESS_STRATEGIES:
        raise ValueError("snapshot strategy is not authoritative")
    if objects_scanned == 0 and str(strategy).startswith("OBJECT_ITERATOR_"):
        raise ValueError("iterator snapshot without a scan")
    if payload.get("compileStatus") not in _COMPILE_STATUSES:
        raise ValueError("invalid compile status")
    if "packageDirty" in payload and not isinstance(payload["packageDirty"], bool):
        raise ValueError("invalid package dirty flag")

    observed_nodes = _sequence(payload.get("nodes"))
    authoritative_nodes = _sequence(payload.get("authoritativeNodes"))
    if legacy_returned != len(observed_nodes) or legacy_returned > matching:
        raise ValueError("invalid observed node count")
    if returned != len(authoritative_nodes):
        raise ValueError("invalid authoritative node count")
    for node in observed_nodes:
        _validate_node(node, authoritative=False)
    for node in authoritative_nodes:
        _validate_node(node, authoritative=True)

    capabilities = _mapping(payload.get("capabilities"))
    _require_keys(capabilities, _REQUIRED_TARGET_CAPABILITIES)
    for status in capabilities.values():
        require_status(status)
    if capabilities["nodePositionRead"] == "AVAILABLE":
        for node in authoritative_nodes:
            mapped = _mapping(node)
            if "x" not in mapped or "y" not in mapped:
                raise ValueError("position capability overstates node data")
    _sequence(payload.get("gaps"))
    _digest_matches(payload)
    assert_path_free(payload)
    return payload


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _status(group: Mapping[str, object], key: str) -> str:
    value = group.get(key, "NOT_TESTED")
    return status_to_validator(value)


def validator_summary(
    probe_path: Path,
    snapshot_path: Path,
) -> dict[str, str]:
    summary = {key: "NOT_TESTED" for key in SUMMARY_ORDER}
    if not probe_path.is_file():
        return summary
    try:
        probe = validate_probe_result(_load(probe_path))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {key: "ERROR" for key in SUMMARY_ORDER}

    python = _mapping(probe["python"])
    summary["PYTHON_RUNTIME"] = "PASS" if python["available"] else "UNAVAILABLE"
    summary["UNREAL_IMPORT"] = "PASS" if python["unrealImport"] else "UNAVAILABLE"
    classes = _mapping(probe["classes"])
    summary["ASSET_EDITOR_SUBSYSTEM"] = _status(classes, "AssetEditorSubsystem")
    summary["BLUEPRINT_EDITOR_LIBRARY"] = _status(classes, "BlueprintEditorLibrary")
    summary["EDITOR_UTILITY_SUBSYSTEM"] = _status(classes, "EditorUtilitySubsystem")
    class_summary = {
        "OBJECT_ITERATOR": "ObjectIterator",
        "ED_GRAPH_NODE_CLASS": "EdGraphNode",
        "K2_NODE_CLASS": "K2Node",
        "BLUEPRINT_GRAPH_EDITOR_CLASS": "BlueprintGraphEditor",
        "BLUEPRINT_GRAPH_PIN_LIBRARY_CLASS": "BlueprintGraphPinLibrary",
    }
    for summary_key, class_key in class_summary.items():
        summary[summary_key] = _status(classes, class_key)
    target = _mapping(probe["explicitTarget"])
    capabilities = _mapping(target["capabilities"])
    capability_summary = {
        "EXPLICIT_ASSET_LOAD": "explicitAssetLoad",
        "EXPLICIT_GRAPH_FIND": "explicitGraphFind",
        "DIRECT_GRAPH_PROPERTY": "directGraphProperty",
        "OBJECT_ITERATOR_EDGRAPHNODE": "objectIteratorEdGraphNode",
        "OBJECT_ITERATOR_K2NODE": "objectIteratorK2Node",
        "EXACT_OUTER_NODE_ENUMERATION": "exactOuterNodeEnumeration",
        "EXACT_TYPED_OUTER_NODE_ENUMERATION": "exactTypedOuterNodeEnumeration",
        "BLUEPRINT_LIST_GRAPHS": "blueprintListGraphs",
        "BLUEPRINT_LIST_GRAPH_NAMES": "blueprintListGraphNames",
        "GRAPH_NODE_ENUMERATION": "graphNodeEnumeration",
        "NODE_GUID_READ": "nodeGuidRead",
        "NODE_POSITION_READ": "nodePositionRead",
        "NODE_GET_POS": "nodeGetPos",
        "NODE_LIST_ALL_PINS": "nodeListAllPins",
        "NODE_PIN_READ": "nodePinRead",
        "BLUEPRINT_STATUS_READ": "blueprintStatusRead",
        "PACKAGE_DIRTY_READ": "packageDirtyRead",
        "OBJECT_GET_OUTER": "objectGetOuter",
        "OBJECT_GET_TYPED_OUTER": "objectGetTypedOuter",
        "OBJECT_GET_PATH_NAME": "objectGetPathName",
    }
    for summary_key, capability_key in capability_summary.items():
        summary[summary_key] = _status(capabilities, capability_key)

    active = _mapping(probe["activeEditor"])
    summary["ACTIVE_ASSET_READ"] = _status(active, "activeAsset")
    summary["FOCUSED_GRAPH_READ"] = _status(active, "focusedGraph")
    summary["SELECTION_READ"] = _status(active, "selection")
    summary["DIRTY_STATE_READ"] = _status(active, "dirtyState")
    summary["COMPILE_STATE_READ"] = _status(active, "compileState")

    if target.get("status") == "AVAILABLE":
        required_pass = all(
            (
                summary["PYTHON_RUNTIME"] == "PASS",
                summary["UNREAL_IMPORT"] == "PASS",
                summary["EXPLICIT_ASSET_LOAD"] == "PASS",
                summary["EXPLICIT_GRAPH_FIND"] == "PASS",
                summary["GRAPH_NODE_ENUMERATION"] == "PASS",
                summary["NODE_GUID_READ"] == "PASS",
                summary["NODE_POSITION_READ"] == "PASS",
                target.get("scanTruncated") is False,
                target.get("enumerationStrategy") in _SUCCESS_STRATEGIES,
            )
        )
        if not required_pass or not snapshot_path.is_file():
            summary["EXPLICIT_GRAPH_SNAPSHOT"] = "ERROR"
        else:
            try:
                snapshot = validate_snapshot_result(_load(snapshot_path))
                bound_keys = (
                    "objectPath",
                    "graphName",
                    "objectsScanned",
                    "matchingNodes",
                    "returnedNodes",
                    "nodesOmitted",
                    "scanTruncated",
                    "enumerationStrategy",
                    "compileStatus",
                )
                if any(snapshot.get(key) != target.get(key) for key in bound_keys):
                    raise ValueError("snapshot target binding mismatch")
                if snapshot.get("capabilities") != capabilities:
                    raise ValueError("snapshot capability binding mismatch")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                summary["EXPLICIT_GRAPH_SNAPSHOT"] = "ERROR"
            else:
                summary["EXPLICIT_GRAPH_SNAPSHOT"] = "PASS"
    elif target.get("status") == "MISSING":
        summary["EXPLICIT_GRAPH_SNAPSHOT"] = "UNAVAILABLE"
    elif target.get("status") == "ERROR":
        summary["EXPLICIT_GRAPH_SNAPSHOT"] = "ERROR"
    assert_path_free(summary)
    return summary


def render_validator_summary(summary: Mapping[str, str]) -> str:
    assert_path_free(summary)
    return "\n".join(f"{key}={summary[key]}" for key in SUMMARY_ORDER)


__all__ = [
    "SUMMARY_ORDER",
    "render_validator_summary",
    "validate_probe_result",
    "validate_snapshot_result",
    "validator_summary",
]
