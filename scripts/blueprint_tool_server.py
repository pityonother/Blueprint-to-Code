"""Local web control center for the Blueprint translator.

This module is the process composition root and compatibility facade. Domain
logic lives in ``blueprint_server`` modules; the standard-library HTTP handler,
server factory, and CLI remain here so existing launchers and imports keep
working.
"""

# ruff: noqa: E402, F401 - bootstrap imports and compatibility re-exports.

from __future__ import annotations

import argparse
import mimetypes
import subprocess
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from blueprint_translator.artifact_modes import normalize_artifact_mode
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
from blueprint_translator.devkit_paths import first_existing_devkit_content_root
from blueprint_translator.evidence_publication import (
    _lexical_absolute,
    _require_plain_directory,
    _require_plain_path_chain,
)
from blueprint_translator.evidence_repository import open_asset_repository
from blueprint_translator.graph_queue import (
    graph_queue_summary,
    graph_queue_text_for_mode,
)
from blueprint_translator.harvest_build_jobs import (
    HarvestBuildAlreadyRunning,
    HarvestBuildArgumentError,
    HarvestBuildJobManager,
    HarvestBuildJobNotFound,
)
from blueprint_translator.harvest_evaluation_catalog import (
    AVAILABILITY_GLOBAL_TRANSFER_ALLOWED,
    METRIC_OBSERVED_PER_NODE,
    METRIC_OBSERVED_PER_SECOND,
    METRIC_STATIC_CYCLE_SPEED,
    METRIC_STATIC_TOTAL,
    POLICY_CONFIRMED,
    POLICY_INCLUDE_CONDITIONAL,
    VARIANT_ALL,
    VARIANT_BEST_DISCOVERED_EXPLORATORY,
    VARIANT_CANONICAL,
)
from blueprint_translator.harvest_node_repository import (
    HarvestDatasetInvalid,
    HarvestDatasetNotBuilt,
    HarvestNodeRepository,
)
from blueprint_translator.harvest_runtime_observations import (
    HarvestRuntimeProfileError,
)
from blueprint_translator.kb_vnext.kb_api import (
    KnowledgeApiError,
    VNextKnowledgeService,
)
from blueprint_translator.kb_vnext.shadow_compare import LegacyVNextComparator
from blueprint_translator.report_query import (
    DEFAULT_REPORT_QUERY_BUDGET,
    MAX_REPORT_CONTEXT_LINES,
    MAX_REPORT_QUERY_BUDGET,
    REPORT_FILES,
    build_report_view,
    read_report_source,
    resolve_report_source,
)
from blueprint_translator.resource_nodes import NODE_PAGE_MAX_LIMIT
from blueprint_translator.uasset_graphs import (
    current_uasset_graph_payload_files,
    mine_graph_candidates,
    normalize_blueprint_object_path,
    object_path_to_uasset_path,
    read_uasset_graph_content,
    write_graph_candidate_files,
    write_uasset_graph_read_files,
)
from blueprint_translator.utils import read_clipboard, safe_filename

from blueprint_server import assets as _assets
from blueprint_server import captures as _captures
from blueprint_server import devkit as _devkit
from blueprint_server import harvest as _harvest
from blueprint_server import kb_routes as _kb_routes
from blueprint_server import knowledge as _knowledge
from blueprint_server import reports as _reports
from blueprint_server.assets import (
    _evidence_public_metadata,
    _indexed_evidence_declared,
    captured_graph_keys,
    collection_size,
    component_source_counts,
    count_components,
    count_defaults,
    devkit_export_quality,
    export_quality_summary,
    graph_candidate_count,
    graph_count,
    graph_name_key,
    graph_queue_count,
    graph_queue_counts,
    iso_time,
    newest_mtime,
    normalize_asset_path,
    parse_devkit_report_counts,
    read_json_file,
    uasset_structure_counts,
)
from blueprint_server.captures import (
    append_notes_for_functions,
    existing_note_function_names,
    markdown_table_cells,
    missing_functions_from_context_json,
    missing_functions_from_report,
    normalize_note_function_name,
)
from blueprint_server.harvest import (
    _harvest_build_problem,
    _harvest_dataset_problem,
    _harvest_runtime_profile_problem,
    _harvest_runtime_ranking_options,
)
from blueprint_server.jobs import (
    JOB_TIMEOUT_SECONDS,
    cancel_job,
    create_background_job,
    get_job,
)
from blueprint_server.kb_routes import _kb_api_problem, _kb_query_value
from blueprint_server.knowledge import KNOWLEDGE_TARGETS
from blueprint_server.reports import (
    OPEN_TARGETS,
    REPORT_TARGETS,
    is_within,
    open_path,
    parse_report_query_int,
    query_report_for_request,
    resolve_target,
)
from blueprint_server.request import (
    ApiProblem,
    discard_bounded_body,
    problem,
    read_json_object,
)
from blueprint_server.responses import (
    encode_json_response,
    error_payload,
    prepare_json_response,
    static_content_type,
)
from blueprint_server.routes_blueprint import blueprint_get_payload
from blueprint_server.routes_state import StateRoute, state_route_payload
from blueprint_server.security import SecurityPolicy, redact_sensitive_text
from package_full_env import read_project_version
from arkdev_scripting_probe.native_class import run_native_class_probe


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
EXPORT_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "devkit_exporters"
    / "export_current_blueprint_defaults.py"
)
NATIVE_CLASS_PROBE_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "arkdev_scripting_probe"
    / "in_editor"
    / "arkdev_native_class_probe.py"
)
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
HARVEST_RUNTIME_OBSERVATION_ROOT = (
    PROJECT_ROOT / "analysis" / "harvest_rankings" / "runtime_observations"
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
    runtime_observation_root=HARVEST_RUNTIME_OBSERVATION_ROOT,
)
HARVEST_BUILD_MANAGER = HarvestBuildJobManager(project_root=PROJECT_ROOT)
DEFAULT_COMPARE_ROOT = CAPTURE_ROOT / "_compare_reports"


