#!/usr/bin/env python3
"""Build the indexed SQLite companion for the ARK resource-node JSON catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_catalog_sqlite import (  # noqa: E402
    convert_resource_node_catalog,
)


PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CATALOG = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "harvest_catalog.sqlite"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert resource_node_catalog.json to an indexed SQLite catalog."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = convert_resource_node_catalog(args.catalog, args.output)
    print(json.dumps({"ok": True, **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
