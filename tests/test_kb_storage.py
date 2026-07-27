from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.snapshot import (  # noqa: E402
    build_vnext_snapshot,
)


def _discovery_fixture(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE assets(
            object_path TEXT PRIMARY KEY,
            package_path TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            asset_class_path TEXT NOT NULL,
            blueprint_kind TEXT NOT NULL,
            generated_class_path TEXT NOT NULL,
            parent_class_path TEXT NOT NULL,
            native_parent_class_path TEXT NOT NULL,
            mount_point TEXT NOT NULL,
            plugin_or_dlc TEXT NOT NULL,
            is_blueprint INTEGER,
            is_data_asset INTEGER,
            is_data_table INTEGER,
            is_function_library INTEGER,
            is_blueprint_interface INTEGER,
            is_map INTEGER NOT NULL,
            file_size_total INTEGER NOT NULL,
            source_fingerprint TEXT NOT NULL,
            source_modified TEXT NOT NULL,
            capture_exists INTEGER NOT NULL,
            evidence_freshness TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            identity_status TEXT NOT NULL,
            identity_confidence TEXT NOT NULL,
            graph_count INTEGER NOT NULL,
            default_property_count INTEGER NOT NULL,
            descendant_count INTEGER NOT NULL,
            referencer_count INTEGER NOT NULL,
            component_reuse_count INTEGER NOT NULL,
            cross_domain_reference_count INTEGER NOT NULL,
            registry_usage_count INTEGER NOT NULL,
            query_hit_count INTEGER,
            query_hit_status TEXT NOT NULL,
            existing_report_count INTEGER,
            existing_report_status TEXT NOT NULL
        );
        CREATE TABLE asset_references(
            reference_id TEXT PRIMARY KEY,
            source_object_path TEXT NOT NULL,
            target_object_path TEXT NOT NULL,
            edge_kind TEXT NOT NULL,
            reference_strength TEXT NOT NULL,
            source_property TEXT NOT NULL,
            source_evidence_id TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_kind TEXT NOT NULL
        );
        CREATE TABLE class_edges(
            child_class_path TEXT NOT NULL,
            parent_class_path TEXT NOT NULL,
            edge_kind TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            confidence TEXT NOT NULL
        );
        CREATE TABLE system_registrations(
            registration_id TEXT PRIMARY KEY,
            owner_object_path TEXT NOT NULL,
            registration_type TEXT NOT NULL,
            target_object_path TEXT NOT NULL,
            source_property TEXT NOT NULL,
            source_evidence_id TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_kind TEXT NOT NULL
        );
        CREATE TABLE coverage(
            object_path TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            confirmed_count INTEGER NOT NULL,
            heuristic_count INTEGER NOT NULL,
            ambiguous_count INTEGER NOT NULL,
            not_recovered_count INTEGER NOT NULL,
            source_not_available_count INTEGER NOT NULL,
            stale_count INTEGER NOT NULL,
            failure_reason TEXT NOT NULL,
            PRIMARY KEY(object_path, stage)
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
    )
    assets = [
        (
            "/Game/Test/BP_Base.BP_Base",
            "/Game/Test/BP_Base",
            "BP_Base",
            "/Script/Engine.Blueprint",
            "Blueprint",
            "/Game/Test/BP_Base.BP_Base_C",
            "/Script/Engine.Actor",
            "/Script/Engine.Actor",
            "/Game",
            "base",
            1,
            0,
            0,
            0,
            0,
            0,
            100,
            "sha-base",
            "2026-07-27T00:00:00+00:00",
            1,
            "FRESH",
            "CONFIRMED",
            "EXTRACTED",
            "HIGH",
            2,
            3,
            1,
            1,
            0,
            1,
            1,
            1,
            "MEASURED",
            1,
            "MEASURED",
        ),
        (
            "/Game/Test/T_Texture.T_Texture",
            "/Game/Test/T_Texture",
            "T_Texture",
            "/Script/Engine.Texture2D",
            "Object",
            "UNKNOWN",
            "UNKNOWN",
            "/Script/Engine.Texture",
            "/Game",
            "base",
            0,
            0,
            0,
            0,
            0,
            0,
            200,
            "sha-texture",
            "2026-07-27T00:00:00+00:00",
            0,
            "NOT_MEASURED",
            "NOT_MEASURED",
            "EXTRACTED",
            "HIGH",
            0,
            0,
            0,
            500,
            0,
            2,
            0,
            None,
            "NOT_MEASURED",
            None,
            "NOT_MEASURED",
        ),
    ]
    connection.executemany(
        "INSERT INTO assets VALUES ("
        + ",".join("?" for _ in range(35))
        + ")",
        assets,
    )
    connection.execute(
        """
        INSERT INTO asset_references VALUES (
            'r1', '/Game/Test/BP_Base.BP_Base',
            '/Game/Test/T_Texture.T_Texture', 'object_reference', 'hard',
            'Texture', 'bp://fixture/r1', 'HIGH', 'blueprint_evidence'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO class_edges VALUES (
            '/Game/Test/BP_Base.BP_Base_C', '/Script/Engine.Actor',
            'native_parent', 'fixture', 'HIGH'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO system_registrations VALUES (
            's1', '/Game/Test/BP_Base.BP_Base', 'global_asset_reference',
            '/Game/Test/T_Texture.T_Texture', 'Texture',
            'existing-kb://fixture/1', 'MEDIUM',
            'existing_knowledge_database'
        )
        """
    )
    connection.executemany(
        "INSERT INTO coverage VALUES (?, 'asset_identity', 'CONFIRMED', 1, 0, 0, 0, 0, 0, '')",
        [(asset[0],) for asset in assets],
    )
    connection.execute(
        """
        INSERT INTO source_inventory VALUES (
            'source-1', 'registry', 'v1', 'fixture', 'COMPLETE',
            'HIGH', 2, '2026-07-27T00:00:00+00:00', '[]'
        )
        """
    )
    connection.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("schema", "blueprint-to-code-kb-discovery/v1"),
            ("generated_at_utc", "2026-07-27T00:00:00+00:00"),
        ],
    )
    connection.commit()
    connection.close()


