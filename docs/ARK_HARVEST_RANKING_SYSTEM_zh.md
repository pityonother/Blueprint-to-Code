# ARK 资源采集排行系统：证据、口径与使用方法

## 1. 这套系统解决什么问题

这套系统不再让 AI 每次读取一份巨大的蓝图转储后自行猜测，而是把本地 ARK DevKit 资产预处理成四层兼容结果：

1. `.full.json` 保存完整、可追溯的解析结果；
2. `.ai.json` 保存 AI 首轮判断所需的最小事实、缺口与源指纹；
3. `.query.json` 保存兼容 Component API 按需排行所需的最佳行索引；
4. `.md` 保存便于人工复核的具体蓝图结论。

排行依据来自当前本地 DevKit 中恢复出的攻击、伤害类型、采集组件和资源权重。网上的星级或经验榜单只用于选择候选和交叉检查，不进入系数计算。

这里的“精确”是指：报告中的输入系数可以追溯到具体 `.uasset`、属性、继承链和源文件 SHA-256。它不等于已经还原游戏运行时的最终掉落公式。

## 2. 组成与职责

| 文件 | 职责 |
| --- | --- |
| `scripts/blueprint_translator/harvest_ranking.py` | 纯数据投影、兼容性判断、比较指数计算和排序 |
| `scripts/rank_ark_harvest.py` | 扫描 DevKit、解析生物和组件、追踪伤害类型继承与资源覆盖、生成报告 |
| `scripts/blueprint_translator/harvest_report_validation.py` | 定义完整报告与压缩报告之间的验证合同 |
| `scripts/verify_ark_harvest_report.py` | 从命令行执行压缩合同验证 |
| `scripts/blueprint_translator/harvest_evaluation_catalog.py` | 全 Content 生物候选发现、祖先链确认、物种折叠和紧凑攻击事实目录 |
| `scripts/blueprint_translator/harvest_catalog_sqlite.py` | 将 canonical 节点 JSON 构建成分页、搜索、过滤和详情用 SQLite 读模型 |
| `scripts/blueprint_translator/harvest_node_repository.py` | 只读打开 SQLite，并校验其绑定的源 JSON SHA-256；不一致时失败关闭 |
| `scripts/blueprint_translator/harvest_ranking_verifier.py` | 从底层事实独立抽样复算正向和反向排行 |
| `scripts/build_ark_harvest_explorer.py` | 八个受控子命令、staging 验收、原子提升与失败回滚的总编排器 |
| `analysis/harvest_rankings/resource_catalog.json` | 已发现资源类到 HarvestComponent 的索引 |
| `analysis/harvest_rankings/harvest_ranking_*.full.json` | 所有攻击、组件、状态、缺口和源文件指纹 |
| `analysis/harvest_rankings/harvest_ranking_*.ai.json` | 供 AI 优先读取的压缩视图 |
| `analysis/harvest_rankings/harvest_ranking_*.query.json` | 供兼容 Component API 查询的运行时索引，不默认交给 AI |
| `analysis/harvest_rankings/harvest_ranking_*.md` | 面向人的具体蓝图摘要 |

当前实现读取以下范围：

- 内容根：`<DevKit>/Projects/ShooterGame/Content`；
- 采集组件：标准 PrimalEarth 目录、可选的全 Content `*HarvestComponent*.uasset`，以及资源点精确引用清单；
- 伤害类型：`PrimalEarth/CoreBlueprints/DamageTypes/*.uasset`，按实际攻击与父链有界展开；
- 生物：由对象路径直接定位，可来自 `/Game` 下其他目录。

这里有两个入口，不能混为一谈：`rank_ark_harvest.py` 不指定 `--creature` 时仍只生成四只代表生物的兼容 Component 报告；Resource Explorer 则自动发现 `*Character*.uasset` 和 `*Char_BP*.uasset`，再以 `PrimalDinoCharacter` 祖先证据筛选。2026-07-21 正式 evaluation 产物在 2,088 个候选中确认了 1,406 个生物资产，折叠为 280 个物种和 3,586 个攻击。它仍保留 `claimsAllCreatures=false`、`claimsAllDiscoveredCandidates=false` 和 `claimsGlobalTop=false`。

## 3. 精确系数的证据链

每一条可排名记录都经过同一条链路：

