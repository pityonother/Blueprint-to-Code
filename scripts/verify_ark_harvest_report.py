#!/usr/bin/env python3
"""Verify that a compact ARK harvest report remains faithful to its full source."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_report_validation import validate_harvest_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", type=Path, required=True, help="harvest_ranking_*.full.json")
    parser.add_argument("--ai", type=Path, required=True, help="harvest_ranking_*.ai.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    full_text = args.full.read_text(encoding="utf-8")
    ai_text = args.ai.read_text(encoding="utf-8")
    full_payload = json.loads(full_text)
    ai_payload = json.loads(ai_text)
    result = validate_harvest_report(
        full_payload,
        ai_payload,
        full_path=args.full,
        full_characters=len(full_text),
        ai_characters=len(ai_text),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