# Compatibility facade. Wrappers resolve patchable process dependencies at call
# time so existing tests and external callers can keep monkeypatching this module.


def resolve_harvest_image_path(
    image_identity: str,
    image_root: Path = HARVEST_IMAGE_ROOT,
) -> Path:
    return _harvest.resolve_harvest_image_path(image_identity, image_root)


def configured_devkit_content_root() -> Path | None:
    return _devkit.configured_devkit_content_root(DEVKIT_CONTENT_ROOT_FILE)


def indexed_asset_metrics(
    asset_dir: Path,
) -> tuple[dict[str, int], int, str, dict[str, object]]:
    return _assets.indexed_asset_metrics(
        asset_dir,
        repository_opener=open_asset_repository,
    )


def uasset_graph_read_counts(asset_dir: Path) -> dict[str, int]:
    return _assets.uasset_graph_read_counts(
        asset_dir,
        repository_opener=open_asset_repository,
    )


def asset_summary(asset_dir: Path) -> dict[str, object]:
    return _assets.asset_summary(
        asset_dir,
        report_targets=REPORT_TARGETS,
        repository_opener=open_asset_repository,
    )


def list_assets() -> list[dict[str, object]]:
    return _assets.list_assets(
        CAPTURE_ROOT,
        report_targets=REPORT_TARGETS,
        repository_opener=open_asset_repository,
    )


def read_devkit_request() -> str:
    return _devkit.read_devkit_request(DEVKIT_REQUEST_PATH)


def write_devkit_request(asset_path: str) -> None:
    _devkit.write_devkit_request(
        asset_path,
        capture_root=CAPTURE_ROOT,
        request_path=DEVKIT_REQUEST_PATH,
    )


def mine_uasset_graph_candidates_for_request(
    asset_path: str,
    max_candidates: int = 1600,
) -> dict[str, object]:
    return _devkit.mine_uasset_graph_candidates_for_request(
        asset_path,
        max_candidates=max_candidates,
        capture_root=CAPTURE_ROOT,
        write_request=write_devkit_request,
        python_command=devkit_python_command,
        output_log_command=devkit_output_log_command,
        mine_candidates=mine_graph_candidates,
        write_candidate_files=write_graph_candidate_files,
    )


def read_native_class_for_request(asset_path: str) -> dict[str, object]:
    return _devkit.read_native_class_for_request(
        asset_path,
        content_root=configured_devkit_content_root(),
        probe_script=NATIVE_CLASS_PROBE_SCRIPT,
        probe_runner=run_native_class_probe,
    )


def read_uasset_graphs_for_request(
    asset_path: str,
    max_graphs: int = 0,
    report_level: str = "standard",
    analyze_after: bool = True,
    artifact_mode: str | None = None,
) -> dict[str, object]:
    return _devkit.read_uasset_graphs_for_request(
        asset_path,
        max_graphs=max_graphs,
        report_level=report_level,
        analyze_after=analyze_after,
        artifact_mode=artifact_mode,
        capture_root=CAPTURE_ROOT,
        write_request=write_devkit_request,
        summarize_asset=asset_summary,
        start_analysis_job=start_analyzer_job,
        object_path_resolver=object_path_to_uasset_path,
        read_graph_content=read_uasset_graph_content,
        write_graph_files=write_uasset_graph_read_files,
        native_class_reader=read_native_class_for_request,
    )


