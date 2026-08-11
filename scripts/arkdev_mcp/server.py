"""Official MCP Python SDK v2 composition root for Evidence and Task planning."""

from __future__ import annotations

import os
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from .blueprint_service import BlueprintService
from .contracts import McpExecutionError, assert_path_free
from .editor_bridge import (
    MUTATION_CAPABILITIES,
    EditorBridge,
)
from .editor_binding import EditorBindingService
from .editor_bridge_file import FileEditorBridge
from .prompts import register_prompts
from .resources import register_resources
from .schemas import (
    AssetListToolOutput,
    ContextToolOutput,
    EditorToolOutput,
    NodeToolOutput,
    PatchPlanConfirmToolOutput,
    PatchPlanDraftToolOutput,
    PatchPlanValidateToolOutput,
    StatusToolOutput,
    TaskCreateToolOutput,
    TaskResearchToolOutput,
    TaskResumeToolOutput,
)
from .tasking.plan_service import PlanService
from .tasking.research_service import ResearchService
from .tasking.store import TaskStore
from .tasking.task_service import TaskService


SERVER_VERSION = "0.2.0"
STATUS_SCHEMA = "blueprint-to-code.arkdev-mcp-status/v1"
READ_ONLY_DESCRIPTION = "READ-ONLY. NO ARK DEVKIT MUTATION."
METADATA_WRITE_DESCRIPTION = (
    "WRITES LOCAL TASK METADATA ONLY. "
    "DOES NOT MODIFY ARK DEVKIT OR BLUEPRINT EVIDENCE."
)

StatusToolResult: TypeAlias = Annotated[CallToolResult, StatusToolOutput]
EditorToolResult: TypeAlias = Annotated[CallToolResult, EditorToolOutput]
AssetListToolResult: TypeAlias = Annotated[CallToolResult, AssetListToolOutput]
ContextToolResult: TypeAlias = Annotated[CallToolResult, ContextToolOutput]
NodeToolResult: TypeAlias = Annotated[CallToolResult, NodeToolOutput]
TaskCreateToolResult: TypeAlias = Annotated[CallToolResult, TaskCreateToolOutput]
TaskResumeToolResult: TypeAlias = Annotated[CallToolResult, TaskResumeToolOutput]
TaskResearchToolResult: TypeAlias = Annotated[CallToolResult, TaskResearchToolOutput]
PatchPlanDraftToolResult: TypeAlias = Annotated[
    CallToolResult, PatchPlanDraftToolOutput
]
PatchPlanValidateToolResult: TypeAlias = Annotated[
    CallToolResult, PatchPlanValidateToolOutput
]
PatchPlanConfirmToolResult: TypeAlias = Annotated[
    CallToolResult, PatchPlanConfirmToolOutput
]


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "UNKNOWN"


def _project_version() -> str:
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "UNKNOWN"


def _success(payload: dict[str, object], summary: str) -> CallToolResult:
    assert_path_free(payload)
    return CallToolResult(
        content=[TextContent(type="text", text=summary)],
        structured_content=payload,
        is_error=False,
    )


def _failure(error: McpExecutionError) -> CallToolResult:
    payload = error.as_payload()
    return CallToolResult(
        content=[TextContent(type="text", text=f"{error.code}: {error.message}")],
        structured_content=payload,
        is_error=True,
    )


def _invoke(
    operation: Callable[[], dict[str, object]],
    summary: Callable[[dict[str, object]], str],
) -> CallToolResult:
    try:
        payload = operation()
        return _success(payload, summary(payload))
    except McpExecutionError as exc:
        return _failure(exc)
    except Exception:
        return _failure(
            McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "The ARK Dev MCP tool could not satisfy its public contract.",
            )
        )


def _read_only_annotations() -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


def _metadata_write_annotations() -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    )


