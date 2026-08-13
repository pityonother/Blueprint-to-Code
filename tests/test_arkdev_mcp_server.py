from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import Client


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import TOOL_NAMES  # noqa: E402
from arkdev_mcp.editor_bridge import (  # noqa: E402
    EditorCapability,
    FixtureEditorBridge,
)
from arkdev_mcp.server import create_server  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import publish_interpretation_fixture  # noqa: E402


class ArkdevMcpServerContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.capture_root = Path(self._temporary.name) / "captures"
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        publish_interpretation(asset_dir, budget=32_000)
        self.server = create_server(self.capture_root)

    async def test_discovery_surface_is_exact_bounded_and_read_only(self) -> None:
        async with Client(self.server) as client:
            tools = (await client.list_tools()).tools
            resources = (await client.list_resources()).resources
            templates = (
                await client.list_resource_templates()
            ).resource_templates
            prompts = (await client.list_prompts()).prompts

        self.assertEqual(tuple(tool.name for tool in tools), TOOL_NAMES)
        self.assertEqual(
            {str(resource.uri) for resource in resources},
            {"arkdev://status", "arkdev://editor/state"},
        )
        self.assertEqual(
            {template.uri_template for template in templates},
            {
                "blueprint://assets/{asset}/health",
                "arkdev://tasks/{task_id}",
                "arkdev://plans/{plan_id}",
                "arkdev://solvers/{solver_id}",
            },
        )
        self.assertLessEqual(len(resources) + len(templates), 6)
        self.assertEqual(
            {prompt.name for prompt in prompts},
            {
                "analyze_blueprint_task",
                "inspect_blueprint_node",
                "design_blueprint_patch",
                "solve_ark_blueprint_requirement",
            },
        )
        by_name = {tool.name: tool for tool in tools}
        context_schema = by_name["blueprint_get_context"].input_schema
        self.assertEqual(set(context_schema["required"]), {"asset", "goal"})
        self.assertEqual(
            context_schema["properties"]["seedRefs"]["maxItems"], 10
        )
        budget_schema = context_schema["properties"]["budgetTokens"]
        self.assertEqual(budget_schema["default"], 2400)
        self.assertEqual(budget_schema["minimum"], 800)
        self.assertEqual(budget_schema["maximum"], 6000)
        self.assertEqual(
            by_name["blueprint_get_node"].input_schema["properties"][
                "maxHops"
            ]["maximum"],
            1,
        )
        self.assertEqual(
            by_name["blueprint_list_assets"].input_schema["properties"][
                "limit"
            ]["maximum"],
            100,
        )
        for tool in tools[:5]:
            with self.subTest(tool=tool.name):
                self.assertIsNotNone(tool.output_schema)
                self.assertIsNotNone(tool.annotations)
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertFalse(tool.annotations.open_world_hint)
                self.assertIn("READ-ONLY", tool.description or "")
                self.assertIn("NO ARK DEVKIT MUTATION", tool.description or "")
        for tool in tools[5:11]:
            with self.subTest(tool=tool.name):
                self.assertIsNotNone(tool.output_schema)
                self.assertIsNotNone(tool.annotations)
                self.assertFalse(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertFalse(tool.annotations.open_world_hint)
                self.assertIn("WRITES LOCAL TASK METADATA ONLY", tool.description or "")
                self.assertIn("DOES NOT MODIFY ARK DEVKIT", tool.description or "")
        for tool in tools[11:]:
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

    async def test_tools_return_structured_content_and_stable_execution_errors(
        self,
    ) -> None:
        async with Client(self.server) as client:
            status = await client.call_tool("arkdev_status", {})
            editor = await client.call_tool(
                "arkdev_editor_state", {"includeSelection": True}
            )
            assets = await client.call_tool(
                "blueprint_list_assets", {"query": "Fixture", "limit": 25}
            )
            context = await client.call_tool(
                "blueprint_get_context",
                {
                    "asset": "InterpretationFixture",
                    "goal": "ReceiveBeginPlay",
                    "maxHops": 0,
                    "budgetTokens": 2400,
                },
            )
            error = await client.call_tool(
                "blueprint_get_context",
                {"asset": "MissingFixture", "goal": "ReceiveBeginPlay"},
            )

        self.assertFalse(status.is_error)
        self.assertEqual(
            status.structured_content["schema"],
            "blueprint-to-code.arkdev-mcp-status/v1",
        )
        self.assertTrue(status.structured_content["readOnly"])
        self.assertTrue(status.structured_content["taskMetadataWrite"])
        self.assertTrue(status.structured_content["capabilities"]["patchPlan"])
        self.assertTrue(status.structured_content["capabilities"]["solver"])
        self.assertTrue(
            status.structured_content["capabilities"]["localSolverMetadataWrite"]
        )
        self.assertEqual(status.structured_content["transport"], "stdio")
        self.assertNotIn(str(ROOT), json.dumps(status.structured_content))
        self.assertFalse(editor.structured_content["connected"])
        self.assertEqual(
            editor.structured_content["reasonCode"],
            "EDITOR_BRIDGE_NOT_INSTALLED",
        )
        self.assertEqual(
            assets.structured_content["schema"],
            "blueprint-to-code.mcp-blueprint-assets/v1",
        )
        self.assertEqual(
            context.structured_content["schema"],
            "blueprint-to-code.mcp-blueprint-context/v1",
        )
        self.assertTrue(error.is_error)
        self.assertEqual(
            error.structured_content,
            {
                "schema": "blueprint-to-code.arkdev-mcp-error/v1",
                "code": "ASSET_NOT_FOUND",
                "message": "Blueprint asset was not found.",
                "retryable": False,
                "details": {},
            },
        )

    async def test_exact_node_resource_and_prompts_use_the_public_surface(self) -> None:
        async with Client(self.server) as client:
            context = await client.call_tool(
                "blueprint_get_context",
                {
                    "asset": "InterpretationFixture",
                    "goal": "ReceiveBeginPlay",
                    "maxHops": 0,
                    "budgetTokens": 2400,
                },
            )
            node_ref = context.structured_content["nodes"][0]["ref"]
            node = await client.call_tool(
                "blueprint_get_node",
                {
                    "asset": "InterpretationFixture",
                    "nodeRef": node_ref,
                    "includeNeighborhood": True,
                    "maxHops": 1,
                },
            )
            status_resource = await client.read_resource("arkdev://status")
            editor_resource = await client.read_resource("arkdev://editor/state")
            health_resource = await client.read_resource(
                "blueprint://assets/InterpretationFixture/health"
            )
            task_prompt = await client.get_prompt(
                "analyze_blueprint_task",
                {"asset": "InterpretationFixture", "goal": "ReceiveBeginPlay"},
            )
            node_prompt = await client.get_prompt(
                "inspect_blueprint_node",
                {"asset": "InterpretationFixture", "nodeRef": node_ref},
            )
            patch_prompt = await client.get_prompt(
                "design_blueprint_patch",
                {"asset": "InterpretationFixture", "goal": "Prepare a plan"},
            )

        self.assertFalse(node.is_error)
        self.assertEqual(node.structured_content["node"]["ref"], node_ref)
        self.assertEqual(
            json.loads(status_resource.contents[0].text)["schema"],
            "blueprint-to-code.arkdev-mcp-status/v1",
        )
        self.assertFalse(json.loads(editor_resource.contents[0].text)["connected"])
        self.assertEqual(
            json.loads(health_resource.contents[0].text)["asset"],
            "InterpretationFixture",
        )
        task_text = task_prompt.messages[0].content.text
        self.assertIn("blueprint_get_context", task_text)
        self.assertIn("最多 5 次 blueprint_get_node", task_text)
        self.assertIn("不得使用 shell", task_text)
        node_text = node_prompt.messages[0].content.text
        self.assertIn(node_ref, node_text)
        self.assertIn("直接邻域", node_text)
        self.assertNotIn("Patch Plan", node_text)
        patch_text = patch_prompt.messages[0].content.text
        self.assertIn("blueprint_patch_plan_validate", patch_text)
        self.assertIn("explicitly approves", patch_text)
        self.assertIn("current conversation", patch_text)
        self.assertIn("不得执行蓝图", patch_text)

    async def test_connected_fixture_status_has_no_disconnected_reason(self) -> None:
        bridge = FixtureEditorBridge(
            active_asset="/Game/Test/Fixture.Fixture",
            capabilities=(EditorCapability.READ_ACTIVE_ASSET,),
        )
        async with Client(
            create_server(self.capture_root, editor_bridge=bridge)
        ) as client:
            status = await client.call_tool("arkdev_status", {})

        self.assertFalse(status.is_error)
        self.assertTrue(status.structured_content["capabilities"]["editorBridge"])
        self.assertEqual(
            status.structured_content["editorBridge"],
            {"status": "CONNECTED", "reasonCode": ""},
        )


if __name__ == "__main__":
    unittest.main()
