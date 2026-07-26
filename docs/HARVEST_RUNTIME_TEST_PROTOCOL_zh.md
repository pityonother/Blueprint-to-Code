# ARK Harvest Runtime 实测协议

本协议用于采集真实 observation。仓库当前只提交 synthetic fixtures；没有完成下面的固定条件和记录步骤时，不得把观察值写成 `RUNTIME_CONFIRMED`。

## 1. 固定环境

每轮采样前记录：

- 游戏或 DevKit build；
- 地图；
- 单机/服务器类型；
- `HarvestAmountMultiplier` 与其他资源倍率；
- 生物等级、属性、Buff、基因、鞍具和装备；
- 完整 Mod 列表及版本；
- 节点类、节点最大生命与资源 entry；
- 是否存在任务、区域、天气或服务器插件 hook。

不得在同一 observation set 中混合不同 build、倍率或 Mod 组合。

## 2. 固定对象

为一个 observation set 固定：

- 一个新鲜、完整生命的节点类型；
- 一个目标资源；
- 一种生物；
- 一种攻击；
- 相同的攻击基础伤害与 harvesting multiplier。

每个 trial 必须使用新的完整节点。不要把半残节点的结果与完整节点结果混在一起。

## 3. 每击记录

建议至少记录：

```json
{
  "hitIndex": 1,
  "nodeHealthBefore": 100.0,
  "nodeHealthAfter": 50.0,
  "damageShown": 50.0,
  "resourceUnitsGranted": 2,
  "notes": ""
}
```

若界面无法直接显示节点生命，明确写入 `notes`，不要填入猜测值。

## 4. Trial 数

- 冒烟检查：1 次，只能证明采集流程可用；
- 初步校准：至少 2 次；
- 当前默认确认门槛：至少 3 次；
- 有随机数量范围时建议 20 次以上，并保留原始 trial，不只保存平均数。

样本量门槛由 observation 的 `policy.minimumTrialsForConfirmation` 显式记录。

## 5. 导入

复制一个 synthetic fixture 作为字段模板，但必须：

1. 把 `synthetic` 改为 `false`；
2. 使用新的 `runtime://` ID；
3. 填入真实环境和每个 trial；
4. 不删除异常 trial；在 `notes` 说明异常条件；
5. 若存在未建模 hook，把名称写入 `unsupportedDynamicBranches`。

运行：

```powershell
.\runtime\python\python.exe scripts\compare_harvest_runtime_observations.py `
  <真实-observation.json> `
  --json-out <comparison.json> `
  --markdown-out <comparison.md> `
  --pretty
```

## 6. 结果判读

- `RUNTIME_DIVERGED` 是调查入口，不自动证明静态反编译错误；先检查倍率、节点生命、Buff/Mod、随机 entry 和 build。
- `UNSUPPORTED_DYNAMIC_BRANCH` 表示当前模型不支持该分支，不能用 0 或空数组代替。
- `RUNTIME_CONFIRMED` 只确认本 observation set 中的记录条件。
- 原始 observation 与 comparison 应一起保留，报告 claim 只引用其 `runtime://` ID。

