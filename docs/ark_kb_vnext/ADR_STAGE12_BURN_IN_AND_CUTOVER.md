# ADR: vNext burn-in 与最终切换

- 状态：Accepted
- 日期：2026-07-29
- 适用版本：`ark-kb-burn-in-policy/v1`

## 背景

75 个质量门是发布前的必要条件，但一次 `75/75` 不能证明连续构建、
legacy/vNext 差异处置、回滚和并发 reader 已经经过真实运行验证。旧的可变
gate publisher 还曾允许仅凭外部报告把 legacy 布局标为 `ready/vnext`，
这与“最终切换只能由新的 immutable snapshot 完成”的规则冲突。

当前真实环境没有满足本 ADR 的 burn-in 记录，也没有足够的独立 gold。
因此当前状态是：

```text
BLOCKED_BY_INDEPENDENT_REVIEW
BLOCKED_BY_INCOMPLETE_PRODUCTION_INCREMENTAL
BLOCKED_BY_MISSING_BURN_IN_EVIDENCE
mode=shadow
defaultQuerySource=legacy
```

## 决策

新的 immutable snapshot 只有同时满足以下两层条件，才能密封为
`ready/vnext`：

1. 质量报告包含完整且未削弱的 75 门，所有 critical gates 通过。
2. 构建时显式提供、校验并复制一份
   `ark-kb-burn-in-attestation/v1`，其 SHA-256 密封进同一个
   snapshot manifest。

没有 attestation 时，即使质量报告为 `75/75`，manifest 也必须记录：

```json
{
  "qualityGates": {
    "qualityReportCutoverEligible": true,
    "cutoverEligible": false
  },
  "burnIn": {
    "status": "MISSING",
    "gapCode": "BURN_IN_ATTESTATION_MISSING"
  },
  "cutover": {
    "mode": "shadow",
    "defaultQuerySource": "legacy"
  }
}
```

可变或事后生成的 gate report 只能作为 diagnostics，不能切换默认来源。
最终切换必须重新执行 full snapshot build，并通过
`--burn-in-attestation` 在发布前密封证据；禁止修改旧 manifest 或 current
指向的 snapshot 内容。

## Burn-in 合同

attestation 必须证明：

- 至少 3 个不同的 sealed shadow snapshots 连续通过全部质量门；
- 每个 snapshot 的质量报告 SHA-256 已记录，且
  `qualityReportCutoverEligible=true`、`sealedInSnapshotManifest=true`；
- representative shadow corpus 的 wrong answer、stale leak 和 candidate
  completion 均为 0；
- legacy/vNext diff 已全部 disposition，未处置数量为 0；
- rollback drill 通过，from/to build 不同；
- current pointer 并发 reader drill 没有 mixed-build observation；
- 生产增量的 12 个场景全部通过，包括新增、修改、删除、失败回滚、并发
  reader 和 unchanged cache hit。

这里使用 `qualityReportCutoverEligible` 而不是
`qualityGates.cutoverEligible` 来引用前三个 shadow builds。后者还需要
burn-in，本身会形成循环依赖。第四个新 build 才能在引用前三个通过质量门
的 shadow builds 后成为 `ready/vnext`。

构建器会从现有 `snapshots/<buildId>/manifest.json` 重新读取历史，要求
attestation 中的 build 顺序正好等于最新的连续 builds，并重算每份密封
quality report 的 SHA-256。仅填写三个看似合法的 build ID 或 hash 不能
通过生产构建。

12 个生产增量场景比最小 burn-in 建议更严格。原因是当前系统仍有 9 个
backend 属于 `BLOCKED_GAP`；在这些场景获得真实 production receipt 前，
不能把 fixture、planner 或 staging-copy 测试当作生产增量完成。

## 人工与证据边界

- `review.reviewerType` 必须为 `HUMAN_OPERATOR`，但仓库不会生成 reviewer
  身份或签名。
- Codex 不创建生产 attestation，不把 fixture attestation 当作 burn-in。
- 测试中的 attestation 明确标记 `fixture-only`，仅验证 fail-closed 逻辑。
- 缺少真实人工复核、真实 receipts 或三次通过的 sealed builds 时，保持
  `BLOCKED_BY_MISSING_BURN_IN_EVIDENCE`。

## 回滚

legacy 数据和 reader fallback 必须保留。回滚演练使用已发布的 immutable
build ID，并在原子替换 current pointer 前后记录：

- from/to build ID；
- manifest 与质量报告 SHA-256；
- 并发 reader mixed-build observation 数量；
- 完成时间和人工 operator。

只有 `mixedBuildObservations=0` 的演练才能进入 attestation。回滚不得删除
新旧 snapshot，也不得改写任一 immutable manifest。

先进行只读预检：

```powershell
python scripts/rollback_ark_kb_vnext.py `
  --snapshot-root knowledge_base/vnext `
  --to-build-id <target-build-id> `
  --expected-current-build-id <current-build-id> `
  --dry-run
```

确认 receipt、并发 reader 和 operator 记录后，去掉 `--dry-run` 才执行原子
pointer swap。命令要求显式 expected current；验证期间 current 发生变化会
fail closed。

## 结果

这项决策会让“75 门通过但缺少运行证据”的 build 继续处于 shadow；这是
预期的 fail-closed 行为。只有一个新的、发布前已密封质量报告和 burn-in
证据的 snapshot 可以切换 vNext，legacy fallback 始终保留。
