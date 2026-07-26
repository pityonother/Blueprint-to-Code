# ARK 鱼篓可装生物与判定机制报告

生成日期：2026-07-26
证据范围：本机当前 ARK DevKit `ShooterGame/Content`、Blueprint to Code Evidence Store v2
核心资产：

- `/Game/PrimalEarth/Structures/FishBasket.FishBasket`
- `/Game/PrimalEarth/CoreBlueprints/Items/PrimalItem_FishBasketFilled.PrimalItem_FishBasketFilled`

## 一、结论

鱼篓不是按“鱼类”“水生”“体型小”之类的通用分类捕获生物。它会在附近搜索 `PrimalDinoCharacter`，再读取目标生物蓝图上的两个资格开关：

- `bAllowTrapping=true`：该生物允许进入鱼篓捕获流程。
- `bPreventWildTrapping=true`：野生个体被排除，只允许处理已经驯服且满足队伍/所有权条件的个体。

当前本机 DevKit 中确认有 **11 类生物**具备鱼篓资格：

- **7 类具备野生捕获资格**：腔棘鱼、剑齿鲑鱼、食人鱼、三叶虫、水蛭、七鳃鳗、Draco。
- **4 类仅限已驯服个体**：安康鱼、蝠鲼、鱼龙、小型美西螈。

其中前 9 类是经典 ARK 生物；`Draco` 和小型美西螈来自当前 DevKit 的内容包目录，是否会在玩家所用地图、服务器或内容组合中出现，必须另看运行时内容是否启用。

## 二、完整资格表

### 2.1 可捕获野生个体

| 生物类型 | 核心 Character 资产 | `bAllowTrapping` | `bPreventWildTrapping` | 结论 |
| --- | --- | ---: | ---: | --- |
| 腔棘鱼（Coelacanth） | `Coel_Character_BP` | `true` | 未设置为 `true` | 可捕获野生个体 |
| 剑齿鲑鱼（Sabertooth Salmon） | `Salmon_Character_BP` | `true` | 未设置为 `true` | 可捕获野生个体 |
| 食人鱼（Piranha） | `Piranha_Character_BP` | `true` | 未设置为 `true` | 可捕获野生个体 |
| 三叶虫（Trilobite） | `Trilobite_Character` | `true` | 未设置为 `true` | 可捕获野生个体；它不是鱼，但仍在鱼篓白名单中 |
| 水蛭（Leech） | `Leech_Character` | `true` | 未设置为 `true` | 可捕获野生个体 |
| 七鳃鳗（Lamprey） | `Lamprey_Character` | `true` | 未设置为 `true` | 可捕获野生个体 |
| Draco | `Draco_Character_BP` | `true` | 未发现本类的禁止野生捕获值 | 蓝图技术资格成立；属于 `Dragontopia` 内容，建议在目标地图实测 |

这里的“可捕获”表示能够通过鱼篓资格筛选，不等同于“可骑乘”“有普通驯服栏”“能繁殖”或“拥有标准宠物功能”。三叶虫、水蛭、七鳃鳗等特殊生物尤其不应把“能装进鱼篓”直接解释为完整传统驯服。

### 2.2 只能装已经驯服的个体

| 生物类型 | 核心 Character 资产 | `bAllowTrapping` | `bPreventWildTrapping` | 结论 |
| --- | --- | ---: | ---: | --- |
| 安康鱼（Anglerfish） | `Angler_Character_BP` | `true` | `true` | 野生不能装；已驯服个体可进入鱼篓流程 |
| 蝠鲼（Manta） | `Manta_Character_BP` | `true` | `true` | 野生不能装；已驯服个体可进入鱼篓流程 |
| 鱼龙（Ichthyosaurus） | `Dolphin_Character_BP` | `true` | `true` | 野生不能装；已驯服个体可进入鱼篓流程 |
| 小型美西螈（Small Axolotl） | `Axolotl_Small_Character_BP` | `true` | `true` | 野生不能装；当前 `TidesOfFortune` 内容中的已驯服个体可装 |

