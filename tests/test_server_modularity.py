from __future__ import annotations

import ast
import importlib
import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from blueprint_server.responses import (  # noqa: E402
    error_payload,
    prepare_json_response,
    static_content_type,
)
from blueprint_server.routes_state import (  # noqa: E402
    StateRoute,
    state_route_payload,
)
import blueprint_tool_server as tool_server  # noqa: E402


DOMAIN_MODULE_NAMES = (
    "assets",
    "devkit",
    "reports",
    "captures",
    "knowledge",
    "harvest",
    "kb_routes",
)

COMPATIBILITY_EXPORTS = (
    "REPORT_TARGETS",
    "OPEN_TARGETS",
    "KNOWLEDGE_TARGETS",
    "read_json_file",
    "collection_size",
    "count_defaults",
    "count_components",
    "component_source_counts",
    "parse_devkit_report_counts",
    "devkit_export_quality",
    "export_quality_summary",
    "newest_mtime",
    "iso_time",
    "graph_name_key",
    "captured_graph_keys",
    "graph_count",
    "graph_queue_count",
    "graph_queue_counts",
    "graph_candidate_count",
    "uasset_structure_counts",
    "_indexed_evidence_declared",
    "_evidence_public_metadata",
    "indexed_asset_metrics",
    "uasset_graph_read_counts",
    "asset_summary",
    "list_assets",
    "normalize_asset_path",
    "configured_devkit_content_root",
    "read_devkit_request",
    "write_devkit_request",
    "mine_uasset_graph_candidates_for_request",
    "read_uasset_graphs_for_request",
    "devkit_python_command",
    "devkit_output_log_command",
    "resolve_asset_dir",
    "resolve_target",
    "query_asset_evidence",
    "query_report_for_request",
    "parse_report_query_int",
    "open_path",
    "analyzer_command",
    "report_generation_command",
    "run_analyzer",
    "start_analyzer_job",
    "start_report_generation_job",
    "markdown_table_cells",
    "normalize_note_function_name",
    "missing_functions_from_context_json",
    "missing_functions_from_report",
    "existing_note_function_names",
    "append_notes_for_functions",
    "resolve_capture_target",
    "capture_graph_from_request",
    "asset_compare_command",
    "run_asset_compare_for_gui",
    "start_asset_compare_job",
    "knowledge_base_summary",
    "knowledge_command",
    "start_knowledge_base_job",
    "priority_read_command",
    "start_priority_read_job",
    "resolve_knowledge_target",
    "resolve_harvest_image_path",
    "_harvest_dataset_problem",
    "query_harvest_nodes_for_request",
    "query_harvest_node_for_request",
    "_harvest_runtime_ranking_options",
    "_harvest_runtime_profile_problem",
    "query_harvest_ranking_for_request",
    "query_harvest_creatures_for_request",
    "query_harvest_creature_specialties_for_request",
    "_harvest_build_problem",
    "query_harvest_build_for_request",
    "start_harvest_build_for_request",
    "cancel_harvest_build_for_request",
    "_kb_api_problem",
    "_kb_query_value",
    "kb_get_payload",
)


def domain_source(module_name: str) -> str:
    return (SCRIPTS / "blueprint_server" / f"{module_name}.py").read_text(
        encoding="utf-8"
    )


