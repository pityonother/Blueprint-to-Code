from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.kb_asset_unknown_clustering import (  # noqa: E402
    UnknownClusterBuildError,
    build_unknown_clusters,
)


def _canonical_line(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _known_definition(
    package_name: str,
    *,
    class_path: str,
    family: str = "LOGIC",
    kind: str = "BLUEPRINT",
    confirmed_roles: tuple[str, ...] = (),
    candidate_roles: tuple[str, ...] = (),
) -> dict[str, object]:
    asset_name = package_name.rsplit("/", 1)[-1]
    return {
        "schema": "ark.kb.asset-taxonomy-package.v1",
        "packageName": package_name,
        "primaryObjectPath": f"{package_name}.{asset_name}",
        "primaryTechnicalFamily": family,
        "primaryTechnicalKind": kind,
        "classificationConfidence": "HIGH",
        "semanticStatus": "CONFIRMED" if confirmed_roles else "CANDIDATE",
        "confirmedSemanticAnchors": [
            {"role": role, "status": "CONFIRMED"} for role in confirmed_roles
        ],
        "semanticCandidates": [
            {"role": role, "confidence": "LOW"} for role in candidate_roles
        ],
        "objects": [
            {
                "objectPath": f"{package_name}.{asset_name}",
                "assetName": asset_name,
                "assetClassPath": "/Script/Engine.Blueprint",
                "classificationConfidence": "HIGH",
                "classReferences": [class_path],
            }
        ],
    }


def _unknown_package(
    package_name: str,
    class_path: str,
    *,
    asset_name: str | None = None,
) -> dict[str, object]:
    name = asset_name or package_name.rsplit("/", 1)[-1]
    return {
        "schema": "ark.kb.asset-taxonomy-package.v1",
        "packageName": package_name,
        "primaryObjectPath": f"{package_name}.{name}",
        "primaryTechnicalFamily": "UNKNOWN",
        "primaryTechnicalKind": "UNMAPPED_EXACT_CLASS",
        "classificationConfidence": "UNKNOWN",
        "semanticStatus": "UNKNOWN",
        "confirmedSemanticAnchors": [],
        "semanticCandidates": [],
        "objects": [
            {
                "objectPath": f"{package_name}.{name}",
                "assetName": name,
                "assetClassPath": class_path,
                "classificationConfidence": "UNKNOWN",
                "classReferences": [],
            }
        ],
    }


def _write_taxonomy_source(
    root: Path,
    rows: list[dict[str, object]],
) -> Path:
    root.mkdir(parents=True)
    taxonomy_path = root / "package_taxonomy.jsonl"
    payload = b"".join(_canonical_line(row) for row in rows)
    taxonomy_path.write_bytes(payload)
    unknown_objects = sum(
        item.get("classificationConfidence") == "UNKNOWN"
        for row in rows
        for item in row["objects"]
    )
    manifest = {
        "schema": "ark.kb.asset-taxonomy-manifest.v1",
        "status": "COMPLETE",
        "publicationEligible": True,
        "taxonomyFingerprint": hashlib.sha256(b"fixture-taxonomy").hexdigest(),
        "sourceBinding": {
            "status": "VERIFIED_MANIFEST",
            "generationId": "fixture-generation",
        },
        "counts": {
            "packages": len(rows),
            "technicallyUnknownAssetObjects": unknown_objects,
            "technicallyUnknownPackages": unknown_objects,
        },
        "distributions": {
            "technicalFamily": {"LOGIC": len(rows) - unknown_objects, "UNKNOWN": unknown_objects}
        },
        "files": {
            "packageTaxonomy": {
                "path": taxonomy_path.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "lines": len(rows),
                "rowSchema": "ark.kb.asset-taxonomy-package.v1",
            }
        },
    }
    manifest_path = root / "taxonomy_manifest.json"
    manifest_path.write_bytes(_canonical_line(manifest))
    return manifest_path


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


class UnknownAssetClusteringTests(unittest.TestCase):
    def test_exact_class_dedup_reads_definition_instead_of_wp_guid(self) -> None:
        class_path = (
            "/Game/Structures/BPStructure_Wall.BPStructure_Wall_C"
        )
        definition = _known_definition(
            "/Game/Structures/BPStructure_Wall",
            class_path=class_path,
            confirmed_roles=("STRUCTURE",),
        )
        placements = [
            _unknown_package(
                f"/Game/__ExternalActors__/Maps/Test/0/AA/GUID{index:018d}",
                class_path,
            )
            for index in range(3)
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", [definition, *placements])
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            clusters = _read_jsonl(output / "unknown_exact_clusters.jsonl")
            memberships = _read_jsonl(output / "unknown_cluster_membership.jsonl")
            queue = _read_jsonl(output / "unknown_deep_read_queue.jsonl")
            self.assertEqual(len(clusters), 1)
            self.assertEqual(clusters[0]["memberObjectCount"], 3)
            self.assertEqual(len(memberships), 3)
            self.assertEqual(
                clusters[0]["representative"]["targetKind"], "CLASS_DEFINITION"
            )
            self.assertEqual(
                clusters[0]["representative"]["targetPath"],
                "/Game/Structures/BPStructure_Wall.BPStructure_Wall",
            )
            self.assertNotIn("__ExternalActors__", queue[0]["targetPath"])
            self.assertEqual(
                clusters[0]["existingTaxonomyLink"]["technicalLabelZh"],
                "逻辑与蓝图",
            )
            self.assertEqual(
                clusters[0]["propagatedClassSemantics"][0]["role"], "STRUCTURE"
            )
            self.assertEqual(clusters[0]["propagationScope"], "CLASS_LEVEL_ONLY")
            self.assertIn("INSTANCE_DEFAULTS", clusters[0]["notPropagated"])

    def test_confirmed_classification_does_not_skip_blueprint_content_read(self) -> None:
        class_path = "/Game/Buffs/Buff_Confirmed.Buff_Confirmed_C"
        definition = _known_definition(
            "/Game/Buffs/Buff_Confirmed",
            class_path=class_path,
            confirmed_roles=("GAMEPLAY_EFFECT_BUFF",),
        )
        placement = _unknown_package(
            "/Game/__ExternalActors__/Maps/Test/0/AA/CONFIRMED0000000000000",
            class_path,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(
                root / "source", [definition, placement]
            )
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            queue = _read_jsonl(output / "unknown_deep_read_queue.jsonl")
            self.assertEqual(len(queue), 1)
            self.assertEqual(
                queue[0]["recommendedAction"],
                "READ_BLUEPRINT_CLASS_DEFINITION",
            )

    def test_native_category_rules_do_not_call_restriction_volume_a_building(
        self,
    ) -> None:
        rows = [
            _unknown_package(
                "/Game/__ExternalActors__/Maps/Test/0/AA/MISSION000000000000000",
                "/Script/ShooterGame.MissionTrigger",
            ),
            _unknown_package(
                "/Game/__ExternalActors__/Maps/Test/0/AA/RESTRICT0000000000000",
                "/Script/ShooterGame.StructurePreventionZoneVolume",
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", rows)
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            clusters = {
                row["exactClassPath"]: row
                for row in _read_jsonl(output / "unknown_exact_clusters.jsonl")
            }
            mission = clusters["/Script/ShooterGame.MissionTrigger"]
            restriction = clusters[
                "/Script/ShooterGame.StructurePreventionZoneVolume"
            ]
            self.assertEqual(
                mission["categoryCandidates"][0]["code"], "MISSION_EVENT_FLOW"
            )
            self.assertEqual(
                restriction["categoryCandidates"][0]["code"],
                "WORLD_RESTRICTION_VOLUME",
            )
            self.assertNotEqual(
                restriction["categoryCandidates"][0]["code"], "STRUCTURE_BUILDING"
            )
            self.assertTrue(
                all(item["status"] == "CANDIDATE" for item in mission["categoryCandidates"])
            )

    def test_reward_actor_spawn_volume_and_resource_node_remain_distinct(self) -> None:
        expected = {
            "/Script/ShooterGame.ExplorerChest": "EXPLORER_CHEST_ACTOR",
            "/Script/ShooterGame.SupplyCrateSpawningVolume": "REWARD_SPAWN_VOLUME",
            "/Script/ShooterGame.LunarOxygenVent": "RESOURCE_INTERACTION_NODE",
        }
        rows = [
            _unknown_package(f"/Game/Unknown/Item{index}", class_path)
            for index, class_path in enumerate(expected)
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", rows)
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            clusters = {
                row["exactClassPath"]: row
                for row in _read_jsonl(output / "unknown_exact_clusters.jsonl")
            }
            for class_path, category in expected.items():
                self.assertEqual(
                    clusters[class_path]["categoryCandidates"][0]["code"],
                    category,
                )
                self.assertEqual(
                    clusters[class_path]["confirmedFunctionalClassifications"],
                    [],
                )

    def test_specific_resource_and_destructible_rules_beat_structure_parent(self) -> None:
        oxygen_class = (
            "/Game/Genesis/Structures/GasVein/LunarOxygenVent_BP."
            "LunarOxygenVent_BP_C"
        )
        oxygen_definition = _known_definition(
            "/Game/Genesis/Structures/GasVein/LunarOxygenVent_BP",
            class_path=oxygen_class,
        )
        oxygen_definition["objects"][0]["classReferences"].append(
            "/Script/ShooterGame.PrimalStructure"
        )
        pillar_class = (
            "/DinoDefense/Environment/DestructibleStructures/"
            "BPStructure_Pillar_3.BPStructure_Pillar_3_C"
        )
        pillar_definition = _known_definition(
            "/DinoDefense/Environment/DestructibleStructures/BPStructure_Pillar_3",
            class_path=pillar_class,
        )
        rows = [
            oxygen_definition,
            _unknown_package("/Game/__ExternalActors__/Map/Oxygen", oxygen_class),
            pillar_definition,
            _unknown_package("/Game/__ExternalActors__/Map/Pillar", pillar_class),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", rows)
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            clusters = {
                row["exactClassPath"]: row
                for row in _read_jsonl(output / "unknown_exact_clusters.jsonl")
            }
            self.assertEqual(
                clusters[oxygen_class]["categoryCandidates"][0]["code"],
                "RESOURCE_INTERACTION_NODE",
            )
            self.assertEqual(
                clusters[pillar_class]["categoryCandidates"][0]["code"],
                "DESTRUCTIBLE_ENVIRONMENT_STRUCTURE",
            )

    def test_generic_server_point_tool_spline_and_hazard_are_separate(self) -> None:
        expected = {
            "/Game/Core/ServerSidePoint.ServerSidePoint_C": (
                "WORLD_LOGIC_MARKER_POINT"
            ),
            "/Game/Art_Tools/EUA_AreaPoint.EUA_AreaPoint_C": "EDITOR_LEVEL_TOOL",
            "/Script/ShooterGame.SplineActor": "WORLD_SPLINE_PATH",
            "/Script/ShooterGame.TogglePainVolume": "HAZARD_DAMAGE_VOLUME",
        }
        rows: list[dict[str, object]] = []
        for index, class_path in enumerate(expected):
            if class_path.startswith("/Script/"):
                rows.append(_unknown_package(f"/Game/Unknown/{index}", class_path))
                continue
            package = class_path.split(".", 1)[0]
            rows.append(_known_definition(package, class_path=class_path))
            rows.append(_unknown_package(f"/Game/Unknown/{index}", class_path))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", rows)
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            clusters = {
                row["exactClassPath"]: row
                for row in _read_jsonl(output / "unknown_exact_clusters.jsonl")
            }
            for class_path, category in expected.items():
                self.assertEqual(
                    clusters[class_path]["categoryCandidates"][0]["code"],
                    category,
                )

    def test_mount_path_and_wp_storage_path_do_not_create_false_semantics(self) -> None:
        turret_class = (
            "/DinoDefense/Props/BaseTurret_Platformer.BaseTurret_Platformer_C"
        )
        turret_definition = _known_definition(
            "/DinoDefense/Props/BaseTurret_Platformer",
            class_path=turret_class,
        )
        spawn_crate_class = "/Game/EndGame/BP_SpawnCrate.BP_SpawnCrate_C"
        spawn_crate_definition = _known_definition(
            "/Game/EndGame/BP_SpawnCrate",
            class_path=spawn_crate_class,
        )
        spawn_crate_definition["objects"][0]["classReferences"].append(
            "/Script/Engine.Emitter"
        )
        rows = [
            turret_definition,
            _unknown_package("/Game/__ExternalActors__/Map/Turret", turret_class),
            spawn_crate_definition,
            _unknown_package(
                "/Game/__ExternalActors__/Map/Crate", spawn_crate_class
            ),
            _unknown_package(
                "/Game/__ExternalActors__/Map/UI/RandomHash",
                "/Script/Engine.Actor",
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", rows)
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            clusters = {
                row["exactClassPath"]: row
                for row in _read_jsonl(output / "unknown_exact_clusters.jsonl")
            }
            self.assertNotEqual(
                clusters[turret_class]["categoryCandidates"][0]["code"],
                "CREATURE_CHARACTER",
            )
            self.assertEqual(
                clusters[spawn_crate_class]["categoryCandidates"][0]["code"],
                "REWARD_SPAWN_EFFECT",
            )
            self.assertNotEqual(
                clusters["/Script/Engine.Actor"]["categoryCandidates"][0][
                    "code"
                ],
                "USER_INTERFACE",
            )

    def test_numeric_name_family_is_candidate_only_and_keeps_exact_clusters(
        self,
    ) -> None:
        rows: list[dict[str, object]] = []
        for index in (1, 2):
            package = f"/Game/Environment/Canyon/BPP_Canyon_{index:02d}"
            class_path = f"{package}.BPP_Canyon_{index:02d}_C"
            rows.append(_known_definition(package, class_path=class_path))
            rows.append(
                _unknown_package(
                    f"/Game/__ExternalActors__/Maps/Test/0/AA/CANYON{index:016d}",
                    class_path,
                )
            )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", rows)
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            clusters = _read_jsonl(output / "unknown_exact_clusters.jsonl")
            families = _read_jsonl(output / "unknown_name_families.jsonl")
            queue = _read_jsonl(output / "unknown_deep_read_queue.jsonl")
            self.assertEqual(len(clusters), 2)
            self.assertEqual(len(queue), 2)
            self.assertEqual(len(families), 1)
            self.assertEqual(families[0]["status"], "CANDIDATE_ONLY")
            self.assertFalse(families[0]["approvedForSafeDedupe"])
            self.assertEqual(families[0]["exactClusterCount"], 2)

    def test_role_stem_family_groups_navigation_but_not_exact_evidence(self) -> None:
        rows: list[dict[str, object]] = []
        for suffix in ("DinoDossiers", "Li"):
            package = f"/Game/ExplorerChest/ExplorerChest_{suffix}"
            class_path = f"{package}.ExplorerChest_{suffix}_C"
            rows.append(_known_definition(package, class_path=class_path))
            rows.append(
                _unknown_package(
                    f"/Game/__ExternalActors__/Map/{suffix}",
                    class_path,
                )
            )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", rows)
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            families = _read_jsonl(output / "unknown_name_families.jsonl")
            clusters = _read_jsonl(output / "unknown_exact_clusters.jsonl")
            queue = _read_jsonl(output / "unknown_deep_read_queue.jsonl")
            role_family = next(
                row
                for row in families
                if row["normalizationRule"] == "ROLE_STEM_PREFIX"
            )
            self.assertEqual(role_family["exactClusterCount"], 2)
            self.assertFalse(role_family["approvedForSafeDedupe"])
            self.assertEqual(len(clusters), 2)
            self.assertEqual(len(queue), 2)

    def test_redirector_with_matching_package_name_is_not_a_class_definition(
        self,
    ) -> None:
        class_path = "/Game/Redirected/Foo.Foo_C"
        redirector = {
            "schema": "ark.kb.asset-taxonomy-package.v1",
            "packageName": "/Game/Redirected/Foo",
            "primaryObjectPath": "/Game/Redirected/Foo.Foo",
            "primaryTechnicalFamily": "REDIRECTOR",
            "primaryTechnicalKind": "OBJECT_REDIRECTOR",
            "classificationConfidence": "HIGH",
            "semanticStatus": "UNKNOWN",
            "confirmedSemanticAnchors": [],
            "semanticCandidates": [],
            "objects": [
                {
                    "objectPath": "/Game/Redirected/Foo.Foo",
                    "assetName": "Foo",
                    "assetClassPath": "/Script/CoreUObject.ObjectRedirector",
                    "classificationConfidence": "HIGH",
                    "classReferences": [],
                }
            ],
        }
        placement = _unknown_package("/Game/Unknown/FooPlacement", class_path)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(
                root / "source", [redirector, placement]
            )
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            cluster = _read_jsonl(output / "unknown_exact_clusters.jsonl")[0]
            queue = _read_jsonl(output / "unknown_deep_read_queue.jsonl")[0]
            self.assertEqual(cluster["existingTaxonomyLink"]["status"], "NOT_FOUND")
            self.assertEqual(
                cluster["representative"]["targetKind"], "PLACEMENT_FALLBACK"
            )
            self.assertEqual(queue["recommendedAction"], "MANUAL_IDENTITY_REVIEW")

    def test_input_order_is_byte_deterministic_and_report_is_chinese(self) -> None:
        class_path = "/Game/Missions/MissionPoint.MissionPoint_C"
        rows = [
            _known_definition("/Game/Missions/MissionPoint", class_path=class_path),
            _unknown_package(
                "/Game/__ExternalActors__/Maps/Test/0/AA/BBBBBBBBBBBBBBBBBBBBBB",
                class_path,
            ),
            _unknown_package(
                "/Game/__ExternalActors__/Maps/Test/0/AA/AAAAAAAAAAAAAAAAAAAAAA",
                class_path,
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_a = _write_taxonomy_source(root / "source-a", rows)
            manifest_b = _write_taxonomy_source(root / "source-b", list(reversed(rows)))
            output_a = root / "clusters-a"
            output_b = root / "clusters-b"

            build_unknown_clusters(manifest_a, output_a)
            build_unknown_clusters(manifest_b, output_b)

            for name in (
                "unknown_exact_clusters.jsonl",
                "unknown_cluster_membership.jsonl",
                "unknown_name_families.jsonl",
                "unknown_deep_read_queue.jsonl",
                "unknown_stratified_sample_plan.json",
                "unknown_cluster_review_zh.md",
                "ark_asset_classification_summary_zh.md",
            ):
                self.assertEqual((output_a / name).read_bytes(), (output_b / name).read_bytes())
            report = (output_a / "ark_asset_classification_summary_zh.md").read_text(
                "utf-8"
            )
            self.assertIn("逻辑与蓝图", report)
            self.assertIn("任务运行与事件支撑", report)
            self.assertIn("类级结论", report)
            self.assertIn("不能传播实例默认值", report)

    def test_stratified_plan_covers_each_candidate_category(self) -> None:
        rows = [
            _unknown_package(
                "/Game/Unknown/MissionA",
                "/Script/ShooterGame.MissionTrigger",
            ),
            _unknown_package(
                "/Game/Unknown/MissionB",
                "/Script/ShooterGame.MissionSpline",
            ),
            _unknown_package(
                "/Game/Unknown/Restriction",
                "/Script/ShooterGame.StructurePreventionZoneVolume",
            ),
            _unknown_package(
                "/Game/Unknown/Hazard",
                "/Script/ShooterGame.TogglePainVolume",
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(root / "source", rows)
            output = root / "clusters"

            build_unknown_clusters(manifest, output)

            plan = json.loads(
                (output / "unknown_stratified_sample_plan.json").read_text("utf-8")
            )
            self.assertEqual(plan["status"], "PLANNED_NOT_READ")
            self.assertEqual(plan["samplesPerCategoryMaximum"], 2)
            self.assertEqual(plan["coverage"]["categoryGroupsCovered"], 3)
            self.assertEqual(plan["coverage"]["categoryGroupsTotal"], 3)
            self.assertEqual(len(plan["samples"]), 4)
            self.assertEqual(
                len({sample["clusterId"] for sample in plan["samples"]}),
                len(plan["samples"]),
            )
            self.assertTrue(
                all(sample["readStatus"] == "PLANNED_NOT_READ" for sample in plan["samples"])
            )

    def test_manifest_hash_mismatch_fails_closed_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_taxonomy_source(
                root / "source",
                [
                    _unknown_package(
                        "/Game/Unknown/One",
                        "/Script/ShooterGame.MissionTrigger",
                    )
                ],
            )
            payload = json.loads(manifest.read_text("utf-8"))
            payload["files"]["packageTaxonomy"]["sha256"] = "0" * 64
            manifest.write_bytes(_canonical_line(payload))
            output = root / "clusters"

            with self.assertRaisesRegex(
                UnknownClusterBuildError, "SOURCE_TAXONOMY_SHA256_MISMATCH"
            ):
                build_unknown_clusters(manifest, output)

            self.assertFalse(output.exists())

    def test_publication_manifest_matches_checked_in_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _write_taxonomy_source(
                root / "source",
                [
                    _unknown_package(
                        "/Game/Unknown/One",
                        "/Script/ShooterGame.MissionTrigger",
                    )
                ],
            )
            output = root / "clusters"

            build_unknown_clusters(source, output)

            schema = json.loads(
                (ROOT / "schemas" / "kb_asset_unknown_cluster_manifest_v1.schema.json")
                .read_text("utf-8")
            )
            manifest = json.loads(
                (output / "unknown_cluster_manifest.json").read_text("utf-8")
            )
            Draft202012Validator(schema).validate(manifest)


if __name__ == "__main__":
    unittest.main()
