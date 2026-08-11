# Deferred Follow-ups

以下项目有意延期，不属于 Phase 2 / Phase 3B 范围：

- 持续的 ARK DevKit Editor Bridge、active/focused editor tracking 与启动脚本。Phase 3B 只探测官方 scripting surfaces，并允许一次显式目标、one-shot、read-only Graph Snapshot。
- Patch Executor、Graph Diff 执行、compile/save、rollback receipt 与 runtime correctness 验证。
- HTTP/SSE transport、OAuth、远程服务和 plugin packaging。
- portable runtime 内置 MCP 依赖。
- Computer Use、截图识别、视觉模板和蓝图施工图。
- `blueprint_service.py` 拆分与 query planner 优化；Phase 2 有意只复用现有领域函数。
- Pin type compatibility、`TryCreateConnection` 与当前 DevKit build 的 node creation availability 验证。
- Editor Utility Blueprint/Widget fallback asset；只有 Phase 3B 证明 Python 路线不足且 Editor Utility 可用后，下一阶段才单独设计。
- 将 Phase 3B one-shot snapshot 接入现有 EditorBridge protocol；本阶段不扩展 MCP tool list。

Phase 2 只证明 proposed-node 的声明、唯一 CREATE_NODE 与 operation dependency closure 完整；它不把该结构性检查扩张为 Unreal 类型、factory、compile、save 或 runtime correctness 声明。

任何未来 mutation 工具必须使用新名称、独立审批和可验证 rollback，不能改变五个 Evidence 只读工具或六个本地 metadata 工具的语义。

PR #43 保留为需要匹配 C++ source-plugin build 环境的独立 Draft 路线。Phase 3B 不修改、合并、关闭或 retarget 它，也不复制其 C++ bridge 实现。
