# Phase 1 MCP Tool Contracts

所有工具描述都明确包含 `READ-ONLY` 和 `NO ARK DEVKIT MUTATION`。工具不写文件/数据库，不触发 Capture、重分析、shell、进程、外部网络或编辑器 mutation。

## Tools（恰好五个）

| Tool | 主要输入 | 成功 schema | 关键约束 |
|---|---|---|---|
| `arkdev_status` | `{}` | `blueprint-to-code.arkdev-mcp-status/v1` | deterministic；不扫描全部资产；不含时间戳或本机信息 |
| `arkdev_editor_state` | `includeSelection` | `blueprint-to-code.arkdev-editor-state/v1` | Phase 1 默认明确返回 `DISCONNECTED`；不得声明 mutation capability |
| `blueprint_list_assets` | `query`, `limit<=100`, `cursor` | `blueprint-to-code.mcp-blueprint-assets/v1` | 复用公开 asset list；不触发分析；cursor deterministic |
| `blueprint_get_context` | `asset`, `goal`, graph/seed refs、caps、budget、continuation | `blueprint-to-code.mcp-blueprint-context/v1` | `maxHops<=2`；nodes/pins/edges 上限 100/400/400；budget 800..6000；fail closed |
| `blueprint_get_node` | `asset`, exact `nodeRef`, direct neighborhood | `blueprint-to-code.mcp-blueprint-node/v1` | 不模糊搜索；`maxHops<=1`；revision-bound |

每次成功返回简短文本摘要和经 `outputSchema` 校验的 `structuredContent`。业务执行错误使用 `isError=true`，并返回：

```json
{
  "schema": "blueprint-to-code.arkdev-mcp-error/v1",
  "code": "EVIDENCE_STALE",
  "message": "Current Blueprint evidence is stale.",
  "retryable": false,
  "details": {}
}
```

稳定 error codes：

```text
INVALID_ARGUMENT
ASSET_NOT_FOUND
EVIDENCE_NOT_FOUND
EVIDENCE_STALE
EVIDENCE_NOT_AUTHORITATIVE
EVIDENCE_REVISION_MISMATCH
GRAPH_SELECTION_REQUIRED
NODE_NOT_FOUND
RESULT_BUDGET_EXCEEDED
EDITOR_BRIDGE_NOT_INSTALLED
EDITOR_BRIDGE_UNAVAILABLE
INTERNAL_CONTRACT_ERROR
```

协议级参数 schema 错误由 MCP SDK 处理；业务错误不得包含 stack trace、raw exception、本机路径或秘密。

## Evidence 绑定和预算

- Context 只接受当前 authoritative、FRESH Evidence 与其精确绑定的当前 Interpretation。
- `querySignature` 绑定 Evidence revision、规范化 goal、graph/seed refs 和 budgets。
- continuation 是 opaque、bounded、revision-bound、query-bound 的显式 token；没有跨调用隐式 session。
- 无法唯一选图时返回最多五个候选和 `GRAPH_SELECTION_REQUIRED`，不会退化为全图扫描。
- Node 查询只接受当前 revision 的 exact `bp://` node reference。
- 所有公开结构递归执行 path-free 检查；Unreal `/Game/`、`/Engine/`、`/Script/` object path 不是本机文件路径。

## Resources（最多三个）

```text
arkdev://status
arkdev://editor/state
blueprint://assets/{asset}/health
```

它们只是已有只读能力的轻量 JSON 投影，不暴露全 Graph、Evidence DB 或报告。客户端不支持 Resources 时，五个 Tools 仍可独立使用。

## Prompts（恰好两个）

- `analyze_blueprint_task`：status -> context -> 最多五次 exact node，输出事实/假设/未知/建议后停止。
- `inspect_blueprint_node`：只分析一个 exact nodeRef 和直接邻域。

Prompts 不启动 shell 或 Computer Use，也不声称生成 Patch Plan。
