# ARK Harvest Runtime 实测协议

Ranking Contract v2 的公开实测 schema 是：

```text
schemas/harvest_runtime_observation_v2.schema.json
```

字段模板是 `examples/harvest_runtime_observation_v2.example.json`。模板故意标记为
`synthetic=true`，它不是 gold，也永远不能进入公开排行。真实 observation 保存在被
`.gitignore` 覆盖的 `analysis/harvest_rankings/runtime_observations/`。

先验证单个文件：

```powershell
.\runtime\python\python.exe scripts\validate_harvest_runtime_observation.py `
  analysis\harvest_rankings\runtime_observations\<observation>.json
```

再验证整个公开 overlay：

```powershell
.\runtime\python\python.exe scripts\validate_harvest_runtime_ranking.py `
  analysis\harvest_rankings\runtime_observations
```

## 1. Runtime profile 与环境指纹

每个 observation 必须写入非空的 `runtimeProfileId`，以及由可比环境生成的
`environmentFingerprint`。指纹算法为：取下面 12 个字段组成对象，按 JSON key 排序，
不转义 Unicode，使用紧凑分隔符和 UTF-8 编码，再计算小写 SHA-256：

- `gameBuild`
- `map`
- `sessionType`
- `HarvestAmountMultiplier`
- `otherHarvestMultipliers`
- `mods`（每个 Mod 都要记录 `id` 和 `version`）
- `creature`（至少包含 `level`、`meleePercent`、`relevantStats`）
- `buffs`
- `genes`
- `worldState`
- `nodeFreshnessContract`
- `measurementMethod`

`notes` 可以记录说明，但故意不进入指纹；不能用修改 `notes` 的方式掩盖环境变化。
同一个 `runtimeProfileId` 只能对应一个环境指纹。若 build、地图、倍率、Mod 版本、生物
状态或其他可比条件变化，应创建新的 profile；同一 profile 下出现不同指纹会 fail closed。

## 2. 固定对象与测量方法

一个 observation set 固定：

- 一个新鲜、完整生命的节点类型；
- 一个目标资源；
- 一种生物与精确 `creatureObjectPath`；
- 一个 `attackIndex`；
- 相同的生物属性、Buff、基因、装备影响和 harvesting multiplier；
- 相同的测量方法，例如库存差值。

公开 overlay 使用
`nodeId + nodeResourceId + speciesKey + creatureObjectPath + attackIndex` 精确匹配，
并要求 model/extractor/policy/node/evaluation/component 身份一致。每个 trial 必须使用新的
完整节点；不要把半残节点结果与完整节点结果混在一起。

建议逐击记录：

```json
{
  "hitIndex": 1,
  "nodeHealthBefore": 100.0,
  "nodeHealthAfter": 50.0,
  "damageShown": 50.0,
  "resourceUnitsGranted": 2,
  "notes": ""
}
```

若界面无法直接显示节点生命，明确写入 `notes`，不要填入猜测值。

## 3. Trial 数与发布层级

- 1 或 2 个真实 trial：`OBSERVED_PRELIMINARY`；默认不进入排行。
- 至少 3 个真实 trial：`OBSERVED_CONFIRMED`；可进入选中 profile 的公开 overlay。
- `synthetic=true`：`SYNTHETIC_NOT_PUBLISHABLE`；无论 trial 数量多少都不进入排行。

调用方只有显式传入 `include_preliminary=True` 才能读取 preliminary 行。这个开关适合
调试和审阅，不应被当成已确认发布。

当 `measurementMethod.randomQuantity=true` 时，建议把
`recommendedSampleCount` 设为 20 或更高，并保留每一个原始 trial。该数字只是采样建议：
系统会校验并记录它，但绝不会补造 trial，也不会仅凭建议样本数把 1 次实测升级为确认。

## 4. Profile 选择与安全发现

- 目录只有一个非 synthetic profile 时，可以自动选择。
- 目录有多个 profile 时，必须显式传入 `runtime_profile_id`；否则返回稳定错误码
  `HARVEST_RUNTIME_PROFILE_REQUIRED`。
- 指定不存在的 profile 时返回 `HARVEST_RUNTIME_PROFILE_NOT_FOUND`。
- 静态发现只需要列出 profile、不应加载任何实测行时，传入
  `allow_unselected_profiles=True`。此时 `runtimeProfileSelected=null` 且 `rows` 为空。

不同 profile 可以包含相同的精确排行对象，但绝不能把它们的 trial 或平均值混合。
同一 profile 中重复的精确对象必须合并进一个 observation set，否则 fail closed。

覆盖信息固定包含六个字段：

- `runtimeProfilesAvailable`
- `runtimeProfileSelected`
- `publishableConfirmedRows`
- `preliminaryRows`
- `syntheticExcluded`
- `profileMismatchExcluded`

## 5. 导入检查清单

复制 v2 `.example.json` 作为字段模板，然后：

1. 把 `synthetic` 改为 `false`。
2. 使用新的 `runtime://` observation set ID。
3. 选择稳定的 `runtimeProfileId`。
4. 填入全部真实环境字段，并重新计算 `environmentFingerprint`。
5. 填入精确 subject、静态数据集身份和每个真实 trial。
6. 不删除异常 trial；在 `notes` 中说明异常条件。
7. 先运行单文件验证，再运行 overlay 验证。

比较命令：

```powershell
.\runtime\python\python.exe scripts\compare_harvest_runtime_observations.py `
  <真实-observation.json> `
  --json-out <comparison.json> `
  --markdown-out <comparison.md> `
  --pretty
```

## 6. 结果判读

- `RUNTIME_DIVERGED` 是调查入口，不自动证明静态反编译错误；先检查倍率、节点生命、
  Buff/Mod、随机 entry、环境指纹和 build。
- `UNSUPPORTED_DYNAMIC_BRANCH` 表示当前模型不支持该分支，不能用 0 或空数组替代。
- confirmed 只确认该 observation set 记录的精确条件，不能外推到其他 runtime profile。
- 原始 observation 与 comparison 应一起保留；报告 claim 只引用其 `runtime://` ID。
