import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_tool_server import (
    append_notes_for_functions,
    asset_summary,
    cancel_job,
    create_background_job,
    get_job,
    missing_functions_from_report,
    normalize_asset_path,
    priority_read_command,
)


def wait_for_job(job_id: str, timeout_seconds: float = 5.0) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        job = get_job(job_id)
        if str(job.get("status")) in {"succeeded", "failed", "cancelled", "timed_out"}:
            return job
        time.sleep(0.05)
    return get_job(job_id)


class ToolServerTests(unittest.TestCase):
    def test_normalize_asset_path_accepts_devkit_reference(self):
        raw = "Blueprint'/Game/Mods/Test/MilkGlider_Character_BP.MilkGlider_Character_BP_C'"
        self.assertEqual(
            normalize_asset_path(raw),
            "/Game/Mods/Test/MilkGlider_Character_BP.MilkGlider_Character_BP",
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
