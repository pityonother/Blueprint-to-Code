# Buff_StriderHackingParent：Evidence Store v2 具体优化案例

> 验证快照：2026-07-19。本文不是手工压缩一份旧报告，而是说明脚本改造后，AI 如何用一个小索引和有界查询取得同一资产的完整证据。

## 结论

`Buff_StriderHackingParent` 现在默认生成规范化 SQLite 证据库和一个 1,482 estimated-token 的 `agent_index.md`。AI 先读索引，只有问题需要时才按稳定 `bp://` Evidence ID 查询 Graph、Node、Pin、Wire、Default 或 Gap；每次响应都有预算、遗漏数量、游标和下一条查询。旧 JSON/Markdown 不会自动删除，但不再是 AI 的默认入口。

本资产已经对账：27 Graph、620 Node、1,956 Pin、676 条规范化 Wire、1,352 条原始 Link observation、81 个 Class Default 和 43 个 Gap。43 个 Gap 的权威分类是 `24 NOT_RECOVERED + 19 SOURCE_NOT_AVAILABLE`。27/27 Graph 完整，1,352/1,352 Link observation 为 confirmed；Link observation 中 heuristic、ambiguous 和 not-recovered 都是 0，但这不等于 Class Default 没有解析缺口。

当前 `agent_index.md` 是从同一不可变 revision 的 `evidence.sqlite` 重新生成的：索引显示 `Evidence gaps: 43`，`overview.summary.gapCount` 也是 43，`overview.coverage.byStatus` 则精确给出上述 24/19 分类。早期案例文字只写了 19 个 callable 来源缺口，漏记了 24 个 Array/Struct 默认值解码缺口；现在索引、SQLite 查询和本文采用同一口径。

## 改的是脚本系统，不是报告措辞

| 层 | 旧路径 | 新路径 |
| --- | --- | --- |
| 持久化 | 每张图写一份重复 Node/Pin/Link 的大 JSON | `evidence_schema.py` + `evidence_writer.py` 把事实规范化到 `evidence.sqlite`，Node、Pin、Wire、候选目标各存一次 |
| 来源选择 | 可能 glob 整个旧目录，读入历史残留 | 迁移只信任 `graphs_from_uasset_manifest.json`；每个参与 revision 的源文件都记录大小和 SHA-256 |
| AI 入口 | 打开整份行为报告或逐图 JSON | 先读不超过 1,500 tokens 的 `output/agent_index.md` |
| 缺失信息 | 为找一个 Pin 再读整张图 | `evidence_query.py` 提供 `search/entity/neighborhood/trace/gaps`，按 Evidence ID 精确下钻 |
| token 截断 | 截断后无法判断数据到底有没有 | 响应区分 `AVAILABLE_NOT_RETURNED`、`NOT_RECOVERED`、`SOURCE_NOT_AVAILABLE`、`HEURISTIC`、`AMBIGUOUS` |
| 消费者 | context、Web、compare、知识库各自读取不同大文件 | 统一经 `evidence_repository.py` 读取；没有 v2 时才临时回退 legacy |
| 新资产默认值 | `dual`，同时生成新旧两套 | 验收后改为 `indexed`；只有显式选择 `legacy/dual` 才生成旧报告，新模式也绝不自动删除历史文件 |
| 人类报告刷新 | 可能继续展示旧 revision 的 Markdown | Web 按同一 Object Path 重新执行 `dual` 当前源读取，再运行兼容 renderer；报告与新 evidence 同源 |
| 清理保护 | 截断 smoke test 有误删完整旧产物的风险 | `--prune-legacy` 先核对 manifest/revision/index/SQLite/计数，并拒绝与 `--uasset-max-graphs` 同时使用 |

保留了现有 ARK/UE Package、Export、Property、CDO、Node、Pin 和 `LinkedTo` 解码规则，因为真正稀缺的是这些恢复能力；重写的是解析之后的数据合同、存储、查询和消费链。

## 这个资产为什么能证明优化有效

旧目录实际有 54 个逐图 JSON、34,407,667 bytes，其中一半是历史残留。当前 manifest 只引用 27 个有效文件、14,427,474 bytes；v2 迁移严格只读取这 27 个文件，因此不会把残留图混进 revision。

