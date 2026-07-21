import tempfile
import unittest
from pathlib import Path


import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_ark_resource_node_catalog import (  # noqa: E402
    _attach_node_thumbnail,
    _build_resource_name_catalog,
    _dataset_revision,
    _discover_node_candidates,
    _extract_node_candidate,
    _evaluation_coverage_summary,
    _indirect_map_reference_status,
    _node_type_discovery_coverage,
    _resource_name_lookup,
    _stratified_limit,
)
from blueprint_translator.resource_nodes import NotFoliageTypeAsset  # noqa: E402


class ResourceNodeCatalogBuilderTests(unittest.TestCase):
    def test_resource_name_catalog_rejects_missing_exact_object_path(self):
        class FakeReader:
            def __init__(self):
                self.effective_defaults_calls = 0

            def effective_defaults(self, path, _class_index):
                self.effective_defaults_calls += 1
                return (
                    [
                        {
                            "name": "DescriptiveNameBase",
                            "value": "Common Mushroom",
                            "confidence": "high",
                        }
                    ],
                    [path],
                )

            def defaults(self, _path):
                return {
                    "variables": {
                        "DescriptiveNameBase": {
                            "value": "Common Mushroom",
                            "confidence": "high",
                        }
                    }
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            decoy = (
                content_root
                / "PrimalEarth"
                / "Resources"
                / "PrimalItemResource_CommonMushroom.uasset"
            )
            decoy.parent.mkdir(parents=True, exist_ok=True)
            decoy.touch()
            reader = FakeReader()
            catalog = _build_resource_name_catalog(
                [
                    {
                        "resourceEntries": [
                            {
                                "resource": "PrimalItemResource_CommonMushroom_C",
                                "resourceObjectPath": (
                                    "/Game/Aberration/Items/"
                                    "PrimalItemResource_CommonMushroom."
                                    "PrimalItemResource_CommonMushroom_C"
                                ),
                            }
                        ]
                    }
                ],
                content_root=content_root,
                candidate_paths=[decoy],
                reader=reader,
            )

        self.assertEqual(catalog["coverage"]["resolved"], 0)
        self.assertEqual(catalog["coverage"]["unresolved"], 1)
        self.assertEqual(
            catalog["failures"][0]["reasonCode"],
            "RESOURCE_ITEM_OBJECT_PATH_NOT_FOUND",
        )
        self.assertEqual(reader.effective_defaults_calls, 0)

    def test_resource_name_catalog_uses_exact_object_path_and_effective_devkit_default(self):
        class FakeReader:
            def effective_defaults(self, path, _class_index):
                name = (
                    "Aggeravic Mushroom"
                    if "Aberration" in path.parts
                    else "Common Mushroom"
                )
                return (
                    [
                        {
                            "name": "DescriptiveNameBase",
                            "value": name,
                            "confidence": "high",
                        }
                    ],
                    [path],
                )

            def defaults(self, path):
                name = (
                    "Aggeravic Mushroom"
                    if "Aberration" in path.parts
                    else "Common Mushroom"
                )
                return {
                    "variables": {
                        "DescriptiveNameBase": {
                            "value": name,
                            "confidence": "high",
                        }
                    }
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            aberration = (
                content_root
                / "Aberration"
                / "Items"
                / "PrimalItemResource_CommonMushroom.uasset"
            )
            primal = (
                content_root
                / "PrimalEarth"
                / "Resources"
                / "PrimalItemResource_CommonMushroom.uasset"
            )
            for path in (aberration, primal):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            components = [
                {
                    "resourceEntries": [
                        {
                            "resource": "PrimalItemResource_CommonMushroom_C",
                            "resourceObjectPath": (
                                "/Game/Aberration/Items/"
                                "PrimalItemResource_CommonMushroom."
                                "PrimalItemResource_CommonMushroom_C"
                            ),
                        }
                    ]
                }
            ]

            catalog = _build_resource_name_catalog(
                components,
                content_root=content_root,
                candidate_paths=[aberration, primal],
                reader=FakeReader(),
            )

        self.assertEqual(catalog["coverage"]["requested"], 1)
        self.assertEqual(catalog["coverage"]["resolved"], 1)
        self.assertEqual(catalog["items"][0]["displayName"], "Aggeravic Mushroom")
        self.assertEqual(catalog["items"][0]["propertyName"], "DescriptiveNameBase")
        lookup = _resource_name_lookup(catalog)
        self.assertEqual(
            lookup[
                (
                    "/game/aberration/items/"
                    "primalitemresource_commonmushroom."
                    "primalitemresource_commonmushroom_c"
                )
            ],
            "Aggeravic Mushroom",
        )
        self.assertNotIn("primalitemresource_commonmushroom_c", lookup)

    def test_indirect_map_status_does_not_claim_skipped_scanners_were_indexed(self):
        self.assertEqual(
            _indirect_map_reference_status(
                {"status": "NOT_INDEXED"}, {"status": "NOT_INDEXED"}
            ),
            "NOT_INDEXED",
        )
        self.assertEqual(
            _indirect_map_reference_status(
                {"status": "PCG_BIOME_SCAN_COMPLETE"},
                {"status": "NOT_INDEXED"},
            ),
            "PCG_INDEXED_WORLD_PARTITION_NOT_INDEXED",
        )
        self.assertEqual(
            _indirect_map_reference_status(
                {"status": "NOT_INDEXED"},
                {"status": "WORLD_PARTITION_EXTERNAL_ACTOR_SCAN_COMPLETE"},
            ),
            "WORLD_PARTITION_INDEXED_PCG_NOT_INDEXED",
        )
    def test_evaluation_coverage_does_not_replace_node_candidate_discovery(self):
        summary = _evaluation_coverage_summary(
            {
                "coverage": {
                    "candidateDiscovery": {"candidatesDiscovered": 1422},
                    "creatureAssetsCataloged": 1322,
                    "speciesCataloged": 254,
                }
            }
        )

        self.assertEqual(summary["creatureCandidatesDiscovered"], 1422)
        self.assertEqual(summary["creatureAssetsCataloged"], 1322)
        self.assertNotIn("candidateDiscovery", summary)

    def test_catalog_revision_changes_with_map_evidence_and_thumbnail_identity(self):
        base = {
            "objectPath": "/Game/Nodes/Rock.Rock",
            "evidence": {"sourceSha256": "1" * 64},
            "mapReferences": {"items": []},
            "image": {"status": "AVAILABLE", "sha256": "2" * 64},
        }
        ranking = {"datasetRevision": "3" * 64}
        evaluation = {"dataset": {"revision": "5" * 64}}
        first = _dataset_revision([base], ranking, evaluation)

        with_map = {
            **base,
            "mapReferences": {
                "items": [
                    {
                        "objectPath": "/Game/Maps/Island/Island",
                        "relation": "DIRECT_SERIALIZED_PACKAGE_REFERENCE",
                        "evidenceStatus": "CONFIRMED",
                    }
                ]
            },
        }
        with_image_change = {
            **base,
            "image": {"status": "AVAILABLE", "sha256": "4" * 64},
        }

        self.assertNotEqual(first, _dataset_revision([with_map], ranking, evaluation))
        self.assertNotEqual(first, _dataset_revision([with_image_change], ranking, evaluation))
        changed_evaluation = {"dataset": {"revision": "6" * 64}}
        self.assertNotEqual(first, _dataset_revision([base], ranking, changed_evaluation))

    def test_node_thumbnail_attachment_is_explicit_when_enabled_or_skipped(self):
        node = {"id": "node-1", "image": {"status": "NOT_EXTRACTED"}}

        enabled = _attach_node_thumbnail(
            dict(node),
            Path("Node.uasset"),
            Path("images"),
            skip_images=False,
            cacher=lambda _path, _root: {
                "status": "AVAILABLE",
                "sha256": "a" * 64,
                "url": f"/api/harvest/images/{'a' * 64}.jpg",
            },
        )
        skipped = _attach_node_thumbnail(
            dict(node),
            Path("Node.uasset"),
            Path("images"),
            skip_images=True,
        )

        self.assertEqual(enabled["image"]["status"], "AVAILABLE")
        self.assertEqual(skipped["image"]["status"], "NOT_INDEXED")
        self.assertEqual(skipped["image"]["reasonCode"], "IMAGE_EXTRACTION_DISABLED")

    def test_non_foliage_settings_asset_is_a_skipped_candidate_not_a_decode_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir)
            asset = content_root / "Decor_settings.uasset"
            asset.touch()

            def reject(_path, _content_root):
                raise NotFoliageTypeAsset("not foliage")

            result = _extract_node_candidate(
                asset,
                content_root,
                extractor=reject,
            )

        self.assertEqual(result["candidateStatus"], "NOT_FOLIAGE_TYPE")
        self.assertEqual(result["objectPath"], "/Game/Decor_settings")

    def test_candidate_discovery_walks_once_and_filters_resource_node_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = [
                root / "A" / "Rock_settings.uasset",
                root / "B" / "CustomFoliageType.uasset",
                # Current DevKit contains FA_LumaA_02, a resource-bearing
                # FoliageType_Actor missed by the previous two filename globs.
                root / "C" / "FA_LumaA_02.uasset",
            ]
            ignored = [
                root / "A" / "Rock.uasset",
                root / "B" / "CustomFoliageType.txt",
            ]
            for path in [*expected, *ignored]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            result, backend = _discover_node_candidates(root, prefer_rg=False)

        self.assertEqual(result, sorted(path.resolve() for path in expected))
        self.assertEqual(backend, "OS_WALK")

    def test_node_type_coverage_separates_definitions_from_runtime_or_placement_assets(self):
        coverage = _node_type_discovery_coverage(
            [
                {"nodeType": "FOLIAGE", "assetClass": "FoliageType_InstancedStaticMesh"},
                {"nodeType": "FOLIAGE_ACTOR", "assetClass": "FoliageType_Actor"},
            ]
        )

        self.assertEqual(
            coverage["supportedDefinitionClasses"]["FoliageType_Actor"]["decoded"],
            1,
        )
        self.assertEqual(
            coverage["nonDefinitionAssetModels"]["StaticMesh"]["reasonCode"],
            "GEOMETRY_JOINED_THROUGH_FOLIAGE_DEFINITION",
        )
        self.assertEqual(
            coverage["nonDefinitionAssetModels"]["PCG"]["reasonCode"],
            "PLACEMENT_EVIDENCE_NOT_NODE_DEFINITION",
        )
        self.assertFalse(coverage["claimsAllNodeDefinitionClasses"])

    def test_bounded_discovery_is_stratified_across_top_level_content_families(self):
        content_root = Path("C:/Content")
        paths = [
            content_root / "Aberration" / f"A{index}_settings.uasset"
            for index in range(4)
        ] + [
            content_root / "Genesis2" / f"G{index}_settings.uasset"
            for index in range(4)
        ] + [
            content_root / "PrimalEarth" / f"P{index}_settings.uasset"
            for index in range(4)
        ]

        selected = _stratified_limit(paths, content_root=content_root, limit=3)

        self.assertEqual(
            {path.relative_to(content_root).parts[0] for path in selected},
            {"Aberration", "Genesis2", "PrimalEarth"},
        )
        self.assertEqual(len(selected), 3)


if __name__ == "__main__":
    unittest.main()
