from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.gold_review import (  # noqa: E402
    BLOCKED_BY_INDEPENDENT_REVIEW,
    READY_TO_FREEZE,
    GoldReviewError,
    build_query_review_pack,
    build_review_pack,
    review_content_sha256,
    validate_review_pack,
    validate_review_set,
)


SOURCE_SHA256 = "a" * 64
REVISION_SHA256 = "b" * 64


def _candidate(case_id: str = "query-001") -> dict[str, object]:
    return {
        "caseId": case_id,
        "payload": {
            "question": "What is the reviewed item weight?",
            "entity": "/Game/Test/PrimalItem_Test.PrimalItem_Test",
            "category": "FACT",
            "requirements": {
                "answerMode": "FACT",
                "factTypes": ["ITEM_PROPERTY"],
                "factNames": ["BaseItemWeight"],
                "edgeTypes": [],
                "requiresNative": False,
                "requiresRuntime": False,
                "requiresMapEvidence": False,
            },
            "evidenceUris": [
                "bp://fixture/review/default/BaseItemWeight",
            ],
        },
    }


def _pack() -> dict[str, object]:
    return build_review_pack(
        kind="query",
        author_id="query-pack-author",
        author_key_fingerprint="author-key",
        seed="stage10-query-v1",
        selection_rule="manual-fixed-all-cases",
        source_manifest_sha256=SOURCE_SHA256,
        candidates=[_candidate()],
        created_at="2026-07-29T00:00:00+00:00",
        tool_version="ark-kb-gold-review/v1",
    )


def _receipt(
    pack: dict[str, object],
    *,
    reviewer_id: str,
    reviewer_key: str,
    round_number: int,
    answer_value: int = 5,
    role: str = "REVIEWER",
    freshness: str = "FRESH",
) -> dict[str, object]:
    candidate = pack["candidates"][0]
    receipt: dict[str, object] = {
        "schema": "ark-kb-gold-review/v1",
        "packId": pack["packId"],
        "packSha256": pack["packSha256"],
        "caseId": candidate["caseId"],
        "candidateSha256": candidate["candidateSha256"],
        "reviewerId": reviewer_id,
        "reviewerKeyFingerprint": reviewer_key,
        "reviewerRole": role,
        "round": round_number,
        "reviewedAt": f"2026-07-29T0{round_number}:00:00+00:00",
        "verdict": "CONFIRMED",
        "answer": {
            "facts": [
                {
                    "factType": "ITEM_PROPERTY",
                    "factName": "BaseItemWeight",
                    "value": answer_value,
                }
            ]
        },
        "evidence": [
            {
                "uri": "bp://fixture/review/default/BaseItemWeight",
                "sourceRevisionSha256": REVISION_SHA256,
                "freshness": freshness,
            }
        ],
        "rationale": "Verified against the named fresh Blueprint evidence.",
        "toolVersion": "manual-review/v1",
    }
    receipt["contentSha256"] = review_content_sha256(receipt)
    return receipt


