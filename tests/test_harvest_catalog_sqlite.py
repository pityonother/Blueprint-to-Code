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
from blueprint_translator.resource_nodes import (  # noqa: E402
    query_resource_nodes,
    resource_display_name,
)


def _node(
    node_id: str,
    name: str,
    *,
    resource: str,
    map_name: str,
    mesh_name: str = "Mesh",
    additional_maps: tuple[str, ...] = (),
    display_name: str | None = None,
    resource_object_path: str = "",
) -> dict[str, object]:
    node_resource_id = f"resource-{node_id}"
    map_families = tuple(
        family for family in (map_name, *additional_maps) if family
    )
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
                    "resourceKey": resource_object_path or resource,
                    "resourceObjectPath": resource_object_path,
                    "displayName": display_name or resource_display_name(resource),
                    "nodeResourceId": node_resource_id,
                    "evidenceStatus": "CONFIRMED",
                    "gaps": [],
                }
            ],
        },
        "mapReferences": {
            "status": "DIRECT_SCAN_COMPLETE",
            "count": len(map_families),
            "items": [
                {
                    "id": f"map-{node_id}-{family}",
                    "name": family,
                    "objectPath": f"/Game/Maps/{family}/{family}",
                    "mapFamily": family,
                    "mapKind": "PLAYABLE_MAP_EVIDENCE",
                    "relation": "DIRECT_PACKAGE_REFERENCE",
                    "evidenceStatus": "CONFIRMED",
                    "usageStatus": "DIRECT_REFERENCE",
                }
                for family in map_families
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
    def test_resource_object_path_is_the_facet_and_filter_identity_with_legacy_class_support(self):
        resource_class = "PrimalItemResource_CommonMushroom_C"
        aberration_path = (
            "/Game/Aberration/CoreBlueprints/Resources/"
            "PrimalItemResource_CommonMushroom.PrimalItemResource_CommonMushroom_C"
        )
        primal_earth_path = (
            "/Game/PrimalEarth/CoreBlueprints/Resources/"
            "PrimalItemResource_CommonMushroom.PrimalItemResource_CommonMushroom_C"
        )
        catalog = {
            "schema": "ark-resource-node-catalog/v1",
            "dataset": {"revision": "c" * 64},
            "coverage": {"nodesDecoded": 2, "mapScan": {}},
            "nodes": [
                _node(
                    "node-aggeravic",
                    "Aberration Mushroom Patch",
                    resource=resource_class,
                    resource_object_path=aberration_path,
                    display_name="Aggeravic Mushroom",
                    map_name="Aberration",
                ),
                _node(
                    "node-common",
                    "Island Mushroom Patch",
                    resource=resource_class,
                    resource_object_path=primal_earth_path,
                    display_name="Common Mushroom",
                    map_name="TheIsland",
                ),
            ],
        }

        json_all = query_resource_nodes(catalog)
        json_aberration = query_resource_nodes(catalog, resource=aberration_path)
        json_primal_earth = query_resource_nodes(catalog, resource=primal_earth_path)
        json_legacy = query_resource_nodes(catalog, resource=resource_class)
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "harvest_catalog.sqlite"
            build_harvest_catalog_sqlite(catalog, sqlite_path)
            reader = SQLiteHarvestCatalog(sqlite_path)
            sqlite_all = reader.list_nodes()
            sqlite_aberration = reader.list_nodes(resource=aberration_path)
            sqlite_primal_earth = reader.list_nodes(resource=primal_earth_path)
            sqlite_legacy = reader.list_nodes(resource=resource_class)

        self.assertEqual(sqlite_all, json_all)
        self.assertEqual(sqlite_aberration, json_aberration)
        self.assertEqual(sqlite_primal_earth, json_primal_earth)
        self.assertEqual(sqlite_legacy, json_legacy)
        self.assertEqual(
            json_all["facets"]["resources"],
            [
                {
                    "resourceKey": aberration_path,
                    "resource": resource_class,
                    "resourceObjectPath": aberration_path,
                    "displayName": "Aggeravic Mushroom",
                    "nodeCount": 1,
                },
                {
                    "resourceKey": primal_earth_path,
                    "resource": resource_class,
                    "resourceObjectPath": primal_earth_path,
                    "displayName": "Common Mushroom",
                    "nodeCount": 1,
                },
            ],
        )
        self.assertEqual(
            [item["id"] for item in json_aberration["items"]],
            ["node-aggeravic"],
        )
        self.assertEqual(
            [item["id"] for item in json_primal_earth["items"]],
            ["node-common"],
        )
        self.assertEqual(json_legacy["total"], 2)

    def test_devkit_display_name_survives_json_facets_sqlite_preview_and_detail(self):
        resource_class = "PrimalItemConsumable_JellyVenom_C"
        node = _node(
            "node-toxin",
            "Jelly Node",
            resource=resource_class,
            display_name="Bio Toxin",
            map_name="Aberration",
        )
        catalog = {
            "schema": "ark-resource-node-catalog/v1",
            "dataset": {"revision": "b" * 64},
            "coverage": {"nodesDecoded": 1, "mapScan": {}},
            "nodes": [node],
        }

        json_page = query_resource_nodes(catalog, resource=resource_class)
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "harvest_catalog.sqlite"
            build_harvest_catalog_sqlite(catalog, sqlite_path)
            reader = SQLiteHarvestCatalog(sqlite_path)
            sqlite_page = reader.list_nodes(resource=resource_class)
            detail = reader.get_node("node-toxin")

        self.assertEqual(json_page["facets"]["resources"][0]["displayName"], "Bio Toxin")
        self.assertEqual(sqlite_page, json_page)
        self.assertEqual(
            sqlite_page["items"][0]["resources"]["items"][0]["displayName"],
            "Bio Toxin",
        )
        self.assertEqual(
            detail["resources"]["items"][0]["displayName"],
            "Bio Toxin",
        )

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

    def test_evidence_exclusive_map_filter_and_resource_facets_match_json_fallback(self):
        catalog = _catalog()
        catalog["nodes"].extend(
            [
                _node(
                    "node-shared",
                    "Shared Metal",
                    resource="PrimalItemResource_Metal_C",
                    map_name="TheIsland",
                    additional_maps=("Genesis",),
                ),
                _node(
                    "node-island-wood",
                    "Island Wood",
                    resource="PrimalItemResource_Wood_C",
                    map_name="TheIsland",
                ),
                _node(
                    "node-unknown",
                    "Unknown Metal",
                    resource="PrimalItemResource_Metal_C",
                    map_name="",
                ),
            ]
        )
        arguments = {
            "only_map_family": "theisland",
            "resource": "PrimalItemResource_Metal_C",
            "offset": 0,
            "limit": 1,
        }

        json_page = query_resource_nodes(catalog, **arguments)
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "harvest_catalog.sqlite"
            build_harvest_catalog_sqlite(catalog, sqlite_path)
            sqlite_page = SQLiteHarvestCatalog(sqlite_path).list_nodes(**arguments)

        self.assertEqual(sqlite_page, json_page)
        self.assertEqual(json_page["total"], 1)
        self.assertEqual([item["id"] for item in json_page["items"]], ["node-a"])
        self.assertEqual(
            json_page["appliedFilters"],
            {
                "q": "",
                "map": "",
                "onlyMapFamily": "theisland",
                "resource": "PrimalItemResource_Metal_C",
            },
        )
        self.assertEqual(
            json_page["facets"]["resources"],
            [
                {
                    "resourceKey": "PrimalItemResource_Metal_C",
                    "resource": "PrimalItemResource_Metal_C",
                    "displayName": "Metal",
                    "nodeCount": 1,
                },
                {
                    "resourceKey": "PrimalItemResource_Wood_C",
                    "resource": "PrimalItemResource_Wood_C",
                    "displayName": "Wood",
                    "nodeCount": 1,
                },
            ],
        )
        exclusive_counts = {
            item["mapFamily"]: item["nodeCount"]
            for item in json_page["facets"]["onlyMapFamilies"]
        }
        self.assertEqual(
            exclusive_counts,
            {"Genesis": 1, "Genesis2": 1, "TheIsland": 2},
        )
        self.assertEqual(
            json_page["facets"]["mapExclusivity"]["definition"],
            "RECOVERED_PLAYABLE_MAP_FAMILY_SET_EQUALS_SELECTED_FAMILY",
        )
        self.assertFalse(
            json_page["facets"]["mapExclusivity"]["claimsCompleteMapUsage"]
        )
        self.assertFalse(
            json_page["facets"]["mapExclusivity"]["isGlobalExclusivityClaim"]
        )

        contains_page = query_resource_nodes(catalog, map_name="TheIsland")
        exclusive_page = query_resource_nodes(
            catalog,
            only_map_family="TheIsland",
            offset=1,
            limit=1,
        )
        self.assertEqual(contains_page["total"], 3)
        self.assertEqual(exclusive_page["total"], 2)
        self.assertEqual(exclusive_page["offset"], 1)
        self.assertEqual(len(exclusive_page["items"]), 1)
        self.assertNotEqual(exclusive_page["items"][0]["id"], "node-unknown")

    def test_exclusive_map_facets_casefold_duplicate_family_evidence(self):
        catalog = {
            **_catalog(),
            "nodes": [
                _node(
                    "node-casefold-map",
                    "Casefold Map Rock",
                    resource="PrimalItemResource_Metal_C",
                    map_name="TheIsland",
                    additional_maps=("theisland",),
                )
            ],
        }
        arguments = {"only_map_family": "THEISLAND"}
        json_page = query_resource_nodes(catalog, **arguments)

        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "harvest_catalog.sqlite"
            build_harvest_catalog_sqlite(catalog, sqlite_path)
            sqlite_page = SQLiteHarvestCatalog(sqlite_path).list_nodes(**arguments)

        self.assertEqual(sqlite_page, json_page)
        self.assertEqual(json_page["total"], 1)
        self.assertEqual(
            json_page["facets"]["onlyMapFamilies"],
            [{"mapFamily": "TheIsland", "nodeCount": 1}],
        )

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
                    "SELECT resource_key, resource, evidence_status "
                    "FROM resource_evidence "
                    "WHERE node_resource_id = ?",
                    ("resource-node-a",),
                ).fetchone()
                resource_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(resource_evidence)"
                    )
                }
                user_version = connection.execute("PRAGMA user_version").fetchone()[0]
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
                "idx_resource_evidence_key",
                "idx_resource_evidence_identity",
                "idx_map_evidence_family",
            }
            <= indices
        )
        self.assertTrue({"resource_key", "resource_key_fold"} <= resource_columns)
        self.assertEqual(user_version, 2)
        self.assertEqual(
            resource,
            (
                "PrimalItemResource_Metal_C",
                "PrimalItemResource_Metal_C",
                "CONFIRMED",
            ),
        )
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
