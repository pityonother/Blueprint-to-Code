"""ARK DevKit request, asset-read, and command services."""

from __future__ import annotations

import json
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

from arkdev_scripting_probe.native_class import (
    NativeClassProbeError,
    is_native_class_path,
    run_native_class_probe,
)
from blueprint_translator.artifact_modes import normalize_artifact_mode
from blueprint_translator.devkit_paths import first_existing_devkit_content_root
from blueprint_translator.uasset_graphs import (
    mine_graph_candidates,
    object_path_to_uasset_path,
    read_uasset_graph_content,
    write_graph_candidate_files,
    write_uasset_graph_read_files,
)

from .assets import normalize_asset_path, read_json_file
from .request import ApiProblem


def configured_devkit_content_root(content_root_file: Path) -> Path | None:
    return first_existing_devkit_content_root(config_file=content_root_file)


def read_devkit_request(request_path: Path) -> str:
    data = read_json_file(request_path)
    if isinstance(data, dict):
        return str(data.get("asset_path") or "")
    return ""


def write_devkit_request(
    asset_path: str,
    *,
    capture_root: Path,
    request_path: Path,
) -> None:
    capture_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "blueprint-translator.devkit-export-request.v1",
        "asset_path": asset_path,
    }
    request_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def mine_uasset_graph_candidates_for_request(
    asset_path: str,
    max_candidates: int = 1600,
    *,
    capture_root: Path,
    write_request: Callable[[str], None],
    python_command: Callable[[], str],
    output_log_command: Callable[[], str],
    mine_candidates: Callable[..., tuple[dict[str, object], list[str]]] = (
        mine_graph_candidates
    ),
    write_candidate_files: Callable[..., dict[str, str]] = (
        write_graph_candidate_files
    ),
) -> dict[str, object]:
    normalized = normalize_asset_path(asset_path)
    if not normalized:
        raise ValueError("Paste an ARK DevKit Object Path that starts with /Game/.")
    if normalized.casefold().startswith("/script/"):
        raise ApiProblem(
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "code": "native_class_requires_reflection",
                "error": "/Script 原生类没有 .uasset 候选，请使用原生类只读反射。",
            },
        )
    payload, attempted = mine_candidates(
        normalized,
        max_candidates=max_candidates,
    )
    paths = write_candidate_files(normalized, capture_root, payload)
    write_request(normalized)
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
        "pythonCommand": python_command(),
        "outputLogCommand": output_log_command(),
    }


def _devkit_root_from_content_root(content_root: Path | None) -> Path:
    if content_root is None:
        raise ApiProblem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "ok": False,
                "code": "devkit_not_found",
                "error": "没有找到本机 ARK DevKit，请先在 Epic Games Launcher 安装或定位 DevKit。",
            },
        )
    root = Path(content_root)
    if (
        root.name.casefold() != "content"
        or root.parent.name.casefold() != "shootergame"
        or root.parent.parent.name.casefold() != "projects"
    ):
        raise ApiProblem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "ok": False,
                "code": "devkit_root_invalid",
                "error": "DevKit Content root 不能定位到 Projects/ShooterGame/Content。",
            },
        )
    return root.parents[2]


def read_native_class_for_request(
    asset_path: str,
    *,
    content_root: Path | None,
    probe_script: Path,
    probe_runner: Callable[..., dict[str, object]] = run_native_class_probe,
) -> dict[str, object]:
    normalized = normalize_asset_path(asset_path)
    if not is_native_class_path(normalized):
        raise ValueError("Paste a native class path like /Script/Module.ClassName.")
    devkit_root = _devkit_root_from_content_root(content_root)
    try:
        result = probe_runner(
            normalized,
            devkit_root=devkit_root,
            probe_script=probe_script,
        )
    except NativeClassProbeError as exc:
        if exc.code == "invalid_native_class_path":
            status = HTTPStatus.BAD_REQUEST
        elif exc.code == "native_class_probe_timed_out":
            status = HTTPStatus.GATEWAY_TIMEOUT
        elif exc.code in {
            "devkit_commandlet_not_found",
            "devkit_project_not_found",
            "native_class_probe_script_not_found",
        }:
            status = HTTPStatus.SERVICE_UNAVAILABLE
        else:
            status = HTTPStatus.BAD_GATEWAY
        raise ApiProblem(
            status,
            {
                "ok": False,
                "code": exc.code,
                "error": str(exc),
            },
        ) from exc
    if result.get("classLoaded") is not True:
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "code": "native_class_not_found",
                "error": f"ARK DevKit 没有加载到原生类 {normalized}。",
            },
        )
    return result


