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


if __name__ == "__main__":
    unittest.main()
