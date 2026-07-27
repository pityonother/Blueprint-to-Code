"""Selective invalidation planning and dependency propagation for KB vNext."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Mapping

from .projections import DOMAIN_PROJECTIONS


CHANGE_KINDS = {
    "ASSET",
    "CLASS",
    "REGISTRY",
    "NATIVE",
    "ONTOLOGY",
    "PARSER",
}


@dataclass(frozen=True)
class InvalidationPlan:
    event_kind: str
    upstream_revision_id: int | None
    downstream: Mapping[str, tuple[int, ...]]
    reasons: Mapping[str, str]

    @property
    def affected_count(self) -> int:
        return sum(len(values) for values in self.downstream.values())


def rebuild_invalidation_dependencies(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    """Materialize revision-to-derived-record dependencies from provenance."""

    connection.execute("DELETE FROM invalidation_dependencies")
    connection.execute(
        """
        INSERT OR IGNORE INTO invalidation_dependencies
        SELECT source_revision_id, 'FACT', fact_id, 'DIRECT_FACT_EVIDENCE'
        FROM fact_evidence
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO invalidation_dependencies
        SELECT
            evidence.source_revision_id,
            'EFFECTIVE_ENTITY',
            effective.entity_id,
            'EFFECTIVE_FACT_SOURCE'
        FROM effective_facts AS effective
        JOIN fact_evidence AS evidence
          ON evidence.fact_id=effective.fact_id
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO invalidation_dependencies
        SELECT
            source_revision_id, 'NATIVE_FUNCTION',
            native_function_id, 'NATIVE_BUILD_BINDING'
        FROM native_functions
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO invalidation_dependencies
        SELECT
            function.source_revision_id, 'BLUEPRINT_NATIVE_ENTITY',
            link.blueprint_entity_id, 'BLUEPRINT_NATIVE_BINDING'
        FROM native_blueprint_links AS link
        JOIN native_functions AS function
          ON function.native_function_id=link.native_function_id
        WHERE link.native_function_id IS NOT NULL
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO invalidation_dependencies
        SELECT
            source_revision_id, 'REGISTRATION_ENTITY',
            owner.entity_id, 'REGISTRY_GENERATION'
        FROM typed_registrations AS registration
        JOIN entities AS owner
          ON owner.canonical_uri=registration.owner_uri
        WHERE source_revision_id IS NOT NULL
        """
    )
    discovery_revision = connection.execute(
        """
        SELECT revision_id FROM source_revisions
        WHERE source_kind='discovery'
        ORDER BY revision_id LIMIT 1
        """
    ).fetchone()
    if discovery_revision:
        revision_id = int(discovery_revision[0])
        connection.execute(
            """
            INSERT OR IGNORE INTO invalidation_dependencies
            SELECT ?, 'ROLE_ENTITY', entity_id, 'ROLE_INPUT'
            FROM knowledge_roles
            """,
            (revision_id,),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO invalidation_dependencies
            SELECT ?, 'DOMAIN_ENTITY', entity_id, 'DOMAIN_INPUT'
            FROM domain_memberships
            """,
            (revision_id,),
        )
    connection.commit()
    return {
        str(kind): int(count)
        for kind, count in connection.execute(
            """
            SELECT downstream_kind, COUNT(*)
            FROM invalidation_dependencies
            GROUP BY downstream_kind
            ORDER BY downstream_kind
            """
        )
    }


def _descendant_entities(
    connection: sqlite3.Connection, class_ids: Iterable[int]
) -> set[int]:
    values = {int(value) for value in class_ids}
    if not values:
        return set()
    placeholders = ",".join("?" for _ in values)
    return {
        int(row[0])
        for row in connection.execute(
            f"""
            SELECT DISTINCT assignment.entity_id
            FROM class_closure AS closure
            JOIN asset_class_assignments AS assignment
              ON assignment.class_id=closure.descendant_class_id
            WHERE closure.ancestor_class_id IN ({placeholders})
              AND assignment.assignment_kind='GENERATED_CLASS'
            """,
            tuple(sorted(values)),
        )
    }


