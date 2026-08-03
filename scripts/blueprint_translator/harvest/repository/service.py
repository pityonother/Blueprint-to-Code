"""Thin composition facade for harvest repository services."""

from __future__ import annotations

from pathlib import Path

from .caches import initialize_repository_state
from .creature_service import CreatureServiceMixin
from .dataset_loader import (
    CREATURE_PAGE_SCHEMA,
    DatasetLoaderMixin,
    HarvestDatasetInvalid,
    HarvestDatasetNotBuilt,
)
from .forward_service import ForwardRankingMixin
from .revision_binding import RevisionBindingMixin
from .runtime_overlay import RuntimeOverlayMixin
from .specialty_service import CREATURE_SPECIALTIES_SCHEMA, SpecialtyServiceMixin


class HarvestNodeRepository(
    DatasetLoaderMixin,
    RevisionBindingMixin,
    RuntimeOverlayMixin,
    ForwardRankingMixin,
    CreatureServiceMixin,
    SpecialtyServiceMixin,
):
    """Compose loading, ranking, and projection services behind one stable API."""

    def __init__(
        self,
        catalog_path: Path,
        ranking_path: Path,
        evaluation_catalog_path: Path | None = None,
        sqlite_catalog_path: Path | None = None,
        runtime_observation_root: Path | None = None,
    ):
        self.catalog_path = Path(catalog_path)
        self.ranking_path = Path(ranking_path)
        self.evaluation_catalog_path = (
            Path(evaluation_catalog_path)
            if evaluation_catalog_path is not None
            else None
        )
        self.sqlite_catalog_path = (
            Path(sqlite_catalog_path) if sqlite_catalog_path is not None else None
        )
        self.runtime_observation_root = (
            Path(runtime_observation_root)
            if runtime_observation_root is not None
            else None
        )
        initialize_repository_state(self)


__all__ = [
    "CREATURE_PAGE_SCHEMA",
    "CREATURE_SPECIALTIES_SCHEMA",
    "HarvestDatasetInvalid",
    "HarvestDatasetNotBuilt",
    "HarvestNodeRepository",
]
