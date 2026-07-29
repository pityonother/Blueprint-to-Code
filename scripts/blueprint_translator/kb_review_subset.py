"""Export a bounded, reproducible review subset from a Discovery database.

The Discovery SQLite remains read-only.  Every data-bearing review artifact is
materialized from a documented SQL query so reviewers do not need Git LFS or
the multi-gigabyte source database.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path


REVIEW_SCHEMA = "blueprint-to-code-kb-discovery-review/v1"
MAX_REVIEW_ROWS = 300
UNKNOWN_STATES = (
    "UNKNOWN",
    "AMBIGUOUS",
    "NOT_RECOVERED",
    "NOT_MEASURED",
    "SOURCE_NOT_AVAILABLE",
    "STALE",
)


def _canonical_json(value: object, *, pretty: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _git_commit(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    return result.stdout.strip() or "UNKNOWN"


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    quoted = '"' + table.replace('"', '""') + '"'
    return int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])


def _query_rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Sequence[object] = (),
) -> tuple[list[dict[str, object]], list[str]]:
    cursor = connection.execute(sql, parameters)
    fieldnames = [str(item[0]) for item in (cursor.description or ())]
    return [dict(row) for row in cursor], fieldnames


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _write_json(path: Path, value: object) -> None:
    _write_text(path, _canonical_json(value, pretty=True) + "\n")


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    _write_text(
        path,
        "".join(_canonical_json(row) + "\n" for row in rows),
    )


def _write_csv(
    path: Path,
    rows: Sequence[dict[str, object]],
    *,
    fieldnames: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        if fieldnames:
            writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
            writer.writeheader()
            writer.writerows(rows)
    os.replace(temporary, path)


def _schema_sql(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type, name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
          AND name NOT LIKE 'sqlite_%'
        ORDER BY
          CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,
          name
        """
    )
    statements = [str(row["sql"]).rstrip(";") + ";" for row in rows]
    return "\n\n".join(statements) + "\n"


