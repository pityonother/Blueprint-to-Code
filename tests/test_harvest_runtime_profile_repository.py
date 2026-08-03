from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    EVALUATION_CATALOG_SCHEMA,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    METRIC_OBSERVED_PER_NODE,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    POLICY_INCLUDE_CONDITIONAL,
    RANKING_RESULT_SCHEMA,
    VARIANT_CANONICAL,
    HarvestEvaluationEngine,
)
from blueprint_translator.harvest_node_repository import (  # noqa: E402
    HarvestNodeRepository,
)
from blueprint_translator.harvest_ranking import YIELD_MODEL_VERSION  # noqa: E402
from blueprint_translator.harvest_runtime_observations import (  # noqa: E402
    HarvestRuntimeObservationIndex,
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
                            "nodeResourceId": "resource",
                            "resource": "PrimalItemResource_Test_C",
                            "displayName": "Test",
                        }
                    ]
                },
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
            "formulaVersion": YIELD_MODEL_VERSION,
            "policyVersion": HARVEST_RANKING_POLICY_VERSION,
            "usageScope": "TAMED_RIDDEN",
        },
        "coverage": {"claimsAllCreatures": True},
        "components": [{"objectPath": "/Game/Components/Test.Test"}],
        "damageTypeParents": {},
        "resourceDamageOverrides": [],
        "damageTypeGaps": {},
        "creatures": [
            {
                "name": name,
                "speciesKey": name.casefold(),
                "objectPath": (
                    f"/Game/PrimalEarth/Dinos/{name}/"
                    f"{name}_Character_BP.{name}_Character_BP"
                ),
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [{"attackIndex": 0, "attackName": "Harvest"}],
            }
            for name in ("Alpha", "Beta")
        ],
    }


def _shared_evaluation_key_node_catalog() -> dict[str, object]:
    catalog = _node_catalog()
    first = catalog["nodes"][0]
    second = deepcopy(first)
    first["id"] = "node-a"
    first["name"] = "Node A"
    first["objectPath"] = "/Game/Nodes/A.A"
    first["resources"]["items"][0]["nodeResourceId"] = "resource-a"
    second["id"] = "node-b"
    second["name"] = "Node B"
    second["objectPath"] = "/Game/Nodes/B.B"
    second["resources"]["items"][0]["nodeResourceId"] = "resource-b"
    catalog["nodes"] = [first, second]
    return catalog


def _index(profile: str | None) -> HarvestRuntimeObservationIndex:
    rows = {
        ("node", "resource", "alpha", "/game/dinos/alpha.alpha_c", 0): {
            "runtimeProfileId": profile,
        }
    }
    return HarvestRuntimeObservationIndex(
        rows=rows if profile else {},
        revision="runtime-revision",
        files_scanned=3,
        synthetic_excluded=1,
        runtime_profiles_available=("profile-a", "profile-b"),
        runtime_profile_selected=profile,
        publishable_confirmed_rows=1 if profile else 0,
        preliminary_rows=1 if profile else 0,
        profile_mismatch_excluded=1 if profile else 0,
    )


def _empty_result() -> dict[str, object]:
    return {
        "schema": RANKING_RESULT_SCHEMA,
        "identity": {},
        "dataset": {},
        "coverage": {},
        "confirmedItems": [],
        "conditionalItems": [],
        "items": [],
    }


