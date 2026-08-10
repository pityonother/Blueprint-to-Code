"""Small read-only MCP resource projections."""

from __future__ import annotations

import json
from collections.abc import Callable

from mcp.server import MCPServer


PayloadProvider = Callable[[], dict[str, object]]
AssetPayloadProvider = Callable[[str], dict[str, object]]


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


__all__ = ["register_resources"]
