from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.fact_store import (  # noqa: E402
    FactValue,
    store_fact,
)
from blueprint_translator.kb_vnext.kb_api import (  # noqa: E402
    KnowledgeApiError,
    VNextKnowledgeService,
)
from blueprint_translator.kb_vnext import kb_api as kb_api_module  # noqa: E402
from blueprint_translator.kb_vnext.kb_context import (  # noqa: E402
    build_bounded_context_pack,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.profiling import (  # noqa: E402
    SegmentTiming,
)
from blueprint_translator.kb_vnext.projections import (  # noqa: E402
    DOMAIN_PROJECTIONS,
    build_domain_projections,
)
from blueprint_translator.kb_vnext.quality_contract import (  # noqa: E402
    QUALITY_GATE_CONTRACT,
)
from blueprint_translator.kb_vnext.snapshot import (  # noqa: E402
    semantic_inputs_sha256,
    snapshot_build_id,
)
from blueprint_translator.kb_vnext.source_manifest import (  # noqa: E402
    SourceManifest,
    SourceRevision,
    source_id,
    source_manifest_binding,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    CACHE_SCHEMA_SQL,
    CACHE_SCHEMA_VERSION,
    CATALOG_SCHEMA_VERSION,
    CORE_SCHEMA_VERSION,
    FULL_CATALOG_SCHEMA_SQL,
    FULL_CORE_SCHEMA_SQL,
    build_search_database,
    database_metrics,
)
import blueprint_tool_server as tool_server  # noqa: E402


def _write_snapshot_manifest(
    root: Path,
    manifest: dict[str, object],
) -> None:
    manifest_text = json.dumps(manifest)
    (root / "manifests" / "current.json").write_text(
        manifest_text,
        encoding="utf-8",
    )
    (root / "manifests" / f"{manifest['buildId']}.json").write_text(
        manifest_text,
        encoding="utf-8",
    )


def _refresh_snapshot_database_metrics(root: Path) -> None:
    manifest = json.loads(
        (root / "manifests" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    databases = manifest["databases"]
    for name in databases:
        path = root / name
        if not path.is_file():
            continue
        databases[name]["bytes"] = path.stat().st_size
        databases[name]["sha256"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    _write_snapshot_manifest(root, manifest)


def _publish_immutable_fixture(
    root: Path,
) -> tuple[VNextKnowledgeService, Path, dict[str, object]]:
    _snapshot(root)
    manifest = json.loads(
        (root / "manifests" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    build_id = str(manifest["buildId"])
    immutable = root / "snapshots" / build_id
    immutable.mkdir(parents=True)
    for name in (
        "catalog.sqlite",
        "core.sqlite",
        "search.sqlite",
        "cache.sqlite",
    ):
        os.replace(root / name, immutable / name)
    os.replace(
        root / "domain_exports",
        immutable / "domain_exports",
    )
    benchmark = {"schema": "ark-kb-query-benchmark/v2", "queries": []}
    gates = [
        {
            "id": gate_id,
            "category": category,
            "critical": critical,
            "passed": False,
            "target": True,
            "actual": False,
            "detail": "API fixture intentionally remains shadow.",
        }
        for gate_id, category, critical in sorted(QUALITY_GATE_CONTRACT)
    ]
    failed_count = len(gates)
    report = {
        "schema": "ark-kb-quality-gates/v1",
        "buildId": build_id,
        "summary": {
            "total": len(gates),
            "passed": 0,
            "failed": failed_count,
            "cutoverEligible": False,
            "recommendation": "keep_legacy_shadow",
        },
        "gates": gates,
        "benchmark": benchmark,
    }
    reports = immutable / "reports"
    reports.mkdir()
    report_path = reports / "quality_gates.json"
    benchmark_path = reports / "query_benchmark.json"
    report_path.write_text(
        json.dumps(report),
        encoding="utf-8",
    )
    benchmark_path.write_text(
        json.dumps(benchmark),
        encoding="utf-8",
    )
    manifest["qualityGates"] = {
        "schema": report["schema"],
        "reportUri": "reports/quality_gates.json",
        "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "benchmarkUri": "reports/query_benchmark.json",
        "benchmarkSha256": hashlib.sha256(
            benchmark_path.read_bytes()
        ).hexdigest(),
        "passed": 0,
        "failed": failed_count,
        "cutoverEligible": False,
        "sealedInSnapshotManifest": True,
    }
    manifest["cutover"] = {
        "mode": "shadow",
        "defaultQuerySource": "legacy",
    }
    manifest["runtimeHealth"] = {
        "schema": "ark-kb-runtime-health/v1",
        "buildId": build_id,
        "sourceSha256": str(manifest["source"]["sha256"]),
        "activeStaleSources": 0,
        "sealedInSnapshotManifest": True,
    }
    source_inputs = manifest["source"]["inputs"]
    generated_at = str(manifest["generatedAt"])
    manifest["incrementalUpdate"] = source_manifest_binding(
        SourceManifest(
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
                for key, fingerprint in source_inputs.items()
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
    )
    (immutable / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (root / "current.json").write_text(
        json.dumps(
            {
                "buildId": build_id,
                "snapshotRelativePath": f"snapshots/{build_id}",
            }
        ),
        encoding="utf-8",
    )
    return VNextKnowledgeService(root), immutable, manifest


def _snapshot(root: Path) -> VNextKnowledgeService:
    ontology = load_ontology(PROJECT_ROOT / "ontology")
    generated_at = "2026-07-27T00:00:00+00:00"
    semantic_inputs = {
        "discovery": "d" * 64,
        "captures": "c" * 64,
        "classHierarchyContract": "b" * 64,
        "semanticProducerContract": "e" * 64,
        "legacy": "1" * 64,
        "ontology": "2" * 64,
        "benchmarkGold": "3" * 64,
        "qualityGold": "4" * 64,
        "mapEvidence": "5" * 64,
        "nativeEvidence": "6" * 64,
    }
    discovery_fingerprint = semantic_inputs["discovery"]
    semantic_fingerprint = semantic_inputs_sha256(semantic_inputs)
    build_id = snapshot_build_id(generated_at, semantic_fingerprint)
    root.mkdir(parents=True)
    (root / "manifests").mkdir()
    core = sqlite3.connect(root / "core.sqlite")
    core.execute("PRAGMA foreign_keys=ON")
    core.executescript(FULL_CORE_SCHEMA_SQL)
    core.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("schema_version", CORE_SCHEMA_VERSION),
            ("ontology_version", ontology.version),
            ("source_fingerprint", discovery_fingerprint),
            ("snapshot_build_id", build_id),
            (
                "snapshot_source_fingerprint",
                semantic_fingerprint,
            ),
            ("generated_at", generated_at),
            ("runtime_health_schema", "ark-kb-runtime-health/v1"),
            ("runtime_health_active_stale_sources", "0"),
            ("runtime_health_build_id", build_id),
            (
                "runtime_health_source_sha256",
                semantic_fingerprint,
            ),
        ],
    )
    core.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'capture', 'capture://fixture', 'sha', 'test', 'v1',
            '2026-07-27T00:00:00Z', 'FRESH'
        )
        """
    )
    core.execute(
        """
        INSERT INTO packages(
            package_id, package_path, mount_point, current_revision_id
        ) VALUES (
            1, '/Game/Test', '/Game', 1
        )
        """
    )
    core.executemany(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, package_id,
            display_name, internal_name, status, confidence
        ) VALUES (
            ?, ?, 'BLUEPRINT_ASSET', 1, ?, ?,
            'CONFIRMED', 'HIGH'
        )
        """,
        [
            (1, "/Game/Test/ItemA.ItemA", "Item A", "ItemA"),
            (2, "/Game/Test/ItemB.ItemB", "Item B", "ItemB"),
            (3, "/Game/Test/Other.Other", "Other", "Other"),
        ],
    )
    core.executemany(
        """
        INSERT INTO aliases(
            alias, entity_id, alias_kind, language, confidence
        ) VALUES (?, ?, 'DISPLAY_NAME', 'en', 'HIGH')
        """,
        [
            ("PrimaryItem", 1),
            ("SecondaryItem", 2),
        ],
    )
    fact_id = store_fact(
        core,
        ontology=ontology,
        subject_entity_id=1,
        fact_type="ITEM_PROPERTY",
        fact_name="Weight",
        scope_kind="DERIVED_STATIC",
        declared_on_entity_id=1,
        value=FactValue("NUMBER", value_number=2.5),
        status="CONFIRMED",
        confidence="HIGH",
        source_revision_id=1,
        evidence_uri="bp://fixture/item-a/weight",
        evidence_role="DIRECT_FIELD",
    )
    core.execute(
        """
        INSERT INTO knowledge_roles VALUES (
            1, 'entity_definition', 'HIGH', 'CONFIRMED',
            '["class ancestry"]', 'v1', 1
        )
        """
    )
    core.execute(
        """
        INSERT INTO domain_memberships VALUES (
            1, 'item_use', 'CLASS_ANCESTRY', 'HIGH', 'CONFIRMED',
            'ontology://fixture/item-use', 'v1', 1
        )
        """
    )
    core.execute(
        """
        INSERT INTO classes(
            class_id, class_path, class_name, module_or_package,
            class_kind, is_native, source_revision_id,
            status, confidence
        ) VALUES (
            1, '/Game/Test/Baseline.Baseline_C', 'Baseline_C', '/Game/Test',
            'BLUEPRINT_GENERATED_CLASS', 0, 1,
            'IDENTIFIED', 'HIGH'
        )
        """
    )
    core.execute(
        """
        INSERT INTO asset_class_assignments(
            entity_id, class_id, assignment_kind, evidence_uri,
            status, confidence, source_revision_id
        ) VALUES (
            1, 1, 'BASELINE_CLASS', 'bp://fixture/item-a/baseline-class',
            'EXTRACTED', 'HIGH', 1
        )
        """
    )
    core.execute(
        """
        INSERT INTO effective_facts VALUES (
            1, 'EFFECTIVE_DEFAULT', 'Weight', ?, NULL,
            '{"classes":[],"overrideDepth":0}', 'RESOLVED', 'hash'
        )
        """,
        (fact_id,),
    )
    core.execute(
        """
        INSERT INTO coverage VALUES (
            1, 'asset_identity', 'CONFIRMED',
            1, 0, 0, 0, 0, 0, ''
        )
        """
    )
    core.commit()
    core.close()

    catalog = sqlite3.connect(root / "catalog.sqlite")
    catalog.executescript(FULL_CATALOG_SCHEMA_SQL)
    catalog.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("schema_version", CATALOG_SCHEMA_VERSION),
            ("source_fingerprint", discovery_fingerprint),
            ("snapshot_build_id", build_id),
            (
                "snapshot_source_fingerprint",
                semantic_fingerprint,
            ),
            ("generated_at", generated_at),
        ],
    )
    catalog.commit()
    catalog.close()

    build_search_database(
        core_path=root / "core.sqlite",
        output_path=root / "search.sqlite",
        source_fingerprint=semantic_fingerprint,
        generated_at=generated_at,
        snapshot_build_id=build_id,
        snapshot_source_fingerprint=semantic_fingerprint,
    )

    cache = sqlite3.connect(root / "cache.sqlite")
    cache.executescript(CACHE_SCHEMA_SQL)
    cache.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("schema_version", CACHE_SCHEMA_VERSION),
            ("source_fingerprint", semantic_fingerprint),
            ("snapshot_build_id", build_id),
            (
                "snapshot_source_fingerprint",
                semantic_fingerprint,
            ),
            ("generated_at", generated_at),
            ("disposable", "true"),
        ],
    )
    cache.commit()
    cache.close()
    exports = root / "domain_exports"
    projection_counts = build_domain_projections(
        core_path=root / "core.sqlite",
        output_dir=exports,
        generated_at=generated_at,
        ontology_version=ontology.version,
        review_path=PROJECT_ROOT / "ontology" / "projection_review.v1.json",
        snapshot_build_id=build_id,
        snapshot_source_fingerprint=semantic_fingerprint,
    )
    projection_metrics: dict[str, dict[str, object]] = {}
    for value in projection_counts.values():
        projection_metrics[f"domain_exports/{value['path']}"] = {
            key: value[key]
            for key in (
                "bytes",
                "sha256",
                "integrity",
                "foreignKeyViolations",
                "schemaVersion",
                "projectionVersion",
                "ontologyVersion",
                "contentDigest",
                "reviewConfigSha256",
                "sourceRevisionSetHash",
                "validationStatus",
                "tableCounts",
            )
        }
    manifest = {
        "schema": "ark-kb-vnext-snapshot/v1",
        "buildId": build_id,
        "generatedAt": generated_at,
        "source": {
            "kind": "semantic_input_set",
            "uri": "kb-inputs://ark/vnext",
            "sha256": semantic_fingerprint,
            "inputs": semantic_inputs,
        },
        "ontologyVersion": ontology.version,
        "counts": {},
        "databases": {
            **{
                name: database_metrics(root / name)
                for name in (
                    "catalog.sqlite",
                    "core.sqlite",
                    "search.sqlite",
                    "cache.sqlite",
                )
            },
            **projection_metrics,
        },
        "cutover": {
            "mode": "shadow",
            "defaultQuerySource": "legacy",
        },
    }
    _write_snapshot_manifest(root, manifest)
    return VNextKnowledgeService(root)


