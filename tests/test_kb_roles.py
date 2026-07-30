from __future__ import annotations

import json
import math
import sqlite3
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.roles import (  # noqa: E402
    _PersistedRoleSignals,
    _collect_edge_signals,
    _fresh_revision_ids,
    classify_asset,
    enrich_type_percentiles,
    materialize_discovery_roles,
)
from blueprint_translator.kb_vnext.registrations import (  # noqa: E402
    create_registration_tables,
)


def _base_asset(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "object_path": "/Game/Test/BP_Test.BP_Test",
        "asset_class_path": "/Script/Engine.Blueprint",
        "generated_class_path": "/Game/Test/BP_Test.BP_Test_C",
        "parent_class_path": "/Script/Engine.Actor",
        "native_parent_class_path": "/Script/Engine.Actor",
        "identity_status": "CONFIRMED",
        "identity_confidence": "HIGH",
        "is_blueprint": 1,
        "is_data_asset": 0,
        "is_data_table": 0,
        "is_actor_component": 0,
        "is_function_library": 0,
        "is_blueprint_interface": 0,
        "is_map": 0,
        "capture_exists": 0,
        "evidence_freshness": "NOT_MEASURED",
        "parse_status": "NOT_MEASURED",
        "descendant_count": 0,
        "referencer_count": 0,
        "component_reuse_count": 0,
        "cross_domain_reference_count": 0,
        "confirmed_cross_domain_evidence_count": 0,
        "registration_owner_count": 0,
        "distinct_registration_type_count": 0,
        "registry_usage_count": 0,
        "native_confirmed_count": 0,
        "confirmed_formula_count": 0,
        "query_hit_count": 0,
        "existing_report_count": 0,
        "distinct_query_domain_count": 0,
        "repeated_fact_demand_count": 0,
        "descendant_percentile": 0.0,
        "referencer_percentile": 0.0,
        "component_reuse_percentile": 0.0,
        "cross_domain_percentile": 0.0,
        "registration_percentile": 0.0,
        "query_demand_percentile": 0.0,
    }
    row.update(overrides)
    return row