```text
PrimalDinoCharacter descendant.AttackInfos
  -> 攻击名、MeleeDamageAmount、AttackInterval、MeleeDamageType
  -> DamageType 的父类链
  -> 该 DamageType 针对目标资源的替换 DamageType
  -> HarvestComponent.HarvestDamageTypeEntries 的最近匹配项
  -> HarvestComponent.HarvestResourceEntries 的资源权重及 DamageType 覆盖
  -> 兼容性状态与比较指数
```

解析器保留 `AttackInfos`、`HarvestResourceEntries` 和 `HarvestDamageTypeEntries` 的数组元素边界。也就是说，一个元素中的 `EntryWeight` 不会被误当作另一个元素的值。对象数组按位置对齐后，才会形成“资源 -> 替换伤害类型”或“伤害类型 -> 权重覆盖”的映射。

伤害类型匹配不是只看名称相等。系统从有效伤害类型开始沿父类链向上查找，选择 HarvestComponent 中最近的可接受条目；资源权重、最小数量和最大数量覆盖也按同一条链选择最近匹配项。

### 3.1 具体例子：Magmasaur 的 Bite 采集 Metal

当前报告对应的重点组件是 `MetalHarvestComponent`。完整链路如下。

#### 第一步：生物攻击蓝图

`Cherufe_Character_BP.uasset` 的 `AttackInfos[0]` 恢复出：

| 属性 | 值 |
| --- | ---: |
| `AttackName` | `Bite` |
| `MeleeDamageAmount` | `120` |
| `AttackInterval` | `0.5` |
| `MeleeDamageType` | `DmgType_Melee_DmgStone_ExtraHarvest_AndMetal_C` |
| `MeleeSwingRadius` | `450` |
| `bBasicAttack` | `true` |

#### 第二步：资源专用伤害类型替换

`DmgType_Melee_DmgStone_ExtraHarvest_AndMetal.uasset` 将以下资源的有效伤害类型替换为 `DmgType_Melee_Dino_Herbivore_Medium_MineStone_C`：

- `PrimalItemResource_Metal_C`；
- `PrimalItemResource_Obsidian_C`；
- `PrimalItemResource_Crystal_C`。

因此，Magmasaur 的原始伤害类型不能直接拿去查金属节点系数；目标是 Metal 时必须先应用这项替换。

#### 第三步：MetalHarvestComponent 中的匹配系数

`MetalHarvestComponent.uasset` 对 `MineStone` 恢复出：

| 项目 | Stone | Metal |
| --- | ---: | ---: |
| 有效资源权重 | `0.4` | `0.63` |
| 最小数量覆盖 | `1` | `1` |
| 最大数量覆盖 | `1` | `2` |

同一组件的 `HarvestDamageTypeEntries` 对 `MineStone` 给出：

- `DamageMultiplier = 2`；
- `HarvestQuantityMultiplier = 1`。

组件还记录了 `MaxHarvestHealth = 620` 和 `HarvestHealthGiveResourceInterval = 40`。这些值会保留在报告中，但当前没有被擅自拼入最终掉落公式。

#### 第四步：可复算的比较指数

```text
baseDamage / attackInterval = 120 / 0.5 = 240
harvestPressurePerSecond    = 240 * 2 * 1 = 480
resourceWeightShare         = 0.63 / (0.40 + 0.63)
                            = 0.6116504801
engineComparisonIndex       = 480 * 0.6116504801
                            = 293.5922304478
```

这里的 `0.629999995...` 和 `0.400000005...` 是资产中浮点数的实际序列化表示；Markdown 为便于阅读显示为 `0.63` 和 `0.4`，完整精度保留在 `.full.json`。

### 3.2 为什么不能只看到“3 × 7”就给 Doedicurus 金属高分

`MetalHarvestComponent` 确实包含 `SuperMineStone` 的：

- `DamageMultiplier = 3`；
- `HarvestQuantityMultiplier = 7`。

但 Metal 对 `SuperMineStone` 没有正权重覆盖，回退后的 `EntryWeight` 是明确的 `0`。因此 Doedicurus 在这个具体组件上被标记为：

```text
INCOMPATIBLE / ZERO_RESOURCE_WEIGHT
```

