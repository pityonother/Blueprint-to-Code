# 巨盗龙蓝图玩家向分析

资产：`/Game/ASA/Dinos/Gigantoraptor/Gigantoraptor_Character_BP.Gigantoraptor_Character_BP`

关联羽毛道具：`/Game/ASA/Dinos/Gigantoraptor/PrimalItemResource_GigantoraptorFeather.PrimalItemResource_GigantoraptorFeather`

## 读取覆盖情况

当前工具已经从 `.uasset` 直接读取到：

| 项目 | 数值 |
| --- | ---: |
| 巨盗龙图页 | 109 |
| 巨盗龙节点 | 4631 |
| 巨盗龙 Pins | 15242 |
| 巨盗龙连线 | 14629 |
| 完整图页 | 106 |
| 部分图页 | 3 |

还需要手动补采或后续规则增强的图页：

| 图页 | 状态 |
| --- | --- |
| `CanNurseDino` | partial |
| `ShouldPreventHibernation` | partial |
| `UserConstructionScript` | partial |

这不影响对主要玩法系统的判断。巨盗龙的核心系统，包括羽毛、育婴、训练、蛋、巢、羁绊、乘客、交互菜单，都已经读到主要逻辑。

## 一句话结论

巨盗龙不是主战输出生物，它的主要价值是 **育种、幼崽管理、蛋/巢互动、羁绊训练、拔羽毛影响属性继承概率**。

玩家最大收益方向不是拿它打架，而是围绕高属性个体建立育种辅助体系。

## 核心玩家价值

| 方向 | 价值 |
| --- | --- |
| 羽毛 | 从巨盗龙身上拔取，记录该个体最高属性，并影响对应属性继承权重 |
| 育种 | 通过羽毛提高特定属性继承概率，适合追求高血、高攻、高耐等种龙线 |
| 幼崽训练 | 参与 baby training、bonded dino、imprint 相关逻辑 |
| 蛋和巢 | 有野外巢、蛋搜索、孵化、破蛋、生成幼崽相关逻辑 |
| 幼崽携带 | 读取到 baby passenger、乘客保护、缩放、座位逻辑 |
| HUD | 有专门 HUD 元素显示羁绊、训练、蛋槽等信息 |
| 战斗 | 基础伤害较低，不是主要收益点 |

## 关键默认数值

### 羽毛

| 字段 | 数值 |
| --- | ---: |
| `FeatherItemClass` | `PrimalItemResource_GigantoraptorFeather_C` |
| 每次拔毛数量 | 1 |
| 普通/服务器拔毛冷却 | 28800 秒，8 小时 |
| 单机拔毛冷却 | 7200 秒，2 小时 |
| 冷却记录变量 | `LastFeatherPluckTime` |

### 羽毛道具本身

| 字段 | 数值 |
| --- | ---: |
| 名称 | `Gigantoraptor Feather` |
| 描述 | `A feather plucked from a Gigantoraptor.` |
| 默认数量 | 1 |
| 最大堆叠 | 1 |
| 动态名称 | true |
| 动态描述 | true |
| 动态图标 | true |
| `DistributionForMaxWeight` | 0.5 |
| `InheritStatWeightMinMax.X` | 0.55 |
| `InheritStatWeightMinMax.Y` | 0.75 |

### 羁绊和训练

| 字段 | 数值 |
| --- | ---: |
| `MaxBondedDinos` | 12 |
| `MaxBondedStacks` | 25 |
| `BondedDinoRefreshRadius` | 10000 |
| `BondedStacksDecreaseInterval` | 60 秒 |
| `BondedPassImprintMultiplier` | 0.3 |
| `BaseTrainingLevels` | 3 |
| `ExtraTrainingEveryNumBabyLevels` | 25 |
| `BabyTrainingFindTargetMinMax` | 30 到 30 秒 |
| `BabyTrainingExpireDuration` | 300 秒 |
| `TrainingTargetLevelDiffAgainstMaxRange` | 0.4 |

### 蛋、巢、幼崽

