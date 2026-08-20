from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.cohort_publication import (  # noqa: E402
    CohortPublicationError,
    preflight_evidence_cohort,
    publish_evidence_cohort,
)
import blueprint_translator.cohort_publication as cohort_publication  # noqa: E402
from blueprint_translator.evidence_policy import evaluate_evidence  # noqa: E402
from blueprint_translator.evidence_repository import (  # noqa: E402
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_writer import (  # noqa: E402
    write_evidence_artifacts_from_payload,
)
from blueprint_translator.interpretation_publication import (  # noqa: E402
    inspect_interpretation_health,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)
from publish_blueprint_evidence_cohort import main as cohort_cli_main  # noqa: E402


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
        final_state = resolve_asset_evidence_state(destination)
        final_decision = evaluate_evidence(final_state, purpose="publish")
        self.assertEqual(published["evidenceDecision"]["reasonCode"], "ALLOWED")
        self.assertEqual(
            published["evidenceDecision"]["bindingDigest"],
            final_decision.binding_digest,
        )

    def test_preflight_proves_the_cohort_without_creating_destinations(self) -> None:
        publish_interpretation_fixture(self.source_root, name="FirstFixture")
        publish_interpretation_fixture(self.source_root, name="SecondFixture")
        self.write_plan(
            [
                self.entry("FirstFixture", represented_object_count=3),
                self.entry("SecondFixture", represented_object_count=5),
            ]
        )

        result = preflight_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )

        self.assertEqual(result["status"], "READY_TO_PUBLISH")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["representedObjectCount"], 8)
        self.assertEqual(
            [item["asset"] for item in result["assets"]],
            ["FirstFixture", "SecondFixture"],
        )
        self.assertTrue(
            all(item["semanticFactCount"] > 0 for item in result["assets"])
        )
        self.assertTrue(
            all(
                item["sourceEvidenceDecision"]["reasonCode"] == "ALLOWED"
                for item in result["assets"]
            )
        )
        self.assertTrue(
            all(
                len(item["sourceEvidenceDecision"]["bindingDigest"]) == 64
                for item in result["assets"]
            )
        )

    def test_preflight_accepts_confirmed_data_asset_fields_without_graphs(self) -> None:
        name = "DataOnlyFixture"
        source_asset = self.source_root / name
        source_binary = source_asset / "source" / f"{name}.uasset"
        source_binary.parent.mkdir(parents=True)
        source_binary.write_bytes(b"data-only-fixture")
        write_evidence_artifacts_from_payload(
            f"/Game/Test/{name}.{name}",
            source_binary,
            {
                "asset_name": name,
                "asset_path": f"/Game/Test/{name}.{name}",
                "graphs": [],
                "asset_fields": {
                    "loaded": True,
                    "instance_object": name,
                    "variables": {
                        "Units.count": {
                            "value": 1,
                            "type": "ArrayCount",
                            "source": "fixture",
                            "confidence": "high",
                            "owner_kind": "asset",
                            "confirmed_value_usable": True,
                        }
                    },
                    "gaps": [],
                },
            },
            source_asset,
        )
        self.write_plan([self.entry(name)])

        result = preflight_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )

        self.assertEqual(result["assets"][0]["semanticFactCount"], 1)
        self.assertTrue(
            all(item["destinationStatus"] == "NEW" for item in result["assets"])
        )
        self.assertNotIn(str(self.source_root), json.dumps(result))
        self.assertFalse(self.capture_root.exists())

    def test_cli_preflight_only_prints_receipt_without_publishing(self) -> None:
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        self.write_plan([self.entry("CohortFixture")])
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = cohort_cli_main(
                [
                    "--plan",
                    str(self.plan_path),
                    "--source-root",
                    str(self.source_root),
                    "--capture-root",
                    str(self.capture_root),
                    "--budget",
                    "32000",
                    "--preflight-only",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["status"],
            "READY_TO_PUBLISH",
        )
        self.assertFalse(self.capture_root.exists())

    def test_publish_rejects_source_generation_changed_after_preflight(self) -> None:
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        self.write_plan([self.entry("CohortFixture")])
        preflight = preflight_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )
        expected_sha256 = str(preflight["preflightSha256"])
        self.assertEqual(len(expected_sha256), 64)

        changed_payload = interpretation_payload("CohortFixture")
        changed_payload["class_defaults"]["variables"]["ChangedAfterReview"] = {
            "value": 1,
            "type": "IntProperty",
            "source": "test",
            "confidence": "high",
        }
        publish_interpretation_fixture(
            self.source_root,
            name="CohortFixture",
            payload=changed_payload,
        )

        with self.assertRaisesRegex(
            CohortPublicationError,
            "COHORT_PREFLIGHT_MISMATCH",
        ):
            publish_evidence_cohort(
                plan_path=self.plan_path,
                source_root=self.source_root,
                capture_root=self.capture_root,
                budget=32_000,
                expected_preflight_sha256=expected_sha256,
            )

        self.assertFalse(self.capture_root.exists())

    def test_cli_publish_binds_the_reviewed_preflight_sha256(self) -> None:
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        self.write_plan([self.entry("CohortFixture")])
        preflight = preflight_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = cohort_cli_main(
                [
                    "--plan",
                    str(self.plan_path),
                    "--source-root",
                    str(self.source_root),
                    "--capture-root",
                    str(self.capture_root),
                    "--budget",
                    "32000",
                    "--expected-preflight-sha256",
                    str(preflight["preflightSha256"]),
                ]
            )

        self.assertEqual(exit_code, 0)
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(
            receipt["preflightSha256"],
            preflight["preflightSha256"],
        )
        self.assertTrue(
            (
                self.capture_root
                / "CohortFixture"
                / "interpretation"
                / "current.json"
            ).is_file()
        )

    def test_cli_publish_requires_a_reviewed_preflight_sha256(self) -> None:
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        self.write_plan([self.entry("CohortFixture")])
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = cohort_cli_main(
                [
                    "--plan",
                    str(self.plan_path),
                    "--source-root",
                    str(self.source_root),
                    "--capture-root",
                    str(self.capture_root),
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("COHORT_PREFLIGHT_REQUIRED", stderr.getvalue())
        self.assertFalse(self.capture_root.exists())

    def test_second_source_advancing_mid_cohort_fails_before_its_write(self) -> None:
        publish_interpretation_fixture(self.source_root, name="FirstFixture")
        publish_interpretation_fixture(self.source_root, name="SecondFixture")
        self.write_plan([self.entry("FirstFixture"), self.entry("SecondFixture")])
        preflight = preflight_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )
        original_publish = cohort_publication.publish_prepared_evidence_revision

        def publish_and_advance_second(**kwargs: object) -> object:
            result = original_publish(**kwargs)
            if Path(str(kwargs["asset_dir"])).name == "FirstFixture":
                changed_payload = interpretation_payload("SecondFixture")
                changed_payload["class_defaults"]["variables"]["Advanced"] = {
                    "value": 2,
                    "type": "IntProperty",
                    "source": "test",
                    "confidence": "high",
                }
                publish_interpretation_fixture(
                    self.source_root,
                    name="SecondFixture",
                    payload=changed_payload,
                )
            return result

        with (
            patch.object(
                cohort_publication,
                "publish_prepared_evidence_revision",
                side_effect=publish_and_advance_second,
            ),
            self.assertRaisesRegex(
                CohortPublicationError,
                "COHORT_SOURCE_GENERATION_CHANGED",
            ),
        ):
            publish_evidence_cohort(
                plan_path=self.plan_path,
                source_root=self.source_root,
                capture_root=self.capture_root,
                budget=32_000,
                expected_preflight_sha256=str(preflight["preflightSha256"]),
            )

        self.assertTrue(
            (self.capture_root / "FirstFixture" / "evidence" / "current.json").is_file()
        )
        self.assertFalse((self.capture_root / "SecondFixture").exists())

    def test_second_destination_created_mid_cohort_is_not_overwritten(self) -> None:
        publish_interpretation_fixture(self.source_root, name="FirstFixture")
        publish_interpretation_fixture(self.source_root, name="SecondFixture")
        self.write_plan([self.entry("FirstFixture"), self.entry("SecondFixture")])
        preflight = preflight_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )
        original_publish = cohort_publication.publish_prepared_evidence_revision
        external_pointer: bytes | None = None

        def publish_and_create_second(**kwargs: object) -> object:
            nonlocal external_pointer
            result = original_publish(**kwargs)
            if Path(str(kwargs["asset_dir"])).name == "FirstFixture":
                destination, _source, _payload = publish_interpretation_fixture(
                    self.capture_root,
                    name="SecondFixture",
                )
                external_pointer = (
                    destination / "evidence" / "current.json"
                ).read_bytes()
            return result

        with (
            patch.object(
                cohort_publication,
                "publish_prepared_evidence_revision",
                side_effect=publish_and_create_second,
            ),
            self.assertRaisesRegex(
                CohortPublicationError,
                "COHORT_ASSET_PUBLICATION_FAILED",
            ),
        ):
            publish_evidence_cohort(
                plan_path=self.plan_path,
                source_root=self.source_root,
                capture_root=self.capture_root,
                budget=32_000,
                expected_preflight_sha256=str(preflight["preflightSha256"]),
            )

        self.assertIsNotNone(external_pointer)
        self.assertEqual(
            (
                self.capture_root
                / "SecondFixture"
                / "evidence"
                / "current.json"
            ).read_bytes(),
            external_pointer,
        )

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

    def test_upgrades_matching_v2_compatibility_destination(self) -> None:
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        destination = self.capture_root / "CohortFixture"
        object_path = "/Game/Test/CohortFixture.CohortFixture"
        write_evidence_artifacts_from_payload(
            object_path,
            None,
            interpretation_payload("CohortFixture"),
            destination,
            publish_v3=False,
        )
        compatibility_manifest = json.loads(
            (destination / "evidence" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(compatibility_manifest["object_path"], object_path)
        self.assertFalse((destination / "evidence" / "current.json").exists())
        self.write_plan([self.entry("CohortFixture", object_path=object_path)])

        result = publish_evidence_cohort(
            plan_path=self.plan_path,
            source_root=self.source_root,
            capture_root=self.capture_root,
            budget=32_000,
        )

        self.assertEqual(result["ready"], 1)
        health = inspect_interpretation_health(destination)
        self.assertEqual(health["status"], "READY")
        self.assertEqual(health["evidence"]["freshnessStatus"], "FRESH")
        self.assertTrue(health["evidence"]["releaseAuthority"])
        self.assertTrue((destination / "evidence" / "current.json").is_file())
        self.assertTrue((destination / "interpretation" / "current.json").is_file())

    def test_rejects_conflicting_v2_destination_before_mutation(self) -> None:
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        destination = self.capture_root / "CohortFixture"
        write_evidence_artifacts_from_payload(
            "/Game/Test/DifferentIdentity.DifferentIdentity",
            None,
            interpretation_payload("DifferentIdentity"),
            destination,
            publish_v3=False,
        )
        protected_paths = (
            destination / "evidence" / "evidence.sqlite",
            destination / "evidence" / "manifest.json",
            destination / "output" / "agent_index.md",
        )
        protected_before = {path: path.read_bytes() for path in protected_paths}
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

        self.assertEqual(
            {path: path.read_bytes() for path in protected_paths},
            protected_before,
        )
        self.assertFalse((destination / "evidence" / "current.json").exists())

    def test_v2_alias_cannot_bypass_full_cohort_identity_preflight(self) -> None:
        publish_interpretation_fixture(self.source_root, name="EarlierFixture")
        publish_interpretation_fixture(self.source_root, name="CohortFixture")
        destination = self.capture_root / "CohortFixture"
        write_evidence_artifacts_from_payload(
            "/Game/Test/DifferentIdentity.DifferentIdentity",
            None,
            interpretation_payload("DifferentIdentity"),
            destination,
            publish_v3=False,
        )
        manifest_path = destination / "evidence" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["objectPath"] = "/Game/Test/CohortFixture.CohortFixture"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        protected_paths = (
            destination / "evidence" / "evidence.sqlite",
            manifest_path,
            destination / "output" / "agent_index.md",
        )
        protected_before = {path: path.read_bytes() for path in protected_paths}
        self.write_plan(
            [self.entry("EarlierFixture"), self.entry("CohortFixture")]
        )

        with self.assertRaisesRegex(
            CohortPublicationError,
            "COHORT_DESTINATION_IDENTITY_CONFLICT",
        ):
            publish_evidence_cohort(
                plan_path=self.plan_path,
                source_root=self.source_root,
                capture_root=self.capture_root,
            )

        self.assertFalse((self.capture_root / "EarlierFixture").exists())
        self.assertEqual(
            {path: path.read_bytes() for path in protected_paths},
            protected_before,
        )
        self.assertFalse((destination / "evidence" / "current.json").exists())

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
