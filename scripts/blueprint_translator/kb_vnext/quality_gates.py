"""Evidence-backed quality gates and fail-closed cutover decision."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .benchmark import run_query_benchmark
from .invalidation import validate_effective_resolution_dependencies
from .ontology import load_ontology
from .projections import DOMAIN_PROJECTIONS
from .quality_contract import (
    QUALITY_GATE_SCHEMA,
    validate_quality_gate_contract,
)
from .query_planner import source_revision_is_fresh
from .registrations import (
    is_valid_registration_evidence_uri,
    registration_edge_type,
)
from .schema_capabilities import (
    CORE_SCHEMA_VERSION,
    supports_typed_map_usage_evidence,
)
from .semantic_quality import semantic_quality_gates
from .snapshot import (
    CurrentSnapshot,
    SNAPSHOT_SCHEMA,
    _safe_build_id,
    resolve_current_snapshot,
    validate_snapshot_runtime_health_summary,
)


OPEN_CLASS_GAPS = (
    "NATIVE_ROOT_NOT_REACHED",
    "INHERITANCE_CYCLE",
    "MULTIPLE_PARENT_CANDIDATES",
)
EFFECTIVE_CANDIDATE_PATH_STATUSES = (
    "SELF",
    "CONFIRMED",
    "AMBIGUOUS",
)
EFFECTIVE_CANDIDATE_REJECTION_REASONS = (
    "UNUSABLE_VALUE_KIND",
    "UNUSABLE_FACT_STATUS",
    "NO_FRESH_EVIDENCE",
    "AMBIGUOUS_DECLARATION",
    "AMBIGUOUS_PATH",
    "SAME_DEPTH_CONFLICT",
    "SHADOWED_BY_NEARER_USABLE",
    "PARENT_CHAIN_OPEN",
    "ASSIGNMENT_UNVERIFIED",
    "MULTIPLE_PARENT_CANDIDATES",
    "INHERITANCE_CYCLE",
)
CONFIRMED_RELATIONSHIP_STATUSES = (
    "CONFIRMED",
    "VERIFIED",
    "RESOLVED",
)
CONFIRMED_MAP_VIEW_REQUIRED_COLUMNS = frozenset(
    {
        "edge_id",
        "edge_type",
        "status",
        "confidence",
        "source_revision_id",
        "evidence_uri",
        "map_usage_id",
        "evidence_layer",
        "source_evidence_status",
        "usage_status",
        "freshness_status",
        "claims_complete_map_usage",
        "claims_spawn_coordinates",
        "evidence_count",
    }
)


def _resolve_quality_snapshot(snapshot_root: Path) -> CurrentSnapshot:
    """Resolve a configured root or one direct immutable build directory."""

    snapshot_root = snapshot_root.resolve()
    try:
        return resolve_current_snapshot(snapshot_root)
    except FileNotFoundError:
        manifest_path = snapshot_root / "manifest.json"
        if not manifest_path.is_file():
            raise
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"immutable snapshot manifest is unreadable: {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict):
        raise ValueError(
            f"immutable snapshot manifest must be an object: {manifest_path}"
        )
    build_id = _safe_build_id(manifest.get("buildId"))
    if manifest.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("immutable snapshot manifest schema is unknown")
    return CurrentSnapshot(
        root=snapshot_root,
        snapshot_dir=snapshot_root,
        manifest_path=manifest_path,
        pointer_path=manifest_path,
        build_id=build_id,
        manifest=manifest,
        layout="immutable-v2-direct",
    )


def _immutable_configured_root(location: CurrentSnapshot) -> Path:
    """Return the non-snapshot root where mutable reports may be written."""

    if location.layout == "immutable-v2":
        return location.root
    if (
        location.layout == "immutable-v2-direct"
        and location.snapshot_dir.name == location.build_id
        and location.snapshot_dir.parent.name == "snapshots"
    ):
        return location.snapshot_dir.parent.parent.resolve()
    raise ValueError(
        "Direct immutable snapshot reporting requires a canonical "
        "<configured-root>/snapshots/<buildId> directory"
    )


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _strict_source_revision_is_fresh(
    *,
    revision_id: object,
    source_kind: object,
    source_uri: object,
    source_fingerprint: object,
    producer_version: object,
    schema_version: object,
    generated_at: object,
    freshness_status: object,
) -> bool:
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
                "freshnessStatus": freshness_status,
            }
        )
    )


def _class_closure_metrics(
    core: sqlite3.Connection,
) -> dict[str, int | float]:
    placeholders = ", ".join("?" for _ in OPEN_CLASS_GAPS)
    row = core.execute(
        f"""
        WITH selected_assignment AS (
            SELECT
                policy.entity_id,
                COALESCE(
                    MAX(
                        CASE
                            WHEN assignment.assignment_kind='GENERATED_CLASS'
                            THEN assignment.class_id
                        END
                    ),
                    MAX(
                        CASE
                            WHEN assignment.assignment_kind='ASSET_CLASS'
                            THEN assignment.class_id
                        END
                    )
                ) AS class_id
            FROM knowledge_depth_policies AS policy
            LEFT JOIN asset_class_assignments AS assignment
              ON assignment.entity_id=policy.entity_id
            WHERE policy.depth_policy IN ('DEEP', 'SEMANTIC')
            GROUP BY policy.entity_id
        ),
        open_class AS (
            SELECT DISTINCT class_id
            FROM class_gaps
            WHERE gap_kind IN ({placeholders})
        )
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN selected.class_id IS NOT NULL THEN 1 ELSE 0 END)
                AS applicable_count,
            SUM(CASE WHEN selected.class_id IS NULL THEN 1 ELSE 0 END)
                AS not_applicable_count,
            SUM(
                CASE
                    WHEN selected.class_id IS NOT NULL
                     AND open.class_id IS NULL
                    THEN 1 ELSE 0
                END
            ) AS closed_count,
            SUM(
                CASE
                    WHEN selected.class_id IS NOT NULL
                     AND open.class_id IS NOT NULL
                    THEN 1 ELSE 0
                END
            ) AS open_count
        FROM selected_assignment AS selected
        LEFT JOIN open_class AS open ON open.class_id=selected.class_id
        """,
        OPEN_CLASS_GAPS,
    ).fetchone()
    total_count = int(row[0] or 0)
    applicable_count = int(row[1] or 0)
    not_applicable_count = int(row[2] or 0)
    closed_count = int(row[3] or 0)
    open_count = int(row[4] or 0)
    return {
        "classApplicableCount": applicable_count,
        "classClosedCount": closed_count,
        "classNotApplicableCount": not_applicable_count,
        "classOpenCount": open_count,
        "deepSemanticEntityCount": total_count,
        "closureRate": _ratio(closed_count, applicable_count),
    }


def _effective_candidate_metrics(
    core: sqlite3.Connection,
) -> dict[str, int | bool]:
    row = core.execute(
        """
        WITH candidate_selection AS (
            SELECT
                entity_id, fact_type, fact_name,
                SUM(CASE WHEN selected=1 THEN 1 ELSE 0 END)
                    AS selected_count,
                MAX(
                    CASE WHEN selected=1 THEN candidate_fact_id END
                ) AS selected_fact_id
            FROM effective_fact_candidates
            GROUP BY entity_id, fact_type, fact_name
        )
        SELECT
            COUNT(*) AS effective_rows,
            SUM(
                CASE
                    WHEN UPPER(effective.resolution_status)='RESOLVED'
                    THEN 1 ELSE 0
                END
            ) AS resolved_rows,
            SUM(
                CASE
                    WHEN UPPER(effective.resolution_status)<>'RESOLVED'
                    THEN 1 ELSE 0
                END
            ) AS unresolved_rows,
            SUM(
                CASE
                    WHEN UPPER(effective.resolution_status)='RESOLVED'
                     AND (
                        effective.fact_id IS NULL
                        OR COALESCE(candidate.selected_count, 0)<>1
                        OR candidate.selected_fact_id IS NULL
                        OR candidate.selected_fact_id<>effective.fact_id
                     )
                    THEN 1
                    WHEN UPPER(effective.resolution_status)<>'RESOLVED'
                     AND (
                        effective.fact_id IS NOT NULL
                        OR COALESCE(candidate.selected_count, 0)<>0
                     )
                    THEN 1
                    ELSE 0
                END
            ) AS invalid_selection_rows
        FROM effective_facts AS effective
        LEFT JOIN candidate_selection AS candidate
          ON candidate.entity_id=effective.entity_id
         AND candidate.fact_type=effective.fact_type
         AND candidate.fact_name=effective.fact_name
        """
    ).fetchone()
    orphan_candidate_rows = int(
        core.execute(
            """
            SELECT COUNT(*)
            FROM effective_fact_candidates AS candidate
            LEFT JOIN effective_facts AS effective
              ON effective.entity_id=candidate.entity_id
             AND effective.fact_type=candidate.fact_type
             AND effective.fact_name=candidate.fact_name
            WHERE effective.entity_id IS NULL
            """
        ).fetchone()[0]
        or 0
    )
    path_placeholders = ", ".join(
        "?" for _ in EFFECTIVE_CANDIDATE_PATH_STATUSES
    )
    reason_placeholders = ", ".join(
        "?" for _ in EFFECTIVE_CANDIDATE_REJECTION_REASONS
    )
    invalid_candidate_lineage_rows = int(
        core.execute(
            f"""
            SELECT COUNT(*)
            FROM effective_fact_candidates AS candidate
            LEFT JOIN facts AS fact
              ON fact.fact_id=candidate.candidate_fact_id
            WHERE fact.fact_id IS NULL
               OR fact.fact_type<>'DECLARED_DEFAULT'
               OR fact.fact_name<>candidate.fact_name
               OR fact.subject_entity_id<>candidate.declared_on_entity_id
               OR fact.declared_on_entity_id IS NULL
               OR fact.declared_on_entity_id<>candidate.declared_on_entity_id
               OR fact.scope_kind<>'DECLARED'
               OR fact.current<>1
               OR candidate.inheritance_depth<0
               OR candidate.path_status NOT IN ({path_placeholders})
               OR (
                    candidate.selected=1
                    AND candidate.rejection_reason<>''
               )
               OR (
                    candidate.selected=0
                    AND candidate.rejection_reason NOT IN (
                        {reason_placeholders}
                    )
               )
            """,
            (
                *EFFECTIVE_CANDIDATE_PATH_STATUSES,
                *EFFECTIVE_CANDIDATE_REJECTION_REASONS,
            ),
        ).fetchone()[0]
        or 0
    )
    effective_rows = int(row[0] or 0)
    resolved_rows = int(row[1] or 0)
    unresolved_rows = int(row[2] or 0)
    invalid_selection_rows = int(row[3] or 0)
    return {
        "effectiveRows": effective_rows,
        "resolvedRows": resolved_rows,
        "unresolvedRows": unresolved_rows,
        "invalidSelectionRows": invalid_selection_rows,
        "orphanCandidateRows": orphan_candidate_rows,
        "invalidCandidateLineageRows": invalid_candidate_lineage_rows,
        "consistent": (
            invalid_selection_rows == 0
            and orphan_candidate_rows == 0
            and invalid_candidate_lineage_rows == 0
        ),
    }


def _effective_resolution_metrics(
    core: sqlite3.Connection,
) -> dict[str, object]:
    try:
        dependencies = validate_effective_resolution_dependencies(core)
    except (sqlite3.DatabaseError, ValueError) as error:
        return {
            "consistent": False,
            "dependencyRows": 0,
            "error": str(error),
        }
    return {
        "consistent": True,
        "dependencyRows": len(dependencies),
        "error": "",
    }


def _gate(
    gate_id: str,
    category: str,
    *,
    target: object,
    actual: object,
    passed: bool,
    detail: str,
    critical: bool = True,
) -> dict[str, object]:
    return {
        "id": gate_id,
        "category": category,
        "target": target,
        "actual": actual,
        "passed": bool(passed),
        "critical": critical,
        "detail": detail,
    }


def _registration_confidence_metrics(
    core: sqlite3.Connection,
) -> dict[str, int]:
    """Count open registration claims carrying complete confidence."""

    def incomplete_high(alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        return f"""
            upper({prefix}status)
              NOT IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
            AND upper({prefix}confidence) IN ('HIGH', 'CONFIRMED')
        """

    metrics = {
        "typedRegistrations": int(
            core.execute(
                f"""
                SELECT COUNT(*) FROM typed_registrations
                WHERE {incomplete_high()}
                """
            ).fetchone()[0]
        ),
        "registrationEdges": int(
            core.execute(
                f"""
                SELECT COUNT(DISTINCT edge.edge_id)
                FROM edges AS edge
                JOIN entities AS owner
                  ON owner.entity_id=edge.source_entity_id
                JOIN entities AS target
                  ON target.entity_id=edge.target_entity_id
                JOIN typed_registrations AS registration
                  ON registration.owner_uri=owner.canonical_uri
                 AND registration.target_uri=target.canonical_uri
                 AND registration.source_property=edge.source_property
                 AND registration.evidence_uri=edge.evidence_uri
                WHERE {incomplete_high("edge")}
                """
            ).fetchone()[0]
        ),
        "typedMemberships": int(
            core.execute(
                f"""
                SELECT COUNT(*) FROM domain_memberships
                WHERE membership_kind='TYPED_REGISTRATION'
                  AND {incomplete_high()}
                """
            ).fetchone()[0]
        ),
    }
    metrics["total"] = sum(metrics.values())
    return metrics


def _registration_confidence_gate(
    metrics: Mapping[str, object],
) -> dict[str, object]:
    contradictions = int(metrics.get("total") or 0)
    return _gate(
        "registrations.noncomplete_high_confidence",
        "registrations",
        target=0,
        actual=dict(metrics),
        passed=contradictions == 0,
        detail=(
            "Candidate and legacy registration claims, their typed Core "
            "edges, and typed memberships must not carry complete confidence."
        ),
    )


def _registration_lineage_metrics(
    core: sqlite3.Connection,
) -> dict[str, int]:
    required_registration_columns = {
        "owner_uri",
        "target_uri",
        "source_property",
        "evidence_uri",
        "source_revision_id",
    }
    required_revision_columns = {
        "revision_id",
        "source_kind",
        "source_uri",
        "source_fingerprint",
        "producer_version",
        "schema_version",
        "generated_at",
        "freshness_status",
    }
    table_columns = {
        table: {
            str(row[1])
            for row in core.execute(f"PRAGMA table_info({table})")
        }
        for table in ("typed_registrations", "source_revisions")
    }
    if (
        not required_registration_columns.issubset(
            table_columns["typed_registrations"]
        )
        or not required_revision_columns.issubset(
            table_columns["source_revisions"]
        )
    ):
        return {"total": 0, "complete": 0, "incomplete": 0}
    rows = list(
        core.execute(
            """
            SELECT
                registration.owner_uri,
                registration.target_uri,
                registration.source_property,
                registration.evidence_uri,
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
        )
    )
    complete = sum(
        bool(str(row[0] or "").strip())
        and bool(str(row[1] or "").strip())
        and bool(str(row[2] or "").strip())
        and is_valid_registration_evidence_uri(row[3])
        and _strict_source_revision_is_fresh(
            revision_id=row[4],
            source_kind=row[5],
            source_uri=row[6],
            source_fingerprint=row[7],
            producer_version=row[8],
            schema_version=row[9],
            generated_at=row[10],
            freshness_status=row[11],
        )
        for row in rows
    )
    return {
        "total": len(rows),
        "complete": int(complete),
        "incomplete": len(rows) - int(complete),
    }


