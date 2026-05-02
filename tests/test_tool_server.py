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

            summary = asset_summary(asset_dir)

        self.assertEqual(summary["name"], "TestAsset")
        self.assertEqual(summary["graphs"], 1)
        self.assertEqual(summary["defaultsCount"], 2)
        self.assertEqual(summary["componentsCount"], 1)
        self.assertTrue(summary["hasDefaults"])
        self.assertTrue(summary["hasComponents"])

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
