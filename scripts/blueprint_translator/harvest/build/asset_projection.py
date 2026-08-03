"""Project recovered creature and component facts into compact catalog records."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from ..evaluation.contracts import TAMED_RIDDEN
from ..evaluation.aggregation import extract_creature_identity
from ..facts.extraction import extract_creature_attacks
from rank_ark_harvest import uasset_object_path


def _semantic_value(prop: dict[str, Any] | None) -> Any:
    if not isinstance(prop, dict):
        return None
    if str(prop.get("type") or "") == "ObjectProperty":
        return prop.get("object_path") or prop.get("object") or prop.get("value")
    return prop.get("value")


def _effective_properties(
    ancestry: dict[str, Any],
    load_asset: Callable[[Path], dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    paths = [Path(value) for value in ancestry.get("sourcePaths", []) if value]
    for source in reversed(paths):
        fact = load_asset(source)
        rows = fact.get("properties")
        source_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        has_attack_array = any(
            str(row.get("name") or "") == "AttackInfos"
            and str(row.get("type") or "") == "ArrayProperty"
            for row in source_rows
        )
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            if (
                name == "AttackInfos"
                and str(row.get("type") or "") != "ArrayProperty"
                and has_attack_array
            ):
                # Some current DevKit assets expose a low-confidence ghost StructProperty
                # immediately after the real array tag. It is parser noise, not a child
                # override, and must not erase an explicit empty AttackInfos array.
                continue
            if name:
                merged[name] = row
    return list(merged.values())


def _attack_applicability(attack: dict[str, Any]) -> dict[str, Any]:
    if attack.get("skipTamed") is True:
        return {
            "scope": TAMED_RIDDEN,
            "status": "INELIGIBLE",
            "reasonCodes": ["ATTACK_SKIPPED_WHEN_TAMED"],
        }
    if attack.get("onlyOnWildDinos") is True:
        return {
            "scope": TAMED_RIDDEN,
            "status": "INELIGIBLE",
            "reasonCodes": ["ATTACK_ONLY_ON_WILD_DINOS"],
        }
    if attack.get("preventWithRider") is True:
        return {
            "scope": TAMED_RIDDEN,
            "status": "INELIGIBLE",
            "reasonCodes": ["ATTACK_PREVENTED_WITH_RIDER"],
        }
    conditional_reasons: list[str] = []
    if attack.get("useBlueprintCanRiderAttack") is True:
        conditional_reasons.append("BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED")
    if attack.get("useBlueprintAdjustOutputDamage") is True:
        conditional_reasons.append(
            "BLUEPRINT_ADJUST_OUTPUT_DAMAGE_NOT_RECOVERED"
        )
    if conditional_reasons:
        return {
            "scope": TAMED_RIDDEN,
            "status": "CONDITIONAL",
            "reasonCodes": conditional_reasons,
        }
    return {"scope": TAMED_RIDDEN, "status": "ELIGIBLE", "reasonCodes": []}


def _compact_attack(attack: dict[str, Any], creature_object_path: str) -> dict[str, Any]:
    keys = (
        "attackIndex",
        "attackName",
        "damageType",
        "damageTypeObjectPath",
        "baseDamage",
        "attackInterval",
        "riderAttackInterval",
        "skipTamed",
        "skipAI",
        "onlyOnWildDinos",
        "preventWithRider",
        "useBlueprintCanRiderAttack",
        "useBlueprintAdjustOutputDamage",
        "meleeSwingRadius",
        "basicAttack",
        "valueStatus",
        "gaps",
    )
    result = {key: attack.get(key) for key in keys if key in attack}
    result["attackId"] = f"{creature_object_path}#{attack.get('attackIndex')}"
    result["applicability"] = _attack_applicability(attack)
    return result


def _tameability(properties: list[dict[str, Any]]) -> dict[str, Any]:
    rows = {
        str(row.get("name") or ""): row
        for row in properties
        if isinstance(row, dict) and row.get("name")
    }
    boss = _semantic_value(rows.get("bIsBossDino"))
    can_be_tamed = _semantic_value(rows.get("bCanBeTamed"))
    if boss is True:
        return {"status": "PREVENTED", "reasonCodes": ["BOSS_DINO"]}
    if can_be_tamed is False:
        return {"status": "PREVENTED", "reasonCodes": ["CANNOT_BE_TAMED"]}
    if can_be_tamed is True:
        return {"status": "ALLOWED", "reasonCodes": []}
    return {"status": "UNKNOWN", "reasonCodes": ["TAMEABILITY_NOT_RECOVERED"]}


def _rideability(properties: list[dict[str, Any]]) -> dict[str, Any]:
    rows = {
        str(row.get("name") or ""): row
        for row in properties
        if isinstance(row, dict) and row.get("name")
    }
    allow_riding = _semantic_value(rows.get("bAllowRiding"))
    if allow_riding is True:
        return {"status": "ALLOWED", "reasonCodes": []}
    if allow_riding is False:
        return {"status": "PREVENTED", "reasonCodes": ["RIDING_NOT_ALLOWED"]}
    return {"status": "UNKNOWN", "reasonCodes": ["RIDEABILITY_NOT_RECOVERED"]}


def build_creature_record(
    path: Path,
    *,
    content_root: Path,
    load_asset: Callable[[Path], dict[str, Any]],
    ancestry: dict[str, Any],
) -> dict[str, Any]:
    if ancestry.get("status") != "CONFIRMED":
        raise ValueError("Creature ancestry must be confirmed before projection.")
    resolved = Path(path).resolve()
    object_path = uasset_object_path(resolved, content_root)
    properties = _effective_properties(ancestry, load_asset)
    identity = extract_creature_identity(properties, fallback_name=resolved.stem)
    attacks = extract_creature_attacks(properties)
    attack_infos = next(
        (
            row
            for row in properties
            if isinstance(row, dict) and str(row.get("name") or "") == "AttackInfos"
        ),
        None,
    )
    parse = attack_infos.get("array_parse") if isinstance(attack_infos, dict) else None
    if isinstance(parse, dict) and parse.get("parsed") is True:
        attack_status = "DECODED" if attacks else "CONFIRMED_EMPTY"
    elif isinstance(attack_infos, dict) and (
        int(attack_infos.get("declared_size") or 0) == 0
        and attack_infos.get("value") == []
    ):
        attack_status = "CONFIRMED_EMPTY"
    else:
        attack_status = "NOT_RECOVERED"
    gaps: list[str] = []
    if identity["identityStatus"] != "CONFIRMED":
        gaps.append("DINO_NAME_TAG_NOT_RECOVERED")
    if attack_status == "NOT_RECOVERED":
        gaps.append("ATTACK_INFOS_NOT_RECOVERED")
    return {
        "assetId": "creature_" + hashlib.sha256(object_path.encode("utf-8")).hexdigest()[:20],
        **identity,
        "objectPath": object_path,
        "ancestryStatus": "CONFIRMED",
        "parentChain": ancestry.get("objectPathChain") or [],
        "tameability": _tameability(properties),
        "rideability": _rideability(properties),
        "attackCatalogStatus": attack_status,
        "attacks": [_compact_attack(attack, object_path) for attack in attacks],
        "gaps": sorted(gaps),
    }


def _compact_component(component: dict[str, Any]) -> dict[str, Any]:
    resource_entries = []
    for entry in component.get("resourceEntries", []):
        if not isinstance(entry, dict):
            continue
        resource_entries.append(
            {
                key: value
                for key, value in entry.items()
                if key not in {"rawOffsets", "damageTypeEntryValues"}
            }
        )
    damage_entries = []
    for entry in component.get("damageEntries", []):
        if not isinstance(entry, dict):
            continue
        damage_entries.append(
            {key: value for key, value in entry.items() if key != "rawOffsets"}
        )
    keys = (
        "component",
        "objectPath",
        "maxHarvestHealth",
        "harvestHealthGiveResourceInterval",
        "gaps",
        "rankingGaps",
        "informationalGaps",
    )
    return {
        **{key: component.get(key) for key in keys if key in component},
        "resourceEntries": resource_entries,
        "damageEntries": damage_entries,
    }