def _sql_catalog(limit: int) -> dict[str, tuple[str, tuple[object, ...]]]:
    common_asset_columns = """
        object_path, asset_name, asset_class_path, generated_class_path,
        parent_class_path, native_parent_class_path, blueprint_kind,
        identity_status, identity_confidence, capture_exists,
        evidence_freshness, descendant_count, referencer_count,
        component_reuse_count, cross_domain_reference_count,
        registry_usage_count, native_call_count, unresolved_native_call_count,
        query_hit_count, existing_report_count, provisional_tier,
        provisional_reasons_json
    """
    return {
        "query_corpus.jsonl": (
            """
            SELECT *
            FROM query_corpus
            ORDER BY query_id
            """,
            (),
        ),
        "representative_sample_manifest.json": (
            """
            SELECT
                s.object_path,
                s.selection_reason,
                s.source_rank,
                a.asset_name,
                a.asset_class_path,
                a.parent_class_path,
                a.native_parent_class_path,
                a.provisional_tier,
                a.evidence_freshness,
                a.identity_status
            FROM sample_membership AS s
            LEFT JOIN assets AS a ON a.object_path=s.object_path
            ORDER BY s.selection_reason, s.source_rank, s.object_path
            """,
            (),
        ),
        "top_descendant_assets.csv": (
            f"""
            SELECT {common_asset_columns}
            FROM assets
            WHERE descendant_count > 0
            ORDER BY descendant_count DESC, object_path
            LIMIT ?
            """,
            (limit,),
        ),
        "top_referenced_assets.csv": (
            f"""
            SELECT {common_asset_columns}
            FROM assets
            WHERE referencer_count > 0
            ORDER BY referencer_count DESC, object_path
            LIMIT ?
            """,
            (limit,),
        ),
        "top_component_reuse_assets.csv": (
            f"""
            SELECT {common_asset_columns}
            FROM assets
            WHERE component_reuse_count > 0
            ORDER BY component_reuse_count DESC, object_path
            LIMIT ?
            """,
            (limit,),
        ),
        "top_cross_domain_assets.csv": (
            f"""
            SELECT {common_asset_columns}
            FROM assets
            WHERE cross_domain_reference_count > 0
            ORDER BY cross_domain_reference_count DESC, object_path
            LIMIT ?
            """,
            (limit,),
        ),
        "top_registration_targets.csv": (
            """
            SELECT
                target_object_path,
                COUNT(*) AS registration_count,
                COUNT(DISTINCT owner_object_path) AS owner_count,
                COUNT(DISTINCT registration_type) AS registration_type_count,
                GROUP_CONCAT(DISTINCT registration_type) AS registration_types,
                MIN(confidence) AS minimum_confidence
            FROM system_registrations
            GROUP BY target_object_path
            ORDER BY registration_count DESC, target_object_path
            LIMIT ?
            """,
            (limit,),
        ),
        "top_native_boundary_candidates.csv": (
            """
            SELECT
                blueprint_asset_path,
                blueprint_function_name,
                status,
                resolution_method,
                confidence,
                COUNT(*) AS candidate_count,
                COUNT(DISTINCT native_evidence_id) AS native_target_count,
                GROUP_CONCAT(DISTINCT native_evidence_id) AS native_evidence_ids
            FROM blueprint_native_edges
            GROUP BY
                blueprint_asset_path, blueprint_function_name, status,
                resolution_method, confidence
            ORDER BY
                CASE status
                  WHEN 'CONFIRMED' THEN 0
                  WHEN 'VERIFIED' THEN 0
                  WHEN 'RESOLVED' THEN 0
                  WHEN 'AMBIGUOUS' THEN 1
                  WHEN 'NAME_ONLY_CANDIDATE' THEN 2
                  ELSE 3
                END,
                candidate_count DESC,
                blueprint_asset_path,
                blueprint_function_name
            LIMIT ?
            """,
            (limit,),
        ),
        "current_provisional_tiers.csv": (
            """
            WITH ranked AS (
                SELECT
                    object_path,
                    asset_name,
                    asset_class_path,
                    parent_class_path,
                    native_parent_class_path,
                    provisional_tier,
                    provisional_reasons_json,
                    descendant_count,
                    referencer_count,
                    component_reuse_count,
                    cross_domain_reference_count,
                    registry_usage_count,
                    native_call_count,
                    query_hit_count,
                    existing_report_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY provisional_tier
                        ORDER BY
                            registry_usage_count DESC,
                            descendant_count DESC,
                            component_reuse_count DESC,
                            cross_domain_reference_count DESC,
                            referencer_count DESC,
                            native_call_count DESC,
                            object_path
                    ) AS tier_sample_rank
                FROM assets
            )
            SELECT
                *,
                'top structural and demand signals within provisional tier'
                    AS sample_method
            FROM ranked
            WHERE tier_sample_rank <= ?
            ORDER BY provisional_tier, tier_sample_rank
            """,
            (limit,),
        ),
        "class_identity_coverage.csv": (
            """
            SELECT
                asset_class_path,
                COUNT(*) AS asset_count,
                SUM(CASE WHEN is_blueprint=1 THEN 1 ELSE 0 END)
                    AS blueprint_count,
                SUM(CASE
                    WHEN generated_class_path NOT IN ('', 'UNKNOWN')
                    THEN 1 ELSE 0 END
                ) AS generated_class_known,
                SUM(CASE
                    WHEN parent_class_path NOT IN ('', 'UNKNOWN')
                    THEN 1 ELSE 0 END
                ) AS parent_class_known,
                SUM(CASE
                    WHEN native_parent_class_path NOT IN ('', 'UNKNOWN')
                    THEN 1 ELSE 0 END
                ) AS native_parent_class_known,
                SUM(CASE WHEN identity_status='CONFIRMED' THEN 1 ELSE 0 END)
                    AS confirmed_identity_count,
                SUM(CASE
                    WHEN identity_status IN (
                        'UNKNOWN', 'AMBIGUOUS', 'NOT_RECOVERED',
                        'NOT_MEASURED', 'SOURCE_NOT_AVAILABLE', 'STALE'
                    )
                    THEN 1 ELSE 0 END
                ) AS open_identity_count
            FROM assets
            GROUP BY asset_class_path
            ORDER BY asset_count DESC, asset_class_path
            LIMIT ?
            """,
            (limit,),
        ),
        "system_registration_summary.csv": (
            """
            SELECT
                registration_type,
                COUNT(*) AS registration_count,
                COUNT(DISTINCT owner_object_path) AS owner_count,
                COUNT(DISTINCT target_object_path) AS target_count,
                SUM(CASE
                    WHEN confidence IN ('CONFIRMED', 'HIGH')
                    THEN 1 ELSE 0 END
                ) AS high_confidence_count,
                SUM(CASE
                    WHEN confidence NOT IN ('CONFIRMED', 'HIGH')
                    THEN 1 ELSE 0 END
                ) AS non_high_confidence_count
            FROM system_registrations
            GROUP BY registration_type
            ORDER BY registration_count DESC, registration_type
            LIMIT ?
            """,
            (limit,),
        ),
        "existing_kb_coverage.csv": (
            """
            SELECT *
            FROM existing_knowledge_tables
            ORDER BY database_name, table_name
            LIMIT ?
            """,
            (limit,),
        ),
        "stale_and_high_gap_assets.csv": (
            """
            SELECT
                c.object_path,
                a.asset_name,
                a.asset_class_path,
                a.evidence_freshness,
                c.stage,
                c.status,
                c.ambiguous_count,
                c.not_recovered_count,
                c.source_not_available_count,
                c.stale_count,
                c.failure_reason,
                (
                    c.ambiguous_count
                    + c.not_recovered_count
                    + c.source_not_available_count
                    + c.stale_count
                ) AS gap_count
            FROM coverage AS c
            LEFT JOIN assets AS a ON a.object_path=c.object_path
            WHERE c.status IN (
                    'UNKNOWN', 'AMBIGUOUS', 'NOT_RECOVERED',
                    'NOT_MEASURED', 'SOURCE_NOT_AVAILABLE', 'STALE'
                  )
               OR c.ambiguous_count > 0
               OR c.not_recovered_count > 0
               OR c.source_not_available_count > 0
               OR c.stale_count > 0
            ORDER BY
                gap_count DESC,
                CASE c.status
                  WHEN 'STALE' THEN 0
                  WHEN 'NOT_RECOVERED' THEN 1
                  WHEN 'SOURCE_NOT_AVAILABLE' THEN 2
                  WHEN 'AMBIGUOUS' THEN 3
                  ELSE 4
                END,
                c.object_path,
                c.stage
            LIMIT ?
            """,
            (limit,),
        ),
        "data_asset_classification_candidates.csv": (
            """
            SELECT
                object_path,
                asset_name,
                asset_class_path,
                generated_class_path,
                parent_class_path,
                native_parent_class_path,
                is_data_asset,
                identity_status,
                identity_confidence,
                CASE
                  WHEN is_data_asset=1 THEN 'registry_exact_flag'
                  WHEN lower(asset_class_path) LIKE '%dataasset%'
                    THEN 'asset_class_name_candidate'
                  WHEN lower(parent_class_path) LIKE '%dataasset%'
                    THEN 'parent_class_name_candidate'
                  WHEN lower(native_parent_class_path) LIKE '%dataasset%'
                    THEN 'native_parent_name_candidate'
                  ELSE 'not_classified'
                END AS current_classification_basis
            FROM assets
            WHERE is_data_asset=1
               OR lower(asset_class_path) LIKE '%dataasset%'
               OR lower(parent_class_path) LIKE '%dataasset%'
               OR lower(native_parent_class_path) LIKE '%dataasset%'
            ORDER BY
                CASE WHEN is_data_asset=1 THEN 0 ELSE 1 END,
                object_path
            LIMIT ?
            """,
            (limit,),
        ),
    }


