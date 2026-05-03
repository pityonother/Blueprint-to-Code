from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


PARSER_VERSION = "uasset_binary_reader_v1"
SUCCESS_STATUSES = {"read", "skipped_existing", "skipped_processed"}


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def file_info(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"size": 0, "modified": ""}
    stat = path.stat()
    return {
        "size": int(stat.st_size),
        "modified": _dt.datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
    }


def metadata_fingerprint(
    *,
    uasset_size: int = 0,
    uasset_modified: str = "",
    uexp_size: int = 0,
    uexp_modified: str = "",
    ubulk_size: int = 0,
    ubulk_modified: str = "",
) -> str:
    payload = {
        "uasset_size": int(uasset_size or 0),
        "uasset_modified": str(uasset_modified or ""),
        "uexp_size": int(uexp_size or 0),
        "uexp_modified": str(uexp_modified or ""),
        "ubulk_size": int(ubulk_size or 0),
        "ubulk_modified": str(ubulk_modified or ""),
    }
    return hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()


def fingerprint_for_scan_item(item: dict[str, Any]) -> str:
    return metadata_fingerprint(
        uasset_size=int(item.get("uasset_size") or 0),
        uasset_modified=str(item.get("uasset_modified") or item.get("modified") or ""),
        uexp_size=int(item.get("uexp_size") or 0),
        uexp_modified=str(item.get("uexp_modified") or ""),
        ubulk_size=int(item.get("ubulk_size") or 0),
        ubulk_modified=str(item.get("ubulk_modified") or ""),
    )


def asset_file_metadata(uasset_path: Path | str | None) -> dict[str, Any]:
    if not uasset_path:
        return {
            "uasset_path": "",
            "uasset_size": 0,
            "uasset_modified": "",
            "uexp_size": 0,
            "uexp_modified": "",
            "ubulk_size": 0,
            "ubulk_modified": "",
            "fingerprint": "",
        }
    path = Path(uasset_path)
    uasset = file_info(path)
    uexp = file_info(path.with_suffix(".uexp"))
    ubulk = file_info(path.with_suffix(".ubulk"))
    fingerprint = metadata_fingerprint(
        uasset_size=uasset["size"],
        uasset_modified=uasset["modified"],
        uexp_size=uexp["size"],
        uexp_modified=uexp["modified"],
        ubulk_size=ubulk["size"],
        ubulk_modified=ubulk["modified"],
    )
    return {
        "uasset_path": str(path),
        "uasset_size": uasset["size"],
        "uasset_modified": uasset["modified"],
        "uexp_size": uexp["size"],
        "uexp_modified": uexp["modified"],
        "ubulk_size": ubulk["size"],
        "ubulk_modified": ubulk["modified"],
        "fingerprint": fingerprint,
    }


