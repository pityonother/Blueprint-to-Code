# Deferred Follow-ups

以下项目有意延期，不属于 Phase 2 / Phase 3B / Phase 3 read-only bridge 的已验证范围：

- 当前 DevKit build 的稳定公开 selection API；现阶段明确返回 `UNSUPPORTED_BY_DEVKIT_BUILD`。
- 用户侧真实 DevKit plugin compile 与 live runtime acceptance；fixture/source contract 不能代替。
- 持续的 active/focused editor tracking 与自动启动脚本。Phase 3B 只探测官方 scripting surfaces，并允许一次显式目标、one-shot、read-only Graph Snapshot；source bridge 仍需上述真实编译与运行验收。
- Patch Executor、Graph Diff 执行、compile/save、rollback receipt 与 runtime correctness 验证。
- Named Pipe、socket、HTTP/SSE transport、OAuth、远程服务和 precompiled plugin packaging。
- portable runtime 内置 MCP 依赖。
- Computer Use、截图识别、视觉模板和蓝图施工图。
- `blueprint_service.py` 拆分与 query planner 优化；Phase 2 有意只复用现有领域函数。
- Pin type compatibility、`TryCreateConnection` 与当前 DevKit build 的 node creation availability 验证。本轮只允许只读 `list_all_pins` / Pin 签名探测，不调用连接或 Pin mutation。
- Editor Utility Blueprint/Widget fallback asset；2026-08-12 bounded ObjectIterator closure 为 `PARTIAL_NO_NODE_ENUMERATION`，下一阶段若继续，必须作为独立 capability spike 设计。本轮只反射 `BlueprintGraphEditor` / `BlueprintGraphPinLibrary`，不创建或运行 Utility asset。
- 将 Phase 3B one-shot snapshot 接入现有 EditorBridge protocol；Phase 3B 的 2026-08-12 结果未达到该门。后续 Gate B 的 WC reflection 成功是独立路线，不追溯改变 Phase 3B 结论，也不扩展现有 MCP tool list。

Phase 3 只证明 bounded snapshot、freshness、公开 Editor state 与 exact Evidence/Task binding；它不把 live match 扩张为 mutation approval、Unreal 类型、factory、compile、save 或 runtime correctness 声明。

任何未来 mutation 工具必须使用新名称、独立审批和可验证 rollback，不能改变五个 Evidence 只读工具或六个本地 metadata 工具的语义。

PR #43 保留为需要匹配 C++ source-plugin build 环境的独立 Draft 路线。Phase 3B 不修改、合并、关闭或 retarget 它，也不复制其 C++ bridge 实现。

## Phase 3C initial Evidence-guided spike (2026-08-12)

初始 exact-node lookup spike 已实现合同与只读 probe，但当时检查的 312 个 indexed captures 都没有非空 node `NodeGuid`，v2 compatibility generation 也没有 public authority manifest SHA。因此该次 request builder 正确 fail closed，未安排 DevKit 人工运行；它没有用 UObject name、离线临时解析或全局 ObjectIterator 冒充 authority。随后独立生成的 v4 Cryopod Evidence 补齐了 Gate A 所需的 confirmed NodeGuid 与 current manifest binding，形成下述较新的 Gate A / Gate B 结果。

## Phase 3C Gate A / Gate B closure (2026-08-12)

- Gate A is `PARTIAL`, not a full pass. The current v4 Cryopod Evidence contains 45 Graphs and exact NodeGuid for 1678/1678 nodes. In the scoped EventGraph, GraphGuid and all 8/8 node identities are exact.
- The same EventGraph has 30 Pins, but authoritative native PinId and PersistentGuid recovery remain 0. Pin identity is unavailable and recovered Links remain heuristic; GUID-shaped values from `uasset_custom_pin_scan` must not be promoted to authority.
- Gate B contract is `PASS` for bounded existing-node identity. `WC_REFLECTION` returned 8 nodes for 8 Evidence locators, with 100% NodeGuid and position coverage.
- Gate B reported Pins as `UNAVAILABLE` and Links as `NOT_TESTED`. All recorded mutation/write-side flags were `false`, and the source asset was unchanged.
- Gate B therefore closes only the Node identity question. Pin identity remains `false`, and Phase 4A readiness remains `false`.
- The Gate B result records `adapter=true`; this permits only the next independently scoped WC Evidence adapter stage. It is not evidence that the adapter stage is complete.
- `PR45 retry=false`. PR #45 is not unblocked, and no retry, Patch Executor, connection mutation, compile, save, or runtime-correctness claim is authorized by Gate A or Gate B.

The next independent phase is the WC Evidence adapter: bind current Evidence locators to the bounded `WC_REFLECTION` read-only result while preserving exact revision/manifest identity and fail-closed NodeGuid matching. Pin recovery and any Phase 4A execution remain separate later gates.
