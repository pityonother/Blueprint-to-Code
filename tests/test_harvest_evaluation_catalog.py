import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    EVALUATION_CATALOG_SCHEMA,
    RANKING_RESULT_SCHEMA,
    HarvestEvaluationEngine,
    extract_creature_identity,
    prepare_attack_for_usage_scope,
)
from blueprint_translator.harvest_ranking import (  # noqa: E402
    YIELD_MODEL_VERSION,
    YIELD_SCORE_BASIS,
)


class HarvestEvaluationCatalogTests(unittest.TestCase):
    def test_creature_identity_prefers_effective_descriptive_name_and_stable_species_tag(self):
        identity = extract_creature_identity(
            [
                {"name": "DinoNameTag", "type": "NameProperty", "value": "Anky"},
                {
                    "name": "DescriptiveName",
                    "type": "StrProperty",
                    "value": "Ankylosaurus",
                },
            ],
            fallback_name="Ankylo_Character_BP",
        )

        self.assertEqual(identity["name"], "Ankylosaurus")
        self.assertEqual(identity["speciesKey"], "anky")
        self.assertEqual(identity["identityStatus"], "CONFIRMED")

    def test_tamed_ridden_scope_uses_rider_interval_and_rejects_explicitly_blocked_attacks(self):
        allowed, reason = prepare_attack_for_usage_scope(
            {
                "attackInterval": 0.5,
                "riderAttackInterval": 1.25,
                "skipTamed": False,
                "preventWithRider": False,
            },
            usage_scope="TAMED_RIDDEN",
        )
        ai_only, ai_only_reason = prepare_attack_for_usage_scope(
            {"attackInterval": 0.1, "skipTamed": False, "skipAI": True},
            usage_scope="TAMED_RIDDEN",
        )
        tamed_filtered, tamed_filtered_reason = prepare_attack_for_usage_scope(
            {"attackInterval": 0.1, "skipTamed": True, "skipAI": False},
            usage_scope="TAMED_RIDDEN",
        )
        blocked, blocked_reason = prepare_attack_for_usage_scope(
            {"attackInterval": 0.1, "preventWithRider": True},
            usage_scope="TAMED_RIDDEN",
        )
        gated, gated_reason = prepare_attack_for_usage_scope(
            {"attackInterval": 0.1, "useBlueprintCanRiderAttack": True},
            usage_scope="TAMED_RIDDEN",
        )
        wild_only, wild_only_reason = prepare_attack_for_usage_scope(
            {"attackInterval": 0.1, "onlyOnWildDinos": True},
            usage_scope="TAMED_RIDDEN",
        )
        dynamic_damage, dynamic_damage_reason = prepare_attack_for_usage_scope(
            {"attackInterval": 0.1, "useBlueprintAdjustOutputDamage": True},
            usage_scope="TAMED_RIDDEN",
        )

        self.assertIsNone(reason)
        self.assertEqual(allowed["attackInterval"], 1.25)
        self.assertEqual(allowed["baseAttackInterval"], 0.5)
        self.assertEqual(allowed["attackIntervalSource"], "RIDER_ATTACK_INTERVAL")
        self.assertIsNone(ai_only_reason)
        self.assertEqual(ai_only["attackInterval"], 0.1)
        self.assertIsNone(tamed_filtered)
        self.assertEqual(tamed_filtered_reason, "ATTACK_SKIPPED_WHEN_TAMED")
        self.assertIsNone(blocked)
        self.assertEqual(blocked_reason, "ATTACK_PREVENTED_WITH_RIDER")
        self.assertIsNone(gated_reason)
        self.assertEqual(gated["usageEligibilityStatus"], "CONDITIONAL")
        self.assertEqual(
            gated["usageConditionReasonCodes"],
            ["BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED"],
        )
        self.assertIsNone(wild_only)
        self.assertEqual(wild_only_reason, "ATTACK_ONLY_ON_WILD_DINOS")
        self.assertIsNone(dynamic_damage_reason)
        self.assertEqual(dynamic_damage["usageEligibilityStatus"], "CONDITIONAL")
        self.assertEqual(
            dynamic_damage["usageConditionReasonCodes"],
            ["BLUEPRINT_ADJUST_OUTPUT_DAMAGE_NOT_RECOVERED"],
        )

    def test_lazy_engine_ranks_one_component_resource_and_collapses_species_variants(self):
        node_catalog = {
            "schema": "ark-resource-node-catalog/v1",
            "dataset": {"rankingDatasetRevision": "a" * 64},
            "nodes": [
                {
                    "id": "metal-node",
                    "name": "Metal Rock",
                    "objectPath": "/Game/Nodes/Metal.Metal",
                    "harvestComponent": {
                        "packagePath": "/Game/Components/MetalHarvestComponent"
                    },
                    "resources": {
                        "items": [
                            {
                                "entryIndex": 0,
                                "resource": "PrimalItemResource_Metal_C",
                                "nodeResourceId": "metal-entry",
                            }
                        ]
                    },
                }
            ],
        }
        evaluation_catalog = {
            "schema": EVALUATION_CATALOG_SCHEMA,
            "dataset": {
                "revision": "b" * 64,
                "componentDatasetRevision": "a" * 64,
            },
            "methodology": {
                "usageScope": "TAMED_RIDDEN",
                "rideabilityRequirement": "B_ALLOW_RIDING_TRUE",
            },
            "coverage": {
                "creatureAssetsCataloged": 5,
                "speciesCataloged": 4,
                "attacksDecoded": 6,
                "claimsAllCreatures": True,
            },
            "components": [
                {
                    "component": "MetalHarvestComponent",
                    "objectPath": (
                        "/Game/Components/MetalHarvestComponent."
                        "MetalHarvestComponent"
                    ),
                    "maxHarvestHealth": 620.0,
                    "harvestHealthGiveResourceInterval": 40.0,
                    "resourceEntries": [
                        {
                            "entryIndex": 0,
                            "resource": "PrimalItemResource_Metal_C",
                            "entryWeight": 1.0,
                            "weightOverrides": {"DmgType_MineStone_C": 1.0},
                            "overrideQuantityMin": 1.0,
                            "overrideQuantityMax": 2.0,
                            "overrideQuantityRandomPower": 1.0,
                            "minQuantityOverrides": {},
                            "maxQuantityOverrides": {},
                            "gaps": [],
                        }
                    ],
                    "damageEntries": [
                        {
                            "damageTypeParent": "DmgType_MineStone_C",
                            "damageMultiplier": 2.0,
                            "harvestQuantityMultiplier": 1.0,
                            "gaps": [],
                        }
                    ],
                    "gaps": [],
                    "rankingGaps": [],
                }
            ],
            "damageTypeParents": {},
            "resourceDamageOverrides": [],
            "damageTypeGaps": {},
            "creatures": [
                {
                    "name": "Ankylosaurus",
                    "speciesKey": "anky",
                    "objectPath": "/Game/Dinos/Anky",
                    "tameability": {"status": "ALLOWED", "reasonCodes": []},
                    "rideability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Tail",
                            "damageType": "DmgType_MineStone_C",
                            "baseDamage": 50.0,
                            "attackInterval": 0.5,
                            "riderAttackInterval": 2.0,
                            "skipTamed": False,
                            "preventWithRider": False,
                            "useBlueprintCanRiderAttack": True,
                            "gaps": [],
                        }
                    ],
                },
                {
                    "name": "Ankylosaurus Variant",
                    "speciesKey": "anky",
                    "objectPath": "/Game/Dinos/AnkyVariant",
                    "tameability": {"status": "ALLOWED", "reasonCodes": []},
                    "rideability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Tail",
                            "damageType": "DmgType_MineStone_C",
                            "baseDamage": 80.0,
                            "attackInterval": 1.0,
                            "riderAttackInterval": 2.0,
                            "skipTamed": False,
                            "preventWithRider": False,
                            "useBlueprintCanRiderAttack": True,
                            "gaps": [],
                        }
                    ],
                },
                {
                    "name": "Untameable Winner",
                    "speciesKey": "blocked",
                    "objectPath": "/Game/Dinos/Blocked",
                    "rideability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Cheat",
                            "damageType": "DmgType_MineStone_C",
                            "baseDamage": 9999.0,
                            "attackInterval": 0.01,
                            "skipTamed": True,
                            "preventWithRider": False,
                            "useBlueprintCanRiderAttack": True,
                            "gaps": [],
                        }
                    ],
                },
                {
                    "name": "Doedicurus",
                    "speciesKey": "doed",
                    "objectPath": "/Game/Dinos/Doed",
                    "tameability": {"status": "ALLOWED", "reasonCodes": []},
                    "rideability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Tail",
                            "damageType": "DmgType_MineStone_C",
                            "baseDamage": 25.0,
                            "attackInterval": 1.0,
                            "riderAttackInterval": 1.0,
                            "skipTamed": False,
                            "preventWithRider": False,
                            "useBlueprintCanRiderAttack": True,
                            "gaps": [],
                        },
                        {
                            "attackIndex": 1,
                            "attackName": "Blocked Rider Attack",
                            "damageType": "DmgType_MineStone_C",
                            "baseDamage": 9000.0,
                            "attackInterval": 0.01,
                            "skipTamed": False,
                            "preventWithRider": True,
                            "gaps": [],
                        },
                    ],
                },
                {
                    "name": "Rideability Unknown Winner",
                    "speciesKey": "rideability-unknown",
                    "objectPath": "/Game/Dinos/RideabilityUnknown",
                    "rideability": {
                        "status": "UNKNOWN",
                        "reasonCodes": ["RIDEABILITY_NOT_RECOVERED"],
                    },
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Unknown Ride",
                            "damageType": "DmgType_MineStone_C",
                            "baseDamage": 99999.0,
                            "attackInterval": 0.01,
                            "riderAttackInterval": 0.01,
                            "preventWithRider": False,
                            "gaps": [],
                        }
                    ],
                },
                {
                    "name": "Boss That Must Not Rank",
                    "speciesKey": "boss",
                    "objectPath": "/Game/Dinos/Boss",
                    "rideability": {"status": "ALLOWED", "reasonCodes": []},
                    "tameability": {
                        "status": "PREVENTED",
                        "reasonCodes": ["BOSS_DINO"],
                    },
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Boss Hit",
                            "damageType": "DmgType_MineStone_C",
                            "baseDamage": 99999.0,
                            "attackInterval": 0.01,
                            "riderAttackInterval": 0.01,
                            "preventWithRider": False,
                            "gaps": [],
                        }
                    ],
                },
            ],
        }

        result = HarvestEvaluationEngine(evaluation_catalog).rank_node_resource(
            node_catalog,
            node_id="metal-node",
            node_resource_id="metal-entry",
            limit=10,
        )

        self.assertEqual([row["speciesKey"] for row in result["items"]], ["anky", "doed"])
        self.assertEqual(result["items"][0]["creatureObjectPath"], "/Game/Dinos/Anky")
        self.assertEqual(result["items"][0]["variantCount"], 2)
        self.assertEqual(result["items"][0]["attackInterval"], 2.0)
        self.assertEqual(result["items"][0]["tameabilityStatus"], "ALLOWED")
        self.assertEqual(result["items"][0]["relativeToNodeTopPercent"], 100.0)
        self.assertEqual(result["items"][0]["rankingTier"], "CONDITIONAL")
        self.assertLess(result["items"][1]["relativeToNodeTopPercent"], 100.0)
        self.assertEqual(result["items"][0]["evidence"]["status"], "PARTIAL")
        self.assertIn(
            "BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED",
            result["items"][0]["evidence"]["gaps"],
        )
        self.assertIn(
            "BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED",
            result["items"][1]["evidence"]["gaps"],
        )
        self.assertEqual(result["coverage"]["attacksConditionallyEvaluated"], 3)
        self.assertEqual(
            result["coverage"]["conditionalEvaluationByReason"],
            {
                "BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED": 3,
            },
        )
        self.assertEqual(result["coverage"]["attacksExcludedByScope"], 2)
        self.assertEqual(result["coverage"]["attacksExcludedByCreatureScope"], 2)
        self.assertEqual(
            result["coverage"]["excludedCreatureByReason"],
            {"BOSS_DINO": 1, "RIDEABILITY_NOT_RECOVERED": 1},
        )
        self.assertEqual(
            result["coverage"]["excludedByReason"],
            {
                "ATTACK_PREVENTED_WITH_RIDER": 1,
                "ATTACK_SKIPPED_WHEN_TAMED": 1,
            },
        )
        self.assertEqual(result["scopeStatus"], "ALL_DISCOVERED_CREATURES_EVALUATED")
        self.assertFalse(result["claimsGlobalTop"])
        self.assertEqual(result["schema"], RANKING_RESULT_SCHEMA)
        self.assertEqual(result["methodology"]["formulaVersion"], YIELD_MODEL_VERSION)
        self.assertEqual(result["methodology"]["metric"], "estimatedYieldPerNode")
        self.assertEqual(result["methodology"]["scoreBasis"], YIELD_SCORE_BASIS)

    def test_lazy_engine_selects_duplicate_resource_by_exact_entry_index(self):
        node_catalog = {
            "schema": "ark-resource-node-catalog/v1",
            "dataset": {},
            "nodes": [
                {
                    "id": "duplicate-resource-node",
                    "name": "Duplicate resource node",
                    "objectPath": "/Game/Nodes/Duplicate.Duplicate",
                    "harvestComponent": {
                        "packagePath": "/Game/Components/DuplicateHarvestComponent"
                    },
                    "resources": {
                        "items": [
                            {
                                "entryIndex": 0,
                                "resource": "PrimalItemResource_Berry_C",
                                "nodeResourceId": "berry-entry-0",
                            },
                            {
                                "entryIndex": 1,
                                "resource": "PrimalItemResource_Berry_C",
                                "nodeResourceId": "berry-entry-1",
                            },
                        ]
                    },
                }
            ],
        }
        evaluation_catalog = {
            "schema": EVALUATION_CATALOG_SCHEMA,
            "dataset": {
                "revision": "b" * 64,
                "componentDatasetRevision": "a" * 64,
            },
            "methodology": {"usageScope": "TAMED_RIDDEN"},
            "coverage": {"claimsAllCreatures": False},
            "components": [
                {
                    "component": "DuplicateHarvestComponent",
                    "objectPath": (
                        "/Game/Components/DuplicateHarvestComponent."
                        "DuplicateHarvestComponent"
                    ),
                    "maxHarvestHealth": 100.0,
                    "harvestHealthGiveResourceInterval": 20.0,
                    "resourceEntries": [
                        {
                            "entryIndex": 0,
                            "resource": "PrimalItemResource_Berry_C",
                            "entryWeight": 1.0,
                            "weightOverrides": {},
                            "overrideQuantityMin": 1.0,
                            "overrideQuantityMax": 2.0,
                            "overrideQuantityRandomPower": 1.0,
                            "minQuantityOverrides": {},
                            "maxQuantityOverrides": {},
                            "gaps": [],
                        },
                        {
                            "entryIndex": 1,
                            "resource": "PrimalItemResource_Berry_C",
                            "entryWeight": 0.5,
                            "weightOverrides": {},
                            "overrideQuantityMin": 1.0,
                            "overrideQuantityMax": 2.0,
                            "overrideQuantityRandomPower": 1.0,
                            "minQuantityOverrides": {},
                            "maxQuantityOverrides": {},
                            "gaps": [],
                        },
                    ],
                    "damageEntries": [
                        {
                            "damageTypeParent": "DmgType_Harvest_C",
                            "damageMultiplier": 1.0,
                            "harvestQuantityMultiplier": 1.0,
                            "gaps": [],
                        }
                    ],
                    "gaps": [],
                    "rankingGaps": [],
                }
            ],
            "damageTypeParents": {},
            "resourceDamageOverrides": [],
            "damageTypeGaps": {},
            "creatures": [
                {
                    "name": "Test Creature",
                    "speciesKey": "test-creature",
                    "objectPath": "/Game/Dinos/TestCreature",
                    "tameability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Harvest",
                            "damageType": "DmgType_Harvest_C",
                            "baseDamage": 30.0,
                            "attackInterval": 1.0,
                            "preventWithRider": False,
                            "useBlueprintCanRiderAttack": False,
                            "gaps": [],
                        }
                    ],
                }
            ],
        }
        engine = HarvestEvaluationEngine(deepcopy(evaluation_catalog))

        first = engine.rank_node_resource(
            node_catalog,
            node_id="duplicate-resource-node",
            node_resource_id="berry-entry-0",
        )
        second = engine.rank_node_resource(
            node_catalog,
            node_id="duplicate-resource-node",
            node_resource_id="berry-entry-1",
        )

        self.assertEqual(first["items"][0]["resourceEntryIndex"], 0)
        self.assertEqual(second["items"][0]["resourceEntryIndex"], 1)
        self.assertAlmostEqual(first["items"][0]["resourceWeightShare"], 2 / 3)
        self.assertAlmostEqual(second["items"][0]["resourceWeightShare"], 1 / 3)
        self.assertAlmostEqual(
            first["items"][0]["engineComparisonIndex"],
            second["items"][0]["engineComparisonIndex"] * 2,
        )

    def test_ranking_best_relative_score_and_ties_use_only_complete_node_yield(self):
        node_catalog = {
            "dataset": {},
            "nodes": [
                {
                    "id": "node",
                    "name": "Node",
                    "objectPath": "/Game/Nodes/Node.Node",
                    "harvestComponent": {"packagePath": "/Game/Components/Test"},
                    "resources": {
                        "items": [
                            {
                                "entryIndex": 0,
                                "resource": "PrimalItemResource_Test_C",
                                "nodeResourceId": "resource",
                            }
                        ]
                    },
                }
            ],
        }
        evaluation_catalog = {
            "schema": EVALUATION_CATALOG_SCHEMA,
            "methodology": {"usageScope": "TAMED_RIDDEN"},
            "coverage": {"claimsAllCreatures": True},
            "components": [
                {
                    "objectPath": "/Game/Components/Test.Test",
                    "resourceEntries": [],
                    "damageEntries": [],
                }
            ],
            "damageTypeParents": {},
            "resourceDamageOverrides": [],
            "damageTypeGaps": {},
            "creatures": [
                {
                    "name": "Alpha",
                    "speciesKey": "alpha",
                    "objectPath": "/Game/Dinos/Alpha",
                    "tameability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Alpha hit",
                            "mockYield": 100.0,
                            "legacyScore": 1.0,
                        }
                    ],
                },
                {
                    "name": "Beta",
                    "speciesKey": "beta",
                    "objectPath": "/Game/Dinos/Beta",
                    "tameability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Beta hit",
                            "mockYield": 100.0,
                            "legacyScore": 999999.0,
                        }
                    ],
                },
                {
                    "name": "Gamma",
                    "speciesKey": "gamma",
                    "objectPath": "/Game/Dinos/Gamma",
                    "tameability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Old-index winner",
                            "mockYield": 50.0,
                            "legacyScore": 1000000.0,
                        },
                        {
                            "attackIndex": 1,
                            "attackName": "Yield winner",
                            "mockYield": 75.0,
                            "legacyScore": 0.0,
                        },
                    ],
                },
            ],
        }

        def fake_evaluate_attack_resource(**kwargs):
            attack = kwargs["attack"]
            return {
                "rankingStatus": "RANKED",
                "creature": kwargs["creature"],
                "creatureObjectPath": kwargs["creature_object_path"],
                "attackIndex": attack["attackIndex"],
                "attackName": attack["attackName"],
                "estimatedYieldPerNode": attack["mockYield"],
                # Deliberately contradictory legacy values prove that no
                # ordering, best-attack, rank, or relative calculation reads it.
                "engineComparisonIndex": attack["legacyScore"],
            }

        with patch(
            "blueprint_translator.harvest.evaluation.engine.evaluate_attack_resource",
            side_effect=fake_evaluate_attack_resource,
        ):
            result = HarvestEvaluationEngine(evaluation_catalog).rank_node_resource(
                node_catalog,
                node_id="node",
                node_resource_id="resource",
            )

        self.assertEqual(
            [row["speciesKey"] for row in result["items"]],
            ["alpha", "beta", "gamma"],
        )
        self.assertEqual(
            [row["estimatedYieldPerNode"] for row in result["items"]],
            [100.0, 100.0, 75.0],
        )
        self.assertEqual([row["rank"] for row in result["items"]], [1, 1, 3])
        self.assertEqual(
            [row["relativeToNodeTopPercent"] for row in result["items"]],
            [100.0, 100.0, 75.0],
        )
        self.assertEqual(result["items"][2]["attackIndex"], 1)


if __name__ == "__main__":
    unittest.main()
