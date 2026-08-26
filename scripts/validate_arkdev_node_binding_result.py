"""Validate one path-free ARK DevKit node-binding request/result pair."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from arkdev_scripting_probe.node_binding import validate_request, validate_result  # noqa: E402


def _load(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, Mapping):
        raise ValueError("contract root must be a JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an exact-node binding result against its request."
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        request = validate_request(_load(arguments.request))
        result = validate_result(_load(arguments.result), request=request)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        print("ARKDEV_NODE_BINDING_RESULT=ERROR", file=sys.stderr)
        return 2
    summary = result["summary"]
    assert isinstance(summary, Mapping)
    print("ARKDEV_NODE_BINDING_RESULT=PASS")
    print(f"REQUESTED_NODES={summary['requested']}")
    print(f"EXACT_NODE_BINDINGS={summary['exact']}")
    print(f"NODE_BINDING_ROUTE={result['route']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
