"""Repository boundary for indexed and legacy Blueprint evidence."""

from __future__ import annotations

import tempfile
import json
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .evidence_query import EvidenceQueryService
from .evidence_values import project_default_value
from .evidence_writer import write_evidence_store_from_capture


class EvidenceRepository:
    """Own a read-only query service and any temporary legacy projection."""

    def __init__(
        self,
        service: EvidenceQueryService,
        *,
        source_kind: str,
        temporary: tempfile.TemporaryDirectory[str] | None = None,
    ) -> None:
        self._service = service
        self._temporary = temporary
        self.source_kind = source_kind
        self.database_path = service.database_path
        self.asset_id = service.asset_id
        self.revision_id = service.revision_id
        self._closed = False

    def query(self, request: Mapping[str, object]) -> dict[str, object]:
        if self._closed:
            raise RuntimeError("EvidenceRepository is closed")
        return self._service.query(request)

    def identity(self) -> dict[str, object]:
        row = self._service._connection.execute(  # noqa: SLF001 - repository owns the service
            "SELECT asset_id, asset_name, object_path, revision_id, source_fingerprint, uasset_path "
            "FROM asset_revisions LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("evidence database has no asset revision")
        return {key: row[key] for key in row.keys()}

    def graph_summaries(self) -> list[dict[str, object]]:
        rows = self._service._connection.execute(  # noqa: SLF001
            "SELECT graph_ref, export_index, name, graph_type, status, confidence, node_count, pin_count, "
            "link_observation_count, coverage_json FROM graphs ORDER BY export_index"
        ).fetchall()
        return [
            {
                "ref": str(row["graph_ref"]),
                "export_index": int(row["export_index"]),
                "name": str(row["name"]),
                "graph_type": str(row["graph_type"]),
                "status": str(row["status"]),
                "confidence": str(row["confidence"]),
                "node_count": int(row["node_count"]),
                "pin_count": int(row["pin_count"]),
                "link_count": int(row["link_observation_count"]),
                "coverage": json.loads(str(row["coverage_json"] or "{}")),
            }
            for row in rows
        ]

    def node_summaries(self) -> list[dict[str, object]]:
        rows = self._service._connection.execute(  # noqa: SLF001
            "SELECT node_ref, graph_ref, name, label, class_name, node_type, function_name, variable_name, "
            "event_name, x, y, confidence FROM nodes ORDER BY graph_ref, local_index"
        ).fetchall()
        return [
            {
                "ref": str(row["node_ref"]),
                "graph_ref": str(row["graph_ref"]),
                "name": str(row["name"]),
                "label": str(row["label"]),
                "class_name": str(row["class_name"]),
                "node_type": str(row["node_type"]),
                "function": str(row["function_name"]),
                "variable": str(row["variable_name"]),
                "event": str(row["event_name"]),
                "x": row["x"],
                "y": row["y"],
                "confidence": str(row["confidence"]),
            }
            for row in rows
        ]

    @staticmethod
    def _decode_value(row: Any) -> object:
        codec = str(row["value_codec"] or "json")
        if codec == "json":
            return json.loads(str(row["value_json"]))
        if codec == "zlib-json-utf8":
            return json.loads(zlib.decompress(bytes(row["value_blob"])).decode("utf-8"))
        raise ValueError(f"unsupported evidence value codec: {codec}")

    def default_summaries(self, *, include_values: bool = True) -> list[dict[str, object]]:
        rows = self._service._connection.execute(  # noqa: SLF001
            "SELECT default_ref, name, type_name, value_json, value_codec, value_blob, confidence, source, extra_json "
            "FROM class_defaults ORDER BY name"
        ).fetchall()
        summaries: list[dict[str, object]] = []
        for row in rows:
            value_loaded = include_values or str(row["value_codec"] or "json") == "json"
            if include_values:
                value = self._decode_value(row)
            elif value_loaded:
                try:
                    value = json.loads(str(row["value_json"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    value = None
            else:
                value = None
            try:
                extra = json.loads(str(row["extra_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                extra = {}
            summaries.append(
                {
                    "ref": str(row["default_ref"]),
                    "name": str(row["name"]),
                    "type": str(row["type_name"]),
                    "confidence": str(row["confidence"]),
                    "source": str(row["source"]),
                    **project_default_value(
                        str(row["type_name"]),
                        value,
                        extra,
                        value_loaded=value_loaded,
                    ),
                    **({"value": value} if include_values else {}),
                }
            )
        return summaries

    @staticmethod
    def _gap_summary_row(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "ref": str(row.get("ref") or ""),
            "scope_kind": str(row.get("scopeKind") or ""),
            "scope_ref": str(row.get("scopeRef") or row.get("graphRef") or ""),
            "name": str(row.get("name") or ""),
            "status": str(row.get("status") or ""),
            "reason_code": str(row.get("reasonCode") or ""),
            "detail": str(row.get("detail") or row.get("title") or ""),
            "next_probe": str(row.get("nextProbe") or ""),
            "kind": str(row.get("kind") or "diagnostic"),
        }

    def _all_gap_summary_rows(self) -> list[dict[str, object]]:
        """Materialize each gap once for accurate aggregate coverage.

        Query pagination intentionally limits response size, but repeatedly
        calling the public gaps query would rebuild the full gap set for every
        page.  The repository owns the query service, so it uses the same item
        projectors directly and aggregates before applying its downstream cap.
        """

        diagnostic_rows = self._service._connection.execute(  # noqa: SLF001
            "SELECT * FROM diagnostics WHERE revision_id = ?",
            (self.revision_id,),
        ).fetchall()
        raw_rows: list[Mapping[str, object]] = [
            self._service._diagnostic_item(row)  # noqa: SLF001
            for row in diagnostic_rows
        ]
        raw_rows.extend(self._service._observation_gap_items())  # noqa: SLF001
        raw_rows.extend(self._service._default_value_gap_items())  # noqa: SLF001
        summaries = [self._gap_summary_row(row) for row in raw_rows]
        summaries.sort(
            key=lambda row: (
                str(row.get("status") or ""),
                str(row.get("reason_code") or ""),
                str(row.get("ref") or ""),
            )
        )
        return summaries

    def gap_summary(
        self,
        *,
        limit: int = 200,
        example_limit: int = 3,
    ) -> dict[str, object]:
        """Return bounded rows plus loss-aware aggregates for every gap."""

        bounded_limit = max(0, int(limit))
        bounded_example_limit = max(0, int(example_limit))
        summaries = self._all_gap_summary_rows()
        returned_rows = summaries[:bounded_limit]
        by_status: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        groups: dict[tuple[str, str], dict[str, object]] = {}
        for row in summaries:
            status = str(row.get("status") or "")
            reason = str(row.get("reason_code") or "")
            by_status[status] = by_status.get(status, 0) + 1
            by_reason[reason] = by_reason.get(reason, 0) + 1
            key = (status, reason)
            group = groups.setdefault(
                key,
                {
                    "status": status,
                    "reason_code": reason,
                    "count": 0,
                    "examples": [],
                },
            )
            group["count"] = int(group["count"]) + 1
            examples = group["examples"]
            if isinstance(examples, list) and len(examples) < bounded_example_limit:
                examples.append(dict(row))

        total = len(summaries)
        returned = len(returned_rows)
        omitted = max(0, total - returned)
        return {
            "items": returned_rows,
            "total": total,
            "returned": returned,
            "omitted": omitted,
            "truncated": omitted > 0,
            "by_status": dict(sorted(by_status.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "groups": [groups[key] for key in sorted(groups)],
        }

    def gap_summaries(self, *, limit: int = 200) -> list[dict[str, object]]:
        """Compatibility view of bounded gap rows; use ``gap_summary`` for coverage."""

        if limit <= 0:
            return []
        projection = self.gap_summary(limit=limit)
        items = projection.get("items")
        return items if isinstance(items, list) else []

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._service.close()
        finally:
            if self._temporary is not None:
                self._temporary.cleanup()
            self._closed = True

    def __enter__(self) -> "EvidenceRepository":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def open_asset_repository(asset_dir: str | Path) -> EvidenceRepository:
    """Open v2 evidence, or make a read-only temporary v2 view of legacy data.

    The fallback never writes into ``asset_dir``.  This keeps old captures
    usable while internal consumers migrate to the repository boundary.
    """

    root = Path(asset_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"NO_EVIDENCE: asset directory not found: {root}")
    indexed_database = root / "evidence" / "evidence.sqlite"
    if indexed_database.is_file():
        return EvidenceRepository(
            EvidenceQueryService.open(indexed_database),
            source_kind="indexed",
        )

    legacy_manifest = root / "graphs_from_uasset_manifest.json"
    if not legacy_manifest.is_file():
        raise FileNotFoundError(f"NO_EVIDENCE: evidence not found under {root}")
    temporary = tempfile.TemporaryDirectory(prefix="blueprint-evidence-legacy-")
    try:
        database_path = Path(temporary.name) / "evidence.sqlite"
        write_evidence_store_from_capture(root, database_path)
        service = EvidenceQueryService.open(database_path)
    except Exception:
        temporary.cleanup()
        raise
    return EvidenceRepository(service, source_kind="legacy-fallback", temporary=temporary)


__all__ = ["EvidenceRepository", "open_asset_repository"]
