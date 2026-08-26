#!/usr/bin/env python3
"""Build the five-file BTC acceptance delivery from one authoritative result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from os import PathLike
from pathlib import Path
from tempfile import NamedTemporaryFile

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.evidence_status import (  # noqa: E402
    project_sample_status_zh,
)
from blueprint_translator.public_paths import (  # noqa: E402
    is_public_unreal_object_path,
    public_value_is_path_free,
)


DELIVERY_SCHEMA = "ark.btc.delivery-manifest.v1"
PRIMARY_ARTIFACTS = (
    "artifact-manifest.json",
    "result.json",
    "report.md",
    "query-examples.ps1",
    "SHA256SUMS.txt",
)
COMPATIBILITY_ALIASES = {
    "btc_category_unfinished_samples_final.json": "result.json",
    "btc_category_unfinished_samples_final.md": "report.md",
    "验收报告.md": "report.md",
    "复现查询.ps1": "query-examples.ps1",
    "输入哈希.json": "artifact-manifest.json",
    "14样本矩阵.json": "result.json",
}

_ROUTE_LABELS_ZH = {
    "BLUEPRINT_EVIDENCE": "蓝图证据",
    "NATIVE_EVIDENCE": "原生代码证据",
    "DATA_ASSET_EVIDENCE": "数据资产证据",
    "CLASSIFICATION_ONLY": "仅分类身份",
}
_CLOSURE_LABELS_ZH = {
    "COMPLETE": "指定问题已闭环",
    "PARTIAL": "指定问题只能回答一部分",
    "NOT_REVIEWED": "指定问题尚未测试",
}
_NARRATIVE_FIELDS = frozenset(
    {
        "blockingGaps",
        "labelZh",
        "noteZh",
        "questionZh",
        "categoryQuestionZh",
        "textZh",
    }
)
_MACHINE_LOCAL_TEXT = re.compile(
    r"(?i)(?:[A-Z]:[\\/]|\\\\|file:|(?:^|\s)/(?:home|users|tmp|var|etc|opt|mnt|private)/)"
)
_PUBLIC_TERM_REPLACEMENTS = {
    "READY": "可正式查询",
    "FRESH": "与当前资产一致",
    "releaseAuthority": "可作为正式证据",
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _public_delivery_value_is_path_free(
    value: object,
    *,
    field_name: str = "",
) -> bool:
    """Allow canonical Unreal identities while rejecting machine-local paths."""

    if isinstance(value, PathLike):
        return False
    if isinstance(value, str):
        if is_public_unreal_object_path(value, field_name="objectPath"):
            return True
        if public_value_is_path_free(value, field_name=field_name):
            return True
        if field_name in _NARRATIVE_FIELDS:
            return _MACHINE_LOCAL_TEXT.search(value) is None
        return False
    if isinstance(value, Mapping):
        return all(
            _public_delivery_value_is_path_free(key)
            and _public_delivery_value_is_path_free(item, field_name=str(key))
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return all(
            _public_delivery_value_is_path_free(item, field_name=field_name)
            for item in value
        )
    return True


def _sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _markdown_cell(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _public_narrative_cell(value: object) -> str:
    projected = str(value or "")
    for internal, public in _PUBLIC_TERM_REPLACEMENTS.items():
        projected = projected.replace(internal, public)
    return _markdown_cell(projected)


def _sample_status_zh(sample: Mapping[str, object]) -> str:
    axes = _mapping(sample.get("axes"))
    return project_sample_status_zh(
        str(axes.get("evidenceAvailability") or "UNAVAILABLE"),
        str(axes.get("answerClosure") or "NOT_REVIEWED"),
    )


def _sample_evidence_refs(sample: Mapping[str, object]) -> list[str]:
    result = _mapping(sample.get("result"))
    refs: list[str] = []
    for claim_value in _sequence(result.get("claims")):
        claim = _mapping(claim_value)
        refs.extend(str(value) for value in _sequence(claim.get("evidenceRefs")))
    if refs:
        return list(dict.fromkeys(refs))
    evidence = _mapping(sample.get("evidence"))
    content = _mapping(evidence.get("content"))
    return list(
        dict.fromkeys(
            str(value) for value in _sequence(content.get("sampleEvidenceRefs"))
        )
    )


def _sample_conclusion(sample: Mapping[str, object]) -> str:
    result = _mapping(sample.get("result"))
    claim_texts = [
        str(_mapping(value).get("textZh") or "")
        for value in _sequence(result.get("claims"))
        if str(_mapping(value).get("textZh") or "")
    ]
    if claim_texts:
        return "；".join(claim_texts[:2])
    gaps = [str(value) for value in _sequence(result.get("blockingGaps"))]
    if gaps:
        return "缺口：" + "；".join(gaps[:2])
    return "本结果未记录可引用结论。"


def render_report_zh(result: Mapping[str, object], result_sha256: str) -> str:
    """Render the public report only from the canonical result payload."""

    samples = [
        _mapping(value)
        for value in _sequence(result.get("samples"))
        if isinstance(value, Mapping)
    ]
    availability_counts: dict[str, int] = {}
    closure_counts: dict[str, int] = {}
    for sample in samples:
        axes = _mapping(sample.get("axes"))
        availability = str(axes.get("evidenceAvailability") or "UNAVAILABLE")
        closure = str(axes.get("answerClosure") or "NOT_REVIEWED")
        availability_counts[availability] = availability_counts.get(availability, 0) + 1
        closure_counts[closure] = closure_counts.get(closure, 0) + 1

    lines = [
        "<!-- generated-from: result.json -->",
        f"<!-- result-sha256: {result_sha256} -->",
        "# BTC 分类样本验收报告",
        "",
        "这份报告把证据是否可查与指定玩家问题是否闭环分开统计。前者通过，",
        "不代表机制已经完整还原。机器字段保留在 `result.json`。",
        "",
        "## 验收摘要",
        "",
        f"- 固定样本：{len(samples)}",
        f"- 可正式查询：{availability_counts.get('FORMAL_QUERY', 0)}",
        f"- 只识别资产身份：{availability_counts.get('IDENTITY_ONLY', 0)}",
        f"- 当前工具无法读取：{availability_counts.get('UNAVAILABLE', 0)}",
        f"- 指定问题已闭环：{closure_counts.get('COMPLETE', 0)}",
        f"- 指定问题只能回答一部分：{closure_counts.get('PARTIAL', 0)}",
        f"- 指定问题尚未测试：{closure_counts.get('NOT_REVIEWED', 0)}",
        f"- 权威结果 SHA-256：`{result_sha256}`",
        "",
        "## 逐项结果",
        "",
        "| # | 类别 | 资产 | 读取路线 | 用户状态 | 问题闭环 | 玩家问题 | 结论或缺口 | 证据编号 |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]
    for sample in samples:
        stratum = _mapping(sample.get("plannedStratum"))
        route = _mapping(sample.get("route"))
        axes = _mapping(sample.get("axes"))
        result_map = _mapping(sample.get("result"))
        question = _mapping(result_map.get("question"))
        route_code = str(route.get("effectiveReader") or "")
        closure = str(axes.get("answerClosure") or "NOT_REVIEWED")
        refs = _sample_evidence_refs(sample)
        lines.append(
            "| {index} | {category} | `{asset}` | {route} | {status} | {closure} | "
            "{question} | {conclusion} | `{evidence}` |".format(
                index=_markdown_cell(sample.get("sampleIndex")),
                category=_public_narrative_cell(
                    stratum.get("labelZh") or stratum.get("code") or ""
                ),
                asset=_markdown_cell(sample.get("targetPath")),
                route=_public_narrative_cell(
                    _ROUTE_LABELS_ZH.get(route_code, "其他证据路线")
                ),
                status=_markdown_cell(_sample_status_zh(sample)),
                closure=_public_narrative_cell(
                    _CLOSURE_LABELS_ZH.get(closure, "指定问题尚未测试")
                ),
                question=_public_narrative_cell(question.get("questionZh")),
                conclusion=_public_narrative_cell(_sample_conclusion(sample)),
                evidence=_markdown_cell(refs[0] if refs else "未记录"),
            )
        )
    lines.extend(
        [
            "",
            "## 复查方式",
            "",
            "使用同目录 `query-examples.ps1` 重放查询；先核对脚本头部的结果哈希，",
            "再以 `result.json` 中的证据编号进行单项重查。文件完整性以",
            "`SHA256SUMS.txt` 为准。",
            "",
        ]
    )
    report = "\n".join(lines)
    for forbidden in ("READY", "FRESH", "releaseAuthority"):
        if forbidden in report:
            raise ValueError(f"public report leaked internal gate word: {forbidden}")
    return report


def _query_script(raw_source: str, result_sha256: str) -> str:
    normalized = raw_source.replace("\r\n", "\n").replace("\r", "\n")
    header = (
        "# Canonical query examples for result.json\n"
        f"# result-sha256: {result_sha256}\n"
        "# Compatibility source is retained for one release; this file is canonical.\n\n"
    )
    return header + normalized.lstrip("\ufeff")


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _validate_output_directory(output_dir: Path) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"output is not a directory: {output_dir}")
    if not output_dir.exists():
        return
    unexpected = sorted(
        path.name for path in output_dir.iterdir() if path.name not in PRIMARY_ARTIFACTS
    )
    if unexpected:
        raise ValueError("output directory contains unexpected files: " + ", ".join(unexpected))


def build_delivery(
    *,
    result_source: Path,
    query_source: Path,
    output_dir: Path,
) -> dict[str, object]:
    """Build or replace only the five canonical files; never remove other files."""

    result_input_raw = result_source.read_bytes()
    result_payload = json.loads(result_input_raw.decode("utf-8-sig"))
    if not isinstance(result_payload, Mapping):
        raise ValueError("result must be a JSON object")
    if not isinstance(result_payload.get("samples"), list):
        raise ValueError("result.samples must be an array")
    if not _public_delivery_value_is_path_free(result_payload):
        raise ValueError("result contains machine-local path data")
    _validate_output_directory(output_dir)

    result_raw = _json_bytes(result_payload)
    result_sha256 = _sha256(result_raw)
    report_raw = render_report_zh(result_payload, result_sha256).encode("utf-8")
    query_input_raw = query_source.read_bytes()
    query_text = query_input_raw.decode("utf-8-sig")
    query_raw = _query_script(query_text, result_sha256).encode("utf-8")

    artifacts = [
        {
            "name": "result.json",
            "role": "authoritative-result",
            "sha256": result_sha256,
            "bytes": len(result_raw),
        },
        {
            "name": "report.md",
            "role": "result-derived-public-report",
            "sha256": _sha256(report_raw),
            "bytes": len(report_raw),
            "resultSha256": result_sha256,
        },
        {
            "name": "query-examples.ps1",
            "role": "result-bound-query-examples",
            "sha256": _sha256(query_raw),
            "bytes": len(query_raw),
            "resultSha256": result_sha256,
        },
    ]
    manifest: dict[str, object] = {
        "schema": DELIVERY_SCHEMA,
        "status": "PASS",
        "resultSha256": result_sha256,
        "primaryArtifacts": list(PRIMARY_ARTIFACTS),
        "artifacts": artifacts,
        "sourceBindings": {
            "resultInput": {
                "sha256": _sha256(result_input_raw),
                "bytes": len(result_input_raw),
            },
            "queryTemplate": {
                "sha256": _sha256(query_input_raw),
                "bytes": len(query_input_raw),
            },
        },
        "compatibilityAliases": dict(COMPATIBILITY_ALIASES),
        "compatibilityPolicy": {
            "status": "PRESERVE_EXISTING_ONE_RELEASE",
            "removalNotAuthorized": True,
            "noteZh": "旧名称保留一个兼容发布周期；本次不删除、不改名。",
        },
        "checksumCoverage": [
            "artifact-manifest.json",
            "result.json",
            "report.md",
            "query-examples.ps1",
        ],
    }
    if not public_value_is_path_free(manifest):
        raise ValueError("manifest contains machine-local path data")
    manifest_raw = _json_bytes(manifest)
    checksum_rows = [
        f"# result-sha256: {result_sha256}",
        f"{_sha256(manifest_raw)}  artifact-manifest.json",
        f"{_sha256(result_raw)}  result.json",
        f"{_sha256(report_raw)}  report.md",
        f"{_sha256(query_raw)}  query-examples.ps1",
        "",
    ]
    checksums_raw = "\n".join(checksum_rows).encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "result.json": result_raw,
        "report.md": report_raw,
        "query-examples.ps1": query_raw,
        "artifact-manifest.json": manifest_raw,
        "SHA256SUMS.txt": checksums_raw,
    }
    write_order = (
        "result.json",
        "report.md",
        "query-examples.ps1",
        "artifact-manifest.json",
        "SHA256SUMS.txt",
    )
    for name in write_order:
        _atomic_write(output_dir / name, payloads[name])
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--query-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_delivery(
        result_source=args.result,
        query_source=args.query_source,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "schema": manifest["schema"],
                "status": manifest["status"],
                "resultSha256": manifest["resultSha256"],
                "outputDir": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
