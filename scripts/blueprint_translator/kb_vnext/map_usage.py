"""Evidence-backed typed map-usage relationships.

Map package identity and domain membership are deliberately not map-usage
evidence.  Direct edges come only from fresh Asset Registry package
dependencies whose source package uniquely resolves to a map.  PCG and World
Partition edges come from the revision-validated resource-node catalog and
retain that source's usage status without promotion.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping


MAP_USAGE_EXTRACTOR_VERSION = "ark-kb-map-usage/v1"
RESOURCE_NODE_CATALOG_SCHEMA = "ark-resource-node-catalog/v1"

MAP_DIRECT_REFERENCE = "MAP_DIRECT_REFERENCE"
MAP_PCG_DEPENDENCY = "MAP_PCG_DEPENDENCY"
MAP_WORLD_PARTITION_REFERENCE = "MAP_WORLD_PARTITION_REFERENCE"
MAP_USAGE_EDGE_TYPES = (
    MAP_DIRECT_REFERENCE,
    MAP_PCG_DEPENDENCY,
    MAP_WORLD_PARTITION_REFERENCE,
)

_CONFIRMED_STATUSES = frozenset({"CONFIRMED", "VERIFIED", "RESOLVED"})
_CONFIRMED_CONFIDENCE = frozenset({"HIGH", "CONFIRMED"})
_KNOWN_USAGE_STATUSES = frozenset(
    {
        *_CONFIRMED_STATUSES,
        "CANDIDATE",
        "AMBIGUOUS",
        "LEGACY_UNVERIFIED",
        "STALE",
        "NOT_RECOVERED",
        "SOURCE_NOT_AVAILABLE",
    }
)
_IDENTITY_STATUSES = frozenset(
    {"EXTRACTED", "CONFIRMED", "VERIFIED", "RESOLVED"}
)
_UNRECOVERED_EVIDENCE_IDS = frozenset(
    {
        "UNKNOWN",
        "NOT_RECOVERED",
        "SOURCE_NOT_AVAILABLE",
        "UNRESOLVED",
    }
)
_UNRECOVERED_SOURCE_REVISION_VALUES = frozenset(
    {
        *_UNRECOVERED_EVIDENCE_IDS,
        "AMBIGUOUS",
        "LEGACY_UNVERIFIED",
        "CONFIRMED_FINGERPRINT_ONLY",
        "NOT_AVAILABLE",
        "UNAVAILABLE",
    }
)
_DIRECT_MAP_EVIDENCE_PREFIXES = (
    "registry-reference://",
    "map-evidence://asset-registry/",
)
_MAP_EVIDENCE_PREFIXES = (
    *_DIRECT_MAP_EVIDENCE_PREFIXES,
    "map-evidence://resource-node-catalog/",
)
_CATALOG_RELATIONS = {
    "PCG_BIOME_REFERENCE": (
        MAP_PCG_DEPENDENCY,
        "PCG_BIOME_SERIALIZED_DEPENDENCY",
        "DERIVED",
    ),
    "WORLD_PARTITION_EXTERNAL_ACTOR_REFERENCE": (
        MAP_WORLD_PARTITION_REFERENCE,
        "WORLD_PARTITION_EXTERNAL_ACTOR_PACKAGE_REFERENCE",
        "DERIVED",
    ),
}


MAP_USAGE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS map_usage_sources(
    source_id TEXT PRIMARY KEY,
    source_revision_id INTEGER,
    source_uri TEXT NOT NULL,
    source_schema_version TEXT NOT NULL,
    status TEXT NOT NULL,
    freshness_status TEXT NOT NULL,
    claims_complete_map_usage INTEGER NOT NULL CHECK(
        claims_complete_map_usage IN (0, 1)
    ),
    claims_spawn_coordinates INTEGER NOT NULL CHECK(
        claims_spawn_coordinates IN (0, 1)
    ),
    input_count INTEGER NOT NULL CHECK(input_count >= 0),
    materialized_count INTEGER NOT NULL CHECK(materialized_count >= 0),
    candidate_count INTEGER NOT NULL CHECK(candidate_count >= 0),
    rejected_count INTEGER NOT NULL CHECK(rejected_count >= 0),
    failure_reason TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS map_usage_edge_evidence(
    map_usage_id TEXT PRIMARY KEY,
    edge_id INTEGER UNIQUE NOT NULL,
    source_item_id TEXT NOT NULL,
    evidence_layer TEXT NOT NULL,
    map_family TEXT NOT NULL,
    map_kind TEXT NOT NULL,
    source_evidence_status TEXT NOT NULL,
    usage_status TEXT NOT NULL,
    freshness_status TEXT NOT NULL,
    claims_complete_map_usage INTEGER NOT NULL CHECK(
        claims_complete_map_usage IN (0, 1)
    ),
    claims_spawn_coordinates INTEGER NOT NULL CHECK(
        claims_spawn_coordinates IN (0, 1)
    ),
    evidence_count INTEGER NOT NULL CHECK(evidence_count >= 1),
    evidence_examples_json TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    FOREIGN KEY(edge_id) REFERENCES edges(edge_id)
);

CREATE INDEX IF NOT EXISTS idx_map_usage_evidence_layer
    ON map_usage_edge_evidence(
        evidence_layer, usage_status, freshness_status, edge_id
    );

CREATE VIEW IF NOT EXISTS confirmed_map_usage_edges AS
SELECT
    edge.edge_id,
    edge.source_entity_id,
    edge.target_entity_id,
    edge.edge_type,
    edge.edge_strength,
    edge.status,
    edge.confidence,
    edge.source_revision_id,
    edge.evidence_uri,
    edge.source_property,
    edge.source_graph,
    evidence.map_usage_id,
    evidence.source_item_id,
    evidence.evidence_layer,
    evidence.map_family,
    evidence.map_kind,
    evidence.source_evidence_status,
    evidence.usage_status,
    evidence.freshness_status,
    evidence.claims_complete_map_usage,
    evidence.claims_spawn_coordinates,
    evidence.evidence_count,
    evidence.evidence_examples_json,
    revision.source_uri AS source_revision_uri,
    revision.source_fingerprint,
    revision.generated_at AS source_generated_at
FROM edges AS edge
JOIN map_usage_edge_evidence AS evidence
  ON evidence.edge_id=edge.edge_id
JOIN source_revisions AS revision
  ON revision.revision_id=edge.source_revision_id
WHERE edge.edge_type IN (
        'MAP_DIRECT_REFERENCE',
        'MAP_PCG_DEPENDENCY',
        'MAP_WORLD_PARTITION_REFERENCE'
      )
  AND edge.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
  AND edge.confidence IN ('HIGH', 'CONFIRMED')
  AND evidence.source_evidence_status IN (
        'CONFIRMED', 'VERIFIED', 'RESOLVED'
      )
  AND evidence.usage_status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
  AND evidence.freshness_status='FRESH'
  AND revision.freshness_status='FRESH'
  AND TRIM(revision.source_kind)<>''
  AND UPPER(TRIM(revision.source_kind)) NOT IN (
        'UNKNOWN', 'NOT_RECOVERED', 'SOURCE_NOT_AVAILABLE',
        'UNRESOLVED', 'AMBIGUOUS', 'LEGACY_UNVERIFIED',
        'CONFIRMED_FINGERPRINT_ONLY', 'NOT_AVAILABLE', 'UNAVAILABLE'
      )
  AND TRIM(revision.source_uri)<>''
  AND UPPER(TRIM(revision.source_uri)) NOT IN (
        'UNKNOWN', 'NOT_RECOVERED', 'SOURCE_NOT_AVAILABLE',
        'UNRESOLVED', 'AMBIGUOUS', 'LEGACY_UNVERIFIED',
        'CONFIRMED_FINGERPRINT_ONLY', 'NOT_AVAILABLE', 'UNAVAILABLE'
      )
  AND TRIM(revision.source_fingerprint)<>''
  AND UPPER(TRIM(revision.source_fingerprint)) NOT IN (
        'UNKNOWN', 'NOT_RECOVERED', 'SOURCE_NOT_AVAILABLE',
        'UNRESOLVED', 'AMBIGUOUS', 'LEGACY_UNVERIFIED',
        'CONFIRMED_FINGERPRINT_ONLY', 'NOT_AVAILABLE', 'UNAVAILABLE'
      )
  AND TRIM(revision.producer_version)<>''
  AND UPPER(TRIM(revision.producer_version)) NOT IN (
        'UNKNOWN', 'NOT_RECOVERED', 'SOURCE_NOT_AVAILABLE',
        'UNRESOLVED', 'AMBIGUOUS', 'LEGACY_UNVERIFIED',
        'CONFIRMED_FINGERPRINT_ONLY', 'NOT_AVAILABLE', 'UNAVAILABLE'
      )
  AND TRIM(revision.schema_version)<>''
  AND UPPER(TRIM(revision.schema_version)) NOT IN (
        'UNKNOWN', 'NOT_RECOVERED', 'SOURCE_NOT_AVAILABLE',
        'UNRESOLVED', 'AMBIGUOUS', 'LEGACY_UNVERIFIED',
        'CONFIRMED_FINGERPRINT_ONLY', 'NOT_AVAILABLE', 'UNAVAILABLE'
      )
  AND TRIM(revision.generated_at)<>''
  AND UPPER(TRIM(revision.generated_at)) NOT IN (
        'UNKNOWN', 'NOT_RECOVERED', 'SOURCE_NOT_AVAILABLE',
        'UNRESOLVED', 'AMBIGUOUS', 'LEGACY_UNVERIFIED',
        'CONFIRMED_FINGERPRINT_ONLY', 'NOT_AVAILABLE', 'UNAVAILABLE'
      )
  AND (
        edge.evidence_uri GLOB 'registry-reference://?*'
        OR edge.evidence_uri
           GLOB 'map-evidence://asset-registry/?*'
        OR edge.evidence_uri
           GLOB 'map-evidence://resource-node-catalog/?*'
      )
  AND evidence.map_usage_id<>''
  AND evidence.evidence_layer<>''
  AND evidence.evidence_count>=1;
"""