def _report_markdown(
    *,
    counts: dict[str, int],
    metadata: dict[str, str],
    database_sha256: str,
    source_commit: str,
    row_counts: dict[str, int],
) -> str:
    return "\n".join(
        [
            "# ARK Knowledge Discovery bounded review",
            "",
            "This report is generated from the final Discovery SQLite by the "
            "queries documented in `README.md`.",
            "",
            "## Source",
            "",
            f"- Discovery schema: `{metadata.get('schema', 'UNKNOWN')}`",
            f"- Discovery generated at: `{metadata.get('generated_at_utc', 'UNKNOWN')}`",
            f"- Discovery SQLite SHA-256: `{database_sha256}`",
            f"- Source commit: `{source_commit}`",
            "",
            "## Database scale",
            "",
            *[f"- `{name}`: {count:,}" for name, count in sorted(counts.items())],
            "",
            "## Review exports",
            "",
            *[f"- `{name}`: {count:,} rows" for name, count in sorted(row_counts.items())],
            "",
            "## Evidence boundary",
            "",
            "- `provisional_tier` is preserved only for review of the old "
            "classifier; it is not a production role.",
            "- Data Asset rows are candidates until class ancestry closes.",
            "- UNKNOWN, AMBIGUOUS, NOT_RECOVERED, NOT_MEASURED, "
            "SOURCE_NOT_AVAILABLE, and STALE remain explicit.",
            "- No ARK package, binary, PDB, Ghidra project, decompiled body, or "
            "local absolute path is included.",
            "",
        ]
    )


