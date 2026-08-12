"""Validate the local live probe and print only a path-free capability matrix."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

_SUMMARY_ORDER = (
    "PYTHON_RUNTIME",
    "UNREAL_IMPORT",
    "ASSET_EDITOR_SUBSYSTEM",
    "BLUEPRINT_EDITOR_LIBRARY",
    "EDITOR_UTILITY_SUBSYSTEM",
    "EXPLICIT_ASSET_LOAD",
    "EXPLICIT_GRAPH_FIND",
    "GRAPH_NODE_ENUMERATION",
    "NODE_GUID_READ",
    "NODE_POSITION_READ",
    "ACTIVE_ASSET_READ",
    "FOCUSED_GRAPH_READ",
    "SELECTION_READ",
    "DIRTY_STATE_READ",
    "COMPILE_STATE_READ",
    "EXPLICIT_GRAPH_SNAPSHOT",
)


def main() -> int:
    from arkdev_scripting_probe.result_reader import (
        render_validator_summary,
        validator_summary,
    )

    output_root = ROOT / ".arkdev-probe"
    summary = validator_summary(
        output_root / "live-probe.json",
        output_root / "explicit-graph-snapshot.json",
    )
    print(render_validator_summary(summary))
    return 1 if "ERROR" in summary.values() else 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        print("\n".join(f"{key}=ERROR" for key in _SUMMARY_ORDER))
        exit_code = 1
    raise SystemExit(exit_code)
