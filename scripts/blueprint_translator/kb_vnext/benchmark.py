"""Fixed expected-answer query benchmark for ARK KB vNext."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .kb_context import build_bounded_context_pack
from .query_planner import (
    COMPLETE_CONFIDENCE,
    GAP_CODES,
    IDENTITY_COMPLETE_STATUSES,
    QueryRequirements,
    fact_value_is_usable,
    plan_query,
)


BENCHMARK_SCHEMA = "ark-kb-query-benchmark/v2"
GOLD_SET_SCHEMA = "ark-kb-query-gold-set/v1"
DEFAULT_GOLD_SET_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "kb_query_gold_set.v1.json"
)
DEFAULT_PROJECTION_REVIEW_PATH = (
    Path(__file__).resolve().parents[3]
    / "ontology"
    / "projection_review.v1.json"
)
CATEGORY_MINIMUMS = {
    "FACT": 30,
    "EFFECTIVE": 20,
    "RELATIONSHIP": 20,
    "REGISTRATION": 10,
    "MAP": 10,
    "NATIVE": 10,
    "RUNTIME": 10,
    "NEGATIVE": 20,
}
TIER_COUNTS = dict(CATEGORY_MINIMUMS)
SEMANTIC_POSITIVE_MINIMUMS = {
    "FACT": 30,
    "EFFECTIVE": 20,
    "RELATIONSHIP": 20,
    "REGISTRATION": 10,
    "MAP": 10,
    "NATIVE": 10,
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
    "map_world",
    "native_boundary",
    "runtime_validation",
)
NEGATIVE_CASES = (
    "ambiguous_alias_is_not_identity",
    "candidate_edge_is_not_complete",
    "identity_only_is_not_semantic",
    "legacy_registration_is_not_confirmed",
    "map_namespace_is_not_map_usage",
    "missing_entity_is_not_identity",
    "native_symbol_is_not_confirmed_callsite",
    "pcg_reference_is_not_direct_placement",
    "stale_source_is_not_complete",
    "static_default_is_not_runtime_observation",
    "world_partition_reference_is_not_usage",
)
REVIEW_STATUSES = {"HUMAN_REVIEWED", "EMPIRICAL", "FIXTURE_EXACT"}
ANSWER_MODES = {"IDENTITY", "FACT", "RELATIONSHIP", "MECHANISM"}
SEMANTIC_EXPECTATIONS = {"EXACT", "GAP_ONLY", "IDENTITY_ONLY"}
COMPLETE_ROUTES = {"IDENTITY_ONLY_COMPLETE", "DB_SEMANTIC_COMPLETE"}
NONCOMPLETE_ROUTES = {
    "DB_PARTIAL",
    "EVIDENCE_REQUIRED",
    "AMBIGUOUS",
}
COMPLETE_STATUSES = {"CONFIRMED", "VERIFIED", "RESOLVED"}
UNUSABLE_VALUE_KINDS = {
    "",
    "UNKNOWN",
    "FINGERPRINT",
    "CONFIRMED_EMPTY",
}


def _expected_gap_probe(
    case: BenchmarkCase,
    code: str,
) -> dict[str, object]:
    asset = str(case.expected.get("identityUri") or "")
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
    if code == "IDENTITY_PROVENANCE_UNKNOWN":
        return {
            "probeType": "identity_revision_probe",
            "asset": asset,
            "operation": "resolve_identity_source_revision",
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
            "operation": "rebuild_core_v4_snapshot",
            "budgetTokens": 500,
            "reason": code,
        }
    return {
        "probeType": "blueprint_evidence_query",
        "asset": asset,
        "operation": (
            "inheritance_path"
            if code == "PARENT_CHAIN_OPEN"
            else "named_fact"
        ),
        "budgetTokens": 1500,
        "reason": code,
    }


def _expected_gap_requirement(
    case: BenchmarkCase,
    code: str,
) -> str:
    fact_types = [
        str(value).upper()
        for value in case.request.get("factTypes", [])
    ]
    fact_names = [
        str(value)
        for value in case.request.get("factNames", [])
    ]
    edge_types = [
        str(value).upper()
        for value in case.request.get("edgeTypes", [])
    ]
    if code in {"NO_ENTITY_MATCH", "AMBIGUOUS_ENTITY"}:
        return "unique entity"
    if code == "IDENTITY_PROVENANCE_UNKNOWN":
        return "identity source revision"
    if code == "NATIVE_BOUNDARY_UNRESOLVED":
        return (
            "fresh confirmed Blueprint-native callsite "
            "with graph and native evidence"
        )
    if code == "RUNTIME_DYNAMIC_BRANCH":
        return "materialized confirmed runtime observation"
    if code == "MAP_USAGE_INCOMPLETE":
        requested = ", ".join(edge_types) or "typed map usage"
        return (
            f"{requested}: confirmed typed direct, PCG, or "
            "World Partition map usage"
        )
    if code == "REFERENCE_CLOSURE_OPEN":
        requested = ", ".join(edge_types) or "requested relationship"
        return f"{requested}:confirmed edge evidence"
    if code == "SCHEMA_MIGRATION_REQUIRED":
        return "typed map-usage evidence tables"
    if code in {
        "MISSING_FACT",
        "FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED",
        "FACT_NOT_FOUND",
        "FACT_STALE",
        "FACT_AMBIGUOUS",
    }:
        fact_type = fact_types[0] if fact_types else "requested fact"
        return (
            f"{fact_type}:{fact_names[0]}"
            if fact_names
            else fact_type
        )
    if code == "PARENT_CHAIN_OPEN":
        return "confirmed fresh effective-class evidence"
    if code == "STALE_SOURCE":
        return "fresh source evidence"
    if code == "EVIDENCE_LIMIT_INSUFFICIENT":
        return "visible fresh evidence within response limit"
    if code == "UNSUPPORTED_SERIALIZATION":
        return "supported typed serialization"
    if code in {"REQUEST_UNDERSPECIFIED", "REQUEST_MODE_MISMATCH"}:
        return "valid explicit query contract"
    raise ValueError(f"Unsupported benchmark gap code: {code}")


def _expected_gap_status(case: BenchmarkCase) -> str:
    return {
        "AMBIGUOUS": "AMBIGUOUS",
        "DB_PARTIAL": "PARTIAL",
        "EVIDENCE_REQUIRED": "GAP",
    }.get(str(case.expected.get("route") or ""), "")


@dataclass(frozen=True)
class BenchmarkCase:
    query_id: str
    question: str
    category: str
    primary_domain: str
    entity: str
    request: Mapping[str, object]
    expected: Mapping[str, object]
    review_status: str
    protocol_boundary_only: bool
    negative_case: str = ""
    performance_path: str = ""

    @property
    def tier(self) -> str:
        """Compatibility alias for the v1 materialized table column."""

        return self.category

    @property
    def expected_answer_type(self) -> str:
        return str(self.expected["semanticExpectation"])

    @property
    def expected_gap_code(self) -> str:
        gaps = self.expected.get("gapCodes", [])
        return str(gaps[0]) if isinstance(gaps, list) and gaps else ""


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return list(value)


def _parse_case(raw: object, *, index: int) -> BenchmarkCase:
    if not isinstance(raw, dict):
        raise ValueError(f"Gold case {index} must be an object")
    required = {
        "id",
        "question",
        "category",
        "primaryDomain",
        "entity",
        "requirements",
        "expected",
        "reviewStatus",
        "protocolBoundaryOnly",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError(f"Gold case {index} missing fields: {missing}")
    requirements = raw["requirements"]
    expected = raw["expected"]
    if not isinstance(requirements, dict) or not isinstance(expected, dict):
        raise ValueError(f"Gold case {index} request/expected must be objects")
    request = {
        "entity": str(raw["entity"]),
        "answerMode": str(requirements.get("answerMode") or ""),
        "factTypes": _as_string_list(
            requirements.get("factTypes", []),
            field=f"case {index} factTypes",
        ),
        "factNames": _as_string_list(
            requirements.get("factNames", []),
            field=f"case {index} factNames",
        ),
        "edgeTypes": _as_string_list(
            requirements.get("edgeTypes", []),
            field=f"case {index} edgeTypes",
        ),
        "requiresNative": bool(requirements.get("requiresNative")),
        "requiresRuntime": bool(requirements.get("requiresRuntime")),
        "requiresMapEvidence": bool(
            requirements.get("requiresMapEvidence")
        ),
        "evidenceLimit": int(requirements.get("evidenceLimit") or 50),
        "budgetTokens": int(requirements.get("budgetTokens") or 2_000),
    }
    normalized_expected = {
        "route": str(expected.get("route") or ""),
        "status": expected.get("status"),
        "identityUri": expected.get("identityUri"),
        "identityStatus": expected.get("identityStatus"),
        "identityConfidence": expected.get("identityConfidence"),
        "identityEvidence": expected.get("identityEvidence"),
        "facts": expected.get("facts", []),
        "relationships": expected.get("relationships", []),
        "gapCodes": _as_string_list(
            expected.get("gapCodes", []),
            field=f"case {index} gapCodes",
        ),
        "mustContainEvidence": bool(
            expected.get("mustContainEvidence")
        ),
        "semanticExpectation": str(
            expected.get("semanticExpectation") or ""
        ),
    }
    if not isinstance(normalized_expected["facts"], list):
        raise ValueError(f"Gold case {index} facts must be a list")
    if not isinstance(normalized_expected["relationships"], list):
        raise ValueError(f"Gold case {index} relationships must be a list")
    return BenchmarkCase(
        query_id=str(raw["id"]),
        question=str(raw["question"]),
        category=str(raw["category"]).upper(),
        primary_domain=str(raw["primaryDomain"]),
        entity=str(raw["entity"]),
        request=request,
        expected=normalized_expected,
        review_status=str(raw["reviewStatus"]),
        protocol_boundary_only=bool(raw["protocolBoundaryOnly"]),
        negative_case=str(raw.get("negativeCase") or ""),
        performance_path=str(raw.get("performancePath") or ""),
    )


def _validate_fact(case: BenchmarkCase, fact: object) -> None:
    if not isinstance(fact, dict):
        raise ValueError(f"{case.query_id}: expected fact must be an object")
    required = {
        "factType",
        "factName",
        "valueKind",
        "value",
        "status",
        "evidenceUri",
    }
    missing = sorted(required - fact.keys())
    if missing:
        raise ValueError(
            f"{case.query_id}: expected fact missing {missing}"
        )
    if str(fact["valueKind"]).upper() in UNUSABLE_VALUE_KINDS:
        raise ValueError(
            f"{case.query_id}: exact gold fact must have a usable value"
        )
    if not str(fact["evidenceUri"]):
        raise ValueError(
            f"{case.query_id}: exact gold fact requires fixed evidence URI"
        )
    if str(fact["status"]).upper() not in COMPLETE_STATUSES:
        raise ValueError(
            f"{case.query_id}: exact gold fact status is not complete"
        )


def _validate_relationship(
    case: BenchmarkCase,
    relationship: object,
) -> None:
    if not isinstance(relationship, dict):
        raise ValueError(
            f"{case.query_id}: expected relationship must be an object"
        )
    required = {"edgeType", "targetUri", "status", "evidenceUri"}
    missing = sorted(required - relationship.keys())
    if missing:
        raise ValueError(
            f"{case.query_id}: expected relationship missing {missing}"
        )
    if not str(relationship["targetUri"]).startswith(("/", "class://")):
        raise ValueError(
            f"{case.query_id}: relationship target must be a fixed URI"
        )
    if not str(relationship["evidenceUri"]):
        raise ValueError(
            f"{case.query_id}: relationship requires fixed evidence URI"
        )
    if "sourceUri" in relationship and not str(
        relationship["sourceUri"]
    ).startswith(("/", "class://")):
        raise ValueError(
            f"{case.query_id}: relationship source must be a fixed URI"
        )
    if "freshness" in relationship and str(
        relationship["freshness"]
    ).upper() != "FRESH":
        raise ValueError(
            f"{case.query_id}: exact relationship freshness must be FRESH"
        )
    if case.category == "MAP":
        map_required = {
            "evidenceLayer",
            "claimsCompleteMapUsage",
            "claimsSpawnCoordinates",
        }
        map_missing = sorted(map_required - relationship.keys())
        if map_missing:
            raise ValueError(
                f"{case.query_id}: map relationship missing {map_missing}"
            )
        if not str(relationship["evidenceLayer"]):
            raise ValueError(
                f"{case.query_id}: map relationship needs an evidence layer"
            )
        if (
            relationship["claimsCompleteMapUsage"] is not False
            or relationship["claimsSpawnCoordinates"] is not False
        ):
            raise ValueError(
                f"{case.query_id}: bounded map gold cannot claim completeness "
                "or coordinates"
            )


def _validate_identity_contract(case: BenchmarkCase) -> None:
    expected = case.expected
    if str(expected.get("status") or "").upper() != "COMPLETE":
        raise ValueError(
            f"{case.query_id}: identity-only gold status must be COMPLETE"
        )
    if (
        str(expected.get("identityStatus") or "").upper()
        not in IDENTITY_COMPLETE_STATUSES
    ):
        raise ValueError(
            f"{case.query_id}: identity-only gold status is not complete"
        )
    if (
        str(expected.get("identityConfidence") or "").upper()
        not in COMPLETE_CONFIDENCE
    ):
        raise ValueError(
            f"{case.query_id}: identity-only gold confidence is not high"
        )
    evidence = expected.get("identityEvidence")
    if not isinstance(evidence, Mapping):
        raise ValueError(
            f"{case.query_id}: identity-only gold requires identityEvidence"
        )
    required_evidence = {
        "evidenceUri",
        "evidenceRole",
        "freshness",
        "sourceRevision",
    }
    missing_evidence = sorted(required_evidence - evidence.keys())
    if missing_evidence:
        raise ValueError(
            f"{case.query_id}: identityEvidence missing {missing_evidence}"
        )
    if (
        str(evidence.get("evidenceRole") or "") != "IDENTITY_REVISION"
        or str(evidence.get("freshness") or "").upper() != "FRESH"
    ):
        raise ValueError(
            f"{case.query_id}: identityEvidence must be a fresh "
            "IDENTITY_REVISION"
        )
    revision = evidence.get("sourceRevision")
    if not isinstance(revision, Mapping):
        raise ValueError(
            f"{case.query_id}: identityEvidence requires sourceRevision"
        )
    required_revision = {
        "sourceKind",
        "sourceUri",
        "sourceFingerprint",
        "producerVersion",
        "schemaVersion",
        "freshness",
    }
    missing_revision = sorted(required_revision - revision.keys())
    if missing_revision or any(
        not str(revision.get(field) or "")
        for field in required_revision
    ):
        raise ValueError(
            f"{case.query_id}: identity sourceRevision missing "
            f"{missing_revision or 'non-empty values'}"
        )
    if str(revision.get("freshness") or "").upper() != "FRESH":
        raise ValueError(
            f"{case.query_id}: identity sourceRevision must be FRESH"
        )
    evidence_uri = str(evidence.get("evidenceUri") or "")
    if not evidence_uri or evidence_uri != str(revision["sourceUri"]):
        raise ValueError(
            f"{case.query_id}: identity evidenceUri must match "
            "sourceRevision sourceUri"
        )


def _validate_case(case: BenchmarkCase) -> None:
    if not case.query_id or not case.question or not case.entity:
        raise ValueError("Gold case identity/question/entity cannot be empty")
    if case.category not in TIER_COUNTS:
        raise ValueError(f"{case.query_id}: unknown category {case.category}")
    if case.primary_domain not in MAJOR_DOMAINS:
        raise ValueError(
            f"{case.query_id}: unknown domain {case.primary_domain}"
        )
    answer_mode = str(case.request["answerMode"])
    if answer_mode not in ANSWER_MODES:
        raise ValueError(f"{case.query_id}: invalid answerMode")
    if case.review_status not in REVIEW_STATUSES:
        raise ValueError(f"{case.query_id}: invalid reviewStatus")
    expected = case.expected
    semantic_expectation = str(expected["semanticExpectation"])
    if semantic_expectation not in SEMANTIC_EXPECTATIONS:
        raise ValueError(f"{case.query_id}: invalid semanticExpectation")
    facts = expected["facts"]
    relationships = expected["relationships"]
    gaps = expected["gapCodes"]
    if case.protocol_boundary_only:
        if semantic_expectation != "GAP_ONLY" or not gaps:
            raise ValueError(
                f"{case.query_id}: protocol boundary must be GAP_ONLY"
            )
        if facts or relationships or expected["mustContainEvidence"]:
            raise ValueError(
                f"{case.query_id}: protocol boundary cannot claim semantics"
            )
    elif semantic_expectation == "GAP_ONLY":
        raise ValueError(
            f"{case.query_id}: GAP_ONLY must be a protocol boundary"
        )
    if semantic_expectation == "EXACT":
        if not facts and not relationships:
            raise ValueError(
                f"{case.query_id}: exact semantic case has no expected answer"
            )
        if gaps or not expected["mustContainEvidence"]:
            raise ValueError(
                f"{case.query_id}: exact semantic case needs evidence, no gap"
            )
        if expected["route"] != "DB_SEMANTIC_COMPLETE":
            raise ValueError(
                f"{case.query_id}: exact semantic route must be complete"
            )
        if (
            not case.entity.startswith("/")
            or "." not in case.entity
            or "/Gold/" in case.entity
        ):
            raise ValueError(
                f"{case.query_id}: exact semantic entity must be a real "
                "fixed canonical URI"
            )
        if expected["identityUri"] != case.entity:
            raise ValueError(
                f"{case.query_id}: exact semantic identity must match entity"
            )
    if semantic_expectation == "IDENTITY_ONLY":
        if answer_mode != "IDENTITY" or facts or relationships or gaps:
            raise ValueError(
                f"{case.query_id}: identity-only answer has semantic claims"
            )
        if expected["route"] != "IDENTITY_ONLY_COMPLETE":
            raise ValueError(
                f"{case.query_id}: identity-only route must be identity"
            )
        if not expected["mustContainEvidence"]:
            raise ValueError(
                f"{case.query_id}: identity-only answer requires evidence"
            )
        _validate_identity_contract(case)
    if semantic_expectation == "GAP_ONLY" and (
        expected["route"] not in NONCOMPLETE_ROUTES
    ):
        raise ValueError(
            f"{case.query_id}: gap-only route must remain non-complete"
        )
    if len(gaps) != len(set(gaps)):
        raise ValueError(f"{case.query_id}: gapCodes must be unique")
    unknown_gaps = sorted(set(gaps) - GAP_CODES)
    if unknown_gaps:
        raise ValueError(
            f"{case.query_id}: unknown gapCodes {unknown_gaps}"
        )
    for gap_code in gaps:
        probe = _expected_gap_probe(case, gap_code)
        if (
            ("asset" in probe and not str(probe["asset"]))
            or ("target" in probe and not str(probe["target"]))
        ):
            raise ValueError(
                f"{case.query_id}: {gap_code} probe requires fixed identity"
            )
    for fact in facts:
        _validate_fact(case, fact)
    for relationship in relationships:
        _validate_relationship(case, relationship)
        if str(relationship["status"]).upper() not in COMPLETE_STATUSES:
            raise ValueError(
                f"{case.query_id}: exact relationship status is not complete"
            )
    if case.category == "FACT" and (
        not facts
        or any(
            str(fact["factType"]) == "EFFECTIVE_DEFAULT"
            for fact in facts
        )
    ):
        raise ValueError(f"{case.query_id}: FACT requires a concrete fact")
    if case.category == "EFFECTIVE" and (
        not facts
        or any(
            str(fact["factType"]) != "EFFECTIVE_DEFAULT"
            for fact in facts
        )
    ):
        raise ValueError(
            f"{case.query_id}: EFFECTIVE requires effective defaults"
        )
    if case.category == "RELATIONSHIP" and not relationships:
        raise ValueError(
            f"{case.query_id}: RELATIONSHIP requires an exact edge"
        )
    if case.category == "REGISTRATION" and (
        answer_mode != "RELATIONSHIP"
        or case.request["edgeTypes"] != ["REGISTERS"]
    ):
        raise ValueError(
            f"{case.query_id}: REGISTRATION must request REGISTERS"
        )
    if case.category == "MAP" and not case.request["requiresMapEvidence"]:
        raise ValueError(f"{case.query_id}: MAP requires map evidence")
    if (
        case.category == "MAP"
        and semantic_expectation == "EXACT"
        and any(
            "sourceUri" not in relationship
            or str(relationship.get("targetUri")) != case.entity
            or str(relationship.get("freshness")).upper() != "FRESH"
            for relationship in relationships
        )
    ):
        raise ValueError(
            f"{case.query_id}: exact MAP gold requires fixed sourceUri, "
            "query-target targetUri, and FRESH evidence"
        )
    if case.category == "NATIVE" and not case.request["requiresNative"]:
        raise ValueError(f"{case.query_id}: NATIVE requires native evidence")
    if case.category == "RUNTIME" and not case.request["requiresRuntime"]:
        raise ValueError(f"{case.query_id}: RUNTIME requires runtime evidence")


def validate_benchmark_shape(cases: Sequence[BenchmarkCase]) -> None:
    if len(cases) < 130:
        raise ValueError(
            f"Benchmark must contain at least 130 cases, got {len(cases)}"
        )
    identifiers = [case.query_id for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Benchmark query IDs must be unique")
    for case in cases:
        _validate_case(case)
    category_counts = Counter(case.category for case in cases)
    below_minimum = {
        category: {
            "actual": category_counts[category],
            "minimum": minimum,
        }
        for category, minimum in CATEGORY_MINIMUMS.items()
        if category_counts[category] < minimum
    }
    if below_minimum:
        raise ValueError(f"Benchmark category quotas missing: {below_minimum}")
    negative_count = sum(bool(case.negative_case) for case in cases)
    if negative_count < 20:
        raise ValueError(
            f"Benchmark needs at least 20 negative cases, got {negative_count}"
        )
    fact_kinds = {
        str(fact["valueKind"]).upper()
        for case in cases
        if case.category == "FACT"
        for fact in case.expected["facts"]
    }
    if not {"NUMBER", "TEXT", "BOOLEAN"} <= fact_kinds:
        raise ValueError(
            "Concrete fact gold must include number, text, and boolean"
        )


def _corpus_readiness(
    cases: Sequence[BenchmarkCase],
) -> tuple[bool, list[dict[str, object]]]:
    exact_counts = Counter(
        case.category
        for case in cases
        if case.expected["semanticExpectation"] == "EXACT"
    )
    gaps = [
        {
            "category": category,
            "exactCases": exact_counts[category],
            "minimum": minimum,
            "code": "FIXED_SEMANTIC_GOLD_INCOMPLETE",
        }
        for category, minimum in SEMANTIC_POSITIVE_MINIMUMS.items()
        if exact_counts[category] < minimum
    ]
    return not gaps, gaps


def _review_value(item: Mapping[str, object]) -> object:
    kind = str(item.get("valueKind") or "").upper()
    key = {
        "NUMBER": "valueNumber",
        "INTEGER": "valueInteger",
        "BOOLEAN": "valueBoolean",
        "JSON": "valueJson",
        "TEXT": "valueText",
        "ENTITY_REF": "valueText",
    }.get(kind, "")
    return (
        _normalized_review_value(kind, item.get(key))
        if key
        else None
    )


def _normalized_review_value(kind: str, value: object) -> object:
    if kind == "NUMBER" and isinstance(value, (int, float)):
        return float(value)
    if kind == "INTEGER" and isinstance(value, (int, float)):
        return int(value)
    if kind == "BOOLEAN":
        return bool(value)
    return value


def _human_reviewed_fact_keys(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    projections = payload.get("projections")
    if not isinstance(projections, dict):
        raise ValueError("Projection review trust root is malformed")
    keys: set[str] = set()
    for rows in projections.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            keys.add(
                _json_hash(
                    {
                        "entity": row.get("canonicalUri"),
                        "factType": row.get("factType"),
                        "factName": row.get("factName"),
                        "valueKind": row.get("valueKind"),
                        "value": _review_value(row),
                        "evidenceUri": row.get("evidenceUri"),
                    }
                )
            )
    return keys


def _validate_human_reviewed_cases(
    cases: Sequence[BenchmarkCase],
    *,
    projection_review_path: Path,
) -> None:
    reviewed = _human_reviewed_fact_keys(projection_review_path)
    for case in cases:
        if case.review_status != "HUMAN_REVIEWED":
            continue
        facts = case.expected["facts"]
        if not facts:
            raise ValueError(
                f"{case.query_id}: HUMAN_REVIEWED needs a trust-root fact"
            )
        for fact in facts:
            if not isinstance(fact, dict):
                raise ValueError(
                    f"{case.query_id}: malformed HUMAN_REVIEWED fact"
                )
            key = _json_hash(
                {
                    "entity": case.entity,
                    "factType": fact.get("factType"),
                    "factName": fact.get("factName"),
                    "valueKind": fact.get("valueKind"),
                    "value": _normalized_review_value(
                        str(fact.get("valueKind") or "").upper(),
                        fact.get("value"),
                    ),
                    "evidenceUri": fact.get("evidenceUri"),
                }
            )
            if key not in reviewed:
                raise ValueError(
                    f"{case.query_id}: HUMAN_REVIEWED claim is absent "
                    "from projection review trust root"
                )


def load_benchmark_gold_set(
    path: Path = DEFAULT_GOLD_SET_PATH,
    *,
    projection_review_path: Path = DEFAULT_PROJECTION_REVIEW_PATH,
) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Benchmark gold corpus must be a JSON object")
    if raw.get("schema") != GOLD_SET_SCHEMA:
        raise ValueError("Unexpected benchmark gold corpus schema")
    if raw.get("selectionMode") != "MANUAL_FIXED":
        raise ValueError("Gold selectionMode must be MANUAL_FIXED")
    if raw.get("generatedFromCore") is not False:
        raise ValueError("Gold generatedFromCore must be false")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("Benchmark gold corpus cases must be a list")
    cases = [
        _parse_case(value, index=index)
        for index, value in enumerate(raw_cases, start=1)
    ]
    validate_benchmark_shape(cases)
    _validate_human_reviewed_cases(
        cases,
        projection_review_path=projection_review_path,
    )
    ready, gaps = _corpus_readiness(cases)
    return {
        "schema": GOLD_SET_SCHEMA,
        "version": str(raw.get("version") or ""),
        "selectionMode": "MANUAL_FIXED",
        "generatedFromCore": False,
        "corpusSha256": _json_hash(raw),
        "cases": cases,
        "corpusReadyForCutover": ready,
        "corpusGaps": gaps,
        "limitations": raw.get("semanticCoverageLimitations", []),
    }


def build_benchmark_cases(
    connection: sqlite3.Connection,
    *,
    gold_set_path: Path = DEFAULT_GOLD_SET_PATH,
) -> list[BenchmarkCase]:
    """Load checked-in cases without inspecting the current Core database."""

    del connection
    payload = load_benchmark_gold_set(gold_set_path)
    return list(payload["cases"])


def _encoded_query(case: BenchmarkCase) -> str:
    payload = {
        **case.request,
        "_gold": {
            "expected": case.expected,
            "reviewStatus": case.review_status,
            "protocolBoundaryOnly": case.protocol_boundary_only,
            "negativeCase": case.negative_case,
            "performancePath": case.performance_path,
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def materialize_benchmark_queries(
    connection: sqlite3.Connection,
    *,
    gold_set_path: Path = DEFAULT_GOLD_SET_PATH,
    projection_review_path: Path = DEFAULT_PROJECTION_REVIEW_PATH,
) -> dict[str, int]:
    payload = load_benchmark_gold_set(
        gold_set_path,
        projection_review_path=projection_review_path,
    )
    cases = list(payload["cases"])
    connection.execute("DELETE FROM benchmark_queries")
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
                case.category,
                case.primary_domain,
                case.expected_answer_type,
                case.expected_gap_code,
                _encoded_query(case),
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
        "benchmarkNegativeCases": sum(
            bool(case.negative_case) for case in cases
        ),
        "benchmarkProtocolBoundaryOnly": sum(
            case.protocol_boundary_only for case in cases
        ),
        "benchmarkCorpusReady": int(
            bool(payload["corpusReadyForCutover"])
        ),
    }


def _requirements(request: Mapping[str, object]) -> QueryRequirements:
    def values(key: str) -> tuple[str, ...]:
        raw = request.get(key, [])
        return (
            tuple(str(value) for value in raw)
            if isinstance(raw, list)
            else ()
        )

    arguments: dict[str, object] = {
        "entity_query": str(request.get("entity") or ""),
        "fact_types": values("factTypes"),
        "fact_names": values("factNames"),
        "edge_types": values("edgeTypes"),
        "requires_native": bool(request.get("requiresNative")),
        "requires_runtime": bool(request.get("requiresRuntime")),
        "requires_map_evidence": bool(
            request.get("requiresMapEvidence")
        ),
        "evidence_limit": int(request.get("evidenceLimit") or 50),
    }
    if "answer_mode" in QueryRequirements.__dataclass_fields__:
        arguments["answer_mode"] = str(request.get("answerMode") or "")
    return QueryRequirements(**arguments)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _actual_fact_value(fact: Mapping[str, object]) -> object:
    kind = str(fact.get("valueKind") or "").upper()
    if kind in {"TEXT", "ENTITY_REF"}:
        return fact.get("valueText")
    if kind == "NUMBER":
        return fact.get("valueNumber")
    if kind == "INTEGER":
        return fact.get("valueInteger")
    if kind == "BOOLEAN":
        raw = fact.get("valueInteger")
        return bool(raw) if raw in {0, 1} else None
    if kind == "JSON":
        raw_json = fact.get("valueJson")
        if isinstance(raw_json, str):
            try:
                return json.loads(raw_json)
            except json.JSONDecodeError:
                return raw_json
        return raw_json
    return None


def _equal_value(expected: object, actual: object) -> bool:
    if (
        isinstance(expected, (int, float))
        and not isinstance(expected, bool)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
    ):
        return expected == actual
    return expected == actual


def _fact_matches(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> bool:
    return (
        fact_value_is_usable(actual)
        and str(actual.get("factType")) == str(expected.get("factType"))
        and str(actual.get("factName")) == str(expected.get("factName"))
        and str(actual.get("valueKind")).upper()
        == str(expected.get("valueKind")).upper()
        and str(actual.get("status")).upper()
        == str(expected.get("status")).upper()
        and str(actual.get("status")).upper() in COMPLETE_STATUSES
        and str(actual.get("confidence") or "").upper()
        in COMPLETE_CONFIDENCE
        and _equal_value(expected.get("value"), _actual_fact_value(actual))
    )


def _relationship_evidence_uris(
    relationship: Mapping[str, object],
) -> set[str]:
    uris = {str(relationship.get("evidenceUri") or "")}
    nested = relationship.get("evidence", [])
    if isinstance(nested, list):
        uris.update(
            str(item.get("evidenceUri") or "")
            for item in nested
            if isinstance(item, dict)
        )
    return {uri for uri in uris if uri}


def _has_fresh_source_revision(value: Mapping[str, object]) -> bool:
    revision = value.get("sourceRevision")
    return (
        isinstance(revision, Mapping)
        and revision.get("revisionId") is not None
        and bool(str(revision.get("sourceKind") or ""))
        and bool(str(revision.get("sourceUri") or ""))
        and bool(str(revision.get("sourceFingerprint") or ""))
        and bool(str(revision.get("producerVersion") or ""))
        and bool(str(revision.get("schemaVersion") or ""))
        and bool(str(revision.get("generatedAt") or ""))
        and str(revision.get("freshness") or "").upper() == "FRESH"
        and value.get("sourceRevisionId") == revision.get("revisionId")
    )


def _source_revision_matches_gold(
    expected: Mapping[str, object],
    actual: object,
) -> bool:
    if not isinstance(actual, Mapping):
        return False
    exact_fields = (
        "sourceKind",
        "sourceUri",
        "sourceFingerprint",
        "producerVersion",
        "schemaVersion",
    )
    return (
        all(
            str(actual.get(field) or "")
            == str(expected.get(field) or "")
            for field in exact_fields
        )
        and str(actual.get("freshness") or "").upper()
        == str(expected.get("freshness") or "").upper()
    )


def _identity_contract_matches(
    case: BenchmarkCase,
    result: Mapping[str, object],
) -> bool:
    expected_evidence = case.expected.get("identityEvidence")
    entity = result.get("entity")
    if not isinstance(expected_evidence, Mapping) or not isinstance(
        entity,
        Mapping,
    ):
        return False
    if (
        str(result.get("status") or "").upper()
        != str(case.expected.get("status") or "").upper()
        or str(entity.get("status") or "").upper()
        != str(case.expected.get("identityStatus") or "").upper()
        or str(entity.get("status") or "").upper()
        not in IDENTITY_COMPLETE_STATUSES
        or str(entity.get("confidence") or "").upper()
        != str(case.expected.get("identityConfidence") or "").upper()
        or str(entity.get("confidence") or "").upper()
        not in COMPLETE_CONFIDENCE
        or str(entity.get("freshness") or "").upper() != "FRESH"
        or str(result.get("freshness") or "").upper() != "FRESH"
    ):
        return False
    expected_revision = expected_evidence.get("sourceRevision")
    entity_revision = entity.get("sourceRevision")
    if not isinstance(expected_revision, Mapping) or not (
        _source_revision_matches_gold(expected_revision, entity_revision)
    ):
        return False
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != 1:
        return False
    item = evidence[0]
    if not isinstance(item, Mapping):
        return False
    item_revision = item.get("sourceRevision")
    return (
        str(item.get("canonicalUri") or "")
        == str(case.expected.get("identityUri") or "")
        and str(item.get("evidenceUri") or "")
        == str(expected_evidence.get("evidenceUri") or "")
        and str(item.get("evidenceRole") or "")
        == str(expected_evidence.get("evidenceRole") or "")
        and str(item.get("freshness") or "").upper()
        == str(expected_evidence.get("freshness") or "").upper()
        and _has_fresh_source_revision(item)
        and _source_revision_matches_gold(
            expected_revision,
            item_revision,
        )
        and item_revision == entity_revision
    )


def _relationship_matches(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> bool:
    nested = actual.get("evidence")
    expected_evidence_uri = str(expected.get("evidenceUri") or "")
    return (
        str(actual.get("edgeType")) == str(expected.get("edgeType"))
        and (
            "sourceUri" not in expected
            or str(actual.get("sourceUri"))
            == str(expected.get("sourceUri"))
        )
        and str(actual.get("targetUri")) == str(expected.get("targetUri"))
        and str(actual.get("status")).upper()
        == str(expected.get("status")).upper()
        and str(actual.get("confidence") or "").upper()
        in {"HIGH", "CONFIRMED"}
        and str(actual.get("freshness") or "").upper() == "FRESH"
        and _has_fresh_source_revision(actual)
        and isinstance(nested, list)
        and any(
            isinstance(item, Mapping)
            and str(item.get("evidenceUri") or "")
            == expected_evidence_uri
            and str(item.get("freshness") or "").upper() == "FRESH"
            and _has_fresh_source_revision(item)
            for item in nested
        )
        and (
            "freshness" not in expected
            or str(actual.get("freshness")).upper()
            == str(expected.get("freshness")).upper()
        )
        and expected_evidence_uri
        in _relationship_evidence_uris(actual)
        and all(
            key not in expected
            or actual.get(key) == expected.get(key)
            for key in (
                "claimsCompleteMapUsage",
                "claimsSpawnCoordinates",
                "evidenceLayer",
            )
        )
    )


def _exact_semantics(
    case: BenchmarkCase,
    result: Mapping[str, object],
) -> bool:
    expected_facts = [
        item
        for item in case.expected["facts"]
        if isinstance(item, dict)
    ]
    actual_facts = [
        item
        for item in result.get("facts", [])
        if isinstance(item, dict)
    ]
    expected_relationships = [
        item
        for item in case.expected["relationships"]
        if isinstance(item, dict)
    ]
    actual_relationships = [
        item
        for item in result.get("relationships", [])
        if isinstance(item, dict)
    ]
    facts_match = (
        len(expected_facts) == len(actual_facts)
        and all(
            any(
                _fact_matches(expected, actual)
                for actual in actual_facts
            )
            for expected in expected_facts
        )
    )
    relationships_match = (
        len(expected_relationships) == len(actual_relationships)
        and all(
            any(
                _relationship_matches(expected, actual)
                for actual in actual_relationships
            )
            for expected in expected_relationships
        )
    )
    return facts_match and relationships_match


def _fresh_evidence_complete(
    case: BenchmarkCase,
    result: Mapping[str, object],
) -> bool:
    evidence = [
        item
        for item in result.get("evidence", [])
        if isinstance(item, dict)
    ]
    facts = [
        item
        for item in result.get("facts", [])
        if isinstance(item, dict)
    ]
    relationships = [
        item
        for item in result.get("relationships", [])
        if isinstance(item, dict)
    ]
    expected_identity = case.expected.get("identityUri")
    if isinstance(expected_identity, str) and expected_identity:
        if not any(
            str(item.get("evidenceRole") or "")
            == "IDENTITY_REVISION"
            and str(item.get("canonicalUri") or "")
            == expected_identity
            and str(item.get("freshness") or "").upper() == "FRESH"
            and _has_fresh_source_revision(item)
            for item in evidence
        ):
            return False
    for expected in case.expected["facts"]:
        if not isinstance(expected, dict):
            return False
        matching = [
            fact for fact in facts if _fact_matches(expected, fact)
        ]
        if len(matching) != 1:
            return False
        fact_id = matching[0].get("factId")
        expected_uri = str(expected["evidenceUri"])
        if not any(
            item.get("factId") == fact_id
            and str(item.get("evidenceUri")) == expected_uri
            and str(item.get("freshness")).upper() == "FRESH"
            and _has_fresh_source_revision(item)
            for item in evidence
        ):
            return False
    for expected in case.expected["relationships"]:
        if not isinstance(expected, dict):
            return False
        matching = [
            relationship
            for relationship in relationships
            if _relationship_matches(expected, relationship)
        ]
        if len(matching) != 1:
            return False
        relationship = matching[0]
        direct_freshness = str(
            relationship.get("freshness") or ""
        ).upper()
        nested = relationship.get("evidence", [])
        nested_fresh = (
            isinstance(nested, list)
            and any(
                isinstance(item, dict)
                and str(item.get("freshness")).upper() == "FRESH"
                and str(item.get("evidenceUri"))
                == str(expected["evidenceUri"])
                for item in nested
            )
        )
        if direct_freshness != "FRESH" and not nested_fresh:
            return False
    return True


def _has_stale_payload(result: Mapping[str, object]) -> bool:
    if str(result.get("freshness") or "").upper() == "STALE":
        return True
    for key in ("facts", "relationships", "evidence"):
        values = result.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            if str(value.get("freshness") or "").upper() == "STALE":
                return True
            if str(value.get("status") or "").upper() == "STALE":
                return True
    return False


def _has_incomplete_edge(result: Mapping[str, object]) -> bool:
    relationships = result.get("relationships", [])
    if not isinstance(relationships, list):
        return False
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        if str(relationship.get("status") or "").upper() not in (
            COMPLETE_STATUSES
        ):
            return True
        freshness = str(relationship.get("freshness") or "").upper()
        nested = relationship.get("evidence", [])
        has_fresh_nested = (
            isinstance(nested, list)
            and any(
                isinstance(item, dict)
                and str(item.get("freshness") or "").upper() == "FRESH"
                and str(item.get("evidenceUri") or "")
                for item in nested
            )
        )
        if freshness != "FRESH" and not has_fresh_nested:
            return True
        if not _relationship_evidence_uris(relationship):
            return True
    return False


def evaluate_benchmark_result(
    case: BenchmarkCase,
    result: Mapping[str, object],
) -> dict[str, bool]:
    route = str(result.get("route") or "")
    status = str(result.get("status") or "")
    missing = result.get("missingRequirements", [])
    probes = result.get("recommendedProbes", [])
    missing_list = missing if isinstance(missing, list) else []
    probe_list = probes if isinstance(probes, list) else []
    gap_code_list = [
        str(item.get("code") or "")
        if isinstance(item, Mapping)
        else ""
        for item in missing_list
    ]
    actual_requirements_by_code = {
        str(item.get("code") or ""): str(
            item.get("requirement") or ""
        )
        for item in missing_list
        if isinstance(item, Mapping)
    }
    expected_route = str(case.expected.get("route") or "")
    route_matches = bool(expected_route) and route == expected_route
    complete = (
        route in COMPLETE_ROUTES
        and status == "COMPLETE"
        and not missing_list
        and not probe_list
    )
    bounded = (
        route in NONCOMPLETE_ROUTES
        and status in {"PARTIAL", "GAP", "AMBIGUOUS"}
        and bool(missing_list)
        and bool(probe_list)
    )
    semantic_expectation = str(
        case.expected.get("semanticExpectation") or ""
    )
    expected_gap_list = [
        str(code) for code in case.expected.get("gapCodes", [])
    ]
    actual_probes_by_reason = {
        str(item.get("reason") or ""): dict(item)
        for item in probe_list
        if isinstance(item, Mapping)
    }
    expected_probes_by_reason = {
        code: _expected_gap_probe(case, code)
        for code in expected_gap_list
    }
    expected_requirements_by_code = {
        code: _expected_gap_requirement(case, code)
        for code in expected_gap_list
    }
    probe_payload_matches = (
        len(actual_probes_by_reason) == len(probe_list)
        and len(expected_probes_by_reason) == len(expected_gap_list)
        and actual_probes_by_reason == expected_probes_by_reason
    )
    gap_payload_matches = (
        len(gap_code_list) == len(expected_gap_list)
        and sorted(gap_code_list) == sorted(expected_gap_list)
        and len(actual_requirements_by_code) == len(missing_list)
        and actual_requirements_by_code
        == expected_requirements_by_code
        and probe_payload_matches
    )
    gap_status_matches = status.upper() == _expected_gap_status(case)
    gap_contract_matches = (
        semantic_expectation != "GAP_ONLY"
        or (bounded and gap_payload_matches and gap_status_matches)
    )
    entity = result.get("entity")
    actual_identity = (
        str(entity.get("canonicalUri") or "")
        if isinstance(entity, dict)
        else ""
    )
    expected_identity = case.expected.get("identityUri")
    identity_eligible = isinstance(expected_identity, str) and bool(
        expected_identity
    )
    gap_identity_matches = (
        semantic_expectation != "GAP_ONLY"
        or not identity_eligible
        or actual_identity == expected_identity
    )
    gap_contract_matches = (
        gap_contract_matches and gap_identity_matches
    )
    identity_contract_matches = (
        semantic_expectation != "IDENTITY_ONLY"
        or _identity_contract_matches(case, result)
    )
    identity_answer = (
        identity_eligible
        and actual_identity == expected_identity
        and route_matches
        and identity_contract_matches
    )
    protocol = (
        str(result.get("answerMode") or "")
        == str(case.request["answerMode"])
        and route_matches
        and (complete or bounded)
        and gap_contract_matches
        and identity_contract_matches
    )
    semantic_eligible = (
        semantic_expectation == "EXACT"
        and case.request["answerMode"] != "IDENTITY"
    )
    exact_semantics = semantic_eligible and _exact_semantics(case, result)
    stale_leak = complete and _has_stale_payload(result)
    candidate_edge_complete = complete and _has_incomplete_edge(result)
    semantic_answer = (
        exact_semantics
        and identity_answer
        and complete
        and route == "DB_SEMANTIC_COMPLETE"
        and not stale_leak
        and not candidate_edge_complete
    )
    usable_eligible = bool(case.expected["facts"])
    actual_facts = [
        item
        for item in result.get("facts", [])
        if isinstance(item, dict)
    ]
    actual_relationships = [
        item
        for item in result.get("relationships", [])
        if isinstance(item, dict)
    ]
    usable_value = (
        usable_eligible
        and semantic_answer
        and all(fact_value_is_usable(item) for item in actual_facts)
    )
    evidence_complete = (
        semantic_answer
        and bool(case.expected["mustContainEvidence"])
        and _fresh_evidence_complete(case, result)
    )
    expected_gaps = set(expected_gap_list)
    expected_gap_matched = (
        semantic_expectation == "GAP_ONLY"
        and gap_payload_matches
        and bounded
        and route_matches
        and gap_status_matches
        and gap_identity_matches
    )
    ambiguous_route = (
        route == "AMBIGUOUS" or status == "AMBIGUOUS"
    )
    expected_ambiguous = (
        semantic_expectation == "GAP_ONLY"
        and bool(
            expected_gaps
            & {
                "AMBIGUOUS_ENTITY",
                "FACT_AMBIGUOUS",
            }
        )
    )
    gap_only = bounded and not semantic_answer
    contradictory_complete = complete and (
        (
            semantic_expectation == "EXACT"
            and not semantic_answer
        )
        or (
            semantic_expectation == "GAP_ONLY"
            and complete
        )
        or (
            semantic_expectation == "IDENTITY_ONLY"
            and not identity_answer
        )
    )
    contradictory_claim = False
    if semantic_expectation == "EXACT":
        expected_facts = [
            item
            for item in case.expected["facts"]
            if isinstance(item, dict)
        ]
        expected_relationships = [
            item
            for item in case.expected["relationships"]
            if isinstance(item, dict)
        ]
        contradictory_claim = (
            bool(actual_identity)
            and identity_eligible
            and actual_identity != expected_identity
        ) or any(
            not any(
                _fact_matches(expected, actual)
                for expected in expected_facts
            )
            for actual in actual_facts
        ) or any(
            not any(
                _relationship_matches(expected, actual)
                for expected in expected_relationships
            )
            for actual in actual_relationships
        )
    elif semantic_expectation in {
        "GAP_ONLY",
        "IDENTITY_ONLY",
    }:
        contradictory_claim = (
            bool(actual_identity)
            and identity_eligible
            and actual_identity != expected_identity
        ) or any(
            str(fact.get("status") or "").upper() in COMPLETE_STATUSES
            and str(fact.get("confidence") or "").upper()
            in {"HIGH", "CONFIRMED"}
            and fact_value_is_usable(fact)
            for fact in actual_facts
        ) or any(
            str(relationship.get("status") or "").upper()
            in COMPLETE_STATUSES
            and str(relationship.get("confidence") or "").upper()
            in {"HIGH", "CONFIRMED"}
            for relationship in actual_relationships
        )
    return {
        "protocolCompliance": protocol,
        "identityEligible": identity_eligible,
        "identityAnswer": identity_answer,
        "semanticEligible": semantic_eligible,
        "semanticAnswer": semantic_answer,
        "usableValueEligible": usable_eligible,
        "usableValue": usable_value,
        "evidenceBackedComplete": evidence_complete,
        "gapOnly": gap_only,
        "gapExpectedEligible": (
            case.expected["semanticExpectation"] == "GAP_ONLY"
        ),
        "expectedGapMatched": expected_gap_matched,
        "wrongAnswer": (
            not route_matches
            or (
                semantic_expectation == "GAP_ONLY"
                and not gap_contract_matches
            )
            or (
                semantic_expectation == "IDENTITY_ONLY"
                and not identity_contract_matches
            )
            or contradictory_complete
            or contradictory_claim
        ),
        "ambiguousAnswer": ambiguous_route and not expected_ambiguous,
        "expectedAmbiguousAnswer": (
            ambiguous_route and expected_ambiguous
        ),
        "staleLeak": stale_leak,
        "candidateEdgeComplete": candidate_edge_complete,
    }


def _metric(
    results: Sequence[Mapping[str, object]],
    key: str,
    *,
    eligible: str | None = None,
) -> dict[str, object]:
    selected = (
        [item for item in results if bool(item.get(eligible))]
        if eligible
        else list(results)
    )
    numerator = sum(bool(item.get(key)) for item in selected)
    denominator = len(selected)
    return {
        "count": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else 0.0,
    }


def _degree_samples(
    connection: sqlite3.Connection,
) -> list[tuple[str, int, str]]:
    samples: list[tuple[str, int, str]] = []
    queries = {
        "TOP_OUT_DEGREE": (
            """
            SELECT source_entity_id, COUNT(*) AS degree
            FROM edges GROUP BY source_entity_id
            ORDER BY degree DESC, source_entity_id LIMIT 20
            """,
            "OUT",
        ),
        "TOP_IN_DEGREE": (
            """
            SELECT target_entity_id, COUNT(*) AS degree
            FROM edges GROUP BY target_entity_id
            ORDER BY degree DESC, target_entity_id LIMIT 20
            """,
            "IN",
        ),
        "TOP_CROSS_DOMAIN": (
            """
            SELECT edge.source_entity_id, COUNT(*) AS degree
            FROM edges AS edge
            JOIN domain_memberships AS source_domain
              ON source_domain.entity_id=edge.source_entity_id
            JOIN domain_memberships AS target_domain
              ON target_domain.entity_id=edge.target_entity_id
             AND target_domain.domain_id<>source_domain.domain_id
            GROUP BY edge.source_entity_id
            ORDER BY degree DESC, edge.source_entity_id LIMIT 20
            """,
            "OUT",
        ),
        "MEDIAN_DEGREE": (
            """
            WITH degrees AS (
              SELECT source_entity_id AS entity_id, COUNT(*) AS degree
              FROM edges GROUP BY source_entity_id
            ), ranked AS (
              SELECT entity_id, degree,
                     ROW_NUMBER() OVER (ORDER BY degree, entity_id) AS rank,
                     COUNT(*) OVER () AS total
              FROM degrees
            )
            SELECT entity_id, degree FROM ranked
            ORDER BY ABS(rank - ((total + 1) / 2.0)), entity_id
            LIMIT 20
            """,
            "OUT",
        ),
    }
    for label, (sql, direction) in queries.items():
        samples.extend(
            (label, int(row[0]), direction)
            for row in connection.execute(sql)
        )
    return samples


def _degree_latency(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    by_path: dict[str, list[float]] = defaultdict(list)
    for path, entity_id, direction in _degree_samples(connection):
        started = time.perf_counter()
        if direction == "OUT":
            list(
                connection.execute(
                    """
                    SELECT second.target_entity_id
                    FROM edges AS first
                    JOIN edges AS second
                      ON second.source_entity_id=first.target_entity_id
                    WHERE first.source_entity_id=? LIMIT 500
                    """,
                    (entity_id,),
                )
            )
        else:
            list(
                connection.execute(
                    """
                    SELECT second.source_entity_id
                    FROM edges AS first
                    JOIN edges AS second
                      ON second.target_entity_id=first.source_entity_id
                    WHERE first.target_entity_id=? LIMIT 500
                    """,
                    (entity_id,),
                )
            )
        by_path[path].append((time.perf_counter() - started) * 1_000)
    all_values = [
        latency for values in by_path.values() for latency in values
    ]
    return {
        "samples": len(all_values),
        "p50": round(_percentile(all_values, 0.50), 3),
        "p95": round(_percentile(all_values, 0.95), 3),
        "p99": round(_percentile(all_values, 0.99), 3),
        "byPath": {
            path: {
                "samples": len(values),
                "p95": round(_percentile(values, 0.95), 3),
            }
            for path, values in sorted(by_path.items())
        },
    }


def _verify_materialized_rows(
    rows: Sequence[sqlite3.Row],
    cases: Sequence[BenchmarkCase],
) -> None:
    by_id = {case.query_id: case for case in cases}
    if len(rows) != len(cases):
        raise ValueError(
            "Snapshot benchmark row count does not match fixed gold corpus"
        )
    for row in rows:
        query_id = str(row["query_id"])
        case = by_id.get(query_id)
        if case is None:
            raise ValueError(
                f"Snapshot has non-gold benchmark query {query_id}"
            )
        actual = {
            "question": str(row["question"]),
            "tier": str(row["tier"]),
            "primary_domain": str(row["primary_domain"]),
            "expected_answer_type": str(row["expected_answer_type"]),
            "expected_gap_code": str(row["expected_gap_code"]),
            "query_json": str(row["query_json"]),
            "negative_case": str(row["negative_case"]),
        }
        expected = {
            "question": case.question,
            "tier": case.category,
            "primary_domain": case.primary_domain,
            "expected_answer_type": case.expected_answer_type,
            "expected_gap_code": case.expected_gap_code,
            "query_json": _encoded_query(case),
            "negative_case": case.negative_case,
        }
        if actual != expected:
            raise ValueError(
                f"Snapshot benchmark row {query_id} differs from gold"
            )


def run_query_benchmark(
    core_path: Path,
    *,
    gold_set_path: Path = DEFAULT_GOLD_SET_PATH,
) -> dict[str, object]:
    gold = load_benchmark_gold_set(gold_set_path)
    cases = list(gold["cases"])
    cases_by_id = {case.query_id: case for case in cases}
    cold_started = time.perf_counter()
    connection = sqlite3.connect(
        f"file:{core_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("SELECT 1").fetchone()
    cold_ms = (time.perf_counter() - cold_started) * 1_000
    warm_started = time.perf_counter()
    connection.execute("SELECT 1").fetchone()
    warm_ms = (time.perf_counter() - warm_started) * 1_000
    results: list[dict[str, object]] = []
    latencies: list[float] = []
    path_latencies: dict[str, list[float]] = defaultdict(list)
    try:
        rows = list(
            connection.execute(
                "SELECT * FROM benchmark_queries ORDER BY query_id"
            )
        )
        _verify_materialized_rows(rows, cases)
        for row in rows:
            case = cases_by_id[str(row["query_id"])]
            started = time.perf_counter()
            result = plan_query(connection, _requirements(case.request))
            context = build_bounded_context_pack(
                result,
                budget_tokens=int(case.request["budgetTokens"]),
            )
            elapsed_ms = (time.perf_counter() - started) * 1_000
            latencies.append(elapsed_ms)
            if case.performance_path:
                path_latencies[case.performance_path].append(elapsed_ms)
            classification = evaluate_benchmark_result(case, result)
            missing = result.get("missingRequirements", [])
            gap_codes = sorted(
                {
                    str(item.get("code"))
                    for item in missing
                    if isinstance(item, dict) and item.get("code")
                }
            )
            results.append(
                {
                    "queryId": case.query_id,
                    "category": case.category,
                    "tier": case.category,
                    "primaryDomain": case.primary_domain,
                    "reviewStatus": case.review_status,
                    "protocolBoundaryOnly": (
                        case.protocol_boundary_only
                    ),
                    "negativeCase": case.negative_case,
                    "performancePath": case.performance_path,
                    "answerMode": str(result.get("answerMode") or ""),
                    "status": str(result.get("status") or ""),
                    "route": str(result.get("route") or ""),
                    "gapCodes": gap_codes,
                    "probeCount": len(
                        result.get("recommendedProbes", [])
                    ),
                    "contextTokens": int(context["estimatedTokens"]),
                    "latencyMs": round(elapsed_ms, 3),
                    **classification,
                }
            )
        degree_latency = _degree_latency(connection)
    finally:
        connection.close()
    metrics = {
        "protocolCompliance": _metric(
            results,
            "protocolCompliance",
        ),
        "identityAnswer": _metric(
            results,
            "identityAnswer",
            eligible="identityEligible",
        ),
        "semanticAnswer": _metric(
            results,
            "semanticAnswer",
            eligible="semanticEligible",
        ),
        "usableValueAnswer": _metric(
            results,
            "usableValue",
            eligible="usableValueEligible",
        ),
        "evidenceBackedComplete": _metric(
            results,
            "evidenceBackedComplete",
            eligible="semanticEligible",
        ),
        "gapOnly": _metric(results, "gapOnly"),
        "expectedGapMatched": _metric(
            results,
            "expectedGapMatched",
            eligible="gapExpectedEligible",
        ),
        "wrongAnswer": _metric(results, "wrongAnswer"),
        "ambiguousAnswer": _metric(results, "ambiguousAnswer"),
        "expectedAmbiguousAnswer": _metric(
            results,
            "expectedAmbiguousAnswer",
        ),
        "staleLeak": _metric(results, "staleLeak"),
        "candidateEdgeComplete": _metric(
            results,
            "candidateEdgeComplete",
        ),
    }
    context_max = max(
        (int(item["contextTokens"]) for item in results),
        default=0,
    )
    protocol = metrics["protocolCompliance"]
    identity = metrics["identityAnswer"]
    semantic = metrics["semanticAnswer"]
    usable = metrics["usableValueAnswer"]
    evidence_complete = metrics["evidenceBackedComplete"]
    gap_only = metrics["gapOnly"]
    expected_gap = metrics["expectedGapMatched"]
    wrong = metrics["wrongAnswer"]
    ambiguous = metrics["ambiguousAnswer"]
    expected_ambiguous = metrics["expectedAmbiguousAnswer"]
    stale = metrics["staleLeak"]
    candidate = metrics["candidateEdgeComplete"]
    return {
        "schema": BENCHMARK_SCHEMA,
        "goldSet": {
            "schema": GOLD_SET_SCHEMA,
            "sha256": gold["corpusSha256"],
            "selectionMode": gold["selectionMode"],
            "generatedFromCore": gold["generatedFromCore"],
            "fixedGoldCases": len(cases),
            "humanGoldCases": sum(
                case.review_status in {"HUMAN_REVIEWED", "EMPIRICAL"}
                for case in cases
            ),
            "protocolBoundaryOnlyCases": sum(
                case.protocol_boundary_only for case in cases
            ),
            "negativeGoldCases": sum(
                case.category == "NEGATIVE" for case in cases
            ),
            "reviewStatusCounts": dict(
                Counter(case.review_status for case in cases)
            ),
            "categoryCounts": dict(
                Counter(case.category for case in cases)
            ),
            "semanticExactByCategory": dict(
                Counter(
                    case.category
                    for case in cases
                    if case.expected["semanticExpectation"] == "EXACT"
                )
            ),
            "protocolBoundaryByCategory": dict(
                Counter(
                    case.category
                    for case in cases
                    if case.protocol_boundary_only
                )
            ),
            "corpusReadyForCutover": gold[
                "corpusReadyForCutover"
            ],
            "corpusGaps": gold["corpusGaps"],
            "limitations": gold["limitations"],
        },
        "total": len(results),
        "tierCounts": dict(
            Counter(str(item["category"]) for item in results)
        ),
        "routeCounts": dict(
            Counter(str(item["route"]) for item in results)
        ),
        "metrics": metrics,
        "protocolComplianceRate": protocol["rate"],
        "identityAnswerRate": identity["rate"],
        "semanticAnswerRate": semantic["rate"],
        "semanticExactMatchRate": semantic["rate"],
        "usableValueAnswerRate": usable["rate"],
        "evidenceBackedCompleteRate": evidence_complete["rate"],
        "gapOnlyRate": gap_only["rate"],
        "expectedGapMatchedRate": expected_gap["rate"],
        "wrongAnswerRate": wrong["rate"],
        "ambiguousAnswerRate": ambiguous["rate"],
        "unexpectedAmbiguousAnswerRate": ambiguous["rate"],
        "expectedAmbiguousAnswers": expected_ambiguous["count"],
        "staleLeakRate": stale["rate"],
        "candidateEdgeCompleteRate": candidate["rate"],
        "identityOnlyNotCountedAsSemantic": all(
            not item["semanticEligible"]
            for item in results
            if item["answerMode"] == "IDENTITY"
        ),
        "latencyMs": {
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "p99": round(_percentile(latencies, 0.99), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
            "coldConnection": round(cold_ms, 3),
            "warmConnection": round(warm_ms, 3),
            "twoHopP95": degree_latency["p95"],
            "twoHopP99": degree_latency["p99"],
            "twoHopSamples": degree_latency["samples"],
            "degreePaths": degree_latency["byPath"],
            "queryPaths": {
                path: {
                    "samples": len(values),
                    "p50": round(_percentile(values, 0.50), 3),
                    "p95": round(_percentile(values, 0.95), 3),
                    "p99": round(_percentile(values, 0.99), 3),
                }
                for path, values in sorted(path_latencies.items())
            },
        },
        "storagePathCoverage": {
            "core": True,
            "search": False,
            "cache": False,
            "complete": False,
            "gapCode": "SEARCH_CACHE_BENCHMARK_NOT_WIRED",
        },
        "contextTokens": {
            "maximum": context_max,
            "budget": 2_000,
            "withinBudget": context_max <= 2_000,
        },
        "results": results,
        # Deprecated v1 aliases: protocol success, never semantic coverage.
        "completeOrBounded": protocol["count"],
        "completeOrBoundedRate": protocol["rate"],
        "simpleDbOnly": identity["count"],
        "simpleDbOnlyRate": identity["rate"],
        "unresolved": len(results) - int(protocol["count"]),
    }
