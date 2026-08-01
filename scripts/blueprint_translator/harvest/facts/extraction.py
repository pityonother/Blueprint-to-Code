"""Decode creature attacks, harvest components, and resource overrides."""

from __future__ import annotations

from typing import Any, Iterable

from ..contracts import CONFIRMED, INFORMATIONAL_QUANTITY_GAPS, NOT_RECOVERED

_INFORMATIONAL_QUANTITY_GAPS = INFORMATIONAL_QUANTITY_GAPS

def normalize_unreal_object_identity(value: object) -> str:
    """Normalize short and full Unreal object references to one comparable name.

    ARK DevKit builds may expose the same class as either ``Foo_C`` or
    ``/Game/Path/Foo.Foo_C``.  Raw string equality would make recovered
    harvest overrides disappear after such a serialization-format change.
    """

    text = str(value or "").strip().replace("\\", "/")
    if "'" in text:
        quoted = text.split("'", 1)[1]
        text = quoted.rsplit("'", 1)[0]
    text = text.strip().strip("\"'")
    if ":" in text:
        text = text.split(":", 1)[0]
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.strip().strip("\"'")


def _semantic_property_value(prop: dict[str, Any]) -> Any:
    type_name = str(prop.get("type") or prop.get("type_name") or "")
    if type_name == "ObjectProperty":
        resolved = prop.get("object")
        if isinstance(resolved, str) and resolved:
            return resolved
    if type_name == "ArrayProperty":
        objects = prop.get("objects")
        if isinstance(objects, list) and objects:
            return objects
    return prop.get("value")


