from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.fact_store import (  # noqa: E402
    FactValue,
    materialize_effective_defaults,
    store_fact,
)
from blueprint_translator.kb_vnext.invalidation import (  # noqa: E402
    InvalidationPlan,
    apply_invalidation_plan,
    plan_invalidation,
    rebuild_invalidation_dependencies,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


def _fixture() -> tuple[sqlite3.Connection, dict[str, int]]:
    ontology = load_ontology(PROJECT_ROOT / "ontology")
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(FULL_CORE_SCHEMA_SQL)
    connection.executemany(
        "INSERT INTO source_revisions VALUES (?, ?, ?, ?, 'test', 'v1', '2026-07-27T00:00:00Z', 'FRESH')",
        [
            (1, "discovery", "discovery://fixture", "discovery-sha"),
            (2, "native_evidence", "native://fixture", "native-sha"),
            (
                3,
                "blueprint_parser",
                "parser://fixture/class-edges",
                "parser-edge-sha",
            ),
            (
                4,
                "native_evidence",
                "native://fixture/class-edges",
                "native-edge-sha",
            ),
            (
                5,
                "native_evidence",
                "native://fixture/class-root",
                "native-root-sha",
            ),
            (
                6,
                "blueprint_parser",
                "parser://fixture/alternate-parent",
                "alternate-parent-sha",
            ),
        ],
    )
    connection.executemany(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind, status, confidence
        ) VALUES (?, ?, 'BLUEPRINT_ASSET', 'CONFIRMED', 'HIGH')
        """,
        [
            (1, "/Game/Test/Base.Base"),
            (2, "/Game/Test/Child.Child"),
            (3, "/Game/Test/Leaf.Leaf"),
            (4, "/Game/Test/Other.Other"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO classes(
            class_id, class_path, class_name, module_or_package,
            class_kind, is_native, source_revision_id, status, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, 1, 'IDENTIFIED', 'HIGH')
        """,
        [
            (
                100,
                "/Script/CoreUObject.Object",
                "Object",
                "CoreUObject",
                "NATIVE",
                1,
            ),
            (
                11,
                "/Game/Test/Base.Base_C",
                "Base_C",
                "/Game/Test",
                "BLUEPRINT_GENERATED_CLASS",
                0,
            ),
            (
                12,
                "/Game/Test/Child.Child_C",
                "Child_C",
                "/Game/Test",
                "BLUEPRINT_GENERATED_CLASS",
                0,
            ),
            (
                13,
                "/Game/Test/Leaf.Leaf_C",
                "Leaf_C",
                "/Game/Test",
                "BLUEPRINT_GENERATED_CLASS",
                0,
            ),
            (
                14,
                "/Game/Test/Other.Other_C",
                "Other_C",
                "/Game/Test",
                "BLUEPRINT_GENERATED_CLASS",
                0,
            ),
        ],
    )
    connection.execute(
        "UPDATE classes SET source_revision_id=5 WHERE class_id=100"
    )
    connection.executemany(
        "INSERT INTO class_edges VALUES (?, ?, ?, ?, ?, 'CONFIRMED', 'HIGH')",
        [
            (11, 100, "native_parent", "edge://base/root", 4),
            (12, 11, "blueprint_parent", "edge://child/base", 3),
            (13, 12, "blueprint_parent", "edge://leaf/child", 3),
            (14, 100, "native_parent", "edge://other/root", 4),
        ],
    )
    connection.executemany(
        "INSERT INTO class_closure VALUES (?, ?, ?, ?)",
        [
            (100, 100, 0, "SELF"),
            (11, 11, 0, "SELF"),
            (100, 11, 1, "CONFIRMED"),
            (12, 12, 0, "SELF"),
            (11, 12, 1, "CONFIRMED"),
            (100, 12, 2, "CONFIRMED"),
            (13, 13, 0, "SELF"),
            (12, 13, 1, "CONFIRMED"),
            (11, 13, 2, "CONFIRMED"),
            (100, 13, 3, "CONFIRMED"),
            (14, 14, 0, "SELF"),
            (100, 14, 1, "CONFIRMED"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO asset_class_assignments VALUES (
            ?, ?, 'GENERATED_CLASS', 'fixture://class',
            'EXTRACTED', 'HIGH', 1
        )
        """,
        [(1, 11), (2, 12), (3, 13), (4, 14)],
    )
    fact_ids: dict[str, int] = {}
    for entity_id, label in ((1, "base"), (3, "leaf"), (4, "other")):
        fact_ids[label] = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=entity_id,
            fact_type="DECLARED_DEFAULT",
            fact_name="Rate",
            scope_kind="DECLARED",
            declared_on_entity_id=entity_id,
            value=FactValue(
                "INTEGER",
                value_integer={"base": 1, "leaf": 3, "other": 4}[label],
            ),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri=f"bp://fixture/{label}",
            evidence_role="DEFAULT_VALUE_ACTUAL",
        )
    fact_ids["native"] = store_fact(
        connection,
        ontology=ontology,
        subject_entity_id=2,
        fact_type="NATIVE_FIELD_ACCESS",
        fact_name="ItemRating",
        scope_kind="NATIVE_REVERSED",
        declared_on_entity_id=None,
        value=FactValue("TEXT", value_text="0x20 READ"),
        status="CONFIRMED",
        confidence="HIGH",
        source_revision_id=2,
        evidence_uri="native-slice://fixture/item-rating",
        evidence_role="PROGRAM_SLICE",
    )
    materialize_effective_defaults(connection)
    for entity_id in range(1, 5):
        connection.execute(
            """
            INSERT INTO knowledge_roles VALUES (
                ?, 'entity_definition', 'HIGH', 'CONFIRMED',
                '[]', 'v1', 1
            )
            """,
            (entity_id,),
        )
        connection.execute(
            """
            INSERT INTO domain_memberships VALUES (
                ?, 'item_use', 'CLASS_ANCESTRY', 'HIGH', 'CONFIRMED',
                ?, 'v1', 1
            )
            """,
            (entity_id, f"ontology://{entity_id}"),
        )
    connection.execute(
        """
        INSERT INTO typed_registrations VALUES (
            'reg-1', '/Game/Test/Child.Child', '/Game/Test/Leaf.Leaf',
            'item_registration', 'ItemClass', 'bp://fixture/reg',
            'DECLARED', 'HIGH', 'CONFIRMED', 1, 'v1',
            'exact_source_property'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO native_functions(
            native_function_id, canonical_uri, qualified_symbol,
            module_name, rva, signature, binary_sha256, pdb_sha256,
            pdb_guid_age, recipe_ids_json, evidence_set_ids_json,
            caller_count, callee_count, callsite_status, status,
            confidence, source_revision_id
        ) VALUES (
            21, 'native://fixture/function', 'UItem::Rate', 'Shooter.dll',
            '0x10', 'void Rate()', 'binary', 'pdb', 'guid/1',
            '["recipe/v1"]', '["native-set://fixture"]', 1, 1,
            'AVAILABLE_VIA_EVIDENCE_STORE', 'CONFIRMED', 'HIGH', 2
        )
        """
    )
    connection.execute(
        """
        INSERT INTO native_blueprint_links VALUES (
            'link-1', 2, 'bp://fixture/callsite', 'Rate', 21,
            'native://fixture/function', 'verified_callsite',
            'CONFIRMED', 'HIGH', 1
        )
        """
    )
    connection.execute(
        """
        INSERT INTO projection_runs VALUES (
            'item_properties', 'v1', 'hash', 'v1',
            '2026-07-27T00:00:00Z', 1, 'VALID'
        )
        """
    )
    connection.commit()
    rebuild_invalidation_dependencies(connection)
    return connection, fact_ids


class KnowledgeInvalidationTests(unittest.TestCase):
    def test_candidate_evidence_from_selected_and_rejected_facts_invalidates_effective_entity(
        self,
    ):
        connection, fact_ids = _fixture()
        connection.execute(
            """
            INSERT INTO fact_evidence VALUES (
                ?, 2, 'bp://fixture/rejected/revision-2',
                'DEFAULT_VALUE'
            )
            """,
            (fact_ids["base"],),
        )
        materialize_effective_defaults(
            connection,
            changed_fact_ids=[fact_ids["base"]],
        )
        rebuild_invalidation_dependencies(connection)

        revisions = {
            int(row[0])
            for row in connection.execute(
                """
                SELECT upstream_revision_id
                FROM invalidation_dependencies
                WHERE downstream_kind='EFFECTIVE_ENTITY'
                  AND downstream_id=3
                  AND dependency_reason='EFFECTIVE_FACT_SOURCE'
                """
            )
        }
        self.assertEqual(revisions, {1, 2})
        connection.close()

    def test_parser_edge_revision_invalidates_only_entities_using_that_path(
        self,
    ):
        connection, _ = _fixture()

        plan = plan_invalidation(
            connection,
            event_kind="PARSER",
            upstream_revision_id=3,
        )

        self.assertEqual(plan.downstream["EFFECTIVE_ENTITY"], (2, 3))
        self.assertNotIn("FACT", plan.downstream)
        self.assertEqual(
            {
                (int(row[0]), str(row[1]))
                for row in connection.execute(
                    """
                    SELECT downstream_id, dependency_reason
                    FROM invalidation_dependencies
                    WHERE upstream_revision_id=3
                      AND downstream_kind='EFFECTIVE_ENTITY'
                    """
                )
            },
            {
                (2, "EFFECTIVE_SELECTED_CLASS_EDGE"),
                (2, "EFFECTIVE_NATIVE_ROOT_CLASS_EDGE"),
                (3, "EFFECTIVE_NATIVE_ROOT_CLASS_EDGE"),
            },
        )
        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T00:30:00Z",
        )
        self.assertEqual(
            {
                int(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT entity_id FROM effective_facts"
                )
            },
            {1, 4},
        )
        self.assertEqual(
            {
                int(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT entity_id FROM effective_fact_candidates"
                )
            },
            {1, 4},
        )
        connection.close()

    def test_native_root_edge_revision_invalidates_effective_closure_proof(
        self,
    ):
        connection, _ = _fixture()

        plan = plan_invalidation(
            connection,
            event_kind="NATIVE",
            upstream_revision_id=4,
        )

        self.assertEqual(
            plan.downstream["EFFECTIVE_ENTITY"],
            (1, 2, 3, 4),
        )
        self.assertNotIn("FACT", plan.downstream)
        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T00:31:00Z",
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM effective_facts"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM effective_fact_candidates"
            ).fetchone()[0],
            0,
        )
        connection.close()

    def test_native_root_class_revision_invalidates_effective_closure_proof(
        self,
    ):
        connection, _ = _fixture()

        plan = plan_invalidation(
            connection,
            event_kind="NATIVE",
            upstream_revision_id=5,
        )

        self.assertEqual(
            plan.downstream["EFFECTIVE_ENTITY"],
            (1, 2, 3, 4),
        )
        self.assertNotIn("FACT", plan.downstream)
        self.assertEqual(
            {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT dependency_reason
                    FROM invalidation_dependencies
                    WHERE upstream_revision_id=5
                      AND downstream_kind='EFFECTIVE_ENTITY'
                    """
                )
            },
            {"EFFECTIVE_NATIVE_ROOT_SOURCE"},
        )
        connection.close()

    def test_missing_native_root_proof_fails_closed_without_erasing_dependencies(
        self,
    ):
        connection, _ = _fixture()
        dependency_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE effective_facts
            SET resolution_chain_json=json_remove(
                resolution_chain_json, '$.nativeRootProof'
            )
            WHERE entity_id=2
              AND fact_type='EFFECTIVE_DEFAULT'
              AND fact_name='Rate'
            """
        )

        with self.assertRaisesRegex(ValueError, "resolution path"):
            rebuild_invalidation_dependencies(connection)

        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0],
            dependency_count,
        )
        connection.close()

    def test_stale_native_root_revision_requeues_parent_chain_open_entities(
        self,
    ):
        connection, _ = _fixture()
        connection.execute(
            """
            UPDATE source_revisions
            SET freshness_status='STALE'
            WHERE revision_id=5
            """
        )
        materialize_effective_defaults(connection)
        rebuild_invalidation_dependencies(connection)
        self.assertEqual(
            {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT resolution_status FROM effective_facts"
                )
            },
            {"PARENT_CHAIN_OPEN"},
        )

        plan = plan_invalidation(
            connection,
            event_kind="NATIVE",
            upstream_revision_id=5,
        )

        self.assertEqual(
            plan.downstream["EFFECTIVE_ENTITY"],
            (1, 2, 3, 4),
        )
        connection.execute(
            """
            UPDATE source_revisions
            SET freshness_status='FRESH'
            WHERE revision_id=5
            """
        )
        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T00:32:00Z",
        )
        materialize_effective_defaults(
            connection,
            affected_entity_ids=plan.downstream["EFFECTIVE_ENTITY"],
        )
        self.assertEqual(
            {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT resolution_status FROM effective_facts"
                )
            },
            {"RESOLVED"},
        )
        connection.close()

    def test_unresolved_inherited_owner_fails_before_erasing_dependencies(
        self,
    ):
        connection, _ = _fixture()
        connection.execute(
            """
            UPDATE source_revisions
            SET freshness_status='STALE'
            WHERE revision_id=5
            """
        )
        materialize_effective_defaults(connection)
        rebuild_invalidation_dependencies(connection)
        dependency_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE effective_facts
            SET inherited_from_entity_id=1
            WHERE entity_id=2
              AND fact_type='EFFECTIVE_DEFAULT'
              AND fact_name='Rate'
              AND resolution_status='PARENT_CHAIN_OPEN'
            """
        )

        with self.assertRaisesRegex(ValueError, "unresolved facts"):
            rebuild_invalidation_dependencies(connection)

        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0],
            dependency_count,
        )
        connection.close()

    def test_removed_ambiguous_edge_revision_requeues_only_its_descendants(
        self,
    ):
        connection, _ = _fixture()
        connection.execute(
            """
            INSERT INTO classes(
                class_id, class_path, class_name, module_or_package,
                class_kind, is_native, source_revision_id,
                status, confidence
            ) VALUES (
                15, '/Game/Test/Alternate.Alternate_C', 'Alternate_C',
                '/Game/Test', 'BLUEPRINT_GENERATED_CLASS', 0, 1,
                'IDENTIFIED', 'HIGH'
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO class_edges VALUES (
                ?, ?, ?, ?, ?, 'CONFIRMED', 'HIGH'
            )
            """,
            [
                (
                    12,
                    15,
                    "blueprint_parent",
                    "edge://child/alternate",
                    6,
                ),
                (
                    15,
                    100,
                    "native_parent",
                    "edge://alternate/root",
                    4,
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO class_closure VALUES (?, ?, ?, ?)",
            [
                (15, 15, 0, "SELF"),
                (100, 15, 1, "CONFIRMED"),
                (15, 12, 1, "CONFIRMED"),
                (15, 13, 2, "CONFIRMED"),
            ],
        )
        materialize_effective_defaults(connection)
        rebuild_invalidation_dependencies(connection)
        self.assertEqual(
            {
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT entity_id
                    FROM effective_facts
                    WHERE resolution_status='AMBIGUOUS_INHERITANCE'
                    """
                )
            },
            {2, 3},
        )

        plan = plan_invalidation(
            connection,
            event_kind="PARSER",
            upstream_revision_id=6,
        )

        self.assertEqual(plan.downstream["EFFECTIVE_ENTITY"], (2, 3))
        connection.execute(
            """
            DELETE FROM class_edges
            WHERE child_class_id=12 AND parent_class_id=15
            """
        )
        connection.execute(
            """
            DELETE FROM class_closure
            WHERE ancestor_class_id=15
              AND descendant_class_id IN (12, 13)
            """
        )
        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T00:33:00Z",
        )
        materialize_effective_defaults(
            connection,
            affected_entity_ids=plan.downstream["EFFECTIVE_ENTITY"],
        )
        self.assertEqual(
            {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT resolution_status
                    FROM effective_facts
                    WHERE entity_id IN (2, 3)
                    """
                )
            },
            {"RESOLVED"},
        )
        connection.close()

    def test_assignment_path_mismatch_fails_before_erasing_dependencies(
        self,
    ):
        connection, _ = _fixture()
        dependency_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE asset_class_assignments
            SET class_id=14
            WHERE entity_id=2
              AND assignment_kind='GENERATED_CLASS'
            """
        )

        with self.assertRaisesRegex(ValueError, "assignment"):
            rebuild_invalidation_dependencies(connection)

        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0],
            dependency_count,
        )
        connection.close()

    def test_selected_candidate_mismatch_fails_before_erasing_dependencies(
        self,
    ):
        connection, _ = _fixture()
        dependency_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE effective_fact_candidates
            SET selected=0, rejection_reason='TAMPERED'
            WHERE entity_id=2
              AND fact_type='EFFECTIVE_DEFAULT'
              AND fact_name='Rate'
              AND selected=1
            """
        )

        with self.assertRaisesRegex(ValueError, "selected candidate"):
            rebuild_invalidation_dependencies(connection)

        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0],
            dependency_count,
        )
        connection.close()

    def test_revision_hash_mismatch_fails_before_erasing_dependencies(self):
        connection, _ = _fixture()
        dependency_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE effective_facts
            SET source_revision_set_hash='tampered'
            WHERE entity_id=2
              AND fact_type='EFFECTIVE_DEFAULT'
              AND fact_name='Rate'
            """
        )

        with self.assertRaisesRegex(ValueError, "revision set hash"):
            rebuild_invalidation_dependencies(connection)

        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM invalidation_dependencies"
            ).fetchone()[0],
            dependency_count,
        )
        connection.close()

    def test_dependency_rebuild_rolls_back_all_rows_on_insert_failure(self):
        connection, _ = _fixture()
        before = list(
            connection.execute(
                """
                SELECT upstream_revision_id, downstream_kind,
                       downstream_id, dependency_reason
                FROM invalidation_dependencies
                ORDER BY 1, 2, 3, 4
                """
            )
        )
        connection.execute(
            """
            CREATE TEMP TRIGGER abort_invalidation_insert
            BEFORE INSERT ON invalidation_dependencies
            BEGIN
                SELECT RAISE(ABORT, 'forced invalidation insert failure');
            END
            """
        )

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced invalidation insert failure",
        ):
            rebuild_invalidation_dependencies(connection)

        self.assertEqual(
            list(
                connection.execute(
                    """
                    SELECT upstream_revision_id, downstream_kind,
                           downstream_id, dependency_reason
                    FROM invalidation_dependencies
                    ORDER BY 1, 2, 3, 4
                    """
                )
            ),
            before,
        )
        connection.close()

    def test_leaf_asset_change_does_not_trigger_full_rebuild(self):
        connection, fact_ids = _fixture()
        plan = plan_invalidation(
            connection, event_kind="ASSET", entity_ids=[3]
        )
        self.assertEqual(plan.downstream["FACT"], (fact_ids["leaf"],))
        self.assertEqual(plan.downstream["EFFECTIVE_ENTITY"], (3,))
        self.assertNotIn(4, plan.downstream["EFFECTIVE_ENTITY"])
        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T01:00:00Z",
        )
        current = dict(
            connection.execute(
                "SELECT fact_id, current FROM facts ORDER BY fact_id"
            )
        )
        self.assertEqual(current[fact_ids["leaf"]], 0)
        self.assertEqual(current[fact_ids["base"]], 1)
        self.assertEqual(current[fact_ids["other"]], 1)
        connection.close()

    def test_effective_invalidation_deletes_candidates_for_only_affected_entities(
        self,
    ):
        connection, fact_ids = _fixture()
        plan = plan_invalidation(
            connection, event_kind="ASSET", entity_ids=[3]
        )

        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T01:00:30Z",
        )

        self.assertEqual(
            connection.execute(
                """
                SELECT COUNT(*) FROM effective_fact_candidates
                WHERE entity_id=3
                """
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute(
                """
                SELECT COUNT(*) FROM effective_fact_candidates
                WHERE entity_id=4
                """
            ).fetchone()[0],
            1,
        )
        connection.close()

    def test_parent_change_recomputes_descendants_without_deleting_declared_facts(self):
        connection, fact_ids = _fixture()
        plan = plan_invalidation(
            connection, event_kind="CLASS", class_ids=[11]
        )
        self.assertEqual(
            plan.downstream["EFFECTIVE_ENTITY"], (1, 2, 3)
        )
        self.assertNotIn("FACT", plan.downstream)
        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T01:01:00Z",
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM facts WHERE current=1"
            ).fetchone()[0],
            len(fact_ids),
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM effective_facts WHERE entity_id=4"
            ).fetchone()[0],
            1,
        )
        connection.close()

    def test_class_change_keeps_prechange_affected_entities_after_closure_changes(
        self,
    ):
        connection, _ = _fixture()
        connection.execute(
            """
            DELETE FROM class_closure
            WHERE ancestor_class_id=11
              AND descendant_class_id IN (12, 13)
            """
        )

        plan = plan_invalidation(
            connection,
            event_kind="CLASS",
            class_ids=[11],
            affected_entity_ids=[2, 3],
        )

        self.assertEqual(
            plan.downstream["EFFECTIVE_ENTITY"],
            (1, 2, 3),
        )
        connection.close()

    def test_registry_change_is_bounded_to_named_entities(self):
        connection, _ = _fixture()
        plan = plan_invalidation(
            connection, event_kind="REGISTRY", entity_ids=[2, 3]
        )
        self.assertEqual(plan.downstream["ROLE_ENTITY"], (2, 3))
        self.assertEqual(plan.downstream["DOMAIN_ENTITY"], (2, 3))
        self.assertNotIn("FACT", plan.downstream)
        connection.close()

    def test_native_revision_stales_only_native_dependents(self):
        connection, fact_ids = _fixture()
        connection.execute(
            """
            INSERT INTO native_gold_targets(
                target_id, domain_id, qualified_symbol, expected_rva,
                recipe_id, native_function_id, status, gap_code
            ) VALUES (
                'native-gold-21', 'loot', 'UItem::Rate', '0x10',
                'ark-loot/v1', 21, 'CONFIRMED', ''
            )
            """
        )
        plan = plan_invalidation(
            connection,
            event_kind="NATIVE",
            upstream_revision_id=2,
        )
        self.assertEqual(plan.downstream["NATIVE_FUNCTION"], (21,))
        self.assertIn(fact_ids["native"], plan.downstream["FACT"])
        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T01:02:00Z",
        )
        self.assertEqual(
            connection.execute(
                "SELECT status FROM native_functions WHERE native_function_id=21"
            ).fetchone()[0],
            "STALE",
        )
        self.assertEqual(
            connection.execute(
                """
                SELECT status, gap_code
                FROM native_gold_targets
                WHERE target_id='native-gold-21'
                """
            ).fetchone(),
            ("GAP", "SOURCE_REVISION_STALE"),
        )
        self.assertEqual(
            connection.execute(
                "SELECT status FROM native_blueprint_links WHERE link_id='link-1'"
            ).fetchone()[0],
            "CANDIDATE",
        )
        self.assertEqual(
            connection.execute(
                "SELECT current FROM facts WHERE fact_id=?",
                (fact_ids["base"],),
            ).fetchone()[0],
            1,
        )
        connection.close()

    def test_native_function_only_invalidation_also_downgrades_its_links(
        self,
    ):
        connection, _ = _fixture()
        plan = InvalidationPlan(
            event_kind="NATIVE",
            upstream_revision_id=2,
            downstream={"NATIVE_FUNCTION": (21,)},
            reasons={"NATIVE_FUNCTION": "NATIVE_EVIDENCE_CHANGED"},
        )

        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T01:04:00Z",
        )

        self.assertEqual(
            connection.execute(
                """
                SELECT status
                FROM native_functions
                WHERE native_function_id=21
                """
            ).fetchone()[0],
            "STALE",
        )
        self.assertEqual(
            connection.execute(
                """
                SELECT status, confidence
                FROM native_blueprint_links
                WHERE link_id='link-1'
                """
            ).fetchone(),
            ("CANDIDATE", "LOW"),
        )
        connection.close()

    def test_blueprint_native_entity_only_invalidation_is_independent(
        self,
    ):
        connection, _ = _fixture()
        plan = InvalidationPlan(
            event_kind="NATIVE",
            upstream_revision_id=2,
            downstream={"BLUEPRINT_NATIVE_ENTITY": (2,)},
            reasons={
                "BLUEPRINT_NATIVE_ENTITY": "BLUEPRINT_GRAPH_CHANGED"
            },
        )

        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T01:05:00Z",
        )

        self.assertEqual(
            connection.execute(
                """
                SELECT status, confidence
                FROM native_blueprint_links
                WHERE link_id='link-1'
                """
            ).fetchone(),
            ("CANDIDATE", "LOW"),
        )
        self.assertEqual(
            connection.execute(
                """
                SELECT status
                FROM native_functions
                WHERE native_function_id=21
                """
            ).fetchone()[0],
            "CONFIRMED",
        )
        connection.close()

    def test_native_invalidation_uses_each_downstream_set_for_its_bindings(
        self,
    ):
        connection, _ = _fixture()
        connection.execute(
            """
            INSERT INTO native_functions
            SELECT
                22, 'native://fixture/function-22', 'UItem::Rate22',
                module_name, '0x22', 'void Rate22()',
                binary_sha256, pdb_sha256, pdb_guid_age,
                recipe_ids_json, evidence_set_ids_json,
                caller_count, callee_count, callsite_status,
                status, confidence, source_revision_id
            FROM native_functions
            WHERE native_function_id=21
            """
        )
        connection.execute(
            """
            INSERT INTO native_blueprint_links VALUES(
                'link-2', 3, 'bp://fixture/callsite-2', 'Rate22', 22,
                'native://fixture/function-22', 'verified_callsite',
                'CONFIRMED', 'HIGH', 1
            )
            """
        )
        plan = InvalidationPlan(
            event_kind="NATIVE",
            upstream_revision_id=2,
            downstream={
                "NATIVE_FUNCTION": (21,),
                "BLUEPRINT_NATIVE_ENTITY": (3, 4),
            },
            reasons={
                "NATIVE_FUNCTION": "NATIVE_EVIDENCE_CHANGED",
                "BLUEPRINT_NATIVE_ENTITY": "BLUEPRINT_GRAPH_CHANGED",
            },
        )

        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T01:06:00Z",
        )

        self.assertEqual(
            dict(
                connection.execute(
                    """
                    SELECT link_id, status
                    FROM native_blueprint_links
                    ORDER BY link_id
                    """
                )
            ),
            {"link-1": "CANDIDATE", "link-2": "CANDIDATE"},
        )
        self.assertEqual(
            dict(
                connection.execute(
                    """
                    SELECT native_function_id, status
                    FROM native_functions
                    ORDER BY native_function_id
                    """
                )
            ),
            {21: "STALE", 22: "CONFIRMED"},
        )
        connection.close()

    def test_ontology_change_preserves_raw_facts_and_stales_read_models(self):
        connection, fact_ids = _fixture()
        plan = plan_invalidation(connection, event_kind="ONTOLOGY")
        self.assertNotIn("FACT", plan.downstream)
        apply_invalidation_plan(
            connection,
            plan,
            created_at="2026-07-27T01:03:00Z",
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM facts WHERE current=1"
            ).fetchone()[0],
            len(fact_ids),
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_roles WHERE status='STALE'"
            ).fetchone()[0],
            4,
        )
        self.assertEqual(
            connection.execute(
                "SELECT validation_status FROM projection_runs"
            ).fetchone()[0],
            "STALE",
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
