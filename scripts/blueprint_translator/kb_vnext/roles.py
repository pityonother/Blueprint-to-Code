"""Explainable multi-role and depth-policy classification for KB vNext.

Raw popularity is intentionally not a semantic qualification.  The classifier
keeps type-normalized measurements separate, requires confirmed structural
evidence for reusable/background roles, and defaults presentation assets to a
bounded catalog entry.
"""

from __future__ import annotations

import math
import json
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from .registrations import registration_provenance_is_confirmed


ROLE_CLASSIFIER_VERSION = "ark-kb-roles/v1"
KNOWLEDGE_ROLES = (
    "catalog_asset",
    "global_system_hub",
    "reusable_base_class",
    "reusable_component",
    "function_library",
    "blueprint_interface",
    "domain_rule_asset",
    "registration_owner",
    "entity_definition",
    "leaf_variant",
    "map_placement_asset",
    "visual_support_asset",
    "configuration_asset",
    "native_runtime_implementation",
    "query_snapshot",
    "unknown_role",
)

DEPTH_POLICIES = (
    "INDEX_ONLY",
    "STRUCTURE",
    "SEMANTIC",
    "DEEP",
    "ON_DEMAND",
    "BLOCKED_UNKNOWN",
)

PERCENTILE_METRICS = (
    ("descendant_count", "descendant_percentile"),
    ("referencer_count", "referencer_percentile"),
    ("component_reuse_count", "component_reuse_percentile"),
    ("cross_domain_reference_count", "cross_domain_percentile"),
    ("registration_count", "registration_percentile"),
    ("query_demand_count", "query_demand_percentile"),
)

ROLE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS role_metrics (
    entity_id INTEGER PRIMARY KEY,
    percentile_group TEXT NOT NULL,
    descendant_count INTEGER NOT NULL,
    descendant_log1p REAL NOT NULL,
    descendant_percentile REAL NOT NULL,
    referencer_count INTEGER NOT NULL,
    referencer_log1p REAL NOT NULL,
    referencer_percentile REAL NOT NULL,
    component_reuse_count INTEGER NOT NULL,
    component_reuse_log1p REAL NOT NULL,
    component_reuse_percentile REAL NOT NULL,
    cross_domain_reference_count INTEGER NOT NULL,
    cross_domain_reference_log1p REAL NOT NULL,
    cross_domain_percentile REAL NOT NULL,
    registration_count INTEGER NOT NULL,
    registration_log1p REAL NOT NULL,
    registration_percentile REAL NOT NULL,
    query_hit_count INTEGER,
    query_hit_status TEXT NOT NULL,
    existing_report_count INTEGER,
    existing_report_status TEXT NOT NULL,
    distinct_query_domain_count INTEGER NOT NULL,
    repeated_fact_demand_count INTEGER NOT NULL,
    query_demand_count INTEGER NOT NULL,
    query_demand_log1p REAL NOT NULL,
    query_demand_percentile REAL NOT NULL,
    semantic_qualifications_json TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
);

CREATE TABLE IF NOT EXISTS knowledge_roles (
    entity_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    source_revision_id INTEGER,
    PRIMARY KEY(entity_id, role),
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
);

CREATE TABLE IF NOT EXISTS knowledge_depth_policies (
    entity_id INTEGER PRIMARY KEY,
    depth_policy TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
);

CREATE INDEX IF NOT EXISTS idx_role_metrics_group
    ON role_metrics(percentile_group);
CREATE INDEX IF NOT EXISTS idx_knowledge_roles_role
    ON knowledge_roles(role, confidence, status);
CREATE INDEX IF NOT EXISTS idx_depth_policy
    ON knowledge_depth_policies(depth_policy);
