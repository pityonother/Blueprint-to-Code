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
        assert_path_free({"asset": asset, "goal": goal})
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

    @server.prompt(
        name="design_blueprint_patch",
        description="Build a revision-bound local Blueprint Patch Plan and stop before execution.",
    )
    def design_blueprint_patch(asset: str, goal: str) -> str:
        assert_path_free({"asset": asset, "goal": goal})
        return f"""为资产 {asset} 的目标设计精确 Blueprint Patch Plan：{goal}

严格按以下顺序执行：
1. 调用 blueprint_task_create。
2. 调用 blueprint_task_research；若存在 blockers，停止并询问用户。
3. 只有 Task 到达 READY_TO_PLAN 后才调用 blueprint_patch_plan_draft。
4. 调用 blueprint_patch_plan_validate。
5. 展示 humanSummary，并等待当前对话中的用户明确批准。
6. Never call blueprint_patch_plan_confirm until the user explicitly approves the displayed plan in the current conversation.
7. 获得明确批准后才以 exact semantic digest 调用 confirm，然后停止。

不得使用 shell 或 Computer Use；不得修改 Evidence；不得执行蓝图、编译或保存；CONFIRMED 仍不是 ARK mutation 授权。"""

    @server.prompt(
        name="solve_ark_blueprint_requirement",
        description="Compile a bounded ARK Blueprint requirement and orchestrate Evidence readiness.",
    )
    def solve_ark_blueprint_requirement(
        rawRequest: str,  # noqa: N803
        language: str = "zh-CN",
    ) -> str:
        assert_path_free({"rawRequest": rawRequest, "language": language})
        return f"""处理以下 ARK Blueprint requirement（language={language}）：
{rawRequest}

The model is the semantic front-end.
BTC is the deterministic compiler and orchestrator.
Never invent an Evidence-ready state.

严格按以下十步执行：
1. 将用户原文拆成最多 8 个 bounded subproblems。
2. 为每个 subproblem 保留与原文完全对应的 source spans。
3. 提交 typed Proposal 并调用 blueprint_solver_create。
4. 调用 blueprint_solver_preflight。
5. 有 target 或语义歧义时，只询问一个最小问题，然后停止。
6. Evidence 缺失时展示 acquisition actions，不执行 acquisition writes。
7. 条件满足时调用 blueprint_solver_materialize_task 创建 existing Task。
8. 不直接编造最终答案，也不得伪造 confirmed facts。
9. 不得自动进入 Patch Plan confirm。
10. 返回 solverId、当前状态和下一步后停止。

不得使用 shell、网页搜索或 Computer Use；不得修改 Evidence 或 ARK DevKit。"""


__all__ = ["register_prompts"]