def _property_map(properties: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name") or "")
        if name:
            rows[name] = prop
    return rows


def extract_creature_attacks(properties: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project AttackInfos struct elements into compact per-attack facts."""

    attacks: list[dict[str, Any]] = []
    for container in properties:
        if not isinstance(container, dict) or str(container.get("name") or "") != "AttackInfos":
            continue
        parse = container.get("array_parse")
        if not isinstance(parse, dict) or parse.get("parsed") is not True:
            continue
        elements = parse.get("elements")
        if not isinstance(elements, list):
            continue
        for ordinal, element in enumerate(elements):
            if not isinstance(element, dict):
                continue
            nested = element.get("properties")
            fields = _property_map(nested if isinstance(nested, list) else [])

            def value(name: str) -> Any:
                return _semantic_property_value(fields[name]) if name in fields else None

            attack_name = value("AttackName")
            damage_type_property = fields.get("MeleeDamageType")
            damage_type_reference = value("MeleeDamageType")
            damage_type_object_path = (
                damage_type_property.get("object_path")
                if isinstance(damage_type_property, dict)
                else None
            )
            damage_type = normalize_unreal_object_identity(damage_type_reference)
            base_damage = value("MeleeDamageAmount")
            attack_interval = value("AttackInterval")
            missing: list[str] = []
            if not isinstance(attack_name, str) or not attack_name:
                missing.append("AttackName")
            if not isinstance(damage_type, str) or not damage_type:
                missing.append("MeleeDamageType")
            if not isinstance(base_damage, (int, float)):
                missing.append("MeleeDamageAmount")
            if not isinstance(attack_interval, (int, float)):
                missing.append("AttackInterval")
            attacks.append(
                {
                    "attackIndex": int(element.get("index", ordinal)),
                    "attackName": attack_name,
                    "damageType": damage_type,
                    "damageTypeObjectPath": (
                        damage_type_object_path
                        if isinstance(damage_type_object_path, str)
                        and damage_type_object_path
                        else damage_type_reference
                        if isinstance(damage_type_reference, str)
                        else None
                    ),
                    "baseDamage": base_damage,
                    "attackInterval": attack_interval,
                    "riderAttackInterval": value("RiderAttackInterval"),
                    "skipTamed": value("bSkipTamed"),
                    "skipAI": value("bSkipAI"),
                    "onlyOnWildDinos": value("bOnlyOnWildDinos"),
                    "preventWithRider": value("bPreventWithRider"),
                    "useBlueprintCanRiderAttack": value(
                        "bUseBlueprintCanRiderAttack"
                    ),
                    "useBlueprintAdjustOutputDamage": value(
                        "bUseBlueprintAdjustOutputDamage"
                    ),
                    "meleeSwingRadius": value("MeleeSwingRadius"),
                    "basicAttack": value("bBasicAttack"),
                    "animations": value("AttackAnimations") or [],
                    "rawOffsets": element.get("raw_offsets") or {},
                    "valueStatus": CONFIRMED if not missing else NOT_RECOVERED,
                    "gaps": missing,
                }
            )
    return attacks


def _decoded_array(prop: dict[str, Any] | None) -> list[Any] | None:
    if not isinstance(prop, dict):
        return None
    parse = prop.get("array_parse")
    if not isinstance(parse, dict) or parse.get("parsed") is not True:
        return None
    value = _semantic_property_value(prop)
    return list(value) if isinstance(value, list) else None


def _aligned_override_map(
    damage_types: list[Any] | None,
    values: list[Any] | None,
    *,
    gap_code: str,
    gaps: list[str],
) -> dict[str, Any]:
    if damage_types == [] and values is None:
        return {}
    if damage_types is None or values is None:
        gaps.append(gap_code)
        return {}
    if len(damage_types) != len(values):
        gaps.append(gap_code)
        return {}
    return {
        normalize_unreal_object_identity(damage_type): value
        for damage_type, value in zip(damage_types, values)
        if isinstance(damage_type, str) and damage_type
    }


def extract_harvest_component(
    properties: Iterable[dict[str, Any]],
    *,
    component: str,
    object_path: str,
) -> dict[str, Any]:
    """Project HarvestResourceEntries and HarvestDamageTypeEntries."""

    top = _property_map(properties)
    gaps: list[str] = []
    resource_entries: list[dict[str, Any]] = []
    damage_entries: list[dict[str, Any]] = []
    ranking_gaps: list[str] = []
    informational_gaps: list[str] = []

    resource_container = top.get("HarvestResourceEntries")
    resource_parse = resource_container.get("array_parse") if isinstance(resource_container, dict) else None
    if not isinstance(resource_parse, dict) or resource_parse.get("parsed") is not True:
        gaps.append("HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED")
        ranking_gaps.append("HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED")
    else:
        elements = resource_parse.get("elements")
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict):
                continue
            nested = element.get("properties")
            fields = _property_map(nested if isinstance(nested, list) else [])
            raw_resource = _semantic_property_value(fields["ResourceItem"]) if "ResourceItem" in fields else None
            resource = normalize_unreal_object_identity(raw_resource)
            resource_object_path = (
                str(fields["ResourceItem"].get("object_path") or "")
                if isinstance(fields.get("ResourceItem"), dict)
                else ""
            )
            damage_types = _decoded_array(fields.get("DamageTypeEntryValuesOverrides"))
            entry_gaps: list[str] = []
            weight_overrides = _aligned_override_map(
                damage_types,
                _decoded_array(fields.get("DamageTypeEntryWeightOverrides")),
                gap_code="DAMAGE_TYPE_WEIGHT_OVERRIDE_NOT_RECOVERED",
                gaps=entry_gaps,
            )
            min_overrides = _aligned_override_map(
                damage_types,
                _decoded_array(fields.get("DamageTypeEntryMinQuantityOverrides")),
                gap_code="DAMAGE_TYPE_MIN_QUANTITY_OVERRIDE_NOT_RECOVERED",
                gaps=entry_gaps,
            )
            max_overrides = _aligned_override_map(
                damage_types,
                _decoded_array(fields.get("DamageTypeEntryMaxQuantityOverrides")),
                gap_code="DAMAGE_TYPE_MAX_QUANTITY_OVERRIDE_NOT_RECOVERED",
                gaps=entry_gaps,
            )
            if not isinstance(resource, str) or not resource:
                entry_gaps.append("RESOURCE_ITEM_NOT_RECOVERED")
            entry_ranking_gaps = [
                gap
                for gap in entry_gaps
                if gap not in _INFORMATIONAL_QUANTITY_GAPS
            ]
            entry_informational_gaps = [
                gap
                for gap in entry_gaps
                if gap in _INFORMATIONAL_QUANTITY_GAPS
            ]
            resource_entries.append(
                {
                    "entryIndex": element.get("index"),
                    "resource": resource,
                    "resourceObjectPath": resource_object_path,
                    "entryWeight": _semantic_property_value(fields["EntryWeight"])
                    if "EntryWeight" in fields
                    else None,
                    "effectivenessQuantityMultiplier": _semantic_property_value(
                        fields["EffectivenessQuantityMultiplier"]
                    )
                    if "EffectivenessQuantityMultiplier" in fields
                    else None,
                    "overrideQuantityMin": _semantic_property_value(fields["OverrideQuantityMin"])
                    if "OverrideQuantityMin" in fields
                    else None,
                    "overrideQuantityMax": _semantic_property_value(fields["OverrideQuantityMax"])
                    if "OverrideQuantityMax" in fields
                    else None,
                    "overrideQuantityRandomPower": _semantic_property_value(
                        fields["OverrideQuantityRandomPower"]
                    )
                    if "OverrideQuantityRandomPower" in fields
                    else None,
                    "weightOverrides": weight_overrides,
                    "minQuantityOverrides": min_overrides,
                    "maxQuantityOverrides": max_overrides,
                    "damageTypeEntryValues": damage_types,
                    "gaps": sorted(set(entry_gaps)),
                    "rankingGaps": sorted(set(entry_ranking_gaps)),
                    "informationalGaps": sorted(set(entry_informational_gaps)),
                    "rawOffsets": element.get("raw_offsets") or {},
                }
            )
            gaps.extend(entry_gaps)
            informational_gaps.extend(entry_informational_gaps)

    damage_container = top.get("HarvestDamageTypeEntries")
    damage_parse = damage_container.get("array_parse") if isinstance(damage_container, dict) else None
    if not isinstance(damage_parse, dict) or damage_parse.get("parsed") is not True:
        gaps.append("HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED")
        ranking_gaps.append("HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED")
    else:
        elements = damage_parse.get("elements")
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict):
                continue
            nested = element.get("properties")
            fields = _property_map(nested if isinstance(nested, list) else [])
            damage_type_parent = (
                normalize_unreal_object_identity(_semantic_property_value(fields["DamageTypeParent"]))
                if "DamageTypeParent" in fields
                else None
            )
            damage_multiplier = (
                _semantic_property_value(fields["DamageMultiplier"])
                if "DamageMultiplier" in fields
                else None
            )
            harvest_quantity_multiplier = (
                _semantic_property_value(fields["HarvestQuantityMultiplier"])
                if "HarvestQuantityMultiplier" in fields
                else None
            )
            entry_gaps: list[str] = []
            if not isinstance(damage_type_parent, str) or not damage_type_parent:
                entry_gaps.append("DAMAGE_TYPE_PARENT_NOT_RECOVERED")
            if not isinstance(damage_multiplier, (int, float)):
                entry_gaps.append("DAMAGE_MULTIPLIER_NOT_RECOVERED")
            if not isinstance(harvest_quantity_multiplier, (int, float)):
                entry_gaps.append("HARVEST_QUANTITY_MULTIPLIER_NOT_RECOVERED")
            damage_entries.append(
                {
                    "entryIndex": element.get("index"),
                    "damageTypeParent": damage_type_parent,
                    "damageMultiplier": damage_multiplier,
                    "harvestQuantityMultiplier": harvest_quantity_multiplier,
                    "damageHarvestAdditionalEffectiveness": _semantic_property_value(
                        fields["DamageHarvestAdditionalEffectiveness"]
                    )
                    if "DamageHarvestAdditionalEffectiveness" in fields
                    else None,
                    "gaps": sorted(set(entry_gaps)),
                    "rawOffsets": element.get("raw_offsets") or {},
                }
            )
            gaps.extend(entry_gaps)

    return {
        "component": component,
        "objectPath": object_path,
        "resourceEntries": resource_entries,
        "damageEntries": damage_entries,
        "maxHarvestHealth": _semantic_property_value(top["MaxHarvestHealth"])
        if "MaxHarvestHealth" in top
        else None,
        "harvestHealthGiveResourceInterval": _semantic_property_value(
            top["HarvestHealthGiveResourceInterval"]
        )
        if "HarvestHealthGiveResourceInterval" in top
        else None,
        "clampResourceHarvestDamage": bool(
            _semantic_property_value(top["bClampResourceHarvestDamage"])
        )
        if "bClampResourceHarvestDamage" in top
        else False,
        "clampResourceHarvestDamageSource": (
            "SERIALIZED_EFFECTIVE_DEFAULT"
            if "bClampResourceHarvestDamage" in top
            else "NATIVE_DEFAULT_FALSE"
        ),
        "isSingleUnitHarvest": bool(
            _semantic_property_value(top["bIsSingleUnitHarvest"])
        )
        if "bIsSingleUnitHarvest" in top
        else False,
        "isSingleUnitHarvestSource": (
            "SERIALIZED_EFFECTIVE_DEFAULT"
            if "bIsSingleUnitHarvest" in top
            else "NATIVE_DEFAULT_FALSE"
        ),
        "gaps": sorted(set(gaps)),
        "rankingGaps": sorted(set(ranking_gaps)),
        "informationalGaps": sorted(set(informational_gaps)),
    }


def extract_resource_damage_overrides(
    properties: Iterable[dict[str, Any]],
    damage_type: str,
) -> dict[str, Any]:
    top = _property_map(properties)
    items = _decoded_array(top.get("OverrideDamageForResourceHarvestingItems"))
    replacements = _decoded_array(top.get("OverrideDamageForResourceHarvestingDamageTypes"))
    gaps: list[str] = []
    if (
        "OverrideDamageForResourceHarvestingItems" not in top
        and "OverrideDamageForResourceHarvestingDamageTypes" not in top
    ):
        return {"damageType": damage_type, "overrides": {}, "gaps": []}
    if items is None or replacements is None:
        gaps.append("RESOURCE_DAMAGE_OVERRIDE_NOT_RECOVERED")
        return {"damageType": damage_type, "overrides": {}, "gaps": gaps}
    if len(items) != len(replacements):
        gaps.append("RESOURCE_DAMAGE_OVERRIDE_LENGTH_MISMATCH")
        return {"damageType": damage_type, "overrides": {}, "gaps": gaps}
    normalized_damage_type = normalize_unreal_object_identity(damage_type)
    overrides = {
        (
            normalized_damage_type,
            normalize_unreal_object_identity(resource),
        ): normalize_unreal_object_identity(replacement)
        for resource, replacement in zip(items, replacements)
        if isinstance(resource, str)
        and resource
        and isinstance(replacement, str)
        and replacement
    }
    return {"damageType": normalized_damage_type, "overrides": overrides, "gaps": gaps}
