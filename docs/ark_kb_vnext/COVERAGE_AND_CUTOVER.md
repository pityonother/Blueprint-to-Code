# ARK Knowledge Base vNext 覆盖率与切换报告

## 当前结论

**keep legacy / shadow**

Stage 8/9 已把 typed registration、真实角色信号、独立 gold 计分、不可变
快照和发布前门禁做成 fail-closed 实现，但当前独立语义证据仍不满足切换
条件。旧库不能删除，vNext 不能改为默认。

本文中的快照统计来自最后一个已经发布到本机默认目录的基线。该基线仍是
兼容读取的 legacy-v1 布局；新的构建器会发布
`current.json -> snapshots/<buildId>` 的 immutable-v2 布局。没有重新构建
并密封门禁的新 snapshot 之前，不能把代码层修复解释为该旧基线已经通过
新门禁。

## 已发布基线身份

| 项目 | 值 |
|---|---:|
| Build ID | `20260727T035514+0000-9f106a091815` |
| Discovery SHA-256 | `9f106a091815dd88aa729d28140db728e0f1b37dbeebf2fd5f2182492ef4ea50` |
| Discovery bytes | 3,816,177,664 |
| Snapshot schema | `ark-kb-vnext-snapshot/v1` |
| Published layout | `legacy-v1`（兼容读取；待下一次完整构建迁移） |
| Published cutover | `mode=shadow`, `defaultQuerySource=legacy` |

## 已发布基线内容

下表用于标识仍在本机 current 中的实际数据，不是新门禁通过声明。

| 指标 | 基线实际 |
|---|---:|
| Entities / assets | 577,579 |
| Catalog edges | 3,441,879 |
| Classes / closure rows | 26,495 / 92,248 |
| Roles | 1,091,275 |
| Typed registration rows / materialized edges | 135 / 28 |
| Declared / effective facts | 10,588 / 102,330 |
| Legacy lineage rows | 298,003 |
| Invalidation dependencies | 593,234 |
| Exact native functions | 20 |
| Blueprint-native candidate / confirmed links | 132 / 0 |

基线的六个核心领域投影 `buff_effects`、`item_properties`、
`status_values`、`loot_entries`、`harvest_rules` 和
`mission_rewards` 均为 0 行。空投影表示当前没有满足 typed value、fresh
Evidence 和 lineage 要求的行，不能解释成该领域不存在。

## 独立 gold 现状

固定 query corpus 共有 130 条，但只有 5 条标为 `HUMAN_REVIEWED`；其余
125 条是冻结的 `FIXTURE_EXACT` protocol/evidence cases。后者可以验证
路由、gap 和 fail-closed 行为，不能被计作人工语义正确性。

| 门禁 | 当前可计数证据 | 门槛 | 状态 |
|---|---:|---:|---|
| Query human gold | 5 / 130 cases | ≥120 reviewed | 阻断 |
| Registration Owner→Target gold | 0 / 100 | ≥100 reviewed | 阻断 |
| Role gold | 0 / 300 | ≥300，precision/recall ≥95% | 阻断 |
| Blueprint-native confirmed link | 已发布基线 0；验证快照 1 | ≥1 且双侧 Evidence | 实现已验证，尚未进入规范 current |

Registration 的 property-name unit fixture 不再计作真实关系 gold。
`kb_registration_gold_set.json` 当前明确记录
`relationshipGoldStatus=NOT_AVAILABLE` 和空 `relationshipCases`。门禁会
重算 edge type，并核对 Owner、Target、materialized edge、双方 source
revision 和 Evidence；不会相信 fixture 中的布尔自证。

Role gold 文件当前不存在，因此计数为 0。即使 classifier unit cases 有
`correct=true`，也不能代替 300 个真实 canonical entity 的两轮独立复核和
分歧 adjudication。

已发布快照的 Blueprint-native confirmed 数仍是 0。工作树验证快照
`20260727T205302-3e842d2336d2` 已得到恰好 1 条
`CONFIRMED/HIGH` link：Shapeshifter Small Blueprint 的
`AddItemObjectEx` 指向
`UPrimalInventoryComponent::AddItemObjectEx`（RVA `0x1390DB0`），
resolution 为 `verified_callsite`，双方 source revision 均为 `FRESH`；
旧 name-only 行没有升级。该快照不属于规范
`knowledge_base/vnext/current`，且自身仍密封为 `shadow / legacy`
（39 项门禁失败），所以本表继续按已发布基线 0 处理，不把验证结果误写成
已切换状态。

