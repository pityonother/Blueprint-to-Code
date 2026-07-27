"""Normalized physical storage builders for ARK Knowledge Base vNext."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Mapping

from .benchmark import materialize_benchmark_queries
from .blueprint_ingest import materialize_blueprint_defaults
from .class_hierarchy import CLASS_TABLES_SQL, materialize_discovery_classes
from .fact_store import (
    materialize_declared_defaults,
    materialize_effective_defaults,
)
from .legacy import import_legacy_lineage
from .native_gold_set import materialize_native_gold_set
from .invalidation import rebuild_invalidation_dependencies
from .ontology import OntologyBundle, infer_domain_memberships
from .registrations import (
    REGISTRATION_TABLES_SQL,
    materialize_typed_registrations,
)
from .roles import ROLE_TABLES_SQL, materialize_discovery_roles
from .schema_capabilities import CORE_SCHEMA_VERSION


CATALOG_SCHEMA_VERSION = "ark-kb-catalog/v1"
SEARCH_SCHEMA_VERSION = "ark-kb-search/v1"
CACHE_SCHEMA_VERSION = "ark-kb-cache/v1"

CATALOG_SCHEMA_SQL = """
CREATE TABLE metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE source_revisions(
    revision_id INTEGER PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    freshness_status TEXT NOT NULL,
    UNIQUE(source_kind, source_uri, source_fingerprint)
);

CREATE TABLE content_packs(
    content_pack_id INTEGER PRIMARY KEY,
    mount_point TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL
);

CREATE TABLE packages(
    package_id INTEGER PRIMARY KEY,
    package_path TEXT UNIQUE NOT NULL,
    mount_point TEXT NOT NULL,
    content_pack_id INTEGER,
    current_revision_id INTEGER,
    plugin_or_dlc TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    FOREIGN KEY(content_pack_id) REFERENCES content_packs(content_pack_id),
    FOREIGN KEY(current_revision_id) REFERENCES source_revisions(revision_id)
);

CREATE TABLE catalog_nodes(
    node_id INTEGER PRIMARY KEY,
    canonical_uri TEXT UNIQUE NOT NULL,
    node_kind TEXT NOT NULL,
    package_id INTEGER,
    FOREIGN KEY(package_id) REFERENCES packages(package_id)
);

CREATE TABLE catalog_assets(
    node_id INTEGER PRIMARY KEY,
    asset_name TEXT NOT NULL,
    asset_class_path TEXT NOT NULL,
    generated_class_path TEXT NOT NULL,
    parent_class_path TEXT NOT NULL,
    native_parent_class_path TEXT NOT NULL,
    is_blueprint INTEGER,
    is_map INTEGER NOT NULL,
    file_size_total INTEGER NOT NULL,
    source_fingerprint TEXT NOT NULL,
    source_modified TEXT NOT NULL,
    identity_status TEXT NOT NULL,
    identity_confidence TEXT NOT NULL,
    evidence_freshness TEXT NOT NULL,
    FOREIGN KEY(node_id) REFERENCES catalog_nodes(node_id)
);

CREATE TABLE edge_types(
    edge_type_id INTEGER PRIMARY KEY,
    edge_type TEXT UNIQUE NOT NULL
);

CREATE TABLE edge_strengths(
    edge_strength_id INTEGER PRIMARY KEY,
    edge_strength TEXT UNIQUE NOT NULL
);

CREATE TABLE source_properties(
    source_property_id INTEGER PRIMARY KEY,
    source_property TEXT UNIQUE NOT NULL
);

CREATE TABLE catalog_edges(
    edge_id INTEGER PRIMARY KEY,
    source_node_id INTEGER NOT NULL,
    target_node_id INTEGER NOT NULL,
    edge_type_id INTEGER NOT NULL,
    edge_strength_id INTEGER NOT NULL,
    source_property_id INTEGER NOT NULL,
    source_revision_id INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    FOREIGN KEY(source_node_id) REFERENCES catalog_nodes(node_id),
    FOREIGN KEY(target_node_id) REFERENCES catalog_nodes(node_id),
    FOREIGN KEY(edge_type_id) REFERENCES edge_types(edge_type_id),
    FOREIGN KEY(edge_strength_id) REFERENCES edge_strengths(edge_strength_id),
    FOREIGN KEY(source_property_id) REFERENCES source_properties(source_property_id),
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
);

CREATE TABLE catalog_coverage(
    node_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    gap_count INTEGER NOT NULL,
    stale_count INTEGER NOT NULL,
    PRIMARY KEY(node_id, stage),
    FOREIGN KEY(node_id) REFERENCES catalog_nodes(node_id)
) WITHOUT ROWID;

CREATE TABLE source_inventory(
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    limitations_json TEXT NOT NULL
) WITHOUT ROWID;
"""

CATALOG_INDEX_SQL = """
CREATE INDEX idx_catalog_nodes_kind
    ON catalog_nodes(node_kind, node_id);
