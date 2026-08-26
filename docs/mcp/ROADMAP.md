# ARK Dev MCP Roadmap

本文件只记录后续方向，不授权或实现任何下一阶段能力。

## Phase 2：Task Context + Patch Plan（完成）

显式 task handle、resume、revision-bound bounded graph slice cache，以及 exact Node/Pin draft/validate/explicit-confirm Patch Plan。CONFIRMED 仍为本地 metadata，`executionReady=false`。

## Phase 3：Read-only Editor Bridge（当前）

通过 `BlueprintToCodeExporter` 的 bounded atomic file snapshot，从公开 Editor API 读取 active asset、focused graph、NodeGuid/位置、dirty state、compile state 与 capability probe；MCP 以六秒 freshness gate 读取并 exact 绑定 Evidence/Task。selection 在当前 build 无稳定公开接口时显式降级；不使用截图猜测。

## Phase 4：人工批准的 Patch Executor

未来独立工具可考虑 dry run、批量应用、compile/save、recapture/diff 和 rollback。前置条件是 exact revision、backup、receipt、approval 与可验证 rollback。

## Phase 5：视觉识别与施工图

视觉仅作为节点外观模板、Computer Use fallback 或人类展示，不取代编辑器内部权威状态。

Phase 3 明确不包含 Phase 4 及之后内容，也不包含 Named Pipe、Graph mutation、HTTP/SSE/OAuth、远程服务或 portable runtime 内置 MCP。
