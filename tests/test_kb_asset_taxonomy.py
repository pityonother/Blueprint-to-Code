from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.kb_asset_taxonomy import (  # noqa: E402
    TaxonomyBuildError,
    build_taxonomy,
)
from blueprint_translator.kb_asset_taxonomy_sampling import (  # noqa: E402
    _candidate_rank,
    _keep_smallest,
    _sample_targets,
    _select_samples,
)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _asset(
    package: str,
    asset_class: str,
    *,
    name: str | None = None,
    object_suffix: str | None = None,
    tags: dict[str, str] | None = None,
) -> dict[str, object]:
    leaf = package.rsplit("/", 1)[-1]
    asset_name = name or leaf
    suffix = object_suffix or asset_name
    return {
        "schema": "ark.kb.registry-asset.v1",
        "object_path": f"{package}.{suffix}",
        "package_name": package,
        "package_path": package.rsplit("/", 1)[0],
        "asset_name": asset_name,
        "asset_class_path": asset_class,
        "identity_status": "CONFIRMED",
        "identity_confidence": "HIGH",
        "identity_source_kind": "asset_registry",
        "package_flags": 0,
        "tags": tags or {},
    }


def _write_registry(
    root: Path,
    rows: list[dict[str, object]],
    *,
    with_manifest: bool = True,
) -> tuple[Path, Path | None]:
    generation = root / "generations" / "fixture-generation"
    generation.mkdir(parents=True)
    assets_path = generation / "registry_assets.jsonl"
    payload = b"".join(_canonical_bytes(row) for row in rows)
    assets_path.write_bytes(payload)
    if not with_manifest:
        return assets_path, None
    manifest = {
        "schema": "ark.kb.registry-snapshot.v2",
        "status": "COMPLETE",
        "generation_id": "fixture-generation",
        "asset_count": len(rows),
        "package_count": len({str(row["package_name"]) for row in rows}),
        "dependencies_enabled": False,
        "files": {
            "assets": {
                "path": "generations/fixture-generation/registry_assets.jsonl",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "lines": len(rows),
                "record_count": len(rows),
                "row_schema": "ark.kb.registry-asset.v1",
            }
        },
    }
    manifest_path = root / "registry_manifest.json"
    manifest_path.write_bytes(_canonical_bytes(manifest))
    return assets_path, manifest_path


def _write_hierarchy(root: Path, registry_sha256: str | None = None) -> Path:
    generation = root / "generations" / "hierarchy-generation"
    generation.mkdir(parents=True)
    rows = [
        {
            "schema": "ark.kb.class-hierarchy-row.v1",
            "class_path": "/Game/Buffs/Buff_Test.Buff_Test_C",
            "super_class_path": "/Script/ShooterGame.PrimalBuff",
            "parent_status": "CONFIRMED",
            "interfaces_status": "NOT_RECOVERED",
            "source": "asset_registry_parent_tag",
            "status": "PARTIAL",
            "confidence": "MEDIUM",
            "interfaces": [],
            "is_native": False,
        },
        {
            "schema": "ark.kb.class-hierarchy-row.v1",
            "class_path": "/Script/ShooterGame.PrimalBuff",
            "super_class_path": "/Script/Engine.Actor",
            "parent_status": "CONFIRMED",
            "interfaces_status": "NOT_RECOVERED",
            "source": "asset_registry_class_ancestry",
            "status": "PARTIAL",
            "confidence": "MEDIUM",
            "interfaces": [],
            "is_native": True,
        },
    ]
    payload = b"".join(_canonical_bytes(row) for row in rows)
    classes_path = generation / "class_hierarchy.jsonl"
    classes_path.write_bytes(payload)
    manifest = {
        "schema": "ark.kb.class-hierarchy-snapshot.v2",
        "status": "COMPLETE",
        "generation_id": "hierarchy-generation",
        "producer": {
            "runtime_identity": {
                "devkit_build_id": (
                    f"registry-{registry_sha256}"
                    if registry_sha256 is not None
                    else "UNSPECIFIED"
                )
            }
        },
        "outputs": {
            "classes": "generations/hierarchy-generation/class_hierarchy.jsonl"
        },
        "files": {
            "classes": {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "record_count": len(rows),
            }
        },
    }
    manifest_path = root / "class_hierarchy_manifest.json"
    manifest_path.write_bytes(_canonical_bytes(manifest))
    return manifest_path


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


