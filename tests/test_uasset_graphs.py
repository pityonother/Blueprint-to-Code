import json
import os
import sys
import tempfile
import unittest
import struct
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.uasset_graphs import (  # noqa: E402
    compare_clipboard_and_uasset_payloads,
    compare_uasset_with_clipboard,
    build_partial_graph_triage,
    build_quality_gate_payload,
    node_info_from_export,
    mine_graph_candidates,
    normalize_blueprint_object_path,
    object_ref_path,
    object_path_to_uasset_path,
    parse_custom_pins,
    read_uasset_class_defaults,
    parse_export_properties,
    resolve_graph_link_target_pins,
    render_candidate_text,
    render_uasset_class_defaults_report,
    render_pin_link_report,
    synthesize_boundary_pins_from_incoming_links,
    is_complete_empty_graph,
)
from blueprint_translator import uasset_graphs as uasset_graphs_module  # noqa: E402


class UAssetGraphCandidateTests(unittest.TestCase):
    def test_object_property_reference_preserves_full_import_object_path(self):
        imports = [
            {
                "object_name": "/Game/DLC/DamageTypes/DmgType_Shared",
                "class_name": "Package",
                "outer_index": 0,
            },
            {
                "object_name": "DmgType_Shared_C",
                "class_name": "BlueprintGeneratedClass",
                "outer_index": -1,
            },
        ]

        self.assertEqual(
            object_ref_path(-2, imports, []),
            "/Game/DLC/DamageTypes/DmgType_Shared.DmgType_Shared_C",
        )

    def test_object_path_maps_to_devkit_uasset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            asset = content_root / "Genesis2" / "Dinos" / "LionfishLion" / "LionfishLion_Character_BP.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"EventGraph\x00")

            found, attempted = object_path_to_uasset_path(
                "/Game/Genesis2/Dinos/LionfishLion/LionfishLion_Character_BP.LionfishLion_Character_BP",
                extra_roots=[content_root],
            )

        self.assertEqual(found, asset)
        self.assertIn(str(asset), attempted)

    def test_epic_manifest_discovers_custom_akd_devkit_install(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            program_data = temp_root / "ProgramData"
            manifest_dir = program_data / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
            manifest_dir.mkdir(parents=True)
            install_root = temp_root / "AKD" / "ARKDevkit"
            content_root = install_root / "Projects" / "ShooterGame" / "Content"
            asset = content_root / "PrimalEarth" / "CoreBlueprints" / "Projectiles" / "ProjGrenadeTek.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"EventGraph\x00")
            (manifest_dir / "ARKDevKit.item").write_text(
                json.dumps(
                    {
                        "InstallLocation": str(install_root),
                        "MandatoryAppFolderName": "ARKDevkit",
                    }
                ),
                encoding="utf-8",
            )

            empty_config = temp_root / "missing-devkit-content-root.txt"
            environment = {
                "PROGRAMDATA": str(program_data),
                "ARK_DEVKIT_CONTENT_ROOT": "",
                "BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT": "",
                "ARK_DEVKIT_ROOT": "",
                "BLUEPRINT_TO_CODE_DEVKIT_ROOT": "",
                "ARK_DEVKIT_PATH_MAPPINGS": "",
                "BLUEPRINT_TO_CODE_DEVKIT_PATH_MAPPINGS": "",
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(uasset_graphs_module, "DEVKIT_CONTENT_ROOT_FILE", empty_config),
                patch.object(
                    uasset_graphs_module,
                    "DEVKIT_PATH_MAPPINGS_FILE",
                    temp_root / "missing-devkit-path-mappings.txt",
                ),
                patch.object(uasset_graphs_module, "DEFAULT_CONTENT_ROOTS", ()),
            ):
                found, attempted = object_path_to_uasset_path(
                    "/Game/PrimalEarth/CoreBlueprints/Projectiles/ProjGrenadeTek"
                )

        self.assertEqual(found, asset)
        self.assertEqual(attempted, [str(asset)])

    def test_epic_manifest_discovery_ignores_corrupt_and_unrelated_items(self):
        from blueprint_translator.devkit_paths import discover_epic_launcher_content_roots

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_dir = temp_root / "Manifests"
            manifest_dir.mkdir()
            install_root = temp_root / "AKD" / "ARKDevkit"
            content_root = install_root / "Projects" / "ShooterGame" / "Content"
            content_root.mkdir(parents=True)
            (manifest_dir / "broken.item").write_text("{not-json", encoding="utf-8")
            (manifest_dir / "unrelated.item").write_text(
                json.dumps(
                    {
                        "InstallLocation": str(temp_root / "Fortnite"),
                        "MandatoryAppFolderName": "Fortnite",
                    }
                ),
                encoding="utf-8",
            )
            (manifest_dir / "ark.item").write_text(
                json.dumps(
                    {
                        "InstallLocation": str(install_root),
                        "MandatoryAppFolderName": "ARKDevkit",
                    }
                ),
                encoding="utf-8",
            )

            discovered = discover_epic_launcher_content_roots(manifest_dir)

        self.assertEqual(discovered, [content_root])

    def test_explicit_content_root_precedes_epic_manifest_discovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            explicit_root = temp_root / "Explicit" / "Projects" / "ShooterGame" / "Content"
            explicit_root.mkdir(parents=True)
            program_data = temp_root / "ProgramData"
            manifest_dir = program_data / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
            manifest_dir.mkdir(parents=True)
            install_root = temp_root / "AKD" / "ARKDevkit"
            discovered_root = install_root / "Projects" / "ShooterGame" / "Content"
            discovered_root.mkdir(parents=True)
            (manifest_dir / "ARKDevKit.item").write_text(
                json.dumps(
                    {
                        "InstallLocation": str(install_root),
                        "MandatoryAppFolderName": "ARKDevkit",
                    }
                ),
                encoding="utf-8",
            )

            environment = {
                "PROGRAMDATA": str(program_data),
                "BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT": str(explicit_root),
                "ARK_DEVKIT_CONTENT_ROOT": "",
                "ARK_DEVKIT_ROOT": "",
                "BLUEPRINT_TO_CODE_DEVKIT_ROOT": "",
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(
                    uasset_graphs_module,
                    "DEVKIT_CONTENT_ROOT_FILE",
                    temp_root / "missing-devkit-content-root.txt",
                ),
                patch.object(uasset_graphs_module, "DEFAULT_CONTENT_ROOTS", ()),
            ):
                roots = uasset_graphs_module.content_roots()

        self.assertEqual(roots, [explicit_root, discovered_root])

    def test_deeply_corrupt_manifest_does_not_block_explicit_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            explicit_root = temp_root / "Explicit" / "Projects" / "ShooterGame" / "Content"
            explicit_root.mkdir(parents=True)
            program_data = temp_root / "ProgramData"
            manifest_dir = program_data / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "deeply-corrupt.item").write_text(
                "[" * 20000 + "]" * 20000,
                encoding="utf-8",
            )
            (manifest_dir / "oversized-integer.item").write_text(
                '{"value": ' + "9" * 5000 + "}",
                encoding="utf-8",
            )

            environment = {
                "PROGRAMDATA": str(program_data),
                "BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT": str(explicit_root),
                "ARK_DEVKIT_CONTENT_ROOT": "",
                "ARK_DEVKIT_ROOT": "",
                "BLUEPRINT_TO_CODE_DEVKIT_ROOT": "",
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(
                    uasset_graphs_module,
                    "DEVKIT_CONTENT_ROOT_FILE",
                    temp_root / "missing-devkit-content-root.txt",
                ),
                patch.object(uasset_graphs_module, "DEFAULT_CONTENT_ROOTS", ()),
            ):
                roots = uasset_graphs_module.content_roots()

        self.assertEqual(roots, [explicit_root])

    def test_normalize_accepts_mod_relative_reference(self):
        raw = "Kaminan_server/SkinBuff/SkinBuffHuman/MetalShield/BuffSkin_MetalShield.BuffSkin_MetalShield"
        self.assertEqual(
            normalize_blueprint_object_path(raw),
            "/Game/Mods/Kaminan_server/SkinBuff/SkinBuffHuman/MetalShield/BuffSkin_MetalShield.BuffSkin_MetalShield",
        )

    def test_object_path_maps_to_external_mod_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            shooter_game = Path(temp_dir) / "ShooterGame"
            content_root = shooter_game / "Content"
            mod_content_root = shooter_game / "Mods" / "Kaminan_server" / "Content"
            asset = mod_content_root / "Dinos" / "Wyvern" / "MyWyvern_BP.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"EventGraph\x00")

            found, attempted = object_path_to_uasset_path(
                "/Game/Mods/Kaminan_server/Dinos/Wyvern/MyWyvern_BP.MyWyvern_BP",
                extra_roots=[content_root],
            )

        self.assertEqual(found, asset)
        self.assertIn(str(asset), attempted)

    def test_object_path_maps_to_explicit_mod_content_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mod_content_root = Path(temp_dir) / "Mods" / "Kaminan_server" / "Content"
            asset = mod_content_root / "Weapons" / "LightningGun_BP.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"EventGraph\x00")

            found, attempted = object_path_to_uasset_path(
                "/Game/Mods/Kaminan_server/Weapons/LightningGun_BP.LightningGun_BP",
                extra_roots=[mod_content_root],
            )

        self.assertEqual(found, asset)
        self.assertIn(str(asset), attempted)

    def test_safe_string_scan_keeps_likely_graph_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            asset = content_root / "Genesis2" / "Dinos" / "LionfishLion" / "LionfishLion_Character_BP.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(
                b"\x00".join(
                    [
                        b"Amount Of Combo Meter To Add On Melee Hit",
                        b"pack buff",
                        b"Roar",
                        b"Sleep During Day",
                        b"BPUnstasis",
                        b"BPTimerServer",
                        b"BPPreventRiding",
                        b"collapsed awoken from other buff added",
                        b"/Game/Genesis2/Dinos/LionfishLion/Animations/LionfishLion_Roar_Montage",
                        b"K2Node_CallFunction",
                    ]
                )
            )

            payload, _attempted = mine_graph_candidates(
                "/Game/Genesis2/Dinos/LionfishLion/LionfishLion_Character_BP.LionfishLion_Character_BP",
                extra_roots=[content_root],
                max_candidates=100,
            )

        names = {item["name"] for item in payload["candidates"]}
        self.assertIn("Combo", names)
        self.assertIn("Pack Buff", names)
        self.assertIn("Roar", names)
        self.assertIn("Sleep During Day", names)
        self.assertIn("BPUnstasis", names)
        self.assertIn("BPTimerServer", names)
        self.assertIn("BPPreventRiding", names)
        self.assertIn("collapsed awoken from other buff added", names)
        self.assertNotIn("K2Node_CallFunction", names)
        self.assertNotIn("/Game/Genesis2/Dinos/LionfishLion/Animations/LionfishLion_Roar_Montage", names)
        self.assertIn("BPUnstasis | Unknown", render_candidate_text(payload))

    def test_real_order_tagged_properties_recover_nodes_and_ints(self):
        names = ["/Game/Fixture"] + [f"Filler{i}" for i in range(99)] + [
            "Schema",
            "ObjectProperty",
            "Nodes",
            "ArrayProperty",
            "NodePosX",
            "IntProperty",
            "None",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def prop_object(name: str, value: int) -> bytes:
            return fname(name) + fname("ObjectProperty") + struct.pack("<iiB", 0, 4, 0) + struct.pack("<i", value)

        def prop_int(name: str, value: int) -> bytes:
            return fname(name) + fname("IntProperty") + struct.pack("<iiB", 0, 4, 0) + struct.pack("<i", value)

        refs = [2, 3]
        array_value = struct.pack("<i", len(refs)) + b"".join(struct.pack("<i", ref) for ref in refs)
        prop_nodes = (
            fname("Nodes")
            + fname("ArrayProperty")
            + struct.pack("<i", 0)
            + fname("ObjectProperty")
            + struct.pack("<i", 0)
            + struct.pack("<iB", len(array_value), 0)
            + array_value
        )
        data = b"\x00" + prop_object("Schema", -1) + prop_nodes + prop_int("NodePosX", 144) + fname("None")
        exports = [{"display_name": "Graph"}, {"display_name": "NodeA"}, {"display_name": "NodeB"}]

        properties, warnings = parse_export_properties(data, names, [], exports)

        self.assertEqual(warnings, [])
        self.assertEqual(properties["Schema"]["value"], -1)
        self.assertEqual(properties["Nodes"]["value"], refs)
        self.assertEqual(properties["NodePosX"]["value"], 144)

    def test_member_reference_name_can_extend_past_next_property_marker(self):
        names = ["/Game/Fixture"] + [f"Filler{i}" for i in range(99)] + [
            "FunctionReference",
            "StructProperty",
            "HideCategories",
            "MemberReference",
            "MemberName",
            "NameProperty",
            "bCommentBubblePinned",
            "BoolProperty",
            "IsPrimalDino",
            "None",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        data = bytearray(b"\x00" * 160)
        data[0:8] = fname("FunctionReference")
        data[8:16] = fname("StructProperty")
        data[16:24] = fname("HideCategories")
        data[24:32] = fname("MemberReference")
        data[78:86] = fname("MemberName")
        data[86:94] = fname("NameProperty")
        data[94:102] = fname("bCommentBubblePinned")
        data[103:111] = fname("IsPrimalDino")
        data[120:128] = fname("None")

        properties, warnings = parse_export_properties(bytes(data), names, [], [])

        self.assertEqual(warnings, [])
        self.assertEqual(properties["FunctionReference"]["member_name"], "IsPrimalDino")

    def test_invalid_property_type_marker_does_not_overwrite_real_property(self):
        names = [f"Filler{i}" for i in range(100)] + [
            "FunctionReference",
            "StructProperty",
            "MemberReference",
            "MemberName",
            "NameProperty",
            "RealFunction",
            "NotAPropertyType",
            "NodePosX",
            "IntProperty",
            "None",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        data = bytearray(b"\x00" * 220)
        data[0:8] = fname("FunctionReference")
        data[8:16] = fname("StructProperty")
        data[24:32] = fname("MemberReference")
        data[72:80] = fname("MemberName")
        data[80:88] = fname("NameProperty")
        data[97:105] = fname("RealFunction")
        data[128:136] = fname("FunctionReference")
        data[136:144] = fname("NotAPropertyType")
        data[160:168] = fname("NodePosX")
        data[168:176] = fname("IntProperty")
        data[176:185] = struct.pack("<iiB", 0, 4, 0)
        data[185:189] = struct.pack("<i", 42)
        data[196:204] = fname("None")

        properties, warnings = parse_export_properties(bytes(data), names, [], [])

        self.assertEqual(warnings, [])
        self.assertEqual(properties["FunctionReference"]["member_name"], "RealFunction")
        self.assertEqual(properties["NodePosX"]["value"], 42)

    def test_cdo_class_defaults_recover_scalar_struct_and_soft_object_values(self):
        names = [
            "/Game/Fixture",
            "Fixture",
            "/Script/CoreUObject",
            "MaxStoredXP",
            "MinStoredXPForTreasure",
            "StoredXPTreasureQualityMinMax",
            "TreasureSupplyCrateClass",
            "ExTreasureItemEveryNumLevels",
            "DoubleProperty",
            "IntProperty",
            "StructProperty",
            "SoftObjectProperty",
            "Vector2D",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def scalar(name: str, type_name: str, size: int, value: bytes) -> bytes:
            return fname(name) + fname(type_name) + struct.pack("<iiB", 0, size, 2) + (b"G" * 16) + value

        cdo_data = b"".join(
            [
                scalar("MaxStoredXP", "DoubleProperty", 8, struct.pack("<d", 30000.0)),
                scalar("MinStoredXPForTreasure", "DoubleProperty", 8, struct.pack("<d", 5000.0)),
                (
                    fname("StoredXPTreasureQualityMinMax")
                    + fname("StructProperty")
                    + struct.pack("<i", 1)
                    + fname("Vector2D")
                    + struct.pack("<i", 1)
                    + fname("/Script/CoreUObject")
                    + struct.pack("<iiB", 0, 16, 2)
                    + (b"G" * 16)
                    + struct.pack("<dd", 0.000001, 10.000001)
                ),
                scalar("TreasureSupplyCrateClass", "SoftObjectProperty", 4, struct.pack("<i", 1)),
                scalar("ExTreasureItemEveryNumLevels", "IntProperty", 4, struct.pack("<i", 75)),
            ]
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {"file": "uasset", "offset": 0, "size": len(cdo_data), "available": True},
                }
            ],
            "soft_object_paths": [
                {"object_path": "/Game/Other.Other_C"},
                {"object_path": "/Game/Fixture/SupplyCrate.SupplyCrate_C"},
            ],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        report = render_uasset_class_defaults_report(payload)

        variables = payload["variables"]
        self.assertEqual(variables["MaxStoredXP"]["value"], 30000.0)
        self.assertEqual(variables["MinStoredXPForTreasure"]["value"], 5000.0)
        self.assertEqual(variables["StoredXPTreasureQualityMinMax"]["value"]["x"], 0.000001)
        self.assertEqual(variables["StoredXPTreasureQualityMinMax"]["value"]["y"], 10.000001)
        self.assertEqual(variables["TreasureSupplyCrateClass"]["value"], "/Game/Fixture/SupplyCrate.SupplyCrate_C")
        self.assertEqual(variables["ExTreasureItemEveryNumLevels"]["value"], 75)
        properties = {item["name"]: item for item in payload["properties"]}
        self.assertTrue(properties["StoredXPTreasureQualityMinMax"]["struct_parse"]["parsed"])
        self.assertEqual(properties["StoredXPTreasureQualityMinMax"]["struct_parse"]["struct_name"], "Vector2D")
        self.assertEqual(properties["TreasureSupplyCrateClass"]["object"], "/Game/Fixture/SupplyCrate.SupplyCrate_C")
        self.assertIn("MinStoredXPForTreasure", report)

    def test_cdo_soft_object_array_recovers_inline_fname_paths(self):
        damage_type_paths = [
            "/Game/PrimalEarth/CoreBlueprints/DamageTypes/DmgType_Melee_Dino_Herbivore.DmgType_Melee_Dino_Herbivore_C",
            "/Game/PrimalEarth/CoreBlueprints/DamageTypes/DmgType_Melee_SickleHarvest.DmgType_Melee_SickleHarvest_C",
            "/Game/PrimalEarth/CoreBlueprints/DamageTypes/DmgType_Melee_BigfootHarvest.DmgType_Melee_BigfootHarvest_C",
        ]
        names = [
            "None",
            "DamageTypeEntryValuesOverrides",
            "ArrayProperty",
            "SoftObjectProperty",
            *damage_type_paths,
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        array_value = struct.pack("<i", len(damage_type_paths)) + b"".join(
            fname(path) + struct.pack("<i", 0) for path in damage_type_paths
        )
        cdo_data = b"".join(
            [
                fname("DamageTypeEntryValuesOverrides"),
                fname("ArrayProperty"),
                struct.pack("<ii", len(array_value), 0),
                fname("SoftObjectProperty"),
                array_value,
                fname("None"),
            ]
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        prop = {item["name"]: item for item in payload["properties"]}[
            "DamageTypeEntryValuesOverrides"
        ]

        self.assertTrue(prop["array_parse"]["parsed"], prop)
        self.assertEqual(prop["array_parse"]["count"], 3)
        self.assertEqual(prop["objects"], damage_type_paths)
        self.assertEqual(prop["object_paths"], damage_type_paths)
        self.assertEqual(
            [element["object_path"] for element in prop["array_parse"]["elements"]],
            damage_type_paths,
        )

    def test_cdo_inline_soft_object_array_fails_closed_on_malformed_elements(self):
        valid_path = "/Game/Fixture/Damage.Damage_C"
        names = [
            "None",
            "DamageTypeEntryValuesOverrides",
            "ArrayProperty",
            "SoftObjectProperty",
            valid_path,
            "NotAnObjectPath",
        ]

        def fname(name: str, number: int = 0) -> bytes:
            return struct.pack("<ii", names.index(name), number)

        def parse(array_value: bytes):
            cdo_data = b"".join(
                [
                    fname("DamageTypeEntryValuesOverrides"),
                    fname("ArrayProperty"),
                    struct.pack("<ii", len(array_value), 0),
                    fname("SoftObjectProperty"),
                    array_value,
                    fname("None"),
                ]
            )
            package = {
                "uasset_data": cdo_data,
                "uexp_data": b"",
                "names": names,
                "imports": [],
                "exports": [
                    {
                        "object_name": "Default__Fixture_C",
                        "serial_location": {
                            "file": "uasset",
                            "offset": 0,
                            "size": len(cdo_data),
                            "available": True,
                        },
                    }
                ],
                "soft_object_paths": [],
            }
            payload = read_uasset_class_defaults(package, "Fixture")
            return {item["name"]: item for item in payload["properties"]}[
                "DamageTypeEntryValuesOverrides"
            ]

        malformed_values = {
            "ansi fstring missing null terminator": (
                struct.pack("<i", 1)
                + fname(valid_path)
                + struct.pack("<i", 1)
                + b"X"
            ),
            "utf16 fstring missing null terminator": (
                struct.pack("<i", 1)
                + fname(valid_path)
                + struct.pack("<i", -1)
                + b"X\x00"
            ),
            "invalid utf16 fstring encoding": (
                struct.pack("<i", 1)
                + fname(valid_path)
                + struct.pack("<i", -2)
                + b"\x00\xd8\x00\x00"
            ),
            "trailing byte": (
                struct.pack("<i", 1)
                + fname(valid_path)
                + struct.pack("<i", 0)
                + b"\xff"
            ),
            "numbered fname": (
                struct.pack("<i", 1)
                + fname(valid_path, number=1)
                + struct.pack("<i", 0)
            ),
            "non-object path": (
                struct.pack("<i", 1)
                + fname("NotAnObjectPath")
                + struct.pack("<i", 0)
            ),
        }

        for label, array_value in malformed_values.items():
            with self.subTest(label=label):
                prop = parse(array_value)
                self.assertFalse(prop["array_parse"]["parsed"], prop)
                self.assertEqual(prop["objects"], [])
                self.assertEqual(prop["object_paths"], [])

    def test_cdo_soft_object_array_preserves_confirmed_zero_count(self):
        names = [
            "None",
            "DamageTypeEntryValuesOverrides",
            "ArrayProperty",
            "SoftObjectProperty",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        array_value = struct.pack("<i", 0)
        cdo_data = b"".join(
            [
                fname("DamageTypeEntryValuesOverrides"),
                fname("ArrayProperty"),
                struct.pack("<ii", len(array_value), 0),
                fname("SoftObjectProperty"),
                array_value,
                fname("None"),
            ]
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        prop = {item["name"]: item for item in payload["properties"]}[
            "DamageTypeEntryValuesOverrides"
        ]

        self.assertTrue(prop["array_parse"]["parsed"], prop)
        self.assertEqual(prop["array_parse"]["count"], 0)
        self.assertEqual(prop["objects"], [])
        self.assertEqual(prop["object_paths"], [])

    def test_cdo_soft_object_array_preserves_path_table_indices(self):
        soft_object_paths = [
            {"object_path": "/Game/Fixture/First.First_C"},
            {"object_path": "/Game/Fixture/Second.Second_C"},
        ]
        names = [
            "None",
            "DamageTypeEntryValuesOverrides",
            "ArrayProperty",
            "SoftObjectProperty",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        indices = [1, 0]
        array_value = struct.pack("<i", len(indices)) + b"".join(
            struct.pack("<i", index) for index in indices
        )
        cdo_data = b"".join(
            [
                fname("DamageTypeEntryValuesOverrides"),
                fname("ArrayProperty"),
                struct.pack("<ii", len(array_value), 0),
                fname("SoftObjectProperty"),
                array_value,
                fname("None"),
            ]
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": soft_object_paths,
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        prop = {item["name"]: item for item in payload["properties"]}[
            "DamageTypeEntryValuesOverrides"
        ]
        expected = [
            soft_object_paths[index]["object_path"] for index in indices
        ]

        self.assertTrue(prop["array_parse"]["parsed"], prop)
        self.assertEqual(prop["array_parse"]["count"], 2)
        self.assertEqual(prop["objects"], expected)
        self.assertEqual(prop["object_paths"], expected)

    def test_cdo_array_and_unparsed_struct_keep_parser_metadata(self):
        names = [
            "/Game/Fixture",
            "Fixture",
            "/Script/CoreUObject",
            "TreasureItemSets",
            "ItemSetWeights",
            "ArrayProperty",
            "ObjectProperty",
            "StructProperty",
            "MysteryStruct",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        refs = [2, 3]
        array_value = struct.pack("<i", len(refs)) + b"".join(struct.pack("<i", ref) for ref in refs)
        array_prop = (
            fname("TreasureItemSets")
            + fname("ArrayProperty")
            + struct.pack("<i", 0)
            + fname("ObjectProperty")
            + struct.pack("<i", 0)
            + struct.pack("<iB", len(array_value), 2)
            + (b"A" * 16)
            + array_value
        )
        struct_value = b"not-parsed-yet"
        struct_prop = (
            fname("ItemSetWeights")
            + fname("StructProperty")
            + struct.pack("<i", 1)
            + fname("MysteryStruct")
            + struct.pack("<i", 1)
            + fname("/Script/CoreUObject")
            + struct.pack("<iiB", 0, len(struct_value), 2)
            + (b"B" * 16)
            + struct_value
        )
        cdo_data = array_prop + struct_prop
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {"file": "uasset", "offset": 0, "size": len(cdo_data), "available": True},
                },
                {"object_name": "ItemSetA"},
                {"object_name": "ItemSetB"},
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        properties = {item["name"]: item for item in payload["properties"]}

        self.assertEqual(properties["TreasureItemSets"]["value"], refs)
        self.assertEqual(properties["TreasureItemSets"]["array_parse"]["parsed"], True)
        self.assertEqual(properties["TreasureItemSets"]["array_parse"]["element_kind"], "FPackageIndex")
        self.assertEqual(properties["TreasureItemSets"]["objects"], ["ItemSetA", "ItemSetB"])
        self.assertEqual(payload["variables"]["TreasureItemSets"]["value"], refs)
        self.assertEqual(properties["ItemSetWeights"]["struct_parse"]["parsed"], False)
        self.assertEqual(properties["ItemSetWeights"]["struct_parse"]["struct_name"], "MysteryStruct")
        self.assertNotIn("ItemSetWeights", payload["variables"])

    def test_cdo_ark_tagged_struct_array_preserves_element_boundaries(self):
        names = [
            "None",
            "HarvestResourceEntries",
            "ArrayProperty",
            "StructProperty",
            "OverrideQuantityMax",
            "IntProperty",
            "EntryWeight",
            "FloatProperty",
            "ResourceItem",
            "ObjectProperty",
            "MaxHarvestHealth",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def ark_tag(name: str, type_name: str, value: bytes, *, type_meta: bytes = b"") -> bytes:
            return (
                fname(name)
                + fname(type_name)
                + struct.pack("<ii", len(value), 0)
                + type_meta
                + value
            )

        first_entry = b"".join(
            [
                ark_tag("OverrideQuantityMax", "IntProperty", struct.pack("<i", 3)),
                ark_tag("EntryWeight", "FloatProperty", struct.pack("<f", 2.5)),
                ark_tag("ResourceItem", "ObjectProperty", struct.pack("<i", -1)),
                fname("None"),
            ]
        )
        second_entry = b"".join(
            [
                ark_tag("OverrideQuantityMax", "IntProperty", struct.pack("<i", 1)),
                ark_tag("EntryWeight", "FloatProperty", struct.pack("<f", 0.25)),
                ark_tag("ResourceItem", "ObjectProperty", struct.pack("<i", -2)),
                fname("None"),
            ]
        )
        entries_value = struct.pack("<i", 2) + first_entry + second_entry
        cdo_data = b"".join(
            [
                ark_tag(
                    "HarvestResourceEntries",
                    "ArrayProperty",
                    entries_value,
                    type_meta=fname("StructProperty"),
                ),
                ark_tag("MaxHarvestHealth", "FloatProperty", struct.pack("<f", 620.0)),
                fname("None"),
            ]
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [
                {"object_name": "PrimalItemResource_Metal_C"},
                {"object_name": "PrimalItemResource_Stone_C"},
            ],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        properties = {item["name"]: item for item in payload["properties"]}
        entries = properties["HarvestResourceEntries"]

        self.assertEqual(payload["property_count"], 2)
        self.assertNotIn("EntryWeight", properties)
        self.assertEqual(properties["MaxHarvestHealth"]["value"], 620.0)
        self.assertTrue(entries["array_parse"]["parsed"], entries)
        self.assertEqual(entries["array_parse"]["element_kind"], "StructProperty")
        self.assertEqual(entries["array_parse"]["count"], 2)
        self.assertEqual(entries["value"][0]["OverrideQuantityMax"], 3)
        self.assertEqual(entries["value"][1]["EntryWeight"], 0.25)
        self.assertEqual(
            entries["array_parse"]["elements"][0]["properties"][2]["object"],
            "PrimalItemResource_Metal_C",
        )
        self.assertEqual(
            entries["array_parse"]["elements"][1]["properties"][2]["object"],
            "PrimalItemResource_Stone_C",
        )
        self.assertEqual(
            payload["variables"]["HarvestResourceEntries"]["value"],
            entries["value"],
        )

    def test_cdo_ue5_property_type_name_array_is_recovered_from_native_wrapping(self):
        names = [
            "None",
            "AttackInfos",
            "ArrayProperty",
            "StructProperty",
            "DinoAttackInfo",
            "/Script/ShooterGame",
            "AttackName",
            "NameProperty",
            "Bite",
            "Claw",
            "MeleeDamageType",
            "ObjectProperty",
            "MeleeDamageAmount",
            "IntProperty",
            "AttackInterval",
            "FloatProperty",
            "bBasicAttack",
            "BoolProperty",
            "TailValue",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def property_type(name: str, parameters=()) -> bytes:
            return fname(name) + struct.pack("<i", len(parameters)) + b"".join(
                property_type(child_name, child_parameters)
                for child_name, child_parameters in parameters
            )

        def ue5_tag(name: str, type_name: str, value: bytes, *, parameters=()) -> bytes:
            return (
                fname(name)
                + property_type(type_name, parameters)
                + struct.pack("<i", len(value))
                + b"\x00"
                + value
            )

        def ue5_bool(name: str, value: bool) -> bytes:
            return fname(name) + property_type("BoolProperty") + struct.pack("<i", 0) + bytes([value])

        def attack(name: str, damage_type: int, damage: int, interval: float, basic: bool) -> bytes:
            return b"".join(
                [
                    ue5_tag("AttackName", "NameProperty", fname(name)),
                    ue5_tag("MeleeDamageType", "ObjectProperty", struct.pack("<i", damage_type)),
                    ue5_tag("MeleeDamageAmount", "IntProperty", struct.pack("<i", damage)),
                    ue5_tag("AttackInterval", "FloatProperty", struct.pack("<f", interval)),
                    ue5_bool("bBasicAttack", basic),
                    fname("None"),
                ]
            )

        attacks_value = struct.pack("<i", 2) + attack("Bite", -1, 120, 0.5, True) + attack(
            "Claw", -2, 80, 0.75, False
        )
        attack_type_parameters = (
            (
                "StructProperty",
                (("DinoAttackInfo", (("/Script/ShooterGame", ()),)),),
            ),
        )
        attack_array = ue5_tag(
            "AttackInfos",
            "ArrayProperty",
            attacks_value,
            parameters=attack_type_parameters,
        )
        tail = ue5_tag("TailValue", "IntProperty", struct.pack("<i", 7))
        native_prefix = b"native-prefix"
        native_suffix = b"native-suffix"
        cdo_data = native_prefix + attack_array + tail + fname("None") + native_suffix
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [
                {"object_name": "DmgType_Melee_MineStone_C"},
                {"object_name": "DmgType_Melee_Claw_C"},
            ],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        properties = {item["name"]: item for item in payload["properties"]}
        attacks = properties["AttackInfos"]

        self.assertEqual(payload["property_count"], 2)
        self.assertNotIn("AttackName", properties)
        self.assertEqual(properties["TailValue"]["value"], 7)
        self.assertEqual(attacks["array_parse"]["count"], 2)
        self.assertEqual(attacks["value"][0]["AttackName"], "Bite")
        self.assertEqual(attacks["value"][0]["MeleeDamageAmount"], 120)
        self.assertIs(attacks["value"][0]["bBasicAttack"], True)
        self.assertIs(attacks["value"][1]["bBasicAttack"], False)
        self.assertEqual(
            attacks["array_parse"]["elements"][0]["properties"][1]["object"],
            "DmgType_Melee_MineStone_C",
        )

    def test_cdo_compact_tags_with_guid_marker_decode_object_arrays(self):
        names = [
            "None",
            "InvalidHarvestOverrideDamageType",
            "ObjectProperty",
            "OverrideDamageForResourceHarvestingItems",
            "ArrayProperty",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def tagged(name: str, type_name: str, value: bytes, *, type_meta: bytes = b"") -> bytes:
            return (
                fname(name)
                + fname(type_name)
                + struct.pack("<ii", len(value), 0)
                + type_meta
                + b"\x00"
                + value
            )

        refs = [-2, -3, -4]
        array_value = struct.pack("<i", len(refs)) + b"".join(struct.pack("<i", ref) for ref in refs)
        cdo_data = b"".join(
            [
                tagged("InvalidHarvestOverrideDamageType", "ObjectProperty", struct.pack("<i", -1)),
                tagged(
                    "OverrideDamageForResourceHarvestingItems",
                    "ArrayProperty",
                    array_value,
                    type_meta=fname("ObjectProperty"),
                ),
                fname("None"),
            ]
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [
                {"object_name": "DmgType_MineStone_C"},
                {"object_name": "PrimalItemResource_Metal_C"},
                {"object_name": "PrimalItemResource_Obsidian_C"},
                {"object_name": "PrimalItemResource_Crystal_C"},
            ],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        properties = {item["name"]: item for item in payload["properties"]}

        self.assertEqual(payload["property_count"], 2)
        self.assertEqual(
            properties["InvalidHarvestOverrideDamageType"]["object"],
            "DmgType_MineStone_C",
        )
        self.assertEqual(
            properties["OverrideDamageForResourceHarvestingItems"]["objects"],
            [
                "PrimalItemResource_Metal_C",
                "PrimalItemResource_Obsidian_C",
                "PrimalItemResource_Crystal_C",
            ],
        )
        self.assertEqual(
            properties["OverrideDamageForResourceHarvestingItems"]["array_parse"]["count"],
            3,
        )

    def test_cdo_compact_guid_struct_array_skips_the_struct_envelope(self):
        names = [
            "None",
            "HarvestResourceEntries",
            "ArrayProperty",
            "StructProperty",
            "HarvestResourceEntry",
            "EntryWeight",
            "FloatProperty",
            "ResourceItem",
            "ObjectProperty",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def guid_tag(
            name: str,
            type_name: str,
            value: bytes,
            *,
            type_meta: bytes = b"",
            property_guid: bytes | None = None,
        ) -> bytes:
            return (
                fname(name)
                + fname(type_name)
                + struct.pack("<ii", len(value), 0)
                + type_meta
                + (b"\x00" if property_guid is None else b"\x01" + property_guid)
                + value
            )

        def entry(weight: float, resource_index: int) -> bytes:
            return b"".join(
                [
                    guid_tag("EntryWeight", "FloatProperty", struct.pack("<f", weight)),
                    guid_tag(
                        "ResourceItem",
                        "ObjectProperty",
                        struct.pack("<i", resource_index),
                    ),
                    fname("None"),
                ]
            )

        element_stream = entry(1.0, -1) + entry(0.5, -2)
        envelope = guid_tag(
            "HarvestResourceEntries",
            "StructProperty",
            element_stream,
            type_meta=fname("HarvestResourceEntry") + bytes(16),
        )
        outer_value = struct.pack("<i", 2) + envelope
        cdo_data = b"".join(
            [
                guid_tag(
                    "HarvestResourceEntries",
                    "ArrayProperty",
                    outer_value,
                    type_meta=fname("StructProperty"),
                    property_guid=b"\xff" * 16,
                ),
                fname("None"),
            ]
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [
                {"object_name": "PrimalItemResource_Wood_C"},
                {"object_name": "PrimalItemResource_Thatch_C"},
            ],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        properties = {item["name"]: item for item in payload["properties"]}
        entries = properties["HarvestResourceEntries"]

        self.assertTrue(entries["array_parse"]["parsed"], entries)
        self.assertEqual(entries["array_parse"]["count"], 2)
        self.assertEqual(entries["value"][0]["EntryWeight"], 1.0)
        self.assertEqual(entries["value"][1]["EntryWeight"], 0.5)
        self.assertEqual(
            entries["array_parse"]["elements"][0]["properties"][1]["object"],
            "PrimalItemResource_Wood_C",
        )
        self.assertEqual(
            entries["array_parse"]["elements"][1]["properties"][1]["object"],
            "PrimalItemResource_Thatch_C",
        )

    def test_cdo_compact_guid_bool_consumes_marker_before_following_float(self):
        names = [
            "None",
            "bUseHarvestingDamageType",
            "BoolProperty",
            "ArmorDurabilityDegradationMultiplier",
            "FloatProperty",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        bool_tag = (
            fname("bUseHarvestingDamageType")
            + fname("BoolProperty")
            + struct.pack("<ii", 0, 0)
            + b"\x01"  # bool value lives in the tag
            + b"\x00"  # no property GUID follows
        )
        float_tag = (
            fname("ArmorDurabilityDegradationMultiplier")
            + fname("FloatProperty")
            + struct.pack("<ii", 4, 0)
            + b"\x00"  # no property GUID follows
            + struct.pack("<f", 12.0)
        )
        # Real ARK CDO exports may keep four zero padding bytes after None.
        cdo_data = bool_tag + float_tag + fname("None") + (b"\x00" * 4)
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        properties = {item["name"]: item for item in payload["properties"]}

        self.assertEqual(payload["property_count"], 2)
        self.assertIs(properties["bUseHarvestingDamageType"]["value"], True)
        self.assertEqual(
            properties["bUseHarvestingDamageType"]["tag_layout"],
            "ark_compact_guid_marker",
        )
        self.assertAlmostEqual(
            properties["ArmorDurabilityDegradationMultiplier"]["value"],
            12.0,
        )
        self.assertEqual(
            properties["ArmorDurabilityDegradationMultiplier"]["confidence"],
            "high",
        )

    def test_cdo_compact_guid_bool_consumes_optional_property_guid(self):
        names = ["None", "bGuidTagged", "BoolProperty"]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        property_guid = bytes(range(16))
        cdo_data = (
            fname("bGuidTagged")
            + fname("BoolProperty")
            + struct.pack("<ii", 0, 0)
            + b"\x01"
            + b"\x01"
            + property_guid
            + fname("None")
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        prop = payload["properties"][0]

        self.assertIs(prop["value"], True)
        self.assertEqual(prop["tag_layout"], "ark_compact_guid_marker")
        self.assertEqual(prop["raw_size"], 42)

    def test_cdo_compact_scalar_does_not_read_past_declared_value_boundary(self):
        names = ["None", "BrokenInt", "IntProperty", "TailValue"]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        broken = (
            fname("BrokenInt")
            + fname("IntProperty")
            + struct.pack("<ii", 1, 0)
            + b"\x07"
        )
        tail = (
            fname("TailValue")
            + fname("IntProperty")
            + struct.pack("<ii", 4, 0)
            + struct.pack("<i", 42)
        )
        cdo_data = broken + tail + fname("None")
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        broken_prop = next(
            item for item in payload["properties"] if item["name"] == "BrokenInt"
        )

        self.assertNotEqual(broken_prop.get("value"), 775)
        self.assertEqual(broken_prop["confidence"], "low")

    def test_cdo_invalid_object_package_index_is_explicitly_not_recovered(self):
        names = ["None", "InvalidObject", "ObjectProperty"]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        cdo_data = (
            fname("InvalidObject")
            + fname("ObjectProperty")
            + struct.pack("<ii", 4, 0)
            + struct.pack("<i", -99)
            + fname("None")
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [{"object_name": "OnlyValidImport"}],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        invalid = payload["properties"][0]

        self.assertIsNone(invalid["value"])
        self.assertEqual(invalid["package_index"], -99)
        self.assertEqual(invalid["confidence"], "low")
        self.assertIn("outside package maps", invalid["error"])
        self.assertNotIn("InvalidObject", payload["variables"])

    def test_cdo_fixed_array_index_is_preserved_in_variable_projection(self):
        names = ["None", "FixedValue", "IntProperty"]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def tagged(value: int, array_index: int) -> bytes:
            return (
                fname("FixedValue")
                + fname("IntProperty")
                + struct.pack("<ii", 4, array_index)
                + struct.pack("<i", value)
            )

        cdo_data = tagged(10, 0) + tagged(20, 1) + fname("None")
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {
                        "file": "uasset",
                        "offset": 0,
                        "size": len(cdo_data),
                        "available": True,
                    },
                }
            ],
            "soft_object_paths": [],
        }

        payload = read_uasset_class_defaults(package, "Fixture")

        self.assertEqual(
            [item["array_index"] for item in payload["properties"]],
            [0, 1],
        )
        self.assertEqual(payload["variables"]["FixedValue"]["value"], 10)
        self.assertEqual(payload["variables"]["FixedValue[1]"]["value"], 20)

    def test_custom_pin_scan_recovers_pin_names_and_candidate_links(self):
        names = [f"Filler{i}" for i in range(100)] + ["NodePosX", "IntProperty", "None", "execute", "then", "exec", "object"]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        prop = fname("NodePosX") + fname("IntProperty") + struct.pack("<iiB", 0, 4, 0) + struct.pack("<i", 12)
        pin_data = (
            b"A" * 12
            + fname("execute")
            + b"B" * 8
            + struct.pack("<i", 2)
            + b"C" * 8
            + fname("exec")
            + b"D" * 32
            + fname("then")
            + b"E" * 12
            + fname("exec")
            + b"F" * 16
        )
        data = b"\x00" + prop + fname("None") + struct.pack("<i", 0) + struct.pack("<i", 2) + pin_data
        exports = [{"display_name": "Source"}, {"display_name": "Target"}]
        properties, _warnings = parse_export_properties(data, names, [], exports)

        pins, warnings = parse_custom_pins(
            data,
            names,
            properties,
            node_export={"display_name": "Source", "package_index": 1, "class_name": "K2Node_CallFunction"},
            graph_refset={1, 2},
            imports=[],
            exports=exports,
        )

        self.assertEqual(warnings, [])
        self.assertEqual([pin.name for pin in pins], ["execute", "then"])
        self.assertEqual(pins[0].category, "exec")
        self.assertEqual(pins[0].links[0]["target_node"], "Target")
        self.assertEqual(pins[0].source, "uasset_custom_pin_scan")
        self.assertIn(pins[0].confidence, {"medium", "low"})

    def test_legacy_exported_edgraphpin_objects_recover_links(self):
        names = [f"Filler{i}" for i in range(100)] + [
            "Pins",
            "ArrayProperty",
            "ObjectProperty",
            "None",
            "PinName",
            "StrProperty",
            "Direction",
            "ByteProperty",
            "EEdGraphPinDirection",
            "EGPD_Output",
            "PinType",
            "StructProperty",
            "EdGraphPinType",
            "LinkedTo",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def fstring(value: str) -> bytes:
            raw = value.encode("utf-8") + b"\x00"
            return struct.pack("<i", len(raw)) + raw

        def str_prop(name: str, value: str) -> bytes:
            payload = fstring(value)
            return fname(name) + fname("StrProperty") + struct.pack("<ii", len(payload), 0) + payload

        def byte_prop(name: str, value: str) -> bytes:
            return fname(name) + fname("ByteProperty") + struct.pack("<ii", 8, 0) + fname("EEdGraphPinDirection") + fname(value)

        def pin_type_prop(category: str) -> bytes:
            payload = fname("EdGraphPinType") + fstring(category)
            return fname("PinType") + fname("StructProperty") + struct.pack("<ii", len(payload), 0) + payload

        def array_prop(name: str, refs: list[int]) -> bytes:
            payload = struct.pack("<i", len(refs)) + b"".join(struct.pack("<i", ref) for ref in refs)
            return fname(name) + fname("ArrayProperty") + struct.pack("<ii", len(payload), 0) + fname("ObjectProperty") + payload

        source_pin_data = (
            str_prop("PinName", "then")
            + byte_prop("Direction", "EGPD_Output")
            + pin_type_prop("exec")
            + array_prop("LinkedTo", [4])
            + fname("None")
        )
        target_pin_data = str_prop("PinName", "execute") + pin_type_prop("exec") + fname("None")
        uasset_data = source_pin_data + target_pin_data
        exports = [
            {"display_name": "Source", "class_name": "K2Node_IfThenElse", "package_index": 1},
            {"display_name": "Target", "class_name": "K2Node_CallFunction", "package_index": 2},
            {
                "display_name": "SourceThenPin",
                "object_name": "EdGraphPin",
                "class_name": "EdGraphPin",
                "package_index": 3,
                "serial_location": {"file": "uasset", "offset": 0, "size": len(source_pin_data), "available": True},
            },
            {
                "display_name": "TargetExecutePin",
                "object_name": "EdGraphPin",
                "class_name": "EdGraphPin",
                "package_index": 4,
                "serial_location": {
                    "file": "uasset",
                    "offset": len(source_pin_data),
                    "size": len(target_pin_data),
                    "available": True,
                },
            },
        ]
        package = {"uasset_data": uasset_data, "uexp_data": b"", "names": names, "imports": [], "exports": exports}
        node_data = array_prop("Pins", [3]) + fname("None")
        properties, property_warnings = parse_export_properties(node_data, names, [], exports)

        pins, pin_warnings = parse_custom_pins(
            node_data,
            names,
            properties,
            node_export=exports[0],
            graph_refset={1, 2},
            imports=[],
            exports=exports,
            package=package,
            pin_owner_by_ref={
                3: {"node_package_index": 1, "node_name": "Source", "pin_id": "SourceThenPin", "pin_name": "then"},
                4: {"node_package_index": 2, "node_name": "Target", "pin_id": "TargetExecutePin", "pin_name": "execute"},
            },
        )

        self.assertEqual(property_warnings, [])
        self.assertEqual(pin_warnings, [])
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0].name, "then")
        self.assertEqual(pins[0].category, "exec")
        self.assertEqual(pins[0].direction, "EGPD_Output")
        self.assertEqual(pins[0].source, "uasset_exported_pin_object")
        self.assertEqual(pins[0].links[0]["target_node"], "Target")
        self.assertEqual(pins[0].links[0]["target_pin_id"], "TargetExecutePin")
        self.assertEqual(pins[0].links[0]["resolution_status"], "resolved_pin")

    def test_node_semantic_reader_emits_function_semantics(self):
        properties = {
            "FunctionReference": {
                "name": "FunctionReference",
                "type": "StructProperty",
                "member_name": "DoThing",
                "confidence": "medium",
            },
            "NodePosX": {"value": 10, "confidence": "high"},
            "NodePosY": {"value": 20, "confidence": "high"},
        }
        pins = []
        node = node_info_from_export(
            node_export={
                "class_name": "K2Node_CallFunction",
                "display_name": "K2Node_CallFunction_1",
                "index": 0,
                "serial_location": {"offset": 100, "size": 44},
            },
            properties=properties,
            pins=pins,
            index=1,
        )

        self.assertEqual(node.function, "DoThing")
        self.assertEqual(node.semantic["kind"], "call_function")
        self.assertEqual(node.raw_offsets, {"start": 100, "end": 144})
        self.assertEqual(node.source, "uasset_binary")

    def test_pin_link_report_summarizes_node_resolved_links(self):
        payload = {
            "asset_name": "Fixture",
            "generated": "now",
            "graphs": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "status": "complete",
                    "confidence": "medium",
                    "payload": {
                        "nodes": [{"name": "Source"}, {"name": "Target"}],
                        "links": [
                            {
                                "source_node": "Source",
                                "source_pin": "then",
                                "source_pin_category": "exec",
                                "target_node": "Target",
                                "target_pin_id": "",
                            }
                        ],
                    },
                }
            ],
        }

        report = render_pin_link_report(payload)

        self.assertIn("node_resolved_pin_unknown", report)
        self.assertIn("EventGraph", report)

    def test_uasset_vs_clipboard_compare_matches_by_class_distribution(self):
        clipboard_payload = {
            "metadata": {"pin_count": 2, "link_count": 1},
            "nodes": [
                {"node_type": "K2Node_Event", "event": "BeginPlay"},
                {"node_type": "K2Node_CallFunction", "function": "DoThing"},
            ],
            "pins": [{}, {}],
            "links": [{}],
        }
        uasset_payload = {
            "metadata": {"pin_count": 2, "link_count": 1},
            "nodes": [
                {"node_type": "K2Node_Event", "event": "BeginPlay"},
                {"node_type": "K2Node_CallFunction", "function": "DoThing"},
            ],
            "pins": [{}, {}],
            "links": [{}],
        }

        result = compare_clipboard_and_uasset_payloads(clipboard_payload, uasset_payload)

        self.assertEqual(result["node_match_ratio"], 1.0)
        self.assertEqual(result["function_hit_ratio"], 1.0)
        self.assertEqual(result["confidence"], "high")

    def test_link_target_pin_resolver_fills_heuristic_target_pin(self):
        from blueprint_translator.models import NodeInfo, PinInfo

        source = NodeInfo(index=1, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Source")
        target = NodeInfo(index=2, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Target")
        source_pin = PinInfo(id="source_then", name="then", direction="EGPD_Output", category="exec")
        source_pin.links.append({"target_node": "Target", "target_pin_id": "", "confidence": "medium"})
        target_pin = PinInfo(id="target_execute", name="execute", direction="EGPD_Input", category="exec")
        source.pins.append(source_pin)
        target.pins.append(target_pin)

        counts = resolve_graph_link_target_pins([source, target])

        self.assertEqual(counts["resolved_pin_heuristic"], 1)
        self.assertEqual(source_pin.links[0]["target_pin_id"], "target_execute")
        self.assertEqual(source_pin.links[0]["resolution_status"], "resolved_pin_heuristic")
        self.assertEqual(source_pin.links[0]["resolution_method"], "heuristic_direction_category")

    def test_link_resolution_marks_exact_existing_target_pin_id(self):
        from blueprint_translator.models import NodeInfo, PinInfo

        source = NodeInfo(index=1, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Source")
        target = NodeInfo(index=2, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Target")
        source_pin = PinInfo(id="source_then", name="then", direction="EGPD_Output", category="exec")
        source_pin.links.append({"target_node": "Target", "target_pin_id": "target_execute", "confidence": "medium"})
        target_pin = PinInfo(id="target_execute", name="execute", direction="EGPD_Input", category="exec")
        source.pins.append(source_pin)
        target.pins.append(target_pin)

        counts = resolve_graph_link_target_pins([source, target])

        self.assertEqual(counts["resolved_pin"], 1)
        self.assertEqual(source_pin.links[0]["resolution_status"], "resolved_pin")
        self.assertEqual(source_pin.links[0]["resolution_method"], "exact_existing_target_pin_id")

    def test_link_resolution_marks_exact_target_pin_id_candidate(self):
        from blueprint_translator.models import NodeInfo, PinInfo

        source = NodeInfo(index=1, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Source")
        target = NodeInfo(index=2, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Target")
        source_pin = PinInfo(id="source_then", name="then", direction="EGPD_Output", category="exec")
        source_pin.links.append(
            {
                "target_node": "Target",
                "target_pin_id": "",
                "target_pin_id_candidates": ["target_execute"],
                "confidence": "medium",
            }
        )
        target_pin = PinInfo(id="target_execute", name="execute", direction="EGPD_Input", category="exec")
        source.pins.append(source_pin)
        target.pins.append(target_pin)

        counts = resolve_graph_link_target_pins([source, target])

        self.assertEqual(counts["resolved_pin"], 1)
        self.assertEqual(source_pin.links[0]["resolution_status"], "resolved_pin")
        self.assertEqual(source_pin.links[0]["resolution_method"], "exact_target_pin_id_candidate")
        self.assertEqual(source_pin.links[0]["confidence"], "high")

    def test_incoming_links_synthesize_boundary_pins(self):
        from blueprint_translator.models import NodeInfo, PinInfo

        call = NodeInfo(index=1, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Call")
        entry = NodeInfo(index=2, class_name="K2Node_FunctionEntry", node_type="K2Node_FunctionEntry", name="Entry")
        call_pin = PinInfo(id="call_execute", name="execute", direction="EGPD_Input", category="exec")
        call_pin.links.append({"target_node": "Entry", "target_pin_id": "", "confidence": "medium"})
        call.pins.append(call_pin)

        warnings = synthesize_boundary_pins_from_incoming_links([call, entry])
        counts = resolve_graph_link_target_pins([call, entry])

        self.assertEqual(len(entry.pins), 1)
        self.assertEqual(entry.pins[0].source, "uasset_reverse_link_synthesis")
        self.assertEqual(entry.pins[0].category, "exec")
        self.assertIn("Synthesized 1 boundary pins", warnings[0])
        self.assertEqual(counts["resolved_pin_heuristic"], 1)
        self.assertEqual(call_pin.links[0]["target_pin_id"], entry.pins[0].id)
        self.assertEqual(call_pin.links[0]["resolution_method"], "heuristic_direction_category")

    def test_empty_event_and_construction_graphs_are_complete(self):
        from blueprint_translator.models import NodeInfo, PinInfo

        self.assertTrue(
            is_complete_empty_graph(
                [],
                [],
                [],
                graph_name="EventGraph",
                graph_type="EventGraph",
            )
        )
        self.assertTrue(
            is_complete_empty_graph(
                [
                    NodeInfo(
                        index=1,
                        class_name="K2Node_FunctionEntry",
                        node_type="K2Node_FunctionEntry",
                        name="K2Node_FunctionEntry_728",
                        function="UserConstructionScript",
                    )
                ],
                [1],
                [],
                graph_name="UserConstructionScript",
                graph_type="ConstructionScript",
            )
        )
        self.assertTrue(
            is_complete_empty_graph(
                [
                    NodeInfo(
                        index=1,
                        class_name="K2Node_FunctionEntry",
                        node_type="K2Node_FunctionEntry",
                        name="K2Node_FunctionEntry_1",
                        pins=[PinInfo(name="then", direction="EGPD_Output", category="exec")],
                    )
                ],
                [1],
                [],
                graph_name="EmptyFunction",
                graph_type="Function",
            )
        )

    def test_partial_triage_and_quality_gates_are_structured(self):
        payload = {
            "generated": "now",
            "asset_name": "Fixture",
            "graph_count": 307,
            "node_count": 11117,
            "pin_count": 38640,
            "link_count": 26940,
            "status_counts": {"complete": 250, "partial": 57},
            "failure_category_counts": {"need_manual_clipboard": 1},
            "unknown_properties": [],
            "pin_links": {"summary": {"resolution_counts": {"node_resolved_pin_unknown": 10}, "kind_counts": {"exec": 1}}, "graphs": []},
            "graphs": [
                {
                    "graph": "PartialGraph",
                    "graph_type": "Function",
                    "status": "partial",
                    "confidence": "medium",
                    "node_count": 1,
                    "pin_count": 1,
                    "link_count": 0,
                    "warnings": [],
                    "failure_categories": ["need_manual_clipboard"],
                }
            ],
        }

        triage = build_partial_graph_triage(payload)
        gates = build_quality_gate_payload(payload)

        self.assertEqual(triage["partial_graph_count"], 1)
        self.assertEqual(triage["graphs"][0]["primary_reason"], "manual_only")
        self.assertTrue(gates["passed"])

    def test_compare_uasset_with_clipboard_fixture_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "FixtureAsset"
            graphs_dir = asset_dir / "graphs"
            uasset_dir = asset_dir / "graphs_from_uasset"
            graphs_dir.mkdir(parents=True)
            uasset_dir.mkdir(parents=True)
            (graphs_dir / "EventGraph.txt").write_text(
                """
Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="K2Node_Event_0"
   EventReference=(MemberName="ReceiveBeginPlay")
End Object
""",
                encoding="utf-8",
            )
            (uasset_dir / "EventGraph_1.json").write_text(
                json_dumps(
                    {
                        "metadata": {"graph_name": "EventGraph", "pin_count": 0, "link_count": 0},
                        "nodes": [{"node_type": "K2Node_Event", "event": "ReceiveBeginPlay"}],
                        "pins": [],
                        "links": [],
                    }
                ),
                encoding="utf-8",
            )

            result = compare_uasset_with_clipboard(asset_dir)

        self.assertEqual(result["matched_graph_count"], 1)


def json_dumps(value):
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    unittest.main()
