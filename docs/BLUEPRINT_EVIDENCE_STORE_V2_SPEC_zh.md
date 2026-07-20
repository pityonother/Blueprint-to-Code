# Blueprint Evidence Store v2 规格（已实施并验证）

状态：Implemented / Validated。用户于 2026-07-11 确认实施；2026-07-19 完成 52 资产对账并把新生成默认切换为 `indexed`。本次未执行 `--prune-legacy`，现有 legacy 文件继续保留。

## 1. 架构决定

保留现有 `.uasset/.uexp` 二进制解析核心，重写解析之后的数据合同、持久化、索引和 AI 查询层。

不建议推倒整个解析器。现有代码中最难替代、最有价值的是 ARK/UE Package、Export、Property、CDO、Node、Pin 和 `LinkedTo` 的恢复规则；token 膨胀发生在这些事实已经解析出来之后。

目标结构：

```text
.uasset / .uexp / .ubulk
          ↓
现有二进制解码器（保留）
          ↓
规范化 EvidenceWriter（新建）
          ↓
captures/<Asset>/evidence/evidence.sqlite
          ↓
EvidenceQueryService（新建）
     ↙              ↓              ↘
agent_index.md    CLI 查询        HTTP API
                                    ↓
                              兼容旧 Markdown
```

最终原则是：原始事实只保存一次，Markdown 只是按需生成的视图，不再充当唯一证据源。

## 2. Objective

为 ARK Blueprint 建立一个默认低 token、需要时能精确下钻、能明确表达“真正缺失”和“只是本轮未返回”的证据系统。

主要用户：

- 使用 Codex/其他 AI 分析蓝图的 Mod 开发者；
- 通过本地网页查看报告的人；
- 构建 ARK 知识库和业务数据库的脚本。

完成后的默认体验：

1. AI 首次只读取不超过 1,500 tokens 的资产索引。
2. AI 可搜索任意 Graph、Function、Variable、Event、Default 或 Diagnostic。
3. AI 可通过稳定 Evidence ID 精确取得一个 Node、其全部 Pins、相关 Wires、Properties 和缺口。
4. 任意被预算省略的信息都会返回数量、原因、游标和下一条查询命令。
5. 真正没有恢复的信息会说明缺失原因和下一步需要补采什么。

## 3. 当前问题与已测基线

当前 `build_blueprint_payload_from_nodes()` 会在单个逐图 JSON 中同时保存：

- 含完整 Pins/Links 的 `nodes`；
- 再次展平的 `pins` 和 `links`；
- 再次复制完整 Node 的 `function_calls`、`variable_gets`、`variable_sets`、`events`、`delegates`、`macros` 和 `comments`；
- 从同一批事实派生的 `exec_flow`、`data_flow` 和 diagnostics；
- 每张图重复一份 `profile_keyword_groups`、`node_semantics` 和 `ark_glossary`。

`write_uasset_graph_read_files()` 随后又同时写逐图 JSON、aggregate nodes、properties、pin links、质量文件和多份 Markdown；资产分析阶段再把所有逐图 JSON 整体读回内存。

四个真实蓝图的当前有效产物：

| 资产 | Graphs | Nodes | Pins | Link observations | 当前逐图 JSON |
| --- | ---: | ---: | ---: | ---: | ---: |
| `Buff_GigantoraptorPassProtection` | 3 | 10 | 24 | 20 | 419,270 B |
| `Buff_GigantoraptorBonded` | 10 | 101 | 308 | 289 | 5,077,611 B |
| `Buff_StriderHackingParent` | 27 | 620 | 1,956 | 1,352 | 14,427,474 B |
| `Archelon_Character_BP_ASA` | 25 | 674 | 2,216 | 2,222 | 36,720,080 B |

已确认的重复：

- 四个资产中，`nodes[*].pins` 与顶层 `pins` 逐项 100% 相同。
- Node 分类列表保存的是完整 Node 副本，不是引用。
- `target_pin_id_candidates` 会随 Node、Pin 和派生流重复；Archelon 的候选数组重复约浪费 9.47 MB。
- Pretty JSON 缩进和换行使逐图文件比紧凑序列化大约 50%。
- Strider 目录中还留有 manifest 未引用的 27 个旧逐图文件，共 19.05 MiB；当前 writer 不清理旧运行残留。
- `--report-level compact` 只减少后续 Markdown/调试报告，不减少上述核心捕获产物。

