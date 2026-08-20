from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_status import (  # noqa: E402
    project_query_status_zh,
    project_sample_status_zh,
)


class EvidenceStatusProjectionTests(unittest.TestCase):
    def test_query_projection_preserves_formal_identity_draft_and_refusal(self):
        cases = (
            ("FORMAL_QUERY", "formal_query", True, "可正式查询"),
            ("IDENTITY_ONLY", "benchmark", True, "只识别资产身份"),
            ("UNAVAILABLE", "draft_query", True, "只能回答一部分"),
            ("UNAVAILABLE", "formal_query", False, "当前工具无法读取"),
        )
        for availability, purpose, allowed, expected in cases:
            with self.subTest(availability=availability, purpose=purpose):
                self.assertEqual(
                    project_query_status_zh(
                        availability,
                        purpose=purpose,
                        allowed=allowed,
                    ),
                    expected,
                )

    def test_sample_projection_keeps_answer_closure_independent(self):
        cases = (
            ("FORMAL_QUERY", "COMPLETE", "可正式查询"),
            ("FORMAL_QUERY", "PARTIAL", "只能回答一部分"),
            ("FORMAL_QUERY", "NOT_REVIEWED", "尚未测试"),
            ("IDENTITY_ONLY", "PARTIAL", "只识别资产身份"),
            ("UNAVAILABLE", "PARTIAL", "当前工具无法读取"),
        )
        for availability, closure, expected in cases:
            with self.subTest(availability=availability, closure=closure):
                self.assertEqual(
                    project_sample_status_zh(availability, closure),
                    expected,
                )

    def test_unknown_machine_state_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "availability"):
            project_sample_status_zh("READY", "COMPLETE")
        with self.assertRaisesRegex(ValueError, "closure"):
            project_sample_status_zh("FORMAL_QUERY", "CLOSED_EXACT")


if __name__ == "__main__":
    unittest.main()
