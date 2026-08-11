"""Fail-closed reader for the local atomic ARK DevKit Editor snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from arkdev_mcp.editor_bridge import (
    EDITOR_STATE_SCHEMA,
    MUTATION_CAPABILITIES,
    EditorCapability,
)
from arkdev_mcp.tasking.canonical import canonical_json


EDITOR_SNAPSHOT_SCHEMA = "blueprint-to-code.arkdev-editor-snapshot/v1"
EDITOR_SNAPSHOT_PROTOCOL = "arkdev-editor-snapshot/v1"
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_NODES = 2000
DEFAULT_MAX_AGE_SEC = 6.0
MAX_FUTURE_SKEW_SEC = 2.0

_GUID = re.compile(r"^[0-9A-Fa-f]{32}$")
_READ_CAPABILITIES = frozenset(
    capability.value
    for capability in EditorCapability
    if capability.value not in MUTATION_CAPABILITIES
)
_MINIMUM_CAPABILITIES = frozenset(
    {
        EditorCapability.READ_ACTIVE_ASSET.value,
        EditorCapability.READ_ACTIVE_GRAPH.value,
        EditorCapability.READ_GRAPH_POSITIONS.value,
        EditorCapability.READ_DIRTY_STATE.value,
        EditorCapability.READ_COMPILE_STATE.value,
    }
)
_COMPILE_STATUSES = frozenset({"UP_TO_DATE", "DIRTY", "ERROR", "UNKNOWN"})
_ACTIVITY_STATUSES = frozenset(
    {
        "ACTIVE_BLUEPRINT",
        "NO_OPEN_BLUEPRINT",
        "ACTIVE_BLUEPRINT_AMBIGUOUS",
    }
)
_GRAPH_STATUSES = frozenset(
    {
        "NO_ACTIVE_BLUEPRINT",
        "BLUEPRINT_EDITOR_INTERFACE_UNAVAILABLE",
        "NO_FOCUSED_GRAPH",
        "FOCUSED_GRAPH",
    }
)
_SELECTION_STATUSES = frozenset(
    {"AVAILABLE", "UNSUPPORTED_BY_DEVKIT_BUILD"}
)


@dataclass(frozen=True)
class _SnapshotIssue(Exception):
    state_status: str
    reason_code: str


@dataclass(frozen=True)
class _LoadedSnapshot:
    payload: dict[str, object]
    raw_sha256: str
    semantic_digest: str
    written_at: datetime
    age_ms: int


def _text(
    value: object,
    *,
    field: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    if (not value and not allow_empty) or len(value) > maximum:
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    return value


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    if value < minimum or value > maximum:
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    return value


def _object_or_none(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    return value


def _guid(value: object) -> str:
    candidate = _text(value, field="nodeGuid", maximum=32)
    if _GUID.fullmatch(candidate) is None:
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    return candidate.upper()


def _node(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    return {
        "nodeGuid": _guid(value.get("nodeGuid")),
        "name": _text(value.get("name"), field="nodeName", maximum=256),
        "className": _text(
            value.get("className"), field="className", maximum=256
        ),
        "x": _integer(value.get("x"), minimum=-(2**31), maximum=(2**31) - 1),
        "y": _integer(value.get("y"), minimum=-(2**31), maximum=(2**31) - 1),
    }


def _timestamp(value: object) -> datetime:
    candidate = _text(value, field="writtenAtUtc", maximum=64)
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _SnapshotIssue(
            "STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID"
        ) from exc
    if parsed.tzinfo is None:
        raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
    return parsed.astimezone(timezone.utc)


def _semantic_digest(payload: Mapping[str, object]) -> str:
    semantic = dict(payload)
    semantic.pop("writtenAtUtc", None)
    semantic.pop("sequence", None)
    return hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()


class FileEditorBridge:
    """Read a bounded snapshot without exposing its machine-local location."""

    def __init__(
        self,
        state_file: str | Path,
        *,
        max_age_sec: float = DEFAULT_MAX_AGE_SEC,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1.0 <= float(max_age_sec) <= 30.0:
            raise ValueError("max_age_sec must be between 1 and 30 seconds")
        self._state_file = Path(state_file)
        self._max_age_sec = float(max_age_sec)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def for_project_root(cls, project_root: str | Path) -> FileEditorBridge:
        override = os.environ.get("ARKDEV_EDITOR_BRIDGE_STATE_FILE")
        state_file = (
            Path(override)
            if override
            else Path(project_root) / ".arkdev-bridge" / "editor_state.json"
        )
        return cls(state_file)

    def get_state(
        self,
        *,
        include_selection: bool,
        include_graph_nodes: bool = False,
        max_graph_nodes: int = 200,
    ) -> dict[str, object]:
        if max_graph_nodes < 1 or max_graph_nodes > 1000:
            raise ValueError("max_graph_nodes must be between 1 and 1000")
        try:
            loaded = self._load()
            return self._public_state(
                loaded,
                include_selection=include_selection,
                include_graph_nodes=include_graph_nodes,
                max_graph_nodes=max_graph_nodes,
            )
        except FileNotFoundError:
            return self._disconnected(
                "STATE_NOT_FOUND", "EDITOR_BRIDGE_STATE_NOT_FOUND"
            )
        except _SnapshotIssue as issue:
            return self._disconnected(issue.state_status, issue.reason_code)

    def get_capabilities(self) -> tuple[str, ...]:
        state = self.get_state(
            include_selection=False,
            include_graph_nodes=False,
            max_graph_nodes=200,
        )
        if not state.get("connected"):
            return ()
        return tuple(str(item) for item in state.get("capabilities", []))

    def health(self) -> dict[str, object]:
        try:
            loaded = self._load(include_node_details=False)
            connected = bool(loaded.payload.get("connected"))
            state_status = (
                "CONNECTED" if connected else "PLUGIN_REPORTED_DISCONNECTED"
            )
            reason_code = (
                "" if connected else "EDITOR_BRIDGE_PLUGIN_DISCONNECTED"
            )
        except FileNotFoundError:
            connected = False
            state_status = "STATE_NOT_FOUND"
            reason_code = "EDITOR_BRIDGE_STATE_NOT_FOUND"
        except _SnapshotIssue as issue:
            connected = False
            state_status = issue.state_status
            reason_code = issue.reason_code
        return {
            "connected": connected,
            "status": "CONNECTED" if connected else "DISCONNECTED",
            "stateStatus": state_status,
            "reasonCode": reason_code,
        }

    def _read_bytes(self) -> bytes:
        before = self._state_file.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if before.st_size > MAX_SNAPSHOT_BYTES:
            raise _SnapshotIssue(
                "STATE_OVERSIZED", "EDITOR_BRIDGE_STATE_OVERSIZED"
            )
        with self._state_file.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise _SnapshotIssue(
                    "STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID"
                )
            raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise _SnapshotIssue(
                "STATE_OVERSIZED", "EDITOR_BRIDGE_STATE_OVERSIZED"
            )
        if len(raw) != opened.st_size:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        return raw

    def _load(self, *, include_node_details: bool = True) -> _LoadedSnapshot:
        raw = self._read_bytes()
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _SnapshotIssue(
                "STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID"
            ) from exc
        if not isinstance(decoded, dict):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        normalized = self._normalize(
            decoded,
            include_node_details=include_node_details,
        )
        written_at = _timestamp(normalized["writtenAtUtc"])
        now = self._clock()
        if now.tzinfo is None:
            raise RuntimeError("FileEditorBridge clock must return an aware datetime")
        age_sec = (now.astimezone(timezone.utc) - written_at).total_seconds()
        if age_sec < -MAX_FUTURE_SKEW_SEC:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if age_sec > self._max_age_sec:
            raise _SnapshotIssue("STATE_STALE", "EDITOR_BRIDGE_STATE_STALE")
        return _LoadedSnapshot(
            payload=normalized,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            semantic_digest=_semantic_digest(normalized),
            written_at=written_at,
            age_ms=max(0, int(round(age_sec * 1000))),
        )

    def _normalize(
        self,
        payload: Mapping[str, object],
        *,
        include_node_details: bool,
    ) -> dict[str, object]:
        if payload.get("schema") != EDITOR_SNAPSHOT_SCHEMA:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if payload.get("protocolVersion") != EDITOR_SNAPSHOT_PROTOCOL:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        capabilities_raw = payload.get("capabilities")
        if not isinstance(capabilities_raw, Sequence) or isinstance(
            capabilities_raw, (str, bytes, bytearray)
        ):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        capabilities = [
            _text(item, field="capability", maximum=64)
            for item in capabilities_raw
        ]
        advertised = set(capabilities)
        if (
            len(advertised) != len(capabilities)
            or advertised - _READ_CAPABILITIES
            or advertised & MUTATION_CAPABILITIES
        ):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        connected = _boolean(payload.get("connected"))
        if connected and not _MINIMUM_CAPABILITIES.issubset(advertised):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        activity_status = _text(
            payload.get("activityStatus"),
            field="activityStatus",
            maximum=64,
        )
        if activity_status not in _ACTIVITY_STATUSES:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        graph_status = _text(
            payload.get("graphStatus"),
            field="graphStatus",
            maximum=64,
        )
        if graph_status not in _GRAPH_STATUSES:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")

        active_blueprint = self._normalize_blueprint(
            _object_or_none(payload.get("activeBlueprint"))
        )
        focused_graph = self._normalize_graph(
            _object_or_none(payload.get("focusedGraph")),
            include_node_details=include_node_details,
        )
        if activity_status == "ACTIVE_BLUEPRINT" and active_blueprint is None:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if activity_status != "ACTIVE_BLUEPRINT" and (
            active_blueprint is not None or focused_graph is not None
        ):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if focused_graph is not None and active_blueprint is None:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if active_blueprint is None and graph_status != "NO_ACTIVE_BLUEPRINT":
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if (
            active_blueprint is not None
            and focused_graph is None
            and graph_status not in {
                "BLUEPRINT_EDITOR_INTERFACE_UNAVAILABLE",
                "NO_FOCUSED_GRAPH",
            }
        ):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if focused_graph is not None and graph_status != "FOCUSED_GRAPH":
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")

        selection_status = _text(
            payload.get("selectionStatus"),
            field="selectionStatus",
            maximum=64,
        )
        if selection_status not in _SELECTION_STATUSES:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        selected_raw = payload.get("selectedNodeGuids")
        if not isinstance(selected_raw, Sequence) or isinstance(
            selected_raw, (str, bytes, bytearray)
        ) or len(selected_raw) > MAX_SNAPSHOT_NODES:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        selected = sorted({_guid(item) for item in selected_raw})
        selection_supported = EditorCapability.READ_SELECTION.value in advertised
        if selection_supported != (selection_status == "AVAILABLE"):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if not selection_supported and selected:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")

        return {
            "schema": EDITOR_SNAPSHOT_SCHEMA,
            "protocolVersion": EDITOR_SNAPSHOT_PROTOCOL,
            "bridgeVersion": _text(
                payload.get("bridgeVersion"), field="bridgeVersion", maximum=128
            ),
            "bridgeInstanceId": _text(
                payload.get("bridgeInstanceId"),
                field="bridgeInstanceId",
                maximum=128,
            ),
            "sequence": _integer(
                payload.get("sequence"), minimum=0, maximum=(2**63) - 1
            ),
            "writtenAtUtc": _text(
                payload.get("writtenAtUtc"), field="writtenAtUtc", maximum=64
            ),
            "engineVersion": _text(
                payload.get("engineVersion"),
                field="engineVersion",
                maximum=128,
            ),
            "buildVersion": _text(
                payload.get("buildVersion"), field="buildVersion", maximum=128
            ),
            "connected": connected,
            "activityStatus": activity_status,
            "graphStatus": graph_status,
            "capabilities": sorted(advertised),
            "activeBlueprint": active_blueprint,
            "focusedGraph": focused_graph,
            "selectionStatus": selection_status,
            "selectedNodeGuids": selected,
            "reasonCode": _text(
                payload.get("reasonCode", ""),
                field="reasonCode",
                maximum=128,
                allow_empty=True,
            ),
        }

    @staticmethod
    def _normalize_blueprint(
        value: Mapping[str, object] | None,
    ) -> dict[str, object] | None:
        if value is None:
            return None
        compile_status = _text(
            value.get("compileStatus"), field="compileStatus", maximum=32
        )
        if compile_status not in _COMPILE_STATUSES:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        return {
            "name": _text(value.get("name"), field="assetName", maximum=256),
            "objectPath": _text(
                value.get("objectPath"), field="objectPath", maximum=4096
            ),
            "dirty": _boolean(value.get("dirty")),
            "compileStatus": compile_status,
        }

    @staticmethod
    def _normalize_graph(
        value: Mapping[str, object] | None,
        *,
        include_node_details: bool,
    ) -> dict[str, object] | None:
        if value is None:
            return None
        raw_nodes = value.get("nodes")
        if not isinstance(raw_nodes, Sequence) or isinstance(
            raw_nodes, (str, bytes, bytearray)
        ) or len(raw_nodes) > MAX_SNAPSHOT_NODES:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        nodes: list[dict[str, object]] = []
        if include_node_details:
            nodes.extend(_node(raw) for raw in raw_nodes)
            nodes.sort(
                key=lambda item: (
                    item["y"],
                    item["x"],
                    item["nodeGuid"],
                    item["name"],
                )
            )
        else:
            for raw in raw_nodes:
                _node(raw)
        node_count = _integer(
            value.get("nodeCount"), minimum=0, maximum=(2**31) - 1
        )
        omitted = _integer(
            value.get("nodesOmitted"), minimum=0, maximum=(2**31) - 1
        )
        truncated = _boolean(value.get("nodesTruncated"))
        if node_count != len(raw_nodes) + omitted:
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        if truncated != (omitted > 0):
            raise _SnapshotIssue("STATE_INVALID", "EDITOR_BRIDGE_STATE_INVALID")
        return {
            "name": _text(value.get("name"), field="graphName", maximum=256),
            "pathName": _text(
                value.get("pathName"), field="graphPathName", maximum=4096
            ),
            "schemaClass": _text(
                value.get("schemaClass"), field="schemaClass", maximum=256
            ),
            "graphType": _text(
                value.get("graphType"), field="graphType", maximum=64
            ),
            "nodeCount": node_count,
            "nodesTruncated": truncated,
            "nodesOmitted": omitted,
            "nodes": nodes,
        }

    @staticmethod
    def _disconnected(state_status: str, reason_code: str) -> dict[str, object]:
        return {
            "schema": EDITOR_STATE_SCHEMA,
            "connected": False,
            "bridgeVersion": "",
            "devkitBuild": "",
            "activeAsset": None,
            "activeGraph": None,
            "selectedNodes": [],
            "dirty": None,
            "compileStatus": "UNKNOWN",
            "capabilities": [],
            "reasonCode": reason_code,
            "stateStatus": state_status,
            "snapshot": {
                "protocolVersion": "",
                "bridgeInstanceId": "",
                "sequence": 0,
                "writtenAtUtc": "",
                "ageMs": None,
                "rawSha256": "",
                "semanticDigest": "",
            },
            "activityStatus": "DISCONNECTED",
            "graphStatus": "DISCONNECTED",
            "selectionStatus": "NOT_REQUESTED",
            "activeAssetDetails": None,
            "activeGraphDetails": None,
            "activeAssetBinding": {},
            "activeGraphBinding": {},
            "graphNodes": [],
            "graphNodeSummary": {
                "returned": 0,
                "total": 0,
                "omitted": 0,
                "snapshotTruncated": False,
            },
            "taskBinding": {},
        }

    @staticmethod
    def _public_state(
        loaded: _LoadedSnapshot,
        *,
        include_selection: bool,
        include_graph_nodes: bool,
        max_graph_nodes: int,
    ) -> dict[str, object]:
        payload = loaded.payload
        active = payload.get("activeBlueprint")
        active = dict(active) if isinstance(active, Mapping) else None
        graph = payload.get("focusedGraph")
        graph = dict(graph) if isinstance(graph, Mapping) else None
        all_nodes = list(graph.get("nodes", [])) if graph else []
        nodes = all_nodes[:max_graph_nodes] if include_graph_nodes else []
        total = int(graph.get("nodeCount", 0)) if graph else 0
        connected = bool(payload["connected"])
        reason_code = (
            "" if connected else "EDITOR_BRIDGE_PLUGIN_DISCONNECTED"
        )
        return {
            "schema": EDITOR_STATE_SCHEMA,
            "connected": connected,
            "bridgeVersion": str(payload["bridgeVersion"]),
            "devkitBuild": str(payload["buildVersion"]),
            "activeAsset": active.get("objectPath") if active else None,
            "activeGraph": graph.get("pathName") if graph else None,
            "selectedNodes": (
                list(payload.get("selectedNodeGuids", []))
                if include_selection
                and EditorCapability.READ_SELECTION.value
                in payload.get("capabilities", [])
                else []
            ),
            "dirty": active.get("dirty") if active else None,
            "compileStatus": (
                str(active.get("compileStatus")) if active else "UNKNOWN"
            ),
            "capabilities": list(payload.get("capabilities", [])),
            "reasonCode": reason_code,
            "stateStatus": "CONNECTED" if connected else "PLUGIN_REPORTED_DISCONNECTED",
            "snapshot": {
                "protocolVersion": str(payload["protocolVersion"]),
                "bridgeInstanceId": str(payload["bridgeInstanceId"]),
                "sequence": int(payload["sequence"]),
                "writtenAtUtc": str(payload["writtenAtUtc"]),
                "ageMs": loaded.age_ms,
                "rawSha256": loaded.raw_sha256,
                "semanticDigest": loaded.semantic_digest,
            },
            "activityStatus": str(payload["activityStatus"]),
            "graphStatus": str(payload["graphStatus"]),
            "selectionStatus": (
                str(payload["selectionStatus"])
                if include_selection
                else "NOT_REQUESTED"
            ),
            "activeAssetDetails": active,
            "activeGraphDetails": (
                {
                    key: value
                    for key, value in graph.items()
                    if key not in {"nodes", "nodesTruncated", "nodesOmitted"}
                }
                if graph
                else None
            ),
            "activeAssetBinding": {},
            "activeGraphBinding": {},
            "graphNodes": nodes,
            "graphNodeSummary": {
                "returned": len(nodes),
                "total": total,
                "omitted": max(0, total - len(nodes)),
                "snapshotTruncated": bool(
                    graph.get("nodesTruncated", False) if graph else False
                ),
            },
            "taskBinding": {},
        }


__all__ = [
    "DEFAULT_MAX_AGE_SEC",
    "EDITOR_SNAPSHOT_PROTOCOL",
    "EDITOR_SNAPSHOT_SCHEMA",
    "MAX_SNAPSHOT_BYTES",
    "MAX_SNAPSHOT_NODES",
    "FileEditorBridge",
]