当前 context pack 只从 `uasset_graph_nodes.json` 和 class defaults 取摘要；前者不含完整 Pin 默认值、Link 和 Property 值。因此小包可以指出“某图相关”，却不能稳定完成 Node/Pin/Link 下钻，只能重新打开巨型逐图 JSON。

## 4. Non-goals

首版不做：

- 重写 Package/Name/Import/Export 或 CDO 解码器；
- 修改 ARK DevKit、引擎目录或游戏资产；
- 让 AI 直接编写 SQL；
- 在一次响应中返回整个 Blueprint；
- 自动删除已有 legacy 产物；
- 引入外部数据库或第三方搜索依赖。

## 5. Canonical Evidence Schema

每个资产 revision 使用一个 SQLite 数据库。当前捆绑 Python 已验证为 SQLite 3.50.4，并启用 FTS5。

| 表 | 作用 |
| --- | --- |
| `asset_revisions` | Object Path、源文件内容指纹、解析器/schema 版本、生成时间 |
| `graphs` | Graph 身份、类型、export index、状态、置信度和计数 |
| `nodes` | Node 身份、类、函数/变量/事件、坐标、语义和来源 |
| `pins` | Pin 方向、类型、默认值、原生 ID、置信度和来源 |
| `edges` | 去重后的规范化 Wire，只保存一次 source→target 关系 |
| `edge_observations` | 原始方向化 Link 观察、解析状态和启发式证据 |
| `edge_candidates` | 启发式 target Pin 候选；逐行关联，不再复制候选数组 |
| `properties` | Asset/Graph/Node/Pin Property 的类型、值、置信度和原始偏移 |
| `class_defaults` | CDO/default 名称、类型、值和来源 |
| `diagnostics` | 结构化诊断、原因代码、影响和 next probe |
| `coverage` | 每个 scope 的 Node/Pin/Link/Default 恢复程度 |
| `references` | Function call、Macro、Delegate、外部类/资产引用 |
| `derived_claims` | 公式和行为推断；不复制底层 Node |
| `claim_evidence` | 派生结论到 Evidence ID 的多对多映射 |
| `search_entities` | Graph/Node/Pin/Default/Diagnostic 的统一搜索投影 |
| `search_fts` | FTS5 索引；不可用时回退普通索引和 `LIKE` |
| `source_manifest` | 实际参与本 revision 的文件、hash、大小和状态 |

规范化规则：

- Node 只保存一次。
- Pin 只保存一次，通过 `node_ref` 关联。
- Wire 只保存一次；原始双向观察保留在 `edge_observations`。
- Function/Event/Variable 分类保存 Node ref 或建立索引，不复制 Node。
- `exec_flow` / `data_flow` 按 ID 查询或生成精简派生事实。
- 静态 glossary/semantic map 每个 schema 版本只引用一次。
- Formula/behavior 只引用 Evidence ID。

## 6. Stable Evidence ID

原始 `EdGraphPin_*` 不能单独作为主键；真实资产中存在大量 Pin ID 碰撞。Evidence ID 必须与资产 revision 和图/节点位置绑定：

```text
bp://<asset-hash>@<revision-id>/g/<graph-export-index>
bp://<asset-hash>@<revision-id>/g/<graph-export-index>/n/<node-export-or-local-index>
bp://<asset-hash>@<revision-id>/g/<graph-export-index>/n/<node-id>/p/<pin-ordinal>
bp://<asset-hash>@<revision-id>/default/<encoded-property-path>
```

- `asset-hash`：规范化 Object Path 的 hash。
- `revision-id`：`.uasset + .uexp + .ubulk` 内容 hash，加解析器和 schema 版本。
- Graph 使用 export index；同名 Graph 返回多个 ref，不静默选一个。
- Node 优先使用 package/export index，否则使用 revision 内 local index。
- Pin 使用 Node ref + ordinal；原生 Pin ID/Persistent GUID 只作属性和搜索键。
- Wire ID 由两个 Pin ref 的规范组合生成。
- 跨 revision 比较另存 `logical_key`，不能拿旧 revision 的 ref 查询新资产。