"""

OPEN_STATES = {
    "UNKNOWN",
    "AMBIGUOUS",
    "NOT_RECOVERED",
    "NOT_MEASURED",
    "SOURCE_NOT_AVAILABLE",
}
UNRECOVERED_REVISION_VALUES = frozenset(
    {
        *OPEN_STATES,
        "UNRESOLVED",
        "LEGACY_UNVERIFIED",
        "CONFIRMED_FINGERPRINT_ONLY",
        "NOT_AVAILABLE",
        "UNAVAILABLE",
    }
)

VISUAL_CLASS_NAMES = {
    "animationsequence",
    "animsequence",
    "animmontage",
    "font",
    "material",
    "materialinstance",
    "materialinstanceconstant",
    "niagarasystem",
    "particle",
    "particlesystem",
    "poseasset",
    "skeletalmesh",
    "sound",
    "soundcue",
    "soundwave",
    "staticmesh",
    "texture",
    "texture2d",
    "uiimage",
}


@dataclass(frozen=True)
class RoleAssignment:
    role: str
    confidence: str
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RoleDecision:
    roles: tuple[RoleAssignment, ...]
    depth_policy: str
    depth_reasons: tuple[str, ...]
    semantic_qualifications: tuple[str, ...]

    def role_names(self) -> set[str]:
        return {assignment.role for assignment in self.roles}


def _text(row: Mapping[str, object], key: str, default: str = "") -> str:
    value = row.get(key)
    return default if value is None else str(value)


def _integer(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _number(row: Mapping[str, object], key: str) -> float:
    value = row.get(key)
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _truthy(row: Mapping[str, object], key: str) -> bool:
    value = row.get(key)
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "confirmed"}
    return bool(value)


def _class_leaf(value: str) -> str:
    leaf = value.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    return leaf.removesuffix("_C").casefold()


def _is_visual_asset(row: Mapping[str, object]) -> bool:
    explicit_category = _text(row, "semantic_class_category").casefold()
    if explicit_category == "visual_support":
        return True
    class_leaf = _class_leaf(_text(row, "asset_class_path"))
    return any(
        class_leaf == name or class_leaf.startswith(name)
        for name in VISUAL_CLASS_NAMES
    )


def _identity_open(row: Mapping[str, object]) -> bool:
    status = _text(row, "identity_status", "UNKNOWN").upper()
    class_path = _text(row, "asset_class_path", "UNKNOWN").upper()
    return status in OPEN_STATES or class_path in {"", "UNKNOWN"}


def _fresh_semantic_evidence(row: Mapping[str, object]) -> bool:
    freshness = _text(row, "evidence_freshness", "UNKNOWN").upper()
    parse_status = _text(row, "parse_status", "UNKNOWN").upper()
    return (
        _truthy(row, "capture_exists")
        and freshness not in OPEN_STATES | {"STALE"}
        and parse_status not in OPEN_STATES
        and (
            _integer(row, "graph_count") > 0
            or _integer(row, "default_property_count") > 0
            or _integer(row, "confirmed_fact_count") > 0
        )
    )


def _mechanism_visual_evidence(row: Mapping[str, object]) -> bool:
    return any(
        _integer(row, field) > 0
        for field in (
            "animation_notify_mechanism_count",
            "curve_mechanism_count",
            "collision_mechanism_count",
            "material_parameter_input_count",
            "world_placement_evidence_count",
        )
    )


def _semantic_qualifications(row: Mapping[str, object]) -> list[str]:
    qualifications: list[str] = []
    identity_confirmed = (
        not _identity_open(row)
        and _text(row, "identity_confidence", "UNKNOWN").upper()
        in {"CONFIRMED", "HIGH"}
    )
    if (
        identity_confirmed
        and _text(row, "generated_class_path", "UNKNOWN")
        not in {"", "UNKNOWN"}
        and _text(row, "parent_class_path", "UNKNOWN") not in {"", "UNKNOWN"}
    ):
        qualifications.append("confirmed_class_identity")
    if _integer(row, "registration_owner_count") > 0:
        qualifications.append("confirmed_registration_owner")
    if (
        _truthy(row, "is_actor_component")
        and _integer(row, "component_reuse_count") > 0
    ):
        qualifications.append("confirmed_reusable_component_class")
    if _truthy(row, "is_function_library"):
        qualifications.append("confirmed_function_library")
    if _truthy(row, "is_blueprint_interface"):
        qualifications.append("confirmed_blueprint_interface")
    if (
        identity_confirmed
        and _integer(row, "descendant_count") > 0
        and _text(row, "generated_class_path", "UNKNOWN")
        not in {"", "UNKNOWN"}
    ):
        qualifications.append("confirmed_blueprint_descendants")
    if (
        identity_confirmed
        and _integer(row, "cross_domain_reference_count") > 1
        and _integer(row, "confirmed_cross_domain_evidence_count") > 0
    ):
        qualifications.append("confirmed_cross_domain_evidence")
    if _integer(row, "confirmed_formula_count") > 0:
        qualifications.append("confirmed_public_rule_or_formula")
    if (
        _integer(row, "query_hit_count") > 0
        and _integer(row, "existing_report_count") > 0
    ):
        qualifications.append("repeated_query_and_report_demand")
    return qualifications


def _assignment(
    role: str,
    *reasons: str,
    confidence: str = "HIGH",
    status: str = "CONFIRMED",
) -> RoleAssignment:
    if role not in KNOWLEDGE_ROLES:
        raise ValueError(f"Unknown knowledge role: {role}")
    return RoleAssignment(
        role=role,
        confidence=confidence,
        status=status,
        reasons=tuple(reasons),
    )


def classify_asset(row: Mapping[str, object]) -> RoleDecision:
    """Classify one asset without using names or raw popularity as promotion."""

    roles: list[RoleAssignment] = [
        _assignment("catalog_asset", "registry_or_inventory_identity")
    ]
    qualifications = _semantic_qualifications(row)
    identity_open = _identity_open(row)
    visual = _is_visual_asset(row)
    visual_mechanism = _mechanism_visual_evidence(row)

    if identity_open:
        roles.append(
            _assignment(
                "unknown_role",
                "identity_or_class_not_closed",
                confidence="UNKNOWN",
                status=_text(row, "identity_status", "UNKNOWN").upper(),
            )
        )

    if visual:
        roles.append(
            _assignment(
                "visual_support_asset",
                (
                    "confirmed_mechanism_visual_evidence"
                    if visual_mechanism
                    else "presentation_class_default"
                ),
            )
        )

    if _truthy(row, "is_map") or _integer(
        row, "world_placement_evidence_count"
    ) > 0:
        roles.append(
            _assignment(
                "map_placement_asset",
                "confirmed_map_or_world_placement_identity",
            )
        )

    if _truthy(row, "is_data_asset") or _truthy(row, "is_data_table"):
        roles.append(
            _assignment(
                "configuration_asset",
                "confirmed_data_asset_or_table_ancestry",
            )
        )

    if _truthy(row, "is_function_library"):
        roles.append(
            _assignment("function_library", "confirmed_function_library")
        )
    if _truthy(row, "is_blueprint_interface"):
        roles.append(
            _assignment("blueprint_interface", "confirmed_blueprint_interface")
        )

    registration_owner_count = _integer(row, "registration_owner_count")
    if registration_owner_count > 0:
        roles.append(
            _assignment(
                "registration_owner",
                "typed_registration_owner",
                f"registration_owner_count={registration_owner_count}",
            )
        )
        if (
            _number(row, "registration_percentile") >= 0.90
            and (
                registration_owner_count >= 2
                or _integer(row, "distinct_registration_type_count") >= 2
            )
        ):
            roles.append(
                _assignment(
                    "global_system_hub",
                    "confirmed_registration_owner",
                    "registration_percentile>=0.90",
                )
            )

    if (
        not visual
        and "confirmed_class_identity" in qualifications
        and _integer(row, "descendant_count") >= 5
        and _number(row, "descendant_percentile") >= 0.95
    ):
        roles.append(
            _assignment(
                "reusable_base_class",
                "confirmed_class_identity",
                "descendant_count>=5",
                "descendant_percentile>=0.95",
            )
        )

    if (
        not visual
        and _truthy(row, "is_actor_component")
        and _integer(row, "component_reuse_count") >= 3
        and _number(row, "component_reuse_percentile") >= 0.95
    ):
        roles.append(
            _assignment(
                "reusable_component",
                "confirmed_actor_component_ancestry",
                "component_reuse_count>=3",
                "component_reuse_percentile>=0.95",
            )
        )

    rule_evidence = (
        "confirmed_public_rule_or_formula" in qualifications
        or (
            "confirmed_cross_domain_evidence" in qualifications
            and "repeated_query_and_report_demand" in qualifications
        )
    )
    if not visual and rule_evidence:
        roles.append(
            _assignment(
                "domain_rule_asset",
                (
                    "confirmed_public_rule_or_formula"
                    if "confirmed_public_rule_or_formula" in qualifications
                    else "confirmed_cross_domain_and_repeated_demand"
                ),
            )
        )

    if _truthy(row, "is_blueprint") or _truthy(
        row, "is_data_asset"
    ) or _truthy(row, "is_data_table"):
        roles.append(
            _assignment("entity_definition", "confirmed_semantic_asset_identity")
        )

    if (
        not visual
        and not identity_open
        and _text(row, "parent_class_path", "UNKNOWN") not in {"", "UNKNOWN"}
        and _integer(row, "descendant_count") == 0
        and registration_owner_count == 0
        and not _truthy(row, "is_function_library")
        and not _truthy(row, "is_blueprint_interface")
    ):
        roles.append(
            _assignment(
                "leaf_variant",
                "confirmed_parent",
                "no_descendants",
                "not_registration_owner",
            )
        )

    if _integer(row, "native_confirmed_count") > 0 or _text(
        row, "entity_kind"
    ).casefold() == "native_function":
        roles.append(
            _assignment(
                "native_runtime_implementation",
                "confirmed_native_evidence_identity",
            )
        )

    role_names = {item.role for item in roles}
    if identity_open:
        depth = "BLOCKED_UNKNOWN"
        depth_reasons = ("identity_or_class_not_closed",)
    elif visual and not visual_mechanism:
        depth = "INDEX_ONLY"
        depth_reasons = ("presentation_asset_without_mechanism_evidence",)
    elif role_names & {
        "global_system_hub",
        "reusable_base_class",
        "reusable_component",
        "function_library",
        "blueprint_interface",
        "domain_rule_asset",
        "native_runtime_implementation",
    }:
        depth = "DEEP"
        depth_reasons = (
            "confirmed_reusable_or_system_role",
            "deep_evidence_required_for_shared_mechanism",
        )
    elif (
        "registration_owner" in role_names
        or (
            "entity_definition" in role_names
            and _fresh_semantic_evidence(row)
        )
    ):
        depth = "SEMANTIC"
        depth_reasons = ("confirmed_semantic_or_registration_evidence",)
    elif (
        visual_mechanism
        or "configuration_asset" in role_names
        or (
            "entity_definition" in role_names
            and _truthy(row, "capture_exists")
        )
    ):
        depth = "STRUCTURE"
        depth_reasons = ("structure_or_defaults_needed",)
    elif "leaf_variant" in role_names:
        depth = "ON_DEMAND"
        depth_reasons = ("leaf_variant_without_repeated_demand",)
    else:
        depth = "INDEX_ONLY"
        depth_reasons = ("catalog_identity_is_currently_sufficient",)

    if depth not in DEPTH_POLICIES:
        raise AssertionError(depth)
    return RoleDecision(
        roles=tuple(roles),
        depth_policy=depth,
        depth_reasons=depth_reasons,
        semantic_qualifications=tuple(qualifications),
    )


def _query_demand_count(row: Mapping[str, object]) -> int:
    return sum(
        _integer(row, field)
        for field in (
            "query_hit_count",
            "existing_report_count",
            "distinct_query_domain_count",
            "repeated_fact_demand_count",
        )
    )


def _registration_count(row: Mapping[str, object]) -> int:
    return (
        _integer(row, "registration_owner_count")
        + _integer(row, "registry_usage_count")
    )


def enrich_type_percentiles(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Add raw/log1p/type-percentile fields without collapsing to one score."""

    enriched: list[dict[str, object]] = []
    for source in rows:
        row = dict(source)
        row["registration_count"] = _registration_count(row)
        row["query_demand_count"] = _query_demand_count(row)
        enriched.append(row)

    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(enriched):
        group = _text(row, "semantic_class_category") or _text(
            row, "asset_class_path", "UNKNOWN"
        )
        row["percentile_group"] = group
        groups[group].append(index)

    for indexes in groups.values():
        for raw_field, percentile_field in PERCENTILE_METRICS:
            values = [_integer(enriched[index], raw_field) for index in indexes]
            logs = [math.log1p(max(0, value)) for value in values]
            ordered = sorted(logs)
            log_field = raw_field.removesuffix("_count") + "_log1p"
            for index, log_value in zip(indexes, logs, strict=True):
                enriched[index][log_field] = log_value
                enriched[index][percentile_field] = (
                    bisect_right(ordered, log_value) / len(ordered)
                )
    return enriched


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def create_role_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(ROLE_TABLES_SQL)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name=?
            """,
            (table_name,),
        ).fetchone()
        is not None
    )


def _is_recovered_revision_value(value: object) -> bool:
    text = str(value or "").strip()
    normalized = "_".join(
        text.upper().replace("-", " ").replace("_", " ").split()
    )
    return bool(text) and normalized not in UNRECOVERED_REVISION_VALUES


def _canonical_registration_entity_uris(
    target: sqlite3.Connection,
) -> set[str]:
    if not _table_exists(target, "entities"):
        return set()
    result = {
        str(row[0])
        for row in target.execute(
            "SELECT canonical_uri FROM entities"
        )
        if str(row[0] or "").strip()
    }
    if not (
        _table_exists(target, "classes")
        and _table_exists(target, "asset_class_assignments")
    ):
        return result
    result.update(
        str(row[0])
        for row in target.execute(
            """
            SELECT DISTINCT class.class_path
            FROM classes AS class
            JOIN asset_class_assignments AS assignment
              ON assignment.class_id=class.class_id
            JOIN entities AS entity
              ON entity.entity_id=assignment.entity_id
            WHERE assignment.assignment_kind='GENERATED_CLASS'
            """
        )
        if str(row[0] or "").strip()
    )
    return result


def _prepare_canonical_registration_counts(
    discovery: sqlite3.Connection,
    target: sqlite3.Connection,
) -> None:
    discovery.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS kb_vnext_registration_counts(
            owner_uri TEXT PRIMARY KEY,
            registration_owner_count INTEGER NOT NULL,
            distinct_registration_type_count INTEGER NOT NULL
        ) WITHOUT ROWID
        """
    )
    discovery.execute("DELETE FROM temp.kb_vnext_registration_counts")
    if not (
        _table_exists(target, "typed_registrations")
        and _table_exists(target, "source_revisions")
    ):
        return

    canonical_entity_uris = _canonical_registration_entity_uris(target)
    registration_count_by_owner: dict[str, int] = defaultdict(int)
    registration_types_by_owner: dict[str, set[str]] = defaultdict(set)
    for row in target.execute(
        """
        SELECT
            registration.owner_uri,
            registration.target_uri,
            registration.registration_type,
            registration.evidence_uri,
            registration.status,
            registration.confidence,
            revision.source_kind,
            revision.source_uri,
            revision.source_fingerprint,
            revision.producer_version,
            revision.schema_version,
            revision.generated_at,
            revision.freshness_status
        FROM typed_registrations AS registration
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=registration.source_revision_id
        """
    ):
        owner_uri = str(row[0] or "").strip()
        target_uri = str(row[1] or "").strip()
        registration_type = str(row[2] or "")
        evidence_uri = str(row[3] or "")
        status = str(row[4] or "").upper()
        confidence = str(row[5] or "").upper()
        revision_is_fresh = (
            str(row[12] or "").upper() == "FRESH"
            and all(
                _is_recovered_revision_value(value)
                for value in row[6:12]
            )
        )
        if (
            not registration_provenance_is_confirmed(
                status,
                confidence,
                evidence_uri,
            )
            or not revision_is_fresh
            or not registration_type
            or owner_uri not in canonical_entity_uris
            or target_uri not in canonical_entity_uris
        ):
            continue
        registration_count_by_owner[owner_uri] += 1
        registration_types_by_owner[owner_uri].add(registration_type)

    discovery.executemany(
        """
        INSERT INTO temp.kb_vnext_registration_counts VALUES (?, ?, ?)
        """,
        (
            (
                owner_uri,
                registration_count_by_owner[owner_uri],
                len(types),
            )
            for owner_uri, types in registration_types_by_owner.items()
        ),
    )


