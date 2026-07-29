"""Read-only legacy knowledge import with complete row-level lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Mapping


LEGACY_IMPORTER_VERSION = "ark-kb-legacy-import/v1"
ASSET_COLUMNS = (
    "object_path",
    "asset_object_path",
    "source_object_path",
    "owner_object_path",
    "creature_object_path",
    "status_object_path",
    "item_object_path",
    "buff_object_path",
)
EVIDENCE_PREFIXES = (
    "bp://",
    "native://",
    "runtime://",
    "evidence://",
    "class-edge://",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _safe_asset_uri(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if text.startswith(("/Game/", "/Mods/", "/Script/")):
        return text
    if text.startswith("/") and ":" not in text:
        return text
    return ""


def _find_evidence(value: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(EVIDENCE_PREFIXES):
            return text
        if text.startswith(("{", "[")):
            try:
                return _find_evidence(json.loads(text))
            except (json.JSONDecodeError, RecursionError):
                return ""
        return ""
    if isinstance(value, Mapping):
        for key in (
            "evidence_id",
            "evidenceId",
            "evidence_uri",
            "evidenceUri",
            "source_evidence_id",
            "sourceEvidenceId",
            "ref",
            "id",
        ):
            if key in value:
                result = _find_evidence(value[key])
                if result:
                    return result
        for item in value.values():
            result = _find_evidence(item)
            if result:
                return result
        return ""
    if isinstance(value, list):
        for item in value:
            result = _find_evidence(item)
            if result:
                return result
    return ""


def _primary_key(
    row: Mapping[str, object],
    pk_columns: list[str],
) -> str:
    if pk_columns:
        payload = {name: row.get(name) for name in pk_columns}
    else:
        payload = {"rowid": row.get("__rowid")}
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _row_asset_uri(row: Mapping[str, object]) -> str:
    for column in ASSET_COLUMNS:
        value = _safe_asset_uri(row.get(column))
        if value:
            return value
    return ""


def _row_evidence(
    row: Mapping[str, object],
    *,
    database_name: str,
    table_name: str,
    primary_key: str,
) -> tuple[str, bool]:
    for column in (
        "evidence_uri",
        "evidence_id",
        "source_evidence_id",
        "evidence_json",
        "source_json",
        "source_capture",
        "source_graph",
    ):
        if evidence := _find_evidence(row.get(column)):
            return evidence, True
    digest = hashlib.sha256(primary_key.encode("utf-8")).hexdigest()
    return (
        f"legacy://{database_name}/{table_name}/{digest}",
        False,
    )


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        if str(row[0]) != "metadata"
    ]


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[list[str], list[str]]:
    columns = list(connection.execute(f"PRAGMA table_info({_quote(table)})"))
    names = [str(row[1]) for row in columns]
    primary = [
        str(row[1])
        for row in sorted(columns, key=lambda item: int(item[5]))
        if int(row[5]) > 0
    ]
    return names, primary


def _iter_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: list[str],
) -> tuple[sqlite3.Cursor, bool]:
    selected = ", ".join(_quote(name) for name in columns)
    try:
        return (
            connection.execute(
                f"SELECT rowid AS __rowid, {selected} FROM {_quote(table)}"
            ),
            True,
        )
    except sqlite3.OperationalError:
        return (
            connection.execute(
                f"SELECT {selected} FROM {_quote(table)}"
            ),
            False,
        )


def import_legacy_lineage(
    *,
    core: sqlite3.Connection,
    legacy_root: Path,
    generated_at: str,
) -> dict[str, object]:
    """Import every non-metadata legacy row without promoting unproved facts."""

    legacy_root = legacy_root.resolve()
    if not legacy_root.is_dir():
        return {
            "databases": 0,
            "tables": 0,
            "rows": 0,
            "resolvedEntities": 0,
            "withDirectEvidence": 0,
            "legacyUnverified": 0,
            "byDatabase": {},
        }
    entity_by_uri = {
        str(uri): int(entity_id)
        for uri, entity_id in core.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    for class_path, entity_id in core.execute(
        """
        SELECT c.class_path, a.entity_id
        FROM classes AS c
        JOIN asset_class_assignments AS a ON a.class_id=c.class_id
        WHERE a.assignment_kind='GENERATED_CLASS'
        """
    ):
        entity_by_uri.setdefault(str(class_path), int(entity_id))

    next_revision = int(
        core.execute(
            "SELECT COALESCE(MAX(revision_id), 0) + 1 FROM source_revisions"
        ).fetchone()[0]
    )
    total_tables = 0
    total_rows = 0
    resolved_entities = 0
    direct_evidence = 0
    legacy_unverified = 0
    by_database: dict[str, int] = {}

    for database_path in sorted(legacy_root.glob("*.sqlite")):
        database_name = database_path.name
        fingerprint = _sha256_file(database_path)
        revision_id = next_revision
        next_revision += 1
        core.execute(
            """
            INSERT INTO source_revisions(
                revision_id, source_kind, source_uri, source_fingerprint,
                producer_version, schema_version, generated_at,
                freshness_status
            ) VALUES (?, 'legacy_kb', ?, ?, ?, 'legacy/unknown', ?, 'LEGACY')
            """,
            (
                revision_id,
                f"legacy-kb://{database_name}",
                fingerprint,
                LEGACY_IMPORTER_VERSION,
                generated_at,
            ),
        )
        legacy = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro",
            uri=True,
        )
        legacy.row_factory = sqlite3.Row
        database_rows = 0
        try:
            for table in _tables(legacy):
                columns, primary_columns = _table_columns(legacy, table)
                cursor, has_rowid = _iter_rows(legacy, table, columns)
                total_tables += 1
                while batch := cursor.fetchmany(2_000):
                    lineage_rows: list[tuple[object, ...]] = []
                    for sqlite_row in batch:
                        row = dict(sqlite_row)
                        if not has_rowid:
                            row["__rowid"] = None
                        primary_key = _primary_key(row, primary_columns)
                        source_asset_uri = _row_asset_uri(row)
                        target_id = entity_by_uri.get(source_asset_uri)
                        evidence_uri, is_direct = _row_evidence(
                            row,
                            database_name=database_name,
                            table_name=table,
                            primary_key=primary_key,
                        )
                        status = (
                            "IMPORTED_WITH_EVIDENCE"
                            if target_id is not None and is_direct
                            else "LEGACY_UNVERIFIED"
                        )
                        lineage_rows.append(
                            (
                                (
                                    "ENTITY"
                                    if target_id is not None
                                    else "UNRESOLVED_LEGACY_ROW"
                                ),
                                target_id,
                                database_name,
                                table,
                                primary_key,
                                source_asset_uri,
                                evidence_uri,
                                status,
                                revision_id,
                            )
                        )
                        total_rows += 1
                        database_rows += 1
                        resolved_entities += int(target_id is not None)
                        direct_evidence += int(is_direct)
                        legacy_unverified += int(
                            status == "LEGACY_UNVERIFIED"
                        )
                    core.executemany(
                        """
                        INSERT INTO legacy_lineage(
                            target_kind, target_id, legacy_database,
                            legacy_table, legacy_primary_key,
                            source_asset_uri, evidence_uri, status,
                            source_revision_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        lineage_rows,
                    )
        finally:
            legacy.close()
        by_database[database_name] = database_rows
    core.commit()
    return {
        "databases": len(by_database),
        "tables": total_tables,
        "rows": total_rows,
        "resolvedEntities": resolved_entities,
        "withDirectEvidence": direct_evidence,
        "legacyUnverified": legacy_unverified,
        "byDatabase": by_database,
    }
