# ARK Knowledge Base vNext 覆盖率与切换报告

## 当前结论

构建 `20260727T035514+0000-9f106a091815` 已成功发布并通过四库 integrity/FK 复核。质量门禁为 **23/26 通过**；三个关键证据门禁仍未满足，因此当前建议是：

```text
keep legacy default
+ run vNext in shadow/compare mode
+ do not delete legacy databases
```

manifest 已自动写入 `mode=shadow`、`defaultQuerySource=legacy`，不存在人工绕过。

## 快照身份

| 项目 | 值 |
|---|---:|
| Build ID | `20260727T035514+0000-9f106a091815` |
| Discovery SHA-256 | `9f106a091815dd88aa729d28140db728e0f1b37dbeebf2fd5f2182492ef4ea50` |
| Discovery bytes | 3,816,177,664 |
| Ontology | `ark-domains/v1\|ark-roles/v1\|ark-edge-types/v1\|ark-fact-types/v1` |
| Core schema | `ark-kb-core/v1` |
| Gate schema | `ark-kb-quality-gates/v1` |

## 存储与完整性

| 数据库 | Bytes | Discovery 比例 | Integrity | FK 违规 |
|---|---:|---:|---|---:|
| `catalog.sqlite` | 1,115,377,664 | 29.23% | ok | 0 |
| `core.sqlite` | 1,475,784,704 | 38.67% | ok | 0 |
| `search.sqlite` | 458,588,160 | 12.02% | ok | 0 |
| `cache.sqlite` | 28,672 | <0.01% | ok | 0 |

Core 明显小于 Discovery，且大型 2-hop 查询计划同时使用 `idx_edges_source` covering index。

## 核心覆盖

| 指标 | 实际 |
|---|---:|
| Entities / assets | 577,579 |
| Packages | 576,341 |
| Catalog edges | 3,441,879 |
| Classes / class edges / closure rows | 26,495 / 38,398 / 92,248 |
| Class gaps / cycles | 12,779 / 0 |
| DataAsset ancestry classified assets | 349 |
| Asset-class assignments | 601,893 |
| Roles / domain memberships | 1,091,275 / 2,335 |
| Typed registrations / materialized registration edges | 135 / 28 |
| Declared / effective facts | 10,588 / 102,330 |
| Fact Evidence | 10,588 |
| Legacy lineage rows / resolved entities | 298,003 / 184,295 |
| Invalidation dependencies | 593,234 |
| Native exact targets / confirmed functions | 20 / 20 |
| Blueprint-native candidate / confirmed links | 132 / 0 |

深度策略：

| 策略 | 数量 |
|---|---:|
| `INDEX_ONLY` | 552,231 |
| `ON_DEMAND` | 21,625 |
| `BLOCKED_UNKNOWN` | 1,372 |
| `DEEP` | 1,216 |
| `STRUCTURE` | 970 |
| `SEMANTIC` | 165 |

## 120 条查询基准

| 层级 | 数量 |
|---|---:|
| 简单事实 | 30 |
| 跨资产关系 | 30 |
| 继承 / effective default | 20 |
| 地图 / 注册 | 15 |
| Native 边界 | 15 |
| 运行时待验证 | 10 |

12 个主要领域各有 10 条，另外包含 8 类负例：近似名称、热门 Texture、confirmed empty、stale Evidence、父类变化、native 重载、map namespace、叶子覆盖公共父规则。

| 查询结果 | 实际 | 门槛 | 结果 |
|---|---:|---:|---|
| 完整或明确受限 DB-first | 120/120 = 100% | ≥70% | 通过 |
| 简单查询 DB-only | 29/30 = 96.67% | ≥90% | 通过 |
| 无 gap/probe 的静默未解 | 0 | 0 | 通过 |
| 单实体 p50 / p95 | 0.054 / 0.341 ms | p95 <250 ms | 通过 |
| 2-hop p95 | 0.016 ms | <800 ms | 通过 |
| 最大 Context Pack | 440 tokens | ≤2,000 | 通过 |

`DB_ONLY_COMPLETE=64`，`EVIDENCE_REQUIRED=56`。后者仍算“明确受限”只因为它同时返回具体 gap 和定向 probe，不代表事实已经确认。

## 已通过的关键门禁

- Blueprint `asset_class_path` 可用率 100%。
- 所有角色记录都有可解释 reasons；表现资产误升为 `DEEP/SEMANTIC` 为 0%。
- 注册 fixture 精度/召回均为 100%，135/135 实际注册的 Owner/Target/Property/Evidence 完整。
- 20/20 exact native gold targets 解析成功。
- 10,588/10,588 当前事实有 source revision 和 Evidence。
- UNKNOWN 未被写成零；declared/effective 无混淆；canonical fact 无重复。
- 选择性失效依赖覆盖 role、domain、fact、effective、registration 与 native。
- 四库 integrity 为 `ok`，外键违规为 0。
- 质量报告不含本机绝对路径。

## 未通过：阻止切换的三项

### 1. Deep/Semantic 类链闭合率

- 实际：589/1,381 = **42.65%**
- 门槛：≥98%
- 影响：792 个深层/语义实体仍缺完整 parent/native root 证明。
- 下一步：按 `class_gaps` 的 `NATIVE_ROOT_NOT_REACHED`、`MULTIPLE_PARENT_CANDIDATES` 分组补 Registry/资产导出，不应靠名称推断闭合。

### 2. 独立角色 gold set

- 实际：0 个独立人工/实证复核资产。
- 门槛：≥300，关键角色精度 ≥95%。
- 影响：现有 classifier unit fixture 能证明规则行为，但不能证明真实资产总体精度。
- 下一步：从六类角色/反例按分层抽样导出至少 300 个 canonical entities，由独立复核者标记；不能用 classifier 自己生成标签再给自己评分。

### 3. Blueprint-native 确认边

- 实际：132 个候选，0 个确认。
- 门槛：确认边精度 100%，且至少有一条可审计确认边。
- 影响：native 函数 gold target 虽为 20/20，但尚未形成 Blueprint graph Evidence → exact native function 的完整绑定。
- 下一步：对候选逐条补 qualified symbol/RVA、Blueprint callsite 和 matching recipe evidence；同名重载继续保持候选。

## 切换规则

只有 `quality_gates.json.summary.cutoverEligible=true` 才能将 manifest 改为：

```json
{
  "mode": "ready",
  "defaultQuerySource": "vnext"
}
```

当前自动结果为：

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy",
  "reason": "3 critical quality gates remain open"
}
```

门禁输出位于本机生成目录 `knowledge_base/vnext/reports/`；该目录包含由 proprietary 派生的大型快照指标，不提交 Git。
