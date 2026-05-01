import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bp_clipboard_to_prompt.py"
FIXTURES = ROOT / "tests" / "fixtures"


def load_translator():
    spec = importlib.util.spec_from_file_location("bp_translator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CompareTests(unittest.TestCase):
    def test_compare_detects_logic_differences(self):
        bp = load_translator()
        keywords = bp.profile_keywords("ark", [])
        old_text = (FIXTURES / "blueprint_old.txt").read_text(encoding="utf-8")
        new_text = (FIXTURES / "blueprint_new.txt").read_text(encoding="utf-8")
        _, _, old_payload = bp.parse_blueprint_text(
            text=old_text,
            source="old",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=keywords,
        )
        _, _, new_payload = bp.parse_blueprint_text(
            text=new_text,
            source="new",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=keywords,
        )
        diff = bp.compare_payloads(old_payload, new_payload)
        self.assertEqual(diff["node_count"]["old"], 5)
        self.assertEqual(diff["node_count"]["new"], 6)
        self.assertIn("RegisterNearbyDino", diff["function_call_delta"])
        self.assertTrue(diff["changed_pin_defaults"])
        self.assertTrue(diff["likely_logic_changes"])
        self.assertIn("Radius", diff["keyword_delta"])

    def test_fuzzy_compare_matches_same_logic_with_different_guids(self):
        bp = load_translator()
        keywords = bp.profile_keywords("ark", [])
        old_text = (FIXTURES / "fuzzy_compare_old.txt").read_text(encoding="utf-8")
        new_text = (FIXTURES / "fuzzy_compare_new.txt").read_text(encoding="utf-8")
        _, _, old_payload = bp.parse_blueprint_text(
            text=old_text,
            source="old fuzzy",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=keywords,
        )
        _, _, new_payload = bp.parse_blueprint_text(
            text=new_text,
            source="new fuzzy",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=keywords,
        )
        diff = bp.compare_payloads(old_payload, new_payload)
        self.assertEqual(diff["added_nodes"], [])
        self.assertEqual(diff["removed_nodes"], [])
        self.assertTrue(diff["matched_by_signature"] or diff["matched_by_fuzzy"])

    def test_compare_asset_detects_graph_sidecar_and_logic_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            old_dir = tmp_path / "old_asset"
            new_dir = tmp_path / "new_asset"
            old_graphs = old_dir / "graphs"
            new_graphs = new_dir / "graphs"
            old_graphs.mkdir(parents=True)
            new_graphs.mkdir(parents=True)
            (old_graphs / "EventGraph.txt").write_text((FIXTURES / "blueprint_old.txt").read_text(encoding="utf-8"), encoding="utf-8")
            (new_graphs / "EventGraph.txt").write_text((FIXTURES / "blueprint_new.txt").read_text(encoding="utf-8"), encoding="utf-8")
            (new_graphs / "Function_InventoryRefresh.txt").write_text((FIXTURES / "real_ark_achatina_inventory_refresh.txt").read_text(encoding="utf-8"), encoding="utf-8")
            (old_dir / "defaults.json").write_text(json.dumps({"variables": {"FeedingRange": 3000}}), encoding="utf-8")
            (new_dir / "defaults.json").write_text(json.dumps({"variables": {"FeedingRange": 4500}}), encoding="utf-8")
            (old_dir / "components.json").write_text(json.dumps({"components": [{"name": "Inventory", "class": "PrimalInventoryComponent", "defaults": {"MaxItems": 100}}]}), encoding="utf-8")
            (new_dir / "components.json").write_text(json.dumps({"components": [{"name": "Inventory", "class": "PrimalInventoryComponent", "defaults": {"MaxItems": 200}}]}), encoding="utf-8")
            for asset_dir in (old_dir, new_dir):
                graphs = [{"name": "EventGraph", "type": "EventGraph", "path": "graphs/EventGraph.txt"}]
                if asset_dir == new_dir:
                    graphs.append({"name": "Function_InventoryRefresh", "type": "Function", "path": "graphs/Function_InventoryRefresh.txt"})
                (asset_dir / "manifest.json").write_text(json.dumps({"asset_name": "TestAsset", "graphs": graphs}), encoding="utf-8")

            out_dir = tmp_path / "out"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--compare-asset",
                    str(old_dir),
                    str(new_dir),
                    "--output-dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Behavior-relevant changes", result.stdout)
            compare_json = out_dir / "compare.json"
            self.assertTrue(compare_json.exists())
            diff = json.loads(compare_json.read_text(encoding="utf-8"))
            self.assertIn("Function_InventoryRefresh", diff["added_graphs"])
            self.assertTrue(diff["defaults_delta"]["changed"])
            self.assertTrue(diff["components_delta"]["changed"])
            self.assertTrue(any("EventGraph" in note for note in diff["likely_behavior_changes"]))
            report = (out_dir / "compare_report.md").read_text(encoding="utf-8")
            self.assertIn("Blueprint Asset Compare Report", report)
            self.assertIn("Component Delta", report)
            self.assertIn("Function_InventoryRefresh", report)


if __name__ == "__main__":
    unittest.main()
