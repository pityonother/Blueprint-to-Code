# ARK 背景知识库数据库结构

数据库主目录：

```text
knowledge_base/db/
```

整体分成 `1 个总目录库 + 5 个业务库`。

## 0. asset_catalog.sqlite

总目录库，不负责解释游戏机制，只负责：

- 全量文件索引
- 资产类别分桶
- 当前轮优先队列
- 已读去重
- 失败记录
- 暂缓记录

主要表：

```text
asset_files
  全量 .uasset 文件目录，机器底表，数量最大。

assets
  知识库工作资产，只保留会进入分析循环的类型。

priority_categories
  五个类别的第一层优先级。

priority_queue
  当前轮真正要读的资产，包含类别顺序和类别内排名。

processed_assets
  已经读取并纳入知识库的资产。文件 fingerprint 没变就不会重复读取。

failed_assets
  读取失败的资产，避免每轮无限重复失败。

deferred_assets
  暂缓资产，例如单个生物的 DinoCharacterStatusComponent_BP_恐龙名。
```

## 1. primal_game_data.sqlite

全局规则库。

主要表：

```text
game_data_assets
game_data_rules
registered_creatures
registered_items
registered_buffs
registered_loot
remaps
game_data_references
read_sources
unresolved_work
asset_references
```

## 2. status_components.sqlite

状态与属性库。

主要表：

```text
status_assets
status_values
leveling_rules
growth_rules
taming_status_rules
creature_status_links
deferred_creature_status
read_sources
unresolved_work
asset_references
```

## 3. primal_items.sqlite

物品库。

主要表：

```text
item_assets
item_display
item_properties
item_use_logic
item_crafting_costs
item_grants
item_references
read_sources
unresolved_work
asset_references
```

## 4. buffs.sqlite

Buff 与效果库。

主要表：

```text
buff_assets
buff_effects
buff_triggers
buff_conditions
buff_stacks
buff_stat_modifiers
buff_references
read_sources
unresolved_work
asset_references
```

## 5. loot.sqlite

宝箱与掉落库。

主要表：

```text
loot_assets
loot_crates
loot_item_sets
loot_entries
loot_conditions
loot_rewards
loot_references
read_sources
unresolved_work
asset_references
```

## 循环逻辑

```text
扫描 asset_files
筛出 assets
按 priority_categories 分桶
生成 priority_queue
读取队列资产
写入 processed_assets / failed_assets / deferred_assets
把解析结果填入 5 个业务库
下一轮自动排除已入库且文件未变的资产
```

核心原则：类别是第一层，分数只在类别内部排序。
