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
import time
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
from blueprint_translator.artifact_modes import normalize_artifact_mode
from blueprint_translator.devkit_paths import first_existing_devkit_content_root
from blueprint_translator.graph_queue import graph_queue_summary, graph_queue_text_for_mode
from blueprint_translator.evidence_repository import open_asset_repository
from blueprint_translator.harvest_node_repository import (
    HarvestDatasetInvalid,
    HarvestDatasetNotBuilt,
    HarvestNodeRepository,
)
from blueprint_translator.harvest_build_jobs import (
    HarvestBuildAlreadyRunning,
    HarvestBuildArgumentError,
    HarvestBuildJobManager,
    HarvestBuildJobNotFound,
)
from blueprint_translator.resource_nodes import NODE_PAGE_MAX_LIMIT
from blueprint_translator.report_query import (
    DEFAULT_REPORT_QUERY_BUDGET,
    MAX_REPORT_CONTEXT_LINES,
    MAX_REPORT_QUERY_BUDGET,
    REPORT_FILES,
    build_report_view,
    resolve_report_path,
)
from blueprint_translator.utils import read_clipboard, safe_filename
from blueprint_translator.uasset_graphs import (
    current_uasset_graph_payload_files,
    mine_graph_candidates,
    normalize_blueprint_object_path,
    object_path_to_uasset_path,
    read_uasset_graph_content,
    write_graph_candidate_files,
    write_uasset_graph_read_files,
)
from blueprint_translator.kb_vnext.kb_api import (
    KnowledgeApiError,
    VNextKnowledgeService,
)
from blueprint_translator.kb_vnext.shadow_compare import (
    LegacyVNextComparator,
)
from blueprint_server.jobs import (
    JOB_TIMEOUT_SECONDS,
    cancel_job,
    create_background_job,
    get_job,
)
from blueprint_server.request import (
    ApiProblem,
    discard_bounded_body,
    read_json_object,
)
from blueprint_server.responses import (
    encode_json_response,
    error_payload,
    prepare_json_response,
    static_content_type,
)
from blueprint_server.routes_state import StateRoute, state_route_payload
from blueprint_server.security import SecurityPolicy, redact_sensitive_text
from package_full_env import read_project_version


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_VERSION = read_project_version(PROJECT_ROOT)
CAPTURE_ROOT = PROJECT_ROOT / "captures"
DIST_ROOT = PROJECT_ROOT / "dist"
KNOWLEDGE_ROOT = PROJECT_ROOT / "knowledge_base"
KB_VNEXT_SERVICE = VNextKnowledgeService(KNOWLEDGE_ROOT / "vnext")
KB_SHADOW_COMPARATOR = LegacyVNextComparator(
    vnext=KB_VNEXT_SERVICE,
    legacy_root=KNOWLEDGE_ROOT / "db",
)
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "devkit_exporters" / "export_current_blueprint_defaults.py"
DEVKIT_REQUEST_PATH = CAPTURE_ROOT / "_devkit_export_request.json"
DEVKIT_CONTENT_ROOT_FILE = PROJECT_ROOT / "devkit_content_root.txt"
HARVEST_CATALOG_PATH = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "resource_node_catalog.json"
)
HARVEST_IMAGE_ROOT = PROJECT_ROOT / "analysis" / "harvest_nodes" / "images"
HARVEST_RANKING_PATH = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_ranking_all_resources.query.json"
)
HARVEST_EVALUATION_CATALOG_PATH = (
    PROJECT_ROOT
    / "analysis"
    / "harvest_rankings"
    / "harvest_evaluation_catalog.json"
)
HARVEST_SQLITE_CATALOG_PATH = (
    PROJECT_ROOT / "analysis" / "harvest_nodes" / "harvest_catalog.sqlite"
)
HARVEST_REPOSITORY = HarvestNodeRepository(
    HARVEST_CATALOG_PATH,
    HARVEST_RANKING_PATH,
    evaluation_catalog_path=HARVEST_EVALUATION_CATALOG_PATH,
    sqlite_catalog_path=(
        HARVEST_SQLITE_CATALOG_PATH
        if HARVEST_SQLITE_CATALOG_PATH.is_file()
        else None
    ),
)
HARVEST_BUILD_MANAGER = HarvestBuildJobManager(project_root=PROJECT_ROOT)

def resolve_harvest_image_path(image_identity: str, image_root: Path = HARVEST_IMAGE_ROOT) -> Path:
    """Resolve one immutable image by lowercase SHA-256 identity only."""

    if re.fullmatch(r"[0-9a-f]{64}", str(image_identity or "")) is None:
        raise ValueError("Invalid harvest image identity.")
    resolved_root = Path(image_root).resolve()
    candidate = (resolved_root / f"{image_identity}.jpg").resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Harvest image resolves outside the cache root.") from exc
    if not candidate.is_file():
        raise FileNotFoundError("Harvest image was not found.")
    return candidate


def configured_devkit_content_root() -> Path | None:
    return first_existing_devkit_content_root(config_file=DEVKIT_CONTENT_ROOT_FILE)


