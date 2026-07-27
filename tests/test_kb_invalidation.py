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
            class_kind, is_native, status, confidence
        ) VALUES (?, ?, ?, '/Game/Test', 'BLUEPRINT_GENERATED_CLASS',
                  0, 'CONFIRMED', 'HIGH')
        """,
        [
            (11, "/Game/Test/Base.Base_C", "Base_C"),
            (12, "/Game/Test/Child.Child_C", "Child_C"),
            (13, "/Game/Test/Leaf.Leaf_C", "Leaf_C"),
            (14, "/Game/Test/Other.Other_C", "Other_C"),
        ],
    )
    connection.executemany(
        "INSERT INTO class_closure VALUES (?, ?, ?, 'CONFIRMED')",
        [
            (11, 11, 0),
            (11, 12, 1),
            (12, 12, 0),
            (11, 13, 2),
            (12, 13, 1),
            (13, 13, 0),
            (14, 14, 0),
        ],
    )
    connection.executemany(
        """
        INSERT INTO asset_class_assignments VALUES (
            ?, ?, 'GENERATED_CLASS', 'fixture://class',
            'CONFIRMED', 'HIGH'
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
            value=FactValue("FINGERPRINT", value_text=label),
            status="CONFIRMED_FINGERPRINT_ONLY",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri=f"bp://fixture/{label}",
            evidence_role="DEFAULT_VALUE_FINGERPRINT",
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
                ?, 'entity_definition', 'HIGH', 'CONFIRMED', '[]', 'v1'
            )
            """,
            (entity_id,),
        )
        connection.execute(
            """
            INSERT INTO domain_memberships VALUES (
                ?, 'item_use', 'CLASS_ANCESTRY', 'HIGH', 'CONFIRMED',
                ?, 'v1'
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
            'CONFIRMED', 'HIGH'
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