def _source_role_rows(
    discovery: sqlite3.Connection,
) -> sqlite3.Cursor:
    discovery.row_factory = sqlite3.Row
    return discovery.execute(
        """
        WITH registration_counts AS (
            SELECT
                owner_uri AS owner_object_path,
                registration_owner_count,
                distinct_registration_type_count
            FROM temp.kb_vnext_registration_counts
        )
        SELECT
            a.*,
            COALESCE(r.registration_owner_count, 0)
                AS registration_owner_count,
            COALESCE(r.distinct_registration_type_count, 0)
                AS distinct_registration_type_count,
            0 AS distinct_query_domain_count,
            0 AS repeated_fact_demand_count,
            0 AS confirmed_cross_domain_evidence_count,
            0 AS confirmed_formula_count,
            0 AS native_confirmed_count,
            0 AS animation_notify_mechanism_count,
            0 AS curve_mechanism_count,
            0 AS collision_mechanism_count,
            0 AS material_parameter_input_count,
            0 AS world_placement_evidence_count,
            0 AS is_actor_component,
            '' AS semantic_class_category,
            a.asset_class_path AS percentile_group,
            (
                COALESCE(r.registration_owner_count, 0)
                + COALESCE(a.registry_usage_count, 0)
            ) AS registration_count,
            (
                COALESCE(a.query_hit_count, 0)
                + COALESCE(a.existing_report_count, 0)
            ) AS query_demand_count,
            CUME_DIST() OVER (
                PARTITION BY a.asset_class_path
                ORDER BY a.descendant_count
            ) AS descendant_percentile,
            CUME_DIST() OVER (
                PARTITION BY a.asset_class_path
                ORDER BY a.referencer_count
            ) AS referencer_percentile,
            CUME_DIST() OVER (
                PARTITION BY a.asset_class_path
                ORDER BY a.component_reuse_count
            ) AS component_reuse_percentile,
            CUME_DIST() OVER (
                PARTITION BY a.asset_class_path
                ORDER BY a.cross_domain_reference_count
            ) AS cross_domain_percentile,
            CUME_DIST() OVER (
                PARTITION BY a.asset_class_path
                ORDER BY (
                    COALESCE(r.registration_owner_count, 0)
                    + COALESCE(a.registry_usage_count, 0)
                )
            ) AS registration_percentile,
            CUME_DIST() OVER (
                PARTITION BY a.asset_class_path
                ORDER BY (
                    COALESCE(a.query_hit_count, 0)
                    + COALESCE(a.existing_report_count, 0)
                )
            ) AS query_demand_percentile
        FROM assets AS a
        LEFT JOIN registration_counts AS r
          ON r.owner_object_path=a.object_path
        ORDER BY a.asset_class_path, a.object_path
        """
    )


