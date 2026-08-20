from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_policy import (  # noqa: E402
    POLICY_VERSION,
    evaluate_evidence,
)
from blueprint_translator.evidence_repository import (  # noqa: E402
    ResolvedEvidenceState,
    is_release_ready_evidence,
)


def _state(**overrides: object) -> ResolvedEvidenceState:
    manifest_raw = b'{"schema":"fixture"}\n'
    index_raw = b"# fixture\n"
    values: dict[str, object] = {
        "asset_dir": Path("C:/private/Fixture_BP"),
        "database_path": Path("C:/private/Fixture_BP/evidence.sqlite"),
        "agent_index_path": Path("C:/private/Fixture_BP/agent_index.md"),
        "manifest_path": Path("C:/private/Fixture_BP/manifest.json"),
        "pointer_path": Path("C:/private/Fixture_BP/current.json"),
        "source_kind": "INDEXED_V3_CURRENT",
        "release_authority": True,
        "freshness_status": "FRESH",
        "migration_required": False,
        "manifest_sha256": "a" * 64,
        "pointer_sha256": "b" * 64,
        "database_sha256": "c" * 64,
        "database_bytes": 4096,
        "manifest_content_sha256": "d" * 64,
        "manifest_bytes": len(manifest_raw),
        "manifest_raw": manifest_raw,
        "agent_index_sha256": "e" * 64,
        "agent_index_bytes": len(index_raw),
        "agent_index_raw": index_raw,
        "semantic_fact_count": 7,
    }
    values.update(overrides)
    return ResolvedEvidenceState(**values)  # type: ignore[arg-type]


class EvidencePolicyTests(unittest.TestCase):
    def test_ready_state_matches_compatibility_predicate_for_strict_purposes(self):
        state = _state()

        self.assertEqual(POLICY_VERSION, "1")
        self.assertTrue(is_release_ready_evidence(state))
        for purpose in ("formal_query", "publish", "benchmark"):
            with self.subTest(purpose=purpose):
                decision = evaluate_evidence(state, purpose=purpose)
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.reason_code, "ALLOWED")
                self.assertEqual(decision.reason_codes, ())
                self.assertEqual(decision.evidence_availability, "FORMAL_QUERY")
                self.assertEqual(decision.public_status_zh, "可正式查询")
                self.assertEqual(len(decision.binding_digest), 64)
                self.assertNotIn("C:/private", str(decision.binding_summary))

    def test_strict_policy_blocks_each_legacy_predicate_failure(self):
        cases = (
            (
                {"source_kind": "INDEXED_V2_COMPATIBILITY"},
                "SOURCE_KIND_NOT_CURRENT",
            ),
            ({"freshness_status": "STALE"}, "EVIDENCE_STALE"),
            ({"freshness_status": "SOURCE_UNAVAILABLE"}, "SOURCE_UNAVAILABLE"),
            ({"release_authority": False}, "RELEASE_AUTHORITY_MISSING"),
            ({"migration_required": True}, "MIGRATION_REQUIRED"),
            ({"manifest_sha256": None}, "MANIFEST_BINDING_MISSING"),
            ({"pointer_sha256": None}, "POINTER_BINDING_MISSING"),
        )
        for overrides, expected in cases:
            with self.subTest(expected=expected):
                state = _state(**overrides)
                decision = evaluate_evidence(state, purpose="formal_query")
                self.assertFalse(is_release_ready_evidence(state))
                self.assertFalse(decision.allowed)
                self.assertIn(expected, decision.reason_codes)

    def test_invalid_binding_and_empty_facts_fail_closed(self):
        state = _state(
            database_sha256="not-a-hash",
            database_bytes=0,
            semantic_fact_count=0,
        )

        decision = evaluate_evidence(state, purpose="publish")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "DATABASE_BINDING_INVALID")
        self.assertIn("EVIDENCE_EMPTY", decision.reason_codes)
        self.assertEqual(decision.evidence_availability, "IDENTITY_ONLY")
        self.assertEqual(decision.public_status_zh, "只识别资产身份")

    def test_benchmark_can_classify_authoritative_identity_only_evidence(self):
        state = _state(semantic_fact_count=0)

        decision = evaluate_evidence(state, purpose="benchmark")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason_code, "ALLOWED")
        self.assertEqual(decision.evidence_availability, "IDENTITY_ONLY")
        self.assertTrue(is_release_ready_evidence(state))
        self.assertFalse(evaluate_evidence(state, purpose="formal_query").allowed)
        self.assertFalse(evaluate_evidence(state, purpose="publish").allowed)

    def test_binding_digest_is_path_independent_and_content_sensitive(self):
        first = evaluate_evidence(_state(), purpose="formal_query")
        moved = evaluate_evidence(
            _state(
                asset_dir=Path("D:/moved/Fixture_BP"),
                database_path=Path("D:/moved/evidence.sqlite"),
                manifest_path=Path("D:/moved/manifest.json"),
                pointer_path=Path("D:/moved/current.json"),
            ),
            purpose="formal_query",
        )
        changed = evaluate_evidence(
            replace(_state(), database_sha256="f" * 64),
            purpose="formal_query",
        )

        self.assertEqual(first.binding_digest, moved.binding_digest)
        self.assertNotEqual(first.binding_digest, changed.binding_digest)

    def test_non_upgradeable_gaps_are_preserved_verbatim(self):
        state = _state(
            non_upgradeable_gaps=(
                "NOT_RECOVERED",
                "HEURISTIC_ONLY",
                "BUDGET_EXHAUSTED",
            )
        )

        decision = evaluate_evidence(state, purpose="formal_query")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.non_upgradeable_gaps, state.non_upgradeable_gaps)

    def test_unknown_purpose_is_rejected_instead_of_defaulting_to_query(self):
        with self.assertRaisesRegex(ValueError, "unsupported evidence purpose"):
            evaluate_evidence(_state(), purpose="release")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
