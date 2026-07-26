# Blueprint ↔ Native Hybrid Evidence Linking

Hybrid Evidence 把 Blueprint call/ref 节点与一个版本固定的 Native Evidence
Store 连接起来。连接是独立、可失效的证据边；系统不会只凭相似名称把两个来源
揉成同一个结论。

## 1. 关系和状态

正向关系：

```text
CALLS_NATIVE
REFERENCES_NATIVE
```

查询层可派生反向关系：

```text
CALLED_BY_BLUEPRINT
```

每条边保存 Blueprint revision/source fingerprint、Native evidence set/source
fingerprint、解析规则、全部候选和 gap。状态语义：

| 状态 | 含义 |
| --- | --- |
| `CONFIRMED` | owner + member + 可选 signature 只解析到一个 native target |
| `AMBIGUOUS` | 有多个候选，系统不静默选择 |
| `SOURCE_NOT_AVAILABLE` | 当前 Native Evidence Store 中没有相符来源 |
| `NOT_RECOVERED` | Blueprint macro 或 Blueprint-implemented 函数不能冒充 native |
| `STALE` | Blueprint revision、Blueprint source、Native source 或 evidence set 已变化 |

边 ID 是 source ID、relation 和 native evidence set 的稳定哈希。重复的同源关系
会被拒绝，避免后写入的猜测覆盖先前歧义。

## 2. 解析规则

解析只接受稳定 `bp://` node/pin Evidence ID。优先使用：

1. Blueprint member name；
2. 明确 owner；
3. 可选 signature hints；
4. 候选数必须恰好为 1。

只有短名称而没有 owner 时输出候选并标记 `AMBIGUOUS`。Macro、
`BLUEPRINT_IMPLEMENTED` 和其他 Blueprint-only 实现输出明确 gap，不按名称
搜索一个 native 函数来“补全”。

## 3. 生成显式边

从现有 Blueprint Evidence Store 读取 call 节点：

```powershell
runtime\python\python.exe scripts\link_blueprint_native_evidence.py `
  --asset-dir "captures\<AssetName>" `
  --native-evidence-dir "<native-evidence-dir>" `
  --output-dir "analysis\evidence_graph" `
  --pretty
```

也可把人工复核后的调用输入保存为 JSON：

```json
{
  "schema": "blueprint-to-code-blueprint-native-calls/v1",
  "blueprintRevisionId": "<revision>",
  "blueprintSourceFingerprint": "<sha256>",
  "calls": [
    {
      "evidenceId": "bp://.../g/1/n/10",
      "memberName": "GenerateCrateItems",
      "owner": "UPrimalInventoryComponent",
      "signatureHints": []
    }
  ]
}
```

然后运行：

```powershell
runtime\python\python.exe scripts\link_blueprint_native_evidence.py `
  --calls-json "<calls.json>" `
  --native-evidence-dir "<native-evidence-dir>" `
  --output-dir "analysis\evidence_graph" `
  --pretty
```

输出目录包含 authoritative `hybrid_edges.json`、SHA-256 绑定的只读 SQLite
和 manifest。任何 JSON/SQLite hash 或 dependency fingerprint 不一致都
fail closed。

## 4. Hybrid Context Pack

```powershell
runtime\python\python.exe scripts\build_hybrid_context_pack.py `
  --hybrid-dir "analysis\evidence_graph" `
  --native-evidence-dir "<native-evidence-dir>" `
  --asset-dir "captures\<AssetName>" `
  --question "这个 Blueprint 调用如何进入原生品质计算？" `
  --budget 2200 `
  --output-dir "analysis\hybrid_context"
```

输出严格分区：

- Blueprint confirmed facts；
- Native confirmed facts；
- resolved cross-source edges；
- assumptions；
- Blueprint gaps；
- Native gaps；
- runtime-only gaps；
- stale provenance warnings。

默认预算为 2,200 estimated tokens，上限 8,000。完整反编译正文不进入 pack。
当当前 Blueprint revision/source fingerprint 与边依赖不一致时，边会变为
`STALE`；调用方必须重新运行 linker，不能沿用旧 target。

## 5. 与 Claim Manifest 的关系

Hybrid edge 只证明“当前两个证据版本之间如何解析”，不直接证明玩家可见结论。
报告中的 `claim://` 还必须列出它依赖的 `bp://`、`native://`、假设、source
fingerprints、失效条件和 runtime validation。验证命令与 formal policy 见
[`REPORT_CLAIM_MANIFEST_zh.md`](REPORT_CLAIM_MANIFEST_zh.md)。
