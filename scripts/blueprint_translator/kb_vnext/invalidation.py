"""Selective invalidation planning and dependency propagation for KB vNext."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterable, Mapping

from .class_hierarchy import (
    CONFIRMED_ASSIGNMENT_CONFIDENCE,
    CONFIRMED_ASSIGNMENT_STATUSES,
    CONFIRMED_CLASS_CONFIDENCE,
    CONFIRMED_CLASS_STATUSES,
    _affected_descendants,
    _graph,
)
from .projections import DOMAIN_PROJECTIONS
from .query_planner import is_valid_generic_evidence_uri


CHANGE_KINDS = {
    "ASSET",
    "CLASS",
    "REGISTRY",
    "NATIVE",
    "ONTOLOGY",
    "PARSER",
}
_CONFIRMED_PATH_STATUSES = {"CONFIRMED", "VERIFIED", "RESOLVED"}
_CONFIRMED_PATH_CONFIDENCE = {"HIGH", "CONFIRMED"}
_INHERITANCE_EDGE_KINDS = {"blueprint_parent", "native_parent", "parent"}
_EFFECTIVE_PATH_SCHEMA = "ark-kb-effective-path/v1"
_NATIVE_ROOT_PROOF_SCHEMA = "ark-kb-native-root-proof/v1"
_EFFECTIVE_PATH_FIELDS = {
    "schema",
    "startClassId",
    "declaredOnClassId",
    "declaredOnEntityId",
    "overrideDepth",
    "classes",
    "edges",
    "nativeRootProof",
}
_NATIVE_ROOT_PROOF_FIELDS = {
    "schema",
    "startClassId",
    "rootClassId",
    "classes",
    "edges",
    "sourceRevision",
}
RevisionIdentity = tuple[str, str, str, str, str, str, str]
_MAX_EFFECTIVE_DEPENDENCY_CLASSES = 4096
_ADDITIVE_REQUIRED_DERIVED_KINDS = {
    "ROLE_ENTITY",
    "DOMAIN_ENTITY",
    "PROJECTION",
    "QUERY_SNAPSHOT",
}
_ADDITIVE_ALLOWED_DEPENDENCY_KINDS = {
    "CLASS_CLOSURE",
    "REGISTRATION_ENTITY",
    "FACT",
    "EDGE_ENTITY",
    "NATIVE_FUNCTION",
    "BLUEPRINT_NATIVE_ENTITY",
    "EFFECTIVE_ENTITY",
    "ROLE_ENTITY",
    "DOMAIN_ENTITY",
    "PROJECTION",
    "QUERY_SNAPSHOT",
}


@dataclass(frozen=True)
class InvalidationPlan:
    event_kind: str
    upstream_revision_id: int | None
    downstream: Mapping[str, tuple[int, ...]]
    reasons: Mapping[str, str]
    class_closure_scopes: Mapping[int, tuple[int, ...]] = field(
        default_factory=dict
    )
    role_scope_proof: Mapping[str, object] = field(default_factory=dict)

    @property
    def affected_count(self) -> int:
        return sum(len(values) for values in self.downstream.values())


class InvalidationBlockedGap(ValueError):
    """A selective plan cannot safely represent the observed durable delta."""

    status = "BLOCKED_GAP"

    def __init__(self, gap_code: str, message: str) -> None:
        super().__init__(message)
        self.gap_code = gap_code


def _table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM sqlite_schema
            WHERE type='table' AND name=?
            """,
            (table_name,),
        ).fetchone()
        is not None
    )


def _required_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"Invalid effective resolution path: {field} must be an integer"
        )
    return int(value)


def _stable_revision_hash(
    identities: Iterable[RevisionIdentity],
) -> str:
    payload = sorted(set(identities))
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _path_edge_revision_dependencies(
    *,
    path: Mapping[str, object],
    path_name: str,
    expected_start_class_id: int,
    expected_end_class_id: int,
    eligible_edges: Mapping[
        tuple[int, int],
        tuple[tuple[str, str, int], ...],
    ],
) -> set[int]:
    classes_value = path.get("classes")
    edges_value = path.get("edges")
    if not isinstance(classes_value, list) or not isinstance(edges_value, list):
        raise ValueError(
            f"Invalid effective resolution path: {path_name} "
            "classes and edges must be arrays"
        )
    classes = tuple(
        _required_int(value, field=f"{path_name}.classes")
        for value in classes_value
    )
    if (
        not classes
        or classes[0] != expected_start_class_id
        or classes[-1] != expected_end_class_id
        or len(classes) != len(edges_value) + 1
        or len(set(classes)) != len(classes)
    ):
        raise ValueError(
            f"Invalid effective resolution path: {path_name} "
            "does not form the declared class chain"
        )

    revision_ids: set[int] = set()
    for index, edge_value in enumerate(edges_value):
        if not isinstance(edge_value, dict):
            raise ValueError(
                f"Invalid effective resolution path: {path_name}.edges "
                "must contain objects"
            )
        child_class_id = _required_int(
            edge_value.get("childClassId"),
            field=f"{path_name}.edges.childClassId",
        )
        parent_class_id = _required_int(
            edge_value.get("parentClassId"),
            field=f"{path_name}.edges.parentClassId",
        )
        if (
            child_class_id != classes[index]
            or parent_class_id != classes[index + 1]
            or edge_value.get("status") != "CONFIRMED"
        ):
            raise ValueError(
                f"Invalid effective resolution path: {path_name} "
                "edge does not match its class chain"
            )
        edge_kind = edge_value.get("edgeKind")
        evidence_ids_value = edge_value.get("evidenceIds")
        if (
            not isinstance(edge_kind, str)
            or edge_kind not in _INHERITANCE_EDGE_KINDS
            or not isinstance(evidence_ids_value, list)
            or not evidence_ids_value
            or any(
                not isinstance(evidence_id, str) or not evidence_id
                for evidence_id in evidence_ids_value
            )
        ):
            raise ValueError(
                f"Invalid effective resolution path: {path_name} "
                "edge provenance is incomplete"
            )

        supports = eligible_edges.get(
            (child_class_id, parent_class_id),
            (),
        )
        support_evidence_ids = {support[1] for support in supports}
        evidence_ids = set(evidence_ids_value)
        support_kinds = {support[0] for support in supports}
        if (
            not supports
            or evidence_ids != support_evidence_ids
            or edge_kind != sorted(support_kinds)[0]
        ):
            raise ValueError(
                f"Invalid effective resolution path: {path_name} "
                "edge evidence no longer matches fresh class-edge support"
            )
        revision_ids.update(support[2] for support in supports)
    return revision_ids


