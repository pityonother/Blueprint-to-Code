# EffectivenessQuantityMultiplier 证据结论与验证方案

## 当前结论

`EffectivenessQuantityMultiplier` 已经从 HarvestComponent 的资源 entry 中稳定提取，但本仓库当前没有恢复它在原生资源发放路径中的消费者、运算位置或精确公式。因此 Ranking Contract v2 **不把该字段乘入静态产量公式**。

处理规则：

- 字段为 `1.0` 时，数值上是中性值；公开行仍在 `omittedFactors` 中说明该字段尚未建模。
- 字段明确不等于 `1.0` 时，行增加 `EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED`，只能进入条件性估算，不能进入已确认榜。
- 不把缺失或未知值填成 `0`，也不因某个生物而设置例外。

当前全量 evaluation catalog 中共有 1,349 个带值 entry：1,342 个为 `1.0`，6 个为 `2.0`，1 个为 `0.0`。非中性样本集中在：

- Extinction `CactusHarvestComponent_Ex_Large`：Thatch、Cactus Sap、Wood 为 `2.0`；
- Scorched Earth `CactusLargeHarvestComponent`：Thatch、Cactus Sap、Wood 为 `2.0`；
- Genesis 2 `PoisonMushroomTree_HarvestComponent`：Fibers 为 `0.0`。

这些是序列化事实，不是消费者语义证明。

上述全量计数绑定到 evaluation revision `2b978b17005dd009f3b04078bc50482d87a5574a645eb058456ccbf46f82c2a8`（生成时间 `2026-08-01T03:36:14.737620+00:00`）。换用新 DevKit 或重建 catalog 后必须重新审计，不能把这组数字当作跨版本常量。

## Ranking coverage 的三个计数

每次精确节点/资源查询都公开以下字段：

| coverage 字段 | 精确定义 |
| --- | --- |
| `rowsWithEffectivenessField` | 本次精确 Component/resource/entry 查询中，进入 `RANKED` 静态评估且显式带有该字段的攻击候选行数 |
| `rowsWithNonNeutralEffectiveness` | 上述行中，数值明确不等于 `1.0` 的行数 |
| `rowsConditionalBecauseEffectiveness` | 因该非中性值尚未建模而必须加入 `EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED`、不能进入 confirmed 层的候选行数 |

这些计数发生在 variant 折叠和 Top-K 截断之前，是“候选评估行”计数；不是返回条数、唯一物种数，也不是上面的全 catalog 资源 entry 计数。`audit_harvest_ranking_v2_changes.py` 会对每个唯一 `Component + resource + entryIndex` 查询汇总这三个字段，因此审计总数也不能与 1,349 个资源 entry 直接比较。

## 原生证据配方

1. 固定 DevKit build、evaluation revision、component revision 和上述 7 个 entry 的源文件 SHA-256。
2. 在本地原生分析中搜索字段名、反射注册、结构偏移和所有读引用。
3. 从读引用向上恢复“选择资源 entry → 计算发放数量 → 写入物品”的调用链。
4. 明确该值作用于哪一个量、发生在随机取值前后、是否依赖 damage effectiveness，以及 `0` 的分支语义。
5. 用第二种静态证据（反编译伪代码、反汇编窗口或可重复的符号/偏移定位）复核；只有字段声明或字符串命中不算完成。

若无法恢复消费者或存在动态 Blueprint/native 分支，结论保持 `NOT_MODELED`。

## 受控 runtime 实验

对每个非中性 component/resource 选择一个 `1.0` 的相近 control，并固定：游戏 build、地图、服务器倍率、Mod 列表、生物对象路径、攻击索引、等级/近战属性、Buff、基因、任务状态和完整节点生命。

- 每次 trial 使用新的完整节点；
- 记录每击资源增量、总节点产量和耗时；
- 随机数量 entry 建议至少 20 次，并在 `measurementMethod` 中记录 `randomQuantity=true` 与 `recommendedSampleCount`；20 是采样建议，不会把不足 3 次的 observation 自动升级为 confirmed；
- 原始 observation 使用 `blueprint-to-code.harvest-runtime-observation/v2`，必须 `synthetic=false`，并绑定非空 `runtimeProfileId` 与由完整可比环境计算出的 `environmentFingerprint`；
- `2.0` 与 control 比较，`0.0` 单独验证“禁用、归零或参与其他 effectiveness 分支”三种假设；
- 不删除异常 trial，只在 notes 中记录环境偏差。

1–2 个真实 trial 只能是 `OBSERVED_PRELIMINARY`，默认不进入排行；至少 3 个真实 trial 才能成为 `OBSERVED_CONFIRMED`。`synthetic=true` 永远不可发布。不同 runtime profile 的 trial 和平均值禁止混合；环境有任何可比维度变化时必须使用新的 profile。

调查时按下面顺序判读，不能倒推公式：

1. 先用原生读引用确定字段的消费者、输入量、运算顺序、随机取值前后位置与 `0` 分支；仅有字段声明或字符串命中不算证据。
2. 再用同 profile 的 `1.0` control、`2.0` 和 `0.0` 实测检验原生假设，并保留所有 trial 与 observation ID。
3. 若只有 runtime 差异而没有原生消费者，记录为待调查 hook，不拟合并发布一个“看起来符合”的公式。
4. 若只有原生路径而 runtime 不支持同一公式，继续保持 `NOT_MODELED`，检查倍率、Buff、Mod、节点新鲜度、随机数量和动态分支。

## 建模门槛

只有同时满足以下条件才允许把该字段写入静态公式：

1. 原生消费者、运算顺序与边界条件已恢复；
2. 至少一个 `2.0`、一个 `0.0` 和一个 `1.0` control 的合法 observation 支持同一公式；
3. 独立复算器以独立代码得到一致结果；
4. model、policy、result schema 全部升级版本，并生成变更审计。

在此之前，非中性 entry 保持条件性；验证工具不会创建“正确答案”或 runtime gold。
