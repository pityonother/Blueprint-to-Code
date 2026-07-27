"""Database-first query planning with explicit, bounded evidence gaps."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from .schema_capabilities import supports_effective_candidate_explanations


GAP_CODES = {
    "NO_ENTITY_MATCH",
    "AMBIGUOUS_ENTITY",
    "MISSING_FACT",
    "STALE_SOURCE",
    "PARENT_CHAIN_OPEN",
    "REFERENCE_CLOSURE_OPEN",
    "NATIVE_BOUNDARY_UNRESOLVED",
    "RUNTIME_DYNAMIC_BRANCH",
    "MAP_USAGE_INCOMPLETE",
    "UNSUPPORTED_SERIALIZATION",
    "SCHEMA_MIGRATION_REQUIRED",
}
COMPLETE_STATUSES = {
    "CONFIRMED",
    "VERIFIED",
    "RESOLVED",
    "CONFIRMED_EMPTY",
}
OPEN_STATUSES = {
    "UNKNOWN",
    "AMBIGUOUS",
    "NOT_RECOVERED",
    "SOURCE_NOT_AVAILABLE",
    "LEGACY_UNVERIFIED",
    "CONFIRMED_FINGERPRINT_ONLY",
}
MAX_EFFECTIVE_CANDIDATES_PER_FACT = 8
CANDIDATE_EXPLANATION_AVAILABLE = "AVAILABLE"
CANDIDATE_EXPLANATION_SCHEMA_MIGRATION_REQUIRED = (
    "SCHEMA_MIGRATION_REQUIRED"
)


@dataclass(frozen=True)
class QueryRequirements:
    entity_query: str
    fact_types: tuple[str, ...] = ()
    fact_names: tuple[str, ...] = ()
    edge_types: tuple[str, ...] = ()
    requires_native: bool = False
    requires_runtime: bool = False
    requires_map_evidence: bool = False
    evidence_limit: int = 50


def _bounded_limit(value: int, *, minimum: int = 1, maximum: int = 200) -> int:
    return max(minimum, min(maximum, int(value)))


def _entity_projection(row: sqlite3.Row) -> dict[str, object]:
    return {
        "entityId": int(row["entity_id"]),
        "canonicalUri": str(row["canonical_uri"]),
        "entityKind": str(row["entity_kind"]),
        "displayName": str(row["display_name"] or ""),
        "internalName": str(row["internal_name"] or ""),
        "status": str(row["status"]),
        "confidence": str(row["confidence"]),
    }


def resolve_entities(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Resolve exact identities first, then bounded alias/name candidates."""

    query = query.strip()
    if not query:
        return []
    connection.row_factory = sqlite3.Row
    limit = _bounded_limit(limit, maximum=50)
    canonical = connection.execute(
        """
        SELECT
            entity_id, canonical_uri, entity_kind, display_name,
            internal_name, status, confidence
        FROM entities
        WHERE canonical_uri=?
        LIMIT 1
        """,
        (query,),
    ).fetchone()
    if canonical is not None:
        return [_entity_projection(canonical)]
    exact = list(
        connection.execute(
            """
            SELECT DISTINCT
                entity.entity_id, entity.canonical_uri,
                entity.entity_kind, entity.display_name,
                entity.internal_name, entity.status, entity.confidence
            FROM entities AS entity
            LEFT JOIN aliases AS alias ON alias.entity_id=entity.entity_id
            WHERE lower(COALESCE(entity.display_name, ''))=lower(?)
               OR lower(COALESCE(entity.internal_name, ''))=lower(?)
               OR lower(COALESCE(alias.alias, ''))=lower(?)
            ORDER BY
                entity.entity_id
            LIMIT ?
            """,
            (query, query, query, limit),
        )
    )
    if exact:
        return [_entity_projection(row) for row in exact]
    escaped = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    candidates = list(
        connection.execute(
            """
            SELECT DISTINCT
                entity.entity_id, entity.canonical_uri,
                entity.entity_kind, entity.display_name,
                entity.internal_name, entity.status, entity.confidence
            FROM entities AS entity
            LEFT JOIN aliases AS alias ON alias.entity_id=entity.entity_id
            WHERE entity.canonical_uri LIKE '%' || ? || '%' ESCAPE '\\'
               OR COALESCE(entity.display_name, '') LIKE '%' || ? || '%' ESCAPE '\\'
               OR COALESCE(entity.internal_name, '') LIKE '%' || ? || '%' ESCAPE '\\'
               OR COALESCE(alias.alias, '') LIKE '%' || ? || '%' ESCAPE '\\'
            ORDER BY entity.entity_id
            LIMIT ?
            """,
            (escaped, escaped, escaped, escaped, limit),
        )
    )
    return [_entity_projection(row) for row in candidates]


