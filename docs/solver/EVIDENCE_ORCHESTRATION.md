# Evidence Orchestration v1

Solver preflight 只通过公开 `BlueprintService` 接口发现资产和检查 health，不直接访问 Evidence 数据库。

## 来源优先级

```text
CURRENT_V4_EVIDENCE
BINARY_V4_REBUILD
WC_REFLECTION
CLIPBOARD_GRAPH_CAPTURE
DEFAULTS_EXPORT / LOCALIZATION_IMPORT / DATASET_IMPORT
USER_SUPPLIED_ASSET / USER_SUPPLIED_MOD_ASSET / NATIVE_SOURCE_REFERENCE
```

`WC_REFLECTION` 复用 ARK capability ledger：目前只可作为有界 Node identity/position 来源；Pin identity 不可用，不能被 Solver 提升为 READY。

## Preflight 原则

- 候选匹配只使用 exact casefold name、exact object-path suffix、exact alias 和 token overlap。
- 每个 hint 最多 5 个候选，每个 problem 最多 20 个。
- 稳定排序不能打破语义同分；最高分并列必须 `AMBIGUOUS`。
- health 显示 READY 不等于 Task-ready；Task materialization 仍以 current FRESH authority gate 为准。
- 公开 health 无法证明的 Evidence kind 保持 `UNRESOLVED` 或 `ACQUISITION_REQUIRED`，不得猜成 READY。
- `USER_FORMULA` 是 Proposal 内已经严格验证并原样持久化的用户输入；preflight 仅据此将该 requirement 标为 `REQUIREMENT_PROPOSAL` 来源的 READY，不会执行表达式。

## Acquisition Action

Action 同时包含 `actionKind` 与 `sourceKind`。前者描述操作，例如 `SELECT_ASSET_CANDIDATE`；后者描述证据来源。两者不能混为同一枚举。

v1 只生成并保存 Action。`AVAILABLE_TO_AUTOMATE` 也不表示本轮会执行写入；没有任何 Solver 工具会发布 Evidence、运行 DevKit 脚本或导入用户文件。
