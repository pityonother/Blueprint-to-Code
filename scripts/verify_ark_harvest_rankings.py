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
from blueprint_translator.harvest_runtime_observations import (  # noqa: E402
    load_harvest_runtime_observations,
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
    def query(
        node_id: str,
        node_resource_id: str,
        _limit: int,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = f"{node_id}::{node_resource_id}"
        result: object = payload.get(key)
        if options is not None and isinstance(payload.get("forward"), dict):
            metric_rows = payload["forward"].get(options.get("metric"))
            result = metric_rows.get(key) if isinstance(metric_rows, dict) else None
        if not isinstance(result, dict):
            raise KeyError(f"REFERENCE_RESULT_NOT_FOUND:{key}")
        return result

    return query


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Use an implementation independent of the production ranking formula "
            "to verify eligibility, estimatedYieldPerNode, and Top-N ordering."
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
            "Optional externally captured query results. Legacy/v1 flat: "
            "{\"<nodeId>::<nodeResourceId>\": <response>}. Contract v2: "
            "{\"forward\": {\"<metric>\": "
            "{\"<nodeId>::<nodeResourceId>\": <response>}}}."
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="Verify every eligible node/resource target.")
    selection.add_argument("--sample-size", type=int, default=32)
    parser.add_argument("--seed", default="phase5-v1")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--float-tolerance", type=float, default=1e-9)
    parser.add_argument(
        "--runtime-observations",
        type=Path,
        help=(
            "Optional directory of validated v2 controlled runtime observations. "
            "Observed metrics are explicitly skipped when this is absent."
        ),
    )
    parser.add_argument("--runtime-profile-id")
    parser.add_argument("--include-preliminary", action="store_true")
    parser.add_argument(
        "--reverse-species",
        action="append",
        help="Optional exact speciesKey to verify in reverse specialties (repeatable).",
    )
    parser.add_argument("--reverse-page-size", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        node_catalog = _load_json(args.node_catalog.resolve())
        evaluation_catalog = _load_json(args.evaluation_catalog.resolve())
        runtime_rows = None
        selected_runtime_profile = None
        if args.runtime_observations is not None:
            runtime_index = load_harvest_runtime_observations(
                args.runtime_observations.resolve(),
                runtime_profile_id=args.runtime_profile_id,
                # Keep validated preliminary candidates here so profile identity
                # survives even when no confirmed rows exist.  The independent
                # verifier applies the user-facing inclusion policy below.
                include_preliminary=True,
            )
            runtime_rows = runtime_index.rows
            selected_runtime_profile = runtime_index.runtime_profile_selected
        if args.reference_results is not None:
            reference_query = _reference_file_query(
                _load_json(args.reference_results.resolve())
            )
            reference_specialties_query = None
            reference_source = {
                "mode": "CAPTURED_QUERY_RESULTS",
                "path": str(args.reference_results.resolve()),
            }
        else:
            repository = HarvestNodeRepository(
                args.node_catalog.resolve(),
                args.ranking_catalog.resolve(),
                evaluation_catalog_path=args.evaluation_catalog.resolve(),
                runtime_observation_root=(
                    args.runtime_observations.resolve()
                    if args.runtime_observations is not None
                    else None
                ),
            )
            contract_v2 = (
                evaluation_catalog.get("methodology", {}).get("contractVersion")
                == "harvest-ranking-contract/v2"
            )

            def reference_query(
                node_id: str,
                node_resource_id: str,
                limit: int,
                options: dict[str, Any] | None = None,
            ):
                options = options or {}
                return repository.rankings(
                    node_id,
                    node_resource_id,
                    limit=limit,
                    evidence_policy=(
                        options.get("evidence_policy", "includeConditional")
                        if contract_v2
                        else "confirmed"
                    ),
                    variant_policy=options.get(
                        "variant_policy", "CANONICAL_VARIANT"
                    ),
                    metric=options.get(
                        "metric", "staticCompleteNodeTargetYield"
                    ),
                    availability_policy=options.get(
                        "availability_policy", "GLOBAL_TRANSFER_ALLOWED"
                    ),
                    runtime_profile_id=options.get("runtime_profile_id"),
                    include_preliminary=options.get(
                        "include_preliminary", False
                    ),
                )

            def reference_specialties_query(
                species_key: str,
                offset: int,
                limit: int,
                options: dict[str, Any],
            ):
                return repository.creature_specialties(
                    species_key,
                    offset=offset,
                    limit=limit,
                    evidence_policy=options["evidence_policy"],
                    variant_policy=options["variant_policy"],
                    metric=options["metric"],
                    availability_policy=options["availability_policy"],
                    runtime_profile_id=options.get("runtime_profile_id"),
                    include_preliminary=options.get("include_preliminary", False),
                )

            reference_source = {
                "mode": "LIVE_HARVEST_NODE_REPOSITORY",
                "rankingCatalogPath": str(args.ranking_catalog.resolve()),
                "evidencePolicy": (
                    "includeConditional" if contract_v2 else "legacy"
                ),
            }
        summary = verify_catalogs(
            node_catalog,
            evaluation_catalog,
            reference_query=reference_query,
            reference_specialties_query=reference_specialties_query,
            runtime_observations=runtime_rows,
            runtime_profile_id=selected_runtime_profile,
            include_preliminary=args.include_preliminary,
            reverse_species=args.reverse_species,
            reverse_page_size=args.reverse_page_size,
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
