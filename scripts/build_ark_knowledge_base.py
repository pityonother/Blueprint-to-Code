from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.asset_ledger import (  # noqa: E402
    annotate_scan_item,
    fingerprint_for_scan_item,
    read_ledger_snapshot,
    replace_deferred_assets,
    restore_ledger_snapshot,
)
from blueprint_translator.devkit_paths import devkit_content_roots  # noqa: E402
from blueprint_translator.uasset_graphs import (  # noqa: E402
    current_uasset_graph_payload_files,
)
from blueprint_translator.evidence_repository import (  # noqa: E402
    open_asset_repository,
    resolve_asset_evidence_state,
)
from blueprint_translator.evidence_values import (  # noqa: E402
    default_value_is_usable,
    downstream_default_metadata,
)
from import_captures_to_knowledge_dbs import (  # noqa: E402
    import_captures_to_business_databases,
)


DEFAULT_FOCUS = "gigantoraptor"
DEFAULT_ASSETS = [
    "Gigantoraptor_Character_BP",
    "PrimalItemResource_GigantoraptorFeather",
    "Buff_GigantoraptorCallPlayer",
]

FIXED_PRIORITY_OBJECT_PATHS = {
    "/Game/ASA/Dinos/Gigantoraptor/PrimalItemResource_GigantoraptorFeather.PrimalItemResource_GigantoraptorFeather",
    "/Game/ASA/Dinos/ShoulderDragon/Chest/PrimalItem_TreasureMap_ShoulderDragon.PrimalItem_TreasureMap_ShoulderDragon",
    "/Game/ASA/Dinos/ShoulderDragon/Chest/Buff_PingTreasureLocationPOI_ShoulderDragon.Buff_PingTreasureLocationPOI_ShoulderDragon",
    "/Game/ASA/Dinos/ShoulderDragon/Chest/SupplyCrate_BuriedTreasure_ShoulderDragon.SupplyCrate_BuriedTreasure_ShoulderDragon",
    "/Game/ASA/Dinos/ShoulderDragon/Buff_ShoulderDragonXPTaming.Buff_ShoulderDragonXPTaming",
    "/Game/Packs/Frontier/Structures/TreasureCache/SupplyCrate/SupplyCrate_BuriedTreasureBase.SupplyCrate_BuriedTreasureBase",
    "/Game/Packs/Frontier/Structures/TreasureCache/SupplyCrate/SupplyCrate_BuriedTreasureBase_Primitive.SupplyCrate_BuriedTreasureBase_Primitive",
    "/Game/Packs/Frontier/Structures/TreasureCache/SupplyCrate/SupplyCrate_BuriedTreasureBase_Journeyman.SupplyCrate_BuriedTreasureBase_Journeyman",
    "/Game/Packs/Frontier/Structures/TreasureCache/SupplyCrate/SupplyCrate_BuriedTreasureBase_Ascendant.SupplyCrate_BuriedTreasureBase_Ascendant",
    "/Game/PrimalEarth/CoreBlueprints/DinoCharacterStatusComponent_BP.DinoCharacterStatusComponent_BP",
}
FIXED_PRIORITY_ASSET_NAMES = {
    "PrimalItemResource_GigantoraptorFeather",
    "Buff_Archelon_BabyDryLand_ASA",
    "Buff_HiddenInNest",
    "Buff_YiLingPoison",
    "Buff_YiLingFeathered",
    "PrimalItem_TreasureMap_ShoulderDragon",
    "Buff_PingTreasureLocationPOI_ShoulderDragon",
    "SupplyCrate_BuriedTreasure_ShoulderDragon",
    "Buff_ShoulderDragonXPTaming",
    "SupplyCrate_BuriedTreasureBase",
    "SupplyCrate_BuriedTreasureBase_Primitive",
    "SupplyCrate_BuriedTreasureBase_Journeyman",
    "SupplyCrate_BuriedTreasureBase_Ascendant",
    "DinoCharacterStatusComponent_BP",
}
RELATED_PRIORITY_REFERENCE_TABLES = (
    "asset_references",
    "item_references",
    "buff_references",
    "loot_references",
)
RELATED_PRIORITY_FORMULA_TABLES = (
    ("formula_candidates", "next_probe_json", "formula_candidates.next_probe"),
    ("unresolved_formulas", "required_next_probe_json", "unresolved_formulas.required_next_probe"),
)
UE_OBJECT_PATH_PATTERN = re.compile(r"/Game/[A-Za-z0-9_./]+")
KEYWORD_GROUPS = {
    "feather_inheritance": [
        "Feather",
        "Inherit",
        "LevelUp",
        "Distribution",
        "DistributionForMaxWeight",
        "InheritStatWeightMinMax",
        "MaxExtraStatWeight",
        "GetFeatherCustomData",
        "GetDinoStatDistributionAgainstMax",
        "ConvertIntToCharacterStatusEnum",
    ],
    "baby_training": [
        "Baby",
        "Training",
        "Maturation",
        "Imprint",
        "Passenger",
        "Saddle",
        "Call",
    ],
    "bonding_buff": [
        "Bond",
        "Buff",
        "Stack",
        "Pride",
        "Parent",
        "ShareImprinter",
    ],
    "nest_taming": [
        "Nest",
        "Taming",
        "Claim",
        "MultiUse",
        "SpawnNest",
        "HiddenInNest",
    ],
    "xp_treasure": [
        "Experience",
        "Treasure",
        "StoredXP",
        "KillXP",
        "MinStored",
        "MaxStored",
    ],
    "archelon_algae_cycle": [
        "Algae",
        "CollectAlgae",
        "GetAlgaePercentage",
        "SetAlgaePercentage",
        "UpdateAlgaeMaterial",
        "AlgaeGrowthInterval",
        "GenerateAlgaeInterval",
        "SwimmingAlgaeInterval_Multi",
        "ARKTransferAlgaePercentage",
        "LastTimeHarvested",
        "LastTimeSwimming_Algae",
    ],
    "archelon_swim_and_raft": [
        "Swim",
        "Swimming",
        "Raft",
        "CheckRaftMode",
        "CheckFullyInWater",
        "BPOverrideCharacterNewSwimVelocity",
        "BP_InterceptMoveForward",
        "BPModifyDesiredRotation",
        "bIsInRaftMode",
        "bIsFullyInWater",
        "bWaterVolume",
        "Buoyancy",
        "GravityScale",
    ],
    "archelon_multiuse_inventory": [
        "MultiUse",
        "BPGetMultiUseEntries",
        "BPTryMultiUse",
        "MakeUseEntryString",
        "CollectAlgae",
        "AddNewItem",
        "MyInventoryComponent",
        "AlgaeQuantity",
    ],
    "archelon_jellyfish_damage": [
        "Jellyfish",
        "IsJellyfish",
        "JellyfishBuffClass",
        "BlueprintAdjustOutputDamage",
        "BPAdjustDamage",
        "BPModifyHarvestingQuantity",
        "BPKilledSomethingEvent",
        "outputDamageMultiplierJellyfish",
        "receivedDamageMultiplierInsects",
        "finalDamage",
        "finalAmount",
    ],
}

EVIDENCE_KEYWORDS = sorted(
    {
        word
        for words in KEYWORD_GROUPS.values()
        for word in words
    }
    | {
        "羽毛",
        "宝箱",
        "经验",
        "继承",
        "属性",
        "繁殖",
        "驯养",
        "藻类",
        "水母",
        "木筏",
        "游泳",
        "采集",
        "收益",
        "native",
    }
)

KISMET_PREFIXES = (
    "Add_",
    "Array_",
    "Break",
    "Conv_",
    "Divide_",
    "EqualEqual_",
    "FFloor",
    "FCeil",
    "Greater_",
    "Less_",
    "Make",
    "Max",
    "Min",
    "Multiply_",
    "Not_",
    "Random",
    "Select",
    "Subtract_",
)

UTILITY_PREFIXES = (
    "Boolean",
    "Concat_",
    "GetGameTimeInSeconds",
    "GetNetworkTimeInSeconds",
    "IsValid",
    "K2_",
    "MapRange",
    "SelectFloat",
    "SelectString",
)

PRIORITY_NATIVE_KEYWORDS = (
    "Baby",
    "Buff",
    "Claim",
    "Dino",
    "Experience",
    "Feather",
    "Imprint",
    "Level",
    "Nest",
    "Stat",
    "Taming",
    "XP",
    "Algae",
    "Jellyfish",
    "MultiUse",
    "Raft",
    "Swim",
)

DEEP_READ_GROUPS = {
    "primal_game_data": {
        "title": "PrimalGameData：全局规则、资源注册、物品/生物入口",
        "asset_types": {"primal_game_data"},
        "keywords": {
            "PrimalGameData": 40,
            "PrimalGameData_BP": 60,
            "BASE": 20,
            "CORE": 20,
            "CoreBlueprints": 25,
            "ASA": 10,
        },
        "first_batch_limit": 15,
        "include_captured_in_queue": True,
        "limit": 60,
    },
    "status_component_blueprint": {
        "title": "StatusComponent：生物属性、成长、经验和状态值",
        "asset_types": {"status_component_blueprint"},
        "queue_include_asset_names": {"PlayerCharacterStatusComponent_BP", "DinoCharacterStatusComponent_BP"},
        "defer_name_patterns": {
            r"StatusComponent_BP_.+",
            r"^DinoCharacterStatusComponent_(?!BP$).+",
        },
        "defer_reason": "单个生物 StatusComponent，留给后续全生物扫描",
        "keywords": {
            "PlayerCharacterStatusComponent": 90,
            "StatusComponent": 20,
            "Baby": 25,
            "Experience": 35,
            "XP": 35,
            "Level": 30,
            "Imprint": 30,
            "Maturation": 25,
            "Taming": 25,
        },
        "first_batch_limit": 1,
        "include_captured_in_queue": True,
        "generic_path_bonus": False,
        "limit": 120,
    },
    "primal_item_blueprint": {
        "title": "PrimalItem：物品描述、使用逻辑、消耗与显示数据",
        "asset_types": {"primal_item_blueprint"},
        "include_asset_names": {"PrimalItem_TreasureMap_ShoulderDragon", "PrimalItemResource_GigantoraptorFeather"},
        "exclude_keywords": {"Egg", "Saddle", "Costume", "Skin", "Chibi"},
        "keywords": {
            "Treasure": 50,
            "Experience": 35,
            "XP": 35,
            "Resource": 20,
            "Consumable": 20,
            "Artifact": 15,
        },
        "first_batch_limit": 1,
        "include_captured_in_queue": True,
        "limit": 20,
    },
    "buff_blueprint": {
        "title": "Buff：临时效果、训练、继承概率、状态覆盖",
        "asset_types": {"buff_blueprint"},
        "keywords": {
            "Gigantoraptor": 90,
            "Baby": 40,
            "Call": 30,
            "Training": 45,
            "Imprint": 45,
            "Feather": 50,
            "Stat": 40,
            "Claim": 35,
            "Taming": 35,
            "Parent": 25,
            "Pride": 25,
        },
        "first_batch_limit": "all",
        "include_captured_in_queue": True,
        "limit": 160,
    },
    "loot_or_supply_crate": {
        "title": "Loot/SupplyCrate：宝箱、掉落和奖励池",
        "asset_types": {"loot_or_supply_crate"},
        "keywords": {
            "Treasure": 80,
            "Loot": 45,
            "SupplyCrate": 45,
            "Reward": 35,
            "Experience": 35,
            "XP": 35,
            "Gigantoraptor": 50,
            "ItemSet": 25,
            "Quality": 20,
        },
        "first_batch_limit": "all",
        "include_captured_in_queue": True,
        "limit": 160,
    },
}

KNOWLEDGE_ASSET_TYPES = {
    "primal_game_data",
    "creature_character_blueprint",
    "status_component_blueprint",
    "primal_item_blueprint",
    "buff_blueprint",
    "loot_or_supply_crate",
    "engram_entry",
}

