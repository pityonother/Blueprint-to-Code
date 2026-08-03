"""Atomically rebuild bounded agent indexes from published Evidence Store databases."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.evidence_repository import (  # noqa: E402
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_writer import refresh_agent_index  # noqa: E402


def _lexical_absolute(path: str | os.PathLike[str]) -> Path:
    """Return an absolute path without following links or reparse points."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


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
    root = _lexical_absolute(capture_root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    evidence_markers = [
        *root.rglob("evidence/current.json"),
        *root.rglob("evidence/evidence.sqlite"),
    ]
    return sorted(
        {_lexical_absolute(marker.parent.parent) for marker in evidence_markers},
        key=lambda path: str(path).casefold(),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        asset_dirs = (
            discover_asset_dirs(args.capture_root)
            if args.all
            else sorted(
                {_lexical_absolute(path) for path in args.asset_dir},
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
            state = resolve_asset_evidence_state(asset_dir)
            refreshed = refresh_agent_index(asset_dir)
            results.append(
                {
                    "assetDir": str(asset_dir),
                    "asset": str(refreshed.get("asset_name") or asset_dir.name),
                    "revisionId": str(refreshed.get("revision_id") or ""),
                    "gapCount": int(refreshed.get("gap_count") or 0),
                    "estimatedTokens": int(refreshed.get("estimated_tokens") or 0),
                    "sourceKind": state.source_kind,
                    "releaseAuthority": state.release_authority,
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
