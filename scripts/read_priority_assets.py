from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from blueprint_translator.uasset_graphs import (
    object_path_to_uasset_path,
    read_uasset_graph_content,
    write_uasset_graph_read_files,
)
from blueprint_translator.asset_ledger import (
    processed_current_for_path,
    record_asset_results,
)
from blueprint_translator.utils import safe_filename
CAPTURE_ROOT = PROJECT_ROOT / "captures"
DEFAULT_QUEUE = PROJECT_ROOT / "knowledge_base" / "priorities" / "deep_read_queue.txt"
CATALOG_DB = PROJECT_ROOT / "knowledge_base" / "db" / "asset_catalog.sqlite"
LEGACY_LEDGER_DB = PROJECT_ROOT / "knowledge_base" / "global" / "asset_index.sqlite"


def ledger_db_path() -> Path:
    return CATALOG_DB if CATALOG_DB.is_file() else LEGACY_LEDGER_DB


def record_results(results: list[dict[str, Any]], *, knowledge_status: str) -> None:
    primary = ledger_db_path()
    record_asset_results(primary, results, knowledge_status=knowledge_status)
    if CATALOG_DB.is_file() and LEGACY_LEDGER_DB.is_file() and primary.resolve() != LEGACY_LEDGER_DB.resolve():
        record_asset_results(LEGACY_LEDGER_DB, results, knowledge_status=knowledge_status)


def read_queue(path: Path) -> list[str]:
    if not path.is_file():
        return []
    items: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if value not in seen:
            seen.add(value)
            items.append(value)
    return items


def asset_name_from_object_path(object_path: str) -> str:
    tail = object_path.rsplit(".", 1)[-1] if "." in object_path else object_path.rsplit("/", 1)[-1]
    return tail.removesuffix("_C")


def load_graph_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        "graph_count": int(payload.get("graph_count") or 0),
        "node_count": int(payload.get("node_count") or 0),
        "pin_count": int(payload.get("pin_count") or 0),
        "link_count": int(payload.get("link_count") or 0),
        "status_counts": payload.get("status_counts") or {},
    }


def existing_capture_result(object_path: str, asset_name: str, asset_dir: Path, uasset_path: Path) -> dict[str, Any]:
    summary = load_graph_summary(asset_dir / "uasset_graph_nodes.json")
    return {
        "asset_path": object_path,
        "asset_name": asset_name,
        "status": "skipped_existing",
        "asset_dir": str(asset_dir),
        "uasset_path": str(uasset_path),
        "graph_count": summary.get("graph_count", 0),
        "node_count": summary.get("node_count", 0),
        "pin_count": summary.get("pin_count", 0),
        "link_count": summary.get("link_count", 0),
        "status_counts": summary.get("status_counts", {}),
        "duration_seconds": 0,
    }


def select_queue_items(queue: list[str], *, limit: int, force: bool) -> tuple[list[str], list[dict[str, Any]]]:
    selected: list[str] = []
    skipped_current: list[dict[str, Any]] = []
    if limit < 0:
        return selected, skipped_current
    unlimited = limit == 0
    for object_path in queue:
        if not force:
            uasset_path, _attempted = object_path_to_uasset_path(object_path)
            if uasset_path is not None and processed_current_for_path(ledger_db_path(), object_path, uasset_path):
                skipped_current.append(
                    {
                        "asset_path": object_path,
                        "asset_name": asset_name_from_object_path(object_path),
                        "status": "skipped_processed",
                        "uasset_path": str(uasset_path),
                    }
                )
                continue
        selected.append(object_path)
        if not unlimited and len(selected) >= limit:
            break
    return selected, skipped_current


def analyze_asset(asset_dir: Path, report_level: str) -> dict[str, Any]:
    output_dir = asset_dir / "output"
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "bp_clipboard_to_prompt.py"),
        "--asset-dir",
        str(asset_dir),
        "--output-dir",
        str(output_dir),
        "--report-level",
        report_level,
        "--keep-stale-output",
    ]
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    return {
        "command": " ".join(command),
        "return_code": completed.returncode,
        "stdout_tail": completed.stdout[-3000:],
        "stderr_tail": completed.stderr[-3000:],
    }


def read_asset(object_path: str, *, max_graphs: int, analyze: bool, report_level: str, force: bool) -> dict[str, Any]:
    asset_name = asset_name_from_object_path(object_path)
    asset_dir = CAPTURE_ROOT / safe_filename(asset_name, "BlueprintAsset")
    uasset_path, attempted = object_path_to_uasset_path(object_path)
    if uasset_path is None:
        return {
            "asset_path": object_path,
            "asset_name": asset_name,
            "status": "missing_uasset",
            "attempted": attempted,
        }

    if not force and processed_current_for_path(ledger_db_path(), object_path, uasset_path):
        return {
            "asset_path": object_path,
            "asset_name": asset_name,
            "status": "skipped_processed",
            "asset_dir": str(asset_dir),
            "uasset_path": str(uasset_path),
        }

    existing = asset_dir / "uasset_graph_nodes.json"
    if existing.is_file() and not force:
        return existing_capture_result(object_path, asset_name, asset_dir, uasset_path)

    started = time.time()
    payload = read_uasset_graph_content(object_path, uasset_path, max_graphs=max_graphs)
    paths = write_uasset_graph_read_files(object_path, CAPTURE_ROOT, payload)
    result: dict[str, Any] = {
        "asset_path": object_path,
        "asset_name": payload.get("asset_name") or asset_name,
        "status": "read" if payload.get("loaded", True) else "read_failed",
        "asset_dir": paths.get("asset_dir", str(asset_dir)),
        "uasset_path": str(uasset_path),
        "graph_count": payload.get("graph_count", 0),
        "node_count": payload.get("node_count", 0),
        "pin_count": payload.get("pin_count", 0),
        "link_count": payload.get("link_count", 0),
        "status_counts": payload.get("status_counts", {}),
        "duration_seconds": round(time.time() - started, 2),
    }
    if analyze:
        try:
            result["analysis"] = analyze_asset(Path(paths["asset_dir"]), report_level)
        except Exception as exc:
            result["analysis"] = {"error": str(exc)}
    return result


