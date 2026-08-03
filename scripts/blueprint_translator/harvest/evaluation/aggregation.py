"""Pure identity, eligibility, metric, and aggregation helpers."""

from __future__ import annotations

import math
from typing import Any, Iterable

from .contracts import METRIC_CONTRACTS, TAMED_RIDDEN


def _estimated_yield(row: dict[str, Any]) -> float | None:
    """Return the only numeric value allowed to influence ranking order."""

    value = row.get("estimatedYieldPerNode")
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        return None
    return float(value)


def _stable_row_identity(row: dict[str, Any]) -> tuple[str, str, int, str]:
    """Make equal-yield best-attack and result selection deterministic."""

    attack_index = row.get("attackIndex")
    return (
        str(row.get("creature") or "").casefold(),
        str(row.get("creatureObjectPath") or ""),
        int(attack_index)
        if isinstance(attack_index, int) and not isinstance(attack_index, bool)
        else 0,
        str(row.get("attackName") or "").casefold(),
    )

def _metric_value(row: dict[str, Any], metric: str) -> float | None:
    value = row.get(metric)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        return None
    return float(value)


def _enrich_v2_metrics(row: dict[str, Any], *, metric: str) -> None:
    static_total = _estimated_yield(row)
    row["staticCompleteNodeTargetYield"] = static_total
    # Compatibility is intentionally a value alias, never a second formula.
    row["estimatedYieldPerNode"] = static_total
    hit_count = row.get("estimatedHitsToDepleteNode")
    interval = row.get("attackInterval")
    cycle_seconds = (
        float(hit_count) * float(interval)
        if isinstance(hit_count, (int, float))
        and not isinstance(hit_count, bool)
        and float(hit_count) > 0
        and isinstance(interval, (int, float))
        and not isinstance(interval, bool)
        and float(interval) > 0
        else None
    )
    row["staticAttackCycleSecondsToDepleteNode"] = cycle_seconds
    row["staticYieldPerAttackCycleSecond"] = (
        static_total / cycle_seconds
        if static_total is not None and cycle_seconds is not None
        else None
    )
    row["staticFirstHitTiming"] = "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE"
    row.setdefault("observedYieldPerNode", None)
    row.setdefault("observedYieldPerSecond", None)
    row.setdefault("runtimeStatus", "NOT_MEASURED")
    row["scoreBasis"] = METRIC_CONTRACTS[metric]["scoreBasis"]
    breakdown = dict(row.get("scoreBreakdown") or {})
    breakdown["metric"] = metric
    row["scoreBreakdown"] = breakdown

def _semantic_property_value(prop: dict[str, Any]) -> Any:
    type_name = str(prop.get("type") or prop.get("type_name") or "")
    if type_name == "ObjectProperty":
        resolved = prop.get("object")
        if isinstance(resolved, str) and resolved:
            return resolved
    return prop.get("value")


def extract_creature_identity(
    properties: Iterable[dict[str, Any]],
    *,
    fallback_name: str,
) -> dict[str, str]:
    """Recover a stable species identity without treating a filename as confirmed UI text."""

    rows = {
        str(row.get("name") or ""): row
        for row in properties
        if isinstance(row, dict) and row.get("name")
    }
    tag_value = _semantic_property_value(rows["DinoNameTag"]) if "DinoNameTag" in rows else None
    name_value = (
        _semantic_property_value(rows["DescriptiveName"])
        if "DescriptiveName" in rows
        else None
    )
    tag = str(tag_value or "").strip()
    name = str(name_value or "").strip()
    fallback = str(fallback_name or "UnknownCreature").strip()
    species_source = tag or name or fallback
    species_key = " ".join(species_source.casefold().split())
    return {
        "name": name or tag or fallback,
        "dinoNameTag": tag,
        "speciesKey": species_key,
        "identityStatus": "CONFIRMED" if tag or name else "FILENAME_FALLBACK",
    }


def prepare_attack_for_usage_scope(
    attack: dict[str, Any],
    *,
    usage_scope: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply explicit scope blocks and preserve dynamic Blueprint gates as estimates.

    ``skipTamed``, ``onlyOnWildDinos``, and ``preventWithRider`` are recovered
    negative facts, so they exclude an attack from the tamed-ridden scope.  The
    two ``useBlueprint*`` flags say that native/static defaults are not the whole
    runtime answer; they do *not* prove that the attack is unavailable.  They are
    forwarded as explicit conditional gaps.  The yield evaluator can then fail
    closed when a runtime hook (notably output-damage adjustment) could change
    the complete-node result.
    """

    if usage_scope != TAMED_RIDDEN:
        raise ValueError(f"Unsupported harvest usage scope: {usage_scope}")
    if attack.get("skipTamed") is True:
        return None, "ATTACK_SKIPPED_WHEN_TAMED"
    if attack.get("onlyOnWildDinos") is True:
        return None, "ATTACK_ONLY_ON_WILD_DINOS"
    if attack.get("preventWithRider") is True:
        return None, "ATTACK_PREVENTED_WITH_RIDER"
    prepared = dict(attack)
    conditional_reasons: list[str] = []
    if attack.get("useBlueprintCanRiderAttack") is True:
        conditional_reasons.append("BLUEPRINT_RIDER_ELIGIBILITY_NOT_RECOVERED")
    if attack.get("useBlueprintAdjustOutputDamage") is True:
        conditional_reasons.append("BLUEPRINT_ADJUST_OUTPUT_DAMAGE_NOT_RECOVERED")
    base_interval = attack.get("attackInterval")
    rider_interval = attack.get("riderAttackInterval")
    prepared["baseAttackInterval"] = base_interval
    if isinstance(rider_interval, (int, float)) and float(rider_interval) > 0:
        prepared["attackInterval"] = float(rider_interval)
        prepared["attackIntervalSource"] = "RIDER_ATTACK_INTERVAL"
    else:
        prepared["attackIntervalSource"] = "GENERAL_ATTACK_INTERVAL"
    prepared["usageScope"] = usage_scope
    prepared["usageEligibilityStatus"] = (
        "CONDITIONAL" if conditional_reasons else "ELIGIBLE_BY_EXPLICIT_FLAGS"
    )
    prepared["usageConditionReasonCodes"] = conditional_reasons
    prepared["usageEstimateBasis"] = (
        "STATIC_ATTACK_FACTS_WITH_BLUEPRINT_RUNTIME_RESULT_NOT_RECOVERED"
        if conditional_reasons
        else "STATIC_ATTACK_FACTS"
    )
    return prepared, None


def find_node_and_resource(
    catalog: dict[str, Any],
    node_id: str,
    node_resource_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes = catalog.get("nodes")
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or str(node.get("id") or "") != node_id:
            continue
        resources = node.get("resources", {}).get("items", [])
        for resource in resources if isinstance(resources, list) else []:
            if (
                isinstance(resource, dict)
                and str(resource.get("nodeResourceId") or "") == node_resource_id
            ):
                return node, resource
        raise KeyError("NODE_RESOURCE_NOT_FOUND")
    raise KeyError("RESOURCE_NODE_NOT_FOUND")


def _override_map(rows: object) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        source = str(row.get("sourceDamageType") or "")
        resource = str(row.get("resource") or "")
        replacement = str(row.get("replacementDamageType") or "")
        if source and resource and replacement:
            result[(source, resource)] = replacement
    return result
