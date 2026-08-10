"""Report, evidence-query, and analyzer services for the control center."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

from blueprint_translator.evidence_publication import (
    _lexical_absolute,
    _require_plain_directory,
    _require_plain_path_chain,
)
from blueprint_translator.evidence_repository import open_asset_repository
from blueprint_translator.report_query import (
    DEFAULT_REPORT_QUERY_BUDGET,
    MAX_REPORT_CONTEXT_LINES,
    MAX_REPORT_QUERY_BUDGET,
    REPORT_FILES,
    build_report_view,
    read_report_source,
)
from blueprint_translator.utils import safe_filename

from .assets import (
    _evidence_public_metadata,
    _indexed_evidence_declared,
    normalize_asset_path,
)


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


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_asset_dir(raw_path: str, *, project_root: Path) -> Path:
    if not raw_path:
        raise ValueError("Missing asset path.")
    asset_dir = Path(unquote(raw_path))
    if not asset_dir.is_absolute():
        asset_dir = project_root / asset_dir
    asset_dir = _lexical_absolute(asset_dir)
    _require_plain_path_chain(asset_dir, label="asset directory")
    _require_plain_directory(asset_dir, label="asset directory")
    if os.path.normcase(os.path.commonpath((asset_dir, project_root))) != os.path.normcase(
        os.fspath(project_root)
    ):
        raise ValueError("Asset directory must be inside the project.")
    return asset_dir


def resolve_target(
    asset_dir: Path,
    target: str,
    mapping: dict[str, tuple[str, ...]],
) -> Path:
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
    *,
    repository_opener=open_asset_repository,
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
        raise ValueError(
            "asset identifier must be one directory name inside the capture root"
        )
    root = _lexical_absolute(capture_root)
    _require_plain_path_chain(root, label="capture root")
    _require_plain_directory(root, label="capture root")
    asset_dir = _lexical_absolute(root / identifier)
    _require_plain_path_chain(asset_dir, label="asset directory")
    if (
        os.path.normcase(os.path.commonpath((asset_dir, root)))
        != os.path.normcase(os.fspath(root))
        or not asset_dir.is_dir()
    ):
        raise ValueError("asset identifier does not resolve to a capture directory")
    if not isinstance(request, dict):
        raise ValueError("evidence query request must be an object")
    with repository_opener(asset_dir) as repository:
        result = repository.query(request)
        return {**result, **_evidence_public_metadata(repository)}


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
    report_path, report_text, evidence_metadata = read_report_source(
        asset_dir,
        target,
    )
    result = build_report_view(
        report_text,
        mode=mode,
        query=query,
        section=section,
        section_start_line=section_start_line,
        cursor=max(int(cursor or 0), 0),
        token_budget=min(max(int(budget or 0), 1), MAX_REPORT_QUERY_BUDGET),
        context_lines=min(
            max(int(context_lines or 0), 0),
            MAX_REPORT_CONTEXT_LINES,
        ),
    )
    return {"path": str(report_path), **result, **evidence_metadata}


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


def analyzer_command(
    asset_dir: Path,
    report_level: str,
    *,
    project_root: Path,
    keep_stale_output: bool = False,
    python_executable: str = sys.executable,
) -> list[str]:
    if report_level not in {"compact", "standard", "debug"}:
        raise ValueError("Invalid report level.")
    output_dir = asset_dir / "output"
    command = [
        python_executable,
        str(project_root / "scripts" / "bp_clipboard_to_prompt.py"),
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
    project_root: Path,
    keep_stale_output: bool = False,
    analyzer_command_builder: Callable[..., list[str]] | None = None,
    repository_opener=open_asset_repository,
    indexed_evidence_declared: Callable[[Path], bool] = _indexed_evidence_declared,
    normalize_path: Callable[[str], str] = normalize_asset_path,
    python_executable: str = sys.executable,
) -> list[str]:
    """Build current human reports, refreshing indexed sources in dual mode first."""

    root = asset_dir.expanduser().resolve()
    if not indexed_evidence_declared(root):
        if analyzer_command_builder is None:
            return analyzer_command(
                root,
                report_level,
                project_root=project_root,
                keep_stale_output=keep_stale_output,
                python_executable=python_executable,
            )
        return analyzer_command_builder(
            root,
            report_level,
            keep_stale_output=keep_stale_output,
        )
    if report_level not in {"compact", "standard", "debug"}:
        raise ValueError("Invalid report level.")
    with repository_opener(root) as repository:
        overview = repository.query(
            {"operation": "overview", "budgetTokens": 800}
        )
    asset = overview.get("asset", {})
    object_path = (
        normalize_path(str(asset.get("objectPath") or ""))
        if isinstance(asset, dict)
        else ""
    )
    if not object_path:
        raise ValueError(
            "Indexed evidence does not contain a valid /Game Object Path for report refresh."
        )
    target_name = safe_filename(
        object_path.rsplit(".", 1)[-1],
        "BlueprintAsset",
    )
    if (root.parent / target_name).resolve() != root:
        raise ValueError(
            "Indexed Object Path does not map back to the selected capture directory."
        )
    command = [
        python_executable,
        str(project_root / "scripts" / "bp_clipboard_to_prompt.py"),
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


def run_analyzer(
    asset_dir: Path,
    report_level: str,
    *,
    project_root: Path,
    command_builder: Callable[..., list[str]],
    summarize_asset: Callable[[Path], dict[str, object]],
    timeout_seconds: int,
) -> dict[str, object]:
    command = command_builder(asset_dir, report_level)
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    return {
        "command": " ".join(command),
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "durationSeconds": round(time.time() - started, 2),
        "asset": summarize_asset(asset_dir),
    }


def start_analyzer_job(
    asset_dir: Path,
    report_level: str,
    *,
    keep_stale_output: bool = False,
    command_builder: Callable[..., list[str]],
    summarize_asset: Callable[[Path], dict[str, object]],
    create_job: Callable[..., dict[str, object]],
) -> dict[str, object]:
    command = command_builder(
        asset_dir,
        report_level,
        keep_stale_output=keep_stale_output,
    )

    def complete(_return_code: int) -> dict[str, object]:
        return {
            "asset": summarize_asset(asset_dir),
            "outputDir": str(asset_dir / "output"),
        }

    return create_job(
        "analyze",
        f"{asset_dir.name} {report_level} 分析",
        command,
        complete,
    )


def start_report_generation_job(
    asset_dir: Path,
    report_level: str,
    *,
    keep_stale_output: bool = False,
    command_builder: Callable[..., list[str]],
    summarize_asset: Callable[[Path], dict[str, object]],
    create_job: Callable[..., dict[str, object]],
) -> dict[str, object]:
    command = command_builder(
        asset_dir,
        report_level,
        keep_stale_output=keep_stale_output,
    )

    def complete(_return_code: int) -> dict[str, object]:
        return {
            "asset": summarize_asset(asset_dir),
            "outputDir": str(asset_dir / "output"),
        }

    return create_job(
        "report_generation",
        f"{asset_dir.name} {report_level} 当前 revision 人类报告",
        command,
        complete,
    )


__all__ = [
    "OPEN_TARGETS",
    "REPORT_TARGETS",
    "analyzer_command",
    "is_within",
    "open_path",
    "parse_report_query_int",
    "query_asset_evidence",
    "query_report_for_request",
    "report_generation_command",
    "resolve_asset_dir",
    "resolve_target",
    "run_analyzer",
    "start_analyzer_job",
    "start_report_generation_job",
]
