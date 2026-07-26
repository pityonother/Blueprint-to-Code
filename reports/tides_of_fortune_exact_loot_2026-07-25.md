# Tides of Fortune 漂流瓶：六档完整物品奖池

生成日期：2026-07-25

数据来源为当前本机 ARK DevKit。奖池物品优先显示 DevKit 自带的官方简体中文名，并在括号内保留精确 Blueprint 类名，方便继续核验。
本报告的 292 个不同物品类均已完成中文对照：224 个按蓝图本地化源位置直接匹配，68 个按同一官方中文表中的英文显示名匹配，没有人工猜译项。
`SetWeight`、`EntryWeight` 和单物品权重都是各自层级的相对权重，不可直接当作最终百分比。

## 阅读规则

- 宝箱最多选择 4 个 Item Set。
- 每个 Item Set 再按 EntryWeight 选择条目；条目内若没有 ItemsWeights，物品按默认等权逻辑处理。
- `BP chance` 是该条目把装备变成蓝图的覆盖概率；`Force BP` 表示强制蓝图。
- 品质区间是 Item Entry 自身的 Min/Max Quality，之后仍受宝箱 2.0–4.0 倍率和服务器倍率影响。
- 名称格式示例：十字弩（Blueprint 类 PrimalItem_WeaponCrossbow）；中文用于阅读，括号内类名才是精确资产标识。

## 羁绊羽毛：游戏内具体使用方法

官方名称是**羁绊羽毛**（`PrimalItem_BondingFeather`）。官方物品说明为：“赠予任何生物的绝佳礼物。可代替任意印痕或被动驯服需求。”

### 最短操作步骤

1. 把羽毛放在**玩家自己的物品栏**里，最好拖到快捷栏；不要把它塞进目标生物的物品栏。
2. 走到目标附近，让准星直接对准目标生物本体。
3. 按对应的快捷栏按键使用。蓝图会先检查准星所指目标是否满足条件，通过后消耗 1 枚羽毛。
4. 成功后，目标会获得一个看不见但会保存的“已使用羁绊羽毛”标记；同一只生物以后不能再吃第二枚羁绊羽毛。

### 用在被动驯服目标上

- 目标必须是**正在进行被动驯服的有效野生生物**；昏迷喂食驯服不属于这套逻辑。
- 成功使用会直接补充驯服亲和度，公式是：

```text
增加量 = min(驯服所需总亲和度 × 30%，驯服剩余亲和度)
```

- 因此它最多增加总进度的 30 个百分点；如果目标原本已经达到 70% 或以上，可以直接补到 100% 并完成驯服。
- 它代替的是一次被动驯服需求，不是永久提高该生物的驯服速度。

### 用在幼崽留痕上

- 目标必须是你或部落拥有的**幼崽**，而且此刻已经出现一项留痕照料要求。
- 无论当前要求是散步、拥抱还是指定食物，羽毛都会代替这一次要求，并触发游戏正常的成功留痕流程。
- 羽毛在幼崽上**不是固定增加 30% 留痕**。实际增加多少，取决于该幼崽正常完成本次照料本应获得的留痕值，以及服务器的成长/留痕倍率。
- 如果幼崽尚未刷新出照料要求、已经成年，或当前没有有效请求，羽毛不会通过使用检查。

### 能否与血之灵药混用

可以。当前 DevKit 中，羁绊羽毛与血之灵药分别写入两个不同的永久隐藏标记，所以是**羽毛每只生物一次、血之灵药每只生物一次**，二者不共用次数。

- 幼崽：建议先用羁绊羽毛完成当前照料，再用血之灵药取得固定的 30% 留痕。
- 被动驯服：两件道具的亲和度公式都按总需求的 30% 计算，理论合计最多 60 个百分点；但如果第一件已经把目标推到 100% 并完成驯服，第二件就不再需要，也不能继续当作驯服道具使用。
- 同一种道具的已使用标记会随生物保存；重启服务器、重新进出渲染范围或让生物重新苏醒，都不能清掉次数。

### 按下快捷键没反应时检查

- 准星是否真的对准目标，而且距离足够近。
- 被动驯服目标是否正处于有效驯服状态，而不是昏迷驯服。
- 幼崽是否已经出现当前照料要求，以及是否属于你或同一部落。
- 这只生物是否已经用过一次羁绊羽毛；该标记不会显示在 HUD 上。
- 羽毛是否仍在玩家物品栏。它的蓝图基础腐坏时间是 `14400` 秒，即 4 小时；实际时间仍会受容器和服务器倍率影响。
- 客户端是否正确识别 Tides of Fortune DLC 权限；该物品蓝图带有 DLC 使用保护。

以上操作结论来自 `PrimalItem_BondingFeather` 的 `BPCanUse`、`CheckValidForUse`、`BlueprintUsed` 图和类默认值；“对准后从玩家快捷栏使用”是蓝图实际取准星目标的入口。

## 六档汇总

| 地图品质 | 等级 | Item Set 行数 | 去重物品类 | 宝箱倍率 |
|---|---:|---:|---:|---:|
| Primitive | 1 | 6 | 156 | 2.0–4.0 |
| Ramshackle | 15 | 6 | 150 | 2.0–4.0 |
| Apprentice | 25 | 5 | 144 | 2.0–4.0 |
| Journeyman | 35 | 7 | 76 | 2.0–4.0 |
| Mastercraft | 45 | 8 | 80 | 2.0–4.0 |
| Ascendant | 60 | 11 | 106 | 2.0–4.0 |

## Primitive

- 开箱等级：1
- 宝箱品质倍率：2.0–4.0

### 1. Lootset level 25

- SetWeight：0.3
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level25_Gen1.LootItemSet_SupplyDrop_Level25_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Structures - Useable | 0.4 | 1–2 | 0–1 | 0 | 弩炮台（`PrimalItemStructure_TurretBallista`） (item weight 0.2)<br>火炮（`PrimalItemStructure_Cannon`） (item weight 0.2)<br>绊线报警陷阱（`PrimalItem_WeaponAlarmTrap`） (item weight 2)<br>绊线麻醉陷阱（`PrimalItem_WeaponPoisonTrap`） (item weight 2)<br>精炼炉（`PrimalItemStructure_Forge`） (item weight 0.4)<br>马桶（`PrimalItemStructure_Toilet`） (item weight 1)<br>壁炉（`PrimalItemStructure_Fireplace`） (item weight 0.6)<br>树脂龙头（`PrimalItemStructure_TreeTap`） (item weight 2)<br>木制树屋平台（`PrimalItemStructure_TreePlatform_Wood`） (item weight 0.1)<br>神器底座（`PrimalItemStructure_TrophyBase`） (item weight 2) |
| Armor, Tools, and Weapons with Quality | 0.6 | 1–1 | 1.8–3.84 | 0.1 | 十字弩（`PrimalItem_WeaponCrossbow`）<br>金属镰刀（`PrimalItem_WeaponSickle`）<br>金属矛（`PrimalItem_WeaponPike`）<br>手铐（`PrimalItem_WeaponHandcuffs`）<br>毛皮靴（`PrimalItemArmor_FurBoots`）<br>毛皮手套（`PrimalItemArmor_FurGloves`）<br>毛皮帽（`PrimalItemArmor_FurHelmet`）<br>毛皮护腿（`PrimalItemArmor_FurPants`）<br>毛皮胸甲（`PrimalItemArmor_FurShirt`） |
| Saddles with Quality | 0.5 | 1–1 | 1.2–3.2 | 0.2 | 甲龙鞍（`PrimalItemArmor_AnkyloSaddle`）<br>剑齿虎鞍（`PrimalItemArmor_SaberSaddle`）<br>蜘蛛鞍（`PrimalItemArmor_SpiderSaddle`）<br>禽龙鞍（`PrimalItemArmor_IguanodonSaddle`）<br>大角鹿鞍（`PrimalItemArmor_StagSaddle`）<br>猛犸象鞍（`PrimalItemArmor_MammothSaddle`）<br>梁龙鞍（`PrimalItemArmor_DiplodocusSaddle`）<br>骇鸟鞍（`PrimalItemArmor_TerrorBirdSaddle`）<br>星尾兽鞍（`PrimalItemArmor_DoedSaddle`）<br>帝鳄鞍（`PrimalItemArmor_SarcoSaddle`）<br>潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`） |
| Armor, Tools, and Weapons with no Quality | 0.8 | 1–1 | 0–1 | 0 | 十字弩（`PrimalItem_WeaponCrossbow`）<br>金属镰刀（`PrimalItem_WeaponSickle`）<br>金属矛（`PrimalItem_WeaponPike`）<br>甲壳靴（`PrimalItemArmor_ChitinBoots`）<br>甲壳手套（`PrimalItemArmor_ChitinGloves`）<br>甲壳头盔（`PrimalItemArmor_ChitinHelmet`）<br>甲壳腿（`PrimalItemArmor_ChitinPants`）<br>甲壳胸甲（`PrimalItemArmor_ChitinShirt`）<br>水瓶（`PrimalItemConsumable_WaterJarRefill`） |
| Consumables - High Quantity | 1 | 5–30 | 0–1 | 0 | 麻醉药（`PrimalItemConsumable_Narcotic`）<br>兴奋剂（`PrimalItemConsumable_Stimulant`）<br>熟肉干（`PrimalItemConsumable_CookedMeat_Jerky`） |
| Consumables - Low Quantity | 0.9 | 1–3 | 0–1 | 0 | 肥皂（`PrimalItemConsumableSoap`）<br>耐力炖锅（`PrimalItemConsumable_Soup_EnduroStew`） |
| Resources | 1 | 20–100 | 0–1 | 0 | 甲壳素（`PrimalItemResource_Chitin`）<br>稀有花朵（`PrimalItemResource_RareFlower`）<br>稀有蘑菇（`PrimalItemResource_RareMushroom`）<br>水泥（`PrimalItemResource_ChitinPaste`）<br>毛皮（`PrimalItemResource_Pelt`） |
| Ammo | 0.8 | 5–50 | 0–1 | 0 | 石箭（`PrimalItemAmmo_ArrowStone`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>弩箭（`PrimalItemAmmo_BallistaArrow`） |
| Ammo - Blueprint Only | 0.2 | 1–1 | 0–1 | 1 | 石箭（`PrimalItemAmmo_ArrowStone`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>弩箭（`PrimalItemAmmo_BallistaArrow`） |

### 2. Structure pool: Stone

- SetWeight：0.05
- 精确池：`/Game/PrimalEarth/CoreBlueprints/ItemLootSets/LootItemSet_SupplyDrop_Structures__ASA_Stone.LootItemSet_SupplyDrop_Structures__ASA_Stone_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Serialized structure candidates | 1 | 1–1 | 0–1 | 0 | 石制引水管（`PrimalItemStructure_StonePipeIntake`）<br>石制天花板&天窗框（`PrimalItemStructure_StoneCeiling`）<br>石制加固大型天窗门（`PrimalItemStructure_StoneCeilingDoorGiant`）<br>加固石门&窗户（`PrimalItemStructure_StoneDoor`）<br>石制栅栏地基 & 支架（`PrimalItemStructure_StoneFenceFoundation`）<br>石制地基（`PrimalItemStructure_StoneFloor`）<br>石制加固门（`PrimalItemStructure_StoneGate`）<br>石制加固巨兽恐龙门（`PrimalItemStructure_StoneGateLarge`）<br>石制恐龙门框（`PrimalItemStructure_StoneGateframe`）<br>石制巨兽门框（`PrimalItemStructure_StoneGateframe_Large`）<br>石制柱子（`PrimalItemStructure_StonePillar`）<br>石制小墙&栏杆（`PrimalItemStructure_StoneRailing`）<br>石制墙,门框&窗框（`PrimalItemStructure_StoneWall`）<br>石制小天花板 & 三角天花板（`PrimalItemStructure_TriCeiling_Stone`）<br>石制三角地基 & 小型地基（`PrimalItemStructure_TriFoundation_Stone`）<br>石制屋顶, 斜坡 & 楼梯（`PrimalItemStructure_Ramp_Stone`）<br>石制三角屋顶 & 直角三角屋顶（`PrimalItemStructure_TriRoof_Stone`） |

### 3. Lootset level 35

