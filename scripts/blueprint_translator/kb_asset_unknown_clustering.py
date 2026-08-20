"""Class-level UNKNOWN deduplication and conservative Chinese candidate labels.

The safe deduplication key is the complete Unreal ``assetClassPath``.  Similar
names are emitted separately as review-only families and never authorize
semantic propagation or instance-level claims.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path


UNKNOWN_CLUSTER_MANIFEST_SCHEMA = "ark.kb.asset-unknown-cluster-manifest.v1"
EXACT_CLUSTER_ROW_SCHEMA = "ark.kb.asset-unknown-exact-cluster.v1"
MEMBERSHIP_ROW_SCHEMA = "ark.kb.asset-unknown-cluster-membership.v1"
NAME_FAMILY_ROW_SCHEMA = "ark.kb.asset-unknown-name-family.v1"
DEEP_READ_QUEUE_ROW_SCHEMA = "ark.kb.asset-unknown-deep-read-target.v1"
RULESET_VERSION = "ark-unknown-class-clustering-rules/v2"


TECHNICAL_FAMILY_LABELS_ZH = {
    "AI": "人工智能",
    "ANIMATION": "动画",
    "AUDIO": "音频",
    "CINEMATIC_MEDIA": "过场与媒体",
    "DATA": "数据资产",
    "EDITOR": "编辑器与工具",
    "INPUT": "输入",
    "LOGIC": "逻辑与蓝图",
    "ML": "机器学习",
    "PHYSICS": "物理",
    "PROCEDURAL": "程序化生成",
    "REDIRECTOR": "重定向器",
    "SCHEMA": "类型与结构定义",
    "UI": "用户界面",
    "UNKNOWN": "待分类",
    "VFX": "视觉特效",
    "VISUAL": "视觉资源",
    "WORLD": "世界、关卡与场景",
}

TECHNICAL_KIND_LABELS_ZH = {
    "BLUEPRINT": "蓝图",
    "TOOL_METADATA": "编辑器工具元数据",
    "OBJECT_REDIRECTOR": "对象重定向器",
    "UNMAPPED_EXACT_CLASS": "精确类尚未映射",
}

CATEGORY_LABELS_ZH = {
    "MISSION_EVENT_FLOW": "任务运行与事件支撑",
    "EXPLORER_CHEST_ACTOR": "探索宝箱与可开启收集物 Actor（非 Loot 池）",
    "REWARD_SPAWN_VOLUME": "补给箱与奖励点刷新区域",
    "REWARD_MARKER_POINT": "宝箱与奖励放置标记点",
    "REWARD_SPAWN_EFFECT": "补给箱刷新视觉/发射器",
    "SPAWN_ZONE_SYSTEM": "生物/NPC 刷新与区域管理",
    "NPC_SPAWN_MANAGER_WATER": "水域 NPC 刷新管理器",
    "NPC_SPAWN_MANAGER_LAND": "陆地 NPC 刷新管理器",
    "NPC_SPAWN_MANAGER_CAVE": "洞穴 NPC 刷新管理器",
    "WORLD_LOGIC_MARKER_POINT": "通用服务端世界逻辑标记点",
    "WORLD_RESTRICTION_VOLUME": "世界限制与禁建区域",
    "STRUCTURE_BUILDING": "建筑与结构",
    "DESTRUCTIBLE_ENVIRONMENT_STRUCTURE": "场景可破坏结构与 GeometryCollection 构件",
    "WORLD_TERMINAL": "世界终端与交互设施",
    "CREATURE_CHARACTER": "生物、NPC 与角色",
    "RESOURCE_INTERACTION_NODE": "资源点与环境交互节点",
    "HAZARD_DAMAGE_VOLUME": "危险、伤害与环境触发区域",
    "PORTAL_TELEPORT_POINT": "传送、入口与目标点",
    "PROGRESSION_SKILL_DATA": "成长、技能树与里程碑数据",
    "PROCEDURAL_ECOLOGY": "PCG 与程序化生态",
    "WATER_SYSTEM": "水体与水面系统",
    "LIGHTING_WEATHER_SKY": "光照、天气与天空",
    "AUDIO_AMBIENCE": "环境音与音频区域",
    "VFX_POST_PROCESS": "特效与后处理",
    "PACKED_LEVEL_ENVIRONMENT_ACTOR": "打包关卡与环境几何 Actor",
    "WORLD_ENVIRONMENT_GEOMETRY": "环境、地形与场景 Actor",
    "WORLD_SPLINE_PATH": "世界样条、路径与轨迹 Actor",
    "NAVIGATION_SYSTEM": "导航与寻路系统",
    "EDITOR_LEVEL_TOOL": "编辑器与关卡制作工具",
    "CINEMATIC_CONTROL": "过场、Matinee 与演出控制",
    "GAMEPLAY_EFFECT_BUFF": "游戏效果与 Buff",
    "CAMERA_CONTROL": "摄像机与视角控制",
    "ITEM_WEAPON_PICKUP": "物品、武器与拾取物",
    "MOD_METADATA": "Mod 元数据",
    "DATA_CONFIGURATION": "数据、数据库与配置",
    "USER_INTERFACE": "用户界面",
    "REVIEW_REQUIRED": "其他待深读",
}

NOT_PROPAGATED = [
    "INSTANCE_DEFAULTS",
    "PLACEMENT_TRANSFORM",
    "INSTANCE_REFERENCES",
    "RUNTIME_BEHAVIOR",
]


class UnknownClusterBuildError(RuntimeError):
    """Fail-closed UNKNOWN clustering or publication error."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_line(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_line(value))


def _require_mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise UnknownClusterBuildError(code)
    return value


