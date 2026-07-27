# ARK KB vNext 语义补全与工程加固收口报告

## 总体状态

Stage 8/9 的工程实现已完成以下收口：typed registration 语义拆分、角色真实
信号接线、独立 gold fail-closed 计分、受验证的 rebuild worker 边界、
immutable snapshot、发布前门禁密封、原子 current pointer 和统一 reader
解析。

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

- Build：`20260727T222549-a2d56bd7fed8`
- Source SHA-256：`a2d56bd7fed88edd1098915ea3723da0fdef0b0a263567b56f46bae074f385cd`
- Discovery SHA-256：`028a12c429903466aa52f99c5e63c8d90813585b9d5c6a8c303fbb93a9d6a31f`
- 当前发布布局：`immutable-v2`
- 发布合同：根 `current.json` 指向
  `snapshots/<buildId>/manifest.json`
- 密封门禁：58/75 passed，17 failed
- Runtime health：`activeStaleSources=0`
- 当前 cutover：`shadow / legacy`

质量报告、benchmark、runtime health 和数据库 identity 已在 pointer 可见
前密封到 snapshot manifest；发布后的外部复核得到相同 58/75 结果。

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
- gate/worker 失败时禁止 publication。

尚未交付为生产可用：

- 选择性 Blueprint/source ingest；
- 除 `CLASS_CLOSURE` 和 `EFFECTIVE_ENTITY` 外的完整 rebuild backend；
- selective narrow gates；
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

本轮最终受影响矩阵实跑结果为 `136 passed, 135 subtests passed`；合并后的
`test_kb_*.py` 全量验收为 `439 tests OK`，update 专项为 `46 passed`，
performance/document 专项为 `12 passed, 6 subtests passed`。Ruff 与
`git diff --check` 通过。真实 full build、发布后 sealed validator、API
health/search、外部门禁和 unchanged update 均已执行。外部门禁按预期以
非零退出并报告 58/75，而不是把 shadow 误报成失败构建。

复现命令：

```powershell
.\runtime\python\python.exe -m unittest discover -s tests -p "test_kb_*.py"
.\runtime\python\python.exe -m pytest tests\test_update_ark_kb_vnext.py -q
git diff --check
```

## Cutover

当前已知关键阻断：

1. query human gold 5/130，低于 120；
2. registration relationship gold 0/100；
3. role gold 0/300；
4. query protocol compliance `91.54%`、expected-gap match `76.60%` 和
   wrong-answer rate `9.23%` 未达门槛；
5. single-entity P95 `358.929ms`，高于 `<250ms`；
6. 单资产生产选择性 ingest/backend/publisher 尚未闭合。

密封报告中 native、projection、storage、runtime freshness 和 stale-leak
门已经通过；17 个失败门集中在 role gold、registration gold/派生指标和
query gold/正确性/延迟。

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
