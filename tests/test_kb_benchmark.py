from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.benchmark import (  # noqa: E402
    CATEGORY_MINIMUMS,
    DEFAULT_GOLD_SET_PATH,
    BenchmarkCase,
    build_benchmark_cases,
    build_query_case_result,
    build_query_failure_matrix,
    evaluate_benchmark_result,
    load_benchmark_gold_set,
    query_case_results_jsonl_bytes,
    query_diagnostic_artifact_bytes,
    query_failure_matrix_json_bytes,
    validate_benchmark_shape,
)
class FixedGoldCorpusTests(unittest.TestCase):
    def test_checked_in_corpus_is_fixed_and_meets_all_case_quotas(self):
        payload = load_benchmark_gold_set(DEFAULT_GOLD_SET_PATH)
        cases = payload["cases"]

        self.assertGreaterEqual(len(cases), 130)
        self.assertEqual(payload["selectionMode"], "MANUAL_FIXED")
        self.assertFalse(payload["generatedFromCore"])
        counts = Counter(case.category for case in cases)
        self.assertTrue(
            all(
                counts[category] >= minimum
                for category, minimum in CATEGORY_MINIMUMS.items()
            )
        )
        self.assertGreaterEqual(
            sum(bool(case.negative_case) for case in cases),
            20,
        )
        self.assertTrue(
            all(
                case.expected["facts"]
                or case.expected["relationships"]
                or case.expected["gapCodes"]
                or case.expected["semanticExpectation"] == "IDENTITY_ONLY"
                for case in cases
            )
        )
        identity_cases = [
            case
            for case in cases
            if case.expected["semanticExpectation"] == "IDENTITY_ONLY"
        ]
        self.assertEqual(len(identity_cases), 3)
        self.assertTrue(
            all(
                case.expected.get("identityStatus") == "EXTRACTED"
                and case.expected.get("status") == "COMPLETE"
                and case.expected.get("identityConfidence") == "HIGH"
                and case.expected.get("mustContainEvidence") is True
                and isinstance(
                    case.expected.get("identityEvidence"),
                    dict,
                )
                and case.expected["identityEvidence"].get("evidenceRole")
                == "IDENTITY_REVISION"
                and isinstance(
                    case.expected["identityEvidence"].get(
                        "sourceRevision"
                    ),
                    dict,
                )
                and case.expected["identityEvidence"].get("evidenceUri")
                == case.expected["identityEvidence"]["sourceRevision"].get(
                    "sourceUri"
                )
                and case.expected["identityEvidence"]["sourceRevision"].get(
                    "sourceKind"
                )
                == "asset_package"
                and str(
                    case.expected["identityEvidence"]["sourceRevision"].get(
                        "sourceUri"
                    )
                ).startswith("package:///Game/")
                and case.expected["identityEvidence"]["sourceRevision"].get(
                    "schemaVersion"
                )
                == "ark-asset-package/v1"
                and len(
                    str(
                        case.expected["identityEvidence"][
                            "sourceRevision"
                        ].get("sourceFingerprint")
                    )
                )
                == 64
                for case in identity_cases
            )
        )
        map_cases = [case for case in cases if case.category == "MAP"]
        self.assertEqual(len(map_cases), 10)
        self.assertTrue(
            all(
                case.expected["semanticExpectation"] == "EXACT"
                and case.expected["route"] == "DB_SEMANTIC_COMPLETE"
                and len(case.expected["relationships"]) == 1
                and case.expected["relationships"][0]["sourceUri"]
                and case.expected["relationships"][0]["targetUri"]
                == case.entity
                and case.expected["relationships"][0]["edgeType"]
                == "MAP_DIRECT_REFERENCE"
                and case.expected["relationships"][0]["evidenceUri"]
                and case.expected["relationships"][0]["freshness"]
                == "FRESH"
                and case.expected["relationships"][0][
                    "evidenceLayer"
                ]
                in {
                    "ASSET_REGISTRY_HARD_PACKAGE_DEPENDENCY",
                    "ASSET_REGISTRY_SOFT_PACKAGE_DEPENDENCY",
                }
                and case.expected["relationships"][0][
                    "claimsCompleteMapUsage"
                ]
                is False
                and case.expected["relationships"][0][
                    "claimsSpawnCoordinates"
                ]
                is False
                for case in map_cases
            )
        )
        self.assertEqual(
            {
                gap["category"]
                for gap in payload["corpusGaps"]
            },
            {"REGISTRATION", "NATIVE"},
        )
        candidate_map_boundaries = [
            case
            for case in cases
            if case.negative_case
            in {
                "pcg_reference_is_not_direct_placement",
                "world_partition_reference_is_not_usage",
            }
        ]
        self.assertEqual(len(candidate_map_boundaries), 2)
        self.assertTrue(
            all(
                case.protocol_boundary_only
                and case.request["requiresMapEvidence"]
                and case.expected["semanticExpectation"] == "GAP_ONLY"
                and case.expected["gapCodes"] == ["MAP_USAGE_INCOMPLETE"]
                for case in candidate_map_boundaries
            )
        )

    def test_build_cases_does_not_read_the_current_core(self):
        connection = sqlite3.connect(":memory:")

        def deny_reads(
            action: int,
            _arg1: str | None,
            _arg2: str | None,
            _database: str | None,
            _trigger: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_reads)
        cases = build_benchmark_cases(connection)
        connection.close()

        self.assertGreaterEqual(len(cases), 130)
        self.assertEqual(
            cases[0].request["entity"],
            cases[0].entity,
        )

    def test_validator_rejects_a_self_derived_corpus(self):
        payload = json.loads(DEFAULT_GOLD_SET_PATH.read_text(encoding="utf-8"))
        payload["generatedFromCore"] = True
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "self-derived.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "generatedFromCore"):
                load_benchmark_gold_set(path)

    def test_human_reviewed_label_must_exist_in_checked_in_trust_root(self):
        payload = json.loads(DEFAULT_GOLD_SET_PATH.read_text(encoding="utf-8"))
        unreviewed = next(
            case
            for case in payload["cases"]
            if case["reviewStatus"] == "FIXTURE_EXACT"
            and case["expected"]["facts"]
        )
        unreviewed["reviewStatus"] = "HUMAN_REVIEWED"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "false-human-review.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "trust root"):
                load_benchmark_gold_set(path)

    def test_empirical_label_requires_signed_v2_review_provenance(self):
        payload = json.loads(DEFAULT_GOLD_SET_PATH.read_text(encoding="utf-8"))
        unreviewed = next(
            case
            for case in payload["cases"]
            if case["reviewStatus"] == "FIXTURE_EXACT"
        )
        unreviewed["reviewStatus"] = "EMPIRICAL"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "false-empirical-review.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "SIGNED_V2_RECEIPTS_REQUIRED",
            ):
                load_benchmark_gold_set(path)

    def test_empirical_v1_hash_receipts_never_count_as_production_gold(
        self,
    ):
        payload = json.loads(DEFAULT_GOLD_SET_PATH.read_text(encoding="utf-8"))
        reviewed_case = next(
            case
            for case in payload["cases"]
            if case["reviewStatus"] == "FIXTURE_EXACT"
            and case["expected"]["facts"]
        )
        reviewed_case["reviewStatus"] = "EMPIRICAL"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "empirical-review.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            reviewed_case["reviewProvenance"] = {
                "schema": "ark-kb-query-review-provenance/v1",
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "SIGNED_V2_RECEIPTS_REQUIRED",
            ):
                load_benchmark_gold_set(path)

    def test_validator_rejects_a_gap_case_that_claims_semantic_success(self):
        connection = sqlite3.connect(":memory:")
        cases = build_benchmark_cases(connection)
        connection.close()
        original = cases[0]
        invalid = BenchmarkCase(
            **{
                **original.__dict__,
                "protocol_boundary_only": True,
                "expected": {
                    **original.expected,
                    "semanticExpectation": "EXACT",
                    "gapCodes": ["MISSING_FACT"],
                },
            }
        )
        with self.assertRaisesRegex(ValueError, "protocol boundary"):
            validate_benchmark_shape([invalid, *cases[1:]])

    def test_identity_only_gold_requires_explicit_evidence_contract(self):
        payload = json.loads(
            DEFAULT_GOLD_SET_PATH.read_text(encoding="utf-8")
        )
        identity_case = next(
            case
            for case in payload["cases"]
            if case["expected"]["semanticExpectation"] == "IDENTITY_ONLY"
        )
        identity_case["expected"].pop("identityEvidence")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "identity-without-evidence.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "identityEvidence"):
                load_benchmark_gold_set(path)

    def test_identity_only_gold_rejects_incomplete_status_or_confidence(self):
        original = json.loads(
            DEFAULT_GOLD_SET_PATH.read_text(encoding="utf-8")
        )
        identity_index = next(
            index
            for index, case in enumerate(original["cases"])
            if case["expected"]["semanticExpectation"] == "IDENTITY_ONLY"
        )
        mutations = (
            ("identityStatus", "AMBIGUOUS", "status is not complete"),
            ("identityConfidence", "LOW", "confidence is not high"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for field, value, message in mutations:
                with self.subTest(field=field, value=value):
                    payload = deepcopy(original)
                    payload["cases"][identity_index]["expected"][field] = value
                    path = Path(temporary) / f"identity-{field}.json"
                    path.write_text(
                        json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        load_benchmark_gold_set(path)

    def test_exact_fact_gold_rejects_candidate_status(self):
        payload = json.loads(
            DEFAULT_GOLD_SET_PATH.read_text(encoding="utf-8")
        )
        fact_case = next(
            case
            for case in payload["cases"]
            if case["expected"]["facts"]
        )
        fact_case["expected"]["facts"][0]["status"] = "CANDIDATE"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "candidate-fact.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "status is not complete"):
                load_benchmark_gold_set(path)


class BenchmarkAnswerClassificationTests(unittest.TestCase):
    def _fact_case(self) -> BenchmarkCase:
        return BenchmarkCase(
            query_id="fact-fixed-001",
            question="What is the fixed weight?",
            category="FACT",
            primary_domain="item_use",
            entity="/Game/Gold/Item.Item",
            request={
                "entity": "/Game/Gold/Item.Item",
                "answerMode": "FACT",
                "factTypes": ["ITEM_PROPERTY"],
                "factNames": ["BaseItemWeight"],
                "edgeTypes": [],
                "requiresNative": False,
                "requiresRuntime": False,
                "requiresMapEvidence": False,
                "evidenceLimit": 50,
                "budgetTokens": 2_000,
            },
            expected={
                "route": "DB_SEMANTIC_COMPLETE",
                "identityUri": "/Game/Gold/Item.Item",
                "facts": [
                    {
                        "factType": "ITEM_PROPERTY",
                        "factName": "BaseItemWeight",
                        "valueKind": "NUMBER",
                        "value": 5.0,
                        "status": "CONFIRMED",
                        "evidenceUri": "bp://gold/default/BaseItemWeight",
                    }
                ],
                "relationships": [],
                "gapCodes": [],
                "mustContainEvidence": True,
                "semanticExpectation": "EXACT",
            },
            review_status="HUMAN_REVIEWED",
            protocol_boundary_only=False,
            negative_case="",
            performance_path="EXACT_CANONICAL_URI",
        )

    def _complete_fact_result(self) -> dict[str, object]:
        revision = {
            "revisionId": 1,
            "sourceKind": "blueprint_evidence",
            "sourceUri": "bp://gold/revision",
            "sourceFingerprint": "gold-sha",
            "producerVersion": "gold-fixture-v1",
            "schemaVersion": "ark.blueprint.evidence.v2",
            "generatedAt": "2026-07-27T00:00:00Z",
            "freshness": "FRESH",
        }
        return {
            "answerMode": "FACT",
            "status": "COMPLETE",
            "route": "DB_SEMANTIC_COMPLETE",
            "entity": {"canonicalUri": "/Game/Gold/Item.Item"},
            "entityCandidates": [],
            "facts": [
                {
                    "factId": 7,
                    "factType": "ITEM_PROPERTY",
                    "factName": "BaseItemWeight",
                    "valueKind": "NUMBER",
                    "valueText": None,
                    "valueNumber": 5.0,
                    "valueInteger": None,
                    "valueJson": None,
                    "status": "CONFIRMED",
                    "confidence": "HIGH",
                }
            ],
            "relationships": [],
            "evidence": [
                {
                    "entityId": 1,
                    "canonicalUri": "/Game/Gold/Item.Item",
                    "evidenceUri": "bp://gold/identity/Item",
                    "evidenceRole": "IDENTITY_REVISION",
                    "sourceRevisionId": 1,
                    "sourceRevision": revision,
                    "freshness": "FRESH",
                },
                {
                    "factId": 7,
                    "evidenceUri": "bp://gold/default/BaseItemWeight",
                    "sourceRevisionId": 1,
                    "sourceRevision": revision,
                    "freshness": "FRESH",
                }
            ],
            "freshness": "FRESH",
            "missingRequirements": [],
            "recommendedProbes": [],
        }

    def _identity_case(self) -> BenchmarkCase:
        original = self._fact_case()
        return BenchmarkCase(
            **{
                **original.__dict__,
                "request": {
                    **original.request,
                    "answerMode": "IDENTITY",
                    "factTypes": [],
                    "factNames": [],
                },
                "expected": {
                    "route": "IDENTITY_ONLY_COMPLETE",
                    "status": "COMPLETE",
                    "identityUri": original.entity,
                    "identityStatus": "CONFIRMED",
                    "identityConfidence": "HIGH",
                    "identityEvidence": {
                        "evidenceUri": "bp://gold/revision",
                        "evidenceRole": "IDENTITY_REVISION",
                        "freshness": "FRESH",
                        "sourceRevision": {
                            "sourceKind": "blueprint_evidence",
                            "sourceUri": "bp://gold/revision",
                            "sourceFingerprint": "gold-sha",
                            "producerVersion": "gold-fixture-v1",
                            "schemaVersion": "ark.blueprint.evidence.v2",
                            "freshness": "FRESH",
                        },
                    },
                    "facts": [],
                    "relationships": [],
                    "gapCodes": [],
                    "mustContainEvidence": True,
                    "semanticExpectation": "IDENTITY_ONLY",
                },
            }
        )

    def _complete_identity_result(self) -> dict[str, object]:
        result = self._complete_fact_result()
        identity_evidence = deepcopy(result["evidence"][0])
        identity_evidence["evidenceUri"] = "bp://gold/revision"
        revision = deepcopy(identity_evidence["sourceRevision"])
        result.update(
            {
                "answerMode": "IDENTITY",
                "route": "IDENTITY_ONLY_COMPLETE",
                "entity": {
                    "canonicalUri": "/Game/Gold/Item.Item",
                    "status": "CONFIRMED",
                    "confidence": "HIGH",
                    "sourceRevision": revision,
                    "freshness": "FRESH",
                },
                "facts": [],
                "evidence": [identity_evidence],
            }
        )
        return result

    def _gap_case_and_result(
        self,
    ) -> tuple[BenchmarkCase, dict[str, object]]:
        original = self._fact_case()
        case = BenchmarkCase(
            **{
                **original.__dict__,
                "protocol_boundary_only": True,
                "expected": {
                    "route": "EVIDENCE_REQUIRED",
                    "identityUri": original.entity,
                    "facts": [],
                    "relationships": [],
                    "gapCodes": ["MISSING_FACT"],
                    "mustContainEvidence": False,
                    "semanticExpectation": "GAP_ONLY",
                },
            }
        )
        result = self._complete_fact_result()
        result.update(
            {
                "status": "GAP",
                "route": "EVIDENCE_REQUIRED",
                "facts": [],
                "evidence": [],
                "freshness": "UNKNOWN",
                "missingRequirements": [
                    {
                        "code": "MISSING_FACT",
                        "requirement": "ITEM_PROPERTY:BaseItemWeight",
                    }
                ],
                "recommendedProbes": [
                    {
                        "probeType": "blueprint_evidence_query",
                        "asset": original.entity,
                        "operation": "named_fact",
                        "budgetTokens": 1500,
                        "reason": "MISSING_FACT",
                    }
                ],
            }
        )
        return case, result

    def test_exact_typed_fact_is_semantic_usable_and_evidence_backed(self):
        classified = evaluate_benchmark_result(
            self._fact_case(),
            self._complete_fact_result(),
        )
        self.assertTrue(classified["protocolCompliance"])
        self.assertTrue(classified["identityAnswer"])
        self.assertTrue(classified["semanticAnswer"])
        self.assertTrue(classified["usableValue"])
        self.assertTrue(classified["evidenceBackedComplete"])
        self.assertFalse(classified["gapOnly"])
        self.assertFalse(classified["wrongAnswer"])

    def test_gap_and_probe_do_not_comply_with_exact_gold(self):
        result = self._complete_fact_result()
        result.update(
            {
                "status": "GAP",
                "route": "EVIDENCE_REQUIRED",
                "facts": [],
                "evidence": [],
                "freshness": "UNKNOWN",
                "missingRequirements": [
                    {
                        "code": "MISSING_FACT",
                        "requirement": (
                            "ITEM_PROPERTY:BaseItemWeight"
                        ),
                    }
                ],
                "recommendedProbes": [{"probeType": "blueprint_evidence_query"}],
            }
        )
        classified = evaluate_benchmark_result(self._fact_case(), result)
        self.assertFalse(classified["protocolCompliance"])
        self.assertTrue(classified["gapOnly"])
        self.assertFalse(classified["semanticAnswer"])
        self.assertFalse(classified["usableValue"])
        self.assertTrue(classified["wrongAnswer"])

    def test_wrong_value_is_not_hidden_by_a_complete_route(self):
        result = self._complete_fact_result()
        result["facts"][0]["valueNumber"] = 6.0
        classified = evaluate_benchmark_result(self._fact_case(), result)
        self.assertFalse(classified["semanticAnswer"])
        self.assertTrue(classified["wrongAnswer"])

    def test_near_numeric_value_is_wrong_instead_of_exact(self):
        original = self._fact_case()
        expected_fact = {
            **original.expected["facts"][0],
            "value": 22_213_970.0,
        }
        case = BenchmarkCase(
            **{
                **original.__dict__,
                "expected": {
                    **original.expected,
                    "facts": [expected_fact],
                },
            }
        )
        result = self._complete_fact_result()
        result["facts"][0]["valueNumber"] = 22_213_970.01

        classified = evaluate_benchmark_result(case, result)

        self.assertFalse(classified["semanticAnswer"])
        self.assertFalse(classified["evidenceBackedComplete"])
        self.assertTrue(classified["wrongAnswer"])

    def test_boolean_fact_rejects_non_binary_integer_payload(self):
        original = self._fact_case()
        expected_fact = {
            **original.expected["facts"][0],
            "valueKind": "BOOLEAN",
            "value": True,
        }
        case = BenchmarkCase(
            **{
                **original.__dict__,
                "expected": {
                    **original.expected,
                    "facts": [expected_fact],
                },
            }
        )
        result = self._complete_fact_result()
        result["facts"][0].update(
            {
                "valueKind": "BOOLEAN",
                "valueNumber": None,
                "valueInteger": 2,
            }
        )

        classified = evaluate_benchmark_result(case, result)

        self.assertEqual(
            {
                "semanticAnswer": classified["semanticAnswer"],
                "usableValue": classified["usableValue"],
                "evidenceBackedComplete": classified[
                    "evidenceBackedComplete"
                ],
                "wrongAnswer": classified["wrongAnswer"],
            },
            {
                "semanticAnswer": False,
                "usableValue": False,
                "evidenceBackedComplete": False,
                "wrongAnswer": True,
            },
        )

    def test_wrong_partial_claim_is_noncompliant_and_wrong_for_exact_gold(self):
        result = self._complete_fact_result()
        result.update(
            {
                "status": "PARTIAL",
                "route": "DB_PARTIAL",
                "missingRequirements": [
                    {"code": "FACT_NOT_FOUND"}
                ],
                "recommendedProbes": [
                    {"probeType": "blueprint_evidence_query"}
                ],
            }
        )
        result["facts"][0]["valueNumber"] = 6.0

        classified = evaluate_benchmark_result(self._fact_case(), result)

        self.assertFalse(classified["protocolCompliance"])
        self.assertFalse(classified["semanticAnswer"])
        self.assertTrue(classified["wrongAnswer"])

    def test_stale_complete_fact_is_counted_as_a_stale_leak(self):
        result = self._complete_fact_result()
        result["freshness"] = "STALE"
        result["evidence"][0]["freshness"] = "STALE"
        classified = evaluate_benchmark_result(self._fact_case(), result)
        self.assertTrue(classified["staleLeak"])
        self.assertFalse(classified["evidenceBackedComplete"])

    def test_complete_route_is_wrong_when_gold_requires_a_gap(self):
        original = self._fact_case()
        case = BenchmarkCase(
            **{
                **original.__dict__,
                "protocol_boundary_only": True,
                "expected": {
                    "route": "EVIDENCE_REQUIRED",
                    "identityUri": original.entity,
                    "facts": [],
                    "relationships": [],
                    "gapCodes": ["MISSING_FACT"],
                    "mustContainEvidence": False,
                    "semanticExpectation": "GAP_ONLY",
                },
            }
        )
        result = self._complete_fact_result()
        result["facts"] = []
        result["evidence"] = []
        classified = evaluate_benchmark_result(case, result)
        self.assertTrue(classified["wrongAnswer"])
        self.assertFalse(classified["semanticAnswer"])

    def test_expected_route_is_strict_for_every_semantic_expectation(self):
        original = self._fact_case()

        exact_result = self._complete_fact_result()
        exact_result["route"] = "IDENTITY_ONLY_COMPLETE"

        gap_case = BenchmarkCase(
            **{
                **original.__dict__,
                "protocol_boundary_only": True,
                "expected": {
                    "route": "EVIDENCE_REQUIRED",
                    "identityUri": original.entity,
                    "facts": [],
                    "relationships": [],
                    "gapCodes": ["MISSING_FACT"],
                    "mustContainEvidence": False,
                    "semanticExpectation": "GAP_ONLY",
                },
            }
        )
        gap_result = self._complete_fact_result()
        gap_result.update(
            {
                "status": "PARTIAL",
                "route": "DB_PARTIAL",
                "facts": [],
                "evidence": [],
                "missingRequirements": [
                    {
                        "code": "MISSING_FACT",
                        "requirement": (
                            "ITEM_PROPERTY:BaseItemWeight"
                        ),
                    }
                ],
                "recommendedProbes": [
                    {
                        "probeType": "blueprint_evidence_query",
                        "asset": original.entity,
                        "operation": "named_fact",
                        "budgetTokens": 1500,
                        "reason": "MISSING_FACT",
                    }
                ],
            }
        )

        identity_case = BenchmarkCase(
            **{
                **original.__dict__,
                "request": {
                    **original.request,
                    "answerMode": "IDENTITY",
                    "factTypes": [],
                    "factNames": [],
                },
                "expected": {
                    "route": "IDENTITY_ONLY_COMPLETE",
                    "identityUri": original.entity,
                    "facts": [],
                    "relationships": [],
                    "gapCodes": [],
                    "mustContainEvidence": False,
                    "semanticExpectation": "IDENTITY_ONLY",
                },
            }
        )
        identity_result = self._complete_fact_result()
        identity_result.update(
            {
                "answerMode": "IDENTITY",
                "route": "DB_SEMANTIC_COMPLETE",
                "facts": [],
                "evidence": [],
            }
        )

        for semantic_expectation, case, result in (
            ("EXACT", original, exact_result),
            ("GAP_ONLY", gap_case, gap_result),
            ("IDENTITY_ONLY", identity_case, identity_result),
        ):
            with self.subTest(semanticExpectation=semantic_expectation):
                classified = evaluate_benchmark_result(case, result)
                self.assertFalse(classified["protocolCompliance"])
                self.assertTrue(classified["wrongAnswer"])

    def test_gap_gold_rejects_additional_gap_or_probe(self):
        original = self._fact_case()
        case = BenchmarkCase(
            **{
                **original.__dict__,
                "protocol_boundary_only": True,
                "expected": {
                    "route": "EVIDENCE_REQUIRED",
                    "identityUri": original.entity,
                    "facts": [],
                    "relationships": [],
                    "gapCodes": ["MISSING_FACT"],
                    "mustContainEvidence": False,
                    "semanticExpectation": "GAP_ONLY",
                },
            }
        )
        baseline = self._complete_fact_result()
        baseline.update(
            {
                "status": "GAP",
                "route": "EVIDENCE_REQUIRED",
                "facts": [],
                "evidence": [],
                "missingRequirements": [
                    {
                        "code": "MISSING_FACT",
                        "requirement": (
                            "ITEM_PROPERTY:BaseItemWeight"
                        ),
                    }
                ],
                "recommendedProbes": [
                    {
                        "probeType": "blueprint_evidence_query",
                        "asset": original.entity,
                        "operation": "named_fact",
                        "budgetTokens": 1500,
                        "reason": "MISSING_FACT",
                    }
                ],
            }
        )
        extra_gap = deepcopy(baseline)
        extra_gap["missingRequirements"].append({"code": "FACT_STALE"})
        extra_gap["recommendedProbes"].append(
            {
                "probeType": "blueprint_evidence_query",
                "asset": original.entity,
                "operation": "named_fact",
                "budgetTokens": 1500,
                "reason": "FACT_STALE",
            }
        )
        extra_probe = deepcopy(baseline)
        extra_probe["recommendedProbes"].append(
            {
                "probeType": "blueprint_evidence_query",
                "asset": original.entity,
                "operation": "named_fact",
                "budgetTokens": 1500,
                "reason": "MISSING_FACT",
            }
        )

        for label, result in (
            ("extra gap", extra_gap),
            ("extra probe", extra_probe),
        ):
            with self.subTest(label=label):
                classified = evaluate_benchmark_result(case, result)
                self.assertFalse(classified["protocolCompliance"])
                self.assertFalse(classified["expectedGapMatched"])
                self.assertTrue(classified["wrongAnswer"])

    def test_gap_gold_rejects_wrong_probe_contract_with_matching_reason(self):
        original = self._fact_case()
        case = BenchmarkCase(
            **{
                **original.__dict__,
                "protocol_boundary_only": True,
                "expected": {
                    "route": "EVIDENCE_REQUIRED",
                    "identityUri": original.entity,
                    "facts": [],
                    "relationships": [],
                    "gapCodes": ["MISSING_FACT"],
                    "mustContainEvidence": False,
                    "semanticExpectation": "GAP_ONLY",
                },
            }
        )
        result = self._complete_fact_result()
        result.update(
            {
                "status": "GAP",
                "route": "EVIDENCE_REQUIRED",
                "facts": [],
                "evidence": [],
                "missingRequirements": [
                    {
                        "code": "MISSING_FACT",
                        "requirement": (
                            "ITEM_PROPERTY:BaseItemWeight"
                        ),
                    }
                ],
                "recommendedProbes": [
                    {
                        "probeType": "totally_wrong_probe",
                        "asset": "/Game/Wrong/Wrong.Wrong",
                        "operation": "destroy_everything",
                        "budgetTokens": 1,
                        "reason": "MISSING_FACT",
                    }
                ],
            }
        )

        classified = evaluate_benchmark_result(case, result)

        self.assertFalse(classified["protocolCompliance"])
        self.assertFalse(classified["expectedGapMatched"])
        self.assertTrue(classified["wrongAnswer"])

    def test_gap_gold_rejects_wrong_requirement_text(self):
        case, result = self._gap_case_and_result()
        result["missingRequirements"][0]["requirement"] = "anything non-empty"

        classified = evaluate_benchmark_result(case, result)

        self.assertFalse(classified["protocolCompliance"])
        self.assertFalse(classified["expectedGapMatched"])
        self.assertTrue(classified["wrongAnswer"])

    def test_gap_gold_rejects_wrong_status_for_the_expected_route(self):
        case, result = self._gap_case_and_result()
        result["status"] = "PARTIAL"

        classified = evaluate_benchmark_result(case, result)

        self.assertFalse(classified["protocolCompliance"])
        self.assertFalse(classified["expectedGapMatched"])
        self.assertTrue(classified["wrongAnswer"])

    def test_gap_gold_requires_the_known_gold_identity(self):
        case, valid = self._gap_case_and_result()
        invalid_results = (
            ("missing", {**valid, "entity": None}),
            (
                "mismatch",
                {
                    **valid,
                    "entity": {"canonicalUri": "/Game/Wrong/Wrong.Wrong"},
                },
            ),
        )

        for label, result in invalid_results:
            with self.subTest(label=label):
                classified = evaluate_benchmark_result(case, result)
                self.assertFalse(classified["protocolCompliance"])
                self.assertFalse(classified["expectedGapMatched"])
                self.assertTrue(classified["wrongAnswer"])

    def test_identity_only_requires_exact_gold_provenance_contract(self):
        case = self._identity_case()
        valid = evaluate_benchmark_result(
            case,
            self._complete_identity_result(),
        )
        self.assertTrue(valid["protocolCompliance"])
        self.assertTrue(valid["identityAnswer"])
        self.assertFalse(valid["wrongAnswer"])

        invalid_results: list[tuple[str, dict[str, object]]] = []
        wrong_response_status = self._complete_identity_result()
        wrong_response_status["status"] = "PARTIAL"
        invalid_results.append(("response status", wrong_response_status))
        wrong_status = self._complete_identity_result()
        wrong_status["entity"]["status"] = "STALE"
        invalid_results.append(("status", wrong_status))
        wrong_confidence = self._complete_identity_result()
        wrong_confidence["entity"]["confidence"] = "LOW"
        invalid_results.append(("confidence", wrong_confidence))
        wrong_evidence = self._complete_identity_result()
        wrong_evidence["evidence"][0]["evidenceUri"] = "bp://wrong"
        invalid_results.append(("evidence", wrong_evidence))
        wrong_revision = self._complete_identity_result()
        wrong_revision["entity"]["sourceRevision"][
            "sourceFingerprint"
        ] = "wrong-sha"
        wrong_revision["evidence"][0]["sourceRevision"][
            "sourceFingerprint"
        ] = "wrong-sha"
        invalid_results.append(("source revision", wrong_revision))
        incomplete_revision = self._complete_identity_result()
        incomplete_revision["entity"]["sourceRevision"].pop("generatedAt")
        incomplete_revision["evidence"][0]["sourceRevision"].pop(
            "generatedAt"
        )
        invalid_results.append(
            ("incomplete source revision", incomplete_revision)
        )

        for label, result in invalid_results:
            with self.subTest(label=label):
                classified = evaluate_benchmark_result(case, result)
                self.assertFalse(classified["protocolCompliance"])
                self.assertFalse(classified["identityAnswer"])
                self.assertTrue(classified["wrongAnswer"])

    def test_gap_only_result_with_a_confirmed_fact_claim_is_wrong(self):
        original = self._fact_case()
        case = BenchmarkCase(
            **{
                **original.__dict__,
                "protocol_boundary_only": True,
                "expected": {
                    "route": "EVIDENCE_REQUIRED",
                    "identityUri": original.entity,
                    "facts": [],
                    "relationships": [],
                    "gapCodes": ["MISSING_FACT"],
                    "mustContainEvidence": False,
                    "semanticExpectation": "GAP_ONLY",
                },
            }
        )
        result = self._complete_fact_result()
        result.update(
            {
                "status": "GAP",
                "route": "EVIDENCE_REQUIRED",
                "missingRequirements": [
                    {
                        "code": "MISSING_FACT",
                        "requirement": (
                            "ITEM_PROPERTY:BaseItemWeight"
                        ),
                    }
                ],
                "recommendedProbes": [
                    {
                        "probeType": "blueprint_evidence_query",
                        "asset": original.entity,
                        "operation": "named_fact",
                        "budgetTokens": 1500,
                        "reason": "MISSING_FACT",
                    }
                ],
            }
        )
        result["facts"][0]["valueNumber"] = 999.0

        classified = evaluate_benchmark_result(case, result)

        self.assertTrue(classified["protocolCompliance"])
        self.assertTrue(classified["gapOnly"])
        self.assertTrue(classified["wrongAnswer"])

    def test_exact_complete_requires_fresh_identity_evidence(self):
        result = self._complete_fact_result()
        result["evidence"] = [
            item
            for item in result["evidence"]
            if item.get("evidenceRole") != "IDENTITY_REVISION"
        ]

        classified = evaluate_benchmark_result(
            self._fact_case(),
            result,
        )

        self.assertTrue(classified["semanticAnswer"])
        self.assertFalse(classified["evidenceBackedComplete"])

    def test_candidate_relationship_cannot_satisfy_complete(self):
        case = BenchmarkCase(
            query_id="relationship-fixed-001",
            question="Which component is owned?",
            category="RELATIONSHIP",
            primary_domain="harvest",
            entity="/Game/Gold/Creature.Creature",
            request={
                "entity": "/Game/Gold/Creature.Creature",
                "answerMode": "RELATIONSHIP",
                "factTypes": [],
                "factNames": [],
                "edgeTypes": ["OWNS_COMPONENT"],
                "requiresNative": False,
                "requiresRuntime": False,
                "requiresMapEvidence": False,
                "evidenceLimit": 50,
                "budgetTokens": 2_000,
            },
            expected={
                "route": "DB_SEMANTIC_COMPLETE",
                "identityUri": "/Game/Gold/Creature.Creature",
                "facts": [],
                "relationships": [
                    {
                        "edgeType": "OWNS_COMPONENT",
                        "targetUri": "/Game/Gold/Component.Component_C",
                        "status": "CONFIRMED",
                        "evidenceUri": "bp://gold/default/Component",
                    }
                ],
                "gapCodes": [],
                "mustContainEvidence": True,
                "semanticExpectation": "EXACT",
            },
            review_status="HUMAN_REVIEWED",
            protocol_boundary_only=False,
            negative_case="candidate_edge_is_not_complete",
            performance_path="",
        )
        result = {
            "answerMode": "RELATIONSHIP",
            "status": "COMPLETE",
            "route": "DB_SEMANTIC_COMPLETE",
            "entity": {"canonicalUri": case.entity},
            "entityCandidates": [],
            "facts": [],
            "relationships": [
                {
                    "edgeType": "OWNS_COMPONENT",
                    "targetUri": "/Game/Gold/Component.Component_C",
                    "status": "CANDIDATE",
                    "freshness": "FRESH",
                    "evidenceUri": "bp://gold/default/Component",
                }
            ],
            "evidence": [],
            "freshness": "FRESH",
            "missingRequirements": [],
            "recommendedProbes": [],
        }
        classified = evaluate_benchmark_result(case, result)
        self.assertTrue(classified["candidateEdgeComplete"])
        self.assertFalse(classified["semanticAnswer"])
        self.assertTrue(classified["wrongAnswer"])

    def test_reviewed_ambiguous_negative_is_not_unexpected_ambiguity(self):
        payload = load_benchmark_gold_set(DEFAULT_GOLD_SET_PATH)
        case = next(
            item
            for item in payload["cases"]
            if "AMBIGUOUS_ENTITY" in item.expected["gapCodes"]
        )
        result = {
            "answerMode": case.request["answerMode"],
            "status": "AMBIGUOUS",
            "route": "AMBIGUOUS",
            "entity": None,
            "entityCandidates": [
                {"canonicalUri": "/Game/Test/A.A"},
                {"canonicalUri": "/Game/Test/B.B"},
            ],
            "facts": [],
            "relationships": [],
            "evidence": [],
            "freshness": "UNKNOWN",
            "missingRequirements": [
                {
                    "code": "AMBIGUOUS_ENTITY",
                    "requirement": "unique entity",
                }
            ],
            "recommendedProbes": [
                {
                    "probeType": "entity_disambiguation",
                    "operation": "choose_canonical_identity",
                    "budgetTokens": 500,
                    "reason": "AMBIGUOUS_ENTITY",
                }
            ],
        }

        classified = evaluate_benchmark_result(case, result)

        self.assertTrue(classified["protocolCompliance"])
        self.assertTrue(classified["expectedAmbiguousAnswer"])
        self.assertFalse(classified["ambiguousAnswer"])

    def test_map_gold_checks_source_target_evidence_and_freshness(self):
        payload = load_benchmark_gold_set(DEFAULT_GOLD_SET_PATH)
        case = next(
            item for item in payload["cases"] if item.category == "MAP"
        )
        expected = case.expected["relationships"][0]
        revision = {
            "revisionId": 1,
            "sourceKind": "map_evidence",
            "sourceUri": "map-evidence://gold/revision",
            "sourceFingerprint": "gold-map-sha",
            "producerVersion": "gold-map-fixture-v1",
            "schemaVersion": "ark-kb-map-evidence/v1",
            "generatedAt": "2026-07-27T00:00:00Z",
            "freshness": "FRESH",
        }
        result = {
            "answerMode": "MECHANISM",
            "status": "COMPLETE",
            "route": "DB_SEMANTIC_COMPLETE",
            "entity": {"canonicalUri": case.entity},
            "entityCandidates": [],
            "facts": [],
            "relationships": [
                {
                    **expected,
                    "confidence": "HIGH",
                    "sourceRevisionId": 1,
                    "sourceRevision": revision,
                    "evidence": [
                        {
                            "evidenceUri": expected["evidenceUri"],
                            "sourceRevisionId": 1,
                            "sourceRevision": revision,
                            "freshness": "FRESH",
                        }
                    ],
                }
            ],
            "evidence": [
                {
                    "entityId": 1,
                    "canonicalUri": case.entity,
                    "evidenceUri": "map-evidence://gold/identity",
                    "evidenceRole": "IDENTITY_REVISION",
                    "sourceRevisionId": 1,
                    "sourceRevision": revision,
                    "freshness": "FRESH",
                }
            ],
            "freshness": "FRESH",
            "missingRequirements": [],
            "recommendedProbes": [],
        }
        classified = evaluate_benchmark_result(case, result)
        self.assertTrue(classified["semanticAnswer"])
        self.assertTrue(classified["evidenceBackedComplete"])

        wrong_source = deepcopy(result)
        wrong_source["relationships"][0]["sourceUri"] = (
            "/Game/Maps/Wrong.Wrong"
        )
        self.assertFalse(
            evaluate_benchmark_result(case, wrong_source)["semanticAnswer"]
        )

        stale = deepcopy(result)
        stale["relationships"][0]["freshness"] = "STALE"
        stale["relationships"][0]["evidence"][0]["freshness"] = "STALE"
        stale_classified = evaluate_benchmark_result(case, stale)
        self.assertTrue(stale_classified["staleLeak"])
        self.assertFalse(stale_classified["semanticAnswer"])

        missing_claim = deepcopy(result)
        del missing_claim["relationships"][0]["claimsCompleteMapUsage"]
        self.assertFalse(
            evaluate_benchmark_result(
                case,
                missing_claim,
            )["semanticAnswer"]
        )

        missing_revision = deepcopy(result)
        del missing_revision["relationships"][0]["sourceRevision"]
        self.assertFalse(
            evaluate_benchmark_result(
                case,
                missing_revision,
            )["semanticAnswer"]
        )


class QueryCaseDiagnosticTests(unittest.TestCase):
    def _fixtures(
        self,
    ) -> tuple[BenchmarkCase, dict[str, object]]:
        fixtures = BenchmarkAnswerClassificationTests()
        return fixtures._fact_case(), fixtures._complete_fact_result()

    def test_exact_case_records_auditable_expected_actual_and_latency(self):
        case, result = self._fixtures()

        diagnostic = build_query_case_result(
            case,
            result,
            latency_spans_ms={
                "planner": 1.23456,
                "contextSerialization": 0.34567,
                "total": 1.58023,
            },
        )

        self.assertEqual(
            diagnostic["schema"],
            "ark-kb-query-case-result/v1",
        )
        self.assertEqual(diagnostic["caseId"], case.query_id)
        self.assertEqual(diagnostic["category"], "FACT")
        self.assertEqual(diagnostic["domain"], "item_use")
        self.assertEqual(
            diagnostic["expected"]["route"],
            "DB_SEMANTIC_COMPLETE",
        )
        self.assertEqual(
            diagnostic["actual"]["route"],
            "DB_SEMANTIC_COMPLETE",
        )
        self.assertEqual(
            diagnostic["expected"]["identity"],
            "/Game/Gold/Item.Item",
        )
        self.assertEqual(
            diagnostic["actual"]["identity"],
            "/Game/Gold/Item.Item",
        )
        self.assertEqual(diagnostic["factDiff"]["missing"], [])
        self.assertEqual(diagnostic["factDiff"]["extra"], [])
        self.assertEqual(diagnostic["factDiff"]["wrongValues"], [])
        self.assertEqual(
            diagnostic["relationshipDiff"],
            {"missing": [], "extra": [], "wrong": []},
        )
        self.assertEqual(diagnostic["evidenceUriMismatch"]["missing"], [])
        self.assertEqual(diagnostic["protocolViolations"], [])
        self.assertEqual(
            diagnostic["latencySpansMs"],
            {
                "contextSerialization": 0.346,
                "planner": 1.235,
                "total": 1.58,
            },
        )
        self.assertEqual(diagnostic["failureClass"], "PASS")
        self.assertEqual(diagnostic["failureClasses"], [])
        self.assertTrue(diagnostic["semanticAnswer"])

    def test_wrong_fact_value_is_explicit_and_deterministically_classified(self):
        case, result = self._fixtures()
        result["facts"][0]["valueNumber"] = 5.5

        diagnostic = build_query_case_result(
            case,
            result,
            latency_spans_ms={"total": 2.0},
        )

        self.assertEqual(diagnostic["factDiff"]["missing"], [])
        self.assertEqual(diagnostic["factDiff"]["extra"], [])
        self.assertEqual(
            diagnostic["factDiff"]["wrongValues"],
            [
                {
                    "factType": "ITEM_PROPERTY",
                    "factName": "BaseItemWeight",
                    "fields": {
                        "value": {
                            "expected": 5.0,
                            "actual": 5.5,
                        }
                    },
                }
            ],
        )
        self.assertIn("WRONG_ANSWER", diagnostic["failureClasses"])
        self.assertIn("SEMANTIC_MISMATCH", diagnostic["failureClasses"])
        self.assertEqual(diagnostic["failureClass"], "WRONG_ANSWER")

    def test_protocol_and_leak_details_use_a_stable_priority(self):
        case, result = self._fixtures()
        result["answerMode"] = "RELATIONSHIP"
        result["facts"][0]["status"] = "CANDIDATE"
        result["facts"][0]["freshness"] = "STALE"
        result["evidence"][1]["evidenceUri"] = "existing-kb://legacy/fact"

        diagnostic = build_query_case_result(
            case,
            result,
            latency_spans_ms={"total": 3.0},
        )

        self.assertEqual(
            diagnostic["protocolViolations"],
            ["ANSWER_MODE_MISMATCH"],
        )
        self.assertEqual(
            diagnostic["leakage"],
            {
                "stale": True,
                "candidate": True,
                "legacy": True,
            },
        )
        self.assertEqual(
            diagnostic["failureClasses"],
            [
                "PROTOCOL_VIOLATION",
                "WRONG_ANSWER",
                "STALE_LEAKAGE",
                "CANDIDATE_LEAKAGE",
                "LEGACY_LEAKAGE",
                "SEMANTIC_MISMATCH",
                "EVIDENCE_URI_MISMATCH",
            ],
        )
        self.assertEqual(
            diagnostic["failureClass"],
            "PROTOCOL_VIOLATION",
        )

    def test_failure_matrix_binds_corpus_and_case_result_digest(self):
        case, passing_result = self._fixtures()
        passing = build_query_case_result(
            case,
            passing_result,
            latency_spans_ms={"total": 1.0},
        )
        failing_result = deepcopy(passing_result)
        failing_result["facts"][0]["valueNumber"] = 99.0
        failing = build_query_case_result(
            BenchmarkCase(
                **{
                    **case.__dict__,
                    "query_id": "fact-fixed-002",
                }
            ),
            failing_result,
            latency_spans_ms={"total": 2.0},
        )

        encoded = query_case_results_jsonl_bytes(
            [failing, passing]
        )
        matrix = build_query_failure_matrix(
            [failing, passing],
            build_id="fixture-build",
            corpus_sha256="a" * 64,
        )

        encoded_lines = encoded.decode("utf-8").splitlines()
        self.assertEqual(
            [
                json.loads(line)["caseId"]
                for line in encoded_lines
            ],
            ["fact-fixed-001", "fact-fixed-002"],
        )
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(
            matrix["schema"],
            "ark-kb-query-failure-matrix/v1",
        )
        self.assertEqual(matrix["buildId"], "fixture-build")
        self.assertEqual(
            matrix["corpus"],
            {"sha256": "a" * 64, "caseCount": 2},
        )
        self.assertEqual(
            matrix["caseResults"]["sha256"],
            __import__("hashlib").sha256(encoded).hexdigest(),
        )
        self.assertEqual(
            matrix["totals"],
            {"cases": 2, "passing": 1, "failing": 1},
        )
        self.assertEqual(
            matrix["primaryFailureClassCounts"],
            {"PASS": 1, "WRONG_ANSWER": 1},
        )
        self.assertEqual(
            matrix["failureClassCounts"],
            {"SEMANTIC_MISMATCH": 1, "WRONG_ANSWER": 1},
        )
        self.assertEqual(
            matrix["byCategory"]["FACT"]["failing"],
            1,
        )
        self.assertEqual(
            matrix["byDomain"]["item_use"]["total"],
            2,
        )
        self.assertEqual(
            matrix["failures"][0]["caseId"],
            "fact-fixed-002",
        )

    def test_jsonl_rejects_duplicate_or_wrong_schema_case_records(self):
        case, result = self._fixtures()
        diagnostic = build_query_case_result(
            case,
            result,
            latency_spans_ms={"total": 1.0},
        )
        with self.assertRaisesRegex(ValueError, "duplicate caseId"):
            query_case_results_jsonl_bytes(
                [diagnostic, diagnostic]
            )
        wrong_schema = {
            **diagnostic,
            "schema": "ark-kb-query-case-result/unknown",
        }
        with self.assertRaisesRegex(ValueError, "schema"):
            query_case_results_jsonl_bytes([wrong_schema])

    def test_artifact_bundle_recomputes_and_rejects_digest_tampering(self):
        case, result = self._fixtures()
        diagnostic = build_query_case_result(
            case,
            result,
            latency_spans_ms={"total": 1.0},
        )
        case_bytes = query_case_results_jsonl_bytes([diagnostic])
        matrix = build_query_failure_matrix(
            [diagnostic],
            build_id="fixture-build",
            corpus_sha256="a" * 64,
        )
        matrix_bytes = query_failure_matrix_json_bytes(matrix)
        benchmark = {
            "schema": "ark-kb-query-benchmark/v2",
            "total": 1,
            "goldSet": {"sha256": "a" * 64},
            "results": [diagnostic],
            "diagnosticArtifacts": {
                "schema": "ark-kb-query-diagnostics/v1",
                "buildId": "fixture-build",
                "buildBinding": "SNAPSHOT_METADATA",
                "corpusSha256": "a" * 64,
                "caseResults": {
                    "schema": "ark-kb-query-case-result/v1",
                    "uri": "reports/query_case_results.jsonl",
                    "sha256": __import__("hashlib").sha256(
                        case_bytes
                    ).hexdigest(),
                    "count": 1,
                },
                "failureMatrix": {
                    "schema": "ark-kb-query-failure-matrix/v1",
                    "uri": "reports/query_failure_matrix.json",
                    "sha256": __import__("hashlib").sha256(
                        matrix_bytes
                    ).hexdigest(),
                    "caseCount": 1,
                },
            },
        }

        actual_case_bytes, actual_matrix_bytes = (
            query_diagnostic_artifact_bytes(
                benchmark,
                expected_build_id="fixture-build",
            )
        )

        self.assertEqual(actual_case_bytes, case_bytes)
        self.assertEqual(actual_matrix_bytes, matrix_bytes)
        tampered = deepcopy(benchmark)
        tampered["results"][0]["failureClass"] = "WRONG_ANSWER"
        with self.assertRaisesRegex(
            ValueError,
            "case results? digest",
        ):
            query_diagnostic_artifact_bytes(
                tampered,
                expected_build_id="fixture-build",
            )


if __name__ == "__main__":
    unittest.main()
