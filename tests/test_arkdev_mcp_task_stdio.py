from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import TOOL_NAMES  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import publish_interpretation_fixture  # noqa: E402


def _direction(value: object) -> str:
    return "OUTPUT" if value in {"OUTPUT", "EGPD_Output"} else "INPUT"


class ArkdevMcpTaskStdioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ARK MCP Phase 2 空 格 ")
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.capture_root = self.root / "蓝图 captures"
        self.task_root = self.root / ".blueprint-tasks"
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        publish_interpretation(asset_dir, budget=32_000)

    async def test_stdio_create_research_cache_draft_validate_and_resume_without_confirm(self) -> None:
        stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.addCleanup(stderr.close)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                str(SCRIPTS / "run_arkdev_mcp.py"),
                "--capture-root",
                str(self.capture_root),
            ],
            env={"ARKDEV_MCP_TASK_ROOT": str(self.task_root)},
            cwd=ROOT,
            encoding="utf-8",
        )
        async with Client(stdio_client(parameters, errlog=stderr)) as client:
            tools = (await client.list_tools()).tools
            created = await client.call_tool(
                "blueprint_task_create",
                {
                    "mode": "IMPLEMENTATION_PREP",
                    "asset": "InterpretationFixture",
                    "goal": "Prepare one exact Blueprint patch plan",
                    "completionCriteria": ["Every connection names exact pins"],
                    "allowedChanges": ["EventGraph"],
                    "forbiddenChanges": ["Other assets"],
                },
            )
            task_id = created.structured_content["taskId"]
            first = await client.call_tool(
                "blueprint_task_research",
                {
                    "taskId": task_id,
                    "question": "ReceiveBeginPlay execution flow",
                    "maxHops": 0,
                },
            )
            second = await client.call_tool(
                "blueprint_task_research",
                {
                    "taskId": task_id,
                    "question": "ReceiveBeginPlay execution flow",
                    "maxHops": 0,
                },
            )
            node = first.structured_content["nodes"][0]
            pins = [
                pin
                for pin in first.structured_content["pins"]
                if pin.get("nodeRef") == node["ref"]
            ]
            pin_signatures = [
                {
                    "name": pin["name"],
                    "direction": _direction(pin["direction"]),
                    "category": pin.get("category", ""),
                    "subcategory": pin.get("subcategory", ""),
                    "ordinal": int(str(pin["ref"]).rsplit("/", 1)[-1]),
                    "containerType": pin.get("containerType", "None"),
                }
                for pin in pins
            ]
            graph_ref = node["graphRef"]
            drafted = await client.call_tool(
                "blueprint_patch_plan_draft",
                {
                    "taskId": task_id,
                    "nodes": [
                        {
                            "nodeRef": node["ref"],
                            "graphRef": graph_ref,
                            "signature": {
                                "nodeFamily": "EXISTING",
                                "className": node["className"],
                                "functionOwner": "",
                                "functionName": "",
                                "variableName": "",
                                "eventName": str(node.get("signals", {}).get("event", "")),
                                "pure": False,
                                "pinSignatures": pin_signatures,
                            },
                        }
                    ],
                    "operations": [
                        {
                            "operationId": "op://preserve-entry",
                            "kind": "PRESERVE",
                            "graphRef": graph_ref,
                            "dependsOn": [],
                            "preconditions": [{"nodeRef": node["ref"]}],
                            "payload": {"nodeRef": node["ref"]},
                            "postconditions": [{"unchanged": True}],
                            "checkpoint": "checkpoint://preserve",
                        }
                    ],
                    "capabilityRequirements": [],
                    "checkpoints": [
                        {
                            "checkpointId": "checkpoint://preserve",
                            "description": "The entry node remains unchanged",
                        }
                    ],
                    "blockingQuestions": [],
                },
            )
            plan_id = drafted.structured_content["planId"]
            validated = await client.call_tool(
                "blueprint_patch_plan_validate",
                {"taskId": task_id, "planId": plan_id},
            )
            resumed = await client.call_tool(
                "blueprint_task_resume",
                {"taskId": task_id},
            )
            metadata_before = {
                path.relative_to(self.task_root): path.read_bytes()
                for path in self.task_root.rglob("*.json")
            }
            task_resource = await client.read_resource(
                f"arkdev://tasks/{task_id.removeprefix('task://')}"
            )
            plan_resource = await client.read_resource(
                f"arkdev://plans/{plan_id.removeprefix('patch-plan://')}"
            )
            metadata_after = {
                path.relative_to(self.task_root): path.read_bytes()
                for path in self.task_root.rglob("*.json")
            }

        self.assertEqual(tuple(tool.name for tool in tools), TOOL_NAMES)
        self.assertEqual(len(tools), 11)
        self.assertFalse(created.is_error)
        self.assertFalse(first.is_error)
        self.assertFalse(first.structured_content["cached"])
        self.assertTrue(second.structured_content["cached"])
        self.assertEqual(first.structured_content["querySignature"], second.structured_content["querySignature"])
        self.assertFalse(drafted.is_error)
        self.assertEqual(drafted.structured_content["status"], "DRAFT")
        self.assertTrue(validated.structured_content["valid"])
        self.assertTrue(validated.structured_content["confirmable"])
        self.assertFalse(validated.structured_content["executionReady"])
        self.assertEqual(resumed.structured_content["phase"], "PLAN_DRAFT")
        self.assertEqual(resumed.structured_content["plan"]["planId"], plan_id)
        self.assertNotEqual(resumed.structured_content["phase"], "PLAN_CONFIRMED")
        self.assertIn('\"phase\":\"PLAN_DRAFT\"', task_resource.contents[0].text)
        self.assertIn('\"valid\":true', plan_resource.contents[0].text)
        self.assertEqual(metadata_after, metadata_before)
        stderr.seek(0)
        self.assertNotIn("Traceback", stderr.read())


if __name__ == "__main__":
    unittest.main()
