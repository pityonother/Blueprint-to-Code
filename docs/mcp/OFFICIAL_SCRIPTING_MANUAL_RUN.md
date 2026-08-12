# ARK DevKit Official Scripting Probe v2：一次性人工运行

ObjectIterator closure 只允许一次新的 DevKit 内 probe，不需要截图。只有脚本因
明确编码错误而未生成合法结果时，才允许修复后重跑一次。Codex 不通过
Computer Use 操作编辑器。

## 运行前

1. 先运行外部安装扫描，确认 `NEXT_MANUAL_ACTION=RUN_IN_EDITOR_PROBE`。
2. 在 ARK DevKit 的 Plugins 中检查 `Python Editor Script Plugin` 和 `Editor Scripting Utilities`。
3. 如果插件存在但未启用，由用户决定是否启用并重启 DevKit；探测器本身不会改插件配置。
4. 选择一个不含敏感信息的测试 Blueprint，并确认目标 Graph 至少有三个 Node。

如果 Python 插件不存在或 embedded runtime 不存在，到此停止，不尝试其他自动化路线。

## 准备显式目标

在仓库根目录创建 gitignored 的 `.arkdev-probe/request.json`：

```json
{
  "schema": "blueprint-to-code.arkdev-scripting-probe-request/v1",
  "objectPath": "/Game/YourFolder/BP_Test.BP_Test",
  "graphName": "EventGraph",
  "maxNodes": 200,
  "maxObjectsScanned": 50000
}
```

`objectPath` 必须是 `/Game/` 或 `/Engine/` 开头的 Unreal object path，不是本机文件路径。`graphName` 必须与 Blueprint 中的 Graph 名完全一致。

`maxNodes` 的硬上限为 200，`maxObjectsScanned` 的硬上限为 50,000；达到扫描
上限即停止，不会转为无界 Iterator。

## 在 DevKit 内只运行一次

先打开或加载 `request.json` 指定的 Blueprint，再执行脚本。完成后只需回复
“已运行”，不要求截图。

二选一，不要两种方式都执行：

### 方式 A：菜单

选择 `File -> Execute Python Script`，然后选择：

```text
<repo>\scripts\arkdev_scripting_probe\in_editor\arkdev_official_scripting_probe.py
```

### 方式 B：Python 控制台

Python 插件已经启用时，在 DevKit 的 Python 控制台运行：

```text
py "<repo>\scripts\arkdev_scripting_probe\in_editor\arkdev_official_scripting_probe.py"
```

完成时只应看到：

```text
ARKDEV_OFFICIAL_SCRIPTING_PROBE=COMPLETE
PYTHON_RUNTIME=PASS
UNREAL_IMPORT=PASS
EXPLICIT_TARGET=AVAILABLE|MISSING|ERROR
```

脚本写入：

```text
.arkdev-probe/live-probe.json
.arkdev-probe/explicit-graph-snapshot.json（仅全部 snapshot PASS 门成功时）
```

`live-probe.json` 每次都会刷新。若本次不是权威 snapshot 成功，脚本会移除旧的
`explicit-graph-snapshot.json`，避免 validator 把历史文件误认成本次结果。

## 外部验证

回到仓库根目录运行：

```powershell
python scripts\validate_arkdev_scripting_probe.py
```

不要把 `.arkdev-probe` 原始文件提交到 Git。validator 的终端输出不包含用户名、安装路径或仓库路径。

## 本次运行明确不会做什么

- 不打开或切换 Blueprint；用户在运行前自行选择测试目标。
- 不读取窗口标题来推断 active/focused Graph。
- 不改 Node、Pin、默认值或位置。
- 不 compile、不 save、不创建 Editor Utility asset。
- 不连接 MCP，不启动 listener，不访问外部网络。

## 2026-08-12 closure 运行记录

本轮唯一一次新运行已完成并生成合法 v2 结果，状态为
`EXPLICIT_TARGET=ERROR`，gap 为 `OBJECT_ITERATOR_SCAN_LIMIT_REACHED`。这不是
编码错误，因此不得重跑。历史 Phase 3B probe 1 次，本轮 closure 1 次，累计
DevKit 人工运行 2 次。
