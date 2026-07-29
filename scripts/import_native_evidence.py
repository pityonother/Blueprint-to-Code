#!/usr/bin/env python
"""Import authoritative Native Evidence v2 JSON into a hash-bound artifact set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.native_evidence_store import (  # noqa: E402
    write_native_evidence_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy authoritative Native Evidence v2 JSON and build its "
            "read-only SQLite/index companions."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    trust = parser.add_mutually_exclusive_group()
    trust.add_argument(
        "--formal",
        dest="formal",
        action="store_true",
        help="Require VERIFIED trust, loaded matching PDB, and a clean generator.",
    )
    trust.add_argument(
        "--allow-experimental",
        dest="formal",
        action="store_false",
        help="Import non-formal evidence while preserving its trust status.",
    )
    parser.set_defaults(formal=True)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = write_native_evidence_artifacts(
            args.source.resolve(),
            args.evidence_dir.resolve(),
            formal=args.formal,
        )
        payload = {
            "ok": True,
            **result,
        }
        exit_code = 0
    except Exception as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 1
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
            default=str,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
