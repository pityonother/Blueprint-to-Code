# ARK 机制调查报告

本目录保存基于当前本机 ARK DevKit 资产、Blueprint to Code Evidence Store
和明确标注的原生证据形成的人类可读报告。

## 当前报告

- `ARK_FISH_BASKET_CAPTURABLE_CREATURES_2026-07-26_zh.md`：鱼篓捕获资格、
  野生/已驯服限制和任务变种边界。
- `FEROX_FORCE_FLEE_MECHANISM_2026-07-26.md`：猿狐持续逃跑状态的蓝图机制。
- `FEROX_ELEMENT_CONSUMED_NO_TAMING_PROGRESS_2026-07-26.md`：元素被扣但驯服
  进度不增长的条件链。
- `FEROX_COMPLETE_GAMEPLAY_AND_GENE_TRAITS_2026-07-26.md`：猿狐整体玩法和
  五槽词条搭配。
- `tides_of_fortune_2026-07-25.md`：Tides of Fortune 本地资产调查底稿。
- `tides_of_fortune_exact_loot_2026-07-25.md`：漂流瓶六档物品池。
- `tides_of_fortune_loot_flow_player_guide_2026-07-25.md`：玩家流程说明。
- `TIDES_OF_FORTUNE_COMPLETE_NATIVE_2026-07-26.md`：蓝图与原生边界合并报告。
- `ARK_PLAYER_VISIBLE_REWARD_MODEL_DEEP_DIVE_2026-07-26.md`：品质、蓝图成本
  和玩家可见奖励模型。

## 证据边界

- 报告中的确认值绑定生成时的 DevKit 资产版本；DevKit 更新后需要重新验证。
- `CONFIRMED`、`HEURISTIC`、`SOURCE_NOT_AVAILABLE` 和运行时待验证项不能互相替代。
- `captures/`、`native_evidence/`、Ghidra 工程和报告生成中间文件是本机产物，
  不随这些 Markdown 报告提交。
- `manifests/*.claims.json` 记录关键结论、Evidence ID、假设和失效条件；
  `evidence_manifests/` 只保存可公开的 fingerprints 与 target 摘要。
- 历史原生证据缺少 recipe/generator 指纹时标记
  `PROVENANCE_INCOMPLETE`，不会伪装为正式 `VERIFIED`。

验证 committed 报告：

```powershell
runtime\python\python.exe scripts\validate_report_claims.py --all --pretty
```

正式发布使用 `--formal`。它会拒绝 incomplete/stale provenance；本机完整
evidence 缺失则显示 `LOCAL_EVIDENCE_REQUIRED`，不能用坏链接代替。
