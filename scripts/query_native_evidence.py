#!/usr/bin/env python
"""CLI adapter for bounded Native Evidence queries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.native_evidence_repository import (  # noqa: E402
    open_native_evidence_repository,
)


def _budget(parser: argparse.ArgumentParser, default: int) -> None:
    parser.add_argument(
        "--budget",
        type=int,
        default=default,
        help="Whole-response estimated-token budget (minimum 500, cap 8000).",
    )


def _paging(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--page-size", type=int, default=25)
    parser.add_argument("--cursor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query Native Evidence v2.")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="operation", required=True)

    overview = commands.add_parser("overview")
    _budget(overview, 700)

    search = commands.add_parser("search")
    search.add_argument("--query", required=True)
    _paging(search)
    _budget(search, 900)

    function = commands.add_parser("function")
    function.add_argument("--id", required=True)
    function.add_argument("--include-decompile", action="store_true")
    function.add_argument("--snippet-chars", type=int, default=600)
    _paging(function)
    _budget(function, 1200)

    for name in ("callers", "callees"):
        relation = commands.add_parser(name)
        relation.add_argument("--id", required=True)
        relation.add_argument("--depth", type=int, default=1)
        _paging(relation)
        _budget(relation, 1400)

    fields = commands.add_parser("field-accesses")
    fields.add_argument("--query", default="")
    fields.add_argument("--id", default="")
    _paging(fields)
    _budget(fields, 1200)

    constants = commands.add_parser("constants")
    constants.add_argument("--query", default="")
    constants.add_argument("--id", default="")
    _paging(constants)
    _budget(constants, 1000)

    gaps = commands.add_parser("gaps")
    gaps.add_argument("--id", default="")
    gaps.add_argument("--reason-code", default="")
    _paging(gaps)
    _budget(gaps, 800)

    links = commands.add_parser("blueprint-links")
    links.add_argument("--id", default="")
    links.add_argument("--source-id", default="")
    _paging(links)
    _budget(links, 1000)
    return parser


def request_from_args(args: argparse.Namespace) -> dict[str, object]:
    request: dict[str, object] = {
        "operation": args.operation,
        "budgetTokens": args.budget,
    }
    mappings = {
        "query": "query",
        "id": "id",
        "depth": "depth",
        "page_size": "pageSize",
        "cursor": "cursor",
        "snippet_chars": "snippetChars",
        "reason_code": "reasonCode",
        "source_id": "sourceId",
    }
    for attribute, key in mappings.items():
        if not hasattr(args, attribute):
            continue
        value = getattr(args, attribute)
        if value not in (None, ""):
            request[key] = value
    if getattr(args, "include_decompile", False):
        request["includeDecompile"] = True
    return request


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with open_native_evidence_repository(args.evidence_dir) as repository:
            response = repository.query(request_from_args(args))
        exit_code = 0
    except Exception as exc:
        response = {
            "schema": "blueprint-to-code-native-query-error/v1",
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 1
    print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
