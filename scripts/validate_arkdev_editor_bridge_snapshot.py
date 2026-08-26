"""Validate one local Editor Bridge snapshot and print a path-free summary."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.editor_bridge_file import FileEditorBridge  # noqa: E402


def _render(value: bool | str | int | None) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path)
    options = parser.parse_args(argv)

    override = os.environ.get("ARKDEV_EDITOR_BRIDGE_STATE_FILE")
    state_file = (
        options.state_file
        or (Path(override) if override else None)
        or PROJECT_ROOT / ".arkdev-bridge" / "editor_state.json"
    )
    bridge = FileEditorBridge(state_file)
    state = bridge.get_state(
        include_selection=True,
        include_graph_nodes=False,
        max_graph_nodes=200,
    )
    health = bridge.health()
    summary: dict[str, bool | str | int | None] = {
        "EDITOR_BRIDGE_STATE_FOUND": state_file.is_file(),
        "EDITOR_BRIDGE_STATE_STATUS": str(health.get("stateStatus") or ""),
        "EDITOR_BRIDGE_STATE_FRESH": bool(state.get("connected")),
        "EDITOR_BRIDGE_CONNECTED": bool(state.get("connected")),
        "EDITOR_BRIDGE_REASON_CODE": str(state.get("reasonCode") or ""),
        "EDITOR_BRIDGE_SEQUENCE": state.get("snapshot", {}).get("sequence", 0),
        "EDITOR_BRIDGE_AGE_MS": state.get("snapshot", {}).get("ageMs"),
        "EDITOR_ACTIVE_ASSET_AVAILABLE": bool(state.get("activeAsset")),
        "EDITOR_ACTIVE_GRAPH_AVAILABLE": bool(state.get("activeGraph")),
        "EDITOR_GRAPH_POSITIONS_AVAILABLE": (
            "READ_GRAPH_POSITIONS" in state.get("capabilities", [])
        ),
        "EDITOR_SELECTION_AVAILABLE": (
            True
            if state.get("selectionStatus") == "AVAILABLE"
            else (
                "SKIPPED_WITH_REASON:unsupported_by_devkit_build"
                if state.get("selectionStatus") == "UNSUPPORTED_BY_DEVKIT_BUILD"
                else False
            )
        ),
    }
    for name, value in summary.items():
        print(f"{name}={_render(value)}")
    return 0 if state.get("connected") else 1


if __name__ == "__main__":
    raise SystemExit(main())
