from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.kb_vnext.map_usage import (  # noqa: E402
    MAP_DIRECT_REFERENCE,
    MAP_PCG_DEPENDENCY,
    MAP_WORLD_PARTITION_REFERENCE,
    create_map_usage_tables,
    materialize_map_usage_edges,
)
from blueprint_translator.kb_vnext.schema_capabilities import (  # noqa: E402
    supports_typed_map_usage_evidence,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


CORE_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE source_revisions(
    revision_id INTEGER PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    freshness_status TEXT NOT NULL,
    UNIQUE(source_kind, source_uri, source_fingerprint)
);

CREATE TABLE packages(
    package_id INTEGER PRIMARY KEY,
    package_path TEXT UNIQUE NOT NULL,
    mount_point TEXT NOT NULL,
    content_pack_id INTEGER,
    current_revision_id INTEGER
);

CREATE TABLE entities(
    entity_id INTEGER PRIMARY KEY,
    canonical_uri TEXT UNIQUE NOT NULL,
    entity_kind TEXT NOT NULL,
    package_id INTEGER,
    class_id INTEGER,
    display_name TEXT,
    internal_name TEXT,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    FOREIGN KEY(package_id) REFERENCES packages(package_id)
);

CREATE TABLE edges(
    edge_id INTEGER PRIMARY KEY,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL,
    edge_strength TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_revision_id INTEGER NOT NULL,
    evidence_uri TEXT NOT NULL,
    source_property TEXT NOT NULL DEFAULT '',
    source_graph TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(source_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(target_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
);
"""

DISCOVERY_SQL = """
CREATE TABLE assets(
    object_path TEXT PRIMARY KEY,
    package_path TEXT NOT NULL,
    is_map INTEGER NOT NULL,
    identity_status TEXT NOT NULL,
    identity_confidence TEXT NOT NULL
);

CREATE TABLE asset_references(
    reference_id TEXT PRIMARY KEY,
    source_object_path TEXT NOT NULL,
    target_object_path TEXT NOT NULL,
    edge_kind TEXT NOT NULL,
    reference_strength TEXT NOT NULL,
    source_property TEXT NOT NULL,
    source_graph TEXT NOT NULL,
    source_function TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_kind TEXT NOT NULL
);

CREATE TABLE source_inventory(
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    limitations_json TEXT NOT NULL
);
"""


def _semantic_revision(payload: dict[str, object]) -> str:
    dataset = payload["dataset"]
    assert isinstance(dataset, dict)
    digest = hashlib.sha256()
    digest.update(str(dataset.get("sourceStatus") or "").encode("utf-8"))
    coverage = payload.get("coverage")
    map_scan = (
        coverage.get("mapScan")
        if isinstance(coverage, dict)
        else {}
    )
    digest.update(
        json.dumps(
            map_scan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(
        str(
            dataset.get("rankingDatasetRevision")
            or dataset.get("rankingScanManifestHash")
            or ""
        ).encode("utf-8")
    )
    evaluation_revision = str(
        dataset.get("evaluationDatasetRevision") or ""
    )
    if evaluation_revision:
        digest.update(evaluation_revision.encode("utf-8"))
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    for node in sorted(
        nodes,
        key=lambda value: str(value.get("objectPath") or ""),
    ):
        digest.update(
            json.dumps(
                node,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _resource_catalog(
    *,
    relation: str,
    usage_status: str,
    source_package: str,
    target_uri: str,
    map_kind: str = "PLAYABLE_MAP_EVIDENCE",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "ark-resource-node-catalog/v1",
        "dataset": {
            "revision": "",
            "generatedAt": "2026-07-27T01:00:00+00:00",
            "rankingDatasetRevision": "a" * 64,
            "sourceStatus": "PARTIAL",
        },
        "coverage": {
            "mapScan": {
                "status": "REFERENCE_SCAN_COMPLETE",
                "claimsCompleteMapUsage": False,
                "direct": {"status": "DIRECT_SCAN_COMPLETE"},
                "pcgBiome": {"status": "PCG_BIOME_SCAN_COMPLETE"},
                "worldPartitionExternalActors": {
                    "status": (
                        "WORLD_PARTITION_EXTERNAL_ACTOR_SCAN_COMPLETE"
                    )
                },
            }
        },
        "nodes": [
            {
                "id": "node_fixture",
                "objectPath": target_uri,
                "mapReferences": {
                    "status": "REFERENCE_SCAN_COMPLETE",
                    "items": [
                        {
                            "id": "map_fixture",
                            "objectPath": source_package,
                            "mapFamily": "TheIsland",
                            "mapKind": map_kind,
                            "relation": relation,
                            "evidenceStatus": "CONFIRMED",
                            "usageStatus": usage_status,
                            "evidenceCount": 3,
                            "evidenceExamples": [
                                "/Game/__ExternalActors__/Fixture/A",
                            ],
                        }
                    ],
                },
                "mapUsage": {
                    "status": "PARTIAL",
                    "claimsCompleteMapUsage": False,
                },
            }
        ],
    }
    dataset = payload["dataset"]
    assert isinstance(dataset, dict)
    dataset["revision"] = _semantic_revision(payload)
    return payload


class MapUsageIngestTests(unittest.TestCase):
    def test_full_core_storage_schema_contains_typed_map_contract(
        self,
    ) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(FULL_CORE_SCHEMA_SQL)
            objects = {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    """
                    SELECT name, type
                    FROM sqlite_master
                    WHERE name LIKE 'map_usage_%'
                       OR name='confirmed_map_usage_edges'
                    """
                )
            }
        finally:
            connection.close()
        self.assertEqual(
            objects,
            {
                ("map_usage_sources", "table"),
                ("map_usage_edge_evidence", "table"),
                ("confirmed_map_usage_edges", "view"),
            },
        )

    def test_map_capability_requires_every_column_read_by_the_planner(
        self,
    ) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(FULL_CORE_SCHEMA_SQL)
            connection.execute("DROP VIEW confirmed_map_usage_edges")
            connection.execute(
                """
                CREATE VIEW confirmed_map_usage_edges AS
                SELECT
                    edge_id, source_entity_id, target_entity_id,
                    edge_type, status, confidence, source_revision_id,
                    evidence_uri, '' AS map_usage_id,
                    '' AS evidence_layer, '' AS usage_status,
                    '' AS freshness_status,
                    0 AS claims_complete_map_usage,
                    0 AS claims_spawn_coordinates
                FROM edges
                """
            )

            compatible = supports_typed_map_usage_evidence(connection)
        finally:
            connection.close()

        self.assertFalse(compatible)

    def test_map_capability_rejects_an_unfiltered_same_shape_view(
        self,
    ) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(FULL_CORE_SCHEMA_SQL)
            connection.execute("DROP VIEW confirmed_map_usage_edges")
            connection.execute(
                """
                CREATE VIEW confirmed_map_usage_edges AS
                SELECT
                    edge.edge_id,
                    edge.source_entity_id,
                    edge.target_entity_id,
                    edge.edge_type,
                    edge.edge_strength,
                    edge.status,
                    edge.confidence,
                    edge.source_revision_id,
                    edge.evidence_uri,
                    evidence.map_usage_id,
                    evidence.evidence_layer,
                    evidence.map_family,
                    evidence.map_kind,
                    evidence.source_evidence_status,
                    evidence.usage_status,
                    evidence.freshness_status,
                    evidence.claims_complete_map_usage,
                    evidence.claims_spawn_coordinates,
                    evidence.evidence_count,
                    evidence.evidence_examples_json
                FROM edges AS edge
                JOIN map_usage_edge_evidence AS evidence
                  ON evidence.edge_id=edge.edge_id
                """
            )

            compatible = supports_typed_map_usage_evidence(connection)
        finally:
            connection.close()

        self.assertFalse(compatible)

    def setUp(self) -> None:
        self.core = sqlite3.connect(":memory:")
        self.core.executescript(CORE_SQL)
        create_map_usage_tables(self.core)
        self.discovery = sqlite3.connect(":memory:")
        self.discovery.executescript(DISCOVERY_SQL)
        self.core.execute(
            """
            INSERT INTO source_revisions VALUES(
                1, 'discovery', 'discovery://fixture',
                'fixture-sha', 'fixture', 'fixture/v1',
                '2026-07-27T00:00:00+00:00', 'FRESH'
            )
            """
        )
        self.discovery.execute(
            """
            INSERT INTO source_inventory VALUES(
                'registry', 'unreal_asset_registry',
                'ark.kb.registry-snapshot.v2', 'registry-sha',
                'COMPLETE', 'HIGH', 2,
                '2026-07-27T00:00:00+00:00', '[]'
            )
            """
        )
        self._add_asset(
            "/Game/Maps/Fixture.Fixture",
            "/Game/Maps/Fixture",
            is_map=True,
        )
        self._add_asset(
            "/Game/Items/Thing.Thing",
            "/Game/Items/Thing",
        )
        self.core.commit()
        self.discovery.commit()

    def tearDown(self) -> None:
        self.discovery.close()
        self.core.close()

    def _add_asset(
        self,
        uri: str,
        package_path: str,
        *,
        is_map: bool = False,
    ) -> None:
        package_id = int(
            self.core.execute(
                """
                INSERT INTO packages(
                    package_path, mount_point, current_revision_id
                ) VALUES(?, '/Game', 1)
                RETURNING package_id
                """,
                (package_path,),
            ).fetchone()[0]
        )
        self.core.execute(
            """
            INSERT INTO entities(
                canonical_uri, entity_kind, package_id,
                display_name, internal_name, status, confidence
            ) VALUES(?, ?, ?, ?, ?, 'EXTRACTED', 'HIGH')
            """,
            (
                uri,
                "MAP_ASSET" if is_map else "ASSET",
                package_id,
                uri.rsplit(".", 1)[-1],
                uri.rsplit(".", 1)[-1],
            ),
        )
        self.discovery.execute(
            """
            INSERT INTO assets VALUES(
                ?, ?, ?, 'EXTRACTED', 'HIGH'
            )
            """,
            (uri, package_path, int(is_map)),
        )

    def _add_reference(
        self,
        reference_id: str,
        *,
        strength: str = "hard",
        confidence: str = "HIGH",
        source_kind: str = "unreal_asset_registry",
    ) -> None:
        self.discovery.execute(
            """
            INSERT INTO asset_references VALUES(
                ?, '/Game/Maps/Fixture', '/Game/Items/Thing',
                'package_dependency', ?, 'AssetRegistryDependency',
                '', '', ?, ?, ?
            )
            """,
            (
                reference_id,
                strength,
                f"registry-reference://{reference_id}",
                confidence,
                source_kind,
            ),
        )
        self.discovery.commit()

    def test_materializes_exact_registry_map_dependency_as_confirmed_edge(
        self,
    ) -> None:
        self._add_reference("direct-hard")

        result = materialize_map_usage_edges(
            self.discovery,
            self.core,
            source_revision_id=1,
            resource_catalog_path=None,
            generated_at="2026-07-27T02:00:00+00:00",
        )

        self.assertEqual(result["directConfirmed"], 1)
        edge = self.core.execute(
            """
            SELECT edge_type, edge_strength, status, confidence,
                   source_revision_id, evidence_uri
            FROM edges
            """
        ).fetchone()
        self.assertEqual(
            edge,
            (
                MAP_DIRECT_REFERENCE,
                "HARD",
                "CONFIRMED",
                "HIGH",
                1,
                "registry-reference://direct-hard",
            ),
        )
        evidence = self.core.execute(
            """
            SELECT evidence_layer, map_kind, freshness_status,
                   claims_complete_map_usage,
                   claims_spawn_coordinates
            FROM map_usage_edge_evidence
            """
        ).fetchone()
        self.assertEqual(
            evidence,
            (
                "ASSET_REGISTRY_HARD_PACKAGE_DEPENDENCY",
                "MAP_ASSET",
                "FRESH",
                0,
                0,
            ),
        )
        self.assertEqual(
            self.core.execute(
                "SELECT COUNT(*) FROM confirmed_map_usage_edges"
            ).fetchone()[0],
            1,
        )

    def test_invalid_explicit_registry_evidence_uri_never_confirms(
        self,
    ) -> None:
        self._add_reference("direct-hard")

        for evidence_uri in (
            "UNKNOWN",
            "NOT_RECOVERED",
            "SOURCE_NOT_AVAILABLE",
            "garbage",
        ):
            with self.subTest(evidence_uri=evidence_uri):
                self.discovery.execute(
                    """
                    UPDATE asset_references
                    SET source_evidence_id=?
                    WHERE reference_id='direct-hard'
                    """,
                    (evidence_uri,),
                )
                self.discovery.commit()

                result = materialize_map_usage_edges(
                    self.discovery,
                    self.core,
                    source_revision_id=1,
                    resource_catalog_path=None,
                    generated_at="2026-07-27T02:00:00+00:00",
                )

                self.assertEqual(result["directConfirmed"], 0)
                self.assertEqual(result["directCandidate"], 1)
                self.assertEqual(
                    self.core.execute(
                        "SELECT status FROM edges"
                    ).fetchone()[0],
                    "CANDIDATE",
                )
                self.assertEqual(
                    self.core.execute(
                        "SELECT COUNT(*) FROM confirmed_map_usage_edges"
                    ).fetchone()[0],
                    0,
                )

    def test_missing_registry_evidence_uses_trusted_generated_uri(
        self,
    ) -> None:
        self._add_reference("direct-hard")
        self.discovery.execute(
            """
            UPDATE asset_references
            SET source_evidence_id=''
            WHERE reference_id='direct-hard'
            """
        )
        self.discovery.commit()

        result = materialize_map_usage_edges(
            self.discovery,
            self.core,
            source_revision_id=1,
            resource_catalog_path=None,
            generated_at="2026-07-27T02:00:00+00:00",
        )

        self.assertEqual(result["directConfirmed"], 1)
        self.assertTrue(
            self.core.execute(
                "SELECT evidence_uri FROM edges"
            ).fetchone()[0].startswith(
                "map-evidence://asset-registry/"
            )
        )
        self.assertEqual(
            self.core.execute(
                "SELECT COUNT(*) FROM confirmed_map_usage_edges"
            ).fetchone()[0],
            1,
        )

    def test_confirmed_map_view_rejects_invalid_evidence_uri(self) -> None:
        self._add_reference("direct-hard")
        materialize_map_usage_edges(
            self.discovery,
            self.core,
            source_revision_id=1,
            resource_catalog_path=None,
            generated_at="2026-07-27T02:00:00+00:00",
        )
        self.core.execute(
            """
            UPDATE edges
            SET evidence_uri='UNKNOWN'
            """
        )

        self.assertEqual(
            self.core.execute(
                "SELECT COUNT(*) FROM confirmed_map_usage_edges"
            ).fetchone()[0],
            0,
        )

    def test_incomplete_source_revision_identity_never_confirms(
        self,
    ) -> None:
        self._add_reference("direct-hard")
        original = {
            row[1]: row[0]
            for row in self.core.execute(
                """
                SELECT source_kind, 'source_kind'
                FROM source_revisions WHERE revision_id=1
                UNION ALL
                SELECT source_uri, 'source_uri'
                FROM source_revisions WHERE revision_id=1
                UNION ALL
                SELECT source_fingerprint, 'source_fingerprint'
                FROM source_revisions WHERE revision_id=1
                UNION ALL
                SELECT producer_version, 'producer_version'
                FROM source_revisions WHERE revision_id=1
                UNION ALL
                SELECT schema_version, 'schema_version'
                FROM source_revisions WHERE revision_id=1
                UNION ALL
                SELECT generated_at, 'generated_at'
                FROM source_revisions WHERE revision_id=1
                """
            )
        }

        for field in original:
            for sentinel in (
                "UNKNOWN",
                "NOT_RECOVERED",
                "SOURCE_NOT_AVAILABLE",
            ):
                with self.subTest(field=field, sentinel=sentinel):
                    self.core.execute(
                        f"""
                        UPDATE source_revisions
                        SET {field}=?
                        WHERE revision_id=1
                        """,
                        (sentinel,),
                    )
                    self.core.commit()

                    result = materialize_map_usage_edges(
                        self.discovery,
                        self.core,
                        source_revision_id=1,
                        resource_catalog_path=None,
                        generated_at="2026-07-27T02:00:00+00:00",
                    )

                    self.assertEqual(result["directConfirmed"], 0)
                    self.assertEqual(
                        self.core.execute(
                            """
                            SELECT COUNT(*)
                            FROM confirmed_map_usage_edges
                            """
                        ).fetchone()[0],
                        0,
                    )
                    self.core.execute(
                        f"""
                        UPDATE source_revisions
                        SET {field}=?
                        WHERE revision_id=1
                        """,
                        (original[field],),
                    )
                    self.core.commit()

    def test_candidate_legacy_and_stale_rows_never_enter_confirmed_view(
        self,
    ) -> None:
        self._add_reference("searchable", strength="searchable")
        self._add_reference(
            "legacy",
            source_kind="existing_knowledge_database",
        )
        self.core.execute(
            """
            UPDATE source_revisions
            SET freshness_status='STALE'
            WHERE revision_id=1
            """
        )
        self.core.commit()

        materialize_map_usage_edges(
            self.discovery,
            self.core,
            source_revision_id=1,
            resource_catalog_path=None,
            generated_at="2026-07-27T02:00:00+00:00",
        )

        statuses = {
            row[0]: row[1]
            for row in self.core.execute(
                """
                SELECT evidence_uri, status
                FROM edges
                ORDER BY evidence_uri
                """
            )
        }
        self.assertEqual(
            statuses,
            {
                "registry-reference://legacy": "STALE",
                "registry-reference://searchable": "STALE",
            },
        )
        original_statuses = {
            row[0]: row[1]
            for row in self.core.execute(
                """
                SELECT e.evidence_uri, m.usage_status
                FROM edges AS e
                JOIN map_usage_edge_evidence AS m USING(edge_id)
                """
            )
        }
        self.assertEqual(
            original_statuses,
            {
                "registry-reference://legacy": "LEGACY_UNVERIFIED",
                "registry-reference://searchable": "CANDIDATE",
            },
        )
        self.assertEqual(
            self.core.execute(
                "SELECT COUNT(*) FROM confirmed_map_usage_edges"
            ).fetchone()[0],
            0,
        )

    def test_candidate_source_evidence_never_enters_confirmed_view(
        self,
    ) -> None:
        self._add_reference("direct-hard")
        materialize_map_usage_edges(
            self.discovery,
            self.core,
            source_revision_id=1,
            resource_catalog_path=None,
            generated_at="2026-07-27T02:00:00+00:00",
        )
        self.core.execute(
            """
            UPDATE map_usage_edge_evidence
            SET source_evidence_status='CANDIDATE'
            """
        )

        confirmed = int(
            self.core.execute(
                "SELECT COUNT(*) FROM confirmed_map_usage_edges"
            ).fetchone()[0]
        )

        self.assertEqual(confirmed, 0)

    def test_pcg_and_world_partition_keep_catalog_usage_status(
        self,
    ) -> None:
        self._add_asset(
            "/Game/PCG/Biome.Biome",
            "/Game/PCG/Biome",
        )
        self._add_asset(
            "/Game/Maps/TheIsland_WP.TheIsland_WP",
            "/Game/Maps/TheIsland_WP",
            is_map=True,
        )
        self.core.commit()
        self.discovery.commit()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pcg_path = root / "pcg.json"
            wp_path = root / "wp.json"
            pcg_path.write_text(
                json.dumps(
                    _resource_catalog(
                        relation="PCG_BIOME_REFERENCE",
                        usage_status="CANDIDATE",
                        source_package="/Game/PCG/Biome",
                        target_uri="/Game/Items/Thing.Thing",
                    )
                ),
                encoding="utf-8",
            )
            wp_path.write_text(
                json.dumps(
                    _resource_catalog(
                        relation=(
                            "WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE"
                        ),
                        usage_status="CONFIRMED",
                        source_package="/Game/Maps/TheIsland_WP",
                        target_uri="/Game/Items/Thing.Thing",
                    )
                ),
                encoding="utf-8",
            )

            pcg_result = materialize_map_usage_edges(
                self.discovery,
                self.core,
                source_revision_id=1,
                resource_catalog_path=pcg_path,
                generated_at="2026-07-27T02:00:00+00:00",
            )
            self.assertEqual(pcg_result["pcgCandidate"], 1)
            self.assertEqual(
                self.core.execute(
                    """
                    SELECT edge_type, status, freshness_status,
                           claims_complete_map_usage,
                           claims_spawn_coordinates
                    FROM edges
                    JOIN map_usage_edge_evidence USING(edge_id)
                    """
                ).fetchone(),
                (
                    MAP_PCG_DEPENDENCY,
                    "CANDIDATE",
                    "FRESH",
                    0,
                    0,
                ),
            )
            self.assertEqual(
                self.core.execute(
                    "SELECT COUNT(*) FROM confirmed_map_usage_edges"
                ).fetchone()[0],
                0,
            )

            wp_result = materialize_map_usage_edges(
                self.discovery,
                self.core,
                source_revision_id=1,
                resource_catalog_path=wp_path,
                generated_at="2026-07-27T02:00:00+00:00",
            )
            self.assertEqual(wp_result["worldPartitionConfirmed"], 1)
            self.assertEqual(
                self.core.execute(
                    """
                    SELECT edge_type, status, evidence_count,
                           evidence_examples_json
                    FROM confirmed_map_usage_edges
                    """
                ).fetchone(),
                (
                    MAP_WORLD_PARTITION_REFERENCE,
                    "CONFIRMED",
                    3,
                    '["/Game/__ExternalActors__/Fixture/A"]',
                ),
            )

    def test_catalog_rejects_unrecovered_generated_at(self) -> None:
        for sentinel in (
            "UNKNOWN",
            "NOT_RECOVERED",
            "SOURCE_NOT_AVAILABLE",
        ):
            with self.subTest(sentinel=sentinel):
                payload = _resource_catalog(
                    relation="PCG_BIOME_REFERENCE",
                    usage_status="CONFIRMED",
                    source_package="/Game/PCG/Biome",
                    target_uri="/Game/Items/Thing.Thing",
                )
                dataset = payload["dataset"]
                assert isinstance(dataset, dict)
                dataset["generatedAt"] = sentinel
                with tempfile.TemporaryDirectory() as temp_dir:
                    catalog_path = Path(temp_dir) / "catalog.json"
                    catalog_path.write_text(
                        json.dumps(payload),
                        encoding="utf-8",
                    )

                    with self.assertRaisesRegex(
                        ValueError,
                        "generatedAt",
                    ):
                        materialize_map_usage_edges(
                            self.discovery,
                            self.core,
                            source_revision_id=1,
                            resource_catalog_path=catalog_path,
                            generated_at=(
                                "2026-07-27T02:00:00+00:00"
                            ),
                        )

    def test_auxiliary_catalog_reference_is_not_map_usage_edge(self) -> None:
        self._add_asset(
            "/Game/PCG/Biome.Biome",
            "/Game/PCG/Biome",
        )
        self.core.commit()
        self.discovery.commit()
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    _resource_catalog(
                        relation="PCG_BIOME_REFERENCE",
                        usage_status="CONFIRMED",
                        source_package="/Game/PCG/Biome",
                        target_uri="/Game/Items/Thing.Thing",
                        map_kind="AUXILIARY_MAP_EVIDENCE",
                    )
                ),
                encoding="utf-8",
            )

            result = materialize_map_usage_edges(
                self.discovery,
                self.core,
                source_revision_id=1,
                resource_catalog_path=catalog_path,
                generated_at="2026-07-27T02:00:00+00:00",
            )

        self.assertEqual(result["catalogAuxiliarySkipped"], 1)
        self.assertEqual(
            self.core.execute(
                """
                SELECT COUNT(*) FROM edges
                WHERE edge_type IN (?, ?, ?)
                """,
                (
                    MAP_DIRECT_REFERENCE,
                    MAP_PCG_DEPENDENCY,
                    MAP_WORLD_PARTITION_REFERENCE,
                ),
            ).fetchone()[0],
            0,
        )

    def test_missing_catalog_records_explicit_gap(self) -> None:
        result = materialize_map_usage_edges(
            self.discovery,
            self.core,
            source_revision_id=1,
            resource_catalog_path=None,
            generated_at="2026-07-27T02:00:00+00:00",
        )

        self.assertEqual(result["catalogStatus"], "SOURCE_NOT_AVAILABLE")
        self.assertEqual(
            self.core.execute(
                """
                SELECT status, freshness_status,
                       claims_complete_map_usage,
                       claims_spawn_coordinates, failure_reason
                FROM map_usage_sources
                WHERE source_id='RESOURCE_NODE_CATALOG'
                """
            ).fetchone(),
            (
                "SOURCE_NOT_AVAILABLE",
                "SOURCE_NOT_AVAILABLE",
                0,
                0,
                "MAP_EVIDENCE_SOURCE_NOT_AVAILABLE",
            ),
        )

    def test_tampered_catalog_revision_fails_before_writing_edges(self) -> None:
        self._add_reference("direct-hard")
        self._add_asset(
            "/Game/PCG/Biome.Biome",
            "/Game/PCG/Biome",
        )
        self.core.commit()
        self.discovery.commit()
        payload = _resource_catalog(
            relation="PCG_BIOME_REFERENCE",
            usage_status="CANDIDATE",
            source_package="/Game/PCG/Biome",
            target_uri="/Game/Items/Thing.Thing",
        )
        dataset = payload["dataset"]
        assert isinstance(dataset, dict)
        dataset["revision"] = "0" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "catalog.json"
            catalog_path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "semantic revision",
            ):
                materialize_map_usage_edges(
                    self.discovery,
                    self.core,
                    source_revision_id=1,
                    resource_catalog_path=catalog_path,
                    generated_at="2026-07-27T02:00:00+00:00",
                )

        self.assertEqual(
            self.core.execute(
                "SELECT COUNT(*) FROM edges"
                ).fetchone()[0],
                0,
            )

    def test_map_scan_coverage_is_part_of_the_semantic_revision(
        self,
    ) -> None:
        payload = _resource_catalog(
            relation="PCG_BIOME_REFERENCE",
            usage_status="CANDIDATE",
            source_package="/Game/PCG/Biome",
            target_uri="/Game/Items/Thing.Thing",
        )
        original_revision = str(payload["dataset"]["revision"])
        payload["coverage"]["mapScan"]["direct"]["status"] = (
            "DIRECT_SCAN_PARTIAL"
        )

        changed_revision = _semantic_revision(payload)

        self.assertNotEqual(changed_revision, original_revision)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tampered-coverage.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "semantic revision"):
                materialize_map_usage_edges(
                    self.discovery,
                    self.core,
                    source_revision_id=1,
                    resource_catalog_path=path,
                    generated_at="2026-07-27T02:00:00+00:00",
                )


if __name__ == "__main__":
    unittest.main()
