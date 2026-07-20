import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.harvest_ranking import (  # noqa: E402
    evaluate_attack_resource,
    extract_creature_attacks,
    extract_harvest_component,
    extract_resource_damage_overrides,
    rank_harvest_rows,
)
from rank_ark_harvest import (  # noqa: E402
    best_rows,
    build_damage_context,
    build_resource_candidates,
    compact_row,
    discover_components,
    scan_manifest_hash,
    summarize_component_gaps,
)


class HarvestRankingTests(unittest.TestCase):
    def test_damage_context_indexes_non_dmgtype_blueprint_and_stops_at_native_parent(self):
        class FakeReader:
            def __init__(self):
                self.index_keys = set()

            def generated_class_parent(self, path):
                return "ShooterDamageType"

            def effective_defaults(self, path, class_index):
                self.index_keys = set(class_index)
                return [], [path]

        creatures = [
            {
                "attacks": [
                    {
                        "damageType": "ShooterDamageTypeBP_Base_C",
                    }
                ]
            }
        ]
        reader = FakeReader()

        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir)
            damage_root = (
                content_root / "PrimalEarth" / "CoreBlueprints" / "DamageTypes"
            )
            damage_root.mkdir(parents=True)
            base_path = damage_root / "ShooterDamageTypeBP_Base.uasset"
            base_path.touch()

            parent_map, _overrides, facts, _used_paths, gaps = build_damage_context(
                creatures=creatures,
                resources=["PrimalItemResource_Metal_C"],
                content_root=content_root,
                reader=reader,
            )

        self.assertIn("ShooterDamageTypeBP_Base", reader.index_keys)
        self.assertIn("ShooterDamageTypeBP_Base_C", reader.index_keys)
        self.assertEqual(
            parent_map["ShooterDamageTypeBP_Base_C"],
            "ShooterDamageType",
        )
        self.assertEqual(gaps["ShooterDamageTypeBP_Base_C"], [])
        self.assertNotIn("ShooterDamageType", gaps)
        self.assertFalse(
            any(
                fact.get("damageType") == "ShooterDamageType"
                and "DAMAGE_TYPE_ASSET_NOT_FOUND" in (fact.get("gaps") or [])
                for fact in facts
            )
        )

    def test_extract_creature_attacks_keeps_struct_elements_and_resolved_damage_type(self):
        properties = [
            {
                "name": "AttackInfos",
                "type": "ArrayProperty",
                "array_parse": {
                    "parsed": True,
                    "count": 1,
                    "elements": [
                        {
                            "index": 0,
                            "raw_offsets": {"start": 100, "end": 300},
                            "properties": [
                                {"name": "AttackName", "type": "NameProperty", "value": "Bite"},
                                {
                                    "name": "MeleeDamageType",
                                    "type": "ObjectProperty",
                                    "value": -4,
                                    "object": "DmgType_MineStone_C",
                                },
                                {"name": "MeleeDamageAmount", "type": "IntProperty", "value": 120},
                                {"name": "AttackInterval", "type": "FloatProperty", "value": 0.5},
                                {"name": "MeleeSwingRadius", "type": "FloatProperty", "value": 450.0},
                            ],
                        }
                    ],
                },
            }
        ]

        attacks = extract_creature_attacks(properties)

        self.assertEqual(len(attacks), 1)
        self.assertEqual(attacks[0]["attackName"], "Bite")
        self.assertEqual(attacks[0]["damageType"], "DmgType_MineStone_C")
        self.assertEqual(attacks[0]["baseDamage"], 120)
        self.assertEqual(attacks[0]["attackInterval"], 0.5)
        self.assertEqual(attacks[0]["rawOffsets"], {"start": 100, "end": 300})
        self.assertEqual(attacks[0]["valueStatus"], "CONFIRMED")

    def test_magmasaur_metal_override_produces_bounded_engine_index(self):
        attack = {
            "attackIndex": 0,
            "attackName": "Bite",
            "damageType": "DmgType_ExtraHarvestAndMetal_C",
            "baseDamage": 120.0,
            "attackInterval": 0.5,
            "valueStatus": "CONFIRMED",
            "gaps": [],
        }
        component = self._metal_component()

        row = evaluate_attack_resource(
            creature="Magmasaur",
            creature_object_path="/Game/Genesis/Dinos/Cherufe/Cherufe_Character_BP",
            attack=attack,
            component=component,
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={
                "DmgType_ExtraHarvestAndMetal_C": "DmgType_ExtraHarvest_C",
                "DmgType_MineStoneNoBerries_C": "DmgType_MineStone_C",
            },
            resource_damage_overrides={
                ("DmgType_ExtraHarvestAndMetal_C", "PrimalItemResource_Metal_C"): "DmgType_MineStone_C"
            },
        )

        self.assertEqual(row["rankingStatus"], "RANKED")
        self.assertEqual(row["effectiveDamageType"], "DmgType_MineStone_C")
        self.assertEqual(row["damageTypeMatch"], "DmgType_MineStone_C")
        self.assertAlmostEqual(row["resourceWeight"], 0.63)
        self.assertAlmostEqual(row["resourceWeightShare"], 0.63 / 1.03)
        self.assertAlmostEqual(row["harvestPressurePerSecond"], 480.0)
        self.assertAlmostEqual(row["engineComparisonIndex"], 480.0 * (0.63 / 1.03))
        self.assertIsNone(row["observedYieldPerSecond"])
        self.assertEqual(row["scoreBasis"], "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD")

    def test_zero_resource_weight_is_incompatible_not_zero_score(self):
        attack = {
            "attackIndex": 0,
            "attackName": "Tail",
            "damageType": "DmgType_SuperMineStone_C",
            "baseDamage": 32.0,
            "attackInterval": 0.67,
            "valueStatus": "CONFIRMED",
            "gaps": [],
        }

        row = evaluate_attack_resource(
            creature="Doedicurus",
            creature_object_path="/Game/PrimalEarth/Dinos/Doedicurus/Doed_Character_BP",
            attack=attack,
            component=self._metal_component(),
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={},
            resource_damage_overrides={},
        )

        self.assertEqual(row["rankingStatus"], "INCOMPATIBLE")
        self.assertEqual(row["reasonCode"], "ZERO_RESOURCE_WEIGHT")
        self.assertEqual(row["resourceWeight"], 0.0)
        self.assertIsNone(row["engineComparisonIndex"])
        self.assertNotEqual(row["engineComparisonIndex"], 0.0)

    def test_missing_attack_interval_is_not_recovered(self):
        attack = {
            "attackIndex": 0,
            "attackName": "Bite",
            "damageType": "DmgType_MineStone_C",
            "baseDamage": 120.0,
            "attackInterval": None,
            "valueStatus": "NOT_RECOVERED",
            "gaps": ["AttackInterval"],
        }

        row = evaluate_attack_resource(
            creature="Unknown",
            creature_object_path="/Game/Unknown",
            attack=attack,
            component=self._metal_component(),
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={},
            resource_damage_overrides={},
        )

        self.assertEqual(row["rankingStatus"], "UNRANKED")
        self.assertEqual(row["reasonCode"], "REQUIRED_ATTACK_FACT_NOT_RECOVERED")
        self.assertIsNone(row["engineComparisonIndex"])
        self.assertIn("AttackInterval", row["missingFacts"])
        self.assertEqual(row["missingFactsByScope"]["attack"], ["AttackInterval"])

    def test_ranked_rows_sort_before_explicit_unranked_rows(self):
        rows = [
            {"creature": "Zed", "attackName": "B", "rankingStatus": "UNRANKED", "engineComparisonIndex": None},
            {"creature": "Anky", "attackName": "Tail", "rankingStatus": "RANKED", "engineComparisonIndex": 90.0},
            {"creature": "Magma", "attackName": "Bite", "rankingStatus": "RANKED", "engineComparisonIndex": 290.0},
        ]

        ranked = rank_harvest_rows(rows)

        self.assertEqual([row["creature"] for row in ranked], ["Magma", "Anky", "Zed"])

    def test_extract_harvest_component_zips_damage_type_overrides_by_index(self):
        def array_prop(name, value, *, objects=None, elements=None):
            return {
                "name": name,
                "type": "ArrayProperty",
                "value": value,
                "objects": objects or [],
                "array_parse": {
                    "parsed": True,
                    "count": len(value),
                    "elements": elements or [],
                },
            }

        resource_element = {
            "index": 0,
            "properties": [
                {"name": "ResourceItem", "type": "ObjectProperty", "value": -1, "object": "Metal_C"},
                {"name": "EntryWeight", "type": "FloatProperty", "value": 0.0},
                {"name": "OverrideQuantityMin", "type": "IntProperty", "value": 0},
                {"name": "OverrideQuantityMax", "type": "IntProperty", "value": 1},
                array_prop(
                    "DamageTypeEntryValuesOverrides",
                    [-2],
                    objects=["MineStone_C"],
                ),
                array_prop("DamageTypeEntryWeightOverrides", [0.63]),
                array_prop("DamageTypeEntryMinQuantityOverrides", [1.0]),
                array_prop("DamageTypeEntryMaxQuantityOverrides", [2.0]),
            ],
        }
        damage_element = {
            "index": 0,
            "properties": [
                {"name": "DamageTypeParent", "type": "ObjectProperty", "value": -2, "object": "MineStone_C"},
                {"name": "DamageMultiplier", "type": "FloatProperty", "value": 2.0},
                {"name": "HarvestQuantityMultiplier", "type": "FloatProperty", "value": 1.0},
            ],
        }
        properties = [
            array_prop("HarvestResourceEntries", [{}], elements=[resource_element]),
            array_prop("HarvestDamageTypeEntries", [{}], elements=[damage_element]),
            {"name": "MaxHarvestHealth", "type": "FloatProperty", "value": 620.0},
            {"name": "HarvestHealthGiveResourceInterval", "type": "FloatProperty", "value": 40.0},
        ]

        component = extract_harvest_component(
            properties,
            component="MetalHarvestComponent",
            object_path="/Game/Harvest/MetalHarvestComponent",
        )

        self.assertEqual(component["gaps"], [])
        self.assertEqual(component["resourceEntries"][0]["resource"], "Metal_C")
        self.assertEqual(component["resourceEntries"][0]["weightOverrides"], {"MineStone_C": 0.63})
        self.assertEqual(component["resourceEntries"][0]["minQuantityOverrides"], {"MineStone_C": 1.0})
        self.assertEqual(component["damageEntries"][0]["damageTypeParent"], "MineStone_C")
        self.assertEqual(component["maxHarvestHealth"], 620.0)

    def test_damage_override_extractor_rejects_misaligned_arrays(self):
        properties = [
            {
                "name": "OverrideDamageForResourceHarvestingItems",
                "type": "ArrayProperty",
                "value": [-1, -2],
                "objects": ["Metal_C", "Crystal_C"],
                "array_parse": {"parsed": True, "count": 2},
            },
            {
                "name": "OverrideDamageForResourceHarvestingDamageTypes",
                "type": "ArrayProperty",
                "value": [-3],
                "objects": ["MineStone_C"],
                "array_parse": {"parsed": True, "count": 1},
            },
        ]

        result = extract_resource_damage_overrides(properties, "ExtraHarvestAndMetal_C")

        self.assertEqual(result["overrides"], {})
        self.assertIn("RESOURCE_DAMAGE_OVERRIDE_LENGTH_MISMATCH", result["gaps"])

    def test_damage_type_gap_blocks_false_incompatible_but_farther_gap_after_match_does_not(self):
        attack = {
            "attackIndex": 4,
            "attackName": "Tail",
            "damageType": "DmgType_Child_C",
            "baseDamage": 20.0,
            "attackInterval": 1.0,
            "gaps": [],
        }
        component = self._metal_component()
        component["damageEntries"] = []

        unknown = evaluate_attack_resource(
            creature="Creature",
            creature_object_path="/Game/Creature",
            attack=attack,
            component=component,
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={"DmgType_Child_C": "ShooterDamageTypeBP_Base_C"},
            resource_damage_overrides={},
            damage_type_gaps={"ShooterDamageTypeBP_Base_C": ["DAMAGE_TYPE_ASSET_NOT_FOUND"]},
        )

        self.assertEqual(unknown["rankingStatus"], "UNRANKED")
        self.assertEqual(unknown["reasonCode"], "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED")
        self.assertEqual(
            unknown["missingFactsByScope"]["damageType"],
            ["DAMAGE_TYPE_ASSET_NOT_FOUND"],
        )

        component["damageEntries"] = [
            {
                "damageTypeParent": "DmgType_Child_C",
                "damageMultiplier": 2.0,
                "harvestQuantityMultiplier": 1.0,
                "gaps": [],
            }
        ]
        component["resourceEntries"][1]["entryWeight"] = 0.63
        recovered_by_nearer_match = evaluate_attack_resource(
            creature="Creature",
            creature_object_path="/Game/Creature",
            attack=attack,
            component=component,
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={"DmgType_Child_C": "ShooterDamageTypeBP_Base_C"},
            resource_damage_overrides={},
            damage_type_gaps={"ShooterDamageTypeBP_Base_C": ["DAMAGE_TYPE_ASSET_NOT_FOUND"]},
        )

        self.assertEqual(recovered_by_nearer_match["rankingStatus"], "RANKED")

        component["damageEntries"] = [
            {
                "damageTypeParent": "DmgType_Base_C",
                "damageMultiplier": 2.0,
                "harvestQuantityMultiplier": 1.0,
                "gaps": [],
            }
        ]
        broken_before_farther_match = evaluate_attack_resource(
            creature="Creature",
            creature_object_path="/Game/Creature",
            attack=attack,
            component=component,
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={
                "DmgType_Child_C": "DmgType_Intermediate_C",
                "DmgType_Intermediate_C": "DmgType_Base_C",
            },
            resource_damage_overrides={},
            damage_type_gaps={
                "DmgType_Intermediate_C": ["DAMAGE_TYPE_DECODE_FAILED"]
            },
        )

        self.assertEqual(broken_before_farther_match["rankingStatus"], "UNRANKED")
        self.assertIn("DAMAGE_TYPE_DECODE_FAILED", broken_before_farther_match["missingFacts"])

    def test_damage_override_gap_on_source_blocks_false_ranked_row(self):
        attack = {
            "attackIndex": 0,
            "attackName": "Bite",
            "damageType": "DmgType_MineStone_C",
            "baseDamage": 120.0,
            "attackInterval": 0.5,
            "gaps": [],
        }

        row = evaluate_attack_resource(
            creature="Creature",
            creature_object_path="/Game/Creature",
            attack=attack,
            component=self._metal_component(),
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={},
            resource_damage_overrides={},
            damage_type_gaps={
                "DmgType_MineStone_C": ["RESOURCE_DAMAGE_OVERRIDE_LENGTH_MISMATCH"]
            },
        )

        self.assertEqual(row["rankingStatus"], "UNRANKED")
        self.assertEqual(row["reasonCode"], "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED")

    def test_min_max_quantity_gaps_are_informational_for_current_formula(self):
        component = self._metal_component()
        component["gaps"] = [
            "DAMAGE_TYPE_MIN_QUANTITY_OVERRIDE_NOT_RECOVERED",
            "DAMAGE_TYPE_MAX_QUANTITY_OVERRIDE_NOT_RECOVERED",
        ]
        component["rankingGaps"] = []
        component["resourceEntries"][1]["gaps"] = list(component["gaps"])

        row = evaluate_attack_resource(
            creature="Magmasaur",
            creature_object_path="/Game/Magma",
            attack={
                "attackIndex": 0,
                "attackName": "Bite",
                "damageType": "DmgType_MineStone_C",
                "baseDamage": 120.0,
                "attackInterval": 0.5,
                "gaps": [],
            },
            component=component,
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={},
            resource_damage_overrides={},
        )

        self.assertEqual(row["rankingStatus"], "RANKED")
        self.assertEqual(
            row["warnings"],
            [
                "DAMAGE_TYPE_MAX_QUANTITY_OVERRIDE_NOT_RECOVERED",
                "DAMAGE_TYPE_MIN_QUANTITY_OVERRIDE_NOT_RECOVERED",
            ],
        )

    def test_unknown_competing_resource_weight_blocks_normalized_share(self):
        component = self._metal_component()
        component["resourceEntries"][0]["entryWeight"] = None
        component["resourceEntries"][0]["weightOverrides"] = {}

        row = evaluate_attack_resource(
            creature="Magmasaur",
            creature_object_path="/Game/Magma",
            attack={
                "attackIndex": 0,
                "attackName": "Bite",
                "damageType": "DmgType_MineStone_C",
                "baseDamage": 120.0,
                "attackInterval": 0.5,
                "gaps": [],
            },
            component=component,
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={},
            resource_damage_overrides={},
        )

        self.assertEqual(row["rankingStatus"], "UNRANKED")
        self.assertEqual(row["reasonCode"], "RESOURCE_WEIGHT_NORMALIZATION_NOT_RECOVERED")
        self.assertIn("EntryWeight:PrimalItemResource_Stone_C", row["missingFacts"])
        self.assertEqual(
            row["missingFactsByScope"]["component"],
            ["EntryWeight:PrimalItemResource_Stone_C"],
        )

        component = self._metal_component()
        component["resourceEntries"][1]["entryWeight"] = None
        component["resourceEntries"][1]["weightOverrides"] = {}
        missing_target = evaluate_attack_resource(
            creature="Magmasaur",
            creature_object_path="/Game/Magma",
            attack={
                "attackIndex": 0,
                "attackName": "Bite",
                "damageType": "DmgType_MineStone_C",
                "baseDamage": 120.0,
                "attackInterval": 0.5,
                "gaps": [],
            },
            component=component,
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={},
            resource_damage_overrides={},
        )
        self.assertEqual(missing_target["reasonCode"], "RESOURCE_WEIGHT_NOT_RECOVERED")
        self.assertEqual(missing_target["missingFactsByScope"]["target"], ["EntryWeight"])

    def test_component_container_gap_is_not_mislabeled_as_attack_gap(self):
        component = self._metal_component()
        component["gaps"] = ["HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED"]
        component["rankingGaps"] = ["HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED"]

        row = evaluate_attack_resource(
            creature="Magmasaur",
            creature_object_path="/Game/Magma",
            attack={
                "attackIndex": 0,
                "attackName": "Bite",
                "damageType": "DmgType_MineStone_C",
                "baseDamage": 120.0,
                "attackInterval": 0.5,
                "gaps": [],
            },
            component=component,
            resource="PrimalItemResource_Metal_C",
            damage_type_parents={},
            resource_damage_overrides={},
        )

        self.assertEqual(row["rankingStatus"], "UNRANKED")
        self.assertEqual(row["reasonCode"], "REQUIRED_COMPONENT_FACT_NOT_RECOVERED")
        self.assertEqual(
            row["missingFactsByScope"]["component"],
            ["HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED"],
        )

    def test_best_rows_keeps_asset_identity_and_prefers_unknown_over_all_incompatible(self):
        rows = [
            {
                "resource": "Metal_C",
                "component": "SameComponent",
                "componentObjectPath": "/Game/Nodes/A",
                "creature": "SameLabel",
                "creatureObjectPath": "/Game/Creatures/A",
                "attackIndex": 0,
                "rankingStatus": "INCOMPATIBLE",
                "engineComparisonIndex": None,
            },
            {
                "resource": "Metal_C",
                "component": "SameComponent",
                "componentObjectPath": "/Game/Nodes/A",
                "creature": "SameLabel",
                "creatureObjectPath": "/Game/Creatures/A",
                "attackIndex": 1,
                "rankingStatus": "UNRANKED",
                "engineComparisonIndex": None,
            },
            {
                "resource": "Metal_C",
                "component": "SameComponent",
                "componentObjectPath": "/Game/Nodes/B",
                "creature": "SameLabel",
                "creatureObjectPath": "/Game/Creatures/B",
                "attackIndex": 0,
                "rankingStatus": "RANKED",
                "engineComparisonIndex": 5.0,
            },
        ]

        selected = best_rows(rows)

        self.assertEqual(len(selected), 2)
        by_creature_path = {row["creatureObjectPath"]: row for row in selected}
        self.assertEqual(by_creature_path["/Game/Creatures/A"]["rankingStatus"], "UNRANKED")
        self.assertEqual(by_creature_path["/Game/Creatures/A"]["attackIndex"], 1)
        self.assertEqual(by_creature_path["/Game/Creatures/B"]["rankingStatus"], "RANKED")

    def test_resource_candidates_preserve_each_requested_resource_and_explicit_empty_status(self):
        candidates = build_resource_candidates(
            ["Metal_C", "Wood_C", "Fiber_C"],
            [
                {
                    "resource": "Metal_C",
                    "componentObjectPath": "/Game/Metal",
                    "creatureObjectPath": "/Game/Magma",
                    "attackIndex": 0,
                    "rankingStatus": "RANKED",
                },
                {
                    "resource": "Wood_C",
                    "componentObjectPath": "/Game/Wood",
                    "creatureObjectPath": "/Game/Anky",
                    "attackIndex": 2,
                    "rankingStatus": "UNRANKED",
                },
            ],
        )

        self.assertEqual([row["resource"] for row in candidates], ["Metal_C", "Wood_C", "Fiber_C"])
        self.assertEqual(candidates[0]["discoveryStatus"], "RANKED_CANDIDATES_AVAILABLE")
        self.assertEqual(candidates[0]["rankedDiscoveryStatus"], "RANKED_ROWS_AVAILABLE")
        self.assertEqual(candidates[1]["discoveryStatus"], "ONLY_UNRANKED_CANDIDATES")
        self.assertEqual(candidates[1]["rankedDiscoveryStatus"], "NO_RANKED_ROW")
        self.assertEqual(candidates[2]["discoveryStatus"], "NO_ROWS")
        self.assertEqual(candidates[2]["rankedDiscoveryStatus"], "NO_RANKED_ROW")
        self.assertEqual(candidates[2]["bestRows"], [])
        self.assertEqual(candidates[0]["bestRows"][0]["componentObjectPath"], "/Game/Metal")
        self.assertEqual(candidates[0]["bestRows"][0]["creatureObjectPath"], "/Game/Magma")
        self.assertEqual(candidates[0]["bestRows"][0]["attackIndex"], 0)

    def test_component_gap_summary_is_bounded_and_manifest_hash_changes_with_state(self):
        manifest = [
            {
                "component": f"Component{index}",
                "componentObjectPath": f"/Game/Component{index}",
                "attempted": True,
                "decoded": True,
                "semanticGap": True,
                "matched": False,
                "gaps": ["HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED"],
            }
            for index in range(7)
        ]

        summary = summarize_component_gaps(manifest)

        self.assertEqual(summary[0]["count"], 7)
        self.assertEqual(len(summary[0]["examples"]), 5)
        self.assertEqual(scan_manifest_hash(manifest), scan_manifest_hash(manifest))
        changed = [dict(row) for row in manifest]
        changed[0] = {**changed[0], "matched": True}
        self.assertNotEqual(scan_manifest_hash(manifest), scan_manifest_hash(changed))

    def test_compact_row_keeps_path_and_attack_identity(self):
        row = compact_row(
            {
                "resource": "Metal_C",
                "component": "DisplayNode",
                "componentObjectPath": "/Game/Nodes/A",
                "creature": "DisplayCreature",
                "creatureObjectPath": "/Game/Creatures/A",
                "attackIndex": 7,
                "attackName": "SameName",
                "rankingStatus": "UNRANKED",
                "missingFacts": ["X"],
                "missingFactsByScope": {"target": ["X"]},
            }
        )

        self.assertEqual(
            (
                row["componentObjectPath"],
                row["creatureObjectPath"],
                row["attackIndex"],
            ),
            ("/Game/Nodes/A", "/Game/Creatures/A", 7),
        )

    def test_component_discovery_indexes_all_parents_before_filters_and_records_semantic_gaps(self):
        class FakeReader:
            def __init__(self):
                self.index_keys = set()

            def effective_defaults(self, path, class_index):
                self.index_keys = set(class_index)
                return [], [class_index["ParentComponent_C"], path]

        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir)
            harvest_root = (
                content_root / "PrimalEarth" / "CoreBlueprints" / "HarvestComponents"
            )
            harvest_root.mkdir(parents=True)
            (harvest_root / "ParentComponent.uasset").touch()
            (harvest_root / "SelectedChild.uasset").touch()
            reader = FakeReader()
            fake_fact = {
                "component": "SelectedChild",
                "objectPath": "/Game/SelectedChild.SelectedChild",
                "resourceEntries": [],
                "damageEntries": [],
                "gaps": ["HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED"],
                "rankingGaps": ["HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED"],
            }

            with patch("rank_ark_harvest.extract_harvest_component", return_value=fake_fact):
                components, catalog, failures, manifest = discover_components(
                    content_root=content_root,
                    reader=reader,
                    selected_names={"SelectedChild"},
                    max_components=1,
                    target_resources={"Metal_C"},
                )

        self.assertIn("ParentComponent_C", reader.index_keys)
        self.assertEqual(len(components), 1)
        self.assertEqual(catalog, {})
        self.assertEqual(failures[0]["reasonCode"], "COMPONENT_SEMANTIC_GAP")
        self.assertEqual(manifest[0]["attempted"], True)
        self.assertEqual(manifest[0]["decoded"], True)
        self.assertEqual(manifest[0]["semanticGap"], True)
        self.assertEqual(manifest[0]["matched"], False)
        self.assertEqual(manifest[0]["discoveryStatus"], "SEMANTIC_GAP")

    @staticmethod
    def _metal_component():
        return {
            "component": "MetalHarvestComponent",
            "objectPath": "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/MetalHarvestComponent",
            "maxHarvestHealth": 620.0,
            "harvestHealthGiveResourceInterval": 40.0,
            "resourceEntries": [
                {
                    "resource": "PrimalItemResource_Stone_C",
                    "entryWeight": 1.0,
                    "weightOverrides": {"DmgType_MineStone_C": 0.4},
                    "minQuantityOverrides": {"DmgType_MineStone_C": 1.0},
                    "maxQuantityOverrides": {"DmgType_MineStone_C": 1.0},
                },
                {
                    "resource": "PrimalItemResource_Metal_C",
                    "entryWeight": 0.0,
                    "weightOverrides": {"DmgType_MineStone_C": 0.63},
                    "minQuantityOverrides": {"DmgType_MineStone_C": 1.0},
                    "maxQuantityOverrides": {"DmgType_MineStone_C": 2.0},
                },
            ],
            "damageEntries": [
                {
                    "damageTypeParent": "DmgType_MineStone_C",
                    "damageMultiplier": 2.0,
                    "harvestQuantityMultiplier": 1.0,
                },
                {
                    "damageTypeParent": "DmgType_SuperMineStone_C",
                    "damageMultiplier": 3.0,
                    "harvestQuantityMultiplier": 7.0,
                },
            ],
            "gaps": [],
        }


if __name__ == "__main__":
    unittest.main()