class GoldReviewPackTests(unittest.TestCase):
    def test_query_pack_covers_fixed_cases_without_answer_leakage(self):
        gold_path = (
            PROJECT_ROOT / "tests" / "fixtures" / "kb_query_gold_set.v1.json"
        )
        raw_gold = json.loads(gold_path.read_text(encoding="utf-8"))

        first = build_query_review_pack(
            gold_set_path=gold_path,
            author_id="query-pack-author",
            author_key_fingerprint="author-key",
            seed="stage10-query-v1",
            created_at="2026-07-29T00:00:00+00:00",
            tool_version="ark-kb-gold-review/v1",
        )
        second = build_query_review_pack(
            gold_set_path=gold_path,
            author_id="query-pack-author",
            author_key_fingerprint="author-key",
            seed="stage10-query-v1",
            created_at="2026-07-29T00:00:00+00:00",
            tool_version="ark-kb-gold-review/v1",
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first["candidates"]), 130)
        self.assertEqual(
            {candidate["caseId"] for candidate in first["candidates"]},
            {case["id"] for case in raw_gold["cases"]},
        )
        self.assertEqual(
            first["sourceManifestSha256"],
            hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            first["selectionRule"],
            "MANUAL_FIXED_ALL_CASES",
        )

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return {
                    str(key).casefold()
                    for key in value
                } | {
                    nested
                    for child in value.values()
                    for nested in keys(child)
                }
            if isinstance(value, list):
                return {
                    nested
                    for child in value
                    for nested in keys(child)
                }
            return set()

        reviewer_keys = {
            key
            for candidate in first["candidates"]
            for key in keys(candidate["payload"])
        }
        for forbidden in (
            "expected",
            "route",
            "prediction",
            "confidence",
            "reviewstatus",
        ):
            self.assertNotIn(forbidden, reviewer_keys)

    def test_pack_is_deterministic_and_blind(self):
        first = _pack()
        second = _pack()

        self.assertEqual(first, second)
        self.assertRegex(str(first["packSha256"]), r"^[0-9a-f]{64}$")
        self.assertRegex(
            str(first["candidates"][0]["candidateSha256"]),
            r"^[0-9a-f]{64}$",
        )
        validate_review_pack(first)
        serialized = str(first).lower()
        for forbidden in ("expected", "route", "prediction", "confidence"):
            self.assertNotIn(forbidden, serialized)

    def test_pack_rejects_prediction_leakage(self):
        candidate = _candidate()
        candidate["payload"]["expected"] = {
            "route": "DB_SEMANTIC_COMPLETE",
        }

        with self.assertRaisesRegex(
            GoldReviewError,
            "prediction leakage",
        ):
            build_review_pack(
                kind="query",
                author_id="query-pack-author",
                author_key_fingerprint="author-key",
                seed="stage10-query-v1",
                selection_rule="manual-fixed-all-cases",
                source_manifest_sha256=SOURCE_SHA256,
                candidates=[candidate],
                created_at="2026-07-29T00:00:00+00:00",
                tool_version="ark-kb-gold-review/v1",
            )

    def test_pack_rejects_duplicate_case_identity(self):
        with self.assertRaisesRegex(GoldReviewError, "duplicate caseId"):
            build_review_pack(
                kind="query",
                author_id="query-pack-author",
                author_key_fingerprint="author-key",
                seed="stage10-query-v1",
                selection_rule="manual-fixed-all-cases",
                source_manifest_sha256=SOURCE_SHA256,
                candidates=[_candidate(), _candidate()],
                created_at="2026-07-29T00:00:00+00:00",
                tool_version="ark-kb-gold-review/v1",
            )

    def test_pack_rejects_unsupported_review_kind(self):
        with self.assertRaisesRegex(
            GoldReviewError,
            "unsupported review kind",
        ):
            build_review_pack(
                kind="classifier-generated",
                author_id="query-pack-author",
                author_key_fingerprint="author-key",
                seed="stage10-query-v1",
                selection_rule="manual-fixed-all-cases",
                source_manifest_sha256=SOURCE_SHA256,
                candidates=[_candidate()],
                created_at="2026-07-29T00:00:00+00:00",
                tool_version="ark-kb-gold-review/v1",
            )

    def test_pack_rejects_candidate_content_tamper(self):
        pack = _pack()
        pack["candidates"][0]["payload"]["question"] = "Tampered question"

        with self.assertRaisesRegex(GoldReviewError, "candidate SHA-256"):
            validate_review_pack(pack)

    def test_pack_rejects_manifest_content_tamper(self):
        pack = _pack()
        pack["toolVersion"] = "tampered-tool/v9"

        with self.assertRaisesRegex(GoldReviewError, "pack SHA-256"):
            validate_review_pack(pack)