def _blocked_path_revision_dependencies(
    *,
    start_class_id: int,
    parent_graph: Mapping[
        int,
        tuple[tuple[int, int | None], ...],
    ],
    native_class_ids: set[int],
    native_class_revision_ids: Mapping[int, int],
) -> tuple[set[int], set[int]]:
    """Bind only existing ancestor paths that can block one effective key."""

    edge_revision_ids: set[int] = set()
    native_revision_ids: set[int] = set()
    pending = [start_class_id]
    visited: set[int] = set()
    while pending:
        class_id = pending.pop()
        if class_id in visited:
            continue
        visited.add(class_id)
        if len(visited) > _MAX_EFFECTIVE_DEPENDENCY_CLASSES:
            raise ValueError(
                "Invalid effective resolution path: blocked ancestor graph "
                "exceeds the bounded dependency limit"
            )
        if class_id in native_class_ids:
            revision_id = native_class_revision_ids.get(class_id)
            if revision_id is not None:
                native_revision_ids.add(revision_id)
            continue
        for parent_class_id, revision_id in parent_graph.get(class_id, ()):
            if revision_id is not None:
                edge_revision_ids.add(revision_id)
            if parent_class_id not in visited:
                pending.append(parent_class_id)
    return edge_revision_ids, native_revision_ids


def _assignment_is_verified(
    assignment: tuple[tuple[int, str, str], ...],
) -> bool:
    return (
        len(assignment) == 1
        and assignment[0][1] in CONFIRMED_ASSIGNMENT_STATUSES
        and assignment[0][2] in CONFIRMED_ASSIGNMENT_CONFIDENCE
    )


