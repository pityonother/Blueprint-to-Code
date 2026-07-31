# ARK KB vNext 语义补全与工程加固收口报告

## 总体状态

Stage 8–12 建立了 typed registration 语义拆分、真实角色信号、独立 gold
fail-closed 计分、逐 case 查询诊断、immutable snapshot、发布前门禁密封、
原子 current pointer、burn-in attestation 和 rollback 边界。Stage 13–15 与
后续 `main` 加固又补上签名/registry 基础、artifact-bound review、durable
worker row scope、reparse-safe staging/quarantine、pointer CAS、writer lock、
UpdateBaseline 和 base-bound delta receipt。

这不等于“已经可以替换 legacy”。当前人工/实证 gold 和部分生产增量能力
仍缺失，所以交付状态是：

```text
implementation hardened
+ evidence gates intentionally open
+ keep legacy default
+ run vNext in shadow/compare
```

## Build identity

以下身份来自本机规范 current 指向的真实全量 post-hardening 快照：

- Build：`20260730T172442-19e56659d331`
- Source SHA-256：`19e56659d331489e1f82881d1a0c7dae3c51d73ba5397bc3601ccb8404054293`
- Discovery SHA-256：`028a12c429903466aa52f99c5e63c8d90813585b9d5c6a8c303fbb93a9d6a31f`
- Snapshot manifest SHA-256：`6c957681a6463c9e5d5e83ada999cf1d5cb24a64d53af6516eb0399c1fd29136`
- Current pointer SHA-256：`de74be48111cba8d3a1241b22cf94dc0e28945e32d084419163235383c6c556f`
- Source-manifest fingerprint：`fbb474d8ca1073dee5305cbe0247fdbec7fa4cbea97e882cb2cabc438b8750ca`
- semanticProducerContract：`66a8c3d93c9cce5485e0e82fdbd8092340e0db1c225e707ee7a97b0aab4d0eab`
- Previous build：`20260730T162735-b46eb9304da3`
- Previous manifest SHA-256：`9ae250a4dba1c01cd980cb8acee82831dd2516af822541e59afb96eb585a9e3c`
- Sealed quality report SHA-256：`84a6cd1dae885d7efe00e6174be72207e27a9a4681070d266707437c3a6f700b`
- Blueprint Evidence：234 entries
- 当前发布布局：`immutable-v2`
- 发布合同：根 `current.json` 指向
  `snapshots/<buildId>/manifest.json`
- 密封门禁：60/75 passed，15 failed
- Runtime health：`activeStaleSources=0`
- 当前 cutover：`shadow / legacy`
- Burn-in：`MISSING / BURN_IN_ATTESTATION_MISSING`

质量报告、benchmark、runtime health 和数据库 identity 已在 pointer 可见
前密封到 snapshot manifest；独立复核重算了报告与诊断哈希，并得到相同
60/75 结果。

`catalog.sqlite`、`core.sqlite`、`search.sqlite`、六个 projection、manifest
和密封报告是不可变权威；`cache.sqlite` 明确标记为 `disposable=true`，
允许运行时写入，但每次命中仍须验证 build、revision set、TTL 和
invalidation token。cache 的发布时 SHA 只描述空种子，不是运行时不变式。

## Actual semantic content

当前快照包含：

- 577,579 entities；
- 1,197,285 catalog nodes、3,442,470 edges、576,341 packages；
- 26,495 classes、92,033 closure rows；
- 1,091,270 roles；
- 10,587 declared facts；
- 102,329 effective facts；
- 136 semantic facts；
- 145 typed registration rows；
- 26 materialized relationship edges；
- 298,003 preserved legacy lineage rows；
- 1,199,519 invalidation dependencies；
- 20 exact native functions；
- 713 Blueprint-native candidates、1 confirmed link；
- 234 Blueprint Evidence entries；
- 六个核心 domain projections 共 136 行。

