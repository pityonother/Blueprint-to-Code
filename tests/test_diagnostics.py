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


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostics_find_missing_defaults_components_and_links(self):
        bp = load_translator()
        text = (FIXTURES / "blueprint_old.txt").read_text(encoding="utf-8")
        _, _, payload = bp.parse_blueprint_text(
            text=text,
            source="blueprint_old.txt",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=bp.profile_keywords("ark", []),
        )
        codes = {item["code"] for item in bp.build_diagnostic_findings(payload)}
        self.assertIn("BP040", codes)
        self.assertIn("BP041", codes)

    def test_real_delegate_and_missing_link_map_are_reported(self):
        bp = load_translator()
        text = (FIXTURES / "real_ark_achatina_beginplay.txt").read_text(encoding="utf-8")
        _, nodes, payload = bp.parse_blueprint_text(
            text=text,
            source="real_ark_achatina_beginplay.txt",
            asset_name="Achatina_Character_BP",
            graph_name="EventGraph",
            keywords=bp.profile_keywords("ark", []),
        )
        self.assertEqual(payload["delegates"][0]["delegate"], "OnDied")
        self.assertNotIn("K2Node_AddDelegate", payload["diagnostics"]["unsupported_node_types"])
        missing_targets = {item["target_node"] for item in payload["diagnostics"]["missing_link_map"]}
        self.assertIn("K2Node_ExecutionSequence_388", missing_targets)
        self.assertIn("K2Node_CallFunction_61776", missing_targets)
        sequence = next(item for item in payload["diagnostics"]["missing_link_map"] if item["target_node"] == "K2Node_ExecutionSequence_388")
        self.assertEqual(sequence["target_kind"], "sequence")
        report = bp.render_diagnostics_report(payload)
        self.assertIn("Missing LinkedTo Target Map", report)
        self.assertIn("Execution sequence is missing after ReceiveBeginPlay.then", report)
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertIn("bind OnDied -> OnDied_Event", pseudocode)

    def test_class_defaults_are_attached_to_data_flow(self):
        bp = load_translator()
        text = (FIXTURES / "blueprint_old.txt").read_text(encoding="utf-8")
        _, _, payload = bp.parse_blueprint_text(
            text=text,
            source="blueprint_old.txt",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=bp.profile_keywords("ark", []),
            context={"defaults_text": '{"variables":{"bIsSleeping":{"default":false}}}', "components_text": "Inventory component"},
        )
        branch = payload["data_flow"]["branch_conditions"][0]
        self.assertEqual(branch["class_default_refs"][0]["name"], "bIsSleeping")
        self.assertFalse(branch["class_default_refs"][0]["value"])

    def test_component_defaults_are_parsed_and_attached_to_data_flow(self):
        bp = load_translator()
        text = (FIXTURES / "blueprint_old.txt").read_text(encoding="utf-8")
        _, _, payload = bp.parse_blueprint_text(
            text=text,
            source="blueprint_old.txt",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=bp.profile_keywords("ark", []),
            context={
                "components_text": json.dumps(
                    {
                        "components": [
                            {
                                "name": "Inventory",
                                "class": "PrimalInventoryComponent",
                                "defaults": {"self": "InventoryComponentRef"},
                            }
                        ]
                    }
                )
            },
        )
        self.assertEqual(payload["component_defaults"]["components"][0]["name"], "Inventory")
        dep = next(item for item in payload["data_flow"]["dependencies"] if item["node_label"] == "InventoryRefresh" and item["pin"] == "self")
        self.assertIn("component_refs", dep)
        self.assertEqual(dep["component_refs"][0]["name"], "Inventory")
        self.assertEqual(dep["source"], "Inventory.self (component default InventoryComponentRef)")

    def test_cli_writes_diagnostics_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(FIXTURES / "real_ark_achatina_beginplay.txt"),
                    "--asset-name",
                    "Achatina_Character_BP",
                    "--graph-name",
                    "EventGraph",
                    "--output-dir",
                    tmp,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("diagnostics_report", result.stdout)
            report = pathlib.Path(tmp) / "diagnostics_report.md"
            payload = pathlib.Path(tmp) / "diagnostics.json"
            self.assertTrue(report.exists())
            self.assertTrue(payload.exists())
            self.assertIn("Blueprint Diagnostics Report", report.read_text(encoding="utf-8"))
            data = json.loads(payload.read_text(encoding="utf-8"))
            self.assertIn("findings", data)


if __name__ == "__main__":
    unittest.main()
