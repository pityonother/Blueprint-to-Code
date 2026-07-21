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

每个资源条目还保留 `resourceObjectPath`，并以它生成稳定的 `resourceKey`；只有旧证据确实没有完整路径时，`resourceKey` 才回退到短 class。构建器以完整 Object Path 定位物品 Blueprint，读取有效继承链中的 `DescriptiveNameBase` 或 `ItemName`，把结果写入 `displayName`。因此 GUI 的主标签是玩家名称、完整路径是筛选身份、Blueprint class 只是辅助核对和旧链接兼容信息；未知或 mod 资产无法恢复名称时才使用明确的技术名拆词回退。

GUI 有三个视图：

- **按资源点查询**：按名称、地图使用证据和资源类筛选节点；地图可选择“包含所选地图”或“当前证据仅此地图”，点击某个资源条目后惰性计算最多 10 个物种；
- **按恐龙反向查询**：选择一个物种，按“该恐龙完整节点预计产量 ÷ 同一节点资源榜首产量”排列它擅长的节点资源；
- **数据构建与验收**：启动、查看或取消一次受控全量构建，显示真实阶段和有界日志。

排行没有预先保存“所有恐龙 × 所有节点资源”的笛卡尔积。构建期保存可复算事实，查询期只计算用户选中的 Component 和资源条目；反向查询也先按独立 Component/resource/entry 组合复算，再展开到引用它们的节点。

## 2. 九阶段现状

| 阶段 | 当前状态 | 完成定义与边界 |
| --- | --- | --- |
| 1. 双样本证据门 | 已完成 | 用树木和金属岩等不同节点验证 `FoliageType → AttachedComponentClass → resource entry`，未知值不转成 0。 |
| 2. 节点目录与 SQLite | 已完成 | Canonical JSON 保存完整证据；SQLite 保存分页、搜索、地图/资源索引和详情 payload，并记录源 JSON SHA-256。二者不一致时 API 失败关闭。 |
| 3. 节点类型发现 | 已完成严格范围 | 支持 `FoliageType_InstancedStaticMesh` 和真实反例 `FoliageType_Actor`。StaticMesh 是几何，PCG 是放置依赖，`PrimalDestructibleFoliage` 是运行时破坏 Actor，不重复计为独立采集配置。仍不声称枚举了未知的全部节点定义类。 |
| 4. 恐龙发现与继承 | 已完成严格范围 | 候选扩大为 `*Character*.uasset + *Char_BP*.uasset`，逐个追踪到原生 `PrimalDinoCharacter`。这比旧 `*Character_BP*` 多覆盖 84 个已确认资产，但仍是文件名候选，不是全局类注册表证明。 |
| 5. 惰性 Top 10 与独立复算 | 已完成 | 精确到 Component/resource/entry，按 `speciesKey` 合并变体，给出 `estimatedYieldPerNode`、相对榜首百分比和证据层级；正式验收以独立实现复算有限节点逐击发放循环。 |
| 6. 地图与图片 | 已完成严格范围 | 同时索引 `.umap`、PCG_Biomes 和 World Partition `__ExternalActors__`；图片只接受 UAsset header 内可验证的长度前缀 JPEG。地图依赖闭包和运行时 Spawner 仍未完整证明。 |
| 7. API 与构建任务 | 已完成 | 提供节点、排行、恐龙、反向强项、图片、构建状态/启动/取消 API；构建任务最多一个，参数白名单，`shell=False`，日志有界。 |
| 8. GUI | 已完成 | 节点/地图/资源三联过滤、“当前证据仅此地图”边界提示、详情、Top 10、相对百分比、正反向查询、构建进度和取消均已接通真实 API。 |
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
| 资源名称 | 94 / 94 | 全部由 DevKit 有效 Class Defaults 恢复，0 个未解析 |

当前识别出的可玩地图家族为 Aberration、Extinction、Genesis、Genesis2、LostColony、Ragnarok、ScorchedEarth、TheCenter 和 TheIsland。`claimsCompleteMapUsage=false`，因为 PCG 之外的完整依赖闭包、Data Layer 组合和运行时 Spawner/脚本仍可能形成使用关系。

### 3.4 文件大小

| 文件 | 大小 |
| --- | ---: |
| `resource_node_catalog.json` | 16,280,218 bytes |
| `harvest_catalog.sqlite` | 43,089,920 bytes |
| `harvest_evaluation_catalog.json` | 5,855,927 bytes |
| `harvest_evaluation_catalog.ai.json` | 13,858 bytes |
| `harvest_ranking_all_resources.full.json` | 58,769,271 bytes |
| `harvest_ranking_all_resources.ai.json` | 33,611 bytes，估算 8,403 tokens |

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

