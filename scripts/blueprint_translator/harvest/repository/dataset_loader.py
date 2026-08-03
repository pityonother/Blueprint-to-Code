"""Dataset loading and resource-node query boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...harvest_catalog_sqlite import (
    SQLiteHarvestCatalog,
    SQLiteHarvestCatalogInvalid,
)
from ...resource_nodes import query_resource_nodes


CREATURE_PAGE_SCHEMA = "blueprint-to-code.harvest-creature-page/v1"


class HarvestDatasetNotBuilt(FileNotFoundError):
    code = "HARVEST_DATASET_NOT_BUILT"


class HarvestDatasetInvalid(ValueError):
    code = "HARVEST_DATASET_INVALID"


class DatasetLoaderMixin:
    @staticmethod
    def _signature(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _read_object(path: Path, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt(f"{label} has not been generated.") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise HarvestDatasetInvalid(f"{label} cannot be read: {exc}") from exc
        if not isinstance(payload, dict):
            raise HarvestDatasetInvalid(f"{label} must contain a JSON object.")
        return payload

    def _load_catalog(self) -> dict[str, Any]:
        try:
            signature = self._signature(self.catalog_path)
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt("Resource-node catalog has not been generated.") from exc
        with self._lock:
            if self._catalog is None or signature != self._catalog_signature:
                payload = self._read_object(self.catalog_path, "Resource-node catalog")
                if payload.get("schema") != "ark-resource-node-catalog/v1" or not isinstance(
                    payload.get("nodes"), list
                ):
                    raise HarvestDatasetInvalid("Resource-node catalog schema is invalid.")
                self._catalog = payload
                self._catalog_signature = signature
            return self._catalog

    def _load_ranking(self) -> dict[str, Any]:
        try:
            signature = self._signature(self.ranking_path)
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt("Harvest ranking report has not been generated.") from exc
        with self._lock:
            if self._ranking is None or signature != self._ranking_signature:
                payload = self._read_object(self.ranking_path, "Harvest ranking report")
                if payload.get("schema") not in {
                    "ark-harvest-ranking/v1",
                    "ark-harvest-ranking/v2",
                } or not isinstance(
                    payload.get("bestRows"), list
                ):
                    raise HarvestDatasetInvalid("Harvest ranking report schema is invalid.")
                self._ranking = payload
                self._ranking_signature = signature
            return self._ranking

    def _load_sqlite_catalog(self) -> SQLiteHarvestCatalog:
        path = self.sqlite_catalog_path
        if path is None:
            raise HarvestDatasetInvalid("SQLite harvest catalog is not configured.")
        try:
            signature = self._signature(path)
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt(
                "SQLite resource-node catalog has not been generated."
            ) from exc
        try:
            source_signature = self._signature(self.catalog_path)
        except FileNotFoundError:
            source_signature = None
        with self._lock:
            database_changed = (
                self._sqlite_catalog is None or signature != self._sqlite_signature
            )
            source_changed = source_signature != self._sqlite_source_signature
            if database_changed or source_changed:
                reader = (
                    SQLiteHarvestCatalog(path)
                    if database_changed
                    else self._sqlite_catalog
                )
                if reader is None:
                    raise HarvestDatasetInvalid(
                        "SQLite resource-node catalog reader is unavailable."
                    )
                try:
                    reader.dataset()
                    if source_signature is not None:
                        reader.assert_matches_source(self.catalog_path)
                except FileNotFoundError as exc:
                    raise HarvestDatasetNotBuilt(
                        "SQLite resource-node catalog has not been generated."
                    ) from exc
                except SQLiteHarvestCatalogInvalid as exc:
                    raise HarvestDatasetInvalid(str(exc)) from exc
                self._sqlite_catalog = reader
                self._sqlite_signature = signature
                self._sqlite_source_signature = source_signature
                self._lazy_ranking_cache.clear()
                self._top_baseline_cache.clear()
                self._creature_pair_cache.clear()
                self._v2_tier_baseline_cache.clear()
                self._specialty_response_cache.clear()
            return self._sqlite_catalog

    def _catalog_for_node(self, node_id: str) -> dict[str, Any]:
        if self.sqlite_catalog_path is not None:
            try:
                return self._load_sqlite_catalog().catalog_for_node(node_id)
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        return self._load_catalog()

    def list_nodes(
        self,
        *,
        q: str = "",
        map_name: str = "",
        only_map_family: str = "",
        resource: str = "",
        offset: int = 0,
        limit: int = 24,
    ) -> dict[str, Any]:
        if self.sqlite_catalog_path is not None:
            try:
                return self._load_sqlite_catalog().list_nodes(
                    q=q,
                    map_name=map_name,
                    only_map_family=only_map_family,
                    resource=resource,
                    offset=offset,
                    limit=limit,
                )
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        return query_resource_nodes(
            self._load_catalog(),
            q=q,
            map_name=map_name,
            only_map_family=only_map_family,
            resource=resource,
            offset=offset,
            limit=limit,
        )

    def get_node(self, node_id: str) -> dict[str, Any]:
        if self.sqlite_catalog_path is not None:
            try:
                return self._load_sqlite_catalog().get_node(node_id)
            except SQLiteHarvestCatalogInvalid as exc:
                raise HarvestDatasetInvalid(str(exc)) from exc
        catalog = self._load_catalog()
        nodes = catalog.get("nodes")
        for node in nodes if isinstance(nodes, list) else []:
            if isinstance(node, dict) and str(node.get("id") or "") == node_id:
                return dict(node)
        raise KeyError("RESOURCE_NODE_NOT_FOUND")