`Dolphin_Character_BP` 是 DevKit 的技术类名，玩家通常称其为鱼龙（Ichthyosaurus），不是现实分类里的海豚。

## 三、鱼篓实际筛选机制

从 `FishBasket` 的 `BPGetMultiUseEntries` 与 `BPTryMultiUse` 可以恢复出以下流程：

1. 鱼篓调用 `SphereOverlapActorsSimple` 搜索附近 Actor。
2. 候选对象被转换为 `PrimalDinoCharacter`。
3. 读取目标的 `bAllowTrapping`、`bPreventWildTrapping`、`BPIsTamed`、`BPIsConscious` 和 `TargetingTeam`。
4. 排除未开放捕获、意识状态不符合、野生捕获被禁止或驯服所有权/队伍不符合的目标。
5. 对候选按距离排序，并将目标写入 `PotentialTrappedFish` / `TrappedFish`。
6. `TrapFish` 读取生物数据、名称、颜色、队伍和原驯服状态，将其写入装满的鱼篓物品。

可确认的结构默认值：

| 参数 | 值 | 含义 |
| --- | ---: | --- |
| `TrapRange` | `200.0` | 附近候选搜索半径，单位为 Unreal Unit |
| `WarmupTime` | `8.0` | 放置后的暖机时间条件 |

因此，鱼篓没有发现一个对所有生物生效的“最大体型数值”。真正的类型白名单是每个生物自己的 `bAllowTrapping`。视觉上很小但没有开启该字段的生物仍不能装；非鱼类只要开启字段也可能进入捕获流程。

`BPIsConscious`、队伍和驯服状态确实出现在筛选图中；但当前 `.uasset` 的大量 Pin 连接属于启发式恢复，所以完整布尔表达式的每一条连线只给中等置信度。上述两个资格字段及其生物默认值则是 Class Defaults 的高置信度确认值。

## 四、装满鱼篓后的数据与时限

`TrapFish` 会调用 `GetDinoData`、`SetCustomItemData` 和 `AddNewItem`。装满鱼篓的 `BlueprintUsed` 图中又出现：

- `BPNetSpawnActorAtLocation`
- `DinoData`
- `DinoClass`
- `DinoName`
- `TargetingTeam`
- `ByteArray`
- `bIsTrapTamed`

这说明装满的鱼篓不是简单替换为一种固定鱼物品，而是保存被装生物的类和个体数据，再在使用物品时生成相应生物。精确生成参数包含原生函数和启发式连线，因此本报告不把特殊生物释放后的每一种驯服/认领结果提升为完全确认。

装满鱼篓物品的确认默认值：

- `SpoilingTime=2400.0`，类默认值相当于 40 分钟。
- `BaseItemWeight=15.0`。
- `bUseSlottedTick=true`，并在 `SlottedTick` 中调用 `GetSpoilingTime` 更新下一次腐坏时间。

实际腐坏时间仍可能被库存、服务器倍率、物品保存倍率或其他原生逻辑修改，所以 40 分钟是资产基准值，不是所有环境中的固定实测值。

## 五、变种与例外

### 已确认的特殊变种

- `GiantTurtle_Piranha_Character_BP` 的 `bAllowTrapping=true`，属于食人鱼系特殊生成物，技术上通过鱼篓类型资格。
- Genesis 海洋 Gauntlet 的腔棘鱼、剑齿鲑鱼和食人鱼变种都显式设置 `bAllowTrapping=false`，因此这些任务斗兽场变种被排除，不能因为基础物种可捕获就自动认为任务变种也可捕获。

### 继承变种的处理原则

畸变、海洋、稀有、节日、病变等派生 Character 如果没有覆盖捕获字段，通常会继承基础类资格；但任务脚本、生成场景或派生类仍可能增加其他限制。本报告只把已经解析到明确布尔值的资产列为“确认”，没有把所有名字相似的变种一概列成可捕获。