## 7. Evidence Gap 是一等数据

系统必须区分：

1. `CONFIRMED`：有直接可追溯证据；
2. `HEURISTIC`：有证据，但 Link/Property 等包含启发式恢复；
3. `NOT_RECOVERED`：解析器当前确实没有恢复；
4. `SOURCE_NOT_AVAILABLE`：父类/native/macro 内部不在当前资产证据中；
5. `AMBIGUOUS`：同名或候选目标不唯一；
6. `AVAILABLE_NOT_RETURNED`：数据存在，只因本次 token 预算未展开；
7. `STALE_REVISION`：查询 ID/cursor 与当前资产 revision 不一致。

至少结构化记录：未解析目标 Pin、启发式 Link、未解析 Property 值、CDO 缺失、父类/native 函数体不可见、Macro 内部不可见、组件默认值缺失、Graph 缺页和需要手动补采的项目。

Class Default 还有一条强制语义：`value=[]` 本身不能证明 Unreal 数组真的为空。`entity` 必须同时返回 `valueStatus`、`valueUsable` 和有界 `parse` 元数据；只有 `array_parse.parsed=true` 才能把空数组当成 `CONFIRMED`。`parsed=false` 时，同一个 `[]` 只是占位值，状态必须是 `NOT_RECOVERED`，并由 `overview/gaps` 计数。对象与结构体数组另返回有界的 `resolvedObjectName(s)` 或带 element/property 位置的 `resolvedObjectFields`，不能让 revision-local PackageIndex 冒充稳定对象身份。

该状态必须贯穿消费者：知识库导入不写入 `valueUsable=false` 的占位值，但仍把对应 default gap 写入 unresolved work；主题摘要不拿不可用值生成事实或提高置信度；蓝图比较只把两边都可用的语义值归为行为差异，证据缺失或恢复归入 unknown/evidence 边界。

跨 revision 比较会递归处理结构体数组中的嵌套 `ObjectProperty` 与对象数组。稳定投影使用对象名和 `valuePath`，不直接比较 revision-local PackageIndex；只要嵌套对象投影未解析、缺失或被截断，整个 Default 就是不可比较证据，变化进入 `unknown_changes`，不能伪报成行为变化。

大量缺口也不能通过分页被静默丢失。repository 的 `gap_summary()` 保留 `total/returned/omitted/truncated`，同时按 status/reason 汇总完整计数并为每组保存少量样例；知识投影使用该聚合，而不是把默认前 200 条当成全部。即使一个资产没有可用 Default、没有图和公式，只要存在 evidence gap，业务库导入也必须写入 `unresolved_work`。

## 8. Query Contract

CLI 和 HTTP 必须复用同一个服务边界：

```python
EvidenceQueryService.query(request) -> response
```

首版操作：

- `overview`：低 token 资产入口；
- `search`：搜索 Graph/Node/Pin/Default/Diagnostic；
- `entity`：按 Evidence ID 读取一个实体；
- `neighborhood`：读取一个 Node 周围 N 跳的 Node+Pin+Wire 原子 bundle；
- `trace`：沿 exec/data Wire 上游或下游追踪；
- `gaps`：查询解析缺口、歧义和 next probe。

`overview.summary.gapCount` 与无筛选 `gaps.coverage.requested` 使用同一缺口集合，包含 Default 解码缺口，而不只统计 Graph/Link diagnostics。

每个响应必须包含：

```json
{
  "asset": {"id": "...", "revisionId": "..."},
  "items": [],
  "coverage": {
    "requested": 58,
    "returned": 7,
    "availableNotReturned": 51,
    "notRecovered": 0
  },
  "omissions": [{"reason": "AVAILABLE_NOT_RETURNED", "count": 51}],
  "nextQueries": [],
  "page": {"nextCursor": "..."},
  "budget": {"requested": 1200, "effective": 1200, "estimatedUsed": 1158}
}
```

约束：

