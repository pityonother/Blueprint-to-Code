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
    store_fact,
)
from blueprint_translator.kb_vnext.ontology import load_ontology  # noqa: E402
from blueprint_translator.kb_vnext.query_planner import (  # noqa: E402
    QueryRequirements,
    plan_query,
    resolve_entities,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
)


def _fixture() -> sqlite3.Connection:
    ontology = load_ontology(PROJECT_ROOT / "ontology")
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(FULL_CORE_SCHEMA_SQL)
    connection.execute(
        """
        INSERT INTO source_revisions VALUES (
            1, 'capture', 'capture://fixture', 'sha', 'test', 'v1',
            '2026-07-27T00:00:00Z', 'FRESH'
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO entities(
            entity_id, canonical_uri, entity_kind,
            display_name, internal_name, status, confidence
        ) VALUES (?, ?, 'BLUEPRINT_ASSET', ?, ?, 'CONFIRMED', 'HIGH')
        """,
        [
            (1, "/Game/Test/Item.Item", "Test Item", "Item"),
            (2, "/Game/Test/Other.Other", "Other", "Other"),
            (3, "/Game/Test/Third.Third", "Third", "Third"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO aliases VALUES (
            ?, ?, 'PLAYER_NAME', '', 'HIGH'
        )
        """,
        [("Shared", 2), ("Shared", 3)],
    )
    store_fact(
        connection,
        ontology=ontology,
        subject_entity_id=1,
        fact_type="ITEM_PROPERTY",
        fact_name="Weight",
        scope_kind="DERIVED_STATIC",
        declared_on_entity_id=1,
        value=FactValue("NUMBER", value_number=2.5),
        status="CONFIRMED",
        confidence="HIGH",
        source_revision_id=1,
        evidence_uri="bp://fixture/item/weight",
        evidence_role="DIRECT_FIELD",
    )
    store_fact(
        connection,
        ontology=ontology,
        subject_entity_id=1,
        fact_type="FORMULA",
        fact_name="Cost",
        scope_kind="DERIVED_STATIC",
        declared_on_entity_id=1,
        value=FactValue("TEXT", value_text="x * 2"),
        status="STALE",
        confidence="LOW",
        source_revision_id=1,
        evidence_uri="bp://fixture/item/cost",
        evidence_role="FUNCTION_SUMMARY",
    )
    self_class = 11
    connection.execute(
        """
        INSERT INTO classes(
            class_id, class_path, class_name, module_or_package,
            class_kind, is_native, status, confidence
        ) VALUES (
            11, '/Game/Test/Item.Item_C', 'Item_C', '/Game/Test',
            'BLUEPRINT_GENERATED_CLASS', 0, 'CONFIRMED', 'HIGH'
        )
        """
    )
    connection.execute(
        "INSERT INTO class_closure VALUES (11, 11, 0, 'SELF')"
    )
    connection.execute(
        """
        INSERT INTO asset_class_assignments VALUES (
            1, ?, 'GENERATED_CLASS', 'bp://fixture/class',
            'CONFIRMED', 'HIGH'
        )
        """,
        (self_class,),
    )
    connection.commit()
    return connection


class KnowledgeQueryPlannerTests(unittest.TestCase):
    def test_exact_canonical_uri_uses_unique_index_fast_path(self):
        connection = _fixture()
        plan = list(
            connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT entity_id FROM entities
                WHERE canonical_uri='/Game/Test/Item.Item'
                LIMIT 1
                """
            )
        )
        self.assertIn("INDEX", " ".join(str(row[3]) for row in plan).upper())
        result = resolve_entities(
            connection,
            "/Game/Test/Item.Item",
        )
        self.assertEqual(result[0]["canonicalUri"], "/Game/Test/Item.Item")
        connection.close()

    def test_complete_fact_query_returns_db_only_with_evidence(self):
        connection = _fixture()
        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                fact_types=("ITEM_PROPERTY",),
            ),
        )
        self.assertEqual(result["route"], "DB_ONLY_COMPLETE")
        self.assertEqual(result["freshness"], "FRESH")
        self.assertEqual(result["facts"][0]["factName"], "Weight")
        self.assertEqual(
            result["evidence"][0]["evidenceUri"],
            "bp://fixture/item/weight",
        )
        self.assertEqual(result["missingRequirements"], [])
        connection.close()

    def test_entity_resolution_fails_closed_for_none_and_ambiguous_alias(self):
        connection = _fixture()
        none = plan_query(
            connection,
            QueryRequirements(entity_query="DoesNotExist"),
        )
        self.assertEqual(
            none["missingRequirements"][0]["code"], "NO_ENTITY_MATCH"
        )
        ambiguous = plan_query(
            connection,
            QueryRequirements(entity_query="Shared"),
        )
        self.assertEqual(
            ambiguous["missingRequirements"][0]["code"],
            "AMBIGUOUS_ENTITY",
        )
        self.assertEqual(len(ambiguous["entityCandidates"]), 2)
        connection.close()

    def test_stale_fact_requests_only_targeted_blueprint_probe(self):
        connection = _fixture()
        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("FORMULA",),
            ),
        )
        self.assertEqual(result["route"], "EVIDENCE_REQUIRED")
        self.assertEqual(
            {item["code"] for item in result["missingRequirements"]},
            {"STALE_SOURCE"},
        )
        self.assertEqual(
            result["recommendedProbes"][0]["probeType"],
            "blueprint_evidence_query",
        )
        self.assertLessEqual(
            result["recommendedProbes"][0]["budgetTokens"], 1500
        )
        connection.close()

    def test_open_parent_chain_blocks_effective_default_answer(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="Rate",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("NUMBER", value_number=1.0),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/rate",
            evidence_role="DEFAULT_VALUE",
        )
        connection.execute(
            """
            INSERT INTO effective_facts VALUES (
                1, 'EFFECTIVE_DEFAULT', 'Rate', ?, NULL, '{}',
                'RESOLVED', 'hash'
            )
            """,
            (fact_id,),
        )
        connection.execute(
            """
            INSERT INTO class_gaps VALUES (
                11, 'NATIVE_ROOT_NOT_REACHED', 'missing parent',
                'NOT_RECOVERED'
            )
            """
        )
        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
                fact_names=("Rate",),
            ),
        )
        self.assertIn(
            "PARENT_CHAIN_OPEN",
            {item["code"] for item in result["missingRequirements"]},
        )
        connection.close()

    def test_name_only_native_link_and_runtime_branch_remain_gaps(self):
        connection = _fixture()
        connection.execute(
            """
            INSERT INTO native_blueprint_links VALUES (
                'candidate', 1, 'bp://fixture/name', 'GenerateCrateItems',
                NULL, 'native://candidate', 'exact_simple_name_candidate',
                'CANDIDATE', 'LOW'
            )
            """
        )
        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                requires_native=True,
                requires_runtime=True,
            ),
        )
        codes = {
            item["code"] for item in result["missingRequirements"]
        }
        self.assertEqual(
            codes,
            {
                "NATIVE_BOUNDARY_UNRESOLVED",
                "RUNTIME_DYNAMIC_BRANCH",
            },
        )
        self.assertEqual(
            {
                probe["probeType"]
                for probe in result["recommendedProbes"]
            },
            {"native_recipe", "runtime_probe"},
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
