#!/usr/bin/env python3
"""Validate and atomically roll back the ARK KB vNext current pointer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.pointer_cas import (  # noqa: E402
    PointerCASUncertainStateError,
)
from blueprint_translator.kb_vnext.snapshot import (  # noqa: E402
    rollback_current_snapshot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an existing immutable snapshot, then atomically "
            "replace only the current pointer."
        )
    )
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        default=PROJECT_ROOT / "knowledge_base" / "vnext",
    )
    parser.add_argument("--to-build-id", required=True)
    parser.add_argument(
        "--expected-current-build-id",
        required=True,
        help="Fail closed if current changed before the rollback.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the target and expected current without swapping.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = rollback_current_snapshot(
            output_dir=args.snapshot_root,
            target_build_id=args.to_build_id,
            expected_current_build_id=args.expected_current_build_id,
            dry_run=args.dry_run,
        )
    except PointerCASUncertainStateError as exc:
        print(
            json.dumps(
                {
                    "schema": "ark-kb-vnext-rollback/v1",
                    "status": "UNCERTAIN",
                    "error": str(exc),
                    "pointerUpdated": None,
                    "pointerCAS": exc.receipt,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema": "ark-kb-vnext-rollback/v1",
                    "status": "BLOCKED",
                    "error": str(exc),
                    "pointerUpdated": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
