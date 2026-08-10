"""Asset inventory and path-free status summaries for the control center."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from pathlib import Path

from blueprint_translator.evidence_repository import open_asset_repository
from blueprint_translator.graph_queue import graph_queue_summary
from blueprint_translator.uasset_graphs import (
    current_uasset_graph_payload_files,
    normalize_blueprint_object_path,
)


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
    components = (
        data
        if isinstance(data, list)
        else data.get("components", [])
        if isinstance(data, dict)
        else []
    )
    if not isinstance(components, list):
        return counts
    for component in components:
        if not isinstance(component, dict):
            continue
        source = str(component.get("source") or "manual_or_unknown")
        counts[source] = counts.get(source, 0) + 1
    return dict(
        sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]
    )


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


def devkit_export_quality(
    asset_dir: Path,
    components_data: object | None,
) -> dict[str, object]:
    log_path = asset_dir / "devkit_export_log.json"
    report_path = asset_dir / "devkit_export_report.md"
    log_data = read_json_file(log_path)
    warnings: list[object] = []
    errors: list[object] = []
    skipped: list[object] = []
    debug: list[object] = []
    if isinstance(log_data, dict):
        warnings = (
            list(log_data.get("warnings", []))
            if isinstance(log_data.get("warnings", []), list)
            else []
        )
        errors = (
            list(log_data.get("errors", []))
            if isinstance(log_data.get("errors", []), list)
            else []
        )
        skipped = (
            list(log_data.get("skipped", []))
            if isinstance(log_data.get("skipped", []), list)
            else []
        )
        debug = (
            list(log_data.get("debug", []))
            if isinstance(log_data.get("debug", []), list)
            else []
        )
    skipped_attempts = (
        int(log_data.get("skipped_attempts", len(skipped)) or len(skipped))
        if isinstance(log_data, dict)
        else len(skipped)
    )
    report_text = (
        report_path.read_text(encoding="utf-8-sig", errors="replace")
        if report_path.is_file()
        else ""
    )
    report_counts = parse_devkit_report_counts(report_text)
    sources = component_source_counts(components_data)
    safe_scs_hits = sum(
        count
        for source, count in sources.items()
        if "scs" in source.lower()
        or "simple_construction" in source.lower()
        or "componenttemplate" in source.lower()
    )
    restored_or_manual = sum(
        count
        for source, count in sources.items()
        if "manual" in source.lower()
        or "restored" in source.lower()
        or "unknown" in source.lower()
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
        "summary": export_quality_summary(
            status,
            report_counts,
            len(warnings),
            len(errors),
            len(skipped),
            safe_scs_hits,
            restored_or_manual,
        ),
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
        return (
            f"组件上下文里有 {safe_scs_hits} 个疑似 SCS/component-template 来源，"
            "下一步可以补安全默认值字段白名单。"
        )
    if exported_components == 0 and restored_or_manual:
        return (
            "这次 DevKit 没有直接导出组件；当前 components.json "
            "更像是分析器恢复或手工整理的候选。"
        )
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
            items = [
                {"name": name, **value}
                if isinstance(value, dict)
                else {"name": name, "path": value}
                for name, value in graphs.items()
            ]
        elif isinstance(graphs, list):
            items = graphs
        else:
            items = []
        for item in items:
            if isinstance(item, dict):
                path_text = str(item.get("path") or item.get("file") or "")
                keys.add(
                    graph_name_key(
                        str(
                            item.get("name")
                            or item.get("graph_name")
                            or Path(path_text).stem
                        )
                    )
                )
            elif isinstance(item, str):
                keys.add(graph_name_key(Path(item).stem))
    graphs_dir = asset_dir / "graphs"
    if graphs_dir.is_dir():
        keys.update(
            graph_name_key(path.stem) for path in graphs_dir.glob("*.txt")
        )
    return {key for key in keys if key}


def graph_count(asset_dir: Path) -> int:
    keys = captured_graph_keys(asset_dir)
    keys.update(
        graph_name_key(path.stem.rsplit("_", 1)[0])
        for path in current_uasset_graph_payload_files(asset_dir)
    )
    return len({key for key in keys if key})


def graph_queue_count(asset_dir: Path) -> int:
    queue_path = asset_dir / "graph_queue.txt"
    if not queue_path.is_file():
        queue_text = ""
    else:
        queue_text = queue_path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )
    return int(graph_queue_summary(queue_text).get("total") or 0)


def graph_queue_counts(asset_dir: Path) -> dict[str, int]:
    queue_path = asset_dir / "graph_queue.txt"
    queue_text = (
        queue_path.read_text(encoding="utf-8-sig", errors="replace")
        if queue_path.is_file()
        else ""
    )
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


def _indexed_evidence_declared(asset_dir: Path) -> bool:
    for candidate in (
        asset_dir / "evidence" / "current.json",
        asset_dir / "evidence" / "evidence.sqlite",
    ):
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        return True
    return False


def _evidence_public_metadata(repository: object) -> dict[str, object]:
    """Return the bounded, path-free evidence identity exposed by HTTP."""

    return {
        "sourceKind": str(getattr(repository, "source_kind")),
        "freshnessStatus": str(getattr(repository, "freshness_status")),
        "releaseAuthority": bool(getattr(repository, "release_authority")),
        "migrationRequired": bool(getattr(repository, "migration_required")),
        "manifestSha256": getattr(repository, "manifest_sha256"),
        "pointerSha256": getattr(repository, "pointer_sha256"),
    }


def indexed_asset_metrics(
    asset_dir: Path,
    *,
    repository_opener=open_asset_repository,
) -> tuple[dict[str, int], int, str, dict[str, object]]:
    with repository_opener(asset_dir) as repository:
        overview = repository.query(
            {"operation": "overview", "budgetTokens": 800}
        )
        graph_rows = repository.graph_summaries()
        evidence_metadata = _evidence_public_metadata(repository)
    summary = overview.get("summary", {})
    status_rows = [
        (
            str(row.get("status") or "").casefold(),
            graph_name_key(str(row.get("name") or "")),
        )
        for row in graph_rows
    ]
    captured_keys = captured_graph_keys(asset_dir)
    graph_counts = {
        "graphs": len(graph_rows),
        "nodes": int(summary.get("nodeCount") or 0),
        "pins": int(summary.get("pinCount") or 0),
        "links": int(summary.get("linkObservationCount") or 0),
        "complete": sum(
            status in {"complete", "complete_empty", "confirmed"}
            for status, _ in status_rows
        ),
        "partial": sum(
            status in {"partial", "heuristic", "ambiguous"}
            for status, _ in status_rows
        ),
        "needs": sum(
            status in {"needs_clipboard", "failed", "not_recovered"}
            and graph_key not in captured_keys
            for status, graph_key in status_rows
        ),
    }
    default_count = int(summary.get("defaultCount") or 0)
    revision = str(overview.get("asset", {}).get("revisionId") or "")
    return graph_counts, default_count, revision, evidence_metadata


def uasset_graph_read_counts(
    asset_dir: Path,
    *,
    repository_opener=open_asset_repository,
) -> dict[str, int]:
    if _indexed_evidence_declared(asset_dir):
        graph_counts, _, _, _ = indexed_asset_metrics(
            asset_dir,
            repository_opener=repository_opener,
        )
        return graph_counts
    payload = read_json_file(asset_dir / "uasset_graph_nodes.json")
    if not isinstance(payload, dict):
        return {
            "graphs": 0,
            "nodes": 0,
            "pins": 0,
            "links": 0,
            "complete": 0,
            "partial": 0,
            "needs": 0,
        }
    status_counts = payload.get("status_counts", {})
    if not isinstance(status_counts, dict):
        status_counts = {}
    failed_queue = read_json_file(asset_dir / "uasset_failed_graph_queue.json")
    captured_keys = captured_graph_keys(asset_dir)
    pending_manual = 0
    if isinstance(failed_queue, dict) and isinstance(
        failed_queue.get("graphs"),
        list,
    ):
        for item in failed_queue.get("graphs", []):
            if (
                isinstance(item, dict)
                and graph_name_key(str(item.get("graph") or ""))
                not in captured_keys
            ):
                pending_manual += 1
    else:
        pending_manual = int(status_counts.get("needs_clipboard") or 0) + int(
            status_counts.get("failed") or 0
        )
    return {
        "graphs": int(payload.get("graph_count") or 0),
        "nodes": int(payload.get("node_count") or 0),
        "pins": int(payload.get("pin_count") or 0),
        "links": int(payload.get("link_count") or 0),
        "complete": int(status_counts.get("complete") or 0),
        "partial": int(status_counts.get("partial") or 0)
        + int(status_counts.get("heuristic") or 0),
        "needs": pending_manual,
    }


def asset_summary(
    asset_dir: Path,
    *,
    report_targets: Mapping[str, tuple[str, ...]],
    repository_opener=open_asset_repository,
) -> dict[str, object]:
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
    has_indexed_evidence = _indexed_evidence_declared(asset_dir)
    evidence_metadata: dict[str, object] = {}
    if has_indexed_evidence:
        (
            graph_read_counts,
            evidence_default_count,
            evidence_revision,
            evidence_metadata,
        ) = indexed_asset_metrics(
            asset_dir,
            repository_opener=repository_opener,
        )
    else:
        graph_read_counts = uasset_graph_read_counts(
            asset_dir,
            repository_opener=repository_opener,
        )
        evidence_default_count = 0
        evidence_revision = ""
    defaults_data = read_json_file(defaults_path)
    uasset_defaults_data = read_json_file(uasset_defaults_path)
    defaults_count = count_defaults(defaults_data)
    if not defaults_count:
        defaults_count = count_defaults(uasset_defaults_data) or evidence_default_count
    components_data = read_json_file(components_path)
    formula_data = read_json_file(formula_candidates_path)
    formula_summary = (
        formula_data.get("summary", {})
        if isinstance(formula_data, dict)
        and isinstance(formula_data.get("summary", {}), dict)
        else {}
    )
    reports = {
        key: (asset_dir / Path(*parts)).is_file()
        for key, parts in report_targets.items()
    }
    if has_indexed_evidence:
        # The repository open above already validated the manifest-bound index.
        reports["agent_index"] = True
    preserved_legacy_reports = bool(
        has_indexed_evidence
        and any(
            reports.get(key, False)
            for key in (
                "behavior_summary",
                "asset_report",
                "diagnostics_report",
                "call_graph_summary",
            )
        )
    )
    report_mtime = newest_mtime(output_dir)
    return {
        "name": asset_dir.name,
        "path": str(asset_dir),
        "graphs": (
            graph_read_counts["graphs"]
            if has_indexed_evidence
            else graph_count(asset_dir)
        ),
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
        "hasUassetGraphRead": uasset_graph_read_path.is_file()
        or has_indexed_evidence,
        "hasEvidenceStore": has_indexed_evidence,
        "evidenceRevision": evidence_revision,
        "uassetReadGraphCount": graph_read_counts["graphs"],
        "uassetReadNodeCount": graph_read_counts["nodes"],
        "uassetReadPinCount": graph_read_counts["pins"],
        "uassetReadLinkCount": graph_read_counts["links"],
        "uassetReadCompleteCount": graph_read_counts["complete"],
        "uassetReadPartialCount": graph_read_counts["partial"],
        "uassetReadNeedsClipboardCount": graph_read_counts["needs"],
        "hasDefaults": defaults_path.is_file()
        or uasset_defaults_path.is_file()
        or evidence_default_count > 0,
        "defaultsCount": defaults_count,
        "hasComponents": components_path.is_file(),
        "componentsCount": count_components(components_data),
        "hasNotes": (asset_dir / "notes.md").is_file()
        or (asset_dir / "notes.txt").is_file(),
        "hasOutput": output_dir.is_dir(),
        "lastOutputAt": iso_time(report_mtime),
        "reports": reports,
        "preservedLegacyReports": preserved_legacy_reports,
        "formulaCandidateCount": int(formula_summary.get("candidate_count") or 0),
        "unresolvedFormulaCount": int(formula_summary.get("unresolved_count") or 0),
        "assetMemoryCardExists": asset_memory_card_path.is_file(),
        "contextPackExists": context_pack_path.is_file(),
        "exportQuality": devkit_export_quality(asset_dir, components_data),
        **evidence_metadata,
    }


def list_assets(
    capture_root: Path,
    *,
    report_targets: Mapping[str, tuple[str, ...]],
    repository_opener=open_asset_repository,
) -> list[dict[str, object]]:
    if not capture_root.is_dir():
        return []
    assets = []
    for path in sorted(capture_root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        if (
            (path / "graphs").is_dir()
            or (path / "manifest.json").is_file()
            or (path / "defaults.json").is_file()
            or (path / "uasset_class_defaults.json").is_file()
            or (path / "graph_candidates_uasset.json").is_file()
            or (path / "uasset_graph_nodes.json").is_file()
            or _indexed_evidence_declared(path)
        ):
            assets.append(
                asset_summary(
                    path,
                    report_targets=report_targets,
                    repository_opener=repository_opener,
                )
            )
    return assets


def normalize_asset_path(raw_text: str) -> str:
    return normalize_blueprint_object_path(raw_text)


__all__ = [
    "_evidence_public_metadata",
    "_indexed_evidence_declared",
    "asset_summary",
    "captured_graph_keys",
    "collection_size",
    "component_source_counts",
    "count_components",
    "count_defaults",
    "devkit_export_quality",
    "export_quality_summary",
    "graph_candidate_count",
    "graph_count",
    "graph_name_key",
    "graph_queue_count",
    "graph_queue_counts",
    "indexed_asset_metrics",
    "iso_time",
    "list_assets",
    "newest_mtime",
    "normalize_asset_path",
    "parse_devkit_report_counts",
    "read_json_file",
    "uasset_graph_read_counts",
    "uasset_structure_counts",
]
