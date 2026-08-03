"""Compact all-creature facts and lazy per-node harvesting evaluation."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from typing import Any, Iterable, Mapping

from .harvest_ranking import (
    YIELD_MODEL_VERSION,
    YIELD_SCORE_BASIS,
    evaluate_attack_resource,
)
from .harvest_runtime_observations import (
    MINIMUM_CONFIRMED_TRIALS,
    RUNTIME_STATUS_OBSERVED_CONFIRMED,
    RUNTIME_STATUS_OBSERVED_PRELIMINARY,
    HarvestRuntimeProfileError,
)
from .resource_nodes import canonical_package_path


EVALUATION_CATALOG_SCHEMA = "ark-harvest-evaluation-catalog/v2"
RANKING_RESULT_SCHEMA = "blueprint-to-code.harvest-ranking-result/v4"
TAMED_RIDDEN = "TAMED_RIDDEN"
HARVEST_RANKING_CONTRACT_VERSION = "harvest-ranking-contract/v2"
HARVEST_RANKING_POLICY_VERSION = (
    "harvest-ranking-policy/v2-confirmed-canonical-relative-specialty"
)

POLICY_CONFIRMED = "confirmed"
POLICY_INCLUDE_CONDITIONAL = "includeConditional"
VARIANT_CANONICAL = "CANONICAL_VARIANT"
VARIANT_ALL = "ALL_VARIANTS"
VARIANT_BEST_DISCOVERED_EXPLORATORY = "BEST_DISCOVERED_VARIANT_EXPLORATORY"
METRIC_STATIC_TOTAL = "staticCompleteNodeTargetYield"
METRIC_STATIC_CYCLE_SPEED = "staticYieldPerAttackCycleSecond"
METRIC_OBSERVED_PER_NODE = "observedYieldPerNode"
METRIC_OBSERVED_PER_SECOND = "observedYieldPerSecond"
AVAILABILITY_GLOBAL_TRANSFER_ALLOWED = "GLOBAL_TRANSFER_ALLOWED"

METRIC_CONTRACTS: dict[str, dict[str, object]] = {
    METRIC_STATIC_TOTAL: {
        "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
        "unit": "target_resource_units/node",
        "runtime": False,
    },
    METRIC_STATIC_CYCLE_SPEED: {
        "scoreBasis": "STATIC_TARGET_RESOURCE_UNITS_PER_ATTACK_CYCLE_SECOND",
        "unit": "target_resource_units/attack_cycle_second",
        "runtime": False,
    },
    METRIC_OBSERVED_PER_NODE: {
        "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_COMPLETE_NODE",
        "unit": "target_resource_units/node",
        "runtime": True,
    },
    METRIC_OBSERVED_PER_SECOND: {
        "scoreBasis": "OBSERVED_TARGET_RESOURCE_UNITS_PER_SECOND",
        "unit": "target_resource_units/second",
        "runtime": True,
    },
}

_EVIDENCE_POLICIES = {POLICY_CONFIRMED, POLICY_INCLUDE_CONDITIONAL}
_VARIANT_POLICIES = {
    VARIANT_CANONICAL,
    VARIANT_ALL,
    VARIANT_BEST_DISCOVERED_EXPLORATORY,
}
_RANKING_METRICS = set(METRIC_CONTRACTS)
_AVAILABILITY_POLICIES = {AVAILABILITY_GLOBAL_TRANSFER_ALLOWED}

_VARIANT_BASE = "BASE"
_VARIANT_MAP = "MAP_VARIANT"
_VARIANT_MISSION = "MISSION"
_VARIANT_BOSS = "BOSS"
_VARIANT_EVENT = "EVENT"
_VARIANT_TEST = "TEST"
_VARIANT_UNKNOWN = "UNKNOWN_VARIANT"
_VARIANT_SELECTION_AUDIT_LIMIT = 10


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


def _canonical_variant_key(creature: dict[str, Any]) -> tuple[int, int, str]:
    """Return deterministic variant ordering without granting canonical status."""

    object_path = str(creature.get("objectPath") or "")
    normalized = object_path.casefold()
    if normalized.startswith("/game/primalearth/dinos/"):
        package_priority = 0
    elif normalized.startswith("/game/earth/dinos/"):
        package_priority = 1
    else:
        package_priority = 2
    return package_priority, len(object_path), normalized


def _variant_class(creature: dict[str, Any]) -> str:
    """Classify a variant from generic path markers, never a species allowlist."""

    object_path = str(creature.get("objectPath") or "").replace("\\", "/")
    normalized = object_path.casefold()
    segments = [segment for segment in normalized.split("/") if segment]
    if not segments:
        return _VARIANT_UNKNOWN

    def has_marker(*markers: str) -> bool:
        return any(
            marker in segment
            for marker in markers
            for segment in segments
        )

    if has_marker("test", "debug", "developer"):
        return _VARIANT_TEST
    if has_marker("mission"):
        return _VARIANT_MISSION
    if has_marker("boss"):
        return _VARIANT_BOSS
    if has_marker("event"):
        return _VARIANT_EVENT
    if has_marker("mapvariant", "map_variant") or "/maps/" in normalized:
        return _VARIANT_MAP
    if has_marker("variant", "special"):
        return _VARIANT_UNKNOWN
    return _VARIANT_BASE


def _normalized_variant_package(value: object) -> str:
    return canonical_package_path(value).casefold()


def _base_variant_ancestry(
    base_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ancestry roots and derived BASE candidates using explicit chains."""

    package_by_identity = {
        id(creature): _normalized_variant_package(creature.get("objectPath"))
        for creature in base_candidates
    }
    roots: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    for creature in base_candidates:
        own_package = package_by_identity[id(creature)]
        parent_chain = creature.get("parentChain")
        ancestor_packages = {
            _normalized_variant_package(value)
            for value in parent_chain
            if _normalized_variant_package(value)
        } if isinstance(parent_chain, list) else set()
        ancestor_packages.discard(own_package)
        other_base_packages = {
            package
            for other_identity, package in package_by_identity.items()
            if other_identity != id(creature) and package
        }
        if ancestor_packages & other_base_packages:
            derived.append(creature)
        else:
            roots.append(creature)
    return roots, derived


