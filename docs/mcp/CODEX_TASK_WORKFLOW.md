# Codex Blueprint Task Workflow

Solver v1 在现有 Task Workflow 前增加确定性需求与 Evidence 编排：

```text
solver_create
  -> solver_preflight
  -> ambiguous? ask one minimal question and solver_update
  -> acquisition required? show actions and stop
  -> ready? solver_materialize_task
  -> existing task workflow
```

Solver 只创建已有 Task Context；不会跳过 research、不会注入 confirmed facts、不会自动 draft 或 confirm Patch Plan。

```text
task_create
  -> task_research
  -> blockers? stop and ask the user
  -> READY_TO_PLAN
  -> patch_plan_draft
  -> patch_plan_validate
  -> display humanSummary
  -> wait for explicit user approval in the current conversation
  -> patch_plan_confirm
  -> stop
```

## 不可跳过的规则

- `blueprint_get_context` 保持一发式只读工具；精确属性名可返回带当前 `bp://` 引用的 `CLASS_DEFAULT`，未完整返回或被路径策略隐藏的值不得晋升为 confirmed fact；Task research 只是复用同一领域函数并保存 bounded slice。
- 纯默认值查询可以完成只读事实核对，但没有 Graph target 时 Task 仍保持 `DISCOVERY`；不能把一个属性值直接当作可施工的 Graph/Patch Plan 范围。
- 不允许 caller 直接注入 confirmedFacts。
- validate 之后必须展示目标、revision、graphs、node/default/connect changes、capabilities、blockers 与 status。
- Never call `blueprint_patch_plan_confirm` until the user explicitly approves the displayed plan in the current conversation.
- confirm 后停止；Phase 2 没有 execution tool。
- 不使用 shell、Computer Use、截图/OCR、Editor Bridge 猜测或 ARK mutation。

## Approval 边界

Codex config 的 `auto` 只覆盖五个 Evidence reads、六个 local task metadata tools 与五个 local Solver metadata tools。它不代表用户批准 Evidence acquisition、ARK Editor mutation 或未来 Patch Executor。Phase 3 只读 Editor Bridge 和更后的 Patch Executor 必须分别设计权限合同。