系统不会把它当作“缺数据”，也不会把 `3 × 7` 误写成金属产量倍率。它可以在另一个具有正 Metal 权重的组件上进入排行，所以结论必须绑定到“生物 + 攻击 + 资源 + HarvestComponent”，不能压成一个全局生物星级。

### 3.3 二进制解析的保真护栏

报告压缩之前先修解析边界，而不是压缩错误值：

- ARK compact Bool tag 会消费 `bool + property-GUID marker + optional 16-byte GUID`；
- `None` 终止符后允许最多 16 字节的全零对齐填充，但不吞任意 native 尾部；
- Int/Float/Object 等 scalar 必须在 `declared_size/property_end` 内完整读取；
- 越界 PackageIndex 标记为 `NOT_RECOVERED/low`，不能以非空整数取得 high confidence；
- fixed-array 的 `array_index` 保留到属性和变量投影，防止同名元素覆盖；
- heuristic fallback 的 scalar 不再自动标记 high。

真实 DevKit 回归中，`DmgType_Melee_Dino_Carnivore_MineWood_Piercing` 的 `ArmorDurabilityDegradationMultiplier` 恢复为 `12.0`；`DmgType_Melee` 的 `DefaultImpulse` 恢复为 `50000.0`，`KillIcon` 内的 `U/V/UL/VL` 也不再错误上浮为顶层字段。

DamageType 索引必须覆盖该目录的全部 `.uasset`，不能只匹配 `DmgType_*`。当前目录共有 273 个资产；旧规则只索引 265 个并漏掉 `ShooterDamageTypeBP_Base.uasset`，曾把 119 条三资源组合误标成“资产缺失”。现在 `ShooterDamageTypeBP_Base_C -> ShooterDamageType` 会保留为已解析蓝图父链到原生终点，非 `_C` 原生类不再被伪报成缺失资产。

## 4. 三种状态不能混用

| 状态 | 含义 | 是否有数值排行 |
| --- | --- | --- |
| `RANKED` | 必需系数已恢复，目标资源存在，有效 DamageType 被接受，资源权重大于零 | 有 `engineComparisonIndex` |
| `INCOMPATIBLE` | 已有足够证据确认这次组合不适用，不是信息缺失 | 没有，指数为 `null` |
| `UNRANKED` | 某项必需事实未恢复，无法可靠判断 | 没有，指数为 `null` |

常见 `INCOMPATIBLE` 原因：

- `RESOURCE_NOT_IN_COMPONENT`：组件没有目标资源条目；
- `DAMAGE_TYPE_NOT_ACCEPTED`：有效 DamageType 及其父类链都没有匹配的采集条目；
- `ZERO_RESOURCE_WEIGHT`：资源存在，但对该 DamageType 的有效权重明确小于等于零。

常见 `UNRANKED` 原因：

- `REQUIRED_ATTACK_FACT_NOT_RECOVERED`：攻击名、伤害类型、基础伤害或攻击间隔缺失；
- `REQUIRED_COMPONENT_FACT_NOT_RECOVERED`：资源数组、伤害类型数组或组件必需字段未可靠恢复；
- `REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED`：DamageType 资产、父链或资源专用替换未可靠恢复；
- `TARGET_RESOURCE_FACT_NOT_RECOVERED`：某个资源条目的身份或必需权重未可靠恢复；
- `RESOURCE_WEIGHT_NOT_RECOVERED`：目标资源权重未知；
- `REQUIRED_COEFFICIENT_NOT_RECOVERED`：伤害倍率、数量倍率或合法攻击间隔缺失。

一个关键约束是：未知永远不转换为数字 `0`。`0` 只有在资产明确给出零值时才参与兼容性判断。
攻击、组件、目标资源和 DamageType 的缺口分别保存在 `missingFactsByScope`；不进入当前指数的 Min/Max 数量覆盖缺口只作为 `warnings`，不会伪装成公式必需项。只要任一竞争资源权重未知，归一化分母就不完整，该行必须保持 `UNRANKED`。

## 5. 公式口径与禁止外推范围

当前公式是：

```text
harvestPressurePerSecond = baseDamage / attackInterval
                           * DamageMultiplier
                           * HarvestQuantityMultiplier

resourceWeightShare      = target effective weight
                           / sum(all positive effective weights)

engineComparisonIndex    = harvestPressurePerSecond
                           * resourceWeightShare
```

