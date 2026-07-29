# Megachelon：Genesis 1 深水交配条件蓝图实测方案

## 1. 测试目标

在 Genesis 1 海洋生态区验证 Megachelon（`GiantTurtle_Character_BP`）的特殊交配深度门槛，并确认离开门槛后是否会清零交配进度。

当前从原始蓝图恢复出的待验证公式是：

```text
Allow = ActorLocation.Z <= GetDeepWaterStartZ(ActorLocation) - 1000
```

等价写法：

```text
DepthBelowDeepStart = DeepWaterStartZ - ActorZ
Allow = DepthBelowDeepStart >= 1000
```

这里的 `1000` 是 Unreal 单位，约等于 10 米。`GetDeepWaterStartZ` 是当前位置 XY 对应的“深水开始线 Z”，不是海面高度，也不是一个全地图固定常量。

## 2. 已确认的编辑器资产

- Genesis 1 主地图：`/Game/Maps/Genesis/Genesis_WP`
- Megachelon 原始角色蓝图：`/Game/Genesis/Dinos/GiantTurtle/GiantTurtle_Character_BP`
- 测试目录建议：`/Game/Mods/MegachelonDeepWaterTest/`

不要直接修改原始 `GiantTurtle_Character_BP`。在测试目录内创建它的子蓝图：

```text
BP_Megachelon_DeepWaterProbe
```

## 3. 第一阶段：搭建观测蓝图

### 3.1 变量

在 `BP_Megachelon_DeepWaterProbe` 中新增：

| 变量 | 类型 | 默认值 | 用途 |
|---|---|---:|---|
| `SampleInterval` | Float | `1.0` | 每秒采样一次 |
| `DeepWaterStartZ_Debug` | Double | `0` | 深水开始线 |
| `ActorZ_Debug` | Double | `0` | 当前生物 Z |
| `RequiredZ_Debug` | Double | `0` | `DeepWaterStartZ - 1000` |
| `DepthBelowStart_Debug` | Double | `0` | `DeepWaterStartZ - ActorZ` |
| `FormulaPass_Debug` | Boolean | `false` | 重建公式结果 |
| `NativeAllow_Debug` | Boolean | `false` | 原蓝图 `GetAllowMating` 的结果 |
| `LogEnabled` | Boolean | `true` | 是否打印日志 |

### 3.2 启动采样

事件图执行链：

```text
Event BeginPlay
  -> Switch Has Authority
  -> Authority
  -> Set Timer by Event
       Time = SampleInterval
       Looping = true
  -> SampleOnce
```

不要放在 `Event Tick`，每秒采样已经足够，也更容易读日志。

### 3.3 `SampleOnce` 节点链

按下面顺序搭建：

```text
Get World
  -> GetDayCycleManager(World)
  -> Is Valid

Get Actor Location(Self)
  -> Break Vector
  -> ActorZ_Debug

GetDeepWaterStartZ
  Self       = DayCycleManager
  AtLocation = Actor Location
  -> DeepWaterStartZ_Debug

DeepWaterStartZ_Debug - 1000
  -> RequiredZ_Debug

ActorZ_Debug <= RequiredZ_Debug
  -> FormulaPass_Debug

DeepWaterStartZ_Debug - ActorZ_Debug
  -> DepthBelowStart_Debug
```

比较节点的接线必须是：

```text
A = ActorZ_Debug
B = RequiredZ_Debug
运算 = Less or Equal（<=）
```

再调用继承的 `GetAllowMating`，把返回的 `Allow` 保存到 `NativeAllow_Debug`。同时读取继承变量 `bAllowMating` 和 `MatingProgress`。如果子蓝图中无法直接读取其中某个变量，就先只记录 `GetAllowMating` 与游戏画面的交配状态，不要为了测试去修改原始蓝图访问权限。

最后用 `Format Text -> Print String` 输出：

```text
Z={ActorZ} | DeepStart={DeepStartZ} | NeedZ={RequiredZ}
BelowStart={DepthBelow} | Formula={FormulaPass} | Native={NativeAllow}
StoredAllow={bAllowMating} | Progress={MatingProgress}
```

颜色建议：通过为绿色，不通过为红色，`DayCycleManager` 无效为黄色。

## 4. 第二阶段：边界扫描

先只放一只 `BP_Megachelon_DeepWaterProbe`。在同一个 XY 坐标上改变 Z，避免把“位置变化”误当成“深度变化”。

定义：

```text
Offset = ActorZ - DeepWaterStartZ
```

依次测试以下点，每个点停留 5 至 10 秒：

