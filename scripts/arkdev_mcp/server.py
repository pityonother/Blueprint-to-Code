"""Official MCP Python SDK v2 composition root for the read-only server."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, TypeAlias

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from .blueprint_service import BlueprintService
from .contracts import McpExecutionError, assert_path_free
from .editor_bridge import (
    MUTATION_CAPABILITIES,
    DisconnectedEditorBridge,
    EditorBridge,
)
from .prompts import register_prompts
from .resources import register_resources
from .schemas import (
    AssetListToolOutput,
    ContextToolOutput,
    EditorToolOutput,
    NodeToolOutput,
    StatusToolOutput,
)


SERVER_VERSION = "0.1.0"
STATUS_SCHEMA = "blueprint-to-code.arkdev-mcp-status/v1"
READ_ONLY_DESCRIPTION = "READ-ONLY. NO ARK DEVKIT MUTATION."

StatusToolResult: TypeAlias = Annotated[CallToolResult, StatusToolOutput]
EditorToolResult: TypeAlias = Annotated[CallToolResult, EditorToolOutput]
AssetListToolResult: TypeAlias = Annotated[CallToolResult, AssetListToolOutput]
ContextToolResult: TypeAlias = Annotated[CallToolResult, ContextToolOutput]
NodeToolResult: TypeAlias = Annotated[CallToolResult, NodeToolOutput]


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
                "The read-only MCP tool could not satisfy its public contract.",
            )
        )


def _read_only_annotations() -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


def create_server(
    capture_root: str | Path,
    *,
    editor_bridge: EditorBridge | None = None,
) -> MCPServer:
    """Build a fresh in-memory-compatible MCP server without side effects."""

    blueprint = BlueprintService(capture_root)
    bridge = editor_bridge or DisconnectedEditorBridge()
    server = MCPServer(
        name="arkdev-blueprint",
        title="ARK Dev Blueprint Read-Only MCP",
        description=READ_ONLY_DESCRIPTION,
        instructions=(
            "Use only the five bounded read-only tools. "
            "Do not mutate ARK DevKit or claim editor connectivity."
        ),
        version=SERVER_VERSION,
        log_level="ERROR",
    )

    def editor_state_payload(*, include_selection: bool) -> dict[str, object]:
        try:
            capabilities = set(bridge.get_capabilities())
            if capabilities & MUTATION_CAPABILITIES:
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "The editor bridge advertised a forbidden mutation capability.",
                )
            state = dict(bridge.get_state(include_selection=include_selection))
            state["capabilities"] = sorted(capabilities)
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
            "capabilities": {
                "blueprintEvidence": True,
                "blueprintInterpretation": True,
                "boundedGraphContext": True,
                "editorBridge": bool(health.get("connected")),
                "patchPlan": False,
                "mutation": False,
            },
            "editorBridge": {
                "status": str(health.get("status") or "DISCONNECTED"),
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
    ) -> EditorToolResult:
        return _invoke(
            lambda: editor_state_payload(include_selection=includeSelection),
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
                "The read-only resource could not satisfy its public contract.",
            ).as_payload()

    register_resources(
        server,
        status_provider=lambda: safe_resource(status_payload),
        editor_state_provider=lambda: safe_resource(
            lambda: editor_state_payload(include_selection=True)
        ),
        asset_health_provider=lambda asset: safe_resource(
            lambda: blueprint.health(asset=asset)
        ),
    )
    register_prompts(server)
    return server


__all__ = ["SERVER_VERSION", "create_server"]