CREATE INDEX idx_catalog_assets_class
    ON catalog_assets(asset_class_path, node_id);
CREATE INDEX idx_catalog_edges_source
    ON catalog_edges(source_node_id, edge_type_id, target_node_id);
CREATE INDEX idx_catalog_edges_target
    ON catalog_edges(target_node_id, edge_type_id, source_node_id);
CREATE INDEX idx_catalog_edges_property
    ON catalog_edges(source_property_id, edge_type_id);
CREATE INDEX idx_catalog_coverage_status
    ON catalog_coverage(stage, status);
"""

FULL_CATALOG_SCHEMA_SQL = CATALOG_SCHEMA_SQL + "\n" + CATALOG_INDEX_SQL

CORE_SCHEMA_SQL = """
CREATE TABLE metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE source_revisions(
    revision_id INTEGER PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    freshness_status TEXT NOT NULL,
    UNIQUE(source_kind, source_uri, source_fingerprint)
);

CREATE TABLE packages(
    package_id INTEGER PRIMARY KEY,
    package_path TEXT UNIQUE NOT NULL,
    mount_point TEXT NOT NULL,
    content_pack_id INTEGER,
    current_revision_id INTEGER
);

CREATE TABLE entities(
    entity_id INTEGER PRIMARY KEY,
    canonical_uri TEXT UNIQUE NOT NULL,
    entity_kind TEXT NOT NULL,
    package_id INTEGER,
    class_id INTEGER,
    display_name TEXT,
    internal_name TEXT,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    FOREIGN KEY(package_id) REFERENCES packages(package_id)
);

CREATE TABLE aliases(
    alias TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    alias_kind TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL,
    PRIMARY KEY(alias, entity_id, alias_kind),
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
) WITHOUT ROWID;

