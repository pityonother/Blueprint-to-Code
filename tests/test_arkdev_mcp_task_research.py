from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.contracts import McpExecutionError  # noqa: E402
from arkdev_mcp.tasking.research_service import ResearchService  # noqa: E402
from arkdev_mcp.tasking.store import TaskStore  # noqa: E402
from arkdev_mcp.tasking.task_service import TaskService  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)


class CountingBlueprintService(BlueprintService):
    def __init__(self, capture_root: Path) -> None:
        super().__init__(capture_root)
        self.context_calls = 0

    def get_context(self, **kwargs: object) -> dict[str, object]:
        self.context_calls += 1
        return super().get_context(**kwargs)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TaskResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.capture_root = self.root / "captures"
        self.asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        publish_interpretation(self.asset_dir, budget=32_000)
        self.blueprint = CountingBlueprintService(self.capture_root)
        self.store = TaskStore(self.root / ".blueprint-tasks")
        self.tasks = TaskService(self.blueprint, self.store)
        self.research = ResearchService(self.blueprint, self.tasks, self.store)
        self.graph_ref = self.blueprint.get_task_authority(
            asset="InterpretationFixture"
        )["graphTargets"][0]["ref"]
        self.context = self.tasks.create(
            mode="IMPLEMENTATION_PREP",
            asset="InterpretationFixture",
            goal="Prepare an exact Blueprint change",
            completion_criteria=["Every connection identifies exact endpoints"],
            allowed_changes=["EventGraph"],
            forbidden_changes=["Other assets"],
            graph_ref=self.graph_ref,
            supporting_assets=[],
        )

    def call(self, question: str = "ReceiveBeginPlay execution flow", **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "task_id": self.context["taskId"],
            "question": question,
            "graph_ref": self.graph_ref,
            "seed_refs": [],
            "max_hops": 1,
            "max_nodes": 40,
            "max_pins": 160,
            "max_edges": 160,
            "budget_tokens": 2400,
            "task_update": {},
        }
        arguments.update(overrides)
        return self.research.research(**arguments)

    def metadata_snapshot(self) -> dict[Path, bytes]:
        task_root = self.store.root / self.context["taskId"].removeprefix("task://")
        return {
            path.relative_to(task_root): path.read_bytes()
            for path in task_root.rglob("*.json")
        }

    def test_unusable_class_default_is_not_promoted_to_confirmed_fact(self) -> None:
        revision = "a" * 24
        candidate = {
            "id": f"bp://asset@{revision}/default/Value",
            "kind": "CLASS_DEFAULT",
            "status": "CONFIRMED",
            "valueUsable": False,
            "evidenceRefs": [f"bp://asset@{revision}/default/Value"],
        }

        merged = ResearchService._merge_confirmed_facts(  # noqa: SLF001
            [],
            [candidate],
            revision=revision,
        )

        self.assertEqual(merged, [])

    def test_partial_object_identity_default_is_not_promoted_to_confirmed_fact(
        self,
    ) -> None:
        revision = "b" * 24
        candidate = {
            "id": f"bp://asset@{revision}/default/Values",
            "kind": "CLASS_DEFAULT",
            "status": "CONFIRMED",
            "valueUsable": True,
            "resolvedObjectIdentityComplete": False,
            "evidenceRefs": [f"bp://asset@{revision}/default/Values"],
        }

        merged = ResearchService._merge_confirmed_facts(  # noqa: SLF001
            [],
            [candidate],
            revision=revision,
        )

        self.assertEqual(merged, [])

    def test_identical_signature_hits_cache_without_a_second_context_query(self) -> None:
        first = self.call()
        second = self.call()

        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["querySignature"], second["querySignature"])
        self.assertEqual(self.blueprint.context_calls, 1)
        self.assertEqual(second["queryLedger"]["researchCalls"], 2)
        self.assertEqual(second["queryLedger"]["uniqueQueries"], 1)
        self.assertEqual(second["queryLedger"]["cacheHits"], 1)
        self.assertEqual(second["queryLedger"]["contextPages"], 1)
        self.assertEqual(second["taskReadiness"], "READY_TO_PLAN")

    def test_question_or_budget_change_is_a_cache_miss(self) -> None:
        first = self.call()
        second = self.call(question="Different exact flow")
        third = self.call(question="Different exact flow", budget_tokens=2600)

        self.assertEqual(self.blueprint.context_calls, 3)
        self.assertEqual(len({first["querySignature"], second["querySignature"], third["querySignature"]}), 3)
        self.assertEqual(third["queryLedger"]["uniqueQueries"], 3)

    def test_ninth_unique_slice_is_rejected_without_eviction(self) -> None:
        for index in range(8):
            self.call(question=f"ReceiveBeginPlay flow {index}")

        with self.assertRaises(McpExecutionError) as raised:
            self.call(question="ninth unique question")

        self.assertEqual(raised.exception.code, "TASK_SLICE_LIMIT_REACHED")
        saved = self.store.load_context(self.context["taskId"])
        self.assertEqual(len(saved["graphSlices"]), 8)
        self.assertEqual(len(list(self.store.iter_slices(self.context["taskId"]))), 8)

    def test_task_update_tracks_resolves_blockers_and_types_assumptions(self) -> None:
        blocked = self.call(
            task_update={
                "addBlockingQuestions": ["Which restore guard is authoritative?"],
                "addAssumptions": ["The existing restore guard remains authoritative"],
            }
        )
        saved = self.store.load_context(self.context["taskId"])
        question_id = saved["blockingQuestions"][0]["questionId"]
        self.assertEqual(blocked["taskReadiness"], "DISCOVERY")
        self.assertEqual(saved["confirmedFacts"], [])
        self.assertEqual(saved["assumptions"][0]["type"], "ASSUMPTION")

        ready = self.call(
            task_update={"resolveBlockingQuestionIds": [question_id]}
        )
        self.assertTrue(ready["cached"])
        self.assertEqual(ready["taskReadiness"], "READY_TO_PLAN")
        self.assertEqual(self.blueprint.context_calls, 1)

    def test_unknown_blocker_ids_reject_atomically_before_research_side_effects(self) -> None:
        self.call(
            task_update={
                "addBlockingQuestions": ["Which restore guard is authoritative?"]
            }
        )
        saved = self.store.load_context(self.context["taskId"])
        known_id = saved["blockingQuestions"][0]["questionId"]
        unknown_id = "question://" + "f" * 24

        for resolve_ids in ([unknown_id], [known_id, unknown_id]):
            with self.subTest(resolve_ids=resolve_ids):
                before = self.metadata_snapshot()
                calls_before = self.blueprint.context_calls

                with self.assertRaises(McpExecutionError) as raised:
                    self.call(
                        task_update={"resolveBlockingQuestionIds": resolve_ids}
                    )

                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                self.assertEqual(self.metadata_snapshot(), before)
                self.assertEqual(self.blueprint.context_calls, calls_before)

    def test_task_update_rejects_unknown_fields_and_confirmed_fact_injection(self) -> None:
        for update in (
            {"phase": ["READY_TO_PLAN"]},
            {"confirmedFacts": ["caller supplied"]},
        ):
            with self.subTest(update=update):
                before = self.metadata_snapshot()
                calls_before = self.blueprint.context_calls

                with self.assertRaises(McpExecutionError) as raised:
                    self.call(task_update=update)

                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                self.assertEqual(self.metadata_snapshot(), before)
                self.assertEqual(self.blueprint.context_calls, calls_before)

    def test_path_like_task_update_is_rejected_before_any_side_effect(self) -> None:
        before = self.metadata_snapshot()

        with self.assertRaises(McpExecutionError) as raised:
            self.call(
                task_update={
                    "addAssumptions": ["../../private/restore-objective"]
                }
            )

        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self.metadata_snapshot(), before)
        self.assertEqual(self.blueprint.context_calls, 0)

    def test_research_is_path_free_and_does_not_mutate_evidence_or_interpretation(self) -> None:
        protected = sorted(path for path in self.asset_dir.rglob("*") if path.is_file())
        before = {path: _sha256(path) for path in protected}

        result = self.call()

        after = {path: _sha256(path) for path in protected}
        self.assertEqual(after, before)
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotRegex(encoded, r"(?i)(?<![A-Za-z0-9_])[a-z]:[\\/]")
        self.assertEqual(result["schema"], "blueprint-to-code.graph-slice/v1")
        self.assertLessEqual(len(result["nodes"]), 40)
        self.assertLessEqual(len(result["pins"]), 160)
        self.assertLessEqual(len(result["edges"]), 160)

    def test_research_rejects_out_of_bounds_inputs(self) -> None:
        for overrides in (
            {"max_hops": 3},
            {"seed_refs": ["bp://x"] * 11},
            {"max_nodes": 101},
            {"max_pins": 401},
            {"max_edges": 401},
            {"budget_tokens": 6001},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(McpExecutionError) as raised:
                    self.call(**overrides)
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_revision_change_blocks_before_old_cache_can_be_reused(self) -> None:
        first = self.call()
        self.assertFalse(first["cached"])
        changed = interpretation_payload()
        changed["graphs"][0]["payload"]["metadata"]["confidence"] = "medium"
        publish_interpretation_fixture(self.capture_root, payload=changed)
        publish_interpretation(self.asset_dir, budget=32_000)

        with self.assertRaises(McpExecutionError) as raised:
            self.call()

        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_CHANGED")
        self.assertEqual(self.blueprint.context_calls, 1)
        self.assertEqual(
            self.store.load_session(self.context["taskId"])["phase"], "BLOCKED"
        )

    def test_research_refuses_a_third_distinct_target_graph(self) -> None:
        graph_refs = [
            item["ref"]
            for item in self.blueprint.get_task_authority(
                asset="InterpretationFixture"
            )["graphTargets"]
        ]
        self.assertGreaterEqual(len(graph_refs), 3)
        self.call(question="first graph", graph_ref=graph_refs[0])
        self.call(question="second graph", graph_ref=graph_refs[1])

        with self.assertRaises(McpExecutionError) as raised:
            self.call(question="third graph", graph_ref=graph_refs[2])

        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        saved = self.store.load_context(self.context["taskId"])
        self.assertEqual(len(saved["graphTargets"]), 2)


if __name__ == "__main__":
    unittest.main()
