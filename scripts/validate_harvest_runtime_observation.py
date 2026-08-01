#!/usr/bin/env python3
"""Validate one exact Harvest runtime ranking observation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_runtime_observations import (  # noqa: E402
    validate_harvest_runtime_observation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("observation", type=Path)
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Validate a field template but do not accept it as publishable runtime data.",
    )
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.observation.read_text(encoding="utf-8-sig"))
        result = validate_harvest_runtime_observation(payload)
        if result["synthetic"] and not args.allow_synthetic:
            raise ValueError(
                "Synthetic observations are not legal public ranking measurements."
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