class GoldReviewReceiptTests(unittest.TestCase):
    def test_two_trusted_independent_reviews_are_ready_to_freeze(self):
        pack = _pack()
        reviews = [
            _receipt(
                pack,
                reviewer_id="reviewer-a",
                reviewer_key="key-a",
                round_number=1,
            ),
            _receipt(
                pack,
                reviewer_id="reviewer-b",
                reviewer_key="key-b",
                round_number=2,
            ),
        ]

        result = validate_review_set(
            pack,
            reviews,
            trusted_reviewers={
                "reviewer-a": "key-a",
                "reviewer-b": "key-b",
            },
        )

        self.assertEqual(result["status"], READY_TO_FREEZE)
        self.assertEqual(result["reviewedCases"], 1)

    def test_missing_trusted_registry_stays_blocked(self):
        pack = _pack()
        reviews = [
            _receipt(
                pack,
                reviewer_id="reviewer-a",
                reviewer_key="key-a",
                round_number=1,
            ),
            _receipt(
                pack,
                reviewer_id="reviewer-b",
                reviewer_key="key-b",
                round_number=2,
            ),
        ]

        result = validate_review_set(pack, reviews)

        self.assertEqual(
            result["status"],
            BLOCKED_BY_INDEPENDENT_REVIEW,
        )
        self.assertIn("TRUSTED_REVIEWER_REGISTRY_REQUIRED", result["gaps"])

    def test_review_rejects_author_self_review(self):
        pack = _pack()
        review = _receipt(
            pack,
            reviewer_id="query-pack-author",
            reviewer_key="key-author",
            round_number=1,
        )

        with self.assertRaisesRegex(GoldReviewError, "author cannot review"):
            validate_review_set(
                pack,
                [review],
                trusted_reviewers={"query-pack-author": "key-author"},
            )

    def test_review_rejects_author_alias_with_same_key(self):
        pack = _pack()
        review = _receipt(
            pack,
            reviewer_id="pack-author-alias",
            reviewer_key="author-key",
            round_number=1,
        )

        with self.assertRaisesRegex(GoldReviewError, "author cannot review"):
            validate_review_set(
                pack,
                [review],
                trusted_reviewers={"pack-author-alias": "author-key"},
            )

    def test_review_rejects_aliases_with_same_key_fingerprint(self):
        pack = _pack()
        reviews = [
            _receipt(
                pack,
                reviewer_id="reviewer-a",
                reviewer_key="shared-key",
                round_number=1,
            ),
            _receipt(
                pack,
                reviewer_id="reviewer-a-alias",
                reviewer_key="shared-key",
                round_number=2,
            ),
        ]

        with self.assertRaisesRegex(
            GoldReviewError,
            "reviewer key fingerprint",
        ):
            validate_review_set(
                pack,
                reviews,
                trusted_reviewers={
                    "reviewer-a": "shared-key",
                    "reviewer-a-alias": "shared-key",
                },
            )

    def test_review_rejects_same_reviewer_in_two_rounds(self):
        pack = _pack()
        reviews = [
            _receipt(
                pack,
                reviewer_id="reviewer-a",
                reviewer_key="key-a",
                round_number=1,
            ),
            _receipt(
                pack,
                reviewer_id="reviewer-a",
                reviewer_key="key-a",
                round_number=2,
            ),
        ]

        with self.assertRaisesRegex(
            GoldReviewError,
            "duplicate reviewer",
        ):
            validate_review_set(
                pack,
                reviews,
                trusted_reviewers={"reviewer-a": "key-a"},
            )

    def test_review_rejects_stale_confirmed_evidence(self):
        pack = _pack()
        stale = _receipt(
            pack,
            reviewer_id="reviewer-a",
            reviewer_key="key-a",
            round_number=1,
            freshness="STALE",
        )

        with self.assertRaisesRegex(
            GoldReviewError,
            "confirmed review requires fresh evidence",
        ):
            validate_review_set(
                pack,
                [stale],
                trusted_reviewers={"reviewer-a": "key-a"},
            )

    def test_review_rejects_content_tamper(self):
        pack = _pack()
        review = _receipt(
            pack,
            reviewer_id="reviewer-a",
            reviewer_key="key-a",
            round_number=1,
        )
        review["rationale"] = "Changed after review."

        with self.assertRaisesRegex(
            GoldReviewError,
            "review content SHA-256",
        ):
            validate_review_set(
                pack,
                [review],
                trusted_reviewers={"reviewer-a": "key-a"},
            )

    def test_disagreement_requires_independent_adjudicator(self):
        pack = _pack()
        reviews = [
            _receipt(
                pack,
                reviewer_id="reviewer-a",
                reviewer_key="key-a",
                round_number=1,
                answer_value=5,
            ),
            _receipt(
                pack,
                reviewer_id="reviewer-b",
                reviewer_key="key-b",
                round_number=2,
                answer_value=7,
            ),
        ]

        blocked = validate_review_set(
            pack,
            reviews,
            trusted_reviewers={
                "reviewer-a": "key-a",
                "reviewer-b": "key-b",
            },
        )
        self.assertEqual(
            blocked["status"],
            BLOCKED_BY_INDEPENDENT_REVIEW,
        )
        self.assertTrue(
            any(
                gap.startswith("INDEPENDENT_ADJUDICATION_REQUIRED:")
                for gap in blocked["gaps"]
            )
        )

        reused_reviewer = copy.deepcopy(reviews[0])
        reused_reviewer["reviewerRole"] = "ADJUDICATOR"
        reused_reviewer["round"] = 3
        reused_reviewer["reviewedAt"] = "2026-07-29T03:00:00+00:00"
        reused_reviewer["contentSha256"] = review_content_sha256(
            reused_reviewer
        )
        with self.assertRaisesRegex(
            GoldReviewError,
            "adjudicator must be independent",
        ):
            validate_review_set(
                pack,
                [*reviews, reused_reviewer],
                trusted_reviewers={
                    "reviewer-a": "key-a",
                    "reviewer-b": "key-b",
                },
            )
