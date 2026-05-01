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


if __name__ == "__main__":
    unittest.main()