def materialize_discovery_roles(
    discovery: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    source_revision_id: int | None = None,
) -> dict[str, int]:
    """Materialize vNext role/depth rows from a read-only Discovery snapshot."""

    create_role_tables(target)
    target.execute("DELETE FROM role_metrics")
    target.execute("DELETE FROM knowledge_roles")
    target.execute("DELETE FROM knowledge_depth_policies")
    entity_ids = {
        str(uri): int(entity_id)
        for uri, entity_id in target.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    categories_by_entity: dict[int, set[str]] = defaultdict(set)
    if target.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name='class_ancestry_categories'
        """
    ).fetchone():
        for entity_id, category in target.execute(
            """
            SELECT DISTINCT a.entity_id, c.category
            FROM asset_class_assignments AS a
            JOIN class_ancestry_categories AS c
              ON c.class_id=a.class_id
            """
        ):
            categories_by_entity[int(entity_id)].add(str(category))

    asset_count = 0
    role_count = 0
    depth_counts: dict[str, int] = defaultdict(int)
    _prepare_canonical_registration_counts(discovery, target)
    rows = _source_role_rows(discovery)
    while source_batch := rows.fetchmany(10_000):
        metric_rows: list[tuple[object, ...]] = []
        role_rows: list[tuple[object, ...]] = []
        depth_rows: list[tuple[object, ...]] = []
        for source in source_batch:
            row = dict(source)
            entity_uri = _text(row, "object_path")
            entity_id = entity_ids.get(entity_uri)
            if entity_id is None:
                continue
            categories = categories_by_entity.get(entity_id, set())
            row["is_actor_component"] = int("ACTOR_COMPONENT" in categories)
            row["is_data_asset"] = int(
                _truthy(row, "is_data_asset")
                or "DATA_ASSET" in categories
                or "PRIMARY_DATA_ASSET" in categories
            )
            row["descendant_log1p"] = math.log1p(
                max(0, _integer(row, "descendant_count"))
            )
            row["referencer_log1p"] = math.log1p(
                max(0, _integer(row, "referencer_count"))
            )
            row["component_reuse_log1p"] = math.log1p(
                max(0, _integer(row, "component_reuse_count"))
            )
            row["cross_domain_reference_log1p"] = math.log1p(
                max(0, _integer(row, "cross_domain_reference_count"))
            )
            row["registration_log1p"] = math.log1p(
                max(0, _integer(row, "registration_count"))
            )
            row["query_demand_log1p"] = math.log1p(
                max(0, _integer(row, "query_demand_count"))
            )
            decision = classify_asset(row)
            metric_rows.append(
                (
                    entity_id,
                    _text(row, "percentile_group", "UNKNOWN"),
                    _integer(row, "descendant_count"),
                    _number(row, "descendant_log1p"),
                    _number(row, "descendant_percentile"),
                    _integer(row, "referencer_count"),
                    _number(row, "referencer_log1p"),
                    _number(row, "referencer_percentile"),
                    _integer(row, "component_reuse_count"),
                    _number(row, "component_reuse_log1p"),
                    _number(row, "component_reuse_percentile"),
                    _integer(row, "cross_domain_reference_count"),
                    _number(row, "cross_domain_reference_log1p"),
                    _number(row, "cross_domain_percentile"),
                    _integer(row, "registration_count"),
                    _number(row, "registration_log1p"),
                    _number(row, "registration_percentile"),
                    (
                        None
                        if row.get("query_hit_count") is None
                        else _integer(row, "query_hit_count")
                    ),
                    _text(row, "query_hit_status", "NOT_MEASURED"),
                    (
                        None
                        if row.get("existing_report_count") is None
                        else _integer(row, "existing_report_count")
                    ),
                    _text(row, "existing_report_status", "NOT_MEASURED"),
                    _integer(row, "distinct_query_domain_count"),
                    _integer(row, "repeated_fact_demand_count"),
                    _integer(row, "query_demand_count"),
                    _number(row, "query_demand_log1p"),
                    _number(row, "query_demand_percentile"),
                    _json(decision.semantic_qualifications),
                    ROLE_CLASSIFIER_VERSION,
                )
            )
            for assignment in decision.roles:
                role_rows.append(
                    (
                        entity_uri,
                        entity_id,
                        assignment.role,
                        assignment.confidence,
                        assignment.status,
                        _json(assignment.reasons),
                        ROLE_CLASSIFIER_VERSION,
                        source_revision_id,
                    )
                )
            depth_rows.append(
                (
                    entity_id,
                    decision.depth_policy,
                    _json(decision.depth_reasons),
                    ROLE_CLASSIFIER_VERSION,
                )
            )
            asset_count += 1
            role_count += len(decision.roles)
            depth_counts[decision.depth_policy] += 1
        target.executemany(
            """
            INSERT INTO role_metrics VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            metric_rows,
        )
        target.executemany(
            "INSERT INTO knowledge_roles VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    entity_id,
                    role,
                    confidence,
                    status,
                    reasons,
                    version,
                    revision_id,
                )
                for (
                    _entity_uri,
                    entity_id,
                    role,
                    confidence,
                    status,
                    reasons,
                    version,
                    revision_id,
                ) in role_rows
            ],
        )
        target.executemany(
            "INSERT INTO knowledge_depth_policies VALUES (?, ?, ?, ?)",
            depth_rows,
        )
    target.commit()
    return {
        "assets": asset_count,
        "roles": role_count,
        **{
            f"depth_{policy.casefold()}": int(depth_counts.get(policy, 0))
            for policy in DEPTH_POLICIES
        },
    }
