from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.contracts import McpExecutionError  # noqa: E402
from arkdev_mcp.tasking.canonical import canonical_json, semantic_digest  # noqa: E402
from arkdev_mcp.tasking.store import TaskStore  # noqa: E402
from arkdev_mcp.tasking.task_service import TaskService  # noqa: E402
from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)


class TaskContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.capture_root = self.root / "captures"
        self.asset_dir, self.source_path, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        publish_interpretation(self.asset_dir, budget=32_000)
        self.blueprint = BlueprintService(self.capture_root)
        self.store = TaskStore(self.root / ".blueprint-tasks")
        self.tasks = TaskService(self.blueprint, self.store)

    def create(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "mode": "BLUEPRINT_DESIGN",
            "asset": "InterpretationFixture",
            "goal": "Save the restore slot after server-side fire",
            "completion_criteria": [
                "Server entry is exact",
                "Every planned connection names both pins",
            ],
            "allowed_changes": ["The selected EventGraph only"],
            "forbidden_changes": ["Do not modify supporting assets"],
            "graph_ref": "",
            "supporting_assets": [],
        }
        arguments.update(overrides)
        return self.tasks.create(**arguments)

    def test_create_binds_current_authority_to_an_opaque_local_handle(self) -> None:
        graph_ref = self.blueprint.get_task_authority(
            asset="InterpretationFixture"
        )["graphTargets"][0]["ref"]

        context = self.create(graph_ref=graph_ref)
        session = self.store.load_session(context["taskId"])

        self.assertEqual(context["schema"], "blueprint-to-code.task-context/v1")
        self.assertRegex(context["taskId"], r"^task://[0-9a-f]{32}$")
        self.assertNotIn("InterpretationFixture", context["taskId"])
        self.assertEqual(context["primaryAsset"]["freshness"], "FRESH")
        self.assertEqual(
            context["primaryAsset"]["evidenceRevisionId"],
            self.blueprint.get_task_authority(asset="InterpretationFixture")[
                "evidenceRevisionId"
            ],
        )
        self.assertEqual(context["graphTargets"][0]["ref"], graph_ref)
        self.assertEqual(context["readiness"], "DISCOVERY")
        self.assertEqual(context["phase"], "DISCOVERY")
        self.assertEqual(
            context["nextRecommendedTool"],
            "blueprint_task_research",
        )
        self.assertEqual(session["phase"], "DISCOVERY")
        self.assertEqual(session["schema"], "blueprint-to-code.task-session/v1")
        self.assertEqual(len(context["semanticDigest"]), 64)
        self.assertTrue((self.store.root / context["taskId"].removeprefix("task://")).is_dir())
        encoded = json.dumps(context, ensure_ascii=False)
        self.assertNotIn(str(self.store.root), encoded)
        self.assertNotRegex(encoded, r"(?i)(?<![A-Za-z0-9_])[a-z]:[\\/]")

    def test_identity_invalidating_errors_are_normalized_and_persist_blocked(self) -> None:
        source_codes = (
            "EVIDENCE_NOT_AUTHORITATIVE",
            "EVIDENCE_REVISION_MISMATCH",
            "EVIDENCE_REVISION_CHANGED",
            "EVIDENCE_STALE",
            "EVIDENCE_NOT_FOUND",
            "ASSET_NOT_FOUND",
        )
        for source_code in source_codes:
            with self.subTest(source_code=source_code):
                context = self.create(goal=f"Verify authority failure {source_code}")
                with patch.object(
                    self.blueprint,
                    "get_task_authority",
                    side_effect=McpExecutionError(source_code, "authority unavailable"),
                ):
                    with self.assertRaises(McpExecutionError) as raised:
                        self.tasks.resume(context["taskId"])

                self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_CHANGED")
                self.assertEqual(
                    raised.exception.details.get("sourceCode"),
                    source_code,
                )
                session = self.store.load_session(context["taskId"])
                self.assertEqual(session["phase"], "BLOCKED")
                self.assertEqual(
                    session["reasonCode"],
                    "EVIDENCE_REVISION_CHANGED",
                )

                with self.assertRaises(McpExecutionError) as blocked:
                    self.tasks.resume(context["taskId"])
                self.assertEqual(blocked.exception.code, "TASK_BLOCKED")

    def test_resume_is_compact_and_rejects_non_opaque_or_traversal_handles(self) -> None:
        context = self.create()

        resumed = self.tasks.resume(context["taskId"])

        self.assertEqual(resumed["phase"], "DISCOVERY")
        self.assertEqual(resumed["goal"], context["goal"])
        self.assertEqual(resumed["storedSliceSummaries"], [])
        self.assertNotIn("graphSlices", resumed)
        self.assertNotIn("filesystemPath", resumed)
        self.assertLess(len(json.dumps(resumed, ensure_ascii=False)), 6400)
        for invalid in ("task://../escape", "task://abc/../../escape", "not-a-task"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(McpExecutionError) as raised:
                    self.tasks.resume(invalid)
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_task_store_rejects_metadata_roots_outside_named_boundary(self) -> None:
        with self.assertRaises(McpExecutionError) as raised:
            TaskStore(self.root / "task-metadata")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_resume_keeps_large_persistent_context_within_default_token_budget(self) -> None:
        context = self.create()
        session = self.store.load_session(context["taskId"])
        context["confirmedFacts"] = [
            {"id": f"fact://{index}", "text": "confirmed " + "x" * 500}
            for index in range(40)
        ]
        context["blockingQuestions"] = [
            {"questionId": f"question://{index}", "text": "blocking " + "y" * 900}
            for index in range(20)
        ]
        context["nonBlockingUnknowns"] = [
            {"unknownId": f"unknown://{index}", "text": "unknown " + "z" * 900}
            for index in range(20)
        ]
        context["graphSlices"] = [
            {
                "sliceId": f"slice://{index:032x}",
                "querySignature": f"query-{index}",
                "question": "slice " + "q" * 220,
                "graphRefs": [],
                "nodeCount": 100,
                "pinCount": 400,
                "edgeCount": 400,
                "semanticDigest": f"{index:064x}",
            }
            for index in range(8)
        ]
        self.tasks.sync_and_save(context, session)

        resumed = self.tasks.resume(context["taskId"])

        self.assertLessEqual(estimate_tokens(canonical_json(resumed)), 1600)
        self.assertEqual(resumed["summaryCounts"]["blockingQuestions"], 20)
        self.assertEqual(len(resumed["blockingQuestions"]), 20)
        self.assertLessEqual(resumed["estimatedTokens"], 1600)

    def test_create_enforces_freshness_primary_supporting_and_input_limits(self) -> None:
        with self.assertRaises(McpExecutionError) as raised:
            self.create(asset="MissingFixture")
        self.assertEqual(raised.exception.code, "ASSET_NOT_FOUND")

        with self.assertRaises(McpExecutionError) as raised:
            self.create(completion_criteria=[])
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

        with self.assertRaises(McpExecutionError) as raised:
            self.create(supporting_assets=["a", "b", "c", "d"])
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

        self.source_path.write_bytes(self.source_path.read_bytes() + b"-stale")
        with self.assertRaises(McpExecutionError) as raised:
            self.create()
        self.assertEqual(raised.exception.code, "EVIDENCE_STALE")

    def test_semantic_digest_excludes_timestamps_and_random_task_id(self) -> None:
        context = self.create()
        changed = copy.deepcopy(context)
        changed["taskId"] = "task://" + "f" * 32
        changed["createdAt"] = "2099-01-01T00:00:00Z"
        changed["updatedAt"] = "2099-01-02T00:00:00Z"

        self.assertEqual(semantic_digest(context), semantic_digest(changed))

    def test_resume_blocks_after_authoritative_evidence_revision_changes(self) -> None:
        context = self.create()
        original_revision = context["primaryAsset"]["evidenceRevisionId"]
        changed_payload = interpretation_payload()
        changed_payload["graphs"][0]["payload"]["metadata"]["confidence"] = "medium"
        publish_interpretation_fixture(
            self.capture_root,
            payload=changed_payload,
        )
        publish_interpretation(self.asset_dir, budget=32_000)
        current_revision = self.blueprint.get_task_authority(
            asset="InterpretationFixture"
        )["evidenceRevisionId"]
        self.assertNotEqual(current_revision, original_revision)

        with self.assertRaises(McpExecutionError) as raised:
            self.tasks.resume(context["taskId"])

        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_CHANGED")
        session = self.store.load_session(context["taskId"])
        self.assertEqual(session["phase"], "BLOCKED")
        self.assertEqual(session["reasonCode"], "EVIDENCE_REVISION_CHANGED")

    def test_generated_handles_are_hex_only_and_never_derive_from_goal(self) -> None:
        with self.assertRaises(McpExecutionError) as raised:
            self.create(goal="../../private/restore-objective")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

        context = self.create(goal="private restore objective")
        opaque_id = context["taskId"].removeprefix("task://")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{32}", opaque_id))
        self.assertNotIn("private", opaque_id)


if __name__ == "__main__":
    unittest.main()
