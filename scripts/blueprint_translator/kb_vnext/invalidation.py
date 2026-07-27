"""Selective invalidation planning and dependency propagation for KB vNext."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable, Mapping

from .class_hierarchy import (
    CONFIRMED_ASSIGNMENT_CONFIDENCE,
    CONFIRMED_ASSIGNMENT_STATUSES,
    CONFIRMED_CLASS_CONFIDENCE,
    CONFIRMED_CLASS_STATUSES,
)
from .projections import DOMAIN_PROJECTIONS


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


@dataclass(frozen=True)
class InvalidationPlan:
    event_kind: str
    upstream_revision_id: int | None
    downstream: Mapping[str, tuple[int, ...]]
    reasons: Mapping[str, str]

    @property
    def affected_count(self) -> int:
        return sum(len(values) for values in self.downstream.values())


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
