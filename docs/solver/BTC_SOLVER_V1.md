# BTC Solver v1

BTC Solver v1 是自然语言请求与现有 Blueprint Task Context 之间的确定性编排层：

```text
Codex semantic front-end
  -> Requirement Proposal
  -> Requirement IR
  -> generic Research Operator DAG
  -> Evidence Requirement Matrix
  -> Evidence Acquisition Plan
  -> existing Task Context when ready
```

Codex 只负责语义拆解。BTC 验证原文 span、编译固定算子、推导证据需求、检查当前证据，并保存可恢复的 `solver://` 状态。生产 Solver 不按生物、物品、Mod 或 Benchmark 名称分支。

## v1 能力

- 验证最多 8 个有界 subproblem，拒绝重叠 span 和超过阈值的未分配原文。
- 将六种 output kind 编译为固定、无环、稳定排序的通用 Operator DAG。
- 自动推导 Evidence Requirement Matrix。
- 用 `BlueprintService.list_assets` 和 `BlueprintService.health` 做有界 preflight；不直接读 SQLite。
- 将 stale、migration、missing asset、dataset、localization 或 Mod 缺口转成 Acquisition Action。
- 原子保存六份 Solver metadata，并从同一 `solverId` 恢复。
- 只有目标和 current authoritative Evidence 满足 gate 时，复用 `TaskService.create` 创建现有 Task。

## 安全边界

五个 Solver MCP 工具只写 `.blueprint-solvers/**`，或通过现有 TaskService 写 `.blueprint-tasks/**`：

```text
WRITES LOCAL SOLVER/TASK METADATA ONLY.
DOES NOT MODIFY ARK DEVKIT OR BLUEPRINT EVIDENCE.
```

v1 不执行分析 Operator，不发布或修改 Evidence，不打开 Editor，不运行 DevKit 脚本，不生成或确认 Patch Plan，不修改、编译或保存 Blueprint。

## 状态

```text
CREATED
  -> PREFLIGHT
  -> ACQUISITION_REQUIRED | READY_FOR_TASKS | BLOCKED
  -> TASKS_MATERIALIZED
```

调用方不能直接写 Evidence `READY`、confirmed facts、Operator DAG 或状态跳转。候选最高分并列时必须显式选择。

## 本地文件

每个 `solver://<32hex>` 对应 `.blueprint-solvers/<32hex>/`：

```text
requirement.json
research-plan.json
evidence-matrix.json
acquisition-plan.json
state.json
bindings.json
```

公开 payload 始终 path-free、bounded、deterministic。随机 handle 和时间不进入语义 digest。
