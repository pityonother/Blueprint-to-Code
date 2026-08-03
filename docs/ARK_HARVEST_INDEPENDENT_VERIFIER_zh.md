# ARK 采集排行独立复算门禁

阶段 5 增加了一个与生产排行实现分离的验证器：

```powershell
python scripts\verify_ark_harvest_rankings.py --sample-size 128
```

它直接读取资源节点目录和采集评估目录，以另一套代码重新完成：

1. 驯服、骑乘和攻击可用性筛选；
2. DamageType 资源替换与父类链匹配；
3. 资源条目权重覆盖和归一化；
4. 在有限节点上逐击复算 `estimatedYieldPerNode`；
5. 同物种变体折叠与 Top 10 顺序；
6. 排名、未排名、不兼容和被范围排除的数量核对。

独立实现位于 `scripts/blueprint_translator/harvest_ranking_verifier.py`。该模块不导入或调用生产实现中的 `HarvestEvaluationEngine` 和 `evaluate_attack_resource`。CLI 只把生产查询接口当作黑盒，比较两边的结果。

## 独立复算的完整节点模型

排行指标是“从一个全新节点采完后，目标资源的预计单位数”，不是每秒伤害或攻击速度。验证器按每次命中独立执行下面的静态模型：

1. 每击采集伤害为 `baseDamage * damageMultiplier`；
2. 使用标准化 `amountScale = 2`，所以发放阈值为 `HarvestHealthGiveResourceInterval / 2`；
3. 若组件开启 clamp，每击最多计入剩余节点生命；否则最终一击最多计入 `3.5 * 剩余节点生命`；
4. 将本击计入的生命损失累加，再取 `floor(accumulator / threshold)`；
5. 本击发放次数为 `trunc(HarvestQuantityMultiplier * rawGrantUnits)`；
6. 发生发放时清空 accumulator，包括未达到下一阈值的余数；
7. 线性 `OverrideQuantityRandomPower = 1` 时，单次选择的期望数量为 `(min + max) / 2`；
8. 最终 `estimatedYieldPerNode = grantCalls * 目标资源权重占比 * 单次选择期望数量`。

`AttackInterval` 仅保留作诊断信息，不参与完整节点产量或排行。因此 `0.01` 秒不会凭空产生 100 倍的节点总产量。

以下分支尚不能由静态证据可靠复算，会明确标记为未排名，而不是猜一个分数：

- `bIsSingleUnitHarvest = true`；
- `DamageHarvestAdditionalEffectiveness != 0`；
- `OverrideQuantityRandomPower != 1`；
- `bUseBlueprintAdjustOutputDamage = true`。

默认使用固定种子做 32 个节点-资源目标的确定性抽样。验收时可以扩大样本：

```powershell
python scripts\verify_ark_harvest_rankings.py `
  --sample-size 128 `
  --seed phase5-acceptance-v1 `
  --output analysis\harvest_rankings\harvest_ranking_independent_verification.json
```

需要完整复算时使用：

```powershell
python scripts\verify_ark_harvest_rankings.py --all
```

输出是结构化 JSON，包含输入 SHA-256、样本键、对比数量和最多 100 条差异。退出码定义如下：

- `0`：全部对比通过；
- `1`：发现资格、预计节点产量或 Top 结果差异；
- `2`：输入目录无效、读取失败或验证器运行错误。

也可以用 `--reference-results <json>` 对比预先捕获的 API 结果。这适合把生成和验证放进两个独立进程或 CI 作业。参考文件格式取决于排行合同版本。

旧合同/v1 使用 flat 根对象，以 `nodeId::nodeResourceId` 为键，值为对应的完整排行响应：

```json
{
  "metal-node::metal-entry": {
    "items": []
  }
}
```

v2 必须先按正向方向和 metric 分桶，再以 `nodeId::nodeResourceId` 为键：

```json
{
  "forward": {
    "staticCompleteNodeTargetYield": {
      "metal-node::metal-entry": {
        "confirmedItems": [],
        "conditionalItems": []
      }
    },
    "staticYieldPerAttackCycleSecond": {
      "metal-node::metal-entry": {
        "confirmedItems": [],
        "conditionalItems": []
      }
    }
  }
}
```

没有 `--runtime-observations` 时，两个 observed metric 会明确标记为 `SKIPPED_WITH_REASON`，静态验证文件仍必须同时提供上述两个静态 metric 桶。提供受控 runtime observation 后，恰好一个非 synthetic profile 会自动选择；多个 profile 必须明确指定。此时还必须用同一结构提供 `observedYieldPerNode` 与 `observedYieldPerSecond` 桶。捕获文件模式当前不提供 reverse specialties 回调，因此 reverse coverage 会明确跳过。

v2 黑盒对比会逐 metric 核对 selected value、Top-N 顺序、row `scoreBasis` 与 `scoreBreakdown.metric`；反向专长 row 也使用同一套四 metric 合同。独立实现自行执行 runtime profile 单选、单 profile 自动选择、synthetic/preliminary/status 防线，并在应用 `TAMED_RIDDEN` 前独立统计完整 canonical 资产/物种审计。`engineComparisonIndex` 只是过渡兼容字段：参考结果可以不提供；如果仍提供，其数值必须与静态完整节点指标完全一致，不能成为另一套冲突的排行指标。

这个门禁验证的是已恢复静态证据范围内的完整节点预计产量与排序一致性。运行时 Blueprint、Buff、基因、任务和服务器倍率仍不在这个静态模型内，因此后续游戏实测用于校准模型边界，而不是把当前结果解释成资源/秒。

必须把门禁边界写成下面两句，而不能把 `mismatchCount = 0` 扩大解释为游戏真实性：

```text
production implementation == independent implementation
static model != real game proof
```

也就是说，零差异只证明生产实现与独立复算实现对同一静态合同给出了相同结果；它不证明 `static model == real game`。后者仍需要合法、`synthetic=false` 的 runtime observation。
