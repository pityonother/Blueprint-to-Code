"""Compact SQLite persistence and indexed reads for ARK resource-node catalogs.

The generated JSON remains the portable, human-inspectable interchange artifact.
This module builds a read-optimized companion database so the live API does not
need to deserialize and retain the complete node catalog in memory.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Iterator

from .resource_nodes import (
    NODE_PAGE_MAX_LIMIT,
    NODE_PAGE_SCHEMA,
    _node_page_coverage,
    _node_page_preview,
)


SQLITE_CATALOG_SCHEMA = "ark-harvest-sqlite/v1"
SOURCE_CATALOG_SCHEMA = "ark-resource-node-catalog/v1"
SQLITE_USER_VERSION = 1


class SQLiteHarvestCatalogInvalid(ValueError):
    """The persisted SQLite artifact is unreadable or has an unknown schema."""


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_object(value: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SQLiteHarvestCatalogInvalid(
            f"SQLite harvest catalog {label} is invalid: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise SQLiteHarvestCatalogInvalid(
            f"SQLite harvest catalog {label} must be an object."
        )
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_dicts(value: object) -> Iterator[dict[str, Any]]:
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            yield item


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE node_index (
            node_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            object_path TEXT NOT NULL,
            sort_name TEXT NOT NULL,
            search_text TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE node_payload (
            node_id TEXT PRIMARY KEY,
            detail_json TEXT NOT NULL,
            preview_json TEXT NOT NULL,
            FOREIGN KEY (node_id) REFERENCES node_index(node_id) ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE TABLE resource_evidence (
            node_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            node_resource_id TEXT NOT NULL,
            resource TEXT NOT NULL,
            resource_fold TEXT NOT NULL,
            entry_index INTEGER,
            evidence_status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (node_id, ordinal),
            FOREIGN KEY (node_id) REFERENCES node_index(node_id) ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE TABLE map_evidence (
            node_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            map_id TEXT NOT NULL,
            name TEXT NOT NULL,
            object_path TEXT NOT NULL,
            map_family TEXT NOT NULL,
            map_family_fold TEXT NOT NULL,
            relation TEXT NOT NULL,
            evidence_status TEXT NOT NULL,
            usage_status TEXT NOT NULL,
            search_text TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (node_id, ordinal),
            FOREIGN KEY (node_id) REFERENCES node_index(node_id) ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE INDEX idx_node_index_sort
            ON node_index(sort_name, node_id);
        CREATE INDEX idx_node_index_object_path
            ON node_index(object_path);
        CREATE INDEX idx_resource_evidence_resource
            ON resource_evidence(resource_fold, node_id);
        CREATE INDEX idx_resource_evidence_identity
            ON resource_evidence(node_resource_id, node_id);
        CREATE INDEX idx_map_evidence_family
            ON map_evidence(map_family_fold, node_id);
        CREATE INDEX idx_map_evidence_node
            ON map_evidence(node_id, ordinal);
        """
    )
    connection.execute(f"PRAGMA user_version = {SQLITE_USER_VERSION}")


def _metadata_payload(
    catalog: dict[str, Any],
    *,
    source_path: Path | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "sqliteSchema": SQLITE_CATALOG_SCHEMA,
        "sourceCatalogSchema": catalog.get("schema"),
        "dataset": catalog.get("dataset") or {},
        "coverage": catalog.get("coverage") or {},
        "pageCoverage": _node_page_coverage(catalog.get("coverage")),
        "failures": catalog.get("failures") or [],
        "skipped": catalog.get("skipped") or {},
    }
    if source_path is not None:
        source = source_path.resolve()
        stat = source.stat()
        metadata["sourceJson"] = {
            "path": str(source),
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "sha256": _sha256_file(source),
        }
    return metadata


