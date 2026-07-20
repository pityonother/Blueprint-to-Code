import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import blueprint_tool_server as tool_server  # noqa: E402
from blueprint_translator.evidence_schema import ensure_evidence_schema  # noqa: E402
from blueprint_tool_server import (
    OPEN_TARGETS,
    REPORT_TARGETS,
    append_notes_for_functions,
    asset_summary,
    cancel_job,
    create_background_job,
    get_job,
    missing_functions_from_report,
    normalize_asset_path,
    parse_report_query_int,
    priority_read_command,
    query_report_for_request,
    read_uasset_graphs_for_request,
    report_generation_command,
)


def _write_indexed_asset(
    asset_dir: Path,
    statuses: list[str],
    *,
    link_observation_count: int = 0,
    diagnostics: list[tuple[str, str, int]] | None = None,
) -> None:
    database_path = asset_dir / "evidence" / "evidence.sqlite"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        ensure_evidence_schema(connection)
        asset_id = "asset-tool-server-v2"
        revision_id = "revision-tool-server-v2"
        connection.execute(
            "INSERT INTO asset_revisions("
            "revision_id, asset_id, asset_name, object_path, source_fingerprint, "
            "parser_version, schema_version, generated_at, uasset_path"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id,
                asset_id,
                asset_dir.name,
                f"/Game/Test/{asset_dir.name}.{asset_dir.name}",
                "fixture-fingerprint",
                "fixture-parser",
                "ark.blueprint.evidence.v2",
                "2026-07-19T00:00:00+00:00",
                "",
            ),
        )
        graph_refs: list[str] = []
        for export_index, status in enumerate(statuses):
            graph_ref = f"bp://{asset_id}@{revision_id}/g/{export_index}"
            graph_refs.append(graph_ref)
            connection.execute(
                "INSERT INTO graphs("
                "graph_ref, revision_id, export_index, name, graph_type, status, confidence"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    graph_ref,
                    revision_id,
                    export_index,
                    f"Graph_{export_index}",
                    "Function",
                    status,
                    "high" if status == "complete" else "medium",
                ),
            )
        for ordinal in range(link_observation_count):
            connection.execute(
                "INSERT INTO edge_observations(observation_ref, graph_ref) VALUES (?, ?)",
                (f"{graph_refs[0]}/observation/{ordinal}", graph_refs[0]),
            )
        for ordinal, (status, reason_code, graph_index) in enumerate(diagnostics or []):
            scope_ref = graph_refs[graph_index]
            connection.execute(
                "INSERT INTO diagnostics("
                "diagnostic_ref, revision_id, scope_kind, scope_ref, status, reason_code"
                ") VALUES (?, ?, 'graph', ?, ?, ?)",
                (f"{scope_ref}/diagnostic/{ordinal}", revision_id, scope_ref, status, reason_code),
            )
        connection.commit()
    finally:
        connection.close()
    output_dir = asset_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "agent_index.md").write_text("# Indexed evidence\n", encoding="utf-8")


def wait_for_job(job_id: str, timeout_seconds: float = 5.0) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        job = get_job(job_id)
        if str(job.get("status")) in {"succeeded", "failed", "cancelled", "timed_out"}:
            return job
        time.sleep(0.05)
    return get_job(job_id)


