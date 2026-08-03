from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.report_query import (  # noqa: E402
    DEFAULT_REPORT_QUERY_BUDGET,
    MAX_REPORT_QUERY_BUDGET,
    REPORT_FILES,
    build_report_view,
    read_report_source,
)


def report_inventory(asset_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in REPORT_FILES:
        try:
            path, text, _metadata = read_report_source(asset_dir, key)
        except (FileNotFoundError, ValueError):
            continue
        rows.append(
            {
                "report": key,
                "path": str(path.resolve()),
                "bytes": len(text.encode("utf-8")),
                "estimated_tokens": estimate_tokens(text),
                "default_read": key in {"context_pack", "asset_memory_card"},
            }
        )
    return sorted(rows, key=lambda row: (not bool(row["default_read"]), int(row["estimated_tokens"])))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a Blueprint Markdown report through a token-bounded outline, section, or search view."
    )
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--report", default="asset_report", help="Known report key or path relative to the asset directory.")
    parser.add_argument("--mode", choices=("outline", "meta", "section", "search", "full"), default="outline")
    parser.add_argument("--query", default="")
    parser.add_argument("--section", default="")
    parser.add_argument(
        "--section-start-line",
        "--section-line",
        dest="section_line",
        type=int,
        help="Disambiguate repeated headings by their 1-based start line.",
    )
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument(
        "--budget",
        "--budget-tokens",
        dest="budget",
        type=int,
        default=DEFAULT_REPORT_QUERY_BUDGET,
        help=(
            f"Maximum estimated tokens returned in content "
            f"(default: {DEFAULT_REPORT_QUERY_BUDGET}, hard max: {MAX_REPORT_QUERY_BUDGET})."
        ),
    )
    parser.add_argument("--context-lines", type=int, default=2)
    parser.add_argument("--list", action="store_true", help="List available reports and their estimated token sizes.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of the Markdown view.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    asset_dir = args.asset_dir.expanduser().resolve()
    if not asset_dir.is_dir():
        print(f"Asset directory not found: {asset_dir}", file=sys.stderr)
        return 2

    if args.list:
        rows = report_inventory(asset_dir)
        if args.json:
            print(json.dumps({"asset_dir": str(asset_dir), "reports": rows}, ensure_ascii=False, indent=2))
        else:
            print("# Blueprint Report Inventory")
            print()
            print(f"- Asset directory: {asset_dir}")
            print()
            print("| Report | Bytes | Estimated tokens | Default read |")
            print("|---|---:|---:|---|")
            for row in rows:
                print(
                    f"| {row['report']} | {row['bytes']} | {row['estimated_tokens']} | "
                    f"{'yes' if row['default_read'] else 'no'} |"
                )
        return 0

    try:
        report_path, report_text, _metadata = read_report_source(
            asset_dir,
            args.report,
        )
        result = build_report_view(
            report_text,
            mode=args.mode,
            query=args.query,
            section=args.section,
            section_start_line=args.section_line,
            cursor=args.cursor,
            token_budget=max(int(args.budget or 0), 1),
            context_lines=max(int(args.context_lines or 0), 0),
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result["path"] = str(report_path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"<!-- report={args.report} mode={args.mode} estimated_tokens={result['estimated_tokens']} -->")
        if result["truncated"]:
            print(f"<!-- truncated=true next_cursor={result['next_cursor']} -->")
        print(result["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
