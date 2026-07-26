# ARK Tides of Fortune：鹦鹉、漂流瓶、船技能与任务树本地证据

生成日期：2026-07-25

这份附录来自当前本机 ARK DevKit 的 `TidesOfFortune` 资产与 Blueprint-to-Code 解析结果。它用于保留完整候选任务组合；玩家界面中的抽取顺序和部分最终数值仍可能由原生代码决定。

## 关键本地资产

- 鹦鹉：`C:\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content\Packs\TidesOfFortune\Dinos\Parrot\Parrot_Character_BP.uasset`
- 漂流瓶地图基类：`C:\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content\Packs\TidesOfFortune\Items\Tools\TreasureMapBottle\Gameplay\BaseClasses\PrimalItem_TreasureMap_Wild_Bottle_Base.uasset`
- 船技能数值：`C:\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content\Packs\TidesOfFortune\CoreBlueprints\Skills\DT_ShipSkills.uasset`
- 船技能连线：`C:\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content\Packs\TidesOfFortune\CoreBlueprints\Skills\ST_Ship.uasset`
- 任务文本：`C:\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content\Packs\TidesOfFortune\CoreBlueprints\Milestones\DT_Milestones_ToF.uasset`

## 漂流瓶品质与宝箱

| 地图品质 | 宝箱访问等级 | 宝箱品质倍率范围 | 每箱最多物品组 |
|---|---:|---:|---:|
| Primitive | 1 | 2.0–4.0 | 4 |
| Ramshackle | 15 | 2.0–4.0 | 4 |
| Apprentice | 25 | 2.0–4.0 | 4 |
| Journeyman | 35 | 2.0–4.0 | 4 |
| Mastercraft | 45 | 2.0–4.0 | 4 |
| Ascendant | 60 | 2.0–4.0 | 4 |

六档地图会切换到不同宝箱类和奖池；六个宝箱的 `MinQualityMultiplier` / `MaxQualityMultiplier` 都是 2.0 / 4.0。因此地图品质主要改变奖池层级和构成，不是把这两个倍率逐档抬高。最终物品品质抽取位于原生逻辑，蓝图里没有可验证的闭式公式。

### 六档奖池路由

这里的权重是同一宝箱内的相对 `SetWeight`，不是可以直接当成掉率的百分比。宝箱最多抽 4 个 Item Set。

| 地图品质 | 候选 Item Set 与相对权重 |
|---|---|
| Primitive | Gen1 L25（0.3）；石制建筑（0.05）；Gen1 L35（1.0）；温室建筑（0.1）；Gen1 L45 QualityOnly（0.025）；同一个 L45 QualityOnly 再出现一次（0.0025） |
| Ramshackle | Gen1 L25 QualityOnly（0.3）；温室建筑（0.05）；Gen1 L45 完整池（1.0）；金属建筑（0.1）；L45 QualityOnly（0.025）；L60 QualityOnly（0.0045） |
| Apprentice | Gen1 L45 完整池（0.3）；金属建筑（0.05）；Gen1 L60 完整池（1.0）；Tek 建筑（0.01）；L60 QualityOnly（0.025） |
| Journeyman | 洞穴 T1 护甲/武器（各 0.4）；T2 护甲/武器（各 0.6）；T4 护甲/武器（各 0.7）；水下 T1 护甲（1.0） |
| Mastercraft | 洞穴 T2 护甲/武器（各 0.6）；界面标签为 T3、实际对象路径却指向 T1 的护甲/武器（各 0.8）；T4 护甲/武器（各 0.5）；水下 T1/T2 护甲（0.8/1.0） |
| Ascendant | 洞穴 T1/T2/T3/T4 护甲和武器（各档分别 0.4/0.8/1.0/0.3）；水下 T1/T2/T3 护甲（0.6/0.8/1.0） |

高阶池中可出现泵动霰弹枪、突击步枪、复合弓、制式狙击步枪、泰克榴弹发射器、泰克爪、火焰喷射器、采矿钻、手持加农炮、火箭筒、高阶鞍、Hazard/Flak/Riot/SCUBA 等；但 Ascendant 仍混有 T1/T2 候选池，并不等于“只出顶级装备”。Mastercraft 的两个“T3”条目实际指向 T1 是当前 DevKit 数据中可以复现的异常。

## 船技能总览

- Piracy：10 个技能，全部升满需要 41 点。
- Merchant：10 个技能，全部升满需要 39 点。
- Luxury：10 个技能，全部升满需要 28 点。
- 三树合计：30 个技能，全部升满需要 108 点。

### Piracy（海盗）

| 技能 | 等级/点数 | 当前数据效果 |
|---|---:|---|
| Gun Crews | 3；1/1/1 | 火炮冷却 -5%/-10%/-15% |
| Siege Vessel | 3；2/2/2 | 对建筑与 NPC 船的火炮伤害 +25%/+50%/+75% |
| Hinder | 3；2/2/2 | 火炮直接命中使目标减速 5%/10%/15%，持续 15 秒 |
| Long Barrels | 2；3/3 | 炮弹速度/射程 +15%/+30%；两级说明均为伤害 -50% |
| Launch Planks | 2；2/2 | 发射登船并提供空中控制；伤害减免 20%，持续 60/120 秒 |
| Attack Dinghies | 1；2 | 召唤两艘炮艇 300 秒；冷却 900 秒 |
| Boarding Party | 1；1 | 300 秒内盟友伤害 +25%，击杀回复 100 耐力；冷却 900 秒 |
| Long-Range Salvage | 1；1 | 对敌船造成的火炮伤害按 5% 转为修船 |
| Expose Weakness | 2；4/4 | 每层承伤 +1%/+2%，最多 5 层，持续 30 秒 |
| Sea Mines | 1；4 | 3 次充能，每 45 秒恢复 1 次 |

主要连线：`Gun Crews → Hinder / Siege Vessel → Launch Planks / Attack Dinghies / Long Barrels → Boarding Party / Long-Range Salvage / Expose Weakness / Sea Mines`。有两条前置路径汇入同一技能时，本地结构是分开的单前置组，表现为满足任一路径即可继续。

### Merchant（商贸）

| 技能 | 等级/点数 | 当前数据效果 |
|---|---:|---|
| Mercantilism | 3；1/1/1 | 船上盟友负重 ×1.25/×1.50/×1.75；离船后保留 300 秒 |
| Pickling Bins | 3；1/1/1 | 腐坏时间 ×1.15/×1.30/×1.45 |
| Friend of the Deep | 3；2/2/2 | 野生生物不主动攻击船上盟友，除非先被攻击；三档没有不同的数值表 |
| Treasure Hunters | 2；3/3 | 采集 ×1.25/×1.50，移速 ×1.20，持续 10/20 秒；离船效果保留 300 秒 |
| Smokescreen | 1；1 | 20 秒减伤 25%，脱离野生/NPC 船仇恨，期间不能开火；冷却 180 秒 |
| Deep Sea Lures | 3；2/2/2 | 吸引 5/10/15 条鱼，体型 +10%/+20%/+30%；持续和冷却均 600 秒 |
| Harvest Lines | 1；2 | 自动/远程采集线持续 120 秒；冷却 600 秒 |
| Emergency Repairs | 1；4 | 10 秒内每秒修复最大生命 2%，只可修到 60%；冷却 600 秒 |
| Chum the Water | 1；4 | 选择一种饲料，品质越高吸引的生物越强；冷却 600 秒 |
| Summon Ghost Fish | 1；4 | 4 次充能；幽灵安康鱼持续 450 秒，每 300 秒恢复 1 次 |

主要连线：`Mercantilism → Pickling Bins / Friend of the Deep → Treasure Hunters / Smokescreen / Deep Sea Lures → Harvest Lines / Emergency Repairs / Chum the Water / Summon Ghost Fish`。

### Luxury（奢华）

| 技能 | 等级/点数 | 当前数据效果 |
|---|---:|---|
| Hydrodynamic | 3；1/1/1 | 船速 +5%/+10%/+15% |
| Reinforced Hull | 3；2/2/2 | 减伤 2%/4%/6% |
| Ramming Vessel | 3；1/1/1 | 正面碰撞承伤 -25%/-50%/-75%，撞击伤害 ×2/×3/×4 |
| Galley | 2；4/4 | 生命/耐力回复 ×2/×3；食物和水消耗 -40%/-80% |
| Advanced Lateen | 1；1 | 逆风惩罚降低 60%；附近盟船获得原始移动速度加值 300 |
| Smooth Rudder | 2；1/1 | 加速、减速、转向 ×1.25/×1.50 |
| Wet Dock | 1；2 | 锚定且 30 秒未受伤后缓慢修船；底层回复值很小但 Tick 单位未在蓝图中定死 |
| Life Rafts | 1；1 | 船毁时每位盟友获得携带随机容器的临时救生筏，持续 900 秒 |
| Ghostly Wind | 1；1 | 20 秒内船速 ×1.75、撞击伤害 ×1.50；冷却 600 秒 |
| Linebreaker | 1；1 | 周围每艘船/大型生物提供 2.5% 减伤，最多 10 层；敌船尾流 20 秒内机动 -50%、船速 -15% |

主要连线：`Hydrodynamic → Reinforced Hull / Ramming Vessel → Galley / Smooth Rudder / Advanced Lateen → Wet Dock / Life Rafts / Ghostly Wind / Linebreaker`。

## Milestone 候选池统计

| 阶段 | 配置资产 | 每次配置 | 去重任务数 |
|---:|---:|---:|---:|
| 1 | 25 | 3 条链 × 每链 3 个任务 | 33 |
| 2 | 25 | 3 条链 × 每链 3 个任务 | 33 |
| 3 | 25 | 3 条链 × 每链 3 个任务 | 36 |
| 4 | 25 | 3 条链 × 每链 3 个任务 | 36 |
| 5 | 25 | 3 条链 × 每链 3 个任务 | 36 |

本地共有 5 阶段 × 25 配置 = 125 个候选配置资产；每个配置均为 3 条并行任务链，每条链必须按左到右完成 3 个任务。5 阶段去重后共 174 个任务行。下面列出全部候选配置；括号内保留内部 Task ID，便于核验。

## 阶段 1：25 个候选配置

### C1

1. Tame 1 X-Parasaur (`Task.ToF.TameXParasaur`) → Harvest 2,500 Berries (`Task.ToF.HarvestBerries`) → Harvest 1,000 Fiber (`Task.ToF.HarvestFiber`)
2. Craft 5 Fish Nets (`Task.ToF.CraftFishNets`) → Catch 25 Fish with Fish Nets (`Task.ToF.CatchFishNetsA`) → Tame 1 X-Otter (`Task.ToF.TameXOtterA`)
3. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)

### C2

