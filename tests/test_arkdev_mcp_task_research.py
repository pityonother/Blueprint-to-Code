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
from interpretation_fixture import publish_interpretation_fixture  # noqa: E402


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

    def test_task_update_tracks_and_resolves_blockers_without_fact_injection(self) -> None:
        blocked = self.call(
            task_update={
                "addBlockingQuestions": ["Which restore guard is authoritative?"],
                "confirmedFacts": [{"text": "caller supplied"}],
            }
        )
        saved = self.store.load_context(self.context["taskId"])
        question_id = saved["blockingQuestions"][0]["questionId"]
        self.assertEqual(blocked["taskReadiness"], "DISCOVERY")
        self.assertEqual(saved["confirmedFacts"], [])

        ready = self.call(
            task_update={"resolveBlockingQuestionIds": [question_id]}
        )
        self.assertTrue(ready["cached"])
        self.assertEqual(ready["taskReadiness"], "READY_TO_PLAN")
        self.assertEqual(self.blueprint.context_calls, 1)

    def test_research_is_path_free_and_does_not_mutate_evidence_or_interpretation(self) -> None:
        protected = [
            self.asset_dir / "evidence" / "current.json",
            self.asset_dir / "interpretation" / "current.json",
        ]
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


if __name__ == "__main__":
    unittest.main()