| 序号 | Offset | 预期 `FormulaPass` | 原因 |
|---:|---:|---|---|
| 1 | `+100` | false | 位于深水开始线之上 |
| 2 | `0` | false | 仅到达深水开始线 |
| 3 | `-500` | false | 只低 500 单位 |
| 4 | `-999` | false | 尚未达到 1000 单位 |
| 5 | `-1000` | true | 因为原公式使用 `<=`，等号应通过 |
| 6 | `-1001` | true | 刚越过门槛 |
| 7 | `-1500` | true | 明确位于门槛以下 |

如果要自动化扫描，可以再建一个 `BP_DeepWaterTestController`：

- `Target`：`BP_Megachelon_DeepWaterProbe` Object Reference，设为 Instance Editable。
- `Offsets`：Double Array，填入上表数值。
- 每 5 秒读取 Target 当前 XY 的 `DeepWaterStartZ`。
- 新 Z 使用 `DeepWaterStartZ + Offset`。
- `SetActorLocation` 时只替换 Z，X/Y 保持不变，并启用 Teleport。
- 不要在 Blueprint Function 内使用 `Delay`；用 Timer 或 Custom Event 串联步骤。

## 5. 第三阶段：真实交配验证

### 5.1 固定条件

准备同类、已驯服、已成年的一公一母：

- 双方都启用交配。
- 母方交配冷却为 0。
- 两只保持在普通交配距离内。
- 测试过程中不冷冻、不上传、不离开渲染范围。
- 除了指定的 Z 以外，尽量不改变其他条件。

原 `UpdateAllowMating` 蓝图先检查 `BPIsTamed`，再检查 `bIsFemale`，因此特殊深水门槛的关键观测对象是母龟。

### 5.2 三步主实验

1. **通过点**：母龟放在 `Offset=-1001`，公龟保持在交配距离内。启用交配，等待 `MatingProgress > 0`。
2. **越界点**：只把母龟移动到 `Offset=-999`。预期 `GetAllowMating=false`、`bAllowMating=false`，并且 `MatingProgress` 归零。
3. **重新进入**：只把母龟移回 `Offset=-1001`。预期允许状态恢复，但交配进度从 0 重新开始，而不是接着旧进度。

### 5.3 对照实验

- 母龟固定在 `-1001`，只移动公龟到 `-999`，同时保证仍在交配距离内。
- 这是验证“特殊深水门槛是否只由母方触发”的对照，不要提前把结果当成定论。
- 如果交配停止，先区分是深度门槛还是普通距离、水域、寻路等条件造成。

## 6. 地图重复性

在 Genesis 1 海洋区选择至少三个不同 XY 位置：

1. 岛屿外缘或较浅海域。
2. 开阔海域。
3. 深海沟或明显更深的区域。

每个位置重复 `-999 / -1000 / -1001` 三个边界点至少两次。预期不同地点的 `DeepWaterStartZ` 数值可以不同，但相对门槛始终是 `DeepWaterStartZ - 1000`。

## 7. 数据记录表

| Run | Site | X | Y | ActorZ | DeepWaterStartZ | Offset | RequiredZ | Formula | NativeAllow | StoredAllow | Progress Before | Progress After | 结果 |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|---|
| 1 | A |  |  |  |  | -999 |  |  |  |  |  |  |  |
| 2 | A |  |  |  |  | -1000 |  |  |  |  |  |  |  |
| 3 | A |  |  |  |  | -1001 |  |  |  |  |  |  |  |

每个点至少保存一张屏幕截图，并把 Output Log 中对应的打印行一并保存。

## 8. 完成标准

满足以下条件即可认为实测完成：

- `FormulaPass` 与原生 `GetAllowMating` 在所有边界点一致。
- `-999` 不通过，`-1000` 与 `-1001` 通过。
- 母龟在交配过程中从通过区进入不通过区后，交配进度被清零。
- 至少三个 XY 位置都得到相同的相对门槛规律。
- 对异常结果重复一次，并排除距离、冷却、性别、驯服、成年状态等普通交配条件。

## 9. 本次启动故障修复记录

不兼容插件为 `UnrealCopilot`。它在 ARK DevKit 的 `PostEngineInit` 阶段报“无法找到模块 UnrealCopilot”。插件目录已移出有效 Plugins 路径，ARK DevKit 已通过 Epic 重启并正常进入编辑器。

可恢复备份保存在项目目录之外的
`ARKDevKit-plugin-backups/UnrealCopilot-<timestamp>`。该目录是本机恢复材料，
不会提交到仓库；实际路径以移出插件时记录的备份输出为准。