这些是本轮 full snapshot 的真实统计。fingerprint、
`LEGACY_UNVERIFIED`、`STALE`、`NOT_RECOVERED` 和 candidate edge 仍不计作
可用语义答案。

## Domain projections

| Projection | Rows | Complete | Partial | 状态 |
|---|---:|---:|---:|---|
| `buff_effects` | 46 | 46 | 0 | `VALID` |
| `item_properties` | 28 | 28 | 0 | `VALID` |
| `status_values` | 13 | 13 | 0 | `VALID` |
| `loot_entries` | 28 | 0 | 28 | `VALID` |
| `harvest_rules` | 10 | 0 | 10 | `VALID` |
| `mission_rewards` | 11 | 9 | 2 | `VALID` |

投影均通过 Core/artifact content binding。`loot_entries` 与
`harvest_rules` 仍是 partial，文档不会把非零误写成完整。

## Query gold

| 指标 | 当前 |
|---|---:|
| 固定 cases | 130 |
| `HUMAN_REVIEWED` | 5 |
| `FIXTURE_EXACT` | 125 |
| human gold 门槛 | 120 |
| cutover 状态 | 阻断 |

Protocol case 可以证明 identity-only 不冒充 semantic complete、candidate
edge 不闭合、stale 不泄漏、gap/probe 稳定，但不能证明事实答案正确。只有
独立人工/实证 case 才进入 semantic exact-match、wrong-answer 和 stale-leak
的切换口径。

## Registration

Core materialization 已改成具体 edge type，不再统一输出 `REGISTERS`。
Global registration、mechanism relationship 与 placement/world relation
分开；缺 Owner、Target、fresh source revision 或可恢复 Evidence 的 complete
claim 会降级。

真实 Owner→Target gold 当前为 **0/100**。现有 property-name unit fixture
只验证分类器局部行为，不计生产 precision/recall。门禁会重算期望 edge 并
核对 Owner resolution、Target resolution、edge materialization 与
Evidence correctness。

## Roles

Role classifier v2 已接入真实的：

- query domain 与 repeated fact demand；
- confirmed cross-domain evidence；
- formula 与 native confirmation；
- component、animation notify、curve、collision、material parameter；
- world placement；
- ancestry-derived semantic category percentile。

不新鲜或缺 source revision 的信号不计确认；self-derived benchmark demand
不计人工需求。独立 role gold 当前为 **0/300**，因此不能声称真实资产总体
precision/recall 已达标。

## Native

- exact functions：20/20 gold targets；
- confirmed BP-native edges：1；
- confirmed field accesses：0；
- candidate BP-native links：713。

规范 current 已生成恰好 1 条 `CONFIRMED/HIGH` BP-native link：

- Blueprint：
  `/Game/Genesis/Dinos/Shapeshifter/Shapeshifter_Small/Shapeshifter_Small_Character_BP.Shapeshifter_Small_Character_BP`；
- function：`AddItemObjectEx`；
- native target：`UPrimalInventoryComponent::AddItemObjectEx`，
  RVA `0x1390DB0`；
- resolution：`verified_callsite`；
- Blueprint 与 Native source revision 均为 `FRESH`。

该行通过 qualified symbol、RVA、真实 callsite、signature、双方 Evidence、
规范 SHA-256、带时区 revision 与 freshness 校验；旧 name-only/未绑定行
保持 candidate。`native.gold_targets_resolved` 与
`native.blueprint_link_precision` 均通过。整体仍为
`shadow / legacy`，因为 native 通过不能替代独立 query、registration 和
role gold。

## Incremental

已交付：

- 11 类 typed rebuild task；
- queue claim、状态机、孤立 RUNNING 恢复、receipt 与目标状态核验；
- 无 backend 时稳定 `BLOCKED_GAP`；
- full builder/update 共用的 source-manifest 模型、扫描与 diff；
- 新 full snapshot 对 source manifest 与 fingerprint 的原子 binding；
- 10 项 semantic input、runtime 汇总、每个 capture revision 和每个已验证
  Native evidence set 的 path-free identity；
