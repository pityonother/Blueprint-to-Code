"""Capture, missing-function notes, and asset-compare services."""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
import sys
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

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
from blueprint_translator.utils import read_clipboard, safe_filename

from .assets import read_json_file
from .reports import is_within
from .request import ApiProblem


def markdown_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [
        cell.strip().replace("\\|", "|")
        for cell in stripped.strip("|").split("|")
    ]


def normalize_note_function_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "", name.lower())


def missing_functions_from_context_json(
    asset_dir: Path,
    existing: set[str],
) -> list[dict[str, object]] | None:
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
                "sourceGraphs": (
                    [str(value) for value in source_graphs if str(value)]
                    if isinstance(source_graphs, list)
                    else []
                ),
                "areas": (
                    [str(value) for value in areas if str(value)]
                    if isinstance(areas, list)
                    else []
                ),
                "suggested": str(
                    item.get("notes_inherited")
                    or item.get("suggested")
                    or f"inherited: {function}"
                ),
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
    for line in report_path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    ).splitlines():
        cells = markdown_table_cells(line)
        if not cells:
            if in_table:
                break
            continue
        normalized = [cell.lower() for cell in cells]
        if normalized[:4] in (
            ["function", "source graphs", "areas", "notes line"],
            [
                "function",
                "source graphs",
                "behavior areas",
                "suggested notes.md entry",
            ],
        ):
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
                "sourceGraphs": [
                    item.strip() for item in cells[1].split(",") if item.strip()
                ],
                "areas": [
                    item.strip() for item in cells[2].split(",") if item.strip()
                ],
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
        if prefix_key in {
            "inherited",
            "native",
            "parent",
            "external",
            "ignore missing graph",
            "ignore_missing",
        }:
            names.update(
                normalize_note_function_name(item)
                for item in re.split(r"[,;]", values)
                if item.strip()
            )
        elif any(
            marker in value_text
            for marker in ("parent", "native", "inherited", "external", "ignore")
        ):
            names.add(normalize_note_function_name(prefix_key))
    return names


def append_notes_for_functions(
    asset_dir: Path,
    kind: str,
    functions: list[object],
    reason: str = "",
) -> dict[str, object]:
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
        lines = [
            "",
            f"## Web Review {stamp}",
            "",
            f"{note_prefix}: {', '.join(added)}",
        ]
        if reason.strip():
            lines.append(f"reason: {reason.strip()}")
        notes_path.write_text(
            notes_path.read_text(encoding="utf-8-sig", errors="replace").rstrip()
            + "\n"
            + "\n".join(lines)
            + "\n",
            encoding="utf-8",
        )
    return {"notesPath": str(notes_path), "added": added, "skipped": skipped}


def resolve_capture_target(
    body: dict[str, object],
    *,
    capture_root: Path,
    project_root: Path,
    resolve_asset: Callable[[str], Path],
) -> tuple[Path, str]:
    asset_path = str(body.get("assetPath") or "").strip()
    asset_name = str(body.get("assetName") or "").strip()
    if asset_path:
        asset_dir = resolve_asset(asset_path)
        manifest = load_capture_manifest(asset_dir)
        return asset_dir, str(manifest.get("asset_name") or asset_dir.name)
    if not asset_name:
        raise ValueError("Asset name is required for a new capture.")
    asset_dir = (
        capture_root / safe_filename(asset_name, "BlueprintAsset")
    ).resolve()
    if not is_within(asset_dir, project_root):
        raise ValueError("Capture asset must stay inside the project.")
    return asset_dir, safe_filename(asset_name, "BlueprintAsset")


