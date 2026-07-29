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
    source_revision_is_fresh,
)
from blueprint_translator.kb_vnext.storage import (  # noqa: E402
    FULL_CORE_SCHEMA_SQL,
    SEARCH_SCHEMA_SQL,
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
    connection.execute(
        """
        INSERT INTO packages(
            package_id, package_path, mount_point, current_revision_id
        ) VALUES(1, '/Game/Test', '/Game', 1)
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
    connection.execute("UPDATE entities SET package_id=1")
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
            class_kind, is_native, source_revision_id, status, confidence
        ) VALUES (
            11, '/Game/Test/Item.Item_C', 'Item_C', '/Game/Test',
            'BLUEPRINT_GENERATED_CLASS', 0, 1, 'CONFIRMED', 'HIGH'
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
            'CONFIRMED', 'HIGH', 1
        )
        """,
        (self_class,),
    )
    connection.commit()
    return connection


def _search_fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEARCH_SCHEMA_SQL)
    connection.executemany(
        """
        INSERT INTO entity_search_meta(
            entity_id, canonical_uri, entity_kind, display_name,
            internal_name, freshness_status
        ) VALUES (?, ?, 'BLUEPRINT_ASSET', ?, ?, 'FRESH')
        """,
        [
            (1, "/Game/Test/Item.Item", "Test Item", "Item"),
            (2, "/Game/Test/Other.Other", "Other", "Other"),
            (3, "/Game/Test/Third.Third", "Third", "Third"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO search_aliases(
            alias, entity_id, alias_kind, language, confidence
        ) VALUES (?, ?, 'PLAYER_NAME', '', 'HIGH')
        """,
        [
            ("Needle Identity", 1),
            ("Shared", 2),
            ("Shared", 3),
        ],
    )
    connection.executemany(
        """
        INSERT INTO entities_fts(
            entity_id, canonical_uri, display_name, internal_name, aliases
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (1, "/Game/Test/Item.Item", "Test Item", "Item", "Needle Identity"),
            (2, "/Game/Test/Other.Other", "Other", "Other", "Shared"),
            (3, "/Game/Test/Third.Third", "Third", "Third", "Shared"),
        ],
    )
    connection.commit()
    return connection


def _insert_map_usage(
    connection: sqlite3.Connection,
    *,
    status: str = "CONFIRMED",
    freshness: str = "FRESH",
    edge_id: int = 100,
    edge_type: str = "MAP_DIRECT_REFERENCE",
    source_entity_id: int = 1,
    target_entity_id: int = 2,
) -> None:
    usage_status = "CONFIRMED" if status == "CONFIRMED" else "CANDIDATE"
    evidence_layer = {
        "MAP_DIRECT_REFERENCE": (
            "ASSET_REGISTRY_HARD_PACKAGE_DEPENDENCY"
        ),
        "MAP_PCG_DEPENDENCY": "PCG_BIOME_SERIALIZED_DEPENDENCY",
        "MAP_WORLD_PARTITION_REFERENCE": (
            "WORLD_PARTITION_EXTERNAL_ACTOR_PACKAGE_REFERENCE"
        ),
    }[edge_type]
    map_usage_id = (
        "map-usage-fixture"
        if edge_id == 100
        else f"map-usage-fixture-{edge_id}"
    )
    evidence_uri = (
        "registry-reference://fixture/map-usage"
        if edge_id == 100
        else f"registry-reference://fixture/map-usage/{edge_id}"
    )
    connection.execute(
        """
        INSERT INTO edges(
            edge_id, source_entity_id, target_entity_id,
            edge_type, edge_strength, status, confidence,
            source_revision_id, evidence_uri
        ) VALUES(
            ?, ?, ?, ?, 'HARD', ?, 'HIGH', 1, ?
        )
        """,
        (
            edge_id,
            source_entity_id,
            target_entity_id,
            edge_type,
            status,
            evidence_uri,
        ),
    )
    connection.execute(
        """
        INSERT INTO map_usage_edge_evidence VALUES(
            ?, ?, ?, ?,
            'TheIsland', 'MAP_ASSET', 'CONFIRMED', ?, ?, 0, 0, 1,
            '["/Game/Test/Other"]', 'test'
        )
        """,
        (
            map_usage_id,
            edge_id,
            f"registry-reference-fixture-{edge_id}",
            evidence_layer,
            usage_status,
            freshness,
        ),
    )
    if freshness == "STALE":
        connection.execute(
            """
            UPDATE source_revisions
            SET freshness_status='STALE'
            WHERE revision_id=1
            """
        )
    connection.commit()


def _replace_with_unfiltered_map_view(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("DROP VIEW confirmed_map_usage_edges")
    connection.execute(
        """
        CREATE VIEW confirmed_map_usage_edges AS
        SELECT
            edge.edge_id,
            edge.source_entity_id,
            edge.target_entity_id,
            edge.edge_type,
            edge.edge_strength,
            edge.status,
            edge.confidence,
            edge.source_revision_id,
            edge.evidence_uri,
            evidence.map_usage_id,
            evidence.evidence_layer,
            evidence.map_family,
            evidence.map_kind,
            evidence.source_evidence_status,
            evidence.usage_status,
            evidence.freshness_status,
            evidence.claims_complete_map_usage,
            evidence.claims_spawn_coordinates,
            evidence.evidence_count,
            evidence.evidence_examples_json
        FROM edges AS edge
        JOIN map_usage_edge_evidence AS evidence
          ON evidence.edge_id=edge.edge_id
        """
    )
    connection.commit()


class KnowledgeQueryPlannerTests(unittest.TestCase):
    def test_source_revision_sentinel_fields_are_not_fresh(self):
        revision = {
            "revisionId": 1,
            "sourceKind": "capture",
            "sourceUri": "capture://fixture",
            "sourceFingerprint": "sha",
            "producerVersion": "test",
            "schemaVersion": "v1",
            "generatedAt": "2026-07-27T00:00:00Z",
            "freshness": "FRESH",
        }
        self.assertTrue(source_revision_is_fresh(revision))

        for field in (
            "sourceKind",
            "sourceUri",
            "sourceFingerprint",
            "producerVersion",
            "schemaVersion",
            "generatedAt",
        ):
            for sentinel in (
                "UNKNOWN",
                "NOT_RECOVERED",
                "SOURCE_NOT_AVAILABLE",
            ):
                with self.subTest(field=field, sentinel=sentinel):
                    invalid = dict(revision)
                    invalid[field] = sentinel
                    self.assertFalse(source_revision_is_fresh(invalid))

    def test_source_revision_uri_requires_a_controlled_protocol(self):
        revision = {
            "revisionId": 1,
            "sourceKind": "fixture",
            "sourceUri": "fixture://source",
            "sourceFingerprint": "sha",
            "producerVersion": "test",
            "schemaVersion": "v1",
            "generatedAt": "2026-07-27T00:00:00Z",
            "freshness": "FRESH",
        }
        allowed = (
            "package:///Game/Test/BP_Item",
            "package:///Game/PrimalEarth/Effects/Particles/Impacts/Unknown/P_ImpactUnknown",
            "capture://fixture/revision",
            "discovery://ark/full-snapshot",
            "legacy-db://fixture/table/row",
            "legacy-kb://asset_catalog.sqlite",
            "ontology://ark-domains/v1",
            "parser://fixture/class-edges",
            "registry://ark/typed-registrations",
            "class://fixture/revision",
            "class-hierarchy://ark/v2",
            "classifier://ark-kb-roles/v1",
            "map-catalog://resource-nodes",
            "map-evidence://gold/revision",
            "native://fixture/function",
            "native-symbol-set://fixture/revision",
            "bp://fixture/revision",
            "blueprint-graph://fixture/callsite",
            "runtime://fixture/trace",
            "fixture://source/revision",
        )
        for source_uri in allowed:
            with self.subTest(source_uri=source_uri):
                candidate = dict(revision)
                candidate["sourceUri"] = source_uri
                self.assertTrue(source_revision_is_fresh(candidate))

        rejected = (
            "ftp://fixture/source",
            "http://fixture/source",
            "https://fixture/source",
            "file:///C:/fixture/source",
            "EventGraph",
            "UNKNOWN",
            "bp://UNKNOWN",
            "bp://fixture/%20",
            "bp://",
        )
        for source_uri in rejected:
            with self.subTest(source_uri=source_uri):
                candidate = dict(revision)
                candidate["sourceUri"] = source_uri
                self.assertFalse(source_revision_is_fresh(candidate))

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

    def test_exact_alias_uses_search_nocase_index(self):
        core = _fixture()
        search = _search_fixture()
        plan = list(
            search.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT entity_id FROM search_aliases
                WHERE alias=? COLLATE NOCASE
                ORDER BY entity_id
                LIMIT 20
                """,
                ("shared",),
            )
        )
        plan_text = " ".join(str(row[3]) for row in plan).upper()
        self.assertIn("IDX_SEARCH_ALIASES_NOCASE", plan_text)

        result = resolve_entities(
            core,
            "shared",
            search_connection=search,
        )

        self.assertEqual(
            [item["canonicalUri"] for item in result],
            [
                "/Game/Test/Other.Other",
                "/Game/Test/Third.Third",
            ],
        )
        search.close()
        core.close()

    def test_fuzzy_resolution_uses_search_fts_candidates(self):
        core = _fixture()
        search = _search_fixture()
        plan = list(
            search.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT entity_id FROM entities_fts
                WHERE entities_fts MATCH '"test" AND "ite"*'
                LIMIT 20
                """
            )
        )
        self.assertIn(
            "VIRTUAL TABLE INDEX",
            " ".join(str(row[3]) for row in plan).upper(),
        )

        result = resolve_entities(
            core,
            "Test Ite",
            search_connection=search,
        )

        self.assertEqual(
            [item["canonicalUri"] for item in result],
            ["/Game/Test/Item.Item"],
        )
        search.close()
        core.close()

    def test_explicit_canonical_miss_does_not_fall_back_to_fuzzy_search(self):
        core = _fixture()
        search = _search_fixture()

        result = resolve_entities(
            core,
            "/Game/Test/Missing.Missing",
            search_connection=search,
        )

        self.assertEqual(result, [])
        search.close()
        core.close()

    def test_plan_query_can_resolve_search_only_alias(self):
        core = _fixture()
        search = _search_fixture()

        result = plan_query(
            core,
            QueryRequirements(
                entity_query="Needle Identity",
                fact_types=("ITEM_PROPERTY",),
            ),
            search_connection=search,
        )

        self.assertEqual(result["route"], "DB_ONLY_COMPLETE")
        self.assertEqual(
            result["entity"]["canonicalUri"],
            "/Game/Test/Item.Item",
        )
        search.close()
        core.close()

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
            next(
                item["evidenceUri"]
                for item in result["evidence"]
                if item["evidenceRole"] == "DIRECT_FIELD"
            ),
            "bp://fixture/item/weight",
        )
        self.assertEqual(result["missingRequirements"], [])
        connection.close()

    def test_fact_gaps_distinguish_missing_unusable_stale_and_ambiguous(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="Opaque",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("FINGERPRINT", value_text="opaque-sha"),
            status="CONFIRMED_FINGERPRINT_ONLY",
            confidence="LOW",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/opaque",
            evidence_role="DEFAULT_VALUE_FINGERPRINT",
        )
        store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="Choice",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("UNKNOWN"),
            status="AMBIGUOUS",
            confidence="LOW",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/choice",
            evidence_role="DEFAULT_VALUE",
        )
        cases = (
            (
                QueryRequirements(
                    entity_query="Item",
                    fact_types=("LOOT_ENTRY",),
                ),
                "FACT_NOT_FOUND",
            ),
            (
                QueryRequirements(
                    entity_query="Item",
                    fact_types=("DECLARED_DEFAULT",),
                    fact_names=("Opaque",),
                ),
                "FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED",
            ),
            (
                QueryRequirements(
                    entity_query="Item",
                    fact_types=("FORMULA",),
                ),
                "FACT_STALE",
            ),
            (
                QueryRequirements(
                    entity_query="Item",
                    fact_types=("DECLARED_DEFAULT",),
                    fact_names=("Choice",),
                ),
                "FACT_AMBIGUOUS",
            ),
        )
        for requirements, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result = plan_query(connection, requirements)
                self.assertIn(
                    expected_code,
                    {
                        item["code"]
                        for item in result["missingRequirements"]
                    },
                )
                self.assertNotEqual(result["status"], "COMPLETE")
        connection.close()

    def test_fact_without_revision_evidence_never_completes(self):
        connection = _fixture()
        connection.execute(
            """
            DELETE FROM fact_evidence
            WHERE fact_id=(
                SELECT fact_id FROM facts
                WHERE fact_type='ITEM_PROPERTY' AND fact_name='Weight'
            )
            """
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("ITEM_PROPERTY",),
                answer_mode="FACT",
            ),
        )

        self.assertEqual(result["route"], "DB_PARTIAL")
        self.assertEqual(result["freshness"], "UNKNOWN")
        self.assertEqual(
            {item["code"] for item in result["missingRequirements"]},
            {"FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED"},
        )
        connection.close()

    def test_every_requested_fact_name_must_be_satisfied(self):
        connection = _fixture()

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("ITEM_PROPERTY",),
                fact_names=("Weight", "MissingWeight"),
                answer_mode="FACT",
            ),
        )

        self.assertEqual(result["route"], "DB_PARTIAL")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(
            [fact["factName"] for fact in result["facts"]],
            ["Weight"],
        )
        self.assertEqual(
            result["missingRequirements"],
            [
                {
                    "code": "FACT_NOT_FOUND",
                    "requirement": "ITEM_PROPERTY:MissingWeight",
                }
            ],
        )
        connection.close()

    def test_runtime_observations_satisfy_every_requested_fact_name(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="RUNTIME_OBSERVATION",
            fact_name="ObservedA",
            scope_kind="RUNTIME_OBSERVED",
            declared_on_entity_id=None,
            value=FactValue("TEXT", value_text="branch A"),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="runtime://fixture/item/observed-a",
            evidence_role="RUNTIME_TRACE",
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_names=("ObservedA", "ObservedB"),
                requires_runtime=True,
                answer_mode="MECHANISM",
            ),
        )

        self.assertEqual(result["route"], "DB_PARTIAL")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["freshness"], "FRESH")
        self.assertEqual(
            [fact["factName"] for fact in result["facts"]],
            ["ObservedA"],
        )
        self.assertEqual(
            result["missingRequirements"],
            [
                {
                    "code": "FACT_NOT_FOUND",
                    "requirement": "RUNTIME_OBSERVATION:ObservedB",
                }
            ],
        )
        connection.close()

    def test_conflicting_confirmed_runtime_values_are_ambiguous(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        for value, suffix in (("branch A", "a"), ("branch B", "b")):
            store_fact(
                connection,
                ontology=ontology,
                subject_entity_id=1,
                fact_type="RUNTIME_OBSERVATION",
                fact_name="GenerateBranch",
                scope_kind="RUNTIME_OBSERVED",
                declared_on_entity_id=None,
                value=FactValue("TEXT", value_text=value),
                status="CONFIRMED",
                confidence="HIGH",
                source_revision_id=1,
                evidence_uri=f"runtime://fixture/item/generate-{suffix}",
                evidence_role="RUNTIME_TRACE",
            )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_names=("GenerateBranch",),
                requires_runtime=True,
                answer_mode="MECHANISM",
            ),
        )

        self.assertNotEqual(result["status"], "COMPLETE")
        self.assertNotEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertIn(
            "FACT_AMBIGUOUS",
            {item["code"] for item in result["missingRequirements"]},
        )
        connection.close()

    def test_conflicting_confirmed_values_for_one_fact_key_are_ambiguous(
        self,
    ):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="ITEM_PROPERTY",
            fact_name="Weight",
            scope_kind="DERIVED_STATIC",
            declared_on_entity_id=1,
            value=FactValue("NUMBER", value_number=3.5),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/conflicting-weight",
            evidence_role="DIRECT_FIELD",
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("ITEM_PROPERTY",),
                fact_names=("Weight",),
                answer_mode="FACT",
            ),
        )

        self.assertEqual(result["route"], "DB_PARTIAL")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(
            {fact["valueNumber"] for fact in result["facts"]},
            {2.5, 3.5},
        )
        self.assertEqual(
            result["missingRequirements"],
            [
                {
                    "code": "FACT_AMBIGUOUS",
                    "requirement": "ITEM_PROPERTY:Weight",
                }
            ],
        )
        connection.close()

    def test_malformed_number_and_nonportable_json_never_complete(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="ConflictingNumberColumns",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue(
                "NUMBER",
                value_number=4.5,
                value_integer=4,
            ),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/conflicting-number",
            evidence_role="DEFAULT_VALUE",
        )
        store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="NonPortableJson",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("JSON", value_json='{"value": NaN}'),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/nonportable-json",
            evidence_role="DEFAULT_VALUE",
        )

        for fact_name in (
            "ConflictingNumberColumns",
            "NonPortableJson",
        ):
            with self.subTest(fact_name=fact_name):
                result = plan_query(
                    connection,
                    QueryRequirements(
                        entity_query="Item",
                        fact_types=("DECLARED_DEFAULT",),
                        fact_names=(fact_name,),
                        answer_mode="FACT",
                    ),
                )
                self.assertEqual(result["route"], "DB_PARTIAL")
                self.assertEqual(result["status"], "PARTIAL")
                self.assertEqual(
                    result["missingRequirements"],
                    [
                        {
                            "code": (
                                "FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED"
                            ),
                            "requirement": (
                                f"DECLARED_DEFAULT:{fact_name}"
                            ),
                        }
                    ],
                )
        connection.close()

    def test_entity_resolution_fails_closed_for_none_and_ambiguous_alias(self):
        connection = _fixture()
        none = plan_query(
            connection,
            QueryRequirements(
                entity_query="DoesNotExist",
                answer_mode="IDENTITY",
            ),
        )
        self.assertEqual(
            none["missingRequirements"][0]["code"], "NO_ENTITY_MATCH"
        )
        ambiguous = plan_query(
            connection,
            QueryRequirements(
                entity_query="Shared",
                answer_mode="IDENTITY",
            ),
        )
        self.assertEqual(
            ambiguous["missingRequirements"][0]["code"],
            "AMBIGUOUS_ENTITY",
        )
        self.assertEqual(len(ambiguous["entityCandidates"]), 2)
        connection.close()

    def test_identity_mode_is_explicit_and_never_uses_semantic_db_route(self):
        connection = _fixture()

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                answer_mode="IDENTITY",
            ),
        )

        self.assertEqual(result["answerMode"], "IDENTITY")
        self.assertEqual(result["route"], "IDENTITY_ONLY_COMPLETE")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["relationships"], [])
        self.assertEqual(result["missingRequirements"], [])
        connection.close()

    def test_untrusted_entity_status_or_confidence_never_completes_identity(
        self,
    ):
        for status, confidence in (
            ("AMBIGUOUS", "HIGH"),
            ("CONFIRMED", "LOW"),
        ):
            with self.subTest(status=status, confidence=confidence):
                connection = _fixture()
                connection.execute(
                    """
                    UPDATE entities
                    SET status=?, confidence=?
                    WHERE entity_id=1
                    """,
                    (status, confidence),
                )

                result = plan_query(
                    connection,
                    QueryRequirements(
                        entity_query="/Game/Test/Item.Item",
                        answer_mode="IDENTITY",
                    ),
                )

                self.assertNotEqual(
                    result["route"],
                    "IDENTITY_ONLY_COMPLETE",
                )
                self.assertNotEqual(result["status"], "COMPLETE")
                self.assertTrue(result["missingRequirements"])
                connection.close()

    def test_missing_answer_mode_and_semantic_requirements_is_underspecified(
        self,
    ):
        connection = _fixture()

        result = plan_query(
            connection,
            QueryRequirements(entity_query="/Game/Test/Item.Item"),
        )

        self.assertIsNone(result["answerMode"])
        self.assertEqual(result["route"], "EVIDENCE_REQUIRED")
        self.assertEqual(result["status"], "GAP")
        self.assertEqual(
            result["missingRequirements"],
            [
                {
                    "code": "REQUEST_UNDERSPECIFIED",
                    "requirement": (
                        "answerMode=IDENTITY or a semantic requirement"
                    ),
                }
            ],
        )
        self.assertEqual(result["recommendedProbes"], [])
        connection.close()

    def test_identity_reports_linked_revision_freshness_without_assuming_fresh(
        self,
    ):
        connection = _fixture()
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'registry', 'registry://historical', 'old-sha',
                'test', 'v0', '2026-07-26T00:00:00Z', 'STALE'
            )
            """
        )
        connection.execute(
            """
            UPDATE packages SET current_revision_id=2
            WHERE package_id=1
            """
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                answer_mode="IDENTITY",
            ),
        )

        self.assertEqual(result["route"], "DB_PARTIAL")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["freshness"], "STALE")
        self.assertEqual(
            {item["code"] for item in result["missingRequirements"]},
            {"STALE_SOURCE"},
        )
        self.assertEqual(result["entity"]["freshness"], "STALE")
        self.assertEqual(
            result["entity"]["sourceRevision"],
            {
                "revisionId": 2,
                "sourceKind": "registry",
                "sourceUri": "registry://historical",
                "sourceFingerprint": "old-sha",
                "producerVersion": "test",
                "schemaVersion": "v0",
                "generatedAt": "2026-07-26T00:00:00Z",
                "freshness": "STALE",
            },
        )
        self.assertEqual(
            result["evidence"],
            [
                {
                    "entityId": 1,
                    "canonicalUri": "/Game/Test/Item.Item",
                    "evidenceUri": "registry://historical",
                    "evidenceRole": "IDENTITY_REVISION",
                    "sourceRevisionId": 2,
                    "sourceRevision": {
                        "revisionId": 2,
                        "sourceKind": "registry",
                        "sourceUri": "registry://historical",
                        "sourceFingerprint": "old-sha",
                        "producerVersion": "test",
                        "schemaVersion": "v0",
                        "generatedAt": "2026-07-26T00:00:00Z",
                        "freshness": "STALE",
                    },
                    "freshness": "STALE",
                }
            ],
        )
        fact_result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                fact_types=("ITEM_PROPERTY",),
                answer_mode="FACT",
            ),
        )
        self.assertEqual(fact_result["route"], "DB_PARTIAL")
        self.assertEqual(fact_result["status"], "PARTIAL")
        self.assertEqual(fact_result["freshness"], "STALE")
        self.assertIn(
            "STALE_SOURCE",
            {
                item["code"]
                for item in fact_result["missingRequirements"]
            },
        )
        connection.close()

    def test_stale_entity_or_fact_status_controls_top_level_freshness(self):
        entity_connection = _fixture()
        entity_connection.execute(
            "UPDATE entities SET status='STALE' WHERE entity_id=1"
        )

        entity_result = plan_query(
            entity_connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                answer_mode="IDENTITY",
            ),
        )

        self.assertEqual(entity_result["status"], "PARTIAL")
        self.assertEqual(entity_result["freshness"], "STALE")
        self.assertEqual(entity_result["entity"]["status"], "STALE")
        self.assertEqual(
            {item["code"] for item in entity_result["missingRequirements"]},
            {"STALE_SOURCE"},
        )
        entity_connection.close()

        fact_connection = _fixture()
        fact_result = plan_query(
            fact_connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                fact_types=("FORMULA",),
                fact_names=("Cost",),
                answer_mode="FACT",
            ),
        )

        self.assertEqual(fact_result["status"], "PARTIAL")
        self.assertEqual(fact_result["freshness"], "STALE")
        self.assertEqual(fact_result["facts"][0]["status"], "STALE")
        self.assertEqual(
            {item["code"] for item in fact_result["missingRequirements"]},
            {"FACT_STALE"},
        )
        fact_connection.close()

    def test_answer_mode_must_match_the_requested_semantic_kind(self):
        connection = _fixture()

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                edge_types=("USES_ITEM",),
                answer_mode="FACT",
            ),
        )

        self.assertEqual(result["status"], "GAP")
        self.assertEqual(
            result["missingRequirements"][0]["code"],
            "REQUEST_MODE_MISMATCH",
        )
        self.assertEqual(result["recommendedProbes"], [])
        connection.close()

    def test_missing_multi_edge_requirement_is_aggregated_once(self):
        connection = _fixture()

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                edge_types=("USES_ITEM", "OWNS_COMPONENT"),
                answer_mode="RELATIONSHIP",
            ),
        )

        reference_gaps = [
            item
            for item in result["missingRequirements"]
            if item["code"] == "REFERENCE_CLOSURE_OPEN"
        ]
        self.assertEqual(
            reference_gaps,
            [
                {
                    "code": "REFERENCE_CLOSURE_OPEN",
                    "requirement": (
                        "USES_ITEM, OWNS_COMPONENT:"
                        "confirmed edge evidence"
                    ),
                }
            ],
        )
        self.assertEqual(
            [
                probe["reason"]
                for probe in result["recommendedProbes"]
            ],
            ["REFERENCE_CLOSURE_OPEN"],
        )
        connection.close()

    def test_fact_requires_recovered_evidence_uri(self):
        for evidence_uri in ("UNKNOWN", "not-a-uri", "ftp://fixture/fact"):
            with self.subTest(evidence_uri=evidence_uri):
                connection = _fixture()
                connection.execute(
                    """
                    UPDATE fact_evidence
                    SET evidence_uri=?
                    WHERE fact_id=(
                        SELECT fact_id
                        FROM facts
                        WHERE fact_type='ITEM_PROPERTY'
                          AND fact_name='Weight'
                    )
                    """,
                    (evidence_uri,),
                )

                result = plan_query(
                    connection,
                    QueryRequirements(
                        entity_query="/Game/Test/Item.Item",
                        fact_types=("ITEM_PROPERTY",),
                        fact_names=("Weight",),
                        answer_mode="FACT",
                    ),
                )

                self.assertEqual(result["status"], "PARTIAL")
                self.assertNotEqual(
                    result["route"],
                    "DB_SEMANTIC_COMPLETE",
                )
                self.assertEqual(
                    {
                        item["code"]
                        for item in result["missingRequirements"]
                    },
                    {"FACT_STALE"},
                )
                connection.close()

    def test_relationship_requires_usable_status_fresh_revision_and_evidence(
        self,
    ):
        cases = (
            ("CONFIRMED", 1, "bp://fixture/edge/confirmed", "COMPLETE", set()),
            (
                "CANDIDATE",
                1,
                "bp://fixture/edge/candidate",
                "PARTIAL",
                {"REFERENCE_CLOSURE_OPEN"},
            ),
            (
                "LEGACY_UNVERIFIED",
                1,
                "bp://fixture/edge/legacy",
                "PARTIAL",
                {"REFERENCE_CLOSURE_OPEN"},
            ),
            (
                "CONFIRMED",
                2,
                "bp://fixture/edge/stale",
                "PARTIAL",
                {"STALE_SOURCE"},
            ),
            (
                "CONFIRMED",
                1,
                "",
                "PARTIAL",
                {"REFERENCE_CLOSURE_OPEN"},
            ),
            (
                "CONFIRMED",
                1,
                "UNKNOWN",
                "PARTIAL",
                {"REFERENCE_CLOSURE_OPEN"},
            ),
            (
                "CONFIRMED",
                1,
                "not-a-uri",
                "PARTIAL",
                {"REFERENCE_CLOSURE_OPEN"},
            ),
            (
                "CONFIRMED",
                1,
                "ftp://fixture/edge",
                "PARTIAL",
                {"REFERENCE_CLOSURE_OPEN"},
            ),
        )
        for edge_status, revision_id, evidence_uri, status, gap_codes in cases:
            with self.subTest(
                edge_status=edge_status,
                revision_id=revision_id,
                evidence_uri=evidence_uri,
            ):
                connection = _fixture()
                connection.execute(
                    """
                    INSERT INTO source_revisions VALUES (
                        2, 'capture', 'capture://stale-edge', 'old-sha',
                        'test', 'v0', '2026-07-26T00:00:00Z', 'STALE'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO edges(
                        edge_id, source_entity_id, target_entity_id,
                        edge_type, edge_strength, status, confidence,
                        source_revision_id, evidence_uri
                    ) VALUES (1, 1, 2, 'USES_ITEM', 'DIRECT', ?, 'HIGH', ?, ?)
                    """,
                    (edge_status, revision_id, evidence_uri),
                )

                result = plan_query(
                    connection,
                    QueryRequirements(
                        entity_query="/Game/Test/Item.Item",
                        edge_types=("USES_ITEM",),
                        answer_mode="RELATIONSHIP",
                    ),
                )

                self.assertEqual(result["answerMode"], "RELATIONSHIP")
                self.assertEqual(result["status"], status)
                self.assertEqual(
                    result["route"],
                    (
                        "DB_SEMANTIC_COMPLETE"
                        if status == "COMPLETE"
                        else "DB_PARTIAL"
                    ),
                )
                self.assertEqual(
                    {
                        item["code"]
                        for item in result["missingRequirements"]
                    },
                    gap_codes,
                )
                relationship = result["relationships"][0]
                self.assertEqual(relationship["sourceRevisionId"], revision_id)
                self.assertEqual(
                    relationship["sourceRevision"]["revisionId"],
                    revision_id,
                )
                self.assertEqual(
                    relationship["freshness"],
                    "STALE" if revision_id == 2 else "FRESH",
                )
                self.assertEqual(
                    relationship["evidence"][0]["evidenceUri"],
                    evidence_uri,
                )
                self.assertEqual(result["freshness"], relationship["freshness"])
                self.assertEqual(
                    next(
                        item["edgeId"]
                        for item in result["evidence"]
                        if "edgeId" in item
                    ),
                    1,
                )
                connection.close()

    def test_asset_class_assignment_is_a_typed_relationship(self):
        connection = _fixture()
        connection.execute(
            """
            INSERT INTO classes(
                class_id, class_path, class_name, module_or_package,
                class_kind, is_native, source_revision_id,
                status, confidence
            ) VALUES (
                12, '/Script/Engine.Blueprint', 'Blueprint', 'Engine',
                'NATIVE_CLASS', 1, 1, 'CONFIRMED', 'HIGH'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO asset_class_assignments(
                entity_id, class_id, assignment_kind, evidence_uri,
                status, confidence, source_revision_id
            ) VALUES (
                1, 12, 'ASSET_CLASS',
                'discovery://asset/%2FGame%2FTest%2FItem.Item#asset-class',
                'EXTRACTED', 'HIGH', 1
            )
            """
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                edge_types=("ASSET_CLASS",),
                answer_mode="RELATIONSHIP",
            ),
        )

        self.assertEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["missingRequirements"], [])
        self.assertEqual(len(result["relationships"]), 1)
        relationship = result["relationships"][0]
        self.assertEqual(relationship["edgeType"], "ASSET_CLASS")
        self.assertEqual(
            relationship["targetUri"],
            "/Script/Engine.Blueprint",
        )
        self.assertEqual(relationship["status"], "CONFIRMED")
        self.assertEqual(
            relationship["sourceEvidenceStatus"],
            "EXTRACTED",
        )
        self.assertEqual(relationship["freshness"], "FRESH")
        self.assertEqual(
            relationship["evidenceUri"],
            "discovery://asset/%2FGame%2FTest%2FItem.Item#asset-class",
        )
        connection.close()

    def test_confirmed_entity_ref_fact_is_a_typed_component_relationship(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="HARVEST_RULE",
            fact_name="DeathHarvestingComponent",
            scope_kind="DERIVED_STATIC",
            declared_on_entity_id=1,
            value=FactValue(
                "ENTITY_REF",
                value_text="/Game/Test/Other.Other_C",
            ),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/default/DeathHarvestingComponent",
            evidence_role="DIRECT_FIELD",
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                edge_types=("OWNS_COMPONENT",),
                answer_mode="RELATIONSHIP",
            ),
        )

        self.assertEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["missingRequirements"], [])
        self.assertEqual(len(result["relationships"]), 1)
        relationship = result["relationships"][0]
        self.assertEqual(relationship["edgeType"], "OWNS_COMPONENT")
        self.assertEqual(
            relationship["targetUri"],
            "/Game/Test/Other.Other_C",
        )
        self.assertEqual(relationship["status"], "CONFIRMED")
        self.assertEqual(relationship["freshness"], "FRESH")
        self.assertEqual(
            relationship["evidenceUri"],
            "bp://fixture/default/DeathHarvestingComponent",
        )
        connection.execute(
            """
            UPDATE facts
            SET value_text='UNKNOWN'
            WHERE fact_type='HARVEST_RULE'
              AND fact_name='DeathHarvestingComponent'
            """
        )
        invalid_target = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                edge_types=("OWNS_COMPONENT",),
                answer_mode="RELATIONSHIP",
            ),
        )
        self.assertEqual(invalid_target["route"], "DB_PARTIAL")
        self.assertEqual(
            invalid_target["relationships"][0]["status"],
            "NOT_RECOVERED",
        )
        self.assertEqual(
            {
                item["code"]
                for item in invalid_target["missingRequirements"]
            },
            {"REFERENCE_CLOSURE_OPEN"},
        )
        connection.close()

    def test_complete_relationship_answer_does_not_leak_candidate_rows(self):
        connection = _fixture()
        connection.executemany(
            """
            INSERT INTO edges(
                edge_id, source_entity_id, target_entity_id,
                edge_type, edge_strength, status, confidence,
                source_revision_id, evidence_uri
            ) VALUES (?, 1, ?, 'USES_ITEM', 'DIRECT', ?, ?, 1, ?)
            """,
            [
                (
                    1,
                    2,
                    "CANDIDATE",
                    "LOW",
                    "bp://fixture/edge/candidate",
                ),
                (
                    2,
                    3,
                    "CONFIRMED",
                    "HIGH",
                    "bp://fixture/edge/confirmed",
                ),
            ],
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                edge_types=("USES_ITEM",),
                answer_mode="RELATIONSHIP",
            ),
        )

        self.assertEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(
            [item["status"] for item in result["relationships"]],
            ["CONFIRMED"],
        )
        self.assertEqual(
            [
                item["edgeId"]
                for item in result["evidence"]
                if "edgeId" in item
            ],
            [2],
        )
        connection.close()

    def test_relationship_gate_is_not_truncated_by_evidence_limit(self):
        connection = _fixture()
        connection.executemany(
            """
            INSERT INTO edges(
                edge_id, source_entity_id, target_entity_id,
                edge_type, edge_strength, status, confidence,
                source_revision_id, evidence_uri
            ) VALUES (?, 1, ?, 'USES_ITEM', 'DIRECT', ?, ?, 1, ?)
            """,
            [
                (
                    1,
                    2,
                    "CANDIDATE",
                    "LOW",
                    "bp://fixture/edge/candidate-first",
                ),
                (
                    2,
                    3,
                    "CONFIRMED",
                    "HIGH",
                    "bp://fixture/edge/confirmed-second",
                ),
            ],
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Item.Item",
                edge_types=("USES_ITEM",),
                answer_mode="RELATIONSHIP",
                evidence_limit=1,
            ),
        )

        self.assertEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(
            [
                (item["edgeId"], item["status"], item["confidence"])
                for item in result["relationships"]
            ],
            [(2, "CONFIRMED", "HIGH")],
        )
        connection.close()

    def test_map_requires_confirmed_typed_usage_not_domain_membership(self):
        connection = _fixture()
        connection.execute(
            """
            INSERT INTO domain_memberships VALUES(
                2, 'map', 'NAMESPACE', 'HIGH', 'CONFIRMED',
                'namespace://fixture/map', 'test', 1
            )
            """
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Other.Other",
                requires_map_evidence=True,
                answer_mode="MECHANISM",
            ),
        )

        self.assertEqual(result["status"], "GAP")
        self.assertEqual(result["relationships"], [])
        self.assertEqual(
            {item["code"] for item in result["missingRequirements"]},
            {"MAP_USAGE_INCOMPLETE"},
        )
        connection.close()

    def test_explicit_map_edge_type_cannot_bypass_typed_map_evidence(self):
        connection = _fixture()
        connection.execute(
            """
            INSERT INTO edges(
                edge_id, source_entity_id, target_entity_id,
                edge_type, edge_strength, status, confidence,
                source_revision_id, evidence_uri
            ) VALUES(
                100, 1, 2, 'MAP_DIRECT_REFERENCE', 'HARD',
                'CONFIRMED', 'HIGH', 1,
                'registry-reference://fixture/map-usage'
            )
            """
        )
        requirements = QueryRequirements(
            entity_query="/Game/Test/Other.Other",
            edge_types=("MAP_DIRECT_REFERENCE",),
            answer_mode="RELATIONSHIP",
        )

        raw_only = plan_query(connection, requirements)

        self.assertEqual(raw_only["route"], "EVIDENCE_REQUIRED")
        self.assertEqual(raw_only["status"], "GAP")
        self.assertEqual(raw_only["relationships"], [])
        self.assertEqual(
            raw_only["missingRequirements"],
            [
                {
                    "code": "MAP_USAGE_INCOMPLETE",
                    "requirement": (
                        "MAP_DIRECT_REFERENCE: confirmed typed direct, "
                        "PCG, or World Partition map usage"
                    ),
                }
            ],
        )

        connection.execute(
            """
            INSERT INTO map_usage_edge_evidence VALUES(
                'map-usage-fixture', 100, 'registry-reference-fixture',
                'ASSET_REGISTRY_HARD_PACKAGE_DEPENDENCY',
                'TheIsland', 'MAP_ASSET', 'CONFIRMED', 'CONFIRMED',
                'FRESH', 0, 0, 1, '["/Game/Test/Other"]', 'test'
            )
            """
        )
        typed = plan_query(connection, requirements)

        self.assertEqual(typed["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(typed["status"], "COMPLETE")
        self.assertEqual(
            [item["edgeType"] for item in typed["relationships"]],
            ["MAP_DIRECT_REFERENCE"],
        )
        self.assertEqual(
            {
                item["evidenceRole"]
                for item in typed["evidence"]
                if item["evidenceRole"] == "MAP_USAGE_EVIDENCE"
            },
            {"MAP_USAGE_EVIDENCE"},
        )
        connection.close()

    def test_unfiltered_confirmed_map_view_never_completes_a_query(self):
        connection = _fixture()
        _insert_map_usage(connection)
        connection.execute(
            """
            UPDATE map_usage_edge_evidence
            SET source_evidence_status='CANDIDATE'
            WHERE edge_id=100
            """
        )
        _replace_with_unfiltered_map_view(connection)

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                edge_types=("MAP_DIRECT_REFERENCE",),
                answer_mode="RELATIONSHIP",
            ),
        )

        self.assertNotEqual(result["status"], "COMPLETE")
        self.assertNotEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertIn(
            "SCHEMA_MIGRATION_REQUIRED",
            {item["code"] for item in result["missingRequirements"]},
        )
        connection.close()

    def test_each_explicit_map_edge_type_requires_its_own_typed_evidence(
        self,
    ):
        connection = _fixture()
        _insert_map_usage(connection)
        requirements = QueryRequirements(
            entity_query="/Game/Test/Other.Other",
            edge_types=(
                "MAP_DIRECT_REFERENCE",
                "MAP_PCG_DEPENDENCY",
            ),
            answer_mode="RELATIONSHIP",
        )

        one_of_two = plan_query(connection, requirements)

        self.assertEqual(one_of_two["route"], "DB_PARTIAL")
        self.assertEqual(one_of_two["status"], "PARTIAL")
        self.assertEqual(
            {item["edgeType"] for item in one_of_two["relationships"]},
            {"MAP_DIRECT_REFERENCE"},
        )
        self.assertEqual(
            one_of_two["missingRequirements"],
            [
                {
                    "code": "MAP_USAGE_INCOMPLETE",
                    "requirement": (
                        "MAP_PCG_DEPENDENCY: confirmed typed direct, PCG, "
                        "or World Partition map usage"
                    ),
                }
            ],
        )

        _insert_map_usage(
            connection,
            edge_id=101,
            edge_type="MAP_PCG_DEPENDENCY",
        )
        both = plan_query(connection, requirements)

        self.assertEqual(both["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(both["status"], "COMPLETE")
        self.assertEqual(
            {item["edgeType"] for item in both["relationships"]},
            {"MAP_DIRECT_REFERENCE", "MAP_PCG_DEPENDENCY"},
        )
        self.assertEqual(both["missingRequirements"], [])
        connection.close()

    def test_confirmed_map_usage_returns_inbound_evidenced_relationship(self):
        connection = _fixture()
        _insert_map_usage(connection)

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Other.Other",
                requires_map_evidence=True,
                answer_mode="MECHANISM",
            ),
        )

        self.assertEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["freshness"], "FRESH")
        self.assertEqual(result["missingRequirements"], [])
        relationship = result["relationships"][0]
        self.assertEqual(relationship["direction"], "INBOUND")
        self.assertEqual(
            relationship["sourceUri"],
            "/Game/Test/Item.Item",
        )
        self.assertEqual(
            relationship["targetUri"],
            "/Game/Test/Other.Other",
        )
        self.assertEqual(relationship["edgeType"], "MAP_DIRECT_REFERENCE")
        self.assertEqual(
            relationship["evidenceLayer"],
            "ASSET_REGISTRY_HARD_PACKAGE_DEPENDENCY",
        )
        self.assertFalse(relationship["claimsCompleteMapUsage"])
        self.assertFalse(relationship["claimsSpawnCoordinates"])
        self.assertEqual(
            relationship["sourceRevision"]["freshness"],
            "FRESH",
        )
        self.assertEqual(
            next(
                item["evidenceUri"]
                for item in result["evidence"]
                if item["evidenceRole"] == "MAP_USAGE_EVIDENCE"
            ),
            "registry-reference://fixture/map-usage",
        )
        connection.close()

    def test_candidate_and_stale_map_rows_are_partial_never_complete(self):
        cases = (
            ("CANDIDATE", "FRESH", "MAP_USAGE_INCOMPLETE", "UNKNOWN"),
            ("CONFIRMED", "STALE", "STALE_SOURCE", "STALE"),
        )
        for status, freshness, gap_code, expected_freshness in cases:
            with self.subTest(status=status, freshness=freshness):
                connection = _fixture()
                _insert_map_usage(
                    connection,
                    status=status,
                    freshness=freshness,
                )

                result = plan_query(
                    connection,
                    QueryRequirements(
                        entity_query="/Game/Test/Other.Other",
                        requires_map_evidence=True,
                        answer_mode="MECHANISM",
                    ),
                )

                self.assertEqual(result["route"], "DB_PARTIAL")
                self.assertEqual(result["status"], "PARTIAL")
                self.assertEqual(result["freshness"], expected_freshness)
                self.assertEqual(
                    {item["code"] for item in result["missingRequirements"]},
                    {gap_code},
                )
                self.assertEqual(len(result["relationships"]), 1)
                self.assertNotEqual(
                    result["relationships"][0]["status"],
                    "VERIFIED",
                )
                connection.close()

    def test_pre_v4_core_reports_migration_gap_before_map_sql(self):
        connection = _fixture()
        connection.execute("DROP VIEW confirmed_map_usage_edges")
        connection.execute("DROP TABLE map_usage_edge_evidence")
        connection.execute("DROP TABLE map_usage_sources")

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="/Game/Test/Other.Other",
                requires_map_evidence=True,
                answer_mode="MECHANISM",
            ),
        )

        self.assertEqual(result["status"], "GAP")
        self.assertEqual(
            {item["code"] for item in result["missingRequirements"]},
            {"SCHEMA_MIGRATION_REQUIRED"},
        )
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
            {"FACT_STALE"},
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

    def test_stale_effective_class_evidence_blocks_completion(self):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'class-capture', 'class://historical/item', 'old-class-sha',
                'test', 'v0', '2026-07-26T00:00:00Z', 'STALE'
            )
            """
        )
        connection.execute(
            """
            UPDATE asset_class_assignments
            SET source_revision_id=2
            WHERE entity_id=1 AND assignment_kind='GENERATED_CLASS'
            """
        )
        fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="StaleClassRate",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("NUMBER", value_number=8.0),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/stale-class-rate",
            evidence_role="DEFAULT_VALUE",
        )
        connection.execute(
            """
            INSERT INTO effective_facts VALUES (
                1, 'EFFECTIVE_DEFAULT', 'StaleClassRate', ?, NULL, '{}',
                'RESOLVED', 'hash'
            )
            """,
            (fact_id,),
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                fact_types=("EFFECTIVE_DEFAULT",),
                fact_names=("StaleClassRate",),
                answer_mode="FACT",
            ),
        )

        self.assertEqual(result["route"], "DB_PARTIAL")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["freshness"], "STALE")
        self.assertEqual(
            result["missingRequirements"],
            [
                {
                    "code": "STALE_SOURCE",
                    "requirement": "fresh effective-class evidence",
                }
            ],
        )
        class_evidence = next(
            item
            for item in result["evidence"]
            if item["evidenceRole"] == "CLASS_ASSIGNMENT"
        )
        self.assertEqual(class_evidence["sourceRevisionId"], 2)
        self.assertEqual(class_evidence["freshness"], "STALE")
        connection.close()

    def test_effective_default_requires_recovered_class_evidence_uri(self):
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        for evidence_uri in ("UNKNOWN", "not-a-uri", "ftp://fixture/class"):
            with self.subTest(evidence_uri=evidence_uri):
                connection = _fixture()
                connection.execute(
                    """
                    UPDATE asset_class_assignments
                    SET evidence_uri=?
                    WHERE entity_id=1
                      AND assignment_kind='GENERATED_CLASS'
                    """,
                    (evidence_uri,),
                )
                fact_id = store_fact(
                    connection,
                    ontology=ontology,
                    subject_entity_id=1,
                    fact_type="DECLARED_DEFAULT",
                    fact_name="InvalidClassEvidenceRate",
                    scope_kind="DECLARED",
                    declared_on_entity_id=1,
                    value=FactValue("NUMBER", value_number=3.0),
                    status="CONFIRMED",
                    confidence="HIGH",
                    source_revision_id=1,
                    evidence_uri=(
                        "bp://fixture/item/"
                        "invalid-class-evidence-rate"
                    ),
                    evidence_role="DEFAULT_VALUE",
                )
                connection.execute(
                    """
                    INSERT INTO effective_facts VALUES(
                        1, 'EFFECTIVE_DEFAULT',
                        'InvalidClassEvidenceRate', ?,
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
                        fact_names=("InvalidClassEvidenceRate",),
                        answer_mode="FACT",
                    ),
                )

                self.assertEqual(result["status"], "PARTIAL")
                self.assertEqual(result["freshness"], "UNKNOWN")
                self.assertIn(
                    "PARENT_CHAIN_OPEN",
                    {
                        item["code"]
                        for item in result["missingRequirements"]
                    },
                )
                connection.close()

    def test_effective_class_freshness_gate_is_not_truncated_by_evidence_limit(
        self,
    ):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'class-capture', 'class://historical/other',
                'old-class-sha', 'test', 'v0',
                '2026-07-26T00:00:00Z', 'STALE'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO classes(
                class_id, class_path, class_name, module_or_package,
                class_kind, is_native, source_revision_id,
                status, confidence
            ) VALUES(
                12, '/Game/Test/Other.Other_C', 'Other_C', '/Game/Test',
                'BLUEPRINT_GENERATED_CLASS', 0, 2,
                'CONFIRMED', 'HIGH'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO asset_class_assignments VALUES(
                1, 12, 'SECONDARY_GENERATED_CLASS',
                'bp://fixture/class/stale-secondary',
                'CONFIRMED', 'HIGH', 2
            )
            """
        )
        fact_id = store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="DECLARED_DEFAULT",
            fact_name="EvidenceLimitedRate",
            scope_kind="DECLARED",
            declared_on_entity_id=1,
            value=FactValue("NUMBER", value_number=4.0),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="bp://fixture/item/evidence-limited-rate",
            evidence_role="DEFAULT_VALUE",
        )
        connection.execute(
            """
            INSERT INTO effective_facts VALUES(
                1, 'EFFECTIVE_DEFAULT', 'EvidenceLimitedRate', ?,
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
                fact_names=("EvidenceLimitedRate",),
                evidence_limit=1,
                answer_mode="FACT",
            ),
        )

        self.assertEqual(result["freshness"], "STALE")
        self.assertNotEqual(result["status"], "COMPLETE")
        self.assertIn(
            "STALE_SOURCE",
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
            "rebuild_core_v4_snapshot",
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
        self.assertEqual(
            {item["evidenceRole"] for item in result["evidence"]},
            {"IDENTITY_REVISION"},
        )
        self.assertIn(
            "FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED",
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
            {
                item["factId"]
                for item in result["evidence"]
                if "factId" in item
            },
            {fact_id},
        )
        self.assertIn(
            "CLASS_ASSIGNMENT",
            {item["evidenceRole"] for item in result["evidence"]},
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
            "FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED",
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
            {
                item["factId"]
                for item in result["evidence"]
                if "factId" in item
            },
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
        self.assertEqual(
            {item["evidenceRole"] for item in result["evidence"]},
            {"IDENTITY_REVISION"},
        )
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
                'CANDIDATE', 'LOW', 1
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

    def test_stale_blueprint_graph_revision_blocks_native_completion(self):
        connection = _fixture()
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                2, 'blueprint-graph', 'graph://historical/item',
                'old-graph-sha', 'test', 'v0',
                '2026-07-26T00:00:00Z', 'STALE'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO native_functions VALUES(
                1, 'native://ShooterGame/UItem.Generate',
                'UItem::Generate', 'ShooterGame', '0x10',
                'void UItem::Generate()', 'binary-sha', 'pdb-sha',
                'guid/1', '["recipe/v1"]', '["native-set://fixture"]',
                1, 1, 'AVAILABLE_VIA_EVIDENCE_STORE',
                'CONFIRMED', 'HIGH', 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO native_blueprint_links VALUES(
                'native-link-stale-graph', 1,
                'bp://fixture/item/generate-historical',
                'Generate', 1, 'native-slice://fixture/item/generate',
                'verified_callsite', 'CONFIRMED', 'HIGH', 2
            )
            """
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                requires_native=True,
                answer_mode="MECHANISM",
            ),
        )

        self.assertEqual(result["route"], "DB_PARTIAL")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["freshness"], "STALE")
        self.assertEqual(
            {item["code"] for item in result["missingRequirements"]},
            {"NATIVE_BOUNDARY_UNRESOLVED", "STALE_SOURCE"},
        )
        relationship = result["relationships"][0]
        self.assertEqual(relationship["sourceRevisionId"], 1)
        self.assertEqual(relationship["sourceRevision"]["freshness"], "FRESH")
        self.assertEqual(relationship["freshness"], "STALE")
        graph_evidence = next(
            item
            for item in result["evidence"]
            if item["evidenceRole"] == "BLUEPRINT_GRAPH_EVIDENCE"
        )
        self.assertEqual(graph_evidence["sourceRevisionId"], 2)
        self.assertEqual(
            graph_evidence["sourceRevision"],
            {
                "revisionId": 2,
                "sourceKind": "blueprint-graph",
                "sourceUri": "graph://historical/item",
                "sourceFingerprint": "old-graph-sha",
                "producerVersion": "test",
                "schemaVersion": "v0",
                "generatedAt": "2026-07-26T00:00:00Z",
                "freshness": "STALE",
            },
        )
        self.assertEqual(graph_evidence["freshness"], "STALE")
        native_evidence = next(
            item
            for item in result["evidence"]
            if item["evidenceRole"] == "NATIVE_EVIDENCE"
        )
        self.assertEqual(native_evidence["sourceRevisionId"], 1)
        self.assertEqual(native_evidence["freshness"], "FRESH")
        connection.close()

    def test_confirmed_native_and_runtime_mechanisms_return_fresh_evidence(
        self,
    ):
        connection = _fixture()
        ontology = load_ontology(PROJECT_ROOT / "ontology")
        connection.execute(
            """
            INSERT INTO native_functions VALUES(
                1, 'native://ShooterGame/UItem.Generate',
                'UItem::Generate', 'ShooterGame', '0x10',
                'void UItem::Generate()', 'binary-sha', 'pdb-sha',
                'guid/1', '["recipe/v1"]', '["native-set://fixture"]',
                1, 1, 'AVAILABLE_VIA_EVIDENCE_STORE',
                'CONFIRMED', 'HIGH', 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO native_blueprint_links VALUES(
                'native-link', 1, 'bp://fixture/item/generate',
                'Generate', 1, 'native-slice://fixture/item/generate',
                'verified_callsite', 'CONFIRMED', 'HIGH', 1
            )
            """
        )
        store_fact(
            connection,
            ontology=ontology,
            subject_entity_id=1,
            fact_type="RUNTIME_OBSERVATION",
            fact_name="GenerateBranch",
            scope_kind="RUNTIME_OBSERVED",
            declared_on_entity_id=None,
            value=FactValue("TEXT", value_text="branch A"),
            status="CONFIRMED",
            confidence="HIGH",
            source_revision_id=1,
            evidence_uri="runtime://fixture/item/generate",
            evidence_role="RUNTIME_TRACE",
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                requires_native=True,
                requires_runtime=True,
                answer_mode="MECHANISM",
            ),
        )

        self.assertEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["freshness"], "FRESH")
        self.assertEqual(result["missingRequirements"], [])
        self.assertEqual(
            result["facts"][0]["factType"],
            "RUNTIME_OBSERVATION",
        )
        native = result["relationships"][0]
        self.assertEqual(native["edgeType"], "BLUEPRINT_CALLS_NATIVE")
        self.assertEqual(native["qualifiedSymbol"], "UItem::Generate")
        self.assertEqual(native["freshness"], "FRESH")
        self.assertEqual(
            {item["evidenceRole"] for item in result["evidence"]},
            {
                "BLUEPRINT_GRAPH_EVIDENCE",
                "IDENTITY_REVISION",
                "NATIVE_EVIDENCE",
                "RUNTIME_TRACE",
            },
        )
        connection.close()

    def test_native_gate_is_not_truncated_by_evidence_limit(self):
        connection = _fixture()
        connection.execute(
            """
            INSERT INTO native_functions VALUES(
                1, 'native://ShooterGame/UItem.Generate',
                'UItem::Generate', 'ShooterGame', '0x10',
                'void UItem::Generate()', 'binary-sha', 'pdb-sha',
                'guid/1', '["recipe/v1"]', '["native-set://fixture"]',
                1, 1, 'AVAILABLE_VIA_EVIDENCE_STORE',
                'CONFIRMED', 'HIGH', 1
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO native_blueprint_links VALUES(
                ?, 1, ?, 'Generate', 1,
                'native-slice://fixture/item/generate',
                'verified_callsite', 'CONFIRMED', ?, 1
            )
            """,
            [
                ("a-low", "bp://fixture/item/low", "LOW"),
                ("z-high", "bp://fixture/item/high", "HIGH"),
            ],
        )

        result = plan_query(
            connection,
            QueryRequirements(
                entity_query="Item",
                requires_native=True,
                answer_mode="MECHANISM",
                evidence_limit=1,
            ),
        )

        self.assertEqual(result["route"], "DB_SEMANTIC_COMPLETE")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(
            [
                (item["edgeId"], item["status"], item["confidence"])
                for item in result["relationships"]
            ],
            [("z-high", "CONFIRMED", "HIGH")],
        )
        connection.close()

    def test_native_gate_rejects_invalid_graph_uri_or_function_name(self):
        cases = (
            ("UNKNOWN", "Generate"),
            ("EventGraph", "Generate"),
            ("ftp://fixture/callsite", "Generate"),
            ("blueprint-graph://unresolved/callsite", "Generate"),
            ("bp://fixture/item/generate", ""),
            ("bp://fixture/item/generate", "NOT_RECOVERED"),
        )
        for graph_uri, function_name in cases:
            with self.subTest(
                graph_uri=graph_uri,
                function_name=function_name,
            ):
                connection = _fixture()
                connection.execute(
                    """
                    INSERT INTO native_functions VALUES(
                        1, 'native://ShooterGame/UItem.Generate',
                        'UItem::Generate', 'ShooterGame', '0x10',
                        'void UItem::Generate()', 'binary-sha', 'pdb-sha',
                        'guid/1', '["recipe/v1"]',
                        '["native-set://fixture"]',
                        1, 1, 'AVAILABLE_VIA_EVIDENCE_STORE',
                        'CONFIRMED', 'HIGH', 1
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO native_blueprint_links VALUES(
                        'native-link', 1, ?, ?, 1,
                        'native-slice://fixture/item/generate',
                        'verified_callsite', 'CONFIRMED', 'HIGH', 1
                    )
                    """,
                    (graph_uri, function_name),
                )

                result = plan_query(
                    connection,
                    QueryRequirements(
                        entity_query="Item",
                        requires_native=True,
                        answer_mode="MECHANISM",
                    ),
                )

                self.assertEqual(result["route"], "DB_PARTIAL")
                self.assertEqual(result["status"], "PARTIAL")
                self.assertIn(
                    "NATIVE_BOUNDARY_UNRESOLVED",
                    {
                        item["code"]
                        for item in result["missingRequirements"]
                    },
                )
                connection.close()

if __name__ == "__main__":
    unittest.main()