1. Craft 1 Wood Ocean Platform (`Task.ToF.CraftWoodPlatform`) → Craft 1 Cargo Ledger (`Task.ToF.CraftCargoLedgerA`) → Craft 1 Market (`Task.ToF.CraftMarketA`)
2. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)
3. Tame 1 X-Raptor (`Task.ToF.TameXRaptor`) → Tame 1 X-Sabertooth (`Task.ToF.TameXSabertooth`) → Tame 1 X-Allosaurus (`Task.ToF.TameXAllosaurus`)

### C3

1. Tame 1 X-Ankylosaurus (`Task.ToF.TameXAnkylosaurus`) → Harvest 500 Raw Metal (`Task.ToF.HarvestRawMetalA`) → Harvest 1,000 Stone (`Task.ToF.HarvestStone`)
2. Complete 1 Gamma Mission (`Task.ToF.CompleteGammaMissionsA`) → Complete 1 Race Mission (`Task.ToF.CompleteRaceMission`) → Complete 1 Escort Mission (`Task.ToF.CompleteEscortMission`)
3. Craft 1 Aquarium (`Task.ToF.CraftFishTankA`) → Craft 10 Fish Baskets (`Task.ToF.CraftFishBasketsA`) → Catch 10 X-Sabertooth Salmon with Fish Baskets (`Task.ToF.CatchSalmon`)

### C4

1. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)
2. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)
3. Tame 1 X-Trike (`Task.ToF.TameXTrike`) → Harvest 1,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodA`) → Harvest 1,000 Thatch (`Task.ToF.HarvestThatch`)

### C5

1. Craft 5 Fish Nets (`Task.ToF.CraftFishNets`) → Catch 25 Fish with Fish Nets (`Task.ToF.CatchFishNetsA`) → Tame 1 X-Otter (`Task.ToF.TameXOtterA`)
2. Tame 1 X-Ankylosaurus (`Task.ToF.TameXAnkylosaurus`) → Harvest 500 Raw Metal (`Task.ToF.HarvestRawMetalA`) → Harvest 1,000 Stone (`Task.ToF.HarvestStone`)
3. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)

### C6

1. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)
2. Craft 1 Aquarium (`Task.ToF.CraftFishTankA`) → Craft 10 Fish Baskets (`Task.ToF.CraftFishBasketsA`) → Catch 10 X-Sabertooth Salmon with Fish Baskets (`Task.ToF.CatchSalmon`)
3. Tame 1 X-Trike (`Task.ToF.TameXTrike`) → Harvest 1,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodA`) → Harvest 1,000 Thatch (`Task.ToF.HarvestThatch`)

### C7

1. Tame 1 X-Parasaur (`Task.ToF.TameXParasaur`) → Harvest 2,500 Berries (`Task.ToF.HarvestBerries`) → Harvest 1,000 Fiber (`Task.ToF.HarvestFiber`)
2. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)
3. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)

### C8

1. Craft 5 Fish Nets (`Task.ToF.CraftFishNets`) → Catch 25 Fish with Fish Nets (`Task.ToF.CatchFishNetsA`) → Tame 1 X-Otter (`Task.ToF.TameXOtterA`)
2. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)
3. Tame 1 X-Trike (`Task.ToF.TameXTrike`) → Harvest 1,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodA`) → Harvest 1,000 Thatch (`Task.ToF.HarvestThatch`)

### C9

1. Tame 1 X-Parasaur (`Task.ToF.TameXParasaur`) → Harvest 2,500 Berries (`Task.ToF.HarvestBerries`) → Harvest 1,000 Fiber (`Task.ToF.HarvestFiber`)
2. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)
3. Craft 1 Aquarium (`Task.ToF.CraftFishTankA`) → Craft 10 Fish Baskets (`Task.ToF.CraftFishBasketsA`) → Catch 10 X-Sabertooth Salmon with Fish Baskets (`Task.ToF.CatchSalmon`)

### C10

1. Craft 1 Wood Ocean Platform (`Task.ToF.CraftWoodPlatform`) → Craft 1 Cargo Ledger (`Task.ToF.CraftCargoLedgerA`) → Craft 1 Market (`Task.ToF.CraftMarketA`)
2. Tame 1 X-Raptor (`Task.ToF.TameXRaptor`) → Tame 1 X-Sabertooth (`Task.ToF.TameXSabertooth`) → Tame 1 X-Allosaurus (`Task.ToF.TameXAllosaurus`)
3. Complete 1 Gamma Mission (`Task.ToF.CompleteGammaMissionsA`) → Complete 1 Race Mission (`Task.ToF.CompleteRaceMission`) → Complete 1 Escort Mission (`Task.ToF.CompleteEscortMission`)

### C11

1. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)
2. Tame 1 X-Ankylosaurus (`Task.ToF.TameXAnkylosaurus`) → Harvest 500 Raw Metal (`Task.ToF.HarvestRawMetalA`) → Harvest 1,000 Stone (`Task.ToF.HarvestStone`)
3. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)

### C12

1. Tame 1 X-Parasaur (`Task.ToF.TameXParasaur`) → Harvest 2,500 Berries (`Task.ToF.HarvestBerries`) → Harvest 1,000 Fiber (`Task.ToF.HarvestFiber`)
2. Craft 5 Fish Nets (`Task.ToF.CraftFishNets`) → Catch 25 Fish with Fish Nets (`Task.ToF.CatchFishNetsA`) → Tame 1 X-Otter (`Task.ToF.TameXOtterA`)
3. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)

### C13

1. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)
2. Tame 1 X-Ankylosaurus (`Task.ToF.TameXAnkylosaurus`) → Harvest 500 Raw Metal (`Task.ToF.HarvestRawMetalA`) → Harvest 1,000 Stone (`Task.ToF.HarvestStone`)
3. Craft 1 Aquarium (`Task.ToF.CraftFishTankA`) → Craft 10 Fish Baskets (`Task.ToF.CraftFishBasketsA`) → Catch 10 X-Sabertooth Salmon with Fish Baskets (`Task.ToF.CatchSalmon`)

### C14

1. Craft 5 Fish Nets (`Task.ToF.CraftFishNets`) → Catch 25 Fish with Fish Nets (`Task.ToF.CatchFishNetsA`) → Tame 1 X-Otter (`Task.ToF.TameXOtterA`)
2. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)
3. Tame 1 X-Trike (`Task.ToF.TameXTrike`) → Harvest 1,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodA`) → Harvest 1,000 Thatch (`Task.ToF.HarvestThatch`)

### C15

1. Tame 1 X-Parasaur (`Task.ToF.TameXParasaur`) → Harvest 2,500 Berries (`Task.ToF.HarvestBerries`) → Harvest 1,000 Fiber (`Task.ToF.HarvestFiber`)
2. Craft 1 Wood Ocean Platform (`Task.ToF.CraftWoodPlatform`) → Craft 1 Cargo Ledger (`Task.ToF.CraftCargoLedgerA`) → Craft 1 Market (`Task.ToF.CraftMarketA`)
3. Complete 1 Gamma Mission (`Task.ToF.CompleteGammaMissionsA`) → Complete 1 Race Mission (`Task.ToF.CompleteRaceMission`) → Complete 1 Escort Mission (`Task.ToF.CompleteEscortMission`)

### C16

1. Tame 1 X-Raptor (`Task.ToF.TameXRaptor`) → Tame 1 X-Sabertooth (`Task.ToF.TameXSabertooth`) → Tame 1 X-Allosaurus (`Task.ToF.TameXAllosaurus`)
2. Complete 1 Gamma Mission (`Task.ToF.CompleteGammaMissionsA`) → Complete 1 Race Mission (`Task.ToF.CompleteRaceMission`) → Complete 1 Escort Mission (`Task.ToF.CompleteEscortMission`)
3. Craft 1 Aquarium (`Task.ToF.CraftFishTankA`) → Craft 10 Fish Baskets (`Task.ToF.CraftFishBasketsA`) → Catch 10 X-Sabertooth Salmon with Fish Baskets (`Task.ToF.CatchSalmon`)

### C17

1. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)
2. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)
3. Tame 1 X-Ankylosaurus (`Task.ToF.TameXAnkylosaurus`) → Harvest 500 Raw Metal (`Task.ToF.HarvestRawMetalA`) → Harvest 1,000 Stone (`Task.ToF.HarvestStone`)

### C18

1. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)
2. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)
3. Tame 1 X-Trike (`Task.ToF.TameXTrike`) → Harvest 1,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodA`) → Harvest 1,000 Thatch (`Task.ToF.HarvestThatch`)

### C19

1. Craft 5 Fish Nets (`Task.ToF.CraftFishNets`) → Catch 25 Fish with Fish Nets (`Task.ToF.CatchFishNetsA`) → Tame 1 X-Otter (`Task.ToF.TameXOtterA`)
2. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)
3. Tame 1 X-Ankylosaurus (`Task.ToF.TameXAnkylosaurus`) → Harvest 500 Raw Metal (`Task.ToF.HarvestRawMetalA`) → Harvest 1,000 Stone (`Task.ToF.HarvestStone`)

### C20

1. Craft 1 Wood Ocean Platform (`Task.ToF.CraftWoodPlatform`) → Craft 1 Cargo Ledger (`Task.ToF.CraftCargoLedgerA`) → Craft 1 Market (`Task.ToF.CraftMarketA`)
2. Complete 1 Gamma Mission (`Task.ToF.CompleteGammaMissionsA`) → Complete 1 Race Mission (`Task.ToF.CompleteRaceMission`) → Complete 1 Escort Mission (`Task.ToF.CompleteEscortMission`)
3. Tame 1 X-Trike (`Task.ToF.TameXTrike`) → Harvest 1,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodA`) → Harvest 1,000 Thatch (`Task.ToF.HarvestThatch`)

### C21

1. Tame 1 X-Parasaur (`Task.ToF.TameXParasaur`) → Harvest 2,500 Berries (`Task.ToF.HarvestBerries`) → Harvest 1,000 Fiber (`Task.ToF.HarvestFiber`)
2. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)
3. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)

### C22

1. Tame 1 X-Raptor (`Task.ToF.TameXRaptor`) → Tame 1 X-Sabertooth (`Task.ToF.TameXSabertooth`) → Tame 1 X-Allosaurus (`Task.ToF.TameXAllosaurus`)
2. Craft 1 Aquarium (`Task.ToF.CraftFishTankA`) → Craft 10 Fish Baskets (`Task.ToF.CraftFishBasketsA`) → Catch 10 X-Sabertooth Salmon with Fish Baskets (`Task.ToF.CatchSalmon`)
3. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)

### C23

1. Craft 1 Wood Ocean Platform (`Task.ToF.CraftWoodPlatform`) → Craft 1 Cargo Ledger (`Task.ToF.CraftCargoLedgerA`) → Craft 1 Market (`Task.ToF.CraftMarketA`)
2. Tame 1 X-Ankylosaurus (`Task.ToF.TameXAnkylosaurus`) → Harvest 500 Raw Metal (`Task.ToF.HarvestRawMetalA`) → Harvest 1,000 Stone (`Task.ToF.HarvestStone`)
3. Complete 1 Gamma Mission (`Task.ToF.CompleteGammaMissionsA`) → Complete 1 Race Mission (`Task.ToF.CompleteRaceMission`) → Complete 1 Escort Mission (`Task.ToF.CompleteEscortMission`)