- `generatedAt` 不参与 fingerprint 的 unchanged input cache hit；
- single-writer lock 和 publisher receipt 合同；
- current pointer/manifest 的 exact baseline 与发布前二次核验；
- Windows/POSIX no-follow whole-tree staging 和单资产 additive quarantine；
- v3 base-bound delta receipt、独立 raw SHA 与 staged/live Core 复核；
- 从已验证 durable event 与 terminal receipt 推导的 additive invalidation scope；
- 生产 `QUERY_SNAPSHOT` backend：只在 worker-owned cache transaction 中按
  顺序清理 `context_packs`、`answer_plans`、
  `materialized_neighborhoods` 与 `query_snapshots`，保留严格的 whole-cache
  equal-digest receipt、external marker 和崩溃恢复合同；
- selective `ROLE_ENTITY`：按 content-addressed percentile dependency proof
  展开真实闭包，并同步重建 role、depth、metrics 与 signal metrics；
- ontology-owned `DOMAIN_ENTITY`：只替换目标实体的 class ancestry 与 typed
  registration membership，保留 manual/map/其他 producer；
- 六个 exact `PROJECTION` backend：固定 ID/name 反向校验、同卷 staging、
  artifact/Core 内容核验、external marker 与单文件原子替换；
- 固定 11 项 production narrow-gate runner：从最终 staged candidate 计算
  observation，绑定 v3 delta、完整 worker receipts、projection digests、cache
  marker、candidate lineage 与未变化 base；
- incremental candidate reseal：为 10 个数据库/投影、runtime health、质量报告、
  manifest 与 previous Snapshot 生成并复核一个新的 immutable build identity；
- atomic shadow publisher：reserved staging、同卷 rename、exact pointer CAS、
  CAS 前 live Source Manifest 复核、切换后独立回读与 content-addressed local-write
  receipt；
- gate/worker 失败时禁止 publication。

仍未交付为 cutover/生产授权：

- 独立真实签名的 artifact/production authorization；
- live 合格单新增输入上的 shadow publication 与对应 E4 运行证据；
- 75/75 Gold、三轮 burn-in、rollback/concurrent-reader 实际演练与 cutover。

PR #27 已以 merge commit
`86c7715dab7dc15635c0cb18789f36d5cd8f3f69` 合入 `main`。真实 Scarecrow
prepublication 回放证明选择性 ingest、FACT、EFFECTIVE_ENTITY 与
QUERY_SNAPSHOT 的已接通路径：

```text
SUCCEEDED=4
BLOCKED_GAP=8
FAILED=0
baseBindingVerified=true
productionAuthority=false
published=false
e4Scenario2Complete=false
```

当时 4 个成功任务为 `FACT × 2`、`EFFECTIVE_ENTITY × 1` 和
`QUERY_SNAPSHOT × 1`；当时 8 个阻断任务为 `ROLE_ENTITY × 1`、
`DOMAIN_ENTITY × 1` 和 `PROJECTION × 6`。v3 receipt 的独立 raw SHA-256 为
`6c56aa85ff43349ac20b64fae93058e51ad645d27660099c87758ca62c5e94b3`。
这是历史运行证据，不用于冒充本轮能力或 live replay。

本轮 production-shaped 场景已得到精确 12-task drain：

```text
SUCCEEDED=12
BLOCKED_GAP=0
FAILED=0
remaining_pending=0
remaining_running=0
worker.drained=true
```

它仅证明 backend/worker 合同。live Source Diff 已变为 14 个新增和 9 个变更，
命中 `NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED`，所以没有执行真实 incremental
worker、narrow gates、publisher 或 E4 attestation。

隔离 Work Package B fixture 另行证明固定 11/11 narrow-gate report 和真实临时
目录 rename/CAS/独立 verification 路径。它不改变上述 live 结论；本机 current
仍未发生 pointer swap，Snapshot 数仍为 3。

## Storage

