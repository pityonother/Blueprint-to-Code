from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
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


def _report_uris(snapshot_root: Path, build_id: str) -> tuple[str, str]:
    immutable = (
        (snapshot_root / "current.json").is_file()
        or (snapshot_root / "manifest.json").is_file()
    )
    report_root = f"reports/{build_id}" if immutable else "reports"
    return (
        f"{report_root}/quality_gates.json",
        f"{report_root}/query_benchmark.json",
    )


def _diagnostic_report_metadata(
    snapshot_root: Path,
    build_id: str,
    benchmark: object,
) -> dict[str, str]:
    if not isinstance(benchmark, Mapping):
        return {}
    artifacts = benchmark.get("diagnosticArtifacts")
    if not isinstance(artifacts, Mapping):
        return {}
    case_results = artifacts.get("caseResults")
    failure_matrix = artifacts.get("failureMatrix")
    if not isinstance(case_results, Mapping) or not isinstance(
        failure_matrix,
        Mapping,
    ):
        return {}
    immutable = (
        (snapshot_root / "current.json").is_file()
        or (snapshot_root / "manifest.json").is_file()
    )
    report_root = f"reports/{build_id}" if immutable else "reports"
    return {
        "caseResultsUri": (
            f"{report_root}/query_case_results.jsonl"
        ),
        "caseResultsSha256": str(case_results.get("sha256") or ""),
        "failureMatrixUri": (
            f"{report_root}/query_failure_matrix.json"
        ),
        "failureMatrixSha256": str(
            failure_matrix.get("sha256") or ""
        ),
    }


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
    report_uri, benchmark_uri = _report_uris(
        snapshot_root,
        str(report["buildId"]),
    )
    diagnostic_metadata = _diagnostic_report_metadata(
        snapshot_root,
        str(report["buildId"]),
        report["benchmark"],
    )
    print(
        json.dumps(
            {
                "schema": report["schema"],
                "buildId": report["buildId"],
                "summary": report["summary"],
                "cutover": cutover,
                "reportUri": report_uri,
                "benchmarkUri": benchmark_uri,
                **diagnostic_metadata,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["summary"]["cutoverEligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
