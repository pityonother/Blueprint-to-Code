from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.cohort_publication import (  # noqa: E402
    CohortPublicationError,
    publish_evidence_cohort,
)
from blueprint_translator.interpretation_publication import (  # noqa: E402
    inspect_interpretation_health,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)


class BlueprintEvidenceCohortPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.source_root = root / "source-captures"
        self.capture_root = root / "published-captures"
        self.plan_path = root / "cohort.json"

    def write_plan(self, assets: list[dict[str, object]]) -> None:
        self.plan_path.write_text(
            json.dumps(
                {
                    "schema": "blueprint-to-code.evidence-cohort-plan/v1",
                    "cohortId": "fixture-cross-category-v1",
                    "assets": assets,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def entry(
        name: str,
        *,
        source_asset_dir: str | None = None,
        object_path: str | None = None,
        category_code: str = "FIXTURE_CATEGORY",
        represented_object_count: int = 7,
    ) -> dict[str, object]:
        return {
            "asset": name,
            "sourceAssetDir": source_asset_dir or name,
            "objectPath": object_path or f"/Game/Test/{name}.{name}",
            "categoryCode": category_code,
            "representedObjectCount": represented_object_count,
        }

    def test_publishes_fresh_v3_evidence_and_current_interpretation(self) -> None:
        source_asset, _source, _payload = publish_interpretation_fixture(
            self.source_root,
            name="CohortFixture",
        )
        self.assertFalse((source_asset / "interpretation" / "current.json").exists())
        self.write_plan([self.entry("CohortFixture")])

        result = publish_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["total"], 1)
        self.assertNotIn(str(self.source_root), json.dumps(result))
        published = result["assets"][0]
        self.assertEqual(published["status"], "READY")
        self.assertEqual(published["categoryCode"], "FIXTURE_CATEGORY")
        self.assertEqual(published["representedObjectCount"], 7)

        destination = self.capture_root / "CohortFixture"
        health = inspect_interpretation_health(destination)
        self.assertEqual(health["status"], "READY")
        self.assertEqual(health["evidence"]["freshnessStatus"], "FRESH")
        self.assertTrue(health["evidence"]["releaseAuthority"])
        self.assertFalse(health["evidence"]["migrationRequired"])
        self.assertTrue((destination / "evidence" / "current.json").is_file())
        self.assertTrue((destination / "interpretation" / "current.json").is_file())
        self.assertFalse((source_asset / "interpretation" / "current.json").exists())

    def test_rerun_is_idempotent_and_keeps_the_same_current_revisions(self) -> None:
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        self.write_plan([self.entry("CohortFixture")])
        first = publish_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )

        second = publish_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )

        self.assertEqual(first["assets"][0]["evidenceRevisionId"], second["assets"][0]["evidenceRevisionId"])
        self.assertEqual(first["assets"][0]["interpretationRevisionId"], second["assets"][0]["interpretationRevisionId"])
        self.assertTrue(second["assets"][0]["evidenceReused"])
        self.assertTrue(second["assets"][0]["interpretationReused"])

    def test_rejects_zero_fact_capture_before_creating_any_destination(self) -> None:
        empty = {
            "asset_name": "IdentityOnly",
            "asset_path": "/Game/Test/IdentityOnly.IdentityOnly",
            "graphs": [],
            "class_defaults": {"variables": {}},
        }
        publish_interpretation_fixture(
            self.source_root,
            name="IdentityOnly",
            payload=empty,
        )
        self.write_plan([self.entry("IdentityOnly")])

        with self.assertRaisesRegex(
            CohortPublicationError,
            "COHORT_SOURCE_HAS_NO_SEMANTIC_FACTS",
        ):
            publish_evidence_cohort(
                plan_path=self.plan_path,
                source_root=self.source_root,
                capture_root=self.capture_root,
            )

        self.assertFalse((self.capture_root / "IdentityOnly").exists())

    def test_preflights_every_asset_before_mutating_the_first_destination(self) -> None:
        publish_interpretation_fixture(self.source_root, name="ValidFixture")
        empty = {
            "asset_name": "IdentityOnly",
            "asset_path": "/Game/Test/IdentityOnly.IdentityOnly",
            "graphs": [],
            "class_defaults": {"variables": {}},
        }
        publish_interpretation_fixture(
            self.source_root,
            name="IdentityOnly",
            payload=empty,
        )
        self.write_plan(
            [self.entry("ValidFixture"), self.entry("IdentityOnly")]
        )

        with self.assertRaises(CohortPublicationError):
            publish_evidence_cohort(
                plan_path=self.plan_path,
                source_root=self.source_root,
                capture_root=self.capture_root,
            )

        self.assertFalse((self.capture_root / "ValidFixture").exists())

    def test_rejects_same_short_name_with_a_different_object_path(self) -> None:
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        conflicting_payload = interpretation_payload("DifferentIdentity")
        publish_interpretation_fixture(
            self.capture_root,
            name="CohortFixture",
            payload=conflicting_payload,
        )
        pointer_path = (
            self.capture_root / "CohortFixture" / "evidence" / "current.json"
        )
        pointer_before = pointer_path.read_bytes()
        self.write_plan([self.entry("CohortFixture")])

        with self.assertRaisesRegex(
            CohortPublicationError,
            "COHORT_DESTINATION_IDENTITY_CONFLICT",
        ):
            publish_evidence_cohort(
                plan_path=self.plan_path,
                source_root=self.source_root,
                capture_root=self.capture_root,
            )

        self.assertEqual(pointer_path.read_bytes(), pointer_before)

    def test_rejects_source_path_traversal(self) -> None:
        self.write_plan(
            [self.entry("CohortFixture", source_asset_dir="../outside")]
        )

        with self.assertRaisesRegex(
            CohortPublicationError,
            "COHORT_PLAN_INVALID",
        ):
            publish_evidence_cohort(
                plan_path=self.plan_path,
                source_root=self.source_root,
                capture_root=self.capture_root,
            )


if __name__ == "__main__":
    unittest.main()
