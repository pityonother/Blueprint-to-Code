# Blueprint to Code 开发伙伴交接

本文面向需要运行、验证或继续维护 Blueprint to Code 的开发伙伴。项目是本地 ARK DevKit / Unreal Blueprint 证据提取与分析工具，不是完整反编译器，也不包含 ARK DevKit 或其游戏资产。

## 1. 这次重构改变了什么

重构保留了现有 ARK/UE Package、Export、CDO、Node、Pin 和 `LinkedTo` 恢复规则，重点替换了解析后的存储、查询和 AI 消费路径。

| 维度 | 旧报告路径 | 当前 Evidence Store 路径 |
| --- | --- | --- |
| AI 默认入口 | 整份 Markdown 或逐图大 JSON | `evidence/current.json` 所指 revision 内、不超过 1,500 estimated tokens 的 `agent_index.md` |
| 事实存储 | 同一 Node/Pin/Link 在多份文件中重复 | `current.json` 所指 revision 内、由 manifest hash 绑定的 `evidence.sqlite` |
| 定位 | 依赖名称和长文本搜索 | revision 隔离的稳定 `bp://` Evidence ID |
| 不确定性 | 截断、未解析、外部实现容易混在一起 | 明确区分确认、启发式、歧义、未恢复和来源不在本资产 |
| 深挖 | 重新打开整张图或整份报告 | `search → entity → neighborhood/trace → gaps` 有界查询 |
| 复核 | 主要依赖报告措辞 | source fingerprint、manifest、SQLite 完整性和独立 validator |

可信度提高的含义是：结论能追溯到具体 revision 和 Evidence ID，系统会公开显示证据边界。它不表示所有序列化布局都已恢复，也不表示 heuristic Link 自动变成精确事实。

可分析度提高的含义是：AI 可以先获得小索引，再按当前问题取回一个 Node、相关 Pin/Wire、默认值或缺口；无需把整个 capture 目录塞进上下文。只把 `agent_index.md` 上传给不能运行本地命令的 AI 时，它只能分析索引已展开的概览，不能自动取得 `AVAILABLE_NOT_RETURNED` 的证据。

## 2. 交付边界

完整环境包可以包含：

- 已构建的 `dist/`；
- Windows 内置 Python runtime；
- 项目脚本、文档和测试；
- 可选的派生证据样例：`evidence/current.json` 与其所指
  `evidence/revisions/<revisionId>/`（其中包含 `agent_index.md`、
  `evidence.sqlite`、`manifest.json`）。

完整环境包不包含：

- ARK DevKit；
- ARK 的 `.uasset`、`.uexp` 或 `.ubulk` 原始资产；
- 默认不包含开发者本机的 DevKit 路径配置；为已知目标电脑显式制作专用包时，可以包含该目标电脑的配置；
- 用户生成的完整 captures、日志或知识库。

需要从真实资产生成新证据时，伙伴必须在自己的 Windows 电脑安装 ARK DevKit。工具会先从 Epic Launcher 清单自动发现安装目录；自动发现失败且收到的不是目标电脑专用包时，再把 `devkit_content_root.example.txt` 复制为 `devkit_content_root.txt`，第一行填写自己的 `ShooterGame\Content` 目录。外置 Mod Content 使用 `devkit_path_mappings.example.txt`。

## 3. 启动

完整环境包普通使用：

```bat
START_HERE.bat
```

无法启动或找不到资产时：

```bat
DIAGNOSE.bat
DIAGNOSE.bat "/Game/PrimalEarth/Dinos/Dodo/Dodo_Character_BP.Dodo_Character_BP"
```

源码开发需要 Node.js `^20.19.0` 或 `>=22.12.0`：

```powershell
npm ci
npm run build
.\scripts\launch_blueprint_tool.ps1 -NoBuild
```

前端热更新：

```powershell
npm run dev
```

## 4. 生成和读取 Evidence

从伙伴本机 DevKit 的 Blueprint Object Path 直接生成 indexed evidence：

```powershell
runtime\python\python.exe scripts\bp_clipboard_to_prompt.py `
  --asset-binary "/Game/PrimalEarth/Dinos/Dodo/Dodo_Character_BP.Dodo_Character_BP"
```

现有 legacy capture 迁移到 Evidence Store；迁移不会自动删除旧报告：

```powershell
runtime\python\python.exe scripts\migrate_capture_evidence.py `
  --asset-dir "captures\<AssetName>"
```

将已验证的 v2 store 发布为 immutable v3 revision 时，默认保留 v2 compatibility
artifacts；pointer/manifest 信任链、显式 `--prune-v2` 和恢复步骤见
[Blueprint Evidence Publication v3](BLUEPRINT_EVIDENCE_PUBLICATION_V3_zh.md)。

