"""Benchmark indexed search and a real two-hop Blueprint evidence query.

The default target is the largest checked-in capture, LionfishLion_Character_BP.
The command prints one JSON record and exits non-zero when either p95 latency
exceeds its acceptance threshold.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.evidence_query import EvidenceQueryService  # noqa: E402
from blueprint_translator.evidence_repository import (  # noqa: E402
    resolve_asset_evidence_state,
)


DEFAULT_ITERATIONS = 25
DEFAULT_SEARCH_P95_MS = 100.0
DEFAULT_TWO_HOP_P95_MS = 200.0
DEFAULT_SEARCH_QUERY = "BeginPlay"
DEFAULT_DATABASE = (
    SCRIPT_DIR.parent
    / "captures"
    / "LionfishLion_Character_BP"
    / "evidence"
    / "evidence.sqlite"
)


def _p95(samples: list[float]) -> float:
    if not samples:
        raise ValueError("at least one timing sample is required")
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _measure(
    service: EvidenceQueryService,
    request: Mapping[str, object],
    *,
    iterations: int,
    warmups: int = 3,
) -> list[float]:
    for _ in range(warmups):
        service.query(request)
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        service.query(request)
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def benchmark_database(
    database_path: str | Path,
    *,
    search_query: str = DEFAULT_SEARCH_QUERY,
    iterations: int = DEFAULT_ITERATIONS,
    max_search_p95_ms: float = DEFAULT_SEARCH_P95_MS,
    max_two_hop_p95_ms: float = DEFAULT_TWO_HOP_P95_MS,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> dict[str, Any]:
    path = Path(database_path).expanduser().resolve()
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if max_search_p95_ms < 0 or max_two_hop_p95_ms < 0:
        raise ValueError("latency thresholds must be non-negative")

    search_request: dict[str, object] = {
        "operation": "search",
        "query": search_query,
        "pageSize": 25,
        "budgetTokens": 8000,
    }
    with EvidenceQueryService.open(
        path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    ) as service:
        discovery = service.query(search_request)
        node = next(
            (
                item
                for item in discovery.get("items", [])
                if isinstance(item, Mapping) and item.get("kind") == "node"
            ),
            None,
        )
        if not isinstance(node, Mapping) or not node.get("ref"):
            raise ValueError(
                f"search query {search_query!r} did not return a node for the two-hop benchmark"
            )
        node_ref = str(node["ref"])
        two_hop_request: dict[str, object] = {
            "operation": "neighborhood",
            "selector": {"ref": node_ref},
            "traversal": {
                "maxHops": 2,
                "direction": "both",
                "edgeKinds": ["exec", "data"],
            },
            "budgetTokens": 8000,
        }
        search_samples = _measure(
            service, search_request, iterations=iterations
        )
        two_hop_samples = _measure(
            service, two_hop_request, iterations=iterations
        )

    search_p95 = _p95(search_samples)
    two_hop_p95 = _p95(two_hop_samples)
    errors: list[str] = []
    if search_p95 > max_search_p95_ms:
        errors.append(
            f"search p95 {search_p95:.3f}ms exceeds {max_search_p95_ms:.3f}ms"
        )
    if two_hop_p95 > max_two_hop_p95_ms:
        errors.append(
            f"2-hop p95 {two_hop_p95:.3f}ms exceeds {max_two_hop_p95_ms:.3f}ms"
        )
    return {
        "ok": not errors,
        "database": str(path),
        "iterations": iterations,
        "search": {
            "query": search_query,
            "request": search_request,
            "p95Ms": search_p95,
            "meanMs": sum(search_samples) / len(search_samples),
            "maxP95Ms": max_search_p95_ms,
        },
        "twoHop": {
            "nodeRef": node_ref,
            "request": two_hop_request,
            "p95Ms": two_hop_p95,
            "meanMs": sum(two_hop_samples) / len(two_hop_samples),
            "maxP95Ms": max_two_hop_p95_ms,
        },
        "errors": errors,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--database", type=Path)
    target.add_argument("--asset-dir", type=Path)
    parser.add_argument("--search-query", default=DEFAULT_SEARCH_QUERY)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--max-search-p95-ms", type=float, default=DEFAULT_SEARCH_P95_MS)
    parser.add_argument("--max-two-hop-p95-ms", type=float, default=DEFAULT_TWO_HOP_P95_MS)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    database_path = (
        args.database
        if args.database is not None
        else DEFAULT_DATABASE
    )
    expected_sha256: str | None = None
    expected_size: int | None = None
    try:
        if args.asset_dir is not None:
            state = resolve_asset_evidence_state(args.asset_dir)
            database_path = state.database_path
            expected_sha256 = state.database_sha256
            expected_size = state.database_bytes
        result = benchmark_database(
            database_path,
            search_query=args.search_query,
            iterations=args.iterations,
            max_search_p95_ms=args.max_search_p95_ms,
            max_two_hop_p95_ms=args.max_two_hop_p95_ms,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "database": str(Path(database_path).expanduser().resolve()),
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
