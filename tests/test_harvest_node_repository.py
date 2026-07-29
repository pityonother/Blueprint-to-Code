import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_node_repository import (  # noqa: E402
    HarvestDatasetInvalid,
    HarvestDatasetNotBuilt,
    HarvestNodeRepository,
    _best_discovered_scope_row,
    _eligible_attack_candidates,
)
from blueprint_translator.harvest_evaluation_catalog import (  # noqa: E402
    EVALUATION_CATALOG_SCHEMA,
    HarvestEvaluationEngine,
    RANKING_RESULT_SCHEMA,
)
from blueprint_translator.harvest_catalog_sqlite import (  # noqa: E402
    build_harvest_catalog_sqlite,
    convert_resource_node_catalog,
)
from blueprint_translator.harvest_ranking import evaluate_attack_resource  # noqa: E402


EVALUATION_REVISION = "e" * 64
COMPONENT_REVISION = "c" * 64


def _node(
    node_id: str,
    node_resource_id: str,
    *,
    component: str = "MetalHarvestComponent",
    resource: str = "PrimalItemResource_Metal_C",
    entry_index: int = 0,
) -> dict[str, object]:
    return {
        "id": node_id,
        "name": node_id,
        "objectPath": f"/Game/Nodes/{node_id}.{node_id}",
        "harvestComponent": {
            "packagePath": f"/Game/Components/{component}"
        },
        "resources": {
            "status": "CONFIRMED",
            "count": 1,
            "items": [
                {
                    "entryIndex": entry_index,
                    "resource": resource,
                    "nodeResourceId": node_resource_id,
                }
            ],
        },
    }


