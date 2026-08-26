"""Re-run exact class-default queries from the fixed category benchmark.

This is a read-only regression gate.  It proves that already-authoritative
Evidence values are reachable through the public MCP domain service; it does
not claim that every category sample or every gameplay mechanism is closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.contracts import McpExecutionError, assert_path_free  # noqa: E402
from blueprint_translator.evidence_status import (  # noqa: E402
    project_sample_status_zh,
)


CONTRACT_SCHEMA = "ark.btc.category-default-fact-regression-contract.v1"
RECEIPT_SCHEMA = "ark.btc.category-default-fact-regression-receipt.v1"


class _ContextService(Protocol):
    def get_context(self, *, asset: str, goal: str) -> dict[str, object]: ...


def _validated_cases(contract: Mapping[str, object]) -> list[dict[str, object]]:
    if str(contract.get("schema") or "") != CONTRACT_SCHEMA:
        raise ValueError("unsupported default-fact regression contract schema")
    raw_cases = contract.get("cases")
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
        raise ValueError("default-fact regression cases must be an array")
    cases: list[dict[str, object]] = []
    identities: set[tuple[str, str]] = set()
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise ValueError("default-fact regression cases must be objects")
        case = dict(raw)
        asset = str(case.get("asset") or "").strip()
        goal = str(case.get("goal") or "").strip()
        expected = case.get("expected")
        if not asset or not goal or not isinstance(expected, Mapping) or not expected:
            raise ValueError("every default-fact case needs asset, goal, and expected")
        identity = (asset.casefold(), goal.casefold())
        if identity in identities:
            raise ValueError(f"duplicate default-fact case: {asset}/{goal}")
        identities.add(identity)
        cases.append(case)
    if not cases:
        raise ValueError("default-fact regression cases must contain at least one case")
    declared_count = contract.get("caseCount")
    if declared_count is not None:
        if isinstance(declared_count, bool):
            raise ValueError("default-fact regression caseCount must be an integer")
        try:
            normalized_count = int(declared_count)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "default-fact regression caseCount must be an integer"
            ) from exc
        if normalized_count != len(cases):
            raise ValueError(
                "default-fact regression caseCount does not match cases"
            )
    return cases


def _claimed_default_refs(
    sample: Mapping[str, object],
    *,
    asset_id: str,
    revision_id: str,
) -> dict[str, set[str]]:
    result = sample.get("result")
    result_map = result if isinstance(result, Mapping) else {}
    claims = result_map.get("claims")
    claim_rows = (
        claims
        if isinstance(claims, Sequence) and not isinstance(claims, (str, bytes))
        else []
    )
    refs_by_name: dict[str, set[str]] = {}
    for claim in claim_rows:
        if not isinstance(claim, Mapping):
            continue
        refs = claim.get("evidenceRefs")
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)):
            continue
        for ref in refs:
            parsed = urlsplit(str(ref or ""))
            marker = "/default/"
            if parsed.scheme != "bp" or marker not in parsed.path:
                continue
            ref_asset_id, separator, ref_revision_id = parsed.netloc.rpartition("@")
            if (
                separator != "@"
                or ref_asset_id != asset_id
                or ref_revision_id != revision_id
            ):
                raise ValueError(
                    "benchmark default claim is not bound to its Evidence revision"
                )
            name = unquote(parsed.path.split(marker, 1)[1]).casefold()
            refs_by_name.setdefault(name, set()).add(str(ref))
    return refs_by_name


def _benchmark_sample_index(
    benchmark_artifact: Mapping[str, object],
) -> dict[int, dict[str, object]]:
    if str(benchmark_artifact.get("schema") or "") != (
        "ark.btc.category-capability-benchmark.v1"
    ):
        raise ValueError("benchmark artifact uses an unsupported schema")
    raw_samples = benchmark_artifact.get("samples")
    if not isinstance(raw_samples, Sequence) or isinstance(
        raw_samples,
        (str, bytes),
    ):
        raise ValueError("benchmark artifact samples must be an array")
    samples: dict[int, dict[str, object]] = {}
    for raw in raw_samples:
        if not isinstance(raw, Mapping):
            raise ValueError("benchmark artifact samples must be objects")
        try:
            index = int(raw.get("sampleIndex"))
        except (TypeError, ValueError) as exc:
            raise ValueError("benchmark sampleIndex must be an integer") from exc
        if index in samples:
            raise ValueError(f"duplicate benchmark sampleIndex: {index}")
        samples[index] = dict(raw)
    return samples


def _validate_benchmark_bindings(
    cases: Sequence[Mapping[str, object]],
    benchmark_artifact: Mapping[str, object],
) -> dict[tuple[int, str], frozenset[str]]:
    samples = _benchmark_sample_index(benchmark_artifact)
    bindings: dict[tuple[int, str], frozenset[str]] = {}
    for case in cases:
        index = int(case["sampleIndex"])
        sample = samples.get(index)
        if sample is None:
            raise ValueError("default-fact case has no benchmark binding")
        p0 = sample.get("p0")
        p0_map = p0 if isinstance(p0, Mapping) else {}
        result = sample.get("result")
        result_map = result if isinstance(result, Mapping) else {}
        axes = sample.get("axes")
        axes_map = axes if isinstance(axes, Mapping) else {}
        if p0_map.get("ready") is not True:
            raise ValueError("benchmark sample is not READY")
        if str(result_map.get("benchmarkClosureStatus") or "") != "CLOSED_EXACT":
            raise ValueError("benchmark sample is not CLOSED_EXACT")
        if axes_map and (
            str(axes_map.get("evidenceAvailability") or "") != "FORMAL_QUERY"
            or str(axes_map.get("answerClosure") or "") != "COMPLETE"
        ):
            raise ValueError(
                "benchmark sample axes are not FORMAL_QUERY and COMPLETE"
            )
        stratum = sample.get("plannedStratum")
        stratum_map = stratum if isinstance(stratum, Mapping) else {}
        if str(stratum_map.get("code") or "") != str(case["categoryCode"]):
            raise ValueError("default-fact category does not match benchmark binding")
        evidence = sample.get("evidence")
        evidence_map = evidence if isinstance(evidence, Mapping) else {}
        asset = evidence_map.get("asset")
        asset_map = asset if isinstance(asset, Mapping) else {}
        asset_id = str(asset_map.get("assetId") or "")
        evidence_revision_id = str(asset_map.get("revisionId") or "")
        p0_revision_id = str(p0_map.get("evidenceRevisionId") or "")
        if (
            not asset_id
            or not evidence_revision_id
            or p0_revision_id != evidence_revision_id
        ):
            raise ValueError(
                "benchmark READY sample is not bound to one Evidence revision"
            )
        benchmark_asset = str(asset_map.get("name") or "")
        if not benchmark_asset:
            target_path = str(sample.get("targetPath") or "")
            benchmark_asset = target_path.rsplit(".", 1)[-1]
        if benchmark_asset.casefold() != str(case["asset"]).casefold():
            raise ValueError("default-fact asset does not match benchmark binding")
        goal_key = str(case["goal"]).casefold()
        refs_by_name = _claimed_default_refs(
            sample,
            asset_id=asset_id,
            revision_id=evidence_revision_id,
        )
        goal_refs = refs_by_name.get(goal_key)
        if not goal_refs:
            raise ValueError("default-fact goal has no benchmark claim binding")
        bindings[(index, goal_key)] = frozenset(goal_refs)
    return bindings


def _exact_default_fact(
    facts: object,
    *,
    goal: str,
) -> tuple[dict[str, object] | None, list[str]]:
    if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes)):
        return None, ["FACTS_NOT_AN_ARRAY"]
    matches = [
        dict(item)
        for item in facts
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") == "CLASS_DEFAULT"
        and str(item.get("name") or "").casefold() == goal.casefold()
    ]
    if len(matches) != 1:
        return None, ["EXACT_DEFAULT_FACT_NOT_UNIQUE"]
    return matches[0], []


def _fact_reasons(
    fact: Mapping[str, object],
    expected: Mapping[str, object],
) -> list[str]:
    reasons: list[str] = []
    if str(fact.get("status") or "") != "CONFIRMED":
        reasons.append("FACT_NOT_CONFIRMED")
    if str(fact.get("sourceValueStatus") or "") != "CONFIRMED":
        reasons.append("SOURCE_VALUE_NOT_CONFIRMED")
    if fact.get("valueUsable") is not True:
        reasons.append("FACT_VALUE_NOT_USABLE")
    evidence_ref = str(fact.get("id") or "")
    refs = fact.get("evidenceRefs")
    if (
        not evidence_ref.startswith("bp://")
        or not isinstance(refs, Sequence)
        or isinstance(refs, (str, bytes))
        or list(refs) != [evidence_ref]
    ):
        reasons.append("EXACT_EVIDENCE_REF_BINDING_INVALID")
    for key, expected_value in expected.items():
        if fact.get(str(key)) != expected_value:
            reasons.append(f"EXPECTED_{str(key).upper()}_MISMATCH")
    return sorted(set(reasons))


def run_regression(
    service: _ContextService,
    contract: Mapping[str, object],
    *,
    benchmark_artifact: Mapping[str, object],
    input_bindings: Mapping[str, object] | None = None,
) -> dict[str, object]:
    cases = _validated_cases(contract)
    benchmark_bindings = _validate_benchmark_bindings(cases, benchmark_artifact)
    rows: list[dict[str, object]] = []
    for case in cases:
        asset = str(case["asset"])
        goal = str(case["goal"])
        reasons: list[str] = []
        fact: dict[str, object] | None = None
        evidence_availability = "UNAVAILABLE"
        try:
            response = service.get_context(asset=asset, goal=goal)
        except McpExecutionError as exc:
            reasons.append(f"MCP_{exc.code}")
        except Exception:
            reasons.append("UNEXPECTED_QUERY_FAILURE")
        else:
            identity = response.get("identity")
            identity_map = identity if isinstance(identity, Mapping) else {}
            evidence = identity_map.get("evidence")
            evidence_map = evidence if isinstance(evidence, Mapping) else {}
            decision = evidence_map.get("decision")
            decision_map = decision if isinstance(decision, Mapping) else {}
            projected_availability = str(
                decision_map.get("evidenceAvailability") or ""
            )
            if projected_availability:
                evidence_availability = projected_availability
                if evidence_availability != "FORMAL_QUERY":
                    reasons.append("EVIDENCE_NOT_FORMALLY_QUERYABLE")
            fact, fact_reasons = _exact_default_fact(
                response.get("facts"),
                goal=goal,
            )
            reasons.extend(fact_reasons)
            if fact is not None:
                reasons.extend(_fact_reasons(fact, case["expected"]))
                allowed_refs = benchmark_bindings[
                    (int(case["sampleIndex"]), goal.casefold())
                ]
                if str(fact.get("id") or "") not in allowed_refs:
                    reasons.append("FACT_REF_DOES_NOT_MATCH_BENCHMARK_CLAIM")
        answer_closure = (
            "COMPLETE"
            if not reasons
            else ("PARTIAL" if fact is not None else "NOT_REVIEWED")
        )
        row: dict[str, object] = {
            "sampleIndex": int(case.get("sampleIndex") or 0),
            "categoryCode": str(case.get("categoryCode") or ""),
            "asset": asset,
            "goal": goal,
            "status": "PASS" if not reasons else "FAIL",
            "reasons": sorted(set(reasons)),
            "expected": dict(case["expected"]),
            "evidenceRef": str(fact.get("id") or "") if fact is not None else "",
            "fact": fact or {},
            "axes": {
                "evidenceAvailability": evidence_availability,
                "answerClosure": answer_closure,
            },
            "statusZh": project_sample_status_zh(
                evidence_availability,
                answer_closure,
            ),
        }
        assert_path_free(row)
        rows.append(row)
    passed = sum(1 for row in rows if row["status"] == "PASS")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS" if passed == len(rows) else "FAIL",
        "inputBindings": dict(input_bindings or {}),
        "counts": {
            "cases": len(rows),
            "passed": passed,
            "failed": len(rows) - passed,
        },
        "boundaryZh": (
            "本收据只验证分类基线中已精确闭环且已 READY 的默认值能通过 MCP 重查；"
            "不代表 62 个样本全部闭环。"
        ),
        "cases": rows,
    }
    assert_path_free(receipt)
    return receipt


def render_report_zh(receipt: Mapping[str, object]) -> str:
    counts = receipt.get("counts")
    count_map = counts if isinstance(counts, Mapping) else {}
    raw_cases = receipt.get("cases")
    cases = (
        [item for item in raw_cases if isinstance(item, Mapping)]
        if isinstance(raw_cases, Sequence) and not isinstance(raw_cases, (str, bytes))
        else []
    )
    bindings_value = receipt.get("inputBindings")
    bindings = bindings_value if isinstance(bindings_value, Mapping) else {}
    benchmark_value = bindings.get("benchmarkArtifact")
    benchmark_binding = (
        benchmark_value if isinstance(benchmark_value, Mapping) else {}
    )
    contract_value = bindings.get("contract")
    contract_binding = (
        contract_value if isinstance(contract_value, Mapping) else {}
    )
    lines = [
        "# BTC 分类样本默认值查询修复回归",
        "",
        f"- 结果：`{receipt.get('status', '')}`",
        f"- 固定查询：{count_map.get('cases', 0)}",
        f"- 通过：{count_map.get('passed', 0)}",
        f"- 失败：{count_map.get('failed', 0)}",
        "",
        f"这轮验证的是固定的 {count_map.get('cases', 0)} 个属性能查到、"
        "值可用、并带当前 `bp://` 引用；"
        "不等于 62 个样本全部闭环，也不等于执行链已经可靠恢复。",
        "",
        "## 输入绑定",
        "",
        f"- 分类基线 SHA-256：`{benchmark_binding.get('sha256', '')}`",
        f"- 固定查询合同 SHA-256：`{contract_binding.get('sha256', '')}`",
        "",
        "| 样本 | 类别 | 资产 | 属性 | 结果 | 证据 |",
        "|---:|---|---|---|---|---|",
    ]
    for row in cases:
        lines.append(
            "| {sample} | `{category}` | `{asset}` | `{goal}` | {status} | `{ref}` |".format(
                sample=row.get("sampleIndex", ""),
                category=row.get("categoryCode", ""),
                asset=row.get("asset", ""),
                goal=row.get("goal", ""),
                status=row.get("status", ""),
                ref=row.get("evidenceRef", ""),
            )
        )
    failed = [row for row in cases if row.get("status") != "PASS"]
    if failed:
        lines.extend(["", "## 失败原因", ""])
        for row in failed:
            reasons = row.get("reasons")
            reason_text = ", ".join(str(value) for value in reasons or [])
            lines.append(f"- `{row.get('asset')}/{row.get('goal')}`：{reason_text}")
    lines.append("")
    return "\n".join(lines)


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _file_binding(path: Path, payload: object | None = None) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    binding: dict[str, object] = {
        "sha256": digest.hexdigest(),
        "bytes": size,
    }
    if isinstance(payload, Mapping) and payload.get("schema"):
        binding["schema"] = str(payload["schema"])
    return binding


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--benchmark-artifact", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    contract = _load_json(args.contract)
    benchmark_artifact = _load_json(args.benchmark_artifact)
    if not isinstance(contract, Mapping) or not isinstance(
        benchmark_artifact,
        Mapping,
    ):
        raise ValueError(
            "default-fact regression contract and benchmark artifact must be objects"
        )
    receipt = run_regression(
        BlueprintService(args.capture_root),
        contract,
        benchmark_artifact=benchmark_artifact,
        input_bindings={
            "benchmarkArtifact": _file_binding(
                args.benchmark_artifact,
                benchmark_artifact,
            ),
            "contract": _file_binding(args.contract, contract),
            "verifier": _file_binding(Path(__file__).resolve()),
        },
    )
    _write_json(args.output_json, receipt)
    if args.output_markdown is not None:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(render_report_zh(receipt), encoding="utf-8")
    print(json.dumps(receipt["counts"], ensure_ascii=False, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