def _registration_gold_metrics(
    project_root: Path,
    core: sqlite3.Connection,
) -> dict[str, object]:
    """Evaluate explicit reviewed Owner→Target rows against persisted edges."""

    gold_path = (
        project_root
        / "tests"
        / "fixtures"
        / "kb_registration_gold_set.json"
    )
    unavailable = {
        "available": False,
        "relationships": 0,
        "positiveCases": 0,
        "negativeCases": 0,
        "precision": 0.0,
        "recall": 0.0,
        "classificationPrecision": 0.0,
        "classificationRecall": 0.0,
        "ownerResolutionRate": 0.0,
        "targetResolutionRate": 0.0,
        "edgeMaterializationRate": 0.0,
        "evidenceCorrectnessRate": 0.0,
        "gapCode": "INDEPENDENT_OWNER_TARGET_REVIEW_REQUIRED",
    }
    try:
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return unavailable
    if (
        not isinstance(gold, Mapping)
        or gold.get("schema") != "ark-kb-registration-gold-set/v2"
    ):
        return unavailable
    raw_cases = gold.get("relationshipCases")
    if not isinstance(raw_cases, list) or not raw_cases:
        return unavailable

    required_fields = {
        "ownerUri",
        "targetUri",
        "registrationType",
        "sourceProperty",
        "expectedEdgeType",
        "expectedStatus",
        "evidenceUri",
        "reviewStatus",
        "reviews",
    }
    cases: list[Mapping[str, object]] = []
    identities: set[tuple[str, str, str, str, str]] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping) or not required_fields <= set(
            raw_case
        ):
            continue
        reviews = raw_case.get("reviews")
        if (
            raw_case.get("reviewStatus")
            not in {"HUMAN_REVIEWED", "EMPIRICAL"}
            or not isinstance(reviews, list)
            or len(reviews) < 2
        ):
            continue
        confirmed_reviews = [
            review
            for review in reviews
            if isinstance(review, Mapping)
            and str(review.get("verdict") or "").upper() == "CONFIRMED"
        ]
        reviewer_ids = {
            str(review.get("reviewerId") or "").strip()
            for review in confirmed_reviews
        }
        review_rounds = {
            str(review.get("round") or "").strip()
            for review in confirmed_reviews
        }
        if (
            "" in reviewer_ids
            or len(reviewer_ids) < 2
            or len(review_rounds) < 2
        ):
            continue
        values = {
            key: str(raw_case.get(key) or "").strip()
            for key in required_fields - {"reviews"}
        }
        if any(not value for value in values.values()):
            continue
        identity = (
            values["ownerUri"],
            values["targetUri"],
            values["registrationType"],
            values["sourceProperty"],
            values["expectedEdgeType"],
        )
        if identity in identities:
            continue
        identities.add(identity)
        cases.append(raw_case)
    if (
        not cases
        or len(cases) != len(raw_cases)
        or gold.get("relationshipGoldStatus")
        != "INDEPENDENTLY_REVIEWED"
    ):
        return {
            **unavailable,
            "reviewedCases": len(cases),
            "declaredCases": len(raw_cases),
        }

    required_schema = {
        "entities": {"entity_id", "canonical_uri"},
        "typed_registrations": {
            "owner_uri",
            "target_uri",
            "registration_type",
            "source_property",
            "evidence_uri",
            "status",
            "confidence",
            "source_revision_id",
        },
        "edges": {
            "source_entity_id",
            "target_entity_id",
            "edge_type",
            "status",
            "confidence",
            "evidence_uri",
            "source_property",
            "source_revision_id",
        },
        "source_revisions": {
            "revision_id",
            "source_kind",
            "source_uri",
            "source_fingerprint",
            "producer_version",
            "schema_version",
            "generated_at",
            "freshness_status",
        },
    }
    for table_name, expected_columns in required_schema.items():
        columns = {
            str(row[1])
            for row in core.execute(f"PRAGMA table_info({table_name})")
        }
        if not expected_columns.issubset(columns):
            return {
                **unavailable,
                "reviewedCases": len(cases),
                "declaredCases": len(raw_cases),
                "gapCode": "REGISTRATION_GOLD_EVALUATION_SCHEMA_REQUIRED",
            }

    complete_statuses = set(CONFIRMED_RELATIONSHIP_STATUSES)
    complete_confidence = {"HIGH", "CONFIRMED"}
    owner_resolved = 0
    target_resolved = 0
    classified_true_positive = 0
    classified_false_positive = 0
    classified_false_negative = 0
    materialized = 0
    evidence_correct = 0
    answer_true_positive = 0
    answer_false_positive = 0
    answer_false_negative = 0
    positive_cases = 0

    for case in cases:
        owner_uri = str(case["ownerUri"])
        target_uri = str(case["targetUri"])
        registration_type = str(case["registrationType"])
        source_property = str(case["sourceProperty"])
        expected_edge_type = str(case["expectedEdgeType"])
        expected_status = str(case["expectedStatus"]).upper()
        expected_evidence = str(case["evidenceUri"])
        owner = core.execute(
            "SELECT entity_id FROM entities WHERE canonical_uri=?",
            (owner_uri,),
        ).fetchone()
        target = core.execute(
            "SELECT entity_id FROM entities WHERE canonical_uri=?",
            (target_uri,),
        ).fetchone()
        owner_resolved += int(owner is not None)
        target_resolved += int(target is not None)
        rows: list[sqlite3.Row | tuple[object, ...]] = []
        if owner is not None and target is not None:
            rows = list(
                core.execute(
                    """
                    SELECT
                        edge.edge_type,
                        edge.status,
                        edge.confidence,
                        edge.evidence_uri,
                        edge_revision.revision_id,
                        edge_revision.source_kind,
                        edge_revision.source_uri,
                        edge_revision.source_fingerprint,
                        edge_revision.producer_version,
                        edge_revision.schema_version,
                        edge_revision.generated_at,
                        edge_revision.freshness_status,
                        registration.status,
                        registration.confidence,
                        registration.evidence_uri,
                        registration_revision.revision_id,
                        registration_revision.source_kind,
                        registration_revision.source_uri,
                        registration_revision.source_fingerprint,
                        registration_revision.producer_version,
                        registration_revision.schema_version,
                        registration_revision.generated_at,
                        registration_revision.freshness_status
                    FROM typed_registrations AS registration
                    JOIN source_revisions AS registration_revision
                      ON registration_revision.revision_id=
                         registration.source_revision_id
                    JOIN edges AS edge
                      ON edge.source_entity_id=?
                     AND edge.target_entity_id=?
                     AND edge.source_property=
                         registration.source_property
                     AND edge.evidence_uri=registration.evidence_uri
                    JOIN source_revisions AS edge_revision
                      ON edge_revision.revision_id=edge.source_revision_id
                    WHERE registration.owner_uri=?
                      AND registration.target_uri=?
                      AND registration.registration_type=?
                      AND registration.source_property=?
                    ORDER BY edge.edge_type, edge.evidence_uri
                    """,
                    (
                        int(owner[0]),
                        int(target[0]),
                        owner_uri,
                        target_uri,
                        registration_type,
                        source_property,
                    ),
                )
            )
        predicted_edge_type = registration_edge_type(
            registration_type=registration_type,
            source_property=source_property,
        )
        if predicted_edge_type == expected_edge_type:
            classified_true_positive += 1
        else:
            classified_false_negative += 1
            classified_false_positive += 1

        fresh_rows = [
            row
            for row in rows
            if _strict_source_revision_is_fresh(
                revision_id=row[4],
                source_kind=row[5],
                source_uri=row[6],
                source_fingerprint=row[7],
                producer_version=row[8],
                schema_version=row[9],
                generated_at=row[10],
                freshness_status=row[11],
            )
            and _strict_source_revision_is_fresh(
                revision_id=row[15],
                source_kind=row[16],
                source_uri=row[17],
                source_fingerprint=row[18],
                producer_version=row[19],
                schema_version=row[20],
                generated_at=row[21],
                freshness_status=row[22],
            )
            and is_valid_registration_evidence_uri(row[3])
            and is_valid_registration_evidence_uri(row[14])
        ]
        status_rows = [
            row
            for row in fresh_rows
            if str(row[0]) == expected_edge_type
            and str(row[1]).upper() == expected_status
            and str(row[12]).upper() == expected_status
        ]
        materialized += int(bool(status_rows))
        exact_rows = [
            row
            for row in status_rows
            if str(row[3]) == expected_evidence
            and str(row[14]) == expected_evidence
        ]
        evidence_correct += int(bool(exact_rows))

        expected_complete = expected_status in complete_statuses
        positive_cases += int(expected_complete)
        complete_rows = [
            row
            for row in fresh_rows
            if str(row[1]).upper() in complete_statuses
            and str(row[2]).upper() in complete_confidence
            and str(row[12]).upper() in complete_statuses
            and str(row[13]).upper() in complete_confidence
            and is_valid_registration_evidence_uri(row[3])
            and is_valid_registration_evidence_uri(row[14])
        ]
        correct_complete = any(
            str(row[0]) == expected_edge_type
            and str(row[3]) == expected_evidence
            and str(row[14]) == expected_evidence
            for row in complete_rows
        )
        if expected_complete and correct_complete:
            answer_true_positive += 1
        elif expected_complete:
            answer_false_negative += 1
        if not expected_complete and complete_rows:
            answer_false_positive += len(complete_rows)
        elif expected_complete:
            answer_false_positive += sum(
                1
                for row in complete_rows
                if str(row[0]) != expected_edge_type
                or str(row[3]) != expected_evidence
                or str(row[14]) != expected_evidence
            )

    relationships = len(cases)
    return {
        "available": True,
        "relationships": relationships,
        "positiveCases": positive_cases,
        "negativeCases": relationships - positive_cases,
        "precision": _ratio(
            answer_true_positive,
            answer_true_positive + answer_false_positive,
        ),
        "recall": _ratio(
            answer_true_positive,
            answer_true_positive + answer_false_negative,
        ),
        "classificationPrecision": _ratio(
            classified_true_positive,
            classified_true_positive + classified_false_positive,
        ),
        "classificationRecall": _ratio(
            classified_true_positive,
            classified_true_positive + classified_false_negative,
        ),
        "ownerResolutionRate": _ratio(owner_resolved, relationships),
        "targetResolutionRate": _ratio(target_resolved, relationships),
        "edgeMaterializationRate": _ratio(materialized, relationships),
        "evidenceCorrectnessRate": _ratio(
            evidence_correct,
            relationships,
        ),
        "gapCode": "",
    }


