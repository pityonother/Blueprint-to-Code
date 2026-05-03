"""Local web control center for the Blueprint translator.

The server intentionally uses only Python's standard library. The UI is built
with Vite into dist/ and calls these JSON endpoints to run the existing
Blueprint translator, open reports, and prepare ARK DevKit export requests.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.capture import (
    CAPTURE_GRAPH_TYPES,
    graph_capture_path,
    infer_graph_type,
    load_capture_manifest,
    manifest_graph_records,
    maybe_write_capture_sidecars,
    save_captured_graph,
    upsert_graph_record,
    write_capture_manifest,
)
from blueprint_translator.graph_queue import graph_queue_summary, graph_queue_text_for_mode
from blueprint_translator.utils import read_clipboard, safe_filename
from blueprint_translator.uasset_graphs import (
    mine_graph_candidates,
    object_path_to_uasset_path,
    read_uasset_graph_content,
    write_graph_candidate_files,
    write_uasset_graph_read_files,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = PROJECT_ROOT / "captures"
DIST_ROOT = PROJECT_ROOT / "dist"
KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge_base"
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "devkit_exporters" / "export_current_blueprint_defaults.py"
DEVKIT_REQUEST_PATH = CAPTURE_ROOT / "_devkit_export_request.json"

REPORT_TARGETS = {
    "next_actions": ("output", "next_actions.md"),
    "notes_todo": ("output", "notes_todo.md"),
    "behavior_summary": ("output", "behavior_summary.md"),
    "context_review": ("output", "context_review.md"),
    "capture_quality_report": ("output", "capture_quality_report.md"),
    "diagnostics_report": ("output", "diagnostics_report.md"),
    "asset_report": ("output", "asset_report.md"),
    "call_graph_summary": ("output", "call_graph_summary.md"),
    "notes": ("notes.md",),
    "defaults": ("defaults.json",),
    "components": ("components.json",),
    "devkit_report": ("devkit_export_report.md",),
    "uasset_graph_read_report": ("uasset_graph_read_report.md",),
    "uasset_property_parse_report": ("uasset_property_parse_report.md",),
    "uasset_link_resolution_report": ("uasset_link_resolution_report.md",),
    "uasset_partial_graph_triage": ("uasset_partial_graph_triage.md",),
    "uasset_quality_gates": ("uasset_quality_gates.md",),
    "uasset_vs_clipboard_compare": ("uasset_vs_clipboard_compare.md",),
    "uasset_structure_report": ("uasset_structure_report.md",),
    "uasset_class_defaults_report": ("uasset_class_defaults_report.md",),
}

OPEN_TARGETS = {
    **REPORT_TARGETS,
    "asset_folder": (),
    "output_folder": ("output",),
    "graph_reports": ("output", "graph_reports"),
}

KNOWLEDGE_TARGETS = {
    "folder": (),
    "index": ("index.json",),
    "report": ("reports", "gigantoraptor_knowledge_base.md"),
    "global_report": ("global", "asset_index_report.md"),
    "global_index": ("global", "asset_index.sqlite"),
    "global_summary": ("global", "asset_index_summary.json"),
    "system": ("systems", "gigantoraptor.json"),
    "native_functions": ("native_functions.json",),
    "evidence": ("evidence.json",),
}

DEFAULT_COMPARE_ROOT = CAPTURE_ROOT / "_compare_reports"
JOB_TIMEOUT_SECONDS = 1800
JOB_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "timed_out"}
JOBS: dict[str, dict[str, object]] = {}
JOBS_LOCK = threading.Lock()


class ApiProblem(Exception):
    def __init__(self, status: HTTPStatus, payload: dict[str, object]):
        super().__init__(str(payload.get("error") or status.phrase))
        self.status = status
        self.payload = payload


def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def public_job(job: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in job.items()
        if key not in {"process", "thread", "cancelRequested", "onComplete"}
    }


def get_job(job_id: str) -> dict[str, object]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise ApiProblem(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "code": "job_not_found", "error": f"任务不存在：{job_id}"},
            )
        return public_job(dict(job))


def append_job_stream(job_id: str, key: str, text: str) -> None:
    if not text:
        return
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job[key] = str(job.get(key) or "") + text


def read_job_stream(job_id: str, stream: object, key: str) -> None:
    try:
        for line in iter(stream.readline, ""):  # type: ignore[attr-defined]
            append_job_stream(job_id, key, line)
    finally:
        try:
            stream.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def prune_finished_jobs(limit: int = 60) -> None:
    with JOBS_LOCK:
        finished = [
            (str(job.get("finishedAt") or ""), job_id)
            for job_id, job in JOBS.items()
            if str(job.get("status")) in JOB_TERMINAL_STATUSES
        ]
        finished.sort()
        for _finished_at, job_id in finished[: max(0, len(finished) - limit)]:
            JOBS.pop(job_id, None)


def run_background_job(
    job_id: str,
    command: list[str],
    on_complete: object,
) -> None:
    started = time.time()
    process: subprocess.Popen[str] | None = None
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        if bool(job.get("cancelRequested")):
            job["status"] = "cancelled"
            job["finishedAt"] = now_iso()
            return
        job["status"] = "running"
        job["startedAt"] = now_iso()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["process"] = process
        readers: list[threading.Thread] = []
        if process.stdout:
            readers.append(threading.Thread(target=read_job_stream, args=(job_id, process.stdout, "stdout"), daemon=True))
        if process.stderr:
            readers.append(threading.Thread(target=read_job_stream, args=(job_id, process.stderr, "stderr"), daemon=True))
        for reader in readers:
            reader.start()
        try:
            return_code = process.wait(timeout=JOB_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
            with JOBS_LOCK:
                if job_id in JOBS:
                    JOBS[job_id]["status"] = "timed_out"
                    JOBS[job_id]["error"] = f"任务超过 {JOB_TIMEOUT_SECONDS // 60} 分钟后超时。"
        for reader in readers:
            reader.join(timeout=2)
        duration = round(time.time() - started, 2)
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            cancel_requested = bool(job.get("cancelRequested"))
            if str(job.get("status")) != "timed_out":
                job["status"] = "cancelled" if cancel_requested else "succeeded" if return_code == 0 else "failed"
            job["returnCode"] = return_code
            job["durationSeconds"] = duration
            job["finishedAt"] = now_iso()
        result: dict[str, object] = {}
        if callable(on_complete):
            result = on_complete(return_code)  # type: ignore[misc]
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["result"] = result
    except Exception as exc:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["status"] = "failed"
                JOBS[job_id]["error"] = str(exc)
                JOBS[job_id]["durationSeconds"] = round(time.time() - started, 2)
                JOBS[job_id]["finishedAt"] = now_iso()
    finally:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id].pop("process", None)


def create_background_job(
    kind: str,
    title: str,
    command: list[str],
    on_complete: object,
) -> dict[str, object]:
    prune_finished_jobs()
    job_id = uuid.uuid4().hex[:12]
    job: dict[str, object] = {
        "id": job_id,
        "kind": kind,
        "title": title,
        "status": "queued",
        "command": " ".join(command),
        "stdout": "",
        "stderr": "",
        "returnCode": None,
        "durationSeconds": 0,
        "createdAt": now_iso(),
        "startedAt": "",
        "finishedAt": "",
        "error": "",
        "result": {},
        "cancelRequested": False,
    }
    thread = threading.Thread(target=run_background_job, args=(job_id, command, on_complete), daemon=True)
    job["thread"] = thread
    with JOBS_LOCK:
        JOBS[job_id] = job
    thread.start()
    return public_job(job)


def cancel_job(job_id: str) -> dict[str, object]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise ApiProblem(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "code": "job_not_found", "error": f"任务不存在：{job_id}"},
            )
        job["cancelRequested"] = True
        process = job.get("process")
        if str(job.get("status")) == "queued":
            job["status"] = "cancelled"
            job["finishedAt"] = now_iso()
    if isinstance(process, subprocess.Popen):
        process.terminate()
    return get_job(job_id)


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def read_json_file(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def collection_size(value: object) -> int:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


def count_defaults(data: object | None) -> int:
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return 0
    for key in ("defaults", "class_defaults", "properties", "values", "variables"):
        count = collection_size(data.get(key))
        if count:
            return count
    return collection_size(data)


def count_components(data: object | None) -> int:
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return 0
    for key in ("components", "component_candidates", "templates", "items"):
        count = collection_size(data.get(key))
        if count:
            return count
    return collection_size(data)


def component_source_counts(data: object | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    components = data if isinstance(data, list) else data.get("components", []) if isinstance(data, dict) else []
    if not isinstance(components, list):
        return counts
    for component in components:
        if not isinstance(component, dict):
            continue
        source = str(component.get("source") or "manual_or_unknown")
        counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12])


def parse_devkit_report_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, key in [
        ("Blueprint variables exported", "blueprintVariables"),
        ("Class defaults exported", "classDefaults"),
        ("Components exported", "componentsExported"),
        ("Warnings", "warnings"),
        ("Errors", "errors"),
        ("Skipped properties", "skipped"),
    ]:
        match = re.search(rf"-\s*{re.escape(label)}:\s*(\d+)", text)
        if match:
            counts[key] = int(match.group(1))
    return counts


def devkit_export_quality(asset_dir: Path, components_data: object | None) -> dict[str, object]:
    log_path = asset_dir / "devkit_export_log.json"
    report_path = asset_dir / "devkit_export_report.md"
    log_data = read_json_file(log_path)
    warnings: list[object] = []
    errors: list[object] = []
    skipped: list[object] = []
    debug: list[object] = []
    if isinstance(log_data, dict):
        warnings = list(log_data.get("warnings", [])) if isinstance(log_data.get("warnings", []), list) else []
        errors = list(log_data.get("errors", [])) if isinstance(log_data.get("errors", []), list) else []
        skipped = list(log_data.get("skipped", [])) if isinstance(log_data.get("skipped", []), list) else []
        debug = list(log_data.get("debug", [])) if isinstance(log_data.get("debug", []), list) else []
    skipped_attempts = int(log_data.get("skipped_attempts", len(skipped)) or len(skipped)) if isinstance(log_data, dict) else len(skipped)
    report_text = report_path.read_text(encoding="utf-8-sig", errors="replace") if report_path.is_file() else ""
    report_counts = parse_devkit_report_counts(report_text)
    sources = component_source_counts(components_data)
    safe_scs_hits = sum(
        count
        for source, count in sources.items()
        if "scs" in source.lower() or "simple_construction" in source.lower() or "componenttemplate" in source.lower()
    )
    restored_or_manual = sum(
        count
        for source, count in sources.items()
        if "manual" in source.lower() or "restored" in source.lower() or "unknown" in source.lower()
    )
    status = "missing"
    if log_path.is_file() or report_path.is_file():
        status = "ok"
        if errors:
            status = "error"
        elif warnings or skipped:
            status = "warning"
    return {
        "status": status,
        "hasLog": log_path.is_file(),
        "hasReport": report_path.is_file(),
        "logPath": str(log_path) if log_path.is_file() else "",
        "reportPath": str(report_path) if report_path.is_file() else "",
        "warnings": len(warnings),
        "errors": len(errors),
        "skipped": len(skipped),
        "skippedAttempts": skipped_attempts,
        "debugMessages": len(debug),
        "reportCounts": report_counts,
        "componentSourceCounts": sources,
        "safeScsComponentCount": safe_scs_hits,
        "manualOrRestoredComponentCount": restored_or_manual,
        "summary": export_quality_summary(status, report_counts, len(warnings), len(errors), len(skipped), safe_scs_hits, restored_or_manual),
    }


def export_quality_summary(
    status: str,
    report_counts: dict[str, int],
    warning_count: int,
    error_count: int,
    skipped_count: int,
    safe_scs_hits: int,
    restored_or_manual: int,
) -> str:
    if status == "missing":
        return "还没有 DevKit 导出日志。请先保存资产路径，然后在 ARK DevKit 里运行导出器。"
    if error_count:
        return f"DevKit 导出出现 {error_count} 个错误，请先查看 devkit_export_report.md。"
    exported_components = report_counts.get("componentsExported", 0)
    if safe_scs_hits:
        return f"组件上下文里有 {safe_scs_hits} 个疑似 SCS/component-template 来源，下一步可以补安全默认值字段白名单。"
    if exported_components == 0 and restored_or_manual:
        return "这次 DevKit 没有直接导出组件；当前 components.json 更像是分析器恢复或手工整理的候选。"
    if skipped_count:
        return f"导出成功，但跳过了 {skipped_count} 个属性；建议按报告复查关键默认值。"
    if warning_count:
        return f"导出成功，但有 {warning_count} 个警告。"
    return "DevKit 导出状态正常。"


def newest_mtime(path: Path) -> float | None:
    if not path.exists():
        return None
    newest: float | None = None
    for item in path.rglob("*") if path.is_dir() else [path]:
        try:
            mtime = item.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    return newest


def iso_time(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def graph_name_key(value: str) -> str:
    lowered = value.lower().strip()
    for prefix in ("function_", "func_", "macro_", "event_", "graph_"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
    return re.sub(r"[^a-z0-9_]+", "", lowered)


def captured_graph_keys(asset_dir: Path) -> set[str]:
    keys: set[str] = set()
    manifest = read_json_file(asset_dir / "manifest.json")
    if isinstance(manifest, dict):
        graphs = manifest.get("graphs")
        if isinstance(graphs, dict):
            items = [{"name": name, **value} if isinstance(value, dict) else {"name": name, "path": value} for name, value in graphs.items()]
        elif isinstance(graphs, list):
            items = graphs
        else:
            items = []
        for item in items:
            if isinstance(item, dict):
                path_text = str(item.get("path") or item.get("file") or "")
                keys.add(graph_name_key(str(item.get("name") or item.get("graph_name") or Path(path_text).stem)))
            elif isinstance(item, str):
                keys.add(graph_name_key(Path(item).stem))
    graphs_dir = asset_dir / "graphs"
    if graphs_dir.is_dir():
        keys.update(graph_name_key(path.stem) for path in graphs_dir.glob("*.txt"))
    return {key for key in keys if key}


def graph_count(asset_dir: Path) -> int:
    keys = captured_graph_keys(asset_dir)
    uasset_graphs_dir = asset_dir / "graphs_from_uasset"
    if uasset_graphs_dir.is_dir():
        keys.update(graph_name_key(path.stem.rsplit("_", 1)[0]) for path in uasset_graphs_dir.glob("*.json"))
    return len({key for key in keys if key})


def graph_queue_count(asset_dir: Path) -> int:
    queue_path = asset_dir / "graph_queue.txt"
    if not queue_path.is_file():
        queue_text = ""
    else:
        queue_text = queue_path.read_text(encoding="utf-8-sig", errors="replace")
    return int(graph_queue_summary(queue_text).get("total") or 0)


def graph_queue_counts(asset_dir: Path) -> dict[str, int]:
    queue_path = asset_dir / "graph_queue.txt"
    queue_text = queue_path.read_text(encoding="utf-8-sig", errors="replace") if queue_path.is_file() else ""
    summary = graph_queue_summary(queue_text)
    return {
        "total": int(summary.get("total") or 0),
        "compact": int(summary.get("compact") or summary.get("recommended") or 0),
        "recommended": int(summary.get("recommended") or 0),
        "optional": int(summary.get("optional") or 0),
        "deferred": int(summary.get("deferred") or 0),
        "focused": int(summary.get("focused") or 0),
    }


def graph_candidate_count(asset_dir: Path) -> int:
    payload = read_json_file(asset_dir / "graph_candidates_uasset.json")
    if not isinstance(payload, dict):
        return 0
    try:
        return int(payload.get("candidate_count") or len(payload.get("candidates", [])))
    except Exception:
        return 0


def uasset_structure_counts(asset_dir: Path) -> dict[str, int]:
    payload = read_json_file(asset_dir / "uasset_structure.json")
    if not isinstance(payload, dict):
        return {
            "edgraph": 0,
            "function_graph": 0,
            "collapsed": 0,
            "standalone": 0,
            "function": 0,
        }
    return {
        "edgraph": int(payload.get("graph_exports_count") or 0),
        "function_graph": int(payload.get("function_graph_exports_count") or 0),
        "collapsed": int(payload.get("collapsed_graph_exports_count") or 0),
        "standalone": int(payload.get("standalone_graph_exports_count") or 0),
        "function": int(payload.get("function_exports_count") or 0),
    }


def uasset_graph_read_counts(asset_dir: Path) -> dict[str, int]:
    payload = read_json_file(asset_dir / "uasset_graph_nodes.json")
    if not isinstance(payload, dict):
        return {"graphs": 0, "nodes": 0, "pins": 0, "links": 0, "complete": 0, "partial": 0, "needs": 0}
    status_counts = payload.get("status_counts", {})
    if not isinstance(status_counts, dict):
        status_counts = {}
    failed_queue = read_json_file(asset_dir / "uasset_failed_graph_queue.json")
    captured_keys = captured_graph_keys(asset_dir)
    pending_manual = 0
    if isinstance(failed_queue, dict) and isinstance(failed_queue.get("graphs"), list):
        for item in failed_queue.get("graphs", []):
            if isinstance(item, dict) and graph_name_key(str(item.get("graph") or "")) not in captured_keys:
                pending_manual += 1
    else:
        pending_manual = int(status_counts.get("needs_clipboard") or 0) + int(status_counts.get("failed") or 0)
    return {
        "graphs": int(payload.get("graph_count") or 0),
        "nodes": int(payload.get("node_count") or 0),
        "pins": int(payload.get("pin_count") or 0),
        "links": int(payload.get("link_count") or 0),
        "complete": int(status_counts.get("complete") or 0),
        "partial": int(status_counts.get("partial") or 0) + int(status_counts.get("heuristic") or 0),
        "needs": pending_manual,
    }


def asset_summary(asset_dir: Path) -> dict[str, object]:
    defaults_path = asset_dir / "defaults.json"
    uasset_defaults_path = asset_dir / "uasset_class_defaults.json"
    components_path = asset_dir / "components.json"
    output_dir = asset_dir / "output"
    graph_queue_path = asset_dir / "graph_queue.txt"
    graph_candidates_path = asset_dir / "graph_candidates_uasset.json"
    uasset_structure_path = asset_dir / "uasset_structure.json"
    uasset_graph_read_path = asset_dir / "uasset_graph_nodes.json"
    queue_counts = graph_queue_counts(asset_dir)
    structure_counts = uasset_structure_counts(asset_dir)
    graph_read_counts = uasset_graph_read_counts(asset_dir)
    defaults_data = read_json_file(defaults_path)
    uasset_defaults_data = read_json_file(uasset_defaults_path)
    defaults_count = count_defaults(defaults_data)
    if not defaults_count:
        defaults_count = count_defaults(uasset_defaults_data)
    components_data = read_json_file(components_path)
    reports = {
        key: (asset_dir / Path(*parts)).is_file()
        for key, parts in REPORT_TARGETS.items()
    }
    report_mtime = newest_mtime(output_dir)
    return {
        "name": asset_dir.name,
        "path": str(asset_dir),
        "graphs": graph_count(asset_dir),
        "hasGraphQueue": graph_queue_path.is_file(),
        "graphQueueCount": queue_counts["total"],
        "graphQueueCompactCount": queue_counts["compact"],
        "graphQueueRecommendedCount": queue_counts["recommended"],
        "graphQueueOptionalCount": queue_counts["optional"],
        "graphQueueDeferredCount": queue_counts["deferred"],
        "graphQueueFocusedCount": queue_counts["focused"],
        "hasGraphCandidates": graph_candidates_path.is_file(),
        "graphCandidateCount": graph_candidate_count(asset_dir),
        "hasUassetStructure": uasset_structure_path.is_file(),
        "uassetEdGraphCount": structure_counts["edgraph"],
        "uassetFunctionGraphCount": structure_counts["function_graph"],
        "uassetCollapsedGraphCount": structure_counts["collapsed"],
        "uassetStandaloneGraphCount": structure_counts["standalone"],
        "uassetFunctionCount": structure_counts["function"],
        "hasUassetGraphRead": uasset_graph_read_path.is_file(),
        "uassetReadGraphCount": graph_read_counts["graphs"],
        "uassetReadNodeCount": graph_read_counts["nodes"],
        "uassetReadPinCount": graph_read_counts["pins"],
        "uassetReadLinkCount": graph_read_counts["links"],
        "uassetReadCompleteCount": graph_read_counts["complete"],
        "uassetReadPartialCount": graph_read_counts["partial"],
        "uassetReadNeedsClipboardCount": graph_read_counts["needs"],
        "hasDefaults": defaults_path.is_file() or uasset_defaults_path.is_file(),
        "defaultsCount": defaults_count,
        "hasComponents": components_path.is_file(),
        "componentsCount": count_components(components_data),
        "hasNotes": (asset_dir / "notes.md").is_file() or (asset_dir / "notes.txt").is_file(),
        "hasOutput": output_dir.is_dir(),
        "lastOutputAt": iso_time(report_mtime),
        "reports": reports,
        "exportQuality": devkit_export_quality(asset_dir, components_data),
    }


def list_assets() -> list[dict[str, object]]:
    if not CAPTURE_ROOT.is_dir():
        return []
    assets = []
    for path in sorted(CAPTURE_ROOT.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        if (
            (path / "graphs").is_dir()
            or (path / "manifest.json").is_file()
            or (path / "defaults.json").is_file()
            or (path / "uasset_class_defaults.json").is_file()
            or (path / "graph_candidates_uasset.json").is_file()
            or (path / "uasset_graph_nodes.json").is_file()
        ):
            assets.append(asset_summary(path))
    return assets


def normalize_asset_path(raw_text: str) -> str:
    text = str(raw_text or "").strip().replace("\\", "/").strip("\"'")
    quoted = re.search(r"['\"](?P<path>/Game/[^'\"]+)['\"]", text)
    if quoted:
        text = quoted.group("path").strip()
    path_match = re.search(r"(?P<path>/Game/[^\s,'\"]+)", text)
    if path_match:
        text = path_match.group("path").strip()
    text = text.strip("\"'")
    if not text.startswith("/Game/"):
        return ""
    if "." in text and text.endswith("_C"):
        package, obj = text.rsplit(".", 1)
        text = package + "." + obj[:-2]
    if "." not in text:
        object_name = text.rsplit("/", 1)[-1]
        if object_name:
            text = text + "." + object_name
    return text


def read_devkit_request() -> str:
    data = read_json_file(DEVKIT_REQUEST_PATH)
    if isinstance(data, dict):
        return str(data.get("asset_path") or "")
    return ""


def write_devkit_request(asset_path: str) -> None:
    CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "blueprint-translator.devkit-export-request.v1",
        "asset_path": asset_path,
    }
    DEVKIT_REQUEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mine_uasset_graph_candidates_for_request(asset_path: str, max_candidates: int = 1600) -> dict[str, object]:
    normalized = normalize_asset_path(asset_path)
    if not normalized:
        raise ValueError("Paste an ARK DevKit Object Path that starts with /Game/.")
    payload, attempted = mine_graph_candidates(normalized, max_candidates=max_candidates)
    paths = write_graph_candidate_files(normalized, CAPTURE_ROOT, payload)
    write_devkit_request(normalized)
    return {
        "assetPath": normalized,
        "assetDir": paths.get("asset_dir", ""),
        "jsonPath": paths.get("json", ""),
        "textPath": paths.get("text", ""),
        "reportPath": paths.get("report", ""),
        "structureJsonPath": paths.get("structure_json", ""),
        "structureReportPath": paths.get("structure_report", ""),
        "uassetPath": str(payload.get("uasset_path") or ""),
        "candidateCount": int(payload.get("candidate_count") or 0),
        "rawStringCount": int(payload.get("raw_string_count") or 0),
        "structure": payload.get("structure", {}),
        "attemptedPaths": attempted,
        "pythonCommand": devkit_python_command(),
        "outputLogCommand": devkit_output_log_command(),
    }


def read_uasset_graphs_for_request(asset_path: str, max_graphs: int = 0, report_level: str = "standard", analyze_after: bool = True) -> dict[str, object]:
    normalized = normalize_asset_path(asset_path)
    if not normalized:
        raise ValueError("Paste an ARK DevKit Object Path that starts with /Game/.")
    uasset_path, attempted = object_path_to_uasset_path(normalized)
    if uasset_path is None:
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "code": "uasset_not_found",
                "error": "本地 .uasset 没有找到，请检查 DevKit Content root 或对象路径。",
                "attemptedPaths": attempted,
            },
        )
    payload = read_uasset_graph_content(normalized, uasset_path, max_graphs=max_graphs)
    paths = write_uasset_graph_read_files(normalized, CAPTURE_ROOT, payload)
    write_devkit_request(normalized)
    result: dict[str, object] = {
        "assetPath": normalized,
        "assetDir": paths.get("asset_dir", ""),
        "uassetPath": str(uasset_path),
        "uexpPath": str(payload.get("uexp_path") or ""),
        "graphReportPath": paths.get("graph_report", ""),
        "graphNodesPath": paths.get("graph_nodes_json", ""),
        "propertyReportPath": paths.get("property_report", ""),
        "pinLinkReportPath": paths.get("pin_link_report", ""),
        "partialTriageReportPath": paths.get("partial_triage_report", ""),
        "qualityGatesReportPath": paths.get("quality_gates_report", ""),
        "compareReportPath": paths.get("compare_report", ""),
        "failedQueuePath": paths.get("failed_queue", ""),
        "failedQueueJsonPath": paths.get("failed_queue_json", ""),
        "graphsDir": paths.get("graphs_dir", ""),
        "graphCount": int(payload.get("graph_count") or 0),
        "nodeCount": int(payload.get("node_count") or 0),
        "pinCount": int(payload.get("pin_count") or 0),
        "linkCount": int(payload.get("link_count") or 0),
        "statusCounts": payload.get("status_counts", {}),
        "attemptedPaths": attempted,
        "asset": asset_summary(Path(paths.get("asset_dir", ""))),
    }
    if analyze_after:
        result["analysisJob"] = start_analyzer_job(Path(paths["asset_dir"]), report_level, keep_stale_output=True)
    return result


def markdown_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip().replace("\\|", "|") for cell in stripped.strip("|").split("|")]


def normalize_note_function_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "", name.lower())


def missing_functions_from_context_json(asset_dir: Path, existing: set[str]) -> list[dict[str, object]] | None:
    report_path = asset_dir / "output" / "context_review.json"
    review = read_json_file(report_path)
    if not isinstance(review, dict):
        return None
    rows: list[dict[str, object]] = []
    for item in review.get("missing_functions", []):
        if not isinstance(item, dict):
            continue
        function = str(item.get("function") or "").strip()
        if not function or normalize_note_function_name(function) in existing:
            continue
        source_graphs = item.get("source_graphs", item.get("sourceGraphs", []))
        areas = item.get("areas", [])
        rows.append(
            {
                "function": function,
                "sourceGraphs": [str(value) for value in source_graphs if str(value)] if isinstance(source_graphs, list) else [],
                "areas": [str(value) for value in areas if str(value)] if isinstance(areas, list) else [],
                "suggested": str(item.get("notes_inherited") or item.get("suggested") or f"inherited: {function}"),
            }
        )
    return rows


def missing_functions_from_report(asset_dir: Path) -> list[dict[str, object]]:
    existing = existing_note_function_names(asset_dir)
    json_rows = missing_functions_from_context_json(asset_dir, existing)
    if json_rows is not None:
        return json_rows
    report_path = asset_dir / "output" / "context_review.md"
    if not report_path.is_file():
        report_path = asset_dir / "output" / "notes_todo.md"
    if not report_path.is_file():
        return []
    rows: list[dict[str, object]] = []
    in_table = False
    for line in report_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        cells = markdown_table_cells(line)
        if not cells:
            if in_table:
                break
            continue
        normalized = [cell.lower() for cell in cells]
        if normalized[:4] in (["function", "source graphs", "areas", "notes line"], ["function", "source graphs", "behavior areas", "suggested notes.md entry"]):
            in_table = True
            continue
        if in_table and set(cells) == {"---"}:
            continue
        if not in_table or len(cells) < 4:
            continue
        function = cells[0].strip()
        if not function or function == "---":
            continue
        function_key = normalize_note_function_name(function)
        if function_key in existing:
            continue
        rows.append(
            {
                "function": function,
                "sourceGraphs": [item.strip() for item in cells[1].split(",") if item.strip()],
                "areas": [item.strip() for item in cells[2].split(",") if item.strip()],
                "suggested": cells[3].strip(),
            }
        )
    return rows


def existing_note_function_names(asset_dir: Path) -> set[str]:
    notes_path = asset_dir / "notes.md"
    if not notes_path.is_file():
        notes_path = asset_dir / "notes.txt"
    if not notes_path.is_file():
        return set()
    text = notes_path.read_text(encoding="utf-8-sig", errors="replace").lower()
    names: set[str] = set()
    for line in text.splitlines():
        if ":" not in line:
            continue
        prefix, values = line.split(":", 1)
        prefix_key = prefix.strip().lower()
        value_text = values.strip().lower()
        if prefix_key in {"inherited", "native", "parent", "external", "ignore missing graph", "ignore_missing"}:
            names.update(normalize_note_function_name(item) for item in re.split(r"[,;]", values) if item.strip())
        elif any(marker in value_text for marker in ("parent", "native", "inherited", "external", "ignore")):
            names.add(normalize_note_function_name(prefix_key))
    return names


def append_notes_for_functions(asset_dir: Path, kind: str, functions: list[object], reason: str = "") -> dict[str, object]:
    valid_kinds = {
        "inherited": "inherited",
        "native": "inherited",
        "parent": "inherited",
        "ignore": "ignore missing graph",
        "ignore_missing": "ignore missing graph",
    }
    note_prefix = valid_kinds.get(kind)
    if not note_prefix:
        raise ValueError("Unknown notes kind.")
    names = [str(item).strip() for item in functions if str(item).strip()]
    if not names:
        raise ValueError("No functions selected.")
    existing = existing_note_function_names(asset_dir)
    added: list[str] = []
    skipped: list[str] = []
    for name in names:
        key = normalize_note_function_name(name)
        if key in existing:
            skipped.append(name)
            continue
        existing.add(key)
        added.append(name)
    notes_path = asset_dir / "notes.md"
    if not notes_path.exists():
        notes_path.write_text("# Capture Notes\n\n", encoding="utf-8")
    if added:
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = ["", f"## Web Review {stamp}", "", f"{note_prefix}: {', '.join(added)}"]
        if reason.strip():
            lines.append(f"reason: {reason.strip()}")
        notes_path.write_text(notes_path.read_text(encoding="utf-8-sig", errors="replace").rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return {"notesPath": str(notes_path), "added": added, "skipped": skipped}


def devkit_python_command() -> str:
    return 'exec(open(r"{}", encoding="utf-8").read())'.format(EXPORT_SCRIPT)


def devkit_output_log_command() -> str:
    return 'py exec(open(r"{}", encoding="utf-8").read())'.format(EXPORT_SCRIPT)


def resolve_asset_dir(raw_path: str) -> Path:
    if not raw_path:
        raise ValueError("Missing asset path.")
    asset_dir = Path(unquote(raw_path))
    if not asset_dir.is_absolute():
        asset_dir = PROJECT_ROOT / asset_dir
    asset_dir = asset_dir.resolve()
    if not asset_dir.is_dir():
        raise ValueError("Asset directory does not exist.")
    if not is_within(asset_dir, PROJECT_ROOT):
        raise ValueError("Asset directory must be inside the project.")
    return asset_dir


def resolve_target(asset_dir: Path, target: str, mapping: dict[str, tuple[str, ...]]) -> Path:
    if target not in mapping:
        raise ValueError("Unknown target.")
    parts = mapping[target]
    path = asset_dir if not parts else asset_dir.joinpath(*parts)
    if not is_within(path, asset_dir):
        raise ValueError("Target must stay inside the asset directory.")
    return path


def open_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(str(path))
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)])


def analyzer_command(asset_dir: Path, report_level: str, *, keep_stale_output: bool = False) -> list[str]:
    if report_level not in {"compact", "standard", "debug"}:
        raise ValueError("Invalid report level.")
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
    ]
    if keep_stale_output:
        command.append("--keep-stale-output")
    return command


def run_analyzer(asset_dir: Path, report_level: str) -> dict[str, object]:
    command = analyzer_command(asset_dir, report_level)
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=JOB_TIMEOUT_SECONDS,
    )
    return {
        "command": " ".join(command),
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "durationSeconds": round(time.time() - started, 2),
        "asset": asset_summary(asset_dir),
    }


def start_analyzer_job(asset_dir: Path, report_level: str, *, keep_stale_output: bool = False) -> dict[str, object]:
    command = analyzer_command(asset_dir, report_level, keep_stale_output=keep_stale_output)

    def complete(_return_code: int) -> dict[str, object]:
        return {
            "asset": asset_summary(asset_dir),
            "outputDir": str(asset_dir / "output"),
        }

    return create_background_job("analyze", f"{asset_dir.name} {report_level} 分析", command, complete)


def resolve_capture_target(body: dict[str, object]) -> tuple[Path, str]:
    asset_path = str(body.get("assetPath") or "").strip()
    asset_name = str(body.get("assetName") or "").strip()
    if asset_path:
        asset_dir = resolve_asset_dir(asset_path)
        manifest = load_capture_manifest(asset_dir)
        return asset_dir, str(manifest.get("asset_name") or asset_dir.name)
    if not asset_name:
        raise ValueError("Asset name is required for a new capture.")
    asset_dir = (CAPTURE_ROOT / safe_filename(asset_name, "BlueprintAsset")).resolve()
    if not is_within(asset_dir, PROJECT_ROOT):
        raise ValueError("Capture asset must stay inside the project.")
    return asset_dir, safe_filename(asset_name, "BlueprintAsset")


def capture_graph_from_request(body: dict[str, object]) -> dict[str, object]:
    asset_dir, asset_name = resolve_capture_target(body)
    graph_name = str(body.get("graphName") or "").strip()
    if not graph_name:
        raise ValueError("Graph name is required.")
    graph_type = str(body.get("graphType") or infer_graph_type(graph_name))
    if graph_type not in CAPTURE_GRAPH_TYPES:
        graph_type = "Unknown"
    text = str(body.get("text") or "")
    source = "request body"
    if not text.strip():
        text = read_clipboard().lstrip("\ufeff")
        source = "Windows clipboard"
    manifest = load_capture_manifest(asset_dir)
    records = manifest_graph_records(manifest)
    allow_overwrite = bool(body.get("allowOverwrite"))
    existing_path = graph_capture_path(asset_dir, graph_name)
    if existing_path.exists() and not allow_overwrite:
        raise ApiProblem(
            HTTPStatus.CONFLICT,
            {
                "ok": False,
                "code": "overwrite_required",
                "error": f"图页已存在：{existing_path.name}",
                "existingPath": str(existing_path),
            },
        )
    record = save_captured_graph(asset_dir, graph_name, graph_type, text, allow_overwrite=allow_overwrite)
    records = upsert_graph_record(records, record)
    write_capture_manifest(
        asset_dir,
        asset_name,
        records,
        parent_class=str(manifest.get("parent_class") or ""),
        interfaces=manifest.get("interfaces", []) if isinstance(manifest.get("interfaces", []), list) else [],
        tags=manifest.get("tags", []) if isinstance(manifest.get("tags", []), list) else [],
    )
    maybe_write_capture_sidecars(asset_dir)
    result: dict[str, object] = {
        "source": source,
        "record": record,
        "asset": asset_summary(asset_dir),
        "manifest": str(asset_dir / "manifest.json"),
        "graphPath": str(asset_dir / str(record.get("path", ""))),
    }
    if bool(body.get("analyzeAfter")):
        result["analysisJob"] = start_analyzer_job(asset_dir, str(body.get("reportLevel") or "standard"))
    return result


def asset_compare_command(old_asset_dir: Path, new_asset_dir: Path) -> tuple[list[str], Path]:
    DEFAULT_COMPARE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    compare_dir = DEFAULT_COMPARE_ROOT / f"{safe_filename(old_asset_dir.name, 'old')}_to_{safe_filename(new_asset_dir.name, 'new')}_{stamp}"
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "bp_clipboard_to_prompt.py"),
        "--compare-asset",
        str(old_asset_dir),
        str(new_asset_dir),
        "--output-dir",
        str(compare_dir),
    ], compare_dir


def run_asset_compare_for_gui(old_asset_dir: Path, new_asset_dir: Path) -> dict[str, object]:
    command, compare_dir = asset_compare_command(old_asset_dir, new_asset_dir)
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=JOB_TIMEOUT_SECONDS,
    )
    behavior_report = compare_dir / "behavior_impact_report.md"
    summary = compare_dir / "compare_summary.md"
    return {
        "command": " ".join(command),
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "durationSeconds": round(time.time() - started, 2),
        "outputDir": str(compare_dir),
        "behaviorImpactPath": str(behavior_report) if behavior_report.is_file() else "",
        "summaryPath": str(summary) if summary.is_file() else "",
        "behaviorImpact": behavior_report.read_text(encoding="utf-8-sig", errors="replace") if behavior_report.is_file() else "",
    }


def start_asset_compare_job(old_asset_dir: Path, new_asset_dir: Path) -> dict[str, object]:
    command, compare_dir = asset_compare_command(old_asset_dir, new_asset_dir)

    def complete(_return_code: int) -> dict[str, object]:
        behavior_report = compare_dir / "behavior_impact_report.md"
        summary = compare_dir / "compare_summary.md"
        return {
            "outputDir": str(compare_dir),
            "behaviorImpactPath": str(behavior_report) if behavior_report.is_file() else "",
            "summaryPath": str(summary) if summary.is_file() else "",
            "behaviorImpact": behavior_report.read_text(encoding="utf-8-sig", errors="replace") if behavior_report.is_file() else "",
        }

    title = f"{old_asset_dir.name} → {new_asset_dir.name} 行为对比"
    return create_background_job("compare_asset", title, command, complete)


def knowledge_base_summary() -> dict[str, object]:
    index_path = KNOWLEDGE_ROOT / "index.json"
    report_path = KNOWLEDGE_ROOT / "reports" / "gigantoraptor_knowledge_base.md"
    global_report_path = KNOWLEDGE_ROOT / "global" / "asset_index_report.md"
    index = read_json_file(index_path)
    assets = index.get("assets", []) if isinstance(index, dict) else []
    systems = index.get("systems", []) if isinstance(index, dict) else []
    global_data = index.get("global", {}) if isinstance(index, dict) else {}
    generated = str(index.get("generated") or "") if isinstance(index, dict) else ""
    focus = str(index.get("focus") or "gigantoraptor") if isinstance(index, dict) else "gigantoraptor"
    return {
        "exists": index_path.is_file(),
        "root": str(KNOWLEDGE_ROOT),
        "indexPath": str(index_path),
        "reportPath": str(report_path),
        "reportExists": report_path.is_file(),
        "globalReportPath": str(global_report_path),
        "globalReportExists": global_report_path.is_file(),
        "generated": generated,
        "focus": focus,
        "assetCount": len(assets) if isinstance(assets, list) else 0,
        "systemCount": len(systems) if isinstance(systems, list) else 0,
        "globalAssetCount": int(global_data.get("asset_count") or 0) if isinstance(global_data, dict) else 0,
        "capturedAssetCount": int(global_data.get("captured_asset_count") or 0) if isinstance(global_data, dict) else 0,
    }


def knowledge_command(focus: str = "gigantoraptor", assets: list[str] | None = None) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_ark_knowledge_base.py"),
        "--focus",
        focus or "gigantoraptor",
    ]
    for asset in assets or []:
        if str(asset).strip():
            command.extend(["--asset", str(asset).strip()])
    return command


def start_knowledge_base_job(focus: str = "gigantoraptor", assets: list[str] | None = None) -> dict[str, object]:
    command = knowledge_command(focus, assets)

    def complete(_return_code: int) -> dict[str, object]:
        return {
            "knowledgeBase": knowledge_base_summary(),
        }

    return create_background_job("knowledge_base", f"{focus or 'gigantoraptor'} 背景知识库", command, complete)


def resolve_knowledge_target(target: str) -> Path:
    if target not in KNOWLEDGE_TARGETS:
        raise ValueError("Unknown knowledge base target.")
    parts = KNOWLEDGE_TARGETS[target]
    path = KNOWLEDGE_ROOT if not parts else KNOWLEDGE_ROOT.joinpath(*parts)
    if not is_within(path, KNOWLEDGE_ROOT):
        raise ValueError("Target must stay inside the knowledge base directory.")
    return path


def api_state() -> dict[str, object]:
    return {
        "projectRoot": str(PROJECT_ROOT),
        "captureRoot": str(CAPTURE_ROOT),
        "assets": list_assets(),
        "knowledgeBase": knowledge_base_summary(),
        "devkitRequestPath": str(DEVKIT_REQUEST_PATH),
        "devkitAssetPath": read_devkit_request(),
        "devkitPythonCommand": devkit_python_command(),
        "devkitOutputLogCommand": devkit_output_log_command(),
    }


class ControlCenterHandler(BaseHTTPRequestHandler):
    server_version = "BlueprintToolControlCenter/1.0"

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("[BlueprintTool] " + (format % args) + "\n")

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_error_json(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/state":
                self.send_json({"ok": True, **api_state()})
                return
            if parsed.path == "/api/report":
                self.handle_report(parsed.query)
                return
            if parsed.path == "/api/missing-functions":
                values = parse_qs(parsed.query)
                asset_dir = resolve_asset_dir(values.get("assetPath", [""])[0])
                self.send_json({"ok": True, "items": missing_functions_from_report(asset_dir)})
                return
            if parsed.path == "/api/graph-queue":
                values = parse_qs(parsed.query)
                asset_dir = resolve_asset_dir(values.get("assetPath", [""])[0])
                mode = values.get("mode", ["all"])[0]
                queue_path = asset_dir / "graph_queue.txt"
                queue_text = queue_path.read_text(encoding="utf-8-sig", errors="replace") if queue_path.is_file() else ""
                self.send_json(
                    {
                        "ok": True,
                        "path": str(queue_path),
                        "mode": mode,
                        "content": graph_queue_text_for_mode(queue_text, mode),
                        "summary": graph_queue_summary(queue_text),
                    }
                )
                return
            if parsed.path == "/api/uasset-failed-queue":
                values = parse_qs(parsed.query)
                asset_dir = resolve_asset_dir(values.get("assetPath", [""])[0])
                queue_path = asset_dir / "uasset_failed_graph_queue.txt"
                queue_json_path = asset_dir / "uasset_failed_graph_queue.json"
                queue_text = queue_path.read_text(encoding="utf-8-sig", errors="replace") if queue_path.is_file() else ""
                captured_keys = captured_graph_keys(asset_dir)
                if captured_keys:
                    pending_lines = []
                    for line in queue_text.splitlines():
                        name = line.split("|", 1)[0].strip()
                        if graph_name_key(name) not in captured_keys:
                            pending_lines.append(line)
                    queue_text = "\n".join(pending_lines)
                    if queue_text:
                        queue_text += "\n"
                classified = read_json_file(queue_json_path)
                if isinstance(classified, dict) and captured_keys:
                    graphs = classified.get("graphs")
                    if isinstance(graphs, list):
                        classified = dict(classified)
                        classified["graphs"] = [
                            item
                            for item in graphs
                            if not isinstance(item, dict) or graph_name_key(str(item.get("graph") or "")) not in captured_keys
                        ]
                self.send_json(
                    {
                        "ok": True,
                        "path": str(queue_path),
                        "jsonPath": str(queue_json_path),
                        "content": queue_text,
                        "summary": graph_queue_summary(queue_text),
                        "classified": classified,
                    }
                )
                return
            if parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                self.send_json({"ok": True, "job": get_job(job_id)})
                return
            self.serve_static(parsed.path)
        except ApiProblem as exc:
            self.send_json(exc.payload, exc.status)
        except Exception as exc:
            self.send_error_json(str(exc))

    def do_POST(self) -> None:
        try:
            body = self.read_json_body()
            if self.path == "/api/analyze":
                asset_dir = resolve_asset_dir(str(body.get("assetPath") or ""))
                job = start_analyzer_job(asset_dir, str(body.get("reportLevel") or "standard"))
                self.send_json({"ok": True, "job": job}, HTTPStatus.ACCEPTED)
                return
            if self.path == "/api/capture-graph":
                result = capture_graph_from_request(body)
                self.send_json({"ok": True, **result})
                return
            if self.path == "/api/compare-asset":
                old_asset = resolve_asset_dir(str(body.get("oldAssetPath") or ""))
                new_asset = resolve_asset_dir(str(body.get("newAssetPath") or ""))
                job = start_asset_compare_job(old_asset, new_asset)
                self.send_json({"ok": True, "job": job}, HTTPStatus.ACCEPTED)
                return
            if self.path.startswith("/api/jobs/") and self.path.endswith("/cancel"):
                job_id = self.path.split("/")[-2]
                self.send_json({"ok": True, "job": cancel_job(job_id)})
                return
            if self.path == "/api/open":
                asset_dir = resolve_asset_dir(str(body.get("assetPath") or ""))
                target_path = resolve_target(asset_dir, str(body.get("target") or ""), OPEN_TARGETS)
                open_path(target_path)
                self.send_json({"ok": True, "path": str(target_path)})
                return
            if self.path == "/api/open-captures":
                CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
                open_path(CAPTURE_ROOT)
                self.send_json({"ok": True, "path": str(CAPTURE_ROOT)})
                return
            if self.path == "/api/knowledge-base/build":
                raw_assets = body.get("assets", [])
                assets = [str(item) for item in raw_assets] if isinstance(raw_assets, list) else None
                job = start_knowledge_base_job(str(body.get("focus") or "gigantoraptor"), assets)
                self.send_json({"ok": True, "job": job}, HTTPStatus.ACCEPTED)
                return
            if self.path == "/api/knowledge-base/open":
                target_path = resolve_knowledge_target(str(body.get("target") or "report"))
                open_path(target_path)
                self.send_json({"ok": True, "path": str(target_path)})
                return
            if self.path == "/api/devkit-request":
                asset_path = normalize_asset_path(str(body.get("assetPath") or ""))
                if not asset_path:
                    self.send_error_json("Paste an ARK DevKit path that starts with /Game/.")
                    return
                write_devkit_request(asset_path)
                self.send_json(
                    {
                        "ok": True,
                        "assetPath": asset_path,
                        "requestPath": str(DEVKIT_REQUEST_PATH),
                        "pythonCommand": devkit_python_command(),
                        "outputLogCommand": devkit_output_log_command(),
                    }
                )
                return
            if self.path == "/api/uasset-candidates":
                asset_path = str(body.get("assetPath") or "")
                max_candidates = int(body.get("maxCandidates") or 1600)
                result = mine_uasset_graph_candidates_for_request(asset_path, max_candidates=max_candidates)
                self.send_json({"ok": True, **result})
                return
            if self.path == "/api/uasset-graphs":
                asset_path = str(body.get("assetPath") or "")
                max_graphs = int(body.get("maxGraphs") or 0)
                analyze_after = bool(body.get("analyzeAfter", True))
                report_level = str(body.get("reportLevel") or "standard")
                result = read_uasset_graphs_for_request(
                    asset_path,
                    max_graphs=max_graphs,
                    report_level=report_level,
                    analyze_after=analyze_after,
                )
                self.send_json({"ok": True, **result}, HTTPStatus.ACCEPTED if analyze_after else HTTPStatus.OK)
                return
            if self.path == "/api/notes-append":
                asset_dir = resolve_asset_dir(str(body.get("assetPath") or ""))
                functions = body.get("functions", [])
                if not isinstance(functions, list):
                    raise ValueError("functions must be a list.")
                result = append_notes_for_functions(
                    asset_dir,
                    str(body.get("kind") or "inherited"),
                    functions,
                    str(body.get("reason") or ""),
                )
                self.send_json({"ok": True, **result, "items": missing_functions_from_report(asset_dir)})
                return
            self.send_error_json("Unknown API endpoint.", HTTPStatus.NOT_FOUND)
        except subprocess.TimeoutExpired:
            self.send_error_json("Analyzer timed out after 30 minutes.", HTTPStatus.REQUEST_TIMEOUT)
        except ApiProblem as exc:
            self.send_json(exc.payload, exc.status)
        except Exception as exc:
            self.send_error_json(str(exc))

    def handle_report(self, query: str) -> None:
        values = parse_qs(query)
        try:
            asset_dir = resolve_asset_dir(values.get("assetPath", [""])[0])
            target_path = resolve_target(asset_dir, values.get("target", [""])[0], REPORT_TARGETS)
            if not target_path.is_file():
                self.send_error_json("Report file does not exist.", HTTPStatus.NOT_FOUND)
                return
            content = target_path.read_text(encoding="utf-8-sig", errors="replace")
            self.send_json({"ok": True, "path": str(target_path), "content": content})
        except Exception as exc:
            self.send_error_json(str(exc))

    def serve_static(self, request_path: str) -> None:
        if request_path in {"", "/"}:
            relative = Path("index.html")
        else:
            relative = Path(unquote(request_path.lstrip("/")))
        static_path = (DIST_ROOT / relative).resolve()
        if not is_within(static_path, DIST_ROOT) or not static_path.is_file():
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            self.wfile.write(b"Build the UI first with: npm run build")
            return
        mime_type, _encoding = mimetypes.guess_type(str(static_path))
        data = static_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Blueprint translator web control center.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the control center in the default browser.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    server = ThreadingHTTPServer((args.host, args.port), ControlCenterHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Blueprint Tool Control Center: {url}")
    print("Press Ctrl+C to stop.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Blueprint Tool Control Center.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