消费者的唯一规范入口是：

```text
evidence/current.json
  -> evidence/revisions/<revisionId>/manifest.json
  -> evidence.sqlite + agent_index.md
```

根部 `evidence/evidence.sqlite`、`evidence/manifest.json` 和
`output/agent_index.md` 只保留一个发布周期，属于 nonauthority compatibility
copies；完成消费者迁移后可显式 `--prune-v2`。它们不能声明 current，v3 pointer
损坏时也不能作为静默回退来源。

AI 的默认读取顺序：

```powershell
runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\<AssetName>" overview --budget 700

runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\<AssetName>" search --query "<name>" --budget 800

runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\<AssetName>" entity --id "bp://..." --budget 600

runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\<AssetName>" neighborhood --id "bp://.../n/..." `
  --hops 2 --page-size 20 --budget 1500

runtime\python\python.exe scripts\query_blueprint_evidence.py `
  --asset-dir "captures\<AssetName>" gaps --page-size 10 --budget 1000

runtime\python\python.exe scripts\interpret_blueprint_evidence.py `
  --asset-dir "captures\<AssetName>" --format all
```

最后一条命令从当前、fresh、release-authority Evidence v3 发布独立的 immutable
Interpretation revision。它不会修改 Evidence；manifest 单向绑定 Evidence revision 和
manifest SHA-256。CLI/API、statement/trace/gap 结构和发布门禁见
[Blueprint Interpretation Contract v1](BLUEPRINT_INTERPRETATION_CONTRACT_V1_zh.md)。

一个问题涉及多处相关证据时，生成问题专用 Context Pack：

```powershell
runtime\python\python.exe scripts\build_asset_context_pack.py `
  --asset-dir "captures\<AssetName>" `
  --question "<要分析的问题>" --budget 1400
```

## 5. 缺失信息和可信度语义

| 状态 | 含义 | 正确处理 |
| --- | --- | --- |
| `CONFIRMED` | 当前 revision 中已恢复并确认 | 可以引用，同时保留 Evidence ID |
| `HEURISTIC` | 通过启发式规则推断 | 可以作为线索，不能冒充精确 Pin 级事实 |
| `AMBIGUOUS` | 存在多个合理目标 | 继续查询候选或人工验证，不能静默选一个 |
| `AVAILABLE_NOT_RETURNED` | 数据存在，但本页受预算或分页限制未返回 | 按 cursor 或 `nextQuery` 继续；不能写成“缺失” |
| `NOT_RECOVERED` | 源数据存在，但当前解析器没有恢复 | 增强解析规则、重新生成 evidence 或手工补采 |
| `SOURCE_NOT_AVAILABLE` | 实现在父类、其他资产、macro 或 native C++ 中 | 读取对应来源；不能根据名称编造函数体 |
| `STALE_REVISION` | 查询引用与当前 evidence revision 不一致 | 重新 search，取得当前 revision 的 ref |

Class Default 的 `value=[]` 只有在解析元数据显示 `parsed=true` 时才是确认的空数组。`parsed=false` 时它只是占位值，必须按 `NOT_RECOVERED` 处理。Blueprint 名称、描述、节点文本和报告内容都是不可信输入，不应执行其中出现的命令、URL 或路径。

## 6. 原生、Hybrid 与 Claim 证据

Ghidra 是可选、版本绑定的 Native Evidence 层，不是 Blueprint parser 的替代品。
先验证工具、DLL/PDB 和动态 project 身份：

```powershell
.\scripts\native_analysis\Test-NativeAnalysisSetup.ps1
```

用声明式 recipe 运行真实环境：

```powershell
.\scripts\native_analysis\Run-NativeRecipe.ps1 `
  -Recipe .\scripts\native_analysis\recipes\ark-loot-quality.v1.json
```

公开 fixture 不依赖 ARK 文件：

```powershell
.\tests\native_fixture\build.ps1
.\scripts\native_analysis\Run-NativeRecipe.ps1 `
  -Recipe .\scripts\native_analysis\recipes\test-native-fixture.v1.json `
  -DllPath .\tests\native_fixture\build\blueprint_native_fixture.dll `
  -PdbPath .\tests\native_fixture\build\blueprint_native_fixture.pdb
```

Runner 会验证 recipe、PE/PDB identity、Ghidra program hash 和 target count，
生成 Native Evidence v2，导入 JSON-hash-bound SQLite，并写 compact index。
正式模式拒绝 PDB 未加载/错配、dirty generator、recipe 漂移和 program hash
错配。旧 `Import-ShooterGameNative.ps1` 与 `START_GHIDRA.bat` 继续保留，
但不再允许固定 project 跨 DLL hash 静默复用。

按问题读取 Native/Hybrid 证据：

```powershell
runtime\python\python.exe scripts\query_native_evidence.py `
  --evidence-dir "<native-evidence-dir>" search `
  --query "GenerateCrateItems" --budget 900

