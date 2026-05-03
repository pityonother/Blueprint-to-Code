import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from import_captures_to_knowledge_dbs import import_captures_to_business_databases  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def create_common_tables(connection: sqlite3.Connection, asset_table: str) -> None:
    connection.execute(
        f"""
        CREATE TABLE {asset_table} (
            object_path TEXT PRIMARY KEY,
            asset_name TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            relative_path TEXT NOT NULL DEFAULT '',
            uasset_path TEXT NOT NULL DEFAULT '',
            captured INTEGER NOT NULL DEFAULT 0,
            processed_current INTEGER NOT NULL DEFAULT 0,
            failed_current INTEGER NOT NULL DEFAULT 0,
            fingerprint TEXT NOT NULL DEFAULT '',
            capture_dir TEXT NOT NULL DEFAULT '',
            read_status TEXT NOT NULL DEFAULT '',
            knowledge_status TEXT NOT NULL DEFAULT '',
            last_read_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        """
        CREATE TABLE read_sources (
            object_path TEXT PRIMARY KEY,
            capture_dir TEXT NOT NULL DEFAULT '',
            package_json TEXT NOT NULL DEFAULT '',
            graph_nodes_json TEXT NOT NULL DEFAULT '',
            class_defaults_json TEXT NOT NULL DEFAULT '',
            last_read_at TEXT NOT NULL DEFAULT '',
            read_status TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE unresolved_work (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_path TEXT NOT NULL,
            work_type TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            source_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'open'
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE asset_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_path TEXT NOT NULL,
            reference_path TEXT NOT NULL,
            reference_type TEXT NOT NULL DEFAULT '',
            source_property TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'unknown'
        )
        """
    )


class KnowledgeCaptureImportTests(unittest.TestCase):
    def test_imports_buff_defaults_graphs_and_unresolved_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db"
            capture_root = root / "captures"
            db_dir.mkdir()
            capture_dir = capture_root / "Buff_Test"
            write_json(
                capture_dir / "uasset_class_defaults.json",
                {
                    "variables": {
                        "ExDamagePerStack": {"value": 0.02, "type": "DoubleProperty", "confidence": "high"},
                        "MinStacksForFury": {"value": 15, "type": "IntProperty", "confidence": "high"},
                        "POIBuff": {
                            "value": "/Game/ASA/Test/Buff_Other.Buff_Other_C",
                            "type": "SoftObjectProperty",
                            "confidence": "high",
                        },
                    }
                },
            )
            write_json(
                capture_dir / "uasset_graph_nodes.json",
                {
                    "graphs": [
                        {
                            "graph": "EventGraph",
                            "confidence": "medium",
                            "nodes": [
                                {"class": "K2Node_CustomEvent", "event": "Refresh", "confidence": "medium"},
                                {"class": "K2Node_CallFunction", "function": "SetFloatParameter", "confidence": "medium"},
                            ],
                        }
                    ]
                },
            )
            write_json(
                capture_dir / "uasset_failed_graph_queue.json",
                {"graphs": [{"graph": "ReceiveBeginPlay", "primary_category": "need_manual_clipboard"}]},
            )

            db_path = db_dir / "buffs.sqlite"
            connection = sqlite3.connect(db_path)
            create_common_tables(connection, "buff_assets")
            connection.execute(
                """
                CREATE TABLE buff_effects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    effect_key TEXT NOT NULL,
                    effect_value TEXT NOT NULL DEFAULT '',
                    duration TEXT NOT NULL DEFAULT '',
                    interval TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown',
                    source_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE buff_triggers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    trigger_name TEXT NOT NULL,
                    graph_name TEXT NOT NULL DEFAULT '',
                    function_name TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE buff_conditions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    condition_key TEXT NOT NULL,
                    condition_value TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown',
                    source_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE buff_stacks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    stack_key TEXT NOT NULL,
                    stack_value TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown',
                    source_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE buff_stat_modifiers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    stat_name TEXT NOT NULL,
                    operation TEXT NOT NULL DEFAULT '',
                    value TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown',
                    source_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE buff_references (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    reference_path TEXT NOT NULL,
                    reference_type TEXT NOT NULL DEFAULT '',
                    source_property TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO buff_assets (
                    object_path, asset_name, captured, processed_current,
                    capture_dir, read_status, knowledge_status
                )
                VALUES (?, ?, 1, 1, ?, 'read', 'imported')
                """,
                ("/Game/Test/Buff_Test.Buff_Test", "Buff_Test", str(capture_dir)),
            )
            connection.commit()
            connection.close()

            payload = import_captures_to_business_databases(db_dir, capture_root, None)

            self.assertEqual(payload["totals"]["assets_imported"], 1)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM buff_effects").fetchone()[0], 3)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM buff_stat_modifiers").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM buff_stacks").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM buff_triggers").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM buff_references").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM unresolved_work").fetchone()[0], 1)
                row = connection.execute("SELECT reference_type, source_property FROM asset_references").fetchone()
                self.assertEqual(row, ("buff", "POIBuff"))
            finally:
                connection.close()

    def test_imports_item_display_properties_and_use_logic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db"
            capture_root = root / "captures"
            db_dir.mkdir()
            capture_dir = capture_root / "PrimalItem_Test"
            write_json(
                capture_dir / "uasset_class_defaults.json",
                {
                    "variables": {
                        "DescriptiveNameBase": {"value": "Dragon Hoard Key", "type": "StrProperty", "confidence": "high"},
                        "ItemDescription": {"value": "Requires a mounted Drakeling.", "type": "StrProperty", "confidence": "high"},
                        "POIBuff": {
                            "value": "/Game/ASA/Test/Buff_POI.Buff_POI_C",
                            "type": "SoftObjectProperty",
                            "confidence": "high",
                        },
                    }
                },
            )
            write_json(
                capture_dir / "uasset_graph_nodes.json",
                {"graphs": [{"graph": "EventGraph", "nodes": [{"class": "K2Node_CallFunction", "function": "UseItem"}]}]},
            )

            db_path = db_dir / "primal_items.sqlite"
            connection = sqlite3.connect(db_path)
            create_common_tables(connection, "item_assets")
            connection.execute(
                """
                CREATE TABLE item_display (
                    object_path TEXT PRIMARY KEY,
                    item_name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    icon_path TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE item_properties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    property_name TEXT NOT NULL,
                    property_value TEXT NOT NULL DEFAULT '',
                    value_type TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown',
                    source_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE item_use_logic (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    trigger_name TEXT NOT NULL,
                    effect_summary TEXT NOT NULL DEFAULT '',
                    source_graph TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE item_crafting_costs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    ingredient_path TEXT NOT NULL,
                    quantity TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE item_grants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    grant_type TEXT NOT NULL,
                    grant_path TEXT NOT NULL DEFAULT '',
                    grant_value TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE item_references (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    reference_path TEXT NOT NULL,
                    reference_type TEXT NOT NULL DEFAULT '',
                    source_property TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO item_assets (
                    object_path, asset_name, captured, processed_current,
                    capture_dir, read_status, knowledge_status
                )
                VALUES (?, ?, 1, 1, ?, 'read', 'imported')
                """,
                ("/Game/Test/PrimalItem_Test.PrimalItem_Test", "PrimalItem_Test", str(capture_dir)),
            )
            connection.commit()
            connection.close()

            import_captures_to_business_databases(db_dir, capture_root, None)

            connection = sqlite3.connect(db_path)
            try:
                display = connection.execute("SELECT item_name, description FROM item_display").fetchone()
                self.assertEqual(display, ("Dragon Hoard Key", "Requires a mounted Drakeling."))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM item_properties").fetchone()[0], 3)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM item_grants").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM item_use_logic").fetchone()[0], 1)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