def _remove_effective_candidate_capability(root: Path) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        core.execute("DROP TABLE effective_fact_candidates")
        core.execute(
            """
            UPDATE metadata
            SET value='ark-kb-core/v1'
            WHERE key='schema_version'
            """
        )
        core.commit()
    finally:
        core.close()
    _refresh_snapshot_database_metrics(root)


def _remove_semantic_derivation_capability(root: Path) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        core.execute("DROP TABLE semantic_adapter_decisions")
        core.execute("DROP TABLE semantic_adapter_runs")
        core.execute(
            """
            UPDATE metadata
            SET value='ark-kb-core/v2'
            WHERE key='schema_version'
            """
        )
        core.commit()
    finally:
        core.close()
    _refresh_snapshot_database_metrics(root)


def _remove_typed_map_capability(root: Path) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        core.execute("DROP VIEW confirmed_map_usage_edges")
        core.execute("DROP TABLE map_usage_edge_evidence")
        core.execute("DROP TABLE map_usage_sources")
        core.execute(
            """
            UPDATE metadata
            SET value='ark-kb-core/v3'
            WHERE key='schema_version'
            """
        )
        core.commit()
    finally:
        core.close()
    _refresh_snapshot_database_metrics(root)


def _remove_query_provenance_capability(root: Path) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        core.execute("DROP TABLE knowledge_roles")
        core.execute(
            """
            CREATE TABLE knowledge_roles(
                entity_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                confidence TEXT NOT NULL,
                status TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                classifier_version TEXT NOT NULL,
                PRIMARY KEY(entity_id, role)
            )
            """
        )
        core.commit()
    finally:
        core.close()
    _refresh_snapshot_database_metrics(root)


def _remove_table_column(
    root: Path,
    *,
    table_name: str,
    column_name: str,
) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        columns = [
            str(row[1])
            for row in core.execute(
                f'PRAGMA table_info("{table_name}")'
            )
            if str(row[1]) != column_name
        ]
        projection = ", ".join(f'"{name}"' for name in columns)
        temporary = f"__{table_name}_without_{column_name}"
        core.execute(
            f'CREATE TABLE "{temporary}" AS '
            f'SELECT {projection} FROM "{table_name}"'
        )
        core.execute(f'DROP TABLE "{table_name}"')
        core.execute(
            f'ALTER TABLE "{temporary}" RENAME TO "{table_name}"'
        )
        core.commit()
    finally:
        core.close()
    _refresh_snapshot_database_metrics(root)


def _seed_stale_active_provenance(root: Path, scenario: str) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        core.execute(
            """
            INSERT INTO source_revisions VALUES(
                2, 'historical', 'historical://fixture', 'stale-sha',
                'test', 'v0', '2026-07-26T00:00:00Z', 'STALE'
            )
            """
        )
        if scenario in {"native_function", "native_link"}:
            core.execute(
                """
                INSERT INTO native_functions VALUES(
                    1, 'native://fixture/function', 'UItem::Function',
                    'ShooterGame', '0x10', 'void Function()',
                    'binary-sha', 'pdb-sha', 'guid/1',
                    '["recipe/v1"]', '["native-set://fixture"]',
                    1, 1, 'AVAILABLE_VIA_EVIDENCE_STORE',
                    'CONFIRMED', 'HIGH', ?
                )
                """,
                (2 if scenario == "native_function" else 1,),
            )
            core.execute(
                """
                INSERT INTO native_blueprint_links VALUES(
                    'native-link', 1, 'bp://fixture/item-a/function',
                    'Function', 1, 'native-slice://fixture/function',
                    'verified_callsite', 'CONFIRMED', 'HIGH', ?
                )
                """,
                (2 if scenario == "native_link" else 1,),
            )
        elif scenario == "class":
            core.execute(
                """
                INSERT INTO classes VALUES(
                    11, '/Game/Test/ItemA.ItemA_C', 'ItemA_C',
                    '/Game/Test', 'BLUEPRINT_GENERATED_CLASS', 0,
                    2, 'CONFIRMED', 'HIGH'
                )
                """
            )
            core.execute(
                """
                INSERT INTO asset_class_assignments VALUES(
                    1, 11, 'GENERATED_CLASS', 'bp://fixture/item-a/class',
                    'CONFIRMED', 'HIGH', 1
                )
                """
            )
        elif scenario == "class_assignment":
            core.execute(
                """
                INSERT INTO classes VALUES(
                    11, '/Game/Test/ItemA.ItemA_C', 'ItemA_C',
                    '/Game/Test', 'BLUEPRINT_GENERATED_CLASS', 0,
                    1, 'CONFIRMED', 'HIGH'
                )
                """
            )
            core.execute(
                """
                INSERT INTO asset_class_assignments VALUES(
                    1, 11, 'GENERATED_CLASS', 'bp://fixture/item-a/class',
                    'CONFIRMED', 'HIGH', 2
                )
                """
            )
        elif scenario == "class_edge":
            core.executemany(
                """
                INSERT INTO classes VALUES(
                    ?, ?, ?, '/Game/Test',
                    'BLUEPRINT_GENERATED_CLASS', 0,
                    1, 'CONFIRMED', 'HIGH'
                )
                """,
                [
                    (11, "/Game/Test/ItemA.ItemA_C", "ItemA_C"),
                    (12, "/Game/Test/Base.Base_C", "Base_C"),
                ],
            )
            core.execute(
                """
                INSERT INTO asset_class_assignments VALUES(
                    1, 11, 'GENERATED_CLASS', 'bp://fixture/item-a/class',
                    'CONFIRMED', 'HIGH', 1
                )
                """
            )
            core.execute(
                """
                INSERT INTO class_edges VALUES(
                    11, 12, 'blueprint_parent',
                    'bp://fixture/item-a/parent', 2,
                    'CONFIRMED', 'HIGH'
                )
                """
            )
        else:
            raise AssertionError(scenario)
        core.commit()
    finally:
        core.close()
    _refresh_snapshot_database_metrics(root)


def _seed_invalid_active_evidence(root: Path, scenario: str) -> None:
    core = sqlite3.connect(root / "core.sqlite")
    try:
        if scenario == "fact":
            core.execute(
                "UPDATE fact_evidence SET evidence_uri='UNKNOWN'"
            )
        elif scenario == "relationship":
            core.execute(
                """
                INSERT INTO edges(
                    source_entity_id, target_entity_id, edge_type,
                    edge_strength, status, confidence,
                    source_revision_id, evidence_uri
                ) VALUES(
                    1, 2, 'REFERENCES', 'HARD', 'CONFIRMED', 'HIGH',
                    1, 'UNKNOWN'
                )
                """
            )
        elif scenario == "class_assignment":
            core.execute(
                """
                UPDATE asset_class_assignments
                SET evidence_uri='UNKNOWN'
                WHERE entity_id=1 AND assignment_kind='BASELINE_CLASS'
                """
            )
        elif scenario == "class_edge":
            core.executemany(
                """
                INSERT INTO classes VALUES(
                    ?, ?, ?, '/Game/Test',
                    'BLUEPRINT_GENERATED_CLASS', 0,
                    1, 'CONFIRMED', 'HIGH'
                )
                """,
                [
                    (11, "/Game/Test/Child.Child_C", "Child_C"),
                    (12, "/Game/Test/Parent.Parent_C", "Parent_C"),
                ],
            )
            core.execute(
                """
                INSERT INTO class_edges VALUES(
                    11, 12, 'blueprint_parent', 'UNKNOWN', 1,
                    'CONFIRMED', 'HIGH'
                )
                """
            )
        else:
            raise AssertionError(scenario)
        core.commit()
    finally:
        core.close()
    _refresh_snapshot_database_metrics(root)


