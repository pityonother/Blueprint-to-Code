import ast
from contextlib import redirect_stdout
import io
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_ranking_verifier import (  # noqa: E402
    deterministic_targets,
    independently_rank_target,
    verify_catalogs,
)
from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    HarvestEvaluationEngine,
)
from verify_ark_harvest_rankings import main  # noqa: E402


COMPONENT_REVISION = "a" * 64
EVALUATION_REVISION = "b" * 64


def _catalogs():
    node_catalog = {
        "schema": "ark-resource-node-catalog/v1",
        "dataset": {
            "componentDatasetRevision": COMPONENT_REVISION,
            "rankingDatasetRevision": COMPONENT_REVISION,
            "evaluationDatasetRevision": EVALUATION_REVISION,
        },
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
                        },
                        {
                            "entryIndex": 1,
                            "resource": "PrimalItemResource_Stone_C",
                            "nodeResourceId": "stone-entry",
                        },
                    ]
                },
            }
        ],
    }
    evaluation_catalog = {
        "schema": "ark-harvest-evaluation-catalog/v2",
        "dataset": {
            "revision": EVALUATION_REVISION,
            "componentDatasetRevision": COMPONENT_REVISION,
        },
        "methodology": {
            "usageScope": "TAMED_RIDDEN",
            "rideabilityRequirement": "B_ALLOW_RIDING_TRUE",
        },
        "coverage": {"claimsAllCreatures": True},
        "components": [
            {
                "component": "MetalHarvestComponent",
                "objectPath": (
                    "/Game/Components/MetalHarvestComponent."
                    "MetalHarvestComponent"
                ),
                "maxHarvestHealth": 150.0,
                "harvestHealthGiveResourceInterval": 40.0,
                "clampResourceHarvestDamage": False,
                "isSingleUnitHarvest": False,
                "resourceEntries": [
                    {
                        "entryIndex": 0,
                        "resource": "PrimalItemResource_Metal_C",
                        "entryWeight": 0.75,
                        "weightOverrides": {"DmgType_Mine_C": 1.5},
                        "overrideQuantityMin": 1.0,
                        "overrideQuantityMax": 2.0,
                        "overrideQuantityRandomPower": 1.0,
                        "minQuantityOverrides": {},
                        "maxQuantityOverrides": {},
                        "gaps": [],
                    },
                    {
                        "entryIndex": 1,
                        "resource": "PrimalItemResource_Stone_C",
                        "entryWeight": 0.25,
                        "weightOverrides": {},
                        "overrideQuantityMin": 1.0,
                        "overrideQuantityMax": 1.0,
                        "overrideQuantityRandomPower": 1.0,
                        "minQuantityOverrides": {},
                        "maxQuantityOverrides": {},
                        "gaps": [],
                    },
                ],
                "damageEntries": [
                    {
                        "damageTypeParent": "DmgType_Mine_C",
                        "damageMultiplier": 2.0,
                        "harvestQuantityMultiplier": 1.2,
                        "damageHarvestAdditionalEffectiveness": 0.0,
                        "gaps": [],
                    }
                ],
                "gaps": [],
                "rankingGaps": [],
            }
        ],
        "damageTypeParents": {},
        "damageTypeGaps": {},
        "resourceDamageOverrides": [],
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
                        "damageType": "DmgType_Mine_C",
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
                "name": "Doedicurus",
                "speciesKey": "doed",
                "objectPath": "/Game/Dinos/Doed",
                "tameability": {"status": "UNKNOWN", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Tail",
                        "damageType": "DmgType_Mine_C",
                        "baseDamage": 30.0,
                        "attackInterval": 1.0,
                        "riderAttackInterval": 1.0,
                        "skipTamed": False,
                        "preventWithRider": False,
                        "gaps": [],
                    },
                    {
                        "attackIndex": 1,
                        "attackName": "Blocked",
                        "damageType": "DmgType_Mine_C",
                        "baseDamage": 9999.0,
                        "attackInterval": 0.01,
                        "preventWithRider": True,
                        "gaps": [],
                    },
                ],
            },
            {
                "name": "Unridden",
                "speciesKey": "unridden",
                "objectPath": "/Game/Dinos/Unridden",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {
                    "status": "UNKNOWN",
                    "reasonCodes": ["RIDEABILITY_NOT_RECOVERED"],
                },
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Cheat",
                        "damageType": "DmgType_Mine_C",
                        "baseDamage": 9999.0,
                        "attackInterval": 0.01,
                        "gaps": [],
                    }
                ],
            },
        ],
    }
    return node_catalog, evaluation_catalog