def _fact_rows(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    fact_type: str,
    fact_names: tuple[str, ...],
    limit: int,
) -> list[dict[str, object]]:
    connection.row_factory = sqlite3.Row
    parameters: list[object] = [entity_id]
    fact_name_filter = ""
    effective_name_filter = ""
    if fact_names:
        placeholders = ",".join("?" for _ in fact_names)
        fact_name_filter = f" AND f.fact_name IN ({placeholders})"
        effective_name_filter = (
            f" AND effective.fact_name IN ({placeholders})"
        )
        parameters.extend(fact_names)
    parameters.append(limit)
    if fact_type == "EFFECTIVE_DEFAULT":
        rows = connection.execute(
            f"""
            SELECT
                effective.fact_id, effective.fact_type, effective.fact_name,
                f.value_kind, f.value_text, f.value_number,
                f.value_integer, f.value_json, f.unit, f.status,
                f.confidence, effective.resolution_status,
                effective.inherited_from_entity_id,
                effective.resolution_chain_json
            FROM effective_facts AS effective
            LEFT JOIN facts AS f
              ON f.fact_id=effective.fact_id
             AND f.current=1
            WHERE effective.entity_id=?
              {effective_name_filter}
            ORDER BY effective.fact_name
            LIMIT ?
            """,
            parameters,
        )
    else:
        parameters.insert(1, fact_type)
        rows = connection.execute(
            f"""
            SELECT
                f.fact_id, f.fact_type, f.fact_name, f.value_kind,
                f.value_text, f.value_number, f.value_integer,
                f.value_json, f.unit, f.status, f.confidence,
                '' AS resolution_status,
                NULL AS inherited_from_entity_id,
                '{{}}' AS resolution_chain_json
            FROM facts AS f
            WHERE f.subject_entity_id=? AND f.fact_type=?
              AND f.current=1
              {fact_name_filter}
            ORDER BY f.fact_name, f.fact_id
            LIMIT ?
            """,
            parameters,
        )
    return [
        {
            "factId": (
                int(row["fact_id"])
                if row["fact_id"] is not None
                else None
            ),
            "factType": str(row["fact_type"]),
            "factName": str(row["fact_name"]),
            "valueKind": (
                str(row["value_kind"])
                if row["value_kind"] is not None
                else None
            ),
            "valueText": row["value_text"],
            "valueNumber": row["value_number"],
            "valueInteger": row["value_integer"],
            "valueJson": row["value_json"],
            "unit": (
                str(row["unit"]) if row["unit"] is not None else None
            ),
            "status": (
                str(row["status"]) if row["status"] is not None else None
            ),
            "confidence": (
                str(row["confidence"])
                if row["confidence"] is not None
                else None
            ),
            "resolutionStatus": str(row["resolution_status"]),
            "inheritedFromEntityId": row["inherited_from_entity_id"],
            "resolutionChain": json.loads(
                str(row["resolution_chain_json"] or "{}")
            ),
        }
        for row in rows
    ]


