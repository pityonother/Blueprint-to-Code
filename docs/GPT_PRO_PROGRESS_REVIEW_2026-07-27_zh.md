# ARK Knowledge Discovery 当前完成情况（供 GPT Pro 视察）

## 视察目的

这份文档只用于让 GPT Pro 检查 Codex 当前完成的工程工作、已经发现的知识、证据强度和剩余盲区，并据此给 Codex 下一阶段的方向与优先级。它不是交接文档，也不是要求 GPT Pro 接管实现。

本轮严格停留在“范围发现”阶段：没有重构或迁移最终知识库，没有把启发式结果伪装成已确认事实，也没有把 ARK 原始包、二进制、PDB、Ghidra 工程、完整反编译文本、本机绝对路径或秘密写入产物。

## 从 GitHub 获取视察 ZIP（Git LFS）

`knowledge_base/discovery_bundle.zip` 已通过 Git LFS 托管在公开仓库
`https://github.com/pityonother/Blueprint-to-Code` 的
`codex/fix-partner-devkit-root` 分支。它只用于让 GPT Pro 视察本轮发现结果，不是项目交接包，也不要求 GPT Pro 接管或重写实现。

首次获取：

```text
git lfs install
git clone --branch codex/fix-partner-devkit-root --single-branch https://github.com/pityonother/Blueprint-to-Code.git
cd Blueprint-to-Code
git lfs pull --include="knowledge_base/discovery_bundle.zip"
```

已有该分支的本地仓库：

```text
git switch codex/fix-partner-devkit-root
git pull --ff-only origin codex/fix-partner-devkit-root
git lfs pull --include="knowledge_base/discovery_bundle.zip"
```

拉取后应验证文件身份：

```powershell
Get-FileHash -Algorithm SHA256 knowledge_base\discovery_bundle.zip
```

预期 SHA-256：
`7eae98300ea5c1665c50222cc888580be8349aac1b92e5f8ee7f3713cae2292d`。
如果没有安装 Git LFS，Git 只能取得很小的 pointer 文件，不能取得完整 ZIP。

## Codex 已完成的工程工作

1. 实现了可断点、可增量运行的发现管线：
   - `scripts/devkit_exporters/export_kb_registry_snapshot.py`
   - `scripts/export_kb_discovery_bundle.py`
   - `scripts/blueprint_translator/kb_discovery.py`
   - `tests/test_knowledge_discovery_bundle.py`
2. 建立 Asset Registry v2 快照协议：
   - 每一代 Registry 导出不可变；
   - manifest 原子发布；
   - checkpoint 同时绑定输入清单、提取器源码和 producer SHA-256；
   - Registry 或提取器变化后会使旧 checkpoint 失效。
3. 把以下证据统一写入一个调查数据库：
   - DevKit 文件清单；
   - Asset Registry 资产、类和依赖；
   - 序列化包头中的硬引用与软对象路径；
   - 已有 Blueprint Evidence Store；
   - 已有领域知识库；
   - 当前 DLL/PDB 与 Ghidra 边界证据。
4. 为缺口建立显式状态，而不是填空猜测：
   - `UNKNOWN`
   - `AMBIGUOUS`
   - `NOT_RECOVERED`
   - `NOT_MEASURED`
   - `SOURCE_NOT_AVAILABLE`
   - `STALE`
5. 生成了：
   - 20 张结构化调查表；
   - 38 个代表性查询问题；
   - 102 个可复核样本；
   - 14 个规格问题的发现报告；
   - 带逐文件 SHA-256、隐私审计和 ZIP64 校验的可上传 ZIP。
6. 在完整 DevKit 实跑中发现并修正了四类真实问题：
   - 同一 Registry package 内多个 asset 被覆盖；
   - `Function_*` 等非 package 标识被错误当成 package 依赖；
   - checkpoint 未绑定实际提取器来源；
   - 576K 级资产查询缺少 package/object path 索引而显著变慢。

## 已发现的知识

### 1. 全量目录规模与资产形态