- SetWeight：1
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level35_Gen1.LootItemSet_SupplyDrop_Level35_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Structures - Useable | 0.3 | 1–2 | 0–1 | 0 | 金属蓄水池（`PrimalItemStructure_WaterTankMetal`）<br>大型捕兽夹（`PrimalItemStructure_BearTrap_Large`）<br>简易爆炸装置（`PrimalItem_WeaponTripwireC4`）<br>金属尖刺墙（`PrimalItemStructure_MetalSpikeWall`）<br>金属广告板（`PrimalItemStructure_MetalSign_Large`）<br>金属标识板（`PrimalItemStructure_MetalSign`）<br>双层床（`PrimalItemStructure_Bed_Modern`） |
| Armor, Tools, and Weapons with Quality | 0.6 | 1–1 | 1.8–3.84 | 0.1 | 鱼叉枪（`PrimalItem_WeaponHarpoon`）<br>霰弹枪（`PrimalItem_WeaponShotgun`）<br>长管步枪（`PrimalItem_WeaponOneShotRifle`）<br>金属斧子（`PrimalItem_WeaponMetalHatchet`）<br>金属镐（`PrimalItem_WeaponMetalPick`）<br>简易手枪（`PrimalItem_WeaponGun`）<br>吉利靴（`PrimalItemArmor_GhillieBoots`）<br>吉利手套（`PrimalItemArmor_GhillieGloves`）<br>吉利面具（`PrimalItemArmor_GhillieHelmet`）<br>吉利护腿（`PrimalItemArmor_GhilliePants`）<br>吉利胸甲（`PrimalItemArmor_GhillieShirt`）<br>甲壳靴（`PrimalItemArmor_ChitinBoots`）<br>甲壳手套（`PrimalItemArmor_ChitinGloves`）<br>甲壳头盔（`PrimalItemArmor_ChitinHelmet`）<br>甲壳腿（`PrimalItemArmor_ChitinPants`）<br>甲壳胸甲（`PrimalItemArmor_ChitinShirt`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`） |
| Saddles with Quality | 0.5 | 1–1 | 1.8–3.84 | 0.2 | 无齿翼龙鞍（`PrimalItemArmor_PteroSaddle`）<br>猪鳄鞍（`PrimalItemArmor_KaprosuchusSaddle`）<br>魔鬼蛙鞍（`PrimalItemArmor_ToadSaddle`）<br>巨犀鞍（`PrimalItemArmor_Paracer_Saddle`）<br>砂犷兽鞍（`PrimalItemArmor_ChalicoSaddle`）<br>邓氏鱼鞍（`PrimalItemArmor_DunkleosteusSaddle`）<br>恐熊鞍（`PrimalItemArmor_DireBearSaddle`）<br>大地懒鞍（`PrimalItemArmor_MegatheriumSaddle`）<br>古马陆鞍（`PrimalItemArmor_ArthroSaddle`）<br>牛龙鞍（`PrimalItemArmor_CarnoSaddle`）<br>伪齿鸟鞍（`PrimalItemArmor_PelaSaddle`）<br>潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`） |
| Armor, Tools, and Weapons with no Quality | 0.8 | 1–1 | 0–1 | 0 | 鱼叉枪（`PrimalItem_WeaponHarpoon`）<br>霰弹枪（`PrimalItem_WeaponShotgun`）<br>长管步枪（`PrimalItem_WeaponOneShotRifle`）<br>金属斧子（`PrimalItem_WeaponMetalHatchet`）<br>金属镐（`PrimalItem_WeaponMetalPick`）<br>简易手枪（`PrimalItem_WeaponGun`）<br>吉利靴（`PrimalItemArmor_GhillieBoots`）<br>吉利手套（`PrimalItemArmor_GhillieGloves`）<br>吉利面具（`PrimalItemArmor_GhillieHelmet`）<br>吉利护腿（`PrimalItemArmor_GhilliePants`）<br>吉利胸甲（`PrimalItemArmor_GhillieShirt`）<br>毛皮靴（`PrimalItemArmor_FurBoots`）<br>毛皮手套（`PrimalItemArmor_FurGloves`）<br>毛皮帽（`PrimalItemArmor_FurHelmet`）<br>毛皮护腿（`PrimalItemArmor_FurPants`）<br>毛皮胸甲（`PrimalItemArmor_FurShirt`）<br>毒气手雷（`PrimalItem_PoisonGrenade`）<br>手雷（`PrimalItem_WeaponGrenade`）<br>简易爆炸装置（`PrimalItem_WeaponTripwireC4`）<br>喷枪（`PrimalItem_WeaponSprayPaint`）<br>手铐（`PrimalItem_WeaponHandcuffs`） |
| Consumables - High Quantity | 1 | 1–5 | 0–1 | 0 | 优质熟鱼肉（`PrimalItemConsumable_CookedPrimeMeat_Fish`）<br>优质熟肉干（`PrimalItemConsumable_CookedPrimeMeat_Jerky`）<br>优质熟肉（`PrimalItemConsumable_CookedPrimeMeat`）<br>熟羊肉（`PrimalItemConsumable_CookedLambChop`）<br>轻型解药（`PrimalItemConsumable_CureLow`）<br>驱虫剂（`PrimalItemConsumable_BugRepellant`）<br>扎啤（`PrimalItemConsumable_BeerJar`）<br>焦红辣椒（`PrimalItemConsumable_Soup_FocalChili`）<br>菲拉咖喱（`PrimalItemConsumable_Soup_FriaCurry`）<br>卡琳汤（`PrimalItemConsumable_Soup_CalienSoup`） |
| Resources | 1 | 10–80 | 0–1 | 0 | 金属（`PrimalItemResource_Metal`）<br>金属锭（`PrimalItemResource_MetalIngot`）<br>黑曜石（`PrimalItemResource_Obsidian`）<br>树脂（`PrimalItemResource_Sap`）<br>水蛭血（`PrimalItemResource_LeechBlood`）<br>火药（`PrimalItemResource_Gunpowder`） |
| Ammo | 0.8 | 5–20 | 1.5–8 | 0 | 简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>简易步枪子弹（`PrimalItemAmmo_SimpleRifleBullet`）<br>弩箭（`PrimalItemAmmo_BallistaArrow`） |
| Ammo - Blueprint Only | 0.2 | 1–1 | 0–1 | 1 | 简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>简易步枪子弹（`PrimalItemAmmo_SimpleRifleBullet`）<br>弩箭（`PrimalItemAmmo_BallistaArrow`） |

### 4. Structure pool: Greenhouse

- SetWeight：0.1
- 精确池：`/Game/PrimalEarth/CoreBlueprints/ItemLootSets/LootItemSet_SupplyDrop_Structures__ASA_Greenhouse.LootItemSet_SupplyDrop_Structures__ASA_Greenhouse_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Serialized structure candidates | 1 | 1–1 | 0–1 | 0 | 石制引水管（`PrimalItemStructure_StonePipeIntake`）<br>石制天花板&天窗框（`PrimalItemStructure_StoneCeiling`）<br>石制加固大型天窗门（`PrimalItemStructure_StoneCeilingDoorGiant`）<br>加固石门&窗户（`PrimalItemStructure_StoneDoor`）<br>石制栅栏地基 & 支架（`PrimalItemStructure_StoneFenceFoundation`）<br>石制地基（`PrimalItemStructure_StoneFloor`）<br>石制加固门（`PrimalItemStructure_StoneGate`）<br>石制加固巨兽恐龙门（`PrimalItemStructure_StoneGateLarge`）<br>石制恐龙门框（`PrimalItemStructure_StoneGateframe`）<br>石制巨兽门框（`PrimalItemStructure_StoneGateframe_Large`）<br>石制柱子（`PrimalItemStructure_StonePillar`）<br>石制小墙&栏杆（`PrimalItemStructure_StoneRailing`）<br>石制墙,门框&窗框（`PrimalItemStructure_StoneWall`）<br>石制小天花板 & 三角天花板（`PrimalItemStructure_TriCeiling_Stone`）<br>石制三角地基 & 小型地基（`PrimalItemStructure_TriFoundation_Stone`）<br>石制屋顶, 斜坡 & 楼梯（`PrimalItemStructure_Ramp_Stone`）<br>石制三角屋顶 & 直角三角屋顶（`PrimalItemStructure_TriRoof_Stone`） |

### 5. Lootset level 45

- SetWeight：0.025
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level45_Gen1_QualityOnly.LootItemSet_SupplyDrop_Level45_Gen1_QualityOnly_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Armor, Tools, and Weapons with Quality | 1 | 1–1 | 1.8–3.84 | 0.3 | 电击棒（`PrimalItem_WeaponProd`）<br>制式手枪（`PrimalItem_WeaponMachinedPistol`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`）<br>探照灯枪（`PrimalItem_WeaponRadioactiveLanternCharge`）<br>防护靴（`PrimalItemArmor_HazardSuitBoots`）<br>防护手套（`PrimalItemArmor_HazardSuitGloves`）<br>防护头盔（`PrimalItemArmor_HazardSuitHelmet`）<br>防护裤（`PrimalItemArmor_HazardSuitPants`）<br>防护上衣（`PrimalItemArmor_HazardSuitShirt`） |
| Saddles with Quality | 1 | 1–1 | 1.8–3.84 | 0.3 | 巨齿鲨鞍（`PrimalItemArmor_MegalodonSaddle`）<br>重爪龙鞍（`PrimalItemArmor_BaryonyxSaddle`）<br>巨犀平台鞍（`PrimalItemArmor_ParacerSaddle_Platform`）<br>袋狮鞍（`PrimalItemArmor_ThylacoSaddle`）<br>披毛犀鞍（`PrimalItemArmor_RhinoSaddle`）<br>古神翼龙鞍（`PrimalItemArmor_TapejaraSaddle`）<br>斑龙鞍（`PrimalItemArmor_MegalosaurusSaddle`）<br>凶齿豨鞍（`PrimalItemArmor_DaeodonSaddle`）<br>龙王鲸鞍（`PrimalItemArmor_BasiloSaddle`）<br>巨河狸鞍（`PrimalItemArmor_BeaverSaddle`）<br>阿根廷巨鹰鞍（`PrimalItemArmor_ArgentavisSaddle`）<br>异特龙鞍（`PrimalItemArmor_AlloSaddle`）<br>蛇颈龙鞍（`PrimalItemArmor_PlesiaSaddle`）<br>古巨蜥鞍（`PrimalItemArmor_MegalaniaSaddle`）<br>雷龙鞍（`PrimalItemArmor_SauroSaddle`）<br>角鼻龙鞍（`PrimalItemArmor_CeratosaurusSaddle_ASA`）<br>剑射鱼鞍（`PrimalItemArmor_XiphSaddle_ASA`）<br>恐鳄鞍（`PrimalItemArmor_Deinosuchus_Saddle_ASA`）<br>古巨龟鞍（`PrimalItem_Armor_Archelon_Saddle_ASA`）<br>旋齿鲨鞍具（`PrimalItemArmor_Helicoprion`）<br>潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`）<br>岛龟平台鞍（`PrimalItemArmor_GiantTurtleSaddle`） |

### 6. Lootset level 45

- SetWeight：0.0025；同一池第 2 次出现
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level45_Gen1_QualityOnly.LootItemSet_SupplyDrop_Level45_Gen1_QualityOnly_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Armor, Tools, and Weapons with Quality | 1 | 1–1 | 1.8–3.84 | 0.3 | 电击棒（`PrimalItem_WeaponProd`）<br>制式手枪（`PrimalItem_WeaponMachinedPistol`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`）<br>探照灯枪（`PrimalItem_WeaponRadioactiveLanternCharge`）<br>防护靴（`PrimalItemArmor_HazardSuitBoots`）<br>防护手套（`PrimalItemArmor_HazardSuitGloves`）<br>防护头盔（`PrimalItemArmor_HazardSuitHelmet`）<br>防护裤（`PrimalItemArmor_HazardSuitPants`）<br>防护上衣（`PrimalItemArmor_HazardSuitShirt`） |
| Saddles with Quality | 1 | 1–1 | 1.8–3.84 | 0.3 | 巨齿鲨鞍（`PrimalItemArmor_MegalodonSaddle`）<br>重爪龙鞍（`PrimalItemArmor_BaryonyxSaddle`）<br>巨犀平台鞍（`PrimalItemArmor_ParacerSaddle_Platform`）<br>袋狮鞍（`PrimalItemArmor_ThylacoSaddle`）<br>披毛犀鞍（`PrimalItemArmor_RhinoSaddle`）<br>古神翼龙鞍（`PrimalItemArmor_TapejaraSaddle`）<br>斑龙鞍（`PrimalItemArmor_MegalosaurusSaddle`）<br>凶齿豨鞍（`PrimalItemArmor_DaeodonSaddle`）<br>龙王鲸鞍（`PrimalItemArmor_BasiloSaddle`）<br>巨河狸鞍（`PrimalItemArmor_BeaverSaddle`）<br>阿根廷巨鹰鞍（`PrimalItemArmor_ArgentavisSaddle`）<br>异特龙鞍（`PrimalItemArmor_AlloSaddle`）<br>蛇颈龙鞍（`PrimalItemArmor_PlesiaSaddle`）<br>古巨蜥鞍（`PrimalItemArmor_MegalaniaSaddle`）<br>雷龙鞍（`PrimalItemArmor_SauroSaddle`）<br>角鼻龙鞍（`PrimalItemArmor_CeratosaurusSaddle_ASA`）<br>剑射鱼鞍（`PrimalItemArmor_XiphSaddle_ASA`）<br>恐鳄鞍（`PrimalItemArmor_Deinosuchus_Saddle_ASA`）<br>古巨龟鞍（`PrimalItem_Armor_Archelon_Saddle_ASA`）<br>旋齿鲨鞍具（`PrimalItemArmor_Helicoprion`）<br>潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`）<br>岛龟平台鞍（`PrimalItemArmor_GiantTurtleSaddle`） |

## Ramshackle

- 开箱等级：15
- 宝箱品质倍率：2.0–4.0

### 1. Lootset level 25 quality only

- SetWeight：0.3
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level25_Gen1_QualityOnly.LootItemSet_SupplyDrop_Level25_Gen1_QualityOnly_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Armor, Tools, and Weapons with Quality | 1 | 1–1 | 1.8–3.84 | 0.3 | 十字弩（`PrimalItem_WeaponCrossbow`）<br>金属镰刀（`PrimalItem_WeaponSickle`）<br>金属矛（`PrimalItem_WeaponPike`）<br>手铐（`PrimalItem_WeaponHandcuffs`）<br>毛皮靴（`PrimalItemArmor_FurBoots`）<br>毛皮手套（`PrimalItemArmor_FurGloves`）<br>毛皮帽（`PrimalItemArmor_FurHelmet`）<br>毛皮护腿（`PrimalItemArmor_FurPants`）<br>毛皮胸甲（`PrimalItemArmor_FurShirt`） |
| Saddles with Quality | 1 | 1–1 | 1.8–3.84 | 0.3 | 甲龙鞍（`PrimalItemArmor_AnkyloSaddle`）<br>剑齿虎鞍（`PrimalItemArmor_SaberSaddle`）<br>蜘蛛鞍（`PrimalItemArmor_SpiderSaddle`）<br>禽龙鞍（`PrimalItemArmor_IguanodonSaddle`）<br>大角鹿鞍（`PrimalItemArmor_StagSaddle`）<br>猛犸象鞍（`PrimalItemArmor_MammothSaddle`）<br>梁龙鞍（`PrimalItemArmor_DiplodocusSaddle`）<br>骇鸟鞍（`PrimalItemArmor_TerrorBirdSaddle`）<br>星尾兽鞍（`PrimalItemArmor_DoedSaddle`）<br>帝鳄鞍（`PrimalItemArmor_SarcoSaddle`）<br>潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`） |

