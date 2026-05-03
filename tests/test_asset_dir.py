import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bp_clipboard_to_prompt.py"
FIXTURES = ROOT / "tests" / "fixtures"


class AssetDirTests(unittest.TestCase):
    def test_asset_dir_parses_multiple_graphs_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = pathlib.Path(tmp) / "Achatina_Character_BP"
            graphs_dir = asset_dir / "graphs"
            out_dir = pathlib.Path(tmp) / "out"
            graphs_dir.mkdir(parents=True)
            (graphs_dir / "EventGraph.txt").write_text(
                (FIXTURES / "real_ark_achatina_beginplay.txt").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (graphs_dir / "Function_InventoryRefresh.txt").write_text(
                (FIXTURES / "real_ark_achatina_inventory_refresh.txt").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (asset_dir / "defaults.json").write_text(json.dumps({"variables": {"FeedingRange": 3000}}), encoding="utf-8")
            (asset_dir / "components.json").write_text(
                json.dumps({"components": [{"name": "Inventory", "class": "PrimalInventoryComponent", "defaults": {"MaxItems": 100}}]}),
                encoding="utf-8",
            )
            (asset_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "asset_name": "Achatina_Character_BP",
                        "parent_class": "PrimalDinoCharacter",
                        "graphs": [
                            {"name": "EventGraph", "type": "EventGraph", "path": "graphs/EventGraph.txt"},
                            {"name": "Function_InventoryRefresh", "type": "Function", "path": "graphs/Function_InventoryRefresh.txt"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--asset-dir",
                    str(asset_dir),
                    "--output-dir",
                    str(out_dir),
                    "--report-level",
                    "debug",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Parsed graphs: 2", result.stdout)
            asset_json = out_dir / "asset.json"
            self.assertTrue(asset_json.exists())
            payload = json.loads(asset_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["metadata"]["graph_count"], 2)
            self.assertTrue(payload["metadata"]["defaults_present"])
            self.assertTrue(payload["metadata"]["components_present"])
            self.assertEqual(payload["metadata"]["component_count"], 1)
            self.assertEqual(payload["call_graph"]["delegate_bindings"][0]["delegate"], "OnDied")
            self.assertEqual(payload["call_graph"]["missing_macro_links"][0]["missing_macro_node"], "K2Node_MacroInstance_0")
            self.assertTrue((out_dir / "asset_report.md").exists())
            self.assertTrue((out_dir / "diagnostics_report.md").exists())
            self.assertTrue((out_dir / "call_graph.md").exists())
            self.assertTrue((out_dir / "call_graph_summary.md").exists())
            self.assertTrue((out_dir / "behavior_summary.md").exists())
            self.assertTrue((out_dir / "context_review.md").exists())
            self.assertTrue((out_dir / "context_review.json").exists())
            self.assertTrue((out_dir / "notes_todo.md").exists())
            self.assertTrue((out_dir / "capture_quality_report.md").exists())
            self.assertTrue((out_dir / "capture_quality.json").exists())
            self.assertTrue((out_dir / "context_review.json").exists())
            self.assertTrue((out_dir / "defaults_suggestions.json").exists())
            self.assertTrue((out_dir / "components_suggestions.json").exists())
            self.assertTrue((out_dir / "next_actions.md").exists())
            self.assertTrue((out_dir / "graph_reports" / "index.md").exists())
            report_text = (out_dir / "asset_report.md").read_text(encoding="utf-8")
            call_graph_text = (out_dir / "call_graph.md").read_text(encoding="utf-8")
            behavior_text = (out_dir / "behavior_summary.md").read_text(encoding="utf-8")
            context_review_text = (out_dir / "context_review.md").read_text(encoding="utf-8")
            notes_todo_text = (out_dir / "notes_todo.md").read_text(encoding="utf-8")
            quality_text = (out_dir / "capture_quality_report.md").read_text(encoding="utf-8")
            defaults_suggestions = json.loads((out_dir / "defaults_suggestions.json").read_text(encoding="utf-8"))
            components_suggestions = json.loads((out_dir / "components_suggestions.json").read_text(encoding="utf-8"))
            next_actions = (out_dir / "next_actions.md").read_text(encoding="utf-8-sig")
            self.assertIn("Missing LinkedTo Targets", report_text)
            self.assertIn("K2Node_ExecutionSequence_388", report_text)
            self.assertIn("Delegate Bindings", call_graph_text)
            self.assertIn("OnDied_Event", call_graph_text)
            self.assertIn("Missing Macro Links", call_graph_text)
            self.assertIn("Behavior Areas", behavior_text)
            self.assertIn("Inferred Behavior", behavior_text)
            self.assertIn("Area Details", behavior_text)
            self.assertIn("Default Candidate Triage", context_review_text)
            self.assertIn("Missing Function Notes Queue", context_review_text)
            self.assertIn("Blueprint Notes Todo", notes_todo_text)
            self.assertIn("Candidates To Verify", notes_todo_text)
            self.assertIn("Next Capture Actions", quality_text)
            self.assertIn("variables", defaults_suggestions)
            self.assertIn("components", components_suggestions)
            self.assertIn("填 defaults.json", next_actions)


    def test_standard_report_level_skips_heavy_debug_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = pathlib.Path(tmp) / "Achatina_Character_BP"
            graphs_dir = asset_dir / "graphs"
            out_dir = pathlib.Path(tmp) / "out"
            graphs_dir.mkdir(parents=True)
            (graphs_dir / "EventGraph.txt").write_text(
                (FIXTURES / "real_ark_achatina_beginplay.txt").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (asset_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "asset_name": "Achatina_Character_BP",
                        "graphs": [{"name": "EventGraph", "type": "EventGraph", "path": "graphs/EventGraph.txt"}],
                    }
                ),
                encoding="utf-8",
            )
            out_dir.mkdir()
            (out_dir / "asset.json").write_text("stale", encoding="utf-8")
            (out_dir / "call_graph.md").write_text("stale", encoding="utf-8")
            stale_graph_dir = out_dir / "graph_reports"
            stale_graph_dir.mkdir()
            (stale_graph_dir / "stale.json").write_text("stale", encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--asset-dir",
                    str(asset_dir),
                    "--output-dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertTrue((out_dir / "next_actions.md").exists())
            self.assertTrue((out_dir / "capture_quality_report.md").exists())
            self.assertTrue((out_dir / "diagnostics_report.md").exists())
            self.assertTrue((out_dir / "behavior_summary.md").exists())
            self.assertTrue((out_dir / "context_review.md").exists())
            self.assertTrue((out_dir / "notes_todo.md").exists())
            self.assertTrue((out_dir / "asset_report.md").exists())
            self.assertTrue((out_dir / "call_graph_summary.md").exists())
            self.assertTrue((out_dir / "graph_reports" / "index.md").exists())
            self.assertFalse((out_dir / "asset.json").exists())
            self.assertFalse((out_dir / "call_graph.md").exists())
            self.assertFalse((out_dir / "graph_reports" / "stale.json").exists())
            self.assertFalse((out_dir / "report.md").exists())

    def test_manual_graphs_override_uasset_graphs_without_hiding_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = pathlib.Path(tmp) / "Hybrid_Character_BP"
            graphs_dir = asset_dir / "graphs"
            uasset_dir = asset_dir / "graphs_from_uasset"
            out_dir = pathlib.Path(tmp) / "out"
            graphs_dir.mkdir(parents=True)
            uasset_dir.mkdir()
            (graphs_dir / "ManualGraph.txt").write_text(
                (FIXTURES / "real_ark_achatina_beginplay.txt").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (uasset_dir / "ManualGraph_1.json").write_text(
                json.dumps(
                    {
                        "metadata": {"graph_name": "ManualGraph", "graph_type": "Function", "node_count": 1},
                        "nodes": [{"name": "StaleBinaryNode", "node_type": "K2Node_CallFunction"}],
                    }
                ),
                encoding="utf-8",
            )
            (uasset_dir / "BinaryOnly_2.json").write_text(
                json.dumps(
                    {
                        "metadata": {"graph_name": "BinaryOnly", "graph_type": "Function", "node_count": 1},
                        "nodes": [{"name": "RecoveredNode", "node_type": "K2Node_CallFunction"}],
                    }
                ),
                encoding="utf-8",
            )
            (asset_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "asset_name": "Hybrid_Character_BP",
                        "graphs": [{"name": "ManualGraph", "type": "Function", "path": "graphs/ManualGraph.txt"}],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--asset-dir",
                    str(asset_dir),
                    "--output-dir",
                    str(out_dir),
                    "--report-level",
                    "debug",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Parsed graphs: 2", result.stdout)
            payload = json.loads((out_dir / "asset.json").read_text(encoding="utf-8"))
            graph_sources = {item["graph_name"]: item.get("source_kind", "clipboard") for item in payload["graphs"]}
            self.assertEqual(graph_sources["ManualGraph"], "clipboard")
            self.assertEqual(graph_sources["BinaryOnly"], "uasset_binary")


if __name__ == "__main__":
    unittest.main()