### C24

1. Craft 5 Fish Nets (`Task.ToF.CraftFishNets`) → Catch 25 Fish with Fish Nets (`Task.ToF.CatchFishNetsA`) → Tame 1 X-Otter (`Task.ToF.TameXOtterA`)
2. Craft 1 Bounty Board (`Task.ToF.CraftBountyBoard`) → Complete 1 Ramshackle Contract (`Task.ToF.CompleteRamsContractA`) → Spend 100 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsA`)
3. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)

### C25

1. Tame 1 X-Parasaur (`Task.ToF.TameXParasaur`) → Harvest 2,500 Berries (`Task.ToF.HarvestBerries`) → Harvest 1,000 Fiber (`Task.ToF.HarvestFiber`)
2. Tame 1 X-Ichthyosaurus (`Task.ToF.TameXIchthy`) → Travel 100,000 Meters Distance on an X-Ichthyosaurus (`Task.ToF.TravelXIchthy`) → Collect 1 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoA`)
3. Craft 1 Shipyard (`Task.ToF.CraftShipyardA`) → Construct a Sloop (`Task.ToF.ConstructSloopA`) → Travel 10,000 Meters Distance with Sloop (`Task.ToF.TravelSloop`)

## 阶段 2：25 个候选配置

### C1

1. Craft 3 Cargo Ledgers (`Task.ToF.CraftCargoLedgerB`) → Harvest 5,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodB`) → Craft 25 Large Storage Boxes (`Task.ToF.CraftStorageBoxes`)
2. Tame 1 Tidepup (`Task.ToF.TameTidepupA`) → Tame 3 Parrots (`Task.ToF.TameParrotB`) → Tame 3 X-Otters (`Task.ToF.TameXOtterB`)
3. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)

### C2

1. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
2. Complete 3 Gamma Missions (`Task.ToF.CompleteGammaMissionsB`) → Complete 1 Beta Mission (`Task.ToF.CompleteBetaMissionsA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsB`)
3. Complete 3 Ramshackle Contracts (`Task.ToF.CompleteRamsContractB`) → Complete 1 Apprentice Contract (`Task.ToF.CompleteApprContractA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsC`)

### C3

1. Craft 3 Aquariums (`Task.ToF.CraftFishTankB`) → Craft 25 Fish Baskets (`Task.ToF.CraftFishBasketsB`) → Catch 50 Fish with Fish Nets (`Task.ToF.CatchFishNetsB`)
2. Raid 1 Pirate Camp or Ocean Outpost (`Task.ToF.RaidPirateCampsA`) → Complete 1 Treasure Map Bottle (`Task.ToF.CompleteTreasureMapsA`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsA`)
3. Craft 1 Metal Ocean Platform (`Task.ToF.CraftMetalPlatform`) → Craft 1 Shipyard (`Task.ToF.CraftShipyardB`) → Craft 1 Market (`Task.ToF.CraftMarketB`)

### C4

1. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)
2. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
3. Craft 1 Tek Sensor (`Task.ToF.CraftTekSensor`) → Craft 1 Tek Jump Pad (`Task.ToF.CraftTekJumpPad`) → Travel 10,000 Meters Distance with Tek Jump Pad (`Task.ToF.TravelTekJumpPad`)

### C5

1. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
2. Craft 3 Aquariums (`Task.ToF.CraftFishTankB`) → Craft 25 Fish Baskets (`Task.ToF.CraftFishBasketsB`) → Catch 50 Fish with Fish Nets (`Task.ToF.CatchFishNetsB`)
3. Raid 1 Pirate Camp or Ocean Outpost (`Task.ToF.RaidPirateCampsA`) → Complete 1 Treasure Map Bottle (`Task.ToF.CompleteTreasureMapsA`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsA`)

### C6

1. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)
2. Complete 3 Ramshackle Contracts (`Task.ToF.CompleteRamsContractB`) → Complete 1 Apprentice Contract (`Task.ToF.CompleteApprContractA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsC`)
3. Craft 1 Tek Sensor (`Task.ToF.CraftTekSensor`) → Craft 1 Tek Jump Pad (`Task.ToF.CraftTekJumpPad`) → Travel 10,000 Meters Distance with Tek Jump Pad (`Task.ToF.TravelTekJumpPad`)

### C7

1. Craft 3 Cargo Ledgers (`Task.ToF.CraftCargoLedgerB`) → Harvest 5,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodB`) → Craft 25 Large Storage Boxes (`Task.ToF.CraftStorageBoxes`)
2. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
3. Complete 3 Gamma Missions (`Task.ToF.CompleteGammaMissionsB`) → Complete 1 Beta Mission (`Task.ToF.CompleteBetaMissionsA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsB`)

### C8

1. Tame 1 Tidepup (`Task.ToF.TameTidepupA`) → Tame 3 Parrots (`Task.ToF.TameParrotB`) → Tame 3 X-Otters (`Task.ToF.TameXOtterB`)
2. Craft 1 Metal Ocean Platform (`Task.ToF.CraftMetalPlatform`) → Craft 1 Shipyard (`Task.ToF.CraftShipyardB`) → Craft 1 Market (`Task.ToF.CraftMarketB`)
3. Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoB`) → Collect 3 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoA`) → Collect 1 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoA`)

### C9

1. Craft 3 Cargo Ledgers (`Task.ToF.CraftCargoLedgerB`) → Harvest 5,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodB`) → Craft 25 Large Storage Boxes (`Task.ToF.CraftStorageBoxes`)
2. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)
3. Complete 3 Ramshackle Contracts (`Task.ToF.CompleteRamsContractB`) → Complete 1 Apprentice Contract (`Task.ToF.CompleteApprContractA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsC`)

### C10

1. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
2. Raid 1 Pirate Camp or Ocean Outpost (`Task.ToF.RaidPirateCampsA`) → Complete 1 Treasure Map Bottle (`Task.ToF.CompleteTreasureMapsA`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsA`)
3. Craft 1 Tek Sensor (`Task.ToF.CraftTekSensor`) → Craft 1 Tek Jump Pad (`Task.ToF.CraftTekJumpPad`) → Travel 10,000 Meters Distance with Tek Jump Pad (`Task.ToF.TravelTekJumpPad`)

### C11

1. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)
2. Complete 3 Gamma Missions (`Task.ToF.CompleteGammaMissionsB`) → Complete 1 Beta Mission (`Task.ToF.CompleteBetaMissionsA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsB`)
3. Craft 3 Aquariums (`Task.ToF.CraftFishTankB`) → Craft 25 Fish Baskets (`Task.ToF.CraftFishBasketsB`) → Catch 50 Fish with Fish Nets (`Task.ToF.CatchFishNetsB`)

### C12

1. Tame 1 Tidepup (`Task.ToF.TameTidepupA`) → Tame 3 Parrots (`Task.ToF.TameParrotB`) → Tame 3 X-Otters (`Task.ToF.TameXOtterB`)
2. Raid 1 Pirate Camp or Ocean Outpost (`Task.ToF.RaidPirateCampsA`) → Complete 1 Treasure Map Bottle (`Task.ToF.CompleteTreasureMapsA`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsA`)
3. Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoB`) → Collect 3 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoA`) → Collect 1 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoA`)

### C13

1. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
2. Complete 3 Gamma Missions (`Task.ToF.CompleteGammaMissionsB`) → Complete 1 Beta Mission (`Task.ToF.CompleteBetaMissionsA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsB`)
3. Craft 1 Tek Sensor (`Task.ToF.CraftTekSensor`) → Craft 1 Tek Jump Pad (`Task.ToF.CraftTekJumpPad`) → Travel 10,000 Meters Distance with Tek Jump Pad (`Task.ToF.TravelTekJumpPad`)

### C14

1. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)
2. Complete 3 Ramshackle Contracts (`Task.ToF.CompleteRamsContractB`) → Complete 1 Apprentice Contract (`Task.ToF.CompleteApprContractA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsC`)
3. Craft 3 Aquariums (`Task.ToF.CraftFishTankB`) → Craft 25 Fish Baskets (`Task.ToF.CraftFishBasketsB`) → Catch 50 Fish with Fish Nets (`Task.ToF.CatchFishNetsB`)

### C15

1. Raid 1 Pirate Camp or Ocean Outpost (`Task.ToF.RaidPirateCampsA`) → Complete 1 Treasure Map Bottle (`Task.ToF.CompleteTreasureMapsA`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsA`)
2. Craft 1 Metal Ocean Platform (`Task.ToF.CraftMetalPlatform`) → Craft 1 Shipyard (`Task.ToF.CraftShipyardB`) → Craft 1 Market (`Task.ToF.CraftMarketB`)
3. Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoB`) → Collect 3 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoA`) → Collect 1 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoA`)

### C16

1. Craft 3 Cargo Ledgers (`Task.ToF.CraftCargoLedgerB`) → Harvest 5,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodB`) → Craft 25 Large Storage Boxes (`Task.ToF.CraftStorageBoxes`)
2. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
3. Complete 3 Ramshackle Contracts (`Task.ToF.CompleteRamsContractB`) → Complete 1 Apprentice Contract (`Task.ToF.CompleteApprContractA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsC`)

### C17

1. Craft 3 Cargo Ledgers (`Task.ToF.CraftCargoLedgerB`) → Harvest 5,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodB`) → Craft 25 Large Storage Boxes (`Task.ToF.CraftStorageBoxes`)
2. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)
3. Complete 3 Gamma Missions (`Task.ToF.CompleteGammaMissionsB`) → Complete 1 Beta Mission (`Task.ToF.CompleteBetaMissionsA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsB`)

### C18

1. Tame 1 Tidepup (`Task.ToF.TameTidepupA`) → Tame 3 Parrots (`Task.ToF.TameParrotB`) → Tame 3 X-Otters (`Task.ToF.TameXOtterB`)
2. Craft 1 Tek Sensor (`Task.ToF.CraftTekSensor`) → Craft 1 Tek Jump Pad (`Task.ToF.CraftTekJumpPad`) → Travel 10,000 Meters Distance with Tek Jump Pad (`Task.ToF.TravelTekJumpPad`)
3. Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoB`) → Collect 3 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoA`) → Collect 1 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoA`)

### C19

1. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
2. Complete 3 Gamma Missions (`Task.ToF.CompleteGammaMissionsB`) → Complete 1 Beta Mission (`Task.ToF.CompleteBetaMissionsA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsB`)
3. Craft 3 Aquariums (`Task.ToF.CraftFishTankB`) → Craft 25 Fish Baskets (`Task.ToF.CraftFishBasketsB`) → Catch 50 Fish with Fish Nets (`Task.ToF.CatchFishNetsB`)

### C20

