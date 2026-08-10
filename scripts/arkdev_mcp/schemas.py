"""Pydantic output contracts advertised by the Phase 1 MCP tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class PublicOutput(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ErrorOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.arkdev-mcp-error/v1"] = Field(
        alias="schema"
    )
    code: Literal[
        "INVALID_ARGUMENT",
        "ASSET_NOT_FOUND",
        "EVIDENCE_NOT_FOUND",
        "EVIDENCE_STALE",
        "EVIDENCE_NOT_AUTHORITATIVE",
        "EVIDENCE_REVISION_MISMATCH",
        "GRAPH_SELECTION_REQUIRED",
        "NODE_NOT_FOUND",
        "RESULT_BUDGET_EXCEEDED",
        "EDITOR_BRIDGE_NOT_INSTALLED",
        "EDITOR_BRIDGE_UNAVAILABLE",
        "INTERNAL_CONTRACT_ERROR",
    ]
    message: str
    retryable: bool
    details: dict[str, Any]


class StatusCapabilities(PublicOutput):
    blueprintEvidence: bool
    blueprintInterpretation: bool
    boundedGraphContext: bool
    editorBridge: bool
    patchPlan: Literal[False]
    mutation: Literal[False]


class StatusEditorBridge(PublicOutput):
    status: str
    reasonCode: str


class StatusOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.arkdev-mcp-status/v1"] = Field(
        alias="schema"
    )
    serverVersion: str
    mcpSdkVersion: str
    projectVersion: str
    platform: Literal["windows-x64"]
    transport: Literal["stdio"]
    readOnly: Literal[True]
    capabilities: StatusCapabilities
    editorBridge: StatusEditorBridge


class EditorStateOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.arkdev-editor-state/v1"] = Field(
        alias="schema"
    )
    connected: bool
    bridgeVersion: str
    devkitBuild: str
    activeAsset: str | None
    activeGraph: str | None
    selectedNodes: list[str]
    dirty: bool | None
    compileStatus: str
    capabilities: list[str]
    reasonCode: str


class AssetListOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.mcp-blueprint-assets/v1"] = Field(
        alias="schema"
    )
    items: list[dict[str, Any]]
    page: dict[str, Any]


class BlueprintContextOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.mcp-blueprint-context/v1"] = Field(
        alias="schema"
    )
    identity: dict[str, Any]
    freshness: str
    goal: str
    graphTargets: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    pins: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    omitted: dict[str, int]
    truncated: bool
    querySignature: str
    continuation: str


class BlueprintNodeOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.mcp-blueprint-node/v1"] = Field(
        alias="schema"
    )
    identity: dict[str, Any]
    freshness: str
    node: dict[str, Any]
    neighborhood: list[dict[str, Any]]
    pins: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    defaults: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    truncated: bool


class StatusToolOutput(RootModel[StatusOutput | ErrorOutput]):
    pass


class EditorToolOutput(RootModel[EditorStateOutput | ErrorOutput]):
    pass


class AssetListToolOutput(RootModel[AssetListOutput | ErrorOutput]):
    pass


class ContextToolOutput(RootModel[BlueprintContextOutput | ErrorOutput]):
    pass


class NodeToolOutput(RootModel[BlueprintNodeOutput | ErrorOutput]):
    pass


__all__ = [
    "AssetListOutput",
    "AssetListToolOutput",
    "BlueprintContextOutput",
    "BlueprintNodeOutput",
    "ContextToolOutput",
    "EditorStateOutput",
    "EditorToolOutput",
    "ErrorOutput",
    "NodeToolOutput",
    "StatusOutput",
    "StatusToolOutput",
]