def ensure_ledger_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_assets (
            object_path TEXT PRIMARY KEY,
            asset_name TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            uasset_path TEXT NOT NULL DEFAULT '',
            uasset_size INTEGER NOT NULL DEFAULT 0,
            uasset_modified TEXT NOT NULL DEFAULT '',
            uexp_size INTEGER NOT NULL DEFAULT 0,
            uexp_modified TEXT NOT NULL DEFAULT '',
            ubulk_size INTEGER NOT NULL DEFAULT 0,
            ubulk_modified TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL DEFAULT '',
            read_status TEXT NOT NULL,
            knowledge_status TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            graph_count INTEGER NOT NULL DEFAULT 0,
            node_count INTEGER NOT NULL DEFAULT 0,
            pin_count INTEGER NOT NULL DEFAULT 0,
            link_count INTEGER NOT NULL DEFAULT 0,
            status_counts_json TEXT NOT NULL DEFAULT '{}',
            capture_dir TEXT NOT NULL DEFAULT '',
            last_read_at TEXT NOT NULL,
            last_imported_at TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS failed_assets (
            object_path TEXT PRIMARY KEY,
            asset_name TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            uasset_path TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL DEFAULT '',
            error_status TEXT NOT NULL,
            error_message TEXT NOT NULL DEFAULT '',
            attempted_json TEXT NOT NULL DEFAULT '[]',
            failure_count INTEGER NOT NULL DEFAULT 1,
            parser_version TEXT NOT NULL,
            last_failed_at TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS deferred_assets (
            object_path TEXT NOT NULL,
            group_id TEXT NOT NULL,
            asset_name TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (object_path, group_id)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_processed_fingerprint ON processed_assets(fingerprint)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_processed_knowledge ON processed_assets(knowledge_status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_failed_status ON failed_assets(error_status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_deferred_group ON deferred_assets(group_id)")


def _read_table(connection: sqlite3.Connection, table: str, key: str = "object_path") -> dict[str, dict[str, Any]]:
    try:
        cursor = connection.execute(f"SELECT * FROM {table}")
    except sqlite3.Error:
        return {}
    columns = [item[0] for item in cursor.description or []]
    rows: dict[str, dict[str, Any]] = {}
    for row in cursor.fetchall():
        payload = dict(zip(columns, row))
        rows[str(payload.get(key) or "")] = payload
    return rows


def read_ledger_snapshot(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return {"processed": {}, "failed": {}, "deferred": []}
    connection = sqlite3.connect(db_path)
    try:
        ensure_ledger_tables(connection)
        processed = _read_table(connection, "processed_assets")
        failed = _read_table(connection, "failed_assets")
        try:
            cursor = connection.execute("SELECT * FROM deferred_assets")
            columns = [item[0] for item in cursor.description or []]
            deferred = [dict(zip(columns, row)) for row in cursor.fetchall()]
        except sqlite3.Error:
            deferred = []
        return {"processed": processed, "failed": failed, "deferred": deferred}
    finally:
        connection.close()


def restore_ledger_snapshot(connection: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    ensure_ledger_tables(connection)
    for row in (snapshot.get("processed") or {}).values():
        connection.execute(
            """
            INSERT OR REPLACE INTO processed_assets (
                object_path, asset_name, asset_type, domain, uasset_path,
                uasset_size, uasset_modified, uexp_size, uexp_modified,
                ubulk_size, ubulk_modified, fingerprint, read_status,
                knowledge_status, parser_version, graph_count, node_count,
                pin_count, link_count, status_counts_json, capture_dir,
                last_read_at, last_imported_at, result_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("object_path", ""),
                row.get("asset_name", ""),
                row.get("asset_type", ""),
                row.get("domain", ""),
                row.get("uasset_path", ""),
                int(row.get("uasset_size") or 0),
                row.get("uasset_modified", ""),
                int(row.get("uexp_size") or 0),
                row.get("uexp_modified", ""),
                int(row.get("ubulk_size") or 0),
                row.get("ubulk_modified", ""),
                row.get("fingerprint", ""),
                row.get("read_status", ""),
                row.get("knowledge_status", ""),
                row.get("parser_version", ""),
                int(row.get("graph_count") or 0),
                int(row.get("node_count") or 0),
                int(row.get("pin_count") or 0),
                int(row.get("link_count") or 0),
                row.get("status_counts_json", "{}"),
                row.get("capture_dir", ""),
                row.get("last_read_at", ""),
                row.get("last_imported_at", ""),
                row.get("result_json", "{}"),
            ),
        )
    for row in (snapshot.get("failed") or {}).values():
        connection.execute(
            """
            INSERT OR REPLACE INTO failed_assets (
                object_path, asset_name, asset_type, domain, uasset_path,
                fingerprint, error_status, error_message, attempted_json,
                failure_count, parser_version, last_failed_at, result_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("object_path", ""),
                row.get("asset_name", ""),
                row.get("asset_type", ""),
                row.get("domain", ""),
                row.get("uasset_path", ""),
                row.get("fingerprint", ""),
                row.get("error_status", ""),
                row.get("error_message", ""),
                row.get("attempted_json", "[]"),
                int(row.get("failure_count") or 1),
                row.get("parser_version", ""),
                row.get("last_failed_at", ""),
                row.get("result_json", "{}"),
            ),
        )
    connection.commit()


def ledger_row_current(row: dict[str, Any] | None, fingerprint: str, *, parser_version: str = PARSER_VERSION) -> bool:
    if not row:
        return False
    if str(row.get("fingerprint") or "") != str(fingerprint or ""):
        return False
    if str(row.get("parser_version") or "") != parser_version:
        return False
    return True


def annotate_scan_item(item: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    fingerprint = fingerprint_for_scan_item(item)
    object_path = str(item.get("object_path") or "")
    processed = (snapshot.get("processed") or {}).get(object_path)
    failed = (snapshot.get("failed") or {}).get(object_path)
    processed_current = ledger_row_current(processed, fingerprint)
    failed_current = ledger_row_current(failed, fingerprint)
    item["fingerprint"] = fingerprint
    item["processed_current"] = processed_current
    item["failed_current"] = failed_current
    item["knowledge_status"] = str((processed or {}).get("knowledge_status") or "")
    item["read_status"] = str((processed or {}).get("read_status") or "")
    item["last_read_at"] = str((processed or {}).get("last_read_at") or "")
    item["failure_count"] = int((failed or {}).get("failure_count") or 0) if failed_current else 0
    item["last_failed_at"] = str((failed or {}).get("last_failed_at") or "") if failed_current else ""
    return item


def processed_current_for_path(db_path: Path, object_path: str, uasset_path: Path | str) -> bool:
    if not db_path.is_file():
        return False
    metadata = asset_file_metadata(uasset_path)
    snapshot = read_ledger_snapshot(db_path)
    row = (snapshot.get("processed") or {}).get(object_path)
    return ledger_row_current(row, str(metadata.get("fingerprint") or ""))


def lookup_asset_row(connection: sqlite3.Connection, object_path: str) -> dict[str, Any]:
    row = None
    for table in ("assets", "asset_files"):
        try:
            cursor = connection.execute(
                f"SELECT asset_name, asset_type, domain, uasset_path FROM {table} WHERE object_path = ? LIMIT 1",
                (object_path,),
            )
            row = cursor.fetchone()
        except sqlite3.Error:
            row = None
        if row:
            break
    if not row:
        return {}
    return {
        "asset_name": row[0],
        "asset_type": row[1],
        "domain": row[2],
        "uasset_path": row[3],
    }


def record_asset_results(
    db_path: Path,
    results: list[dict[str, Any]],
    *,
    knowledge_status: str,
    parser_version: str = PARSER_VERSION,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        ensure_ledger_tables(connection)
        timestamp = now_iso()
        for result in results:
            object_path = str(result.get("asset_path") or result.get("object_path") or "")
            if not object_path:
                continue
            asset_row = lookup_asset_row(connection, object_path)
            asset_name = str(result.get("asset_name") or asset_row.get("asset_name") or object_path.rsplit(".", 1)[-1])
            uasset_path = str(result.get("uasset_path") or asset_row.get("uasset_path") or "")
            metadata = asset_file_metadata(uasset_path)
            status = str(result.get("status") or "unknown")
            if status in SUCCESS_STATUSES:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO processed_assets (
                        object_path, asset_name, asset_type, domain, uasset_path,
                        uasset_size, uasset_modified, uexp_size, uexp_modified,
                        ubulk_size, ubulk_modified, fingerprint, read_status,
                        knowledge_status, parser_version, graph_count, node_count,
                        pin_count, link_count, status_counts_json, capture_dir,
                        last_read_at, last_imported_at, result_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        object_path,
                        asset_name,
                        str(asset_row.get("asset_type") or result.get("asset_type") or ""),
                        str(asset_row.get("domain") or result.get("domain") or ""),
                        str(metadata.get("uasset_path") or uasset_path),
                        int(metadata.get("uasset_size") or 0),
                        str(metadata.get("uasset_modified") or ""),
                        int(metadata.get("uexp_size") or 0),
                        str(metadata.get("uexp_modified") or ""),
                        int(metadata.get("ubulk_size") or 0),
                        str(metadata.get("ubulk_modified") or ""),
                        str(metadata.get("fingerprint") or ""),
                        status,
                        knowledge_status,
                        parser_version,
                        int(result.get("graph_count") or 0),
                        int(result.get("node_count") or 0),
                        int(result.get("pin_count") or 0),
                        int(result.get("link_count") or 0),
                        json_dumps(result.get("status_counts") or {}),
                        str(result.get("asset_dir") or ""),
                        timestamp,
                        timestamp if knowledge_status == "imported" else "",
                        json_dumps(result),
                    ),
                )
                connection.execute("DELETE FROM failed_assets WHERE object_path = ?", (object_path,))
                continue

            existing = connection.execute(
                "SELECT failure_count FROM failed_assets WHERE object_path = ?",
                (object_path,),
            ).fetchone()
            failure_count = int(existing[0]) + 1 if existing else 1
            connection.execute(
                """
                INSERT OR REPLACE INTO failed_assets (
                    object_path, asset_name, asset_type, domain, uasset_path,
                    fingerprint, error_status, error_message, attempted_json,
                    failure_count, parser_version, last_failed_at, result_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    object_path,
                    asset_name,
                    str(asset_row.get("asset_type") or result.get("asset_type") or ""),
                    str(asset_row.get("domain") or result.get("domain") or ""),
                    str(metadata.get("uasset_path") or uasset_path),
                    str(metadata.get("fingerprint") or ""),
                    status,
                    str(result.get("error") or result.get("message") or ""),
                    json_dumps(result.get("attempted") or []),
                    failure_count,
                    parser_version,
                    timestamp,
                    json_dumps(result),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def replace_deferred_assets(db_path: Path, priority: dict[str, Any]) -> None:
    if not db_path.is_file():
        return
    connection = sqlite3.connect(db_path)
    try:
        ensure_ledger_tables(connection)
        connection.execute("DELETE FROM deferred_assets")
        timestamp = now_iso()
        rows: list[tuple[Any, ...]] = []
        for group_id, group in (priority.get("groups") or {}).items():
            for item in group.get("deferred_candidates") or []:
                object_path = str(item.get("object_path") or "")
                if not object_path:
                    continue
                rows.append(
                    (
                        object_path,
                        str(group_id),
                        str(item.get("asset_name") or ""),
                        str(item.get("asset_type") or ""),
                        str(item.get("domain") or ""),
                        int(item.get("score") or 0),
                        str(item.get("deferred_reason") or ""),
                        timestamp,
                    )
                )
        connection.executemany(
            """
            INSERT OR REPLACE INTO deferred_assets (
                object_path, group_id, asset_name, asset_type, domain, score, reason, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()
