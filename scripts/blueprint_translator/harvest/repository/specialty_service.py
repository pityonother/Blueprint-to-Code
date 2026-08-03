"""Reverse creature-specialty ranking and response projection."""

from __future__ import annotations

import copy
from collections import Counter, OrderedDict
from typing import Any

from ...harvest_catalog_sqlite import (
    SQLiteHarvestCatalog,
    SQLiteHarvestCatalogInvalid,
)
from ...harvest_runtime_observations import HarvestRuntimeObservationIndex
from ...resource_nodes import canonical_package_path
from ..contracts import YIELD_MODEL_VERSION, YIELD_SCORE_BASIS
from ..evaluation import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    POLICY_INCLUDE_CONDITIONAL,
    RANKING_RESULT_SCHEMA,
    VARIANT_CANONICAL,
    HarvestEvaluationEngine,
)
from ..evaluation.contracts import METRIC_CONTRACTS
from ..evaluation.specialties import _eligible_attack_candidates
from .caches import (
    SPECIALTY_RESPONSE_CACHE_CAPACITY,
    V2_TIER_BASELINE_CACHE_CAPACITY,
)
from .creature_service import _creature_representative_key
from .dataset_loader import HarvestDatasetInvalid


CREATURE_SPECIALTIES_SCHEMA = "blueprint-to-code.harvest-creature-specialties/v3"


_SPECIALTY_ROW_FIELDS = (
    "creature",
    "creatureObjectPath",
    "speciesKey",
    "dinoNameTag",
    "variantCount",
    "attackIndex",
    "attackName",
    "sourceDamageType",
    "effectiveDamageType",
    "damageOverrideApplied",
    "baseDamage",
    "baseAttackInterval",
    "riderAttackInterval",
    "attackInterval",
    "attackIntervalSource",
    "usageEligibilityStatus",
    "usageConditionReasonCodes",
    "usageEstimateBasis",
    "damageMultiplier",
    "harvestQuantityMultiplier",
    "resourceWeightShare",
    "harvestPressurePerSecond",
    "estimatedYieldPerNode",
    "staticCompleteNodeTargetYield",
    "staticYieldPerAttackCycleSecond",
    "staticAttackCycleSecondsToDepleteNode",
    "staticFirstHitTiming",
    "observedYieldPerNode",
    "observedYieldPerSecond",
    "runtimeStatus",
    "runtimeObservation",
    "estimatedGrantCallsPerNode",
    "estimatedHitsToDepleteNode",
    "expectedQuantityPerSelection",
    "quantityRandomPower",
    "normalizedHarvestAmountScale",
    "yieldModelVersion",
    "yieldModelBasis",
    "engineComparisonIndex",
    "rankingStatus",
    "reasonCode",
    "rankingTier",
    "scoreBasis",
    "tameabilityStatus",
    "tameabilityReasonCodes",
    "rideabilityStatus",
    "rideabilityReasonCodes",
    "warnings",
    "warningsByScope",
    "evidence",
    "scoreBreakdown",
    "variantSelection",
)


def _compact_specialty_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(row[key])
        for key in _SPECIALTY_ROW_FIELDS
        if key in row
    }

def _specialty_competition_key(row: dict[str, Any]) -> tuple[float, float]:
    return (
        float(row.get("relativeToNodeTopPercent") or 0.0),
        float(row.get("selectedMetricValue") or 0.0),
    )

