import ast
from contextlib import redirect_stdout
import io
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_ranking_verifier import (  # noqa: E402
    V2_METRIC_CONTRACTS,
    deterministic_targets,
    independently_rank_specialties,
    independently_rank_target,
    verify_catalogs,
)
from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    HarvestEvaluationEngine,
    METRIC_CONTRACTS,
)
from blueprint_translator.harvest_node_repository import (  # noqa: E402
    HarvestNodeRepository,
)
from verify_ark_harvest_rankings import build_parser, main  # noqa: E402


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


def _v2_catalogs():
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
    return nodes, evaluation


def _controlled_runtime_observations(*, preliminary=False, synthetic=False):
    status = "OBSERVED_PRELIMINARY" if preliminary else "OBSERVED_CONFIRMED"
    return {
        (
            "metal-node",
            "metal-entry",
            "anky",
            "/game/dinos/anky",
            0,
        ): {
            "observationSetId": "runtime://profile-a/metal/anky/tail",
            "runtimeProfileId": "profile-a",
            "environmentFingerprint": "c" * 64,
            "runtimeStatus": status,
            "trialCount": 2 if preliminary else 3,
            "observedYieldPerNode": 42.0,
            "observedYieldPerSecond": 7.0,
            "synthetic": synthetic,
        },
        (
            "metal-node",
            "metal-entry",
            "doed",
            "/game/dinos/doed",
            0,
        ): {
            "observationSetId": "runtime://profile-a/metal/doed/tail",
            "runtimeProfileId": "profile-a",
            "environmentFingerprint": "c" * 64,
            "runtimeStatus": status,
            "trialCount": 2 if preliminary else 3,
            "observedYieldPerNode": 21.0,
            "observedYieldPerSecond": 10.0,
            "synthetic": synthetic,
        },
    }


def _engine_reference_query(engine, nodes, runtime_observations=None):
    def query(node_id, node_resource_id, limit, options):
        return engine.rank_node_resource(
            nodes,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=limit,
            evidence_policy=options["evidence_policy"],
            variant_policy=options["variant_policy"],
            metric=options["metric"],
            availability_policy=options["availability_policy"],
            runtime_observations=runtime_observations,
            runtime_profile_id=options.get("runtime_profile_id"),
            include_preliminary=options.get("include_preliminary", False),
        )

    return query


