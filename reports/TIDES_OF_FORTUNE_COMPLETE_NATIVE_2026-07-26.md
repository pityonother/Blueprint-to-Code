# ARK《Tides of Fortune》完整机制报告：鹦鹉、羁绊羽毛、漂流瓶奖池、船技能与任务树

报告版本：`Native v1 / 2026-07-26`

这是一份新的主报告，不是 7 月 25 日旧报告的覆盖版。它把本机 ARK DevKit 资产、Blueprint-to-Code 图恢复结果，以及 Ghidra 12.1.2 对 `ShooterGameEditor-ShooterGame.dll` 的原生反编译结论合并到一起。

配套附录：

- [附录 A：六档漂流瓶全部 292 个中文物品与 Blueprint 类名](./tides_of_fortune_exact_loot_2026-07-25.md)
- [附录 B：五阶段全部 125 套潜在任务配置](./tides_of_fortune_2026-07-25.md)
- [附录 C：羁绊羽毛与血之灵药逐图对照](./tof_feather_vs_sanguine/conclusion_zh.md)
- [报告 Claim Manifest](./manifests/tides-of-fortune-complete-native-2026-07-26.claims.json)
- [脱敏原生证据 Manifest](./evidence_manifests/shooter-game-native-legacy-2026-07-26.native.json)

> 历史本地 v1 反编译导出仍保持忽略状态；当时的 recipe 与生成器指纹未留存，因此正式发布前必须按当前 recipe 重建。下文历史结论保持不变。

## 一、先给结论

1. 漂流瓶不是固定抽 4 个子奖池。六档宝箱实际继承 `MinItemSets=1`、`NumItemSetsPower=1`，只把 `MaxItemSets` 改为 4，所以每箱抽 1、2、3、4 组的概率约为 `16.67% / 33.33% / 33.33% / 16.67%`，平均 2.5 组。
2. 六档宝箱允许外层子奖池重复抽取。也就是说，同一箱可能两次进入同一个 Level 35、T3 武器或水下护甲池。
3. `SetWeight=1` 不是 100%。外层、Entry 层和具体物品层都要在各自层级按权重总和归一化。
4. 六档资产都写着 `MinQualityMultiplier=2.0`、`MaxQualityMultiplier=4.0`，但原生宝箱构造器还会应用默认 `AboveOneExtraQualityMultiplier=1.2`。普通未改服、没有临时修正时，进入物品生成器的宝箱品质范围是 `2.2–4.6`。
5. 漂流瓶品质最主要改变的是“接到哪一个宝箱类、宝箱里有哪些奖池”，不是逐档提高 2–4 这个字段。Ascendant 仍保留 T1/T2；Mastercraft 甚至有两个标成 T3、实际指向 T1 的入口。
6. 蓝图率是在具体物品已经选定后才判定。某行写 50% BP，只代表进入该 Entry 并选中该物品以后，实物和蓝图大约各占一半；不是整箱 50% 出蓝图。
7. 羁绊羽毛与血之灵药可以对同一只符合条件的生物各使用一次，因为二者写入不同的永久隐藏 Buff。
8. 羁绊羽毛不是“所有驯服方式通用的 +30%”。它只接受正常被动/清醒驯服请求或有效幼崽留痕请求。猿狐 Ferox 使用自定义元素变身/成瘾驯服流程，并把普通清醒驯服入口实质禁用，所以血之灵药能生效，不代表羁绊羽毛也能通过检查。
9. 船技能有海盗、商贸、奢华三树，共 30 个技能、全部升满 108 点。
10. 任务系统不是一棵固定的 174 任务长树。本地数据是 5 个阶段，每阶段有 25 套候选配置；每套配置包含 3 条并行链，每条 3 个顺序任务。若运行时每阶段采用一套配置，则一次完整路线是 45 个任务；“具体选哪套”的最终运行时选择仍未在当前蓝图证据中闭合。

## 二、鹦鹉到底有什么功能

本节对应资产 `Parrot_Character_BP`，不是内部名为 ShoulderDragon 的 Drakeling。

### 2.1 特殊驯服：观察动作后飞到肩上

鹦鹉不是普通喂食驯服。恢复出的图表现为：

1. 野生鹦鹉寻找附近、符合条件且不在冷却中的玩家。
2. 给潜在驯服者添加动作追踪 Buff。
3. 等待玩家使用指定动作；`Buff_ParrotTaming` 当前追踪的动作枚举为 `[5, 6]`。
4. 动作满足后，鹦鹉向玩家移动并挂到肩上。
5. 肩上阶段通过相关事件增加亲和度；长时间没有亲和度事件会脱离，极端情况下会把玩家设为强制仇恨目标。
6. 非驯服期间每 90 秒按所需总亲和度的约 1% 衰减当前亲和度。

关键默认值：

| 参数 | 当前值 | 作用 |
|---|---:|---|
| `RequiredTameAffinity` | 1600 | 基础所需亲和度 |
| `RequiredTameAffinityPerBaseLevel` | 65 | 随野生基础等级增加需求 |
| `TamingPlayerFollowDuration` | 20 秒 | 跟随玩家阶段时间 |
| `TamingPlayerCircleDuration` | 45 秒 | 绕行/观察阶段时间 |
| `TamingInactivityInterval` | 90 秒 | 无进展衰减检查间隔 |
| `AttackTamingPlayerAfterTime` | 600 秒 | 长期无有效进展后的敌对阈值 |

