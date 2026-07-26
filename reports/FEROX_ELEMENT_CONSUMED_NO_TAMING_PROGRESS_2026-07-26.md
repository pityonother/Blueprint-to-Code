# 猿狐元素被扣但驯服进度不增长：机制调查

调查日期：2026-07-26

## 直接结论

猿狐的“扣除元素”和“增加永久驯服进度”不是同一个时刻发生。

正常链路是：

```text
小型猿狐吃元素
  → 本轮 ElementConsumed 增加
  → 达到本轮元素要求
  → 变成大型猿狐
  → 大型变身完成后 TransformationCount + 1
  → 大型状态结束，变回小型
  → 小型用 TransformationCount / 所需变身次数
     计算并写入 CurrentTameAffinity
  → 驯服进度此时才永久增长
```

所以，如果它刚吃完元素、已经变成大型形态但还没有变回小型，永久驯服进度没有变化属于正常机制。不要在这个阶段把“元素已扣但进度不涨”判断成失败。

## 三种现场情况

### 情况 A：喂的是小型猿狐，并成功变成大型

先等待大型阶段完整结束。野生大型形态有 `WildUnTransformDelayTimeBase = 30.0` 秒，实际时间还可受等级曲线和相关状态影响。

永久进度是在大型形态把 `TransformationCount` 回传给小型猿狐的 `UnTransform` 流程里结算，不是在扣元素的一刻结算。

### 情况 B：喂的是大型猿狐

大型形态自己的 `AnimNotify_AteElement` 没有增加 `TransformationCount` 或 `CurrentTameAffinity`。它执行的是：

- 恢复耐力，比例默认值 `PercentStaminaOnAteElement = 0.5`
- 增加成瘾度，默认每个元素 `AddictionIncreasePerElement = 0.05`
- 重置/更新大型形态结束时间

因此，元素被大型形态吃掉但驯服进度不增长，是代码设计结果，不应继续靠给大型形态喂元素来推进驯服。

### 情况 C：喂的是小型猿狐，但没有完成变身，或者完整变回小型后仍是 0

这才属于异常链路。

小型猿狐的元素计数不是在玩家物品被扣除时直接写入，而是在吃元素动画的 Notify/服务器命令链里写入 `ElementConsumed`。如果吃元素动画没有走到关键 Notify、玩家/队伍归属没有锁定，或者变身状态被中断，就可能出现：

```text
玩家元素已扣
  但 ElementConsumed / TransformationCount 没有成功提交
  → 永久驯服进度不增长
```

结合这只猿狐此前存在强制逃跑状态，最可疑的是同一只个体的 AI/动画状态没有完全恢复；这是现场推断，当前代码不能证明强制逃跑必然导致该提交失败。

## 为什么界面可能看起来回到 0

小型猿狐的自定义进度条有两个阶段：

1. `CurrentTameAffinity == 0` 时，显示的是本轮元素进度，近似为
   `ElementConsumed / 当前变身所需元素数`。
2. 已经有永久驯服值后，显示
   `CurrentTameAffinity / RequiredTameAffinity`。

在生成大型形态时，小型猿狐会消费/降低本轮 `ElementConsumed`。因此，本轮元素条瞬间填满后又回到 0，不代表永久驯服值已经结算；永久值仍要等大型形态结束。

## 当前建议

1. 先确认这次喂食后它有没有完整变成大型。
2. 如果已经是大型，不要再喂元素，等待它完整变回小型后再看驯服百分比。
3. 如果小型吃掉元素却完全没有变身，立刻停止继续喂，避免继续损失元素。
4. 把坐骑和其他攻击来源移远并设为被动，等待猿狐回到正常的小型乞求状态。
5. 让区域完整卸载再回来；单机可退主菜单重进，服务器可先离开渲染范围，仍异常再考虑低风险重启服务器。
6. 下一次只在小型猿狐正常播放乞求动作、没有逃跑或其他 Montage 时喂；观察它是否完整播放吃元素动画并进入小到大的过渡。
7. 如果完成一次“小型 → 大型 → 小型”完整循环后仍然是 0，这只个体的 `TransformationCount`/小型实例关联很可能已经卡住。此时换一只新的野生猿狐做对照，比继续消耗元素更稳妥。

## 关键代码证据

### 小型猿狐

- `BPDinoTooltipCustomTamingProgressBar`
  `bp://f66149335e0fefb4dad91f76@678888f577bb49fd826ff2df/g/17`
- `BPServerHandleNetExecCommand`
  `bp://f66149335e0fefb4dad91f76@678888f577bb49fd826ff2df/g/33`
- `EventGraph`，含 `AnimNotify_EatingElement` 与 `ElementConsumed + 1`
  `bp://f66149335e0fefb4dad91f76@678888f577bb49fd826ff2df/g/43`
- `Spawn Bigly Fn`，消费本轮 `ElementConsumed` 并建立大型实例
  `bp://f66149335e0fefb4dad91f76@678888f577bb49fd826ff2df/g/66`
- `UnTransform`，用变身次数计算 `CurrentTameAffinity`
  `bp://f66149335e0fefb4dad91f76@678888f577bb49fd826ff2df/g/76`

小型角色证据 revision：`678888f577bb49fd826ff2df`

### 大型猿狐

- `AnimNotify_AteElement`，只处理耐力、成瘾度和变身时间
  `bp://9c177c39a792e8d5894c4f34@4e27540fe9b5d9151843de0f/g/24`
- `AnimNotify_CompleteTransform`，执行 `TransformationCount + 1`
  `bp://9c177c39a792e8d5894c4f34@4e27540fe9b5d9151843de0f/g/30`
- `Un TransformFn`，把 `TransformationCount` 回传给小型实例的 `UnTransform`
  `bp://9c177c39a792e8d5894c4f34@4e27540fe9b5d9151843de0f/g/151`

大型角色证据 revision：`4e27540fe9b5d9151843de0f`

大型资产共恢复 140 个图表、2666 个节点和 9637 个 Pin。SQLite 完整性、外键、索引计数和源 `.uasset` SHA-256 校验均通过。
