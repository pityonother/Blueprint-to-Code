from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "arkdev_editor_bridge"
    / "editor_state.connected.json"
)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.contracts import TOOL_NAMES  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)


class ArkdevEditorBridgeStdioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ARK editor bridge 空 格 ")
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.capture_root = self.root / "蓝图 captures"
        payload = interpretation_payload()
        nodes = payload["graphs"][0]["payload"]["nodes"]
        for index, node in enumerate(nodes[:4], start=1):
            node["node_guid"] = str(index) * 32
        asset_dir, _source, _ = publish_interpretation_fixture(
            self.capture_root,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)
        authority = BlueprintService(self.capture_root).get_task_authority(
            asset="InterpretationFixture"
        )
        self.graph_ref = next(
            item["ref"]
            for item in authority["graphTargets"]
            if item["name"] == "EventGraph"
        )
        self.state_file = self.root / "private bridge" / "editor_state.json"
        self.task_root = self.root / ".blueprint-tasks"

    def refresh_snapshot(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["writtenAtUtc"] = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    async def test_official_stdio_client_reads_connected_lightweight_nodes_and_task_binding(self) -> None:
        stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.addCleanup(stderr.close)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                str(SCRIPTS / "run_arkdev_mcp.py"),
                "--capture-root",
                str(self.capture_root),
            ],
            env={
                "ARKDEV_MCP_TASK_ROOT": str(self.task_root),
                "ARKDEV_EDITOR_BRIDGE_STATE_FILE": str(self.state_file),
            },
            cwd=ROOT,
            encoding="utf-8",
        )

        async with Client(stdio_client(parameters, errlog=stderr)) as client:
            tools = (await client.list_tools()).tools
            by_name = {tool.name: tool for tool in tools}
            editor_input = by_name["arkdev_editor_state"].input_schema["properties"]
            self.assertEqual(tuple(tool.name for tool in tools), TOOL_NAMES)
            self.assertFalse(editor_input["includeGraphNodes"]["default"])
            self.assertEqual(editor_input["maxGraphNodes"]["default"], 200)
            self.assertEqual(editor_input["maxGraphNodes"]["maximum"], 1000)

            created = await client.call_tool(
                "blueprint_task_create",
                {
                    "mode": "BLUEPRINT_DESIGN",
                    "asset": "InterpretationFixture",
                    "goal": "Verify live Editor state binding",
                    "completionCriteria": ["Exact Evidence identity matches"],
                    "allowedChanges": ["Read-only inspection"],
                    "forbiddenChanges": ["ARK DevKit mutation"],
                    "graphRef": self.graph_ref,
                },
            )
            task_id = created.structured_content["taskId"]
            task_dir = self.task_root / task_id.removeprefix("task://")
            before = {
                path.relative_to(task_dir).as_posix(): (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                )
                for path in task_dir.rglob("*")
                if path.is_file()
            }

            self.refresh_snapshot()
            status = await client.call_tool("arkdev_status", {})
            lightweight = await client.call_tool("arkdev_editor_state", {})
            nodes = await client.call_tool(
                "arkdev_editor_state",
                {"includeGraphNodes": True, "maxGraphNodes": 2},
            )
            task = await client.call_tool(
                "arkdev_editor_state",
                {
                    "includeGraphNodes": False,
                    "taskId": task_id,
                },
            )
            resource = await client.read_resource("arkdev://editor/state")

        after = {
            path.relative_to(task_dir).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in task_dir.rglob("*")
            if path.is_file()
        }
        self.assertFalse(status.is_error)
        self.assertTrue(status.structured_content["capabilities"]["editorBridge"])
        self.assertEqual(status.structured_content["editorBridge"]["status"], "CONNECTED")
        self.assertFalse(lightweight.is_error)
        self.assertTrue(lightweight.structured_content["connected"])
        self.assertEqual(lightweight.structured_content["graphNodes"], [])
        self.assertEqual(lightweight.structured_content["activeAssetBinding"]["status"], "EXACT")
        self.assertEqual(len(nodes.structured_content["graphNodes"]), 2)
        self.assertTrue(
            all(
                item["bindingStatus"] == "EXACT"
                for item in nodes.structured_content["graphNodes"]
            )
        )
        self.assertEqual(task.structured_content["taskBinding"]["status"], "MATCHED")
        self.assertFalse(task.structured_content["taskBinding"]["mutationReady"])
        resource_payload = json.loads(resource.contents[0].text)
        self.assertEqual(resource_payload["graphNodes"], [])
        self.assertEqual(resource_payload["taskBinding"], {})
        self.assertEqual(before, after)
        stderr.seek(0)
        self.assertNotIn("Traceback", stderr.read())


if __name__ == "__main__":
    unittest.main()