- token 预算覆盖整个序列化响应，不只计算正文。
- 接受范围为 500–8,000 estimated tokens；低于 500 直接拒绝，高于 8,000 时保留原始 `requested` 并把 `effective` 明确截到 8,000。
- 推荐默认 800–1,200 tokens。
- 不提供无上限 `full`。
- Node bundle 必须原子返回，不能截在半个 Node/Pin 中间。
- Cursor 绑定 `revisionId + query hash + last ref`；资产变化时返回 `STALE_CURSOR`。
- `maxHops` 最大 3。
- JSON 与 Markdown 是两种渲染，不在同一响应复制两份内容。
- 同名搜索返回全部 ref；调用者必须显式选择。

建议命令：

```powershell
runtime\python\python.exe scripts\query_blueprint_evidence.py --asset-dir "captures\Buff_StriderHackingParent" overview --budget 1200
runtime\python\python.exe scripts\query_blueprint_evidence.py --asset-dir "captures\Buff_StriderHackingParent" search --query "NextTimeOut" --budget 800
runtime\python\python.exe scripts\query_blueprint_evidence.py --asset-dir "captures\Buff_StriderHackingParent" neighborhood --id "bp://..." --hops 2 --budget 1500
runtime\python\python.exe scripts\query_blueprint_evidence.py --asset-dir "captures\Buff_StriderHackingParent" trace --id "bp://..." --direction upstream --budget 1500
runtime\python\python.exe scripts\query_blueprint_evidence.py --asset-dir "captures\Buff_StriderHackingParent" gaps --scope "graph:UpdateBuffTimer" --budget 1000
```

HTTP 新增 `POST /api/evidence-queries`，请求/响应 schema 与 CLI 服务一致。

## 9. 默认 AI 文档

默认只生成一个 `output/agent_index.md`，内容不超过 1,500 tokens：

- Asset/Object Path/Revision；
- Graph、Node、Pin、规范化 Wire 和 unresolved 数量；
- 恢复覆盖率；
- 高信号 Graph/Default/Diagnostic；
- 当前没有展开的信息及数量；
- 可直接复制的下一条查询命令；
- `evidence.sqlite` 和 source manifest 路径。

它不能只说“省略了完整 Pins”；必须同时说明 Pins 有多少、是否已恢复、如何查询、是否存在歧义。

## 10. Artifact Modes 与迁移

增加：

```text
--artifact-mode legacy   # 仅当前 JSON/Markdown
--artifact-mode dual     # v2 + legacy JSON/Markdown
--artifact-mode indexed  # v2 + 小型索引；旧产物按需导出
```

已执行的迁移：

1. 原型和对账阶段使用 `dual`，验收后已切换 `indexed`。
2. 从现有 capture 建库时，只信任 `graphs_from_uasset_manifest.json`，禁止 glob 整个旧目录。
3. 真实问题链和全部 52 个资产已完成 Graph/Node/Pin/Link/Default/Gap 对账。
4. `context_pack` 已改从 Evidence Repository 查询，并引用 Evidence ID。
5. context pack、compare、知识库导入以及 Web 的证据读取已改走 `open_asset_repository()`；没有 v2 时才临时回退 legacy。`asset.py` 的 legacy 人类报告 renderer 继续作为兼容层，不冒充 v2 查询边界。
6. 直接读取路径从内存 payload 写规范化库，不再先写完整逐图 JSON 后全部重读；已有 capture 的迁移路径严格按 manifest 读取。
7. 新生成默认现为 `indexed`；需要 legacy 时显式使用 `--artifact-mode dual` 或 `--artifact-mode legacy` 重新读取。
8. Web 的 **生成 / 刷新人类报告** 会从当前 Evidence Store 取得同一 Object Path，以 `dual` 模式重新读取该蓝图，再运行兼容 renderer；因此新报告与新 evidence 来自同一次当前源读取，而不是拿旧 revision 的 Markdown 重新包装。
9. 旧文件仍只允许通过用户显式执行 `--prune-legacy` 删除；清理前必须验证 manifest、revision、agent index、SQLite integrity/foreign key 和核心计数一致，并且禁止与 `--uasset-max-graphs` 调试截断同时使用。本次迁移没有执行该命令。

