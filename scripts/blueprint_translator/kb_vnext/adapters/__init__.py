"""Versioned, evidence-validated semantic domain adapters."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from ..ontology import OntologyBundle
from .base import (
    ADAPTER_VERSION,
    AdapterSpec,
    LegacyTableSpec,
    LineageAnchorSpec,
    SemanticRule,
)
from .buffs import ADAPTER as BUFFS_ADAPTER
from .harvest import ADAPTER as HARVEST_ADAPTER
from .loot import ADAPTER as LOOT_ADAPTER
from .missions import ADAPTER as MISSIONS_ADAPTER
from .primal_game_data import ADAPTER as PRIMAL_GAME_DATA_ADAPTER
from .primal_items import ADAPTER as PRIMAL_ITEMS_ADAPTER
from .runner import AdapterSchemaError
from .runner import materialize_semantic_adapters as _materialize
from .status_components import ADAPTER as STATUS_COMPONENTS_ADAPTER


ADAPTER_SPECS: tuple[AdapterSpec, ...] = (
    PRIMAL_GAME_DATA_ADAPTER,
    BUFFS_ADAPTER,
    PRIMAL_ITEMS_ADAPTER,
    STATUS_COMPONENTS_ADAPTER,
    LOOT_ADAPTER,
    HARVEST_ADAPTER,
    MISSIONS_ADAPTER,
)


def materialize_semantic_adapters(
    *,
    core: sqlite3.Connection,
    legacy_root: Path,
    ontology: OntologyBundle,
    generated_at: str,
    adapter_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    """Run the checked-in adapter registry through the central resolver."""

    return _materialize(
        core=core,
        legacy_root=legacy_root,
        ontology=ontology,
        generated_at=generated_at,
        adapter_specs=ADAPTER_SPECS,
        adapter_ids=adapter_ids,
    )


__all__ = [
    "ADAPTER_SPECS",
    "ADAPTER_VERSION",
    "AdapterSchemaError",
    "AdapterSpec",
    "LegacyTableSpec",
    "LineageAnchorSpec",
    "SemanticRule",
    "materialize_semantic_adapters",
]
