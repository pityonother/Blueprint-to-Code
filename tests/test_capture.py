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


class CaptureTests(unittest.TestCase):
    def test_save_captured_graph_writes_manifest_ready_record(self):
        bp = load_translator()
        text = (FIXTURES / "blueprint_old.txt").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = pathlib.Path(tmp) / "Achatina_Character_BP"
            first = bp.save_captured_graph(asset_dir, "EventGraph", "EventGraph", text)
            second = bp.save_captured_graph(asset_dir, "EventGraph", "EventGraph", text)
            records = bp.upsert_graph_record([], first)
            records = bp.upsert_graph_record(records, second)
            manifest_path = bp.write_capture_manifest(
                asset_dir,
                "Achatina_Character_BP",
                records,
                parent_class="PrimalDinoCharacter",
                interfaces=["BPI_Test"],
                tags=["ARK"],
            )

            self.assertTrue((asset_dir / "graphs" / "EventGraph.txt").exists())
            self.assertEqual(len(records), 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["asset_name"], "Achatina_Character_BP")
            self.assertEqual(manifest["graphs"][0]["path"], "graphs/EventGraph.txt")
            self.assertEqual(manifest["parent_class"], "PrimalDinoCharacter")

    def test_capture_once_cli_builds_asset_directory_without_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = pathlib.Path(tmp) / "CapturedAsset"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--capture-asset",
                    str(asset_dir),
                    "--capture-once",
                    "EventGraph",
                    "--input",
                    str(FIXTURES / "blueprint_old.txt"),
                    "--capture-no-report",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Captured graphs: 1", result.stdout)
            self.assertTrue((asset_dir / "graphs" / "EventGraph.txt").exists())
            self.assertTrue((asset_dir / "defaults.json").exists())
            self.assertTrue((asset_dir / "components.json").exists())
            manifest = json.loads((asset_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["graphs"][0]["name"], "EventGraph")

    def test_function_call_classification_de_noises_engine_calls(self):
        bp = load_translator()
        self.assertEqual(bp.classify_function_call("K2_GetWorld"), "unreal_engine")
        self.assertEqual(bp.classify_function_call("Multiply_DoubleDouble"), "kismet_math_or_data")
        self.assertEqual(bp.classify_function_call("K2_SetTimer"), "engine_timer")
        self.assertEqual(bp.classify_function_call("SetActive"), "component_or_presentation")
        self.assertEqual(bp.classify_function_call("UpdateJumpRotation"), "blueprint_graph_candidate")

    def test_suggestions_are_structured_for_sidecars(self):
        bp = load_translator()
        payload = {
            "metadata": {"asset_name": "TestAsset", "graph_count": 1, "node_count": 2},
            "graphs": [
                {
                    "graph_name": "EventGraph",
                    "payload": {
                        "variable_gets": [
                            {"variable": "CharacterMovement"},
                            {"variable": "MaxGlideHeight"},
                        ],
                        "variable_sets": [
                            {"variable": "bCanGlide"},
                        ],
                    },
                }
            ],
            "class_defaults": {"variables": {}},
            "component_defaults": {"components": []},
            "call_graph": {"calls": [], "missing_targets": [], "native_or_inherited_calls": []},
        }

        defaults = bp.build_defaults_suggestions(payload)
        components = bp.build_components_suggestions(payload)
        next_actions = bp.render_next_actions(payload)

        self.assertIn("bCanGlide", defaults["variables"])
        self.assertEqual(defaults["variables"]["bCanGlide"]["_hint"], "boolean")
        self.assertEqual(components["components"][0]["name"], "CharacterMovement")
        self.assertIn("填 defaults.json", next_actions)


if __name__ == "__main__":
    unittest.main()
