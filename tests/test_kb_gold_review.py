from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
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
    build_registration_review_pack,
    build_review_pack,
    registration_review_source_from_sqlite,
    review_content_sha256,
    validate_review_pack,
    validate_registration_review_source,
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


def _registration_discovery_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE source_inventory (
            source_id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            generated_at TEXT NOT NULL,
            limitations_json TEXT NOT NULL
        );
        CREATE TABLE system_registrations (
            registration_id TEXT PRIMARY KEY,
            owner_object_path TEXT NOT NULL,
            registration_type TEXT NOT NULL,
            target_object_path TEXT NOT NULL,
            source_property TEXT NOT NULL,
            source_evidence_id TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_kind TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            ("schema", "blueprint-to-code-kb-discovery/v2"),
            ("generated_at_utc", "2026-07-29T00:00:00+00:00"),
        ),
    )
    connection.execute(
        """
        INSERT INTO source_inventory(
            source_id, source_kind, schema_version, source_fingerprint,
            status, confidence, record_count, generated_at,
            limitations_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source://existing-knowledge-databases",
            "existing_knowledge_database",
            "sqlite-snapshot-inventory/v1",
            SOURCE_SHA256,
            "COMPLETE",
            "HIGH",
            3,
            "2026-07-29T00:00:00+00:00",
            "[]",
        ),
    )
    rows = (
        (
            "registration://b",
            "/Game/Owner/B.Owner",
            "buff_registration",
            "/Game/Target/B.Target_C",
            "BuffClass",
            "existing-kb://registrations/b",
            "MEDIUM",
            "existing_knowledge_database",
        ),
        (
            "registration://a",
            "/Game/Owner/A.Owner",
            "item_registration",
            "/Game/Target/A.Target_C",
            "ItemClass",
            "existing-kb://registrations/a",
            "HIGH",
            "existing_knowledge_database",
        ),
        (
            "registration://c",
            "/Game/Owner/C.Owner",
            "creature_registration",
            "/Game/Target/C.Target_C",
            "NPCClass",
            "existing-kb://registrations/c",
            "LOW",
            "existing_knowledge_database",
        ),
    )
    connection.executemany(
        """
        INSERT INTO system_registrations(
            registration_id, owner_object_path, registration_type,
            target_object_path, source_property, source_evidence_id,
            confidence, source_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()


class GoldReviewPackTests(unittest.TestCase):
    def test_registration_pack_uses_independent_typed_source_without_labels(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "discovery.sqlite"
            _registration_discovery_db(database)

            source = registration_review_source_from_sqlite(database)
            first = build_registration_review_pack(
                source_manifest=source,
                author_id="registration-pack-author",
                author_key_fingerprint="registration-author-key",
                seed="stage10-registration-v1",
                created_at="2026-07-29T00:00:00+00:00",
                tool_version="ark-kb-gold-review/v1",
                limit=120,
            )
            second = build_registration_review_pack(
                source_manifest=source,
                author_id="registration-pack-author",
                author_key_fingerprint="registration-author-key",
                seed="stage10-registration-v1",
                created_at="2026-07-29T00:00:00+00:00",
                tool_version="ark-kb-gold-review/v1",
                limit=120,
            )

        self.assertEqual(first, second)
        self.assertEqual(first["kind"], "registration")
        self.assertEqual(len(first["candidates"]), 3)
        self.assertEqual(
            first["selectionRule"],
            "INDEPENDENT_TYPED_REGISTRATIONS_STABLE_HASH_V1_LIMIT_120",
        )
        expected_fields = {
            "ownerUri",
            "targetUri",
            "registrationType",
            "sourceProperty",
            "evidenceUri",
            "sourceKind",
        }
        for candidate in first["candidates"]:
            self.assertEqual(
                set(candidate["payload"]),
                expected_fields,
            )
        serialized = json.dumps(first, sort_keys=True).casefold()
        for forbidden in (
            "expectededgetype",
            "expectedstatus",
            "reviewstatus",
            "prediction",
            "confidence",
        ):
            self.assertNotIn(forbidden, serialized)
        validate_review_pack(first)

    def test_registration_source_rejects_classifier_generated_candidates(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "discovery.sqlite"
            _registration_discovery_db(database)
            source = registration_review_source_from_sqlite(database)
        source["generatedFromClassifier"] = True

        with self.assertRaisesRegex(
            GoldReviewError,
            "independent source",
        ):
            validate_registration_review_source(source)

    def test_registration_payload_rejects_label_or_prediction_fields(self):
        for forbidden_field in (
            "expectedEdgeType",
            "expectedStatus",
            "reviewStatus",
            "prediction",
            "confidence",
        ):
            candidate = {
                "caseId": "registration://leak",
                "payload": {
                    "ownerUri": "/Game/Owner/A.Owner",
                    "targetUri": "/Game/Target/A.Target_C",
                    "registrationType": "item_registration",
                    "sourceProperty": "ItemClass",
                    "evidenceUri": "existing-kb://registrations/a",
                    "sourceKind": "existing_knowledge_database",
                    forbidden_field: "leaked",
                },
            }
            with self.subTest(field=forbidden_field), self.assertRaises(
                GoldReviewError,
            ):
                build_review_pack(
                    kind="registration",
                    author_id="registration-pack-author",
                    author_key_fingerprint="registration-author-key",
                    seed="stage10-registration-v1",
                    selection_rule="test",
                    source_manifest_sha256=SOURCE_SHA256,
                    candidates=[candidate],
                    created_at="2026-07-29T00:00:00+00:00",
                    tool_version="ark-kb-gold-review/v1",
                )

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
