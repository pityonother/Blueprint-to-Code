import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_tool_server import cancel_job, create_background_job, asset_summary, get_job, normalize_asset_path


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


if __name__ == "__main__":
    unittest.main()
