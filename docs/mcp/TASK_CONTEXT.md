# Task Context 与 Session v1

Phase 2 用 opaque `task://<32-hex>` 取代长对话中的隐式任务状态。每个 Task 恰好一个可修改 primary asset；最多三个 supporting assets，且全部只读。

## 本地存储

```text
.blueprint-tasks/<opaque-id>/
  context.json
  session.json
  slices/
  plans/
```

JSON 采用 sibling temp file、flush、`os.replace` 的轻量原子写入。MCP payload 只返回 handle，不返回 filesystem path。`.blueprint-tasks/` 被 gitignore。

## Revision gate

create 从当前 authoritative Evidence 读取 `assetId`、Unreal `objectPath`、revision、manifest 与 freshness；调用者不能覆盖。每个 Task/Plan tool 调用前重新读取 current authority：

- identity 与 Task 完全相同且 `FRESH`：继续；
- 任一 identity/freshness 改变：写入 `BLOCKED/EVIDENCE_REVISION_CHANGED` 并停止；
- 不自动 refresh、不复用旧 cache、不 remap 旧 NodeRef。

## Research cache

Graph Slice signature 绑定 Evidence revision/manifest、规范化 question、graph/seed refs、hop/node/pin/edge/token budgets。同 Task 同 signature 的第二次调用直接读取 stored slice，并增加 `cacheHits`，不会再次调用 `BlueprintService.get_context()`。

每 Task 最多八个不同 slices；第九个返回 `TASK_SLICE_LIMIT_REACHED`，不静默淘汰旧 slice。`taskUpdate` 只能维护 assumptions/questions/unknowns；confirmed facts 只能由带 exact Evidence refs 的 `CONFIRMED` Evidence fact 产生。

## PLAN_READINESS

只有以下条件全部成立才为 `READY_TO_PLAN`：FRESH primary、至少一个 target graph、无 blocking questions、至少一个 Graph Slice、completion criteria 非空。研究次数不是 readiness 条件。