1. Tame 1 Tidepup (`Task.ToF.TameTidepupA`) → Tame 3 Parrots (`Task.ToF.TameParrotB`) → Tame 3 X-Otters (`Task.ToF.TameXOtterB`)
2. Raid 1 Pirate Camp or Ocean Outpost (`Task.ToF.RaidPirateCampsA`) → Complete 1 Treasure Map Bottle (`Task.ToF.CompleteTreasureMapsA`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsA`)
3. Craft 1 Metal Ocean Platform (`Task.ToF.CraftMetalPlatform`) → Craft 1 Shipyard (`Task.ToF.CraftShipyardB`) → Craft 1 Market (`Task.ToF.CraftMarketB`)

### C21

1. Complete 3 Ramshackle Contracts (`Task.ToF.CompleteRamsContractB`) → Complete 1 Apprentice Contract (`Task.ToF.CompleteApprContractA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsC`)
2. Craft 1 Metal Ocean Platform (`Task.ToF.CraftMetalPlatform`) → Craft 1 Shipyard (`Task.ToF.CraftShipyardB`) → Craft 1 Market (`Task.ToF.CraftMarketB`)
3. Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoB`) → Collect 3 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoA`) → Collect 1 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoA`)

### C22

1. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)
2. Raid 1 Pirate Camp or Ocean Outpost (`Task.ToF.RaidPirateCampsA`) → Complete 1 Treasure Map Bottle (`Task.ToF.CompleteTreasureMapsA`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsA`)
3. Craft 1 Tek Sensor (`Task.ToF.CraftTekSensor`) → Craft 1 Tek Jump Pad (`Task.ToF.CraftTekJumpPad`) → Travel 10,000 Meters Distance with Tek Jump Pad (`Task.ToF.TravelTekJumpPad`)

### C23

1. Tame 1 Parrot (`Task.ToF.TameParrotA`) → Kill 1 Alpha Carno (`Task.ToF.KillAlphaCarno`) → Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoC`)
2. Complete 3 Ramshackle Contracts (`Task.ToF.CompleteRamsContractB`) → Complete 1 Apprentice Contract (`Task.ToF.CompleteApprContractA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsC`)
3. Craft 3 Aquariums (`Task.ToF.CraftFishTankB`) → Craft 25 Fish Baskets (`Task.ToF.CraftFishBasketsB`) → Catch 50 Fish with Fish Nets (`Task.ToF.CatchFishNetsB`)

### C24

1. Craft 3 Cargo Ledgers (`Task.ToF.CraftCargoLedgerB`) → Harvest 5,000 Wood/Fungal Wood (`Task.ToF.HarvestWoodB`) → Craft 25 Large Storage Boxes (`Task.ToF.CraftStorageBoxes`)
2. Tame 1 Tidepup (`Task.ToF.TameTidepupA`) → Tame 3 Parrots (`Task.ToF.TameParrotB`) → Tame 3 X-Otters (`Task.ToF.TameXOtterB`)
3. Collect 5 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoB`) → Collect 3 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoA`) → Collect 1 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoA`)

### C25

1. Construct 3 Sloops (`Task.ToF.ConstructSloopB`) → Destroy 3 Pirate Ships (`Task.ToF.DestroyPirateShipsB`) → Defeat 1 Fleet Captain Ship (`Task.ToF.DefeatFleetCaptainA`)
2. Complete 3 Gamma Missions (`Task.ToF.CompleteGammaMissionsB`) → Complete 1 Beta Mission (`Task.ToF.CompleteBetaMissionsA`) → Spend 2,500 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsB`)
3. Craft 1 Tek Sensor (`Task.ToF.CraftTekSensor`) → Craft 1 Tek Jump Pad (`Task.ToF.CraftTekJumpPad`) → Travel 10,000 Meters Distance with Tek Jump Pad (`Task.ToF.TravelTekJumpPad`)

## 阶段 3：25 个候选配置

### C1

1. Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoC`) → Complete 3 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsB`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsC`)
2. Complete 3 Apprentice Contracts (`Task.ToF.CompleteApprContractB`) → Complete 1 Journeyman Contract (`Task.ToF.CompleteJourContractA`) → Spend 5,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsD`)
3. Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsA`) → Complete 5 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsA`) → Complete 5 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsA`)

### C2

1. Craft 1 Mining Drill (`Task.ToF.CraftMiningDrill`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillA`) → Harvest 25,000 Raw Metal (`Task.ToF.HarvestRawMetalB`)
2. Tame 1 X-Rex (`Task.ToF.TameXRex`) → Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusB`) → Tame 1 X-Spino (`Task.ToF.TameXSpino`)
3. Tame 3 Tidepups (`Task.ToF.TameTidepupB`) → Evolve 1 Tidepup via Neotenic Stabilization (`Task.ToF.EvolveTidepupNeotenic`) → Have 10 debuffs dispelled by Tidepup on your shoulder (`Task.ToF.DispelDebuffsTidepup`)

### C3

1. Tame 1 X-Mosasaurus (`Task.ToF.TameXMosasaurus`) → Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsB`) → Defeat Boss: Gamma Moeder, Master of the Ocean (`Task.ToF.DefeatGammaMoeder`)
2. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineA`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsD`) → Raid 5 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsB`)
3. Collect 25 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoD`) → Collect 15 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoB`) → Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoB`)

### C4

1. Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusA`) → Complete 10 Gamma Missions (`Task.ToF.CompleteGammaMissionsC`) → Defeat Boss: Gamma Corrupted Master Controller (`Task.ToF.DefeatGammaController`)
2. Create 10 Blood Packs (`Task.ToF.CreateBloodPacksA`) → Tame 1 Bloodstalker (`Task.ToF.TameBloodstalkerA`) → Complete 5 Bog Biome Missions (`Task.ToF.CompleteBogMissionsA`)
3. Tame 3 X-Dunkleosteus (`Task.ToF.TameXDunkleosteus`) → Harvest 10 Shell Fragments (`Task.ToF.HarvestShellFragments`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillB`)

### C5

1. Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoC`) → Complete 3 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsB`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsC`)
2. Craft 1 Mining Drill (`Task.ToF.CraftMiningDrill`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillA`) → Harvest 25,000 Raw Metal (`Task.ToF.HarvestRawMetalB`)
3. Tame 3 Tidepups (`Task.ToF.TameTidepupB`) → Evolve 1 Tidepup via Neotenic Stabilization (`Task.ToF.EvolveTidepupNeotenic`) → Have 10 debuffs dispelled by Tidepup on your shoulder (`Task.ToF.DispelDebuffsTidepup`)

### C6

1. Tame 1 X-Mosasaurus (`Task.ToF.TameXMosasaurus`) → Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsB`) → Defeat Boss: Gamma Moeder, Master of the Ocean (`Task.ToF.DefeatGammaMoeder`)
2. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineA`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsD`) → Raid 5 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsB`)
3. Create 10 Blood Packs (`Task.ToF.CreateBloodPacksA`) → Tame 1 Bloodstalker (`Task.ToF.TameBloodstalkerA`) → Complete 5 Bog Biome Missions (`Task.ToF.CompleteBogMissionsA`)

### C7

1. Complete 3 Apprentice Contracts (`Task.ToF.CompleteApprContractB`) → Complete 1 Journeyman Contract (`Task.ToF.CompleteJourContractA`) → Spend 5,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsD`)
2. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineA`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsD`) → Raid 5 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsB`)
3. Tame 3 X-Dunkleosteus (`Task.ToF.TameXDunkleosteus`) → Harvest 10 Shell Fragments (`Task.ToF.HarvestShellFragments`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillB`)

### C8

1. Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoC`) → Complete 3 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsB`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsC`)
2. Craft 1 Mining Drill (`Task.ToF.CraftMiningDrill`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillA`) → Harvest 25,000 Raw Metal (`Task.ToF.HarvestRawMetalB`)
3. Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusA`) → Complete 10 Gamma Missions (`Task.ToF.CompleteGammaMissionsC`) → Defeat Boss: Gamma Corrupted Master Controller (`Task.ToF.DefeatGammaController`)

### C9

1. Complete 3 Apprentice Contracts (`Task.ToF.CompleteApprContractB`) → Complete 1 Journeyman Contract (`Task.ToF.CompleteJourContractA`) → Spend 5,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsD`)
2. Tame 3 Tidepups (`Task.ToF.TameTidepupB`) → Evolve 1 Tidepup via Neotenic Stabilization (`Task.ToF.EvolveTidepupNeotenic`) → Have 10 debuffs dispelled by Tidepup on your shoulder (`Task.ToF.DispelDebuffsTidepup`)
3. Tame 3 X-Dunkleosteus (`Task.ToF.TameXDunkleosteus`) → Harvest 10 Shell Fragments (`Task.ToF.HarvestShellFragments`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillB`)

### C10

1. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineA`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsD`) → Raid 5 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsB`)
2. Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusA`) → Complete 10 Gamma Missions (`Task.ToF.CompleteGammaMissionsC`) → Defeat Boss: Gamma Corrupted Master Controller (`Task.ToF.DefeatGammaController`)
3. Create 10 Blood Packs (`Task.ToF.CreateBloodPacksA`) → Tame 1 Bloodstalker (`Task.ToF.TameBloodstalkerA`) → Complete 5 Bog Biome Missions (`Task.ToF.CompleteBogMissionsA`)

### C11

1. Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsA`) → Complete 5 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsA`) → Complete 5 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsA`)
2. Tame 1 X-Rex (`Task.ToF.TameXRex`) → Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusB`) → Tame 1 X-Spino (`Task.ToF.TameXSpino`)
3. Collect 25 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoD`) → Collect 15 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoB`) → Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoB`)

### C12

1. Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoC`) → Complete 3 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsB`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsC`)
2. Tame 3 Tidepups (`Task.ToF.TameTidepupB`) → Evolve 1 Tidepup via Neotenic Stabilization (`Task.ToF.EvolveTidepupNeotenic`) → Have 10 debuffs dispelled by Tidepup on your shoulder (`Task.ToF.DispelDebuffsTidepup`)
3. Tame 1 X-Mosasaurus (`Task.ToF.TameXMosasaurus`) → Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsB`) → Defeat Boss: Gamma Moeder, Master of the Ocean (`Task.ToF.DefeatGammaMoeder`)

### C13

1. Craft 1 Mining Drill (`Task.ToF.CraftMiningDrill`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillA`) → Harvest 25,000 Raw Metal (`Task.ToF.HarvestRawMetalB`)
2. Tame 1 X-Mosasaurus (`Task.ToF.TameXMosasaurus`) → Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsB`) → Defeat Boss: Gamma Moeder, Master of the Ocean (`Task.ToF.DefeatGammaMoeder`)
3. Create 10 Blood Packs (`Task.ToF.CreateBloodPacksA`) → Tame 1 Bloodstalker (`Task.ToF.TameBloodstalkerA`) → Complete 5 Bog Biome Missions (`Task.ToF.CompleteBogMissionsA`)

### C14

1. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineA`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsD`) → Raid 5 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsB`)
2. Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusA`) → Complete 10 Gamma Missions (`Task.ToF.CompleteGammaMissionsC`) → Defeat Boss: Gamma Corrupted Master Controller (`Task.ToF.DefeatGammaController`)
3. Tame 3 X-Dunkleosteus (`Task.ToF.TameXDunkleosteus`) → Harvest 10 Shell Fragments (`Task.ToF.HarvestShellFragments`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillB`)