### 2. Structure pool: Greenhouse

- SetWeight：0.05
- 精确池：`/Game/PrimalEarth/CoreBlueprints/ItemLootSets/LootItemSet_SupplyDrop_Structures__ASA_Greenhouse.LootItemSet_SupplyDrop_Structures__ASA_Greenhouse_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Serialized structure candidates | 1 | 1–1 | 0–1 | 0 | 石制引水管（`PrimalItemStructure_StonePipeIntake`）<br>石制天花板&天窗框（`PrimalItemStructure_StoneCeiling`）<br>石制加固大型天窗门（`PrimalItemStructure_StoneCeilingDoorGiant`）<br>加固石门&窗户（`PrimalItemStructure_StoneDoor`）<br>石制栅栏地基 & 支架（`PrimalItemStructure_StoneFenceFoundation`）<br>石制地基（`PrimalItemStructure_StoneFloor`）<br>石制加固门（`PrimalItemStructure_StoneGate`）<br>石制加固巨兽恐龙门（`PrimalItemStructure_StoneGateLarge`）<br>石制恐龙门框（`PrimalItemStructure_StoneGateframe`）<br>石制巨兽门框（`PrimalItemStructure_StoneGateframe_Large`）<br>石制柱子（`PrimalItemStructure_StonePillar`）<br>石制小墙&栏杆（`PrimalItemStructure_StoneRailing`）<br>石制墙,门框&窗框（`PrimalItemStructure_StoneWall`）<br>石制小天花板 & 三角天花板（`PrimalItemStructure_TriCeiling_Stone`）<br>石制三角地基 & 小型地基（`PrimalItemStructure_TriFoundation_Stone`）<br>石制屋顶, 斜坡 & 楼梯（`PrimalItemStructure_Ramp_Stone`）<br>石制三角屋顶 & 直角三角屋顶（`PrimalItemStructure_TriRoof_Stone`） |

### 3. Lootset level 45

- SetWeight：1
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level45_Gen1.LootItemSet_SupplyDrop_Level45_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Structures - Useable | 0.2 | 1–2 | 0–1 | 0 | 大型电梯平台（`PrimalItemStructure_ElevatorPlatformLarge`） (item weight 0.4)<br>小型电梯平台（`PrimalItemStructure_ElevatorPlatformSmall`） (item weight 1)<br>中型电梯平台（`PrimalItemStructure_ElevatorPlatformMedium`） (item weight 8)<br>电梯轨道（`PrimalItemStructure_ElevatorTrackBase`） (item weight 1)<br>保险柜（`PrimalItemStructure_StorageBox_Huge`） (item weight 0.2)<br>水雷（`PrimalItemStructure_SeaMine`） (item weight 0.2)<br>遥控板（`PrimalItemStructure_Keypad`） (item weight 1)<br>电灯（`PrimalItemStructure_Lamppost`） (item weight 1)<br>全向电灯（`PrimalItemStructure_LamppostOmni`） (item weight 1)<br>发电机（`PrimalItemStructure_PowerGenerator`） (item weight 0.4)<br>空调（`PrimalItemStructure_AirConditioner`） (item weight 1) |
| Armor, Tools, and Weapons with Quality | 0.5 | 1–1 | 1.8–3.84 | 0.1 | 电击棒（`PrimalItem_WeaponProd`）<br>制式手枪（`PrimalItem_WeaponMachinedPistol`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`）<br>探照灯枪（`PrimalItem_WeaponRadioactiveLanternCharge`）<br>防护靴（`PrimalItemArmor_HazardSuitBoots`）<br>防护手套（`PrimalItemArmor_HazardSuitGloves`）<br>防护头盔（`PrimalItemArmor_HazardSuitHelmet`）<br>防护裤（`PrimalItemArmor_HazardSuitPants`）<br>防护上衣（`PrimalItemArmor_HazardSuitShirt`） |
| Saddles with Quality | 0.45 | 1–1 | 1.8–3.84 | 0.2 | 巨齿鲨鞍（`PrimalItemArmor_MegalodonSaddle`）<br>重爪龙鞍（`PrimalItemArmor_BaryonyxSaddle`）<br>巨犀平台鞍（`PrimalItemArmor_ParacerSaddle_Platform`）<br>袋狮鞍（`PrimalItemArmor_ThylacoSaddle`）<br>披毛犀鞍（`PrimalItemArmor_RhinoSaddle`）<br>古神翼龙鞍（`PrimalItemArmor_TapejaraSaddle`）<br>斑龙鞍（`PrimalItemArmor_MegalosaurusSaddle`）<br>凶齿豨鞍（`PrimalItemArmor_DaeodonSaddle`）<br>龙王鲸鞍（`PrimalItemArmor_BasiloSaddle`）<br>巨河狸鞍（`PrimalItemArmor_BeaverSaddle`）<br>阿根廷巨鹰鞍（`PrimalItemArmor_ArgentavisSaddle`）<br>异特龙鞍（`PrimalItemArmor_AlloSaddle`）<br>蛇颈龙鞍（`PrimalItemArmor_PlesiaSaddle`）<br>古巨蜥鞍（`PrimalItemArmor_MegalaniaSaddle`）<br>雷龙鞍（`PrimalItemArmor_SauroSaddle`）<br>剑射鱼鞍（`PrimalItemArmor_XiphSaddle_ASA`）<br>角鼻龙鞍（`PrimalItemArmor_CeratosaurusSaddle_ASA`）<br>恐鳄鞍（`PrimalItemArmor_Deinosuchus_Saddle_ASA`）<br>古巨龟鞍（`PrimalItem_Armor_Archelon_Saddle_ASA`）<br>旋齿鲨鞍具（`PrimalItemArmor_Helicoprion`）<br>潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`）<br>岛龟平台鞍（`PrimalItemArmor_GiantTurtleSaddle`） |
| Armor, Tools, and Weapons with no Quality | 0.8 | 1–1 | 0–1 | 0 | 电击棒（`PrimalItem_WeaponProd`）<br>制式手枪（`PrimalItem_WeaponMachinedPistol`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>C4遥控起爆器（`PrimalItem_WeaponC4`）<br>消音器附件（`PrimalItemWeaponAttachment_Silencer`）<br>瞄准镜附件（`PrimalItemWeaponAttachment_Scope`）<br>激光附件（`PrimalItemWeaponAttachment_Laser`）<br>全息瞄准镜（`PrimalItemWeaponAttachment_HoloScope`）<br>手电筒附件（`PrimalItemWeaponAttachment_Flashlight`）<br>军用水壶（`PrimalItemConsumable_CanteenRefill`） |
| Consumables - High Quantity | 1 | 1–3 | 0–1 | 0 | 生羊肉（`PrimalItemConsumable_RawMutton`）<br>拉撒路杂烩（`PrimalItemConsumable_Soup_LazarusChowder`）<br>暗影牛排（`PrimalItemConsumable_Soup_ShadowSteak`）<br>战斗鞑靼牛排（`PrimalItemConsumable_Soup_BattleTartare`） |
| Resources | 1 | 10–50 | 0–1 | 0 | 汽油（`PrimalItemResource_Gasoline`）<br>有机聚合物（`PrimalItemResource_Polymer_Organic`）<br>菊石黏液（`PrimalItemResource_AmmoniteBlood`）<br>鮟鱇鱼油（`PrimalItemResource_AnglerGel`）<br>含硅珍珠（`PrimalItemResource_Silicon`） |
| Ammo | 0.8 | 10–100 | 0–1 | 0 | 高级子弹（`PrimalItemAmmo_AdvancedBullet`） |
| Ammo Low Quantity | 0.15 | 1–5 | 0–1 | 0 | C4炸药（`PrimalItemC4Ammo`） |
| Ammo - Blueprint Only | 0.2 | 1–1 | 0–1 | 1 | 高级子弹（`PrimalItemAmmo_AdvancedBullet`）<br>C4炸药（`PrimalItemC4Ammo`） |

### 4. Structure pool: Metal

- SetWeight：0.1
- 精确池：`/Game/PrimalEarth/CoreBlueprints/ItemLootSets/LootItemSet_SupplyDrop_Structures__ASA_Metal.LootItemSet_SupplyDrop_Structures__ASA_Metal_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Serialized structure candidates | 1 | 1–1 | 0–1 | 0 | 金属天花板&天窗框（`PrimalItemStructure_MetalCeiling`）<br>金属门（`PrimalItemStructure_MetalDoor`）<br>金属栅栏地基 & 支架（`PrimalItemStructure_MetalFenceFoundation`）<br>金属地基（`PrimalItemStructure_MetalFloor`）<br>金属恐龙门（`PrimalItemStructure_MetalGate`）<br>金属巨兽恐龙门（`PrimalItemStructure_MetalGate_Large`）<br>金属恐龙门框（`PrimalItemStructure_MetalGateframe`）<br>金属巨兽恐龙门框（`PrimalItemStructure_MetalGateframe_Large`）<br>金属梯子（`PrimalItemStructure_MetalLadder`）<br>金属柱子（`PrimalItemStructure_MetalPillar`）<br>金属墙,门框&窗框（`PrimalItemStructure_MetalWall`）<br>金属引水口（`PrimalItemStructure_MetalPipeIntake`）<br>金属小天花板 & 三角天花板（`PrimalItemStructure_TriCeiling_Metal`）<br>金属三角地基（`PrimalItemStructure_TriFoundation_Metal`）<br>金属屋顶, 斜坡 & 楼梯（`PrimalItemStructure_Ramp_Metal`）<br>金属三角屋顶 & 直角三角屋顶（`PrimalItemStructure_TriRoof_Metal`） |

### 5. Lootset level 45

- SetWeight：0.025
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level45_Gen1_QualityOnly.LootItemSet_SupplyDrop_Level45_Gen1_QualityOnly_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Armor, Tools, and Weapons with Quality | 1 | 1–1 | 1.8–3.84 | 0.3 | 电击棒（`PrimalItem_WeaponProd`）<br>制式手枪（`PrimalItem_WeaponMachinedPistol`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`）<br>探照灯枪（`PrimalItem_WeaponRadioactiveLanternCharge`）<br>防护靴（`PrimalItemArmor_HazardSuitBoots`）<br>防护手套（`PrimalItemArmor_HazardSuitGloves`）<br>防护头盔（`PrimalItemArmor_HazardSuitHelmet`）<br>防护裤（`PrimalItemArmor_HazardSuitPants`）<br>防护上衣（`PrimalItemArmor_HazardSuitShirt`） |
| Saddles with Quality | 1 | 1–1 | 1.8–3.84 | 0.3 | 巨齿鲨鞍（`PrimalItemArmor_MegalodonSaddle`）<br>重爪龙鞍（`PrimalItemArmor_BaryonyxSaddle`）<br>巨犀平台鞍（`PrimalItemArmor_ParacerSaddle_Platform`）<br>袋狮鞍（`PrimalItemArmor_ThylacoSaddle`）<br>披毛犀鞍（`PrimalItemArmor_RhinoSaddle`）<br>古神翼龙鞍（`PrimalItemArmor_TapejaraSaddle`）<br>斑龙鞍（`PrimalItemArmor_MegalosaurusSaddle`）<br>凶齿豨鞍（`PrimalItemArmor_DaeodonSaddle`）<br>龙王鲸鞍（`PrimalItemArmor_BasiloSaddle`）<br>巨河狸鞍（`PrimalItemArmor_BeaverSaddle`）<br>阿根廷巨鹰鞍（`PrimalItemArmor_ArgentavisSaddle`）<br>异特龙鞍（`PrimalItemArmor_AlloSaddle`）<br>蛇颈龙鞍（`PrimalItemArmor_PlesiaSaddle`）<br>古巨蜥鞍（`PrimalItemArmor_MegalaniaSaddle`）<br>雷龙鞍（`PrimalItemArmor_SauroSaddle`）<br>角鼻龙鞍（`PrimalItemArmor_CeratosaurusSaddle_ASA`）<br>剑射鱼鞍（`PrimalItemArmor_XiphSaddle_ASA`）<br>恐鳄鞍（`PrimalItemArmor_Deinosuchus_Saddle_ASA`）<br>古巨龟鞍（`PrimalItem_Armor_Archelon_Saddle_ASA`）<br>旋齿鲨鞍具（`PrimalItemArmor_Helicoprion`）<br>潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`）<br>岛龟平台鞍（`PrimalItemArmor_GiantTurtleSaddle`） |

### 6. Lootset level 60 quality only

