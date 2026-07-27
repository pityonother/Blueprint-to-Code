"""Evidence-backed quality gates and fail-closed cutover decision."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from .benchmark import run_query_benchmark
from .invalidation import validate_effective_resolution_dependencies
from .ontology import load_ontology
from .projections import DOMAIN_PROJECTIONS
from .registrations import classify_registration_property
from .schema_capabilities import (
    CORE_SCHEMA_VERSION,
    supports_typed_map_usage_evidence,
)
from .semantic_quality import semantic_quality_gates


QUALITY_GATE_SCHEMA = "ark-kb-quality-gates/v1"
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

    incomplete_high = """
        upper(status) NOT IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
        AND upper(confidence) IN ('HIGH', 'CONFIRMED')
    """
    metrics = {
        "typedRegistrations": int(
            core.execute(
                f"""
                SELECT COUNT(*) FROM typed_registrations
                WHERE {incomplete_high}
                """
            ).fetchone()[0]
        ),
        "registrationEdges": int(
            core.execute(
                f"""
                SELECT COUNT(*) FROM edges
                WHERE edge_type='REGISTERS'
                  AND {incomplete_high}
                """
            ).fetchone()[0]
        ),
        "typedMemberships": int(
            core.execute(
                f"""
                SELECT COUNT(*) FROM domain_memberships
                WHERE membership_kind='TYPED_REGISTRATION'
                  AND {incomplete_high}
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
            "Candidate and legacy registration claims, REGISTERS edges, "
            "and typed memberships must not carry complete confidence."
        ),
    )


def _registration_gold_metrics(project_root: Path) -> dict[str, object]:
    gold_path = (
        project_root
        / "tests"
        / "fixtures"
        / "kb_registration_gold_set.json"
    )
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    expected = {
        (str(property_name), str(registration_type))
        for property_name, registration_type in gold["cases"]
    }
    actual: set[tuple[str, str]] = set()
    for property_name, _ in expected:
        actual.update(
            (
                property_name,
                result.registration_type,
            )
            for result in classify_registration_property(property_name)
            if result.status == "CONFIRMED"
        )
    negative_predictions = sum(
        1
        for property_name in gold["negativeCases"]
        for result in classify_registration_property(str(property_name))
        if result.status == "CONFIRMED"
    )
    true_positive = len(expected.intersection(actual))
    false_positive = len(actual - expected) + negative_predictions
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, len(expected))
    return {
        "relationships": len(gold["owners"]) * len(gold["cases"]),
        "precision": precision,
        "recall": recall,
        "negativeFalsePositives": negative_predictions,
    }


