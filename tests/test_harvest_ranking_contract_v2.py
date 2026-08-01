from __future__ import annotations

import sys
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    EVALUATION_CATALOG_SCHEMA,
    HARVEST_RANKING_CONTRACT_VERSION,
    METRIC_STATIC_CYCLE_SPEED,
    METRIC_STATIC_TOTAL,
    METRIC_OBSERVED_PER_NODE,
    POLICY_CONFIRMED,
    POLICY_INCLUDE_CONDITIONAL,
    VARIANT_BEST_DISCOVERED_EXPLORATORY,
    VARIANT_CANONICAL,
    HarvestEvaluationEngine,
)
from blueprint_translator.harvest_node_repository import HarvestNodeRepository  # noqa: E402
from blueprint_translator.harvest_ranking import YIELD_MODEL_VERSION  # noqa: E402


def _node_catalog() -> dict[str, object]:
    return {
        "dataset": {
            "revision": "1" * 64,
            "componentDatasetRevision": "2" * 64,
            "evaluationDatasetRevision": "3" * 64,
        },
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
                            "displayName": "Test resource",
                            "nodeResourceId": "resource",
                        }
                    ]
                },
            }
        ],
    }


def _creature(
    name: str,
    species: str,
    object_path: str,
    score: float,
    *,
    conditional: bool = False,
    effectiveness: float = 1.0,
    interval: float = 1.0,
) -> dict[str, object]:
    return {
        "name": name,
        "speciesKey": species,
        "objectPath": object_path,
        "tameability": {"status": "ALLOWED", "reasonCodes": []},
        "rideability": {"status": "ALLOWED", "reasonCodes": []},
        "attacks": [
            {
                "attackIndex": 0,
                "attackName": "Harvest",
                "mockYield": score,
                "attackInterval": interval,
                "useBlueprintCanRiderAttack": conditional,
                "effectivenessQuantityMultiplier": effectiveness,
                "gaps": [],
            }
        ],
    }


def _evaluation_catalog() -> dict[str, object]:
    return {
        "schema": EVALUATION_CATALOG_SCHEMA,
        "dataset": {
            "revision": "3" * 64,
            "componentDatasetRevision": "2" * 64,
            "extractorVersion": "extractor-test/v1",
        },
        "methodology": {
            "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
            "usageScope": "TAMED_RIDDEN",
            "rideabilityRequirement": "B_ALLOW_RIDING_TRUE",
        },
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
            _creature(
                "Alpha canonical",
                "alpha",
                "/Game/PrimalEarth/Dinos/Alpha/Alpha_Character_BP.Alpha_Character_BP",
                10.0,
            ),
            _creature(
                "Alpha event variant",
                "alpha",
                "/Game/Mods/Event/Alpha_Character_BP.Alpha_Character_BP",
                100.0,
            ),
            _creature(
                "Beta conditional",
                "beta",
                "/Game/PrimalEarth/Dinos/Beta/Beta_Character_BP.Beta_Character_BP",
                1000.0,
                conditional=True,
            ),
            _creature(
                "Gamma confirmed",
                "gamma",
                "/Game/PrimalEarth/Dinos/Gamma/Gamma_Character_BP.Gamma_Character_BP",
                20.0,
                interval=2.0,
            ),
        ],
    }


def _fake_evaluate_attack_resource(**kwargs: object) -> dict[str, object]:
    attack = kwargs["attack"]
    assert isinstance(attack, dict)
    score = float(attack["mockYield"])
    interval = float(attack["attackInterval"])
    return {
        "rankingStatus": "RANKED",
        "creature": kwargs["creature"],
        "creatureObjectPath": kwargs["creature_object_path"],
        "attackIndex": attack["attackIndex"],
        "attackName": attack["attackName"],
        "attackInterval": interval,
        "estimatedHitsToDepleteNode": 2,
        "estimatedYieldPerNode": score,
        "engineComparisonIndex": score,
        "effectivenessQuantityMultiplier": attack[
            "effectivenessQuantityMultiplier"
        ],
        "missingFacts": [],
        "warnings": [],
        "scoreBreakdown": {"metric": "estimatedYieldPerNode"},
    }


