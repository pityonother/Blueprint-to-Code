# Ghidra 原生分析辅助层

## 它在本项目里负责什么

Blueprint to Code 继续负责 `.uasset` / `.uexp`、Class Defaults、Components 和蓝图图页证据。Ghidra 只补充 ARK DevKit 没有公开 C++ 源码时的原生函数证据，例如：

- `UPrimalInventoryComponent::GenerateCrateItems`
- `UPrimalInventoryComponent::GenerateCustomCrateItems`
- `APrimalStructureItemContainer_SupplyCrate::GenerateCrateItems`
- `UPrimalItem::ClampItemRating`
- `UPrimalGameData::GetItemQualityIndex`
- `UPrimalItem::OverrideItemRating`

原生结果不能替代蓝图证据，也不能自动恢复开发者原始 C++ 源码。反编译结果是基于当前 DLL、PDB、Ghidra 版本和分析设置生成的近似表示。

## 已锁定工具

版本、官方发布链接和 SHA-256 都记录在 [`scripts/native_analysis/toolchain.json`](../scripts/native_analysis/toolchain.json)：

- Ghidra 12.1.2，默认目录：`C:\Users\<用户名>\tools-projects\ghidra_12.1.2_PUBLIC`
- Eclipse Temurin JDK 21.0.11+10，默认目录：`C:\Users\<用户名>\tools-projects\jdk-21.0.11+10`
- 工具放在项目外，Ghidra 工程默认放在 `C:\Users\<用户名>\tools-projects\ghidra-workspaces\BlueprintToCode`
- 仓库不保存 Ghidra 本体、DevKit DLL/PDB、Ghidra 工程或生成的原生证据

可用环境变量覆盖默认位置：

```powershell
$env:BLUEPRINT_TO_CODE_TOOLS_ROOT = "D:\tools-projects"
$env:BLUEPRINT_TO_CODE_GHIDRA_HOME = "D:\tools-projects\ghidra_12.1.2_PUBLIC"
$env:BLUEPRINT_TO_CODE_JAVA_HOME = "D:\tools-projects\jdk-21.0.11+10"
$env:BLUEPRINT_TO_CODE_DEVKIT_ROOT = "E:\ARKDevkit"
```

这些值只在当前 PowerShell 进程中使用，脚本不会修改系统 PATH。

## 自检

```powershell
.\scripts\native_analysis\Test-NativeAnalysisSetup.ps1
```

自检会确认 Ghidra、JDK 21、DevKit DLL/PDB 均存在，计算 DLL/PDB SHA-256，
从 PE CodeView 和 PDB stream 读取 GUID/Age，并打印当前动态 project identity。
正式状态要求 PDB GUID/Age 与 PE 完全一致。只想检查工具与 identity parser、
但不要求 toolchain 中登记的 hash 时可加 `-SkipDevKitHash`；这个开关不会把
evidence 变成 `VERIFIED`。

## 一键打开

在项目根目录运行：

```powershell
.\scripts\native_analysis\Start-Ghidra.ps1
```

脚本先计算实际 DLL SHA-256，再使用：

```text
<tools-root>/ghidra-workspaces/BlueprintToCode/<binary-sha12>/
  ShooterGameNative_<binary-sha12>.gpr
  ShooterGameNative_<binary-sha12>.manifest.json
```

如果这个 hash 的工程已存在，脚本会核对 project manifest 后打开；不存在时只
打开项目管理器并显示预期目录。`-ProjectFile` 不能指向另一个 hash 的工程。
旧固定项目不会被自动移动或删除，迁移方法见
[`decisions/ADR-002-native-build-and-project-identity.md`](decisions/ADR-002-native-build-and-project-identity.md)。

## 运行声明式 recipe

Loot/quality：

```powershell
.\scripts\native_analysis\Run-NativeRecipe.ps1 `
  -Recipe .\scripts\native_analysis\recipes\ark-loot-quality.v1.json
```

Harvest native：

```powershell
.\scripts\native_analysis\Run-NativeRecipe.ps1 `
  -Recipe .\scripts\native_analysis\recipes\ark-harvest-native.v1.json
```

公开 C++ fixture：

```powershell
.\tests\native_fixture\build.ps1
.\scripts\native_analysis\Run-NativeRecipe.ps1 `
  -Recipe .\scripts\native_analysis\recipes\test-native-fixture.v1.json `
  -DllPath .\tests\native_fixture\build\blueprint_native_fixture.dll `
  -PdbPath .\tests\native_fixture\build\blueprint_native_fixture.pdb