### 4.1 “当前证据仅此地图”筛选口径

该筛选不是按资源文件目录判断，也不是只检查当前分页。服务端对每个节点执行以下判定：

1. 只保留 `mapKind=PLAYABLE_MAP_EVIDENCE` 的正式可玩地图证据；
2. 按规范化 `mapFamily` 去重，测试、工具等辅助地图证据不参与集合；
3. 去重后的地图家族集合必须恰好只有一个值；
4. 这个唯一值必须与用户选择的地图家族精确相等（不区分大小写）。

没有正式地图证据的节点和同时命中多个正式地图家族的节点都不会进入结果。由于当前 `claimsCompleteMapUsage=false`，GUI 和 API 的结论只能表述为“当前已恢复证据仅此地图”，不能表述为“全游戏绝对只属于这张地图”。

当前正式数据中，1,328 个节点里有 704 个恰好恢复出一个正式地图家族，207 个恢复出多个家族，417 个尚无正式地图家族证据。稳定验收样例为：

| 筛选组合 | 当前节点数 |
| --- | ---: |
| The Island + Metal | 37 |
| Genesis 2 + Wood | 110 |
| The Center + Charcoal | 4 |

资源类型下拉框来自服务端 facet：它应用搜索词和地图条件，但忽略当前选中的资源类型，因此用户选择 Metal 后仍可直接切换到同一地图条件下的 Wood、Stone 等其他资源。facet 和筛选按 `resourceKey`（优先完整 Object Path）区分，同一个短 class 位于两个包时会显示为两个独立选项；旧链接传短 class 时仍可匹配该 class 的全部条目。地图、匹配方式和资源类型按 AND 组合；改变地图或匹配方式时会清空旧资源选择，避免把不适用于新地图的资源身份悄悄保留下来。

### 4.2 玩家名称与 Blueprint 身份

名称不是从 `PrimalItemConsumable_*` 或 `PrimalItemResource_*` 猜出来的。正式构建会：

1. 从 `HarvestResourceEntries[].ResourceItem` 同时保留短类名和完整 `resourceObjectPath`；
2. 用完整路径定位本地 DevKit `.uasset`，避免同名类跨包冲突；非空完整路径若已经失效则明确记为失败，不静默串到另一个同名资产；
3. 沿父类链合并 Class Defaults，优先读取 `DescriptiveNameBase`，其次读取 `ItemName`；
4. 在 canonical JSON 顶层 `resourceNames` 保存名称、属性名、置信度、实际源资产和继承链；
5. 节点 JSON、SQLite facet、节点详情和排行都复用同一 `displayName`，JSON 与 SQLite 都按同一 `resourceKey` 聚合和过滤。

当前 94 个已发现资源身份全部解析成功。例如：

- `PrimalItemConsumable_JellyVenom_C` → `Bio Toxin`；
- `PrimalItemResource_Gem_BioLum_C` → `Blue Gem`；
- `PrimalItemResource_Gem_Fertile_C` 的名称从父类有效默认值继承为 `Green Gem`；
- 同名 `PrimalItemResource_CommonMushroom_C` 通过完整包路径区分，当前 Aberration 条目显示 `Aggeravic Mushroom`。

Bio Toxin 还覆盖了一个旧解析缺口：三个毒性植物节点所用 Component 把 DamageType 覆盖键保存为内联 `FName + FString` SoftObjectPath 数组。解析器现在只在完整边界匹配时接受这种布局，并保留原 4-byte 路径表格式；越界、尾随字节、非法路径或非零 FName number 都继续 `parsed=false`。这保证权重与数量覆盖可以进入新模型；旧产物中由攻速指数得出的 Dreadnoughtus 榜首结论已经失效，不能继续当作产量结论。

启动、查询和构建均不联网抓取第三方站点。Wikily 等页面只用于人工交叉核验；第三方页面结构或可用性不会影响本地 GUI。

## 5. 排行、相对百分比与条件静态估算

当前主场景是 `TAMED_RIDDEN`。以下明确负面事实会直接排除攻击：

- `bSkipTamed=true`；
- `bOnlyOnWildDinos=true`；
- `bPreventWithRider=true`；
- 生物明确不可驯服或 Boss；
- 配置要求确认可骑乘时，`bAllowRiding` 不是明确 true。

`bSkipAI=true` 只限制 AI 使用，不足以证明玩家骑乘时不能手动使用，所以不会单独排除。

两个动态标志采用不同的失败边界：

