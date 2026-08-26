# Codex Windows 设置

## 1. 安装独立环境

在仓库根目录执行：

```powershell
.\scripts\install_arkdev_mcp.ps1
```

安装器优先探测仓库 bundled Python，否则使用 Python 3.11+；依赖只安装到 gitignored 的 `.runtime\arkdev-mcp`，不会修改全局 Python。安装完成后会运行无输出 self-test。

## 2. 生成 Codex 配置

```powershell
.\.runtime\arkdev-mcp\Scripts\python.exe scripts\print_codex_mcp_config.py
```

把输出粘贴到个人 Codex 配置。提交的示例位于 `.codex\arkdev-mcp.config.example.toml`，只含 `<ABS_PROJECT_ROOT>` 占位符；不要提交含个人绝对路径的真实配置。

配置固定使用：

- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...\run_arkdev_mcp.ps1`
- `required = true`
- `startup_timeout_sec = 20`
- `tool_timeout_sec = 60`
- 16 工具精确 allowlist
- `default_tools_approval_mode = "auto"`，只覆盖 Evidence/Editor 读取与 `.blueprint-tasks/**`、`.blueprint-solvers/**` 本地 metadata 写入

Phase 2/3 auto approval 不授权 ARK DevKit mutation。任何未来 create/connect/default/compile/save 工具必须使用不同审批策略，不能继承当前配置。

## 3. 诊断

```powershell
.\.runtime\arkdev-mcp\Scripts\python.exe scripts\diagnose_arkdev_mcp.py
```

当指定 capture root 中存在所选 FRESH fixture 时，核心项应为 `true`：

```text
DEPENDENCY_INSTALLED
SERVER_IMPORTABLE
STDIO_HANDSHAKE_OK
TOOLS_DISCOVERED
STATUS_CALL_OK
EDITOR_BRIDGE_STATE_FOUND
EDITOR_BRIDGE_STATE_FRESH
EDITOR_BRIDGE_CONNECTED
EDITOR_ACTIVE_ASSET_AVAILABLE
EDITOR_ACTIVE_GRAPH_AVAILABLE
EDITOR_GRAPH_POSITIONS_AVAILABLE
EDITOR_SELECTION_AVAILABLE
EDITOR_EVIDENCE_BINDING_AVAILABLE
BLUEPRINT_FIXTURE_CALL_OK
TASK_CREATE_OK
TASK_RESUME_OK
TASK_RESEARCH_OK
TASK_CACHE_HIT_OK
PATCH_PLAN_DRAFT_OK
PATCH_PLAN_VALIDATE_OK
CODEX_CONFIG_RENDER_OK
```

真实 DevKit 尚未启动或 snapshot 缺失时，`EDITOR_*` 可以为 `false`；它们不影响 Phase 1/2 fixture diagnosis 的退出码。当前 build 不支持稳定 selection 时，`EDITOR_SELECTION_AVAILABLE=SKIPPED_WITH_REASON:unsupported_by_devkit_build` 是预期状态。

`CODEX_CLI_AVAILABLE` 与 `CODEX_SERVER_LISTED` 是独立状态。CLI 存在但个人配置无法加载或尚未粘贴 server 配置时，后者可以为 `false`；诊断不会修改个人 Codex 配置。

## 故障定位

- 启动器提示环境缺失：重新运行 `install_arkdev_mcp.ps1`。
- `BLUEPRINT_FIXTURE_CALL_OK=false`：确认指定 capture root 中存在已发布、FRESH、authoritative 的 Evidence 与当前 Interpretation。
- handshake 失败：直接运行诊断；不要在 `run_arkdev_mcp.ps1` 中加入 `Write-Host` 或 banner。
- `EDITOR_BRIDGE_STATE_FOUND=false`：先按手工验收文档编译/安装插件；不要用 fixture 冒充 live state。
- `EDITOR_BRIDGE_STATE_FRESH=false`：确认 DevKit 正在运行并等待一次两秒 heartbeat；默认超过六秒即断开。
- 单文件验证：运行 `python scripts\validate_arkdev_editor_bridge_snapshot.py`，输出不会包含真实文件路径。
- Codex server 未列出：先确认个人配置本身可解析，再粘贴渲染出的配置并重启 Codex。

服务 stdout 专用于 MCP framing；安全错误和启动失败仅写 stderr，且不输出 Evidence 内容、秘密或本机路径。Task handle 只能使用 opaque `task://` ID；不可把 `.blueprint-tasks` 文件路径放入 prompt 或公开 payload。
