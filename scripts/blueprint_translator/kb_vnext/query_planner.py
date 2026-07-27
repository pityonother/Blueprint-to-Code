"""Database-first query planning with explicit, bounded evidence gaps."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.parse import unquote, urlsplit

from .map_usage import (
    MAP_USAGE_EDGE_TYPES,
    is_valid_map_evidence_uri,
)
from .native_gold_set import (
    is_recovered_evidence_uri,
    is_recovered_identifier,
    is_valid_blueprint_graph_evidence_uri,
)
from .schema_capabilities import (
    supports_effective_candidate_explanations,
    supports_typed_map_usage_evidence,
)


GAP_CODES = {
    "REQUEST_UNDERSPECIFIED",
    "REQUEST_MODE_MISMATCH",
    "NO_ENTITY_MATCH",
    "AMBIGUOUS_ENTITY",
    "IDENTITY_PROVENANCE_UNKNOWN",
    "MISSING_FACT",
    "FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED",
    "FACT_NOT_FOUND",
    "FACT_STALE",
    "FACT_AMBIGUOUS",
    "STALE_SOURCE",
    "PARENT_CHAIN_OPEN",
    "REFERENCE_CLOSURE_OPEN",
    "NATIVE_BOUNDARY_UNRESOLVED",
    "RUNTIME_DYNAMIC_BRANCH",
    "MAP_USAGE_INCOMPLETE",
    "EVIDENCE_LIMIT_INSUFFICIENT",
    "UNSUPPORTED_SERIALIZATION",
    "SCHEMA_MIGRATION_REQUIRED",
}
ANSWER_MODES = frozenset(
    {
        "IDENTITY",
        "FACT",
        "RELATIONSHIP",
        "MECHANISM",
    }
)
COMPLETE_STATUSES = {
    "CONFIRMED",
    "VERIFIED",
    "RESOLVED",
    "CONFIRMED_EMPTY",
}
COMPLETE_CONFIDENCE = frozenset({"HIGH", "CONFIRMED"})
IDENTITY_COMPLETE_STATUSES = frozenset(
    {"EXTRACTED", "CONFIRMED", "VERIFIED", "RESOLVED"}
)
RELATIONSHIP_COMPLETE_STATUSES = frozenset(
    {
        "CONFIRMED",
        "VERIFIED",
        "RESOLVED",
    }
)
CLASS_EVIDENCE_COMPLETE_STATUSES = frozenset(
    {
        "EXTRACTED",
        "IDENTIFIED",
        *RELATIONSHIP_COMPLETE_STATUSES,
    }
)
FACT_BACKED_RELATIONSHIP_RULES = {
    "OWNS_COMPONENT": (
        ("HARVEST_RULE", "DeathHarvestingComponent"),
    ),
}
OPEN_STATUSES = {
    "UNKNOWN",
    "AMBIGUOUS",
    "NOT_RECOVERED",
    "SOURCE_NOT_AVAILABLE",
    "LEGACY_UNVERIFIED",
    "CONFIRMED_FINGERPRINT_ONLY",
}
UNRECOVERED_SOURCE_REVISION_SENTINELS = frozenset(
    {
        *OPEN_STATUSES,
        "UNRESOLVED",
        "NOT_AVAILABLE",
        "UNAVAILABLE",
    }
)
GENERIC_EVIDENCE_URI_SCHEMES = {
    "bp",
    "blueprint-graph",
    "blueprint-reference",
    "class-edge",
    "class-hierarchy",
    "discovery",
    "discovery-reference",
    "evidence",
    "existing-kb",
    "fact",
    "fixture",
    "legacy",
    "legacy-kb",
    "map-evidence",
    "native",
    "native-field",
    "native-slice",
    "ontology",
    "ontology-evidence",
    "registration",
    "registration-reference",
    "registration-vnext",
    "registry",
    "registry-reference",
    "runtime",
    "serialized-import-evidence",
    "serialized-soft-path-evidence",
}
SOURCE_REVISION_URI_SCHEMES = frozenset(
    {
        "blueprint-graph",
        "bp",
        "capture",
        "class",
        "class-hierarchy",
        "classifier",
        "discovery",
        "discovery-reference",
        "existing-kb",
        "fixture",
        "legacy",
        "legacy-db",
        "legacy-kb",
        "map",
        "map-catalog",
        "map-evidence",
        "native",
        "native-field",
        "native-set",
        "native-slice",
        "native-symbol-set",
        "ontology",
        "package",
        "parser",
        "registry",
        "registry-reference",
        "runtime",
    }
)
MAX_EFFECTIVE_CANDIDATES_PER_FACT = 8
CANDIDATE_EXPLANATION_AVAILABLE = "AVAILABLE"
CANDIDATE_EXPLANATION_SCHEMA_MIGRATION_REQUIRED = (
    "SCHEMA_MIGRATION_REQUIRED"
)


def is_valid_generic_evidence_uri(value: object) -> bool:
    """Accept recovered evidence only from known KB producer schemes."""

    return is_recovered_evidence_uri(
        value,
        allowed_schemes=GENERIC_EVIDENCE_URI_SCHEMES,
    )


def _is_valid_relationship_target_uri(value: object) -> bool:
    raw = str(value or "")
    text = raw.strip()
    leaf = text.rsplit("/", 1)[-1]
    return (
        raw == text
        and text.startswith(("/Game/", "/Mods/", "/Script/"))
        and "." in leaf
        and "\\" not in text
        and ":" not in text
        and ".." not in text
    )


def is_valid_source_revision_uri(value: object) -> bool:
    """Accept only stable source identities from reviewed KB protocols."""

    raw = str(value or "")
    text = raw.strip()
    if (
        not text
        or raw != text
        or "://" not in text
        or any(character.isspace() for character in text)
    ):
        return False
    parsed = urlsplit(text)
    if (
        parsed.scheme.casefold() not in SOURCE_REVISION_URI_SCHEMES
        or parsed.query
        or parsed.fragment
    ):
        return False
    identity_parts = [
        *([unquote(parsed.netloc)] if parsed.netloc else []),
        *(
            unquote(part)
            for part in parsed.path.split("/")
            if part
        ),
    ]
    if not identity_parts or any(
        part in {".", ".."}
        or any(character.isspace() for character in part)
        for part in identity_parts
    ):
        return False
    if parsed.scheme.casefold() == "package":
        # Unreal package paths can legitimately contain a folder or asset
        # literally named "Unknown"; reject only an all-sentinel identity.
        return any(is_recovered_identifier(part) for part in identity_parts)
    return all(is_recovered_identifier(part) for part in identity_parts)


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
    answer_mode: str | None = None


def _bounded_limit(value: int, *, minimum: int = 1, maximum: int = 200) -> int:
    return max(minimum, min(maximum, int(value)))


def _entity_projection(row: sqlite3.Row) -> dict[str, object]:
    revision_id = row["identity_revision_id"]
    source_revision = (
        {
            "revisionId": int(revision_id),
            "sourceKind": str(row["identity_source_kind"]),
            "sourceUri": str(row["identity_source_uri"]),
            "sourceFingerprint": str(
                row["identity_source_fingerprint"]
            ),
            "producerVersion": str(row["identity_producer_version"]),
            "schemaVersion": str(row["identity_schema_version"]),
            "generatedAt": str(row["identity_generated_at"]),
            "freshness": str(row["identity_freshness"]),
        }
        if revision_id is not None
        else None
    )
    status = str(row["status"])
    revision_freshness = (
        str(row["identity_freshness"]).upper()
        if row["identity_freshness"] is not None
        else "UNKNOWN"
    )
    freshness = (
        "STALE"
        if status.upper() == "STALE"
        else (
            revision_freshness
            if revision_freshness in {"FRESH", "STALE"}
            else "UNKNOWN"
        )
    )
    return {
        "entityId": int(row["entity_id"]),
        "canonicalUri": str(row["canonical_uri"]),
        "entityKind": str(row["entity_kind"]),
        "displayName": str(row["display_name"] or ""),
        "internalName": str(row["internal_name"] or ""),
        "status": status,
        "confidence": str(row["confidence"]),
        "sourceRevision": source_revision,
        "freshness": freshness,
    }


def _identity_evidence(
    entity: dict[str, object],
) -> list[dict[str, object]]:
    revision = entity.get("sourceRevision")
    if not isinstance(revision, dict):
        return []
    source_uri = str(revision.get("sourceUri") or "")
    if not source_uri:
        return []
    return [
        {
            "entityId": int(entity["entityId"]),
            "canonicalUri": str(entity["canonicalUri"]),
            "evidenceUri": source_uri,
            "evidenceRole": "IDENTITY_REVISION",
            "sourceRevisionId": revision.get("revisionId"),
            "sourceRevision": dict(revision),
            "freshness": str(revision.get("freshness") or "UNKNOWN"),
        }
    ]


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
            entity.entity_id, entity.canonical_uri, entity.entity_kind,
            entity.display_name, entity.internal_name, entity.status,
            entity.confidence,
            revision.revision_id AS identity_revision_id,
            revision.source_kind AS identity_source_kind,
            revision.source_uri AS identity_source_uri,
            revision.source_fingerprint AS identity_source_fingerprint,
            revision.producer_version AS identity_producer_version,
            revision.schema_version AS identity_schema_version,
            revision.generated_at AS identity_generated_at,
            revision.freshness_status AS identity_freshness
        FROM entities AS entity
        LEFT JOIN packages AS package
          ON package.package_id=entity.package_id
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=package.current_revision_id
        WHERE entity.canonical_uri=?
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
                entity.internal_name, entity.status, entity.confidence,
                revision.revision_id AS identity_revision_id,
                revision.source_kind AS identity_source_kind,
                revision.source_uri AS identity_source_uri,
                revision.source_fingerprint AS identity_source_fingerprint,
                revision.producer_version AS identity_producer_version,
                revision.schema_version AS identity_schema_version,
                revision.generated_at AS identity_generated_at,
                revision.freshness_status AS identity_freshness
            FROM entities AS entity
            LEFT JOIN aliases AS alias ON alias.entity_id=entity.entity_id
            LEFT JOIN packages AS package
              ON package.package_id=entity.package_id
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=package.current_revision_id
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
                entity.internal_name, entity.status, entity.confidence,
                revision.revision_id AS identity_revision_id,
                revision.source_kind AS identity_source_kind,
                revision.source_uri AS identity_source_uri,
                revision.source_fingerprint AS identity_source_fingerprint,
                revision.producer_version AS identity_producer_version,
                revision.schema_version AS identity_schema_version,
                revision.generated_at AS identity_generated_at,
                revision.freshness_status AS identity_freshness
            FROM entities AS entity
            LEFT JOIN aliases AS alias ON alias.entity_id=entity.entity_id
            LEFT JOIN packages AS package
              ON package.package_id=entity.package_id
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=package.current_revision_id
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
    limit: int | None,
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
    limit_clause = ""
    if limit is not None:
        parameters.append(limit)
        limit_clause = " LIMIT ?"
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
            {limit_clause}
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
            {limit_clause}
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


def _json_value_is_portable(value: object) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, int):
        return -(2**63) <= value <= 2**63 - 1
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value_is_portable(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _json_value_is_portable(item)
            for key, item in value.items()
        )
    return False


def fact_value_is_usable(fact: Mapping[str, object]) -> bool:
    kind = str(fact.get("valueKind") or "").upper()
    status = str(fact.get("status") or "").upper()
    text = fact.get("valueText")
    number = fact.get("valueNumber")
    integer = fact.get("valueInteger")
    raw_json = fact.get("valueJson")
    if kind == "CONFIRMED_EMPTY":
        return status == "CONFIRMED_EMPTY" and all(
            value is None for value in (text, number, integer, raw_json)
        )
    if status == "CONFIRMED_EMPTY":
        return False
    if kind == "BOOLEAN":
        return (
            integer in {0, 1}
            and text is None
            and number is None
            and raw_json is None
        )
    if kind == "INTEGER":
        return (
            isinstance(integer, int)
            and not isinstance(integer, bool)
            and text is None
            and number is None
            and raw_json is None
        )
    if kind == "NUMBER":
        return (
            isinstance(number, (int, float))
            and not isinstance(number, bool)
            and math.isfinite(float(number))
            and text is None
            and integer is None
            and raw_json is None
        )
    if kind in {"TEXT", "ENTITY_REF"}:
        return (
            isinstance(text, str)
            and number is None
            and integer is None
            and raw_json is None
            and (kind != "ENTITY_REF" or text.startswith("/"))
        )
    if kind == "JSON":
        if (
            not isinstance(raw_json, str)
            or text is not None
            or number is not None
            or integer is not None
        ):
            return False
        try:
            decoded = json.loads(raw_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return _json_value_is_portable(decoded)
    return False


def _fact_value_key(fact: dict[str, object]) -> tuple[object, ...]:
    return (
        str(fact.get("valueKind") or "").upper(),
        fact.get("valueText"),
        fact.get("valueNumber"),
        fact.get("valueInteger"),
        fact.get("valueJson"),
        fact.get("unit"),
    )


def source_revision_is_fresh(
    value: object,
    *,
    require_revision_id: bool = True,
) -> bool:
    """Return true only for a complete, fresh source-revision identity."""

    if not isinstance(value, Mapping):
        return False
    freshness = str(
        value.get("freshness") or value.get("freshnessStatus") or ""
    ).strip().upper()
    identity_fields = (
        "sourceKind",
        "sourceUri",
        "sourceFingerprint",
        "producerVersion",
        "schemaVersion",
        "generatedAt",
    )

    def is_recovered(field: str) -> bool:
        text = str(value.get(field) or "").strip()
        normalized = "_".join(
            text.upper().replace("-", " ").replace("_", " ").split()
        )
        return (
            bool(text)
            and normalized not in UNRECOVERED_SOURCE_REVISION_SENTINELS
        )

    return (
        (not require_revision_id or value.get("revisionId") is not None)
        and all(is_recovered(field) for field in identity_fields)
        and is_valid_source_revision_uri(value.get("sourceUri"))
        and freshness == "FRESH"
    )


def _conflicting_fact_names(
    facts: Iterable[dict[str, object]],
) -> set[str]:
    facts_by_name: dict[str, list[dict[str, object]]] = {}
    for fact in facts:
        facts_by_name.setdefault(str(fact.get("factName") or ""), []).append(
            fact
        )
    return {
        fact_name
        for fact_name, same_name_facts in facts_by_name.items()
        if len(
            {
                _fact_value_key(fact)
                for fact in same_name_facts
                if str(fact.get("status") or "").upper()
                in COMPLETE_STATUSES
                and str(
                    fact.get("resolutionStatus") or ""
                ).upper()
                not in OPEN_STATUSES
                and str(fact.get("confidence") or "").upper()
                in COMPLETE_CONFIDENCE
                and fact_value_is_usable(fact)
            }
        )
        > 1
    }


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
            revision.source_kind, revision.source_uri,
            revision.source_fingerprint, revision.producer_version,
            revision.schema_version, revision.generated_at,
            revision.freshness_status
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
                "sourceRevision": {
                    "revisionId": int(row[3]),
                    "sourceKind": str(row[4]),
                    "sourceUri": str(row[5]),
                    "sourceFingerprint": str(row[6]),
                    "producerVersion": str(row[7]),
                    "schemaVersion": str(row[8]),
                    "generatedAt": str(row[9]),
                    "freshness": str(row[10]),
                },
                "freshness": str(row[10]),
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
        for row in connection.execute(
            f"""
            SELECT
                evidence.fact_id,
                evidence.evidence_uri,
                revision.revision_id,
                revision.source_kind,
                revision.source_uri,
                revision.source_fingerprint,
                revision.producer_version,
                revision.schema_version,
                revision.generated_at,
                revision.freshness_status
            FROM fact_evidence AS evidence
            JOIN source_revisions AS revision
              ON revision.revision_id=evidence.source_revision_id
            WHERE evidence.fact_id IN ({placeholders})
            """,
            tuple(batch),
        ):
            normalized_id = int(row[0])
            evidenced.add(normalized_id)
            source_revision = {
                "revisionId": int(row[2]),
                "sourceKind": str(row[3]),
                "sourceUri": str(row[4]),
                "sourceFingerprint": str(row[5]),
                "producerVersion": str(row[6]),
                "schemaVersion": str(row[7]),
                "generatedAt": str(row[8]),
                "freshness": str(row[9]),
            }
            if is_valid_generic_evidence_uri(
                row[1]
            ) and source_revision_is_fresh(source_revision):
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


def _resolved_answer_mode(
    requirements: QueryRequirements,
) -> tuple[str | None, bool]:
    """Return the normalized mode and whether it was inferred for compatibility."""

    if requirements.answer_mode is not None:
        answer_mode = requirements.answer_mode.strip().upper()
        if answer_mode not in ANSWER_MODES:
            raise ValueError(
                "answer_mode must be one of "
                + ", ".join(sorted(ANSWER_MODES))
            )
        return answer_mode, False
    if (
        requirements.requires_native
        or requirements.requires_runtime
        or requirements.requires_map_evidence
    ):
        return "MECHANISM", True
    if requirements.edge_types:
        return "RELATIONSHIP", True
    if requirements.fact_types:
        return "FACT", True
    return None, False


def _request_contract_gap(
    requirements: QueryRequirements,
    answer_mode: str | None,
) -> dict[str, str] | None:
    has_fact_requirement = bool(requirements.fact_types)
    has_relationship_requirement = bool(requirements.edge_types)
    has_mechanism_requirement = bool(
        requirements.requires_native
        or requirements.requires_runtime
        or requirements.requires_map_evidence
    )
    has_semantic_requirement = (
        has_fact_requirement
        or has_relationship_requirement
        or has_mechanism_requirement
    )
    if answer_mode is None or (
        answer_mode != "IDENTITY" and not has_semantic_requirement
    ):
        return {
            "code": "REQUEST_UNDERSPECIFIED",
            "requirement": (
                "answerMode=IDENTITY or a semantic requirement"
            ),
        }
    if answer_mode == "IDENTITY" and has_semantic_requirement:
        return {
            "code": "REQUEST_MODE_MISMATCH",
            "requirement": (
                "answerMode=IDENTITY cannot include semantic requirements"
            ),
        }
    expected_requirement = {
        "FACT": has_fact_requirement,
        "RELATIONSHIP": has_relationship_requirement,
        "MECHANISM": has_mechanism_requirement,
    }.get(answer_mode, True)
    if not expected_requirement:
        return {
            "code": "REQUEST_MODE_MISMATCH",
            "requirement": (
                f"answerMode={answer_mode} requires its matching "
                "semantic requirement"
            ),
        }
    return None


def _request_gap_result(
    *,
    answer_mode: str | None,
    gap: dict[str, str],
) -> dict[str, object]:
    return {
        "answerMode": answer_mode,
        "status": "GAP",
        "route": "EVIDENCE_REQUIRED",
        "entity": None,
        "entityCandidates": [],
        "facts": [],
        "relationships": [],
        "evidence": [],
        "returned": 0,
        "omitted": 0,
        "freshness": "UNKNOWN",
        "missingRequirements": [gap],
        "recommendedProbes": [],
    }


def _map_usage_projection(
    row: sqlite3.Row,
    *,
    entity_id: int,
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        examples = json.loads(str(row["evidence_examples_json"]))
    except json.JSONDecodeError:
        examples = []
    if not isinstance(examples, list):
        examples = []
    source_revision = {
        "revisionId": int(row["source_revision_id"]),
        "sourceKind": str(row["revision_source_kind"]),
        "sourceUri": str(row["revision_source_uri"]),
        "sourceFingerprint": str(row["revision_source_fingerprint"]),
        "producerVersion": str(row["revision_producer_version"]),
        "schemaVersion": str(row["revision_schema_version"]),
        "generatedAt": str(row["revision_generated_at"]),
        "freshness": str(row["revision_freshness"]),
    }
    evidence = {
        "edgeId": int(row["edge_id"]),
        "mapUsageId": str(row["map_usage_id"]),
        "evidenceUri": str(row["evidence_uri"]),
        "evidenceRole": "MAP_USAGE_EVIDENCE",
        "evidenceLayer": str(row["evidence_layer"]),
        "sourceRevisionId": int(row["source_revision_id"]),
        "sourceRevision": source_revision,
        "freshness": str(row["freshness_status"]),
    }
    relationship = {
        "edgeId": int(row["edge_id"]),
        "edgeType": str(row["edge_type"]),
        "edgeStrength": str(row["edge_strength"]),
        "status": str(row["status"]),
        "confidence": str(row["confidence"]),
        "sourceEntityId": int(row["source_entity_id"]),
        "sourceUri": str(row["source_uri"]),
        "targetEntityId": int(row["target_entity_id"]),
        "targetUri": str(row["target_uri"]),
        "direction": (
            "OUTBOUND"
            if int(row["source_entity_id"]) == entity_id
            else "INBOUND"
        ),
        "evidenceUri": str(row["evidence_uri"]),
        "sourceRevisionId": int(row["source_revision_id"]),
        "sourceRevision": source_revision,
        "freshness": str(row["freshness_status"]),
        "mapUsageId": str(row["map_usage_id"]),
        "evidenceLayer": str(row["evidence_layer"]),
        "mapFamily": str(row["map_family"]),
        "mapKind": str(row["map_kind"]),
        "sourceEvidenceStatus": str(row["source_evidence_status"]),
        "usageStatus": str(row["usage_status"]),
        "claimsCompleteMapUsage": bool(
            row["claims_complete_map_usage"]
        ),
        "claimsSpawnCoordinates": bool(
            row["claims_spawn_coordinates"]
        ),
        "evidenceCount": int(row["evidence_count"]),
        "evidenceExamples": examples,
        "evidence": [evidence],
    }
    return relationship, evidence


def _map_usage_rows(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    confirmed_only: bool,
    edge_types: Iterable[str] = MAP_USAGE_EDGE_TYPES,
    limit: int,
) -> list[sqlite3.Row]:
    requested_edge_types = tuple(
        dict.fromkeys(str(value).upper() for value in edge_types)
    )
    if not requested_edge_types:
        return []
    source = (
        "confirmed_map_usage_edges AS map"
        if confirmed_only
        else (
            "edges AS map "
            "JOIN map_usage_edge_evidence AS evidence "
            "ON evidence.edge_id=map.edge_id"
        )
    )
    evidence_columns = (
        """
        map.map_usage_id, map.evidence_layer, map.map_family,
        map.map_kind, map.source_evidence_status, map.usage_status,
        map.freshness_status, map.claims_complete_map_usage,
        map.claims_spawn_coordinates, map.evidence_count,
        map.evidence_examples_json
        """
        if confirmed_only
        else
        """
        evidence.map_usage_id, evidence.evidence_layer,
        evidence.map_family, evidence.map_kind,
        evidence.source_evidence_status, evidence.usage_status,
        evidence.freshness_status,
        evidence.claims_complete_map_usage,
        evidence.claims_spawn_coordinates, evidence.evidence_count,
        evidence.evidence_examples_json
        """
    )
    placeholders = ", ".join("?" for _ in requested_edge_types)
    return list(
        connection.execute(
            f"""
            SELECT
                map.edge_id, map.source_entity_id, map.target_entity_id,
                map.edge_type, map.edge_strength, map.status,
                map.confidence, map.source_revision_id, map.evidence_uri,
                source.canonical_uri AS source_uri,
                target.canonical_uri AS target_uri,
                {evidence_columns},
                revision.source_kind AS revision_source_kind,
                revision.source_uri AS revision_source_uri,
                revision.source_fingerprint
                    AS revision_source_fingerprint,
                revision.producer_version AS revision_producer_version,
                revision.schema_version AS revision_schema_version,
                revision.generated_at AS revision_generated_at,
                revision.freshness_status AS revision_freshness
            FROM {source}
            JOIN entities AS source
              ON source.entity_id=map.source_entity_id
            JOIN entities AS target
              ON target.entity_id=map.target_entity_id
            JOIN source_revisions AS revision
              ON revision.revision_id=map.source_revision_id
            WHERE (
                    map.source_entity_id=?
                    OR map.target_entity_id=?
                  )
              AND map.edge_type IN ({placeholders})
            ORDER BY map.edge_id
            LIMIT ?
            """,
            (
                entity_id,
                entity_id,
                *requested_edge_types,
                limit,
            ),
        )
    )


def _map_usage_requirement(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    edge_types: Iterable[str],
    limit: int,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, str]],
    str,
]:
    requested_types = tuple(
        dict.fromkeys(str(value).upper() for value in edge_types)
    )
    requirement = (
        ", ".join(requested_types)
        if requested_types
        else "typed map usage"
    )
    if not supports_typed_map_usage_evidence(connection):
        return (
            [],
            [],
            [
                {
                    "code": "SCHEMA_MIGRATION_REQUIRED",
                    "requirement": "typed map-usage evidence tables",
                }
            ],
            "UNKNOWN",
        )
    confirmed_rows = [
        row
        for row in _map_usage_rows(
            connection,
            entity_id=entity_id,
            confirmed_only=True,
            edge_types=requested_types,
            limit=limit,
        )
        if (
            str(row["edge_type"]).upper() in MAP_USAGE_EDGE_TYPES
            and str(row["status"]).upper()
            in RELATIONSHIP_COMPLETE_STATUSES
            and str(row["confidence"]).upper() in COMPLETE_CONFIDENCE
            and is_valid_map_evidence_uri(row["evidence_uri"])
            and bool(str(row["map_usage_id"]).strip())
            and bool(str(row["evidence_layer"]).strip())
            and str(row["source_evidence_status"]).upper()
            in RELATIONSHIP_COMPLETE_STATUSES
            and str(row["usage_status"]).upper()
            in RELATIONSHIP_COMPLETE_STATUSES
            and str(row["freshness_status"]).upper() == "FRESH"
            and int(row["evidence_count"] or 0) >= 1
            and source_revision_is_fresh(
                {
                    "revisionId": row["source_revision_id"],
                    "sourceKind": row["revision_source_kind"],
                    "sourceUri": row["revision_source_uri"],
                    "sourceFingerprint": (
                        row["revision_source_fingerprint"]
                    ),
                    "producerVersion": (
                        row["revision_producer_version"]
                    ),
                    "schemaVersion": row["revision_schema_version"],
                    "generatedAt": row["revision_generated_at"],
                    "freshness": row["revision_freshness"],
                }
            )
        )
    ]
    selected_rows = confirmed_rows
    if not selected_rows:
        selected_rows = _map_usage_rows(
            connection,
            entity_id=entity_id,
            confirmed_only=False,
            edge_types=requested_types,
            limit=limit,
        )
    projected = [
        _map_usage_projection(row, entity_id=entity_id)
        for row in selected_rows
    ]
    relationships = [
        relationship for relationship, _ in projected
    ]
    evidence = [item for _, item in projected]
    if confirmed_rows:
        return relationships, evidence, [], "FRESH"
    stale = any(
        str(row["status"]).upper() == "STALE"
        or str(row["freshness_status"]).upper() == "STALE"
        or str(row["revision_freshness"]).upper() == "STALE"
        for row in selected_rows
    )
    return (
        relationships,
        evidence,
        [
            {
                "code": "STALE_SOURCE" if stale else "MAP_USAGE_INCOMPLETE",
                "requirement": (
                    f"{requirement}: confirmed typed direct, PCG, or "
                    "World Partition map usage"
                ),
            }
        ],
        "STALE" if stale else "UNKNOWN",
    )


def _native_mechanism_rows(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT
                link.link_id, link.blueprint_graph_evidence_uri,
                link.blueprint_function_name, link.native_function_id,
                link.native_evidence_uri, link.resolution_method,
                link.status AS link_status,
                link.confidence AS link_confidence,
                graph_revision.revision_id AS graph_revision_id,
                graph_revision.source_kind AS graph_source_kind,
                graph_revision.source_uri AS graph_source_uri,
                graph_revision.source_fingerprint
                    AS graph_source_fingerprint,
                graph_revision.producer_version AS graph_producer_version,
                graph_revision.schema_version AS graph_schema_version,
                graph_revision.generated_at AS graph_generated_at,
                graph_revision.freshness_status
                    AS graph_revision_freshness,
                function.canonical_uri AS native_uri,
                function.qualified_symbol, function.module_name,
                function.rva, function.signature,
                function.status AS function_status,
                function.confidence AS function_confidence,
                revision.revision_id, revision.source_kind,
                revision.source_uri, revision.source_fingerprint,
                revision.producer_version, revision.schema_version,
                revision.generated_at,
                revision.freshness_status
            FROM native_blueprint_links AS link
            LEFT JOIN native_functions AS function
              ON function.native_function_id=link.native_function_id
            LEFT JOIN source_revisions AS graph_revision
              ON graph_revision.revision_id=
                 link.blueprint_graph_source_revision_id
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=function.source_revision_id
            WHERE link.blueprint_entity_id=?
            ORDER BY
                CASE
                    WHEN link.status='CONFIRMED'
                     AND function.status='CONFIRMED'
                    THEN 0 ELSE 1
                END,
                link.link_id
            """,
            (entity_id,),
        )
    )


