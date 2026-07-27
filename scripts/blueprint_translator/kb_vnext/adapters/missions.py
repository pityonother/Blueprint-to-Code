"""Mission reward mappings from current typed Blueprint evidence."""

from __future__ import annotations

from .base import (
    ADAPTER_VERSION,
    ASSET_FILE_LINEAGE,
    AdapterSpec,
    SemanticRule,
)
from .json_shapes import ITEM_SET_ARRAY


MISSION_CURRENCY_REWARD = SemanticRule(
    rule_id="mission.currency-reward.v1",
    source_properties=(
        "HexagonsOnCompletion",
        "FirstTimeCompletionHexagonRewardBonus",
        "number of hexagons to reward upon gathering items",
    ),
    output_fact_type="MISSION_REWARD",
    allowed_value_kinds=("INTEGER", "NUMBER"),
    required_native_roots=("/Script/ShooterGame.MissionType",),
)
MISSION_ITEM_SETS = SemanticRule(
    rule_id="mission.item-set-reward.v1",
    source_properties=("CustomItemSets",),
    output_fact_type="MISSION_REWARD",
    allowed_value_kinds=("JSON",),
    required_native_roots=("/Script/ShooterGame.MissionType",),
    minimum_confidence="MEDIUM",
    require_nonempty_json=True,
    json_shape=ITEM_SET_ARRAY,
    partial=True,
)

ADAPTER = AdapterSpec(
    adapter_id="missions",
    adapter_version=ADAPTER_VERSION,
    output_fact_types=("MISSION_REWARD",),
    direct_rules=(MISSION_CURRENCY_REWARD, MISSION_ITEM_SETS),
    lineage_anchor=ASSET_FILE_LINEAGE,
)
