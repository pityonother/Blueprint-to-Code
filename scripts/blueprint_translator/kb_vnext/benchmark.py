"""Balanced, deterministic query benchmark for ARK KB vNext."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .kb_context import build_bounded_context_pack
from .query_planner import QueryRequirements, plan_query


BENCHMARK_SCHEMA = "ark-kb-query-benchmark/v1"
TIER_COUNTS = {
    "simple_fact": 30,
    "cross_asset_relationship": 30,
    "inheritance_effective": 20,
    "map_registration": 15,
    "native_boundary": 15,
    "runtime_validation": 10,
}
MAJOR_DOMAINS = (
    "global_registration",
    "class_inheritance",
    "creature_definition",
    "breeding_growth_imprinting_genetics",
    "status_component",
    "damage_resistance",
    "buff",
    "inventory",
    "item_use",
    "crafting_engram",
    "loot_quality_reward",
    "harvest",
)
NEGATIVE_CASES = (
    "similar_name_is_not_same_asset",
    "high_reference_texture_is_not_mechanism_hub",
    "confirmed_empty_is_not_unrecovered",
    "stale_evidence_remains_stale",
    "parent_change_invalidates_effective_fact",
    "native_overload_remains_candidate",
    "map_namespace_is_not_map_usage",
    "leaf_does_not_override_public_parent_rule",
)
NEGATIVE_CASE_INDEXES = {
    0: "similar_name_is_not_same_asset",
    1: "high_reference_texture_is_not_mechanism_hub",
    60: "confirmed_empty_is_not_unrecovered",
    61: "parent_change_invalidates_effective_fact",
    62: "leaf_does_not_override_public_parent_rule",
    80: "map_namespace_is_not_map_usage",
    90: "native_overload_remains_candidate",
    110: "stale_evidence_remains_stale",
}


@dataclass(frozen=True)
class BenchmarkCase:
    query_id: str
    question: str
    tier: str
    primary_domain: str
    expected_answer_type: str
    expected_gap_code: str
    request: Mapping[str, object]
    negative_case: str = ""


def _row_value(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> tuple[object, ...] | None:
    row = connection.execute(sql, parameters).fetchone()
    return None if row is None else tuple(row)


def _all_entities(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT canonical_uri FROM entities ORDER BY entity_id"
        )
    ]


def _domain_entities(
    connection: sqlite3.Connection,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for domain in MAJOR_DOMAINS:
        result[domain] = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT entity.canonical_uri
                FROM domain_memberships AS membership
                JOIN entities AS entity
                  ON entity.entity_id=membership.entity_id
                WHERE membership.domain_id=?
                ORDER BY
                    CASE membership.status
                      WHEN 'CONFIRMED' THEN 0
                      WHEN 'CANDIDATE' THEN 1
                      ELSE 2
                    END,
                    entity.entity_id
                LIMIT 200
                """,
                (domain,),
            )
        ]
    return result


def _pick_entity(
    *,
    domain: str,
    index: int,
    by_domain: Mapping[str, list[str]],
    all_entities: list[str],
) -> str:
    candidates = by_domain.get(domain) or all_entities
    if not candidates:
        raise ValueError("Cannot build benchmark without entities")
    return candidates[index % len(candidates)]


def _request(
    entity: str,
    **requirements: object,
) -> dict[str, object]:
    return {
        "entity": entity,
        "factTypes": [],
        "factNames": [],
        "edgeTypes": [],
        "requiresNative": False,
        "requiresRuntime": False,
        "requiresMapEvidence": False,
        "evidenceLimit": 50,
        "budgetTokens": 2_000,
        **requirements,
    }


