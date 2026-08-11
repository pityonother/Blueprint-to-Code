"""Fail-closed readers and path-free summaries for live probe results."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from arkdev_scripting_probe.contracts import (
    MAX_GRAPH_NODES,
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
    "EXPLICIT_ASSET_LOAD",
    "EXPLICIT_GRAPH_FIND",
    "GRAPH_NODE_ENUMERATION",
    "NODE_GUID_READ",
    "NODE_POSITION_READ",
    "ACTIVE_ASSET_READ",
    "FOCUSED_GRAPH_READ",
    "SELECTION_READ",
    "DIRTY_STATE_READ",
    "COMPILE_STATE_READ",
    "EXPLICIT_GRAPH_SNAPSHOT",
)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("expected object")
    return value


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("expected array")
    return value


def _digest_matches(value: Mapping[str, object]) -> None:
    digest = require_digest(value.get("semanticDigest"))
    if digest != semantic_digest(value):
        raise ValueError("semantic digest mismatch")


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
    for group_name in ("classes", "methods", "activeEditor", "editorUtility"):
        group = _mapping(payload.get(group_name))
        for status in group.values():
            require_status(status)
    target = _mapping(payload.get("explicitTarget"))
    require_status(target.get("status"))
    for status in _mapping(target.get("capabilities", {})).values():
        require_status(status)
    for item in _sequence(payload.get("mutationApisObserved")):
        mutation = _mapping(item)
        if mutation.get("status") != "PRESENT_BUT_NOT_USED":
            raise ValueError("mutation API status is unsafe")
    _sequence(payload.get("gaps"))
    _digest_matches(payload)
    assert_path_free(payload)
    return payload


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
    node_count = payload.get("nodeCount")
    returned = payload.get("nodesReturned")
    omitted = payload.get("nodesOmitted")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (node_count, returned, omitted)
    ):
        raise ValueError("invalid node counts")
    assert isinstance(node_count, int)
    assert isinstance(returned, int)
    assert isinstance(omitted, int)
    nodes = _sequence(payload.get("nodes"))
    if returned != len(nodes) or returned > MAX_GRAPH_NODES:
        raise ValueError("invalid returned node count")
    if node_count != returned + omitted:
        raise ValueError("invalid omitted node count")
    for raw in nodes:
        node = _mapping(raw)
        for key in ("name", "className"):
            if not isinstance(node.get(key), str):
                raise ValueError("invalid node display field")
        guid = node.get("nodeGuid")
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
    for status in _mapping(payload.get("capabilities")).values():
        require_status(status)
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
    summary["BLUEPRINT_EDITOR_LIBRARY"] = _status(
        classes,
        "BlueprintEditorLibrary",
    )
    summary["EDITOR_UTILITY_SUBSYSTEM"] = _status(
        classes,
        "EditorUtilitySubsystem",
    )
    target = _mapping(probe["explicitTarget"])
    capabilities = _mapping(target.get("capabilities", {}))
    summary["EXPLICIT_ASSET_LOAD"] = _status(capabilities, "explicitAssetLoad")
    summary["EXPLICIT_GRAPH_FIND"] = _status(capabilities, "explicitGraphFind")
    summary["GRAPH_NODE_ENUMERATION"] = _status(
        capabilities,
        "graphNodeEnumeration",
    )
    summary["NODE_GUID_READ"] = _status(capabilities, "nodeGuidRead")
    summary["NODE_POSITION_READ"] = _status(capabilities, "nodePositionRead")
    active = _mapping(probe["activeEditor"])
    summary["ACTIVE_ASSET_READ"] = _status(active, "activeAsset")
    summary["FOCUSED_GRAPH_READ"] = _status(active, "focusedGraph")
    summary["SELECTION_READ"] = _status(active, "selection")
    summary["DIRTY_STATE_READ"] = _status(active, "dirtyState")
    summary["COMPILE_STATE_READ"] = _status(active, "compileState")

    if target.get("status") == "AVAILABLE":
        if not snapshot_path.is_file():
            summary["EXPLICIT_GRAPH_SNAPSHOT"] = "ERROR"
        else:
            try:
                snapshot = validate_snapshot_result(_load(snapshot_path))
                if (
                    snapshot.get("objectPath") != target.get("objectPath")
                    or snapshot.get("graphName") != target.get("graphName")
                    or snapshot.get("capabilities") != capabilities
                ):
                    raise ValueError("snapshot target binding mismatch")
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
