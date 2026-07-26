# Blueprint to Code：给 GPT Pro 的实施进度审查说明

日期：2026-07-27  
仓库：`https://github.com/pityonother/Blueprint-to-Code`  
实施分支：`codex/fix-partner-devkit-root`  
版本：`0.2.0`  
实现完成点：`741a359`（本说明文档和最终审查修复位于后续提交）

## 这份文档的用途

请 GPT Pro **只审查当前实施进度并给出下一阶段方向**。不需要接手项目、修改
代码或重复实现已经完成的内容。

希望 GPT Pro 回答：

1. 原改进计划的关键目标是否已经形成完整闭环；
2. 当前证据平台最值得优先补强的 3 个方向；
3. 哪些结论仍缺少足够证明，不能对外宣称完成；
4. 下一阶段应优先做 runtime calibration、历史报告再生成、查询体验，还是其他事项；
5. 是否发现会破坏 provenance、fail-closed 或兼容性的结构性风险。

## 已经完成的工作

### 1. Native 构建身份和 provenance

- Ghidra project 按真实 DLL SHA-256 隔离，不再跨版本静默复用。
- 正式证据绑定 DLL/PDB SHA、PE CodeView、PDB GUID + Age、PDB 加载状态、
  Ghidra/Java、language/compiler spec、recipe、生成器脚本和 Git 状态。
- PDB、project、recipe 或输出身份不一致时正式模式 fail closed。
- `-AllowHashMismatch` 只能与显式 `-Experimental` 一起使用。

主要实现：

- `scripts/blueprint_translator/native_identity.py`
- `scripts/native_analysis/native_identity.py`
- `scripts/native_analysis/Get-NativeBuildIdentity.ps1`
- `scripts/native_analysis/NativeAnalysis.Common.ps1`

### 2. 声明式 Native Analysis Recipe

- 新增 versioned recipe schema。
- loot/quality、harvest 和公开 fixture 都已迁移到声明式 recipe。
- selector、expected match count、caller/callee 深度、field、constant、vtable
  和预算由 recipe 控制。
- 统一执行入口为 `Run-NativeRecipe.ps1`。

主要实现：

- `schemas/native_analysis_recipe_v1.schema.json`
- `scripts/native_analysis/Run-NativeRecipe.ps1`
- `scripts/native_analysis/ghidra/ExportNativeRecipe.java`
- `scripts/native_analysis/recipes/`

### 3. Native Evidence Store 和有界查询

- 新增 JSON + SQLite + compact index 的统一存储。
- Native evidence 使用版本绑定的 `native://` ID。
- 支持 overview、search、function、callers、callees、field accesses、
  constants、gaps 和 Blueprint links。
- 查询返回预算、截断、omitted 数量和下一步查询建议。
- compact index 已区分“确实没有 gaps”和“存在 gaps、但明细因 token
  budget 省略”，不会再把预算裁剪误写成无 gaps。

主要实现：

- `scripts/blueprint_translator/native_evidence_store.py`
- `scripts/blueprint_translator/native_evidence_repository.py`
- `scripts/blueprint_translator/native_evidence_query.py`
- `schemas/native_evidence_set_v2.schema.json`

### 4. Blueprint、Native 和 Hybrid Evidence

- 新增 Blueprint-to-Native 显式证据边。
- resolved、unresolved、ambiguous 状态可机器读取。
- Blueprint gaps 与 Native gaps 保持来源分离。
- 未验证 Native 来源不会进入 `nativeConfirmedFacts`。
- 可生成有 token 预算的 Hybrid Context Pack，无需把整份 pseudo-C 塞给模型。

主要实现：

- `scripts/link_blueprint_native_evidence.py`
- `scripts/build_hybrid_context_pack.py`
- `scripts/blueprint_translator/hybrid_evidence.py`
- `docs/decisions/ADR-003-hybrid-evidence-graph.md`

### 5. Report Claim Manifest 和 runtime calibration

- 依赖 Ghidra 的主要历史报告已增加 machine-readable Claim Manifest。
- Claim 绑定 evidence refs、source fingerprints、assumptions、confidence、
  invalidation conditions 和 runtime validation。
- 新增通用 runtime observation schema、synthetic fixtures 和 Harvest 实测协议。
- validator 会重新计算 observation 状态；只修改 JSON 状态字段不能伪造确认。
- Synthetic observation 不能正式升级为 `RUNTIME_CALIBRATED` 或
  `RUNTIME_CONFIRMED`。

主要实现：

- `schemas/report_claim_manifest_v1.schema.json`
- `schemas/runtime_observation_set_v1.schema.json`
- `scripts/validate_report_claims.py`
- `scripts/compare_runtime_observations.py`
- `scripts/blueprint_translator/runtime_calibration.py`

### 6. 控制中心安全和模块化

- 原服务器入口继续兼容，但请求、响应、安全、作业和路由已拆分。
- 默认只绑定 loopback；remote 模式必须显式启用并提供 bearer token。
- 所有 `/api` GET/POST 校验 Host，并按场景校验 Origin；remote API 额外校验
  bearer token；mutation 再校验 session token、Content-Type 和 body size。
