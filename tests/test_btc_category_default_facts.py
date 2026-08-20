from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_btc_category_default_facts as verifier  # noqa: E402


class _FakeService:
    def __init__(self, responses: dict[tuple[str, str], dict[str, object]]) -> None:
        self.responses = responses

    def get_context(self, *, asset: str, goal: str) -> dict[str, object]:
        return self.responses[(asset, goal)]


def _fact(
    name: str,
    value: object,
    *,
    revision: str = "canonical-revision",
) -> dict[str, object]:
    ref = f"bp://asset@{revision}/default/{name}"
    return {
        "id": ref,
        "kind": "CLASS_DEFAULT",
        "name": name,
        "typeName": "IntProperty",
        "status": "CONFIRMED",
        "sourceValueStatus": "CONFIRMED",
        "valueUsable": True,
        "value": value,
        "valueExposure": "RETURNED",
        "evidenceRefs": [ref],
    }


def _benchmark_artifact(
    *,
    sample_index: int,
    category_code: str,
    asset: str,
    goal: str,
    ready: bool = True,
    closure_status: str = "CLOSED_EXACT",
) -> dict[str, object]:
    return {
        "schema": "ark.btc.category-capability-benchmark.v1",
        "samples": [
            {
                "sampleIndex": sample_index,
                "plannedStratum": {"code": category_code},
                "targetPath": f"/Game/Test/{asset}.{asset}",
                "evidence": {
                    "asset": {
                        "assetId": "asset",
                        "name": asset,
                        "revisionId": "canonical-revision",
                    }
                },
                "p0": {
                    "ready": ready,
                    "evidenceRevisionId": "canonical-revision",
                },
                "result": {
                    "benchmarkClosureStatus": closure_status,
                    "claims": [
                        {
                            "textZh": "这个默认值已经精确确认。",
                            "evidenceRefs": [
                                f"bp://asset@canonical-revision/default/{goal}"
                            ],
                        }
                    ],
                },
                "axes": {
                    "evidenceAvailability": "FORMAL_QUERY",
                    "answerClosure": "COMPLETE",
                },
            }
        ],
    }