BUSINESS_DATABASES = {
    "primal_game_data": {
        "filename": "primal_game_data.sqlite",
        "asset_table": "game_data_assets",
        "asset_types": {"primal_game_data"},
        "tables": {
            "game_data_rules": """
                rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                rule_key TEXT NOT NULL,
                rule_value TEXT NOT NULL DEFAULT '',
                value_type TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "registered_creatures": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                creature_path TEXT NOT NULL,
                creature_name TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "registered_items": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                item_path TEXT NOT NULL,
                item_name TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "registered_buffs": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                buff_path TEXT NOT NULL,
                buff_name TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "registered_loot": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                loot_path TEXT NOT NULL,
                loot_name TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "remaps": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                remap_type TEXT NOT NULL,
                from_path TEXT NOT NULL DEFAULT '',
                to_path TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "game_data_references": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                reference_path TEXT NOT NULL,
                reference_type TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
        },
    },
    "status_component_blueprint": {
        "filename": "status_components.sqlite",
        "asset_table": "status_assets",
        "asset_types": {"status_component_blueprint"},
        "tables": {
            "status_values": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                stat_name TEXT NOT NULL,
                base_value TEXT NOT NULL DEFAULT '',
                per_level_value TEXT NOT NULL DEFAULT '',
                value_type TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "leveling_rules": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                rule_key TEXT NOT NULL,
                rule_value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "growth_rules": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                rule_key TEXT NOT NULL,
                rule_value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "taming_status_rules": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                rule_key TEXT NOT NULL,
                rule_value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "creature_status_links": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creature_object_path TEXT NOT NULL,
                status_object_path TEXT NOT NULL,
                link_source TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "deferred_creature_status": """
                object_path TEXT PRIMARY KEY,
                asset_name TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL DEFAULT ''
            """,
        },
    },
    "primal_item_blueprint": {
        "filename": "primal_items.sqlite",
        "asset_table": "item_assets",
        "asset_types": {"primal_item_blueprint"},
        "tables": {
            "item_display": """
                object_path TEXT PRIMARY KEY,
                item_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                icon_path TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "item_properties": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                property_name TEXT NOT NULL,
                property_value TEXT NOT NULL DEFAULT '',
                value_type TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "item_use_logic": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                trigger_name TEXT NOT NULL,
                effect_summary TEXT NOT NULL DEFAULT '',
                source_graph TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "item_crafting_costs": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                ingredient_path TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "item_grants": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                grant_type TEXT NOT NULL,
                grant_path TEXT NOT NULL DEFAULT '',
                grant_value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "item_references": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                reference_path TEXT NOT NULL,
                reference_type TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
        },
    },
    "buff_blueprint": {
        "filename": "buffs.sqlite",
        "asset_table": "buff_assets",
        "asset_types": {"buff_blueprint"},
        "tables": {
            "buff_effects": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                effect_key TEXT NOT NULL,
                effect_value TEXT NOT NULL DEFAULT '',
                duration TEXT NOT NULL DEFAULT '',
                interval TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "buff_triggers": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                trigger_name TEXT NOT NULL,
                graph_name TEXT NOT NULL DEFAULT '',
                function_name TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "buff_conditions": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                condition_key TEXT NOT NULL,
                condition_value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "buff_stacks": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                stack_key TEXT NOT NULL,
                stack_value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "buff_stat_modifiers": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                stat_name TEXT NOT NULL,
                operation TEXT NOT NULL DEFAULT '',
                value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "buff_references": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                reference_path TEXT NOT NULL,
                reference_type TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
        },
    },
    "loot_or_supply_crate": {
        "filename": "loot.sqlite",
        "asset_table": "loot_assets",
        "asset_types": {"loot_or_supply_crate"},
        "tables": {
            "loot_crates": """
                object_path TEXT PRIMARY KEY,
                crate_type TEXT NOT NULL DEFAULT '',
                quality_min TEXT NOT NULL DEFAULT '',
                quality_max TEXT NOT NULL DEFAULT '',
                level_requirement TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "loot_item_sets": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                set_name TEXT NOT NULL DEFAULT '',
                set_weight TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "loot_entries": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                item_path TEXT NOT NULL DEFAULT '',
                entry_weight TEXT NOT NULL DEFAULT '',
                quantity_min TEXT NOT NULL DEFAULT '',
                quantity_max TEXT NOT NULL DEFAULT '',
                quality_min TEXT NOT NULL DEFAULT '',
                quality_max TEXT NOT NULL DEFAULT '',
                blueprint_chance TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "loot_conditions": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                condition_key TEXT NOT NULL,
                condition_value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
            "loot_rewards": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                reward_type TEXT NOT NULL,
                reward_value TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown',
                source_json TEXT NOT NULL DEFAULT '{}'
            """,
            "loot_references": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                reference_path TEXT NOT NULL,
                reference_type TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            """,
        },
    },
}


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_parse_error": str(exc), "_path": str(path)}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def short_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def configured_content_roots() -> list[Path]:
    return devkit_content_roots(
        config_file=SCRIPT_ROOT.parent / "devkit_content_root.txt"
    )


def default_content_root() -> Path | None:
    for path in configured_content_roots():
        if path.is_dir():
            return path
    return None


def object_path_from_uasset(path: Path, content_root: Path) -> str:
    rel = path.relative_to(content_root).with_suffix("")
    package = "/Game/" + rel.as_posix()
    return package + "." + path.stem


def classify_devkit_asset(path: Path, content_root: Path) -> dict[str, str]:
    rel = path.relative_to(content_root).as_posix()
    lowered = rel.lower()
    name = path.stem
    lower_name = name.lower()
    top_folder = rel.split("/", 1)[0] if "/" in rel else ""
    domain = "other"
    asset_type = "unknown_uasset"

    if "/dinos/" in lowered or lowered.startswith("asa/dinos/"):
        domain = "creature"
    elif "primalgamedata" in lower_name:
        domain = "game_rules"
    elif "/buff" in lowered or lower_name.startswith("buff_"):
        domain = "buff"
    elif "loot" in lowered or "supplycrate" in lowered:
        domain = "loot"
    elif "engram" in lowered:
        domain = "engram"
    elif "inventory" in lowered:
        domain = "inventory"
    elif "structure" in lowered:
        domain = "structure"
    elif "weapon" in lowered:
        domain = "weapon"
    elif "resource" in lowered or "primalitem" in lower_name:
        domain = "item"

    if lower_name.endswith("_character_bp") or lower_name.endswith("_character"):
        asset_type = "creature_character_blueprint"
    elif "statuscomponent" in lower_name:
        asset_type = "status_component_blueprint"
    elif lower_name.startswith("buff_") or "buff" in lower_name:
        asset_type = "buff_blueprint"
    elif lower_name.startswith("primalitem"):
        asset_type = "primal_item_blueprint"
    elif "primalgamedata" in lower_name:
        asset_type = "primal_game_data"
    elif "engram" in lower_name:
        asset_type = "engram_entry"
    elif "loot" in lower_name or "supplycrate" in lower_name:
        asset_type = "loot_or_supply_crate"
    elif "inventory" in lower_name:
        asset_type = "inventory_blueprint"
    elif "datatable" in lower_name or "/datatable" in lowered or "/data/" in lowered:
        asset_type = "data_asset_or_table"
    elif lower_name.endswith("_ai") or "aicontroller" in lower_name:
        asset_type = "ai_controller_blueprint"
    elif "anim" in lower_name:
        asset_type = "animation_asset"

    return {
        "top_folder": top_folder,
        "domain": domain,
        "asset_type": asset_type,
    }


def captured_asset_lookup(captures_root: Path) -> dict[str, str]:
    if not captures_root.is_dir():
        return {}
    captured: dict[str, str] = {}
    for path in captures_root.iterdir():
        if not path.is_dir() or path.name.startswith("_"):
            continue
        captured[path.name.lower()] = str(path)
    return captured


def scan_devkit_assets(
    content_root: Path,
    captures_root: Path,
    project_root: Path,
    ledger_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    captured = captured_asset_lookup(captures_root)
    ledger_snapshot = ledger_snapshot or {"processed": {}, "failed": {}, "deferred": []}
    assets: list[dict[str, Any]] = []
    by_domain: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    by_top_folder: Counter[str] = Counter()
    captured_count = 0
    total_size = 0

    for path in content_root.rglob("*.uasset"):
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = path.relative_to(content_root).as_posix()
        classification = classify_devkit_asset(path, content_root)
        name_key = path.stem.lower()
        is_captured = name_key in captured
        if is_captured:
            captured_count += 1
        total_size += stat.st_size
        by_domain[classification["domain"]] += 1
        by_type[classification["asset_type"]] += 1
        by_top_folder[classification["top_folder"]] += 1
        uexp = path.with_suffix(".uexp")
        ubulk = path.with_suffix(".ubulk")
        uexp_stat = uexp.stat() if uexp.is_file() else None
        ubulk_stat = ubulk.stat() if ubulk.is_file() else None
        modified = datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat()
        item = {
            "asset_name": path.stem,
            "object_path": object_path_from_uasset(path, content_root),
            "relative_path": rel,
            "uasset_path": str(path),
            "has_uexp": uexp.is_file(),
            "has_ubulk": ubulk.is_file(),
            "uasset_size": stat.st_size,
            "uasset_modified": modified,
            "uexp_size": uexp_stat.st_size if uexp_stat else 0,
            "uexp_modified": datetime.fromtimestamp(uexp_stat.st_mtime).replace(microsecond=0).isoformat() if uexp_stat else "",
            "ubulk_size": ubulk_stat.st_size if ubulk_stat else 0,
            "ubulk_modified": datetime.fromtimestamp(ubulk_stat.st_mtime).replace(microsecond=0).isoformat() if ubulk_stat else "",
            "modified": modified,
            "captured": is_captured,
            "capture_dir": short_path(Path(captured[name_key]), project_root) if is_captured else "",
            **classification,
        }
        assets.append(annotate_scan_item(item, ledger_snapshot))

    assets.sort(key=lambda item: item["object_path"].lower())
    knowledge_assets = [item for item in assets if item["asset_type"] in KNOWLEDGE_ASSET_TYPES]
    processed_current_count = sum(1 for item in knowledge_assets if item.get("processed_current"))
    failed_current_count = sum(1 for item in knowledge_assets if item.get("failed_current"))
    return {
        "schema": "ark-devkit-knowledge.global-asset-index.v1",
        "generated": now_iso(),
        "content_root": str(content_root),
        "asset_count": len(assets),
        "knowledge_asset_count": len(knowledge_assets),
        "captured_asset_count": captured_count,
        "processed_current_count": processed_current_count,
        "failed_current_count": failed_current_count,
        "total_uasset_size": total_size,
        "counts": {
            "by_domain": dict(by_domain.most_common()),
            "by_type": dict(by_type.most_common()),
            "by_top_folder": dict(by_top_folder.most_common()),
        },
        "priority_assets": knowledge_assets[:5000],
        "knowledge_assets": knowledge_assets,
        "assets": assets,
    }


ASSET_TABLE_COLUMNS = """
    asset_name TEXT NOT NULL,
    object_path TEXT NOT NULL PRIMARY KEY,
    relative_path TEXT NOT NULL,
    uasset_path TEXT NOT NULL,
    top_folder TEXT NOT NULL,
    domain TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    captured INTEGER NOT NULL,
    capture_dir TEXT NOT NULL,
    has_uexp INTEGER NOT NULL,
    has_ubulk INTEGER NOT NULL,
    uasset_size INTEGER NOT NULL,
    uasset_modified TEXT NOT NULL,
    uexp_size INTEGER NOT NULL,
    uexp_modified TEXT NOT NULL,
    ubulk_size INTEGER NOT NULL,
    ubulk_modified TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    processed_current INTEGER NOT NULL,
    failed_current INTEGER NOT NULL,
    knowledge_status TEXT NOT NULL,
    read_status TEXT NOT NULL,
    last_read_at TEXT NOT NULL,
    failure_count INTEGER NOT NULL,
    last_failed_at TEXT NOT NULL,
    modified TEXT NOT NULL
"""


def asset_table_row(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["asset_name"],
        item["object_path"],
        item["relative_path"],
        item["uasset_path"],
        item["top_folder"],
        item["domain"],
        item["asset_type"],
        1 if item["captured"] else 0,
        item["capture_dir"],
        1 if item["has_uexp"] else 0,
        1 if item["has_ubulk"] else 0,
        int(item["uasset_size"]),
        item.get("uasset_modified") or item.get("modified") or "",
        int(item["uexp_size"]),
        item.get("uexp_modified") or "",
        int(item["ubulk_size"]),
        item.get("ubulk_modified") or "",
        item.get("fingerprint") or fingerprint_for_scan_item(item),
        1 if item.get("processed_current") else 0,
        1 if item.get("failed_current") else 0,
        item.get("knowledge_status") or "",
        item.get("read_status") or "",
        item.get("last_read_at") or "",
        int(item.get("failure_count") or 0),
        item.get("last_failed_at") or "",
        item.get("modified") or item.get("uasset_modified") or "",
    )


def write_global_asset_database(path: Path, index: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger_snapshot = read_ledger_snapshot(path)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"CREATE TABLE asset_files ({ASSET_TABLE_COLUMNS})")
        connection.execute(f"CREATE TABLE assets ({ASSET_TABLE_COLUMNS})")
        columns = """
            asset_name, object_path, relative_path, uasset_path, top_folder,
            domain, asset_type, captured, capture_dir, has_uexp, has_ubulk,
            uasset_size, uasset_modified, uexp_size, uexp_modified,
            ubulk_size, ubulk_modified, fingerprint, processed_current,
            failed_current, knowledge_status, read_status, last_read_at,
            failure_count, last_failed_at, modified
        """
        placeholders = ", ".join(["?"] * 26)
        all_rows = [asset_table_row(item) for item in index.get("assets", [])]
        knowledge_rows = [asset_table_row(item) for item in index.get("knowledge_assets", [])]
        connection.executemany(
            f"INSERT INTO asset_files ({columns}) VALUES ({placeholders})",
            all_rows,
        )
        connection.executemany(f"INSERT INTO assets ({columns}) VALUES ({placeholders})", knowledge_rows)
        for table in ("asset_files", "assets"):
            connection.execute(f"CREATE INDEX idx_{table}_name ON {table}(asset_name)")
            connection.execute(f"CREATE INDEX idx_{table}_object_path ON {table}(object_path)")
            connection.execute(f"CREATE INDEX idx_{table}_type ON {table}(asset_type)")
            connection.execute(f"CREATE INDEX idx_{table}_domain ON {table}(domain)")
            connection.execute(f"CREATE INDEX idx_{table}_captured ON {table}(captured)")
            connection.execute(f"CREATE INDEX idx_{table}_processed ON {table}(processed_current)")
            connection.execute(f"CREATE INDEX idx_{table}_failed ON {table}(failed_current)")
            connection.execute(f"CREATE INDEX idx_{table}_relative_path ON {table}(relative_path)")
        connection.execute(
            """
            CREATE TABLE priority_categories (
                category_order INTEGER NOT NULL,
                group_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                total_count INTEGER NOT NULL,
                candidate_count INTEGER NOT NULL,
                processed_count INTEGER NOT NULL,
                failed_count INTEGER NOT NULL,
                deferred_count INTEGER NOT NULL,
                first_batch_count INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE priority_queue (
                queue_index INTEGER PRIMARY KEY,
                category_order INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                rank_in_category INTEGER NOT NULL,
                object_path TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                score INTEGER NOT NULL,
                reasons_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        connection.execute("CREATE INDEX idx_priority_queue_group ON priority_queue(group_id)")
        connection.execute("CREATE INDEX idx_priority_queue_object_path ON priority_queue(object_path)")
        connection.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        metadata = {
            "schema": index.get("schema", ""),
            "generated": index.get("generated", ""),
            "content_root": index.get("content_root", ""),
            "asset_count": str(index.get("asset_count", 0)),
            "knowledge_asset_count": str(index.get("knowledge_asset_count", 0)),
            "captured_asset_count": str(index.get("captured_asset_count", 0)),
            "processed_current_count": str(index.get("processed_current_count", 0)),
            "failed_current_count": str(index.get("failed_current_count", 0)),
        }
        connection.executemany("INSERT INTO metadata (key, value) VALUES (?, ?)", metadata.items())
        restore_ledger_snapshot(connection, ledger_snapshot)
        connection.commit()
    finally:
        connection.close()


def global_index_summary(index: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in index.items()
        if key not in {"assets", "knowledge_assets", "priority_assets"}
    }


def normalize_related_path_token(token: str) -> str:
    value = str(token or "").strip().strip("\"'`")
    value = value.rstrip(".,;)]}'\"")
    if not value.startswith("/Game/"):
        return ""
    if "." in value:
        package, asset_name = value.rsplit(".", 1)
        asset_name = asset_name.split(":", 1)[0].rstrip(".")
        if asset_name.endswith("_C"):
            asset_name = asset_name[:-2]
        if not asset_name:
            asset_name = package.rsplit("/", 1)[-1]
        return f"{package}.{asset_name}"
    package = value
    asset_name = package.rsplit("/", 1)[-1]
    if asset_name.endswith("_C"):
        asset_name = asset_name[:-2]
        package = package[:-2]
    if not asset_name:
        return ""
    return f"{package}.{asset_name}"


def related_paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in UE_OBJECT_PATH_PATTERN.finditer(str(text or "")):
        path = normalize_related_path_token(match.group(0))
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def related_paths_from_value(value: Any) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        if path and path not in seen:
            seen.add(path)
            paths.append(path)

    def visit(item: Any) -> None:
        if isinstance(item, str):
            for path in related_paths_from_text(item):
                add(path)
        elif isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple, set)):
            for nested in item:
                visit(nested)

    visit(value)
    return paths


def related_asset_name(object_path: str) -> str:
    path = normalize_related_path_token(object_path)
    if not path:
        return ""
    return path.rsplit(".", 1)[-1]


def add_related_priority_value(
    related: dict[str, Any],
    value: Any,
    source_type: str,
    source_detail: str = "",
) -> int:
    assets = related.setdefault("assets", {})
    added = 0
    for object_path in related_paths_from_value(value):
        info = assets.setdefault(
            object_path,
            {
                "object_path": object_path,
                "asset_name": related_asset_name(object_path),
                "sources": [],
            },
        )
        source = {
            "source_type": source_type,
            "source_detail": source_detail,
        }
        if source not in info["sources"]:
            info["sources"].append(source)
        added += 1
    return added


def sqlite_table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def parse_json_column(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def collect_related_priority_from_capture_file(path: Path, related: dict[str, Any]) -> int:
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return 0
    source_detail = path.parent.parent.name
    count = 0
    for candidate in payload.get("candidates") or []:
        if isinstance(candidate, dict):
            count += add_related_priority_value(
                related,
                candidate.get("next_probe"),
                "formula_candidates.next_probe",
                source_detail,
            )
    for unresolved in payload.get("unresolved_formulas") or []:
        if isinstance(unresolved, dict):
            count += add_related_priority_value(
                related,
                unresolved.get("required_next_probe"),
                "unresolved_formulas.required_next_probe",
                source_detail,
            )
    return count


def collect_related_priority_from_database(path: Path, related: dict[str, Any]) -> int:
    if not path.is_file():
        return 0
    count = 0
    connection = sqlite3.connect(path)
    try:
        for table_name in RELATED_PRIORITY_REFERENCE_TABLES:
            if not sqlite_table_exists(connection, table_name):
                continue
            rows = connection.execute(
                f"""
                SELECT object_path, reference_path, reference_type, source_property
                FROM {table_name}
                WHERE reference_path LIKE '%/Game/%'
                """
            ).fetchall()
            for source_path, reference_path, reference_type, source_property in rows:
                detail = f"{path.name}:{table_name}:{source_path}:{reference_type}:{source_property}"
                count += add_related_priority_value(
                    related,
                    reference_path,
                    table_name,
                    detail,
                )
        for table_name, column_name, source_type in RELATED_PRIORITY_FORMULA_TABLES:
            if not sqlite_table_exists(connection, table_name):
                continue
            rows = connection.execute(
                f"SELECT object_path, mechanism_type, {column_name} FROM {table_name}"
            ).fetchall()
            for source_path, mechanism_type, raw_value in rows:
                detail = f"{path.name}:{table_name}:{source_path}:{mechanism_type}"
                count += add_related_priority_value(
                    related,
                    parse_json_column(raw_value),
                    source_type,
                    detail,
                )
    finally:
        connection.close()
    return count


def collect_related_priority_sources(captures: Path, db_dir: Path) -> dict[str, Any]:
    related: dict[str, Any] = {
        "schema": "ark-devkit-knowledge.related-priority.v1",
        "generated": now_iso(),
        "assets": {},
        "source_counts": {},
    }
    source_counts: Counter[str] = Counter()
    if captures.is_dir():
        for formula_path in sorted(captures.glob("*/output/formula_candidates.json")):
            before = sum(len(info.get("sources") or []) for info in related["assets"].values())
            collect_related_priority_from_capture_file(formula_path, related)
            after = sum(len(info.get("sources") or []) for info in related["assets"].values())
            if after > before:
                source_counts["capture_formula_candidates"] += after - before
    if db_dir.is_dir():
        for db_path in sorted(db_dir.glob("*.sqlite")):
            before = sum(len(info.get("sources") or []) for info in related["assets"].values())
            collect_related_priority_from_database(db_path, related)
            after = sum(len(info.get("sources") or []) for info in related["assets"].values())
            if after > before:
                source_counts[f"db:{db_path.name}"] += after - before
    related["source_counts"] = dict(source_counts)
    related["path_count"] = len(related["assets"])
    return related


def related_priority_lookup(related_priority: dict[str, Any] | None) -> dict[str, Any]:
    assets = (related_priority or {}).get("assets") or {}
    paths = {str(path) for path in assets.keys() if path}
    names: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        name = related_asset_name(path)
        if name:
            names[name].add(path)
    return {
        "assets": assets,
        "paths": paths,
        "names": names,
    }


def related_priority_paths_for_item(item: dict[str, Any], lookup: dict[str, Any] | None) -> list[str]:
    if not lookup:
        return []
    paths: set[str] = set()
    object_path = normalize_related_path_token(str(item.get("object_path") or ""))
    if object_path and object_path in lookup.get("paths", set()):
        paths.add(object_path)
    name = str(item.get("asset_name") or "")
    for path in (lookup.get("names") or {}).get(name, set()):
        paths.add(path)
    return sorted(paths)


def related_priority_match(item: dict[str, Any], lookup: dict[str, Any] | None) -> bool:
    return bool(related_priority_paths_for_item(item, lookup))


def related_priority_sources_for_item(item: dict[str, Any], lookup: dict[str, Any] | None) -> list[str]:
    sources: list[str] = []
    assets = (lookup or {}).get("assets") or {}
    for path in related_priority_paths_for_item(item, lookup):
        info = assets.get(path) or {}
        for source in info.get("sources") or []:
            source_type = str(source.get("source_type") or "")
            source_detail = str(source.get("source_detail") or "")
            text = source_type if not source_detail else f"{source_type}: {source_detail}"
            if text and text not in sources:
                sources.append(text)
    return sources


def render_global_asset_report(index: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# ARK DevKit 全局资产索引")
    lines.append("")
    lines.append(f"生成时间：{index.get('generated')}")
    lines.append(f"Content 根目录：`{index.get('content_root')}`")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- `.uasset` 数量：{index.get('asset_count', 0)}")
    lines.append(f"- 知识库工作资产：{index.get('knowledge_asset_count', 0)}")
    lines.append(f"- 已有深度解析 captures：{index.get('captured_asset_count', 0)}")
    lines.append(f"- 已入库且文件未变：{index.get('processed_current_count', 0)}")
    lines.append(f"- 最近失败且文件未变：{index.get('failed_current_count', 0)}")
    lines.append("")
    lines.append("## 按类型统计")
    lines.append("")
    lines.append("| 类型 | 数量 |")
    lines.append("| --- | ---: |")
    for key, value in (index.get("counts", {}).get("by_type", {}) or {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.append("")
    lines.append("## 按领域统计")
    lines.append("")
    lines.append("| 领域 | 数量 |")
    lines.append("| --- | ---: |")
    for key, value in (index.get("counts", {}).get("by_domain", {}) or {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.append("")
    lines.append("## 已深度解析资产")
    lines.append("")
    captured = [item for item in index.get("assets", []) if item.get("captured")]
    if not captured:
        lines.append("还没有匹配到 captures 里的深度解析资产。")
    else:
        for item in captured[:80]:
            lines.append(
                f"- `{item['asset_name']}`：{item['asset_type']}，`{item['object_path']}`"
            )
    lines.append("")
    lines.append("## 下一步最值得补读的类型")
    lines.append("")
    lines.append("- `primal_game_data`：全局规则、资源注册、物品/生物入口。")
    lines.append("- `status_component_blueprint`：生物属性、成长、经验和状态值。")
    lines.append("- `primal_item_blueprint`：物品描述、使用逻辑、消耗与显示数据。")
    lines.append("- `buff_blueprint`：临时效果、训练、继承概率、状态覆盖。")
    lines.append("- `loot_or_supply_crate`：宝箱、掉落和奖励池。")
    lines.append("")
    return "\n".join(lines)


def priority_text_for_asset(item: dict[str, Any]) -> str:
    return " ".join(
        [
            str(item.get("asset_name") or ""),
            str(item.get("object_path") or ""),
            str(item.get("relative_path") or ""),
            str(item.get("asset_type") or ""),
            str(item.get("domain") or ""),
        ]
    )


def priority_include_asset_names(group: dict[str, Any]) -> set[str]:
    return {str(value) for value in group.get("include_asset_names") or []}


def queue_include_asset_names(group: dict[str, Any]) -> set[str]:
    return {str(value) for value in group.get("queue_include_asset_names") or []}


def fixed_priority_match(item: dict[str, Any]) -> bool:
    name = str(item.get("asset_name") or "")
    object_path = str(item.get("object_path") or "")
    return name in FIXED_PRIORITY_ASSET_NAMES or object_path in FIXED_PRIORITY_OBJECT_PATHS


def explicit_priority_match(item: dict[str, Any], group: dict[str, Any]) -> bool:
    name = str(item.get("asset_name") or "")
    object_path = str(item.get("object_path") or "")
    include_names = priority_include_asset_names(group)
    queue_include_names = queue_include_asset_names(group)
    return (
        fixed_priority_match(item)
        or name in include_names
        or object_path in include_names
        or name in queue_include_names
        or object_path in queue_include_names
    )


def priority_asset_allowed(
    item: dict[str, Any],
    group: dict[str, Any],
    related_lookup: dict[str, Any] | None = None,
) -> bool:
    if explicit_priority_match(item, group) or related_priority_match(item, related_lookup):
        return True

    text = priority_text_for_asset(item)
    for keyword in group.get("exclude_keywords") or []:
        if contains_keyword(text, str(keyword)):
            return False
    return True


def priority_asset_defer_reason(item: dict[str, Any], group: dict[str, Any]) -> str | None:
    name = str(item.get("asset_name") or "")
    for pattern in group.get("defer_name_patterns") or []:
        if re.search(str(pattern), name):
            return str(group.get("defer_reason") or f"匹配暂缓规则：{pattern}")
    return None


def queue_asset_allowed(
    item: dict[str, Any],
    group: dict[str, Any],
    related_lookup: dict[str, Any] | None = None,
) -> bool:
    if fixed_priority_match(item) or related_priority_match(item, related_lookup):
        return True
    name = str(item.get("asset_name") or "")
    object_path = str(item.get("object_path") or "")
    include_names = queue_include_asset_names(group)
    if include_names and name not in include_names and object_path not in include_names:
        return False
    return True


def first_batch_limit(group: dict[str, Any]) -> int | None:
    value = group.get("first_batch_limit", 25)
    if isinstance(value, str) and value.lower() == "all":
        return None
    return max(int(value), 0)


def score_priority_asset(
    item: dict[str, Any],
    group: dict[str, Any],
    related_lookup: dict[str, Any] | None = None,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if fixed_priority_match(item):
        score += 1000
        reasons.append("固定高优先级样本")
    if related_priority_match(item, related_lookup):
        score += 700
        reasons.append("related asset probe")
    asset_type = item.get("asset_type")
    if asset_type in group["asset_types"]:
        score += 100
        reasons.append(f"类型匹配：{asset_type}")
    text = priority_text_for_asset(item)
    for keyword, weight in group["keywords"].items():
        if contains_keyword(text, keyword):
            score += int(weight)
            reasons.append(f"命中关键词：{keyword}")
    rel = str(item.get("relative_path") or "")
    if group.get("generic_path_bonus", True):
        if rel.startswith("ASA/"):
            score += 10
            reasons.append("ASA 路径")
        if "/Dinos/" in rel or rel.startswith("ASA/Dinos/"):
            score += 10
            reasons.append("Dinos 路径")
    if item.get("captured"):
        score += 15
        reasons.append("已有 captures，可直接做交叉验证")
    if item.get("has_uexp"):
        reasons.append("存在 .uexp，深读时需要一起定位")
    return score, reasons


def build_priority_targets(
    global_index: dict[str, Any],
    related_priority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assets = global_index.get("knowledge_assets") or global_index.get("assets", [])
    lookup = related_priority_lookup(related_priority)
    groups: dict[str, Any] = {}
    all_queue: list[str] = []
    related_match_count = 0
    for category_order, (group_id, group) in enumerate(DEEP_READ_GROUPS.items(), start=1):
        candidates: list[dict[str, Any]] = []
        deferred_candidates: list[dict[str, Any]] = []
        failed_candidates: list[dict[str, Any]] = []
        total_count = 0
        captured_count = 0
        processed_count = 0
        failed_count = 0
        for item in assets:
            if item.get("asset_type") not in group["asset_types"]:
                continue
            related_paths = related_priority_paths_for_item(item, lookup)
            related_match = bool(related_paths)
            force_include = explicit_priority_match(item, group) or related_match
            total_count += 1
            if item.get("captured"):
                captured_count += 1
            if item.get("processed_current"):
                processed_count += 1
                if not force_include:
                    continue
            if item.get("failed_current"):
                failed_count += 1
                if not force_include:
                    failed_candidates.append(
                        {
                            "asset_name": item.get("asset_name"),
                            "object_path": item.get("object_path"),
                            "asset_type": item.get("asset_type"),
                            "domain": item.get("domain"),
                            "relative_path": item.get("relative_path"),
                            "captured": item.get("captured"),
                            "has_uexp": item.get("has_uexp"),
                            "failure_count": item.get("failure_count", 0),
                            "last_failed_at": item.get("last_failed_at", ""),
                        }
                    )
                    continue
            if not priority_asset_allowed(item, group, lookup):
                continue
            score, reasons = score_priority_asset(item, group, lookup)
            if score <= 0:
                continue
            candidate = {
                "asset_name": item.get("asset_name"),
                "object_path": item.get("object_path"),
                "asset_type": item.get("asset_type"),
                "domain": item.get("domain"),
                "relative_path": item.get("relative_path"),
                "captured": item.get("captured"),
                "has_uexp": item.get("has_uexp"),
                "score": score,
                "reasons": reasons[:8],
                "force_include": force_include,
            }
            if related_match:
                related_match_count += 1
                candidate["related_priority"] = True
                candidate["related_priority_paths"] = related_paths[:8]
                candidate["related_priority_sources"] = related_priority_sources_for_item(item, lookup)[:8]
            defer_reason = priority_asset_defer_reason(item, group)
            if defer_reason:
                candidate["deferred"] = True
                candidate["deferred_reason"] = defer_reason
                deferred_candidates.append(candidate)
            else:
                candidates.append(candidate)
        candidates.sort(key=lambda item: (-int(item["score"]), bool(item["captured"]), str(item["object_path"]).lower()))
        deferred_candidates.sort(key=lambda item: (-int(item["score"]), bool(item["captured"]), str(item["object_path"]).lower()))
        failed_candidates.sort(key=lambda item: (-int(item.get("failure_count") or 0), str(item["object_path"]).lower()))
        limit = int(group.get("limit") or 100)
        queue_min_score = int(group.get("queue_min_score") or 0)
        include_captured = bool(group.get("include_captured_in_queue"))
        batch_candidates = [
            item
            for item in candidates
            if (
                queue_asset_allowed(item, group, lookup)
                and (include_captured or not item.get("captured"))
                and int(item.get("score") or 0) >= queue_min_score
            )
        ]
        batch_limit = first_batch_limit(group)
        if batch_limit is None:
            first_batch = batch_candidates
        else:
            forced_batch = [item for item in batch_candidates if item.get("force_include")]
            regular_batch = [item for item in batch_candidates if not item.get("force_include")]
            first_batch = forced_batch + regular_batch[: max(batch_limit - len(forced_batch), 0)]
        for item in first_batch:
            object_path = str(item.get("object_path") or "")
            if object_path and object_path not in all_queue:
                all_queue.append(object_path)
        groups[group_id] = {
            "title": group["title"],
            "category_order": category_order,
            "total_count": total_count,
            "captured_count": captured_count,
            "processed_count": processed_count,
            "failed_count": failed_count,
            "candidate_count": len(candidates),
            "deferred_count": len(deferred_candidates),
            "queue_min_score": queue_min_score,
            "first_batch_limit": group.get("first_batch_limit", 25),
            "include_captured_in_queue": include_captured,
            "first_batch_count": len(first_batch),
            "first_batch": first_batch,
            "candidates": candidates[:limit],
            "deferred_candidates": deferred_candidates,
            "failed_candidates": failed_candidates,
        }
    return {
        "schema": "ark-devkit-knowledge.priority-targets.v1",
        "generated": now_iso(),
        "source_global_asset_count": global_index.get("asset_count", 0),
        "source_knowledge_asset_count": global_index.get("knowledge_asset_count", 0),
        "related_priority": {
            "path_count": len(lookup.get("paths") or []),
            "matched_asset_count": related_match_count,
            "source_counts": (related_priority or {}).get("source_counts") or {},
            "sample_paths": sorted(lookup.get("paths") or [])[:50],
        },
        "groups": groups,
        "deep_read_queue": all_queue,
    }


def render_priority_targets_report(priority: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 五类重点资产补读清单")
    lines.append("")
    lines.append(f"生成时间：{priority.get('generated')}")
    lines.append("")
    lines.append("这份清单从全局 DevKit 索引里挑出下一批最值得深度解析的资产。")
    lines.append("优先级按类别分桶：先决定类别顺序，再在类别内部按分数排序。")
    lines.append("目标是补齐：全局规则、属性状态、物品逻辑、Buff 效果、宝箱/掉落。")
    lines.append("")
    lines.append("## 一键队列")
    lines.append("")
    lines.append("队列文件：`knowledge_base/priorities/deep_read_queue.txt`")
    lines.append("")
    lines.append(f"- 队列资产数：{len(priority.get('deep_read_queue', []))}")
    lines.append("")
    for group_id, group in priority.get("groups", {}).items():
        lines.append(f"## {group['title']}")
        lines.append("")
        lines.append(f"- 类别顺序：{group.get('category_order', '-')}")
        lines.append(f"- 全局数量：{group.get('total_count', 0)}")
        lines.append(f"- 已深度解析：{group.get('captured_count', 0)}")
        if group.get("processed_count"):
            lines.append(f"- 已入库并跳过：{group.get('processed_count', 0)}")
        if group.get("failed_count"):
            lines.append(f"- 失败暂不重试：{group.get('failed_count', 0)}")
        lines.append(f"- 本轮候选：{group.get('candidate_count', 0)}")
        if group.get("deferred_count"):
            lines.append(f"- 暂缓候选：{group.get('deferred_count', 0)}")
        if group.get("queue_min_score"):
            lines.append(f"- 自动队列最低分：{group.get('queue_min_score')}")
        if group.get("first_batch_limit") == "all":
            lines.append("- 自动队列范围：全部候选")
        else:
            lines.append(f"- 自动队列上限：{group.get('first_batch_limit', 25)}")
        lines.append("")
        lines.append("### 第一批建议深读")
        lines.append("")
        first_batch = group.get("first_batch", [])
        if not first_batch:
            lines.append("暂无未深读候选。")
        else:
            for idx, item in enumerate(first_batch[:25], start=1):
                reasons = "；".join(item.get("reasons", [])[:4])
                lines.append(f"{idx}. `{item.get('asset_name')}`")
                lines.append(f"   - Object Path：`{item.get('object_path')}`")
                lines.append(f"   - 分数：{item.get('score')}；原因：{reasons}")
        lines.append("")
        lines.append("### 候选 Top 15")
        lines.append("")
        lines.append("| 资产 | 类型 | 分数 | 已深读 |")
        lines.append("| --- | --- | ---: | --- |")
        for item in group.get("candidates", [])[:15]:
            captured = "是" if item.get("captured") else "否"
            lines.append(
                f"| `{item.get('asset_name')}` | `{item.get('asset_type')}` | {item.get('score')} | {captured} |"
            )
        lines.append("")
        deferred = group.get("deferred_candidates", [])
        if deferred:
            lines.append("### 暂缓候选 Top 15")
            lines.append("")
            lines.append("| 资产 | 分数 | 暂缓原因 |")
            lines.append("| --- | ---: | --- |")
            for item in deferred[:15]:
                lines.append(
                    f"| `{item.get('asset_name')}` | {item.get('score')} | {item.get('deferred_reason', '')} |"
                )
            lines.append("")
        failed = group.get("failed_candidates", [])
        if failed:
            lines.append("### 失败候选 Top 15")
            lines.append("")
            lines.append("| 资产 | 失败次数 | 最近失败 |")
            lines.append("| --- | ---: | --- |")
            for item in failed[:15]:
                lines.append(
                    f"| `{item.get('asset_name')}` | {item.get('failure_count', 0)} | {item.get('last_failed_at', '')} |"
                )
            lines.append("")
    return "\n".join(lines)


def write_priority_outputs(out_dir: Path, priority: dict[str, Any]) -> None:
    write_json(out_dir / "priorities" / "priority_targets.json", priority)
    write_text(out_dir / "priorities" / "priority_targets.md", render_priority_targets_report(priority))
    queue = "\n".join(priority.get("deep_read_queue", []))
    if queue:
        queue += "\n"
    write_text(out_dir / "priorities" / "deep_read_queue.txt", queue)
    for group_id, group in priority.get("groups", {}).items():
        group_queue = "\n".join(str(item.get("object_path") or "") for item in group.get("first_batch", []) if item.get("object_path"))
        if group_queue:
            group_queue += "\n"
        write_text(out_dir / "priorities" / f"{group_id}_queue.txt", group_queue)


def write_priority_database_tables(db_path: Path, priority: dict[str, Any]) -> None:
    if not db_path.is_file():
        return
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DELETE FROM priority_categories")
        connection.execute("DELETE FROM priority_queue")
        category_rows: list[tuple[Any, ...]] = []
        queue_rows: list[tuple[Any, ...]] = []
        queue_index = 1
        for group_id, group in priority.get("groups", {}).items():
            category_order = int(group.get("category_order") or 0)
            category_rows.append(
                (
                    category_order,
                    str(group_id),
                    str(group.get("title") or ""),
                    int(group.get("total_count") or 0),
                    int(group.get("candidate_count") or 0),
                    int(group.get("processed_count") or 0),
                    int(group.get("failed_count") or 0),
                    int(group.get("deferred_count") or 0),
                    int(group.get("first_batch_count") or 0),
                )
            )
            for rank, item in enumerate(group.get("first_batch", []), start=1):
                queue_rows.append(
                    (
                        queue_index,
                        category_order,
                        str(group_id),
                        rank,
                        str(item.get("object_path") or ""),
                        str(item.get("asset_name") or ""),
                        str(item.get("asset_type") or ""),
                        int(item.get("score") or 0),
                        json.dumps(item.get("reasons") or [], ensure_ascii=False),
                    )
                )
                queue_index += 1
        connection.executemany(
            """
            INSERT INTO priority_categories (
                category_order, group_id, title, total_count, candidate_count,
                processed_count, failed_count, deferred_count, first_batch_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            category_rows,
        )
        connection.executemany(
            """
            INSERT INTO priority_queue (
                queue_index, category_order, group_id, rank_in_category,
                object_path, asset_name, asset_type, score, reasons_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            queue_rows,
        )
        connection.commit()
    finally:
        connection.close()


BUSINESS_ASSET_COLUMNS = """
    object_path TEXT PRIMARY KEY,
    asset_name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL DEFAULT '',
    uasset_path TEXT NOT NULL DEFAULT '',
    captured INTEGER NOT NULL DEFAULT 0,
    processed_current INTEGER NOT NULL DEFAULT 0,
    failed_current INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL DEFAULT '',
    capture_dir TEXT NOT NULL DEFAULT '',
    read_status TEXT NOT NULL DEFAULT '',
    knowledge_status TEXT NOT NULL DEFAULT '',
    last_read_at TEXT NOT NULL DEFAULT ''
"""

FORMULA_CANDIDATES_TABLE = """
    id TEXT PRIMARY KEY,
    object_path TEXT NOT NULL,
    asset_name TEXT NOT NULL DEFAULT '',
    asset_type TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    mechanism_type TEXT NOT NULL DEFAULT '',
    mechanism TEXT NOT NULL DEFAULT '',
    player_meaning TEXT NOT NULL DEFAULT '',
    graph TEXT NOT NULL DEFAULT '',
    visible_rule TEXT NOT NULL DEFAULT '',
    formula_text TEXT NOT NULL DEFAULT '',
    formula_ast_json TEXT NOT NULL DEFAULT '{}',
    inputs_json TEXT NOT NULL DEFAULT '[]',
    outputs_json TEXT NOT NULL DEFAULT '[]',
    conditions_json TEXT NOT NULL DEFAULT '[]',
    math_nodes_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    link_quality_json TEXT NOT NULL DEFAULT '{}',
    external_dependencies_json TEXT NOT NULL DEFAULT '[]',
    missing_evidence_json TEXT NOT NULL DEFAULT '[]',
    next_probe_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'candidate',
    source_capture TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
"""

UNRESOLVED_FORMULAS_TABLE = """
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL DEFAULT '',
    object_path TEXT NOT NULL,
    asset_name TEXT NOT NULL DEFAULT '',
    asset_type TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    mechanism_type TEXT NOT NULL DEFAULT '',
    mechanism TEXT NOT NULL DEFAULT '',
    known_visible_part TEXT NOT NULL DEFAULT '',
    blocked_by_json TEXT NOT NULL DEFAULT '[]',
    missing_evidence_json TEXT NOT NULL DEFAULT '[]',
    required_next_probe_json TEXT NOT NULL DEFAULT '[]',
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'open',
    confidence TEXT NOT NULL DEFAULT 'unresolved_formula',
    source_capture TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
"""


def business_asset_row(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("object_path") or "",
        item.get("asset_name") or "",
        item.get("asset_type") or "",
        item.get("domain") or "",
        item.get("relative_path") or "",
        item.get("uasset_path") or "",
        1 if item.get("captured") else 0,
        1 if item.get("processed_current") else 0,
        1 if item.get("failed_current") else 0,
        item.get("fingerprint") or fingerprint_for_scan_item(item),
        item.get("capture_dir") or "",
        item.get("read_status") or "",
        item.get("knowledge_status") or "",
        item.get("last_read_at") or "",
    )


def write_business_database(
    db_dir: Path,
    group_id: str,
    config: dict[str, Any],
    global_index: dict[str, Any],
    priority_targets: dict[str, Any],
) -> None:
    db_path = db_dir / str(config["filename"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    assets = [
        item
        for item in global_index.get("knowledge_assets", [])
        if item.get("asset_type") in set(config.get("asset_types") or [])
    ]
    group = (priority_targets.get("groups") or {}).get(group_id, {})
    connection = sqlite3.connect(db_path)
    try:
        asset_table = str(config["asset_table"])
        connection.execute(f"CREATE TABLE {asset_table} ({BUSINESS_ASSET_COLUMNS})")
        connection.executemany(
            f"""
            INSERT INTO {asset_table} (
                object_path, asset_name, asset_type, domain, relative_path, uasset_path,
                captured, processed_current, failed_current, fingerprint, capture_dir,
                read_status, knowledge_status, last_read_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [business_asset_row(item) for item in assets],
        )
        connection.execute(f"CREATE INDEX idx_{asset_table}_asset_name ON {asset_table}(asset_name)")
        connection.execute(f"CREATE INDEX idx_{asset_table}_processed ON {asset_table}(processed_current)")
        connection.execute(f"CREATE INDEX idx_{asset_table}_failed ON {asset_table}(failed_current)")

        connection.execute(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        metadata = {
            "schema": "ark-devkit-knowledge.business-db.v1",
            "group_id": group_id,
            "generated": global_index.get("generated", ""),
            "asset_table": asset_table,
            "asset_count": str(len(assets)),
            "candidate_count": str(group.get("candidate_count", 0)),
            "processed_count": str(group.get("processed_count", 0)),
            "failed_count": str(group.get("failed_count", 0)),
            "deferred_count": str(group.get("deferred_count", 0)),
            "first_batch_count": str(group.get("first_batch_count", 0)),
        }
        connection.executemany("INSERT INTO metadata (key, value) VALUES (?, ?)", metadata.items())

        connection.execute(
            """
            CREATE TABLE read_sources (
                object_path TEXT PRIMARY KEY,
                capture_dir TEXT NOT NULL DEFAULT '',
                package_json TEXT NOT NULL DEFAULT '',
                graph_nodes_json TEXT NOT NULL DEFAULT '',
                class_defaults_json TEXT NOT NULL DEFAULT '',
                last_read_at TEXT NOT NULL DEFAULT '',
                read_status TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE unresolved_work (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                work_type TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                source_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'open'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE asset_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_path TEXT NOT NULL,
                reference_path TEXT NOT NULL,
                reference_type TEXT NOT NULL DEFAULT '',
                source_property TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'unknown'
            )
            """
        )
        connection.execute(f"CREATE TABLE formula_candidates ({FORMULA_CANDIDATES_TABLE})")
        connection.execute("CREATE INDEX idx_formula_candidates_object_path ON formula_candidates(object_path)")
        connection.execute("CREATE INDEX idx_formula_candidates_type ON formula_candidates(mechanism_type)")
        connection.execute("CREATE INDEX idx_formula_candidates_confidence ON formula_candidates(confidence)")
        connection.execute(f"CREATE TABLE unresolved_formulas ({UNRESOLVED_FORMULAS_TABLE})")
        connection.execute("CREATE INDEX idx_unresolved_formulas_object_path ON unresolved_formulas(object_path)")
        connection.execute("CREATE INDEX idx_unresolved_formulas_status ON unresolved_formulas(status)")
        connection.execute("CREATE INDEX idx_unresolved_formulas_priority ON unresolved_formulas(priority)")
        for table_name, columns in (config.get("tables") or {}).items():
            connection.execute(f"CREATE TABLE {table_name} ({columns})")
            if table_name.endswith("_references") or table_name == "asset_references":
                connection.execute(f"CREATE INDEX idx_{table_name}_object_path ON {table_name}(object_path)")

        if group_id == "status_component_blueprint":
            rows = [
                (
                    item.get("object_path") or "",
                    item.get("asset_name") or "",
                    item.get("deferred_reason") or "",
                    priority_targets.get("generated") or "",
                )
                for item in group.get("deferred_candidates", [])
            ]
            if rows:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO deferred_creature_status (
                        object_path, asset_name, reason, last_seen_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
        connection.commit()
    finally:
        connection.close()


def write_business_databases(out_dir: Path, global_index: dict[str, Any], priority_targets: dict[str, Any]) -> dict[str, str]:
    db_dir = out_dir / "db"
    paths: dict[str, str] = {}
    for group_id, config in BUSINESS_DATABASES.items():
        write_business_database(db_dir, group_id, config, global_index, priority_targets)
        paths[group_id] = f"db/{config['filename']}"
    return paths


def infer_asset_role(name: str) -> str:
    lowered = name.lower()
    if "character_bp" in lowered:
        return "creature_character_blueprint"
    if lowered.startswith("primalitem") or "primalitem" in lowered:
        return "item_or_resource_blueprint"
    if lowered.startswith("buff_") or "buff" in lowered:
        return "buff_blueprint"
    if "statuscomponent" in lowered or "status" in lowered:
        return "status_component_blueprint"
    if "inventory" in lowered:
        return "inventory_blueprint"
    return "unknown_blueprint_asset"


def confidence_rank(value: str | None) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get((value or "").lower(), 0)


def keyword_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [word for word in EVIDENCE_KEYWORDS if word.lower() in lowered]


def matches_keywords(text: str, words: list[str]) -> bool:
    return any(contains_keyword(text, word) for word in words)


def contains_keyword(text: str, word: str) -> bool:
    if not text:
        return False
    if word == "XP":
        return bool(re.search(r"(?<![A-Za-z])XP(?![A-Za-z])|KillXP|StoredXP", text))
    if word == "Stat":
        return bool(re.search(r"(?<![A-Za-z])Stat(?![a-z])|Stat[A-Z_]", text))
    return word.lower() in text.lower()


def collect_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            strings.extend(collect_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(collect_strings(item))
    return strings


def extract_game_paths(value: Any) -> list[str]:
    paths: set[str] = set()
    for text in collect_strings(value):
        for match in re.findall(r"/Game/[A-Za-z0-9_./-]+", text):
            paths.add(match.rstrip(".,;:)\"'"))
    return sorted(paths)


def summarize_default_variables(defaults: dict[str, Any]) -> dict[str, Any]:
    variables = defaults.get("variables") or {}
    summary: dict[str, Any] = {}
    for name, info in sorted(variables.items()):
        if not isinstance(info, dict):
            continue
        summary[name] = {
            "value": info.get("value"),
            "type": info.get("type"),
            "confidence": info.get("confidence", "unknown"),
            "source": info.get("source", "uasset_cdo"),
            "keyword_hits": keyword_hits(name),
            **downstream_default_metadata(info),
        }
    return summary


def parse_markdown_table_unresolved(report_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(report_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|") or "missing/native/inherited" not in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4 or set(cells[0]) <= {"-"}:
            continue
        rows.append(
            {
                "graph": cells[0],
                "function": cells[1],
                "resolution": cells[2],
                "category": cells[3],
                "line": line_no,
            }
        )
    return rows


def collect_markdown_evidence(asset_dir: Path, root: Path) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    candidates = list((asset_dir / "output").glob("*.md")) + list(asset_dir.glob("*.md"))
    for path in sorted(candidates):
        text = read_text(path)
        if not text:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            hits = keyword_hits(line)
            if not hits:
                continue
            evidence.append(
                {
                    "source": short_path(path, root),
                    "line": line_no,
                    "keywords": hits,
                    "text": line.strip()[:500],
                }
            )
            if len(evidence) >= 160:
                return evidence
    return evidence


def summarize_graph_file(path: Path, root: Path) -> dict[str, Any]:
    graph = read_json(path, {})
    metadata = graph.get("metadata") if isinstance(graph, dict) else {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    calls: Counter[str] = Counter()
    variables: Counter[str] = Counter()
    node_classes: Counter[str] = Counter()
    semantic_nodes: list[dict[str, Any]] = []

    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            class_name = node.get("class_name") or node.get("node_type") or ""
            node_classes[class_name] += 1
            function = node.get("function") or ""
            variable = node.get("variable") or ""
            if function:
                calls[function] += 1
            if variable:
                variables[variable] += 1
            if function or variable or class_name in {
                "K2Node_CustomEvent",
                "K2Node_Event",
                "K2Node_IfThenElse",
                "K2Node_ExecutionSequence",
            }:
                semantic_nodes.append(
                    {
                        "name": node.get("name"),
                        "class_name": class_name,
                        "function": function,
                        "variable": variable,
                        "x": node.get("x"),
                        "y": node.get("y"),
                    }
                )

    return {
        "name": metadata.get("graph_name") or path.stem,
        "graph_type": metadata.get("graph_type"),
        "source": short_path(path, root),
        "read_status": metadata.get("uasset_read_status"),
        "confidence": metadata.get("confidence"),
        "node_count": metadata.get("node_count", 0),
        "pin_count": metadata.get("pin_count", 0),
        "link_count": metadata.get("link_count", 0),
        "coverage": metadata.get("coverage", {}),
        "calls": dict(calls.most_common()),
        "variables": dict(variables.most_common()),
        "node_classes": dict(node_classes.most_common()),
        "semantic_nodes_sample": semantic_nodes[:80],
        "keyword_groups": [
            group
            for group, words in KEYWORD_GROUPS.items()
            if matches_keywords(metadata.get("graph_name") or path.stem, words)
            or any(matches_keywords(name, words) for name in list(calls) + list(variables))
        ],
    }


def summarize_graphs(asset_dir: Path, root: Path) -> list[dict[str, Any]]:
    return [
        summarize_graph_file(path, root)
        for path in current_uasset_graph_payload_files(asset_dir)
    ]


def summarize_asset_from_repository(asset_dir: Path, root: Path) -> dict[str, Any]:
    """Build the knowledge projection without reopening legacy graph JSON."""

    with open_asset_repository(asset_dir) as repository:
        overview = repository.query({"operation": "overview", "budgetTokens": 800})
        identity = repository.identity()
        graph_rows = repository.graph_summaries()
        node_rows = repository.node_summaries()
        default_rows = repository.default_summaries(include_values=True)
        gap_projection = repository.gap_summary()
        database_path = repository.database_path
        agent_index_path = repository.agent_index_path
        agent_index = repository.agent_index_text or ""
        source_kind = repository.source_kind
        freshness_status = repository.freshness_status
        release_authority = repository.release_authority
        manifest_sha256 = repository.manifest_sha256
        pointer_sha256 = repository.pointer_sha256

    gap_groups = (
        gap_projection.get("groups", [])
        if isinstance(gap_projection.get("groups"), list)
        else []
    )
    gap_example_rows = [
        example
        for group in gap_groups
        if isinstance(group, dict)
        for example in (
            group.get("examples", [])
            if isinstance(group.get("examples"), list)
            else []
        )
        if isinstance(example, dict)
    ]

    nodes_by_graph: dict[str, list[dict[str, object]]] = defaultdict(list)
    for node in node_rows:
        nodes_by_graph[str(node.get("graph_ref") or "")].append(node)
    graphs: list[dict[str, Any]] = []
    calls: Counter[str] = Counter()
    variables_used: Counter[str] = Counter()
    keyword_group_counts: Counter[str] = Counter()
    for graph in graph_rows:
        graph_nodes = nodes_by_graph.get(str(graph.get("ref") or ""), [])
        graph_calls = Counter(str(node.get("function")) for node in graph_nodes if node.get("function"))
        graph_variables = Counter(str(node.get("variable")) for node in graph_nodes if node.get("variable"))
        node_classes = Counter(str(node.get("class_name")) for node in graph_nodes if node.get("class_name"))
        calls.update(graph_calls)
        variables_used.update(graph_variables)
        groups = [
            group
            for group, words in KEYWORD_GROUPS.items()
            if matches_keywords(str(graph.get("name") or ""), words)
            or any(matches_keywords(name, words) for name in [*graph_calls, *graph_variables])
        ]
        keyword_group_counts.update(groups)
        semantic = [
            {
                "ref": node.get("ref"),
                "name": node.get("name"),
                "class_name": node.get("class_name"),
                "function": node.get("function"),
                "variable": node.get("variable"),
                "x": node.get("x"),
                "y": node.get("y"),
            }
            for node in graph_nodes
            if node.get("function") or node.get("variable") or node.get("event")
        ][:80]
        graphs.append(
            {
                "name": graph.get("name"),
                "graph_type": graph.get("graph_type"),
                "source": graph.get("ref"),
                "read_status": graph.get("status"),
                "confidence": graph.get("confidence"),
                "node_count": graph.get("node_count", 0),
                "pin_count": graph.get("pin_count", 0),
                "link_count": graph.get("link_count", 0),
                "coverage": graph.get("coverage", {}),
                "calls": dict(graph_calls.most_common()),
                "variables": dict(graph_variables.most_common()),
                "node_classes": dict(node_classes.most_common()),
                "semantic_nodes_sample": semantic,
                "keyword_groups": groups,
            }
        )

    default_vars = {
        str(row["name"]): {
            "value": row.get("value"),
            "type": row.get("type"),
            "confidence": row.get("confidence") or "unknown",
            "source": row.get("source") or "uasset_cdo",
            "ref": row.get("ref"),
            "keyword_hits": keyword_hits(str(row["name"])),
            **downstream_default_metadata(row),
        }
        for row in default_rows
    }
    status_counts = Counter(str(graph.get("status") or "unknown") for graph in graph_rows)
    confidence_counts = Counter(str(graph.get("confidence") or "unknown") for graph in graph_rows)
    graph_name_by_ref = {
        str(graph.get("ref") or ""): str(graph.get("name") or "")
        for graph in graph_rows
    }
    local_graph_names = {name for name in graph_name_by_ref.values() if name}
    unresolved_calls = [
        {
            "function": str(node.get("function")),
            "graph": graph_name_by_ref.get(str(node.get("graph_ref") or ""), ""),
            "category": "indexed_external_or_native_call",
            "line": 0,
            "evidence_ref": str(node.get("ref") or ""),
        }
        for node in node_rows
        if node.get("function")
        and str(node.get("function")) not in local_graph_names
        and not is_utility_function(str(node.get("function")))
    ]
    summary = overview.get("summary", {}) if isinstance(overview.get("summary"), dict) else {}
    evidence = [
        {
            "source": str(row.get("ref") or ""),
            "line": 0,
            "keywords": keyword_hits(str(row.get("name") or "")),
            "text": f"Graph {row.get('name')} ({row.get('status')})",
        }
        for row in graph_rows[:80]
    ]
    evidence.extend(
        {
            "source": str(row.get("ref") or ""),
            "line": 0,
            "keywords": keyword_hits(str(row.get("name") or "")),
            "text": f"Default {row.get('name')} ({row.get('type')})",
        }
        for row in default_rows[:40]
    )
    evidence.extend(
        {
            "source": str(row.get("ref") or ""),
            "line": 0,
            "keywords": keyword_hits(str(row.get("reason_code") or "")),
            "text": f"{row.get('status')}: {row.get('reason_code')} — {row.get('next_probe')}",
        }
        for row in gap_example_rows[:40]
    )
    return {
        "schema": "ark-blueprint-knowledge.asset.v1",
        "asset_name": str(identity.get("asset_name") or asset_dir.name),
        "role": infer_asset_role(asset_dir.name),
        "capture_dir": short_path(asset_dir, root),
        "generated": now_iso(),
        "sources": {
            "evidence_store": short_path(database_path, root),
            "agent_index": short_path(
                agent_index_path
                if isinstance(agent_index_path, Path)
                else asset_dir / "output" / "agent_index.md",
                root,
            ),
            "revision": str(identity.get("revision_id") or ""),
            "source_kind": source_kind,
            "freshness_status": freshness_status,
            "release_authority": release_authority,
            "manifest_sha256": manifest_sha256,
            "pointer_sha256": pointer_sha256,
        },
        "package": {
            "asset_path": str(identity.get("object_path") or ""),
            "uasset_path": str(identity.get("uasset_path") or ""),
            "package_name": str(identity.get("object_path") or "").split(".", 1)[0],
            "uasset_size": None,
            "uexp_path": "",
        },
        "metrics": {
            "default_variable_count": len(default_vars),
            "usable_default_variable_count": sum(default_value_is_usable(info) for info in default_vars.values()),
            "not_recovered_default_variable_count": sum(
                not default_value_is_usable(info) for info in default_vars.values()
            ),
            "graph_count": int(summary.get("graphCount") or 0),
            "node_count": int(summary.get("nodeCount") or 0),
            "pin_count": int(summary.get("pinCount") or 0),
            "link_count": int(summary.get("linkObservationCount") or 0),
            "status_counts": dict(status_counts),
            "confidence_counts": dict(confidence_counts),
            "unresolved_call_count": len(unresolved_calls),
            "link_resolution_counts": {},
            "link_kind_counts": {},
            "context_missing_function_count": 0,
            "missing_graph_candidate_count": 0,
            "context_default_candidate_count": 0,
            "evidence_gap_count": int(gap_projection.get("total") or 0),
            "evidence_gap_returned_count": int(gap_projection.get("returned") or 0),
            "evidence_gap_omitted_count": int(gap_projection.get("omitted") or 0),
        },
        "quality_caveats": [
            {
                "kind": str(group.get("reason_code") or "evidence_gap"),
                "status": str(group.get("status") or ""),
                "count": int(group.get("count") or 0),
                "meaning": str(examples[0].get("detail") or "") if examples else "",
                "recommended_use": (
                    str(examples[0].get("next_probe") or "") if examples else ""
                ),
                "evidence_ref": str(examples[0].get("ref") or "") if examples else "",
                "examples": [
                    {
                        "ref": str(example.get("ref") or ""),
                        "scope_kind": str(example.get("scope_kind") or ""),
                        "scope_ref": str(example.get("scope_ref") or ""),
                        "name": str(example.get("name") or ""),
                        "detail": str(example.get("detail") or ""),
                        "next_probe": str(example.get("next_probe") or ""),
                    }
                    for example in examples
                ],
            }
            for group in gap_groups
            if isinstance(group, dict)
            for examples in [
                group.get("examples", [])
                if isinstance(group.get("examples"), list)
                else []
            ]
        ],
        "gap_summary": {
            "total": int(gap_projection.get("total") or 0),
            "returned": int(gap_projection.get("returned") or 0),
            "omitted": int(gap_projection.get("omitted") or 0),
            "truncated": bool(gap_projection.get("truncated")),
            "by_status": gap_projection.get("by_status", {}),
            "by_reason": gap_projection.get("by_reason", {}),
            "groups": [
                {
                    "status": str(group.get("status") or ""),
                    "reason_code": str(group.get("reason_code") or ""),
                    "count": int(group.get("count") or 0),
                }
                for group in gap_groups
                if isinstance(group, dict)
            ],
        },
        "default_variables": default_vars,
        "top_calls": dict(calls.most_common(80)),
        "top_variables_used": dict(variables_used.most_common(80)),
        "graphs": graphs,
        "keyword_group_counts": dict(keyword_group_counts.most_common()),
        "external_asset_paths": extract_game_paths(
            {name: info for name, info in default_vars.items() if default_value_is_usable(info)}
        ),
        "unresolved_calls": unresolved_calls,
        "evidence": evidence[:160],
        "report_snippets": {
            "behavior_summary_head": "\n".join(agent_index.splitlines()[:80]),
            "diagnostics_head": "\n".join(
                f"- {row.get('status')}: {row.get('reason_code')} — {row.get('next_probe')}"
                for row in gap_example_rows[:80]
            ),
        },
    }


def summarize_asset(asset_dir: Path, root: Path) -> dict[str, Any]:
    try:
        resolve_asset_evidence_state(asset_dir, allow_stale=False)
    except FileNotFoundError:
        pass
    else:
        return summarize_asset_from_repository(asset_dir, root)
    name = asset_dir.name
    defaults = read_json(asset_dir / "uasset_class_defaults.json", {})
    graph_nodes = read_json(asset_dir / "uasset_graph_nodes.json", {})
    pin_links = read_json(asset_dir / "uasset_pin_links.json", {})
    context_review = read_json(asset_dir / "output" / "context_review.json", {})
    structure = read_json(asset_dir / "uasset_structure.json", {})
    package = read_json(asset_dir / "uasset_package.json", {})
    export_map = read_json(asset_dir / "uasset_exports.json", [])
    asset_report_text = read_text(asset_dir / "output" / "asset_report.md")
    behavior_text = read_text(asset_dir / "output" / "behavior_summary.md")
    diagnostics_text = read_text(asset_dir / "output" / "diagnostics_report.md")

    graphs = summarize_graphs(asset_dir, root)
    calls: Counter[str] = Counter()
    variables_used: Counter[str] = Counter()
    keyword_group_counts: Counter[str] = Counter()
    for graph in graphs:
        calls.update(graph.get("calls") or {})
        variables_used.update(graph.get("variables") or {})
        keyword_group_counts.update(graph.get("keyword_groups") or [])

    default_vars = summarize_default_variables(defaults if isinstance(defaults, dict) else {})
    all_json_sources = {
        "defaults": defaults,
        "graph_nodes": graph_nodes,
        "structure": structure,
        "package": package,
        "exports_sample": export_map[:250] if isinstance(export_map, list) else export_map,
    }
    unresolved = parse_markdown_table_unresolved(asset_report_text)
    pin_link_summary = pin_links.get("summary", {}) if isinstance(pin_links, dict) else {}
    link_resolution_counts = (
        pin_link_summary.get("resolution_counts", {}) if isinstance(pin_link_summary, dict) else {}
    )
    link_kind_counts = pin_link_summary.get("kind_counts", {}) if isinstance(pin_link_summary, dict) else {}
    quality_caveats: list[dict[str, Any]] = []
    heuristic_links = int(link_resolution_counts.get("resolved_pin_heuristic") or 0) if isinstance(link_resolution_counts, dict) else 0
    if heuristic_links:
        quality_caveats.append(
            {
                "kind": "pin_level_heuristic",
                "count": heuristic_links,
                "meaning": "Target nodes are recovered, but many target pins are inferred by direction/category fallback.",
                "recommended_use": "Use for behavior and dependency knowledge; verify exact pin-level diffs before gameplay-sensitive rewrites.",
            }
        )
    missing_functions = context_review.get("missing_functions", []) if isinstance(context_review, dict) else []
    if isinstance(missing_functions, list) and missing_functions:
        quality_caveats.append(
            {
                "kind": "missing_function_candidates",
                "count": len(missing_functions),
                "meaning": "Some graph-like calls still need local graph capture or notes.md classification.",
                "recommended_use": "Resolve notes before treating the call graph as complete.",
            }
        )

    return {
        "schema": "ark-blueprint-knowledge.asset.v1",
        "asset_name": name,
        "role": infer_asset_role(name),
        "capture_dir": short_path(asset_dir, root),
        "generated": now_iso(),
        "sources": {
            "class_defaults": short_path(asset_dir / "uasset_class_defaults.json", root)
            if (asset_dir / "uasset_class_defaults.json").exists()
            else "",
            "graph_nodes": short_path(asset_dir / "uasset_graph_nodes.json", root)
            if (asset_dir / "uasset_graph_nodes.json").exists()
            else "",
            "asset_report": short_path(asset_dir / "output" / "asset_report.md", root)
            if (asset_dir / "output" / "asset_report.md").exists()
            else "",
            "behavior_summary": short_path(asset_dir / "output" / "behavior_summary.md", root)
            if (asset_dir / "output" / "behavior_summary.md").exists()
            else "",
            "diagnostics_report": short_path(asset_dir / "output" / "diagnostics_report.md", root)
            if (asset_dir / "output" / "diagnostics_report.md").exists()
            else "",
            "pin_links": short_path(asset_dir / "uasset_pin_links.json", root)
            if (asset_dir / "uasset_pin_links.json").exists()
            else "",
            "context_review": short_path(asset_dir / "output" / "context_review.json", root)
            if (asset_dir / "output" / "context_review.json").exists()
            else "",
        },
        "package": {
            "asset_path": graph_nodes.get("asset_path") if isinstance(graph_nodes, dict) else "",
            "uasset_path": graph_nodes.get("uasset_path") if isinstance(graph_nodes, dict) else "",
            "package_name": ((structure.get("summary") or {}).get("package_name") if isinstance(structure, dict) else ""),
            "uasset_size": structure.get("uasset_size") if isinstance(structure, dict) else None,
            "uexp_path": structure.get("uexp_path") if isinstance(structure, dict) else "",
        },
        "metrics": {
            "default_variable_count": len(default_vars),
            "graph_count": graph_nodes.get("graph_count", len(graphs)) if isinstance(graph_nodes, dict) else len(graphs),
            "node_count": graph_nodes.get("node_count", 0) if isinstance(graph_nodes, dict) else 0,
            "pin_count": graph_nodes.get("pin_count", 0) if isinstance(graph_nodes, dict) else 0,
            "link_count": graph_nodes.get("link_count", 0) if isinstance(graph_nodes, dict) else 0,
            "status_counts": graph_nodes.get("status_counts", {}) if isinstance(graph_nodes, dict) else {},
            "confidence_counts": graph_nodes.get("confidence_counts", {}) if isinstance(graph_nodes, dict) else {},
            "unresolved_call_count": len(unresolved),
            "link_resolution_counts": link_resolution_counts if isinstance(link_resolution_counts, dict) else {},
            "link_kind_counts": link_kind_counts if isinstance(link_kind_counts, dict) else {},
            "context_missing_function_count": len(missing_functions) if isinstance(missing_functions, list) else 0,
            "missing_graph_candidate_count": len(missing_functions) if isinstance(missing_functions, list) else 0,
            "context_default_candidate_count": len(context_review.get("default_candidates", []))
            if isinstance(context_review, dict) and isinstance(context_review.get("default_candidates", []), list)
            else 0,
        },
        "quality_caveats": quality_caveats,
        "default_variables": default_vars,
        "top_calls": dict(calls.most_common(80)),
        "top_variables_used": dict(variables_used.most_common(80)),
        "graphs": graphs,
        "keyword_group_counts": dict(keyword_group_counts.most_common()),
        "external_asset_paths": extract_game_paths(all_json_sources),
        "unresolved_calls": unresolved,
        "evidence": collect_markdown_evidence(asset_dir, root),
        "report_snippets": {
            "behavior_summary_head": "\n".join(behavior_text.splitlines()[:80]),
            "diagnostics_head": "\n".join(diagnostics_text.splitlines()[:80]),
        },
    }


def classify_call(function_name: str, local_graph_names: set[str]) -> str:
    if function_name in local_graph_names:
        return "local_blueprint_graph"
    if is_utility_function(function_name):
        return "kismet_or_unreal_utility"
    if function_name.startswith(("BP", "BPTry", "Server_", "Multi_")):
        return "ark_parent_or_blueprint_event"
    if function_name in {
        "GetDinoStatDistributionAgainstMax",
        "GetLevelUpPoints",
        "StaticAddBuff",
        "GetBuff",
        "GetCharacterLevel",
        "BPGetCurrentStatusValue",
        "BPGetMaxStatusValue",
    }:
        return "ark_native_or_parent_implementation"
    return "unresolved_or_external"


def is_utility_function(function_name: str) -> bool:
    return function_name.startswith(KISMET_PREFIXES) or function_name.startswith(UTILITY_PREFIXES)


def is_actionable_unresolved(row: dict[str, Any]) -> bool:
    category = (row.get("category") or "").lower()
    if category in {"kismet_math_or_data", "unreal_engine"}:
        return False
    function = row.get("function") or ""
    if is_utility_function(function):
        return False
    return True


def native_priority(item: dict[str, Any]) -> tuple[int, int, str]:
    function = item.get("function") or ""
    keyword_hit = int(any(word.lower() in function.lower() for word in PRIORITY_NATIVE_KEYWORDS))
    return (-keyword_hit, -int(item.get("example_count") or 0), function)


def build_native_catalog(assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    catalog: dict[str, Any] = {}
    local_graph_names = {
        graph["name"]
        for asset in assets.values()
        for graph in asset.get("graphs", [])
        if graph.get("name")
    }
    for asset_name, asset in assets.items():
        for row in asset.get("unresolved_calls", []):
            function = row.get("function")
            if not function:
                continue
            entry = catalog.setdefault(
                function,
                {
                    "function": function,
                    "classification": classify_call(function, local_graph_names),
                    "certainty": "unknown_body",
                    "why_it_matters": [],
                    "examples": [],
                },
            )
            example = {
                "asset": asset_name,
                "graph": row.get("graph"),
                "category": row.get("category"),
                "source_line": row.get("line"),
            }
            if example not in entry["examples"]:
                entry["examples"].append(example)
    for function, entry in catalog.items():
        if any(word.lower() in function.lower() for word in ["stat", "level", "xp", "experience"]):
            entry["why_it_matters"].append("数值/属性机制可能依赖这个函数，蓝图报告只能看到调用点，不能看到函数内部公式。")
        if any(word.lower() in function.lower() for word in ["buff", "baby", "dino"]):
            entry["why_it_matters"].append("生物状态、Buff 或繁殖训练流程可能依赖这个函数。")
        if not entry["why_it_matters"]:
            entry["why_it_matters"].append("需要父类、原生代码或更多资产报告补充语义。")
        entry["example_count"] = len(entry["examples"])
        if is_utility_function(function):
            entry["classification"] = "kismet_or_unreal_utility"
        else:
            categories = {example.get("category") for example in entry["examples"]}
            if "ark_parent_or_rpc" in categories:
                entry["classification"] = "ark_parent_or_rpc"
            elif "blueprint_graph_candidate" in categories and entry["classification"] == "unresolved_or_external":
                entry["classification"] = "unresolved_blueprint_or_parent"
    return {
        "schema": "ark-blueprint-knowledge.native-functions.v1",
        "generated": now_iso(),
        "functions": sorted(catalog.values(), key=native_priority),
    }


def make_fact(
    fact_id: str,
    subject: str,
    predicate: str,
    value: Any,
    confidence: str,
    source: str,
    note: str = "",
) -> dict[str, Any]:
    return {
        "id": fact_id,
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "confidence": confidence,
        "source": source,
        "note": note,
    }


def keyword_groups_for_focus(focus: str) -> dict[str, list[str]]:
    lowered = focus.lower()
    if "archelon" in lowered:
        return {
            key: words
            for key, words in KEYWORD_GROUPS.items()
            if key.startswith("archelon_")
        }
    if "gigantoraptor" in lowered:
        return {
            key: words
            for key, words in KEYWORD_GROUPS.items()
            if not key.startswith("archelon_")
        }
    return KEYWORD_GROUPS


def build_system_focus(focus: str, assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    themes: dict[str, dict[str, Any]] = {}
    keyword_groups = keyword_groups_for_focus(focus)

    for asset_name, asset in assets.items():
        source = asset.get("sources", {}).get("class_defaults") or asset.get("capture_dir")
        for variable, info in asset.get("default_variables", {}).items():
            if not default_value_is_usable(info):
                continue
            value_text = f"{variable} {info.get('value')}"
            for group, words in keyword_groups.items():
                if matches_keywords(value_text, words):
                    facts.append(
                        make_fact(
                            f"{asset_name}.{variable}",
                            asset_name,
                            "default_variable",
                            {
                                "name": variable,
                                "value": info.get("value"),
                                "type": info.get("type"),
                            },
                            info.get("confidence", "unknown"),
                            source,
                        )
                    )

    for group, words in keyword_groups.items():
        functions: Counter[str] = Counter()
        variables: Counter[str] = Counter()
        graphs: list[dict[str, Any]] = []
        defaults: list[dict[str, Any]] = []
        native_refs: list[dict[str, Any]] = []

        for asset_name, asset in assets.items():
            for graph in asset.get("graphs", []):
                graph_text = " ".join(
                    [
                        graph.get("name") or "",
                        " ".join((graph.get("calls") or {}).keys()),
                        " ".join((graph.get("variables") or {}).keys()),
                    ]
                )
                if matches_keywords(graph_text, words):
                    graphs.append(
                        {
                            "asset": asset_name,
                            "graph": graph.get("name"),
                            "source": graph.get("source"),
                            "read_status": graph.get("read_status"),
                            "confidence": graph.get("confidence"),
                            "node_count": graph.get("node_count"),
                        }
                    )
                    functions.update(graph.get("calls") or {})
                    variables.update(graph.get("variables") or {})
            for variable, info in asset.get("default_variables", {}).items():
                if not default_value_is_usable(info):
                    continue
                if matches_keywords(f"{variable} {info.get('value')}", words):
                    defaults.append(
                        {
                            "asset": asset_name,
                            "name": variable,
                            "value": info.get("value"),
                            "type": info.get("type"),
                            "confidence": info.get("confidence"),
                        }
                    )
            for row in asset.get("unresolved_calls", []):
                if is_actionable_unresolved(row) and matches_keywords(f"{row.get('function')} {row.get('graph')}", words):
                    native_refs.append({"asset": asset_name, **row})

        themes[group] = {
            "description": describe_theme(group),
            "graphs": graphs[:80],
            "default_variables": defaults,
            "top_functions": dict(functions.most_common(50)),
            "top_variables_used": dict(variables.most_common(50)),
            "native_or_parent_refs": native_refs[:80],
            "confidence": infer_theme_confidence(graphs, defaults, native_refs),
        }

    return {
        "schema": "ark-blueprint-knowledge.system-focus.v1",
        "focus": focus,
        "generated": now_iso(),
        "assets": [
            {
                "name": name,
                "role": asset.get("role"),
                "capture_dir": asset.get("capture_dir"),
                "metrics": asset.get("metrics"),
            }
            for name, asset in assets.items()
        ],
        "themes": themes,
        "facts": facts,
        "open_questions": build_open_questions(themes),
        "player_answer_support": build_player_answer_support(themes),
    }


def describe_theme(group: str) -> str:
    return {
        "feather_inheritance": "羽毛与属性继承概率相关的逻辑，包括最高属性选择、权重、物品显示和 native 公式缺口。",
        "baby_training": "幼崽训练、背负/乘客、成长时间、训练目标和提示信息。",
        "bonding_buff": "亲密/绑定 Buff、层数、持续时间和父母/骑手相关状态。",
        "nest_taming": "巢穴、驯养、认领、多用途交互和巢穴生成。",
        "xp_treasure": "经验、宝箱、StoredXP 等收益相关线索。",
        "archelon_algae_cycle": "Archelon 的藻类生成、百分比同步、采集、材质表现和转移状态。",
        "archelon_swim_and_raft": "Archelon 的游泳速度、浮力/重力、水体检测、木筏模式和移动输入覆盖。",
        "archelon_multiuse_inventory": "Archelon 的 MultiUse 菜单、采集执行、物品加入库存和交互文本。",
        "archelon_jellyfish_damage": "Archelon 的水母判定、带电 Buff、伤害调整和采集/击杀相关条件。",
    }.get(group, group)


def infer_theme_confidence(
    graphs: list[dict[str, Any]],
    defaults: list[dict[str, Any]],
    native_refs: list[dict[str, Any]],
) -> str:
    if defaults and not native_refs:
        return "medium"
    if graphs and defaults and native_refs:
        return "mixed"
    if graphs or defaults:
        return "medium"
    return "low"


def theme_has_evidence(theme: dict[str, Any]) -> bool:
    return bool(theme.get("graphs") or theme.get("default_variables") or theme.get("native_or_parent_refs"))


def build_open_questions(themes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    feather = themes.get("feather_inheritance", {})
    if feather.get("native_or_parent_refs"):
        questions.append(
            {
                "topic": "羽毛继承概率公式",
                "question": "GetDinoStatDistributionAgainstMax 的内部公式是什么，输入属性点后输出权重的上下限是多少？",
                "why_open": "当前蓝图能看到调用点和默认权重，但看不到 native/父类函数内部。",
                "next_steps": [
                    "继续追父类或原生导出符号。",
                    "用 DevKit 或游戏内样本导出 30/40/50 点个体的羽毛 CustomData 做回归。",
                    "补读 PrimalItemResource_GigantoraptorFeather 的图页报告，确认显示文本如何使用权重。",
                ],
            }
        )
    xp = themes.get("xp_treasure", {})
    has_gigantoraptor_context = any(
        theme_has_evidence(themes.get(group, {}))
        for group in ("feather_inheritance", "baby_training", "bonding_buff", "nest_taming")
    )
    if has_gigantoraptor_context and not xp.get("default_variables") and not xp.get("graphs"):
        questions.append(
            {
                "topic": "XP 与宝箱收益",
                "question": "XP 存储和宝箱奖励是否来自巨盗龙本体、父类、状态组件还是 PrimalGameData？",
                "why_open": "当前巨盗龙样本里没有形成足够直接的 XP/Treasure 证据链。",
                "next_steps": [
                    "搜索并读取包含 Treasure、StoredXP、XP 的相关资产。",
                    "补读 PrimalGameData 和可能的 LootSet/Inventory 资产。",
                    "把父类 Character/Buff/StatusComponent 资产纳入知识库。",
                ],
            }
        )
    algae = themes.get("archelon_algae_cycle", {})
    if theme_has_evidence(algae):
        questions.append(
            {
                "topic": "Archelon 藻类生成与采集",
                "question": "藻类百分比、采集数量、库存物品和材质表现之间的精确公式是什么？",
                "why_open": "当前图页能串起本资产的状态流，但 AddNewItem 等库存调用在父类/组件侧，物品类与数量的最终落点仍需交叉资产确认。",
                "next_steps": [
                    "补读或索引与 AlgaeQuantity、AddNewItem、TamedInventoryComponentTemplate 相关的物品/库存资产。",
                    "用 GetAlgaePercentage、CollectAlgae、SetAlgaePercentage 三个图页交叉确认百分比和数量边界。",
                    "把 UpdateAlgaeMaterial 的材质参数与实际材质资产对应起来。",
                ],
            }
        )
    swim = themes.get("archelon_swim_and_raft", {})
    if theme_has_evidence(swim):
        questions.append(
            {
                "topic": "Archelon 游泳与木筏模式",
                "question": "木筏模式、完全入水检测和游泳速度覆盖如何影响玩家输入与服务器校正？",
                "why_open": "本资产图页已经覆盖移动分支，但 GetInputDirectionVector、AddMovementInput、CurrentlyHasRider 等调用属于父类/引擎边界。",
                "next_steps": [
                    "将 BP_InterceptMoveForward、CheckRaftMode、BPOverrideCharacterNewSwimVelocity 作为移动改动前的核心复查图页。",
                    "追父类 Character movement 相关函数，确认服务端/客户端分支是否还有隐藏条件。",
                ],
            }
        )
    return questions


def build_player_answer_support(themes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if any(
        theme_has_evidence(themes.get(group, {}))
        for group in ("nest_taming", "baby_training", "bonding_buff", "feather_inheritance")
    ):
        items.extend(
            [
                {
                    "player_question": "巨盗龙对玩家有什么用？",
                    "kb_support": [
                        "nest_taming",
                        "baby_training",
                        "bonding_buff",
                        "feather_inheritance",
                    ],
                    "answer_style": "先说玩家能做什么，再说收益条件，最后列不确定机制。",
                },
                {
                    "player_question": "羽毛具体影响哪个属性、概率怎么算？",
                    "kb_support": ["feather_inheritance", "native_functions"],
                    "answer_style": "区分已知：最高属性会影响羽毛属性；未知：native 公式和上下限。",
                },
                {
                    "player_question": "怎么最大化收益？",
                    "kb_support": [
                        "feather_inheritance",
                        "baby_training",
                        "bonding_buff",
                        "xp_treasure",
                    ],
                    "answer_style": "按可操作步骤回答，并把尚未证实的宝箱/XP部分标为待验证。",
                },
            ]
        )
    if any(
        theme_has_evidence(themes.get(group, {}))
        for group in (
            "archelon_algae_cycle",
            "archelon_swim_and_raft",
            "archelon_multiuse_inventory",
            "archelon_jellyfish_damage",
        )
    ):
        items.extend(
            [
                {
                    "player_question": "Archelon 的藻类机制怎么运作？",
                    "kb_support": ["archelon_algae_cycle", "archelon_multiuse_inventory"],
                    "answer_style": "先讲藻类百分比和时间/游泳条件，再讲 MultiUse 采集与库存落点，最后列父类边界。",
                },
                {
                    "player_question": "改 Archelon 移动或木筏模式要注意什么？",
                    "kb_support": ["archelon_swim_and_raft", "native_functions"],
                    "answer_style": "按客户端输入、服务器校正、水体检测、骑乘状态分层说明风险。",
                },
                {
                    "player_question": "Archelon 和水母/伤害有什么关系？",
                    "kb_support": ["archelon_jellyfish_damage", "archelon_algae_cycle"],
                    "answer_style": "区分本资产能看到的 IsJellyfish/伤害调整和仍在父类/软类判断里的部分。",
                },
            ]
        )
    return items


def render_report(
    focus: str,
    assets: dict[str, dict[str, Any]],
    system: dict[str, Any],
    native_catalog: dict[str, Any],
    global_summary: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# {focus.title()} 背景知识库第一版")
    lines.append("")
    lines.append(f"生成时间：{system['generated']}")
    lines.append("")
    lines.append("## 这是什么")
    lines.append("")
    lines.append(
        "这是给 Blueprint to Code 使用的背景知识库试验版。它由“ARK DevKit 全局资产索引”"
        "和若干专题模块组成。专题模块会把单个蓝图报告、默认值、图页调用、未知 native 函数和证据来源放到一起。"
    )
    lines.append("")
    if global_summary:
        lines.append("## 全局底座")
        lines.append("")
        if global_summary.get("exists"):
            lines.append(f"- 全局 `.uasset` 索引数量：{global_summary.get('asset_count', 0)}")
            lines.append(f"- 已有深度解析 captures：{global_summary.get('captured_asset_count', 0)}")
            lines.append(f"- DevKit Content：`{global_summary.get('content_root')}`")
        else:
            lines.append("- 尚未生成全局 DevKit 索引。")
            if global_summary.get("error"):
                lines.append(f"- 原因：{global_summary.get('error')}")
        lines.append("")
    lines.append("## 已纳入资产")
    lines.append("")
    lines.append("| 资产 | 角色 | 图页 | 节点 | 默认变量 | Pin 启发式 | 未确认图候选 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for name, asset in assets.items():
        metrics = asset.get("metrics", {})
        link_resolution = metrics.get("link_resolution_counts", {}) if isinstance(metrics, dict) else {}
        heuristic_links = link_resolution.get("resolved_pin_heuristic", 0) if isinstance(link_resolution, dict) else 0
        lines.append(
            f"| `{name}` | {asset.get('role')} | {metrics.get('graph_count', 0)} | "
            f"{metrics.get('node_count', 0)} | {metrics.get('default_variable_count', 0)} | "
            f"{heuristic_links} | {metrics.get('missing_graph_candidate_count', 0)} |"
        )
    lines.append("")
    caveat_lines: list[str] = []
    for name, asset in assets.items():
        for caveat in asset.get("quality_caveats", []):
            if not isinstance(caveat, dict):
                continue
            caveat_lines.append(
                "- `{}` {} x{}：{}".format(
                    name,
                    caveat.get("kind", "caveat"),
                    caveat.get("count", 0),
                    caveat.get("recommended_use", caveat.get("meaning", "")),
                )
            )
    if caveat_lines:
        lines.append("## 质量标注")
        lines.append("")
        lines.extend(caveat_lines)
        lines.append("")
    lines.append("## 关键主题")
    lines.append("")
    for group, theme in system.get("themes", {}).items():
        lines.append(f"### {group}")
        lines.append("")
        lines.append(theme.get("description", ""))
        lines.append("")
        lines.append(f"- 可信度：`{theme.get('confidence')}`")
        lines.append(f"- 相关图页：{len(theme.get('graphs', []))}")
        lines.append(f"- 相关默认变量：{len(theme.get('default_variables', []))}")
        lines.append(f"- native/父类缺口：{len(theme.get('native_or_parent_refs', []))}")
        defaults = theme.get("default_variables", [])[:12]
        if defaults:
            lines.append("")
            lines.append("默认值线索：")
            for item in defaults:
                lines.append(
                    f"- `{item['asset']}.{item['name']}` = `{item.get('value')}` "
                    f"({item.get('type')}, {item.get('confidence')})"
                )
        functions = list((theme.get("top_functions") or {}).items())[:12]
        if functions:
            lines.append("")
            lines.append("高频/关键调用：")
            for name, count in functions:
                lines.append(f"- `{name}` x{count}")
        native_refs = theme.get("native_or_parent_refs", [])[:8]
        if native_refs:
            lines.append("")
            lines.append("需要继续追的调用：")
            for ref in native_refs:
                lines.append(
                    f"- `{ref.get('function')}` in `{ref.get('asset')}.{ref.get('graph')}` "
                    f"({ref.get('category')})"
                )
        lines.append("")
    lines.append("## Native / 父类函数缺口")
    lines.append("")
    actionable_functions = sorted(
        [
        item
        for item in native_catalog.get("functions", [])
        if item.get("classification") != "kismet_or_unreal_utility"
        ],
        key=native_priority,
    )
    for item in actionable_functions[:25]:
        lines.append(
            f"- `{item['function']}`：{item['classification']}，出现 {item['example_count']} 次。"
        )
    lines.append("")
    lines.append("## 当前能回答什么")
    lines.append("")
    for item in system.get("player_answer_support", []):
        lines.append(f"- {item['player_question']}：使用 `{', '.join(item['kb_support'])}`，{item['answer_style']}")
    lines.append("")
    lines.append("## 仍然缺什么")
    lines.append("")
    for item in system.get("open_questions", []):
        lines.append(f"### {item['topic']}")
        lines.append("")
        lines.append(f"- 问题：{item['question']}")
        lines.append(f"- 为什么还不能确定：{item['why_open']}")
        lines.append("- 下一步：")
        for step in item.get("next_steps", []):
            lines.append(f"  - {step}")
        lines.append("")
    lines.append("## 文件位置")
    lines.append("")
    lines.append("- `knowledge_base/index.json`：知识库入口。")
    lines.append("- `knowledge_base/assets/*.json`：单个资产的结构化摘要。")
    lines.append(f"- `knowledge_base/systems/{focus}.json`：`{focus}` 主题知识。")
    lines.append("- `knowledge_base/native_functions.json`：native/父类函数缺口。")
    lines.append("- `knowledge_base/evidence.json`：报告行级证据。")
    lines.append("")
    return "\n".join(lines)


def build_knowledge_base(
    root: Path,
    out_dir: Path,
    focus: str,
    asset_names: list[str],
    content_root: Path | None,
    scan_devkit: bool,
) -> dict[str, Any]:
    captures = root / "captures"
    assets: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for name in asset_names:
        asset_dir = captures / name
        if not asset_dir.exists():
            missing.append(name)
            continue
        assets[name] = summarize_asset(asset_dir, root)

    native_catalog = build_native_catalog(assets)
    system = build_system_focus(focus, assets)

    all_evidence = []
    for asset in assets.values():
        for item in asset.get("evidence", []):
            all_evidence.append({"asset": asset["asset_name"], **item})

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, asset in assets.items():
        write_json(out_dir / "assets" / f"{name}.json", asset)
    write_json(out_dir / "systems" / f"{focus}.json", system)
    write_json(out_dir / "native_functions.json", native_catalog)
    write_json(
        out_dir / "evidence.json",
        {
            "schema": "ark-blueprint-knowledge.evidence.v1",
            "generated": now_iso(),
            "items": all_evidence,
        },
    )
    global_index_path = ""
    legacy_global_index_path = ""
    global_report_path = ""
    priority_report_path = ""
    priority_targets_path = ""
    business_database_paths: dict[str, str] = {}
    capture_import_summary: dict[str, Any] = {}
    global_summary: dict[str, Any] = {
        "exists": False,
        "content_root": "",
        "asset_count": 0,
        "captured_asset_count": 0,
    }
    if scan_devkit:
        resolved_content_root = content_root or default_content_root()
        if resolved_content_root and resolved_content_root.is_dir():
            catalog_db_path = out_dir / "db" / "asset_catalog.sqlite"
            legacy_db_path = out_dir / "global" / "asset_index.sqlite"
            ledger_snapshot = read_ledger_snapshot(catalog_db_path)
            if not ledger_snapshot.get("processed") and legacy_db_path.exists():
                ledger_snapshot = read_ledger_snapshot(legacy_db_path)
            global_index = scan_devkit_assets(resolved_content_root.resolve(), captures, root, ledger_snapshot)
            stale_full_json = out_dir / "global" / "asset_index.json"
            if stale_full_json.exists():
                stale_full_json.unlink()
            write_global_asset_database(catalog_db_path, global_index)
            write_json(out_dir / "global" / "asset_index_summary.json", global_index_summary(global_index))
            write_text(out_dir / "global" / "asset_index_report.md", render_global_asset_report(global_index))
            related_priority = collect_related_priority_sources(captures, out_dir / "db")
            priority_targets = build_priority_targets(global_index, related_priority=related_priority)
            write_priority_outputs(out_dir, priority_targets)
            write_priority_database_tables(catalog_db_path, priority_targets)
            replace_deferred_assets(catalog_db_path, priority_targets)
            business_database_paths = write_business_databases(out_dir, global_index, priority_targets)
            capture_import_summary = import_captures_to_business_databases(
                out_dir / "db",
                captures,
                out_dir / "imports",
            )
            legacy_db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(catalog_db_path, legacy_db_path)
            global_index_path = "db/asset_catalog.sqlite"
            legacy_global_index_path = "global/asset_index.sqlite"
            global_report_path = "global/asset_index_report.md"
            priority_report_path = "priorities/priority_targets.md"
            priority_targets_path = "priorities/priority_targets.json"
            global_summary = {
                "exists": True,
                "content_root": global_index["content_root"],
                "asset_count": global_index["asset_count"],
                "knowledge_asset_count": global_index["knowledge_asset_count"],
                "captured_asset_count": global_index["captured_asset_count"],
                "processed_current_count": global_index["processed_current_count"],
                "failed_current_count": global_index["failed_current_count"],
                "database": global_index_path,
                "legacy_database": legacy_global_index_path,
                "business_databases": business_database_paths,
                "capture_import": "imports/capture_import_report.md",
                "summary": "global/asset_index_summary.json",
                "priority_report": priority_report_path,
                "priority_targets": priority_targets_path,
                "deep_read_queue": "priorities/deep_read_queue.txt",
            }
        else:
            global_summary = {
                "exists": False,
                "content_root": str(resolved_content_root or ""),
                "asset_count": 0,
                "captured_asset_count": 0,
                "error": "DevKit Content root was not found.",
            }
    index = {
        "schema": "ark-blueprint-knowledge.index.v1",
        "generated": now_iso(),
        "scope": "ark_devkit_global_with_focused_systems",
        "focus": focus,
        "global_asset_index": global_index_path,
        "legacy_global_asset_index": legacy_global_index_path,
        "global_asset_report": global_report_path,
        "priority_report": priority_report_path,
        "priority_targets": priority_targets_path,
        "global": global_summary,
        "business_databases": business_database_paths,
        "capture_import": capture_import_summary,
        "assets": [
            {
                "name": name,
                "role": asset.get("role"),
                "path": f"assets/{name}.json",
                "metrics": asset.get("metrics", {}),
            }
            for name, asset in assets.items()
        ],
        "systems": [{"name": focus, "path": f"systems/{focus}.json"}],
        "native_functions": "native_functions.json",
        "evidence": "evidence.json",
        "reports": [{"name": f"{focus}_knowledge_base", "path": f"reports/{focus}_knowledge_base.md"}],
        "missing_assets": missing,
    }
    write_json(out_dir / "index.json", index)
    write_text(
        out_dir / "reports" / f"{focus}_knowledge_base.md",
        render_report(focus, assets, system, native_catalog, global_summary),
    )
    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a focused ARK blueprint background knowledge base.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--focus", default=DEFAULT_FOCUS)
    parser.add_argument("--asset", action="append", dest="assets", help="Capture asset folder name. Can be passed multiple times.")
    parser.add_argument("--content-root", type=Path, default=None, help="ARK DevKit ShooterGame/Content directory.")
    parser.add_argument("--no-devkit-index", action="store_true", help="Skip the global filesystem asset index.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    out_dir = args.out.resolve() if args.out else root / "knowledge_base"
    assets = args.assets or DEFAULT_ASSETS
    index = build_knowledge_base(
        root,
        out_dir,
        args.focus,
        assets,
        args.content_root.resolve() if args.content_root else None,
        not args.no_devkit_index,
    )
    print(f"Built knowledge base: {out_dir}")
    print(f"Assets: {len(index['assets'])}; missing: {len(index['missing_assets'])}")
    if isinstance(index.get("global"), dict):
        print(f"Global assets: {index['global'].get('asset_count', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