### 2.2 模仿聊天

驯服后可在多用途轮盘中开关 Imitation，并选择模仿模式。它会：

- 接收符合当前频道/模式的聊天消息；
- 经过聊天净化、敏感词过滤、特殊字符清理和长度截断；
- 把消息切成最多 60 字符一段，每段显示 8 秒；
- 保存最近最多 60 条可用消息；
- 在约 10–40 秒冷却范围内选择消息显示；
- 单机没有聊天历史时使用默认台词，例如 `Treasure!`、`Check the map!`、`Pretty bird`、`Ahoy!`。

这不是语音识别；从资产看，它处理的是游戏聊天消息和头顶聊天气泡。

### 2.3 Treasure Hunter：宝藏追踪

驯服且骑肩后，可从轮盘启用 Treasure Hunter：

- 持续 45 秒；
- 默认搜索半径 30,000 Unreal 单位；
- 可选择 30,000 / 20,000 / 10,000 三档距离；
- 最多同时追踪 40 个目标；
- 每 10 秒重新搜索，或玩家移动约 2,000 单位后提前刷新；
- 六种宝藏追踪类型默认全部启用，可在轮盘逐类开关；
- 启用结束后添加独立冷却 Buff；当前已确认冷却类存在，但冷却时长不在本次已恢复默认值中。

### 2.4 羁绊羽毛来源

鹦鹉资产直接引用 `PrimalItem_BondingFeather`，并设置：

```text
ChanceToReceiveBondingFeather = 0.15
```

这说明相关奖励事件中存在 15% 的羽毛获得判定。它不是鹦鹉常驻给所有生物的被动 Buff；羽毛本身是一个独立消耗品。

## 三、羁绊羽毛如何使用

### 3.1 操作方法

1. 把羁绊羽毛放在玩家自己的物品栏，最好放到快捷栏。
2. 走到目标附近，让准星直接对准目标生物本体。
3. 按快捷栏按键使用。物品会从玩家控制器取得准星目标，再执行 `CheckValidForUse`。
4. 检查通过才会消耗羽毛，并给目标写入 `Buff_BondingFeatherUsed_C`。

不要把羽毛放进目标生物的物品栏等待它自动吃；当前蓝图入口不是这种用法。

基础腐坏时间为 14,400 秒，即 4 小时；实际时间仍会受到容器和服务器腐坏倍率影响。

### 3.2 用于被动驯服

目标必须正在走正常被动/清醒驯服入口，而且 `CanFeedWakingTame` 返回可用。成功时：

```text
增加亲和度
= min(RequiredTameAffinity × 0.30,
      RequiredTameAffinity - CurrentTameAffinity)
```

所以最多增加总进度的 30 个百分点；若原本已经达到 70% 或更高，可以直接补到 100% 并触发 `TameDino`。

### 3.3 用于幼崽

目标必须：

- 已驯服并属于本人/同盟；
- 仍是幼崽；
- 当前已经刷新出散步、拥抱或指定食物留痕请求；
- 没有使用过羁绊羽毛。

羽毛替代当前这一次请求，并调用正常 `OnSuccessfulImprinting`。因此它在幼崽上不是固定 +30% 留痕；增加多少由该次正常留痕值和服务器成长/留痕设置决定。

### 3.4 能否和血之灵药混用

可以。

| 物品 | 一次性标记 |
|---|---|
| 羁绊羽毛 | `Buff_BondingFeatherUsed_C` |
| 血之灵药 | `Buff_SanguineElixirUsed_C` |

两者没有共同互斥 Tag，也不检查对方的 Buff，所以同一只符合条件的生物可以各用一次。两个标记都隐藏、持久化并允许保存，重启服务器或重新进出渲染范围不会重置次数。

建议顺序：

- 幼崽：先用羽毛替代当前照料，再用血之灵药取得固定 30% 留痕。
- 被动驯服：先后均可；理论最多合计 60 个百分点。但第一件已经完成驯服后，第二件就不再需要作为驯服道具使用。

### 3.5 为什么猿狐能吃血之灵药，却不能用羽毛加驯养度

Ferox 的本地资产显示：

- 驯服由元素、变身次数、成瘾度和自定义进度函数管理；
- `RequiredTameAffinity=1` 只是兼容字段，不代表它走普通被动驯服；
- `MinPlayerLevelForWakingTame=999999`，等价于把正常清醒喂食入口挡住；
- 羁绊羽毛的有效性检查明确调用 `CanFeedWakingTame`。

因此失败点不是“Ferox 已经用了血之灵药导致冲突”，而是“Ferox 没有给羁绊羽毛提供可替代的正常被动驯服请求”。血之灵药的目标兼容范围更宽，所以两者在 Ferox 上表现不同。

## 四、漂流瓶从开箱到出物品的完整原生流程

```text
漂流瓶品质
→ 对应宝箱类
→ 决定本箱抽几个 Item Set
→ 按 SetWeight 选择 Item Set
→ 按 EntryWeight 选择 Entry
→ 按 ItemsWeights 选择具体物品
→ 计算数量
→ 计算品质评分输入
→ 判定实物或蓝图
→ 应用属性与评分封顶
→ 加入宝箱库存
```

