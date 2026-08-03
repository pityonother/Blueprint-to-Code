"""Migrate an existing legacy Blueprint capture to Evidence Store v2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.artifact_modes import DEFAULT_ARTIFACT_MODE  # noqa: E402
from blueprint_translator.evidence_writer import migrate_asset_capture  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build normalized evidence without deleting legacy capture files.")
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument(
        "--artifact-mode",
        choices=["dual", "indexed"],
        default=DEFAULT_ARTIFACT_MODE,
        help="Defaults to indexed after the validated cutover; both modes preserve existing legacy files.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        result = migrate_asset_capture(args.asset_dir)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = {**result, "artifact_mode": args.artifact_mode, "legacy_artifacts_deleted": False}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