REPORT_TARGETS = {
    **REPORT_FILES,
    "formula_candidates_json": ("output", "formula_candidates.json"),
    "unresolved_formulas": ("output", "formula_candidates.md"),
    "notes": ("notes.md",),
    "defaults": ("defaults.json",),
    "components": ("components.json",),
    "devkit_report": ("devkit_export_report.md",),
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
    "priority_report": ("priorities", "priority_targets.md"),
    "priority_results": ("priorities", "priority_read_results.md"),
    "priority_queue": ("priorities", "deep_read_queue.txt"),
    "system": ("systems", "gigantoraptor.json"),
    "native_functions": ("native_functions.json",),
    "evidence": ("evidence.json",),
}

DEFAULT_COMPARE_ROOT = CAPTURE_ROOT / "_compare_reports"


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
    keys.update(graph_name_key(path.stem.rsplit("_", 1)[0]) for path in current_uasset_graph_payload_files(asset_dir))
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


def indexed_asset_metrics(asset_dir: Path) -> tuple[dict[str, int], int, str]:
    with open_asset_repository(asset_dir) as repository:
        overview = repository.query({"operation": "overview", "budgetTokens": 800})
        graph_rows = repository.graph_summaries()
    summary = overview.get("summary", {})
    status_rows = [
        (str(row.get("status") or "").casefold(), graph_name_key(str(row.get("name") or "")))
        for row in graph_rows
    ]
    captured_keys = captured_graph_keys(asset_dir)
    graph_counts = {
        "graphs": len(graph_rows),
        "nodes": int(summary.get("nodeCount") or 0),
        "pins": int(summary.get("pinCount") or 0),
        "links": int(summary.get("linkObservationCount") or 0),
        "complete": sum(status in {"complete", "complete_empty", "confirmed"} for status, _ in status_rows),
        "partial": sum(status in {"partial", "heuristic", "ambiguous"} for status, _ in status_rows),
        "needs": sum(
            status in {"needs_clipboard", "failed", "not_recovered"} and graph_key not in captured_keys
            for status, graph_key in status_rows
        ),
    }
    default_count = int(summary.get("defaultCount") or 0)
    revision = str(overview.get("asset", {}).get("revisionId") or "")
    return graph_counts, default_count, revision


def uasset_graph_read_counts(asset_dir: Path) -> dict[str, int]:
    if (asset_dir / "evidence" / "evidence.sqlite").is_file():
        graph_counts, _, _ = indexed_asset_metrics(asset_dir)
        return graph_counts
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
    formula_candidates_path = output_dir / "formula_candidates.json"
    asset_memory_card_path = output_dir / "asset_memory_card.json"
    context_pack_path = output_dir / "context_pack.json"
    queue_counts = graph_queue_counts(asset_dir)
    structure_counts = uasset_structure_counts(asset_dir)
    evidence_database_path = asset_dir / "evidence" / "evidence.sqlite"
    if evidence_database_path.is_file():
        graph_read_counts, evidence_default_count, evidence_revision = indexed_asset_metrics(asset_dir)
    else:
        graph_read_counts = uasset_graph_read_counts(asset_dir)
        evidence_default_count = 0
        evidence_revision = ""
    defaults_data = read_json_file(defaults_path)
    uasset_defaults_data = read_json_file(uasset_defaults_path)
    defaults_count = count_defaults(defaults_data)
    if not defaults_count:
        defaults_count = count_defaults(uasset_defaults_data) or evidence_default_count
    components_data = read_json_file(components_path)
    formula_data = read_json_file(formula_candidates_path)
    formula_summary = formula_data.get("summary", {}) if isinstance(formula_data, dict) and isinstance(formula_data.get("summary", {}), dict) else {}
    reports = {
        key: (asset_dir / Path(*parts)).is_file()
        for key, parts in REPORT_TARGETS.items()
    }
    preserved_legacy_reports = bool(
        evidence_database_path.is_file()
        and any(
            reports.get(key, False)
            for key in ("behavior_summary", "asset_report", "diagnostics_report", "call_graph_summary")
        )
    )
    report_mtime = newest_mtime(output_dir)
    return {
        "name": asset_dir.name,
        "path": str(asset_dir),
        "graphs": graph_read_counts["graphs"] if evidence_database_path.is_file() else graph_count(asset_dir),
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
        "hasUassetGraphRead": uasset_graph_read_path.is_file() or evidence_database_path.is_file(),
        "hasEvidenceStore": evidence_database_path.is_file(),
        "evidenceRevision": evidence_revision,
        "uassetReadGraphCount": graph_read_counts["graphs"],
        "uassetReadNodeCount": graph_read_counts["nodes"],
        "uassetReadPinCount": graph_read_counts["pins"],
        "uassetReadLinkCount": graph_read_counts["links"],
        "uassetReadCompleteCount": graph_read_counts["complete"],
        "uassetReadPartialCount": graph_read_counts["partial"],
        "uassetReadNeedsClipboardCount": graph_read_counts["needs"],
        "hasDefaults": defaults_path.is_file() or uasset_defaults_path.is_file() or evidence_default_count > 0,
        "defaultsCount": defaults_count,
        "hasComponents": components_path.is_file(),
        "componentsCount": count_components(components_data),
        "hasNotes": (asset_dir / "notes.md").is_file() or (asset_dir / "notes.txt").is_file(),
        "hasOutput": output_dir.is_dir(),
        "lastOutputAt": iso_time(report_mtime),
        "reports": reports,
        "preservedLegacyReports": preserved_legacy_reports,
        "formulaCandidateCount": int(formula_summary.get("candidate_count") or 0),
        "unresolvedFormulaCount": int(formula_summary.get("unresolved_count") or 0),
        "assetMemoryCardExists": asset_memory_card_path.is_file(),
        "contextPackExists": context_pack_path.is_file(),
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
            or (path / "evidence" / "evidence.sqlite").is_file()
        ):
            assets.append(asset_summary(path))
    return assets


