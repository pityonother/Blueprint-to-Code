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

自检会确认 Ghidra、JDK 21、DevKit DLL/PDB 均存在，并核对当前锁定的 DLL/PDB SHA-256。只想快速检查路径和 Java 时可加 `-SkipDevKitHash`。

## 一键打开

在项目根目录运行：

```powershell
.\scripts\native_analysis\Start-Ghidra.ps1
```

如果已经生成默认工程，脚本会直接打开它；否则打开 Ghidra 项目管理器。

## 导入或刷新 ShooterGame 原生证据

```powershell
.\scripts\native_analysis\Import-ShooterGameNative.ps1
```

脚本会：

1. 找到 DevKit 的 `ShooterGameEditor-ShooterGame.dll` 和同版本 PDB。
2. 核对配置中锁定的 DLL/PDB SHA-256；版本变化时默认停止。
3. 首次运行创建 Ghidra 工程，后续运行处理已有工程。
4. 让 Ghidra 从本地 PDB 仓库加载符号。
5. 只导出奖池与品质公式相关的目标函数到 `native_evidence/*.json`。

已有工程默认只重新导出证据，不重复耗时的全量分析。如需在原工程上重新运行分析器：

```powershell
.\scripts\native_analysis\Import-ShooterGameNative.ps1 -Reanalyze
```

如果 DevKit 更新导致哈希变化，先记录新版本和新哈希，再显式运行：

```powershell
.\scripts\native_analysis\Import-ShooterGameNative.ps1 -AllowHashMismatch
```

不要把 `-AllowHashMismatch` 当作日常开关；否则不同 DevKit 版本的地址和反编译结果会被混在一起。

## 证据格式

每个函数使用版本绑定的证据 ID：

```text
native://<binary-sha256>/<module>/<rva>
```

导出的 JSON 包含：

- DLL SHA-256、映像基址、语言和编译器规格
- PDB 是否实际加载
- 函数完整名、RVA、签名和符号来源
- Ghidra 反编译 C 文本或明确的失败原因

后续把原生证据与蓝图证据关联时，应保存显式边，不要把两种证据揉成一个未经区分的结论：

```text
bp://.../call/...  --calls-native-->  native://.../0x...
```

## 当前边界

- PDB 能提供符号名和大量类型信息，但不等于原始源码。
- RVA 只在同一个 DLL 哈希内稳定；DevKit 更新后必须重新分析。
- Ghidra 的伪 C 是分析产物，公式结论仍要结合调用方、参数、蓝图默认值和游戏内测试。
- 本辅助层不启动游戏、不注入进程、不修改 DevKit 文件。