- SetWeight：0.0045
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level60_Gen1_QualityOnly.LootItemSet_SupplyDrop_Level60_Gen1_QualityOnly_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Armor, Tools, and Weapons with Quality | 0.6 | 1–1 | 1.8–4.8 | 0.3 | 制式突击步枪（`PrimalItem_WeaponRifle`）<br>复合弓（`PrimalItem_WeaponCompoundBow`）<br>制式狙击步枪（`PrimalItem_WeaponMachinedSniper`）<br>泵动式霰弹枪（`PrimalItem_WeaponMachinedShotgun`）<br>防暴胸甲（`PrimalItemArmor_RiotShirt`）<br>防暴裤（`PrimalItemArmor_RiotPants`）<br>防暴帽（`PrimalItemArmor_RiotHelmet`）<br>防暴手套（`PrimalItemArmor_RiotGloves`）<br>防暴靴（`PrimalItemArmor_RiotBoots`）<br>防暴盾（`PrimalItemArmor_TransparentRiotShield`）<br>矿枪（`PrimalItem_WeaponMiningDrill`） |
| Saddles with Quality | 0.5 | 1–1 | 1.8–3.84 | 0.3 | 镰刀龙鞍（`PrimalItemArmor_TherizinosaurusSaddle`）<br>棘背龙鞍（`PrimalItemArmor_SpinoSaddle`）<br>霸王龙鞍（`PrimalItemArmor_RexSaddle`）<br>风神翼龙鞍（`PrimalItemArmor_QuetzSaddle`）<br>沧龙鞍（`PrimalItemArmor_MosaSaddle`）<br>羽暴龙鞍（`PrimalItemArmor_YutySaddle`）<br>雷龙平台鞍（`PrimalItemArmor_SauroSaddle_Platform`）<br>蛇颈龙平台鞍（`PrimalItemArmor_PlesiSaddle_Platform`）<br>托斯特巨鱿鞍（`PrimalItemArmor_TusoSaddle`）<br>沧龙平台鞍（`PrimalItemArmor_MosaSaddle_Platform`）<br>鲨齿龙鞍（`PrimalItemArmor_CarchaSaddle`）<br>南方巨兽龙鞍（`PrimalItemArmor_GigantSaddle`）<br>风神翼龙平台鞍（`PrimalItemArmor_QuetzSaddle_Platform`）<br>巨盗龙鞍（`PrimalItemArmor_GigantoraptorSaddle`）<br>恐象鞍具（`PrimalItemArmor_DeinotheriumSaddle_ASA`）<br>高棘龙鞍（`PrimalItemArmor_AcroSaddle`）<br>熔喉龙鞍（`PrimalItemArmor_CherufeSaddle`） |

## Apprentice

- 开箱等级：25
- 宝箱品质倍率：2.0–4.0

### 1. Lootset level 45

- SetWeight：0.3
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level45_Gen1.LootItemSet_SupplyDrop_Level45_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Structures - Useable | 0.2 | 1–2 | 0–1 | 0 | 大型电梯平台（`PrimalItemStructure_ElevatorPlatformLarge`） (item weight 0.4)<br>小型电梯平台（`PrimalItemStructure_ElevatorPlatformSmall`） (item weight 1)<br>中型电梯平台（`PrimalItemStructure_ElevatorPlatformMedium`） (item weight 8)<br>电梯轨道（`PrimalItemStructure_ElevatorTrackBase`） (item weight 1)<br>保险柜（`PrimalItemStructure_StorageBox_Huge`） (item weight 0.2)<br>水雷（`PrimalItemStructure_SeaMine`） (item weight 0.2)<br>遥控板（`PrimalItemStructure_Keypad`） (item weight 1)<br>电灯（`PrimalItemStructure_Lamppost`） (item weight 1)<br>全向电灯（`PrimalItemStructure_LamppostOmni`） (item weight 1)<br>发电机（`PrimalItemStructure_PowerGenerator`） (item weight 0.4)<br>空调（`PrimalItemStructure_AirConditioner`） (item weight 1) |
| Armor, Tools, and Weapons with Quality | 0.5 | 1–1 | 1.8–3.84 | 0.1 | 电击棒（`PrimalItem_WeaponProd`）<br>制式手枪（`PrimalItem_WeaponMachinedPistol`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`）<br>探照灯枪（`PrimalItem_WeaponRadioactiveLanternCharge`）<br>防护靴（`PrimalItemArmor_HazardSuitBoots`）<br>防护手套（`PrimalItemArmor_HazardSuitGloves`）<br>防护头盔（`PrimalItemArmor_HazardSuitHelmet`）<br>防护裤（`PrimalItemArmor_HazardSuitPants`）<br>防护上衣（`PrimalItemArmor_HazardSuitShirt`） |
| Saddles with Quality | 0.45 | 1–1 | 1.8–3.84 | 0.2 | 巨齿鲨鞍（`PrimalItemArmor_MegalodonSaddle`）<br>重爪龙鞍（`PrimalItemArmor_BaryonyxSaddle`）<br>巨犀平台鞍（`PrimalItemArmor_ParacerSaddle_Platform`）<br>袋狮鞍（`PrimalItemArmor_ThylacoSaddle`）<br>披毛犀鞍（`PrimalItemArmor_RhinoSaddle`）<br>古神翼龙鞍（`PrimalItemArmor_TapejaraSaddle`）<br>斑龙鞍（`PrimalItemArmor_MegalosaurusSaddle`）<br>凶齿豨鞍（`PrimalItemArmor_DaeodonSaddle`）<br>龙王鲸鞍（`PrimalItemArmor_BasiloSaddle`）<br>巨河狸鞍（`PrimalItemArmor_BeaverSaddle`）<br>阿根廷巨鹰鞍（`PrimalItemArmor_ArgentavisSaddle`）<br>异特龙鞍（`PrimalItemArmor_AlloSaddle`）<br>蛇颈龙鞍（`PrimalItemArmor_PlesiaSaddle`）<br>古巨蜥鞍（`PrimalItemArmor_MegalaniaSaddle`）<br>雷龙鞍（`PrimalItemArmor_SauroSaddle`）<br>剑射鱼鞍（`PrimalItemArmor_XiphSaddle_ASA`）<br>角鼻龙鞍（`PrimalItemArmor_CeratosaurusSaddle_ASA`）<br>恐鳄鞍（`PrimalItemArmor_Deinosuchus_Saddle_ASA`）<br>古巨龟鞍（`PrimalItem_Armor_Archelon_Saddle_ASA`）<br>旋齿鲨鞍具（`PrimalItemArmor_Helicoprion`）<br>潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`）<br>岛龟平台鞍（`PrimalItemArmor_GiantTurtleSaddle`） |
| Armor, Tools, and Weapons with no Quality | 0.8 | 1–1 | 0–1 | 0 | 电击棒（`PrimalItem_WeaponProd`）<br>制式手枪（`PrimalItem_WeaponMachinedPistol`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>C4遥控起爆器（`PrimalItem_WeaponC4`）<br>消音器附件（`PrimalItemWeaponAttachment_Silencer`）<br>瞄准镜附件（`PrimalItemWeaponAttachment_Scope`）<br>激光附件（`PrimalItemWeaponAttachment_Laser`）<br>全息瞄准镜（`PrimalItemWeaponAttachment_HoloScope`）<br>手电筒附件（`PrimalItemWeaponAttachment_Flashlight`）<br>军用水壶（`PrimalItemConsumable_CanteenRefill`） |
| Consumables - High Quantity | 1 | 1–3 | 0–1 | 0 | 生羊肉（`PrimalItemConsumable_RawMutton`）<br>拉撒路杂烩（`PrimalItemConsumable_Soup_LazarusChowder`）<br>暗影牛排（`PrimalItemConsumable_Soup_ShadowSteak`）<br>战斗鞑靼牛排（`PrimalItemConsumable_Soup_BattleTartare`） |
| Resources | 1 | 10–50 | 0–1 | 0 | 汽油（`PrimalItemResource_Gasoline`）<br>有机聚合物（`PrimalItemResource_Polymer_Organic`）<br>菊石黏液（`PrimalItemResource_AmmoniteBlood`）<br>鮟鱇鱼油（`PrimalItemResource_AnglerGel`）<br>含硅珍珠（`PrimalItemResource_Silicon`） |
| Ammo | 0.8 | 10–100 | 0–1 | 0 | 高级子弹（`PrimalItemAmmo_AdvancedBullet`） |
| Ammo Low Quantity | 0.15 | 1–5 | 0–1 | 0 | C4炸药（`PrimalItemC4Ammo`） |
| Ammo - Blueprint Only | 0.2 | 1–1 | 0–1 | 1 | 高级子弹（`PrimalItemAmmo_AdvancedBullet`）<br>C4炸药（`PrimalItemC4Ammo`） |

### 2. Structure pool: Metal

