"""Fail-closed semantic-content gates for ARK Knowledge Base vNext."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Mapping

from .projections import (
    ACTIVE_PROMOTED_DERIVATION_PREDICATE,
    ADAPTER_OWNED_SEMANTIC_FACT_PREDICATE,
    DOMAIN_PROJECTIONS,
    PROJECTION_SCHEMA_VERSION,
    _value_matches_review,
    compute_core_projection_content_digest,
    compute_projection_artifact_content_digest,
    load_projection_review_contract,
)


USABLE_FACT_STATUSES = (
    "CONFIRMED",
    "VERIFIED",
    "RESOLVED",
    "CONFIRMED_EMPTY",
)
USABLE_EFFECTIVE_STATUSES = (
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


def typed_usable_fact_predicate(alias: str) -> str:
    regular_statuses = ", ".join(
        f"'{value}'"
        for value in USABLE_FACT_STATUSES
        if value != "CONFIRMED_EMPTY"
    )
    return f"""
        (
            (
                UPPER({alias}.status)='CONFIRMED_EMPTY'
                AND UPPER({alias}.value_kind)='CONFIRMED_EMPTY'
                AND {alias}.value_text IS NULL
                AND {alias}.value_number IS NULL
                AND {alias}.value_integer IS NULL
                AND {alias}.value_json IS NULL
            )
            OR (
                UPPER({alias}.status) IN ({regular_statuses})
                AND (
                    (
                        UPPER({alias}.value_kind)='BOOLEAN'
                        AND TYPEOF({alias}.value_integer)='integer'
                        AND {alias}.value_integer IN (0, 1)
                        AND {alias}.value_text IS NULL
                        AND {alias}.value_number IS NULL
                        AND {alias}.value_json IS NULL
                    )
                    OR (
                        UPPER({alias}.value_kind)='INTEGER'
                        AND TYPEOF({alias}.value_integer)='integer'
                        AND {alias}.value_text IS NULL
                        AND {alias}.value_number IS NULL
                        AND {alias}.value_json IS NULL
                    )
                    OR (
                        UPPER({alias}.value_kind)='NUMBER'
                        AND TYPEOF({alias}.value_number)
                            IN ('integer', 'real')
                        AND ABS({alias}.value_number)
                            <=1.7976931348623157e308
                        AND {alias}.value_text IS NULL
                        AND {alias}.value_integer IS NULL
                        AND {alias}.value_json IS NULL
                    )
                    OR (
                        UPPER({alias}.value_kind)='TEXT'
                        AND TYPEOF({alias}.value_text)='text'
                        AND {alias}.value_number IS NULL
                        AND {alias}.value_integer IS NULL
                        AND {alias}.value_json IS NULL
                    )
                    OR (
                        UPPER({alias}.value_kind)='ENTITY_REF'
                        AND TYPEOF({alias}.value_text)='text'
                        AND SUBSTR({alias}.value_text, 1, 1)='/'
                        AND {alias}.value_number IS NULL
                        AND {alias}.value_integer IS NULL
                        AND {alias}.value_json IS NULL
                    )
                    OR (
                        UPPER({alias}.value_kind)='JSON'
                        AND TYPEOF({alias}.value_json)='text'
                        AND JSON_VALID({alias}.value_json)=1
                        AND {alias}.value_text IS NULL
                        AND {alias}.value_number IS NULL
                        AND {alias}.value_integer IS NULL
                    )
                )
            )
        )
    """


def _semantic_fact_metrics(
    core: sqlite3.Connection,
) -> dict[str, int | float]:
    usable = typed_usable_fact_predicate("fact")
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
    semantic_facts = int(
        core.execute(
            f"""
            SELECT COUNT(*)
            FROM facts AS fact
            WHERE fact.current=1
              AND {usable}
              AND {ADAPTER_OWNED_SEMANTIC_FACT_PREDICATE}
            """
        ).fetchone()[0]
    )
    fresh_semantic_facts = int(
        core.execute(
            f"""
            SELECT COUNT(*)
            FROM facts AS fact
            WHERE fact.current=1
              AND {usable}
              AND {ACTIVE_PROMOTED_DERIVATION_PREDICATE}
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
                    WHEN fact.current=1
                     AND {usable}
                     AND UPPER(effective.resolution_status)
                         IN ({effective_statuses})
                    THEN 1 ELSE 0
                END
            )
        FROM effective_facts AS effective
        LEFT JOIN facts AS fact ON fact.fact_id=effective.fact_id
        """
    ).fetchone()
    total_effective = int(total_effective or 0)
    usable_effective = int(usable_effective or 0)
    return {
        "totalFacts": total_facts,
        "semanticFacts": semantic_facts,
        "usableValueFacts": usable_facts,
        "usableValueFactRate": _ratio(usable_facts, total_facts),
        "freshEvidenceSemanticFacts": fresh_semantic_facts,
        "semanticFreshEvidenceRate": _ratio(
            fresh_semantic_facts, semantic_facts
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _review_contract_matches(
    projection: sqlite3.Connection,
    *,
    expected_review_version: str,
    expected_reviews: list[dict[str, object]],
) -> bool:
    projection.row_factory = sqlite3.Row
    rows = list(
        projection.execute(
            """
            SELECT
                review.review_id,
                review.fact_id,
                review.review_status,
                review.evidence_uri,
                review.review_version,
                row.canonical_uri,
                row.fact_type,
                row.fact_name,
                row.value_kind,
                row.value_text,
                row.value_number,
                row.value_integer,
                row.value_json
            FROM projection_reviews AS review
            JOIN projection_rows AS row
              ON row.fact_id=review.fact_id
            ORDER BY review.review_id
            """
        )
    )
    expected_by_id = {
        str(review["reviewId"]): review
        for review in expected_reviews
    }
    if len(rows) != len(expected_by_id):
        return False
    for row in rows:
        expected = expected_by_id.get(str(row["review_id"]))
        if expected is None:
            return False
        if (
            str(row["review_status"]) != "FIXTURE_EXACT"
            or str(row["review_version"]) != expected_review_version
            or str(row["evidence_uri"])
            != str(expected.get("evidenceUri") or "")
            or str(row["canonical_uri"])
            != str(expected.get("canonicalUri") or "")
            or str(row["fact_type"])
            != str(expected.get("factType") or "")
            or str(row["fact_name"])
            != str(expected.get("factName") or "")
            or not _value_matches_review(row, expected)
        ):
            return False
        evidence_exists = projection.execute(
            """
            SELECT 1
            FROM projection_evidence
            WHERE fact_id=?
              AND evidence_uri=?
              AND UPPER(freshness_status)='FRESH'
            LIMIT 1
            """,
            (int(row["fact_id"]), str(row["evidence_uri"])),
        ).fetchone()
        if evidence_exists is None:
            return False
    return True


def _projection_artifact_metrics(
    *,
    snapshot_root: Path,
    projection_name: str,
    entry: Mapping[str, object],
    run: Mapping[str, object],
    core_ontology_version: str,
    manifest_ontology_version: str,
    expected_ontology_version: str,
    expected_content_digest: str,
    expected_review_version: str,
    expected_review_config_sha256: str,
    expected_reviews: list[dict[str, object]],
) -> dict[str, object]:
    expected_name = f"{projection_name}.sqlite"
    manifest_path = str(entry.get("path") or "")
    path = snapshot_root / "domain_exports" / expected_name
    result: dict[str, object] = {
        "path": f"domain_exports/{expected_name}",
        "pathMatches": manifest_path == expected_name,
        "exists": path.is_file(),
        "bytes": 0,
        "sha256": "",
        "digestMatches": False,
        "integrity": "missing",
        "foreignKeyViolations": -1,
        "schemaVersion": "",
        "projectionVersion": "",
        "projectionName": "",
        "sourceRevisionSetHash": "",
        "ontologyVersion": "",
        "declaredContentDigest": "",
        "contentDigest": "",
        "expectedContentDigest": expected_content_digest,
        "contentDigestMatches": False,
        "reviewVersion": "",
        "reviewConfigSha256": "",
        "reviewContractMatches": False,
        "tableCounts": {},
        "tableCountsMatch": False,
        "completeRows": 0,
        "partialRows": 0,
        "unspecifiedRows": 0,
        "freshEvidenceRows": 0,
        "lineageRows": 0,
        "reviewedRows": 0,
        "verified": False,
        "error": "",
    }
    if not path.is_file():
        result["error"] = "MISSING_ARTIFACT"
        return result
    try:
        result["bytes"] = path.stat().st_size
        result["sha256"] = _sha256_file(path)
        result["digestMatches"] = (
            int(result["bytes"]) == _nonnegative_int(entry.get("bytes"))
            and str(result["sha256"]) == str(entry.get("sha256") or "")
        )
        projection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        try:
            result["integrity"] = str(
                projection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            result["foreignKeyViolations"] = len(
                list(projection.execute("PRAGMA foreign_key_check"))
            )
            metadata = {
                str(key): str(value)
                for key, value in projection.execute(
                    "SELECT key, value FROM metadata"
                )
            }
            result["schemaVersion"] = metadata.get("schema_version", "")
            result["projectionVersion"] = metadata.get(
                "projection_version",
                "",
            )
            result["projectionName"] = metadata.get("projection_name", "")
            result["sourceRevisionSetHash"] = metadata.get(
                "source_revision_set_hash",
                "",
            )
            result["ontologyVersion"] = metadata.get("ontology_version", "")
            result["declaredContentDigest"] = metadata.get(
                "content_digest",
                "",
            )
            result["reviewVersion"] = metadata.get(
                "review_version",
                "",
            )
            result["reviewConfigSha256"] = metadata.get(
                "review_config_sha256",
                "",
            )
            result["contentDigest"] = (
                compute_projection_artifact_content_digest(projection)
            )
            result["contentDigestMatches"] = (
                bool(expected_content_digest)
                and str(result["contentDigest"])
                == str(result["declaredContentDigest"])
                == str(entry.get("contentDigest") or "")
                == expected_content_digest
            )
            result["reviewContractMatches"] = (
                bool(expected_review_version)
                and bool(expected_review_config_sha256)
                and _review_contract_matches(
                    projection,
                    expected_review_version=expected_review_version,
                    expected_reviews=expected_reviews,
                )
            )
            table_counts = {
                table: int(
                    projection.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                )
                for table in (
                    "metadata",
                    "projection_evidence",
                    "projection_lineage",
                    "projection_reviews",
                    "projection_rows",
                )
            }
            result["tableCounts"] = table_counts
            expected_counts = _mapping(entry.get("tableCounts"))
            result["tableCountsMatch"] = all(
                table_counts[table]
                == _nonnegative_int(expected_counts.get(table))
                for table in table_counts
            )
            (
                complete_rows,
                partial_rows,
                unspecified_rows,
            ) = projection.execute(
                """
                SELECT
                    SUM(completeness_status='COMPLETE'),
                    SUM(completeness_status='PARTIAL'),
                    SUM(completeness_status NOT IN ('COMPLETE', 'PARTIAL'))
                FROM projection_rows
                """
            ).fetchone()
            result["completeRows"] = int(complete_rows or 0)
            result["partialRows"] = int(partial_rows or 0)
            result["unspecifiedRows"] = int(unspecified_rows or 0)
            result["freshEvidenceRows"] = int(
                projection.execute(
                    """
                    SELECT COUNT(*)
                    FROM projection_rows AS row
                    WHERE EXISTS (
                        SELECT 1
                        FROM projection_evidence AS evidence
                        WHERE evidence.fact_id=row.fact_id
                          AND UPPER(evidence.freshness_status)='FRESH'
                    )
                    """
                ).fetchone()[0]
            )
            result["lineageRows"] = int(
                projection.execute(
                    """
                    SELECT COUNT(*)
                    FROM projection_rows AS row
                    WHERE EXISTS (
                        SELECT 1
                        FROM projection_lineage AS lineage
                        WHERE lineage.fact_id=row.fact_id
                          AND lineage.reason_code IN (
                              'VERIFIED', 'VERIFIED_PARTIAL'
                          )
                    )
                    """
                ).fetchone()[0]
            )
            result["reviewedRows"] = int(
                projection.execute(
                    """
                    SELECT COUNT(DISTINCT fact_id)
                    FROM projection_reviews
                    WHERE UPPER(review_status) IN (
                        'HUMAN_REVIEWED', 'EMPIRICAL', 'FIXTURE_EXACT'
                    )
                    """
                ).fetchone()[0]
            )
        finally:
            projection.close()
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as error:
        result["error"] = f"{type(error).__name__}:{error}"
        return result

    rows = _nonnegative_int(entry.get("rows"))
    run_rows = _nonnegative_int(run.get("rowCount"))
    manifest_counts = _mapping(entry.get("tableCounts"))
    result["verified"] = (
        bool(result["pathMatches"])
        and bool(result["digestMatches"])
        and result["integrity"] == "ok"
        and int(result["foreignKeyViolations"]) == 0
        and result["schemaVersion"] == PROJECTION_SCHEMA_VERSION
        and result["projectionVersion"] == "v2"
        and result["projectionName"] == projection_name
        and result["sourceRevisionSetHash"]
        == str(entry.get("sourceRevisionSetHash") or "")
        == str(run.get("sourceRevisionSetHash") or "")
        and result["ontologyVersion"]
        == str(entry.get("ontologyVersion") or "")
        == str(run.get("ontologyVersion") or "")
        == core_ontology_version
        == manifest_ontology_version
        == expected_ontology_version
        and "ark-fact-types/v2" in expected_ontology_version.split("|")
        and bool(result["contentDigestMatches"])
        and result["reviewVersion"]
        == str(entry.get("reviewVersion") or "")
        == expected_review_version
        and result["reviewConfigSha256"]
        == str(entry.get("reviewConfigSha256") or "")
        == expected_review_config_sha256
        and bool(result["reviewContractMatches"])
        and bool(result["tableCountsMatch"])
        and _nonnegative_int(manifest_counts.get("projection_rows"))
        == rows
        == run_rows
        and int(result["completeRows"]) + int(result["partialRows"]) == rows
        and int(result["unspecifiedRows"]) == 0
        and int(result["freshEvidenceRows"]) == rows
        and int(result["lineageRows"]) == rows
        and int(result["reviewedRows"])
        == _nonnegative_int(entry.get("reviewedRows"))
    )
    return result


def _semantic_projection_metrics(
    core: sqlite3.Connection,
    manifest: Mapping[str, object],
    *,
    snapshot_root: Path,
    expected_ontology_version: str,
    review_path: Path | None = None,
) -> dict[str, dict[str, object]]:
    resolved_review_path = (
        review_path
        if review_path is not None
        else snapshot_root / "projection_review.v1.json"
    )
    try:
        (
            expected_review_version,
            expected_review_config,
            expected_review_config_sha256,
        ) = load_projection_review_contract(resolved_review_path)
        review_contract_error = ""
    except (OSError, ValueError) as error:
        expected_review_version = ""
        expected_review_config = {}
        expected_review_config_sha256 = ""
        review_contract_error = type(error).__name__
    review_contract_uri = resolved_review_path.name
    if resolved_review_path.parent.name == "ontology":
        review_contract_uri = (
            f"ontology/{resolved_review_path.name}"
        )
    projection_manifest = _mapping(
        _mapping(manifest.get("counts")).get("domainProjections")
    )
    try:
        core_ontology_row = core.execute(
            """
            SELECT value FROM metadata
            WHERE key='ontology_version'
            """
        ).fetchone()
    except sqlite3.DatabaseError:
        core_ontology_row = None
    core_ontology_version = (
        str(core_ontology_row[0]) if core_ontology_row is not None else ""
    )
    manifest_ontology_version = str(manifest.get("ontologyVersion") or "")
    ontology_versions_match = (
        bool(expected_ontology_version)
        and "ark-fact-types/v2" in expected_ontology_version.split("|")
        and core_ontology_version
        == manifest_ontology_version
        == expected_ontology_version
    )
    runs: dict[str, dict[str, object]] = {
        str(row[0]): {
            "projectionVersion": str(row[1] or ""),
            "sourceRevisionSetHash": str(row[2] or ""),
            "ontologyVersion": str(row[3] or ""),
            "rowCount": int(row[4] or 0),
            "validationStatus": str(row[5] or "").upper(),
        }
        for row in core.execute(
            """
            SELECT projection_name, projection_version,
                   source_revision_set_hash, ontology_version,
                   row_count, validation_status
            FROM projection_runs
            """
        )
    }
    usable = typed_usable_fact_predicate("fact")
    result: dict[str, dict[str, object]] = {}
    for projection_name, fact_types in DOMAIN_PROJECTIONS.items():
        placeholders = ", ".join("?" for _ in fact_types)
        (
            core_rows,
            active_rows,
            usable_rows,
            fresh_rows,
        ) = core.execute(
            f"""
            SELECT
                COUNT(*),
                SUM(
                    CASE
                        WHEN fact.ontology_version=?
                         AND {ACTIVE_PROMOTED_DERIVATION_PREDICATE}
                        THEN 1 ELSE 0
                    END
                ),
                SUM(
                    CASE
                        WHEN fact.ontology_version=?
                         AND {ACTIVE_PROMOTED_DERIVATION_PREDICATE}
                         AND {usable}
                        THEN 1 ELSE 0
                    END
                ),
                SUM(
                    CASE
                        WHEN fact.ontology_version=?
                         AND {ACTIVE_PROMOTED_DERIVATION_PREDICATE}
                         AND {usable}
                         AND EXISTS (
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
            """,
            (
                expected_ontology_version,
                expected_ontology_version,
                expected_ontology_version,
                *fact_types,
            ),
        ).fetchone()
        core_rows = int(core_rows or 0)
        active_rows = int(active_rows or 0)
        usable_rows = int(usable_rows or 0)
        fresh_rows = int(fresh_rows or 0)
        run = runs.get(
            projection_name,
            {
                "projectionVersion": "",
                "sourceRevisionSetHash": "",
                "ontologyVersion": "",
                "rowCount": 0,
                "validationStatus": "MISSING",
            },
        )
        entry = _mapping(projection_manifest.get(projection_name))
        manifest_rows = _nonnegative_int(entry.get("rows"))
        reviewed_rows = _nonnegative_int(entry.get("reviewedRows"))
        review_status = str(entry.get("reviewStatus") or "").upper()
        manifest_validation = str(
            entry.get("validationStatus") or ""
        ).upper()
        manifest_schema = str(entry.get("schemaVersion") or "")
        manifest_projection_version = str(
            entry.get("projectionVersion") or ""
        )
        manifest_lineage_rows = _nonnegative_int(entry.get("lineageRows"))
        manifest_evidence_rows = _nonnegative_int(entry.get("evidenceRows"))
        complete_rows = _nonnegative_int(entry.get("completeRows"))
        partial_rows = _nonnegative_int(entry.get("partialRows"))
        unspecified_rows = _nonnegative_int(entry.get("unspecifiedRows"))
        run_rows = int(run["rowCount"])
        run_validation = str(run["validationStatus"])
        run_projection_version = str(run["projectionVersion"])
        expected_content_digest = (
            compute_core_projection_content_digest(
                core,
                projection_name=projection_name,
                fact_types=fact_types,
                ontology_version=expected_ontology_version,
                review_version=expected_review_version,
                reviews=expected_review_config.get(
                    projection_name,
                    (),
                ),
            )
        )
        artifact = _projection_artifact_metrics(
            snapshot_root=snapshot_root,
            projection_name=projection_name,
            entry=entry,
            run=run,
            core_ontology_version=core_ontology_version,
            manifest_ontology_version=manifest_ontology_version,
            expected_ontology_version=expected_ontology_version,
            expected_content_digest=expected_content_digest,
            expected_review_version=expected_review_version,
            expected_review_config_sha256=(
                expected_review_config_sha256
            ),
            expected_reviews=expected_review_config.get(
                projection_name,
                [],
            ),
        )
        projection_ontology_versions_match = (
            ontology_versions_match
            and str(run["ontologyVersion"]) == expected_ontology_version
            and str(entry.get("ontologyVersion") or "")
            == expected_ontology_version
            and str(artifact["ontologyVersion"])
            == expected_ontology_version
        )
        row_counts_match = (
            core_rows
            == active_rows
            == run_rows
            == manifest_rows
        )
        ready = (
            run_rows > 0
            and row_counts_match
            and run_projection_version == "v2"
            and manifest_projection_version == "v2"
            and manifest_schema == PROJECTION_SCHEMA_VERSION
            and projection_ontology_versions_match
            and run_validation == "VALID"
            and manifest_validation == "VALID"
            and review_status in REVIEWED_PROJECTION_STATUSES
            and 0 < reviewed_rows <= run_rows
            and usable_rows == run_rows
            and fresh_rows == run_rows
            and manifest_evidence_rows >= run_rows
            and manifest_lineage_rows >= run_rows
            and complete_rows + partial_rows == run_rows
            and unspecified_rows == 0
            and bool(artifact["verified"])
        )
        result[projection_name] = {
            "rows": run_rows,
            "coreRows": core_rows,
            "activePromotedRows": active_rows,
            "manifestRows": manifest_rows,
            "rowCountsMatch": row_counts_match,
            "projectionVersion": run_projection_version or "MISSING",
            "manifestProjectionVersion": (
                manifest_projection_version or "MISSING"
            ),
            "manifestSchemaVersion": manifest_schema or "MISSING",
            "ontologyVersionsMatch": projection_ontology_versions_match,
            "coreOntologyVersion": core_ontology_version or "MISSING",
            "manifestOntologyVersion": (
                manifest_ontology_version or "MISSING"
            ),
            "expectedOntologyVersion": (
                expected_ontology_version or "MISSING"
            ),
            "projectionOntologyVersion": (
                str(run["ontologyVersion"]) or "MISSING"
            ),
            "contentDigest": expected_content_digest,
            "manifestContentDigest": (
                str(entry.get("contentDigest") or "") or "MISSING"
            ),
            "reviewContractUri": review_contract_uri,
            "reviewContractError": review_contract_error,
            "reviewConfigSha256": (
                expected_review_config_sha256 or "MISSING"
            ),
            "reviewContractMatches": bool(
                artifact["reviewContractMatches"]
            ),
            "reviewedRows": reviewed_rows,
            "reviewStatus": review_status or "MISSING",
            "usableRows": usable_rows,
            "freshEvidenceRows": fresh_rows,
            "freshEvidenceRate": _ratio(fresh_rows, run_rows),
            "validationStatus": run_validation,
            "manifestValidationStatus": (
                manifest_validation or "MISSING"
            ),
            "lineageRows": manifest_lineage_rows,
            "evidenceRows": manifest_evidence_rows,
            "completeRows": complete_rows,
            "partialRows": partial_rows,
            "unspecifiedRows": unspecified_rows,
            "artifactVerified": bool(artifact["verified"]),
            "artifact": artifact,
            "ready": ready,
        }
    return result


def semantic_quality_gates(
    core: sqlite3.Connection,
    manifest: Mapping[str, object],
    *,
    snapshot_root: Path,
    expected_ontology_version: str,
    review_path: Path | None = None,
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
    projection_metrics = _semantic_projection_metrics(
        core,
        manifest,
        snapshot_root=snapshot_root,
        expected_ontology_version=expected_ontology_version,
        review_path=review_path,
    )
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