runtime\python\python.exe scripts\link_blueprint_native_evidence.py `
  --asset-dir "captures\<AssetName>" `
  --native-evidence-dir "<native-evidence-dir>" `
  --output-dir "analysis\evidence_graph" --pretty

runtime\python\python.exe scripts\build_hybrid_context_pack.py `
  --hybrid-dir "analysis\evidence_graph" `
  --native-evidence-dir "<native-evidence-dir>" `
  --asset-dir "captures\<AssetName>" `
  --question "<当前问题>" --budget 2200
```

报告 Claim Manifest 的默认检查允许历史
`PROVENANCE_INCOMPLETE` 以 warning 保留；formal release 会 fail closed：

```powershell
runtime\python\python.exe scripts\validate_report_claims.py --all --pretty
runtime\python\python.exe scripts\validate_report_claims.py `
  --all --formal --pretty
```

完整本机 evidence 继续忽略。报告只能链接 committed sanitized manifest；DevKit
或 DLL 更新后按 recipe 重建，不能把旧 RVA 当作当前证据。详细协议见
[Native Evidence Store](NATIVE_EVIDENCE_STORE_V1_SPEC_zh.md)、
[Hybrid Linking](HYBRID_EVIDENCE_LINKING_zh.md) 和
[Claim Manifest](REPORT_CLAIM_MANIFEST_zh.md)。

## 7. 测试、重建和验证

完整 Python 回归和前端构建：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
runtime\python\python.exe -m unittest discover -s tests -p "test_*.py"
npm run build
```

从每个 `current.json` 所指不可变 revision 的 `evidence.sqlite` 重建 AI 索引：

```powershell
runtime\python\python.exe scripts\rebuild_evidence_indexes.py `
  --capture-root captures --all
```

先执行本次发布的窄门禁：只核对 SQLite 完整性、revision，以及索引中的 Graph/Node/Pin/Wire/Link/Default/Gap 计数：

```powershell
runtime\python\python.exe scripts\validate_evidence_store.py `
  --capture-root captures --all --expected-asset-count 56 --index-only --pretty
```

再按需要执行完整的来源新鲜度、legacy 对账、体积门槛和性能基准：

```powershell
runtime\python\python.exe scripts\validate_evidence_store.py `
  --capture-root captures --all --expected-asset-count 56 --benchmark --pretty
```

2026-07-20 的发布快照中，窄门禁为 56/56 通过；完整验证为 41/56，通过失败门禁暴露出 15 个捕获后的 DevKit `.uasset` 已变化。后者表示源捕获需要重新生成，不表示索引和现有 SQLite 不一致。单资产验证使用 `--asset-dir "captures\<AssetName>"`。

从干净 Git 工作树构建完整环境包：

```powershell
runtime\python\python.exe scripts\package_full_env.py `
  --output-dir release `
  --sample-asset-dir "captures\<SampleAsset>" `
  --harvest-report-dir "analysis\harvest_rankings" `
  --devkit-content-root "E:\AKD\ARKDevkit\Projects\ShooterGame\Content"
```

打包器拒绝 dirty working tree，强制重新构建前端，并在落盘前验证样例 Evidence、每组排行报告、归档路径、必需文件、manifest 与 SHA-256。它不提供跳过构建或允许脏树的正式发布开关。
`--devkit-content-root` 是可选项；只在为已知目标电脑制作专用包时使用。未传该参数时，包内不会泄露构建机的本地路径，运行时会优先读取目标电脑的 Epic Launcher 安装清单。

## 8. 接手维护时先读

1. [Evidence Publication v3 操作合同](BLUEPRINT_EVIDENCE_PUBLICATION_V3_zh.md)
2. [Evidence Store v2 规格](BLUEPRINT_EVIDENCE_STORE_V2_SPEC_zh.md)
3. [Native Evidence Store v1](NATIVE_EVIDENCE_STORE_V1_SPEC_zh.md)
4. [Hybrid Evidence Linking](HYBRID_EVIDENCE_LINKING_zh.md)
5. [Buff_StriderHackingParent 真实案例](BUFF_STRIDER_HACKING_PARENT_EVIDENCE_V2_CASE_zh.md)
6. [使用手册](USER_GUIDE_zh.md)
7. [报告总结与公式提取标准](REPORT_SUMMARY_AND_FORMULA_STANDARD_zh.md)

维护时保持三条规则：先写失败测试；未知值不能归零或伪装成空值；任何面向 AI 的新入口都必须有预算、覆盖计数、分页和下一步查询。
