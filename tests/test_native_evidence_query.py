from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "native_evidence" / "native_evidence_v2.json"
sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.native_evidence_repository import (  # noqa: E402
    open_native_evidence_repository,
)
from blueprint_translator.native_evidence_store import write_native_evidence_artifacts  # noqa: E402
from build_native_context_pack import build_native_context_pack, render_native_context_pack  # noqa: E402


INT_FUNCTION = (
    "native://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"
    "fixture.dll/0x1000"
)


class NativeEvidenceQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        source = self.root / "source.json"
        shutil.copyfile(FIXTURE, source)
        self.evidence_dir = self.root / "evidence"
        write_native_evidence_artifacts(source, self.evidence_dir)
        self.repository = open_native_evidence_repository(self.evidence_dir)

    def tearDown(self) -> None:
        self.repository.close()
        self._temporary.cleanup()

    def test_query_contract_is_bounded_and_cursor_pages_without_duplicates(self):
        overview = self.repository.query({"operation": "overview", "budgetTokens": 700})
        self.assertEqual(overview["schema"], "blueprint-to-code-native-query/v1")
        self.assertEqual(overview["requestedBudget"], 700)
        self.assertEqual(overview["effectiveBudget"], 700)
        self.assertLessEqual(overview["estimatedTokens"], 700)
        self.assertEqual(overview["evidenceSetId"], self.repository.evidence_set_id)
        self.assertEqual(overview["sourceFingerprint"], self.repository.source_sha256)
        self.assertEqual(self.repository.trust_status, "VERIFIED")
        self.assertTrue(self.repository.formal_validation)

        first = self.repository.query(
            {
                "operation": "search",
                "query": "ComputeQuality",
                "pageSize": 1,
                "budgetTokens": 700,
            }
        )
        self.assertEqual(first["returnedCount"], 1)
        self.assertGreater(first["omittedCount"], 0)
        self.assertEqual(
            first["coverage"]["byStatus"]["AVAILABLE_NOT_RETURNED"],
            first["omittedCount"],
        )
        self.assertIsNotNone(first["cursor"])
        self.assertEqual(first["nextQuery"]["cursor"], first["cursor"])

        second = self.repository.query(
            {
                "operation": "search",
                "query": "ComputeQuality",
                "pageSize": 1,
                "cursor": first["cursor"],
                "budgetTokens": 700,
            }
        )
        self.assertNotEqual(first["items"][0]["evidenceId"], second["items"][0]["evidenceId"])

    def test_function_and_relation_queries_default_to_compact_evidence(self):
        function = self.repository.query(
            {
                "operation": "function",
                "id": INT_FUNCTION,
                "budgetTokens": 1200,
            }
        )
        self.assertEqual(function["returnedCount"], 1)
        self.assertNotIn("decompiledC", function["items"][0])
        self.assertNotIn(
            "FULL_DECOMPILE_SHOULD_NOT_APPEAR_IN_INDEX",
            str(function),
        )

        with_snippet = self.repository.query(
            {
                "operation": "function",
                "id": INT_FUNCTION,
                "includeDecompile": True,
                "snippetChars": 80,
                "budgetTokens": 1200,
            }
        )
        self.assertLessEqual(len(with_snippet["items"][0]["decompileSnippet"]), 80)

        callers = self.repository.query(
            {
                "operation": "callers",
                "id": INT_FUNCTION,
                "budgetTokens": 900,
            }
        )
        self.assertEqual(callers["items"][0]["name"], "BuildReward")
        callees = self.repository.query(
            {
                "operation": "callees",
                "id": INT_FUNCTION,
                "budgetTokens": 900,
            }
        )
        self.assertEqual(callees["items"][0]["name"], "ApplyClamp")

    def test_specialized_queries_and_context_pack_preserve_gaps(self):
        operations = {
            "field-accesses": {"query": "QualityScale"},
            "constants": {"query": "quality"},
            "gaps": {},
        }
        for operation, extra in operations.items():
            with self.subTest(operation=operation):
                response = self.repository.query(
                    {
                        "operation": operation,
                        "budgetTokens": 900,
                        **extra,
                    }
                )
                self.assertGreaterEqual(response["returnedCount"], 1)
                self.assertLessEqual(response["estimatedTokens"], 900)

        gaps = self.repository.query(
            {
                "operation": "gaps",
                "reasonCode": "DECOMPILE_FAILED",
                "budgetTokens": 900,
            }
        )
        self.assertEqual(gaps["returnedCount"], 1)
        self.assertEqual(gaps["gaps"][0]["reasonCode"], "DECOMPILE_FAILED")
        self.assertEqual(
            gaps["gaps"][0]["functionEvidenceId"],
            (
                "native://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"
                "fixture.dll/0x1300"
            ),
        )
        self.assertEqual(gaps["gaps"][0]["detail"], "Fixture decompiler timeout.")

        pack = build_native_context_pack(
            self.repository,
            question="How does ComputeQuality read QualityScale and apply constants?",
            budget=1600,
        )
        rendered = render_native_context_pack(pack)
        self.assertLessEqual(estimate_tokens(rendered), 1600)
        self.assertTrue(pack["functions"])
        self.assertTrue(pack["fieldAccesses"])
        self.assertTrue(pack["constants"])
        self.assertNotIn("FULL_DECOMPILE_SHOULD_NOT_APPEAR_IN_INDEX", rendered)
        self.assertTrue(pack["gaps"])
        self.assertEqual(
            pack["sourceTrust"],
            {"status": "VERIFIED", "formalValidation": True},
        )
        self.assertEqual(pack["provenanceWarnings"], [])
        self.assertTrue(
            all(
                row["status"] == row["evidenceStatus"]
                for row in pack["functions"]
            )
        )

    def test_experimental_context_pack_downgrades_functions_and_warns(self):
        source = self.root / "experimental-source.json"
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["trust"]["status"] = "EXPERIMENTAL"
        source.write_text(json.dumps(payload), encoding="utf-8")
        evidence_dir = self.root / "experimental-evidence"
        write_native_evidence_artifacts(source, evidence_dir, formal=False)

        with open_native_evidence_repository(evidence_dir) as repository:
            self.assertEqual(repository.trust_status, "EXPERIMENTAL")
            self.assertFalse(repository.formal_validation)
            pack = build_native_context_pack(
                repository,
                question="How does ComputeQuality work?",
                budget=500,
            )

        self.assertEqual(
            pack["sourceTrust"],
            {"status": "EXPERIMENTAL", "formalValidation": False},
        )
        self.assertEqual(
            {warning["code"] for warning in pack["provenanceWarnings"]},
            {"PROVENANCE_UNVERIFIED"},
        )
        self.assertTrue(pack["functions"])
        self.assertTrue(
            all(
                row["status"] == "PROVENANCE_UNVERIFIED"
                and row["evidenceStatus"] == "CONFIRMED"
                for row in pack["functions"]
            )
        )
        rendered = render_native_context_pack(pack)
        self.assertLessEqual(estimate_tokens(rendered), 500)
        self.assertIn("EXPERIMENTAL", rendered)
        self.assertIn("PROVENANCE_UNVERIFIED", rendered)

    def test_budget_below_public_minimum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least 500"):
            self.repository.query({"operation": "overview", "budgetTokens": 499})


if __name__ == "__main__":
    unittest.main()