CREATE TABLE edges(
    edge_id INTEGER PRIMARY KEY,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL,
    edge_strength TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_revision_id INTEGER NOT NULL,
    evidence_uri TEXT NOT NULL,
    source_property TEXT NOT NULL DEFAULT '',
    source_graph TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(source_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(target_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
);

CREATE TABLE facts(
    fact_id INTEGER PRIMARY KEY,
    subject_entity_id INTEGER NOT NULL,
    fact_type TEXT NOT NULL,
    fact_name TEXT NOT NULL DEFAULT '',
    scope_kind TEXT NOT NULL,
    declared_on_entity_id INTEGER,
    value_kind TEXT NOT NULL,
    value_text TEXT,
    value_number REAL,
    value_integer INTEGER,
    value_json TEXT,
    unit TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    ontology_version TEXT NOT NULL,
    current INTEGER NOT NULL DEFAULT 1,
    canonical_fact_key TEXT UNIQUE NOT NULL,
    FOREIGN KEY(subject_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(declared_on_entity_id) REFERENCES entities(entity_id)
);

CREATE TABLE fact_evidence(
    fact_id INTEGER NOT NULL,
    source_revision_id INTEGER NOT NULL,
    evidence_uri TEXT NOT NULL,
    evidence_role TEXT NOT NULL,
    PRIMARY KEY(fact_id, source_revision_id, evidence_uri),
    FOREIGN KEY(fact_id) REFERENCES facts(fact_id),
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
) WITHOUT ROWID;

CREATE TABLE effective_facts(
    entity_id INTEGER NOT NULL,
    fact_type TEXT NOT NULL,
    fact_name TEXT NOT NULL DEFAULT '',
    fact_id INTEGER,
    inherited_from_entity_id INTEGER,
    resolution_chain_json TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    source_revision_set_hash TEXT NOT NULL,
    PRIMARY KEY(entity_id, fact_type, fact_name),
    CHECK(
        (resolution_status='RESOLVED' AND fact_id IS NOT NULL)
        OR
        (resolution_status<>'RESOLVED' AND fact_id IS NULL)
    ),
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(fact_id) REFERENCES facts(fact_id),
    FOREIGN KEY(inherited_from_entity_id) REFERENCES entities(entity_id)
) WITHOUT ROWID;

CREATE TABLE effective_fact_candidates(
    entity_id INTEGER NOT NULL,
    fact_type TEXT NOT NULL,
    fact_name TEXT NOT NULL DEFAULT '',
    candidate_fact_id INTEGER NOT NULL,
    declared_on_entity_id INTEGER NOT NULL,
    inheritance_depth INTEGER NOT NULL CHECK(inheritance_depth >= 0),
    path_status TEXT NOT NULL,
    selected INTEGER NOT NULL CHECK(selected IN (0, 1)),
    rejection_reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(
        entity_id, fact_type, fact_name, candidate_fact_id
    ),
    CHECK(
        (selected=1 AND rejection_reason='')
        OR
        (selected=0 AND rejection_reason<>'')
    ),
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(candidate_fact_id) REFERENCES facts(fact_id),
    FOREIGN KEY(declared_on_entity_id) REFERENCES entities(entity_id)
) WITHOUT ROWID;

CREATE TABLE domain_memberships(
    entity_id INTEGER NOT NULL,
    domain_id TEXT NOT NULL,
    membership_kind TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    ontology_version TEXT NOT NULL,
    PRIMARY KEY(entity_id, domain_id, membership_kind, evidence_id),
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
) WITHOUT ROWID;

CREATE TABLE coverage(
    entity_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    confirmed_count INTEGER NOT NULL,
    heuristic_count INTEGER NOT NULL,
    ambiguous_count INTEGER NOT NULL,
    not_recovered_count INTEGER NOT NULL,
    source_not_available_count INTEGER NOT NULL,
    stale_count INTEGER NOT NULL,
    failure_reason TEXT NOT NULL,
    PRIMARY KEY(entity_id, stage),
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
) WITHOUT ROWID;

CREATE TABLE invalidation_dependencies(
    upstream_revision_id INTEGER NOT NULL,
    downstream_kind TEXT NOT NULL,
    downstream_id INTEGER NOT NULL,
    dependency_reason TEXT NOT NULL,
    PRIMARY KEY(
        upstream_revision_id, downstream_kind,
        downstream_id, dependency_reason
    ),
    FOREIGN KEY(upstream_revision_id) REFERENCES source_revisions(revision_id)
) WITHOUT ROWID;

CREATE TABLE invalidation_events(
    event_id TEXT PRIMARY KEY,
    event_kind TEXT NOT NULL,
    upstream_revision_id INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY(upstream_revision_id) REFERENCES source_revisions(revision_id)
) WITHOUT ROWID;

CREATE TABLE invalidation_queue(
    event_id TEXT NOT NULL,
    downstream_kind TEXT NOT NULL,
    downstream_id INTEGER NOT NULL,
    dependency_reason TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY(event_id, downstream_kind, downstream_id),
    FOREIGN KEY(event_id) REFERENCES invalidation_events(event_id)
) WITHOUT ROWID;

CREATE TABLE legacy_lineage(
    lineage_id INTEGER PRIMARY KEY,
    target_kind TEXT NOT NULL,
    target_id INTEGER,
    legacy_database TEXT NOT NULL,
    legacy_table TEXT NOT NULL,
    legacy_primary_key TEXT NOT NULL,
    source_asset_uri TEXT NOT NULL,
    evidence_uri TEXT NOT NULL,
    status TEXT NOT NULL,
    source_revision_id INTEGER NOT NULL,
    UNIQUE(legacy_database, legacy_table, legacy_primary_key),
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
);

CREATE TABLE projection_runs(
    projection_name TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    source_revision_set_hash TEXT NOT NULL,
    ontology_version TEXT NOT NULL,
    built_at TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    validation_status TEXT NOT NULL,
    PRIMARY KEY(projection_name, projection_version)
) WITHOUT ROWID;

CREATE TABLE native_functions(
    native_function_id INTEGER PRIMARY KEY,
    canonical_uri TEXT UNIQUE NOT NULL,
    qualified_symbol TEXT NOT NULL,
    module_name TEXT NOT NULL,
    rva TEXT NOT NULL,
    signature TEXT NOT NULL,
    binary_sha256 TEXT NOT NULL,
    pdb_sha256 TEXT NOT NULL,
    pdb_guid_age TEXT NOT NULL,
    recipe_ids_json TEXT NOT NULL,
    evidence_set_ids_json TEXT NOT NULL,
    caller_count INTEGER NOT NULL,
    callee_count INTEGER NOT NULL,
    callsite_status TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_revision_id INTEGER NOT NULL,
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
);

CREATE TABLE native_field_accesses(
    field_access_id INTEGER PRIMARY KEY,
    native_function_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    field_offset TEXT NOT NULL,
    access_kind TEXT NOT NULL,
    instruction_or_slice_uri TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    FOREIGN KEY(native_function_id) REFERENCES native_functions(native_function_id)
);

CREATE TABLE native_gold_targets(
    target_id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL,
    qualified_symbol TEXT NOT NULL,
    expected_rva TEXT NOT NULL,
    recipe_id TEXT NOT NULL,
    native_function_id INTEGER,
    status TEXT NOT NULL,
    gap_code TEXT NOT NULL,
    FOREIGN KEY(native_function_id)
      REFERENCES native_functions(native_function_id)
) WITHOUT ROWID;

CREATE TABLE native_blueprint_links(
    link_id TEXT PRIMARY KEY,
    blueprint_entity_id INTEGER NOT NULL,
    blueprint_graph_evidence_uri TEXT NOT NULL,
    blueprint_function_name TEXT NOT NULL,
    native_function_id INTEGER,
    native_evidence_uri TEXT NOT NULL,
    resolution_method TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    FOREIGN KEY(blueprint_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(native_function_id)
      REFERENCES native_functions(native_function_id)
) WITHOUT ROWID;

CREATE TABLE benchmark_queries(
    query_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    tier TEXT NOT NULL,
    primary_domain TEXT NOT NULL,
    expected_answer_type TEXT NOT NULL,
    expected_gap_code TEXT NOT NULL DEFAULT '',
    query_json TEXT NOT NULL,
    negative_case TEXT NOT NULL DEFAULT ''
) WITHOUT ROWID;

CREATE INDEX idx_entities_kind ON entities(entity_kind, entity_id);
CREATE INDEX idx_aliases_entity ON aliases(entity_id, alias_kind);
CREATE INDEX idx_edges_source ON edges(source_entity_id, edge_type, target_entity_id);
CREATE INDEX idx_edges_target ON edges(target_entity_id, edge_type, source_entity_id);
CREATE INDEX idx_facts_subject
    ON facts(subject_entity_id, fact_type, fact_name, current);
CREATE INDEX idx_facts_type_status
    ON facts(fact_type, fact_name, status, confidence);
CREATE UNIQUE INDEX idx_effective_candidate_one_selected
    ON effective_fact_candidates(entity_id, fact_type, fact_name)
    WHERE selected=1;
CREATE INDEX idx_effective_candidate_declared
    ON effective_fact_candidates(
        declared_on_entity_id, fact_name, entity_id
    );
CREATE INDEX idx_effective_candidate_fact
    ON effective_fact_candidates(candidate_fact_id, entity_id);
CREATE INDEX idx_domain_memberships_domain
    ON domain_memberships(domain_id, status, confidence, entity_id);
CREATE INDEX idx_coverage_status ON coverage(stage, status, entity_id);
CREATE INDEX idx_invalidation_downstream
    ON invalidation_dependencies(downstream_kind, downstream_id);
CREATE INDEX idx_invalidation_queue_status
    ON invalidation_queue(status, downstream_kind, downstream_id);
CREATE INDEX idx_legacy_lineage_source
    ON legacy_lineage(legacy_database, legacy_table, legacy_primary_key);
CREATE INDEX idx_native_qualified_symbol
    ON native_functions(qualified_symbol, rva);
CREATE INDEX idx_native_blueprint_entity
    ON native_blueprint_links(blueprint_entity_id, status);
"""

FULL_CORE_SCHEMA_SQL = (
    CORE_SCHEMA_SQL
    + "\n"
    + CLASS_TABLES_SQL
    + "\n"
    + ROLE_TABLES_SQL
    + "\n"
    + REGISTRATION_TABLES_SQL
)

SEARCH_SCHEMA_SQL = """
CREATE TABLE metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE entity_search_meta(
    entity_id INTEGER PRIMARY KEY,
    canonical_uri TEXT UNIQUE NOT NULL,
    entity_kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    internal_name TEXT NOT NULL,
    freshness_status TEXT NOT NULL
);

CREATE TABLE search_aliases(
    alias TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    alias_kind TEXT NOT NULL,
    language TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY(alias, entity_id, alias_kind)
) WITHOUT ROWID;

CREATE VIRTUAL TABLE entities_fts USING fts5(
    entity_id UNINDEXED,
    canonical_uri,
    display_name,
    internal_name,
    aliases,
    tokenize='unicode61'
);
"""

CACHE_SCHEMA_SQL = """
CREATE TABLE metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE query_snapshots(
    snapshot_id TEXT PRIMARY KEY,
    query_fingerprint TEXT UNIQUE NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    source_revision_set_hash TEXT NOT NULL,
    invalidation_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE context_packs(
    context_pack_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    content TEXT NOT NULL,
    estimated_tokens INTEGER NOT NULL,
    returned_count INTEGER NOT NULL,
    omitted_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES query_snapshots(snapshot_id)
) WITHOUT ROWID;

CREATE TABLE answer_plans(
    plan_id TEXT PRIMARY KEY,
    query_fingerprint TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    source_revision_set_hash TEXT NOT NULL,
    invalidation_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE materialized_neighborhoods(
    neighborhood_id TEXT PRIMARY KEY,
    entity_id INTEGER NOT NULL,
    hops INTEGER NOT NULL,
    edge_types_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_revision_set_hash TEXT NOT NULL,
    invalidation_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
) WITHOUT ROWID;
"""


def _connect(path: Path, schema_sql: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, uri=True)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(schema_sql)
    return connection


def _attach_read_only(
    connection: sqlite3.Connection,
    path: Path,
    alias: str,
) -> None:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection.execute(f"ATTACH DATABASE ? AS {alias}", (uri,))


def _metadata(
    connection: sqlite3.Connection,
    values: Mapping[str, object],
) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        [(str(key), str(value)) for key, value in values.items()],
    )


def build_catalog_database(
    *,
    discovery_path: Path,
    output_path: Path,
    source_fingerprint: str,
    generated_at: str,
) -> dict[str, int]:
    connection = _connect(output_path, CATALOG_SCHEMA_SQL)
    try:
        _attach_read_only(connection, discovery_path, "discovery")
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                1, 'discovery', 'discovery://ark/full-snapshot',
                ?, 'Blueprint-to-Code', ?, ?, 'FRESH'
            )
            """,
            (source_fingerprint, CATALOG_SCHEMA_VERSION, generated_at),
        )
        _metadata(
            connection,
            {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "source_fingerprint": source_fingerprint,
                "generated_at": generated_at,
            },
        )
        connection.execute(
            """
            INSERT INTO content_packs(mount_point, label)
            SELECT DISTINCT mount_point, mount_point
            FROM discovery.assets
            ORDER BY mount_point
            """
        )
        connection.execute(
            """
            INSERT INTO packages(
                package_path, mount_point, content_pack_id,
                current_revision_id, plugin_or_dlc, source_fingerprint
            )
            SELECT
                a.package_path,
                MIN(a.mount_point),
                MIN(c.content_pack_id),
                1,
                MIN(a.plugin_or_dlc),
                MIN(a.source_fingerprint)
            FROM discovery.assets AS a
            JOIN content_packs AS c ON c.mount_point=a.mount_point
            GROUP BY a.package_path
            ORDER BY a.package_path
            """
        )
        connection.execute(
            """
            INSERT INTO catalog_nodes(canonical_uri, node_kind, package_id)
            SELECT a.object_path, 'ASSET', p.package_id
            FROM discovery.assets AS a
            LEFT JOIN packages AS p ON p.package_path=a.package_path
            ORDER BY a.object_path
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO catalog_nodes(
                canonical_uri, node_kind, package_id
            )
            SELECT package_path, 'PACKAGE', package_id
            FROM packages
            ORDER BY package_path
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO catalog_nodes(canonical_uri, node_kind)
            SELECT source_object_path, 'REFERENCE_IDENTITY'
            FROM discovery.asset_references
            WHERE source_object_path <> ''
            UNION
            SELECT target_object_path, 'REFERENCE_IDENTITY'
            FROM discovery.asset_references
            WHERE target_object_path <> ''
            ORDER BY 1
            """
        )
        connection.execute(
            """
            INSERT INTO catalog_assets
            SELECT
                n.node_id,
                a.asset_name,
                a.asset_class_path,
                a.generated_class_path,
                a.parent_class_path,
                a.native_parent_class_path,
                a.is_blueprint,
                a.is_map,
                a.file_size_total,
                a.source_fingerprint,
                a.source_modified,
                a.identity_status,
                a.identity_confidence,
                a.evidence_freshness
            FROM discovery.assets AS a
            JOIN catalog_nodes AS n ON n.canonical_uri=a.object_path
            ORDER BY n.node_id
            """
        )
        connection.execute(
            """
            INSERT INTO edge_types(edge_type)
            SELECT DISTINCT edge_kind
            FROM discovery.asset_references
            ORDER BY edge_kind
            """
        )
        connection.execute(
            """
            INSERT INTO edge_strengths(edge_strength)
            SELECT DISTINCT reference_strength
            FROM discovery.asset_references
            ORDER BY reference_strength
            """
        )
        connection.execute(
            """
            INSERT INTO source_properties(source_property)
            SELECT DISTINCT source_property
            FROM discovery.asset_references
            ORDER BY source_property
            """
        )
        connection.execute(
            """
            INSERT INTO catalog_edges(
                source_node_id, target_node_id, edge_type_id,
                edge_strength_id, source_property_id, source_revision_id,
                confidence, source_kind
            )
            SELECT
                source.node_id,
                target.node_id,
                kind.edge_type_id,
                strength.edge_strength_id,
                property.source_property_id,
                1,
                r.confidence,
                r.source_kind
            FROM discovery.asset_references AS r
            JOIN catalog_nodes AS source
              ON source.canonical_uri=r.source_object_path
            JOIN catalog_nodes AS target
              ON target.canonical_uri=r.target_object_path
            JOIN edge_types AS kind ON kind.edge_type=r.edge_kind
            JOIN edge_strengths AS strength
              ON strength.edge_strength=r.reference_strength
            JOIN source_properties AS property
              ON property.source_property=r.source_property
            ORDER BY r.reference_id
            """
        )
        connection.execute(
            """
            INSERT INTO catalog_coverage
            SELECT
                n.node_id,
                c.stage,
                CASE
                  WHEN SUM(c.stale_count) > 0 THEN 'STALE'
                  WHEN SUM(c.not_recovered_count) > 0 THEN 'NOT_RECOVERED'
                  WHEN SUM(c.ambiguous_count) > 0 THEN 'AMBIGUOUS'
                  ELSE MIN(c.status)
                END,
                SUM(
                    c.ambiguous_count + c.not_recovered_count
                    + c.source_not_available_count
                ),
                SUM(c.stale_count)
            FROM discovery.coverage AS c
            JOIN catalog_nodes AS n ON n.canonical_uri=c.object_path
            GROUP BY n.node_id, c.stage
            """
        )
        connection.execute(
            """
            INSERT INTO source_inventory
            SELECT * FROM discovery.source_inventory
            """
        )
        connection.executescript(CATALOG_INDEX_SQL)
        connection.execute("ANALYZE main")
        connection.commit()
        counts = {
            "packages": int(
                connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
            ),
            "nodes": int(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_nodes"
                ).fetchone()[0]
            ),
            "assets": int(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_assets"
                ).fetchone()[0]
            ),
            "edges": int(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_edges"
                ).fetchone()[0]
            ),
        }
        connection.execute("DETACH DATABASE discovery")
        return counts
    finally:
        connection.close()


def _entity_category_map(
    connection: sqlite3.Connection,
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for uri, category in connection.execute(
        """
        SELECT DISTINCT e.canonical_uri, c.category
        FROM entities AS e
        JOIN asset_class_assignments AS a ON a.entity_id=e.entity_id
        JOIN class_ancestry_categories AS c ON c.class_id=a.class_id
        """
    ):
        result.setdefault(str(uri), set()).add(str(category))
    return result


def _materialize_domains(
    connection: sqlite3.Connection,
    ontology: OntologyBundle,
) -> int:
    category_to_domains: dict[str, list[str]] = {}
    for domain_id, definition in ontology.domains.items():
        for category in definition.class_categories:
            category_to_domains.setdefault(category, []).append(domain_id)
    rows: list[tuple[object, ...]] = []
    for entity_id, category, class_id in connection.execute(
        """
        SELECT DISTINCT a.entity_id, c.category, c.ancestor_class_id
        FROM asset_class_assignments AS a
        JOIN class_ancestry_categories AS c ON c.class_id=a.class_id
        """
    ):
        for domain_id in category_to_domains.get(str(category), []):
            rows.append(
                (
                    int(entity_id),
                    domain_id,
                    "CLASS_ANCESTRY",
                    "HIGH",
                    "CONFIRMED",
                    f"class-category://{class_id}/{category}",
                    ontology.version,
                )
            )
    if rows:
        connection.executemany(
            """
            INSERT OR IGNORE INTO domain_memberships VALUES (
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
    return len(rows)


def _registration_entity_id(
    connection: sqlite3.Connection,
    uri: str,
) -> int | None:
    row = connection.execute(
        "SELECT entity_id FROM entities WHERE canonical_uri=?",
        (uri,),
    ).fetchone()
    if row:
        return int(row[0])
    row = connection.execute(
        """
        SELECT a.entity_id
        FROM classes AS c
        JOIN asset_class_assignments AS a ON a.class_id=c.class_id
        WHERE c.class_path=?
          AND a.assignment_kind='GENERATED_CLASS'
        LIMIT 1
        """,
        (uri,),
    ).fetchone()
    return None if row is None else int(row[0])


def _materialize_registration_edges(
    connection: sqlite3.Connection,
    ontology: OntologyBundle,
) -> tuple[int, int]:
    edge_rows: list[tuple[object, ...]] = []
    domain_rows: list[tuple[object, ...]] = []
    for row in connection.execute(
        """
        SELECT
            owner_uri, target_uri, registration_type, source_property,
            evidence_uri, confidence, status
        FROM typed_registrations
        """
    ):
        owner_uri, target_uri = str(row[0]), str(row[1])
        owner_id = _registration_entity_id(connection, owner_uri)
        target_id = _registration_entity_id(connection, target_uri)
        if owner_id is None or target_id is None:
            continue
        registration_type = str(row[2])
        edge_rows.append(
            (
                owner_id,
                target_id,
                "REGISTERS",
                "HARD",
                str(row[6]),
                str(row[5]),
                1,
                str(row[4]),
                str(row[3]),
                "",
            )
        )
        for entity_id, membership_context in (
            (
                owner_id,
                {
                    "entity_uri": owner_uri,
                    "registration_types": ["global_asset_reference"],
                },
            ),
            (
                target_id,
                {
                    "entity_uri": target_uri,
                    "registration_types": [registration_type],
                },
            ),
        ):
            for membership in infer_domain_memberships(
                ontology, membership_context
            ):
                domain_rows.append(
                    (
                        entity_id,
                        membership.domain_id,
                        membership.membership_kind,
                        membership.confidence,
                        membership.status,
                        membership.evidence_id,
                        ontology.version,
                    )
                )
    if edge_rows:
        connection.executemany(
            """
            INSERT INTO edges(
                source_entity_id, target_entity_id, edge_type,
                edge_strength, status, confidence, source_revision_id,
                evidence_uri, source_property, source_graph
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            edge_rows,
        )
    if domain_rows:
        connection.executemany(
            """
            INSERT OR IGNORE INTO domain_memberships VALUES (
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            domain_rows,
        )
    return len(edge_rows), len(domain_rows)


def build_core_database(
    *,
    discovery_path: Path,
    capture_root: Path,
    output_path: Path,
    source_fingerprint: str,
    generated_at: str,
    ontology: OntologyBundle,
    legacy_kb_root: Path,
    native_gold_set_path: Path,
) -> dict[str, int]:
    connection = _connect(output_path, FULL_CORE_SCHEMA_SQL)
    discovery = sqlite3.connect(
        f"file:{discovery_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    try:
        _attach_read_only(connection, discovery_path, "discovery")
        connection.execute(
            """
            INSERT INTO source_revisions VALUES (
                1, 'discovery', 'discovery://ark/full-snapshot',
                ?, 'Blueprint-to-Code', ?, ?, 'FRESH'
            )
            """,
            (source_fingerprint, CORE_SCHEMA_VERSION, generated_at),
        )
        _metadata(
            connection,
            {
                "schema_version": CORE_SCHEMA_VERSION,
                "ontology_version": ontology.version,
                "source_fingerprint": source_fingerprint,
                "generated_at": generated_at,
            },
        )
        connection.execute(
            """
            INSERT INTO packages(
                package_path, mount_point, current_revision_id
            )
            SELECT package_path, MIN(mount_point), 1
            FROM discovery.assets
            GROUP BY package_path
            ORDER BY package_path
            """
        )
        connection.execute(
            """
            INSERT INTO entities(
                canonical_uri, entity_kind, package_id, class_id,
                display_name, internal_name, status, confidence
            )
            SELECT
                a.object_path,
                CASE
                  WHEN a.is_map=1 THEN 'MAP_ASSET'
                  WHEN a.is_blueprint=1 THEN 'BLUEPRINT_ASSET'
                  ELSE 'ASSET'
                END,
                p.package_id,
                NULL,
                a.asset_name,
                a.asset_name,
                a.identity_status,
                a.identity_confidence
            FROM discovery.assets AS a
            LEFT JOIN packages AS p ON p.package_path=a.package_path
            ORDER BY a.object_path
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO aliases(
                alias, entity_id, alias_kind, language, confidence
            )
            SELECT
                COALESCE(internal_name, ''),
                entity_id,
                'INTERNAL_NAME',
                '',
                confidence
            FROM entities
            WHERE internal_name <> ''
            """
        )
        connection.execute(
            """
            INSERT INTO coverage
            SELECT
                e.entity_id,
                c.stage,
                c.status,
                c.confirmed_count,
                c.heuristic_count,
                c.ambiguous_count,
                c.not_recovered_count,
                c.source_not_available_count,
                c.stale_count,
                c.failure_reason
            FROM discovery.coverage AS c
            JOIN entities AS e ON e.canonical_uri=c.object_path
            """
        )
        connection.commit()
        connection.execute("DETACH DATABASE discovery")
        class_counts = materialize_discovery_classes(
            discovery,
            connection,
            source_revision_id=1,
        )
        connection.execute(
            """
            UPDATE entities
            SET class_id=(
                SELECT class_id
                FROM asset_class_assignments AS a
                WHERE a.entity_id=entities.entity_id
                ORDER BY
                    CASE a.assignment_kind
                      WHEN 'GENERATED_CLASS' THEN 0 ELSE 1
                    END
                LIMIT 1
            )
            """
        )
        role_counts = materialize_discovery_roles(discovery, connection)
        category_map = _entity_category_map(connection)
        registration_counts = materialize_typed_registrations(
            discovery,
            connection,
            source_revision_id=1,
            target_categories_by_uri=category_map,
        )
        domain_count = _materialize_domains(connection, ontology)
        registration_edge_count, registration_domain_count = (
            _materialize_registration_edges(connection, ontology)
        )
        blueprint_result = materialize_blueprint_defaults(
            discovery,
            connection,
            capture_root=capture_root,
            ontology=ontology,
        )
        materialize_declared_defaults(
            discovery,
            connection,
            ontology=ontology,
            source_revision_id=1,
            covered_properties=blueprint_result.covered_properties,
            freshness_gap_assets=blueprint_result.freshness_gap_assets,
            untrusted_assets=blueprint_result.untrusted_assets,
        )
        fact_counts = {
            "declaredFacts": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM facts
                    WHERE fact_type='DECLARED_DEFAULT' AND current=1
                    """
                ).fetchone()[0]
            ),
            "factEvidence": int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM fact_evidence AS evidence
                    JOIN facts AS fact ON fact.fact_id=evidence.fact_id
                    WHERE fact.fact_type='DECLARED_DEFAULT'
                      AND fact.current=1
                    """
                ).fetchone()[0]
            ),
            "notRecoveredFacts": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM facts
                    WHERE fact_type='DECLARED_DEFAULT'
                      AND current=1
                      AND status IN (
                          'UNKNOWN', 'NOT_RECOVERED',
                          'SOURCE_NOT_AVAILABLE'
                      )
                    """
                ).fetchone()[0]
            ),
        }
        effective_counts = materialize_effective_defaults(connection)
        native_counts = materialize_native_gold_set(
            discovery,
            connection,
            config_path=native_gold_set_path,
            generated_at=generated_at,
        )
        legacy_counts = import_legacy_lineage(
            core=connection,
            legacy_root=legacy_kb_root,
            generated_at=generated_at,
        )
        invalidation_counts = rebuild_invalidation_dependencies(connection)
        benchmark_counts = materialize_benchmark_queries(connection)
        connection.execute("ANALYZE main")
        connection.commit()
        return {
            "packages": int(
                connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
            ),
            "entities": int(
                connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            ),
            "coverage": int(
                connection.execute("SELECT COUNT(*) FROM coverage").fetchone()[0]
            ),
            "domainMemberships": domain_count + registration_domain_count,
            "registrationEdges": registration_edge_count,
            **class_counts,
            **role_counts,
            **registration_counts,
            **fact_counts,
            **{
                "blueprint" + key[0].upper() + key[1:]: value
                for key, value in blueprint_result.counts.items()
            },
            **effective_counts,
            **native_counts,
            **benchmark_counts,
            "invalidationDependencies": sum(
                invalidation_counts.values()
            ),
            "legacyRows": int(legacy_counts["rows"]),
            "legacyResolvedEntities": int(
                legacy_counts["resolvedEntities"]
            ),
            "legacyUnverified": int(legacy_counts["legacyUnverified"]),
        }
    finally:
        discovery.close()
        connection.close()


