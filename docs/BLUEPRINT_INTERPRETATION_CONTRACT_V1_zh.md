# Blueprint Interpretation Contract v1

Blueprint Interpretation Contract v1 把一份已发布、来源身份仍有效的 Blueprint
Evidence v3 转成确定性的控制流/数据流解释、简短人类报告、逐句 Evidence 追踪和显式
缺口。它不是完整反编译器，不恢复原始 C++，也不保证伪代码可编译。

## 1. 权威边界

解释器只从当前 Evidence SQLite 的 Graph、Node、Pin、Edge、Observation、Default、
Diagnostic 和 Reference 读取事实，不从 Markdown 报告、文件名或用户笔记回读结论。

- `CONFIRMED` statement 至少绑定一个当前 revision 中存在的 exact `bp://` ref。
- 只有 `resolution_status=resolved_pin` 且 exact Pin 方向为 output→input 的 Edge
  可进入确认的控制流或数据流；同向、反向或未知方向会成为显式 edge gap。
- 只有 Function Reference 的 `target_ref` 已精确绑定到当前 Evidence revision 的
  `bp://.../g/<export-index>` 时，本地函数调用才可绑定本地图；owner/name-only 匹配
  不能升级为确认事实。当前解析源若未提供非布尔、非负的
  `member_graph_export_index`（或等价 `target_graph_export_index`），解释器会保留
  callable-body gap。写入器还要求 `member_parent_object_path` 精确等于当前资产
  Object Path，且 reference、调用节点及源/目标图 provenance 均为 confirmed。
- graph/node provenance 若为 heuristic、ambiguous、not recovered 或 source not
  available，对应 statement 会保留相同的非确认状态，不进入 confirmed summary。
- delegate 的 Bind 与 Invoke 只按 exact node type 区分；未知 delegate operation 不会
  被补写成调用或绑定。
- 节点名称与关键词只生成 `heuristicReviewHints`；每条 hint 固定标记
  `basis=KEYWORD_AND_NAME_HEURISTIC`、`confidence=HEURISTIC`、
  `notEvidence=true`。
- 未恢复、歧义或不在本资产中的来源进入 `gaps.json`，不会由解释器补写实现体。

statement 状态只有：`CONFIRMED`、`HEURISTIC`、`SOURCE_NOT_AVAILABLE`、
`NOT_RECOVERED`、`AMBIGUOUS`。`SOURCE_NOT_AVAILABLE` 与 `NOT_RECOVERED` 都不等于空值
或否定事实。

## 2. 不可变发布布局

Interpretation 与 Evidence 使用独立 revision 和 pointer。Interpretation manifest
单向绑定精确的 `evidenceRevisionId` 与 Evidence manifest SHA-256；Evidence 不反向
引用 Interpretation。

```text
captures/<AssetName>/
  interpretation/
    current.json
    revisions/
      <interpretationRevisionId>/
        interpretation.json
        interpretation.md
        trace.json
        gaps.json
        pseudocode.txt
        manifest.json
```

发布器在 asset 同一文件系统内完成 staging、严格合同验证、immutable revision rename，
再在共享 `.publication.lock` 下重新验证 Evidence baseline，并以 compare-and-swap 原子
更新 `interpretation/current.json`。旧 pointer、manifest、artifact hash、来源身份或目录
身份发生变化时失败关闭；不会覆盖 Evidence source，也不会静默回退到 v2/legacy。

默认 reader 会同时验证：

1. Evidence pointer、manifest、SQLite 身份和 source freshness；
2. Interpretation pointer、manifest、全部 artifact 的 bytes/SHA-256；
3. 两个 revision 的单向绑定、assetId/Object Path 和 semantic digest；
4. 每个 ref、statement、gap、伪代码行号与 UTF-8 byte range；
5. 发布目录没有链接、hardlink、额外文件或被替换的路径组件。

此外，reader 会用当前 Evidence 在确定性预算内重新生成 Interpretation，并逐项比对
`interpretation.json`、`trace.json`、`gaps.json`、Markdown 与伪代码；仅重新计算 hash、
manifest 或 revision ID 的语义篡改同样失败关闭。

具体并发与恢复原因见
[ADR-004](decisions/ADR-004-immutable-evidence-and-interpretation-revisions.md)。

## 3. 产物含义

