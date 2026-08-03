#!/usr/bin/env python3
"""Repeatable cold/warm benchmark for Harvest Ranking Contract v2.

The runner exercises bounded forward and reverse queries only.  It never
materializes the species x node/resource cross product and never writes the
generated Harvest datasets it reads.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    VARIANT_CANONICAL,
)
from blueprint_translator.harvest_node_repository import (  # noqa: E402
    HarvestNodeRepository,
)
from blueprint_translator.resource_nodes import canonical_package_path  # noqa: E402


BENCHMARK_SCHEMA = "blueprint-to-code.harvest-ranking-benchmark/v1"
GATE_THRESHOLD_SET = "windows-local-real-dataset-2026-08-03-v1"
# Calibrated from two clean Python 3.13 processes on the repository's real
# 1328-node/1406-creature snapshot.  The two runs took 189.2 s and 182.4 s.
# Thresholds preserve the architectural distinction between the one-time
# reverse baseline build and cached requests; they add 19%-90% headroom to the
# observed maxima rather than hiding regressions behind one loose total time.
DEFAULT_PERFORMANCE_THRESHOLDS: dict[str, float | int] = {
    "latencyMs.forward.cold.p50": 225.0,
    "latencyMs.forward.cold.p95": 300.0,
    "latencyMs.forward.cold.maximum": 400.0,
    "latencyMs.forward.warm.p50": 5.0,
    "latencyMs.forward.warm.p95": 10.0,
    "latencyMs.forward.warm.maximum": 20.0,
    "latencyMs.reverse.cold.p50": 750.0,
    "latencyMs.reverse.cold.p95": 40_000.0,
    "latencyMs.reverse.cold.maximum": 150_000.0,
    "latencyMs.reverse.warm.p50": 225.0,
    "latencyMs.reverse.warm.p95": 250.0,
    "latencyMs.reverse.warm.maximum": 300.0,
    "memory.rssDeltaBytes": 96 * 1024 * 1024,
    "memory.rssAfterBytes": 192 * 1024 * 1024,
}
EXPECTED_DATASET_SCALE: dict[str, int] = {
    "nodeDefinitions": 1328,
    "creatureAssets": 1406,
    "evaluationRows": 20130,
    "nodeResourceOccurrences": 9100,
    "uniqueEvaluationKeys": 903,
}
DEFAULT_NODE_CATALOG = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json"
)
DEFAULT_RANKING_REPORT = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_ranking_all_resources.query.json"
)
DEFAULT_EVALUATION_CATALOG = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_evaluation_catalog.json"
)
DEFAULT_SQLITE_CATALOG = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "harvest_catalog.sqlite"
)
DEFAULT_RUNTIME_OBSERVATIONS = (
    PROJECT_ROOT / "analysis" / "harvest_rankings" / "runtime_observations"
)


class BenchmarkRepository(Protocol):
    def rankings(
        self,
        node_id: str,
        node_resource_id: str,
        **kwargs: object,
    ) -> Mapping[str, object]: ...

    def creature_specialties(
        self,
        species_key: str,
        **kwargs: object,
    ) -> Mapping[str, object]: ...


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _stable_digest(*parts: object) -> str:
    identity = "\0".join(str(part or "") for part in parts)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def build_forward_targets(
    node_catalog: Mapping[str, Any],
    *,
    limit: int = 128,
) -> list[tuple[str, str]]:
    """Choose unique node/resource identities independent of input ordering."""

    identities: set[tuple[str, str]] = set()
    nodes = node_catalog.get("nodes")
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("id") or "").strip()
        resources = node.get("resources")
        items = resources.get("items") if isinstance(resources, Mapping) else None
        for resource in items if isinstance(items, list) else []:
            if not isinstance(resource, Mapping):
                continue
            node_resource_id = str(resource.get("nodeResourceId") or "").strip()
            if node_id and node_resource_id:
                identities.add((node_id, node_resource_id))
    ordered = sorted(
        identities,
        key=lambda value: (_stable_digest(*value), value[0], value[1]),
    )
    return ordered[: max(0, int(limit))]


def build_reverse_species(
    evaluation_catalog: Mapping[str, Any],
    *,
    limit: int = 20,
) -> list[str]:
    """Choose unique species identities independent of catalog ordering."""

    species = {
        str(creature.get("speciesKey") or "").casefold().strip()
        for creature in (
            evaluation_catalog.get("creatures")
            if isinstance(evaluation_catalog.get("creatures"), list)
            else []
        )
        if isinstance(creature, Mapping)
        and str(creature.get("speciesKey") or "").strip()
    }
    ordered = sorted(
        species,
        key=lambda value: (_stable_digest(value), value),
    )
    return ordered[: max(0, int(limit))]


def measure_dataset_scale(
    node_catalog: Mapping[str, Any],
    evaluation_catalog: Mapping[str, Any],
    ranking_report: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Measure the exact real-data identities used by the benchmark gate.

    A unique evaluation key follows the dominance-audit contract:
    HarvestComponent package + resource + entry index + usage scope + model
    version + policy version.  This prevents repeated node occurrences from
    being misreported as independent evaluation work.
    """

    nodes_value = node_catalog.get("nodes")
    nodes = nodes_value if isinstance(nodes_value, list) else []
    creatures_value = evaluation_catalog.get("creatures")
    creatures = creatures_value if isinstance(creatures_value, list) else []
    methodology_value = evaluation_catalog.get("methodology")
    methodology = (
        methodology_value if isinstance(methodology_value, Mapping) else {}
    )
    evaluation_keys: set[tuple[str, str, int | None, str, str, str]] = set()
    occurrences = 0
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        component_value = node.get("harvestComponent")
        component = component_value if isinstance(component_value, Mapping) else {}
        component_package = canonical_package_path(component.get("packagePath"))
        resources_value = node.get("resources")
        resources = resources_value if isinstance(resources_value, Mapping) else {}
        items_value = resources.get("items")
        items = items_value if isinstance(items_value, list) else []
        for resource in items:
            if not isinstance(resource, Mapping):
                continue
            occurrences += 1
            entry_index_value = resource.get("entryIndex")
            entry_index = (
                int(entry_index_value)
                if isinstance(entry_index_value, int)
                and not isinstance(entry_index_value, bool)
                else None
            )
            evaluation_keys.add(
                (
                    component_package.casefold(),
                    str(resource.get("resource") or "").casefold(),
                    entry_index,
                    str(methodology.get("usageScope") or ""),
                    str(methodology.get("formulaVersion") or ""),
                    str(methodology.get("policyVersion") or ""),
                )
            )
    report_coverage_value = (
        ranking_report.get("coverage") if isinstance(ranking_report, Mapping) else {}
    )
    report_coverage = (
        report_coverage_value
        if isinstance(report_coverage_value, Mapping)
        else {}
    )
    evaluation_rows_value = report_coverage.get("rows")
    return {
        "nodeDefinitions": sum(isinstance(node, Mapping) for node in nodes),
        "creatureAssets": sum(
            isinstance(creature, Mapping) for creature in creatures
        ),
        "evaluationRows": (
            int(evaluation_rows_value)
            if isinstance(evaluation_rows_value, int)
            and not isinstance(evaluation_rows_value, bool)
            else 0
        ),
        "nodeResourceOccurrences": occurrences,
        "uniqueEvaluationKeys": len(evaluation_keys),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _latency_summary(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "p50": round(_percentile(values, 0.50), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "maximum": round(max(values, default=0.0), 6),
    }


def current_rss_bytes() -> int:
    """Return current resident memory with standard-library OS facilities."""

    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        if not ok:
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.WorkingSetSize)

    statm = Path("/proc/self/statm")
    if statm.is_file():
        resident_pages = int(statm.read_text(encoding="ascii").split()[1])
        return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    import resource  # noqa: PLC0415 - unavailable on Windows

    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _timed(clock: Callable[[], float], operation: Callable[[], object]) -> float:
    started = clock()
    operation()
    return (clock() - started) * 1000.0


def run_benchmark(
    repository: BenchmarkRepository,
    node_catalog: Mapping[str, Any],
    evaluation_catalog: Mapping[str, Any],
    *,
    ranking_report: Mapping[str, Any] | None = None,
    forward_limit: int = 128,
    reverse_limit: int = 20,
    clock: Callable[[], float] = time.perf_counter,
    rss_bytes: Callable[[], int] = current_rss_bytes,
) -> dict[str, Any]:
    targets = build_forward_targets(node_catalog, limit=forward_limit)
    species = build_reverse_species(evaluation_catalog, limit=reverse_limit)
    if len(targets) < forward_limit:
        raise ValueError(
            f"Benchmark requires {forward_limit} forward targets; found {len(targets)}"
        )
    if len(species) < reverse_limit:
        raise ValueError(
            f"Benchmark requires {reverse_limit} species; found {len(species)}"
        )

    before_rss = rss_bytes()
    latency: dict[str, dict[str, list[float]]] = {
        "forward": {"cold": [], "warm": []},
        "reverse": {"cold": [], "warm": []},
    }
    for cache_state in ("cold", "warm"):
        for node_id, node_resource_id in targets:
            latency["forward"][cache_state].append(
                _timed(
                    clock,
                    lambda node_id=node_id, node_resource_id=node_resource_id: (
                        repository.rankings(
                            node_id,
                            node_resource_id,
                            limit=10,
                            evidence_policy=POLICY_CONFIRMED,
                            variant_policy=VARIANT_CANONICAL,
                            metric=METRIC_STATIC_TOTAL,
                            availability_policy=AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
                        )
                    ),
                )
            )
        for species_key in species:
            latency["reverse"][cache_state].append(
                _timed(
                    clock,
                    lambda species_key=species_key: repository.creature_specialties(
                        species_key,
                        offset=0,
                        limit=1,
                        evidence_policy=POLICY_CONFIRMED,
                        variant_policy=VARIANT_CANONICAL,
                        metric=METRIC_STATIC_TOTAL,
                        availability_policy=AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
                    ),
                )
            )
    after_rss = rss_bytes()
    return {
        "schema": BENCHMARK_SCHEMA,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "selection": {
            "forwardTargets": len(targets),
            "reverseSpecies": len(species),
            "deterministic": True,
        },
        "datasetScale": measure_dataset_scale(
            node_catalog,
            evaluation_catalog,
            ranking_report,
        ),
        "latencyMs": {
            direction: {
                cache_state: _latency_summary(values)
                for cache_state, values in cache_states.items()
            }
            for direction, cache_states in latency.items()
        },
        "memory": {
            "rssBeforeBytes": before_rss,
            "rssAfterBytes": after_rss,
            "rssDeltaBytes": after_rss - before_rss,
        },
        "architecture": {
            "queryMode": "BOUNDED_LAZY_FORWARD_AND_REVERSE",
            "precomputedSpeciesNodeCrossProduct": False,
            "crossProductPairsMaterialized": 0,
        },
    }


def _nested_number(payload: Mapping[str, Any], dotted_path: str) -> float | int:
    value: object = payload
    for part in dotted_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise ValueError(f"Benchmark report is missing gate metric: {dotted_path}")
        value = value[part]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Benchmark gate metric is not numeric: {dotted_path}")
    return value


def evaluate_performance_gate(
    report: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float | int] = DEFAULT_PERFORMANCE_THRESHOLDS,
    expected_dataset_scale: Mapping[str, int] = EXPECTED_DATASET_SCALE,
) -> dict[str, Any]:
    """Return a fail-closed, machine-readable gate for one benchmark report."""

    checks: list[dict[str, Any]] = []
    for metric, limit in thresholds.items():
        actual = _nested_number(report, metric)
        checks.append(
            {
                "metric": metric,
                "actual": actual,
                "operator": "<=",
                "limit": limit,
                "status": "PASS" if actual <= limit else "FAIL",
            }
        )
    scale_value = report.get("datasetScale")
    scale = scale_value if isinstance(scale_value, Mapping) else {}
    for metric, expected in expected_dataset_scale.items():
        actual = scale.get(metric)
        checks.append(
            {
                "metric": f"datasetScale.{metric}",
                "actual": actual,
                "operator": "==",
                "limit": expected,
                "status": "PASS" if actual == expected else "FAIL",
            }
        )
    failed = [str(check["metric"]) for check in checks if check["status"] == "FAIL"]
    return {
        "status": "PASS" if not failed else "FAIL",
        "thresholdSet": GATE_THRESHOLD_SET,
        "checks": checks,
        "failedChecks": failed,
        "calibration": {
            "cleanProcessRuns": 2,
            "elapsedSeconds": [189.2, 182.4],
            "architecture": (
                "One cold reverse request builds the bounded 903-key tier "
                "baseline; later reverse and warm requests use LRU caches."
            ),
        },
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-catalog", type=Path, default=DEFAULT_NODE_CATALOG)
    parser.add_argument("--ranking-report", type=Path, default=DEFAULT_RANKING_REPORT)
    parser.add_argument(
        "--evaluation-catalog", type=Path, default=DEFAULT_EVALUATION_CATALOG
    )
    parser.add_argument("--sqlite-catalog", type=Path, default=DEFAULT_SQLITE_CATALOG)
    parser.add_argument(
        "--runtime-observations", type=Path, default=DEFAULT_RUNTIME_OBSERVATIONS
    )
    parser.add_argument("--forward-targets", type=int, default=128)
    parser.add_argument("--reverse-species", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    node_catalog = _load_object(args.node_catalog)
    ranking_report = _load_object(args.ranking_report)
    evaluation_catalog = _load_object(args.evaluation_catalog)
    repository = HarvestNodeRepository(
        args.node_catalog,
        args.ranking_report,
        evaluation_catalog_path=args.evaluation_catalog,
        sqlite_catalog_path=(
            args.sqlite_catalog if args.sqlite_catalog.is_file() else None
        ),
        runtime_observation_root=(
            args.runtime_observations
            if args.runtime_observations.is_dir()
            else None
        ),
    )
    report = run_benchmark(
        repository,
        node_catalog,
        evaluation_catalog,
        ranking_report=ranking_report,
        forward_limit=max(1, args.forward_targets),
        reverse_limit=max(1, args.reverse_species),
    )
    report["performanceGate"] = evaluate_performance_gate(report)
    if args.output is not None:
        _write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["performanceGate"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
