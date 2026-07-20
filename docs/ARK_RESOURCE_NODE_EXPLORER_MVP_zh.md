# ARK 资源点采集查询：九阶段完成版

## 1. 系统现在回答什么

Harvest Explorer 的查询单位是“资源点定义中的精确资源条目”，不是脱离节点的 Wood、Metal 等资源名：

```text
FoliageType_InstancedStaticMesh / FoliageType_Actor
  → Mesh 或 ActorClass
  → AttachedComponentClass
  → 精确 HarvestComponent Package Path
  → HarvestResourceEntries[entryIndex]
  → 当前节点资源的恐龙 Top 10
```

同一个资源类可能出现在多个 HarvestComponent、多个条目和多个节点中。`nodeResourceId` 因此同时绑定节点、Component Package Path、条目序号和资源类，查询不会把不同节点里的同名资源混成一张榜。

GUI 有三个视图：

- **按资源点查询**：按名称、地图使用证据和资源类筛选节点，点击某个资源条目后惰性计算最多 10 个物种；
- **按恐龙反向查询**：选择一个物种，按“该恐龙指数 ÷ 同一节点资源榜首指数”排列它擅长的节点资源；
- **数据构建与验收**：启动、查看或取消一次受控全量构建，显示真实阶段和有界日志。

排行没有预先保存“所有恐龙 × 所有节点资源”的笛卡尔积。构建期保存可复算事实，查询期只计算用户选中的 Component 和资源条目；反向查询也先按独立 Component/resource/entry 组合复算，再展开到引用它们的节点。

## 2. 九阶段现状

| 阶段 | 当前状态 | 完成定义与边界 |
| --- | --- | --- |
| 1. 双样本证据门 | 已完成 | 用树木和金属岩等不同节点验证 `FoliageType → AttachedComponentClass → resource entry`，未知值不转成 0。 |
| 2. 节点目录与 SQLite | 已完成 | Canonical JSON 保存完整证据；SQLite 保存分页、搜索、地图/资源索引和详情 payload，并记录源 JSON SHA-256。二者不一致时 API 失败关闭。 |
| 3. 节点类型发现 | 已完成严格范围 | 支持 `FoliageType_InstancedStaticMesh` 和真实反例 `FoliageType_Actor`。StaticMesh 是几何，PCG 是放置依赖，`PrimalDestructibleFoliage` 是运行时破坏 Actor，不重复计为独立采集配置。仍不声称枚举了未知的全部节点定义类。 |
| 4. 恐龙发现与继承 | 已完成严格范围 | 候选扩大为 `*Character*.uasset + *Char_BP*.uasset`，逐个追踪到原生 `PrimalDinoCharacter`。这比旧 `*Character_BP*` 多覆盖 84 个已确认资产，但仍是文件名候选，不是全局类注册表证明。 |
| 5. 惰性 Top 10 与独立复算 | 已完成 | 精确到 Component/resource/entry，按 `speciesKey` 合并变体，给出绝对指数、相对榜首百分比和证据层级；正式验收使用独立公式实现对 128 个确定性样本黑盒复算，当前产物为 0 mismatch。 |
| 6. 地图与图片 | 已完成严格范围 | 同时索引 `.umap`、PCG_Biomes 和 World Partition `__ExternalActors__`；图片只接受 UAsset header 内可验证的长度前缀 JPEG。地图依赖闭包和运行时 Spawner 仍未完整证明。 |
| 7. API 与构建任务 | 已完成 | 提供节点、排行、恐龙、反向强项、图片、构建状态/启动/取消 API；构建任务最多一个，参数白名单，`shell=False`，日志有界。 |
| 8. GUI | 已完成 | 节点/地图/资源过滤、详情、Top 10、相对百分比、正反向查询、构建进度和取消均已接通真实 API。 |
| 9. 增量构建与原子验收 | 已完成 | 节点、生物和地图扫描缓存；临时 staging 全套构建；报告合同、revision、SQLite、Repository smoke 和独立排行复算通过后才原子替换正式产物，失败回滚旧版。 |

“已完成严格范围”表示该阶段的已声明范围有自动化和证据支撑，不表示 `claimsAll*` 可以变成 true。

## 3. 当前正式产物快照

以下数字来自 2026-07-21 当前正式 `analysis/harvest_*` 产物，而不是旧阶段报告。

### 3.1 资源节点

| 项目 | 当前值 |
| --- | ---: |
| 文件名候选 | 1,985 |
| 已解码节点 | 1,328 |
| `FOLIAGE` | 1,327 |
| `FOLIAGE_ACTOR` | 1 |
| 节点解析失败 | 0 |
| 装饰性 Foliage，正确跳过 | 608 |
| 非 Foliage 候选，正确跳过 | 49 |
| 有确认 HarvestComponent 的节点 | 1,328 |
| 节点资源条目 | 9,100 |
| 缺少 Component 源证明的节点 | 7 |
| 可用缩略图 | 1,327 / 1,328 |