def normalize_asset_path(raw_text: str) -> str:
    return normalize_blueprint_object_path(raw_text)


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


def read_uasset_graphs_for_request(
    asset_path: str,
    max_graphs: int = 0,
    report_level: str = "standard",
    analyze_after: bool = True,
    artifact_mode: str | None = None,
) -> dict[str, object]:
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
                "error": "本地 .uasset 没有找到，请检查 DevKit Content root、devkit_path_mappings.txt 或对象路径。",
                "attemptedPaths": attempted,
            },
        )
    mode = normalize_artifact_mode(artifact_mode)
    payload = read_uasset_graph_content(normalized, uasset_path, max_graphs=max_graphs)
    paths = write_uasset_graph_read_files(normalized, CAPTURE_ROOT, payload, artifact_mode=mode)
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
        "artifactMode": mode,
        "evidenceDatabasePath": paths.get("evidence_database", ""),
        "evidenceManifestPath": paths.get("evidence_manifest", ""),
        "agentIndexPath": paths.get("agent_index", ""),
        "revisionId": paths.get("revision_id", ""),
        "graphCount": int(payload.get("graph_count") or 0),
        "nodeCount": int(payload.get("node_count") or 0),
        "pinCount": int(payload.get("pin_count") or 0),
        "linkCount": int(payload.get("link_count") or 0),
        "statusCounts": payload.get("status_counts", {}),
        "attemptedPaths": attempted,
        "asset": asset_summary(Path(paths.get("asset_dir", ""))),
    }
    if analyze_after and mode != "indexed":
        result["analysisJob"] = start_analyzer_job(Path(paths["asset_dir"]), report_level, keep_stale_output=True)
    elif analyze_after:
        result["analysisSkipped"] = "indexed mode already produced bounded evidence; legacy report analysis was not run"
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
    return 'BLUEPRINT_TO_CODE_PROJECT_ROOT = r"{}"; exec(open(r"{}", encoding="utf-8").read())'.format(PROJECT_ROOT, EXPORT_SCRIPT)


def devkit_output_log_command() -> str:
    return 'py BLUEPRINT_TO_CODE_PROJECT_ROOT = r"{}"; exec(open(r"{}", encoding="utf-8").read())'.format(PROJECT_ROOT, EXPORT_SCRIPT)


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


def query_asset_evidence(
    capture_root: Path,
    asset_identifier: str,
    request: dict[str, object],
) -> dict[str, object]:
    """Run a bounded evidence query without accepting a caller-supplied DB path."""

    identifier = str(asset_identifier or "").strip()
    candidate_part = Path(identifier)
    if (
        not identifier
        or candidate_part.is_absolute()
        or candidate_part.name != identifier
        or identifier in {".", ".."}
        or "/" in identifier
        or "\\" in identifier
        or ":" in identifier
    ):
        raise ValueError("asset identifier must be one directory name inside the capture root")
    root = Path(capture_root).expanduser().resolve()
    asset_dir = (root / identifier).resolve()
    if not is_within(asset_dir, root) or not asset_dir.is_dir():
        raise ValueError("asset identifier does not resolve to a capture directory")
    if not isinstance(request, dict):
        raise ValueError("evidence query request must be an object")
    with open_asset_repository(asset_dir) as repository:
        return repository.query(request)


def query_report_for_request(
    asset_dir: Path,
    target: str,
    *,
    mode: str = "outline",
    query: str = "",
    section: str = "",
    section_start_line: int | None = None,
    cursor: int = 0,
    budget: int = DEFAULT_REPORT_QUERY_BUDGET,
    context_lines: int = 2,
) -> dict[str, object]:
    report_path = resolve_report_path(asset_dir, target)
    result = build_report_view(
        report_path.read_text(encoding="utf-8-sig", errors="replace"),
        mode=mode,
        query=query,
        section=section,
        section_start_line=section_start_line,
        cursor=max(int(cursor or 0), 0),
        token_budget=min(max(int(budget or 0), 1), MAX_REPORT_QUERY_BUDGET),
        context_lines=min(max(int(context_lines or 0), 0), MAX_REPORT_CONTEXT_LINES),
    )
    return {"path": str(report_path), **result}


def parse_report_query_int(raw_value: str, name: str, default: int) -> int:
    if not str(raw_value or "").strip():
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


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