### 4.1 本箱抽几组

原生公式为：

```text
U ~ Uniform(0, 1)
N = round(MinItemSets
          + (MaxItemSets - MinItemSets)
          × U^NumItemSetsPower)
```

若 `SetQuantityWeights` 与 `SetQuantityValues` 都有值，则改走这两个数组的离散加权表，不再使用上面的公式。

潮汐六档宝箱实际继承：

```text
MinItemSets = 1
MaxItemSets = 4
NumItemSetsPower = 1
NumPasses = 1
bSetsRandomWithoutReplacement = false
```

所以近似分布为：

| 本箱 Item Set 次数 | 概率 |
|---:|---:|
| 1 | 16.67% |
| 2 | 33.33% |
| 3 | 33.33% |
| 4 | 16.67% |

期望值是 2.5 组。`false` 表示同一外层池可以重复抽中。

`NumItemSetsPower` 的通用影响：

- `=1`：按线性区间取值；
- `>1`：`U^Power` 更偏向 0，更常接近最小组数；
- `0<Power<1`：更偏向最大组数。

### 4.2 外层 SetWeight

一次外层选择中：

```text
P(ItemSet i) = SetWeight_i / ΣSetWeight
```

潮汐宝箱调用原生函数时 `SetPowerWeight=1`，所以没有额外改变权重。通用原生形式其实是 `weight^SetPowerWeight`。

### 4.3 一个 Item Set 里抽几条

通用公式与外层相同：

```text
K = round(MinNumItems
          + (MaxNumItems - MinNumItems)
          × U^NumItemsPower)
```

六档顶层路由目前均为 `MinNumItems=MaxNumItems=1`，所以一次选中的外层 Item Set 会进入一条 Entry 选择。

### 4.4 EntryWeight

```text
P(Entry j | 已进入 ItemSet)
= EntryWeight_j^SetPowerWeight
  / Σ EntryWeight^SetPowerWeight
```

当前 `SetPowerWeight=1`，因此可直接用 `EntryWeight / 总和`。

`RequiresMinQuality` 是进入 Entry 之前的门槛。若本次宝箱最低品质参数低于该值，该 Entry 权重会直接变成 0。

### 4.5 具体物品权重

Entry 内存在 `ItemsWeights` 时，同样按相对权重选择。当前调用的幂仍为 1：

```text
P(item k | 已进入 Entry) = ItemWeight_k / ΣItemWeight
```

没有对应权重数组时，候选物品按等权处理。`ItemQuantityOverrides` 可对某个具体物品覆盖通用数量。

### 4.6 数量

```text
Quantity
= round(QuantityMultiplier × MinQuantity
        + QuantityMultiplier
        × (MaxQuantity - MinQuantity)
        × U^QuantityPower)
```

潮汐宝箱的 `QuantityMultiplier=1`。若 Entry 设置 `bIgnoreQuantityMultipliers`，原生会把该 Entry 的数量倍率强制按 1 处理。

### 4.7 “抽到了但不给物品”的门槛

Entry 还有 `ChanceToActuallyGiveItem`。原生在物品创建路径中再次随机检查它；未通过时，本次 Entry 不加入箱子。

当前中文完整奖池附录没有单列这个字段，而且部分引用的原版 Genesis ItemSet 在当前解析器中没有完整恢复该默认值。因此本报告中的“具体物品整箱概率例子”按 `ChanceToActuallyGiveItem=1` 计算，并明确标为配置路径概率；若目标 ItemSet 的实际值小于 1，还要再乘这一项。外层 Item Set 概率不受这个缺口影响。

## 五、品质到底怎么算

### 5.1 宝箱 2–4 先变成 2.2–4.6

宝箱构造器的原生处理为：

```text
if CrateQuality > 1:
    EffectiveCrateQuality
    = 1 + (CrateQuality - 1)
          × AboveOneExtraQualityMultiplier
```

原生默认：

```text
AboveOneExtraQualityMultiplier = 1.2
```

所以六档共同的 `2.0–4.0` 先变为：

```text
Cmin = 1 + (2 - 1) × 1.2 = 2.2
Cmax = 1 + (4 - 1) × 1.2 = 4.6
```

之后还会乘服务器/游戏模式的补给箱品质倍率，并叠加临时宝箱品质修正。

### 5.2 Entry 品质与宝箱品质合成

对有品质 Entry，原生恢复出的评分输入为：

```text
U ~ Uniform(0, 1)
RatingInput
= EntryMinQuality × Cmin
  + (EntryMaxQuality × Cmax
     - EntryMinQuality × Cmin)
    × U^EntryQualityPower
```

例子：

- Level 25 有品质装备 Entry 为 `1.8–3.84`；
- 普通未改服宝箱为 `2.2–4.6`；
- 进入 `AddNewItem` 的评分输入范围约为 `3.96–17.664`。

洞穴 T1–T4 有品质装备常见 Entry 为 `3.6–7.2`，对应评分输入范围约为 `7.92–33.12`。

这些是物品评分生成输入，不是屏幕上直接显示的 Primitive、Ramshackle、Ascendant 档位名称。

### 5.3 为什么结果还会被截断