class KnowledgeRoleTests(unittest.TestCase):
    def test_highly_referenced_texture_stays_visual_index_only(self) -> None:
        decision = classify_asset(
            _base_asset(
                asset_class_path="/Script/Engine.Texture2D",
                generated_class_path="UNKNOWN",
                parent_class_path="UNKNOWN",
                native_parent_class_path="/Script/Engine.Texture",
                is_blueprint=0,
                referencer_count=50000,
                referencer_percentile=1.0,
                cross_domain_reference_count=25,
                cross_domain_percentile=1.0,
            )
        )
        self.assertIn("visual_support_asset", decision.role_names())
        self.assertNotIn("global_system_hub", decision.role_names())
        self.assertNotIn("domain_rule_asset", decision.role_names())
        self.assertEqual(decision.depth_policy, "INDEX_ONLY")

    def test_component_and_registration_owner_can_have_multiple_roles(self) -> None:
        decision = classify_asset(
            _base_asset(
                semantic_class_category="actor_component",
                is_actor_component=1,
                component_reuse_count=120,
                component_reuse_percentile=0.99,
                registration_owner_count=5,
                distinct_registration_type_count=3,
                registration_percentile=0.98,
                descendant_count=8,
                descendant_percentile=0.97,
            )
        )
        self.assertTrue(
            {
                "catalog_asset",
                "registration_owner",
                "global_system_hub",
                "reusable_component",
                "reusable_base_class",
                "entity_definition",
            }.issubset(decision.role_names())
        )
        self.assertEqual(decision.depth_policy, "DEEP")

    def test_captured_leaf_is_not_promoted_only_for_capture(self) -> None:
        decision = classify_asset(
            _base_asset(
                capture_exists=1,
                evidence_freshness="FRESH",
                parse_status="CONFIRMED",
                graph_count=0,
                default_property_count=0,
                referencer_count=500,
                referencer_percentile=1.0,
            )
        )
        self.assertIn("leaf_variant", decision.role_names())
        self.assertNotIn("reusable_base_class", decision.role_names())
        self.assertNotIn("global_system_hub", decision.role_names())
        self.assertEqual(decision.depth_policy, "STRUCTURE")

    def test_unknown_identity_fails_closed(self) -> None:
        decision = classify_asset(
            _base_asset(
                asset_class_path="UNKNOWN",
                identity_status="NOT_RECOVERED",
                identity_confidence="UNKNOWN",
                registration_owner_count=9,
                registration_percentile=1.0,
            )
        )
        self.assertIn("unknown_role", decision.role_names())
        self.assertEqual(decision.depth_policy, "BLOCKED_UNKNOWN")

    def test_visual_curve_can_upgrade_to_structure(self) -> None:
        decision = classify_asset(
            _base_asset(
                asset_class_path="/Script/Engine.AnimSequence",
                generated_class_path="UNKNOWN",
                parent_class_path="/Script/Engine.AnimationAsset",
                is_blueprint=0,
                curve_mechanism_count=1,
            )
        )
        self.assertIn("visual_support_asset", decision.role_names())
        self.assertEqual(decision.depth_policy, "STRUCTURE")

    def test_repeated_fact_demand_combines_with_confirmed_cross_domain_edge(
        self,
    ) -> None:
        decision = classify_asset(
            _base_asset(
                cross_domain_reference_count=2,
                confirmed_cross_domain_evidence_count=2,
                query_hit_count=2,
                repeated_fact_demand_count=1,
            )
        )
        self.assertIn("domain_rule_asset", decision.role_names())

    def test_percentiles_are_type_local_and_keep_raw_log_values(self) -> None:
        rows = enrich_type_percentiles(
            [
                _base_asset(
                    object_path="/Game/Test/A.A",
                    descendant_count=0,
                    query_hit_count=1,
                ),
                _base_asset(
                    object_path="/Game/Test/B.B",
                    descendant_count=9,
                    query_hit_count=3,
                    existing_report_count=2,
                ),
                _base_asset(
                    object_path="/Game/Test/C.C",
                    descendant_count=0,
                    query_hit_count=0,
                ),
                _base_asset(
                    object_path="/Game/Test/T.T",
                    asset_class_path="/Script/Engine.Texture2D",
                    descendant_count=1000,
                    query_hit_count=0,
                ),
            ]
        )
        by_path = {str(row["object_path"]): row for row in rows}
        self.assertAlmostEqual(
            float(by_path["/Game/Test/A.A"]["descendant_percentile"]),
            2 / 3,
        )
        self.assertEqual(
            by_path["/Game/Test/A.A"]["descendant_percentile"],
            by_path["/Game/Test/C.C"]["descendant_percentile"],
        )
        self.assertEqual(by_path["/Game/Test/B.B"]["descendant_percentile"], 1.0)
        self.assertEqual(by_path["/Game/Test/T.T"]["descendant_percentile"], 1.0)
        self.assertAlmostEqual(
            float(by_path["/Game/Test/B.B"]["descendant_log1p"]),
            math.log1p(9),
        )
        self.assertEqual(by_path["/Game/Test/B.B"]["query_demand_count"], 5)
        self.assertIn("query_demand_percentile", by_path["/Game/Test/B.B"])

    def test_materializes_roles_without_mutating_discovery(self) -> None:
        discovery = sqlite3.connect(":memory:")
        discovery.executescript(
            """
            CREATE TABLE assets(
                object_path TEXT PRIMARY KEY,
                asset_class_path TEXT NOT NULL,
                generated_class_path TEXT NOT NULL,
                parent_class_path TEXT NOT NULL,
                native_parent_class_path TEXT NOT NULL,
                identity_status TEXT NOT NULL,
                identity_confidence TEXT NOT NULL,
                is_blueprint INTEGER,
                is_data_asset INTEGER,
                is_data_table INTEGER,
                is_function_library INTEGER,
                is_blueprint_interface INTEGER,
                is_map INTEGER,
                capture_exists INTEGER,
                evidence_freshness TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                descendant_count INTEGER NOT NULL,
                referencer_count INTEGER NOT NULL,
                component_reuse_count INTEGER NOT NULL,
                cross_domain_reference_count INTEGER NOT NULL,
                registry_usage_count INTEGER NOT NULL,
                query_hit_count INTEGER,
                query_hit_status TEXT NOT NULL,
                existing_report_count INTEGER,
                existing_report_status TEXT NOT NULL,
                graph_count INTEGER NOT NULL,
                default_property_count INTEGER NOT NULL
            );
            CREATE TABLE system_registrations(
                owner_object_path TEXT NOT NULL,
                registration_type TEXT NOT NULL
            );
            """
        )
        discovery.executemany(
            """
            INSERT INTO assets VALUES (
                ?, ?, ?, ?, ?, 'CONFIRMED', 'HIGH', ?, 0, 0, 0, 0, 0, 0,
                'NOT_MEASURED', 'NOT_MEASURED', 0, ?, 0, 0, 0, NULL,
                'NOT_MEASURED', NULL, 'NOT_MEASURED', 0, 0
            )
            """,
            [
                (
                    "/Game/Test/T.T",
                    "/Script/Engine.Texture2D",
                    "UNKNOWN",
                    "UNKNOWN",
                    "/Script/Engine.Texture",
                    0,
                    999,
                ),
                (
                    "/Game/Test/B.B",
                    "/Script/Engine.Blueprint",
                    "/Game/Test/B.B_C",
                    "/Script/Engine.Actor",
                    "/Script/Engine.Actor",
                    1,
                    1,
                ),
            ],
        )
        target = sqlite3.connect(":memory:")
        target.executescript(
            """
            CREATE TABLE entities(
                entity_id INTEGER PRIMARY KEY,
                canonical_uri TEXT UNIQUE NOT NULL
            );
            INSERT INTO entities VALUES (1, '/Game/Test/T.T');
            INSERT INTO entities VALUES (2, '/Game/Test/B.B');
            """
        )
        result = materialize_discovery_roles(discovery, target)
        self.assertEqual(result["assets"], 2)
        texture_roles = {
            row[0]
            for row in target.execute(
                """
                SELECT role
                FROM knowledge_roles
                WHERE entity_id=1
                """
            )
        }
        self.assertIn("visual_support_asset", texture_roles)
        self.assertNotIn("global_system_hub", texture_roles)
        self.assertEqual(
            target.execute(
                """
                SELECT DISTINCT source_revision_id
                FROM knowledge_roles
                """
            ).fetchall(),
            [(None,)],
        )
        self.assertEqual(
            target.execute(
                """
                SELECT depth_policy
                FROM knowledge_depth_policies
                WHERE entity_id=1
                """
            ).fetchone()[0],
            "INDEX_ONLY",
        )
        missing_signal = target.execute(
            """
            SELECT
                query_hit_count, query_hit_status,
                confirmed_formula_count, native_confirmed_count,
                confirmed_cross_domain_evidence_count,
                provenance_json
            FROM role_signal_metrics
            WHERE entity_id=2
            """
        ).fetchone()
        self.assertEqual(missing_signal[:5], (None, "NOT_MEASURED", 0, 0, 0))
        missing_provenance = json.loads(str(missing_signal[5]))
        self.assertEqual(
            missing_provenance["sourceStatus"]["benchmarkQueries"],
            "SOURCE_NOT_AVAILABLE",
        )
        self.assertEqual(
            missing_provenance["sourceStatus"]["factEvidence"],
            "SOURCE_NOT_AVAILABLE",
        )
        self.assertEqual(
            discovery.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
            2,
        )
        discovery.close()
        target.close()

    def test_materialized_roles_consume_persisted_semantic_signals(
        self,
    ) -> None:
        discovery = sqlite3.connect(":memory:")
        discovery.executescript(
            """
            CREATE TABLE assets(
                object_path TEXT PRIMARY KEY,
                asset_class_path TEXT NOT NULL,
                generated_class_path TEXT NOT NULL,
                parent_class_path TEXT NOT NULL,
                native_parent_class_path TEXT NOT NULL,
                identity_status TEXT NOT NULL,
                identity_confidence TEXT NOT NULL,
                is_blueprint INTEGER NOT NULL,
                is_data_asset INTEGER NOT NULL,
                is_data_table INTEGER NOT NULL,
                is_function_library INTEGER NOT NULL,
                is_blueprint_interface INTEGER NOT NULL,
                is_map INTEGER NOT NULL,
                capture_exists INTEGER NOT NULL,
                evidence_freshness TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                descendant_count INTEGER NOT NULL,
                referencer_count INTEGER NOT NULL,
                component_reuse_count INTEGER NOT NULL,
                cross_domain_reference_count INTEGER NOT NULL,
                registry_usage_count INTEGER NOT NULL,
                query_hit_count INTEGER,
                query_hit_status TEXT NOT NULL,
                existing_report_count INTEGER,
                existing_report_status TEXT NOT NULL,
                graph_count INTEGER NOT NULL,
                default_property_count INTEGER NOT NULL
            );
            CREATE TABLE system_registrations(
                owner_object_path TEXT NOT NULL,
                registration_type TEXT NOT NULL
            );
            INSERT INTO assets VALUES(
                '/Game/Test/Rule.Rule',
                '/Script/Engine.Blueprint',
                '/Game/Test/Rule.Rule_C',
                '/Script/Engine.Actor',
                '/Script/Engine.Actor',
                'CONFIRMED', 'HIGH', 1, 0, 0, 0, 0, 0, 1,
                'FRESH', 'CONFIRMED', 0, 2, 0, 2, 0,
                NULL, 'NOT_MEASURED', NULL, 'NOT_MEASURED', 1, 1
            );
            INSERT INTO assets VALUES(
                '/Game/Test/Target.Target',
                '/Script/Engine.BlueprintGeneratedClass',
                '/Game/Test/Target.Target_C',
                '/Script/Engine.Actor',
                '/Script/Engine.Actor',
                'CONFIRMED', 'HIGH', 1, 0, 0, 0, 0, 0, 0,
                'NOT_MEASURED', 'NOT_MEASURED', 0, 1, 0, 1, 0,
                NULL, 'NOT_MEASURED', NULL, 'NOT_MEASURED', 0, 0
            );
            """
        )
        target = sqlite3.connect(":memory:")
        target.executescript(
            """
            CREATE TABLE entities(
                entity_id INTEGER PRIMARY KEY,
                canonical_uri TEXT UNIQUE NOT NULL
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
            CREATE TABLE facts(
                fact_id INTEGER PRIMARY KEY,
                subject_entity_id INTEGER NOT NULL,
                fact_type TEXT NOT NULL,
                fact_name TEXT NOT NULL,
                current INTEGER NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE fact_evidence(
                fact_id INTEGER NOT NULL,
                source_revision_id INTEGER NOT NULL,
                evidence_uri TEXT NOT NULL
            );
            CREATE TABLE domain_memberships(
                entity_id INTEGER NOT NULL,
                domain_id TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                source_revision_id INTEGER NOT NULL
            );
            CREATE TABLE edges(
                edge_id INTEGER PRIMARY KEY,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                edge_type TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                evidence_uri TEXT NOT NULL,
                source_revision_id INTEGER NOT NULL
            );
            CREATE TABLE benchmark_queries(
                query_id TEXT PRIMARY KEY,
                primary_domain TEXT NOT NULL,
                query_json TEXT NOT NULL
            );
            CREATE TABLE native_functions(
                native_function_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                source_revision_id INTEGER NOT NULL
            );
            CREATE TABLE native_blueprint_links(
                link_id TEXT PRIMARY KEY,
                blueprint_entity_id INTEGER NOT NULL,
                blueprint_graph_evidence_uri TEXT NOT NULL,
                native_function_id INTEGER,
                native_evidence_uri TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                blueprint_graph_source_revision_id INTEGER NOT NULL
            );
            INSERT INTO entities VALUES(1, '/Game/Test/Rule.Rule');
            INSERT INTO entities VALUES(2, '/Game/Test/Target.Target');
            INSERT INTO entities VALUES(3, '/Game/Test/OwnerA.OwnerA');
            INSERT INTO entities VALUES(4, '/Game/Test/OwnerB.OwnerB');
            INSERT INTO entities VALUES(5, '/Game/Test/OwnerC.OwnerC');
            INSERT INTO source_revisions VALUES(
                1, 'fixture', 'fixture://revision',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'fixture',
                'fixture/v1', '2026-07-28T00:00:00Z', 'FRESH'
            );
            INSERT INTO facts VALUES(
                1, 1, 'FORMULA', 'DamageFormula', 1, 'CONFIRMED', 'HIGH'
            );
            INSERT INTO facts VALUES(
                2, 1, 'DECLARED_DEFAULT', 'DamageCurve', 1,
                'CONFIRMED', 'HIGH'
            );
            INSERT INTO fact_evidence VALUES(1, 1, 'bp://fixture/formula');
            INSERT INTO fact_evidence VALUES(2, 1, 'bp://fixture/curve');
            INSERT INTO domain_memberships VALUES(
                1, 'domain-a', 'CONFIRMED', 'HIGH', 1
            );
            INSERT INTO domain_memberships VALUES(
                2, 'domain-b', 'CONFIRMED', 'HIGH', 1
            );
            INSERT INTO edges VALUES(
                1, 1, 2, 'MAP_DIRECT_REFERENCE',
                'CONFIRMED', 'HIGH', 'map-resource://fixture/edge', 1
            );
            INSERT INTO edges VALUES(
                2, 3, 2, 'OWNS_COMPONENT',
                'CONFIRMED', 'HIGH', 'bp://fixture/component-1', 1
            );
            INSERT INTO edges VALUES(
                3, 4, 2, 'USES_COMPONENT',
                'CONFIRMED', 'HIGH', 'bp://fixture/component-2', 1
            );
            INSERT INTO edges VALUES(
                4, 5, 2, 'USES_STATUS_COMPONENT',
                'CONFIRMED', 'HIGH', 'bp://fixture/component-3', 1
            );
            INSERT INTO native_functions VALUES(
                1, 'CONFIRMED', 'HIGH', 1
            );
            INSERT INTO native_blueprint_links VALUES(
                'link-1', 1, 'bp://fixture/node', 1,
                'native://fixture/function', 'CONFIRMED', 'HIGH', 1
            );
            """
        )
        request = {
            "entity": "/Game/Test/Rule.Rule",
            "factTypes": ["FORMULA"],
            "factNames": ["DamageFormula"],
            "edgeTypes": [],
            "_gold": {
                "reviewStatus": "HUMAN_REVIEWED",
                "protocolBoundaryOnly": False,
            },
        }
        fixture_request = {
            **request,
            "_gold": {
                "reviewStatus": "FIXTURE_EXACT",
                "protocolBoundaryOnly": False,
            },
        }
        protocol_request = {
            **request,
            "_gold": {
                "reviewStatus": "HUMAN_REVIEWED",
                "protocolBoundaryOnly": True,
            },
        }
        target.executemany(
            "INSERT INTO benchmark_queries VALUES(?, ?, ?)",
            (
                ("query-1", "damage", json.dumps(request)),
                ("query-2", "damage", json.dumps(request)),
                (
                    "fixture-query",
                    "self-derived",
                    json.dumps(fixture_request),
                ),
                (
                    "protocol-query",
                    "protocol-boundary",
                    json.dumps(protocol_request),
                ),
            ),
        )
        target.commit()

        try:
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            signal = target.execute(
                """
                SELECT
                    distinct_query_domain_count,
                    repeated_fact_demand_count,
                    confirmed_cross_domain_evidence_count,
                    confirmed_formula_count,
                    native_confirmed_count,
                    curve_mechanism_count,
                    world_placement_evidence_count
                FROM role_signal_metrics
                WHERE entity_id=1
                """
            ).fetchone()
            self.assertEqual(signal, (1, 1, 1, 1, 1, 1, 1))
            signal_provenance = json.loads(
                str(
                    target.execute(
                        """
                        SELECT provenance_json
                        FROM role_signal_metrics
                        WHERE entity_id=1
                        """
                    ).fetchone()[0]
                )
            )
            self.assertEqual(
                signal_provenance["sourceStatus"]["factEvidence"],
                "MEASURED",
            )
            self.assertTrue(
                signal_provenance["records"]["confirmed_formula_count"]
            )
            query_metrics = target.execute(
                """
                SELECT
                    query_hit_count, query_hit_status,
                    distinct_query_domain_count,
                    repeated_fact_demand_count
                FROM role_metrics
                WHERE entity_id=1
                """
            ).fetchone()
            self.assertEqual(query_metrics, (2, "MEASURED", 1, 1))
            roles = {
                str(row[0])
                for row in target.execute(
                    "SELECT role FROM knowledge_roles WHERE entity_id=1"
                )
            }
            self.assertTrue(
                {
                    "domain_rule_asset",
                    "native_runtime_implementation",
                    "map_placement_asset",
                }.issubset(roles)
            )
            percentile_rows = target.execute(
                """
                SELECT percentile_group, referencer_percentile
                FROM role_metrics
                ORDER BY entity_id
                """
            ).fetchall()
            self.assertEqual(
                percentile_rows,
                [
                    ("BLUEPRINT_BASE_CLASS", 1.0),
                    ("BLUEPRINT_BASE_CLASS", 0.5),
                ],
            )
            component_signal = target.execute(
                """
                SELECT confirmed_component_relationship_count
                FROM role_signal_metrics
                WHERE entity_id=2
                """
            ).fetchone()
            self.assertEqual(component_signal, (3,))
            self.assertEqual(
                target.execute(
                    """
                    SELECT component_reuse_count
                    FROM role_metrics
                    WHERE entity_id=2
                    """
                ).fetchone(),
                (3,),
            )

            target.execute(
                """
                UPDATE source_revisions
                SET freshness_status='STALE'
                WHERE revision_id=1
                """
            )
            target.execute(
                "UPDATE edges SET status='CANDIDATE' WHERE edge_id=1"
            )
            target.execute(
                """
                UPDATE native_blueprint_links
                SET status='CANDIDATE'
                WHERE link_id='link-1'
                """
            )
            target.commit()
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            closed_signal = target.execute(
                """
                SELECT
                    distinct_query_domain_count,
                    repeated_fact_demand_count,
                    confirmed_cross_domain_evidence_count,
                    confirmed_formula_count,
                    native_confirmed_count,
                    curve_mechanism_count,
                    world_placement_evidence_count
                FROM role_signal_metrics
                WHERE entity_id=1
                """
            ).fetchone()
            self.assertEqual(closed_signal, (1, 1, 0, 0, 0, 0, 0))
            closed_roles = {
                str(row[0])
                for row in target.execute(
                    "SELECT role FROM knowledge_roles WHERE entity_id=1"
                )
            }
            self.assertFalse(
                {
                    "domain_rule_asset",
                    "native_runtime_implementation",
                    "map_placement_asset",
                }
                & closed_roles
            )

            target.execute(
                """
                DELETE FROM benchmark_queries
                WHERE query_id IN ('query-1', 'query-2')
                """
            )
            target.commit()
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            self.assertEqual(
                target.execute(
                    """
                    SELECT
                        query_hit_count, query_hit_status,
                        distinct_query_domain_count,
                        repeated_fact_demand_count
                    FROM role_metrics
                    WHERE entity_id=1
                    """
                ).fetchone(),
                (None, "UNVERIFIED", 0, 0),
            )
            unverified_provenance = json.loads(
                str(
                    target.execute(
                        """
                        SELECT provenance_json
                        FROM role_signal_metrics
                        WHERE entity_id=1
                        """
                    ).fetchone()[0]
                )
            )
            self.assertEqual(
                unverified_provenance["sourceStatus"][
                    "benchmarkQueries"
                ],
                "UNVERIFIED",
            )
        finally:
            discovery.close()
            target.close()

    def test_unrevisioned_edge_and_domain_tables_fail_closed(self) -> None:
        target = sqlite3.connect(":memory:")
        target.executescript(
            """
            CREATE TABLE domain_memberships(
                entity_id INTEGER NOT NULL,
                domain_id TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE edges(
                edge_id INTEGER PRIMARY KEY,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                edge_type TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                evidence_uri TEXT NOT NULL
            );
            INSERT INTO domain_memberships VALUES(
                1, 'domain-a', 'CONFIRMED', 'HIGH'
            );
            INSERT INTO domain_memberships VALUES(
                2, 'domain-b', 'CONFIRMED', 'HIGH'
            );
            INSERT INTO edges VALUES(
                1, 1, 2, 'MAP_DIRECT_REFERENCE',
                'CONFIRMED', 'HIGH', 'map-resource://fixture/edge'
            );
            """
        )
        result = _PersistedRoleSignals(
            counts_by_entity={},
            query_hits_by_entity={},
            provenance_by_entity={},
            source_statuses={},
        )
        try:
            _collect_edge_signals(
                target,
                fresh_revision_ids={1},
                result=result,
            )
        finally:
            target.close()

        self.assertEqual(result.counts_by_entity, {})
        self.assertEqual(
            result.source_statuses["confirmedEdges"],
            "UNVERIFIED",
        )
        self.assertEqual(
            result.source_statuses["domainMemberships"],
            "UNVERIFIED",
        )

    def test_malformed_source_revision_is_not_fresh(self) -> None:
        target = sqlite3.connect(":memory:")
        target.executescript(
            """
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
            INSERT INTO source_revisions VALUES(
                1, 'fixture', 'C:/Users/' || 'ac/private',
                'x', 'fixture', 'fixture/v1', 'not-a-time', 'FRESH'
            );
            INSERT INTO source_revisions VALUES(
                2, 'fixture', 'fixture://valid',
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                'fixture', 'fixture/v1',
                '2026-07-28T00:00:00Z', 'FRESH'
            );
            """
        )
        try:
            self.assertEqual(_fresh_revision_ids(target), {2})
        finally:
            target.close()

    def test_registration_owner_role_requires_fresh_global_registration(
        self,
    ) -> None:
        discovery = sqlite3.connect(":memory:")
        discovery.executescript(
            """
            CREATE TABLE assets(
                object_path TEXT PRIMARY KEY,
                asset_class_path TEXT NOT NULL,
                generated_class_path TEXT NOT NULL,
                parent_class_path TEXT NOT NULL,
                native_parent_class_path TEXT NOT NULL,
                identity_status TEXT NOT NULL,
                identity_confidence TEXT NOT NULL,
                is_blueprint INTEGER NOT NULL,
                descendant_count INTEGER NOT NULL,
                referencer_count INTEGER NOT NULL,
                component_reuse_count INTEGER NOT NULL,
                cross_domain_reference_count INTEGER NOT NULL,
                registry_usage_count INTEGER NOT NULL,
                query_hit_count INTEGER,
                existing_report_count INTEGER
            );
            CREATE TABLE system_registrations(
                owner_object_path TEXT NOT NULL,
                registration_type TEXT NOT NULL
            );
            INSERT INTO assets VALUES(
                '/Game/Test/Owner.Owner',
                '/Script/Engine.Blueprint',
                '/Game/Test/Owner.Owner_C',
                '/Script/Engine.Actor',
                '/Script/Engine.Actor',
                'CONFIRMED', 'HIGH', 1,
                0, 0, 0, 0, 0, 0, 0
            );
            INSERT INTO system_registrations VALUES(
                '/Game/Test/Owner.Owner', 'buff_registration'
            );
            INSERT INTO system_registrations VALUES(
                '/Game/Test/Owner.Owner', 'item_registration'
            );
            """
        )
        target = sqlite3.connect(":memory:")
        target.executescript(
            """
            CREATE TABLE entities(
                entity_id INTEGER PRIMARY KEY,
                canonical_uri TEXT UNIQUE NOT NULL
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
            INSERT INTO entities VALUES(
                1, '/Game/Test/Owner.Owner'
            );
            INSERT INTO source_revisions VALUES(
                1, 'registry_generation',
                'registry://fixture',
                'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                'fixture', 'fixture/v1',
                '2026-07-27T00:00:00Z', 'FRESH'
            );
            """
        )
        create_registration_tables(target)
        target.executemany(
            """
            INSERT INTO typed_registrations VALUES(
                ?, '/Game/Test/Owner.Owner', ?, ?, ?,
                ?, 'DECLARED', ?, ?, 1, 'fixture', ?
            )
            """,
            [
                (
                    "candidate",
                    "/Game/Test/Buff.Buff",
                    "buff_registration",
                    "BuffClass",
                    "bp://fixture/candidate",
                    "LOW",
                    "CANDIDATE",
                    "property_token_candidate",
                ),
                (
                    "legacy",
                    "/Game/Test/Item.Item",
                    "item_registration",
                    "ItemClass",
                    "existing-kb://fixture/legacy",
                    "MEDIUM",
                    "LEGACY_UNVERIFIED",
                    "legacy_reference_candidate",
                ),
            ],
        )
        target.commit()

        try:
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            self.assertFalse(
                {
                    "registration_owner",
                    "global_system_hub",
                }
                & {
                    str(row[0])
                    for row in target.execute(
                        "SELECT role FROM knowledge_roles"
                    )
                }
            )

            target.execute(
                """
                UPDATE typed_registrations
                SET status='CONFIRMED', confidence='HIGH',
                    evidence_uri='UNKNOWN'
                """
            )
            target.commit()
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            self.assertFalse(
                {
                    "registration_owner",
                    "global_system_hub",
                }
                & {
                    str(row[0])
                    for row in target.execute(
                        "SELECT role FROM knowledge_roles"
                    )
                }
            )

            target.executemany(
                """
                UPDATE typed_registrations
                SET evidence_uri=?
                WHERE registration_id=?
                """,
                (
                    ("bp://fixture/candidate", "candidate"),
                    ("bp://fixture/legacy", "legacy"),
                ),
            )
            target.commit()
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            self.assertFalse(
                {
                    "registration_owner",
                    "global_system_hub",
                }
                & {
                    str(row[0])
                    for row in target.execute(
                        "SELECT role FROM knowledge_roles"
                    )
                }
            )

            target.executemany(
                "INSERT INTO entities VALUES(?, ?)",
                (
                    (2, "/Game/Test/Buff.Buff"),
                    (3, "/Game/Test/Item.Item"),
                ),
            )
            target.execute(
                """
                UPDATE source_revisions
                SET freshness_status='STALE'
                WHERE revision_id=1
                """
            )
            target.commit()
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            self.assertFalse(
                {
                    "registration_owner",
                    "global_system_hub",
                }
                & {
                    str(row[0])
                    for row in target.execute(
                        "SELECT role FROM knowledge_roles"
                    )
                }
            )

            target.execute(
                """
                UPDATE source_revisions
                SET freshness_status='FRESH'
                WHERE revision_id=1
                """
            )
            target.commit()
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            self.assertFalse(
                {
                    "registration_owner",
                    "global_system_hub",
                }
                & {
                    str(row[0])
                    for row in target.execute(
                        "SELECT role FROM knowledge_roles"
                    )
                }
            )

            target.executemany(
                "INSERT INTO entities VALUES(?, ?)",
                (
                    (4, "/Game/Test/Engram.Engram"),
                    (5, "/Game/Test/Structure.Structure"),
                ),
            )
            target.executemany(
                """
                INSERT INTO typed_registrations VALUES(
                    ?,
                    '/Game/Test/Owner.Owner',
                    ?, ?, ?, ?,
                    'DECLARED', 'HIGH', 'CONFIRMED', 1,
                    'fixture', 'exact_property'
                )
                """,
                (
                    (
                        "global-engram",
                        "/Game/Test/Engram.Engram",
                        "engram_registration",
                        "AdditionalEngramBlueprintClasses",
                        "bp://fixture/global-engram",
                    ),
                    (
                        "global-structure",
                        "/Game/Test/Structure.Structure",
                        "structure_registration",
                        "AdditionalStructuresToPlace",
                        "bp://fixture/global-structure",
                    ),
                ),
            )
            target.commit()
            materialize_discovery_roles(
                discovery,
                target,
                source_revision_id=1,
            )
            self.assertTrue(
                {
                    "registration_owner",
                    "global_system_hub",
                }.issubset(
                    {
                        str(row[0])
                        for row in target.execute(
                            "SELECT role FROM knowledge_roles"
                        )
                    }
                )
            )
        finally:
            discovery.close()
            target.close()


if __name__ == "__main__":
    unittest.main()
