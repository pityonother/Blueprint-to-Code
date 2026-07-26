"""Bounded, source-fingerprinted queries over Native Evidence SQLite."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from .context_pack import estimate_tokens


NATIVE_QUERY_SCHEMA = "blueprint-to-code-native-query/v1"
MIN_BUDGET_TOKENS = 500
MAX_BUDGET_TOKENS = 8000
DEFAULT_BUDGET_TOKENS = 1000
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_CALL_DEPTH = 3


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _payload(value: object) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _cursor_encode(payload: Mapping[str, object]) -> str:
    raw = _compact_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_decode(value: object) -> dict[str, object]:
    text = str(value or "")
    if not text:
        raise ValueError("cursor must not be empty")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("INVALID_CURSOR: cursor cannot be decoded") from exc
    if not isinstance(payload, dict):
        raise ValueError("INVALID_CURSOR: cursor payload must be an object")
    return payload


def _query_signature(operation: str, request: Mapping[str, object]) -> str:
    excluded = {"cursor", "budgetTokens", "pageSize"}
    normalized = {
        key: value
        for key, value in request.items()
        if key not in excluded
    }
    normalized["operation"] = operation
    return hashlib.sha256(_compact_json(normalized).encode("utf-8")).hexdigest()[:20]


def _status(value: object, default: str = "CONFIRMED") -> str:
    normalized = str(value or default).strip().upper().replace("-", "_")
    return normalized or default


class NativeEvidenceQueryService:
    """Read-only query surface independent from the SQLite storage details."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        evidence_set_id: str,
        source_sha256: str,
    ) -> None:
        self._connection = connection
        self.evidence_set_id = evidence_set_id
        self.source_sha256 = source_sha256

    def query(self, request: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(request, Mapping):
            raise TypeError("request must be a mapping")
        operation = str(request.get("operation") or "").strip().casefold()
        handlers = {
            "overview": self._overview,
            "search": self._search,
            "function": self._function,
            "callers": lambda value: self._relations(value, direction="callers"),
            "callees": lambda value: self._relations(value, direction="callees"),
            "field-accesses": self._field_accesses,
            "constants": self._constants,
            "gaps": self._gaps,
            "blueprint-links": self._blueprint_links,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise ValueError(
                f"unsupported native evidence query operation: {operation or '<empty>'}"
            )
        requested_budget, effective_budget = self._budget(request)
        page_size = self._page_size(request)
        items, gaps, query_details = handler(request)
        signature = _query_signature(operation, request)
        start = self._cursor_start(request.get("cursor"), signature)
        if start > len(items if operation != "gaps" else gaps):
            raise ValueError("INVALID_CURSOR: cursor offset exceeds result set")
        candidates = gaps if operation == "gaps" else items
        return self._bounded_response(
            operation=operation,
            request=request,
            query_details=query_details,
            candidates=candidates,
            gaps_mode=operation == "gaps",
            start=start,
            page_size=page_size,
            requested_budget=requested_budget,
            effective_budget=effective_budget,
            signature=signature,
        )

    @staticmethod
    def _budget(request: Mapping[str, object]) -> tuple[int, int]:
        try:
            requested = int(request.get("budgetTokens", DEFAULT_BUDGET_TOKENS))
        except (TypeError, ValueError) as exc:
            raise ValueError("budgetTokens must be an integer") from exc
        if requested < MIN_BUDGET_TOKENS:
            raise ValueError(
                f"budgetTokens must be at least {MIN_BUDGET_TOKENS}"
            )
        return requested, min(requested, MAX_BUDGET_TOKENS)

    @staticmethod
    def _page_size(request: Mapping[str, object]) -> int:
        try:
            value = int(request.get("pageSize", DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError) as exc:
            raise ValueError("pageSize must be an integer") from exc
        if value <= 0:
            raise ValueError("pageSize must be positive")
        return min(value, MAX_PAGE_SIZE)

    def _cursor_start(self, cursor: object, signature: str) -> int:
        if cursor is None:
            return 0
        payload = _cursor_decode(cursor)
        if payload.get("source") != self.source_sha256:
            raise ValueError(
                "STALE_CURSOR: cursor belongs to another native evidence source"
            )
        if payload.get("query") != signature:
            raise ValueError(
                "CURSOR_QUERY_MISMATCH: cursor belongs to another native query"
            )
        try:
            offset = int(payload.get("offset"))
        except (TypeError, ValueError) as exc:
            raise ValueError("INVALID_CURSOR: cursor offset is invalid") from exc
        if offset < 0:
            raise ValueError("INVALID_CURSOR: cursor offset is negative")
        return offset

    def _response_shell(
        self,
        *,
        operation: str,
        request: Mapping[str, object],
        query_details: Mapping[str, object],
        requested_budget: int,
        effective_budget: int,
    ) -> dict[str, object]:
        visible_query = {
            key: value
            for key, value in request.items()
            if key not in {"cursor", "includeDecompile"}
        }
        visible_query.update(query_details)
        visible_query["operation"] = operation
        return {
            "schema": NATIVE_QUERY_SCHEMA,
            "query": visible_query,
            "requestedBudget": requested_budget,
            "effectiveBudget": effective_budget,
            "estimatedTokens": 0,
            "returnedCount": 0,
            "omittedCount": 0,
            "cursor": None,
            "nextQuery": None,
            "sourceFingerprint": self.source_sha256,
            "evidenceSetId": self.evidence_set_id,
            "coverage": {
                "available": 0,
                "returned": 0,
                "availableNotReturned": 0,
                "byStatus": {},
            },
            "items": [],
            "gaps": [],
        }

    @staticmethod
    def _estimate(response: dict[str, object]) -> int:
        estimate = 0
        for _attempt in range(5):
            response["estimatedTokens"] = estimate
            updated = estimate_tokens(_compact_json(response))
            if updated == estimate:
                break
            estimate = updated
        response["estimatedTokens"] = estimate_tokens(_compact_json(response))
        return int(response["estimatedTokens"])

    def _bounded_response(
        self,
        *,
        operation: str,
        request: Mapping[str, object],
        query_details: Mapping[str, object],
        candidates: Sequence[dict[str, object]],
        gaps_mode: bool,
        start: int,
        page_size: int,
        requested_budget: int,
        effective_budget: int,
        signature: str,
    ) -> dict[str, object]:
        response = self._response_shell(
            operation=operation,
            request=request,
            query_details=query_details,
            requested_budget=requested_budget,
            effective_budget=effective_budget,
        )
        target_key = "gaps" if gaps_mode else "items"
        accepted = response[target_key]
        assert isinstance(accepted, list)
        page = list(candidates[start : start + page_size])
        for candidate in page:
            accepted.append(candidate)
            self._update_coverage(
                response,
                candidates,
                start=start,
                returned=len(accepted),
            )
            if self._estimate(response) > effective_budget:
                accepted.pop()
                break
        self._update_coverage(
            response,
            candidates,
            start=start,
            returned=len(accepted),
        )
        next_offset = start + len(accepted)
        if next_offset < len(candidates):
            cursor = _cursor_encode(
                {
                    "v": 1,
                    "source": self.source_sha256,
                    "query": signature,
                    "offset": next_offset,
                }
            )
            response["cursor"] = cursor
            response["nextQuery"] = {
                **{
                    key: value
                    for key, value in request.items()
                    if key != "cursor"
                },
                "operation": operation,
                "cursor": cursor,
            }
        used = self._estimate(response)
        if used > effective_budget:
            raise ValueError(
                f"budgetTokens={requested_budget} cannot hold the minimum "
                f"{operation} response; retry with a larger budget"
            )
        if candidates and not accepted:
            raise ValueError(
                f"budgetTokens={requested_budget} cannot hold one bounded "
                f"{operation} item"
            )
        return response

    @staticmethod
    def _update_coverage(
        response: dict[str, object],
        candidates: Sequence[dict[str, object]],
        *,
        start: int,
        returned: int,
    ) -> None:
        omitted = max(0, len(candidates) - start - returned)
        status_counts: dict[str, int] = {}
        for item in candidates:
            status = _status(item.get("status"))
            status_counts[status] = status_counts.get(status, 0) + 1
        status_counts["AVAILABLE_NOT_RETURNED"] = omitted
        response["returnedCount"] = returned
        response["omittedCount"] = omitted
        response["coverage"] = {
            "available": len(candidates),
            "returned": returned,
            "availableNotReturned": omitted,
            "byStatus": status_counts,
        }

    def _overview(
        self, _request: Mapping[str, object]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        tables = (
            "native_functions",
            "native_call_edges",
            "native_field_accesses",
            "native_constants",
            "native_branches",
            "native_vtable_slots",
            "native_gaps",
            "native_recipe_targets",
            "native_blueprint_links",
        )
        counts = {
            table.removeprefix("native_"): int(
                self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in tables
        }
        row = self._connection.execute(
            "SELECT sets.provenance_status, sets.recipe_id, sets.recipe_sha256, "
            "sets.pdb_sha256, sets.pdb_guid, sets.pdb_age, sets.pdb_matched, "
            "binaries.binary_sha256, binaries.module "
            "FROM native_evidence_sets AS sets "
            "JOIN native_binaries AS binaries ON binaries.evidence_set_id = sets.evidence_set_id "
            "LIMIT 1"
        ).fetchone()
        assert row is not None
        item = {
            "kind": "overview",
            "status": str(row["provenance_status"]),
            "counts": counts,
            "binary": {
                "sha256": str(row["binary_sha256"]),
                "module": str(row["module"]),
            },
            "pdb": {
                "sha256": str(row["pdb_sha256"]),
                "guid": str(row["pdb_guid"]),
                "age": int(row["pdb_age"]),
                "matched": bool(row["pdb_matched"]),
            },
            "recipe": {
                "id": str(row["recipe_id"]),
                "sha256": str(row["recipe_sha256"]),
            },
        }
        return [item], [], {}

    def _search(
        self, request: Mapping[str, object]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        query = str(request.get("query") or "").strip()
        if not query or not query.strip("*"):
            raise ValueError("search query must contain a specific term")
        like = f"%{query.casefold()}%"
        rows = self._connection.execute(
            "SELECT * FROM native_functions "
            "WHERE lower(name) LIKE ? OR lower(qualified_name) LIKE ? "
            "OR lower(signature) LIKE ? OR lower(owner) LIKE ? "
            "ORDER BY qualified_name, evidence_id",
            (like, like, like, like),
        ).fetchall()
        return [self._function_summary(row) for row in rows], [], {"text": query}

    def _function(
        self, request: Mapping[str, object]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        evidence_id = str(request.get("id") or "").strip()
        if not evidence_id:
            raise ValueError("function id is required")
        row = self._connection.execute(
            "SELECT * FROM native_functions WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            return [], [], {"id": evidence_id}
        include_decompile = request.get("includeDecompile") is True
        try:
            snippet_chars = min(
                max(int(request.get("snippetChars", 600)), 1),
                2000,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("snippetChars must be an integer") from exc
        return [
            self._function_detail(
                row,
                include_decompile=include_decompile,
                snippet_chars=snippet_chars,
            )
        ], [], {"id": evidence_id}

    def _relations(
        self,
        request: Mapping[str, object],
        *,
        direction: str,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        evidence_id = str(request.get("id") or "").strip()
        if not evidence_id:
            raise ValueError(f"{direction} function id is required")
        try:
            depth = int(request.get("depth", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("depth must be an integer") from exc
        if depth < 1 or depth > MAX_CALL_DEPTH:
            raise ValueError(f"depth must be between 1 and {MAX_CALL_DEPTH}")
        seen = {evidence_id}
        queue: deque[tuple[str, int]] = deque([(evidence_id, 0)])
        results: list[dict[str, object]] = []
        while queue:
            current, hops = queue.popleft()
            if hops >= depth:
                continue
            if direction == "callers":
                rows = self._connection.execute(
                    "SELECT functions.*, edges.call_edge_id, edges.caller_evidence_id, "
                    "edges.callee_evidence_id, edges.status AS edge_status, "
                    "edges.confidence AS edge_confidence "
                    "FROM native_call_edges AS edges "
                    "JOIN native_functions AS functions "
                    "ON functions.evidence_id = edges.caller_evidence_id "
                    "WHERE edges.callee_evidence_id = ? "
                    "ORDER BY functions.qualified_name",
                    (current,),
                ).fetchall()
                next_key = "caller_evidence_id"
            else:
                rows = self._connection.execute(
                    "SELECT functions.*, edges.call_edge_id, edges.caller_evidence_id, "
                    "edges.callee_evidence_id, edges.status AS edge_status, "
                    "edges.confidence AS edge_confidence "
                    "FROM native_call_edges AS edges "
                    "JOIN native_functions AS functions "
                    "ON functions.evidence_id = edges.callee_evidence_id "
                    "WHERE edges.caller_evidence_id = ? "
                    "ORDER BY functions.qualified_name",
                    (current,),
                ).fetchall()
                next_key = "callee_evidence_id"
            for row in rows:
                related_id = str(row[next_key])
                if related_id in seen:
                    continue
                seen.add(related_id)
                item = self._function_summary(row)
                item["relation"] = {
                    "direction": direction,
                    "from": current,
                    "callEdgeId": str(row["call_edge_id"]),
                    "status": str(row["edge_status"]),
                    "confidence": str(row["edge_confidence"]),
                    "depth": hops + 1,
                }
                results.append(item)
                queue.append((related_id, hops + 1))
        return results, [], {"id": evidence_id, "depth": depth}

    def _field_accesses(
        self, request: Mapping[str, object]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        query = str(request.get("query") or "").strip()
        function_id = str(request.get("id") or "").strip()
        clauses: list[str] = []
        parameters: list[object] = []
        if query:
            like = f"%{query.casefold()}%"
            clauses.append(
                "(lower(accesses.field_name) LIKE ? OR lower(accesses.owner_type) LIKE ? "
                "OR lower(accesses.access_kind) LIKE ?)"
            )
            parameters.extend((like, like, like))
        if function_id:
            clauses.append("accesses.function_evidence_id = ?")
            parameters.append(function_id)
        sql = (
            "SELECT accesses.*, functions.name, functions.qualified_name "
            "FROM native_field_accesses AS accesses "
            "JOIN native_functions AS functions "
            "ON functions.evidence_id = accesses.function_evidence_id"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY accesses.field_name, accesses.field_access_id"
        rows = self._connection.execute(sql, tuple(parameters)).fetchall()
        items = [
            {
                "kind": "field-access",
                "fieldAccessId": str(row["field_access_id"]),
                "functionEvidenceId": str(row["function_evidence_id"]),
                "function": str(row["qualified_name"]),
                "ownerType": str(row["owner_type"]),
                "fieldName": str(row["field_name"]),
                "offset": str(row["field_offset"]),
                "access": str(row["access_kind"]),
                "status": str(row["status"]),
                "confidence": str(row["confidence"]),
            }
            for row in rows
        ]
        return items, [], {"text": query, "id": function_id}

    def _constants(
        self, request: Mapping[str, object]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        query = str(request.get("query") or "").strip()
        function_id = str(request.get("id") or "").strip()
        clauses: list[str] = []
        parameters: list[object] = []
        if query:
            like = f"%{query.casefold()}%"
            clauses.append(
                "(lower(constants.context) LIKE ? OR lower(constants.value_json) LIKE ? "
                "OR lower(functions.qualified_name) LIKE ?)"
            )
            parameters.extend((like, like, like))
        if function_id:
            clauses.append("constants.function_evidence_id = ?")
            parameters.append(function_id)
        sql = (
            "SELECT constants.*, functions.qualified_name "
            "FROM native_constants AS constants "
            "JOIN native_functions AS functions "
            "ON functions.evidence_id = constants.function_evidence_id"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY constants.context, constants.constant_id"
        rows = self._connection.execute(sql, tuple(parameters)).fetchall()
        items = [
            {
                "kind": "constant",
                "constantId": str(row["constant_id"]),
                "functionEvidenceId": str(row["function_evidence_id"]),
                "function": str(row["qualified_name"]),
                "value": json.loads(str(row["value_json"])),
                "valueType": str(row["value_type"]),
                "context": str(row["context"]),
                "status": str(row["status"]),
                "confidence": str(row["confidence"]),
            }
            for row in rows
        ]
        return items, [], {"text": query, "id": function_id}

    def _gaps(
        self, request: Mapping[str, object]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        function_id = str(request.get("id") or "").strip()
        reason = str(request.get("reasonCode") or "").strip()
        clauses: list[str] = []
        parameters: list[object] = []
        if function_id:
            clauses.append("function_evidence_id = ?")
            parameters.append(function_id)
        if reason:
            clauses.append("reason_code = ?")
            parameters.append(reason)
        sql = "SELECT * FROM native_gaps"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY status, reason_code, gap_id"
        rows = self._connection.execute(sql, tuple(parameters)).fetchall()
        gaps = [
            {
                "kind": "gap",
                "gapId": str(row["gap_id"]),
                "functionEvidenceId": str(row["function_evidence_id"] or ""),
                "status": str(row["status"]),
                "reasonCode": str(row["reason_code"]),
                "detail": str(row["detail"]),
                "nextProbe": str(row["next_probe"]),
            }
            for row in rows
        ]
        return [], gaps, {"id": function_id, "reasonCode": reason}

    def _blueprint_links(
        self, request: Mapping[str, object]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
        native_id = str(request.get("id") or "").strip()
        source_id = str(request.get("sourceId") or "").strip()
        clauses: list[str] = []
        parameters: list[object] = []
        if native_id:
            clauses.append("target_id = ?")
            parameters.append(native_id)
        if source_id:
            clauses.append("source_id = ?")
            parameters.append(source_id)
        sql = "SELECT * FROM native_blueprint_links"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY source_id, target_id, edge_id"
        rows = self._connection.execute(sql, tuple(parameters)).fetchall()
        items = [
            {
                "kind": "blueprint-link",
                "edgeId": str(row["edge_id"]),
                "sourceId": str(row["source_id"]),
                "relation": str(row["relation"]),
                "targetId": str(row["target_id"]),
                "status": str(row["status"]),
            }
            for row in rows
        ]
        return items, [], {"id": native_id, "sourceId": source_id}

    @staticmethod
    def _function_summary(row: sqlite3.Row) -> dict[str, object]:
        return {
            "kind": "function",
            "evidenceId": str(row["evidence_id"]),
            "name": str(row["name"]),
            "qualifiedName": str(row["qualified_name"]),
            "owner": str(row["owner"]),
            "rva": str(row["rva"]),
            "signature": str(row["signature"]),
            "status": str(row["status"]),
            "confidence": str(row["confidence"]),
            "source": str(row["source"]),
        }

    def _function_detail(
        self,
        row: sqlite3.Row,
        *,
        include_decompile: bool,
        snippet_chars: int,
    ) -> dict[str, object]:
        item = self._function_summary(row)
        function_id = str(row["evidence_id"])
        item["parameters"] = [
            {
                "ordinal": int(value["ordinal"]),
                "name": str(value["name"]),
                "type": str(value["type_name"]),
            }
            for value in self._connection.execute(
                "SELECT * FROM native_parameters WHERE function_evidence_id = ? "
                "ORDER BY ordinal",
                (function_id,),
            )
        ]
        item["callCounts"] = {
            "callers": int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM native_call_edges WHERE callee_evidence_id = ?",
                    (function_id,),
                ).fetchone()[0]
            ),
            "callees": int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM native_call_edges WHERE caller_evidence_id = ?",
                    (function_id,),
                ).fetchone()[0]
            ),
        }
        item["fieldAccessCount"] = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM native_field_accesses WHERE function_evidence_id = ?",
                (function_id,),
            ).fetchone()[0]
        )
        item["constantCount"] = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM native_constants WHERE function_evidence_id = ?",
                (function_id,),
            ).fetchone()[0]
        )
        item["branchCount"] = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM native_branches WHERE function_evidence_id = ?",
                (function_id,),
            ).fetchone()[0]
        )
        if include_decompile:
            full = str(row["decompiled_c"])
            item["decompileSnippet"] = full[:snippet_chars]
            item["decompileAvailableChars"] = len(full)
            item["decompileReturnedChars"] = min(len(full), snippet_chars)
            item["decompileTruncated"] = len(full) > snippet_chars
        return item