物品生成后还有两层原生约束：

1. `UPrimalItem::ClampStats`：按服务器为各物品属性设置的封顶值裁剪具体属性。
2. `UPrimalItem::ClampItemRating`：若游戏模式设置了最大物品评分，按比例压低各有效属性，并重新计算 `ItemRating`。

最后 `UPrimalGameData::GetItemQualityIndex` 从高到低比较品质定义阈值，给物品写入显示用的品质索引。

所以：

- 提高补给箱品质倍率会抬高生成输入；
- 它不保证最终显示品质按相同比例上涨；
- 一旦撞到属性或评分上限，继续提高倍率的收益会变小；
- 消耗品、资源、结构等不使用装备属性的物品，即使经过相同箱子，也不会表现成高护甲/高伤害装备。

### 5.4 蓝图率

具体物品选定后：

```text
if bForceBlueprint:
    只给蓝图
else:
    U <= ChanceToBeBlueprintOverride
    且物品本身允许成为蓝图
    → 给蓝图
```

因此整箱某蓝图的配置路径概率至少包含：

```text
抽中该 ItemSet
× 抽中该 Entry
× 抽中该具体物品
× BP chance
× ChanceToActuallyGiveItem
```

## 六、六档漂流瓶里到底有什么

下面两列概率含义：

- “单次外层”：一次 Item Set 选择进入该池的概率。
- “整箱至少一次”：利用已恢复的 1–4 组分布，并允许重复抽池，计算本箱至少一次进入该池的概率。

它们只描述进入子奖池，不代表必出表中某件装备。

### 6.1 Primitive

外层总权重 `1.4775`；156 个去重物品类。

| 子奖池 | 单次外层 | 整箱至少一次 | 主要实际产物 |
|---|---:|---:|---|
| Level 25 完整池 | 20.30% | 41.95% | 十字弩、金属镰刀、金属矛、毛皮套、甲龙/剑齿虎/猛犸/潮佑螈鞍；水泥、毛皮、稀有花朵；石箭、麻醉箭、弩箭 |
| 石制建筑 | 3.38% | 8.20% | 石地基、墙、门、恐龙门框、巨兽门框、柱子、天花板、斜坡、三角结构等 17 种 |
| Level 35 完整池 | 67.68% | 89.83% | 鱼叉枪、霰弹枪、长管步枪、简易手枪、金属镐斧、吉利/甲壳/毛皮装备、手炮；金属锭、黑曜石、树脂、火药；中级鞍具与弹药 |
| 名为 Greenhouse 的结构池 | 6.77% | 15.88% | 当前序列化候选仍是上述石制结构，不是玻璃温室部件 |
| Level 45 品质池入口 1 | 1.69% | 4.16% | 电击棒、制式手枪、潜水/防弹/防护装备、金属盾、手炮、探照灯枪及中高阶鞍具；BP 30% |
| 同一 Level 45 品质池入口 2 | 0.17% | 0.42% | 与上一行完全相同，只是另一条更低权重入口 |

两条 Level 45 品质入口合并后，单次约 1.86%，整箱至少进入一次约 4.57%。

配置路径例子：Level 35 资源行中的金属锭，单次外层约 2.1693%；按当前 1–4 组分布，整箱至少走中这条路径约 5.3145%，再乘该 Entry 的实际 `ChanceToActuallyGiveItem`。

### 6.2 Ramshackle

外层总权重 `1.4795`；150 个去重物品类。

| 子奖池 | 单次外层 | 整箱至少一次 | 主要实际产物 |
|---|---:|---:|---|
| Level 25 品质专用 | 20.28% | 41.90% | 有品质十字弩、金属镰刀、金属矛、毛皮套与中级鞍具；装备/鞍具 BP 30% |
| Greenhouse 名义池 | 3.38% | 8.19% | 当前仍解析为石制结构 |
| Level 45 完整池 | 67.59% | 89.78% | 制式手枪、潜水/防弹/防护套、电击棒、手炮；汽油、有机聚合物、菊石黏液、鮟鱇鱼油、含硅珍珠；C4、弹药、中高阶鞍 |
| 金属建筑池 | 6.76% | 15.86% | 金属地基、墙、门、门框、柱子、梯子、天花板、屋顶等 16 种 |
| Level 45 品质专用 | 1.69% | 4.16% | 中高阶有品质装备或鞍具，BP 30% |
| Level 60 品质专用 | 0.30% | 0.76% | 突击步枪、复合弓、制式狙击、泵动霰弹枪、矿枪、防暴套；霸王龙、南巨、鲨齿龙、沧龙、风神平台等高阶鞍 |

主力 Level 45 完整池中“制式手枪蓝图”的配置路径单次约 0.0349%，整箱至少一次约 0.0872%，再乘实际 `ChanceToActuallyGiveItem`。

### 6.3 Apprentice

外层总权重 `1.385`；144 个去重物品类。

