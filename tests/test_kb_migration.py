from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.legacy import (  # noqa: E402
    import_legacy_lineage,
)


def _core_fixture() -> sqlite3.Connection:
    core = sqlite3.connect(":memory:")
    core.executescript(
        """
        CREATE TABLE entities(
            entity_id INTEGER PRIMARY KEY,
            canonical_uri TEXT UNIQUE NOT NULL
        );
        CREATE TABLE classes(
            class_id INTEGER PRIMARY KEY,
            class_path TEXT UNIQUE NOT NULL
        );
        CREATE TABLE asset_class_assignments(
            entity_id INTEGER NOT NULL,
            class_id INTEGER NOT NULL,
            assignment_kind TEXT NOT NULL
        );
        CREATE TABLE source_revisions(
            revision_id INTEGER PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_uri TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            producer_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            freshness_status TEXT NOT NULL
        );
        CREATE TABLE legacy_lineage(
            lineage_id INTEGER PRIMARY KEY,
            target_kind TEXT NOT NULL,
            target_id INTEGER,
            legacy_database TEXT NOT NULL,
            legacy_table TEXT NOT NULL,
            legacy_primary_key TEXT NOT NULL,
            source_asset_uri TEXT NOT NULL,
            evidence_uri TEXT NOT NULL,
            status TEXT NOT NULL,
            source_revision_id INTEGER NOT NULL,
            UNIQUE(legacy_database, legacy_table, legacy_primary_key)
        );
        INSERT INTO entities VALUES (
            1, '/Game/Test/Buff_Known.Buff_Known'
        );
        INSERT INTO classes VALUES (
            1, '/Game/Test/Buff_Known.Buff_Known_C'
        );
        INSERT INTO asset_class_assignments VALUES (
            1, 1, 'GENERATED_CLASS'
        );
        INSERT INTO source_revisions VALUES (
            1, 'discovery', 'discovery://fixture', 'fixture',
            'fixture', 'v1', '2026-07-27T00:00:00+00:00', 'FRESH'
        );
        """
    )
    return core


def _legacy_fixture(root: Path) -> Path:
    root.mkdir(parents=True)
    path = root / "buffs.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE buff_effects(
            id INTEGER PRIMARY KEY,
            object_path TEXT NOT NULL,
            effect_key TEXT NOT NULL,
            effect_value TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_json TEXT NOT NULL,
            capture_dir TEXT NOT NULL
        );
        INSERT INTO buff_effects VALUES (
            1,
            '/Game/Test/Buff_Known.Buff_Known',
            'health',
            '-5',
            'HIGH',
            '{"evidence_id":"bp://fixture/buff/effect/1"}',
            'C:\\Users\\secret\\captures\\Buff_Known'
        );
        INSERT INTO buff_effects VALUES (
            2,
            '/Game/Test/Buff_Unknown.Buff_Unknown',
            'health',
            '-10',
            'LOW',
            '{}',
            'C:\\Users\\secret\\captures\\Buff_Unknown'
        );
        """
    )
    connection.commit()
    connection.close()
    return path


class KnowledgeMigrationTests(unittest.TestCase):
    def test_imports_every_row_with_lineage_and_no_local_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_root = root / "legacy"
            legacy_path = _legacy_fixture(legacy_root)
            before = hashlib.sha256(legacy_path.read_bytes()).hexdigest()
            core = _core_fixture()
            result = import_legacy_lineage(
                core=core,
                legacy_root=legacy_root,
                generated_at="2026-07-27T00:00:00+00:00",
            )
            after = hashlib.sha256(legacy_path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(result["rows"], 2)
            rows = list(
                core.execute(
                    """
                    SELECT
                        target_id, legacy_database, legacy_table,
                        legacy_primary_key, source_asset_uri,
                        evidence_uri, status
                    FROM legacy_lineage
                    ORDER BY lineage_id
                    """
                )
            )
            self.assertEqual(rows[0][0], 1)
            self.assertEqual(rows[0][1], "buffs.sqlite")
            self.assertEqual(rows[0][2], "buff_effects")
            self.assertIn('"id":1', rows[0][3])
            self.assertEqual(
                rows[0][4], "/Game/Test/Buff_Known.Buff_Known"
            )
            self.assertEqual(
                rows[0][5], "bp://fixture/buff/effect/1"
            )
            self.assertEqual(rows[0][6], "IMPORTED_WITH_EVIDENCE")
            self.assertIsNone(rows[1][0])
            self.assertTrue(str(rows[1][5]).startswith("legacy://"))
            self.assertEqual(rows[1][6], "LEGACY_UNVERIFIED")
            serialized = "\n".join(
                str(value)
                for row in rows
                for value in row
                if value is not None
            )
            self.assertNotIn("C:\\Users\\", serialized)
            source = core.execute(
                """
                SELECT source_uri, source_fingerprint
                FROM source_revisions
                WHERE source_kind='legacy_kb'
                """
            ).fetchone()
            self.assertEqual(source[0], "legacy-kb://buffs.sqlite")
            self.assertEqual(source[1], before)
            core.close()


if __name__ == "__main__":
    unittest.main()
