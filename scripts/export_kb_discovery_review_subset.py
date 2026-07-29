from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_review_subset import (  # noqa: E402
    MAX_REVIEW_ROWS,
    export_review_subset,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a bounded Git-readable Discovery review subset."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT
        / "knowledge_base"
        / "discovery_bundle"
        / "kb_discovery.sqlite",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "docs" / "discovery_review",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=MAX_REVIEW_ROWS,
        help=f"Maximum rows per Top export (1-{MAX_REVIEW_ROWS}).",
    )
    parser.add_argument(
        "--source-commit",
        help="Override the recorded source commit (used by reproducibility tests).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = (
        args.database
        if args.database.is_absolute()
        else PROJECT_ROOT / args.database
    )
    output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    result = export_review_subset(
        database_path=database,
        output_dir=output,
        project_root=PROJECT_ROOT,
        limit=args.max_rows,
        source_commit=args.source_commit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