class BtcCategoryDefaultFactVerifierTests(unittest.TestCase):
    def test_exact_fact_and_expected_projection_pass(self) -> None:
        contract = {
            "schema": "ark.btc.category-default-fact-regression-contract.v1",
            "cases": [
                {
                    "sampleIndex": 44,
                    "categoryCode": "REWARD_SPAWN_EFFECT",
                    "asset": "BP_SpawnCrate",
                    "goal": "AnimLength",
                    "expected": {"typeName": "IntProperty", "value": 6},
                }
            ],
        }
        service = _FakeService(
            {
                ("BP_SpawnCrate", "AnimLength"): {
                    "identity": {
                        "evidence": {
                            "decision": {
                                "evidenceAvailability": "FORMAL_QUERY"
                            }
                        }
                    },
                    "facts": [_fact("AnimLength", 6)],
                }
            }
        )

        receipt = verifier.run_regression(
            service,
            contract,
            benchmark_artifact=_benchmark_artifact(
                sample_index=44,
                category_code="REWARD_SPAWN_EFFECT",
                asset="BP_SpawnCrate",
                goal="AnimLength",
            ),
            input_bindings={
                "benchmarkArtifact": {"sha256": "benchmark-sha"},
                "contract": {"sha256": "contract-sha"},
            },
        )

        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["counts"], {"cases": 1, "passed": 1, "failed": 0})
        self.assertEqual(receipt["cases"][0]["evidenceRef"], _fact("AnimLength", 6)["id"])
        self.assertEqual(
            receipt["cases"][0]["axes"],
            {
                "evidenceAvailability": "FORMAL_QUERY",
                "answerClosure": "COMPLETE",
            },
        )
        self.assertEqual(
            receipt["inputBindings"]["benchmarkArtifact"]["sha256"],
            "benchmark-sha",
        )

    def test_query_fact_ref_must_match_the_exact_benchmark_claim(self) -> None:
        contract = {
            "schema": "ark.btc.category-default-fact-regression-contract.v1",
            "cases": [
                {
                    "sampleIndex": 44,
                    "categoryCode": "REWARD_SPAWN_EFFECT",
                    "asset": "BP_SpawnCrate",
                    "goal": "AnimLength",
                    "expected": {"typeName": "IntProperty", "value": 6},
                }
            ],
        }
        service = _FakeService(
            {
                ("BP_SpawnCrate", "AnimLength"): {
                    "facts": [
                        _fact("AnimLength", 6, revision="different-revision")
                    ]
                }
            }
        )

        receipt = verifier.run_regression(
            service,
            contract,
            benchmark_artifact=_benchmark_artifact(
                sample_index=44,
                category_code="REWARD_SPAWN_EFFECT",
                asset="BP_SpawnCrate",
                goal="AnimLength",
            ),
        )

        self.assertEqual(receipt["status"], "FAIL")
        self.assertIn(
            "FACT_REF_DOES_NOT_MATCH_BENCHMARK_CLAIM",
            receipt["cases"][0]["reasons"],
        )

    def test_benchmark_claim_must_bind_to_its_evidence_identity_and_revision(
        self,
    ) -> None:
        contract = {
            "schema": "ark.btc.category-default-fact-regression-contract.v1",
            "cases": [
                {
                    "sampleIndex": 44,
                    "categoryCode": "REWARD_SPAWN_EFFECT",
                    "asset": "BP_SpawnCrate",
                    "goal": "AnimLength",
                    "expected": {"typeName": "IntProperty", "value": 6},
                }
            ],
        }
        benchmark_artifact = _benchmark_artifact(
            sample_index=44,
            category_code="REWARD_SPAWN_EFFECT",
            asset="BP_SpawnCrate",
            goal="AnimLength",
        )
        benchmark_artifact["samples"][0]["result"]["claims"][0][
            "evidenceRefs"
        ] = ["bp://asset@stale-revision/default/AnimLength"]

        with self.assertRaisesRegex(ValueError, "claim|revision|Evidence"):
            verifier.run_regression(
                _FakeService({}),
                contract,
                benchmark_artifact=benchmark_artifact,
            )

    def test_missing_or_unusable_fact_fails_closed(self) -> None:
        contract = {
            "schema": "ark.btc.category-default-fact-regression-contract.v1",
            "cases": [
                {
                    "sampleIndex": 1,
                    "categoryCode": "TEST",
                    "asset": "Asset",
                    "goal": "Value",
                    "expected": {"value": 6},
                }
            ],
        }
        fact = _fact("Value", 6)
        fact["valueUsable"] = False
        service = _FakeService({("Asset", "Value"): {"facts": [fact]}})

        receipt = verifier.run_regression(
            service,
            contract,
            benchmark_artifact=_benchmark_artifact(
                sample_index=1,
                category_code="TEST",
                asset="Asset",
                goal="Value",
            ),
        )

        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["counts"]["failed"], 1)
        self.assertIn("FACT_VALUE_NOT_USABLE", receipt["cases"][0]["reasons"])

    def test_declared_case_count_must_match_cases(self) -> None:
        contract = {
            "schema": "ark.btc.category-default-fact-regression-contract.v1",
            "caseCount": 2,
            "cases": [
                {
                    "sampleIndex": 1,
                    "categoryCode": "TEST",
                    "asset": "Asset",
                    "goal": "Value",
                    "expected": {"value": 6},
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "caseCount"):
            verifier.run_regression(
                _FakeService({}),
                contract,
                benchmark_artifact=_benchmark_artifact(
                    sample_index=1,
                    category_code="TEST",
                    asset="Asset",
                    goal="Value",
                ),
            )

    def test_empty_contract_is_rejected_instead_of_passing_zero_of_zero(self) -> None:
        contract = {
            "schema": "ark.btc.category-default-fact-regression-contract.v1",
            "caseCount": 0,
            "cases": [],
        }

        with self.assertRaisesRegex(ValueError, "empty|at least one|cases"):
            verifier.run_regression(
                _FakeService({}),
                contract,
                benchmark_artifact={
                    "schema": "ark.btc.category-capability-benchmark.v1",
                    "samples": [],
                },
            )

    def test_contract_identity_must_bind_to_ready_closed_exact_benchmark_sample(
        self,
    ) -> None:
        base_case = {
            "sampleIndex": 44,
            "categoryCode": "REWARD_SPAWN_EFFECT",
            "asset": "BP_SpawnCrate",
            "goal": "AnimLength",
            "expected": {"typeName": "IntProperty", "value": 6},
        }
        benchmark_artifact = _benchmark_artifact(
            sample_index=44,
            category_code="REWARD_SPAWN_EFFECT",
            asset="BP_SpawnCrate",
            goal="AnimLength",
        )
        mismatches = {
            "sampleIndex": 999,
            "categoryCode": "FAKE_CATEGORY",
            "asset": "OtherAsset",
            "goal": "OtherDefault",
        }

        for field, wrong_value in mismatches.items():
            with self.subTest(field=field):
                contract = {
                    "schema": (
                        "ark.btc.category-default-fact-regression-contract.v1"
                    ),
                    "caseCount": 1,
                    "cases": [{**base_case, field: wrong_value}],
                }
                with self.assertRaisesRegex(
                    ValueError,
                    "benchmark|binding|READY|CLOSED_EXACT",
                ):
                    verifier.run_regression(
                        _FakeService({}),
                        contract,
                        benchmark_artifact=benchmark_artifact,
                    )

    def test_contract_rejects_sample_that_is_not_both_ready_and_closed_exact(
        self,
    ) -> None:
        contract = {
            "schema": "ark.btc.category-default-fact-regression-contract.v1",
            "caseCount": 1,
            "cases": [
                {
                    "sampleIndex": 44,
                    "categoryCode": "REWARD_SPAWN_EFFECT",
                    "asset": "BP_SpawnCrate",
                    "goal": "AnimLength",
                    "expected": {"typeName": "IntProperty", "value": 6},
                }
            ],
        }

        for ready, closure_status in (
            (False, "CLOSED_EXACT"),
            (True, "PARTIAL"),
        ):
            with self.subTest(ready=ready, closure_status=closure_status):
                with self.assertRaisesRegex(
                    ValueError,
                    "READY|CLOSED_EXACT|benchmark",
                ):
                    verifier.run_regression(
                        _FakeService({}),
                        contract,
                        benchmark_artifact=_benchmark_artifact(
                            sample_index=44,
                            category_code="REWARD_SPAWN_EFFECT",
                            asset="BP_SpawnCrate",
                            goal="AnimLength",
                            ready=ready,
                            closure_status=closure_status,
                        ),
                    )

    def test_rendered_chinese_report_keeps_benchmark_boundary(self) -> None:
        receipt = {
            "status": "PASS",
            "counts": {"cases": 1, "passed": 1, "failed": 0},
            "cases": [
                {
                    "sampleIndex": 44,
                    "categoryCode": "REWARD_SPAWN_EFFECT",
                    "asset": "BP_SpawnCrate",
                    "goal": "AnimLength",
                    "status": "PASS",
                    "evidenceRef": "bp://asset@revision/default/AnimLength",
                    "reasons": [],
                }
            ],
        }

        rendered = verifier.render_report_zh(receipt)

        self.assertIn("1 个属性能查到", rendered)
        self.assertIn("不等于 62 个样本全部闭环", rendered)
        self.assertIn("BP_SpawnCrate", rendered)


if __name__ == "__main__":
    unittest.main()
