# ADR-001：资源点地图证据与惰性排行架构

## 状态

Accepted

## 日期

2026-07-21

## 背景

ARK DevKit 的资源采集不是“资源类直接对应一个地图”。实际关系是资源点定义绑定 `HarvestComponent`，该组件再定义一个或多个资源条目；同一 Wood、Metal 等资源类可以出现在很多不同节点中。

UE5/ASA 的地图使用关系也不只存在于 `.umap`：PCG Biome 资产与 World Partition `__ExternalActors__` 都可能引用资源点。资产位于 `/Game/Genesis` 或 `/Game/PrimalEarth` 只表示包的来源命名空间，不证明它在哪张地图被使用。旧实现把可见的直接 `.umap` 引用当成地图全集，结果大量节点看起来只属于 Genesis 1/2。

同时，预先写出“全部恐龙 × 全部节点资源”的组合会产生巨大的报告，既浪费磁盘和 token，也使源事实、缺失信息和计算结果难以分层验证。

## 决策

1. 资源点身份以可验证的节点定义资产为准。目前支持精确属性标签可解码的 `FoliageType_InstancedStaticMesh` 与 `FoliageType_Actor`。`StaticMesh` 是几何资源，PCG 是放置依赖，`PrimalDestructibleFoliage` 是运行时破坏模型；没有独立 `HarvestComponent` 证据时不得冒充新的节点定义。
2. `assetOrigin.packageNamespace` 与 `mapUsage` 永久分离。前者只说明资产包来源，后者只由地图使用证据生成。
3. 地图证据分三层保存：
   - `.umap` 中的精确序列化包引用；
   - PCG Biome 对节点包的精确依赖；
   - World Partition 外部 Actor 中的精确包引用，并按世界聚合证据次数和示例。
4. PCG 与 World Partition 证据证明依赖关系，不证明具体生成坐标。Spawner、Data Layer 和未闭合的间接依赖仍可能存在，因此 `claimsCompleteMapUsage=false` 必须保持，直到有新的完整性证据。
5. 构建期保存节点、组件、生物、攻击、DamageType、适用范围和缺口事实；查询期才对一个精确 `nodeResourceId` 计算物种 Top 10，或对一个 `speciesKey` 反向计算擅长节点资源。不得持久化全量笛卡尔积排行。
6. JSON 是可移交、可审计的权威交换产物；SQLite 是由同一 JSON 哈希绑定生成的只读查询伴随索引。API 优先使用 SQLite，但绝不能把 SQLite 当成独立来源。
7. 完整构建必须在暂存目录执行，经过 revision、schema、大小、SQLite 源哈希、独立公式复算和报告压缩门禁后，再作为一个可回滚的文件集合发布。

## 被否决的方案

### 用资产目录推断地图

否决原因：`/Game/Genesis2/...` 只能说明资产来源；PrimalEarth 节点也会被 Genesis、The Island、The Center 等地图复用。该方案会把来源和使用混成一个结论。

### 只扫描 `.umap`

否决原因：真实反例 `SM_MetalRock_01_Settings` 在 The Island 的使用主要存在于 World Partition 外部 Actor 中；UmbrellaTree 也同时通过外部 Actor 与 PCG 恢复出非 Genesis 地图证据。

### 预计算所有恐龙和节点资源组合

否决原因：组合规模大、重复值多、报告 token 成本高，并且一次 DevKit 更新会使整个组合文件失效。惰性计算能保留相同源事实并把响应限制在用户正在看的查询上。

### 把未知或不兼容当作 0

否决原因：0 是数值结论，缺失证据不是 0。未知、明确不兼容、明确禁用和运行时条件必须分别保存和展示。

## 后果

- 地图筛选能显示多层真实证据，但不会声称已恢复每一个运行时生成位置。
- 节点与恐龙发现仍使用宽文件名候选再做类/祖先验证，所以 `claimsAllNodes=false`、`claimsAllCreatures=false` 保持诚实。
- API 可以在不解析整份节点 JSON 的情况下分页查询；AI 也可以先读小型 coverage/AI 摘要，再按节点、资源或物种取证。
- DevKit 更新、解析器版本变化或源文件指纹变化会使相应缓存失效；缓存只用于性能，不是可信结论。
- 新增节点定义类、地图证据层或条件排行口径时，必须先增加真实反例测试，并同步独立验证器与本 ADR 的后续 superseding 决策。