| 子奖池 | 单次外层 | 整箱至少一次 | 主要实际产物 |
|---|---:|---:|---|
| Level 45 完整池 | 21.66% | 44.18% | 电梯、保险柜、水雷、发电设施；制式手枪、潜水/防弹/防护装备；汽油、聚合物、珍珠与 C4 |
| 金属建筑池 | 3.61% | 8.73% | 16 种金属结构 |
| Level 60 完整池 | 72.20% | 91.98% | 工业研磨机、工业熔炉、工业大锅、机床、化学实验桌、重炮塔；突击步枪、复合弓、制式狙击、泵动霰弹枪、矿枪、防暴套；高阶鞍、聚合物、电路原件、火箭和高级弹药 |
| 名为 Tek 的结构池 | 0.72% | 1.79% | 当前序列化候选实际仍是金属结构，没有解析到 Tek 建筑物品 |
| Level 60 品质专用 | 1.81% | 4.44% | 高品质高级枪械、防暴套和高阶鞍；装备/鞍具 BP 30% |

Level 60 完整池中“泵动式霰弹枪蓝图”的配置路径单次约 0.0631%，整箱至少一次约 0.1577%，再乘实际 `ChanceToActuallyGiveItem`。

### 6.4 Journeyman

外层总权重 `4.4`；76 个去重物品类。

| 子奖池 | 单次外层 | 整箱至少一次 | 主要实际产物 |
|---|---:|---:|---|
| T1 护甲 | 9.09% | 20.87% | 粗布套、兽皮套、木盾；BP 50% |
| T1 武器 | 9.09% | 20.87% | 石镐、石斧、火把、木棒、弹弓、弓；流星锤、望远镜、长矛、剪刀；基础箭与石头 |
| T2 护甲 | 13.64% | 30.00% | 甲壳套、沙漠套；BP 50% |
| T2 武器 | 13.64% | 30.00% | 金属镐斧、镰刀、剑、金属矛、十字弩、霰弹枪、手炮；手雷与基础弹药 |
| T4 护甲 | 15.91% | 34.26% | 防弹套、金属盾、防护套；BP 50% |
| T4 武器 | 15.91% | 34.26% | 泵动霰弹枪、突击步枪、复合弓、电击棒、制式狙击、泰克榴弹发射器、泰克光刃、火焰喷射器、矿枪、手炮；火箭、C4、高级弹药 |
| 水下 T1 护甲 | 22.73% | 45.90% | 甲壳套 5 件；BP 50% |

T4 武器池内部，品质武器、无品质爆炸物、弹药的 EntryWeight 为 `1 / 0.3 / 1`。所以即使进入 T4 武器池，仍约有 43.48% 只走弹药行。

“泵动式霰弹枪蓝图”的配置路径单次约 0.3458%，整箱至少一次约 0.8617%，再乘实际 `ChanceToActuallyGiveItem`。

### 6.5 Mastercraft

外层总权重 `5.6`；80 个去重物品类。

| 子奖池 | 单次外层 | 整箱至少一次 | 主要实际产物 |
|---|---:|---:|---|
| T2 护甲 | 10.71% | 24.23% | 甲壳套、沙漠套 |
| T2 武器 | 10.71% | 24.23% | 金属工具、剑、金属矛、十字弩、霰弹枪、手炮、手雷和基础弹药 |
| 标记 T3、实际 T1 护甲 | 14.29% | 31.24% | 粗布、兽皮、木盾 |
| 标记 T3、实际 T1 武器 | 14.29% | 31.24% | 石镐、石斧、火把、木棒、弹弓、弓和基础物品 |
| T4 护甲 | 8.93% | 20.53% | 防弹套、金属盾、防护套 |
| T4 武器 | 8.93% | 20.53% | 高阶枪械、泰克武器、火箭/C4与高级弹药 |
| 水下 T1 护甲 | 14.29% | 31.24% | 甲壳套 |
| 水下 T2 护甲 | 17.86% | 37.75% | 潜水套、防弹套、金属盾 |

“T3 实际 T1 武器”不是报告翻译错误，而是当前 DevKit 对象路径确实指向 T1。由此得到石镐蓝图的配置路径单次约 0.4252%，整箱至少一次约 1.0588%，再乘实际 `ChanceToActuallyGiveItem`。

### 6.6 Ascendant

外层总权重 `7.4`；106 个去重物品类。

| 子奖池 | 单次外层 | 整箱至少一次 | 主要实际产物 |
|---|---:|---:|---|
| T1 护甲 | 5.41% | 12.85% | 粗布、兽皮、木盾 |
| T1 武器 | 5.41% | 12.85% | 石制工具、弓、弹弓、基础物品和弹药 |
| T2 护甲 | 10.81% | 24.42% | 甲壳套、沙漠套 |
| T2 武器 | 10.81% | 24.42% | 金属工具、十字弩、霰弹枪、手炮、手雷和基础弹药 |
| 真 T3 护甲 | 13.51% | 29.76% | 吉利套、毛皮套 |
| 真 T3 武器 | 13.51% | 29.76% | 长管步枪、制式手枪、鱼叉枪、登山镐、探照灯枪、手炮；毒气手雷、简易爆炸装置和中级弹药 |
| T4 护甲 | 4.05% | 9.76% | 防弹套、金属盾、防护套 |
| T4 武器 | 4.05% | 9.76% | 泵动霰弹枪、突击步枪、复合弓、制式狙击、泰克武器、火焰喷射器、矿枪、手炮、火箭和 C4 |
| 水下 T1 护甲 | 8.11% | 18.79% | 甲壳套 |
| 水下 T2 护甲 | 10.81% | 24.42% | 潜水套、防弹套、金属盾 |
| 水下 T3 护甲 | 13.51% | 29.76% | 防暴套、防暴盾、潜水服、潜水脚蹼 |

