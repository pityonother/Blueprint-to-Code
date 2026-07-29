# ARK 背景知识库范围发现：实施计划

## 目标

本轮不重构最终知识库。先实现一个可断点、可增量运行的发现工具，从本机
ARK DevKit 元数据、Blueprint Evidence Store、现有知识数据库和 Ghidra
Native Evidence Store 生成脱敏调查包：

```text
knowledge_base/discovery_bundle.zip
```

调查包用于让 GPT Pro 判断知识分层、实体索引深度、预计算边界、统一证据图谱、
数据库结构、更新失效策略和查询时的解析器调用边界。

## 架构边界

- 工作状态与可上传结果分离。断点、源目录和本机状态只写入被忽略的工作数据库。
- 资产身份以 DevKit 内 `AssetRegistryHelpers.get_asset_registry()` 导出的
  Object Path、AssetClass、GeneratedClass、ParentClass、NativeParentClass、
  BlueprintType、ImplementedInterfaces 和五类 package dependency 为第一来源。
- 文件系统扫描只确认 `.uasset/.umap` 的存在、大小、修改时间和 companion
  状态；现有序列化 Import/Export 解析作为 Registry 缺失时的第二来源，
  文件名/目录不得覆盖真实 Registry 结果。
- Blueprint Evidence 只汇总 revision、覆盖、诊断、引用和计数。
- Native Evidence 只汇总已验证身份、recipe、target、函数索引和 gap；不导出
  `decompiled_c`、原始 payload、DLL、PDB 或 Ghidra workspace。
- 所有跨 Blueprint/Native 的自动匹配都只标为候选，不提升为已确认事实。
- ZIP 采用允许清单，并在生成后重新扫描成员路径、文本、SQLite 字段和敏感标记。

## 任务 1：断点与增量底座

**验收标准**

- DevKit-side Registry exporter 以 JSONL 批次写入并保存 asset/package cursor、
  byte offset、snapshot signature 和五类 dependency 计数。
- DevKit 扫描按稳定相对路径顺序运行，并按批次保存 cursor。
- 中断后可从 cursor 继续；完整运行后能识别新增、变化和删除。
- 工作状态允许保留绝对源路径，但不会进入上传包。

**验证**

- 临时 DevKit fixture 上验证中断、继续和二次增量运行。
- 检查完成后的实体数量和 fingerprint 变化。

## 任务 2：证据来源发现

**验收标准**

- 每个 Blueprint Evidence Store 以 revision/source fingerprint 增量处理。
- 每个 binary + recipe 只选择最新完整 Native Evidence Store。
- 汇总已有语义数据库的表规模和读入覆盖，但明确标注其快照时间。
- 不读取或输出 Native `decompiled_c`。

**验证**

- fixture 中重复 Native store 正确选出最新一份。
- 未改变的 Evidence Store 在二次运行中跳过。
- 诊断、coverage、reference、target、function 和 gap 计数可复算。

## 任务 3：范围与图谱建议

**验收标准**

- 输出规范要求的 15 张核心表、可解释中心性原始特征，以及 provisional
  Tier 0～Tier 4 建议；不执行不可逆分类或迁移。
- 输出 Blueprint reference、Blueprint → Native 候选桥接和 unresolved/ambiguous
  状态，不伪造确认边。
- 输出至少 30 条跨领域查询语料与包含系统中心、领域普通实体、低中心叶子、
  名称误导、完整证据和高缺口的有界代表性样例。
- `discovery_report.md` 逐项回答规格中的 14 个范围问题。

**验证**

- JSON schema 标识、计数和 SQLite 行数一致。
- 所有建议都引用本轮实际发现规模，不依赖硬编码旧快照。

## 任务 4：脱敏打包

**验收标准**

- ZIP 只包含 README、manifest、schema、report、CSV、query JSONL、SQLite、
  有界样例和校验和，并使用单一 `discovery_bundle/` 根目录。
- ZIP 不含 ARK 原始资产、DLL/PDB、Ghidra workspace、pseudo-C、绝对本机路径、
  用户名、token 或 secret。
- 每个成员有 SHA-256，生成后重新打开 ZIP 并验证。

**验证**

- 自动隐私审计通过。
- `SHA256SUMS.txt` 与 ZIP 中实际内容一致。
- `knowledge_base/discovery_bundle.zip` 可独立解压和查询。

## 最终检查点

- 发现工具单测与全量仓库测试通过。
- 对本机真实 DevKit、227 个 Blueprint Evidence Store 和可用 Native Store
  完整运行。
- GPT Pro 视察文档同时说明实际 knowledge 发现和 Codex 已完成的工程工作。
- 源码与文档提交 GitHub；本机调查 ZIP 作为聊天附件交付，不提交 proprietary
  派生全集。
