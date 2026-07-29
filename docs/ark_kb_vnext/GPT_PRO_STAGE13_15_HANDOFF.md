# GPT Pro 审核交接报告：ARK KB vNext Stage 13–15

- 核验时间：2026-07-30（Asia/Shanghai）
- 远端仓库：<https://github.com/pityonother/Blueprint-to-Code>
- 唯一初始基线：`codex/ark-kb-stage12-cutover@91964de123d1a538f3d406380e740fe96444a271`
- 本报告基线：`main@b897dc241012ad258c7698afda439e8c4a6c37db`
- 报告分支：`codex/ark-kb-stage15-handoff`

本报告用于 GPT Pro 和后续工程人员进行证据审计。它不是独立 reviewer
approval、human operator approval、production Gold freeze、burn-in attestation
或 vNext cutover 批准。

## 执行结论

Stage 13A 的完整集成、真实 GitHub CI 和 Native Fixture 已完成。Stage 13B
的签名、registry、artifact-bound review/burn-in v2 verifier 合同已经实现并
保持 fail closed；burn-in v2 尚未接入 snapshot eligibility、build CLI 或
current pointer。Stage 13C 的 review infrastructure、blind review packs 和
proposal-only Gold freeze 工具已经完成，但没有伪造任何人工结论。

在等待真实人工复核期间，已继续完成 worker row-scope、TEST_ONLY add-only
基础、pointer CAS、11-gate diagnostic contract，以及 UpdateBaseline /
pre-publication inspection 工程切片。当前环境缺少真实授权的新 Blueprint、
生产签名授权、安全的整树 staging/quarantine 和独立 base-binding，因此没有
生产增量发布，没有完成 E4 scenario 2，也没有启动 Stage 15 三轮 burn-in。

