# Blueprint Evidence Publication v3 操作合同

本文件说明单个 `captures/<AssetName>` 如何发布、读取和迁移 Blueprint
Evidence v3。v3 的目标是让消费者只通过一个原子 pointer 选择一份不可变、由
manifest 和 SHA-256 绑定的 revision。它不改变 `bp://` Evidence ID 的语义，也不
把静态证据升级为运行时实测。

v3 是否可用于具体发布，必须由当前 revision 的验证结果和来源新鲜度决定；本文
只定义操作合同，不代表任何本机 capture 已完成迁移或验收。

## 1. 目录布局

```text
captures/<AssetName>/
  .publication.lock
  evidence/
    current.json
    revisions/
      <evidenceRevisionId>/
        evidence.sqlite
        agent_index.md
        manifest.json
    evidence.sqlite              # v2 compatibility copy
    manifest.json                # v2 compatibility copy
  output/
    agent_index.md               # v2 compatibility copy
  interpretation/
    current.json
    revisions/
      <interpretationRevisionId>/
        interpretation.json
        interpretation.md
        trace.json
        gaps.json
        pseudocode.txt
        manifest.json
```

`evidence/revisions/<evidenceRevisionId>/` 发布后不可修改。它的精确文件集合只有
`evidence.sqlite`、`agent_index.md` 和 `manifest.json`；SQLite 的 `-wal`、`-shm`
sidecar 或额外文件都会使验证失败。根目录的三份 v2 文件只是兼容副本，不是 v3
authority。

Interpretation 使用独立 revision 树，并单向绑定 Evidence revision 与其 manifest
hash。Evidence 不反向绑定 Interpretation，因而不会产生 hash 循环，也不会为了
补写解释而修改已发布 Evidence。设计理由见
[ADR-004：Separate immutable Evidence and Interpretation revisions](decisions/ADR-004-immutable-evidence-and-interpretation-revisions.md)。
Interpretation 的 statement、trace、gap、CLI、HTTP 和 UI 合同见
[Blueprint Interpretation Contract v1](BLUEPRINT_INTERPRETATION_CONTRACT_V1_zh.md)。

## 2. Pointer、manifest 与 artifact 信任链

消费者的规范读取顺序是：

```text
evidence/current.json
  -> revisions/<revisionId>/manifest.json
  -> evidence.sqlite + agent_index.md
```

验证必须逐层 fail closed：

1. 严格解析 `current.json`，拒绝重复 key、非有限数字、绝对路径、路径穿越、链接、
   junction 和 reparse point。
2. 确认 pointer 的 `revisionId`、相对 `manifest` 路径与
   `manifestSha256` 一致。
3. 严格解析 manifest，确认 revision、Object Path、parser/schema、source
   fingerprint、semantic digest、artifact 相对路径、字节数和 SHA-256。
4. 以只读模式打开 SQLite，拒绝 WAL/SHM，检查 schema/user version、数据库身份、
   `integrity_check`、foreign keys、表计数、覆盖统计和 semantic digest。
5. 再检查 revision 目录没有新增或被替换的文件，最后评估来源新鲜度。

任一层失败都不得继续读取，也不得静默回退 v2 或 legacy。`release_authority=true`
只表示该 revision 当前由通过完整校验的 v3 `current.json` 选中；显式读取旧 revision
或 pointer 提交前留下的 orphan 一律不是 authority。该字段不等于来源新鲜，也不等于
运行时结论已验证。

Public pointer 和 manifest 不得包含本机绝对路径、用户目录或 UNC 路径。来源字节
仍由 hash 绑定；无法安全公开的来源名使用稳定的
`@external/<path-hash>/<filename>` 别名。绝对路径只可留在 ignored 本机诊断信息，
不能进入发布包或提交。

## 3. 来源新鲜度

| 状态 | 含义 | 操作 |
| --- | --- | --- |
| `FRESH` | 可解析的本机来源仍存在，且内容 hash 与 revision 记录一致 | 可继续其他发布门禁；仍须保留 parser gap 和证据级别 |
| `STALE` | 至少一个当前来源存在但 hash 已变化 | 默认拒绝读取；重新读取来源并发布新 revision |
| `SOURCE_UNAVAILABLE` | 当前机器无法取得用于比较的原始来源 | 可审计不可变 artifact，但不能声称已证明来源仍新鲜 |

`allow_stale=True` 只适合显式诊断或恢复，不是把 `STALE` 改写为 `FRESH` 的开关。
同样，`SOURCE_UNAVAILABLE` 必须原样暴露，不能用 artifact 自洽性冒充 source
freshness。

## 4. v2 compatibility 与 legacy 边界

- 有有效 v3 pointer 时，只读 v3。v3 损坏时直接失败，不能借 v2 掩盖损坏。
- 没有 v3 pointer、但 v2 SQLite/manifest/index 有效时，可作为
  `INDEXED_V2_COMPATIBILITY` 读取；它的 `release_authority=false`，并报告
  `migration_required=true`。
- 只有调用方显式启用 legacy fallback 时，才可建立
  `LEGACY_TEMPORARY_PROJECTION`；它同样不是 release authority。
