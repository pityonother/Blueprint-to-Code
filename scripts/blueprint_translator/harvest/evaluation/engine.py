"""Lazy all-creature evaluation and Ranking Contract v2 projection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from ...resource_nodes import canonical_package_path
from ..model.attack_evaluation import evaluate_attack_resource
from .aggregation import _override_map
from .contracts import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    EVALUATION_CATALOG_SCHEMA,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    VARIANT_CANONICAL,
)
from .legacy import rank_node_resource_v1
from .result_projection import rank_node_resource_v2
from .variant_selection import _canonical_variant_audit


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
        return rank_node_resource_v1(
            self,
            node_catalog,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=limit,
            evaluate_attack=evaluate_attack_resource,
        )

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
        return rank_node_resource_v2(
            self,
            node_catalog,
            node_id=node_id,
            node_resource_id=node_resource_id,
            limit=limit,
            evidence_policy=evidence_policy,
            variant_policy=variant_policy,
            metric=metric,
            availability_policy=availability_policy,
            runtime_observations=runtime_observations,
            runtime_profile_id=runtime_profile_id,
            include_preliminary=include_preliminary,
            runtime_profiles_available=runtime_profiles_available,
            evaluate_attack=evaluate_attack_resource,
        )
