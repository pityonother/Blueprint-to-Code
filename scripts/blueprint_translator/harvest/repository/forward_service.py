"""Forward ranking orchestration and legacy ranking fallback."""

from __future__ import annotations

import copy
from typing import Any

from ..contracts import YIELD_MODEL_VERSION
from ..evaluation import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    HARVEST_RANKING_POLICY_VERSION,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    RANKING_RESULT_SCHEMA,
    VARIANT_CANONICAL,
    HarvestEvaluationEngine,
    find_node_and_resource,
)
from ..evaluation.contracts import METRIC_CONTRACTS
from ..evaluation.specialties import _best_discovered_scope_row
from ...resource_nodes import canonical_package_path, rank_node_resource
from .caches import (
    CREATURE_PAIR_CACHE_CAPACITY,
    LAZY_CACHE_CAPACITY,
    TOP_BASELINE_CACHE_CAPACITY,
)
from .dataset_loader import HarvestDatasetInvalid


class ForwardRankingMixin:
    @staticmethod
    def _bind_lazy_result(
        cached: dict[str, Any],
        *,
        node_catalog: dict[str, Any],
        node: dict[str, Any],
        resource: dict[str, Any],
        component_package: str,
        limit: int,
    ) -> dict[str, Any]:
        result = copy.deepcopy(cached)
        result["dataset"] = {
            **dict(node_catalog.get("dataset") or {}),
            **{
                key: value
                for key, value in dict(cached.get("dataset") or {}).items()
                if key in {"evaluationRevision", "evaluationGeneratedAt"}
            },
        }
        result["node"] = {
            "id": node.get("id"),
            "name": node.get("name"),
            "objectPath": node.get("objectPath"),
        }
        result["resource"] = {
            **resource,
            "harvestComponentPackagePath": component_package,
        }
        bounded_limit = max(1, min(int(limit), 10))
        confirmed_items = [
            dict(row) for row in result.get("confirmedItems", [])[:bounded_limit]
        ]
        conditional_items = [
            dict(row) for row in result.get("conditionalItems", [])[:bounded_limit]
        ]
        if "confirmedItems" in result or "conditionalItems" in result:
            result["confirmedItems"] = confirmed_items
            result["conditionalItems"] = conditional_items
            items = list(confirmed_items)
        else:
            items = [dict(row) for row in result.get("items", [])[:bounded_limit]]
        result["items"] = items
        coverage = dict(result.get("coverage") or {})
        if "confirmedItems" in result or "conditionalItems" in result:
            confirmed_total = int(
                coverage.get("rankedSpeciesConfirmed") or len(confirmed_items)
            )
            conditional_total = int(
                coverage.get("rankedSpeciesConditional") or len(conditional_items)
            )
            coverage["returnedConfirmed"] = len(confirmed_items)
            coverage["returnedConditional"] = len(conditional_items)
            coverage["omittedConfirmed"] = max(
                0, confirmed_total - len(confirmed_items)
            )
            coverage["omittedConditional"] = max(
                0, conditional_total - len(conditional_items)
            )
            ranked_total = confirmed_total
        else:
            ranked_total = int(coverage.get("rankedForNodeResource") or len(items))
        coverage["returned"] = len(items)
        coverage["omitted"] = max(0, ranked_total - len(items))
        result["coverage"] = coverage
        return result

    def _lazy_rankings(
        self,
        node_catalog: dict[str, Any],
        evaluation_catalog: dict[str, Any],
        engine: HarvestEvaluationEngine,
        *,
        node_id: str,
        node_resource_id: str,
        limit: int,
        evidence_policy: str,
        variant_policy: str,
        metric: str,
        availability_policy: str,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
    ) -> dict[str, Any]:
        evaluation_revision, _component_revision = self._evaluation_revisions(
            node_catalog,
            evaluation_catalog,
        )
        node, resource = find_node_and_resource(
            node_catalog,
            node_id,
            node_resource_id,
        )
        component_ref = node.get("harvestComponent")
        component_package = canonical_package_path(
            component_ref.get("packagePath")
            if isinstance(component_ref, dict)
            else ""
        )
        resource_class = str(resource.get("resource") or "")
        raw_entry_index = resource.get("entryIndex")
        resource_entry_index = (
            int(raw_entry_index)
            if isinstance(raw_entry_index, int) and not isinstance(raw_entry_index, bool)
            else None
        )
        usage_scope = str(
            evaluation_catalog.get("methodology", {}).get("usageScope") or ""
        )
        evaluation_dataset = dict(evaluation_catalog.get("dataset") or {})
        node_dataset = dict(node_catalog.get("dataset") or {})
        runtime_index = self._load_runtime_observations(
            self._runtime_identity(node_catalog, evaluation_catalog),
            runtime_profile_id=runtime_profile_id,
            include_preliminary=include_preliminary,
            allow_unselected_profiles=not bool(
                METRIC_CONTRACTS.get(metric, {}).get("runtime")
            ),
        )
        runtime_node_identity: tuple[str, ...] = (
            (node_id, node_resource_id)
            if bool(METRIC_CONTRACTS.get(metric, {}).get("runtime"))
            else ()
        )
        cache_key = (
            str(evaluation_dataset.get("extractorVersion") or ""),
            YIELD_MODEL_VERSION,
            HARVEST_RANKING_POLICY_VERSION,
            RANKING_RESULT_SCHEMA,
            str(node_dataset.get("revision") or ""),
            evaluation_revision,
            str(evaluation_dataset.get("componentDatasetRevision") or ""),
            component_package.casefold(),
            resource_class.casefold(),
            resource_entry_index,
            *runtime_node_identity,
            usage_scope,
            evidence_policy,
            variant_policy,
            metric,
            availability_policy,
            runtime_index.revision,
            runtime_index.runtime_profile_selected,
            bool(include_preliminary),
        )
        with self._lock:
            cached = self._lazy_ranking_cache.pop(cache_key, None)
            if cached is not None:
                self._lazy_ranking_cache[cache_key] = cached
        if cached is None:
            computed = engine.rank_node_resource(
                node_catalog,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=10,
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
            identity = dict(computed.get("identity") or {})
            identity["runtimeObservationRevision"] = runtime_index.revision
            computed["identity"] = identity
            computed["runtimeCoverage"] = {
                "filesScanned": runtime_index.files_scanned,
                **runtime_index.coverage,
            }
            with self._lock:
                existing = self._lazy_ranking_cache.pop(cache_key, None)
                cached = existing if existing is not None else copy.deepcopy(computed)
                self._lazy_ranking_cache[cache_key] = cached
                while len(self._lazy_ranking_cache) > LAZY_CACHE_CAPACITY:
                    self._lazy_ranking_cache.popitem(last=False)
        return self._bind_lazy_result(
            cached,
            node_catalog=node_catalog,
            node=node,
            resource=resource,
            component_package=component_package,
            limit=limit,
        )

    def _top_baseline(
        self,
        evaluation_catalog: dict[str, Any],
        engine: HarvestEvaluationEngine,
        candidates: list[dict[str, Any]],
        *,
        evaluation_revision: str,
        component_package: str,
        resource: str,
        resource_entry_index: int | None,
    ) -> dict[str, Any] | None:
        usage_scope = str(
            evaluation_catalog.get("methodology", {}).get("usageScope") or ""
        )
        cache_key = (
            evaluation_revision,
            component_package.casefold(),
            resource.casefold(),
            resource_entry_index,
            usage_scope,
        )
        with self._lock:
            cached = self._top_baseline_cache.pop(cache_key, None)
            if cached is not None:
                self._top_baseline_cache[cache_key] = cached
        if cached is None:
            row = _best_discovered_scope_row(
                engine,
                component_package=component_package,
                resource=resource,
                resource_entry_index=resource_entry_index,
                candidates=candidates,
            )
            computed = {"row": copy.deepcopy(row)}
            with self._lock:
                existing = self._top_baseline_cache.pop(cache_key, None)
                cached = existing if existing is not None else computed
                self._top_baseline_cache[cache_key] = cached
                while len(self._top_baseline_cache) > TOP_BASELINE_CACHE_CAPACITY:
                    self._top_baseline_cache.popitem(last=False)
        row = cached.get("row")
        return copy.deepcopy(row) if isinstance(row, dict) else None

    def _creature_pair_result(
        self,
        engine: HarvestEvaluationEngine,
        node_catalog: dict[str, Any],
        *,
        evaluation_revision: str,
        species_key: str,
        component_package: str,
        resource: str,
        resource_entry_index: int | None,
        usage_scope: str,
        node_id: str,
        node_resource_id: str,
    ) -> dict[str, Any]:
        cache_key = (
            evaluation_revision,
            species_key.casefold(),
            component_package.casefold(),
            resource.casefold(),
            resource_entry_index,
            usage_scope,
        )
        with self._lock:
            cached = self._creature_pair_cache.pop(cache_key, None)
            if cached is not None:
                self._creature_pair_cache[cache_key] = cached
        if cached is None:
            try:
                result = engine.rank_node_resource(
                    node_catalog,
                    node_id=node_id,
                    node_resource_id=node_resource_id,
                    limit=1,
                )
            except KeyError as exc:
                computed = {
                    "row": None,
                    "disposition": str(exc.args[0] if exc.args else exc),
                }
            else:
                items = result.get("items")
                row = (
                    dict(items[0])
                    if isinstance(items, list) and items and isinstance(items[0], dict)
                    else None
                )
                computed = {
                    "row": row,
                    "disposition": "RANKED" if row is not None else "NOT_RANKED_FOR_SPECIES",
                }
            with self._lock:
                existing = self._creature_pair_cache.pop(cache_key, None)
                cached = existing if existing is not None else computed
                self._creature_pair_cache[cache_key] = cached
                while len(self._creature_pair_cache) > CREATURE_PAIR_CACHE_CAPACITY:
                    self._creature_pair_cache.popitem(last=False)
        return copy.deepcopy(cached)

    def rankings(
        self,
        node_id: str,
        node_resource_id: str,
        *,
        limit: int = 10,
        evidence_policy: str = POLICY_CONFIRMED,
        variant_policy: str = VARIANT_CANONICAL,
        metric: str = METRIC_STATIC_TOTAL,
        availability_policy: str = AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
        runtime_profile_id: str | None = None,
        include_preliminary: bool = False,
    ) -> dict[str, Any]:
        catalog = self._catalog_for_node(node_id)
        if self.evaluation_catalog_path is not None:
            evaluation, engine = self._load_evaluation()
            return self._lazy_rankings(
                catalog,
                evaluation,
                engine,
                node_id=node_id,
                node_resource_id=node_resource_id,
                limit=limit,
                evidence_policy=evidence_policy,
                variant_policy=variant_policy,
                metric=metric,
                availability_policy=availability_policy,
                runtime_profile_id=runtime_profile_id,
                include_preliminary=include_preliminary,
            )
        ranking = self._load_ranking()
        dataset = catalog.get("dataset")
        expected_revision = (
            str(
                dataset.get("rankingDatasetRevision")
                or dataset.get("rankingScanManifestHash")
                or ""
            )
            if isinstance(dataset, dict)
            else ""
        )
        actual_revision = str(
            ranking.get("datasetRevision") or ranking.get("scanManifestHash") or ""
        )
        if expected_revision and expected_revision != actual_revision:
            raise HarvestDatasetInvalid(
                "Resource-node catalog and ranking report revisions do not match."
            )
        return rank_node_resource(
            catalog,
            ranking,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=limit,
        )