Ascendant 并非 T4 专属。T4 护甲和武器单次都只有 4.05%，而 T3 护甲、T3 武器、水下 T3 各为 13.51%。

配置路径例子：

- 长管步枪蓝图：单次约 0.4896%，整箱至少一次约 1.2184%；
- 防暴胸甲蓝图：单次约 0.8446%，整箱至少一次约 2.0949%。

两者都还要乘目标 Entry 的实际 `ChanceToActuallyGiveItem`。

六档每一个 Entry 的数量、Entry 品质、BP chance、中文物品名和精确 Blueprint 类都在[附录 A](./tides_of_fortune_exact_loot_2026-07-25.md)。

## 七、参数到底影响哪一层

| 参数 | 所在层 | 实际作用 | 不要误解成 |
|---|---|---|---|
| `MinItemSets / MaxItemSets` | 整箱 | 本箱最少/最多选择几组 | 固定出多少件物品 |
| `NumItemSetsPower` | 整箱 | 让组数偏向最小或最大 | 奖池权重 |
| `SetQuantityWeights / Values` | 整箱 | 用离散加权表覆盖组数公式 | 物品权重 |
| `bSetsRandomWithoutReplacement` | 整箱 | 是否禁止重复选同一个外层池 | 禁止重复物品 |
| `SetWeight` | 外层池 | 选中哪个 Item Set | 最终掉率 |
| `MinNumItems / MaxNumItems` | Item Set | 选中该 Set 后抽几条 Entry | 每叠物品数量 |
| `NumItemsPower` | Item Set | Entry 次数偏向最小或最大 | 装备品质 |
| `bItemsRandomWithoutReplacement` | Item Set | 同一 Set 多抽时是否重复 Entry | 外层池是否重复 |
| `EntryWeight` | Entry | 在同一 Set 内选哪个类别 | 具体物品率 |
| `RequiresMinQuality` | Entry | 宝箱最低品质不足时禁用该 Entry | 最终显示品质 |
| `ItemsWeights` | 具体物品 | 在同一 Entry 内选哪个物品 | Entry 权重 |
| `Min/MaxQuantity` | 数量 | 一叠物品的数量范围 | 箱内 Entry 次数 |
| `QuantityPower` | 数量 | 数量偏向最小或最大 | 品质幂 |
| `ItemQuantityOverrides` | 具体物品 | 单独覆盖某个候选物品的数量 | 权重 |
| `Min/MaxQuality` | 品质 | 与宝箱有效品质相乘后形成评分输入区间 | 保证显示某个颜色 |
| `QualityPower` | 品质 | 评分随机值偏向下限或上限 | 蓝图率 |
| `MinRandomQuality` | 品质 | 作为另一项随机品质输入交给 `AddNewItem` | Entry 最低品质本身 |
| `bForceBlueprint` | 蓝图 | 强制走蓝图 | 50% 蓝图率 |
| `ChanceToBeBlueprintOverride` | 蓝图 | 物品选定后转成蓝图的概率 | 整箱蓝图率 |
| `ChanceToActuallyGiveItem` | 最终给物品 | 通过后才把本次生成物加入箱子 | 物品权重 |
| `ItemStatClampsMultiplier` | 属性封顶 | 调整该 Entry 的属性上限 | 宝箱倍率 |
| `AboveOneExtraQualityMultiplier` | 宝箱品质 | 放大高于 1 的宝箱品质部分 | 服务器总倍率 |
| `SupplyCrateLootQualityMultiplier` | 服务器 | 全局缩放补给箱品质输入 | 改变奖池内容 |
| `MaxItemDifficultyClamp` | 物品创建 | 传给物品初始化流程的难度上限 | 品质颜色 |

## 八、船技能树

### 8.1 总点数

| 分支 | 技能数 | 全部升满 |
|---|---:|---:|
| Piracy 海盗 | 10 | 41 点 |
| Merchant 商贸 | 10 | 39 点 |
| Luxury 奢华 | 10 | 28 点 |
| 合计 | 30 | 108 点 |

### 8.2 Piracy

| 技能 | 等级/点数 | 当前数据效果 |
|---|---:|---|
| Gun Crews | 3；1/1/1 | 火炮冷却 -5%/-10%/-15% |
| Siege Vessel | 3；2/2/2 | 对建筑与 NPC 船火炮伤害 +25%/+50%/+75% |
| Hinder | 3；2/2/2 | 火炮直击减速 5%/10%/15%，15 秒 |
| Long Barrels | 2；3/3 | 炮弹速度/射程 +15%/+30%；两级说明均写伤害 -50% |
| Launch Planks | 2；2/2 | 发射登船并提供空中控制；减伤 20%，持续 60/120 秒 |
| Attack Dinghies | 1；2 | 召唤两艘炮艇 300 秒；冷却 900 秒 |
| Boarding Party | 1；1 | 300 秒内盟友伤害 +25%，击杀回复 100 耐力；冷却 900 秒 |
| Long-Range Salvage | 1；1 | 对敌船火炮伤害的 5% 转为修船 |
| Expose Weakness | 2；4/4 | 每层承伤 +1%/+2%，最多 5 层，30 秒 |
| Sea Mines | 1；4 | 3 次充能，每 45 秒恢复 1 次 |

