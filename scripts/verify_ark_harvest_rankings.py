#!/usr/bin/env python3
"""Independently recompute and black-box verify lazy harvest Top-N results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_node_repository import HarvestNodeRepository  # noqa: E402
from blueprint_translator.harvest_ranking_verifier import (  # noqa: E402
    VERIFICATION_SCHEMA,
    verify_catalogs,
)


DEFAULT_NODE_CATALOG = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json"
)
DEFAULT_EVALUATION_CATALOG = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_evaluation_catalog.json"
)
DEFAULT_RANKING_CATALOG = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_ranking_all_resources.query.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_ranking_independent_verification.json"
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _reference_file_query(payload: dict[str, Any]):
    def query(node_id: str, node_resource_id: str, _limit: int) -> dict[str, Any]:
        key = f"{node_id}::{node_resource_id}"
        result = payload.get(key)
        if not isinstance(result, dict):
            raise KeyError(f"REFERENCE_RESULT_NOT_FOUND:{key}")
        return result

    return query


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Use an implementation independent of the production ranking formula "
            "to verify eligibility, engineComparisonIndex, and Top-N ordering."
        )
    )
    parser.add_argument("--node-catalog", type=Path, default=DEFAULT_NODE_CATALOG)
    parser.add_argument(
        "--evaluation-catalog", type=Path, default=DEFAULT_EVALUATION_CATALOG
    )
    parser.add_argument(
        "--ranking-catalog",
        type=Path,
        default=DEFAULT_RANKING_CATALOG,
        help="Legacy query catalog path used only to construct the black-box repository.",
    )
    parser.add_argument(
        "--reference-results",
        type=Path,
        help=(
            "Optional JSON object keyed by nodeId::nodeResourceId. When supplied, "
            "compare against these externally captured query results."
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="Verify every eligible node/resource target.")
    selection.add_argument("--sample-size", type=int, default=32)
    parser.add_argument("--seed", default="phase5-v1")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--float-tolerance", type=float, default=1e-9)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        node_catalog = _load_json(args.node_catalog.resolve())
        evaluation_catalog = _load_json(args.evaluation_catalog.resolve())
        if args.reference_results is not None:
            reference_query = _reference_file_query(
                _load_json(args.reference_results.resolve())
            )
            reference_source = {
                "mode": "CAPTURED_QUERY_RESULTS",
                "path": str(args.reference_results.resolve()),
            }
        else:
            repository = HarvestNodeRepository(
                args.node_catalog.resolve(),
                args.ranking_catalog.resolve(),
                evaluation_catalog_path=args.evaluation_catalog.resolve(),
            )

            def reference_query(node_id: str, node_resource_id: str, limit: int):
                return repository.rankings(
                    node_id, node_resource_id, limit=limit
                )

            reference_source = {
                "mode": "LIVE_HARVEST_NODE_REPOSITORY",
                "rankingCatalogPath": str(args.ranking_catalog.resolve()),
            }
        summary = verify_catalogs(
            node_catalog,
            evaluation_catalog,
            reference_query=reference_query,
            sample_size=None if args.all else args.sample_size,
            seed=args.seed,
            limit=args.limit,
            float_tolerance=args.float_tolerance,
        )
        summary["reference"] = reference_source
    except Exception as exc:
        summary = {
            "schema": VERIFICATION_SCHEMA,
            "status": "ERROR",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        exit_code = 2
    else:
        exit_code = 0 if summary.get("status") == "PASS" else 1
    _atomic_write_json(args.output.resolve(), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
