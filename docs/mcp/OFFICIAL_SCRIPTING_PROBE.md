# ARK DevKit Official Scripting Probe

## 目标与边界

Phase 3B 只回答一个问题：当前 ARK DevKit 发行版实际暴露了哪些官方 Editor scripting 能力，以及能否对一个用户明确指定的 Blueprint Graph 生成一次性只读快照。

执行链固定为：

```text
外部安装扫描
→ DevKit embedded Python 运行时反射
→ 可选的显式 asset/graph 快照
→ 外部 path-free validator
→ 路线结论
```

它不实现持续 bridge、active/focused editor tracking、MCP tool、Blueprint mutation、compile、save 或 Patch Executor。

## 官方资料与事实边界

候选接口来自 Unreal Engine 官方资料：

- [在 Unreal Editor 中使用 Python 编写脚本](https://dev.epicgames.com/documentation/en-us/unreal-engine/scripting-the-unreal-editor-using-python)
- [AssetEditorSubsystem 5.5 Python API](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AssetEditorSubsystem?application_version=5.5)
- [BlueprintEditorLibrary 5.5 Python API](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/BlueprintEditorLibrary?application_version=5.5)
- [EditorUtilitySubsystem 5.5 Python API](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/EditorUtilitySubsystem?application_version=5.5)

这些资料只用于确定候选类和方法名。最终事实始终来自当前 ARK DevKit embedded Python 的 `hasattr` / `getattr` 运行时反射；不会用 UE 5.8 或其他安装的 API 推断 ARK DevKit 5.5 一定可用。

## 外部只读扫描

从仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\probe_arkdev_official_scripting.ps1 `
  -DevKitRoot "<ARK DevKit root>"
```

扫描器只读取 editor executable、build marker、`.uplugin`、`.uproject`、官方插件二进制和 `Content/Python` 位置。它不会复制文件、修改配置、启用插件或启动 DevKit。

完整安装结果写入 gitignored 的 `.arkdev-probe/install-capabilities.json`。终端只显示不含本机路径的能力摘要和 `NEXT_MANUAL_ACTION`。

## DevKit 内一次性探测（Probe v2）

`scripts/arkdev_scripting_probe/in_editor/arkdev_official_scripting_probe.py` 只执行以下操作：

- 导入 `unreal` 并读取 Python/engine 版本；
- 反射官方类、候选只读方法和相关可见方法名；
- 读取 `get_all_edited_assets`（如果确实存在），但不把 opened assets 推断为 active asset；
- 读取用户在 `.arkdev-probe/request.json` 指定的 asset 和 graph；
- 按 `graph.nodes`、`ObjectIterator(EdGraphNode)`、`ObjectIterator(K2Node)`
  的顺序尝试节点枚举；
- Iterator 成员只接受 `get_outer() is graph`，或“outer 确为 `EdGraph` 且完整
  Unreal object path 完全相等”；`get_typed_outer(EdGraph)` 可作为第二种 exact proof；
- 最多检查 50,000 个 UObject，最多返回 200 个节点；达到扫描上限立即以
  `OBJECT_ITERATOR_SCAN_LIMIT_REACHED` fail closed；
- 按 exact object path / GUID 去重；无有效非全零 GUID 的 exact member 只计入
  `matchingNodes`，不进入 `authoritativeNodes`；
- 只读 Node GUID、位置、Pin 签名、Blueprint compile status，并在官方只读
  dirty 查询存在时读取 package dirty state。

Probe v2 记录：

```text
objectsScanned
matchingNodes
returnedNodes
nodesOmitted
scanTruncated
enumerationStrategy
compileStatus
```

名字、类名、位置、数组顺序和路径前缀都不能作为 Graph 成员证明。

脚本不会打开或聚焦资产编辑器。详细的一次性人工步骤见 [OFFICIAL_SCRIPTING_MANUAL_RUN.md](OFFICIAL_SCRIPTING_MANUAL_RUN.md)。

## 只读保证

以下 API 即使在运行时存在，也只记录为 `PRESENT_BUT_NOT_USED`：

```text
open_editor_for_assets
compile_blueprint
add_function_graph
set_node_pos
break_pin_links
create_connection
save_asset
modify
set_editor_property
```

`BlueprintGraphEditor` 和 `BlueprintGraphPinLibrary` 只做反射。其创建、连接、
断开、删除和默认值修改方法即使可见，也只记录为
`PRESENT_BUT_NOT_USED`，本阶段绝不调用。

实现中不调用 `set_editor_property`、`modify`、`ScopedEditorTransaction`、compile、save、open/focus editor、socket、HTTP listener、private DLL、`ctypes`、Computer Use、截图或 OCR。

## 合同与验证

- Probe schema：`blueprint-to-code.arkdev-scripting-probe/v1`
- Probe implementation：`arkdev-official-scripting-probe/v2`
- Snapshot：`blueprint-to-code.arkdev-explicit-graph-snapshot/v1`
- 状态：`AVAILABLE | MISSING | ERROR | NOT_TESTED | PRESENT_BUT_NOT_USED`
- `semanticDigest`：对排除 `generatedAt` 和 `semanticDigest` 后的 canonical JSON 计算 SHA-256。

外部 validator：

```powershell
python scripts\validate_arkdev_scripting_probe.py
```

它只输出 `PASS | UNAVAILABLE | NOT_TESTED | ERROR` 的 path-free 能力矩阵。
其中类/方法可见性与实际调用能力分别记录；`NODE_LIST_ALL_PINS` 表示调用面，
`NODE_PIN_READ` 表示实际节点数据读取结果；旧的 active-editor
`COMPILE_STATE_READ` 不替代显式 Blueprint 的 `BLUEPRINT_STATUS_READ`。
字段解释和路线判定见 [OFFICIAL_SCRIPTING_RESULT.md](OFFICIAL_SCRIPTING_RESULT.md)。

## 与 PR #43 的关系

PR #43 是需要匹配 C++ source-plugin build 环境的独立路线，继续保持 Draft。本探测只使用发行版内官方 scripting surfaces，不复制 PR #43 的 C++ bridge、binding service 或 snapshot schema，也不修改、合并、关闭或 retarget PR #43。

如果 Python 路线成立，后续阶段才决定是否把一次性快照接入既有 EditorBridge protocol；本阶段不会开始该集成。
