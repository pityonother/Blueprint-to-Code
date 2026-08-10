"""Legacy knowledge-base status, commands, and job services."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from .assets import read_json_file
from .reports import is_within


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


def knowledge_base_summary(knowledge_root: Path) -> dict[str, object]:
    index_path = knowledge_root / "index.json"
    report_path = (
        knowledge_root / "reports" / "gigantoraptor_knowledge_base.md"
    )
    global_report_path = knowledge_root / "global" / "asset_index_report.md"
    priority_report_path = knowledge_root / "priorities" / "priority_targets.md"
    priority_results_path = (
        knowledge_root / "priorities" / "priority_read_results.md"
    )
    priority_queue_path = knowledge_root / "priorities" / "deep_read_queue.txt"
    index = read_json_file(index_path)
    assets = index.get("assets", []) if isinstance(index, dict) else []
    systems = index.get("systems", []) if isinstance(index, dict) else []
    global_data = index.get("global", {}) if isinstance(index, dict) else {}
    generated = (
        str(index.get("generated") or "") if isinstance(index, dict) else ""
    )
    focus = (
        str(index.get("focus") or "gigantoraptor")
        if isinstance(index, dict)
        else "gigantoraptor"
    )
    return {
        "exists": index_path.is_file(),
        "root": str(knowledge_root),
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
        "globalAssetCount": (
            int(global_data.get("asset_count") or 0)
            if isinstance(global_data, dict)
            else 0
        ),
        "capturedAssetCount": (
            int(global_data.get("captured_asset_count") or 0)
            if isinstance(global_data, dict)
            else 0
        ),
    }


def knowledge_command(
    focus: str = "gigantoraptor",
    assets: list[str] | None = None,
    *,
    project_root: Path,
    configured_content_root: Callable[[], Path | None],
    python_executable: str = sys.executable,
) -> list[str]:
    command = [
        python_executable,
        str(project_root / "scripts" / "build_ark_knowledge_base.py"),
        "--focus",
        focus or "gigantoraptor",
    ]
    content_root = configured_content_root()
    if content_root:
        command.extend(["--content-root", str(content_root)])
    for asset in assets or []:
        if str(asset).strip():
            command.extend(["--asset", str(asset).strip()])
    return command


def start_knowledge_base_job(
    focus: str = "gigantoraptor",
    assets: list[str] | None = None,
    *,
    command_builder: Callable[..., list[str]],
    summarize_knowledge: Callable[[], dict[str, object]],
    create_job: Callable[..., dict[str, object]],
) -> dict[str, object]:
    command = command_builder(focus, assets)

    def complete(_return_code: int) -> dict[str, object]:
        return {
            "knowledgeBase": summarize_knowledge(),
        }

    return create_job(
        "knowledge_base",
        f"{focus or 'gigantoraptor'} 背景知识库",
        command,
        complete,
    )


def priority_read_command(
    limit: int = 25,
    *,
    analyze: bool = True,
    rebuild_knowledge: bool = True,
    project_root: Path,
    python_executable: str = sys.executable,
) -> list[str]:
    command = [
        python_executable,
        str(project_root / "scripts" / "read_priority_assets.py"),
        "--limit",
        str(max(limit, 0)),
    ]
    if not analyze:
        command.append("--no-analyze")
    if rebuild_knowledge:
        command.append("--rebuild-knowledge")
    return command


def start_priority_read_job(
    limit: int = 25,
    *,
    analyze: bool = True,
    command_builder: Callable[..., list[str]],
    summarize_knowledge: Callable[[], dict[str, object]],
    create_job: Callable[..., dict[str, object]],
) -> dict[str, object]:
    command = command_builder(limit, analyze=analyze)

    def complete(_return_code: int) -> dict[str, object]:
        return {
            "knowledgeBase": summarize_knowledge(),
        }

    return create_job(
        "priority_read",
        f"自动解析重点资产前 {limit} 个",
        command,
        complete,
    )


def resolve_knowledge_target(target: str, *, knowledge_root: Path) -> Path:
    if target not in KNOWLEDGE_TARGETS:
        raise ValueError("Unknown knowledge base target.")
    parts = KNOWLEDGE_TARGETS[target]
    path = knowledge_root if not parts else knowledge_root.joinpath(*parts)
    if not is_within(path, knowledge_root):
        raise ValueError("Target must stay inside the knowledge base directory.")
    return path


__all__ = [
    "KNOWLEDGE_TARGETS",
    "knowledge_base_summary",
    "knowledge_command",
    "priority_read_command",
    "resolve_knowledge_target",
    "start_knowledge_base_job",
    "start_priority_read_job",
]