def _entity_classes(
    connection: sqlite3.Connection, entity_ids: set[int]
) -> set[int]:
    if not entity_ids:
        return set()
    placeholders = ",".join("?" for _ in entity_ids)
    return {
        int(row[0])
        for row in connection.execute(
            f"""
            SELECT class_id FROM asset_class_assignments
            WHERE assignment_kind='GENERATED_CLASS'
              AND entity_id IN ({placeholders})
            """,
            tuple(sorted(entity_ids)),
        )
    }


def _fact_ids(
    connection: sqlite3.Connection, entity_ids: set[int]
) -> set[int]:
    if not entity_ids:
        return set()
    placeholders = ",".join("?" for _ in entity_ids)
    return {
        int(row[0])
        for row in connection.execute(
            f"""
            SELECT fact_id FROM facts
            WHERE current=1
              AND subject_entity_id IN ({placeholders})
            """,
            tuple(sorted(entity_ids)),
        )
    }


def _all_projection_ids() -> set[int]:
    return set(range(1, len(DOMAIN_PROJECTIONS) + 1))


def plan_invalidation(
    connection: sqlite3.Connection,
    *,
    event_kind: str,
    entity_ids: Iterable[int] = (),
    class_ids: Iterable[int] = (),
    upstream_revision_id: int | None = None,
) -> InvalidationPlan:
    """Return a bounded recomputation plan without mutating source Evidence."""

    event_kind = event_kind.upper()
    if event_kind not in CHANGE_KINDS:
        raise ValueError(f"Unsupported invalidation event: {event_kind}")
    entities = {int(value) for value in entity_ids}
    classes = {int(value) for value in class_ids}
    downstream: dict[str, set[int]] = {}
    reasons: dict[str, str] = {}

    def add(kind: str, values: Iterable[int], reason: str) -> None:
        normalized = {int(value) for value in values}
        if not normalized:
            return
        downstream.setdefault(kind, set()).update(normalized)
        reasons[kind] = reason

    if event_kind == "ASSET":
        descendant_entities = _descendant_entities(
            connection, _entity_classes(connection, entities)
        )
        affected_entities = entities | descendant_entities
        add("FACT", _fact_ids(connection, entities), "ASSET_FACT_SOURCE_CHANGED")
        add(
            "EFFECTIVE_ENTITY",
            affected_entities,
            "DECLARED_DEFAULT_OR_PARENT_CHANGED",
        )
        add("ROLE_ENTITY", entities, "ASSET_ROLE_INPUT_CHANGED")
        add("DOMAIN_ENTITY", entities, "ASSET_DOMAIN_INPUT_CHANGED")
        add("EDGE_ENTITY", entities, "DIRECT_ASSET_EDGE_EVIDENCE_CHANGED")
        add("PROJECTION", _all_projection_ids(), "FACT_PROJECTION_CHANGED")
    elif event_kind == "CLASS":
        descendants = _descendant_entities(connection, classes)
        add("CLASS_CLOSURE", classes, "PARENT_CLASS_CHANGED")
        add(
            "EFFECTIVE_ENTITY",
            descendants,
            "INHERITED_DEFAULT_CHAIN_CHANGED",
        )
        add("ROLE_ENTITY", descendants, "INHERITANCE_ROLE_CHANGED")
        add("DOMAIN_ENTITY", descendants, "INHERITANCE_DOMAIN_CHANGED")
    elif event_kind == "REGISTRY":
        add("REGISTRATION_ENTITY", entities, "REGISTRY_GENERATION_CHANGED")
        add("ROLE_ENTITY", entities, "REGISTRY_CENTRALITY_CHANGED")
        add("DOMAIN_ENTITY", entities, "REGISTRY_DOMAIN_CHANGED")
    elif event_kind in {"NATIVE", "PARSER"}:
        if upstream_revision_id is None:
            raise ValueError(f"{event_kind} invalidation requires a revision")
        rows = list(
            connection.execute(
                """
                SELECT downstream_kind, downstream_id, dependency_reason
                FROM invalidation_dependencies
                WHERE upstream_revision_id=?
                """,
                (upstream_revision_id,),
            )
        )
        for downstream_kind, downstream_id, reason in rows:
            add(str(downstream_kind), [int(downstream_id)], str(reason))
        if event_kind == "NATIVE":
            add(
                "QUERY_SNAPSHOT",
                [upstream_revision_id],
                "NATIVE_EVIDENCE_CHANGED",
            )
        else:
            add(
                "PROJECTION",
                _all_projection_ids(),
                "PARSER_DERIVED_FACTS_CHANGED",
            )
    elif event_kind == "ONTOLOGY":
        add(
            "ROLE_ENTITY",
            (row[0] for row in connection.execute("SELECT DISTINCT entity_id FROM knowledge_roles")),
            "ONTOLOGY_ROLE_RULES_CHANGED",
        )
        add(
            "DOMAIN_ENTITY",
            (row[0] for row in connection.execute("SELECT DISTINCT entity_id FROM domain_memberships")),
            "ONTOLOGY_DOMAIN_RULES_CHANGED",
        )
        add("PROJECTION", _all_projection_ids(), "ONTOLOGY_PROJECTION_CHANGED")

    return InvalidationPlan(
        event_kind=event_kind,
        upstream_revision_id=upstream_revision_id,
        downstream={
            kind: tuple(sorted(values))
            for kind, values in sorted(downstream.items())
        },
        reasons=reasons,
    )


