# ARK DevKit Official Scripting Probe：结果解释

## 证据层级

结果按三层解释，不能越级推断：

1. 安装层：描述符、二进制或配置存在。
2. 反射层：embedded Python 中类或方法名可见。
3. 功能层：只读调用或显式 Graph Snapshot 实际成功。

类存在只证明反射层 `AVAILABLE`，不等于功能已经工作。`PRESENT_BUT_NOT_USED` 只表示 mutation API 名可见，绝不表示调用过。

## Validator 字段

| 字段 | PASS 的含义 |
| --- | --- |
| `PYTHON_RUNTIME` | probe 确实在 Python runtime 中执行 |
| `UNREAL_IMPORT` | `import unreal` 成功 |
| `ASSET_EDITOR_SUBSYSTEM` | 类在当前 runtime 中可见 |
| `BLUEPRINT_EDITOR_LIBRARY` | 类在当前 runtime 中可见 |
| `EDITOR_UTILITY_SUBSYSTEM` | 类在当前 runtime 中可见 |
| `EXPLICIT_ASSET_LOAD` | 指定 Unreal object path 实际加载成功 |
| `EXPLICIT_GRAPH_FIND` | 指定 Graph 实际找到 |
| `GRAPH_NODE_ENUMERATION` | Graph nodes 实际可枚举 |
| `NODE_GUID_READ` | 每个返回范围内的来源 Node 都读取到合法非全零 GUID |
| `NODE_POSITION_READ` | 每个来源 Node 都读取到 x/y |
| `ACTIVE_ASSET_READ` | 官方 API 能明确读取 active asset，而非从 opened list 推断 |
| `FOCUSED_GRAPH_READ` | 官方 API 能明确读取 focused Graph |
| `SELECTION_READ` | 官方 API 能明确读取 editor selection |
| `DIRTY_STATE_READ` | 官方 API 能明确读取 dirty state |
| `COMPILE_STATE_READ` | 官方 API 能明确读取 compile state |
| `EXPLICIT_GRAPH_SNAPSHOT` | snapshot 文件通过合同、digest、node cap 和 path-free 验证 |

Validator 值含义：

- `PASS`：对应运行时功能或合同验证已经成功。
- `UNAVAILABLE`：已经探测，但当前运行时缺少该能力。
- `NOT_TESTED`：没有进行该项功能探测，不能解释为成功或失败。
- `ERROR`：调用、文件或合同验证失败；必须 fail closed。

## 路线判定

### `EXPLICIT_TARGET_PYTHON_SNAPSHOT`

只有以下全部为 `PASS` 才成立：

```text
PYTHON_RUNTIME
UNREAL_IMPORT
EXPLICIT_ASSET_LOAD
EXPLICIT_GRAPH_FIND
GRAPH_NODE_ENUMERATION
NODE_POSITION_READ
EXPLICIT_GRAPH_SNAPSHOT
```

下一阶段可以评估把 one-shot snapshot 接入既有 EditorBridge protocol，但本 PR 不实施。

### `PARTIAL`

Python 与 `unreal` 可用，但上述完整快照条件没有全部通过。必须逐项保留缺口，不接入 MCP。下一阶段再比较显式目标 bridge 与 Editor Utility fallback。

### `EDITOR_UTILITY_CANDIDATE`

Python 路线不可用，但安装/运行时证据显示 Editor Utility 支持。本阶段不创建任何 Editor Utility asset。

### `UNAVAILABLE`

官方 Python 与 Editor Utility 路线均不可用。Phase 1/2 保持可用，PR #43 保持 Draft，Patch Executor 暂停；不会转向 private DLL、`ctypes`、Computer Use 或视觉识别。

## 确定性与隐私

Probe 和 snapshot 的 `semanticDigest` 排除 `generatedAt` 与 digest 自身。结果允许 Unreal `/Game/`、`/Engine/` object path，但 validator 拒绝 Windows 绝对路径、UNC、本机文件 URI 和常见机器 POSIX 路径。