新增的真实 Actor 型节点是 `FA_LumaA_02`。它从 `ActorClass` 和 `AttachedComponentClass` 恢复出 `BP_LumaA_02`、`LumaA02HarvestComponent` 以及 Wood、Thatch、RareMushroom 三个资源条目。它不是把普通 Actor 名称猜成资源点，而是确认包内导出类为 `FoliageType_Actor` 后才进入目录。

### 3.2 生物与攻击

| 项目 | 当前值 |
| --- | ---: |
| `*Character* + *Char_BP*` 候选 | 2,088 |
| 旧 `*Character_BP*` 规则外候选 | 666 |
| 旧规则外、祖先链确认进入目录 | 84 |
| 确认继承 `PrimalDinoCharacter` | 1,406 |
| 物种分组 | 280 |
| 已解码攻击 | 3,586 |
| 明确适用于 `TAMED_RIDDEN` 的攻击 | 1,541 |
| 需要运行时蓝图结论的条件攻击 | 1,712 |
| 明确不适用的攻击 | 333 |
| 可骑乘 / 不可骑乘 / 未恢复 | 993 / 54 / 359 |

`claimsAllCreatures=false` 仍然正确：69 个候选没有恢复父类，4 个父资产未找到，93 个已确认生物资产没有恢复攻击目录，另有驯服性、骑乘性、动态攻击门禁、DamageType 和 Component 缺口。

### 3.3 地图证据

| 层级 | 扫描量 | 当前关系数/命中 |
| --- | ---: | ---: |
| `.umap` 直接包引用 | 1,490 | 5,896 条节点关系 |
| PCG_Biomes 依赖 | 154 | 1,416 条节点关系 |
| World Partition External Actors | 197,363 | 5,846 个候选文件被精确解析，2,191 条聚合关系，命中 536 个节点 |
| 三层合计 | 199,007 | 911 个节点具有至少一个地图家族使用证据 |

当前识别出的可玩地图家族为 Aberration、Extinction、Genesis、Genesis2、LostColony、Ragnarok、ScorchedEarth、TheCenter 和 TheIsland。`claimsCompleteMapUsage=false`，因为 PCG 之外的完整依赖闭包、Data Layer 组合和运行时 Spawner/脚本仍可能形成使用关系。

### 3.4 文件大小

| 文件 | 大小 |
| --- | ---: |
| `resource_node_catalog.json` | 13,543,126 bytes |
| `harvest_catalog.sqlite` | 30,666,752 bytes |
| `harvest_evaluation_catalog.json` | 5,661,085 bytes |
| `harvest_evaluation_catalog.ai.json` | 13,858 bytes |
| `harvest_ranking_all_resources.full.json` | 59,195,097 bytes |
| `harvest_ranking_all_resources.ai.json` | 36,652 bytes，估算 9,163 tokens |

SQLite 较 JSON 大并不表示它更适合交给 AI；它是本地 API 的索引读模型。AI 应先读有界 `.ai.json`，然后调用有界查询，而不是加载 SQLite 或完整 JSON。

## 4. 地图证据为什么分三层

旧实现只扫描 `.umap` 的精确 Package token。ASA/UE5 的真实地图还大量使用 PCG_Biomes 和 World Partition External Actor `.uasset`，所以很多 PrimalEarth 节点曾只显示 Genesis/Genesis2，或者完全没有地图。问题不是这些节点只属于创世纪，而是证据扫描少了两层。

现在每条地图关系保留来源类型：

- `DIRECT_PACKAGE_REFERENCE`：某个 `.umap` 直接序列化了节点 Package Path；
- `PCG_BIOME_REFERENCE`：某地图家族的 PCG biome 资产依赖该节点；
- `WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE`：该世界的 External Actor 包引用节点，多个 Actor 文件按世界聚合并保留计数和有界样例。

这三类证据都能证明“地图资产链中引用了节点”，但都不是已恢复的生成坐标，因此 `usageStatus` 保持候选语义。External Actor 快速路径先用 ASCII/UTF-16 搜索筛候选，再用同一精确 Unreal Package token 解析器复核，避免 `/Game/X` 错配 `/Game/X_2`。

两个当前反例说明修复效果：

- `SM_MetalRock_01_Settings` 的 `assetOrigin.packageNamespace` 是 `PrimalEarth`，但 `mapUsage` 现在有 Genesis、Genesis2、Ragnarok、ScorchedEarth、TheCenter、TheIsland 六个家族；
- `UmbrellaTree_SM_settings` 的 origin 同样是 `PrimalEarth`，地图使用证据来自 Ragnarok PCG 和 TheIsland World Partition。

