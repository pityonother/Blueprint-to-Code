# Runtime Calibration v1

> 本文的 observation-set v1 继续用于静态模型校准比较。Harvest Ranking Contract v2 的公开排行 overlay 使用更强的精确身份 schema `schemas/harvest_runtime_observation_v2.schema.json`；两者不会互相冒充。参见 [HARVEST_RUNTIME_TEST_PROTOCOL_zh.md](HARVEST_RUNTIME_TEST_PROTOCOL_zh.md)。

Runtime calibration 是静态证据之上的独立观察层。它不会改写 Blueprint Evidence Store、Native Evidence Store 或静态 Harvest 模型；它只回答“在一组明确记录的环境与 trials 中，观察结果是否支持当前静态预测”。

## 状态

| 状态 | 含义 |
|---|---|
| `STATIC_REVERSED` | 有静态预测，但没有 runtime trial |
| `INSUFFICIENT_OBSERVATIONS` | trial 太少，不能确认 |
| `RUNTIME_CALIBRATED` | 小样本落在容差内 |
| `RUNTIME_CONFIRMED` | 达到最小 trial 数且落在容差内 |
| `RUNTIME_DIVERGED` | 观察均值超出容差 |
| `UNSUPPORTED_DYNAMIC_BRANCH` | 已知 runtime hook/动态分支未建模；不生成伪分数 |

这些状态只描述一个 observation set。即使状态为 `RUNTIME_CONFIRMED`，也不代表所有服务器倍率、Mod、地图、节点或游戏版本都相同。

## Schema

权威 schema：

```text
schemas/runtime_observation_set_v1.schema.json
```
每个 observation set 必须记录：

- `observationSetId`；
- 是否为 `synthetic`；
- game build、服务器设置、Mod、地图与备注；
- node/resource/species/attack；
- 静态模型版本与输入；
- 尚未支持的 runtime branches；
- 容差策略；
- 每个 trial 的击打记录和最终资源数量。

仓库内的 `tests/fixtures/runtime_observations/` 全部是 synthetic fixture，不是 ARK 实测结果。

## 运行比较

```powershell
.\runtime\python\python.exe scripts\compare_runtime_observations.py `
  tests\fixtures\runtime_observations\harvest-linear-match.json `
  --pretty
```

同时写 JSON 与 Markdown：

```powershell
.\runtime\python\python.exe scripts\compare_harvest_runtime_observations.py `
  <observation.json> `
  --json-out <comparison.json> `
  --markdown-out <comparison.md> `
  --pretty
```

比较器输出预测值、trial 数、均值、方差、绝对/相对误差、实际容差和最终状态。若 `unsupportedDynamicBranches` 非空，`estimatedYieldPerNode` 与 `comparison` 会保持 `null`。

## Synthetic fixture 覆盖

- 线性数量分布完全匹配；
- clamped final hit；
- native static profile 的 unclamped `3.5 × remaining health` 分支；
- `floor` 与 quantity multiplier `trunc` 边界；
- 明确 mismatch；
- unsupported Blueprint hook 不产生伪分数。

运行测试：

```powershell
.\runtime\python\python.exe -m unittest discover `
  -s tests -p "test_runtime_calibration.py"
```
