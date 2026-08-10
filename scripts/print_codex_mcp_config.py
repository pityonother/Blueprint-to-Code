"""Render a local, pasteable Codex stdio MCP configuration block."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePath, PureWindowsPath

from arkdev_mcp.contracts import TOOL_NAMES


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _toml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def render_config(project_root: str | PurePath) -> str:
    root = PureWindowsPath(str(project_root))
    launcher = root / "scripts" / "run_arkdev_mcp.ps1"
    lines = [
        "[mcp_servers.arkdev_blueprint]",
        'command = "powershell.exe"',
        "args = [",
        '  "-NoProfile",',
        '  "-ExecutionPolicy",',
        '  "Bypass",',
        '  "-File",',
        f"  {_toml_string(launcher)}",
        "]",
        f"cwd = {_toml_string(root)}",
        "required = true",
        "startup_timeout_sec = 20",
        "tool_timeout_sec = 60",
        "enabled_tools = [",
    ]
    lines.extend(f"  {_toml_string(name)}," for name in TOOL_NAMES)
    lines.extend(
        [
            "]",
            'default_tools_approval_mode = "auto"',
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the local Codex configuration for ARK Dev MCP."
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    options = parser.parse_args()
    print(render_config(options.project_root.resolve()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
