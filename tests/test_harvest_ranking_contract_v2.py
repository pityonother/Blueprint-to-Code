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
    METRIC_OBSERVED_PER_SECOND,
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
from blueprint_translator.harvest_runtime_observations import (  # noqa: E402
    HarvestRuntimeProfileError,
)


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
    interval: float | None = 1.0,
    hits: int | float | None = 2,
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
                "mockHits": hits,
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
    interval = attack.get("attackInterval")
    return {
        "rankingStatus": "RANKED",
        "creature": kwargs["creature"],
        "creatureObjectPath": kwargs["creature_object_path"],
        "attackIndex": attack["attackIndex"],
        "attackName": attack["attackName"],
        "attackInterval": interval,
        "estimatedHitsToDepleteNode": attack.get("mockHits"),
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
    def _rank_catalog(
        self,
        catalog: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        with patch(
            "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
            side_effect=_fake_evaluate_attack_resource,
        ):
            return HarvestEvaluationEngine(catalog).rank_node_resource(
                _node_catalog(),
                node_id="node",
                node_resource_id="resource",
                **kwargs,
            )

    def _rank(self, **kwargs: object) -> dict[str, object]:
        return self._rank_catalog(_evaluation_catalog(), **kwargs)

    def test_each_metric_reports_its_exact_score_basis_unit_and_runtime_contract(self):
        runtime_rows = {
            (
                "node",
                "resource",
                "alpha",
                "/game/primalearth/dinos/alpha/alpha_character_bp.alpha_character_bp",
                0,
            ): {
                "observationSetId": "runtime://test/alpha",
                "runtimeProfileId": "profile-a",
                "environmentFingerprint": "a" * 64,
                "synthetic": False,
                "trialCount": 3,
                "observedYieldPerNode": 7.5,
                "observedYieldPerSecond": 2.5,
                "runtimeStatus": "OBSERVED_CONFIRMED",
            }
        }
        expected_contracts = {
            METRIC_STATIC_TOTAL: {
                "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
                "unit": "target_resource_units/node",
                "runtime": False,
            },
            METRIC_STATIC_CYCLE_SPEED: {
                "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_ATTACK_CYCLE_SECOND",
                "unit": "target_resource_units/attack_cycle_second",
                "runtime": False,
            },
            METRIC_OBSERVED_PER_NODE: {
                "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
                "unit": "target_resource_units/node",
                "runtime": True,
            },
            METRIC_OBSERVED_PER_SECOND: {
                "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND",
                "unit": "target_resource_units/second",
                "runtime": True,
            },
        }

        for metric, expected in expected_contracts.items():
            with self.subTest(metric=metric):
                result = self._rank(
                    metric=metric,
                    runtime_observations=runtime_rows,
                )
                self.assertEqual(
                    {
                        key: result["methodology"].get(key)
                        for key in ("scoreBasis", "unit", "runtime")
                    },
                    expected,
                )
                self.assertTrue(result["confirmedItems"])
                self.assertTrue(
                    all(
                        row.get("scoreBasis") == expected["scoreBasis"]
                        and row["scoreBreakdown"]["metric"] == metric
                        for row in result["confirmedItems"]
                    )
                )
                if expected["runtime"]:
                    self.assertEqual(
                        result["queryPolicy"]["runtimeProfileId"], "profile-a"
                    )
                warning = result["methodology"]["warning"].casefold()
                if expected["runtime"]:
                    self.assertIn("runtimeprofileid", warning)
                    self.assertNotIn("静态模型", warning)
                else:
                    self.assertIn("静态模型", warning)

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

    def test_compatibility_items_never_include_conditional_rows(self):
        result = self._rank(evidence_policy=POLICY_INCLUDE_CONDITIONAL)

        self.assertEqual(result["items"], result["confirmedItems"])
        self.assertEqual(
            {row["speciesKey"] for row in result["items"]},
            {"alpha", "gamma"},
        )
        self.assertEqual(
            {row["speciesKey"] for row in result["conditionalItems"]},
            {"beta"},
        )

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
            "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
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

    def test_unique_base_variant_exposes_auditable_selection_fields(self):
        result = self._rank()
        alpha = next(
            row for row in result["confirmedItems"] if row["speciesKey"] == "alpha"
        )
        expected = {
            "canonicalObjectPath": (
                "/Game/PrimalEarth/Dinos/Alpha/"
                "Alpha_Character_BP.Alpha_Character_BP"
            ),
            "selectionReasons": ["UNIQUE_BASE_VARIANT"],
            "excludedVariantClasses": ["EVENT"],
            "ambiguous": False,
            "ambiguityReasons": [],
        }

        self.assertEqual(
            {
                key: alpha["variantSelection"].get(key)
                for key in expected
            },
            expected,
        )
        audit = next(
            row
            for row in result["variantSelectionAudits"]
            if row["speciesKey"] == "alpha"
        )
        self.assertEqual({key: audit[key] for key in expected}, expected)

    def test_multiple_base_variants_fail_closed_and_report_ambiguity(self):
        catalog = _evaluation_catalog()
        catalog["creatures"] = [
            _creature(
                "Multi base A",
                "multi-base",
                "/Game/PrimalEarth/Dinos/Multi/BaseA_Character_BP.BaseA_Character_BP",
                10.0,
            ),
            _creature(
                "Multi base B",
                "multi-base",
                "/Game/PrimalEarth/Dinos/Multi/BaseB_Character_BP.BaseB_Character_BP",
                20.0,
            ),
        ]

        result = self._rank_catalog(catalog)

        with self.subTest("default canonical ranking excludes ambiguous species"):
            self.assertNotIn(
                "multi-base",
                {row["speciesKey"] for row in result["confirmedItems"]},
            )
        with self.subTest("audit explains multiple base candidates"):
            audit = next(
                row
                for row in result["variantSelectionAudits"]
                if row["speciesKey"] == "multi-base"
            )
            self.assertIsNone(audit["canonicalObjectPath"])
            self.assertEqual(audit["selectionReasons"], [])
            self.assertEqual(audit["excludedVariantClasses"], [])
            self.assertIs(audit["ambiguous"], True)
            self.assertIn(
                "MULTIPLE_BASE_VARIANT_CANDIDATES",
                audit["ambiguityReasons"],
            )

    def test_unique_ancestry_root_selects_base_and_demotes_derived_base_candidate(self):
        catalog = _evaluation_catalog()
        root_path = (
            "/Game/PrimalEarth/Dinos/Anky/"
            "Anky_Character_BP.Anky_Character_BP"
        )
        child_path = (
            "/Game/PrimalEarth/Dinos/Anky/"
            "Anky_Character_BP_Aberrant.Anky_Character_BP_Aberrant"
        )
        root = _creature("Anky", "anky", root_path, 10.0)
        root["parentChain"] = [
            root_path,
            "/Game/PrimalEarth/CoreBlueprints/Dino_Character_BP.Dino_Character_BP_C",
        ]
        child = _creature("Aberrant Anky", "anky", child_path, 100.0)
        child["parentChain"] = [
            child_path,
            "/Game/PrimalEarth/Dinos/Anky/Anky_Character_BP.Anky_Character_BP_C",
            "/Game/PrimalEarth/CoreBlueprints/Dino_Character_BP.Dino_Character_BP_C",
        ]
        catalog["creatures"] = [child, root]

        result = self._rank_catalog(catalog)
        selected = next(
            row for row in result["confirmedItems"] if row["speciesKey"] == "anky"
        )
        audit = next(
            row
            for row in result["variantSelectionAudits"]
            if row["speciesKey"] == "anky"
        )

        self.assertEqual(selected["creatureObjectPath"], root_path)
        self.assertEqual(audit["canonicalObjectPath"], root_path)
        self.assertEqual(
            audit["selectionReasons"],
            ["UNIQUE_ANCESTRY_ROOT_BASE_VARIANT"],
        )
        self.assertEqual(audit["excludedVariantClasses"], ["UNKNOWN_VARIANT"])
        self.assertIs(audit["ambiguous"], False)

    def test_two_independent_ancestry_roots_fail_closed_without_path_fallback(self):
        catalog = _evaluation_catalog()
        first_path = "/Game/Dinos/Roots/First_Character_BP.First_Character_BP"
        second_path = "/Game/Dinos/Roots/Second_Character_BP.Second_Character_BP"
        first = _creature("First root", "two-roots", first_path, 10.0)
        first["parentChain"] = [
            first_path,
            "/Game/PrimalEarth/CoreBlueprints/Dino_Character_BP.Dino_Character_BP_C",
        ]
        second = _creature("Second root", "two-roots", second_path, 20.0)
        second["parentChain"] = [
            second_path,
            "/Game/PrimalEarth/CoreBlueprints/Dino_Character_BP.Dino_Character_BP_C",
        ]
        catalog["creatures"] = [first, second]

        result = self._rank_catalog(catalog)
        audit = next(
            row
            for row in result["variantSelectionAudits"]
            if row["speciesKey"] == "two-roots"
        )

        self.assertNotIn(
            "two-roots",
            {row["speciesKey"] for row in result["confirmedItems"]},
        )
        self.assertIsNone(audit["canonicalObjectPath"])
        self.assertIn(
            "MULTIPLE_ANCESTRY_ROOT_BASE_VARIANTS",
            audit["ambiguityReasons"],
        )

    def test_no_base_variant_fails_closed_and_reports_excluded_classes(self):
        catalog = _evaluation_catalog()
        catalog["creatures"] = [
            _creature(
                "Event only",
                "no-base",
                "/Game/Events/Summer/NoBase_Character_BP.NoBase_Character_BP",
                30.0,
            ),
            _creature(
                "Mission only",
                "no-base",
                "/Game/Missions/NoBase_Character_BP.NoBase_Character_BP",
                40.0,
            ),
        ]

        result = self._rank_catalog(catalog)

        with self.subTest("default canonical ranking requires a base candidate"):
            self.assertNotIn(
                "no-base",
                {row["speciesKey"] for row in result["confirmedItems"]},
            )
        with self.subTest("audit explains why no canonical path was selected"):
            audit = next(
                row
                for row in result["variantSelectionAudits"]
                if row["speciesKey"] == "no-base"
            )
            self.assertIsNone(audit["canonicalObjectPath"])
            self.assertEqual(audit["selectionReasons"], [])
            self.assertEqual(
                audit["excludedVariantClasses"],
                ["EVENT", "MISSION"],
            )
            self.assertIs(audit["ambiguous"], True)
            self.assertIn("NO_BASE_VARIANT_CANDIDATE", audit["ambiguityReasons"])

    def test_canonical_ambiguity_coverage_counts_all_and_bounds_examples(self):
        catalog = _evaluation_catalog()
        catalog["creatures"] = [
            _creature(
                f"Event-only {index:02d}",
                f"event-only-{index:02d}",
                (
                    f"/Game/Events/Summer/EventOnly{index:02d}_Character_BP."
                    f"EventOnly{index:02d}_Character_BP"
                ),
                float(index + 1),
            )
            for index in range(12)
        ]

        result = self._rank_catalog(catalog)
        examples = result["coverage"]["canonicalVariantAmbiguityExamples"]

        self.assertEqual(result["coverage"]["canonicalVariantAmbiguousSpecies"], 12)
        self.assertGreater(len(examples), 0)
        self.assertLessEqual(len(examples), 10)
        self.assertLess(len(examples), 12)
        self.assertEqual(
            len({row["speciesKey"] for row in examples}),
            len(examples),
        )
        self.assertTrue(all(row["ambiguous"] is True for row in examples))
        self.assertLessEqual(len(result["variantSelectionAudits"]), 10)
        self.assertEqual(result["coverage"]["canonicalVariantsAudited"], 12)
        self.assertEqual(
            result["coverage"]["variantSelectionAuditsOmitted"],
            2,
        )

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

    def test_cycle_metric_handles_one_many_missing_zero_and_negative_intervals(self):
        catalog = _evaluation_catalog()
        catalog["creatures"] = [
            _creature(
                "One hit",
                "one-hit",
                "/Game/PrimalEarth/Dinos/OneHit/OneHit_Character_BP.OneHit_Character_BP",
                12.0,
                hits=1,
                interval=2.0,
            ),
            _creature(
                "Many hits",
                "many-hits",
                "/Game/PrimalEarth/Dinos/ManyHits/ManyHits_Character_BP.ManyHits_Character_BP",
                12.0,
                hits=3,
                interval=2.0,
            ),
            _creature(
                "Missing interval",
                "missing-interval",
                "/Game/PrimalEarth/Dinos/Missing/Missing_Character_BP.Missing_Character_BP",
                12.0,
                hits=2,
                interval=None,
            ),
            _creature(
                "Zero interval",
                "zero-interval",
                "/Game/PrimalEarth/Dinos/Zero/Zero_Character_BP.Zero_Character_BP",
                12.0,
                hits=2,
                interval=0.0,
            ),
            _creature(
                "Negative interval",
                "negative-interval",
                "/Game/PrimalEarth/Dinos/Negative/Negative_Character_BP.Negative_Character_BP",
                12.0,
                hits=2,
                interval=-1.0,
            ),
        ]

        result = self._rank_catalog(catalog, metric=METRIC_STATIC_TOTAL)
        by_species = {row["speciesKey"]: row for row in result["confirmedItems"]}

        self.assertEqual(
            by_species["one-hit"]["staticAttackCycleSecondsToDepleteNode"],
            2.0,
        )
        self.assertEqual(
            by_species["one-hit"]["staticYieldPerAttackCycleSecond"],
            6.0,
        )
        self.assertEqual(
            by_species["many-hits"]["staticAttackCycleSecondsToDepleteNode"],
            6.0,
        )
        self.assertEqual(
            by_species["many-hits"]["staticYieldPerAttackCycleSecond"],
            2.0,
        )
        for species in ("missing-interval", "zero-interval", "negative-interval"):
            with self.subTest(species=species):
                self.assertIsNone(
                    by_species[species]["staticAttackCycleSecondsToDepleteNode"]
                )
                self.assertIsNone(
                    by_species[species]["staticYieldPerAttackCycleSecond"]
                )

    def test_equal_static_totals_rank_by_different_cycle_speeds(self):
        catalog = _evaluation_catalog()
        catalog["creatures"] = [
            _creature(
                "Fast cycle",
                "fast",
                "/Game/PrimalEarth/Dinos/Fast/Fast_Character_BP.Fast_Character_BP",
                12.0,
                hits=2,
                interval=1.0,
            ),
            _creature(
                "Slow cycle",
                "slow",
                "/Game/PrimalEarth/Dinos/Slow/Slow_Character_BP.Slow_Character_BP",
                12.0,
                hits=2,
                interval=3.0,
            ),
        ]

        result = self._rank_catalog(catalog, metric=METRIC_STATIC_CYCLE_SPEED)

        self.assertEqual(
            [row["speciesKey"] for row in result["confirmedItems"]],
            ["fast", "slow"],
        )
        self.assertEqual(
            [row["staticCompleteNodeTargetYield"] for row in result["confirmedItems"]],
            [12.0, 12.0],
        )
        self.assertEqual(
            [row["staticYieldPerAttackCycleSecond"] for row in result["confirmedItems"]],
            [6.0, 2.0],
        )
        self.assertEqual([row["rank"] for row in result["confirmedItems"]], [1, 2])

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
            "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
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
        self.assertEqual(
            {
                key: result["coverage"].get(key)
                for key in (
                    "rowsWithEffectivenessField",
                    "rowsWithNonNeutralEffectiveness",
                    "rowsConditionalBecauseEffectiveness",
                )
            },
            {
                "rowsWithEffectivenessField": 5,
                "rowsWithNonNeutralEffectiveness": 1,
                "rowsConditionalBecauseEffectiveness": 1,
            },
        )

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
                "runtimeProfileId": "profile-a",
                "environmentFingerprint": "a" * 64,
                "synthetic": False,
                "trialCount": 3,
                "observedYieldPerNode": 7.5,
                "observedYieldPerSecond": 2.5,
                "runtimeStatus": "OBSERVED_CONFIRMED",
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

    def test_preliminary_runtime_overlay_requires_opt_in_and_stays_conditional(self):
        runtime_rows = {
            (
                "node",
                "resource",
                "alpha",
                "/game/primalearth/dinos/alpha/alpha_character_bp.alpha_character_bp",
                0,
            ): {
                "observationSetId": "runtime://test/alpha-preliminary",
                "runtimeProfileId": "profile-a",
                "environmentFingerprint": "a" * 64,
                "synthetic": False,
                "trialCount": 1,
                "observedYieldPerNode": 7.5,
                "observedYieldPerSecond": 2.5,
                "runtimeStatus": "OBSERVED_PRELIMINARY",
            }
        }

        excluded = self._rank(
            metric=METRIC_OBSERVED_PER_NODE,
            evidence_policy=POLICY_INCLUDE_CONDITIONAL,
            runtime_observations=runtime_rows,
            runtime_profile_id="profile-a",
            include_preliminary=False,
        )
        included = self._rank(
            metric=METRIC_OBSERVED_PER_NODE,
            evidence_policy=POLICY_INCLUDE_CONDITIONAL,
            runtime_observations=runtime_rows,
            runtime_profile_id="profile-a",
            include_preliminary=True,
        )

        self.assertNotIn(
            "alpha",
            {
                row["speciesKey"]
                for row in [
                    *excluded["confirmedItems"],
                    *excluded["conditionalItems"],
                ]
            },
        )
        self.assertNotIn(
            "alpha",
            {row["speciesKey"] for row in included["confirmedItems"]},
        )
        preliminary = next(
            row
            for row in included["conditionalItems"]
            if row["speciesKey"] == "alpha"
        )
        self.assertEqual(preliminary["runtimeStatus"], "OBSERVED_PRELIMINARY")
        self.assertIn(
            "OBSERVED_PRELIMINARY_MINIMUM_TRIALS_NOT_MET",
            preliminary["evidence"]["gaps"],
        )

    def test_synthetic_runtime_overlay_is_never_ranked_even_if_injected_directly(self):
        runtime_rows = {
            (
                "node",
                "resource",
                "alpha",
                "/game/primalearth/dinos/alpha/alpha_character_bp.alpha_character_bp",
                0,
            ): {
                "observationSetId": "runtime://test/synthetic",
                "runtimeProfileId": "profile-a",
                "environmentFingerprint": "b" * 64,
                "synthetic": True,
                "trialCount": 20,
                "observedYieldPerNode": 999999.0,
                "observedYieldPerSecond": 999999.0,
                "runtimeStatus": "SYNTHETIC_NOT_PUBLISHABLE",
            }
        }

        result = self._rank(
            metric=METRIC_OBSERVED_PER_NODE,
            evidence_policy=POLICY_INCLUDE_CONDITIONAL,
            runtime_observations=runtime_rows,
            include_preliminary=True,
        )

        self.assertNotIn(
            "alpha",
            {
                row["speciesKey"]
                for row in [
                    *result["confirmedItems"],
                    *result["conditionalItems"],
                ]
            },
        )
        self.assertEqual(result["runtimeCoverage"]["syntheticExcluded"], 1)
        with self.assertRaises(HarvestRuntimeProfileError) as raised:
            self._rank(
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_observations=runtime_rows,
                runtime_profile_id="profile-a",
            )
        self.assertEqual(
            raised.exception.code, "HARVEST_RUNTIME_PROFILE_NOT_FOUND"
        )

    def test_runtime_metric_requires_one_profile_and_reports_stable_coverage(self):
        observations = {}
        for profile_id, species, path in (
            (
                "profile-a",
                "alpha",
                "/game/primalearth/dinos/alpha/alpha_character_bp.alpha_character_bp",
            ),
            (
                "profile-b",
                "gamma",
                "/game/primalearth/dinos/gamma/gamma_character_bp.gamma_character_bp",
            ),
        ):
            observations[("node", "resource", species, path, 0)] = {
                "observationSetId": f"runtime://test/{profile_id}/{species}",
                "runtimeProfileId": profile_id,
                "environmentFingerprint": profile_id[-1] * 64,
                "synthetic": False,
                "trialCount": 3,
                "observedYieldPerNode": 7.5,
                "observedYieldPerSecond": 2.5,
                "runtimeStatus": "OBSERVED_CONFIRMED",
            }

        with self.assertRaises(HarvestRuntimeProfileError) as raised:
            self._rank(
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_observations=observations,
            )
        self.assertEqual(raised.exception.code, "HARVEST_RUNTIME_PROFILE_REQUIRED")
        self.assertEqual(
            str(raised.exception),
            "Multiple runtime profiles are available; select runtimeProfileId.",
        )

        static_result = self._rank(
            metric=METRIC_STATIC_TOTAL,
            runtime_observations=observations,
        )
        self.assertTrue(static_result["confirmedItems"])
        self.assertIsNone(static_result["queryPolicy"]["runtimeProfileId"])

        selected = self._rank(
            metric=METRIC_OBSERVED_PER_NODE,
            runtime_observations=observations,
            runtime_profile_id="profile-a",
        )
        self.assertEqual(selected["queryPolicy"]["runtimeProfileId"], "profile-a")
        self.assertEqual(
            selected["runtimeCoverage"],
            {
                "runtimeProfilesAvailable": ["profile-a", "profile-b"],
                "runtimeProfileSelected": "profile-a",
                "publishableConfirmedRows": 1,
                "preliminaryRows": 0,
                "syntheticExcluded": 0,
                "profileMismatchExcluded": 1,
            },
        )

    def test_runtime_metric_with_zero_profiles_is_empty_and_explicit_missing_fails(self):
        result = self._rank(
            metric=METRIC_OBSERVED_PER_NODE,
            runtime_observations={},
        )

        self.assertEqual(result["confirmedStatus"], "UNAVAILABLE")
        self.assertEqual(result["conditionalStatus"], "UNAVAILABLE")
        self.assertEqual(result["items"], [])
        self.assertEqual(
            result["runtimeCoverage"],
            {
                "runtimeProfilesAvailable": [],
                "runtimeProfileSelected": None,
                "publishableConfirmedRows": 0,
                "preliminaryRows": 0,
                "syntheticExcluded": 0,
                "profileMismatchExcluded": 0,
            },
        )
        with self.assertRaises(HarvestRuntimeProfileError) as raised:
            self._rank(
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_observations={},
                runtime_profile_id="missing-profile",
            )
        self.assertEqual(raised.exception.code, "HARVEST_RUNTIME_PROFILE_NOT_FOUND")
        self.assertEqual(
            str(raised.exception),
            "Requested runtimeProfileId 'missing-profile' is not available.",
        )

    def test_direct_runtime_overlay_rejects_invalid_status_and_trial_tier(self):
        runtime_key = (
            "node",
            "resource",
            "alpha",
            "/game/primalearth/dinos/alpha/alpha_character_bp.alpha_character_bp",
            0,
        )
        base = {
            "observationSetId": "runtime://test/status-defense",
            "runtimeProfileId": "profile-a",
            "environmentFingerprint": "d" * 64,
            "synthetic": False,
            "trialCount": 3,
            "observedYieldPerNode": 7.5,
            "observedYieldPerSecond": 2.5,
        }
        invalid_rows = (
            {**base, "runtimeStatus": "OBSERVED_CONTROLLED_ENVIRONMENT"},
            {**base, "runtimeStatus": "SYNTHETIC_NOT_PUBLISHABLE"},
            {**base, "runtimeStatus": "OBSERVED_CONFIRMED", "trialCount": 2},
            {**base, "runtimeStatus": "OBSERVED_PRELIMINARY", "trialCount": 3},
        )

        for observation in invalid_rows:
            with self.subTest(
                status=observation["runtimeStatus"],
                trial_count=observation["trialCount"],
            ):
                result = self._rank(
                    metric=METRIC_OBSERVED_PER_NODE,
                    evidence_policy=POLICY_INCLUDE_CONDITIONAL,
                    runtime_observations={runtime_key: observation},
                    runtime_profile_id="profile-a",
                    include_preliminary=True,
                )
                self.assertEqual(result["items"], [])
                self.assertEqual(result["conditionalItems"], [])

    def test_canonical_audit_covers_full_catalog_before_tamed_ridden_scope(self):
        catalog = _evaluation_catalog()
        excluded = []
        for index in range(12):
            creature = _creature(
                f"Excluded {index}",
                f"excluded-{index:02d}",
                f"/Game/PrimalEarth/Dinos/Excluded{index}/Excluded_Character_BP",
                9999.0,
            )
            creature["tameability"] = {
                "status": "PREVENTED",
                "reasonCodes": ["CREATURE_NOT_TAMEABLE"],
            }
            excluded.append(creature)
        catalog["creatures"].extend(excluded)

        engine = HarvestEvaluationEngine(catalog)
        result = self._rank_catalog(catalog)
        full_audits = engine.canonical_variant_audits()

        self.assertEqual(len(catalog["creatures"]), 16)
        self.assertEqual(len(full_audits), 15)
        self.assertEqual(result["coverage"]["canonicalCreatureAssetsAudited"], 16)
        self.assertEqual(result["coverage"]["canonicalVariantsAudited"], 15)
        self.assertEqual(result["coverage"]["speciesEvaluated"], 3)
        self.assertEqual(len(result["variantSelectionAudits"]), 10)
        self.assertLessEqual(
            len(result["coverage"]["canonicalVariantAmbiguityExamples"]), 10
        )
        self.assertIn(
            "excluded-11", {row["speciesKey"] for row in full_audits}
        )

    def test_selected_runtime_profile_errors_when_only_mismatched_or_unprofiled(self):
        runtime_key = (
            "node",
            "resource",
            "alpha",
            "/game/primalearth/dinos/alpha/alpha_character_bp.alpha_character_bp",
            0,
        )
        base_observation = {
            "observationSetId": "runtime://test/profile-defense",
            "environmentFingerprint": "c" * 64,
            "synthetic": False,
            "trialCount": 3,
            "observedYieldPerNode": 7.5,
            "observedYieldPerSecond": 2.5,
            "runtimeStatus": "OBSERVED_CONFIRMED",
        }

        for runtime_profile_id in ("profile-b", None):
            with self.subTest(runtime_profile_id=runtime_profile_id):
                observation = dict(base_observation)
                if runtime_profile_id is not None:
                    observation["runtimeProfileId"] = runtime_profile_id
                with self.assertRaises(HarvestRuntimeProfileError) as raised:
                    self._rank(
                        metric=METRIC_OBSERVED_PER_NODE,
                        evidence_policy=POLICY_INCLUDE_CONDITIONAL,
                        runtime_observations={runtime_key: observation},
                        runtime_profile_id="profile-a",
                    )
                self.assertEqual(
                    raised.exception.code, "HARVEST_RUNTIME_PROFILE_NOT_FOUND"
                )
                self.assertEqual(
                    str(raised.exception),
                    "Requested runtimeProfileId 'profile-a' is not available.",
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
            "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
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
