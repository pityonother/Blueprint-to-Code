"""Run bounded ARK Dev MCP diagnostics with disposable local Task metadata."""

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
)

EDITOR_CHECKS = (
    "EDITOR_BRIDGE_STATE_FOUND",
    "EDITOR_BRIDGE_STATE_FRESH",
    "EDITOR_BRIDGE_CONNECTED",
    "EDITOR_ACTIVE_ASSET_AVAILABLE",
    "EDITOR_ACTIVE_GRAPH_AVAILABLE",
    "EDITOR_GRAPH_POSITIONS_AVAILABLE",
    "EDITOR_SELECTION_AVAILABLE",
    "EDITOR_EVIDENCE_BINDING_AVAILABLE",
)

CORE_REQUIRED = tuple(
    name
    for name in CHECK_ORDER
    if name not in {*EDITOR_CHECKS, "CODEX_CLI_AVAILABLE", "CODEX_SERVER_LISTED"}
)


async def _stdio_checks(
    *,
    capture_root: Path,
    fixture_asset: str,
    editor_state_file: Path,
) -> dict[str, bool | str]:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from arkdev_mcp.contracts import TOOL_NAMES

    checks = {
        "STDIO_HANDSHAKE_OK": False,
        "TOOLS_DISCOVERED": False,
        "STATUS_CALL_OK": False,
        "EDITOR_BRIDGE_STATE_FOUND": editor_state_file.is_file(),
        "EDITOR_BRIDGE_STATE_FRESH": False,
        "EDITOR_BRIDGE_CONNECTED": False,
        "EDITOR_ACTIVE_ASSET_AVAILABLE": False,
        "EDITOR_ACTIVE_GRAPH_AVAILABLE": False,
        "EDITOR_GRAPH_POSITIONS_AVAILABLE": False,
        "EDITOR_SELECTION_AVAILABLE": False,
        "EDITOR_EVIDENCE_BINDING_AVAILABLE": False,
        "BLUEPRINT_FIXTURE_CALL_OK": False,
        "TASK_CREATE_OK": False,
        "TASK_RESUME_OK": False,
        "TASK_RESEARCH_OK": False,
        "TASK_CACHE_HIT_OK": False,
        "PATCH_PLAN_DRAFT_OK": False,
        "PATCH_PLAN_VALIDATE_OK": False,
    }
    with tempfile.TemporaryDirectory(prefix="arkdev-mcp-diagnose-") as temporary:
        task_root = str(Path(temporary) / ".blueprint-tasks")
        child_env = {
            "ARKDEV_MCP_TASK_ROOT": task_root,
            "ARKDEV_EDITOR_BRIDGE_STATE_FILE": str(editor_state_file),
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                str(SCRIPTS / "run_arkdev_mcp.py"),
                "--capture-root",
                str(capture_root),
            ],
            env=child_env,
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
                        and status.structured_content.get("taskMetadataWrite") is True
                        and status.structured_content.get("transport") == "stdio"
                    )
                    editor = await client.call_tool(
                        "arkdev_editor_state",
                        {
                            "includeSelection": True,
                            "includeGraphNodes": True,
                            "maxGraphNodes": 1,
                        },
                    )
                    editor_payload = editor.structured_content or {}
                    snapshot = editor_payload.get("snapshot") or {}
                    checks["EDITOR_BRIDGE_CONNECTED"] = bool(
                        not editor.is_error and editor_payload.get("connected")
                    )
                    checks["EDITOR_BRIDGE_STATE_FRESH"] = bool(
                        checks["EDITOR_BRIDGE_CONNECTED"]
                        and isinstance(snapshot.get("ageMs"), int)
                    )
                    checks["EDITOR_ACTIVE_ASSET_AVAILABLE"] = bool(
                        editor_payload.get("activeAsset")
                    )
                    checks["EDITOR_ACTIVE_GRAPH_AVAILABLE"] = bool(
                        editor_payload.get("activeGraph")
                    )
                    checks["EDITOR_GRAPH_POSITIONS_AVAILABLE"] = bool(
                        "READ_GRAPH_POSITIONS"
                        in editor_payload.get("capabilities", [])
                        and editor_payload.get("graphNodes")
                    )
                    selection_status = editor_payload.get("selectionStatus")
                    checks["EDITOR_SELECTION_AVAILABLE"] = (
                        True
                        if selection_status == "AVAILABLE"
                        else (
                            "SKIPPED_WITH_REASON:unsupported_by_devkit_build"
                            if selection_status == "UNSUPPORTED_BY_DEVKIT_BUILD"
                            else False
                        )
                    )
                    checks["EDITOR_EVIDENCE_BINDING_AVAILABLE"] = bool(
                        (editor_payload.get("activeAssetBinding") or {}).get(
                            "status"
                        )
                        == "EXACT"
                        and (editor_payload.get("activeGraphBinding") or {}).get(
                            "status"
                        )
                        == "EXACT"
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
                    created = await client.call_tool(
                        "blueprint_task_create",
                        {
                            "mode": "IMPLEMENTATION_PREP",
                            "asset": fixture_asset,
                            "goal": "Diagnose the local Task and Patch Plan workflow",
                            "completionCriteria": ["Exact Evidence refs remain valid"],
                            "allowedChanges": ["Selected Blueprint graph"],
                            "forbiddenChanges": ["Other assets"],
                        },
                    )
                    checks["TASK_CREATE_OK"] = bool(
                        not created.is_error
                        and created.structured_content
                        and created.structured_content.get("taskId")
                    )
                    if checks["TASK_CREATE_OK"]:
                        task_id = created.structured_content["taskId"]
                        resumed = await client.call_tool(
                            "blueprint_task_resume", {"taskId": task_id}
                        )
                        checks["TASK_RESUME_OK"] = bool(
                            not resumed.is_error
                            and resumed.structured_content
                            and resumed.structured_content.get("phase") == "DISCOVERY"
                        )
                        first = await client.call_tool(
                            "blueprint_task_research",
                            {
                                "taskId": task_id,
                                "question": "ReceiveBeginPlay execution flow",
                                "maxHops": 0,
                            },
                        )
                        checks["TASK_RESEARCH_OK"] = bool(
                            not first.is_error
                            and first.structured_content
                            and first.structured_content.get("nodes")
                            and first.structured_content.get("cached") is False
                        )
                        second = await client.call_tool(
                            "blueprint_task_research",
                            {
                                "taskId": task_id,
                                "question": "ReceiveBeginPlay execution flow",
                                "maxHops": 0,
                            },
                        )
                        checks["TASK_CACHE_HIT_OK"] = bool(
                            not second.is_error
                            and second.structured_content
                            and second.structured_content.get("cached") is True
                            and second.structured_content.get("querySignature")
                            == first.structured_content.get("querySignature")
                        )
                        if checks["TASK_RESEARCH_OK"]:
                            node = first.structured_content["nodes"][0]
                            pins = [
                                pin
                                for pin in first.structured_content.get("pins", [])
                                if pin.get("nodeRef") == node.get("ref")
                            ]
                            signatures = [
                                {
                                    "name": pin.get("name", ""),
                                    "direction": (
                                        "OUTPUT"
                                        if pin.get("direction") == "EGPD_Output"
                                        else "INPUT"
                                    ),
                                    "category": pin.get("category", ""),
                                    "subcategory": pin.get("subcategory", ""),
                                    "ordinal": int(
                                        str(pin.get("ref", "")).rsplit("/", 1)[-1]
                                    ),
                                    "containerType": pin.get("containerType", "None"),
                                }
                                for pin in pins
                            ]
                            graph_ref = node.get("graphRef")
                            draft = await client.call_tool(
                                "blueprint_patch_plan_draft",
                                {
                                    "taskId": task_id,
                                    "nodes": [
                                        {
                                            "nodeRef": node.get("ref"),
                                            "graphRef": graph_ref,
                                            "signature": {
                                                "nodeFamily": "EXISTING",
                                                "className": node.get("className"),
                                                "functionOwner": "",
                                                "functionName": "",
                                                "variableName": "",
                                                "eventName": str(
                                                    node.get("signals", {}).get(
                                                        "event", ""
                                                    )
                                                ),
                                                "pure": False,
                                                "pinSignatures": signatures,
                                            },
                                        }
                                    ],
                                    "operations": [
                                        {
                                            "operationId": "op://preserve-diagnostic",
                                            "kind": "PRESERVE",
                                            "graphRef": graph_ref,
                                            "dependsOn": [],
                                            "preconditions": [
                                                {"nodeRef": node.get("ref")}
                                            ],
                                            "payload": {"nodeRef": node.get("ref")},
                                            "postconditions": [{"unchanged": True}],
                                            "checkpoint": "checkpoint://diagnostic",
                                        }
                                    ],
                                    "capabilityRequirements": [],
                                    "checkpoints": [
                                        {
                                            "checkpointId": "checkpoint://diagnostic",
                                            "description": "Diagnostic preserve check",
                                        }
                                    ],
                                    "blockingQuestions": [],
                                },
                            )
                            checks["PATCH_PLAN_DRAFT_OK"] = bool(
                                not draft.is_error
                                and draft.structured_content
                                and draft.structured_content.get("status") == "DRAFT"
                            )
                            if checks["PATCH_PLAN_DRAFT_OK"]:
                                validation = await client.call_tool(
                                    "blueprint_patch_plan_validate",
                                    {
                                        "taskId": task_id,
                                        "planId": draft.structured_content["planId"],
                                    },
                                )
                                checks["PATCH_PLAN_VALIDATE_OK"] = bool(
                                    not validation.is_error
                                    and validation.structured_content
                                    and validation.structured_content.get("valid") is True
                                    and validation.structured_content.get(
                                        "executionReady"
                                    )
                                    is False
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
    parser.add_argument("--editor-state-file", type=Path)
    parser.add_argument("--editor-fixture-state-file", type=Path)
    options = parser.parse_args(argv)

    editor_state_file = (
        options.editor_state_file
        or PROJECT_ROOT / ".arkdev-bridge" / "editor_state.json"
    )

    checks: dict[str, bool | str] = {name: False for name in CHECK_ORDER}
    fixture_checks: dict[str, bool | str] | None = None
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
        if options.editor_fixture_state_file is not None:
            fixture_checks = asyncio.run(
                _stdio_checks(
                    capture_root=options.capture_root,
                    fixture_asset=options.fixture_asset,
                    editor_state_file=options.editor_fixture_state_file,
                )
            )
        checks.update(
            asyncio.run(
                _stdio_checks(
                    capture_root=options.capture_root,
                    fixture_asset=options.fixture_asset,
                    editor_state_file=editor_state_file,
                )
            )
        )

    checks["CODEX_CONFIG_RENDER_OK"] = _config_render_ok()
    cli_available, server_listed = _codex_checks()
    checks["CODEX_CLI_AVAILABLE"] = cli_available
    checks["CODEX_SERVER_LISTED"] = server_listed

    for name in CHECK_ORDER:
        print(f"{name}={_render(checks[name])}")

    if options.editor_fixture_state_file is not None:
        if fixture_checks is None:
            fixture_checks = {name: False for name in CHECK_ORDER}
        for name in EDITOR_CHECKS:
            print(f"FIXTURE_{name}={_render(fixture_checks[name])}")

    return 0 if all(checks[name] is True for name in CORE_REQUIRED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
