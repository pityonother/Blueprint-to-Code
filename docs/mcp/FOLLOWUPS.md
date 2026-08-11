# Deferred Follow-ups

以下项目有意延期，不属于 Phase 3 范围：

- 当前 DevKit build 的稳定公开 selection API；现阶段明确返回 `UNSUPPORTED_BY_DEVKIT_BUILD`。
- 用户侧真实 DevKit plugin compile 与 live runtime acceptance；fixture/source contract 不能代替。
- Patch Executor、Graph Diff 执行、compile/save、rollback receipt 与 runtime correctness 验证。
- Named Pipe、socket、HTTP/SSE transport、OAuth、远程服务和 precompiled plugin packaging。
- portable runtime 内置 MCP 依赖。
- Computer Use、截图识别、视觉模板和蓝图施工图。
- `blueprint_service.py` 拆分与 query planner 优化；Phase 2 有意只复用现有领域函数。
- Pin type compatibility、`TryCreateConnection` 与当前 DevKit build 的 node creation availability 验证。

Phase 3 只证明 bounded snapshot、freshness、公开 Editor state 与 exact Evidence/Task binding；它不把 live match 扩张为 mutation approval、Unreal 类型、factory、compile、save 或 runtime correctness 声明。

任何未来 mutation 工具必须使用新名称、独立审批和可验证 rollback，不能改变五个 Evidence 只读工具或六个本地 metadata 工具的语义。