class HarvestRuntimeProfileRepositoryTests(unittest.TestCase):
    def test_real_loader_keeps_profile_partitioned_ranking_caches_warm(self) -> None:
        engine = Mock()
        engine.rank_node_resource.side_effect = (
            lambda *_args, **_kwargs: _empty_result()
        )
        common = {
            "node_id": "node",
            "node_resource_id": "resource",
            "limit": 10,
            "evidence_policy": POLICY_CONFIRMED,
            "variant_policy": VARIANT_CANONICAL,
            "metric": METRIC_OBSERVED_PER_NODE,
            "availability_policy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
            "include_preliminary": False,
        }

        with TemporaryDirectory() as temp_dir:
            repository = HarvestNodeRepository(
                Path("unused"),
                Path("unused"),
                runtime_observation_root=Path(temp_dir),
            )

            def load_index(
                *_args: object, **kwargs: object
            ) -> HarvestRuntimeObservationIndex:
                profile = kwargs.get("runtime_profile_id")
                return _index(str(profile) if profile is not None else None)

            with patch(
                "blueprint_translator.harvest_node_repository."
                "load_harvest_runtime_observations",
                side_effect=load_index,
            ) as loader:
                for profile in ("profile-a", "profile-b", "profile-a"):
                    repository._lazy_rankings(  # noqa: SLF001
                        _node_catalog(),
                        _evaluation_catalog(),
                        engine,
                        runtime_profile_id=profile,
                        **common,
                    )

        self.assertEqual(loader.call_count, 2)
        self.assertEqual(engine.rank_node_resource.call_count, 2)
        self.assertEqual(len(repository._lazy_ranking_cache), 2)  # noqa: SLF001

    def test_runtime_observation_profile_cache_is_bounded_lru(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repository = HarvestNodeRepository(
                Path("unused"),
                Path("unused"),
                runtime_observation_root=Path(temp_dir),
            )

            def load_index(
                *_args: object, **kwargs: object
            ) -> HarvestRuntimeObservationIndex:
                profile = kwargs.get("runtime_profile_id")
                return _index(str(profile) if profile is not None else None)

            with patch(
                "blueprint_translator.harvest_node_repository."
                "load_harvest_runtime_observations",
                side_effect=load_index,
            ) as loader:
                for index in range(33):
                    repository._load_runtime_observations(  # noqa: SLF001
                        runtime_profile_id=f"profile-{index}"
                    )
                repository._load_runtime_observations(  # noqa: SLF001
                    runtime_profile_id="profile-32"
                )
                repository._load_runtime_observations(  # noqa: SLF001
                    runtime_profile_id="profile-0"
                )

        self.assertEqual(len(repository._runtime_observation_cache), 32)  # noqa: SLF001
        self.assertEqual(loader.call_count, 34)

    def test_runtime_file_change_invalidates_profile_partitioned_rankings(self) -> None:
        engine = Mock()
        engine.rank_node_resource.side_effect = (
            lambda *_args, **_kwargs: _empty_result()
        )
        common = {
            "node_id": "node",
            "node_resource_id": "resource",
            "limit": 10,
            "evidence_policy": POLICY_CONFIRMED,
            "variant_policy": VARIANT_CANONICAL,
            "metric": METRIC_OBSERVED_PER_NODE,
            "availability_policy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
            "runtime_profile_id": "profile-a",
            "include_preliminary": False,
        }

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            observation_path = root / "observation.json"
            observation_path.write_text("{}", encoding="utf-8")
            repository = HarvestNodeRepository(
                Path("unused"),
                Path("unused"),
                runtime_observation_root=root,
            )
            with patch(
                "blueprint_translator.harvest_node_repository."
                "load_harvest_runtime_observations",
                return_value=_index("profile-a"),
            ) as loader:
                repository._lazy_rankings(  # noqa: SLF001
                    _node_catalog(), _evaluation_catalog(), engine, **common
                )
                observation_path.write_text("{}\n", encoding="utf-8")
                repository._lazy_rankings(  # noqa: SLF001
                    _node_catalog(), _evaluation_catalog(), engine, **common
                )

        self.assertEqual(loader.call_count, 2)
        self.assertEqual(engine.rank_node_resource.call_count, 2)
        self.assertEqual(len(repository._lazy_ranking_cache), 1)  # noqa: SLF001

    def test_v2_tier_baseline_cache_is_complete_for_runtime_profile_and_policy(self) -> None:
        repository = HarvestNodeRepository(Path("unused"), Path("unused"))
        engine = Mock()
        engine.rank_node_resource.return_value = {
            "confirmedItems": [{"speciesKey": "alpha"}],
            "conditionalItems": [{"speciesKey": "beta"}],
        }
        common = {
            "evaluation_revision": "3" * 64,
            "node_id": "node",
            "node_resource_id": "resource",
            "evidence_policy": POLICY_INCLUDE_CONDITIONAL,
            "variant_policy": VARIANT_CANONICAL,
            "metric": METRIC_OBSERVED_PER_NODE,
            "availability_policy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        }

        first = repository._v2_tier_baselines(  # noqa: SLF001
            engine,
            _node_catalog(),
            runtime_index=_index("profile-a"),
            include_preliminary=False,
            **common,
        )
        repeated = repository._v2_tier_baselines(  # noqa: SLF001
            engine,
            _node_catalog(),
            runtime_index=_index("profile-a"),
            include_preliminary=False,
            **common,
        )
        repository._v2_tier_baselines(  # noqa: SLF001
            engine,
            _node_catalog(),
            runtime_index=_index("profile-b"),
            include_preliminary=False,
            **common,
        )
        repository._v2_tier_baselines(  # noqa: SLF001
            engine,
            _node_catalog(),
            runtime_index=_index("profile-a"),
            include_preliminary=True,
            **common,
        )

        self.assertEqual(first, repeated)
        self.assertIsNot(first, repeated)
        self.assertEqual(engine.rank_node_resource.call_count, 3)
        self.assertEqual(first["CONFIRMED"]["speciesKey"], "alpha")
        self.assertEqual(first["CONDITIONAL"]["speciesKey"], "beta")

    def test_forward_cache_and_overlay_are_isolated_by_runtime_profile(self) -> None:
        repository = HarvestNodeRepository(Path("unused"), Path("unused"))
        engine = Mock()
        engine.rank_node_resource.side_effect = (
            lambda *_args, **_kwargs: _empty_result()
        )

        def load_index(*_args: object, **kwargs: object) -> HarvestRuntimeObservationIndex:
            return _index(str(kwargs["runtime_profile_id"]))

        repository._load_runtime_observations = Mock(  # type: ignore[method-assign]  # noqa: SLF001
            side_effect=load_index
        )
        common = {
            "node_id": "node",
            "node_resource_id": "resource",
            "limit": 10,
            "evidence_policy": POLICY_CONFIRMED,
            "variant_policy": VARIANT_CANONICAL,
            "metric": METRIC_OBSERVED_PER_NODE,
            "availability_policy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
            "include_preliminary": True,
        }

        first = repository._lazy_rankings(  # noqa: SLF001
            _node_catalog(),
            _evaluation_catalog(),
            engine,
            runtime_profile_id="profile-a",
            **common,
        )
        repository._lazy_rankings(  # noqa: SLF001
            _node_catalog(),
            _evaluation_catalog(),
            engine,
            runtime_profile_id="profile-b",
            **common,
        )

        self.assertEqual(engine.rank_node_resource.call_count, 2)
        self.assertEqual(len(repository._lazy_ranking_cache), 2)  # noqa: SLF001
        first_call = engine.rank_node_resource.call_args_list[0].kwargs
        self.assertEqual(first_call["runtime_profile_id"], "profile-a")
        self.assertIs(first_call["include_preliminary"], True)
        self.assertEqual(
            first["runtimeCoverage"]["runtimeProfileSelected"], "profile-a"
        )
        self.assertEqual(first["runtimeCoverage"]["profileMismatchExcluded"], 1)
        loader_kwargs = repository._load_runtime_observations.call_args_list[0].kwargs  # type: ignore[attr-defined]  # noqa: SLF001
        self.assertIs(loader_kwargs["allow_unselected_profiles"], False)

    def test_forward_cache_isolated_by_exact_node_resource_identity(self) -> None:
        repository = HarvestNodeRepository(Path("unused"), Path("unused"))
        engine = Mock()
        runtime_index = _index("profile-a")
        repository._load_runtime_observations = Mock(  # type: ignore[method-assign]  # noqa: SLF001
            return_value=runtime_index
        )

        def rank_for_node(
            _catalog: dict[str, object],
            *,
            node_id: str,
            **_kwargs: object,
        ) -> dict[str, object]:
            value = 1.0 if node_id == "node-a" else 2.0
            result = _empty_result()
            result["coverage"] = {"rankedSpeciesConfirmed": 1}
            result["confirmedItems"] = [
                {
                    "speciesKey": "alpha",
                    "observedYieldPerNode": value,
                    "runtimeObservation": {
                        "observationSetId": f"runtime://{node_id}"
                    },
                }
            ]
            return result

        engine.rank_node_resource.side_effect = rank_for_node
        catalog = _shared_evaluation_key_node_catalog()
        common = {
            "node_catalog": catalog,
            "evaluation_catalog": _evaluation_catalog(),
            "engine": engine,
            "limit": 10,
            "evidence_policy": POLICY_CONFIRMED,
            "variant_policy": VARIANT_CANONICAL,
            "metric": METRIC_OBSERVED_PER_NODE,
            "availability_policy": AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
            "runtime_profile_id": "profile-a",
        }

        first = repository._lazy_rankings(  # noqa: SLF001
            node_id="node-a",
            node_resource_id="resource-a",
            **common,
        )
        second = repository._lazy_rankings(  # noqa: SLF001
            node_id="node-b",
            node_resource_id="resource-b",
            **common,
        )

        self.assertEqual(engine.rank_node_resource.call_count, 2)
        self.assertEqual(first["items"][0]["observedYieldPerNode"], 1.0)
        self.assertEqual(second["items"][0]["observedYieldPerNode"], 2.0)
        self.assertEqual(
            second["items"][0]["runtimeObservation"]["observationSetId"],
            "runtime://node-b",
        )

    def test_static_forward_discovers_profiles_without_selecting_or_overlaying(self) -> None:
        repository = HarvestNodeRepository(Path("unused"), Path("unused"))
        engine = Mock()
        engine.rank_node_resource.return_value = _empty_result()
        repository._load_runtime_observations = Mock(  # type: ignore[method-assign]  # noqa: SLF001
            return_value=_index(None)
        )

        result = repository._lazy_rankings(  # noqa: SLF001
            _node_catalog(),
            _evaluation_catalog(),
            engine,
            node_id="node",
            node_resource_id="resource",
            limit=10,
            evidence_policy=POLICY_CONFIRMED,
            variant_policy=VARIANT_CANONICAL,
            metric=METRIC_STATIC_TOTAL,
            availability_policy=AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        )

        loader_kwargs = repository._load_runtime_observations.call_args.kwargs  # type: ignore[attr-defined]  # noqa: SLF001
        self.assertIs(loader_kwargs["allow_unselected_profiles"], True)
        self.assertEqual(result["runtimeCoverage"]["runtimeProfilesAvailable"], [
            "profile-a",
            "profile-b",
        ])
        self.assertIsNone(result["runtimeCoverage"]["runtimeProfileSelected"])
        self.assertEqual(engine.rank_node_resource.call_args.kwargs["runtime_observations"], {})

    def test_preliminary_only_selected_profile_stays_valid_when_rows_are_filtered(self) -> None:
        repository = HarvestNodeRepository(
            Path("unused"),
            Path("unused"),
            evaluation_catalog_path=Path("unused-evaluation"),
        )
        node_catalog = _node_catalog()
        evaluation_catalog = _evaluation_catalog()
        engine = HarvestEvaluationEngine(evaluation_catalog)
        runtime_index = HarvestRuntimeObservationIndex(
            rows={},
            revision="preliminary-only-revision",
            files_scanned=1,
            synthetic_excluded=0,
            runtime_profiles_available=("profile-a",),
            runtime_profile_selected="profile-a",
            publishable_confirmed_rows=0,
            preliminary_rows=1,
            profile_mismatch_excluded=0,
        )

        with (
            patch.object(repository, "_load_catalog", return_value=node_catalog),
            patch.object(
                repository,
                "_load_evaluation",
                return_value=(evaluation_catalog, engine),
            ),
            patch.object(
                repository,
                "_load_runtime_observations",
                return_value=runtime_index,
            ),
        ):
            forward = repository.rankings(
                "node",
                "resource",
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_profile_id="profile-a",
            )
            reverse = repository.creature_specialties(
                "alpha",
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_profile_id="profile-a",
            )

        for result in (forward, reverse):
            self.assertEqual(result["queryPolicy"]["runtimeProfileId"], "profile-a")
            self.assertEqual(result["confirmedStatus"], "UNAVAILABLE")
            self.assertEqual(result["conditionalStatus"], "UNAVAILABLE")
            self.assertEqual(result["items"], [])
            self.assertEqual(result["runtimeCoverage"]["preliminaryRows"], 1)
            self.assertEqual(
                result["runtimeCoverage"]["runtimeProfilesAvailable"],
                ["profile-a"],
            )

    def test_reverse_selected_row_and_baseline_use_the_same_runtime_profile(self) -> None:
        repository = HarvestNodeRepository(Path("unused"), Path("unused"))
        node_catalog = _node_catalog()
        evaluation_catalog = _evaluation_catalog()
        main_engine = HarvestEvaluationEngine(evaluation_catalog)
        runtime_index = _index("profile-a")
        observed_maps: list[object] = []

        def fake_rank(
            engine: HarvestEvaluationEngine,
            _catalog: dict[str, object],
            **kwargs: object,
        ) -> dict[str, object]:
            observed_maps.append(kwargs["runtime_observations"])
            creatures = engine.catalog.get("creatures", [])
            selected_only = len(creatures) == 1
            row = {
                "speciesKey": "alpha" if selected_only else "beta",
                "creature": "Alpha" if selected_only else "Beta",
                "creatureObjectPath": "/Game/Dinos/Creature.Creature_C",
                "attackIndex": 0,
                "attackName": "Harvest",
                "rankingTier": "CONFIRMED",
                "observedYieldPerNode": 10.0 if selected_only else 20.0,
                "evidence": {"status": "CONFIRMED", "gaps": []},
            }
            return {"confirmedItems": [row], "conditionalItems": []}

        with (
            patch.object(repository, "_load_catalog", return_value=node_catalog),
            patch.object(
                repository,
                "_load_evaluation",
                return_value=(evaluation_catalog, main_engine),
            ),
            patch.object(
                repository,
                "_load_runtime_observations",
                return_value=runtime_index,
            ),
            patch.object(
                HarvestEvaluationEngine,
                "rank_node_resource",
                autospec=True,
                side_effect=fake_rank,
            ) as rank_mock,
        ):
            result = repository.creature_specialties(
                "alpha",
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_profile_id="profile-a",
            )
            repeated = repository.creature_specialties(
                "alpha",
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_profile_id="profile-a",
            )
            repository.creature_specialties(
                "alpha",
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_profile_id="profile-a",
                include_preliminary=True,
            )

        self.assertEqual(len(observed_maps), 4)
        self.assertEqual(rank_mock.call_count, 4)
        self.assertEqual(result, repeated)
        self.assertIs(observed_maps[0], runtime_index.rows)
        self.assertIs(observed_maps[1], runtime_index.rows)
        self.assertEqual(result["confirmedItems"][0]["relativeToNodeTopPercent"], 50.0)
        self.assertEqual(result["queryPolicy"]["runtimeProfileId"], "profile-a")
        self.assertEqual(
            result["methodology"]["scoreBasis"],
            "OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
        )
        self.assertEqual(result["items"], result["confirmedItems"])

    def test_reverse_observed_does_not_project_one_representative_across_nodes(self) -> None:
        repository = HarvestNodeRepository(Path("unused"), Path("unused"))
        node_catalog = _shared_evaluation_key_node_catalog()
        evaluation_catalog = _evaluation_catalog()
        main_engine = HarvestEvaluationEngine(evaluation_catalog)
        runtime_index = _index("profile-a")

        def fake_rank(
            engine: HarvestEvaluationEngine,
            _catalog: dict[str, object],
            **kwargs: object,
        ) -> dict[str, object]:
            node_id = str(kwargs["node_id"])
            selected_only = len(engine.catalog.get("creatures", [])) == 1
            selected_value = 10.0 if node_id == "node-a" else 30.0
            value = selected_value if selected_only else selected_value * 2.0
            row = {
                "speciesKey": "alpha" if selected_only else "beta",
                "creature": "Alpha" if selected_only else "Beta",
                "creatureObjectPath": "/Game/Dinos/Creature.Creature_C",
                "attackIndex": 0,
                "attackName": "Harvest",
                "rankingTier": "CONFIRMED",
                "observedYieldPerNode": value,
                "staticCompleteNodeTargetYield": value,
                "runtimeObservation": {
                    "observationSetId": f"runtime://{node_id}"
                },
                "evidence": {"status": "CONFIRMED", "gaps": []},
            }
            return {"confirmedItems": [row], "conditionalItems": []}

        with (
            patch.object(repository, "_load_catalog", return_value=node_catalog),
            patch.object(
                repository,
                "_load_evaluation",
                return_value=(evaluation_catalog, main_engine),
            ),
            patch.object(
                repository,
                "_load_runtime_observations",
                return_value=runtime_index,
            ),
            patch.object(
                HarvestEvaluationEngine,
                "rank_node_resource",
                autospec=True,
                side_effect=fake_rank,
            ) as rank_mock,
        ):
            observed = repository.creature_specialties(
                "alpha",
                metric=METRIC_OBSERVED_PER_NODE,
                runtime_profile_id="profile-a",
            )
            static = repository.creature_specialties(
                "alpha",
                metric=METRIC_STATIC_TOTAL,
            )

        self.assertEqual(
            [row["node"]["id"] for row in observed["confirmedItems"]],
            ["node-b", "node-a"],
        )
        self.assertEqual(
            [row["selectedMetricValue"] for row in observed["confirmedItems"]],
            [30.0, 10.0],
        )
        self.assertEqual(
            [
                row["runtimeObservation"]["observationSetId"]
                for row in observed["confirmedItems"]
            ],
            ["runtime://node-b", "runtime://node-a"],
        )
        self.assertEqual(observed["coverage"]["uniqueEvaluationPairs"], 2)
        self.assertEqual(observed["coverage"]["uniqueEvaluationPairsRanked"], 2)
        self.assertEqual(static["coverage"]["uniqueEvaluationPairs"], 1)
        self.assertEqual(static["coverage"]["uniqueEvaluationPairsRanked"], 1)
        self.assertEqual(rank_mock.call_count, 6)


if __name__ == "__main__":
    unittest.main()
