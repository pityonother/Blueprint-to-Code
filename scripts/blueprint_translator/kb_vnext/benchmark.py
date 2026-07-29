"""Fixed expected-answer query benchmark for ARK KB vNext."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .gold_review import (
    GoldReviewError,
    load_trusted_reviewer_registry,
    validate_query_review_provenance,
)
from .kb_context import build_bounded_context_pack
from .map_usage import MAP_USAGE_EDGE_TYPES
from .profiling import SegmentTiming, measure_segment
from .registrations import GLOBAL_REGISTRATION_EDGE_TYPES
from .query_planner import (
    COMPLETE_CONFIDENCE,
    GAP_CODES,
    IDENTITY_COMPLETE_STATUSES,
    QueryRequirements,
    fact_value_is_usable,
    plan_query,
)
from .quality_contract import BENCHMARK_SCHEMA


GOLD_SET_SCHEMA = "ark-kb-query-gold-set/v1"
QUERY_CASE_RESULT_SCHEMA = "ark-kb-query-case-result/v1"
QUERY_FAILURE_MATRIX_SCHEMA = "ark-kb-query-failure-matrix/v1"
QUERY_DIAGNOSTICS_SCHEMA = "ark-kb-query-diagnostics/v1"
QUERY_FAILURE_CLASS_PRIORITY = (
    "PROTOCOL_VIOLATION",
    "WRONG_ANSWER",
    "STALE_LEAKAGE",
    "CANDIDATE_LEAKAGE",
    "LEGACY_LEAKAGE",
    "EXPECTED_GAP_MISMATCH",
    "IDENTITY_MISMATCH",
    "SEMANTIC_MISMATCH",
    "EVIDENCE_URI_MISMATCH",
    "AMBIGUOUS_ROUTING",
)
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
DEFAULT_TRUSTED_REVIEWER_REGISTRY_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "kb_trusted_reviewer_registry.v1.json"
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
PERFORMANCE_SAMPLE_TARGET = 20
SEARCH_FUZZY_P95_LIMIT_MS = 250.0
CACHE_HIT_P95_LIMIT_MS = 250.0
ONE_HOP_P95_LIMIT_MS = 250.0
TWO_HOP_P95_LIMIT_MS = 800.0


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
        requested = ", ".join(
            edge_types or list(MAP_USAGE_EDGE_TYPES)
        )
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
        or set(case.request["edgeTypes"])
        != GLOBAL_REGISTRATION_EDGE_TYPES
        or len(case.request["edgeTypes"])
        != len(GLOBAL_REGISTRATION_EDGE_TYPES)
    ):
        raise ValueError(
            f"{case.query_id}: REGISTRATION must request the explicit "
            "global/system registration edge set"
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


def _validate_empirical_cases(
    raw_cases: Sequence[object],
    *,
    trusted_reviewer_registry_path: Path | None,
) -> None:
    empirical_cases = [
        raw_case
        for raw_case in raw_cases
        if isinstance(raw_case, Mapping)
        and raw_case.get("reviewStatus") == "EMPIRICAL"
    ]
    if not empirical_cases:
        return
    registry_path = (
        trusted_reviewer_registry_path
        or DEFAULT_TRUSTED_REVIEWER_REGISTRY_PATH
    )
    if not registry_path.is_file():
        raise ValueError(
            "EMPIRICAL requires validated review provenance and a "
            "trusted reviewer registry"
        )
    try:
        trusted_reviewers = load_trusted_reviewer_registry(registry_path)
        for raw_case in empirical_cases:
            provenance = raw_case.get("reviewProvenance")
            if not isinstance(provenance, Mapping):
                raise GoldReviewError(
                    "EMPIRICAL requires validated review provenance"
                )
            validate_query_review_provenance(
                raw_case,
                provenance,
                trusted_reviewers=trusted_reviewers,
            )
    except GoldReviewError as error:
        raise ValueError(str(error)) from error


def load_benchmark_gold_set(
    path: Path = DEFAULT_GOLD_SET_PATH,
    *,
    projection_review_path: Path = DEFAULT_PROJECTION_REVIEW_PATH,
    trusted_reviewer_registry_path: Path | None = None,
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
    _validate_empirical_cases(
        raw_cases,
        trusted_reviewer_registry_path=trusted_reviewer_registry_path,
    )
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


def _latency_summary(values: Sequence[float]) -> dict[str, object]:
    return {
        "samples": len(values),
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "maximum": round(max(values), 3) if values else 0.0,
    }


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


def _stable_mapping_list(
    values: object,
) -> list[Mapping[str, object]]:
    if not isinstance(values, list):
        return []
    return [
        item for item in values if isinstance(item, Mapping)
    ]


def _stable_sort_key(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalized_expected_fact(
    fact: Mapping[str, object],
) -> dict[str, object]:
    return {
        "factType": str(fact.get("factType") or ""),
        "factName": str(fact.get("factName") or ""),
        "valueKind": str(fact.get("valueKind") or "").upper(),
        "value": fact.get("value"),
        "status": str(fact.get("status") or "").upper(),
        "evidenceUri": str(fact.get("evidenceUri") or ""),
    }


def _normalized_actual_fact(
    fact: Mapping[str, object],
) -> dict[str, object]:
    return {
        "factId": fact.get("factId"),
        "factType": str(fact.get("factType") or ""),
        "factName": str(fact.get("factName") or ""),
        "valueKind": str(fact.get("valueKind") or "").upper(),
        "value": _actual_fact_value(fact),
        "status": str(fact.get("status") or "").upper(),
        "confidence": str(fact.get("confidence") or "").upper(),
        "freshness": str(fact.get("freshness") or "").upper(),
    }


def _fact_key(fact: Mapping[str, object]) -> tuple[str, str]:
    return (
        str(fact.get("factType") or ""),
        str(fact.get("factName") or ""),
    )


def _fact_wrong_fields(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> dict[str, object]:
    fields: dict[str, object] = {}
    comparisons = (
        (
            "valueKind",
            str(expected.get("valueKind") or "").upper(),
            str(actual.get("valueKind") or "").upper(),
        ),
        ("value", expected.get("value"), _actual_fact_value(actual)),
        (
            "status",
            str(expected.get("status") or "").upper(),
            str(actual.get("status") or "").upper(),
        ),
    )
    for field, expected_value, actual_value in comparisons:
        if not _equal_value(expected_value, actual_value):
            fields[field] = {
                "expected": expected_value,
                "actual": actual_value,
            }
    actual_confidence = str(actual.get("confidence") or "").upper()
    if actual_confidence not in COMPLETE_CONFIDENCE:
        fields["confidence"] = {
            "expected": sorted(COMPLETE_CONFIDENCE),
            "actual": actual_confidence,
        }
    if not fact_value_is_usable(actual) and "value" not in fields:
        fields["usableValue"] = {
            "expected": True,
            "actual": False,
        }
    return fields


def _fact_diff(
    expected_facts: Sequence[Mapping[str, object]],
    actual_facts: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    unmatched_actual = list(actual_facts)
    missing: list[dict[str, object]] = []
    wrong: list[dict[str, object]] = []
    for expected in expected_facts:
        exact_index = next(
            (
                index
                for index, actual in enumerate(unmatched_actual)
                if _fact_matches(expected, actual)
            ),
            None,
        )
        if exact_index is not None:
            unmatched_actual.pop(exact_index)
            continue
        same_key_index = next(
            (
                index
                for index, actual in enumerate(unmatched_actual)
                if _fact_key(actual) == _fact_key(expected)
            ),
            None,
        )
        if same_key_index is None:
            missing.append(_normalized_expected_fact(expected))
            continue
        actual = unmatched_actual.pop(same_key_index)
        wrong.append(
            {
                "factType": _fact_key(expected)[0],
                "factName": _fact_key(expected)[1],
                "fields": _fact_wrong_fields(expected, actual),
            }
        )
    extra = [
        _normalized_actual_fact(actual)
        for actual in unmatched_actual
    ]
    return {
        "missing": sorted(missing, key=_stable_sort_key),
        "extra": sorted(extra, key=_stable_sort_key),
        "wrongValues": sorted(wrong, key=_stable_sort_key),
    }


def _normalized_expected_relationship(
    relationship: Mapping[str, object],
) -> dict[str, object]:
    normalized = {
        "edgeType": str(relationship.get("edgeType") or ""),
        "targetUri": str(relationship.get("targetUri") or ""),
        "status": str(relationship.get("status") or "").upper(),
        "evidenceUri": str(
            relationship.get("evidenceUri") or ""
        ),
    }
    for field in (
        "sourceUri",
        "freshness",
        "claimsCompleteMapUsage",
        "claimsSpawnCoordinates",
        "evidenceLayer",
    ):
        if field in relationship:
            normalized[field] = relationship.get(field)
    return normalized


def _normalized_actual_relationship(
    relationship: Mapping[str, object],
) -> dict[str, object]:
    return {
        "edgeId": relationship.get("edgeId"),
        "edgeType": str(relationship.get("edgeType") or ""),
        "sourceUri": str(relationship.get("sourceUri") or ""),
        "targetUri": str(relationship.get("targetUri") or ""),
        "status": str(relationship.get("status") or "").upper(),
        "confidence": str(
            relationship.get("confidence") or ""
        ).upper(),
        "freshness": str(
            relationship.get("freshness") or ""
        ).upper(),
        "evidenceUris": sorted(
            _relationship_evidence_uris(relationship)
        ),
    }


def _relationship_key(
    relationship: Mapping[str, object],
) -> tuple[str, str]:
    return (
        str(relationship.get("edgeType") or ""),
        str(relationship.get("targetUri") or ""),
    )


def _relationship_wrong_fields(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> dict[str, object]:
    fields: dict[str, object] = {}
    for field in (
        "sourceUri",
        "status",
        "freshness",
        "claimsCompleteMapUsage",
        "claimsSpawnCoordinates",
        "evidenceLayer",
    ):
        if field not in expected:
            continue
        expected_value = expected.get(field)
        actual_value = actual.get(field)
        if field in {"status", "freshness"}:
            expected_value = str(expected_value or "").upper()
            actual_value = str(actual_value or "").upper()
        if expected_value != actual_value:
            fields[field] = {
                "expected": expected_value,
                "actual": actual_value,
            }
    expected_evidence = str(expected.get("evidenceUri") or "")
    actual_evidence = sorted(_relationship_evidence_uris(actual))
    if expected_evidence not in actual_evidence:
        fields["evidenceUri"] = {
            "expected": expected_evidence,
            "actual": actual_evidence,
        }
    confidence = str(actual.get("confidence") or "").upper()
    if confidence not in {"HIGH", "CONFIRMED"}:
        fields["confidence"] = {
            "expected": ["CONFIRMED", "HIGH"],
            "actual": confidence,
        }
    if not _has_fresh_source_revision(actual):
        fields["freshSourceRevision"] = {
            "expected": True,
            "actual": False,
        }
    return fields


def _relationship_diff(
    expected_relationships: Sequence[Mapping[str, object]],
    actual_relationships: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    unmatched_actual = list(actual_relationships)
    missing: list[dict[str, object]] = []
    wrong: list[dict[str, object]] = []
    for expected in expected_relationships:
        exact_index = next(
            (
                index
                for index, actual in enumerate(unmatched_actual)
                if _relationship_matches(expected, actual)
            ),
            None,
        )
        if exact_index is not None:
            unmatched_actual.pop(exact_index)
            continue
        same_key_index = next(
            (
                index
                for index, actual in enumerate(unmatched_actual)
                if _relationship_key(actual)
                == _relationship_key(expected)
            ),
            None,
        )
        if same_key_index is None:
            missing.append(
                _normalized_expected_relationship(expected)
            )
            continue
        actual = unmatched_actual.pop(same_key_index)
        wrong.append(
            {
                "edgeType": _relationship_key(expected)[0],
                "targetUri": _relationship_key(expected)[1],
                "fields": _relationship_wrong_fields(
                    expected,
                    actual,
                ),
            }
        )
    extra = [
        _normalized_actual_relationship(actual)
        for actual in unmatched_actual
    ]
    return {
        "missing": sorted(missing, key=_stable_sort_key),
        "extra": sorted(extra, key=_stable_sort_key),
        "wrong": sorted(wrong, key=_stable_sort_key),
    }


def _actual_evidence_uris(
    result: Mapping[str, object],
) -> set[str]:
    uris = {
        str(item.get("evidenceUri") or "")
        for item in _stable_mapping_list(result.get("evidence"))
    }
    for relationship in _stable_mapping_list(
        result.get("relationships")
    ):
        uris.update(_relationship_evidence_uris(relationship))
    return {uri for uri in uris if uri}


def _evidence_uri_mismatch(
    case: BenchmarkCase,
    result: Mapping[str, object],
) -> dict[str, object]:
    expected_facts = _stable_mapping_list(
        case.expected.get("facts")
    )
    expected_relationships = _stable_mapping_list(
        case.expected.get("relationships")
    )
    expected_uris = {
        str(item.get("evidenceUri") or "")
        for item in (*expected_facts, *expected_relationships)
    }
    identity_evidence = case.expected.get("identityEvidence")
    if isinstance(identity_evidence, Mapping):
        expected_uris.add(
            str(identity_evidence.get("evidenceUri") or "")
        )
    expected_uris.discard("")
    actual_uris = _actual_evidence_uris(result)
    wrong_bindings: list[dict[str, object]] = []
    actual_evidence = _stable_mapping_list(result.get("evidence"))
    actual_facts = _stable_mapping_list(result.get("facts"))
    for expected in expected_facts:
        actual = next(
            (
                item
                for item in actual_facts
                if _fact_key(item) == _fact_key(expected)
            ),
            None,
        )
        if actual is None:
            continue
        fact_id = actual.get("factId")
        bound = sorted(
            {
                str(item.get("evidenceUri") or "")
                for item in actual_evidence
                if item.get("factId") == fact_id
                and item.get("evidenceUri")
            }
        )
        expected_uri = str(expected.get("evidenceUri") or "")
        if expected_uri and expected_uri not in bound:
            wrong_bindings.append(
                {
                    "kind": "FACT",
                    "key": {
                        "factType": _fact_key(expected)[0],
                        "factName": _fact_key(expected)[1],
                    },
                    "expected": expected_uri,
                    "actual": bound,
                }
            )
    actual_relationships = _stable_mapping_list(
        result.get("relationships")
    )
    for expected in expected_relationships:
        actual = next(
            (
                item
                for item in actual_relationships
                if _relationship_key(item)
                == _relationship_key(expected)
            ),
            None,
        )
        if actual is None:
            continue
        bound = sorted(_relationship_evidence_uris(actual))
        expected_uri = str(expected.get("evidenceUri") or "")
        if expected_uri and expected_uri not in bound:
            wrong_bindings.append(
                {
                    "kind": "RELATIONSHIP",
                    "key": {
                        "edgeType": _relationship_key(expected)[0],
                        "targetUri": _relationship_key(expected)[1],
                    },
                    "expected": expected_uri,
                    "actual": bound,
                }
            )
    return {
        "missing": sorted(expected_uris - actual_uris),
        "extra": sorted(actual_uris - expected_uris),
        "wrongBindings": sorted(
            wrong_bindings,
            key=_stable_sort_key,
        ),
    }


def _payload_has_marker(
    value: object,
    *,
    statuses: set[str] | None = None,
    freshness: set[str] | None = None,
    uri_prefixes: tuple[str, ...] = (),
) -> bool:
    if isinstance(value, Mapping):
        status = str(value.get("status") or "").upper()
        freshness_value = str(
            value.get("freshness") or ""
        ).upper()
        if statuses and status in statuses:
            return True
        if freshness and freshness_value in freshness:
            return True
        return any(
            _payload_has_marker(
                child,
                statuses=statuses,
                freshness=freshness,
                uri_prefixes=uri_prefixes,
            )
            for child in value.values()
        )
    if isinstance(value, list):
        return any(
            _payload_has_marker(
                child,
                statuses=statuses,
                freshness=freshness,
                uri_prefixes=uri_prefixes,
            )
            for child in value
        )
    if isinstance(value, str) and uri_prefixes:
        lowered = value.lower()
        return any(
            lowered.startswith(prefix) for prefix in uri_prefixes
        )
    return False


def _protocol_violations(
    case: BenchmarkCase,
    result: Mapping[str, object],
    classification: Mapping[str, bool],
) -> list[str]:
    violations: list[str] = []
    if str(result.get("answerMode") or "") != str(
        case.request.get("answerMode") or ""
    ):
        violations.append("ANSWER_MODE_MISMATCH")
    if str(result.get("route") or "") != str(
        case.expected.get("route") or ""
    ):
        violations.append("ROUTE_MISMATCH")
    semantic_expectation = str(
        case.expected.get("semanticExpectation") or ""
    )
    if (
        semantic_expectation == "GAP_ONLY"
        and not classification["expectedGapMatched"]
    ):
        violations.append("GAP_CONTRACT_MISMATCH")
    if (
        semantic_expectation == "IDENTITY_ONLY"
        and not classification["identityAnswer"]
    ):
        violations.append("IDENTITY_CONTRACT_MISMATCH")
    if not classification["protocolCompliance"] and not violations:
        violations.append("RESPONSE_SHAPE_MISMATCH")
    return violations


def build_query_case_result(
    case: BenchmarkCase,
    result: Mapping[str, object],
    *,
    latency_spans_ms: Mapping[str, float],
) -> dict[str, object]:
    """Build a stable, auditable per-case benchmark diagnostic."""

    classification = evaluate_benchmark_result(case, result)
    expected_facts = _stable_mapping_list(
        case.expected.get("facts")
    )
    actual_facts = _stable_mapping_list(result.get("facts"))
    expected_relationships = _stable_mapping_list(
        case.expected.get("relationships")
    )
    actual_relationships = _stable_mapping_list(
        result.get("relationships")
    )
    fact_diff = _fact_diff(expected_facts, actual_facts)
    relationship_diff = _relationship_diff(
        expected_relationships,
        actual_relationships,
    )
    expected_gap_codes = sorted(
        str(code)
        for code in case.expected.get("gapCodes", [])
    )
    missing_requirements = _stable_mapping_list(
        result.get("missingRequirements")
    )
    actual_gap_codes = sorted(
        str(item.get("code") or "")
        for item in missing_requirements
        if item.get("code")
    )
    evidence_mismatch = _evidence_uri_mismatch(case, result)
    protocol_violations = _protocol_violations(
        case,
        result,
        classification,
    )
    leakage = {
        "stale": _payload_has_marker(
            result,
            statuses={"STALE"},
            freshness={"STALE"},
        ),
        "candidate": _payload_has_marker(
            result,
            statuses={"CANDIDATE"},
        ),
        "legacy": _payload_has_marker(
            result,
            statuses={"LEGACY_UNVERIFIED", "LEGACY"},
            uri_prefixes=("existing-kb://", "legacy://"),
        ),
    }
    entity = result.get("entity")
    actual_identity = (
        str(entity.get("canonicalUri") or "")
        if isinstance(entity, Mapping)
        else ""
    )
    expected_identity = str(
        case.expected.get("identityUri") or ""
    )
    semantic_expectation = str(
        case.expected.get("semanticExpectation") or ""
    )
    has_fact_diff = any(
        bool(fact_diff[key])
        for key in ("missing", "extra", "wrongValues")
    )
    has_relationship_diff = any(
        bool(relationship_diff[key])
        for key in ("missing", "extra", "wrong")
    )
    has_evidence_mismatch = bool(
        evidence_mismatch["missing"]
        or evidence_mismatch["wrongBindings"]
    )
    present_failure_classes: set[str] = set()
    if protocol_violations:
        present_failure_classes.add("PROTOCOL_VIOLATION")
    if classification["wrongAnswer"]:
        present_failure_classes.add("WRONG_ANSWER")
    if leakage["stale"]:
        present_failure_classes.add("STALE_LEAKAGE")
    if leakage["candidate"]:
        present_failure_classes.add("CANDIDATE_LEAKAGE")
    if leakage["legacy"]:
        present_failure_classes.add("LEGACY_LEAKAGE")
    if (
        semantic_expectation == "GAP_ONLY"
        and not classification["expectedGapMatched"]
    ):
        present_failure_classes.add("EXPECTED_GAP_MISMATCH")
    if (
        expected_identity
        and actual_identity != expected_identity
    ):
        present_failure_classes.add("IDENTITY_MISMATCH")
    if (
        semantic_expectation == "EXACT"
        and (
            has_fact_diff
            or has_relationship_diff
            or not classification["semanticAnswer"]
        )
    ):
        present_failure_classes.add("SEMANTIC_MISMATCH")
    if has_evidence_mismatch:
        present_failure_classes.add("EVIDENCE_URI_MISMATCH")
    if classification["ambiguousAnswer"]:
        present_failure_classes.add("AMBIGUOUS_ROUTING")
    failure_classes = [
        name
        for name in QUERY_FAILURE_CLASS_PRIORITY
        if name in present_failure_classes
    ]
    normalized_spans = {
        str(name): round(float(value), 3)
        for name, value in sorted(latency_spans_ms.items())
    }
    return {
        "schema": QUERY_CASE_RESULT_SCHEMA,
        "caseId": case.query_id,
        "category": case.category,
        "tier": case.category,
        "domain": case.primary_domain,
        "reviewStatus": case.review_status,
        "protocolBoundaryOnly": case.protocol_boundary_only,
        "negativeCase": case.negative_case,
        "performancePath": case.performance_path,
        "expected": {
            "answerMode": str(
                case.request.get("answerMode") or ""
            ),
            "semanticExpectation": semantic_expectation,
            "route": str(case.expected.get("route") or ""),
            "identity": expected_identity,
            "facts": [
                _normalized_expected_fact(item)
                for item in expected_facts
            ],
            "relationships": [
                _normalized_expected_relationship(item)
                for item in expected_relationships
            ],
            "gapCodes": expected_gap_codes,
        },
        "actual": {
            "answerMode": str(result.get("answerMode") or ""),
            "status": str(result.get("status") or ""),
            "route": str(result.get("route") or ""),
            "identity": actual_identity,
            "facts": [
                _normalized_actual_fact(item)
                for item in actual_facts
            ],
            "relationships": [
                _normalized_actual_relationship(item)
                for item in actual_relationships
            ],
            "gapCodes": actual_gap_codes,
            "missingRequirements": [
                dict(item) for item in missing_requirements
            ],
            "recommendedProbes": [
                dict(item)
                for item in _stable_mapping_list(
                    result.get("recommendedProbes")
                )
            ],
        },
        "factDiff": fact_diff,
        "relationshipDiff": relationship_diff,
        "gapCodeDiff": {
            "expected": expected_gap_codes,
            "actual": actual_gap_codes,
            "missing": sorted(
                set(expected_gap_codes) - set(actual_gap_codes)
            ),
            "extra": sorted(
                set(actual_gap_codes) - set(expected_gap_codes)
            ),
        },
        "evidenceUriMismatch": evidence_mismatch,
        "leakage": leakage,
        "protocolViolations": protocol_violations,
        "latencySpansMs": normalized_spans,
        "failureClass": (
            failure_classes[0] if failure_classes else "PASS"
        ),
        "failureClasses": failure_classes,
        **classification,
    }


def query_case_results_jsonl_bytes(
    case_results: Sequence[Mapping[str, object]],
) -> bytes:
    """Serialize per-case diagnostics in stable case-id order."""

    by_case_id: dict[str, Mapping[str, object]] = {}
    for index, case_result in enumerate(case_results):
        if (
            str(case_result.get("schema") or "")
            != QUERY_CASE_RESULT_SCHEMA
        ):
            raise ValueError(
                f"Query case result {index} has an unknown schema"
            )
        case_id = str(case_result.get("caseId") or "")
        if not case_id:
            raise ValueError(
                f"Query case result {index} has no caseId"
            )
        if case_id in by_case_id:
            raise ValueError(
                f"Query case results contain duplicate caseId {case_id}"
            )
        by_case_id[case_id] = case_result
    return b"".join(
        (
            json.dumps(
                by_case_id[case_id],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for case_id in sorted(by_case_id)
    )


def _failure_group_summary(
    case_results: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    primary_counts = Counter(
        str(item.get("failureClass") or "")
        for item in case_results
    )
    failing = sum(
        str(item.get("failureClass") or "") != "PASS"
        for item in case_results
    )
    return {
        "total": len(case_results),
        "passing": len(case_results) - failing,
        "failing": failing,
        "primaryFailureClassCounts": dict(
            sorted(primary_counts.items())
        ),
    }


def build_query_failure_matrix(
    case_results: Sequence[Mapping[str, object]],
    *,
    build_id: str,
    corpus_sha256: str,
) -> dict[str, object]:
    """Aggregate deterministic failure classes without hiding case detail."""

    if not build_id:
        raise ValueError("Query failure matrix requires buildId")
    if len(corpus_sha256) != 64 or any(
        character not in "0123456789abcdef"
        for character in corpus_sha256.lower()
    ):
        raise ValueError(
            "Query failure matrix requires corpus sha256"
        )
    encoded_results = query_case_results_jsonl_bytes(case_results)
    ordered = sorted(
        case_results,
        key=lambda item: str(item.get("caseId") or ""),
    )
    passing = sum(
        str(item.get("failureClass") or "") == "PASS"
        for item in ordered
    )
    primary_counts = Counter(
        str(item.get("failureClass") or "")
        for item in ordered
    )
    all_counts = Counter(
        str(failure_class)
        for item in ordered
        for failure_class in item.get("failureClasses", [])
    )
    by_category: dict[str, list[Mapping[str, object]]] = defaultdict(
        list
    )
    by_domain: dict[str, list[Mapping[str, object]]] = defaultdict(
        list
    )
    for item in ordered:
        by_category[str(item.get("category") or "")].append(item)
        by_domain[str(item.get("domain") or "")].append(item)
    failures = [
        {
            "caseId": str(item.get("caseId") or ""),
            "category": str(item.get("category") or ""),
            "domain": str(item.get("domain") or ""),
            "failureClass": str(
                item.get("failureClass") or ""
            ),
            "failureClasses": list(
                item.get("failureClasses", [])
            ),
            "protocolViolations": list(
                item.get("protocolViolations", [])
            ),
            "leakage": dict(item.get("leakage", {})),
            "latencySpansMs": dict(
                item.get("latencySpansMs", {})
            ),
        }
        for item in ordered
        if str(item.get("failureClass") or "") != "PASS"
    ]
    return {
        "schema": QUERY_FAILURE_MATRIX_SCHEMA,
        "buildId": build_id,
        "corpus": {
            "sha256": corpus_sha256,
            "caseCount": len(ordered),
        },
        "caseResults": {
            "schema": QUERY_CASE_RESULT_SCHEMA,
            "sha256": hashlib.sha256(
                encoded_results
            ).hexdigest(),
            "count": len(ordered),
        },
        "totals": {
            "cases": len(ordered),
            "passing": passing,
            "failing": len(ordered) - passing,
        },
        "primaryFailureClassCounts": dict(
            sorted(primary_counts.items())
        ),
        "failureClassCounts": dict(sorted(all_counts.items())),
        "byCategory": {
            name: _failure_group_summary(values)
            for name, values in sorted(by_category.items())
        },
        "byDomain": {
            name: _failure_group_summary(values)
            for name, values in sorted(by_domain.items())
        },
        "failures": failures,
    }


def query_failure_matrix_json_bytes(
    failure_matrix: Mapping[str, object],
) -> bytes:
    if (
        str(failure_matrix.get("schema") or "")
        != QUERY_FAILURE_MATRIX_SCHEMA
    ):
        raise ValueError("Query failure matrix has an unknown schema")
    return (
        json.dumps(
            failure_matrix,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def query_diagnostic_artifact_bytes(
    benchmark: Mapping[str, object],
    *,
    expected_build_id: str | None = None,
) -> tuple[bytes, bytes]:
    """Recompute and validate the two diagnostic artifact byte streams."""

    if str(benchmark.get("schema") or "") != BENCHMARK_SCHEMA:
        raise ValueError("Query diagnostic benchmark schema is invalid")
    diagnostics = benchmark.get("diagnosticArtifacts")
    gold_set = benchmark.get("goldSet")
    raw_results = benchmark.get("results")
    if (
        not isinstance(diagnostics, Mapping)
        or not isinstance(gold_set, Mapping)
        or not isinstance(raw_results, list)
        or any(
            not isinstance(item, Mapping)
            for item in raw_results
        )
    ):
        raise ValueError(
            "Query diagnostic artifact contract is missing"
        )
    build_id = str(diagnostics.get("buildId") or "")
    corpus_sha256 = str(
        diagnostics.get("corpusSha256") or ""
    ).lower()
    if (
        diagnostics.get("schema") != QUERY_DIAGNOSTICS_SCHEMA
        or not build_id
        or (
            expected_build_id is not None
            and build_id != expected_build_id
        )
        or corpus_sha256
        != str(gold_set.get("sha256") or "").lower()
        or int(benchmark.get("total") or 0) != len(raw_results)
    ):
        raise ValueError(
            "Query diagnostic build or corpus binding is invalid"
        )
    case_results = [
        item for item in raw_results if isinstance(item, Mapping)
    ]
    case_bytes = query_case_results_jsonl_bytes(case_results)
    case_contract = diagnostics.get("caseResults")
    if (
        not isinstance(case_contract, Mapping)
        or case_contract.get("schema") != QUERY_CASE_RESULT_SCHEMA
        or case_contract.get("uri")
        != "reports/query_case_results.jsonl"
        or int(case_contract.get("count") or 0)
        != len(case_results)
        or str(case_contract.get("sha256") or "").lower()
        != hashlib.sha256(case_bytes).hexdigest()
    ):
        raise ValueError("query case results digest is invalid")
    matrix = build_query_failure_matrix(
        case_results,
        build_id=build_id,
        corpus_sha256=corpus_sha256,
    )
    matrix_bytes = query_failure_matrix_json_bytes(matrix)
    matrix_contract = diagnostics.get("failureMatrix")
    if (
        not isinstance(matrix_contract, Mapping)
        or matrix_contract.get("schema")
        != QUERY_FAILURE_MATRIX_SCHEMA
        or matrix_contract.get("uri")
        != "reports/query_failure_matrix.json"
        or int(matrix_contract.get("caseCount") or 0)
        != len(case_results)
        or str(matrix_contract.get("sha256") or "").lower()
        != hashlib.sha256(matrix_bytes).hexdigest()
    ):
        raise ValueError("query failure matrix digest is invalid")
    return case_bytes, matrix_bytes


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
) -> tuple[list[tuple[str, int, str, int]], dict[str, int]]:
    samples: list[tuple[str, int, str, int]] = []
    available = {
        "TOP_OUT_DEGREE": int(
            connection.execute(
                "SELECT COUNT(DISTINCT source_entity_id) FROM edges"
            ).fetchone()[0]
        ),
        "TOP_IN_DEGREE": int(
            connection.execute(
                "SELECT COUNT(DISTINCT target_entity_id) FROM edges"
            ).fetchone()[0]
        ),
        "TOP_CROSS_DOMAIN": int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT edge.source_entity_id
                  FROM edges AS edge
                  JOIN domain_memberships AS source_domain
                    ON source_domain.entity_id=edge.source_entity_id
                  JOIN domain_memberships AS target_domain
                    ON target_domain.entity_id=edge.target_entity_id
                   AND target_domain.domain_id<>source_domain.domain_id
                  GROUP BY edge.source_entity_id
                )
                """
            ).fetchone()[0]
        ),
        "RANDOM_MEDIAN_DEGREE": 0,
    }
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
            SELECT edge.source_entity_id,
                   COUNT(DISTINCT edge.edge_id) AS degree
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
    }
    for label, (sql, direction) in queries.items():
        samples.extend(
            (label, int(row[0]), direction, int(row[1]))
            for row in connection.execute(sql)
        )
    degrees = [
        (int(row[0]), int(row[1]))
        for row in connection.execute(
            """
            SELECT source_entity_id, COUNT(*) AS degree
            FROM edges
            GROUP BY source_entity_id
            ORDER BY degree, source_entity_id
            """
        )
    ]
    if degrees:
        median_degree = degrees[(len(degrees) - 1) // 2][1]
        median_pool = [
            item for item in degrees if item[1] == median_degree
        ]
        available["RANDOM_MEDIAN_DEGREE"] = len(median_pool)
        randomizer = random.Random(0xA4B5C6)
        selected = randomizer.sample(
            median_pool,
            k=min(PERFORMANCE_SAMPLE_TARGET, len(median_pool)),
        )
        samples.extend(
            ("RANDOM_MEDIAN_DEGREE", entity_id, "OUT", degree)
            for entity_id, degree in selected
        )
    return samples, available


def _degree_latency(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    one_hop: dict[str, list[float]] = defaultdict(list)
    two_hop: dict[str, list[float]] = defaultdict(list)
    degree_values: dict[str, list[int]] = defaultdict(list)
    samples, available = _degree_samples(connection)
    for path, entity_id, direction, degree in samples:
        degree_values[path].append(degree)
        started = time.perf_counter()
        if direction == "OUT":
            list(
                connection.execute(
                    """
                    SELECT target_entity_id
                    FROM edges
                    WHERE source_entity_id=?
                    LIMIT 500
                    """,
                    (entity_id,),
                )
            )
        else:
            list(
                connection.execute(
                    """
                    SELECT source_entity_id
                    FROM edges
                    WHERE target_entity_id=?
                    LIMIT 500
                    """,
                    (entity_id,),
                )
            )
        one_hop[path].append(
            (time.perf_counter() - started) * 1_000
        )

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
        two_hop[path].append(
            (time.perf_counter() - started) * 1_000
        )
    all_one_hop = [
        latency for values in one_hop.values() for latency in values
    ]
    all_two_hop = [
        latency for values in two_hop.values() for latency in values
    ]
    cohort_names = (
        "TOP_OUT_DEGREE",
        "TOP_IN_DEGREE",
        "TOP_CROSS_DOMAIN",
        "RANDOM_MEDIAN_DEGREE",
    )
    return {
        # Compatibility aliases continue to describe the two-hop path.
        **_latency_summary(all_two_hop),
        "oneHop": _latency_summary(all_one_hop),
        "twoHop": _latency_summary(all_two_hop),
        "byPath": {
            path: {
                "requested": PERFORMANCE_SAMPLE_TARGET,
                "available": available[path],
                "samples": len(two_hop.get(path, [])),
                "degreeMinimum": (
                    min(degree_values[path])
                    if degree_values.get(path)
                    else 0
                ),
                "degreeMaximum": (
                    max(degree_values[path])
                    if degree_values.get(path)
                    else 0
                ),
                "oneHop": _latency_summary(one_hop.get(path, [])),
                "twoHop": _latency_summary(two_hop.get(path, [])),
            }
            for path in cohort_names
        },
    }


def _copy_snapshot_for_benchmark(
    snapshot_root: Path,
    isolated_root: Path,
    *,
    allow_unsealed_snapshot: bool = False,
    timing: SegmentTiming | None = None,
) -> bool:
    snapshot_root = snapshot_root.resolve()
    isolated_root = isolated_root.resolve()
    # Local imports avoid snapshot -> storage -> benchmark import cycles.
    from .snapshot import (
        SNAPSHOT_SCHEMA,
        _safe_build_id,
        resolve_current_snapshot,
    )

    with measure_segment(timing, "pointerManifestResolution"):
        try:
            location = resolve_current_snapshot(snapshot_root)
            source_root = location.snapshot_dir
            manifest_path = location.manifest_path
            manifest = location.manifest
            build_id = location.build_id
            source_layout = location.layout
        except FileNotFoundError:
            manifest_path = snapshot_root / "manifest.json"
            if not manifest_path.is_file():
                raise
            try:
                raw_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Immutable snapshot manifest is unreadable"
                ) from exc
            if not isinstance(raw_manifest, dict):
                raise ValueError("Snapshot manifest must be an object")
            manifest = raw_manifest
            build_id = _safe_build_id(manifest.get("buildId"))
            if manifest.get("schema") != SNAPSHOT_SCHEMA:
                raise ValueError("Snapshot manifest schema is unknown")
            source_root = snapshot_root
            source_layout = "immutable-v2-direct"
    databases = manifest.get("databases")
    if not isinstance(databases, Mapping):
        raise ValueError("Snapshot manifest is missing build databases")

    if source_layout == "legacy-v1":
        destination_root = isolated_root
        manifests = isolated_root / "manifests"
        manifests.mkdir(parents=True)
        shutil.copy2(manifest_path, manifests / "current.json")
        shutil.copy2(
            manifest_path,
            manifests / f"{build_id}.json",
        )
    else:
        destination_root = isolated_root / "snapshots" / build_id
        destination_root.mkdir(parents=True)
        shutil.copy2(
            manifest_path,
            destination_root / "manifest.json",
        )
        (isolated_root / "current.json").write_text(
            json.dumps(
                {
                    "buildId": build_id,
                    "snapshotRelativePath": f"snapshots/{build_id}",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    for raw_name in databases:
        name = str(raw_name)
        relative = Path(name)
        canonical_root_names = {
            "cache.sqlite",
            "catalog.sqlite",
            "core.sqlite",
            "search.sqlite",
        }
        canonical_export = (
            len(relative.parts) == 2
            and relative.parts[0] == "domain_exports"
            and relative.parts[1].endswith(".sqlite")
            and relative.parts[1] not in {"", ".", ".."}
        )
        if (
            relative.is_absolute()
            or name not in canonical_root_names
            and not canonical_export
            or ".." in relative.parts
        ):
            raise ValueError(
                f"Unsafe snapshot database path: {name!r}"
            )
        source = (source_root / relative).resolve()
        destination = (destination_root / relative).resolve()
        if (
            not source.is_relative_to(source_root)
            or not destination.is_relative_to(destination_root)
        ):
            raise ValueError(
                f"Snapshot database escapes its root: {name!r}"
            )
        if not source.is_file():
            raise FileNotFoundError(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if name == "cache.sqlite":
            source_uri = (
                f"file:{source.resolve().as_posix()}?mode=ro"
            )
            with closing(
                sqlite3.connect(source_uri, uri=True)
            ) as source_cache:
                with closing(sqlite3.connect(destination)) as target_cache:
                    source_cache.backup(target_cache)
            continue
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
    if source_layout != "legacy-v1":
        quality = manifest.get("qualityGates")
        if not isinstance(quality, Mapping):
            if allow_unsealed_snapshot:
                return True
            raise ValueError(
                "Immutable benchmark snapshot has no sealed quality report"
            )
        report_keys = ["reportUri", "benchmarkUri"]
        diagnostic_keys = (
            "caseResultsUri",
            "failureMatrixUri",
        )
        if any(quality.get(key) for key in diagnostic_keys):
            if not all(quality.get(key) for key in diagnostic_keys):
                raise ValueError(
                    "Immutable benchmark snapshot has incomplete "
                    "query diagnostics"
                )
            report_keys.extend(diagnostic_keys)
        for key in report_keys:
            raw_name = str(quality.get(key) or "")
            relative = Path(raw_name)
            if (
                not raw_name
                or relative.is_absolute()
                or ".." in relative.parts
                or "\\" in raw_name
            ):
                raise ValueError(
                    f"Unsafe snapshot report path: {raw_name!r}"
                )
            source = (source_root / relative).resolve()
            destination = (destination_root / relative).resolve()
            if (
                not source.is_relative_to(source_root)
                or not destination.is_relative_to(destination_root)
                or not source.is_file()
            ):
                raise ValueError(
                    f"Snapshot report escapes its root: {raw_name!r}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return False


def _connection_latency(
    path: Path,
    *,
    sample_count: int,
    timing: SegmentTiming | None = None,
) -> dict[str, object]:
    cold: list[float] = []
    for _ in range(sample_count):
        started = time.perf_counter()
        with measure_segment(timing, "connectionAcquire"):
            connection = sqlite3.connect(
                f"file:{path.resolve().as_posix()}?mode=ro",
                uri=True,
            )
            try:
                if timing is not None:
                    connection.set_trace_callback(timing.record_query)
                connection.execute("SELECT 1").fetchone()
            finally:
                connection.close()
        cold.append((time.perf_counter() - started) * 1_000)

    warm: list[float] = []
    with measure_segment(timing, "connectionAcquire"):
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        if timing is not None:
            connection.set_trace_callback(timing.record_query)
        connection.execute("SELECT 1").fetchone()
    try:
        for _ in range(sample_count):
            started = time.perf_counter()
            connection.execute("SELECT 1").fetchone()
            warm.append((time.perf_counter() - started) * 1_000)
    finally:
        connection.close()
    return {
        "coldConnection": _latency_summary(cold),
        "warmConnection": _latency_summary(warm),
    }


def _search_probe_queries(
    service: object,
    search_path: Path,
) -> dict[str, str]:
    desired: dict[str, list[str]] = {
        "EXACT_CANONICAL_URI": [],
        "EXACT_ALIAS": [],
        "FTS_PHRASE": [],
        "FUZZY_CANDIDATE": [],
    }
    connection = sqlite3.connect(
        f"file:{search_path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        desired["EXACT_CANONICAL_URI"] = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT canonical_uri
                FROM entity_search_meta
                WHERE canonical_uri<>''
                ORDER BY entity_id
                LIMIT 100
                """
            )
        ]
        desired["EXACT_ALIAS"] = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT alias
                FROM search_aliases
                WHERE alias<>''
                ORDER BY entity_id, alias
                LIMIT 100
                """
            )
        ]
        phrase_candidates: list[str] = []
        fuzzy_sources: list[str] = []
        for row in connection.execute(
            """
            SELECT display_name, internal_name
            FROM entity_search_meta
            ORDER BY entity_id
            LIMIT 500
            """
        ):
            for value in (str(row[0]), str(row[1])):
                terms = tuple(
                    token
                    for token in value.replace("_", " ").split()
                    if token
                )
                if len(terms) >= 2:
                    phrase_candidates.append(" ".join(terms[:2]))
                fuzzy_sources.extend(terms)
                if value:
                    fuzzy_sources.append(value)
        for row in connection.execute(
            """
            SELECT alias
            FROM search_aliases
            WHERE alias<>''
            ORDER BY entity_id, alias
            LIMIT 500
            """
        ):
            fuzzy_sources.append(str(row[0]))
        desired["FTS_PHRASE"] = phrase_candidates
        fuzzy_candidates: list[str] = []
        for source in fuzzy_sources:
            compact = source.strip()
            if len(compact) < 5:
                continue
            fuzzy_candidates.append(
                compact[:2] + compact[3] + compact[2] + compact[4:]
            )
        desired["FUZZY_CANDIDATE"] = fuzzy_candidates
    finally:
        connection.close()

    selected: dict[str, str] = {}
    search_entities = getattr(service, "search_entities")
    for match_type, candidates in desired.items():
        seen: set[str] = set()
        for query in candidates:
            if not query or query in seen:
                continue
            seen.add(query)
            result = search_entities(query=query, limit=20)
            items = result.get("items", [])
            if any(
                isinstance(item, Mapping)
                and item.get("matchType") == match_type
                for item in items
            ):
                selected[match_type] = query
                break
    return selected


def _query_fingerprint(request: Mapping[str, object]) -> str:
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clear_cache_probe(cache_path: Path, fingerprint: str) -> None:
    snapshot_id = "query-snapshot://" + fingerprint
    with closing(sqlite3.connect(cache_path)) as cache:
        cache.execute(
            "DELETE FROM context_packs WHERE snapshot_id=?",
            (snapshot_id,),
        )
        cache.execute(
            "DELETE FROM query_snapshots WHERE query_fingerprint=?",
            (fingerprint,),
        )
        cache.commit()


def _cache_probe_request(search_path: Path) -> dict[str, object]:
    with closing(
        sqlite3.connect(
            f"file:{search_path.resolve().as_posix()}?mode=ro",
            uri=True,
        )
    ) as search:
        row = search.execute(
            """
            SELECT canonical_uri
            FROM entity_search_meta
            WHERE canonical_uri<>''
            ORDER BY entity_id
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise ValueError("Search store has no entity for cache probe")
    return {
        "entity": str(row[0]),
        "answerMode": "IDENTITY",
        "budgetTokens": 500,
    }


def _cache_reason(response: Mapping[str, object]) -> str:
    cache = response.get("cache")
    return (
        str(cache.get("reason") or "")
        if isinstance(cache, Mapping)
        else ""
    )


def _cache_status(response: Mapping[str, object]) -> str:
    cache = response.get("cache")
    return (
        str(cache.get("status") or "")
        if isinstance(cache, Mapping)
        else ""
    )


def _timing_diagnostics(
    timing: SegmentTiming,
    *,
    scope: str,
) -> dict[str, object]:
    report = timing.report()
    report["scope"] = scope
    if scope == "queryPlanner":
        report["coverage"] = {
            "factRequirementPlanning": "MEASURED",
            "identityLookup": "MEASURED",
            "factQuery": "MEASURED",
            "effectiveFactQuery": "MEASURED",
            "relationshipQuery": "MEASURED",
            "sourceRevisionValidation": "PARTIAL",
            "evidenceHydration": "PARTIAL",
            "answerContextSerialization": "MEASURED",
        }
        report["unseparatedSegments"] = {
            "inlineRevisionAndEvidenceChecks": {
                "includedIn": [
                    "factQuery",
                    "relationshipQuery",
                    "evidenceHydration",
                ],
                "reason": (
                    "URI, status, confidence, and some freshness checks are "
                    "intentionally evaluated inline with their fail-closed "
                    "gate and cannot be isolated without changing semantics."
                ),
            },
            "relationshipEvidenceProjection": {
                "includedIn": ["relationshipQuery"],
                "reason": (
                    "Relationship SQL projection and evidence hydration share "
                    "one planner operation and are reported together."
                ),
            },
        }
    else:
        report["coverage"] = {
            "pointerManifestResolution": "MEASURED",
            "connectionAcquire": "MEASURED",
            "cacheValidation": "MEASURED",
            "cacheWrite": "MEASURED",
        }
        report["unseparatedSegments"] = {
            "snapshotStructureValidation": {
                "includedIn": ["cacheValidation"],
                "reason": (
                    "Snapshot binding and cache revision validity are nested "
                    "fail-closed checks; cache validation is inclusive."
                ),
            }
        }
    return report


def _attach_timing_diagnostics(
    payload: dict[str, object],
    timing: SegmentTiming | None,
    *,
    scope: str,
) -> dict[str, object]:
    if timing is not None:
        payload["timingDiagnostics"] = _timing_diagnostics(
            timing,
            scope=scope,
        )
    return payload


def run_storage_path_benchmark(
    snapshot_root: Path,
    *,
    sample_count: int = PERFORMANCE_SAMPLE_TARGET,
    allow_unsealed_snapshot: bool = False,
    include_timing: bool = False,
) -> dict[str, object]:
    """Exercise Search and Cache through an isolated copy of a real snapshot."""

    if sample_count < 1:
        raise ValueError("sample_count must be at least 1")
    timing = SegmentTiming() if include_timing else None
    empty = {
        "sampleTarget": sample_count,
        "connections": {},
        "search": {
            "ftsPlanUsed": False,
            "paths": {},
            "coldOperation": _latency_summary([]),
            "warmOperation": _latency_summary([]),
        },
        "cache": {
            "validHit": False,
            "expiredRejected": False,
            "sourceRevisionRejected": False,
            "invalidationTokenRejected": False,
            "buildRejected": False,
            "miss": _latency_summary([]),
            "hit": _latency_summary([]),
            "coldOperation": _latency_summary([]),
            "warmOperation": _latency_summary([]),
        },
        "coverage": {
            "search": False,
            "cache": False,
            "complete": False,
        },
        "error": "",
    }
    try:
        # The local import avoids storage -> benchmark -> API -> storage cycles.
        from .kb_api import VNextKnowledgeService

        with tempfile.TemporaryDirectory(
            prefix="ark-kb-storage-benchmark-"
        ) as temporary:
            isolated_root = Path(temporary) / "snapshot"
            isolated_root.mkdir()
            copied_unsealed_snapshot = _copy_snapshot_for_benchmark(
                snapshot_root.resolve(),
                isolated_root,
                allow_unsealed_snapshot=allow_unsealed_snapshot,
                timing=timing,
            )
            service = VNextKnowledgeService(
                isolated_root,
                _allow_unsealed_benchmark=copied_unsealed_snapshot,
                _timing=timing,
            )
            runtime_health = service.health()
            if runtime_health.get("status") == "INVALID":
                gaps = runtime_health.get("gap")
                detail = next(
                    (
                        str(item.get("detail") or "")
                        for item in gaps
                        if isinstance(item, Mapping)
                        and item.get("code")
                        == "KB_VNEXT_SNAPSHOT_INVALID"
                    ),
                    "Snapshot runtime binding is invalid.",
                )
                raise ValueError(detail)
            active_root = service.root
            search_path = service.search_path
            cache_path = service.cache_path

            connections = {
                name: _connection_latency(
                    active_root / name,
                    sample_count=sample_count,
                    timing=timing,
                )
                for name in (
                    "core.sqlite",
                    "search.sqlite",
                    "cache.sqlite",
                )
            }

            with closing(
                sqlite3.connect(
                    f"file:{search_path.resolve().as_posix()}?mode=ro",
                    uri=True,
                )
            ) as search:
                fts_plan = " ".join(
                    str(row[3])
                    for row in search.execute(
                        """
                        EXPLAIN QUERY PLAN
                        SELECT rowid FROM entities_fts
                        WHERE entities_fts MATCH ?
                        """,
                        ('"benchmark"*',),
                    )
                )
            fts_plan_used = "VIRTUAL TABLE INDEX" in fts_plan.upper()
            queries = _search_probe_queries(
                service,
                search_path,
            )
            cold_search_values: list[float] = []
            cold_search_query = queries.get("EXACT_CANONICAL_URI")
            if cold_search_query is not None:
                cold_search_service = VNextKnowledgeService(
                    isolated_root,
                    _allow_unsealed_benchmark=copied_unsealed_snapshot,
                    _timing=timing,
                )
                started = time.perf_counter()
                cold_search_service.search_entities(
                    query=cold_search_query,
                    limit=20,
                )
                cold_search_values.append(
                    (time.perf_counter() - started) * 1_000
                )
            search_paths: dict[str, dict[str, object]] = {}
            for match_type in (
                "EXACT_CANONICAL_URI",
                "EXACT_ALIAS",
                "FTS_PHRASE",
                "FUZZY_CANDIDATE",
            ):
                query = queries.get(match_type)
                values: list[float] = []
                observed = False
                if query is not None:
                    for _ in range(sample_count):
                        started = time.perf_counter()
                        result = service.search_entities(
                            query=query,
                            limit=20,
                        )
                        values.append(
                            (time.perf_counter() - started) * 1_000
                        )
                        observed = observed or any(
                            isinstance(item, Mapping)
                            and item.get("matchType") == match_type
                            for item in result.get("items", [])
                        )
                search_paths[match_type] = {
                    "queryAvailable": query is not None,
                    "matchTypeObserved": observed,
                    **_latency_summary(values),
                }

            request = _cache_probe_request(search_path)
            fingerprint = _query_fingerprint(request)
            _clear_cache_probe(cache_path, fingerprint)
            cache_service = VNextKnowledgeService(
                isolated_root,
                _allow_unsealed_benchmark=copied_unsealed_snapshot,
                _timing=timing,
            )
            started = time.perf_counter()
            first = cache_service.query(request)
            cold_cache_ms = (
                time.perf_counter() - started
            ) * 1_000
            second = cache_service.query(request)
            valid_hit = (
                _cache_status(first) == "MISS"
                and _cache_status(second) == "HIT"
                and _cache_reason(second) == "VALID"
            )

            snapshot_id = "query-snapshot://" + fingerprint
            with closing(sqlite3.connect(cache_path)) as cache:
                cache.execute(
                    """
                    UPDATE query_snapshots
                    SET expires_at='2000-01-01T00:00:00+00:00'
                    WHERE snapshot_id=?
                    """,
                    (snapshot_id,),
                )
                cache.commit()
            expired = cache_service.query(request)

            with closing(sqlite3.connect(cache_path)) as cache:
                cache.execute(
                    """
                    UPDATE query_snapshots
                    SET source_revision_set_hash=?
                    WHERE snapshot_id=?
                    """,
                    ("0" * 64, snapshot_id),
                )
                cache.commit()
            source_revision = cache_service.query(request)

            with closing(sqlite3.connect(cache_path)) as cache:
                cache.execute(
                    """
                    UPDATE query_snapshots
                    SET invalidation_token=?
                    WHERE snapshot_id=?
                    """,
                    ("0" * 64, snapshot_id),
                )
                cache.commit()
            invalidation = cache_service.query(request)

            with closing(sqlite3.connect(cache_path)) as cache:
                row = cache.execute(
                    """
                    SELECT value FROM metadata
                    WHERE key='snapshot_build_id'
                    """
                ).fetchone()
                if row is None:
                    raise ValueError(
                        "Cache metadata has no snapshot_build_id"
                    )
                build_id = str(row[0])
                cache.execute(
                    """
                    UPDATE metadata SET value='benchmark-build-mismatch'
                    WHERE key='snapshot_build_id'
                    """
                )
                cache.commit()
            build = cache_service.query(request)
            with closing(sqlite3.connect(cache_path)) as cache:
                cache.execute(
                    """
                    UPDATE metadata SET value=?
                    WHERE key='snapshot_build_id'
                    """,
                    (build_id,),
                )
                cache.commit()

            misses: list[float] = []
            hits: list[float] = []
            miss_observed = True
            hit_observed = True
            for _ in range(sample_count):
                _clear_cache_probe(cache_path, fingerprint)
                started = time.perf_counter()
                miss = cache_service.query(request)
                misses.append(
                    (time.perf_counter() - started) * 1_000
                )
                miss_observed = (
                    miss_observed
                    and _cache_status(miss) == "MISS"
                )
                started = time.perf_counter()
                hit = cache_service.query(request)
                hits.append(
                    (time.perf_counter() - started) * 1_000
                )
                hit_observed = (
                    hit_observed
                    and _cache_status(hit) == "HIT"
                    and _cache_reason(hit) == "VALID"
                )

            cache_result = {
                "validHit": valid_hit,
                "expiredRejected": (
                    _cache_status(expired) == "MISS"
                    and _cache_reason(expired) == "EXPIRED"
                ),
                "sourceRevisionRejected": (
                    _cache_status(source_revision) == "MISS"
                    and _cache_reason(source_revision)
                    == "SOURCE_REVISION_SET_CHANGED"
                ),
                "invalidationTokenRejected": (
                    _cache_status(invalidation) == "MISS"
                    and _cache_reason(invalidation)
                    == "INVALIDATION_TOKEN_CHANGED"
                ),
                "buildRejected": (
                    _cache_status(build) == "MISS"
                    and _cache_reason(build) == "BUILD_MISMATCH"
                ),
                "missObserved": miss_observed,
                "hitObserved": hit_observed,
                "miss": _latency_summary(misses),
                "hit": _latency_summary(hits),
                "coldOperation": _latency_summary(
                    [cold_cache_ms]
                ),
                "warmOperation": _latency_summary(hits),
            }
            search_complete = (
                fts_plan_used
                and all(
                    path["queryAvailable"]
                    and path["matchTypeObserved"]
                    and int(path["samples"]) == sample_count
                    for path in search_paths.values()
                )
            )
            cache_complete = all(
                bool(cache_result[key])
                for key in (
                    "validHit",
                    "expiredRejected",
                    "sourceRevisionRejected",
                    "invalidationTokenRejected",
                    "buildRejected",
                    "missObserved",
                    "hitObserved",
                )
            )
            return _attach_timing_diagnostics({
                "sampleTarget": sample_count,
                "connections": connections,
                "search": {
                    "ftsPlanUsed": fts_plan_used,
                    "paths": search_paths,
                    "coldOperation": _latency_summary(
                        cold_search_values
                    ),
                    "warmOperation": dict(
                        search_paths.get(
                            "EXACT_CANONICAL_URI",
                            _latency_summary([]),
                        )
                    ),
                },
                "cache": cache_result,
                "coverage": {
                    "search": search_complete,
                    "cache": cache_complete,
                    "complete": search_complete and cache_complete,
                },
                "error": "",
            }, timing, scope="storage")
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.DatabaseError,
    ) as error:
        return _attach_timing_diagnostics({
            **empty,
            "error": f"{type(error).__name__}: {error}",
        }, timing, scope="storage")


def _runtime_performance_gates(
    storage: Mapping[str, object],
    degree: Mapping[str, object],
) -> dict[str, object]:
    raw_search = storage.get("search")
    search = raw_search if isinstance(raw_search, Mapping) else {}
    raw_paths = search.get("paths")
    paths = raw_paths if isinstance(raw_paths, Mapping) else {}
    raw_fuzzy = paths.get("FUZZY_CANDIDATE")
    fuzzy = raw_fuzzy if isinstance(raw_fuzzy, Mapping) else {}
    raw_cache = storage.get("cache")
    cache = raw_cache if isinstance(raw_cache, Mapping) else {}
    raw_hit = cache.get("hit")
    hit = raw_hit if isinstance(raw_hit, Mapping) else {}
    raw_one_hop = degree.get("oneHop")
    one_hop = (
        raw_one_hop if isinstance(raw_one_hop, Mapping) else {}
    )
    raw_two_hop = degree.get("twoHop")
    two_hop = (
        raw_two_hop if isinstance(raw_two_hop, Mapping) else {}
    )
    raw_degree_paths = degree.get("byPath")
    degree_paths = (
        raw_degree_paths
        if isinstance(raw_degree_paths, Mapping)
        else {}
    )
    required_cohorts = (
        "TOP_OUT_DEGREE",
        "TOP_IN_DEGREE",
        "TOP_CROSS_DOMAIN",
        "RANDOM_MEDIAN_DEGREE",
    )
    cohort_actual: dict[str, dict[str, int]] = {}
    cohort_coverage = True
    for name in required_cohorts:
        raw_cohort = degree_paths.get(name)
        cohort = (
            raw_cohort if isinstance(raw_cohort, Mapping) else {}
        )
        try:
            requested = int(cohort.get("requested"))
            available = int(cohort.get("available"))
            samples = int(cohort.get("samples"))
        except (TypeError, ValueError):
            requested = 0
            available = 0
            samples = 0
        required = min(requested, available)
        cohort_actual[name] = {
            "requested": requested,
            "available": available,
            "required": required,
            "samples": samples,
        }
        cohort_coverage = (
            cohort_coverage
            and requested == PERFORMANCE_SAMPLE_TARGET
            and available > 0
            and samples == required
        )

    def threshold_check(
        metrics: Mapping[str, object],
        *,
        limit: float,
        minimum_samples: int,
    ) -> dict[str, object]:
        try:
            actual = float(metrics.get("p95"))
            samples = int(metrics.get("samples"))
        except (TypeError, ValueError):
            actual = 0.0
            samples = 0
        return {
            "target": f"<{limit:g} ms with >= {minimum_samples} samples",
            "actual": {"p95Ms": actual, "samples": samples},
            "passed": samples >= minimum_samples and actual < limit,
        }

    checks: dict[str, dict[str, object]] = {
        "ftsPlanUsed": {
            "target": True,
            "actual": search.get("ftsPlanUsed"),
            "passed": search.get("ftsPlanUsed") is True,
        },
        "cacheValidHit": {
            "target": True,
            "actual": cache.get("validHit"),
            "passed": cache.get("validHit") is True,
        },
        "cacheExpiredRejected": {
            "target": True,
            "actual": cache.get("expiredRejected"),
            "passed": cache.get("expiredRejected") is True,
        },
        "cacheSourceRevisionRejected": {
            "target": True,
            "actual": cache.get("sourceRevisionRejected"),
            "passed": cache.get("sourceRevisionRejected") is True,
        },
        "cacheInvalidationTokenRejected": {
            "target": True,
            "actual": cache.get("invalidationTokenRejected"),
            "passed": cache.get("invalidationTokenRejected") is True,
        },
        "cacheBuildRejected": {
            "target": True,
            "actual": cache.get("buildRejected"),
            "passed": cache.get("buildRejected") is True,
        },
        "degreeCohortsCovered": {
            "target": (
                "all available members, up to 20, from each required cohort"
            ),
            "actual": cohort_actual,
            "passed": cohort_coverage,
        },
        "fuzzyP95": threshold_check(
            fuzzy,
            limit=SEARCH_FUZZY_P95_LIMIT_MS,
            minimum_samples=PERFORMANCE_SAMPLE_TARGET,
        ),
        "cacheHitP95": threshold_check(
            hit,
            limit=CACHE_HIT_P95_LIMIT_MS,
            minimum_samples=PERFORMANCE_SAMPLE_TARGET,
        ),
        "oneHopP95": threshold_check(
            one_hop,
            limit=ONE_HOP_P95_LIMIT_MS,
            minimum_samples=1,
        ),
        "twoHopP95": threshold_check(
            two_hop,
            limit=TWO_HOP_P95_LIMIT_MS,
            minimum_samples=1,
        ),
    }
    return {
        "checks": checks,
        "passed": all(
            bool(check.get("passed")) for check in checks.values()
        ),
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
    allow_unsealed_snapshot: bool = False,
    include_timing: bool = False,
) -> dict[str, object]:
    timing = SegmentTiming() if include_timing else None
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
    search_path = core_path.with_name("search.sqlite")
    search_connection: sqlite3.Connection | None = None
    if search_path.is_file():
        search_connection = sqlite3.connect(
            f"file:{search_path.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        search_connection.row_factory = sqlite3.Row
        search_connection.execute("PRAGMA query_only=ON")
        search_connection.execute("SELECT 1").fetchone()
    cold_ms = (time.perf_counter() - cold_started) * 1_000
    build_row = connection.execute(
        """
        SELECT value
        FROM metadata
        WHERE key='snapshot_build_id'
        """
    ).fetchone()
    if build_row is not None and str(build_row[0] or ""):
        build_id = str(build_row[0])
        build_binding = "SNAPSHOT_METADATA"
    else:
        build_id = (
            "unsealed-core-"
            + hashlib.sha256(core_path.read_bytes()).hexdigest()[:16]
        )
        build_binding = "CORE_SHA256_FALLBACK"
    warm_started = time.perf_counter()
    connection.execute("SELECT 1").fetchone()
    if search_connection is not None:
        search_connection.execute("SELECT 1").fetchone()
    if timing is not None:
        connection.set_trace_callback(timing.record_query)
        if search_connection is not None:
            search_connection.set_trace_callback(timing.record_query)
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
            planner_started = time.perf_counter()
            result = plan_query(
                connection,
                _requirements(case.request),
                search_connection=search_connection,
                timing=timing,
            )
            planner_ms = (
                time.perf_counter() - planner_started
            ) * 1_000
            context_started = time.perf_counter()
            with measure_segment(timing, "answerContextSerialization"):
                context = build_bounded_context_pack(
                    result,
                    budget_tokens=int(case.request["budgetTokens"]),
                )
            context_ms = (
                time.perf_counter() - context_started
            ) * 1_000
            elapsed_ms = (time.perf_counter() - started) * 1_000
            latencies.append(elapsed_ms)
            if case.performance_path:
                path_latencies[case.performance_path].append(elapsed_ms)
            case_result = build_query_case_result(
                case,
                result,
                latency_spans_ms={
                    "planner": planner_ms,
                    "contextSerialization": context_ms,
                    "total": elapsed_ms,
                },
            )
            case_result.update(
                {
                    "queryId": case.query_id,
                    "primaryDomain": case.primary_domain,
                    "answerMode": str(result.get("answerMode") or ""),
                    "status": str(result.get("status") or ""),
                    "route": str(result.get("route") or ""),
                    "gapCodes": list(
                        case_result["actual"]["gapCodes"]
                    ),
                    "probeCount": len(
                        result.get("recommendedProbes", [])
                    ),
                    "contextTokens": int(context["estimatedTokens"]),
                    "latencyMs": round(elapsed_ms, 3),
                }
            )
            results.append(case_result)
        degree_latency = _degree_latency(connection)
    finally:
        if search_connection is not None:
            search_connection.close()
        connection.close()
    storage_performance = run_storage_path_benchmark(
        core_path.parent,
        allow_unsealed_snapshot=allow_unsealed_snapshot,
        include_timing=include_timing,
    )
    performance_gates = _runtime_performance_gates(
        storage_performance,
        degree_latency,
    )
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
    case_results_bytes = query_case_results_jsonl_bytes(results)
    failure_matrix = build_query_failure_matrix(
        results,
        build_id=build_id,
        corpus_sha256=str(gold["corpusSha256"]),
    )
    failure_matrix_bytes = query_failure_matrix_json_bytes(
        failure_matrix
    )
    benchmark = {
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
            "oneHopP95": degree_latency["oneHop"]["p95"],
            "oneHopP99": degree_latency["oneHop"]["p99"],
            "oneHopSamples": degree_latency["oneHop"]["samples"],
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
            "search": bool(
                storage_performance["coverage"]["search"]
            ),
            "cache": bool(
                storage_performance["coverage"]["cache"]
            ),
            "complete": bool(
                storage_performance["coverage"]["complete"]
            ),
            "gapCode": (
                ""
                if storage_performance["coverage"]["complete"]
                else "SEARCH_CACHE_BENCHMARK_INCOMPLETE"
            ),
        },
        "storagePathPerformance": storage_performance,
        "performanceGates": performance_gates,
        "contextTokens": {
            "maximum": context_max,
            "budget": 2_000,
            "withinBudget": context_max <= 2_000,
        },
        "diagnosticArtifacts": {
            "schema": QUERY_DIAGNOSTICS_SCHEMA,
            "buildId": build_id,
            "buildBinding": build_binding,
            "corpusSha256": str(gold["corpusSha256"]),
            "caseResults": {
                "schema": QUERY_CASE_RESULT_SCHEMA,
                "uri": "reports/query_case_results.jsonl",
                "sha256": hashlib.sha256(
                    case_results_bytes
                ).hexdigest(),
                "count": len(results),
            },
            "failureMatrix": {
                "schema": QUERY_FAILURE_MATRIX_SCHEMA,
                "uri": "reports/query_failure_matrix.json",
                "sha256": hashlib.sha256(
                    failure_matrix_bytes
                ).hexdigest(),
                "caseCount": len(results),
            },
        },
        "results": results,
        # Deprecated v1 aliases: protocol success, never semantic coverage.
        "completeOrBounded": protocol["count"],
        "completeOrBoundedRate": protocol["rate"],
        "simpleDbOnly": identity["count"],
        "simpleDbOnlyRate": identity["rate"],
        "unresolved": len(results) - int(protocol["count"]),
    }
    return _attach_timing_diagnostics(
        benchmark,
        timing,
        scope="queryPlanner",
    )