| 字段 | 数值 |
| --- | ---: |
| `WanderEggSearchRadius` | 6000 |
| `WildNestSpawnInterval` | 7200 秒 |
| `SpawnNestMaxAttempts` | 8 |
| `WanderMaxDistFromNest` | 1000 |
| `ForceBabyRunOutsideDistanceFromNest` | 1250 |
| `AdultReturnToNestIntervalMinMax` | 25 到 35 秒 |
| `BabyReturnToNestIntervalMinMax` | 15 到 25 秒 |
| `ExReturnToNestIntervalFromEgg` | 10 秒 |
| `BabyPassengerCarrySizeLimit` | 85 |
| `BabyMaturationDuration` | 函数读取到，但具体最终值依赖图内计算和外部数据 |

### 移动和战斗

| 字段 | 数值 |
| --- | ---: |
| `MeleeDamageAmount` | 25 |
| `AttackRange` | 850 |
| `MeleeSwingRadius` | 275 |
| `AttackInterval` | 5 秒 |
| `RiderAttackInterval` | 5 秒 |
| `RunningSpeedModifier` | 5.473599910736084 |
| `TamedRunningSpeedModifier` | 0.8999999761581421 |
| `UntamedRunningSpeedModifier` | 0.800000011920929 |
| `SlowFallingStaminaCostPerSecond` | 7.5 |
| `MaxFallSpeed` | 1750 |
| `FallDamageMultiplier` | 40 |

### 驯服相关

| 字段 | 数值 |
| --- | ---: |
| `RequiredTameAffinity` | 2400 |
| `RequiredTameAffinityPerBaseLevel` | 100 |
| `TamingPlayerMatchAnimWithinDuration` | 3 秒 |
| `BabyTamingWarningDuration` | 3 秒 |
| `TameIneffectivenessByAffinity` | 1.0 |
| `KillXPBase` | 18 |

## 羽毛如何决定影响哪项属性

拔羽毛时，巨盗龙蓝图调用 `GetFeatherCustomData`。

逻辑是：

```text
对属性编号 0..11 循环：
  points = GetLevelUpPoints(ConvertIntToCharacterStatusEnum(index), bTamedPoints=false)

找出 points 最高的属性。
如果多个属性并列最高，则从并列属性里随机选一个。
```

然后写入羽毛的 `StatInfo`：

```text
StatInfo[0] = 最高属性的属性编号
StatInfo[1] = 该属性的点数
StatInfo[2] = GetDinoStatDistributionAgainstMax(该属性, false, false, false)
```

当前能稳定读到的属性名：

| 编号 | 属性 |
| ---: | --- |
| 0 | Health |
| 1 | Stamina |
| 2 | Torpidity |
| 3 | Oxygen |
| 4 | Food |
| 5 | Water |
| 6 | Temperature |
| 7-11 | 图里存在范围检查，但当前解析器没有稳定恢复显示名 |

注意：`GetLevelUpPoints(..., bTamedPoints=false)` 表示它看的是原始/野生/出生属性点，不是玩家后续手动加点。

所以玩家后天升级不应该提高羽毛质量。要刷好羽毛，应该靠高出生属性、高野生点个体。

## 羽毛继承权重公式

羽毛道具蓝图 `PrimalItemResource_GigantoraptorFeather` 实现了 `BPOverrideInheritedStatWeight`。

核心公式是：

```text
Weight = MapRangeClamped(
  StatInfo[2],
  0.0,
  DistributionForMaxWeight,
  InheritStatWeightMinMax.X,
  InheritStatWeightMinMax.Y
)
```

代入当前资产读到的默认值：

```text
DistributionForMaxWeight = 0.5
InheritStatWeightMinMax = (0.55, 0.75)
```

所以公式可以写成：

```text
Weight = clamp(0.55 + StatInfo[2] / 0.5 * 0.20, 0.55, 0.75)
```

也就是：

```text
Weight = clamp(0.55 + StatInfo[2] * 0.4, 0.55, 0.75)
```

换算表：

| `StatInfo[2]` 属性分布值 | 羽毛继承权重 |
| ---: | ---: |
| 0.00 | 55% |
| 0.10 | 59% |
| 0.25 | 65% |
| 0.40 | 71% |
| 0.50 | 75% |
| 大于 0.50 | 75%，封顶 |