- v3 pointer 成功且通过公共 reader 复验后，发布器才刷新 v2 compatibility copies。

因此兼容读取用于过渡，不是把旧布局重新命名成 v3，也不能作为跳过迁移的发布
依据。

## 5. 从 v2 迁移

默认迁移会验证 v2，发布/复用不可变 v3 revision，原子更新 pointer，然后保留并
刷新 v2 compatibility copies：

```powershell
runtime\python\python.exe scripts\migrate_blueprint_evidence_v3.py `
  --asset-dir "captures\<AssetName>"
```

成功时 stdout 是 JSON；失败时不输出成功 JSON，错误写入 stderr，进程退出码为
`2`。先检查 JSON 中的 `revision_id`、`manifest_sha256`、`pointer_sha256`、
`freshness_status`、`reused_existing`、`pointer_updated`、
`compatibility_copy_status` 和 `pruned_v2`，再运行消费者查询与发布门禁。普通 writer
返回的 `database_path`、`manifest_path`、`agent_index_path` 指向 pointer 绑定的
immutable revision；flat compatibility 路径只通过明确的
`compatibility_*_path` 字段暴露。

只有已单独备份或确认所有消费者都能解析 v3，并且确实要移除兼容副本时，才显式
执行：

```powershell
runtime\python\python.exe scripts\migrate_blueprint_evidence_v3.py `
  --asset-dir "captures\<AssetName>" `
  --prune-v2
```

`--prune-v2` 会在 v3 current revision 再次验证且 revision/manifest 仍与刚发布结果
一致后，只删除三份精确的 v2 compatibility artifact。它不会删除 immutable
revision，但也不应在首次迁移、并发发布或消费者尚未切换时使用。不要手工批量删除
capture 文件。

prune 的事务提交点是三份 canonical v2 文件全部原子移入带操作 ID 的 quarantine：
若任一 rename 尚未完成就失败，已移动文件会按反序恢复；三个 rename 全部完成后，
canonical v2 布局即视为已清理，后续 unlink 只是垃圾回收。若 Windows 文件锁或杀毒
软件阻止 quarantine 清理，命令仍报告 `pruned_v2=true`，同时返回
`prune_cleanup_status=PENDING`、`prune_cleanup_error` 与相对
`prune_cleanup_leftovers`；发布门禁必须显式看到并处理该状态，不能声称物理清理已经
全部完成。重试清理前仍须重新验证 current，不得恢复成可被 reader 发现的 flat v2。

## 6. 并发、失败与恢复

发布器先在同一 asset filesystem 中构建完整 staging revision，验证后再把目录原子
改名；随后在 `captures/<AssetName>/.publication.lock` 下比较 pointer 的原始
SHA-256，并以 CAS 方式原子替换 `current.json`。

在 Windows 上，该稳定 lock file 使用单字节文件锁。不要删除、替换或链接它；
link/junction/reparse point 会被拒绝。杀毒软件或另一个进程暂时占用目标时，原子
替换会在有界时间内重试，超时后失败，而不是降级为非原子覆盖。

| 失败位置 | 保证 | 恢复动作 |
| --- | --- | --- |
| pointer 更新前 | 旧 pointer 不变；staging 会清理 | 修复输入后重跑 |
| revision 改名后、pointer 更新前 | 可能留下未被 pointer 引用的 immutable revision | 保留并检查；重跑可安全复用相同 revision，不要直接覆盖 |
| CAS 冲突 | 另一个发布者已改变 pointer，本次不覆盖它 | 重新读取并验证 current，再从新 baseline 重跑 |
| pointer 更新后 | pointer 是完整旧 JSON 或完整新 JSON，不会是半写文件 | 先用公共 reader 验证 current；有效时重跑以完成兼容副本，未知时停止 prune 和发布 |

当前迁移 CLI 不提供“手工改 pointer”的回滚捷径。需要回到旧 revision 时，必须先
验证旧 revision 的 manifest/artifacts，再通过同一 lock + CAS 发布路径选择它；不要
编辑 `current.json`、覆盖 revision 目录或删除新 revision。若 pointer 状态无法确定，
先复制保全整个 asset 目录并停止写入，再进行恢复。

## 7. 聚焦验证

代码库中的聚焦合同检查：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
runtime\python\python.exe -m unittest `
  tests.test_evidence_publication_v3 `
  tests.test_migrate_blueprint_evidence_v3
runtime\python\python.exe tests\test_documentation_consistency.py
git diff --check
```

对真实 asset 的迁移验收还应逐项确认：

1. `current.json` 指向的 manifest raw SHA-256 相符；
2. manifest 中两个 artifact 的 bytes/SHA-256 与文件相符；
3. SQLite integrity、foreign keys、identity、counts 与 semantic digest 通过；
4. freshness 明确为 `FRESH`、`STALE` 或 `SOURCE_UNAVAILABLE`，没有被省略；
5. 查询结果继续绑定同一 revision 与 `bp://` Evidence ID；
6. public pointer/manifest 不含本机路径、秘密或 proprietary 原始资产；
7. 未显式使用 `--prune-v2` 时，三份 compatibility artifact 仍存在。