def build_harvest_catalog_sqlite(
    catalog: dict[str, Any],
    destination: Path,
    *,
    source_path: Path | None = None,
) -> dict[str, Any]:
    """Atomically persist a validated resource-node catalog to SQLite."""

    if catalog.get("schema") != SOURCE_CATALOG_SCHEMA:
        raise ValueError("Resource-node catalog schema is invalid.")
    nodes = catalog.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("Resource-node catalog nodes must be a list.")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.next"
    )
    node_count = 0
    resource_count = 0
    map_count = 0
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            with connection:
                connection.execute("PRAGMA journal_mode = OFF")
                connection.execute("PRAGMA synchronous = OFF")
                connection.execute("PRAGMA temp_store = MEMORY")
                connection.execute("PRAGMA foreign_keys = ON")
                _create_schema(connection)
                metadata = _metadata_payload(catalog, source_path=source_path)
                connection.executemany(
                    "INSERT INTO metadata(key, value_json) VALUES (?, ?)",
                    ((key, _compact_json(value)) for key, value in metadata.items()),
                )

                for node in _iter_dicts(nodes):
                    node_id = str(node.get("id") or "")
                    if not node_id:
                        raise ValueError("Resource-node catalog contains a node without an id.")
                    name = str(node.get("name") or "")
                    object_path = str(node.get("objectPath") or "")
                    mesh = node.get("mesh")
                    mesh_name = (
                        str(mesh.get("name") or "") if isinstance(mesh, dict) else ""
                    )
                    connection.execute(
                        """
                        INSERT INTO node_index(
                            node_id, name, object_path, sort_name, search_text
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            node_id,
                            name,
                            object_path,
                            name.casefold(),
                            " ".join((name, object_path, mesh_name)).casefold(),
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO node_payload(node_id, detail_json, preview_json)
                        VALUES (?, ?, ?)
                        """,
                        (
                            node_id,
                            _compact_json(node),
                            _compact_json(_node_page_preview(node)),
                        ),
                    )
                    node_count += 1

                    resources = node.get("resources")
                    resource_items = (
                        resources.get("items") if isinstance(resources, dict) else []
                    )
                    for ordinal, resource in enumerate(_iter_dicts(resource_items)):
                        resource_name = str(resource.get("resource") or "")
                        entry_index = resource.get("entryIndex")
                        connection.execute(
                            """
                            INSERT INTO resource_evidence(
                                node_id, ordinal, node_resource_id, resource,
                                resource_fold, entry_index, evidence_status, payload_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                node_id,
                                ordinal,
                                str(resource.get("nodeResourceId") or ""),
                                resource_name,
                                resource_name.casefold(),
                                (
                                    int(entry_index)
                                    if isinstance(entry_index, int)
                                    and not isinstance(entry_index, bool)
                                    else None
                                ),
                                str(resource.get("evidenceStatus") or ""),
                                _compact_json(resource),
                            ),
                        )
                        resource_count += 1

                    references = node.get("mapReferences")
                    map_items = (
                        references.get("items") if isinstance(references, dict) else []
                    )
                    for ordinal, map_item in enumerate(_iter_dicts(map_items)):
                        map_name = str(map_item.get("name") or "")
                        map_path = str(map_item.get("objectPath") or "")
                        map_family = str(map_item.get("mapFamily") or "")
                        connection.execute(
                            """
                            INSERT INTO map_evidence(
                                node_id, ordinal, map_id, name, object_path,
                                map_family, map_family_fold, relation, evidence_status,
                                usage_status, search_text, payload_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                node_id,
                                ordinal,
                                str(map_item.get("id") or ""),
                                map_name,
                                map_path,
                                map_family,
                                map_family.casefold(),
                                str(map_item.get("relation") or ""),
                                str(map_item.get("evidenceStatus") or ""),
                                str(map_item.get("usageStatus") or ""),
                                " ".join((map_name, map_path)).casefold(),
                                _compact_json(map_item),
                            ),
                        )
                        map_count += 1

                check = connection.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise ValueError("Generated SQLite harvest catalog failed integrity_check.")
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "schema": SQLITE_CATALOG_SCHEMA,
        "path": str(destination.resolve()),
        "sizeBytes": destination.stat().st_size,
        "nodes": node_count,
        "resources": resource_count,
        "mapEvidence": map_count,
        "datasetRevision": str((catalog.get("dataset") or {}).get("revision") or ""),
    }


