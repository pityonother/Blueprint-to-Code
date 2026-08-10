"""Start the Windows-only ARK Dev MCP over stdio."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the read-only ARK Dev Blueprint MCP over stdio."
    )
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=PROJECT_ROOT / "captures",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = _arguments(argv)
    if os.name != "nt":
        print("ARK Dev MCP Phase 1 supports Windows only.", file=sys.stderr)
        return 2
    try:
        from arkdev_mcp.server import create_server
    except ModuleNotFoundError:
        print(
            "ARK Dev MCP dependency is missing. Run "
            "./scripts/install_arkdev_mcp.ps1.",
            file=sys.stderr,
        )
        return 2

    server = create_server(options.capture_root)
    if options.self_test:
        return 0
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