def _role_gold_metrics(project_root: Path) -> dict[str, object]:
    path = project_root / "tests" / "fixtures" / "kb_role_gold_set.json"
    if not path.is_file():
        return {
            "available": False,
            "assets": 0,
            "precision": None,
            "detail": (
                "No independently reviewed 300-asset role gold set exists; "
                "classifier unit cases are not counted as production gold."
            ),
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    reviewed = [
        case
        for case in cases
        if isinstance(case, dict)
        and case.get("reviewStatus") in {"HUMAN_REVIEWED", "EMPIRICAL"}
    ]
    correct = sum(bool(case.get("correct")) for case in reviewed)
    return {
        "available": True,
        "assets": len(reviewed),
        "precision": _ratio(correct, len(reviewed)),
        "detail": "Independent role-gold review records.",
    }


def _integrity_metrics(snapshot_root: Path) -> dict[str, object]:
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
    return result


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
    return [
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
    snapshot_root = snapshot_root.resolve()
    discovery_database = discovery_database.resolve()
    manifest = json.loads(
        (snapshot_root / "manifests" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    current_ontology_version = load_ontology(
        project_root / "ontology"
    ).version
    benchmark = run_query_benchmark(snapshot_root / "core.sqlite")
    core = _read_only(snapshot_root / "core.sqlite")
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
        role_gold = _role_gold_metrics(project_root)
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
        registration_gold = _registration_gold_metrics(project_root)
        typed_total, typed_incomplete = core.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE
                      WHEN owner_uri='' OR target_uri='' OR source_property=''
                        OR evidence_uri=''
                      THEN 1 ELSE 0 END)
            FROM typed_registrations
            """
        ).fetchone()
        typed_total = int(typed_total or 0)
        typed_incomplete = int(typed_incomplete or 0)
        gates.extend(
            [
                _gate(
                    "registrations.gold_precision",
                    "registrations",
                    target=">=0.99",
                    actual=registration_gold["precision"],
                    passed=float(registration_gold["precision"]) >= 0.99,
                    detail=f"{registration_gold['relationships']} explicit gold relationships",
                ),
                _gate(
                    "registrations.gold_recall",
                    "registrations",
                    target=">=0.95",
                    actual=registration_gold["recall"],
                    passed=float(registration_gold["recall"]) >= 0.95,
                    detail=f"{registration_gold['relationships']} explicit gold relationships",
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
        native_targets, native_confirmed = core.execute(
            """
            SELECT COUNT(*),
                   SUM(
                     CASE
                       WHEN target.status='CONFIRMED'
                        AND function.status='CONFIRMED'
                        AND function.confidence='HIGH'
                        AND revision.freshness_status='FRESH'
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
        native_targets = int(native_targets or 0)
        native_confirmed = int(native_confirmed or 0)
        confirmed_links, valid_links = core.execute(
            """
            SELECT
              SUM(CASE WHEN link.status='CONFIRMED' THEN 1 ELSE 0 END),
              SUM(CASE
                    WHEN link.status='CONFIRMED'
                     AND link.native_function_id IS NOT NULL
                     AND link.blueprint_graph_evidence_uri<>''
                     AND link.native_evidence_uri<>''
                     AND graph_revision.revision_id IS NOT NULL
                     AND graph_revision.freshness_status='FRESH'
                     AND native_revision.revision_id IS NOT NULL
                     AND native_revision.freshness_status='FRESH'
                    THEN 1 ELSE 0 END)
            FROM native_blueprint_links AS link
            LEFT JOIN source_revisions AS graph_revision
              ON graph_revision.revision_id=
                 link.blueprint_graph_source_revision_id
            LEFT JOIN native_functions AS function
              ON function.native_function_id=link.native_function_id
            LEFT JOIN source_revisions AS native_revision
              ON native_revision.revision_id=function.source_revision_id
            """
        ).fetchone()
        confirmed_links = int(confirmed_links or 0)
        valid_links = int(valid_links or 0)
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
                snapshot_root=snapshot_root,
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
    integrity = _integrity_metrics(snapshot_root)
    integrity_passed = all(
        bool(item["exists"])
        and item["integrity"] == "ok"
        and int(item["foreignKeyViolations"]) == 0
        and bool(item.get("verified"))
        for item in integrity.values()
        if isinstance(item, dict)
    )
    gates.append(
        _gate(
            "storage.integrity",
            "storage",
            target="all databases ok; zero FK violations",
            actual=integrity,
            passed=integrity_passed,
            detail="Published read-only snapshot stores.",
        )
    )
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
        "buildId": str(manifest.get("buildId") or ""),
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

    snapshot_root = snapshot_root.resolve()
    reports = snapshot_root / "reports"
    benchmark = report["benchmark"]
    _write_json_atomic(reports / "query_benchmark.json", benchmark)
    gate_bytes = _write_json_atomic(reports / "quality_gates.json", report)
    gate_sha = hashlib.sha256(gate_bytes).hexdigest()
    current_path = snapshot_root / "manifests" / "current.json"
    manifest = json.loads(current_path.read_text(encoding="utf-8"))
    eligible = bool(report["summary"]["cutoverEligible"])
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
    build_manifest = snapshot_root / "manifests" / f"{build_id}.json"
    if build_manifest.is_file():
        _write_json_atomic(build_manifest, manifest)
    return manifest["cutover"]
