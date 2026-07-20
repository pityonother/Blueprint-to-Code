"""Compact, evidence-aware ARK harvesting comparison helpers.

The module intentionally does not claim to reproduce runtime resource yield.
It builds a bounded comparison index from values recovered from creature attack
structs and harvest-component structs, while keeping incompatible and missing
facts distinct from numeric zero.
"""

from __future__ import annotations

from typing import Any, Iterable


CONFIRMED = "CONFIRMED"
NOT_RECOVERED = "NOT_RECOVERED"

_INFORMATIONAL_QUANTITY_GAPS = {
    "DAMAGE_TYPE_MIN_QUANTITY_OVERRIDE_NOT_RECOVERED",
    "DAMAGE_TYPE_MAX_QUANTITY_OVERRIDE_NOT_RECOVERED",
}


def _semantic_property_value(prop: dict[str, Any]) -> Any:
    type_name = str(prop.get("type") or prop.get("type_name") or "")
    if type_name == "ObjectProperty":
        resolved = prop.get("object")
        if isinstance(resolved, str) and resolved:
            return resolved
    if type_name == "ArrayProperty":
        objects = prop.get("objects")
        if isinstance(objects, list) and objects:
            return objects
    return prop.get("value")


def _property_map(properties: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name") or "")
        if name:
            rows[name] = prop
    return rows


def extract_creature_attacks(properties: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project AttackInfos struct elements into compact per-attack facts."""

    attacks: list[dict[str, Any]] = []
    for container in properties:
        if not isinstance(container, dict) or str(container.get("name") or "") != "AttackInfos":
            continue
        parse = container.get("array_parse")
        if not isinstance(parse, dict) or parse.get("parsed") is not True:
            continue
        elements = parse.get("elements")
        if not isinstance(elements, list):
            continue
        for ordinal, element in enumerate(elements):
            if not isinstance(element, dict):
                continue
            nested = element.get("properties")
            fields = _property_map(nested if isinstance(nested, list) else [])

            def value(name: str) -> Any:
                return _semantic_property_value(fields[name]) if name in fields else None

            attack_name = value("AttackName")
            damage_type = value("MeleeDamageType")
            base_damage = value("MeleeDamageAmount")
            attack_interval = value("AttackInterval")
            missing: list[str] = []
            if not isinstance(attack_name, str) or not attack_name:
                missing.append("AttackName")
            if not isinstance(damage_type, str) or not damage_type:
                missing.append("MeleeDamageType")
            if not isinstance(base_damage, (int, float)):
                missing.append("MeleeDamageAmount")
            if not isinstance(attack_interval, (int, float)):
                missing.append("AttackInterval")
            attacks.append(
                {
                    "attackIndex": int(element.get("index", ordinal)),
                    "attackName": attack_name,
                    "damageType": damage_type,
                    "baseDamage": base_damage,
                    "attackInterval": attack_interval,
                    "meleeSwingRadius": value("MeleeSwingRadius"),
                    "basicAttack": value("bBasicAttack"),
                    "animations": value("AttackAnimations") or [],
                    "rawOffsets": element.get("raw_offsets") or {},
                    "valueStatus": CONFIRMED if not missing else NOT_RECOVERED,
                    "gaps": missing,
                }
            )
    return attacks


def _decoded_array(prop: dict[str, Any] | None) -> list[Any] | None:
    if not isinstance(prop, dict):
        return None
    parse = prop.get("array_parse")
    if not isinstance(parse, dict) or parse.get("parsed") is not True:
        return None
    value = _semantic_property_value(prop)
    return list(value) if isinstance(value, list) else None


def _aligned_override_map(
    damage_types: list[Any] | None,
    values: list[Any] | None,
    *,
    gap_code: str,
    gaps: list[str],
) -> dict[str, Any]:
    if damage_types == [] and values is None:
        return {}
    if damage_types is None or values is None:
        gaps.append(gap_code)
        return {}
    if len(damage_types) != len(values):
        gaps.append(gap_code)
        return {}
    return {
        str(damage_type): value
        for damage_type, value in zip(damage_types, values)
        if isinstance(damage_type, str) and damage_type
    }


def extract_harvest_component(
    properties: Iterable[dict[str, Any]],
    *,
    component: str,
    object_path: str,
) -> dict[str, Any]:
    """Project HarvestResourceEntries and HarvestDamageTypeEntries."""

    top = _property_map(properties)
    gaps: list[str] = []
    resource_entries: list[dict[str, Any]] = []
    damage_entries: list[dict[str, Any]] = []
    ranking_gaps: list[str] = []
    informational_gaps: list[str] = []

    resource_container = top.get("HarvestResourceEntries")
    resource_parse = resource_container.get("array_parse") if isinstance(resource_container, dict) else None
    if not isinstance(resource_parse, dict) or resource_parse.get("parsed") is not True:
        gaps.append("HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED")
        ranking_gaps.append("HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED")
    else:
        elements = resource_parse.get("elements")
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict):
                continue
            nested = element.get("properties")
            fields = _property_map(nested if isinstance(nested, list) else [])
            resource = _semantic_property_value(fields["ResourceItem"]) if "ResourceItem" in fields else None
            damage_types = _decoded_array(fields.get("DamageTypeEntryValuesOverrides"))
            entry_gaps: list[str] = []
            weight_overrides = _aligned_override_map(
                damage_types,
                _decoded_array(fields.get("DamageTypeEntryWeightOverrides")),
                gap_code="DAMAGE_TYPE_WEIGHT_OVERRIDE_NOT_RECOVERED",
                gaps=entry_gaps,
            )
            min_overrides = _aligned_override_map(
                damage_types,
                _decoded_array(fields.get("DamageTypeEntryMinQuantityOverrides")),
                gap_code="DAMAGE_TYPE_MIN_QUANTITY_OVERRIDE_NOT_RECOVERED",
                gaps=entry_gaps,
            )
            max_overrides = _aligned_override_map(
                damage_types,
                _decoded_array(fields.get("DamageTypeEntryMaxQuantityOverrides")),
                gap_code="DAMAGE_TYPE_MAX_QUANTITY_OVERRIDE_NOT_RECOVERED",
                gaps=entry_gaps,
            )
            if not isinstance(resource, str) or not resource:
                entry_gaps.append("RESOURCE_ITEM_NOT_RECOVERED")
            entry_ranking_gaps = [
                gap
                for gap in entry_gaps
                if gap not in _INFORMATIONAL_QUANTITY_GAPS
            ]
            entry_informational_gaps = [
                gap
                for gap in entry_gaps
                if gap in _INFORMATIONAL_QUANTITY_GAPS
            ]
            resource_entries.append(
                {
                    "entryIndex": element.get("index"),
                    "resource": resource,
                    "entryWeight": _semantic_property_value(fields["EntryWeight"])
                    if "EntryWeight" in fields
                    else None,
                    "effectivenessQuantityMultiplier": _semantic_property_value(
                        fields["EffectivenessQuantityMultiplier"]
                    )
                    if "EffectivenessQuantityMultiplier" in fields
                    else None,
                    "overrideQuantityMin": _semantic_property_value(fields["OverrideQuantityMin"])
                    if "OverrideQuantityMin" in fields
                    else None,
                    "overrideQuantityMax": _semantic_property_value(fields["OverrideQuantityMax"])
                    if "OverrideQuantityMax" in fields
                    else None,
                    "weightOverrides": weight_overrides,
                    "minQuantityOverrides": min_overrides,
                    "maxQuantityOverrides": max_overrides,
                    "damageTypeEntryValues": damage_types,
                    "gaps": sorted(set(entry_gaps)),
                    "rankingGaps": sorted(set(entry_ranking_gaps)),
                    "informationalGaps": sorted(set(entry_informational_gaps)),
                    "rawOffsets": element.get("raw_offsets") or {},
                }
            )
            gaps.extend(entry_gaps)
            informational_gaps.extend(entry_informational_gaps)

    damage_container = top.get("HarvestDamageTypeEntries")
    damage_parse = damage_container.get("array_parse") if isinstance(damage_container, dict) else None
    if not isinstance(damage_parse, dict) or damage_parse.get("parsed") is not True:
        gaps.append("HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED")
        ranking_gaps.append("HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED")
    else:
        elements = damage_parse.get("elements")
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict):
                continue
            nested = element.get("properties")
            fields = _property_map(nested if isinstance(nested, list) else [])
            damage_type_parent = (
                _semantic_property_value(fields["DamageTypeParent"])
                if "DamageTypeParent" in fields
                else None
            )
            damage_multiplier = (
                _semantic_property_value(fields["DamageMultiplier"])
                if "DamageMultiplier" in fields
                else None
            )
            harvest_quantity_multiplier = (
                _semantic_property_value(fields["HarvestQuantityMultiplier"])
                if "HarvestQuantityMultiplier" in fields
                else None
            )
            entry_gaps: list[str] = []
            if not isinstance(damage_type_parent, str) or not damage_type_parent:
                entry_gaps.append("DAMAGE_TYPE_PARENT_NOT_RECOVERED")
            if not isinstance(damage_multiplier, (int, float)):
                entry_gaps.append("DAMAGE_MULTIPLIER_NOT_RECOVERED")
            if not isinstance(harvest_quantity_multiplier, (int, float)):
                entry_gaps.append("HARVEST_QUANTITY_MULTIPLIER_NOT_RECOVERED")
            damage_entries.append(
                {
                    "entryIndex": element.get("index"),
                    "damageTypeParent": damage_type_parent,
                    "damageMultiplier": damage_multiplier,
                    "harvestQuantityMultiplier": harvest_quantity_multiplier,
                    "damageHarvestAdditionalEffectiveness": _semantic_property_value(
                        fields["DamageHarvestAdditionalEffectiveness"]
                    )
                    if "DamageHarvestAdditionalEffectiveness" in fields
                    else None,
                    "gaps": sorted(set(entry_gaps)),
                    "rawOffsets": element.get("raw_offsets") or {},
                }
            )
            gaps.extend(entry_gaps)

    return {
        "component": component,
        "objectPath": object_path,
        "resourceEntries": resource_entries,
        "damageEntries": damage_entries,
        "maxHarvestHealth": _semantic_property_value(top["MaxHarvestHealth"])
        if "MaxHarvestHealth" in top
        else None,
        "harvestHealthGiveResourceInterval": _semantic_property_value(
            top["HarvestHealthGiveResourceInterval"]
        )
        if "HarvestHealthGiveResourceInterval" in top
        else None,
        "gaps": sorted(set(gaps)),
        "rankingGaps": sorted(set(ranking_gaps)),
        "informationalGaps": sorted(set(informational_gaps)),
    }


def extract_resource_damage_overrides(
    properties: Iterable[dict[str, Any]],
    damage_type: str,
) -> dict[str, Any]:
    top = _property_map(properties)
    items = _decoded_array(top.get("OverrideDamageForResourceHarvestingItems"))
    replacements = _decoded_array(top.get("OverrideDamageForResourceHarvestingDamageTypes"))
    gaps: list[str] = []
    if (
        "OverrideDamageForResourceHarvestingItems" not in top
        and "OverrideDamageForResourceHarvestingDamageTypes" not in top
    ):
        return {"damageType": damage_type, "overrides": {}, "gaps": []}
    if items is None or replacements is None:
        gaps.append("RESOURCE_DAMAGE_OVERRIDE_NOT_RECOVERED")
        return {"damageType": damage_type, "overrides": {}, "gaps": gaps}
    if len(items) != len(replacements):
        gaps.append("RESOURCE_DAMAGE_OVERRIDE_LENGTH_MISMATCH")
        return {"damageType": damage_type, "overrides": {}, "gaps": gaps}
    overrides = {
        (damage_type, str(resource)): str(replacement)
        for resource, replacement in zip(items, replacements)
        if isinstance(resource, str)
        and resource
        and isinstance(replacement, str)
        and replacement
    }
    return {"damageType": damage_type, "overrides": overrides, "gaps": gaps}


def damage_type_chain(damage_type: str, parents: dict[str, str]) -> list[str]:
    chain: list[str] = []
    current = str(damage_type or "")
    seen: set[str] = set()
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = str(parents.get(current) or "")
    return chain


def _nearest_override(
    overrides: dict[str, Any],
    chain: list[str],
    fallback: Any,
) -> tuple[Any, str | None]:
    for damage_type in chain:
        if damage_type in overrides:
            return overrides[damage_type], damage_type
    return fallback, None


def _unranked_row(
    base: dict[str, Any],
    reason_code: str,
    missing: Iterable[str] = (),
    *,
    scope: str = "unknown",
    missing_by_scope: dict[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    scoped: dict[str, list[str]] = {}
    if missing_by_scope:
        for scope_name, values in missing_by_scope.items():
            normalized = sorted({str(item) for item in values if str(item)})
            if normalized:
                scoped[str(scope_name)] = normalized
    direct = sorted({str(item) for item in missing if str(item)})
    if direct:
        scoped[scope] = sorted(set(scoped.get(scope, [])) | set(direct))
    flattened = sorted({item for values in scoped.values() for item in values})
    return {
        **base,
        "rankingStatus": "UNRANKED",
        "reasonCode": reason_code,
        "missingFacts": flattened,
        "missingFactsByScope": scoped,
        "engineComparisonIndex": None,
        "harvestPressurePerSecond": None,
        "observedYieldPerSecond": None,
        "scoreBasis": "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD",
    }


def evaluate_attack_resource(
    *,
    creature: str,
    creature_object_path: str,
    attack: dict[str, Any],
    component: dict[str, Any],
    resource: str,
    damage_type_parents: dict[str, str],
    resource_damage_overrides: dict[tuple[str, str], str],
    damage_type_gaps: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Evaluate one attack against one resource-bearing harvest component.

    ``engineComparisonIndex`` is intentionally dimensionless. It combines the
    recovered attack cadence, damage/quantity multipliers, and the target's
    normalized selection weight. It is not resources per hit or per second.
    """

    source_damage_type = attack.get("damageType")
    base: dict[str, Any] = {
        "creature": creature,
        "creatureObjectPath": creature_object_path,
        "attackIndex": attack.get("attackIndex"),
        "attackName": attack.get("attackName"),
        "sourceDamageType": source_damage_type,
        "component": component.get("component"),
        "componentObjectPath": component.get("objectPath"),
        "resource": resource,
        "observedYieldPerSecond": None,
    }
    component_warnings = sorted(set(component.get("informationalGaps") or []))
    if component_warnings:
        base["warnings"] = component_warnings
        base["warningsByScope"] = {"component": component_warnings}
    missing = list(attack.get("gaps") or [])
    if not isinstance(source_damage_type, str) or not source_damage_type:
        missing.append("MeleeDamageType")
    if not isinstance(attack.get("baseDamage"), (int, float)):
        missing.append("MeleeDamageAmount")
    if not isinstance(attack.get("attackInterval"), (int, float)):
        missing.append("AttackInterval")
    if missing:
        return _unranked_row(
            base,
            "REQUIRED_ATTACK_FACT_NOT_RECOVERED",
            missing,
            scope="attack",
        )

    component_ranking_gaps = component.get("rankingGaps")
    if not isinstance(component_ranking_gaps, list):
        component_ranking_gaps = [
            str(gap)
            for gap in component.get("gaps") or []
            if str(gap).startswith("HARVEST_")
        ]
    container_gaps = [
        str(gap)
        for gap in component_ranking_gaps
        if str(gap)
        in {
            "HARVEST_RESOURCE_ENTRIES_NOT_RECOVERED",
            "HARVEST_DAMAGE_ENTRIES_NOT_RECOVERED",
        }
    ]
    if container_gaps:
        return _unranked_row(
            base,
            "REQUIRED_COMPONENT_FACT_NOT_RECOVERED",
            container_gaps,
            scope="component",
        )

    attack_interval_value = float(attack["attackInterval"])
    base_damage_value = float(attack["baseDamage"])
    base.update(
        {
            "baseDamage": base_damage_value,
            "attackInterval": attack_interval_value,
            "potentialAttackRate": (
                base_damage_value / attack_interval_value if attack_interval_value > 0 else None
            ),
        }
    )

    effective_damage_type = resource_damage_overrides.get(
        (source_damage_type, resource), source_damage_type
    )
    chain = damage_type_chain(effective_damage_type, damage_type_parents)
    base.update(
        {
            "effectiveDamageType": effective_damage_type,
            "damageOverrideApplied": effective_damage_type != source_damage_type,
            "damageTypeChain": chain,
        }
    )
    damage_type_gaps = damage_type_gaps or {}
    direct_damage_type_gaps = [
        gap
        for damage_type in {source_damage_type, effective_damage_type}
        for gap in damage_type_gaps.get(str(damage_type), [])
    ]
    if direct_damage_type_gaps:
        return _unranked_row(
            base,
            "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED",
            direct_damage_type_gaps,
            scope="damageType",
        )

    resource_entries = component.get("resourceEntries")
    if not isinstance(resource_entries, list):
        return _unranked_row(
            base,
            "REQUIRED_COMPONENT_FACT_NOT_RECOVERED",
            ["HarvestResourceEntries"],
            scope="component",
        )
    target_entry = next(
        (
            entry
            for entry in resource_entries
            if isinstance(entry, dict) and str(entry.get("resource") or "") == resource
        ),
        None,
    )
    if not isinstance(target_entry, dict):
        resource_identity_gaps = [
            "ResourceItem"
            for entry in resource_entries
            if isinstance(entry, dict)
            and "RESOURCE_ITEM_NOT_RECOVERED" in (entry.get("rankingGaps") or entry.get("gaps") or [])
        ]
        if resource_identity_gaps:
            return _unranked_row(
                base,
                "TARGET_RESOURCE_FACT_NOT_RECOVERED",
                resource_identity_gaps,
                scope="target",
            )
        return {
            **base,
            "rankingStatus": "INCOMPATIBLE",
            "reasonCode": "RESOURCE_NOT_IN_COMPONENT",
            "missingFacts": [],
            "engineComparisonIndex": None,
            "harvestPressurePerSecond": None,
            "observedYieldPerSecond": None,
            "scoreBasis": "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD",
        }
    target_warnings = sorted(
        set(target_entry.get("informationalGaps") or [])
        | (set(target_entry.get("gaps") or []) & _INFORMATIONAL_QUANTITY_GAPS)
    )
    if target_warnings:
        base.setdefault("warningsByScope", {})["target"] = target_warnings
        base["warnings"] = sorted(
            set(base.get("warnings") or []) | set(target_warnings)
        )

    damage_entries = component.get("damageEntries")
    if not isinstance(damage_entries, list):
        return _unranked_row(
            base,
            "REQUIRED_COMPONENT_FACT_NOT_RECOVERED",
            ["HarvestDamageTypeEntries"],
            scope="component",
        )
    damage_entry: dict[str, Any] | None = None
    damage_match: str | None = None
    unresolved_chain_gaps: list[str] = []
    for candidate in chain:
        candidate_entry = next(
            (
                entry
                for entry in damage_entries
                if isinstance(entry, dict) and str(entry.get("damageTypeParent") or "") == candidate
            ),
            None,
        )
        candidate_gaps = damage_type_gaps.get(candidate, [])
        if candidate_entry is not None:
            if unresolved_chain_gaps or candidate_gaps:
                return _unranked_row(
                    base,
                    "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED",
                    [*unresolved_chain_gaps, *candidate_gaps],
                    scope="damageType",
                )
            damage_entry = candidate_entry
            damage_match = candidate
            break
        unresolved_chain_gaps.extend(candidate_gaps)
    if damage_entry is None:
        unresolved_damage_entries = [
            gap
            for entry in damage_entries
            if isinstance(entry, dict)
            for gap in entry.get("gaps") or []
            if gap == "DAMAGE_TYPE_PARENT_NOT_RECOVERED"
        ]
        if unresolved_chain_gaps:
            return _unranked_row(
                base,
                "REQUIRED_DAMAGE_TYPE_FACT_NOT_RECOVERED",
                unresolved_chain_gaps,
                scope="damageType",
            )
        if unresolved_damage_entries:
            return _unranked_row(
                base,
                "REQUIRED_COMPONENT_FACT_NOT_RECOVERED",
                unresolved_damage_entries,
                scope="component",
            )
        return {
            **base,
            "rankingStatus": "INCOMPATIBLE",
            "reasonCode": "DAMAGE_TYPE_NOT_ACCEPTED",
            "missingFacts": [],
            "damageTypeMatch": None,
            "engineComparisonIndex": None,
            "harvestPressurePerSecond": None,
            "observedYieldPerSecond": None,
            "scoreBasis": "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD",
        }

    weighted_entries: list[tuple[dict[str, Any], float | None, str | None]] = []
    for entry in resource_entries:
        if not isinstance(entry, dict):
            continue
        entry_gaps = set(entry.get("rankingGaps") or entry.get("gaps") or [])
        override_types = entry.get("damageTypeEntryValues")
        weight_override_unknown = (
            "DAMAGE_TYPE_WEIGHT_OVERRIDE_NOT_RECOVERED" in entry_gaps
            and (
                not isinstance(override_types, list)
                or any(candidate in override_types for candidate in chain)
            )
        )
        if weight_override_unknown:
            weight = None
            matched = None
        else:
            overrides = entry.get("weightOverrides")
            value, matched = _nearest_override(
                overrides if isinstance(overrides, dict) else {},
                chain,
                entry.get("entryWeight"),
            )
            weight = float(value) if isinstance(value, (int, float)) else None
        weighted_entries.append((entry, weight, matched))
    target_weight_row = next(
        (row for row in weighted_entries if str(row[0].get("resource") or "") == resource),
        None,
    )
    if target_weight_row is None or target_weight_row[1] is None:
        return _unranked_row(
            base,
            "RESOURCE_WEIGHT_NOT_RECOVERED",
            ["EntryWeight"],
            scope="target",
        )
    resource_weight = float(target_weight_row[1])
    base.update(
        {
            "damageTypeMatch": damage_match,
            "resourceWeight": resource_weight,
            "resourceWeightMatch": target_weight_row[2],
        }
    )
    if resource_weight <= 0.0:
        return {
            **base,
            "rankingStatus": "INCOMPATIBLE",
            "reasonCode": "ZERO_RESOURCE_WEIGHT",
            "missingFacts": [],
            "resourceWeightShare": 0.0,
            "engineComparisonIndex": None,
            "harvestPressurePerSecond": None,
            "observedYieldPerSecond": None,
            "scoreBasis": "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD",
        }
    unknown_competing_weights = [
        f"EntryWeight:{entry.get('resource') or '#' + str(entry.get('entryIndex'))}"
        for entry, weight, _match in weighted_entries
        if entry is not target_entry and weight is None
    ]
    if unknown_competing_weights:
        return _unranked_row(
            base,
            "RESOURCE_WEIGHT_NORMALIZATION_NOT_RECOVERED",
            unknown_competing_weights,
            scope="component",
        )
    total_positive_weight = sum(
        max(0.0, float(weight))
        for _entry, weight, _match in weighted_entries
        if weight is not None
    )
    base["totalPositiveResourceWeight"] = total_positive_weight
    if total_positive_weight <= 0.0:
        return _unranked_row(
            base,
            "RESOURCE_WEIGHT_NORMALIZATION_NOT_RECOVERED",
            ["EntryWeight"],
            scope="component",
        )

    damage_multiplier = damage_entry.get("damageMultiplier")
    quantity_multiplier = damage_entry.get("harvestQuantityMultiplier")
    component_coefficients: list[str] = []
    attack_coefficients: list[str] = []
    if not isinstance(damage_multiplier, (int, float)):
        component_coefficients.append("DamageMultiplier")
    if not isinstance(quantity_multiplier, (int, float)):
        component_coefficients.append("HarvestQuantityMultiplier")
    attack_interval = attack_interval_value
    if attack_interval <= 0:
        attack_coefficients.append("AttackInterval>0")
    if component_coefficients or attack_coefficients:
        return _unranked_row(
            base,
            "REQUIRED_COEFFICIENT_NOT_RECOVERED",
            missing_by_scope={
                "attack": attack_coefficients,
                "component": component_coefficients,
            },
        )

    min_quantity, min_match = _nearest_override(
        target_entry.get("minQuantityOverrides")
        if isinstance(target_entry.get("minQuantityOverrides"), dict)
        else {},
        chain,
        target_entry.get("overrideQuantityMin"),
    )
    max_quantity, max_match = _nearest_override(
        target_entry.get("maxQuantityOverrides")
        if isinstance(target_entry.get("maxQuantityOverrides"), dict)
        else {},
        chain,
        target_entry.get("overrideQuantityMax"),
    )
    weight_share = resource_weight / total_positive_weight
    pressure = (
        base_damage_value
        / attack_interval
        * float(damage_multiplier)
        * float(quantity_multiplier)
    )
    return {
        **base,
        "rankingStatus": "RANKED",
        "reasonCode": "ENGINE_COEFFICIENTS_RECOVERED",
        "missingFacts": [],
        "baseDamage": base_damage_value,
        "attackInterval": attack_interval,
        "damageMultiplier": float(damage_multiplier),
        "harvestQuantityMultiplier": float(quantity_multiplier),
        "resourceWeightShare": weight_share,
        "overrideQuantityMin": min_quantity,
        "overrideQuantityMax": max_quantity,
        "quantityOverrideMatch": min_match or max_match,
        "maxHarvestHealth": component.get("maxHarvestHealth"),
        "harvestHealthGiveResourceInterval": component.get("harvestHealthGiveResourceInterval"),
        "harvestPressurePerSecond": pressure,
        "engineComparisonIndex": pressure * weight_share,
        "observedYieldPerSecond": None,
        "scoreBasis": "INFERRED_ENGINE_COEFFICIENT_INDEX_NOT_RESOURCE_YIELD",
    }


def rank_harvest_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    status_order = {"RANKED": 0, "UNRANKED": 1, "INCOMPATIBLE": 2}

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        status = str(row.get("rankingStatus") or "UNRANKED")
        score = row.get("engineComparisonIndex")
        fallback = row.get("potentialAttackRate")
        numeric_score = (
            float(score)
            if isinstance(score, (int, float))
            else (float(fallback) if isinstance(fallback, (int, float)) else float("-inf"))
        )
        return (
            status_order.get(status, 9),
            -numeric_score,
            str(row.get("creature") or ""),
            str(row.get("attackName") or ""),
            str(row.get("component") or ""),
        )

    return sorted((dict(row) for row in rows), key=key)
