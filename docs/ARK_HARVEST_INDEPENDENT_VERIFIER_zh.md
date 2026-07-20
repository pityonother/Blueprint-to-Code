# ARK 采集排行独立复算门禁

阶段 5 增加了一个与生产排行实现分离的验证器：

```powershell
python scripts\verify_ark_harvest_rankings.py --sample-size 128
```

它直接读取资源节点目录和采集评估目录，以另一套代码重新完成：

1. 驯服、骑乘和攻击可用性筛选；
2. DamageType 资源替换与父类链匹配；
3. 资源条目权重覆盖和归一化；
4. `engineComparisonIndex` 复算；
5. 同物种变体折叠与 Top 10 顺序；
6. 排名、未排名、不兼容和被范围排除的数量核对。

独立实现位于 `scripts/blueprint_translator/harvest_ranking_verifier.py`。该模块不导入或调用生产实现中的 `HarvestEvaluationEngine` 和 `evaluate_attack_resource`。CLI 只把生产查询接口当作黑盒，比较两边的结果。

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
- `1`：发现资格、系数或 Top 结果差异；
- `2`：输入目录无效、读取失败或验证器运行错误。

也可以用 `--reference-results <json>` 对比预先捕获的 API 结果。文件根对象以 `nodeId::nodeResourceId` 为键，值为对应的排行响应。这适合把生成和验证放进两个独立进程或 CI 作业。

这个门禁验证的是已恢复证据范围内的引擎系数和排序一致性，不会把 `engineComparisonIndex` 解释为游戏内实测每击产量或资源/秒。
