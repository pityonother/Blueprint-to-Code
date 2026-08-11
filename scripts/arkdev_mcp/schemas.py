"""Pydantic output contracts advertised by the ARK Dev MCP tools."""

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
        "TASK_NOT_FOUND",
        "TASK_PHASE_INVALID",
        "TASK_BLOCKED",
        "TASK_SLICE_LIMIT_REACHED",
        "EVIDENCE_REVISION_CHANGED",
        "PATCH_PLAN_NOT_FOUND",
        "PATCH_PLAN_INVALID",
        "PATCH_PLAN_LIMIT_EXCEEDED",
        "PATCH_PLAN_NOT_CONFIRMABLE",
        "PATCH_PLAN_DIGEST_MISMATCH",
        "PLAN_CONFIRMATION_REQUIRED",
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
    taskContext: bool
    patchPlan: bool
    localTaskMetadataWrite: bool
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
    taskMetadataWrite: bool
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


class TaskContextOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.task-context/v1"] = Field(alias="schema")
    taskId: str
    mode: str
    goal: str
    primaryAsset: dict[str, Any]
    graphTargets: list[dict[str, Any]]
    readiness: str
    phase: Literal["DISCOVERY"]
    nextRecommendedTool: Literal["blueprint_task_research"]
    semanticDigest: str


class TaskResumeOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.task-resume/v1"] = Field(alias="schema")
    taskId: str
    phase: str
    goal: str
    evidenceIdentity: dict[str, Any]
    queryLedger: dict[str, Any]
    plan: dict[str, Any]


class GraphSliceOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.graph-slice/v1"] = Field(alias="schema")
    sliceId: str
    querySignature: str
    question: str
    graphTargets: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    pins: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    cached: bool
    queryLedger: dict[str, Any]
    taskReadiness: str
    semanticDigest: str


class PatchPlanOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.blueprint-patch-plan/v1"] = Field(
        alias="schema"
    )
    planId: str
    taskId: str
    status: Literal["DRAFT", "CONFIRMED"]
    target: dict[str, Any]
    capabilityRequirements: list[str]
    nodes: list[dict[str, Any]]
    operations: list[dict[str, Any]]
    blockingQuestions: list[dict[str, Any]]
    semanticDigest: str
    executionReady: Literal[False]


class PatchPlanValidationOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.patch-plan-validation/v1"] = Field(
        alias="schema"
    )
    taskId: str
    planId: str
    valid: bool
    confirmable: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    executionReady: Literal[False]
    semanticDigest: str
    humanSummary: str


class PatchPlanConfirmationOutput(PublicOutput):
    schema_: Literal["blueprint-to-code.patch-plan-confirmation/v1"] = Field(
        alias="schema"
    )
    confirmed: Literal[True]
    planId: str
    semanticDigest: str
    capabilityRequirements: list[str]
    executionReady: Literal[False]
    nextPhase: Literal["READ_ONLY_EDITOR_BRIDGE"]


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


class TaskCreateToolOutput(RootModel[TaskContextOutput | ErrorOutput]):
    pass


class TaskResumeToolOutput(RootModel[TaskResumeOutput | ErrorOutput]):
    pass


class TaskResearchToolOutput(RootModel[GraphSliceOutput | ErrorOutput]):
    pass


class PatchPlanDraftToolOutput(RootModel[PatchPlanOutput | ErrorOutput]):
    pass


class PatchPlanValidateToolOutput(
    RootModel[PatchPlanValidationOutput | ErrorOutput]
):
    pass


class PatchPlanConfirmToolOutput(
    RootModel[PatchPlanConfirmationOutput | ErrorOutput]
):
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
    "PatchPlanConfirmToolOutput",
    "PatchPlanDraftToolOutput",
    "PatchPlanValidateToolOutput",
    "StatusOutput",
    "StatusToolOutput",
    "TaskCreateToolOutput",
    "TaskResearchToolOutput",
    "TaskResumeToolOutput",
]