- Asset Registry：576,203 个 asset，574,969 个 package。
- DevKit 文件系统清单：505,169 个物理 package，其中 503,679 个 `.uasset`、1,490 个 `.umap`。
- 调查数据库中的 `assets`：577,579 行、576,341 个不同 package。该数值高于物理文件数是因为一个 package 可以暴露多个 Registry object，并且数据库还保留了其他证据源中的对象身份。
- 精确类识别结果：
  - Blueprint：25,686
  - DataTable：611
  - Map：1,541
- 1,372 个对象的 Blueprint 适用性仍是 `UNKNOWN`。
- `is_data_asset` 当前没有确认行。这不是“ARK 没有 Data Asset”，而是本轮坚持只接受 Registry 精确类，没有把未知子类启发式提升为确认事实。

### 2. 引用图谱

- 共记录 3,480,942 条引用边。
- Asset Registry 贡献 3,405,173 条合法 package 引用：
  - hard package：2,736,029
  - soft package：668,597
  - searchable name：547
- Registry 原始记录中另有 24 个非 package 标识；它们被单独存为低置信度 `TARGET_NOT_PACKAGE_PATH` 缺口，没有污染 package 图。
- 其他证据源补充了：
  - 序列化 package import 硬引用：30,537
  - 序列化软对象引用：6,142
  - Blueprint 未解析函数调用：24,424
  - Blueprint 启发式未解析引用：14,629
  - 已有知识库登记关系：27
- 报告已经计算最常被继承、最常被引用和跨顶层领域引用的候选项。当前“跨领域”只使用顶层内容目录作为代理维度，尚不等同于最终业务领域分类。

### 3. Blueprint 默认值与覆盖状态

- 默认属性表面共 10,588 条：
  - `CONFIRMED_FINGERPRINT_ONLY`：9,376
  - `NOT_RECOVERED`：1,212
- 所有 `NOT_RECOVERED` 行都没有伪造属性值。
- Blueprint Evidence：
  - `FRESH`：210
  - `NOT_APPLICABLE`：550,514
  - `NOT_MEASURED`：26,838
  - `SOURCE_NOT_AVAILABLE`：2
  - `STALE`：15
- 即使是 `FRESH` 证据也仍可能有局部盲区：累计 52 个 `NOT_RECOVERED` 项和 1,392 个 `SOURCE_NOT_AVAILABLE` 项。因此“Evidence 新鲜”不等于“对象已完整恢复”。

### 4. Native / Ghidra 边界

- 扫描到 13 个候选 native evidence store，最终只选择 2 个与当前二进制身份相符的来源；其余 11 个 fixture 或重复来源被过滤。
- 当前 DLL 与 PDB 哈希匹配，并记录了 Ghidra 12.1.2、Java 21.0.11+10-LTS 的分析环境身份。
- 收集到 204 个 native symbol。
- 132 个 Blueprint/native 关系目前仅为 `NAME_ONLY_CANDIDATE`。
- 已确认 native 关系：0。
- 已恢复 native field access：0。

这说明 native 边界已经被结构化地纳入同一调查包，但当前证据只够定位候选，不能把名称相似性升级成已确认调用或字段访问。

### 5. 已有领域知识

发现并登记了 6 个现有 SQLite 知识库、74 张表、298,096 行：

| 领域表 | 行数 |
| --- | ---: |
| asset catalog | 289,181 |
| buffs | 3,353 |
| primal items | 3,340 |
| status components | 874 |
| primal game data | 676 |
| loot | 672 |

已有知识库还提供了 27 个系统登记关系：

- buff：7
- creature：4
- item：2
- global asset reference：14

这些关系已进入统一图谱，但登记覆盖率明显不足，不能据此推断只有这些全局系统入口。

### 6. 查询与抽样覆盖

- 查询语料：38 个问题、13 个领域标签，其中 27 个涉及 native 边界、9 个涉及地图、28 个涉及运行时行为。
- 复核样本：102 个。
- 规定的抽样规则全部满足，没有样本短缺：
  - 高 descendant：10
  - 高 referencer：10
  - 跨领域候选：10
  - Blueprint/native 候选：10
  - 全局候选：20
  - leaf：10
  - 易误导候选：5
  - 完整 fresh 证据：5
  - 高缺口或 stale：5
  - 12 个领域各至少 2 个

