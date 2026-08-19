# Phase 2 MCP Tool Contracts

五个 Phase 1 工具保持名称、参数和只读行为兼容。六个 Phase 2 工具只写 `.blueprint-tasks/**`，annotation 固定为 `readOnly=false`、`destructive=false`、`openWorld=false`，description 明确写出：

```text
WRITES LOCAL TASK METADATA ONLY.
DOES NOT MODIFY ARK DEVKIT OR BLUEPRINT EVIDENCE.
```

## Tools（恰好 11 个）

| Tool | 写入 | 成功合同 | 关键约束 |
|---|---|---|---|
| `arkdev_status` | 无 | MCP status v1 | stdio/windows-x64；ARK mutation=false |
| `arkdev_editor_state` | 无 | editor state v1 | 默认 DISCONNECTED |
| `blueprint_list_assets` | 无 | asset list v1 | bounded/path-free |
| `blueprint_get_context` | 无 | context v1 | 精确属性名可直接返回 `CLASS_DEFAULT`；图查询 maxHops<=2 |
| `blueprint_get_node` | 无 | node v1 | exact current `bp://` nodeRef |
| `blueprint_task_create` | Task metadata | Task Context v1 | authoritative FRESH identity；opaque handle；不自动 research |
| `blueprint_task_resume` | verification timestamp / BLOCKED state | compact resume v1 | 默认小于 1600 estimated tokens；不返回 raw slices/operations |
| `blueprint_task_research` | Task metadata + Graph Slice | Graph Slice v1 | 同 revision/signature cache；最多 8 slices；不接受 caller confirmedFacts |
| `blueprint_patch_plan_draft` | Plan metadata | Patch Plan v1 DRAFT | exact refs、DAG、caps、limits；blockers 可保存 |
| `blueprint_patch_plan_validate` | verification metadata | validation v1 | 每次重验 Evidence/Node/Pin ownership/graph scope/DAG/blockers/capabilities |
| `blueprint_patch_plan_confirm` | Plan status metadata | confirmation v1 | 仅 explicit `confirm=true` + exact digest + confirmable DRAFT |

## Error 合同

所有业务失败继续使用 `blueprint-to-code.arkdev-mcp-error/v1`、`isError=true`，不得返回 stack trace、raw exception、本机路径或秘密。Phase 2 新增：

```text
TASK_NOT_FOUND
TASK_PHASE_INVALID
TASK_BLOCKED
TASK_SLICE_LIMIT_REACHED
EVIDENCE_REVISION_CHANGED
PATCH_PLAN_NOT_FOUND
PATCH_PLAN_INVALID
PATCH_PLAN_LIMIT_EXCEEDED
PATCH_PLAN_NOT_CONFIRMABLE
PATCH_PLAN_DIGEST_MISMATCH
PLAN_CONFIRMATION_REQUIRED
```

Evidence identity 改变时，Task 写入 `phase=BLOCKED` 与 `reasonCode=EVIDENCE_REVISION_CHANGED` 后 fail closed；绝不自动把旧 refs 映射到新 revision。

`CLASS_DEFAULT` 只把完整返回且 `valueUsable=true` 的值晋升为 confirmed fact。对象数组会同时返回 bounded 的 `resolvedObjectPaths`/`resolvedObjectNames`、`resolvedObjectCoverage` 与 `resolvedObjectIdentityComplete`；后者为 false 时只能使用已返回的对象身份，不能声称整个数组引用已完整恢复。`sourceValueStatus` 缺失、值被路径策略隐藏、压缩值只返回片段时一律 fail closed。

## Resources（最多五个）

```text
arkdev://status
arkdev://editor/state
blueprint://assets/{asset}/health
arkdev://tasks/{task_id}
arkdev://plans/{plan_id}
```

Task/Plan Resource 参数只接受 opaque ID；投影会重新执行 revision gate，不暴露本机路径。

## Prompts（恰好三个）

- `analyze_blueprint_task`
- `inspect_blueprint_node`
- `design_blueprint_patch`

`design_blueprint_patch` 必须先展示 validate 的 `humanSummary`。在当前对话中用户明确批准前，绝不能调用 `blueprint_patch_plan_confirm`。

## 验证边界

Validator 检查 current revision、existing Node/Pin ownership、proposed signatures、CONNECT OUTPUT→INPUT、operation DAG、limits、blockers 与 capability requirements。它明确不声称 Pin type 兼容、runtime 正确、DevKit 可创建该节点、compile/save 正确或 `TryCreateConnection` 会成功。