def _native_mechanism_projection(
    row: sqlite3.Row,
    *,
    entity: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    graph_revision = (
        {
            "revisionId": int(row["graph_revision_id"]),
            "sourceKind": str(row["graph_source_kind"]),
            "sourceUri": str(row["graph_source_uri"]),
            "sourceFingerprint": str(
                row["graph_source_fingerprint"]
            ),
            "producerVersion": str(row["graph_producer_version"]),
            "schemaVersion": str(row["graph_schema_version"]),
            "generatedAt": str(row["graph_generated_at"]),
            "freshness": str(row["graph_revision_freshness"]),
        }
        if row["graph_revision_id"] is not None
        else None
    )
    native_revision = (
        {
            "revisionId": int(row["revision_id"]),
            "sourceKind": str(row["source_kind"]),
            "sourceUri": str(row["source_uri"]),
            "sourceFingerprint": str(row["source_fingerprint"]),
            "producerVersion": str(row["producer_version"]),
            "schemaVersion": str(row["schema_version"]),
            "generatedAt": str(row["generated_at"]),
            "freshness": str(row["freshness_status"]),
        }
        if row["revision_id"] is not None
        else None
    )
    evidence: list[dict[str, object]] = []
    graph_uri = str(row["blueprint_graph_evidence_uri"] or "")
    if graph_uri:
        evidence.append(
            {
                "nativeLinkId": str(row["link_id"]),
                "evidenceUri": graph_uri,
                "evidenceRole": "BLUEPRINT_GRAPH_EVIDENCE",
                "sourceRevisionId": (
                    graph_revision.get("revisionId")
                    if graph_revision is not None
                    else None
                ),
                "sourceRevision": graph_revision,
                "freshness": (
                    str(graph_revision.get("freshness") or "UNKNOWN")
                    if graph_revision is not None
                    else "UNKNOWN"
                ),
            }
        )
    native_uri = str(row["native_evidence_uri"] or "")
    if native_uri:
        evidence.append(
            {
                "nativeLinkId": str(row["link_id"]),
                "evidenceUri": native_uri,
                "evidenceRole": "NATIVE_EVIDENCE",
                "sourceRevisionId": (
                    native_revision.get("revisionId")
                    if native_revision is not None
                    else None
                ),
                "sourceRevision": native_revision,
                "freshness": (
                    str(native_revision.get("freshness") or "UNKNOWN")
                    if native_revision is not None
                    else "UNKNOWN"
                ),
            }
        )
    evidence_freshness = {
        str(item["freshness"]).upper() for item in evidence
    }
    if "STALE" in evidence_freshness:
        freshness = "STALE"
    elif len(evidence) == 2 and all(
        source_revision_is_fresh(item.get("sourceRevision"))
        for item in evidence
    ):
        freshness = "FRESH"
    else:
        freshness = "UNKNOWN"
    relationship = {
        "edgeId": str(row["link_id"]),
        "edgeType": "BLUEPRINT_CALLS_NATIVE",
        "edgeStrength": "DIRECT_CALLSITE",
        "status": str(row["link_status"]),
        "confidence": str(row["link_confidence"]),
        "sourceEntityId": int(entity["entityId"]),
        "sourceUri": str(entity["canonicalUri"]),
        "targetUri": str(row["native_uri"] or ""),
        "direction": "OUTBOUND",
        "blueprintFunctionName": str(
            row["blueprint_function_name"] or ""
        ),
        "qualifiedSymbol": str(row["qualified_symbol"] or ""),
        "moduleName": str(row["module_name"] or ""),
        "rva": str(row["rva"] or ""),
        "signature": str(row["signature"] or ""),
        "resolutionMethod": str(row["resolution_method"]),
        "functionStatus": str(row["function_status"] or ""),
        "functionConfidence": str(row["function_confidence"] or ""),
        "evidenceUri": graph_uri,
        "nativeEvidenceUri": native_uri,
        "sourceRevisionId": (
            native_revision.get("revisionId")
            if native_revision is not None
            else None
        ),
        "sourceRevision": native_revision,
        "freshness": freshness,
        "evidence": evidence,
    }
    return relationship, evidence


def _class_assignment_relationships(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            assignment.class_id, assignment.status,
            assignment.confidence, assignment.evidence_uri,
            class.class_path, revision.revision_id,
            revision.source_kind, revision.source_uri,
            revision.source_fingerprint, revision.producer_version,
            revision.schema_version, revision.generated_at,
            revision.freshness_status
        FROM asset_class_assignments AS assignment
        JOIN classes AS class ON class.class_id=assignment.class_id
        LEFT JOIN source_revisions AS revision
          ON revision.revision_id=assignment.source_revision_id
        WHERE assignment.entity_id=?
          AND assignment.assignment_kind='ASSET_CLASS'
        ORDER BY assignment.class_id
        """,
        (entity_id,),
    )
    relationships: list[dict[str, object]] = []
    for row in rows:
        revision = (
            {
                "revisionId": int(row[5]),
                "sourceKind": str(row[6]),
                "sourceUri": str(row[7]),
                "sourceFingerprint": str(row[8]),
                "producerVersion": str(row[9]),
                "schemaVersion": str(row[10]),
                "generatedAt": str(row[11]),
                "freshness": str(row[12]),
            }
            if row[5] is not None
            else None
        )
        edge_id = f"class-assignment:{entity_id}:{int(row[0])}"
        evidence = {
            "edgeId": edge_id,
            "classId": int(row[0]),
            "evidenceUri": str(row[3]),
            "evidenceRole": "CLASS_ASSIGNMENT",
            "sourceRevisionId": (
                revision.get("revisionId")
                if revision is not None
                else None
            ),
            "sourceRevision": revision,
            "freshness": str(row[12] or "UNKNOWN"),
        }
        raw_status = str(row[1]).upper()
        target_uri = str(row[4])
        relationships.append(
            {
                "edgeId": edge_id,
                "edgeType": "ASSET_CLASS",
                "edgeStrength": "DIRECT_ASSIGNMENT",
                "status": (
                    "CONFIRMED"
                    if (
                        raw_status in CLASS_EVIDENCE_COMPLETE_STATUSES
                        and _is_valid_relationship_target_uri(target_uri)
                    )
                    else raw_status
                ),
                "sourceEvidenceStatus": raw_status,
                "confidence": str(row[2]),
                "sourceEntityId": entity_id,
                "targetUri": target_uri,
                "evidenceUri": str(row[3]),
                "sourceRevisionId": evidence["sourceRevisionId"],
                "sourceRevision": revision,
                "freshness": str(row[12] or "UNKNOWN"),
                "evidence": [evidence],
            }
        )
    return relationships


def _fact_backed_relationships(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    edge_type: str,
) -> list[dict[str, object]]:
    rules = FACT_BACKED_RELATIONSHIP_RULES.get(edge_type, ())
    projected: dict[int, dict[str, object]] = {}
    for fact_type, fact_name in rules:
        rows = connection.execute(
            """
            SELECT
                fact.fact_id, fact.fact_name, fact.value_text,
                fact.status, fact.confidence, evidence.evidence_uri,
                evidence.evidence_role, revision.revision_id,
                revision.source_kind, revision.source_uri,
                revision.source_fingerprint, revision.producer_version,
                revision.schema_version, revision.generated_at,
                revision.freshness_status
            FROM facts AS fact
            LEFT JOIN fact_evidence AS evidence
              ON evidence.fact_id=fact.fact_id
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=evidence.source_revision_id
            WHERE fact.subject_entity_id=?
              AND fact.current=1
              AND fact.fact_type=?
              AND fact.fact_name=?
              AND fact.value_kind='ENTITY_REF'
            ORDER BY fact.fact_id, evidence.evidence_uri
            """,
            (entity_id, fact_type, fact_name),
        )
        for row in rows:
            fact_id = int(row[0])
            revision = (
                {
                    "revisionId": int(row[7]),
                    "sourceKind": str(row[8]),
                    "sourceUri": str(row[9]),
                    "sourceFingerprint": str(row[10]),
                    "producerVersion": str(row[11]),
                    "schemaVersion": str(row[12]),
                    "generatedAt": str(row[13]),
                    "freshness": str(row[14]),
                }
                if row[7] is not None
                else None
            )
            target_uri = str(row[2] or "")
            raw_status = str(row[3])
            relationship = projected.setdefault(
                fact_id,
                {
                    "edgeId": f"fact-relationship:{fact_id}:{edge_type}",
                    "edgeType": edge_type,
                    "edgeStrength": "TYPED_FACT",
                    "status": (
                        raw_status
                        if _is_valid_relationship_target_uri(target_uri)
                        else "NOT_RECOVERED"
                    ),
                    "confidence": str(row[4]),
                    "sourceEntityId": entity_id,
                    "targetUri": target_uri,
                    "sourceProperty": str(row[1]),
                    "factId": fact_id,
                    "evidenceUri": str(row[5] or ""),
                    "sourceRevisionId": (
                        revision.get("revisionId")
                        if revision is not None
                        else None
                    ),
                    "sourceRevision": revision,
                    "freshness": str(row[14] or "UNKNOWN"),
                    "evidence": [],
                },
            )
            evidence_uri = str(row[5] or "")
            if not evidence_uri or revision is None:
                continue
            relationship["evidence"].append(
                {
                    "edgeId": relationship["edgeId"],
                    "factId": fact_id,
                    "evidenceUri": evidence_uri,
                    "evidenceRole": str(row[6]),
                    "sourceRevisionId": revision["revisionId"],
                    "sourceRevision": revision,
                    "freshness": str(row[14]),
                }
            )
    return list(projected.values())


def load_effective_class_evidence(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    effective_facts: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    resolved_facts = [
        fact
        for fact in effective_facts
        if fact.get("factId") is not None
        and str(fact.get("resolutionStatus") or "") == "RESOLVED"
    ]
    if not resolved_facts:
        return []
    assignments = list(
        connection.execute(
            """
            SELECT
                assignment.evidence_uri, assignment.assignment_kind,
                assignment.status, assignment.confidence,
                class.class_id, class.class_path,
                revision.revision_id, revision.source_kind,
                revision.source_uri, revision.source_fingerprint,
                revision.producer_version, revision.schema_version,
                revision.generated_at,
                revision.freshness_status
            FROM asset_class_assignments AS assignment
            JOIN classes AS class ON class.class_id=assignment.class_id
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=assignment.source_revision_id
            WHERE assignment.entity_id=?
            ORDER BY assignment.assignment_kind, class.class_id
            """,
            (entity_id,),
        )
    )
    evidence: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()

    def add(item: dict[str, object]) -> None:
        key = (
            item.get("factId"),
            item.get("evidenceRole"),
            item.get("evidenceUri"),
        )
        if key not in seen:
            seen.add(key)
            evidence.append(item)

    for fact in resolved_facts:
        fact_id = int(fact["factId"])
        for row in assignments:
            revision = (
                {
                    "revisionId": int(row[6]),
                    "sourceKind": str(row[7]),
                    "sourceUri": str(row[8]),
                    "sourceFingerprint": str(row[9]),
                    "producerVersion": str(row[10]),
                    "schemaVersion": str(row[11]),
                    "generatedAt": str(row[12]),
                    "freshness": str(row[13]),
                }
                if row[6] is not None
                else None
            )
            add(
                {
                    "factId": fact_id,
                    "classId": int(row[4]),
                    "classPath": str(row[5]),
                    "assignmentKind": str(row[1]),
                    "status": str(row[2]),
                    "confidence": str(row[3]),
                    "evidenceUri": str(row[0]),
                    "evidenceRole": "CLASS_ASSIGNMENT",
                    "sourceRevisionId": (
                        revision.get("revisionId")
                        if revision is not None
                        else None
                    ),
                    "sourceRevision": revision,
                    "freshness": (
                        str(revision.get("freshness") or "UNKNOWN")
                        if revision is not None
                        else "UNKNOWN"
                    ),
                }
            )
        chain = fact.get("resolutionChain")
        if not isinstance(chain, dict):
            continue
        edge_ids: set[str] = set()
        edge_groups = [chain.get("edges", [])]
        native_root = chain.get("nativeRootProof")
        if isinstance(native_root, dict):
            edge_groups.append(native_root.get("edges", []))
            raw_revision = native_root.get("sourceRevision")
            if isinstance(raw_revision, dict):
                source_uri = str(raw_revision.get("sourceUri") or "")
                if source_uri:
                    add(
                        {
                            "factId": fact_id,
                            "evidenceUri": source_uri,
                            "evidenceRole": "NATIVE_ROOT_CLASS_REVISION",
                            "sourceRevision": {
                                "sourceKind": str(
                                    raw_revision.get("sourceKind") or ""
                                ),
                                "sourceUri": source_uri,
                                "sourceFingerprint": str(
                                    raw_revision.get(
                                        "sourceFingerprint"
                                    )
                                    or ""
                                ),
                                "producerVersion": str(
                                    raw_revision.get("producerVersion") or ""
                                ),
                                "schemaVersion": str(
                                    raw_revision.get("schemaVersion") or ""
                                ),
                                "generatedAt": str(
                                    raw_revision.get("generatedAt") or ""
                                ),
                                "freshness": str(
                                    raw_revision.get(
                                        "freshnessStatus"
                                    )
                                    or "UNKNOWN"
                                ),
                            },
                            "freshness": str(
                                raw_revision.get("freshnessStatus")
                                or "UNKNOWN"
                            ),
                        }
                    )
        for edge_group in edge_groups:
            if not isinstance(edge_group, list):
                continue
            for edge in edge_group:
                if not isinstance(edge, dict):
                    continue
                identifiers = edge.get("evidenceIds", [])
                if isinstance(identifiers, list):
                    edge_ids.update(
                        str(identifier)
                        for identifier in identifiers
                        if str(identifier)
                    )
        if not edge_ids:
            continue
        placeholders = ",".join("?" for _ in edge_ids)
        rows = connection.execute(
            f"""
            SELECT
                edge.evidence_id, edge.child_class_id,
                edge.parent_class_id, edge.edge_kind,
                edge.status, edge.confidence,
                revision.revision_id, revision.source_kind,
                revision.source_uri, revision.source_fingerprint,
                revision.producer_version, revision.schema_version,
                revision.generated_at,
                revision.freshness_status
            FROM class_edges AS edge
            LEFT JOIN source_revisions AS revision
              ON revision.revision_id=edge.source_revision_id
            WHERE edge.evidence_id IN ({placeholders})
            ORDER BY edge.evidence_id, edge.child_class_id
            """,
            tuple(sorted(edge_ids)),
        )
        for row in rows:
            revision = (
                {
                    "revisionId": int(row[6]),
                    "sourceKind": str(row[7]),
                    "sourceUri": str(row[8]),
                    "sourceFingerprint": str(row[9]),
                    "producerVersion": str(row[10]),
                    "schemaVersion": str(row[11]),
                    "generatedAt": str(row[12]),
                    "freshness": str(row[13]),
                }
                if row[6] is not None
                else None
            )
            add(
                {
                    "factId": fact_id,
                    "childClassId": int(row[1]),
                    "parentClassId": int(row[2]),
                    "edgeKind": str(row[3]),
                    "status": str(row[4]),
                    "confidence": str(row[5]),
                    "evidenceUri": str(row[0]),
                    "evidenceRole": "CLASS_EDGE_EVIDENCE",
                    "sourceRevisionId": (
                        revision.get("revisionId")
                        if revision is not None
                        else None
                    ),
                    "sourceRevision": revision,
                    "freshness": (
                        str(revision.get("freshness") or "UNKNOWN")
                        if revision is not None
                        else "UNKNOWN"
                    ),
                }
            )
    return evidence


def effective_class_evidence_freshness(
    evidence: Iterable[dict[str, object]],
) -> str:
    """Evaluate every class proof independently of response evidence limits."""

    items = list(evidence)
    if any(
        str(item.get("freshness") or "UNKNOWN").upper() == "STALE"
        or str(item.get("status") or "").upper() == "STALE"
        for item in items
    ):
        return "STALE"
    if not items:
        return "UNKNOWN"
    complete = all(
        source_revision_is_fresh(
            item.get("sourceRevision"),
            require_revision_id=(
                str(item.get("evidenceRole") or "")
                != "NATIVE_ROOT_CLASS_REVISION"
            ),
        )
        and is_valid_generic_evidence_uri(item.get("evidenceUri"))
        and (
            not item.get("status")
            or str(item.get("status")).upper()
            in CLASS_EVIDENCE_COMPLETE_STATUSES
        )
        and (
            not item.get("confidence")
            or str(item.get("confidence")).upper() in COMPLETE_CONFIDENCE
        )
        for item in items
    )
    return "FRESH" if complete else "UNKNOWN"


def plan_query(
    connection: sqlite3.Connection,
    requirements: QueryRequirements,
) -> dict[str, object]:
    """Plan and answer from Core when every requested evidence gate is closed."""

    evidence_limit = _bounded_limit(requirements.evidence_limit)
    answer_mode, inferred_answer_mode = _resolved_answer_mode(requirements)
    contract_gap = _request_contract_gap(requirements, answer_mode)
    if contract_gap is not None:
        return _request_gap_result(
            answer_mode=answer_mode,
            gap=contract_gap,
        )
    candidates = resolve_entities(connection, requirements.entity_query)
    missing: list[dict[str, str]] = []
    if not candidates:
        missing.append(
            {"code": "NO_ENTITY_MATCH", "requirement": "unique entity"}
        )
        return {
            "answerMode": answer_mode,
            "status": "GAP",
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
            "answerMode": answer_mode,
            "status": "AMBIGUOUS",
            "route": (
                "EVIDENCE_REQUIRED"
                if inferred_answer_mode
                else "AMBIGUOUS"
            ),
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
    identity_source_freshness = str(entity["freshness"]).upper()
    identity_revision_fresh = source_revision_is_fresh(
        entity.get("sourceRevision")
    )
    identity_confirmed = (
        str(entity.get("status") or "").upper()
        in IDENTITY_COMPLETE_STATUSES
        and str(entity.get("confidence") or "").upper()
        in COMPLETE_CONFIDENCE
    )
    identity_freshness = (
        "STALE"
        if identity_source_freshness == "STALE"
        else (
            "FRESH"
            if identity_confirmed and identity_revision_fresh
            else "UNKNOWN"
        )
    )
    identity_gap = (
        {
            "code": "STALE_SOURCE",
            "requirement": "fresh identity source revision",
        }
        if identity_source_freshness == "STALE"
        else (
            {
                "code": "AMBIGUOUS_ENTITY",
                "requirement": "confirmed high-confidence identity",
            }
            if not identity_confirmed
            else (
                {
                    "code": "IDENTITY_PROVENANCE_UNKNOWN",
                    "requirement": "identity source revision",
                }
                if not identity_revision_fresh
                else None
            )
        )
    )
    if answer_mode == "IDENTITY":
        identity_evidence = _identity_evidence(entity)
        identity_missing = [identity_gap] if identity_gap is not None else []
        return {
            "answerMode": answer_mode,
            "status": "PARTIAL" if identity_missing else "COMPLETE",
            "route": (
                "DB_PARTIAL"
                if identity_missing
                else "IDENTITY_ONLY_COMPLETE"
            ),
            "entity": entity,
            "entityCandidates": candidates,
            "facts": [],
            "relationships": [],
            "evidence": identity_evidence,
            "returned": 1 + len(identity_evidence),
            "omitted": 0,
            "freshness": identity_freshness,
            "missingRequirements": identity_missing,
            "recommendedProbes": (
                [_probe(str(identity_gap["code"]), entity)]
                if identity_gap is not None
                else []
            ),
        }
    if identity_gap is not None:
        missing.append(identity_gap)
    entity_id = int(entity["entityId"])
    facts: list[dict[str, object]] = []
    for fact_type in requirements.fact_types:
        normalized_type = fact_type.upper()
        gate_matched = _fact_rows(
            connection,
            entity_id=entity_id,
            fact_type=normalized_type,
            fact_names=requirements.fact_names,
            limit=None,
        )
        matched = gate_matched[:evidence_limit]
        facts.extend(matched)
        matched_names = {
            str(fact["factName"]) for fact in gate_matched
        }
        missing_names = [
            fact_name
            for fact_name in dict.fromkeys(requirements.fact_names)
            if fact_name not in matched_names
        ]
        if missing_names:
            missing.extend(
                {
                    "code": "FACT_NOT_FOUND",
                    "requirement": f"{normalized_type}:{fact_name}",
                }
                for fact_name in missing_names
            )
        elif not matched:
            missing.append(
                {
                    "code": "FACT_NOT_FOUND",
                    "requirement": normalized_type,
                }
            )
            continue
        for fact_name in sorted(_conflicting_fact_names(gate_matched)):
            missing.append(
                {
                    "code": "FACT_AMBIGUOUS",
                    "requirement": f"{normalized_type}:{fact_name}",
                }
            )
        for fact in gate_matched:
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
                        "code": "FACT_STALE",
                        "requirement": (
                            f"{normalized_type}:{fact['factName']}"
                        ),
                    }
                )
            elif (
                status == "AMBIGUOUS"
                or resolution == "AMBIGUOUS_INHERITANCE"
            ):
                missing.append(
                    {
                        "code": "FACT_AMBIGUOUS",
                        "requirement": (
                            f"{normalized_type}:{fact['factName']}"
                        ),
                    }
                )
            elif (
                status not in COMPLETE_STATUSES
                or str(fact.get("confidence") or "").upper()
                not in COMPLETE_CONFIDENCE
                or resolution in OPEN_STATUSES
                or not fact_value_is_usable(fact)
            ):
                missing.append(
                    {
                        "code": (
                            "FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED"
                        ),
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
    relationship_evidence: list[dict[str, object]] = []
    relationship_gate_freshness: list[str] = []
    handled_map_edge_types: set[str] = set()
    for edge_type in requirements.edge_types:
        normalized_edge_type = edge_type.upper()
        if normalized_edge_type in MAP_USAGE_EDGE_TYPES:
            (
                map_relationships,
                map_evidence,
                map_missing,
                map_freshness,
            ) = _map_usage_requirement(
                connection,
                entity_id=entity_id,
                edge_types=(normalized_edge_type,),
                limit=evidence_limit,
            )
            relationships.extend(map_relationships)
            relationship_evidence.extend(map_evidence)
            missing.extend(map_missing)
            relationship_gate_freshness.append(map_freshness)
            handled_map_edge_types.add(normalized_edge_type)
            continue
        if normalized_edge_type == "ASSET_CLASS":
            projected_rows = _class_assignment_relationships(
                connection,
                entity_id=entity_id,
            )
        elif normalized_edge_type in FACT_BACKED_RELATIONSHIP_RULES:
            projected_rows = _fact_backed_relationships(
                connection,
                entity_id=entity_id,
                edge_type=normalized_edge_type,
            )
        else:
            rows = list(
                connection.execute(
                    """
                    SELECT
                        edge.edge_id, edge.edge_type, edge.edge_strength,
                        edge.status, edge.confidence, target.entity_id,
                        target.canonical_uri, edge.evidence_uri,
                        revision.revision_id, revision.source_kind,
                        revision.source_uri, revision.source_fingerprint,
                        revision.producer_version, revision.schema_version,
                        revision.generated_at,
                        revision.freshness_status
                    FROM edges AS edge
                    JOIN entities AS target
                      ON target.entity_id=edge.target_entity_id
                    JOIN source_revisions AS revision
                      ON revision.revision_id=edge.source_revision_id
                    WHERE edge.source_entity_id=? AND edge.edge_type=?
                    ORDER BY edge.edge_id
                    """,
                    (entity_id, normalized_edge_type),
                )
            )
            projected_rows = []
            for row in rows:
                source_revision = {
                    "revisionId": int(row[8]),
                    "sourceKind": str(row[9]),
                    "sourceUri": str(row[10]),
                    "sourceFingerprint": str(row[11]),
                    "producerVersion": str(row[12]),
                    "schemaVersion": str(row[13]),
                    "generatedAt": str(row[14]),
                    "freshness": str(row[15]),
                }
                edge_evidence = {
                    "edgeId": int(row[0]),
                    "evidenceUri": str(row[7]),
                    "evidenceRole": "EDGE_EVIDENCE",
                    "sourceRevisionId": int(row[8]),
                    "sourceRevision": source_revision,
                    "freshness": str(row[15]),
                }
                projected_rows.append(
                    {
                        "edgeId": int(row[0]),
                        "edgeType": str(row[1]),
                        "edgeStrength": str(row[2]),
                        "status": str(row[3]),
                        "confidence": str(row[4]),
                        "targetEntityId": int(row[5]),
                        "targetUri": str(row[6]),
                        "evidenceUri": str(row[7]),
                        "sourceRevisionId": int(row[8]),
                        "sourceRevision": source_revision,
                        "freshness": str(row[15]),
                        "evidence": [edge_evidence],
                    }
                )
        if not projected_rows:
            missing.append(
                {
                    "code": "REFERENCE_CLOSURE_OPEN",
                    "requirement": (
                        f"{normalized_edge_type}:confirmed edge evidence"
                    ),
                }
            )
            relationship_gate_freshness.append("UNKNOWN")
            continue
        usable_rows = [
            relationship
            for relationship in projected_rows
            if str(relationship["status"]).upper()
            in RELATIONSHIP_COMPLETE_STATUSES
            and str(relationship["confidence"]).upper()
            in {"HIGH", "CONFIRMED"}
            and str(relationship["freshness"]).upper() == "FRESH"
            and source_revision_is_fresh(
                relationship.get("sourceRevision")
            )
            and is_valid_generic_evidence_uri(
                relationship["evidenceUri"]
            )
        ]
        if usable_rows:
            returned_usable_rows = usable_rows[:evidence_limit]
            relationships.extend(returned_usable_rows)
            relationship_evidence.extend(
                evidence
                for relationship in returned_usable_rows
                for evidence in relationship.get("evidence", [])
            )
            relationship_gate_freshness.append("FRESH")
            continue
        returned_projected_rows = projected_rows[:evidence_limit]
        relationships.extend(returned_projected_rows)
        relationship_evidence.extend(
            evidence
            for relationship in returned_projected_rows
            for evidence in relationship.get("evidence", [])
        )
        has_stale_row = any(
            str(relationship["status"]).upper() == "STALE"
            or str(relationship["freshness"]).upper() == "STALE"
            for relationship in projected_rows
        )
        if has_stale_row:
            missing.append(
                {
                    "code": "STALE_SOURCE",
                    "requirement": (
                        f"{normalized_edge_type}:fresh confirmed edge evidence"
                    ),
                }
            )
            relationship_gate_freshness.append("STALE")
        else:
            missing.append(
                {
                    "code": "REFERENCE_CLOSURE_OPEN",
                    "requirement": (
                        f"{normalized_edge_type}:confirmed edge evidence"
                    ),
                }
            )
            edge_freshness = {
                str(relationship["freshness"]).upper()
                for relationship in projected_rows
            }
            relationship_gate_freshness.append(
                "FRESH" if edge_freshness == {"FRESH"} else "UNKNOWN"
            )
    if requirements.requires_native:
        native_rows = _native_mechanism_rows(
            connection,
            entity_id=entity_id,
        )
        projected_native = [
            _native_mechanism_projection(row, entity=entity)
            for row in native_rows
        ]
        usable_native = [
            (relationship, evidence_items)
            for relationship, evidence_items in projected_native
            if str(relationship["status"]).upper()
            in RELATIONSHIP_COMPLETE_STATUSES
            and str(relationship["functionStatus"]).upper()
            in RELATIONSHIP_COMPLETE_STATUSES
            and str(relationship["confidence"]).upper()
            in {"HIGH", "CONFIRMED"}
            and str(relationship["functionConfidence"]).upper()
            in {"HIGH", "CONFIRMED"}
            and str(relationship["freshness"]).upper() == "FRESH"
            and is_valid_blueprint_graph_evidence_uri(
                relationship["evidenceUri"]
            )
            and is_recovered_identifier(
                relationship["blueprintFunctionName"]
            )
            and bool(str(relationship["nativeEvidenceUri"]).strip())
            and len(evidence_items) == 2
            and all(
                source_revision_is_fresh(item.get("sourceRevision"))
                for item in evidence_items
            )
        ]
        selected_native = (
            usable_native or projected_native
        )[:evidence_limit]
        relationships.extend(
            relationship for relationship, _ in selected_native
        )
        relationship_evidence.extend(
            evidence_item
            for _, evidence_items in selected_native
            for evidence_item in evidence_items
        )
        if usable_native:
            relationship_gate_freshness.append("FRESH")
        else:
            missing.append(
                {
                    "code": "NATIVE_BOUNDARY_UNRESOLVED",
                    "requirement": (
                        "fresh confirmed Blueprint-native callsite "
                        "with graph and native evidence"
                    ),
                }
            )
            native_freshness = {
                str(relationship["freshness"]).upper()
                for relationship, _ in projected_native
            }
            if "STALE" in native_freshness:
                missing.append(
                    {
                        "code": "STALE_SOURCE",
                        "requirement": "Blueprint-native evidence",
                    }
                )
                relationship_gate_freshness.append("STALE")
            else:
                relationship_gate_freshness.append("UNKNOWN")
    if requirements.requires_runtime:
        runtime_gate_rows = _fact_rows(
            connection,
            entity_id=entity_id,
            fact_type="RUNTIME_OBSERVATION",
            fact_names=requirements.fact_names,
            limit=None,
        )
        runtime_rows = runtime_gate_rows[:evidence_limit]
        known_fact_ids = {
            fact["factId"] for fact in facts if fact["factId"] is not None
        }
        facts.extend(
            fact
            for fact in runtime_rows
            if fact["factId"] not in known_fact_ids
        )
        runtime_usable = [
            fact
            for fact in runtime_gate_rows
            if str(fact["status"] or "").upper() in COMPLETE_STATUSES
            and str(fact.get("confidence") or "").upper()
            in COMPLETE_CONFIDENCE
            and fact_value_is_usable(fact)
        ]
        runtime_usable_names = {
            str(fact["factName"]) for fact in runtime_usable
        }
        missing.extend(
            {
                "code": "FACT_AMBIGUOUS",
                "requirement": f"RUNTIME_OBSERVATION:{fact_name}",
            }
            for fact_name in sorted(
                _conflicting_fact_names(runtime_usable)
            )
        )
        missing_runtime_names = [
            fact_name
            for fact_name in dict.fromkeys(requirements.fact_names)
            if fact_name not in runtime_usable_names
        ]
        if missing_runtime_names:
            missing.extend(
                {
                    "code": "FACT_NOT_FOUND",
                    "requirement": (
                        f"RUNTIME_OBSERVATION:{fact_name}"
                    ),
                }
                for fact_name in missing_runtime_names
            )
        elif not runtime_usable:
            missing.append(
                {
                    "code": "RUNTIME_DYNAMIC_BRANCH",
                    "requirement": (
                        "materialized confirmed runtime observation"
                    ),
                }
            )
    if requirements.requires_map_evidence and not handled_map_edge_types:
        (
            map_relationships,
            map_evidence,
            map_missing,
            map_freshness,
        ) = _map_usage_requirement(
            connection,
            entity_id=entity_id,
            edge_types=MAP_USAGE_EDGE_TYPES,
            limit=evidence_limit,
        )
        relationships.extend(map_relationships)
        relationship_evidence.extend(map_evidence)
        missing.extend(map_missing)
        relationship_gate_freshness.append(map_freshness)
    evidence, fact_evidence_total = _fact_evidence(
        connection,
        (fact["factId"] for fact in facts),
        limit=evidence_limit,
    )
    class_evidence = load_effective_class_evidence(
        connection,
        entity_id=entity_id,
        effective_facts=effective_facts,
    )
    class_gate_freshness: list[str] = []
    resolved_effective_facts = [
        fact
        for fact in effective_facts
        if fact.get("factId") is not None
        and str(fact.get("resolutionStatus") or "").upper() == "RESOLVED"
    ]
    if resolved_effective_facts:
        class_freshness = effective_class_evidence_freshness(
            class_evidence
        )
        if class_freshness == "STALE":
            missing.append(
                {
                    "code": "STALE_SOURCE",
                    "requirement": "fresh effective-class evidence",
                }
            )
            class_gate_freshness.append("STALE")
        elif class_freshness == "FRESH":
            class_gate_freshness.append("FRESH")
        else:
            missing.append(
                {
                    "code": "PARENT_CHAIN_OPEN",
                    "requirement": (
                        "confirmed fresh effective-class evidence"
                    ),
                }
            )
            class_gate_freshness.append("UNKNOWN")
    identity_evidence = _identity_evidence(entity)
    evidence = [
        *identity_evidence,
        *evidence,
        *class_evidence,
        *relationship_evidence,
    ][:evidence_limit]
    evidence_total = (
        len(identity_evidence)
        + fact_evidence_total
        + len(class_evidence)
        + len(relationship_evidence)
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
    visible_fresh_fact_evidence = {
        int(fact_id)
        for item in evidence
        if (fact_id := item.get("factId")) is not None
        and str(item.get("freshness") or "").upper() == "FRESH"
        and source_revision_is_fresh(item.get("sourceRevision"))
        and is_valid_generic_evidence_uri(item.get("evidenceUri"))
    }
    for fact_id in sorted(
        (returned_fact_ids & fresh_fact_ids)
        - visible_fresh_fact_evidence
    ):
        fact = next(
            item for item in facts if item.get("factId") == fact_id
        )
        missing.append(
            {
                "code": "EVIDENCE_LIMIT_INSUFFICIENT",
                "requirement": (
                    f"{fact['factType']}:{fact['factName']}"
                ),
            }
        )
    missing_current_fact = any(
        fact["factId"] is not None
        and (fact["valueKind"] is None or fact["status"] is None)
        for fact in facts
    )
    facts_by_id = {
        int(fact_id): fact
        for fact in facts
        if (fact_id := fact["factId"]) is not None
    }
    for fact_id in sorted(returned_fact_ids - fresh_fact_ids):
        fact = facts_by_id[fact_id]
        missing.append(
            {
                "code": (
                    "FACT_STALE"
                    if fact_id in evidenced_fact_ids
                    else "FACT_EXISTS_BUT_VALUE_NOT_MATERIALIZED"
                ),
                "requirement": (
                    f"{fact['factType']}:{fact['factName']}"
                ),
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
    returned_fact_is_stale = any(
        str(fact.get("status") or "").upper() == "STALE"
        or str(fact.get("resolutionStatus") or "").upper() == "STALE"
        for fact in facts
        if fact.get("factId") is not None
    )
    if returned_fact_is_stale:
        fact_freshness = "STALE"
    elif (
        returned_fact_ids
        and returned_fact_ids <= fresh_fact_ids
        and not missing_current_fact
    ):
        fact_freshness = "FRESH"
    elif (returned_fact_ids - fresh_fact_ids) & evidenced_fact_ids:
        fact_freshness = "STALE"
    else:
        fact_freshness = "UNKNOWN"
    requested_freshness = [
        identity_freshness,
        *(
            [fact_freshness]
            if requirements.fact_types or requirements.requires_runtime
            else []
        ),
        *class_gate_freshness,
        *relationship_gate_freshness,
    ]
    if "STALE" in requested_freshness:
        freshness = "STALE"
    elif requested_freshness and all(
        value == "FRESH" for value in requested_freshness
    ):
        freshness = "FRESH"
    else:
        freshness = "UNKNOWN"
    has_partial_answer = bool(facts or relationships)
    if missing:
        route = (
            "DB_PARTIAL"
            if not inferred_answer_mode and has_partial_answer
            else "EVIDENCE_REQUIRED"
        )
        status = "PARTIAL" if has_partial_answer else "GAP"
    else:
        route = (
            "DB_ONLY_COMPLETE"
            if inferred_answer_mode
            else "DB_SEMANTIC_COMPLETE"
        )
        status = "COMPLETE"
    return {
        "answerMode": answer_mode,
        "status": status,
        "route": route,
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