### C15

1. Complete 3 Apprentice Contracts (`Task.ToF.CompleteApprContractB`) → Complete 1 Journeyman Contract (`Task.ToF.CompleteJourContractA`) → Spend 5,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsD`)
2. Craft 1 Mining Drill (`Task.ToF.CraftMiningDrill`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillA`) → Harvest 25,000 Raw Metal (`Task.ToF.HarvestRawMetalB`)
3. Tame 3 Tidepups (`Task.ToF.TameTidepupB`) → Evolve 1 Tidepup via Neotenic Stabilization (`Task.ToF.EvolveTidepupNeotenic`) → Have 10 debuffs dispelled by Tidepup on your shoulder (`Task.ToF.DispelDebuffsTidepup`)

### C16

1. Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoC`) → Complete 3 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsB`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsC`)
2. Create 10 Blood Packs (`Task.ToF.CreateBloodPacksA`) → Tame 1 Bloodstalker (`Task.ToF.TameBloodstalkerA`) → Complete 5 Bog Biome Missions (`Task.ToF.CompleteBogMissionsA`)
3. Tame 3 X-Dunkleosteus (`Task.ToF.TameXDunkleosteus`) → Harvest 10 Shell Fragments (`Task.ToF.HarvestShellFragments`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillB`)

### C17

1. Complete 3 Apprentice Contracts (`Task.ToF.CompleteApprContractB`) → Complete 1 Journeyman Contract (`Task.ToF.CompleteJourContractA`) → Spend 5,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsD`)
2. Tame 1 X-Rex (`Task.ToF.TameXRex`) → Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusB`) → Tame 1 X-Spino (`Task.ToF.TameXSpino`)
3. Collect 25 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoD`) → Collect 15 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoB`) → Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoB`)

### C18

1. Tame 1 X-Mosasaurus (`Task.ToF.TameXMosasaurus`) → Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsB`) → Defeat Boss: Gamma Moeder, Master of the Ocean (`Task.ToF.DefeatGammaMoeder`)
2. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineA`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsD`) → Raid 5 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsB`)
3. Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusA`) → Complete 10 Gamma Missions (`Task.ToF.CompleteGammaMissionsC`) → Defeat Boss: Gamma Corrupted Master Controller (`Task.ToF.DefeatGammaController`)

### C19

1. Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsA`) → Complete 5 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsA`) → Complete 5 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsA`)
2. Tame 3 Tidepups (`Task.ToF.TameTidepupB`) → Evolve 1 Tidepup via Neotenic Stabilization (`Task.ToF.EvolveTidepupNeotenic`) → Have 10 debuffs dispelled by Tidepup on your shoulder (`Task.ToF.DispelDebuffsTidepup`)
3. Collect 25 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoD`) → Collect 15 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoB`) → Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoB`)

### C20

1. Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoC`) → Complete 3 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsB`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsC`)
2. Craft 1 Mining Drill (`Task.ToF.CraftMiningDrill`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillA`) → Harvest 25,000 Raw Metal (`Task.ToF.HarvestRawMetalB`)
3. Tame 1 X-Mosasaurus (`Task.ToF.TameXMosasaurus`) → Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsB`) → Defeat Boss: Gamma Moeder, Master of the Ocean (`Task.ToF.DefeatGammaMoeder`)

### C21

1. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineA`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsD`) → Raid 5 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsB`)
2. Create 10 Blood Packs (`Task.ToF.CreateBloodPacksA`) → Tame 1 Bloodstalker (`Task.ToF.TameBloodstalkerA`) → Complete 5 Bog Biome Missions (`Task.ToF.CompleteBogMissionsA`)
3. Tame 3 X-Dunkleosteus (`Task.ToF.TameXDunkleosteus`) → Harvest 10 Shell Fragments (`Task.ToF.HarvestShellFragments`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillB`)

### C22

1. Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsA`) → Complete 5 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsA`) → Complete 5 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsA`)
2. Craft 1 Mining Drill (`Task.ToF.CraftMiningDrill`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillA`) → Harvest 25,000 Raw Metal (`Task.ToF.HarvestRawMetalB`)
3. Tame 1 X-Rex (`Task.ToF.TameXRex`) → Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusB`) → Tame 1 X-Spino (`Task.ToF.TameXSpino`)

### C23

1. Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoC`) → Complete 3 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsB`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsC`)
2. Tame 3 Tidepups (`Task.ToF.TameTidepupB`) → Evolve 1 Tidepup via Neotenic Stabilization (`Task.ToF.EvolveTidepupNeotenic`) → Have 10 debuffs dispelled by Tidepup on your shoulder (`Task.ToF.DispelDebuffsTidepup`)
3. Tame 1 X-Yutyrannus (`Task.ToF.TameXYutyrannusA`) → Complete 10 Gamma Missions (`Task.ToF.CompleteGammaMissionsC`) → Defeat Boss: Gamma Corrupted Master Controller (`Task.ToF.DefeatGammaController`)

### C24

1. Complete 5 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsA`) → Complete 5 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsA`) → Complete 5 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsA`)
2. Collect 25 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoD`) → Collect 15 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoB`) → Collect 5 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoB`)
3. Tame 3 X-Dunkleosteus (`Task.ToF.TameXDunkleosteus`) → Harvest 10 Shell Fragments (`Task.ToF.HarvestShellFragments`) → Harvest 150,000 Resources with Mining Drill (`Task.ToF.HarvestMiningDrillB`)

### C25

1. Complete 3 Apprentice Contracts (`Task.ToF.CompleteApprContractB`) → Complete 1 Journeyman Contract (`Task.ToF.CompleteJourContractA`) → Spend 5,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsD`)
2. Tame 3 Tidepups (`Task.ToF.TameTidepupB`) → Evolve 1 Tidepup via Neotenic Stabilization (`Task.ToF.EvolveTidepupNeotenic`) → Have 10 debuffs dispelled by Tidepup on your shoulder (`Task.ToF.DispelDebuffsTidepup`)
3. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineA`) → Destroy 10 Pirate Ships (`Task.ToF.DestroyPirateShipsD`) → Raid 5 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsB`)

## 阶段 4：25 个候选配置

### C1

1. Craft 1 Tek Hover Skiff (`Task.ToF.CraftHoverSkiff`) → Travel 100,000 Meters Distance with Tek Hover Skiff (`Task.ToF.TravelHoverSkiff`) → Complete 10 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsB`)
2. Complete 3 Journeyman Contracts (`Task.ToF.CompleteJourContractB`) → Complete 1 Mastercraft Contract (`Task.ToF.CompleteMasterContractA`) → Spend 10,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsE`)
3. Tame 1 X-Mosasaurus at 90% Taming Effectiveness (`Task.ToF.TameXMosasaurusHighEff`) → Tame 1 Megachelon (`Task.ToF.TameMegachelon`) → Tame 3 Palaeoctopus (`Task.ToF.TamePalaeoctopusA`)

### C2

1. Raid 10 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsC`) → Complete 5 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsC`) → Defeat 5 Fleet Captain Ships (`Task.ToF.DefeatFleetCaptainB`)
2. Tame 5 Tidepups (`Task.ToF.TameTidepupC`) → Evolve 1 Tidepup via Metamorphosis (`Task.ToF.EvolveTidepupMetamorph`) → Restore 50,000 health to allies while mounted on a Tidepup (`Task.ToF.HealAlliesTidepup`)
3. Complete 3 Gauntlet Missions (`Task.ToF.CompleteGauntletMissions`) → Complete 10 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsB`) → Defeat Boss: Beta Corrupted Master Controller (`Task.ToF.DefeatBetaController`)

### C3

1. Harvest 5,000 Element Shards (`Task.ToF.HarvestElementShards`) → Tame 1 Ferox at level 135+ (`Task.ToF.TameFeroxHighLvl`) → Tame 1 Ferox at 90% Taming Effectiveness (`Task.ToF.TameFeroxHighEff`)
2. Tame 1 Astrocetus (`Task.ToF.TameAstrocetusA`) → Obtain 50 Ambergris (`Task.ToF.ObtainAmbergris`) → Travel 100,000 Meters Distance with Astrocetus (`Task.ToF.TravelAstrocetus`)
3. Deal 150,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonA`) → Fire 100 Grenades with Tek Grenade Launcher (`Task.ToF.FireTekGrenades`) → Deal 100,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonA`)

### C4

1. Tame 5 X-Megalodon (`Task.ToF.TameXMegalodon`) → Complete 10 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsC`) → Defeat Boss: Beta Moeder, Master of the Ocean (`Task.ToF.DefeatBetaMoeder`)
2. Complete 10 Beta Missions (`Task.ToF.CompleteBetaMissionsB`) → Complete 10 Bog Biome Missions (`Task.ToF.CompleteBogMissionsB`) → Complete 10 Arctic Biome Missions (`Task.ToF.CompleteArcticMissionsA`)
3. Steal 1 Magmasaur Egg (`Task.ToF.StealMagmasaurEgg`) → Harvest 50,000 Obsidian (`Task.ToF.HarvestObsidian`) → Harvest 250,000 Raw Metal (`Task.ToF.HarvestRawMetalC`)

### C5

1. Craft 1 Tek Hover Skiff (`Task.ToF.CraftHoverSkiff`) → Travel 100,000 Meters Distance with Tek Hover Skiff (`Task.ToF.TravelHoverSkiff`) → Complete 10 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsB`)
2. Tame 5 Tidepups (`Task.ToF.TameTidepupC`) → Evolve 1 Tidepup via Metamorphosis (`Task.ToF.EvolveTidepupMetamorph`) → Restore 50,000 health to allies while mounted on a Tidepup (`Task.ToF.HealAlliesTidepup`)
3. Deal 150,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonA`) → Fire 100 Grenades with Tek Grenade Launcher (`Task.ToF.FireTekGrenades`) → Deal 100,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonA`)

### C6

1. Raid 10 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsC`) → Complete 5 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsC`) → Defeat 5 Fleet Captain Ships (`Task.ToF.DefeatFleetCaptainB`)
2. Tame 1 Astrocetus (`Task.ToF.TameAstrocetusA`) → Obtain 50 Ambergris (`Task.ToF.ObtainAmbergris`) → Travel 100,000 Meters Distance with Astrocetus (`Task.ToF.TravelAstrocetus`)
3. Steal 1 Magmasaur Egg (`Task.ToF.StealMagmasaurEgg`) → Harvest 50,000 Obsidian (`Task.ToF.HarvestObsidian`) → Harvest 250,000 Raw Metal (`Task.ToF.HarvestRawMetalC`)

### C7

1. Complete 3 Gauntlet Missions (`Task.ToF.CompleteGauntletMissions`) → Complete 10 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsB`) → Defeat Boss: Beta Corrupted Master Controller (`Task.ToF.DefeatBetaController`)
2. Harvest 5,000 Element Shards (`Task.ToF.HarvestElementShards`) → Tame 1 Ferox at level 135+ (`Task.ToF.TameFeroxHighLvl`) → Tame 1 Ferox at 90% Taming Effectiveness (`Task.ToF.TameFeroxHighEff`)
3. Deal 150,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonA`) → Fire 100 Grenades with Tek Grenade Launcher (`Task.ToF.FireTekGrenades`) → Deal 100,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonA`)