所以这个羽毛的结论是：

```text
下限：55%
上限：75%
吃满上限所需分布值：0.5
```

## 羽毛生效条件

`BPOverrideInheritedStatWeight` 里还读到了这些检查：

```text
不是蓝图物品
不是 Engram
存在 StatInfo
StatInfo 至少有 3 个值
StatInfo[0] 在 0..11 范围内
StatInfo[1] > 0
GetCanMutateStat(StatInfo[0]) = true
```

通过检查后，才会返回上面的权重。

## 原生函数读取结果

继续追 `GetDinoStatDistributionAgainstMax` 后，已经从 DevKit 的 `ShooterGameEditor-ShooterGame.dll` 里反汇编到函数体。

函数签名：

```text
UPrimalCharacterStatusComponent::GetDinoStatDistributionAgainstMax(
  EPrimalCharacterStatusValue::Type valueType,
  bool bTamedPoints,
  bool bCheckLevel,
  bool bIncludeMaxTamingEffLevels
) -> float
```

羽毛生成时调用的是：

```text
GetDinoStatDistributionAgainstMax(目标属性, false, false, false)
```

所以羽毛这一路走的是野生属性点，不是驯养后手动加点。

对羽毛而言，公式可以写成：

```text
StatInfo[2] = clamp(
  WildLevelUpPoints[目标属性] / (GetScaledMaxWildSpawnLevel() - 1),
  0,
  1
)
```

如果服务器最大野生等级是 150：

```text
StatInfo[2] = 野生属性点 / 149
```

再代入羽毛继承权重：

```text
Weight = MapRangeClamped(StatInfo[2], 0, 0.5, 0.55, 0.75)
```

也就是：

```text
Weight = clamp(0.55 + StatInfo[2] * 0.4, 0.55, 0.75)
```

如果按常见 150 最大野生等级计算：

| 目标属性野生点 | StatInfo[2] | 羽毛继承权重 |
| ---: | ---: | ---: |
| 30 | 0.2013 | 63.05% |
| 40 | 0.2685 | 65.74% |
| 50 | 0.3356 | 68.42% |
| 60 | 0.4027 | 71.11% |
| 70 | 0.4698 | 73.79% |
| 75 | 0.5034 | 75.00% |

这里的 75 点已经超过 `StatInfo[2] = 0.5` 的吃满线，所以权重封顶 75%。

如果服务器最大野生等级不是 150，只要替换分母：

```text
分母 = 服务器最大野生等级 - 1
StatInfo[2] = 野生属性点 / 分母
吃满点数 = ceil(分母 * 0.5)
```

例如最大野生等级 180 时，分母是 179，吃满需要约 90 点。

也就是说，现在可以确定：

- 羽毛根据最高原始属性决定影响哪项属性。
- 羽毛用该属性的分布值计算继承权重。
- 继承权重范围是 55% 到 75%。
- 分布值达到 0.5 就吃满。
- 后天加点不提高羽毛质量，因为羽毛传入的是 `bTamedPoints=false`。

## 玩家最大收益策略

1. 不要从低属性个体身上拔羽毛。
2. 优先保留高出生点、高野生点的巨盗龙。
3. 每只巨盗龙的最高属性决定羽毛影响哪项继承。
4. 如果最高属性并列，羽毛属性会随机落在并列项之一，所以想稳定出某项羽毛，最好让目标属性明显高于其他属性。
5. 后天加点大概率没有意义，因为蓝图读取的是 `bTamedPoints=false`。
6. 服务器每 8 小时拔一次，单机每 2 小时拔一次。
7. 羽毛不能堆叠，每根都有独立 `StatInfo`，要按属性和质量分类保存。
8. 最值得刷的羽毛是你育种目标属性对应的高分布羽毛，比如高血、高耐、高攻等。
9. 如果目标是稳定高收益，应建立多只高目标属性巨盗龙轮流拔毛。
10. 分布值达到 0.5 后已经封顶，继续提高源个体属性可能不再提高羽毛继承权重，但仍可能帮助稳定该属性成为最高属性。

## 巨盗龙不是主战生物的原因

战斗相关默认值显示它不像主力输出：

