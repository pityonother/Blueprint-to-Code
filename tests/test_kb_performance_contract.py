from __future__ import annotations

import json
import shutil
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
    BenchmarkCase,
    _copy_snapshot_for_benchmark,
    _expected_gap_requirement,
    _runtime_performance_gates,
    build_benchmark_cases,
    materialize_benchmark_queries,
    run_query_benchmark,
    run_storage_path_benchmark,
)
from blueprint_translator.kb_vnext.map_usage import (  # noqa: E402
    MAP_USAGE_EDGE_TYPES,
)
from blueprint_translator.kb_vnext.quality_gates import (  # noqa: E402
    _query_benchmark_gates,
)
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SourceManifest,
    SourceRevision,
    source_id,
    source_manifest_binding,
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
        ) VALUES (?, ?, 'REFERENCES_OBJECT', 'HARD', 'CONFIRMED', 'HIGH', 1,
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
    def test_gap_requirements_share_map_and_relationship_contracts(self):
        base = {
            "query_id": "gap-contract",
            "question": "What is missing?",
            "category": "MAP",
            "primary_domain": "map_world",
            "entity": "/Game/Test/Asset.Asset",
            "expected": {
                "route": "EVIDENCE_REQUIRED",
                "gapCodes": ["MAP_USAGE_INCOMPLETE"],
                "semanticExpectation": "GAP_ONLY",
            },
            "review_status": "FIXTURE_EXACT",
            "protocol_boundary_only": True,
        }
        map_case = BenchmarkCase(
            **base,
            request={
                "entity": base["entity"],
                "edgeTypes": [],
                "requiresMapEvidence": True,
            },
        )
        relationship_case = BenchmarkCase(
            **{
                **base,
                "category": "RELATIONSHIP",
                "primary_domain": "inventory",
                "request": {
                    "entity": base["entity"],
                    "edgeTypes": ["OWNS_COMPONENT"],
                },
            }
        )

        map_requirement = _expected_gap_requirement(
            map_case,
            "MAP_USAGE_INCOMPLETE",
        )
        relationship_requirement = _expected_gap_requirement(
            relationship_case,
            "REFERENCE_CLOSURE_OPEN",
        )

        self.assertEqual(
            map_requirement,
            (
                f"{', '.join(MAP_USAGE_EDGE_TYPES)}: confirmed typed "
                "direct, PCG, or World Partition map usage"
            ),
        )
        self.assertEqual(
            relationship_requirement,
            "OWNS_COMPONENT:confirmed edge evidence",
        )

    def test_storage_benchmark_uses_real_fts_and_cache_validation_paths(self):
        test_root = PROJECT_ROOT / "tests"
        if str(test_root) not in sys.path:
            sys.path.insert(0, str(test_root))
        from test_kb_api import _snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "vnext"
            _snapshot(root)
            report = run_storage_path_benchmark(
                root,
                sample_count=3,
                include_timing=True,
            )
            original_cache = sqlite3.connect(root / "cache.sqlite")
            try:
                original_cache_rows = int(
                    original_cache.execute(
                        "SELECT COUNT(*) FROM query_snapshots"
                    ).fetchone()[0]
                )
            finally:
                original_cache.close()

        self.assertEqual(report["error"], "")
        self.assertTrue(report["coverage"]["complete"])
        timing = report["timingDiagnostics"]
        self.assertEqual(timing["schema"], "ark-kb-segment-timing/v1")
        for segment in (
            "pointerManifestResolution",
            "connectionAcquire",
            "cacheValidation",
            "cacheWrite",
        ):
            metrics = timing["segments"][segment]
            self.assertGreater(metrics["samples"], 0)
            self.assertTrue({"p50", "p95", "p99"} <= set(metrics))
        self.assertGreater(
            timing["segments"]["cacheValidation"]["queryCount"],
            0,
        )
        self.assertGreater(timing["segments"]["cacheWrite"]["queryCount"], 0)
        self.assertTrue(report["search"]["ftsPlanUsed"])
        self.assertEqual(
            set(report["search"]["paths"]),
            {
                "EXACT_CANONICAL_URI",
                "EXACT_ALIAS",
                "FTS_PHRASE",
                "FUZZY_CANDIDATE",
            },
        )
        for metrics in report["search"]["paths"].values():
            self.assertTrue(metrics["matchTypeObserved"])
            self.assertEqual(metrics["samples"], 3)
            self.assertTrue(
                {"p50", "p95", "p99"} <= set(metrics)
            )
        self.assertEqual(
            report["search"]["coldOperation"]["samples"],
            1,
        )
        self.assertEqual(
            report["search"]["warmOperation"]["samples"],
            3,
        )
        for key in (
            "validHit",
            "expiredRejected",
            "sourceRevisionRejected",
            "invalidationTokenRejected",
            "buildRejected",
        ):
            self.assertTrue(report["cache"][key], key)
        self.assertEqual(
            report["cache"]["coldOperation"]["samples"],
            1,
        )
        self.assertEqual(
            report["cache"]["warmOperation"]["samples"],
            3,
        )
        for store in (
            "core.sqlite",
            "search.sqlite",
            "cache.sqlite",
        ):
            connection = report["connections"][store]
            for mode in ("coldConnection", "warmConnection"):
                self.assertEqual(connection[mode]["samples"], 3)
                self.assertTrue(
                    {"p50", "p95", "p99"} <= set(connection[mode])
                )
        self.assertEqual(original_cache_rows, 0)

    def test_preseal_staging_storage_benchmark_requires_explicit_context(self):
        test_root = PROJECT_ROOT / "tests"
        if str(test_root) not in sys.path:
            sys.path.insert(0, str(test_root))
        from test_kb_api import _snapshot

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            legacy = base / "legacy-fixture"
            _snapshot(legacy)
            staging = base / "candidate-staging"
            staging.mkdir()
            for name in (
                "catalog.sqlite",
                "core.sqlite",
                "search.sqlite",
                "cache.sqlite",
            ):
                shutil.copy2(legacy / name, staging / name)
            shutil.copytree(
                legacy / "domain_exports",
                staging / "domain_exports",
            )
            manifest = json.loads(
                (legacy / "manifests" / "current.json").read_text(
                    encoding="utf-8"
                )
            )
            generated_at = str(manifest["generatedAt"])
            semantic_inputs = manifest["source"]["inputs"]
            source_manifest = SourceManifest(
                entries=tuple(
                    SourceRevision(
                        source_id=source_id(
                            "SEMANTIC_INPUT",
                            f"semantic-input://{key}",
                        ),
                        source_kind="SEMANTIC_INPUT",
                        source_uri=f"semantic-input://{key}",
                        fingerprint=str(fingerprint),
                    )
                    for key, fingerprint in semantic_inputs.items()
                )
                + (
                    SourceRevision(
                        source_id=source_id(
                            "SEMANTIC_INPUT",
                            "semantic-input://runtimeObservations",
                        ),
                        source_kind="SEMANTIC_INPUT",
                        source_uri=(
                            "semantic-input://runtimeObservations"
                        ),
                        fingerprint="9" * 64,
                    ),
                ),
                generated_at=generated_at,
            )
            manifest["incrementalUpdate"] = source_manifest_binding(
                source_manifest
            )
            (staging / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            rejected = run_storage_path_benchmark(
                staging,
                sample_count=1,
            )
            measured = run_storage_path_benchmark(
                staging,
                sample_count=1,
                allow_unsealed_snapshot=True,
            )

        self.assertIn(
            "no sealed quality report",
            rejected["error"],
        )
        self.assertFalse(rejected["coverage"]["complete"])
        self.assertEqual(measured["error"], "")
        self.assertTrue(measured["coverage"]["complete"])
        self.assertTrue(measured["search"]["ftsPlanUsed"])
        self.assertTrue(measured["cache"]["validHit"])

    def test_unsealed_context_never_bypasses_a_declared_seal(self):
        test_root = PROJECT_ROOT / "tests"
        if str(test_root) not in sys.path:
            sys.path.insert(0, str(test_root))
        from test_kb_snapshot_atomicity import (
            _fixture_build_id,
            _staging,
        )

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            build_id = _fixture_build_id("11:22:33")
            staging, manifest = _staging(base, build_id)
            (staging / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            report_path = (
                staging / "reports" / "quality_gates.json"
            )
            report_path.write_bytes(report_path.read_bytes() + b" ")

            measured = run_storage_path_benchmark(
                staging,
                sample_count=1,
                allow_unsealed_snapshot=True,
            )

        self.assertIn(
            "sealed quality report hash is invalid",
            measured["error"],
        )
        self.assertFalse(measured["coverage"]["complete"])

    def test_storage_benchmark_rejects_manifest_path_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source_root = base / "source" / "snapshot"
            manifests = source_root / "manifests"
            manifests.mkdir(parents=True)
            isolated_root = base / "target" / "snapshot"
            isolated_root.mkdir(parents=True)
            payload = {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": "unsafe-build",
                "databases": {"../escape.sqlite": {}},
            }
            manifest_text = json.dumps(payload)
            (manifests / "current.json").write_text(
                manifest_text,
                encoding="utf-8",
            )
            (manifests / "unsafe-build.json").write_text(
                manifest_text,
                encoding="utf-8",
            )
            escaped_source = source_root.parent / "escape.sqlite"
            escaped_source.write_text("source", encoding="utf-8")
            escaped_target = isolated_root.parent / "escape.sqlite"
            escaped_target.write_text("do-not-overwrite", encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "Unsafe snapshot database path",
            ):
                _copy_snapshot_for_benchmark(
                    source_root,
                    isolated_root,
                )

            self.assertEqual(
                escaped_target.read_text(encoding="utf-8"),
                "do-not-overwrite",
            )

    def test_benchmark_copy_resolves_one_immutable_snapshot_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            configured_root = base / "configured"
            build_id = "fixture-build"
            snapshot = (
                configured_root / "snapshots" / build_id
            )
            snapshot.mkdir(parents=True)
            database_names = (
                "catalog.sqlite",
                "core.sqlite",
                "search.sqlite",
                "cache.sqlite",
            )
            for name in database_names:
                connection = sqlite3.connect(snapshot / name)
                connection.execute(
                    "CREATE TABLE metadata(key TEXT, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO metadata VALUES('snapshot_build_id', ?)",
                    (build_id,),
                )
                connection.commit()
                connection.close()
            manifest = {
                "schema": "ark-kb-vnext-snapshot/v1",
                "buildId": build_id,
                "databases": {name: {} for name in database_names},
                "qualityGates": {
                    "reportUri": "reports/quality_gates.json",
                    "benchmarkUri": "reports/query_benchmark.json",
                    "caseResultsUri": (
                        "reports/query_case_results.jsonl"
                    ),
                    "failureMatrixUri": (
                        "reports/query_failure_matrix.json"
                    ),
                },
            }
            reports = snapshot / "reports"
            reports.mkdir()
            (reports / "quality_gates.json").write_text(
                '{"fixture": true}',
                encoding="utf-8",
            )
            (reports / "query_benchmark.json").write_text(
                '{"fixture": true}',
                encoding="utf-8",
            )
            (reports / "query_case_results.jsonl").write_text(
                '{"caseId":"fixture"}\n',
                encoding="utf-8",
            )
            (reports / "query_failure_matrix.json").write_text(
                '{"fixture": true}',
                encoding="utf-8",
            )
            (snapshot / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            (configured_root / "current.json").write_text(
                json.dumps(
                    {
                        "buildId": build_id,
                        "snapshotRelativePath": (
                            f"snapshots/{build_id}"
                        ),
                    }
                ),
                encoding="utf-8",
            )

            for source in (configured_root, snapshot):
                isolated = base / f"isolated-{source.name}"
                isolated.mkdir()

                _copy_snapshot_for_benchmark(source, isolated)

                pointer = json.loads(
                    (isolated / "current.json").read_text(
                        encoding="utf-8"
                    )
                )
                copied = (
                    isolated
                    / "snapshots"
                    / build_id
                )
                self.assertEqual(
                    pointer["snapshotRelativePath"],
                    f"snapshots/{build_id}",
                )
                self.assertEqual(
                    json.loads(
                        (copied / "manifest.json").read_text(
                            encoding="utf-8"
                        )
                    ),
                    manifest,
                )
                self.assertTrue(
                    all((copied / name).is_file() for name in database_names)
                )
                self.assertTrue(
                    all(
                        (copied / "reports" / name).is_file()
                        for name in (
                            "quality_gates.json",
                            "query_benchmark.json",
                            "query_case_results.jsonl",
                            "query_failure_matrix.json",
                        )
                    )
                )

    def test_runtime_performance_gates_are_fail_closed_and_sample_bound(self):
        functional_check_names = {
            "ftsPlanUsed",
            "cacheValidHit",
            "cacheExpiredRejected",
            "cacheSourceRevisionRejected",
            "cacheInvalidationTokenRejected",
            "cacheBuildRejected",
        }
        missing = _runtime_performance_gates({}, {})
        self.assertTrue(
            all(
                not check["passed"]
                for check in missing["checks"].values()
            )
        )

        storage = {
            "search": {
                "ftsPlanUsed": True,
                "paths": {
                    "FUZZY_CANDIDATE": {
                        "samples": 19,
                        "p95": 1.0,
                    }
                },
            },
            "cache": {
                "validHit": True,
                "expiredRejected": True,
                "sourceRevisionRejected": True,
                "invalidationTokenRejected": True,
                "buildRejected": True,
                "hit": {"samples": 19, "p95": 1.0},
            },
        }
        degree = {
            "oneHop": {"samples": 1, "p95": 1.0},
            "twoHop": {"samples": 1, "p95": 1.0},
            "byPath": {
                name: {
                    "requested": 20,
                    "available": 1,
                    "samples": 1,
                }
                for name in (
                    "TOP_OUT_DEGREE",
                    "TOP_IN_DEGREE",
                    "TOP_CROSS_DOMAIN",
                    "RANDOM_MEDIAN_DEGREE",
                )
            },
        }
        gates = _runtime_performance_gates(storage, degree)
        self.assertTrue(
            all(
                gates["checks"][name]["passed"]
                for name in functional_check_names
            )
        )
        self.assertFalse(gates["checks"]["fuzzyP95"]["passed"])
        self.assertFalse(gates["checks"]["cacheHitP95"]["passed"])
        self.assertTrue(
            gates["checks"]["degreeCohortsCovered"]["passed"]
        )
        self.assertTrue(gates["checks"]["oneHopP95"]["passed"])
        self.assertTrue(gates["checks"]["twoHopP95"]["passed"])
        missing_cohort = {
            **degree,
            "byPath": {
                **degree["byPath"],
                "TOP_CROSS_DOMAIN": {
                    "requested": 20,
                    "available": 1,
                    "samples": 0,
                },
            },
        }
        self.assertFalse(
            _runtime_performance_gates(
                storage,
                missing_cohort,
            )["checks"]["degreeCohortsCovered"]["passed"]
        )

        global_gates = {
            str(gate["id"]): gate
            for gate in _query_benchmark_gates(
                {"performanceGates": gates}
            )
        }
        self.assertTrue(
            global_gates["queries.search_fts_plan_used"]["passed"]
        )
        self.assertFalse(
            global_gates["queries.search_fuzzy_p95_ms"]["passed"]
        )
        absent_global_gates = {
            str(gate["id"]): gate
            for gate in _query_benchmark_gates({})
        }
        self.assertTrue(
            all(
                not absent_global_gates[gate_id]["passed"]
                for gate_id in (
                    "queries.search_fts_plan_used",
                    "queries.cache_valid_hit",
                    "queries.cache_expired_rejected",
                    "queries.cache_source_revision_rejected",
                    "queries.cache_invalidation_token_rejected",
                    "queries.cache_build_rejected",
                    "queries.degree_cohorts_covered",
                    "queries.search_fuzzy_p95_ms",
                )
            )
        )

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
        self.assertNotIn("timingDiagnostics", result)
        self.assertEqual(result["tierCounts"], TIER_COUNTS)
        self.assertTrue(
            all(
                item["schema"]
                == "ark-kb-query-case-result/v1"
                and item["caseId"] == item["queryId"]
                and "latencySpansMs" in item
                and "failureClass" in item
                for item in result["results"]
            )
        )
        diagnostics = result["diagnosticArtifacts"]
        self.assertEqual(
            diagnostics["schema"],
            "ark-kb-query-diagnostics/v1",
        )
        self.assertEqual(
            diagnostics["corpusSha256"],
            result["goldSet"]["sha256"],
        )
        self.assertEqual(
            diagnostics["caseResults"]["count"],
            result["total"],
        )
        self.assertEqual(
            diagnostics["failureMatrix"]["caseCount"],
            result["total"],
        )
        self.assertEqual(len(route_matches), 28)
        self.assertEqual(len(route_mismatches), 102)
        self.assertEqual(
            sum(item["protocolCompliance"] for item in route_matches),
            1,
        )
        self.assertTrue(
            all(not item["protocolCompliance"] for item in route_mismatches)
        )
        self.assertEqual(
            sum(item["wrongAnswer"] for item in route_matches),
            27,
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
        self.assertEqual(result["goldSet"]["humanGoldCases"], 0)
        self.assertEqual(
            result["goldSet"]["compatibilityReviewedCases"],
            5,
        )
        self.assertEqual(
            result["goldSet"]["productionReviewContract"],
            "SIGNED_V2_RECEIPTS_REQUIRED",
        )
        self.assertFalse(result["goldSet"]["corpusReadyForCutover"])
        self.assertTrue(result["identityOnlyNotCountedAsSemantic"])
        self.assertLessEqual(result["contextTokens"]["maximum"], 2_000)
        self.assertLess(result["latencyMs"]["p95"], 250)
        self.assertLess(result["latencyMs"]["p99"], 250)
        self.assertLess(result["latencyMs"]["oneHopP95"], 250)
        self.assertLess(result["latencyMs"]["twoHopP95"], 800)
        self.assertEqual(
            set(result["latencyMs"]["degreePaths"]),
            {
                "TOP_OUT_DEGREE",
                "TOP_IN_DEGREE",
                "TOP_CROSS_DOMAIN",
                "RANDOM_MEDIAN_DEGREE",
            },
        )
        for cohort in result["latencyMs"]["degreePaths"].values():
            self.assertGreater(cohort["samples"], 0)
            self.assertTrue(
                {"p50", "p95", "p99"}
                <= set(cohort["oneHop"])
            )
            self.assertTrue(
                {"p50", "p95", "p99"}
                <= set(cohort["twoHop"])
            )
        self.assertFalse(result["storagePathCoverage"]["complete"])


if __name__ == "__main__":
    unittest.main()
