# ARK Knowledge Base vNext 实施完成报告

## 总体状态

计划中的 vNext 架构、四库存储、本体、类闭包、角色/深度、注册、事实、native gold、失效图、查询规划、API、工作台、legacy shadow compare、120 条基准与 fail-closed 门禁均已实现。

真实全量快照构建成功；23/26 个关键门禁通过。由于三个证据门禁未达标，完成状态是“可并行使用与继续补证”，不是“可替换 legacy”。

## 已交付

- 非破坏性、可原子提升的 `catalog/core/search/cache` 快照构建器。
- typed ARK domain、edge、fact、role 和 depth-policy ontology。
- Blueprint/native 统一类身份、closure、gap 和祖先类别。
- 类型化 system registration 识别与 materialized `REGISTERS` 边。
- 声明事实、继承后的 effective facts、领域投影和完整 lineage。
- 20 个 exact native targets 的 fail-closed gold pipeline。
- revision-driven 选择性失效依赖与队列。
- exact URI 快通道、DB-first planner、稳定 gap code、定向 probe、≤2,000 tokens Context Pack。
- 安全 HTTP API、同源/session 防护和三模式知识库工作台。
- legacy/vNext 只读影子比较与路径脱敏。
- 120 条平衡查询基准、26 项质量/性能/完整性/隐私门禁和自动 cutover 决策。

## 真实快照

- Build：`20260727T035514+0000-9f106a091815`
- Source SHA-256：`9f106a091815dd88aa729d28140db728e0f1b37dbeebf2fd5f2182492ef4ea50`
- 577,579 entities
- 3,441,879 catalog edges
- 26,495 classes / 92,248 closure rows
- 1,091,275 role rows
- 135 typed registrations
- 10,588 declared facts / 102,330 effective facts
- 298,003 preserved legacy lineage rows
- 593,234 invalidation dependencies
- 四库 integrity 均为 `ok`，FK 违规均为 0

## 查询与性能

- 120/120 给出完整或明确受限的 DB-first 结果。
- 64 条 `DB_ONLY_COMPLETE`，56 条 `EVIDENCE_REQUIRED`。
- 简单查询 DB-only 为 29/30（96.67%）。
- 单实体 p50/p95 为 0.054/0.341 ms。
- 2-hop p95 为 0.016 ms。
- 最大 Context Pack 为 440 estimated tokens。
- 一次真实性能修复把精确 URI p95 从 465.662 ms 降到门禁内：精确 canonical URI 先走唯一索引，再退回有界模糊搜索。

## API

| Method | Route | 用途 |
|---|---|---|
| POST | `/api/kb/query` | DB-first 查询与 Context Pack |
| POST | `/api/kb/plan` | 查询计划兼容入口 |
| POST | `/api/kb/compare` | legacy/vNext 只读对账 |
| GET | `/api/kb/health` | 快照、schema、cutover 状态 |
| GET | `/api/kb/entities/search` | 有界实体搜索 |
| GET | `/api/kb/entities/{id}` | 实体、角色、领域 |
| GET | `/api/kb/entities/{id}/facts` | 声明事实 |
| GET | `/api/kb/entities/{id}/relationships` | 类型化关系 |
| GET | `/api/kb/entities/{id}/coverage` | Coverage 和 gap |
| GET | `/api/kb/entities/{id}/effective-defaults` | 生效默认值 |

所有 POST 继续受 Host、Origin、loopback session、Content-Type 与请求体上限保护。

## 浏览器验收

在隔离 Playwright 浏览器中对真实快照完成：

- 1440×1000 桌面视口：`scrollWidth=1425`，无横向溢出。
- 390×844 触屏视口：document/body `scrollWidth=375`，无横向溢出。
- 实体搜索 `Dodo_Character_BP` 返回真实候选并可展开角色、事实、关系与 Coverage。
- compare 模式成功调用 `POST /api/kb/compare`（HTTP 200）。
- 缺少 Item fact 时显示 `MISSING_FACT` 和 `blueprint_evidence_query / named_fact`，没有伪造答案。
- 健康状态正确显示 `READY / shadow / legacy`。
- 键盘焦点按“蓝图分析 → 资源点采集排行 → 知识库 vNext → 刷新状态”进入工作区。
- 控制台 0 error、0 warning；所有被触发的 KB 请求均为 HTTP 200。

## 测试

在最终文档提交前执行：

```powershell
.\runtime\python\python.exe -m unittest discover -s tests -p "test_*.py"
npm run build
node tests\frontend_core_contract.mjs
node tests\api_frontend_contract.mjs
node tests\harvest_frontend_contract.mjs
node tests\knowledge_frontend_contract.mjs
git diff --check
```

最终结果：Python 全量 **698 项通过**；KB 专项 **60 项通过**；服务器安全回归 **15 项通过**；TypeScript/Vite production build 通过；4 个前端 `.mjs` 契约全部通过。

## 仍未解决

1. Deep/Semantic parent/native closure 只有 42.65%，未达到 98%。
2. 尚无独立复核的 300 资产角色 gold set。
3. 132 条 Blueprint-native 候选中没有确认边。
4. 六个高频领域投影当前为 0 行；这反映 typed verified fact 尚不足，不代表领域不存在。
5. legacy 298,003 行均保留 `LEGACY_UNVERIFIED` lineage，不提升成 confirmed fact。

## 切换建议

**keep legacy / shadow**

继续让工作台提供 `legacy`、`vNext`、`compare` 三种模式；优先补齐上述三项关键证据。不要删除旧库，不要把 vNext 改为默认，不要把候选 native 边或 legacy lineage 批量提升为确认事实。