def _required_text(value: object, code: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise UnknownClusterBuildError(code)
    return text


def _required_int(mapping: Mapping[str, object], key: str, code: str) -> int:
    value = mapping.get(key)
    if value is None or isinstance(value, bool):
        raise UnknownClusterBuildError(code)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise UnknownClusterBuildError(code) from None
    if number < 0:
        raise UnknownClusterBuildError(code)
    return number


def _load_json(path: Path, code: str) -> Mapping[str, object]:
    try:
        return _require_mapping(json.loads(path.read_text("utf-8")), code)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnknownClusterBuildError(f"{code}:{type(exc).__name__}") from None


def _resolve_member(manifest_path: Path, relative: object, code: str) -> Path:
    text = _required_text(relative, code).replace("/", os.sep)
    root = manifest_path.parent.resolve()
    candidate = (root / text).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise UnknownClusterBuildError(code) from None
    if not candidate.is_file():
        raise UnknownClusterBuildError(code)
    return candidate


def _sha256_file(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    byte_count = 0
    line_count = 0
    last = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            byte_count += len(chunk)
            line_count += chunk.count(b"\n")
            last = chunk[-1:]
    if byte_count and last != b"\n":
        line_count += 1
    return digest.hexdigest(), byte_count, line_count


def _file_descriptor(path: Path) -> dict[str, object]:
    sha256, byte_count, line_count = _sha256_file(path)
    return {
        "path": path.name,
        "sha256": sha256,
        "bytes": byte_count,
        "lines": line_count,
    }


def _class_leaf(class_path: str) -> str:
    if class_path.startswith("/Script/") and "." in class_path:
        return class_path.rsplit(".", 1)[-1]
    leaf = class_path.rsplit("/", 1)[-1].split(".", 1)[0]
    return leaf.removesuffix("_C")


def _generated_definition_package(class_path: str) -> str | None:
    if class_path.startswith("/Script/") or not class_path.endswith("_C"):
        return None
    if "." not in class_path:
        return None
    return class_path.split(".", 1)[0]


def _cluster_id(class_path: str) -> str:
    return "class-" + hashlib.sha256(class_path.encode("utf-8")).hexdigest()[:20]


def _family_id(key: str) -> str:
    return "family-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _label(mapping: Mapping[str, str], code: str) -> str:
    return mapping.get(code, f"未翻译：{code}")


def _rule_candidate(
    code: str,
    rule_id: str,
    evidence: Iterable[str],
    *,
    confidence: str = "MEDIUM",
) -> list[dict[str, object]]:
    return [
        {
            "code": code,
            "labelZh": CATEGORY_LABELS_ZH[code],
            "status": "CANDIDATE",
            "confidence": confidence,
            "evidenceKind": "EXACT_CLASS_PARENT_OR_PATH_RULE",
            "ruleId": rule_id,
            "matchedEvidence": sorted(set(evidence)),
        }
    ]


def _category_candidates(
    class_path: str,
    definition: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    leaf = _class_leaf(class_path)
    definition_package = "" if definition is None else str(definition["packageName"])
    definition_leaf = (
        "" if not definition_package else definition_package.rsplit("/", 1)[-1]
    )
    references = (
        [] if definition is None else [str(item) for item in definition.get("classReferences", [])]
    )
    reference_leaves = {_class_leaf(reference).casefold() for reference in references}
    names = [item for item in (leaf, definition_leaf) if item]
    names_folded = [item.casefold() for item in names]
    package_folded = definition_package.casefold()
    evidence = [class_path, *references]
    if definition_package:
        evidence.insert(1, definition_package)

    def name_contains(*needles: str) -> bool:
        return any(
            needle.casefold() in name
            for needle in needles
            for name in names_folded
        )

    def name_starts(*prefixes: str) -> bool:
        return any(
            name.startswith(prefix.casefold())
            for prefix in prefixes
            for name in names_folded
        )

    def parent_is(*parent_leaves: str) -> bool:
        return any(parent.casefold() in reference_leaves for parent in parent_leaves)

    if (
        name_starts("EUA_")
        or "/art_tools/level_tools/" in package_folded
        or "/utilities/scaletool/" in package_folded
        or "/building_tools/" in package_folded
    ):
        return _rule_candidate("EDITOR_LEVEL_TOOL", "category.editor-tool.v1", evidence)
    if name_starts(
        "MissionTrigger",
        "MissionServerSidePoint",
        "MissionSpline",
        "MissionDispatcher",
        "MissionManager",
        "PointOfInterestBP_Mission",
        "TEMP_Mission",
        "Helper_ShowHuntStages",
    ) or (
        "/missions/" in package_folded
        and name_starts(
            "Mission", "BossArenaManager", "VRBattle_CodeKeyTumbler"
        )
    ):
        return _rule_candidate("MISSION_EVENT_FLOW", "category.mission.v2", evidence)
    if name_contains("SupplyCrateSpawningVolume"):
        return _rule_candidate(
            "REWARD_SPAWN_VOLUME", "category.reward-spawn-volume.v2", evidence
        )
    if name_starts("ExplorerChest") or parent_is("ExplorerChest_Base"):
        return _rule_candidate(
            "EXPLORER_CHEST_ACTOR", "category.explorer-chest.v2", evidence
        )
    if name_starts("ServerSidePoint_Chest"):
        return _rule_candidate(
            "REWARD_MARKER_POINT", "category.reward-marker.v1", evidence
        )
    if name_contains("SpawnCrate") and parent_is("Emitter"):
        return _rule_candidate(
            "REWARD_SPAWN_EFFECT", "category.reward-spawn-effect.v1", evidence
        )
    if (
        parent_is("PrimalProgressionTreeAsset")
        or leaf.casefold() == "primalprogressiontreeasset"
        or name_contains("ProgressionTree", "SkillTree", "Milestone")
    ):
        return _rule_candidate(
            "PROGRESSION_SKILL_DATA", "category.progression.v2", evidence
        )
    if name_starts("NPCZoneManagerBlueprint_Water"):
        return _rule_candidate(
            "NPC_SPAWN_MANAGER_WATER", "category.npc-manager-water.v1", evidence
        )
    if name_starts("NPCZoneManagerBlueprint_Land"):
        return _rule_candidate(
            "NPC_SPAWN_MANAGER_LAND", "category.npc-manager-land.v1", evidence
        )
    if name_starts("NPCZoneManagerBlueprint_Cave"):
        return _rule_candidate(
            "NPC_SPAWN_MANAGER_CAVE", "category.npc-manager-cave.v1", evidence
        )
    if name_contains(
        "NPCZone", "BiomeZone", "DinoSpawner", "SpawnLocation", "SpawnPoint"
    ):
        return _rule_candidate("SPAWN_ZONE_SYSTEM", "category.spawn-zone.v2", evidence)
    if leaf.casefold() in {"serversidepoint", "serversidepoint_huge"}:
        return _rule_candidate(
            "WORLD_LOGIC_MARKER_POINT", "category.server-side-point.v1", evidence
        )
    if name_contains("StructurePreventionZoneVolume"):
        return _rule_candidate(
            "WORLD_RESTRICTION_VOLUME", "category.restriction-volume.v2", evidence
        )
    if name_contains("OxygenVent", "OilVein", "GasVein", "ElementPool"):
        return _rule_candidate(
            "RESOURCE_INTERACTION_NODE", "category.resource-node.v2", evidence
        )
    if name_contains("PrimalStructureDB", "ModTemplateData"):
        return _rule_candidate(
            "DATA_CONFIGURATION", "category.structure-data.v1", evidence
        )
    if (
        "/destructiblestructures/" in package_folded
        or parent_is("Structure_WithGeoCollection_Platformer")
    ):
        return _rule_candidate(
            "DESTRUCTIBLE_ENVIRONMENT_STRUCTURE",
            "category.destructible-structure.v1",
            evidence,
        )
    if name_contains("TributeTerminal"):
        return _rule_candidate("WORLD_TERMINAL", "category.world-terminal.v1", evidence)
    if (
        name_starts("BPStructure", "PrimalStructure", "StructureTek")
        or parent_is("PrimalStructure", "PrimalStructureWaterPipe")
    ):
        return _rule_candidate("STRUCTURE_BUILDING", "category.structure.v2", evidence)
    if name_contains("PoisonPlant", "PainVolume", "DamageVolume", "HazardTrigger"):
        return _rule_candidate(
            "HAZARD_DAMAGE_VOLUME", "category.hazard-damage.v1", evidence
        )
    if name_contains("Character", "Creature", "SimpleAI") or re.search(
        r"(?:^|_)Dino(?:_|$|[A-Z])", leaf
    ):
        return _rule_candidate(
            "CREATURE_CHARACTER", "category.creature-character.v2", evidence
        )
    if name_contains("Teleport", "Portal", "TargetPoint"):
        return _rule_candidate(
            "PORTAL_TELEPORT_POINT", "category.portal-teleport.v2", evidence
        )
    if name_starts("PCG", "BP_PCG", "PDA_PCG"):
        return _rule_candidate(
            "PROCEDURAL_ECOLOGY", "category.procedural-ecology.v2", evidence
        )
    if name_contains(
        "WaterPlane",
        "WaterPhysics",
        "Waterfall",
        "WaterPostProcess",
        "Waterline",
        "Shore_Capture",
        "WaterSimulation",
        "OceanVolume",
        "WaterMethodVolume",
    ):
        return _rule_candidate("WATER_SYSTEM", "category.water.v2", evidence)
    if name_contains("Weather", "Sky", "Light", "Cloud", "DayCycle"):
        return _rule_candidate(
            "LIGHTING_WEATHER_SKY", "category.lighting-weather.v2", evidence
        )
    if name_contains("AmbientSound", "VoiceCollection", "FoleyCollection", "Audio"):
        return _rule_candidate("AUDIO_AMBIENCE", "category.audio.v2", evidence)
    if name_contains("PostProcess", "Particle", "Niagara", "LensAndFilm", "VFX"):
        return _rule_candidate("VFX_POST_PROCESS", "category.vfx.v2", evidence)
    if name_contains("Camera", "ViewTarget"):
        return _rule_candidate("CAMERA_CONTROL", "category.camera.v2", evidence)
    if name_contains("Pickup", "Dropped", "Weapon", "PrimalItem"):
        return _rule_candidate("ITEM_WEAPON_PICKUP", "category.item-weapon.v2", evidence)
    if name_contains("ModDataAsset"):
        return _rule_candidate("MOD_METADATA", "category.mod-metadata.v2", evidence)
    if (
        name_contains("DataAsset", "Database", "Config", "SettingsData")
        or any(re.search(r"(?:^|_)DB(?:_|$)", name, re.IGNORECASE) for name in names)
    ):
        return _rule_candidate(
            "DATA_CONFIGURATION", "category.data-configuration.v2", evidence
        )
    if name_contains("Widget", "UserInterface") or any(
        re.search(r"(?:^|_)UI(?:_|$)", name, re.IGNORECASE) for name in names
    ):
        return _rule_candidate("USER_INTERFACE", "category.ui.v2", evidence)
    if name_contains("Buff_") or parent_is("PrimalBuff"):
        return _rule_candidate(
            "GAMEPLAY_EFFECT_BUFF", "category.gameplay-buff.v1", evidence
        )
    if leaf.casefold() == "splineactor" or name_contains("WorldSpline", "PathSpline"):
        return _rule_candidate("WORLD_SPLINE_PATH", "category.world-spline.v1", evidence)
    if name_contains("RecastNavMesh", "Navigation"):
        return _rule_candidate("NAVIGATION_SYSTEM", "category.navigation.v1", evidence)
    if name_contains("Matinee", "Cinematic"):
        return _rule_candidate(
            "CINEMATIC_CONTROL", "category.cinematic-control.v1", evidence
        )
    if parent_is("PackedLevelActor"):
        return _rule_candidate(
            "PACKED_LEVEL_ENVIRONMENT_ACTOR",
            "category.packed-level-environment.v1",
            evidence,
            confidence="MEDIUM",
        )
    if name_contains(
        "Landscape", "Cave", "Canyon", "Rock", "GiantBranch", "Lava_River"
    ):
        return _rule_candidate(
            "WORLD_ENVIRONMENT_GEOMETRY",
            "category.environment-actor.v2",
            evidence,
            confidence="LOW",
        )
    return _rule_candidate(
        "REVIEW_REQUIRED", "category.unresolved.v2", evidence, confidence="UNKNOWN"
    )


def _validate_source_manifest(
    manifest_path: Path,
) -> tuple[Mapping[str, object], Path, dict[str, object]]:
    manifest = _load_json(manifest_path, "SOURCE_MANIFEST_INVALID")
    if manifest.get("schema") != "ark.kb.asset-taxonomy-manifest.v1":
        raise UnknownClusterBuildError("SOURCE_MANIFEST_SCHEMA_UNSUPPORTED")
    if manifest.get("status") != "COMPLETE":
        raise UnknownClusterBuildError("SOURCE_MANIFEST_NOT_COMPLETE")
    fingerprint = _required_text(
        manifest.get("taxonomyFingerprint"), "SOURCE_TAXONOMY_FINGERPRINT_MISSING"
    )
    files = _require_mapping(manifest.get("files"), "SOURCE_MANIFEST_FILES_INVALID")
    descriptor = _require_mapping(
        files.get("packageTaxonomy"), "SOURCE_PACKAGE_TAXONOMY_DESCRIPTOR_INVALID"
    )
    taxonomy_path = _resolve_member(
        manifest_path,
        descriptor.get("path"),
        "SOURCE_PACKAGE_TAXONOMY_PATH_INVALID",
    )
    expected = {
        "sha256": _required_text(
            descriptor.get("sha256"), "SOURCE_TAXONOMY_SHA256_MISSING"
        ),
        "bytes": _required_int(
            descriptor, "bytes", "SOURCE_TAXONOMY_BYTES_INVALID"
        ),
        "lines": _required_int(
            descriptor, "lines", "SOURCE_TAXONOMY_LINES_INVALID"
        ),
        "unknownObjects": _required_int(
            _require_mapping(manifest.get("counts"), "SOURCE_COUNTS_INVALID"),
            "technicallyUnknownAssetObjects",
            "SOURCE_UNKNOWN_OBJECT_COUNT_INVALID",
        ),
        "fingerprint": fingerprint,
    }
    return manifest, taxonomy_path, expected


def _definition_summary(row: Mapping[str, object]) -> dict[str, object]:
    references: set[str] = set()
    for item in row.get("objects", []):
        if not isinstance(item, Mapping):
            continue
        for reference in item.get("classReferences", []):
            text = str(reference).strip()
            if text:
                references.add(text)
    return {
        "packageName": str(row["packageName"]),
        "primaryObjectPath": str(row.get("primaryObjectPath") or row["packageName"]),
        "primaryTechnicalFamily": str(
            row.get("primaryTechnicalFamily") or "UNKNOWN"
        ),
        "primaryTechnicalKind": str(row.get("primaryTechnicalKind") or "UNKNOWN"),
        "classificationConfidence": str(
            row.get("classificationConfidence") or "UNKNOWN"
        ),
        "semanticStatus": str(row.get("semanticStatus") or "UNKNOWN"),
        "confirmedSemanticAnchors": sorted(
            (
                dict(item)
                for item in row.get("confirmedSemanticAnchors", [])
                if isinstance(item, Mapping)
            ),
            key=_canonical_json,
        ),
        "semanticCandidates": sorted(
            (
                dict(item)
                for item in row.get("semanticCandidates", [])
                if isinstance(item, Mapping)
            ),
            key=_canonical_json,
        ),
        "classReferences": sorted(references),
    }


_DEFINITION_KINDS = {
    "BLUEPRINT",
    "BLUEPRINT_INTERFACE",
    "BLUEPRINT_RIG",
    "FUNCTION_LIBRARY",
    "MACRO_LIBRARY",
    "TOOL_METADATA",
    "WIDGET_BLUEPRINT",
}


def _scan_taxonomy(
    taxonomy_path: Path,
    expected: Mapping[str, object],
) -> tuple[
    dict[str, list[dict[str, str]]],
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
    dict[str, object],
]:
    digest = hashlib.sha256()
    byte_count = 0
    line_count = 0
    package_names: set[str] = set()
    cluster_members: dict[str, list[dict[str, str]]] = defaultdict(list)
    definitions_by_class: dict[str, dict[str, object]] = {}
    definitions_by_package: dict[str, dict[str, object]] = {}

    with taxonomy_path.open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            digest.update(raw)
            byte_count += len(raw)
            line_count += 1
            try:
                row = _require_mapping(
                    json.loads(raw.decode("utf-8")),
                    "SOURCE_TAXONOMY_ROW_INVALID",
                )
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise UnknownClusterBuildError(
                    f"SOURCE_TAXONOMY_ROW_INVALID:{line_number}:{type(exc).__name__}"
                ) from None
            if row.get("schema") != "ark.kb.asset-taxonomy-package.v1":
                raise UnknownClusterBuildError("SOURCE_TAXONOMY_ROW_SCHEMA_INVALID")
            package_name = _required_text(
                row.get("packageName"), "SOURCE_PACKAGE_NAME_MISSING"
            )
            if package_name in package_names:
                raise UnknownClusterBuildError("SOURCE_PACKAGE_DUPLICATE")
            package_names.add(package_name)

            summary = _definition_summary(row)
            own_generated_references = [
                reference
                for reference in summary["classReferences"]
                if _generated_definition_package(str(reference)) == package_name
            ]
            if (
                str(row.get("primaryTechnicalKind")) in _DEFINITION_KINDS
                or own_generated_references
            ):
                definitions_by_package[package_name] = summary
            for reference in own_generated_references:
                existing = definitions_by_class.get(str(reference))
                if existing is not None and existing["packageName"] != package_name:
                    raise UnknownClusterBuildError(
                        "GENERATED_CLASS_DEFINITION_AMBIGUOUS"
                    )
                definitions_by_class[str(reference)] = summary

            objects = row.get("objects")
            if not isinstance(objects, list):
                raise UnknownClusterBuildError("SOURCE_OBJECTS_INVALID")
            for item in objects:
                if not isinstance(item, Mapping):
                    raise UnknownClusterBuildError("SOURCE_OBJECT_INVALID")
                if item.get("classificationConfidence") != "UNKNOWN":
                    continue
                object_path = _required_text(
                    item.get("objectPath"), "SOURCE_UNKNOWN_OBJECT_PATH_MISSING"
                )
                class_path = str(item.get("assetClassPath") or "").strip()
                if not class_path:
                    class_path = f"MISSING_CLASS::{object_path}"
                cluster_members[class_path].append(
                    {
                        "packageName": package_name,
                        "objectPath": object_path,
                        "assetName": str(item.get("assetName") or ""),
                    }
                )

    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected["sha256"]:
        raise UnknownClusterBuildError("SOURCE_TAXONOMY_SHA256_MISMATCH")
    if byte_count != expected["bytes"]:
        raise UnknownClusterBuildError("SOURCE_TAXONOMY_BYTES_MISMATCH")
    if line_count != expected["lines"]:
        raise UnknownClusterBuildError("SOURCE_TAXONOMY_LINES_MISMATCH")
    unknown_object_count = sum(len(members) for members in cluster_members.values())
    if unknown_object_count != expected["unknownObjects"]:
        raise UnknownClusterBuildError("SOURCE_UNKNOWN_OBJECT_COUNT_MISMATCH")
    return (
        cluster_members,
        definitions_by_class,
        definitions_by_package,
        {
            "sha256": actual_sha256,
            "bytes": byte_count,
            "lines": line_count,
            "packageCount": len(package_names),
            "unknownObjectCount": unknown_object_count,
        },
    )


def _definition_for_class(
    class_path: str,
    definitions_by_class: Mapping[str, dict[str, object]],
    definitions_by_package: Mapping[str, dict[str, object]],
) -> dict[str, object] | None:
    exact = definitions_by_class.get(class_path)
    if exact is not None:
        return exact
    package_name = _generated_definition_package(class_path)
    return None if package_name is None else definitions_by_package.get(package_name)


_CONFIRMED_ROLE_CATEGORIES = {
    "ACTOR_COMPONENT": ("ACTOR_COMPONENT", "Actor 与角色组件"),
    "CREATURE": ("CREATURE_CHARACTER", CATEGORY_LABELS_ZH["CREATURE_CHARACTER"]),
    "GAMEPLAY_BUFF": ("GAMEPLAY_EFFECT_BUFF", "游戏效果与 Buff"),
    "GAMEPLAY_INVENTORY": ("INVENTORY_SYSTEM", "库存与容器系统"),
    "GAMEPLAY_ITEM": ("GAMEPLAY_ITEM", "物品定义"),
    "STRUCTURE": ("STRUCTURE_BUILDING", CATEGORY_LABELS_ZH["STRUCTURE_BUILDING"]),
    "USER_INTERFACE": ("USER_INTERFACE", CATEGORY_LABELS_ZH["USER_INTERFACE"]),
    "WEAPON": ("ITEM_WEAPON_PICKUP", CATEGORY_LABELS_ZH["ITEM_WEAPON_PICKUP"]),
}


def _propagated_semantics(
    definition: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    if definition is None:
        return []
    result: list[dict[str, object]] = []
    for anchor in definition.get("confirmedSemanticAnchors", []):
        if not isinstance(anchor, Mapping):
            continue
        role = str(anchor.get("role") or "").strip()
        if not role:
            continue
        row = dict(anchor)
        row["role"] = role
        row["status"] = "CONFIRMED"
        row["evidenceKind"] = "EXISTING_DEFINITION_PACKAGE_CONFIRMED_ANCHOR"
        row["propagationScope"] = "CLASS_LEVEL_ONLY"
        result.append(row)
    return sorted(result, key=_canonical_json)


def _confirmed_functional_categories(
    propagated: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for anchor in propagated:
        role = str(anchor.get("role") or "")
        category = _CONFIRMED_ROLE_CATEGORIES.get(role)
        if category is None:
            continue
        code, label_zh = category
        rows[code] = {
            "code": code,
            "labelZh": label_zh,
            "status": "CONFIRMED",
            "confidence": "HIGH",
            "evidenceKind": "EXISTING_DEFINITION_PACKAGE_CONFIRMED_ANCHOR",
            "sourceSemanticRole": role,
        }
    return [rows[code] for code in sorted(rows)]


def _representative(
    class_path: str,
    members: list[dict[str, str]],
    definition: Mapping[str, object] | None,
) -> dict[str, object]:
    if definition is not None:
        return {
            "targetKind": "CLASS_DEFINITION",
            "targetPath": str(definition["primaryObjectPath"]),
            "packageName": str(definition["packageName"]),
            "selectionEvidence": "EXACT_GENERATED_CLASS_DEFINITION_LINK",
        }
    if class_path.startswith("/Script/"):
        return {
            "targetKind": "NATIVE_CLASS",
            "targetPath": class_path,
            "packageName": None,
            "selectionEvidence": "EXACT_NATIVE_CLASS_PATH",
        }
    member = members[0]
    return {
        "targetKind": "PLACEMENT_FALLBACK",
        "targetPath": member["objectPath"],
        "packageName": member["packageName"],
        "selectionEvidence": "NO_CLASS_DEFINITION_FOUND_DO_NOT_PROPAGATE",
    }


def _taxonomy_link(
    definition: Mapping[str, object] | None,
) -> dict[str, object]:
    if definition is None:
        return {
            "status": "NOT_FOUND",
            "technicalFamily": "UNKNOWN",
            "technicalLabelZh": TECHNICAL_FAMILY_LABELS_ZH["UNKNOWN"],
            "technicalKind": "UNMAPPED_EXACT_CLASS",
            "technicalKindLabelZh": TECHNICAL_KIND_LABELS_ZH[
                "UNMAPPED_EXACT_CLASS"
            ],
            "classificationConfidence": "UNKNOWN",
            "semanticStatus": "UNKNOWN",
        }
    family = str(definition["primaryTechnicalFamily"])
    kind = str(definition["primaryTechnicalKind"])
    return {
        "status": "EXACT_CLASS_DEFINITION_MATCH",
        "packageName": str(definition["packageName"]),
        "objectPath": str(definition["primaryObjectPath"]),
        "technicalFamily": family,
        "technicalLabelZh": _label(TECHNICAL_FAMILY_LABELS_ZH, family),
        "technicalKind": kind,
        "technicalKindLabelZh": _label(TECHNICAL_KIND_LABELS_ZH, kind),
        "classificationConfidence": str(definition["classificationConfidence"]),
        "semanticStatus": str(definition["semanticStatus"]),
        "semanticCandidates": list(definition.get("semanticCandidates", [])),
    }


def _build_exact_rows(
    cluster_members: Mapping[str, list[dict[str, str]]],
    definitions_by_class: Mapping[str, dict[str, object]],
    definitions_by_package: Mapping[str, dict[str, object]],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    exact_rows: list[dict[str, object]] = []
    membership_rows: list[dict[str, object]] = []
    queue_seed_rows: list[dict[str, object]] = []

    for class_path in sorted(cluster_members):
        members = sorted(
            cluster_members[class_path],
            key=lambda item: (
                item["packageName"],
                item["objectPath"],
                item["assetName"],
            ),
        )
        cluster_id = _cluster_id(class_path)
        definition = _definition_for_class(
            class_path, definitions_by_class, definitions_by_package
        )
        representative = _representative(class_path, members, definition)
        propagated = _propagated_semantics(definition)
        confirmed_categories = _confirmed_functional_categories(propagated)
        candidate_categories = _category_candidates(class_path, definition)
        row = {
            "schema": EXACT_CLUSTER_ROW_SCHEMA,
            "clusterId": cluster_id,
            "safeDeduplicationKey": {
                "kind": "EXACT_ASSET_CLASS_PATH",
                "value": class_path,
            },
            "exactClassPath": class_path,
            "classLeaf": _class_leaf(class_path),
            "classSourceKind": (
                "NATIVE_CLASS"
                if class_path.startswith("/Script/")
                else "GENERATED_CLASS"
                if _generated_definition_package(class_path) is not None
                else "OTHER_OR_MISSING_CLASS"
            ),
            "memberObjectCount": len(members),
            "memberPackageCount": len({item["packageName"] for item in members}),
            "representative": representative,
            "existingTaxonomyLink": _taxonomy_link(definition),
            "confirmedFunctionalClassifications": confirmed_categories,
            "categoryCandidates": candidate_categories,
            "propagatedClassSemantics": propagated,
            "propagationScope": "CLASS_LEVEL_ONLY",
            "notPropagated": list(NOT_PROPAGATED),
            "memberExamples": [item["objectPath"] for item in members[:3]],
        }
        exact_rows.append(row)
        for member in members:
            membership_rows.append(
                {
                    "schema": MEMBERSHIP_ROW_SCHEMA,
                    "clusterId": cluster_id,
                    "exactClassPath": class_path,
                    "packageName": member["packageName"],
                    "objectPath": member["objectPath"],
                    "assetName": member["assetName"],
                    "membershipEvidence": "EXACT_ASSET_CLASS_PATH_EQUALITY",
                    "propagationScope": "CLASS_LEVEL_ONLY",
                }
            )
        queue_seed_rows.append(row)

    membership_rows.sort(
        key=lambda item: (
            str(item["exactClassPath"]),
            str(item["packageName"]),
            str(item["objectPath"]),
        )
    )
    return exact_rows, membership_rows, queue_seed_rows


def _numeric_name_template(class_path: str) -> tuple[str, str, str] | None:
    package_name = _generated_definition_package(class_path)
    if package_name is None or "/" not in package_name:
        return None
    parent, leaf = package_name.rsplit("/", 1)
    tokens = leaf.split("_")
    normalized = ["{N}" if token.isdigit() else token for token in tokens]
    if normalized == tokens:
        return None
    template = "_".join(normalized)
    key = f"{parent.casefold()}/{template.casefold()}"
    return key, parent, template


def _build_name_families(
    exact_rows: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    exact_rows = list(exact_rows)
    grouped: dict[str, dict[str, object]] = {}
    for row in exact_rows:
        class_path = str(row["exactClassPath"])
        normalized = _numeric_name_template(class_path)
        if normalized is None:
            continue
        key, parent, template = normalized
        group = grouped.setdefault(
            key,
            {
                "parentPackagePath": parent,
                "normalizedClassLeafTemplate": template,
                "clusters": [],
            },
        )
        clusters = group["clusters"]
        assert isinstance(clusters, list)
        clusters.append(
            {
                "clusterId": row["clusterId"],
                "exactClassPath": class_path,
                "memberObjectCount": row["memberObjectCount"],
            }
        )

    result: list[dict[str, object]] = []
    for key in sorted(grouped):
        group = grouped[key]
        clusters = sorted(
            group["clusters"], key=lambda item: str(item["exactClassPath"])
        )
        if len(clusters) < 2:
            continue
        result.append(
            {
                "schema": NAME_FAMILY_ROW_SCHEMA,
                "familyId": _family_id(key),
                "status": "CANDIDATE_ONLY",
                "approvedForSafeDedupe": False,
                "normalizationRule": "SAME_PARENT_AND_UNDERSCORE_DELIMITED_NUMERIC_TOKEN",
                "parentPackagePath": group["parentPackagePath"],
                "normalizedClassLeafTemplate": group[
                    "normalizedClassLeafTemplate"
                ],
                "exactClusterCount": len(clusters),
                "memberObjectCount": sum(
                    int(item["memberObjectCount"]) for item in clusters
                ),
                "exactClusters": clusters,
                "reviewBoundaryZh": (
                    "仅提示名称族；数字后缀可能代表版本、尺寸或行为差异，"
                    "不得据此合并精确类或传播结论。"
                ),
            }
        )

    role_stems = (
        "ExplorerChest_",
        "MissionServerSidePoint_",
        "MissionTrigger_",
        "NPCZoneManagerBlueprint_",
        "BPStructure_Steamboat_Willie_",
    )
    role_groups: dict[str, dict[str, object]] = {}
    for row in exact_rows:
        class_path = str(row["exactClassPath"])
        leaf = _class_leaf(class_path)
        prefix = next((item for item in role_stems if leaf.startswith(item)), None)
        if prefix is None or leaf == prefix:
            continue
        package_name = _generated_definition_package(class_path)
        parent_path = (
            "NATIVE_OR_UNKNOWN"
            if package_name is None or "/" not in package_name
            else package_name.rsplit("/", 1)[0]
        )
        key = f"role-stem:{prefix.casefold()}"
        group = role_groups.setdefault(
            key,
            {
                "prefix": prefix,
                "parentPackagePaths": set(),
                "clusters": [],
            },
        )
        parent_paths = group["parentPackagePaths"]
        clusters = group["clusters"]
        assert isinstance(parent_paths, set)
        assert isinstance(clusters, list)
        parent_paths.add(parent_path)
        clusters.append(
            {
                "clusterId": row["clusterId"],
                "exactClassPath": class_path,
                "memberObjectCount": row["memberObjectCount"],
            }
        )

    for key in sorted(role_groups):
        group = role_groups[key]
        clusters = sorted(
            group["clusters"], key=lambda item: str(item["exactClassPath"])
        )
        if len(clusters) < 2:
            continue
        parent_paths = sorted(group["parentPackagePaths"])
        result.append(
            {
                "schema": NAME_FAMILY_ROW_SCHEMA,
                "familyId": _family_id(key),
                "status": "CANDIDATE_ONLY",
                "approvedForSafeDedupe": False,
                "normalizationRule": "ROLE_STEM_PREFIX",
                "parentPackagePath": (
                    parent_paths[0]
                    if len(parent_paths) == 1
                    else "MULTIPLE_PARENT_PATHS"
                ),
                "parentPackagePaths": parent_paths,
                "normalizedClassLeafTemplate": f"{group['prefix']}{{VARIANT}}",
                "exactClusterCount": len(clusters),
                "memberObjectCount": sum(
                    int(item["memberObjectCount"]) for item in clusters
                ),
                "exactClusters": clusters,
                "navigationOnly": True,
                "reviewBoundaryZh": (
                    "共同角色前缀仅压缩展示与首轮抽样导航；同族默认值、引用和"
                    "行为可能不同，不得合并内容证据。"
                ),
            }
        )
    return sorted(result, key=lambda item: str(item["familyId"]))


def _queue_action(row: Mapping[str, object]) -> tuple[int, str, str]:
    representative = _require_mapping(
        row.get("representative"), "INTERNAL_REPRESENTATIVE_INVALID"
    )
    target_kind = representative.get("targetKind")
    if target_kind == "CLASS_DEFINITION":
        reason = (
            "已有分类只用于内容读取导航，不能代替这个精确资产的正式内容证据。"
            if row.get("propagatedClassSemantics")
            else "读取定义包，不读取 World Partition GUID placement。"
        )
        return (
            0,
            "READ_BLUEPRINT_CLASS_DEFINITION",
            reason,
        )
    if target_kind == "NATIVE_CLASS":
        return (
            1,
            "ROUTE_NATIVE_CLASS_EVIDENCE",
            "原生类无 Blueprint 定义包；转类层级、反射或 Native Evidence。",
        )
    return (
        2,
        "MANUAL_IDENTITY_REVIEW",
        "未找到可验证定义包，placement 仅作身份复核，禁止类级传播。",
    )


def _build_deep_read_queue(
    rows: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    ranked: list[tuple[tuple[object, ...], Mapping[str, object], str, str]] = []
    for row in rows:
        priority, action, reason = _queue_action(row)
        key = (
            priority,
            -int(row["memberObjectCount"]),
            str(row["exactClassPath"]),
        )
        ranked.append((key, row, action, reason))
    ranked.sort(key=lambda item: item[0])

    result: list[dict[str, object]] = []
    for rank, (_, row, action, reason) in enumerate(ranked, 1):
        representative = _require_mapping(
            row.get("representative"), "INTERNAL_REPRESENTATIVE_INVALID"
        )
        target_path = str(representative["targetPath"])
        result.append(
            {
                "schema": DEEP_READ_QUEUE_ROW_SCHEMA,
                "queueRank": rank,
                "clusterId": row["clusterId"],
                "exactClassPath": row["exactClassPath"],
                "memberObjectCount": row["memberObjectCount"],
                "targetKind": representative["targetKind"],
                "targetPath": target_path,
                "recommendedAction": action,
                "reasonZh": reason,
                "confirmedFunctionalClassifications": row[
                    "confirmedFunctionalClassifications"
                ],
                "categoryCandidates": row["categoryCandidates"],
                "captureIsolationKey": (
                    str(row["clusterId"])
                    + "-"
                    + hashlib.sha256(target_path.encode("utf-8")).hexdigest()[:16]
                ),
                "requiredPropagationScope": "CLASS_LEVEL_ONLY",
                "acceptanceGate": [
                    "FRESH_CAPTURE_ROOT_PER_TARGET",
                    "EVIDENCE_STORE_VALIDATION_PASS",
                    "NO_HIDDEN_FAILED_GRAPH",
                    "NO_HEURISTIC_TO_CONFIRMED_PROMOTION",
                ],
            }
        )
    return result


def _build_stratified_sample_plan(
    exact_rows: Iterable[Mapping[str, object]],
    name_families: Iterable[Mapping[str, object]],
    queue_rows: Iterable[Mapping[str, object]],
    *,
    samples_per_category: int = 2,
) -> dict[str, object]:
    queue_by_cluster = {str(row["clusterId"]): row for row in queue_rows}
    families_by_cluster: dict[str, set[str]] = defaultdict(set)
    for family in name_families:
        family_id = str(family["familyId"])
        for cluster in family.get("exactClusters", []):
            if isinstance(cluster, Mapping):
                families_by_cluster[str(cluster["clusterId"])].add(family_id)

    groups: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in exact_rows:
        categories = row.get("confirmedFunctionalClassifications") or row.get(
            "categoryCandidates"
        )
        for category in categories or []:
            if not isinstance(category, Mapping):
                continue
            key = (
                str(category["code"]),
                str(category["labelZh"]),
                str(category["status"]),
            )
            groups[key].append(row)

    selected: dict[str, dict[str, object]] = {}
    coverage_groups: set[tuple[str, str, str]] = set()
    target_priority = {
        "CLASS_DEFINITION": 0,
        "NATIVE_CLASS": 1,
        "PLACEMENT_FALLBACK": 2,
    }
    for key in sorted(groups):
        candidates = sorted(
            groups[key],
            key=lambda row: (
                target_priority.get(
                    str(row["representative"]["targetKind"]), 9
                ),
                -int(row["memberObjectCount"]),
                str(row["exactClassPath"]),
            ),
        )
        chosen_for_group: list[str] = []
        chosen_families: set[str] = set()
        while len(chosen_for_group) < samples_per_category:
            remaining = [
                row
                for row in candidates
                if str(row["clusterId"]) not in chosen_for_group
            ]
            if not remaining:
                break
            diverse = [
                row
                for row in remaining
                if not (
                    families_by_cluster[str(row["clusterId"])] & chosen_families
                )
            ]
            chosen = (diverse or remaining)[0]
            cluster_id = str(chosen["clusterId"])
            chosen_for_group.append(cluster_id)
            chosen_families.update(families_by_cluster[cluster_id])
            coverage_groups.add(key)
            coverage = {
                "code": key[0],
                "labelZh": key[1],
                "status": key[2],
            }
            existing = selected.get(cluster_id)
            if existing is not None:
                existing_groups = existing["coverageCategoryGroups"]
                assert isinstance(existing_groups, list)
                existing_groups.append(coverage)
                continue
            queue = queue_by_cluster[cluster_id]
            selected[cluster_id] = {
                "clusterId": cluster_id,
                "exactClassPath": chosen["exactClassPath"],
                "memberObjectCount": chosen["memberObjectCount"],
                "targetKind": queue["targetKind"],
                "targetPath": queue["targetPath"],
                "recommendedAction": queue["recommendedAction"],
                "captureIsolationKey": queue["captureIsolationKey"],
                "coverageCategoryGroups": [coverage],
                "candidateNameFamilyIds": sorted(families_by_cluster[cluster_id]),
                "selectionReasons": [
                    "CATEGORY_STRATUM",
                    "READABLE_TARGET_FIRST",
                    "POPULATION_WITHIN_TARGET_KIND",
                    "NAME_FAMILY_DIVERSITY_WHEN_AVAILABLE",
                ],
                "readStatus": "PLANNED_NOT_READ",
                "propagationScope": "CLASS_LEVEL_ONLY",
            }

    samples = sorted(
        selected.values(),
        key=lambda row: (
            str(row["coverageCategoryGroups"][0]["code"]),
            str(row["coverageCategoryGroups"][0]["status"]),
            -int(row["memberObjectCount"]),
            str(row["exactClassPath"]),
        ),
    )
    for index, sample in enumerate(samples, 1):
        sample["sampleIndex"] = index
    return {
        "schema": "ark.kb.asset-unknown-stratified-sample-plan.v1",
        "status": "PLANNED_NOT_READ",
        "selectionAlgorithm": "category-stratified-role-family-aware/v1",
        "samplesPerCategoryMaximum": samples_per_category,
        "coverage": {
            "categoryGroupsTotal": len(groups),
            "categoryGroupsCovered": len(coverage_groups),
            "uncoveredCategoryGroups": [
                {"code": key[0], "labelZh": key[1], "status": key[2]}
                for key in sorted(set(groups) - coverage_groups)
            ],
        },
        "sampleCount": len(samples),
        "samples": samples,
        "boundariesZh": [
            "这是待读计划，不代表资产内容已经读取。",
            "相似名称族首轮优先只取一个，但不会删除其余精确类目标。",
            "类级结论不得传播实例默认值、placement 或运行时行为。",
        ],
    }


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> int:
    count = 0
    with path.open("wb") as handle:
        for row in rows:
            handle.write(_canonical_line(row))
            count += 1
    return count


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _technical_distribution(
    source_manifest: Mapping[str, object],
) -> list[tuple[str, str, int]]:
    distributions = _require_mapping(
        source_manifest.get("distributions"), "SOURCE_DISTRIBUTIONS_INVALID"
    )
    families = _require_mapping(
        distributions.get("technicalFamily"),
        "SOURCE_TECHNICAL_FAMILY_DISTRIBUTION_INVALID",
    )
    result = []
    for code, raw_count in families.items():
        if isinstance(raw_count, bool):
            raise UnknownClusterBuildError(
                "SOURCE_TECHNICAL_FAMILY_DISTRIBUTION_INVALID"
            )
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            raise UnknownClusterBuildError(
                "SOURCE_TECHNICAL_FAMILY_DISTRIBUTION_INVALID"
            ) from None
        result.append((str(code), _label(TECHNICAL_FAMILY_LABELS_ZH, str(code)), count))
    return sorted(result, key=lambda item: (-item[2], item[0]))


def _category_distribution(
    exact_rows: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    cluster_counts: Counter[tuple[str, str, str]] = Counter()
    object_counts: Counter[tuple[str, str, str]] = Counter()
    for row in exact_rows:
        confirmed = row.get("confirmedFunctionalClassifications") or []
        categories = confirmed or row.get("categoryCandidates") or []
        for category in categories:
            if not isinstance(category, Mapping):
                continue
            key = (
                str(category.get("code")),
                str(category.get("labelZh")),
                str(category.get("status")),
            )
            cluster_counts[key] += 1
            object_counts[key] += int(row["memberObjectCount"])
    return [
        {
            "code": key[0],
            "labelZh": key[1],
            "status": key[2],
            "exactClusterCount": cluster_counts[key],
            "memberObjectCount": object_counts[key],
        }
        for key in sorted(
            cluster_counts,
            key=lambda item: (-object_counts[item], item[0], item[2]),
        )
    ]


def _render_review_report(
    exact_rows: list[dict[str, object]],
    name_families: list[dict[str, object]],
) -> str:
    lines = [
        "# UNKNOWN 精确类去重与候选归类复核表",
        "",
        "## 判定规则",
        "",
        "- 安全去重只采用完整 `assetClassPath` 完全相等；原始成员均保留在 membership 文件。",
        "- 相似名称族只用于挑选样本，状态恒为 `CANDIDATE_ONLY`，不会自动合并。",
        "- Blueprint 生成类优先读取定义包，不读取 World Partition GUID placement。",
        "- 类级结论不能传播实例默认值、坐标、DataLayer、实例引用或运行时行为。",
        "",
        "## 精确类簇",
        "",
        "| 类簇 | 数量 | 类路径 | 中文功能分类 | 状态 | 代表读取目标 |",
        "|---|---:|---|---|---|---|",
    ]
    for row in sorted(
        exact_rows,
        key=lambda item: (-int(item["memberObjectCount"]), str(item["exactClassPath"])),
    ):
        confirmed = row["confirmedFunctionalClassifications"]
        categories = confirmed or row["categoryCandidates"]
        labels = "、".join(str(item["labelZh"]) for item in categories)
        states = "、".join(str(item["status"]) for item in categories)
        representative = row["representative"]
        lines.append(
            "| {cluster} | {count} | `{class_path}` | {labels} | {states} | `{target}` |".format(
                cluster=_markdown_cell(row["clusterId"]),
                count=row["memberObjectCount"],
                class_path=_markdown_cell(row["exactClassPath"]),
                labels=_markdown_cell(labels),
                states=_markdown_cell(states),
                target=_markdown_cell(representative["targetPath"]),
            )
        )
    lines.extend(
        [
            "",
            "## 相似名称族（仅候选）",
            "",
            "| 名称族 | 模板 | 精确类数 | UNKNOWN 对象数 |",
            "|---|---|---:|---:|",
        ]
    )
    for row in name_families:
        lines.append(
            "| {family} | `{parent}/{template}` | {clusters} | {objects} |".format(
                family=_markdown_cell(row["familyId"]),
                parent=_markdown_cell(row["parentPackagePath"]),
                template=_markdown_cell(row["normalizedClassLeafTemplate"]),
                clusters=row["exactClusterCount"],
                objects=row["memberObjectCount"],
            )
        )
    lines.extend(
        [
            "",
            "> 名称相似不等于语义相同。例如数字可能代表关卡版本、尺寸、默认值或行为分支。",
            "",
        ]
    )
    return "\n".join(lines)


def _render_combined_report(
    source_manifest: Mapping[str, object],
    exact_rows: list[dict[str, object]],
    membership_rows: list[dict[str, object]],
    name_families: list[dict[str, object]],
    queue_rows: list[dict[str, object]],
    sample_plan: Mapping[str, object],
) -> str:
    technical = _technical_distribution(source_manifest)
    categories = _category_distribution(exact_rows)
    repeated = sum(int(row["memberObjectCount"]) > 1 for row in exact_rows)
    native = sum(row["classSourceKind"] == "NATIVE_CLASS" for row in exact_rows)
    linked = sum(
        row["existingTaxonomyLink"]["status"] == "EXACT_CLASS_DEFINITION_MATCH"
        for row in exact_rows
    )
    confirmed_objects = sum(
        int(row["memberObjectCount"])
        for row in exact_rows
        if row["confirmedFunctionalClassifications"]
    )
    lines = [
        "# ARK Dev 全库资产中文分类汇总（含 UNKNOWN 去重候选）",
        "",
        "## 一句话结论",
        "",
        (
            f"全库浅层技术分类中，UNKNOWN 有 {len(membership_rows):,} 个对象；"
            f"按完整虚幻类路径可安全压缩为 {len(exact_rows):,} 个类级代表，"
            f"其中 {repeated:,} 个是重复类簇。去重是复核视图，不删除任何原始记录。"
        ),
        "",
        "## 全库技术分类（中文）",
        "",
        "| 技术分类代码 | 中文分类 | 包数量 |",
        "|---|---|---:|",
    ]
    for code, label_zh, count in technical:
        lines.append(f"| `{_markdown_cell(code)}` | {_markdown_cell(label_zh)} | {count:,} |")
    lines.extend(
        [
            "",
            "这里的技术分类回答‘它是什么格式/引擎类型’，不等于回答‘它在游戏里做什么’。",
            "",
            "## UNKNOWN 本轮功能候选（中文）",
            "",
            "| 功能分类 | 状态 | 精确类簇 | 对应 UNKNOWN 对象 |",
            "|---|---|---:|---:|",
        ]
    )
    for row in categories:
        lines.append(
            f"| {_markdown_cell(row['labelZh'])} (`{_markdown_cell(row['code'])}`) "
            f"| {_markdown_cell(row['status'])} | {int(row['exactClusterCount']):,} "
            f"| {int(row['memberObjectCount']):,} |"
        )
    lines.extend(
        [
            "",
            "## 去重结果与证据层级",
            "",
            f"- UNKNOWN 原始对象：{len(membership_rows):,}。",
            f"- 完整 `assetClassPath` 精确类簇：{len(exact_rows):,}。",
            f"- 重复精确类簇：{repeated:,}；原生类：{native:,}；成功链接定义包：{linked:,}。",
            f"- 已由定义包 CONFIRMED 语义作类级回填：{confirmed_objects:,} 个 UNKNOWN 对象。",
            (
                f"- 相似名称候选族（数字 token + 角色前缀）："
                f"{len(name_families):,}；全部仅为人工复核/抽样导航。"
            ),
            "",
            "安全自动去重只承认完整类路径完全相等。高度相似的名称可以放进同一候选族，"
            "但仍保留每个精确类代表，因为 `_01`、`_02`、Small/Large、A/B/C 可能改变默认值或行为。",
            "",
            "## 选择性深读路线",
            "",
            f"深读队列包含 {len(queue_rows):,} 个精确类代表，每类仅一个目标。",
            (
                f"分类分层首轮计划包含 {int(sample_plan['sampleCount']):,} 个目标，"
                f"覆盖 {int(sample_plan['coverage']['categoryGroupsCovered']):,}/"
                f"{int(sample_plan['coverage']['categoryGroupsTotal']):,} 个分类/状态组；"
                "计划状态不等于已读取。"
            ),
            "生成类读取 Blueprint 定义包；`/Script/...` 原生类转反射、类层级或 Native Evidence；"
            "不读取 World Partition 的随机 GUID placement 来推断类行为。每个目标必须使用独立 fresh capture root，"
            "完成 evidence store 校验后才能引用。",
            "",
            "## 结论边界",
            "",
            "- `CONFIRMED` 只来自现有定义包的已确认语义锚点；名称规则一律是 `CANDIDATE`。",
            "- ‘探索宝箱 Actor’不能直接证明 LootItemSet 或掉落池；必须继续读取默认值与引用。",
            "- ‘补给箱刷新区域’是区域/刷点，不等于宝箱，也不等于奖励池。",
            "- ‘任务触发器/服务端点’可能只是任务支撑节点，不等于完整任务定义。",
            "- 同一原生 DataAsset 类只允许合并技术类型复核；例如 ProgressionTree 的 MT/ST、DLC、Tier 与 Repeatable 内容仍需分层抽样。",
            "- 类级结论不能传播实例默认值、实例坐标、DataLayer、地图归属、实例引用或运行时行为。",
            "- 当前输入只具备 Registry 技术证据；依赖拓扑、资产内容深读、类层级和运行时行为不在本报告的全库权威范围内。",
            "",
        ]
    )
    return "\n".join(lines)


def _ruleset() -> dict[str, object]:
    rules = {
        "version": RULESET_VERSION,
        "safeDeduplication": "EXACT_ASSET_CLASS_PATH_ONLY",
        "nameFamilyNormalization": "NUMERIC_TOKEN_OR_ROLE_STEM_CANDIDATE_ONLY",
        "nameFamiliesAuthorizeDedupe": False,
        "semanticPropagationScope": "CLASS_LEVEL_ONLY",
        "notPropagated": list(NOT_PROPAGATED),
        "categoryRuleIds": [
            "category.audio.v2",
            "category.camera.v2",
            "category.cinematic-control.v1",
            "category.creature-character.v2",
            "category.data-configuration.v2",
            "category.destructible-structure.v1",
            "category.editor-tool.v1",
            "category.environment-actor.v2",
            "category.explorer-chest.v2",
            "category.gameplay-buff.v1",
            "category.hazard-damage.v1",
            "category.item-weapon.v2",
            "category.lighting-weather.v2",
            "category.mission.v2",
            "category.mod-metadata.v2",
            "category.navigation.v1",
            "category.npc-manager-cave.v1",
            "category.npc-manager-land.v1",
            "category.npc-manager-water.v1",
            "category.packed-level-environment.v1",
            "category.portal-teleport.v2",
            "category.procedural-ecology.v2",
            "category.progression.v2",
            "category.resource-node.v2",
            "category.restriction-volume.v2",
            "category.reward-marker.v1",
            "category.reward-spawn-effect.v1",
            "category.reward-spawn-volume.v2",
            "category.server-side-point.v1",
            "category.spawn-zone.v2",
            "category.structure-data.v1",
            "category.structure.v2",
            "category.ui.v2",
            "category.unresolved.v2",
            "category.vfx.v2",
            "category.water.v2",
            "category.world-spline.v1",
            "category.world-terminal.v1",
        ],
    }
    return {
        **rules,
        "sha256": hashlib.sha256(_canonical_json(rules).encode("utf-8")).hexdigest(),
    }


def build_unknown_clusters(
    source_manifest_path: Path | str,
    output_directory: Path | str,
) -> dict[str, object]:
    """Build a deterministic, fail-closed UNKNOWN clustering publication."""

    manifest_path = Path(source_manifest_path).resolve()
    output = Path(output_directory).resolve()
    if output.exists():
        raise UnknownClusterBuildError("OUTPUT_ALREADY_EXISTS")
    source_manifest, taxonomy_path, expected = _validate_source_manifest(
        manifest_path
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    try:
        (
            cluster_members,
            definitions_by_class,
            definitions_by_package,
            source_verification,
        ) = _scan_taxonomy(taxonomy_path, expected)
        exact_rows, membership_rows, queue_seed_rows = _build_exact_rows(
            cluster_members, definitions_by_class, definitions_by_package
        )
        name_families = _build_name_families(exact_rows)
        queue_rows = _build_deep_read_queue(queue_seed_rows)
        sample_plan = _build_stratified_sample_plan(
            exact_rows, name_families, queue_rows
        )

        exact_path = staging / "unknown_exact_clusters.jsonl"
        membership_path = staging / "unknown_cluster_membership.jsonl"
        family_path = staging / "unknown_name_families.jsonl"
        queue_path = staging / "unknown_deep_read_queue.jsonl"
        sample_plan_path = staging / "unknown_stratified_sample_plan.json"
        review_path = staging / "unknown_cluster_review_zh.md"
        report_path = staging / "ark_asset_classification_summary_zh.md"

        _write_jsonl(exact_path, exact_rows)
        _write_jsonl(membership_path, membership_rows)
        _write_jsonl(family_path, name_families)
        _write_jsonl(queue_path, queue_rows)
        _write_json(sample_plan_path, sample_plan)
        review_path.write_text(
            _render_review_report(exact_rows, name_families),
            encoding="utf-8",
            newline="\n",
        )
        report_path.write_text(
            _render_combined_report(
                source_manifest,
                exact_rows,
                membership_rows,
                name_families,
                queue_rows,
                sample_plan,
            ),
            encoding="utf-8",
            newline="\n",
        )

        repeated_clusters = sum(
            int(row["memberObjectCount"]) > 1 for row in exact_rows
        )
        confirmed_objects = sum(
            int(row["memberObjectCount"])
            for row in exact_rows
            if row["confirmedFunctionalClassifications"]
        )
        files = {
            "exactClusters": _file_descriptor(exact_path),
            "membership": _file_descriptor(membership_path),
            "nameFamilies": _file_descriptor(family_path),
            "deepReadQueue": _file_descriptor(queue_path),
            "stratifiedSamplePlan": _file_descriptor(sample_plan_path),
            "reviewZh": _file_descriptor(review_path),
            "combinedReportZh": _file_descriptor(report_path),
        }
        result = {
            "schema": UNKNOWN_CLUSTER_MANIFEST_SCHEMA,
            "status": "COMPLETE",
            "publicationEligible": bool(source_manifest.get("publicationEligible")),
            "authorityScope": "REGISTRY_EXACT_CLASS_IDENTITY_AND_CLASS_LEVEL_ONLY",
            "sourceBinding": {
                "status": "VERIFIED_MANIFEST",
                "sourceManifestSchema": source_manifest.get("schema"),
                "sourceTaxonomyFingerprint": expected["fingerprint"],
                "sourcePackageTaxonomy": source_verification,
                "sourcePublicationEligible": bool(
                    source_manifest.get("publicationEligible")
                ),
            },
            "counts": {
                "unknownObjects": len(membership_rows),
                "exactClassClusters": len(exact_rows),
                "repeatedExactClassClusters": repeated_clusters,
                "singletonExactClassClusters": len(exact_rows) - repeated_clusters,
                "safeClassLevelRepresentatives": len(exact_rows),
                "generatedClassDefinitionsLinked": sum(
                    row["representative"]["targetKind"] == "CLASS_DEFINITION"
                    for row in exact_rows
                ),
                "nativeClassClusters": sum(
                    row["classSourceKind"] == "NATIVE_CLASS" for row in exact_rows
                ),
                "confirmedClassLevelObjects": confirmed_objects,
                "candidateNameFamilies": len(name_families),
                "deepReadTargets": len(queue_rows),
                "stratifiedSampleTargets": int(sample_plan["sampleCount"]),
            },
            "ruleset": _ruleset(),
            "files": files,
            "warnings": [
                "Name families are CANDIDATE_ONLY and never authorize safe deduplication.",
                "Category name rules are candidates, not content-read confirmations.",
                "Class-level conclusions do not propagate instance defaults, placement, references, or runtime behavior.",
                "Native DataAsset exact-class deduplication covers type review only, not per-object content.",
            ],
        }
        _write_json(staging / "unknown_cluster_manifest.json", result)
        os.replace(staging, output)
        return result
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