## Stage 8 实现覆盖

### Typed registration

- Core edge 不再统一写成 `REGISTERS`，而是保存具体的 system、
  mechanism 或 placement relation。
- Skin association 只记 `REFERENCES_OBJECT`，不推断成 `GRANTS_ITEM`。
- candidate、legacy、缺 Evidence 或 source revision 不新鲜的关系不能闭合
 查询。
- relationship gold 不足时所有 precision/recall/materialization 门禁
  fail closed。

### Role signals

- query demand 只接受人工/实证 benchmark demand；
- cross-domain、formula、native、component、animation、curve、
  collision、material 和 world placement 从实际表聚合；
- 缺 source revision 或不新鲜的信号不计为确认；
- percentile 按 class ancestry 推导的 semantic category 计算。

这说明信号已接线，不说明角色总体精度已经得到独立证明；因此 role gold
仍是切换阻断项。

## Stage 9 存储与门禁覆盖

新快照的规范布局为：

```text
current.json
snapshots/<buildId>/
  catalog.sqlite
  core.sqlite
  search.sqlite
  cache.sqlite
  domain_exports/
  reports/
  manifest.json
```

发布前质量报告及 SHA-256 会密封进同一个 immutable manifest，然后才原子
替换 `current.json`。原子性测试覆盖：

- current pointer 路径逃逸和多余字段拒绝；
- 已存在 immutable build 不可覆盖；
- pointer 切换前崩溃仍读旧 snapshot；
- 旧 SQLite 连接在新发布后继续可读；
- 并发 reader 不观察到混合 build ID。

对已经发布的 snapshot 运行门禁，只能写
`reports/<buildId>/quality_gates.json`、`query_benchmark.json` 和
`cutover_attestation.json`。这些外部报告不修改 current，不改 immutable
manifest，也不能把 default 切到 vNext；通过结果必须密封进一个新 snapshot
后再发布。

## 增量能力门禁

Queue worker 的任务状态、receipt、恢复和 fail-closed 行为已经实现，且定义
了 11 类 rebuild operation。但生产默认 backend 当前只接通
`CLASS_CLOSURE` 和 `EFFECTIVE_ENTITY`。

`scripts/update_ark_kb_vnext.py` 的生产默认路径会：

- 与 full builder 复用同一 source-manifest scanner；
- 在新 immutable snapshot 中原子绑定 fingerprint、10 项 semantic input、
  runtime 汇总、每个 capture revision 和每个已验证 Native evidence set；
- 对完成该绑定的新 full build，在输入未变化时返回 cache hit 且不 stage；
- 首次运行、source 删除、非选择性输入变化或缺少选择性能力时，在
  lock/staging/queue/publication 前返回稳定 gap；
- 设置 `fullRebuildRequired=true`；
- 不复制现有 snapshot，不修改 current，不发布部分构建。

Source-manifest fingerprint 明确排除顶层 `generatedAt`，不会仅因扫描时间
不同产生假变化。当前 runtime 没有可验证的 observation-set loader，因此只
记录汇总 hash；不声称 per-set 粒度。本文所列 legacy-v1 已发布基线尚无该
binding，它的第一次 update 仍会要求 full rebuild。

因此“单资产增量摄取到原子发布”仍未通过生产门禁。当前安全更新方式仍是
显式 `--full-snapshot`。不能把 unchanged cache hit 或 fail-fast 安全性描述
为增量发布已经完成。

## 切换规则

只有新 snapshot 在发布前生成并密封：

```json
{
  "qualityGates": {
    "cutoverEligible": true,
    "sealedInSnapshotManifest": true
  },
  "cutover": {
    "mode": "ready",
    "defaultQuerySource": "vnext"
  }
}
```

才允许把 vNext 设为默认。当前已知阻断至少包括：

1. query human gold 仅 5/130；
2. registration relationship gold 0/100；
3. role gold 0/300；
4. 已发布快照 BP-native confirmed 0；工作树验证快照已有 1 条，但尚未
   进入规范 current；
5. 六个核心领域投影尚无 reviewed nonzero coverage；
6. 生产选择性 ingest/backend/publisher 尚未闭合。

所以当前必须保持：

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy"
}
```