def _event_id(plan: InvalidationPlan, created_at: str) -> str:
    payload = json.dumps(
        {
            "kind": plan.event_kind,
            "revision": plan.upstream_revision_id,
            "downstream": plan.downstream,
            "createdAt": created_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "invalidation://" + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def apply_invalidation_plan(
    connection: sqlite3.Connection,
    plan: InvalidationPlan,
    *,
    created_at: str | None = None,
) -> dict[str, object]:
    """Mark or clear only derived records named by a reviewed plan."""

    created_at = created_at or datetime.now(UTC).isoformat(timespec="seconds")
    event_id = _event_id(plan, created_at)
    connection.execute(
        """
        INSERT INTO invalidation_events(
            event_id, event_kind, upstream_revision_id,
            payload_json, created_at, status
        ) VALUES (?, ?, ?, ?, ?, 'APPLIED')
        """,
        (
            event_id,
            plan.event_kind,
            plan.upstream_revision_id,
            json.dumps(plan.downstream, separators=(",", ":")),
            created_at,
        ),
    )
    queue_rows = [
        (
            event_id,
            kind,
            downstream_id,
            plan.reasons[kind],
            "PENDING_REBUILD",
        )
        for kind, values in plan.downstream.items()
        for downstream_id in values
    ]
    connection.executemany(
        "INSERT INTO invalidation_queue VALUES (?, ?, ?, ?, ?)",
        queue_rows,
    )

    def values(kind: str) -> tuple[int, ...]:
        return plan.downstream.get(kind, ())

    if fact_ids := values("FACT"):
        placeholders = ",".join("?" for _ in fact_ids)
        connection.execute(
            f"UPDATE facts SET current=0 WHERE fact_id IN ({placeholders})",
            fact_ids,
        )
    if effective_entities := values("EFFECTIVE_ENTITY"):
        placeholders = ",".join("?" for _ in effective_entities)
        connection.execute(
            f"DELETE FROM effective_facts WHERE entity_id IN ({placeholders})",
            effective_entities,
        )
    if role_entities := values("ROLE_ENTITY"):
        placeholders = ",".join("?" for _ in role_entities)
        connection.execute(
            f"UPDATE knowledge_roles SET status='STALE' WHERE entity_id IN ({placeholders})",
            role_entities,
        )
    if domain_entities := values("DOMAIN_ENTITY"):
        placeholders = ",".join("?" for _ in domain_entities)
        connection.execute(
            f"UPDATE domain_memberships SET status='STALE' WHERE entity_id IN ({placeholders})",
            domain_entities,
        )
    if native_functions := values("NATIVE_FUNCTION"):
        placeholders = ",".join("?" for _ in native_functions)
        connection.execute(
            f"UPDATE native_functions SET status='STALE' WHERE native_function_id IN ({placeholders})",
            native_functions,
        )
        connection.execute(
            f"""
            UPDATE native_blueprint_links
            SET status='CANDIDATE', confidence='LOW'
            WHERE native_function_id IN ({placeholders})
            """,
            native_functions,
        )
    if values("PROJECTION"):
        connection.execute(
            "UPDATE projection_runs SET validation_status='STALE'"
        )
    connection.commit()
    return {
        "eventId": event_id,
        "eventKind": plan.event_kind,
        "affected": plan.affected_count,
        "queueRows": len(queue_rows),
    }
