"""Small read-only MCP resource projections."""

from __future__ import annotations

import json
from collections.abc import Callable

from mcp.server import MCPServer


PayloadProvider = Callable[[], dict[str, object]]
AssetPayloadProvider = Callable[[str], dict[str, object]]
TaskPayloadProvider = Callable[[str], dict[str, object]]
PlanPayloadProvider = Callable[[str], dict[str, object]]
SolverPayloadProvider = Callable[[str], dict[str, object]]


def _json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def register_resources(
    server: MCPServer,
    *,
    status_provider: PayloadProvider,
    editor_state_provider: PayloadProvider,
    asset_health_provider: AssetPayloadProvider,
    task_provider: TaskPayloadProvider,
    plan_provider: PlanPayloadProvider,
    solver_provider: SolverPayloadProvider,
) -> None:
    @server.resource(
        "arkdev://status",
        name="arkdev_status",
        description="READ-ONLY lightweight projection of ARK Dev MCP status.",
        mime_type="application/json",
    )
    def arkdev_status_resource() -> str:
        return _json(status_provider())

    @server.resource(
        "arkdev://editor/state",
        name="arkdev_editor_state",
        description="READ-ONLY lightweight projection of editor bridge state.",
        mime_type="application/json",
    )
    def arkdev_editor_state_resource() -> str:
        return _json(editor_state_provider())

    @server.resource(
        "blueprint://assets/{asset}/health",
        name="blueprint_asset_health",
        description="READ-ONLY public Evidence health projection for one asset.",
        mime_type="application/json",
    )
    def blueprint_asset_health_resource(asset: str) -> str:
        return _json(asset_health_provider(asset))

    @server.resource(
        "arkdev://tasks/{task_id}",
        name="blueprint_task_state",
        description="Compact revision-verified projection of local Task metadata.",
        mime_type="application/json",
    )
    def blueprint_task_state_resource(task_id: str) -> str:
        return _json(task_provider(task_id))

    @server.resource(
        "arkdev://plans/{plan_id}",
        name="blueprint_patch_plan_state",
        description="Compact validated projection of local Patch Plan metadata.",
        mime_type="application/json",
    )
    def blueprint_patch_plan_state_resource(plan_id: str) -> str:
        return _json(plan_provider(plan_id))

    @server.resource(
        "arkdev://solvers/{solver_id}",
        name="blueprint_solver_state",
        description="READ-ONLY compact projection of local Solver metadata.",
        mime_type="application/json",
    )
    def blueprint_solver_state_resource(solver_id: str) -> str:
        return _json(solver_provider(solver_id))


__all__ = ["register_resources"]
