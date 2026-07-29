"""PrimalGameData registration adapter; stale rows remain unverified."""

from __future__ import annotations

from .base import (
    ADAPTER_VERSION,
    BUSINESS_SCHEMA_VERSION,
    AdapterSpec,
    LegacyTableSpec,
)


NOT_REGISTRATION_GOLD_VERIFIED = "REGISTRATION_KIND_NOT_GOLD_VERIFIED"


def _registration_columns(value_column: str, name_column: str) -> frozenset[str]:
    return frozenset(
        {
            "id",
            "object_path",
            value_column,
            name_column,
            "source_property",
            "confidence",
        }
    )


ADAPTER = AdapterSpec(
    adapter_id="primal_game_data",
    adapter_version=ADAPTER_VERSION,
    output_fact_types=("SYSTEM_REGISTRATION",),
    legacy_sources=(
        LegacyTableSpec(
            database_name="primal_game_data.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="registered_buffs",
            required_columns=_registration_columns("buff_path", "buff_name"),
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="source_property",
            value_column="buff_path",
            source_json_column=None,
            reference_value_column="buff_path",
            rules=(),
            reject_all_reason=NOT_REGISTRATION_GOLD_VERIFIED,
        ),
        LegacyTableSpec(
            database_name="primal_game_data.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="registered_items",
            required_columns=_registration_columns("item_path", "item_name"),
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="source_property",
            value_column="item_path",
            source_json_column=None,
            reference_value_column="item_path",
            rules=(),
            reject_all_reason=NOT_REGISTRATION_GOLD_VERIFIED,
        ),
        LegacyTableSpec(
            database_name="primal_game_data.sqlite",
            schema_version=BUSINESS_SCHEMA_VERSION,
            table_name="registered_creatures",
            required_columns=_registration_columns(
                "creature_path",
                "creature_name",
            ),
            primary_key_columns=("id",),
            object_path_column="object_path",
            property_column="source_property",
            value_column="creature_path",
            source_json_column=None,
            reference_value_column="creature_path",
            rules=(),
            reject_all_reason=NOT_REGISTRATION_GOLD_VERIFIED,
        ),
    ),
)