| 文件 | 用途 |
| --- | --- |
| `interpretation.json` | asset summary、control-flow IR、data-flow IR、statements、review hints |
| `interpretation.md` | 转义后的简短人类报告，分开显示 confirmed、non-confirmed、gaps 与 hints |
| `trace.json` | statement 与伪代码行到 exact Evidence refs 的双向追踪 |
| `gaps.json` | 缺口清单及 code/status/ref/source 汇总 |
| `pseudocode.txt` | Evidence-derived、非原始 C++、不可保证编译的可选伪代码 |
| `manifest.json` | Evidence 绑定、解释器/schema 身份和全部 artifact hash |

伪代码首行固定为：

```text
EVIDENCE-DERIVED PSEUDOCODE — NOT ORIGINAL C++ — NOT GUARANTEED COMPILABLE
```

每个可执行伪代码行必须恰好绑定一个 statement ID，并能通过 `trace.json` 找到 exact
Evidence refs。无法证明的 branch merge 使用 label；缺失表达式、macro 或 callable body
保留显式 gap，不推测实现。不可信文本会用确定性的 JSON/Unicode 形式转义；原始 HTML、
Markdown、shell metacharacter、控制字符或换行不会被直接嵌入伪代码行。

## 4. CLI

生成并发布完整 Interpretation revision：

```powershell
runtime\python\python.exe scripts\interpret_blueprint_evidence.py `
  --asset-dir "captures\<AssetName>" `
  --format all
```

支持：

- `--graph <bp://ref>`：只投影 stdout 中的 JSON；发布仍是完整 asset scope。
- `--format json|markdown|pseudocode|all`：选择 stdout 格式。
- `--budget <正整数>`：确定性 work-unit 上限；超限不发布。
- `--fail-on-gap`：存在任何 gap 时以独立门禁错误退出且不更新 pointer。
- `--allow-stale=false`、`--allow-legacy-fallback=false`：显式诊断开关；即使启用，也
  不能让 stale、v2 或 legacy Evidence 推进 Interpretation current。

`all` 的 stdout 是不含本机绝对路径的 JSON receipt；人类报告与伪代码从 immutable
revision 读取。CLI 成功不等于 ARK 运行时实测通过。

## 5. HTTP API 与 UI

Control Center 提供只读 GET：

```text
GET /api/blueprint/assets
GET /api/blueprint/assets/<asset>/evidence/health
GET /api/blueprint/assets/<asset>/interpretation
GET /api/blueprint/assets/<asset>/statements/<url-encoded-id>
GET /api/blueprint/assets/<asset>/trace
GET /api/blueprint/assets/<asset>/gaps
```

所有集合使用 `limit` 与 opaque `cursor`；cursor 绑定 endpoint、filter、Evidence revision
和 Interpretation revision。revision 或查询变化后旧 cursor 失败关闭。公开响应由
`schemas/http_api/blueprint_*_v1.schema.json` 约束，不返回本机绝对路径。Interpretation
响应最多预览 20 条 heuristic hints，summary 只保留固定聚合字段；完整 statement、trace
和 gap 通过分页读取。

Web 主视图为 `Interpretation`、`Evidence`、`Gaps`、`Legacy / Experimental`。点击
statement 可追到 Evidence refs，再查询 neighborhood/trace；页面显示 current revision、
coverage 和 stale 状态。旧 heuristic C++/report 只在 Legacy / Experimental 中显示，
不称为恢复出的 C++。

## 6. 发布门禁与验证

正式门禁为：

```text
confirmed statements without Evidence = 0
executable pseudocode lines without trace = 0
semantic digest mismatch on identical input = 0
unreported unresolved edges = 0
```

聚焦验证：

```powershell
runtime\python\python.exe -m pytest -q `
  tests/test_interpretation_contract.py `
  tests/test_interpretation_publication.py `
  tests/test_blueprint_interpretation_cli.py `
  tests/test_blueprint_interpretation_http.py
node tests/blueprint_frontend_contract.mjs
npm run build
```

fixture 覆盖 entry、branch true/false、sequence、local/external call、variable、pure/shared
expression、return、delegate、cast、macro/foreach、collapsed graph、reroute、cycle、重复身份、
unresolved/ambiguous edge、不可信 Unicode/HTML/Markdown/shell 字符、深图与预算、确定性、
source 变化和中断发布。fixture 只证明公共管线合同，不冒充真实 ARK DevKit 或游戏运行时
证据。