class ToolServerTests(unittest.TestCase):
    def test_list_assets_discovers_v2_only_asset_and_uses_unbounded_graph_summaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            capture_root = Path(temp_dir) / "captures"
            asset_dir = capture_root / "IndexedOnly"
            statuses = ["complete"] * 120 + ["heuristic"] * 4 + ["partial", "needs_clipboard"]
            _write_indexed_asset(
                asset_dir,
                statuses,
                link_observation_count=3,
                diagnostics=[
                    ("SOURCE_NOT_AVAILABLE", "external_callable_body_not_in_asset", 0),
                    ("NOT_RECOVERED", "need_manual_clipboard", 125),
                ],
            )

            with patch.object(tool_server, "CAPTURE_ROOT", capture_root):
                assets = tool_server.list_assets()

        self.assertEqual(len(assets), 1)
        summary = assets[0]
        self.assertEqual(summary["name"], "IndexedOnly")
        self.assertEqual(summary["graphs"], 126)
        self.assertEqual(summary["uassetReadGraphCount"], 126)
        self.assertEqual(summary["uassetReadCompleteCount"], 120)
        self.assertEqual(summary["uassetReadPartialCount"], 5)
        self.assertEqual(summary["uassetReadNeedsClipboardCount"], 1)
        self.assertEqual(summary["uassetReadLinkCount"], 3)

    def test_frontend_requests_indexed_artifacts_without_hardcoded_dual_mode(self):
        source = (ROOT / "src" / "main.ts").read_text(encoding="utf-8")

        self.assertNotIn("artifactMode: 'dual'", source)
        self.assertIn("const DEFAULT_ARTIFACT_MODE = 'indexed'", source)
        self.assertIn("artifactMode: DEFAULT_ARTIFACT_MODE", source)
        self.assertIn("if (payload.graphReportPath)", source)
        self.assertIn("历史/按需报告", source)

    def test_backend_omitted_mode_uses_indexed_and_skips_legacy_analyzer(self):
        fake_paths = {
            "asset_dir": "C:/capture/IndexedDefault",
            "artifact_mode": "indexed",
            "evidence_database": "C:/capture/IndexedDefault/evidence/evidence.sqlite",
            "evidence_manifest": "C:/capture/IndexedDefault/evidence/manifest.json",
            "agent_index": "C:/capture/IndexedDefault/output/agent_index.md",
            "revision_id": "revision-indexed-default",
        }
        payload = {
            "graph_count": 1,
            "node_count": 2,
            "pin_count": 3,
            "link_count": 1,
            "status_counts": {"complete": 1},
            "uexp_path": "",
        }

        with (
            patch.object(tool_server, "object_path_to_uasset_path", return_value=(Path("C:/DevKit/Test.uasset"), [])),
            patch.object(tool_server, "read_uasset_graph_content", return_value=payload),
            patch.object(tool_server, "write_uasset_graph_read_files", return_value=fake_paths) as writer,
            patch.object(tool_server, "write_devkit_request"),
            patch.object(tool_server, "asset_summary", return_value={}),
            patch.object(tool_server, "start_analyzer_job") as analyzer,
        ):
            result = read_uasset_graphs_for_request(
                "/Game/Test/IndexedDefault.IndexedDefault",
                analyze_after=True,
                artifact_mode=None,
            )

        self.assertEqual(result["artifactMode"], "indexed")
        self.assertIn("analysisSkipped", result)
        self.assertNotIn("analysisJob", result)
        analyzer.assert_not_called()
        self.assertEqual(writer.call_args.kwargs["artifact_mode"], "indexed")

    def test_indexed_report_generation_rereads_same_object_path_in_dual_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "captures" / "IndexedCurrent"
            _write_indexed_asset(asset_dir, ["complete"])

            command = report_generation_command(asset_dir, "standard")

        self.assertIn("--asset-binary", command)
        self.assertEqual(
            command[command.index("--asset-binary") + 1],
            "/Game/Test/IndexedCurrent.IndexedCurrent",
        )
        self.assertEqual(command[command.index("--artifact-mode") + 1], "dual")
        self.assertEqual(command[command.index("--capture-root") + 1], str(asset_dir.parent))
        self.assertNotIn("--asset-dir", command)

    def test_legacy_report_generation_keeps_existing_asset_analyzer_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "LegacyOnly"
            asset_dir.mkdir()

            command = report_generation_command(asset_dir, "standard")

        self.assertIn("--asset-dir", command)
        self.assertNotIn("--asset-binary", command)

    def test_indexed_summary_marks_preserved_legacy_reports_as_potentially_historical(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "IndexedWithLegacy"
            _write_indexed_asset(asset_dir, ["complete"])
            (asset_dir / "output" / "behavior_summary.md").write_text(
                "# Preserved legacy report\n",
                encoding="utf-8",
            )

            summary = asset_summary(asset_dir)

        self.assertTrue(summary["reports"]["agent_index"])
        self.assertTrue(summary["reports"]["behavior_summary"])
        self.assertTrue(summary["preservedLegacyReports"])

    def test_asset_summary_prefers_current_v2_revision_without_pruning_legacy_graphs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "DualAsset"
            _write_indexed_asset(asset_dir, ["complete", "complete"])
            graphs_dir = asset_dir / "graphs"
            graphs_dir.mkdir()
            legacy_paths = [graphs_dir / f"LegacyGraph_{ordinal}.txt" for ordinal in range(3)]
            for path in legacy_paths:
                path.write_text(f"legacy sentinel {path.stem}\n", encoding="utf-8")

            summary = asset_summary(asset_dir)
            legacy_contents = [path.read_text(encoding="utf-8") for path in legacy_paths]

        self.assertEqual(summary["graphs"], 2)
        self.assertEqual(legacy_contents, [f"legacy sentinel LegacyGraph_{ordinal}\n" for ordinal in range(3)])

    def test_v2_manual_queue_does_not_repeat_a_preserved_clipboard_capture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "DualAsset"
            _write_indexed_asset(asset_dir, ["needs_clipboard"])
            graphs_dir = asset_dir / "graphs"
            graphs_dir.mkdir()
            captured_graph = graphs_dir / "Graph_0.txt"
            captured_graph.write_text("Begin Object\n", encoding="utf-8")

            summary = asset_summary(asset_dir)
            captured_text = captured_graph.read_text(encoding="utf-8")

        self.assertEqual(summary["uassetReadNeedsClipboardCount"], 0)
        self.assertEqual(captured_text, "Begin Object\n")

    def test_normalize_asset_path_accepts_devkit_reference(self):
        raw = "Blueprint'/Game/Mods/Test/MilkGlider_Character_BP.MilkGlider_Character_BP_C'"
        self.assertEqual(
            normalize_asset_path(raw),
            "/Game/Mods/Test/MilkGlider_Character_BP.MilkGlider_Character_BP",
        )

    def test_normalize_asset_path_accepts_mod_relative_reference(self):
        raw = "Kaminan_server/SkinBuff/SkinBuffHuman/MetalShield/BuffSkin_MetalShield.BuffSkin_MetalShield"
        self.assertEqual(
            normalize_asset_path(raw),
            "/Game/Mods/Kaminan_server/SkinBuff/SkinBuffHuman/MetalShield/BuffSkin_MetalShield.BuffSkin_MetalShield",
        )

    def test_asset_summary_counts_graphs_defaults_and_components(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "TestAsset"
            graphs_dir = asset_dir / "graphs"
            graphs_dir.mkdir(parents=True)
            (graphs_dir / "EventGraph.txt").write_text("Begin Object Class=/Script/BlueprintGraph.K2Node_Event\n", encoding="utf-8")
            (asset_dir / "defaults.json").write_text(
                json.dumps({"defaults": [{"name": "Speed"}, {"name": "Glide"}]}),
                encoding="utf-8",
            )
            (asset_dir / "components.json").write_text(
                json.dumps({"components": [{"name": "Mesh"}]}),
                encoding="utf-8",
            )
            (asset_dir / "graph_queue.txt").write_text("EventGraph | EventGraph\nStartGlide | Function\n", encoding="utf-8")
            (asset_dir / "graph_candidates_uasset.json").write_text(
                json.dumps({"candidate_count": 3, "candidates": [{"name": "EventGraph"}, {"name": "StartGlide"}, {"name": "CanGlide"}]}),
                encoding="utf-8",
            )

            summary = asset_summary(asset_dir)

        self.assertEqual(summary["name"], "TestAsset")
        self.assertEqual(summary["graphs"], 1)
        self.assertEqual(summary["defaultsCount"], 2)
        self.assertEqual(summary["componentsCount"], 1)
        self.assertTrue(summary["hasGraphQueue"])
        self.assertEqual(summary["graphQueueCount"], 2)
        self.assertEqual(summary["graphQueueCompactCount"], 2)
        self.assertEqual(summary["graphQueueRecommendedCount"], 2)
        self.assertEqual(summary["graphQueueFocusedCount"], 2)
        self.assertEqual(summary["graphQueueOptionalCount"], 0)
        self.assertEqual(summary["graphQueueDeferredCount"], 0)
        self.assertTrue(summary["hasGraphCandidates"])
        self.assertEqual(summary["graphCandidateCount"], 3)
        self.assertTrue(summary["hasDefaults"])
        self.assertTrue(summary["hasComponents"])

    def test_asset_summary_exposes_small_context_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "TestAsset"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "formula_candidates.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "candidate_count": 2,
                            "unresolved_count": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "asset_memory_card.json").write_text("{}", encoding="utf-8")
            (output_dir / "context_pack.json").write_text("{}", encoding="utf-8")
            (output_dir / "asset_memory_card.md").write_text("# card", encoding="utf-8")
            (output_dir / "context_pack.md").write_text("# context", encoding="utf-8")
            (output_dir / "formula_candidates.md").write_text("# formulas", encoding="utf-8")

            summary = asset_summary(asset_dir)

        self.assertEqual(summary["formulaCandidateCount"], 2)
        self.assertEqual(summary["unresolvedFormulaCount"], 1)
        self.assertTrue(summary["assetMemoryCardExists"])
        self.assertTrue(summary["contextPackExists"])
        self.assertTrue(summary["reports"]["asset_memory_card"])
        self.assertTrue(summary["reports"]["context_pack"])
        self.assertTrue(summary["reports"]["formula_candidates"])

    def test_small_context_reports_are_openable_targets(self):
        expected = {
            "asset_memory_card": ("output", "asset_memory_card.md"),
            "context_pack": ("output", "context_pack.md"),
            "formula_candidates": ("output", "formula_candidates.md"),
            "formula_candidates_json": ("output", "formula_candidates.json"),
            "unresolved_formulas": ("output", "formula_candidates.md"),
        }

        for key, target in expected.items():
            self.assertEqual(REPORT_TARGETS[key], target)
            self.assertEqual(OPEN_TARGETS[key], target)

    def test_report_query_returns_a_bounded_outline_instead_of_full_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "TestAsset"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            report_path = output_dir / "asset_report.md"
            report_path.write_text(
                "# Asset Report\nFULL_SENTINEL\n## Summary\nsummary body\n## Calls\ncall body",
                encoding="utf-8",
            )

            result = query_report_for_request(
                asset_dir,
                "asset_report",
                mode="outline",
                budget=100,
            )

        self.assertEqual(result["path"], str(report_path))
        self.assertIn("# Asset Report", result["content"])
        self.assertIn("## Summary", result["content"])
        self.assertNotIn("FULL_SENTINEL", result["content"])
        self.assertLessEqual(result["estimated_tokens"], 100)

    def test_report_query_integer_errors_are_user_facing(self):
        with self.assertRaisesRegex(ValueError, "budget must be an integer"):
            parse_report_query_int("not-a-number", "budget", 1200)

    def test_asset_summary_counts_uasset_remaining_manual_queue_after_captures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "TestAsset"
            graphs_dir = asset_dir / "graphs"
            uasset_dir = asset_dir / "graphs_from_uasset"
            graphs_dir.mkdir(parents=True)
            uasset_dir.mkdir()
            (graphs_dir / "EventGraph.txt").write_text("EventGraph", encoding="utf-8")
            (uasset_dir / "EventGraph_1.json").write_text("{}", encoding="utf-8")
            (uasset_dir / "OtherGraph_2.json").write_text("{}", encoding="utf-8")
            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps({"graph_count": 2, "status_counts": {"complete": 1, "failed": 1}}),
                encoding="utf-8",
            )
            (asset_dir / "uasset_failed_graph_queue.json").write_text(
                json.dumps({"graphs": [{"graph": "EventGraph", "status": "failed"}]}),
                encoding="utf-8",
            )

            summary = asset_summary(asset_dir)

        self.assertEqual(summary["graphs"], 2)
        self.assertEqual(summary["uassetReadGraphCount"], 2)
        self.assertEqual(summary["uassetReadNeedsClipboardCount"], 0)

    def test_background_job_records_output_and_result(self):
        job = create_background_job(
            "test",
            "test job",
            [sys.executable, "-c", "print('job-ok')"],
            lambda return_code: {"done": return_code == 0},
        )

        final = wait_for_job(str(job["id"]))

        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["returnCode"], 0)
        self.assertIn("job-ok", final["stdout"])
        self.assertEqual(final["result"]["done"], True)

    def test_priority_read_command_analyzes_by_default(self):
        command = priority_read_command(3)

        self.assertNotIn("--no-analyze", command)

    def test_priority_read_command_can_skip_analysis_for_queue_debugging(self):
        command = priority_read_command(3, analyze=False)

        self.assertIn("--no-analyze", command)

    def test_background_job_can_be_cancelled(self):
        job = create_background_job(
            "test",
            "slow job",
            [sys.executable, "-c", "import time; time.sleep(5)"],
            lambda return_code: {"returnCode": return_code},
        )
        time.sleep(0.1)

        cancel_job(str(job["id"]))
        final = wait_for_job(str(job["id"]))

        self.assertEqual(final["status"], "cancelled")

    def test_missing_function_queue_filters_notes_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "TestAsset"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "context_review.md").write_text(
                "\n".join(
                    [
                        "# Blueprint Context Review",
                        "",
                        "## Missing Function Notes Queue",
                        "",
                        "| Function | Source Graphs | Areas | Notes line |",
                        "| --- | --- | --- | --- |",
                        "| UpdateJumpRotation | EventGraph | Movement | inherited: UpdateJumpRotation |",
                        "| StartSliding | Tick | Sliding | inherited: StartSliding |",
                        "| ClearJump | EventGraph | Movement | inherited: ClearJump |",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (asset_dir / "notes.md").write_text("ClearJump: parent - implemented by Dino_Character_BP\n", encoding="utf-8")

            first = missing_functions_from_report(asset_dir)
            result = append_notes_for_functions(asset_dir, "inherited", ["UpdateJumpRotation"])
            second = missing_functions_from_report(asset_dir)

        self.assertEqual([item["function"] for item in first], ["UpdateJumpRotation", "StartSliding"])
        self.assertEqual(result["added"], ["UpdateJumpRotation"])
        self.assertEqual([item["function"] for item in second], ["StartSliding"])

    def test_missing_function_queue_prefers_context_review_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "TestAsset"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "context_review.json").write_text(
                json.dumps(
                    {
                        "missing_functions": [
                            {
                                "function": "SetParachuteState",
                                "source_graphs": ["EventGraph"],
                                "areas": ["Parachute"],
                                "notes_inherited": "inherited: SetParachuteState",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "context_review.md").write_text(
                "\n".join(
                    [
                        "| Function | Source Graphs | Areas | Notes line |",
                        "| --- | --- | --- | --- |",
                        "| StaleMarkdownOnly | EventGraph | Glide | inherited: StaleMarkdownOnly |",
                    ]
                ),
                encoding="utf-8",
            )

            rows = missing_functions_from_report(asset_dir)

        self.assertEqual([item["function"] for item in rows], ["SetParachuteState"])
        self.assertEqual(rows[0]["areas"], ["Parachute"])


if __name__ == "__main__":
    unittest.main()
