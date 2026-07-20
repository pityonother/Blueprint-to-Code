import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.formulas import build_formula_candidates  # noqa: E402


def load_gigantoraptor_payload() -> dict[str, object]:
    class_defaults = {
        "variables": {
            "DistributionForMaxWeight": {
                "type": "FloatProperty",
                "value": 0.5,
                "source": "synthetic_test_fixture",
                "confidence": "high",
            },
            "DescriptiveNameBase": {
                "type": "StrProperty",
                "value": "Gigantoraptor Feather",
                "source": "synthetic_test_fixture",
                "confidence": "high",
            },
        },
        "properties": [
            {
                "name": "InheritStatWeightMinMax",
                "type": "StructProperty",
                "value": {"parsed": False},
                "confidence": "low",
            }
        ],
    }
    formula_nodes = [
        {"variable": "DistributionForMaxWeight"},
        {"function": "MapRangeClamped"},
        {"function": "Multiply_DoubleFloat"},
        {"function": "FTrunc"},
        {"function": "InRange_IntInt"},
        {"function": "Array_Length"},
        {"function": "GetCustomItemData"},
    ]
    graphs = [
        {
            "graph_name": "GetFeatherStatInfo",
            "graph_type": "Function",
            "source": "synthetic_test_fixture",
            "source_kind": "unit_test",
            "node_count": len(formula_nodes),
            "confidence": "high",
            "payload": {
                "metadata": {
                    "graph_name": "GetFeatherStatInfo",
                    "graph_type": "Function",
                    "confidence": "high",
                    "link_resolution_counts": {"resolved_pin": 6},
                },
                "nodes": formula_nodes,
            },
        },
        {
            "graph_name": "BPOverrideInheritedStatWeight",
            "graph_type": "Function",
            "source": "synthetic_test_fixture",
            "source_kind": "unit_test",
            "node_count": 2,
            "confidence": "high",
            "payload": {
                "metadata": {
                    "graph_name": "BPOverrideInheritedStatWeight",
                    "graph_type": "Function",
                    "confidence": "high",
                },
                "nodes": [
                    {"variable": "DistributionForMaxWeight"},
                    {"function": "Multiply_DoubleFloat"},
                ],
            },
        },
    ]
    return {
        "metadata": {
            "asset_dir": "tests/fixtures/formula_candidates",
            "asset_name": "PrimalItemResource_GigantoraptorFeather",
            "asset_path": (
                "/Game/Extinction/Dinos/Gigantoraptor/"
                "PrimalItemResource_GigantoraptorFeather.PrimalItemResource_GigantoraptorFeather"
            ),
            "graph_count": len(graphs),
            "node_count": sum(int(graph["node_count"]) for graph in graphs),
            "default_variable_count": 1,
        },
        "class_defaults": class_defaults,
        "uasset_binary": {
            "present": True,
            "asset_name": "PrimalItemResource_GigantoraptorFeather",
            "asset_path": (
                "/Game/Extinction/Dinos/Gigantoraptor/"
                "PrimalItemResource_GigantoraptorFeather.PrimalItemResource_GigantoraptorFeather"
            ),
            "uasset_path": "",
            "pin_link_summary": {},
            "class_defaults": class_defaults,
        },
        "graphs": graphs,
        "call_graph": {},
        "diagnostics": {},
    }


class FormulaCandidateTests(unittest.TestCase):
    def test_gigantoraptor_feather_formula_candidates(self):
        payload = build_formula_candidates(load_gigantoraptor_payload())

        self.assertEqual(payload["schema"], "ark.blueprint.formula_candidates.v1")
        candidates = payload["candidates"]
        self.assertGreaterEqual(len(candidates), 1)
        self.assertTrue(all(candidate["status"] == "candidate" for candidate in candidates))
        self.assertTrue(
            any(candidate["mechanism_type"] in {"stat_weight", "custom_item_data"} for candidate in candidates)
        )
        self.assertTrue(
            any(candidate["graph"] in {"GetFeatherStatInfo", "BPOverrideInheritedStatWeight"} for candidate in candidates)
        )

        combined = json.dumps(candidates, ensure_ascii=False)
        self.assertIn("DistributionForMaxWeight", combined)
        self.assertIn("0.5", combined)
        self.assertIn("MapRangeClamped", combined)
        self.assertIn("Multiply_DoubleFloat", combined)
        self.assertIn("FTrunc", combined)
        self.assertIn("InRange_IntInt", combined)
        self.assertIn("Array_Length", combined)
        self.assertIn("GetCustomItemData", combined)
        self.assertGreaterEqual(len(payload["unresolved_formulas"]), 1)


if __name__ == "__main__":
    unittest.main()