`engineComparisonIndex` 是无量纲的引擎系数比较值，只适合在以下条件一致时横向排序：

- 同一目标资源；
- 同一 HarvestComponent；
- 同一 DevKit 资产版本；
- 相同服务器和运行时条件。

它不是“每击资源数”，也不是“资源/秒”。虽然内部字段名是 `harvestPressurePerSecond`，`AttackInterval` 仍只是蓝图配置值，尚未用真实动画墙钟时间校准。

以下因素当前明确不进入指数：

- 生物运行时近战属性和成长点；
- 服务器采集倍率及其他配置；
- 节点剩余生命和单次扣血上限；
- 实际动画周期、移动、转向和攻击取消；
- 一次攻击实际命中的节点数；
- 受控环境中的实测掉落；
- `OverrideQuantityMin/Max`、`MaxHarvestHealth`、`HarvestHealthGiveResourceInterval` 与最终发放流程的完整运行时关系。

所以报告中的 `observedYieldPerSecond` 按设计保持 `null`。只有补齐运行时公式或可复现的受控实测后，才能新增真实产量指标；不能把当前指数改名成产量。

### 5.1 Explorer 的确认层与条件静态估算

Explorer 查询只针对当前选中的“节点定义 + HarvestComponent + 资源条目”复算，不把不同 Component 的指数直接合并成全局倍率：

- `bSkipTamed`、`bOnlyOnWildDinos` 和 `bPreventWithRider` 是硬排除；命中后不进入可用排行；
- `bUseBlueprintCanRiderAttack` 或 `bUseBlueprintAdjustOutputDamage` 依赖未恢复的蓝图运行时返回值，不会被假定为通过；
- 如果后一类行的全部静态攻击和 Component 必需事实已经恢复，可以显示数值估算，但必须保持 `usageEligibilityStatus=CONDITIONAL`、`usageEstimateBasis=STATIC_ATTACK_FACTS_WITH_BLUEPRINT_RUNTIME_RESULT_NOT_RECOVERED`、`rankingTier=CONDITIONAL` 和 `evidence.status=PARTIAL`；
- 只有没有条件原因、且生物和排行证据均确认的行，才进入 `CONFIRMED` 层。

正向查询的 `relativeToNodeTopPercent` 是该行指数除以同一节点资源的第一名指数。反向“某只恐龙擅长什么”也使用“该恐龙在某节点资源上的指数 ÷ 该节点资源第一名指数”排序，再展开引用该精确 Component/resource/entry 组合的节点。这个百分比表示相对当前可评估候选的强弱，不是游戏中的掉落概率、采集率或实测资源/秒。

## 6. 报告压缩合同

### 6.1 兼容 Component 报告的五类输出

- `.full.json`：所有解析出的生物攻击、组件条目、DamageType 链、逐组合结果、完整组件扫描清单、失败项和每个来源文件的 `path/size/mtime/sha256`；
- `.ai.json`：每个资源各自的焦点组件、最多六条排行发现、候选/返回/省略计数、对全部组合行按原因聚合的未知项、有界组件索引、失败摘要以及两个来源集合指纹；
- `.md`：人工可快速核验的重点组件表和口径说明；
- `.query.json`：只保留 API 排行需要的最佳行、revision 和 coverage，不由 AI 整份读取；
- `resource_catalog.json`：扫描到的资源类及其组件位置，用于下一轮选资源。

推荐的读取顺序是：

1. AI 先读 `.ai.json`；
2. 需要解释某一具体结论时读 `.md`；
3. 需要所有攻击、完整不兼容原因、原始路径或精度时，按需读 `.full.json`；
4. 需要找新资源时读 `resource_catalog.json`；兼容 Component API 使用 `.query.json`。

`componentIndex` 最多返回 16 项，但同时给出 `total/returned/omitted/truncated`。这不是静默删项；compact 顶层 `detailLocation` 会给出本次报告确切的同名 `.full.json` 文件名，需要剩余组件或 Top-K 之外的行时按它下钻，不再使用可能命中多份文件的通配符。同理，每个 `resourceView` 的 `rankedDiscoveryCoverage` 会告诉 AI Top-K 之外还省略了多少条。