### C8

1. Craft 1 Tek Hover Skiff (`Task.ToF.CraftHoverSkiff`) → Travel 100,000 Meters Distance with Tek Hover Skiff (`Task.ToF.TravelHoverSkiff`) → Complete 10 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsB`)
2. Tame 5 Tidepups (`Task.ToF.TameTidepupC`) → Evolve 1 Tidepup via Metamorphosis (`Task.ToF.EvolveTidepupMetamorph`) → Restore 50,000 health to allies while mounted on a Tidepup (`Task.ToF.HealAlliesTidepup`)
3. Tame 5 X-Megalodon (`Task.ToF.TameXMegalodon`) → Complete 10 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsC`) → Defeat Boss: Beta Moeder, Master of the Ocean (`Task.ToF.DefeatBetaMoeder`)

### C9

1. Complete 3 Journeyman Contracts (`Task.ToF.CompleteJourContractB`) → Complete 1 Mastercraft Contract (`Task.ToF.CompleteMasterContractA`) → Spend 10,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsE`)
2. Tame 1 Astrocetus (`Task.ToF.TameAstrocetusA`) → Obtain 50 Ambergris (`Task.ToF.ObtainAmbergris`) → Travel 100,000 Meters Distance with Astrocetus (`Task.ToF.TravelAstrocetus`)
3. Steal 1 Magmasaur Egg (`Task.ToF.StealMagmasaurEgg`) → Harvest 50,000 Obsidian (`Task.ToF.HarvestObsidian`) → Harvest 250,000 Raw Metal (`Task.ToF.HarvestRawMetalC`)

### C10

1. Raid 10 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsC`) → Complete 5 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsC`) → Defeat 5 Fleet Captain Ships (`Task.ToF.DefeatFleetCaptainB`)
2. Harvest 5,000 Element Shards (`Task.ToF.HarvestElementShards`) → Tame 1 Ferox at level 135+ (`Task.ToF.TameFeroxHighLvl`) → Tame 1 Ferox at 90% Taming Effectiveness (`Task.ToF.TameFeroxHighEff`)
3. Deal 150,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonA`) → Fire 100 Grenades with Tek Grenade Launcher (`Task.ToF.FireTekGrenades`) → Deal 100,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonA`)

### C11

1. Complete 3 Journeyman Contracts (`Task.ToF.CompleteJourContractB`) → Complete 1 Mastercraft Contract (`Task.ToF.CompleteMasterContractA`) → Spend 10,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsE`)
2. Tame 5 Tidepups (`Task.ToF.TameTidepupC`) → Evolve 1 Tidepup via Metamorphosis (`Task.ToF.EvolveTidepupMetamorph`) → Restore 50,000 health to allies while mounted on a Tidepup (`Task.ToF.HealAlliesTidepup`)
3. Tame 1 Astrocetus (`Task.ToF.TameAstrocetusA`) → Obtain 50 Ambergris (`Task.ToF.ObtainAmbergris`) → Travel 100,000 Meters Distance with Astrocetus (`Task.ToF.TravelAstrocetus`)

### C12

1. Craft 1 Tek Hover Skiff (`Task.ToF.CraftHoverSkiff`) → Travel 100,000 Meters Distance with Tek Hover Skiff (`Task.ToF.TravelHoverSkiff`) → Complete 10 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsB`)
2. Tame 5 X-Megalodon (`Task.ToF.TameXMegalodon`) → Complete 10 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsC`) → Defeat Boss: Beta Moeder, Master of the Ocean (`Task.ToF.DefeatBetaMoeder`)
3. Steal 1 Magmasaur Egg (`Task.ToF.StealMagmasaurEgg`) → Harvest 50,000 Obsidian (`Task.ToF.HarvestObsidian`) → Harvest 250,000 Raw Metal (`Task.ToF.HarvestRawMetalC`)

### C13

1. Tame 5 Tidepups (`Task.ToF.TameTidepupC`) → Evolve 1 Tidepup via Metamorphosis (`Task.ToF.EvolveTidepupMetamorph`) → Restore 50,000 health to allies while mounted on a Tidepup (`Task.ToF.HealAlliesTidepup`)
2. Complete 3 Gauntlet Missions (`Task.ToF.CompleteGauntletMissions`) → Complete 10 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsB`) → Defeat Boss: Beta Corrupted Master Controller (`Task.ToF.DefeatBetaController`)
3. Deal 150,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonA`) → Fire 100 Grenades with Tek Grenade Launcher (`Task.ToF.FireTekGrenades`) → Deal 100,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonA`)

### C14

1. Craft 1 Tek Hover Skiff (`Task.ToF.CraftHoverSkiff`) → Travel 100,000 Meters Distance with Tek Hover Skiff (`Task.ToF.TravelHoverSkiff`) → Complete 10 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsB`)
2. Raid 10 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsC`) → Complete 5 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsC`) → Defeat 5 Fleet Captain Ships (`Task.ToF.DefeatFleetCaptainB`)
3. Harvest 5,000 Element Shards (`Task.ToF.HarvestElementShards`) → Tame 1 Ferox at level 135+ (`Task.ToF.TameFeroxHighLvl`) → Tame 1 Ferox at 90% Taming Effectiveness (`Task.ToF.TameFeroxHighEff`)

### C15

1. Tame 1 X-Mosasaurus at 90% Taming Effectiveness (`Task.ToF.TameXMosasaurusHighEff`) → Tame 1 Megachelon (`Task.ToF.TameMegachelon`) → Tame 3 Palaeoctopus (`Task.ToF.TamePalaeoctopusA`)
2. Complete 10 Beta Missions (`Task.ToF.CompleteBetaMissionsB`) → Complete 10 Bog Biome Missions (`Task.ToF.CompleteBogMissionsB`) → Complete 10 Arctic Biome Missions (`Task.ToF.CompleteArcticMissionsA`)
3. Steal 1 Magmasaur Egg (`Task.ToF.StealMagmasaurEgg`) → Harvest 50,000 Obsidian (`Task.ToF.HarvestObsidian`) → Harvest 250,000 Raw Metal (`Task.ToF.HarvestRawMetalC`)

### C16

1. Raid 10 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsC`) → Complete 5 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsC`) → Defeat 5 Fleet Captain Ships (`Task.ToF.DefeatFleetCaptainB`)
2. Tame 1 Astrocetus (`Task.ToF.TameAstrocetusA`) → Obtain 50 Ambergris (`Task.ToF.ObtainAmbergris`) → Travel 100,000 Meters Distance with Astrocetus (`Task.ToF.TravelAstrocetus`)
3. Tame 5 X-Megalodon (`Task.ToF.TameXMegalodon`) → Complete 10 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsC`) → Defeat Boss: Beta Moeder, Master of the Ocean (`Task.ToF.DefeatBetaMoeder`)

### C17

1. Complete 3 Journeyman Contracts (`Task.ToF.CompleteJourContractB`) → Complete 1 Mastercraft Contract (`Task.ToF.CompleteMasterContractA`) → Spend 10,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsE`)
2. Harvest 5,000 Element Shards (`Task.ToF.HarvestElementShards`) → Tame 1 Ferox at level 135+ (`Task.ToF.TameFeroxHighLvl`) → Tame 1 Ferox at 90% Taming Effectiveness (`Task.ToF.TameFeroxHighEff`)
3. Deal 150,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonA`) → Fire 100 Grenades with Tek Grenade Launcher (`Task.ToF.FireTekGrenades`) → Deal 100,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonA`)

### C18

1. Tame 5 Tidepups (`Task.ToF.TameTidepupC`) → Evolve 1 Tidepup via Metamorphosis (`Task.ToF.EvolveTidepupMetamorph`) → Restore 50,000 health to allies while mounted on a Tidepup (`Task.ToF.HealAlliesTidepup`)
2. Complete 3 Gauntlet Missions (`Task.ToF.CompleteGauntletMissions`) → Complete 10 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsB`) → Defeat Boss: Beta Corrupted Master Controller (`Task.ToF.DefeatBetaController`)
3. Tame 1 Astrocetus (`Task.ToF.TameAstrocetusA`) → Obtain 50 Ambergris (`Task.ToF.ObtainAmbergris`) → Travel 100,000 Meters Distance with Astrocetus (`Task.ToF.TravelAstrocetus`)

### C19

1. Raid 10 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsC`) → Complete 5 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsC`) → Defeat 5 Fleet Captain Ships (`Task.ToF.DefeatFleetCaptainB`)
2. Tame 5 X-Megalodon (`Task.ToF.TameXMegalodon`) → Complete 10 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsC`) → Defeat Boss: Beta Moeder, Master of the Ocean (`Task.ToF.DefeatBetaMoeder`)
3. Steal 1 Magmasaur Egg (`Task.ToF.StealMagmasaurEgg`) → Harvest 50,000 Obsidian (`Task.ToF.HarvestObsidian`) → Harvest 250,000 Raw Metal (`Task.ToF.HarvestRawMetalC`)

### C20

1. Tame 1 X-Mosasaurus at 90% Taming Effectiveness (`Task.ToF.TameXMosasaurusHighEff`) → Tame 1 Megachelon (`Task.ToF.TameMegachelon`) → Tame 3 Palaeoctopus (`Task.ToF.TamePalaeoctopusA`)
2. Deal 150,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonA`) → Fire 100 Grenades with Tek Grenade Launcher (`Task.ToF.FireTekGrenades`) → Deal 100,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonA`)
3. Complete 10 Beta Missions (`Task.ToF.CompleteBetaMissionsB`) → Complete 10 Bog Biome Missions (`Task.ToF.CompleteBogMissionsB`) → Complete 10 Arctic Biome Missions (`Task.ToF.CompleteArcticMissionsA`)

### C21

