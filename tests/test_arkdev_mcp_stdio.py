from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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


class ArkdevMcpStdioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ARK MCP 空 格 ")
        self.addCleanup(self._temporary.cleanup)
        self.capture_root = Path(self._temporary.name) / "蓝图 captures"
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        publish_interpretation(asset_dir, budget=32_000)

    async def test_real_stdio_initialize_discovery_status_and_fixture_context(
        self,
    ) -> None:
        stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.addCleanup(stderr.close)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                str(SCRIPTS / "run_arkdev_mcp.py"),
                "--capture-root",
                str(self.capture_root),
            ],
            cwd=ROOT,
            encoding="utf-8",
        )
        async with Client(stdio_client(parameters, errlog=stderr)) as client:
            tools = (await client.list_tools()).tools
            status = await client.call_tool("arkdev_status", {})
            context = await client.call_tool(
                "blueprint_get_context",
                {
                    "asset": "InterpretationFixture",
                    "goal": "ReceiveBeginPlay",
                    "maxHops": 0,
                    "budgetTokens": 2400,
                },
            )

        self.assertEqual(tuple(tool.name for tool in tools), TOOL_NAMES)
        self.assertFalse(status.is_error)
        self.assertEqual(status.structured_content["transport"], "stdio")
        self.assertFalse(context.is_error)
        self.assertTrue(context.structured_content["nodes"])
        stderr.seek(0)
        self.assertNotIn("Traceback", stderr.read())

    def test_import_has_no_stdout_or_stderr_noise(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    f"sys.path.insert(0, {str(SCRIPTS)!r}); "
                    "import arkdev_mcp.server"
                ),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, "")
        self.assertEqual(process.stderr, "")

    def test_diagnostic_reports_core_and_codex_discovery_separately(self) -> None:
        missing_editor_state = Path(self._temporary.name) / "missing-editor-state.json"
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "diagnose_arkdev_mcp.py"),
                "--capture-root",
                str(self.capture_root),
                "--fixture-asset",
                "InterpretationFixture",
                "--editor-state-file",
                str(missing_editor_state),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=40,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        checks = dict(
            line.split("=", 1)
            for line in process.stdout.splitlines()
            if "=" in line
        )
        expected = {
            "DEPENDENCY_INSTALLED",
            "SERVER_IMPORTABLE",
            "STDIO_HANDSHAKE_OK",
            "TOOLS_DISCOVERED",
            "STATUS_CALL_OK",
            "EDITOR_BRIDGE_STATE_FOUND",
            "EDITOR_BRIDGE_STATE_FRESH",
            "EDITOR_BRIDGE_CONNECTED",
            "EDITOR_ACTIVE_ASSET_AVAILABLE",
            "EDITOR_ACTIVE_GRAPH_AVAILABLE",
            "EDITOR_GRAPH_POSITIONS_AVAILABLE",
            "EDITOR_SELECTION_AVAILABLE",
            "EDITOR_EVIDENCE_BINDING_AVAILABLE",
            "BLUEPRINT_FIXTURE_CALL_OK",
            "TASK_CREATE_OK",
            "TASK_RESUME_OK",
            "TASK_RESEARCH_OK",
            "TASK_CACHE_HIT_OK",
            "PATCH_PLAN_DRAFT_OK",
            "PATCH_PLAN_VALIDATE_OK",
            "CODEX_CONFIG_RENDER_OK",
            "CODEX_CLI_AVAILABLE",
            "CODEX_SERVER_LISTED",
        }
        self.assertEqual(set(checks), expected)
        for name in expected - {"CODEX_CLI_AVAILABLE", "CODEX_SERVER_LISTED"}:
            with self.subTest(check=name):
                if name.startswith("EDITOR_"):
                    self.assertIn(
                        checks[name],
                        {
                            "false",
                            "SKIPPED_WITH_REASON:unsupported_by_devkit_build",
                        },
                    )
                else:
                    self.assertEqual(checks[name], "true")
        if checks["CODEX_CLI_AVAILABLE"] == "false":
            self.assertTrue(
                checks["CODEX_SERVER_LISTED"].startswith("SKIPPED_WITH_REASON")
            )
        else:
            self.assertIn(checks["CODEX_SERVER_LISTED"], {"true", "false"})

    def test_diagnostic_never_promotes_fixture_state_to_live_state(self) -> None:
        fixture_state = Path(self._temporary.name) / "fixture-editor-state.json"
        missing_editor_state = Path(self._temporary.name) / "missing-editor-state.json"
        payload = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "arkdev_editor_bridge"
                / "editor_state.connected.json"
            ).read_text(encoding="utf-8")
        )
        payload["writtenAtUtc"] = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        fixture_state.write_text(json.dumps(payload), encoding="utf-8")

        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "diagnose_arkdev_mcp.py"),
                "--capture-root",
                str(self.capture_root),
                "--fixture-asset",
                "InterpretationFixture",
                "--editor-state-file",
                str(missing_editor_state),
                "--editor-fixture-state-file",
                str(fixture_state),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

        self.assertEqual(process.returncode, 0, process.stderr)
        checks = dict(
            line.split("=", 1)
            for line in process.stdout.splitlines()
            if "=" in line
        )
        self.assertEqual(checks["EDITOR_BRIDGE_CONNECTED"], "false")
        self.assertEqual(checks["FIXTURE_EDITOR_BRIDGE_CONNECTED"], "true")
        self.assertEqual(
            checks["FIXTURE_EDITOR_SELECTION_AVAILABLE"],
            "SKIPPED_WITH_REASON:unsupported_by_devkit_build",
        )
        self.assertEqual(
            checks["FIXTURE_EDITOR_EVIDENCE_BINDING_AVAILABLE"], "true"
        )


if __name__ == "__main__":
    unittest.main()