Resource Explorer 使用另一组正式运行产物：`harvest_ranking_all_resources.full.json/.ai.json` 提供全资源 Component 事实及有界 AI 入口，`harvest_evaluation_catalog.json` 提供扩展生物攻击事实，`resource_node_catalog.json` 保存 canonical 节点、资源、图片和三层地图证据，`harvest_catalog.sqlite` 是 API 的主查询读模型。`.query.json` 继续作为兼容产物存在，但 Explorer 的节点列表、详情、过滤和恐龙强项投影不需要把它或 canonical JSON 整份载入内存。SQLite 也不应交给 AI 阅读；AI 仍应从 `.ai.json` 开始，缺什么再走有界 API 或精确下钻。

### 6.2 验证器当前会检查什么

`verify_ark_harvest_report.py` 会将 `.ai.json` 与同次生成的 `.full.json` 对比，并检查：

- full schema、`ark-harvest-compact/v2`、资源范围、生成时间、方法口径和 coverage 完全一致；
- 完整行数以及 `RANKED/INCOMPATIBLE/UNRANKED` 计数与 coverage 一致；
- 从 full 唯一重算整份 compact 视图并逐字段相等比较，而不是只检查 compact 自己选择携带的字段；
- 每个请求资源必须有独立 `resourceView`，包括焦点行、确定性 Top-K、候选状态和省略计数；
- `bestRows` 与 `resourceCandidates` 必须从 full 的全部组合行独立重算，组件 gap 聚合、扫描 manifest 的 SHA-256 和覆盖计数也从 full 重新计算；manifest 的文件路径与对象路径必须非空且各自唯一，所有组件和组合行也必须归属于该 manifest；
- `unknownSummaryScope` 必须是 `allRows`，`unknownSummary` 的状态、原因、缺失字段和示例必须与完整报告的全部组合行一致；
- `failureSummary`、有界 `componentIndex`、来源文件数量及基于 `path|sha256` 生成的集合摘要一致；来源路径不得重复且每项必须有合法 SHA-256，生物、DamageType、组件、扫描清单及其 `sourceChain` 引用的每个文件都必须在来源集合中找到；
- full 和 compact 都不能捏造 `observedYieldPerSecond`，非 `RANKED` 行也不能携带数值指数；
- `tokenEstimate` 必须与实际 compact 文件长度一致，compact 必须小于 full，且硬上限为 12,000 估算 token；
- compact 不允许缺少必需字段，也不允许在顶层或 `tokenEstimate` 等嵌套结构中加入未定义的结论字段；布尔值也不能冒充整数 token 计数。

以下是 2026-07-19 阶段一金属样例的历史验证基线，不是当前全资源报告：

| 指标 | 值 |
| --- | ---: |
| 完整行 | `135` |
| 最佳行 | `36` |
| 重点行 | `4` |
| 来源文件 | `192` |
| 扫描组件 / 语义缺口 | `166 / 59` |
| `.full.json` 字符数 | `784388` |
| `.ai.json` 字符数 | `22923` |
| AI 估算 token | `5731` |
| 字符减少 | `97.08%` |

这证明的是“合同覆盖字段在大幅压缩后仍与完整报告一致”，不是证明所有被省略字段都无损，也不是证明完整报告已经还原全部游戏运行时行为。

同一批脚本还对 Stone 和 Wood 做了跨资源实跑：Stone 为 `1010767 → 23191` 字符（减少 `97.71%`，估算 `5798` token），Wood 为 `1376173 → 25404` 字符（减少 `98.15%`，估算 `6351` token），两者均通过合同验证。Wood 压缩报告仍明确保留全部 `36` 条 `MeleeDamageType` 缺失，同时把另外 `4` 条有完整父链证据的组合正确标为 `DAMAGE_TYPE_NOT_ACCEPTED`，不会再把“已确认不兼容”混成“资产缺失”。Stone 的 Min/Max 数量覆盖缺口保留为 warning，不再错误阻断当前不使用 Min/Max 的指数。

三资源批量报告为 `2337269 → 47268` 字符（减少 `97.98%`，估算 `11817` token），Metal、Stone、Wood 分别保留 `36/56/72` 条 canonical 候选及各自的独立视图。第一次生成曾达到 `14593` token，并被 12,000 上限正确拒绝；改成有界组件索引和每资源 Top-K 后才通过。