def read_uasset_graphs_for_request(
    asset_path: str,
    max_graphs: int = 0,
    report_level: str = "standard",
    analyze_after: bool = True,
    artifact_mode: str | None = None,
    *,
    capture_root: Path,
    write_request: Callable[[str], None],
    summarize_asset: Callable[[Path], dict[str, object]],
    start_analysis_job: Callable[..., dict[str, object]],
    object_path_resolver: Callable[..., tuple[Path | None, list[str]]] = (
        object_path_to_uasset_path
    ),
    read_graph_content: Callable[..., dict[str, object]] = (
        read_uasset_graph_content
    ),
    write_graph_files: Callable[..., dict[str, str]] = (
        write_uasset_graph_read_files
    ),
    native_class_reader: Callable[[str], dict[str, object]] | None = None,
) -> dict[str, object]:
    normalized = normalize_asset_path(asset_path)
    if not normalized:
        raise ValueError(
            "Paste an ARK DevKit Object Path that starts with /Game/ or /Script/."
        )
    if normalized.casefold().startswith("/script/"):
        if not is_native_class_path(normalized):
            raise ValueError("Paste a native class path like /Script/Module.ClassName.")
        if native_class_reader is None:
            raise ApiProblem(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "code": "native_class_reader_unavailable",
                    "error": "当前版本没有配置 ARK DevKit 原生类只读反射。",
                },
            )
        return native_class_reader(normalized)
    uasset_path, attempted = object_path_resolver(normalized)
    if uasset_path is None:
        raise ApiProblem(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "code": "uasset_not_found",
                "error": (
                    "本地 .uasset 没有找到，请检查 DevKit Content root、"
                    "devkit_path_mappings.txt 或对象路径。"
                ),
                "attemptedPaths": attempted,
            },
        )
    mode = normalize_artifact_mode(artifact_mode)
    payload = read_graph_content(
        normalized,
        uasset_path,
        max_graphs=max_graphs,
    )
    paths = write_graph_files(
        normalized,
        capture_root,
        payload,
        artifact_mode=mode,
    )
    write_request(normalized)
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
        "asset": summarize_asset(Path(paths.get("asset_dir", ""))),
    }
    if analyze_after and mode != "indexed":
        result["analysisJob"] = start_analysis_job(
            Path(paths["asset_dir"]),
            report_level,
            keep_stale_output=True,
        )
    elif analyze_after:
        result["analysisSkipped"] = (
            "indexed mode already produced bounded evidence; "
            "legacy report analysis was not run"
        )
    return result


def devkit_python_command(project_root: Path, export_script: Path) -> str:
    return (
        'BLUEPRINT_TO_CODE_PROJECT_ROOT = r"{}"; '
        'exec(open(r"{}", encoding="utf-8").read())'
    ).format(project_root, export_script)


def devkit_output_log_command(project_root: Path, export_script: Path) -> str:
    return (
        'py BLUEPRINT_TO_CODE_PROJECT_ROOT = r"{}"; '
        'exec(open(r"{}", encoding="utf-8").read())'
    ).format(project_root, export_script)


__all__ = [
    "configured_devkit_content_root",
    "devkit_output_log_command",
    "devkit_python_command",
    "mine_uasset_graph_candidates_for_request",
    "read_native_class_for_request",
    "read_devkit_request",
    "read_uasset_graphs_for_request",
    "write_devkit_request",
]