def report_generation_command(
    asset_dir: Path,
    report_level: str,
    *,
    keep_stale_output: bool = False,
) -> list[str]:
    """Build current human reports, refreshing indexed sources in dual mode first."""

    root = asset_dir.expanduser().resolve()
    if not (root / "evidence" / "evidence.sqlite").is_file():
        return analyzer_command(root, report_level, keep_stale_output=keep_stale_output)
    if report_level not in {"compact", "standard", "debug"}:
        raise ValueError("Invalid report level.")
    with open_asset_repository(root) as repository:
        overview = repository.query({"operation": "overview", "budgetTokens": 800})
    asset = overview.get("asset", {})
    object_path = normalize_asset_path(str(asset.get("objectPath") or "")) if isinstance(asset, dict) else ""
    if not object_path:
        raise ValueError("Indexed evidence does not contain a valid /Game Object Path for report refresh.")
    target_name = safe_filename(object_path.rsplit(".", 1)[-1], "BlueprintAsset")
    if (root.parent / target_name).resolve() != root:
        raise ValueError("Indexed Object Path does not map back to the selected capture directory.")
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "bp_clipboard_to_prompt.py"),
        "--asset-binary",
        object_path,
        "--capture-root",
        str(root.parent),
        "--artifact-mode",
        "dual",
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


def start_report_generation_job(
    asset_dir: Path,
    report_level: str,
    *,
    keep_stale_output: bool = False,
) -> dict[str, object]:
    command = report_generation_command(
        asset_dir,
        report_level,
        keep_stale_output=keep_stale_output,
    )

    def complete(_return_code: int) -> dict[str, object]:
        return {
            "asset": asset_summary(asset_dir),
            "outputDir": str(asset_dir / "output"),
        }

    return create_background_job(
        "report_generation",
        f"{asset_dir.name} {report_level} 当前 revision 人类报告",
        command,
        complete,
    )


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
    priority_report_path = KNOWLEDGE_ROOT / "priorities" / "priority_targets.md"
    priority_results_path = KNOWLEDGE_ROOT / "priorities" / "priority_read_results.md"
    priority_queue_path = KNOWLEDGE_ROOT / "priorities" / "deep_read_queue.txt"
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
        "priorityReportPath": str(priority_report_path),
        "priorityReportExists": priority_report_path.is_file(),
        "priorityResultsPath": str(priority_results_path),
        "priorityResultsExists": priority_results_path.is_file(),
        "priorityQueuePath": str(priority_queue_path),
        "priorityQueueExists": priority_queue_path.is_file(),
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
    content_root = configured_devkit_content_root()
    if content_root:
        command.extend(["--content-root", str(content_root)])
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


def priority_read_command(limit: int = 25, *, analyze: bool = True, rebuild_knowledge: bool = True) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "read_priority_assets.py"),
        "--limit",
        str(max(limit, 0)),
    ]
    if not analyze:
        command.append("--no-analyze")
    if rebuild_knowledge:
        command.append("--rebuild-knowledge")
    return command


def start_priority_read_job(limit: int = 25, *, analyze: bool = True) -> dict[str, object]:
    command = priority_read_command(limit, analyze=analyze)

    def complete(_return_code: int) -> dict[str, object]:
        return {
            "knowledgeBase": knowledge_base_summary(),
        }

    return create_background_job("priority_read", f"自动解析重点资产前 {limit} 个", command, complete)


def resolve_knowledge_target(target: str) -> Path:
    if target not in KNOWLEDGE_TARGETS:
        raise ValueError("Unknown knowledge base target.")
    parts = KNOWLEDGE_TARGETS[target]
    path = KNOWLEDGE_ROOT if not parts else KNOWLEDGE_ROOT.joinpath(*parts)
    if not is_within(path, KNOWLEDGE_ROOT):
        raise ValueError("Target must stay inside the knowledge base directory.")
    return path


def _harvest_dataset_problem(exc: Exception) -> ApiProblem:
    if isinstance(exc, HarvestDatasetNotBuilt):
        return ApiProblem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "ok": False,
                "code": exc.code,
                "error": "资源节点索引尚未生成，请先运行 build_ark_resource_node_catalog.py。",
            },
        )
    if isinstance(exc, HarvestDatasetInvalid):
        return ApiProblem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "ok": False,
                "code": exc.code,
                "error": "资源节点索引无效，请重新生成。",
            },
        )
    raise exc


def query_harvest_nodes_for_request(query: str) -> dict[str, object]:
    values = parse_qs(query)
    try:
        offset = max(
            0,
            parse_report_query_int(values.get("offset", [""])[0], "offset", 0),
        )
        limit = min(
            NODE_PAGE_MAX_LIMIT,
            max(
                1,
                parse_report_query_int(values.get("limit", [""])[0], "limit", 24),
            ),
        )
        return HARVEST_REPOSITORY.list_nodes(
            q=values.get("q", [""])[0],
            map_name=values.get("map", [""])[0],
            only_map_family=values.get("onlyMapFamily", [""])[0],
            resource=values.get("resource", [""])[0],
            offset=offset,
            limit=limit,
        )
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc
    except ValueError as exc:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "INVALID_HARVEST_NODE_FILTER",
                "error": "Invalid resource-node filter.",
            },
        ) from exc


def query_harvest_node_for_request(node_id: str) -> dict[str, object]:
    if not node_id:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {"ok": False, "code": "RESOURCE_NODE_ID_REQUIRED", "error": "缺少资源节点 ID。"},
        )
    try:
        return HARVEST_REPOSITORY.get_node(node_id)
    except KeyError as exc:
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {"ok": False, "code": "RESOURCE_NODE_NOT_FOUND", "error": "资源节点不存在。"},
        ) from exc
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc


def query_harvest_ranking_for_request(query: str) -> dict[str, object]:
    values = parse_qs(query)
    node_id = values.get("nodeId", [""])[0].strip()
    node_resource_id = values.get("nodeResourceId", [""])[0].strip()
    if not node_id or not node_resource_id:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "NODE_RESOURCE_ID_REQUIRED",
                "error": "排名查询必须同时提供 nodeId 和 nodeResourceId。",
            },
        )
    limit = min(
        10,
        max(
            1,
            parse_report_query_int(values.get("limit", [""])[0], "limit", 10),
        ),
    )
    try:
        return HARVEST_REPOSITORY.rankings(
            node_id,
            node_resource_id,
            limit=limit,
        )
    except KeyError as exc:
        code = str(exc).strip("'")
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {"ok": False, "code": code, "error": "资源节点或资源条目不存在。"},
        ) from exc
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc


def query_harvest_creatures_for_request(query: str) -> dict[str, object]:
    values = parse_qs(query)
    offset = max(
        0,
        parse_report_query_int(values.get("offset", [""])[0], "offset", 0),
    )
    limit = min(
        100,
        max(
            1,
            parse_report_query_int(values.get("limit", [""])[0], "limit", 24),
        ),
    )
    try:
        return HARVEST_REPOSITORY.list_creatures(
            q=values.get("q", [""])[0],
            offset=offset,
            limit=limit,
        )
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc


def query_harvest_creature_specialties_for_request(
    species_key: str,
    query: str,
) -> dict[str, object]:
    if not species_key:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "HARVEST_SPECIES_KEY_REQUIRED",
                "error": "A creature species key is required.",
            },
        )
    values = parse_qs(query)
    offset = max(
        0,
        parse_report_query_int(values.get("offset", [""])[0], "offset", 0),
    )
    limit = min(
        100,
        max(
            1,
            parse_report_query_int(values.get("limit", [""])[0], "limit", 24),
        ),
    )
    try:
        return HARVEST_REPOSITORY.creature_specialties(
            species_key,
            offset=offset,
            limit=limit,
        )
    except KeyError as exc:
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "code": "HARVEST_SPECIES_NOT_FOUND",
                "error": "The requested creature species was not found.",
            },
        ) from exc
    except (HarvestDatasetNotBuilt, HarvestDatasetInvalid) as exc:
        raise _harvest_dataset_problem(exc) from exc


def _harvest_build_problem(exc: Exception) -> ApiProblem:
    if isinstance(exc, HarvestBuildArgumentError):
        status = HTTPStatus.BAD_REQUEST
        message = "Invalid harvest build request."
    elif isinstance(exc, HarvestBuildAlreadyRunning):
        status = HTTPStatus.CONFLICT
        message = "A harvest build is already running."
    elif isinstance(exc, HarvestBuildJobNotFound):
        status = HTTPStatus.NOT_FOUND
        message = "The harvest build job was not found."
    else:
        raise exc
    payload: dict[str, object] = {
        "ok": False,
        "code": exc.code,
        "error": message,
    }
    job_id = getattr(exc, "job_id", None)
    if job_id:
        payload["jobId"] = job_id
    return ApiProblem(status, payload)


def query_harvest_build_for_request(query: str) -> dict[str, object] | None:
    values = parse_qs(query)
    job_id = values.get("jobId", [""])[0].strip() or None
    try:
        return HARVEST_BUILD_MANAGER.get(job_id)
    except HarvestBuildJobNotFound as exc:
        if job_id is None and exc.job_id is None:
            return None
        raise _harvest_build_problem(exc) from exc
    except (
        HarvestBuildArgumentError,
        HarvestBuildAlreadyRunning,
    ) as exc:
        raise _harvest_build_problem(exc) from exc


def start_harvest_build_for_request(body: dict[str, object]) -> dict[str, object]:
    if set(body) != {"options"}:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "HARVEST_BUILD_REQUEST_INVALID",
                "error": "The harvest build request must contain only an options object.",
            },
        )
    options = body.get("options")
    if not isinstance(options, dict):
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "HARVEST_BUILD_REQUEST_INVALID",
                "error": "The harvest build options value must be an object.",
            },
        )
    if options:
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "HARVEST_BUILD_OPTIONS_FORBIDDEN",
                "error": "Public harvest builds do not accept configuration overrides.",
            },
        )
    try:
        return HARVEST_BUILD_MANAGER.start(options)
    except (
        HarvestBuildArgumentError,
        HarvestBuildAlreadyRunning,
        HarvestBuildJobNotFound,
    ) as exc:
        raise _harvest_build_problem(exc) from exc


def cancel_harvest_build_for_request(job_id: str) -> dict[str, object]:
    if not job_id:
        raise _harvest_build_problem(
            HarvestBuildArgumentError("A harvest build job id is required.")
        )
    try:
        return HARVEST_BUILD_MANAGER.cancel(job_id)
    except (
        HarvestBuildArgumentError,
        HarvestBuildAlreadyRunning,
        HarvestBuildJobNotFound,
    ) as exc:
        raise _harvest_build_problem(exc) from exc


