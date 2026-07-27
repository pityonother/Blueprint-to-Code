"""Fail-closed contracts shared by ARK semantic domain adapters."""

from __future__ import annotations

from dataclasses import dataclass


BUSINESS_SCHEMA_VERSION = "ark-devkit-knowledge.business-db.v1"
ASSET_INDEX_SCHEMA_VERSION = "ark-devkit-knowledge.global-asset-index.v1"
ADAPTER_VERSION = "ark-kb-semantic-adapter/v2"


@dataclass(frozen=True)
class SemanticRule:
    """One reviewed mapping from a typed Blueprint property to an ontology fact."""

    rule_id: str
    source_properties: tuple[str, ...]
    output_fact_type: str
    allowed_value_kinds: tuple[str, ...]
    required_native_roots: tuple[str, ...]
    minimum_confidence: str = "HIGH"
    require_nonempty_json: bool = False
    json_shape: str = ""
    reject_denormal_number: bool = False
    partial: bool = False


@dataclass(frozen=True)
class LegacyTableSpec:
    """Exact legacy schema surface that an adapter is allowed to inspect."""

    database_name: str
    schema_version: str
    table_name: str
    required_columns: frozenset[str]
    primary_key_columns: tuple[str, ...]
    object_path_column: str
    property_column: str | None
    value_column: str | None
    source_json_column: str | None
    rules: tuple[SemanticRule, ...]
    reference_value_column: str | None = None
    reject_all_reason: str = ""


@dataclass(frozen=True)
class LineageAnchorSpec:
    """Non-authoritative legacy row used only to retain source-row lineage."""

    database_name: str
    schema_version: str
    table_name: str
    required_columns: frozenset[str]
    object_path_column: str


@dataclass(frozen=True)
class AdapterSpec:
    """Versioned contract for one semantic domain adapter."""

    adapter_id: str
    adapter_version: str
    output_fact_types: tuple[str, ...]
    legacy_sources: tuple[LegacyTableSpec, ...] = ()
    direct_rules: tuple[SemanticRule, ...] = ()
    lineage_anchor: LineageAnchorSpec | None = None


ASSET_FILE_LINEAGE = LineageAnchorSpec(
    database_name="asset_catalog.sqlite",
    schema_version=ASSET_INDEX_SCHEMA_VERSION,
    table_name="asset_files",
    required_columns=frozenset({"object_path", "asset_type", "domain"}),
    object_path_column="object_path",
)
