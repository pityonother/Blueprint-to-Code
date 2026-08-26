# ARK DevKit Editor Bridge 手工验收

这份清单只验证 Phase 3 的只读 Editor State Bridge。它不授权或测试任何 Blueprint mutation、Patch Executor、compile、save 或 rollback。

## 前置条件

1. 从当前 Draft PR branch 获取源码。
2. 使用 `scripts/devkit_plugins/install_blueprint_to_code_exporter.ps1` 安装/编译 `BlueprintToCodeExporter` 0.2.0。
3. 如果脚本提示当前 DevKit 缺少 C++ source-plugin build 环境，停止安装并记录：

   ```text
   DEVKIT_PLUGIN_COMPILE=UNAVAILABLE_IN_CURRENT_INSTALL
   LIVE_EDITOR_BRIDGE=NOT_RUN
   ```

   不要使用 `-ForceSourceInstall` 绕过一个已知不能编译插件的环境，也不要复制私有 DLL 或使用内存 offset。将探测结果记录到 `EDITOR_BRIDGE_FEASIBILITY_RESULT.md` 后停止，不得把 fixture/source-contract PASS 当作真实运行 PASS。

## 验收步骤

1. 启动 ARK DevKit，确认插件未被禁用。
2. 打开一个专用测试 Blueprint。
3. 在两个 Graph 之间切换，运行：

   ```powershell
   python scripts\validate_arkdev_editor_bridge_snapshot.py
   python scripts\diagnose_arkdev_mcp.py
   ```

4. 确认 `connected=true`、`graphStatus=FOCUSED_GRAPH`，并核对 active asset 与 focused Graph 都是当前对象。
5. 调用 `arkdev_editor_state`：

   ```json
   {
     "includeSelection": true,
     "includeGraphNodes": true,
     "maxGraphNodes": 200,
     "taskId": ""
   }
   ```

6. 核对 `graphNodes` 的 NodeGuid 与 graph-space `x/y`。NodeGuid 必须为 32 位 hex digits 且不能是全零 GUID；无有效 NodeGuid 的节点不得写入 `nodes`。返回顺序应为 `y, x, nodeGuid, name`，调用最多 1000 项，插件 snapshot 最多 2000 项。`nodeCount` 表示 Graph 中实际非空节点总数，`nodesOmitted` 可能同时包含容量截断和无有效 NodeGuid 的节点。
7. 移动一个节点但不要保存，仅用于确认位置与 `dirty=true` 在两秒内更新；随后立刻在 DevKit 中撤销该移动。
8. 核对 `compileStatus` 只读返回 `UP_TO_DATE`、`DIRTY`、`ERROR` 或 `UNKNOWN`；插件不得触发 compile。
9. 关闭 Blueprint。DevKit 应保持 connected，但 `activeAsset=null`、`activityStatus=NO_OPEN_BLUEPRINT`、`graphStatus=NO_ACTIVE_BLUEPRINT`。
10. 关闭 DevKit并等待超过六秒。snapshot 应被删除，或被 freshness gate 报为 disconnected/stale。

## 不变性检查

- 测试前后比较测试 Blueprint 文件 bytes/hash；插件不得自动保存或改变资产 bytes。
- Output Log 中不得出现自动 compile、save、node create/delete、pin connect/disconnect 或窗口 focus 操作。
- 公开 MCP/diagnostic 输出不得出现用户名、安装路径、repo 路径或 bridge state 文件路径。
- `selectionStatus=UNSUPPORTED_BY_DEVKIT_BUILD` 是允许结果；不得为 selection 引入 Slate tree crawl 或具体 `FBlueprintEditor` 私有实现依赖。
- `graphStatus=BLUEPRINT_EDITOR_INTERFACE_UNAVAILABLE` 与 `graphStatus=NO_FOCUSED_GRAPH` 是不同的只读降级结果，不得通过聚焦或打开编辑器来消除。
- `activeAssetBinding`、`activeGraphBinding`、node binding 只能在 exact current Evidence 下为 `EXACT`。
- 带 `taskId` 的调用前后，`.blueprint-tasks` 文件 bytes 与时间戳不变，并且 `mutationReady=false`。

## 单实例边界

Phase 3 只承诺同一个 Blueprint-to-Code root 对应一个正在运行的 ARK DevKit 实例。多个 DevKit 实例共享同一 `editor_state.json` 暂不支持；一个实例退出可能影响共享 snapshot。多实例命名与仲裁后移，不在本轮实现。

## 通过记录

只有完成上述真实 DevKit 步骤后才能填写：

```text
DEVKIT_PLUGIN_COMPILE=PASS
LIVE_EDITOR_BRIDGE=PASS
TEST_BLUEPRINT=<non-sensitive test asset name>
ACTIVE_ASSET=PASS
FOCUSED_GRAPH=PASS
GRAPH_POSITIONS=PASS
DIRTY_STATE=PASS
COMPILE_STATE=PASS
HEARTBEAT_WITHIN_2_SECONDS=PASS
IDLE_STATE=PASS
STALE_OR_SHUTDOWN_STATE=PASS
BLUEPRINT_BYTES_UNCHANGED=PASS
```

fixture、Python tests、静态 source contract 或普通 UE build 均不能替代这份真实 ARK DevKit 验收。
