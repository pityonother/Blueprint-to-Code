# ARK DevKit Capability Ledger

本台账只记录实际运行或仓库内已成功使用的能力。`PRESENT` 只表示符号或入口可见，不能替代 `CALLABLE`、`ACTUAL_RESULT` 或权威等级。

证据优先级：真实 ARK DevKit 调用结果 > 仓库内已成功使用的脚本 > Wildcard 官方 DevKit 文档/Changelog > Unreal 5.5 官方文档（仅候选） > 更新版 Unreal 文档（不作为当前事实）。

| candidate | source | build | PRESENT | CALLABLE | ACTUAL_RESULT | BOUND | AUTHORITY_LEVEL | last verified |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Embedded Python runtime | Phase 3B one-shot editor probe | current installed ARK DevKit | yes | yes | Python runtime and `unreal` import passed | explicit one-shot probe | runtime-observed | 2026-08-12 |
| Explicit Blueprint load and `find_graph` | Phase 3B one-shot editor probe | current installed ARK DevKit | yes | yes | explicit target load/graph lookup executed; later node enumeration exhausted its bound | one asset and one graph | runtime-observed, target result was ERROR | 2026-08-12 |
| `ObjectIterator` graph-node fallback | Phase 3B one-shot editor probe | current installed ARK DevKit | yes | yes | shared 50,000-object bound reached with zero exact target nodes; result failed closed | 50,000 scanned objects, 200 returned-node cap | runtime-observed, unavailable for authoritative enumeration | 2026-08-12 |
| Binary `StructProperty(Guid/FGuid)` identity decode | current v4 Gate A real `.uasset` rebuild | current installed ARK DevKit content | yes | yes | Cryopod 的 45 个 Graph 中 1678/1678 NodeGuid 精确恢复；限定 EventGraph 的 GraphGuid 精确，8/8 节点身份可用 | declared size 16, exact bounds, non-zero only | authoritative Graph/Node binary evidence；Gate A 总体为 PARTIAL | 2026-08-12 |
| Binary PinId / PersistentGuid identity | current v4 Gate A real `.uasset` rebuild | current installed ARK DevKit content | partial | partial | 限定 EventGraph 有 30 个 Pins，但 native PinId 与 PersistentGuid 的精确恢复数均为 0；Pin identity unavailable，Links 仅 heuristic | one graph, 30 pins | heuristic only; not authoritative | 2026-08-12 |
| Wildcard `wc_*` Graph reflection | current Gate B one-shot result | current installed ARK DevKit | yes | yes | contract PASS；`WC_REFLECTION` 返回 8 nodes / 8 Evidence locators，NodeGuid 与 position 均为 100%；Pins `UNAVAILABLE`，Links `NOT_TESTED` | one explicit asset/graph, 8 requested locators, read-only | runtime-observed authority for existing-node identity only | 2026-08-12 |
| C++ source plugin compile | PR #43 / local environment audit | current local environment | source present | no | required source-plugin build environment unavailable | separate Draft path | unavailable | 2026-08-12 |

## Current v4 Gate A / Gate B result

- Gate A source: exact binary FGuid value decoding in indexed v4 Evidence.
- Cryopod inventory: 45 Graphs；1678/1678 nodes have exact, valid, non-zero NodeGuid values.
- Scoped EventGraph: GraphGuid is exact；8/8 nodes have usable object name/class/position/NodeGuid locators. Its 30 Pins have no authoritative native PinId or PersistentGuid, and recovered Links remain heuristic.
- Gate A verdict is `PARTIAL`: Graph/Node identity is authoritative, but Pin identity is unavailable. Gate A does not satisfy Phase 4A readiness.
- Gate B result: contract `PASS`；source is `WC_REFLECTION`；8 nodes matched 8 Evidence locators, with 100% NodeGuid and position coverage. Pin reading is `UNAVAILABLE` and Link reading is `NOT_TESTED`.
- Gate B safety: all recorded mutation/write-side flags are `false`, and the source asset remained unchanged.
- Gate B proves the bounded, read-only existing-node identity route only. It does not prove authoritative Pin identity, Link identity, mutation, compile, save, rollback, or runtime correctness.
- The result records `adapter=true`, which opens only a separately scoped WC Evidence adapter stage. `PR45 retry=false`; PR #45 remains blocked and must not be described as unblocked.

Current capability state: Node identity `PASS`; Pin identity `false`; Phase 4A readiness `false`. The next independent stage is the WC Evidence adapter, not Patch execution.
