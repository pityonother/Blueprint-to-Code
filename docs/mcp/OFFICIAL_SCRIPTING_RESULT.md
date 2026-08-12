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
| `OBJECT_ITERATOR` | `ObjectIterator` 表面在当前 runtime 可见；具体迭代结果另看两种 iterator 字段 |
| `ED_GRAPH_NODE_CLASS` / `K2_NODE_CLASS` | 对应节点类在当前 runtime 可见 |
| `BLUEPRINT_GRAPH_EDITOR_CLASS` / `BLUEPRINT_GRAPH_PIN_LIBRARY_CLASS` | 对应类在当前 runtime 可见；不代表调用过 mutation |
| `OBJECT_GET_OUTER` / `OBJECT_GET_TYPED_OUTER` / `OBJECT_GET_PATH_NAME` | 对显式目标的实际只读调用成功，不只是方法名可见 |
| `EXPLICIT_ASSET_LOAD` | 指定 Unreal object path 实际加载成功 |
| `EXPLICIT_GRAPH_FIND` | 指定 Graph 实际找到 |
| `DIRECT_GRAPH_PROPERTY` | `graph.nodes` 或等价 editor property 实际可读 |
| `OBJECT_ITERATOR_EDGRAPHNODE` / `OBJECT_ITERATOR_K2NODE` | 对应 bounded Iterator 实际完成；扫描截断或调用异常为 `ERROR` |
| `EXACT_OUTER_NODE_ENUMERATION` | Graph nodes 由 identity、exact typed outer 或“EdGraph 类型 + 完整路径相等”证明；名字/类名/位置/路径前缀不算 |
| `GRAPH_NODE_ENUMERATION` | Graph nodes 经允许策略实际完整枚举，且未扫描截断 |
| `NODE_GUID_READ` | 每个 exact member 都读取到合法非全零 GUID；无效 GUID 节点不得进入权威返回集合 |
| `NODE_POSITION_READ` | 每个 exact member 都实际读取到 x/y |
| `NODE_LIST_ALL_PINS` / `NODE_PIN_READ` | Pin API 实际可调用 / 节点 Pin 签名实际读取成功；不是 snapshot 硬门 |
| `BLUEPRINT_STATUS_READ` | 显式 Blueprint compile status 实际只读成功 |
| `PACKAGE_DIRTY_READ` | package dirty state 有稳定官方只读调用且执行成功；不是 snapshot 硬门 |
| `ACTIVE_ASSET_READ` | 官方 API 能明确读取 active asset，而非从 opened list 推断 |
| `FOCUSED_GRAPH_READ` | 官方 API 能明确读取 focused Graph |
| `SELECTION_READ` | 官方 API 能明确读取 editor selection |
| `DIRTY_STATE_READ` | 官方 API 能明确读取 dirty state |
| `COMPILE_STATE_READ` | 官方 API 能明确读取 compile state |
| `EXPLICIT_GRAPH_SNAPSHOT` | snapshot 文件通过合同、digest、node cap 和 path-free 验证 |

计数语义固定为：`objectsScanned` 是实际检查的 Iterator 对象数；
`matchingNodes` 是去重后的 exact members（包含无效 GUID）；`returnedNodes` 是带
有效 GUID 的权威返回节点数；`nodesOmitted = matchingNodes - returnedNodes`。
`scanTruncated=true` 永远不能得到 snapshot PASS。

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
NODE_GUID_READ
NODE_POSITION_READ
EXPLICIT_GRAPH_SNAPSHOT
```

下一阶段可以评估把 one-shot snapshot 接入既有 EditorBridge protocol，但本 PR 不实施。

### `PARTIAL_NODE_ENUMERATION`

exact node enumeration 成功，但 GUID 或位置硬门未通过。必须逐项保留 GUID、位置
和 Pin 缺口，不接入 MCP。

### `PARTIAL_NO_NODE_ENUMERATION`

直接属性与 bounded ObjectIterator 都没有产出完整 exact node enumeration。扫描
达到 50,000 上限也属于此运行时可行性结果，但它只证明“允许预算内没有完成”，
不证明目标节点在全局 UObject 集合中不存在。

### `PARTIAL`

保留为旧 Probe v1 的历史路线名。Probe v2 新结果使用上述两个更精确的 partial
分支。

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

## 2026-08-12 ObjectIterator closure 实测记录

在同一 ARK DevKit 5.5.4 embedded Python 中执行了本轮唯一一次新 probe。v2
`live-probe.json` 的 digest、合同和 path-free 校验成功；能力 validator 按合同报告
`EXPLICIT_GRAPH_SNAPSHOT=ERROR` 并以非零状态 fail closed。未生成 snapshot。

```text
PROBE_VERSION=arkdev-official-scripting-probe/v2
OBJECT_ITERATOR=PASS
ED_GRAPH_NODE_CLASS=PASS
K2_NODE_CLASS=PASS
BLUEPRINT_GRAPH_EDITOR_CLASS=UNAVAILABLE
BLUEPRINT_GRAPH_PIN_LIBRARY_CLASS=UNAVAILABLE

