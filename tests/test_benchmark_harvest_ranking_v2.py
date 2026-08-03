from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_harvest_ranking_v2 as benchmark  # noqa: E402
from benchmark_harvest_ranking_v2 import (  # noqa: E402
    DEFAULT_PERFORMANCE_THRESHOLDS,
    EXPECTED_DATASET_SCALE,
    build_forward_targets,
    build_reverse_species,
    evaluate_performance_gate,
    measure_dataset_scale,
    run_benchmark,
)


def _node_catalog(count: int = 160) -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": f"node-{index:03d}",
                "resources": {
                    "items": [
                        {
                            "nodeResourceId": f"resource-{index:03d}",
                            "resource": "PrimalItemResource_Test_C",
                        }
                    ]
                },
            }
            for index in range(count)
        ]
    }


def _evaluation_catalog(count: int = 32) -> dict[str, object]:
    return {
        "creatures": [
            {
                "speciesKey": f"species-{index:03d}",
                "objectPath": f"/Game/Dinos/Species{index:03d}.Species{index:03d}_C",
            }
            for index in range(count)
        ]
    }


class _FakeRepository:
    def __init__(self) -> None:
        self.forward_calls: list[tuple[str, str]] = []
        self.reverse_calls: list[str] = []

    def rankings(
        self,
        node_id: str,
        node_resource_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        self.forward_calls.append((node_id, node_resource_id))
        return {"confirmedItems": [], "conditionalItems": []}

    def creature_specialties(
        self,
        species_key: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        self.reverse_calls.append(species_key)
        return {"confirmedItems": [], "conditionalItems": []}


def _clock_for_durations(durations_ms: list[float]):
    values: list[float] = []
    current = 0.0
    for duration_ms in durations_ms:
        values.extend((current, current + (duration_ms / 1000.0)))
        current += (duration_ms / 1000.0) + 0.001
    iterator = iter(values)
    return lambda: next(iterator)


def _passing_gate_report() -> dict[str, object]:
    latency = {
        direction: {
            state: {"p50": 0.0, "p95": 0.0, "maximum": 0.0}
            for state in ("cold", "warm")
        }
        for direction in ("forward", "reverse")
    }
    return {
        "datasetScale": dict(EXPECTED_DATASET_SCALE),
        "latencyMs": latency,
        "memory": {"rssDeltaBytes": 0, "rssAfterBytes": 1},
    }

class HarvestRankingV2BenchmarkTests(unittest.TestCase):
    def test_target_selection_is_deterministic_bounded_and_unique(self) -> None:
        node_catalog = _node_catalog()
        evaluation_catalog = _evaluation_catalog()

        first_targets = build_forward_targets(node_catalog, limit=128)
        second_targets = build_forward_targets(node_catalog, limit=128)
        first_species = build_reverse_species(evaluation_catalog, limit=20)
        second_species = build_reverse_species(evaluation_catalog, limit=20)

        self.assertEqual(first_targets, second_targets)
        self.assertEqual(first_species, second_species)
        self.assertEqual(len(first_targets), 128)
        self.assertEqual(len(set(first_targets)), 128)
        self.assertEqual(len(first_species), 20)
        self.assertEqual(len(set(first_species)), 20)

    def test_benchmark_measures_cold_and_warm_without_cross_product(self) -> None:
        repository = _FakeRepository()
        rss_values = iter((100 * 1024 * 1024, 104 * 1024 * 1024))
        clock = _clock_for_durations(
            ([10.0] * 128)
            + ([20.0] * 20)
            + ([1.0] * 128)
            + ([2.0] * 20)
        )

        report = run_benchmark(
            repository,
            _node_catalog(),
            _evaluation_catalog(),
            ranking_report={"coverage": {"rows": 777}},
            clock=clock,
            rss_bytes=lambda: next(rss_values),
        )

        self.assertEqual(report["selection"]["forwardTargets"], 128)
        self.assertEqual(report["selection"]["reverseSpecies"], 20)
        self.assertEqual(len(repository.forward_calls), 256)
        self.assertEqual(len(repository.reverse_calls), 40)
        self.assertEqual(report["memory"]["rssDeltaBytes"], 4 * 1024 * 1024)
        self.assertGreater(
            report["latencyMs"]["forward"]["cold"]["p50"],
            report["latencyMs"]["forward"]["warm"]["p50"],
        )
        self.assertGreater(
            report["latencyMs"]["reverse"]["cold"]["p50"],
            report["latencyMs"]["reverse"]["warm"]["p50"],
        )
        self.assertEqual(
            report["architecture"]["precomputedSpeciesNodeCrossProduct"],
            False,
        )
        self.assertEqual(
            report["architecture"]["crossProductPairsMaterialized"],
            0,
        )
        for direction in ("forward", "reverse"):
            for cache_state in ("cold", "warm"):
                metrics = report["latencyMs"][direction][cache_state]
                self.assertGreater(metrics["samples"], 0)
                self.assertGreaterEqual(metrics["p95"], metrics["p50"])

    def test_dataset_scale_uses_occurrences_and_unique_evaluation_keys(self) -> None:
        scale = measure_dataset_scale(
            _node_catalog(),
            _evaluation_catalog(),
            {"coverage": {"rows": 777}},
        )

        self.assertEqual(
            scale,
            {
                "nodeDefinitions": 160,
                "creatureAssets": 32,
                "evaluationRows": 777,
                "nodeResourceOccurrences": 160,
                "uniqueEvaluationKeys": 1,
            },
        )

    def test_performance_gate_fails_for_latency_or_dataset_drift(self) -> None:
        report = _passing_gate_report()
        report["latencyMs"]["forward"]["warm"]["p95"] = (  # type: ignore[index]
            DEFAULT_PERFORMANCE_THRESHOLDS["latencyMs.forward.warm.p95"] + 0.001
        )
        report["datasetScale"]["uniqueEvaluationKeys"] = 902  # type: ignore[index]

        gate = evaluate_performance_gate(report)

        self.assertEqual(gate["status"], "FAIL")
        self.assertEqual(
            gate["failedChecks"],
            [
                "latencyMs.forward.warm.p95",
                "datasetScale.uniqueEvaluationKeys",
            ],
        )

    def test_cli_returns_nonzero_and_prints_structured_gate_on_failure(self) -> None:
        report = _passing_gate_report()
        report["latencyMs"]["reverse"]["cold"]["maximum"] = (  # type: ignore[index]
            DEFAULT_PERFORMANCE_THRESHOLDS[
                "latencyMs.reverse.cold.maximum"
            ]
            + 1
        )
        stdout = io.StringIO()
        with (
            mock.patch.object(
                benchmark,
                "_load_object",
                side_effect=[_node_catalog(), {}, _evaluation_catalog()],
            ),
            mock.patch.object(benchmark, "HarvestNodeRepository", return_value=object()),
            mock.patch.object(benchmark, "run_benchmark", return_value=report),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = benchmark.main(
                [
                    "--node-catalog",
                    "node.json",
                    "--ranking-report",
                    "ranking.json",
                    "--evaluation-catalog",
                    "evaluation.json",
                ]
            )

        output = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(output["performanceGate"]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