```text
MeleeDamageAmount = 25
AttackInterval = 5
RiderAttackInterval = 5
```

相比战斗，它的蓝图节点大量集中在：

- `BPTryMultiUse`
- `BPGetMultiUseEntries`
- `RefreshBabyTraining`
- `RefreshBondedDinos`
- `AttemptBondWithDino`
- `AttemptSpawnNest`
- `SearchForEggs`
- `GetFeatherCustomData`
- `BPOverrideInheritedStatWeight`

所以它更像育种和幼崽管理工具，而不是打架工具。

## 我为什么需要反复阅读很多次

你的问题其实一直在逐步收窄：

1. 一开始你问“这个蓝图是什么”。
2. 然后问“作为玩家怎么最大化收益”。
3. 接着问“羽毛具体数据”。
4. 最后才明确问“巨盗龙某项属性和羽毛继承概率的具体公式、上下限”。

这些问题看起来都和巨盗龙有关，但需要读取的东西不一样：

| 你问法 | 我需要读什么 |
| --- | --- |
| 这个蓝图是什么 | 全局报告、行为总结、图页分布 |
| 玩家怎么收益最大 | 默认值、核心系统、交互图、育种相关图 |
| 羽毛有什么用 | 巨盗龙的拔毛逻辑、羽毛道具蓝图 |
| 羽毛具体数据 | 羽毛 CDO 默认值、`StatInfo`、显示函数 |
| 属性和继承概率公式 | `GetFeatherCustomData` + `BPOverrideInheritedStatWeight` + 原始结构字节 |

真正关键的是：你想问的是“玩家结果公式”，不是“蓝图功能介绍”。如果一开始这么说，我就会直接读公式链路，而不是先做大范围行为分析。

## 以后可以直接这样问

下面这个 prompt 比较适合你后续分析别的蓝图：

```text
请读取这个 ARK/ASA 蓝图资产：

<这里填 Blueprint Object Path>

我的目标不是普通代码解释，而是玩家向机制分析。请直接读取 `.uasset/.uexp`、class defaults、图页节点、变量默认值、函数调用和相关道具蓝图。

请优先回答：

1. 这个生物/物品对玩家有什么实际用途。
2. 哪些机制能带来收益，收益怎么最大化。
3. 关键变量的具体数值、冷却、范围、上限、下限。
4. 如果涉及概率、权重、品质、经验、掉落、继承、驯服、成长，请追到具体公式。
5. 如果公式跨到另一个道具、buff、inventory、supply crate、原生函数或父类函数，请继续追相关资产。
6. 请区分：
   - 已经从资产直接读到的事实
   - 从蓝图连线推断出的结论
   - 还需要 DevKit/运行样本/原生函数确认的部分
7. 最后用玩家视角给出操作建议：
   - 我应该刷什么
   - 怎么刷
   - 什么属性最值得
   - 什么行为没有收益
   - 哪些数值达到上限后不必继续堆

请不要只总结图页名字，要把相关蓝图链路读穿。如果某个值现在读不到，请告诉我缺的是哪个函数、哪个资产或哪类样本。
```

如果你专门要问公式，可以用更短的版本：

```text
请不要先做泛泛介绍，直接追这个机制的公式：

机制：<例如 巨盗龙羽毛影响属性继承概率>
资产：<Blueprint Object Path>

我要知道：

1. 输入变量是什么。
2. 输入变量来自哪里。
3. 计算公式是什么。
4. 上限和下限是多少。
5. 哪些条件会导致公式不生效。
6. 玩家能否通过加点、等级、品质或其他操作提高结果。
7. 哪一步是蓝图里直接读到的，哪一步需要原生函数或运行样本确认。
```

## 后续最值得继续追的点

如果要把巨盗龙机制彻底拆干净，下一步最值得做三件事：

1. 追 `GetDinoStatDistributionAgainstMax` 的实现或样本数据，补全“属性点数到分布值”的公式。
2. 追实际消耗羽毛的系统，确认它在哪个育种/继承流程里被读取。
3. 用几只不同属性点的巨盗龙生成羽毛样本，验证 `StatInfo[2]` 和继承权重是否与公式完全一致。
