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
            with self.assertRaises(FileExistsError):
                bp.save_captured_graph(asset_dir, "EventGraph", "EventGraph", text)
            second = bp.save_captured_graph(asset_dir, "EventGraph", "EventGraph", text + "\n", allow_overwrite=True)
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
            self.assertTrue(second["overwritten"])
            self.assertTrue((asset_dir / second["backup_path"]).exists())
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

            refused = subprocess.run(
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
                check=False,
            )
            self.assertEqual(refused.returncode, 3)
            self.assertIn("--capture-overwrite", refused.stderr)

            overwritten = subprocess.run(
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
                    "--capture-overwrite",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Backup of overwritten graph:", overwritten.stdout)

    def test_function_call_classification_de_noises_engine_calls(self):
        bp = load_translator()
        self.assertEqual(bp.classify_function_call("K2_GetWorld"), "unreal_engine")
        self.assertEqual(bp.classify_function_call("Multiply_DoubleDouble"), "kismet_math_or_data")
        self.assertEqual(bp.classify_function_call("K2_SetTimer"), "engine_timer")
        self.assertEqual(bp.classify_function_call("SetActive"), "component_or_presentation")
        self.assertEqual(bp.classify_function_call("Delay"), "unreal_engine")
        self.assertEqual(bp.classify_function_call("GetActorRightVector"), "unreal_engine")
        self.assertEqual(bp.classify_function_call("FormatAsTime"), "unreal_engine")
        self.assertEqual(bp.classify_function_call("HideBoneByName"), "component_or_presentation")
        self.assertEqual(bp.classify_function_call("InputRunPressed"), "ark_parent_or_rpc")
        self.assertEqual(bp.classify_function_call("IsRunningOnServer"), "ark_parent_or_rpc")
        self.assertEqual(bp.classify_function_call("BlueprintCanRiderAttack"), "ark_parent_or_rpc")
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

    def test_behavior_summary_applies_ark_rule_fixture(self):
        bp = load_translator()
        payload = {
            "metadata": {"asset_name": "MilkGlider_Character_BP", "graph_count": 1, "node_count": 4},
            "diagnostics": {"confidence_level": "medium"},
            "graphs": [
                {
                    "graph_name": "Client Tick Gliding",
                    "node_count": 4,
                    "payload": {
                        "variable_gets": [
                            {"variable": "bCanGlide"},
                            {"variable": "CharacterMovement"},
                            {"variable": "WingTrail"},
                        ],
                        "variable_sets": [
                            {"variable": "bOverrideNewFallVelocity"},
                        ],
                    },
                }
            ],
            "class_defaults": {"variables": {"bCanGlide": True, "GlidingPullUpMultiplier": 1.25}},
            "component_defaults": {"components": [{"name": "CharacterMovement"}, {"name": "WingTrail"}]},
            "call_graph": {"calls": []},
        }

        text = bp.render_behavior_summary(payload)

        self.assertIn("Behavior Rule Checks", text)
        self.assertIn("Glide", text)
        self.assertIn("bCanGlide", text)
        self.assertIn("CharacterMovement", text)
        self.assertIn("Confirm start checks", text)


    def test_notes_sidecar_suppresses_known_external_function_candidates(self):
        bp = load_translator()
        notes = bp.parse_notes_context({"notes_text": "inherited: UpdateJumpRotation\nignore missing graph: FooBar"})
        self.assertEqual(bp.function_note_for_name(notes, "UpdateJumpRotation")["kind"], "noted_native_or_inherited")
        self.assertEqual(bp.function_note_for_name(notes, "FooBar")["kind"], "noted_ignored")

        asset_payload = {
            "notes": notes,
            "graphs": [
                {
                    "graph_name": "EventGraph",
                    "payload": {
                        "nodes": [],
                        "function_calls": [
                            {"node_type": "K2Node_CallFunction", "function": "UpdateJumpRotation", "label": "UpdateJumpRotation"},
                            {"node_type": "K2Node_CallFunction", "function": "StillMissingGraph", "label": "StillMissingGraph"},
                        ],
                    },
                }
            ],
        }
        call_graph = bp.build_asset_call_graph(asset_payload)
        missing = [item["function"] for item in call_graph["missing_targets"]]
        native_or_noted = {item["function"]: item["call_kind"] for item in call_graph["native_or_inherited_calls"]}

        self.assertNotIn("UpdateJumpRotation", missing)
        self.assertIn("StillMissingGraph", missing)
        self.assertEqual(native_or_noted["UpdateJumpRotation"], "noted_native_or_inherited")


if __name__ == "__main__":
    unittest.main()