def _canonical_variant_audit(
    species_key: str,
    variants: list[dict[str, Any]],
) -> dict[str, Any]:
    classified = [
        (creature, _variant_class(creature))
        for creature in variants
    ]
    base_candidates = [
        creature
        for creature, variant_class in classified
        if variant_class == _VARIANT_BASE
    ]
    excluded_classes = sorted(
        {
            variant_class
            for _creature, variant_class in classified
            if variant_class != _VARIANT_BASE
        }
    )
    ancestry_roots: list[dict[str, Any]] = []
    derived_base_candidates: list[dict[str, Any]] = []
    if len(base_candidates) > 1:
        ancestry_roots, derived_base_candidates = _base_variant_ancestry(
            base_candidates
        )
        if derived_base_candidates:
            excluded_classes = sorted(
                {*excluded_classes, _VARIANT_UNKNOWN}
            )

    if len(base_candidates) == 1:
        canonical_path: str | None = str(
            base_candidates[0].get("objectPath") or ""
        )
        selection_reasons = ["UNIQUE_BASE_VARIANT"]
        ambiguous = False
        ambiguity_reasons: list[str] = []
    elif len(ancestry_roots) == 1:
        canonical_path = str(ancestry_roots[0].get("objectPath") or "")
        selection_reasons = ["UNIQUE_ANCESTRY_ROOT_BASE_VARIANT"]
        ambiguous = False
        ambiguity_reasons = []
    elif not base_candidates:
        canonical_path = None
        selection_reasons = []
        ambiguous = True
        ambiguity_reasons = [
            "CANONICAL_VARIANT_AMBIGUOUS",
            "NO_BASE_VARIANT_CANDIDATE",
        ]
    else:
        canonical_path = None
        selection_reasons = []
        ambiguous = True
        ambiguity_reasons = [
            "CANONICAL_VARIANT_AMBIGUOUS",
            "MULTIPLE_BASE_VARIANT_CANDIDATES",
            (
                "NO_ANCESTRY_ROOT_BASE_VARIANT"
                if not ancestry_roots
                else "MULTIPLE_ANCESTRY_ROOT_BASE_VARIANTS"
            ),
        ]
    return {
        "speciesKey": species_key,
        "canonicalObjectPath": canonical_path,
        "selectionReasons": selection_reasons,
        "excludedVariantClasses": excluded_classes,
        "ambiguous": ambiguous,
        "ambiguityReasons": ambiguity_reasons,
    }


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


