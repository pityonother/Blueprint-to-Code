# Codex Blueprint Task Workflow

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

- `blueprint_get_context` 保持一发式只读工具；Task research 只是复用同一领域函数并保存 bounded slice。
- 不允许 caller 直接注入 confirmedFacts。
- validate 之后必须展示目标、revision、graphs、node/default/connect changes、capabilities、blockers 与 status。
- Never call `blueprint_patch_plan_confirm` until the user explicitly approves the displayed plan in the current conversation.
- confirm 后停止；Phase 2 没有 execution tool。
- 不使用 shell、Computer Use、截图/OCR、Editor Bridge 猜测或 ARK mutation。

## Approval 边界

Codex config 的 `auto` 只覆盖五个 Evidence reads 与六个 local task metadata tools。它不代表用户批准未来的 ARK Editor mutation。Phase 3 只读 Editor Bridge 和更后的 Patch Executor 必须分别设计权限合同。