- SetWeight：0.05
- 精确池：`/Game/PrimalEarth/CoreBlueprints/ItemLootSets/LootItemSet_SupplyDrop_Structures__ASA_Metal.LootItemSet_SupplyDrop_Structures__ASA_Metal_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Serialized structure candidates | 1 | 1–1 | 0–1 | 0 | 金属天花板&天窗框（`PrimalItemStructure_MetalCeiling`）<br>金属门（`PrimalItemStructure_MetalDoor`）<br>金属栅栏地基 & 支架（`PrimalItemStructure_MetalFenceFoundation`）<br>金属地基（`PrimalItemStructure_MetalFloor`）<br>金属恐龙门（`PrimalItemStructure_MetalGate`）<br>金属巨兽恐龙门（`PrimalItemStructure_MetalGate_Large`）<br>金属恐龙门框（`PrimalItemStructure_MetalGateframe`）<br>金属巨兽恐龙门框（`PrimalItemStructure_MetalGateframe_Large`）<br>金属梯子（`PrimalItemStructure_MetalLadder`）<br>金属柱子（`PrimalItemStructure_MetalPillar`）<br>金属墙,门框&窗框（`PrimalItemStructure_MetalWall`）<br>金属引水口（`PrimalItemStructure_MetalPipeIntake`）<br>金属小天花板 & 三角天花板（`PrimalItemStructure_TriCeiling_Metal`）<br>金属三角地基（`PrimalItemStructure_TriFoundation_Metal`）<br>金属屋顶, 斜坡 & 楼梯（`PrimalItemStructure_Ramp_Metal`）<br>金属三角屋顶 & 直角三角屋顶（`PrimalItemStructure_TriRoof_Metal`） |

### 3. Lootset level 60

- SetWeight：1
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level60_Gen1.LootItemSet_SupplyDrop_Level60_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Structures - Useable | 0.05 | 1–2 | 0–1 | 0 | 工业研磨机（`PrimalItemStructure_Grinder`）<br>工业烤箱（`PrimalItemStructure_Grill`）<br>工业熔炉（`PrimalItemStructure_IndustrialForge`）<br>工业大锅（`PrimalItemStructure_IndustrialCookingPot`）<br>金属树屋平台（`PrimalItemStructure_TreePlatform_Metal`）<br>机床（`PrimalItemStructure_Fabricator`）<br>火箭炮台（`PrimalItemStructure_TurretRocket`）<br>重型自动炮台（`PrimalItemStructure_HeavyTurret`）<br>机枪炮台（`PrimalItemStructure_TurretMinigun`）<br>自动炮台（`PrimalItemStructure_Turret`）<br>化学实验桌（`PrimalItemStructure_ChemBench`） |
| Armor, Tools, and Weapons with Quality | 0.5 | 1–1 | 1.8–3.84 | 0.1 | 制式突击步枪（`PrimalItem_WeaponRifle`）<br>复合弓（`PrimalItem_WeaponCompoundBow`）<br>制式狙击步枪（`PrimalItem_WeaponMachinedSniper`）<br>泵动式霰弹枪（`PrimalItem_WeaponMachinedShotgun`）<br>防暴胸甲（`PrimalItemArmor_RiotShirt`）<br>防暴裤（`PrimalItemArmor_RiotPants`）<br>防暴帽（`PrimalItemArmor_RiotHelmet`）<br>防暴手套（`PrimalItemArmor_RiotGloves`）<br>防暴靴（`PrimalItemArmor_RiotBoots`）<br>防暴盾（`PrimalItemArmor_TransparentRiotShield`）<br>矿枪（`PrimalItem_WeaponMiningDrill`） |
| Saddles with Quality | 0.45 | 1–1 | 1.8–3.84 | 0.2 | 镰刀龙鞍（`PrimalItemArmor_TherizinosaurusSaddle`）<br>棘背龙鞍（`PrimalItemArmor_SpinoSaddle`）<br>霸王龙鞍（`PrimalItemArmor_RexSaddle`）<br>风神翼龙鞍（`PrimalItemArmor_QuetzSaddle`）<br>沧龙鞍（`PrimalItemArmor_MosaSaddle`）<br>羽暴龙鞍（`PrimalItemArmor_YutySaddle`）<br>雷龙平台鞍（`PrimalItemArmor_SauroSaddle_Platform`）<br>蛇颈龙平台鞍（`PrimalItemArmor_PlesiSaddle_Platform`）<br>托斯特巨鱿鞍（`PrimalItemArmor_TusoSaddle`）<br>沧龙平台鞍（`PrimalItemArmor_MosaSaddle_Platform`）<br>鲨齿龙鞍（`PrimalItemArmor_CarchaSaddle`）<br>南方巨兽龙鞍（`PrimalItemArmor_GigantSaddle`）<br>风神翼龙平台鞍（`PrimalItemArmor_QuetzSaddle_Platform`）<br>恐象鞍具（`PrimalItemArmor_DeinotheriumSaddle_ASA`）<br>高棘龙鞍（`PrimalItemArmor_AcroSaddle`）<br>熔喉龙鞍（`PrimalItemArmor_CherufeSaddle`） |
| Armor, Tools, and Weapons with no Quality | 0.8 | 1–1 | 0–1 | 0 | 制式突击步枪（`PrimalItem_WeaponRifle`）<br>复合弓（`PrimalItem_WeaponCompoundBow`）<br>制式狙击步枪（`PrimalItem_WeaponMachinedSniper`）<br>泵动式霰弹枪（`PrimalItem_WeaponMachinedShotgun`）<br>防暴胸甲（`PrimalItemArmor_RiotShirt`）<br>防暴裤（`PrimalItemArmor_RiotPants`）<br>防暴帽（`PrimalItemArmor_RiotHelmet`）<br>防暴手套（`PrimalItemArmor_RiotGloves`）<br>防暴靴（`PrimalItemArmor_RiotBoots`）<br>防暴盾（`PrimalItemArmor_TransparentRiotShield`） |
| Consumables - High Quantity | 1 | 1–3 | 0–1 | 0 | 稀土肥料（`PrimalItemConsumableMiracleGro`）<br>可口蔬菜蛋糕（`PrimalItemConsumable_SweetVeggieCake`）<br>遗忘汤（`PrimalItemConsumableRespecSoup`）<br>启蒙之汤（`PrimalItemConsumable_TheHorn`）<br>披毛犀角（`PrimalItemResource_Horn`）<br>吸附剂（`PrimalItemResource_SubstrateAbsorbent`）<br>黑珍珠（`PrimalItemResource_BlackPearl`）<br>暗影牛排（`PrimalItemConsumable_Soup_ShadowSteak`）<br>拉撒路杂烩（`PrimalItemConsumable_Soup_LazarusChowder`）<br>菲拉咖喱（`PrimalItemConsumable_Soup_FriaCurry`）<br>焦红辣椒（`PrimalItemConsumable_Soup_FocalChili`）<br>耐力炖锅（`PrimalItemConsumable_Soup_EnduroStew`）<br>卡琳汤（`PrimalItemConsumable_Soup_CalienSoup`）<br>战斗鞑靼牛排（`PrimalItemConsumable_Soup_BattleTartare`）<br>药酒（`PrimalItemConsumable_HealSoup`） |
| Resources | 1 | 10–50 | 0–1 | 0 | 电路原件（`PrimalItemResource_Electronics`）<br>聚合物（`PrimalItemResource_Polymer`） |
| Ammo | 1 | 5–20 | 0–1 | 0 | 高级步枪子弹（`PrimalItemAmmo_AdvancedRifleBullet`）<br>金属箭（`PrimalItemAmmo_CompoundBowArrow`）<br>制式狙击步枪子弹（`PrimalItemAmmo_AdvancedSniperBullet`）<br>简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`） |
| Ammo Low Quantity | 0.2 | 1–5 | 0–1 | 0 | 火箭助推榴弹（`PrimalItemAmmo_Rocket`） |
| Ammo - Blueprint Only | 0.2 | 1–1 | 0–1 | 1 | 高级步枪子弹（`PrimalItemAmmo_AdvancedRifleBullet`）<br>金属箭（`PrimalItemAmmo_CompoundBowArrow`）<br>制式狙击步枪子弹（`PrimalItemAmmo_AdvancedSniperBullet`）<br>简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`）<br>火箭助推榴弹（`PrimalItemAmmo_Rocket`） |

### 4. Structure pool: Tek

- SetWeight：0.01
- 精确池：`/Game/PrimalEarth/CoreBlueprints/ItemLootSets/LootItemSet_SupplyDrop_Structures__ASA_Tek.LootItemSet_SupplyDrop_Structures__ASA_Tek_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Serialized structure candidates | 1 | 1–1 | 0–1 | 0 | 金属天花板&天窗框（`PrimalItemStructure_MetalCeiling`）<br>金属门（`PrimalItemStructure_MetalDoor`）<br>金属栅栏地基 & 支架（`PrimalItemStructure_MetalFenceFoundation`）<br>金属地基（`PrimalItemStructure_MetalFloor`）<br>金属恐龙门（`PrimalItemStructure_MetalGate`）<br>金属巨兽恐龙门（`PrimalItemStructure_MetalGate_Large`）<br>金属恐龙门框（`PrimalItemStructure_MetalGateframe`）<br>金属巨兽恐龙门框（`PrimalItemStructure_MetalGateframe_Large`）<br>金属梯子（`PrimalItemStructure_MetalLadder`）<br>金属柱子（`PrimalItemStructure_MetalPillar`）<br>金属墙,门框&窗框（`PrimalItemStructure_MetalWall`）<br>金属引水口（`PrimalItemStructure_MetalPipeIntake`）<br>金属小天花板 & 三角天花板（`PrimalItemStructure_TriCeiling_Metal`）<br>金属三角地基（`PrimalItemStructure_TriFoundation_Metal`）<br>金属屋顶, 斜坡 & 楼梯（`PrimalItemStructure_Ramp_Metal`）<br>金属三角屋顶 & 直角三角屋顶（`PrimalItemStructure_TriRoof_Metal`） |

### 5. Lootset level 60 quality only

- SetWeight：0.025
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_SupplyDrop_Level60_Gen1_QualityOnly.LootItemSet_SupplyDrop_Level60_Gen1_QualityOnly_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Armor, Tools, and Weapons with Quality | 0.6 | 1–1 | 1.8–4.8 | 0.3 | 制式突击步枪（`PrimalItem_WeaponRifle`）<br>复合弓（`PrimalItem_WeaponCompoundBow`）<br>制式狙击步枪（`PrimalItem_WeaponMachinedSniper`）<br>泵动式霰弹枪（`PrimalItem_WeaponMachinedShotgun`）<br>防暴胸甲（`PrimalItemArmor_RiotShirt`）<br>防暴裤（`PrimalItemArmor_RiotPants`）<br>防暴帽（`PrimalItemArmor_RiotHelmet`）<br>防暴手套（`PrimalItemArmor_RiotGloves`）<br>防暴靴（`PrimalItemArmor_RiotBoots`）<br>防暴盾（`PrimalItemArmor_TransparentRiotShield`）<br>矿枪（`PrimalItem_WeaponMiningDrill`） |
| Saddles with Quality | 0.5 | 1–1 | 1.8–3.84 | 0.3 | 镰刀龙鞍（`PrimalItemArmor_TherizinosaurusSaddle`）<br>棘背龙鞍（`PrimalItemArmor_SpinoSaddle`）<br>霸王龙鞍（`PrimalItemArmor_RexSaddle`）<br>风神翼龙鞍（`PrimalItemArmor_QuetzSaddle`）<br>沧龙鞍（`PrimalItemArmor_MosaSaddle`）<br>羽暴龙鞍（`PrimalItemArmor_YutySaddle`）<br>雷龙平台鞍（`PrimalItemArmor_SauroSaddle_Platform`）<br>蛇颈龙平台鞍（`PrimalItemArmor_PlesiSaddle_Platform`）<br>托斯特巨鱿鞍（`PrimalItemArmor_TusoSaddle`）<br>沧龙平台鞍（`PrimalItemArmor_MosaSaddle_Platform`）<br>鲨齿龙鞍（`PrimalItemArmor_CarchaSaddle`）<br>南方巨兽龙鞍（`PrimalItemArmor_GigantSaddle`）<br>风神翼龙平台鞍（`PrimalItemArmor_QuetzSaddle_Platform`）<br>巨盗龙鞍（`PrimalItemArmor_GigantoraptorSaddle`）<br>恐象鞍具（`PrimalItemArmor_DeinotheriumSaddle_ASA`）<br>高棘龙鞍（`PrimalItemArmor_AcroSaddle`）<br>熔喉龙鞍（`PrimalItemArmor_CherufeSaddle`） |

## Journeyman

- 开箱等级：35
- 宝箱品质倍率：2.0–4.0

### 1. T1 Armor

- SetWeight：0.4
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T1_Armor_Gen1.LootItemSet_CaveDrop_T1_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 粗布裤子（`PrimalItemArmor_ClothPants`）<br>粗布衣服（`PrimalItemArmor_ClothShirt`）<br>粗布帽子（`PrimalItemArmor_ClothHelmet`）<br>粗布手套（`PrimalItemArmor_ClothGloves`）<br>粗布鞋（`PrimalItemArmor_ClothBoots`）<br>兽皮靴（`PrimalItemArmor_HideBoots`）<br>兽皮手套（`PrimalItemArmor_HideGloves`）<br>兽皮帽（`PrimalItemArmor_HideHelmet`）<br>兽皮裤（`PrimalItemArmor_HidePants`）<br>兽皮上衣（`PrimalItemArmor_HideShirt`）<br>木制盾牌（`PrimalItemArmor_WoodShield`） |

### 2. T1 Weapons

- SetWeight：0.4
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T1_Weapons_Gen1.LootItemSet_CaveDrop_T1_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 石镐（`PrimalItem_WeaponStonePick`）<br>石制斧头（`PrimalItem_WeaponStoneHatchet`）<br>火把（`PrimalItem_WeaponTorch`）<br>木制球棒（`PrimalItem_WeaponStoneClub`）<br>弹弓（`PrimalItem_WeaponSlingshot`）<br>弓（`PrimalItem_WeaponBow`） |
| No quality weapons | 0.8 | 1–1 | 0–1 | 0 | 烟雾弹（`PrimalItem_GasGrenade`）<br>流星锤（`PrimalItem_WeaponBola`）<br>涂料刷（`PrimalItem_WeaponPaintbrush`）<br>望远镜（`PrimalItem_WeaponSpyglass`）<br>长矛（`PrimalItem_WeaponSpear`）<br>放大镜（`PrimalItem_WeaponMagnifyingGlass`）<br>剪刀（`PrimalItem_WeaponScissors`）<br>信号枪（`PrimalItem_WeaponFlareGun`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 石箭（`PrimalItemAmmo_ArrowStone`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>石头（`PrimalItemResource_Stone`） |

### 3. T2 Armor

- SetWeight：0.6
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T2_Armor_Gen1.LootItemSet_CaveDrop_T2_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 甲壳靴（`PrimalItemArmor_ChitinBoots`）<br>甲壳手套（`PrimalItemArmor_ChitinGloves`）<br>甲壳头盔（`PrimalItemArmor_ChitinHelmet`）<br>甲壳腿（`PrimalItemArmor_ChitinPants`）<br>甲壳胸甲（`PrimalItemArmor_ChitinShirt`）<br>沙漠鞋（`PrimalItemArmor_DesertClothBoots`）<br>沙漠手套（`PrimalItemArmor_DesertClothGloves`）<br>沙漠眼镜和帽子（`PrimalItemArmor_DesertClothGogglesHelmet`）<br>沙漠裤子（`PrimalItemArmor_DesertClothPants`）<br>沙漠衣服（`PrimalItemArmor_DesertClothShirt`） |

### 4. T2 Weapons

- SetWeight：0.6
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T2_Weapons_Gen1.LootItemSet_CaveDrop_T2_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 金属斧子（`PrimalItem_WeaponMetalHatchet`）<br>金属镐（`PrimalItem_WeaponMetalPick`）<br>金属镰刀（`PrimalItem_WeaponSickle`）<br>剑（`PrimalItem_WeaponSword`）<br>金属矛（`PrimalItem_WeaponPike`）<br>信号枪（`PrimalItem_WeaponFlareGun`）<br>十字弩（`PrimalItem_WeaponCrossbow`）<br>霰弹枪（`PrimalItem_WeaponShotgun`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`） |
| No quality weapons | 0.3 | 1–1 | 0–1 | 0 | 手雷（`PrimalItem_WeaponGrenade`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 石箭（`PrimalItemAmmo_ArrowStone`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>简易子弹（`PrimalItemAmmo_SimpleBullet`）<br>简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`） |

### 5. T4 Armor

- SetWeight：0.7
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T4_Armor_Gen1.LootItemSet_CaveDrop_T4_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 防弹靴（`PrimalItemArmor_MetalBoots`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>防护靴（`PrimalItemArmor_HazardSuitBoots`）<br>防护手套（`PrimalItemArmor_HazardSuitGloves`）<br>防护头盔（`PrimalItemArmor_HazardSuitHelmet`）<br>防护裤（`PrimalItemArmor_HazardSuitPants`）<br>防护上衣（`PrimalItemArmor_HazardSuitShirt`） |

### 6. T4 Weapons

- SetWeight：0.7
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T4_Weapons_Gen1.LootItemSet_CaveDrop_T4_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 泵动式霰弹枪（`PrimalItem_WeaponMachinedShotgun`）<br>制式突击步枪（`PrimalItem_WeaponRifle`）<br>复合弓（`PrimalItem_WeaponCompoundBow`）<br>电击棒（`PrimalItem_WeaponProd`）<br>制式狙击步枪（`PrimalItem_WeaponMachinedSniper`）<br>泰克榴弹发射器（`PrimalItem_WeaponTekGrenadeLauncher`）<br>泰克光刃（`PrimalItem_WeaponTekClaws`）<br>火焰喷射器（`PrimalItem_WeapFlamethrower`）<br>矿枪（`PrimalItem_WeaponMiningDrill`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`） |
| No quality weapons | 0.3 | 1–1 | 0–1 | 0 | 火箭发射器（`PrimalItem_WeaponRocketLauncher`）<br>C4遥控起爆器（`PrimalItem_WeaponC4`）<br>C4炸药（`PrimalItemC4Ammo`）<br>火箭助推榴弹（`PrimalItemAmmo_Rocket`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`）<br>高级步枪子弹（`PrimalItemAmmo_AdvancedRifleBullet`）<br>制式狙击步枪子弹（`PrimalItemAmmo_AdvancedSniperBullet`）<br>金属箭（`PrimalItemAmmo_CompoundBowArrow`） |