def convert_resource_node_catalog(
    source: Path,
    destination: Path,
) -> dict[str, Any]:
    """Convert the canonical JSON artifact to its SQLite read companion."""

    source = Path(source)
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Resource-node catalog cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Resource-node catalog must contain a JSON object.")
    return build_harvest_catalog_sqlite(payload, destination, source_path=source)


class SQLiteHarvestCatalog:
    """Read-only indexed view over a generated SQLite node catalog."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._metadata_cache: dict[str, Any] | None = None
        self._signature: tuple[int, int] | None = None

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _metadata(self) -> dict[str, Any]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            raise
        signature = (stat.st_mtime_ns, stat.st_size)
        if self._metadata_cache is not None and signature == self._signature:
            return self._metadata_cache
        try:
            with closing(self._connect()) as connection:
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                rows = connection.execute(
                    "SELECT key, value_json FROM metadata"
                ).fetchall()
        except sqlite3.Error as exc:
            raise SQLiteHarvestCatalogInvalid(
                f"SQLite harvest catalog cannot be read: {exc}"
            ) from exc
        try:
            metadata = {str(row["key"]): json.loads(row["value_json"]) for row in rows}
        except (TypeError, json.JSONDecodeError) as exc:
            raise SQLiteHarvestCatalogInvalid(
                f"SQLite harvest catalog metadata is invalid: {exc}"
            ) from exc
        if (
            user_version != SQLITE_USER_VERSION
            or metadata.get("sqliteSchema") != SQLITE_CATALOG_SCHEMA
        ):
            raise SQLiteHarvestCatalogInvalid(
                "SQLite harvest catalog schema is invalid."
            )
        if metadata.get("sourceCatalogSchema") != SOURCE_CATALOG_SCHEMA:
            raise SQLiteHarvestCatalogInvalid(
                "SQLite resource-node source schema is invalid."
            )
        if not isinstance(metadata.get("dataset"), dict) or not isinstance(
            metadata.get("pageCoverage"), dict
        ):
            raise SQLiteHarvestCatalogInvalid(
                "SQLite harvest catalog metadata is incomplete."
            )
        self._metadata_cache = metadata
        self._signature = signature
        return metadata

    def dataset(self) -> dict[str, Any]:
        return copy.deepcopy(self._metadata()["dataset"])

    def coverage(self) -> dict[str, Any]:
        coverage = self._metadata().get("coverage")
        return copy.deepcopy(coverage if isinstance(coverage, dict) else {})

    def assert_matches_source(self, source: Path) -> None:
        """Fail closed when a recorded canonical JSON artifact has changed."""

        source_metadata = self._metadata().get("sourceJson")
        if not isinstance(source_metadata, dict):
            return
        expected_hash = str(source_metadata.get("sha256") or "")
        if len(expected_hash) != 64:
            raise SQLiteHarvestCatalogInvalid(
                "SQLite harvest catalog source identity is invalid."
            )
        source = Path(source)
        try:
            actual_hash = _sha256_file(source)
        except OSError as exc:
            raise SQLiteHarvestCatalogInvalid(
                f"Canonical resource-node JSON cannot be verified: {exc}"
            ) from exc
        if actual_hash != expected_hash:
            raise SQLiteHarvestCatalogInvalid(
                "SQLite harvest catalog does not match the canonical resource-node JSON."
            )

    def list_nodes(
        self,
        *,
        q: str = "",
        map_name: str = "",
        resource: str = "",
        offset: int = 0,
        limit: int = 24,
    ) -> dict[str, Any]:
        if len(q) > 100:
            raise ValueError("q must be at most 100 characters")
        q = q.strip()
        map_name = map_name.strip()
        resource = resource.strip()
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), NODE_PAGE_MAX_LIMIT))

        conditions: list[str] = []
        parameters: list[object] = []
        if q:
            conditions.append("instr(n.search_text, ?) > 0")
            parameters.append(q.casefold())
        if map_name:
            conditions.append(
                "EXISTS (SELECT 1 FROM map_evidence m "
                "WHERE m.node_id = n.node_id AND instr(m.search_text, ?) > 0)"
            )
            parameters.append(map_name.casefold())
        if resource:
            conditions.append(
                "EXISTS (SELECT 1 FROM resource_evidence r "
                "WHERE r.node_id = n.node_id AND r.resource_fold = ?)"
            )
            parameters.append(resource.casefold())
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        try:
            self._metadata()
            with closing(self._connect()) as connection:
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM node_index n{where}",
                        parameters,
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    f"SELECT p.preview_json FROM node_index n "
                    f"JOIN node_payload p ON p.node_id = n.node_id{where} "
                    "ORDER BY n.sort_name, n.node_id LIMIT ? OFFSET ?",
                    [*parameters, limit, offset],
                ).fetchall()
        except sqlite3.Error as exc:
            raise SQLiteHarvestCatalogInvalid(
                f"SQLite harvest node query failed: {exc}"
            ) from exc
        items = [_decode_object(row["preview_json"], "node preview") for row in rows]
        next_offset = offset + len(items) if offset + len(items) < total else None
        metadata = self._metadata()
        return {
            "schema": NODE_PAGE_SCHEMA,
            "dataset": copy.deepcopy(metadata["dataset"]),
            "coverage": copy.deepcopy(metadata["pageCoverage"]),
            "total": total,
            "offset": offset,
            "limit": limit,
            "nextOffset": next_offset,
            "items": items,
        }

    def get_node(self, node_id: str) -> dict[str, Any]:
        self._metadata()
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT detail_json FROM node_payload WHERE node_id = ?",
                    (node_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise SQLiteHarvestCatalogInvalid(
                f"SQLite harvest node detail query failed: {exc}"
            ) from exc
        if row is None:
            raise KeyError("RESOURCE_NODE_NOT_FOUND")
        return _decode_object(row["detail_json"], "node detail")

    def catalog_for_node(self, node_id: str) -> dict[str, Any]:
        """Return the minimal legacy-compatible payload needed for one ranking."""

        metadata = self._metadata()
        return {
            "schema": SOURCE_CATALOG_SCHEMA,
            "dataset": copy.deepcopy(metadata["dataset"]),
            "coverage": copy.deepcopy(metadata.get("coverage") or {}),
            "nodes": [self.get_node(node_id)],
        }

    def catalog_for_specialties(self) -> dict[str, Any]:
        """Project only fields required by the reverse creature query.

        Map evidence, meshes, images, and other display-only node fields remain
        inside SQLite.  This deliberately avoids deserializing the canonical
        resource-node JSON (or returning every full ``detail_json`` payload)
        when one creature's specialties are requested.
        """

        metadata = self._metadata()
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT
                        n.node_id,
                        n.name,
                        n.object_path,
                        json_extract(p.detail_json, '$.harvestComponent')
                            AS harvest_component_json,
                        json_extract(p.detail_json, '$.resources')
                            AS resources_json
                    FROM node_index n
                    JOIN node_payload p ON p.node_id = n.node_id
                    ORDER BY n.sort_name, n.node_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise SQLiteHarvestCatalogInvalid(
                f"SQLite harvest specialty projection failed: {exc}"
            ) from exc

        nodes: list[dict[str, Any]] = []
        try:
            for row in rows:
                harvest_component = (
                    json.loads(row["harvest_component_json"])
                    if row["harvest_component_json"] is not None
                    else {}
                )
                resources = (
                    json.loads(row["resources_json"])
                    if row["resources_json"] is not None
                    else {}
                )
                if not isinstance(harvest_component, dict) or not isinstance(
                    resources, dict
                ):
                    raise TypeError("projected node fields must be objects")
                nodes.append(
                    {
                        "id": str(row["node_id"]),
                        "name": str(row["name"]),
                        "objectPath": str(row["object_path"]),
                        "harvestComponent": harvest_component,
                        "resources": resources,
                    }
                )
        except (TypeError, json.JSONDecodeError) as exc:
            raise SQLiteHarvestCatalogInvalid(
                f"SQLite harvest specialty fields are invalid: {exc}"
            ) from exc

        return {
            "schema": SOURCE_CATALOG_SCHEMA,
            "dataset": copy.deepcopy(metadata["dataset"]),
            "coverage": copy.deepcopy(metadata.get("coverage") or {}),
            "nodes": nodes,
        }