```

Runner 按顺序：

1. 解析、hash 并验证 recipe；
2. 计算 DLL/PE CodeView/PDB identity；
3. 选择 DLL hash 隔离的 workspace/project；
4. 导入或 `-process` 当前 program，并配置 PDB analyzer；
5. 精确解析 target、caller/callee、字段、常量、分支和 vtable；
6. 由 Python 再次核对 target count 与全部 provenance；
7. 导入 Native Evidence Store 并生成 compact index；
8. 返回结构化成功摘要或非零错误。

`qualifiedName + signature` 用于重载；simple name 必须由 recipe 显式允许；
regex 只可做 experimental discovery，formal recipe 拒绝它。0 个、多于
`expectedMatches` 个或重复 target 都会失败并保留候选/拒绝原因。

## 兼容入口

```powershell
.\scripts\native_analysis\Import-ShooterGameNative.ps1
```

旧命令继续可用，内部委托给 `ark-loot-quality/v1`，不再维护另一份硬编码函数
列表。已有同 hash 工程默认用 `-process`；需要重新导入或重新分析时：

```powershell
.\scripts\native_analysis\Import-ShooterGameNative.ps1 -Reimport
.\scripts\native_analysis\Import-ShooterGameNative.ps1 -Reanalyze
```

若 DevKit 更新后 hash 尚未登记，可显式生成实验产物：

```powershell
.\scripts\native_analysis\Import-ShooterGameNative.ps1 `
  -AllowHashMismatch -Experimental
```

它仍会创建新的 hash workspace，不会复用旧 project；trust 也不会升级为
`VERIFIED`。更新 `toolchain.json` 前应先独立核对 build 来源和 PDB identity。

## 证据格式

每个函数使用版本绑定的证据 ID：

```text
native://<binary-sha256>/<module>/<rva>
```

Native Evidence v2 记录：

- DLL SHA-256、PE identity、映像基址、语言和 compiler spec；
- PDB SHA-256、GUID/Age、loaded 和 binary match；
- Ghidra/JDK/analysis options；
- recipe 与 runner/exporter/configurator hashes；
- Git commit/dirty 与生成时间；
- 每个 target 的候选、接受/拒绝原因、RVA、签名、调用、字段、常量、分支、
  vtable、decompile hash/计数和 gap。

正式 evidence 默认不含本机绝对路径。完整反编译只留在 ignored
`evidence.full.json`；AI 默认先读 compact index，再按预算查询。具体命令见
[`NATIVE_EVIDENCE_STORE_V1_SPEC_zh.md`](NATIVE_EVIDENCE_STORE_V1_SPEC_zh.md)。

把原生证据与蓝图证据关联时保存显式边：

```text
bp://.../g/.../n/... --CALLS_NATIVE--> native://.../0x...
```

解析、歧义和 stale 规则见
[`HYBRID_EVIDENCE_LINKING_zh.md`](HYBRID_EVIDENCE_LINKING_zh.md)。

## 稳定错误语义

常见非零错误包括：

```text
NATIVE_TOOL_MISSING
NATIVE_JAVA_VERSION_MISMATCH
NATIVE_BINARY_HASH_UNREGISTERED
NATIVE_PDB_HASH_MISMATCH
NATIVE_PDB_IDENTITY_MISMATCH
NATIVE_PDB_NOT_LOADED
NATIVE_PROJECT_PROGRAM_HASH_MISMATCH
NATIVE_RECIPE_SCHEMA_INVALID
NATIVE_RECIPE_SELECTOR_FORBIDDEN
NATIVE_RECIPE_TARGET_COUNT_MISMATCH
NATIVE_EXPORT_SCHEMA_INVALID
NATIVE_EVIDENCE_PROVENANCE_MISMATCH
NATIVE_TEMP_PATH_INVALID
NATIVE_TEMP_CLEANUP_FAILED
```

CLI 会打印人类可读摘要，同时把结构化 diagnostic JSON 保留在本机输出目录。

## 当前边界

- PDB 能提供符号名和大量类型信息，但不等于原始源码。
- RVA 只在同一个 DLL 哈希内稳定；DevKit 更新后必须重新分析。
- Ghidra 的伪 C 是分析产物，公式结论仍要结合调用方、参数、蓝图默认值和游戏内测试。
- 本辅助层不启动游戏、不注入进程、不修改 DevKit 文件。
