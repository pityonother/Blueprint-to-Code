# Frontend App Shell Modules

## 重构前问题

`src/main.ts` 同时拥有应用启动、三个工作区组合、Legacy Control Center 类型、状态、视图、API 动作、job 轮询、localStorage 和 DOM 事件。任何新工作区接线都需要在同一个大入口中理解 Legacy 细节。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `src/main.ts` | 创建根节点和四个 workspace/controller，组合 App Shell，处理 workspace URL 导航与启动加载 |
| `src/app/shell.ts` | 纯渲染 Topbar、Footer、Loading 和 workspace shell |
| `src/control-center/types.ts` | Legacy API、资产、job、报告、队列类型 |
| `src/control-center/capture-queue.ts` | 纯队列解析、图类型归一化与标签 |
| `src/control-center/views/common.ts` | 通用 metric、button、状态、资产和报告纯渲染 helper |
| `src/control-center/views/workflow.ts` | Object Path、读取、结果和报告主流程的纯字符串渲染 |
| `src/control-center/views/capture.ts` | Capture queue、失败图页和补采面板的纯字符串渲染 |
| `src/control-center/views/advanced.ts` | DevKit、质检、notes、compare、legacy KB、历史和日志的纯字符串渲染 |
| `src/control-center/workspace.ts` | Legacy 状态、API 动作、job、localStorage、DOM binding 与 view-model 组合 |

## 依赖方向

```text
shared + app/router
        ↓
control-center/types + capture-queue
        ↓
control-center/views
        ↓
control-center/workspace
        ↓
main.ts
```

禁止反向依赖：Control Center 模块不导入 `main.ts`；纯 view 不调用 API 或访问 DOM、window、localStorage、clipboard；App Shell 不创建或导入 feature controller。

## Legacy workspace 合同

`LegacyControlCenterWorkspace` 是单实例 owner。它保留原字段初值、endpoint、请求 body、action 字符串、job polling 间隔、错误文案和以下 localStorage key：

- `blueprint-tool.selected`
- `blueprint-tool.captureQueue`
- `blueprint-tool.captureQueueCursor`

Blueprint 选中资产仍通过 `selectAssetByName()` 同步到 Legacy selected path。`renderLegacy()` 与 `renderExperimental()` 继续作为 `BlueprintController.render()` 的两个内容插槽。

## 明确不改

- 工作区 URL 与 query-string 清理规则
- UI 文案、class、id、`data-action` 和 disabled 条件
- Blueprint Interpretation / Evidence / Gaps / Trace 合同
- Harvest ranking/API/UI
- Knowledge vNext query/API/UI
- Legacy endpoint、job、Capture、Compare、DevKit 和知识库行为
- `src/blueprint/controller.ts`、`src/styles.css` 与后端路由

Blueprint Visual Plan 属于后续独立执行档案，本次没有实现。