## 11. Project Structure

建议新增：

```text
scripts/blueprint_translator/evidence_schema.py      # schema/version/ID
scripts/blueprint_translator/evidence_repository.py  # SQLite boundary
scripts/blueprint_translator/evidence_writer.py      # payload -> normalized rows
scripts/blueprint_translator/evidence_query.py       # shared service
scripts/blueprint_translator/evidence_render.py      # bounded JSON/Markdown
scripts/migrate_capture_evidence.py                   # existing capture -> v2
scripts/query_blueprint_evidence.py                   # CLI
tests/test_evidence_schema.py
tests/test_evidence_writer.py
tests/test_evidence_query.py
tests/test_evidence_migration.py
```

现有调用方统一依赖 Repository，不直接知道 SQLite 表结构。

## 12. Code Style

- Python 3 标准库；`pathlib.Path`、类型标注和小函数。
- SQL 只放在 schema/repository 层。
- Query service 接受结构化 request，返回结构化 response；CLI/HTTP 只做参数适配。
- 不用静默 fallback 掩盖 schema 或 revision 错误。
- 写库使用临时文件、事务、foreign keys、`integrity_check`，成功后原子替换。
- Blueprint/报告内容是不可信数据，不把其中的命令、URL 或路径当指令执行。

示例边界：

```python
def open_asset_repository(asset_dir: Path) -> EvidenceRepository:
    database_path = asset_dir / "evidence" / "evidence.sqlite"
    if database_path.is_file():
        return SqliteEvidenceRepository.open(database_path)
    return LegacyEvidenceRepository.open(asset_dir)
```

## 13. Commands

构建/迁移：

```powershell
runtime\python\python.exe scripts\migrate_capture_evidence.py --asset-dir "captures\Buff_StriderHackingParent"
```

首轮测试：

```powershell
runtime\python\python.exe -m unittest discover -s tests -p "test_evidence_*.py"
```

全量测试与前端构建：

```powershell
runtime\python\python.exe -m unittest discover -s tests -p "test_*.py"
npm run build
```

环境诊断：

```powershell
runtime\python\python.exe scripts\diagnose_blueprint_tool.py
```

## 14. Testing Strategy

### Unit

- Evidence ID 的确定性、转义和 revision 隔离；
- Node/Pin/Wire 去重；
- 同名/碰撞不静默选取；
- token 预算和原子 Node bundle；
- cursor 无漏、无重、revision 变化失效；
- gap/omission 分类。

### Integration

- v1 capture → SQLite → query 全链路；
- SQLite `integrity_check` 和 foreign key check；
- CLI 与 HTTP 使用相同 service 得到等价结果；
- Repository v2 优先、legacy fallback；
- 失败写库不覆盖上一份有效数据库。

### Real-asset regression

固定四条问题链：

1. 小：`IsPrimalDino`；
2. 中：`CachedCharsKilled@GetBondedChanges`；
3. 大：`NextTimeOut@UpdateBuffTimer`；
4. 复杂：`MapRangeClamped@GetAlgaePercentage`。

每条都验证：`search → ref → node → pins/links → raw property/evidence → gaps`。

## 15. Success Criteria