def _component(name: str = "MetalHarvestComponent") -> dict[str, object]:
    return {
        "component": name,
        "objectPath": f"/Game/Components/{name}.{name}",
        "maxHarvestHealth": 75.0,
        "harvestHealthGiveResourceInterval": 20.0,
        "clampResourceHarvestDamage": False,
        "isSingleUnitHarvest": False,
        "resourceEntries": [
            {
                "entryIndex": 0,
                "resource": "PrimalItemResource_Metal_C",
                "entryWeight": 1.0,
                "weightOverrides": {"DmgType_MineStone_C": 1.0},
                "overrideQuantityMin": 1.0,
                "overrideQuantityMax": 1.0,
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


def _evaluation_catalog(
    *,
    revision: str = EVALUATION_REVISION,
    component_revision: str = COMPONENT_REVISION,
    components: list[dict[str, object]] | None = None,
    creatures: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema": EVALUATION_CATALOG_SCHEMA,
        "dataset": {
            "revision": revision,
            "componentDatasetRevision": component_revision,
        },
        "methodology": {"usageScope": "TAMED_RIDDEN"},
        "coverage": {"claimsAllCreatures": False},
        "components": components or [_component()],
        "damageTypeParents": {},
        "resourceDamageOverrides": [],
        "damageTypeGaps": {},
        "creatures": creatures or [
            {
                "name": "Ankylosaurus",
                "speciesKey": "anky",
                "objectPath": "/Game/Dinos/Anky",
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Tail",
                        "damageType": "DmgType_MineStone_C",
                        "baseDamage": 50.0,
                        "attackInterval": 1.0,
                        "riderAttackInterval": 1.0,
                        "preventWithRider": False,
                        "useBlueprintCanRiderAttack": False,
                        "gaps": [],
                    }
                ],
            }
        ],
    }


def _node_catalog(
    nodes: list[dict[str, object]],
    *,
    evaluation_revision: str = EVALUATION_REVISION,
    component_revision: str = COMPONENT_REVISION,
) -> dict[str, object]:
    return {
        "schema": "ark-resource-node-catalog/v1",
        "dataset": {
            "evaluationDatasetRevision": evaluation_revision,
            "componentDatasetRevision": component_revision,
        },
        "nodes": nodes,
    }


class HarvestNodeRepositoryTests(unittest.TestCase):
    def test_optional_sqlite_catalog_serves_list_detail_and_ranking_without_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sqlite_path = root / "harvest_catalog.sqlite"
            evaluation_path = root / "evaluation.json"
            build_harvest_catalog_sqlite(
                _node_catalog([_node("node-a", "resource-a")]),
                sqlite_path,
            )
            evaluation_path.write_text(
                json.dumps(_evaluation_catalog()), encoding="utf-8"
            )
            repository = HarvestNodeRepository(
                root / "catalog-does-not-exist.json",
                root / "ranking-does-not-exist.json",
                evaluation_catalog_path=evaluation_path,
                sqlite_catalog_path=sqlite_path,
            )

            with patch.object(
                repository,
                "_load_catalog",
                side_effect=AssertionError("JSON catalog must stay unloaded"),
            ):
                page = repository.list_nodes(q="node-a")
                detail = repository.get_node("node-a")
                ranking = repository.rankings("node-a", "resource-a", limit=1)
                specialties = repository.creature_specialties("anky", limit=1)

        self.assertEqual(page["total"], 1)
        self.assertEqual(detail["id"], "node-a")
        self.assertEqual(ranking["items"][0]["speciesKey"], "anky")
        self.assertEqual(specialties["items"][0]["speciesKey"], "anky")
        self.assertIsNone(repository._catalog)

    def test_list_creatures_groups_variants_and_supports_bounded_search_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evaluation_path = root / "evaluation.json"
            creatures = [
                {
                    "name": "Ankylosaurus",
                    "speciesKey": "anky",
                    "dinoNameTag": "Anky",
                    "objectPath": "/Game/Dinos/Anky_A",
                    "tameability": {"status": "ALLOWED"},
                    "rideability": {"status": "ALLOWED"},
                    "attacks": [{"attackIndex": 0}],
                },
                {
                    "name": "Ankylosaurus Variant",
                    "speciesKey": "anky",
                    "dinoNameTag": "Anky",
                    "objectPath": "/Game/Dinos/Anky_B",
                    "tameability": {"status": "UNKNOWN"},
                    "rideability": {"status": "PREVENTED"},
                    "attacks": [{"attackIndex": 0}, {"attackIndex": 1}],
                },
                {
                    "name": "Doedicurus",
                    "speciesKey": "doed",
                    "dinoNameTag": "Doed",
                    "objectPath": "/Game/Dinos/Doed",
                    "tameability": {"status": "ALLOWED"},
                    "rideability": {"status": "ALLOWED"},
                    "attacks": [],
                },
            ]
            evaluation_path.write_text(
                json.dumps(_evaluation_catalog(creatures=creatures)),
                encoding="utf-8",
            )
            repository = HarvestNodeRepository(
                root / "catalog.json",
                root / "ranking.json",
                evaluation_catalog_path=evaluation_path,
            )

            page = repository.list_creatures(q="ANKY", offset=0, limit=1)

        self.assertEqual(page["schema"], "blueprint-to-code.harvest-creature-page/v1")
        self.assertEqual(page["total"], 1)
        self.assertIsNone(page["nextOffset"])
        self.assertEqual(page["items"][0]["speciesKey"], "anky")
        self.assertEqual(page["items"][0]["variantCount"], 2)
        self.assertEqual(page["items"][0]["attackCount"], 3)
        self.assertEqual(
            page["items"][0]["rideabilityStatuses"],
            ["ALLOWED", "PREVENTED"],
        )

    def test_explicit_missing_sqlite_catalog_has_stable_not_built_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = HarvestNodeRepository(
                root / "catalog.json",
                root / "ranking.json",
                sqlite_catalog_path=root / "missing.sqlite",
            )

            with self.assertRaises(HarvestDatasetNotBuilt) as context:
                repository.list_nodes()

        self.assertEqual(context.exception.code, "HARVEST_DATASET_NOT_BUILT")

    def test_sqlite_catalog_rejects_a_changed_canonical_json_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            sqlite_path = root / "harvest_catalog.sqlite"
            original = _node_catalog([_node("node-a", "resource-a")])
            catalog_path.write_text(json.dumps(original), encoding="utf-8")
            convert_resource_node_catalog(catalog_path, sqlite_path)
            changed = _node_catalog([_node("node-b", "resource-b")])
            catalog_path.write_text(json.dumps(changed), encoding="utf-8")
            repository = HarvestNodeRepository(
                catalog_path,
                root / "ranking.json",
                sqlite_catalog_path=sqlite_path,
            )

            with self.assertRaises(HarvestDatasetInvalid):
                repository.list_nodes()

    def test_missing_catalog_has_stable_not_built_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = HarvestNodeRepository(
                Path(temp_dir) / "missing-catalog.json",
                Path(temp_dir) / "missing-ranking.json",
            )

            with self.assertRaises(HarvestDatasetNotBuilt) as context:
                repository.list_nodes()

        self.assertEqual(context.exception.code, "HARVEST_DATASET_NOT_BUILT")

    def test_loads_once_and_serves_bounded_node_and_exact_ranking_queries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            ranking_path = root / "ranking.json"
            node_resource_id = "node-resource-metal"
            catalog_path.write_text(
                json.dumps(
                    {
                        "schema": "ark-resource-node-catalog/v1",
                        "dataset": {"revision": "revision-1"},
                        "coverage": {"rankingCreatures": 1},
                        "nodes": [
                            {
                                "id": "node-metal",
                                "name": "Metal Rock",
                                "objectPath": "/Game/Nodes/Metal.Metal",
                                "harvestComponent": {
                                    "packagePath": "/Game/Components/MetalHarvestComponent"
                                },
                                "resources": {
                                    "status": "CONFIRMED",
                                    "count": 1,
                                    "items": [
                                        {
                                            "entryIndex": 0,
                                            "resource": "PrimalItemResource_Metal_C",
                                            "nodeResourceId": node_resource_id,
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            ranking_path.write_text(
                json.dumps(
                    {
                        "schema": "ark-harvest-ranking/v1",
                        "coverage": {"creaturesLoaded": 1},
                        "bestRows": [
                            {
                                "creature": "Ankylosaurus",
                                "creatureObjectPath": "/Game/Dinos/Ankylo",
                                "componentObjectPath": (
                                    "/Game/Components/MetalHarvestComponent."
                                    "MetalHarvestComponent"
                                ),
                                "resource": "PrimalItemResource_Metal_C",
                                "rankingStatus": "RANKED",
                                "estimatedYieldPerNode": 91.2,
                                "engineComparisonIndex": 91.2,
                                "attackIndex": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            repository = HarvestNodeRepository(catalog_path, ranking_path)

            page = repository.list_nodes(q="metal", limit=24)
            detail = repository.get_node("node-metal")
            ranking = repository.rankings(
                "node-metal", node_resource_id, limit=10
            )

        self.assertEqual(page["total"], 1)
        self.assertEqual(detail["id"], "node-metal")
        self.assertEqual(ranking["items"][0]["creature"], "Ankylosaurus")
        self.assertFalse(ranking["claimsGlobalTop"])

    def test_ranking_rejects_catalog_from_a_different_report_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            ranking_path = root / "ranking.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "schema": "ark-resource-node-catalog/v1",
                        "dataset": {"rankingDatasetRevision": "a" * 64},
                        "nodes": [],
                    }
                ),
                encoding="utf-8",
            )
            ranking_path.write_text(
                json.dumps(
                    {
                        "schema": "ark-harvest-ranking/v1",
                        "datasetRevision": "b" * 64,
                        "bestRows": [],
                    }
                ),
                encoding="utf-8",
            )
            repository = HarvestNodeRepository(catalog_path, ranking_path)

            with self.assertRaises(HarvestDatasetInvalid):
                repository.rankings("node", "resource")

    def test_optional_evaluation_catalog_serves_lazy_rankings_without_legacy_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            evaluation_path = root / "evaluation.json"
            catalog_path.write_text(
                json.dumps(_node_catalog([_node("node-a", "resource-a")])),
                encoding="utf-8",
            )
            evaluation_path.write_text(
                json.dumps(_evaluation_catalog()), encoding="utf-8"
            )
            repository = HarvestNodeRepository(
                catalog_path,
                root / "missing-legacy-ranking.json",
                evaluation_catalog_path=evaluation_path,
            )

            result = repository.rankings("node-a", "resource-a", limit=10)

        self.assertEqual(result["schema"], RANKING_RESULT_SCHEMA)
        self.assertEqual(result["node"]["id"], "node-a")
        self.assertEqual(result["items"][0]["speciesKey"], "anky")
        self.assertEqual(result["dataset"]["evaluationRevision"], EVALUATION_REVISION)

    def test_lazy_result_preserves_competition_ranks_for_equal_yields(self):
        cached = {
            "items": [
                {
                    "speciesKey": "alpha",
                    "estimatedYieldPerNode": 20.0,
                    "rank": 1,
                },
                {
                    "speciesKey": "beta",
                    "estimatedYieldPerNode": 20.0,
                    "rank": 1,
                },
                {
                    "speciesKey": "gamma",
                    "estimatedYieldPerNode": 10.0,
                    "rank": 3,
                },
            ],
            "coverage": {"rankedForNodeResource": 3},
        }

        result = HarvestNodeRepository._bind_lazy_result(
            cached,
            node_catalog={"dataset": {}},
            node={"id": "node-a", "name": "Node A", "objectPath": "/Node/A"},
            resource={"nodeResourceId": "resource-a"},
            component_package="/Component/A",
            limit=3,
        )

        self.assertEqual([row["rank"] for row in result["items"]], [1, 1, 3])

    def test_evaluation_catalog_schema_and_revisions_fail_closed(self):
        invalid_payloads = {
            "wrong schema": {
                **_evaluation_catalog(),
                "schema": "ark-harvest-evaluation-catalog/v1",
            },
            "short revision": _evaluation_catalog(revision="abc"),
            "uppercase revision": _evaluation_catalog(revision="E" * 64),
            "short component revision": _evaluation_catalog(component_revision="abc"),
        }
        for label, evaluation in invalid_payloads.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                catalog_path = root / "catalog.json"
                evaluation_path = root / "evaluation.json"
                catalog_path.write_text(
                    json.dumps(_node_catalog([_node("node-a", "resource-a")])),
                    encoding="utf-8",
                )
                evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
                repository = HarvestNodeRepository(
                    catalog_path,
                    root / "missing-ranking.json",
                    evaluation_catalog_path=evaluation_path,
                )

                with self.assertRaises(HarvestDatasetInvalid):
                    repository.rankings("node-a", "resource-a")

    def test_evaluation_catalog_must_match_both_node_dataset_revisions(self):
        mismatches = {
            "missing evaluation revision": _node_catalog(
                [_node("node-a", "resource-a")], evaluation_revision=""
            ),
            "evaluation revision mismatch": _node_catalog(
                [_node("node-a", "resource-a")], evaluation_revision="f" * 64
            ),
            "component revision mismatch": _node_catalog(
                [_node("node-a", "resource-a")], component_revision="d" * 64
            ),
        }
        for label, node_catalog in mismatches.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                catalog_path = root / "catalog.json"
                evaluation_path = root / "evaluation.json"
                catalog_path.write_text(json.dumps(node_catalog), encoding="utf-8")
                evaluation_path.write_text(
                    json.dumps(_evaluation_catalog()), encoding="utf-8"
                )
                repository = HarvestNodeRepository(
                    catalog_path,
                    root / "missing-ranking.json",
                    evaluation_catalog_path=evaluation_path,
                )

                with self.assertRaises(HarvestDatasetInvalid):
                    repository.rankings("node-a", "resource-a")

    def test_lazy_lru_reuses_one_component_resource_result_across_nodes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            evaluation_path = root / "evaluation.json"
            catalog_path.write_text(
                json.dumps(
                    _node_catalog(
                        [
                            _node("node-a", "resource-a"),
                            _node("node-b", "resource-b"),
                        ]
                    )
                ),
                encoding="utf-8",
            )
            evaluation_path.write_text(
                json.dumps(_evaluation_catalog()), encoding="utf-8"
            )
            repository = HarvestNodeRepository(
                catalog_path,
                root / "missing-ranking.json",
                evaluation_catalog_path=evaluation_path,
            )

            with patch(
                "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
                wraps=evaluate_attack_resource,
            ) as evaluator:
                first = repository.rankings("node-a", "resource-a", limit=10)
                second = repository.rankings("node-b", "resource-b", limit=5)

        self.assertEqual(evaluator.call_count, 1)
        self.assertEqual(first["node"]["id"], "node-a")
        self.assertEqual(second["node"]["id"], "node-b")
        self.assertEqual(second["resource"]["nodeResourceId"], "resource-b")
        self.assertEqual(first["items"], second["items"])

    def test_lazy_lru_does_not_reuse_a_different_resource_entry_index(self):
        component = _component()
        component["resourceEntries"] = [
            {
                "entryIndex": 0,
                "resource": "PrimalItemResource_Metal_C",
                "entryWeight": 1.0,
                "weightOverrides": {},
                "overrideQuantityMin": 1.0,
                "overrideQuantityMax": 1.0,
                "overrideQuantityRandomPower": 1.0,
                "minQuantityOverrides": {},
                "maxQuantityOverrides": {},
                "gaps": [],
            },
            {
                "entryIndex": 1,
                "resource": "PrimalItemResource_Metal_C",
                "entryWeight": 0.5,
                "weightOverrides": {},
                "overrideQuantityMin": 1.0,
                "overrideQuantityMax": 1.0,
                "overrideQuantityRandomPower": 1.0,
                "minQuantityOverrides": {},
                "maxQuantityOverrides": {},
                "gaps": [],
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            evaluation_path = root / "evaluation.json"
            catalog_path.write_text(
                json.dumps(
                    _node_catalog(
                        [
                            _node("node-a", "resource-a", entry_index=0),
                            _node("node-b", "resource-b", entry_index=1),
                        ]
                    )
                ),
                encoding="utf-8",
            )
            evaluation_path.write_text(
                json.dumps(_evaluation_catalog(components=[component])),
                encoding="utf-8",
            )
            repository = HarvestNodeRepository(
                catalog_path,
                root / "missing-ranking.json",
                evaluation_catalog_path=evaluation_path,
            )

            with patch(
                "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
                wraps=evaluate_attack_resource,
            ) as evaluator:
                first = repository.rankings("node-a", "resource-a", limit=10)
                second = repository.rankings("node-b", "resource-b", limit=10)

        self.assertEqual(evaluator.call_count, 2)
        self.assertEqual(first["items"][0]["resourceEntryIndex"], 0)
        self.assertEqual(second["items"][0]["resourceEntryIndex"], 1)
        self.assertNotEqual(
            first["items"][0]["engineComparisonIndex"],
            second["items"][0]["engineComparisonIndex"],
        )

    def test_lazy_lru_capacity_is_256(self):
        component_count = 257
        nodes = [
            _node(
                f"node-{index}",
                f"resource-{index}",
                component=f"Component{index}",
            )
            for index in range(component_count)
        ]
        components = [_component(f"Component{index}") for index in range(component_count)]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            evaluation_path = root / "evaluation.json"
            catalog_path.write_text(
                json.dumps(_node_catalog(nodes)), encoding="utf-8"
            )
            evaluation_path.write_text(
                json.dumps(_evaluation_catalog(components=components)), encoding="utf-8"
            )
            repository = HarvestNodeRepository(
                catalog_path,
                root / "missing-ranking.json",
                evaluation_catalog_path=evaluation_path,
            )

            with patch(
                "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
                wraps=evaluate_attack_resource,
            ) as evaluator:
                for index in range(component_count):
                    repository.rankings(
                        f"node-{index}", f"resource-{index}", limit=1
                    )
                repository.rankings("node-0", "resource-0", limit=1)

        self.assertEqual(evaluator.call_count, component_count + 1)

    def test_creature_specialties_uses_best_species_variant_and_relative_node_top(self):
        metal_component = _component("MetalHarvestComponent")
        metal_component["damageEntries"] = [
            {
                "damageTypeParent": "DmgType_Anky_C",
                "damageMultiplier": 1.0,
                "harvestQuantityMultiplier": 1.0,
                "gaps": [],
            },
            {
                "damageTypeParent": "DmgType_Doed_C",
                "damageMultiplier": 2.0,
                "harvestQuantityMultiplier": 2.0,
                "gaps": [],
            },
        ]
        stone_component = _component("StoneHarvestComponent")
        stone_component["damageEntries"] = [
            {
                "damageTypeParent": "DmgType_Anky_C",
                "damageMultiplier": 2.0,
                "harvestQuantityMultiplier": 2.0,
                "gaps": [],
            },
            {
                "damageTypeParent": "DmgType_Doed_C",
                "damageMultiplier": 1.0,
                "harvestQuantityMultiplier": 1.0,
                "gaps": [],
            },
        ]
        stone_component["resourceEntries"][0]["resource"] = (
            "PrimalItemResource_Stone_C"
        )
        # Deliberately invert relative strength and absolute yield: Anky is the
        # Stone winner at 100%, but its Metal yield is still higher.  The
        # specialties rank must follow yield, not the relative percentage.
        stone_component["resourceEntries"][0]["overrideQuantityMin"] = 0.25
        stone_component["resourceEntries"][0]["overrideQuantityMax"] = 0.25
        creatures = [
            {
                "name": "Ankylosaurus",
                "speciesKey": "anky",
                "objectPath": "/Game/Dinos/AnkyBase",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Weak Tail",
                        "damageType": "DmgType_Anky_C",
                        "baseDamage": 20.0,
                        "attackInterval": 1.0,
                        "riderAttackInterval": 1.0,
                        "gaps": [],
                    }
                ],
            },
            {
                "name": "Aberrant Ankylosaurus",
                "speciesKey": "anky",
                "objectPath": "/Game/Dinos/AnkyAberrant",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 1,
                        "attackName": "Strong Tail",
                        "damageType": "DmgType_Anky_C",
                        "baseDamage": 50.0,
                        "attackInterval": 1.0,
                        "riderAttackInterval": 1.0,
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
                        "damageType": "DmgType_Doed_C",
                        "baseDamage": 50.0,
                        "attackInterval": 1.0,
                        "riderAttackInterval": 1.0,
                        "gaps": [],
                    }
                ],
            },
        ]
        metal_node = _node("node-metal", "resource-metal")
        stone_node = _node(
            "node-stone",
            "resource-stone",
            component="StoneHarvestComponent",
            resource="PrimalItemResource_Stone_C",
        )
        stone_clone = _node(
            "node-stone-clone",
            "resource-stone-clone",
            component="StoneHarvestComponent",
            resource="PrimalItemResource_Stone_C",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            evaluation_path = root / "evaluation.json"
            catalog_path.write_text(
                json.dumps(_node_catalog([metal_node, stone_node, stone_clone])),
                encoding="utf-8",
            )
            evaluation_path.write_text(
                json.dumps(
                    _evaluation_catalog(
                        components=[metal_component, stone_component],
                        creatures=creatures,
                    )
                ),
                encoding="utf-8",
            )
            repository = HarvestNodeRepository(
                catalog_path,
                root / "missing-ranking.json",
                evaluation_catalog_path=evaluation_path,
            )

            with patch(
                "blueprint_translator.harvest_evaluation_catalog.evaluate_attack_resource",
                wraps=evaluate_attack_resource,
            ) as evaluator:
                first_page = repository.creature_specialties(
                    "AnKy", offset=0, limit=2
                )
                first_evaluation_count = evaluator.call_count
                second_page = repository.creature_specialties(
                    "anky", offset=2, limit=2
                )
                second_evaluation_count = evaluator.call_count

        self.assertEqual(
            first_page["schema"],
            "blueprint-to-code.harvest-creature-specialties/v2",
        )
        self.assertEqual(first_page["species"]["speciesKey"], "anky")
        self.assertEqual(first_page["species"]["variantCount"], 2)
        self.assertEqual(first_page["page"]["total"], 3)
        self.assertEqual(first_page["page"]["returned"], 2)
        self.assertEqual(first_page["coverage"]["uniqueEvaluationPairs"], 2)
        self.assertEqual(first_page["coverage"]["nodeResourcePairsRanked"], 3)
        self.assertEqual(
            [row["node"]["id"] for row in first_page["items"]],
            ["node-metal", "node-stone"],
        )
        self.assertEqual(
            [row["rank"] for row in first_page["items"]],
            [1, 2],
        )
        self.assertEqual(first_page["items"][0]["estimatedYieldPerNode"], 10.0)
        self.assertEqual(
            first_page["items"][0]["engineComparisonIndex"],
            first_page["items"][0]["estimatedYieldPerNode"],
        )
        self.assertEqual(first_page["items"][0]["relativeToNodeTopPercent"], 50.0)
        self.assertNotIn("damageTypeChain", first_page["items"][0])
        self.assertIn("damageMultiplier", first_page["items"][0])
        self.assertEqual(
            first_page["items"][0]["creatureObjectPath"],
            "/Game/Dinos/AnkyAberrant",
        )
        self.assertEqual(first_page["items"][0]["nodeTopEstimatedYieldPerNode"], 20.0)
        self.assertEqual(first_page["items"][0]["nodeTopEngineComparisonIndex"], 20.0)
        self.assertEqual(first_page["items"][0]["nodeTop"]["speciesKey"], "doed")
        self.assertEqual(
            first_page["items"][0]["nodeTop"]["estimatedYieldPerNode"],
            first_page["items"][0]["nodeTop"]["engineComparisonIndex"],
        )
        self.assertEqual(second_page["items"][0]["node"]["id"], "node-stone-clone")
        self.assertEqual(second_page["items"][0]["rank"], 2)
        self.assertEqual(second_page["items"][0]["estimatedYieldPerNode"], 5.0)
        self.assertEqual(second_page["items"][0]["nodeTopEstimatedYieldPerNode"], 5.0)
        self.assertEqual(second_page["items"][0]["relativeToNodeTopPercent"], 100.0)
        self.assertEqual(second_page["items"][0]["nodeTop"]["speciesKey"], "anky")
        self.assertEqual(
            first_page["methodology"]["metric"], "estimatedYieldPerNode"
        )
        self.assertEqual(
            first_page["methodology"]["sortMetric"], "estimatedYieldPerNode"
        )
        self.assertGreater(first_evaluation_count, 0)
        self.assertEqual(second_evaluation_count, first_evaluation_count)

    def test_creature_specialties_rejects_unknown_species_without_fuzzy_guessing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            evaluation_path = root / "evaluation.json"
            catalog_path.write_text(
                json.dumps(_node_catalog([_node("node-a", "resource-a")])),
                encoding="utf-8",
            )
            evaluation_path.write_text(
                json.dumps(_evaluation_catalog()), encoding="utf-8"
            )
            repository = HarvestNodeRepository(
                catalog_path,
                root / "missing-ranking.json",
                evaluation_catalog_path=evaluation_path,
            )

            with self.assertRaises(KeyError) as context:
                repository.creature_specialties("ank")

        self.assertEqual(context.exception.args[0], "HARVEST_SPECIES_NOT_FOUND")

    def test_fast_node_top_matches_authoritative_engine_with_inheritance_and_override(self):
        component = _component("ComplexHarvestComponent")
        component["resourceEntries"] = [
            {
                "entryIndex": 0,
                "resource": "PrimalItemResource_Metal_C",
                "entryWeight": 0.25,
                "weightOverrides": {"DmgType_Override_C": 0.75},
                "damageTypeEntryValues": ["DmgType_Override_C"],
                "overrideQuantityMin": 1.0,
                "overrideQuantityMax": 1.0,
                "overrideQuantityRandomPower": 1.0,
                "minQuantityOverrides": {},
                "maxQuantityOverrides": {},
                "gaps": [],
            },
            {
                "entryIndex": 1,
                "resource": "PrimalItemResource_Stone_C",
                "entryWeight": 0.75,
                "weightOverrides": {"DmgType_Override_C": 0.25},
                "damageTypeEntryValues": ["DmgType_Override_C"],
                "overrideQuantityMin": 1.0,
                "overrideQuantityMax": 1.0,
                "overrideQuantityRandomPower": 1.0,
                "minQuantityOverrides": {},
                "maxQuantityOverrides": {},
                "gaps": [],
            },
        ]
        component["damageEntries"] = [
            {
                "damageTypeParent": "DmgType_Override_C",
                "damageMultiplier": 3.0,
                "harvestQuantityMultiplier": 2.0,
                "gaps": [],
            },
            {
                "damageTypeParent": "DmgType_Parent_C",
                "damageMultiplier": 2.0,
                "harvestQuantityMultiplier": 1.0,
                "gaps": [],
            },
        ]
        creatures = [
            {
                "name": "Override winner",
                "speciesKey": "winner",
                "objectPath": "/Game/Dinos/Winner",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Override hit",
                        "damageType": "DmgType_Child_C",
                        "baseDamage": 10.0,
                        "attackInterval": 1.0,
                        "riderAttackInterval": 1.0,
                        "gaps": [],
                    }
                ],
            },
            {
                "name": "Parent contender",
                "speciesKey": "contender",
                "objectPath": "/Game/Dinos/Contender",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Parent hit",
                        "damageType": "DmgType_Parent_C",
                        "baseDamage": 20.0,
                        "attackInterval": 1.0,
                        "riderAttackInterval": 1.0,
                        "gaps": [],
                    }
                ],
            },
        ]
        evaluation = _evaluation_catalog(
            components=[component],
            creatures=creatures,
        )
        evaluation["damageTypeParents"] = {
            "DmgType_Child_C": "DmgType_Parent_C"
        }
        evaluation["resourceDamageOverrides"] = [
            {
                "sourceDamageType": "DmgType_Child_C",
                "resource": "PrimalItemResource_Metal_C",
                "replacementDamageType": "DmgType_Override_C",
            }
        ]
        node_catalog = _node_catalog(
            [
                _node(
                    "node-complex",
                    "resource-metal",
                    component="ComplexHarvestComponent",
                )
            ]
        )
        engine = HarvestEvaluationEngine(evaluation)
        candidates, _variant_counts = _eligible_attack_candidates(evaluation)

        fast = _best_discovered_scope_row(
            engine,
            component_package="/Game/Components/ComplexHarvestComponent",
            resource="PrimalItemResource_Metal_C",
            resource_entry_index=0,
            candidates=candidates,
        )
        authoritative = engine.rank_node_resource(
            node_catalog,
            node_id="node-complex",
            node_resource_id="resource-metal",
            limit=1,
        )["items"][0]

        self.assertIsNotNone(fast)
        self.assertEqual(fast["speciesKey"], authoritative["speciesKey"])
        self.assertEqual(
            fast["estimatedYieldPerNode"],
            authoritative["estimatedYieldPerNode"],
        )
        self.assertEqual(
            fast["engineComparisonIndex"], fast["estimatedYieldPerNode"]
        )
        self.assertEqual(fast["rankingTier"], "CONFIRMED")
        self.assertEqual(fast["rankingTier"], authoritative["rankingTier"])
        self.assertEqual(fast["evidence"], authoritative["evidence"])

    def test_fast_node_top_does_not_reward_an_extreme_attack_interval(self):
        component = _component("CadenceNeutralComponent")
        creatures = [
            {
                "name": "Z Fast Cadence",
                "speciesKey": "fast",
                "objectPath": "/Game/Dinos/Fast",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Fast",
                        "damageType": "DmgType_MineStone_C",
                        "baseDamage": 30.0,
                        "attackInterval": 0.01,
                        "riderAttackInterval": 0.01,
                        "gaps": [],
                    }
                ],
            },
            {
                "name": "A Normal Cadence",
                "speciesKey": "normal",
                "objectPath": "/Game/Dinos/Normal",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Normal",
                        "damageType": "DmgType_MineStone_C",
                        "baseDamage": 30.0,
                        "attackInterval": 1.0,
                        "riderAttackInterval": 1.0,
                        "gaps": [],
                    }
                ],
            },
        ]
        evaluation = _evaluation_catalog(
            components=[component], creatures=creatures
        )
        engine = HarvestEvaluationEngine(evaluation)
        candidates, _variant_counts = _eligible_attack_candidates(evaluation)

        winner = _best_discovered_scope_row(
            engine,
            component_package="/Game/Components/CadenceNeutralComponent",
            resource="PrimalItemResource_Metal_C",
            resource_entry_index=0,
            candidates=candidates,
        )

        self.assertIsNotNone(winner)
        self.assertEqual(winner["speciesKey"], "normal")
        self.assertEqual(winner["estimatedYieldPerNode"], 11.0)
        self.assertEqual(
            winner["engineComparisonIndex"], winner["estimatedYieldPerNode"]
        )

    def test_extinction_streetlight_top_prefers_doedicurus_over_dreadnoughtus(self):
        component = _component("CityPropHarvestComponent_Light_Large_Off")
        component.update(
            {
                "maxHarvestHealth": 150.0,
                "harvestHealthGiveResourceInterval": 40.0,
                "clampResourceHarvestDamage": False,
                "resourceEntries": [
                    {
                        "entryIndex": 0,
                        "resource": "PrimalItemResource_Electronics_C",
                        "entryWeight": 0.2,
                        "overrideQuantityMin": 0.0,
                        "overrideQuantityMax": 1.0,
                        "overrideQuantityRandomPower": 1.0,
                        "minQuantityOverrides": {},
                        "maxQuantityOverrides": {},
                        "weightOverrides": {},
                        "gaps": [],
                    },
                    {
                        "entryIndex": 1,
                        "resource": "PrimalItemResource_ScrapMetal_C",
                        "entryWeight": 1.485,
                        "overrideQuantityMin": 1.0,
                        "overrideQuantityMax": 1.0,
                        "overrideQuantityRandomPower": 1.0,
                        "minQuantityOverrides": {},
                        "maxQuantityOverrides": {},
                        "weightOverrides": {},
                        "gaps": [],
                    },
                ],
                "damageEntries": [
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
                ],
            }
        )
        creatures = [
            {
                "name": "Dreadnoughtus",
                "speciesKey": "dread",
                "objectPath": "/Game/Dinos/Dread",
                "tameability": {"status": "ALLOWED", "reasonCodes": []},
                "rideability": {"status": "ALLOWED", "reasonCodes": []},
                "attacks": [
                    {
                        "attackIndex": 0,
                        "attackName": "Bite",
                        "damageType": "DmgType_Dread_C",
                        "baseDamage": 1080.0,
                        "attackInterval": 0.5,
                        "riderAttackInterval": 0.5,
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
                        "damageType": "DmgType_Doed_C",
                        "baseDamage": 32.0,
                        "attackInterval": 0.67,
                        "riderAttackInterval": 0.67,
                        "gaps": [],
                    }
                ],
            },
        ]
        evaluation = _evaluation_catalog(
            components=[component], creatures=creatures
        )
        engine = HarvestEvaluationEngine(evaluation)
        candidates, _variant_counts = _eligible_attack_candidates(evaluation)
        rows = {
            candidate["speciesKey"]: evaluate_attack_resource(
                creature=candidate["creature"]["name"],
                creature_object_path=candidate["creature"]["objectPath"],
                attack=candidate["preparedAttack"],
                component=component,
                resource="PrimalItemResource_Electronics_C",
                resource_entry_index=0,
                damage_type_parents={},
                resource_damage_overrides={},
                damage_type_gaps={},
            )
            for candidate in candidates
        }

        winner = _best_discovered_scope_row(
            engine,
            component_package=(
                "/Game/Components/CityPropHarvestComponent_Light_Large_Off"
            ),
            resource="PrimalItemResource_Electronics_C",
            resource_entry_index=0,
            candidates=candidates,
        )

        self.assertIsNotNone(winner)
        self.assertEqual(winner["speciesKey"], "doed")
        self.assertGreater(
            rows["doed"]["estimatedYieldPerNode"],
            rows["dread"]["estimatedYieldPerNode"],
        )
        self.assertAlmostEqual(
            winner["estimatedYieldPerNode"], 56 * (0.2 / 1.685) * 0.5
        )

    def test_fast_node_top_fails_closed_for_unsupported_native_branches(self):
        base_component = _component("UnsupportedComponent")
        evaluation = _evaluation_catalog(components=[base_component])
        candidates, _variant_counts = _eligible_attack_candidates(evaluation)

        cases = {}
        single_unit = copy.deepcopy(base_component)
        single_unit["isSingleUnitHarvest"] = True
        cases["single unit"] = single_unit
        nonzero_effectiveness = copy.deepcopy(base_component)
        nonzero_effectiveness["damageEntries"][0][
            "damageHarvestAdditionalEffectiveness"
        ] = 0.5
        cases["nonzero effectiveness"] = nonzero_effectiveness
        nonlinear_random = copy.deepcopy(base_component)
        nonlinear_random["resourceEntries"][0][
            "overrideQuantityRandomPower"
        ] = 2.0
        cases["nonlinear random power"] = nonlinear_random

        for label, component in cases.items():
            with self.subTest(label=label):
                case_evaluation = _evaluation_catalog(components=[component])
                engine = HarvestEvaluationEngine(case_evaluation)
                self.assertIsNone(
                    _best_discovered_scope_row(
                        engine,
                        component_package="/Game/Components/UnsupportedComponent",
                        resource="PrimalItemResource_Metal_C",
                        resource_entry_index=0,
                        candidates=candidates,
                    )
                )


if __name__ == "__main__":
    unittest.main()