1. Complete 3 Gauntlet Missions (`Task.ToF.CompleteGauntletMissions`) → Complete 10 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsB`) → Defeat Boss: Beta Corrupted Master Controller (`Task.ToF.DefeatBetaController`)
2. Tame 1 Astrocetus (`Task.ToF.TameAstrocetusA`) → Obtain 50 Ambergris (`Task.ToF.ObtainAmbergris`) → Travel 100,000 Meters Distance with Astrocetus (`Task.ToF.TravelAstrocetus`)
3. Steal 1 Magmasaur Egg (`Task.ToF.StealMagmasaurEgg`) → Harvest 50,000 Obsidian (`Task.ToF.HarvestObsidian`) → Harvest 250,000 Raw Metal (`Task.ToF.HarvestRawMetalC`)

### C22

1. Craft 1 Tek Hover Skiff (`Task.ToF.CraftHoverSkiff`) → Travel 100,000 Meters Distance with Tek Hover Skiff (`Task.ToF.TravelHoverSkiff`) → Complete 10 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsB`)
2. Raid 10 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsC`) → Complete 5 Treasure Map Bottles (`Task.ToF.CompleteTreasureMapsC`) → Defeat 5 Fleet Captain Ships (`Task.ToF.DefeatFleetCaptainB`)
3. Tame 5 Tidepups (`Task.ToF.TameTidepupC`) → Evolve 1 Tidepup via Metamorphosis (`Task.ToF.EvolveTidepupMetamorph`) → Restore 50,000 health to allies while mounted on a Tidepup (`Task.ToF.HealAlliesTidepup`)

### C23

1. Complete 3 Journeyman Contracts (`Task.ToF.CompleteJourContractB`) → Complete 1 Mastercraft Contract (`Task.ToF.CompleteMasterContractA`) → Spend 10,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsE`)
2. Tame 1 Astrocetus (`Task.ToF.TameAstrocetusA`) → Obtain 50 Ambergris (`Task.ToF.ObtainAmbergris`) → Travel 100,000 Meters Distance with Astrocetus (`Task.ToF.TravelAstrocetus`)
3. Deal 150,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonA`) → Fire 100 Grenades with Tek Grenade Launcher (`Task.ToF.FireTekGrenades`) → Deal 100,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonA`)

### C24

1. Craft 1 Tek Hover Skiff (`Task.ToF.CraftHoverSkiff`) → Travel 100,000 Meters Distance with Tek Hover Skiff (`Task.ToF.TravelHoverSkiff`) → Complete 10 Space Biome Missions (`Task.ToF.CompleteSpaceMissionsB`)
2. Tame 1 X-Mosasaurus at 90% Taming Effectiveness (`Task.ToF.TameXMosasaurusHighEff`) → Tame 1 Megachelon (`Task.ToF.TameMegachelon`) → Tame 3 Palaeoctopus (`Task.ToF.TamePalaeoctopusA`)
3. Complete 3 Gauntlet Missions (`Task.ToF.CompleteGauntletMissions`) → Complete 10 Volcanic Biome Missions (`Task.ToF.CompleteVolcanicMissionsB`) → Defeat Boss: Beta Corrupted Master Controller (`Task.ToF.DefeatBetaController`)

### C25

1. Complete 3 Journeyman Contracts (`Task.ToF.CompleteJourContractB`) → Complete 1 Mastercraft Contract (`Task.ToF.CompleteMasterContractA`) → Spend 10,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsE`)
2. Tame 5 Tidepups (`Task.ToF.TameTidepupC`) → Evolve 1 Tidepup via Metamorphosis (`Task.ToF.EvolveTidepupMetamorph`) → Restore 50,000 health to allies while mounted on a Tidepup (`Task.ToF.HealAlliesTidepup`)
3. Steal 1 Magmasaur Egg (`Task.ToF.StealMagmasaurEgg`) → Harvest 50,000 Obsidian (`Task.ToF.HarvestObsidian`) → Harvest 250,000 Raw Metal (`Task.ToF.HarvestRawMetalC`)

## 阶段 5：25 个候选配置

### C1

1. Tame 3 X-Rex at level 135+ (`Task.ToF.TameXRexHighLvl`) → Complete 5 Alpha Missions (`Task.ToF.CompleteAlphaMissions`) → Defeat Boss: Alpha Corrupted Master Controller (`Task.ToF.DefeatAlphaController`)
2. Create 100 Blood Packs (`Task.ToF.CreateBloodPacksB`) → Tame 5 Bloodstalkers (`Task.ToF.TameBloodstalkerB`) → Complete 25 Bog Biome Missions (`Task.ToF.CompleteBogMissionsC`)
3. Construct 5 Brigantines (`Task.ToF.ConstructBrigantineB`) → Destroy 50 Pirate Ships (`Task.ToF.DestroyPirateShipsE`) → Raid 25 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsD`)

### C2

1. Tame 5 X-Megalodon at level 135+ (`Task.ToF.TameXMegalodonHighLvl`) → Tame 5 X-Mosasaurus at level 135+ (`Task.ToF.TameXMosasaurusHighLvl`) → Defeat Boss: Alpha Moeder, Master of the Ocean (`Task.ToF.DefeatAlphaMoeder`)
2. Complete 3 Mastercraft Contracts (`Task.ToF.CompleteMasterContractB`) → Complete 1 Ascendant Contract (`Task.ToF.CompleteAscContract`) → Spend 50,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsF`)
3. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineC`) → Travel 250,000 Meters Distance with Brigantine (`Task.ToF.TravelBrigantine`) → Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineB`)

### C3

1. Craft 10 Cruise Missiles (`Task.ToF.CraftCruiseMissiles`) → Tame 3 Astrocetus (`Task.ToF.TameAstrocetusB`) → Tame 1 Astrocetus at 90% Taming Effectiveness (`Task.ToF.TameAstrocetusHighEff`)
2. Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineA`) → Deal 250,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonB`) → Deal 500,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonB`)
3. Tame 1 Palaeoctopus at level 135+ (`Task.ToF.TamePalaeoctopusHighLvl`) → Tame 1 Megachelon at level 135+ (`Task.ToF.TameMegachelonHighLvl`) → Complete 25 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsD`)

### C4

1. Collect 100 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoE`) → Collect 75 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoC`) → Collect 25 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoD`)
2. Complete 10 Alpha Arctic Biome Missions (`Task.ToF.CompleteAlphaArcticMissions`) → Complete 10 Alpha Volcanic Biome Missions (`Task.ToF.CompleteAlphaVolcanicMissions`) → Complete 10 Alpha Space Biome Missions (`Task.ToF.CompleteAlphaSpaceMissions`)
3. Harvest 1,000 Bio Toxin (`Task.ToF.HarvestBioToxin`) → Tame 5 Palaeoctopus (`Task.ToF.TamePalaeoctopusB`) → Tame 3 X-Basilosaurus at level 135+ (`Task.ToF.TameXBasilosaurusHighLvl`)

### C5

1. Tame 3 X-Rex at level 135+ (`Task.ToF.TameXRexHighLvl`) → Complete 5 Alpha Missions (`Task.ToF.CompleteAlphaMissions`) → Defeat Boss: Alpha Corrupted Master Controller (`Task.ToF.DefeatAlphaController`)
2. Construct 5 Brigantines (`Task.ToF.ConstructBrigantineB`) → Destroy 50 Pirate Ships (`Task.ToF.DestroyPirateShipsE`) → Raid 25 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsD`)
3. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineC`) → Travel 250,000 Meters Distance with Brigantine (`Task.ToF.TravelBrigantine`) → Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineB`)

### C6

1. Construct 5 Brigantines (`Task.ToF.ConstructBrigantineB`) → Destroy 50 Pirate Ships (`Task.ToF.DestroyPirateShipsE`) → Raid 25 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsD`)
2. Tame 5 X-Megalodon at level 135+ (`Task.ToF.TameXMegalodonHighLvl`) → Tame 5 X-Mosasaurus at level 135+ (`Task.ToF.TameXMosasaurusHighLvl`) → Defeat Boss: Alpha Moeder, Master of the Ocean (`Task.ToF.DefeatAlphaMoeder`)
3. Complete 3 Mastercraft Contracts (`Task.ToF.CompleteMasterContractB`) → Complete 1 Ascendant Contract (`Task.ToF.CompleteAscContract`) → Spend 50,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsF`)

### C7

1. Complete 3 Mastercraft Contracts (`Task.ToF.CompleteMasterContractB`) → Complete 1 Ascendant Contract (`Task.ToF.CompleteAscContract`) → Spend 50,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsF`)
2. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineC`) → Travel 250,000 Meters Distance with Brigantine (`Task.ToF.TravelBrigantine`) → Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineB`)
3. Craft 10 Cruise Missiles (`Task.ToF.CraftCruiseMissiles`) → Tame 3 Astrocetus (`Task.ToF.TameAstrocetusB`) → Tame 1 Astrocetus at 90% Taming Effectiveness (`Task.ToF.TameAstrocetusHighEff`)

### C8

1. Tame 3 X-Rex at level 135+ (`Task.ToF.TameXRexHighLvl`) → Complete 5 Alpha Missions (`Task.ToF.CompleteAlphaMissions`) → Defeat Boss: Alpha Corrupted Master Controller (`Task.ToF.DefeatAlphaController`)
2. Create 100 Blood Packs (`Task.ToF.CreateBloodPacksB`) → Tame 5 Bloodstalkers (`Task.ToF.TameBloodstalkerB`) → Complete 25 Bog Biome Missions (`Task.ToF.CompleteBogMissionsC`)
3. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineC`) → Travel 250,000 Meters Distance with Brigantine (`Task.ToF.TravelBrigantine`) → Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineB`)

### C9

1. Construct 5 Brigantines (`Task.ToF.ConstructBrigantineB`) → Destroy 50 Pirate Ships (`Task.ToF.DestroyPirateShipsE`) → Raid 25 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsD`)
2. Complete 3 Mastercraft Contracts (`Task.ToF.CompleteMasterContractB`) → Complete 1 Ascendant Contract (`Task.ToF.CompleteAscContract`) → Spend 50,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsF`)
3. Harvest 1,000 Bio Toxin (`Task.ToF.HarvestBioToxin`) → Tame 5 Palaeoctopus (`Task.ToF.TamePalaeoctopusB`) → Tame 3 X-Basilosaurus at level 135+ (`Task.ToF.TameXBasilosaurusHighLvl`)

### C10

1. Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineA`) → Deal 250,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonB`) → Deal 500,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonB`)
2. Tame 1 Palaeoctopus at level 135+ (`Task.ToF.TamePalaeoctopusHighLvl`) → Tame 1 Megachelon at level 135+ (`Task.ToF.TameMegachelonHighLvl`) → Complete 25 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsD`)
3. Collect 100 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoE`) → Collect 75 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoC`) → Collect 25 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoD`)

### C11

1. Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineA`) → Deal 250,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonB`) → Deal 500,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonB`)
2. Complete 10 Alpha Arctic Biome Missions (`Task.ToF.CompleteAlphaArcticMissions`) → Complete 10 Alpha Volcanic Biome Missions (`Task.ToF.CompleteAlphaVolcanicMissions`) → Complete 10 Alpha Space Biome Missions (`Task.ToF.CompleteAlphaSpaceMissions`)
3. Harvest 1,000 Bio Toxin (`Task.ToF.HarvestBioToxin`) → Tame 5 Palaeoctopus (`Task.ToF.TamePalaeoctopusB`) → Tame 3 X-Basilosaurus at level 135+ (`Task.ToF.TameXBasilosaurusHighLvl`)

