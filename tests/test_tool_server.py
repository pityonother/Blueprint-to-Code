import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_tool_server import asset_summary, normalize_asset_path


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


if __name__ == "__main__":
    unittest.main()
