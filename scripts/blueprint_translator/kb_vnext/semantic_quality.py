"""Fail-closed semantic-content gates for ARK Knowledge Base vNext."""

from __future__ import annotations

import sqlite3
from typing import Mapping

from .projections import DOMAIN_PROJECTIONS


USABLE_FACT_STATUSES = (
    "CONFIRMED",
    "VERIFIED",
    "RESOLVED",
    "CONFIRMED_EMPTY",
)
USABLE_EFFECTIVE_STATUSES = (
    "CONFIRMED",
    "VERIFIED",
    "RESOLVED",
)
REVIEWED_PROJECTION_STATUSES = {
    "HUMAN_REVIEWED",
    "EMPIRICAL",
    "FIXTURE_EXACT",
}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _gate(
    gate_id: str,
    category: str,
    *,
    target: object,
    actual: object,
    passed: bool,
    detail: str,
) -> dict[str, object]:
    return {
        "id": gate_id,
        "category": category,
        "target": target,
        "actual": actual,
        "passed": bool(passed),
        "critical": True,
        "detail": detail,
    }


def _usable_fact_predicate(alias: str) -> str:
    statuses = ", ".join(f"'{value}'" for value in USABLE_FACT_STATUSES)
    return f"""
        UPPER({alias}.status) IN ({statuses})
        AND UPPER({alias}.value_kind) NOT IN ('FINGERPRINT', 'UNKNOWN')
        AND (
            UPPER({alias}.value_kind)='CONFIRMED_EMPTY'
            OR {alias}.value_text IS NOT NULL
            OR {alias}.value_number IS NOT NULL
            OR {alias}.value_integer IS NOT NULL
            OR {alias}.value_json IS NOT NULL
        )
    """