## 六、容易混淆的两件事

### 6.1 Shadowmane 的 `0.45` 不是鱼篓捕获体型限制

装满鱼篓物品里存在：

`minimum size requirement for feeding shadowmane = 0.44999998807907104`

这个值位于 Shadowmane 喂食流程，用于判断装在鱼篓里的鱼是否达到喂影鬃所需尺寸。它不负责决定生物能否被鱼篓捕获，不能拿它扩展或缩小鱼篓白名单。

### 6.2 “出现字段名称”不等于“该生物可捕获”

全 Content 二进制扫描还会在展示柜、结构、生物研磨机、Kaiju 等资产里找到同名字段引用。只有同时满足以下条件才被纳入本报告：

1. 资产是相关 `PrimalDinoCharacter`；
2. Evidence Store 恢复到可用的 Class Default；
3. `bAllowTrapping` 的确认值为 `true`；
4. 再结合 `bPreventWildTrapping` 区分野生和已驯服资格。

## 七、证据索引

### 鱼篓机制

- `FishBasket.BPGetMultiUseEntries`：`bp://439d5d2918d622c281459753@80e90fab07a308fea30d70ae/g/2`
- `bAllowTrapping` 读取节点：`bp://439d5d2918d622c281459753@80e90fab07a308fea30d70ae/g/2/n/package%3A262`
- `bPreventWildTrapping` 读取节点：`bp://439d5d2918d622c281459753@80e90fab07a308fea30d70ae/g/2/n/package%3A261`
- `BPIsTamed`：`bp://439d5d2918d622c281459753@80e90fab07a308fea30d70ae/g/2/n/package%3A37`
- `BPIsConscious`：`bp://439d5d2918d622c281459753@80e90fab07a308fea30d70ae/g/2/n/package%3A40`
- `TrapRange=200.0`：`bp://439d5d2918d622c281459753@80e90fab07a308fea30d70ae/default/TrapRange`
- `WarmupTime=8.0`：`bp://439d5d2918d622c281459753@80e90fab07a308fea30d70ae/default/WarmupTime`

### 可捕获野生个体

- 腔棘鱼：`bp://19cb0d666a54b847022f575c@27d5ec10621caeeac0fd796d/default/bAllowTrapping`
- 剑齿鲑鱼：`bp://d6561140344a222485a4cf9a@5c837b9246e282718248f2f5/default/bAllowTrapping`
- 食人鱼：`bp://80e5c7da9d893efe28660b6b@a69970c8e8b89fc7f6a1c268/default/bAllowTrapping`
- 三叶虫：`bp://26402732609c4ec3fef8bf0e@b1611c4117bb5e0029bc53c9/default/bAllowTrapping`
- 水蛭：`bp://da8c56f51267efc5b51c1566@bc5dfd3e39078497785634dd/default/bAllowTrapping`
- 七鳃鳗：`bp://3c71d8aad97dd5c52f8fba92@0b4e32108ab2f9e2e2fd1768/default/bAllowTrapping`
- Draco：`bp://47c5ccbe9dabded18a965aa0@2f307eb128f0ef1f9b95f849/default/bAllowTrapping`
- Giant Turtle 食人鱼变种：`bp://517c13c04681b2eff743f3ef@d359ceb4fe6e1240c74efe3b/default/bAllowTrapping`

### 仅限已驯服个体

- 安康鱼：
  - `bp://e89f45a9dbe6ed1997bf60a8@b6f9df21f5847686bdd6a5c2/default/bAllowTrapping`
  - `bp://e89f45a9dbe6ed1997bf60a8@b6f9df21f5847686bdd6a5c2/default/bPreventWildTrapping`
- 蝠鲼：
  - `bp://e50133eff8e78f05ad15212b@05762cd90d2d92053748e263/default/bAllowTrapping`
  - `bp://e50133eff8e78f05ad15212b@05762cd90d2d92053748e263/default/bPreventWildTrapping`