def devkit_python_command() -> str:
    return _devkit.devkit_python_command(PROJECT_ROOT, EXPORT_SCRIPT)


def devkit_output_log_command() -> str:
    return _devkit.devkit_output_log_command(PROJECT_ROOT, EXPORT_SCRIPT)


def resolve_asset_dir(raw_path: str) -> Path:
    return _reports.resolve_asset_dir(raw_path, project_root=PROJECT_ROOT)


def query_asset_evidence(
    capture_root: Path,
    asset_identifier: str,
    request: dict[str, object],
) -> dict[str, object]:
    return _reports.query_asset_evidence(
        capture_root,
        asset_identifier,
        request,
        repository_opener=open_asset_repository,
    )


def analyzer_command(
    asset_dir: Path,
    report_level: str,
    *,
    keep_stale_output: bool = False,
) -> list[str]:
    return _reports.analyzer_command(
        asset_dir,
        report_level,
        project_root=PROJECT_ROOT,
        keep_stale_output=keep_stale_output,
        python_executable=sys.executable,
    )


def report_generation_command(
    asset_dir: Path,
    report_level: str,
    *,
    keep_stale_output: bool = False,
) -> list[str]:
    return _reports.report_generation_command(
        asset_dir,
        report_level,
        project_root=PROJECT_ROOT,
        keep_stale_output=keep_stale_output,
        analyzer_command_builder=analyzer_command,
        repository_opener=open_asset_repository,
        indexed_evidence_declared=_indexed_evidence_declared,
        normalize_path=normalize_asset_path,
        python_executable=sys.executable,
    )


def run_analyzer(asset_dir: Path, report_level: str) -> dict[str, object]:
    return _reports.run_analyzer(
        asset_dir,
        report_level,
        project_root=PROJECT_ROOT,
        command_builder=analyzer_command,
        summarize_asset=asset_summary,
        timeout_seconds=JOB_TIMEOUT_SECONDS,
    )


def start_analyzer_job(
    asset_dir: Path,
    report_level: str,
    *,
    keep_stale_output: bool = False,
) -> dict[str, object]:
    return _reports.start_analyzer_job(
        asset_dir,
        report_level,
        keep_stale_output=keep_stale_output,
        command_builder=analyzer_command,
        summarize_asset=asset_summary,
        create_job=create_background_job,
    )


def start_report_generation_job(
    asset_dir: Path,
    report_level: str,
    *,
    keep_stale_output: bool = False,
) -> dict[str, object]:
    return _reports.start_report_generation_job(
        asset_dir,
        report_level,
        keep_stale_output=keep_stale_output,
        command_builder=report_generation_command,
        summarize_asset=asset_summary,
        create_job=create_background_job,
    )


def resolve_capture_target(body: dict[str, object]) -> tuple[Path, str]:
    return _captures.resolve_capture_target(
        body,
        capture_root=CAPTURE_ROOT,
        project_root=PROJECT_ROOT,
        resolve_asset=resolve_asset_dir,
    )


def capture_graph_from_request(body: dict[str, object]) -> dict[str, object]:
    return _captures.capture_graph_from_request(
        body,
        resolve_capture=resolve_capture_target,
        summarize_asset=asset_summary,
        start_analysis_job=start_analyzer_job,
    )


def asset_compare_command(
    old_asset_dir: Path,
    new_asset_dir: Path,
) -> tuple[list[str], Path]:
    return _captures.asset_compare_command(
        old_asset_dir,
        new_asset_dir,
        compare_root=DEFAULT_COMPARE_ROOT,
        project_root=PROJECT_ROOT,
        python_executable=sys.executable,
    )


def run_asset_compare_for_gui(
    old_asset_dir: Path,
    new_asset_dir: Path,
) -> dict[str, object]:
    return _captures.run_asset_compare_for_gui(
        old_asset_dir,
        new_asset_dir,
        project_root=PROJECT_ROOT,
        command_builder=asset_compare_command,
        timeout_seconds=JOB_TIMEOUT_SECONDS,
    )


def start_asset_compare_job(
    old_asset_dir: Path,
    new_asset_dir: Path,
) -> dict[str, object]:
    return _captures.start_asset_compare_job(
        old_asset_dir,
        new_asset_dir,
        command_builder=asset_compare_command,
        create_job=create_background_job,
    )


def knowledge_base_summary() -> dict[str, object]:
    return _knowledge.knowledge_base_summary(KNOWLEDGE_ROOT)


def knowledge_command(
    focus: str = "gigantoraptor",
    assets: list[str] | None = None,
) -> list[str]:
    return _knowledge.knowledge_command(
        focus,
        assets,
        project_root=PROJECT_ROOT,
        configured_content_root=configured_devkit_content_root,
        python_executable=sys.executable,
    )