def build_benchmark_cases(
    connection: sqlite3.Connection,
) -> list[BenchmarkCase]:
    """Bind the fixed benchmark shape to stable identities in this snapshot."""

    all_entities = _all_entities(connection)
    by_domain = _domain_entities(connection)
    cases: list[BenchmarkCase] = []
    global_index = 0
    for tier, count in TIER_COUNTS.items():
        for tier_index in range(count):
            domain = MAJOR_DOMAINS[global_index % len(MAJOR_DOMAINS)]
            entity = _pick_entity(
                domain=domain,
                index=tier_index,
                by_domain=by_domain,
                all_entities=all_entities,
            )
            expected_gap = ""
            negative_case = ""
            request = _request(entity)
            if tier == "simple_fact":
                question = (
                    "Return the canonical identity and entity kind for a "
                    f"{domain} representative."
                )
            elif tier == "cross_asset_relationship":
                relation = _row_value(
                    connection,
                    """
                    SELECT source.canonical_uri, edge.edge_type
                    FROM edges AS edge
                    JOIN entities AS source
                      ON source.entity_id=edge.source_entity_id
                    LEFT JOIN domain_memberships AS membership
                      ON membership.entity_id=source.entity_id
                     AND membership.domain_id=?
                    ORDER BY
                        CASE WHEN membership.entity_id IS NULL THEN 1 ELSE 0 END,
                        edge.edge_id
                    LIMIT 1 OFFSET ?
                    """,
                    (domain, tier_index),
                )
                if relation is None:
                    request = _request(
                        entity,
                        edgeTypes=["REFERENCES_OBJECT"],
                    )
                    expected_gap = "REFERENCE_CLOSURE_OPEN"
                else:
                    request = _request(
                        str(relation[0]),
                        edgeTypes=[str(relation[1])],
                    )
                question = (
                    "Return a typed outbound relationship for a "
                    f"{domain} representative."
                )
            elif tier == "inheritance_effective":
                effective = _row_value(
                    connection,
                    """
                    SELECT entity.canonical_uri, fact.fact_name
                    FROM effective_facts AS effective
                    JOIN entities AS entity
                      ON entity.entity_id=effective.entity_id
                    JOIN facts AS fact ON fact.fact_id=effective.fact_id
                    LEFT JOIN domain_memberships AS membership
                      ON membership.entity_id=entity.entity_id
                     AND membership.domain_id=?
                    ORDER BY
                        CASE WHEN membership.entity_id IS NULL THEN 1 ELSE 0 END,
                        effective.entity_id, effective.fact_name
                    LIMIT 1 OFFSET ?
                    """,
                    (domain, tier_index),
                )
                if effective is None:
                    request = _request(
                        entity,
                        factTypes=["EFFECTIVE_DEFAULT"],
                    )
                    expected_gap = "MISSING_FACT"
                else:
                    request = _request(
                        str(effective[0]),
                        factTypes=["EFFECTIVE_DEFAULT"],
                        factNames=[str(effective[1])],
                    )
                question = (
                    "Resolve an effective default through the class chain for "
                    f"a {domain} representative."
                )
            elif tier == "map_registration":
                if tier_index % 2 == 0:
                    registration = _row_value(
                        connection,
                        """
                        SELECT source.canonical_uri
                        FROM edges AS edge
                        JOIN entities AS source
                          ON source.entity_id=edge.source_entity_id
                        WHERE edge.edge_type='REGISTERS'
                        ORDER BY edge.edge_id
                        LIMIT 1 OFFSET ?
                        """,
                        (tier_index // 2,),
                    )
                    if registration is None:
                        request = _request(
                            entity,
                            edgeTypes=["REGISTERS"],
                        )
                        expected_gap = "REFERENCE_CLOSURE_OPEN"
                    else:
                        request = _request(
                            str(registration[0]),
                            edgeTypes=["REGISTERS"],
                        )
                    question = (
                        "Return a typed system registration and its evidence."
                    )
                else:
                    request = _request(
                        entity,
                        requiresMapEvidence=True,
                    )
                    question = (
                        "Confirm direct map or PCG usage, not a namespace hint."
                    )
            elif tier == "native_boundary":
                native = _row_value(
                    connection,
                    """
                    SELECT entity.canonical_uri
                    FROM native_blueprint_links AS link
                    JOIN entities AS entity
                      ON entity.entity_id=link.blueprint_entity_id
                    WHERE link.status='CONFIRMED'
                    ORDER BY link.link_id
                    LIMIT 1 OFFSET ?
                    """,
                    (tier_index,),
                )
                request = _request(
                    str(native[0]) if native else entity,
                    requiresNative=True,
                )
                if native is None:
                    expected_gap = "NATIVE_BOUNDARY_UNRESOLVED"
                question = (
                    "Confirm an exact Blueprint-to-native edge or return the "
                    "bounded native evidence gap."
                )
            else:
                runtime = _row_value(
                    connection,
                    """
                    SELECT entity.canonical_uri
                    FROM facts AS fact
                    JOIN entities AS entity
                      ON entity.entity_id=fact.subject_entity_id
                    WHERE fact.fact_type='RUNTIME_OBSERVATION'
                      AND fact.current=1
                      AND fact.status IN ('CONFIRMED', 'VERIFIED', 'RESOLVED')
                    ORDER BY fact.fact_id
                    LIMIT 1 OFFSET ?
                    """,
                    (tier_index,),
                )
                request = _request(
                    str(runtime[0]) if runtime else entity,
                    requiresRuntime=True,
                )
                if runtime is None:
                    expected_gap = "RUNTIME_DYNAMIC_BRANCH"
                question = (
                    "Return a runtime observation or a named runtime probe."
                )
            negative_case = NEGATIVE_CASE_INDEXES.get(global_index, "")
            if negative_case == "similar_name_is_not_same_asset":
                request = _request(
                    "__ARK_KB_NEGATIVE_NOT_SAME_ASSET__",
                )
                expected_gap = "NO_ENTITY_MATCH"
            if negative_case == "map_namespace_is_not_map_usage":
                request = _request(
                    entity,
                    requiresMapEvidence=True,
                )
            cases.append(
                BenchmarkCase(
                    query_id=f"{tier}-{tier_index + 1:03d}",
                    question=question,
                    tier=tier,
                    primary_domain=domain,
                    expected_answer_type="complete_or_bounded_gap",
                    expected_gap_code=expected_gap,
                    request=request,
                    negative_case=negative_case,
                )
            )
            global_index += 1
    validate_benchmark_shape(cases)
    return cases


def validate_benchmark_shape(cases: list[BenchmarkCase]) -> None:
    if len(cases) != 120:
        raise ValueError(f"Benchmark must contain exactly 120 cases, got {len(cases)}")
    tier_counts = Counter(case.tier for case in cases)
    if dict(tier_counts) != TIER_COUNTS:
        raise ValueError(f"Unbalanced benchmark tiers: {dict(tier_counts)}")
    domain_counts = Counter(case.primary_domain for case in cases)
    below_minimum = {
        domain: domain_counts[domain]
        for domain in MAJOR_DOMAINS
        if domain_counts[domain] < 5
    }
    if below_minimum:
        raise ValueError(f"Major domains below five cases: {below_minimum}")
    present_negatives = {case.negative_case for case in cases if case.negative_case}
    if present_negatives != set(NEGATIVE_CASES):
        raise ValueError("Benchmark negative cases are incomplete")


def materialize_benchmark_queries(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    cases = build_benchmark_cases(connection)
    connection.executemany(
        """
        INSERT INTO benchmark_queries(
            query_id, question, tier, primary_domain,
            expected_answer_type, expected_gap_code,
            query_json, negative_case
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                case.query_id,
                case.question,
                case.tier,
                case.primary_domain,
                case.expected_answer_type,
                case.expected_gap_code,
                json.dumps(
                    case.request,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                case.negative_case,
            )
            for case in cases
        ],
    )
    return {
        "benchmarkQueries": len(cases),
        "benchmarkDomains": len(
            {case.primary_domain for case in cases}
        ),
        "benchmarkNegativeCases": len(
            {case.negative_case for case in cases if case.negative_case}
        ),
    }


def _requirements(request: Mapping[str, object]) -> QueryRequirements:
    def values(key: str) -> tuple[str, ...]:
        raw = request.get(key, [])
        return tuple(str(value) for value in raw) if isinstance(raw, list) else ()

    return QueryRequirements(
        entity_query=str(request.get("entity") or ""),
        fact_types=values("factTypes"),
        fact_names=values("factNames"),
        edge_types=values("edgeTypes"),
        requires_native=bool(request.get("requiresNative")),
        requires_runtime=bool(request.get("requiresRuntime")),
        requires_map_evidence=bool(request.get("requiresMapEvidence")),
        evidence_limit=int(request.get("evidenceLimit") or 50),
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def run_query_benchmark(core_path: Path) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{core_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    results: list[dict[str, object]] = []
    latencies: list[float] = []
    two_hop_latencies: list[float] = []
    try:
        rows = list(
            connection.execute(
                """
                SELECT * FROM benchmark_queries
                ORDER BY
                  CASE tier
                    WHEN 'simple_fact' THEN 1
                    WHEN 'cross_asset_relationship' THEN 2
                    WHEN 'inheritance_effective' THEN 3
                    WHEN 'map_registration' THEN 4
                    WHEN 'native_boundary' THEN 5
                    WHEN 'runtime_validation' THEN 6
                  END,
                  query_id
                """
            )
        )
        if len(rows) != 120:
            raise ValueError(
                f"Snapshot benchmark must contain 120 rows, got {len(rows)}"
            )
        for row in rows:
            request = json.loads(str(row["query_json"]))
            started = time.perf_counter()
            result = plan_query(connection, _requirements(request))
            context = build_bounded_context_pack(
                result,
                budget_tokens=int(request.get("budgetTokens") or 2_000),
            )
            elapsed_ms = (time.perf_counter() - started) * 1_000
            latencies.append(elapsed_ms)
            gap_codes = {
                str(item["code"])
                for item in result["missingRequirements"]
            }
            expected_gap = str(row["expected_gap_code"])
            explicit_bounded_gap = bool(
                result["missingRequirements"]
                and result["recommendedProbes"]
            )
            requirement_met = (
                result["route"] == "DB_ONLY_COMPLETE"
                or explicit_bounded_gap
            )
            if expected_gap:
                requirement_met = requirement_met and expected_gap in gap_codes
            results.append(
                {
                    "queryId": str(row["query_id"]),
                    "tier": str(row["tier"]),
                    "primaryDomain": str(row["primary_domain"]),
                    "negativeCase": str(row["negative_case"]),
                    "route": str(result["route"]),
                    "gapCodes": sorted(gap_codes),
                    "probeCount": len(result["recommendedProbes"]),
                    "contextTokens": int(context["estimatedTokens"]),
                    "latencyMs": round(elapsed_ms, 3),
                    "requirementMet": requirement_met,
                }
            )
        source_ids = [
            int(row[0])
            for row in connection.execute(
                """
                SELECT DISTINCT source_entity_id
                FROM edges ORDER BY source_entity_id LIMIT 30
                """
            )
        ]
        for source_id in source_ids:
            started = time.perf_counter()
            list(
                connection.execute(
                    """
                    SELECT second.target_entity_id
                    FROM edges AS first
                    JOIN edges AS second
                      ON second.source_entity_id=first.target_entity_id
                    WHERE first.source_entity_id=?
                    LIMIT 200
                    """,
                    (source_id,),
                )
            )
            two_hop_latencies.append(
                (time.perf_counter() - started) * 1_000
            )
    finally:
        connection.close()
    route_counts = Counter(str(item["route"]) for item in results)
    tier_counts = Counter(str(item["tier"]) for item in results)
    simple = [item for item in results if item["tier"] == "simple_fact"]
    satisfied = sum(bool(item["requirementMet"]) for item in results)
    context_max = max(int(item["contextTokens"]) for item in results)
    return {
        "schema": BENCHMARK_SCHEMA,
        "total": len(results),
        "tierCounts": dict(tier_counts),
        "routeCounts": dict(route_counts),
        "completeOrBounded": satisfied,
        "completeOrBoundedRate": satisfied / len(results),
        "simpleDbOnly": sum(
            item["route"] == "DB_ONLY_COMPLETE" for item in simple
        ),
        "simpleDbOnlyRate": (
            sum(item["route"] == "DB_ONLY_COMPLETE" for item in simple)
            / len(simple)
        ),
        "unresolved": len(results) - satisfied,
        "latencyMs": {
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
            "twoHopP95": round(
                _percentile(two_hop_latencies, 0.95),
                3,
            ),
            "twoHopSamples": len(two_hop_latencies),
        },
        "contextTokens": {
            "maximum": context_max,
            "budget": 2_000,
            "withinBudget": context_max <= 2_000,
        },
        "results": results,
    }