当前验证器不重新读取并哈希现场 DevKit 文件；它验证两份报告彼此一致，并验证 full 内完整扫描清单与 compact 指纹一致。如果 DevKit 更新，仍必须重新运行排行脚本，才能取得新的现场 SHA-256。当前全资源/Foliage Explorer 的生成结果和边界见 `ARK_RESOURCE_NODE_EXPLORER_MVP_zh.md`。

## 7. 运行与验证

以下命令从项目根目录执行：

```powershell
Set-Location -LiteralPath '<PROJECT_ROOT>'
```

### 7.1 生成默认金属报告

不传 `--creature` 时会使用 Magmasaur、Ankylosaurus、Doedicurus 和 Therizinosaurus 四个代表生物。

```powershell
runtime\python\python.exe scripts\rank_ark_harvest.py `
  --resource Metal `
  --output-dir analysis\harvest_rankings
```

`Metal` 会规范化为 `PrimalItemResource_Metal_C`。也可以直接传完整资源类名或对象路径。

要生成全资源、节点清单、三层地图证据、缩略图和 SQLite，使用统一自动化入口：

```powershell
runtime\python\python.exe scripts\build_ark_harvest_explorer.py
```

编排器实际执行八个子命令：基础全资源 Component 报告、preliminary 节点与精确 Component manifest、带 manifest 的最终 Component 报告、扩展生物 evaluation、最终节点/地图/图片目录、SQLite、128 目标独立排行复算、full/AI 合同验证。所有输出先写入 staging；revision、SQLite 源 SHA-256、Repository smoke 和排行验证全部通过后才原子替换正式文件，失败时保留旧版。完整阶段表和构建安全边界见 [`ARK_RESOURCE_NODE_EXPLORER_MVP_zh.md`](ARK_RESOURCE_NODE_EXPLORER_MVP_zh.md)。

### 7.2 验证压缩报告

```powershell
runtime\python\python.exe scripts\verify_ark_harvest_report.py `
  --full analysis\harvest_rankings\harvest_ranking_metal.full.json `
  --ai analysis\harvest_rankings\harvest_ranking_metal.ai.json
```

验证成功时返回码为 `0` 且输出 `"valid": true`；合同不一致时返回码为 `1` 并列出错误。

### 7.3 一次比较多个资源

`--resource` 可以重复：

```powershell
runtime\python\python.exe scripts\rank_ark_harvest.py `
  --resource Metal `
  --resource Stone `
  --resource Wood `
  --output-dir analysis\harvest_rankings
```

多资源报告仍然按“资源 + 组件对象路径 + 生物对象路径”分别选最佳攻击，每个资源独立保留候选、焦点和 Top-K；不应跨组件直接比较指数。

### 7.4 限定组件进行诊断

```powershell
runtime\python\python.exe scripts\rank_ark_harvest.py `
  --resource Metal `
  --component MetalHarvestComponent `
  --output-dir analysis\harvest_rankings\metal_component_check
```

`--component` 和 `--max-components` 适合快速诊断。脚本会先用全部组件建立父类索引，再过滤待评估目标，因此子组件仍能继承父组件默认值；但 `--max-components` 只是按排序截断，不代表完整或随机样本，正式全量报告仍建议不限制组件。

## 8. 扩展生物（兼容 Component 报告）

本节的 `--creature` 和 `--creature-file` 只用于定向生成 `rank_ark_harvest.py` 兼容报告。正式 Explorer 已按文件候选模式自动发现，再用祖先链证明成员关系；不需要人工维护一份“所有恐龙”清单。自动发现仍不是 Asset Registry 的完整类枚举，所以未恢复父类或攻击目录的候选会保留为缺口，而不是静默排除。

### 8.1 命令行添加

只要知道 Character Blueprint 的 `/Game/...` 对象路径，就可以重复传入 `--creature`：

```powershell
runtime\python\python.exe scripts\rank_ark_harvest.py `
  --resource Metal `
  --creature 'Magmasaur=/Game/Genesis/Dinos/Cherufe/Cherufe_Character_BP.Cherufe_Character_BP' `
  --creature 'Ankylosaurus=/Game/PrimalEarth/Dinos/Ankylo/Ankylo_Character_BP.Ankylo_Character_BP'
```

一旦显式传入 `--creature`，代表生物预设会被替换，不会自动追加。

