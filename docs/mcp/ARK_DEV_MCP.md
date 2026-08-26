# ARK Dev Blueprint MCP Phase 3

本功能把 Blueprint-to-Code 已有的公开 Blueprint Evidence 与 Interpretation 能力，通过本机 Windows `stdio` MCP 暴露给 Codex。它是 source/developer feature；portable runtime 尚未内置 MCP 依赖，首次运行前必须执行：

```powershell
.\scripts\install_arkdev_mcp.ps1
```

## 当前边界

- Windows-only、stdio-only，运行时不访问外部网络。
- 官方 MCP Python SDK 固定为 `mcp==2.0.0`，协议基线为 MCP `2026-07-28`。
- 五个 Evidence/Editor 只读工具保持兼容；六个 Phase 2 Task/Plan 工具与五个 Solver 工具只写本地 metadata，总计 16 个。
- 六个轻量 Resource、四个用户主动选择的 Prompt。
- 复用现有 Evidence authority、Interpretation、freshness、revision/manifest binding 与 bounded query。
- 不直接读取 `evidence.sqlite`，不新增 parser、数据库 schema 或无界图算法。
- `BlueprintToCodeExporter` 通过公开 Unreal Editor API 写 bounded atomic snapshot；MCP 只读该文件并执行六秒 freshness gate。
- Editor snapshot 写入仅发生在 DevKit 插件侧的 gitignored `.arkdev-bridge/**`；MCP 侧只写 `.blueprint-tasks/**` 与 `.blueprint-solvers/**` metadata，不写 Evidence、Interpretation、Capture、Harvest、KB 或 ARK 资产。
- 不提供 shell、Computer Use、HTTP transport、Patch Executor 或任何 ARK mutation。
- bridge 不是 RPC：无 Named Pipe、socket、HTTP listener、私有 DLL、内存 offset 或 Slate/截图爬取。

依赖方向固定为：

```text
Blueprint-to-Code core
  -> blueprint_service / FileEditorBridge / exact editor_binding
  -> tasking + solver contracts/store/services/validator
  -> MCP tool/resource adapters
  -> server.py
  -> run_arkdev_mcp.py
```

## 快速验证

```powershell
.\.runtime\arkdev-mcp\Scripts\python.exe scripts\diagnose_arkdev_mcp.py
```

诊断分别报告依赖、server import、stdio handshake、16-tool discovery、真实 Editor snapshot、Phase 1 fixture、Task create/resume/research/cache、Patch Plan draft/validate、配置渲染以及 Codex CLI/登记状态。诊断使用临时 Task/Solver root 并在结束时清理；它绝不调用 confirm。没有安装 Codex CLI 时，`CODEX_SERVER_LISTED` 必须显示 `SKIPPED_WITH_REASON`，不能伪装成通过。

默认 bridge 文件为 `<repo>/.arkdev-bridge/editor_state.json`。测试可通过 `ARKDEV_EDITOR_BRIDGE_STATE_FILE` 覆盖。reader 仅接受 2 MiB 内的普通 UTF-8 JSON 文件、精确 schema、0..2000 nodes、合法 UTC 时间与只读 capability；缺失、过期、无效或插件报告断开都返回 `connected=false`，不是 MCP protocol error。

fixture diagnosis 必须通过独立的 `--editor-fixture-state-file` 显式运行，并使用 `FIXTURE_EDITOR_*` 前缀；它不会覆盖或冒充真实 `EDITOR_*` 结果。真实 DevKit 手工验收见 [EDITOR_BRIDGE_MANUAL_ACCEPTANCE.md](EDITOR_BRIDGE_MANUAL_ACCEPTANCE.md)。

默认 fixture 名为 `InterpretationFixture`；若当前 capture root 没有该公开测试资产，请通过 `--capture-root` 和 `--fixture-asset` 指定一份已发布的 FRESH fixture。诊断会如实报告 `BLUEPRINT_FIXTURE_CALL_OK=false`，不会临时生成或修改 Evidence。

详细设置见 [CODEX_SETUP_WINDOWS.md](CODEX_SETUP_WINDOWS.md)，工具合同见 [MCP_TOOL_CONTRACTS.md](MCP_TOOL_CONTRACTS.md)，Task/Plan 工作流见 [CODEX_TASK_WORKFLOW.md](CODEX_TASK_WORKFLOW.md)。

## 权威来源

- [Official MCP Python SDK v2.0.0](https://github.com/modelcontextprotocol/python-sdk/tree/v2.0.0)
- [MCP Specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)
- [OpenAI Codex MCP configuration](https://developers.openai.com/codex/mcp)