- `bUseBlueprintCanRiderAttack=true`：缺少最终蓝图资格判定，可保留带明确条件标签的静态产量估算；
- `bUseBlueprintAdjustOutputDamage=true`：缺少会直接改变逐击产量输入的运行时伤害结果，因此当前必须 `UNRANKED`。

只有前一类攻击在静态伤害、DamageType 和 Component 必需事实完整时可以出现在数值排序中，并必须带：

```text
usageEligibilityStatus = CONDITIONAL
usageEstimateBasis = STATIC_ATTACK_FACTS_WITH_BLUEPRINT_RUNTIME_RESULT_NOT_RECOVERED
rankingTier = CONDITIONAL
evidence.status = PARTIAL
```

这些标签表示“按当前静态事实的条件估算”，不是确认攻击一定可用。输出伤害会被动态调整的攻击不会得到猜测分数；只有驯服/骑乘和动态门禁证据均满足时才标为 `rankingTier=CONFIRMED`。

正向榜的 `relativeToNodeTopPercent` 为：

```text
本行 estimatedYieldPerNode / 当前节点资源全部可排行物种的榜首 estimatedYieldPerNode × 100
```

反向榜也严格按 `estimatedYieldPerNode` 从高到低排列，因此名次表示该生物在一整个完整节点上的预计资源单位数。逐节点归一化的 `relativeToNodeTopPercent` 仍保留，用于辅助回答“它在这个节点资源上离当前榜首多远”，但不再影响排名。反向榜会横跨不同资源条目比较单位数；该数字表示产量数量，不表示不同资源之间的价值高低。

`estimatedYieldPerNode` 模拟一个新鲜节点从满生命到完全耗尽的发放过程：

```text
effectiveDamagePerHit = baseDamage × DamageMultiplier
逐击按节点剩余生命、发放阈值和 HarvestQuantityMultiplier 计算整数 grant calls
estimatedYieldPerNode = totalGrantCalls
                        × normalizedResourceWeight
                        × expectedQuantityPerSelection
```

它表示标准化静态 profile 下的“预计目标资源单位/完整节点”，不是实测每击产量或资源/秒。模型已经包含节点生命、最终一击上限、整数发放阈值、线性数量期望和舍入；服务器倍率、运行时近战属性、动画墙钟周期、一次攻击命中节点数、动态 Blueprint/Buff/基因/任务 hook 仍不包含。非线性随机、单单位采集和非零 additional-effectiveness 分支当前失败关闭。

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
- `resource_node_catalog.json` 是节点证据的 canonical JSON；顶层 `resourceNames` 保存玩家名称的 DevKit 属性、完整资产路径、继承链和解析覆盖率；
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
GET /api/harvest/nodes?q=&map=&onlyMapFamily=&resource=&offset=&limit=
GET /api/harvest/nodes/{nodeId}
GET /api/harvest/rankings?nodeId=&nodeResourceId=&limit=10
GET /api/harvest/creatures?q=&offset=&limit=
GET /api/harvest/creatures/{speciesKey}/specialties?offset=&limit=
GET /api/harvest/images/{sha256}.jpg
```

`map` 保留原有的地图名称/路径包含匹配语义。新增的 `onlyMapFamily` 是精确、不区分大小写的证据集合筛选；两者都存在时按 AND 组合。一般 GUI 请求只会发送其中一个。例如：

```text
GET /api/harvest/nodes?onlyMapFamily=TheIsland&resource=%2FGame%2FPrimalEarth%2FCoreBlueprints%2FResources%2FPrimalItemResource_Metal.PrimalItemResource_Metal_C&limit=16
```

`resource` 推荐传 URL-encoded `resourceKey`；为兼容旧链接，也接受短 class，并匹配该 class 的全部路径。节点分页响应的 `appliedFilters` 回显规范化后的条件，`facets.onlyMapFamilies` 给出各“当前证据仅此地图”的节点数，`facets.resources` 给出当前搜索/地图范围内每个精确资源身份对应的去重节点数；`facets.mapExclusivity.isGlobalExclusivityClaim` 固定为 `false`，防止调用方把证据范围误读成全局绝对结论。

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
5. 从精确资源 Object Path 恢复玩家名称，并构建最终节点、三层地图和图片目录；
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
- 条件攻击仍可能缺少蓝图运行时资格门禁；动态伤害调整攻击已失败关闭；
- 完整节点预计产量没有校准服务器倍率与受控实测，且不是采集速度指标。

因此正确表述是：系统能在当前 DevKit、当前已恢复证据和明确场景中，对精确节点资源给出可复算的确认/条件排序；它不能声称是所有版本、模组、地图运行时和服务器配置下的最终实测产量榜。
