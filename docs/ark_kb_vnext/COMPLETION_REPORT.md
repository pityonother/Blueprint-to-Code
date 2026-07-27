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

以下身份是本机默认目录中最后一个已经发布的基线，不是尚未生成的
post-hardening immutable-v2 快照：

- Build：`20260727T035514+0000-9f106a091815`
- Source SHA-256：`9f106a091815dd88aa729d28140db728e0f1b37dbeebf2fd5f2182492ef4ea50`
- 当前发布布局：`legacy-v1` 兼容读取
- 新发布合同：根 `current.json` 指向
  `snapshots/<buildId>/manifest.json`
- 当前 cutover：`shadow / legacy`

下一次完整构建会以完整 semantic input set 生成 source identity，并在
pointer 可见前把质量报告密封到 snapshot manifest。只有该新快照的报告
才能代表本轮加固后的真实全量统计。

## Actual semantic content

已发布基线仍包含：

- 577,579 entities；
- 10,588 declared facts；
- 102,330 effective facts；
- 135 typed registration rows；
- 28 materialized relationship edges；
- 298,003 preserved legacy lineage rows；
- 20 exact native functions；
- 132 Blueprint-native candidates、0 confirmed links；
- 六个核心 domain projections 均为 0 行。

这些数字只描述旧基线存量。新的 typed value、registration edge v2、role
signal v2 和 quality-gate 口径必须通过一次新 full snapshot 才能回填真实
全量数量。fingerprint、`LEGACY_UNVERIFIED`、`STALE`、
`NOT_RECOVERED` 和 candidate edge 不计作可用语义答案。

## Domain projections

| Projection | 已发布基线 | 切换要求 |
|---|---:|---|
| `buff_effects` | 0 | reviewed、fresh、非零 |
| `item_properties` | 0 | reviewed、fresh、非零 |
| `status_values` | 0 | reviewed、fresh、非零 |
| `loot_entries` | 0 | reviewed、fresh、非零 |
| `harvest_rules` | 0 | reviewed、fresh、非零 |
| `mission_rewards` | 0 | reviewed、fresh、非零 |

零行保持显式 coverage gap；不会用 legacy-only 行或 fingerprint 填充来让
门禁变绿。

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

- 已发布基线 exact functions：20；
- 已发布基线 confirmed BP-native edges：0；
- 已发布基线 confirmed field accesses：0；
- 已发布基线 ambiguous/candidate BP-native links：132。

工作树验证快照 `20260727T205302-3e842d2336d2` 已生成恰好 1 条
`CONFIRMED/HIGH` BP-native link：

- Blueprint：
  `/Game/Genesis/Dinos/Shapeshifter/Shapeshifter_Small/Shapeshifter_Small_Character_BP.Shapeshifter_Small_Character_BP`；
- function：`AddItemObjectEx`；
- native target：`UPrimalInventoryComponent::AddItemObjectEx`，
  RVA `0x1390DB0`；
- resolution：`verified_callsite`；
- Blueprint 与 Native source revision 均为 `FRESH`。

该验证快照已通过 qualified symbol、RVA、真实 callsite、signature、双方
Evidence 与 freshness 校验；旧 name-only 行仍保持 `CANDIDATE/LOW`。
但它位于 `.tmp/stage8-native-confirmation/snapshot-final2`，不是本机规范
`knowledge_base/vnext/current`，而且其密封门禁仍为
`shadow / legacy`、39 项失败。因此这里记录 Stage 8 的验证完成，不把
已发布基线 confirmed 数从 0 改写为 1，也不宣称已完成 cutover。

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
hash，不虚构 per-set entries。旧 legacy-v1 基线也没有 source-manifest
binding，必须先 full rebuild；之后未变输入才会 cache hit。

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

最终验收应在所有并行实现合并后重新运行，而不沿用旧报告中的历史测试总数：

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
4. 已发布基线 confirmed BP-native link 0；工作树验证快照已有 1 条，但
   尚未进入规范 current；
5. 六个 reviewed core projections 尚未非零；
6. 单资产生产选择性 ingest/backend/publisher 尚未闭合。

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
