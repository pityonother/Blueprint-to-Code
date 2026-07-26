# 猿狐（Ferox）持续逃跑机制调查报告

调查日期：2026-07-26
目标资产：`Shapeshifter_Small_Character_BP`（小型/可驯服形态猿狐）

## 一、结论

如果你准备驯服的那只野生猿狐实际吃到了正伤害——包括坐骑直接攻击、范围伤害或擦到判定——它会通过蓝图主动进入 `AllowAIForceFlee = true` 的强制逃跑状态。伤害来源是已驯服恐龙时，同样会触发这一逻辑。

这类逃跑之所以会看起来“一直不结束”，不是单纯的受击动画：

1. 猿狐蓝图把强制逃跑开关设为真。
2. 原生 `APrimalDinoAIController::ShouldForceFlee` 每次重新判定、且蓝图仍返回需要逃跑时，会刷新 `LastForcedFleeTime`。
3. 原生 AI 还会在自己的 `ForcedFleeDuration` 窗口以及距离/攻击范围条件下继续逃跑。
4. 因此，单纯从最后一下伤害开始原地等待 30 秒，并不能由当前代码证据保证恢复。

蓝图中能够明确找到的复位路径，是猿狐完成“发现元素并嗅闻/乞求”的动画流程后，启动一个非循环计时器：`ResetAllowFleeDelay = 30.0` 秒；计时结束执行 `ResetAllowFlee`，把 `AllowAIForceFlee` 重新设为假。

## 二、现场最可行的处理

1. 立刻把刚才攻击的坐骑收进低温舱，或停到足够远的位置并设为被动，避免再次擦伤目标。
2. 玩家身上携带正常的“元素”。代码会遍历玩家背包物品槽并检查 `ElementCustomTag`；放到快捷栏便于后续喂食，但检测本身不只看快捷栏。
3. 玩家下坐骑，进入目标猿狐约 1000 Unreal 单位的检测半径，约等于 10 米。
4. 不要追着贴身碰撞，也不要再造成任何伤害；让它重新锁定玩家并完整播放嗅闻/乞求元素动画。
5. 从嗅闻动画完成后开始，至少再等约 30 秒，期间不新增伤害。蓝图还有一个 0.5 秒延迟，所以实际应留出略多于 30 秒。
6. 即使蓝图开关已经复位，原生 AI 的现有逃跑窗口仍可能再持续一小段时间；等待它自然结束后再按正常流程喂元素。

如果它带着元素也始终不重新嗅闻、完全无法进入乞求流程，那么已确认的蓝图复位入口没有被执行。此时可以尝试让该区域彻底卸载后再回来；单机可退到主菜单重进，服务器可先离开渲染范围再返回。这个“重载恢复”属于现场规避办法，不是本次代码中明确写出的复位逻辑，不能保证所有服务器设置下都有效。

## 三、关键机制链

### 1. 受伤触发

蓝图图表：`BPAdjustDamage`

已恢复的判断链：

```text
实际伤害 > 0
  → 当前猿狐仍是野生
  → 伤害制造者是玩家
       或
     伤害制造者是已驯服恐龙
  → ShouldFlee = true
  → AllowAIForceFlee = true
  → 调用 ShouldForceFlee
```

蓝图引用：

`bp://f66149335e0fefb4dad91f76@678888f577bb49fd826ff2df/g/13`

### 2. 蓝图逃跑门

蓝图图表：`BPShouldForceFlee`

该图会检查 `AllowAIForceFlee`，并排除已驯服状态；角色默认值同时明确启用了：

- `bUseBPAdjustDamage = true`
- `bUseBPShouldForceFlee = true`

蓝图引用：

`bp://f66149335e0fefb4dad91f76@678888f577bb49fd826ff2df/g/34`

### 3. 元素检测与复位

相关图表：

- `Throttled TickFn`
- `Check for ElementFn`
- `TargetHasElement`
- `TryBegFn`
- `EventGraph` 中的 `AnimNotify_OnFinishedSniffing`

确认的默认值和行为：

