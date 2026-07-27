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
from blueprint_translator.kb_vnext.benchmark import (  # noqa: E402
    build_benchmark_cases,
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

    def test_v1_candidate_schema_fails_closed_without_operational_error(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="LegacyRate",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("NUMBER", value_number=1.5),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/legacy-rate",
            evidence_role="DEFAULT_VALUE",
        )
        connection.execute(
            """
            INSERT INTO effective_facts VALUES (
                1, 'EFFECTIVE_DEFAULT', 'LegacyRate', ?, 1, '{}',
                'RESOLVED', 'hash'
            )
            """,
            (fact_id,),
        )
        connection.execute("DROP TABLE effective_fact_candidates")

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
                fact_names=("LegacyRate",),
            ),
        )

        self.assertEqual(result["route"], "EVIDENCE_REQUIRED")
        self.assertEqual(
            result["facts"][0]["candidateExplanationStatus"],
            "SCHEMA_MIGRATION_REQUIRED",
        )
        self.assertEqual(result["facts"][0]["candidates"], [])
        self.assertIn(
            "SCHEMA_MIGRATION_REQUIRED",
            {item["code"] for item in result["missingRequirements"]},
        )
        self.assertIn(
            "rebuild_core_v2_snapshot",
            {
                item["operation"]
                for item in result["recommendedProbes"]
            },
        )
        connection.close()

    def test_unresolved_effective_default_is_visible_as_a_typed_gap(self):
        connection = _fixture()
        connection.execute(
            """
            INSERT INTO effective_facts(
                entity_id, fact_type, fact_name, fact_id,
                inherited_from_entity_id, resolution_chain_json,
                resolution_status, source_revision_set_hash
            ) VALUES (
                1, 'EFFECTIVE_DEFAULT', 'MissingRate', NULL, NULL,
                '{"schema":"ark-kb-effective-path/v1","classes":[],"edges":[]}',
                'NOT_RECOVERED', 'hash'
            )
            """
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
                fact_names=("MissingRate",),
            ),
        )

        self.assertEqual(len(result["facts"]), 1)
        fact = result["facts"][0]
        self.assertEqual(fact["factName"], "MissingRate")
        self.assertIsNone(fact["factId"])
        self.assertIsNone(fact["valueKind"])
        self.assertIsNone(fact["unit"])
        self.assertIsNone(fact["status"])
        self.assertIsNone(fact["confidence"])
        self.assertEqual(fact["resolutionStatus"], "NOT_RECOVERED")
        self.assertEqual(fact["candidates"], [])
        self.assertEqual(fact["candidateTotal"], 0)
        self.assertEqual(fact["candidateReturned"], 0)
        self.assertEqual(fact["candidateOmitted"], 0)
        self.assertEqual(result["evidence"], [])
        self.assertIn(
            "MISSING_FACT",
            {item["code"] for item in result["missingRequirements"]},
        )
        connection.close()

    def test_effective_evidence_lookup_ignores_unresolved_fact_ids(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="ResolvedRate",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("NUMBER", value_number=4.0),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/resolved-rate",
            evidence_role="DEFAULT_VALUE",
        )
        connection.executemany(
            """
            INSERT INTO effective_facts(
                entity_id, fact_type, fact_name, fact_id,
                inherited_from_entity_id, resolution_chain_json,
                resolution_status, source_revision_set_hash
            ) VALUES (1, 'EFFECTIVE_DEFAULT', ?, ?, ?, '{}', ?, 'hash')
            """,
            [
                ("ResolvedRate", fact_id, 1, "RESOLVED"),
                ("UnresolvedRate", None, None, "PARENT_CHAIN_OPEN"),
            ],
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
            ),
        )

        self.assertEqual(
            {fact["factName"] for fact in result["facts"]},
            {"ResolvedRate", "UnresolvedRate"},
        )
        self.assertEqual(
            {item["factId"] for item in result["evidence"]},
            {fact_id},
        )
        connection.close()

    def test_mixed_fact_evidence_uses_fresh_proof_without_stale_veto(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'capture', 'capture://historical', 'old-sha',
                'test', 'v0', '2026-07-26T00:00:00Z', 'STALE'
            )
            """
        )
        fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="MixedEvidenceRate",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("NUMBER", value_number=4.0),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/mixed-current",
            evidence_role="DEFAULT_VALUE",
        )
        connection.execute(
            """
            INSERT INTO fact_evidence VALUES (
                ?, 2, 'bp://fixture/item/mixed-historical',
                'HISTORICAL_DEFAULT_VALUE'
            )
            """,
            (fact_id,),
        )
        connection.execute(
            """
            INSERT INTO effective_facts VALUES (
                1, 'EFFECTIVE_DEFAULT', 'MixedEvidenceRate', ?,
                NULL, '{}', 'RESOLVED', 'hash'
            )
            """,
            (fact_id,),
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
                fact_names=("MixedEvidenceRate",),
            ),
        )

        self.assertEqual(result["route"], "DB_ONLY_COMPLETE")
        self.assertEqual(result["freshness"], "FRESH")
        self.assertNotIn(
            "STALE_SOURCE",
            {item["code"] for item in result["missingRequirements"]},
        )
        self.assertEqual(
            {item["freshness"] for item in result["evidence"]},
            {"FRESH", "STALE"},
        )
        connection.close()

    def test_missing_current_effective_fact_has_unknown_freshness(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="RetiredRate",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("NUMBER", value_number=4.0),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/retired-rate",
            evidence_role="DEFAULT_VALUE",
        )
        connection.execute(
            """
            INSERT INTO effective_facts VALUES (
                1, 'EFFECTIVE_DEFAULT', 'RetiredRate', ?,
                NULL, '{}', 'RESOLVED', 'hash'
            )
            """,
            (fact_id,),
        )
        connection.execute(
            "UPDATE facts SET current=0 WHERE fact_id=?",
            (fact_id,),
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
                fact_names=("RetiredRate",),
            ),
        )

        self.assertEqual(result["route"], "EVIDENCE_REQUIRED")
        self.assertEqual(result["freshness"], "UNKNOWN")
        self.assertIsNone(result["facts"][0]["valueKind"])
        self.assertIsNone(result["facts"][0]["status"])
        self.assertIn(
            "MISSING_FACT",
            {item["code"] for item in result["missingRequirements"]},
        )
        connection.close()

    def test_effective_candidates_are_typed_bounded_and_do_not_veto_freshness(
        self,
    ):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'capture', 'capture://historical', 'old-sha',
                'test', 'v0', '2026-07-26T00:00:00Z', 'STALE'
            )
            """
        )
        selected_fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="ExplainedRate",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("INTEGER", value_integer=7),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/explained-rate",
            evidence_role="DEFAULT_VALUE",
        )
        rejected_fact_ids = [
            store_fact(
                connection,
                ontology=ontology,
                subject_entity_id=owner_id,
                fact_type="DECLARED_DEFAULT",
                fact_name="ExplainedRate",
                scope_kind="DECLARED",
                declared_on_entity_id=owner_id,
                value=FactValue("INTEGER", value_integer=20 + index),
                status="CONFIRMED",
                confidence="MEDIUM",
                source_revision_id=2,
                evidence_uri=(
                    f"bp://fixture/candidate/{owner_id}/{index}"
                ),
                evidence_role="DEFAULT_VALUE",
            )
            for index, owner_id in enumerate(
                (2, 3, 2, 3, 2, 3, 2, 3, 2),
                start=1,
            )
        ]
        connection.execute(
            """
            INSERT INTO effective_facts VALUES (
                1, 'EFFECTIVE_DEFAULT', 'ExplainedRate', ?, 1, '{}',
                'RESOLVED', 'hash'
            )
            """,
            (selected_fact_id,),
        )
        connection.execute(
            """
            INSERT INTO effective_fact_candidates VALUES (
                1, 'EFFECTIVE_DEFAULT', 'ExplainedRate', ?, 1, 0,
                'CONFIRMED', 1, ''
            )
            """,
            (selected_fact_id,),
        )
        connection.executemany(
            """
            INSERT INTO effective_fact_candidates VALUES (
                1, 'EFFECTIVE_DEFAULT', 'ExplainedRate', ?, ?, ?,
                'CONFIRMED', 0, 'SHADOWED_BY_NEARER_USABLE'
            )
            """,
            [
                (fact_id, owner_id, depth)
                for depth, (fact_id, owner_id) in enumerate(
                    zip(
                        rejected_fact_ids,
                        (2, 3, 2, 3, 2, 3, 2, 3, 2),
                        strict=True,
                    ),
                    start=1,
                )
            ],
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
                fact_names=("ExplainedRate",),
            ),
        )

        self.assertEqual(result["route"], "DB_ONLY_COMPLETE")
        self.assertEqual(result["freshness"], "FRESH")
        self.assertEqual(
            {item["factId"] for item in result["evidence"]},
            {selected_fact_id},
        )
        fact = result["facts"][0]
        self.assertEqual(fact["candidateTotal"], 10)
        self.assertEqual(fact["candidateReturned"], 8)
        self.assertEqual(fact["candidateOmitted"], 2)
        self.assertEqual(len(fact["candidates"]), 8)
        selected = fact["candidates"][0]
        self.assertEqual(selected["candidateFactId"], selected_fact_id)
        self.assertEqual(selected["declaredOnEntityId"], 1)
        self.assertEqual(
            selected["declaredOnUri"], "/Game/Test/Item.Item"
        )
        self.assertEqual(selected["inheritanceDepth"], 0)
        self.assertEqual(selected["pathStatus"], "CONFIRMED")
        self.assertTrue(selected["selected"])
        self.assertEqual(selected["rejectionReason"], "")
        self.assertEqual(selected["valueKind"], "INTEGER")
        self.assertEqual(selected["valueInteger"], 7)
        self.assertEqual(selected["status"], "CONFIRMED")
        rejected = fact["candidates"][1]
        self.assertFalse(rejected["selected"])
        self.assertEqual(
            rejected["rejectionReason"],
            "SHADOWED_BY_NEARER_USABLE",
        )
        self.assertEqual(
            rejected["declaredOnUri"], "/Game/Test/Other.Other"
        )
        connection.close()

    def test_unresolved_effective_fact_still_explains_rejected_candidates(
        self,
    ):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        candidate_fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=2,
            fact_type="DECLARED_DEFAULT",
            fact_name="UnresolvedRate",
            scope_kind="DECLARED",
            declared_on_entity_id=2,
            value=FactValue("UNKNOWN"),
            status="NOT_RECOVERED",
            confidence="LOW",
            source_revision_id=1,
            evidence_uri="bp://fixture/other/unresolved-rate",
            evidence_role="DEFAULT_VALUE",
        )
        connection.execute(
            """
            INSERT INTO effective_facts VALUES (
                1, 'EFFECTIVE_DEFAULT', 'UnresolvedRate', NULL, NULL, '{}',
                'PARENT_CHAIN_OPEN', 'hash'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO effective_fact_candidates VALUES (
                1, 'EFFECTIVE_DEFAULT', 'UnresolvedRate', ?, 2, 1,
                'PARENT_CHAIN_OPEN', 0, 'PARENT_CHAIN_OPEN'
            )
            """,
            (candidate_fact_id,),
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
                fact_names=("UnresolvedRate",),
            ),
        )

        fact = result["facts"][0]
        self.assertIsNone(fact["factId"])
        self.assertEqual(fact["candidateTotal"], 1)
        self.assertEqual(fact["candidateReturned"], 1)
        self.assertEqual(fact["candidateOmitted"], 0)
        candidate = fact["candidates"][0]
        self.assertEqual(candidate["candidateFactId"], candidate_fact_id)
        self.assertFalse(candidate["selected"])
        self.assertEqual(
            candidate["rejectionReason"], "PARENT_CHAIN_OPEN"
        )
        self.assertEqual(candidate["valueKind"], "UNKNOWN")
        self.assertEqual(candidate["status"], "NOT_RECOVERED")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(
            {item["code"] for item in result["missingRequirements"]},
            {"PARENT_CHAIN_OPEN"},
        )
        self.assertEqual(
            result["recommendedProbes"][0]["operation"],
            "inheritance_path",
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

    def test_effective_benchmark_uses_only_resolved_typed_fresh_values(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'capture', 'capture://stale', 'stale-sha', 'test', 'v0',
                '2026-07-26T00:00:00Z', 'STALE'
            )
            """
        )
        rows = (
            (
                "CrossWired",
                FactValue(
                    "INTEGER",
                    value_text="wrong-column",
                    value_integer=7,
                ),
                "CONFIRMED",
                1,
                "RESOLVED",
            ),
            (
                "Fingerprint",
                FactValue("FINGERPRINT", value_text="sha"),
                "CONFIRMED_FINGERPRINT_ONLY",
                1,
                "RESOLVED",
            ),
            (
                "Stale",
                FactValue("NUMBER", value_number=2.0),
                "CONFIRMED",
                2,
                "RESOLVED",
            ),
            (
                "Usable",
                FactValue("NUMBER", value_number=3.0),
                "CONFIRMED",
                1,
                "RESOLVED",
            ),
        )
        for name, value, status, revision_id, resolution_status in rows:
            fact_id = store_fact(
                connection,
                ontology=ontology,
                subject_entity_id=1,
                fact_type="DECLARED_DEFAULT",
                fact_name=name,
                scope_kind="DECLARED",
                declared_on_entity_id=1,
                value=value,
                status=status,
                confidence="HIGH",
                source_revision_id=revision_id,
                evidence_uri=f"bp://fixture/item/{name.lower()}",
                evidence_role="DEFAULT_VALUE",
            )
            connection.execute(
                """
                INSERT INTO effective_facts VALUES (
                    1, 'EFFECTIVE_DEFAULT', ?, ?, 1, '{}', ?, 'hash'
                )
                """,
                (name, fact_id, resolution_status),
            )
        connection.execute(
            """
            INSERT INTO effective_facts(
                entity_id, fact_type, fact_name, fact_id,
                inherited_from_entity_id, resolution_chain_json,
                resolution_status, source_revision_set_hash
            ) VALUES (
                1, 'EFFECTIVE_DEFAULT', 'Ambiguous', NULL, NULL,
                '{"schema":"ark-kb-effective-path/v1","classes":[],"edges":[]}',
                'AMBIGUOUS_INHERITANCE', 'hash'
            )
            """
        )

        cases = build_benchmark_cases(connection)
        effective_case = next(
            case for case in cases if case.tier == "inheritance_effective"
        )

        self.assertEqual(effective_case.request["factNames"], ["Usable"])
        connection.close()


if __name__ == "__main__":
    unittest.main()
