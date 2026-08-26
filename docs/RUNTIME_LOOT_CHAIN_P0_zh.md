# 运行时掉落链 P0

本合同把“目标物品出现在某个奖励池”与“玩家行为确实会沿地图执行链到达该奖励池”分开。只有每一段都有当前、绑定且可正式查询的 Evidence，整条链才返回 `COMPLETE`。

## 单资产查询

`runtime-signals` 读取运行时发射与刷出信号：

- `CallGlobalLevelEvent.EventName` 必须是精确 native Pin 身份、未连接的字面默认值；
- 有权威 Wire 时，编辑器默认值不可升级为运行时事件名，返回 `DYNAMIC_EVENT_NAME_NOT_RESOLVED`；
- `SpawnActor.Class` 必须是精确 native Pin 身份和可用的未连接类默认值，否则返回明确 gap。

`runtime-routes --event-name <name>` 在一个地图或接收资产内寻找同名 `K2Node_CustomEvent`，只沿规范化 `edges` 表中的精确 exec Wire 追踪到 `K2Node_SpawnActorFromClass`。字符串同名但没有权威执行线，不构成路线。

`loot-rewards --item-query <item>` 在已解码 Class Default 中定向寻找 Loot Entry，并区分：

- `PHYSICAL_ITEM_ONLY`：只给实体成品；
- `PHYSICAL_BLUEPRINT_ONLY`：只给实体蓝图；
- `ITEM_OR_PHYSICAL_BLUEPRINT`：进入该 Entry 且实际发放物品后，再按显式 Blueprint 转换概率决定；
- `TEKGRAM_UNLOCK`：只有显式解锁字段存在时才成立，物品名含 `Tek` 不构成解锁证据。

`EntryWeight` 不直接换算总掉率。`ChanceToBeBlueprintOverride` 也只标为 `CONDITIONAL_ON_ENTRY_SELECTION_AND_ITEM_GRANT`，不会输出未经完整 ItemSet 选择上下文证明的 `overallDropChance`。

## 跨资产组合

```powershell
runtime\python\python.exe scripts\query_runtime_loot_chain.py `
  --emitter-asset-dir "captures\Iceworm_Queen_Character_BP" `
  --receiver-asset-dir "captures\Ragnarok_WP" `
  --reward-asset-dir "captures\SupplyCrate_Base_Horde_Easy" `
  --event-name "Ice Queen is Killed" `
  --item-query "PrimalItemArmor_UmbraSaddle"
```

返回 schema 为 `blueprint-to-code.runtime-loot-chain/v1`，固定包含五步：

1. `event_emit`
2. `event_receiver`
3. `spawn_actor`
4. `reward_membership`
5. `spawn_to_reward_source`

最后一步只有在刷出的 generated class 能精确归一到同一个奖励 Evidence 资产 Object Path 时才确认。单独给出另一个 Loot Pool，即使目标物品命中，也只能确认奖励池成员关系，不能补造刷箱到池的绑定。

常见断点：

- `GLOBAL_EVENT_RECEIVER_NOT_INDEXED`：当前接收资产未恢复同名 Custom Event；不等于游戏中绝对不存在；
- `SPAWN_ROUTE_NOT_RECOVERED`：没有权威 exec 路径到可识别的 Spawn Class；
- `SPAWNED_CLASS_TO_REWARD_SOURCE_NOT_BOUND`：刷出类与奖励资产没有精确身份绑定；
- `RECEIVER_SOURCE_NOT_AVAILABLE` / `REWARD_SOURCE_NOT_AVAILABLE`：调用者声明的预期地图或地图专属奖励源未提供；
- `NOT_FOUND_IN_RECOVERED_DEFAULTS`：只表示目标未出现在当前资产已恢复的默认值中，不表示全局不掉落。

地图 Object Path 捕获会按包类型回退到 `.umap`，来源快照继续包含主包与已有 `.uexp` / `.ubulk` 伴随文件；工具不会修改 ARK DevKit 源资产。
