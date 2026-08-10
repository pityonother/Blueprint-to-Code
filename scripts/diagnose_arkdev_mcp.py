"""Run bounded, non-mutating ARK Dev MCP diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.metadata
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CHECK_ORDER = (
    "DEPENDENCY_INSTALLED",
    "SERVER_IMPORTABLE",
    "STDIO_HANDSHAKE_OK",
    "TOOLS_DISCOVERED",
    "STATUS_CALL_OK",
    "BLUEPRINT_FIXTURE_CALL_OK",
    "CODEX_CONFIG_RENDER_OK",
    "CODEX_CLI_AVAILABLE",
    "CODEX_SERVER_LISTED",
)


async def _stdio_checks(
    *,
    capture_root: Path,
    fixture_asset: str,
) -> dict[str, bool]:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from arkdev_mcp.contracts import TOOL_NAMES

    checks = {
        "STDIO_HANDSHAKE_OK": False,
        "TOOLS_DISCOVERED": False,
        "STATUS_CALL_OK": False,
        "BLUEPRINT_FIXTURE_CALL_OK": False,
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            str(SCRIPTS / "run_arkdev_mcp.py"),
            "--capture-root",
            str(capture_root),
        ],
        cwd=PROJECT_ROOT,
        encoding="utf-8",
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            async with Client(stdio_client(parameters, errlog=errlog)) as client:
                checks["STDIO_HANDSHAKE_OK"] = True
                tools = (await client.list_tools()).tools
                checks["TOOLS_DISCOVERED"] = tuple(
                    tool.name for tool in tools
                ) == TOOL_NAMES
                status = await client.call_tool("arkdev_status", {})
                checks["STATUS_CALL_OK"] = bool(
                    not status.is_error
                    and status.structured_content
                    and status.structured_content.get("readOnly") is True
                    and status.structured_content.get("transport") == "stdio"
                )
                context = await client.call_tool(
                    "blueprint_get_context",
                    {
                        "asset": fixture_asset,
                        "goal": "ReceiveBeginPlay",
                        "maxHops": 0,
                        "budgetTokens": 2400,
                    },
                )
                checks["BLUEPRINT_FIXTURE_CALL_OK"] = bool(
                    not context.is_error
                    and context.structured_content
                    and context.structured_content.get("nodes")
                )
        except Exception:
            pass
    return checks


def _config_render_ok() -> bool:
    try:
        from arkdev_mcp.contracts import TOOL_NAMES
        from print_codex_mcp_config import render_config

        config = tomllib.loads(render_config(PROJECT_ROOT))
        server = config["mcp_servers"]["arkdev_blueprint"]
        return bool(
            server["command"] == "powershell.exe"
            and tuple(server["enabled_tools"]) == TOOL_NAMES
            and server["required"] is True
            and server["default_tools_approval_mode"] == "auto"
        )
    except Exception:
        return False


def _codex_checks() -> tuple[bool, bool | str]:
    executable = shutil.which("codex")
    if not executable:
        return False, "SKIPPED_WITH_REASON:codex_command_not_found"
    try:
        result = subprocess.run(
            [executable, "mcp", "list"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return True, False
    listed = result.returncode == 0 and "arkdev_blueprint" in result.stdout
    return True, listed


def _render(value: bool | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=PROJECT_ROOT / "captures",
    )
    parser.add_argument("--fixture-asset", default="InterpretationFixture")
    options = parser.parse_args(argv)

    checks: dict[str, bool | str] = {name: False for name in CHECK_ORDER}
    try:
        checks["DEPENDENCY_INSTALLED"] = (
            importlib.metadata.version("mcp") == "2.0.0"
        )
    except importlib.metadata.PackageNotFoundError:
        checks["DEPENDENCY_INSTALLED"] = False

    if checks["DEPENDENCY_INSTALLED"]:
        try:
            importlib.import_module("arkdev_mcp.server")
            checks["SERVER_IMPORTABLE"] = True
        except Exception:
            checks["SERVER_IMPORTABLE"] = False

    if checks["SERVER_IMPORTABLE"]:
        checks.update(
            asyncio.run(
                _stdio_checks(
                    capture_root=options.capture_root,
                    fixture_asset=options.fixture_asset,
                )
            )
        )

    checks["CODEX_CONFIG_RENDER_OK"] = _config_render_ok()
    cli_available, server_listed = _codex_checks()
    checks["CODEX_CLI_AVAILABLE"] = cli_available
    checks["CODEX_SERVER_LISTED"] = server_listed

    for name in CHECK_ORDER:
        print(f"{name}={_render(checks[name])}")

    required = CHECK_ORDER[:7]
    return 0 if all(checks[name] is True for name in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