- 鱼龙：
  - `bp://efa3389ce56fbd036f60a757@a87208900823a1a4dc349840/default/bAllowTrapping`
  - `bp://efa3389ce56fbd036f60a757@a87208900823a1a4dc349840/default/bPreventWildTrapping`
- 小型美西螈：
  - `bp://e36e4d9b60dbf09e8dbf6461@aa1a1a9fe678f7bd514a7281/default/bAllowTrapping`
  - `bp://e36e4d9b60dbf09e8dbf6461@aa1a1a9fe678f7bd514a7281/default/bPreventWildTrapping`

### 明确排除的任务变种

- Gauntlet 腔棘鱼 `bAllowTrapping=false`：`bp://3e4982e8a1db34f0c649e720@19406767de23887d173f32eb/default/bAllowTrapping`
- Gauntlet 剑齿鲑鱼 `bAllowTrapping=false`：`bp://b17be58642e6454fb2d31d67@6b23136e7ad8d4623e8d4d38/default/bAllowTrapping`
- Gauntlet 食人鱼 `bAllowTrapping=false`：`bp://f271a0e498f827b10da704ed@211ed2d79d806c656c08ff99/default/bAllowTrapping`

### 装满鱼篓

- `PrimalItem_FishBasketFilled.BlueprintUsed`：`bp://39cefee33dc90a92a27c587d@4ca49396c7575501053eb336/g/2`
- `SpoilingTime=2400.0`：`bp://39cefee33dc90a92a27c587d@4ca49396c7575501053eb336/default/SpoilingTime`
- `BaseItemWeight=15.0`：`bp://39cefee33dc90a92a27c587d@4ca49396c7575501053eb336/default/BaseItemWeight`
- Shadowmane 喂食最小尺寸 `0.45`：`bp://39cefee33dc90a92a27c587d@4ca49396c7575501053eb336/default/minimum%20size%20requirement%20for%20feeding%20shadowmane`

## 八、验证与置信度

- 17 个本报告直接使用的 Evidence Store 全部通过 `--index-only` 一致性验证：`17/17`。
- 其中 15 个直接 indexed 生物资产通过完整验证：`15/15`，包括 SQLite 完整性、版本、现场源文件哈希和索引一致性。
- `FishBasket` 与 `PrimalItem_FishBasketFilled` 为了生成人类可读报告保留了 dual/legacy 图文件；当前完整验证器会把这些目录识别为 legacy 来源，却同时读到 v3 直接捕获的 parser version，产生版本合同冲突。两者的 SQLite、索引计数、源文件清单和现场 SHA-256 检查均通过，`--index-only` 也通过；此冲突不改变本次字段值和生物资格结论。
- 生物 Class Defaults 的布尔值：高置信度。
- 鱼篓读取这些字段、搜索半径和暖机时间：高置信度。
- 完整的筛选连线顺序：中等置信度，因为大量 Pin 连接为启发式恢复。
- 特殊生物释放后的所有原生驯服/认领细节、内容包地图可用性：尚未进行游戏内运行时验证。

## 九、玩家速查版

想抓野生鱼或小型特殊生物，优先按下面这张短表判断：

- 直接尝试：腔棘鱼、剑齿鲑鱼、食人鱼、三叶虫、水蛭、七鳃鳗。
- 当前内容包技术资格：Draco。
- 只能先驯服再装：安康鱼、蝠鲼、鱼龙、小型美西螈。
- 不要默认任务变种也能装：Genesis 海洋 Gauntlet 的腔棘鱼、鲑鱼、食人鱼已明确关闭捕获资格。
- 捕获失败时依次检查：鱼篓是否完成 8 秒暖机、目标是否在 200 UU 范围内、目标是否有意识、目标是否属于允许捕获的 Character 类、野生捕获是否被该类禁止、驯服目标是否属于自己的队伍。