def load_effective_candidate_explanations(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    fact_names: Iterable[str],
    per_fact_limit: int = MAX_EFFECTIVE_CANDIDATES_PER_FACT,
) -> dict[str, dict[str, object]]:
    """Load selected/rejected effective candidates with a hard per-fact cap."""

    names = tuple(
        dict.fromkeys(
            str(fact_name)
            for fact_name in fact_names
            if str(fact_name)
        )
    )
    if not names:
        return {}
    limit = _bounded_limit(
        per_fact_limit,
        maximum=MAX_EFFECTIVE_CANDIDATES_PER_FACT,
    )
    explanations: dict[str, dict[str, object]] = {
        fact_name: {
            "candidates": [],
            "candidateTotal": 0,
            "candidateReturned": 0,
            "candidateOmitted": 0,
            "candidateExplanationStatus": (
                CANDIDATE_EXPLANATION_AVAILABLE
            ),
        }
        for fact_name in names
    }
    if not supports_effective_candidate_explanations(connection):
        for explanation in explanations.values():
            explanation["candidateExplanationStatus"] = (
                CANDIDATE_EXPLANATION_SCHEMA_MIGRATION_REQUIRED
            )
        return explanations
    placeholders = ",".join("?" for _ in names)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"""
        WITH ranked_candidates AS (
            SELECT
                candidate.fact_name,
                candidate.candidate_fact_id,
                candidate.declared_on_entity_id,
                owner.canonical_uri AS declared_on_uri,
                candidate.inheritance_depth,
                candidate.path_status,
                candidate.selected,
                candidate.rejection_reason,
                fact.value_kind,
                fact.value_text,
                fact.value_number,
                fact.value_integer,
                fact.value_json,
                fact.unit,
                fact.status,
                fact.confidence,
                COUNT(*) OVER (
                    PARTITION BY candidate.fact_name
                ) AS candidate_total,
                ROW_NUMBER() OVER (
                    PARTITION BY candidate.fact_name
                    ORDER BY
                        candidate.selected DESC,
                        candidate.inheritance_depth,
                        candidate.declared_on_entity_id,
                        candidate.candidate_fact_id
                ) AS candidate_rank
            FROM effective_fact_candidates AS candidate
            JOIN entities AS owner
              ON owner.entity_id=candidate.declared_on_entity_id
            JOIN facts AS fact
              ON fact.fact_id=candidate.candidate_fact_id
            WHERE candidate.entity_id=?
              AND candidate.fact_type='EFFECTIVE_DEFAULT'
              AND candidate.fact_name IN ({placeholders})
        )
        SELECT *
        FROM ranked_candidates
        WHERE candidate_rank<=?
        ORDER BY fact_name, candidate_rank
        """,
        (entity_id, *names, limit),
    )
    for row in rows:
        fact_name = str(row["fact_name"])
        explanation = explanations[fact_name]
        explanation["candidateTotal"] = int(row["candidate_total"])
        candidates = explanation["candidates"]
        if not isinstance(candidates, list):
            raise AssertionError("Candidate explanation must be a list")
        candidates.append(
            {
                "candidateFactId": int(row["candidate_fact_id"]),
                "declaredOnEntityId": int(
                    row["declared_on_entity_id"]
                ),
                "declaredOnUri": str(row["declared_on_uri"]),
                "inheritanceDepth": int(row["inheritance_depth"]),
                "pathStatus": str(row["path_status"]),
                "selected": bool(row["selected"]),
                "rejectionReason": str(row["rejection_reason"]),
                "valueKind": str(row["value_kind"]),
                "valueText": row["value_text"],
                "valueNumber": row["value_number"],
                "valueInteger": row["value_integer"],
                "valueJson": row["value_json"],
                "unit": str(row["unit"]),
                "status": str(row["status"]),
                "confidence": str(row["confidence"]),
            }
        )
    for explanation in explanations.values():
        candidates = explanation["candidates"]
        if not isinstance(candidates, list):
            raise AssertionError("Candidate explanation must be a list")
        total = int(explanation["candidateTotal"])
        explanation["candidateReturned"] = len(candidates)
        explanation["candidateOmitted"] = max(0, total - len(candidates))
    return explanations


