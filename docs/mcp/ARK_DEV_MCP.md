# ARK Dev Blueprint MCP Phase 1

本功能把 Blueprint-to-Code 已有的公开 Blueprint Evidence 与 Interpretation 能力，通过本机 Windows `stdio` MCP 暴露给 Codex。它是 source/developer feature；portable runtime 尚未内置 MCP 依赖，首次运行前必须执行：

```powershell
.\scripts\install_arkdev_mcp.ps1
```

## 当前边界

- 只读、Windows-only、stdio-only，运行时不访问外部网络。
- 官方 MCP Python SDK 固定为 `mcp==2.0.0`，协议基线为 MCP `2026-07-28`。
- 只有五个高层工具、最多三个轻量 Resource、两个用户主动选择的 Prompt。
- 复用现有 Evidence authority、Interpretation、freshness、revision/manifest binding 与 bounded query。
- 不直接读取 `evidence.sqlite`，不新增 parser、数据库 schema 或无界图算法。
- 不连接或修改真实 ARK DevKit；Editor Bridge 只提供断开协议和测试 fixture。
- 不提供 shell、Computer Use、Capture、Task Context、Patch Plan、HTTP transport 或任何 mutation。

依赖方向固定为：

```text
Blueprint-to-Code core
  -> blueprint_service / editor_bridge protocol
  -> MCP tool/resource adapters
  -> server.py
  -> run_arkdev_mcp.py
```

## 快速验证

```powershell
.\.runtime\arkdev-mcp\Scripts\python.exe scripts\diagnose_arkdev_mcp.py
```

诊断分别报告依赖、server import、stdio handshake、tool discovery、status、fixture、配置渲染以及 Codex CLI/登记状态。没有安装 Codex CLI 时，`CODEX_SERVER_LISTED` 必须显示 `SKIPPED_WITH_REASON`，不能伪装成通过。

默认 fixture 名为 `InterpretationFixture`；若当前 capture root 没有该公开测试资产，请通过 `--capture-root` 和 `--fixture-asset` 指定一份已发布的 FRESH fixture。诊断会如实报告 `BLUEPRINT_FIXTURE_CALL_OK=false`，不会临时生成或修改 Evidence。

详细设置见 [CODEX_SETUP_WINDOWS.md](CODEX_SETUP_WINDOWS.md)，工具合同见 [MCP_TOOL_CONTRACTS.md](MCP_TOOL_CONTRACTS.md)。

## 权威来源

- [Official MCP Python SDK v2.0.0](https://github.com/modelcontextprotocol/python-sdk/tree/v2.0.0)
- [MCP Specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)
- [OpenAI Codex MCP configuration](https://developers.openai.com/codex/mcp)