`assetOrigin` 与 `mapUsage` 必须分开：

- `assetOrigin.packageNamespace` 只说明资源文件存放在哪个 `/Game/<namespace>`；
- `mapUsage.families` 来自地图依赖证据；
- 不能因为资产位于 `Genesis2` 就自动断言只在 Genesis2 使用，也不能因为某层没有找到引用就断言该地图绝对不用它。

## 5. 排行、相对百分比与条件静态估算

当前主场景是 `TAMED_RIDDEN`。以下明确负面事实会直接排除攻击：

- `bSkipTamed=true`；
- `bOnlyOnWildDinos=true`；
- `bPreventWithRider=true`；
- 生物明确不可驯服或 Boss；
- 配置要求确认可骑乘时，`bAllowRiding` 不是明确 true。

`bSkipAI=true` 只限制 AI 使用，不足以证明玩家骑乘时不能手动使用，所以不会单独排除。

两个动态标志采用“条件静态估算”而不是静默排除或假定成功：

- `bUseBlueprintCanRiderAttack=true`：缺少最终蓝图资格判定；
- `bUseBlueprintAdjustOutputDamage=true`：缺少运行时伤害调整结果。

如果静态攻击数值、DamageType 和 Component 系数足以复算，这些攻击可以出现在数值排序中，但必须带：

```text
usageEligibilityStatus = CONDITIONAL
usageEstimateBasis = STATIC_ATTACK_FACTS_WITH_BLUEPRINT_RUNTIME_RESULT_NOT_RECOVERED
rankingTier = CONDITIONAL
evidence.status = PARTIAL
```

它们表示“按当前静态事实的条件估算”，不是确认攻击一定可用，也不是确认运行时伤害与静态值相同。只有驯服/骑乘和动态门禁证据均满足时才标为 `rankingTier=CONFIRMED`。

正向榜的 `relativeToNodeTopPercent` 为：

```text
本行 engineComparisonIndex / 当前节点资源全部可排行物种的榜首指数 × 100
```

反向榜使用同一分母逐节点归一化，因此可以回答“这只恐龙在哪些节点资源上接近或达到当前榜首”。它不是把不同 HarvestComponent 的绝对指数直接混排。

`engineComparisonIndex` 仍是引擎系数比较指数：

```text
baseDamage / attackInterval
× DamageMultiplier
× HarvestQuantityMultiplier
× normalizedResourceWeight
```

它不是实测每击产量、最终掉落倍率或资源/秒，也不包含服务器倍率、运行时近战属性、动画墙钟周期、实际命中数量、节点剩余生命、随机与舍入、以及未恢复的蓝图逻辑。

## 6. SQLite 和 token 分层

正式产物分工如下：

```text
analysis/harvest_rankings/harvest_ranking_all_resources.full.json
analysis/harvest_rankings/harvest_ranking_all_resources.ai.json
analysis/harvest_rankings/harvest_ranking_all_resources.query.json
analysis/harvest_rankings/harvest_evaluation_catalog.json
analysis/harvest_rankings/harvest_evaluation_catalog.ai.json
analysis/harvest_rankings/harvest_ranking_independent_verification.json
analysis/harvest_nodes/resource_node_catalog.json
analysis/harvest_nodes/harvest_catalog.sqlite
analysis/harvest_nodes/images/<sha256>.jpg
```

- `full.json` 是 Component/资源系数的完整验证证据；默认四个代表生物只是该兼容报告的行范围，不是 Explorer 的全生物来源；
- `harvest_evaluation_catalog.json` 保存 1,406 个生物资产及攻击、DamageType、Component 事实，没有 `rows` / `bestRows` 笛卡尔积；
- 两个 `.ai.json` 保存覆盖、方法、缺口、计数和下钻入口，供 AI 首读；
- `resource_node_catalog.json` 是节点证据的 canonical JSON；
- `harvest_catalog.sqlite` 是由 canonical JSON 原子生成的只读索引，节点列表、详情、过滤和反向查询只投影必要字段；它记录源 JSON SHA-256，revision 或内容不匹配时拒绝读取；
- `harvest_ranking_independent_verification.json` 记录独立公式复算的选择范围、输入 SHA-256 和 mismatch。

节点列表上限为 16，恐龙和反向结果也分页返回。GUI 不需要把 13.5 MB 节点 JSON 或 59 MB full 报告送进浏览器或 AI 上下文。

## 7. GUI 与 API

启动本地控制中心：

```powershell
npm run control
```

打开：

```text
http://127.0.0.1:8765/?view=harvest
```

只读查询 API：

