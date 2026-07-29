from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "export_ark_kb_gold_review_packs.py"
VALIDATE_SCRIPT = (
    PROJECT_ROOT / "scripts" / "validate_ark_kb_gold_reviews.py"
)
IMPORT_SCRIPT = PROJECT_ROOT / "scripts" / "import_ark_kb_gold_reviews.py"
QUERY_GOLD = PROJECT_ROOT / "tests" / "fixtures" / "kb_query_gold_set.v1.json"


class GoldReviewCliTests(unittest.TestCase):
    def test_role_pack_export_uses_observable_discovery_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "discovery.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE source_inventory (
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
                CREATE TABLE assets (
                    object_path TEXT PRIMARY KEY,
                    asset_name TEXT NOT NULL,
                    asset_class_path TEXT NOT NULL,
                    blueprint_kind TEXT NOT NULL,
                    parent_class_path TEXT NOT NULL,
                    native_parent_class_path TEXT NOT NULL,
                    top_folder TEXT NOT NULL,
                    plugin_or_dlc TEXT NOT NULL,
                    is_blueprint INTEGER,
                    is_map INTEGER NOT NULL,
                    is_data_asset INTEGER,
                    is_data_table INTEGER,
                    is_function_library INTEGER,
                    is_blueprint_interface INTEGER,
                    is_user_defined_struct INTEGER,
                    is_user_defined_enum INTEGER,
                    identity_status TEXT NOT NULL,
                    identity_source_kind TEXT NOT NULL,
                    evidence_freshness TEXT NOT NULL,
                    referencer_count INTEGER NOT NULL,
                    descendant_count INTEGER NOT NULL,
                    map_usage_count INTEGER NOT NULL,
                    registry_usage_count INTEGER NOT NULL,
                    component_reuse_count INTEGER NOT NULL,
                    cross_domain_reference_count INTEGER NOT NULL
                );
                INSERT INTO metadata VALUES
                    ('schema', 'blueprint-to-code-kb-discovery/v2'),
                    ('generated_at_utc', '2026-07-29T00:00:00+00:00');
                INSERT INTO source_inventory VALUES (
                    'source://filesystem-inventory',
                    'filesystem_inventory',
                    'blueprint-to-code-kb-discovery-state/v1',
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                    'COMPLETE', 'HIGH', 1,
                    '2026-07-29T00:00:00+00:00', '[]'
                );
                INSERT INTO assets VALUES (
                    '/Game/Test/BP_Test.BP_Test',
                    'BP_Test',
                    '/Script/Engine.Blueprint',
                    'NORMAL',
                    '/Script/CoreUObject.Object',
                    '/Script/CoreUObject.Object',
                    'Test',
                    'Test',
                    1, 0, 0, 0, 0, 0, 0, 0,
                    'EXTRACTED',
                    'unreal_asset_registry',
                    'FRESH',
                    1, 2, 3, 4, 5, 6
                );
                """
            )
            connection.commit()
            connection.close()
            output = root / "packs"
            export = subprocess.run(
                [
                    sys.executable,
                    str(EXPORT_SCRIPT),
                    "--kind",
                    "role",
                    "--discovery-db",
                    str(database),
                    "--limit",
                    "360",
                    "--output",
                    str(output),
                    "--author-id",
                    "codex-stage10-pack-author",
                    "--author-key-fingerprint",
                    "automation:codex-stage10-pack-author",
                    "--seed",
                    "stage10-role-v1",
                    "--created-at",
                    "2026-07-29T00:00:00+00:00",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(export.returncode, 0, export.stderr)
            exported = json.loads(export.stdout)
            self.assertEqual(exported["kind"], "role")
            self.assertEqual(exported["candidateCases"], 1)
            pack_path = Path(exported["packPath"])
            source_path = Path(exported["sourceManifestPath"])
            self.assertTrue(pack_path.is_file())
            self.assertTrue(source_path.is_file())
            serialized = pack_path.read_text(encoding="utf-8").casefold()
            for forbidden in (
                "knowledgeroles",
                "currentroles",
                "expectedroles",
                "prediction",
                "confidence",
            ):
                self.assertNotIn(forbidden, serialized)

    def test_registration_pack_export_uses_discovery_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "discovery.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE source_inventory (
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
                CREATE TABLE system_registrations (
                    registration_id TEXT PRIMARY KEY,
                    owner_object_path TEXT NOT NULL,
                    registration_type TEXT NOT NULL,
                    target_object_path TEXT NOT NULL,
                    source_property TEXT NOT NULL,
                    source_evidence_id TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    source_kind TEXT NOT NULL
                );
                CREATE TABLE assets (
                    object_path TEXT PRIMARY KEY,
                    evidence_freshness TEXT NOT NULL
                );
                INSERT INTO metadata VALUES
                    ('schema', 'blueprint-to-code-kb-discovery/v2'),
                    ('generated_at_utc', '2026-07-29T00:00:00+00:00');
                INSERT INTO source_inventory VALUES (
                    'source://existing-knowledge-databases',
                    'existing_knowledge_database',
                    'sqlite-snapshot-inventory/v1',
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    'COMPLETE', 'HIGH', 1,
                    '2026-07-29T00:00:00+00:00', '[]'
                );
                INSERT INTO system_registrations VALUES (
                    'registration://a',
                    '/Game/Owner/A.Owner',
                    'item_registration',
                    '/Game/Target/A.Target_C',
                    'ItemClass',
                    'existing-kb://registrations/a',
                    'HIGH',
                    'existing_knowledge_database'
                );
                INSERT INTO assets VALUES (
                    '/Game/Owner/A.Owner', 'FRESH'
                );
                """
            )
            connection.commit()
            connection.close()
            captures = root / "captures"
            evidence_db = (
                captures
                / "capture-a"
                / "evidence"
                / "evidence.sqlite"
            )
            evidence_db.parent.mkdir(parents=True)
            evidence = sqlite3.connect(evidence_db)
            evidence.executescript(
                """
                CREATE TABLE asset_revisions (
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
                CREATE TABLE class_defaults (
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
                INSERT INTO asset_revisions VALUES (
                    'revision-a', 'asset-a', 'OwnerA',
                    '/Game/Owner/A.Owner',
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                    'evidence-test/v1', 'ark.blueprint.evidence.v2',
                    '2026-07-29T00:00:00+00:00', 'redacted'
                );
                INSERT INTO class_defaults VALUES (
                    'bp://asset-a@revision-a/default/RawClass',
                    'revision-a', 'RawClass', 'SoftObjectProperty',
                    '"/Game/Target/Raw.Raw_C"', 'json', NULL, 'high',
                    'uasset_cdo_property_tag', '{}'
                );
                """
            )
            evidence.commit()
            evidence.close()
            output = root / "packs"
            export = subprocess.run(
                [
                    sys.executable,
                    str(EXPORT_SCRIPT),
                    "--kind",
                    "registration",
                    "--discovery-db",
                    str(database),
                    "--captures-root",
                    str(captures),
                    "--limit",
                    "120",
                    "--output",
                    str(output),
                    "--author-id",
                    "codex-stage10-pack-author",
                    "--author-key-fingerprint",
                    "automation:codex-stage10-pack-author",
                    "--seed",
                    "stage10-registration-v1",
                    "--created-at",
                    "2026-07-29T00:00:00+00:00",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(export.returncode, 0, export.stderr)
            exported = json.loads(export.stdout)
            self.assertEqual(exported["kind"], "registration")
            self.assertEqual(exported["candidateCases"], 2)
            pack_path = Path(exported["packPath"])
            source_path = Path(exported["sourceManifestPath"])
            self.assertTrue(pack_path.is_file())
            self.assertTrue(source_path.is_file())
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            self.assertEqual(pack["kind"], "registration")
            self.assertNotIn("confidence", pack_path.read_text().casefold())

    def test_query_pack_export_validate_and_blocked_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "packs"
            export = subprocess.run(
                [
                    sys.executable,
                    str(EXPORT_SCRIPT),
                    "--kind",
                    "query",
                    "--gold-set",
                    str(QUERY_GOLD),
                    "--output",
                    str(output),
                    "--author-id",
                    "codex-stage10-pack-author",
                    "--author-key-fingerprint",
                    "automation:codex-stage10-pack-author",
                    "--seed",
                    "stage10-query-v1",
                    "--created-at",
                    "2026-07-29T00:00:00+00:00",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(export.returncode, 0, export.stderr)
            exported = json.loads(export.stdout)
            pack_path = Path(exported["packPath"])
            self.assertTrue(pack_path.is_file())

            validate = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATE_SCRIPT),
                    "--pack",
                    str(pack_path),
                    "--pack-only",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(validate.returncode, 0, validate.stderr)
            self.assertEqual(
                json.loads(validate.stdout)["status"],
                "VALID_REVIEW_PACK",
            )

            reviews = root / "reviews"
            reviews.mkdir()
            import_report = root / "import-report.json"
            imported = subprocess.run(
                [
                    sys.executable,
                    str(IMPORT_SCRIPT),
                    "--pack",
                    str(pack_path),
                    "--reviews",
                    str(reviews),
                    "--output",
                    str(import_report),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(imported.returncode, 2, imported.stderr)
            result = json.loads(import_report.read_text(encoding="utf-8"))
            self.assertEqual(
                result["validation"]["status"],
                "BLOCKED_BY_INDEPENDENT_REVIEW",
            )
            self.assertFalse(result["productionGoldWritten"])


if __name__ == "__main__":
    unittest.main()
