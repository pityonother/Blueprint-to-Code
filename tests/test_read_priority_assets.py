import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import read_priority_assets  # noqa: E402


class ReadPriorityAssetsTests(unittest.TestCase):
    def test_select_queue_limit_counts_only_unprocessed_assets(self):
        queue = [
            "/Game/Test/AlreadyRead.AlreadyRead",
            "/Game/Test/NextOne.NextOne",
            "/Game/Test/NextTwo.NextTwo",
        ]

        def fake_object_path_to_uasset_path(object_path: str):
            return Path(f"C:/ARK/{read_priority_assets.asset_name_from_object_path(object_path)}.uasset"), []

        def fake_processed_current(_db_path: Path, object_path: str, _uasset_path: Path):
            return object_path.endswith("AlreadyRead.AlreadyRead")

        with (
            patch.object(read_priority_assets, "object_path_to_uasset_path", side_effect=fake_object_path_to_uasset_path),
            patch.object(read_priority_assets, "processed_current_for_path", side_effect=fake_processed_current),
        ):
            selected, skipped = read_priority_assets.select_queue_items(queue, limit=2, force=False)

        self.assertEqual(selected, queue[1:])
        self.assertEqual([item["asset_name"] for item in skipped], ["AlreadyRead"])

    def test_existing_capture_result_keeps_graph_summary_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "Buff_Test"
            asset_dir.mkdir()
            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps(
                    {
                        "graph_count": 3,
                        "node_count": 12,
                        "pin_count": 40,
                        "link_count": 21,
                        "status_counts": {"complete": 2, "partial": 1},
                    }
                ),
                encoding="utf-8",
            )

            result = read_priority_assets.existing_capture_result(
                "/Game/Test/Buff_Test.Buff_Test",
                "Buff_Test",
                asset_dir,
                Path("C:/ARK/Buff_Test.uasset"),
            )

        self.assertEqual(result["status"], "skipped_existing")
        self.assertEqual(result["graph_count"], 3)
        self.assertEqual(result["node_count"], 12)
        self.assertEqual(result["pin_count"], 40)
        self.assertEqual(result["link_count"], 21)
        self.assertEqual(result["status_counts"], {"complete": 2, "partial": 1})

    def test_evaluate_asset_quality_reads_generated_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "Buff_Test"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "behavior_summary.md").write_text("- Confidence: low\n", encoding="utf-8")
            (output_dir / "diagnostics_report.md").write_text(
                "\n".join(
                    [
                        "- Parsed default variables: 4",
                        "- Parsed components: 0",
                        "- Findings: 1 error, 2 warning, 3 info",
                    ]
                ),
                encoding="utf-8",
            )
            (output_dir / "capture_quality_report.md").write_text(
                "\n".join(
                    [
                        "## Next Capture Actions",
                        "",
                        "| Graph | Reason | Nodes | Confidence |",
                        "| --- | --- | ---: | --- |",
                        "| BuffTickServer | low confidence | 12 | low |",
                        "",
                        "## Likely Missing Blueprint Graphs",
                        "",
                        "| Source Graph | Function | Classification |",
                        "| --- | --- | --- |",
                        "| BuffTickServer | TakeDamage | blueprint_graph_candidate |",
                    ]
                ),
                encoding="utf-8",
            )
            (output_dir / "asset_report.md").write_text("# ok\n", encoding="utf-8")
            (asset_dir / "uasset_class_defaults_report.md").write_text(
                "\n".join(
                    [
                        "- Usable variables: 4",
                        "",
                        "## Variables",
                        "",
                        "| Name | Type | Value | Confidence |",
                        "| --- | --- | --- | --- |",
                        "| DeactivateAfterTime | FloatProperty | 15.0 | high |",
                    ]
                ),
                encoding="utf-8",
            )

            quality = read_priority_assets.evaluate_asset_quality(
                {
                    "asset_name": "Buff_Test",
                    "asset_path": "/Game/Test/Buff_Test.Buff_Test",
                    "asset_dir": str(asset_dir),
                    "status": "read",
                    "graph_count": 1,
                    "node_count": 12,
                    "pin_count": 20,
                    "link_count": 30,
                    "status_counts": {"complete": 1},
                    "analysis": {"return_code": 0},
                }
            )

        self.assertEqual(quality["verdict"], "needs_immediate_followup")
        self.assertIn("diagnostic_errors", quality["quality_flags"])
        self.assertIn("missing_or_external_calls", quality["quality_flags"])
        self.assertEqual(quality["graphs_needing_attention"][0]["Graph"], "BuffTickServer")
        self.assertEqual(quality["missing_or_external_calls"][0]["Function"], "TakeDamage")
        self.assertEqual(quality["key_defaults"][0]["Name"], "DeactivateAfterTime")

    def test_evaluate_asset_quality_suppresses_empty_graph_only_bp000(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "PrimalGameData_BP"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "behavior_summary.md").write_text("- Confidence: low\n", encoding="utf-8")
            (output_dir / "diagnostics_report.md").write_text(
                "\n".join(
                    [
                        "- Parsed default variables: 0",
                        "- Parsed components: 0",
                        "- Confidence: low",
                        "- Findings: 1 error, 0 warning, 0 info",
                        "",
                        "## Findings",
                        "",
                        "### [ERROR] GRAPH:EventGraph:BP000 - EventGraph: No Blueprint nodes were parsed",
                    ]
                ),
                encoding="utf-8",
            )
            (output_dir / "capture_quality_report.md").write_text(
                "## Next Capture Actions\n\n- No graph pages need focused follow-up.\n",
                encoding="utf-8",
            )
            (output_dir / "asset_report.md").write_text("# ok\n", encoding="utf-8")
            (asset_dir / "uasset_class_defaults_report.md").write_text(
                "- Default object: Default__PrimalGameData_BP_C\n- Usable variables: 0\n",
                encoding="utf-8",
            )

            quality = read_priority_assets.evaluate_asset_quality(
                {
                    "asset_name": "PrimalGameData_BP",
                    "asset_path": "/Game/Test/PrimalGameData_BP.PrimalGameData_BP",
                    "asset_dir": str(asset_dir),
                    "status": "read",
                    "graph_count": 1,
                    "node_count": 0,
                    "status_counts": {"complete": 1},
                    "analysis": {"return_code": 0},
                }
            )

        self.assertNotIn("diagnostic_errors", quality["quality_flags"])
        self.assertEqual(quality["diagnostic_error_count_for_verdict"], 0)

    def test_evaluate_asset_quality_flags_mismatched_default_object(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "Buff_StriderHackingParent"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "behavior_summary.md").write_text("- Confidence: medium\n", encoding="utf-8")
            (output_dir / "diagnostics_report.md").write_text(
                "- Parsed default variables: 117\n- Parsed components: 1\n- Findings: 0 error, 0 warning, 0 info\n",
                encoding="utf-8",
            )
            (output_dir / "capture_quality_report.md").write_text(
                "## Next Capture Actions\n\n- No graph pages need focused follow-up.\n",
                encoding="utf-8",
            )
            (output_dir / "asset_report.md").write_text("# ok\n", encoding="utf-8")
            (asset_dir / "uasset_class_defaults_report.md").write_text(
                "\n".join(
                    [
                        "- Default object: Default__KismetSystemLibrary",
                        "- Usable variables: 117",
                        "",
                        "## Variables",
                        "",
                        "| Name | Type | Value | Confidence |",
                        "| --- | --- | --- | --- |",
                        "| DrumBuffRadius | FloatProperty | 3500.0 | high |",
                    ]
                ),
                encoding="utf-8",
            )

            quality = read_priority_assets.evaluate_asset_quality(
                {
                    "asset_name": "Buff_StriderHackingParent",
                    "asset_path": "/Game/Test/Buff_StriderHackingParent.Buff_StriderHackingParent",
                    "asset_dir": str(asset_dir),
                    "status": "read",
                    "graph_count": 1,
                    "node_count": 10,
                    "status_counts": {"complete": 1},
                    "analysis": {"return_code": 0},
                }
            )

        self.assertIn("class_defaults_mismatch", quality["quality_flags"])
        self.assertEqual(quality["parsed_default_variables"], 0)
        self.assertEqual(quality["key_defaults"], [])


if __name__ == "__main__":
    unittest.main()
