from __future__ import annotations

# ruff: noqa: E402 - local package imports follow the PROJECT_ROOT bootstrap.

import argparse
import json
import re
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
from blueprint_translator.artifact_modes import ARTIFACT_MODES, normalize_artifact_mode
from blueprint_translator.evidence_repository import (
    ResolvedEvidenceState,
    evidence_state_metadata,
    open_resolved_asset_repository,
    resolve_asset_evidence_state,
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
QUALITY_JSON = PROJECT_ROOT / "knowledge_base" / "priorities" / "priority_batch_quality_report.json"
QUALITY_MD = PROJECT_ROOT / "knowledge_base" / "priorities" / "priority_batch_quality_report.md"


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


def repository_graph_status_counts(repository: Any) -> dict[str, int]:
    status_counts: dict[str, int] = {}
    for graph in repository.graph_summaries():
        status = str(graph.get("status") or "unknown").casefold()
        status_counts[status] = status_counts.get(status, 0) + 1
    return status_counts


def resolve_indexed_evidence(
    asset_dir: Path,
) -> ResolvedEvidenceState | None:
    def declared(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        return True

    evidence_dir = asset_dir / "evidence"
    current_pointer = evidence_dir / "current.json"
    v2_markers = (
        evidence_dir / "evidence.sqlite",
        evidence_dir / "manifest.json",
    )
    markers = (current_pointer, *v2_markers)
    if not any(declared(marker) for marker in markers):
        return None
    # Once any indexed generation is declared, every missing/corrupt artifact
    # is a fail-closed error.  Legacy reports are only considered when there is
    # no v3/v2 declaration at all; they may not mask a damaged compatibility
    # store or acquire a release-quality verdict.
    return resolve_asset_evidence_state(asset_dir)


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


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def summary_int(text: str, label: str) -> int:
    match = re.search(rf"^- {re.escape(label)}:\s*([0-9]+)", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else 0


def summary_value(text: str, label: str) -> str:
    match = re.search(rf"^- {re.escape(label)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def default_object_matches_asset(default_object: str, asset_name: str) -> bool:
    if not default_object or not asset_name:
        return True
    normalized_default = default_object.strip("`").strip()
    normalized_asset = asset_name.strip("`").strip()
    if normalized_default.startswith("Default__"):
        normalized_default = normalized_default[len("Default__") :]
    normalized_default = normalized_default.removesuffix("_C").lower()
    normalized_asset = normalized_asset.removesuffix("_C").lower()
    return normalized_default == normalized_asset


def diagnostic_counts(text: str) -> dict[str, int]:
    match = re.search(
        r"^- Findings:\s*(?:(\d+)\s+error)?(?:,\s*)?(?:(\d+)\s+warning)?(?:,\s*)?(?:(\d+)\s+info)?",
        text,
        flags=re.MULTILINE,
    )
    if not match:
        return {"error": 0, "warning": 0, "info": 0}
    return {
        "error": int(match.group(1) or 0),
        "warning": int(match.group(2) or 0),
        "info": int(match.group(3) or 0),
    }


def markdown_table_after_heading(text: str, heading: str, *, limit: int = 20) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = index + 1
            break
    if start is None:
        return []
    table_lines: list[str] = []
    table_started = False
    for line in lines[start:]:
        stripped = line.strip()
        if not table_started and not stripped:
            continue
        if not table_started and not stripped.startswith("|"):
            return []
        if stripped.startswith("## ") and table_lines:
            break
        if stripped.startswith("|"):
            table_started = True
            table_lines.append(stripped)
        elif table_lines:
            break
    if len(table_lines) < 2:
        return []
    headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
        if len(rows) >= limit:
            break
    return rows


def top_class_defaults(report_text: str, *, limit: int = 8) -> list[dict[str, str]]:
    return markdown_table_after_heading(report_text, "Variables", limit=limit)


def diagnostic_errors_are_empty_graph_only(text: str) -> bool:
    error_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("### [ERROR]")]
    return bool(error_lines) and all("BP000" in line for line in error_lines)


def evaluate_asset_quality(result: dict[str, Any]) -> dict[str, Any]:
    asset_dir = Path(str(result.get("asset_dir") or ""))
    output_dir = asset_dir / "output"
    behavior = read_text(output_dir / "behavior_summary.md")
    diagnostics = read_text(output_dir / "diagnostics_report.md")
    capture_quality = read_text(output_dir / "capture_quality_report.md")
    defaults = read_text(asset_dir / "uasset_class_defaults_report.md")
    graph_status_counts = result.get("status_counts") or {}
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
    diagnostic_summary = diagnostic_counts(diagnostics)
    missing_graphs = markdown_table_after_heading(capture_quality, "Likely Missing Blueprint Graphs", limit=12)
    next_capture_actions = markdown_table_after_heading(capture_quality, "Next Capture Actions", limit=12)
    component_candidates = markdown_table_after_heading(capture_quality, "Component Candidates", limit=12)
    asset_name = str(result.get("asset_name") or result.get("asset_path") or "")
    default_object = summary_value(defaults, "Default object")
    class_defaults_mismatch = bool(default_object) and not default_object_matches_asset(default_object, asset_name)
    diagnostic_error_count_for_verdict = diagnostic_summary["error"]
    if diagnostic_error_count_for_verdict and diagnostic_errors_are_empty_graph_only(diagnostics) and int(result.get("node_count") or 0) == 0:
        diagnostic_error_count_for_verdict = 0
    legacy_report_files = {
        "behavior_summary": bool(behavior),
        "diagnostics_report": bool(diagnostics),
        "capture_quality_report": bool(capture_quality),
        "asset_report": (output_dir / "asset_report.md").is_file(),
    }
    evidence_state = resolve_indexed_evidence(asset_dir)
    indexed_report_files = {
        "evidence_store": bool(
            evidence_state and evidence_state.database_path.is_file()
        ),
        "agent_index": bool(
            evidence_state and evidence_state.agent_index_path.is_file()
        ),
    }
    report_files = {**legacy_report_files, **indexed_report_files}
    incomplete_graphs = {
        key: value
        for key, value in graph_status_counts.items()
        if key not in {"complete", "complete_empty"}
    }
    report_missing = not (
        all(indexed_report_files.values())
        or all(legacy_report_files.values())
    )
    analysis_failed = bool(analysis.get("error")) or (int(analysis.get("return_code") or 0) != 0 if analysis else False)
    quality_flags: list[str] = []
    if str(result.get("status")) not in {"read", "existing_indexed", "skipped_existing", "skipped_processed"}:
        quality_flags.append("read_failed")
    if report_missing:
        quality_flags.append("reports_missing")
    if analysis_failed:
        quality_flags.append("analysis_failed")
    if incomplete_graphs:
        quality_flags.append("incomplete_graphs")
    if diagnostic_error_count_for_verdict:
        quality_flags.append("diagnostic_errors")
    if class_defaults_mismatch:
        quality_flags.append("class_defaults_mismatch")
    if next_capture_actions:
        quality_flags.append("graphs_need_attention")
    if missing_graphs:
        quality_flags.append("missing_or_external_calls")
    if summary_int(diagnostics, "Parsed components") == 0 and component_candidates:
        quality_flags.append("components_missing")
    if "UASSET021" in diagnostics:
        quality_flags.append("pin_links_heuristic")
    if evidence_state is None:
        quality_flags.append("evidence_not_release_authority")
        quality_flags.append("evidence_migration_required")
    elif not evidence_state.release_authority:
        quality_flags.append("evidence_not_release_authority")
    if evidence_state is not None and evidence_state.migration_required:
        quality_flags.append("evidence_migration_required")
    confidence = summary_value(behavior, "Confidence") or summary_value(diagnostics, "Confidence")
    if confidence == "low":
        quality_flags.append("low_confidence")
    if not quality_flags:
        verdict = "good"
    elif any(flag in quality_flags for flag in ("read_failed", "reports_missing", "analysis_failed", "incomplete_graphs", "diagnostic_errors", "class_defaults_mismatch")):
        verdict = "needs_immediate_followup"
    elif any(flag in quality_flags for flag in ("graphs_need_attention", "missing_or_external_calls", "low_confidence")):
        verdict = "needs_review"
    else:
        verdict = "usable_with_notes"
    return {
        "asset_name": asset_name,
        "asset_path": result.get("asset_path"),
        "asset_dir": str(asset_dir),
        "status": result.get("status"),
        "verdict": verdict,
        "confidence": confidence,
        "graph_count": result.get("graph_count", 0),
        "node_count": result.get("node_count", 0),
        "pin_count": result.get("pin_count", 0),
        "link_count": result.get("link_count", 0),
        "graph_status_counts": graph_status_counts,
        "report_files": report_files,
        "diagnostics": diagnostic_summary,
        "diagnostic_error_count_for_verdict": diagnostic_error_count_for_verdict,
        "class_defaults": {
            "default_object": default_object,
            "matches_asset": not class_defaults_mismatch,
        },
        "quality_flags": quality_flags,
        "graphs_needing_attention": next_capture_actions,
        "missing_or_external_calls": missing_graphs,
        "component_candidates": component_candidates,
        "parsed_default_variables": 0 if class_defaults_mismatch else summary_int(diagnostics, "Parsed default variables") or summary_int(defaults, "Usable variables"),
        "parsed_components": summary_int(diagnostics, "Parsed components"),
        "key_defaults": [] if class_defaults_mismatch else top_class_defaults(defaults),
        "behavior_path": str(output_dir / "behavior_summary.md") if behavior else "",
        "diagnostics_path": str(output_dir / "diagnostics_report.md") if diagnostics else "",
        "capture_quality_path": str(output_dir / "capture_quality_report.md") if capture_quality else "",
        **(
            evidence_state_metadata(evidence_state)
            if evidence_state is not None
            else {
                "sourceKind": (
                    "LEGACY_REPORTS_ONLY"
                    if any(legacy_report_files.values())
                    else "NO_INDEXED_EVIDENCE"
                ),
                "freshnessStatus": "SOURCE_UNAVAILABLE",
                "releaseAuthority": False,
                "migrationRequired": True,
                "manifestSha256": None,
                "pointerSha256": None,
            }
        ),
    }


def build_quality_payload(results: list[dict[str, Any]]) -> dict[str, Any]:
    assets = [evaluate_asset_quality(result) for result in results]
    verdict_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    for item in assets:
        verdict_counts[str(item["verdict"])] = verdict_counts.get(str(item["verdict"]), 0) + 1
        for flag in item.get("quality_flags") or []:
            flag_counts[str(flag)] = flag_counts.get(str(flag), 0) + 1
    return {
        "schema": "ark-devkit-knowledge.priority-batch-quality.v1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "asset_count": len(assets),
        "verdict_counts": verdict_counts,
        "flag_counts": flag_counts,
        "assets": assets,
    }


def write_quality_report(out_path: Path, payload: dict[str, Any], *, title: str = "小批量读取质量评估") -> None:
    lines = [
        f"# {title}",
        "",
        f"- 生成时间：`{payload.get('generated')}`",
        f"- 本轮资产数：{payload.get('asset_count', 0)}",
        f"- 结论分布：{json.dumps(payload.get('verdict_counts') or {}, ensure_ascii=False)}",
        f"- 问题标记：{json.dumps(payload.get('flag_counts') or {}, ensure_ascii=False)}",
        "",
        "| 资产 | 结论 | 置信度 | 图页 | 节点 | 诊断 | 主要缺口 |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in payload.get("assets") or []:
        diagnostics = item.get("diagnostics") or {}
        diag_label = "E{}/W{}/I{}".format(
            diagnostics.get("error", 0),
            diagnostics.get("warning", 0),
            diagnostics.get("info", 0),
        )
        flags = ", ".join((item.get("quality_flags") or [])[:5]) or "-"
        lines.append(
            "| `{}` | `{}` | `{}` | {} | {} | {} | {} |".format(
                item.get("asset_name"),
                item.get("verdict"),
                item.get("confidence") or "-",
                item.get("graph_count", 0),
                item.get("node_count", 0),
                diag_label,
                flags,
            )
        )
    for item in payload.get("assets") or []:
        lines.extend(["", f"## {item.get('asset_name')}", ""])
        lines.append(f"- 报告：`{item.get('behavior_path') or item.get('asset_dir')}`")
        lines.append(f"- 默认值数量：{item.get('parsed_default_variables', 0)}；组件数量：{item.get('parsed_components', 0)}")
        class_defaults = item.get("class_defaults") or {}
        if class_defaults.get("default_object") and not class_defaults.get("matches_asset", True):
            lines.append(f"- 默认对象疑似错位：`{class_defaults.get('default_object')}`，本轮不把这些默认值当作可靠结论")
        attention = item.get("graphs_needing_attention") or []
        if attention:
            lines.append("- 需要继续看的图页：")
            for row in attention[:8]:
                lines.append(
                    f"  - `{row.get('Graph', '')}`：{row.get('Reason', '')}，节点 {row.get('Nodes', '')}，置信度 {row.get('Confidence', '')}"
                )
        missing = item.get("missing_or_external_calls") or []
        if missing:
            lines.append("- 需要判定 native/父类/本地图的调用：")
            for row in missing[:8]:
                lines.append(f"  - `{row.get('Source Graph', '')}` -> `{row.get('Function', '')}`")
            if len(missing) > 8:
                lines.append(f"  - ... 另外 {len(missing) - 8} 个")
        defaults = item.get("key_defaults") or []
        if defaults:
            lines.append("- 关键默认值预览：")
            for row in defaults[:6]:
                lines.append(f"  - `{row.get('Name', '')}` = `{row.get('Value', '')}` ({row.get('Type', '')})")
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def read_asset(
    object_path: str,
    *,
    max_graphs: int,
    analyze: bool,
    report_level: str,
    force: bool,
    artifact_mode: str | None = None,
) -> dict[str, Any]:
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
    evidence_state = None if force else resolve_indexed_evidence(asset_dir)
    if evidence_state is not None:
        with open_resolved_asset_repository(evidence_state) as repository:
            overview = repository.query({"operation": "overview", "budgetTokens": 800})
            status_counts = repository_graph_status_counts(repository)
        summary = overview.get("summary", {})
        return {
            "asset_path": object_path,
            "asset_name": asset_name,
            "status": "existing_indexed",
            "asset_dir": str(asset_dir),
            "uasset_path": str(uasset_path),
            "graph_count": summary.get("graphCount", 0),
            "node_count": summary.get("nodeCount", 0),
            "pin_count": summary.get("pinCount", 0),
            "link_count": summary.get("linkObservationCount", 0),
            "status_counts": status_counts,
            "revision_id": overview.get("asset", {}).get("revisionId", ""),
            **evidence_state_metadata(evidence_state),
        }
    if existing.is_file() and not force:
        result = existing_capture_result(object_path, asset_name, asset_dir, uasset_path)
        if analyze:
            try:
                result["analysis"] = analyze_asset(asset_dir, report_level)
            except Exception as exc:
                result["analysis"] = {"error": str(exc)}
        return result

    started = time.time()
    payload = read_uasset_graph_content(object_path, uasset_path, max_graphs=max_graphs)
    mode = normalize_artifact_mode(artifact_mode)
    paths = write_uasset_graph_read_files(object_path, CAPTURE_ROOT, payload, artifact_mode=mode)
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
        "artifact_mode": mode,
        "revision_id": paths.get("revision_id", ""),
    }
    if analyze and mode != "indexed":
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
    parser.add_argument("--artifact-mode", choices=sorted(ARTIFACT_MODES), default=None)
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
            artifact_mode=args.artifact_mode,
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
    quality_payload = build_quality_payload(results)
    out_json = PROJECT_ROOT / "knowledge_base" / "priorities" / "priority_read_results.json"
    out_md = PROJECT_ROOT / "knowledge_base" / "priorities" / "priority_read_results.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_batch_report(out_md, payload)
    QUALITY_JSON.write_text(json.dumps(quality_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_quality_report(QUALITY_MD, quality_payload)

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
    print(f"wrote {QUALITY_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
