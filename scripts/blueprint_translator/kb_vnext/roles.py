"""Explainable multi-role and depth-policy classification for KB vNext.

Raw popularity is intentionally not a semantic qualification.  The classifier
keeps type-normalized measurements separate, requires confirmed structural
evidence for reusable/background roles, and defaults presentation assets to a
bounded catalog entry.
"""

from __future__ import annotations

import hashlib
import math
import json
import re
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from .native_gold_set import is_recovered_evidence_uri
from .query_planner import source_revision_is_fresh
from .registrations import (
    GLOBAL_REGISTRATION_EDGE_TYPES,
    registration_edge_type,
    registration_provenance_is_confirmed,
)


ROLE_CLASSIFIER_VERSION = "ark-kb-roles/v2"
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

ROLE_SIGNAL_COUNT_FIELDS = (
    "distinct_query_domain_count",
    "repeated_fact_demand_count",
    "confirmed_cross_domain_evidence_count",
    "confirmed_formula_count",
    "native_confirmed_count",
    "animation_notify_mechanism_count",
    "curve_mechanism_count",
    "collision_mechanism_count",
    "material_parameter_input_count",
    "world_placement_evidence_count",
    "confirmed_component_relationship_count",
)

CONFIRMED_STATUSES = frozenset(
    {
        "SELF",
        "EXTRACTED",
        "IDENTIFIED",
        "CONFIRMED",
        "VERIFIED",
        "RESOLVED",
    }
)
CONFIRMED_CONFIDENCE = frozenset({"HIGH", "CONFIRMED"})
MAP_WORLD_EDGE_TYPES = frozenset(
    {
        "MAP_DIRECT_REFERENCE",
        "MAP_PCG_DEPENDENCY",
        "MAP_WORLD_PARTITION_REFERENCE",
        "MAP_USES",
        "PCG_PLACES",
    }
)
SEMANTIC_CLASS_GROUPS = frozenset(
    {
        "BLUEPRINT_BASE_CLASS",
        "ACTOR_COMPONENT",
        "DATA_ASSET",
        "MAP_WORLD",
        "VISUAL",
        "NATIVE_CLASS",
        "UNCLASSIFIED",
    }
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

CREATE TABLE IF NOT EXISTS role_signal_metrics (
    entity_id INTEGER PRIMARY KEY,
    semantic_class_category TEXT NOT NULL,
    query_hit_count INTEGER,
    query_hit_status TEXT NOT NULL,
    distinct_query_domain_count INTEGER NOT NULL,
    repeated_fact_demand_count INTEGER NOT NULL,
    confirmed_cross_domain_evidence_count INTEGER NOT NULL,
    confirmed_formula_count INTEGER NOT NULL,
    native_confirmed_count INTEGER NOT NULL,
    animation_notify_mechanism_count INTEGER NOT NULL,
    curve_mechanism_count INTEGER NOT NULL,
    collision_mechanism_count INTEGER NOT NULL,
    material_parameter_input_count INTEGER NOT NULL,
    world_placement_evidence_count INTEGER NOT NULL,
    confirmed_component_relationship_count INTEGER NOT NULL,
    provenance_json TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    source_revision_id INTEGER,
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY(source_revision_id) REFERENCES source_revisions(revision_id)
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
CREATE INDEX IF NOT EXISTS idx_role_signal_semantic_group
    ON role_signal_metrics(semantic_class_category);
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


@dataclass
class _PersistedRoleSignals:
    counts_by_entity: dict[int, dict[str, int]]
    query_hits_by_entity: dict[int, int]
    provenance_by_entity: dict[int, dict[str, list[str]]]
    source_statuses: dict[str, str]


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


def _confirmed(status: object, confidence: object) -> bool:
    return (
        str(status or "").strip().upper() in CONFIRMED_STATUSES
        and str(confidence or "").strip().upper() in CONFIRMED_CONFIDENCE
    )


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table_name})")
    }


def _fresh_revision_ids(connection: sqlite3.Connection) -> set[int]:
    columns = _table_columns(connection, "source_revisions")
    required = {
        "revision_id",
        "source_kind",
        "source_uri",
        "source_fingerprint",
        "producer_version",
        "schema_version",
        "generated_at",
        "freshness_status",
    }
    if not required.issubset(columns):
        return set()
    result: set[int] = set()
    for row in connection.execute(
        """
        SELECT
            revision_id, source_kind, source_uri, source_fingerprint,
            producer_version, schema_version, generated_at,
            freshness_status
        FROM source_revisions
        """
    ):
        if _complete_fresh_revision(row[1:8], revision_id=row[0]):
            result.add(int(row[0]))
    return result


def _complete_fresh_revision(
    values: Sequence[object],
    *,
    revision_id: object,
) -> bool:
    if len(values) != 7:
        return False
    (
        source_kind,
        source_uri,
        source_fingerprint,
        producer_version,
        schema_version,
        generated_at,
        freshness_status,
    ) = values
    fingerprint = str(source_fingerprint or "").strip()
    generated = str(generated_at or "").strip()
    try:
        timestamp = datetime.fromisoformat(
            generated[:-1] + "+00:00"
            if generated.endswith("Z")
            else generated
        )
    except ValueError:
        return False
    return (
        bool(re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint))
        and timestamp.utcoffset() is not None
        and source_revision_is_fresh(
            {
                "revisionId": revision_id,
                "sourceKind": source_kind,
                "sourceUri": source_uri,
                "sourceFingerprint": fingerprint,
                "producerVersion": producer_version,
                "schemaVersion": schema_version,
                "generatedAt": generated,
                "freshness": freshness_status,
            }
        )
    )


