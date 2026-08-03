"""Migrate one Blueprint Evidence Store v2 asset directory to v3."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.evidence_publication import migrate_v2_evidence_to_v3  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish one Blueprint Evidence Store v2 asset directory as v3."
    )
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument(
        "--prune-v2",
        action="store_true",
        help="Remove v2 artifacts only after the v3 migration succeeds.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        result = migrate_v2_evidence_to_v3(
            args.asset_dir,
            prune_v2=args.prune_v2,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    payload = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
