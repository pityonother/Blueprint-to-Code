# GPT Pro 审核交接报告：ARK KB vNext Stage 10–12

- 生成日期：2026-07-29
- 远程仓库：<https://github.com/pityonother/Blueprint-to-Code>
- 交接分支：`codex/ark-kb-stage12-cutover`
- 主交接 PR：<https://github.com/pityonother/Blueprint-to-Code/pull/8>

## 0. 给 GPT Pro 的执行说明

这是供独立证据审计使用的后续工程交接材料，不构成独立 reviewer
approval，也不是 vNext 切换批准。

请始终遵守以下边界：

1. 不降低、跳过或重新解释任何质量门禁。
2. 不把 fixture、候选样本、自动生成结果或同一执行者的签名当作 production gold。
3. 不伪造独立 reviewer、人工批准或 burn-in attestation。
4. 不因构建、测试或性能通过而提前把 `mode` 改为 `active`，也不把
   `defaultQuerySource` 从 `legacy` 改为 `vnext`。
5. 所有无法由当前证据闭合的结论必须 fail closed，并保留明确 blocker。

当前裁决：

- `BLOCKED_BY_INDEPENDENT_REVIEW`
- `BLOCKED_BY_INCOMPLETE_PRODUCTION_INCREMENTAL`
- `BLOCKED_BY_MISSING_BURN_IN_EVIDENCE`
- `mode=shadow`
- `defaultQuerySource=legacy`
- 不具备 cutover 条件

## 1. 远程 PR 栈

以下状态是 2026-07-29 的核验快照；复核时应再次读取 GitHub 当前状态。