@dataclass(frozen=True)
class _EntityIdentity:
    entity_id: int
    canonical_uri: str
    status: str
    confidence: str


@dataclass(frozen=True)
class _CatalogInput:
    payload: Mapping[str, object]
    dataset_revision: str
    generated_at: str
    freshness: str
    source_status: str


def create_map_usage_tables(connection: sqlite3.Connection) -> None:
    """Create the typed map evidence contract and confirmed-only view."""

    connection.executescript(MAP_USAGE_TABLES_SQL)


def _upper(value: object, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _is_high_confidence(value: object) -> bool:
    return _upper(value) in _CONFIRMED_CONFIDENCE


def _is_confirmed_identity(identity: _EntityIdentity) -> bool:
    return (
        identity.status in _IDENTITY_STATUSES
        and identity.confidence in _CONFIRMED_CONFIDENCE
    )


def _has_allowed_uri_prefix(
    value: object,
    prefixes: Iterable[str],
) -> bool:
    text = str(value or "").strip()
    normalized = text.upper().replace("-", "_").replace(" ", "_")
    if (
        not text
        or normalized in _UNRECOVERED_EVIDENCE_IDS
        or any(character.isspace() for character in text)
    ):
        return False
    lowered = text.casefold()
    return any(
        lowered.startswith(prefix)
        and len(text) > len(prefix)
        for prefix in prefixes
    )


def _is_recovered_source_revision_value(value: object) -> bool:
    text = str(value or "").strip()
    normalized = "_".join(
        text.upper().replace("-", " ").replace("_", " ").split()
    )
    return (
        bool(text)
        and normalized not in _UNRECOVERED_SOURCE_REVISION_VALUES
    )


def is_valid_map_evidence_uri(value: object) -> bool:
    """Return whether a map edge has a recovered, recognized evidence URI."""

    return _has_allowed_uri_prefix(value, _MAP_EVIDENCE_PREFIXES)


def _is_valid_direct_map_evidence_uri(value: object) -> bool:
    return _has_allowed_uri_prefix(value, _DIRECT_MAP_EVIDENCE_PREFIXES)


def _sha256_text(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _catalog_semantic_revision(payload: Mapping[str, object]) -> str:
    dataset = payload.get("dataset")
    nodes = payload.get("nodes")
    if not isinstance(dataset, Mapping) or not isinstance(nodes, list):
        raise ValueError("Map evidence catalog dataset/nodes are invalid")
    digest = hashlib.sha256()
    coverage = payload.get("coverage")
    map_scan = (
        coverage.get("mapScan")
        if isinstance(coverage, Mapping)
        else {}
    )
    digest.update(
        str(dataset.get("sourceStatus") or "").encode("utf-8")
    )
    digest.update(
        json.dumps(
            map_scan if isinstance(map_scan, Mapping) else {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(
        str(
            dataset.get("rankingDatasetRevision")
            or dataset.get("rankingScanManifestHash")
            or ""
        ).encode("utf-8")
    )
    evaluation_revision = str(
        dataset.get("evaluationDatasetRevision") or ""
    )
    if evaluation_revision:
        digest.update(evaluation_revision.encode("utf-8"))
    typed_nodes: list[Mapping[str, object]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError("Map evidence catalog contains a non-object node")
        typed_nodes.append(node)
    for node in sorted(
        typed_nodes,
        key=lambda item: str(item.get("objectPath") or ""),
    ):
        digest.update(
            json.dumps(
                node,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _catalog_freshness(payload: Mapping[str, object]) -> str:
    coverage = payload.get("coverage")
    map_scan = (
        coverage.get("mapScan")
        if isinstance(coverage, Mapping)
        else None
    )
    if not isinstance(map_scan, Mapping):
        return "NOT_VERIFIED"
    required = (
        (map_scan, "REFERENCE_SCAN_COMPLETE"),
        (map_scan.get("direct"), "DIRECT_SCAN_COMPLETE"),
        (map_scan.get("pcgBiome"), "PCG_BIOME_SCAN_COMPLETE"),
        (
            map_scan.get("worldPartitionExternalActors"),
            "WORLD_PARTITION_EXTERNAL_ACTOR_SCAN_COMPLETE",
        ),
    )
    for layer, expected in required:
        if not isinstance(layer, Mapping):
            return "NOT_VERIFIED"
        if _upper(layer.get("status")) != expected:
            return "NOT_VERIFIED"
        failures = layer.get("failures")
        if isinstance(failures, int) and failures != 0:
            return "NOT_VERIFIED"
        if bool(layer.get("truncated")):
            return "NOT_VERIFIED"
    return "FRESH"


def _load_catalog(path: Path | None) -> _CatalogInput | None:
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("Map evidence catalog must be a JSON object")
    if payload.get("schema") != RESOURCE_NODE_CATALOG_SCHEMA:
        raise ValueError("Map evidence catalog schema is missing or invalid")
    dataset = payload.get("dataset")
    if not isinstance(dataset, Mapping):
        raise ValueError("Map evidence catalog dataset is missing")
    declared_revision = str(dataset.get("revision") or "")
    if (
        len(declared_revision) != 64
        or any(character not in "0123456789abcdef" for character in declared_revision)
    ):
        raise ValueError("Map evidence catalog semantic revision is invalid")
    actual_revision = _catalog_semantic_revision(payload)
    if actual_revision != declared_revision:
        raise ValueError(
            "Map evidence catalog semantic revision does not match its content"
        )
    generated_at = str(dataset.get("generatedAt") or "")
    if not _is_recovered_source_revision_value(generated_at):
        raise ValueError(
            "Map evidence catalog generatedAt is missing or unrecovered"
        )
    freshness = _catalog_freshness(payload)
    return _CatalogInput(
        payload=payload,
        dataset_revision=declared_revision,
        generated_at=generated_at,
        freshness=freshness,
        source_status=(
            "COMPLETE" if freshness == "FRESH" else "PARTIAL"
        ),
    )


def _entity_identities_by_package(
    connection: sqlite3.Connection,
) -> dict[str, _EntityIdentity | None]:
    rows: dict[str, list[_EntityIdentity]] = {}
    for row in connection.execute(
        """
        SELECT
            package.package_path,
            entity.entity_id,
            entity.canonical_uri,
            entity.status,
            entity.confidence
        FROM packages AS package
        JOIN entities AS entity ON entity.package_id=package.package_id
        ORDER BY package.package_path, entity.entity_id
        """
    ):
        rows.setdefault(str(row[0]), []).append(
            _EntityIdentity(
                entity_id=int(row[1]),
                canonical_uri=str(row[2]),
                status=_upper(row[3]),
                confidence=_upper(row[4]),
            )
        )
    return {
        package_path: identities[0] if len(identities) == 1 else None
        for package_path, identities in rows.items()
    }


def _entities_by_uri(
    connection: sqlite3.Connection,
) -> dict[str, _EntityIdentity]:
    return {
        str(row[1]): _EntityIdentity(
            entity_id=int(row[0]),
            canonical_uri=str(row[1]),
            status=_upper(row[2]),
            confidence=_upper(row[3]),
        )
        for row in connection.execute(
            """
            SELECT entity_id, canonical_uri, status, confidence
            FROM entities
            """
        )
    }


def _source_revision_freshness(
    connection: sqlite3.Connection,
    revision_id: int,
) -> str:
    row = connection.execute(
        """
        SELECT source_kind, source_uri, source_fingerprint,
               producer_version, schema_version, generated_at,
               freshness_status
        FROM source_revisions
        WHERE revision_id=?
        """,
        (revision_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"Map evidence source revision does not exist: {revision_id}"
        )
    freshness = _upper(row[6])
    if freshness == "STALE":
        return "STALE"
    if (
        freshness != "FRESH"
        or not all(
            _is_recovered_source_revision_value(value)
            for value in row[:6]
        )
    ):
        return "UNKNOWN"
    return "FRESH"


def _registry_source_is_complete(
    discovery: sqlite3.Connection,
) -> bool:
    rows = list(
        discovery.execute(
            """
            SELECT status, confidence, limitations_json
            FROM source_inventory
            WHERE source_kind='unreal_asset_registry'
            """
        )
    )
    if len(rows) != 1:
        return False
    status, confidence, limitations_json = rows[0]
    try:
        limitations = json.loads(str(limitations_json or "[]"))
    except json.JSONDecodeError:
        return False
    return (
        _upper(status) == "COMPLETE"
        and _is_high_confidence(confidence)
        and limitations == []
    )


def _effective_edge_status(
    *,
    usage_status: str,
    freshness: str,
) -> str:
    if freshness != "FRESH":
        return "STALE"
    return usage_status


def _confidence_for_evidence(value: object) -> str:
    confidence = _upper(value)
    return confidence if confidence in _CONFIRMED_CONFIDENCE else "UNKNOWN"


def _normalize_usage_status(value: object) -> str:
    status = _upper(value, "NOT_RECOVERED")
    return status if status in _KNOWN_USAGE_STATUSES else "NOT_RECOVERED"


def _direct_rows(
    discovery: sqlite3.Connection,
) -> Iterator[sqlite3.Row]:
    discovery.row_factory = sqlite3.Row
    return iter(
        discovery.execute(
            """
            WITH unique_map_sources AS (
                SELECT
                    package_path,
                    MIN(object_path) AS object_path,
                    MIN(identity_status) AS identity_status,
                    MIN(identity_confidence) AS identity_confidence
                FROM assets
                WHERE is_map=1
                GROUP BY package_path
                HAVING COUNT(*)=1
            )
            SELECT
                reference.reference_id,
                reference.source_object_path AS source_package_path,
                reference.target_object_path AS target_package_path,
                reference.edge_kind,
                reference.reference_strength,
                reference.source_property,
                reference.source_evidence_id,
                reference.confidence,
                reference.source_kind,
                source.identity_status AS source_identity_status,
                source.identity_confidence AS source_identity_confidence
            FROM asset_references AS reference
            JOIN unique_map_sources AS source
              ON source.package_path=reference.source_object_path
            WHERE reference.edge_kind='package_dependency'
              AND reference.source_property='AssetRegistryDependency'
            ORDER BY reference.reference_id
            """
        )
    )


def _insert_source(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    source_revision_id: int | None,
    source_uri: str,
    schema_version: str,
    status: str,
    freshness: str,
    input_count: int,
    materialized_count: int,
    candidate_count: int,
    rejected_count: int,
    failure_reason: str,
) -> None:
    connection.execute(
        """
        INSERT INTO map_usage_sources VALUES(
            ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            source_id,
            source_revision_id,
            source_uri,
            schema_version,
            status,
            freshness,
            input_count,
            materialized_count,
            candidate_count,
            rejected_count,
            failure_reason,
            MAP_USAGE_EXTRACTOR_VERSION,
        ),
    )


def _insert_edge_batch(
    connection: sqlite3.Connection,
    edge_rows: list[tuple[object, ...]],
    evidence_rows: list[tuple[object, ...]],
) -> None:
    if not edge_rows:
        return
    connection.executemany(
        """
        INSERT INTO edges(
            edge_id, source_entity_id, target_entity_id, edge_type,
            edge_strength, status, confidence, source_revision_id,
            evidence_uri, source_property, source_graph
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        edge_rows,
    )
    connection.executemany(
        """
        INSERT INTO map_usage_edge_evidence VALUES(
            ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?
        )
        """,
        evidence_rows,
    )
    edge_rows.clear()
    evidence_rows.clear()


def _materialize_direct(
    discovery: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    source_revision_id: int,
    next_edge_id: int,
    packages: Mapping[str, _EntityIdentity | None],
) -> tuple[dict[str, int], int]:
    freshness = _source_revision_freshness(target, source_revision_id)
    registry_complete = _registry_source_is_complete(discovery)
    edge_rows: list[tuple[object, ...]] = []
    evidence_rows: list[tuple[object, ...]] = []
    counts = {
        "input": 0,
        "materialized": 0,
        "confirmed": 0,
        "candidate": 0,
        "rejected": 0,
    }
    for row in _direct_rows(discovery):
        counts["input"] += 1
        source = packages.get(str(row["source_package_path"]))
        target_entity = packages.get(str(row["target_package_path"]))
        if source is None or target_entity is None:
            counts["rejected"] += 1
            continue
        source_identity_confirmed = (
            _upper(row["source_identity_status"]) in _IDENTITY_STATUSES
            and _is_high_confidence(row["source_identity_confidence"])
            and _is_confirmed_identity(source)
            and _is_confirmed_identity(target_entity)
        )
        strength = _upper(row["reference_strength"])
        source_kind = str(row["source_kind"] or "")
        if source_kind == "existing_knowledge_database":
            usage_status = "LEGACY_UNVERIFIED"
        elif (
            source_kind == "unreal_asset_registry"
            and registry_complete
            and strength in {"HARD", "SOFT"}
            and _is_high_confidence(row["confidence"])
            and source_identity_confirmed
        ):
            usage_status = "CONFIRMED"
        else:
            usage_status = "CANDIDATE"
        confidence = _confidence_for_evidence(row["confidence"])
        evidence_uri = str(row["source_evidence_id"] or "")
        if not evidence_uri:
            evidence_uri = (
                "map-evidence://asset-registry/"
                + _sha256_text(
                    (
                        str(row["reference_id"]),
                        source.canonical_uri,
                        target_entity.canonical_uri,
                    )
                )
            )
            evidence_uri_is_valid = True
        else:
            evidence_uri_is_valid = (
                _is_valid_direct_map_evidence_uri(evidence_uri)
            )
        if (
            usage_status in _CONFIRMED_STATUSES
            and not evidence_uri_is_valid
        ):
            usage_status = "CANDIDATE"
        status = _effective_edge_status(
            usage_status=usage_status,
            freshness=freshness,
        )
        map_usage_id = "mapuse_" + _sha256_text(
            (
                MAP_DIRECT_REFERENCE,
                evidence_uri,
                source.canonical_uri,
                target_entity.canonical_uri,
            )
        )
        evidence_layer = (
            f"ASSET_REGISTRY_{strength}_PACKAGE_DEPENDENCY"
        )
        edge_rows.append(
            (
                next_edge_id,
                source.entity_id,
                target_entity.entity_id,
                MAP_DIRECT_REFERENCE,
                strength if strength in {"HARD", "SOFT", "SEARCHABLE"} else "DERIVED",
                status,
                confidence,
                source_revision_id,
                evidence_uri,
                "AssetRegistryDependency",
                "",
            )
        )
        evidence_rows.append(
            (
                map_usage_id,
                next_edge_id,
                str(row["reference_id"]),
                evidence_layer,
                "",
                "MAP_ASSET",
                "CONFIRMED"
                if (
                    _is_high_confidence(row["confidence"])
                    and evidence_uri_is_valid
                )
                else "CANDIDATE",
                usage_status,
                freshness,
                1,
                "[]",
                MAP_USAGE_EXTRACTOR_VERSION,
            )
        )
        next_edge_id += 1
        counts["materialized"] += 1
        counts["confirmed"] += int(
            status in _CONFIRMED_STATUSES
            and usage_status in _CONFIRMED_STATUSES
        )
        counts["candidate"] += int(
            usage_status not in _CONFIRMED_STATUSES
            or status not in _CONFIRMED_STATUSES
        )
        if len(edge_rows) >= 10_000:
            _insert_edge_batch(target, edge_rows, evidence_rows)
    _insert_edge_batch(target, edge_rows, evidence_rows)
    _insert_source(
        target,
        source_id="DISCOVERY_ASSET_REGISTRY",
        source_revision_id=source_revision_id,
        source_uri="discovery://ark/asset-registry-map-dependencies",
        schema_version="ark.kb.registry-snapshot.v2",
        status="COMPLETE" if registry_complete else "PARTIAL",
        freshness=freshness,
        input_count=counts["input"],
        materialized_count=counts["materialized"],
        candidate_count=counts["candidate"],
        rejected_count=counts["rejected"],
        failure_reason=(
            ""
            if registry_complete
            else "ASSET_REGISTRY_SOURCE_NOT_COMPLETE"
        ),
    )
    return counts, next_edge_id


def _catalog_revision(
    connection: sqlite3.Connection,
    catalog: _CatalogInput,
) -> int:
    existing = connection.execute(
        """
        SELECT revision_id
        FROM source_revisions
        WHERE source_kind='map_usage_catalog'
          AND source_uri='map-catalog://resource-nodes'
          AND source_fingerprint=?
        """,
        (catalog.dataset_revision,),
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    next_revision_id = int(
        connection.execute(
            "SELECT COALESCE(MAX(revision_id), 0) + 1 FROM source_revisions"
        ).fetchone()[0]
    )
    connection.execute(
        """
        INSERT INTO source_revisions VALUES(
            ?, 'map_usage_catalog', 'map-catalog://resource-nodes',
            ?, ?, ?, ?, ?
        )
        """,
        (
            next_revision_id,
            catalog.dataset_revision,
            MAP_USAGE_EXTRACTOR_VERSION,
            RESOURCE_NODE_CATALOG_SCHEMA,
            catalog.generated_at,
            catalog.freshness,
        ),
    )
    return next_revision_id


def _catalog_items(
    catalog: _CatalogInput,
) -> Iterator[tuple[str, Mapping[str, object]]]:
    nodes = catalog.payload.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        target_uri = str(node.get("objectPath") or "")
        references = node.get("mapReferences")
        items = (
            references.get("items")
            if isinstance(references, Mapping)
            else None
        )
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping):
                yield target_uri, item


def _materialize_catalog(
    target: sqlite3.Connection,
    *,
    catalog: _CatalogInput | None,
    next_edge_id: int,
    packages: Mapping[str, _EntityIdentity | None],
    entities: Mapping[str, _EntityIdentity],
) -> tuple[dict[str, int | str], int]:
    counts: dict[str, int | str] = {
        "status": "SOURCE_NOT_AVAILABLE",
        "input": 0,
        "materialized": 0,
        "pcgConfirmed": 0,
        "pcgCandidate": 0,
        "worldPartitionConfirmed": 0,
        "worldPartitionCandidate": 0,
        "auxiliarySkipped": 0,
        "rejected": 0,
    }
    if catalog is None:
        _insert_source(
            target,
            source_id="RESOURCE_NODE_CATALOG",
            source_revision_id=None,
            source_uri="map-catalog://resource-nodes",
            schema_version=RESOURCE_NODE_CATALOG_SCHEMA,
            status="SOURCE_NOT_AVAILABLE",
            freshness="SOURCE_NOT_AVAILABLE",
            input_count=0,
            materialized_count=0,
            candidate_count=0,
            rejected_count=0,
            failure_reason="MAP_EVIDENCE_SOURCE_NOT_AVAILABLE",
        )
        return counts, next_edge_id
    revision_id = _catalog_revision(target, catalog)
    edge_rows: list[tuple[object, ...]] = []
    evidence_rows: list[tuple[object, ...]] = []
    for target_uri, item in _catalog_items(catalog):
        relation = str(item.get("relation") or "")
        if relation not in _CATALOG_RELATIONS:
            continue
        counts["input"] = int(counts["input"]) + 1
        if str(item.get("mapKind") or "") != "PLAYABLE_MAP_EVIDENCE":
            counts["auxiliarySkipped"] = (
                int(counts["auxiliarySkipped"]) + 1
            )
            continue
        source = packages.get(str(item.get("objectPath") or ""))
        target_entity = entities.get(target_uri)
        if source is None or target_entity is None:
            counts["rejected"] = int(counts["rejected"]) + 1
            continue
        edge_type, evidence_layer, strength = _CATALOG_RELATIONS[
            relation
        ]
        usage_status = _normalize_usage_status(item.get("usageStatus"))
        evidence_status = _normalize_usage_status(
            item.get("evidenceStatus")
        )
        if (
            usage_status in _CONFIRMED_STATUSES
            and (
                evidence_status not in _CONFIRMED_STATUSES
                or not _is_confirmed_identity(source)
                or not _is_confirmed_identity(target_entity)
            )
        ):
            usage_status = "CANDIDATE"
        status = _effective_edge_status(
            usage_status=usage_status,
            freshness=catalog.freshness,
        )
        confidence = (
            "HIGH"
            if evidence_status in _CONFIRMED_STATUSES
            else "UNKNOWN"
        )
        source_item_id = str(item.get("id") or "")
        if not source_item_id:
            source_item_id = _sha256_text(
                (
                    relation,
                    source.canonical_uri,
                    target_entity.canonical_uri,
                )
            )
        map_usage_id = "mapuse_" + _sha256_text(
            (
                catalog.dataset_revision,
                source_item_id,
                source.canonical_uri,
                target_entity.canonical_uri,
                edge_type,
            )
        )
        evidence_uri = (
            "map-evidence://resource-node-catalog/"
            f"{catalog.dataset_revision}/{map_usage_id}"
        )
        raw_count = item.get("evidenceCount", 1)
        evidence_count = (
            max(1, raw_count)
            if isinstance(raw_count, int) and not isinstance(raw_count, bool)
            else 1
        )
        raw_examples = item.get("evidenceExamples")
        examples = sorted(
            {
                str(example)
                for example in (
                    raw_examples if isinstance(raw_examples, list) else []
                )
                if isinstance(example, str) and example.startswith("/")
            }
        )[:16]
        map_family = str(item.get("mapFamily") or "")
        edge_rows.append(
            (
                next_edge_id,
                source.entity_id,
                target_entity.entity_id,
                edge_type,
                strength,
                status,
                confidence,
                revision_id,
                evidence_uri,
                relation,
                "",
            )
        )
        evidence_rows.append(
            (
                map_usage_id,
                next_edge_id,
                source_item_id,
                evidence_layer,
                map_family,
                "PLAYABLE_MAP_EVIDENCE",
                evidence_status,
                usage_status,
                catalog.freshness,
                evidence_count,
                json.dumps(
                    examples,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                MAP_USAGE_EXTRACTOR_VERSION,
            )
        )
        next_edge_id += 1
        counts["materialized"] = int(counts["materialized"]) + 1
        prefix = (
            "pcg" if edge_type == MAP_PCG_DEPENDENCY else "worldPartition"
        )
        suffix = (
            "Confirmed"
            if status in _CONFIRMED_STATUSES
            and usage_status in _CONFIRMED_STATUSES
            else "Candidate"
        )
        key = prefix + suffix
        counts[key] = int(counts[key]) + 1
        if len(edge_rows) >= 10_000:
            _insert_edge_batch(target, edge_rows, evidence_rows)
    _insert_edge_batch(target, edge_rows, evidence_rows)
    candidate_count = int(counts["pcgCandidate"]) + int(
        counts["worldPartitionCandidate"]
    )
    _insert_source(
        target,
        source_id="RESOURCE_NODE_CATALOG",
        source_revision_id=revision_id,
        source_uri="map-catalog://resource-nodes",
        schema_version=RESOURCE_NODE_CATALOG_SCHEMA,
        status=catalog.source_status,
        freshness=catalog.freshness,
        input_count=int(counts["input"]),
        materialized_count=int(counts["materialized"]),
        candidate_count=candidate_count,
        rejected_count=(
            int(counts["rejected"]) + int(counts["auxiliarySkipped"])
        ),
        failure_reason=(
            ""
            if catalog.freshness == "FRESH"
            else "MAP_EVIDENCE_SOURCE_NOT_FRESH"
        ),
    )
    counts["status"] = catalog.source_status
    return counts, next_edge_id


def materialize_map_usage_edges(
    discovery: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    source_revision_id: int,
    resource_catalog_path: Path | None,
    generated_at: str,
) -> dict[str, int | str]:
    """Rebuild typed map edges from explicit, revisioned evidence only."""

    del generated_at
    catalog = _load_catalog(resource_catalog_path)
    create_map_usage_tables(target)
    target.execute("SAVEPOINT materialize_map_usage")
    try:
        target.execute("DELETE FROM map_usage_edge_evidence")
        target.execute(
            """
            DELETE FROM edges
            WHERE edge_type IN (?, ?, ?)
            """,
            MAP_USAGE_EDGE_TYPES,
        )
        target.execute("DELETE FROM map_usage_sources")
        packages = _entity_identities_by_package(target)
        entities = _entities_by_uri(target)
        next_edge_id = int(
            target.execute(
                "SELECT COALESCE(MAX(edge_id), 0) + 1 FROM edges"
            ).fetchone()[0]
        )
        direct, next_edge_id = _materialize_direct(
            discovery,
            target,
            source_revision_id=source_revision_id,
            next_edge_id=next_edge_id,
            packages=packages,
        )
        catalog_counts, _next_edge_id = _materialize_catalog(
            target,
            catalog=catalog,
            next_edge_id=next_edge_id,
            packages=packages,
            entities=entities,
        )
        target.execute("RELEASE SAVEPOINT materialize_map_usage")
        target.commit()
    except Exception:
        target.execute("ROLLBACK TO SAVEPOINT materialize_map_usage")
        target.execute("RELEASE SAVEPOINT materialize_map_usage")
        raise
    return {
        "mapUsageEdges": int(direct["materialized"])
        + int(catalog_counts["materialized"]),
        "directEdges": int(direct["materialized"]),
        "directConfirmed": int(direct["confirmed"]),
        "directCandidate": int(direct["candidate"]),
        "directRejected": int(direct["rejected"]),
        "pcgConfirmed": int(catalog_counts["pcgConfirmed"]),
        "pcgCandidate": int(catalog_counts["pcgCandidate"]),
        "worldPartitionConfirmed": int(
            catalog_counts["worldPartitionConfirmed"]
        ),
        "worldPartitionCandidate": int(
            catalog_counts["worldPartitionCandidate"]
        ),
        "catalogAuxiliarySkipped": int(
            catalog_counts["auxiliarySkipped"]
        ),
        "catalogRejected": int(catalog_counts["rejected"]),
        "catalogStatus": str(catalog_counts["status"]),
    }
