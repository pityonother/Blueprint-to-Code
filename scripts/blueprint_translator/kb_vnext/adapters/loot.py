"""Conservative loot configuration mappings."""

from __future__ import annotations

from .base import (
    ADAPTER_VERSION,
    ASSET_FILE_LINEAGE,
    BUSINESS_SCHEMA_VERSION,
    AdapterSpec,
    LegacyTableSpec,
    SemanticRule,
)
from .json_shapes import ITEM_SET_ARRAY


LOOT_ROOTS = (
    "/Script/ShooterGame.PrimalStructureItemContainer_SupplyCrate",
    "/Script/ShooterGame.PrimalSupplyCrateItemSet",
)
LOOT_NUMERIC_CONFIG = SemanticRule(
    rule_id="loot.numeric-config.v1",
    source_properties=(
        "MinItemSets",
        "MaxItemSets",
        "MinNumItems",
        "MaxNumItems",
        "MinQuantity",
        "MaxQuantity",
        "MinQuality",
        "MaxQuality",
        "MinRandomQuality",
        "ChanceToBeBlueprintOverride",
        "ChanceToActuallyGiveItem",
        "RequiresMinQuality",
        "EntryWeight",
        "SetWeight",
    ),
    output_fact_type="LOOT_ENTRY",
    allowed_value_kinds=("NUMBER",),
    required_native_roots=LOOT_ROOTS,
    partial=True,
)
LOOT_BOOLEAN_CONFIG = SemanticRule(
    rule_id="loot.boolean-config.v1",
    source_properties=(
        "bItemsRandomWithoutReplacement",
        "bSetsRandomWithoutReplacement",
    ),
    output_fact_type="LOOT_ENTRY",
    allowed_value_kinds=("BOOLEAN",),
    required_native_roots=LOOT_ROOTS,
    partial=True,
)
LOOT_CONFIG_RULES = (LOOT_NUMERIC_CONFIG, LOOT_BOOLEAN_CONFIG)
LOOT_ITEM_SETS = SemanticRule(
    rule_id="loot.item-sets.v1",
    source_properties=("ItemSets",),
    output_fact_type="LOOT_ENTRY",
    allowed_value_kinds=("JSON",),
    required_native_roots=LOOT_ROOTS,
    minimum_confidence="MEDIUM",
    require_nonempty_json=True,
    json_shape=ITEM_SET_ARRAY,
    partial=True,
)
LOOT_SET_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "set_name",
        "set_weight",
        "confidence",
        "source_json",
    }
)
LOOT_REWARD_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "reward_type",
        "reward_value",
        "confidence",
        "source_json",
    }
)
LOOT_ENTRY_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "item_path",
        "entry_weight",
        "quantity_min",
        "quantity_max",
        "quality_min",
        "quality_max",
        "blueprint_chance",
        "confidence",
    }
)

ADAPTER = AdapterSpec(
    adapter_id="loot",
    adapter_version=ADAPTER_VERSION,
    output_fact_types=("LOOT_ENTRY",),
    legacy_sources=(
        LegacyTableSpec(
            database_name="loot.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="loot_item_sets",
            required_columns=LOOT_SET_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="set_name",
            value_column="set_weight",
            source_json_column="source_json",
            rules=(*LOOT_CONFIG_RULES, LOOT_ITEM_SETS),
        ),
        LegacyTableSpec(
            database_name="loot.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="loot_rewards",
            required_columns=LOOT_REWARD_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="reward_type",
            value_column="reward_value",
            source_json_column="source_json",
            rules=(*LOOT_CONFIG_RULES, LOOT_ITEM_SETS),
        ),
        LegacyTableSpec(
            database_name="loot.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="loot_entries",
            required_columns=LOOT_ENTRY_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column=None,
            value_column=None,
            source_json_column=None,
            rules=(),
            reject_all_reason="HEURISTIC_TABLE_CLASSIFICATION",
        ),
    ),
    direct_rules=(*LOOT_CONFIG_RULES, LOOT_ITEM_SETS),
    lineage_anchor=ASSET_FILE_LINEAGE,
)
