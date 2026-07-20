import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RUNTIME_PYTHON = ROOT / "runtime" / "python" / "python.exe"
PYTHON = RUNTIME_PYTHON if RUNTIME_PYTHON.is_file() else Path(sys.executable)

sys.path.insert(0, str(SCRIPTS))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.evidence_query import EvidenceQueryService  # noqa: E402
from blueprint_translator.evidence_writer import migrate_asset_capture  # noqa: E402


MIGRATE_SCRIPT = SCRIPTS / "migrate_capture_evidence.py"
QUERY_SCRIPT = SCRIPTS / "query_blueprint_evidence.py"
REBUILD_INDEXES_SCRIPT = SCRIPTS / "rebuild_evidence_indexes.py"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _make_legacy_capture(root: Path) -> tuple[Path, dict[Path, bytes]]:
    """Create one small v1 capture with a searchable two-node data wire."""
    asset_dir = root / "CliFixture"
    graph_path = asset_dir / "graphs_from_uasset" / "EventGraph_7.json"
    graph_payload = {
        "metadata": {
            "asset_name": "CliFixture",
            "graph_name": "EventGraph",
            "graph_type": "EventGraph",
            "uasset_export_index": 7,
            "uasset_read_status": "complete",
            "confidence": "high",
            "node_count": 2,
            "pin_count": 2,
            "link_count": 1,
        },
        "nodes": [
            {
                "index": 1,
                "name": "EnergySource",
                "label": "EnergySource",
                "node_type": "K2Node_VariableGet",
                "variable": "Energy",
                "source": "uasset_binary",
                "confidence": "high",
                "pins": [
                    {
                        "id": "pin-out",
                        "name": "Energy",
                        "direction": "EGPD_Output",
                        "category": "float",
                        "default": "1.0",
                        "source": "uasset_custom_pin_scan",
                        "confidence": "high",
                        "links": [
                            {
                                "target_node": "ConsumeEnergy",
                                "target_pin_id": "pin-in",
                                "target_package_index": 22,
                                "resolution_status": "resolved_pin",
                                "status": "resolved_node",
                                "kind": "data",
                                "source": "uasset_pin_package_index_scan",
                                "confidence": "high",
                            }
                        ],
                    }
                ],
            },
            {
                "index": 2,
                "export_index": 22,
                "name": "ConsumeEnergy",
                "label": "ConsumeEnergy",
                "node_type": "K2Node_CallFunction",
                "function": "ConsumeEnergy",
                "source": "uasset_binary",
                "confidence": "high",
                "pins": [
                    {
                        "id": "pin-in",
                        "name": "Amount",
                        "direction": "EGPD_Input",
                        "category": "float",
                        "default": "0.0",
                        "source": "uasset_custom_pin_scan",
                        "confidence": "high",
                        "links": [],
                    }
                ],
            },
        ],
        "diagnostics": {
            "confidence_level": "high",
            "unsupported_node_types": [],
            "unresolved_links": [],
            "warnings": [],
        },
    }
    _write_json(graph_path, graph_payload)
    manifest_path = asset_dir / "graphs_from_uasset_manifest.json"
    _write_json(
        manifest_path,
        {
            "schema": "blueprint-translator.graphs-from-uasset-manifest.v1",
            "asset_name": "CliFixture",
            "asset_path": "/Game/Test/CliFixture.CliFixture",
            "source_graph_count": 1,
            "graph_file_count": 1,
            "files": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "complete",
                    "confidence": "high",
                    "path": "graphs_from_uasset/EventGraph_7.json",
                }
            ],
        },
    )
    root_manifest_path = asset_dir / "manifest.json"
    _write_json(
        root_manifest_path,
        {
            "asset_name": "CliFixture",
            "asset_path": "/Game/Test/CliFixture.CliFixture",
            "graphs": [],
        },
    )
    stale_path = asset_dir / "graphs_from_uasset" / "stale-unmanifested.json"
    stale_path.write_bytes(b"legacy-stale-file-must-survive")
    legacy_files = {
        graph_path: graph_path.read_bytes(),
        manifest_path: manifest_path.read_bytes(),
        root_manifest_path: root_manifest_path.read_bytes(),
        stale_path: stale_path.read_bytes(),
    }
    return asset_dir, legacy_files