def write_batch_report(out_path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# 自动解析重点资产结果",
        "",
        f"- 队列：`{payload.get('queue')}`",
        f"- 队列资产数：{payload.get('planned_count', 0)}",
        f"- 本轮选择：{payload.get('selected_count', len(payload.get('results', [])))}",
        f"- 本轮实际处理：{len(payload.get('results', []))}",
        f"- 跳过已入库且未变化：{payload.get('skipped_current_count', 0)}",
        "",
        "| 资产 | 状态 | 图页 | 节点 | Pin | 连线 | 图页状态 | 用时 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for item in payload.get("results", []):
        status_counts = item.get("status_counts") or {}
        status_label = ", ".join(f"{key}:{value}" for key, value in status_counts.items()) if status_counts else "-"
        lines.append(
            "| `{}` | `{}` | {} | {} | {} | {} | {} | {} |".format(
                item.get("asset_name") or item.get("asset_path"),
                item.get("status"),
                item.get("graph_count", 0),
                item.get("node_count", 0),
                item.get("pin_count", 0),
                item.get("link_count", 0),
                status_label,
                item.get("duration_seconds", 0),
            )
        )
    failures = [item for item in payload.get("results", []) if item.get("status") not in {"read", "skipped_existing", "skipped_processed"}]
    if failures:
        lines.extend(["", "## 失败或未解析", ""])
        for item in failures:
            lines.append(f"- `{item.get('asset_path')}`：{item.get('status')}")
    skipped_current = payload.get("skipped_current") or []
    if skipped_current:
        lines.extend(["", "## 本轮自动跳过", ""])
        lines.append("这些资产已经读过并且文件指纹没有变化，所以没有占用本轮小批量名额。")
        lines.append("")
        for item in skipped_current[:25]:
            lines.append(f"- `{item.get('asset_name') or item.get('asset_path')}`")
        if len(skipped_current) > 25:
            lines.append(f"- ... 另外 {len(skipped_current) - 25} 个")
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automatically read priority ARK DevKit assets from a knowledge-base queue.")
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--limit", type=int, default=25, help="Maximum assets to process. Use 0 for all.")
    parser.add_argument("--max-graphs", type=int, default=0, help="Maximum graphs per asset. 0 means all.")
    parser.add_argument("--force", action="store_true", help="Re-read assets even if captures already exist.")
    parser.add_argument("--no-analyze", action="store_true", help="Skip report generation after binary read.")
    parser.add_argument("--report-level", default="standard", choices=["compact", "standard", "debug"])
    parser.add_argument("--rebuild-knowledge", action="store_true", help="Rebuild the knowledge base after reading.")
    parser.add_argument("--include-current", action="store_true", help="Let already processed current assets consume the batch limit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue = read_queue(args.queue)
    if args.include_current:
        selected = queue if args.limit == 0 else queue[: max(args.limit, 0)]
        skipped_current: list[dict[str, Any]] = []
    else:
        selected, skipped_current = select_queue_items(queue, limit=args.limit, force=args.force)
    results = []
    for idx, object_path in enumerate(selected, start=1):
        print(f"[{idx}/{len(selected)}] reading {object_path}", flush=True)
        result = read_asset(
            object_path,
            max_graphs=args.max_graphs,
            analyze=not args.no_analyze,
            report_level=args.report_level,
            force=args.force,
        )
        results.append(result)
        print(f"  -> {result.get('status')} graphs={result.get('graph_count', 0)} nodes={result.get('node_count', 0)}", flush=True)

    payload = {
        "schema": "ark-devkit-knowledge.priority-read-results.v1",
        "queue": str(args.queue),
        "planned_count": len(queue),
        "processed_limit": args.limit,
        "selected_count": len(selected),
        "skipped_current_count": len(skipped_current),
        "skipped_current": skipped_current,
        "results": results,
    }
    out_json = PROJECT_ROOT / "knowledge_base" / "priorities" / "priority_read_results.json"
    out_md = PROJECT_ROOT / "knowledge_base" / "priorities" / "priority_read_results.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_batch_report(out_md, payload)

    if args.rebuild_knowledge:
        record_results(results, knowledge_status="captured")
        command = [sys.executable, str(PROJECT_ROOT / "scripts" / "build_ark_knowledge_base.py")]
        print("rebuilding knowledge base", flush=True)
        completed = subprocess.run(command, cwd=str(PROJECT_ROOT), text=True, encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            return completed.returncode
        record_results(results, knowledge_status="imported")
    else:
        record_results(results, knowledge_status="captured")

    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