当前裁决：

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy",
  "cutoverEligible": false
}
```

## CONFIRMED

### 1. 初始基线、集成历史与 tag

- 远端 `refs/heads/codex/ark-kb-stage12-cutover` 仍精确指向
  `91964de123d1a538f3d406380e740fe96444a271`。
- [PR #9](https://github.com/pityonother/Blueprint-to-Code/pull/9)
  将 Stage 8–12 的完整历史以 merge commit 合入 `main`，没有 squash 或
  rebase。
- shadow baseline tag：
  `ark-kb-vnext-shadow-stage12-20260729`。
- annotated tag object：
  `c0d2db9219b4150c8bb9fdcaa5f9544e0bac347c`。
- peeled commit：
  `03547d455c222b1df4cf891304f1fbb766828520`。
- 该 tag 只表示 shadow baseline，不是 production-ready release。

### 2. PR、原始 head、merge commit 与远端 CI

下表中的 merge commit 均为 two-parent merge；第二 parent 等于对应 PR
head，因此保留了原始 commit SHA。

| PR | 范围 | PR head | merge commit | PR CI run/job | post-main CI run/job |
| --- | --- | --- | --- | --- | --- |
| [#9](https://github.com/pityonother/Blueprint-to-Code/pull/9) | Stage 8–12 integration | `91964de123d1a538f3d406380e740fe96444a271` | `03547d455c222b1df4cf891304f1fbb766828520` | `30455698351/90588581129` success | `30456065257/90589815686` success |
| [#10](https://github.com/pityonother/Blueprint-to-Code/pull/10) | v1 cutover/burn-in fail-closed hardening | `50f24ff3c128717cf6a8db59f30eb7c5805c6cf1` | `3d2530738ae137831a5824ec9ae5d57ca77958a8` | `30457343823/90594219433` success | `30457685275/90595396197` success |
| [#11](https://github.com/pityonother/Blueprint-to-Code/pull/11) | signed receipts / trusted registry foundation | `c284ebabca64230b814553c502a4116dfac22166` | `523dc5b82a6056a5a728c2d9b11a589ef9734005` | `30459315157/90600941701` success | `30459556151/90601759269` success |
| [#12](https://github.com/pityonother/Blueprint-to-Code/pull/12) | unsigned Gold hardening | `7a6936e07da7494b6bde0135ede27b7788f96931` | `ca4a39377994e247209bdd09c0c028ad8bb3d982` | `30465191398/90621052462` success | `30465502118/90622098916` success |
| [#13](https://github.com/pityonother/Blueprint-to-Code/pull/13) | burn-in v2 artifact-bound contract | `f37836011e8720384cbc87ad92168e46cdc6354f` | `d4cd1f8b19b41ec8028832c5d3c530004afde4c9` | `30468200124/90631367851` success | `30468434086/90632163245` success |
| [#14](https://github.com/pityonother/Blueprint-to-Code/pull/14) | signed Gold review v2 | `2d4a8d258965b28822a8747993fdb3cee576edfe` | `5bfa9bc914576985afa7714386111c93234b57ab` | `30467585732/90629266035` success | `30468105402/90631043435` success |
| [#15](https://github.com/pityonother/Blueprint-to-Code/pull/15) | worker durable row-scope | `8966b5d39d123103e223582332e5264d1479b0b4` | `e517f988452a50a2cf2ffd3347be8413e4626b70` | `30474840200/90653851248` success | `30475119979/90654788723` success |
| [#16](https://github.com/pityonother/Blueprint-to-Code/pull/16) | proposal-only Gold freeze | `083f384082a0126331b75abf95966b90833f99f9` | `ac45b49b1527ccc9ff4e0a365709d5fb257e496e` | `30474406652/90652376951` success | `30474601516/90653035818` success |
| [#17](https://github.com/pityonother/Blueprint-to-Code/pull/17) | add-only Blueprint TEST_ONLY foundation | `808ca5a39994f501223a45453a631b2ce302a76c` | `18ec8ce053b1b2ac898476972c6327e38ca0bffd` | `30475415487/90655783305` success | `30475663619/90656612985` success |
| [#18](https://github.com/pityonother/Blueprint-to-Code/pull/18) | atomic pointer CAS | `fcf4803fb17d4db521a3c9e9a81060d1302a73aa` | `48a9ffae3c50a2b028b65acd60fccdc77f0cb613` | `30475905972/90657415538` success | `30476198332/90658395412` success |
| [#19](https://github.com/pityonother/Blueprint-to-Code/pull/19) | exact narrow-gate diagnostic contract | `b673a022289b3a6748e58d2be0818da57cab13ba` | `f2ec952d9651f71bf2f87fe71140adade832f5b7` | `30478719053/90666885791` success | `30478922997/90667562194` success |
| [#20](https://github.com/pityonother/Blueprint-to-Code/pull/20) | UpdateBaseline / pre-publication inspection | `28097a4ea86517e9e8f1b0755a22dca3ea9c537c` | `b897dc241012ad258c7698afda439e8c4a6c37db` | `30490966900/90708460432` success | `30491179389/90709163502` success |

PR #9 还取得了独立的真实 Native Fixture 结果：

- PR run/job：`30455698437/90588581412`，success。
- post-main run/job：`30456073936/90589846172`，success。

PR #10–#20 没有修改 Native Fixture 触发范围，不能把普通 CI 写成新的
Native runtime evidence。

### 3. 已实现但不构成生产批准的合同与基础设施

- reviewer/operator registry v2 使用 Ed25519 public-key identity、角色、
  validity、revocation 和 registry entry binding。
- review receipt v2 对 canonical payload、pack/case/build、nonce、registry
  version和签名进行绑定；alias/shared-key、自审、replay、expired/revoked key
  和 tampering 均 fail closed。
- burn-in v2 verifier 要求每个 E4、rollback、concurrent-reader、
  shadow-diff 和 sealed snapshot chain 绑定到独立 artifact URI/SHA 和
  operator signature；它当前是只读 validator，没有 publication consumer。
- v1 review/burn-in 仅允许 fixture compatibility 或 diagnostics，不能使
  `cutoverEligible=true`。
- Gold freeze CLI 默认只生成 proposal；`--apply` 当前固定返回
  `BLOCKED_BY_SIGNED_FREEZE_APPROVAL`。仓库还没有
  `SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED` 所要求的 production
  provenance consumer，因此即使将来收到 signed receipts，当前工具也不能
  直接写 tracked Gold。
- worker terminal receipt 对 durable row-scope、changed table、source/fact/
  evidence identity 和终态进行验证；未知 backend/scenario 继续
  `BLOCKED_GAP`。
- add-only Blueprint 基础固定为 `TEST_ONLY`、`published=false`、
  `e4Scenario2Complete=false`。
- pointer CAS 使用 exact raw pointer SHA、expected build/manifest、持久 lock、
  atomic replace 和 post-write verification；区分 `NOT_REPLACED` 与
  `UNCERTAIN`。
- narrow-gate contract 有 11 个明确 gate ID，但固定为
  `ENGINEERING_DIAGNOSTIC`，不能作为 production authorization。
- UpdateBaseline 在明确的 cooperative-writer、ACL-protected-root 假设下
  现场捕获并复核 pointer/manifest；source manifest 和 source diff 均为
  不可变 canonical identity。该假设没有在本轮被独立证明。
- UpdateBaseline 固定为 `UNSIGNED_LOCAL_UPDATE_BASELINE`、
  `treeValidated=false`、`productionAuthority=false`。
- pre-publication receipt inspection 固定为
  `UNSIGNED_LOCAL_PREPUBLICATION_INSPECTION`、
  `baseBindingVerified=false`、`productionAuthority=false`。
- 新增 UpdateBaseline 增量路径的 whole-tree staging 和 additive
  quarantine 没有成功路径，均在任何文件系统副作用前 fail closed。

### 4. 当前真实 runtime snapshot

原子 pointer 是 `knowledge_base/vnext/current.json`，不是
`manifests/current.json` legacy fallback。

| 项目 | 当前核验值 |
| --- | --- |
| build ID | `20260729T115548-1a203b594bb6` |
| current.json raw SHA-256 | `d2700e48298f8c80806485c70ff54e9552db5e2dae2dfdc2222b64feb4831c2c` |
| snapshot manifest raw SHA-256 | `e82a50dd34b93f2649f3f1f7627c0b15f3b110c741939765fadbbdde3ea1c0da` |
| quality report raw SHA-256 | `bce31ecbe9e50a699b7acc7d5977b1865763c625e087211b9e523c9b814e65aa` |
| source manifest fingerprint | `74e6a62730da8518e6a1964efa62056b9bb3ab7ec37a0ce9e61b892e12df9bf4` |
| manifest runtimeHealth activeStaleSources | `0` |
| quality | `60 passed / 15 failed / 75` |
| sealed in snapshot manifest | `true` |
| quality cutoverEligible | `false` |
| mode | `shadow` |
| default query source | `legacy` |
| burn-in schema/status | `ark-kb-burn-in-attestation/v1 / MISSING` |
| burn-in gap | `BURN_IN_ATTESTATION_MISSING` |

运行 `VNextKnowledgeService(...).health()` 的真实结果：

```text
available=true
status=READY
freshness=FRESH
buildId=20260729T115548-1a203b594bb6
mode=shadow
defaultQuerySource=legacy
reason=15 critical quality gates remain open
```

`READY/FRESH` 只证明当前 shadow snapshot 可读取且未过期，不表示
production Gold、E4、burn-in 或 cutover 已通过。

### 5. Gold 数据的真实分类

- tracked benchmark：
  `tests/fixtures/kb_query_gold_set.v1.json`。
- raw SHA-256：
  `da6691d0725a9d7eddc88047deec1e37ca5be9566b2457947c49902c02326fdc`。
- 130 cases 中只有 `5` 个 `HUMAN_REVIEWED`，其余 `125` 个为
  `FIXTURE_EXACT`。
- 当前 production counts：query/registration/role = `5/0/0`。
- review packs 已生成：query `130`、registration `138`、role `360`。
- 当前 `automation:<id>` packs 仅是 diagnostic review material，不是
  production v2 packs 或 Gold；pack validator success 也不是人工 verdict。
- production v2 必须由外部可信 author fingerprint 重新导出 packs，现有
  automation provenance 不能升级或复用。
- PR #16 没有生成 review receipts、Gold patch 或 apply approval；
  `productionGoldWritten=false`。

## BLOCKED

### 1. 独立 Gold review

```text
BLOCKED_BY_INDEPENDENT_REVIEW
```

当前没有：

- 两轮真实独立 reviewer signed v2 receipts；
- human-managed trusted reviewer registry 和真实 private keys；
- 分歧 case 的第三方 adjudicator receipt；
- pack-author identity 和 signed freeze approval；
- query human Gold >=120；
- registration real Owner→Target Gold >=100；
- role Gold >=300 且 precision/recall >=0.95。

Gold apply 还明确缺少：

```text
BLOCKED_BY_SIGNED_FREEZE_APPROVAL
SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED
```

补齐人工 receipts 本身仍不足以让当前 CLI 写入 production Gold。

Codex 没有创建、模拟或代签 reviewer/adjudicator/operator。三个已知 mismatch
仍必须由真实独立 reviewer 裁决：

```text
negative-015-candidate-edge
registration-003-unverified-owner-target
relationship-003-harvest-component
```

### 2. 仍失败的 15 个 critical gates

```text
roles.independent_gold_set
registrations.real_relationship_gold_count
registrations.gold_precision
registrations.gold_recall
registrations.classification_precision
registrations.classification_recall
registrations.owner_resolution
registrations.target_resolution
registrations.edge_materialization
registrations.evidence_correctness
registrations.lineage_complete
queries.human_gold_cases
queries.corpus_ready_for_cutover
queries.protocol_compliance
queries.no_wrong_answers
```

没有降低、删除、重命名、重新解释任何 gate，也没有调整 Gold、precision、
recall、wrong-answer、stale-leak 或 `<250ms` 阈值。

### 3. 生产增量发布

```text
BLOCKED_BY_MISSING_AUTHORIZED_ADDITIVE_BLUEPRINT_EVIDENCE
BLOCKED_BY_MISSING_SIGNED_PRODUCTION_ARTIFACT_AUTHORIZATION
BLOCKED_BY_UNPROVEN_ADDITIVE_DERIVED_DEPENDENCY_SCOPE
BLOCKED_BY_MISSING_PRODUCTION_BACKEND_TERMINAL_RECEIPTS
BLOCKED_BY_REPARSE_SAFE_WHOLE_TREE_STAGING
BLOCKED_BY_REPARSE_SAFE_ADDITIVE_QUARANTINE
BLOCKED_BY_UNVERIFIED_DELTA_RECEIPT_BASE_BINDING
BLOCKED_BY_MISSING_PRODUCTION_NARROW_GATE_RUNNER
BLOCKED_BY_MISSING_REAL_INCREMENTAL_RUNTIME_EVIDENCE
BLOCKED_BY_MISSING_ATOMIC_INCREMENTAL_PUBLICATION
```

当前 live source URI 中没有一个可确认是经过授权、真实新增、适合生产发布的
Blueprint。fixture、synthetic Evidence、零字节文件或测试数据库都没有被替代
成 production input。

因此 Stage 14A 的真实验收尚未完成：

- 没有新的 immutable production snapshot；
- pointer 没有因增量流程前移；
- sealed semantic authority、manifest 和 pointer 没有被本轮 Stage 14
  增量流程原地改写；mutable `cache.sqlite` 是 disposable cache，不属于该
  声明；
- 没有 production backend terminal receipt set；
- 没有 independently verifiable publication receipt；
- `published=false`；
- `e4Scenario2Complete=false`。

`run_incremental_update()` 仍存在 candidate scan 发生在 incremental writer
lock 之前的 P0 integration gap。当前新增 primitives 没有被接入默认 runner，
也不能被描述为已经关闭该 gap。

Stage 14B 的 update/delete/rename 和 Stage 14C 的其余 E4 capabilities 依赖
Stage 14A 的真实安全闭环；本轮没有越过该依赖顺序扩展或伪造成功 backend。

### 4. burn-in 与最终切换

```text
BLOCKED_BY_MISSING_TRUSTED_BURN_IN_OPERATOR
BLOCKED_BY_MISSING_BURN_IN_EVIDENCE
SIGNED_BURN_IN_V2_REQUIRED
```

当前磁盘仅发现旧 build
`20260727T222549-a2d56bd7fed8` 的
`cutover_attestation.json`：

```text
schema=ark-kb-vnext-cutover-attestation/v1
sealedInSnapshotManifest=false
reportCutoverEligible=false
```

它不是 signed burn-in v2 evidence，不能透明升级，也不能授权 cutover。当前
burn-in v2 代码仍只是只读 verifier；还缺把有效 attestation 接入 snapshot
eligibility、build CLI、seal 和 current-pointer publication 的 reviewed
consumer。真实 operator/evidence 到位后，当前代码仍不能直接生成 ready
snapshot。当前还没有：

- 75/75；
- production incremental E4 12/12 的真实 signed receipts；
- 连续三轮 75/75 sealed shadow snapshots；
- 三轮 explicit previous-manifest chain；
- representative corpus 的 zero wrong/stale/candidate/undispositioned artifact；
- 真实 rollback 和 concurrent-reader drill receipts；
- trusted human operator 的 burn-in v2 signature；
- 第四个携带合格 attestation 的 ready snapshot。

所以 Stage 15 没有启动，`mode` 和默认 query source 没有改变。

## SECURITY / PROVENANCE RISKS

以下风险仍必须在后续审查中保持显式：

1. 测试中的 ephemeral Ed25519 keys、automation identities、fixture evidence
   都不能冒充 human reviewer/operator、production Gold 或 production
   authorization。
2. `contentSha256`、fingerprint、reviewer ID 或 self-hash 不能描述为数字签名。
3. 同一 key 的 reviewer alias、reviewer/adjudicator key reuse、self-review、
   receipt replay、revoked/expired key 必须继续 fail closed。
4. narrow-gate diagnostics 即使产生 11 个不同 digest，也不能排除“同一
   fixture 加盐”；真实 runner 必须重算并绑定语义输入和 durable state。
5. UpdateBaseline 只验证 pointer/manifest，不是 whole-tree attestation；
   该增量路径的 staging/quarantine 与 receipt base-binding 仍明确未实现。
   其 path-based sampling 没有 pin parent-directory handles，也不能抵御
   adversarial same-user parent-directory replacement；实际 root ACL 尚未由
   独立 artifact 证明。
6. 未支持 backend/scenario 必须 `BLOCKED_GAP`，不能以 no-op、planner output、
   cache hit、IntegrationBackend 或 fixture success 自证生产完成。
7. `cache.sqlite` 是 disposable runtime cache，不是 semantic authority。
8. 已发布 immutable snapshot 不得原地改写；任何新结果都必须新建、重封并
   通过 atomic pointer CAS 发布。
9. 后续 merge 必须继续保留已文档化 commit SHA，禁止 squash/rebase 改写
   审计链。
10. health `READY/FRESH`、性能通过或 GitHub CI success 都不能替代 Gold、
    E4 或 burn-in evidence。

## CHANGES

### PR #9：Stage 8–12 integration

- 将完整 stacked history 送入 `main`。
- 取得真实 CI 和 Native Fixture。
- 关闭/标记 superseded stacked PR，建立 shadow-only annotated tag。

### PR #10–#14：review 与 burn-in v2

- `scripts/blueprint_translator/kb_vnext/signed_receipts.py`
- trusted reviewer registry v2 schema 与 validator。
- Gold review v2 schema、canonical signed payload 和 replay protection。
- burn-in policy/attestation v2 schema、artifact binding 和 manifest chain。
- v1 compatibility 保留，但 production cutover eligibility 被禁止。
- benchmark/quality gate 对 unsigned Gold 的 fail-closed hardening。

### PR #16：Gold freeze proposal pipeline

- `scripts/freeze_ark_kb_gold_reviews.py`
- proposal/provenance schema、CLI、tests 和操作文档。
- 无 signed receipts 时只报告 blocker，不写 tracked Gold，不自动 commit/push。

### PR #15、#17、#18：增量基础

- worker durable row-scope 与 terminal receipt 验证。
- add-only Blueprint TEST_ONLY source/fact/evidence delta 和 invalidation
  foundation。
- atomic current pointer CAS、expected base/manifest、lock、post-write
  verification 和 rollback/publisher integration。

### PR #19：narrow-gate diagnostics

- `scripts/blueprint_translator/kb_vnext/narrow_gates.py`
- `schemas/kb_production_narrow_gate_report_v1.schema.json`
- `tests/test_kb_narrow_gates.py`
- `docs/ark_kb_vnext/STAGE14_NARROW_GATE_CONTRACT.md`
- 11 个 gate ID 均为 diagnostic-only，所有 publication/cutover flags 固定
  为 false/shadow/legacy。

### PR #20：UpdateBaseline / pre-publication inspection

- `scripts/blueprint_translator/kb_vnext/update_baseline.py`
- `scripts/blueprint_translator/kb_vnext/pointer_cas.py`
- `scripts/blueprint_translator/kb_vnext/source_manifest.py`
- `scripts/blueprint_translator/kb_vnext/incremental_delta.py`
- `tests/test_kb_update_baseline.py`
- `tests/test_kb_pointer_cas.py`
- `docs/ark_kb_vnext/STAGE14_UPDATE_BASELINE.md`

关键 hardening：

- builder 必须从真实 `snapshot_root` 现场 capture/revalidate，不能接受伪造的
  current baseline 值对象；
- `SourceManifest.entries` 必须是不可变 exact tuple；
- canonical source-diff serializer 只有一个；
- bounded pointer/manifest read 比较 pre-open/open-handle/post-read identity；
- raw receipt bytes 必须先匹配独立提供的 SHA，再验证 internal proof；
- production flag 必须是显式 bool，且 `production=true` 固定 blocker；
- staging/quarantine 在任何文件系统副作用前固定 blocker。

## VALIDATION

### 1. 本地合并后验证

在 PR #20 head `28097a4ea86517e9e8f1b0755a22dca3ea9c537c`
执行：

```powershell
python -m pytest -q
python -m pytest -q `
  tests/test_kb_pointer_cas.py `
  tests/test_kb_update_baseline.py `
  tests/test_kb_incremental_delta.py `
  tests/test_kb_narrow_gates.py `
  tests/test_update_ark_kb_vnext.py
python -m ruff check `
  scripts/blueprint_translator/kb_vnext/pointer_cas.py `
  scripts/blueprint_translator/kb_vnext/source_manifest.py `
  scripts/blueprint_translator/kb_vnext/incremental_delta.py `
  scripts/blueprint_translator/kb_vnext/update_baseline.py `
  tests/test_kb_pointer_cas.py `
  tests/test_kb_update_baseline.py
node tests/api_frontend_contract.mjs
node tests/frontend_core_contract.mjs
node tests/harvest_frontend_contract.mjs
npm run build
git diff --check origin/main HEAD
```

