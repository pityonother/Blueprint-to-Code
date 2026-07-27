"""Database-first query planning with explicit, bounded evidence gaps."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable


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
    name_filter = ""
    if fact_names:
        placeholders = ",".join("?" for _ in fact_names)
        name_filter = f" AND f.fact_name IN ({placeholders})"
        parameters.extend(fact_names)
    parameters.append(limit)
    if fact_type == "EFFECTIVE_DEFAULT":
        rows = connection.execute(
            f"""
            SELECT
                f.fact_id, effective.fact_type, effective.fact_name,
                f.value_kind, f.value_text, f.value_number,
                f.value_integer, f.value_json, f.unit, f.status,
                f.confidence, effective.resolution_status,
                effective.inherited_from_entity_id,
                effective.resolution_chain_json
            FROM effective_facts AS effective
            JOIN facts AS f ON f.fact_id=effective.fact_id
            WHERE effective.entity_id=?
              {name_filter}
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
              {name_filter}
            ORDER BY f.fact_name, f.fact_id
            LIMIT ?
            """,
            parameters,
        )
    return [
        {
            "factId": int(row["fact_id"]),
            "factType": str(row["fact_type"]),
            "factName": str(row["fact_name"]),
            "valueKind": str(row["value_kind"]),
            "valueText": row["value_text"],
            "valueNumber": row["value_number"],
            "valueInteger": row["value_integer"],
            "valueJson": row["value_json"],
            "unit": str(row["unit"]),
            "status": str(row["status"]),
            "confidence": str(row["confidence"]),
            "resolutionStatus": str(row["resolution_status"]),
            "inheritedFromEntityId": row["inherited_from_entity_id"],
            "resolutionChain": json.loads(
                str(row["resolution_chain_json"] or "{}")
            ),
        }
        for row in rows
    ]


def _fact_evidence(
    connection: sqlite3.Connection,
    fact_ids: Iterable[int],
    *,
    limit: int,
) -> tuple[list[dict[str, object]], int]:
    values = tuple(sorted({int(value) for value in fact_ids}))
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
        ORDER BY evidence.fact_id, evidence.evidence_uri
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
            status = str(fact["status"]).upper()
            resolution = str(fact["resolutionStatus"]).upper()
            if status == "STALE" or resolution == "STALE":
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
        (int(fact["factId"]) for fact in facts),
        limit=evidence_limit,
    )
    if any(
        item["freshness"] != "FRESH"
        for item in evidence
    ):
        missing.append(
            {
                "code": "STALE_SOURCE",
                "requirement": "fresh evidence revision",
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
    freshness = (
        "FRESH"
        if evidence and all(
            item["freshness"] == "FRESH" for item in evidence
        )
        else ("UNKNOWN" if not evidence else "STALE")
    )
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
