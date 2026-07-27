"""Read-only legacy/vNext shadow comparison with explicit comparability."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Mapping

from .kb_api import VNextKnowledgeService


IDENTITY_COLUMNS = (
    "object_path",
    "asset_object_path",
    "asset_path",
    "source_asset_uri",
    "blueprint_path",
    "class_path",
    "canonical_uri",
)
FACT_TYPE_COLUMNS = ("fact_type", "type")
FACT_NAME_COLUMNS = (
    "fact_name",
    "property_name",
    "field_name",
    "name",
)
VALUE_COLUMNS = (
    "value_text",
    "value_number",
    "value_integer",
    "value_json",
    "value",
    "amount",
)
STATUS_COLUMNS = ("status", "value_status", "confidence")
EVIDENCE_COLUMNS = (
    "evidence_uri",
    "source_evidence_id",
    "evidence_id",
)
_LOCAL_PATH = re.compile(
    r"(?i)(?:[a-z]:\\(?:users|windows|program files|programdata)\\|"
    r"/(?:home|users|etc|var|tmp)/)"
)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _first(
    row: Mapping[str, object], candidates: tuple[str, ...]
) -> object | None:
    lookup = {str(key).casefold(): value for key, value in row.items()}
    for candidate in candidates:
        if candidate.casefold() in lookup:
            return lookup[candidate.casefold()]
    return None


def _safe_value(value: object) -> object:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if _LOCAL_PATH.search(text):
        return "[LOCAL_PATH_REDACTED]"
    return text[:300] + ("..." if len(text) > 300 else "")


def _table_fact_type(table: str) -> str:
    lowered = table.casefold()
    mappings = (
        ("buff", "STATUS_EFFECT"),
        ("loot", "LOOT_ENTRY"),
        ("item", "ITEM_PROPERTY"),
        ("status", "STATUS_EFFECT"),
        ("harvest", "HARVEST_RULE"),
        ("mission", "MISSION_REWARD"),
    )
    return next(
        (fact_type for token, fact_type in mappings if token in lowered),
        "",
    )


def query_legacy_read_only(
    legacy_root: Path,
    *,
    entity_query: str,
    fact_types: tuple[str, ...] = (),
    fact_names: tuple[str, ...] = (),
    limit: int = 50,
) -> dict[str, object]:
    """Search stable identity columns only; never mutate or expose file paths."""

    limit = max(1, min(100, int(limit)))
    items: list[dict[str, object]] = []
    omitted = 0
    entity_leaf = entity_query.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
    for database in sorted(legacy_root.glob("*.sqlite")):
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            tables = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            ]
            for table in tables:
                columns = [
                    str(row[1])
                    for row in connection.execute(
                        f"PRAGMA table_info({_quote(table)})"
                    )
                ]
                identity = next(
                    (
                        column
                        for column in columns
                        if column.casefold()
                        in {value.casefold() for value in IDENTITY_COLUMNS}
                    ),
                    None,
                )
                if identity is None:
                    continue
                remaining = limit - len(items)
                if remaining <= 0:
                    omitted += int(
                        connection.execute(
                            f"""
                            SELECT COUNT(*) FROM {_quote(table)}
                            WHERE lower(CAST({_quote(identity)} AS TEXT))
                              IN (lower(?), lower(?))
                            """,
                            (entity_query, entity_leaf),
                        ).fetchone()[0]
                    )
                    continue
                rows = list(
                    connection.execute(
                        f"""
                        SELECT * FROM {_quote(table)}
                        WHERE lower(CAST({_quote(identity)} AS TEXT))
                          IN (lower(?), lower(?))
                        LIMIT ?
                        """,
                        (entity_query, entity_leaf, remaining + 1),
                    )
                )
                if len(rows) > remaining:
                    omitted += len(rows) - remaining
                    rows = rows[:remaining]
                for source_row in rows:
                    raw = dict(source_row)
                    fact_type = str(
                        _first(raw, FACT_TYPE_COLUMNS)
                        or _table_fact_type(table)
                    ).upper()
                    fact_name = str(
                        _first(raw, FACT_NAME_COLUMNS) or ""
                    )
                    if fact_types and fact_type not in {
                        item.upper() for item in fact_types
                    }:
                        continue
                    if fact_names and fact_name not in fact_names:
                        continue
                    public_fields = {
                        str(key): _safe_value(value)
                        for key, value in list(raw.items())[:12]
                    }
                    items.append(
                        {
                            "database": database.name,
                            "table": table,
                            "identity": _safe_value(raw.get(identity)),
                            "factType": fact_type,
                            "factName": fact_name,
                            "value": _safe_value(
                                _first(raw, VALUE_COLUMNS)
                            ),
                            "status": str(
                                _first(raw, STATUS_COLUMNS)
                                or "LEGACY_UNVERIFIED"
                            ).upper(),
                            "evidenceUri": _safe_value(
                                _first(raw, EVIDENCE_COLUMNS)
                            ),
                            "fields": public_fields,
                        }
                    )
        finally:
            connection.close()
    return {
        "items": items,
        "returned": len(items),
        "omitted": omitted,
        "nextQuery": "",
        "freshness": (
            "UNKNOWN"
            if not items
            else (
                "STALE"
                if any(item["status"] == "STALE" for item in items)
                else "LEGACY_UNKNOWN"
            )
        ),
        "evidence": [
            {
                "evidenceUri": item["evidenceUri"],
                "database": item["database"],
                "table": item["table"],
            }
            for item in items
            if item["evidenceUri"]
        ],
        "gap": (
            []
            if items
            else [
                {
                    "code": "LEGACY_NO_MATCH",
                    "detail": "No row matched a stable legacy identity column.",
                }
            ]
        ),
    }


def _semantic_map(
    items: list[Mapping[str, object]], *, vnext: bool
) -> dict[tuple[str, str], tuple[str, str]]:
    result: dict[tuple[str, str], tuple[str, str]] = {}
    for item in items:
        fact_type = str(item.get("factType") or "").upper()
        fact_name = str(item.get("factName") or "")
        if not fact_type or not fact_name:
            continue
        if vnext:
            value = next(
                (
                    item.get(key)
                    for key in (
                        "valueText",
                        "valueNumber",
                        "valueInteger",
                        "valueJson",
                    )
                    if item.get(key) is not None
                ),
                None,
            )
        else:
            value = item.get("value")
        result[(fact_type, fact_name)] = (
            json.dumps(value, ensure_ascii=False, sort_keys=True),
            str(item.get("status") or "UNKNOWN").upper(),
        )
    return result


class LegacyVNextComparator:
    def __init__(
        self,
        *,
        vnext: VNextKnowledgeService,
        legacy_root: Path,
    ) -> None:
        self.vnext = vnext
        self.legacy_root = legacy_root.resolve()

    def compare(self, request: Mapping[str, object]) -> dict[str, object]:
        vnext_result = self.vnext.query(request)
        fact_types = tuple(
            str(value)
            for value in request.get("factTypes", [])
            if str(value)
        ) if isinstance(request.get("factTypes", []), list) else ()
        fact_names = tuple(
            str(value)
            for value in request.get("factNames", [])
            if str(value)
        ) if isinstance(request.get("factNames", []), list) else ()
        legacy = query_legacy_read_only(
            self.legacy_root,
            entity_query=str(request.get("entity") or ""),
            fact_types=fact_types,
            fact_names=fact_names,
            limit=int(request.get("evidenceLimit") or 50),
        )
        legacy_items = [
            item
            for item in legacy["items"]
            if isinstance(item, Mapping)
        ]
        vnext_items = [
            item
            for item in vnext_result.get("facts", [])
            if isinstance(item, Mapping)
        ]
        legacy_map = _semantic_map(legacy_items, vnext=False)
        vnext_map = _semantic_map(vnext_items, vnext=True)
        shared = sorted(set(legacy_map) & set(vnext_map))
        reasons: list[str] = []
        consistent: bool | None
        if not legacy_items:
            reasons.append("LEGACY_NO_MATCH")
            consistent = None
        elif not vnext_items:
            reasons.append("VNEXT_NO_MATCH")
            consistent = None
        elif not shared:
            reasons.append("SEMANTIC_ROWS_NOT_COMPARABLE")
            consistent = None
        else:
            for key in shared:
                legacy_value, legacy_status = legacy_map[key]
                vnext_value, vnext_status = vnext_map[key]
                if legacy_value != vnext_value:
                    reasons.append(
                        f"VALUE_MISMATCH:{key[0]}:{key[1]}"
                    )
                if legacy_status != vnext_status:
                    reasons.append(
                        f"STATUS_MISMATCH:{key[0]}:{key[1]}"
                    )
            consistent = not any(
                reason.startswith(("VALUE_MISMATCH", "STATUS_MISMATCH"))
                for reason in reasons
            )
            if consistent:
                reasons.append("MATCH")
        vnext_evidence_count = len(vnext_result.get("evidence", []))
        legacy_evidence_count = len(legacy["evidence"])
        vnext_complete = (
            vnext_result.get("route") == "DB_ONLY_COMPLETE"
            and vnext_evidence_count > 0
        )
        preferred_source = (
            "vnext"
            if vnext_complete
            else (
                "legacy"
                if legacy_items and not vnext_items
                else "none"
            )
        )
        return {
            "mode": "compare",
            "consistent": consistent,
            "differenceReasons": reasons,
            "comparableKeys": [
                {"factType": key[0], "factName": key[1]}
                for key in shared
            ],
            "preferredSource": preferred_source,
            "evidenceCompleteness": {
                "legacy": legacy_evidence_count,
                "vnext": vnext_evidence_count,
                "vnextComplete": vnext_complete,
            },
            "staleOrUnknown": {
                "legacy": legacy["freshness"],
                "vnext": vnext_result.get("freshness", "UNKNOWN"),
            },
            "legacy": legacy,
            "vnext": vnext_result,
            "returned": len(shared),
            "omitted": int(legacy["omitted"])
            + int(vnext_result.get("omitted", 0)),
            "nextQuery": "",
            "freshness": (
                "FRESH"
                if vnext_complete
                else str(vnext_result.get("freshness") or "UNKNOWN")
            ),
            "evidence": [
                *legacy["evidence"],
                *vnext_result.get("evidence", []),
            ],
            "gap": [
                {"code": reason, "detail": reason}
                for reason in reasons
                if reason != "MATCH"
            ],
        }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
