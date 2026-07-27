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
            for index in range(1, 13)
        ],
    )
    connection.executemany(
        """
        INSERT INTO domain_memberships VALUES (
            ?, ?, 'TEST', 'HIGH', 'CONFIRMED',
            'fixture://domain', 'test/v1'
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
            (index, (index % 12) + 1)
            for index in range(1, 13)
        ],
    )
    connection.commit()
    return connection


class KnowledgeBenchmarkContractTests(unittest.TestCase):
    def test_balanced_shape_has_all_negative_cases_and_domain_floor(self):
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
        domains = Counter(case.primary_domain for case in cases)
        self.assertTrue(
            all(domains[domain] >= 5 for domain in MAJOR_DOMAINS)
        )
        connection.close()

    def test_materialized_120_queries_run_with_bounded_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "core.sqlite"
            connection = _fixture(path)
            counts = materialize_benchmark_queries(connection)
            connection.commit()
            connection.close()
            self.assertEqual(counts["benchmarkQueries"], 120)
            result = run_query_benchmark(path)
        self.assertEqual(result["total"], 120)
        self.assertEqual(result["tierCounts"], TIER_COUNTS)
        self.assertEqual(result["unresolved"], 0)
        self.assertGreaterEqual(result["completeOrBoundedRate"], 0.70)
        self.assertGreaterEqual(result["simpleDbOnlyRate"], 0.90)
        self.assertLessEqual(result["contextTokens"]["maximum"], 2_000)
        self.assertLess(result["latencyMs"]["p95"], 250)
        self.assertLess(result["latencyMs"]["twoHopP95"], 800)


if __name__ == "__main__":
    unittest.main()