### 7. Underwater T1 Armor

- SetWeight：1
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_Underwater_T1_Armor_Gen1.LootItemSet_CaveDrop_Underwater_T1_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 甲壳靴（`PrimalItemArmor_ChitinBoots`）<br>甲壳手套（`PrimalItemArmor_ChitinGloves`）<br>甲壳头盔（`PrimalItemArmor_ChitinHelmet`）<br>甲壳腿（`PrimalItemArmor_ChitinPants`）<br>甲壳胸甲（`PrimalItemArmor_ChitinShirt`） |

## Mastercraft

- 开箱等级：45
- 宝箱品质倍率：2.0–4.0

### 1. T2 Armor

- SetWeight：0.6
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T2_Armor_Gen1.LootItemSet_CaveDrop_T2_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 甲壳靴（`PrimalItemArmor_ChitinBoots`）<br>甲壳手套（`PrimalItemArmor_ChitinGloves`）<br>甲壳头盔（`PrimalItemArmor_ChitinHelmet`）<br>甲壳腿（`PrimalItemArmor_ChitinPants`）<br>甲壳胸甲（`PrimalItemArmor_ChitinShirt`）<br>沙漠鞋（`PrimalItemArmor_DesertClothBoots`）<br>沙漠手套（`PrimalItemArmor_DesertClothGloves`）<br>沙漠眼镜和帽子（`PrimalItemArmor_DesertClothGogglesHelmet`）<br>沙漠裤子（`PrimalItemArmor_DesertClothPants`）<br>沙漠衣服（`PrimalItemArmor_DesertClothShirt`） |

### 2. T2 Weapons

- SetWeight：0.6
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T2_Weapons_Gen1.LootItemSet_CaveDrop_T2_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 金属斧子（`PrimalItem_WeaponMetalHatchet`）<br>金属镐（`PrimalItem_WeaponMetalPick`）<br>金属镰刀（`PrimalItem_WeaponSickle`）<br>剑（`PrimalItem_WeaponSword`）<br>金属矛（`PrimalItem_WeaponPike`）<br>信号枪（`PrimalItem_WeaponFlareGun`）<br>十字弩（`PrimalItem_WeaponCrossbow`）<br>霰弹枪（`PrimalItem_WeaponShotgun`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`） |
| No quality weapons | 0.3 | 1–1 | 0–1 | 0 | 手雷（`PrimalItem_WeaponGrenade`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 石箭（`PrimalItemAmmo_ArrowStone`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>简易子弹（`PrimalItemAmmo_SimpleBullet`）<br>简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`） |

### 3. T3 Armor

- SetWeight：0.8
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T1_Armor_Gen1.LootItemSet_CaveDrop_T1_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 粗布裤子（`PrimalItemArmor_ClothPants`）<br>粗布衣服（`PrimalItemArmor_ClothShirt`）<br>粗布帽子（`PrimalItemArmor_ClothHelmet`）<br>粗布手套（`PrimalItemArmor_ClothGloves`）<br>粗布鞋（`PrimalItemArmor_ClothBoots`）<br>兽皮靴（`PrimalItemArmor_HideBoots`）<br>兽皮手套（`PrimalItemArmor_HideGloves`）<br>兽皮帽（`PrimalItemArmor_HideHelmet`）<br>兽皮裤（`PrimalItemArmor_HidePants`）<br>兽皮上衣（`PrimalItemArmor_HideShirt`）<br>木制盾牌（`PrimalItemArmor_WoodShield`） |

### 4. T3 Weapons

- SetWeight：0.8
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T1_Weapons_Gen1.LootItemSet_CaveDrop_T1_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 石镐（`PrimalItem_WeaponStonePick`）<br>石制斧头（`PrimalItem_WeaponStoneHatchet`）<br>火把（`PrimalItem_WeaponTorch`）<br>木制球棒（`PrimalItem_WeaponStoneClub`）<br>弹弓（`PrimalItem_WeaponSlingshot`）<br>弓（`PrimalItem_WeaponBow`） |
| No quality weapons | 0.8 | 1–1 | 0–1 | 0 | 烟雾弹（`PrimalItem_GasGrenade`）<br>流星锤（`PrimalItem_WeaponBola`）<br>涂料刷（`PrimalItem_WeaponPaintbrush`）<br>望远镜（`PrimalItem_WeaponSpyglass`）<br>长矛（`PrimalItem_WeaponSpear`）<br>放大镜（`PrimalItem_WeaponMagnifyingGlass`）<br>剪刀（`PrimalItem_WeaponScissors`）<br>信号枪（`PrimalItem_WeaponFlareGun`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 石箭（`PrimalItemAmmo_ArrowStone`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>石头（`PrimalItemResource_Stone`） |

### 5. T4 Armor

- SetWeight：0.5
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T4_Armor_Gen1.LootItemSet_CaveDrop_T4_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 防弹靴（`PrimalItemArmor_MetalBoots`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>防护靴（`PrimalItemArmor_HazardSuitBoots`）<br>防护手套（`PrimalItemArmor_HazardSuitGloves`）<br>防护头盔（`PrimalItemArmor_HazardSuitHelmet`）<br>防护裤（`PrimalItemArmor_HazardSuitPants`）<br>防护上衣（`PrimalItemArmor_HazardSuitShirt`） |

### 6. T4 Weapons

- SetWeight：0.5
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T4_Weapons_Gen1.LootItemSet_CaveDrop_T4_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 泵动式霰弹枪（`PrimalItem_WeaponMachinedShotgun`）<br>制式突击步枪（`PrimalItem_WeaponRifle`）<br>复合弓（`PrimalItem_WeaponCompoundBow`）<br>电击棒（`PrimalItem_WeaponProd`）<br>制式狙击步枪（`PrimalItem_WeaponMachinedSniper`）<br>泰克榴弹发射器（`PrimalItem_WeaponTekGrenadeLauncher`）<br>泰克光刃（`PrimalItem_WeaponTekClaws`）<br>火焰喷射器（`PrimalItem_WeapFlamethrower`）<br>矿枪（`PrimalItem_WeaponMiningDrill`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`） |
| No quality weapons | 0.3 | 1–1 | 0–1 | 0 | 火箭发射器（`PrimalItem_WeaponRocketLauncher`）<br>C4遥控起爆器（`PrimalItem_WeaponC4`）<br>C4炸药（`PrimalItemC4Ammo`）<br>火箭助推榴弹（`PrimalItemAmmo_Rocket`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`）<br>高级步枪子弹（`PrimalItemAmmo_AdvancedRifleBullet`）<br>制式狙击步枪子弹（`PrimalItemAmmo_AdvancedSniperBullet`）<br>金属箭（`PrimalItemAmmo_CompoundBowArrow`） |

### 7. Underwater T1 Armor

- SetWeight：0.8
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_Underwater_T1_Armor_Gen1.LootItemSet_CaveDrop_Underwater_T1_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 甲壳靴（`PrimalItemArmor_ChitinBoots`）<br>甲壳手套（`PrimalItemArmor_ChitinGloves`）<br>甲壳头盔（`PrimalItemArmor_ChitinHelmet`）<br>甲壳腿（`PrimalItemArmor_ChitinPants`）<br>甲壳胸甲（`PrimalItemArmor_ChitinShirt`） |

### 8. Underwater T2 Armor

- SetWeight：1
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_Underwater_T2_Armor_Gen1.LootItemSet_CaveDrop_Underwater_T2_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>金属盾牌（`PrimalItemArmor_MetalShield`） |

## Ascendant

- 开箱等级：60
- 宝箱品质倍率：2.0–4.0

### 1. T1 Armor

- SetWeight：0.4
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T1_Armor_Gen1.LootItemSet_CaveDrop_T1_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 粗布裤子（`PrimalItemArmor_ClothPants`）<br>粗布衣服（`PrimalItemArmor_ClothShirt`）<br>粗布帽子（`PrimalItemArmor_ClothHelmet`）<br>粗布手套（`PrimalItemArmor_ClothGloves`）<br>粗布鞋（`PrimalItemArmor_ClothBoots`）<br>兽皮靴（`PrimalItemArmor_HideBoots`）<br>兽皮手套（`PrimalItemArmor_HideGloves`）<br>兽皮帽（`PrimalItemArmor_HideHelmet`）<br>兽皮裤（`PrimalItemArmor_HidePants`）<br>兽皮上衣（`PrimalItemArmor_HideShirt`）<br>木制盾牌（`PrimalItemArmor_WoodShield`） |

### 2. T1 Weapons

- SetWeight：0.4
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T1_Weapons_Gen1.LootItemSet_CaveDrop_T1_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 石镐（`PrimalItem_WeaponStonePick`）<br>石制斧头（`PrimalItem_WeaponStoneHatchet`）<br>火把（`PrimalItem_WeaponTorch`）<br>木制球棒（`PrimalItem_WeaponStoneClub`）<br>弹弓（`PrimalItem_WeaponSlingshot`）<br>弓（`PrimalItem_WeaponBow`） |
| No quality weapons | 0.8 | 1–1 | 0–1 | 0 | 烟雾弹（`PrimalItem_GasGrenade`）<br>流星锤（`PrimalItem_WeaponBola`）<br>涂料刷（`PrimalItem_WeaponPaintbrush`）<br>望远镜（`PrimalItem_WeaponSpyglass`）<br>长矛（`PrimalItem_WeaponSpear`）<br>放大镜（`PrimalItem_WeaponMagnifyingGlass`）<br>剪刀（`PrimalItem_WeaponScissors`）<br>信号枪（`PrimalItem_WeaponFlareGun`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 石箭（`PrimalItemAmmo_ArrowStone`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>石头（`PrimalItemResource_Stone`） |

### 3. T2 Armor

- SetWeight：0.8
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T2_Armor_Gen1.LootItemSet_CaveDrop_T2_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 甲壳靴（`PrimalItemArmor_ChitinBoots`）<br>甲壳手套（`PrimalItemArmor_ChitinGloves`）<br>甲壳头盔（`PrimalItemArmor_ChitinHelmet`）<br>甲壳腿（`PrimalItemArmor_ChitinPants`）<br>甲壳胸甲（`PrimalItemArmor_ChitinShirt`）<br>沙漠鞋（`PrimalItemArmor_DesertClothBoots`）<br>沙漠手套（`PrimalItemArmor_DesertClothGloves`）<br>沙漠眼镜和帽子（`PrimalItemArmor_DesertClothGogglesHelmet`）<br>沙漠裤子（`PrimalItemArmor_DesertClothPants`）<br>沙漠衣服（`PrimalItemArmor_DesertClothShirt`） |

### 4. Cave Weapons - Tier 2

- SetWeight：0.8
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T2_Weapons_Gen1.LootItemSet_CaveDrop_T2_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 金属斧子（`PrimalItem_WeaponMetalHatchet`）<br>金属镐（`PrimalItem_WeaponMetalPick`）<br>金属镰刀（`PrimalItem_WeaponSickle`）<br>剑（`PrimalItem_WeaponSword`）<br>金属矛（`PrimalItem_WeaponPike`）<br>信号枪（`PrimalItem_WeaponFlareGun`）<br>十字弩（`PrimalItem_WeaponCrossbow`）<br>霰弹枪（`PrimalItem_WeaponShotgun`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`） |
| No quality weapons | 0.3 | 1–1 | 0–1 | 0 | 手雷（`PrimalItem_WeaponGrenade`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 石箭（`PrimalItemAmmo_ArrowStone`）<br>麻醉箭（`PrimalItemAmmo_ArrowTranq`）<br>简易子弹（`PrimalItemAmmo_SimpleBullet`）<br>简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`） |

### 5. Cave Armor - Tier 3

- SetWeight：1
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T3_Armor_Gen1.LootItemSet_CaveDrop_T3_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 吉利靴（`PrimalItemArmor_GhillieBoots`）<br>吉利手套（`PrimalItemArmor_GhillieGloves`）<br>吉利面具（`PrimalItemArmor_GhillieHelmet`）<br>吉利护腿（`PrimalItemArmor_GhilliePants`）<br>吉利胸甲（`PrimalItemArmor_GhillieShirt`）<br>毛皮靴（`PrimalItemArmor_FurBoots`）<br>毛皮手套（`PrimalItemArmor_FurGloves`）<br>毛皮帽（`PrimalItemArmor_FurHelmet`）<br>毛皮护腿（`PrimalItemArmor_FurPants`）<br>毛皮胸甲（`PrimalItemArmor_FurShirt`） |

### 6. Cave Weapons - Tier 3