STATE_ROUTE = StateRoute(
    version=PROJECT_VERSION,
    project_root=PROJECT_ROOT,
    capture_root=CAPTURE_ROOT,
    devkit_request_path=DEVKIT_REQUEST_PATH,
    list_assets=lambda: list_assets(),
    knowledge_base_summary=lambda: knowledge_base_summary(),
    read_devkit_request=lambda: read_devkit_request(),
    devkit_python_command=lambda: devkit_python_command(),
    devkit_output_log_command=lambda: devkit_output_log_command(),
)


def api_state() -> dict[str, object]:
    """Compatibility entry for callers that imported the legacy server module."""

    return STATE_ROUTE.state()


def _kb_api_problem(exc: KnowledgeApiError) -> ApiProblem:
    return ApiProblem(
        exc.status,
        {
            "ok": False,
            "code": exc.code,
            "error": exc.message,
        },
    )


def _kb_query_value(
    values: dict[str, list[str]], key: str, default: str = ""
) -> str:
    raw = values.get(key, [default])
    return raw[0] if raw else default


def kb_get_payload(path: str, query: str) -> dict[str, object] | None:
    try:
        values = parse_qs(query, keep_blank_values=True)
        if path == "/api/kb/health":
            return KB_VNEXT_SERVICE.health()
        if path == "/api/kb/entities/search":
            return KB_VNEXT_SERVICE.search_entities(
                query=_kb_query_value(values, "q"),
                limit=_kb_query_value(values, "limit", "25"),
                cursor=_kb_query_value(values, "cursor", "0"),
            )
        prefix = "/api/kb/entities/"
        if path.startswith(prefix):
            remainder = unquote(path.removeprefix(prefix)).strip("/")
            parts = remainder.split("/")
            if not parts[0].isdigit() or int(parts[0]) <= 0:
                raise KnowledgeApiError(
                    HTTPStatus.BAD_REQUEST,
                    "REQUEST_INVALID",
                    "Entity id must be a positive integer.",
                )
            entity_id = int(parts[0])
            if len(parts) == 1:
                return KB_VNEXT_SERVICE.entity(entity_id)
            if len(parts) == 2 and parts[1] in {
                "facts",
                "relationships",
                "coverage",
                "effective-defaults",
            }:
                return KB_VNEXT_SERVICE.entity_collection(
                    entity_id,
                    kind=parts[1],
                    limit=_kb_query_value(values, "limit", "50"),
                    cursor=_kb_query_value(values, "cursor", "0"),
                )
            raise KnowledgeApiError(
                HTTPStatus.NOT_FOUND,
                "API_ENDPOINT_NOT_FOUND",
                "Unknown knowledge endpoint.",
            )
        if path.startswith("/api/kb/jobs/"):
            job_id = unquote(path.removeprefix("/api/kb/jobs/")).strip("/")
            return {
                "job": get_job(job_id),
                "returned": 1,
                "omitted": 0,
                "nextQuery": "",
                "freshness": "FRESH",
                "evidence": [],
                "gap": [],
            }
        return None
    except KnowledgeApiError as exc:
        raise _kb_api_problem(exc) from exc


_UNREAD_BODY_PROBLEM_CODES = frozenset(
    {
        "HOST_FORBIDDEN",
        "REQUEST_HEADERS_INVALID",
        "JSON_CONTENT_TYPE_REQUIRED",
        "SESSION_TOKEN_REQUIRED",
        "SESSION_TOKEN_INVALID",
        "ORIGIN_FORBIDDEN",
        "ORIGIN_REQUIRED",
        "REMOTE_AUTH_REQUIRED",
        "TRANSFER_ENCODING_UNSUPPORTED",
        "CONTENT_LENGTH_REQUIRED",
        "CONTENT_LENGTH_INVALID",
        "REQUEST_BODY_REQUIRED",
        "REQUEST_BODY_TOO_LARGE",
    }
)


