"""Canonical variant classification, audit, and deterministic ordering."""

from __future__ import annotations

from typing import Any

from ...resource_nodes import canonical_package_path
from .aggregation import _metric_value, _stable_row_identity
from .contracts import VARIANT_ALL, VARIANT_BEST_DISCOVERED_EXPLORATORY

_VARIANT_BASE = "BASE"
_VARIANT_MAP = "MAP_VARIANT"
_VARIANT_MISSION = "MISSION"
_VARIANT_BOSS = "BOSS"
_VARIANT_EVENT = "EVENT"
_VARIANT_TEST = "TEST"
_VARIANT_UNKNOWN = "UNKNOWN_VARIANT"
VARIANT_SELECTION_AUDIT_LIMIT = 10


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


def project_species_variants(
    *,
    variants: list[dict[str, Any]],
    variant_best_rows_by_tier: dict[str, list[dict[str, Any]]],
    variant_audit: dict[str, Any],
    variant_policy: str,
    metric: str,
) -> list[dict[str, Any]]:
    """Apply variant policy after each evidence tier has chosen its best attack."""

    canonical_path = variant_audit["canonicalObjectPath"]
    variant_paths = [
        str(creature.get("objectPath") or "") for creature in variants
    ]
    projected_rows: list[dict[str, Any]] = []
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
                "selectionReasons": list(variant_audit["selectionReasons"]),
                "excludedVariantClasses": list(
                    variant_audit["excludedVariantClasses"]
                ),
                "ambiguous": variant_audit["ambiguous"],
                "ambiguityReasons": list(variant_audit["ambiguityReasons"]),
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
            projected_rows.append(row)
    return projected_rows
