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

- Build：`20260730T051513-345699a11f21`
- Source SHA-256：`345699a11f21831a5abff9ad86e8417dc8143c874810cc277105477ea1b3910e`
- Discovery SHA-256：`028a12c429903466aa52f99c5e63c8d90813585b9d5c6a8c303fbb93a9d6a31f`
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
- 10,587 declared facts；
- 102,329 effective facts；
- 136 semantic facts；
- 145 typed registration rows；
- 26 materialized relationship edges；
- 298,003 preserved legacy lineage rows；
- 1,199,519 invalidation dependencies；
- 20 exact native functions；
- 713 Blueprint-native candidates、1 confirmed link；
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
- gate/worker 失败时禁止 publication。

尚未交付为生产可用：

- 选择性 Blueprint/source ingest；
- 除 `CLASS_CLOSURE` 和 `EFFECTIVE_ENTITY` 外的完整 rebuild backend；
- production narrow-gate 执行与签名 artifact authorization；
- 绑定 source manifest 的生产 atomic incremental publisher。

当前没有可验证的 runtime observation-set loader，所以 runtime 只绑定汇总
hash，不虚构 per-set entries。当前 immutable-v2 快照已绑定完整 source
manifest。对完全相同输入运行 update，实测：

```text
status=cache_hit
cacheHit=true
fullRebuildPerformed=false
published=false
```

生产默认 `scripts/update_ark_kb_vnext.py` 对 runtime 或其他真实变化会在
任何 lock/staging/queue/current mutation 前 fail fast，并返回稳定 gap 与
`fullRebuildRequired=true`。这是一条安全的阻断路径，不是增量发布成功。

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

完整堆栈的 Python 全量验收为 `1214 passed, 4 skipped`，并通过 606 个
subtests；最终受影响矩阵为 `137 passed, 22 subtests`。前端 API/harvest
contracts 与 production Vite build 均通过。
Ruff 与 `git diff --check` 通过。真实 full build、sealed validator、API
health/search、三次完整性能复测、storage integrity/FK/WAL/SHM 和 unchanged
update 均已执行。update 返回 `cacheHit=true`、`published=false`，没有创建
staging 或交换 current。

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
6. 生产增量目前只允许 1–32 个 add-only Blueprint Evidence 的 FACT
   materialization；update/delete/rename 和其他 backend 仍 fail closed；
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
