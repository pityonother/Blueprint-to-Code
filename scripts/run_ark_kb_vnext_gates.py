from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.quality_gates import (  # noqa: E402
    evaluate_quality_gates,
    publish_gate_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fail-closed ARK KB vNext quality gates."
    )
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        default=PROJECT_ROOT / "knowledge_base" / "vnext",
    )
    parser.add_argument(
        "--discovery-database",
        type=Path,
        required=True,
    )
    return parser


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    snapshot_root = _absolute(args.snapshot_root)
    report = evaluate_quality_gates(
        project_root=PROJECT_ROOT,
        snapshot_root=snapshot_root,
        discovery_database=_absolute(args.discovery_database),
    )
    cutover = publish_gate_report(
        snapshot_root=snapshot_root,
        report=report,
    )
    print(
        json.dumps(
            {
                "schema": report["schema"],
                "buildId": report["buildId"],
                "summary": report["summary"],
                "cutover": cutover,
                "reportUri": "reports/quality_gates.json",
                "benchmarkUri": "reports/query_benchmark.json",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["summary"]["cutoverEligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
