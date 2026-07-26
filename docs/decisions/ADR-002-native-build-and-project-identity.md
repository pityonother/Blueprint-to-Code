# ADR-002：原生构建与 Ghidra 工程身份

- 状态：Accepted
- 日期：2026-07-27
- 适用版本：0.2.0+

## 背景

ARK DevKit 更新会替换 `ShooterGameEditor-ShooterGame.dll` 与 PDB。固定的
Ghidra project name 可能把旧工程、当前 DLL 和新命名的导出文件混在一起。
文件名或“PDB 已加载”布尔值也不足以证明 PDB 属于当前 PE。

## 决策

1. 原生构建的主身份是 DLL 的完整 SHA-256。
2. project name 固定为 `ShooterGameNative_<binary_sha256 前 12 位>`；workspace
   固定为 `<tools-root>/ghidra-workspaces/BlueprintToCode/<binary_sha12>/`。
3. workspace 内保存不含本机路径的 project manifest。manifest 至少绑定完整 DLL
   SHA-256、PE CodeView GUID/Age、PDB SHA-256 与 PDB GUID/Age。
4. 正式证据还必须绑定 Ghidra/JDK 版本、language/compiler spec、分析配置哈希、
   recipe 与生成器脚本哈希、Git commit 和 dirty 状态。
5. `-AllowHashMismatch` 只允许对未登记的新构建进行显式实验，不允许复用另一
   DLL 哈希的工程。检测到旧固定工程时只给迁移说明，不自动删除或移动。
6. runner 在 Ghidra 运行后重新读取导出结果并与当前输入逐项比较。任何
   binary、PDB、recipe、program 或 manifest 不一致都以结构化错误和非零退出码
   fail closed。
7. 完整本地路径只可进入 ignored debug 日志；正式 evidence 与 committed
   sanitized manifest 不保存用户名、DevKit 路径或 workspace 路径。

## 正式与实验模式

正式证据要求：

```text
pdb.loaded == true
pdb.matchesBinary == true
project program hash == current DLL SHA-256
recipe target count == every expectedMatches
repository dirty == false
all provenance hashes == current inputs
```

实验输出允许保留缺口，但必须标成 `PROVENANCE_INCOMPLETE`、
`PDB_IDENTITY_NOT_VERIFIED` 或 `DIRTY_GENERATOR`；它不能进入 formal claim
或 release gate。

## 影响

- 同一 DLL 重复运行会稳定命中同一工程。
- 不同 DLL 必然落入不同目录，不会静默处理旧工程。
- DevKit 更新会使依赖旧 binary/PDB/recipe 的 claim 自动 stale。
- 旧工程与旧 v1 evidence 不会被删除；用户可按文档重新生成并迁移。

## 未选择的方案

- 固定工程名：不能隔离版本。
- 仅比较文件名、大小或 PE 时间戳：碰撞和误配风险高。
- 只验证 PDB SHA-256：不能证明 PE CodeView 指向该符号流。
- 自动迁移或删除旧 workspace：可能破坏用户尚未导出的分析工作。