class ControlCenterHandler(BaseHTTPRequestHandler):
    server_version = "BlueprintToolControlCenter/1.0"

    def security_policy(self) -> SecurityPolicy:
        policy = getattr(self.server, "security_policy", None)
        if not isinstance(policy, SecurityPolicy):
            raise RuntimeError("Control-center security policy is not configured.")
        return policy

    def log_message(self, format: str, *args: object) -> None:
        message = format % args
        policy = getattr(self.server, "security_policy", None)
        if isinstance(policy, SecurityPolicy):
            message = policy.redact(message, PROJECT_ROOT)
        else:
            message = redact_sensitive_text(
                message,
                path_roots=(PROJECT_ROOT,),
            )
        sys.stderr.write("[BlueprintTool] " + message + "\n")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "object-src 'none'; connect-src 'self'; img-src 'self' data:; "
            "script-src 'self'; style-src 'self' 'unsafe-inline'",
        )
        super().end_headers()

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        response = prepare_json_response(
            payload,
            status,
            close_connection=bool(self.close_connection),
        )
        self.send_response(response.status)
        for header, value in response.headers:
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(response.body)

    def send_error_json(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json(error_payload(message), status)

    def send_harvest_image(self, filename: str) -> None:
        identity = filename.removesuffix(".jpg") if filename.endswith(".jpg") else ""
        try:
            image_path = resolve_harvest_image_path(identity)
        except (ValueError, FileNotFoundError):
            self.send_error_json("Harvest image was not found.", HTTPStatus.NOT_FOUND)
            return
        etag = f'"{identity}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            return
        data = image_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def read_json_body(self) -> dict[str, object]:
        return read_json_object(
            self.rfile,
            self.headers,
            max_body_bytes=self.security_policy().max_body_bytes,
        )

    def discard_rejected_request_body(self) -> None:
        """Briefly drain a rejected bounded body before closing on Windows."""

        try:
            self.wfile.flush()
        except OSError:
            return
        previous_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(0.25)
            discard_bounded_body(
                self.rfile,
                self.headers,
                max_body_bytes=self.security_policy().max_body_bytes,
            )
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.connection.settimeout(previous_timeout)
            except OSError:
                pass

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self.security_policy().validate_get_request(
                    self.headers,
                    server_port=int(self.server.server_address[1]),
                )
            if parsed.path == "/api/session":
                policy = self.security_policy()
                self.send_json(
                    {
                        "ok": True,
                        "sessionToken": policy.session_token,
                    }
                )
                return
            kb_payload = kb_get_payload(parsed.path, parsed.query)
            if kb_payload is not None:
                self.send_json({"ok": True, **kb_payload})
                return
            state_payload = state_route_payload(parsed.path, api_state)
            if state_payload is not None:
                self.send_json(state_payload)
                return
            if parsed.path == "/api/harvest/nodes":
                self.send_json({"ok": True, **query_harvest_nodes_for_request(parsed.query)})
                return
            if parsed.path.startswith("/api/harvest/nodes/"):
                node_id = unquote(parsed.path.removeprefix("/api/harvest/nodes/"))
                self.send_json({"ok": True, "node": query_harvest_node_for_request(node_id)})
                return
            if parsed.path == "/api/harvest/rankings":
                self.send_json({"ok": True, **query_harvest_ranking_for_request(parsed.query)})
                return
            if parsed.path == "/api/harvest/creatures":
                self.send_json(
                    {"ok": True, **query_harvest_creatures_for_request(parsed.query)}
                )
                return
            if (
                parsed.path.startswith("/api/harvest/creatures/")
                and parsed.path.endswith("/specialties")
            ):
                species_key = unquote(
                    parsed.path[
                        len("/api/harvest/creatures/") : -len("/specialties")
                    ]
                ).strip("/")
                self.send_json(
                    {
                        "ok": True,
                        **query_harvest_creature_specialties_for_request(
                            species_key,
                            parsed.query,
                        ),
                    }
                )
                return
            if parsed.path == "/api/harvest/build":
                self.send_json(
                    {"ok": True, "job": query_harvest_build_for_request(parsed.query)}
                )
                return
            if parsed.path.startswith("/api/harvest/images/"):
                filename = unquote(parsed.path.removeprefix("/api/harvest/images/"))
                self.send_harvest_image(filename)
                return
            if parsed.path == "/api/report":
                self.handle_report(parsed.query)
                return
            if parsed.path == "/api/report-query":
                self.handle_report_query(parsed.query)
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
        is_harvest_build_request = self.path == "/api/harvest/build"
        is_harvest_cancel_request = (
            self.path.startswith("/api/harvest/build/")
            and self.path.endswith("/cancel")
        )
        try:
            self.security_policy().validate_post_request(
                self.headers,
                server_port=int(self.server.server_address[1]),
            )
            body = self.read_json_body()
            if self.path == "/api/kb/compare":
                try:
                    result = KB_SHADOW_COMPARATOR.compare(body)
                except KnowledgeApiError as exc:
                    raise _kb_api_problem(exc) from exc
                self.send_json({"ok": True, **result})
                return
            if self.path in {"/api/kb/query", "/api/kb/plan"}:
                try:
                    result = KB_VNEXT_SERVICE.query(body)
                except KnowledgeApiError as exc:
                    raise _kb_api_problem(exc) from exc
                self.send_json({"ok": True, **result})
                return
            if (
                self.path.startswith("/api/kb/jobs/")
                and self.path.endswith("/cancel")
            ):
                job_id = unquote(
                    self.path[
                        len("/api/kb/jobs/") : -len("/cancel")
                    ]
                ).strip("/")
                self.send_json(
                    {
                        "ok": True,
                        "job": cancel_job(job_id),
                        "returned": 1,
                        "omitted": 0,
                        "nextQuery": "",
                        "freshness": "FRESH",
                        "evidence": [],
                        "gap": [],
                    }
                )
                return
            if self.path == "/api/harvest/build":
                self.send_json(
                    {"ok": True, "job": start_harvest_build_for_request(body)},
                    HTTPStatus.ACCEPTED,
                )
                return
            if (
                self.path.startswith("/api/harvest/build/")
                and self.path.endswith("/cancel")
            ):
                job_id = unquote(
                    self.path[
                        len("/api/harvest/build/") : -len("/cancel")
                    ]
                ).strip("/")
                self.send_json(
                    {
                        "ok": True,
                        "job": cancel_harvest_build_for_request(job_id),
                    }
                )
                return
            if self.path == "/api/analyze":
                asset_dir = resolve_asset_dir(str(body.get("assetPath") or ""))
                job = start_report_generation_job(asset_dir, str(body.get("reportLevel") or "standard"))
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
            if self.path == "/api/knowledge-base/read-priority":
                limit = int(body.get("limit") or 25)
                analyze = bool(body.get("analyze", True))
                job = start_priority_read_job(limit, analyze=analyze)
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
                artifact_mode = str(body.get("artifactMode") or "") or None
                result = read_uasset_graphs_for_request(
                    asset_path,
                    max_graphs=max_graphs,
                    report_level=report_level,
                    analyze_after=analyze_after,
                    artifact_mode=artifact_mode,
                )
                accepted = analyze_after and result.get("analysisJob") is not None
                self.send_json({"ok": True, **result}, HTTPStatus.ACCEPTED if accepted else HTTPStatus.OK)
                return
            if self.path == "/api/evidence-queries":
                asset_identifier = str(body.get("asset") or body.get("assetName") or "")
                request = body.get("request")
                if request is None:
                    request = {
                        key: value
                        for key, value in body.items()
                        if key not in {"asset", "assetName"}
                    }
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                result = query_asset_evidence(CAPTURE_ROOT, asset_identifier, request)
                self.send_json({"ok": True, **result})
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
            self.send_json(
                {
                    "ok": False,
                    "code": "API_ENDPOINT_NOT_FOUND",
                    "error": "Unknown API endpoint.",
                },
                HTTPStatus.NOT_FOUND,
            )
        except subprocess.TimeoutExpired:
            self.send_json(
                {
                    "ok": False,
                    "code": "ANALYZER_TIMEOUT",
                    "error": "Analyzer timed out after 30 minutes.",
                },
                HTTPStatus.REQUEST_TIMEOUT,
            )
        except ApiProblem as exc:
            body_is_unread = (
                str(exc.payload.get("code") or "")
                in _UNREAD_BODY_PROBLEM_CODES
            )
            if body_is_unread:
                self.close_connection = True
            self.send_json(exc.payload, exc.status)
            if body_is_unread:
                self.discard_rejected_request_body()
        except (TypeError, ValueError):
            self.send_json(
                {
                    "ok": False,
                    "code": "REQUEST_INVALID",
                    "error": "Request arguments are invalid.",
                },
                HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            self.log_message("POST request failed: %s", exc)
            if is_harvest_build_request or is_harvest_cancel_request:
                self.send_json(
                    {
                        "ok": False,
                        "code": "HARVEST_BUILD_FAILED",
                        "error": "Harvest build request failed.",
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            else:
                self.send_json(
                    {
                        "ok": False,
                        "code": "REQUEST_FAILED",
                        "error": "Request failed.",
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

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

    def handle_report_query(self, query: str) -> None:
        values = parse_qs(query)
        try:
            asset_dir = resolve_asset_dir(values.get("assetPath", [""])[0])
            section_line_value = (
                values.get("sectionStartLine", [""])[0]
                or values.get("sectionLine", [""])[0]
            )
            result = query_report_for_request(
                asset_dir,
                values.get("target", ["asset_report"])[0],
                mode=values.get("mode", ["outline"])[0],
                query=values.get("query", [""])[0],
                section=values.get("section", [""])[0],
                section_start_line=(
                    parse_report_query_int(section_line_value, "sectionStartLine", 0)
                    if section_line_value
                    else None
                ),
                cursor=parse_report_query_int(values.get("cursor", [""])[0], "cursor", 0),
                budget=parse_report_query_int(
                    values.get("budget", [""])[0],
                    "budget",
                    DEFAULT_REPORT_QUERY_BUDGET,
                ),
                context_lines=parse_report_query_int(
                    values.get("contextLines", [""])[0],
                    "contextLines",
                    2,
                ),
            )
            self.send_json({"ok": True, **result})
        except FileNotFoundError as exc:
            self.send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.log_message("report query failed: %s", exc)
            self.send_error_json("Report query failed.", HTTPStatus.INTERNAL_SERVER_ERROR)

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
        self.send_header("Content-Type", static_content_type(mime_type))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_control_center_server(
    host: str,
    port: int,
    *,
    allow_remote: bool = False,
    auth_token: str | None = None,
) -> ThreadingHTTPServer:
    policy = SecurityPolicy(
        bind_host=host,
        allow_remote=allow_remote,
        auth_token=auth_token,
    )
    server = ThreadingHTTPServer((host, port), ControlCenterHandler)
    server.security_policy = policy  # type: ignore[attr-defined]
    return server


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Blueprint translator web control center.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow an explicit non-loopback bind. Requires --auth-token.",
    )
    parser.add_argument(
        "--auth-token",
        help="Bearer token required for every remote API request.",
    )
    parser.add_argument("--open", action="store_true", help="Open the control center in the default browser.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        server = create_control_center_server(
            args.host,
            args.port,
            allow_remote=args.allow_remote,
            auth_token=args.auth_token,
        )
    except ValueError as exc:
        print(f"Cannot start Blueprint Tool Control Center: {exc}", file=sys.stderr)
        return 2
    url = f"http://{args.host}:{args.port}/"
    print(f"Blueprint Tool Control Center: {url}")
    if server.security_policy.remote:  # type: ignore[attr-defined]
        print(
            "WARNING: remote access is enabled; bearer authentication is required."
        )
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