### C12

1. Create 100 Blood Packs (`Task.ToF.CreateBloodPacksB`) → Tame 5 Bloodstalkers (`Task.ToF.TameBloodstalkerB`) → Complete 25 Bog Biome Missions (`Task.ToF.CompleteBogMissionsC`)
2. Construct 5 Brigantines (`Task.ToF.ConstructBrigantineB`) → Destroy 50 Pirate Ships (`Task.ToF.DestroyPirateShipsE`) → Raid 25 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsD`)
3. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineC`) → Travel 250,000 Meters Distance with Brigantine (`Task.ToF.TravelBrigantine`) → Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineB`)

### C13

1. Craft 10 Cruise Missiles (`Task.ToF.CraftCruiseMissiles`) → Tame 3 Astrocetus (`Task.ToF.TameAstrocetusB`) → Tame 1 Astrocetus at 90% Taming Effectiveness (`Task.ToF.TameAstrocetusHighEff`)
2. Collect 100 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoE`) → Collect 75 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoC`) → Collect 25 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoD`)
3. Complete 10 Alpha Arctic Biome Missions (`Task.ToF.CompleteAlphaArcticMissions`) → Complete 10 Alpha Volcanic Biome Missions (`Task.ToF.CompleteAlphaVolcanicMissions`) → Complete 10 Alpha Space Biome Missions (`Task.ToF.CompleteAlphaSpaceMissions`)

### C14

1. Construct 5 Brigantines (`Task.ToF.ConstructBrigantineB`) → Destroy 50 Pirate Ships (`Task.ToF.DestroyPirateShipsE`) → Raid 25 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsD`)
2. Tame 5 X-Megalodon at level 135+ (`Task.ToF.TameXMegalodonHighLvl`) → Tame 5 X-Mosasaurus at level 135+ (`Task.ToF.TameXMosasaurusHighLvl`) → Defeat Boss: Alpha Moeder, Master of the Ocean (`Task.ToF.DefeatAlphaMoeder`)
3. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineC`) → Travel 250,000 Meters Distance with Brigantine (`Task.ToF.TravelBrigantine`) → Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineB`)

### C15

1. Create 100 Blood Packs (`Task.ToF.CreateBloodPacksB`) → Tame 5 Bloodstalkers (`Task.ToF.TameBloodstalkerB`) → Complete 25 Bog Biome Missions (`Task.ToF.CompleteBogMissionsC`)
2. Tame 1 Palaeoctopus at level 135+ (`Task.ToF.TamePalaeoctopusHighLvl`) → Tame 1 Megachelon at level 135+ (`Task.ToF.TameMegachelonHighLvl`) → Complete 25 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsD`)
3. Collect 100 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoE`) → Collect 75 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoC`) → Collect 25 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoD`)

### C16

1. Construct 5 Brigantines (`Task.ToF.ConstructBrigantineB`) → Destroy 50 Pirate Ships (`Task.ToF.DestroyPirateShipsE`) → Raid 25 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsD`)
2. Complete 3 Mastercraft Contracts (`Task.ToF.CompleteMasterContractB`) → Complete 1 Ascendant Contract (`Task.ToF.CompleteAscContract`) → Spend 50,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsF`)
3. Craft 10 Cruise Missiles (`Task.ToF.CraftCruiseMissiles`) → Tame 3 Astrocetus (`Task.ToF.TameAstrocetusB`) → Tame 1 Astrocetus at 90% Taming Effectiveness (`Task.ToF.TameAstrocetusHighEff`)

### C17

1. Tame 3 X-Rex at level 135+ (`Task.ToF.TameXRexHighLvl`) → Complete 5 Alpha Missions (`Task.ToF.CompleteAlphaMissions`) → Defeat Boss: Alpha Corrupted Master Controller (`Task.ToF.DefeatAlphaController`)
2. Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineA`) → Deal 250,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonB`) → Deal 500,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonB`)
3. Harvest 1,000 Bio Toxin (`Task.ToF.HarvestBioToxin`) → Tame 5 Palaeoctopus (`Task.ToF.TamePalaeoctopusB`) → Tame 3 X-Basilosaurus at level 135+ (`Task.ToF.TameXBasilosaurusHighLvl`)

### C18

1. Tame 5 X-Megalodon at level 135+ (`Task.ToF.TameXMegalodonHighLvl`) → Tame 5 X-Mosasaurus at level 135+ (`Task.ToF.TameXMosasaurusHighLvl`) → Defeat Boss: Alpha Moeder, Master of the Ocean (`Task.ToF.DefeatAlphaMoeder`)
2. Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineA`) → Deal 250,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonB`) → Deal 500,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonB`)
3. Complete 10 Alpha Arctic Biome Missions (`Task.ToF.CompleteAlphaArcticMissions`) → Complete 10 Alpha Volcanic Biome Missions (`Task.ToF.CompleteAlphaVolcanicMissions`) → Complete 10 Alpha Space Biome Missions (`Task.ToF.CompleteAlphaSpaceMissions`)

### C19

1. Complete 3 Mastercraft Contracts (`Task.ToF.CompleteMasterContractB`) → Complete 1 Ascendant Contract (`Task.ToF.CompleteAscContract`) → Spend 50,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsF`)
2. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineC`) → Travel 250,000 Meters Distance with Brigantine (`Task.ToF.TravelBrigantine`) → Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineB`)
3. Harvest 1,000 Bio Toxin (`Task.ToF.HarvestBioToxin`) → Tame 5 Palaeoctopus (`Task.ToF.TamePalaeoctopusB`) → Tame 3 X-Basilosaurus at level 135+ (`Task.ToF.TameXBasilosaurusHighLvl`)

### C20

1. Tame 3 X-Rex at level 135+ (`Task.ToF.TameXRexHighLvl`) → Complete 5 Alpha Missions (`Task.ToF.CompleteAlphaMissions`) → Defeat Boss: Alpha Corrupted Master Controller (`Task.ToF.DefeatAlphaController`)
2. Tame 1 Palaeoctopus at level 135+ (`Task.ToF.TamePalaeoctopusHighLvl`) → Tame 1 Megachelon at level 135+ (`Task.ToF.TameMegachelonHighLvl`) → Complete 25 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsD`)
3. Collect 100 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoE`) → Collect 75 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoC`) → Collect 25 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoD`)

### C21

1. Create 100 Blood Packs (`Task.ToF.CreateBloodPacksB`) → Tame 5 Bloodstalkers (`Task.ToF.TameBloodstalkerB`) → Complete 25 Bog Biome Missions (`Task.ToF.CompleteBogMissionsC`)
2. Tame 5 X-Megalodon at level 135+ (`Task.ToF.TameXMegalodonHighLvl`) → Tame 5 X-Mosasaurus at level 135+ (`Task.ToF.TameXMosasaurusHighLvl`) → Defeat Boss: Alpha Moeder, Master of the Ocean (`Task.ToF.DefeatAlphaMoeder`)
3. Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineA`) → Deal 250,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonB`) → Deal 500,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonB`)

### C22

1. Construct 5 Brigantines (`Task.ToF.ConstructBrigantineB`) → Destroy 50 Pirate Ships (`Task.ToF.DestroyPirateShipsE`) → Raid 25 Pirate Camps or Ocean Outposts (`Task.ToF.RaidPirateCampsD`)
2. Construct 1 Brigantine (`Task.ToF.ConstructBrigantineC`) → Travel 250,000 Meters Distance with Brigantine (`Task.ToF.TravelBrigantine`) → Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineB`)
3. Tame 1 Palaeoctopus at level 135+ (`Task.ToF.TamePalaeoctopusHighLvl`) → Tame 1 Megachelon at level 135+ (`Task.ToF.TameMegachelonHighLvl`) → Complete 25 Ocean Biome Missions (`Task.ToF.CompleteOceanMissionsD`)

### C23

1. Create 100 Blood Packs (`Task.ToF.CreateBloodPacksB`) → Tame 5 Bloodstalkers (`Task.ToF.TameBloodstalkerB`) → Complete 25 Bog Biome Missions (`Task.ToF.CompleteBogMissionsC`)
2. Craft 10 Cruise Missiles (`Task.ToF.CraftCruiseMissiles`) → Tame 3 Astrocetus (`Task.ToF.TameAstrocetusB`) → Tame 1 Astrocetus at 90% Taming Effectiveness (`Task.ToF.TameAstrocetusHighEff`)
3. Collect 100 Ramshackle Abandoned Cargo with a ship (`Task.ToF.CollectRamsCargoE`) → Collect 75 Apprentice Abandoned Cargo with a ship (`Task.ToF.CollectApprCargoC`) → Collect 25 Journeyman Abandoned Cargo with a ship (`Task.ToF.CollectJourCargoD`)

### C24

1. Craft 10 Cruise Missiles (`Task.ToF.CraftCruiseMissiles`) → Tame 3 Astrocetus (`Task.ToF.TameAstrocetusB`) → Tame 1 Astrocetus at 90% Taming Effectiveness (`Task.ToF.TameAstrocetusHighEff`)
2. Deal 500,000 Damage with Brigantine (`Task.ToF.DealDamageBrigantineA`) → Deal 250,000 Damage with Tek Shoulder Cannon (`Task.ToF.DealDamageShoulderCannonB`) → Deal 500,000 Damage with Hand Cannon (`Task.ToF.DealDamageHandCannonB`)
3. Complete 10 Alpha Arctic Biome Missions (`Task.ToF.CompleteAlphaArcticMissions`) → Complete 10 Alpha Volcanic Biome Missions (`Task.ToF.CompleteAlphaVolcanicMissions`) → Complete 10 Alpha Space Biome Missions (`Task.ToF.CompleteAlphaSpaceMissions`)

### C25

1. Tame 3 X-Rex at level 135+ (`Task.ToF.TameXRexHighLvl`) → Complete 5 Alpha Missions (`Task.ToF.CompleteAlphaMissions`) → Defeat Boss: Alpha Corrupted Master Controller (`Task.ToF.DefeatAlphaController`)
2. Complete 3 Mastercraft Contracts (`Task.ToF.CompleteMasterContractB`) → Complete 1 Ascendant Contract (`Task.ToF.CompleteAscContract`) → Spend 50,000 Hexagons at the Hexchange (`Task.ToF.SpendHexagonsF`)
3. Harvest 1,000 Bio Toxin (`Task.ToF.HarvestBioToxin`) → Tame 5 Palaeoctopus (`Task.ToF.TamePalaeoctopusB`) → Tame 3 X-Basilosaurus at level 135+ (`Task.ToF.TameXBasilosaurusHighLvl`)
