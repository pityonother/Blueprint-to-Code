# Blueprint to Code 本地存储与生成物清理

## 结论

源码仓库本身不应达到数十 GB。主要空间通常来自：

1. `.tmp/` 中的全量 KB 测试构建；
2. `knowledge_base/vnext/snapshots/` 的多个 immutable snapshot；
3. Discovery 数据库、Registry 工作区和中断 staging；
4. 多个 Git worktree 各自物化的 Git LFS 文件；
5. `captures/` 中同时保留 indexed Evidence 与旧 JSON/Markdown 图产物。

不要按扩展名或目录大小直接批量删除。每一类数据的可恢复性不同。

## 2026-07-30 本机清理记录

只读盘点得到：

- 主工作树：87.020 GiB；
- 19 个旧 worktree：7.726 GiB；
- 合计：94.746 GiB（约 101.73 GB）。

完成分阶段清理后，立即复测为：

- 主工作树：20.454 GiB；
- 文档 worktree：0.040 GiB；
- 两者合计：20.494 GiB；
- 相对清理前净释放：74.252 GiB；
- 当前 KB 仍为 `READY / FRESH / shadow / legacy`；
- Git 工作区保持干净。

2026-07-31 再次只读实测：

| 范围 | Bytes | GiB |
|---|---:|---:|
| 主工作树 | 34,847,120,961 | 32.454 |
| 文档 worktree | 43,196,334 | 0.040 |
| 合计 | 34,890,317,295 | 32.494 |
| 其中 5 个 immutable snapshots | 21,473,498,382 | 19.999 |

后续验收构建创建了新的完整 Snapshot。相对清理完成时增加的 12.000 GiB
来自这些仍在保留链中的可重建全量 Snapshot；这不是清理失败，也不授权删除
任何 Snapshot。

已删除：

- `.tmp/` 中约 45.389 GiB 的 Stage 4–8 测试构建；
- `knowledge_base/vnext/.build/` 与中断的
  `.discovery_bundle.building-*`；
- 19 个已合入 `main`、无未提交改动的旧 worktree；
- `.git/lfs/incomplete/` 中的残缺传输；
- 两个不再被 current/previous 链引用的旧 snapshot；
- legacy-v1 根目录数据库；
- Registry snapshot 中两个旧 generation/根级重复副本。

明确保留：

- 当前 snapshot 与它直接绑定的 previous snapshot；
- 被报告、回滚、attestation 或 tag 引用的 snapshot；
- 当前 Discovery 数据库；
- 当前 Registry generation 与 resume state；
- `captures/*/evidence` 和尚未完成 fresh-source prune 的旧图产物；
- legacy 查询库、Native Evidence 与正式分析结果；
- Git LFS 正式 object 和 tracked ZIP。

## 清理顺序

### 1. 建立基线

```powershell
git status --short --branch
git worktree list --porcelain
Get-Content -Raw knowledge_base\vnext\current.json
```

同时检查是否存在 `build_ark_kb_vnext.py`、`update_ark_kb_vnext.py`、
`blueprint_tool_server.py` 或 `git-lfs` 活动进程。存在写入者时不要清理。

### 2. 临时构建

可重建候选：

- `.tmp/`
- `knowledge_base/vnext/.build/`
- `.discovery_bundle.building-*`
- `.git/lfs/incomplete/`

删除前必须逐个解析成明确绝对路径，并确认目标位于仓库内。不要对仓库根、
`knowledge_base/`、`snapshots/` 或通配结果执行递归删除。

### 3. Worktree

每个待删除 worktree 必须同时满足：

- `git status --porcelain` 为空；
- worktree HEAD 是 `origin/main` 的 ancestor；
- 没有需要保留的 ignored 生成物。

然后用：

```powershell
git worktree remove "<exact-worktree-path>"
git worktree prune
```

不要先手工删目录再修 Git metadata。清理 worktree 不要求删除对应分支或标签。

### 4. Immutable snapshot

最小保留策略：

- `current.json` 指向的当前 snapshot；
- 当前 manifest 的 `previousSnapshot` 直接父 snapshot；
- 被 burn-in、rollback、tag 或外部 attestation 明确引用的 snapshot。

删除更旧 snapshot 前：

1. 核对 current pointer；
2. 计算 previous manifest SHA-256 并与 current manifest 比较；
3. 对保留 snapshot 的所有 SQLite 执行只读 `PRAGMA integrity_check`；
4. 搜索 tracked docs、attestation 和 tag 是否仍引用待删 Build ID；
5. 删除后重新调用 `VNextKnowledgeService.health()`。

### 5. Discovery 与 Git LFS

tracked `knowledge_base/discovery_bundle.zip` 是历史 GPT Pro 视察包。判断本机
未压缩 Discovery 是否可删时，必须比较 ZIP entry 与当前 SQLite 的：

- uncompressed bytes；
- SHA-256；
- SQLite integrity。

2026-07-30 的实测结果是两者不同：

| 数据 | Bytes | SHA-256 |
|---|---:|---|
| 当前本机 Discovery | 3,816,792,064 | `028a12c429903466aa52f99c5e63c8d90813585b9d5c6a8c303fbb93a9d6a31f` |
| LFS ZIP 内历史 Discovery | 3,816,177,664 | `9f106a091815dd88aa729d28140db728e0f1b37dbeebf2fd5f2182492ef4ea50` |

因此不能用历史 ZIP 作为当前数据库的恢复副本，也不能据此删除当前 Discovery。
`.git/lfs/objects` 中仍被提交引用的正式 object 同样不能手工删除。

### 6. Registry 工作区

`registry_manifest.json` 会指向唯一当前 generation。只有在当前 generation 的
bytes 与 SHA-256 全部匹配 manifest 后，才可删除未被 manifest 引用的旧
generation 和根级重复副本。`discovery_state.sqlite` 是续建状态；除非接受完整
重建成本，否则保留。

### 7. Capture legacy

`graphs_from_uasset/` 和旧 `output/` 可能很大，但不能只因 indexed Evidence
通过 SQLite integrity 就删除。正式清理还要求：

- Evidence manifest、revision、table counts 与 `agent_index.md` 一致；
- 当前 DevKit 源资产可以重新打开；
- 没有 `--uasset-max-graphs`；
- 所有图是 `complete` / `complete_empty`；
- 使用显式 `--prune-legacy`。

partial、heuristic、needs-clipboard 或 source drift 的资产必须保留旧图产物。

## 清理后验收

```powershell
git status --short --branch
git worktree list
git lfs ls-files -s
```

还需要确认：

- current Build ID 未变化；
- health 为 `READY / FRESH`；
- mode 仍是 `shadow`、默认来源仍是 `legacy`；
- current 与 previous SQLite integrity 全部为 `ok`；
- LFS ZIP hash 未变化；
- 没有 tracked 文件被误删；
- 重新统计后的目录占用符合预期。
