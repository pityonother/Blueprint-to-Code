"""Safe read/query service for ARK Knowledge Base vNext HTTP endpoints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Mapping
from urllib.parse import urlencode

from .kb_context import build_bounded_context_pack
from .query_planner import (
    CANDIDATE_EXPLANATION_SCHEMA_MIGRATION_REQUIRED,
    QueryRequirements,
    load_effective_candidate_explanations,
    plan_query,
)
from .schema_capabilities import core_schema_capabilities


MAX_PAGE_SIZE = 100
MAX_CURSOR = 1_000_000


class KnowledgeApiError(ValueError):
    def __init__(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _bounded_int(
    value: object,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise KnowledgeApiError(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_INVALID",
            f"{name} must be an integer.",
        ) from exc
    if not minimum <= parsed <= maximum:
        raise KnowledgeApiError(
            HTTPStatus.BAD_REQUEST,
            "REQUEST_INVALID",
            f"{name} must be between {minimum} and {maximum}.",
        )
    return parsed


class VNextKnowledgeService:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.core_path = self.root / "core.sqlite"
        self.cache_path = self.root / "cache.sqlite"
        self.manifest_path = self.root / "manifests" / "current.json"

    def _core(self) -> sqlite3.Connection:
        if not self.core_path.is_file():
            raise KnowledgeApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "KB_VNEXT_NOT_BUILT",
                "ARK Knowledge Base vNext snapshot is not available.",
            )
        connection = sqlite3.connect(
            f"file:{self.core_path.as_posix()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _page(
        self,
        *,
        items: list[dict[str, object]],
        total: int,
        limit: int,
        cursor: int,
        path: str,
        query: Mapping[str, object],
        freshness: str = "UNKNOWN",
        evidence: list[dict[str, object]] | None = None,
        gaps: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        next_cursor = cursor + len(items)
        next_query = ""
        if next_cursor < total:
            next_query = path + "?" + urlencode(
                {
                    **{
                        key: value
                        for key, value in query.items()
                        if value not in (None, "")
                    },
                    "limit": limit,
                    "cursor": next_cursor,
                }
            )
        return {
            "items": items,
            "returned": len(items),
            "omitted": max(0, total - cursor - len(items)),
            "nextQuery": next_query,
            "freshness": freshness,
            "evidence": evidence or [],
            "gap": gaps or [],
        }

    def health(self) -> dict[str, object]:
        if not self.core_path.is_file() or not self.manifest_path.is_file():
            return {
                "available": False,
                "status": "NOT_BUILT",
                "buildId": "",
                "cutover": {
                    "mode": "shadow",
                    "defaultQuerySource": "legacy",
                },
                "returned": 0,
                "omitted": 0,
                "nextQuery": "",
                "freshness": "UNKNOWN",
                "evidence": [],
                "capabilities": {
                    "effectiveCandidateExplanations": False,
                },
                "gap": [
                    {
                        "code": "KB_VNEXT_NOT_BUILT",
                        "detail": "Run a full vNext snapshot build.",
                    }
                ],
            }
        manifest = json.loads(
            self.manifest_path.read_text(encoding="utf-8")
        )
        with closing(self._core()) as core:
            integrity = str(
                core.execute("PRAGMA integrity_check").fetchone()[0]
            )
            metadata = dict(
                core.execute("SELECT key, value FROM metadata")
            )
            capabilities = core_schema_capabilities(core)
        compatible = bool(capabilities["compatible"])
        available = integrity == "ok" and compatible
        status = (
            "INVALID"
            if integrity != "ok"
            else ("READY" if compatible else "MIGRATION_REQUIRED")
        )
        gaps = []
        if integrity == "ok" and not compatible:
            gaps.append(
                {
                    "code": "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
                    "detail": (
                        "Build an ark-kb-core/v2 snapshot before enabling "
                        "vNext effective-default reads."
                    ),
                }
            )
        return {
            "available": available,
            "status": status,
            "buildId": str(manifest.get("buildId") or ""),
            "schemaVersion": str(capabilities["schemaVersion"]),
            "ontologyVersion": str(metadata.get("ontology_version") or ""),
            "cutover": manifest.get(
                "cutover",
                {"mode": "shadow", "defaultQuerySource": "legacy"},
            ),
            "returned": int(available),
            "omitted": 0,
            "nextQuery": "",
            "freshness": (
                "FRESH" if available else "UNKNOWN"
            ),
            "evidence": [
                {
                    "sourceUri": str(
                        manifest.get("source", {}).get("uri", "")
                    ),
                    "sha256": str(
                        manifest.get("source", {}).get("sha256", "")
                    ),
                }
            ],
            "capabilities": {
                "effectiveCandidateExplanations": bool(
                    capabilities["effectiveCandidateExplanations"]
                ),
            },
            "gap": gaps,
        }

    def search_entities(
        self,
        *,
        query: str,
        limit: object = 25,
        cursor: object = 0,
    ) -> dict[str, object]:
        query = query.strip()
        if not query:
            raise KnowledgeApiError(
                HTTPStatus.BAD_REQUEST,
                "REQUEST_INVALID",
                "q is required.",
            )
        page_size = _bounded_int(
            limit,
            name="limit",
            default=25,
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        )
        offset = _bounded_int(
            cursor,
            name="cursor",
            default=0,
            minimum=0,
            maximum=MAX_CURSOR,
        )
        escaped = (
            query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        with closing(self._core()) as core:
            parameters = (escaped, escaped, escaped, escaped)
            where = """
                entity.canonical_uri LIKE '%' || ? || '%' ESCAPE '\\'
                OR COALESCE(entity.display_name, '') LIKE '%' || ? || '%' ESCAPE '\\'
                OR COALESCE(entity.internal_name, '') LIKE '%' || ? || '%' ESCAPE '\\'
                OR COALESCE(alias.alias, '') LIKE '%' || ? || '%' ESCAPE '\\'
            """
            total = int(
                core.execute(
                    f"""
                    SELECT COUNT(DISTINCT entity.entity_id)
                    FROM entities AS entity
                    LEFT JOIN aliases AS alias
                      ON alias.entity_id=entity.entity_id
                    WHERE {where}
                    """,
                    parameters,
                ).fetchone()[0]
            )
            rows = core.execute(
                f"""
                SELECT DISTINCT
                    entity.entity_id, entity.canonical_uri,
                    entity.entity_kind, entity.display_name,
                    entity.internal_name, entity.status,
                    entity.confidence
                FROM entities AS entity
                LEFT JOIN aliases AS alias
                  ON alias.entity_id=entity.entity_id
                WHERE {where}
                ORDER BY entity.entity_id
                LIMIT ? OFFSET ?
                """,
                (*parameters, page_size, offset),
            )
            items = [
                {
                    "entityId": int(row["entity_id"]),
                    "canonicalUri": str(row["canonical_uri"]),
                    "entityKind": str(row["entity_kind"]),
                    "displayName": str(row["display_name"] or ""),
                    "internalName": str(row["internal_name"] or ""),
                    "status": str(row["status"]),
                    "confidence": str(row["confidence"]),
                }
                for row in rows
            ]
        return self._page(
            items=items,
            total=total,
            limit=page_size,
            cursor=offset,
            path="/api/kb/entities/search",
            query={"q": query},
            freshness="FRESH",
        )

    def _entity_exists(
        self, core: sqlite3.Connection, entity_id: int
    ) -> sqlite3.Row:
        row = core.execute(
            """
            SELECT * FROM entities WHERE entity_id=?
            """,
            (entity_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeApiError(
                HTTPStatus.NOT_FOUND,
                "KB_ENTITY_NOT_FOUND",
                "Knowledge entity was not found.",
            )
        return row

    def entity(self, entity_id: int) -> dict[str, object]:
        with closing(self._core()) as core:
            row = self._entity_exists(core, entity_id)
            roles = [
                {
                    "role": str(item[0]),
                    "confidence": str(item[1]),
                    "status": str(item[2]),
                    "reasons": json.loads(str(item[3])),
                }
                for item in core.execute(
                    """
                    SELECT role, confidence, status, reasons_json
                    FROM knowledge_roles WHERE entity_id=?
                    ORDER BY role
                    """,
                    (entity_id,),
                )
            ]
            domains = [
                {
                    "domainId": str(item[0]),
                    "membershipKind": str(item[1]),
                    "confidence": str(item[2]),
                    "status": str(item[3]),
                    "evidenceUri": str(item[4]),
                }
                for item in core.execute(
                    """
                    SELECT domain_id, membership_kind, confidence,
                           status, evidence_id
                    FROM domain_memberships WHERE entity_id=?
                    ORDER BY domain_id, membership_kind
                    """,
                    (entity_id,),
                )
            ]
        return {
            "entity": {
                "entityId": int(row["entity_id"]),
                "canonicalUri": str(row["canonical_uri"]),
                "entityKind": str(row["entity_kind"]),
                "displayName": str(row["display_name"] or ""),
                "internalName": str(row["internal_name"] or ""),
                "status": str(row["status"]),
                "confidence": str(row["confidence"]),
            },
            "roles": roles,
            "domains": domains,
            "returned": 1 + len(roles) + len(domains),
            "omitted": 0,
            "nextQuery": "",
            "freshness": "FRESH",
            "evidence": [
                {
                    "evidenceUri": domain["evidenceUri"],
                    "role": "DOMAIN_MEMBERSHIP",
                }
                for domain in domains
            ],
            "gap": [],
        }

    def entity_collection(
        self,
        entity_id: int,
        *,
        kind: str,
        limit: object = 50,
        cursor: object = 0,
    ) -> dict[str, object]:
        page_size = _bounded_int(
            limit,
            name="limit",
            default=50,
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        )
        offset = _bounded_int(
            cursor,
            name="cursor",
            default=0,
            minimum=0,
            maximum=MAX_CURSOR,
        )
        candidate_schema_unavailable = False
        with closing(self._core()) as core:
            self._entity_exists(core, entity_id)
            if kind == "facts":
                count_sql = (
                    "SELECT COUNT(*) FROM facts "
                    "WHERE subject_entity_id=? AND current=1"
                )
                rows_sql = """
                    SELECT
                        fact_id, fact_type, fact_name, scope_kind,
                        value_kind, value_text, value_number,
                        value_integer, value_json, unit, status, confidence
                    FROM facts
                    WHERE subject_entity_id=? AND current=1
                    ORDER BY fact_type, fact_name, fact_id
                    LIMIT ? OFFSET ?
                """
                total = int(core.execute(count_sql, (entity_id,)).fetchone()[0])
                rows = list(
                    core.execute(rows_sql, (entity_id, page_size, offset))
                )
                items = [
                    {
                        "factId": int(row[0]),
                        "factType": str(row[1]),
                        "factName": str(row[2]),
                        "scopeKind": str(row[3]),
                        "valueKind": str(row[4]),
                        "valueText": row[5],
                        "valueNumber": row[6],
                        "valueInteger": row[7],
                        "valueJson": row[8],
                        "unit": str(row[9]),
                        "status": str(row[10]),
                        "confidence": str(row[11]),
                    }
                    for row in rows
                ]
                fact_ids = [int(row[0]) for row in rows]
                evidence = self._evidence_for_facts(core, fact_ids)
            elif kind == "relationships":
                total = int(
                    core.execute(
                        """
                        SELECT COUNT(*) FROM edges
                        WHERE source_entity_id=? OR target_entity_id=?
                        """,
                        (entity_id, entity_id),
                    ).fetchone()[0]
                )
                rows = core.execute(
                    """
                    SELECT
                        edge.edge_id, source.canonical_uri,
                        target.canonical_uri, edge.edge_type,
                        edge.edge_strength, edge.status, edge.confidence,
                        edge.evidence_uri
                    FROM edges AS edge
                    JOIN entities AS source
                      ON source.entity_id=edge.source_entity_id
                    JOIN entities AS target
                      ON target.entity_id=edge.target_entity_id
                    WHERE edge.source_entity_id=? OR edge.target_entity_id=?
                    ORDER BY edge.edge_id
                    LIMIT ? OFFSET ?
                    """,
                    (entity_id, entity_id, page_size, offset),
                )
                items = [
                    {
                        "edgeId": int(row[0]),
                        "sourceUri": str(row[1]),
                        "targetUri": str(row[2]),
                        "edgeType": str(row[3]),
                        "edgeStrength": str(row[4]),
                        "status": str(row[5]),
                        "confidence": str(row[6]),
                        "evidenceUri": str(row[7]),
                    }
                    for row in rows
                ]
                evidence = [
                    {
                        "evidenceUri": item["evidenceUri"],
                        "role": "EDGE_EVIDENCE",
                    }
                    for item in items
                ]
            elif kind == "coverage":
                total = int(
                    core.execute(
                        "SELECT COUNT(*) FROM coverage WHERE entity_id=?",
                        (entity_id,),
                    ).fetchone()[0]
                )
                rows = core.execute(
                    """
                    SELECT * FROM coverage
                    WHERE entity_id=? ORDER BY stage
                    LIMIT ? OFFSET ?
                    """,
                    (entity_id, page_size, offset),
                )
                items = [
                    {
                        "stage": str(row["stage"]),
                        "status": str(row["status"]),
                        "confirmed": int(row["confirmed_count"]),
                        "heuristic": int(row["heuristic_count"]),
                        "ambiguous": int(row["ambiguous_count"]),
                        "notRecovered": int(row["not_recovered_count"]),
                        "sourceNotAvailable": int(
                            row["source_not_available_count"]
                        ),
                        "stale": int(row["stale_count"]),
                        "failureReason": str(row["failure_reason"]),
                    }
                    for row in rows
                ]
                evidence = []
            elif kind == "effective-defaults":
                total = int(
                    core.execute(
                        "SELECT COUNT(*) FROM effective_facts WHERE entity_id=?",
                        (entity_id,),
                    ).fetchone()[0]
                )
                rows = core.execute(
                    """
                    SELECT
                        effective.fact_name, effective.fact_id,
                        effective.inherited_from_entity_id,
                        effective.resolution_chain_json,
                        effective.resolution_status,
                        effective.source_revision_set_hash,
                        fact.value_kind, fact.value_text, fact.value_number,
                        fact.value_integer, fact.value_json, fact.status,
                        fact.confidence
                    FROM effective_facts AS effective
                    LEFT JOIN facts AS fact
                      ON fact.fact_id=effective.fact_id
                     AND fact.current=1
                    WHERE effective.entity_id=?
                    ORDER BY effective.fact_name
                    LIMIT ? OFFSET ?
                    """,
                    (entity_id, page_size, offset),
                )
                rows = list(rows)
                items = [
                    {
                        "factName": str(row[0]),
                        "factId": (
                            int(row[1]) if row[1] is not None else None
                        ),
                        "inheritedFromEntityId": row[2],
                        "resolutionChain": json.loads(str(row[3])),
                        "resolutionStatus": str(row[4]),
                        "sourceRevisionSetHash": str(row[5]),
                        "valueKind": (
                            str(row[6]) if row[6] is not None else None
                        ),
                        "valueText": row[7],
                        "valueNumber": row[8],
                        "valueInteger": row[9],
                        "valueJson": row[10],
                        "status": (
                            str(row[11]) if row[11] is not None else None
                        ),
                        "confidence": (
                            str(row[12]) if row[12] is not None else None
                        ),
                    }
                    for row in rows
                ]
                candidate_explanations = (
                    load_effective_candidate_explanations(
                        core,
                        entity_id=entity_id,
                        fact_names=(
                            str(item["factName"]) for item in items
                        ),
                    )
                )
                for item in items:
                    item.update(
                        candidate_explanations[str(item["factName"])]
                    )
                candidate_schema_unavailable = any(
                    item.get("candidateExplanationStatus")
                    == CANDIDATE_EXPLANATION_SCHEMA_MIGRATION_REQUIRED
                    for item in items
                )
                evidence = self._evidence_for_facts(
                    core, [row[1] for row in rows]
                )
            else:
                raise AssertionError(kind)
        stale_evidence_fact_ids: set[int] = set()
        unknown_evidence_fact_ids: set[int] = set()
        if kind == "effective-defaults":
            returned_fact_ids = {
                int(fact_id)
                for item in items
                if (fact_id := item.get("factId")) is not None
            }
            evidenced_fact_ids = {
                int(fact_id)
                for item in evidence
                if (fact_id := item.get("factId")) is not None
                and item.get("evidenceUri")
            }
            fresh_fact_ids = {
                int(fact_id)
                for item in evidence
                if (fact_id := item.get("factId")) is not None
                and item.get("evidenceUri")
                and str(item.get("freshness") or "").upper() == "FRESH"
            }
            stale_evidence_fact_ids = (
                returned_fact_ids - fresh_fact_ids
            ) & evidenced_fact_ids
            unknown_evidence_fact_ids = (
                returned_fact_ids - evidenced_fact_ids
            )
            gaps = [
                {
                    "code": "COVERAGE_OPEN",
                    "detail": str(item["resolutionStatus"]),
                }
                for item in items
                if item["factId"] is None
                or item["resolutionStatus"] != "RESOLVED"
            ]
            gaps.extend(
                {
                    "code": "COVERAGE_OPEN",
                    "detail": (
                        "EFFECTIVE_DEFAULT:"
                        + str(item["factName"])
                        + ":CURRENT_FACT_MISSING"
                    ),
                }
                for item in items
                if item["factId"] is not None
                and (
                    item.get("valueKind") is None
                    or item.get("status") is None
                )
            )
            if candidate_schema_unavailable:
                gaps.append(
                    {
                        "code": "KB_VNEXT_SCHEMA_MIGRATION_REQUIRED",
                        "detail": (
                            "Core v2 effective candidate lineage is "
                            "required for a complete explanation."
                        ),
                    }
                )
            gaps.extend(
                {
                    "code": "STALE_SOURCE",
                    "detail": (
                        "EFFECTIVE_DEFAULT:"
                        + str(item["factName"])
                        + ":FRESH_EVIDENCE_REQUIRED"
                    ),
                }
                for item in items
                if item.get("factId") in (
                    stale_evidence_fact_ids
                    | unknown_evidence_fact_ids
                )
            )
        else:
            gaps = [
                {
                    "code": "COVERAGE_OPEN",
                    "detail": item.get("failureReason", ""),
                }
                for item in items
                if item.get("status")
                in {
                    "UNKNOWN",
                    "AMBIGUOUS",
                    "NOT_RECOVERED",
                    "SOURCE_NOT_AVAILABLE",
                    "STALE",
                }
            ]
        stale = any(
            item.get("status") == "STALE"
            or item.get("resolutionStatus") == "STALE"
            for item in items
        )
        unresolved_effective = (
            kind == "effective-defaults"
            and (
                candidate_schema_unavailable
                or any(
                    item.get("factId") is None
                    or item.get("resolutionStatus") != "RESOLVED"
                    or item.get("valueKind") is None
                    or item.get("status") is None
                    for item in items
                )
            )
        )
        return self._page(
            items=items,
            total=total,
            limit=page_size,
            cursor=offset,
            path=f"/api/kb/entities/{entity_id}/{kind}",
            query={},
            freshness=(
                "STALE" if stale or stale_evidence_fact_ids else (
                    "UNKNOWN"
                    if unresolved_effective or unknown_evidence_fact_ids
                    else "FRESH"
                )
            ),
            evidence=evidence,
            gaps=gaps,
        )

    def _evidence_for_facts(
        self,
        core: sqlite3.Connection,
        fact_ids: list[int | None],
    ) -> list[dict[str, object]]:
        values = sorted(
            {int(fact_id) for fact_id in fact_ids if fact_id is not None}
        )
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        return [
            {
                "factId": int(row[0]),
                "sourceRevisionId": int(row[1]),
                "evidenceUri": str(row[2]),
                "evidenceRole": str(row[3]),
                "freshness": str(row[4]),
            }
            for row in core.execute(
                f"""
                SELECT
                    evidence.fact_id, evidence.source_revision_id,
                    evidence.evidence_uri, evidence.evidence_role,
                    revision.freshness_status
                FROM fact_evidence AS evidence
                JOIN source_revisions AS revision
                  ON revision.revision_id=evidence.source_revision_id
                WHERE evidence.fact_id IN ({placeholders})
                ORDER BY evidence.fact_id, evidence.evidence_uri
                """,
                values,
            )
        ]

    def query(self, body: Mapping[str, object]) -> dict[str, object]:
        allowed = {
            "entity",
            "factTypes",
            "factNames",
            "edgeTypes",
            "requiresNative",
            "requiresRuntime",
            "requiresMapEvidence",
            "evidenceLimit",
            "budgetTokens",
        }
        if extra := set(body) - allowed:
            raise KnowledgeApiError(
                HTTPStatus.BAD_REQUEST,
                "REQUEST_INVALID",
                f"Unsupported query fields: {sorted(extra)}",
            )
        entity_query = str(body.get("entity") or "").strip()
        if not entity_query or len(entity_query) > 500:
            raise KnowledgeApiError(
                HTTPStatus.BAD_REQUEST,
                "REQUEST_INVALID",
                "entity is required and must be at most 500 characters.",
            )

        def string_array(key: str, maximum: int = 20) -> tuple[str, ...]:
            raw = body.get(key, [])
            if not isinstance(raw, list) or len(raw) > maximum:
                raise KnowledgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "REQUEST_INVALID",
                    f"{key} must be an array with at most {maximum} items.",
                )
            values = tuple(str(item).strip() for item in raw)
            if any(not value or len(value) > 160 for value in values):
                raise KnowledgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "REQUEST_INVALID",
                    f"{key} contains an invalid value.",
                )
            return values

        evidence_limit = _bounded_int(
            body.get("evidenceLimit"),
            name="evidenceLimit",
            default=50,
            minimum=1,
            maximum=100,
        )
        budget = _bounded_int(
            body.get("budgetTokens"),
            name="budgetTokens",
            default=2_000,
            minimum=300,
            maximum=2_000,
        )
        request = QueryRequirements(
            entity_query=entity_query,
            fact_types=string_array("factTypes"),
            fact_names=string_array("factNames"),
            edge_types=string_array("edgeTypes"),
            requires_native=bool(body.get("requiresNative", False)),
            requires_runtime=bool(body.get("requiresRuntime", False)),
            requires_map_evidence=bool(
                body.get("requiresMapEvidence", False)
            ),
            evidence_limit=evidence_limit,
        )
        with closing(self._core()) as core:
            result = plan_query(core, request)
        pack = build_bounded_context_pack(
            result, budget_tokens=budget
        )
        response = {
            **result,
            "contextPack": pack,
            "nextQuery": "",
            "gap": result["missingRequirements"],
        }
        self._cache_query(body, response)
        return response

    def _cache_query(
        self,
        request: Mapping[str, object],
        response: Mapping[str, object],
    ) -> None:
        if not self.cache_path.is_file():
            return
        request_json = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        response_json = json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        fingerprint = hashlib.sha256(
            request_json.encode("utf-8")
        ).hexdigest()
        evidence = response.get("evidence")
        revision_ids = sorted(
            {
                int(item["sourceRevisionId"])
                for item in evidence
                if isinstance(item, Mapping)
                and item.get("sourceRevisionId") is not None
            }
        ) if isinstance(evidence, list) else []
        revision_hash = hashlib.sha256(
            json.dumps(revision_ids, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        invalidation_token = hashlib.sha256(
            (
                revision_hash
                + str(response.get("freshness"))
                + json.dumps(response.get("gap"), sort_keys=True)
            ).encode("utf-8")
        ).hexdigest()
        snapshot_id = "query-snapshot://" + fingerprint
        context_pack_id = "context-pack://" + fingerprint
        now = datetime.now(UTC)
        created_at = now.isoformat(timespec="seconds")
        expires_at = (now + timedelta(hours=1)).isoformat(
            timespec="seconds"
        )
        cache = sqlite3.connect(self.cache_path)
        try:
            cache.execute(
                """
                INSERT OR REPLACE INTO query_snapshots VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, 'VALID'
                )
                """,
                (
                    snapshot_id,
                    fingerprint,
                    request_json,
                    response_json,
                    revision_hash,
                    invalidation_token,
                    created_at,
                    expires_at,
                ),
            )
            pack = response["contextPack"]
            cache.execute(
                """
                INSERT OR REPLACE INTO context_packs VALUES (
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    context_pack_id,
                    snapshot_id,
                    str(pack["content"]),
                    int(pack["estimatedTokens"]),
                    int(pack["returned"]),
                    int(pack["omitted"]),
                    created_at,
                ),
            )
            cache.commit()
        finally:
            cache.close()