def _role_gold_metrics(
    project_root: Path,
    core: sqlite3.Connection,
) -> dict[str, object]:
    unavailable = {
        "available": False,
        "assets": 0,
        "precision": None,
        "recall": None,
        "resolutionRate": 0.0,
        "perRole": {},
        "detail": (
            "No independently reviewed 300-asset role gold set exists; "
            "classifier unit cases are not counted as production gold."
        ),
    }
    path = project_root / "tests" / "fixtures" / "kb_role_gold_set.json"
    if not path.is_file():
        return unavailable
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return unavailable
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema") != "ark-kb-role-gold-set/v1"
        or payload.get("roleGoldStatus") != "INDEPENDENTLY_REVIEWED"
        or not isinstance(payload.get("cases"), list)
        or not payload["cases"]
    ):
        return unavailable

    reviewed_roles = {
        "global_system_hub",
        "reusable_base_class",
        "reusable_component",
        "domain_rule_asset",
        "registration_owner",
        "entity_definition",
        "leaf_variant",
        "map_placement_asset",
        "visual_support_asset",
        "configuration_asset",
        "unknown_role",
    }
    cases: list[tuple[str, frozenset[str]]] = []
    entity_uris: set[str] = set()
    for raw_case in payload["cases"]:
        if (
            not isinstance(raw_case, Mapping)
            or "correct" in raw_case
            or raw_case.get("reviewStatus")
            not in {"HUMAN_REVIEWED", "EMPIRICAL"}
        ):
            return unavailable
        entity_uri = str(raw_case.get("entityUri") or "").strip()
        expected_roles_raw = raw_case.get("expectedRoles")
        reviews = raw_case.get("reviews")
        if (
            not entity_uri
            or entity_uri in entity_uris
            or not isinstance(expected_roles_raw, list)
            or not expected_roles_raw
            or not isinstance(reviews, list)
            or len(reviews) < 2
        ):
            return unavailable
        expected_roles = frozenset(
            str(role or "").strip() for role in expected_roles_raw
        )
        if (
            "" in expected_roles
            or not expected_roles <= reviewed_roles
            or len(expected_roles) != len(expected_roles_raw)
        ):
            return unavailable
        valid_reviews: list[tuple[str, str, frozenset[str]]] = []
        for review in reviews:
            if not isinstance(review, Mapping):
                continue
            reviewer_id = str(review.get("reviewerId") or "").strip()
            review_round = str(review.get("round") or "").strip()
            roles = review.get("roles")
            if (
                not reviewer_id
                or not review_round
                or not isinstance(roles, list)
                or not roles
            ):
                continue
            role_set = frozenset(str(role or "").strip() for role in roles)
            if "" in role_set or not role_set <= reviewed_roles:
                continue
            valid_reviews.append((reviewer_id, review_round, role_set))
        reviewer_ids = {review[0] for review in valid_reviews}
        review_rounds = {review[1] for review in valid_reviews}
        if len(reviewer_ids) < 2 or len(review_rounds) < 2:
            return unavailable
        proposed_roles = {review[2] for review in valid_reviews}
        if len(proposed_roles) == 1:
            if expected_roles not in proposed_roles:
                return unavailable
        else:
            adjudication = raw_case.get("adjudication")
            if not isinstance(adjudication, Mapping):
                return unavailable
            adjudicator = str(
                adjudication.get("reviewerId") or ""
            ).strip()
            adjudicated_roles = adjudication.get("roles")
            if (
                adjudication.get("status") != "RESOLVED"
                or not adjudicator
                or adjudicator in reviewer_ids
                or not isinstance(adjudicated_roles, list)
                or frozenset(
                    str(role or "").strip()
                    for role in adjudicated_roles
                )
                != expected_roles
            ):
                return unavailable
        entity_uris.add(entity_uri)
        cases.append((entity_uri, expected_roles))

    for table_name, expected_columns in {
        "entities": {"entity_id", "canonical_uri"},
        "knowledge_roles": {
            "entity_id",
            "role",
            "confidence",
            "status",
            "source_revision_id",
        },
        "source_revisions": {
            "revision_id",
            "source_kind",
            "source_uri",
            "source_fingerprint",
            "producer_version",
            "schema_version",
            "generated_at",
            "freshness_status",
        },
    }.items():
        columns = {
            str(row[1])
            for row in core.execute(f"PRAGMA table_info({table_name})")
        }
        if not expected_columns.issubset(columns):
            return unavailable

    true_positive = 0
    false_positive = 0
    false_negative = 0
    resolved = 0
    per_role_counts: dict[str, dict[str, int]] = {
        role: {"tp": 0, "fp": 0, "fn": 0}
        for role in sorted(reviewed_roles)
    }
    for entity_uri, expected_roles in cases:
        entity = core.execute(
            "SELECT entity_id FROM entities WHERE canonical_uri=?",
            (entity_uri,),
        ).fetchone()
        actual_roles: set[str] = set()
        if entity is not None:
            resolved += 1
            actual_roles = {
                str(row[0])
                for row in core.execute(
                    """
                    SELECT
                        role.role, role.status, role.confidence,
                        revision.revision_id, revision.source_kind,
                        revision.source_uri, revision.source_fingerprint,
                        revision.producer_version, revision.schema_version,
                        revision.generated_at,
                        revision.freshness_status
                    FROM knowledge_roles AS role
                    JOIN source_revisions AS revision
                      ON revision.revision_id=role.source_revision_id
                    WHERE role.entity_id=?
                    """,
                    (int(entity[0]),),
                )
                if str(row[0]) in reviewed_roles
                and str(row[1] or "").upper()
                in CONFIRMED_RELATIONSHIP_STATUSES
                and str(row[2] or "").upper() in {"HIGH", "CONFIRMED"}
                and _strict_source_revision_is_fresh(
                    revision_id=row[3],
                    source_kind=row[4],
                    source_uri=row[5],
                    source_fingerprint=row[6],
                    producer_version=row[7],
                    schema_version=row[8],
                    generated_at=row[9],
                    freshness_status=row[10],
                )
            }
        true_roles = actual_roles & expected_roles
        extra_roles = actual_roles - expected_roles
        missing_roles = expected_roles - actual_roles
        true_positive += len(true_roles)
        false_positive += len(extra_roles)
        false_negative += len(missing_roles)
        for role in true_roles:
            per_role_counts[role]["tp"] += 1
        for role in extra_roles:
            per_role_counts[role]["fp"] += 1
        for role in missing_roles:
            per_role_counts[role]["fn"] += 1

    per_role = {
        role: {
            **counts,
            "precision": _ratio(
                counts["tp"],
                counts["tp"] + counts["fp"],
            ),
            "recall": _ratio(
                counts["tp"],
                counts["tp"] + counts["fn"],
            ),
        }
        for role, counts in per_role_counts.items()
        if sum(counts.values()) > 0
    }
    return {
        "available": True,
        "assets": len(cases),
        "precision": _ratio(
            true_positive,
            true_positive + false_positive,
        ),
        "recall": _ratio(
            true_positive,
            true_positive + false_negative,
        ),
        "resolutionRate": _ratio(resolved, len(cases)),
        "perRole": per_role,
        "detail": (
            "Predictions were recomputed from Core after two independent "
            "review rounds; disagreements require adjudication."
        ),
    }


