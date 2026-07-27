from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.kb_vnext.registrations import (  # noqa: E402
    REGISTRATION_RULES,
    classify_registration_property,
    materialize_typed_registrations,
)


def _discovery_fixture(
    gold: dict[str, object],
) -> tuple[sqlite3.Connection, set[tuple[str, str, str]]]:
    discovery = sqlite3.connect(":memory:")
    discovery.executescript(
        """
        CREATE TABLE system_registrations(
            registration_id TEXT PRIMARY KEY,
            owner_object_path TEXT NOT NULL,
            target_object_path TEXT NOT NULL,
            registration_type TEXT NOT NULL,
            source_property TEXT NOT NULL,
            source_evidence_id TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_kind TEXT NOT NULL
        );
        CREATE TABLE asset_references(
            reference_id TEXT PRIMARY KEY,
            source_object_path TEXT NOT NULL,
            target_object_path TEXT NOT NULL,
            source_property TEXT NOT NULL,
            source_evidence_id TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            edge_kind TEXT NOT NULL
        );
        """
    )
    expected: set[tuple[str, str, str]] = set()
    index = 0
    owners = [str(value) for value in gold["owners"]]
    for owner in owners:
        for source_property, registration_type in gold["cases"]:
            index += 1
            target = f"/Game/Gold/Target_{index}.Target_{index}"
            discovery.execute(
                """
                INSERT INTO asset_references VALUES (
                    ?, ?, ?, ?, ?, 'HIGH', 'gold_set', 'object_reference'
                )
                """,
                (
                    f"ref-{index}",
                    owner,
                    target,
                    source_property,
                    f"bp://gold/{index}",
                ),
            )
            expected.add((owner, target, registration_type))
    for negative in gold["negativeCases"]:
        index += 1
        discovery.execute(
            """
            INSERT INTO asset_references VALUES (
                ?, ?, ?, ?, ?, 'HIGH', 'gold_set', 'object_reference'
            )
            """,
            (
                f"ref-{index}",
                owners[0],
                f"/Game/Gold/Negative_{index}.Negative_{index}",
                negative,
                f"bp://gold/{index}",
            ),
        )
    discovery.execute(
        """
        INSERT INTO system_registrations VALUES (
            'legacy-1', '/Game/Legacy/Owner.Owner',
            '/Game/Legacy/Buff.Buff_C', 'buff_registration',
            'BuffClassString', 'existing-kb://buffs/1', 'MEDIUM',
            'existing_knowledge_database'
        )
        """
    )
    discovery.execute(
        """
        INSERT INTO asset_references VALUES (
            'legacy-ref-1', '/Game/Legacy/Owner.Owner',
            '/Game/Legacy/Buff.Buff_C', 'BuffClassString',
            'existing-kb://buffs/1', 'MEDIUM',
            'existing_knowledge_database', 'buff_registration'
        )
        """
    )
    discovery.commit()
    return discovery, expected


class KnowledgeRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gold = json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "fixtures"
                / "kb_registration_gold_set.json"
            ).read_text(encoding="utf-8")
        )

    def test_rule_catalog_covers_requested_registration_families(self):
        rule_types = {rule.registration_type for rule in REGISTRATION_RULES}
        self.assertGreaterEqual(len(rule_types), 20)
        self.assertTrue(
            {
                "primal_game_data_registration",
                "game_mode_registration",
                "game_state_registration",
                "player_controller_registration",
                "world_settings_registration",
                "map_world_registration",
                "spawn_registration",
                "mission_world_event_registration",
                "engram_registration",
                "creature_registration",
                "item_registration",
                "buff_registration",
                "structure_registration",
                "loot_reward_registration",
                "harvest_component_registration",
                "damage_type_registration",
                "status_component_registration",
                "inventory_component_registration",
                "biome_pcg_registration",
                "world_partition_registration",
                "save_transfer_registration",
                "boss_arena_encounter_registration",
            }.issubset(rule_types)
        )

    def test_gold_set_has_at_least_100_explicit_relationships(self):
        relationship_count = len(self.gold["owners"]) * len(
            self.gold["cases"]
        )
        self.assertGreaterEqual(relationship_count, 100)

    def test_gold_set_precision_and_recall_meet_gate(self):
        discovery, expected = _discovery_fixture(self.gold)
        target = sqlite3.connect(":memory:")
        result = materialize_typed_registrations(
            discovery,
            target,
            source_revision_id=1,
        )
        actual = {
            (str(owner), str(target_uri), str(registration_type))
            for owner, target_uri, registration_type in target.execute(
                """
                SELECT owner_uri, target_uri, registration_type
                FROM typed_registrations
                WHERE status='CONFIRMED'
                """
            )
        }
        true_positive = len(actual.intersection(expected))
        precision = true_positive / len(actual)
        recall = true_positive / len(expected)
        self.assertGreaterEqual(precision, 0.99)
        self.assertGreaterEqual(recall, 0.95)
        self.assertEqual(result["status_legacy_unverified"], 1)
        legacy = target.execute(
            """
            SELECT status, confidence, evidence_uri
            FROM typed_registrations
            WHERE owner_uri='/Game/Legacy/Owner.Owner'
            """
        ).fetchone()
        self.assertEqual(legacy[0], "LEGACY_UNVERIFIED")
        self.assertEqual(legacy[1], "MEDIUM")
        self.assertTrue(str(legacy[2]).startswith("existing-kb://"))
        discovery.close()
        target.close()

    def test_property_token_without_class_evidence_stays_candidate(self):
        classification = classify_registration_property(
            "SomeCustomBuffClassOverride"
        )
        buff = [
            item
            for item in classification
            if item.registration_type == "buff_registration"
        ]
        self.assertEqual(len(buff), 1)
        self.assertEqual(buff[0].status, "CANDIDATE")
        self.assertEqual(buff[0].confidence, "LOW")

    def test_property_token_plus_confirmed_target_class_can_confirm(self):
        classification = classify_registration_property(
            "SomeCustomBuffClassOverride",
            target_categories=["BUFF"],
        )
        buff = [
            item
            for item in classification
            if item.registration_type == "buff_registration"
        ]
        self.assertEqual(buff[0].status, "CONFIRMED")
        self.assertEqual(
            buff[0].match_method,
            "property_semantic_and_class_ancestry",
        )


if __name__ == "__main__":
    unittest.main()
