# ARK KB vNext 当前状态

> 最后核验：2026-07-31（Asia/Shanghai）。本文从本机密封 Snapshot、
> `current.json`、只读 Source Manifest 扫描与运行时 health 提取可审查状态；
> 本机数据库和原始 Evidence 不进入 Git。

## Snapshot identity

| 项目 | 值 |
|---|---|
| Build ID | `20260730T172442-19e56659d331` |
| Source SHA-256 | `19e56659d331489e1f82881d1a0c7dae3c51d73ba5397bc3601ccb8404054293` |
| Discovery SHA-256 | `028a12c429903466aa52f99c5e63c8d90813585b9d5c6a8c303fbb93a9d6a31f` |
| Snapshot manifest SHA-256 | `6c957681a6463c9e5d5e83ada999cf1d5cb24a64d53af6516eb0399c1fd29136` |
| Current pointer SHA-256 | `de74be48111cba8d3a1241b22cf94dc0e28945e32d084419163235383c6c556f` |
| Source-manifest fingerprint | `fbb474d8ca1073dee5305cbe0247fdbec7fa4cbea97e882cb2cabc438b8750ca` |
| semanticProducerContract | `66a8c3d93c9cce5485e0e82fdbd8092340e0db1c225e707ee7a97b0aab4d0eab` |
| Sealed quality report SHA-256 | `84a6cd1dae885d7efe00e6174be72207e27a9a4681070d266707437c3a6f700b` |
| Previous build | `20260730T162735-b46eb9304da3` |
| Previous manifest SHA-256 | `9ae250a4dba1c01cd980cb8acee82831dd2516af822541e59afb96eb585a9e3c` |
| Blueprint Evidence in Snapshot | `234` |
| Snapshot count | `3`（约 `12.0 GiB`） |
| Layout | `immutable-v2` |

根 `knowledge_base/vnext/current.json` 只包含 `buildId` 和
`snapshotRelativePath`，并精确指向
`snapshots/20260730T172442-19e56659d331/manifest.json`。当前 manifest 对直接
父 Snapshot 的 Build ID 与 manifest SHA-256 绑定均已复核。

密封报告身份也由 manifest 绑定：

| 报告 | SHA-256 |
|---|---|
| Quality report | `84a6cd1dae885d7efe00e6174be72207e27a9a4681070d266707437c3a6f700b` |
| Query benchmark | `69b97a2e15a04d74e61088e2d1010be1f7f182db457009bdc7e15ca3eb896507` |
| Query case results | `6688956c330d4b66fefc894481fe2a94d44d8a4472dbc7e414aa67f8d7e01c87` |
| Failure matrix | `2cab12379bdffb3a63a4b33e95d25ca77c7bae6572db21945db26d9be5dad465` |
| Diagnostics binding | `0d67112ad0a4c0b8deab4c0879ff0010dbd1184efa30343b51262ecc0b97deb8` |

`sealedInSnapshotManifest=true`，quality report 与 manifest 都记录
`cutoverEligible=false`。

## Runtime and cutover

```json
{
  "available": true,
  "status": "READY",
  "freshness": "FRESH",
  "buildId": "20260730T172442-19e56659d331",
  "mode": "shadow",
  "defaultQuerySource": "legacy",
  "activeStaleSources": 0,
  "cutoverEligible": false
}
```

- 密封质量门：`60/75` passed，`15` failed；
- Burn-in：`MISSING / BURN_IN_ATTESTATION_MISSING`；
- query human Gold：`5/130`；
- registration Owner→Target Gold：`0/100`；
- role Gold：`0/300`。

`READY / FRESH` 只证明当前 shadow Snapshot 可读取且来源未过期，不表示已经达到
生产 Gold、连续 burn-in 或 cutover 条件。legacy 仍是默认查询来源。

## Current content

| 指标 | 当前值 |
|---|---:|
| Entities / assets | 577,579 |
| Catalog nodes / edges | 1,197,285 / 3,442,470 |
| Classes / closure rows | 26,495 / 92,033 |
| Roles | 1,091,270 |
| Typed registrations / materialized edges | 145 / 26 |
| Declared / effective facts | 10,587 / 102,329 |
| Semantic facts | 136 |
| Legacy lineage rows | 298,003 |
| Invalidation dependencies | 1,199,519 |
| Exact native functions | 20 |
| Blueprint-native candidate / confirmed links | 713 / 1 |
| Blueprint Evidence entries | 234 |