class KnowledgeStorageTests(unittest.TestCase):
    def test_builds_four_normalized_stores_and_keeps_legacy_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            output = root / "vnext"
            _discovery_fixture(discovery)
            result = build_vnext_snapshot(
                project_root=PROJECT_ROOT,
                discovery_database=discovery,
                legacy_kb_root=root / "legacy",
                capture_root=root / "captures",
                native_root=root / "native",
                output_dir=output,
                full_snapshot=True,
                generated_at="2026-07-27T00:00:00+00:00",
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                result["cutover"]["defaultQuerySource"], "legacy"
            )
            for name in (
                "catalog.sqlite",
                "core.sqlite",
                "search.sqlite",
                "cache.sqlite",
            ):
                self.assertTrue((output / name).is_file())
                self.assertEqual(
                    result["databases"][name]["integrity"], "ok"
                )
                self.assertEqual(
                    result["databases"][name]["foreignKeyViolations"], 0
                )
            catalog = sqlite3.connect(output / "catalog.sqlite")
            core = sqlite3.connect(output / "core.sqlite")
            try:
                self.assertEqual(
                    catalog.execute(
                        "SELECT COUNT(*) FROM catalog_edges"
                    ).fetchone()[0],
                    1,
                )
                source_uri, target_uri = catalog.execute(
                    """
                    SELECT source.canonical_uri, target.canonical_uri
                    FROM catalog_edges AS e
                    JOIN catalog_nodes AS source
                      ON source.node_id=e.source_node_id
                    JOIN catalog_nodes AS target
                      ON target.node_id=e.target_node_id
                    """
                ).fetchone()
                self.assertEqual(
                    source_uri, "/Game/Test/BP_Base.BP_Base"
                )
                self.assertEqual(
                    target_uri, "/Game/Test/T_Texture.T_Texture"
                )
                texture_id = core.execute(
                    """
                    SELECT entity_id FROM entities
                    WHERE canonical_uri='/Game/Test/T_Texture.T_Texture'
                    """
                ).fetchone()[0]
                roles = {
                    row[0]
                    for row in core.execute(
                        """
                        SELECT role FROM knowledge_roles
                        WHERE entity_id=?
                        """,
                        (texture_id,),
                    )
                }
                self.assertIn("visual_support_asset", roles)
                self.assertNotIn("global_system_hub", roles)
                self.assertEqual(
                    core.execute(
                        """
                        SELECT depth_policy
                        FROM knowledge_depth_policies
                        WHERE entity_id=?
                        """,
                        (texture_id,),
                    ).fetchone()[0],
                    "INDEX_ONLY",
                )
            finally:
                catalog.close()
                core.close()
            manifest = json.loads(
                (output / "manifests" / "current.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["schema"], "ark-kb-vnext-snapshot/v1")
            self.assertEqual(
                manifest["cutover"]["mode"], "shadow"
            )

    def test_refuses_first_build_without_full_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            _discovery_fixture(discovery)
            with self.assertRaisesRegex(ValueError, "full-snapshot"):
                build_vnext_snapshot(
                    project_root=PROJECT_ROOT,
                    discovery_database=discovery,
                    legacy_kb_root=root / "legacy",
                    capture_root=root / "captures",
                    native_root=root / "native",
                    output_dir=root / "vnext",
                    full_snapshot=False,
                )


if __name__ == "__main__":
    unittest.main()
