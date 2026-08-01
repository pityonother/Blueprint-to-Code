"""Authoritative complete-node static yield simulation."""

from __future__ import annotations

import math

from ..contracts import (
    NORMALIZED_HARVEST_AMOUNT_SCALE,
    UNCLAMPED_FINAL_HIT_HEALTH_MULTIPLIER,
    YIELD_MODEL_VERSION,
)

def estimate_complete_node_yield(
    *,
    base_damage: float,
    damage_multiplier: float,
    harvest_quantity_multiplier: float,
    max_harvest_health: float,
    harvest_health_give_resource_interval: float,
    resource_weight_share: float,
    minimum_quantity: float,
    maximum_quantity: float,
    quantity_random_power: float = 1.0,
    clamp_resource_harvest_damage: bool = False,
    harvest_amount_scale: float = NORMALIZED_HARVEST_AMOUNT_SCALE,
) -> dict[str, float | int | bool | str]:
    """Estimate target-resource units from one fresh, completely harvested node.

    The hit loop mirrors the recovered native static path: damage is bounded by
    remaining harvest health (or 3.5x remaining health for the native unclamped
    branch), interval units are converted to integer grant calls per hit, and a
    successful grant clears the accumulator including its remainder.  Runtime
    Blueprint, buff, gene, mission, and server hooks are deliberately outside
    this normalized profile.

    The current DevKit component corpus serializes a linear quantity random
    power of 1.0 for every resource entry.  Non-linear powers fail closed until
    their native discrete distribution is implemented exactly.
    """

    values = {
        "base_damage": base_damage,
        "damage_multiplier": damage_multiplier,
        "harvest_quantity_multiplier": harvest_quantity_multiplier,
        "max_harvest_health": max_harvest_health,
        "harvest_health_give_resource_interval": harvest_health_give_resource_interval,
        "resource_weight_share": resource_weight_share,
        "minimum_quantity": minimum_quantity,
        "maximum_quantity": maximum_quantity,
        "quantity_random_power": quantity_random_power,
        "harvest_amount_scale": harvest_amount_scale,
    }
    if not all(math.isfinite(float(value)) for value in values.values()):
        raise ValueError("Complete-node yield inputs must be finite numbers.")
    if base_damage <= 0 or damage_multiplier <= 0:
        raise ValueError("Complete-node yield requires positive harvest damage.")
    if harvest_quantity_multiplier < 0:
        raise ValueError("HarvestQuantityMultiplier cannot be negative.")
    if max_harvest_health <= 0 or harvest_health_give_resource_interval <= 0:
        raise ValueError("Complete-node yield requires positive node health and interval.")
    if resource_weight_share <= 0 or resource_weight_share > 1:
        raise ValueError("Resource weight share must be in (0, 1].")
    if maximum_quantity < minimum_quantity or minimum_quantity < 0:
        raise ValueError("Resource quantity bounds are invalid.")
    if not math.isclose(quantity_random_power, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("Only the recovered linear quantity distribution is supported.")
    if harvest_amount_scale <= 0:
        raise ValueError("Normalized harvest amount scale must be positive.")

    remaining_health = float(max_harvest_health)
    interval_threshold = float(harvest_health_give_resource_interval) / float(
        harvest_amount_scale
    )
    damage_per_hit = float(base_damage) * float(damage_multiplier)
    damage_accumulator = 0.0
    grant_calls = 0
    hit_count = 0
    # A positive hit always removes at least its own damage until the final hit,
    # so this guard is far above the number of iterations valid assets need.
    max_iterations = max(1, int(math.ceil(max_harvest_health / damage_per_hit)) + 2)
    while remaining_health > 1e-9:
        hit_count += 1
        if hit_count > max_iterations:
            raise ValueError("Complete-node hit simulation did not converge.")
        credited_health_loss = min(
            damage_per_hit,
            remaining_health
            if clamp_resource_harvest_damage
            else UNCLAMPED_FINAL_HIT_HEALTH_MULTIPLIER * remaining_health,
        )
        damage_accumulator += credited_health_loss
        raw_grant_units = int(math.floor(damage_accumulator / interval_threshold + 1e-9))
        calls_this_hit = int(float(harvest_quantity_multiplier) * raw_grant_units)
        if calls_this_hit > 0:
            grant_calls += calls_this_hit
            damage_accumulator = 0.0
        remaining_health = max(0.0, remaining_health - credited_health_loss)

    expected_quantity_per_selection = (
        float(minimum_quantity) + float(maximum_quantity)
    ) / 2.0
    estimated_yield = (
        float(grant_calls)
        * float(resource_weight_share)
        * expected_quantity_per_selection
    )
    return {
        "estimatedYieldPerNode": estimated_yield,
        "estimatedGrantCallsPerNode": grant_calls,
        "estimatedHitsToDepleteNode": hit_count,
        "expectedQuantityPerSelection": expected_quantity_per_selection,
        "quantityRandomPower": float(quantity_random_power),
        "clampResourceHarvestDamage": bool(clamp_resource_harvest_damage),
        "normalizedHarvestAmountScale": float(harvest_amount_scale),
        "yieldModelVersion": YIELD_MODEL_VERSION,
        "yieldModelBasis": "NATIVE_STATIC_COMPLETE_NODE_HIT_SIMULATION",
    }