```text
GET /api/harvest/nodes?q=&map=&resource=&offset=&limit=
GET /api/harvest/nodes/{nodeId}
GET /api/harvest/rankings?nodeId=&nodeResourceId=&limit=10
GET /api/harvest/creatures?q=&offset=&limit=
GET /api/harvest/creatures/{speciesKey}/specialties?offset=&limit=
GET /api/harvest/images/{sha256}.jpg
```

构建任务 API：

```text
GET  /api/harvest/build
POST /api/harvest/build
POST /api/harvest/build/{jobId}/cancel
```

公开构建接口只接受 `Content-Type: application/json`、同源 `Origin`（本机工具可不带 `Origin`）和精确请求体 `{"options":{}}`；非空参数、额外字段、跨源请求与非 loopback Host 均会被拒绝。任务管理器仍对内部参数做类型白名单，并把六个输出角色锁到各自的正式路径，不能把 catalog 或 cache 指向 README、`.git` 或另一类正式产物。同时只能有一个 `QUEUED/RUNNING` 任务。

取消不是只终止协调器：Windows 使用独立进程组和 Job Object 管理整棵阶段子进程树，POSIX 使用独立 session/process group；优雅取消失败时才强制结束整组。正式文件 promotion 是不可强杀的短临界区：若取消在提交前生效，旧版保持完整；若整套新数据已经提交，任务返回 `SUCCEEDED`，并以 `cancelRequested=true`、`cancellationDeferred=true`、`cancelTooLate=true` 明确表示取消来得太晚，不能伪报成未发生更新的 `CANCELLED`。

## 8. 全量构建和验收

命令行入口：

```powershell
runtime\python\python.exe scripts\build_ark_harvest_explorer.py
```

查看八个实际子命令而不构建：

```powershell
runtime\python\python.exe scripts\build_ark_harvest_explorer.py --dry-run
```

一次正式构建依次执行：

1. 全资源 Component 基线扫描；
2. 快速节点遍历，生成实际引用 Component 清单；
3. 带清单重建 Component 全量报告；
4. 构建扩展生物 evaluation 目录；
5. 构建最终节点、三层地图和图片目录；
6. 从 canonical 节点 JSON 生成 SQLite；
7. 用独立实现对 128 个确定性节点资源目标复算并黑盒对比；
8. 验证 full/AI 压缩合同。

八个命令全在 staging 目录运行。随后协调器还会检查：

- full、AI、query、evaluation schema 和 revision 一致；
- evaluation 不含笛卡尔积字段且小于 8 MiB；
- 独立复算实际比较了目标且 mismatch 为 0；
- SQLite 能以只读方式打开，并与 canonical JSON SHA-256 匹配；
- Repository 能分页列节点、读取详情和完成至少一个真实排行；
- SQLite 与 Repository 的节点总数一致。

只有以上全部成功才按固定顺序逐文件原子替换正式文件。协调器在 promotion 前输出临界区标记并暂存终止信号；替换中失败会从同目录备份回滚，完成提交后才恢复取消处理。这个文件集合不是一次文件系统事务：极短切换窗口内，Repository 会用 canonical JSON SHA-256、SQLite 源绑定和 node/evaluation revision 检查拒绝跨版本组合，因此查询最多暂时不可用，不会把混合 revision 当成有效结果返回。

## 9. 缓存与可信度边界

- 节点和生物缓存按解析器版本、路径、大小、`mtime_ns` 与内容 SHA-256 失效；
- `.umap` 缓存只用于性能，按大小和 `mtime_ns` 失效，并明确记录 `contentSha256Verified=false`；最终关系仍由精确 token 解析产生，缓存本身不是结论证据；
- World Partition 快速筛选不会直接产出结论，候选文件必须再经过权威 token 解析；
- 缓存命中不会绕过 staging、revision、SQLite 源绑定和独立排行验收。

当前必须继续显示：

```text
claimsAllNodes = false
claimsAllNodeDefinitionClasses = false
claimsAllCreatures = false
claimsAllDiscoveredCandidates = false
claimsCompleteMapUsage = false
claimsGlobalTop = false
```

主要剩余缺口：

- 节点类发现仍是已知 Foliage 定义与文件名候选，不是完整 Asset Registry 类枚举；
- 生物发现仍是两个文件名模式，部分父类、攻击目录、驯服/骑乘状态未恢复；
- 地图使用还没有证明 PCG 之外的完整依赖闭包、运行时 Spawner、脚本和精确坐标；
- 条件攻击缺少蓝图运行时门禁与伤害调整结果；
- 指数没有校准真实动画、服务器倍率和实测掉落。

因此正确表述是：系统能在当前 DevKit、当前已恢复证据和明确场景中，对精确节点资源给出可复算的确认/条件排序；它不能声称是所有版本、模组、地图运行时和服务器配置下的最终实测产量榜。
