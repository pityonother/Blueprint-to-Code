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
- 随机数量 entry 至少 20 次；
- 原始 observation 使用 `blueprint-to-code.harvest-runtime-observation/v2`，且必须 `synthetic=false`；
- `2.0` 与 control 比较，`0.0` 单独验证“禁用、归零或参与其他 effectiveness 分支”三种假设；
- 不删除异常 trial，只在 notes 中记录环境偏差。

## 建模门槛

只有同时满足以下条件才允许把该字段写入静态公式：

1. 原生消费者、运算顺序与边界条件已恢复；
2. 至少一个 `2.0`、一个 `0.0` 和一个 `1.0` control 的合法 observation 支持同一公式；
3. 独立复算器以独立代码得到一致结果；
4. model、policy、result schema 全部升级版本，并生成变更审计。

在此之前，非中性 entry 保持条件性；验证工具不会创建“正确答案”或 runtime gold。
