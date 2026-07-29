"""CLI for runtime-observation comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.runtime_calibration import (  # noqa: E402
    compare_runtime_observations,
    render_runtime_comparison_markdown,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare a static model with runtime observation trials."
    )
    parser.add_argument("observation", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        payload = json.loads(args.observation.read_text(encoding="utf-8-sig"))
        result = compare_runtime_observations(payload)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    serialized = json.dumps(
        result,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
        sort_keys=True,
        separators=None if args.pretty else (",", ":"),
    ) + "\n"
    if args.json_out:
        _write(args.json_out, serialized)
    if args.markdown_out:
        _write(args.markdown_out, render_runtime_comparison_markdown(result))
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
