"""Reviewed status-component mappings."""

from __future__ import annotations

from .base import (
    ADAPTER_VERSION,
    BUSINESS_SCHEMA_VERSION,
    AdapterSpec,
    LegacyTableSpec,
    SemanticRule,
)


STATUS_ROOTS = (
    "/Script/ShooterGame.PrimalDinoStatusComponent",
    "/Script/ShooterGame.PrimalCharacterStatusComponent",
)
STATUS_NUMERIC_VALUE = SemanticRule(
    rule_id="status.numeric-value.v1",
    source_properties=(
        "MovingStaminaRecoveryRateMultiplier",
        "BaseFoodConsumptionRate",
        "RunningStaminaConsumptionRate",
        "WalkingStaminaConsumptionRate",
        "SwimmingOrFlyingStaminaConsumptionRate",
        "InjuredSpeedModifier",
        "WindedSpeedModifier",
        "WindedSpeedModifierSwimmingOrFlying",
        "MaxExperiencePoints",
        "CraftEarnXPMultiplier",
        "KillEarnXPMultiplier",
        "SpecialEarnXPMultiplier",
        "HarvestEarnXPMultiplier",
    ),
    output_fact_type="STATUS_VALUE",
    allowed_value_kinds=("NUMBER", "INTEGER"),
    required_native_roots=STATUS_ROOTS,
    reject_denormal_number=True,
)
STATUS_VALUE_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "stat_name",
        "base_value",
        "per_level_value",
        "value_type",
        "confidence",
        "source_json",
    }
)
STATUS_RULE_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "rule_key",
        "rule_value",
        "confidence",
        "source_json",
    }
)

ADAPTER = AdapterSpec(
    adapter_id="status_components",
    adapter_version=ADAPTER_VERSION,
    output_fact_types=("STATUS_VALUE",),
    legacy_sources=(
        LegacyTableSpec(
            database_name="status_components.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="status_values",
            required_columns=STATUS_VALUE_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="stat_name",
            value_column="base_value",
            source_json_column="source_json",
            rules=(STATUS_NUMERIC_VALUE,),
        ),
        LegacyTableSpec(
            database_name="status_components.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="leveling_rules",
            required_columns=STATUS_RULE_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="rule_key",
            value_column="rule_value",
            source_json_column="source_json",
            rules=(STATUS_NUMERIC_VALUE,),
        ),
        LegacyTableSpec(
            database_name="status_components.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="taming_status_rules",
            required_columns=STATUS_RULE_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="rule_key",
            value_column="rule_value",
            source_json_column="source_json",
            rules=(STATUS_NUMERIC_VALUE,),
        ),
    ),
)
