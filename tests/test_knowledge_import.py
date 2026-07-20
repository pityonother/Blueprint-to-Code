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
    connection.execute(
        """
        CREATE TABLE formula_candidates (
            id TEXT PRIMARY KEY,
            object_path TEXT NOT NULL,
            asset_name TEXT NOT NULL DEFAULT '',
            asset_type TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            mechanism_type TEXT NOT NULL DEFAULT '',
            mechanism TEXT NOT NULL DEFAULT '',
            player_meaning TEXT NOT NULL DEFAULT '',
            graph TEXT NOT NULL DEFAULT '',
            visible_rule TEXT NOT NULL DEFAULT '',
            formula_text TEXT NOT NULL DEFAULT '',
            formula_ast_json TEXT NOT NULL DEFAULT '{}',
            inputs_json TEXT NOT NULL DEFAULT '[]',
            outputs_json TEXT NOT NULL DEFAULT '[]',
            conditions_json TEXT NOT NULL DEFAULT '[]',
            math_nodes_json TEXT NOT NULL DEFAULT '[]',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            link_quality_json TEXT NOT NULL DEFAULT '{}',
            external_dependencies_json TEXT NOT NULL DEFAULT '[]',
            missing_evidence_json TEXT NOT NULL DEFAULT '[]',
            next_probe_json TEXT NOT NULL DEFAULT '[]',
            confidence TEXT NOT NULL DEFAULT 'unknown',
            status TEXT NOT NULL DEFAULT 'candidate',
            source_capture TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE unresolved_formulas (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL DEFAULT '',
            object_path TEXT NOT NULL,
            asset_name TEXT NOT NULL DEFAULT '',
            asset_type TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            mechanism_type TEXT NOT NULL DEFAULT '',
            mechanism TEXT NOT NULL DEFAULT '',
            known_visible_part TEXT NOT NULL DEFAULT '',
            blocked_by_json TEXT NOT NULL DEFAULT '[]',
            missing_evidence_json TEXT NOT NULL DEFAULT '[]',
            required_next_probe_json TEXT NOT NULL DEFAULT '[]',
            priority INTEGER NOT NULL DEFAULT 50,
            status TEXT NOT NULL DEFAULT 'open',
            confidence TEXT NOT NULL DEFAULT 'unresolved_formula',
            source_capture TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )


class KnowledgeCaptureImportTests(unittest.TestCase):
    def test_imports_formula_candidates_and_unresolved_formulas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db"
            capture_root = root / "captures"
            db_dir.mkdir()
            capture_dir = capture_root / "PrimalItem_TestFormula"
            write_json(
                capture_dir / "output" / "formula_candidates.json",
                {
                    "schema": "ark.blueprint.formula_candidates.v1",
                    "asset_name": "PrimalItem_TestFormula",
                    "asset_path": "/Game/Test/PrimalItem_TestFormula.PrimalItem_TestFormula",
                    "asset_type": "primal_item_blueprint",
                    "generated_at": "2026-05-05T00:00:00",
                    "summary": {"candidate_count": 1, "unresolved_count": 1, "confidence_counts": {"low": 1}},
                    "candidates": [
                        {
                            "id": "test_formula_candidate",
                            "domain": "item",
                            "mechanism_type": "stat_weight",
                            "mechanism": "Test stat weight",
                            "player_meaning": "Visible test candidate.",
                            "graph": "BPOverrideInheritedStatWeight",
                            "trigger_graphs": ["BPOverrideInheritedStatWeight"],
                            "visible_rule": "DistributionForMaxWeight = 0.5",
                            "formula_text": "Candidate only.",
                            "formula_ast": {},
                            "inputs": [{"name": "DistributionForMaxWeight", "value": 0.5}],
                            "outputs": [],
                            "conditions": [],
                            "math_nodes": ["MapRangeClamped"],
                            "evidence": [{"source": "test"}],
                            "link_quality": {"resolution_counts": {"resolved_pin_heuristic": 1}},
                            "external_dependencies": [{"name": "GetCustomItemData"}],
                            "missing_evidence": ["Pin/LinkedTo includes resolved_pin_heuristic links"],
                            "confidence": "low",
                            "status": "candidate",
                            "db_targets": ["formula_candidates", "primal_items.sqlite"],
                            "next_probe": [{"kind": "pin_resolution", "detail": "decode exact pin ids"}],
                        }
                    ],
                    "unresolved_formulas": [
                        {
                            "id": "test_formula_unresolved",
                            "candidate_id": "test_formula_candidate",
                            "mechanism_type": "stat_weight",
                            "mechanism": "Test stat weight",
                            "known_visible_part": "DistributionForMaxWeight = 0.5",
                            "blocked_by": ["GetCustomItemData"],
                            "missing_evidence": ["native body unavailable"],
                            "required_next_probe": [{"kind": "native", "detail": "inspect visible caller evidence"}],
                            "priority": 50,
                            "status": "open",
                            "confidence": "unresolved_formula",
                        }
                    ],
                },
            )

            db_path = db_dir / "primal_items.sqlite"
            connection = sqlite3.connect(db_path)
            create_common_tables(connection, "item_assets")
            connection.execute(
                """
                INSERT INTO item_assets (
                    object_path, asset_name, asset_type, captured, processed_current,
                    capture_dir, read_status, knowledge_status
                )
                VALUES (?, ?, 'primal_item_blueprint', 1, 1, ?, 'read', 'imported')
                """,
                ("/Game/Test/PrimalItem_TestFormula.PrimalItem_TestFormula", "PrimalItem_TestFormula", str(capture_dir)),
            )
            connection.commit()
            connection.close()

            payload = import_captures_to_business_databases(db_dir, capture_root, None)

            self.assertEqual(payload["totals"]["formula_candidates_imported"], 1)
            self.assertEqual(payload["totals"]["unresolved_formulas_imported"], 1)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM formula_candidates").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM unresolved_formulas").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM unresolved_work WHERE work_type = 'formula_unresolved_dependency'"
                    ).fetchone()[0],
                    1,
                )
            finally:
                connection.close()

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
            write_json(
                capture_dir / "uasset_unknown_properties.json",
                {
                    "unknown_properties": [
                        {"property": "AdvancedPinDisplay", "type": "Guid", "confidence": "low"},
                        {"property": "BaseArmorValue", "type": "StructProperty", "confidence": "low"},
                    ]
                },
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
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM unresolved_work").fetchone()[0], 2)
                unresolved = {
                    row[0]
                    for row in connection.execute("SELECT detail FROM unresolved_work")
                }
                self.assertIn("BaseArmorValue", unresolved)
                self.assertNotIn("AdvancedPinDisplay", unresolved)
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

    def test_loot_import_marks_property_parser_needed_when_sets_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "db"
            capture_root = root / "captures"
            db_dir.mkdir()
            capture_dir = capture_root / "SupplyCrate_Test"
            write_json(
                capture_dir / "uasset_class_defaults.json",
                {
                    "variables": {},
                    "properties": [
                        {
                            "name": "LootItemSets",
                            "type": "ArrayProperty",
                            "value": [],
                            "array_parse": {
                                "parsed": False,
                                "element_kind": "unknown",
                                "raw_size": 96,
                            },
                            "confidence": "low",
                        },
                        {
                            "name": "ItemSetWeights",
                            "type": "StructProperty",
                            "value": {"parsed": False, "raw_size": 48},
                            "struct_parse": {
                                "parsed": False,
                                "struct_name": "MysteryLootStruct",
                                "raw_size": 48,
                            },
                            "confidence": "low",
                        },
                    ],
                },
            )
            write_json(capture_dir / "uasset_graph_nodes.json", {"graphs": [{"graph": "EventGraph", "nodes": []}]})

            db_path = db_dir / "loot.sqlite"
            connection = sqlite3.connect(db_path)
            create_common_tables(connection, "loot_assets")
            connection.execute(
                """
                CREATE TABLE loot_crates (
                    object_path TEXT PRIMARY KEY,
                    crate_type TEXT NOT NULL DEFAULT '',
                    quality_min TEXT NOT NULL DEFAULT '',
                    quality_max TEXT NOT NULL DEFAULT '',
                    level_requirement TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE loot_item_sets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    set_name TEXT NOT NULL,
                    set_weight TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown',
                    source_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE loot_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    item_path TEXT NOT NULL,
                    entry_weight TEXT NOT NULL DEFAULT '',
                    quantity_min TEXT NOT NULL DEFAULT '',
                    quantity_max TEXT NOT NULL DEFAULT '',
                    quality_min TEXT NOT NULL DEFAULT '',
                    quality_max TEXT NOT NULL DEFAULT '',
                    blueprint_chance TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE loot_conditions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    condition_key TEXT NOT NULL,
                    condition_value TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE loot_rewards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_path TEXT NOT NULL,
                    reward_type TEXT NOT NULL,
                    reward_value TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT 'unknown',
                    source_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE loot_references (
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
                INSERT INTO loot_assets (
                    object_path, asset_name, captured, processed_current,
                    capture_dir, read_status, knowledge_status
                )
                VALUES (?, ?, 1, 1, ?, 'read', 'imported')
                """,
                ("/Game/Test/SupplyCrate_Test.SupplyCrate_Test", "SupplyCrate_Test", str(capture_dir)),
            )
            connection.commit()
            connection.close()

            payload = import_captures_to_business_databases(db_dir, capture_root, None)

            self.assertEqual(payload["totals"]["unresolved_imported"], 2)
            connection = sqlite3.connect(db_path)
            try:
                rows = connection.execute(
                    "SELECT detail, source_json FROM unresolved_work WHERE work_type = 'property_parser_needed'"
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertTrue(any("LootItemSets" in row[0] for row in rows))
                self.assertTrue(any("ItemSetWeights" in row[0] for row in rows))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM loot_item_sets").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM loot_entries").fetchone()[0], 0)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
