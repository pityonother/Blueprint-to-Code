#!/usr/bin/env python3
"""Build conservative UNKNOWN class clusters and Chinese review artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_asset_unknown_clustering import (  # noqa: E402
    UnknownClusterBuildError,
    build_unknown_clusters,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deduplicate technically UNKNOWN ARK assets by exact Unreal class "
            "path, emit review-only similar-name families, and produce Chinese "
            "classification reports without loading asset contents."
        )
    )
    parser.add_argument(
        "--taxonomy-manifest",
        required=True,
        type=Path,
        help="Verified ark.kb.asset-taxonomy-manifest.v1 input.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New output directory; existing directories are rejected.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = build_unknown_clusters(
            arguments.taxonomy_manifest,
            arguments.output,
        )
    except UnknownClusterBuildError as exc:
        print(
            json.dumps(
                {"status": "FAILED", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