def _semantic_fact_metrics(
    core: sqlite3.Connection,
) -> dict[str, int | float]:
    usable = _usable_fact_predicate("fact")
    total_facts, usable_facts = core.execute(
        f"""
        SELECT
            COUNT(*),
            SUM(CASE WHEN {usable} THEN 1 ELSE 0 END)
        FROM facts AS fact
        WHERE fact.current=1
        """
    ).fetchone()
    total_facts = int(total_facts or 0)
    usable_facts = int(usable_facts or 0)
    fresh_semantic_facts = int(
        core.execute(
            f"""
            SELECT COUNT(*)
            FROM facts AS fact
            WHERE fact.current=1
              AND {usable}
              AND EXISTS (
                  SELECT 1
                  FROM fact_evidence AS evidence
                  JOIN source_revisions AS revision
                    ON revision.revision_id=evidence.source_revision_id
                  WHERE evidence.fact_id=fact.fact_id
                    AND evidence.evidence_uri<>''
                    AND UPPER(revision.freshness_status)='FRESH'
              )
            """
        ).fetchone()[0]
    )
    effective_statuses = ", ".join(
        f"'{value}'" for value in USABLE_EFFECTIVE_STATUSES
    )
    total_effective, usable_effective = core.execute(
        f"""
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN {usable}
                     AND UPPER(effective.resolution_status)
                         IN ({effective_statuses})
                    THEN 1 ELSE 0
                END
            )
        FROM effective_facts AS effective
        JOIN facts AS fact ON fact.fact_id=effective.fact_id
        WHERE fact.current=1
        """
    ).fetchone()
    total_effective = int(total_effective or 0)
    usable_effective = int(usable_effective or 0)
    return {
        "totalFacts": total_facts,
        "semanticFacts": usable_facts,
        "usableValueFacts": usable_facts,
        "usableValueFactRate": _ratio(usable_facts, total_facts),
        "freshEvidenceSemanticFacts": fresh_semantic_facts,
        "semanticFreshEvidenceRate": _ratio(
            fresh_semantic_facts, usable_facts
        ),
        "totalEffectiveFacts": total_effective,
        "usableEffectiveFacts": usable_effective,
        "effectiveUsableValueRate": _ratio(
            usable_effective, total_effective
        ),
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _semantic_projection_metrics(
    core: sqlite3.Connection,
    manifest: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    projection_manifest = _mapping(
        _mapping(manifest.get("counts")).get("domainProjections")
    )
    runs = {
        str(row[0]): {
            "rowCount": int(row[1] or 0),
            "validationStatus": str(row[2] or "").upper(),
        }
        for row in core.execute(
            """
            SELECT projection_name, row_count, validation_status
            FROM projection_runs
            """
        )
    }
    usable = _usable_fact_predicate("fact")
    result: dict[str, dict[str, object]] = {}
    for projection_name, fact_types in DOMAIN_PROJECTIONS.items():
        placeholders = ", ".join("?" for _ in fact_types)
        core_rows, usable_rows, fresh_rows = core.execute(
            f"""
            SELECT
                COUNT(*),
                SUM(CASE WHEN {usable} THEN 1 ELSE 0 END),
                SUM(
                    CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM fact_evidence AS fresh_evidence
                            JOIN source_revisions AS revision
                              ON revision.revision_id=
                                 fresh_evidence.source_revision_id
                            WHERE fresh_evidence.fact_id=fact.fact_id
                              AND fresh_evidence.evidence_uri<>''
                              AND UPPER(revision.freshness_status)='FRESH'
                        )
                        THEN 1 ELSE 0
                    END
                )
            FROM facts AS fact
            WHERE fact.current=1
              AND fact.fact_type IN ({placeholders})
              AND EXISTS (
                  SELECT 1 FROM fact_evidence AS evidence
                  WHERE evidence.fact_id=fact.fact_id
              )
            """,
            fact_types,
        ).fetchone()
        core_rows = int(core_rows or 0)
        usable_rows = int(usable_rows or 0)
        fresh_rows = int(fresh_rows or 0)
        run = runs.get(
            projection_name,
            {"rowCount": 0, "validationStatus": "MISSING"},
        )
        entry = _mapping(projection_manifest.get(projection_name))
        manifest_rows = _nonnegative_int(entry.get("rows"))
        reviewed_rows = _nonnegative_int(entry.get("reviewedRows"))
        review_status = str(entry.get("reviewStatus") or "").upper()
        manifest_validation = str(
            entry.get("validationStatus") or ""
        ).upper()
        run_rows = int(run["rowCount"])
        run_validation = str(run["validationStatus"])
        row_counts_match = core_rows == run_rows == manifest_rows
        ready = (
            run_rows > 0
            and row_counts_match
            and run_validation == "VALID"
            and manifest_validation == "VALID"
            and review_status in REVIEWED_PROJECTION_STATUSES
            and 0 < reviewed_rows <= run_rows
            and usable_rows == run_rows
            and fresh_rows == run_rows
        )
        result[projection_name] = {
            "rows": run_rows,
            "coreRows": core_rows,
            "manifestRows": manifest_rows,
            "rowCountsMatch": row_counts_match,
            "reviewedRows": reviewed_rows,
            "reviewStatus": review_status or "MISSING",
            "usableRows": usable_rows,
            "freshEvidenceRows": fresh_rows,
            "freshEvidenceRate": _ratio(fresh_rows, run_rows),
            "validationStatus": run_validation,
            "manifestValidationStatus": (
                manifest_validation or "MISSING"
            ),
            "ready": ready,
        }
    return result


def semantic_quality_gates(
    core: sqlite3.Connection,
    manifest: Mapping[str, object],
) -> list[dict[str, object]]:
    facts = _semantic_fact_metrics(core)
    semantic_facts = int(facts["semanticFacts"])
    gates = [
        _gate(
            "facts.semantic_nonzero",
            "facts",
            target=">0",
            actual=semantic_facts,
            passed=semantic_facts > 0,
            detail=(
                "Current usable semantic facts; "
                "identity and fingerprints excluded."
            ),
        ),
        _gate(
            "facts.usable_value_rate",
            "facts",
            target=">0",
            actual=facts["usableValueFactRate"],
            passed=float(facts["usableValueFactRate"]) > 0.0,
            detail=(
                f"{facts['usableValueFacts']}/{facts['totalFacts']} current "
                "facts have usable typed values."
            ),
        ),
        _gate(
            "facts.effective_usable_value_rate",
            "facts",
            target=">0",
            actual=facts["effectiveUsableValueRate"],
            passed=float(facts["effectiveUsableValueRate"]) > 0.0,
            detail=(
                f"{facts['usableEffectiveFacts']}/"
                f"{facts['totalEffectiveFacts']} effective facts resolve "
                "usable typed values."
            ),
        ),
        _gate(
            "facts.semantic_fresh_evidence",
            "facts",
            target="100%",
            actual=facts["semanticFreshEvidenceRate"],
            passed=(
                semantic_facts > 0
                and int(facts["freshEvidenceSemanticFacts"])
                == semantic_facts
            ),
            detail=(
                f"{facts['freshEvidenceSemanticFacts']}/{semantic_facts} "
                "semantic facts have fresh evidence."
            ),
        ),
    ]
    projection_metrics = _semantic_projection_metrics(core, manifest)
    for projection_name in DOMAIN_PROJECTIONS:
        metrics = projection_metrics[projection_name]
        gates.append(
            _gate(
                f"projections.{projection_name}.semantic_ready",
                "projections",
                target=(
                    "reviewed rows >0; all rows usable; "
                    "fresh evidence=100%; validation=VALID"
                ),
                actual=metrics,
                passed=bool(metrics["ready"]),
                detail=(
                    f"{metrics['reviewedRows']} reviewed, "
                    f"{metrics['usableRows']} usable, "
                    f"{metrics['freshEvidenceRows']}/{metrics['rows']} "
                    "fresh rows."
                ),
            )
        )
    return gates
