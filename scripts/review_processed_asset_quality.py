from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from blueprint_translator.asset_ledger import read_ledger_snapshot  # noqa: E402
from blueprint_translator.evidence_repository import open_asset_repository  # noqa: E402
from read_priority_assets import (  # noqa: E402
    CATALOG_DB,
    CAPTURE_ROOT,
    LEGACY_LEDGER_DB,
    analyze_asset,
    asset_name_from_object_path,
    build_quality_payload,
    load_graph_summary,
    repository_graph_status_counts,
    write_quality_report,
)


OUT_JSON = PROJECT_ROOT / "knowledge_base" / "priorities" / "processed_assets_quality_report.json"
OUT_MD = PROJECT_ROOT / "knowledge_base" / "priorities" / "processed_assets_quality_report.md"


def ledger_db_path() -> Path:
    return CATALOG_DB if CATALOG_DB.is_file() else LEGACY_LEDGER_DB


def report_files_exist(asset_dir: Path) -> bool:
    if (asset_dir / "evidence" / "evidence.sqlite").is_file() and (asset_dir / "output" / "agent_index.md").is_file():
        return True
    output_dir = asset_dir / "output"
    return all(
        (output_dir / name).is_file()
        for name in (
            "behavior_summary.md",
            "diagnostics_report.md",
            "capture_quality_report.md",
            "asset_report.md",
        )
    )


def row_to_result(row: dict[str, Any], *, analyze_missing: bool, analyze_all: bool, report_level: str) -> dict[str, Any]:
    object_path = str(row.get("object_path") or "")
    asset_name = str(row.get("asset_name") or asset_name_from_object_path(object_path))
    asset_dir = Path(str(row.get("capture_dir") or "")) if row.get("capture_dir") else CAPTURE_ROOT / asset_name
    summary = load_graph_summary(asset_dir / "uasset_graph_nodes.json")
    if (asset_dir / "evidence" / "evidence.sqlite").is_file():
        with open_asset_repository(asset_dir) as repository:
            overview = repository.query({"operation": "overview", "budgetTokens": 800})
            status_counts = repository_graph_status_counts(repository)
        indexed = overview.get("summary", {})
        summary = {
            "graph_count": int(indexed.get("graphCount") or 0),
            "node_count": int(indexed.get("nodeCount") or 0),
            "pin_count": int(indexed.get("pinCount") or 0),
            "link_count": int(indexed.get("linkObservationCount") or 0),
            "status_counts": status_counts,
        }
    result: dict[str, Any] = {
        "asset_path": object_path,
        "asset_name": asset_name,
        "status": row.get("read_status") or "read",
        "asset_dir": str(asset_dir),
        "uasset_path": row.get("uasset_path") or "",
        "graph_count": summary.get("graph_count", int(row.get("graph_count") or 0)),
        "node_count": summary.get("node_count", int(row.get("node_count") or 0)),
        "pin_count": summary.get("pin_count", int(row.get("pin_count") or 0)),
        "link_count": summary.get("link_count", int(row.get("link_count") or 0)),
        "status_counts": summary.get("status_counts") or {},
    }
    should_analyze = analyze_all or (analyze_missing and not report_files_exist(asset_dir))
    if should_analyze and asset_dir.is_dir():
        try:
            result["analysis"] = analyze_asset(asset_dir, report_level)
        except Exception as exc:
            result["analysis"] = {"error": str(exc)}
    return result


def select_processed_rows(snapshot: dict[str, Any], assets: list[str], limit: int) -> list[dict[str, Any]]:
    rows = list((snapshot.get("processed") or {}).values())
    if assets:
        wanted = {asset.lower() for asset in assets}
        rows = [
            row
            for row in rows
            if str(row.get("asset_name") or "").lower() in wanted
            or str(row.get("object_path") or "").lower() in wanted
        ]
    rows.sort(key=lambda row: (str(row.get("asset_type") or ""), str(row.get("asset_name") or "").lower()))
    if limit > 0:
        rows = rows[:limit]
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review generated report quality for assets already marked as read.")
    parser.add_argument("--asset", action="append", default=[], help="Restrict review to an asset name or object path. Can be used multiple times.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum processed assets to review. 0 means all.")
    parser.add_argument("--analyze-missing", action="store_true", help="Generate standard reports for captures that do not have them yet.")
    parser.add_argument("--analyze-all", action="store_true", help="Regenerate standard reports for every reviewed capture.")
    parser.add_argument("--report-level", default="standard", choices=["compact", "standard", "debug"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = read_ledger_snapshot(ledger_db_path())
    rows = select_processed_rows(snapshot, [str(item) for item in args.asset], args.limit)
    results = [
        row_to_result(
            row,
            analyze_missing=bool(args.analyze_missing),
            analyze_all=bool(args.analyze_all),
            report_level=str(args.report_level),
        )
        for row in rows
    ]
    payload = build_quality_payload(results)
    payload["source"] = {
        "ledger": str(ledger_db_path()),
        "processed_assets_seen": len((snapshot.get("processed") or {})),
        "reviewed_assets": len(rows),
        "analyze_missing": bool(args.analyze_missing),
        "analyze_all": bool(args.analyze_all),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_quality_report(OUT_MD, payload, title="已读取资产质量巡检")
    print(f"reviewed {len(rows)} processed assets")
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
