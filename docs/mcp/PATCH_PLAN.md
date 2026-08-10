# Blueprint Patch Plan v1

Patch Plan 是 revision-bound 本地机器计划，不是 ARK DevKit mutation 命令。Target identity 总是从 Task 固定，调用者不能覆盖。

## Exact identity

- Existing node：exact `nodeRef` + `graphRef` + complete `NodeSignature`。
- Proposed node：`localPlanNodeId` + `graphRef` + complete `NodeSignature`。
- Existing Pin endpoint：exact `pinRef`，并验证它属于 endpoint 的 exact nodeRef。
- Proposed Pin endpoint：name + direction + ordinal 必须唯一匹配 proposed node 的 `PinSignature`。

CONNECT/DISCONNECT 必须明确 OUTPUT→INPUT。禁止只写“Branch 接 Set”或“Return Value 接过去”。
DISCONNECT 还必须证明 exact source/target Pin 当前确有 Evidence edge；DELETE 的 preconditions 必须包含 current exact nodeRef。Existing SET_DEFAULT 必须包含 exact nodeRef/pinRef/oldValue precondition 与明确的 `valueEncoding`。

## Operations 与 limits

允许 `CREATE_NODE`、`DELETE_NODE`、`CONNECT`、`DISCONNECT`、`SET_DEFAULT`、`MOVE_NODE`、`ADD_COMMENT`、`PRESERVE`。每项含 operation ID、graph、dependsOn、pre/postconditions、payload 与 checkpoint。

硬上限为 64 nodes、128 operations、32 checkpoints；dependsOn 必须为 DAG。Validator 会推导 operation 所需 capability，并要求计划显式声明。

## Confirm

DRAFT 可以带 blockers，但不可 confirm。confirm 必须同时满足：

1. current Evidence 与 Task identity 完全一致；
2. plan 仍为当前 DRAFT；
3. validate `valid=true` 且 `confirmable=true`；
4. `expectedSemanticDigest` exact；
5. `confirm=true`，且调用前用户已在当前对话明确批准展示的计划。

CONFIRMED 只改变本地 plan/session metadata。返回始终包含 `executionReady=false`、`reason=EDITOR_BRIDGE_NOT_INSTALLED`；不执行节点创建、Pin 连线、default、compile 或 save。

## Markdown

```powershell
python scripts/render_blueprint_patch_plan.py --task <task-id> --plan <plan-id>
```

该命令把 stored plan 输出为 Markdown，包含 exact Pin endpoints；它不是 SVG、截图或蓝图施工图。
