"""Reviewed structural validators for semantic JSON facts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


ITEM_SET_ARRAY = "ARK_ITEM_SET_ARRAY"
HARVEST_ENTRY_ARRAY = "ARK_HARVEST_ENTRY_ARRAY"
CRAFTING_REQUIREMENT_ARRAY = "ARK_CRAFTING_REQUIREMENT_ARRAY"
STATUS_VALUE_MODIFIER_ARRAY = "ARK_STATUS_VALUE_MODIFIER_ARRAY"
ASSET_REFERENCE_ARRAY = "ARK_ASSET_REFERENCE_ARRAY"
KNOWN_JSON_SHAPES = frozenset(
    {
        ITEM_SET_ARRAY,
        HARVEST_ENTRY_ARRAY,
        CRAFTING_REQUIREMENT_ARRAY,
        STATUS_VALUE_MODIFIER_ARRAY,
        ASSET_REFERENCE_ARRAY,
    }
)


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _asset_reference(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(
        ("/Game/", "/Mods/", "/Script/")
    ):
        return False
    return (
        "\\" not in value
        and ":" not in value
        and ".." not in value
        and "." in value
    )


def _object_array(value: object) -> list[Mapping[str, object]] | None:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
        or not all(isinstance(item, Mapping) for item in value)
    ):
        return None
    return [item for item in value if isinstance(item, Mapping)]


def _item_entry_has_semantics(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    item_class = value.get("ItemClass")
    items = value.get("Items")
    if item_class not in (None, "") and not _asset_reference(item_class):
        return False
    if items is not None and (
        not isinstance(items, list)
        or not items
        or not all(_asset_reference(item) for item in items)
    ):
        return False
    if not _asset_reference(item_class) and not (
        isinstance(items, list) and items
    ):
        return False
    if "ItemEntryName" in value and not isinstance(
        value["ItemEntryName"],
        str,
    ):
        return False
    numeric_keys = (
        "MinQuantity",
        "MaxQuantity",
        "MinQuality",
        "MaxQuality",
        "EntryWeight",
        "ChanceToBeBlueprintOverride",
        "ChanceToActuallyGiveItem",
    )
    if any(
        key in value and not _finite_number(value[key])
        for key in numeric_keys
    ):
        return False
    for minimum_key, maximum_key in (
        ("MinQuantity", "MaxQuantity"),
        ("MinQuality", "MaxQuality"),
    ):
        if (
            minimum_key in value
            and maximum_key in value
            and float(value[maximum_key]) < float(value[minimum_key])
        ):
            return False
    for chance_key in (
        "ChanceToBeBlueprintOverride",
        "ChanceToActuallyGiveItem",
    ):
        if chance_key in value and not 0 <= float(value[chance_key]) <= 1:
            return False
    return True


def _valid_item_set_array(value: object) -> bool:
    entries = _object_array(value)
    if entries is None:
        return False
    for entry in entries:
        item_entries = entry.get("ItemEntries")
        override = entry.get("ItemSetOverride")
        minimum = entry.get("MinNumItems")
        maximum = entry.get("MaxNumItems")
        weight = entry.get("SetWeight")
        without_replacement = entry.get("bItemsRandomWithoutReplacement")
        if not isinstance(item_entries, list):
            return False
        if item_entries and not all(
            _item_entry_has_semantics(item) for item in item_entries
        ):
            return False
        if override not in (None, "") and not _asset_reference(override):
            return False
        if not item_entries and not _asset_reference(override):
            return False
        if not all(
            _finite_number(item) for item in (minimum, maximum, weight)
        ):
            return False
        if float(minimum) < 0 or float(maximum) < float(minimum):
            return False
        if float(weight) < 0:
            return False
        if not isinstance(without_replacement, bool):
            return False
    return True


def _valid_harvest_entry_array(
    property_name: str,
    value: object,
) -> bool:
    entries = _object_array(value)
    if entries is None:
        return False
    if property_name == "HarvestResourceEntries":
        for entry in entries:
            resource = entry.get(
                "ResourceItemClass",
                entry.get("ResourceClass"),
            )
            if not _asset_reference(resource):
                return False
            numeric = (
                "BaseResourceAmount",
                "Amount",
                "MinQuantity",
                "MaxQuantity",
                "EntryWeight",
            )
            present = [key for key in numeric if key in entry]
            if not present or any(
                not _finite_number(entry[key]) for key in present
            ):
                return False
            if any(float(entry[key]) < 0 for key in present):
                return False
            if (
                "MinQuantity" in entry
                and "MaxQuantity" in entry
                and float(entry["MaxQuantity"])
                < float(entry["MinQuantity"])
            ):
                return False
        return True
    if property_name == "HarvestDamageTypeEntries":
        for entry in entries:
            damage_type = entry.get(
                "DamageType",
                entry.get("DamageTypeClass"),
            )
            if not _asset_reference(damage_type):
                return False
            multiplier_keys = (
                "HarvestAmountMultiplier",
                "ResourceMultiplier",
            )
            present = [key for key in multiplier_keys if key in entry]
            if not present or any(
                not _finite_number(entry[key])
                or float(entry[key]) < 0
                for key in present
            ):
                return False
        return True
    return False


def _valid_crafting_requirement_array(value: object) -> bool:
    entries = _object_array(value)
    if entries is None:
        return False
    return all(
        _asset_reference(entry.get("ResourceItemType"))
        and _finite_number(entry.get("BaseResourceRequirement"))
        and float(entry["BaseResourceRequirement"]) > 0
        and isinstance(entry.get("bCraftingRequireExactResourceType"), bool)
        for entry in entries
    )


def _valid_status_value_modifier_array(value: object) -> bool:
    entries = _object_array(value)
    if entries is None:
        return False
    boolean_keys = (
        "bAddOverTime",
        "bAddOverTimeSpeedInSeconds",
        "bContinueOnUnchangedValue",
        "bDontRequireLessThanMaxToUse",
        "bForceImmediateTick",
        "bForceUseStatOnDinos",
        "bPercentOfCurrentStatusValue",
        "bPercentOfMaxStatusValue",
        "bResetExistingModifierDescriptionIndex",
        "bSetAdditionalValue",
        "bSetValue",
        "bUseItemQuality",
    )
    numeric_keys = (
        "AddOverTimeSpeed",
        "BaseAmountToAdd",
        "ItemQualityAddValueMultiplier",
        "LimitExistingModifierDescriptionToMaxAmount",
        "PercentAbsoluteMaxValue",
        "PercentAbsoluteMinValue",
        "StatusValueModifierDescriptionIndex",
    )
    for entry in entries:
        status_value = entry.get("StatusValueType")
        if (
            not isinstance(status_value, str)
            or not status_value.startswith("EPrimalCharacterStatusValue::")
        ):
            return False
        if any(
            key not in entry or not _finite_number(entry[key])
            for key in ("AddOverTimeSpeed", "BaseAmountToAdd")
        ):
            return False
        if any(
            key in entry and not _finite_number(entry[key])
            for key in numeric_keys
        ):
            return False
        if any(
            key in entry and not isinstance(entry[key], bool)
            for key in boolean_keys
        ):
            return False
    return True


def _valid_asset_reference_array(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_asset_reference(item) for item in value)
    )


def semantic_json_shape_is_valid(
    *,
    shape: str,
    property_name: str,
    value: object,
) -> bool:
    """Return whether a decoded JSON value matches its reviewed domain shape."""

    if not shape:
        return True
    if shape == ITEM_SET_ARRAY:
        return _valid_item_set_array(value)
    if shape == HARVEST_ENTRY_ARRAY:
        return _valid_harvest_entry_array(property_name, value)
    if shape == CRAFTING_REQUIREMENT_ARRAY:
        return _valid_crafting_requirement_array(value)
    if shape == STATUS_VALUE_MODIFIER_ARRAY:
        return _valid_status_value_modifier_array(value)
    if shape == ASSET_REFERENCE_ARRAY:
        return _valid_asset_reference_array(value)
    raise ValueError(f"Unknown semantic JSON shape: {shape}")
