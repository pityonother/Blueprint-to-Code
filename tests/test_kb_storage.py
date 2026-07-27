from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.snapshot import (  # noqa: E402
    build_vnext_snapshot,
)
from blueprint_translator.kb_vnext.quality_gates import (  # noqa: E402
    _effective_resolution_metrics,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    CORE_SCHEMA_VERSION,
)
from blueprint_translator.evidence_schema import (  # noqa: E402
    make_asset_id,
    make_revision_id,
)
from blueprint_translator.asset_ledger import (  # noqa: E402
    metadata_fingerprint,
)


def _blueprint_capture_fixture(
    capture_root: Path,
) -> tuple[str, str, str, int, str]:
    asset_name = "BP_Base"
    object_path = "/Game/Test/BP_Base.BP_Base"
    asset_id = make_asset_id(object_path)
    parser = "uasset-graph-reader-evidence-v3"
    schema = "ark.blueprint.evidence.v2"
    source_path = "@memory/normalized_graph_facts"
    source_sha = "b" * 64
    asset_root = capture_root / asset_name
    asset_root.mkdir(parents=True)
    package_path = asset_root / "BP_Base.uasset"
    package_bytes = b"synthetic-storage-uasset"
    package_path.write_bytes(package_bytes)
    binary_path = f"binary/{package_path.name}"
    binary_sha = hashlib.sha256(package_bytes).hexdigest()
    source_hashes = {
        binary_path: binary_sha,
        source_path: source_sha,
    }
    compact = json.dumps(
        sorted(source_hashes.items()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(compact).hexdigest()
    revision = make_revision_id(
        source_hashes,
        parser_version=parser,
        schema_version=schema,
    )
    default_ref = f"bp://{asset_id}@{revision}/default/Count"
    evidence_root = asset_root / "evidence"
    evidence_root.mkdir(parents=True)
    connection = sqlite3.connect(evidence_root / "evidence.sqlite")
    connection.executescript(
        """
        CREATE TABLE asset_revisions(
            revision_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            object_path TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            uasset_path TEXT NOT NULL
        );
        CREATE TABLE class_defaults(
            default_ref TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL,
            name TEXT NOT NULL,
            type_name TEXT NOT NULL,
            value_json TEXT NOT NULL,
            value_codec TEXT NOT NULL,
            value_blob BLOB,
            confidence TEXT NOT NULL,
            source TEXT NOT NULL,
            extra_json TEXT NOT NULL
        );
        CREATE TABLE source_manifest(
            revision_id TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            source_kind TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO asset_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            revision,
            asset_id,
            asset_name,
            object_path,
            fingerprint,
            parser,
            schema,
            "2026-07-27T00:00:00+00:00",
            str(package_path),
        ),
    )
    connection.execute(
        "INSERT INTO class_defaults VALUES (?, ?, 'Count', 'IntProperty', '7', 'json', NULL, 'high', 'fixture', '{}')",
        (default_ref, revision),
    )
    connection.executemany(
        "INSERT INTO source_manifest VALUES (?, ?, ?, ?, ?)",
        [
            (
                revision,
                binary_path,
                binary_sha,
                len(package_bytes),
                "package_binary",
            ),
            (
                revision,
                source_path,
                source_sha,
                123,
                "in_memory_capture",
            ),
        ],
    )
    connection.commit()
    connection.close()
    (evidence_root / "manifest.json").write_text(
        json.dumps(
            {
                "asset_id": asset_id,
                "asset_name": asset_name,
                "object_path": object_path,
                "revision_id": revision,
                "source_fingerprint": fingerprint,
                "parser_version": parser,
                "schema": schema,
                "database": "evidence.sqlite",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    stat = package_path.stat()
    package_modified = datetime.fromtimestamp(
        stat.st_mtime,
        UTC,
    ).isoformat()
    package_fingerprint = metadata_fingerprint(
        uasset_size=stat.st_size,
        uasset_modified=package_modified,
    )
    return (
        revision,
        default_ref,
        package_fingerprint,
        stat.st_size,
        package_modified,
    )


def _discovery_fixture(
    path: Path,
    *,
    evidence_revision: str = "",
    evidence_ref: str = "",
    package_fingerprint: str = "sha-base",
    package_size: int = 100,
    package_modified: str = "2026-07-27T00:00:00+00:00",
) -> None:
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
            has_uasset INTEGER NOT NULL,
            has_uexp INTEGER NOT NULL,
            has_ubulk INTEGER NOT NULL,
            file_size_total INTEGER NOT NULL,
            source_fingerprint TEXT NOT NULL,
            source_modified TEXT NOT NULL,
            capture_exists INTEGER NOT NULL,
            evidence_revision TEXT NOT NULL,
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
        CREATE TABLE default_property_surface(
            surface_id TEXT PRIMARY KEY,
            asset_object_path TEXT NOT NULL,
            property_name TEXT NOT NULL,
            property_type TEXT NOT NULL,
            has_value INTEGER NOT NULL,
            value_status TEXT NOT NULL,
            value_fingerprint TEXT NOT NULL,
            source_evidence_id TEXT NOT NULL,
            confidence TEXT NOT NULL
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
            1,
            0,
            0,
            package_size,
            package_fingerprint,
            package_modified,
            1,
            evidence_revision,
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
            "/Game/Test/BP_Child.BP_Child",
            "/Game/Test/BP_Child",
            "BP_Child",
            "/Script/Engine.Blueprint",
            "Blueprint",
            "/Game/Test/BP_Child.BP_Child_C",
            "/Game/Test/BP_Base.BP_Base_C",
            "/Script/Engine.Actor",
            "/Game",
            "base",
            1,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            120,
            "sha-child",
            "2026-07-27T00:00:00+00:00",
            0,
            "",
            "NOT_MEASURED",
            "NOT_MEASURED",
            "EXTRACTED",
            "HIGH",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            None,
            "NOT_MEASURED",
            None,
            "NOT_MEASURED",
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
            1,
            0,
            0,
            200,
            "sha-texture",
            "2026-07-27T00:00:00+00:00",
            0,
            "",
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
        + ",".join("?" for _ in range(39))
        + ")",
        assets,
    )
    if evidence_ref:
        connection.execute(
            """
            INSERT INTO default_property_surface VALUES (
                'surface-count', '/Game/Test/BP_Base.BP_Base',
                'Count', 'IntProperty', 1,
                'CONFIRMED_FINGERPRINT_ONLY', 'fallback-fingerprint',
                ?, 'HIGH'
            )
            """,
            (evidence_ref,),
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
    connection.executemany(
        """
        INSERT INTO class_edges VALUES (?, ?, ?, 'fixture', 'HIGH')
        """,
        [
            (
                "/Game/Test/BP_Child.BP_Child_C",
                "/Game/Test/BP_Base.BP_Base_C",
                "blueprint_parent",
            ),
            (
                "/Game/Test/BP_Base.BP_Base_C",
                "/Script/Engine.Actor",
                "native_parent",
            ),
            (
                "/Script/Engine.Actor",
                "/Script/CoreUObject.Object",
                "native_parent",
            ),
        ],
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
            (
                revision,
                evidence_ref,
                package_fingerprint,
                package_size,
                package_modified,
            ) = _blueprint_capture_fixture(root / "captures")
            _discovery_fixture(
                discovery,
                evidence_revision=revision,
                evidence_ref=evidence_ref,
                package_fingerprint=package_fingerprint,
                package_size=package_size,
                package_modified=package_modified,
            )
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
            self.assertEqual(
                result["counts"]["core"]["blueprintPackageVerifiedAssets"],
                1,
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
                self.assertEqual(
                    core.execute(
                        """
                        SELECT value_kind, value_integer, status
                        FROM facts
                        WHERE fact_type='DECLARED_DEFAULT'
                          AND fact_name='Count'
                        """
                    ).fetchall(),
                    [("INTEGER", 7, "CONFIRMED")],
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT COUNT(*)
                        FROM source_revisions
                        WHERE source_kind='blueprint_evidence'
                          AND source_uri LIKE 'bp://%'
                        """
                    ).fetchone()[0],
                    1,
                )
                metadata = dict(
                    core.execute("SELECT key, value FROM metadata")
                )
                self.assertEqual(
                    metadata["schema_version"], CORE_SCHEMA_VERSION
                )
                self.assertEqual(CORE_SCHEMA_VERSION, "ark-kb-core/v3")
                child_entity_id = core.execute(
                    """
                    SELECT entity_id FROM entities
                    WHERE canonical_uri='/Game/Test/BP_Child.BP_Child'
                    """
                ).fetchone()[0]
                base_entity_id = core.execute(
                    """
                    SELECT entity_id FROM entities
                    WHERE canonical_uri='/Game/Test/BP_Base.BP_Base'
                    """
                ).fetchone()[0]
                child_class_id = core.execute(
                    """
                    SELECT class_id FROM classes
                    WHERE class_path='/Game/Test/BP_Child.BP_Child_C'
                    """
                ).fetchone()[0]
                base_class_id = core.execute(
                    """
                    SELECT class_id FROM classes
                    WHERE class_path='/Game/Test/BP_Base.BP_Base_C'
                    """
                ).fetchone()[0]
                actor_class_id = core.execute(
                    """
                    SELECT class_id FROM classes
                    WHERE class_path='/Script/Engine.Actor'
                    """
                ).fetchone()[0]
                object_class_id = core.execute(
                    """
                    SELECT class_id FROM classes
                    WHERE class_path='/Script/CoreUObject.Object'
                    """
                ).fetchone()[0]
                self.assertEqual(
                    core.execute(
                        """
                        SELECT depth, path_status
                        FROM class_closure
                        WHERE ancestor_class_id=?
                          AND descendant_class_id=?
                        """,
                        (object_class_id, child_class_id),
                    ).fetchone(),
                    (3, "CONFIRMED"),
                )
                effective = core.execute(
                    """
                    SELECT
                        effective.fact_id,
                        effective.inherited_from_entity_id,
                        effective.resolution_chain_json,
                        effective.resolution_status,
                        fact.value_kind,
                        fact.value_integer
                    FROM effective_facts AS effective
                    JOIN facts AS fact ON fact.fact_id=effective.fact_id
                    WHERE effective.entity_id=?
                      AND effective.fact_type='EFFECTIVE_DEFAULT'
                      AND effective.fact_name='Count'
                    """,
                    (child_entity_id,),
                ).fetchone()
                self.assertIsNotNone(effective)
                self.assertEqual(effective[1], base_entity_id)
                self.assertEqual(effective[3:], ("RESOLVED", "INTEGER", 7))
                selected_edge_id = core.execute(
                    """
                    SELECT evidence_id
                    FROM class_edges
                    WHERE child_class_id=? AND parent_class_id=?
                      AND edge_kind='blueprint_parent'
                    """,
                    (child_class_id, base_class_id),
                ).fetchone()[0]
                base_to_actor_edge_id = core.execute(
                    """
                    SELECT evidence_id
                    FROM class_edges
                    WHERE child_class_id=? AND parent_class_id=?
                      AND edge_kind='native_parent'
                    """,
                    (base_class_id, actor_class_id),
                ).fetchone()[0]
                native_source = core.execute(
                    """
                    SELECT
                        revision.source_kind,
                        revision.source_uri,
                        revision.source_fingerprint,
                        revision.freshness_status
                    FROM classes AS class
                    JOIN source_revisions AS revision
                      ON revision.revision_id=class.source_revision_id
                    WHERE class.class_id=?
                    """,
                    (actor_class_id,),
                ).fetchone()
                self.assertEqual(
                    json.loads(effective[2]),
                    {
                        "schema": "ark-kb-effective-path/v1",
                        "startClassId": child_class_id,
                        "declaredOnClassId": base_class_id,
                        "declaredOnEntityId": base_entity_id,
                        "overrideDepth": 1,
                        "classes": [child_class_id, base_class_id],
                        "edges": [
                            {
                                "childClassId": child_class_id,
                                "parentClassId": base_class_id,
                                "edgeKind": "blueprint_parent",
                                "evidenceIds": [selected_edge_id],
                                "status": "CONFIRMED",
                            }
                        ],
                        "nativeRootProof": {
                            "schema": "ark-kb-native-root-proof/v1",
                            "startClassId": child_class_id,
                            "rootClassId": actor_class_id,
                            "classes": [
                                child_class_id,
                                base_class_id,
                                actor_class_id,
                            ],
                            "edges": [
                                {
                                    "childClassId": child_class_id,
                                    "parentClassId": base_class_id,
                                    "edgeKind": "blueprint_parent",
                                    "evidenceIds": [selected_edge_id],
                                    "status": "CONFIRMED",
                                },
                                {
                                    "childClassId": base_class_id,
                                    "parentClassId": actor_class_id,
                                    "edgeKind": "native_parent",
                                    "evidenceIds": [base_to_actor_edge_id],
                                    "status": "CONFIRMED",
                                },
                            ],
                            "sourceRevision": {
                                "sourceKind": native_source[0],
                                "sourceUri": native_source[1],
                                "sourceFingerprint": native_source[2],
                                "freshnessStatus": native_source[3],
                            },
                        },
                    },
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT
                            candidate_fact_id, declared_on_entity_id,
                            inheritance_depth, path_status, selected,
                            rejection_reason
                        FROM effective_fact_candidates
                        WHERE entity_id=?
                          AND fact_type='EFFECTIVE_DEFAULT'
                          AND fact_name='Count'
                        """,
                        (child_entity_id,),
                    ).fetchone(),
                    (
                        effective[0],
                        base_entity_id,
                        1,
                        "CONFIRMED",
                        1,
                        "",
                    ),
                )
                self.assertEqual(
                    core.execute(
                        "SELECT COUNT(*) FROM effective_fact_candidates"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT DISTINCT revisions.freshness_status
                        FROM effective_facts AS effective
                        JOIN fact_evidence AS evidence
                          ON evidence.fact_id=effective.fact_id
                        JOIN source_revisions AS revisions
                          ON revisions.revision_id=evidence.source_revision_id
                        WHERE effective.entity_id=?
                        """,
                        (child_entity_id,),
                    ).fetchall(),
                    [("FRESH",)],
                )
                valid_resolution = _effective_resolution_metrics(core)
                self.assertTrue(valid_resolution["consistent"])
                self.assertGreater(
                    int(valid_resolution["dependencyRows"]),
                    0,
                )
                core.execute(
                    """
                    UPDATE effective_facts
                    SET resolution_chain_json='{}'
                    WHERE entity_id=? AND fact_name='Count'
                    """,
                    (child_entity_id,),
                )
                invalid_resolution = _effective_resolution_metrics(core)
                self.assertFalse(invalid_resolution["consistent"])
                self.assertIn(
                    "unsupported envelope",
                    str(invalid_resolution["error"]),
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
            for database_name in (
                "catalog",
                "core",
                "search",
                "cache",
            ):
                source = sqlite3.connect(
                    output / f"{database_name}.sqlite"
                )
                replay = sqlite3.connect(":memory:")
                try:
                    replay.executescript(
                        (
                            output
                            / "manifests"
                            / f"{database_name}_schema.sql"
                        ).read_text(encoding="utf-8")
                    )

                    def schema_objects(
                        connection: sqlite3.Connection,
                    ) -> set[tuple[str, str, str, str]]:
                        return {
                            (
                                str(row[0]),
                                str(row[1]),
                                str(row[2]),
                                " ".join(str(row[3] or "").split()),
                            )
                            for row in connection.execute(
                                """
                                SELECT type, name, tbl_name, sql
                                FROM sqlite_schema
                                WHERE name NOT LIKE 'sqlite_%'
                                """
                            )
                        }

                    self.assertEqual(
                        schema_objects(replay),
                        schema_objects(source),
                        database_name,
                    )
                finally:
                    replay.close()
                    source.close()

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
