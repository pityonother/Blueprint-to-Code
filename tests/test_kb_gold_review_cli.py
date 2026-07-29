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
                    "registration",
                    "--discovery-db",
                    str(database),
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
            self.assertEqual(exported["candidateCases"], 1)
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