def _specialty_identity_text(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()

def _specialty_identity_key(row: dict[str, Any]) -> tuple[object, ...]:
    """Order equal specialty scores by immutable resource/node identities."""

    resource = row.get("resource")
    resource = resource if isinstance(resource, dict) else {}
    node = row.get("node")
    node = node if isinstance(node, dict) else {}
    entry_index = resource.get("entryIndex")
    stable_entry_index = (
        (0, int(entry_index), "")
        if isinstance(entry_index, int) and not isinstance(entry_index, bool)
        else (1, 0, _specialty_identity_text(entry_index))
    )
    attack_index = row.get("attackIndex")
    stable_attack_index = (
        (0, int(attack_index), "")
        if isinstance(attack_index, int) and not isinstance(attack_index, bool)
        else (1, 0, _specialty_identity_text(attack_index))
    )
    return (
        _specialty_identity_text(resource.get("nodeResourceId")),
        _specialty_identity_text(node.get("id")),
        _specialty_identity_text(resource.get("resource")),
        _specialty_identity_text(node.get("objectPath")),
        stable_entry_index,
        _specialty_identity_text(resource.get("harvestComponentPackagePath")),
        _specialty_identity_text(row.get("creatureObjectPath")),
        stable_attack_index,
    )

def _specialty_page_sort_key(row: dict[str, Any]) -> tuple[object, ...]:
    competition_key = _specialty_competition_key(row)
    return (
        -competition_key[0],
        -competition_key[1],
        *_specialty_identity_key(row),
    )


class SpecialtyServiceMixin:
    def _v2_tier_baselines(
        self,
        engine: HarvestEvaluationEngine,
        node_catalog: dict[str, Any],
        *,
        evaluation_revision: str,
        node_id: str,
        node_resource_id: str,
        evidence_policy: str,
        variant_policy: str,
        metric: str,
        availability_policy: str,
        runtime_index: HarvestRuntimeObservationIndex,
        include_preliminary: bool,
    ) -> dict[str, Any]:
        """Cache the two evidence-tier baselines without materializing a cross product."""

        node_dataset = dict(node_catalog.get("dataset") or {})
        cache_key = (
            "V2_TIER_BASELINES",
            str(node_dataset.get("revision") or ""),
            evaluation_revision,
            YIELD_MODEL_VERSION,
            HARVEST_RANKING_POLICY_VERSION,
            RANKING_RESULT_SCHEMA,
            node_id,
            node_resource_id,
            evidence_policy,
            variant_policy,
            metric,
            availability_policy,
            runtime_index.revision,
            runtime_index.runtime_profile_selected,
            bool(include_preliminary),
        )
        with self._lock:
            cached = self._v2_tier_baseline_cache.pop(cache_key, None)
            if cached is not None:
                self._v2_tier_baseline_cache[cache_key] = cached
                return copy.deepcopy(cached)

        ranking = engine.rank_node_resource(
            node_catalog,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=1,
            evidence_policy=evidence_policy,
            variant_policy=variant_policy,
            metric=metric,
            availability_policy=availability_policy,
            runtime_observations=runtime_index.rows,
            runtime_profile_id=runtime_index.runtime_profile_selected,
            include_preliminary=include_preliminary,
            runtime_profiles_available=getattr(
                runtime_index, "runtime_profiles_available", None
            ),
        )
        computed: dict[str, Any] = {}
        for tier, field in (
            ("CONFIRMED", "confirmedItems"),
            ("CONDITIONAL", "conditionalItems"),
        ):
            rows = ranking.get(field)
            computed[tier] = (
                dict(rows[0])
                if isinstance(rows, list)
                and rows
                and isinstance(rows[0], dict)
                else None
            )

        with self._lock:
            existing = self._v2_tier_baseline_cache.pop(cache_key, None)
            cached = existing if existing is not None else copy.deepcopy(computed)
            self._v2_tier_baseline_cache[cache_key] = cached
            while (
                len(self._v2_tier_baseline_cache)
                > V2_TIER_BASELINE_CACHE_CAPACITY
            ):
                self._v2_tier_baseline_cache.popitem(last=False)
        return copy.deepcopy(cached)

    def creature_specialties(
        self,
        species_key: str,
        *,
        offset: int = 0,
        limit: int = 24,
        evidence_policy: str = POLICY_CONFIRMED,
        variant_policy: str = VARIANT_CANONICAL,
        metric: str = METRIC_STATIC_TOTAL,
        availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
    ) -> dict[str, Any]:
        """Return v2-contract specialties in relative-first server order."""

        evaluation_catalog, engine = self._load_evaluation()
        methodology = evaluation_catalog.get("methodology")
        if not isinstance(methodology, dict) or methodology.get(
            "contractVersion"
        ) != HARVEST_RANKING_CONTRACT_VERSION:
            return self._creature_specialties_v1(
                species_key,
                offset=offset,
                limit=limit,
            )
        sqlite_catalog: SQLiteHarvestCatalog | None = None
        if self.sqlite_catalog_path is not None:
            try:
                sqlite_catalog = self._load_sqlite_catalog()
                node_catalog = {"dataset": sqlite_catalog.dataset()}
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        else:
            node_catalog = self._load_catalog()
        # Validate policy values through the authoritative engine before any
        # potentially expensive traversal.
        if evidence_policy not in {POLICY_CONFIRMED, POLICY_INCLUDE_CONDITIONAL}:
            raise ValueError("Unsupported harvest evidence policy.")
        metric_contract = METRIC_CONTRACTS.get(metric)
        if metric_contract is None:
            raise ValueError("Unsupported harvest ranking metric.")
        runtime_index = self._load_runtime_observations(
            self._runtime_identity(node_catalog, evaluation_catalog),
            runtime_profile_id=runtime_profile_id,
            include_preliminary=include_preliminary,
            allow_unselected_profiles=not bool(metric_contract["runtime"]),
        )

        evaluation_revision, component_revision = self._evaluation_revisions(
            node_catalog, evaluation_catalog
        )
        requested_key = " ".join(str(species_key or "").casefold().split())
        variants = [
            creature
            for creature in evaluation_catalog.get("creatures", [])
            if isinstance(creature, dict)
            and " ".join(
                str(
                    creature.get("speciesKey")
                    or creature.get("objectPath")
                    or creature.get("name")
                    or ""
                )
                .casefold()
                .split()
            )
            == requested_key
        ]
        if not variants:
            raise KeyError("HARVEST_SPECIES_NOT_FOUND")
        bounded_offset = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 100))
        node_dataset = dict(node_catalog.get("dataset") or {})
        evaluation_dataset = dict(evaluation_catalog.get("dataset") or {})
        specialty_cache_key = (
            str(node_dataset.get("revision") or ""),
            evaluation_revision,
            component_revision,
            str(evaluation_dataset.get("extractorVersion") or ""),
            YIELD_MODEL_VERSION,
            HARVEST_RANKING_POLICY_VERSION,
            CREATURE_SPECIALTIES_SCHEMA,
            requested_key,
            bounded_offset,
            bounded_limit,
            evidence_policy,
            variant_policy,
            metric,
            availability_policy,
            runtime_index.revision,
            runtime_index.runtime_profile_selected,
            bool(include_preliminary),
        )
        with self._lock:
            cached_specialty = self._specialty_response_cache.pop(
                specialty_cache_key, None
            )
            if cached_specialty is not None:
                self._specialty_response_cache[
                    specialty_cache_key
                ] = cached_specialty
                return copy.deepcopy(cached_specialty)
        if sqlite_catalog is not None:
            try:
                node_catalog = sqlite_catalog.catalog_for_specialties()
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        species_engine = HarvestEvaluationEngine(
            {**evaluation_catalog, "creatures": variants}
        )

        representatives: OrderedDict[tuple[object, ...], tuple[str, str]] = (
            OrderedDict()
        )
        occurrences: list[
            tuple[tuple[object, ...], dict[str, Any], dict[str, Any]]
        ] = []
        nodes = node_catalog.get("nodes")
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            component_ref = node.get("harvestComponent")
            component_package = canonical_package_path(
                component_ref.get("packagePath")
                if isinstance(component_ref, dict)
                else ""
            )
            resources = node.get("resources", {}).get("items", [])
            for resource in resources if isinstance(resources, list) else []:
                if not isinstance(resource, dict):
                    continue
                raw_entry_index = resource.get("entryIndex")
                entry_index = (
                    int(raw_entry_index)
                    if isinstance(raw_entry_index, int)
                    and not isinstance(raw_entry_index, bool)
                    else None
                )
                evaluation_key: tuple[object, ...] = (
                    component_package.casefold(),
                    str(resource.get("resource") or "").casefold(),
                    entry_index,
                )
                pair = (
                    str(node.get("id") or ""),
                    str(resource.get("nodeResourceId") or ""),
                )
                key = (
                    (*evaluation_key, *pair)
                    if bool(metric_contract["runtime"])
                    else evaluation_key
                )
                representatives.setdefault(key, pair)
                occurrences.append((key, node, resource))

        selected_by_key: dict[
            tuple[object, ...], dict[str, dict[str, Any]]
        ] = {}
        top_by_key: dict[
            tuple[object, ...], dict[str, dict[str, Any]]
        ] = {}
        pair_dispositions: Counter[str] = Counter()
        for key, (node_id, node_resource_id) in representatives.items():
            selected_result = species_engine.rank_node_resource(
                node_catalog,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=10,
                evidence_policy=POLICY_INCLUDE_CONDITIONAL,
                variant_policy=variant_policy,
                metric=metric,
                availability_policy=availability_policy,
                runtime_observations=runtime_index.rows,
                runtime_profile_id=runtime_index.runtime_profile_selected,
                include_preliminary=include_preliminary,
                runtime_profiles_available=getattr(
                    runtime_index, "runtime_profiles_available", None
                ),
            )
            selected_rows_by_tier: dict[str, dict[str, Any]] = {}
            for tier, field in (
                ("CONFIRMED", "confirmedItems"),
                ("CONDITIONAL", "conditionalItems"),
            ):
                rows = selected_result.get(field)
                selected_row = next(
                    (
                        dict(row)
                        for row in rows
                        if isinstance(row, dict)
                        and str(row.get("speciesKey") or "").casefold()
                        == requested_key
                    ),
                    None,
                ) if isinstance(rows, list) else None
                if selected_row is not None:
                    selected_rows_by_tier[tier] = selected_row
            if not selected_rows_by_tier:
                pair_dispositions["NOT_RANKED_FOR_SPECIES"] += 1
                continue
            tier_baselines = self._v2_tier_baselines(
                engine,
                node_catalog,
                evaluation_revision=evaluation_revision,
                node_id=node_id,
                node_resource_id=node_resource_id,
                evidence_policy=POLICY_INCLUDE_CONDITIONAL,
                variant_policy=variant_policy,
                metric=metric,
                availability_policy=availability_policy,
                runtime_index=runtime_index,
                include_preliminary=include_preliminary,
            )
            for tier_key, selected_row in selected_rows_by_tier.items():
                cached_top_row = tier_baselines.get(tier_key)
                top_row = (
                    dict(cached_top_row)
                    if isinstance(cached_top_row, dict)
                    else None
                )
                if top_row is None:
                    pair_dispositions[f"{tier_key}_BASELINE_UNAVAILABLE"] += 1
                    continue
                selected_by_key.setdefault(key, {})[tier_key] = selected_row
                top_by_key.setdefault(key, {})[tier_key] = top_row
            if key in selected_by_key:
                pair_dispositions["RANKED"] += 1

        ranked_rows: list[dict[str, Any]] = []
        for key, node, resource in occurrences:
            selected_rows_by_tier = selected_by_key.get(key, {})
            top_rows_by_tier = top_by_key.get(key, {})
            for tier_key in ("CONFIRMED", "CONDITIONAL"):
                selected_row = selected_rows_by_tier.get(tier_key)
                top_row = top_rows_by_tier.get(tier_key)
                if selected_row is None or top_row is None:
                    continue
                selected_score = selected_row.get(metric)
                top_score = top_row.get(metric)
                if (
                    not isinstance(selected_score, (int, float))
                    or isinstance(selected_score, bool)
                    or not isinstance(top_score, (int, float))
                    or isinstance(top_score, bool)
                    or float(top_score) <= 0
                ):
                    continue
                relative_percent = round(
                    min(
                        100.0,
                        max(0.0, float(selected_score) / float(top_score) * 100.0),
                    ),
                    6,
                )
                component_ref = node.get("harvestComponent")
                component_package = canonical_package_path(
                    component_ref.get("packagePath")
                    if isinstance(component_ref, dict)
                    else ""
                )
                ranked_rows.append(
                    {
                        **_compact_specialty_row(selected_row),
                        "node": {
                            "id": node.get("id"),
                            "name": node.get("name"),
                            "objectPath": node.get("objectPath"),
                        },
                        "resource": {
                            **resource,
                            "harvestComponentPackagePath": component_package,
                        },
                        "selectedMetric": metric,
                        "selectedMetricValue": float(selected_score),
                        "nodeTopSelectedMetricValue": float(top_score),
                        "nodeTopStaticCompleteNodeTargetYield": top_row.get(
                            "staticCompleteNodeTargetYield"
                        ),
                        "nodeTopEstimatedYieldPerNode": top_row.get(
                            "estimatedYieldPerNode"
                        ),
                        "relativeToNodeTopPercent": relative_percent,
                        "relativeBasisTier": selected_row.get("rankingTier"),
                        "nodeTop": {
                            "speciesKey": top_row.get("speciesKey"),
                            "creature": top_row.get("creature"),
                            "creatureObjectPath": top_row.get("creatureObjectPath"),
                            "attackIndex": top_row.get("attackIndex"),
                            "attackName": top_row.get("attackName"),
                            "selectedMetric": metric,
                            "selectedMetricValue": float(top_score),
                            "staticCompleteNodeTargetYield": top_row.get(
                                "staticCompleteNodeTargetYield"
                            ),
                            "estimatedYieldPerNode": top_row.get(
                                "estimatedYieldPerNode"
                            ),
                            "rankingTier": top_row.get("rankingTier"),
                            "evidence": copy.deepcopy(top_row.get("evidence") or {}),
                        },
                    }
                )

        def rank_tier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rows.sort(key=_specialty_page_sort_key)
            previous_primary: tuple[float, float] | None = None
            previous_rank = 0
            for ordinal, row in enumerate(rows, start=1):
                primary = _specialty_competition_key(row)
                if previous_primary is None or primary != previous_primary:
                    previous_rank = ordinal
                    previous_primary = primary
                row["rank"] = previous_rank
            return rows

        confirmed_all = rank_tier(
            [row for row in ranked_rows if row.get("rankingTier") == "CONFIRMED"]
        )
        conditional_all = rank_tier(
            [row for row in ranked_rows if row.get("rankingTier") != "CONFIRMED"]
        )
        visible_rows = [
            *confirmed_all,
            *(conditional_all if evidence_policy == POLICY_INCLUDE_CONDITIONAL else []),
        ]
        page_rows = visible_rows[bounded_offset : bounded_offset + bounded_limit]
        confirmed_page = [
            copy.deepcopy(row)
            for row in page_rows
            if row.get("rankingTier") == "CONFIRMED"
        ]
        conditional_page = [
            copy.deepcopy(row)
            for row in page_rows
            if row.get("rankingTier") != "CONFIRMED"
        ]

        evaluation_coverage = dict(evaluation_catalog.get("coverage") or {})
        claims_all_creatures = evaluation_coverage.get("claimsAllCreatures") is True
        claim_blockers = [
            str(value)
            for value in evaluation_catalog.get("claimBlockers", [])
            if str(value)
        ]
        representative = min(variants, key=_creature_representative_key)
        total = len(visible_rows)
        response = {
            "schema": CREATURE_SPECIALTIES_SCHEMA,
            "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
            "identity": {
                "extractorVersion": evaluation_dataset.get("extractorVersion"),
                "modelVersion": YIELD_MODEL_VERSION,
                "policyVersion": HARVEST_RANKING_POLICY_VERSION,
                "resultSchemaVersion": CREATURE_SPECIALTIES_SCHEMA,
                "nodeCatalogRevision": node_dataset.get("revision"),
                "evaluationCatalogRevision": evaluation_revision,
                "componentCatalogRevision": evaluation_dataset.get(
                    "componentDatasetRevision"
                ),
                "runtimeObservationRevision": runtime_index.revision,
            },
            "dataset": {
                **node_dataset,
                "evaluationRevision": evaluation_revision,
                "evaluationGeneratedAt": evaluation_dataset.get("generatedAt"),
            },
            "species": {
                "speciesKey": str(representative.get("speciesKey") or requested_key),
                "name": representative.get("name"),
                "dinoNameTag": representative.get("dinoNameTag"),
                "variantCount": len(variants),
            },
            "queryPolicy": {
                "evidence": evidence_policy,
                "variant": variant_policy,
                "metric": metric,
                "availability": availability_policy,
                "runtimeProfileId": runtime_index.runtime_profile_selected,
                "includePreliminary": bool(include_preliminary),
                "exploratory": variant_policy
                == "BEST_DISCOVERED_VARIANT_EXPLORATORY",
            },
            "methodology": {
                **methodology,
                "contractVersion": HARVEST_RANKING_CONTRACT_VERSION,
                "metric": metric,
                "sortMetric": (
                    "relativeToNodeTopPercent DESC, selectedMetricValue DESC, "
                    "resource.nodeResourceId, node.id, resource.resource, "
                    "node.objectPath, resource.entryIndex, "
                    "resource.harvestComponentPackagePath, "
                    "creatureObjectPath, attackIndex"
                ),
                "relativeBasis": "SAME_EVIDENCE_TIER_NODE_RESOURCE_TOP",
                "tiePolicy": (
                    "COMPETITION_RANK_FOR_EQUAL_RELATIVE_PERCENT_AND_SELECTED_METRIC"
                ),
                "scoreBasis": metric_contract["scoreBasis"],
                "unit": metric_contract["unit"],
                "runtime": metric_contract["runtime"],
            },
            "confirmedStatus": "AVAILABLE" if confirmed_all else "UNAVAILABLE",
            "conditionalStatus": "AVAILABLE" if conditional_all else "UNAVAILABLE",
            "scopeStatus": (
                "ALL_DISCOVERED_CREATURES_EVALUATED"
                if claims_all_creatures
                else "PARTIAL_CREATURE_EVIDENCE"
            ),
            "claimsCompleteWithinScope": claims_all_creatures,
            "claimsGlobalTop": False,
            "claimBlockers": claim_blockers,
            "evidence": {
                "status": "COMPLETE" if claims_all_creatures else "PARTIAL",
                "blockers": claim_blockers,
            },
            "coverage": {
                **evaluation_coverage,
                "speciesVariantsMatched": len(variants),
                "nodeResourcePairsDiscovered": len(occurrences),
                "uniqueEvaluationPairs": len(representatives),
                "uniqueEvaluationPairsRanked": len(selected_by_key),
                "nodeResourcePairsRanked": len(ranked_rows),
                "rankedConfirmed": len(confirmed_all),
                "rankedConditional": len(conditional_all),
                "pairDispositionCounts": dict(sorted(pair_dispositions.items())),
                "returned": len(page_rows),
                "omitted": max(0, total - bounded_offset - len(page_rows)),
            },
            "runtimeCoverage": {
                "filesScanned": runtime_index.files_scanned,
                **runtime_index.coverage,
            },
            "page": {
                "offset": bounded_offset,
                "limit": bounded_limit,
                "total": total,
                "returned": len(page_rows),
                "omitted": max(0, total - bounded_offset - len(page_rows)),
            },
            "total": total,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "nextOffset": (
                bounded_offset + len(page_rows)
                if bounded_offset + len(page_rows) < total
                else None
            ),
            "confirmedItems": confirmed_page,
            "conditionalItems": conditional_page,
            "items": list(confirmed_page),
        }
        with self._lock:
            existing = self._specialty_response_cache.pop(
                specialty_cache_key, None
            )
            cached_specialty = (
                existing if existing is not None else copy.deepcopy(response)
            )
            self._specialty_response_cache[
                specialty_cache_key
            ] = cached_specialty
            while (
                len(self._specialty_response_cache)
                > SPECIALTY_RESPONSE_CACHE_CAPACITY
            ):
                self._specialty_response_cache.popitem(last=False)
        return copy.deepcopy(cached_specialty)

    def _creature_specialties_v1(
        self,
        species_key: str,
        *,
        offset: int = 0,
        limit: int = 24,
    ) -> dict[str, Any]:
        """Rank exact node/resource pairs for one species without a persisted cross product."""

        if self.sqlite_catalog_path is not None:
            try:
                node_catalog = self._load_sqlite_catalog().catalog_for_specialties()
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        else:
            node_catalog = self._load_catalog()
        evaluation_catalog, engine = self._load_evaluation()
        evaluation_revision, _component_revision = self._evaluation_revisions(
            node_catalog,
            evaluation_catalog,
        )
        requested_key = " ".join(str(species_key or "").casefold().split())
        variants = [
            creature
            for creature in evaluation_catalog.get("creatures", [])
            if isinstance(creature, dict)
            and " ".join(
                str(
                    creature.get("speciesKey")
                    or creature.get("objectPath")
                    or creature.get("name")
                    or ""
                )
                .casefold()
                .split()
            )
            == requested_key
        ]
        if not variants:
            raise KeyError("HARVEST_SPECIES_NOT_FOUND")

        species_catalog = {
            **evaluation_catalog,
            "creatures": variants,
        }
        species_engine = HarvestEvaluationEngine(species_catalog)
        top_candidates, _variant_counts = _eligible_attack_candidates(
            evaluation_catalog
        )
        representatives: OrderedDict[
            tuple[str, str, int | None], tuple[str, str]
        ] = OrderedDict()
        occurrences: list[
            tuple[tuple[str, str, int | None], dict[str, Any], dict[str, Any]]
        ] = []
        nodes = node_catalog.get("nodes")
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            component_ref = node.get("harvestComponent")
            component_package = canonical_package_path(
                component_ref.get("packagePath")
                if isinstance(component_ref, dict)
                else ""
            )
            resources = node.get("resources", {}).get("items", [])
            for resource in resources if isinstance(resources, list) else []:
                if not isinstance(resource, dict):
                    continue
                raw_entry_index = resource.get("entryIndex")
                entry_index = (
                    int(raw_entry_index)
                    if isinstance(raw_entry_index, int)
                    and not isinstance(raw_entry_index, bool)
                    else None
                )
                key = (
                    component_package.casefold(),
                    str(resource.get("resource") or "").casefold(),
                    entry_index,
                )
                node_id = str(node.get("id") or "")
                node_resource_id = str(resource.get("nodeResourceId") or "")
                representatives.setdefault(key, (node_id, node_resource_id))
                occurrences.append((key, node, resource))

        selected_by_key: dict[tuple[str, str, int | None], dict[str, Any]] = {}
        top_by_key: dict[tuple[str, str, int | None], dict[str, Any]] = {}
        pair_dispositions: Counter[str] = Counter()
        usage_scope = str(
            evaluation_catalog.get("methodology", {}).get("usageScope") or ""
        )
        for key, (node_id, node_resource_id) in representatives.items():
            component_package, resource_name, resource_entry_index = key
            selected_result = self._creature_pair_result(
                species_engine,
                node_catalog,
                evaluation_revision=evaluation_revision,
                species_key=requested_key,
                component_package=component_package,
                resource=resource_name,
                resource_entry_index=resource_entry_index,
                usage_scope=usage_scope,
                node_id=node_id,
                node_resource_id=node_resource_id,
            )
            selected_row = selected_result.get("row")
            if not isinstance(selected_row, dict):
                pair_dispositions[str(selected_result.get("disposition") or "UNKNOWN")] += 1
                continue
            top_row = self._top_baseline(
                evaluation_catalog,
                engine,
                top_candidates,
                evaluation_revision=evaluation_revision,
                component_package=component_package,
                resource=str(selected_row.get("resource") or ""),
                resource_entry_index=resource_entry_index,
            )
            if top_row is None:
                pair_dispositions["NODE_TOP_NOT_AVAILABLE"] += 1
                continue
            selected_by_key[key] = selected_row
            top_by_key[key] = top_row
            pair_dispositions["RANKED"] += 1

        ranked_rows: list[dict[str, Any]] = []
        for key, node, resource in occurrences:
            selected_row = selected_by_key.get(key)
            top_row = top_by_key.get(key)
            if selected_row is None or top_row is None:
                continue
            selected_score = selected_row.get("estimatedYieldPerNode")
            top_score = top_row.get("estimatedYieldPerNode")
            if not isinstance(selected_score, (int, float)) or not isinstance(
                top_score, (int, float)
            ) or float(top_score) <= 0:
                continue
            relative_percent = round(
                min(100.0, max(0.0, float(selected_score) / float(top_score) * 100.0)),
                6,
            )
            component_ref = node.get("harvestComponent")
            component_package = canonical_package_path(
                component_ref.get("packagePath")
                if isinstance(component_ref, dict)
                else ""
            )
            ranked_rows.append(
                {
                    **_compact_specialty_row(selected_row),
                    "node": {
                        "id": node.get("id"),
                        "name": node.get("name"),
                        "objectPath": node.get("objectPath"),
                    },
                    "resource": {
                        **resource,
                        "harvestComponentPackagePath": component_package,
                    },
                    "nodeTopEstimatedYieldPerNode": float(top_score),
                    # Compatibility alias for one release.  It deliberately
                    # carries the same units/value as the new yield metric.
                    "nodeTopEngineComparisonIndex": float(top_score),
                    "relativeToNodeTopPercent": relative_percent,
                    "nodeTop": {
                        "speciesKey": top_row.get("speciesKey"),
                        "creature": top_row.get("creature"),
                        "creatureObjectPath": top_row.get("creatureObjectPath"),
                        "attackIndex": top_row.get("attackIndex"),
                        "attackName": top_row.get("attackName"),
                        "estimatedYieldPerNode": float(top_score),
                        "engineComparisonIndex": float(top_score),
                        "rankingTier": top_row.get("rankingTier"),
                        "evidence": copy.deepcopy(top_row.get("evidence") or {}),
                    },
                }
            )

        ranked_rows.sort(
            key=lambda row: (
                -float(row.get("estimatedYieldPerNode") or 0.0),
                -float(row.get("relativeToNodeTopPercent") or 0.0),
                str(row.get("resource", {}).get("resource") or "").casefold(),
                str(row.get("node", {}).get("name") or "").casefold(),
                str(row.get("node", {}).get("id") or ""),
            )
        )
        previous_score: float | None = None
        previous_rank = 0
        for ordinal, row in enumerate(ranked_rows, start=1):
            score = float(row.get("estimatedYieldPerNode") or 0.0)
            if previous_score is None or score != previous_score:
                previous_rank = ordinal
                previous_score = score
            row["rank"] = previous_rank
        bounded_offset = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 100))
        page_items = [
            copy.deepcopy(row)
            for row in ranked_rows[bounded_offset : bounded_offset + bounded_limit]
        ]

        evaluation_coverage = dict(evaluation_catalog.get("coverage") or {})
        claims_all_creatures = evaluation_coverage.get("claimsAllCreatures") is True
        claim_blockers = [
            str(value)
            for value in evaluation_catalog.get("claimBlockers", [])
            if str(value)
        ]
        representative = min(variants, key=_creature_representative_key)
        canonical_species_key = str(representative.get("speciesKey") or requested_key)
        total = len(ranked_rows)
        return {
            "schema": "blueprint-to-code.harvest-creature-specialties/v2",
            "dataset": {
                **dict(node_catalog.get("dataset") or {}),
                "evaluationRevision": evaluation_revision,
                "evaluationGeneratedAt": evaluation_catalog.get("dataset", {}).get(
                    "generatedAt"
                ),
            },
            "species": {
                "speciesKey": canonical_species_key,
                "name": representative.get("name"),
                "dinoNameTag": representative.get("dinoNameTag"),
                "variantCount": len(variants),
            },
            "methodology": {
                **dict(evaluation_catalog.get("methodology") or {}),
                "metric": "estimatedYieldPerNode",
                "sortMetric": "estimatedYieldPerNode",
                "relativeBasis": (
                    "SELECTED_SPECIES_ESTIMATED_YIELD_PER_NODE_DIVIDED_BY_"
                    "NODE_RESOURCE_TOP_ESTIMATED_YIELD_PER_NODE"
                ),
                "tiePolicy": (
                    "COMPETITION_RANK_FOR_EQUAL_ESTIMATED_YIELD_1_1_3"
                ),
                "scoreBasis": YIELD_SCORE_BASIS,
                "engineComparisonIndexCompatibility": (
                    "ALIAS_OF_ESTIMATED_YIELD_PER_NODE_NOT_USED_FOR_SELECTION"
                ),
            },
            "scopeStatus": (
                "ALL_DISCOVERED_CREATURES_EVALUATED"
                if claims_all_creatures
                else "PARTIAL_CREATURE_EVIDENCE"
            ),
            "claimsCompleteWithinScope": claims_all_creatures,
            "claimsGlobalTop": False,
            "claimBlockers": claim_blockers,
            "evidence": {
                "status": "COMPLETE" if claims_all_creatures else "PARTIAL",
                "blockers": claim_blockers,
            },
            "coverage": {
                **evaluation_coverage,
                "speciesVariantsMatched": len(variants),
                "nodeResourcePairsDiscovered": len(occurrences),
                "uniqueEvaluationPairs": len(representatives),
                "uniqueEvaluationPairsRanked": len(selected_by_key),
                "nodeResourcePairsRanked": total,
                "pairDispositionCounts": dict(sorted(pair_dispositions.items())),
                "returned": len(page_items),
                "omitted": max(0, total - len(page_items)),
            },
            "page": {
                "offset": bounded_offset,
                "limit": bounded_limit,
                "total": total,
                "returned": len(page_items),
                "omitted": max(0, total - bounded_offset - len(page_items)),
            },
            "items": page_items,
        }