class HarvestRankingVerifierTests(unittest.TestCase):
    def test_independent_formula_module_has_no_production_formula_import_or_call(self):
        source_path = (
            ROOT
            / "scripts"
            / "blueprint_translator"
            / "harvest_ranking_verifier.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertNotIn("blueprint_translator.harvest_ranking", imported_modules)
        self.assertNotIn(
            "blueprint_translator.harvest_evaluation_catalog", imported_modules
        )
        self.assertNotIn("evaluate_attack_resource", called_names)
        self.assertNotIn("HarvestEvaluationEngine", called_names)

    def test_v2_verifier_checks_confirmed_canonical_contract_without_runtime_gold(self):
        nodes, evaluation = _catalogs()
        evaluation["methodology"]["contractVersion"] = "harvest-ranking-contract/v2"
        for entry in evaluation["components"][0]["resourceEntries"]:
            entry["effectivenessQuantityMultiplier"] = 1.0
        evaluation["creatures"][0]["attacks"][0][
            "useBlueprintCanRiderAttack"
        ] = False
        evaluation["creatures"][1]["tameability"] = {
            "status": "ALLOWED",
            "reasonCodes": [],
        }
        engine = HarvestEvaluationEngine(evaluation)

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=lambda node_id, resource_id, limit: engine.rank_node_resource(
                nodes,
                node_id=node_id,
                node_resource_id=resource_id,
                limit=limit,
                evidence_policy="includeConditional",
            ),
            sample_size=2,
            seed="v2-contract",
        )

        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(
            summary["verificationBoundary"]["rankingContractVersion"],
            "harvest-ranking-contract/v2",
        )
        self.assertFalse(
            summary["verificationBoundary"]["runtimeGoldCreatedByVerifier"]
        )
        self.assertEqual(
            summary["methodology"]["metric"],
            "staticCompleteNodeTargetYield",
        )
        self.assertGreater(summary["comparison"]["expectedTopRows"], 0)

    def test_independent_formula_recomputes_complete_node_yield_and_top_order(self):
        nodes, evaluation = _catalogs()

        result = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            limit=10,
        )

        self.assertEqual([row["speciesKey"] for row in result["items"]], ["anky", "doed"])
        # Two 100-health-loss hits, each floor(100 / (40 / 2)) * 1.2 = 6
        # grant calls.  Metal share is 1.5 / 1.75 and expected quantity is 1.5.
        anky = next(row for row in result["items"] if row["speciesKey"] == "anky")
        self.assertAlmostEqual(anky["estimatedYieldPerNode"], 15.4285714286)
        self.assertEqual(
            anky["engineComparisonIndex"], anky["estimatedYieldPerNode"]
        )
        self.assertEqual(anky["estimatedGrantCallsPerNode"], 12)
        self.assertEqual(anky["rankingTier"], "CONDITIONAL")
        self.assertEqual(anky["evidence"]["status"], "PARTIAL")
        self.assertIn(
            "BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED",
            anky["evidence"]["gaps"],
        )
        self.assertEqual(result["coverage"]["attacksExcludedByScope"], 1)
        self.assertEqual(result["coverage"]["attacksConditionallyEvaluated"], 1)
        self.assertEqual(
            result["coverage"]["conditionalEvaluationByReason"],
            {"BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED": 1},
        )
        self.assertEqual(result["coverage"]["attacksExcludedByCreatureScope"], 1)
        self.assertEqual(result["coverage"]["attacksRanked"], 2)

    def test_attack_interval_does_not_change_complete_node_yield_or_order(self):
        nodes, evaluation = _catalogs()
        baseline = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            limit=10,
        )
        evaluation["creatures"][1]["attacks"][0]["attackInterval"] = 0.01
        evaluation["creatures"][1]["attacks"][0]["riderAttackInterval"] = 0.01
        accelerated = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            limit=10,
        )

        self.assertEqual(
            [row["speciesKey"] for row in accelerated["items"]],
            [row["speciesKey"] for row in baseline["items"]],
        )
        self.assertEqual(
            [row["estimatedYieldPerNode"] for row in accelerated["items"]],
            [row["estimatedYieldPerNode"] for row in baseline["items"]],
        )

    def test_equal_yield_species_variants_use_stable_identity_tie_break(self):
        nodes, evaluation = _catalogs()
        later_variant = deepcopy(evaluation["creatures"][0])
        later_variant["objectPath"] = "/Game/Dinos/ZAnkyVariant"
        evaluation["creatures"].insert(0, later_variant)

        result = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            limit=10,
        )

        anky = next(row for row in result["items"] if row["speciesKey"] == "anky")
        self.assertEqual(anky["creatureObjectPath"], "/Game/Dinos/Anky")

    def test_streetlight_complete_node_yield_places_doed_ahead_of_dreadnoughtus(self):
        nodes, evaluation = _catalogs()
        nodes["nodes"][0]["id"] = "streetlight"
        nodes["nodes"][0]["name"] = "Extinction City Streetlight"
        nodes["nodes"][0]["resources"]["items"] = [
            {
                "entryIndex": 0,
                "resource": "PrimalItemResource_Electronics_C",
                "nodeResourceId": "electronics-entry",
            }
        ]
        component = evaluation["components"][0]
        component["maxHarvestHealth"] = 150.0
        component["harvestHealthGiveResourceInterval"] = 40.0
        component["clampResourceHarvestDamage"] = False
        component["resourceEntries"] = [
            {
                "entryIndex": 0,
                "resource": "PrimalItemResource_Electronics_C",
                "entryWeight": 1.0,
                "weightOverrides": {},
                "overrideQuantityMin": 0.0,
                "overrideQuantityMax": 1.0,
                "overrideQuantityRandomPower": 1.0,
                "minQuantityOverrides": {},
                "maxQuantityOverrides": {},
                "gaps": [],
            }
        ]
        component["damageEntries"] = [
            {
                "damageTypeParent": "DmgType_Dread_C",
                "damageMultiplier": 1.0,
                "harvestQuantityMultiplier": 1.0,
                "damageHarvestAdditionalEffectiveness": 0.0,
                "gaps": [],
            },
            {
                "damageTypeParent": "DmgType_Doed_C",
                "damageMultiplier": 3.0,
                "harvestQuantityMultiplier": 7.0,
                "damageHarvestAdditionalEffectiveness": 0.0,
                "gaps": [],
            },
        ]
        evaluation["creatures"] = [
            {
                "name": "Dreadnoughtus",
                "speciesKey": "dreadnoughtus",
                "objectPath": "/Game/Dinos/Dreadnoughtus",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Neck Flail",
                        "damageType": "DmgType_Dread_C",
                        "baseDamage": 1080.0,
                        "attackInterval": 0.5,
                        # A stale caller-provided legacy score must be ignored.
                        "engineComparisonIndex": 999999.0,
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
                        "attackName": "Tail Attack",
                        "damageType": "DmgType_Doed_C",
                        "baseDamage": 32.0,
                        "attackInterval": 0.67,
                        "engineComparisonIndex": 1.0,
                        "gaps": [],
                    }
                ],
            },
        ]

        result = independently_rank_target(
            nodes,
            evaluation,
            node_id="streetlight",
            node_resource_id="electronics-entry",
            limit=10,
        )

        self.assertEqual(
            [row["speciesKey"] for row in result["items"]],
            ["doed", "dreadnoughtus"],
        )
        by_species = {row["speciesKey"]: row for row in result["items"]}
        self.assertEqual(by_species["doed"]["estimatedGrantCallsPerNode"], 56)
        self.assertEqual(
            by_species["dreadnoughtus"]["estimatedGrantCallsPerNode"], 26
        )
        self.assertAlmostEqual(by_species["doed"]["estimatedYieldPerNode"], 28.0)
        self.assertAlmostEqual(
            by_species["dreadnoughtus"]["estimatedYieldPerNode"], 13.0
        )

    def test_unsupported_native_or_blueprint_models_fail_closed(self):
        for scenario in (
            "single-unit",
            "nonzero-effectiveness",
            "nonlinear-random-power",
            "unrecovered-quantity-override",
            "blueprint-output-damage",
        ):
            with self.subTest(scenario=scenario):
                nodes, evaluation = _catalogs()
                evaluation["creatures"] = [evaluation["creatures"][0]]
                component = evaluation["components"][0]
                attack = evaluation["creatures"][0]["attacks"][0]
                if scenario == "single-unit":
                    component["isSingleUnitHarvest"] = True
                elif scenario == "nonzero-effectiveness":
                    component["damageEntries"][0][
                        "damageHarvestAdditionalEffectiveness"
                    ] = 0.25
                elif scenario == "nonlinear-random-power":
                    component["resourceEntries"][0][
                        "overrideQuantityRandomPower"
                    ] = 2.0
                elif scenario == "unrecovered-quantity-override":
                    component["resourceEntries"][0]["damageTypeEntryValues"] = [
                        "DmgType_Mine_C"
                    ]
                    component["resourceEntries"][0]["rankingGaps"] = [
                        "DAMAGE_TYPE_MIN_QUANTITY_OVERRIDE_NOT_RECOVERED"
                    ]
                else:
                    attack["useBlueprintAdjustOutputDamage"] = True

                result = independently_rank_target(
                    nodes,
                    evaluation,
                    node_id="metal-node",
                    node_resource_id="metal-entry",
                    limit=10,
                )

                self.assertEqual(result["items"], [])
                self.assertEqual(result["coverage"]["attacksUnranked"], 1)

    def test_deterministic_sampling_is_stable_and_seeded(self):
        nodes, evaluation = _catalogs()
        first = deterministic_targets(nodes, evaluation, sample_size=1, seed="alpha")
        second = deterministic_targets(nodes, evaluation, sample_size=1, seed="alpha")
        other = deterministic_targets(nodes, evaluation, sample_size=1, seed="beta")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertNotEqual(first, other)

    def test_verification_detects_reference_score_and_eligibility_drift(self):
        nodes, evaluation = _catalogs()

        def wrong_reference(node_id, node_resource_id, limit):
            result = independently_rank_target(
                nodes,
                evaluation,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=limit,
            )
            result = deepcopy(result)
            result["items"][0]["estimatedYieldPerNode"] += 1.0
            result["items"][0]["engineComparisonIndex"] = result["items"][0][
                "estimatedYieldPerNode"
            ]
            result["coverage"]["attacksExcludedByScope"] += 1
            return result

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=wrong_reference,
            sample_size=2,
            seed="drift",
        )

        self.assertEqual(summary["status"], "FAIL")
        self.assertEqual(
            summary["verificationBoundary"],
            {
                "proves": "production implementation == independent implementation",
                "doesNotProve": "static model == real game",
            },
        )
        self.assertGreaterEqual(summary["comparison"]["mismatchCount"], 2)
        fields = {row["field"] for row in summary["mismatches"]}
        self.assertIn("items[0].estimatedYieldPerNode", fields)
        self.assertIn("coverage.attacksExcludedByScope", fields)

    def test_reference_legacy_alias_is_optional_but_cannot_conflict(self):
        nodes, evaluation = _catalogs()

        def without_alias(node_id, node_resource_id, limit):
            result = independently_rank_target(
                nodes,
                evaluation,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=limit,
            )
            result = deepcopy(result)
            for row in result["items"]:
                row.pop("engineComparisonIndex", None)
            return result

        optional_summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=without_alias,
            sample_size=2,
        )
        self.assertEqual(optional_summary["status"], "PASS")

        def conflicting_alias(node_id, node_resource_id, limit):
            result = independently_rank_target(
                nodes,
                evaluation,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=limit,
            )
            result = deepcopy(result)
            result["items"][0]["engineComparisonIndex"] = 999999.0
            return result

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=conflicting_alias,
            sample_size=2,
        )

        self.assertEqual(summary["status"], "FAIL")
        self.assertIn(
            "items[0].engineComparisonIndexAlias",
            {row["field"] for row in summary["mismatches"]},
        )

    def test_verification_does_not_pass_vacuously_without_targets(self):
        nodes, evaluation = _catalogs()
        nodes["nodes"] = []

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=lambda *_args: {},
            sample_size=2,
        )

        self.assertEqual(summary["status"], "FAIL")
        self.assertEqual(summary["comparison"]["targetsCompared"], 0)
        self.assertEqual(
            summary["mismatches"][0]["field"], "selection.targetsEligible"
        )

    def test_cli_returns_zero_on_pass_and_one_on_mismatch(self):
        nodes, evaluation = _catalogs()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node_path = root / "nodes.json"
            evaluation_path = root / "evaluation.json"
            ranking_path = root / "unused.json"
            output_path = root / "verification.json"
            node_path.write_text(json.dumps(nodes), encoding="utf-8")
            evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                passed = main(
                    [
                        "--node-catalog",
                        str(node_path),
                        "--evaluation-catalog",
                        str(evaluation_path),
                        "--ranking-catalog",
                        str(ranking_path),
                        "--all",
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(passed, 0)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["status"], "PASS")

            evaluation["components"][0]["damageEntries"][0]["damageMultiplier"] = 3.0
            evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
            # A separately supplied query result remains at the former value and must fail.
            reference_path = root / "reference.json"
            reference = independently_rank_target(
                nodes,
                _catalogs()[1],
                node_id="metal-node",
                node_resource_id="metal-entry",
                limit=10,
            )
            reference_path.write_text(
                json.dumps({"metal-node::metal-entry": reference}), encoding="utf-8"
            )
            with redirect_stdout(io.StringIO()):
                failed = main(
                    [
                        "--node-catalog",
                        str(node_path),
                        "--evaluation-catalog",
                        str(evaluation_path),
                        "--reference-results",
                        str(reference_path),
                        "--all",
                        "--output",
                        str(output_path),
                    ]
                )
            self.assertEqual(failed, 1)


if __name__ == "__main__":
    unittest.main()
