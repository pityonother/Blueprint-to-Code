from __future__ import annotations

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
    classify_asset,
    enrich_type_percentiles,
    materialize_discovery_roles,
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
                SELECT depth_policy
                FROM knowledge_depth_policies
                WHERE entity_id=1
                """
            ).fetchone()[0],
            "INDEX_ONLY",
        )
        self.assertEqual(
            discovery.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
            2,
        )
        discovery.close()
        target.close()


if __name__ == "__main__":
    unittest.main()
