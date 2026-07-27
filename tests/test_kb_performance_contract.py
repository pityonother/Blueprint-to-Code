from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.benchmark import (  # noqa: E402
    MAJOR_DOMAINS,
    NEGATIVE_CASES,
    TIER_COUNTS,
    build_benchmark_cases,
    materialize_benchmark_queries,
    run_query_benchmark,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


def _fixture(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(FULL_CORE_SCHEMA_SQL)
    connection.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'fixture', 'fixture://benchmark', 'sha', 'test', 'v1',
            '2026-07-27T00:00:00Z', 'FRESH'
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind,
            display_name, internal_name, status, confidence
        ) VALUES (?, ?, 'BLUEPRINT_ASSET', ?, ?, 'CONFIRMED', 'HIGH')
        """,
        [
            (
                index,
                f"/Game/Test/Asset_{index}.Asset_{index}",
                f"Asset {index}",
                f"Asset_{index}",
            )
            for index in range(1, len(MAJOR_DOMAINS) + 1)
        ],
    )
    connection.executemany(
        """
        INSERT INTO domain_memberships(
            entity_id, domain_id, membership_kind, confidence,
            status, evidence_id, ontology_version, source_revision_id
        ) VALUES (
            ?, ?, 'TEST', 'HIGH', 'CONFIRMED',
            'fixture://domain', 'test/v1', 1
        )
        """,
        [
            (index, domain)
            for index, domain in enumerate(MAJOR_DOMAINS, start=1)
        ],
    )
    connection.executemany(
        """
        INSERT INTO edges(
            source_entity_id, target_entity_id, edge_type,
            edge_strength, status, confidence, source_revision_id,
            evidence_uri, source_property, source_graph
        ) VALUES (?, ?, 'REGISTERS', 'HARD', 'CONFIRMED', 'HIGH', 1,
                  'fixture://edge', 'FixtureProperty', '')
        """,
        [
            (index, (index % len(MAJOR_DOMAINS)) + 1)
            for index in range(1, len(MAJOR_DOMAINS) + 1)
        ],
    )
    connection.commit()
    return connection


class KnowledgeBenchmarkContractTests(unittest.TestCase):
    def test_fixed_shape_has_all_categories_and_negative_families(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(FULL_CORE_SCHEMA_SQL)
        connection.execute(
            """
            INSERT INTO entities(
                entity_id, canonical_uri, entity_kind, display_name,
                internal_name, status, confidence
            ) VALUES (
                1, '/Game/Test/Only.Only', 'BLUEPRINT_ASSET',
                'Only', 'Only', 'CONFIRMED', 'HIGH'
            )
            """
        )
        cases = build_benchmark_cases(connection)
        self.assertEqual(
            Counter(case.tier for case in cases),
            Counter(TIER_COUNTS),
        )
        self.assertEqual(
            {case.negative_case for case in cases if case.negative_case},
            set(NEGATIVE_CASES),
        )
        self.assertGreaterEqual(len(cases), 130)
        self.assertGreaterEqual(
            sum(bool(case.negative_case) for case in cases),
            20,
        )
        connection.close()

    def test_sparse_core_exposes_strict_route_mismatches_without_semantic_credit(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "core.sqlite"
            connection = _fixture(path)
            expected_routes = {
                case.query_id: str(case.expected["route"])
                for case in build_benchmark_cases(connection)
            }
            counts = materialize_benchmark_queries(connection)
            connection.commit()
            connection.close()
            self.assertEqual(counts["benchmarkQueries"], 130)
            result = run_query_benchmark(path)
        route_matches = [
            item
            for item in result["results"]
            if item["route"] == expected_routes[item["queryId"]]
        ]
        route_mismatches = [
            item
            for item in result["results"]
            if item["route"] != expected_routes[item["queryId"]]
        ]
        self.assertEqual(result["total"], 130)
        self.assertEqual(result["tierCounts"], TIER_COUNTS)
        self.assertEqual(len(route_matches), 42)
        self.assertEqual(len(route_mismatches), 88)
        self.assertEqual(
            sum(item["protocolCompliance"] for item in route_matches),
            1,
        )
        self.assertTrue(
            all(not item["protocolCompliance"] for item in route_mismatches)
        )
        self.assertEqual(
            sum(item["wrongAnswer"] for item in route_matches),
            41,
        )
        self.assertTrue(all(item["wrongAnswer"] for item in route_mismatches))
        self.assertEqual(result["unresolved"], 129)
        self.assertEqual(
            result["protocolComplianceRate"],
            1 / result["total"],
        )
        self.assertEqual(result["semanticAnswerRate"], 0.0)
        self.assertEqual(
            result["wrongAnswerRate"],
            129 / result["total"],
        )
        self.assertEqual(
            sum(item["expectedGapMatched"] for item in result["results"]),
            1,
        )
        self.assertEqual(result["goldSet"]["fixedGoldCases"], 130)
        self.assertEqual(result["goldSet"]["humanGoldCases"], 5)
        self.assertFalse(result["goldSet"]["corpusReadyForCutover"])
        self.assertTrue(result["identityOnlyNotCountedAsSemantic"])
        self.assertLessEqual(result["contextTokens"]["maximum"], 2_000)
        self.assertLess(result["latencyMs"]["p95"], 250)
        self.assertLess(result["latencyMs"]["p99"], 250)
        self.assertLess(result["latencyMs"]["twoHopP95"], 800)
        self.assertFalse(result["storagePathCoverage"]["complete"])


if __name__ == "__main__":
    unittest.main()