def _run(script: Path, *arguments: object) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [str(PYTHON), str(script), *(str(argument) for argument in arguments)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )


def _compact_token_count(payload: object) -> int:
    return estimate_tokens(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


class EvidenceCliTests(unittest.TestCase):
    def _successful_json(self, process: subprocess.CompletedProcess[str]) -> dict[str, object]:
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"CLI stdout must be one JSON object: {exc}\nstdout={process.stdout!r}")
        self.assertIsInstance(payload, dict)
        return payload

    def test_migration_cli_defaults_to_indexed_accepts_dual_and_never_removes_legacy_files(self):
        for requested_mode, expected_mode in ((None, "indexed"), ("dual", "dual"), ("indexed", "indexed")):
            with self.subTest(artifact_mode=requested_mode), tempfile.TemporaryDirectory() as temp_dir:
                asset_dir, legacy_files = _make_legacy_capture(Path(temp_dir))

                arguments: list[object] = ["--asset-dir", asset_dir]
                if requested_mode is not None:
                    arguments.extend(["--artifact-mode", requested_mode])
                process = _run(MIGRATE_SCRIPT, *arguments)

                result = self._successful_json(process)
                self.assertEqual(result["artifact_mode"], expected_mode)
                self.assertFalse(result["legacy_artifacts_deleted"])
                expected_paths = {
                    "database_path": asset_dir / "evidence" / "evidence.sqlite",
                    "manifest_path": asset_dir / "evidence" / "manifest.json",
                    "agent_index_path": asset_dir / "output" / "agent_index.md",
                }
                for key, expected_path in expected_paths.items():
                    self.assertTrue(expected_path.is_file(), expected_path)
                    self.assertEqual(Path(str(result[key])).resolve(), expected_path.resolve())
                for legacy_path, original_content in legacy_files.items():
                    self.assertEqual(legacy_path.read_bytes(), original_content, legacy_path)

    def test_rebuild_indexes_cli_refreshes_every_selected_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            capture_root = Path(temp_dir) / "captures"
            first, _first_legacy = _make_legacy_capture(capture_root / "first")
            second, _second_legacy = _make_legacy_capture(capture_root / "second")
            first_result = migrate_asset_capture(first)
            second_result = migrate_asset_capture(second)
            for result in (first_result, second_result):
                Path(str(result["agent_index_path"])).write_text("# stale index\n", encoding="utf-8")

            process = _run(
                REBUILD_INDEXES_SCRIPT,
                "--capture-root",
                capture_root,
                "--all",
                "--expected-asset-count",
                2,
            )
            payload = self._successful_json(process)

            self.assertEqual(payload["selected"], 2)
            self.assertEqual(payload["passed"], 2)
            self.assertEqual(payload["failed"], 0)
            for result in (first_result, second_result):
                index_text = Path(str(result["agent_index_path"])).read_text(encoding="utf-8")
                self.assertIn(str(result["revision_id"]), index_text)
                self.assertIn("Evidence gaps:", index_text)

    def test_overview_and_search_json_match_the_shared_service_and_obey_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, _legacy_files = _make_legacy_capture(Path(temp_dir))
            migration = migrate_asset_capture(asset_dir)
            database_path = Path(str(migration["database_path"]))
            budget = 800
            with EvidenceQueryService.open(database_path) as service:
                expected_overview = service.query(
                    {"operation": "overview", "budgetTokens": budget}
                )
                expected_search = service.query(
                    {
                        "operation": "search",
                        "query": "EnergySource",
                        "budgetTokens": budget,
                    }
                )

            overview = self._successful_json(
                _run(QUERY_SCRIPT, "--asset-dir", asset_dir, "overview", "--budget", budget)
            )
            search = self._successful_json(
                _run(
                    QUERY_SCRIPT,
                    "--asset-dir",
                    asset_dir,
                    "search",
                    "--query",
                    "EnergySource",
                    "--budget",
                    budget,
                )
            )

        for key in ("operation", "asset", "summary", "coverage", "budget"):
            self.assertEqual(overview[key], expected_overview[key], key)
        for key in ("operation", "asset", "query", "items", "coverage", "page", "budget"):
            self.assertEqual(search[key], expected_search[key], key)
        self.assertLessEqual(_compact_token_count(overview), budget)
        self.assertLessEqual(_compact_token_count(search), budget)

    def test_entity_neighborhood_trace_and_gaps_subcommands_map_to_service_operations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, _legacy_files = _make_legacy_capture(Path(temp_dir))
            migration = migrate_asset_capture(asset_dir)
            with EvidenceQueryService.open(Path(str(migration["database_path"]))) as service:
                search = service.query(
                    {
                        "operation": "search",
                        "query": "EnergySource",
                        "kinds": ["node"],
                        "budgetTokens": 1200,
                    }
                )
            node_ref = next(
                str(item["ref"])
                for item in search["items"]
                if item.get("kind") == "node" and item.get("name") == "EnergySource"
            )

            invocations = (
                ("entity", "--id", node_ref, "--budget", 1200),
                (
                    "neighborhood",
                    "--id",
                    node_ref,
                    "--hops",
                    1,
                    "--direction",
                    "both",
                    "--budget",
                    1600,
                ),
                (
                    "trace",
                    "--id",
                    node_ref,
                    "--hops",
                    1,
                    "--direction",
                    "downstream",
                    "--budget",
                    1600,
                ),
                ("gaps", "--budget", 800),
            )
            for invocation in invocations:
                with self.subTest(operation=invocation[0]):
                    payload = self._successful_json(
                        _run(QUERY_SCRIPT, "--asset-dir", asset_dir, *invocation)
                    )
                    self.assertEqual(payload["operation"], invocation[0])
                    self.assertEqual(payload["asset"]["revisionId"], migration["revision_id"])

    def test_gaps_graph_name_scope_resolves_only_one_exact_graph_ref(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, _legacy_files = _make_legacy_capture(Path(temp_dir))
            migration = migrate_asset_capture(asset_dir)
            with EvidenceQueryService.open(Path(str(migration["database_path"]))) as service:
                graph_search = service.query(
                    {
                        "operation": "search",
                        "query": "EventGraph",
                        "kinds": ["graph"],
                        "budgetTokens": 1200,
                    }
                )
            graph_ref = next(
                str(item["ref"])
                for item in graph_search["items"]
                if item.get("name") == "EventGraph"
            )

            by_name = self._successful_json(
                _run(
                    QUERY_SCRIPT,
                    "--asset-dir",
                    asset_dir,
                    "gaps",
                    "--scope",
                    "graph:EventGraph",
                    "--budget",
                    2000,
                )
            )
            by_ref = self._successful_json(
                _run(
                    QUERY_SCRIPT,
                    "--asset-dir",
                    asset_dir,
                    "gaps",
                    "--scope",
                    graph_ref,
                    "--budget",
                    2000,
                )
            )

        self.assertEqual(by_name["items"], by_ref["items"])
        self.assertEqual(by_name["coverage"], by_ref["coverage"])

    def test_invalid_artifact_mode_and_budget_exit_nonzero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, legacy_files = _make_legacy_capture(Path(temp_dir))

            invalid_mode = _run(
                MIGRATE_SCRIPT,
                "--asset-dir",
                asset_dir,
                "--artifact-mode",
                "delete-legacy",
            )
            self.assertNotEqual(invalid_mode.returncode, 0)
            self.assertFalse((asset_dir / "evidence" / "evidence.sqlite").exists())
            for legacy_path, original_content in legacy_files.items():
                self.assertEqual(legacy_path.read_bytes(), original_content, legacy_path)

            migrate_asset_capture(asset_dir)
            invalid_budget = _run(
                QUERY_SCRIPT,
                "--asset-dir",
                asset_dir,
                "overview",
                "--budget",
                1,
            )
            self.assertNotEqual(invalid_budget.returncode, 0)
            self.assertIn("at least 500", invalid_budget.stderr)


if __name__ == "__main__":
    unittest.main()
