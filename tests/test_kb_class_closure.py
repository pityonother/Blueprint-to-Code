from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.class_hierarchy import (  # noqa: E402
    BUILTIN_CLASS_EDGES,
    class_hierarchy_contract_fingerprint,
    class_hierarchy_source_fingerprint,
    create_class_tables,
    inheritance_path_to_native_root,
    materialize_discovery_classes,
    rebuild_class_closure,
)
from blueprint_translator.kb_vnext import (  # noqa: E402
    class_hierarchy as class_hierarchy_module,
)
from blueprint_translator.kb_vnext.query_planner import (  # noqa: E402
    is_valid_generic_evidence_uri,
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
                "/Game/Test/PDA_Child.PDA_Child_C",
                "/Script/Engine.PrimaryDataAsset",
                "native_parent",
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
                "/Game/Test/BP_Open.BP_Open_C",
                "/Script/Engine.Actor",
                "native_boundary_hint",
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
    def test_contract_fingerprint_covers_builtin_hierarchy(self):
        discovery = _discovery_fixture()
        baseline_contract = class_hierarchy_contract_fingerprint()
        baseline_source = class_hierarchy_source_fingerprint(discovery)
        changed_edges = (
            *BUILTIN_CLASS_EDGES,
            (
                "/Script/Engine.Actor",
                "/Script/CoreUObject.Object",
                "native_parent",
            ),
        )

        with patch.object(
            class_hierarchy_module,
            "BUILTIN_CLASS_EDGES",
            changed_edges,
        ):
            changed_contract = class_hierarchy_contract_fingerprint()
            changed_source = class_hierarchy_source_fingerprint(discovery)

        self.assertNotEqual(changed_contract, baseline_contract)
        self.assertNotEqual(changed_source, baseline_source)
        discovery.close()

    def test_semantic_fingerprint_changes_with_hierarchy_input(self):
        discovery = _discovery_fixture()
        baseline = class_hierarchy_source_fingerprint(discovery)

        discovery.execute(
            """
            UPDATE class_edges
            SET confidence='MEDIUM'
            WHERE child_class_path='/Game/Test/PDA_Child.PDA_Child_C'
              AND parent_class_path='/Game/Test/PDA_Base.PDA_Base_C'
              AND edge_kind='blueprint_parent'
            """
        )
        changed = class_hierarchy_source_fingerprint(discovery)

        self.assertNotEqual(changed, baseline)
        discovery.close()

    def test_source_fingerprint_covers_assignment_identity_evidence(self):
        discovery = _discovery_fixture()
        baseline = class_hierarchy_source_fingerprint(discovery)

        discovery.execute(
            """
            UPDATE assets
            SET identity_status='AMBIGUOUS',
                identity_confidence='LOW'
            WHERE object_path='/Game/Test/PDA_Child.PDA_Child'
            """
        )
        changed = class_hierarchy_source_fingerprint(discovery)

        self.assertNotEqual(changed, baseline)
        discovery.close()

    def test_assignment_evidence_uri_encodes_the_full_entity_identity(self):
        discovery = _discovery_fixture()
        entity_uri = "/Game/Test/Unknown/BP Test.BP Test"
        discovery.execute(
            """
            INSERT INTO assets VALUES (
                ?, '/Script/Engine.ParticleSystem', '', '', '',
                'EXTRACTED', 'HIGH'
            )
            """,
            (entity_uri,),
        )
        target = _target_fixture()
        target.execute(
            "INSERT INTO entities VALUES (4, ?)",
            (entity_uri,),
        )

        materialize_discovery_classes(discovery, target)

        evidence_uri = target.execute(
            """
            SELECT evidence_uri
            FROM asset_class_assignments
            WHERE entity_id=4 AND assignment_kind='ASSET_CLASS'
            """
        ).fetchone()[0]
        self.assertEqual(
            evidence_uri,
            (
                "discovery://asset/"
                "%2FGame%2FTest%2FUnknown%2FBP%20Test.BP%20Test"
                "#asset-class"
            ),
        )
        self.assertTrue(is_valid_generic_evidence_uri(evidence_uri))
        discovery.close()
        target.close()

    def test_generated_class_prefers_defining_package_revision(self):
        discovery = _discovery_fixture()
        discovery.execute(
            """
            UPDATE assets
            SET asset_class_path='/Game/Test/PDA_Child.PDA_Child_C'
            WHERE object_path='/Game/Test/BP_Actor.BP_Actor'
            """
        )
        target = sqlite3.connect(":memory:")
        target.executescript(
            """
            CREATE TABLE packages(
                package_id INTEGER PRIMARY KEY,
                current_revision_id INTEGER
            );
            CREATE TABLE entities(
                entity_id INTEGER PRIMARY KEY,
                canonical_uri TEXT UNIQUE NOT NULL,
                package_id INTEGER
            );
            INSERT INTO packages VALUES (1, 10), (2, 20), (3, 30);
            INSERT INTO entities VALUES
                (1, '/Game/Test/PDA_Child.PDA_Child', 1),
                (2, '/Game/Test/BP_Actor.BP_Actor', 2),
                (3, '/Game/Test/BP_Open.BP_Open', 3);
            """
        )

        materialize_discovery_classes(
            discovery,
            target,
            source_revision_id=99,
        )

        generated_revision = target.execute(
            """
            SELECT source_revision_id
            FROM classes
            WHERE class_path='/Game/Test/PDA_Child.PDA_Child_C'
            """
        ).fetchone()[0]
        edge_revisions = target.execute(
            """
            SELECT DISTINCT edge.source_revision_id
            FROM class_edges AS edge
            JOIN classes AS child
              ON child.class_id=edge.child_class_id
            WHERE child.class_path='/Game/Test/PDA_Child.PDA_Child_C'
              AND edge.edge_kind='blueprint_parent'
            """
        ).fetchall()
        actor_revision = target.execute(
            """
            SELECT source_revision_id
            FROM classes
            WHERE class_path='/Script/Engine.Actor'
            """
        ).fetchone()[0]

        self.assertEqual(generated_revision, 10)
        self.assertEqual(edge_revisions, [(10,)])
        self.assertEqual(actor_revision, 99)
        discovery.close()
        target.close()

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
        native_root_id = target.execute(
            """
            SELECT class_id
            FROM classes
            WHERE class_path='/Script/Engine.PrimaryDataAsset'
            """
        ).fetchone()[0]
        direct_shortcut_count = target.execute(
            """
            SELECT COUNT(*)
            FROM class_edges
            WHERE child_class_id=?
              AND parent_class_id=?
            """,
            (class_id, native_root_id),
        ).fetchone()[0]
        self.assertEqual(direct_shortcut_count, 0)
        native_root_depth = target.execute(
            """
            SELECT depth
            FROM class_closure
            WHERE descendant_class_id=?
              AND ancestor_class_id=?
            """,
            (class_id, native_root_id),
        ).fetchone()[0]
        self.assertEqual(native_root_depth, 2)
        multiple_parent_gaps = target.execute(
            """
            SELECT COUNT(*)
            FROM class_gaps
            WHERE class_id=?
              AND gap_kind='MULTIPLE_PARENT_CANDIDATES'
            """,
            (class_id,),
        ).fetchone()[0]
        self.assertEqual(multiple_parent_gaps, 0)
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
        open_id = target.execute(
            """
            SELECT class_id FROM classes
            WHERE class_path='/Game/Test/BP_Open.BP_Open_C'
            """
        ).fetchone()[0]
        actor_id = target.execute(
            """
            SELECT class_id FROM classes
            WHERE class_path='/Script/Engine.Actor'
            """
        ).fetchone()[0]
        target.execute(
            """
            INSERT INTO class_edges(
                child_class_id, parent_class_id, edge_kind, evidence_id,
                status, confidence
            ) VALUES (?, ?, 'native_boundary_hint', 'fixture://hint',
                      'CONFIRMED', 'HIGH')
            """,
            (open_id, actor_id),
        )
        rebuild_class_closure(target)
        hinted_path = inheritance_path_to_native_root(
            target, "/Game/Test/BP_Open.BP_Open_C"
        )
        self.assertEqual(hinted_path["status"], "PARENT_CHAIN_OPEN")
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