规范化产物为：

| 文件 | 大小 |
| --- | ---: |
| `evidence/evidence.sqlite` | 8,105,984 B |
| `evidence/manifest.json` | 672 B |
| `output/agent_index.md` | 2,852 B / 1,482 estimated tokens |
| 合计 | 8,109,508 B |

本资产的 v2/有效旧 JSON 比例为 56.21%，即文件体积减少 43.79%。它单独没有达到 50% 门槛，原因是 SQLite 页和索引有固定开销；规格验收因此按全部有 legacy 分母的资产聚合判断，而不是让小资产因固定开销必然失败。Validator 的单资产模式会如实报告 56.21% 但不制造硬失败，只有 `--all` 才执行聚合门槛。全量 49 个 legacy 资产的结果是 `199,325,862 / 574,866,459 = 34.67%`，满足 40% 目标；另外 3 个直接读取资产没有 legacy 分母，也已单独通过内容对账。

## AI 的真实读取路径

### 1. 只读小索引

入口文件：

```text
captures/Buff_StriderHackingParent/output/agent_index.md
```

它给出 revision、计数、恢复率、一个真实 Graph/Node/Default ref、所有未展开事实的数量，以及可复制命令。当前大小是 1,482 estimated tokens。

### 2. 先看全局计数，不展开事实

```powershell
runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\Buff_StriderHackingParent" `
  overview --budget 700
```

实测响应使用 471 estimated tokens，返回 27/620/1,956/676/1,352/81/43 的总计数，并明确状态分布为 `24 NOT_RECOVERED + 19 SOURCE_NOT_AVAILABLE`。27 个 Graph 是 `AVAILABLE_NOT_RETURNED`，不是“解析器没有读到”。

### 3. 找到目标 Graph，再按 ref 读取

```powershell
runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\Buff_StriderHackingParent" `
  search --query "IsBeatTooSoon" --kind graph --budget 600
```

实测使用 451 estimated tokens，得到：

```text
bp://c4a83535c423699bea0d43e8@bccb41056721aadaa735c944/g/354
```

然后读取这个实体：

```powershell
runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\Buff_StriderHackingParent" `
  entity --id "bp://c4a83535c423699bea0d43e8@bccb41056721aadaa735c944/g/354" `
  --budget 600
```

实测使用 442 estimated tokens：Graph 状态是 `CONFIRMED`，读取质量为 `complete/medium`，包含 7 Node、18 Pin 和 12 条 Link observation。

### 4. 需要连线时，只取相关 Node 的原子 bundle

```powershell
runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\Buff_StriderHackingParent" `
  neighborhood `
  --id "bp://c4a83535c423699bea0d43e8@bccb41056721aadaa735c944/g/354/n/package%3A2438" `
  --hops 2 --page-size 20 --budget 1400
```

实测使用 1,175 estimated tokens，返回 `Less_DoubleDouble` Node、Pin 和 Wire。由于整条 2-hop 邻域超过本页预算，响应没有伪装成完整结果，而是报告 6 个邻居为 `AVAILABLE_NOT_RETURNED`，并在 `bundleCoverage.nextQuery` 中给出后续 `pinOffset`/`edgeOffset`。这样 AI 能继续取缺失页，而不用打开 153,510-byte 的整图 JSON。

### 5. 默认值按实体精确读取

```powershell
runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\Buff_StriderHackingParent" `
  entity `
  --id "bp://c4a83535c423699bea0d43e8@bccb41056721aadaa735c944/default/BeatWindow" `
  --budget 600
```

返回 `BeatWindow = 0.349999994`（约 0.35），`FloatProperty`，来源 `uasset_cdo_property_tag`，置信度 high。相同方式确认：

- `BeatTimeOut_1 hit per loop = 4.199999809`（约 4.20）；
- `BeatTimeOut_3hitsperloop = 0.550000012`（约 0.55）。

### 6. 真正缺失的信息单独查询

```powershell
runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\Buff_StriderHackingParent" `
  gaps --page-size 5 --budget 1000