def _readme(
    *,
    database_sha256: str,
    database_size: int,
    source_commit: str,
    metadata: dict[str, str],
    sql_catalog: dict[str, tuple[str, tuple[object, ...]]],
    row_counts: dict[str, int],
    total_size: int,
) -> str:
    lines = [
        "# Discovery review subset",
        "",
        "This directory is a bounded, Git-readable SQL export of the final "
        "Discovery database. Rebuild it; do not edit generated rows by hand.",
        "",
        "## Rebuild",
        "",
        "```powershell",
        "runtime\\python\\python.exe scripts\\export_kb_discovery_review_subset.py `",
        "  --database knowledge_base\\discovery_bundle\\kb_discovery.sqlite `",
        "  --output docs\\discovery_review",
        "```",
        "",
        "## Source identity",
        "",
        f"- Discovery SQLite SHA-256: `{database_sha256}`",
        f"- Discovery SQLite size: `{database_size}` bytes",
        f"- Discovery schema: `{metadata.get('schema', 'UNKNOWN')}`",
        f"- Discovery generated at: `{metadata.get('generated_at_utc', 'UNKNOWN')}`",
        f"- Source commit: `{source_commit}`",
        f"- Review subset size: `{total_size}` bytes",
        "",
        "## Bounds",
        "",
        f"- Top exports are capped at {MAX_REVIEW_ROWS} rows.",
        "- `current_provisional_tiers.csv` is capped independently per tier.",
        "- The old tier is published only to expose classifier defects.",
        "- Object paths are ARK logical identities, not local filesystem paths.",
        "",
        "## Export row counts",
        "",
        "| File | Rows |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{name}` | {count} |" for name, count in sorted(row_counts.items())
    )
    lines.extend(["", "## SQL", ""])
    for name, (sql, parameters) in sql_catalog.items():
        clean_sql = "\n".join(
            line.rstrip() for line in sql.strip().splitlines()
        )
        lines.extend(
            [
                f"### `{name}`",
                "",
                f"Parameters: `{list(parameters)}`",
                "",
                "```sql",
                clean_sql,
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _assert_bounded_and_private(output_dir: Path, *, limit: int) -> None:
    for csv_path in output_dir.glob("*.csv"):
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            row_count = sum(1 for _ in csv.reader(handle)) - 1
        per_tier = csv_path.name == "current_provisional_tiers.csv"
        maximum = limit * 16 if per_tier else limit
        if row_count > maximum:
            raise ValueError(
                f"{csv_path.name} has {row_count} rows; maximum is {maximum}."
            )
    for path in output_dir.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.casefold()
        if "decompiled_c" in lowered:
            raise ValueError(f"{path.name} contains forbidden decompiler content.")
        if any(marker in lowered for marker in ("c:\\users\\", "/users/", "/home/")):
            raise ValueError(f"{path.name} contains a local absolute path.")
    if _directory_size(output_dir) > 20 * 1024 * 1024:
        raise ValueError("Review subset exceeds the 20 MiB publication target.")


def export_review_subset(
    *,
    database_path: Path,
    output_dir: Path,
    project_root: Path,
    limit: int = MAX_REVIEW_ROWS,
    source_commit: str | None = None,
) -> dict[str, object]:
    """Export the final Discovery DB to a small reproducible review directory."""

    database_path = database_path.resolve()
    output_dir = output_dir.resolve()
    project_root = project_root.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if not 1 <= limit <= MAX_REVIEW_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_REVIEW_ROWS}")

    database_sha256 = _sha256_file(database_path)
    source_commit = source_commit or _git_commit(project_root)
    sql_catalog = _sql_catalog(limit)
    staging_parent = output_dir.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = Path(
        tempfile.mkdtemp(prefix=".discovery_review.", dir=staging_parent)
    )
    try:
        connection = _open_read_only(database_path)
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise ValueError(f"Discovery SQLite integrity check failed: {integrity}")
            metadata = {
                str(row["key"]): str(row["value"])
                for row in connection.execute(
                    "SELECT key, value FROM metadata ORDER BY key"
                )
            }
            table_names = [
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            ]
            counts = {
                name: _table_count(connection, name) for name in table_names
            }
            _write_text(staging / "kb_discovery_schema.sql", _schema_sql(connection))

            row_counts: dict[str, int] = {}
            for filename, (sql, parameters) in sql_catalog.items():
                rows, fieldnames = _query_rows(connection, sql, parameters)
                row_counts[filename] = len(rows)
                target = staging / filename
                if filename.endswith(".csv"):
                    _write_csv(target, rows, fieldnames=fieldnames)
                elif filename.endswith(".jsonl"):
                    _write_jsonl(target, rows)
                elif filename == "representative_sample_manifest.json":
                    _write_json(
                        target,
                        {
                            "schema": REVIEW_SCHEMA,
                            "databaseSha256": database_sha256,
                            "samples": rows,
                        },
                    )
                else:
                    raise AssertionError(filename)
        finally:
            connection.close()

        manifest = {
            "schema": REVIEW_SCHEMA,
            "source": {
                "databaseSha256": database_sha256,
                "databaseSizeBytes": database_path.stat().st_size,
                "sourceCommit": source_commit,
                "discoverySchema": metadata.get("schema", "UNKNOWN"),
                "discoveryGeneratedAt": metadata.get(
                    "generated_at_utc", "UNKNOWN"
                ),
            },
            "bounds": {
                "topRowsPerFile": limit,
                "provisionalRowsPerTier": limit,
                "targetBytes": 20 * 1024 * 1024,
            },
            "tableCounts": counts,
            "exportRowCounts": row_counts,
            "sqlFiles": sorted(sql_catalog),
        }
        _write_json(staging / "discovery_manifest.json", manifest)
        _write_text(
            staging / "discovery_report.md",
            _report_markdown(
                counts=counts,
                metadata=metadata,
                database_sha256=database_sha256,
                source_commit=source_commit,
                row_counts=row_counts,
            ),
        )
        preliminary_size = _directory_size(staging)
        _write_text(
            staging / "README.md",
            _readme(
                database_sha256=database_sha256,
                database_size=database_path.stat().st_size,
                source_commit=source_commit,
                metadata=metadata,
                sql_catalog=sql_catalog,
                row_counts=row_counts,
                total_size=preliminary_size,
            ),
        )
        _assert_bounded_and_private(staging, limit=limit)

        if output_dir.exists():
            marker = output_dir / "discovery_manifest.json"
            if not marker.is_file():
                raise ValueError(
                    f"Refusing to replace non-generated directory: {output_dir}"
                )
            existing = json.loads(marker.read_text(encoding="utf-8"))
            if existing.get("schema") != REVIEW_SCHEMA:
                raise ValueError(
                    f"Refusing to replace directory with unknown schema: {output_dir}"
                )
            shutil.rmtree(output_dir)
        os.replace(staging, output_dir)
        staging = None
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)

    return {
        "status": "complete",
        "schema": REVIEW_SCHEMA,
        "databaseSha256": database_sha256,
        "sourceCommit": source_commit,
        "output": str(output_dir),
        "files": sum(1 for path in output_dir.rglob("*") if path.is_file()),
        "bytes": _directory_size(output_dir),
        "rowCounts": row_counts,
    }
