"""Read-only protocol boundary shared by live and fixture Editor bridges."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


EDITOR_STATE_SCHEMA = "blueprint-to-code.arkdev-editor-state/v1"


class EditorCapability(StrEnum):
    READ_ACTIVE_ASSET = "READ_ACTIVE_ASSET"
    READ_ACTIVE_GRAPH = "READ_ACTIVE_GRAPH"
    READ_SELECTION = "READ_SELECTION"
    READ_GRAPH_POSITIONS = "READ_GRAPH_POSITIONS"
    READ_DIRTY_STATE = "READ_DIRTY_STATE"
    READ_COMPILE_STATE = "READ_COMPILE_STATE"
    IMPORT_NODES_FROM_TEXT = "IMPORT_NODES_FROM_TEXT"
    CREATE_NODE = "CREATE_NODE"
    DELETE_NODE = "DELETE_NODE"
    BREAK_PIN_LINKS = "BREAK_PIN_LINKS"
    CREATE_CONNECTION = "CREATE_CONNECTION"
    SET_PIN_DEFAULT = "SET_PIN_DEFAULT"
    MOVE_NODE = "MOVE_NODE"
    COMPILE_BLUEPRINT = "COMPILE_BLUEPRINT"
    SAVE_ASSET = "SAVE_ASSET"
    GRAPH_DIFF = "GRAPH_DIFF"
    ROLLBACK = "ROLLBACK"


MUTATION_CAPABILITIES = frozenset(
    {
        EditorCapability.IMPORT_NODES_FROM_TEXT.value,
        EditorCapability.CREATE_NODE.value,
        EditorCapability.DELETE_NODE.value,
        EditorCapability.BREAK_PIN_LINKS.value,
        EditorCapability.CREATE_CONNECTION.value,
        EditorCapability.SET_PIN_DEFAULT.value,
        EditorCapability.MOVE_NODE.value,
        EditorCapability.COMPILE_BLUEPRINT.value,
        EditorCapability.SAVE_ASSET.value,
        EditorCapability.GRAPH_DIFF.value,
        EditorCapability.ROLLBACK.value,
    }
)


class EditorBridge(Protocol):
    def get_state(
        self,
        *,
        include_selection: bool,
        include_graph_nodes: bool = False,
        max_graph_nodes: int = 200,
    ) -> dict[str, object]: ...

    def get_capabilities(self) -> tuple[str, ...]: ...

    def health(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class DisconnectedEditorBridge:
    reason_code: str = "EDITOR_BRIDGE_NOT_INSTALLED"

    def get_state(
        self,
        *,
        include_selection: bool,
        include_graph_nodes: bool = False,
        max_graph_nodes: int = 200,
    ) -> dict[str, object]:
        del include_selection, include_graph_nodes, max_graph_nodes
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
            "reasonCode": self.reason_code,
            "stateStatus": "DISCONNECTED",
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

    def get_capabilities(self) -> tuple[str, ...]:
        return ()

    def health(self) -> dict[str, object]:
        return {
            "connected": False,
            "status": "DISCONNECTED",
            "reasonCode": self.reason_code,
        }


@dataclass(frozen=True)
class FixtureEditorBridge:
    active_asset: str | None = None
    active_graph: str | None = None
    selected_nodes: tuple[str, ...] = ()
    capabilities: tuple[EditorCapability, ...] = ()
    dirty: bool | None = False
    compile_status: str = "UP_TO_DATE"
    activity_status: str = "ACTIVE_BLUEPRINT"
    graph_status: str | None = None
    graph_nodes: tuple[dict[str, object], ...] = ()

    def __post_init__(self) -> None:
        advertised = {capability.value for capability in self.capabilities}
        if advertised & MUTATION_CAPABILITIES:
            raise ValueError("FixtureEditorBridge cannot advertise mutation capabilities")

    def get_state(
        self,
        *,
        include_selection: bool,
        include_graph_nodes: bool = False,
        max_graph_nodes: int = 200,
    ) -> dict[str, object]:
        if max_graph_nodes < 1 or max_graph_nodes > 1000:
            raise ValueError("max_graph_nodes must be between 1 and 1000")
        nodes = list(self.graph_nodes[:max_graph_nodes]) if include_graph_nodes else []
        total_nodes = len(self.graph_nodes)
        selection_available = (
            EditorCapability.READ_SELECTION in self.capabilities
        )
        graph_status = self.graph_status or (
            "FOCUSED_GRAPH"
            if self.active_graph
            else ("NO_FOCUSED_GRAPH" if self.active_asset else "NO_ACTIVE_BLUEPRINT")
        )
        return {
            "schema": EDITOR_STATE_SCHEMA,
            "connected": True,
            "bridgeVersion": "fixture/read-only-v1",
            "devkitBuild": "fixture",
            "activeAsset": self.active_asset,
            "activeGraph": self.active_graph,
            "selectedNodes": list(self.selected_nodes) if include_selection else [],
            "dirty": self.dirty,
            "compileStatus": self.compile_status,
            "capabilities": list(self.get_capabilities()),
            "reasonCode": "",
            "stateStatus": "CONNECTED",
            "snapshot": {
                "protocolVersion": "fixture/v1",
                "bridgeInstanceId": "fixture",
                "sequence": 1,
                "writtenAtUtc": "",
                "ageMs": 0,
                "rawSha256": "0" * 64,
                "semanticDigest": "0" * 64,
            },
            "activityStatus": self.activity_status,
            "graphStatus": graph_status,
            "selectionStatus": (
                "NOT_REQUESTED"
                if not include_selection
                else (
                    "AVAILABLE"
                    if selection_available
                    else "UNSUPPORTED_BY_DEVKIT_BUILD"
                )
            ),
            "activeAssetDetails": (
                {"name": self.active_asset.rsplit(".", 1)[-1], "objectPath": self.active_asset}
                if self.active_asset
                else None
            ),
            "activeGraphDetails": (
                {"name": self.active_graph, "pathName": self.active_graph}
                if self.active_graph
                else None
            ),
            "activeAssetBinding": {},
            "activeGraphBinding": {},
            "graphNodes": nodes,
            "graphNodeSummary": {
                "returned": len(nodes),
                "total": total_nodes,
                "omitted": total_nodes - len(nodes),
                "snapshotTruncated": False,
            },
            "taskBinding": {},
        }

    def get_capabilities(self) -> tuple[str, ...]:
        return tuple(capability.value for capability in self.capabilities)

    def health(self) -> dict[str, object]:
        return {"connected": True, "status": "CONNECTED", "reasonCode": ""}


__all__ = [
    "EDITOR_STATE_SCHEMA",
    "MUTATION_CAPABILITIES",
    "DisconnectedEditorBridge",
    "EditorBridge",
    "EditorCapability",
    "FixtureEditorBridge",
]
