#!/usr/bin/env python3
"""Validate a local observation directory used by Harvest ranking overlays."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_runtime_observations import (  # noqa: E402
    load_harvest_runtime_observations,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("observation_root", type=Path)
    args = parser.parse_args(argv)
    try:
        index = load_harvest_runtime_observations(args.observation_root)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "schema": "blueprint-to-code.harvest-runtime-ranking-validation/v1",
                "observationRevision": index.revision,
                "filesScanned": index.files_scanned,
                "syntheticExcluded": index.synthetic_excluded,
                "publishableExactRows": len(index.rows),
                "boundary": {
                    "syntheticCanPopulatePublicObservedFields": False,
                    "runtimeObservationChangesStaticEvidence": False,
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
