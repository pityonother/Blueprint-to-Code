"""User-selected read-only analysis prompts for Codex."""

from __future__ import annotations

from mcp.server import MCPServer

from .contracts import assert_path_free


def register_prompts(server: MCPServer) -> None:
    @server.prompt(
        name="analyze_blueprint_task",
        description="Analyze one Blueprint task through the bounded read-only tools.",
    )
    def analyze_blueprint_task(asset: str, goal: str) -> str:
        assert_path_free({"asset": asset})
        return f"""分析资产 {asset} 的目标：{goal}

严格按以下顺序执行：
1. 调用 arkdev_status。
2. 调用 blueprint_get_context。
3. 如确有必要，最多 5 次 blueprint_get_node。
4. 仅输出：已确认事实、假设、阻断未知、非阻断未知、下一步建议。
5. 然后停止。

不得使用 shell、Computer Use 或文件读取；不得修改 ARK DevKit；不得生成补丁方案。"""

    @server.prompt(
        name="inspect_blueprint_node",
        description="Inspect one exact Blueprint node and its direct read-only neighborhood.",
    )
    def inspect_blueprint_node(asset: str, nodeRef: str) -> str:  # noqa: N803
        assert_path_free({"asset": asset, "nodeRef": nodeRef})
        return f"""只读检查资产 {asset} 中的 exact nodeRef：{nodeRef}

仅调用 blueprint_get_node，并只分析该节点、Pin、默认值、gap 与直接邻域。
明确区分已确认事实和未知；不得模糊搜索，不得使用 shell 或 Computer Use，不得修改 ARK DevKit。"""


__all__ = ["register_prompts"]
