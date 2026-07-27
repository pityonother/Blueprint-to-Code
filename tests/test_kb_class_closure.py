from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.class_hierarchy import (  # noqa: E402
    create_class_tables,
    inheritance_path_to_native_root,
    materialize_discovery_classes,
    rebuild_class_closure,
)


def _discovery_fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE assets(
            object_path TEXT PRIMARY KEY,
            asset_class_path TEXT NOT NULL,
            generated_class_path TEXT NOT NULL,
            parent_class_path TEXT NOT NULL,
            native_parent_class_path TEXT NOT NULL,
            identity_status TEXT NOT NULL,
            identity_confidence TEXT NOT NULL
        );
        CREATE TABLE class_edges(
            child_class_path TEXT NOT NULL,
            parent_class_path TEXT NOT NULL,
            edge_kind TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            confidence TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO assets VALUES (?, ?, ?, ?, ?, 'EXTRACTED', 'HIGH')",
        [
            (
                "/Game/Test/PDA_Child.PDA_Child",
                "/Script/Engine.Blueprint",
                "/Game/Test/PDA_Child.PDA_Child_C",
                "/Game/Test/PDA_Base.PDA_Base_C",
                "/Script/Engine.PrimaryDataAsset",
            ),
            (
                "/Game/Test/BP_Actor.BP_Actor",
                "/Script/Engine.Blueprint",
                "/Game/Test/BP_Actor.BP_Actor_C",
                "/Script/Engine.Actor",
                "/Script/Engine.Actor",
            ),
            (
                "/Game/Test/BP_Open.BP_Open",
                "/Script/Engine.Blueprint",
                "/Game/Test/BP_Open.BP_Open_C",
                "/Game/Test/BP_Uncaptured.BP_Uncaptured_C",
                "UNKNOWN",
            ),
        ],
    )
    connection.executemany(
        "INSERT INTO class_edges VALUES (?, ?, ?, 'fixture', 'HIGH')",
        [
            (
                "/Game/Test/PDA_Child.PDA_Child_C",
                "/Game/Test/PDA_Base.PDA_Base_C",
                "blueprint_parent",
            ),
            (
                "/Game/Test/PDA_Base.PDA_Base_C",
                "/Script/Engine.PrimaryDataAsset",
                "native_parent",
            ),
            (
                "/Script/Engine.PrimaryDataAsset",
                "/Script/Engine.DataAsset",
                "native_parent",
            ),
            (
                "/Script/Engine.DataAsset",
                "/Script/CoreUObject.Object",
                "native_parent",
            ),
            (
                "/Game/Test/BP_Actor.BP_Actor_C",
                "/Script/Engine.Actor",
                "native_parent",
            ),
            (
                "/Script/Engine.Actor",
                "/Script/CoreUObject.Object",
                "native_parent",
            ),
            (
                "/Game/Test/BP_Open.BP_Open_C",
                "/Game/Test/BP_Uncaptured.BP_Uncaptured_C",
                "blueprint_parent",
            ),
            (
                "/Game/Test/CycleA.CycleA_C",
                "/Game/Test/CycleB.CycleB_C",
                "blueprint_parent",
            ),
            (
                "/Game/Test/CycleB.CycleB_C",
                "/Game/Test/CycleA.CycleA_C",
                "blueprint_parent",
            ),
        ],
    )
    return connection


def _target_fixture() -> sqlite3.Connection:
    target = sqlite3.connect(":memory:")
    target.execute("PRAGMA foreign_keys=ON")
    target.executescript(
        """
        CREATE TABLE entities(
            entity_id INTEGER PRIMARY KEY,
            canonical_uri TEXT UNIQUE NOT NULL
        );
        INSERT INTO entities VALUES (1, '/Game/Test/PDA_Child.PDA_Child');
        INSERT INTO entities VALUES (2, '/Game/Test/BP_Actor.BP_Actor');
        INSERT INTO entities VALUES (3, '/Game/Test/BP_Open.BP_Open');
        """
    )
    return target


