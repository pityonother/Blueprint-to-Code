# ARK DevKit Official Scripting Probe：一次性人工运行

本流程最多执行一次 DevKit 内 probe，不需要截图。Codex 不通过 Computer Use 操作编辑器。

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
  "maxNodes": 200
}
```

`objectPath` 必须是 `/Game/` 或 `/Engine/` 开头的 Unreal object path，不是本机文件路径。`graphName` 必须与 Blueprint 中的 Graph 名完全一致。

## 在 DevKit 内只运行一次

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
.arkdev-probe/explicit-graph-snapshot.json（仅成功时）
```

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
