"""Extract likely Blueprint graph names from a local ARK DevKit .uasset file."""

from __future__ import annotations

import argparse
from pathlib import Path

from blueprint_translator.uasset_graphs import mine_graph_candidates, write_graph_candidate_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = PROJECT_ROOT / "captures"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine graph-page candidate names from an ARK DevKit Blueprint .uasset.")
    parser.add_argument("asset_path", help="Blueprint Object Path, e.g. /Game/Foo/MyBP.MyBP")
    parser.add_argument("--content-root", action="append", default=[], help="ARK DevKit Content root to search.")
    parser.add_argument("--capture-root", default=str(CAPTURE_ROOT), help="Output captures directory.")
    parser.add_argument("--max-candidates", type=int, default=1600)
    args = parser.parse_args(argv)

    payload, attempted = mine_graph_candidates(args.asset_path, extra_roots=args.content_root, max_candidates=args.max_candidates)
    paths = write_graph_candidate_files(args.asset_path, Path(args.capture_root), payload)

    print("Asset:", payload.get("asset_path") or args.asset_path)
    print("UAsset:", payload.get("uasset_path") or "not found")
    print("Raw strings:", payload.get("raw_string_count", 0))
    print("Candidates:", payload.get("candidate_count", 0))
    print("Candidate JSON:", paths["json"])
    print("Candidate TXT:", paths["text"])
    if not payload.get("uasset_path"):
        print("Attempted paths:")
        for item in attempted:
            print("  " + item)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
