# ARK Knowledge Base vNext 覆盖率与切换报告

## 当前结论

**keep legacy / shadow**

Stage 8/9 已把 typed registration、真实角色信号、独立 gold 计分、不可变
快照和发布前门禁做成 fail-closed 实现，但当前独立语义证据仍不满足切换
条件。旧库不能删除，vNext 不能改为默认。

本文中的统计来自本机规范 `current.json` 指向的真实全量 immutable-v2
快照。质量报告已在 pointer 可见前密封；发布后的外部复核得到相同
`58/75` 结果，但不能改变 current。

## 已发布快照身份

| 项目 | 值 |
|---|---:|
| Build ID | `20260727T222549-a2d56bd7fed8` |
| Source SHA-256 | `a2d56bd7fed88edd1098915ea3723da0fdef0b0a263567b56f46bae074f385cd` |
| Discovery SHA-256 | `028a12c429903466aa52f99c5e63c8d90813585b9d5c6a8c303fbb93a9d6a31f` |
| Discovery bytes | 3,816,792,064 |
| Snapshot schema | `ark-kb-vnext-snapshot/v1` |
| Published layout | `immutable-v2`：`current.json -> snapshots/<buildId>` |
| Sealed quality | 58 / 75 passed，17 failed |
| Runtime health | `activeStaleSources=0` |
| Published cutover | `mode=shadow`, `defaultQuerySource=legacy` |

## 已发布快照内容

| 指标 | 当前实际 |
|---|---:|
| Entities / assets | 577,579 |
| Catalog edges | 3,442,470 |
| Classes / closure rows | 26,495 / 92,033 |
| Roles | 1,091,270 |
| Typed registration rows / materialized edges | 145 / 26 |
| Declared / effective facts | 10,587 / 102,329 |
| Semantic facts | 136 |
| Legacy lineage rows | 298,003 |
| Invalidation dependencies | 1,199,519 |
| Exact native functions | 20 |
| Blueprint-native candidate / confirmed links | 713 / 1 |

六个领域投影均已生成并通过 artifact/Core binding：

| Projection | Rows | Complete | Partial | Validation |
|---|---:|---:|---:|---|
| `buff_effects` | 46 | 46 | 0 | `VALID` |
| `item_properties` | 28 | 28 | 0 | `VALID` |
| `status_values` | 13 | 13 | 0 | `VALID` |
| `loot_entries` | 28 | 0 | 28 | `VALID` |
| `harvest_rules` | 10 | 0 | 10 | `VALID` |
| `mission_rewards` | 11 | 9 | 2 | `VALID` |

`loot_entries` 和 `harvest_rules` 的行仍是 partial；非零不等于完整，也不把
legacy-only 或 fingerprint 值提升为可用语义。

## 独立 gold 现状

固定 query corpus 共有 130 条，但只有 5 条标为 `HUMAN_REVIEWED`；其余
125 条是冻结的 `FIXTURE_EXACT` protocol/evidence cases。后者可以验证
路由、gap 和 fail-closed 行为，不能被计作人工语义正确性。

| 门禁 | 当前可计数证据 | 门槛 | 状态 |
|---|---:|---:|---|
| Query human gold | 5 / 130 cases | ≥120 reviewed | 阻断 |
| Registration Owner→Target gold | 0 / 100 | ≥100 reviewed | 阻断 |
| Role gold | 0 / 300 | ≥300，precision/recall ≥95% | 阻断 |
| Blueprint-native confirmed link | 1 confirmed / 1 fully bound | ≥1 且双侧 Evidence | 通过 |

Registration 的 property-name unit fixture 不再计作真实关系 gold。
`kb_registration_gold_set.json` 当前明确记录
`relationshipGoldStatus=NOT_AVAILABLE` 和空 `relationshipCases`。门禁会
重算 edge type，并核对 Owner、Target、materialized edge、双方 source
revision 和 Evidence；不会相信 fixture 中的布尔自证。

Role gold 文件当前不存在，因此计数为 0。即使 classifier unit cases 有
`correct=true`，也不能代替 300 个真实 canonical entity 的两轮独立复核和
分歧 adjudication。

规范快照已得到恰好 1 条
`CONFIRMED/HIGH` link：Shapeshifter Small Blueprint 的
`AddItemObjectEx` 指向
`UPrimalInventoryComponent::AddItemObjectEx`（RVA `0x1390DB0`），
resolution 为 `verified_callsite`，双方 source revision 均为 `FRESH`；
双方 fingerprint 都是规范 SHA-256，Blueprint revision 为
`uasset-graph-reader-evidence-v3 / ark.blueprint.evidence.v2`。其余 713
条保持 candidate。Native 两门均已通过，但整体仍因其他 17 门保持
`shadow / legacy`。

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
- 并发 reader 不观察到混合 build ID；
- 发布前 checkpoint、`journal_mode=DELETE` 与 WAL/SHM sidecar 拒绝；
- `runtimeHealth` 与 Core metadata 绑定，活动陈旧来源不能进入
  `ready/vnext`。

当前快照的 10 个数据库/投影均通过 integrity、FK 与绑定验证；没有
WAL/SHM sidecar。`storage.integrity` 通过，Core/Discovery 大小比为
`0.6804`。服务初始化约 `0.043s`，轻量 `health()` 约 `0.024s`，返回
`READY / FRESH`；首次完整摘要绑定搜索约 `2.79s`，同服务缓存后约
`0.13s`。

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
记录汇总 hash；不声称 per-set 粒度。当前快照已经绑定 source manifest；
相同输入 update 实测返回 `cacheHit=true`、`published=false`、
`fullRebuildPerformed=false`，current 与快照目录数均未改变。

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

才允许把 vNext 设为默认。当前 17 个失败门按原因归组为：

1. role 独立 gold 仍为 0/300；
2. registration 真实 Owner→Target gold 仍为 0/100，因此 count、
   precision/recall、classification、Owner/Target resolution、
   materialization、Evidence 和 lineage 共 10 门阻断；
3. query human gold 仅 5/130，corpus 尚未 ready；
4. query protocol compliance `91.54%`、expected-gap match `76.60%`、
   wrong-answer rate `9.23%`，未达到 fail-closed 门槛；
5. single-entity P95 为 `358.929ms`，高于 `<250ms` 门槛。

生产选择性 ingest/backend/publisher 仍是工程能力边界，但当前 sealed
质量报告中的 native、projection、storage 和 stale-leak 门已经通过。

所以当前必须保持：

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy"
}
```