class ServerModularityTests(unittest.TestCase):
    def test_json_response_is_prepared_without_handler_state(self) -> None:
        response = prepare_json_response(
            {"ok": True, "label": "资源"},
            HTTPStatus.ACCEPTED,
            close_connection=True,
        )

        self.assertEqual(response.status, HTTPStatus.ACCEPTED)
        self.assertEqual(
            response.headers,
            (
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(response.body))),
                ("Cache-Control", "no-store"),
                ("Connection", "close"),
            ),
        )
        self.assertEqual(
            json.loads(response.body.decode("utf-8")),
            {"ok": True, "label": "资源"},
        )
        self.assertNotIn(b"\n", response.body)

    def test_response_helpers_preserve_public_error_and_mime_contracts(self) -> None:
        self.assertEqual(
            error_payload("No such report."),
            {"ok": False, "error": "No such report."},
        )
        self.assertEqual(
            static_content_type("text/html"),
            "text/html; charset=utf-8",
        )
        self.assertEqual(static_content_type("image/jpeg"), "image/jpeg")
        self.assertEqual(
            static_content_type(None),
            "application/octet-stream",
        )

    def test_state_route_builds_dynamic_state_and_only_matches_state_url(self) -> None:
        asset_snapshots = iter(
            [
                [{"name": "first"}],
                [{"name": "second"}],
            ]
        )
        route = StateRoute(
            version="1.2.3",
            project_root=Path("project"),
            capture_root=Path("captures"),
            devkit_request_path=Path("captures/request.json"),
            list_assets=lambda: next(asset_snapshots),
            knowledge_base_summary=lambda: {"exists": True},
            read_devkit_request=lambda: "/Game/Test/Asset.Asset",
            devkit_python_command=lambda: "python-command",
            devkit_output_log_command=lambda: "output-command",
        )

        first = state_route_payload("/api/state", route.state)
        second = state_route_payload("/api/state", route.state)

        self.assertEqual(first["ok"], True)
        self.assertEqual(first["version"], "1.2.3")
        self.assertEqual(first["assets"], [{"name": "first"}])
        self.assertEqual(second["assets"], [{"name": "second"}])
        self.assertIsNone(state_route_payload("/api/other", route.state))

    def test_legacy_server_entry_delegates_response_and_state_route_work(self) -> None:
        source = (SCRIPTS / "blueprint_tool_server.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "from blueprint_server.responses import",
            source,
        )
        self.assertIn(
            "from blueprint_server.routes_state import",
            source,
        )
        self.assertNotIn("def encode_json_response(", source)
        self.assertNotIn("def static_content_type(", source)
        self.assertIn(
            "state_route_payload(parsed.path, api_state)",
            source,
        )

    def test_legacy_state_entry_resolves_patchable_dependencies_per_call(self) -> None:
        with (
            patch.object(
                tool_server,
                "list_assets",
                return_value=[{"name": "patched"}],
            ),
            patch.object(
                tool_server,
                "knowledge_base_summary",
                return_value={"exists": False},
            ),
            patch.object(
                tool_server,
                "read_devkit_request",
                return_value="/Game/Patched.Asset",
            ),
        ):
            state = tool_server.api_state()

        self.assertEqual(state["assets"], [{"name": "patched"}])
        self.assertEqual(state["knowledgeBase"], {"exists": False})
        self.assertEqual(state["devkitAssetPath"], "/Game/Patched.Asset")

    def test_domain_modules_import_without_the_legacy_server(self) -> None:
        forbidden = {
            "BaseHTTPRequestHandler",
            "ThreadingHTTPServer",
            "ControlCenterHandler",
        }
        for module_name in DOMAIN_MODULE_NAMES:
            with self.subTest(module=module_name):
                module = importlib.import_module(
                    f"blueprint_server.{module_name}"
                )
                self.assertEqual(
                    module.__name__,
                    f"blueprint_server.{module_name}",
                )
                tree = ast.parse(domain_source(module_name))
                imported_modules: set[str] = set()
                bound_names: set[str] = set()
                defined_names = {
                    node.name
                    for node in tree.body
                    if isinstance(
                        node,
                        (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                }
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imported_modules.add(alias.name)
                            bound_names.add(
                                alias.asname or alias.name.split(".")[0]
                            )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imported_modules.add(node.module)
                        for alias in node.names:
                            bound_names.add(alias.asname or alias.name)
                self.assertNotIn(
                    "blueprint_tool_server",
                    imported_modules,
                )
                self.assertFalse(forbidden & (bound_names | defined_names))

    def test_legacy_entry_reexports_moved_compatibility_helpers(self) -> None:
        missing = [
            name
            for name in COMPATIBILITY_EXPORTS
            if not hasattr(tool_server, name)
        ]
        self.assertEqual(missing, [])

    def test_legacy_entry_owns_handler_factory_and_cli(self) -> None:
        self.assertEqual(
            tool_server.ControlCenterHandler.__module__,
            "blueprint_tool_server",
        )
        for name in (
            "create_control_center_server",
            "parse_args",
            "main",
        ):
            self.assertEqual(
                getattr(tool_server, name).__module__,
                "blueprint_tool_server",
            )
        for module_name in DOMAIN_MODULE_NAMES:
            source = domain_source(module_name)
            tree = ast.parse(source)
            defined = {
                node.name
                for node in tree.body
                if isinstance(
                    node,
                    (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                )
            }
            self.assertFalse(
                defined
                & {
                    "ControlCenterHandler",
                    "create_control_center_server",
                    "parse_args",
                    "main",
                }
            )

    def test_domain_import_graph_is_acyclic(self) -> None:
        graph: dict[str, set[str]] = {
            name: set() for name in DOMAIN_MODULE_NAMES
        }
        for module_name in DOMAIN_MODULE_NAMES:
            tree = ast.parse(domain_source(module_name))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 1
                    and node.module
                ):
                    dependency = node.module.split(".", 1)[0]
                    if dependency in graph:
                        graph[module_name].add(dependency)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module_name: str) -> None:
            if module_name in visiting:
                self.fail(f"domain import cycle includes {module_name}")
            if module_name in visited:
                return
            visiting.add(module_name)
            for dependency in graph[module_name]:
                visit(dependency)
            visiting.remove(module_name)
            visited.add(module_name)

        for module_name in DOMAIN_MODULE_NAMES:
            visit(module_name)

    def test_process_singletons_are_created_only_by_legacy_entry(self) -> None:
        constructors = {
            "HarvestNodeRepository",
            "HarvestBuildJobManager",
            "VNextKnowledgeService",
            "LegacyVNextComparator",
        }
        source_paths = [SCRIPTS / "blueprint_tool_server.py"] + [
            SCRIPTS / "blueprint_server" / f"{name}.py"
            for name in DOMAIN_MODULE_NAMES
        ]
        locations: dict[str, list[str]] = {
            name: [] for name in constructors
        }
        for source_path in source_paths:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in constructors
                ):
                    locations[node.func.id].append(source_path.name)
        self.assertEqual(
            locations,
            {
                name: ["blueprint_tool_server.py"]
                for name in constructors
            },
        )


if __name__ == "__main__":
    unittest.main()