def validate_effective_resolution_dependencies(
    connection: sqlite3.Connection,
) -> set[tuple[int, str, int, str]]:
    """Validate effective reality and return its exact revision dependencies."""

    revision_identities = {
        int(revision_id): (
            str(source_kind),
            str(source_uri),
            str(source_fingerprint),
            str(producer_version),
            str(schema_version),
            str(generated_at),
            str(freshness_status).upper(),
        )
        for (
            revision_id,
            source_kind,
            source_uri,
            source_fingerprint,
            producer_version,
            schema_version,
            generated_at,
            freshness_status,
        ) in connection.execute(
            """
            SELECT revision_id, source_kind, source_uri,
                   source_fingerprint, producer_version,
                   schema_version, generated_at, freshness_status
            FROM source_revisions
            """
        )
    }
    parent_graph_values: dict[
        int,
        list[tuple[int, int | None]],
    ] = {}
    for (
        child_class_id,
        parent_class_id,
        edge_kind,
        source_revision_id,
    ) in connection.execute(
        """
        SELECT child_class_id, parent_class_id,
               edge_kind, source_revision_id
        FROM class_edges
        ORDER BY child_class_id, parent_class_id, edge_kind, evidence_id
        """
    ):
        if str(edge_kind) not in _INHERITANCE_EDGE_KINDS:
            continue
        revision_id = (
            int(source_revision_id)
            if source_revision_id is not None
            and int(source_revision_id) in revision_identities
            else None
        )
        value = (int(parent_class_id), revision_id)
        values = parent_graph_values.setdefault(int(child_class_id), [])
        if value not in values:
            values.append(value)
    parent_graph = {
        child_class_id: tuple(values)
        for child_class_id, values in parent_graph_values.items()
    }
    assignment_values: dict[
        int,
        list[tuple[int, str, str]],
    ] = {}
    for entity_id, class_id, status, confidence in connection.execute(
        """
        SELECT entity_id, class_id, status, confidence
        FROM asset_class_assignments
        WHERE assignment_kind='GENERATED_CLASS'
        ORDER BY entity_id, class_id
        """
    ):
        assignment_values.setdefault(int(entity_id), []).append(
            (
                int(class_id),
                str(status).upper(),
                str(confidence).upper(),
            )
        )
    assignments = {
        entity_id: tuple(values)
        for entity_id, values in assignment_values.items()
    }
    facts = {
        int(fact_id): (
            int(subject_entity_id),
            (
                int(declared_on_entity_id)
                if declared_on_entity_id is not None
                else None
            ),
            str(fact_type),
            str(fact_name),
            str(scope_kind),
            int(current),
        )
        for (
            fact_id,
            subject_entity_id,
            declared_on_entity_id,
            fact_type,
            fact_name,
            scope_kind,
            current,
        ) in connection.execute(
            """
            SELECT fact_id, subject_entity_id, declared_on_entity_id,
                   fact_type, fact_name, scope_kind, current
            FROM facts
            """
        )
    }
    fresh_fact_revisions: dict[
        int,
        set[RevisionIdentity],
    ] = {}
    for fact_id, revision_id in connection.execute(
        "SELECT fact_id, source_revision_id FROM fact_evidence"
    ):
        identity = revision_identities.get(int(revision_id))
        if identity is not None and identity[6] == "FRESH":
            fresh_fact_revisions.setdefault(int(fact_id), set()).add(identity)
    selected_candidate_values: dict[
        tuple[int, str, str],
        list[tuple[int, int, int, str, str]],
    ] = {}
    if _table_exists(connection, "effective_fact_candidates"):
        for (
            entity_id,
            fact_type,
            fact_name,
            candidate_fact_id,
            declared_on_entity_id,
            inheritance_depth,
            path_status,
            rejection_reason,
        ) in connection.execute(
            """
            SELECT entity_id, fact_type, fact_name, candidate_fact_id,
                   declared_on_entity_id, inheritance_depth,
                   path_status, rejection_reason
            FROM effective_fact_candidates
            WHERE selected=1
            ORDER BY entity_id, fact_type, fact_name, candidate_fact_id
            """
        ):
            selected_candidate_values.setdefault(
                (int(entity_id), str(fact_type), str(fact_name)),
                [],
            ).append(
                (
                    int(candidate_fact_id),
                    int(declared_on_entity_id),
                    int(inheritance_depth),
                    str(path_status),
                    str(rejection_reason),
                )
            )
    selected_candidates = {
        key: tuple(values)
        for key, values in selected_candidate_values.items()
    }
    eligible_edges: dict[
        tuple[int, int],
        list[tuple[str, str, int]],
    ] = {}
    for (
        child_class_id,
        parent_class_id,
        edge_kind,
        evidence_id,
        source_revision_id,
        status,
        confidence,
        freshness_status,
    ) in connection.execute(
        """
        SELECT
            edge.child_class_id, edge.parent_class_id, edge.edge_kind,
            edge.evidence_id, edge.source_revision_id, edge.status,
            edge.confidence, revision.freshness_status
        FROM class_edges AS edge
        JOIN source_revisions AS revision
          ON revision.revision_id=edge.source_revision_id
        ORDER BY
            edge.child_class_id, edge.parent_class_id,
            edge.edge_kind, edge.evidence_id, edge.source_revision_id
        """
    ):
        normalized_kind = str(edge_kind)
        if (
            normalized_kind not in _INHERITANCE_EDGE_KINDS
            or str(status).upper() not in _CONFIRMED_PATH_STATUSES
            or str(confidence).upper() not in _CONFIRMED_PATH_CONFIDENCE
            or str(freshness_status).upper() != "FRESH"
        ):
            continue
        eligible_edges.setdefault(
            (int(child_class_id), int(parent_class_id)),
            [],
        ).append(
            (
                normalized_kind,
                str(evidence_id),
                int(source_revision_id),
            )
        )
    frozen_edges = {
        key: tuple(values) for key, values in eligible_edges.items()
    }
    native_class_sources: dict[
        int,
        tuple[int, dict[str, str]],
    ] = {}
    native_class_ids: set[int] = set()
    native_class_revision_ids: dict[int, int] = {}
    for (
        class_id,
        source_revision_id,
        class_status,
        class_confidence,
        source_kind,
        source_uri,
        source_fingerprint,
        producer_version,
        schema_version,
        generated_at,
        freshness_status,
    ) in connection.execute(
        """
        SELECT
            class.class_id, class.source_revision_id,
            class.status, class.confidence,
            revision.source_kind, revision.source_uri,
            revision.source_fingerprint, revision.producer_version,
            revision.schema_version, revision.generated_at,
            revision.freshness_status
        FROM classes AS class
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=class.source_revision_id
        WHERE class.is_native=1
        ORDER BY class.class_id
        """
    ):
        normalized_class_id = int(class_id)
        native_class_ids.add(normalized_class_id)
        if (
            source_revision_id is not None
            and int(source_revision_id) in revision_identities
        ):
            native_class_revision_ids[normalized_class_id] = int(
                source_revision_id
            )
        if (
            source_revision_id is None
            or int(source_revision_id) not in revision_identities
            or str(class_status).upper() not in CONFIRMED_CLASS_STATUSES
            or str(class_confidence).upper()
            not in CONFIRMED_CLASS_CONFIDENCE
            or str(freshness_status).upper() != "FRESH"
        ):
            continue
        native_class_sources[normalized_class_id] = (
            int(source_revision_id),
            {
                "sourceKind": str(source_kind),
                "sourceUri": str(source_uri),
                "sourceFingerprint": str(source_fingerprint),
                "producerVersion": str(producer_version),
                "schemaVersion": str(schema_version),
                "generatedAt": str(generated_at),
                "freshnessStatus": str(freshness_status).upper(),
            },
        )
    dependencies: set[tuple[int, str, int, str]] = set()
    blocked_dependency_cache: dict[int, tuple[set[int], set[int]]] = {}
    for (
        entity_id,
        effective_fact_type,
        effective_fact_name,
        effective_fact_id,
        inherited_from_entity_id,
        resolution_status,
        source_revision_set_hash,
        resolution_chain_json,
    ) in connection.execute(
        """
        SELECT entity_id, fact_type, fact_name, fact_id,
               inherited_from_entity_id, resolution_status,
               source_revision_set_hash, resolution_chain_json
        FROM effective_facts
        ORDER BY entity_id, fact_type, fact_name
        """
    ):
        normalized_entity_id = int(entity_id)
        effective_key = (
            normalized_entity_id,
            str(effective_fact_type),
            str(effective_fact_name),
        )
        if str(effective_fact_type) != "EFFECTIVE_DEFAULT":
            raise ValueError(
                "Invalid effective resolution path: unsupported effective "
                "fact type"
            )
        assignment = assignments.get(normalized_entity_id, ())
        try:
            chain = json.loads(str(resolution_chain_json))
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Invalid effective resolution path: malformed JSON for "
                f"entity {entity_id}"
            ) from error
        if (
            not isinstance(chain, dict)
            or chain.get("schema") != _EFFECTIVE_PATH_SCHEMA
            or not _EFFECTIVE_PATH_FIELDS.issubset(chain)
        ):
            raise ValueError(
                "Invalid effective resolution path: unsupported envelope for "
                f"entity {entity_id}"
            )
        if str(resolution_status) != "RESOLVED":
            unresolved_start_class_id = chain.get("startClassId")
            if unresolved_start_class_id is not None:
                start_class_id = _required_int(
                    unresolved_start_class_id,
                    field="startClassId",
                )
                if (
                    len(assignment) != 1
                    or assignment[0][0] != start_class_id
                ):
                    raise ValueError(
                        "Invalid effective resolution path: unresolved "
                        "startClassId does not match its assignment"
                    )
            elif len(assignment) == 1:
                raise ValueError(
                    "Invalid effective resolution path: unresolved assignment "
                    "is missing its startClassId"
                )
            if (
                effective_fact_id is not None
                or inherited_from_entity_id is not None
                or chain.get("declaredOnClassId") is not None
                or chain.get("declaredOnEntityId") is not None
                or chain.get("overrideDepth") is not None
                or chain.get("classes") != []
                or chain.get("edges") != []
                or chain.get("nativeRootProof") is not None
            ):
                raise ValueError(
                    "Invalid effective resolution path: unresolved facts "
                    "must not contain selected path evidence"
                )
            if selected_candidates.get(effective_key):
                raise ValueError(
                    "Invalid effective resolution path: unresolved fact has "
                    "a selected candidate"
                )
            if str(source_revision_set_hash) != _stable_revision_hash(()):
                raise ValueError(
                    "Invalid effective resolution path: unresolved revision "
                    "hash must be empty"
                )
            if unresolved_start_class_id is not None:
                blocked_dependencies = blocked_dependency_cache.get(
                    start_class_id
                )
                if blocked_dependencies is None:
                    blocked_dependencies = (
                        _blocked_path_revision_dependencies(
                            start_class_id=start_class_id,
                            parent_graph=parent_graph,
                            native_class_ids=native_class_ids,
                            native_class_revision_ids=(
                                native_class_revision_ids
                            ),
                        )
                    )
                    blocked_dependency_cache[
                        start_class_id
                    ] = blocked_dependencies
                (
                    blocked_edge_revisions,
                    blocked_native_revisions,
                ) = blocked_dependencies
                dependencies.update(
                    (
                        revision_id,
                        "EFFECTIVE_ENTITY",
                        normalized_entity_id,
                        "EFFECTIVE_BLOCKED_CLASS_EDGE",
                    )
                    for revision_id in blocked_edge_revisions
                )
                dependencies.update(
                    (
                        revision_id,
                        "EFFECTIVE_ENTITY",
                        normalized_entity_id,
                        "EFFECTIVE_BLOCKED_NATIVE_ROOT_SOURCE",
                    )
                    for revision_id in blocked_native_revisions
                )
            continue

        start_class_id = _required_int(
            chain.get("startClassId"),
            field="startClassId",
        )
        declared_class_id = _required_int(
            chain.get("declaredOnClassId"),
            field="declaredOnClassId",
        )
        declared_entity_id = _required_int(
            chain.get("declaredOnEntityId"),
            field="declaredOnEntityId",
        )
        override_depth = _required_int(
            chain.get("overrideDepth"),
            field="overrideDepth",
        )
        if (
            effective_fact_id is None
            or not _assignment_is_verified(assignment)
            or assignment[0][0] != start_class_id
        ):
            raise ValueError(
                "Invalid effective resolution path: target assignment does "
                "not match the resolved startClassId"
            )
        normalized_fact_id = int(effective_fact_id)
        fact = facts.get(normalized_fact_id)
        if (
            fact is None
            or fact[0] != declared_entity_id
            or fact[1] != declared_entity_id
            or fact[2] != "DECLARED_DEFAULT"
            or fact[3] != str(effective_fact_name)
            or fact[4] != "DECLARED"
            or fact[5] != 1
        ):
            raise ValueError(
                "Invalid effective resolution path: selected fact is not the "
                "current declared default named by the chain"
            )
        owner_assignment = assignments.get(declared_entity_id, ())
        if (
            not _assignment_is_verified(owner_assignment)
            or owner_assignment[0][0] != declared_class_id
        ):
            raise ValueError(
                "Invalid effective resolution path: declared owner assignment "
                "does not match declaredOnClassId"
            )
        expected_inherited_from = (
            None
            if declared_entity_id == normalized_entity_id
            else declared_entity_id
        )
        if inherited_from_entity_id != expected_inherited_from:
            raise ValueError(
                "Invalid effective resolution path: inherited owner does not "
                "match declaredOnEntityId"
            )
        expected_path_status = (
            "SELF" if override_depth == 0 else "CONFIRMED"
        )
        if selected_candidates.get(effective_key, ()) != (
            (
                normalized_fact_id,
                declared_entity_id,
                override_depth,
                expected_path_status,
                "",
            ),
        ):
            raise ValueError(
                "Invalid effective resolution path: selected candidate does "
                "not match the effective fact and chain"
            )
        selection_revisions = _path_edge_revision_dependencies(
            path=chain,
            path_name="selection",
            expected_start_class_id=start_class_id,
            expected_end_class_id=declared_class_id,
            eligible_edges=frozen_edges,
        )
        if (
            override_depth != len(chain["edges"])
        ):
            raise ValueError(
                "Invalid effective resolution path: overrideDepth does not "
                "match selection edges"
            )

        native_root_proof = chain.get("nativeRootProof")
        if (
            not isinstance(native_root_proof, dict)
            or native_root_proof.get("schema") != _NATIVE_ROOT_PROOF_SCHEMA
            or not _NATIVE_ROOT_PROOF_FIELDS.issubset(native_root_proof)
        ):
            raise ValueError(
                "Invalid effective resolution path: RESOLVED fact is missing "
                "its native-root proof"
            )
        if (
            _required_int(
                native_root_proof.get("startClassId"),
                field="nativeRootProof.startClassId",
            )
            != start_class_id
        ):
            raise ValueError(
                "Invalid effective resolution path: native-root proof starts "
                "from a different class"
            )
        root_class_id = _required_int(
            native_root_proof.get("rootClassId"),
            field="nativeRootProof.rootClassId",
        )
        if root_class_id not in native_class_sources:
            raise ValueError(
                "Invalid effective resolution path: native-root proof does "
                "not end at a fresh confirmed native class"
            )
        root_source_revision_id, root_source_identity = native_class_sources[
            root_class_id
        ]
        if native_root_proof.get("sourceRevision") != root_source_identity:
            raise ValueError(
                "Invalid effective resolution path: native-root source "
                "revision no longer matches the native class"
            )
        root_revisions = _path_edge_revision_dependencies(
            path=native_root_proof,
            path_name="nativeRootProof",
            expected_start_class_id=start_class_id,
            expected_end_class_id=root_class_id,
            eligible_edges=frozen_edges,
        )
        fact_revision_values = fresh_fact_revisions.get(
            normalized_fact_id,
            set(),
        )
        if not fact_revision_values:
            raise ValueError(
                "Invalid effective resolution path: selected fact has no "
                "fresh source revision"
            )
        expected_revision_values = set(fact_revision_values)
        expected_revision_values.update(
            revision_identities[revision_id]
            for revision_id in selection_revisions | root_revisions
        )
        expected_revision_values.add(
            revision_identities[root_source_revision_id]
        )
        if str(source_revision_set_hash) != _stable_revision_hash(
            expected_revision_values
        ):
            raise ValueError(
                "Invalid effective resolution path: source revision set hash "
                "does not match the selected fact and proof"
            )
        dependencies.update(
            (
                revision_id,
                "EFFECTIVE_ENTITY",
                int(entity_id),
                "EFFECTIVE_SELECTED_CLASS_EDGE",
            )
            for revision_id in selection_revisions
        )
        dependencies.update(
            (
                revision_id,
                "EFFECTIVE_ENTITY",
                int(entity_id),
                "EFFECTIVE_NATIVE_ROOT_CLASS_EDGE",
            )
            for revision_id in root_revisions
        )
        dependencies.add(
            (
                root_source_revision_id,
                "EFFECTIVE_ENTITY",
                int(entity_id),
                "EFFECTIVE_NATIVE_ROOT_SOURCE",
            )
        )
    return dependencies