def _fact_evidence(
    connection: sqlite3.Connection,
    fact_ids: Iterable[int | None],
    *,
    limit: int,
) -> tuple[list[dict[str, object]], int]:
    values = tuple(
        sorted({int(value) for value in fact_ids if value is not None})
    )
    if not values:
        return [], 0
    placeholders = ",".join("?" for _ in values)
    total = int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM fact_evidence
            WHERE fact_id IN ({placeholders})
            """,
            values,
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"""
        SELECT
            evidence.fact_id, evidence.evidence_uri,
            evidence.evidence_role, revision.revision_id,
            revision.source_kind, revision.freshness_status
        FROM fact_evidence AS evidence
        JOIN source_revisions AS revision
          ON revision.revision_id=evidence.source_revision_id
        WHERE evidence.fact_id IN ({placeholders})
        ORDER BY
            CASE
                WHEN UPPER(revision.freshness_status)='FRESH' THEN 0
                ELSE 1
            END,
            evidence.fact_id,
            evidence.evidence_uri
        LIMIT ?
        """,
        (*values, limit),
    )
    return (
        [
            {
                "factId": int(row[0]),
                "evidenceUri": str(row[1]),
                "evidenceRole": str(row[2]),
                "sourceRevisionId": int(row[3]),
                "sourceKind": str(row[4]),
                "freshness": str(row[5]),
            }
            for row in rows
        ],
        total,
    )


def _fact_evidence_freshness(
    connection: sqlite3.Connection,
    fact_ids: Iterable[int | None],
) -> tuple[set[int], set[int]]:
    values = sorted({int(value) for value in fact_ids if value is not None})
    fresh: set[int] = set()
    evidenced: set[int] = set()
    for offset in range(0, len(values), 900):
        batch = values[offset : offset + 900]
        placeholders = ",".join("?" for _ in batch)
        for fact_id, has_fresh in connection.execute(
            f"""
            SELECT
                evidence.fact_id,
                MAX(
                    CASE
                        WHEN evidence.evidence_uri<>''
                         AND UPPER(revision.freshness_status)='FRESH'
                        THEN 1 ELSE 0
                    END
                )
            FROM fact_evidence AS evidence
            JOIN source_revisions AS revision
              ON revision.revision_id=evidence.source_revision_id
            WHERE evidence.fact_id IN ({placeholders})
            GROUP BY evidence.fact_id
            """,
            tuple(batch),
        ):
            normalized_id = int(fact_id)
            evidenced.add(normalized_id)
            if int(has_fresh or 0) == 1:
                fresh.add(normalized_id)
    return fresh, evidenced


def _probe(
    code: str, entity: dict[str, object] | None
) -> dict[str, object]:
    asset = str(entity["canonicalUri"]) if entity else ""
    if code == "NO_ENTITY_MATCH":
        return {
            "probeType": "entity_search",
            "operation": "registry_identity_search",
            "budgetTokens": 500,
            "reason": code,
        }
    if code == "AMBIGUOUS_ENTITY":
        return {
            "probeType": "entity_disambiguation",
            "operation": "choose_canonical_identity",
            "budgetTokens": 500,
            "reason": code,
        }
    if code == "NATIVE_BOUNDARY_UNRESOLVED":
        return {
            "probeType": "native_recipe",
            "target": asset,
            "operation": "bounded_exact_symbol_or_callsite",
            "budgetTokens": 1500,
            "reason": code,
        }
    if code == "RUNTIME_DYNAMIC_BRANCH":
        return {
            "probeType": "runtime_probe",
            "asset": asset,
            "operation": "observe_named_branch",
            "budgetTokens": 1000,
            "reason": code,
        }
    if code == "MAP_USAGE_INCOMPLETE":
        return {
            "probeType": "map_usage_probe",
            "asset": asset,
            "operation": "direct_pcg_world_partition_usage",
            "budgetTokens": 1200,
            "reason": code,
        }
    if code == "REFERENCE_CLOSURE_OPEN":
        return {
            "probeType": "asset_registry_query",
            "asset": asset,
            "operation": "bounded_neighborhood",
            "budgetTokens": 1000,
            "reason": code,
        }
    if code == "SCHEMA_MIGRATION_REQUIRED":
        return {
            "probeType": "snapshot_rebuild",
            "operation": "rebuild_core_v2_snapshot",
            "budgetTokens": 500,
            "reason": code,
        }
    operation = (
        "inheritance_path"
        if code == "PARENT_CHAIN_OPEN"
        else "named_fact"
    )
    return {
        "probeType": "blueprint_evidence_query",
        "asset": asset,
        "operation": operation,
        "budgetTokens": 1500,
        "reason": code,
    }


def plan_query(
    connection: sqlite3.Connection,
    requirements: QueryRequirements,
) -> dict[str, object]:
    """Plan and answer from Core when every requested evidence gate is closed."""

    evidence_limit = _bounded_limit(requirements.evidence_limit)
    candidates = resolve_entities(connection, requirements.entity_query)
    missing: list[dict[str, str]] = []
    if not candidates:
        missing.append(
            {"code": "NO_ENTITY_MATCH", "requirement": "unique entity"}
        )
        return {
            "route": "EVIDENCE_REQUIRED",
            "entity": None,
            "entityCandidates": [],
            "facts": [],
            "relationships": [],
            "evidence": [],
            "returned": 0,
            "omitted": 0,
            "freshness": "UNKNOWN",
            "missingRequirements": missing,
            "recommendedProbes": [_probe("NO_ENTITY_MATCH", None)],
        }
    if len(candidates) > 1:
        missing.append(
            {"code": "AMBIGUOUS_ENTITY", "requirement": "unique entity"}
        )
        return {
            "route": "EVIDENCE_REQUIRED",
            "entity": None,
            "entityCandidates": candidates,
            "facts": [],
            "relationships": [],
            "evidence": [],
            "returned": len(candidates),
            "omitted": 0,
            "freshness": "UNKNOWN",
            "missingRequirements": missing,
            "recommendedProbes": [_probe("AMBIGUOUS_ENTITY", None)],
        }
    entity = candidates[0]
    entity_id = int(entity["entityId"])
    facts: list[dict[str, object]] = []
    for fact_type in requirements.fact_types:
        normalized_type = fact_type.upper()
        matched = _fact_rows(
            connection,
            entity_id=entity_id,
            fact_type=normalized_type,
            fact_names=requirements.fact_names,
            limit=evidence_limit,
        )
        facts.extend(matched)
        if not matched:
            missing.append(
                {
                    "code": "MISSING_FACT",
                    "requirement": normalized_type,
                }
            )
            continue
        for fact in matched:
            status = str(fact["status"] or "").upper()
            resolution = str(fact["resolutionStatus"] or "").upper()
            if resolution == "PARENT_CHAIN_OPEN":
                missing.append(
                    {
                        "code": "PARENT_CHAIN_OPEN",
                        "requirement": (
                            f"{normalized_type}:{fact['factName']}"
                        ),
                    }
                )
            elif status == "STALE" or resolution == "STALE":
                missing.append(
                    {
                        "code": "STALE_SOURCE",
                        "requirement": (
                            f"{normalized_type}:{fact['factName']}"
                        ),
                    }
                )
            elif (
                status not in COMPLETE_STATUSES
                or resolution in OPEN_STATUSES
                or resolution == "AMBIGUOUS_INHERITANCE"
            ):
                missing.append(
                    {
                        "code": "MISSING_FACT",
                        "requirement": (
                            f"{normalized_type}:{fact['factName']}"
                        ),
                    }
                )
    effective_facts = [
        fact
        for fact in facts
        if fact["factType"] == "EFFECTIVE_DEFAULT"
    ]
    candidate_explanations = load_effective_candidate_explanations(
        connection,
        entity_id=entity_id,
        fact_names=(
            str(fact["factName"]) for fact in effective_facts
        ),
    )
    for fact in effective_facts:
        fact.update(
            candidate_explanations[str(fact["factName"])]
        )
    if any(
        fact.get("candidateExplanationStatus")
        == CANDIDATE_EXPLANATION_SCHEMA_MIGRATION_REQUIRED
        for fact in effective_facts
    ):
        missing.append(
            {
                "code": "SCHEMA_MIGRATION_REQUIRED",
                "requirement": "Core v2 effective candidate lineage",
            }
        )
    if "EFFECTIVE_DEFAULT" in {
        value.upper() for value in requirements.fact_types
    }:
        open_chain = connection.execute(
            """
            SELECT 1
            FROM asset_class_assignments AS assignment
            JOIN class_gaps AS gap ON gap.class_id=assignment.class_id
            WHERE assignment.entity_id=?
              AND assignment.assignment_kind='GENERATED_CLASS'
              AND gap.gap_kind IN (
                'NATIVE_ROOT_NOT_REACHED',
                'INHERITANCE_CYCLE',
                'MULTIPLE_PARENT_CANDIDATES'
              )
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        if open_chain:
            missing.append(
                {
                    "code": "PARENT_CHAIN_OPEN",
                    "requirement": "effective default inheritance",
                }
            )
    relationships: list[dict[str, object]] = []
    for edge_type in requirements.edge_types:
        rows = list(
            connection.execute(
                """
                SELECT
                    edge.edge_id, edge.edge_type, edge.edge_strength,
                    edge.status, edge.confidence, target.entity_id,
                    target.canonical_uri, edge.evidence_uri
                FROM edges AS edge
                JOIN entities AS target
                  ON target.entity_id=edge.target_entity_id
                WHERE edge.source_entity_id=? AND edge.edge_type=?
                ORDER BY edge.edge_id
                LIMIT ?
                """,
                (entity_id, edge_type, evidence_limit),
            )
        )
        relationships.extend(
            {
                "edgeId": int(row[0]),
                "edgeType": str(row[1]),
                "edgeStrength": str(row[2]),
                "status": str(row[3]),
                "confidence": str(row[4]),
                "targetEntityId": int(row[5]),
                "targetUri": str(row[6]),
                "evidenceUri": str(row[7]),
            }
            for row in rows
        )
        if not rows:
            missing.append(
                {
                    "code": "REFERENCE_CLOSURE_OPEN",
                    "requirement": edge_type,
                }
            )
    if requirements.requires_native:
        native = connection.execute(
            """
            SELECT 1 FROM native_blueprint_links AS link
            JOIN native_functions AS function
              ON function.native_function_id=link.native_function_id
            WHERE link.blueprint_entity_id=?
              AND link.status='CONFIRMED'
              AND function.status='CONFIRMED'
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        if not native:
            missing.append(
                {
                    "code": "NATIVE_BOUNDARY_UNRESOLVED",
                    "requirement": "confirmed Blueprint-native callsite",
                }
            )
    if requirements.requires_runtime:
        runtime = connection.execute(
            """
            SELECT 1 FROM facts
            WHERE subject_entity_id=?
              AND fact_type='RUNTIME_OBSERVATION'
              AND status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
              AND current=1
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        if not runtime:
            missing.append(
                {
                    "code": "RUNTIME_DYNAMIC_BRANCH",
                    "requirement": "runtime observation",
                }
            )
    if requirements.requires_map_evidence:
        map_evidence = connection.execute(
            """
            SELECT 1 FROM domain_memberships
            WHERE entity_id=?
              AND domain_id IN ('map_world', 'pcg_world_partition')
              AND status='CONFIRMED'
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        if not map_evidence:
            missing.append(
                {
                    "code": "MAP_USAGE_INCOMPLETE",
                    "requirement": "direct map or PCG usage",
                }
            )
    evidence, evidence_total = _fact_evidence(
        connection,
        (fact["factId"] for fact in facts),
        limit=evidence_limit,
    )
    returned_fact_ids = {
        int(fact_id)
        for fact in facts
        if (fact_id := fact["factId"]) is not None
    }
    fresh_fact_ids, evidenced_fact_ids = _fact_evidence_freshness(
        connection,
        returned_fact_ids,
    )
    missing_current_fact = any(
        fact["factId"] is not None
        and (fact["valueKind"] is None or fact["status"] is None)
        for fact in facts
    )
    if returned_fact_ids - fresh_fact_ids:
        missing.append(
            {
                "code": "STALE_SOURCE",
                "requirement": "one fresh evidence revision per returned fact",
            }
        )
    missing = [
        dict(item)
        for item in {
            (item["code"], item["requirement"]): item
            for item in missing
        }.values()
    ]
    missing.sort(key=lambda item: (item["code"], item["requirement"]))
    gap_codes = [item["code"] for item in missing]
    if any(code not in GAP_CODES for code in gap_codes):
        raise AssertionError("Planner emitted an unknown gap code")
    probes = [_probe(code, entity) for code in sorted(set(gap_codes))]
    if (
        returned_fact_ids
        and returned_fact_ids <= fresh_fact_ids
        and not missing_current_fact
    ):
        freshness = "FRESH"
    elif (returned_fact_ids - fresh_fact_ids) & evidenced_fact_ids:
        freshness = "STALE"
    else:
        freshness = "UNKNOWN"
    return {
        "route": (
            "DB_ONLY_COMPLETE" if not missing else "EVIDENCE_REQUIRED"
        ),
        "entity": entity,
        "entityCandidates": candidates,
        "facts": facts,
        "relationships": relationships,
        "evidence": evidence,
        "returned": len(facts) + len(relationships) + len(evidence),
        "omitted": max(0, evidence_total - len(evidence)),
        "freshness": freshness,
        "missingRequirements": missing,
        "recommendedProbes": probes,
    }