新发布合同为：

```text
current.json
  -> snapshots/<buildId>/
       catalog.sqlite
       core.sqlite
       search.sqlite
       cache.sqlite
       domain_exports/
       reports/
       manifest.json
```

构建器先对完整 staging 运行门禁，把报告和哈希密封进 manifest，再做目录
rename，最后原子替换 pointer。Reader 解析一次 pointer 后只从同一个
snapshot 目录打开所有库。

发布前会 checkpoint WAL、把所有主库切到 `journal_mode=DELETE` 并拒绝
sidecar。当前快照未发现 WAL/SHM；10 个主库/投影全部通过 integrity、FK、
schema 和绑定验证。密封 `runtimeHealth` 与 Core metadata 一致，
`activeStaleSources=0`。服务初始化约 `0.043s`，轻量 `health()` 约
`0.024s` 并返回 `READY / FRESH`；首次完整摘要绑定搜索约 `2.79s`，同服务
缓存后约 `0.13s`。

发布后的外部门禁报告位于 `reports/<buildId>/`，只作为 attestation。它
不能修改 current、immutable manifest 或默认来源；即使外部报告变为 eligible，
也必须构建一个密封该结果的新 snapshot。

## Verification

本轮相关自动化覆盖：

- typed registration 与真实 relationship gold fail-closed；
- role signal provenance 与独立 gold rejection；
- worker 状态机、receipt、rollback、外部 projection/cache 标记；
- immutable pointer、崩溃恢复、Windows 旧连接与并发 reader；
- API、benchmark 和 quality reader 的单次 snapshot 解析；
- immutable 外部报告不能改变 current；
- update command 首次运行、删除、缺 capability、gate failure 与 publication
  receipt 的 fail-closed 路径；
- 文档 build/source identity 与当前已发布 manifest 一致性。

本次文档基线对齐只运行文档身份测试、Markdown 链接检查、diff/秘密/路径
检查和 GitHub CI；没有把旧的全量测试数字冒充为本次验证，也没有运行 full
build 或再次回放 Scarecrow。运行时 health 和 Source Diff 均以只读方式复核。

复现命令：

```powershell
python -m pytest -q
python scripts\update_ark_kb_vnext.py `
  --discovery-database knowledge_base\discovery_bundle\kb_discovery.sqlite `
  --capture-root captures `
  --native-root native_evidence `
  --legacy-kb-root knowledge_base\db `
  --map-evidence-catalog analysis\harvest_nodes\resource_node_catalog.json `
  --output knowledge_base\vnext
git diff --check
```

## Cutover

当前已知关键阻断：

1. query human gold 5/130，低于 120；
2. registration relationship gold 0/100；
3. role gold 0/300；
4. query protocol compliance `98.46%` 和 wrong-answer rate `2.31%`
   未达门槛；expected-gap match 已达到 `95.74%`；
5. 单实体性能已通过：密封 P95 `3.786ms`，三次独立完整复测为
   `4.857 / 4.104 / 4.935ms`；
6. Additive rebuild backend 已在 production-shaped 12-task 场景闭合；live
   输入不是选择性单新增；narrow gates 与 publisher 已实现但未在 live 运行；
7. 没有 3 个连续合格 sealed builds、真实 shadow diff disposition、
   rollback/concurrent-reader 记录和 12 个生产增量场景 attestation。

密封报告中 native、projection、storage、runtime freshness 和 stale-leak
门已经通过；15 个失败门集中在 role gold、registration gold/派生指标和
query gold/正确性。性能不再是失败门，但它不能替代独立 review 或 burn-in。

只有所有关键门禁在发布前通过并密封到新 manifest，才允许：

```json
{
  "mode": "ready",
  "defaultQuerySource": "vnext"
}
```

当前建议和自动安全边界均为：

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy"
}
```

不删除 legacy，不把 candidate native edge、self-reviewed gold、
fixture-only precision 或外部 post-publication 报告提升为切换依据。