def start_knowledge_base_job(
    focus: str = "gigantoraptor",
    assets: list[str] | None = None,
) -> dict[str, object]:
    return _knowledge.start_knowledge_base_job(
        focus,
        assets,
        command_builder=knowledge_command,
        summarize_knowledge=knowledge_base_summary,
        create_job=create_background_job,
    )


def priority_read_command(
    limit: int = 25,
    *,
    analyze: bool = True,
    rebuild_knowledge: bool = True,
) -> list[str]:
    return _knowledge.priority_read_command(
        limit,
        analyze=analyze,
        rebuild_knowledge=rebuild_knowledge,
        project_root=PROJECT_ROOT,
        python_executable=sys.executable,
    )


def start_priority_read_job(
    limit: int = 25,
    *,
    analyze: bool = True,
) -> dict[str, object]:
    return _knowledge.start_priority_read_job(
        limit,
        analyze=analyze,
        command_builder=priority_read_command,
        summarize_knowledge=knowledge_base_summary,
        create_job=create_background_job,
    )


def resolve_knowledge_target(target: str) -> Path:
    return _knowledge.resolve_knowledge_target(
        target,
        knowledge_root=KNOWLEDGE_ROOT,
    )


def query_harvest_nodes_for_request(query: str) -> dict[str, object]:
    return _harvest.query_harvest_nodes_for_request(
        query,
        repository=HARVEST_REPOSITORY,
    )


def query_harvest_node_for_request(node_id: str) -> dict[str, object]:
    return _harvest.query_harvest_node_for_request(
        node_id,
        repository=HARVEST_REPOSITORY,
    )


def query_harvest_ranking_for_request(query: str) -> dict[str, object]:
    return _harvest.query_harvest_ranking_for_request(
        query,
        repository=HARVEST_REPOSITORY,
    )


def query_harvest_creatures_for_request(query: str) -> dict[str, object]:
    return _harvest.query_harvest_creatures_for_request(
        query,
        repository=HARVEST_REPOSITORY,
    )


def query_harvest_creature_specialties_for_request(
    species_key: str,
    query: str,
) -> dict[str, object]:
    return _harvest.query_harvest_creature_specialties_for_request(
        species_key,
        query,
        repository=HARVEST_REPOSITORY,
    )


def query_harvest_build_for_request(query: str) -> dict[str, object] | None:
    return _harvest.query_harvest_build_for_request(
        query,
        build_manager=HARVEST_BUILD_MANAGER,
    )


def start_harvest_build_for_request(
    body: dict[str, object],
) -> dict[str, object]:
    return _harvest.start_harvest_build_for_request(
        body,
        build_manager=HARVEST_BUILD_MANAGER,
    )


def cancel_harvest_build_for_request(job_id: str) -> dict[str, object]:
    return _harvest.cancel_harvest_build_for_request(
        job_id,
        build_manager=HARVEST_BUILD_MANAGER,
    )


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


def kb_get_payload(path: str, query: str) -> dict[str, object] | None:
    return _kb_routes.kb_get_payload(
        path,
        query,
        service=KB_VNEXT_SERVICE,
        get_job=get_job,
    )


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
            try:
                blueprint_payload = blueprint_get_payload(
                    parsed.path,
                    parsed.query,
                    capture_root=CAPTURE_ROOT,
                )
            except ApiProblem:
                raise
            except Exception as exc:
                raise problem(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "BLUEPRINT_INTERNAL_ERROR",
                    "Blueprint request failed.",
                ) from exc
            if blueprint_payload is not None:
                self.send_json(blueprint_payload.payload, blueprint_payload.status)
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
                requested_target = str(body.get("target") or "")
                if requested_target == "agent_index":
                    target_path, evidence_metadata = resolve_report_source(
                        asset_dir,
                        requested_target,
                    )
                else:
                    target_path = resolve_target(asset_dir, requested_target, OPEN_TARGETS)
                    evidence_metadata = {}
                open_path(target_path)
                self.send_json(
                    {
                        "ok": True,
                        "path": str(target_path),
                        **evidence_metadata,
                    }
                )
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
            target = values.get("target", [""])[0]
            if target == "agent_index":
                target_path, content, evidence_metadata = read_report_source(
                    asset_dir,
                    target,
                )
            else:
                target_path = resolve_target(asset_dir, target, REPORT_TARGETS)
                evidence_metadata = {}
                if not target_path.is_file():
                    self.send_error_json("Report file does not exist.", HTTPStatus.NOT_FOUND)
                    return
                content = target_path.read_text(
                    encoding="utf-8-sig",
                    errors="replace",
                )
            self.send_json(
                {
                    "ok": True,
                    "path": str(target_path),
                    "content": content,
                    **evidence_metadata,
                }
            )
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