def _replace_invalidation_dependencies(
    connection: sqlite3.Connection,
    effective_edge_dependencies: set[tuple[int, str, int, str]],
) -> dict[str, int]:
    """Replace dependencies after the caller validates and opens a savepoint."""

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
    if _table_exists(connection, "effective_fact_candidates"):
        connection.execute(
            """
            INSERT OR IGNORE INTO invalidation_dependencies
            SELECT
                evidence.source_revision_id,
                'EFFECTIVE_ENTITY',
                candidate.entity_id,
                'EFFECTIVE_FACT_SOURCE'
            FROM effective_fact_candidates AS candidate
            JOIN fact_evidence AS evidence
              ON evidence.fact_id=candidate.candidate_fact_id
            """
        )
    connection.executemany(
        """
        INSERT OR IGNORE INTO invalidation_dependencies(
            upstream_revision_id, downstream_kind,
            downstream_id, dependency_reason
        ) VALUES (?, ?, ?, ?)
        """,
        sorted(effective_edge_dependencies),
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
            link.blueprint_graph_source_revision_id,
            'BLUEPRINT_NATIVE_ENTITY',
            link.blueprint_entity_id,
            'BLUEPRINT_GRAPH_BINDING'
        FROM native_blueprint_links AS link
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
    connection.execute(
        """
        INSERT OR IGNORE INTO invalidation_dependencies
        SELECT
            source_revision_id, 'ROLE_ENTITY',
            entity_id, 'ROLE_CLASSIFIER_REVISION'
        FROM knowledge_roles
        WHERE source_revision_id IS NOT NULL
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO invalidation_dependencies
        SELECT
            source_revision_id, 'DOMAIN_ENTITY',
            entity_id, 'DOMAIN_ONTOLOGY_REVISION'
        FROM domain_memberships
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


def rebuild_invalidation_dependencies(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    """Atomically materialize revision-to-derived-record dependencies."""

    effective_edge_dependencies = validate_effective_resolution_dependencies(
        connection
    )
    savepoint = "rebuild_invalidation_dependencies"
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        result = _replace_invalidation_dependencies(
            connection,
            effective_edge_dependencies,
        )
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    connection.commit()
    return result


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


def _class_closure_scopes(
    connection: sqlite3.Connection,
    *,
    changed_class_ids: set[int],
    prechange_affected_entity_ids: set[int],
) -> dict[int, tuple[int, ...]]:
    """Bind each class task to both old and current descendants.

    The durable closure still describes the old topology when an edge has
    already changed, while the current graph describes newly attached
    descendants.  Entity IDs supplied by the caller preserve an additional
    pre-change boundary when the old closure was already partially removed.
    """

    if not changed_class_ids:
        return {}
    _parents, children = _graph(connection)
    prechange_classes = _entity_classes(
        connection, prechange_affected_entity_ids
    )
    result: dict[int, tuple[int, ...]] = {}
    for changed_class_id in sorted(changed_class_ids):
        durable_descendants = {
            int(row[0])
            for row in connection.execute(
                """
                SELECT descendant_class_id
                FROM class_closure
                WHERE ancestor_class_id=?
                """,
                (changed_class_id,),
            )
        }
        affected = (
            _affected_descendants(children, (changed_class_id,))
            | durable_descendants
            | prechange_classes
            | {changed_class_id}
        )
        result[changed_class_id] = tuple(sorted(affected))
    return result


def _class_source_revision_proof(
    connection: sqlite3.Connection,
    class_ids: Iterable[int],
) -> str:
    values = tuple(sorted({int(value) for value in class_ids}))
    if not values:
        return ""
    placeholders = ",".join("?" for _ in values)
    rows = [
        (
            int(class_id),
            int(revision_id) if revision_id is not None else None,
            str(source_fingerprint or ""),
            str(freshness_status or ""),
        )
        for (
            class_id,
            revision_id,
            source_fingerprint,
            freshness_status,
        ) in connection.execute(
            f"""
            SELECT class.class_id, class.source_revision_id,
                   revision.source_fingerprint,
                   revision.freshness_status
            FROM classes AS class
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=class.source_revision_id
            WHERE class.class_id IN ({placeholders})
            ORDER BY class.class_id
            """,
            values,
        )
    ]
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def plan_invalidation(
    connection: sqlite3.Connection,
    *,
    event_kind: str,
    entity_ids: Iterable[int] = (),
    class_ids: Iterable[int] = (),
    affected_entity_ids: Iterable[int] = (),
    upstream_revision_id: int | None = None,
) -> InvalidationPlan:
    """Return a bounded recomputation plan without mutating source Evidence."""

    event_kind = event_kind.upper()
    if event_kind not in CHANGE_KINDS:
        raise ValueError(f"Unsupported invalidation event: {event_kind}")
    entities = {int(value) for value in entity_ids}
    classes = {int(value) for value in class_ids}
    prechange_affected_entities = {
        int(value) for value in affected_entity_ids
    }
    class_closure_scopes: dict[int, tuple[int, ...]] = {}
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
        class_closure_scopes = _class_closure_scopes(
            connection,
            changed_class_ids=classes,
            prechange_affected_entity_ids=prechange_affected_entities,
        )
        descendants = (
            _descendant_entities(connection, classes)
            | prechange_affected_entities
        )
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
        class_closure_scopes=class_closure_scopes,
    )


def plan_additive_asset_invalidation(
    connection: sqlite3.Connection,
    *,
    fact_ids: Iterable[int],
    entity_ids: Iterable[int],
    source_revision_ids: Iterable[int],
    actual_write_tables: Iterable[str],
    role_entity_ids: Iterable[int] | None = None,
    role_scope_proof: Mapping[str, object] | None = None,
) -> InvalidationPlan:
    """Plan only dependencies proven by an add-only Blueprint fact delta.

    This deliberately requires exact materialized role, domain, projection,
    and query dependency coverage instead of treating the event kind alone as
    proof that a broad generic ASSET plan is complete.
    """

    def strict_ids(values: Iterable[int], field: str) -> set[int]:
        raw = tuple(values)
        if (
            not raw
            or any(type(value) is not int or value < 1 for value in raw)
            or len(raw) != len(set(raw))
        ):
            raise InvalidationBlockedGap(
                "ADDITIVE_ASSET_WRITE_SCOPE_UNSUPPORTED",
                f"add-only ASSET {field} must contain unique positive "
                "integer IDs",
            )
        return set(raw)

    facts = strict_ids(fact_ids, "fact scope")
    entities = strict_ids(entity_ids, "entity scope")
    revisions = strict_ids(source_revision_ids, "revision scope")
    role_entities = strict_ids(
        role_entity_ids if role_entity_ids is not None else entities,
        "role entity scope",
    )
    if not entities.issubset(role_entities):
        raise InvalidationBlockedGap(
            "ADDITIVE_ROLE_DEPENDENCY_SCOPE_INVALID",
            "role dependency scope must include every changed entity",
        )
    role_proof = dict(role_scope_proof or {})
    if role_scope_proof is not None and (
        role_proof.get("schema")
        != "ark-kb-additive-role-dependency-scope/v1"
        or role_proof.get("changedEntityIds") != sorted(entities)
        or role_proof.get("roleEntityIds") != sorted(role_entities)
        or type(role_proof.get("sourceRevisionId")) is not int
        or role_proof.get("triggerSourceRevisionIds") != sorted(revisions)
        or not str(role_proof.get("proof") or "").startswith("role-scope://")
    ):
        raise InvalidationBlockedGap(
            "ADDITIVE_ROLE_DEPENDENCY_SCOPE_INVALID",
            "role dependency proof is missing or does not match the scope",
        )
    raw_tables = tuple(actual_write_tables)
    if any(type(value) is not str for value in raw_tables):
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_WRITE_SCOPE_UNSUPPORTED",
            "add-only ASSET write table scope is not textual",
        )
    tables = set(raw_tables)
    allowed_tables = {"source_revisions", "facts", "fact_evidence"}
    required_tables = {"source_revisions", "fact_evidence"}
    if (
        not facts
        or not entities
        or not revisions
        or not required_tables.issubset(tables)
        or not tables.issubset(allowed_tables)
    ):
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_WRITE_SCOPE_UNSUPPORTED",
            "add-only ASSET invalidation requires exact source, fact, "
            "evidence, and entity scope",
        )

    fact_placeholders = ",".join("?" for _ in facts)
    fact_rows = list(
        connection.execute(
            f"""
            SELECT fact_id, subject_entity_id, fact_type, scope_kind,
                   status, confidence, current
            FROM facts
            WHERE fact_id IN ({fact_placeholders})
            ORDER BY fact_id
            """,
            tuple(sorted(facts)),
        )
    )
    if (
        any(
            type(row[0]) is not int
            or type(row[1]) is not int
            or type(row[6]) is not int
            for row in fact_rows
        )
        or {row[0] for row in fact_rows} != facts
        or any(
            row[1] not in entities
            or str(row[2]).upper() != "DECLARED_DEFAULT"
            or str(row[3]).upper() != "DECLARED"
            for row in fact_rows
        )
    ):
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_FACT_SCOPE_INVALID",
            "fact scope is missing, unrelated, or not a declared default",
        )
    if any(
        str(row[4]).upper() != "CONFIRMED"
        or str(row[5]).upper() != "HIGH"
        or row[6] != 1
        for row in fact_rows
    ):
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_FACT_QUALITY_INVALID",
            "new facts must be current CONFIRMED/HIGH facts",
        )

    revision_placeholders = ",".join("?" for _ in revisions)
    evidence_rows = list(
        connection.execute(
            f"""
            SELECT evidence.fact_id, evidence.source_revision_id,
                   evidence.evidence_uri, evidence.evidence_role,
                   revision.source_uri
            FROM fact_evidence AS evidence
            JOIN source_revisions AS revision
              ON revision.revision_id=evidence.source_revision_id
            WHERE evidence.fact_id IN ({fact_placeholders})
              AND evidence.source_revision_id IN ({revision_placeholders})
              AND LOWER(revision.source_kind)='blueprint_evidence'
              AND UPPER(revision.freshness_status)='FRESH'
            ORDER BY evidence.fact_id, evidence.source_revision_id
            """,
            (*sorted(facts), *sorted(revisions)),
        )
    )
    if (
        any(
            type(row[0]) is not int or type(row[1]) is not int
            for row in evidence_rows
        )
        or {row[0] for row in evidence_rows} != facts
    ):
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_FACT_EVIDENCE_MISSING",
            "each fact must bind a fresh added Blueprint source revision",
        )
    if any(
        str(row[3]).upper() != "DEFAULT_VALUE_ACTUAL"
        or not is_valid_generic_evidence_uri(row[2])
        or not str(row[2]).startswith(str(row[4]) + "/")
        for row in evidence_rows
    ):
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_FACT_EVIDENCE_INVALID",
            "fact evidence must use the bound Blueprint URI and actual role",
        )

    descendant_entities = _descendant_entities(
        connection,
        _entity_classes(connection, entities),
    )
    effective_entities = tuple(sorted(entities | descendant_entities))
    raw_dependency_rows = list(
        connection.execute(
            f"""
            SELECT upstream_revision_id, typeof(upstream_revision_id),
                   downstream_kind, typeof(downstream_kind),
                   downstream_id, typeof(downstream_id),
                   dependency_reason, typeof(dependency_reason)
            FROM invalidation_dependencies
            WHERE upstream_revision_id IN ({revision_placeholders})
            ORDER BY upstream_revision_id, downstream_kind,
                     downstream_id, dependency_reason
            """,
            tuple(sorted(revisions)),
        )
    )
    dependency_rows: list[tuple[int, str, int, str]] = []
    for (
        revision_id,
        revision_id_type,
        kind,
        kind_type,
        target_id,
        target_id_type,
        reason,
        reason_type,
    ) in raw_dependency_rows:
        if (
            type(revision_id) is not int
            or revision_id_type != "integer"
            or type(kind) is not str
            or kind_type != "text"
            or type(target_id) is not int
            or target_id_type != "integer"
            or type(reason) is not str
            or reason_type != "text"
        ):
            raise InvalidationBlockedGap(
                "ADDITIVE_ASSET_DERIVED_DEPENDENCIES_INVALID",
                "derived dependency graph contains non-canonical types",
            )
        dependency_rows.append(
            (revision_id, kind, target_id, reason)
        )
    if any(
        kind not in _ADDITIVE_ALLOWED_DEPENDENCY_KINDS
        or target_id < 1
        or not reason.strip()
        for _revision_id, kind, target_id, reason in dependency_rows
    ):
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_DERIVED_DEPENDENCIES_INVALID",
            "derived dependency graph contains an unsupported target",
        )

    required_targets = {
        "ROLE_ENTITY": set(role_entities),
        "DOMAIN_ENTITY": set(entities),
        "PROJECTION": _all_projection_ids(),
    }
    by_revision_kind: dict[tuple[int, str], set[int]] = {}
    for revision_id, kind, target_id, _reason in dependency_rows:
        by_revision_kind.setdefault((revision_id, kind), set()).add(target_id)
    incomplete: list[str] = []
    for revision_id in sorted(revisions):
        expected_by_kind = {
            kind: (
                {revision_id}
                if kind == "QUERY_SNAPSHOT"
                else required_targets[kind]
            )
            for kind in _ADDITIVE_REQUIRED_DERIVED_KINDS
        }
        for kind, expected in expected_by_kind.items():
            if by_revision_kind.get((revision_id, kind), set()) != expected:
                incomplete.append(f"{revision_id}:{kind}")
    if incomplete:
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_DERIVED_DEPENDENCIES_UNPROVEN",
            "derived dependency completeness is unproven for "
            + ", ".join(incomplete),
        )

    downstream: dict[str, set[int]] = {
        "EFFECTIVE_ENTITY": set(effective_entities),
        "FACT": set(facts),
    }
    reasons: dict[str, str] = {
        "EFFECTIVE_ENTITY": "ADDED_DECLARED_DEFAULT_OR_PARENT",
        "FACT": "ADDED_BLUEPRINT_FACT_EVIDENCE",
    }
    dependency_reasons: dict[str, set[str]] = {}
    for _revision_id, kind, target_id, reason in dependency_rows:
        if kind in {"FACT", "EFFECTIVE_ENTITY"}:
            continue
        downstream.setdefault(kind, set()).add(target_id)
        dependency_reasons.setdefault(kind, set()).add(reason)
    ambiguous = {
        kind: values
        for kind, values in dependency_reasons.items()
        if len(values) != 1
    }
    if ambiguous:
        raise InvalidationBlockedGap(
            "ADDITIVE_ASSET_DERIVED_DEPENDENCIES_AMBIGUOUS",
            "derived dependency reasons cannot be represented exactly",
        )
    reasons.update(
        {
            kind: next(iter(values))
            for kind, values in dependency_reasons.items()
        }
    )
    return InvalidationPlan(
        event_kind="ASSET",
        upstream_revision_id=None,
        downstream={
            kind: tuple(sorted(values))
            for kind, values in sorted(downstream.items())
        },
        reasons=dict(sorted(reasons.items())),
        role_scope_proof=role_proof,
    )


def _event_id(plan: InvalidationPlan, created_at: str) -> str:
    payload = json.dumps(
        {
            "kind": plan.event_kind,
            "revision": plan.upstream_revision_id,
            "downstream": plan.downstream,
            "classClosureScopes": plan.class_closure_scopes,
            "roleScopeProof": plan.role_scope_proof,
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
    event_payload: dict[str, object] = dict(plan.downstream)
    if plan.class_closure_scopes:
        event_payload["_classClosureScopes"] = {
            str(class_id): list(scope)
            for class_id, scope in sorted(plan.class_closure_scopes.items())
        }
        event_payload["_classClosureSourceRevisionProofs"] = {
            str(class_id): _class_source_revision_proof(connection, scope)
            for class_id, scope in sorted(plan.class_closure_scopes.items())
        }
    if plan.role_scope_proof:
        event_payload["_roleScopeProof"] = dict(plan.role_scope_proof)
        event_payload["_upstreamRevisionIds"] = list(
            plan.role_scope_proof.get("triggerSourceRevisionIds", ())
        )
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
            json.dumps(event_payload, separators=(",", ":")),
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
        if _table_exists(connection, "effective_fact_candidates"):
            connection.execute(
                f"""
                DELETE FROM effective_fact_candidates
                WHERE entity_id IN ({placeholders})
                """,
                effective_entities,
            )
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
            f"""
            UPDATE domain_memberships
            SET status='STALE'
            WHERE entity_id IN ({placeholders})
              AND membership_kind IN (
                  'CLASS_ANCESTRY', 'TYPED_REGISTRATION'
              )
            """,
            domain_entities,
        )
    if native_functions := values("NATIVE_FUNCTION"):
        placeholders = ",".join("?" for _ in native_functions)
        connection.execute(
            f"""
            UPDATE native_gold_targets
            SET status='GAP', gap_code='SOURCE_REVISION_STALE'
            WHERE native_function_id IN ({placeholders})
            """,
            native_functions,
        )
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
    if blueprint_native_entities := values("BLUEPRINT_NATIVE_ENTITY"):
        placeholders = ",".join("?" for _ in blueprint_native_entities)
        connection.execute(
            f"""
            UPDATE native_blueprint_links
            SET status='CANDIDATE', confidence='LOW'
            WHERE blueprint_entity_id IN ({placeholders})
            """,
            blueprint_native_entities,
        )
    if projection_ids := values("PROJECTION"):
        if any(
            projection_id < 1
            or projection_id > len(DOMAIN_PROJECTIONS)
            for projection_id in projection_ids
        ):
            raise InvalidationBlockedGap(
                "PROJECTION_SCOPE_INVALID",
                "projection downstream ID is outside the canonical mapping",
            )
        names = tuple(
            tuple(DOMAIN_PROJECTIONS)[projection_id - 1]
            for projection_id in projection_ids
        )
        placeholders = ",".join("?" for _ in names)
        connection.execute(
            f"""
            UPDATE projection_runs
            SET validation_status='STALE'
            WHERE projection_name IN ({placeholders})
            """,
            names,
        )
    connection.commit()
    return {
        "eventId": event_id,
        "eventKind": plan.event_kind,
        "affected": plan.affected_count,
        "queueRows": len(queue_rows),
    }