class HarvestRankingContractV2Tests(unittest.TestCase):
    def _rank(self, **kwargs: object) -> dict[str, object]:
        with patch(
            "blueprint_translator.harvest.evaluation.engine.evaluate_attack_resource",
            side_effect=_fake_evaluate_attack_resource,
        ):
            return HarvestEvaluationEngine(_evaluation_catalog()).rank_node_resource(
                _node_catalog(),
                node_id="node",
                node_resource_id="resource",
                **kwargs,
            )

    def test_defaults_are_confirmed_canonical_static_total_and_global_transfer(self):
        result = self._rank()

        self.assertEqual(result["contractVersion"], HARVEST_RANKING_CONTRACT_VERSION)
        self.assertEqual(result["queryPolicy"]["evidence"], POLICY_CONFIRMED)
        self.assertEqual(result["queryPolicy"]["variant"], VARIANT_CANONICAL)
        self.assertEqual(result["queryPolicy"]["metric"], METRIC_STATIC_TOTAL)
        self.assertEqual(
            result["queryPolicy"]["availability"],
            AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        )
        self.assertEqual(
            [row["speciesKey"] for row in result["confirmedItems"]],
            ["gamma", "alpha"],
        )
        self.assertEqual(result["conditionalItems"], [])
        self.assertEqual(result["items"], result["confirmedItems"])
        self.assertEqual(result["confirmedItems"][1]["staticCompleteNodeTargetYield"], 10.0)
        self.assertEqual(result["confirmedItems"][1]["estimatedYieldPerNode"], 10.0)
        self.assertEqual(result["confirmedItems"][0]["runtimeStatus"], "NOT_MEASURED")
        self.assertIsNone(result["confirmedItems"][0]["observedYieldPerNode"])

    def test_conditional_high_score_never_changes_confirmed_rank_or_baseline(self):
        result = self._rank(evidence_policy=POLICY_INCLUDE_CONDITIONAL)

        self.assertEqual(
            [row["speciesKey"] for row in result["confirmedItems"]],
            ["gamma", "alpha"],
        )
        self.assertEqual([row["rank"] for row in result["confirmedItems"]], [1, 2])
        self.assertEqual(
            [row["speciesKey"] for row in result["conditionalItems"]], ["beta"]
        )
        self.assertEqual(result["conditionalItems"][0]["rank"], 1)
        self.assertEqual(result["confirmedItems"][0]["relativeToNodeTopPercent"], 100.0)
        self.assertEqual(result["confirmedItems"][1]["relativeToNodeTopPercent"], 50.0)

    def test_same_variant_conditional_attack_does_not_suppress_confirmed_attack(self):
        catalog = _evaluation_catalog()
        canonical = catalog["creatures"][0]
        conditional_attack = deepcopy(canonical["attacks"][0])
        conditional_attack.update(
            {
                "attackIndex": 1,
                "attackName": "Conditional burst",
                "mockYield": 500.0,
                "useBlueprintCanRiderAttack": True,
            }
        )
        canonical["attacks"].append(conditional_attack)

        with patch(
            "blueprint_translator.harvest.evaluation.engine.evaluate_attack_resource",
            side_effect=_fake_evaluate_attack_resource,
        ):
            result = HarvestEvaluationEngine(catalog).rank_node_resource(
                _node_catalog(),
                node_id="node",
                node_resource_id="resource",
                evidence_policy=POLICY_INCLUDE_CONDITIONAL,
            )

        confirmed_alpha = next(
            row for row in result["confirmedItems"] if row["speciesKey"] == "alpha"
        )
        conditional_alpha = next(
            row for row in result["conditionalItems"] if row["speciesKey"] == "alpha"
        )
        self.assertEqual(confirmed_alpha["attackIndex"], 0)
        self.assertEqual(confirmed_alpha["staticCompleteNodeTargetYield"], 10.0)
        self.assertEqual(conditional_alpha["attackIndex"], 1)
        self.assertEqual(conditional_alpha["staticCompleteNodeTargetYield"], 500.0)

    def test_canonical_variant_is_stable_and_exploratory_max_is_explicit(self):
        canonical = self._rank()
        alpha = next(
            row for row in canonical["confirmedItems"] if row["speciesKey"] == "alpha"
        )
        self.assertEqual(alpha["staticCompleteNodeTargetYield"], 10.0)
        self.assertEqual(
            alpha["variantSelection"]["selectedObjectPath"],
            "/Game/PrimalEarth/Dinos/Alpha/Alpha_Character_BP.Alpha_Character_BP",
        )
        self.assertTrue(alpha["variantSelection"]["higherExploratoryVariantExists"])

        exploratory = self._rank(
            variant_policy=VARIANT_BEST_DISCOVERED_EXPLORATORY
        )
        alpha_exploratory = next(
            row
            for row in exploratory["confirmedItems"]
            if row["speciesKey"] == "alpha"
        )
        self.assertEqual(alpha_exploratory["staticCompleteNodeTargetYield"], 100.0)
        self.assertTrue(exploratory["queryPolicy"]["exploratory"])

    def test_cycle_speed_uses_end_of_first_cycle_timing_contract(self):
        result = self._rank(metric=METRIC_STATIC_CYCLE_SPEED)
        by_species = {row["speciesKey"]: row for row in result["confirmedItems"]}

        self.assertEqual(by_species["alpha"]["staticAttackCycleSecondsToDepleteNode"], 2.0)
        self.assertEqual(by_species["alpha"]["staticYieldPerAttackCycleSecond"], 5.0)
        self.assertEqual(by_species["gamma"]["staticYieldPerAttackCycleSecond"], 5.0)
        self.assertEqual([row["rank"] for row in result["confirmedItems"]], [1, 1])
        self.assertEqual(
            result["methodology"]["firstHitTiming"],
            "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE",
        )
        self.assertNotIn("real", result["methodology"]["metricLabel"].casefold())

    def test_non_neutral_effectiveness_is_conditional_until_modeled(self):
        catalog = _evaluation_catalog()
        catalog["creatures"].append(
            _creature(
                "Delta effectiveness",
                "delta",
                "/Game/PrimalEarth/Dinos/Delta/Delta_Character_BP.Delta_Character_BP",
                2000.0,
                effectiveness=2.0,
            )
        )
        with patch(
            "blueprint_translator.harvest.evaluation.engine.evaluate_attack_resource",
            side_effect=_fake_evaluate_attack_resource,
        ):
            result = HarvestEvaluationEngine(catalog).rank_node_resource(
                _node_catalog(),
                node_id="node",
                node_resource_id="resource",
                evidence_policy=POLICY_INCLUDE_CONDITIONAL,
            )

        delta = next(
            row for row in result["conditionalItems"] if row["speciesKey"] == "delta"
        )
        self.assertIn(
            "EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED",
            delta["evidence"]["gaps"],
        )
        self.assertNotIn("delta", {row["speciesKey"] for row in result["confirmedItems"]})

    def test_observed_metric_uses_only_exact_prevalidated_runtime_overlay(self):
        runtime_rows = {
            (
                "node",
                "resource",
                "alpha",
                "/game/primalearth/dinos/alpha/alpha_character_bp.alpha_character_bp",
                0,
            ): {
                "observationSetId": "runtime://test/alpha",
                "synthetic": False,
                "trialCount": 3,
                "observedYieldPerNode": 7.5,
                "observedYieldPerSecond": 2.5,
                "runtimeStatus": "OBSERVED_CONTROLLED_ENVIRONMENT",
            }
        }
        result = self._rank(
            metric=METRIC_OBSERVED_PER_NODE,
            runtime_observations=runtime_rows,
        )

        self.assertEqual([row["speciesKey"] for row in result["confirmedItems"]], ["alpha"])
        self.assertEqual(result["confirmedItems"][0]["observedYieldPerNode"], 7.5)
        self.assertEqual(
            result["confirmedItems"][0]["runtimeObservation"]["synthetic"], False
        )

    def test_empty_confirmed_does_not_promote_conditional(self):
        catalog = _evaluation_catalog()
        catalog["creatures"] = [
            _creature(
                "Only conditional",
                "conditional",
                "/Game/PrimalEarth/Dinos/Conditional/Conditional_Character_BP.Conditional_Character_BP",
                50.0,
                conditional=True,
            )
        ]
        with patch(
            "blueprint_translator.harvest.evaluation.engine.evaluate_attack_resource",
            side_effect=_fake_evaluate_attack_resource,
        ):
            result = HarvestEvaluationEngine(catalog).rank_node_resource(
                _node_catalog(),
                node_id="node",
                node_resource_id="resource",
                evidence_policy=POLICY_INCLUDE_CONDITIONAL,
            )

        self.assertEqual(result["confirmedItems"], [])
        self.assertEqual(result["confirmedStatus"], "UNAVAILABLE")
        self.assertEqual(result["conditionalItems"][0]["rank"], 1)

    def test_invalid_policy_values_fail_closed(self):
        for kwargs in (
            {"evidence_policy": "everything"},
            {"variant_policy": "MAX_SPECIAL_CASE"},
            {"metric": "weightedComposite"},
            {"availability_policy": "ASSUME_EVERY_MAP"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self._rank(**kwargs)

    def test_lazy_cache_key_contains_every_identity_policy_metric_and_entry_index(self):
        repository = HarvestNodeRepository(Path("unused"), Path("unused"))
        evaluation = _evaluation_catalog()
        engine = Mock()
        engine.rank_node_resource.return_value = {
            "schema": "blueprint-to-code.harvest-ranking-result/v4",
            "identity": {},
            "dataset": {},
            "coverage": {},
            "confirmedItems": [],
            "conditionalItems": [],
            "items": [],
        }
        common = {
            "node_id": "node",
            "node_resource_id": "resource",
            "limit": 10,
            "evidence_policy": POLICY_CONFIRMED,
            "variant_policy": VARIANT_CANONICAL,
            "availability_policy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        }

        repository._lazy_rankings(  # noqa: SLF001 - cache contract test
            _node_catalog(), evaluation, engine, metric=METRIC_STATIC_TOTAL, **common
        )
        repository._lazy_rankings(  # noqa: SLF001 - cache contract test
            _node_catalog(), evaluation, engine, metric=METRIC_STATIC_CYCLE_SPEED, **common
        )
        repository._lazy_rankings(  # noqa: SLF001 - proves same identity is reused
            _node_catalog(), evaluation, engine, metric=METRIC_STATIC_TOTAL, **common
        )

        self.assertEqual(engine.rank_node_resource.call_count, 2)
        self.assertEqual(len(repository._lazy_ranking_cache), 2)  # noqa: SLF001
        key = next(iter(repository._lazy_ranking_cache))  # noqa: SLF001
        for expected in (
            "extractor-test/v1",
            YIELD_MODEL_VERSION,
            "1" * 64,
            "2" * 64,
            "3" * 64,
            POLICY_CONFIRMED,
            VARIANT_CANONICAL,
            AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
            0,
        ):
            self.assertIn(expected, key)

    def test_v2_repository_rejects_stale_model_policy_and_missing_extractor(self):
        invalid = []
        for field, value in (
            ("formulaVersion", "stale-model"),
            ("policyVersion", "stale-policy"),
        ):
            payload = _evaluation_catalog()
            payload["methodology"][field] = value
            payload["methodology"].setdefault(
                "formulaVersion", YIELD_MODEL_VERSION
            )
            payload["methodology"].setdefault(
                "policyVersion",
                "harvest-ranking-policy/v2-confirmed-canonical-relative-specialty",
            )
            invalid.append((field, payload))
        missing_extractor = _evaluation_catalog()
        missing_extractor["methodology"].update(
            {
                "formulaVersion": YIELD_MODEL_VERSION,
                "policyVersion": "harvest-ranking-policy/v2-confirmed-canonical-relative-specialty",
            }
        )
        missing_extractor["dataset"].pop("extractorVersion")
        invalid.append(("extractorVersion", missing_extractor))

        for label, payload in invalid:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                evaluation_path = root / "evaluation.json"
                evaluation_path.write_text(json.dumps(payload), encoding="utf-8")
                repository = HarvestNodeRepository(
                    root / "nodes.json",
                    root / "ranking.json",
                    evaluation_catalog_path=evaluation_path,
                )
                with self.assertRaises(ValueError):
                    repository._load_evaluation()  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