- 所有 52 个资产的默认 `agent_index.md` 都不超过 1,500 estimated tokens。
- Evidence query 接受 500–8,000 tokens；Search/outline 默认不超过 800；Node summary 不超过 600；Node connections 不超过 1,500；raw evidence 单页不超过 2,000；全局有效硬上限 8,000。
- Function、Variable、Event、Default 精确名称检索 recall 为 100%。
- Graph/Node/Pin/Property/Link observation 计数与源数据 100% 对账。
- 原始 Link observations、启发式状态和全部候选目标可按 ref 取回。
- 规范化 Wire 去重不删除原始 observation。
- 分页无重复、无遗漏；同名返回多 ref。
- v2 build 目录 actual files 与 manifest 完全一致；stale=0、missing=0。
- 每资产静态字典最多一份；分类列表只存 ref；Pin 只存一次。
- 对 49 个存在 legacy 分母的资产，v2 规范化产物聚合体积不超过 manifest 有效逐图 JSON 聚合体积的 50%，稳定目标为 40%。该门槛不是逐资产门槛；SQLite 固定页/索引开销会让小资产或个别资产的单体比例失真。3 个 direct-only 资产没有 legacy 分母，单独做内容对账。
- Validator 的 `--asset-dir` 只展示单资产比例，不以此制造硬失败；只有 `--capture-root ... --all` 才执行上述完整语料聚合门槛。
- 本地普通 search p95 < 100 ms；2-hop neighborhood p95 < 200 ms。
- `coverage` 能严格区分“预算省略”和“没有恢复”。
- 低于最小预算不会返回一个实际超预算的伪成功响应；高于硬上限时响应同时保留 requested/effective，最终序列化估算不得超过 effective。
- source fingerprint 包含 UAsset/UEXP/UBulk 内容、parser/schema 版本；Pin/Link 改变会使旧问题快照失效。
- 当前全部测试继续通过，新增行为均先有失败测试。

现有数据模拟的单节点读取结果：

| 资产/节点 | 当前整图估算 | 精准投影估算 | 降幅 |
| --- | ---: | ---: | ---: |
| 小 `IsPrimalDino` | 136,379 | 847 | 99.38% |
| 中 `CachedCharsKilled` | 469,757 | 695 | 99.85% |
| Strider `NextTimeOut` | 538,575 | 1,147 | 99.79% |
| Archelon `MapRangeClamped` | 299,702 | 1,361 | 99.55% |

这些是实施前的投影模拟。实施后真实验收见第 18 节。

## 16. Boundaries

Always：

- 保留来源、置信度、revision 和 Evidence ID；
- 先测试后实现；
- 原子写入并验证数据库；
- 使用真实四资产和全量 52 资产验证；
- DevKit 目录只读。

Ask first：

- 再次改变当前已验证的 `indexed` 默认策略；
- 执行 `--prune-legacy`；
- 删除旧公开字段或旧 HTTP 路径；
- 改变 Evidence ID/schema 的不兼容规则。

Never：

- 自动删除现有 capture 或 legacy 产物；
- 为了体积指标丢弃无法重建的原始证据；
- 用 Graph 名、原始 Pin ID 或数组位置静默解决歧义；
- 把“本轮没返回”写成“解析器不知道”；
- 修改 `C:\Program Files\Epic Games\ARKDevkit` 下的文件。

## 17. 已完成的迁移决定

实施期间使用 `dual` 对账；真实资产链与全部回归通过后，新生成默认已切换到 `indexed`。现有 legacy 文件没有自动删除，今后也只在用户明确执行 `--prune-legacy` 时清理。

该策略已获用户确认并完成测试驱动实施。

## 18. 2026-07-19 实施验收

- 全量重建：49 个 legacy migration + 3 个 direct read，失败 0；parser version 均为 v3。
- 全量 validator：52/52 资产通过；Graph/Node/Pin/Property/Link observation、候选目标、Default 和 Reference 对账通过。
- 体积：`199,325,862 / 574,866,459 = 34.6734%`，满足 40% 稳定目标和 50% 硬门槛。
- `agent_index.md`：52/52 不超过 1,500 estimated tokens，最大 1,498。
- 精确名称 recall、source hash、SQLite integrity/foreign key、manifest stale/missing、分页无损和四条真实问题链均通过。
- 最终性能复测（25 次，2026-07-19 最新独立进程）：移除搜索热路径中冗余的逐行 `lower()` 后，search p95 64.01 ms（门槛 100 ms）；真实 2-hop p95 10.16 ms（门槛 200 ms）；结果会随本机负载浮动，验收以门槛为准。
- `Buff_StriderHackingParent` 具体案例见 `docs/BUFF_STRIDER_HACKING_PARENT_EVIDENCE_V2_CASE_zh.md`。
- 未执行 `--prune-legacy`；ARK DevKit 安装目录只读。
- 最终边界回归确认：Web 人类报告刷新使用相同 Object Path 的 `dual` 当前源读取；查询预算合同为 500–8,000；截断读取不能触发 legacy 清理。