class KnowledgeApiTests(unittest.TestCase):
    def test_service_binds_all_reads_to_one_pointer_resolved_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service, immutable, manifest = _publish_immutable_fixture(
                root
            )
            build_id = str(manifest["buildId"])
            result = service.search_entities(query="PrimaryItem")
            health = service.health()

        self.assertEqual(service.root, immutable.resolve())
        self.assertEqual(
            result["items"][0]["canonicalUri"],
            "/Game/Test/ItemA.ItemA",
        )
        self.assertTrue(health["available"])
        self.assertEqual(health["buildId"], build_id)

    def test_immutable_service_rejects_tampered_sealed_quality_report(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service, immutable, _manifest = _publish_immutable_fixture(
                root
            )
            report_path = immutable / "reports" / "quality_gates.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["summary"]["cutoverEligible"] = True
            report_path.write_text(
                json.dumps(report),
                encoding="utf-8",
            )

            health = service.health()
            with self.assertRaises(KnowledgeApiError) as raised:
                service.search_entities(query="PrimaryItem")

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "INVALID")
        self.assertEqual(
            raised.exception.code,
            "KB_VNEXT_SNAPSHOT_INVALID",
        )

    def test_search_uses_ranked_search_database_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)

            exact_uri = service.search_entities(
                query="/Game/Test/ItemA.ItemA",
            )
            exact_alias = service.search_entities(query="PrimaryItem")
            phrase = service.search_entities(query="Item A")
            prefix = service.search_entities(query="Ite")
            fuzzy = service.search_entities(query="ItmeA")
            fuzzy_alias = service.search_entities(query="PrimrayItem")

            search = sqlite3.connect(root / "search.sqlite")
            try:
                query_plan = " ".join(
                    str(row[3])
                    for row in search.execute(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT rowid FROM entities_fts
                        WHERE entities_fts MATCH ?
                        """,
                        ('"Item"*',),
                    )
                )
            finally:
                search.close()

        self.assertEqual(
            exact_uri["items"][0]["matchType"],
            "EXACT_CANONICAL_URI",
        )
        self.assertEqual(
            exact_alias["items"][0]["matchType"],
            "EXACT_ALIAS",
        )
        self.assertEqual(
            phrase["items"][0]["matchType"],
            "FTS_PHRASE",
        )
        self.assertEqual(
            prefix["items"][0]["matchType"],
            "FTS_PREFIX",
        )
        self.assertEqual(
            fuzzy["items"][0]["matchType"],
            "FUZZY_CANDIDATE",
        )
        self.assertEqual(fuzzy["items"][0]["entityId"], 1)
        self.assertEqual(
            fuzzy_alias["items"][0]["matchType"],
            "FUZZY_CANDIDATE",
        )
        self.assertEqual(fuzzy_alias["items"][0]["entityId"], 1)
        for result in (
            exact_uri,
            exact_alias,
            phrase,
            prefix,
            fuzzy,
            fuzzy_alias,
        ):
            self.assertGreater(result["items"][0]["score"], 0.0)
            self.assertLessEqual(result["items"][0]["score"], 1.0)
        self.assertIn("VIRTUAL TABLE INDEX", query_plan)

    def test_query_reads_valid_cache_and_reports_hit_miss_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            request = {
                "entity": "ItemA",
                "factTypes": ["ITEM_PROPERTY"],
                "budgetTokens": 500,
                "evidenceLimit": 10,
            }

            cold = service.query(request)
            warm = service.query(request)
            health = service.health()

        self.assertEqual(cold["cache"]["status"], "MISS")
        self.assertEqual(warm["cache"]["status"], "HIT")
        self.assertEqual(warm["status"], cold["status"])
        self.assertEqual(warm["route"], cold["route"])
        self.assertEqual(warm["facts"], cold["facts"])
        self.assertEqual(
            health["cacheMetrics"],
            {"hits": 1, "misses": 1},
        )

    def test_query_rejects_expired_cache_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            request = {
                "entity": "ItemA",
                "factTypes": ["ITEM_PROPERTY"],
                "budgetTokens": 500,
                "evidenceLimit": 10,
            }

            service.query(request)
            cache = sqlite3.connect(root / "cache.sqlite")
            try:
                cache.execute(
                    """
                    UPDATE query_snapshots
                    SET expires_at='2020-01-01T00:00:00+00:00'
                    """
                )
                cache.commit()
            finally:
                cache.close()

            refreshed = service.query(request)

        self.assertEqual(refreshed["cache"]["status"], "MISS")
        self.assertEqual(refreshed["cache"]["reason"], "EXPIRED")

    def test_query_rejects_changed_source_revision_set(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            request = {
                "entity": "ItemA",
                "factTypes": ["ITEM_PROPERTY"],
                "budgetTokens": 500,
                "evidenceLimit": 10,
            }

            service.query(request)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    UPDATE source_revisions
                    SET producer_version='test-v2'
                    WHERE revision_id=1
                    """
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            refreshed = service.query(request)

        self.assertEqual(refreshed["cache"]["status"], "MISS")
        self.assertEqual(
            refreshed["cache"]["reason"],
            "SOURCE_REVISION_SET_CHANGED",
        )

    def test_query_rejects_changed_invalidation_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            request = {
                "entity": "ItemA",
                "factTypes": ["ITEM_PROPERTY"],
                "budgetTokens": 500,
                "evidenceLimit": 10,
            }

            service.query(request)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    INSERT INTO invalidation_events(
                        event_id, event_kind, upstream_revision_id,
                        payload_json, created_at, status
                    ) VALUES (
                        'event://fixture/1', 'SOURCE_REVISION_CHANGED', 1,
                        '{}', '2026-07-28T00:00:00+00:00', 'PENDING'
                    )
                    """
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            refreshed = service.query(request)

        self.assertEqual(refreshed["cache"]["status"], "MISS")
        self.assertEqual(
            refreshed["cache"]["reason"],
            "INVALIDATION_TOKEN_CHANGED",
        )

    def test_query_rejects_cache_from_another_build(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            request = {
                "entity": "ItemA",
                "factTypes": ["ITEM_PROPERTY"],
                "budgetTokens": 500,
                "evidenceLimit": 10,
            }

            service.query(request)
            cache = sqlite3.connect(root / "cache.sqlite")
            try:
                cache.execute(
                    """
                    UPDATE metadata
                    SET value='snapshot-build://another'
                    WHERE key='snapshot_build_id'
                    """
                )
                cache.commit()
            finally:
                cache.close()

            refreshed = service.query(request)

        self.assertEqual(refreshed["cache"]["status"], "MISS")
        self.assertEqual(refreshed["cache"]["reason"], "BUILD_MISMATCH")

    def test_http_routes_inherit_session_origin_and_host_security(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _snapshot(Path(temp_dir) / "vnext")
            server = tool_server.create_control_center_server(
                "127.0.0.1", 0
            )
            port = int(server.server_address[1])
            worker = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            worker.start()
            try:
                with patch.object(
                    tool_server, "KB_VNEXT_SERVICE", service
                ):
                    session = HTTPConnection("127.0.0.1", port, timeout=3)
                    session.request(
                        "GET",
                        "/api/session",
                        headers={"Host": f"127.0.0.1:{port}"},
                    )
                    response = session.getresponse()
                    token = json.loads(
                        response.read().decode("utf-8")
                    )["sessionToken"]
                    session.close()

                    body = json.dumps(
                        {
                            "entity": "ItemA",
                            "factTypes": ["ITEM_PROPERTY"],
                            "budgetTokens": 500,
                        }
                    )
                    request = HTTPConnection(
                        "127.0.0.1", port, timeout=3
                    )
                    request.request(
                        "POST",
                        "/api/kb/query",
                        body=body,
                        headers={
                            "Host": f"127.0.0.1:{port}",
                            "Origin": f"http://127.0.0.1:{port}",
                            "Content-Type": "application/json",
                            "X-Blueprint-Session": str(token),
                        },
                    )
                    response = request.getresponse()
                    payload = json.loads(
                        response.read().decode("utf-8")
                    )
                    request.close()
                    self.assertEqual(response.status, 200)
                    self.assertTrue(payload["ok"])
                    self.assertEqual(
                        payload["route"], "DB_ONLY_COMPLETE"
                    )

                    rejected = HTTPConnection(
                        "127.0.0.1", port, timeout=3
                    )
                    rejected.request(
                        "POST",
                        "/api/kb/query",
                        body=body,
                        headers={
                            "Host": f"127.0.0.1:{port}",
                            "Origin": "https://attacker.example",
                            "Content-Type": "application/json",
                            "X-Blueprint-Session": str(token),
                        },
                    )
                    rejected_response = rejected.getresponse()
                    rejected_payload = json.loads(
                        rejected_response.read().decode("utf-8")
                    )
                    rejected.close()
                    self.assertEqual(rejected_response.status, 403)
                    self.assertEqual(
                        rejected_payload["code"], "ORIGIN_FORBIDDEN"
                    )
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)

    def test_health_rejects_tampered_manifest_schema_or_build_identity(self):
        for field, value in (
            ("schema", "ark-kb-vnext-snapshot/tampered"),
            ("buildId", "tampered-build"),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    current_path = root / "manifests" / "current.json"
                    manifest = json.loads(
                        current_path.read_text(encoding="utf-8")
                    )
                    manifest[field] = value
                    current_path.write_text(
                        json.dumps(manifest),
                        encoding="utf-8",
                    )

                    health = service.health()

                self.assertFalse(health["available"])
                self.assertNotEqual(health["status"], "READY")
                self.assertTrue(health["gap"])

    def test_health_degrades_for_cache_schema_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            cache = sqlite3.connect(root / "cache.sqlite")
            try:
                cache.execute(
                    """
                    UPDATE metadata
                    SET value='ark-kb-cache/tampered'
                    WHERE key='schema_version'
                    """
                )
                cache.commit()
            finally:
                cache.close()

            health = service.health()
            result = service.query(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "answerMode": "FACT",
                    "factTypes": ["ITEM_PROPERTY"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )

        self.assertTrue(health["available"])
        self.assertEqual(health["status"], "DEGRADED_CACHE")
        self.assertEqual(
            {gap["code"] for gap in health["gap"]},
            {"KB_VNEXT_CACHE_DEGRADED"},
        )
        self.assertEqual(result["status"], "COMPLETE")

    def test_domain_exports_are_strictly_bound_to_the_manifest(self):
        scenarios = (
            "missing_declaration",
            "extra_declaration",
            "missing_file",
            "tampered_bytes",
            "schema_metadata",
            "projection_name_metadata",
            "projection_version_metadata",
            "content_digest_metadata",
            "ontology_version_metadata",
            "built_at_metadata",
            "review_config_sha256_metadata",
            "truth_source_metadata",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    projection_name = next(iter(DOMAIN_PROJECTIONS))
                    key = (
                        f"domain_exports/{projection_name}.sqlite"
                    )
                    path = root / key
                    manifest = json.loads(
                        (root / "manifests" / "current.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    if scenario == "missing_declaration":
                        manifest["databases"].pop(key)
                        _write_snapshot_manifest(root, manifest)
                    elif scenario == "extra_declaration":
                        manifest["databases"][
                            "domain_exports/unexpected.sqlite"
                        ] = dict(manifest["databases"][key])
                        _write_snapshot_manifest(root, manifest)
                    elif scenario == "missing_file":
                        path.unlink()
                    elif scenario == "tampered_bytes":
                        with path.open("ab") as handle:
                            handle.write(b"tampered")
                    else:
                        metadata_key, value = {
                            "schema_metadata": (
                                "schema_version",
                                "ark-kb-domain-projection/tampered",
                            ),
                            "projection_name_metadata": (
                                "projection_name",
                                "unexpected",
                            ),
                            "projection_version_metadata": (
                                "projection_version",
                                "v3",
                            ),
                            "content_digest_metadata": (
                                "content_digest",
                                "0" * 64,
                            ),
                            "ontology_version_metadata": (
                                "ontology_version",
                                "ark-fact-types/v999",
                            ),
                            "built_at_metadata": (
                                "built_at",
                                "2026-07-28T00:00:00+00:00",
                            ),
                            "review_config_sha256_metadata": (
                                "review_config_sha256",
                                "0" * 64,
                            ),
                            "truth_source_metadata": (
                                "truth_source",
                                "tampered.sqlite",
                            ),
                        }[scenario]
                        projection = sqlite3.connect(path)
                        try:
                            projection.execute(
                                "UPDATE metadata SET value=? WHERE key=?",
                                (value, metadata_key),
                            )
                            projection.commit()
                        finally:
                            projection.close()
                        _refresh_snapshot_database_metrics(root)

                    health = service.health()
                    with self.assertRaises(
                        KnowledgeApiError
                    ) as raised:
                        service.query(
                            {
                                "entity": (
                                    "/Game/Test/ItemA.ItemA"
                                ),
                                "answerMode": "FACT",
                                "factTypes": ["ITEM_PROPERTY"],
                                "factNames": ["Weight"],
                                "budgetTokens": 500,
                            }
                        )

                self.assertFalse(health["available"])
                self.assertEqual(health["status"], "INVALID")
                self.assertEqual(raised.exception.status.value, 503)
                self.assertEqual(
                    raised.exception.code,
                    "KB_VNEXT_SNAPSHOT_INVALID",
                )

    def test_domain_export_declared_provenance_requires_valid_values(self):
        scenarios = (
            ("ontologyVersion", ""),
            ("ontologyVersion", "forged"),
            ("reviewConfigSha256", ""),
            ("reviewConfigSha256", "not-a-digest"),
        )
        for field, value in scenarios:
            with self.subTest(field=field, value=value):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    projection_name = next(iter(DOMAIN_PROJECTIONS))
                    key = (
                        f"domain_exports/{projection_name}.sqlite"
                    )
                    manifest = json.loads(
                        (root / "manifests" / "current.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    manifest["databases"][key][field] = value
                    _write_snapshot_manifest(root, manifest)

                    health = service.health()
                    with self.assertRaises(
                        KnowledgeApiError
                    ) as raised:
                        service.query(
                            {
                                "entity": (
                                    "/Game/Test/ItemA.ItemA"
                                ),
                                "answerMode": "FACT",
                                "factTypes": ["ITEM_PROPERTY"],
                                "factNames": ["Weight"],
                                "budgetTokens": 500,
                            }
                        )

                self.assertFalse(health["available"])
                self.assertEqual(health["status"], "INVALID")
                self.assertEqual(raised.exception.status.value, 503)

    def test_cache_artifact_failures_are_explicitly_degraded(self):
        scenarios = (
            "missing_declaration",
            "malformed_manifest_digest",
            "wrong_build_metadata",
            "missing_file",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    manifest = json.loads(
                        (root / "manifests" / "current.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    if scenario == "missing_declaration":
                        manifest["databases"].pop("cache.sqlite")
                        _write_snapshot_manifest(root, manifest)
                    elif scenario == "malformed_manifest_digest":
                        manifest["databases"]["cache.sqlite"][
                            "sha256"
                        ] = "not-a-digest"
                        _write_snapshot_manifest(root, manifest)
                    elif scenario == "wrong_build_metadata":
                        cache = sqlite3.connect(root / "cache.sqlite")
                        try:
                            cache.execute(
                                """
                                UPDATE metadata
                                SET value='wrong-build'
                                WHERE key='snapshot_build_id'
                                """
                            )
                            cache.commit()
                        finally:
                            cache.close()
                    else:
                        (root / "cache.sqlite").unlink()

                    health = service.health()
                    result = service.query(
                        {
                            "entity": "/Game/Test/ItemA.ItemA",
                            "answerMode": "FACT",
                            "factTypes": ["ITEM_PROPERTY"],
                            "factNames": ["Weight"],
                            "budgetTokens": 500,
                        }
                    )

                self.assertTrue(health["available"])
                self.assertEqual(
                    health["status"],
                    "DEGRADED_CACHE",
                )
                self.assertEqual(
                    {gap["code"] for gap in health["gap"]},
                    {"KB_VNEXT_CACHE_DEGRADED"},
                )
                self.assertEqual(result["status"], "COMPLETE")

    def test_health_requires_all_declared_immutable_databases(self):
        for scenario in (
            "missing_databases",
            "missing_catalog_declaration",
            "missing_search_file",
        ):
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    manifest_path = root / "manifests" / "current.json"
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    if scenario == "missing_databases":
                        manifest.pop("databases")
                        _write_snapshot_manifest(root, manifest)
                    elif scenario == "missing_catalog_declaration":
                        manifest["databases"].pop("catalog.sqlite")
                        _write_snapshot_manifest(root, manifest)
                    else:
                        (root / "search.sqlite").unlink()

                    health = service.health()

                self.assertFalse(health["available"])
                self.assertNotEqual(health["status"], "READY")
                self.assertTrue(health["gap"])

    def test_query_rejects_mixed_snapshot_build_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            catalog = sqlite3.connect(root / "catalog.sqlite")
            try:
                catalog.execute(
                    """
                    UPDATE metadata
                    SET value='different-build'
                    WHERE key='snapshot_build_id'
                    """
                )
                catalog.commit()
            finally:
                catalog.close()

            health = service.health()
            with self.assertRaises(KnowledgeApiError) as raised:
                service.query(
                    {
                        "entity": "/Game/Test/ItemA.ItemA",
                        "answerMode": "FACT",
                        "factTypes": ["ITEM_PROPERTY"],
                        "factNames": ["Weight"],
                        "budgetTokens": 500,
                    }
                )

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "INVALID")
        self.assertEqual(raised.exception.status.value, 503)
        self.assertEqual(
            raised.exception.code,
            "KB_VNEXT_SNAPSHOT_INVALID",
        )

    def test_health_and_query_reject_same_size_immutable_database_tamper(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            request = {
                "entity": "/Game/Test/ItemA.ItemA",
                "answerMode": "FACT",
                "factTypes": ["ITEM_PROPERTY"],
                "factNames": ["Weight"],
                "budgetTokens": 500,
            }
            self.assertEqual(
                service.query(request)["route"],
                "DB_SEMANTIC_COMPLETE",
            )
            core_path = root / "core.sqlite"
            original_stat = core_path.stat()
            core = sqlite3.connect(core_path)
            try:
                core.execute(
                    """
                    UPDATE facts
                    SET value_number=9.5
                    WHERE fact_type='ITEM_PROPERTY'
                      AND fact_name='Weight'
                    """
                )
                core.commit()
            finally:
                core.close()
            self.assertEqual(
                core_path.stat().st_size,
                original_stat.st_size,
            )
            os.utime(
                core_path,
                ns=(
                    original_stat.st_atime_ns,
                    original_stat.st_mtime_ns,
                ),
            )

            health = service.health()
            with self.assertRaises(KnowledgeApiError) as raised:
                service.query(request)

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "INVALID")
        self.assertEqual(
            raised.exception.code,
            "KB_VNEXT_SNAPSHOT_INVALID",
        )

    def test_health_is_lightweight_but_query_keeps_full_digest_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service, _immutable, _manifest = _publish_immutable_fixture(root)
            digest_calls: list[Path] = []

            def file_digest(path: Path) -> str:
                digest_calls.append(path)
                return hashlib.sha256(path.read_bytes()).hexdigest()

            with patch(
                "blueprint_translator.kb_vnext.kb_api._file_sha256",
                side_effect=file_digest,
            ):
                health = service.health()
                health_digest_calls = len(digest_calls)
                result = service.query(
                    {
                        "entity": "/Game/Test/ItemA.ItemA",
                        "answerMode": "FACT",
                        "factTypes": ["ITEM_PROPERTY"],
                        "factNames": ["Weight"],
                        "budgetTokens": 500,
                    }
                )

        self.assertTrue(health["available"])
        self.assertEqual(health_digest_calls, 0)
        self.assertGreater(len(digest_calls), 0)
        self.assertEqual(result["route"], "DB_SEMANTIC_COMPLETE")

    def test_health_runtime_state_does_not_run_full_quick_check(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service, _immutable, _manifest = _publish_immutable_fixture(root)
            statements: list[str] = []
            original_connect = sqlite3.connect

            def traced_connect(*args, **kwargs):
                connection = original_connect(*args, **kwargs)
                connection.set_trace_callback(statements.append)
                return connection

            with patch.object(
                kb_api_module.sqlite3,
                "connect",
                side_effect=traced_connect,
            ):
                health = service.health()

        self.assertTrue(health["available"])
        self.assertFalse(
            any(
                "quick_check" in statement.casefold()
                for statement in statements
            )
        )
        provenance_tables = (
            " from packages",
            " from knowledge_roles",
            " from domain_memberships",
            " from edges",
            " from facts",
            " from native_functions",
            " from native_blueprint_links",
            " from asset_class_assignments",
            " from class_edges",
            " from classes",
        )
        normalized_statements = [
            " ".join(statement.casefold().split())
            for statement in statements
        ]
        self.assertFalse(
            any(
                table in statement
                for statement in normalized_statements
                for table in provenance_tables
            )
        )

    def test_health_rejects_runtime_summary_forged_only_in_core(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            _service, immutable, _manifest = _publish_immutable_fixture(root)
            core = sqlite3.connect(immutable / "core.sqlite")
            try:
                core.execute(
                    """
                    UPDATE metadata
                    SET value='1'
                    WHERE key='runtime_health_active_stale_sources'
                    """
                )
                core.commit()
            finally:
                core.close()

            health = VNextKnowledgeService(root).health()

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "INVALID")
        self.assertTrue(
            any(
                gap["code"] == "KB_VNEXT_SNAPSHOT_INVALID"
                for gap in health["gap"]
            )
        )

    def test_health_and_query_reject_nonempty_immutable_wal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            (root / "core.sqlite-wal").write_bytes(b"unsealed-change")

            health = service.health()
            with self.assertRaises(KnowledgeApiError) as raised:
                service.query(
                    {
                        "entity": "/Game/Test/ItemA.ItemA",
                        "answerMode": "FACT",
                        "factTypes": ["ITEM_PROPERTY"],
                        "factNames": ["Weight"],
                        "budgetTokens": 500,
                    }
                )

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "INVALID")
        self.assertEqual(
            raised.exception.code,
            "KB_VNEXT_SNAPSHOT_INVALID",
        )

    def test_health_and_query_share_strict_manifest_source_identity(self):
        for scenario in (
            "wrong_kind",
            "wrong_uri",
            "empty_uri",
            "missing_discovery_input",
            "malformed_source_sha",
            "extra_semantic_input",
            "mismatched_aggregate",
            "mismatched_build_id",
            "invalid_generated_at",
            "empty_generated_at",
        ):
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    manifest_path = (
                        root / "manifests" / "current.json"
                    )
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    if scenario == "wrong_kind":
                        manifest["source"]["kind"] = "bad"
                    elif scenario == "wrong_uri":
                        manifest["source"]["uri"] = "not-even-a-uri"
                    elif scenario == "empty_uri":
                        manifest["source"]["uri"] = ""
                    elif scenario == "missing_discovery_input":
                        manifest["source"]["inputs"].pop("discovery")
                    elif scenario == "malformed_source_sha":
                        manifest["source"]["sha256"] = "not-a-sha"
                    elif scenario == "extra_semantic_input":
                        manifest["source"]["inputs"]["unexpected"] = (
                            "b" * 64
                        )
                    elif scenario == "mismatched_aggregate":
                        manifest["source"]["inputs"]["ontology"] = (
                            "f" * 64
                        )
                    elif scenario == "mismatched_build_id":
                        manifest["buildId"] = (
                            "forged-" + manifest["source"]["sha256"][:12]
                        )
                    else:
                        manifest["generatedAt"] = (
                            ""
                            if scenario == "empty_generated_at"
                            else "not-a-time"
                        )
                        manifest["buildId"] = (
                            (
                                "-"
                                if scenario == "empty_generated_at"
                                else "notatime-"
                            )
                            + manifest["source"]["sha256"][:12]
                        )
                        for name in (
                            "catalog.sqlite",
                            "core.sqlite",
                            "search.sqlite",
                            "cache.sqlite",
                        ):
                            database = sqlite3.connect(root / name)
                            try:
                                database.execute(
                                    """
                                    UPDATE metadata
                                    SET value=?
                                    WHERE key='generated_at'
                                    """,
                                    (manifest["generatedAt"],),
                                )
                                database.execute(
                                    """
                                    UPDATE metadata
                                    SET value=?
                                    WHERE key='snapshot_build_id'
                                    """,
                                    (manifest["buildId"],),
                                )
                                database.commit()
                            finally:
                                database.close()
                            manifest["databases"][name] = database_metrics(
                                root / name
                            )
                    _write_snapshot_manifest(root, manifest)

                    binding_error = service._snapshot_binding_error()
                    health = service.health()
                    with self.assertRaises(
                        KnowledgeApiError
                    ) as raised:
                        service.query(
                            {
                                "entity": "/Game/Test/ItemA.ItemA",
                                "answerMode": "IDENTITY",
                                "budgetTokens": 500,
                            }
                        )

                self.assertIsNotNone(binding_error)
                self.assertFalse(health["available"])
                self.assertEqual(health["status"], "INVALID")
                self.assertEqual(
                    raised.exception.code,
                    "KB_VNEXT_SNAPSHOT_INVALID",
                )

    def test_health_rejects_non_object_manifest_source_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            manifest_path = root / "manifests" / "current.json"
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            manifest["source"] = "not-an-object"
            _write_snapshot_manifest(root, manifest)

            health = service.health()

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "INVALID")
        self.assertTrue(health["gap"])

    def test_empty_and_unrecovered_source_revision_fields_fail_closed(self):
        expected = {
            "healthStatus": "STALE",
            "healthFreshness": "STALE",
            "healthHasStaleGap": True,
            "factComplete": False,
            "identityComplete": False,
        }
        for column, value in (
            (column, value)
            for column in (
                "source_kind",
                "source_uri",
                "source_fingerprint",
                "producer_version",
                "schema_version",
                "generated_at",
                "freshness_status",
            )
            for value in (
                "",
                "UNKNOWN",
                "NOT_RECOVERED",
                "SOURCE_NOT_AVAILABLE",
            )
        ):
            with self.subTest(column=column, value=value):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    core = sqlite3.connect(root / "core.sqlite")
                    try:
                        core.execute(
                            f"UPDATE source_revisions SET {column}=? "
                            "WHERE revision_id=1",
                            (value,),
                        )
                        core.commit()
                    finally:
                        core.close()
                    _refresh_snapshot_database_metrics(root)

                    health = service.health()
                    fact = service.query(
                        {
                            "entity": "/Game/Test/ItemA.ItemA",
                            "answerMode": "FACT",
                            "factTypes": ["ITEM_PROPERTY"],
                            "factNames": ["Weight"],
                            "edgeTypes": [],
                            "requiresNative": False,
                            "requiresRuntime": False,
                            "requiresMapEvidence": False,
                            "evidenceLimit": 50,
                            "budgetTokens": 500,
                        }
                    )
                    identity = service.query(
                        {
                            "entity": "/Game/Test/ItemA.ItemA",
                            "answerMode": "IDENTITY",
                            "factTypes": [],
                            "factNames": [],
                            "edgeTypes": [],
                            "requiresNative": False,
                            "requiresRuntime": False,
                            "requiresMapEvidence": False,
                            "evidenceLimit": 50,
                            "budgetTokens": 500,
                        }
                    )

                actual = {
                    "healthStatus": health["status"],
                    "healthFreshness": health["freshness"],
                    "healthHasStaleGap": (
                        "KB_VNEXT_STALE_SOURCE"
                        in {item["code"] for item in health["gap"]}
                    ),
                    "factComplete": fact["status"] == "COMPLETE",
                    "identityComplete": identity["status"] == "COMPLETE",
                }
                self.assertEqual(actual, expected)

    def test_uncontrolled_source_revision_uri_fails_health_fact_and_identity(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    UPDATE source_revisions
                    SET source_uri='ftp://fixture/source'
                    WHERE revision_id=1
                    """
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            health = service.health()
            fact = service.query(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "answerMode": "FACT",
                    "factTypes": ["ITEM_PROPERTY"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )
            identity = service.query(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "answerMode": "IDENTITY",
                    "budgetTokens": 500,
                }
            )

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "STALE")
        self.assertIn(
            "KB_VNEXT_STALE_SOURCE",
            {item["code"] for item in health["gap"]},
        )
        self.assertEqual(fact["status"], "PARTIAL")
        self.assertEqual(fact["route"], "DB_PARTIAL")
        self.assertIn(
            "FACT_STALE",
            {item["code"] for item in fact["missingRequirements"]},
        )
        self.assertEqual(identity["status"], "PARTIAL")
        self.assertEqual(identity["route"], "DB_PARTIAL")
        self.assertIn(
            "IDENTITY_PROVENANCE_UNKNOWN",
            {item["code"] for item in identity["missingRequirements"]},
        )

    def test_missing_query_snapshot_cache_table_is_fail_closed_not_sql_error(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            cache = sqlite3.connect(root / "cache.sqlite")
            try:
                cache.execute("DROP TABLE query_snapshots")
                cache.commit()
            finally:
                cache.close()

            health = service.health()
            try:
                result = service.query(
                    {
                        "entity": "/Game/Test/ItemA.ItemA",
                        "answerMode": "FACT",
                        "factTypes": ["ITEM_PROPERTY"],
                        "factNames": ["Weight"],
                        "budgetTokens": 500,
                    }
                )
            except sqlite3.DatabaseError as error:
                self.fail(f"query leaked a raw cache database error: {error}")

        self.assertTrue(health["available"])
        self.assertEqual(health["status"], "DEGRADED_CACHE")
        self.assertEqual(
            {gap["code"] for gap in health["gap"]},
            {"KB_VNEXT_CACHE_DEGRADED"},
        )
        self.assertEqual(result["status"], "COMPLETE")

    def test_health_and_entity_pages_do_not_expose_local_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _snapshot(Path(temp_dir) / "vnext")
            health = service.health()
            self.assertTrue(health["available"])
            self.assertEqual(health["status"], "READY")
            self.assertEqual(health["cutover"]["mode"], "shadow")
            search = service.search_entities(
                query="Item", limit=1, cursor=0
            )
            self.assertEqual(search["returned"], 1)
            self.assertEqual(search["omitted"], 1)
            self.assertIn("cursor=1", search["nextQuery"])
            entity_id = search["items"][0]["entityId"]
            entity = service.entity(int(entity_id))
            facts = service.entity_collection(
                int(entity_id), kind="facts"
            )
            payload = json.dumps(
                {
                    "health": health,
                    "entity": entity,
                    "facts": facts,
                }
            )
            self.assertNotIn("C:\\", payload)
            self.assertNotIn(str(Path(temp_dir)), payload)
            self.assertTrue(facts["evidence"])
            self.assertTrue(health["capabilities"]["queryProvenance"])
            self.assertEqual(search["freshness"], "FRESH")
            self.assertEqual(
                search["items"][0]["sourceRevision"]["revisionId"],
                1,
            )
            self.assertEqual(entity["freshness"], "FRESH")
            self.assertEqual(
                entity["entity"]["sourceRevision"]["revisionId"],
                1,
            )
            self.assertEqual(
                entity["roles"][0]["sourceRevision"]["revisionId"],
                1,
            )
            self.assertEqual(
                entity["domains"][0]["sourceRevision"]["revisionId"],
                1,
            )
            self.assertEqual(facts["freshness"], "FRESH")
            self.assertEqual(
                facts["items"][0]["sourceRevision"]["revisionId"],
                1,
            )

    def test_query_provenance_capability_is_required_for_v4_health(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            _remove_query_provenance_capability(root)

            health = service.health()
            result = service.query(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "answerMode": "FACT",
                    "factTypes": ["ITEM_PROPERTY"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )
            with self.assertRaises(KnowledgeApiError) as raised:
                service.entity(1)

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "MIGRATION_REQUIRED")
        self.assertEqual(health["schemaVersion"], "ark-kb-core/v4")
        self.assertFalse(health["capabilities"]["queryProvenance"])
        self.assertEqual(
            health["gap"][0]["code"],
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertEqual(result["route"], "EVIDENCE_REQUIRED")
        self.assertNotEqual(result["status"], "COMPLETE")
        self.assertIn(
            "SCHEMA_MIGRATION_REQUIRED",
            {item["code"] for item in result["missingRequirements"]},
        )
        self.assertEqual(
            raised.exception.code,
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertEqual(raised.exception.status.value, 503)

    def test_native_function_and_class_edge_revision_columns_are_required(
        self,
    ):
        for table_name in ("native_functions", "class_edges"):
            with self.subTest(table_name=table_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    _remove_table_column(
                        root,
                        table_name=table_name,
                        column_name="source_revision_id",
                    )

                    health = service.health()
                    result = service.query(
                        {
                            "entity": "/Game/Test/ItemA.ItemA",
                            "answerMode": "FACT",
                            "factTypes": ["ITEM_PROPERTY"],
                            "factNames": ["Weight"],
                            "budgetTokens": 500,
                        }
                    )
                    with self.assertRaises(
                        KnowledgeApiError
                    ) as raised:
                        service.entity(1)

                self.assertFalse(health["available"])
                self.assertEqual(
                    health["status"],
                    "MIGRATION_REQUIRED",
                )
                self.assertFalse(
                    health["capabilities"]["queryProvenance"]
                )
                self.assertNotEqual(result["status"], "COMPLETE")
                self.assertNotEqual(
                    result["route"],
                    "DB_SEMANTIC_COMPLETE",
                )
                self.assertIn(
                    "SCHEMA_MIGRATION_REQUIRED",
                    {
                        item["code"]
                        for item in result["missingRequirements"]
                    },
                )
                self.assertEqual(
                    raised.exception.code,
                    "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
                )

    def test_health_rejects_stale_active_native_and_class_provenance(self):
        for scenario in (
            "native_function",
            "native_link",
            "class",
            "class_assignment",
            "class_edge",
        ):
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    _seed_stale_active_provenance(root, scenario)

                    health = service.health()

                self.assertFalse(health["available"])
                self.assertEqual(health["status"], "STALE")
                self.assertEqual(health["freshness"], "STALE")
                self.assertIn(
                    "KB_VNEXT_STALE_SOURCE",
                    {item["code"] for item in health["gap"]},
                )

    def test_health_rejects_unrecovered_active_evidence_uri(self):
        for scenario in (
            "fact",
            "relationship",
            "class_assignment",
            "class_edge",
        ):
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    _seed_invalid_active_evidence(root, scenario)

                    health = service.health()

                self.assertFalse(health["available"])
                self.assertEqual(health["status"], "STALE")
                self.assertEqual(health["freshness"], "STALE")
                self.assertIn(
                    "KB_VNEXT_STALE_SOURCE",
                    {item["code"] for item in health["gap"]},
                )
                self.assertIn(
                    "fresh, recovered provenance",
                    health["gap"][0]["detail"],
                )

    def test_health_accepts_encoded_discovery_assignment_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    UPDATE asset_class_assignments
                    SET evidence_uri=?
                    WHERE entity_id=1
                    """,
                    (
                        "discovery://asset/"
                        "%2FGame%2FTest%2FUnknown%2FBP%20Test.BP%20Test"
                        "#asset-class",
                    ),
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            health = service.health()

        self.assertTrue(health["available"])
        self.assertEqual(health["status"], "READY")

    def test_fact_collection_uses_any_fresh_active_proof_like_planner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    INSERT INTO source_revisions VALUES(
                        2, 'capture', 'capture://historical',
                        'historical-sha', 'test', 'v0',
                        '2026-07-26T00:00:00Z', 'STALE'
                    )
                    """
                )
                fact_id = int(
                    core.execute(
                        """
                        SELECT fact_id FROM facts
                        WHERE fact_type='ITEM_PROPERTY'
                          AND fact_name='Weight'
                        """
                    ).fetchone()[0]
                )
                core.execute(
                    """
                    INSERT INTO fact_evidence VALUES(
                        ?, 2, 'bp://fixture/item-a/weight/historical',
                        'HISTORICAL_DIRECT_FIELD'
                    )
                    """,
                    (fact_id,),
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            planned = service.query(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "answerMode": "FACT",
                    "factTypes": ["ITEM_PROPERTY"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )
            collection = service.entity_collection(1, kind="facts")

        self.assertEqual(planned["status"], "COMPLETE")
        self.assertEqual(planned["freshness"], "FRESH")
        self.assertEqual(collection["freshness"], "FRESH")
        self.assertEqual(collection["items"][0]["freshness"], "FRESH")
        self.assertNotIn(
            "STALE_SOURCE",
            {item["code"] for item in collection["gap"]},
        )

    def test_collections_reject_unrecovered_evidence_uri(self):
        cases = (
            ("fact", "facts", "PROVENANCE_UNKNOWN"),
            ("relationship", "relationships", "PROVENANCE_UNKNOWN"),
            ("class_assignment", "effective-defaults", "COVERAGE_OPEN"),
        )
        for scenario, kind, gap_code in cases:
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "vnext"
                    service = _snapshot(root)
                    _seed_invalid_active_evidence(root, scenario)

                    collection = service.entity_collection(1, kind=kind)

                self.assertEqual(collection["freshness"], "UNKNOWN")
                self.assertIn(
                    gap_code,
                    {item["code"] for item in collection["gap"]},
                )
                if kind in {"facts", "relationships"}:
                    self.assertEqual(
                        collection["items"][0]["freshness"],
                        "UNKNOWN",
                    )

    def test_effective_default_collection_includes_class_path_provenance(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    INSERT INTO source_revisions VALUES(
                        2, 'class-capture', 'class://historical/parent',
                        'historical-class-sha', 'test', 'v0',
                        '2026-07-26T00:00:00Z', 'STALE'
                    )
                    """
                )
                core.executemany(
                    """
                    INSERT INTO classes VALUES(
                        ?, ?, ?, '/Game/Test',
                        'BLUEPRINT_GENERATED_CLASS', 0,
                        1, 'CONFIRMED', 'HIGH'
                    )
                    """,
                    [
                        (11, "/Game/Test/ItemA.ItemA_C", "ItemA_C"),
                        (12, "/Game/Test/Base.Base_C", "Base_C"),
                    ],
                )
                core.execute(
                    """
                    INSERT INTO asset_class_assignments VALUES(
                        1, 11, 'GENERATED_CLASS',
                        'bp://fixture/item-a/class',
                        'CONFIRMED', 'HIGH', 1
                    )
                    """
                )
                core.execute(
                    """
                    INSERT INTO class_edges VALUES(
                        11, 12, 'blueprint_parent',
                        'bp://fixture/item-a/parent', 2,
                        'CONFIRMED', 'HIGH'
                    )
                    """
                )
                core.execute(
                    """
                    UPDATE effective_facts
                    SET resolution_chain_json=?
                    WHERE entity_id=1 AND fact_name='Weight'
                    """,
                    (
                        json.dumps(
                            {
                                "schema": "ark-kb-effective-path/v1",
                                "classes": [11, 12],
                                "edges": [
                                    {
                                        "childClassId": 11,
                                        "parentClassId": 12,
                                        "edgeKind": "blueprint_parent",
                                        "evidenceIds": [
                                            "bp://fixture/item-a/parent"
                                        ],
                                        "status": "CONFIRMED",
                                    }
                                ],
                            }
                        ),
                    ),
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            result = service.entity_collection(
                1,
                kind="effective-defaults",
            )

        self.assertEqual(result["freshness"], "STALE")
        self.assertIn(
            "STALE_SOURCE",
            {item["code"] for item in result["gap"]},
        )
        self.assertEqual(
            {
                item["evidenceRole"]
                for item in result["evidence"]
                if item.get("evidenceRole")
                in {"CLASS_ASSIGNMENT", "CLASS_EDGE_EVIDENCE"}
            },
            {"CLASS_ASSIGNMENT", "CLASS_EDGE_EVIDENCE"},
        )

    def test_entity_and_collection_freshness_is_lineage_derived(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    INSERT INTO source_revisions VALUES (
                        2, 'capture', 'capture://stale', 'stale-sha',
                        'test', 'v1', '2026-07-26T00:00:00Z', 'STALE'
                    )
                    """
                )
                core.execute(
                    "UPDATE packages SET current_revision_id=2"
                )
                core.execute(
                    "UPDATE knowledge_roles SET source_revision_id=2"
                )
                core.execute(
                    "UPDATE domain_memberships SET source_revision_id=2"
                )
                fact_id = int(
                    core.execute(
                        "SELECT fact_id FROM facts LIMIT 1"
                    ).fetchone()[0]
                )
                core.execute(
                    """
                    INSERT INTO fact_evidence VALUES (
                        ?, 2, 'bp://fixture/item-a/weight-stale',
                        'HISTORICAL_FIELD'
                    )
                    """,
                    (fact_id,),
                )
                core.execute(
                    """
                    INSERT INTO edges(
                        source_entity_id, target_entity_id, edge_type,
                        edge_strength, status, confidence,
                        source_revision_id, evidence_uri
                    ) VALUES (
                        1, 2, 'REFERENCES', 'HARD', 'CONFIRMED', 'HIGH',
                        2, 'bp://fixture/item-a/reference'
                    )
                    """
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            search = service.search_entities(query="ItemA")
            entity = service.entity(1)
            facts = service.entity_collection(1, kind="facts")
            relationships = service.entity_collection(
                1,
                kind="relationships",
            )
            health = service.health()

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "STALE")
        self.assertEqual(health["freshness"], "STALE")
        self.assertIn(
            "KB_VNEXT_STALE_SOURCE",
            {gap["code"] for gap in health["gap"]},
        )
        self.assertEqual(search["freshness"], "STALE")
        self.assertEqual(search["items"][0]["freshness"], "STALE")
        self.assertEqual(entity["freshness"], "STALE")
        self.assertEqual(entity["roles"][0]["freshness"], "STALE")
        self.assertEqual(entity["domains"][0]["freshness"], "STALE")
        self.assertEqual(facts["freshness"], "FRESH")
        self.assertEqual(facts["items"][0]["freshness"], "FRESH")
        self.assertEqual(
            len(facts["items"][0]["sourceRevisions"]),
            2,
        )
        self.assertEqual(relationships["freshness"], "STALE")
        self.assertEqual(
            relationships["items"][0]["sourceRevision"]["revisionId"],
            2,
        )
        self.assertIn(
            "STALE_SOURCE",
            {gap["code"] for gap in relationships["gap"]},
        )

    def test_missing_lineage_reports_unknown_instead_of_fresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    "UPDATE packages SET current_revision_id=NULL"
                )
                core.execute(
                    "UPDATE knowledge_roles SET source_revision_id=NULL"
                )
                core.execute(
                    "UPDATE domain_memberships SET source_revision_id=NULL"
                )
                core.execute("DELETE FROM fact_evidence")
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            search = service.search_entities(query="ItemA")
            entity = service.entity(1)
            facts = service.entity_collection(1, kind="facts")

        self.assertEqual(search["freshness"], "UNKNOWN")
        self.assertIsNone(search["items"][0]["sourceRevision"])
        self.assertEqual(entity["freshness"], "UNKNOWN")
        self.assertIsNone(entity["roles"][0]["sourceRevision"])
        self.assertIsNone(entity["domains"][0]["sourceRevision"])
        self.assertEqual(facts["freshness"], "UNKNOWN")
        self.assertEqual(facts["items"][0]["sourceRevisions"], [])
        self.assertIn(
            "PROVENANCE_UNKNOWN",
            {gap["code"] for gap in facts["gap"]},
        )

    def test_v1_shadow_snapshot_reports_migration_gap_without_sql_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            _remove_effective_candidate_capability(root)

            health = service.health()
            collection = service.entity_collection(
                1, kind="effective-defaults"
            )
            result = service.query(
                {
                    "entity": "/Game/Test/ItemA.ItemA",
                    "factTypes": ["EFFECTIVE_DEFAULT"],
                    "factNames": ["Weight"],
                    "budgetTokens": 500,
                }
            )

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "MIGRATION_REQUIRED")
        self.assertEqual(health["schemaVersion"], "ark-kb-core/v1")
        self.assertFalse(
            health["capabilities"]["effectiveCandidateExplanations"]
        )
        self.assertEqual(
            health["gap"][0]["code"],
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertEqual(collection["returned"], 1)
        self.assertEqual(
            collection["items"][0]["candidateExplanationStatus"],
            "SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertEqual(collection["items"][0]["candidates"], [])
        self.assertIn(
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
            {item["code"] for item in collection["gap"]},
        )
        self.assertEqual(result["route"], "EVIDENCE_REQUIRED")
        self.assertIn(
            "SCHEMA_MIGRATION_REQUIRED",
            {item["code"] for item in result["missingRequirements"]},
        )
        self.assertEqual(
            result["facts"][0]["candidateExplanationStatus"],
            "SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertIn(
            "rebuild_core_v4_snapshot",
            {
                item["operation"]
                for item in result["recommendedProbes"]
            },
        )

    def test_v2_core_without_semantic_derivations_requires_migration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            _remove_semantic_derivation_capability(root)

            health = service.health()

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "MIGRATION_REQUIRED")
        self.assertEqual(health["schemaVersion"], "ark-kb-core/v2")
        self.assertTrue(
            health["capabilities"]["effectiveCandidateExplanations"]
        )
        self.assertFalse(
            health["capabilities"]["semanticAdapterDerivations"]
        )
        self.assertEqual(
            health["gap"][0]["code"],
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
        )

    def test_v3_core_without_typed_map_evidence_requires_migration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            _remove_typed_map_capability(root)

            health = service.health()

        self.assertFalse(health["available"])
        self.assertEqual(health["status"], "MIGRATION_REQUIRED")
        self.assertEqual(health["schemaVersion"], "ark-kb-core/v3")
        self.assertTrue(
            health["capabilities"]["effectiveCandidateExplanations"]
        )
        self.assertTrue(
            health["capabilities"]["semanticAdapterDerivations"]
        )
        self.assertFalse(
            health["capabilities"]["typedMapUsageEvidence"]
        )
        self.assertEqual(
            health["gap"][0]["code"],
            "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
        )

    def test_effective_defaults_expose_unresolved_rows_without_stringifying_none(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                ontology = load_ontology(PROJECT_ROOT / "ontology")
                selected_fact_id = int(
                    core.execute(
                        """
                        SELECT fact_id FROM effective_facts
                        WHERE entity_id=1
                          AND fact_type='EFFECTIVE_DEFAULT'
                          AND fact_name='Weight'
                        """
                    ).fetchone()[0]
                )
                rejected_fact_id = store_fact(
                    core,
                    ontology=ontology,
                    subject_entity_id=2,
                    fact_type="DECLARED_DEFAULT",
                    fact_name="MissingWeight",
                    scope_kind="DECLARED",
                    declared_on_entity_id=2,
                    value=FactValue("UNKNOWN"),
                    status="NOT_RECOVERED",
                    confidence="LOW",
                    source_revision_id=1,
                    evidence_uri="bp://fixture/item-b/missing-weight",
                    evidence_role="DEFAULT_VALUE",
                )
                core.execute(
                    """
                    INSERT INTO effective_facts(
                        entity_id, fact_type, fact_name, fact_id,
                        inherited_from_entity_id, resolution_chain_json,
                        resolution_status, source_revision_set_hash
                    ) VALUES (
                        1, 'EFFECTIVE_DEFAULT', 'MissingWeight', NULL, NULL,
                        '{"schema":"ark-kb-effective-path/v1","classes":[],"edges":[]}',
                        'NOT_RECOVERED', 'hash'
                    )
                    """
                )
                core.execute(
                    """
                    INSERT INTO effective_fact_candidates VALUES (
                        1, 'EFFECTIVE_DEFAULT', 'Weight', ?, 1, 0,
                        'CONFIRMED', 1, ''
                    )
                    """,
                    (selected_fact_id,),
                )
                core.execute(
                    """
                    INSERT INTO effective_fact_candidates VALUES (
                        1, 'EFFECTIVE_DEFAULT', 'MissingWeight', ?, 2, 1,
                        'PARENT_CHAIN_OPEN', 0, 'PARENT_CHAIN_OPEN'
                    )
                    """,
                    (rejected_fact_id,),
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            result = service.entity_collection(
                1, kind="effective-defaults"
            )

        self.assertEqual(result["returned"], 2)
        unresolved = next(
            item
            for item in result["items"]
            if item["factName"] == "MissingWeight"
        )
        self.assertIsNone(unresolved["factId"])
        self.assertIsNone(unresolved["valueKind"])
        self.assertIsNone(unresolved["status"])
        self.assertIsNone(unresolved["confidence"])
        self.assertEqual(
            unresolved["resolutionStatus"], "NOT_RECOVERED"
        )
        self.assertEqual(unresolved["candidateTotal"], 1)
        self.assertEqual(unresolved["candidateReturned"], 1)
        self.assertEqual(unresolved["candidateOmitted"], 0)
        rejected = unresolved["candidates"][0]
        self.assertEqual(rejected["candidateFactId"], rejected_fact_id)
        self.assertEqual(rejected["declaredOnEntityId"], 2)
        self.assertEqual(
            rejected["declaredOnUri"], "/Game/Test/ItemB.ItemB"
        )
        self.assertFalse(rejected["selected"])
        self.assertEqual(
            rejected["rejectionReason"], "PARENT_CHAIN_OPEN"
        )
        resolved = next(
            item for item in result["items"] if item["factName"] == "Weight"
        )
        self.assertEqual(resolved["candidateTotal"], 1)
        self.assertTrue(resolved["candidates"][0]["selected"])
        self.assertNotIn('"None"', json.dumps(result))
        self.assertTrue(result["gap"])
        self.assertEqual(result["freshness"], "UNKNOWN")
        self.assertEqual(
            {item["factId"] for item in result["evidence"]},
            {
                item["factId"]
                for item in result["items"]
                if item["factId"] is not None
            },
        )

    def test_effective_defaults_require_fresh_evidence_for_each_current_fact(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                fact_id = int(
                    core.execute(
                        """
                        SELECT fact_id FROM effective_facts
                        WHERE entity_id=1 AND fact_name='Weight'
                        """
                    ).fetchone()[0]
                )
                core.execute(
                    """
                    INSERT INTO source_revisions VALUES (
                        2, 'capture', 'capture://historical', 'old-sha',
                        'test', 'v0', '2026-07-26T00:00:00Z', 'STALE'
                    )
                    """
                )
                core.execute(
                    """
                    INSERT INTO fact_evidence VALUES (
                        ?, 2, 'bp://fixture/item-a/weight-old',
                        'HISTORICAL_VALUE'
                    )
                    """,
                    (fact_id,),
                )
                core.commit()
                _refresh_snapshot_database_metrics(root)

                mixed = service.entity_collection(
                    1, kind="effective-defaults"
                )
                self.assertEqual(mixed["freshness"], "FRESH")
                self.assertEqual(mixed["gap"], [])
                self.assertEqual(mixed["items"][0]["candidates"], [])
                self.assertEqual(
                    mixed["items"][0]["candidateTotal"], 0
                )
                self.assertEqual(
                    mixed["items"][0]["candidateOmitted"], 0
                )

                core.execute(
                    """
                    UPDATE source_revisions
                    SET freshness_status='STALE'
                    WHERE revision_id=1
                    """
                )
                core.commit()
                _refresh_snapshot_database_metrics(root)
                stale = service.entity_collection(
                    1, kind="effective-defaults"
                )
                self.assertEqual(stale["freshness"], "STALE")
                self.assertIn(
                    "STALE_SOURCE",
                    {item["code"] for item in stale["gap"]},
                )
            finally:
                core.close()

    def test_effective_defaults_reject_missing_current_fact_projection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            core = sqlite3.connect(root / "core.sqlite")
            try:
                core.execute(
                    """
                    UPDATE facts SET current=0
                    WHERE fact_id=(
                        SELECT fact_id FROM effective_facts
                        WHERE entity_id=1 AND fact_name='Weight'
                    )
                    """
                )
                core.commit()
            finally:
                core.close()
            _refresh_snapshot_database_metrics(root)

            result = service.entity_collection(
                1, kind="effective-defaults"
            )

        self.assertEqual(result["freshness"], "UNKNOWN")
        self.assertTrue(result["gap"])
        self.assertIsNone(result["items"][0]["valueKind"])
        self.assertIsNone(result["items"][0]["status"])

    def test_query_is_bounded_and_cached_as_disposable_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            service = _snapshot(root)
            result = service.query(
                {
                    "entity": "ItemA",
                    "factTypes": ["ITEM_PROPERTY"],
                    "budgetTokens": 500,
                    "evidenceLimit": 10,
                }
            )
            self.assertEqual(result["route"], "DB_ONLY_COMPLETE")
            self.assertEqual(result["answerMode"], "FACT")
            self.assertEqual(result["status"], "COMPLETE")
            self.assertLessEqual(
                result["contextPack"]["estimatedTokens"], 500
            )
            self.assertEqual(result["gap"], [])
            cache = sqlite3.connect(root / "cache.sqlite")
            try:
                self.assertEqual(
                    cache.execute(
                        "SELECT COUNT(*) FROM query_snapshots"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    cache.execute(
                        "SELECT COUNT(*) FROM context_packs"
                    ).fetchone()[0],
                    1,
                )
            finally:
                cache.close()

    def test_internal_timing_never_leaks_into_query_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vnext"
            _snapshot(root)
            timing = SegmentTiming()
            service = VNextKnowledgeService(root, _timing=timing)

            result = service.query(
                {
                    "entity": "ItemA",
                    "factTypes": ["ITEM_PROPERTY"],
                    "budgetTokens": 500,
                    "evidenceLimit": 10,
                }
            )

        self.assertNotIn("timing", result)
        self.assertNotIn("timingDiagnostics", result)
        report = timing.report()
        for segment in (
            "pointerManifestResolution",
            "connectionAcquire",
            "cacheValidation",
            "cacheWrite",
            "answerContextSerialization",
        ):
            self.assertGreater(report["segments"][segment]["samples"], 0)

    def test_query_answer_mode_contract_is_explicit_and_additive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _snapshot(Path(temp_dir) / "vnext")

            identity = service.query(
                {
                    "entity": "ItemA",
                    "answerMode": "IDENTITY",
                    "budgetTokens": 500,
                }
            )
            underspecified = service.query(
                {
                    "entity": "ItemA",
                    "budgetTokens": 500,
                }
            )
            fact = service.query(
                {
                    "entity": "ItemA",
                    "answerMode": "FACT",
                    "factTypes": ["ITEM_PROPERTY"],
                    "budgetTokens": 500,
                }
            )

        self.assertEqual(identity["answerMode"], "IDENTITY")
        self.assertEqual(identity["route"], "IDENTITY_ONLY_COMPLETE")
        self.assertEqual(identity["status"], "COMPLETE")
        self.assertEqual(identity["facts"], [])
        self.assertEqual(underspecified["route"], "EVIDENCE_REQUIRED")
        self.assertEqual(underspecified["status"], "GAP")
        self.assertEqual(
            underspecified["missingRequirements"][0]["code"],
            "REQUEST_UNDERSPECIFIED",
        )
        self.assertEqual(fact["answerMode"], "FACT")
        self.assertEqual(fact["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(fact["status"], "COMPLETE")

    def test_query_validation_rejects_unknown_answer_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _snapshot(Path(temp_dir) / "vnext")

            with self.assertRaises(KnowledgeApiError) as raised:
                service.query(
                    {
                        "entity": "ItemA",
                        "answerMode": "SEMANTIC",
                    }
                )

        self.assertEqual(raised.exception.code, "REQUEST_INVALID")
        self.assertIn("answerMode", raised.exception.message)

    def test_query_validation_rejects_unbounded_or_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _snapshot(Path(temp_dir) / "vnext")
            with self.assertRaisesRegex(
                KnowledgeApiError, "Unsupported query fields"
            ):
                service.query(
                    {"entity": "ItemA", "command": ["python", "tool.py"]}
                )
            with self.assertRaisesRegex(
                KnowledgeApiError, "budgetTokens"
            ):
                service.query(
                    {"entity": "ItemA", "budgetTokens": 20_000}
                )
            with self.assertRaisesRegex(
                KnowledgeApiError, "factTypes"
            ):
                service.query(
                    {
                        "entity": "ItemA",
                        "factTypes": ["x"] * 21,
                    }
                )

    def test_context_pack_redacts_absolute_local_paths(self):
        pack = build_bounded_context_pack(
            {
                "route": "EVIDENCE_REQUIRED",
                "freshness": "UNKNOWN",
                "entity": {
                    "canonicalUri": r"C:\Users\person\secret.uasset"
                },
                "facts": [],
                "relationships": [],
                "evidence": [
                    {
                        "evidenceUri": r"C:\Users\person\evidence.json",
                        "freshness": "FRESH",
                    }
                ],
                "missingRequirements": [
                    {"code": "MISSING_FACT", "requirement": "FORMULA"}
                ],
                "recommendedProbes": [],
            },
            budget_tokens=500,
        )
        self.assertNotIn(r"C:\Users", pack["content"])
        self.assertIn("[LOCAL_PATH_REDACTED]", pack["content"])

    def test_context_pack_renders_unresolved_effective_fact_without_none(self):
        pack = build_bounded_context_pack(
            {
                "route": "EVIDENCE_REQUIRED",
                "freshness": "UNKNOWN",
                "entity": {"canonicalUri": "ark://class/ItemA"},
                "facts": [
                    {
                        "factType": "EFFECTIVE_DEFAULT",
                        "factName": "MissingWeight",
                        "status": None,
                        "valueKind": None,
                        "resolutionStatus": "PARENT_CHAIN_OPEN",
                    }
                ],
                "relationships": [],
                "evidence": [],
                "missingRequirements": [],
                "recommendedProbes": [],
            },
            budget_tokens=500,
        )

        self.assertIn(
            "[PARENT_CHAIN_OPEN/UNKNOWN]",
            pack["content"],
        )
        self.assertNotIn("None/None", pack["content"])

    def test_context_pack_compacts_selected_and_rejected_candidates(self):
        candidates = [
            {
                "candidateFactId": 101,
                "declaredOnEntityId": 1,
                "declaredOnUri": "/Game/Test/ItemA.ItemA",
                "inheritanceDepth": 0,
                "pathStatus": "CONFIRMED",
                "selected": True,
                "rejectionReason": "",
                "valueKind": "INTEGER",
                "valueInteger": 7,
                "status": "CONFIRMED",
            },
            *[
                {
                    "candidateFactId": candidate_id,
                    "declaredOnEntityId": 2,
                    "declaredOnUri": "/Game/Test/ItemB.ItemB",
                    "inheritanceDepth": candidate_id - 100,
                    "pathStatus": "CONFIRMED",
                    "selected": False,
                    "rejectionReason": "SHADOWED_BY_NEARER_USABLE",
                    "valueKind": "INTEGER",
                    "valueInteger": candidate_id,
                    "status": "CONFIRMED",
                }
                for candidate_id in range(102, 107)
            ],
        ]
        pack = build_bounded_context_pack(
            {
                "route": "EVIDENCE_REQUIRED",
                "freshness": "UNKNOWN",
                "entity": {"canonicalUri": "ark://class/ItemA"},
                "facts": [
                    {
                        "factType": "EFFECTIVE_DEFAULT",
                        "factName": "Rate",
                        "factId": 101,
                        "resolutionStatus": "RESOLVED",
                        "valueKind": "INTEGER",
                        "valueInteger": 7,
                        "status": "CONFIRMED",
                        "candidates": candidates,
                        "candidateTotal": 10,
                        "candidateReturned": 6,
                        "candidateOmitted": 4,
                    },
                    {
                        "factType": "EFFECTIVE_DEFAULT",
                        "factName": "UnresolvedRate",
                        "factId": None,
                        "resolutionStatus": "PARENT_CHAIN_OPEN",
                        "valueKind": None,
                        "status": None,
                        "candidates": [
                            {
                                "candidateFactId": 201,
                                "declaredOnEntityId": 2,
                                "declaredOnUri": (
                                    "/Game/Test/ItemB.ItemB"
                                ),
                                "inheritanceDepth": 1,
                                "pathStatus": "PARENT_CHAIN_OPEN",
                                "selected": False,
                                "rejectionReason": "PARENT_CHAIN_OPEN",
                                "valueKind": "UNKNOWN",
                                "status": "NOT_RECOVERED",
                            }
                        ],
                        "candidateTotal": 1,
                        "candidateReturned": 1,
                        "candidateOmitted": 0,
                    },
                ],
                "relationships": [],
                "evidence": [],
                "missingRequirements": [],
                "recommendedProbes": [],
            },
            budget_tokens=700,
        )

        content = pack["content"]
        self.assertIn("## Effective candidates", content)
        self.assertIn("selected candidate #101", content)
        self.assertIn("rejected candidate #102", content)
        self.assertIn("SHADOWED_BY_NEARER_USABLE", content)
        self.assertIn("7 candidates omitted", content)
        self.assertIn(
            "UnresolvedRate unresolved=PARENT_CHAIN_OPEN",
            content,
        )
        self.assertIn("rejected candidate #201", content)
        self.assertLessEqual(content.count("candidate #"), 4)
        self.assertLessEqual(pack["estimatedTokens"], 700)

    def test_unbuilt_snapshot_returns_health_gap_and_query_503(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = VNextKnowledgeService(Path(temp_dir) / "vnext")
            health = service.health()
            self.assertFalse(health["available"])
            self.assertEqual(
                health["gap"][0]["code"], "KB_VNEXT_NOT_BUILT"
            )
            with self.assertRaises(KnowledgeApiError) as raised:
                service.query({"entity": "ItemA"})
            self.assertEqual(
                raised.exception.status.value, 503
            )


if __name__ == "__main__":
    unittest.main()
