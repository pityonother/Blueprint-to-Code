import json
import shutil
import sys
import tempfile
import unittest
from struct import pack
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.resource_nodes import (  # noqa: E402
    attach_component_resources,
    build_node_resource_id,
    cache_resource_node_thumbnail,
    component_facts_from_report,
    component_source_freshness,
    extract_length_prefixed_jpeg_thumbnail,
    extract_uasset_thumbnail,
    extract_resource_node,
    query_resource_nodes,
    rank_node_resource,
    referenced_component_package_paths,
    resource_display_name,
    resolve_object_reference,
    scan_direct_map_references,
    scan_pcg_map_references,
    scan_world_partition_external_actor_references,
)


def _jpeg(width: int = 32, height: int = 16) -> bytes:
    return (
        b"\xff\xd8"
        b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + b"\xff\xd9"
    )


class ResourceNodeTests(unittest.TestCase):
    def test_resource_display_name_prefers_exact_devkit_name_and_keeps_readable_fallbacks(self):
        names = {
            "primalitemconsumable_jellyvenom_c": "Bio Toxin",
            (
                "/game/aberration/coreblueprints/items/consumables/"
                "primalitemresource_commonmushroom."
                "primalitemresource_commonmushroom_c"
            ): "Aggeravic Mushroom",
        }

        self.assertEqual(
            resource_display_name(
                "PrimalItemConsumable_JellyVenom_C",
                names,
            ),
            "Bio Toxin",
        )
        self.assertEqual(
            resource_display_name(
                "PrimalItemResource_CommonMushroom_C",
                names,
                resource_object_path=(
                    "/Game/Aberration/CoreBlueprints/Items/Consumables/"
                    "PrimalItemResource_CommonMushroom."
                    "PrimalItemResource_CommonMushroom_C"
                ),
            ),
            "Aggeravic Mushroom",
        )
        self.assertEqual(
            resource_display_name("PrimalItemConsumable_Berry_Amarberry_C"),
            "Amarberry",
        )
        self.assertEqual(
            resource_display_name("PrimalItemConsumable_Seed_Amarberry_C"),
            "Amarberry Seed",
        )
        self.assertEqual(
            resource_display_name("PrimalItemConsumable_CustomFruit_C"),
            "Custom Fruit",
        )

    def test_extracts_only_exact_length_prefixed_jpeg_inside_uasset_header(self):
        jpeg = _jpeg(width=48, height=24)
        data = b"prefix" + pack("<I", len(jpeg)) + jpeg + b"payload"

        result = extract_length_prefixed_jpeg_thumbnail(data, len(data) - len(b"payload"))

        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["mimeType"], "image/jpeg")
        self.assertEqual(result["width"], 48)
        self.assertEqual(result["height"], 24)
        self.assertEqual(result["sizeBytes"], len(jpeg))
        self.assertEqual(result["data"], jpeg)

    def test_thumbnail_extraction_rejects_wrong_length_and_ambiguous_images(self):
        jpeg = _jpeg()
        wrong_length = b"prefix" + pack("<I", len(jpeg) + 1) + jpeg
        first = pack("<I", len(jpeg)) + jpeg
        ambiguous = first + b"middle" + first

        wrong = extract_length_prefixed_jpeg_thumbnail(wrong_length, len(wrong_length))
        multiple = extract_length_prefixed_jpeg_thumbnail(ambiguous, len(ambiguous))

        self.assertEqual(wrong["status"], "NOT_RECOVERED")
        self.assertEqual(wrong["reasonCode"], "UASSET_THUMBNAIL_NOT_RECOVERED")
        self.assertEqual(multiple["status"], "NOT_RECOVERED")
        self.assertEqual(multiple["reasonCode"], "UASSET_THUMBNAIL_AMBIGUOUS")

    def test_uasset_thumbnail_uses_declared_total_header_size(self):
        jpeg = _jpeg()
        data = b"header" + pack("<I", len(jpeg)) + jpeg + b"outside"
        with tempfile.TemporaryDirectory() as temp_dir:
            asset = Path(temp_dir) / "Node.uasset"
            asset.write_bytes(data)
            with patch(
                "blueprint_translator.resource_nodes.parse_uasset_summary",
                return_value=({"total_header_size": len(data) - len(b"outside")}, []),
            ):
                result = extract_uasset_thumbnail(asset)

        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["data"], jpeg)

    def test_thumbnail_cache_uses_content_hash_and_returns_no_inline_bytes(self):
        jpeg = _jpeg(width=64, height=64)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Node.uasset"
            asset.write_bytes(b"unused")
            with patch(
                "blueprint_translator.resource_nodes.extract_uasset_thumbnail",
                return_value={
                    "status": "AVAILABLE",
                    "mimeType": "image/jpeg",
                    "width": 64,
                    "height": 64,
                    "sizeBytes": len(jpeg),
                    "offset": 10,
                    "data": jpeg,
                },
            ):
                result = cache_resource_node_thumbnail(asset, root / "images")

            image_path = root / "images" / f"{result['sha256']}.jpg"
            self.assertEqual(image_path.read_bytes(), jpeg)
            self.assertEqual(result["url"], f"/api/harvest/images/{result['sha256']}.jpg")
            self.assertNotIn("data", result)

            image_path.write_bytes(b"x" * len(jpeg))
            with patch(
                "blueprint_translator.resource_nodes.extract_uasset_thumbnail",
                return_value={
                    "status": "AVAILABLE",
                    "mimeType": "image/jpeg",
                    "width": 64,
                    "height": 64,
                    "sizeBytes": len(jpeg),
                    "offset": 10,
                    "data": jpeg,
                },
            ):
                cache_resource_node_thumbnail(asset, root / "images")
            self.assertEqual(image_path.read_bytes(), jpeg)

    def test_referenced_component_manifest_uses_exact_unique_package_paths(self):
        first = self._metal_node()
        second = self._metal_node()
        second["id"] = "second"
        unresolved = self._metal_node()
        unresolved["id"] = "unresolved"
        unresolved["harvestComponent"] = {
            "status": "NOT_RECOVERED",
            "packagePath": "/Game/Components/Unknown",
        }

        self.assertEqual(
            referenced_component_package_paths([first, second, unresolved]),
            [
                "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/"
                "MetalHarvestComponent"
            ],
        )

    def test_component_catalog_is_preferred_over_target_filtered_components(self):
        matched = {"objectPath": "/Game/Components/Metal", "resourceEntries": []}
        all_components = [
            matched,
            {"objectPath": "/Game/Components/Gem", "resourceEntries": []},
        ]

        result = component_facts_from_report(
            {"components": [matched], "componentCatalog": all_components}
        )

        self.assertEqual(result, all_components)

    def test_legacy_report_components_remain_supported(self):
        components = [{"objectPath": "/Game/Components/Metal", "resourceEntries": []}]

        self.assertEqual(component_facts_from_report({"components": components}), components)

    def test_resolves_imported_generated_class_to_full_object_path(self):
        imports = [
            {
                "class_name": "BlueprintGeneratedClass",
                "outer_index": -2,
                "object_name": "MetalHarvestComponent_C",
            },
            {
                "class_name": "Package",
                "outer_index": 0,
                "object_name": "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/MetalHarvestComponent",
            },
        ]

        resolved = resolve_object_reference(-1, imports, [])

        self.assertEqual(resolved["status"], "CONFIRMED")
        self.assertEqual(resolved["name"], "MetalHarvestComponent_C")
        self.assertEqual(
            resolved["packagePath"],
            "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/MetalHarvestComponent",
        )
        self.assertEqual(
            resolved["objectPath"],
            "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/MetalHarvestComponent.MetalHarvestComponent_C",
        )

    def test_extracts_resource_bearing_foliage_actor_definition(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir)
            asset = content_root / "Aberration" / "FA_LumaA_02.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"actor foliage fixture")
            imports = [
                {
                    "class_name": "BlueprintGeneratedClass",
                    "outer_index": -2,
                    "object_name": "BP_LumaA_02_C",
                },
                {
                    "class_name": "Package",
                    "outer_index": 0,
                    "object_name": "/Game/Aberration/BP_LumaA_02",
                },
                {
                    "class_name": "BlueprintGeneratedClass",
                    "outer_index": -4,
                    "object_name": "LumaA02HarvestComponent_C",
                },
                {
                    "class_name": "Package",
                    "outer_index": 0,
                    "object_name": "/Game/Aberration/LumaA02HarvestComponent",
                },
            ]
            package = {
                "names": ["ActorClass", "AttachedComponentClass"],
                "imports": imports,
                "exports": [
                    {
                        "object_name": "FA_LumaA_02",
                        "class_name": "FoliageType_Actor",
                    }
                ],
            }
            blocks = [
                {"name": "ActorClass"},
                {"name": "AttachedComponentClass"},
            ]

            def parsed(_data, block, _names, _imports, _exports):
                index = -1 if block["name"] == "ActorClass" else -3
                return {
                    "package_index": index,
                    "raw_offsets": {"start": 1, "end": 2},
                    "confidence": "high",
                }

            with (
                patch(
                    "blueprint_translator.resource_nodes.parse_uasset_package",
                    return_value=package,
                ),
                patch(
                    "blueprint_translator.resource_nodes.export_data_bytes",
                    return_value=b"properties",
                ),
                patch(
                    "blueprint_translator.resource_nodes.cdo_property_tag_blocks",
                    return_value=blocks,
                ),
                patch(
                    "blueprint_translator.resource_nodes.parse_property_block_value",
                    side_effect=parsed,
                ),
            ):
                node = extract_resource_node(asset, content_root)

        self.assertEqual(node["nodeType"], "FOLIAGE_ACTOR")
        self.assertEqual(node["assetClass"], "FoliageType_Actor")
        self.assertEqual(node["actorClass"]["status"], "CONFIRMED")
        self.assertEqual(
            node["actorClass"]["objectPath"],
            "/Game/Aberration/BP_LumaA_02.BP_LumaA_02_C",
        )
        self.assertEqual(node["mesh"]["status"], "NOT_APPLICABLE")
        self.assertEqual(
            node["harvestComponent"]["packagePath"],
            "/Game/Aberration/LumaA02HarvestComponent",
        )
        self.assertNotIn("MESH_NOT_RECOVERED", node["gaps"])

    def test_attaches_component_resources_and_builds_stable_node_resource_ids(self):
        node = self._metal_node()
        components = [
            {
                "component": "MetalHarvestComponent",
                "objectPath": (
                    "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/"
                    "MetalHarvestComponent.MetalHarvestComponent"
                ),
                "resourceEntries": [
                    {"entryIndex": 0, "resource": "PrimalItemResource_Stone_C", "gaps": []},
                    {"entryIndex": 1, "resource": "PrimalItemResource_Metal_C", "gaps": []},
                ],
            }
        ]

        result = attach_component_resources(node, components)

        self.assertEqual(result["resources"]["status"], "CONFIRMED")
        self.assertEqual(result["resources"]["count"], 2)
        metal = result["resources"]["items"][1]
        self.assertEqual(metal["resource"], "PrimalItemResource_Metal_C")
        self.assertEqual(metal["resourceKey"], "PrimalItemResource_Metal_C")
        self.assertEqual(
            metal["nodeResourceId"],
            build_node_resource_id(
                "node-metal",
                "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/MetalHarvestComponent",
                1,
                "PrimalItemResource_Metal_C",
            ),
        )

    def test_attached_resource_uses_exact_devkit_name_without_changing_class_identity(self):
        object_path = (
            "/Game/PrimalEarth/CoreBlueprints/Items/Consumables/"
            "PrimalItemConsumable_JellyVenom.PrimalItemConsumable_JellyVenom_C"
        )
        components = [
            {
                "component": "JellyHarvestComponent",
                "objectPath": (
                    "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/"
                    "MetalHarvestComponent.MetalHarvestComponent"
                ),
                "resourceEntries": [
                    {
                        "entryIndex": 0,
                        "resource": "PrimalItemConsumable_JellyVenom_C",
                        "resourceObjectPath": object_path,
                        "gaps": [],
                    }
                ],
            }
        ]

        result = attach_component_resources(
            self._metal_node(),
            components,
            display_names={object_path.casefold(): "Bio Toxin"},
        )

        resource = result["resources"]["items"][0]
        self.assertEqual(resource["resource"], "PrimalItemConsumable_JellyVenom_C")
        self.assertEqual(resource["resourceKey"], object_path)
        self.assertEqual(resource["resourceObjectPath"], object_path)
        self.assertEqual(resource["displayName"], "Bio Toxin")
        self.assertEqual(
            resource["nodeResourceId"],
            build_node_resource_id(
                "node-metal",
                "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/MetalHarvestComponent",
                0,
                "PrimalItemConsumable_JellyVenom_C",
            ),
        )

    def test_missing_component_facts_stays_source_not_available_not_empty_zero(self):
        result = attach_component_resources(self._metal_node(), [])

        self.assertEqual(result["resources"]["status"], "SOURCE_NOT_AVAILABLE")
        self.assertIsNone(result["resources"]["count"])
        self.assertEqual(result["resources"]["items"], [])
        self.assertIn("HARVEST_COMPONENT_FACTS_NOT_AVAILABLE", result["gaps"])

    def test_component_semantic_gap_is_unknown_not_confirmed_empty(self):
        component = {
            "component": "CrystalHarvestComponent",
            "objectPath": (
                "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/"
                "MetalHarvestComponent.MetalHarvestComponent"
            ),
            "resourceEntries": [],
            "gaps": ["HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED"],
        }

        result = attach_component_resources(self._metal_node(), [component])

        self.assertEqual(result["resources"]["status"], "NOT_RECOVERED")
        self.assertIsNone(result["resources"]["count"])
        self.assertEqual(result["resources"]["items"], [])
        self.assertIn("HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED", result["gaps"])

    def test_node_query_is_bounded_and_filters_by_confirmed_resource(self):
        first = attach_component_resources(
            self._metal_node(),
            [
                {
                    "component": "MetalHarvestComponent",
                    "objectPath": (
                        "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/"
                        "MetalHarvestComponent.MetalHarvestComponent"
                    ),
                    "resourceEntries": [
                        {"entryIndex": 0, "resource": "PrimalItemResource_Metal_C", "gaps": []}
                    ],
                }
            ],
        )
        first["harvestComponent"]["packagePath"] = "/Game/Components/MetalHarvestComponent"
        second = {
            **self._metal_node(),
            "id": "node-tree",
            "name": "UmbrellaTree_SM_settings",
            "objectPath": "/Game/Nodes/UmbrellaTree_SM_settings.UmbrellaTree_SM_settings",
            "resources": {
                "status": "CONFIRMED",
                "count": 1,
                "items": [
                    {
                        "entryIndex": 0,
                        "resource": "PrimalItemResource_Wood_C",
                        "nodeResourceId": "nr-wood",
                    }
                ],
            },
        }
        catalog = {"schema": "ark-resource-node-catalog/v1", "coverage": {}, "nodes": [first, second]}

        page = query_resource_nodes(catalog, resource="PrimalItemResource_Metal_C", limit=1)

        self.assertEqual(page["total"], 1)
        self.assertEqual(page["limit"], 1)
        self.assertEqual([item["id"] for item in page["items"]], ["node-metal"])
        self.assertIsNone(page["nextOffset"])

    def test_node_page_uses_map_preview_instead_of_returning_every_reference(self):
        node = self._metal_node()
        node["mesh"] = {
            "status": "CONFIRMED",
            "name": "SM_MetalRock_01",
            "objectPath": "/Game/Meshes/SM_MetalRock_01.SM_MetalRock_01",
            "evidence": {"offset": 12},
        }
        node["mapReferences"] = {
            "status": "DIRECT_SCAN_COMPLETE",
            "count": 20,
            "items": [
                {
                    "id": f"map-{index}",
                    "name": f"Map_{index}",
                    "objectPath": (
                        f"/Game/Maps/TheIslandSubMaps/Map_{index}"
                        if index == 19
                        else f"/Game/Maps/Genesis2/Map_{index}"
                    ),
                    "mapFamily": "TheIsland" if index == 19 else "Genesis2",
                    "mapKind": "PLAYABLE_MAP_EVIDENCE",
                    "relation": "DIRECT_PACKAGE_REFERENCE",
                    "evidenceStatus": "CONFIRMED",
                }
                for index in range(20)
            ],
        }
        node["evidence"] = {"sourceSha256": "a" * 64, "large": "do not return"}
        catalog = {"schema": "ark-resource-node-catalog/v1", "coverage": {}, "nodes": [node]}

        page = query_resource_nodes(catalog)

        preview = page["items"][0]["mapReferences"]
        self.assertEqual(preview["count"], 20)
        self.assertEqual(len(preview["items"]), 6)
        self.assertTrue(preview["truncated"])
        self.assertEqual(
            {
                item["mapFamily"]
                for item in page["items"][0]["mapUsage"]["families"]
            },
            {"Genesis2", "TheIsland"},
        )
        self.assertNotIn("evidence", page["items"][0])
        self.assertEqual(set(page["items"][0]["mesh"]), {"status", "name"})
        self.assertEqual(
            set(preview["items"][0]),
            {
                "id",
                "name",
                "objectPath",
                "mapFamily",
                "mapKind",
                "relation",
                "evidenceStatus",
            },
        )

    def test_node_page_caps_at_sixteen_and_stays_below_api_byte_budget(self):
        nodes = []
        for index in range(48):
            node = self._metal_node()
            node.update(
                {
                    "id": f"node-{index:02d}",
                    "name": f"Large node {index:02d}",
                    "objectPath": f"/Game/Nodes/Large_{index:02d}.Large_{index:02d}",
                    "mapReferences": {
                        "status": "DIRECT_SCAN_COMPLETE",
                        "count": 20,
                        "items": [
                            {
                                "id": f"map-{map_index}",
                                "name": f"Map_{map_index}",
                                "objectPath": f"/Game/Maps/Genesis2/Map_{map_index}",
                                "relation": "DIRECT_PACKAGE_REFERENCE",
                                "evidenceStatus": "CONFIRMED",
                                "usageStatus": "CANDIDATE",
                            }
                            for map_index in range(20)
                        ],
                    },
                }
            )
            nodes.append(node)
        catalog = {
            "schema": "ark-resource-node-catalog/v1",
            "dataset": {"revision": "a" * 64},
            "coverage": {"nodesDecoded": 48},
            "nodes": nodes,
        }

        page = query_resource_nodes(catalog, limit=999)
        encoded = json.dumps(
            {"ok": True, **page},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertEqual(page["limit"], 16)
        self.assertEqual(len(page["items"]), 16)
        self.assertEqual(page["nextOffset"], 16)
        self.assertLess(len(encoded), 64 * 1024)

    def test_ranking_is_scoped_to_exact_node_component_and_resource(self):
        node = attach_component_resources(
            self._metal_node(),
            [
                {
                    "component": "MetalHarvestComponent",
                    "objectPath": (
                        "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/"
                        "MetalHarvestComponent.MetalHarvestComponent"
                    ),
                    "resourceEntries": [
                        {"entryIndex": 1, "resource": "PrimalItemResource_Metal_C", "gaps": []}
                    ],
                }
            ],
        )
        resource = node["resources"]["items"][0]
        catalog = {"schema": "ark-resource-node-catalog/v1", "coverage": {}, "nodes": [node]}
        report = {
            "schema": "ark-harvest-ranking/v1",
            "coverage": {"creaturesRequested": 4, "creaturesLoaded": 4},
            "bestRows": [
                self._rank_row(
                    "Ankylosaurus", "MetalHarvestComponent", 220.0, legacy_score=9999.0
                ),
                self._rank_row(
                    "Magmasaur", "MetalHarvestComponent", 480.0, legacy_score=1.0
                ),
                self._rank_row("Wrong component", "MetalHarvestComponent_Rich", 9999.0),
                self._rank_row("Wrong resource", "MetalHarvestComponent", 8888.0, resource="PrimalItemResource_Stone_C"),
                {
                    **self._rank_row("Legacy only", "MetalHarvestComponent", 7777.0),
                    "estimatedYieldPerNode": None,
                    "engineComparisonIndex": 7777.0,
                },
            ],
        }

        result = rank_node_resource(
            catalog,
            report,
            node_id="node-metal",
            node_resource_id=resource["nodeResourceId"],
            limit=10,
        )

        self.assertEqual([row["creature"] for row in result["items"]], ["Magmasaur", "Ankylosaurus"])
        self.assertEqual(
            [row["estimatedYieldPerNode"] for row in result["items"]],
            [480.0, 220.0],
        )
        self.assertEqual(
            [row["engineComparisonIndex"] for row in result["items"]],
            [480.0, 220.0],
        )
        self.assertEqual(result["schema"], "blueprint-to-code.harvest-ranking-result/v2")
        self.assertEqual(result["methodology"]["metric"], "estimatedYieldPerNode")
        self.assertEqual(
            result["methodology"]["scoreBasis"],
            "ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE",
        )
        self.assertEqual(result["coverage"]["nonRankedForNodeResource"], 1)
        self.assertFalse(result["claimsGlobalTop"])
        self.assertEqual(result["coverage"]["creaturesLoaded"], 4)
        self.assertEqual(result["scopeStatus"], "SCANNED_CREATURES_ONLY")

    def test_direct_map_scan_records_exact_package_reference_and_keeps_indirect_unknown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            map_root = content_root / "Maps" / "TestMap"
            map_root.mkdir(parents=True)
            referenced = map_root / "Referenced.umap"
            referenced.write_bytes(
                b"prefix /Game/Nodes/SM_MetalRock_01_settings suffix"
            )
            (map_root / "Other.umap").write_bytes(b"unrelated")

            nodes, coverage = scan_direct_map_references(
                [self._metal_node()], [map_root], content_root=content_root
            )

        references = nodes[0]["mapReferences"]
        self.assertEqual(references["status"], "DIRECT_SCAN_COMPLETE")
        self.assertEqual(references["count"], 1)
        self.assertEqual(references["indirectStatus"], "NOT_INDEXED")
        self.assertEqual(references["items"][0]["objectPath"], "/Game/Maps/TestMap/Referenced")
        self.assertEqual(coverage["filesScanned"], 2)

    def test_map_scan_matches_ascii_and_utf16_package_tokens_without_prefix_false_positives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            map_root = content_root / "Maps"
            map_root.mkdir(parents=True)
            package = "/Game/Nodes/SM_MetalRock_01_settings"
            (map_root / "Ascii.umap").write_bytes(
                f"prefix {package}.SM_MetalRock_01_settings suffix".encode("ascii")
            )
            (map_root / "Utf16.umap").write_bytes(
                f"prefix {package} suffix".encode("utf-16-le")
            )
            (map_root / "PrefixOnly.umap").write_bytes(
                f"{package}_DifferentNode".encode("ascii")
            )

            nodes, coverage = scan_direct_map_references(
                [self._metal_node()], [map_root], content_root=content_root
            )

        references = nodes[0]["mapReferences"]
        self.assertEqual(references["count"], 2)
        self.assertEqual(
            {item["name"] for item in references["items"]},
            {"Ascii", "Utf16"},
        )
        self.assertEqual(coverage["filesScanned"], 3)

    def test_pcg_map_scan_adds_indirect_map_family_without_erasing_direct_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            direct_root = content_root / "Maps" / "Genesis2"
            direct_root.mkdir(parents=True)
            package = "/Game/Nodes/SM_MetalRock_01_settings"
            (direct_root / "Eden.umap").write_bytes(package.encode("ascii"))
            pcg_root = (
                content_root
                / "Art_Tools"
                / "Level_Tools"
                / "PCG"
                / "PCG_Biomes"
            )
            pcg_asset = pcg_root / "TheCenter" / "PCGBiome_RichResources.uasset"
            pcg_asset.parent.mkdir(parents=True)
            pcg_asset.write_bytes(package.encode("utf-16-le"))

            direct_nodes, _direct_coverage = scan_direct_map_references(
                [self._metal_node()], [direct_root], content_root=content_root
            )
            nodes, coverage = scan_pcg_map_references(
                direct_nodes, [pcg_root], content_root=content_root
            )

        references = nodes[0]["mapReferences"]
        self.assertEqual(references["count"], 2)
        self.assertEqual(references["indirectStatus"], "PCG_BIOME_SCAN_COMPLETE")
        self.assertEqual(
            {(item["mapFamily"], item["relation"]) for item in references["items"]},
            {
                ("Genesis2", "DIRECT_PACKAGE_REFERENCE"),
                ("TheCenter", "PCG_BIOME_REFERENCE"),
            },
        )
        pcg_reference = next(
            item
            for item in references["items"]
            if item["relation"] == "PCG_BIOME_REFERENCE"
        )
        self.assertEqual(
            pcg_reference["objectPath"],
            "/Game/Art_Tools/Level_Tools/PCG/PCG_Biomes/TheCenter/PCGBiome_RichResources",
        )
        self.assertEqual(pcg_reference["usageStatus"], "CANDIDATE")
        self.assertEqual(coverage["filesScanned"], 1)
        self.assertEqual(coverage["families"], ["TheCenter"])

    def test_node_page_preview_keeps_map_provenance_and_family(self):
        node = self._metal_node()
        node["mapReferences"] = {
            "status": "REFERENCE_SCAN_COMPLETE",
            "count": 1,
            "indirectStatus": "PCG_BIOME_SCAN_COMPLETE",
            "items": [
                {
                    "id": "pcg-1",
                    "name": "PCGBiome_RichResources",
                    "objectPath": "/Game/PCG/PCGBiome_RichResources",
                    "mapFamily": "TheCenter",
                    "relation": "PCG_BIOME_REFERENCE",
                    "evidenceStatus": "CONFIRMED",
                    "usageStatus": "CANDIDATE",
                }
            ],
        }

        page = query_resource_nodes(
            {"schema": "ark-resource-node-catalog/v1", "coverage": {}, "nodes": [node]},
            limit=1,
        )

        preview = page["items"][0]["mapReferences"]["items"][0]
        self.assertEqual(preview["mapFamily"], "TheCenter")
        self.assertEqual(preview["relation"], "PCG_BIOME_REFERENCE")
        self.assertEqual(preview["evidenceStatus"], "CONFIRMED")
        self.assertEqual(preview["usageStatus"], "CANDIDATE")

    def test_world_partition_external_actor_scan_aggregates_the_island_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            external_root = (
                content_root
                / "__ExternalActors__"
                / "Maps"
                / "TheIslandSubMaps"
                / "TheIsland_WP"
            )
            package = "/Game/Nodes/SM_MetalRock_01_settings"
            for index in range(2):
                actor = external_root / "F" / str(index) / f"Actor{index}.uasset"
                actor.parent.mkdir(parents=True, exist_ok=True)
                actor.write_bytes(f"prefix {package} suffix".encode("ascii"))

            nodes, coverage = scan_world_partition_external_actor_references(
                [self._metal_node()],
                [external_root],
                content_root=content_root,
                prefer_rg=False,
            )

        reference = nodes[0]["mapReferences"]["items"][0]
        self.assertEqual(reference["mapFamily"], "TheIsland")
        self.assertEqual(
            reference["relation"], "WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE"
        )
        self.assertEqual(reference["evidenceCount"], 2)
        self.assertEqual(
            reference["objectPath"], "/Game/Maps/TheIslandSubMaps/TheIsland_WP"
        )
        self.assertEqual(len(reference["evidenceExamples"]), 2)
        self.assertEqual(coverage["filesScanned"], 2)
        self.assertEqual(coverage["matchedNodes"], 1)
        self.assertEqual(coverage["families"], ["TheIsland"])

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required for fast-path parity")
    def test_world_partition_ripgrep_path_matches_fallback_for_prefix_and_utf16(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            external_root = (
                content_root
                / "__ExternalActors__"
                / "Maps"
                / "TheIslandSubMaps"
                / "TheIsland_WP"
            )
            short = self._metal_node()
            short["id"] = "node-short"
            short["objectPath"] = "/Game/Nodes/FoliageType.FoliageType"
            long = self._metal_node()
            long["id"] = "node-long"
            long["objectPath"] = "/Game/Nodes/FoliageType_2.FoliageType_2"
            ascii_actor = external_root / "F" / "1" / "AsciiActor.uasset"
            utf16_actor = external_root / "F" / "2" / "Utf16Actor.uasset"
            ascii_actor.parent.mkdir(parents=True, exist_ok=True)
            utf16_actor.parent.mkdir(parents=True, exist_ok=True)
            ascii_actor.write_bytes(b"prefix /Game/Nodes/FoliageType_2 suffix")
            utf16_actor.write_bytes("/Game/Nodes/FoliageType".encode("utf-16-le"))

            fast, fast_coverage = scan_world_partition_external_actor_references(
                [short, long],
                [external_root],
                content_root=content_root,
                prefer_rg=True,
            )
            fallback, fallback_coverage = scan_world_partition_external_actor_references(
                [short, long],
                [external_root],
                content_root=content_root,
                prefer_rg=False,
            )

        def evidence(rows):
            return {
                node["id"]: [
                    (item["mapFamily"], item["relation"], item["evidenceCount"])
                    for item in node["mapReferences"]["items"]
                ]
                for node in rows
            }

        self.assertEqual(evidence(fast), evidence(fallback))
        self.assertEqual(evidence(fast)["node-short"][0][2], 1)
        self.assertEqual(evidence(fast)["node-long"][0][2], 1)
        self.assertEqual(fast_coverage["filesScanned"], 2)
        self.assertEqual(fast_coverage["filesParsed"], 2)
        self.assertEqual(fallback_coverage["filesParsed"], 2)

    def test_missing_map_roots_are_partial_not_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            missing = content_root / "missing"
            _nodes, direct = scan_direct_map_references(
                [self._metal_node()], [missing], content_root=content_root
            )
            _nodes, pcg = scan_pcg_map_references(
                [self._metal_node()], [missing], content_root=content_root
            )
            _nodes, external = scan_world_partition_external_actor_references(
                [self._metal_node()],
                [missing],
                content_root=content_root,
                prefer_rg=False,
            )

        self.assertEqual(direct["status"], "DIRECT_SCAN_PARTIAL")
        self.assertEqual(pcg["status"], "PCG_BIOME_SCAN_PARTIAL")
        self.assertEqual(
            external["status"], "WORLD_PARTITION_EXTERNAL_ACTOR_SCAN_PARTIAL"
        )
        self.assertEqual((direct["failures"], pcg["failures"], external["failures"]), (1, 1, 1))

    def test_asa_test_map_is_auxiliary_not_a_playable_map_family(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            map_path = (
                content_root
                / "ASA"
                / "CoreBlueprints"
                / "FoliageInteraction"
                / "Maps"
                / "TestMapArea_FoliageInteraction.umap"
            )
            map_path.parent.mkdir(parents=True)
            map_path.write_bytes(b"/Game/Nodes/SM_MetalRock_01_settings")

            nodes, _coverage = scan_direct_map_references(
                [self._metal_node()], [map_path], content_root=content_root
            )

        reference = nodes[0]["mapReferences"]["items"][0]
        self.assertEqual(reference["mapFamily"], "ASA")
        self.assertEqual(reference["mapKind"], "AUXILIARY_MAP_EVIDENCE")
        self.assertEqual(nodes[0]["mapUsage"]["familyCount"], 0)

    def test_map_scan_limits_are_reported_as_partial_and_truncated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            package = b"/Game/Nodes/SM_MetalRock_01_settings"
            map_root = content_root / "Maps" / "Genesis2"
            pcg_root = content_root / "PCG_Biomes"
            external_root = (
                content_root
                / "__ExternalActors__"
                / "Maps"
                / "TheIslandSubMaps"
                / "TheIsland_WP"
            )
            for index in range(2):
                map_path = map_root / f"Map{index}.umap"
                map_path.parent.mkdir(parents=True, exist_ok=True)
                map_path.write_bytes(package)
                pcg_path = pcg_root / "TheIsland" / f"Biome{index}.uasset"
                pcg_path.parent.mkdir(parents=True, exist_ok=True)
                pcg_path.write_bytes(package)
                actor_path = external_root / "F" / str(index) / f"Actor{index}.uasset"
                actor_path.parent.mkdir(parents=True, exist_ok=True)
                actor_path.write_bytes(package)

            _nodes, direct = scan_direct_map_references(
                [self._metal_node()], [map_root], content_root=content_root, max_files=1
            )
            _nodes, pcg = scan_pcg_map_references(
                [self._metal_node()], [pcg_root], content_root=content_root, max_files=1
            )
            _nodes, external = scan_world_partition_external_actor_references(
                [self._metal_node()],
                [external_root],
                content_root=content_root,
                max_files=1,
                prefer_rg=False,
            )

        for coverage in (direct, pcg, external):
            self.assertEqual(coverage["filesDiscovered"], 2)
            self.assertEqual(coverage["filesScanned"], 1)
            self.assertTrue(coverage["truncated"])
            self.assertTrue(coverage["status"].endswith("_PARTIAL"))

    def test_direct_map_scan_reuses_incremental_cache_without_changing_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            map_root = content_root / "Maps" / "Genesis2"
            map_root.mkdir(parents=True)
            (map_root / "Map.umap").write_bytes(
                b"/Game/Nodes/SM_MetalRock_01_settings"
            )
            cache_path = Path(temp_dir) / "map-cache.json"

            first, first_coverage = scan_direct_map_references(
                [self._metal_node()],
                [map_root],
                content_root=content_root,
                cache_path=cache_path,
            )
            second, second_coverage = scan_direct_map_references(
                [self._metal_node()],
                [map_root],
                content_root=content_root,
                cache_path=cache_path,
            )

        self.assertEqual(first[0]["mapReferences"]["items"], second[0]["mapReferences"]["items"])
        self.assertEqual(first_coverage["cache"]["misses"], 1)
        self.assertEqual(second_coverage["cache"]["hits"], 1)
        self.assertEqual(second_coverage["cache"]["misses"], 0)
        self.assertEqual(second_coverage["cache"]["fingerprintPolicy"], "FILE_STAT_SIZE_MTIME_NS")

    def test_component_source_freshness_detects_live_devkit_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "MetalHarvestComponent.uasset"
            source_path.write_bytes(b"current")
            component = {"sourceChain": [str(source_path)]}
            sources = [
                {
                    "path": str(source_path),
                    "sha256": "d" * 64,
                }
            ]

            status = component_source_freshness(component, sources)

        self.assertEqual(status["status"], "STALE_REVISION")
        self.assertEqual(status["checked"], 1)
        self.assertEqual(status["stale"], ["MetalHarvestComponent.uasset"])

    @staticmethod
    def _metal_node():
        return {
            "id": "node-metal",
            "name": "SM_MetalRock_01_settings",
            "objectPath": "/Game/Nodes/SM_MetalRock_01_settings.SM_MetalRock_01_settings",
            "harvestComponent": {
                "status": "CONFIRMED",
                "name": "MetalHarvestComponent_C",
                "packagePath": (
                    "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/MetalHarvestComponent"
                ),
            },
            "resources": {"status": "NOT_INDEXED", "count": None, "items": []},
            "mapReferences": {
                "status": "NOT_INDEXED",
                "count": None,
                "items": [],
                "coverage": {"filesScanned": 0, "roots": []},
            },
            "gaps": [],
        }

    @staticmethod
    def _rank_row(
        creature,
        component,
        score,
        *,
        resource="PrimalItemResource_Metal_C",
        legacy_score=None,
    ):
        return {
            "creature": creature,
            "component": component,
            "componentObjectPath": (
                "/Game/PrimalEarth/CoreBlueprints/HarvestComponents/"
                f"{component}.{component}"
            ),
            "resource": resource,
            "rankingStatus": "RANKED",
            "estimatedYieldPerNode": score,
            "engineComparisonIndex": score if legacy_score is None else legacy_score,
            "attackName": "Attack",
            "scoreBasis": "ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE",
        }


if __name__ == "__main__":
    unittest.main()