原始 `.arkdev-probe` 目录始终 gitignored。公开终端与 PR 只记录 path-free 能力矩阵和路线结论。

## 本轮实测记录

2026-08-11 在当前 ARK DevKit 5.5.4 embedded Python 中执行了唯一一次人工 probe。外部安装扫描、probe digest 和 path-free validator 均通过；没有生成 snapshot 文件，因为流程在 Graph node enumeration 处按合同停止。

```text
DEVKIT_BUILD=5.5.4-0+UE5
PYTHON_SCRIPT_PLUGIN_PRESENT=true
PYTHON_PLUGIN_ENABLED=TRUE
EDITOR_SCRIPTING_UTILITIES_PRESENT=true
EDITOR_SCRIPTING_UTILITIES_ENABLED=TRUE
EDITOR_UTILITY_PRESENT=true

PYTHON_RUNTIME=PASS
UNREAL_IMPORT=PASS
ASSET_EDITOR_SUBSYSTEM=PASS
BLUEPRINT_EDITOR_LIBRARY=PASS
EDITOR_UTILITY_SUBSYSTEM=PASS

EXPLICIT_ASSET_LOAD=PASS
EXPLICIT_GRAPH_FIND=PASS
GRAPH_NODE_ENUMERATION=UNAVAILABLE
NODE_GUID_READ=NOT_TESTED
NODE_POSITION_READ=NOT_TESTED

ACTIVE_ASSET_READ=UNAVAILABLE
FOCUSED_GRAPH_READ=UNAVAILABLE
SELECTION_READ=UNAVAILABLE
DIRTY_STATE_READ=UNAVAILABLE
COMPILE_STATE_READ=UNAVAILABLE

EXPLICIT_GRAPH_SNAPSHOT=UNAVAILABLE
OFFICIAL_SCRIPTING_ROUTE=PARTIAL
```

运行时确认：

- `BlueprintEditorLibrary.get_blueprint_asset`、`find_event_graph` 和 `find_graph` 可见，显式 asset 与 named Graph 实际定位成功。
- `AssetEditorSubsystem.get_all_edited_assets` 和 `find_editor_for_asset` 不可见，因此没有从 opened assets 猜测 active asset。
- 当前 runtime 对找到的 `EdGraph` 未提供 probe 可读取的 node collection，gap 为 `GRAPH_NODE_ENUMERATION_UNAVAILABLE`。UE 5.5 的 [Python `unreal.EdGraph` 文档](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/EdGraph?application_version=5.5) 也未列出 node collection 的 Python editor property；这与 native `UEdGraph::Nodes` 是否存在是不同证据层级。
- `EditorUtilitySubsystem`、`EditorUtilityWidgetBlueprint` 以及 `try_run` / tab registration 候选方法可见，但本轮没有创建或运行 Editor Utility asset。

以下 mutation API 只通过反射观察到，状态均为 `PRESENT_BUT_NOT_USED`：

```text
AssetEditorSubsystem.open_editor_for_assets
BlueprintEditorLibrary.add_function_graph
BlueprintEditorLibrary.compile_blueprint
EditorAssetLibrary.save_asset
```

```text
PROBE_SEMANTIC_DIGEST=79304d01911f6927365d94ab3af6aca945245689598d7d251a94371d497fb7b9
MANUAL_PROBE_RUNS=1
MUTATION_API_CALLED=false
ARK_ASSET_CHANGED=false
COMPUTER_USE=false
PRIVATE_DLL=false
CTYPES=false
```

结论为 `PARTIAL`，不是完整 Graph Snapshot 成功，也不是官方 scripting surfaces 全部不可用。本 PR 不把该结果接入 MCP；下一阶段只能在显式目标 bridge 的其他官方只读面与 Editor Utility fallback 之间另行评估。
