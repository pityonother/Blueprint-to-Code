"""Harvest facts recovered from serialized assets."""

from .extraction import (
    extract_creature_attacks,
    extract_harvest_component,
    extract_resource_damage_overrides,
    normalize_unreal_object_identity,
)

__all__ = [
    "extract_creature_attacks",
    "extract_harvest_component",
    "extract_resource_damage_overrides",
    "normalize_unreal_object_identity",
]
