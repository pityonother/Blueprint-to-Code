"""Reviewed Buff legacy-to-semantic mappings."""

from __future__ import annotations

from .base import (
    ADAPTER_VERSION,
    BUSINESS_SCHEMA_VERSION,
    AdapterSpec,
    LegacyTableSpec,
    SemanticRule,
)


BUFF_ROOT = ("/Script/ShooterGame.PrimalBuff",)

BUFF_TIMING = SemanticRule(
    rule_id="buff.timing.v1",
    source_properties=(
        "DeactivateAfterTime",
        "Duration",
        "DeactivationLifespan",
        "BuffActivationTime",
        "EffectDuration",
        "BuffTickServerMinTime",
        "BuffTickServerMaxTime",
        "BuffTickClientMinTime",
        "BuffTickClientMaxTime",
    ),
    output_fact_type="STATUS_EFFECT",
    allowed_value_kinds=("NUMBER", "INTEGER"),
    required_native_roots=BUFF_ROOT,
)
BUFF_STACKING = SemanticRule(
    rule_id="buff.stacking.v1",
    source_properties=("AddBuffMaxNumStacks", "MaxStacks"),
    output_fact_type="STATUS_EFFECT",
    allowed_value_kinds=("INTEGER",),
    required_native_roots=BUFF_ROOT,
)
BUFF_REFRESH = SemanticRule(
    rule_id="buff.refresh.v1",
    source_properties=("bAddResetsBuffTime",),
    output_fact_type="STATUS_EFFECT",
    allowed_value_kinds=("BOOLEAN",),
    required_native_roots=BUFF_ROOT,
)
BUFF_NUMERIC_MODIFIER = SemanticRule(
    rule_id="buff.numeric-modifier.v1",
    source_properties=(
        "damageReceivedMultiplier",
        "LoveDamReceiveMultiplier",
        "RageMeleeDamMultiplier",
        "ExDamagePerStack",
        "ExExperiencePerStack",
        "FuryMovementMultiplier",
        "RazorDamagePerStack",
        "PoisonDamagePerStack",
        "DamageScaleWithMeleeMultiplier",
        "TorporScaleWithMeleeMultiplier",
        "RazorDamageScaleWithMeleeMultiplier",
        "PoisonDamageScaleWithMeleeMultiplier",
        "SubmergedMaxSpeedModifier",
        "UnsubmergedMaxSpeedModifier",
    ),
    output_fact_type="STATUS_EFFECT",
    allowed_value_kinds=("NUMBER", "INTEGER"),
    required_native_roots=BUFF_ROOT,
)

BUFF_EFFECT_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "effect_key",
        "effect_value",
        "duration",
        "interval",
        "confidence",
        "source_json",
    }
)
BUFF_STACK_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "stack_key",
        "stack_value",
        "confidence",
        "source_json",
    }
)
BUFF_MODIFIER_COLUMNS = frozenset(
    {
        "id",
        "object_path",
        "stat_name",
        "operation",
        "value",
        "confidence",
        "source_json",
    }
)

ADAPTER = AdapterSpec(
    adapter_id="buffs",
    adapter_version=ADAPTER_VERSION,
    output_fact_types=("STATUS_EFFECT",),
    legacy_sources=(
        LegacyTableSpec(
            database_name="buffs.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="buff_effects",
            required_columns=BUFF_EFFECT_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="effect_key",
            value_column="effect_value",
            source_json_column="source_json",
            rules=(BUFF_TIMING, BUFF_REFRESH),
        ),
        LegacyTableSpec(
            database_name="buffs.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="buff_stacks",
            required_columns=BUFF_STACK_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="stack_key",
            value_column="stack_value",
            source_json_column="source_json",
            rules=(BUFF_STACKING,),
        ),
        LegacyTableSpec(
            database_name="buffs.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="buff_stat_modifiers",
            required_columns=BUFF_MODIFIER_COLUMNS,
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="stat_name",
            value_column="value",
            source_json_column="source_json",
            rules=(BUFF_NUMERIC_MODIFIER,),
        ),
    ),
)
