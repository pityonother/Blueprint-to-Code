#!/usr/bin/env python3
"""Thin CLI for building the ARK harvest evaluation catalog."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest.build.ancestry import trace_primal_dino_ancestry  # noqa: E402,F401
from blueprint_translator.harvest.build.asset_projection import (  # noqa: E402,F401
    _attack_applicability,
    _rideability,
    build_creature_record,
)
from blueprint_translator.harvest.build.catalog_builder import (  # noqa: E402
    build_ai_view,
    build_catalog,
)
from blueprint_translator.harvest.build.constants import (  # noqa: E402,F401
    CREATURE_EXTRACTOR_VERSION,
    DEFAULT_AI_OUTPUT,
    DEFAULT_DEVKIT_ROOT,
    DEFAULT_OUTPUT,
    DEFAULT_RANKING_REPORT,
    DEFAULT_SCAN_CACHE,
)
from blueprint_translator.harvest.build.creature_discovery import (  # noqa: E402,F401
    _open_creature_scan_cache,
    discover_creature_candidates,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover every PrimalDinoCharacter-derived asset and build a compact "
            "catalog for lazy node/resource Top-10 evaluation."
        )
    )
    parser.add_argument("--devkit-root", type=Path, default=DEFAULT_DEVKIT_ROOT)
    parser.add_argument("--ranking-report", type=Path, default=DEFAULT_RANKING_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ai-output", type=Path, default=DEFAULT_AI_OUTPUT)
    parser.add_argument("--scan-cache", type=Path, default=DEFAULT_SCAN_CACHE)
    parser.add_argument("--no-scan-cache", action="store_true")
    parser.add_argument("--refresh-scan-cache", action="store_true")
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Optional diagnostic limit; 0 scans every discovered candidate.",
    )
    return parser.parse_args(argv)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_catalog(args)
    ai_payload = build_ai_view(payload)
    _atomic_write_text(
        args.output,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
    )
    _atomic_write_text(
        args.ai_output,
        json.dumps(ai_payload, ensure_ascii=False, indent=2) + "\n",
    )
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "revision": payload["dataset"]["revision"],
                "coverage": payload["coverage"],
                "output": str(args.output.resolve()),
                "aiOutput": str(args.ai_output.resolve()),
                "bytes": args.output.stat().st_size,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