### 8.2 使用生物清单

`--creature-file` 接受 UTF-8 JSON 数组：

```json
[
  {
    "name": "Magmasaur",
    "objectPath": "/Game/Genesis/Dinos/Cherufe/Cherufe_Character_BP.Cherufe_Character_BP"
  },
  {
    "name": "Ankylosaurus",
    "objectPath": "/Game/PrimalEarth/Dinos/Ankylo/Ankylo_Character_BP.Ankylo_Character_BP"
  }
]
```

运行：

```powershell
runtime\python\python.exe scripts\rank_ark_harvest.py `
  --resource Metal `
  --creature-file .\my_harvest_creatures.json
```

添加生物后要检查：

- `coverage.creaturesRequested` 是否等于预期；
- `coverage.creaturesLoaded` 是否相同；
- full 的 `failures.creatures` 或 compact 的 `failureSummary.creatures` 是否为空；
- `AttackInfos` 中是否至少有一个攻击恢复出 DamageType、基础伤害和攻击间隔；
- 新 DamageType 的资产、父类链和资源覆盖是否均能找到。

## 9. 扩展资源（兼容 Component 报告）

推荐流程：

1. 先运行一次完整组件扫描；
2. 在 `resource_catalog.json` 中确认资源的精确类名和对应组件；
3. 用 `--resource <简名或完整类名>` 生成单资源报告；
4. 检查 `coverage.componentsMatched`、组件失败项和状态分布；
5. 运行压缩合同验证；
6. 只在同资源、同组件内解释指数差异。

如果资源不在 catalog 中，不能立即解释为“游戏里不可采集”。它只说明当前扫描和解析范围没有恢复出该资源。正式 Explorer 会扫描全 Content `*HarvestComponent*.uasset` 并加入节点精确引用的 Component，但特殊 Actor、Buff、Inventory、原生代码或尚未识别的节点定义类仍可能形成缺口，需要先扩展发现证据，再加入排名。

## 10. DevKit 与版本边界

当前脚本按 `Projects/ShooterGame/Content` 布局工作，正式产物只绑定 `C:\Program Files\Epic Games\ARKDevkit` 中生成时的本地资产快照。目录名本身不能证明游戏产品、补丁 Build 或服务器运行条件；不要把不同 DevKit 快照、地图规则、模组或服务器倍率混在同一张排行中。

版本隔离至少应做到：

- 不同产品或 DevKit Build 使用不同的 DevKit 根和输出目录；
- 每次结果保留 `generatedAt` 及 `.full.json#sources` 中的源文件 SHA-256；
- 补丁后重新生成，不用旧报告覆盖新结论；
- 服务器倍率、模组和地图规则作为报告外部条件单独记录；
- 跨版本对比时先比较资产路径、字段结构和源指纹，再比较系数；
- 新版本若改变目录、序列化或类结构，应先适配发现和解析流程，不能仅替换路径后假定结果可靠。

建议目录示例：

```text
analysis/harvest_rankings/<product>_<build>/...
```

源文件集合指纹证明“这份报告使用了哪些具体文件”，但不会自动识别产品名称和游戏 Build；版本标签仍需由运行者明确保存。

## 11. 当前金属样例的正确结论

在当前本地 DevKit 的 `MetalHarvestComponent` 上：

- Magmasaur `Bite`：`RANKED`，比较指数约 `293.5922`；
- Ankylosaurus 尾击：`RANKED`，比较指数约 `91.2911`；
- Therizinosaurus `ClawAttack`：`INCOMPATIBLE / DAMAGE_TYPE_NOT_ACCEPTED`，父链已恢复，但该组件没有接受它；
- Doedicurus 尾击：`INCOMPATIBLE / ZERO_RESOURCE_WEIGHT`。

这个结论不能外推为“Magmasaur 在所有金属节点、所有版本和所有服务器上实际每秒产量最高”。它准确表达的是：在指定资产快照和 `MetalHarvestComponent` 上，Magmasaur 在当前已可排行的候选中指数最高；Therizinosaurus 的该攻击在这个组件上已有足够证据确认不兼容。其他攻击或组件若仍有缺口，必须继续保持未知；所有未恢复的组件与运行时因素都在 compact 报告中以计数、原因、样例、省略数和确切 `detailLocation` 保留。