EXPLICIT_ASSET_LOAD=PASS
EXPLICIT_GRAPH_FIND=PASS
DIRECT_GRAPH_PROPERTY=UNAVAILABLE
OBJECT_ITERATOR_EDGRAPHNODE=ERROR
OBJECT_ITERATOR_K2NODE=NOT_TESTED
EXACT_OUTER_NODE_ENUMERATION=NOT_TESTED
EXACT_TYPED_OUTER_NODE_ENUMERATION=NOT_TESTED

OBJECTS_SCANNED=50000
MATCHING_NODES=0
RETURNED_NODES=0
ENUMERATION_STRATEGY=NONE
SCAN_TRUNCATED=true

NODE_GUID_READ=NOT_TESTED
NODE_POSITION_READ=NOT_TESTED
NODE_PIN_READ=NOT_TESTED
BLUEPRINT_STATUS_READ=UNAVAILABLE
PACKAGE_DIRTY_READ=UNAVAILABLE

EXPLICIT_GRAPH_SNAPSHOT=ERROR
OFFICIAL_SCRIPTING_ROUTE=PARTIAL_NO_NODE_ENUMERATION
```

`ObjectIterator(EdGraphNode)` 确实开始运行，但在检查 50,000 个 UObject 后仍未
完成迭代，也未在预算范围内找到目标 `EventGraph` 的 exact-outer member。脚本按
预算记录 `OBJECT_ITERATOR_SCAN_LIMIT_REACHED` 并停止；由于共享扫描预算已经
耗尽，没有启动 `ObjectIterator(K2Node)`。这是一份合法的 bounded feasibility
结果，不是脚本编码错误，因此不允许第二次 DevKit 运行。

该结果不证明目标节点在全局 UObject 集合中不存在；它证明当前官方 Python
显式目标路线不能在本轮允许的 50,000 对象预算内完成权威节点快照。不能把它
升级为 `GRAPH_NODE_ENUMERATION=PASS`，也不能开始 MCP integration。

运行时还确认：

- `ObjectIterator`、`EdGraphNode`、`K2Node`、`Object.get_outer`、
  `Object.get_typed_outer` 和 `Object.get_path_name` 在反射层可见；
- `BlueprintGraphEditor`、`BlueprintGraphPinLibrary`、Graph list、Pin list、
  node-position library、Blueprint status 和 package dirty 查询在本次所需表面不可用
  或未进入节点读取阶段；
- mutation APIs 只记录为 `PRESENT_BUT_NOT_USED`；脚本没有调用 mutation、compile、
  save、open editor、connection 或 pin mutation。

```text
PROBE_SEMANTIC_DIGEST=1e8763c3ca7a5fd536dc85129b4cda0a291c0b07d75f66fe35bad279e5c675ea
NEW_MANUAL_RUNS=1
CUMULATIVE_MANUAL_RUNS=2
MUTATION_API_CALLED=false
ARK_ASSET_CHANGED=false
MCP_INTEGRATION_STARTED=false
PATCH_EXECUTOR_STARTED=false
```

本轮到此结束。下一项独立决策只能是 Editor Utility / GraphEditor capability spike，
或停止 runtime Bridge 并保留 Phase 1/2；本 PR 不执行任何一种后续路线。
