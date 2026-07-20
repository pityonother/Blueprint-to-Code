"""Atomically rebuild bounded agent indexes from published Evidence Store databases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.evidence_writer import refresh_agent_index


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild agent_index.md from each immutable evidence.sqlite revision."
    )
    parser.add_argument("--asset-dir", action="append", type=Path, default=[])
    parser.add_argument("--capture-root", type=Path)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--expected-asset-count", type=int)
    args = parser.parse_args(argv)
    if args.all:
        if args.capture_root is None:
            parser.error("--all requires --capture-root")
        if args.asset_dir:
            parser.error("--all cannot be combined with --asset-dir")
    elif args.capture_root is not None:
        parser.error("--capture-root requires --all")
    elif not args.asset_dir:
        parser.error("provide --asset-dir or --capture-root ... --all")
    if args.expected_asset_count is not None and args.expected_asset_count < 0:
        parser.error("--expected-asset-count must be non-negative")
    return args


def discover_asset_dirs(capture_root: Path) -> list[Path]:
    root = capture_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    return sorted(
        {database.parent.parent.resolve() for database in root.rglob("evidence/evidence.sqlite")},
        key=lambda path: str(path).casefold(),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        asset_dirs = (
            discover_asset_dirs(args.capture_root)
            if args.all
            else sorted(
                {path.expanduser().resolve() for path in args.asset_dir},
                key=lambda path: str(path).casefold(),
            )
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for asset_dir in asset_dirs:
        try:
            refreshed = refresh_agent_index(asset_dir)
            results.append(
                {
                    "assetDir": str(asset_dir),
                    "asset": str(refreshed.get("asset_name") or asset_dir.name),
                    "revisionId": str(refreshed.get("revision_id") or ""),
                    "gapCount": int(refreshed.get("gap_count") or 0),
                    "estimatedTokens": int(refreshed.get("estimated_tokens") or 0),
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "assetDir": str(asset_dir),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    errors: list[str] = []
    if args.expected_asset_count is not None and len(asset_dirs) != args.expected_asset_count:
        errors.append(
            f"selected {len(asset_dirs)} assets; expected {args.expected_asset_count}"
        )
    payload = {
        "schema": "ark.blueprint.evidence-index-rebuild.v1",
        "selected": len(asset_dirs),
        "passed": len(results),
        "failed": len(failures),
        "results": results,
        "failures": failures,
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if not failures and not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