主要前置：`Gun Crews → Hinder / Siege Vessel → Launch Planks / Attack Dinghies / Long Barrels → 后排技能`。

### 8.3 Merchant

| 技能 | 等级/点数 | 当前数据效果 |
|---|---:|---|
| Mercantilism | 3；1/1/1 | 船上盟友负重 ×1.25/×1.50/×1.75；离船保留 300 秒 |
| Pickling Bins | 3；1/1/1 | 腐坏时间 ×1.15/×1.30/×1.45 |
| Friend of the Deep | 3；2/2/2 | 船上盟友不被野生生物主动攻击，除非先攻击；三档无不同数值表 |
| Treasure Hunters | 2；3/3 | 采集 ×1.25/×1.50，移速 ×1.20，持续 10/20 秒；离船保留 300 秒 |
| Smokescreen | 1；1 | 20 秒减伤 25%，脱离野生/NPC 船仇恨，期间不能开火；冷却 180 秒 |
| Deep Sea Lures | 3；2/2/2 | 吸引 5/10/15 条鱼，体型 +10%/+20%/+30%；持续/冷却 600 秒 |
| Harvest Lines | 1；2 | 自动/远程采集 120 秒；冷却 600 秒 |
| Emergency Repairs | 1；4 | 10 秒内每秒修最大生命 2%，最多修到 60%；冷却 600 秒 |
| Chum the Water | 1；4 | 选择饲料，品质越高吸引生物越强；冷却 600 秒 |
| Summon Ghost Fish | 1；4 | 4 次充能；幽灵安康鱼 450 秒，每 300 秒恢复 1 次 |

主要前置：`Mercantilism → Pickling Bins / Friend of the Deep → Treasure Hunters / Smokescreen / Deep Sea Lures → 后排技能`。

### 8.4 Luxury

| 技能 | 等级/点数 | 当前数据效果 |
|---|---:|---|
| Hydrodynamic | 3；1/1/1 | 船速 +5%/+10%/+15% |
| Reinforced Hull | 3；2/2/2 | 减伤 2%/4%/6% |
| Ramming Vessel | 3；1/1/1 | 正面碰撞承伤 -25%/-50%/-75%，撞击伤害 ×2/×3/×4 |
| Galley | 2；4/4 | 生命/耐力回复 ×2/×3；食物和水消耗 -40%/-80% |
| Advanced Lateen | 1；1 | 逆风惩罚降低 60%；附近盟船获得原始移动速度加值 300 |
| Smooth Rudder | 2；1/1 | 加速、减速、转向 ×1.25/×1.50 |
| Wet Dock | 1；2 | 锚定且 30 秒未受伤后缓慢修船；Tick 单位仍需运行时验证 |
| Life Rafts | 1；1 | 船毁时每位盟友获得带随机容器的临时救生筏，900 秒 |
| Ghostly Wind | 1；1 | 20 秒船速 ×1.75、撞击伤害 ×1.50；冷却 600 秒 |
| Linebreaker | 1；1 | 周围每艘船/大型生物给 2.5% 减伤，最多 10 层；敌船尾流使机动 -50%、船速 -15%，20 秒 |

主要前置：`Hydrodynamic → Reinforced Hull / Ramming Vessel → Galley / Smooth Rudder / Advanced Lateen → 后排技能`。

## 九、潜在任务树

### 9.1 数据结构

本地数据包含：

| 阶段 | 候选配置 | 每套结构 | 阶段内去重任务 |
|---:|---:|---|---:|
| 1 | 25 | 3 条链 × 每链 3 任务 | 33 |
| 2 | 25 | 3 条链 × 每链 3 任务 | 33 |
| 3 | 25 | 3 条链 × 每链 3 任务 | 36 |
| 4 | 25 | 3 条链 × 每链 3 任务 | 36 |
| 5 | 25 | 3 条链 × 每链 3 任务 | 36 |

125 套配置里共有 174 个去重任务行，但不能把 174 理解成一个角色必须依次完成的固定主线。每套配置内的三条链可并行，每条链内部必须从左到右。

### 9.2 每阶段的难度与主题

| 阶段 | 主要主题 | 典型终点 |
|---:|---|---|
| 1 | 基础采集、早期驯服、渔业、单桅帆船、Gamma 入门、Ramshackle 合同 | 1,000 级采集、完成基础合同、旅行 10,000 米 |
| 2 | 中期建造、鹦鹉/Tidepup、海盗船、Tek 跳板、Apprentice 合同 | 舰长船、一次漂流瓶、2,500 Hexagons |
| 3 | Journeyman、钻机大采集、Brigantine、区域任务、Gamma Boss | Gamma Moeder/Controller、10 艘海盗船 |
| 4 | Mastercraft、高效/高等级驯服、Beta Boss、大规模资源与武器使用 | Beta Moeder/Controller、5 艘舰长船 |
| 5 | Ascendant、Alpha Boss、终局舰队、25 区域任务、50,000 Hexagons | Alpha Moeder/Controller、50 艘海盗船 |

### 9.3 一条可读的潜在完整路线

