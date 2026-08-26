# ARK DevKit Editor Bridge 可行性结果

## 结论

2026-08-11 在真实 Windows ARK DevKit 安装上执行了不带
`-ForceSourceInstall` 的只读构建能力探测。当前安装能够扫描插件，但不具备
编译 `BlueprintToCodeExporter` 0.2.0 C++ source plugin 所需的完整环境，也没有
可供安装的预编译模块。

```text
DEVKIT_BUILD_FINGERPRINT=5.5.4-0+UE5 / Installed / Development
DEVKIT_PLUGIN_COMPILE=UNAVAILABLE_IN_CURRENT_INSTALL
LIVE_EDITOR_BRIDGE=NOT_RUN
READY_TO_MARK_FOR_REVIEW=false
```

安装器在复制插件前终止。没有向 DevKit 插件目录写入半成品模块，没有触发
`cannot find module BlueprintToCodeExporter`，也没有加载插件或产生 live
snapshot。因此 active asset、focused Graph、node position/dirty、Evidence/Task
binding、idle/shutdown 与资产 bytes 不变性均未运行，不能标记为 PASS。

## 已确认的缺口

当前安装缺少以下构建条件之一：

- `Engine/Intermediate/Build/BuildRules/UE5Rules.dll`；
- `Engine/Source/Runtime`。

Phase 3 的 fixture、Python tests、source contract 和普通 UE signature 检查不能
替代真实 ARK DevKit 编译/加载验收。本结果不授权使用 `-ForceSourceInstall`、
复制私有 DLL、读取内存 offset、Computer Use 或任何 Blueprint mutation。

## GUID 合同收口

- snapshot 中的 bridge instance ID 与有效 NodeGuid 显式使用
  `EGuidFormats::Digits`，即 32 位十六进制数字；
- 无有效 NodeGuid 的节点不写入 `nodes`，全零 GUID 不能成为 exact binding；
- `nodeCount` 表示 Graph 中实际非空节点总数；
- `nodesOmitted` 可能同时包含 2000-node 上限截断和无有效 NodeGuid 的节点。

## 后续路径

本 PR 不实现 fallback。后续只保留以下三条独立路径：

1. 获取与目标 ARK DevKit build 匹配的独立插件构建环境，再执行真实编译与
   live acceptance；
2. 单独调查 DevKit 官方 Python 或 Editor Utility 是否能提供等价的只读状态；
3. 将 PR #43 保持为实验性 Draft，继续使用 Phase 1/2 的非 Editor 工作流。

## 单实例边界

Phase 3 只承诺同一个 Blueprint-to-Code root 对应一个正在运行的 ARK DevKit
实例。多个 DevKit 实例共享同一 `editor_state.json` 暂不支持；一个实例退出
可能影响共享 snapshot。多实例命名与仲裁后移，不在本轮实现。