def _runtime_profile_context(
    runtime_observations: Mapping[
        tuple[str, str, str, str, int], Mapping[str, Any]
    ]
    | None,
    *,
    requested_profile_id: str | None,
    runtime_metric: bool,
    validated_profiles_available: Iterable[str] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Select one comparable direct-call profile and report stable coverage."""

    observations = [
        row
        for row in (runtime_observations or {}).values()
        if isinstance(row, Mapping)
    ]
    available_profiles = sorted(
        {
            normalized
            for value in (
                validated_profiles_available
                if validated_profiles_available is not None
                else (
                    row.get("runtimeProfileId")
                    for row in observations
                    if row.get("synthetic") is False
                )
            )
            if (normalized := str(value or "").strip())
        }
    )
    selected_profile = (
        str(requested_profile_id).strip()
        if requested_profile_id is not None
        else None
    )
    if runtime_metric:
        if selected_profile is not None:
            if selected_profile not in available_profiles:
                raise HarvestRuntimeProfileError(
                    "HARVEST_RUNTIME_PROFILE_NOT_FOUND",
                    f"Requested runtimeProfileId {selected_profile!r} is not available.",
                )
        elif len(available_profiles) > 1:
            raise HarvestRuntimeProfileError(
                "HARVEST_RUNTIME_PROFILE_REQUIRED",
                "Multiple runtime profiles are available; select runtimeProfileId.",
            )
        elif available_profiles:
            selected_profile = available_profiles[0]

    synthetic_excluded = 0
    publishable_confirmed_rows = 0
    preliminary_rows = 0
    profile_mismatch_excluded = 0
    for observation in observations:
        if observation.get("synthetic") is not False:
            if observation.get("synthetic") is True:
                synthetic_excluded += 1
            continue
        observation_profile = str(
            observation.get("runtimeProfileId") or ""
        ).strip()
        if selected_profile is None:
            continue
        if observation_profile != selected_profile:
            profile_mismatch_excluded += 1
            continue
        runtime_status = str(observation.get("runtimeStatus") or "")
        trial_count = observation.get("trialCount")
        if (
            runtime_status == RUNTIME_STATUS_OBSERVED_CONFIRMED
            and isinstance(trial_count, int)
            and not isinstance(trial_count, bool)
            and trial_count >= MINIMUM_CONFIRMED_TRIALS
        ):
            publishable_confirmed_rows += 1
        elif (
            runtime_status == RUNTIME_STATUS_OBSERVED_PRELIMINARY
            and isinstance(trial_count, int)
            and not isinstance(trial_count, bool)
            and 0 < trial_count < MINIMUM_CONFIRMED_TRIALS
        ):
            preliminary_rows += 1

    return selected_profile, {
        "runtimeProfilesAvailable": available_profiles,
        "runtimeProfileSelected": selected_profile,
        "publishableConfirmedRows": publishable_confirmed_rows,
        "preliminaryRows": preliminary_rows,
        "syntheticExcluded": synthetic_excluded,
        "profileMismatchExcluded": profile_mismatch_excluded,
    }


def _eligible_runtime_observation(
    observation: object,
    *,
    runtime_profile_id: str | None,
    include_preliminary: bool,
) -> Mapping[str, Any] | None:
    """Reject injected runtime rows that bypass the validated file loader."""

    if (
        not isinstance(observation, Mapping)
        or observation.get("synthetic") is not False
        or runtime_profile_id is None
        or str(observation.get("runtimeProfileId") or "").strip()
        != runtime_profile_id
    ):
        return None
    trial_count = observation.get("trialCount")
    if (
        not isinstance(trial_count, int)
        or isinstance(trial_count, bool)
        or trial_count <= 0
    ):
        return None
    runtime_status = str(observation.get("runtimeStatus") or "")
    if runtime_status == RUNTIME_STATUS_OBSERVED_CONFIRMED:
        if trial_count < MINIMUM_CONFIRMED_TRIALS:
            return None
    elif runtime_status == RUNTIME_STATUS_OBSERVED_PRELIMINARY:
        if trial_count >= MINIMUM_CONFIRMED_TRIALS or not include_preliminary:
            return None
    else:
        return None
    for field in ("observedYieldPerNode", "observedYieldPerSecond"):
        value = observation.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            return None
    return observation


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


class HarvestEvaluationEngine:
    """Pre-index a compact catalog and evaluate only the selected component/resource."""

    def __init__(self, catalog: dict[str, Any]):
        if catalog.get("schema") != EVALUATION_CATALOG_SCHEMA:
            raise ValueError("Harvest evaluation catalog schema is invalid.")
        components = catalog.get("components")
        creatures = catalog.get("creatures")
        if not isinstance(components, list) or not isinstance(creatures, list):
            raise ValueError("Harvest evaluation catalog facts are incomplete.")
        self.catalog = catalog
        self.components = {
            canonical_package_path(component.get("objectPath")).casefold(): component
            for component in components
            if isinstance(component, dict)
            and canonical_package_path(component.get("objectPath"))
        }
        self.creatures = [row for row in creatures if isinstance(row, dict)]
        parents = catalog.get("damageTypeParents")
        self.damage_type_parents = dict(parents) if isinstance(parents, dict) else {}
        gaps = catalog.get("damageTypeGaps")
        self.damage_type_gaps = dict(gaps) if isinstance(gaps, dict) else {}
        self.resource_damage_overrides = _override_map(
            catalog.get("resourceDamageOverrides")
        )
        all_variants_by_species: dict[str, list[dict[str, Any]]] = {}
        for creature in self.creatures:
            species_key = str(
                creature.get("speciesKey")
                or creature.get("objectPath")
                or creature.get("name")
                or ""
            ).casefold()
            if species_key:
                all_variants_by_species.setdefault(species_key, []).append(creature)
        self._canonical_variant_audit_by_species = {
            species_key: _canonical_variant_audit(species_key, variants)
            for species_key, variants in sorted(all_variants_by_species.items())
        }
        self._canonical_variant_audits = [
            self._canonical_variant_audit_by_species[species_key]
            for species_key in sorted(self._canonical_variant_audit_by_species)
        ]
        self._canonical_ambiguous_variant_audits = [
            audit
            for audit in self._canonical_variant_audits
            if audit["ambiguous"] is True
        ]

    def canonical_variant_audits(self) -> list[dict[str, Any]]:
        """Return the complete offline audit before ranking-scope exclusions."""

        return deepcopy(self._canonical_variant_audits)

    def _rank_node_resource_v1(
        self,
        node_catalog: dict[str, Any],
        *,
        node_id: str,
        node_resource_id: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        node, resource = find_node_and_resource(
            node_catalog, node_id, node_resource_id
        )
        component_ref = node.get("harvestComponent")
        component_package = canonical_package_path(
            component_ref.get("packagePath") if isinstance(component_ref, dict) else ""
        )
        component = self.components.get(component_package.casefold())
        if not isinstance(component, dict):
            raise KeyError("HARVEST_COMPONENT_NOT_FOUND")

        usage_scope = str(
            self.catalog.get("methodology", {}).get("usageScope") or TAMED_RIDDEN
        )
        require_confirmed_rideability = (
            self.catalog.get("methodology", {}).get("rideabilityRequirement")
            == "B_ALLOW_RIDING_TRUE"
        )
        resource_class = str(resource.get("resource") or "")
        resource_entry_index = resource.get("entryIndex")
        considered_creatures = [
            creature
            for creature in self.creatures
            if str(creature.get("tameability", {}).get("status") or "UNKNOWN")
            != "PREVENTED"
            and (
                not require_confirmed_rideability
                or str(creature.get("rideability", {}).get("status") or "UNKNOWN")
                == "ALLOWED"
            )
        ]
        variant_counts = Counter(
            str(creature.get("speciesKey") or creature.get("objectPath") or "").casefold()
            for creature in considered_creatures
        )
        excluded = Counter()
        excluded_creatures = Counter()
        attacks_excluded_by_creature_scope = 0
        dispositions = Counter()
        conditional_evaluations = Counter()
        attacks_conditionally_evaluated = 0
        conditionally_ranked_attacks = 0
        best_by_species: dict[str, dict[str, Any]] = {}
        species_with_eligible_attack: set[str] = set()

        for creature in self.creatures:
            tameability = creature.get("tameability")
            if (
                isinstance(tameability, dict)
                and tameability.get("status") == "PREVENTED"
            ):
                reasons = tameability.get("reasonCodes")
                reason_values = (
                    [str(value) for value in reasons if value]
                    if isinstance(reasons, list)
                    else ["CREATURE_NOT_TAMEABLE"]
                )
                for reason in reason_values or ["CREATURE_NOT_TAMEABLE"]:
                    excluded_creatures[reason] += 1
                attacks_excluded_by_creature_scope += sum(
                    1 for attack in creature.get("attacks", []) if isinstance(attack, dict)
                )
                continue
            rideability = creature.get("rideability")
            rideability_status = str(
                rideability.get("status")
                if isinstance(rideability, dict)
                else "UNKNOWN"
            ) or "UNKNOWN"
            rideability_reason_codes = (
                [str(value) for value in rideability.get("reasonCodes", []) if value]
                if isinstance(rideability, dict)
                else ["RIDEABILITY_NOT_RECOVERED"]
            )
            if require_confirmed_rideability and rideability_status != "ALLOWED":
                fallback_reason = (
                    "RIDING_NOT_ALLOWED"
                    if rideability_status == "PREVENTED"
                    else "RIDEABILITY_NOT_RECOVERED"
                )
                for reason in rideability_reason_codes or [fallback_reason]:
                    excluded_creatures[reason] += 1
                attacks_excluded_by_creature_scope += sum(
                    1 for attack in creature.get("attacks", []) if isinstance(attack, dict)
                )
                continue
            species_key = str(
                creature.get("speciesKey") or creature.get("objectPath") or creature.get("name") or ""
            ).casefold()
            tameability_status = str(
                tameability.get("status")
                if isinstance(tameability, dict)
                else "UNKNOWN"
            ) or "UNKNOWN"
            tameability_reason_codes = (
                [str(value) for value in tameability.get("reasonCodes", []) if value]
                if isinstance(tameability, dict)
                else ["TAMEABILITY_NOT_RECOVERED"]
            )
            for attack in creature.get("attacks", []):
                if not isinstance(attack, dict):
                    continue
                prepared, exclusion_reason = prepare_attack_for_usage_scope(
                    attack,
                    usage_scope=usage_scope,
                )
                if prepared is None:
                    excluded[str(exclusion_reason or "ATTACK_SCOPE_UNKNOWN")] += 1
                    continue
                condition_reasons = [
                    str(value)
                    for value in prepared.get("usageConditionReasonCodes", [])
                    if value
                ]
                if condition_reasons:
                    attacks_conditionally_evaluated += 1
                    for reason in condition_reasons:
                        conditional_evaluations[reason] += 1
                species_with_eligible_attack.add(species_key)
                row = evaluate_attack_resource(
                    creature=str(creature.get("name") or "Unknown creature"),
                    creature_object_path=str(creature.get("objectPath") or ""),
                    attack=prepared,
                    component=component,
                    resource=resource_class,
                    resource_entry_index=(
                        int(resource_entry_index)
                        if isinstance(resource_entry_index, int)
                        and not isinstance(resource_entry_index, bool)
                        else None
                    ),
                    damage_type_parents=self.damage_type_parents,
                    resource_damage_overrides=self.resource_damage_overrides,
                    damage_type_gaps=self.damage_type_gaps,
                )
                disposition = str(row.get("rankingStatus") or "UNRANKED")
                dispositions[disposition] += 1
                score = _estimated_yield(row)
                if disposition != "RANKED" or score is None:
                    continue
                if condition_reasons:
                    conditionally_ranked_attacks += 1
                creature_evidence_confirmed = tameability_status == "ALLOWED" and (
                    not require_confirmed_rideability
                    or rideability_status == "ALLOWED"
                )
                evidence_confirmed = (
                    creature_evidence_confirmed and not condition_reasons
                )
                evidence_gaps = sorted(
                    set(
                        condition_reasons
                        + (
                            []
                            if tameability_status == "ALLOWED"
                            else tameability_reason_codes
                            or ["TAMEABILITY_NOT_RECOVERED"]
                        )
                        + (
                            rideability_reason_codes
                            if require_confirmed_rideability
                            and rideability_status != "ALLOWED"
                            else []
                        )
                    )
                )
                score_breakdown = dict(row.get("scoreBreakdown") or {})
                if score_breakdown:
                    score_breakdown["evidenceTier"] = (
                        "CONFIRMED" if evidence_confirmed else "CONDITIONAL"
                    )
                row.update(
                    {
                        "speciesKey": species_key,
                        "dinoNameTag": creature.get("dinoNameTag"),
                        "variantCount": variant_counts[species_key],
                        "baseAttackInterval": prepared.get("baseAttackInterval"),
                        "riderAttackInterval": prepared.get("riderAttackInterval"),
                        "attackIntervalSource": prepared.get("attackIntervalSource"),
                        "usageEligibilityStatus": prepared.get(
                            "usageEligibilityStatus"
                        ),
                        "usageConditionReasonCodes": condition_reasons,
                        "usageEstimateBasis": prepared.get("usageEstimateBasis"),
                        "tameabilityStatus": tameability_status,
                        "tameabilityReasonCodes": tameability_reason_codes,
                        "rideabilityStatus": rideability_status,
                        "rideabilityReasonCodes": rideability_reason_codes,
                        "evidence": {
                            "status": "CONFIRMED" if evidence_confirmed else "PARTIAL",
                            "gaps": []
                            if evidence_confirmed
                            else evidence_gaps or ["TAMEABILITY_NOT_RECOVERED"],
                        },
                        "scoreBreakdown": score_breakdown,
                    }
                )
                current = best_by_species.get(species_key)
                current_score = (
                    _estimated_yield(current) if current is not None else None
                )
                if (
                    current is None
                    or current_score is None
                    or score > current_score
                    or (
                        score == current_score
                        and _stable_row_identity(row) < _stable_row_identity(current)
                    )
                ):
                    best_by_species[species_key] = row

        ranked = sorted(
            best_by_species.values(),
            key=lambda row: (
                -float(_estimated_yield(row) or 0.0),
                *_stable_row_identity(row),
            ),
        )
        previous_score: float | None = None
        previous_rank = 0
        ranked_with_positions: list[dict[str, Any]] = []
        for ordinal, source_row in enumerate(ranked, start=1):
            row = dict(source_row)
            score = _estimated_yield(row)
            if score is None:
                continue
            if previous_score is None or score != previous_score:
                previous_rank = ordinal
                previous_score = score
            row["rank"] = previous_rank
            ranked_with_positions.append(row)

        bounded_limit = max(1, min(int(limit), 10))
        selected = ranked_with_positions[:bounded_limit]
        top_score = (
            _estimated_yield(ranked_with_positions[0])
            if ranked_with_positions
            else 0.0
        )
        for row in selected:
            score = _estimated_yield(row) or 0.0
            row["relativeToNodeTopPercent"] = (
                round(min(100.0, max(0.0, score / top_score * 100.0)), 3)
                if top_score is not None and top_score > 0
                else 0.0
            )
            row["rankingTier"] = (
                "CONFIRMED"
                if row.get("evidence", {}).get("status") == "CONFIRMED"
                else "CONDITIONAL"
            )

        coverage = dict(self.catalog.get("coverage") or {})
        coverage.update(
            {
                "speciesEvaluated": len(species_with_eligible_attack),
                "attacksEvaluated": sum(dispositions.values()),
                "attacksRanked": dispositions["RANKED"],
                "attacksUnranked": dispositions["UNRANKED"],
                "attacksIncompatible": dispositions["INCOMPATIBLE"],
                "attacksExcludedByScope": sum(excluded.values()),
                "excludedByReason": dict(sorted(excluded.items())),
                "attacksConditionallyEvaluated": attacks_conditionally_evaluated,
                "conditionallyRankedAttacks": conditionally_ranked_attacks,
                "conditionalEvaluationByReason": dict(
                    sorted(conditional_evaluations.items())
                ),
                "creatureAssetsExcludedFromScope": sum(excluded_creatures.values()),
                "attacksExcludedByCreatureScope": attacks_excluded_by_creature_scope,
                "excludedCreatureByReason": dict(sorted(excluded_creatures.items())),
                "rankedForNodeResource": len(ranked_with_positions),
                "rankedSpeciesWithUnknownTameability": sum(
                    1
                    for row in ranked_with_positions
                    if row.get("tameabilityStatus") != "ALLOWED"
                ),
                "rankedSpeciesWithUnknownRideability": sum(
                    1
                    for row in ranked_with_positions
                    if row.get("rideabilityStatus") != "ALLOWED"
                ),
                "rankedSpeciesConfirmed": sum(
                    1
                    for row in ranked_with_positions
                    if row.get("evidence", {}).get("status") == "CONFIRMED"
                ),
                "rankedSpeciesConditional": sum(
                    1
                    for row in ranked_with_positions
                    if row.get("evidence", {}).get("status") != "CONFIRMED"
                ),
                "returned": len(selected),
                "omitted": max(0, len(ranked_with_positions) - len(selected)),
            }
        )
        complete_scope = coverage.get("claimsAllCreatures") is True
        claim_blockers = [
            str(value)
            for value in self.catalog.get("claimBlockers", [])
            if str(value)
        ]
        evaluation_dataset = self.catalog.get("dataset")
        dataset = dict(node_catalog.get("dataset") or {})
        if isinstance(evaluation_dataset, dict):
            dataset["evaluationRevision"] = evaluation_dataset.get("revision")
            dataset["evaluationGeneratedAt"] = evaluation_dataset.get("generatedAt")
        return {
            "schema": RANKING_RESULT_SCHEMA,
            "dataset": dataset,
            "node": {
                "id": node.get("id"),
                "name": node.get("name"),
                "objectPath": node.get("objectPath"),
            },
            "resource": {
                **resource,
                "harvestComponentPackagePath": component_package,
            },
            "methodology": {
                **dict(self.catalog.get("methodology") or {}),
                "formulaVersion": YIELD_MODEL_VERSION,
                "metric": "estimatedYieldPerNode",
                "scoreBasis": YIELD_SCORE_BASIS,
                "relativeBasis": (
                    "ESTIMATED_RESOURCE_UNITS_PER_COMPLETE_NODE_DIVIDED_BY_"
                    "NODE_RESOURCE_TOP_YIELD"
                ),
                "tiePolicy": "COMPETITION_RANK_FOR_EQUAL_ESTIMATED_YIELD_1_1_3",
                "engineComparisonIndexPolicy": (
                    "COMPATIBILITY_ALIAS_EQUAL_TO_ESTIMATED_YIELD_PER_NODE_"
                    "NEVER_USED_FOR_ORDERING"
                ),
                "conditionalEstimatePolicy": (
                    "BLUEPRINT_OUTPUT_DAMAGE_HOOKS_FAIL_CLOSED;OTHER_DYNAMIC_"
                    "ATTACK_GATES_REMAIN_CONDITIONAL"
                ),
                "warning": (
                    "排名按一个完整新鲜资源点的预计目标资源产量排序；这是静态标准化模型，"
                    "不是服务器环境下的实测产量。"
                ),
            },
            "scopeStatus": (
                "ALL_DISCOVERED_CREATURES_EVALUATED"
                if complete_scope
                else "PARTIAL_CREATURE_EVIDENCE"
            ),
            "claimsCompleteWithinScope": complete_scope,
            "claimsGlobalTop": False,
            "claimBlockers": claim_blockers,
            "evidence": {
                "status": "COMPLETE" if complete_scope else "PARTIAL",
                "blockers": claim_blockers,
            },
            "coverage": coverage,
            "items": selected,
        }

    def rank_node_resource(
        self,
        node_catalog: dict[str, Any],
        *,
        node_id: str,
        node_resource_id: str,
        limit: int = 10,
        evidence_policy: str = POLICY_CONFIRMED,
        variant_policy: str = VARIANT_CANONICAL,
        metric: str = METRIC_STATIC_TOTAL,
        availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        runtime_observations: Mapping[
            tuple[str, str, str, str, int], Mapping[str, Any]
        ]
        | None = None,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
        runtime_profiles_available: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Return Ranking Contract v2 rows, split by evidence tier.

        Legacy in-memory fixtures without a v2 contract marker keep the v1
        behavior. Generated catalogs always carry the marker, so HTTP/API
        defaults are the fail-closed v2 policy.
        """

        methodology = self.catalog.get("methodology")
        contract_version = (
            str(methodology.get("contractVersion") or "")
            if isinstance(methodology, dict)
            else ""
        )
        if contract_version != HARVEST_RANKING_CONTRACT_VERSION:
            return self._rank_node_resource_v1(
                node_catalog,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=limit,
            )
        if evidence_policy not in _EVIDENCE_POLICIES:
            raise ValueError("Unsupported harvest evidence policy.")
        if variant_policy not in _VARIANT_POLICIES:
            raise ValueError("Unsupported harvest variant policy.")
        if metric not in _RANKING_METRICS:
            raise ValueError("Unsupported harvest ranking metric.")
        if availability_policy not in _AVAILABILITY_POLICIES:
            raise ValueError("Unsupported harvest availability policy.")
        runtime_profile_id, runtime_coverage = _runtime_profile_context(
            runtime_observations,
            requested_profile_id=runtime_profile_id,
            runtime_metric=METRIC_CONTRACTS[metric]["runtime"] is True,
            validated_profiles_available=runtime_profiles_available,
        )

        node, resource = find_node_and_resource(
            node_catalog, node_id, node_resource_id
        )
        component_ref = node.get("harvestComponent")
        component_package = canonical_package_path(
            component_ref.get("packagePath")
            if isinstance(component_ref, dict)
            else ""
        )
        component = self.components.get(component_package.casefold())
        if not isinstance(component, dict):
            raise KeyError("HARVEST_COMPONENT_NOT_FOUND")
        usage_scope = str(
            self.catalog.get("methodology", {}).get("usageScope") or TAMED_RIDDEN
        )
        resource_class = str(resource.get("resource") or "")
        raw_entry_index = resource.get("entryIndex")
        resource_entry_index = (
            int(raw_entry_index)
            if isinstance(raw_entry_index, int)
            and not isinstance(raw_entry_index, bool)
            else None
        )

        grouped: dict[str, list[dict[str, Any]]] = {}
        excluded_creatures: Counter[str] = Counter()
        attacks_excluded: Counter[str] = Counter()
        dispositions: Counter[str] = Counter()
        conditional_evaluations: Counter[str] = Counter()
        attacks_conditionally_evaluated = 0
        conditionally_ranked_attacks = 0
        attacks_excluded_by_creature_scope = 0
        rows_with_effectiveness_field = 0
        rows_with_non_neutral_effectiveness = 0
        rows_conditional_because_effectiveness = 0
        for creature in self.creatures:
            tameability = creature.get("tameability")
            rideability = creature.get("rideability")
            tameability_status = str(
                tameability.get("status")
                if isinstance(tameability, dict)
                else "UNKNOWN"
            ) or "UNKNOWN"
            rideability_status = str(
                rideability.get("status")
                if isinstance(rideability, dict)
                else "UNKNOWN"
            ) or "UNKNOWN"
            if tameability_status == "PREVENTED":
                excluded_creatures["CREATURE_NOT_TAMEABLE"] += 1
                attacks_excluded_by_creature_scope += sum(
                    1 for attack in creature.get("attacks", []) if isinstance(attack, dict)
                )
                continue
            if rideability_status == "PREVENTED":
                excluded_creatures["RIDING_NOT_ALLOWED"] += 1
                attacks_excluded_by_creature_scope += sum(
                    1 for attack in creature.get("attacks", []) if isinstance(attack, dict)
                )
                continue
            species_key = str(
                creature.get("speciesKey")
                or creature.get("objectPath")
                or creature.get("name")
                or ""
            ).casefold()
            if species_key:
                grouped.setdefault(species_key, []).append(creature)

        variant_audit_by_species = self._canonical_variant_audit_by_species
        all_variant_selection_audits = self._canonical_variant_audits
        ambiguous_variant_audits = self._canonical_ambiguous_variant_audits
        variant_selection_audits = [
            deepcopy(audit)
            for audit in all_variant_selection_audits[
                :_VARIANT_SELECTION_AUDIT_LIMIT
            ]
        ]

        all_species_rows: list[dict[str, Any]] = []
        attacks_evaluated = 0
        for species_key, variants in sorted(grouped.items()):
            variant_audit = variant_audit_by_species[species_key]
            canonical_path = variant_audit["canonicalObjectPath"]
            variant_best_rows_by_tier: dict[str, list[dict[str, Any]]] = {
                "CONFIRMED": [],
                "CONDITIONAL": [],
            }
            for creature in variants:
                tameability = creature.get("tameability")
                rideability = creature.get("rideability")
                tameability_status = str(
                    tameability.get("status")
                    if isinstance(tameability, dict)
                    else "UNKNOWN"
                ) or "UNKNOWN"
                rideability_status = str(
                    rideability.get("status")
                    if isinstance(rideability, dict)
                    else "UNKNOWN"
                ) or "UNKNOWN"
                tameability_reasons = (
                    [str(value) for value in tameability.get("reasonCodes", []) if value]
                    if isinstance(tameability, dict)
                    else ["TAMEABILITY_NOT_RECOVERED"]
                )
                rideability_reasons = (
                    [str(value) for value in rideability.get("reasonCodes", []) if value]
                    if isinstance(rideability, dict)
                    else ["RIDEABILITY_NOT_RECOVERED"]
                )
                attack_rows: list[dict[str, Any]] = []
                for attack in creature.get("attacks", []):
                    if not isinstance(attack, dict):
                        continue
                    prepared, exclusion_reason = prepare_attack_for_usage_scope(
                        attack, usage_scope=usage_scope
                    )
                    if prepared is None:
                        attacks_excluded[
                            str(exclusion_reason or "ATTACK_SCOPE_UNKNOWN")
                        ] += 1
                        continue
                    row = evaluate_attack_resource(
                        creature=str(creature.get("name") or "Unknown creature"),
                        creature_object_path=str(creature.get("objectPath") or ""),
                        attack=prepared,
                        component=component,
                        resource=resource_class,
                        resource_entry_index=resource_entry_index,
                        damage_type_parents=self.damage_type_parents,
                        resource_damage_overrides=self.resource_damage_overrides,
                        damage_type_gaps=self.damage_type_gaps,
                    )
                    attacks_evaluated += 1
                    disposition = str(row.get("rankingStatus") or "UNRANKED")
                    dispositions[disposition] += 1
                    if disposition != "RANKED":
                        continue
                    effectiveness = row.get("effectivenessQuantityMultiplier")
                    if "effectivenessQuantityMultiplier" in row:
                        rows_with_effectiveness_field += 1
                    effectiveness_is_non_neutral = (
                        isinstance(effectiveness, (int, float))
                        and not isinstance(effectiveness, bool)
                        and not math.isclose(
                            float(effectiveness), 1.0, rel_tol=0.0, abs_tol=1e-9
                        )
                    )
                    if effectiveness_is_non_neutral:
                        rows_with_non_neutral_effectiveness += 1
                        rows_conditional_because_effectiveness += 1
                    _enrich_v2_metrics(row, metric=metric)
                    runtime_key = (
                        str(node_id),
                        str(node_resource_id),
                        species_key,
                        str(creature.get("objectPath") or "").casefold(),
                        int(row.get("attackIndex") or 0),
                    )
                    runtime_observation = (
                        runtime_observations.get(runtime_key)
                        if runtime_observations is not None
                        else None
                    )
                    runtime_observation = _eligible_runtime_observation(
                        runtime_observation,
                        runtime_profile_id=runtime_profile_id,
                        include_preliminary=include_preliminary,
                    )
                    runtime_status = (
                        str(runtime_observation.get("runtimeStatus") or "")
                        if runtime_observation is not None
                        else ""
                    )
                    runtime_observation_is_eligible = runtime_observation is not None
                    if runtime_observation_is_eligible:
                        row.update(
                            {
                                "observedYieldPerNode": runtime_observation.get(
                                    "observedYieldPerNode"
                                ),
                                "observedYieldPerSecond": runtime_observation.get(
                                    "observedYieldPerSecond"
                                ),
                                "runtimeStatus": runtime_observation.get(
                                    "runtimeStatus"
                                ),
                                "runtimeObservation": {
                                    "observationSetId": runtime_observation.get(
                                        "observationSetId"
                                    ),
                                    "runtimeProfileId": runtime_observation.get(
                                        "runtimeProfileId"
                                    )
                                    or runtime_profile_id,
                                    "environmentFingerprint": runtime_observation.get(
                                        "environmentFingerprint"
                                    ),
                                    "trialCount": runtime_observation.get("trialCount"),
                                    "synthetic": False,
                                },
                            }
                        )
                    if _metric_value(row, metric) is None:
                        continue
                    condition_reasons = [
                        str(value)
                        for value in prepared.get("usageConditionReasonCodes", [])
                        if value
                    ]
                    if condition_reasons:
                        attacks_conditionally_evaluated += 1
                        conditionally_ranked_attacks += 1
                        for reason in condition_reasons:
                            conditional_evaluations[reason] += 1
                    evidence_gaps = list(condition_reasons)
                    if tameability_status != "ALLOWED":
                        evidence_gaps.extend(
                            tameability_reasons or ["TAMEABILITY_NOT_RECOVERED"]
                        )
                    if rideability_status != "ALLOWED":
                        evidence_gaps.extend(
                            rideability_reasons or ["RIDEABILITY_NOT_RECOVERED"]
                        )
                    evidence_gaps.extend(
                        str(value) for value in row.get("missingFacts", []) if value
                    )
                    if effectiveness_is_non_neutral:
                        evidence_gaps.append(
                            "EFFECTIVENESS_QUANTITY_MULTIPLIER_NOT_MODELED"
                        )
                    if (
                        METRIC_CONTRACTS[metric]["runtime"] is True
                        and runtime_observation_is_eligible
                        and runtime_status == "OBSERVED_PRELIMINARY"
                    ):
                        evidence_gaps.append(
                            "OBSERVED_PRELIMINARY_MINIMUM_TRIALS_NOT_MET"
                        )
                    evidence_gaps = sorted(set(evidence_gaps))
                    confirmed = not evidence_gaps
                    score_breakdown = dict(row.get("scoreBreakdown") or {})
                    if score_breakdown:
                        score_breakdown["evidenceTier"] = (
                            "CONFIRMED" if confirmed else "CONDITIONAL"
                        )
                    row.update(
                        {
                            "speciesKey": species_key,
                            "dinoNameTag": creature.get("dinoNameTag"),
                            "variantCount": len(variants),
                            "baseAttackInterval": prepared.get("baseAttackInterval"),
                            "riderAttackInterval": prepared.get("riderAttackInterval"),
                            "attackIntervalSource": prepared.get("attackIntervalSource"),
                            "usageEligibilityStatus": prepared.get(
                                "usageEligibilityStatus"
                            ),
                            "usageConditionReasonCodes": condition_reasons,
                            "usageEstimateBasis": prepared.get("usageEstimateBasis"),
                            "tameabilityStatus": tameability_status,
                            "tameabilityReasonCodes": tameability_reasons,
                            "rideabilityStatus": rideability_status,
                            "rideabilityReasonCodes": rideability_reasons,
                            "evidence": {
                                "status": "CONFIRMED" if confirmed else "PARTIAL",
                                "gaps": evidence_gaps,
                            },
                            "rankingTier": "CONFIRMED" if confirmed else "CONDITIONAL",
                            "scoreBreakdown": score_breakdown,
                        }
                    )
                    attack_rows.append(row)
                attack_rows.sort(
                    key=lambda row: (
                        -float(_metric_value(row, metric) or 0.0),
                        *_stable_row_identity(row),
                    )
                )
                for tier in ("CONFIRMED", "CONDITIONAL"):
                    best_for_tier = next(
                        (
                            row
                            for row in attack_rows
                            if row.get("rankingTier") == tier
                        ),
                        None,
                    )
                    if best_for_tier is not None:
                        variant_best_rows_by_tier[tier].append(best_for_tier)

            variant_paths = [
                str(creature.get("objectPath") or "") for creature in variants
            ]
            for tier, variant_best_rows in variant_best_rows_by_tier.items():
                if not variant_best_rows:
                    continue
                rows_by_path = {
                    str(row.get("creatureObjectPath") or ""): row
                    for row in variant_best_rows
                }
                canonical_row = rows_by_path.get(canonical_path)
                exploratory_row = min(
                    variant_best_rows,
                    key=lambda row: (
                        -float(_metric_value(row, metric) or 0.0),
                        *_stable_row_identity(row),
                    ),
                )
                if variant_policy == VARIANT_ALL:
                    selected_rows = sorted(
                        variant_best_rows,
                        key=lambda row: (
                            _canonical_variant_key(
                                next(
                                    creature
                                    for creature in variants
                                    if str(creature.get("objectPath") or "")
                                    == str(row.get("creatureObjectPath") or "")
                                )
                            ),
                            _stable_row_identity(row),
                        ),
                    )
                elif variant_policy == VARIANT_BEST_DISCOVERED_EXPLORATORY:
                    selected_rows = [exploratory_row]
                else:
                    selected_rows = [canonical_row] if canonical_row is not None else []
                comparison = [
                    {
                        "objectPath": path,
                        "creature": (
                            rows_by_path[path].get("creature")
                            if path in rows_by_path
                            else next(
                                (
                                    creature.get("name")
                                    for creature in variants
                                    if str(creature.get("objectPath") or "") == path
                                ),
                                None,
                            )
                        ),
                        "selectedMetricValue": (
                            _metric_value(rows_by_path[path], metric)
                            if path in rows_by_path
                            else None
                        ),
                        "rankingTier": tier if path in rows_by_path else None,
                        "canonical": path == canonical_path,
                        "exploratoryBest": path
                        == str(exploratory_row.get("creatureObjectPath") or ""),
                    }
                    for path in variant_paths
                ]
                for selected_row in selected_rows:
                    row = dict(selected_row)
                    selected_score = _metric_value(row, metric)
                    exploratory_score = _metric_value(exploratory_row, metric)
                    selected_path = str(row.get("creatureObjectPath") or "")
                    row["variantSelection"] = {
                        "policy": variant_policy,
                        "selectedObjectPath": selected_path,
                        "canonicalObjectPath": canonical_path,
                        "selectionReasons": list(
                            variant_audit["selectionReasons"]
                        ),
                        "excludedVariantClasses": list(
                            variant_audit["excludedVariantClasses"]
                        ),
                        "ambiguous": variant_audit["ambiguous"],
                        "ambiguityReasons": list(
                            variant_audit["ambiguityReasons"]
                        ),
                        "excludedObjectPaths": [
                            path for path in variant_paths if path != selected_path
                        ],
                        "comparison": comparison,
                        "higherExploratoryVariantExists": bool(
                            selected_score is not None
                            and exploratory_score is not None
                            and exploratory_score > selected_score
                        ),
                    }
                    all_species_rows.append(row)

        def ranked_tier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            ordered = sorted(
                rows,
                key=lambda row: (
                    -float(_metric_value(row, metric) or 0.0),
                    *_stable_row_identity(row),
                ),
            )
            previous_score: float | None = None
            previous_rank = 0
            top_score = _metric_value(ordered[0], metric) if ordered else None
            result: list[dict[str, Any]] = []
            for ordinal, source_row in enumerate(ordered, start=1):
                row = dict(source_row)
                score = _metric_value(row, metric)
                if score is None:
                    continue
                if previous_score is None or score != previous_score:
                    previous_rank = ordinal
                    previous_score = score
                row["rank"] = previous_rank
                row["relativeToNodeTopPercent"] = (
                    round(min(100.0, max(0.0, score / top_score * 100.0)), 6)
                    if top_score is not None and top_score > 0
                    else 0.0
                )
                row["relativeBasisTier"] = row.get("rankingTier")
                result.append(row)
            return result

        confirmed_all = ranked_tier(
            [row for row in all_species_rows if row.get("rankingTier") == "CONFIRMED"]
        )
        conditional_all = ranked_tier(
            [row for row in all_species_rows if row.get("rankingTier") != "CONFIRMED"]
        )
        bounded_limit = max(1, min(int(limit), 10))
        confirmed_items = confirmed_all[:bounded_limit]
        conditional_items = (
            conditional_all[:bounded_limit]
            if evidence_policy == POLICY_INCLUDE_CONDITIONAL
            else []
        )
        # The legacy alias remains confirmed-only so older clients cannot flatten
        # a conditional winner into the primary ranking.
        compatibility_items = list(confirmed_items)

        coverage = dict(self.catalog.get("coverage") or {})
        coverage.update(
            {
                "speciesEvaluated": len(grouped),
                "attacksEvaluated": attacks_evaluated,
                "attacksRanked": dispositions["RANKED"],
                "attacksUnranked": dispositions["UNRANKED"],
                "attacksIncompatible": dispositions["INCOMPATIBLE"],
                "attacksExcludedByScope": sum(attacks_excluded.values()),
                "excludedByReason": dict(sorted(attacks_excluded.items())),
                "attacksConditionallyEvaluated": attacks_conditionally_evaluated,
                "conditionallyRankedAttacks": conditionally_ranked_attacks,
                "conditionalEvaluationByReason": dict(
                    sorted(conditional_evaluations.items())
                ),
                "rowsWithEffectivenessField": rows_with_effectiveness_field,
                "rowsWithNonNeutralEffectiveness": (
                    rows_with_non_neutral_effectiveness
                ),
                "rowsConditionalBecauseEffectiveness": (
                    rows_conditional_because_effectiveness
                ),
                "canonicalVariantAmbiguousSpecies": len(
                    ambiguous_variant_audits
                ),
                "canonicalCreatureAssetsAudited": len(self.creatures),
                "canonicalVariantsAudited": len(all_variant_selection_audits),
                "variantSelectionAuditsReturned": len(variant_selection_audits),
                "variantSelectionAuditsOmitted": max(
                    0,
                    len(all_variant_selection_audits)
                    - len(variant_selection_audits),
                ),
                "canonicalVariantAmbiguityExamples": [
                    deepcopy(audit) for audit in ambiguous_variant_audits[:10]
                ],
                "creatureAssetsExcludedFromScope": sum(excluded_creatures.values()),
                "attacksExcludedByCreatureScope": attacks_excluded_by_creature_scope,
                "excludedCreatureByReason": dict(sorted(excluded_creatures.items())),
                "rankedForNodeResource": len(confirmed_all) + len(conditional_all),
                "rankedSpeciesConfirmed": len(confirmed_all),
                "rankedSpeciesConditional": len(conditional_all),
                "returnedConfirmed": len(confirmed_items),
                "returnedConditional": len(conditional_items),
                "returned": len(compatibility_items),
                "omitted": max(
                    0,
                    len(confirmed_all) - len(compatibility_items),
                ),
            }
        )
        complete_scope = coverage.get("claimsAllCreatures") is True
        claim_blockers = [
            str(value) for value in self.catalog.get("claimBlockers", []) if str(value)
        ]
        evaluation_dataset = self.catalog.get("dataset")
        node_dataset = dict(node_catalog.get("dataset") or {})
        evaluation_dataset = (
            dict(evaluation_dataset) if isinstance(evaluation_dataset, dict) else {}
        )
        dataset = {
            **node_dataset,
            "evaluationRevision": evaluation_dataset.get("revision"),
            "evaluationGeneratedAt": evaluation_dataset.get("generatedAt"),
        }
        identity = {
            "extractorVersion": evaluation_dataset.get("extractorVersion"),
            "modelVersion": YIELD_MODEL_VERSION,
            "policyVersion": HARVEST_RANKING_POLICY_VERSION,
            "resultSchemaVersion": RANKING_RESULT_SCHEMA,
            "nodeCatalogRevision": node_dataset.get("revision"),
            "evaluationCatalogRevision": evaluation_dataset.get("revision"),
            "componentCatalogRevision": evaluation_dataset.get(
                "componentDatasetRevision"
            ),
        }
        metric_labels = {
            METRIC_STATIC_TOTAL: "静态单节点目标资源总产量",
            METRIC_STATIC_CYCLE_SPEED: "静态攻击周期折算产量",
            METRIC_OBSERVED_PER_NODE: "受控实测单节点目标资源产量",
            METRIC_OBSERVED_PER_SECOND: "受控实测每秒目标资源产量",
        }
        metric_contract = METRIC_CONTRACTS[metric]
        metric_warning = (
            "实测指标仅可在所选 runtimeProfileId 环境内比较；preliminary 仍为条件性"
            "结果，synthetic 永不进入可发布排行。"
            if metric_contract["runtime"] is True
            else (
                "静态模型指标不是服务器环境下的实测产量或真实每秒产量；条件性结果"
                "不会占用已确认榜名次或基线。"
            )
        )
        return {
            "schema": RANKING_RESULT_SCHEMA,
            "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
            "identity": identity,
            "dataset": dataset,
            "node": {
                "id": node.get("id"),
                "name": node.get("name"),
                "objectPath": node.get("objectPath"),
            },
            "resource": {
                **resource,
                "harvestComponentPackagePath": component_package,
            },
            "queryPolicy": {
                "evidence": evidence_policy,
                "variant": variant_policy,
                "metric": metric,
                "availability": availability_policy,
                "runtimeProfileId": runtime_profile_id,
                "includePreliminary": bool(include_preliminary),
                "exploratory": variant_policy
                == VARIANT_BEST_DISCOVERED_EXPLORATORY,
            },
            "methodology": {
                **dict(self.catalog.get("methodology") or {}),
                "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
                "policyVersion": HARVEST_RANKING_POLICY_VERSION,
                "formulaVersion": YIELD_MODEL_VERSION,
                "metric": metric,
                "metricLabel": metric_labels[metric],
                "scoreBasis": metric_contract["scoreBasis"],
                "unit": metric_contract["unit"],
                "runtime": metric_contract["runtime"],
                "firstHitTiming": "FIRST_HIT_AT_END_OF_FIRST_ATTACK_CYCLE",
                "relativeBasis": "WITHIN_SAME_EVIDENCE_TIER_SELECTED_METRIC",
                "tiePolicy": "COMPETITION_RANK_FOR_EQUAL_SELECTED_METRIC_1_1_3",
                "variantSelection": variant_policy,
                "availabilityPolicy": availability_policy,
                "engineComparisonIndexPolicy": (
                    "COMPATIBILITY_ALIAS_EQUAL_TO_STATIC_COMPLETE_NODE_TARGET_YIELD_"
                    "NEVER_USED_FOR_ORDERING"
                ),
                "warning": metric_warning,
            },
            "confirmedStatus": "AVAILABLE" if confirmed_all else "UNAVAILABLE",
            "conditionalStatus": "AVAILABLE" if conditional_all else "UNAVAILABLE",
            "scopeStatus": (
                "ALL_DISCOVERED_CREATURES_EVALUATED"
                if complete_scope
                else "PARTIAL_CREATURE_EVIDENCE"
            ),
            "claimsCompleteWithinScope": complete_scope,
            "claimsGlobalTop": False,
            "claimBlockers": claim_blockers,
            "evidence": {
                "status": "COMPLETE" if complete_scope else "PARTIAL",
                "blockers": claim_blockers,
            },
            "coverage": coverage,
            "runtimeCoverage": runtime_coverage,
            "variantSelectionAudits": variant_selection_audits,
            "confirmedItems": confirmed_items,
            "conditionalItems": conditional_items,
            "items": compatibility_items,
        }