- SetWeight：1
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T3_Weapons_Gen1.LootItemSet_CaveDrop_T3_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 长管步枪（`PrimalItem_WeaponOneShotRifle`）<br>制式手枪（`PrimalItem_WeaponMachinedPistol`）<br>鱼叉枪（`PrimalItem_WeaponHarpoon`）<br>登山镐（`PrimalItem_WeaponClimbPick`）<br>探照灯枪（`PrimalItem_WeaponRadioactiveLanternCharge`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`） |
| No quality weapons | 0.3 | 1–1 | 0–1 | 0 | 毒气手雷（`PrimalItem_PoisonGrenade`）<br>简易爆炸装置（`PrimalItem_WeaponTripwireC4`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 简易步枪子弹（`PrimalItemAmmo_SimpleRifleBullet`）<br>高级子弹（`PrimalItemAmmo_AdvancedBullet`）<br>弩箭（`PrimalItemAmmo_BallistaArrow`） |

### 7. T4 Armor

- SetWeight：0.3
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T4_Armor_Gen1.LootItemSet_CaveDrop_T4_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 防弹靴（`PrimalItemArmor_MetalBoots`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>金属盾牌（`PrimalItemArmor_MetalShield`）<br>防护靴（`PrimalItemArmor_HazardSuitBoots`）<br>防护手套（`PrimalItemArmor_HazardSuitGloves`）<br>防护头盔（`PrimalItemArmor_HazardSuitHelmet`）<br>防护裤（`PrimalItemArmor_HazardSuitPants`）<br>防护上衣（`PrimalItemArmor_HazardSuitShirt`） |

### 8. T4 Weapons

- SetWeight：0.3
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_T4_Weapons_Gen1.LootItemSet_CaveDrop_T4_Weapons_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Weapons | 1 | 1–1 | 3.6–7.2 | 0.5 | 泵动式霰弹枪（`PrimalItem_WeaponMachinedShotgun`）<br>制式突击步枪（`PrimalItem_WeaponRifle`）<br>复合弓（`PrimalItem_WeaponCompoundBow`）<br>电击棒（`PrimalItem_WeaponProd`）<br>制式狙击步枪（`PrimalItem_WeaponMachinedSniper`）<br>泰克榴弹发射器（`PrimalItem_WeaponTekGrenadeLauncher`）<br>泰克光刃（`PrimalItem_WeaponTekClaws`）<br>火焰喷射器（`PrimalItem_WeapFlamethrower`）<br>矿枪（`PrimalItem_WeaponMiningDrill`）<br>手炮（`PrimalItem_WeaponHandCannon_ToF`） |
| No quality weapons | 0.3 | 1–1 | 0–1 | 0 | 火箭发射器（`PrimalItem_WeaponRocketLauncher`）<br>C4遥控起爆器（`PrimalItem_WeaponC4`）<br>C4炸药（`PrimalItemC4Ammo`）<br>火箭助推榴弹（`PrimalItemAmmo_Rocket`） |
| Ammo | 1 | 4–20 | 0–1 | 0 | 简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`）<br>高级步枪子弹（`PrimalItemAmmo_AdvancedRifleBullet`）<br>制式狙击步枪子弹（`PrimalItemAmmo_AdvancedSniperBullet`）<br>金属箭（`PrimalItemAmmo_CompoundBowArrow`） |

### 9. Underwater T1 Armor

- SetWeight：0.6
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_Underwater_T1_Armor_Gen1.LootItemSet_CaveDrop_Underwater_T1_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 甲壳靴（`PrimalItemArmor_ChitinBoots`）<br>甲壳手套（`PrimalItemArmor_ChitinGloves`）<br>甲壳头盔（`PrimalItemArmor_ChitinHelmet`）<br>甲壳腿（`PrimalItemArmor_ChitinPants`）<br>甲壳胸甲（`PrimalItemArmor_ChitinShirt`） |

### 10. Underwater T2 Armor

- SetWeight：0.8
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_Underwater_T2_Armor_Gen1.LootItemSet_CaveDrop_Underwater_T2_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）<br>潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）<br>潜水裤（`PrimalItemArmor_ScubaPants`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>防弹靴（`PrimalItemArmor_MetalBoots`）<br>防弹手套（`PrimalItemArmor_MetalGloves`）<br>防弹头盔（`PrimalItemArmor_MetalHelmet`）<br>防弹护腿（`PrimalItemArmor_MetalPants`）<br>防弹胸甲（`PrimalItemArmor_MetalShirt`）<br>金属盾牌（`PrimalItemArmor_MetalShield`） |

### 11. Underwater T3 Armor

- SetWeight：1
- 精确池：`/Game/Genesis/CoreBlueprints/LootSets/SupplyCrates/LootItemSet_CaveDrop_Underwater_T3_Armor_Gen1.LootItemSet_CaveDrop_Underwater_T3_Armor_Gen1_C`