def _native_gold_metrics(
    core: sqlite3.Connection,
) -> dict[str, int]:
    """Count only gold targets bound to the exact fresh native identity."""

    targets, confirmed = core.execute(
        """
        SELECT COUNT(*),
               SUM(
                 CASE
                   WHEN target.status='CONFIRMED'
                    AND target.gap_code=''
                    AND function.status='CONFIRMED'
                    AND function.confidence='HIGH'
                    AND revision.freshness_status='FRESH'
                    AND target.qualified_symbol=function.qualified_symbol
                    AND target.expected_rva=function.rva
                    AND EXISTS (
                      SELECT 1
                      FROM json_each(
                        CASE
                          WHEN json_valid(function.recipe_ids_json)
                          THEN function.recipe_ids_json
                          ELSE '[]'
                        END
                      ) AS recipe
                      WHERE recipe.type='text'
                        AND recipe.value=target.recipe_id
                    )
                   THEN 1
                   ELSE 0
                 END
               )
        FROM native_gold_targets AS target
        LEFT JOIN native_functions AS function
          ON function.native_function_id=target.native_function_id
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=function.source_revision_id
        """
    ).fetchone()
    return {
        "targets": int(targets or 0),
        "confirmed": int(confirmed or 0),
    }


def _integrity_metrics(snapshot_root: Path) -> dict[str, object]:
    snapshot_root = snapshot_root.resolve()
    manifest: Mapping[str, object] | None = None
    if (
        (snapshot_root / "current.json").is_file()
        or (snapshot_root / "manifest.json").is_file()
        or (
            snapshot_root / "manifests" / "current.json"
        ).is_file()
    ):
        location = _resolve_quality_snapshot(snapshot_root)
        snapshot_root = location.snapshot_dir
        manifest = location.manifest
    result: dict[str, object] = {}
    paths = [
        (name, snapshot_root / name, "")
        for name in (
            "catalog.sqlite",
            "core.sqlite",
            "search.sqlite",
            "cache.sqlite",
        )
    ]
    paths.extend(
        (
            f"domain_exports/{projection_name}.sqlite",
            snapshot_root / "domain_exports" / f"{projection_name}.sqlite",
            projection_name,
        )
        for projection_name in DOMAIN_PROJECTIONS
    )
    for name, path, projection_name in paths:
        if not path.is_file():
            result[name] = {
                "exists": False,
                "integrity": "missing",
                "foreignKeyViolations": -1,
                "bytes": 0,
                "verified": False,
                "error": "MISSING_ARTIFACT",
            }
            continue
        try:
            connection = _read_only(path)
            try:
                integrity = str(
                    connection.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()[0]
                )
                foreign_key_violations = len(
                    list(connection.execute("PRAGMA foreign_key_check"))
                )
                metadata = (
                    {
                        str(key): str(value)
                        for key, value in connection.execute(
                            "SELECT key, value FROM metadata"
                        )
                    }
                    if projection_name
                    else {}
                )
                projection_verified = (
                    not projection_name
                    or (
                        metadata.get("schema_version")
                        == "ark-kb-domain-projection/v2"
                        and metadata.get("projection_version") == "v2"
                        and metadata.get("projection_name") == projection_name
                    )
                )
                result[name] = {
                    "exists": True,
                    "integrity": integrity,
                    "foreignKeyViolations": foreign_key_violations,
                    "bytes": path.stat().st_size,
                    "verified": (
                        integrity == "ok"
                        and foreign_key_violations == 0
                        and projection_verified
                    ),
                    "error": "",
                }
            finally:
                connection.close()
        except (OSError, sqlite3.DatabaseError) as error:
            result[name] = {
                "exists": True,
                "integrity": "error",
                "foreignKeyViolations": -1,
                "bytes": path.stat().st_size,
                "verified": False,
                "error": f"{type(error).__name__}:{error}",
            }
    if manifest is not None:
        runtime_health_exists = isinstance(
            manifest.get("runtimeHealth"),
            Mapping,
        )
        try:
            with closing(_read_only(snapshot_root / "core.sqlite")) as core:
                core_metadata = {
                    str(key): str(value)
                    for key, value in core.execute(
                        "SELECT key, value FROM metadata"
                    )
                }
            active_stale_sources = (
                validate_snapshot_runtime_health_summary(
                    manifest=manifest,
                    core_metadata=core_metadata,
                )
            )
            runtime_health_valid = True
            runtime_health_error = (
                ""
                if active_stale_sources == 0
                else "ACTIVE_STALE_SOURCES"
            )
        except (OSError, sqlite3.DatabaseError, ValueError):
            active_stale_sources = -1
            runtime_health_valid = False
            runtime_health_error = "INVALID_RUNTIME_HEALTH_SUMMARY"
        result["runtimeHealth"] = {
            "exists": runtime_health_exists,
            "integrity": "ok" if runtime_health_valid else "error",
            "foreignKeyViolations": 0,
            "bytes": 0,
            "verified": (
                runtime_health_valid and active_stale_sources == 0
            ),
            "activeStaleSources": active_stale_sources,
            "error": runtime_health_error,
        }
    return result


def _storage_integrity_gate(
    integrity: Mapping[str, object],
) -> dict[str, object]:
    passed = all(
        isinstance(item, Mapping)
        and bool(item.get("exists"))
        and item.get("integrity") == "ok"
        and int(item.get("foreignKeyViolations") or 0) == 0
        and bool(item.get("verified"))
        for item in integrity.values()
    )
    return _gate(
        "storage.integrity",
        "storage",
        target=(
            "all databases ok; zero FK violations; "
            "zero active stale sources"
        ),
        actual=integrity,
        passed=passed,
        detail=(
            "Published read-only snapshot stores and its sealed runtime "
            "health summary."
        ),
    )


def _privacy_scan(value: object) -> list[str]:
    patterns = (
        (
            "windows_absolute_path",
            re.compile(r"(?i)(?<![A-Z0-9])[A-Z]:[\\/]"),
        ),
        (
            "windows_unc_path",
            re.compile(r"(?i)(?:^|[\s\"'])\\\\[^\\/\s]+[\\/]"),
        ),
        (
            "posix_home_path",
            re.compile(r"(?i)(?:^|[\s\"'=])/(?:home|users)/"),
        ),
        (
            "windows_program_files_path",
            re.compile(r"(?i)\bprogram files(?: \(x86\))?[\\/]"),
        ),
    )

    def iter_text(candidate: object):
        if isinstance(candidate, str):
            yield candidate
        elif isinstance(candidate, Mapping):
            for key, item in candidate.items():
                yield from iter_text(key)
                yield from iter_text(item)
        elif isinstance(candidate, (list, tuple, set, frozenset)):
            for item in candidate:
                yield from iter_text(item)

    hits: set[str] = set()
    for text in iter_text(value):
        for label, pattern in patterns:
            if pattern.search(text):
                hits.add(label)
    return sorted(hits)