- `ElementEquippedBegRadius = 1000.0`
- `ResetAllowFleeDelay = 30.0`
- `AllowBegging = true`
- `TargetHasElement` 遍历玩家物品槽并比较 `CustomTag` 与 `ElementCustomTag`
- 嗅闻结束后，经 `Delay 0.5` 设置一次非循环 `ResetAllowFlee` 计时器
- `ResetAllowFlee` 将 `AllowAIForceFlee` 设为假

事件总图引用：

`bp://f66149335e0fefb4dad91f76@678888f577bb49fd826ff2df/g/43`

### 4. 原生 AI 为什么可能延长逃跑

本次用当前 ShooterGame DLL 与 PDB 定位并反编译了：

- `APrimalDinoAIController::ShouldForceFlee`，RVA `0x11E5420`
- `APrimalDinoCharacter::BPShouldForceFlee`，RVA `0x3B82D0`
- `APrimalDinoCharacter::ShouldForceFlee`，RVA `0x15F5B10`

角色层的 `ShouldForceFlee` 会委托给 AI Controller。Controller 在蓝图门仍返回真时，会刷新 `LastForcedFleeTime` 并执行逃跑事件；在原生持续时长和距离条件未结束时，也会继续返回逃跑。

这解释了为什么该状态可能显得比“受击后固定跑若干秒”更顽固。

## 四、关于“只打了它的同类”的边界

当前 `BPAdjustDamage` 证据明确作用于“实际收到这次伤害的那只猿狐”。因此：

- 如果目标猿狐也被坐骑攻击判定、范围伤害或擦伤命中：本报告的触发链可以直接解释。
- 如果能确认目标猿狐血量完全没掉、也没收到任何伤害：本次没有在该蓝图中发现“看到同类受伤就把自己设为 `AllowAIForceFlee`”的直接逻辑。

原生 AI 中确实存在附近盟友数量、目标队伍、逃跑距离等判断，但这不能等同于“目击同类受伤”事件。要证明后一种情况，需要继续追踪伤害通知邻居/群体仇恨的其他原生入口，当前证据不足以断言。

## 五、证据范围与可信度

### 蓝图证据

- 角色证据库：
  `captures/Shapeshifter_Small_Character_BP/evidence/evidence.sqlite`
- 角色索引：
  `captures/Shapeshifter_Small_Character_BP/output/agent_index.md`
- AI Controller 证据库：
  `captures/Shapeshifter_Small_AIController_BP/evidence/evidence.sqlite`
- AI Controller 索引：
  `captures/Shapeshifter_Small_AIController_BP/output/agent_index.md`

校验结果：

- 角色资产：72/72 图表完整，SQLite 完整性与外键检查通过，源 `.uasset` SHA-256 一致。
- AI Controller：2/2 图表完整，SQLite 完整性与外键检查通过，源 `.uasset` SHA-256 一致。
- 角色证据 revision：`678888f577bb49fd826ff2df`
- AI Controller revision：`5948cba45cfe06e72b1a3a7e`

角色图表中的节点、默认值和原始 pin 关联可作为主要证据；自动恢复的执行连线中有较多 heuristic 连接，所以复杂控制流按“中等置信度”表述，没有把它写成完整源码复原。

### 原生证据

- 定向反编译：
  `native_evidence/shooter-game-native-ferox-force-flee-b0e67e1e7625.json`
- 名称检索：
  `native_evidence/shooter-game-native-name-search-ferox-flee-b0e67e1e7625.json`
- ShooterGame DLL SHA-256：
  `b0e67e1e7625dd89a30b5a1df7652a44b9b142b045f820c419b8b51bbe3d7d2a`
- PDB 已加载；3 个目标函数均完成反编译。

未从子类 AI Controller 资产中恢复到继承的 `ForcedFleeDuration`、盟友阈值等具体默认数值，因此报告没有猜测这些数值。

## 六、建议的游戏内验证

为了区分“目标被误伤”和“只因同类受伤”：

1. 找一只新的野生猿狐，记录其血量。
2. 让坐骑只攻击旁边同类，确保测试目标没有伤害数字、血量不变。
3. 观察测试目标是否仍进入同样的长时间逃跑。
4. 再单独做一次让测试目标受到极小正伤害的对照。

如果只有“测试目标实际受伤”的对照组触发，说明当前已经定位的 `BPAdjustDamage` 就是主因；如果“只伤同类、目标零掉血”也稳定触发，才值得继续追原生邻居通知链。
