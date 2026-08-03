# Report Release Registry v1

`reports/report_registry.json` 是报告进入发布门槛时的显式治理清单。它不改变报告内容，也不把缺失的 provenance 伪装成已验证证据。

## 三种状态

- `ACTIVE_FORMAL`：参与当前正式发布门槛；任何 `ERROR` 都会阻断发布。
- `HISTORICAL_PROVENANCE_INCOMPLETE`：保留历史报告、claim manifest 和原始错误，但不把它们计入当前 active formal claim 集合。
- `DIAGNOSTIC`：仅供诊断或参考，不代表当前正式 claim 集合。

当前 v0.3.0 Engineering Preview 登记结果：

- `ACTIVE_FORMAL`：0 份，active errors = 0；
- `HISTORICAL_PROVENANCE_INCOMPLETE`：3 份，历史 errors = 3、warnings = 3；
- `DIAGNOSTIC`：6 份。

这 3 条历史错误仍然是 `PROVENANCE_UNVERIFIED`，没有被删除、降级为 warning 或改写成 `VERIFIED`。

## 验证

```powershell
python scripts/validate_report_registry.py --pretty
```

验证器会失败关闭地检查：

- 每一份 `reports/*.md`（`README.md` 除外）都且仅登记一次；
- 每一个 `reports/manifests/*.claims.json` 都绑定到对应报告；
- `DIAGNOSTIC` 不能携带 claim manifest 来静默隐藏错误；
- `ACTIVE_FORMAL errors = 0`；
- historical issues 必须继续出现在机器可读输出中。

原始、未分组的 claim 检查仍可运行：

```powershell
python scripts/validate_report_claims.py --all --formal --pretty
```

该命令会继续返回 3 条历史 provenance error；发布门槛使用 registry 验证器区分 active 与 historical 范围。