下面不是唯一答案，而是每阶段 C1 的一条完整样例。每个阶段实际还有 24 套其他候选。

#### 阶段 1

1. 驯服 1 只 X-副栉龙 → 采集 2,500 浆果 → 采集 1,000 纤维
2. 制作 5 张渔网 → 用渔网捕 25 条鱼 → 驯服 1 只 X-水獭
3. 制作 1 个悬赏板 → 完成 1 个 Ramshackle 合同 → 在 Hexchange 消费 100 Hexagons

#### 阶段 2

1. 制作 3 本货运账簿 → 采集 5,000 木材/真菌木 → 制作 25 个大型储物箱
2. 驯服 1 只 Tidepup → 驯服 3 只鹦鹉 → 驯服 3 只 X-水獭
3. 建造 3 艘 Sloop → 摧毁 3 艘海盗船 → 击败 1 艘 Fleet Captain 船

#### 阶段 3

1. 用船收集 5 个 Journeyman 废弃货物 → 完成 3 个漂流瓶藏宝图 → 摧毁 10 艘海盗船
2. 完成 3 个 Apprentice 合同 → 完成 1 个 Journeyman 合同 → 消费 5,000 Hexagons
3. 完成海洋、火山、太空生态区任务各 5 个

#### 阶段 4

1. 制作 Tek Hover Skiff → 驾驶 100,000 米 → 完成 10 个太空生态区任务
2. 完成 3 个 Journeyman 合同 → 完成 1 个 Mastercraft 合同 → 消费 10,000 Hexagons
3. 以 90% 驯服有效性驯服 1 只 X-沧龙 → 驯服 1 只巨海龟 → 驯服 3 只 Palaeoctopus

#### 阶段 5

1. 驯服 3 只 135+ X-霸王龙 → 完成 5 个 Alpha 任务 → 击败 Alpha Corrupted Master Controller
2. 制作 100 个血袋 → 驯服 5 只血蛛 → 完成 25 个沼泽生态区任务
3. 建造 5 艘 Brigantine → 摧毁 50 艘海盗船 → 袭击 25 个海盗营地或海洋前哨

五阶段全部 125 套候选、每条任务的内部 Task ID 都保存在[附录 B](./tides_of_fortune_2026-07-25.md)。

### 9.4 当前不能冒充已确认的部分

- 已确认：5 阶段、每阶段 25 套候选、每套 3×3 任务链以及全部任务文本。
- 已确认：里程碑完成后会取得当前层级/索引，读取层级奖励，并处理外观、探索笔记和持久物品奖励。
- 未闭合：服务器运行时如何在每阶段的 25 套候选中最终选择、是否按玩家/服务器固定种子持久化。
- 因此不能把某一套 C1–C25 宣称为所有玩家必定遇到的唯一任务树。

## 十、Ghidra 原生证据与可信度

分析目标：

```text
ShooterGameEditor-ShooterGame.dll
SHA-256:
b0e67e1e7625dd89a30b5a1df7652a44b9b142b045f820c419b8b51bbe3d7d2a
PDB loaded: true
```

主要函数：

| 函数 | RVA | 本报告用于确认 |
|---|---:|---|
| `APrimalStructureItemContainer_SupplyCrate` 构造器 | `0xAEA7E0` | 原生默认 Min/Max、Power、AboveOneExtraQualityMultiplier |
| `APrimalStructureItemContainer_SupplyCrate::GenerateCrateItems` | `0xAEBA50` | 2–4 的预处理、服务器品质倍率、生成器调用参数 |
| `UPrimalInventoryComponent::GenerateCrateItems` | `0x13A1420` | 组数、权重、Entry、数量、品质、BP 的主流程 |
| `UPrimalInventoryComponent::GenerateCustomCrateItems` | `0x13A55E0` | 自定义奖池同构流程与完整参数顺序 |
| `UVictoryCore::GetWeightedRandomIndex` | `0xB65B60` | 权重求和、累计随机选择 |
| `UPrimalItem::AddNewItem` | `0x1414E60` | 评分输入进入物品初始化 |
| `UPrimalItem::ClampStats` | `0x1422930` | 各属性服务器封顶 |
| `UPrimalItem::ClampItemRating` | `0x14223E0` | 总评分上限与按比例压缩 |
| `UPrimalGameData::GetItemQualityIndex` | `0x12EA8F0` | 按评分阈值确定显示品质 |
| `UPrimalItem::OverrideItemRating` | `0x1453B10` | 评分覆盖后重新计算品质索引 |

可信度分级：

- 高：资产默认值、对象路径、PDB 精确函数名/RVA、反编译中直接可见的加权与数学公式。
- 中：Ghidra 对复杂局部变量名的自动恢复；本报告只在字段偏移和 Blueprint 反射字段顺序能互相对应时使用。
- 待运行时验证：`ChanceToActuallyGiveItem` 的引用池默认值、任务候选最终选择方式、Wet Dock 的实际 Tick 单位、服务端自定义配置覆盖后的最终装备分布。

本报告描述的是当前 DLL 哈希和当前 DevKit 资产快照。游戏或 DevKit 更新后，应重新校验 DLL/PDB 哈希并重跑原生导出，不能把 RVA 当成跨版本永久地址。
