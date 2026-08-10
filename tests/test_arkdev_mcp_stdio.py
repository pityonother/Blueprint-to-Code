from __future__ import annotations

import subprocess
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
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "diagnose_arkdev_mcp.py"),
                "--capture-root",
                str(self.capture_root),
                "--fixture-asset",
                "InterpretationFixture",
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
            "BLUEPRINT_FIXTURE_CALL_OK",
            "CODEX_CONFIG_RENDER_OK",
            "CODEX_CLI_AVAILABLE",
            "CODEX_SERVER_LISTED",
        }
        self.assertEqual(set(checks), expected)
        for name in expected - {"CODEX_CLI_AVAILABLE", "CODEX_SERVER_LISTED"}:
            with self.subTest(check=name):
                self.assertEqual(checks[name], "true")
        if checks["CODEX_CLI_AVAILABLE"] == "false":
            self.assertTrue(
                checks["CODEX_SERVER_LISTED"].startswith("SKIPPED_WITH_REASON")
            )
        else:
            self.assertIn(checks["CODEX_SERVER_LISTED"], {"true", "false"})


if __name__ == "__main__":
    unittest.main()