def build_search_database(
    *,
    core_path: Path,
    output_path: Path,
    source_fingerprint: str,
    generated_at: str,
) -> dict[str, int]:
    connection = _connect(output_path, SEARCH_SCHEMA_SQL)
    core = sqlite3.connect(
        f"file:{core_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    core.row_factory = sqlite3.Row
    try:
        _metadata(
            connection,
            {
                "schema_version": SEARCH_SCHEMA_VERSION,
                "source_fingerprint": source_fingerprint,
                "generated_at": generated_at,
            },
        )
        cursor = core.execute(
            """
            SELECT
                entity_id, canonical_uri, entity_kind,
                COALESCE(display_name, '') AS display_name,
                COALESCE(internal_name, '') AS internal_name,
                status
            FROM entities
            ORDER BY entity_id
            """
        )
        while batch := cursor.fetchmany(10_000):
            connection.executemany(
                "INSERT INTO entity_search_meta VALUES (?, ?, ?, ?, ?, ?)",
                [tuple(row) for row in batch],
            )
            connection.executemany(
                "INSERT INTO entities_fts VALUES (?, ?, ?, ?, '')",
                [
                    (
                        row["entity_id"],
                        row["canonical_uri"],
                        row["display_name"],
                        row["internal_name"],
                    )
                    for row in batch
                ],
            )
        alias_cursor = core.execute(
            """
            SELECT alias, entity_id, alias_kind, language, confidence
            FROM aliases
            ORDER BY alias, entity_id, alias_kind
            """
        )
        while batch := alias_cursor.fetchmany(10_000):
            connection.executemany(
                "INSERT INTO search_aliases VALUES (?, ?, ?, ?, ?)",
                [tuple(row) for row in batch],
            )
        connection.execute("ANALYZE main")
        connection.commit()
        return {
            "entities": int(
                connection.execute(
                    "SELECT COUNT(*) FROM entity_search_meta"
                ).fetchone()[0]
            ),
            "aliases": int(
                connection.execute(
                    "SELECT COUNT(*) FROM search_aliases"
                ).fetchone()[0]
            ),
            "ftsRows": int(
                connection.execute(
                    "SELECT COUNT(*) FROM entities_fts"
                ).fetchone()[0]
            ),
        }
    finally:
        core.close()
        connection.close()


def build_cache_database(
    *,
    output_path: Path,
    source_fingerprint: str,
    generated_at: str,
) -> dict[str, int]:
    connection = _connect(output_path, CACHE_SCHEMA_SQL)
    try:
        _metadata(
            connection,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "source_fingerprint": source_fingerprint,
                "generated_at": generated_at,
                "disposable": "true",
            },
        )
        connection.commit()
        return {
            "querySnapshots": 0,
            "contextPacks": 0,
            "answerPlans": 0,
        }
    finally:
        connection.close()


def database_metrics(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        return {
            "bytes": path.stat().st_size,
            "integrity": str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            ),
            "foreignKeyViolations": len(
                list(connection.execute("PRAGMA foreign_key_check"))
            ),
            "tables": {
                table: int(
                    connection.execute(
                        'SELECT COUNT(*) FROM "'
                        + table.replace('"', '""')
                        + '"'
                    ).fetchone()[0]
                )
                for table in tables
                if not table.startswith("entities_fts_")
            },
        }
    finally:
        connection.close()


def source_revision_hash(fingerprints: Mapping[str, str]) -> str:
    payload = json.dumps(
        dict(sorted(fingerprints.items())),
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
