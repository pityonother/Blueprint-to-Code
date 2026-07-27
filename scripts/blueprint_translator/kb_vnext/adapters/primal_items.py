"""Primal Item semantic mappings from exact current Blueprint defaults."""

from __future__ import annotations

from .base import (
    ADAPTER_VERSION,
    ASSET_FILE_LINEAGE,
    BUSINESS_SCHEMA_VERSION,
    AdapterSpec,
    LegacyTableSpec,
    SemanticRule,
)
from .json_shapes import (
    ASSET_REFERENCE_ARRAY,
    CRAFTING_REQUIREMENT_ARRAY,
    STATUS_VALUE_MODIFIER_ARRAY,
)


ITEM_TEXT_PROPERTY = SemanticRule(
    rule_id="item.text-property.v1",
    source_properties=(
        "DescriptiveNameBase",
        "ItemDescription",
    ),
    output_fact_type="ITEM_PROPERTY",
    allowed_value_kinds=("TEXT",),
    required_native_roots=("/Script/ShooterGame.PrimalItem",),
)
ITEM_NUMBER_PROPERTY = SemanticRule(
    rule_id="item.number-property.v1",
    source_properties=(
        "BaseItemWeight",
        "SpoilingTime",
    ),
    output_fact_type="ITEM_PROPERTY",
    allowed_value_kinds=("NUMBER",),
    required_native_roots=("/Script/ShooterGame.PrimalItem",),
)
ITEM_INTEGER_PROPERTY = SemanticRule(
    rule_id="item.integer-property.v1",
    source_properties=("MaxItemQuantity",),
    output_fact_type="ITEM_PROPERTY",
    allowed_value_kinds=("INTEGER",),
    required_native_roots=("/Script/ShooterGame.PrimalItem",),
)
ITEM_CRAFTING_REQUIREMENTS = SemanticRule(
    rule_id="item.crafting-requirements.v1",
    source_properties=("BaseCraftingResourceRequirements",),
    output_fact_type="ITEM_PROPERTY",
    allowed_value_kinds=("JSON",),
    required_native_roots=("/Script/ShooterGame.PrimalItem",),
    require_nonempty_json=True,
    json_shape=CRAFTING_REQUIREMENT_ARRAY,
)
ITEM_INVENTORY_REQUIREMENTS = SemanticRule(
    rule_id="item.inventory-requirements.v1",
    source_properties=("CraftingRequiresInventoryComponent",),
    output_fact_type="ITEM_PROPERTY",
    allowed_value_kinds=("JSON",),
    required_native_roots=("/Script/ShooterGame.PrimalItem",),
    require_nonempty_json=True,
    json_shape=ASSET_REFERENCE_ARRAY,
)
ITEM_STATUS_MODIFIERS = SemanticRule(
    rule_id="item.status-modifiers.v1",
    source_properties=("UseItemAddCharacterStatusValues",),
    output_fact_type="ITEM_PROPERTY",
    allowed_value_kinds=("JSON",),
    required_native_roots=("/Script/ShooterGame.PrimalItem",),
    require_nonempty_json=True,
    json_shape=STATUS_VALUE_MODIFIER_ARRAY,
    partial=True,
)
ITEM_RULES = (
    ITEM_TEXT_PROPERTY,
    ITEM_NUMBER_PROPERTY,
    ITEM_INTEGER_PROPERTY,
    ITEM_CRAFTING_REQUIREMENTS,
    ITEM_INVENTORY_REQUIREMENTS,
    ITEM_STATUS_MODIFIERS,
)
ITEM_PROPERTY_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "property_name",
        "property_value",
        "value_type",
        "confidence",
        "source_json",
    }
)

ADAPTER = AdapterSpec(
    adapter_id="primal_items",
    adapter_version=ADAPTER_VERSION,
    output_fact_types=("ITEM_PROPERTY",),
    legacy_sources=(
        LegacyTableSpec(
            database_name="primal_items.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="item_properties",
            required_columns=ITEM_PROPERTY_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="property_name",
            value_column="property_value",
            source_json_column="source_json",
            rules=ITEM_RULES,
        ),
    ),
    direct_rules=ITEM_RULES,
    lineage_anchor=ASSET_FILE_LINEAGE,
)
