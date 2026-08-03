from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_ranking import (  # noqa: E402
    NORMALIZED_HARVEST_AMOUNT_SCALE,
    STATIC_COMPLETE_NODE_SCORE_BASIS,
    YIELD_MODEL_VERSION,
    YIELD_SCORE_BASIS,
    estimate_complete_node_yield,
    evaluate_attack_resource,
    extract_creature_attacks,
    extract_harvest_component,
    extract_resource_damage_overrides,
    normalize_unreal_object_identity,
    rank_harvest_rows,
)


class HarvestRefactorCharacterizationTests(unittest.TestCase):
    def test_complete_node_formula_golden_json_is_byte_stable(self):
        result = estimate_complete_node_yield(
            base_damage=40.0,
            damage_multiplier=2.5,
            harvest_quantity_multiplier=1.2,
            max_harvest_health=100.0,
            harvest_health_give_resource_interval=20.0,
            resource_weight_share=0.75,
            minimum_quantity=1.0,
            maximum_quantity=2.0,
            quantity_random_power=1.0,
            clamp_resource_harvest_damage=False,
        )

        self.assertEqual(
            json.dumps(result, sort_keys=True, separators=(",", ":")),
            "{\"clampResourceHarvestDamage\":false,\"estimatedGrantCallsPerNode\":12,"
            "\"estimatedHitsToDepleteNode\":1,\"estimatedYieldPerNode\":13.5,"
            "\"expectedQuantityPerSelection\":1.5,\"normalizedHarvestAmountScale\":2.0,"
            "\"quantityRandomPower\":1.0,\"yieldModelBasis\":"
            "\"NATIVE_STATIC_COMPLETE_NODE_HIT_SIMULATION\",\"yieldModelVersion\":"
            "\"harvest-estimated-yield-per-node/v1-native-static-profile\"}",
        )

    def test_public_compatibility_surface_is_locked_before_moving(self):
        self.assertEqual(NORMALIZED_HARVEST_AMOUNT_SCALE, 2.0)
        self.assertEqual(
            YIELD_MODEL_VERSION,
            "harvest-estimated-yield-per-node/v1-native-static-profile",
        )
        self.assertEqual(
            YIELD_SCORE_BASIS,
            "ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE",
        )
        self.assertEqual(
            STATIC_COMPLETE_NODE_SCORE_BASIS,
            "STATIC_COMPLETE_NODE_TARGET_RESOURCE_UNITS",
        )
        for exported in (
            evaluate_attack_resource,
            extract_creature_attacks,
            extract_harvest_component,
            extract_resource_damage_overrides,
            normalize_unreal_object_identity,
            rank_harvest_rows,
        ):
            self.assertTrue(callable(exported))

    def test_ranking_order_and_input_rows_are_characterized(self):
        rows = [
            {
                "creature": "Zulu",
                "attackName": "Tail",
                "component": "Stone",
                "rankingStatus": "RANKED",
                "estimatedYieldPerNode": 10.0,
            },
            {
                "creature": "Alpha",
                "attackName": "Bite",
                "component": "Stone",
                "rankingStatus": "RANKED",
                "estimatedYieldPerNode": 10.0,
            },
            {
                "creature": "Gap",
                "attackName": "Unknown",
                "component": "Stone",
                "rankingStatus": "UNRANKED",
                "estimatedYieldPerNode": None,
            },
        ]
        before = deepcopy(rows)

        ranked = rank_harvest_rows(rows)

        self.assertEqual(rows, before)
        self.assertEqual(
            [(row["rankingStatus"], row["creature"]) for row in ranked],
            [("RANKED", "Alpha"), ("RANKED", "Zulu"), ("UNRANKED", "Gap")],
        )


if __name__ == "__main__":
    unittest.main()
