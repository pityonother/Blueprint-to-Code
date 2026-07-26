# ADR-003：项目级 Hybrid Evidence Graph

- 状态：Accepted
- 日期：2026-07-27
- 适用版本：0.2.0+

## 背景

Blueprint evidence 按资产 revision 隔离，Native evidence 按 binary + recipe
隔离。把跨源边写回某个 Blueprint 数据库会让一个 native build 更新多个资产
数据库，也不利于跨资产查询；只在 Markdown 中写链接又无法检测歧义或失效。

## 决策

采用项目级 Hybrid Evidence Graph：

```text
analysis/evidence_graph/hybrid_edges.json
analysis/evidence_graph/hybrid_evidence.sqlite
```

- JSON 是可移交、可审计的权威交换源。
- SQLite 只是在 JSON SHA-256 绑定下生成的查询索引；哈希不匹配时拒绝查询。
- 两者均为可重建的本机分析产物并继续被 Git 忽略；仓库提交 schema、实现和
  synthetic fixtures。
- 每条边保留 source/target Evidence ID、relation、状态、解析依据、候选数量、
  native evidence set、输入 fingerprints 和 gaps。

首版关系为：

```text
bp://.../call/... --CALLS_NATIVE--> native://...
bp://.../node/... --REFERENCES_NATIVE--> native://...
native://... --CALLED_BY_BLUEPRINT--> bp://...
```

## 解析和失效

解析顺序是 function member name、显式 owner/class、signature hints 与
PDB-qualified name。首版不会猜测未提供的 parent/inheritance 路径；若调用只
能通过父类解析，输入必须先补充已验证 owner 或显式继承映射。只有一个满足约束
的候选才可 `CONFIRMED`。多个候选必须为 `AMBIGUOUS` 并保存全部候选；没有
候选必须按原因区分 `SOURCE_NOT_AVAILABLE` 与 `NOT_RECOVERED`。不得只按短
函数名猜测。

边同时绑定 Blueprint source fingerprint 与 native evidence set/recipe/binary
fingerprint。任一输入变化后，旧边为 stale，不能继续作为 confirmed claim
依据。

## 查询边界

Hybrid Context Pack 只取回答当前问题所需的小窗口，并明确分区：

1. Blueprint confirmed facts；
2. Native confirmed facts；
3. Resolved cross-source edges；
4. Assumptions；
5. Runtime-only gaps；
6. Stale/provenance warnings。

预算、分页和 omitted 计数适用于完整响应；默认不包含整份反编译文本。

## 影响

- 保留现有 per-asset Blueprint revision，不需要迁移其数据库结构。
- 一个 native build 可关联多个资产，并能从 native 反查 Blueprint 调用方。
- SQLite 可安全删除并由权威 JSON 重建。
- claim validator 可以沿边检查 source fingerprint 和 native provenance。

## 未选择的方案

- 把 cross-edge 写入每个 Blueprint DB：更新与跨资产查询成本高。
- 只使用 SQLite：会产生第二个难以审计的权威源。
- 运行查询时临时按名字连接：无法保留歧义、依据和 stale 状态。