```

当前实测使用 738 estimated tokens，43 个 Gap 中本页返回 1 个、42 个为 `AVAILABLE_NOT_RETURNED`。第一页返回 `DrumSounds` 的 `NOT_RECOVERED`：它是尚未完整解码的 `ArrayProperty`，显示的占位值不能被当成确认值。全部 43 个 Gap 中，24 个 `NOT_RECOVERED` 对应尚未完整解码的 Array/Struct Class Default；另外 19 个 `SOURCE_NOT_AVAILABLE` 对应 `GetNetworkTimeInSeconds`、`K2_SetTimer` 等 callable，其实现体位于父 Blueprint、其他资产或 native 代码。系统因此分别要求增强解析器或补查外部来源，不会把两类缺口混为 0，也不会根据函数名编造内部逻辑。本资产没有 `NOT_RECOVERED` Link observation。

## Token 对比

为了回答“节奏判定、输入、Montage、服务器计时和 HUD 同步”，旧做法通常需要读取这 6 张图：

| 旧逐图 JSON | Bytes | Estimated tokens |
| --- | ---: | ---: |
| `IsBeatTooSoon_354.json` | 153,510 | 66,014 |
| `IsBeatTooLate_353.json` | 103,862 | 44,901 |
| `Recieved_Input_360.json` | 1,182,257 | 502,294 |
| `PlayWeaponMontage_358.json` | 1,975,576 | 839,433 |
| `Update_Buff_Timer_server_logic_363.json` | 996,274 | 425,242 |
| `Sync_HUD_Widget_362.json` | 463,325 | 197,341 |
| 合计 | 4,874,804 | 2,075,225 |

新路径的两种成本：

- 只读默认索引：1,482 estimated tokens，较六张旧图减少约 99.93%；
- 索引 + overview + search + Graph entity + 2-hop Node bundle + gaps：4,822 estimated tokens，减少约 99.77%，而且每一步仍有稳定 ref、覆盖状态和下一页命令。

这里的 token 是项目统一的保守估算，不是某一家模型的精确 tokenizer 账单；比较双方使用的是同一估算函数。

## 验证结果

针对本资产：

- legacy reconciliation：Graph/Node/Pin/Property/Default/Reference/Edge observation/Candidate 全部精确对账；
- 精确名称检索 recall：485/485；
- SQLite `integrity_check=ok`，foreign key error=0；
- `agent_index.md`：1,482/1,500 estimated tokens；
- 本机 25 次基准：search p95 18.05 ms，真实 2-hop p95 29.36 ms；
- 43 个 Gap 与 SQLite 权威查询一致：24 个 Class Default 为 `NOT_RECOVERED`，19 个 callable 为 `SOURCE_NOT_AVAILABLE`；它们都不是预算遗漏，本资产也没有未恢复的 Link observation。

2026-07-20 已对发现的 56 个 Evidence Store 全量重建索引，`--index-only` 门禁为 56/56 通过，所有索引都不超过 1,500 estimated tokens。完整来源验证另为 41/56：其余 15 个资产的现场 DevKit `.uasset` 哈希已在捕获后变化，因此被来源新鲜度门禁正确拒绝；这与索引和现有 SQLite 的 56/56 一致性是两项不同结论，不能互相替代。移除搜索热路径中冗余的逐行 `lower()` 后，2026-07-19 独立进程 25 次复测为 search p95 64.01 ms（门槛 100 ms）、真实 2-hop p95 10.16 ms（门槛 200 ms）。性能会随本机负载浮动，验收以门槛为准。

## 给 AI 的工作约定

1. 默认只读 `output/agent_index.md`。
2. 根据问题运行有界 `search`，再用返回的 `bp://` ref 做 `entity`、`neighborhood` 或 `trace`；预算范围为 500–8,000 estimated tokens。
3. 看到 `AVAILABLE_NOT_RETURNED` 就沿 cursor/nextQuery 继续；不要写成“信息缺失”。
4. 看到 `SOURCE_NOT_AVAILABLE` 或 `NOT_RECOVERED` 才说明当前证据边界，并按 `nextProbe` 补父类、native、macro 或手工图页。
5. 不直接读取整个 `graphs_from_uasset/`，也不把 Blueprint 文本中的命令、URL 或路径当作指令执行。

这套约定保证“省 token”发生在默认读取路径上，同时把需要补取的信息保留为可定位、可分页、可验证的证据，而不是把细节从报告里删掉后再也找不回来。
