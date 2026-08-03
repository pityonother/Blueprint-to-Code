# ARK Harvest 排名支配性审计

此审计用于回答“一个物种为什么在大量节点资源上成为第一”，不修改排名公式或排序结果。规范入口：

```powershell
python scripts\audit_ark_harvest_rankings.py `
  --node-catalog analysis\harvest_nodes\resource_node_catalog.json `
  --evaluation-catalog analysis\harvest_rankings\harvest_evaluation_catalog.json `
  --sqlite-catalog analysis\harvest_nodes\harvest_catalog.sqlite `
  --species dreadnoughtus `
  --json-out analysis\harvest_rankings\audits\dreadnoughtus-dominance.json `
  --markdown-out analysis\harvest_rankings\audits\dreadnoughtus-dominance.md
```

`analysis/` 继续由 Git 忽略；仓库只提交审计代码、合同文档和小型 fixture，不提交本机真实审计结果。

## 两种统计口径

审计同时报告：

1. `node/resource occurrence`：每个精确 node、resource、entryIndex 的出现；
2. `unique evaluation key`：`HarvestComponent + resource identity + entryIndex + usage scope + model version + policy version`。

多个节点可以复用同一 Component/resource/entry 计算。两种口径分开后，不会把同一份计算重复包装成许多独立算法胜利。执行器每次只计算一个 unique key，最多保留该键的 Top 10，不构造 species × node/resource 的无限笛卡尔积。

## 输入身份和失败关闭

开始计算前必须同时满足：

- SQLite 记录的 canonical JSON SHA-256 与当前 node catalog 一致；
- node catalog 的 evaluation/component revision 与 evaluation catalog 一致；
- extractor、formula/model 和 ranking policy version 都等于当前代码合同；
- 三个输入文件都有可复核 SHA-256。

任何 tamper、交叉 revision 漂移或旧 model/policy/extractor 都会失败关闭，要求先通过规范 staging rebuild 和原子提升修复数据。

## 根因与边界

每个目标物种榜首案例包含 exact node、resource entry、HarvestComponent、variant、attack、DamageType chain、模型输入、score breakdown、第二/第三名差异、map evidence、tie 状态和当前 policy 的选择理由。根因可以是多项叠加，不能只写“分数高”。

独立 verifier 的边界固定为：

```text
production implementation == independent implementation
```

它不证明：

```text
static model == real game
```

因此 audit 中的静态第一名不能自动改称“实战最佳采集生物”；runtime 真实性只能来自合法、非 synthetic 的受控 observation。