class AssetTaxonomyTests(unittest.TestCase):
    def test_exact_registry_class_wins_and_mixed_package_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                _asset(
                    "/Game/Test/Buff_LooksLikeItem",
                    "/Script/Engine.DataTable",
                ),
                _asset(
                    "/Game/Test/Mixed",
                    "/Script/Engine.DataTable",
                    name="Mixed",
                ),
                _asset(
                    "/Game/Test/Mixed",
                    "/Script/Engine.Texture2D",
                    name="Thumbnail",
                    object_suffix="Thumbnail",
                ),
            ]
            assets, manifest = _write_registry(root / "registry", rows)
            output = root / "taxonomy"

            build_taxonomy(
                assets,
                output,
                source_manifest=manifest,
                sample_size=3,
                seed="fixture",
            )

            packages = {
                row["packageName"]: row
                for row in _read_jsonl(output / "package_taxonomy.jsonl")
            }
            deceptive = packages["/Game/Test/Buff_LooksLikeItem"]
            self.assertEqual(deceptive["primaryTechnicalFamily"], "DATA")
            self.assertEqual(deceptive["primaryTechnicalKind"], "DATA_TABLE")
            self.assertEqual(
                deceptive["objects"][0]["classificationEvidence"][0]["sourceField"],
                "asset_class_path",
            )
            mixed = packages["/Game/Test/Mixed"]
            self.assertEqual(mixed["packageShape"], "MULTI_CATEGORY")
            self.assertTrue(mixed["isMixedPackage"])
            self.assertEqual(
                {
                    (item["family"], item["kind"])
                    for item in mixed["technicalCategories"]
                },
                {("DATA", "DATA_TABLE"), ("VISUAL", "TEXTURE_2D")},
            )
            summary = json.loads((output / "taxonomy_manifest.json").read_text("utf-8"))
            self.assertEqual(summary["counts"]["technicallyKnownAssetObjects"], 3)
            self.assertEqual(summary["counts"]["technicallyUnknownAssetObjects"], 0)

    def test_curated_exact_allowlist_covers_actor_and_leaves_ark_native_unknown(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                _asset("/Game/Placed/Actor", "/Script/Engine.StaticMeshActor"),
                _asset("/Game/Zones/Zone", "/Script/ShooterGame.NPCZoneVolume"),
                _asset(
                    "/DinoDefense/__ExternalActors__/Maps/Defense/0/AA/HASH",
                    "/Script/Engine.StaticMeshActor",
                ),
            ]
            assets, _ = _write_registry(root / "registry", rows, with_manifest=False)
            output = root / "taxonomy"

            build_taxonomy(assets, output, sample_size=3)

            packages = {
                row["packageName"]: row
                for row in _read_jsonl(output / "package_taxonomy.jsonl")
            }
            self.assertEqual(
                packages["/Game/Placed/Actor"]["primaryTechnicalFamily"], "WORLD"
            )
            self.assertEqual(
                packages["/Game/Placed/Actor"]["primaryTechnicalKind"], "ACTOR"
            )
            self.assertEqual(
                packages["/Game/Zones/Zone"]["primaryTechnicalFamily"], "UNKNOWN"
            )
            self.assertEqual(
                packages["/Game/Zones/Zone"]["primaryTechnicalKind"],
                "UNMAPPED_EXACT_CLASS",
            )
            plugin_external = packages[
                "/DinoDefense/__ExternalActors__/Maps/Defense/0/AA/HASH"
            ]
            self.assertEqual(
                plugin_external["layoutKind"], "WORLD_PARTITION_EXTERNAL_ACTOR"
            )
            self.assertEqual(
                plugin_external["ownerWorldPath"], "/DinoDefense/Maps/Defense"
            )

    def test_hierarchy_adds_confirmed_semantic_anchor_and_query_dimensions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = "/Game/Buffs/Buff_Test.Buff_Test_C"
            rows = [
                _asset(
                    "/Game/Buffs/Buff_Test",
                    "/Script/Engine.Blueprint",
                    tags={
                        "BlueprintType": "BPTYPE_Normal",
                        "GeneratedClass": (
                            f"/Script/Engine.BlueprintGeneratedClass'{generated}'"
                        ),
                        "ParentClass": (
                            "/Script/CoreUObject.Class'/Script/ShooterGame.PrimalBuff'"
                        ),
                    },
                ),
                _asset(
                    "/Game/__ExternalActors__/Maps/WorldA/0/AA/ActorPackage",
                    "/Script/CoreUObject.Object",
                ),
                _asset(
                    "/Game/__ExternalObjects__/Maps/WorldA/0/AA/FolderPackage",
                    "/Script/CoreUObject.Object",
                ),
                _asset(
                    "/ASBExportGun/Data/Settings",
                    "/Script/Engine.DataAsset",
                ),
            ]
            assets, manifest = _write_registry(root / "registry", rows)
            registry_sha = json.loads(manifest.read_text("utf-8"))["files"]["assets"][
                "sha256"
            ]
            hierarchy = _write_hierarchy(root / "hierarchy", registry_sha)
            output = root / "taxonomy"

            build_taxonomy(
                assets,
                output,
                source_manifest=manifest,
                class_hierarchy_manifest=hierarchy,
                sample_size=4,
                seed="fixture",
            )

            packages = {
                row["packageName"]: row
                for row in _read_jsonl(output / "package_taxonomy.jsonl")
            }
            buff = packages["/Game/Buffs/Buff_Test"]
            self.assertEqual(
                buff["confirmedSemanticAnchors"][0]["role"], "GAMEPLAY_BUFF"
            )
            self.assertEqual(buff["confirmedSemanticAnchors"][0]["status"], "CONFIRMED")
            self.assertIn("REGISTRY_METADATA", buff["availableCapabilities"])
            self.assertNotIn("BLUEPRINT_GRAPH", buff["availableCapabilities"])
            self.assertIn("BLUEPRINT_GRAPH", buff["acquisitionRoutes"])
            self.assertIn("CLASS_ANCESTRY", buff["acquisitionRoutes"])
            self.assertEqual(buff["capabilityStatus"]["blueprintGraph"], "NOT_CAPTURED")
            self.assertEqual(
                buff["capabilityStatus"]["dependenciesAndReferences"],
                "NOT_REQUESTED",
            )
            external = packages[
                "/Game/__ExternalActors__/Maps/WorldA/0/AA/ActorPackage"
            ]
            self.assertEqual(external["layoutKind"], "WORLD_PARTITION_EXTERNAL_ACTOR")
            self.assertEqual(external["sourceScope"], "ARK_PROJECT_CONTENT")
            self.assertEqual(external["placementLayer"], "EXTERNAL_ACTOR_PACKAGE")
            self.assertEqual(
                external["ownerWorldPathStatus"], "PATH_CONVENTION_CANDIDATE"
            )
            self.assertIn("WORLD_STRUCTURE", external["acquisitionRoutes"])
            self.assertEqual(
                external["capabilityStatus"]["worldStructure"],
                "WORLD_PARTITION_PACKAGE_IDENTITY_ONLY",
            )
            external_object = packages[
                "/Game/__ExternalObjects__/Maps/WorldA/0/AA/FolderPackage"
            ]
            self.assertIn("WORLD_STRUCTURE", external_object["acquisitionRoutes"])
            self.assertEqual(
                external_object["capabilityStatus"]["worldStructure"],
                "WORLD_PARTITION_FOLDER_ORGANIZATION_ONLY",
            )
            plugin = packages["/ASBExportGun/Data/Settings"]
            self.assertEqual(plugin["sourceScope"], "MOUNTED_PLUGIN_CONTENT")
            self.assertEqual(plugin["mountPoint"], "/ASBExportGun")

            summary = json.loads((output / "taxonomy_manifest.json").read_text("utf-8"))
            self.assertEqual(
                summary["authorityScope"], "REGISTRY_TECHNICAL_WITH_CLASS_ANCESTRY"
            )
            self.assertEqual(
                summary["evidenceCoverage"]["dependencyTopology"], "NOT_REQUESTED"
            )
            self.assertEqual(
                summary["evidenceCoverage"]["assetContentDeepRead"], "NOT_RUN"
            )
            self.assertFalse(
                summary["ruleset"]["nameOrFolderConfirmedSemanticInference"]
            )
            self.assertTrue(summary["ruleset"]["nameOrFolderCandidateRecall"])

    def test_build_and_sampling_are_byte_deterministic_across_input_order(self) -> None:
        rows = [
            _asset("/Game/Data/One", "/Script/Engine.DataTable"),
            _asset("/Game/World/One", "/Script/Engine.World"),
            _asset("/Engine/Visual/One", "/Script/Engine.Texture2D"),
            _asset("/Plugin/UI/One", "/Script/UMGEditor.WidgetBlueprint"),
            _asset("/Plugin/Unknown/One", "/Script/Fixture.UnknownType"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets_a, _ = _write_registry(
                root / "registry-a", rows, with_manifest=False
            )
            assets_b, _ = _write_registry(
                root / "registry-b", list(reversed(rows)), with_manifest=False
            )
            output_a = root / "out-a"
            output_b = root / "out-b"

            build_taxonomy(assets_a, output_a, sample_size=4, seed="stable")
            build_taxonomy(assets_b, output_b, sample_size=4, seed="stable")

            for name in (
                "package_taxonomy.jsonl",
                "unknown_packages.jsonl",
                "taxonomy_manifest.json",
                "sample_manifest.json",
                "sample_review_zh.md",
                "taxonomy_report_zh.md",
            ):
                self.assertEqual(
                    (output_a / name).read_bytes(), (output_b / name).read_bytes()
                )
            sample = json.loads((output_a / "sample_manifest.json").read_text("utf-8"))
            self.assertEqual(sample["requestedSampleSize"], 4)
            self.assertEqual(sample["actualSampleSize"], 4)
            self.assertEqual(
                sample["selectionAlgorithm"],
                "quota-first-weighted-greedy-coverage/v5",
            )
            self.assertEqual(
                sample["samplerPolicy"]["version"],
                "ark-asset-taxonomy-sampler-policy/v2",
            )
            self.assertRegex(sample["samplerPolicy"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreaterEqual(sample["coverage"]["coveredTargets"], 4)
            self.assertTrue(all(item["selectionReasons"] for item in sample["samples"]))
            review = (output_a / "sample_review_zh.md").read_text("utf-8")
            for index in range(1, 5):
                self.assertIn(f"| {index} |", review)
            self.assertIn("只是一条待采集路线", review)
            report = (output_a / "taxonomy_report_zh.md").read_text("utf-8")
            self.assertIn("originArea：高频前", report)
            self.assertIn("不代表逐 originArea 全覆盖", report)
            self.assertIn("DEGRADED 不证明不存在其他可行组合", report)
            self.assertFalse(
                sample["samplingConstraints"]["quotaRepairSearch"]["exhaustive"]
            )

    def test_manifest_binding_fails_closed_and_leaves_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets, manifest = _write_registry(
                root / "registry",
                [_asset("/Game/Data/One", "/Script/Engine.DataTable")],
            )
            payload = json.loads(manifest.read_text("utf-8"))
            payload["files"]["assets"]["sha256"] = "0" * 64
            manifest.write_bytes(_canonical_bytes(payload))
            output = root / "taxonomy"

            with self.assertRaisesRegex(
                TaxonomyBuildError, "SOURCE_ASSETS_SHA256_MISMATCH"
            ):
                build_taxonomy(assets, output, source_manifest=manifest)
            self.assertFalse(output.exists())

    def test_duplicate_identity_conflict_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _asset("/Game/Data/One", "/Script/Engine.DataTable")
            second = dict(first)
            second["asset_class_path"] = "/Script/Engine.Texture2D"
            assets, _ = _write_registry(
                root / "registry", [first, second], with_manifest=False
            )
            output = root / "taxonomy"

            with self.assertRaisesRegex(
                TaxonomyBuildError, "DUPLICATE_OBJECT_IDENTITY_CONFLICT"
            ):
                build_taxonomy(assets, output)
            self.assertFalse(output.exists())

    def test_duplicate_identity_with_different_raw_source_content_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _asset("/Game/Data/One", "/Script/Engine.DataTable")
            second = dict(first)
            first["producer_extension"] = "first"
            second["producer_extension"] = "second"
            assets, _ = _write_registry(
                root / "registry", [first, second], with_manifest=False
            )
            output = root / "taxonomy"

            with self.assertRaisesRegex(
                TaxonomyBuildError, "DUPLICATE_OBJECT_IDENTITY_CONFLICT"
            ):
                build_taxonomy(assets, output)
            self.assertFalse(output.exists())

    def test_unbound_input_keeps_confirmed_unknown_and_low_candidate_separate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets, _ = _write_registry(
                root / "registry",
                [_asset("/Game/Buffs/LooksSemantic", "/Script/Fixture.UnknownType")],
                with_manifest=False,
            )
            output = root / "taxonomy"

            build_taxonomy(assets, output, sample_size=1)

            package = _read_jsonl(output / "package_taxonomy.jsonl")[0]
            self.assertEqual(package["primaryTechnicalFamily"], "UNKNOWN")
            self.assertEqual(package["classificationConfidence"], "UNKNOWN")
            self.assertEqual(package["confirmedSemanticAnchors"], [])
            self.assertEqual(package["semanticStatus"], "CANDIDATE")
            self.assertEqual(
                package["semanticCandidates"][0]["role"],
                "BUFF_ASSOCIATED_CONTENT",
            )
            self.assertEqual(package["semanticCandidates"][0]["confidence"], "LOW")
            manifest = json.loads(
                (output / "taxonomy_manifest.json").read_text("utf-8")
            )
            self.assertEqual(manifest["sourceBinding"]["status"], "SELF_HASHED_UNBOUND")
            self.assertFalse(manifest["publicationEligible"])
            self.assertEqual(manifest["counts"]["technicallyUnknownAssetObjects"], 1)

    def test_misleading_names_never_become_confirmed_gameplay_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                _asset("/Game/Genesis/Maps/Genesis_WP", "/Script/Engine.World"),
                _asset(
                    "/Game/Weapons/Visual/SM_Dynamite",
                    "/Script/Engine.StaticMesh",
                ),
                _asset(
                    "/Game/Weapons/Audio/Bow_Charge_Cue",
                    "/Script/Engine.SoundCue",
                ),
            ]
            assets, _ = _write_registry(root / "registry", rows, with_manifest=False)
            output = root / "taxonomy"

            build_taxonomy(assets, output, sample_size=3)

            packages = _read_jsonl(output / "package_taxonomy.jsonl")
            self.assertTrue(
                all(not row["confirmedSemanticAnchors"] for row in packages)
            )
            genesis = next(
                row for row in packages if row["packageName"].endswith("Genesis_WP")
            )
            self.assertFalse(
                any("GENET" in item["role"] for item in genesis["semanticCandidates"])
            )
            self.assertFalse(
                any(
                    item["role"] == "MAP_ASSOCIATED_CONTENT"
                    for item in genesis["semanticCandidates"]
                )
            )
            for package in packages:
                self.assertFalse(
                    any(
                        item["role"] == "WEAPON"
                        for item in package["confirmedSemanticAnchors"]
                    )
                )
                self.assertTrue(
                    all(
                        "cannotPromoteTo" not in item
                        for item in package["semanticCandidates"]
                    )
                )

    def test_unbound_hierarchy_can_only_add_candidate_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = "/Game/Buffs/Buff_Test.Buff_Test_C"
            assets, manifest = _write_registry(
                root / "registry",
                [
                    _asset(
                        "/Game/Buffs/Buff_Test",
                        "/Script/Engine.Blueprint",
                        tags={
                            "BlueprintType": "BPTYPE_Normal",
                            "GeneratedClass": (
                                f"/Script/Engine.BlueprintGeneratedClass'{generated}'"
                            ),
                        },
                    )
                ],
            )
            hierarchy = _write_hierarchy(root / "hierarchy")
            output = root / "taxonomy"

            build_taxonomy(
                assets,
                output,
                source_manifest=manifest,
                class_hierarchy_manifest=hierarchy,
                sample_size=1,
            )

            package = _read_jsonl(output / "package_taxonomy.jsonl")[0]
            self.assertEqual(package["confirmedSemanticAnchors"], [])
            ancestry_candidate = next(
                item
                for item in package["semanticCandidates"]
                if item["role"] == "GAMEPLAY_BUFF"
            )
            self.assertEqual(ancestry_candidate["status"], "CANDIDATE")
            self.assertEqual(ancestry_candidate["confidence"], "MEDIUM")
            taxonomy_manifest = json.loads(
                (output / "taxonomy_manifest.json").read_text("utf-8")
            )
            self.assertEqual(
                taxonomy_manifest["classHierarchyBinding"]["registryBinding"],
                "UNBOUND_OR_MISMATCH",
            )
            self.assertFalse(taxonomy_manifest["publicationEligible"])
            self.assertEqual(
                taxonomy_manifest["authorityScope"],
                "REGISTRY_TECHNICAL_WITH_UNBOUND_HIERARCHY_CANDIDATES",
            )

    def test_world_partition_is_clustered_and_capped_in_review_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                _asset(
                    f"/Game/__ExternalActors__/Maps/WorldA/{index % 3}/AA/HASH{index}",
                    "/Script/Engine.StaticMeshActor",
                    name=f"StaticMeshActor_{index}",
                )
                for index in range(30)
            ]
            rows.extend(
                _asset(
                    f"/Game/Definitions/Definition{index}", "/Script/Engine.DataTable"
                )
                for index in range(10)
            )
            assets, _ = _write_registry(root / "registry", rows, with_manifest=False)
            output = root / "taxonomy"

            build_taxonomy(assets, output, sample_size=10, seed="wp-cap")

            sample = json.loads((output / "sample_manifest.json").read_text("utf-8"))
            external = [
                item
                for item in sample["samples"]
                if item["dimensions"]["samplingLayer"]
                == "WORLD_PARTITION_EXTERNAL_ACTOR"
            ]
            self.assertLessEqual(len(external), 2)
            self.assertEqual(
                sample["samplingConstraints"]["worldPartitionExternalActorMax"], 2
            )
            self.assertEqual(
                len({item["dimensions"]["sampleClusterKey"] for item in external}),
                len(external),
            )
            target_populations = sample["coverage"]["targetPopulations"]
            self.assertTrue(
                any(
                    key.startswith("originAreaCandidate=") for key in target_populations
                )
            )
            self.assertTrue(
                any(key.startswith("capabilityStatus.") for key in target_populations)
            )

    def test_sample_enforces_high_level_category_and_unknown_minimums(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                _asset(f"/Game/Data/Table{index}", "/Script/Engine.DataTable")
                for index in range(100)
            ]
            rows.extend(
                _asset(f"/Game/Visual/Texture{index}", "/Script/Engine.Texture2D")
                for index in range(100)
            )
            rows.extend(
                _asset(
                    f"/Game/Unknown/Asset{index}",
                    f"/Script/ShooterGame.UnmappedFixture{index}",
                )
                for index in range(20)
            )
            assets, _ = _write_registry(root / "registry", rows, with_manifest=False)
            output = root / "taxonomy"

            build_taxonomy(assets, output, sample_size=60, seed="category-minimums")

            sample = json.loads((output / "sample_manifest.json").read_text("utf-8"))
            family_counts: dict[str, int] = {}
            for item in sample["samples"]:
                family = item["dimensions"]["technicalFamily"]
                family_counts[family] = family_counts.get(family, 0) + 1
            self.assertGreaterEqual(family_counts["DATA"], 2)
            self.assertGreaterEqual(family_counts["VISUAL"], 2)
            self.assertGreaterEqual(family_counts["UNKNOWN"], 16)
            constraints = sample["samplingConstraints"]
            self.assertEqual(constraints["technicallyUnknownMinimum"], 16)
            self.assertEqual(constraints["unmetQuotaTargets"], [])
            target_populations = sample["coverage"]["targetPopulations"]
            self.assertTrue(
                any(
                    key.startswith("originAreaCandidate=") for key in target_populations
                )
            )
            self.assertTrue(
                any(key.startswith("unknownClassBucket=") for key in target_populations)
            )
            uncovered = sample["coverage"]["uncoveredTargets"]
            self.assertFalse(
                any(
                    target.startswith(
                        ("technicalKind=", "candidateSemanticRole=", "packageShape=")
                    )
                    for target in uncovered
                )
            )

    def test_unknown_minimum_has_a_deep_candidate_pool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                _asset(
                    f"/Game/Unknown/Repeated{index}",
                    "/Script/ShooterGame.OneRepeatedUnknownClass",
                )
                for index in range(200)
            ]
            rows.extend(
                _asset(f"/Game/Data/Table{index}", "/Script/Engine.DataTable")
                for index in range(200)
            )
            assets, _ = _write_registry(root / "registry", rows, with_manifest=False)
            output = root / "taxonomy"

            build_taxonomy(assets, output, sample_size=120, seed="unknown-depth")

            sample = json.loads((output / "sample_manifest.json").read_text("utf-8"))
            unknown_count = sum(
                item["dimensions"]["classificationConfidence"] == "UNKNOWN"
                for item in sample["samples"]
            )
            self.assertGreaterEqual(unknown_count, 16)
            self.assertEqual(sample["samplingConstraints"]["unmetQuotaTargets"], [])

    def test_large_review_sample_meets_world_partition_minimums(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                _asset(
                    f"/Game/__ExternalActors__/Maps/World{index}/0/AA/Actor{index}",
                    "/Script/Engine.StaticMeshActor",
                )
                for index in range(100)
            ]
            rows.extend(
                _asset(
                    f"/Game/__ExternalObjects__/Maps/World{index}/0/AA/Folder{index}",
                    "/Script/Engine.ActorFolder",
                )
                for index in range(50)
            )
            rows.extend(
                _asset(
                    f"/Game/Definitions/Definition{index}", "/Script/Engine.DataTable"
                )
                for index in range(100)
            )
            assets, _ = _write_registry(root / "registry", rows, with_manifest=False)
            output = root / "taxonomy"

            build_taxonomy(assets, output, sample_size=120, seed="wp-minimums")

            sample = json.loads((output / "sample_manifest.json").read_text("utf-8"))
            layers = {
                layer: sum(
                    item["dimensions"]["samplingLayer"] == layer
                    for item in sample["samples"]
                )
                for layer in (
                    "WORLD_PARTITION_EXTERNAL_ACTOR",
                    "WORLD_PARTITION_EXTERNAL_OBJECT",
                )
            }
            self.assertGreaterEqual(layers["WORLD_PARTITION_EXTERNAL_ACTOR"], 18)
            self.assertGreaterEqual(layers["WORLD_PARTITION_EXTERNAL_OBJECT"], 4)
            self.assertEqual(sample["samplingConstraints"]["unmetQuotaTargets"], [])
            self.assertEqual(sample["status"], "COMPLETE")

    def test_world_partition_minimum_is_clamped_to_distinct_cluster_capacity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [
                _asset(
                    f"/Game/__ExternalActors__/Maps/SharedWorld/0/AA/Actor{index}",
                    "/Script/Engine.StaticMeshActor",
                )
                for index in range(30)
            ]
            rows.extend(
                _asset(f"/Game/Data/Table{index}", "/Script/Engine.DataTable")
                for index in range(20)
            )
            assets, _ = _write_registry(root / "registry", rows, with_manifest=False)
            output = root / "taxonomy"

            build_taxonomy(assets, output, sample_size=10, seed="wp-one-cluster")

            sample = json.loads((output / "sample_manifest.json").read_text("utf-8"))
            constraints = sample["samplingConstraints"]
            actor_count = sum(
                item["dimensions"]["samplingLayer"] == "WORLD_PARTITION_EXTERNAL_ACTOR"
                for item in sample["samples"]
            )
            self.assertEqual(constraints["worldPartitionExternalActorRequestedMin"], 2)
            self.assertEqual(
                constraints["worldPartitionExternalActorDistinctClusterCount"],
                1,
            )
            self.assertEqual(
                constraints["worldPartitionExternalActorRetainedDistinctClusterCount"],
                1,
            )
            self.assertEqual(constraints["worldPartitionExternalActorMin"], 1)
            self.assertEqual(actor_count, 1)
            self.assertEqual(constraints["unmetQuotaTargets"], [])
            self.assertEqual(sample["status"], "COMPLETE")

    def test_quota_multicover_avoids_rich_candidate_dead_end(self) -> None:
        def candidate(
            name: str,
            family: str,
            role: str,
            *,
            extra_coverage: bool = False,
        ) -> dict[str, object]:
            return {
                "packageName": f"/Game/Common/{name}",
                "profileFingerprint": hashlib.sha256(name.encode()).hexdigest(),
                "primaryTechnicalFamily": family,
                "primaryTechnicalKind": "COMMON",
                "sourceScope": "ARK_PROJECT_CONTENT",
                "mountPoint": "/Game",
                "storageArea": "/Game/Common",
                "originArea": "/Game/Common",
                "originAreaStatus": "PACKAGE_PATH",
                "ownerWorldPath": "",
                "ownerWorldPathStatus": "NOT_APPLICABLE",
                "layoutKind": "STANDARD_PACKAGE",
                "packageShape": "SINGLE_OBJECT",
                "classificationConfidence": "HIGH",
                "semanticStatus": "CONFIRMED",
                "samplingLayer": "DEFINITION_OR_SUPPORT",
                "sampleClusterKey": f"/Game/Common/{name}",
                "objects": [],
                "confirmedSemanticAnchors": [{"role": role}],
                "semanticCandidates": [],
                "availableCapabilities": (
                    ["BASE", "EXTRA"] if extra_coverage else ["BASE"]
                ),
                "acquisitionRoutes": ["EXTRA"] if extra_coverage else [],
                "capabilityStatus": {
                    "identity": "CURRENT",
                    **({"extra": "EXTRA"} if extra_coverage else {}),
                },
            }

        rows = [
            candidate("P1_A_X_Rich", "A", "X", extra_coverage=True),
            candidate("P2_A_Y", "A", "Y"),
            candidate("P3_B_X", "B", "X"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            taxonomy_path = Path(temporary) / "taxonomy.jsonl"
            taxonomy_path.write_bytes(b"".join(_canonical_bytes(row) for row in rows))
            target_counts: Counter[str] = Counter()
            target_heaps: dict[str, list[tuple[int, str]]] = defaultdict(list)
            global_heap: list[tuple[int, str]] = []
            for row in rows:
                rank = _candidate_rank("repair-fixture", row)
                _keep_smallest(global_heap, rank, str(row["packageName"]), 32)
                for target in _sample_targets(row):
                    target_counts[target] += 1
                    _keep_smallest(
                        target_heaps[target],
                        rank,
                        str(row["packageName"]),
                        32,
                    )

            sample = _select_samples(
                taxonomy_path,
                seed="repair-fixture",
                sample_size=2,
                package_count=3,
                target_counts=target_counts,
                target_heaps=target_heaps,
                global_heap=global_heap,
                taxonomy_fingerprint="0" * 64,
            )

            self.assertEqual(
                {item["packageName"] for item in sample["samples"]},
                {"/Game/Common/P2_A_Y", "/Game/Common/P3_B_X"},
            )
            self.assertEqual(sample["samplingConstraints"]["unmetQuotaTargets"], [])

    def test_quota_repair_can_escape_a_two_swap_local_minimum(self) -> None:
        def candidate(name: str, roles: list[str]) -> dict[str, object]:
            return {
                "packageName": f"/Game/Common/{name}",
                "profileFingerprint": hashlib.sha256(name.encode()).hexdigest(),
                "primaryTechnicalFamily": "LOGIC",
                "primaryTechnicalKind": "BLUEPRINT",
                "sourceScope": "ARK_PROJECT_CONTENT",
                "mountPoint": "/Game",
                "storageArea": "/Game/Common",
                "originArea": "/Game/Common",
                "originAreaStatus": "PACKAGE_PATH",
                "ownerWorldPath": "",
                "ownerWorldPathStatus": "NOT_APPLICABLE",
                "layoutKind": "STANDARD_PACKAGE",
                "packageShape": "SINGLE_OBJECT",
                "classificationConfidence": "HIGH",
                "semanticStatus": "CONFIRMED",
                "samplingLayer": "DEFINITION_OR_SUPPORT",
                "sampleClusterKey": f"/Game/Common/{name}",
                "objects": [],
                "confirmedSemanticAnchors": [{"role": role} for role in roles],
                "semanticCandidates": [],
                "availableCapabilities": ["BASE"],
                "acquisitionRoutes": [],
                "capabilityStatus": {"identity": "CURRENT"},
            }

        rows = [
            candidate("G", ["g1", "g2", "g3", "g4"]),
            candidate("H", ["h1", "h2"]),
            candidate("A", ["u", "g1", "g2", "h1"]),
            candidate("B", ["u", "g3", "g4", "h2"]),
        ]
        ranks = {
            "/Game/Common/H": "1".zfill(64),
            "/Game/Common/G": "2".zfill(64),
            "/Game/Common/B": "3".zfill(64),
            "/Game/Common/A": "4".zfill(64),
        }
        with tempfile.TemporaryDirectory() as temporary:
            taxonomy_path = Path(temporary) / "taxonomy.jsonl"
            taxonomy_path.write_bytes(b"".join(_canonical_bytes(row) for row in rows))
            target_counts: Counter[str] = Counter()
            target_heaps: dict[str, list[tuple[int, str]]] = defaultdict(list)
            global_heap: list[tuple[int, str]] = []
            for row in rows:
                name = str(row["packageName"])
                rank = ranks[name]
                _keep_smallest(global_heap, rank, name, 32)
                for target in _sample_targets(row):
                    target_counts[target] += 1
                    _keep_smallest(target_heaps[target], rank, name, 32)

            with patch(
                "blueprint_translator.kb_asset_taxonomy_sampling._candidate_rank",
                side_effect=lambda _seed, row: ranks[str(row["packageName"])],
            ):
                sample = _select_samples(
                    taxonomy_path,
                    seed="two-swap-fixture",
                    sample_size=2,
                    package_count=4,
                    target_counts=target_counts,
                    target_heaps=target_heaps,
                    global_heap=global_heap,
                    taxonomy_fingerprint="0" * 64,
                )

            self.assertEqual(
                {item["packageName"] for item in sample["samples"]},
                {"/Game/Common/A", "/Game/Common/B"},
            )
            self.assertEqual(sample["samplingConstraints"]["unmetQuotaTargets"], [])
            repair = sample["samplingConstraints"]["quotaRepairSearch"]
            self.assertEqual(repair["status"], "ACHIEVED")
            self.assertEqual(repair["initialShortfall"], 1)
            self.assertEqual(repair["finalShortfall"], 0)
            self.assertGreaterEqual(repair["depthTwoRepairsApplied"], 1)


if __name__ == "__main__":
    unittest.main()