class KnowledgeClassClosureTests(unittest.TestCase):
    def test_unifies_blueprint_and_native_classes_and_classifies_data_asset(self):
        discovery = _discovery_fixture()
        target = _target_fixture()
        result = materialize_discovery_classes(discovery, target)

        self.assertGreater(result["classes"], 0)
        self.assertGreater(result["classEdges"], 0)
        class_id = target.execute(
            """
            SELECT class_id
            FROM classes
            WHERE class_path='/Game/Test/PDA_Child.PDA_Child_C'
            """
        ).fetchone()[0]
        categories = {
            row[0]
            for row in target.execute(
                """
                SELECT category
                FROM class_ancestry_categories
                WHERE class_id=?
                """,
                (class_id,),
            )
        }
        self.assertIn("DATA_ASSET", categories)
        self.assertIn("PRIMARY_DATA_ASSET", categories)
        path = inheritance_path_to_native_root(
            target, "/Game/Test/PDA_Child.PDA_Child_C"
        )
        self.assertEqual(path["status"], "CONFIRMED")
        self.assertEqual(
            path["path"][-1], "/Script/Engine.PrimaryDataAsset"
        )
        discovery.close()
        target.close()

    def test_open_chain_and_cycle_are_not_silently_ignored(self):
        discovery = _discovery_fixture()
        target = _target_fixture()
        materialize_discovery_classes(discovery, target)
        open_path = inheritance_path_to_native_root(
            target, "/Game/Test/BP_Open.BP_Open_C"
        )
        self.assertEqual(open_path["status"], "PARENT_CHAIN_OPEN")
        self.assertIn("NATIVE_ROOT_NOT_REACHED", open_path["gaps"])
        cycle_count = target.execute(
            """
            SELECT COUNT(*)
            FROM class_gaps
            WHERE gap_kind='INHERITANCE_CYCLE'
            """
        ).fetchone()[0]
        self.assertGreaterEqual(cycle_count, 2)
        discovery.close()
        target.close()

    def test_incremental_rebuild_only_recomputes_descendants(self):
        target = sqlite3.connect(":memory:")
        create_class_tables(target)
        target.executemany(
            """
            INSERT INTO classes(
                class_path, class_name, module_or_package, class_kind,
                is_native, status, confidence
            ) VALUES (?, ?, 'fixture', ?, ?, 'IDENTIFIED', 'HIGH')
            """,
            [
                ("/Script/Test.Root", "Root", "NATIVE_UCLASS", 1),
                ("/Game/Test/Base.Base_C", "Base_C", "BLUEPRINT_GENERATED_CLASS", 0),
                ("/Game/Test/Leaf.Leaf_C", "Leaf_C", "BLUEPRINT_GENERATED_CLASS", 0),
                ("/Game/Test/Other.Other_C", "Other_C", "BLUEPRINT_GENERATED_CLASS", 0),
            ],
        )
        ids = {
            row[0]: row[1]
            for row in target.execute("SELECT class_path, class_id FROM classes")
        }
        target.executemany(
            """
            INSERT INTO class_edges VALUES (
                ?, ?, 'parent', ?, NULL, 'CONFIRMED', 'HIGH'
            )
            """,
            [
                (ids["/Game/Test/Base.Base_C"], ids["/Script/Test.Root"], "e1"),
                (ids["/Game/Test/Leaf.Leaf_C"], ids["/Game/Test/Base.Base_C"], "e2"),
                (ids["/Game/Test/Other.Other_C"], ids["/Script/Test.Root"], "e3"),
            ],
        )
        rebuild_class_closure(target)
        before_other = list(
            target.execute(
                """
                SELECT ancestor_class_id, depth, path_status
                FROM class_closure
                WHERE descendant_class_id=?
                ORDER BY ancestor_class_id
                """,
                (ids["/Game/Test/Other.Other_C"],),
            )
        )
        target.execute(
            """
            UPDATE class_edges
            SET confidence='UNKNOWN', status='AMBIGUOUS'
            WHERE child_class_id=?
            """,
            (ids["/Game/Test/Base.Base_C"],),
        )
        result = rebuild_class_closure(
            target,
            changed_class_ids=[ids["/Game/Test/Base.Base_C"]],
        )
        self.assertEqual(result["affectedClasses"], 2)
        after_other = list(
            target.execute(
                """
                SELECT ancestor_class_id, depth, path_status
                FROM class_closure
                WHERE descendant_class_id=?
                ORDER BY ancestor_class_id
                """,
                (ids["/Game/Test/Other.Other_C"],),
            )
        )
        self.assertEqual(before_other, after_other)
        leaf_status = target.execute(
            """
            SELECT path_status
            FROM class_closure
            WHERE ancestor_class_id=? AND descendant_class_id=?
            """,
            (
                ids["/Script/Test.Root"],
                ids["/Game/Test/Leaf.Leaf_C"],
            ),
        ).fetchone()[0]
        self.assertEqual(leaf_status, "AMBIGUOUS")
        target.close()


if __name__ == "__main__":
    unittest.main()
