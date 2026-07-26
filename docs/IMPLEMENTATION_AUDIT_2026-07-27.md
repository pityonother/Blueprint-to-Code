# Blueprint-to-Code 改造前审计（2026-07-27）

本记录对应全面改进实施开始前的可复现基线。它只记录仓库状态和实际执行结果，不把后续实现结果倒填为基线。

## Git 与 PR

- 分支：`codex/fix-partner-devkit-root`
- HEAD：`31ded95a56a31bfb58a0ce960dcc6cdbe98b4813`
- 工作树：干净
- `git diff --check`：通过
- 相对本地 `main`：前进 11 个提交、没有落后提交
- 相对 `origin/main`：前进 12 个提交、没有落后提交
- GitHub PR：`#2 Add ARK harvest analysis, native evidence tooling, and reports`
- PR base/head：`main` ← `codex/fix-partner-devkit-root`
- GitHub merge state：`CLEAN`

## 基线命令

### Python

```powershell
.\runtime\python\python.exe -m unittest discover -s tests -p "test_*.py"
```

实际结果：

```text
Ran 493 tests in 30.474s
OK
```

### 前端

```powershell
npm run build
```

实际结果：TypeScript 与 Vite build 通过，Vite 变换 9 个模块。

### 可复现安装

```powershell
npm ci
```

改造前失败：

```text
Missing: @emnapi/core@1.11.3 from lock file
Missing: @emnapi/wasi-threads@1.2.3 from lock file
```

这说明 `package.json` 与 `package-lock.json` 的可复现安装门禁在改造前已漂移。后续应独立修复锁文件并重新执行 `npm ci`。

### Ghidra / DevKit

```powershell
.\scripts\native_analysis\Test-NativeAnalysisSetup.ps1
```

实际结果：

- Ghidra `12.1.2`：可用；
- JDK `21.0.11`：可用；
- ARK DevKit DLL/PDB：可用；
- DLL SHA-256：`b0e67e1e7625dd89a30b5a1df7652a44b9b142b045f820c419b8b51bbe3d7d2a`；
- PDB SHA-256：`5285ae571d09fde9183a491f6bdef6e10a143857dd8b7fa5f9e6755b9c01bc16`。

此检查只证明文件存在且 SHA 与 `toolchain.json` 的登记值一致；改造前没有证明 PE CodeView GUID/Age 与 PDB identity 一致。

## 改造前结构审计

### Native / Ghidra

- `scripts/native_analysis/toolchain.json` 使用固定 project name `ShooterGameNative_B0E67E1E`。
- `-AllowHashMismatch` 会跳过 DLL 与 PDB pin，但仍可能复用固定 project。
- 导出完成后只检查文件存在并打印 `matchCount`/`pdbLoaded`；没有逐项核对 program hash、PDB identity、recipe 或 generator hash。
- 当前没有 versioned recipe schema、recipe 目录或统一 runner。
- 现有 Java exporter 以 simple name 硬编码目标并默认导出完整 `decompiledC`。
- 当前没有 native pipeline 自动化测试或公开 C++/PDB fixture。

### Evidence / Claims / Runtime

- Blueprint Evidence Store 已有 JSON/SQLite revision、bounded query 与 context-pack 语义，可作为新原生查询协议的参考。
- `native_evidence/` 被 Git 忽略，当前只有本机 v1 JSON，没有 JSON-hash 绑定的 SQLite companion。
- `schemas/`、Native Evidence Store、hybrid graph、claim manifest validator 和 runtime observation 框架在改造前不存在。
- `reports/TIDES_OF_FORTUNE_COMPLETE_NATIVE_2026-07-26.md` 有两个指向 Git 忽略文件的相对链接。
- Reward/Ferox 报告另有本机 `native_evidence/*.json` 定位文字，缺少 committed sanitized manifest。

### 本地控制中心

- `scripts/blueprint_tool_server.py` 约 2,300 行；`src/main.ts` 约 2,300 行。
- 16 个 POST 中只有 Harvest build/cancel 两个使用了局部 Host/Origin/Content-Type 校验。
- 改造前没有 session token、remote bearer、统一 body size 上限或全路由安全策略。
- 通用 job snapshot 会公开完整命令、stdout/stderr 和绝对路径；输出无界。
- 通用 job 取消只终止父进程；Harvest job 已有可复用的进程树终止实现。
- 默认 bind 是 `127.0.0.1`，但任意非 loopback `--host` 不需要显式授权或 token。

### CI、版本与文档

- 改造前没有 `.github/workflows/`。
- `package.json` 与 lock 的版本均为 `0.0.0`，没有 `VERSION` 或 `CHANGELOG.md`。
- README 仍有不准确的产品定位和 harvest 术语漂移。
- `native_evidence/` 与 `captures/` 均已由 `.gitignore` 排除；改造不得提交 proprietary DLL/PDB、完整反编译输出或本机绝对路径。

## 改造顺序

1. 原生 build/PDB/project identity 与 fail-closed provenance。
2. 声明式 recipe、Native Evidence Store 与 bounded query。
3. Blueprint ↔ Native bridge、Claim Manifest、runtime observation。
4. 全 POST 安全边界、job 限长/脱敏/进程树取消。
5. Public fixture、CI、单一版本来源和文档一致性。

