"""Harvest facts that can be proved from current typed Blueprint evidence."""

from __future__ import annotations

from .base import (
    ADAPTER_VERSION,
    ASSET_FILE_LINEAGE,
    AdapterSpec,
    SemanticRule,
)
from .json_shapes import HARVEST_ENTRY_ARRAY


HARVEST_COMPONENT_LINK = SemanticRule(
    rule_id="harvest.component-link.v1",
    source_properties=("DeathHarvestingComponent",),
    output_fact_type="HARVEST_RULE",
    allowed_value_kinds=("ENTITY_REF",),
    required_native_roots=("/Script/ShooterGame.PrimalDinoCharacter",),
    partial=True,
)
HARVEST_RESOURCE_RULE = SemanticRule(
    rule_id="harvest.resource-rules.v1",
    source_properties=(
        "HarvestResourceEntries",
        "HarvestDamageTypeEntries",
    ),
    output_fact_type="HARVEST_RULE",
    allowed_value_kinds=("JSON",),
    required_native_roots=("/Script/ShooterGame.PrimalHarvestingComponent",),
    minimum_confidence="MEDIUM",
    require_nonempty_json=True,
    json_shape=HARVEST_ENTRY_ARRAY,
    partial=True,
)

ADAPTER = AdapterSpec(
    adapter_id="harvest",
    adapter_version=ADAPTER_VERSION,
    output_fact_types=("HARVEST_RULE",),
    direct_rules=(HARVEST_COMPONENT_LINK, HARVEST_RESOURCE_RULE),
    lineage_anchor=ASSET_FILE_LINEAGE,
)
