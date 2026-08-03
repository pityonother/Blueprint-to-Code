"""Bounded, revision-aware queries over a Blueprint evidence database.

The service deliberately exposes dictionaries rather than SQLite rows.  CLI and
HTTP adapters can therefore share this boundary without knowing the storage
schema, while every response remains small enough to be used as AI context.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import zlib
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .context_pack import estimate_tokens
from .bound_database import materialize_bound_database_snapshot
from .evidence_values import default_parse_gap, project_default_value


DEFAULT_BUDGET_TOKENS = 1000
MIN_BUDGET_TOKENS = 500
HARD_MAX_BUDGET_TOKENS = 8000
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_TRAVERSAL_HOPS = 3

_GAP_STATUSES = {
    "NOT_RECOVERED",
    "SOURCE_NOT_AVAILABLE",
    "AMBIGUOUS",
    "HEURISTIC",
}

_ALL_COVERAGE_STATUSES = (
    "CONFIRMED",
    "HEURISTIC",
    "NOT_RECOVERED",
    "SOURCE_NOT_AVAILABLE",
    "AMBIGUOUS",
    "AVAILABLE_NOT_RETURNED",
    "STALE_REVISION",
)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _first_text(*values: object) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _json_value(value: object, fallback: object) -> object:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _stored_json_value(
    row: sqlite3.Row,
    *,
    json_column: str = "value_json",
    codec_column: str = "value_codec",
    blob_column: str = "value_blob",
    fallback: object = None,
) -> object:
    keys = set(row.keys())
    codec = str(row[codec_column]) if codec_column in keys else "json"
    if codec == "json":
        return _json_value(row[json_column], fallback)
    if codec == "zlib-json-utf8":
        blob = row[blob_column] if blob_column in keys else None
        try:
            return json.loads(zlib.decompress(bytes(blob)).decode("utf-8"))
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError, zlib.error) as exc:
            raise ValueError(f"compressed evidence value is corrupt: {json_column}") from exc
    raise ValueError(f"unsupported evidence value codec: {codec}")


def _short_text(value: object, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _status(value: object, default: str = "CONFIRMED") -> str:
    normalized = str(value or "").strip().replace("-", "_").replace(" ", "_").upper()
    aliases = {
        "COMPLETE": "CONFIRMED",
        "RESOLVED": "CONFIRMED",
        "RESOLVED_PIN": "CONFIRMED",
        "RESOLVED_PIN_HEURISTIC": "HEURISTIC",
        "PARTIAL": "NOT_RECOVERED",
        "FAILED": "NOT_RECOVERED",
        "UNRESOLVED": "NOT_RECOVERED",
        "CROSS_GRAPH_OR_MISSING_NODE": "NOT_RECOVERED",
        "MISSING_TARGET_PIN_ID": "NOT_RECOVERED",
        "AMBIGUOUS_TARGET_NODE": "AMBIGUOUS",
        "AMBIGUOUS_TARGET_PIN": "AMBIGUOUS",
    }
    if normalized in aliases:
        return aliases[normalized]
    if "HEURISTIC" in normalized:
        return "HEURISTIC"
    if "AMBIGUOUS" in normalized:
        return "AMBIGUOUS"
    if "SOURCE_NOT_AVAILABLE" in normalized:
        return "SOURCE_NOT_AVAILABLE"
    if any(marker in normalized for marker in ("MISSING", "UNRESOLVED", "NOT_RECOVERED", "CROSS_GRAPH")):
        return "NOT_RECOVERED"
    return normalized or default


def _cursor_encode(payload: Mapping[str, object]) -> str:
    raw = _compact_json(dict(payload)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_decode(cursor: object) -> dict[str, object]:
    value = str(cursor or "").strip()
    if not value:
        raise ValueError("INVALID_CURSOR: cursor must not be empty")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("INVALID_CURSOR: malformed search cursor") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("INVALID_CURSOR: unsupported search cursor")
    return payload


def _query_hash(operation: str, **parts: object) -> str:
    payload = {"operation": operation, **parts}
    return hashlib.sha256(_compact_json(payload).encode("utf-8")).hexdigest()[:20]


class EvidenceQueryService:
    """Read-only service for one immutable evidence revision."""

    def __init__(
        self,
        database_path: Path,
        connection: sqlite3.Connection,
    ) -> None:
        self.database_path = Path(database_path)
        self._connection = connection
        self._closed = False
        row = connection.execute(
            "SELECT asset_id, asset_name, object_path, revision_id, source_fingerprint "
            "FROM asset_revisions ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            connection.close()
            self._closed = True
            raise ValueError("evidence database has no asset revision")
        self._asset_row = row
        self.asset_id = str(row["asset_id"])
        self.revision_id = str(row["revision_id"])

    @classmethod
    def open(
        cls,
        database_path: str | Path,
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
    ) -> "EvidenceQueryService":
        path = Path(os.path.abspath(os.path.expanduser(os.fspath(database_path))))
        snapshot = materialize_bound_database_snapshot(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = snapshot.open_connection()
            snapshot.close()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA query_only = ON")
            if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
                raise ValueError("evidence query connection did not enable foreign keys")
            if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise ValueError("evidence query connection did not become query-only")
            return cls(path, connection)
        except Exception:
            if connection is not None:
                connection.close()
            snapshot.close()
            raise

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "EvidenceQueryService":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def query(self, request: Mapping[str, object]) -> dict[str, object]:
        if self._closed:
            raise RuntimeError("EvidenceQueryService is closed")
        if not isinstance(request, Mapping):
            raise TypeError("request must be a mapping")
        operation = str(request.get("operation") or "").strip().casefold()
        handlers = {
            "overview": self._overview,
            "search": self._search,
            "entity": self._entity,
            "neighborhood": self._neighborhood,
            "trace": self._trace,
            "gaps": self._gaps,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise ValueError(f"unsupported evidence query operation: {operation or '<empty>'}")
        requested_budget, effective_budget = self._budget(request)
        response = handler(request, effective_budget)
        budget_block = response.get("budget")
        if not isinstance(budget_block, dict):
            raise ValueError("evidence response is missing its budget contract")
        budget_block["requested"] = requested_budget
        budget_block["effective"] = effective_budget
        used = self._update_estimate(response)
        if used > effective_budget:
            raise ValueError(
                f"budgetTokens={requested_budget} cannot hold the minimum {operation} response; "
                f"retry with at least {used} tokens"
            )
        return response

    def _budget(self, request: Mapping[str, object]) -> tuple[int, int]:
        raw = request.get("budgetTokens", DEFAULT_BUDGET_TOKENS)
        try:
            requested = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("budgetTokens must be an integer") from exc
        if requested < MIN_BUDGET_TOKENS:
            raise ValueError(f"budgetTokens must be at least {MIN_BUDGET_TOKENS}")
        return requested, min(requested, HARD_MAX_BUDGET_TOKENS)

    def _asset(self) -> dict[str, object]:
        return {
            "id": self.asset_id,
            "name": str(self._asset_row["asset_name"]),
            "objectPath": str(self._asset_row["object_path"]),
            "revisionId": self.revision_id,
        }

    def _base_response(self, operation: str, budget: int) -> dict[str, object]:
        return {
            "operation": operation,
            "asset": self._asset(),
            "items": [],
            "coverage": {
                "requested": 0,
                "returned": 0,
                "availableNotReturned": 0,
                "notRecovered": 0,
                "byStatus": {status: 0 for status in _ALL_COVERAGE_STATUSES},
            },
            "omissions": [],
            "nextQueries": [],
            "page": {"nextCursor": None},
            "budget": {"requested": budget, "effective": budget, "estimatedUsed": 0},
        }

    @staticmethod
    def _estimated_tokens(response: Mapping[str, object]) -> int:
        return estimate_tokens(_compact_json(response))

    def _update_estimate(self, response: dict[str, object]) -> int:
        budget = response["budget"]
        assert isinstance(budget, dict)
        estimate = 0
        for _attempt in range(5):
            budget["estimatedUsed"] = estimate
            updated = self._estimated_tokens(response)
            if updated == estimate:
                break
            estimate = updated
        budget["estimatedUsed"] = self._estimated_tokens(response)
        return int(budget["estimatedUsed"])

    def _fits(self, response: dict[str, object], budget: int) -> bool:
        return self._update_estimate(response) <= budget

    def _set_coverage(
        self,
        response: dict[str, object],
        *,
        requested: int,
        returned: int,
        not_recovered: int = 0,
        status_counts: Mapping[str, int] | None = None,
    ) -> None:
        coverage = response["coverage"]
        assert isinstance(coverage, dict)
        omitted = max(0, requested - returned)
        by_status = {status: 0 for status in _ALL_COVERAGE_STATUSES}
        for key, value in (status_counts or {}).items():
            normalized = _status(key)
            by_status[normalized] = by_status.get(normalized, 0) + int(value)
        by_status["AVAILABLE_NOT_RETURNED"] = omitted
        by_status["NOT_RECOVERED"] = max(by_status.get("NOT_RECOVERED", 0), not_recovered)
        coverage.update(
            {
                "requested": requested,
                "returned": returned,
                "availableNotReturned": omitted,
                "notRecovered": max(not_recovered, by_status.get("NOT_RECOVERED", 0)),
                "byStatus": by_status,
            }
        )
        omissions: list[dict[str, object]] = []
        if omitted:
            omissions.append({"reason": "AVAILABLE_NOT_RETURNED", "count": omitted})
        response["omissions"] = omissions

    def _bounded_items(
        self,
        response: dict[str, object],
        candidates: Sequence[dict[str, object]],
        budget: int,
        *,
        requested: int | None = None,
        not_recovered: int = 0,
        status_counts: Mapping[str, int] | None = None,
        cursor_factory: Any = None,
    ) -> int:
        total = len(candidates) if requested is None else int(requested)
        accepted: list[dict[str, object]] = []
        response["items"] = accepted
        self._set_coverage(
            response,
            requested=total,
            returned=0,
            not_recovered=not_recovered,
            status_counts=status_counts,
        )
        for item in candidates:
            accepted.append(item)
            self._set_coverage(
                response,
                requested=total,
                returned=len(accepted),
                not_recovered=not_recovered,
                status_counts=status_counts,
            )
            if cursor_factory is not None:
                response["page"] = {"nextCursor": cursor_factory(len(accepted))}
            if not self._fits(response, budget):
                accepted.pop()
                self._set_coverage(
                    response,
                    requested=total,
                    returned=len(accepted),
                    not_recovered=not_recovered,
                    status_counts=status_counts,
                )
                if cursor_factory is not None:
                    response["page"] = {"nextCursor": cursor_factory(len(accepted))}
                break
        self._update_estimate(response)
        return len(accepted)

    def _overview(self, _request: Mapping[str, object], budget: int) -> dict[str, object]:
        response = self._base_response("overview", budget)
        counts = self._connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM graphs) AS graph_count, "
            "(SELECT COUNT(*) FROM nodes) AS node_count, "
            "(SELECT COUNT(*) FROM pins) AS pin_count, "
            "(SELECT COUNT(*) FROM edges) AS wire_count, "
            "(SELECT COUNT(*) FROM edge_observations) AS observation_count, "
            "(SELECT COUNT(*) FROM class_defaults) AS default_count, "
            "(SELECT COUNT(*) FROM diagnostics) + "
            "(SELECT COUNT(*) FROM edge_observations "
            " WHERE lower(COALESCE(NULLIF(resolution_status, ''), status, '')) <> 'resolved_pin') AS gap_count"
        ).fetchone()
        assert counts is not None
        status_counts = self._diagnostic_status_counts()
        gap_count = sum(count for status, count in status_counts.items() if status in _GAP_STATUSES)
        response["summary"] = {
            "graphCount": int(counts["graph_count"]),
            "nodeCount": int(counts["node_count"]),
            "pinCount": int(counts["pin_count"]),
            "wireCount": int(counts["wire_count"]),
            "linkObservationCount": int(counts["observation_count"]),
            "defaultCount": int(counts["default_count"]),
            "gapCount": gap_count,
        }
        self._set_coverage(
            response,
            requested=int(counts["graph_count"]),
            returned=0,
            not_recovered=status_counts.get("NOT_RECOVERED", 0),
            status_counts=status_counts,
        )
        response["nextQueries"] = [
            {"operation": "search", "query": "<name>", "budgetTokens": min(800, budget)},
            {"operation": "gaps", "budgetTokens": min(1000, budget)},
        ]
        # Overview intentionally returns counts rather than pretending the graphs
        # themselves were returned.  Its omissions describe those available rows.
        self._update_estimate(response)
        if not self._fits(response, budget):
            response["nextQueries"] = []
            self._update_estimate(response)
        return response

    def _search(self, request: Mapping[str, object], budget: int) -> dict[str, object]:
        query = str(request.get("query") or "").strip()
        if not query:
            raise ValueError("search query is required")
        if not query.strip("*"):
            raise ValueError(
                "pure wildcard search is disabled; use overview for library counts "
                "or provide a specific search term"
            )
        kinds_value = request.get("kinds")
        if kinds_value is None:
            kinds = ("graph", "node", "pin", "default", "diagnostic")
        elif isinstance(kinds_value, Sequence) and not isinstance(kinds_value, (str, bytes)):
            kinds = tuple(dict.fromkeys(str(value).strip().casefold() for value in kinds_value if str(value).strip()))
        else:
            raise ValueError("kinds must be an array")
        allowed = {"graph", "node", "pin", "default", "diagnostic", "edge_observation"}
        if not kinds or any(kind not in allowed for kind in kinds):
            raise ValueError("kinds contains an unsupported entity kind")
        try:
            page_size = int(request.get("pageSize", DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError) as exc:
            raise ValueError("pageSize must be an integer") from exc
        if page_size <= 0:
            raise ValueError("pageSize must be positive")
        page_size = min(page_size, MAX_PAGE_SIZE)

        signature = _query_hash("search", query=query.casefold(), kinds=sorted(kinds))
        last_ref = ""
        cursor = request.get("cursor")
        if cursor is not None:
            payload = _cursor_decode(cursor)
            if str(payload.get("revision")) != self.revision_id:
                raise ValueError("STALE_CURSOR: cursor belongs to another asset revision")
            if str(payload.get("query")) != signature:
                raise ValueError("CURSOR_QUERY_MISMATCH: cursor belongs to another search")
            last_ref = str(payload.get("lastRef") or "")

        all_items = self._search_rows(query, kinds)
        start = 0
        if last_ref:
            positions = [index for index, item in enumerate(all_items) if item["ref"] == last_ref]
            if not positions:
                raise ValueError("INVALID_CURSOR: last search reference no longer exists")
            start = positions[0] + 1
        page_candidates = all_items[start : start + page_size]
        response = self._base_response("search", budget)

        def next_cursor(returned: int) -> str | None:
            position = start + returned
            if returned <= 0 or position >= len(all_items):
                return None
            return _cursor_encode(
                {"v": 1, "revision": self.revision_id, "query": signature, "lastRef": all_items[position - 1]["ref"]}
            )

        response["query"] = query
        self._bounded_items(
            response,
            page_candidates,
            budget,
            requested=len(all_items),
            cursor_factory=next_cursor,
        )
        return response

    def _search_rows(self, query: str, kinds: Sequence[str]) -> list[dict[str, object]]:
        term = query.casefold()
        list_all = term == "*"
        materialized = self._materialized_search_kinds(kinds)
        indexed_kinds = tuple(kind for kind in kinds if kind in materialized)
        fallback_kinds = tuple(kind for kind in kinds if kind not in materialized)
        indexed_rows: Sequence[sqlite3.Row] = ()
        if indexed_kinds:
            placeholders = ",".join("?" for _ in indexed_kinds)
            if list_all:
                indexed_rows = self._connection.execute(
                    "SELECT ref, kind, name, graph_ref, summary, search_text "
                    f"FROM search_entities WHERE revision_id = ? AND kind IN ({placeholders})",
                    (self.revision_id, *indexed_kinds),
                ).fetchall()
            else:
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                like = f"%{escaped}%"
                indexed_rows = self._connection.execute(
                    "SELECT ref, kind, name, graph_ref, summary, search_text "
                    f"FROM search_entities WHERE revision_id = ? AND kind IN ({placeholders}) "
                    # SQLite LIKE is already ASCII case-insensitive by default.
                    # Avoid lower() over every materialized row: the Lionfish
                    # index has about 50k compact entities, and that redundant
                    # transform dominated bounded-search latency.
                    "AND (name LIKE ? ESCAPE '\\' OR search_text LIKE ? ESCAPE '\\')",
                    (self.revision_id, *indexed_kinds, like, like),
                ).fetchall()
        fallback_rows = (
            self._fallback_search_rows(fallback_kinds, None if list_all else term)
            if fallback_kinds
            else []
        )
        rows_by_ref: dict[str, sqlite3.Row | Mapping[str, object]] = {
            str(row["ref"]): row for row in fallback_rows
        }
        rows_by_ref.update({str(row["ref"]): row for row in indexed_rows})
        rows = list(rows_by_ref.values())

        def rank(row: sqlite3.Row | Mapping[str, object]) -> tuple[int, str, str]:
            name = str(row["name"]).casefold()
            search_text = str(row["search_text"]).casefold()
            score = 0 if name == term else 1 if name.startswith(term) else 2 if term in name else 3 if term in search_text else 4
            return score, name, str(row["ref"])

        ordered = sorted(rows, key=rank)
        return [
            {
                "ref": str(row["ref"]),
                "kind": str(row["kind"]),
                "name": str(row["name"]),
                **({"graphRef": str(row["graph_ref"])} if str(row["graph_ref"] or "") else {}),
                **({"summary": _short_text(row["summary"], 160)} if str(row["summary"] or "") else {}),
            }
            for row in ordered
        ]

    def _materialized_search_kinds(self, kinds: Sequence[str]) -> set[str]:
        eligible = tuple(
            kind for kind in kinds if kind in {"graph", "node", "pin", "default"}
        )
        if not eligible:
            return set()
        placeholders = ",".join("?" for _ in eligible)
        try:
            rows = self._connection.execute(
                "SELECT materialization.kind FROM search_materialization AS materialization "
                "WHERE materialization.revision_id = ? "
                f"AND materialization.kind IN ({placeholders}) "
                "AND materialization.is_complete = 1 "
                "AND materialization.row_count = ("
                "SELECT COUNT(*) FROM search_entities AS entity "
                "WHERE entity.revision_id = materialization.revision_id "
                "AND entity.kind = materialization.kind)",
                (self.revision_id, *eligible),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).casefold():
                raise
            return set()
        return {str(row["kind"]) for row in rows}

    def _fallback_search_rows(
        self,
        kinds: Sequence[str],
        term: str | None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        like = None
        if term is not None:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
        if "graph" in kinds:
            sql = "SELECT graph_ref, name, graph_type, status FROM graphs"
            parameters: tuple[object, ...] = ()
            if like is not None:
                sql += " WHERE lower(name || ' ' || graph_type || ' ' || status) LIKE ? ESCAPE '\\'"
                parameters = (like,)
            for row in self._connection.execute(sql, parameters):
                rows.append(
                    {
                        "ref": row["graph_ref"],
                        "kind": "graph",
                        "name": row["name"],
                        "graph_ref": row["graph_ref"],
                        "summary": f"{row['graph_type']} {row['status']}".strip(),
                        "search_text": f"{row['name']} {row['graph_type']} {row['status']}",
                    }
                )
        if "node" in kinds:
            sql = (
                "SELECT node_ref, graph_ref, name, label, class_name, function_name, variable_name, event_name, comment "
                "FROM nodes"
            )
            parameters = ()
            if like is not None:
                sql += (
                    " WHERE lower(name || ' ' || label || ' ' || class_name || ' ' || function_name || ' ' || "
                    "variable_name || ' ' || event_name || ' ' || comment) LIKE ? ESCAPE '\\'"
                )
                parameters = (like,)
            for row in self._connection.execute(sql, parameters):
                search_text = " ".join(str(row[key] or "") for key in row.keys())
                rows.append(
                    {
                        "ref": row["node_ref"],
                        "kind": "node",
                        "name": row["name"],
                        "graph_ref": row["graph_ref"],
                        "summary": " ".join(filter(None, (str(row["class_name"]), str(row["function_name"]), str(row["variable_name"]), str(row["event_name"])))),
                        "search_text": search_text,
                    }
                )
        if "pin" in kinds:
            sql = (
                "SELECT p.pin_ref, n.graph_ref, p.name, p.native_pin_id, p.category, p.direction "
                "FROM pins p JOIN nodes n ON n.node_ref = p.node_ref"
            )
            parameters = ()
            if like is not None:
                sql += " WHERE lower(p.name || ' ' || p.native_pin_id || ' ' || p.category || ' ' || p.direction) LIKE ? ESCAPE '\\'"
                parameters = (like,)
            for row in self._connection.execute(sql, parameters):
                rows.append(
                    {
                        "ref": row["pin_ref"],
                        "kind": "pin",
                        "name": row["name"],
                        "graph_ref": row["graph_ref"],
                        "summary": f"{row['direction']} {row['category']}".strip(),
                        "search_text": f"{row['name']} {row['native_pin_id']} {row['category']} {row['direction']}",
                    }
                )
        if "default" in kinds:
            sql = "SELECT default_ref, name, type_name, value_json FROM class_defaults"
            parameters = ()
            if like is not None:
                sql += " WHERE lower(name || ' ' || type_name || ' ' || value_json) LIKE ? ESCAPE '\\'"
                parameters = (like,)
            for row in self._connection.execute(sql, parameters):
                rows.append(
                    {
                        "ref": row["default_ref"],
                        "kind": "default",
                        "name": row["name"],
                        "graph_ref": "",
                        "summary": f"{row['type_name']}={_short_text(row['value_json'], 80)}",
                        "search_text": f"{row['name']} {row['type_name']} {row['value_json']}",
                    }
                )
        if "diagnostic" in kinds:
            sql = "SELECT diagnostic_ref, scope_ref, reason_code, title, detail, status FROM diagnostics"
            parameters = ()
            if like is not None:
                sql += " WHERE lower(reason_code || ' ' || title || ' ' || detail || ' ' || status) LIKE ? ESCAPE '\\'"
                parameters = (like,)
            for row in self._connection.execute(sql, parameters):
                rows.append(
                    {
                        "ref": row["diagnostic_ref"],
                        "kind": "diagnostic",
                        "name": row["reason_code"] or row["title"],
                        "graph_ref": row["scope_ref"] if "/g/" in str(row["scope_ref"]) else "",
                        "summary": row["detail"],
                        "search_text": " ".join(str(row[key] or "") for key in row.keys()),
                    }
                )
        if "edge_observation" in kinds:
            sql = (
                "SELECT observation_ref, graph_ref, target_node_name, target_native_pin_id, target_pin_name, "
                "resolution_status, status, kind, source_pin_ref, target_pin_ref FROM edge_observations"
            )
            parameters = ()
            if like is not None:
                sql += (
                    " WHERE lower(target_node_name || ' ' || target_native_pin_id || ' ' || target_pin_name || ' ' || "
                    "resolution_status || ' ' || status || ' ' || kind || ' ' || source_pin_ref || ' ' || "
                    "COALESCE(target_pin_ref, '')) LIKE ? ESCAPE '\\'"
                )
                parameters = (like,)
            for row in self._connection.execute(sql, parameters):
                name = _first_text(
                    row["target_node_name"],
                    row["target_pin_name"],
                    row["target_native_pin_id"],
                    row["resolution_status"],
                    "Link observation",
                )
                rows.append(
                    {
                        "ref": row["observation_ref"],
                        "kind": "edge_observation",
                        "name": name,
                        "graph_ref": row["graph_ref"],
                        "summary": f"{row['kind']} {_status(row['resolution_status'] or row['status'])}".strip(),
                        "search_text": " ".join(str(row[key] or "") for key in row.keys()),
                    }
                )
        return rows

    def _entity(self, request: Mapping[str, object], budget: int) -> dict[str, object]:
        ref = self._selector_ref(request)
        self._validate_ref_revision(ref)
        is_node_ref = self._connection.execute("SELECT 1 FROM nodes WHERE node_ref = ?", (ref,)).fetchone() is not None
        node_summary_only = (
            is_node_ref
            and "propertyLimit" not in request
            and "observationLimit" not in request
            and "propertyOffset" not in request
            and "observationOffset" not in request
        )
        try:
            candidate_offset = int(request.get("candidateOffset", 0))
            candidate_limit = int(request.get("candidateLimit", 25))
            value_offset = int(request.get("valueOffset", 0))
            value_chars = int(request.get("valueChars", 600))
            property_offset = int(request.get("propertyOffset", 0))
            property_limit = int(request.get("propertyLimit", 0 if is_node_ref else 5))
            observation_offset = int(request.get("observationOffset", 0))
            observation_limit = int(request.get("observationLimit", 0 if is_node_ref else 10))
        except (TypeError, ValueError) as exc:
            raise ValueError("entity offsets and limits must be integers") from exc
        if min(
            candidate_offset,
            candidate_limit,
            value_offset,
            value_chars,
            property_offset,
            property_limit,
            observation_offset,
            observation_limit,
        ) < 0:
            raise ValueError("entity offsets and limits must be non-negative")
        candidate_limit = min(candidate_limit, 100)
        value_chars = min(value_chars, 4000)
        property_limit = min(property_limit, 100)
        observation_limit = min(observation_limit, 100)
        while True:
            item = self._entity_item(
                ref,
                candidate_offset=candidate_offset,
                candidate_limit=candidate_limit,
                value_offset=value_offset,
                value_chars=value_chars,
                property_offset=property_offset,
                property_limit=property_limit,
                observation_offset=observation_offset,
                observation_limit=observation_limit,
            )
            if item is None:
                raise KeyError(f"evidence entity not found: {ref}")
            if node_summary_only:
                return self._compact_node_summary_response(item, ref=ref, budget=budget)
            response = self._base_response("entity", budget)
            response["items"] = [item]
            item_status = item.get("status") or item.get("valueStatus") or "CONFIRMED"
            self._set_coverage(
                response,
                requested=1,
                returned=1,
                status_counts={_status(item_status): 1},
            )
            self._annotate_entity_pages(
                response,
                item,
                ref=ref,
                budget=budget,
                candidate_limit=candidate_limit,
                value_chars=value_chars,
                property_limit=property_limit,
                observation_limit=observation_limit,
            )
            if self._fits(response, budget):
                return response

            reductions = (
                ("candidateCoverage", "candidate_limit"),
                ("valueCoverage", "value_chars"),
                ("propertyCoverage", "property_limit"),
                ("observationCoverage", "observation_limit"),
            )
            reduced = False
            for coverage_key, local_name in reductions:
                if not isinstance(item.get(coverage_key), dict):
                    continue
                current = {
                    "candidate_limit": candidate_limit,
                    "value_chars": value_chars,
                    "property_limit": property_limit,
                    "observation_limit": observation_limit,
                }[local_name]
                smaller = current // 2 if current > 1 else 0
                if smaller == current:
                    continue
                if local_name == "candidate_limit":
                    candidate_limit = smaller
                elif local_name == "value_chars":
                    value_chars = smaller
                elif local_name == "property_limit":
                    property_limit = smaller
                else:
                    observation_limit = smaller
                reduced = True
                break
            if reduced:
                continue

            # Even the minimum exact entity header does not fit.  Do not advance
            # any nested offset: the caller must retry with a larger budget.
            response = self._base_response("entity", budget)
            self._set_coverage(response, requested=1, returned=0)
            response["nextQueries"] = [
                {
                    "operation": "entity",
                    "selector": {"ref": ref},
                    "candidateOffset": candidate_offset,
                    "valueOffset": value_offset,
                    "propertyOffset": property_offset,
                    "observationOffset": observation_offset,
                    "budgetTokens": min(HARD_MAX_BUDGET_TOKENS, max(1000, budget * 2)),
                }
            ]
            if not self._fits(response, budget):
                response["nextQueries"] = []
            self._update_estimate(response)
            return response

    def _compact_node_summary_response(
        self,
        source_item: Mapping[str, object],
        *,
        ref: str,
        budget: int,
    ) -> dict[str, object]:
        item = dict(source_item)
        property_coverage = item.pop("propertyCoverage", {})
        observation_coverage = item.pop("observationCoverage", {})
        item.pop("properties", None)
        item.pop("observations", None)
        property_count = (
            int(property_coverage.get("available") or 0)
            if isinstance(property_coverage, Mapping)
            else 0
        )
        observation_count = (
            int(observation_coverage.get("available") or 0)
            if isinstance(observation_coverage, Mapping)
            else 0
        )
        if property_count or observation_count:
            item["relatedCounts"] = {
                "properties": property_count,
                "linkObservations": observation_count,
            }
        response = self._base_response("entity", budget)
        asset = response["asset"]
        assert isinstance(asset, dict)
        asset.pop("objectPath", None)
        # The exact node ref already identifies the asset; the long display name
        # remains available from overview and is omitted to keep this default
        # node summary within the 600-token contract even for long ARK paths.
        asset.pop("name", None)
        response.pop("page", None)
        response["items"] = [item]
        omitted = property_count + observation_count
        response["coverage"] = {
            "requested": 1,
            "returned": 1,
            "availableNotReturned": omitted,
            "notRecovered": 0,
            "byStatus": {
                "CONFIRMED": 1,
                "AVAILABLE_NOT_RETURNED": omitted,
            },
        }
        response["omissions"] = (
            [{"reason": "AVAILABLE_NOT_RETURNED", "count": omitted, "kind": "related_evidence"}]
            if omitted
            else []
        )
        if property_count:
            response["nextQueries"] = [
                {
                    "operation": "entity",
                    "selector": {"ref": ref},
                    "propertyOffset": 0,
                    "propertyLimit": 5,
                    "observationOffset": 0,
                    "budgetTokens": min(HARD_MAX_BUDGET_TOKENS, max(1000, budget * 2)),
                }
            ]
        elif observation_count:
            response["nextQueries"] = [
                {
                    "operation": "entity",
                    "selector": {"ref": ref},
                    "observationOffset": 0,
                    "observationLimit": 10,
                    "budgetTokens": min(HARD_MAX_BUDGET_TOKENS, max(1000, budget * 2)),
                }
            ]
        self._update_estimate(response)
        if self._fits(response, budget):
            return response
        # Position and duplicated label are useful but not required to navigate
        # to properties/connections. Drop them before ever dropping the node.
        item.pop("position", None)
        if item.get("label") == item.get("name"):
            item.pop("label", None)
        self._update_estimate(response)
        if self._fits(response, budget):
            return response
        # Extremely small budgets still receive an honest omission without
        # advancing offsets; this path is normally below the 600-token contract.
        response["items"] = []
        response["coverage"] = {
            "requested": 1,
            "returned": 0,
            "availableNotReturned": 1,
            "notRecovered": 0,
            "byStatus": {"AVAILABLE_NOT_RETURNED": 1},
        }
        response["omissions"] = [{"reason": "AVAILABLE_NOT_RETURNED", "count": 1, "kind": "node"}]
        self._update_estimate(response)
        return response

    def _annotate_entity_pages(
        self,
        response: dict[str, object],
        item: Mapping[str, object],
        *,
        ref: str,
        budget: int,
        candidate_limit: int,
        value_chars: int,
        property_limit: int,
        observation_limit: int,
    ) -> None:
        page_specs = (
            ("candidateCoverage", "available", "returned", "offset", "edge_candidate", "candidateOffset", "candidateLimit", candidate_limit),
            ("valueCoverage", "availableChars", "returnedChars", "offset", "value_character", "valueOffset", "valueChars", value_chars),
            ("propertyCoverage", "available", "returned", "offset", "property", "propertyOffset", "propertyLimit", property_limit),
            ("observationCoverage", "available", "returned", "offset", "edge_observation", "observationOffset", "observationLimit", observation_limit),
        )
        omissions = response["omissions"]
        next_queries = response["nextQueries"]
        coverage = response["coverage"]
        assert isinstance(omissions, list) and isinstance(next_queries, list) and isinstance(coverage, dict)
        by_status = coverage["byStatus"]
        assert isinstance(by_status, dict)
        pending_queries: list[dict[str, object]] = []
        current_offsets: dict[str, int] = {}
        for coverage_key, _available_key, _returned_key, offset_key, _kind, offset_arg, _limit_arg, _page_limit in page_specs:
            nested = item.get(coverage_key)
            if isinstance(nested, Mapping):
                offset = int(nested.get(offset_key) or 0)
                if offset > 0:
                    current_offsets[offset_arg] = offset
        for coverage_key, available_key, returned_key, offset_key, kind, offset_arg, limit_arg, page_limit in page_specs:
            nested = item.get(coverage_key)
            if not isinstance(nested, Mapping):
                continue
            available = int(nested.get(available_key) or 0)
            returned = int(nested.get(returned_key) or 0)
            offset = int(nested.get(offset_key) or 0)
            omitted = max(available - offset - returned, 0)
            if omitted <= 0:
                continue
            omissions.append({"reason": "AVAILABLE_NOT_RETURNED", "count": omitted, "kind": kind})
            pending_queries.append(
                {
                    "operation": "entity",
                    "selector": {"ref": ref},
                    **current_offsets,
                    offset_arg: offset + returned,
                    limit_arg: max(1, page_limit),
                    "budgetTokens": (
                        min(HARD_MAX_BUDGET_TOKENS, max(1000, budget * 2)) if returned == 0 else budget
                    ),
                }
            )
            coverage["availableNotReturned"] = int(coverage.get("availableNotReturned") or 0) + omitted
            by_status["AVAILABLE_NOT_RETURNED"] = int(by_status.get("AVAILABLE_NOT_RETURNED") or 0) + omitted
        # One continuation at a time keeps a node summary within its 600-token
        # contract.  The selected query carries completed offsets, so finishing
        # one nested collection exposes the next without resetting earlier pages.
        next_queries.extend(pending_queries[:1])
        self._update_estimate(response)

    def _entity_item(
        self,
        ref: str,
        *,
        candidate_offset: int = 0,
        candidate_limit: int = 25,
        value_offset: int = 0,
        value_chars: int = 600,
        property_offset: int = 0,
        property_limit: int = 5,
        observation_offset: int = 0,
        observation_limit: int = 10,
    ) -> dict[str, object] | None:
        row = self._connection.execute("SELECT * FROM graphs WHERE graph_ref = ?", (ref,)).fetchone()
        if row is not None:
            return self._graph_item(row)
        row = self._connection.execute("SELECT * FROM nodes WHERE node_ref = ?", (ref,)).fetchone()
        if row is not None:
            return self._node_item(
                row,
                property_offset=property_offset,
                property_limit=property_limit,
                observation_offset=observation_offset,
                observation_limit=observation_limit,
            )
        row = self._connection.execute("SELECT * FROM pins WHERE pin_ref = ?", (ref,)).fetchone()
        if row is not None:
            return self._pin_item(row, observation_offset=observation_offset, observation_limit=observation_limit)
        row = self._connection.execute("SELECT * FROM class_defaults WHERE default_ref = ?", (ref,)).fetchone()
        if row is not None:
            return self._default_item(row, value_offset=value_offset, value_chars=value_chars)
        row = self._connection.execute("SELECT * FROM properties WHERE property_ref = ?", (ref,)).fetchone()
        if row is not None:
            return self._property_item(row, value_offset=value_offset, value_chars=value_chars)
        row = self._connection.execute("SELECT * FROM diagnostics WHERE diagnostic_ref = ?", (ref,)).fetchone()
        if row is not None:
            return self._diagnostic_item(row)
        row = self._connection.execute("SELECT * FROM edges WHERE edge_ref = ?", (ref,)).fetchone()
        if row is not None:
            item = {
                "ref": str(row["edge_ref"]),
                "kind": "edge",
                "graphRef": str(row["graph_ref"]),
                "sourcePinRef": str(row["source_pin_ref"]),
                "targetPinRef": str(row["target_pin_ref"]),
                "edgeKind": str(row["kind"]),
                "confidence": str(row["confidence"]),
                "status": _status(row["resolution_status"]),
            }
            observations, observation_total = self._observation_summaries(
                "(source_pin_ref = ? AND target_pin_ref = ?) OR (source_pin_ref = ? AND target_pin_ref = ?)",
                (row["source_pin_ref"], row["target_pin_ref"], row["target_pin_ref"], row["source_pin_ref"]),
                offset=observation_offset,
                limit=observation_limit,
            )
            item["observations"] = observations
            item["observationCoverage"] = {
                "available": observation_total,
                "returned": len(observations),
                "offset": observation_offset,
            }
            return item
        row = self._connection.execute(
            "SELECT * FROM edge_observations WHERE observation_ref = ?",
            (ref,),
        ).fetchone()
        if row is not None:
            return self._observation_item(
                row,
                candidate_offset=candidate_offset,
                candidate_limit=candidate_limit,
            )
        return None

    def _candidate_dictionary(self) -> list[str]:
        row = self._connection.execute(
            "SELECT codec, values_blob FROM candidate_dictionary WHERE dictionary_id = 1"
        ).fetchone()
        if row is None:
            return []
        if str(row["codec"]) != "zlib-json-utf8":
            raise ValueError(f"unsupported candidate dictionary codec: {row['codec']}")
        try:
            values = json.loads(zlib.decompress(bytes(row["values_blob"])).decode("utf-8"))
        except (ValueError, UnicodeError, json.JSONDecodeError, zlib.error) as exc:
            raise ValueError("candidate dictionary is corrupt") from exc
        return [str(value) for value in values] if isinstance(values, list) else []

    def _observation_item(
        self,
        row: sqlite3.Row,
        *,
        candidate_offset: int,
        candidate_limit: int,
    ) -> dict[str, object]:
        total = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM edge_candidates WHERE observation_id = ?",
                (row["observation_id"],),
            ).fetchone()[0]
        )
        candidate_rows = self._connection.execute(
            "SELECT candidate_ordinal, candidate_symbol_id, candidate_pin_ref FROM edge_candidates "
            "WHERE observation_id = ? ORDER BY candidate_ordinal LIMIT ? OFFSET ?",
            (row["observation_id"], candidate_limit, candidate_offset),
        ).fetchall()
        dictionary = self._candidate_dictionary() if candidate_rows else []
        candidates = []
        for candidate in candidate_rows:
            symbol_id = int(candidate["candidate_symbol_id"])
            native_id = dictionary[symbol_id - 1] if 0 < symbol_id <= len(dictionary) else ""
            candidates.append(
                {
                    "ordinal": int(candidate["candidate_ordinal"]),
                    "nativePinId": native_id,
                    **({"pinRef": str(candidate["candidate_pin_ref"])} if candidate["candidate_pin_ref"] else {}),
                }
            )
        return {
            "ref": str(row["observation_ref"]),
            "kind": "edge_observation",
            "graphRef": str(row["graph_ref"]),
            "sourceNodeRef": str(row["source_node_ref"] or ""),
            "sourcePinRef": str(row["source_pin_ref"] or ""),
            "targetNodeRef": str(row["target_node_ref"] or ""),
            "targetPinRef": str(row["target_pin_ref"] or ""),
            "targetNodeName": str(row["target_node_name"] or ""),
            "targetNativePinId": str(row["target_native_pin_id"] or ""),
            "targetPinName": str(row["target_pin_name"] or ""),
            "edgeKind": str(row["kind"]),
            "status": _status(row["resolution_status"] or row["status"]),
            "confidence": str(row["confidence"] or ""),
            "source": str(row["source"] or ""),
            "rawEvidence": _json_value(row["raw_json"], {}),
            "candidates": candidates,
            "candidateCoverage": {
                "available": total,
                "returned": len(candidates),
                "offset": candidate_offset,
            },
        }

    @staticmethod
    def _graph_item(row: sqlite3.Row) -> dict[str, object]:
        return {
            "ref": str(row["graph_ref"]),
            "kind": "graph",
            "name": str(row["name"]),
            "graphType": str(row["graph_type"]),
            "status": _status(row["status"]),
            "confidence": str(row["confidence"]),
            "counts": {
                "nodes": int(row["node_count"]),
                "pins": int(row["pin_count"]),
                "linkObservations": int(row["link_observation_count"]),
            },
        }

    def _node_item(
        self,
        row: sqlite3.Row,
        *,
        property_offset: int,
        property_limit: int,
        observation_offset: int,
        observation_limit: int,
    ) -> dict[str, object]:
        item: dict[str, object] = {
            "ref": str(row["node_ref"]),
            "kind": "node",
            "graphRef": str(row["graph_ref"]),
            "name": str(row["name"]),
            "label": str(row["label"]),
            "className": str(row["class_name"]),
        }
        if str(row["node_type"] or "") and str(row["node_type"]) != str(row["class_name"]):
            item["nodeType"] = str(row["node_type"])
        if str(row["control_kind"] or "").casefold() not in {"", "call"}:
            item["controlKind"] = str(row["control_kind"])
        if str(row["confidence"] or "").casefold() not in {"", "high"}:
            item["confidence"] = str(row["confidence"])
        signals = {
            key: str(row[column])
            for key, column in (
                ("function", "function_name"),
                ("variable", "variable_name"),
                ("event", "event_name"),
                ("delegate", "delegate_name"),
                ("macro", "macro_name"),
            )
            if str(row[column] or "")
        }
        if signals:
            item["signals"] = signals
        if str(row["comment"] or ""):
            item["comment"] = _short_text(row["comment"], 220)
        if row["x"] is not None or row["y"] is not None:
            item["position"] = {"x": row["x"], "y": row["y"]}
        property_total = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM properties WHERE owner_ref = ?",
                (row["node_ref"],),
            ).fetchone()[0]
        )
        property_rows = self._connection.execute(
            "SELECT property_ref, name, type_name, confidence, source FROM properties "
            "WHERE owner_ref = ? ORDER BY name, property_ref LIMIT ? OFFSET ?",
            (row["node_ref"], property_limit, property_offset),
        ).fetchall()
        item["properties"] = [
            {
                "ref": str(prop["property_ref"]),
                "name": str(prop["name"]),
                "typeName": str(prop["type_name"]),
                "confidence": str(prop["confidence"]),
                "source": str(prop["source"]),
            }
            for prop in property_rows
        ]
        item["propertyCoverage"] = {
            "available": property_total,
            "returned": len(property_rows),
            "offset": property_offset,
        }
        observations, observation_total = self._observation_summaries(
            "source_node_ref = ? OR target_node_ref = ?",
            (row["node_ref"], row["node_ref"]),
            offset=observation_offset,
            limit=observation_limit,
        )
        if observation_total:
            item["observations"] = observations
            item["observationCoverage"] = {
                "available": observation_total,
                "returned": len(observations),
                "offset": observation_offset,
            }
        return item

    def _pin_item(
        self,
        row: sqlite3.Row,
        *,
        observation_offset: int,
        observation_limit: int,
    ) -> dict[str, object]:
        item: dict[str, object] = {
            "ref": str(row["pin_ref"]),
            "kind": "pin",
            "nodeRef": str(row["node_ref"]),
            "name": str(row["name"]),
            "ordinal": int(row["ordinal"]),
            "nativePinId": str(row["native_pin_id"]),
            "direction": str(row["direction"]),
            "category": str(row["category"]),
            "subcategory": str(row["subcategory"]),
            "default": _json_value(row["default_value_json"], str(row["default_value_json"])),
            "defaultObject": str(row["default_object"]),
            "confidence": str(row["confidence"]),
        }
        observations, observation_total = self._observation_summaries(
            "source_pin_ref = ? OR target_pin_ref = ?",
            (row["pin_ref"], row["pin_ref"]),
            offset=observation_offset,
            limit=observation_limit,
        )
        item["observations"] = observations
        item["observationCoverage"] = {
            "available": observation_total,
            "returned": len(observations),
            "offset": observation_offset,
        }
        return item

    def _observation_summaries(
        self,
        where_sql: str,
        parameters: Sequence[object],
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, object]], int]:
        total = int(
            self._connection.execute(
                f"SELECT COUNT(*) FROM edge_observations WHERE {where_sql}",
                tuple(parameters),
            ).fetchone()[0]
        )
        rows = self._connection.execute(
            "SELECT observation_ref, graph_ref, kind, resolution_status, status, target_node_name, "
            f"target_pin_name FROM edge_observations WHERE {where_sql} "
            "ORDER BY observation_ref LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        ).fetchall()
        return (
            [
                {
                    "ref": str(observation["observation_ref"]),
                    "graphRef": str(observation["graph_ref"]),
                    "edgeKind": str(observation["kind"]),
                    "status": _status(observation["resolution_status"] or observation["status"]),
                    **(
                        {"target": _first_text(observation["target_node_name"], observation["target_pin_name"])}
                        if _first_text(observation["target_node_name"], observation["target_pin_name"])
                        else {}
                    ),
                }
                for observation in rows
            ],
            total,
        )

    @staticmethod
    def _paged_value(row: sqlite3.Row, *, value_offset: int, value_chars: int) -> dict[str, object]:
        codec = str(row["value_codec"] or "json") if "value_codec" in row.keys() else "json"
        if codec == "json":
            return {"value": _json_value(row["value_json"], str(row["value_json"]))}
        if codec != "zlib-json-utf8":
            raise ValueError(f"unsupported evidence value codec: {codec}")
        try:
            raw = zlib.decompress(bytes(row["value_blob"])).decode("utf-8")
        except (TypeError, UnicodeError, zlib.error) as exc:
            raise ValueError("compressed evidence value is corrupt") from exc
        page = raw[value_offset : value_offset + value_chars]
        return {
            "valueJsonPage": page,
            "valueCodec": codec,
            "valueCoverage": {
                "availableChars": len(raw),
                "returnedChars": len(page),
                "offset": value_offset,
            },
        }

    @classmethod
    def _default_item(cls, row: sqlite3.Row, *, value_offset: int, value_chars: int) -> dict[str, object]:
        paged_value = cls._paged_value(row, value_offset=value_offset, value_chars=value_chars)
        value_loaded = "value" in paged_value
        value = paged_value.get("value")
        extra = _json_value(row["extra_json"], {}) if "extra_json" in row.keys() else {}
        return {
            "ref": str(row["default_ref"]),
            "kind": "default",
            "name": str(row["name"]),
            "typeName": str(row["type_name"]),
            "confidence": str(row["confidence"]),
            "source": str(row["source"]),
            **project_default_value(
                str(row["type_name"]),
                value,
                extra,
                value_loaded=value_loaded,
            ),
            **paged_value,
        }

    @classmethod
    def _property_item(cls, row: sqlite3.Row, *, value_offset: int, value_chars: int) -> dict[str, object]:
        return {
            "ref": str(row["property_ref"]),
            "kind": "property",
            "ownerKind": str(row["owner_kind"]),
            "ownerRef": str(row["owner_ref"]),
            "name": str(row["name"]),
            "typeName": str(row["type_name"]),
            "confidence": str(row["confidence"]),
            "source": str(row["source"]),
            **cls._paged_value(row, value_offset=value_offset, value_chars=value_chars),
        }

    @staticmethod
    def _diagnostic_item(row: sqlite3.Row) -> dict[str, object]:
        return {
            "ref": str(row["diagnostic_ref"]),
            "kind": "diagnostic",
            "scopeKind": str(row["scope_kind"]),
            "scopeRef": str(row["scope_ref"]),
            "status": _status(row["status"], "NOT_RECOVERED"),
            "reasonCode": str(row["reason_code"]),
            "severity": str(row["severity"]),
            "title": str(row["title"]),
            "detail": _short_text(row["detail"], 260),
            "nextProbe": _short_text(row["next_probe"], 220),
        }

    def _neighborhood(self, request: Mapping[str, object], budget: int) -> dict[str, object]:
        return self._traversal_query("neighborhood", request, budget)

    def _trace(self, request: Mapping[str, object], budget: int) -> dict[str, object]:
        return self._traversal_query("trace", request, budget)

    def _traversal_query(
        self,
        operation: str,
        request: Mapping[str, object],
        budget: int,
    ) -> dict[str, object]:
        ref = self._selector_ref(request)
        self._validate_ref_revision(ref)
        node_ref = self._as_node_ref(ref)
        traversal = request.get("traversal", {})
        if traversal is None:
            traversal = {}
        if not isinstance(traversal, Mapping):
            raise ValueError("traversal must be an object")
        try:
            max_hops = int(traversal.get("maxHops", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("maxHops must be an integer") from exc
        if max_hops < 0 or max_hops > MAX_TRAVERSAL_HOPS:
            raise ValueError(f"maxHops must be between 0 and {MAX_TRAVERSAL_HOPS}")
        direction = str(traversal.get("direction") or "both").casefold()
        if direction not in {"upstream", "downstream", "both"}:
            raise ValueError("direction must be upstream, downstream, or both")
        kinds_value = traversal.get("edgeKinds", ("exec", "data"))
        if not isinstance(kinds_value, Sequence) or isinstance(kinds_value, (str, bytes)):
            raise ValueError("edgeKinds must be an array")
        edge_kinds = tuple(dict.fromkeys(str(value).casefold() for value in kinds_value if str(value).strip()))
        if not edge_kinds:
            raise ValueError("edgeKinds must not be empty")
        try:
            page_size = min(int(request.get("pageSize", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
            pin_offset = int(request.get("pinOffset", 0))
            pin_limit = min(int(request.get("pinLimit", 8)), 100)
            edge_offset = int(request.get("edgeOffset", 0))
            edge_limit = min(int(request.get("edgeLimit", 8)), 100)
        except (TypeError, ValueError) as exc:
            raise ValueError("traversal page, pin, and edge limits must be integers") from exc
        if page_size <= 0 or pin_limit <= 0 or edge_limit <= 0 or min(pin_offset, edge_offset) < 0:
            raise ValueError("traversal limits must be positive and offsets non-negative")

        node_refs = self._walk_nodes(node_ref, max_hops, direction, edge_kinds)
        signature = _query_hash(
            operation,
            ref=node_ref,
            maxHops=max_hops,
            direction=direction,
            edgeKinds=list(edge_kinds),
            pinOffset=pin_offset,
            edgeOffset=edge_offset,
        )
        start = 0
        cursor = request.get("cursor")
        if cursor is not None:
            payload = _cursor_decode(cursor)
            if str(payload.get("revision")) != self.revision_id:
                raise ValueError("STALE_CURSOR: cursor belongs to another asset revision")
            if str(payload.get("query")) != signature:
                raise ValueError("CURSOR_QUERY_MISMATCH: cursor belongs to another traversal")
            last_ref = str(payload.get("lastRef") or "")
            if last_ref not in node_refs:
                raise ValueError("INVALID_CURSOR: last traversal reference no longer exists")
            start = node_refs.index(last_ref) + 1
        page_refs = node_refs[start : start + page_size]

        def next_cursor(returned: int) -> str | None:
            position = start + returned
            if returned <= 0 or position >= len(node_refs):
                return None
            return _cursor_encode(
                {"v": 1, "revision": self.revision_id, "query": signature, "lastRef": node_refs[position - 1]}
            )

        while True:
            bundles = [
                self._node_bundle(
                    value,
                    direction,
                    edge_kinds,
                    pin_offset=pin_offset,
                    pin_limit=pin_limit,
                    edge_offset=edge_offset,
                    edge_limit=edge_limit,
                    budget=budget,
                )
                for value in page_refs
            ]
            response = self._base_response(operation, budget)
            response["traversal"] = {
                "startRef": node_ref,
                "maxHops": max_hops,
                "direction": direction,
                "edgeKinds": list(edge_kinds),
            }
            self._bounded_items(
                response,
                bundles,
                budget,
                requested=len(node_refs),
                cursor_factory=next_cursor,
            )
            if response["items"] or (pin_limit == 1 and edge_limit == 1):
                return response
            pin_limit = max(1, pin_limit // 2)
            edge_limit = max(1, edge_limit // 2)

    def _walk_nodes(
        self,
        start_ref: str,
        max_hops: int,
        direction: str,
        edge_kinds: Sequence[str],
    ) -> list[str]:
        queue: deque[tuple[str, int]] = deque([(start_ref, 0)])
        seen = {start_ref}
        ordered: list[str] = []
        while queue:
            current, depth = queue.popleft()
            ordered.append(current)
            if depth >= max_hops:
                continue
            for neighbor in self._neighbors(current, direction, edge_kinds):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, depth + 1))
        return ordered

    def _neighbors(self, node_ref: str, direction: str, edge_kinds: Sequence[str]) -> list[str]:
        placeholders = ",".join("?" for _ in edge_kinds)
        clauses: list[str] = []
        parameters: list[object] = [*edge_kinds]
        if direction in {"downstream", "both"}:
            clauses.append(
                "e.source_pin_ref IN (SELECT scoped.pin_ref FROM pins AS scoped WHERE scoped.node_ref = ?)"
            )
            parameters.append(node_ref)
        if direction in {"upstream", "both"}:
            clauses.append(
                "e.target_pin_ref IN (SELECT scoped.pin_ref FROM pins AS scoped WHERE scoped.node_ref = ?)"
            )
            parameters.append(node_ref)
        rows = self._connection.execute(
            "SELECT e.edge_ref, source_node.node_ref AS source_node_ref, target_node.node_ref AS target_node_ref "
            "FROM edges e "
            "JOIN pins source_pin ON source_pin.pin_ref = e.source_pin_ref "
            "JOIN nodes source_node ON source_node.node_ref = source_pin.node_ref "
            "JOIN pins target_pin ON target_pin.pin_ref = e.target_pin_ref "
            "JOIN nodes target_node ON target_node.node_ref = target_pin.node_ref "
            f"WHERE lower(e.kind) IN ({placeholders}) AND ({' OR '.join(clauses)}) "
            "ORDER BY e.edge_ref",
            tuple(parameters),
        ).fetchall()
        neighbors: list[str] = []
        for row in rows:
            source = str(row["source_node_ref"])
            target = str(row["target_node_ref"])
            if direction == "downstream":
                candidate = target
            elif direction == "upstream":
                candidate = source
            else:
                candidate = target if source == node_ref else source
            if candidate != node_ref and candidate not in neighbors:
                neighbors.append(candidate)
        return neighbors

    def _node_bundle(
        self,
        node_ref: str,
        direction: str,
        edge_kinds: Sequence[str],
        *,
        pin_offset: int,
        pin_limit: int,
        edge_offset: int,
        edge_limit: int,
        budget: int,
    ) -> dict[str, object]:
        node_row = self._connection.execute("SELECT * FROM nodes WHERE node_ref = ?", (node_ref,)).fetchone()
        if node_row is None:
            raise KeyError(f"node not found: {node_ref}")
        pin_total = int(
            self._connection.execute("SELECT COUNT(*) FROM pins WHERE node_ref = ?", (node_ref,)).fetchone()[0]
        )
        pin_rows = self._connection.execute(
            "SELECT * FROM pins WHERE node_ref = ? ORDER BY ordinal LIMIT ? OFFSET ?",
            (node_ref, pin_limit, pin_offset),
        ).fetchall()
        all_edges = self._edges_for_node(node_ref, direction, edge_kinds)
        edges = all_edges[edge_offset : edge_offset + edge_limit]
        pins = [self._bundle_pin_item(row) for row in pin_rows]
        coverage: dict[str, object] = {
            "pins": {"available": pin_total, "returned": len(pins), "offset": pin_offset},
            "edges": {"available": len(all_edges), "returned": len(edges), "offset": edge_offset},
        }
        next_pin_offset = pin_offset + len(pins)
        next_edge_offset = edge_offset + len(edges)
        if next_pin_offset < pin_total or next_edge_offset < len(all_edges):
            coverage["nextQuery"] = {
                "operation": "neighborhood",
                "selector": {"ref": node_ref},
                "traversal": {"maxHops": 0, "direction": direction, "edgeKinds": list(edge_kinds)},
                "pinOffset": next_pin_offset,
                "pinLimit": pin_limit,
                "edgeOffset": next_edge_offset,
                "edgeLimit": edge_limit,
                "budgetTokens": budget,
            }
        return {
            "kind": "node_bundle",
            "node": self._bundle_node_item(node_row),
            "pins": pins,
            "edges": edges,
            "bundleCoverage": coverage,
        }

    @staticmethod
    def _bundle_node_item(row: sqlite3.Row) -> dict[str, object]:
        """Return the compact, lossless-for-navigation node bundle header.

        A bundle carries several long Evidence IDs already.  Fields derivable
        from the exact node entity (position, duplicate node type, confidence)
        are intentionally left for an ``entity`` query so one traversal can
        return useful multi-hop context without splitting any pin list.
        """
        item: dict[str, object] = {
            "ref": str(row["node_ref"]),
            "kind": "node",
            "name": str(row["name"]),
            "className": str(row["class_name"]),
        }
        label = str(row["label"] or "")
        if label and label != item["name"]:
            item["label"] = label
        control_kind = str(row["control_kind"] or "")
        if control_kind and control_kind != "call":
            item["controlKind"] = control_kind
        signals = {
            key: str(row[column])
            for key, column in (
                ("function", "function_name"),
                ("variable", "variable_name"),
                ("event", "event_name"),
                ("delegate", "delegate_name"),
                ("macro", "macro_name"),
            )
            if str(row[column] or "")
        }
        if signals:
            item["signals"] = signals
        return item

    @staticmethod
    def _bundle_pin_item(row: sqlite3.Row) -> dict[str, object]:
        item: dict[str, object] = {
            "ref": str(row["pin_ref"]),
            "name": str(row["name"]),
            "direction": str(row["direction"]),
            "category": str(row["category"]),
        }
        native_pin_id = str(row["native_pin_id"] or "")
        if native_pin_id:
            item["nativePinId"] = native_pin_id
        subcategory = str(row["subcategory"] or "")
        if subcategory:
            item["subcategory"] = subcategory
        default = _json_value(row["default_value_json"], str(row["default_value_json"]))
        if default not in (None, ""):
            item["default"] = default
        default_object = str(row["default_object"] or "")
        if default_object:
            item["defaultObject"] = default_object
        return item

    def _edges_for_node(
        self,
        node_ref: str,
        direction: str,
        edge_kinds: Sequence[str],
    ) -> list[dict[str, object]]:
        placeholders = ",".join("?" for _ in edge_kinds)
        clauses: list[str] = []
        parameters: list[object] = [*edge_kinds]
        if direction in {"downstream", "both"}:
            clauses.append(
                "e.source_pin_ref IN (SELECT scoped.pin_ref FROM pins AS scoped WHERE scoped.node_ref = ?)"
            )
            parameters.append(node_ref)
        if direction in {"upstream", "both"}:
            clauses.append(
                "e.target_pin_ref IN (SELECT scoped.pin_ref FROM pins AS scoped WHERE scoped.node_ref = ?)"
            )
            parameters.append(node_ref)
        rows = self._connection.execute(
            "SELECT e.edge_ref, e.source_pin_ref, e.target_pin_ref, e.kind, e.confidence, e.resolution_status, "
            "source_node.node_ref AS source_node_ref, target_node.node_ref AS target_node_ref "
            "FROM edges e "
            "JOIN pins source_pin ON source_pin.pin_ref = e.source_pin_ref "
            "JOIN nodes source_node ON source_node.node_ref = source_pin.node_ref "
            "JOIN pins target_pin ON target_pin.pin_ref = e.target_pin_ref "
            "JOIN nodes target_node ON target_node.node_ref = target_pin.node_ref "
            f"WHERE lower(e.kind) IN ({placeholders}) AND ({' OR '.join(clauses)}) "
            "ORDER BY e.edge_ref",
            tuple(parameters),
        ).fetchall()
        return [
            self._compact_edge_item(row)
            for row in rows
        ]

    @staticmethod
    def _compact_edge_item(row: sqlite3.Row) -> dict[str, object]:
        item: dict[str, object] = {
                "ref": str(row["edge_ref"]),
                "sourcePinRef": str(row["source_pin_ref"]),
                "targetPinRef": str(row["target_pin_ref"]),
                "kind": str(row["kind"]),
        }
        status = _status(row["resolution_status"])
        if status != "CONFIRMED":
            item["status"] = status
        confidence = str(row["confidence"] or "")
        if confidence and confidence.casefold() != "high":
            item["confidence"] = confidence
        return item

    def _default_value_gap_items(self, *, scope_ref: str = "") -> list[dict[str, object]]:
        clauses = ["revision_id = ?"]
        parameters: list[object] = [self.revision_id]
        if scope_ref:
            clauses.append("default_ref = ?")
            parameters.append(scope_ref)
        rows = self._connection.execute(
            "SELECT * FROM class_defaults WHERE " + " AND ".join(clauses) + " ORDER BY default_ref",
            tuple(parameters),
        ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            codec = str(row["value_codec"] or "json")
            value_loaded = codec == "json"
            value = _json_value(row["value_json"], None) if value_loaded else None
            extra = _json_value(row["extra_json"], {})
            projection = project_default_value(
                str(row["type_name"]),
                value,
                extra,
                value_loaded=value_loaded,
            )
            gap = default_parse_gap(
                str(row["default_ref"]),
                str(row["name"]),
                str(row["type_name"]),
                projection,
            )
            if gap is not None:
                items.append(gap)
        return items

    def _gaps(self, request: Mapping[str, object], budget: int) -> dict[str, object]:
        selector = request.get("selector")
        clauses = ["revision_id = ?"]
        parameters: list[object] = [self.revision_id]
        scope_ref = ""
        if selector is not None:
            if not isinstance(selector, Mapping):
                raise ValueError("selector must be an object")
            scope_ref = str(selector.get("ref") or selector.get("scopeRef") or "")
            if scope_ref:
                self._validate_ref_revision(scope_ref)
                clauses.append("scope_ref = ?")
                parameters.append(scope_ref)
        reason = str(request.get("reasonCode") or "").strip()
        if reason:
            clauses.append("reason_code = ?")
            parameters.append(reason)
        rows = self._connection.execute(
            "SELECT * FROM diagnostics WHERE " + " AND ".join(clauses) + " ORDER BY status, reason_code, diagnostic_ref",
            tuple(parameters),
        ).fetchall()
        items = [self._diagnostic_item(row) for row in rows]
        observation_items = self._observation_gap_items(scope_ref=scope_ref)
        default_items = self._default_value_gap_items(scope_ref=scope_ref)
        if reason:
            observation_items = [item for item in observation_items if item.get("reasonCode") == reason]
            default_items = [item for item in default_items if item.get("reasonCode") == reason]
        items.extend(observation_items)
        items.extend(default_items)
        items.sort(key=lambda item: (str(item.get("status")), str(item.get("reasonCode")), str(item.get("ref"))))
        status_counts: dict[str, int] = {}
        for item in items:
            status = _status(item.get("status"), "NOT_RECOVERED")
            if status in _GAP_STATUSES:
                status_counts[status] = status_counts.get(status, 0) + 1
        try:
            page_size = min(int(request.get("pageSize", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
        except (TypeError, ValueError) as exc:
            raise ValueError("pageSize must be an integer") from exc
        if page_size <= 0:
            raise ValueError("pageSize must be positive")
        signature = _query_hash("gaps", scopeRef=scope_ref, reasonCode=reason)
        start = 0
        cursor = request.get("cursor")
        if cursor is not None:
            payload = _cursor_decode(cursor)
            if str(payload.get("revision")) != self.revision_id:
                raise ValueError("STALE_CURSOR: cursor belongs to another asset revision")
            if str(payload.get("query")) != signature:
                raise ValueError("CURSOR_QUERY_MISMATCH: cursor belongs to another gaps query")
            last_ref = str(payload.get("lastRef") or "")
            positions = [index for index, item in enumerate(items) if str(item.get("ref")) == last_ref]
            if not positions:
                raise ValueError("INVALID_CURSOR: last gap reference no longer exists")
            start = positions[0] + 1
        page_items = items[start : start + page_size]
        response = self._base_response("gaps", budget)

        def next_cursor(returned: int) -> str | None:
            position = start + returned
            if returned <= 0 or position >= len(items):
                return None
            return _cursor_encode(
                {"v": 1, "revision": self.revision_id, "query": signature, "lastRef": items[position - 1]["ref"]}
            )

        self._bounded_items(
            response,
            page_items,
            budget,
            requested=len(items),
            not_recovered=status_counts.get("NOT_RECOVERED", 0),
            status_counts=status_counts,
            cursor_factory=next_cursor,
        )
        return response

    def _observation_gap_items(self, *, scope_ref: str = "") -> list[dict[str, object]]:
        clauses = ["lower(COALESCE(NULLIF(resolution_status, ''), status, '')) <> 'resolved_pin'"]
        parameters: list[object] = []
        if scope_ref:
            clauses.append(
                "(observation_ref = ? OR graph_ref = ? OR source_node_ref = ? OR source_pin_ref = ? "
                "OR target_node_ref = ? OR target_pin_ref = ?)"
            )
            parameters.extend([scope_ref] * 6)
        rows = self._connection.execute(
            "SELECT observation_ref, graph_ref, source_node_ref, source_pin_ref, target_node_ref, target_pin_ref, "
            "target_node_name, target_native_pin_id, target_pin_name, kind, resolution_status, status, confidence "
            "FROM edge_observations WHERE " + " AND ".join(clauses) + " ORDER BY observation_ref",
            tuple(parameters),
        ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            status = _status(row["resolution_status"] or row["status"])
            if status not in _GAP_STATUSES:
                continue
            reason = {
                "HEURISTIC": "heuristic_link_resolution",
                "AMBIGUOUS": "ambiguous_link_target",
                "SOURCE_NOT_AVAILABLE": "link_source_not_available",
            }.get(status, "unresolved_link_target")
            target = _first_text(row["target_node_name"], row["target_pin_name"], row["target_native_pin_id"])
            items.append(
                {
                    "ref": str(row["observation_ref"]),
                    "kind": "edge_observation_gap",
                    "scopeKind": "graph",
                    "scopeRef": str(row["graph_ref"]),
                    "graphRef": str(row["graph_ref"]),
                    "status": status,
                    "reasonCode": reason,
                    "severity": "warning",
                    "title": "Link target requires evidence-aware review",
                    "detail": _short_text(
                        f"{row['kind']} link target {target or '<unknown>'} has parser status "
                        f"{row['resolution_status'] or row['status']}",
                        260,
                    ),
                    "nextProbe": "Query this observation ref for raw evidence and candidate Pins.",
                    "observationRef": str(row["observation_ref"]),
                }
            )
        return items

    def _diagnostic_status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self._connection.execute("SELECT status, COUNT(*) AS count FROM diagnostics GROUP BY status"):
            status = _status(row["status"], "NOT_RECOVERED")
            counts[status] = counts.get(status, 0) + int(row["count"])
        for row in self._connection.execute(
            "SELECT COALESCE(NULLIF(resolution_status, ''), status, '') AS status, COUNT(*) AS count "
            "FROM edge_observations WHERE lower(COALESCE(NULLIF(resolution_status, ''), status, '')) <> 'resolved_pin' "
            "GROUP BY COALESCE(NULLIF(resolution_status, ''), status, '')"
        ):
            status = _status(row["status"], "NOT_RECOVERED")
            if status in _GAP_STATUSES:
                counts[status] = counts.get(status, 0) + int(row["count"])
        for item in self._default_value_gap_items():
            status = _status(item.get("status"), "NOT_RECOVERED")
            if status in _GAP_STATUSES:
                counts[status] = counts.get(status, 0) + 1
        return counts

    @staticmethod
    def _selector_ref(request: Mapping[str, object]) -> str:
        selector = request.get("selector")
        if not isinstance(selector, Mapping):
            raise ValueError("selector.ref is required")
        ref = str(selector.get("ref") or "").strip()
        if not ref:
            raise ValueError("selector.ref is required")
        return ref

    def _validate_ref_revision(self, ref: str) -> None:
        parsed = urlsplit(ref)
        if parsed.scheme == "bp" and "@" in parsed.netloc:
            asset_id, revision = parsed.netloc.split("@", 1)
            if asset_id != self.asset_id or revision != self.revision_id:
                raise ValueError("STALE_REVISION: evidence reference belongs to another asset revision")

    def _as_node_ref(self, ref: str) -> str:
        if self._connection.execute("SELECT 1 FROM nodes WHERE node_ref = ?", (ref,)).fetchone():
            return ref
        row = self._connection.execute("SELECT node_ref FROM pins WHERE pin_ref = ?", (ref,)).fetchone()
        if row is not None:
            return str(row["node_ref"])
        raise ValueError("selector.ref must identify a node or pin")


__all__ = [
    "DEFAULT_BUDGET_TOKENS",
    "HARD_MAX_BUDGET_TOKENS",
    "MAX_TRAVERSAL_HOPS",
    "EvidenceQueryService",
]