def create_server(
    capture_root: str | Path,
    *,
    editor_bridge: EditorBridge | None = None,
    task_root: str | Path | None = None,
) -> MCPServer:
    """Build a stdio MCP server whose only writes are local Task metadata."""

    blueprint = BlueprintService(capture_root)
    bridge = (
        editor_bridge
        if editor_bridge is not None
        else FileEditorBridge.for_project_root(Path(capture_root).resolve().parent)
    )
    configured_task_root = task_root or os.environ.get("ARKDEV_MCP_TASK_ROOT")
    resolved_task_root = (
        Path(configured_task_root)
        if configured_task_root is not None
        else Path(capture_root).resolve().parent / ".blueprint-tasks"
    )
    store = TaskStore(resolved_task_root)
    tasks = TaskService(blueprint, store)
    research = ResearchService(blueprint, tasks, store)
    plans = PlanService(blueprint, tasks, store)
    editor_binding = EditorBindingService(blueprint, tasks)
    server = MCPServer(
        name="arkdev-blueprint",
        title="ARK Dev Blueprint Task Planning MCP",
        description=(
            "Read-only ARK/Evidence access plus local Task and Patch Plan metadata."
        ),
        instructions=(
            "Five tools read bounded ARK/Evidence data; six tools write only local "
            "Task metadata. Never modify ARK DevKit. Never confirm a Patch Plan "
            "until the user explicitly approves the displayed plan in the current "
            "conversation. Phase 2 has no execution tool."
        ),
        version=SERVER_VERSION,
        log_level="ERROR",
    )

    def editor_state_payload(
        *,
        include_selection: bool,
        include_graph_nodes: bool = False,
        max_graph_nodes: int = 200,
        task_id: str = "",
    ) -> dict[str, object]:
        try:
            state = dict(
                bridge.get_state(
                    include_selection=include_selection,
                    include_graph_nodes=include_graph_nodes,
                    max_graph_nodes=max_graph_nodes,
                )
            )
            capabilities = {
                str(item) for item in state.get("capabilities", [])
            }
            if capabilities & MUTATION_CAPABILITIES:
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "The editor bridge advertised a forbidden mutation capability.",
                )
            state["capabilities"] = sorted(capabilities)
            state = editor_binding.enrich(state, task_id=task_id)
            assert_path_free(state)
            return state
        except McpExecutionError:
            raise
        except Exception as exc:
            raise McpExecutionError(
                "EDITOR_BRIDGE_UNAVAILABLE",
                "The editor bridge is unavailable.",
                retryable=True,
            ) from exc

    def status_payload() -> dict[str, object]:
        try:
            health = dict(bridge.health())
        except Exception:
            health = {
                "connected": False,
                "status": "UNAVAILABLE",
                "reasonCode": "EDITOR_BRIDGE_UNAVAILABLE",
            }
        payload: dict[str, object] = {
            "schema": STATUS_SCHEMA,
            "serverVersion": SERVER_VERSION,
            "mcpSdkVersion": _package_version("mcp"),
            "projectVersion": _project_version(),
            "platform": "windows-x64",
            "transport": "stdio",
            "readOnly": True,
            "taskMetadataWrite": True,
            "capabilities": {
                "blueprintEvidence": True,
                "blueprintInterpretation": True,
                "boundedGraphContext": True,
                "editorBridge": bool(health.get("connected")),
                "taskContext": True,
                "patchPlan": True,
                "localTaskMetadataWrite": True,
                "mutation": False,
            },
            "editorBridge": {
                "status": str(health.get("status") or "DISCONNECTED"),
                "stateStatus": str(
                    health.get("stateStatus")
                    or ("CONNECTED" if health.get("connected") else "DISCONNECTED")
                ),
                "reasonCode": str(
                    health.get("reasonCode")
                    or (
                        ""
                        if health.get("connected")
                        else "EDITOR_BRIDGE_NOT_INSTALLED"
                    )
                ),
            },
        }
        assert_path_free(payload)
        return payload

    @server.tool(
        name="arkdev_status",
        description=f"{READ_ONLY_DESCRIPTION} Report deterministic server capabilities.",
        annotations=_read_only_annotations(),
        structured_output=True,
    )
    def arkdev_status() -> StatusToolResult:
        return _invoke(
            status_payload,
            lambda payload: (
                "ARK Dev MCP is ready in read-only stdio mode; editor bridge is "
                f"{payload['editorBridge']['status']}."  # type: ignore[index]
            ),
        )

    @server.tool(
        name="arkdev_editor_state",
        description=f"{READ_ONLY_DESCRIPTION} Read editor bridge state if connected.",
        annotations=_read_only_annotations(),
        structured_output=True,
    )
    def arkdev_editor_state(  # noqa: N803
        includeSelection: bool = True,
        includeGraphNodes: bool = False,
        maxGraphNodes: Annotated[int, Field(ge=1, le=1000)] = 200,
        taskId: Annotated[
            str,
            Field(
                max_length=39,
                pattern=r"^(?:|task://[0-9a-f]{32})$",
            ),
        ] = "",
    ) -> EditorToolResult:
        return _invoke(
            lambda: editor_state_payload(
                include_selection=includeSelection,
                include_graph_nodes=includeGraphNodes,
                max_graph_nodes=maxGraphNodes,
                task_id=taskId,
            ),
            lambda payload: (
                "Editor bridge state: "
                + ("CONNECTED" if payload.get("connected") else "DISCONNECTED")
                + "."
            ),
        )

    @server.tool(
        name="blueprint_list_assets",
        description=f"{READ_ONLY_DESCRIPTION} List public Blueprint asset identities.",
        annotations=_read_only_annotations(),
        structured_output=True,
    )
    def blueprint_list_assets(
        query: Annotated[str, Field(max_length=128)] = "",
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
        cursor: Annotated[str, Field(max_length=4096)] = "",
    ) -> AssetListToolResult:
        return _invoke(
            lambda: blueprint.list_assets(query=query, limit=limit, cursor=cursor),
            lambda payload: f"Returned {len(payload.get('items', []))} Blueprint assets.",
        )

    @server.tool(
        name="blueprint_get_context",
        description=(
            f"{READ_ONLY_DESCRIPTION} Return bounded, revision-bound Blueprint context."
        ),
        annotations=_read_only_annotations(),
        structured_output=True,
    )
    def blueprint_get_context(  # noqa: N803
        asset: Annotated[str, Field(min_length=1, max_length=256)],
        goal: Annotated[str, Field(min_length=1, max_length=1000)],
        graphRef: Annotated[str, Field(max_length=4096)] = "",
        seedRefs: Annotated[tuple[str, ...], Field(max_length=10)] = (),
        maxHops: Annotated[int, Field(ge=0, le=2)] = 1,
        maxNodes: Annotated[int, Field(ge=1, le=100)] = 40,
        maxPins: Annotated[int, Field(ge=1, le=400)] = 160,
        maxEdges: Annotated[int, Field(ge=1, le=400)] = 160,
        budgetTokens: Annotated[int, Field(ge=800, le=6000)] = 2400,
        continuation: Annotated[str, Field(max_length=4096)] = "",
    ) -> ContextToolResult:
        return _invoke(
            lambda: blueprint.get_context(
                asset=asset,
                goal=goal,
                graph_ref=graphRef,
                seed_refs=seedRefs,
                max_hops=maxHops,
                max_nodes=maxNodes,
                max_pins=maxPins,
                max_edges=maxEdges,
                budget_tokens=budgetTokens,
                continuation=continuation,
            ),
            lambda payload: (
                f"Returned {len(payload.get('nodes', []))} nodes from "
                f"{len(payload.get('graphTargets', []))} graph targets."
            ),
        )

    @server.tool(
        name="blueprint_get_node",
        description=f"{READ_ONLY_DESCRIPTION} Inspect one exact Evidence node reference.",
        annotations=_read_only_annotations(),
        structured_output=True,
    )
    def blueprint_get_node(  # noqa: N803
        asset: Annotated[str, Field(min_length=1, max_length=256)],
        nodeRef: Annotated[str, Field(min_length=1, max_length=4096)],
        includeNeighborhood: bool = True,
        maxHops: Annotated[int, Field(ge=0, le=1)] = 1,
    ) -> NodeToolResult:
        return _invoke(
            lambda: blueprint.get_node(
                asset=asset,
                node_ref=nodeRef,
                include_neighborhood=includeNeighborhood,
                max_hops=maxHops,
            ),
            lambda payload: (
                f"Returned exact node with {len(payload.get('pins', []))} pins."
            ),
        )

    @server.tool(
        name="blueprint_task_create",
        description=(
            f"{METADATA_WRITE_DESCRIPTION} Create one revision-bound Task handle."
        ),
        annotations=_metadata_write_annotations(),
        structured_output=True,
    )
    def blueprint_task_create(  # noqa: N803
        mode: Literal["KNOWLEDGE_QUERY", "BLUEPRINT_DESIGN", "IMPLEMENTATION_PREP"],
        asset: Annotated[str, Field(min_length=1, max_length=256)],
        goal: Annotated[str, Field(min_length=1, max_length=1000)],
        completionCriteria: Annotated[
            tuple[str, ...], Field(min_length=1, max_length=12)
        ],
        allowedChanges: Annotated[tuple[str, ...], Field(max_length=20)] = (),
        forbiddenChanges: Annotated[tuple[str, ...], Field(max_length=20)] = (),
        graphRef: Annotated[str, Field(max_length=4096)] = "",
        supportingAssets: Annotated[tuple[str, ...], Field(max_length=3)] = (),
    ) -> TaskCreateToolResult:
        return _invoke(
            lambda: tasks.create(
                mode=mode,
                asset=asset,
                goal=goal,
                completion_criteria=completionCriteria,
                allowed_changes=allowedChanges,
                forbidden_changes=forbiddenChanges,
                graph_ref=graphRef,
                supporting_assets=supportingAssets,
            ),
            lambda payload: (
                f"Created {payload['taskId']} in {payload['readiness']} readiness."
            ),
        )

    @server.tool(
        name="blueprint_task_resume",
        description=(
            f"{METADATA_WRITE_DESCRIPTION} Resume one compact, revision-verified Task state."
        ),
        annotations=_metadata_write_annotations(),
        structured_output=True,
    )
    def blueprint_task_resume(  # noqa: N803
        taskId: Annotated[str, Field(min_length=39, max_length=39)],
    ) -> TaskResumeToolResult:
        return _invoke(
            lambda: tasks.resume(taskId),
            lambda payload: f"Resumed Task in {payload['phase']} phase.",
        )

    @server.tool(
        name="blueprint_task_research",
        description=(
            f"{METADATA_WRITE_DESCRIPTION} Store or reuse one bounded Graph Slice."
        ),
        annotations=_metadata_write_annotations(),
        structured_output=True,
    )
    def blueprint_task_research(  # noqa: N803
        taskId: Annotated[str, Field(min_length=39, max_length=39)],
        question: Annotated[str, Field(min_length=1, max_length=1000)],
        graphRef: Annotated[str, Field(max_length=4096)] = "",
        seedRefs: Annotated[tuple[str, ...], Field(max_length=10)] = (),
        maxHops: Annotated[int, Field(ge=0, le=2)] = 1,
        maxNodes: Annotated[int, Field(ge=1, le=100)] = 40,
        maxPins: Annotated[int, Field(ge=1, le=400)] = 160,
        maxEdges: Annotated[int, Field(ge=1, le=400)] = 160,
        budgetTokens: Annotated[int, Field(ge=800, le=6000)] = 2400,
        taskUpdate: dict[str, object] | None = None,
    ) -> TaskResearchToolResult:
        return _invoke(
            lambda: research.research(
                task_id=taskId,
                question=question,
                graph_ref=graphRef,
                seed_refs=seedRefs,
                max_hops=maxHops,
                max_nodes=maxNodes,
                max_pins=maxPins,
                max_edges=maxEdges,
                budget_tokens=budgetTokens,
                task_update=taskUpdate,
            ),
            lambda payload: (
                f"Returned Graph Slice with {len(payload.get('nodes', []))} nodes; "
                f"cached={str(payload.get('cached')).lower()}."
            ),
        )

    @server.tool(
        name="blueprint_patch_plan_draft",
        description=(
            f"{METADATA_WRITE_DESCRIPTION} Draft an exact Node/Pin Patch Plan; do not execute it."
        ),
        annotations=_metadata_write_annotations(),
        structured_output=True,
    )
    def blueprint_patch_plan_draft(  # noqa: N803
        taskId: Annotated[str, Field(min_length=39, max_length=39)],
        nodes: Annotated[tuple[dict[str, object], ...], Field(max_length=64)],
        operations: Annotated[
            tuple[dict[str, object], ...], Field(max_length=128)
        ],
        capabilityRequirements: Annotated[
            tuple[str, ...], Field(max_length=20)
        ] = (),
        checkpoints: Annotated[
            tuple[dict[str, object], ...], Field(max_length=32)
        ] = (),
        blockingQuestions: Annotated[tuple[str, ...], Field(max_length=20)] = (),
    ) -> PatchPlanDraftToolResult:
        return _invoke(
            lambda: plans.draft(
                task_id=taskId,
                nodes=nodes,
                operations=operations,
                capability_requirements=capabilityRequirements,
                checkpoints=checkpoints,
                blocking_questions=blockingQuestions,
            ),
            lambda payload: f"Saved DRAFT Patch Plan {payload['planId']}.",
        )

    @server.tool(
        name="blueprint_patch_plan_validate",
        description=(
            f"{METADATA_WRITE_DESCRIPTION} Revalidate exact refs, DAG, blockers, and capabilities."
        ),
        annotations=_metadata_write_annotations(),
        structured_output=True,
    )
    def blueprint_patch_plan_validate(  # noqa: N803
        taskId: Annotated[str, Field(min_length=39, max_length=39)],
        planId: Annotated[str, Field(min_length=45, max_length=45)],
    ) -> PatchPlanValidateToolResult:
        return _invoke(
            lambda: plans.validate(taskId, planId),
            lambda payload: (
                f"Patch Plan valid={str(payload['valid']).lower()}, "
                f"confirmable={str(payload['confirmable']).lower()}."
            ),
        )

    @server.tool(
        name="blueprint_patch_plan_confirm",
        description=(
            "CONFIRMS LOCAL PLAN METADATA ONLY. DOES NOT MODIFY ARK DEVKIT. "
            f"{METADATA_WRITE_DESCRIPTION} Requires explicit approval and exact digest."
        ),
        annotations=_metadata_write_annotations(),
        structured_output=True,
    )
    def blueprint_patch_plan_confirm(  # noqa: N803
        taskId: Annotated[str, Field(min_length=39, max_length=39)],
        planId: Annotated[str, Field(min_length=45, max_length=45)],
        expectedSemanticDigest: Annotated[
            str, Field(min_length=64, max_length=64)
        ],
        confirm: bool,
    ) -> PatchPlanConfirmToolResult:
        return _invoke(
            lambda: plans.confirm(
                task_id=taskId,
                plan_id=planId,
                expected_semantic_digest=expectedSemanticDigest,
                confirm=confirm,
            ),
            lambda payload: f"Confirmed local Patch Plan {payload['planId']} only.",
        )

    def safe_resource(operation: Callable[[], dict[str, object]]) -> dict[str, object]:
        try:
            payload = operation()
            assert_path_free(payload)
            return payload
        except McpExecutionError as exc:
            return exc.as_payload()
        except Exception:
            return McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "The MCP resource could not satisfy its public contract.",
            ).as_payload()

    def plan_resource(plan_opaque_id: str) -> dict[str, object]:
        plan_id = f"patch-plan://{plan_opaque_id}"
        task_id, _plan = store.find_plan(plan_id)
        return plans.validate(
            task_id,
            plan_id,
            persist_verification=False,
        )

    register_resources(
        server,
        status_provider=lambda: safe_resource(status_payload),
        editor_state_provider=lambda: safe_resource(
            lambda: editor_state_payload(
                include_selection=True,
                include_graph_nodes=False,
                max_graph_nodes=200,
                task_id="",
            )
        ),
        asset_health_provider=lambda asset: safe_resource(
            lambda: blueprint.health(asset=asset)
        ),
        task_provider=lambda task_id: safe_resource(
            lambda: tasks.resume(
                f"task://{task_id}",
                persist_verification=False,
            )
        ),
        plan_provider=lambda plan_id: safe_resource(
            lambda: plan_resource(plan_id)
        ),
    )
    register_prompts(server)
    return server


__all__ = ["SERVER_VERSION", "create_server"]
