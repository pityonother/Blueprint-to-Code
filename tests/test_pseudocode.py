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


def parse_fixture(bp, name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return bp.parse_blueprint_text(
        text=text,
        source=name,
        asset_name="TestAsset",
        graph_name="EventGraph",
        keywords=bp.profile_keywords("ark", []),
    )


class PseudocodeTests(unittest.TestCase):
    def test_branch_then_call_is_nested_and_pure_get_is_not_statement(self):
        bp = load_translator()
        _, nodes, payload = parse_fixture(bp, "pseudocode_branch.txt")
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertIn("if bIsSleeping:", pseudocode)
        self.assertRegex(pseudocode, r"if bIsSleeping:\n\s+InventoryRefresh\(\)")
        self.assertNotIn("read bIsSleeping", pseudocode)

    def test_recursive_expression_for_branch_condition(self):
        bp = load_translator()
        _, nodes, payload = parse_fixture(bp, "recursive_expression.txt")
        branch = payload["data_flow"]["branch_conditions"][0]
        self.assertEqual(branch["source"], "FeedingRange > 3000.0")
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertIn("if FeedingRange > 3000.0:", pseudocode)
        self.assertNotIn("Greater_FloatFloat(A, B)", pseudocode)

    def test_sequence_exec_order_is_stable(self):
        bp = load_translator()
        _, nodes, payload = parse_fixture(bp, "sequence_flow.txt")
        ordered = payload["exec_flow"]["ordered_node_names"]
        self.assertLess(ordered.index("K2Node_ExecutionSequence_0"), ordered.index("K2Node_CallFunction_0"))
        self.assertLess(ordered.index("K2Node_CallFunction_0"), ordered.index("K2Node_CallFunction_1"))
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertLess(pseudocode.index("CallA()"), pseudocode.index("CallB()"))


if __name__ == "__main__":
    unittest.main()
