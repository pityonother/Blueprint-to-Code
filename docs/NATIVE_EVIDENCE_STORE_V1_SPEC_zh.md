# Native Evidence Store v1 规格

Native Evidence Store 是 Blueprint to Code 的可选原生证据层。它保存某个
DLL、匹配 PDB 和分析 recipe 下恢复出的函数、调用、字段、常量、分支、vtable
与缺口。它不恢复原始 C++ 源码，也不把 Ghidra 伪 C 当作游戏运行时真值。

## 1. 权威源和目录

一个 evidence 目录包含：

```text
native_evidence/<binary-sha256>/<recipe-slug>/
  evidence.full.json
  evidence.manifest.json
  evidence.sqlite
  output/native_index.md
```

- `evidence.full.json` 是可移交、可审计的权威交换产物。
- `evidence.sqlite` 是从 JSON 生成的只读查询索引，不是第二份权威源。
- `evidence.manifest.json` 绑定 JSON/SQLite 的 SHA-256、大小、表计数和 trust。
- `native_index.md` 是不包含完整反编译正文的小型 AI 入口。

仓库继续忽略 `native_evidence/`。正式报告只提交 bounded sanitized manifest；
ARK DLL/PDB、Ghidra project、完整 proprietary 反编译和本机绝对路径都不能提交。

## 2. 身份和 trust

函数 ID 使用：

```text
native://<binary-sha256>/<module>/<rva>
```

同一 RVA 在不同 recipe 中仍是同一个函数 ID；recipe 和 evidence set 作为独立
来源版本保存。Evidence set ID 同时绑定 binary 与 recipe：

```text
native-set://<binary-sha256>/<recipe-sha256>
```

正式导入默认 fail closed，要求：

- DLL SHA-256 与 evidence、Ghidra program 一致；
- PDB SHA-256、GUID/Age 与 PE CodeView 一致，且 Ghidra 实际加载 PDB；
- recipe ID、recipe SHA-256 和每个 target 数量一致；
- runner、exporter、PDB configurator 等生成器哈希一致；
- Git generator 工作树为 clean；
- schema、manifest、JSON 和 SQLite 哈希/计数一致。

只有上述条件全部成立时才可标记 `VERIFIED`。调试旧 evidence 必须显式使用
`--allow-experimental`，并保留 `PROVENANCE_INCOMPLETE`、
`DIRTY_GENERATOR` 或其他非正式 trust，不能改写为 `VERIFIED`。

## 3. 从 v2 JSON 建索引

正式导入：

```powershell
runtime\python\python.exe scripts\import_native_evidence.py `
  --source "<recipe-output>\evidence.full.json" `
  --evidence-dir "native_evidence\<binary-sha256>\<recipe-slug>" `
  --formal --pretty
```

只为迁移或本机排错导入不完整 evidence：

```powershell
runtime\python\python.exe scripts\native_analysis\native_identity.py `
  --output "<legacy-v2.json>" --pretty `
  wrap-legacy `
  --dll "<matching.dll>" `
  --pdb "<matching.pdb>" `
  --raw-export "<native-targets-v1.json>" `
  --toolchain scripts\native_analysis\toolchain.json `
  --experimental

runtime\python\python.exe scripts\import_native_evidence.py `
  --source "<legacy-v2.json>" `
  --evidence-dir "native_evidence\<binary-sha256>\<recipe-slug>" `
  --allow-experimental --pretty
```

`wrap-legacy` 不会补造旧文件中不存在的 PDB loaded、recipe 或 generator
证据；这类产物保留 `PROVENANCE_INCOMPLETE`，formal report validator 拒绝它。

发布采用 staging 后原子替换。打开 repository 时会重新计算 JSON/SQLite
SHA-256、执行 SQLite integrity/foreign-key 检查、核对 schema version、
evidence set、trust 和每张表的计数。任一不一致都拒绝查询。

## 4. 表和查询

SQLite 至少包含：

```text
native_evidence_sets
native_binaries
native_symbol_sets
native_functions
native_parameters
native_call_edges
native_call_sites
native_field_accesses
native_constants
native_branches
native_vtable_slots
native_gaps
native_recipe_targets
native_blueprint_links
```

常用查询：

```powershell
runtime\python\python.exe scripts\query_native_evidence.py `
  --evidence-dir "<evidence-dir>" overview --budget 700

runtime\python\python.exe scripts\query_native_evidence.py `
  --evidence-dir "<evidence-dir>" search `
  --query "GenerateCrateItems" --budget 900

runtime\python\python.exe scripts\query_native_evidence.py `
  --evidence-dir "<evidence-dir>" function `
  --id "native://<sha>/<module>/<rva>" --budget 1200

runtime\python\python.exe scripts\query_native_evidence.py `
  --evidence-dir "<evidence-dir>" callers `
  --id "native://<sha>/<module>/<rva>" --depth 2 --budget 1200

runtime\python\python.exe scripts\query_native_evidence.py `
  --evidence-dir "<evidence-dir>" field-accesses `
  --query "ItemRating" --budget 900

runtime\python\python.exe scripts\query_native_evidence.py `
  --evidence-dir "<evidence-dir>" constants --budget 900

runtime\python\python.exe scripts\query_native_evidence.py `
  --evidence-dir "<evidence-dir>" gaps --budget 900
```

还支持 `callees` 和 `blueprint-links`。列表查询提供 page size、cursor、
returned/omitted 和 `nextQuery`。整个响应接受 500–8,000 estimated-token
预算；`AVAILABLE_NOT_RETURNED` 表示证据存在但本页没有返回，不等于
`NOT_RECOVERED` 或 `SOURCE_NOT_AVAILABLE`。

`function --include-decompile` 只在明确排错时使用，并受 snippet 上限约束。
默认索引和 Context Pack 不包含完整 `decompiledC`。

## 5. 问题驱动 Context Pack

```powershell
runtime\python\python.exe scripts\build_native_context_pack.py `
  --evidence-dir "<evidence-dir>" `
  --question "GenerateCrateItems 如何选择 Entry 并设置品质？" `
  --budget 1600 `
  --output-dir "analysis\native_context"
```

输出把确认函数、caller/callee、字段、常量、分支和 gap 分开，并记录所用
source fingerprint。它是一个可丢弃的有界视图，不替代权威 JSON。

## 6. 验证和公开 fixture

单个原生产物验证：

```powershell
runtime\python\python.exe scripts\validate_native_evidence.py `
  --evidence-dir "<evidence-dir>" `
  --dll "<matching.dll>" `
  --pdb "<matching.pdb>" `
  --pretty
```

不带 `--experimental` 时，PDB 未加载、GUID/Age 不匹配、dirty generator、
target 数量不符或任何 provenance 漂移都会返回非零。公开 fixture 的完整构建
与 recipe 命令见
[`tests/native_fixture/README.md`](../tests/native_fixture/README.md)；它证明
pipeline 行为，不证明 ARK 运行时行为。
