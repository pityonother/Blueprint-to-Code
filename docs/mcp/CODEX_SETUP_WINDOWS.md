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
- 11 工具精确 allowlist
- `default_tools_approval_mode = "auto"`，只覆盖 Evidence 读取与 `.blueprint-tasks/**` 本地 metadata 写入

Phase 2 auto approval 不授权 ARK DevKit mutation。任何未来 create/connect/default/compile/save 工具必须使用不同审批策略，不能继承当前配置。

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
BLUEPRINT_FIXTURE_CALL_OK
TASK_CREATE_OK
TASK_RESUME_OK
TASK_RESEARCH_OK
TASK_CACHE_HIT_OK
PATCH_PLAN_DRAFT_OK
PATCH_PLAN_VALIDATE_OK
CODEX_CONFIG_RENDER_OK
```

`CODEX_CLI_AVAILABLE` 与 `CODEX_SERVER_LISTED` 是独立状态。CLI 存在但个人配置无法加载或尚未粘贴 server 配置时，后者可以为 `false`；诊断不会修改个人 Codex 配置。

## 故障定位

- 启动器提示环境缺失：重新运行 `install_arkdev_mcp.ps1`。
- `BLUEPRINT_FIXTURE_CALL_OK=false`：确认指定 capture root 中存在已发布、FRESH、authoritative 的 Evidence 与当前 Interpretation。
- handshake 失败：直接运行诊断；不要在 `run_arkdev_mcp.ps1` 中加入 `Write-Host` 或 banner。
- Codex server 未列出：先确认个人配置本身可解析，再粘贴渲染出的配置并重启 Codex。

服务 stdout 专用于 MCP framing；安全错误和启动失败仅写 stderr，且不输出 Evidence 内容、秘密或本机路径。Task handle 只能使用 opaque `task://` ID；不可把 `.blueprint-tasks` 文件路径放入 prompt 或公开 payload。