结果：

- full Python：`1463 passed, 4 skipped, 2 warnings, 662 subtests passed`；
- focused：`197 passed, 10 subtests passed`；
- Ruff：pass；
- frontend contracts：pass；
- TypeScript/Vite production build：pass；
- claim fixture normal/formal validation：pass，且仅作为 fixture；
- release/version/documentation consistency：pass；
- 本 PR 实现范围内的新 fresh-context audit findings 已闭合；已文档化的
  scan-before-writer-lock orchestration P0 gap 仍开放。

Windows 与 Linux 的平台 skip/subtest 数量不同。远端 GitHub Actions 的精确
结果是：

- PR #20：`1460 passed, 7 skipped, 2 warnings, 657 subtests passed`；
- post-main：`1460 passed, 7 skipped, 2 warnings, 657 subtests passed`。

### 2. PR #20 真实 GitHub evidence

PR-head run：

```text
run=30490966900
job=90708460432
head=28097a4ea86517e9e8f1b0755a22dca3ea9c537c
conclusion=success
```

post-main run：

```text
run=30491179389
job=90709163502
head=b897dc241012ad258c7698afda439e8c4a6c37db
conclusion=success
```

两次远端 run 的 full Python、frontend contracts、frontend build、claim
fixture normal/formal、release/version/documentation consistency 均为 success。

