from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import TOOL_NAMES  # noqa: E402
from print_codex_mcp_config import render_config  # noqa: E402
import validate_change  # noqa: E402


class ArkdevMcpWindowsSetupTests(unittest.TestCase):
    def test_renderer_produces_current_parseable_codex_stdio_config(self) -> None:
        project_root = PureWindowsPath("X:" + r"\Workspace With Space\蓝图")
        rendered = render_config(project_root)
        parsed = tomllib.loads(rendered)["mcp_servers"]["arkdev_blueprint"]

        self.assertEqual(parsed["command"], "powershell.exe")
        self.assertEqual(
            parsed["args"][:4],
            ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"],
        )
        self.assertEqual(parsed["cwd"], str(project_root))
        self.assertEqual(tuple(parsed["enabled_tools"]), TOOL_NAMES)
        self.assertTrue(parsed["required"])
        self.assertEqual(parsed["startup_timeout_sec"], 20)
        self.assertEqual(parsed["tool_timeout_sec"], 60)
        self.assertEqual(parsed["default_tools_approval_mode"], "auto")

    def test_committed_example_is_parseable_and_contains_no_machine_path(self) -> None:
        example_path = ROOT / ".codex" / "arkdev-mcp.config.example.toml"
        text = example_path.read_text(encoding="utf-8")
        parsed = tomllib.loads(text)["mcp_servers"]["arkdev_blueprint"]

        self.assertIn("<ABS_PROJECT_ROOT>", text)
        self.assertNotRegex(text, r"(?i)(?<![A-Za-z0-9_])[a-z]:[\\/]")
        self.assertEqual(tuple(parsed["enabled_tools"]), TOOL_NAMES)

    def test_windows_scripts_do_not_emit_status_text_from_the_stdio_launcher(
        self,
    ) -> None:
        launcher = (ROOT / "scripts" / "run_arkdev_mcp.ps1").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "scripts" / "install_arkdev_mcp.ps1").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("Write-Host", launcher)
        self.assertNotIn("Write-Output", launcher)
        self.assertIn("[Console]::Error.WriteLine", launcher)
        self.assertIn("requirements-mcp.txt", installer)
        self.assertIn("runtime\\python\\python.exe", installer)
        self.assertIn(".runtime\\arkdev-mcp", installer)

    def test_windows_ci_installs_and_runs_the_read_only_mcp_gate(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        release = (
            ROOT / ".github" / "workflows" / "release-windows.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("python -m pip install -r requirements-mcp.txt", ci)
        self.assertIn("python scripts/validate_change.py", ci)
        config = validate_change.load_config(
            ROOT / "scripts" / "validation_profiles.json"
        )
        classification = validate_change.classify_changed_files(
            ["scripts/query_blueprint_evidence.py"],
            config,
        )
        commands = validate_change.build_command_plan(
            classification,
            changed_files=["scripts/query_blueprint_evidence.py"],
            base_sha="a" * 40,
            python_command="python",
        )
        query_command = next(
            command
            for command in commands
            if command.identifier == "python-query-contracts"
        )
        expected_mcp_tests = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "tests").glob("test_arkdev_mcp*.py")
        }
        self.assertTrue(expected_mcp_tests)
        self.assertLessEqual(expected_mcp_tests, set(query_command.argv))
        self.assertIn("python -m pip install -r requirements-mcp.txt", release)
        self.assertIn("tests/test_arkdev_mcp_stdio.py", release)


if __name__ == "__main__":
    unittest.main()
