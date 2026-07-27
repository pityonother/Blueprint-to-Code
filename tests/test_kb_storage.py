from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.snapshot import (  # noqa: E402
    build_vnext_snapshot,
    resolve_current_snapshot,
)
import blueprint_translator.kb_vnext.snapshot as snapshot_module  # noqa: E402
from blueprint_translator.kb_vnext import (  # noqa: E402
    registrations as registrations_module,
)
from blueprint_translator.kb_vnext.quality_gates import (  # noqa: E402
    _effective_resolution_metrics,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    CORE_SCHEMA_VERSION,
    FULL_CORE_SCHEMA_SQL,
    _materialize_registration_edges,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.class_hierarchy import (  # noqa: E402
    class_hierarchy_source_fingerprint,
)
from blueprint_translator.evidence_schema import (  # noqa: E402
    make_asset_id,
    make_revision_id,
)
from blueprint_translator.asset_ledger import (  # noqa: E402
    metadata_fingerprint,
)
import update_ark_kb_vnext as update_module  # noqa: E402


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
            'existing-kb://fixture/1', 'HIGH',
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


def _snapshot_identity_for_inputs(
    *,
    project_root: Path,
    discovery_database: Path,
    legacy_kb_root: Path,
    capture_root: Path,
    native_root: Path,
    output_dir: Path,
    map_evidence_path: Path,
    generated_at: str = "2026-07-27T00:00:00+00:00",
) -> dict[str, object]:
    metrics = {
        "integrity": "ok",
        "foreignKeyViolations": 0,
    }
    with (
        patch.object(
            snapshot_module,
            "build_catalog_database",
            return_value={},
        ),
        patch.object(
            snapshot_module,
            "build_core_database",
            return_value={},
        ),
        patch.object(
            snapshot_module,
            "build_domain_projections",
            return_value={},
        ),
        patch.object(
            snapshot_module,
            "build_search_database",
            return_value={},
        ),
        patch.object(
            snapshot_module,
            "build_cache_database",
            return_value={},
        ),
        patch.object(
            snapshot_module,
            "database_metrics",
            return_value=metrics,
        ),
        patch.object(
            snapshot_module,
            "_evaluate_staged_quality_gates",
            side_effect=_fixture_staged_quality_report,
        ),
        patch.object(snapshot_module, "_promote_snapshot"),
    ):
        return build_vnext_snapshot(
            project_root=project_root,
            discovery_database=discovery_database,
            legacy_kb_root=legacy_kb_root,
            capture_root=capture_root,
            native_root=native_root,
            output_dir=output_dir,
            full_snapshot=True,
            generated_at=generated_at,
            map_evidence_path=map_evidence_path,
        )


def _fixture_staged_quality_report(
    *,
    staging: Path,
    **_kwargs: object,
) -> dict[str, object]:
    manifest = json.loads(
        (staging / "manifest.json").read_text(encoding="utf-8")
    )
    return {
        "schema": "ark-kb-quality-gates/v1",
        "buildId": manifest["buildId"],
        "summary": {
            "passed": 0,
            "failed": 1,
            "cutoverEligible": False,
        },
        "benchmark": {},
    }


class KnowledgeStorageTests(unittest.TestCase):
    def test_registration_edges_deduplicate_collapsed_semantic_relations(
        self,
    ) -> None:
        core = sqlite3.connect(":memory:")
        core.executescript(FULL_CORE_SCHEMA_SQL)
        core.executemany(
            """
            INSERT INTO source_revisions VALUES (
                ?, 'fixture', ?, ?, 'test', 'v1',
                '2026-07-28T00:00:00Z', 'FRESH'
            )
            """,
            [
                (1, "fixture://edge", "a" * 64),
                (2, "fixture://domain", "b" * 64),
            ],
        )
        core.executemany(
            """
            INSERT INTO entities(
                entity_id, canonical_uri, entity_kind, package_id,
                display_name, internal_name, status, confidence
            ) VALUES (?, ?, 'BLUEPRINT_ASSET', NULL, ?, ?, 'CONFIRMED', 'HIGH')
            """,
            [
                (1, "/Game/Test/Owner.Owner", "Owner", "Owner"),
                (2, "/Game/Test/Damage.Damage", "Damage", "Damage"),
            ],
        )
        core.executemany(
            """
            INSERT INTO typed_registrations VALUES (
                ?, '/Game/Test/Owner.Owner',
                '/Game/Test/Damage.Damage', ?,
                'CheatDestroyFoliageDamageType',
                'bp://fixture/damage', 'GLOBAL', 'LOW', 'CANDIDATE',
                1, 'test', 'fixture'
            )
            """,
            [
                ("registration-a", "global_asset_reference"),
                ("registration-b", "damage_type_registration"),
            ],
        )
        try:
            edge_count, domain_count = _materialize_registration_edges(
                core,
                load_ontology(PROJECT_ROOT / "ontology"),
                edge_source_revision_id=1,
                domain_source_revision_id=2,
            )
            rows = core.execute(
                """
                SELECT edge_type, source_property, evidence_uri
                FROM edges
                """
            ).fetchall()
        finally:
            core.close()

        self.assertEqual(edge_count, 1)
        self.assertEqual(domain_count, 2)
        self.assertEqual(
            rows,
            [
                (
                    "USES_DAMAGE_TYPE",
                    "CheatDestroyFoliageDamageType",
                    "bp://fixture/damage",
                )
            ],
        )

    def test_full_build_binds_manifest_for_cache_hit_and_runtime_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            output = root / "vnext"
            runtime_root = root / "runtime_observations"
            runtime_root.mkdir()
            runtime_observation = runtime_root / "fixture.json"
            runtime_observation.write_text(
                '{"status":"observed"}',
                encoding="utf-8",
            )
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
                runtime_root=runtime_root,
                output_dir=output,
                full_snapshot=True,
                generated_at="2026-07-27T00:00:00+00:00",
            )
            published = resolve_current_snapshot(output)
            update_paths = update_module.UpdatePaths(
                discovery_database=discovery,
                capture_root=root / "captures",
                native_root=root / "native",
                runtime_root=runtime_root,
                legacy_kb_root=root / "legacy",
                map_evidence_catalog=root / "map.json",
                output=output,
            )
            bound_manifest = update_module.load_current_source_manifest(
                update_paths
            )
            self.assertIsNotNone(bound_manifest)
            incremental = published.manifest["incrementalUpdate"]
            self.assertEqual(
                incremental["sourceManifestFingerprint"],
                bound_manifest.fingerprint,
            )
            self.assertEqual(
                result["sourceManifestFingerprint"],
                bound_manifest.fingerprint,
            )
            semantic_uris = {
                entry.source_uri
                for entry in bound_manifest.entries
                if entry.source_kind == "SEMANTIC_INPUT"
            }
            self.assertEqual(
                semantic_uris,
                {
                    *(
                        f"semantic-input://{key}"
                        for key in snapshot_module.SNAPSHOT_SEMANTIC_INPUT_KEYS
                    ),
                    "semantic-input://runtimeObservations",
                },
            )
            self.assertEqual(
                sum(
                    entry.source_kind == "BLUEPRINT_EVIDENCE"
                    for entry in bound_manifest.entries
                ),
                1,
            )
            encoded_incremental = json.dumps(
                incremental,
                sort_keys=True,
            )
            self.assertNotIn(str(root), encoded_incremental)
            self.assertNotIn("C:\\", encoded_incremental)
            with patch.object(
                update_module,
                "_unavailable_stage",
                side_effect=AssertionError(
                    "unchanged update must not stage"
                ),
            ):
                unchanged = update_module.run_incremental_update(
                    update_paths
                )
                self.assertEqual(unchanged["status"], "cache_hit")
                self.assertTrue(unchanged["cacheHit"])
                runtime_observation.write_text(
                    '{"status":"changed"}',
                    encoding="utf-8",
                )
                changed = update_module.run_incremental_update(update_paths)
            self.assertEqual(
                changed["gapCodes"],
                ["NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED"],
            )
            self.assertTrue(changed["fullRebuildRequired"])
            self.assertFalse(changed["published"])

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
            discovery_connection = sqlite3.connect(discovery)
            try:
                expected_class_fingerprint = (
                    class_hierarchy_source_fingerprint(
                        discovery_connection
                    )
                )
            finally:
                discovery_connection.close()
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
            published = resolve_current_snapshot(output)
            snapshot_root = published.snapshot_dir
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
                self.assertTrue((snapshot_root / name).is_file())
                self.assertFalse((output / name).exists())
                self.assertEqual(
                    result["databases"][name]["integrity"], "ok"
                )
                self.assertEqual(
                    result["databases"][name]["foreignKeyViolations"], 0
                )
                self.assertEqual(
                    result["databases"][name]["sha256"],
                    hashlib.sha256(
                        (snapshot_root / name).read_bytes()
                    ).hexdigest(),
                )
                database = sqlite3.connect(snapshot_root / name)
                try:
                    database_metadata = dict(
                        database.execute(
                            "SELECT key, value FROM metadata"
                        )
                    )
                finally:
                    database.close()
                self.assertEqual(
                    database_metadata["snapshot_build_id"],
                    result["buildId"],
                )
                self.assertEqual(
                    database_metadata["snapshot_source_fingerprint"],
                    result["sourceSha256"],
                )
            catalog = sqlite3.connect(snapshot_root / "catalog.sqlite")
            core = sqlite3.connect(snapshot_root / "core.sqlite")
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
                base_registration_owner_id = core.execute(
                    """
                    SELECT entity_id FROM entities
                    WHERE canonical_uri='/Game/Test/BP_Base.BP_Base'
                    """
                ).fetchone()[0]
                base_registration_roles = {
                    str(row[0])
                    for row in core.execute(
                        """
                        SELECT role FROM knowledge_roles
                        WHERE entity_id=?
                        """,
                        (base_registration_owner_id,),
                    )
                }
                self.assertNotIn(
                    "registration_owner",
                    base_registration_roles,
                )
                self.assertNotIn(
                    "global_system_hub",
                    base_registration_roles,
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT status, confidence
                        FROM typed_registrations
                        WHERE owner_uri='/Game/Test/BP_Base.BP_Base'
                        """
                    ).fetchall(),
                    [("LEGACY_UNVERIFIED", "LOW")],
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT edge_type, status, confidence
                        FROM edges
                        WHERE source_entity_id=?
                          AND source_property='Texture'
                        """,
                        (base_registration_owner_id,),
                    ).fetchall(),
                    [
                        (
                            "REFERENCES_OBJECT",
                            "LEGACY_UNVERIFIED",
                            "LOW",
                        )
                    ],
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT DISTINCT status, confidence
                        FROM domain_memberships
                        WHERE membership_kind='TYPED_REGISTRATION'
                        """
                    ).fetchall(),
                    [("LEGACY_UNVERIFIED", "LOW")],
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT DISTINCT revision.source_kind
                        FROM knowledge_roles AS role
                        JOIN source_revisions AS revision
                          ON revision.revision_id=
                             role.source_revision_id
                        """
                    ).fetchall(),
                    [("role_classifier",)],
                )
                role_revision_ids = {
                    int(row[0])
                    for row in core.execute(
                        """
                        SELECT DISTINCT source_revision_id
                        FROM knowledge_roles
                        """
                    )
                }
                self.assertEqual(
                    core.execute(
                        """
                        SELECT COUNT(*)
                        FROM domain_memberships AS membership
                        JOIN source_revisions AS revision
                          ON revision.revision_id=
                             membership.source_revision_id
                        WHERE revision.source_kind<>'ontology'
                           OR revision.freshness_status<>'FRESH'
                        """
                    ).fetchone()[0],
                    0,
                )
                domain_revision_ids = {
                    int(row[0])
                    for row in core.execute(
                        """
                        SELECT DISTINCT source_revision_id
                        FROM domain_memberships
                        """
                    )
                }
                self.assertEqual(
                    core.execute(
                        """
                        SELECT COUNT(*)
                        FROM packages AS package
                        JOIN source_revisions AS revision
                          ON revision.revision_id=
                             package.current_revision_id
                        WHERE revision.source_kind='asset_package'
                        """
                    ).fetchone()[0],
                    core.execute(
                        "SELECT COUNT(*) FROM packages"
                    ).fetchone()[0],
                )
                package_revision_ids = {
                    int(row[0])
                    for row in core.execute(
                        """
                        SELECT DISTINCT current_revision_id
                        FROM packages
                        """
                    )
                }
                self.assertNotIn(1, package_revision_ids)
                self.assertTrue(role_revision_ids)
                self.assertTrue(domain_revision_ids)
                self.assertTrue(package_revision_ids)
                self.assertTrue(
                    role_revision_ids.isdisjoint(domain_revision_ids)
                )
                self.assertTrue(
                    role_revision_ids.isdisjoint(package_revision_ids)
                )
                self.assertTrue(
                    domain_revision_ids.isdisjoint(package_revision_ids)
                )
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
                self.assertEqual(CORE_SCHEMA_VERSION, "ark-kb-core/v4")
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
                class_revision_rows = core.execute(
                    """
                    SELECT DISTINCT
                        revision.source_kind,
                        revision.source_uri,
                        revision.source_fingerprint,
                        revision.producer_version,
                        revision.schema_version,
                        revision.generated_at,
                        revision.freshness_status
                    FROM classes AS class
                    JOIN source_revisions AS revision
                      ON revision.revision_id=class.source_revision_id
                    """
                ).fetchall()
                self.assertTrue(class_revision_rows)
                self.assertNotIn(
                    "discovery",
                    {str(row[0]) for row in class_revision_rows},
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT COUNT(*)
                        FROM classes AS class
                        LEFT JOIN source_revisions AS revision
                          ON revision.revision_id=class.source_revision_id
                        WHERE revision.revision_id IS NULL
                           OR revision.source_kind='discovery'
                           OR trim(revision.source_kind)=''
                           OR trim(revision.source_uri)=''
                           OR trim(revision.source_fingerprint)=''
                           OR trim(revision.producer_version)=''
                           OR trim(revision.schema_version)=''
                           OR trim(revision.generated_at)=''
                           OR revision.freshness_status<>'FRESH'
                        """
                    ).fetchone()[0],
                    0,
                )
                self.assertTrue(
                    all(
                        all(str(value).strip() for value in row)
                        and row[-1] == "FRESH"
                        for row in class_revision_rows
                    )
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT revision.source_kind, revision.source_uri
                        FROM classes AS class
                        JOIN source_revisions AS revision
                          ON revision.revision_id=class.source_revision_id
                        WHERE class.class_id=?
                        """,
                        (actor_class_id,),
                    ).fetchone(),
                    (
                        "class_hierarchy",
                        "class-hierarchy://ark/"
                        "ark-kb-class-hierarchy/v2",
                    ),
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT DISTINCT source_fingerprint
                        FROM source_revisions
                        WHERE source_kind='class_hierarchy'
                          AND source_uri=?
                        """,
                        (
                            "class-hierarchy://ark/"
                            "ark-kb-class-hierarchy/v2",
                        ),
                    ).fetchall(),
                    [(expected_class_fingerprint,)],
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT revision.source_kind, revision.source_uri
                        FROM classes AS class
                        JOIN source_revisions AS revision
                          ON revision.revision_id=class.source_revision_id
                        WHERE class.class_id=?
                        """,
                        (child_class_id,),
                    ).fetchone(),
                    (
                        "asset_package",
                        "package:///Game/Test/BP_Child",
                    ),
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT revision.source_kind, revision.source_uri
                        FROM class_edges AS edge
                        JOIN source_revisions AS revision
                          ON revision.revision_id=edge.source_revision_id
                        WHERE edge.child_class_id=?
                          AND edge.parent_class_id=?
                          AND edge.edge_kind='blueprint_parent'
                        """,
                        (child_class_id, base_class_id),
                    ).fetchone(),
                    (
                        "asset_package",
                        "package:///Game/Test/BP_Child",
                    ),
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT DISTINCT
                            revision.source_kind,
                            revision.source_uri
                        FROM class_edges AS edge
                        JOIN classes AS child
                          ON child.class_id=edge.child_class_id
                        JOIN classes AS parent
                          ON parent.class_id=edge.parent_class_id
                        JOIN source_revisions AS revision
                          ON revision.revision_id=edge.source_revision_id
                        WHERE child.class_path=
                                '/Script/Engine.PrimaryDataAsset'
                          AND parent.class_path=
                                '/Script/Engine.DataAsset'
                          AND edge.edge_kind='native_parent'
                        """
                    ).fetchall(),
                    [
                        (
                            "class_hierarchy",
                            "class-hierarchy://ark/"
                            "ark-kb-class-hierarchy/v2",
                        )
                    ],
                )
                self.assertEqual(
                    core.execute(
                        """
                        SELECT COUNT(*)
                        FROM class_edges AS edge
                        LEFT JOIN source_revisions AS revision
                          ON revision.revision_id=edge.source_revision_id
                        WHERE revision.revision_id IS NULL
                           OR revision.source_kind='discovery'
                           OR trim(revision.source_kind)=''
                           OR trim(revision.source_uri)=''
                           OR trim(revision.source_fingerprint)=''
                           OR trim(revision.producer_version)=''
                           OR trim(revision.schema_version)=''
                           OR trim(revision.generated_at)=''
                           OR revision.freshness_status<>'FRESH'
                        """
                    ).fetchone()[0],
                    0,
                )
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
                        revision.producer_version,
                        revision.schema_version,
                        revision.generated_at,
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
                                "producerVersion": native_source[3],
                                "schemaVersion": native_source[4],
                                "generatedAt": native_source[5],
                                "freshnessStatus": native_source[6],
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
                published.manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema"], "ark-kb-vnext-snapshot/v1")
            self.assertEqual(
                manifest["cutover"]["mode"], "shadow"
            )
            quality = manifest["qualityGates"]
            quality_report = (
                snapshot_root / str(quality["reportUri"])
            )
            self.assertTrue(quality_report.is_file())
            self.assertEqual(
                quality["sha256"],
                hashlib.sha256(quality_report.read_bytes()).hexdigest(),
            )
            self.assertGreater(int(quality["failed"]), 0)
            self.assertFalse(bool(quality["cutoverEligible"]))
            for database_name in (
                "catalog",
                "core",
                "search",
                "cache",
            ):
                source = sqlite3.connect(
                    snapshot_root / f"{database_name}.sqlite"
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

    def test_snapshot_identity_covers_class_hierarchy_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "capture_root": root / "captures",
                "native_root": root / "native",
                "map_evidence_path": map_evidence,
            }
            with patch.object(
                snapshot_module,
                "class_hierarchy_contract_fingerprint",
                return_value="a" * 64,
            ):
                baseline = _snapshot_identity_for_inputs(
                    output_dir=root / "out-baseline",
                    **common,
                )
            with patch.object(
                snapshot_module,
                "class_hierarchy_contract_fingerprint",
                return_value="b" * 64,
            ):
                variant = _snapshot_identity_for_inputs(
                    output_dir=root / "out-variant",
                    **common,
                )

        self.assertEqual(
            variant["discoverySha256"],
            baseline["discoverySha256"],
        )
        self.assertNotEqual(
            variant["sourceSha256"],
            baseline["sourceSha256"],
        )
        self.assertNotEqual(variant["buildId"], baseline["buildId"])

    def test_snapshot_identity_covers_semantic_producer_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "capture_root": root / "captures",
                "native_root": root / "native",
                "map_evidence_path": map_evidence,
            }
            baseline = _snapshot_identity_for_inputs(
                output_dir=root / "out-baseline",
                **common,
            )
            added_rule = registrations_module.RegistrationRule(
                "fixture_contract_registration",
                ("FixtureContractProperty",),
                ("fixturecontract",),
                ("FIXTURE",),
            )
            with patch.object(
                registrations_module,
                "REGISTRATION_RULES",
                (*registrations_module.REGISTRATION_RULES, added_rule),
            ):
                variant = _snapshot_identity_for_inputs(
                    output_dir=root / "out-variant",
                    **common,
                )

        self.assertEqual(
            variant["discoverySha256"],
            baseline["discoverySha256"],
        )
        self.assertNotEqual(
            variant["sourceSha256"],
            baseline["sourceSha256"],
        )
        self.assertNotEqual(variant["buildId"], baseline["buildId"])

    def test_snapshot_semantic_input_registry_is_exact(self):
        self.assertEqual(
            snapshot_module.SNAPSHOT_SEMANTIC_INPUT_KEYS,
            frozenset(
                {
                    "discovery",
                    "captures",
                    "classHierarchyContract",
                    "semanticProducerContract",
                    "legacy",
                    "ontology",
                    "benchmarkGold",
                    "qualityGold",
                    "mapEvidence",
                    "nativeEvidence",
                }
            ),
        )

    def test_snapshot_identity_covers_native_evidence_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')
            native_root = root / "native"
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "capture_root": root / "captures",
                "native_root": native_root,
                "map_evidence_path": map_evidence,
            }
            with patch.object(
                snapshot_module,
                "native_evidence_input_sha256",
                return_value="a" * 64,
            ) as native_hash:
                baseline = _snapshot_identity_for_inputs(
                    output_dir=root / "out-baseline",
                    **common,
                )
                self.assertEqual(native_hash.call_count, 2)
                native_hash.assert_called_with(native_root.resolve())
            with patch.object(
                snapshot_module,
                "native_evidence_input_sha256",
                return_value="b" * 64,
            ) as native_hash:
                variant = _snapshot_identity_for_inputs(
                    output_dir=root / "out-variant",
                    **common,
                )
                self.assertEqual(native_hash.call_count, 2)
                native_hash.assert_called_with(native_root.resolve())

        self.assertEqual(
            variant["discoverySha256"],
            baseline["discoverySha256"],
        )
        self.assertNotEqual(
            variant["sourceSha256"],
            baseline["sourceSha256"],
        )
        self.assertNotEqual(variant["buildId"], baseline["buildId"])

    def test_snapshot_identity_is_path_free_for_missing_native_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "capture_root": root / "captures",
                "map_evidence_path": map_evidence,
            }
            baseline = _snapshot_identity_for_inputs(
                native_root=root / "missing-native-a",
                output_dir=root / "out-baseline",
                **common,
            )
            variant = _snapshot_identity_for_inputs(
                native_root=root / "missing-native-b",
                output_dir=root / "out-variant",
                **common,
            )

        self.assertEqual(
            variant["sourceSha256"],
            baseline["sourceSha256"],
        )
        self.assertEqual(variant["buildId"], baseline["buildId"])

    def test_snapshot_identity_covers_every_semantic_input_family(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_a = root / "map-a.json"
            map_b = root / "map-b.json"
            map_a.write_bytes(b'{"catalog":"a"}')
            map_b.write_bytes(b'{"catalog":"b"}')

            capture_a = root / "captures-a" / "BP_Test" / "evidence"
            capture_b = root / "captures-b" / "BP_Test" / "evidence"
            capture_a.mkdir(parents=True)
            capture_b.mkdir(parents=True)
            (capture_a / "evidence.sqlite").write_bytes(
                b"capture-evidence-a"
            )
            (capture_b / "evidence.sqlite").write_bytes(
                b"capture-evidence-b"
            )

            changed_project = root / "changed-project"
            shutil.copytree(
                PROJECT_ROOT / "ontology",
                changed_project / "ontology",
            )
            shutil.copytree(
                PROJECT_ROOT / "tests" / "fixtures",
                changed_project / "tests" / "fixtures",
            )
            changed_roles = (
                changed_project / "ontology" / "ark_roles.v1.json"
            )
            changed_roles.write_text(
                changed_roles.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
                newline="\n",
            )

            common = {
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "native_root": root / "native",
            }
            baseline = _snapshot_identity_for_inputs(
                project_root=PROJECT_ROOT,
                capture_root=root / "captures-a",
                output_dir=root / "out-baseline",
                map_evidence_path=map_a,
                **common,
            )
            variants = {
                "map catalog": _snapshot_identity_for_inputs(
                    project_root=PROJECT_ROOT,
                    capture_root=root / "captures-a",
                    output_dir=root / "out-map",
                    map_evidence_path=map_b,
                    **common,
                ),
                "capture evidence": _snapshot_identity_for_inputs(
                    project_root=PROJECT_ROOT,
                    capture_root=root / "captures-b",
                    output_dir=root / "out-capture",
                    map_evidence_path=map_a,
                    **common,
                ),
                "ontology": _snapshot_identity_for_inputs(
                    project_root=changed_project,
                    capture_root=root / "captures-a",
                    output_dir=root / "out-ontology",
                    map_evidence_path=map_a,
                    **common,
                ),
            }

        for input_family, variant in variants.items():
            with self.subTest(input_family=input_family):
                self.assertEqual(
                    variant["discoverySha256"],
                    baseline["discoverySha256"],
                )
                self.assertNotEqual(
                    variant["sourceSha256"],
                    baseline["sourceSha256"],
                )
                self.assertNotEqual(
                    variant["buildId"],
                    baseline["buildId"],
                )

    def test_snapshot_build_rejects_input_changed_after_initial_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            captures = root / "captures"
            (
                revision,
                evidence_ref,
                package_fingerprint,
                package_size,
                package_modified,
            ) = _blueprint_capture_fixture(captures)
            _discovery_fixture(
                discovery,
                evidence_revision=revision,
                evidence_ref=evidence_ref,
                package_fingerprint=package_fingerprint,
                package_size=package_size,
                package_modified=package_modified,
            )
            output = root / "vnext"
            original_capture_hash = (
                snapshot_module._capture_semantic_inputs_sha256
            )
            mutated = False

            def hash_captures_then_mutate(path: Path) -> str:
                nonlocal mutated
                digest = original_capture_hash(path)
                if not mutated:
                    connection = sqlite3.connect(discovery)
                    try:
                        connection.execute(
                            """
                            UPDATE assets
                            SET asset_name='BP_Child_MUTATED'
                            WHERE object_path=
                                  '/Game/Test/BP_Child.BP_Child'
                            """
                        )
                        connection.commit()
                    finally:
                        connection.close()
                    mutated = True
                return digest

            with (
                patch.object(
                    snapshot_module,
                    "_capture_semantic_inputs_sha256",
                    side_effect=hash_captures_then_mutate,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "semantic inputs changed during build.*discovery",
                ),
            ):
                build_vnext_snapshot(
                    project_root=PROJECT_ROOT,
                    discovery_database=discovery,
                    legacy_kb_root=root / "legacy",
                    capture_root=captures,
                    native_root=root / "native",
                    output_dir=output,
                    full_snapshot=True,
                    generated_at="2026-07-27T00:00:00+00:00",
                )

            self.assertFalse(
                (output / "manifests" / "current.json").exists()
            )
            self.assertFalse((output / "core.sqlite").exists())

    def test_snapshot_rechecks_every_semantic_input_before_promotion(self):
        baseline = {
            key: "a" * 64
            for key in snapshot_module.SNAPSHOT_SEMANTIC_INPUT_KEYS
        }
        metrics = {
            "integrity": "ok",
            "foreignKeyViolations": 0,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            for changed_key in sorted(
                snapshot_module.SNAPSHOT_SEMANTIC_INPUT_KEYS
            ):
                changed = {**baseline, changed_key: "b" * 64}
                with (
                    self.subTest(changed_key=changed_key),
                    patch.object(
                        snapshot_module,
                        "_snapshot_semantic_input_hashes",
                        side_effect=(baseline, changed),
                    ),
                    patch.object(
                        snapshot_module,
                        "build_catalog_database",
                        return_value={},
                    ),
                    patch.object(
                        snapshot_module,
                        "build_core_database",
                        return_value={},
                    ),
                    patch.object(
                        snapshot_module,
                        "build_domain_projections",
                        return_value={},
                    ),
                    patch.object(
                        snapshot_module,
                        "build_search_database",
                        return_value={},
                    ),
                    patch.object(
                        snapshot_module,
                        "build_cache_database",
                        return_value={},
                    ),
                    patch.object(
                        snapshot_module,
                        "database_metrics",
                        return_value=metrics,
                    ),
                    patch.object(
                        snapshot_module,
                        "_evaluate_staged_quality_gates",
                        side_effect=_fixture_staged_quality_report,
                    ),
                    patch.object(snapshot_module, "_promote_snapshot"),
                    self.assertRaisesRegex(RuntimeError, changed_key),
                ):
                    build_vnext_snapshot(
                        project_root=PROJECT_ROOT,
                        discovery_database=discovery,
                        legacy_kb_root=root / "legacy",
                        capture_root=root / "captures",
                        native_root=root / "native",
                        output_dir=root / f"out-{changed_key}",
                        full_snapshot=True,
                        generated_at="2026-07-27T00:00:00+00:00",
                    )

    def test_snapshot_generated_at_is_strict_and_normalized_to_utc(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "capture_root": root / "captures",
                "native_root": root / "native",
                "map_evidence_path": map_evidence,
            }

            normalized = _snapshot_identity_for_inputs(
                output_dir=root / "out-normalized",
                generated_at="2026-07-27T08:30:00+08:00",
                **common,
            )
            with self.assertRaisesRegex(
                ValueError,
                "RFC3339",
            ):
                _snapshot_identity_for_inputs(
                    output_dir=root / "out-invalid",
                    generated_at="not-a-time",
                    **common,
                )

        self.assertTrue(
            str(normalized["buildId"]).startswith("20260727T003000-")
        )
        self.assertEqual(
            snapshot_module.normalize_snapshot_generated_at(
                "2026-07-27T08:30:00+08:00"
            ),
            "2026-07-27T00:30:00+00:00",
        )

    def test_snapshot_identity_covers_capture_manifest_and_package_binary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')

            baseline_captures = root / "captures-baseline"
            _blueprint_capture_fixture(baseline_captures)
            evidence_database = (
                baseline_captures
                / "BP_Base"
                / "evidence"
                / "evidence.sqlite"
            )
            evidence = sqlite3.connect(evidence_database)
            try:
                evidence.execute(
                    "UPDATE asset_revisions SET uasset_path=?",
                    ("BP_Base.uasset",),
                )
                evidence.commit()
            finally:
                evidence.close()
            manifest_captures = root / "captures-manifest"
            package_captures = root / "captures-package"
            shutil.copytree(baseline_captures, manifest_captures)
            shutil.copytree(baseline_captures, package_captures)

            manifest_path = (
                manifest_captures
                / "BP_Base"
                / "evidence"
                / "manifest.json"
            )
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            manifest["parser_version"] = "tampered-parser-version"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True),
                encoding="utf-8",
            )
            (
                package_captures / "BP_Base" / "BP_Base.uasset"
            ).write_bytes(b"changed-package-binary")

            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "native_root": root / "native",
                "map_evidence_path": map_evidence,
            }
            baseline = _snapshot_identity_for_inputs(
                capture_root=baseline_captures,
                output_dir=root / "out-baseline",
                **common,
            )
            variants = {
                "capture manifest": _snapshot_identity_for_inputs(
                    capture_root=manifest_captures,
                    output_dir=root / "out-manifest",
                    **common,
                ),
                "package binary": _snapshot_identity_for_inputs(
                    capture_root=package_captures,
                    output_dir=root / "out-package",
                    **common,
                ),
            }

        for input_kind, variant in variants.items():
            with self.subTest(input_kind=input_kind):
                self.assertNotEqual(
                    variant["sourceSha256"],
                    baseline["sourceSha256"],
                )
                self.assertNotEqual(
                    variant["buildId"],
                    baseline["buildId"],
                )

    def test_capture_identity_is_portable_across_absolute_package_roots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture_a = root / "captures-a"
            capture_b = root / "captures-b"
            _blueprint_capture_fixture(capture_a)
            _blueprint_capture_fixture(capture_b)
            evidence_a = (
                capture_a
                / "BP_Base"
                / "evidence"
                / "evidence.sqlite"
            )
            evidence_b = (
                capture_b
                / "BP_Base"
                / "evidence"
                / "evidence.sqlite"
            )
            self.assertNotEqual(
                hashlib.sha256(evidence_a.read_bytes()).hexdigest(),
                hashlib.sha256(evidence_b.read_bytes()).hexdigest(),
            )

            digest_a = (
                snapshot_module._capture_semantic_inputs_sha256(
                    capture_a
                )
            )
            digest_b = (
                snapshot_module._capture_semantic_inputs_sha256(
                    capture_b
                )
            )
            self.assertEqual(digest_b, digest_a)

            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "native_root": root / "native",
                "map_evidence_path": map_evidence,
            }
            result_a = _snapshot_identity_for_inputs(
                capture_root=capture_a,
                output_dir=root / "out-a",
                **common,
            )
            result_b = _snapshot_identity_for_inputs(
                capture_root=capture_b,
                output_dir=root / "out-b",
                **common,
            )

        self.assertEqual(result_b["sourceSha256"], result_a["sourceSha256"])
        self.assertEqual(result_b["buildId"], result_a["buildId"])

    def test_capture_semantic_identity_changes_with_evidence_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture_a = root / "captures-a"
            capture_b = root / "captures-b"
            _blueprint_capture_fixture(capture_a)
            shutil.copytree(capture_a, capture_b)
            evidence_b = sqlite3.connect(
                capture_b
                / "BP_Base"
                / "evidence"
                / "evidence.sqlite"
            )
            try:
                evidence_b.execute(
                    """
                    UPDATE class_defaults
                    SET value_json='8'
                    WHERE name='Count'
                    """
                )
                evidence_b.commit()
            finally:
                evidence_b.close()

            digest_a = (
                snapshot_module._capture_semantic_inputs_sha256(
                    capture_a
                )
            )
            digest_b = (
                snapshot_module._capture_semantic_inputs_sha256(
                    capture_b
                )
            )
            self.assertNotEqual(digest_b, digest_a)

            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "native_root": root / "native",
                "map_evidence_path": map_evidence,
            }
            result_a = _snapshot_identity_for_inputs(
                capture_root=capture_a,
                output_dir=root / "out-a",
                **common,
            )
            result_b = _snapshot_identity_for_inputs(
                capture_root=capture_b,
                output_dir=root / "out-b",
                **common,
            )

        self.assertNotEqual(result_b["sourceSha256"], result_a["sourceSha256"])
        self.assertNotEqual(result_b["buildId"], result_a["buildId"])

    def test_touching_verified_package_preserves_identity_and_ingestion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captures_a = root / "captures-a"
            (
                revision,
                evidence_ref,
                package_fingerprint,
                package_size,
                package_modified,
            ) = _blueprint_capture_fixture(captures_a)
            evidence_a = sqlite3.connect(
                captures_a
                / "BP_Base"
                / "evidence"
                / "evidence.sqlite"
            )
            try:
                evidence_a.execute(
                    "UPDATE asset_revisions SET uasset_path=?",
                    ("BP_Base.uasset",),
                )
                evidence_a.commit()
            finally:
                evidence_a.close()
            captures_b = root / "captures-b"
            shutil.copytree(captures_a, captures_b)
            package_b = captures_b / "BP_Base" / "BP_Base.uasset"
            stat_b = package_b.stat()
            os.utime(
                package_b,
                ns=(
                    stat_b.st_atime_ns,
                    stat_b.st_mtime_ns + 5_000_000_000,
                ),
            )

            self.assertEqual(
                snapshot_module._capture_semantic_inputs_sha256(
                    captures_b
                ),
                snapshot_module._capture_semantic_inputs_sha256(
                    captures_a
                ),
            )
            discovery = root / "discovery.sqlite"
            _discovery_fixture(
                discovery,
                evidence_revision=revision,
                evidence_ref=evidence_ref,
                package_fingerprint=package_fingerprint,
                package_size=package_size,
                package_modified=package_modified,
            )
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "native_root": root / "native",
                "full_snapshot": True,
                "generated_at": "2026-07-27T00:00:00+00:00",
            }
            result_a = build_vnext_snapshot(
                capture_root=captures_a,
                output_dir=root / "out-a",
                **common,
            )
            result_b = build_vnext_snapshot(
                capture_root=captures_b,
                output_dir=root / "out-b",
                **common,
            )

        self.assertEqual(result_b["buildId"], result_a["buildId"])
        self.assertEqual(
            result_a["counts"]["core"]["blueprintPackageVerifiedAssets"],
            1,
        )
        self.assertEqual(
            result_b["counts"]["core"]["blueprintPackageVerifiedAssets"],
            1,
        )
        self.assertEqual(
            result_a["counts"]["core"]["blueprintFreshnessGapAssets"],
            0,
        )
        self.assertEqual(
            result_b["counts"]["core"]["blueprintFreshnessGapAssets"],
            0,
        )

    def test_capture_identity_hashes_external_package_and_missing_sidecars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture_root = root / "captures"
            evidence_root = (
                capture_root / "BP_External" / "evidence"
            )
            evidence_root.mkdir(parents=True)
            external_package = (
                root / "devkit-content" / "BP_External.uasset"
            )
            external_package.parent.mkdir()
            external_package.write_bytes(b"external-uasset-v1")
            evidence = sqlite3.connect(
                evidence_root / "evidence.sqlite"
            )
            try:
                evidence.executescript(
                    """
                    CREATE TABLE asset_revisions(
                        revision_id TEXT PRIMARY KEY,
                        uasset_path TEXT NOT NULL
                    );
                    CREATE TABLE source_manifest(
                        revision_id TEXT NOT NULL,
                        path TEXT NOT NULL,
                        source_kind TEXT NOT NULL
                    );
                    """
                )
                evidence.execute(
                    "INSERT INTO asset_revisions VALUES (?, ?)",
                    ("revision-1", str(external_package)),
                )
                evidence.execute(
                    "INSERT INTO source_manifest VALUES (?, ?, ?)",
                    (
                        "revision-1",
                        "binary/BP_External.uasset",
                        "package_binary",
                    ),
                )
                evidence.commit()
            finally:
                evidence.close()
            (evidence_root / "manifest.json").write_text(
                '{"schema":"fixture"}',
                encoding="utf-8",
            )

            baseline = (
                snapshot_module._capture_semantic_inputs_sha256(
                    capture_root
                )
            )
            external_package.write_bytes(b"external-uasset-v2")
            changed_primary = (
                snapshot_module._capture_semantic_inputs_sha256(
                    capture_root
                )
            )
            external_package.write_bytes(b"external-uasset-v1")
            restored = snapshot_module._capture_semantic_inputs_sha256(
                capture_root
            )
            external_package.with_suffix(".uexp").write_bytes(
                b"new-sidecar"
            )
            added_sidecar = (
                snapshot_module._capture_semantic_inputs_sha256(
                    capture_root
                )
            )

        self.assertNotEqual(changed_primary, baseline)
        self.assertEqual(restored, baseline)
        self.assertNotEqual(added_sidecar, baseline)

    def test_snapshot_identity_ignores_unused_native_root_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery = root / "discovery.sqlite"
            discovery.write_bytes(b"stable-discovery-input")
            map_evidence = root / "map.json"
            map_evidence.write_bytes(b'{"catalog":"stable"}')
            capture = root / "captures" / "BP_Test" / "evidence"
            capture.mkdir(parents=True)
            (capture / "evidence.sqlite").write_bytes(
                b"stable-capture-evidence"
            )
            native_root = root / "native"
            native_root.mkdir()
            unused_native_file = native_root / "not-consumed-by-core.json"
            unused_native_file.write_text(
                '{"revision":"a"}',
                encoding="utf-8",
            )
            common = {
                "project_root": PROJECT_ROOT,
                "discovery_database": discovery,
                "legacy_kb_root": root / "legacy",
                "capture_root": root / "captures",
                "native_root": native_root,
                "map_evidence_path": map_evidence,
            }
            baseline = _snapshot_identity_for_inputs(
                output_dir=root / "out-baseline",
                **common,
            )
            unused_native_file.write_text(
                '{"revision":"b"}',
                encoding="utf-8",
            )
            variant = _snapshot_identity_for_inputs(
                output_dir=root / "out-variant",
                **common,
            )

        self.assertEqual(
            variant["sourceSha256"],
            baseline["sourceSha256"],
        )
        self.assertEqual(variant["buildId"], baseline["buildId"])

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
