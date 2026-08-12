"""Validate and summarize the one-shot ARK DevKit WC Graph result."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from arkdev_scripting_probe.wc_graph_snapshot import (
    validate_wc_graph_snapshot_result,
    validator_summary,
)


DEFAULT_FILENAME = "wc-graph-snapshot-result.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed validation for the ARK DevKit WC Graph result.",
    )
    parser.add_argument("result", nargs="?", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    path = args.result or repo_root / ".arkdev-probe" / DEFAULT_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        result = validate_wc_graph_snapshot_result(raw)
        summary = validator_summary(result)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        print("GATE_B_RESULT_CONTRACT=ERROR")
        return 1

    for key, value in summary.items():
        print(f"{key}={value}")
    snapshot = result.get("snapshot")
    if isinstance(snapshot, Mapping):
        print(f"PIN_IDENTITY={snapshot.get('pinIdentity', 'PIN_UNAVAILABLE')}")
    else:
        print("PIN_IDENTITY=PIN_UNAVAILABLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
