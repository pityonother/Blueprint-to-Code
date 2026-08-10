# Control Center 后端模块边界

## 重构前问题

`scripts/blueprint_tool_server.py` 同时拥有 HTTP/CLI 入口、进程级实例、资产状态、
DevKit、报告、Capture/Compare、Knowledge、Harvest 与 KB 查询适配。修改任一领域都需要
加载并理解整个入口文件，也容易让测试通过 monkeypatch 顶层全局对象的兼容面与领域实现
互相缠绕。

本次重构只改变代码归属，不改变公开行为。

## 新模块职责

| 模块 | 职责 |
| --- | --- |
| `blueprint_server/assets.py` | 读取 Capture 资产目录、计算图页/默认值/组件/Evidence 状态并返回纯数据 |
| `blueprint_server/devkit.py` | DevKit Content root、导出请求、`.uasset/.uexp` 直接读取与命令文本 |
| `blueprint_server/reports.py` | 报告目标、Evidence/报告查询、路径解析、分析命令与后台 job 组装 |
| `blueprint_server/captures.py` | 图页 Capture、缺失函数注记与资产 Compare |
| `blueprint_server/knowledge.py` | Legacy Knowledge 状态、构建/优先读取命令与 job |
| `blueprint_server/harvest.py` | Harvest 查询参数/错误适配与构建 job 请求；不拥有公式或 repository |
| `blueprint_server/kb_routes.py` | `kb_vnext` 的 GET HTTP 适配；不修改 KB 内部 authority 或 publication |

## 依赖方向

```text
blueprint_translator / kb_vnext
          ↓
blueprint_server domain modules
          ↓
blueprint_tool_server composition root + HTTP handler
```

禁止的反向依赖由 `tests/test_server_modularity.py` 锁定：

- domain module 不导入 `blueprint_tool_server`；
- domain module 不定义或导入 `BaseHTTPRequestHandler`、`ThreadingHTTPServer`、
  `ControlCenterHandler`；
- translator core 不依赖 `blueprint_server`。

## Compatibility facade

`scripts/blueprint_tool_server.py` 继续暴露原有 helper、常量、job 函数、
`ControlCenterHandler`、server factory 与 CLI。

纯函数使用 import re-export。依赖可替换进程对象或项目路径的函数使用 thin wrapper，
在每次调用时把当前顶层值显式传给领域模块。因此旧调用者继续可以 monkeypatch：

- `CAPTURE_ROOT` 与项目路径；
- `HARVEST_REPOSITORY`、`HARVEST_BUILD_MANAGER`；
- `KB_VNEXT_SERVICE`；
- DevKit `.uasset` 解析/写入 helper；
- 路由 side-effect helper 与状态函数。

领域模块不反向导入 compatibility facade，也不通过循环 import 读取这些值。

## Singleton ownership

| 进程级对象 | 唯一 owner |
| --- | --- |
| `HARVEST_REPOSITORY` | `blueprint_tool_server.py` |
| `HARVEST_BUILD_MANAGER` | `blueprint_tool_server.py` |
| `KB_VNEXT_SERVICE` | `blueprint_tool_server.py` |
| `KB_SHADOW_COMPARATOR` | `blueprint_tool_server.py` |

领域函数通过显式参数使用这些对象，不会创建第二套 repository、manager 或 service。

## 明确不改的行为

- URL、GET/POST 方法、HTTP status、JSON schema/字段/顺序与错误字符串；
- session、origin、remote bearer 与静态文件安全行为；
- job 创建、轮询、取消及线程/进程模型；
- Harvest Ranking Contract v2、Confirmed/Conditional、canonical variant、runtime
  profile、cache key 与公式；
- Blueprint Evidence/Interpretation；
- KB 的 `mode=shadow`、`defaultQuerySource=legacy`、authority、pointer、Snapshot 与
  publication；
- Windows x64 portable 入口和支持范围。

## 后续阶段

前端应用壳与 Legacy workspace 拆分属于 Phase 2，不在本次 PR 中。任何
`src/main.ts`、`src/blueprint/controller.ts`、`routes_blueprint.py` 或 Blueprint Visual
Plan 工作都必须使用新的独立执行档案、分支与 PR。
