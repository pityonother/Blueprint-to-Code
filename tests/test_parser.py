import importlib.util
import pathlib
import sys
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


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.bp = load_translator()
        text = (FIXTURES / "blueprint_old.txt").read_text(encoding="utf-8")
        keywords = self.bp.profile_keywords("ark", [])
        self.cleaned, self.nodes, self.payload = self.bp.parse_blueprint_text(
            text=text,
            source="fixture",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=keywords,
        )

    def test_node_function_variable_counts(self):
        self.assertEqual(len(self.nodes), 5)
        self.assertEqual(len(self.payload["function_calls"]), 1)
        self.assertEqual(self.payload["function_calls"][0]["function"], "InventoryRefresh")
        self.assertEqual(len(self.payload["variable_gets"]), 1)
        self.assertEqual(self.payload["variable_gets"][0]["variable"], "bIsSleeping")
        self.assertEqual(len(self.payload["variable_sets"]), 1)
        self.assertEqual(self.payload["variable_sets"][0]["variable"], "LastInventoryRefreshTime")

    def test_guid_and_linked_to_are_preserved(self):
        first = self.payload["nodes"][0]
        self.assertEqual(first["node_guid"], "11111111111111111111111111111111")
        pin = first["pins"][0]
        self.assertEqual(pin["id"], "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        self.assertTrue(pin["linked_to_raw"])
        self.assertEqual(pin["persistent_guid"], "00000000000000000000000000000000")

    def test_exec_flow_and_data_flow(self):
        exec_flow = self.payload["exec_flow"]
        self.assertEqual(exec_flow["roots"][0]["event"], "ReceiveBeginPlay")
        self.assertGreaterEqual(len(exec_flow["edges"]), 2)
        ordered = exec_flow["ordered_node_names"]
        self.assertLess(ordered.index("K2Node_Event_0"), ordered.index("K2Node_IfThenElse_0"))
        branch = self.payload["data_flow"]["branch_conditions"][0]
        self.assertEqual(branch["source"], "bIsSleeping")


if __name__ == "__main__":
    unittest.main()
