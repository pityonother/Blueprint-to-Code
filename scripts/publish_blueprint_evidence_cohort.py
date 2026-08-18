"""Publish a reviewed Blueprint Evidence cohort into the canonical capture root."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.cohort_publication import (  # noqa: E402
    publish_evidence_cohort,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish reviewed FRESH/current-v3 Blueprint Evidence sources and "
            "their current Interpretation into a canonical capture root."
        )
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=100_000)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        result = publish_evidence_cohort(
            plan_path=args.plan,
            source_root=args.source_root,
            capture_root=args.capture_root,
            budget=args.budget,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