def capture_graph_from_request(
    body: dict[str, object],
    *,
    resolve_capture: Callable[[dict[str, object]], tuple[Path, str]],
    summarize_asset: Callable[[Path], dict[str, object]],
    start_analysis_job: Callable[..., dict[str, object]],
) -> dict[str, object]:
    asset_dir, asset_name = resolve_capture(body)
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
    record = save_captured_graph(
        asset_dir,
        graph_name,
        graph_type,
        text,
        allow_overwrite=allow_overwrite,
    )
    records = upsert_graph_record(records, record)
    write_capture_manifest(
        asset_dir,
        asset_name,
        records,
        parent_class=str(manifest.get("parent_class") or ""),
        interfaces=(
            manifest.get("interfaces", [])
            if isinstance(manifest.get("interfaces", []), list)
            else []
        ),
        tags=(
            manifest.get("tags", [])
            if isinstance(manifest.get("tags", []), list)
            else []
        ),
    )
    maybe_write_capture_sidecars(asset_dir)
    result: dict[str, object] = {
        "source": source,
        "record": record,
        "asset": summarize_asset(asset_dir),
        "manifest": str(asset_dir / "manifest.json"),
        "graphPath": str(asset_dir / str(record.get("path", ""))),
    }
    if bool(body.get("analyzeAfter")):
        result["analysisJob"] = start_analysis_job(
            asset_dir,
            str(body.get("reportLevel") or "standard"),
        )
    return result


def asset_compare_command(
    old_asset_dir: Path,
    new_asset_dir: Path,
    *,
    compare_root: Path,
    project_root: Path,
    python_executable: str = sys.executable,
) -> tuple[list[str], Path]:
    compare_root.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    compare_dir = compare_root / (
        f"{safe_filename(old_asset_dir.name, 'old')}_to_"
        f"{safe_filename(new_asset_dir.name, 'new')}_{stamp}"
    )
    return [
        python_executable,
        str(project_root / "scripts" / "bp_clipboard_to_prompt.py"),
        "--compare-asset",
        str(old_asset_dir),
        str(new_asset_dir),
        "--output-dir",
        str(compare_dir),
    ], compare_dir


def run_asset_compare_for_gui(
    old_asset_dir: Path,
    new_asset_dir: Path,
    *,
    project_root: Path,
    command_builder: Callable[[Path, Path], tuple[list[str], Path]],
    timeout_seconds: int,
) -> dict[str, object]:
    command, compare_dir = command_builder(old_asset_dir, new_asset_dir)
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
    behavior_report = compare_dir / "behavior_impact_report.md"
    summary = compare_dir / "compare_summary.md"
    return {
        "command": " ".join(command),
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "durationSeconds": round(time.time() - started, 2),
        "outputDir": str(compare_dir),
        "behaviorImpactPath": (
            str(behavior_report) if behavior_report.is_file() else ""
        ),
        "summaryPath": str(summary) if summary.is_file() else "",
        "behaviorImpact": (
            behavior_report.read_text(encoding="utf-8-sig", errors="replace")
            if behavior_report.is_file()
            else ""
        ),
    }


def start_asset_compare_job(
    old_asset_dir: Path,
    new_asset_dir: Path,
    *,
    command_builder: Callable[[Path, Path], tuple[list[str], Path]],
    create_job: Callable[..., dict[str, object]],
) -> dict[str, object]:
    command, compare_dir = command_builder(old_asset_dir, new_asset_dir)

    def complete(_return_code: int) -> dict[str, object]:
        behavior_report = compare_dir / "behavior_impact_report.md"
        summary = compare_dir / "compare_summary.md"
        return {
            "outputDir": str(compare_dir),
            "behaviorImpactPath": (
                str(behavior_report) if behavior_report.is_file() else ""
            ),
            "summaryPath": str(summary) if summary.is_file() else "",
            "behaviorImpact": (
                behavior_report.read_text(
                    encoding="utf-8-sig",
                    errors="replace",
                )
                if behavior_report.is_file()
                else ""
            ),
        }

    title = f"{old_asset_dir.name} → {new_asset_dir.name} 行为对比"
    return create_job("compare_asset", title, command, complete)


__all__ = [
    "append_notes_for_functions",
    "asset_compare_command",
    "capture_graph_from_request",
    "existing_note_function_names",
    "markdown_table_cells",
    "missing_functions_from_context_json",
    "missing_functions_from_report",
    "normalize_note_function_name",
    "resolve_capture_target",
    "run_asset_compare_for_gui",
    "start_asset_compare_job",
]