def _repository_with_in_memory_v2_data(nodes, evaluation, runtime_observations):
    repository = HarvestNodeRepository(Path("unused-nodes"), Path("unused-ranking"))
    engine = HarvestEvaluationEngine(evaluation)
    repository._load_catalog = lambda: nodes
    repository._load_evaluation = lambda: (evaluation, engine)
    repository._load_runtime_observations = lambda *_args, **kwargs: SimpleNamespace(
        rows=runtime_observations,
        revision="d" * 64,
        runtime_profile_selected=kwargs.get("runtime_profile_id"),
        files_scanned=1,
        coverage={
            "runtimeProfilesAvailable": 1,
            "runtimeProfileSelected": kwargs.get("runtime_profile_id"),
            "publishableConfirmedRows": len(runtime_observations),
            "preliminaryRows": 0,
            "syntheticExcluded": 0,
            "profileMismatchExcluded": 0,
        },
    )
    return repository


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

    def test_v2_independent_metric_contracts_are_literal_and_detect_production_drift(self):
        expected = {
            "staticCompleteNodeTargetYield": {
                "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
                "unit": "target_resource_units/node",
                "runtime": False,
            },
            "staticYieldPerAttackCycleSecond": {
                "scoreBasis": (
                    "STATIC_TARGET_RESOURCE_UNITS_PER_ATTACK_CYCLE_SECOND"
                ),
                "unit": "target_resource_units/attack_cycle_second",
                "runtime": False,
            },
            "observedYieldPerNode": {
                "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
                "unit": "target_resource_units/node",
                "runtime": True,
            },
            "observedYieldPerSecond": {
                "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND",
                "unit": "target_resource_units/second",
                "runtime": True,
            },
        }

        self.assertEqual(V2_METRIC_CONTRACTS, expected)
        self.assertEqual(METRIC_CONTRACTS, expected)

    def test_v2_verifier_compares_all_four_forward_metrics_with_one_controlled_profile(self):
        nodes, evaluation = _v2_catalogs()
        runtime_observations = _controlled_runtime_observations()
        engine = HarvestEvaluationEngine(evaluation)

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=_engine_reference_query(
                engine, nodes, runtime_observations
            ),
            runtime_observations=runtime_observations,
            sample_size=2,
            seed="v2-four-metrics",
        )

        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["inputs"]["runtimeProfileId"], "profile-a")
        forward = summary["coverageByDirection"]["forward"]
        self.assertEqual(
            set(forward),
            {
                "staticCompleteNodeTargetYield",
                "staticYieldPerAttackCycleSecond",
                "observedYieldPerNode",
                "observedYieldPerSecond",
            },
        )
        self.assertTrue(all(row["status"] == "VERIFIED" for row in forward.values()))
        self.assertGreater(forward["observedYieldPerNode"]["rowsCompared"], 0)
        self.assertEqual(
            summary["coverageByDirection"]["reverse"]["status"],
            "SKIPPED_WITH_REASON",
        )

        for metric, contract in V2_METRIC_CONTRACTS.items():
            forward_result = independently_rank_target(
                nodes,
                evaluation,
                node_id="metal-node",
                node_resource_id="metal-entry",
                metric=metric,
                evidence_policy="includeConditional",
                runtime_observations=runtime_observations,
            )
            forward_rows = [
                *forward_result["confirmedItems"],
                *forward_result["conditionalItems"],
            ]
            self.assertTrue(forward_rows)
            self.assertTrue(
                all(
                    row["scoreBasis"] == contract["scoreBasis"]
                    for row in forward_rows
                )
            )
            reverse_result = independently_rank_specialties(
                nodes,
                evaluation,
                species_key="anky",
                metric=metric,
                evidence_policy="includeConditional",
                runtime_observations=runtime_observations,
            )
            reverse_rows = [
                *reverse_result["confirmedItems"],
                *reverse_result["conditionalItems"],
            ]
            self.assertTrue(reverse_rows)
            self.assertTrue(
                all(
                    row["scoreBasis"] == contract["scoreBasis"]
                    for row in reverse_rows
                )
            )
            if contract["runtime"]:
                self.assertEqual(
                    forward_result["queryPolicy"]["runtimeProfileId"],
                    "profile-a",
                )
                self.assertEqual(
                    reverse_result["queryPolicy"]["runtimeProfileId"],
                    "profile-a",
                )

        cycle = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            metric="staticYieldPerAttackCycleSecond",
            evidence_policy="includeConditional",
        )["confirmedItems"][0]
        observed = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            metric="observedYieldPerSecond",
            evidence_policy="includeConditional",
            runtime_observations=runtime_observations,
            runtime_profile_id="profile-a",
        )["confirmedItems"]
        self.assertAlmostEqual(
            cycle["staticYieldPerAttackCycleSecond"],
            cycle["staticCompleteNodeTargetYield"]
            / (
                cycle["estimatedHitsToDepleteNode"]
                * cycle["attackInterval"]
            ),
        )
        self.assertEqual(
            [row["speciesKey"] for row in observed], ["doed", "anky"]
        )

    def test_v2_verifier_detects_cycle_and_observed_metric_projection_drift(self):
        nodes, evaluation = _v2_catalogs()
        runtime_observations = _controlled_runtime_observations()
        engine = HarvestEvaluationEngine(evaluation)
        base_reference = _engine_reference_query(
            engine, nodes, runtime_observations
        )

        def drifting_reference(node_id, node_resource_id, limit, options):
            result = deepcopy(
                base_reference(node_id, node_resource_id, limit, options)
            )
            if (
                options["metric"] == "staticYieldPerAttackCycleSecond"
                and result["confirmedItems"]
            ):
                result["confirmedItems"][0]["scoreBreakdown"]["metric"] = (
                    "staticCompleteNodeTargetYield"
                )
            if (
                options["metric"] == "observedYieldPerNode"
                and result["confirmedItems"]
            ):
                result["confirmedItems"][0]["observedYieldPerNode"] += 1.0
            return result

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=drifting_reference,
            runtime_observations=runtime_observations,
            runtime_profile_id="profile-a",
            sample_size=2,
            seed="v2-metric-projection-drift",
        )

        self.assertEqual(summary["status"], "FAIL")
        fields = {row["field"] for row in summary["mismatches"]}
        self.assertIn("confirmedItems[0].scoreBreakdown.metric", fields)
        self.assertIn("confirmedItems[0].observedYieldPerNode", fields)

    def test_v2_independent_runtime_rows_require_profile_and_reject_preliminary_and_synthetic(self):
        nodes, evaluation = _v2_catalogs()
        preliminary = _controlled_runtime_observations(preliminary=True)

        excluded = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            metric="observedYieldPerNode",
            evidence_policy="includeConditional",
            runtime_observations=preliminary,
            runtime_profile_id="profile-a",
            include_preliminary=False,
        )
        included = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            metric="observedYieldPerNode",
            evidence_policy="includeConditional",
            runtime_observations=preliminary,
            runtime_profile_id="profile-a",
            include_preliminary=True,
        )
        synthetic = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            metric="observedYieldPerNode",
            evidence_policy="includeConditional",
            runtime_observations=_controlled_runtime_observations(synthetic=True),
            include_preliminary=True,
        )
        with self.assertRaisesRegex(
            ValueError,
            "Requested runtimeProfileId 'profile-b' is not available",
        ):
            independently_rank_target(
                nodes,
                evaluation,
                node_id="metal-node",
                node_resource_id="metal-entry",
                metric="observedYieldPerNode",
                evidence_policy="includeConditional",
                runtime_observations=_controlled_runtime_observations(),
                runtime_profile_id="profile-b",
            )

        self.assertEqual(excluded["confirmedItems"], [])
        self.assertEqual(excluded["conditionalItems"], [])
        self.assertEqual(included["confirmedItems"], [])
        self.assertEqual(
            {row["speciesKey"] for row in included["conditionalItems"]},
            {"anky", "doed"},
        )
        self.assertTrue(
            all(
                "OBSERVED_PRELIMINARY_MINIMUM_TRIALS_NOT_MET"
                in row["evidence"]["gaps"]
                for row in included["conditionalItems"]
            )
        )
        self.assertEqual(synthetic["confirmedItems"], [])
        self.assertEqual(synthetic["conditionalItems"], [])

    def test_v2_independent_runtime_profile_selection_and_full_prescope_audit(self):
        nodes, evaluation = _v2_catalogs()
        runtime_observations = _controlled_runtime_observations()
        second_profile = deepcopy(next(iter(runtime_observations.values())))
        second_profile["runtimeProfileId"] = "profile-b"
        second_profile["observationSetId"] = "runtime://profile-b/metal/unridden"
        runtime_observations[
            (
                "metal-node",
                "metal-entry",
                "unridden",
                "/game/dinos/unridden",
                0,
            )
        ] = second_profile

        with self.assertRaisesRegex(
            ValueError,
            "Multiple runtime profiles are available; select runtimeProfileId",
        ):
            independently_rank_target(
                nodes,
                evaluation,
                node_id="metal-node",
                node_resource_id="metal-entry",
                metric="observedYieldPerNode",
                runtime_observations=runtime_observations,
            )
        static = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
            metric="staticCompleteNodeTargetYield",
            runtime_observations=runtime_observations,
        )
        self.assertTrue(static["confirmedItems"])

        excluded = deepcopy(evaluation["creatures"][0])
        excluded["name"] = "Excluded"
        excluded["speciesKey"] = "excluded"
        excluded["objectPath"] = "/Game/Dinos/Excluded"
        excluded["tameability"] = {
            "status": "PREVENTED",
            "reasonCodes": ["CREATURE_NOT_TAMEABLE"],
        }
        evaluation["creatures"].append(excluded)
        audited = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
        )
        self.assertEqual(audited["coverage"]["canonicalCreatureAssetsAudited"], 4)
        self.assertEqual(audited["coverage"]["canonicalVariantsAudited"], 4)
        self.assertEqual(audited["coverage"]["speciesEvaluated"], 3)
        self.assertIn(
            "excluded",
            {row["speciesKey"] for row in audited["variantSelectionAudits"]},
        )

    def test_v2_reverse_verifier_uses_same_tier_metric_profile_and_global_rank_before_paging(self):
        nodes, evaluation = _v2_catalogs()
        runtime_observations = _controlled_runtime_observations()
        engine = HarvestEvaluationEngine(evaluation)
        repository = _repository_with_in_memory_v2_data(
            nodes, evaluation, runtime_observations
        )

        def specialties_reference(species_key, offset, limit, options):
            return repository.creature_specialties(
                species_key,
                offset=offset,
                limit=limit,
                evidence_policy=options["evidence_policy"],
                variant_policy=options["variant_policy"],
                metric=options["metric"],
                availability_policy=options["availability_policy"],
                runtime_profile_id=options.get("runtime_profile_id"),
                include_preliminary=options.get("include_preliminary", False),
            )

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=_engine_reference_query(
                engine, nodes, runtime_observations
            ),
            reference_specialties_query=specialties_reference,
            runtime_observations=runtime_observations,
            runtime_profile_id="profile-a",
            reverse_species=["anky"],
            reverse_page_size=1,
            sample_size=2,
            seed="v2-reverse",
        )
        full = independently_rank_specialties(
            nodes,
            evaluation,
            species_key="anky",
            metric="staticCompleteNodeTargetYield",
            evidence_policy="includeConditional",
            runtime_observations=runtime_observations,
            runtime_profile_id="profile-a",
            offset=0,
            limit=10,
        )
        second = independently_rank_specialties(
            nodes,
            evaluation,
            species_key="anky",
            metric="staticCompleteNodeTargetYield",
            evidence_policy="includeConditional",
            runtime_observations=runtime_observations,
            runtime_profile_id="profile-a",
            offset=1,
            limit=1,
        )

        self.assertEqual(summary["status"], "PASS")
        self.assertTrue(
            all(
                row["status"] == "VERIFIED"
                for row in summary["coverageByDirection"]["reverse"]["metrics"].values()
            )
        )
        self.assertEqual(second["items"][0]["rank"], full["items"][1]["rank"])
        self.assertEqual(
            second["items"][0]["relativeBasisTier"],
            second["items"][0]["rankingTier"],
        )
        self.assertEqual(
            second["queryPolicy"]["runtimeProfileId"], "profile-a"
        )

    def test_v2_reverse_equal_scores_use_stable_resource_node_identity(self):
        nodes, evaluation = _v2_catalogs()
        template = deepcopy(nodes["nodes"][0])
        template["resources"]["items"] = [
            deepcopy(template["resources"]["items"][0])
        ]

        node_a = deepcopy(template)
        node_a.update(
            {
                "id": "node-a",
                "name": "Z display name",
                "objectPath": "/Game/Nodes/A.A",
            }
        )
        node_a["resources"]["items"][0].update(
            {
                "nodeResourceId": "resource-a",
                "displayName": "Z display resource",
            }
        )
        node_z = deepcopy(template)
        node_z.update(
            {
                "id": "node-z",
                "name": "A display name",
                "objectPath": "/Game/Nodes/Z.Z",
            }
        )
        node_z["resources"]["items"][0].update(
            {
                "nodeResourceId": "resource-z",
                "displayName": "A display resource",
            }
        )
        first_catalog = {**nodes, "nodes": [node_z, node_a]}

        renamed_a = deepcopy(node_a)
        renamed_a["name"] = "A renamed display name"
        renamed_a["resources"]["items"][0][
            "displayName"
        ] = "A renamed display resource"
        renamed_z = deepcopy(node_z)
        renamed_z["name"] = "Z renamed display name"
        renamed_z["resources"]["items"][0][
            "displayName"
        ] = "Z renamed display resource"
        reversed_catalog = {**nodes, "nodes": [renamed_a, renamed_z]}

        def identities(result):
            return [
                (
                    row["resource"]["nodeResourceId"],
                    row["resource"]["resource"],
                    row["resource"]["entryIndex"],
                    row["node"]["id"],
                    row["node"]["objectPath"],
                    row["rank"],
                )
                for row in result["confirmedItems"]
            ]

        expected = [
            (
                "resource-a",
                "PrimalItemResource_Metal_C",
                0,
                "node-a",
                "/Game/Nodes/A.A",
                1,
            ),
            (
                "resource-z",
                "PrimalItemResource_Metal_C",
                0,
                "node-z",
                "/Game/Nodes/Z.Z",
                1,
            ),
        ]
        production_results = []
        independent_results = []
        production_pages = []
        independent_pages = []
        for catalog in (first_catalog, reversed_catalog):
            repository = _repository_with_in_memory_v2_data(
                catalog, evaluation, {}
            )
            production_results.append(
                repository.creature_specialties("anky", limit=10)
            )
            production_pages.append(
                [
                    repository.creature_specialties(
                        "anky", offset=page_offset, limit=1
                    )
                    for page_offset in (0, 1)
                ]
            )
            independent_results.append(
                independently_rank_specialties(
                    catalog,
                    evaluation,
                    species_key="anky",
                    limit=10,
                )
            )
            independent_pages.append(
                [
                    independently_rank_specialties(
                        catalog,
                        evaluation,
                        species_key="anky",
                        offset=page_offset,
                        limit=1,
                    )
                    for page_offset in (0, 1)
                ]
            )

        self.assertEqual(
            [identities(result) for result in production_results],
            [expected, expected],
        )
        self.assertEqual(
            [identities(result) for result in independent_results],
            [expected, expected],
        )
        expected_pages = [[expected[:1], expected[1:]], [expected[:1], expected[1:]]]
        self.assertEqual(
            [
                [identities(page) for page in catalog_pages]
                for catalog_pages in production_pages
            ],
            expected_pages,
        )
        self.assertEqual(
            [
                [identities(page) for page in catalog_pages]
                for catalog_pages in independent_pages
            ],
            expected_pages,
        )
        for result in [*production_results, *independent_results]:
            sort_metric = result["methodology"]["sortMetric"]
            self.assertNotIn("displayName", sort_metric)
            self.assertNotIn("nodeName", sort_metric)

        repository = _repository_with_in_memory_v2_data(
            first_catalog, evaluation, {}
        )

        def display_sorted_reference(species_key, offset, limit, options):
            full = repository.creature_specialties(
                species_key,
                offset=0,
                limit=100,
                evidence_policy=options["evidence_policy"],
                variant_policy=options["variant_policy"],
                metric=options["metric"],
                availability_policy=options["availability_policy"],
                runtime_profile_id=options.get("runtime_profile_id"),
                include_preliminary=options.get("include_preliminary", False),
            )

            def display_key(row):
                return (
                    str(row["resource"].get("displayName") or "").casefold(),
                    str(row["node"].get("name") or "").casefold(),
                )

            visible = [
                *sorted(full["confirmedItems"], key=display_key),
                *sorted(full["conditionalItems"], key=display_key),
            ]
            page_rows = visible[offset : offset + limit]
            actual = deepcopy(full)
            actual["confirmedItems"] = [
                row
                for row in page_rows
                if row.get("rankingTier") == "CONFIRMED"
            ]
            actual["conditionalItems"] = [
                row
                for row in page_rows
                if row.get("rankingTier") != "CONFIRMED"
            ]
            actual["items"] = deepcopy(actual["confirmedItems"])
            actual["offset"] = offset
            actual["limit"] = limit
            actual["nextOffset"] = (
                offset + len(page_rows)
                if offset + len(page_rows) < len(visible)
                else None
            )
            actual["page"] = {
                "offset": offset,
                "limit": limit,
                "total": len(visible),
                "returned": len(page_rows),
                "omitted": max(0, len(visible) - offset - len(page_rows)),
            }
            return actual

        display_drift = verify_catalogs(
            first_catalog,
            evaluation,
            reference_query=_engine_reference_query(
                HarvestEvaluationEngine(evaluation), first_catalog
            ),
            reference_specialties_query=display_sorted_reference,
            reverse_species=["anky"],
            reverse_page_size=1,
            sample_size=1,
            seed="v2-reverse-display-drift",
        )
        self.assertEqual(display_drift["status"], "FAIL")
        self.assertTrue(
            any(
                row["field"].endswith("resource.nodeResourceId")
                for row in display_drift["mismatches"]
            )
        )

    def test_v2_reverse_verifier_detects_baseline_tier_order_and_page_rank_drift(self):
        nodes, evaluation = _v2_catalogs()
        runtime_observations = _controlled_runtime_observations()
        engine = HarvestEvaluationEngine(evaluation)
        repository = _repository_with_in_memory_v2_data(
            nodes, evaluation, runtime_observations
        )

        def drifting_specialties(species_key, offset, limit, options):
            result = deepcopy(
                repository.creature_specialties(
                    species_key,
                    offset=offset,
                    limit=limit,
                    evidence_policy=options["evidence_policy"],
                    variant_policy=options["variant_policy"],
                    metric=options["metric"],
                    availability_policy=options["availability_policy"],
                    runtime_profile_id=options.get("runtime_profile_id"),
                    include_preliminary=options.get(
                        "include_preliminary", False
                    ),
                )
            )
            if (
                options["metric"] == "staticCompleteNodeTargetYield"
                and result["confirmedItems"]
            ):
                row = result["confirmedItems"][0]
                row["nodeTop"]["rankingTier"] = "CONDITIONAL"
                if offset > 0:
                    row["rank"] = 1
                result["items"] = deepcopy(result["confirmedItems"])
            return result

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=_engine_reference_query(
                engine, nodes, runtime_observations
            ),
            reference_specialties_query=drifting_specialties,
            runtime_observations=runtime_observations,
            runtime_profile_id="profile-a",
            reverse_species=["anky"],
            reverse_page_size=1,
            sample_size=2,
            seed="v2-reverse-drift",
        )

        self.assertEqual(summary["status"], "FAIL")
        fields = {row["field"] for row in summary["mismatches"]}
        self.assertIn("confirmedItems[0].nodeTop.rankingTier", fields)
        self.assertIn("confirmedItems[0].rank", fields)

    def test_v2_verifier_checks_confirmed_canonical_contract_without_runtime_gold(self):
        nodes, evaluation = _v2_catalogs()
        engine = HarvestEvaluationEngine(evaluation)

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=_engine_reference_query(engine, nodes),
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
        self.assertEqual(
            summary["methodology"]["scoreBasis"],
            "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
        )
        self.assertEqual(
            summary["methodology"]["unit"], "target_resource_units/node"
        )
        self.assertIs(summary["methodology"]["runtime"], False)
        self.assertGreater(summary["comparison"]["expectedTopRows"], 0)

    def test_v2_verifier_detects_conditional_metric_and_variant_audit_drift(self):
        nodes, evaluation = _v2_catalogs()
        engine = HarvestEvaluationEngine(evaluation)

        base_reference = _engine_reference_query(engine, nodes)

        def drifting_reference(node_id, node_resource_id, limit, options):
            result = base_reference(node_id, node_resource_id, limit, options)
            result = deepcopy(result)
            result["conditionalItems"][0]["staticCompleteNodeTargetYield"] += 1.0
            result["conditionalItems"][0]["scoreBasis"] = "STALE_SCORE_BASIS"
            result["methodology"]["scoreBasis"] = "STALE_METRIC"
            result["variantSelectionAudits"][0]["selectionReasons"] = [
                "PATH_LENGTH_HEURISTIC"
            ]
            return result

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=drifting_reference,
            sample_size=2,
            seed="v2-drift",
        )

        self.assertEqual(summary["status"], "FAIL")
        fields = {row["field"] for row in summary["mismatches"]}
        self.assertIn(
            "conditionalItems[0].staticCompleteNodeTargetYield", fields
        )
        self.assertIn("conditionalItems[0].scoreBasis", fields)
        self.assertIn("methodology.scoreBasis", fields)
        self.assertIn("variantSelectionAudits", fields)

    def test_v2_verifier_fails_closed_and_audits_ambiguous_base_variants(self):
        nodes, evaluation = _v2_catalogs()
        second_base = deepcopy(evaluation["creatures"][0])
        second_base["name"] = "Ankylosaurus Copy"
        second_base["objectPath"] = "/Game/Dinos/AnkyCopy"
        evaluation["creatures"].append(second_base)
        engine = HarvestEvaluationEngine(evaluation)

        base_reference = _engine_reference_query(engine, nodes)

        def drifting_reference(node_id, node_resource_id, limit, options):
            result = base_reference(node_id, node_resource_id, limit, options)
            result = deepcopy(result)
            audit = next(
                row
                for row in result["variantSelectionAudits"]
                if row["speciesKey"] == "anky"
            )
            audit["ambiguous"] = False
            audit["canonicalObjectPath"] = "/Game/Dinos/Anky"
            return result

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=drifting_reference,
            sample_size=2,
            seed="v2-ambiguous",
        )

        self.assertEqual(summary["status"], "FAIL")
        self.assertIn(
            "variantSelectionAudits",
            {row["field"] for row in summary["mismatches"]},
        )

    def test_v2_verifier_matches_unique_ancestry_root_selection(self):
        nodes, evaluation = _v2_catalogs()
        root = evaluation["creatures"][0]
        root_path = root["objectPath"]
        root["parentChain"] = [
            root_path,
            "/Game/Core/Dino_Character_BP.Dino_Character_BP_C",
        ]
        child = deepcopy(root)
        child["name"] = "Aberrant Ankylosaurus"
        child["objectPath"] = "/Game/Dinos/Anky_Aberrant.Anky_Aberrant"
        child["parentChain"] = [
            child["objectPath"],
            "/Game/Dinos/Anky.Anky_C",
            "/Game/Core/Dino_Character_BP.Dino_Character_BP_C",
        ]
        evaluation["creatures"].append(child)
        engine = HarvestEvaluationEngine(evaluation)

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=_engine_reference_query(engine, nodes),
            sample_size=2,
            seed="v2-ancestry-root",
        )
        independent = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
        )
        audit = next(
            row
            for row in independent["variantSelectionAudits"]
            if row["speciesKey"] == "anky"
        )

        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(audit["canonicalObjectPath"], root_path)
        self.assertEqual(
            audit["selectionReasons"],
            ["UNIQUE_ANCESTRY_ROOT_BASE_VARIANT"],
        )
        self.assertEqual(audit["excludedVariantClasses"], ["UNKNOWN_VARIANT"])

    def test_v2_verifier_matches_two_independent_roots_fail_closed(self):
        nodes, evaluation = _v2_catalogs()
        first = evaluation["creatures"][0]
        first["parentChain"] = [
            first["objectPath"],
            "/Game/Core/Dino_Character_BP.Dino_Character_BP_C",
        ]
        second = deepcopy(first)
        second["name"] = "Independent Ankylosaurus"
        second["objectPath"] = "/Game/Dinos/IndependentAnky.IndependentAnky"
        second["parentChain"] = [
            second["objectPath"],
            "/Game/Core/Dino_Character_BP.Dino_Character_BP_C",
        ]
        evaluation["creatures"].append(second)
        engine = HarvestEvaluationEngine(evaluation)

        summary = verify_catalogs(
            nodes,
            evaluation,
            reference_query=_engine_reference_query(engine, nodes),
            sample_size=2,
            seed="v2-independent-roots",
        )
        independent = independently_rank_target(
            nodes,
            evaluation,
            node_id="metal-node",
            node_resource_id="metal-entry",
        )
        audit = next(
            row
            for row in independent["variantSelectionAudits"]
            if row["speciesKey"] == "anky"
        )

        self.assertEqual(summary["status"], "PASS")
        self.assertIsNone(audit["canonicalObjectPath"])
        self.assertIn(
            "MULTIPLE_ANCESTRY_ROOT_BASE_VARIANTS",
            audit["ambiguityReasons"],
        )
        self.assertNotIn(
            "anky",
            {row["speciesKey"] for row in independent["confirmedItems"]},
        )

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

    def test_cli_v2_nested_reference_results_verify_static_metrics_and_skip_observed(self):
        nodes, evaluation = _v2_catalogs()
        engine = HarvestEvaluationEngine(evaluation)
        reference_query = _engine_reference_query(engine, nodes)
        forward: dict[str, dict[str, object]] = {}
        reference: dict[str, object] = {"forward": forward}
        for metric in (
            "staticCompleteNodeTargetYield",
            "staticYieldPerAttackCycleSecond",
        ):
            metric_results: dict[str, object] = {}
            for node in nodes["nodes"]:
                for resource in node["resources"]["items"]:
                    key = f"{node['id']}::{resource['nodeResourceId']}"
                    metric_results[key] = reference_query(
                        node["id"],
                        resource["nodeResourceId"],
                        10,
                        {
                            "evidence_policy": "includeConditional",
                            "variant_policy": "CANONICAL_VARIANT",
                            "metric": metric,
                            "availability_policy": "GLOBAL_TRANSFER_ALLOWED",
                            "runtime_profile_id": None,
                            "include_preliminary": False,
                        },
                    )
            forward[metric] = metric_results

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node_path = root / "nodes.json"
            evaluation_path = root / "evaluation.json"
            reference_path = root / "reference.json"
            output_path = root / "verification.json"
            node_path.write_text(json.dumps(nodes), encoding="utf-8")
            evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
            reference_path.write_text(json.dumps(reference), encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                exit_code = main(
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

            summary = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["comparison"]["mismatchCount"], 0)
        self.assertFalse(
            any(row.get("field") == "queryError" for row in summary["mismatches"])
        )
        forward_coverage = summary["coverageByDirection"]["forward"]
        self.assertEqual(
            forward_coverage["staticCompleteNodeTargetYield"]["status"],
            "VERIFIED",
        )
        self.assertEqual(
            forward_coverage["staticYieldPerAttackCycleSecond"]["status"],
            "VERIFIED",
        )
        self.assertEqual(
            forward_coverage["observedYieldPerNode"]["status"],
            "SKIPPED_WITH_REASON",
        )
        self.assertEqual(
            forward_coverage["observedYieldPerSecond"]["status"],
            "SKIPPED_WITH_REASON",
        )

    def test_cli_reference_results_help_describes_legacy_and_v2_shapes(self):
        help_text = " ".join(build_parser().format_help().split())

        self.assertIn("Legacy/v1 flat", help_text)
        self.assertIn('{"forward": {"<metric>":', help_text)

    def test_cli_preserves_preliminary_only_profile_identity_when_rows_are_excluded(self):
        nodes, evaluation = _v2_catalogs()
        preliminary_rows = _controlled_runtime_observations(preliminary=True)
        runtime_index = SimpleNamespace(
            rows=preliminary_rows,
            runtime_profile_selected="profile-a",
        )
        captured_verify_kwargs = {}

        def fake_verify(*_args, **kwargs):
            captured_verify_kwargs.update(kwargs)
            return {
                "schema": "blueprint-to-code.harvest-ranking-verification/v2",
                "status": "PASS",
                "mismatches": [],
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node_path = root / "nodes.json"
            evaluation_path = root / "evaluation.json"
            reference_path = root / "reference.json"
            runtime_root = root / "runtime"
            output_path = root / "verification.json"
            runtime_root.mkdir()
            node_path.write_text(json.dumps(nodes), encoding="utf-8")
            evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
            reference_path.write_text("{}", encoding="utf-8")

            with (
                patch(
                    "verify_ark_harvest_rankings.load_harvest_runtime_observations",
                    return_value=runtime_index,
                ) as loader,
                patch(
                    "verify_ark_harvest_rankings.verify_catalogs",
                    side_effect=fake_verify,
                ),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = main(
                    [
                        "--node-catalog",
                        str(node_path),
                        "--evaluation-catalog",
                        str(evaluation_path),
                        "--reference-results",
                        str(reference_path),
                        "--runtime-observations",
                        str(runtime_root),
                        "--runtime-profile-id",
                        "profile-a",
                        "--output",
                        str(output_path),
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(loader.call_args.kwargs["runtime_profile_id"], "profile-a")
        self.assertIs(loader.call_args.kwargs["include_preliminary"], True)
        self.assertIs(
            captured_verify_kwargs["runtime_observations"], preliminary_rows
        )
        self.assertEqual(captured_verify_kwargs["runtime_profile_id"], "profile-a")
        self.assertIs(captured_verify_kwargs["include_preliminary"], False)

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