## 最终产物与可验证身份

| 产物 | 仓库相对路径 | 大小 | SHA-256 |
| --- | --- | ---: | --- |
| Git LFS 托管的视察调查包 | `knowledge_base/discovery_bundle.zip` | 505,740,267 bytes | `7eae98300ea5c1665c50222cc888580be8349aac1b92e5f8ee7f3713cae2292d` |
| 调查数据库 | `knowledge_base/discovery_bundle/kb_discovery.sqlite` | 3,816,177,664 bytes | `9f106a091815dd88aa729d28140db728e0f1b37dbeebf2fd5f2182492ef4ea50` |
| 14 问发现报告 | `knowledge_base/discovery_bundle/discovery_report.md` | 11,736 bytes | `bc929d2e9ebac819ba9deca9b22138d748b1e0350f186599b033ad0847c08b54` |
| 发现 manifest | `knowledge_base/discovery_bundle/discovery_manifest.json` | 3,721 bytes | `3495da024840a354bbc789c23520a5903f999b90e3ef8fc5cf24abe99958756a` |

ZIP 中共有 215 个文件，SQLite 成员使用 ZIP64。manifest 记录的源代码提交为 `58b92ec0400fafb8a24431eeddef342b94a33d8d`；这是生成最终数据包时的干净代码状态，之后增加本视察文档不会改变数据包内容。

## 已完成的验证

- SQLite `PRAGMA integrity_check`：`ok`
- Bundle audit：
  - `passed = true`
  - `errors = []`
  - `sqliteIntegrity = true`
  - `pathRedaction = true`
  - `zipVerified = true`
- ZIP 内每个成员均按 SHA-256 重新读取验证。
- 报告 14 个必答章节顺序完整。
- 查询语料 38 行。
- `SHA256SUMS.txt` 覆盖除其自身外的 214 个文件。
- 报告、README、manifest 和样本清单没有 Unicode replacement character。
- 发现工具专项测试：11/11 通过。
- 仓库完整测试：638 项通过，0 失败。
- Ruff、Python compile、Git diff whitespace 检查通过。

## 目前仍不能声称已经解决的内容

1. 还没有最终知识库的表、索引、预计算边界、失效策略或查询路由定案；本轮只提供决定这些问题所需的调查证据。
2. 132 个 native 关系只是名称候选，尚无已确认调用；native field access 仍为 0。
3. 仍有 1,212 条默认值未恢复、26,838 个 Blueprint 未测量、15 个 stale Evidence 对象和 2 个当前来源不可用对象。
4. Data Asset 子类需要下一轮类层级深读，当前的 0 只是保守分类结果。
5. 跨领域统计目前使用顶层目录代理，最终领域本体尚未定义。
6. 查询语料中的数量代表调查覆盖，不代表所有问题已经得到完整答案。
7. 系统登记主要来自现有知识库，覆盖面不足，仍需从序列化与运行时证据扩大。
8. 本轮没有改造最终知识库，也没有把 discovery 数据直接并入生产查询路径。

## 请 GPT Pro 重点视察并给 Codex 下一步方向

请基于上述证据检查：

1. 目前的调查规模、缺口表达和样本是否足以开始设计最终知识库；
2. 下一轮优先级应放在 native 确认、Blueprint 高缺口深读、Data Asset 精确分类，还是系统登记扩展；
3. 哪些候选可以进入“全局背景 / 领域知识 / 浅层实体索引”三层，哪些仍必须保持未知；
4. 在进入最终方案前，还缺少哪些必须量化的验收阈值或反例样本；
5. Codex 下一阶段应先补证据，还是可以开始提出数据库表、索引、增量失效和查询路由方案。

请给出审查结论与下一阶段方向即可；不需要接管或重写 Codex 已完成的实现。
