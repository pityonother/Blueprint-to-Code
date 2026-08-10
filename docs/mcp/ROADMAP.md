# ARK Dev MCP Roadmap

本文件只记录后续方向，不授权或实现任何下一阶段能力。

## Phase 2：Task Context + Patch Plan

显式 task handle、resume、bounded graph slice、draft/validate patch plan。不得依赖长对话隐式状态。

## Phase 3：Read-only Editor Bridge

从编辑器内部权威接口读取 active asset、active graph、selection、位置、dirty state、compile state 与 capability probe；不使用截图猜测。

## Phase 4：人工批准的 Patch Executor

未来独立工具可考虑 dry run、批量应用、compile/save、recapture/diff 和 rollback。前置条件是 exact revision、backup、receipt、approval 与可验证 rollback。

## Phase 5：视觉识别与施工图

视觉仅作为节点外观模板、Computer Use fallback 或人类展示，不取代编辑器内部权威状态。

Phase 1 明确不包含以上内容，也不包含真实 DevKit plugin、Named Pipe、Graph mutation、HTTP/SSE/OAuth、远程服务或 portable runtime 内置 MCP。
