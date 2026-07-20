import sys
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from blueprint_translator.context_pack import (  # noqa: E402
    DEFAULT_CONTEXT_BUDGET,
    MIN_CONTEXT_BUDGET,
    build_asset_memory_card,
    build_default_context_pack,
    estimate_tokens,
    render_context_pack,
)
from blueprint_translator.formulas import build_formula_candidates  # noqa: E402
from build_asset_context_pack import build_pack, load_asset_payload, read_optional_json  # noqa: E402
from test_formula_candidates import load_gigantoraptor_payload  # noqa: E402


class ContextPackTests(unittest.TestCase):
    def test_token_estimator_counts_newlines_and_indentation(self):
        self.assertGreaterEqual(estimate_tokens("\n" * 20), 20)
        self.assertGreater(estimate_tokens(" " * 80), 0)

    def test_context_pack_is_short_and_omits_full_graph_payloads(self):
        asset_payload = load_gigantoraptor_payload()
        formula_payload = build_formula_candidates(asset_payload)
        asset_memory_card = build_asset_memory_card(asset_payload, formula_payload)
        context_pack = build_default_context_pack(asset_payload, formula_payload, asset_memory_card)
        context_pack_md = render_context_pack(context_pack)

        self.assertEqual(asset_memory_card["schema"], "ark.asset_memory_card.v1")
        self.assertEqual(context_pack["schema"], "ark.context_pack.v1")
        self.assertNotIn('"nodes"', context_pack_md)
        self.assertNotIn('"pins"', context_pack_md)
        self.assertNotIn('"links"', context_pack_md)
        self.assertLess(len(context_pack_md), 12000)
        self.assertEqual(context_pack["budget"], DEFAULT_CONTEXT_BUDGET)
        self.assertLessEqual(context_pack["estimated_tokens"], 1500)
        self.assertIn("Gigantoraptor Feather", context_pack_md)

    def test_question_ranks_matching_formula_defaults_and_graph_first(self):
        asset_payload = {
            "metadata": {"asset_name": "QuestionAsset", "asset_path": "/Game/Test/QuestionAsset.QuestionAsset"},
            "class_defaults": {
                "variables": {
                    "DamageMultiplier": {"value": 4.0, "confidence": "high"},
                    "TamingXPPerSecond": {"value": 12.5, "confidence": "high"},
                }
            },
            "graphs": [
                {
                    "graph_name": "DamageCalculation",
                    "node_count": 80,
                    "payload": {
                        "metadata": {"graph_name": "DamageCalculation", "graph_type": "Function", "node_count": 80},
                        "nodes": [{"function": "ApplyDamage", "variable": "DamageMultiplier"}],
                    },
                },
                {
                    "graph_name": "XPTamingTick",
                    "node_count": 12,
                    "payload": {
                        "metadata": {"graph_name": "XPTamingTick", "graph_type": "Function", "node_count": 12},
                        "nodes": [{"function": "AddExperience", "variable": "TamingXPPerSecond"}],
                    },
                },
            ],
        }
        formula_payload = {
            "candidates": [
                {
                    "id": "damage",
                    "mechanism_type": "damage_multiplier",
                    "graph": "DamageCalculation",
                    "confidence": "high",
                    "visible_rule": "DamageMultiplier = 4.0",
                    "status": "candidate",
                },
                {
                    "id": "taming_xp",
                    "mechanism_type": "xp_reward",
                    "graph": "XPTamingTick",
                    "confidence": "medium",
                    "visible_rule": "TamingXPPerSecond = 12.5",
                    "status": "candidate",
                },
            ],
            "unresolved_formulas": [
                {
                    "id": "damage_unresolved",
                    "candidate_id": "damage",
                    "mechanism_type": "damage_multiplier",
                    "known_visible_part": "DamageMultiplier = 4.0",
                    "blocked_by": ["native damage"],
                },
                {
                    "id": "taming_xp_unresolved",
                    "candidate_id": "taming_xp",
                    "mechanism_type": "xp_reward",
                    "known_visible_part": "TamingXPPerSecond = 12.5",
                    "blocked_by": ["native taming experience"],
                },
            ],
        }
        memory_card = build_asset_memory_card(asset_payload, formula_payload)

        context_pack = build_default_context_pack(
            asset_payload,
            formula_payload,
            memory_card,
            question="驯服经验每秒怎么计算？",
            budget=1200,
        )

        self.assertEqual(context_pack["formula_candidates"][0]["id"], "taming_xp")
        self.assertEqual(context_pack["key_defaults"][0]["name"], "TamingXPPerSecond")
        self.assertEqual(context_pack["key_graphs"][0]["graph"], "XPTamingTick")
        self.assertIn("AddExperience", context_pack["key_graphs"][0]["functions"])
        self.assertEqual(context_pack["unresolved"][0]["candidate_id"], "taming_xp")

    def test_rendered_context_pack_respects_estimated_token_budget(self):
        asset_payload = {
            "metadata": {"asset_name": "BudgetAsset", "asset_path": "/Game/Test/BudgetAsset.BudgetAsset"},
            "class_defaults": {
                "variables": {
                    f"VeryLongDefault{index}": {
                        "value": "value " * 200,
                        "confidence": "high",
                    }
                    for index in range(30)
                }
            },
            "graphs": [
                {
                    "graph_name": f"LargeGraph{index}",
                    "node_count": 500 - index,
                    "payload": {
                        "metadata": {"graph_name": f"LargeGraph{index}", "graph_type": "Function", "node_count": 500 - index},
                        "nodes": [
                            {"function": f"VeryLongFunction{node}_" + ("x" * 200), "variable": f"VeryLongDefault{node % 30}"}
                            for node in range(20)
                        ],
                    },
                }
                for index in range(20)
            ],
        }
        formula_payload = {
            "candidates": [
                {
                    "id": f"candidate_{index}",
                    "mechanism_type": "damage_multiplier",
                    "graph": f"LargeGraph{index}",
                    "confidence": "medium",
                    "visible_rule": "long visible evidence " * 100,
                    "status": "candidate",
                    "missing_evidence": ["missing evidence " * 20],
                }
                for index in range(20)
            ],
            "unresolved_formulas": [],
        }
        memory_card = build_asset_memory_card(asset_payload, formula_payload)

        context_pack = build_default_context_pack(
            asset_payload,
            formula_payload,
            memory_card,
            question="damage multiplier",
            budget=900,
        )
        rendered = render_context_pack(context_pack)

        self.assertLessEqual(context_pack["estimated_tokens"], 900)
        self.assertLessEqual(estimate_tokens(rendered), 900)
        self.assertNotIn("x" * 200, rendered)
        self.assertTrue(context_pack["evidence_pointers"])
        pointer_formula_ids = {
            item.get("id")
            for item in context_pack["evidence_pointers"]
            if isinstance(item, dict) and item.get("kind") == "formula_candidate"
        }
        pointer_graphs = {
            item.get("graph")
            for item in context_pack["evidence_pointers"]
            if isinstance(item, dict) and item.get("kind") == "graph"
        }
        self.assertTrue(
            {item["id"] for item in context_pack["formula_candidates"]}.issubset(pointer_formula_ids)
        )
        self.assertTrue({item["graph"] for item in context_pack["key_graphs"]}.issubset(pointer_graphs))
        for formula_id in pointer_formula_ids:
            self.assertIn(f"- formula_candidate: {formula_id}", rendered)
        for graph in pointer_graphs:
            self.assertIn(f":: {graph}", rendered)

    def test_context_pack_rejects_a_budget_smaller_than_its_public_minimum(self):
        with self.assertRaisesRegex(ValueError, "minimum"):
            build_default_context_pack({}, {}, {}, budget=MIN_CONTEXT_BUDGET - 1)

    def test_mandatory_context_shell_is_compacted_before_rows_are_added(self):
        asset_payload = {
            "metadata": {
                "asset_name": "Asset" + ("A" * 5000),
                "asset_path": "/Game/" + ("VeryLong/" * 1000),
            },
            "class_defaults": {
                "variables": {
                    "ItemDescription": {"value": "!" * 5000, "confidence": "high"},
                }
            },
            "graphs": [],
        }
        formula_payload = {"candidates": [], "unresolved_formulas": []}
        memory_card = build_asset_memory_card(asset_payload, formula_payload)

        context_pack = build_default_context_pack(
            asset_payload,
            formula_payload,
            memory_card,
            budget=MIN_CONTEXT_BUDGET,
            question="?" * 5000,
        )
        rendered = render_context_pack(context_pack)

        self.assertLessEqual(estimate_tokens(rendered), MIN_CONTEXT_BUDGET)
        self.assertEqual(context_pack["estimated_tokens"], estimate_tokens(rendered))

    def test_build_pack_refreshes_a_schema_valid_but_stale_memory_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "RefreshAsset"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps({"asset_name": "RefreshAsset", "asset_path": "/Game/Test/RefreshAsset.RefreshAsset", "graphs": []}),
                encoding="utf-8",
            )
            (asset_dir / "uasset_class_defaults.json").write_text(
                json.dumps({"asset_name": "RefreshAsset", "variables": {"CurrentValue": {"value": 42, "confidence": "high"}}}),
                encoding="utf-8",
            )
            (asset_dir / "uasset_pin_links.json").write_text("{}", encoding="utf-8")
            (output_dir / "formula_candidates.json").write_text(
                json.dumps(
                    {
                        "schema": "ark.blueprint.formula_candidates.v1",
                        "candidates": [{"id": "stale_formula", "mechanism_type": "damage", "graph": "WrongGraph"}],
                        "unresolved_formulas": [],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "asset_memory_card.json").write_text(
                json.dumps({"schema": "ark.asset_memory_card.v1", "asset_name": "RefreshAsset", "key_defaults": [{"name": "StaleValue", "value": -1}]}),
                encoding="utf-8",
            )

            formula, memory_card, _context = build_pack(asset_dir, "", DEFAULT_CONTEXT_BUDGET)

        self.assertNotIn("stale_formula", {item.get("id") for item in formula.get("candidates", [])})
        self.assertEqual(memory_card["key_defaults"][0]["name"], "CurrentValue")
        self.assertEqual(memory_card["key_defaults"][0]["value"], 42)

    def test_context_loader_does_not_parse_the_25mb_pin_link_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "LeanAsset"
            asset_dir.mkdir()
            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps({"asset_name": "LeanAsset", "graphs": []}),
                encoding="utf-8",
            )
            (asset_dir / "uasset_class_defaults.json").write_text(
                json.dumps({"asset_name": "LeanAsset", "variables": {}}),
                encoding="utf-8",
            )
            (asset_dir / "uasset_pin_links.json").write_text("{}", encoding="utf-8")

            def guarded_read(path: Path) -> object:
                if path.name == "uasset_pin_links.json":
                    raise AssertionError("context pack must not load the full pin-link payload")
                return read_optional_json(path)

            with patch("build_asset_context_pack.read_optional_json", side_effect=guarded_read):
                payload = load_asset_payload(asset_dir)

        self.assertEqual(payload["metadata"]["asset_name"], "LeanAsset")

    def test_context_loader_rejects_malformed_capture_shapes_before_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "MalformedAsset"
            asset_dir.mkdir()
            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps({"asset_name": "MalformedAsset", "graphs": "not-a-list"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "graphs"):
                build_pack(asset_dir, "", DEFAULT_CONTEXT_BUDGET)
            self.assertFalse((asset_dir / "output").exists())

            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps({"asset_name": "MalformedAsset", "graphs": [None]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, r"graphs\[0\]"):
                build_pack(asset_dir, "", DEFAULT_CONTEXT_BUDGET)

            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps({"asset_name": "MalformedAsset", "graphs": []}),
                encoding="utf-8",
            )
            (asset_dir / "uasset_class_defaults.json").write_text(
                json.dumps({"variables": {"Broken": 123}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Broken"):
                build_pack(asset_dir, "", DEFAULT_CONTEXT_BUDGET)

    def test_question_pack_uses_a_unique_artifact_without_overwriting_default_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "QuestionAsset"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps({"asset_name": "QuestionAsset", "asset_path": "/Game/Test/QuestionAsset.QuestionAsset", "graphs": []}),
                encoding="utf-8",
            )
            (asset_dir / "uasset_class_defaults.json").write_text(
                json.dumps({"asset_name": "QuestionAsset", "variables": {}}),
                encoding="utf-8",
            )
            default_path = output_dir / "context_pack.md"
            default_path.write_text("DEFAULT_CONTEXT", encoding="utf-8")

            _formula, _memory, pack = build_pack(asset_dir, "damage?", DEFAULT_CONTEXT_BUDGET)

            query_path = Path(str(pack["artifact_path"]))
            default_content = default_path.read_text(encoding="utf-8")
            query_exists = query_path.is_file()
            report_pointers = [
                Path(str(item["path"]))
                for item in pack["evidence_pointers"]
                if isinstance(item, dict) and item.get("kind") == "report" and item.get("path")
            ]
            pointers_exist = all(path.is_file() for path in report_pointers)

        self.assertEqual(default_content, "DEFAULT_CONTEXT")
        self.assertTrue(query_exists)
        self.assertTrue(pointers_exist)
        self.assertIn("context_queries", query_path.parts)

    def test_question_pack_rejects_a_context_queries_symlink_outside_the_capture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset_dir = root / "Asset"
            output_dir = asset_dir / "output"
            outside_dir = root / "Outside"
            output_dir.mkdir(parents=True)
            outside_dir.mkdir()
            (asset_dir / "uasset_graph_nodes.json").write_text(
                json.dumps({"asset_name": "Asset", "asset_path": "/Game/Test/Asset.Asset", "graphs": []}),
                encoding="utf-8",
            )
            try:
                os.symlink(outside_dir, output_dir / "context_queries", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "artifact directory"):
                build_pack(asset_dir, "question", DEFAULT_CONTEXT_BUDGET)
            self.assertEqual(list(outside_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