def _record_provenance(
    result: _PersistedRoleSignals,
    entity_id: int,
    signal_name: str,
    record: object,
) -> None:
    records = result.provenance_by_entity.setdefault(entity_id, {}).setdefault(
        signal_name,
        [],
    )
    text = str(record or "").strip()
    if text and text not in records and len(records) < 8:
        records.append(text)


def _increment_signal(
    result: _PersistedRoleSignals,
    entity_id: int,
    signal_name: str,
    *,
    count: int = 1,
    record: object = "",
) -> None:
    entity_counts = result.counts_by_entity.setdefault(entity_id, {})
    entity_counts[signal_name] = entity_counts.get(signal_name, 0) + count
    _record_provenance(result, entity_id, signal_name, record)


def _component_relationship(edge_type: object) -> bool:
    normalized = str(edge_type or "").strip().upper()
    return normalized in {"OWNS_COMPONENT", "USES_COMPONENT"} or (
        normalized.startswith("USES_") and normalized.endswith("_COMPONENT")
    )


def _normalized_fact_tokens(fact_type: object, fact_name: object) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "",
        f"{fact_type or ''} {fact_name or ''}".casefold(),
    )


def _mechanism_signal_names(
    fact_type: object,
    fact_name: object,
) -> tuple[str, ...]:
    tokens = _normalized_fact_tokens(fact_type, fact_name)
    result: list[str] = []
    if "animnotify" in tokens or "animationnotify" in tokens:
        result.append("animation_notify_mechanism_count")
    if "curve" in tokens:
        result.append("curve_mechanism_count")
    if "collision" in tokens:
        result.append("collision_mechanism_count")
    if "materialparameter" in tokens or (
        "material" in tokens and "parameter" in tokens
    ):
        result.append("material_parameter_input_count")
    return tuple(result)


def _semantic_class_category(
    row: Mapping[str, object],
    ancestry_categories: set[str],
) -> str:
    normalized = {category.strip().upper() for category in ancestry_categories}
    if "ACTOR_COMPONENT" in normalized:
        return "ACTOR_COMPONENT"
    if normalized & {"DATA_ASSET", "PRIMARY_DATA_ASSET"}:
        return "DATA_ASSET"
    if _truthy(row, "is_data_asset") or _truthy(row, "is_data_table"):
        return "DATA_ASSET"
    if _truthy(row, "is_map"):
        return "MAP_WORLD"
    if _is_visual_asset(row):
        return "VISUAL"
    if _truthy(row, "is_blueprint"):
        return "BLUEPRINT_BASE_CLASS"
    if _text(row, "asset_class_path").startswith("/Script/"):
        return "NATIVE_CLASS"
    return "UNCLASSIFIED"


def _class_leaf(value: str) -> str:
    leaf = value.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    return leaf.removesuffix("_C").casefold()


def _is_visual_asset(row: Mapping[str, object]) -> bool:
    explicit_category = _text(row, "semantic_class_category").casefold()
    if explicit_category in {"visual", "visual_support"}:
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
        and (
            _integer(row, "existing_report_count") > 0
            or _integer(row, "repeated_fact_demand_count") > 0
            or _integer(row, "distinct_query_domain_count") > 1
        )
    ):
        qualifications.append("repeated_semantic_demand")
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
            and "repeated_semantic_demand" in qualifications
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
    *,
    percentile_distributions: (
        Mapping[str, Mapping[str, Sequence[float]]] | None
    ) = None,
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
            group = _text(enriched[indexes[0]], "percentile_group")
            ordered = (
                list(percentile_distributions[group][raw_field])
                if percentile_distributions is not None
                else sorted(logs)
            )
            if not ordered:
                ordered = [0.0]
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