def _query_benchmark_gates(
    benchmark: Mapping[str, object],
) -> list[dict[str, object]]:
    """Evaluate only fixed-gold v2 semantics; deprecated v1 aliases are ignored."""

    raw_gold = benchmark.get("goldSet")
    gold = raw_gold if isinstance(raw_gold, Mapping) else {}

    def integer(
        source: Mapping[str, object],
        key: str,
    ) -> int | None:
        try:
            value = source.get(key)
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def rate(key: str) -> float | None:
        try:
            value = benchmark.get(key)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    fixed_gold = integer(gold, "fixedGoldCases")
    human_gold = integer(gold, "humanGoldCases")
    protocol = rate("protocolComplianceRate")
    semantic_exact = rate("semanticExactMatchRate")
    usable_value = rate("usableValueAnswerRate")
    evidence_complete = rate("evidenceBackedCompleteRate")
    expected_gap = rate("expectedGapMatchedRate")
    wrong_answer = rate("wrongAnswerRate")
    ambiguous_answer = rate("unexpectedAmbiguousAnswerRate")
    stale_leak = rate("staleLeakRate")
    candidate_complete = rate("candidateEdgeCompleteRate")
    raw_storage_paths = benchmark.get("storagePathCoverage")
    storage_paths = (
        raw_storage_paths
        if isinstance(raw_storage_paths, Mapping)
        else {}
    )
    required_storage_paths = ("core", "search", "cache", "complete")
    storage_paths_complete = all(
        storage_paths.get(path) is True for path in required_storage_paths
    )
    fixed_corpus = (
        benchmark.get("schema") == "ark-kb-query-benchmark/v2"
        and gold.get("selectionMode") == "MANUAL_FIXED"
        and gold.get("generatedFromCore") is False
    )
    gates = [
        _gate(
            "queries.fixed_gold_cases",
            "queries",
            target=">=120 manually fixed cases",
            actual={
                "count": fixed_gold,
                "selectionMode": gold.get("selectionMode"),
                "generatedFromCore": gold.get("generatedFromCore"),
            },
            passed=bool(
                fixed_corpus
                and fixed_gold is not None
                and fixed_gold >= 120
            ),
            detail="Gold cases are checked in and never selected from Core.",
        ),
        _gate(
            "queries.human_gold_cases",
            "queries",
            target=">=120 HUMAN_REVIEWED or EMPIRICAL cases",
            actual=human_gold,
            passed=human_gold is not None and human_gold >= 120,
            detail="Fixture-exact protocol cases do not count as human gold.",
        ),
        _gate(
            "queries.corpus_ready_for_cutover",
            "queries",
            target=True,
            actual=gold.get("corpusReadyForCutover"),
            passed=gold.get("corpusReadyForCutover") is True,
            detail="The fixed corpus itself declares no outstanding review gap.",
        ),
        _gate(
            "queries.protocol_compliance",
            "queries",
            target=1.0,
            actual=protocol,
            passed=protocol is not None and protocol >= 1.0,
            detail="Every request follows the answer-mode and explicit-gap protocol.",
        ),
        _gate(
            "queries.semantic_exact_match",
            "queries",
            target=">=0.95",
            actual=semantic_exact,
            passed=semantic_exact is not None and semantic_exact >= 0.95,
            detail="Exact semantic answers are measured only on eligible gold.",
        ),
        _gate(
            "queries.usable_value_answer",
            "queries",
            target=">=0.95",
            actual=usable_value,
            passed=usable_value is not None and usable_value >= 0.95,
            detail="Typed value materialization is independently measured.",
        ),
        _gate(
            "queries.evidence_backed_complete",
            "queries",
            target=">=0.95",
            actual=evidence_complete,
            passed=(
                evidence_complete is not None
                and evidence_complete >= 0.95
            ),
            detail="Complete semantic answers carry usable fresh Evidence.",
        ),
        _gate(
            "queries.expected_gap_match",
            "queries",
            target=">=0.95",
            actual=expected_gap,
            passed=expected_gap is not None and expected_gap >= 0.95,
            detail="Expected bounded gaps use their reviewed stable codes.",
        ),
        _gate(
            "queries.no_wrong_answers",
            "queries",
            target=0.0,
            actual=wrong_answer,
            passed=wrong_answer is not None and wrong_answer == 0.0,
            detail="Wrong answers are never exchanged for a higher coverage rate.",
        ),
        _gate(
            "queries.no_unexpected_ambiguous_answers",
            "queries",
            target=0.0,
            actual=ambiguous_answer,
            passed=(
                ambiguous_answer is not None
                and ambiguous_answer == 0.0
            ),
            detail=(
                "Only unexpected ambiguity is an error; reviewed ambiguous "
                "negative cases remain valid explicit gaps."
            ),
        ),
        _gate(
            "queries.no_stale_leaks",
            "queries",
            target=0.0,
            actual=stale_leak,
            passed=stale_leak is not None and stale_leak == 0.0,
            detail="STALE Evidence cannot satisfy a complete answer.",
        ),
        _gate(
            "queries.no_candidate_edge_completion",
            "queries",
            target=0.0,
            actual=candidate_complete,
            passed=(
                candidate_complete is not None
                and candidate_complete == 0.0
            ),
            detail="Candidate relationship rows cannot close a requirement.",
        ),
        _gate(
            "queries.identity_not_semantic",
            "queries",
            target=True,
            actual=benchmark.get("identityOnlyNotCountedAsSemantic"),
            passed=(
                benchmark.get("identityOnlyNotCountedAsSemantic") is True
            ),
            detail="Identity-only completion is excluded from semantic coverage.",
        ),
        _gate(
            "queries.storage_paths_covered",
            "queries",
            target={
                "core": True,
                "search": True,
                "cache": True,
                "complete": True,
            },
            actual={
                path: storage_paths.get(path)
                for path in required_storage_paths
            },
            passed=storage_paths_complete,
            detail=(
                "The fixed benchmark must exercise Core, Search, and Cache "
                "read paths before cutover."
            ),
        ),
    ]
    raw_performance = benchmark.get("performanceGates")
    performance = (
        raw_performance
        if isinstance(raw_performance, Mapping)
        else {}
    )
    raw_checks = performance.get("checks")
    checks = raw_checks if isinstance(raw_checks, Mapping) else {}
    runtime_checks = {
        "ftsPlanUsed": (
            "queries.search_fts_plan_used",
            "FTS EXPLAIN plan uses the virtual-table index.",
        ),
        "cacheValidHit": (
            "queries.cache_valid_hit",
            "A matching fingerprint is read as a validated cache hit.",
        ),
        "cacheExpiredRejected": (
            "queries.cache_expired_rejected",
            "An expired query snapshot is rejected before reuse.",
        ),
        "cacheSourceRevisionRejected": (
            "queries.cache_source_revision_rejected",
            "A changed source-revision set invalidates the cached answer.",
        ),
        "cacheInvalidationTokenRejected": (
            "queries.cache_invalidation_token_rejected",
            "A changed invalidation token invalidates the cached answer.",
        ),
        "cacheBuildRejected": (
            "queries.cache_build_rejected",
            "Cache metadata from a different build cannot be reused.",
        ),
        "degreeCohortsCovered": (
            "queries.degree_cohorts_covered",
            "Every available member, up to 20, is sampled in all cohorts.",
        ),
        "fuzzyP95": (
            "queries.search_fuzzy_p95_ms",
            "Bounded fuzzy search meets the fixed p95 threshold.",
        ),
        "cacheHitP95": (
            "queries.cache_hit_p95_ms",
            "Validated cache reads meet the fixed p95 threshold.",
        ),
        "oneHopP95": (
            "queries.one_hop_p95_ms",
            "Degree-stratified one-hop reads meet the fixed threshold.",
        ),
        "twoHopP95": (
            "queries.degree_stratified_two_hop_p95_ms",
            "Degree-stratified two-hop reads meet the fixed threshold.",
        ),
    }
    for check_name, (gate_id, detail) in runtime_checks.items():
        raw_check = checks.get(check_name)
        check = raw_check if isinstance(raw_check, Mapping) else {}
        gates.append(
            _gate(
                gate_id,
                "performance",
                target=check.get("target", True),
                actual=check.get("actual"),
                passed=check.get("passed") is True,
                detail=detail,
            )
        )
    return gates