六个领域投影均通过 artifact/Core binding：

| Projection | Rows | Complete | Partial | Validation |
|---|---:|---:|---:|---|
| `buff_effects` | 46 | 46 | 0 | `VALID` |
| `item_properties` | 28 | 28 | 0 | `VALID` |
| `status_values` | 13 | 13 | 0 | `VALID` |
| `loot_entries` | 28 | 0 | 28 | `VALID` |
| `harvest_rules` | 10 | 0 | 10 | `VALID` |
| `mission_rewards` | 11 | 9 | 2 | `VALID` |

`loot_entries` 与 `harvest_rules` 的非零行仍是 partial；非零不等于语义完整。
manifest 中的 10 个数据库/投影均为 `integrity=ok`、FK violations `0`。

## 增量验证状态

生产 backend 代码现在覆盖 `FACT × 2`、`EFFECTIVE_ENTITY × 1`、
`ROLE_ENTITY × proven scope`、`DOMAIN_ENTITY × proven scope`、
`PROJECTION × 6` 与 `QUERY_SNAPSHOT × 1`。Role 会把全局 percentile 的精确
受影响闭包写入 content-addressed proof；Domain 只拥有 class ancestry 与 typed
registration 行；每个 Projection 任务只构建和原子替换一个 SQLite。

隔离 production-shaped 场景的本轮结果是：

```text
attempted=12
SUCCEEDED=12
BLOCKED_GAP=0
FAILED=0
remaining_pending=0
remaining_running=0
worker.drained=true
```

这不是 live E4 证据。writer lock 下的实际只读复核得到 base Source Manifest
fingerprint `fbb474d8ca1073dee5305cbe0247fdbec7fa4cbea97e882cb2cabc438b8750ca`；
candidate fingerprint 为
`7f0f79cfe270dbb7ed21a146f5854b3da6aa80c1051df2bf2e1324c1d064a327`，
与 base 不同；Source Diff 为 14 个新增和 10 个变更，
包括多个 Blueprint Evidence 与 semantic/native/legacy 输入变化。生产 capability
check 因此返回 `NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED`，没有进入 quarantine、
ingest、worker、narrow gates 或 publisher。

默认路径的工程实现已经不再停留在 unavailable hook：最终 worker 输出会先重封为
新 immutable candidate，再构造并复核 v3 delta receipt，计算固定 11 项 narrow
gates，把 canonical report 密封进 manifest，最后才允许同卷 rename 与 exact
pointer CAS。隔离 fixture 已通过 11/11 Gate 和真实临时目录 CAS/独立回读；这些是
测试事实，不是上述 live 输入的执行结果，也不产生 production authority。

本轮仍明确保持：

```text
productionAuthority=false
published=false
e4Scenario2Complete=false
cutoverEligible=false
```

- current Snapshot 仍有 `234` 个 Blueprint Evidence；
- current build、manifest 与 pointer SHA-256 均未变化；
- Snapshot 仍为 3 个，没有生成或发布增量 Snapshot；
- `.incremental-staging` 与 `.build` 在审计时为空；
- `mode=shadow`、`defaultQuerySource=legacy`、`cutoverEligible=false`。

## Verification evidence

- `VNextKnowledgeService.health()` 返回同一 Build 的
  `READY / FRESH / shadow / legacy`；
- current → previous manifest hash binding 完全匹配；
- manifest 绑定的 Source、Discovery、quality report 和数据库身份均已复核；
- live Source Diff 已复核为非选择性变化并在 staging 前 fail closed；
- 旧 Snapshot 和 legacy 根目录 fallback 不参与 current 读取路径。

公共 Git clone 不包含这些本机数据库或 Evidence。文档身份测试会在本机
Snapshot 可用时校验本报告、完成报告、覆盖报告与 `current.json` 指向的
manifest 一致。
