# Report Claim Manifest v1

Claim Manifest 把长篇 Markdown 中的关键结论变成可校验记录。Markdown 继续面向读者；manifest 负责列出 Evidence ID、来源指纹、假设、失效条件和 runtime 状态。

## 文件

```text
schemas/report_claim_manifest_v1.schema.json
reports/manifests/<report>.claims.json
reports/evidence_manifests/<evidence-set>.native.json
scripts/validate_report_claims.py
```

完整 `native_evidence/` 仍然是本机 ignored 产物。仓库只提交 sanitized manifest，其中可以包含 binary/PDB/recipe/generator 指纹、目标 Evidence ID 和 gap，但不得包含 DLL/PDB、完整反编译文本或本机绝对路径。

## Claim 最小字段

```json
{
  "claimId": "claim://example/quality-range",
  "summary": "规范化结论",
  "status": "STATIC_REVERSED",
  "confidence": "HIGH",
  "evidenceRefs": ["native://..."],
  "assumptions": [],
  "sourceFingerprints": {
    "nativeEvidenceSetId": "native-set://...",
    "binarySha256": "...",
    "recipeSha256": "..."
  },
  "invalidationConditions": [
    "binary sha changed",
    "recipe changed",
    "generator changed"
  ],
  "reportMarkers": ["报告中必须存在的短文本"],
  "runtimeValidation": {
    "status": "NOT_RUN",
    "observationRefs": []
  }
}
```

`reportMarkers` 不是 Evidence，它用于检测报告正文和 manifest 是否已经分叉。Evidence 必须来自 `evidenceRefs`。

## 验证

验证所有 committed manifests：

```powershell
.\runtime\python\python.exe scripts\validate_report_claims.py --all --pretty
```

发布门禁：

```powershell
.\runtime\python\python.exe scripts\validate_report_claims.py `
  --all --formal --pretty
```

Validator 会检查：

- report 与 manifest 路径必须位于仓库内；
- claim ID 全局唯一；
- Evidence ID 格式与 sanitized target 存在性；
- binary/PDB/evidence-set/recipe/generator 指纹；
- report marker；
- provenance trust 与 dirty generator；
- 本机 full evidence 是否需要重建。

本机 full evidence 缺失但 committed sanitized manifest 已验证时，输出 `LOCAL_EVIDENCE_REQUIRED` warning，而不是坏链接。旧 v1 evidence 缺少 recipe 或 PDB GUID/Age 时必须标为 `PROVENANCE_INCOMPLETE`；普通审阅可以看到 warning，`--formal` 必须失败。

## 自动失效代码

```text
STALE_SOURCE
STALE_NATIVE_BUILD
STALE_RECIPE
STALE_GENERATOR
PROVENANCE_UNVERIFIED
EVIDENCE_REF_NOT_FOUND
REPORT_CLAIM_MARKER_MISSING
```

不要为了通过 validator 把未知来源改成 `CONFIRMED`。正确做法是重新运行绑定当前 DLL/PDB/recipe 的原生流水线，或把无法闭合的 claim 标为 `UNRESOLVED`。