def _typed_map_usage_metrics(
    core: sqlite3.Connection,
) -> dict[str, object]:
    """Validate the v4 typed map view against its base Evidence and revisions."""

    metrics: dict[str, object] = {
        "coreSchemaVersion": "",
        "capability": False,
        "confirmedViewFieldsPresent": False,
        "typedEdgeCount": 0,
        "confirmedCount": 0,
        "candidateCount": 0,
        "staleCount": 0,
        "invalidConfirmedRows": 0,
        "confirmedViewCoverageMismatch": 0,
        "domainMembershipRows": 0,
        "domainMembershipFallbackUsed": False,
        "claimsCompleteMapUsageTrue": 0,
        "claimsSpawnCoordinatesTrue": 0,
        "error": "TYPED_MAP_USAGE_CAPABILITY_MISSING",
    }
    try:
        schema_row = core.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        schema_version = str(schema_row[0]) if schema_row is not None else ""
        metrics["coreSchemaVersion"] = schema_version
        view_columns = {
            str(row[1])
            for row in core.execute(
                'PRAGMA table_info("confirmed_map_usage_edges")'
            )
        }
        fields_present = CONFIRMED_MAP_VIEW_REQUIRED_COLUMNS.issubset(
            view_columns
        )
        metrics["confirmedViewFieldsPresent"] = fields_present
        capability = (
            schema_version == CORE_SCHEMA_VERSION
            and supports_typed_map_usage_evidence(core)
            and fields_present
        )
        metrics["capability"] = capability
        if not capability:
            return metrics
        view_row = core.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type='view' AND name='confirmed_map_usage_edges'
            """
        ).fetchone()
        view_sql = str(view_row[0] or "") if view_row is not None else ""
        domain_membership_rows = int(
            core.execute(
                """
                SELECT COUNT(*)
                FROM domain_memberships
                WHERE domain_id IN ('map_world', 'pcg_world_partition')
                """
            ).fetchone()[0]
            or 0
        )
        row = core.execute(
            """
            WITH typed AS (
                SELECT
                    edge.edge_id,
                    CASE
                        WHEN edge.status IN (
                            'CONFIRMED', 'VERIFIED', 'RESOLVED'
                        )
                         AND edge.confidence IN ('HIGH', 'CONFIRMED')
                         AND evidence.source_evidence_status IN (
                            'CONFIRMED', 'VERIFIED', 'RESOLVED'
                         )
                         AND evidence.usage_status IN (
                            'CONFIRMED', 'VERIFIED', 'RESOLVED'
                         )
                         AND evidence.freshness_status='FRESH'
                         AND revision.freshness_status='FRESH'
                         AND edge.evidence_uri<>''
                         AND evidence.map_usage_id<>''
                         AND evidence.evidence_layer<>''
                         AND evidence.evidence_count>=1
                        THEN 1 ELSE 0
                    END AS confirmed,
                    CASE
                        WHEN edge.status='STALE'
                          OR evidence.freshness_status<>'FRESH'
                          OR revision.freshness_status<>'FRESH'
                        THEN 1 ELSE 0
                    END AS stale
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
            )
            SELECT
                COUNT(*),
                SUM(confirmed),
                SUM(CASE WHEN confirmed=0 AND stale=0 THEN 1 ELSE 0 END),
                SUM(stale)
            FROM typed
            """
        ).fetchone()
        typed_count = int(row[0] or 0)
        eligible_confirmed = int(row[1] or 0)
        candidate_count = int(row[2] or 0)
        stale_count = int(row[3] or 0)
        confirmed_count = int(
            core.execute(
                "SELECT COUNT(*) FROM confirmed_map_usage_edges"
            ).fetchone()[0]
            or 0
        )
        invalid_confirmed = int(
            core.execute(
                """
                SELECT COUNT(*)
                FROM confirmed_map_usage_edges AS confirmed
                JOIN source_revisions AS revision
                  ON revision.revision_id=confirmed.source_revision_id
                WHERE confirmed.status NOT IN (
                        'CONFIRMED', 'VERIFIED', 'RESOLVED'
                      )
                   OR confirmed.confidence NOT IN ('HIGH', 'CONFIRMED')
                   OR confirmed.source_evidence_status NOT IN (
                        'CONFIRMED', 'VERIFIED', 'RESOLVED'
                      )
                   OR confirmed.usage_status NOT IN (
                        'CONFIRMED', 'VERIFIED', 'RESOLVED'
                      )
                   OR confirmed.freshness_status<>'FRESH'
                   OR revision.freshness_status<>'FRESH'
                   OR confirmed.evidence_uri=''
                   OR confirmed.map_usage_id=''
                   OR confirmed.evidence_layer=''
                   OR confirmed.evidence_count<1
                   OR confirmed.claims_complete_map_usage NOT IN (0, 1)
                   OR confirmed.claims_spawn_coordinates NOT IN (0, 1)
                """
            ).fetchone()[0]
            or 0
        )
        claims = core.execute(
            """
            SELECT
                SUM(CASE WHEN claims_complete_map_usage=1 THEN 1 ELSE 0 END),
                SUM(CASE WHEN claims_spawn_coordinates=1 THEN 1 ELSE 0 END)
            FROM confirmed_map_usage_edges
            """
        ).fetchone()
        fallback_layer = int(
            core.execute(
                """
                SELECT COUNT(*)
                FROM map_usage_edge_evidence
                WHERE UPPER(evidence_layer) LIKE '%DOMAIN_MEMBERSHIP%'
                """
            ).fetchone()[0]
            or 0
        )
        metrics.update(
            {
                "typedEdgeCount": typed_count,
                "confirmedCount": confirmed_count,
                "candidateCount": candidate_count,
                "staleCount": stale_count,
                "invalidConfirmedRows": invalid_confirmed,
                "confirmedViewCoverageMismatch": abs(
                    confirmed_count - eligible_confirmed
                ),
                "domainMembershipRows": domain_membership_rows,
                "domainMembershipFallbackUsed": (
                    "domain_memberships" in view_sql.lower()
                    or fallback_layer > 0
                ),
                "claimsCompleteMapUsageTrue": int(claims[0] or 0),
                "claimsSpawnCoordinatesTrue": int(claims[1] or 0),
                "error": "",
            }
        )
    except sqlite3.DatabaseError as error:
        metrics["capability"] = False
        metrics["error"] = (
            "TYPED_MAP_USAGE_METRICS_ERROR:"
            + type(error).__name__
        )
    return metrics


def _typed_map_usage_gates(
    metrics: Mapping[str, object],
) -> list[dict[str, object]]:
    capability = metrics.get("capability") is True
    typed_count = int(metrics.get("typedEdgeCount") or 0)
    invalid_rows = int(metrics.get("invalidConfirmedRows") or 0)
    coverage_mismatch = int(
        metrics.get("confirmedViewCoverageMismatch") or 0
    )
    return [
        _gate(
            "maps.typed_usage_capability",
            "maps",
            target=f"{CORE_SCHEMA_VERSION} typed map contract",
            actual={
                "coreSchemaVersion": metrics.get("coreSchemaVersion"),
                "capability": capability,
                "confirmedViewFieldsPresent": metrics.get(
                    "confirmedViewFieldsPresent"
                ),
                "error": metrics.get("error"),
            },
            passed=capability,
            detail="Core exposes typed map Evidence and a confirmed-only view.",
        ),
        _gate(
            "maps.typed_usage_nonzero",
            "maps",
            target=">0 typed map relationship rows",
            actual=metrics,
            passed=capability and typed_count > 0,
            detail="Domain membership alone never satisfies map usage.",
        ),
        _gate(
            "maps.confirmed_view_integrity",
            "maps",
            target="zero invalid rows and exact confirmed-view coverage",
            actual={
                "confirmed": metrics.get("confirmedCount"),
                "candidate": metrics.get("candidateCount"),
                "stale": metrics.get("staleCount"),
                "invalidConfirmedRows": invalid_rows,
                "coverageMismatch": coverage_mismatch,
            },
            passed=(
                capability
                and typed_count > 0
                and invalid_rows == 0
                and coverage_mismatch == 0
            ),
            detail=(
                "Confirmed rows require confirmed/high edge and Evidence, "
                "fresh revision lineage, non-empty Evidence, and explicit "
                "map/spawn claim fields."
            ),
        ),
        _gate(
            "maps.no_domain_membership_substitution",
            "maps",
            target=False,
            actual=metrics.get("domainMembershipFallbackUsed"),
            passed=(
                capability
                and metrics.get("domainMembershipFallbackUsed") is False
            ),
            detail=(
                "map_world or PCG domain membership is never queried as "
                "map-usage proof."
            ),
        ),
    ]


def evaluate_quality_gates(
    *,
    project_root: Path,
    snapshot_root: Path,
    discovery_database: Path,
    generated_at: str | None = None,
) -> dict[str, object]:
    """Evaluate real snapshot metrics; absent independent evidence fails closed."""

    project_root = project_root.resolve()
    discovery_database = discovery_database.resolve()
    location = _resolve_quality_snapshot(snapshot_root)
    snapshot_dir = location.snapshot_dir
    manifest = location.manifest
    current_ontology_version = load_ontology(
        project_root / "ontology"
    ).version
    benchmark = run_query_benchmark(snapshot_dir / "core.sqlite")
    core = _read_only(snapshot_dir / "core.sqlite")
    discovery = _read_only(discovery_database)
    gates: list[dict[str, object]] = []
    map_usage = _typed_map_usage_metrics(core)
    gates.extend(_typed_map_usage_gates(map_usage))
    try:
        blueprint_total, class_known, parent_known = discovery.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN asset_class_path NOT IN ('', 'UNKNOWN')
                         THEN 1 ELSE 0 END),
                SUM(CASE
                      WHEN parent_class_path NOT IN ('', 'UNKNOWN')
                        OR native_parent_class_path NOT IN ('', 'UNKNOWN')
                      THEN 1 ELSE 0 END)
            FROM assets WHERE is_blueprint=1
            """
        ).fetchone()
        blueprint_total = int(blueprint_total or 0)
        class_known = int(class_known or 0)
        parent_known = int(parent_known or 0)
        class_rate = _ratio(class_known, blueprint_total)
        gates.append(
            _gate(
                "identity.blueprint_asset_class_path",
                "identity",
                target=">=0.99",
                actual=class_rate,
                passed=class_rate >= 0.99,
                detail=f"{class_known}/{blueprint_total} Blueprint assets",
            )
        )
        package_total, package_revision_valid = core.execute(
            """
            SELECT
                COUNT(*),
                SUM(
                    CASE
                      WHEN revision.revision_id IS NOT NULL
                       AND revision.source_kind='asset_package'
                       AND revision.freshness_status='FRESH'
                       AND revision.source_uri=
                           'package://' || package.package_path
                       AND revision.source_fingerprint<>''
                      THEN 1 ELSE 0
                    END
                )
            FROM packages AS package
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=package.current_revision_id
            """
        ).fetchone()
        package_total = int(package_total or 0)
        package_revision_valid = int(package_revision_valid or 0)
        gates.append(
            _gate(
                "identity.package_revision_provenance",
                "identity",
                target="100% package-specific fresh revisions",
                actual=_ratio(package_revision_valid, package_total),
                passed=(
                    package_total > 0
                    and package_revision_valid == package_total
                ),
                detail=(
                    f"{package_revision_valid}/{package_total} packages "
                    "bind their own fingerprint revision."
                ),
            )
        )
        closure_metrics = _class_closure_metrics(core)
        closure_rate = float(closure_metrics["closureRate"])
        applicable_count = int(
            closure_metrics["classApplicableCount"]
        )
        gates.append(
            _gate(
                "identity.deep_parent_native_closure",
                "identity",
                target=">=0.98",
                actual=closure_metrics,
                passed=applicable_count > 0 and closure_rate >= 0.98,
                detail=(
                    f"{closure_metrics['classClosedCount']}/"
                    f"{applicable_count} applicable deep/semantic entities; "
                    f"{closure_metrics['classNotApplicableCount']} "
                    "not applicable"
                ),
            )
        )
        class_gap_count = int(
            core.execute("SELECT COUNT(*) FROM class_gaps").fetchone()[0]
        )
        data_asset_count = int(
            core.execute(
                """
                SELECT COUNT(DISTINCT assignment.entity_id)
                FROM asset_class_assignments AS assignment
                JOIN class_ancestry_categories AS category
                  ON category.class_id=assignment.class_id
                WHERE category.category IN ('DATA_ASSET', 'PRIMARY_DATA_ASSET')
                """
            ).fetchone()[0]
        )
        gates.append(
            _gate(
                "identity.data_asset_ancestry_model",
                "identity",
                target="ancestry table queried with explicit gaps",
                actual={
                    "classifiedAssets": data_asset_count,
                    "classGaps": class_gap_count,
                    "blueprintParentKnown": parent_known,
                },
                passed=class_gap_count >= 0,
                detail=(
                    "DataAsset status is represented by ancestry categories; "
                    "zero results are not interpreted as proof of absence."
                ),
            )
        )
        role_gold = _role_gold_metrics(project_root, core)
        gates.append(
            _gate(
                "roles.independent_gold_set",
                "roles",
                target=">=300 assets and precision >=0.95",
                actual=role_gold,
                passed=(
                    int(role_gold["assets"]) >= 300
                    and role_gold["precision"] is not None
                    and float(role_gold["precision"]) >= 0.95
                    and role_gold["recall"] is not None
                    and float(role_gold["recall"]) >= 0.95
                    and float(role_gold["resolutionRate"]) == 1.0
                ),
                detail=str(role_gold["detail"]),
            )
        )
        role_total = int(
            core.execute("SELECT COUNT(*) FROM knowledge_roles").fetchone()[0]
        )
        role_revision_valid = int(
            core.execute(
                """
                SELECT COUNT(*)
                FROM knowledge_roles AS role
                JOIN source_revisions AS revision
                  ON revision.revision_id=role.source_revision_id
                WHERE revision.freshness_status='FRESH'
                  AND revision.source_kind='role_classifier'
                  AND revision.source_uri<>''
                  AND revision.source_fingerprint<>''
                """
            ).fetchone()[0]
        )
        domain_total, domain_revision_valid = core.execute(
            """
            SELECT
                COUNT(*),
                SUM(
                    CASE
                      WHEN revision.revision_id IS NOT NULL
                       AND revision.freshness_status='FRESH'
                       AND revision.source_kind='ontology'
                       AND revision.source_uri<>''
                       AND revision.source_fingerprint<>''
                      THEN 1 ELSE 0
                    END
                )
            FROM domain_memberships AS membership
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=membership.source_revision_id
            """
        ).fetchone()
        domain_total = int(domain_total or 0)
        domain_revision_valid = int(domain_revision_valid or 0)
        unexplained_roles = int(
            core.execute(
                """
                SELECT COUNT(*) FROM knowledge_roles
                WHERE reasons_json IN ('', '[]', '{}', 'null')
                """
            ).fetchone()[0]
        )
        visual_total = int(
            core.execute(
                """
                SELECT COUNT(DISTINCT entity_id) FROM knowledge_roles
                WHERE role='visual_support_asset'
                """
            ).fetchone()[0]
        )
        visual_deep = int(
            core.execute(
                """
                SELECT COUNT(DISTINCT role.entity_id)
                FROM knowledge_roles AS role
                JOIN knowledge_depth_policies AS policy
                  ON policy.entity_id=role.entity_id
                WHERE role.role='visual_support_asset'
                  AND policy.depth_policy IN ('DEEP', 'SEMANTIC')
                """
            ).fetchone()[0]
        )
        visual_rate = _ratio(visual_deep, visual_total)
        gates.extend(
            [
                _gate(
                    "roles.explainable",
                    "roles",
                    target="100%",
                    actual=_ratio(role_total - unexplained_roles, role_total),
                    passed=role_total > 0 and unexplained_roles == 0,
                    detail=f"{role_total - unexplained_roles}/{role_total} role rows",
                ),
                _gate(
                    "roles.visual_false_promotion",
                    "roles",
                    target="<0.02",
                    actual=visual_rate,
                    passed=visual_rate < 0.02,
                    detail=f"{visual_deep}/{visual_total} visual entities deep/semantic",
                ),
                _gate(
                    "roles.source_revision_provenance",
                    "roles",
                    target="100% fresh role-classifier revisions",
                    actual=_ratio(role_revision_valid, role_total),
                    passed=(
                        role_total > 0
                        and role_revision_valid == role_total
                    ),
                    detail=(
                        f"{role_revision_valid}/{role_total} role rows "
                        "have an independent fresh classifier revision."
                    ),
                ),
                _gate(
                    "domains.source_revision_provenance",
                    "domains",
                    target="100% fresh ontology revisions",
                    actual=_ratio(
                        domain_revision_valid,
                        domain_total,
                    ),
                    passed=(
                        domain_total > 0
                        and domain_revision_valid == domain_total
                    ),
                    detail=(
                        f"{domain_revision_valid}/{domain_total} domain "
                        "rows have an independent fresh ontology revision."
                    ),
                ),
            ]
        )
        registration_confidence = _registration_confidence_metrics(core)
        gates.append(
            _registration_confidence_gate(registration_confidence)
        )
        registration_gold = _registration_gold_metrics(project_root, core)
        registration_lineage = _registration_lineage_metrics(core)
        typed_total = int(registration_lineage["total"])
        typed_incomplete = int(registration_lineage["incomplete"])
        gates.extend(
            [
                _gate(
                    "registrations.real_relationship_gold_count",
                    "registrations",
                    target=">=100 independently reviewed Owner→Target rows",
                    actual=registration_gold["relationships"],
                    passed=(
                        bool(registration_gold["available"])
                        and int(registration_gold["relationships"]) >= 100
                    ),
                    detail=str(
                        registration_gold.get("gapCode")
                        or "Independent relationship rows are available."
                    ),
                ),
                _gate(
                    "registrations.gold_precision",
                    "registrations",
                    target=">=0.99",
                    actual=registration_gold["precision"],
                    passed=(
                        int(registration_gold["relationships"]) >= 100
                        and float(registration_gold["precision"]) >= 0.99
                    ),
                    detail=f"{registration_gold['relationships']} explicit gold relationships",
                ),
                _gate(
                    "registrations.gold_recall",
                    "registrations",
                    target=">=0.95",
                    actual=registration_gold["recall"],
                    passed=(
                        int(registration_gold["relationships"]) >= 100
                        and float(registration_gold["recall"]) >= 0.95
                    ),
                    detail=f"{registration_gold['relationships']} explicit gold relationships",
                ),
                _gate(
                    "registrations.classification_precision",
                    "registrations",
                    target=">=0.99",
                    actual=registration_gold["classificationPrecision"],
                    passed=(
                        int(registration_gold["relationships"]) >= 100
                        and float(
                            registration_gold["classificationPrecision"]
                        )
                        >= 0.99
                    ),
                    detail="Expected typed edge versus materialized edge type.",
                ),
                _gate(
                    "registrations.classification_recall",
                    "registrations",
                    target=">=0.95",
                    actual=registration_gold["classificationRecall"],
                    passed=(
                        int(registration_gold["relationships"]) >= 100
                        and float(
                            registration_gold["classificationRecall"]
                        )
                        >= 0.95
                    ),
                    detail="Expected typed edge versus materialized edge type.",
                ),
                _gate(
                    "registrations.owner_resolution",
                    "registrations",
                    target="100%",
                    actual=registration_gold["ownerResolutionRate"],
                    passed=(
                        int(registration_gold["relationships"]) >= 100
                        and float(
                            registration_gold["ownerResolutionRate"]
                        )
                        == 1.0
                    ),
                    detail="Every reviewed Owner URI resolves canonically.",
                ),
                _gate(
                    "registrations.target_resolution",
                    "registrations",
                    target="100%",
                    actual=registration_gold["targetResolutionRate"],
                    passed=(
                        int(registration_gold["relationships"]) >= 100
                        and float(
                            registration_gold["targetResolutionRate"]
                        )
                        == 1.0
                    ),
                    detail="Every reviewed Target URI resolves canonically.",
                ),
                _gate(
                    "registrations.edge_materialization",
                    "registrations",
                    target=">=0.95",
                    actual=registration_gold["edgeMaterializationRate"],
                    passed=(
                        int(registration_gold["relationships"]) >= 100
                        and float(
                            registration_gold["edgeMaterializationRate"]
                        )
                        >= 0.95
                    ),
                    detail="Expected status and typed edge are persisted.",
                ),
                _gate(
                    "registrations.evidence_correctness",
                    "registrations",
                    target="100%",
                    actual=registration_gold["evidenceCorrectnessRate"],
                    passed=(
                        int(registration_gold["relationships"]) >= 100
                        and float(
                            registration_gold["evidenceCorrectnessRate"]
                        )
                        == 1.0
                    ),
                    detail="Persisted evidence URI matches reviewed evidence.",
                ),
                _gate(
                    "registrations.lineage_complete",
                    "registrations",
                    target="100%",
                    actual=_ratio(typed_total - typed_incomplete, typed_total),
                    passed=typed_total > 0 and typed_incomplete == 0,
                    detail=f"{typed_total - typed_incomplete}/{typed_total} typed registrations",
                ),
            ]
        )
        native_gold = _native_gold_metrics(core)
        native_targets = native_gold["targets"]
        native_confirmed = native_gold["confirmed"]
        link_rows = list(
            core.execute(
            """
            SELECT
                link.status AS link_status,
                link.native_function_id,
                link.blueprint_graph_evidence_uri,
                link.native_evidence_uri,
                function.status AS native_function_status,
                function.confidence AS native_function_confidence,
                graph_revision.revision_id AS graph_revision_id,
                graph_revision.source_kind AS graph_source_kind,
                graph_revision.source_uri AS graph_source_uri,
                graph_revision.source_fingerprint AS graph_source_fingerprint,
                graph_revision.producer_version AS graph_producer_version,
                graph_revision.schema_version AS graph_schema_version,
                graph_revision.generated_at AS graph_generated_at,
                graph_revision.freshness_status AS graph_freshness_status,
                native_revision.revision_id AS native_revision_id,
                native_revision.source_kind AS native_source_kind,
                native_revision.source_uri AS native_source_uri,
                native_revision.source_fingerprint AS native_source_fingerprint,
                native_revision.producer_version AS native_producer_version,
                native_revision.schema_version AS native_schema_version,
                native_revision.generated_at AS native_generated_at,
                native_revision.freshness_status AS native_freshness_status
            FROM native_blueprint_links AS link
            LEFT JOIN source_revisions AS graph_revision
              ON graph_revision.revision_id=
                 link.blueprint_graph_source_revision_id
            LEFT JOIN native_functions AS function
              ON function.native_function_id=link.native_function_id
            LEFT JOIN source_revisions AS native_revision
              ON native_revision.revision_id=function.source_revision_id
            """
            )
        )
        confirmed_links = sum(
            str(row["link_status"] or "").upper() == "CONFIRMED"
            for row in link_rows
        )
        valid_links = sum(
            (
                str(row["link_status"] or "").upper() == "CONFIRMED"
                and row["native_function_id"] is not None
                and bool(str(row["blueprint_graph_evidence_uri"] or ""))
                and bool(str(row["native_evidence_uri"] or ""))
                and str(
                    row["native_function_status"] or ""
                ).upper()
                in CONFIRMED_RELATIONSHIP_STATUSES
                and str(
                    row["native_function_confidence"] or ""
                ).upper()
                in {"HIGH", "CONFIRMED"}
                and _strict_source_revision_is_fresh(
                    revision_id=row["graph_revision_id"],
                    source_kind=row["graph_source_kind"],
                    source_uri=row["graph_source_uri"],
                    source_fingerprint=row[
                        "graph_source_fingerprint"
                    ],
                    producer_version=row["graph_producer_version"],
                    schema_version=row["graph_schema_version"],
                    generated_at=row["graph_generated_at"],
                    freshness_status=row["graph_freshness_status"],
                )
                and _strict_source_revision_is_fresh(
                    revision_id=row["native_revision_id"],
                    source_kind=row["native_source_kind"],
                    source_uri=row["native_source_uri"],
                    source_fingerprint=row[
                        "native_source_fingerprint"
                    ],
                    producer_version=row["native_producer_version"],
                    schema_version=row["native_schema_version"],
                    generated_at=row["native_generated_at"],
                    freshness_status=row["native_freshness_status"],
                )
            )
            for row in link_rows
        )
        gates.extend(
            [
                _gate(
                    "native.gold_targets_resolved",
                    "native",
                    target="100%",
                    actual=_ratio(native_confirmed, native_targets),
                    passed=(
                        native_targets >= 20
                        and native_confirmed == native_targets
                    ),
                    detail=f"{native_confirmed}/{native_targets} exact native targets",
                ),
                _gate(
                    "native.blueprint_link_precision",
                    "native",
                    target="100% with at least one confirmed link",
                    actual={
                        "confirmed": confirmed_links,
                        "fullyBound": valid_links,
                    },
                    passed=confirmed_links > 0 and valid_links == confirmed_links,
                    detail=(
                        "Zero confirmed Blueprint-native links is not treated "
                        "as vacuous 100% precision."
                    ),
                ),
            ]
        )
        fact_total, fact_with_evidence = core.execute(
            """
            SELECT
              COUNT(*),
              SUM(CASE WHEN EXISTS(
                    SELECT 1 FROM fact_evidence AS evidence
                    JOIN source_revisions AS revision
                      ON revision.revision_id=evidence.source_revision_id
                    WHERE evidence.fact_id=fact.fact_id
                      AND evidence.evidence_uri<>''
                      AND revision.freshness_status='FRESH'
                ) THEN 1 ELSE 0 END)
            FROM facts AS fact WHERE fact.current=1
            """
        ).fetchone()
        fact_total = int(fact_total or 0)
        fact_with_evidence = int(fact_with_evidence or 0)
        unknown_with_zero = int(
            core.execute(
                """
                SELECT COUNT(*) FROM facts
                WHERE status='UNKNOWN'
                  AND (
                    value_number=0 OR value_integer=0
                    OR value_text IN ('0', '0.0')
                  )
                """
            ).fetchone()[0]
        )
        invalid_effective = int(
            core.execute(
                """
                SELECT COUNT(*)
                FROM effective_facts AS effective
                LEFT JOIN facts AS fact ON fact.fact_id=effective.fact_id
                WHERE effective.fact_type<>'EFFECTIVE_DEFAULT'
                   OR (
                        UPPER(effective.resolution_status)='RESOLVED'
                        AND (
                            effective.fact_id IS NULL
                            OR fact.fact_id IS NULL
                            OR fact.fact_type<>'DECLARED_DEFAULT'
                            OR fact.declared_on_entity_id IS NULL
                            OR fact.current<>1
                        )
                   )
                   OR (
                        UPPER(effective.resolution_status)<>'RESOLVED'
                        AND effective.fact_id IS NOT NULL
                   )
                """
            ).fetchone()[0]
        )
        effective_candidates = _effective_candidate_metrics(core)
        effective_resolution = _effective_resolution_metrics(core)
        duplicate_facts = int(
            core.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT canonical_fact_key
                  FROM facts
                  GROUP BY canonical_fact_key
                  HAVING COUNT(*)>1
                )
                """
            ).fetchone()[0]
        )
        gates.extend(
            [
                _gate(
                    "facts.provenance_complete",
                    "facts",
                    target="100%",
                    actual=_ratio(fact_with_evidence, fact_total),
                    passed=fact_total > 0 and fact_with_evidence == fact_total,
                    detail=f"{fact_with_evidence}/{fact_total} current facts",
                ),
                _gate(
                    "facts.unknown_not_zero",
                    "facts",
                    target=0,
                    actual=unknown_with_zero,
                    passed=unknown_with_zero == 0,
                    detail="UNKNOWN facts must not acquire a synthetic zero.",
                ),
                _gate(
                    "facts.declared_effective_separated",
                    "facts",
                    target=0,
                    actual=invalid_effective,
                    passed=invalid_effective == 0,
                    detail=(
                        "Resolved rows point to current declared defaults; "
                        "unresolved rows retain a null fact_id."
                    ),
                ),
                _gate(
                    "facts.effective_candidate_consistency",
                    "facts",
                    target=(
                        "one matching selected candidate for RESOLVED; "
                        "none selected for unresolved; no orphan candidates"
                    ),
                    actual=effective_candidates,
                    passed=bool(effective_candidates["consistent"]),
                    detail=(
                        f"{effective_candidates['invalidSelectionRows']} "
                        "effective rows have invalid candidate selection; "
                        f"{effective_candidates['orphanCandidateRows']} "
                        "candidate rows have no effective row; "
                        f"{effective_candidates['invalidCandidateLineageRows']} "
                        "candidate rows have invalid declared lineage."
                    ),
                ),
                _gate(
                    "facts.effective_resolution_reality",
                    "facts",
                    target=(
                        "all effective paths, assignments, selected facts, "
                        "native-root proofs and revision hashes validate"
                    ),
                    actual=effective_resolution,
                    passed=bool(effective_resolution["consistent"]),
                    detail=(
                        "Validated effective path reality and exact revision "
                        "dependencies."
                        if effective_resolution["consistent"]
                        else str(effective_resolution["error"])
                    ),
                ),
                _gate(
                    "facts.canonical_deduplicated",
                    "facts",
                    target=0,
                    actual=duplicate_facts,
                    passed=duplicate_facts == 0,
                    detail="Canonical fact keys remain unique.",
                ),
            ]
        )
        gates.extend(
            semantic_quality_gates(
                core,
                manifest,
                snapshot_root=snapshot_dir,
                expected_ontology_version=current_ontology_version,
                review_path=(
                    project_root
                    / "ontology"
                    / "projection_review.v1.json"
                ),
            )
        )
        gates.extend(_query_benchmark_gates(benchmark))
        gates.extend(
            [
                _gate(
                    "queries.single_entity_p95_ms",
                    "performance",
                    target="<250",
                    actual=benchmark["latencyMs"]["p95"],
                    passed=float(benchmark["latencyMs"]["p95"]) < 250,
                    detail="120 read-only planner/context executions.",
                ),
                _gate(
                    "queries.two_hop_p95_ms",
                    "performance",
                    target="<800",
                    actual=benchmark["latencyMs"]["twoHopP95"],
                    passed=(
                        int(benchmark["latencyMs"]["twoHopSamples"]) > 0
                        and float(benchmark["latencyMs"]["twoHopP95"]) < 800
                    ),
                    detail=f"{benchmark['latencyMs']['twoHopSamples']} indexed samples",
                ),
                _gate(
                    "queries.context_budget",
                    "performance",
                    target="<=2000",
                    actual=benchmark["contextTokens"]["maximum"],
                    passed=bool(benchmark["contextTokens"]["withinBudget"]),
                    detail="Maximum estimated tokens across the benchmark.",
                ),
            ]
        )
        dependency_kinds = {
            str(row[0]): int(row[1])
            for row in core.execute(
                """
                SELECT downstream_kind, COUNT(*)
                FROM invalidation_dependencies
                GROUP BY downstream_kind
                """
            )
        }
        required_dependency_kinds = {
            "ROLE_ENTITY",
            "DOMAIN_ENTITY",
            "NATIVE_FUNCTION",
        }
        gates.append(
            _gate(
                "incremental.dependency_graph",
                "incremental",
                target=sorted(required_dependency_kinds),
                actual=dependency_kinds,
                passed=required_dependency_kinds.issubset(dependency_kinds),
                detail="Selective invalidation roots are materialized.",
            )
        )
        plan_rows = list(
            core.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT second.target_entity_id
                FROM edges AS first
                JOIN edges AS second
                  ON second.source_entity_id=first.target_entity_id
                WHERE first.source_entity_id=1
                LIMIT 200
                """
            )
        )
        plan_text = " | ".join(str(row[3]) for row in plan_rows)
        gates.append(
            _gate(
                "performance.large_query_indexed",
                "performance",
                target="indexed EXPLAIN QUERY PLAN",
                actual=plan_text,
                passed="INDEX" in plan_text.upper(),
                detail="Two-hop traversal plan.",
            )
        )
    finally:
        core.close()
        discovery.close()
    integrity = _integrity_metrics(snapshot_dir)
    gates.append(_storage_integrity_gate(integrity))
    core_bytes = int(integrity["core.sqlite"]["bytes"])
    discovery_bytes = discovery_database.stat().st_size
    gates.append(
        _gate(
            "storage.core_smaller_than_discovery",
            "storage",
            target="<1.0",
            actual=_ratio(core_bytes, discovery_bytes),
            passed=core_bytes < discovery_bytes,
            detail=f"{core_bytes} core bytes vs {discovery_bytes} discovery bytes",
        )
    )
    generated_at = generated_at or datetime.now(UTC).isoformat(
        timespec="seconds"
    )
    failed = [gate for gate in gates if gate["critical"] and not gate["passed"]]
    report: dict[str, object] = {
        "schema": QUALITY_GATE_SCHEMA,
        "generatedAt": generated_at,
        "buildId": location.build_id,
        "summary": {
            "total": len(gates),
            "passed": sum(bool(gate["passed"]) for gate in gates),
            "failed": len(failed),
            "cutoverEligible": not failed,
            "recommendation": (
                "ready_for_default" if not failed else "keep_legacy_shadow"
            ),
        },
        "gates": gates,
        "benchmark": benchmark,
        "mapUsage": map_usage,
    }
    privacy_hits = _privacy_scan(report)
    privacy_gate = _gate(
        "privacy.no_local_paths",
        "privacy",
        target=0,
        actual=privacy_hits,
        passed=not privacy_hits,
        detail="Report payload excludes local absolute paths.",
    )
    gates.append(privacy_gate)
    if not privacy_gate["passed"]:
        failed.append(privacy_gate)
    validate_quality_gate_contract(gates)
    report["summary"] = {
        "total": len(gates),
        "passed": sum(bool(gate["passed"]) for gate in gates),
        "failed": len(failed),
        "cutoverEligible": not failed,
        "recommendation": (
            "ready_for_default" if not failed else "keep_legacy_shadow"
        ),
    }
    return report


