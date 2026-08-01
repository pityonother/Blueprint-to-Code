from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    EVALUATION_CATALOG_SCHEMA,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    POLICY_INCLUDE_CONDITIONAL,
)
from blueprint_translator.harvest_node_repository import (  # noqa: E402
    HarvestNodeRepository,
)
from blueprint_translator.harvest_ranking import YIELD_MODEL_VERSION  # noqa: E402


class HarvestSpecialtiesContractV2Tests(unittest.TestCase):
    def test_reverse_specialties_sort_relative_before_absolute_and_keep_tiers_split(self):
        node_revision = "1" * 64
        component_revision = "2" * 64
        evaluation_revision = "3" * 64
        node_catalog = {
            "schema": "ark-resource-node-catalog/v1",
            "dataset": {
                "revision": node_revision,
                "componentDatasetRevision": component_revision,
                "evaluationDatasetRevision": evaluation_revision,
            },
            "nodes": [
                {
                    "id": "node-high-absolute",
                    "name": "A high absolute",
                    "objectPath": "/Game/Nodes/A.A",
                    "harvestComponent": {"packagePath": "/Game/Components/A"},
                    "resources": {
                        "items": [
                            {
                                "entryIndex": 0,
                                "nodeResourceId": "a-resource",
                                "resource": "PrimalItemResource_Test_C",
                                "displayName": "Test",
                            }
                        ]
                    },
                },
                {
                    "id": "node-relative-top",
                    "name": "B relative top",
                    "objectPath": "/Game/Nodes/B.B",
                    "harvestComponent": {"packagePath": "/Game/Components/B"},
                    "resources": {
                        "items": [
                            {
                                "entryIndex": 0,
                                "nodeResourceId": "b-resource",
                                "resource": "PrimalItemResource_Test_C",
                                "displayName": "Test",
                            }
                        ]
                    },
                },
            ],
        }
        evaluation = {
            "schema": EVALUATION_CATALOG_SCHEMA,
            "dataset": {
                "revision": evaluation_revision,
                "componentDatasetRevision": component_revision,
                "extractorVersion": "extractor-test/v1",
            },
            "methodology": {
                "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
                "formulaVersion": YIELD_MODEL_VERSION,
                "policyVersion": HARVEST_RANKING_POLICY_VERSION,
                "usageScope": "TAMED_RIDDEN",
                "rideabilityRequirement": "B_ALLOW_RIDING_TRUE",
            },
            "coverage": {"claimsAllCreatures": True},
            "components": [
                {
                    "objectPath": "/Game/Components/A.A",
                    "mockScores": {"Alpha": 100.0, "Beta": 1000.0},
                },
                {
                    "objectPath": "/Game/Components/B.B",
                    "mockScores": {"Alpha": 10.0, "Beta": 5.0},
                },
            ],
            "damageTypeParents": {},
            "resourceDamageOverrides": [],
            "damageTypeGaps": {},
            "creatures": [
                {
                    "name": name,
                    "speciesKey": name.casefold(),
                    "objectPath": f"/Game/PrimalEarth/Dinos/{name}/{name}_Character_BP.{name}_Character_BP",
                    "tameability": {"status": "ALLOWED", "reasonCodes": []},
                    "rideability": {"status": "ALLOWED", "reasonCodes": []},
                    "attacks": [
                        {
                            "attackIndex": 0,
                            "attackName": "Harvest",
                            "attackInterval": 1.0,
                            "useBlueprintCanRiderAttack": False,
                            "effectivenessQuantityMultiplier": 1.0,
                            "gaps": [],
                        }
                    ],
                }
                for name in ("Alpha", "Beta")
            ],
        }

        def fake_evaluate(**kwargs: object) -> dict[str, object]:
            component = kwargs["component"]
            assert isinstance(component, dict)
            score = float(component["mockScores"][kwargs["creature"]])
            return {
                "rankingStatus": "RANKED",
                "creature": kwargs["creature"],
                "creatureObjectPath": kwargs["creature_object_path"],
                "attackIndex": 0,
                "attackName": "Harvest",
                "attackInterval": 1.0,
                "estimatedHitsToDepleteNode": 1,
                "estimatedYieldPerNode": score,
                "engineComparisonIndex": score,
                "effectivenessQuantityMultiplier": 1.0,
                "missingFacts": [],
                "warnings": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "nodes.json"
            evaluation_path = root / "evaluation.json"
            ranking_path = root / "unused-ranking.json"
            catalog_path.write_text(json.dumps(node_catalog), encoding="utf-8")
            evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
            ranking_path.write_text(
                json.dumps({"schema": "ark-harvest-ranking/v2", "bestRows": []}),
                encoding="utf-8",
            )
            repository = HarvestNodeRepository(
                catalog_path,
                ranking_path,
                evaluation_catalog_path=evaluation_path,
            )
            with patch(
                "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
                side_effect=fake_evaluate,
            ):
                result = repository.creature_specialties(
                    "alpha",
                    evidence_policy=POLICY_INCLUDE_CONDITIONAL,
                )

        self.assertEqual(
            [row["node"]["id"] for row in result["confirmedItems"]],
            ["node-relative-top", "node-high-absolute"],
        )
        self.assertEqual(
            [row["relativeToNodeTopPercent"] for row in result["confirmedItems"]],
            [100.0, 10.0],
        )
        self.assertEqual(
            [row["staticCompleteNodeTargetYield"] for row in result["confirmedItems"]],
            [10.0, 100.0],
        )
        self.assertEqual(result["conditionalItems"], [])
        self.assertIn("relativeToNodeTopPercent DESC", result["methodology"]["sortMetric"])
        self.assertEqual(result["confirmedItems"][0]["rank"], 1)
        self.assertEqual(result["confirmedItems"][1]["rank"], 2)


if __name__ == "__main__":
    unittest.main()
