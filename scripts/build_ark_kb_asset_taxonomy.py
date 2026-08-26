#!/usr/bin/env python3
"""Build a deterministic ARK Asset Registry taxonomy and review sample."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_asset_taxonomy import (  # noqa: E402
    TaxonomyBuildError,
    build_taxonomy,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify every Registry package without loading asset contents, then "
            "select a deterministic multidimensional review sample."
        )
    )
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--class-hierarchy-manifest", type=Path)
    parser.add_argument("--sample-size", type=int, default=160)
    parser.add_argument("--seed", default="ark-taxonomy-v1")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = build_taxonomy(
            arguments.assets,
            arguments.output,
            source_manifest=arguments.source_manifest,
            class_hierarchy_manifest=arguments.class_hierarchy_manifest,
            sample_size=arguments.sample_size,
            seed=arguments.seed,
        )
    except TaxonomyBuildError as exc:
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