def _write_json_atomic(path: Path, payload: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    contents = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    temporary.write_bytes(contents)
    os.replace(temporary, path)
    return contents


def publish_gate_report(
    *,
    snapshot_root: Path,
    report: dict[str, object],
) -> dict[str, object]:
    """Publish reports and update cutover atomically without deleting legacy."""

    location = _resolve_quality_snapshot(snapshot_root)
    report_build_id = str(report.get("buildId") or "")
    if report_build_id != location.build_id:
        raise ValueError(
            "quality gate report buildId does not match the resolved snapshot"
        )
    eligible = bool(report["summary"]["cutoverEligible"])
    benchmark = report["benchmark"]
    if location.layout.startswith("immutable-v2"):
        configured_root = _immutable_configured_root(location)
        reports = configured_root / "reports" / location.build_id
        benchmark_bytes = _write_json_atomic(
            reports / "query_benchmark.json",
            benchmark,
        )
        gate_bytes = _write_json_atomic(
            reports / "quality_gates.json",
            report,
        )
        gate_sha = hashlib.sha256(gate_bytes).hexdigest()
        benchmark_sha = hashlib.sha256(benchmark_bytes).hexdigest()
        manifest_sha = hashlib.sha256(
            location.manifest_path.read_bytes()
        ).hexdigest()
        failed = int(report["summary"]["failed"])
        cutover = {
            "mode": "shadow",
            "defaultQuerySource": "legacy",
            "reason": (
                "quality gates passed, but the mutable attestation is not "
                "sealed in the immutable manifest; publish a new immutable "
                "snapshot before cutover"
                if eligible
                else f"{failed} critical quality gates remain open"
            ),
        }
        attestation = {
            "schema": "ark-kb-vnext-cutover-attestation/v1",
            "buildId": location.build_id,
            "snapshotLayout": "immutable-v2",
            "immutableManifestSha256": manifest_sha,
            "reportCutoverEligible": eligible,
            "sealedInSnapshotManifest": False,
            "qualityGates": {
                "schema": QUALITY_GATE_SCHEMA,
                "reportUri": (
                    f"reports/{location.build_id}/quality_gates.json"
                ),
                "sha256": gate_sha,
                "passed": int(report["summary"]["passed"]),
                "failed": failed,
            },
            "queryBenchmark": {
                "reportUri": (
                    f"reports/{location.build_id}/query_benchmark.json"
                ),
                "sha256": benchmark_sha,
            },
            "cutover": cutover,
        }
        _write_json_atomic(
            reports / "cutover_attestation.json",
            attestation,
        )
        return cutover

    reports = location.root / "reports"
    _write_json_atomic(reports / "query_benchmark.json", benchmark)
    gate_bytes = _write_json_atomic(reports / "quality_gates.json", report)
    gate_sha = hashlib.sha256(gate_bytes).hexdigest()
    current_path = location.manifest_path
    manifest = dict(location.manifest)
    manifest["qualityGates"] = {
        "schema": QUALITY_GATE_SCHEMA,
        "reportUri": "reports/quality_gates.json",
        "sha256": gate_sha,
        "passed": int(report["summary"]["passed"]),
        "failed": int(report["summary"]["failed"]),
    }
    manifest["cutover"] = {
        "mode": "ready" if eligible else "shadow",
        "defaultQuerySource": "vnext" if eligible else "legacy",
        "reason": (
            "all critical quality gates passed"
            if eligible
            else (
                f"{report['summary']['failed']} critical quality gates "
                "remain open"
            )
        ),
    }
    _write_json_atomic(current_path, manifest)
    build_id = str(manifest.get("buildId") or "")
    build_manifest = (
        location.root / "manifests" / f"{build_id}.json"
    )
    if build_manifest.is_file():
        _write_json_atomic(build_manifest, manifest)
    return manifest["cutover"]