- job 输出有界并脱敏本地路径。
- 取消作业会终止 Windows 子进程树。
- 前端只对 API 请求附加远程 bearer token。

主要实现：

- `scripts/blueprint_server/`
- `scripts/blueprint_tool_server.py`
- `scripts/blueprint_translator/harvest_build_jobs.py`
- `src/shared/api.ts`

### 7. CI、版本、文档和授权策略

- 单一版本来源为 `VERSION`，当前 `0.2.0`。
- 新增 Python/前端/release CI 和公开 Native fixture workflow。
- 公开 C++/PDB fixture 可验证完整 Native pipeline，不包含 ARK proprietary 文件。
- README、用户指南、开发交接、Ghidra、Evidence、Claim 和 Runtime 文档已同步。
- 当前不授予开源许可证，版权由作者保留，详见 `docs/LICENSE_POLICY.md`。

## 已运行的验证

### 最新通用门禁

```text
Python unittest:        624/624 passed
Frontend build:         TypeScript + Vite passed
Node contracts:         core/API/harvest passed
npm audit high:         0 vulnerabilities
Blueprint evidence:     227/227 passed, 0 failed
git diff --check:       passed
```

该数字来自本分支提交前最后一次本地全量运行；GitHub Actions 是独立的 Linux
环境结果，应另行列出，不能用任一方替代另一方。

### Claim 门禁

```text
默认模式：
3 manifests / 10 claims / 0 errors / 6 explicit warnings

正式公开 fixture：
1 manifest / 1 claim / 0 errors
```

三份历史报告仍为 `PROVENANCE_INCOMPLETE`。默认模式保留并警告，formal
模式按设计拒绝；这不是需要用假 verified 状态消除的测试失败。

### 公开 Native fixture

```text
targets:             8/8
functions:           9
field queries:       3
vtable queries:      1
branches:            5
call edges:          4
constants:           84
field accesses:      3
vtable slots:        1
gaps:                0
trust:               VERIFIED
formal_validation:   true
```

### 真实 ARK Native 验证

正式输入：

```text
DLL SHA-256:
b0e67e1e7625dd89a30b5a1df7652a44b9b142b045f820c419b8b51bbe3d7d2a

PDB SHA-256:
5285ae571d09fde9183a491f6bdef6e10a143857dd8b7fa5f9e6755b9c01bc16

PDB GUID / age:
b63263f4-93dd-4e82-a597-81e704da2a86 / 2
```

Loot recipe：

```text
14/14 targets CONFIRMED
120 functions
691 call edges/sites
8000 constants（达到配置上限）
639 explicit gaps
trust VERIFIED
formal_validation true
```

Harvest recipe：

```text
5/5 targets CONFIRMED
100 functions
338 call edges/sites
8000 constants（达到配置上限）
238 explicit gaps
trust VERIFIED
formal_validation true
```

完整本地 stores 被正确忽略，没有推送到 GitHub。

## 当前不能宣称完成的部分

1. 没有进入实际游戏做 runtime observation 手工采样。
2. `formal_validation: true` 只证明 provenance、身份、recipe 和 schema
   通过，不等于游戏运行时已经确认。
3. Ghidra pseudo-C 不是原始 C++ 源码。
4. loot/harvest constants 达到 8000 ceiling，属于有界覆盖，不是完整程序覆盖。
5. 仍有 639/238 个显式 Native gaps，以及未支持 Blueprint 节点、字段和动态 hook。
6. 三份历史报告尚未用当前 verified recipe evidence 重新生成，因此不能通过
   全量 formal gate。
7. ARK DLL/PDB、DevKit 资产和完整 proprietary evidence 不在 GitHub 中。
8. 本机 ARK DevKit 基于 UE 5.6，未安装 Unreal MCP；本轮验证使用本仓库的
   Object Path → `.uasset/.uexp` → Evidence Store 和本地诊断链路。不能把
   standalone UE 5.8 的 MCP 经验直接套用到 ARK DevKit。
9. 工具链 fingerprint 只覆盖登记的关键文件，并不表示整个解压目录逐字节一致。
10. RVA 和 Native Evidence ID 只对当前 DLL hash 有效；DevKit 或游戏二进制
    更新后必须重新运行身份验证和相应 recipe。

## 请 GPT Pro 给出的审查结果格式

请只返回审查意见，不修改代码：

```text
1. 当前完成度判断
   - 已闭环：
   - 部分闭环：
   - 尚未闭环：

2. 最重要的风险
   - P0：
   - P1：
   - P2：

3. 建议的下一阶段方向
   - 第一优先：
   - 第二优先：
   - 第三优先：

4. 不应继续投入的低价值方向

5. 需要 Codex 下一步执行的具体任务清单
```

审查时请以仓库中的代码、测试、schema、Claim Manifest 和公开 fixture 为准；
不要把文档中的自述当成唯一证据。
