import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_catalog_sqlite import (  # noqa: E402
    SQLiteHarvestCatalog,
    build_harvest_catalog_sqlite,
)
from blueprint_translator.resource_nodes import query_resource_nodes  # noqa: E402


def _node(
    node_id: str,
    name: str,
    *,
    resource: str,
    map_name: str,
    mesh_name: str = "Mesh",
) -> dict[str, object]:
    node_resource_id = f"resource-{node_id}"
    return {
        "id": node_id,
        "name": name,
        "objectPath": f"/Game/Nodes/{node_id}.{node_id}",
        "mesh": {"status": "CONFIRMED", "name": mesh_name},
        "harvestComponent": {
            "status": "CONFIRMED",
            "name": "HarvestComponent_C",
            "packagePath": "/Game/Components/HarvestComponent",
        },
        "resources": {
            "status": "CONFIRMED",
            "count": 1,
            "items": [
                {
                    "entryIndex": 0,
                    "resource": resource,
                    "displayName": resource.removesuffix("_C"),
                    "nodeResourceId": node_resource_id,
                    "evidenceStatus": "CONFIRMED",
                    "gaps": [],
                }
            ],
        },
        "mapReferences": {
            "status": "DIRECT_SCAN_COMPLETE",
            "count": 1,
            "items": [
                {
                    "id": f"map-{node_id}",
                    "name": map_name,
                    "objectPath": f"/Game/Maps/{map_name}/{map_name}",
                    "mapFamily": map_name,
                    "relation": "DIRECT_PACKAGE_REFERENCE",
                    "evidenceStatus": "CONFIRMED",
                    "usageStatus": "DIRECT_REFERENCE",
                }
            ],
            "indirectStatus": "NOT_INDEXED",
        },
        "image": {"status": "AVAILABLE", "url": f"/images/{node_id}.jpg"},
        "evidence": {"sourceSha256": node_id * 8, "parser": "test"},
        "gaps": [],
    }


def _catalog() -> dict[str, object]:
    return {
        "schema": "ark-resource-node-catalog/v1",
        "dataset": {"revision": "d" * 64, "generatedAt": "2026-07-21T00:00:00Z"},
        "coverage": {
            "nodesDecoded": 3,
            "mapScan": {
                "status": "DIRECT_SCAN_COMPLETE",
                "filesScanned": 3,
                "indirectReferences": "NOT_INDEXED",
            },
        },
        "nodes": [
            _node(
                "node-b",
                "Beta Rock",
                resource="PrimalItemResource_Stone_C",
                map_name="Genesis",
            ),
            _node(
                "node-a",
                "Alpha Metal",
                resource="PrimalItemResource_Metal_C",
                map_name="TheIsland",
                mesh_name="MetalMesh",
            ),
            _node(
                "node-c",
                "Gamma Metal",
                resource="PrimalItemResource_Metal_C",
                map_name="Genesis2",
            ),
        ],
        "failures": [],
        "skipped": {"nonFoliage": 0},
    }


class HarvestCatalogSQLiteTests(unittest.TestCase):
    def test_indexed_queries_and_detail_match_json_contract(self):
        catalog = _catalog()
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "harvest_catalog.sqlite"
            summary = build_harvest_catalog_sqlite(catalog, sqlite_path)
            reader = SQLiteHarvestCatalog(sqlite_path)

            cases = (
                {},
                {"q": "metal"},
                {"q": "metalmesh"},
                {"map_name": "genesis"},
                {"resource": "PrimalItemResource_Metal_C", "offset": 1, "limit": 1},
            )
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    self.assertEqual(
                        reader.list_nodes(**arguments),
                        query_resource_nodes(catalog, **arguments),
                    )
            self.assertEqual(reader.get_node("node-a"), catalog["nodes"][1])

        self.assertEqual(summary["nodes"], 3)
        self.assertEqual(summary["resources"], 3)
        self.assertEqual(summary["mapEvidence"], 3)

    def test_database_has_normalized_evidence_tables_and_query_indices(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "harvest_catalog.sqlite"
            build_harvest_catalog_sqlite(_catalog(), sqlite_path)

            with closing(sqlite3.connect(sqlite_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                indices = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    )
                }
                resource = connection.execute(
                    "SELECT resource, evidence_status FROM resource_evidence "
                    "WHERE node_resource_id = ?",
                    ("resource-node-a",),
                ).fetchone()
                map_row = connection.execute(
                    "SELECT map_family, relation, evidence_status FROM map_evidence "
                    "WHERE node_id = ?",
                    ("node-a",),
                ).fetchone()

        self.assertTrue({"metadata", "node_index", "resource_evidence", "map_evidence"} <= tables)
        self.assertTrue(
            {
                "idx_node_index_sort",
                "idx_resource_evidence_resource",
                "idx_resource_evidence_identity",
                "idx_map_evidence_family",
            }
            <= indices
        )
        self.assertEqual(resource, ("PrimalItemResource_Metal_C", "CONFIRMED"))
        self.assertEqual(map_row, ("TheIsland", "DIRECT_PACKAGE_REFERENCE", "CONFIRMED"))

    def test_reader_fails_closed_for_unknown_sqlite_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "harvest_catalog.sqlite"
            with closing(sqlite3.connect(sqlite_path)) as connection:
                with connection:
                    connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT)")
                    connection.execute(
                        "INSERT INTO metadata VALUES (?, ?)",
                        ("sqliteSchema", json.dumps("ark-harvest-sqlite/v999")),
                    )

            with self.assertRaises(ValueError):
                SQLiteHarvestCatalog(sqlite_path).list_nodes()


if __name__ == "__main__":
    unittest.main()
