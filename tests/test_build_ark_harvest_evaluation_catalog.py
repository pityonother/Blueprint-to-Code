import tempfile
import sys
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_ark_harvest_evaluation_catalog import (  # noqa: E402
    CREATURE_EXTRACTOR_VERSION,
    _attack_applicability,
    _open_creature_scan_cache,
    _rideability,
    build_creature_record,
    discover_creature_candidates,
    trace_primal_dino_ancestry,
)
from blueprint_translator.creature_asset_scan_cache import (  # noqa: E402
    CreatureAssetScanCache,
)
from blueprint_translator.harvest.build.constants import (  # noqa: E402
    _devkit_root_from_content_root,
)


class BuildArkHarvestEvaluationCatalogTests(unittest.TestCase):
    def test_default_devkit_root_handles_windows_paths_on_posix(self):
        drive = "C:"
        content_root = PurePosixPath(
            drive
            + r"\Program Files\Epic Games\ARKDevkit\Projects\ShooterGame\Content"
        )

        root = _devkit_root_from_content_root(content_root)

        self.assertEqual(
            str(root),
            str(Path(drive + r"\Program Files\Epic Games\ARKDevkit")),
        )

    def test_rideability_requires_an_explicit_allow_riding_fact(self):
        self.assertEqual(
            _rideability(
                [{"name": "bAllowRiding", "type": "BoolProperty", "value": True}]
            ),
            {"status": "ALLOWED", "reasonCodes": []},
        )
        self.assertEqual(
            _rideability(
                [{"name": "bAllowRiding", "type": "BoolProperty", "value": False}]
            ),
            {"status": "PREVENTED", "reasonCodes": ["RIDING_NOT_ALLOWED"]},
        )
        self.assertEqual(
            _rideability([]),
            {"status": "UNKNOWN", "reasonCodes": ["RIDEABILITY_NOT_RECOVERED"]},
        )

    def test_builder_rejects_the_previous_creature_projection_cache_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Dino_Character_BP.uasset"
            asset.write_bytes(b"asset")
            cache_path = root / "creature-cache.json"
            previous = CreatureAssetScanCache(cache_path)
            previous.get_or_extract(asset, lambda _path: {"projection": "old"})
            previous.flush()

            cache = _open_creature_scan_cache(
                SimpleNamespace(
                    no_scan_cache=False,
                    scan_cache=cache_path,
                    refresh_scan_cache=False,
                )
            )
            fact, hit = cache.get_or_extract(
                asset,
                lambda _path: {"projection": "new"},
            )

        self.assertEqual(CREATURE_EXTRACTOR_VERSION, "ark-creature-attack-catalog/v3")
        self.assertEqual(cache.load_status, "VERSION_MISMATCH_IGNORED")
        self.assertFalse(hit)
        self.assertEqual(fact, {"projection": "new"})

    def test_attack_applicability_excludes_wild_only_and_dynamic_damage(self):
        wild_only = _attack_applicability({"onlyOnWildDinos": True})
        dynamic_damage = _attack_applicability(
            {"useBlueprintAdjustOutputDamage": True}
        )

        self.assertEqual(wild_only["status"], "INELIGIBLE")
        self.assertEqual(wild_only["reasonCodes"], ["ATTACK_ONLY_ON_WILD_DINOS"])
        self.assertEqual(dynamic_damage["status"], "CONDITIONAL")
        self.assertEqual(
            dynamic_damage["reasonCodes"],
            ["BLUEPRINT_ADJUST_OUTPUT_DAMAGE_NOT_RECOVERED"],
        )

    def test_attack_applicability_accumulates_all_dynamic_blueprint_gaps(self):
        conditional = _attack_applicability(
            {
                "useBlueprintCanRiderAttack": True,
                "useBlueprintAdjustOutputDamage": True,
            }
        )
        explicitly_blocked = _attack_applicability(
            {
                "preventWithRider": True,
                "useBlueprintCanRiderAttack": True,
                "useBlueprintAdjustOutputDamage": True,
            }
        )

        self.assertEqual(conditional["status"], "CONDITIONAL")
        self.assertEqual(
            conditional["reasonCodes"],
            [
                "BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED",
                "BLUEPRINT_ADJUST_OUTPUT_DAMAGE_NOT_RECOVERED",
            ],
        )
        self.assertEqual(
            explicitly_blocked,
            {
                "scope": "TAMED_RIDDEN",
                "status": "INELIGIBLE",
                "reasonCodes": ["ATTACK_PREVENTED_WITH_RIDER"],
            },
        )

    def test_discovery_uses_wide_character_pattern_not_only_character_bp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = [
                root / "Raptor_Character_BP.uasset",
                root / "Raptor_Character_BP_Aberrant.uasset",
                root / "ChildOfCharacter_BP_Test.uasset",
                # Real DevKit counterexamples use these naming shapes and were
                # omitted by the old *Character_BP* discovery pattern.
                root / "EndBoss_Character.uasset",
                root / "CaveWolf_Character_Base_BP.uasset",
                root / "Pteroteuthis_Char_BP.uasset",
            ]
            ignored = root / "RaptorPawn.uasset"
            for path in [*expected, ignored]:
                path.touch()

            paths, backend = discover_creature_candidates(root, prefer_rg=False)

        self.assertEqual(paths, sorted(path.resolve() for path in expected))
        self.assertEqual(backend, "OS_WALK")

    def test_ancestry_follows_full_parent_path_outside_candidate_glob(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir)
            child = content_root / "Dinos" / "Child_Character_BP.uasset"
            parent = content_root / "Core" / "DinoParent.uasset"
            child.parent.mkdir(parents=True)
            parent.parent.mkdir(parents=True)
            child.touch()
            parent.touch()
            facts = {
                child.resolve(): {
                    "parent": "/Game/Core/DinoParent.DinoParent_C",
                    "properties": [],
                },
                parent.resolve(): {
                    "parent": "/Script/ShooterGame.PrimalDinoCharacter",
                    "properties": [],
                },
            }

            ancestry = trace_primal_dino_ancestry(
                child,
                content_root=content_root,
                load_asset=lambda path: facts[path.resolve()],
                class_index={},
            )

        self.assertEqual(ancestry["status"], "CONFIRMED")
        self.assertEqual(
            ancestry["objectPathChain"],
            [
                "/Game/Dinos/Child_Character_BP.Child_Character_BP",
                "/Game/Core/DinoParent.DinoParent_C",
                "/Script/ShooterGame.PrimalDinoCharacter",
            ],
        )
        self.assertEqual(
            ancestry["sourcePaths"],
            [str(child.resolve()), str(parent.resolve())],
        )

    def test_creature_record_uses_inherited_identity_attack_and_tameability(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir)
            child = content_root / "Dinos" / "Anky_Character_BP_Aberrant.uasset"
            parent = content_root / "Dinos" / "Anky_Character_BP.uasset"
            child.parent.mkdir(parents=True)
            child.touch()
            parent.touch()
            attack_infos = {
                "name": "AttackInfos",
                "type": "ArrayProperty",
                "array_parse": {
                    "parsed": True,
                    "count": 1,
                    "elements": [
                        {
                            "index": 0,
                            "properties": [
                                {"name": "AttackName", "type": "NameProperty", "value": "Tail"},
                                {
                                    "name": "MeleeDamageType",
                                    "type": "ObjectProperty",
                                    "object": "DmgType_MineStone_C",
                                    "object_path": (
                                        "/Game/Damage/DmgType_MineStone."
                                        "DmgType_MineStone_C"
                                    ),
                                },
                                {"name": "MeleeDamageAmount", "type": "IntProperty", "value": 50},
                                {"name": "AttackInterval", "type": "FloatProperty", "value": 0.67},
                                {"name": "RiderAttackInterval", "type": "FloatProperty", "value": 1.2},
                                {"name": "bPreventWithRider", "type": "BoolProperty", "value": False},
                                {
                                    "name": "bUseBlueprintCanRiderAttack",
                                    "type": "BoolProperty",
                                    "value": False,
                                },
                            ],
                        }
                    ],
                },
            }
            facts = {
                child.resolve(): {
                    "parent": "/Game/Dinos/Anky_Character_BP.Anky_Character_BP_C",
                    "properties": [
                        {
                            "name": "DescriptiveName",
                            "type": "StrProperty",
                            "value": "Aberrant Ankylosaurus",
                        }
                    ],
                },
                parent.resolve(): {
                    "parent": "/Script/ShooterGame.PrimalDinoCharacter",
                    "properties": [
                        {"name": "DinoNameTag", "type": "NameProperty", "value": "Anky"},
                        {"name": "bCanBeTamed", "type": "BoolProperty", "value": True},
                        {"name": "bAllowRiding", "type": "BoolProperty", "value": True},
                        attack_infos,
                    ],
                },
            }
            def loader(path):
                return facts[path.resolve()]

            ancestry = trace_primal_dino_ancestry(
                child,
                content_root=content_root,
                load_asset=loader,
                class_index={},
            )

            creature = build_creature_record(
                child,
                content_root=content_root,
                load_asset=loader,
                ancestry=ancestry,
            )

        self.assertEqual(creature["name"], "Aberrant Ankylosaurus")
        self.assertEqual(creature["speciesKey"], "anky")
        self.assertEqual(creature["tameability"]["status"], "ALLOWED")
        self.assertEqual(creature["rideability"]["status"], "ALLOWED")
        self.assertEqual(creature["attacks"][0]["attackName"], "Tail")
        self.assertEqual(creature["attacks"][0]["riderAttackInterval"], 1.2)
        self.assertEqual(creature["attackCatalogStatus"], "DECODED")

    def test_explicit_empty_attack_array_beats_parent_and_parser_ghost_struct(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir)
            child = content_root / "Dinos" / "Mega_Character_BP.uasset"
            parent = content_root / "Dinos" / "Base_Character_BP.uasset"
            child.parent.mkdir(parents=True)
            child.touch()
            parent.touch()
            facts = {
                child.resolve(): {
                    "parent": "/Game/Dinos/Base_Character_BP.Base_Character_BP_C",
                    "properties": [
                        {
                            "name": "AttackInfos",
                            "type": "ArrayProperty",
                            "declared_size": 0,
                            "value": [],
                            "array_parse": {"parsed": False, "raw_size": 0},
                        },
                        {
                            "name": "AttackInfos",
                            "type": "StructProperty",
                            "declared_size": 8,
                            "value": {"parsed": False},
                        },
                    ],
                },
                parent.resolve(): {
                    "parent": "/Script/ShooterGame.PrimalDinoCharacter",
                    "properties": [
                        {
                            "name": "DinoNameTag",
                            "type": "NameProperty",
                            "value": "Base",
                        },
                        {
                            "name": "AttackInfos",
                            "type": "ArrayProperty",
                            "array_parse": {
                                "parsed": True,
                                "elements": [{"index": 0, "properties": []}],
                            },
                        },
                    ],
                },
            }
            ancestry = {
                "status": "CONFIRMED",
                "sourcePaths": [str(child.resolve()), str(parent.resolve())],
                "objectPathChain": [],
            }

            creature = build_creature_record(
                child,
                content_root=content_root,
                load_asset=lambda path: facts[path.resolve()],
                ancestry=ancestry,
            )

        self.assertEqual(creature["attackCatalogStatus"], "CONFIRMED_EMPTY")
        self.assertEqual(creature["attacks"], [])

    def test_child_with_only_unrecovered_attack_tag_does_not_inherit_parent_attacks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir)
            child = content_root / "Dinos" / "Unknown_Character_BP.uasset"
            parent = content_root / "Dinos" / "Base_Character_BP.uasset"
            child.parent.mkdir(parents=True)
            child.touch()
            parent.touch()
            facts = {
                child.resolve(): {
                    "parent": "/Game/Dinos/Base_Character_BP.Base_Character_BP_C",
                    "properties": [
                        {
                            "name": "AttackInfos",
                            "type": "StructProperty",
                            "declared_size": 8,
                            "value": {"parsed": False},
                        }
                    ],
                },
                parent.resolve(): {
                    "parent": "/Script/ShooterGame.PrimalDinoCharacter",
                    "properties": [
                        {
                            "name": "AttackInfos",
                            "type": "ArrayProperty",
                            "array_parse": {
                                "parsed": True,
                                "elements": [{"index": 0, "properties": []}],
                            },
                        }
                    ],
                },
            }
            ancestry = {
                "status": "CONFIRMED",
                "sourcePaths": [str(child.resolve()), str(parent.resolve())],
                "objectPathChain": [],
            }

            creature = build_creature_record(
                child,
                content_root=content_root,
                load_asset=lambda path: facts[path.resolve()],
                ancestry=ancestry,
            )

        self.assertEqual(creature["attackCatalogStatus"], "NOT_RECOVERED")
        self.assertEqual(creature["attacks"], [])
        self.assertIn("ATTACK_INFOS_NOT_RECOVERED", creature["gaps"])


if __name__ == "__main__":
    unittest.main()