def _collect_query_signals(
    target: sqlite3.Connection,
    *,
    entity_ids: Mapping[str, int],
    result: _PersistedRoleSignals,
) -> None:
    columns = _table_columns(target, "benchmark_queries")
    required = {"query_id", "primary_domain", "query_json"}
    if not required.issubset(columns):
        result.source_statuses["benchmarkQueries"] = "SOURCE_NOT_AVAILABLE"
        return

    domains_by_entity: dict[int, set[str]] = defaultdict(set)
    fact_demand: dict[tuple[int, str, str], list[str]] = defaultdict(list)
    invalid_rows = 0
    eligible_rows = 0
    for query_id, primary_domain, query_json in target.execute(
        """
        SELECT query_id, primary_domain, query_json
        FROM benchmark_queries
        ORDER BY query_id
        """
    ):
        try:
            payload = json.loads(str(query_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid_rows += 1
            continue
        if not isinstance(payload, dict):
            invalid_rows += 1
            continue
        gold = payload.get("_gold")
        if (
            not isinstance(gold, dict)
            or str(gold.get("reviewStatus") or "").strip().upper()
            not in {"HUMAN_REVIEWED", "EMPIRICAL"}
            or gold.get("protocolBoundaryOnly") is not False
        ):
            continue
        eligible_rows += 1
        entity_uri = str(payload.get("entity") or "").strip()
        entity_id = entity_ids.get(entity_uri)
        if entity_id is None:
            continue
        result.query_hits_by_entity[entity_id] = (
            result.query_hits_by_entity.get(entity_id, 0) + 1
        )
        domain = str(primary_domain or "").strip()
        if domain:
            domains_by_entity[entity_id].add(domain)
        request = payload.get("requirements")
        if not isinstance(request, dict):
            request = payload
        fact_types = [
            str(value).strip()
            for value in request.get("factTypes", [])
            if str(value).strip()
        ]
        fact_names = [
            str(value).strip()
            for value in request.get("factNames", [])
            if str(value).strip()
        ]
        query_key = str(query_id)
        for fact_type in fact_types or [""]:
            for fact_name in fact_names or [""]:
                if fact_type or fact_name:
                    fact_demand[(entity_id, fact_type, fact_name)].append(
                        query_key
                    )
        _record_provenance(
            result,
            entity_id,
            "distinct_query_domain_count",
            query_key,
        )

    for entity_id, domains in domains_by_entity.items():
        result.counts_by_entity.setdefault(entity_id, {})[
            "distinct_query_domain_count"
        ] = len(domains)
    repeated_by_entity: dict[int, int] = defaultdict(int)
    for (entity_id, fact_type, fact_name), query_ids in fact_demand.items():
        if len(query_ids) < 2:
            continue
        repeated_by_entity[entity_id] += 1
        for query_id in query_ids:
            _record_provenance(
                result,
                entity_id,
                "repeated_fact_demand_count",
                f"{query_id}:{fact_type}:{fact_name}",
            )
    for entity_id, count in repeated_by_entity.items():
        result.counts_by_entity.setdefault(entity_id, {})[
            "repeated_fact_demand_count"
        ] = count
    if eligible_rows == 0:
        result.source_statuses["benchmarkQueries"] = "UNVERIFIED"
    elif invalid_rows:
        result.source_statuses["benchmarkQueries"] = "PARTIAL_INVALID"
    else:
        result.source_statuses["benchmarkQueries"] = "MEASURED"


def _collect_fact_signals(
    target: sqlite3.Connection,
    *,
    fresh_revision_ids: set[int],
    result: _PersistedRoleSignals,
) -> None:
    fact_columns = _table_columns(target, "facts")
    evidence_columns = _table_columns(target, "fact_evidence")
    required_facts = {
        "fact_id",
        "subject_entity_id",
        "fact_type",
        "fact_name",
        "current",
        "status",
        "confidence",
    }
    required_evidence = {
        "fact_id",
        "source_revision_id",
        "evidence_uri",
    }
    if not (
        required_facts.issubset(fact_columns)
        and required_evidence.issubset(evidence_columns)
        and _table_exists(target, "source_revisions")
    ):
        result.source_statuses["factEvidence"] = "SOURCE_NOT_AVAILABLE"
        return

    result.source_statuses["factEvidence"] = "MEASURED"
    accepted_fact_ids: set[int] = set()
    for row in target.execute(
        """
        SELECT
            fact.fact_id,
            fact.subject_entity_id,
            fact.fact_type,
            fact.fact_name,
            fact.current,
            fact.status,
            fact.confidence,
            evidence.source_revision_id,
            evidence.evidence_uri
        FROM facts AS fact
        JOIN fact_evidence AS evidence ON evidence.fact_id=fact.fact_id
        ORDER BY fact.fact_id, evidence.source_revision_id,
                 evidence.evidence_uri
        """
    ):
        fact_id = int(row[0])
        if fact_id in accepted_fact_ids:
            continue
        if (
            int(row[4] or 0) != 1
            or not _confirmed(row[5], row[6])
            or row[7] is None
            or int(row[7]) not in fresh_revision_ids
            or not is_recovered_evidence_uri(row[8])
        ):
            continue
        accepted_fact_ids.add(fact_id)
        entity_id = int(row[1])
        evidence_record = f"fact:{fact_id}@{row[8]}"
        if str(row[2] or "").strip().upper() == "FORMULA":
            _increment_signal(
                result,
                entity_id,
                "confirmed_formula_count",
                record=evidence_record,
            )
        for signal_name in _mechanism_signal_names(row[2], row[3]):
            _increment_signal(
                result,
                entity_id,
                signal_name,
                record=evidence_record,
            )


def _confirmed_domain_memberships(
    target: sqlite3.Connection,
    *,
    fresh_revision_ids: set[int],
    result: _PersistedRoleSignals,
) -> dict[int, set[str]]:
    columns = _table_columns(target, "domain_memberships")
    required = {"entity_id", "domain_id", "status", "confidence"}
    if not required.issubset(columns):
        result.source_statuses["domainMemberships"] = "SOURCE_NOT_AVAILABLE"
        return {}
    if "source_revision_id" not in columns:
        result.source_statuses["domainMemberships"] = "UNVERIFIED"
        return {}

    result.source_statuses["domainMemberships"] = "MEASURED"
    memberships: dict[int, set[str]] = defaultdict(set)
    for entity_id, domain_id, status, confidence, revision_id in target.execute(
        """
        SELECT
            entity_id, domain_id, status, confidence,
            source_revision_id
        FROM domain_memberships
        """
    ):
        if not _confirmed(status, confidence):
            continue
        if revision_id is None or int(revision_id) not in fresh_revision_ids:
            continue
        domain = str(domain_id or "").strip()
        if domain:
            memberships[int(entity_id)].add(domain)
    return memberships


def _collect_edge_signals(
    target: sqlite3.Connection,
    *,
    fresh_revision_ids: set[int],
    result: _PersistedRoleSignals,
) -> None:
    columns = _table_columns(target, "edges")
    required = {
        "edge_id",
        "source_entity_id",
        "target_entity_id",
        "edge_type",
        "status",
        "confidence",
        "evidence_uri",
    }
    if not required.issubset(columns):
        result.source_statuses["confirmedEdges"] = "SOURCE_NOT_AVAILABLE"
        result.source_statuses["domainMemberships"] = "SOURCE_NOT_AVAILABLE"
        return

    memberships = _confirmed_domain_memberships(
        target,
        fresh_revision_ids=fresh_revision_ids,
        result=result,
    )
    if "source_revision_id" not in columns:
        result.source_statuses["confirmedEdges"] = "UNVERIFIED"
        return

    result.source_statuses["confirmedEdges"] = "MEASURED"
    component_pairs: dict[tuple[int, int], str] = {}
    for row in target.execute(
        """
        SELECT
            edge_id, source_entity_id, target_entity_id, edge_type,
            status, confidence, evidence_uri, source_revision_id
        FROM edges
        ORDER BY edge_id
        """
    ):
        if (
            not _confirmed(row[4], row[5])
            or not is_recovered_evidence_uri(row[6])
        ):
            continue
        if row[7] is None or int(row[7]) not in fresh_revision_ids:
            continue
        edge_id = str(row[0])
        source_id = int(row[1])
        target_id = int(row[2])
        edge_type = str(row[3] or "").strip().upper()
        record = f"edge:{edge_id}@{row[6]}"
        if edge_type in MAP_WORLD_EDGE_TYPES:
            _increment_signal(
                result,
                source_id,
                "world_placement_evidence_count",
                record=record,
            )
        if _component_relationship(edge_type):
            component_pairs.setdefault((target_id, source_id), record)
        source_domains = memberships.get(source_id, set())
        target_domains = memberships.get(target_id, set())
        if source_domains and target_domains and source_domains != target_domains:
            _increment_signal(
                result,
                source_id,
                "confirmed_cross_domain_evidence_count",
                record=record,
            )
    for (target_id, _source_id), record in component_pairs.items():
        _increment_signal(
            result,
            target_id,
            "confirmed_component_relationship_count",
            record=record,
        )


def _collect_native_signals(
    target: sqlite3.Connection,
    *,
    fresh_revision_ids: set[int],
    entity_ids: Mapping[str, int],
    result: _PersistedRoleSignals,
) -> None:
    function_columns = _table_columns(target, "native_functions")
    required_functions = {
        "native_function_id",
        "status",
        "confidence",
        "source_revision_id",
    }
    if not required_functions.issubset(function_columns):
        result.source_statuses["nativeFunctions"] = "SOURCE_NOT_AVAILABLE"
        result.source_statuses["nativeBlueprintLinks"] = "SOURCE_NOT_AVAILABLE"
        result.source_statuses["nativeFieldAccesses"] = "SOURCE_NOT_AVAILABLE"
        return

    result.source_statuses["nativeFunctions"] = "MEASURED"
    canonical_expression = (
        "canonical_uri"
        if "canonical_uri" in function_columns
        else "'' AS canonical_uri"
    )
    valid_functions: dict[int, str] = {}
    native_entity_ids: dict[int, int] = {}
    for function_id, canonical_uri, status, confidence, revision_id in (
        target.execute(
            f"""
            SELECT
                native_function_id, {canonical_expression},
                status, confidence, source_revision_id
            FROM native_functions
            """
        )
    ):
        if (
            not _confirmed(status, confidence)
            or revision_id is None
            or int(revision_id) not in fresh_revision_ids
        ):
            continue
        function_key = int(function_id)
        valid_functions[function_key] = str(canonical_uri or "")
        entity_id = entity_ids.get(str(canonical_uri or "").strip())
        if entity_id is not None:
            native_entity_ids[function_key] = entity_id
            _increment_signal(
                result,
                entity_id,
                "native_confirmed_count",
                record=f"native-function:{function_key}",
            )

    link_columns = _table_columns(target, "native_blueprint_links")
    required_links = {
        "link_id",
        "blueprint_entity_id",
        "blueprint_graph_evidence_uri",
        "native_function_id",
        "native_evidence_uri",
        "status",
        "confidence",
        "blueprint_graph_source_revision_id",
    }
    linked_entities_by_function: dict[int, set[int]] = defaultdict(set)
    if not required_links.issubset(link_columns):
        result.source_statuses["nativeBlueprintLinks"] = "SOURCE_NOT_AVAILABLE"
    else:
        result.source_statuses["nativeBlueprintLinks"] = "MEASURED"
        for row in target.execute(
            """
            SELECT
                link_id, blueprint_entity_id,
                blueprint_graph_evidence_uri, native_function_id,
                native_evidence_uri, status, confidence,
                blueprint_graph_source_revision_id
            FROM native_blueprint_links
            ORDER BY link_id
            """
        ):
            function_id = None if row[3] is None else int(row[3])
            if (
                function_id not in valid_functions
                or not _confirmed(row[5], row[6])
                or row[7] is None
                or int(row[7]) not in fresh_revision_ids
                or not is_recovered_evidence_uri(row[2])
                or not is_recovered_evidence_uri(row[4])
            ):
                continue
            entity_id = int(row[1])
            linked_entities_by_function[function_id].add(entity_id)
            _increment_signal(
                result,
                entity_id,
                "native_confirmed_count",
                record=f"native-link:{row[0]}@{row[2]}",
            )

    access_columns = _table_columns(target, "native_field_accesses")
    required_accesses = {
        "field_access_id",
        "native_function_id",
        "instruction_or_slice_uri",
        "status",
        "confidence",
    }
    if not required_accesses.issubset(access_columns):
        result.source_statuses["nativeFieldAccesses"] = "SOURCE_NOT_AVAILABLE"
        return
    result.source_statuses["nativeFieldAccesses"] = "MEASURED"
    for row in target.execute(
        """
        SELECT
            field_access_id, native_function_id,
            instruction_or_slice_uri, status, confidence
        FROM native_field_accesses
        ORDER BY field_access_id
        """
    ):
        function_id = int(row[1])
        if (
            function_id not in valid_functions
            or not _confirmed(row[3], row[4])
            or not is_recovered_evidence_uri(row[2])
        ):
            continue
        record = f"native-field:{row[0]}@{row[2]}"
        for entity_id in linked_entities_by_function.get(function_id, set()):
            _increment_signal(
                result,
                entity_id,
                "native_confirmed_count",
                record=record,
            )
        native_entity_id = native_entity_ids.get(function_id)
        if native_entity_id is not None:
            _increment_signal(
                result,
                native_entity_id,
                "native_confirmed_count",
                record=record,
            )


def _collect_persisted_role_signals(
    target: sqlite3.Connection,
    *,
    entity_ids: Mapping[str, int],
) -> _PersistedRoleSignals:
    result = _PersistedRoleSignals(
        counts_by_entity={},
        query_hits_by_entity={},
        provenance_by_entity={},
        source_statuses={},
    )
    fresh_revision_ids = _fresh_revision_ids(target)
    revision_columns = _table_columns(target, "source_revisions")
    result.source_statuses["sourceRevisions"] = (
        "MEASURED"
        if {
            "revision_id",
            "source_kind",
            "source_uri",
            "source_fingerprint",
            "producer_version",
            "schema_version",
            "generated_at",
            "freshness_status",
        }.issubset(revision_columns)
        else "SOURCE_NOT_AVAILABLE"
    )
    _collect_query_signals(
        target,
        entity_ids=entity_ids,
        result=result,
    )
    _collect_fact_signals(
        target,
        fresh_revision_ids=fresh_revision_ids,
        result=result,
    )
    _collect_edge_signals(
        target,
        fresh_revision_ids=fresh_revision_ids,
        result=result,
    )
    _collect_native_signals(
        target,
        fresh_revision_ids=fresh_revision_ids,
        entity_ids=entity_ids,
        result=result,
    )
    return result


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
            registration.source_property,
            registration.evidence_uri,
            registration.status,
            registration.confidence,
            revision.revision_id,
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
        source_property = str(row[3] or "")
        evidence_uri = str(row[4] or "")
        status = str(row[5] or "").upper()
        confidence = str(row[6] or "").upper()
        revision_is_fresh = _complete_fresh_revision(
            row[8:15],
            revision_id=row[7],
        )
        relationship_type = registration_edge_type(
            registration_type=registration_type,
            source_property=source_property,
        )
        if (
            not registration_provenance_is_confirmed(
                status,
                confidence,
                evidence_uri,
            )
            or not revision_is_fresh
            or relationship_type not in GLOBAL_REGISTRATION_EDGE_TYPES
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
                AS distinct_registration_type_count
        FROM assets AS a
        LEFT JOIN registration_counts AS r
          ON r.owner_object_path=a.object_path
        ORDER BY a.object_path
        """
    )


def _confirmed_ancestry_categories(
    target: sqlite3.Connection,
    *,
    fresh_revision_ids: set[int],
) -> tuple[dict[int, set[str]], str]:
    assignment_columns = _table_columns(target, "asset_class_assignments")
    category_columns = _table_columns(target, "class_ancestry_categories")
    required_assignments = {
        "entity_id",
        "class_id",
        "status",
        "confidence",
    }
    required_categories = {
        "class_id",
        "category",
        "status",
        "confidence",
    }
    if not (
        required_assignments.issubset(assignment_columns)
        and required_categories.issubset(category_columns)
    ):
        return {}, "SOURCE_NOT_AVAILABLE"

    revision_expression = (
        "assignment.source_revision_id"
        if "source_revision_id" in assignment_columns
        else "NULL AS source_revision_id"
    )
    result: dict[int, set[str]] = defaultdict(set)
    for row in target.execute(
        f"""
        SELECT DISTINCT
            assignment.entity_id,
            category.category,
            assignment.status,
            assignment.confidence,
            category.status,
            category.confidence,
            {revision_expression}
        FROM asset_class_assignments AS assignment
        JOIN class_ancestry_categories AS category
          ON category.class_id=assignment.class_id
        """
    ):
        if not (_confirmed(row[2], row[3]) and _confirmed(row[4], row[5])):
            continue
        if (
            "source_revision_id" in assignment_columns
            and (row[6] is None or int(row[6]) not in fresh_revision_ids)
        ):
            continue
        category = str(row[1] or "").strip().upper()
        if category:
            result[int(row[0])].add(category)
    return (
        result,
        (
            "MEASURED"
            if "source_revision_id" in assignment_columns
            else "MEASURED_WITHOUT_ROW_REVISION"
        ),
    )


def _decorate_role_row(
    source: Mapping[str, object],
    *,
    entity_id: int,
    categories_by_entity: Mapping[int, set[str]],
    signals: _PersistedRoleSignals,
) -> dict[str, object]:
    row = dict(source)
    ancestry_categories = categories_by_entity.get(entity_id, set())
    semantic_category = _semantic_class_category(row, ancestry_categories)
    if semantic_category not in SEMANTIC_CLASS_GROUPS:
        semantic_category = "UNCLASSIFIED"
    row["semantic_class_category"] = semantic_category
    row["is_actor_component"] = int(
        semantic_category == "ACTOR_COMPONENT"
    )
    row["is_data_asset"] = int(
        _truthy(row, "is_data_asset")
        or semantic_category == "DATA_ASSET"
    )

    signal_counts = signals.counts_by_entity.get(entity_id, {})
    for field in ROLE_SIGNAL_COUNT_FIELDS:
        row[field] = int(signal_counts.get(field, 0))
    row["component_reuse_count"] = int(
        signal_counts.get("confirmed_component_relationship_count", 0)
    )
    row["cross_domain_reference_count"] = int(
        signal_counts.get("confirmed_cross_domain_evidence_count", 0)
    )
    benchmark_status = signals.source_statuses.get(
        "benchmarkQueries",
        "SOURCE_NOT_AVAILABLE",
    )
    if benchmark_status in {"SOURCE_NOT_AVAILABLE", "UNVERIFIED"}:
        row["query_hit_count"] = None
        row["query_hit_status"] = (
            "UNVERIFIED"
            if benchmark_status == "UNVERIFIED"
            else "NOT_MEASURED"
        )
    else:
        row["query_hit_count"] = int(
            signals.query_hits_by_entity.get(entity_id, 0)
        )
        row["query_hit_status"] = benchmark_status
    row["confirmed_fact_count"] = sum(
        _integer(row, field)
        for field in (
            "confirmed_formula_count",
            "animation_notify_mechanism_count",
            "curve_mechanism_count",
            "collision_mechanism_count",
            "material_parameter_input_count",
        )
    )
    return row


def _percentile_distributions(
    discovery: sqlite3.Connection,
    *,
    entity_ids: Mapping[str, int],
    categories_by_entity: Mapping[int, set[str]],
    signals: _PersistedRoleSignals,
) -> dict[str, dict[str, list[float]]]:
    distributions: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    rows = _source_role_rows(discovery)
    while source_batch := rows.fetchmany(10_000):
        for source in source_batch:
            source_row = dict(source)
            entity_id = entity_ids.get(_text(source_row, "object_path"))
            if entity_id is None:
                continue
            row = _decorate_role_row(
                source_row,
                entity_id=entity_id,
                categories_by_entity=categories_by_entity,
                signals=signals,
            )
            row["registration_count"] = _registration_count(row)
            row["query_demand_count"] = _query_demand_count(row)
            group = _text(
                row,
                "semantic_class_category",
                "UNCLASSIFIED",
            )
            for raw_field, _percentile_field in PERCENTILE_METRICS:
                distributions[group][raw_field].append(
                    math.log1p(max(0, _integer(row, raw_field)))
                )
    return {
        group: {
            raw_field: sorted(values)
            for raw_field, values in metrics.items()
        }
        for group, metrics in distributions.items()
    }


def _role_signal_provenance(
    *,
    entity_id: int,
    semantic_category: str,
    ancestry_categories: set[str],
    signals: _PersistedRoleSignals,
) -> str:
    return _json(
        {
            "semanticClass": {
                "category": semantic_category,
                "confirmedAncestryCategories": sorted(ancestry_categories),
            },
            "sourceStatus": dict(sorted(signals.source_statuses.items())),
            "records": {
                key: value
                for key, value in sorted(
                    signals.provenance_by_entity.get(entity_id, {}).items()
                )
            },
        }
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
    target.execute("DELETE FROM role_signal_metrics")
    target.execute("DELETE FROM knowledge_roles")
    target.execute("DELETE FROM knowledge_depth_policies")
    entity_ids = {
        str(uri): int(entity_id)
        for uri, entity_id in target.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    asset_count = 0
    role_count = 0
    depth_counts: dict[str, int] = defaultdict(int)
    _prepare_canonical_registration_counts(discovery, target)
    fresh_revision_ids = _fresh_revision_ids(target)
    categories_by_entity, category_source_status = (
        _confirmed_ancestry_categories(
            target,
            fresh_revision_ids=fresh_revision_ids,
        )
    )
    signals = _collect_persisted_role_signals(
        target,
        entity_ids=entity_ids,
    )
    signals.source_statuses["semanticClassAncestry"] = category_source_status
    distributions = _percentile_distributions(
        discovery,
        entity_ids=entity_ids,
        categories_by_entity=categories_by_entity,
        signals=signals,
    )
    rows = _source_role_rows(discovery)
    while source_batch := rows.fetchmany(10_000):
        metric_rows: list[tuple[object, ...]] = []
        signal_rows: list[tuple[object, ...]] = []
        role_rows: list[tuple[object, ...]] = []
        depth_rows: list[tuple[object, ...]] = []
        decorated_rows: list[tuple[int, dict[str, object]]] = []
        for source in source_batch:
            source_row = dict(source)
            entity_uri = _text(source_row, "object_path")
            entity_id = entity_ids.get(entity_uri)
            if entity_id is None:
                continue
            decorated_rows.append(
                (
                    entity_id,
                    _decorate_role_row(
                        source_row,
                        entity_id=entity_id,
                        categories_by_entity=categories_by_entity,
                        signals=signals,
                    ),
                )
            )
        enriched_rows = enrich_type_percentiles(
            [row for _entity_id, row in decorated_rows],
            percentile_distributions=distributions,
        )
        for (entity_id, _source_row), row in zip(
            decorated_rows,
            enriched_rows,
            strict=True,
        ):
            entity_uri = _text(row, "object_path")
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
            signal_rows.append(
                (
                    entity_id,
                    _text(
                        row,
                        "semantic_class_category",
                        "UNCLASSIFIED",
                    ),
                    (
                        None
                        if row.get("query_hit_count") is None
                        else _integer(row, "query_hit_count")
                    ),
                    _text(row, "query_hit_status", "NOT_MEASURED"),
                    *(
                        _integer(row, field)
                        for field in ROLE_SIGNAL_COUNT_FIELDS
                    ),
                    _role_signal_provenance(
                        entity_id=entity_id,
                        semantic_category=_text(
                            row,
                            "semantic_class_category",
                            "UNCLASSIFIED",
                        ),
                        ancestry_categories=categories_by_entity.get(
                            entity_id,
                            set(),
                        ),
                        signals=signals,
                    ),
                    ROLE_CLASSIFIER_VERSION,
                    source_revision_id,
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
            """
            INSERT INTO role_signal_metrics VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            signal_rows,
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
        "roleSignals": asset_count,
        "roles": role_count,
        **{
            f"depth_{policy.casefold()}": int(depth_counts.get(policy, 0))
            for policy in DEPTH_POLICIES
        },
    }


def materialize_discovery_role_entities(
    discovery: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    entity_ids: Sequence[int],
    source_revision_id: int | None,
) -> dict[str, int]:
    """Recompute only an explicit role dependency closure.

    Percentile distributions and persisted signals are read globally, but the
    durable write set is limited to ``entity_ids``.  Callers must compute the
    exact percentile dependency closure before invoking this primitive.
    Transaction control remains with the caller.
    """

    selected = tuple(sorted(set(entity_ids)))
    if (
        not selected
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 1
               for value in selected)
        or tuple(entity_ids) != selected
    ):
        raise ValueError("role entity scope must be sorted unique positive IDs")
    if source_revision_id is not None:
        revision = target.execute(
            """
            SELECT freshness_status FROM source_revisions WHERE revision_id=?
            """,
            (source_revision_id,),
        ).fetchone()
        if revision is None or str(revision[0]).upper() != "FRESH":
            raise ValueError("role source revision must exist and be FRESH")

    entity_by_uri = {
        str(uri): int(entity_id)
        for uri, entity_id in target.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    if not set(selected).issubset(set(entity_by_uri.values())):
        raise ValueError("role entity scope contains an unknown entity")
    _prepare_canonical_registration_counts(discovery, target)
    fresh_revision_ids = _fresh_revision_ids(target)
    categories_by_entity, category_source_status = (
        _confirmed_ancestry_categories(
            target,
            fresh_revision_ids=fresh_revision_ids,
        )
    )
    signals = _collect_persisted_role_signals(
        target,
        entity_ids=entity_by_uri,
    )
    signals.source_statuses["semanticClassAncestry"] = category_source_status
    distributions = _percentile_distributions(
        discovery,
        entity_ids=entity_by_uri,
        categories_by_entity=categories_by_entity,
        signals=signals,
    )
    selected_set = set(selected)
    decorated: list[tuple[int, dict[str, object]]] = []
    rows = _source_role_rows(discovery)
    while batch := rows.fetchmany(10_000):
        for source in batch:
            source_row = dict(source)
            entity_id = entity_by_uri.get(_text(source_row, "object_path"))
            if entity_id not in selected_set:
                continue
            decorated.append(
                (
                    entity_id,
                    _decorate_role_row(
                        source_row,
                        entity_id=entity_id,
                        categories_by_entity=categories_by_entity,
                        signals=signals,
                    ),
                )
            )
    if {entity_id for entity_id, _row in decorated} != selected_set:
        raise ValueError("role entity scope is missing from Discovery")
    enriched = enrich_type_percentiles(
        [row for _entity_id, row in decorated],
        percentile_distributions=distributions,
    )

    metric_rows: list[tuple[object, ...]] = []
    signal_rows: list[tuple[object, ...]] = []
    role_rows: list[tuple[object, ...]] = []
    depth_rows: list[tuple[object, ...]] = []
    depth_counts: dict[str, int] = defaultdict(int)
    for (entity_id, _source), row in zip(decorated, enriched, strict=True):
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
                None if row.get("query_hit_count") is None
                else _integer(row, "query_hit_count"),
                _text(row, "query_hit_status", "NOT_MEASURED"),
                None if row.get("existing_report_count") is None
                else _integer(row, "existing_report_count"),
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
        signal_rows.append(
            (
                entity_id,
                _text(row, "semantic_class_category", "UNCLASSIFIED"),
                None if row.get("query_hit_count") is None
                else _integer(row, "query_hit_count"),
                _text(row, "query_hit_status", "NOT_MEASURED"),
                *(_integer(row, field) for field in ROLE_SIGNAL_COUNT_FIELDS),
                _role_signal_provenance(
                    entity_id=entity_id,
                    semantic_category=_text(
                        row, "semantic_class_category", "UNCLASSIFIED"
                    ),
                    ancestry_categories=categories_by_entity.get(
                        entity_id, set()
                    ),
                    signals=signals,
                ),
                ROLE_CLASSIFIER_VERSION,
                source_revision_id,
            )
        )
        role_rows.extend(
            (
                entity_id,
                assignment.role,
                assignment.confidence,
                assignment.status,
                _json(assignment.reasons),
                ROLE_CLASSIFIER_VERSION,
                source_revision_id,
            )
            for assignment in decision.roles
        )
        depth_rows.append(
            (
                entity_id,
                decision.depth_policy,
                _json(decision.depth_reasons),
                ROLE_CLASSIFIER_VERSION,
            )
        )
        depth_counts[decision.depth_policy] += 1

    placeholders = ",".join("?" for _ in selected)
    for table in (
        "knowledge_roles",
        "knowledge_depth_policies",
        "role_metrics",
        "role_signal_metrics",
    ):
        target.execute(
            f'DELETE FROM "{table}" WHERE entity_id IN ({placeholders})',
            selected,
        )
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
        """
        INSERT INTO role_signal_metrics VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        signal_rows,
    )
    target.executemany(
        "INSERT INTO knowledge_roles VALUES (?, ?, ?, ?, ?, ?, ?)",
        role_rows,
    )
    target.executemany(
        "INSERT INTO knowledge_depth_policies VALUES (?, ?, ?, ?)",
        depth_rows,
    )
    return {
        "assets": len(selected),
        "roleSignals": len(signal_rows),
        "roles": len(role_rows),
        **{
            f"depth_{policy.casefold()}": int(depth_counts.get(policy, 0))
            for policy in DEPTH_POLICIES
        },
    }


def compute_additive_role_dependency_scope(
    discovery: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    changed_entity_ids: Sequence[int],
    source_revision_id: int,
) -> tuple[tuple[int, ...], dict[str, object]]:
    """Prove the exact percentile closure for an additive fact change.

    Replacing one entity's metric changes the empirical CDF only for peers in
    the same percentile group whose current value lies between the old and new
    values.  Non-percentile role signals affect the changed entity itself.
    """

    changed = tuple(sorted(set(changed_entity_ids)))
    if (
        not changed
        or tuple(changed_entity_ids) != changed
        or any(type(value) is not int or value < 1 for value in changed)
        or type(source_revision_id) is not int
        or source_revision_id < 1
    ):
        raise ValueError("additive role dependency inputs are not canonical")
    revision = target.execute(
        """
        SELECT freshness_status FROM source_revisions WHERE revision_id=?
        """,
        (source_revision_id,),
    ).fetchone()
    if revision is None or str(revision[0]).upper() != "FRESH":
        raise ValueError("additive role dependency source is not fresh")

    entity_by_uri = {
        str(uri): int(entity_id)
        for uri, entity_id in target.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    if not set(changed).issubset(set(entity_by_uri.values())):
        raise ValueError("changed role entity is missing from Core")
    _prepare_canonical_registration_counts(discovery, target)
    fresh_revision_ids = _fresh_revision_ids(target)
    categories_by_entity, category_source_status = (
        _confirmed_ancestry_categories(
            target,
            fresh_revision_ids=fresh_revision_ids,
        )
    )
    signals = _collect_persisted_role_signals(
        target,
        entity_ids=entity_by_uri,
    )
    signals.source_statuses["semanticClassAncestry"] = category_source_status
    distributions = _percentile_distributions(
        discovery,
        entity_ids=entity_by_uri,
        categories_by_entity=categories_by_entity,
        signals=signals,
    )
    decorated: list[tuple[int, dict[str, object]]] = []
    rows = _source_role_rows(discovery)
    while batch := rows.fetchmany(10_000):
        for source in batch:
            source_row = dict(source)
            entity_id = entity_by_uri.get(_text(source_row, "object_path"))
            if entity_id is None:
                continue
            decorated.append(
                (
                    entity_id,
                    _decorate_role_row(
                        source_row,
                        entity_id=entity_id,
                        categories_by_entity=categories_by_entity,
                        signals=signals,
                    ),
                )
            )
    enriched = enrich_type_percentiles(
        [row for _entity_id, row in decorated],
        percentile_distributions=distributions,
    )
    current_by_id = {
        entity_id: row
        for (entity_id, _source), row in zip(decorated, enriched, strict=True)
    }
    if not set(changed).issubset(current_by_id):
        raise ValueError("changed role entity is missing from Discovery")

    old_rows = {
        int(row[0]): row
        for row in target.execute(
            """
            SELECT entity_id, percentile_group,
                   descendant_count, referencer_count,
                   component_reuse_count, cross_domain_reference_count,
                   registration_count, query_demand_count
            FROM role_metrics
            ORDER BY entity_id
            """
        )
    }
    if not set(changed).issubset(old_rows):
        raise ValueError("changed role entity has no prior role metrics")
    raw_column_index = {
        "descendant_count": 2,
        "referencer_count": 3,
        "component_reuse_count": 4,
        "cross_domain_reference_count": 5,
        "registration_count": 6,
        "query_demand_count": 7,
    }
    affected = set(changed)
    transitions: list[dict[str, object]] = []
    for entity_id in changed:
        old = old_rows[entity_id]
        current = current_by_id[entity_id]
        old_group = str(old[1])
        new_group = _text(current, "percentile_group", "UNCLASSIFIED")
        metric_transitions: dict[str, dict[str, int]] = {}
        if old_group != new_group:
            affected.update(
                candidate_id
                for candidate_id, row in old_rows.items()
                if str(row[1]) == old_group
            )
            affected.update(
                candidate_id
                for candidate_id, row in current_by_id.items()
                if _text(row, "percentile_group", "UNCLASSIFIED")
                == new_group
            )
        for raw_field, _percentile_field in PERCENTILE_METRICS:
            old_value = int(old[raw_column_index[raw_field]])
            new_value = _integer(current, raw_field)
            if old_value == new_value or old_group != new_group:
                if old_value != new_value:
                    metric_transitions[raw_field] = {
                        "before": old_value,
                        "after": new_value,
                    }
                continue
            lower, upper = sorted((old_value, new_value))
            affected.update(
                candidate_id
                for candidate_id, row in current_by_id.items()
                if _text(row, "percentile_group", "UNCLASSIFIED")
                == new_group
                and lower <= _integer(row, raw_field) <= upper
            )
            metric_transitions[raw_field] = {
                "before": old_value,
                "after": new_value,
            }
        transitions.append(
            {
                "entityId": entity_id,
                "beforeGroup": old_group,
                "afterGroup": new_group,
                "metrics": dict(sorted(metric_transitions.items())),
            }
        )
    closure = tuple(sorted(affected))
    proof_body: dict[str, object] = {
        "schema": "ark-kb-additive-role-dependency-scope/v1",
        "classifierVersion": ROLE_CLASSIFIER_VERSION,
        "sourceRevisionId": source_revision_id,
        "changedEntityIds": list(changed),
        "roleEntityIds": list(closure),
        "transitions": transitions,
    }
    proof_body["proof"] = "role-scope://" + hashlib.sha256(
        _json(proof_body).encode("utf-8")
    ).hexdigest()
    return closure, proof_body
