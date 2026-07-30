# ARK KB vNext 当前状态

> 最后核验：2026-07-30（Asia/Shanghai）。本文是从本机密封 snapshot
> manifest 与只读运行时 health 结果提取的可审查状态，不替代未提交的数据库。

## Snapshot identity

| 项目 | 值 |
|---|---|
| Build ID | `20260730T051513-345699a11f21` |
| Source SHA-256 | `345699a11f21831a5abff9ad86e8417dc8143c874810cc277105477ea1b3910e` |
| Discovery SHA-256 | `028a12c429903466aa52f99c5e63c8d90813585b9d5c6a8c303fbb93a9d6a31f` |
| Snapshot manifest SHA-256 | `61d84f0bf1966ce365e997747914781099167ba8bb6e927f0f63330162f10935` |
| Current pointer SHA-256 | `455e28cadba18fe3ca384c681beb7f1f68d65b71986000966663abf47c219b0b` |
| Previous build | `20260729T115548-1a203b594bb6` |
| Previous manifest SHA-256 | `e82a50dd34b93f2649f3f1f7627c0b15f3b110c741939765fadbbdde3ea1c0da` |
| Layout | `immutable-v2` |

根 `knowledge_base/vnext/current.json` 只包含 `buildId` 和
`snapshotRelativePath`，并精确指向
`snapshots/20260730T051513-345699a11f21/manifest.json`。当前 manifest 对直接
父 snapshot 的 Build ID 与 manifest SHA-256 绑定均已复核。

## Runtime and cutover

```json
{
  "available": true,
  "status": "READY",
  "freshness": "FRESH",
  "buildId": "20260730T051513-345699a11f21",
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

`READY / FRESH` 只证明当前 shadow snapshot 可读取且来源未过期，不表示已经达到
生产 Gold、三轮 burn-in 或 cutover 条件。legacy 仍是默认查询来源。

## Current content

| 指标 | 当前值 |
|---|---:|
| Entities / assets | 577,579 |
| Catalog edges | 3,442,470 |
| Classes / closure rows | 26,495 / 92,033 |
| Roles | 1,091,270 |
| Typed registrations / materialized edges | 145 / 26 |
| Declared / effective facts | 10,587 / 102,329 |
| Semantic facts | 136 |
| Legacy lineage rows | 298,003 |
| Invalidation dependencies | 1,199,519 |
| Exact native functions | 20 |
| Blueprint-native candidate / confirmed links | 713 / 1 |

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

## Verification evidence

- 当前与直接父 snapshot 共 20 个 SQLite 数据库/投影逐库执行
  `PRAGMA integrity_check`，全部返回 `ok`；
- current → previous manifest hash binding 完全匹配；
- 删除 legacy-v1 根目录数据库后，`VNextKnowledgeService.health()` 仍返回同一
  Build 的 `READY / FRESH / shadow / legacy`；
- 旧 snapshot 和根目录 fallback 不参与当前读取路径。

公共 Git clone 不包含这些本机数据库。文档身份测试会在本机 snapshot 可用时校验
本报告、完成报告、覆盖报告与 `current.json` 指向的 manifest 一致。
