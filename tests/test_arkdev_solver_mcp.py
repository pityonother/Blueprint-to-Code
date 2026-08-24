from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import ERROR_CODES, TOOL_NAMES  # noqa: E402
from arkdev_mcp.server import create_server  # noqa: E402
from arkdev_mcp.solver.service import SolverService  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import publish_interpretation_fixture  # noqa: E402


SOLVER_TOOL_NAMES = (
    "blueprint_solver_create",
    "blueprint_solver_resume",
    "blueprint_solver_preflight",
    "blueprint_solver_update",
    "blueprint_solver_materialize_task",
)
SOLVER_ERROR_CODES = {
    "SOLVER_NOT_FOUND",
    "REQUIREMENT_PROPOSAL_INVALID",
    "REQUEST_TEXT_UNASSIGNED",
    "SOLVER_PHASE_INVALID",
    "SOLVER_UPDATE_INVALID",
    "TARGET_SELECTION_REQUIRED",
    "TARGET_CANDIDATE_NOT_FOUND",
    "EVIDENCE_ACQUISITION_REQUIRED",
    "TASK_NOT_APPLICABLE",
    "SOLVER_LIMIT_EXCEEDED",
}


class ArkdevSolverMcpContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.capture_root = self.root / "captures"
        self.task_root = self.root / ".blueprint-tasks"
        self.solver_root = self.root / ".blueprint-solvers"
        asset_dir, _source, _payload = publish_interpretation_fixture(self.capture_root)
        publish_interpretation(asset_dir, budget=32_000)

    async def test_create_preserves_typed_ranking_division_in_raw_request(self) -> None:
        server = create_server(
            self.capture_root,
            task_root=self.task_root,
            solver_root=self.solver_root,
        )
        raw_request = "生物体重/死神体重=K如果>1"
        proposal = {
            "schema": "blueprint-to-code.requirement-proposal/v1",
            "rawRequest": raw_request,
            "language": "zh-CN",
            "subproblems": [
                {
                    "sourceStart": 0,
                    "sourceEnd": len(raw_request),
                    "sourceText": raw_request,
                    "intent": "ANSWER_CURRENT_BEHAVIOR",
                    "outputKind": "RANKING",
                    "completeness": "REQUIRED",
                    "targetHints": [],
                    "constraints": {
                        "topK": 10,
                        "userFormula": "penaltyCoefficient = min(K, 1 / K)",
                        "formulaVariables": {
                            "candidateWeight": "候选生物体重",
                            "reaperWeight": "死神体重",
                        },
                    },
                    "acceptanceCriteria": ["按用户公式返回前十名"],
                }
            ],
        }

        async with Client(server) as client:
            prompt = await client.get_prompt(
                "solve_ark_blueprint_requirement",
                {"rawRequest": raw_request, "language": "zh-CN"},
            )
            created = await client.call_tool(
                "blueprint_solver_create",
                {
                    "rawRequest": raw_request,
                    "language": "zh-CN",
                    "proposal": proposal,
                },
            )

        self.assertIn(raw_request, prompt.messages[0].content.text)
        self.assertFalse(created.is_error, created.content)
        opaque = created.structured_content["solverId"].removeprefix("solver://")
        stored = json.loads(
            (self.solver_root / opaque / "requirement.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored["rawRequest"], raw_request)
        self.assertEqual(stored["subproblems"][0]["sourceText"], raw_request)

    async def test_surface_appends_exactly_five_metadata_tools_and_one_resource(
        self,
    ) -> None:
        server = create_server(
            self.capture_root,
            task_root=self.task_root,
            solver_root=self.solver_root,
        )
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            templates = (await client.list_resource_templates()).resource_templates

        self.assertEqual(tuple(tool.name for tool in tools), TOOL_NAMES)
        self.assertEqual(tuple(tool.name for tool in tools[-5:]), SOLVER_TOOL_NAMES)
        self.assertEqual(len(tools), 16)
        self.assertIn(
            "arkdev://solvers/{solver_id}",
            {template.uri_template for template in templates},
        )
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(
            set(by_name["blueprint_solver_create"].input_schema["required"]),
            {"rawRequest", "language", "proposal"},
        )
        self.assertEqual(
            by_name["blueprint_solver_create"].input_schema["properties"]["rawRequest"][
                "maxLength"
            ],
            8000,
        )
        self.assertEqual(
            by_name["blueprint_solver_preflight"].input_schema["properties"][
                "problemIds"
            ]["maxItems"],
            8,
        )
        self.assertEqual(
            set(
                by_name["blueprint_solver_update"].input_schema["properties"][
                    "operation"
                ]["enum"]
            ),
            {
                "selectAssetCandidate",
                "addTargetAlias",
                "resolveBlockingQuestion",
                "provideDatasetDescriptor",
                "provideLocalizationDescriptor",
                "provideModAssetDescriptor",
            },
        )
        self.assertEqual(
            set(by_name["blueprint_solver_materialize_task"].input_schema["required"]),
            {"solverId", "problemId"},
        )
        for tool in tools[-5:]:
            with self.subTest(tool=tool.name):
                self.assertIsNotNone(tool.output_schema)
                self.assertIsNotNone(tool.annotations)
                self.assertFalse(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertFalse(tool.annotations.open_world_hint)
                self.assertIn(
                    "WRITES LOCAL SOLVER/TASK METADATA ONLY.",
                    tool.description or "",
                )
                self.assertIn(
                    "DOES NOT MODIFY ARK DEVKIT OR BLUEPRINT EVIDENCE.",
                    tool.description or "",
                )
                output_schema = tool.output_schema
                encoded = json.dumps(output_schema, sort_keys=True)
                self.assertIn("blueprint-to-code.solver-state/v1", encoded)
                self.assertIn("blueprint-to-code.arkdev-mcp-error/v1", encoded)

    async def test_create_returns_solver_state_and_resource_is_read_only(self) -> None:
        server = create_server(
            self.capture_root,
            task_root=self.task_root,
            solver_root=self.solver_root,
        )
        raw_request = "分析当前蓝图行为"
        proposal = {
            "schema": "blueprint-to-code.requirement-proposal/v1",
            "rawRequest": raw_request,
            "language": "zh-CN",
            "subproblems": [
                {
                    "sourceStart": 0,
                    "sourceEnd": len(raw_request),
                    "sourceText": raw_request,
                    "intent": "ANSWER_CURRENT_BEHAVIOR",
                    "outputKind": "CURRENT_BEHAVIOR",
                    "completeness": "BEST_EFFORT",
                    "targetHints": [],
                    "constraints": {},
                    "acceptanceCriteria": ["描述当前行为"],
                }
            ],
        }
        async with Client(server) as client:
            created = await client.call_tool(
                "blueprint_solver_create",
                {
                    "rawRequest": raw_request,
                    "language": "zh-CN",
                    "proposal": proposal,
                },
            )
            self.assertFalse(created.is_error, created.content)
            solver_id = created.structured_content["solverId"]
            before = {
                path.relative_to(self.solver_root): path.read_bytes()
                for path in self.solver_root.rglob("*.json")
            }
            resource = await client.read_resource(
                f"arkdev://solvers/{solver_id.removeprefix('solver://')}"
            )
            after = {
                path.relative_to(self.solver_root): path.read_bytes()
                for path in self.solver_root.rglob("*.json")
            }

        self.assertEqual(
            created.structured_content["schema"],
            "blueprint-to-code.solver-state/v1",
        )
        self.assertEqual(
            created.structured_content["nextRecommendedTool"],
            "blueprint_solver_preflight",
        )
        self.assertEqual(json.loads(resource.contents[0].text)["solverId"], solver_id)
        self.assertEqual(after, before)

    async def test_solver_errors_use_the_stable_public_envelope(self) -> None:
        server = create_server(
            self.capture_root,
            task_root=self.task_root,
            solver_root=self.solver_root,
        )
        async with Client(server) as client:
            missing = await client.call_tool(
                "blueprint_solver_resume",
                {"solverId": "solver://" + "f" * 32},
            )

        self.assertTrue(missing.is_error)
        self.assertEqual(
            missing.structured_content,
            {
                "schema": "blueprint-to-code.arkdev-mcp-error/v1",
                "code": "SOLVER_NOT_FOUND",
                "message": "Solver metadata was not found.",
                "retryable": False,
                "details": {},
            },
        )
        self.assertTrue(SOLVER_ERROR_CODES.issubset(ERROR_CODES))

    async def test_solver_materialization_reuses_real_task_context_and_can_resume(
        self,
    ) -> None:
        server = create_server(
            self.capture_root,
            task_root=self.task_root,
            solver_root=self.solver_root,
        )
        raw_request = (
            "Cap the server-side value while preserving existing behavior using "
            "生物体重/死神体重=K如果>1"
        )
        proposal = {
            "schema": "blueprint-to-code.requirement-proposal/v1",
            "rawRequest": raw_request,
            "language": "en",
            "subproblems": [
                {
                    "sourceStart": 0,
                    "sourceEnd": len(raw_request),
                    "sourceText": raw_request,
                    "intent": "DESIGN_BLUEPRINT_CHANGE",
                    "outputKind": "BLUEPRINT_CHANGE",
                    "completeness": "BEST_EFFORT",
                    "targetHints": [
                        {
                            "text": "InterpretationFixture",
                            "role": "PRIMARY_BLUEPRINT",
                            "aliases": [],
                            "expectedKind": "BLUEPRINT",
                            "userSupplied": False,
                        }
                    ],
                    "constraints": {
                        "desiredBehavior": ["Cap values above the threshold"],
                        "invariants": ["Preserve values below the threshold"],
                        "acceptanceTests": ["Verify both threshold boundaries"],
                    },
                    "acceptanceCriteria": ["Task is ready for bounded research"],
                }
            ],
        }
        async with Client(server) as client:
            created = await client.call_tool(
                "blueprint_solver_create",
                {"rawRequest": raw_request, "language": "en", "proposal": proposal},
            )
            solver_id = created.structured_content["solverId"]
            problem_id = created.structured_content["problemSummaries"][0]["problemId"]
            preflight = await client.call_tool(
                "blueprint_solver_preflight", {"solverId": solver_id}
            )
            materialized = await client.call_tool(
                "blueprint_solver_materialize_task",
                {"solverId": solver_id, "problemId": problem_id},
            )
            task_id = materialized.structured_content["materializedTasks"][0]["taskId"]
            resumed = await client.call_tool(
                "blueprint_task_resume", {"taskId": task_id}
            )

        self.assertFalse(preflight.is_error, preflight.content)
        self.assertEqual(preflight.structured_content["status"], "READY_FOR_TASKS")
        self.assertFalse(materialized.is_error, materialized.content)
        self.assertEqual(
            materialized.structured_content["status"], "TASKS_MATERIALIZED"
        )
        self.assertFalse(resumed.is_error, resumed.content)
        self.assertEqual(resumed.structured_content["taskId"], task_id)
        self.assertEqual(resumed.structured_content["phase"], "DISCOVERY")
        task_goal = resumed.structured_content["goal"]
        self.assertNotIn(raw_request, task_goal)
        self.assertNotIn("生物体重/死神体重", task_goal)
        self.assertIn(problem_id, task_goal)
        self.assertIn("BLUEPRINT_CHANGE", task_goal)

    async def test_fourth_prompt_has_fixed_solver_flow_and_boundaries(self) -> None:
        server = create_server(
            self.capture_root,
            task_root=self.task_root,
            solver_root=self.solver_root,
        )
        async with Client(server) as client:
            prompts = (await client.list_prompts()).prompts
            prompt = await client.get_prompt(
                "solve_ark_blueprint_requirement",
                {"rawRequest": "分析公式并设计蓝图修改", "language": "zh-CN"},
            )

        self.assertEqual(len(prompts), 4)
        text = prompt.messages[0].content.text
        for step in range(1, 11):
            self.assertIn(f"{step}.", text)
        self.assertIn("blueprint_solver_create", text)
        self.assertIn("blueprint_solver_preflight", text)
        self.assertIn("The model is the semantic front-end.", text)
        self.assertIn("BTC is the deterministic compiler and orchestrator.", text)
        self.assertIn("Never invent an Evidence-ready state.", text)
        self.assertIn("不得自动进入 Patch Plan confirm", text)


class SolverServerInjectionTests(unittest.TestCase):
    def test_create_server_uses_explicit_solver_root_and_shared_task_service(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture_root = root / "captures"
            task_root = root / ".blueprint-tasks"
            solver_root = root / ".blueprint-solvers"
            asset_dir, _source, _payload = publish_interpretation_fixture(capture_root)
            publish_interpretation(asset_dir, budget=32_000)

            original_init = SolverService.__init__
            captured: dict[str, object] = {}

            def capturing_init(
                service: SolverService, blueprint: object, tasks: object, store: object
            ) -> None:
                captured.update(
                    {"blueprint": blueprint, "tasks": tasks, "store": store}
                )
                original_init(service, blueprint, tasks, store)

            with patch.object(SolverService, "__init__", capturing_init):
                create_server(
                    capture_root,
                    task_root=task_root,
                    solver_root=solver_root,
                )

            self.assertEqual(captured["store"].root, solver_root)
            self.assertIs(captured["tasks"].blueprint, captured["blueprint"])

    def test_create_server_uses_solver_root_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture_root = root / "captures"
            solver_root = root / "environment" / ".blueprint-solvers"
            asset_dir, _source, _payload = publish_interpretation_fixture(capture_root)
            publish_interpretation(asset_dir, budget=32_000)
            captured: dict[str, object] = {}
            original_init = SolverService.__init__

            def capturing_init(
                service: SolverService, blueprint: object, tasks: object, store: object
            ) -> None:
                captured["store"] = store
                original_init(service, blueprint, tasks, store)

            with (
                patch.dict(
                    "os.environ",
                    {"ARKDEV_MCP_SOLVER_ROOT": str(solver_root)},
                    clear=False,
                ),
                patch.object(SolverService, "__init__", capturing_init),
            ):
                create_server(capture_root)

            self.assertEqual(captured["store"].root, solver_root)


if __name__ == "__main__":
    unittest.main()
