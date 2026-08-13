# Requirement IR v1

`blueprint-to-code.requirement-proposal/v1` 是 Codex 可提交的唯一语义输入。Codex 不能提交 Operator DAG，也不能声明 Evidence 已就绪。

## 原文绑定

每个 subproblem 必须满足：

```text
rawRequest[sourceStart:sourceEnd] == sourceText
```

Span 按 `sourceStart` 排序、不可重叠。编译器计算 `unassignedText`；超过 20 个有效字符时以 `REQUEST_TEXT_UNASSIGNED` fail closed。

## 有界语义

- Intent：`ANSWER_CURRENT_BEHAVIOR`、`DESIGN_BLUEPRINT_CHANGE`
- Output kind：`FORMULA`、`COMPLETE_ENUMERATION`、`INFLUENCE_FACTORS`、`RANKING`、`CURRENT_BEHAVIOR`、`BLUEPRINT_CHANGE`
- Completeness：`REQUIRED`、`BEST_EFFORT`
- Constraints：仅接受版本化合同中的 typed 字段；拒绝任意扩展对象。

`RANKING` 必须提供 1..100 的 `topK` 和用户公式。`DESIGN_BLUEPRINT_CHANGE` 必须提供 desired behavior、invariants 和 acceptance tests。

## 确定性

相同 `rawRequest + proposal + compilerVersion` 生成相同 problem ID、operator ID、requirement ID、DAG 和 semantic digest。`solverId` 与时间戳不影响 digest。

## 固定 DAG

每种 output kind 使用版本化 Operator Registry 的固定 DAG。v1 只有 `DISCOVER_TARGETS` 与 `CHECK_EVIDENCE_COVERAGE` 被 preflight 实际执行；分析算子诚实保持 `NOT_IMPLEMENTED`，并由 Evidence 或未来 executor 决定下一阶段。