### 3. 可复核的 Git/GitHub 命令

```powershell
git fetch origin
git rev-parse refs/remotes/origin/main
git show --no-patch --pretty=raw b897dc241012ad258c7698afda439e8c4a6c37db
git tag -n ark-kb-vnext-shadow-stage12-20260729
gh pr view 20 --json state,headRefOid,mergeCommit,statusCheckRollup
gh run view 30490966900 --json headSha,status,conclusion,jobs
gh run view 30491179389 --json headSha,status,conclusion,jobs
```

Git clone 不包含被忽略的 runtime snapshots、private review packs、human
private keys 或受控 evidence workspace。缺失时必须报告输入/权限 blocker，
不得用 fixture 或 synthetic data 替代。

## 后续真实依赖顺序

1. 由真实人员管理 registry v2 identities 和 private keys。
2. 由外部可信 author fingerprint 重新导出 production v2 blind packs，
   收取两轮 signed review receipts；分歧由第三方 adjudicator 签署。
3. 先实现并审查 signed Gold provenance consumer 和独立 freeze approval
   consumer；当前 `--apply` 仍无条件阻断，不能仅凭 receipts 写 Gold。
4. consumer 闭合后再生成、人工批准并应用 Gold patch，重建 sealed shadow
   snapshot；任何 gate 失败继续 shadow/legacy。
5. 提供一个经过授权的真实 add-only Blueprint 和受控 evidence workspace。
6. 先关闭 writer-lock-before-scan、reparse-safe quarantine、whole-tree
   staging、base-bound signed delta receipt 和真实 backend receipts。
7. 完成真实 atomic incremental publication、post-publish health、rollback、
   concurrent-reader evidence，才可将 E4 scenario 2 记为完成。
8. 按依赖顺序完成 E4 其余 11 个场景，不以空 backend 或 test fixture 代替。
9. 实现并审查 signed burn-in v2 eligibility/seal/publication consumer；只有
   在 75/75、独立 Gold 阈值、E4 12/12 全部闭合后，才执行三轮 signed
   burn-in v2。
10. 只有携带三轮有效 chain/attestation 的第四个新 sealed snapshot 才允许
   评估 `mode=ready` 和 `defaultQuerySource=vnext`。

## CURRENT CUTOVER STATE

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy",
  "cutoverEligible": false
}
```

在 75/75、独立 Gold、E4 12/12 和三轮 signed burn-in 全部真实闭合前，
上述状态不得改变。