| PR | 阶段 | Head | 远程状态 |
| --- | --- | --- | --- |
| [#2](https://github.com/pityonother/Blueprint-to-Code/pull/2) | baseline / DevKit root | `0cfed0f` | Draft、open；CI 与 Native Fixture checks 通过 |
| [#3](https://github.com/pityonother/Blueprint-to-Code/pull/3) | Stage A baseline | `0e2f7b4` | Draft、open、clean、mergeable |
| [#4](https://github.com/pityonother/Blueprint-to-Code/pull/4) | Stage B gold review infrastructure | `8c4efc8` | Draft、open、clean、mergeable |
| [#5](https://github.com/pityonother/Blueprint-to-Code/pull/5) | Stage C query correctness | `818a9f3` | Draft、open、clean、mergeable |
| [#6](https://github.com/pityonother/Blueprint-to-Code/pull/6) | Stage D performance | `a92a716` | Draft、open、clean、mergeable |
| [#7](https://github.com/pityonother/Blueprint-to-Code/pull/7) | Stage E incremental infrastructure | `e148880` | Draft、open、clean、mergeable |
| [#8](https://github.com/pityonother/Blueprint-to-Code/pull/8) | Stage F burn-in / cutover controls | 以 PR 当前 head 为准 | Draft、open、clean、mergeable |

PR #1 已被 PR #2 取代，不应作为基线继续评审。

除 PR #2 外，核验时 PR #3–#8 尚无 GitHub check runs；这不能被描述为
“CI 已通过”。本报告中的测试结论来自本地真实执行。

## 2. 当前 canonical snapshot

以下是原始 evidence workspace 中的运行时核验结果。生成的
`knowledge_base/vnext/` snapshot、数据库、报告和根指针被 Git 忽略，既不在
Stage 12 worktree 中，也不随本 PR 上传；摘要用于审计，原始 runtime
artifacts 需要另行授权和受控交付。

本地 runtime 根指针：

`knowledge_base/vnext/current.json`

解析后的 immutable snapshot：

| 项目 | 值 |
| --- | --- |
| build ID | `20260729T115548-1a203b594bb6` |
| source fingerprint | `1a203b594bb6119dbf29d5a0c8789bd653c716eaf72e5915ee5a176675576450` |
| discovery fingerprint | `028a12c429903466aa52f99c5e63c8d90813585b9d5c6a8c303fbb93a9d6a31f` |
| quality gates | `60/75` passed，`15` failed |
| 前一密封快照 | `58/75` passed，`17` failed |
| 新增通过 | `queries.expected_gap_match`、`queries.single_entity_p95_ms` |
| health | `READY / FRESH` |
| burn-in | `MISSING` |
| burn-in gap | `BURN_IN_ATTESTATION_MISSING` |

根 `current.json` 是 canonical pointer。`manifests/current.json` 是
legacy-v1 fallback；根指针存在时不能用 fallback 文件反推当前快照悬空。

权威 immutable artifacts 已核验：

- `catalog.sqlite`
- `core.sqlite`
- `search.sqlite`
- 六个 domain projection 数据库
- `manifest.json`
- `quality_gates.json`
- `query_benchmark.json`
- `query_case_results.jsonl`
- `query_failure_matrix.json`

九个权威数据库的 bytes 与 SHA-256 均和 manifest 一致。十个 SQLite
数据库（含 runtime cache）的 `integrity_check` 均为 `ok`，外键违规为
`0`，journal mode 为 `delete`，且没有 WAL/SHM sidecar。

`cache.sqlite` 被明确标为 `disposable=true`，是与当前 build 绑定的可变
runtime cache，不是 semantic authority。运行时 cache 增长不能被误判为
immutable snapshot corruption。

关键密封摘要：

| Artifact | SHA-256 |
| --- | --- |
| manifest | `e82a50dd34b93f2649f3f1f7627c0b15f3b110c741939765fadbbdde3ea1c0da` |
| quality gates | `bce31ecbe9e50a699b7acc7d5977b1865763c625e087211b9e523c9b814e65aa` |
| query benchmark | `f9950a60a0c7bf90ea2427855e84981c0222c030fe710f4e29cbbc9e79bc2361` |
| case results JSONL | `19e17ea1a8ee43c8dbb5ad3d21c6bcec59ffe4c2361b7dc45f09eb11efc8bf99` |
| failure matrix | `1ccbd4f7923cea602f9b5cceea19f703e6968564401bf09cc75302c54574a448` |
| diagnostics binding | `6aca92ac895adb31f283e7356d97f7efc02c5e6f924f6600e69a594494739bc9` |

## 3. Stage A–F 执行结果

| 阶段 | 已真实完成 | 仍然阻塞 |
| --- | --- | --- |
| A | 真实输入身份、shadow baseline、immutable snapshot 和 fail-closed gates | 未达到全部门禁 |
| B | 独立 review schema、validator、blind packs、status/report infrastructure | 无真实独立人工 reviewer；production gold 未写入 |
| C | 130-case query diagnostics、failure matrix、可解释 gap 与 protocol checks | 3 个 wrong-answer cases 需要独立复核 |
| D | 密封性能报告、重复 profile、storage/performance gates | 性能通过不等于可切换 |
| E | source manifest、change classification、staging、部分 production rebuild backends、receipt 验证、cache-hit 路径 | 8 类 production backends、narrow gates、reseal、publisher 和多数 E4 drills 未完成 |
| F | burn-in schema/validator、rollback CLI、切换前置约束 | 没有真实 burn-in attestation，不能切换 |

## 4. Gold review：基础设施已完成，人工结论未完成

production gold 当前计数：

- query：`5`
- registration：`0`
- role：`0`

已生成并通过 pack-only validator 的 blind packs：

| Pack | 数量 | Pack ID | SHA-256 |
| --- | ---: | --- | --- |
| query | 130 | `query-a072115933a575fd` | `9d828118cc7333b7f77a94596be14f4b94f56f9d44b0a0979d1aadf6ed73238c` |
| registration | 138 unique | `registration-b20a2660388c32d5` | `73540bc8636ff4e9a97354572cd1caae390e80c3c2d53756f71d5fbfbccb202f` |
| role | 360 | `role-c43e2571a8d0d505` | `d59708d6a06f31beadee98f53ed4af111e52ab4b1d0056e9ee4afeb17f7f2022` |

registration pack 包含 27 个 typed anchors 与 112 个 raw candidates，并有
1 个 overlap。typed candidate 仍有
`SOURCE_TYPED_CANDIDATE_SHORTFALL:27/120`。

这些 packs 只位于专用的本地 review worktree 下，并被 Git 忽略；它们不在
Stage 12 worktree，也不会提交到 GitHub。原因是它们是待独立授权分发和签收的
review material，不是 production gold。当前：

- `productionGoldWritten=false`
- 没有独立 reviewer receipt
- 不得把 pack validator 成功写成 gold review 完成
- 必须维持 `BLOCKED_BY_INDEPENDENT_REVIEW`

## 5. 剩余 3 个 query wrong-answer cases

### `negative-015-candidate-edge`

- 期望：`route=DB_PARTIAL, status=PARTIAL, gap=REFERENCE_CLOSURE_OPEN`
- 实际：`route=EVIDENCE_REQUIRED, status=GAP, gap=REFERENCE_CLOSURE_OPEN`
- Core 中没有所需 `GRANTS_ITEM` relationship

空 relationship 结果按当前协议必须 fail closed。没有证据支持通过 planner
通用改动强行返回 `DB_PARTIAL`。

### `registration-003-unverified-owner-target`

- 期望：`route=DB_PARTIAL, status=PARTIAL, gap=REFERENCE_CLOSURE_OPEN`
- 实际：`route=EVIDENCE_REQUIRED, status=GAP, gap=REFERENCE_CLOSURE_OPEN`
- 请求的 9 种全局 registration edges 均不存在
- 现有 `GhostItemSkin*` 记录是 `REFERENCES_OBJECT`，不是 registration edge

同样不支持通用代码修复；需独立 reviewer 判断 fixture 期望是否应恢复为
`EVIDENCE_REQUIRED`。

### `relationship-003-harvest-component`

route、identity、`OWNS_COMPONENT`、target 和 status 均一致；唯一不一致是
revision-bound Evidence URI：

- fixture：`@678888f577bb49fd826ff2df`
- 当前 fresh evidence：`@54a55de1437e3a5e184291d6`

旧 revision 不在当前 Core。直接返回旧 URI 会伪造 stale provenance。需独立
reviewer 决定是否把 fixture 更新到当前 fresh revision。

在独立复核完成并重新密封 benchmark 前，不得宣称 `130/130`。

## 6. Query 与性能结果

Query：

- protocol：`128/130`
- wrong answers：`3/130`
- expected gap match：`45/47`

性能：

- sealed old P95：`358.929 ms`
- sealed new P95：`3.786 ms`
- 三次本地独立进程复测 P95：`4.857 ms`、`4.104 ms`、`4.935 ms`
- storage 与 performance gates 通过

这些复测不属于独立人工复核，只证明当前查询路径达到相应性能门禁，不解除
gold、incremental 或 burn-in blocker。

## 7. Stage E production incremental 边界

已接通的真实 production backends：

- `FACT`
- `CLASS_CLOSURE`
- `EFFECTIVE_ENTITY`

尚无 production backend：

- `ROLE_ENTITY`
- `DOMAIN_ENTITY`
- `EDGE_ENTITY`
- `REGISTRATION_ENTITY`
- `NATIVE_FUNCTION`
- `BLUEPRINT_NATIVE_ENTITY`
- `PROJECTION`
- `QUERY_SNAPSHOT`

当前仅放行 1–32 个 add-only `BLUEPRINT_EVIDENCE`；update、delete 和 rename
明确 fail closed。测试中的 `IntegrationBackend` 只验证 worker 协议，不能算作
production backend。

E4 十二场景的真实状态：

| # | 场景 | 当前状态 |
| ---: | --- | --- |
| 1 | 单 Blueprint 修改 | `BLUEPRINT_UPDATE_NOT_SUPPORTED` |
| 2 | 单 Blueprint 新增 | 可安全 stage 和产生部分 receipts；因 backend 不全而不能发布 |
| 3 | 单 Blueprint 删除 | `BLUEPRINT_DELETE_NOT_SUPPORTED` |
| 4 | registration target 改变 | full rebuild required |
| 5 | class parent 改变 | full rebuild required |
| 6 | Native evidence set 更新 | full rebuild required |
| 7 | runtime summary 更新 | full rebuild required；当前没有真实 change input |
| 8 | worker 中途崩溃 | 只有 worker/test drill，没有 production end-to-end drill |
| 9 | narrow gate 失败 | 只有 injected test hook；production narrow gate runner 缺失 |
| 10 | pointer 替换前崩溃 | 有原子性测试基础；production incremental publisher 缺失 |
| 11 | 并发旧/new reader | 尚无 production incremental drill |
| 12 | 相同输入 cache hit | 真实 end-to-end 完成，`cacheHit=true`、`published=false` |

已修复一个 fail-closed 硬化问题：未知 ingest receipt schema 现在会在
worker、gates 和 publish 前返回 `BLUEPRINT_INGEST_RECEIPT_INVALID`，不能再
自证 ingest 完成。

因此 Stage E 必须保持：

`BLOCKED_BY_INCOMPLETE_PRODUCTION_INCREMENTAL`

## 8. Stage F burn-in 与 rollback

切换前必须同时满足：

- 最新连续至少 3 个 sealed shadow snapshots
- 每个 snapshot 都满足 `qualityReportCutoverEligible=true`
- 每个 snapshot 都满足 `sealedInSnapshotManifest=true`
- 真实 `HUMAN_OPERATOR / APPROVED` attestation
- attestation 满足 `complete=true`、`undispositioned=0`、
  `wrongAnswers=0`、`staleLeaks=0`、`candidateCompletions=0`
- rollback drill 通过
- concurrent-reader drill 通过
- E4 十二场景全部形成真实 evidence

rollback CLI 已支持：

- `--expected-current-build-id`
- `--dry-run`
- 仅替换 pointer，不修改历史 immutable snapshot

当前没有合格 burn-in 证据，所以：

`BLOCKED_BY_MISSING_BURN_IN_EVIDENCE`

## 9. 本地验证结果

最终集成分支实测：

- full pytest：`1214 passed, 4 skipped, 2 warnings, 606 subtests`
- affected matrix：`137 passed, 22 subtests`
- frontend API / harvest contracts：通过
- Vite production build：通过
- Ruff：通过
- `git diff --check`：通过
- unchanged updater rerun：`cacheHit=true`、`published=false`、0 changes

Stage E 分支曾独立实测：

- full pytest：`1200 passed, 4 skipped, 606 subtests`
- affected matrix：`100 passed, 29 subtests`

本地通过不能替代缺失的 GitHub checks、独立 reviewer 或 burn-in evidence。

## 10. 建议 GPT Pro 的阅读顺序

1. `docs/ark_kb_vnext/GPT_PRO_STAGE10_12_HANDOFF.md`
2. `docs/ark_kb_vnext/GOLD_REVIEW_STATUS.md`
3. `docs/ark_kb_vnext/GOLD_REVIEW_PROTOCOL.md`
4. `docs/ark_kb_vnext/STAGE10_PERFORMANCE_PROFILE.md`
5. `docs/ark_kb_vnext/COVERAGE_AND_CUTOVER.md`
6. `docs/ark_kb_vnext/ADR_STAGE12_BURN_IN_AND_CUTOVER.md`
7. 若另行取得 runtime artifacts，再读取
   `knowledge_base/vnext/current.json`
8. 再读取它所指 snapshot 内的 `manifest.json`、
   `reports/quality_gates.json`、`reports/query_benchmark.json`、
   `reports/query_failure_matrix.json`
9. `scripts/update_ark_kb_vnext.py`
10. `scripts/blueprint_translator/kb_vnext/query_planner.py`
11. incremental worker、burn-in validator 与 rollback CLI 对应测试

## 11. 可移植复验命令

在仓库根目录执行：

```powershell
git status --short --branch
git diff --check
python -m pytest -q
```

unchanged updater 需要两个作用域：

- `$scriptRepo`：检出 `codex/ark-kb-stage12-cutover` 的脚本仓库或 worktree
- `$evidenceRepo`：经过授权、包含真实输入与 runtime snapshot 的原始
  evidence workspace

先替换 evidence workspace 占位路径并执行 preflight，全部存在后才能复跑：

```powershell
$scriptRepo = (Resolve-Path -LiteralPath '.').Path
$evidenceRepo = 'X:\REPLACE_WITH_AUTHORIZED_EVIDENCE_WORKSPACE'
$required = @(
  (Join-Path $scriptRepo 'scripts\update_ark_kb_vnext.py'),
  (Join-Path $evidenceRepo 'knowledge_base\discovery_bundle\kb_discovery.sqlite'),
  (Join-Path $evidenceRepo 'captures'),
  (Join-Path $evidenceRepo 'native_evidence'),
  (Join-Path $evidenceRepo 'knowledge_base\db'),
  (Join-Path $evidenceRepo 'analysis\harvest_nodes\resource_node_catalog.json'),
  (Join-Path $evidenceRepo 'knowledge_base\vnext\current.json')
)
$missing = $required | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missing) {
  throw "Missing authorized evidence input:`n$($missing -join "`n")"
}

python (Join-Path $scriptRepo 'scripts\update_ark_kb_vnext.py') `
  --discovery-database (Join-Path $evidenceRepo 'knowledge_base\discovery_bundle\kb_discovery.sqlite') `
  --capture-root (Join-Path $evidenceRepo 'captures') `
  --native-root (Join-Path $evidenceRepo 'native_evidence') `
  --legacy-kb-root (Join-Path $evidenceRepo 'knowledge_base\db') `
  --map-evidence-catalog (Join-Path $evidenceRepo 'analysis\harvest_nodes\resource_node_catalog.json') `
  --output (Join-Path $evidenceRepo 'knowledge_base\vnext')
```

GitHub clone 不包含被忽略的 runtime snapshot、review packs 或上述单独授权
分发的 evidence inputs。缺失时应报告输入缺失，不得用 fixture 或合成数据
代替。

## 12. 请求 GPT Pro 返回的审核结果

请按以下结构返回：

### CONFIRMED

- 已由代码、snapshot、manifest、report hash 或真实运行证明的结论。

### BLOCKED

- 独立 reviewer、真实输入、production backend、burn-in 或远程 CI 缺失。

### RISKS

- 任何可能降低 fail-closed 约束、伪造 provenance、把 cache 当 authority、
  把 fixture 当 gold 或提前切换的风险。

### NEXT ACTIONS

- 按依赖顺序给出可以真实执行的最小工程切片。
- 独立 review 与工程工作分开列出。
- 不要把人工 review blocker 扩大成停止所有 diagnostics、profiling 或
  incremental engineering 的理由。

最终批准条件不是“代码看起来完整”，而是全部门禁、独立复核、production
incremental evidence 和 burn-in evidence 同时闭合。在此之前必须保持
`shadow / legacy`。
