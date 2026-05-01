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


class RealArkFixtureTests(unittest.TestCase):
    def test_real_ark_clipboard_fixtures_parse(self):
        bp = load_translator()
        for name in ("real_ark_achatina_beginplay.txt", "real_ark_achatina_inventory_refresh.txt"):
            with self.subTest(name=name):
                text = (FIXTURES / name).read_text(encoding="utf-8")
                _, nodes, payload = bp.parse_blueprint_text(
                    text=text,
                    source=name,
                    asset_name="Achatina_Character_BP",
                    graph_name="EventGraph",
                    keywords=bp.profile_keywords("ark", []),
                )
                self.assertGreaterEqual(len(nodes), 3)
                self.assertGreaterEqual(payload["metadata"]["pin_count"], 3)
                self.assertTrue(payload["nodes"][0]["export_path"].startswith("/Script/BlueprintGraph"))


if __name__ == "__main__":
    unittest.main()