| Entry | EntryWeight | 数量 | Entry 品质 | BP chance | 具体物品（中文名 / Blueprint 类） |
|---|---:|---:|---:|---:|---|
| Blueprints: Clothing | 1 | 1–1 | 3.6–7.2 | 0.5 | 防暴靴（`PrimalItemArmor_RiotBoots`）<br>防暴手套（`PrimalItemArmor_RiotGloves`）<br>防暴帽（`PrimalItemArmor_RiotHelmet`）<br>防暴裤（`PrimalItemArmor_RiotPants`）<br>防暴胸甲（`PrimalItemArmor_RiotShirt`）<br>防暴盾（`PrimalItemArmor_TransparentRiotShield`）<br>潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）<br>潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`） |

## 全部去重物品（中文名 / Blueprint 类）

- 古巨龟鞍（`PrimalItem_Armor_Archelon_Saddle_ASA`）
- 烟雾弹（`PrimalItem_GasGrenade`）
- 毒气手雷（`PrimalItem_PoisonGrenade`）
- 火焰喷射器（`PrimalItem_WeapFlamethrower`）
- 绊线报警陷阱（`PrimalItem_WeaponAlarmTrap`）
- 流星锤（`PrimalItem_WeaponBola`）
- 弓（`PrimalItem_WeaponBow`）
- C4遥控起爆器（`PrimalItem_WeaponC4`）
- 登山镐（`PrimalItem_WeaponClimbPick`）
- 复合弓（`PrimalItem_WeaponCompoundBow`）
- 十字弩（`PrimalItem_WeaponCrossbow`）
- 信号枪（`PrimalItem_WeaponFlareGun`）
- 手雷（`PrimalItem_WeaponGrenade`）
- 简易手枪（`PrimalItem_WeaponGun`）
- 手炮（`PrimalItem_WeaponHandCannon_ToF`）
- 手铐（`PrimalItem_WeaponHandcuffs`）
- 鱼叉枪（`PrimalItem_WeaponHarpoon`）
- 制式手枪（`PrimalItem_WeaponMachinedPistol`）
- 泵动式霰弹枪（`PrimalItem_WeaponMachinedShotgun`）
- 制式狙击步枪（`PrimalItem_WeaponMachinedSniper`）
- 放大镜（`PrimalItem_WeaponMagnifyingGlass`）
- 金属斧子（`PrimalItem_WeaponMetalHatchet`）
- 金属镐（`PrimalItem_WeaponMetalPick`）
- 矿枪（`PrimalItem_WeaponMiningDrill`）
- 长管步枪（`PrimalItem_WeaponOneShotRifle`）
- 涂料刷（`PrimalItem_WeaponPaintbrush`）
- 金属矛（`PrimalItem_WeaponPike`）
- 绊线麻醉陷阱（`PrimalItem_WeaponPoisonTrap`）
- 电击棒（`PrimalItem_WeaponProd`）
- 探照灯枪（`PrimalItem_WeaponRadioactiveLanternCharge`）
- 制式突击步枪（`PrimalItem_WeaponRifle`）
- 火箭发射器（`PrimalItem_WeaponRocketLauncher`）
- 剪刀（`PrimalItem_WeaponScissors`）
- 霰弹枪（`PrimalItem_WeaponShotgun`）
- 金属镰刀（`PrimalItem_WeaponSickle`）
- 弹弓（`PrimalItem_WeaponSlingshot`）
- 长矛（`PrimalItem_WeaponSpear`）
- 喷枪（`PrimalItem_WeaponSprayPaint`）
- 望远镜（`PrimalItem_WeaponSpyglass`）
- 木制球棒（`PrimalItem_WeaponStoneClub`）
- 石制斧头（`PrimalItem_WeaponStoneHatchet`）
- 石镐（`PrimalItem_WeaponStonePick`）
- 剑（`PrimalItem_WeaponSword`）
- 泰克光刃（`PrimalItem_WeaponTekClaws`）
- 泰克榴弹发射器（`PrimalItem_WeaponTekGrenadeLauncher`）
- 火把（`PrimalItem_WeaponTorch`）
- 简易爆炸装置（`PrimalItem_WeaponTripwireC4`）
- 高级子弹（`PrimalItemAmmo_AdvancedBullet`）
- 高级步枪子弹（`PrimalItemAmmo_AdvancedRifleBullet`）
- 制式狙击步枪子弹（`PrimalItemAmmo_AdvancedSniperBullet`）
- 石箭（`PrimalItemAmmo_ArrowStone`）
- 麻醉箭（`PrimalItemAmmo_ArrowTranq`）
- 弩箭（`PrimalItemAmmo_BallistaArrow`）
- 金属箭（`PrimalItemAmmo_CompoundBowArrow`）
- 火箭助推榴弹（`PrimalItemAmmo_Rocket`）
- 简易子弹（`PrimalItemAmmo_SimpleBullet`）
- 简易步枪子弹（`PrimalItemAmmo_SimpleRifleBullet`）
- 简易霰弹枪子弹（`PrimalItemAmmo_SimpleShotgunBullet`）
- 高棘龙鞍（`PrimalItemArmor_AcroSaddle`）
- 异特龙鞍（`PrimalItemArmor_AlloSaddle`）
- 甲龙鞍（`PrimalItemArmor_AnkyloSaddle`）
- 阿根廷巨鹰鞍（`PrimalItemArmor_ArgentavisSaddle`）
- 古马陆鞍（`PrimalItemArmor_ArthroSaddle`）
- 潮佑螈鞍（`PrimalItemArmor_AxolotlSaddle`）
- 重爪龙鞍（`PrimalItemArmor_BaryonyxSaddle`）
- 龙王鲸鞍（`PrimalItemArmor_BasiloSaddle`）
- 巨河狸鞍（`PrimalItemArmor_BeaverSaddle`）
- 鲨齿龙鞍（`PrimalItemArmor_CarchaSaddle`）
- 牛龙鞍（`PrimalItemArmor_CarnoSaddle`）
- 角鼻龙鞍（`PrimalItemArmor_CeratosaurusSaddle_ASA`）
- 砂犷兽鞍（`PrimalItemArmor_ChalicoSaddle`）
- 熔喉龙鞍（`PrimalItemArmor_CherufeSaddle`）
- 甲壳靴（`PrimalItemArmor_ChitinBoots`）
- 甲壳手套（`PrimalItemArmor_ChitinGloves`）
- 甲壳头盔（`PrimalItemArmor_ChitinHelmet`）
- 甲壳腿（`PrimalItemArmor_ChitinPants`）
- 甲壳胸甲（`PrimalItemArmor_ChitinShirt`）
- 粗布鞋（`PrimalItemArmor_ClothBoots`）
- 粗布手套（`PrimalItemArmor_ClothGloves`）
- 粗布帽子（`PrimalItemArmor_ClothHelmet`）
- 粗布裤子（`PrimalItemArmor_ClothPants`）
- 粗布衣服（`PrimalItemArmor_ClothShirt`）
- 凶齿豨鞍（`PrimalItemArmor_DaeodonSaddle`）
- 恐鳄鞍（`PrimalItemArmor_Deinosuchus_Saddle_ASA`）
- 恐象鞍具（`PrimalItemArmor_DeinotheriumSaddle_ASA`）
- 沙漠鞋（`PrimalItemArmor_DesertClothBoots`）
- 沙漠手套（`PrimalItemArmor_DesertClothGloves`）
- 沙漠眼镜和帽子（`PrimalItemArmor_DesertClothGogglesHelmet`）
- 沙漠裤子（`PrimalItemArmor_DesertClothPants`）
- 沙漠衣服（`PrimalItemArmor_DesertClothShirt`）
- 梁龙鞍（`PrimalItemArmor_DiplodocusSaddle`）
- 恐熊鞍（`PrimalItemArmor_DireBearSaddle`）
- 星尾兽鞍（`PrimalItemArmor_DoedSaddle`）
- 邓氏鱼鞍（`PrimalItemArmor_DunkleosteusSaddle`）
- 毛皮靴（`PrimalItemArmor_FurBoots`）
- 毛皮手套（`PrimalItemArmor_FurGloves`）
- 毛皮帽（`PrimalItemArmor_FurHelmet`）
- 毛皮护腿（`PrimalItemArmor_FurPants`）
- 毛皮胸甲（`PrimalItemArmor_FurShirt`）
- 吉利靴（`PrimalItemArmor_GhillieBoots`）
- 吉利手套（`PrimalItemArmor_GhillieGloves`）
- 吉利面具（`PrimalItemArmor_GhillieHelmet`）
- 吉利护腿（`PrimalItemArmor_GhilliePants`）
- 吉利胸甲（`PrimalItemArmor_GhillieShirt`）
- 岛龟平台鞍（`PrimalItemArmor_GiantTurtleSaddle`）
- 巨盗龙鞍（`PrimalItemArmor_GigantoraptorSaddle`）
- 南方巨兽龙鞍（`PrimalItemArmor_GigantSaddle`）
- 防护靴（`PrimalItemArmor_HazardSuitBoots`）
- 防护手套（`PrimalItemArmor_HazardSuitGloves`）
- 防护头盔（`PrimalItemArmor_HazardSuitHelmet`）
- 防护裤（`PrimalItemArmor_HazardSuitPants`）
- 防护上衣（`PrimalItemArmor_HazardSuitShirt`）
- 旋齿鲨鞍具（`PrimalItemArmor_Helicoprion`）
- 兽皮靴（`PrimalItemArmor_HideBoots`）
- 兽皮手套（`PrimalItemArmor_HideGloves`）
- 兽皮帽（`PrimalItemArmor_HideHelmet`）
- 兽皮裤（`PrimalItemArmor_HidePants`）
- 兽皮上衣（`PrimalItemArmor_HideShirt`）
- 禽龙鞍（`PrimalItemArmor_IguanodonSaddle`）
- 猪鳄鞍（`PrimalItemArmor_KaprosuchusSaddle`）
- 猛犸象鞍（`PrimalItemArmor_MammothSaddle`）
- 古巨蜥鞍（`PrimalItemArmor_MegalaniaSaddle`）
- 巨齿鲨鞍（`PrimalItemArmor_MegalodonSaddle`）
- 斑龙鞍（`PrimalItemArmor_MegalosaurusSaddle`）
- 大地懒鞍（`PrimalItemArmor_MegatheriumSaddle`）
- 防弹靴（`PrimalItemArmor_MetalBoots`）
- 防弹手套（`PrimalItemArmor_MetalGloves`）
- 防弹头盔（`PrimalItemArmor_MetalHelmet`）
- 防弹护腿（`PrimalItemArmor_MetalPants`）
- 金属盾牌（`PrimalItemArmor_MetalShield`）
- 防弹胸甲（`PrimalItemArmor_MetalShirt`）
- 沧龙鞍（`PrimalItemArmor_MosaSaddle`）
- 沧龙平台鞍（`PrimalItemArmor_MosaSaddle_Platform`）
- 巨犀鞍（`PrimalItemArmor_Paracer_Saddle`）
- 巨犀平台鞍（`PrimalItemArmor_ParacerSaddle_Platform`）
- 伪齿鸟鞍（`PrimalItemArmor_PelaSaddle`）
- 蛇颈龙鞍（`PrimalItemArmor_PlesiaSaddle`）
- 蛇颈龙平台鞍（`PrimalItemArmor_PlesiSaddle_Platform`）
- 无齿翼龙鞍（`PrimalItemArmor_PteroSaddle`）
- 风神翼龙鞍（`PrimalItemArmor_QuetzSaddle`）
- 风神翼龙平台鞍（`PrimalItemArmor_QuetzSaddle_Platform`）
- 霸王龙鞍（`PrimalItemArmor_RexSaddle`）
- 披毛犀鞍（`PrimalItemArmor_RhinoSaddle`）
- 防暴靴（`PrimalItemArmor_RiotBoots`）
- 防暴手套（`PrimalItemArmor_RiotGloves`）
- 防暴帽（`PrimalItemArmor_RiotHelmet`）
- 防暴裤（`PrimalItemArmor_RiotPants`）
- 防暴胸甲（`PrimalItemArmor_RiotShirt`）
- 剑齿虎鞍（`PrimalItemArmor_SaberSaddle`）
- 帝鳄鞍（`PrimalItemArmor_SarcoSaddle`）
- 雷龙鞍（`PrimalItemArmor_SauroSaddle`）
- 雷龙平台鞍（`PrimalItemArmor_SauroSaddle_Platform`）
- 潜水脚蹼（`PrimalItemArmor_ScubaBoots_Flippers`）
- 潜水面具（`PrimalItemArmor_ScubaHelmet_Goggles`）
- 潜水裤（`PrimalItemArmor_ScubaPants`）
- 潜水服（`PrimalItemArmor_ScubaShirt_SuitWithTank`）
- 蜘蛛鞍（`PrimalItemArmor_SpiderSaddle`）
- 棘背龙鞍（`PrimalItemArmor_SpinoSaddle`）
- 大角鹿鞍（`PrimalItemArmor_StagSaddle`）
- 古神翼龙鞍（`PrimalItemArmor_TapejaraSaddle`）
- 骇鸟鞍（`PrimalItemArmor_TerrorBirdSaddle`）
- 镰刀龙鞍（`PrimalItemArmor_TherizinosaurusSaddle`）
- 袋狮鞍（`PrimalItemArmor_ThylacoSaddle`）
- 魔鬼蛙鞍（`PrimalItemArmor_ToadSaddle`）
- 防暴盾（`PrimalItemArmor_TransparentRiotShield`）
- 托斯特巨鱿鞍（`PrimalItemArmor_TusoSaddle`）
- 木制盾牌（`PrimalItemArmor_WoodShield`）
- 剑射鱼鞍（`PrimalItemArmor_XiphSaddle_ASA`）
- 羽暴龙鞍（`PrimalItemArmor_YutySaddle`）
- C4炸药（`PrimalItemC4Ammo`）
- 扎啤（`PrimalItemConsumable_BeerJar`）
- 驱虫剂（`PrimalItemConsumable_BugRepellant`）
- 军用水壶（`PrimalItemConsumable_CanteenRefill`）
- 熟羊肉（`PrimalItemConsumable_CookedLambChop`）
- 熟肉干（`PrimalItemConsumable_CookedMeat_Jerky`）
- 优质熟肉（`PrimalItemConsumable_CookedPrimeMeat`）
- 优质熟鱼肉（`PrimalItemConsumable_CookedPrimeMeat_Fish`）
- 优质熟肉干（`PrimalItemConsumable_CookedPrimeMeat_Jerky`）
- 轻型解药（`PrimalItemConsumable_CureLow`）
- 药酒（`PrimalItemConsumable_HealSoup`）
- 麻醉药（`PrimalItemConsumable_Narcotic`）
- 生羊肉（`PrimalItemConsumable_RawMutton`）
- 战斗鞑靼牛排（`PrimalItemConsumable_Soup_BattleTartare`）
- 卡琳汤（`PrimalItemConsumable_Soup_CalienSoup`）
- 耐力炖锅（`PrimalItemConsumable_Soup_EnduroStew`）
- 焦红辣椒（`PrimalItemConsumable_Soup_FocalChili`）
- 菲拉咖喱（`PrimalItemConsumable_Soup_FriaCurry`）
- 拉撒路杂烩（`PrimalItemConsumable_Soup_LazarusChowder`）
- 暗影牛排（`PrimalItemConsumable_Soup_ShadowSteak`）
- 兴奋剂（`PrimalItemConsumable_Stimulant`）
- 可口蔬菜蛋糕（`PrimalItemConsumable_SweetVeggieCake`）
- 启蒙之汤（`PrimalItemConsumable_TheHorn`）
- 水瓶（`PrimalItemConsumable_WaterJarRefill`）
- 稀土肥料（`PrimalItemConsumableMiracleGro`）
- 遗忘汤（`PrimalItemConsumableRespecSoup`）
- 肥皂（`PrimalItemConsumableSoap`）
- 菊石黏液（`PrimalItemResource_AmmoniteBlood`）
- 鮟鱇鱼油（`PrimalItemResource_AnglerGel`）
- 黑珍珠（`PrimalItemResource_BlackPearl`）
- 甲壳素（`PrimalItemResource_Chitin`）
- 水泥（`PrimalItemResource_ChitinPaste`）
- 电路原件（`PrimalItemResource_Electronics`）
- 汽油（`PrimalItemResource_Gasoline`）
- 火药（`PrimalItemResource_Gunpowder`）
- 披毛犀角（`PrimalItemResource_Horn`）
- 水蛭血（`PrimalItemResource_LeechBlood`）
- 金属（`PrimalItemResource_Metal`）
- 金属锭（`PrimalItemResource_MetalIngot`）
- 黑曜石（`PrimalItemResource_Obsidian`）
- 毛皮（`PrimalItemResource_Pelt`）
- 聚合物（`PrimalItemResource_Polymer`）
- 有机聚合物（`PrimalItemResource_Polymer_Organic`）
- 稀有花朵（`PrimalItemResource_RareFlower`）
- 稀有蘑菇（`PrimalItemResource_RareMushroom`）
- 树脂（`PrimalItemResource_Sap`）
- 含硅珍珠（`PrimalItemResource_Silicon`）
- 石头（`PrimalItemResource_Stone`）
- 吸附剂（`PrimalItemResource_SubstrateAbsorbent`）
- 空调（`PrimalItemStructure_AirConditioner`）
- 大型捕兽夹（`PrimalItemStructure_BearTrap_Large`）
- 双层床（`PrimalItemStructure_Bed_Modern`）
- 火炮（`PrimalItemStructure_Cannon`）
- 化学实验桌（`PrimalItemStructure_ChemBench`）
- 大型电梯平台（`PrimalItemStructure_ElevatorPlatformLarge`）
- 中型电梯平台（`PrimalItemStructure_ElevatorPlatformMedium`）
- 小型电梯平台（`PrimalItemStructure_ElevatorPlatformSmall`）
- 电梯轨道（`PrimalItemStructure_ElevatorTrackBase`）
- 机床（`PrimalItemStructure_Fabricator`）
- 壁炉（`PrimalItemStructure_Fireplace`）
- 精炼炉（`PrimalItemStructure_Forge`）
- 工业烤箱（`PrimalItemStructure_Grill`）
- 工业研磨机（`PrimalItemStructure_Grinder`）
- 重型自动炮台（`PrimalItemStructure_HeavyTurret`）
- 工业大锅（`PrimalItemStructure_IndustrialCookingPot`）
- 工业熔炉（`PrimalItemStructure_IndustrialForge`）
- 遥控板（`PrimalItemStructure_Keypad`）
- 电灯（`PrimalItemStructure_Lamppost`）
- 全向电灯（`PrimalItemStructure_LamppostOmni`）
- 金属天花板&天窗框（`PrimalItemStructure_MetalCeiling`）
- 金属门（`PrimalItemStructure_MetalDoor`）
- 金属栅栏地基 & 支架（`PrimalItemStructure_MetalFenceFoundation`）
- 金属地基（`PrimalItemStructure_MetalFloor`）
- 金属恐龙门（`PrimalItemStructure_MetalGate`）
- 金属巨兽恐龙门（`PrimalItemStructure_MetalGate_Large`）
- 金属恐龙门框（`PrimalItemStructure_MetalGateframe`）
- 金属巨兽恐龙门框（`PrimalItemStructure_MetalGateframe_Large`）
- 金属梯子（`PrimalItemStructure_MetalLadder`）
- 金属柱子（`PrimalItemStructure_MetalPillar`）
- 金属引水口（`PrimalItemStructure_MetalPipeIntake`）
- 金属标识板（`PrimalItemStructure_MetalSign`）
- 金属广告板（`PrimalItemStructure_MetalSign_Large`）
- 金属尖刺墙（`PrimalItemStructure_MetalSpikeWall`）
- 金属墙,门框&窗框（`PrimalItemStructure_MetalWall`）
- 发电机（`PrimalItemStructure_PowerGenerator`）
- 金属屋顶, 斜坡 & 楼梯（`PrimalItemStructure_Ramp_Metal`）
- 石制屋顶, 斜坡 & 楼梯（`PrimalItemStructure_Ramp_Stone`）
- 水雷（`PrimalItemStructure_SeaMine`）
- 石制天花板&天窗框（`PrimalItemStructure_StoneCeiling`）
- 石制加固大型天窗门（`PrimalItemStructure_StoneCeilingDoorGiant`）
- 加固石门&窗户（`PrimalItemStructure_StoneDoor`）
- 石制栅栏地基 & 支架（`PrimalItemStructure_StoneFenceFoundation`）
- 石制地基（`PrimalItemStructure_StoneFloor`）
- 石制加固门（`PrimalItemStructure_StoneGate`）
- 石制恐龙门框（`PrimalItemStructure_StoneGateframe`）
- 石制巨兽门框（`PrimalItemStructure_StoneGateframe_Large`）
- 石制加固巨兽恐龙门（`PrimalItemStructure_StoneGateLarge`）
- 石制柱子（`PrimalItemStructure_StonePillar`）
- 石制引水管（`PrimalItemStructure_StonePipeIntake`）
- 石制小墙&栏杆（`PrimalItemStructure_StoneRailing`）
- 石制墙,门框&窗框（`PrimalItemStructure_StoneWall`）
- 保险柜（`PrimalItemStructure_StorageBox_Huge`）
- 马桶（`PrimalItemStructure_Toilet`）
- 金属树屋平台（`PrimalItemStructure_TreePlatform_Metal`）
- 木制树屋平台（`PrimalItemStructure_TreePlatform_Wood`）
- 树脂龙头（`PrimalItemStructure_TreeTap`）
- 金属小天花板 & 三角天花板（`PrimalItemStructure_TriCeiling_Metal`）
- 石制小天花板 & 三角天花板（`PrimalItemStructure_TriCeiling_Stone`）
- 金属三角地基（`PrimalItemStructure_TriFoundation_Metal`）
- 石制三角地基 & 小型地基（`PrimalItemStructure_TriFoundation_Stone`）
- 金属三角屋顶 & 直角三角屋顶（`PrimalItemStructure_TriRoof_Metal`）
- 石制三角屋顶 & 直角三角屋顶（`PrimalItemStructure_TriRoof_Stone`）
- 神器底座（`PrimalItemStructure_TrophyBase`）
- 自动炮台（`PrimalItemStructure_Turret`）
- 弩炮台（`PrimalItemStructure_TurretBallista`）
- 机枪炮台（`PrimalItemStructure_TurretMinigun`）
- 火箭炮台（`PrimalItemStructure_TurretRocket`）
- 金属蓄水池（`PrimalItemStructure_WaterTankMetal`）
- 手电筒附件（`PrimalItemWeaponAttachment_Flashlight`）
- 全息瞄准镜（`PrimalItemWeaponAttachment_HoloScope`）
- 激光附件（`PrimalItemWeaponAttachment_Laser`）
- 瞄准镜附件（`PrimalItemWeaponAttachment_Scope`）
- 消音器附件（`PrimalItemWeaponAttachment_Silencer`）
