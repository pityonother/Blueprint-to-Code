from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_asset_context_pack  # noqa: E402
from blueprint_translator.context_pack import estimate_tokens, render_context_pack  # noqa: E402
from blueprint_translator.evidence_revision import EvidenceArtifactInvalid  # noqa: E402
from blueprint_translator.evidence_writer import (  # noqa: E402
    migrate_asset_capture,
    write_evidence_artifacts_from_payload,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_indexed_fixture(root: Path) -> tuple[Path, dict[str, object]]:
    asset_dir = root / "RepositoryContextFixture"
    graph_path = asset_dir / "graphs_from_uasset" / "EventGraph_7.json"
    _write_json(
        graph_path,
        {
            "metadata": {
                "asset_name": "RepositoryContextFixture",
                "graph_name": "EventGraph",
                "graph_type": "EventGraph",
                "uasset_export_index": 7,
                "uasset_read_status": "partial",
                "confidence": "medium",
                "node_count": 2,
                "pin_count": 2,
                "link_count": 1,
            },
            "nodes": [
                {
                    "index": 1,
                    "name": "BeginPlay",
                    "label": "ReceiveBeginPlay",
                    "node_type": "K2Node_Event",
                    "event": "ReceiveBeginPlay",
                    "source": "uasset_binary",
                    "confidence": "high",
                    "large_legacy_detail": "FULL_NODE_BODY_SENTINEL_" + "n" * 4096,
                    "pins": [
                        {
                            "id": "pin-out",
                            "name": "then",
                            "direction": "EGPD_Output",
                            "category": "exec",
                            "default": "FULL_PIN_BODY_SENTINEL_" + "p" * 4096,
                            "source": "uasset_custom_pin_scan",
                            "confidence": "high",
                            "links": [
                                {
                                    "target_node": "ApplyEffectNode",
                                    "target_pin_id": "pin-in",
                                    "target_pin": "execute",
                                    "resolution_status": "resolved_pin",
                                    "kind": "exec",
                                    "source": "uasset_pin_package_index_scan",
                                    "confidence": "high",
                                }
                            ],
                        }
                    ],
                },
                {
                    "index": 2,
                    "name": "ApplyEffectNode",
                    "label": "Apply Effect",
                    "node_type": "K2Node_CallFunction",
                    "function": "ApplyEffect",
                    "source": "uasset_binary",
                    "confidence": "high",
                    "pins": [
                        {
                            "id": "pin-in",
                            "name": "execute",
                            "direction": "EGPD_Input",
                            "category": "exec",
                            "default": "",
                            "source": "uasset_custom_pin_scan",
                            "confidence": "high",
                            "links": [],
                        }
                    ],
                },
            ],
        },
    )
    _write_json(
        asset_dir / "graphs_from_uasset_manifest.json",
        {
            "schema": "blueprint-translator.graphs-from-uasset-manifest.v1",
            "asset_name": "RepositoryContextFixture",
            "asset_path": "/Game/Test/RepositoryContextFixture.RepositoryContextFixture",
            "source_graph_count": 1,
            "graph_file_count": 1,
            "files": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "export_index": 7,
                    "status": "partial",
                    "confidence": "medium",
                    "path": "graphs_from_uasset/EventGraph_7.json",
                }
            ],
        },
    )
    _write_json(
        asset_dir / "uasset_class_defaults.json",
        {
            "asset_name": "RepositoryContextFixture",
            "variables": {
                "MaxHealth": {
                    "type": "FloatProperty",
                    "value": 250.0,
                    "source": "uasset_cdo",
                    "confidence": "high",
                }
            },
        },
    )
    _write_json(
        asset_dir / "uasset_partial_graph_triage.json",
        {
            "asset_name": "RepositoryContextFixture",
            "reason_meanings": {
                "missing_target_pin_id": "One target pin identifier was not recovered."
            },
            "graphs": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "status": "partial",
                    "primary_reason": "missing_target_pin_id",
                    "reasons": ["missing_target_pin_id"],
                    "next_action": "Capture the full graph from the DevKit clipboard.",
                }
            ],
        },
    )
    result = migrate_asset_capture(asset_dir, publish_v3=False)
    return asset_dir, result


def _question_graph(
    *,
    name: str,
    export_index: int,
    function: str,
    failure_category: str,
) -> dict[str, object]:
    node_name = f"K2Node_{function}"
    return {
        "graph": name,
        "graph_type": "Function",
        "export_index": export_index,
        "status": "partial",
        "confidence": "medium",
        "failure_categories": [failure_category],
        "node_count": 1,
        "pin_count": 1,
        "link_count": 0,
        "payload": {
            "metadata": {
                "asset_name": "QuestionContextFixture",
                "graph_name": name,
                "graph_type": "Function",
                "uasset_export_index": export_index,
                "uasset_read_status": "partial",
                "confidence": "medium",
                "node_count": 1,
                "pin_count": 1,
                "link_count": 0,
            },
            "nodes": [
                {
                    "index": 1,
                    "package_index": export_index * 10,
                    "name": node_name,
                    "label": function,
                    "class_name": "K2Node_CallFunction",
                    "node_type": "K2Node_CallFunction",
                    "function": function,
                    "source": "fixture",
                    "confidence": "high",
                    "pins": [
                        {
                            "id": f"{name}-result",
                            "persistent_guid": f"{name}-result",
                            "name": "Result",
                            "direction": "EGPD_Output",
                            "category": "float",
                            "default": "0.0",
                            "source": "fixture",
                            "confidence": "high",
                            "links": [],
                        }
                    ],
                }
            ],
        },
    }


def _make_question_indexed_fixture(root: Path) -> Path:
    asset_dir = root / "QuestionContextFixture"
    payload = {
        "asset_name": "QuestionContextFixture",
        "asset_path": "/Game/Test/QuestionContextFixture.QuestionContextFixture",
        "class_defaults": {
            "asset_name": "QuestionContextFixture",
            "variables": {
                "HealthRegenerationRate": {
                    "type": "FloatProperty",
                    "value": 2.5,
                    "source": "fixture",
                    "confidence": "high",
                },
                "LootRewardAmount": {
                    "type": "IntProperty",
                    "value": 4,
                    "source": "fixture",
                    "confidence": "high",
                },
            },
        },
        "graphs": [
            _question_graph(
                name="HealthRegenerationGraph",
                export_index=11,
                function="ApplyHealthRegeneration",
                failure_category="health_regeneration_source_missing",
            ),
            _question_graph(
                name="LootRewardGraph",
                export_index=22,
                function="GrantLootReward",
                failure_category="loot_reward_table_missing",
            ),
        ],
    }
    write_evidence_artifacts_from_payload(
        "/Game/Test/QuestionContextFixture.QuestionContextFixture",
        None,
        payload,
        asset_dir,
    )
    return asset_dir


class EvidenceRepositoryContextPackTests(unittest.TestCase):
    def test_build_pack_uses_pruned_v3_current_and_fails_closed_on_pointer_tamper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_question_indexed_fixture(Path(temp_dir))
            revision_dir = next((asset_dir / "evidence" / "revisions").iterdir())
            authoritative_index = (revision_dir / "agent_index.md").read_bytes()
            for path in (
                asset_dir / "evidence" / "evidence.sqlite",
                asset_dir / "evidence" / "manifest.json",
                asset_dir / "output" / "agent_index.md",
            ):
                path.unlink(missing_ok=True)

            _formula, _memory, pack = build_asset_context_pack.build_pack(
                asset_dir,
                "health regeneration",
                1200,
            )
            self.assertTrue(pack["revision_id"])
            self.assertEqual((revision_dir / "agent_index.md").read_bytes(), authoritative_index)

            (asset_dir / "evidence" / "current.json").write_text(
                "{}\n", encoding="utf-8"
            )
            with self.assertRaises(EvidenceArtifactInvalid):
                build_asset_context_pack.build_pack(asset_dir, "health", 1200)

    def test_repository_question_selects_distinct_high_signal_refs_and_real_v2_pointers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = _make_question_indexed_fixture(Path(temp_dir))
            _formula, _memory, health_pack = build_asset_context_pack.build_pack(
                asset_dir,
                "How is health regeneration applied?",
                1200,
            )
            loot_pack = build_asset_context_pack.build_pack_from_repository(
                asset_dir,
                question="Where is the loot reward amount decided?",
                budget=1200,
            )
            health_rendered = render_context_pack(health_pack)
            loot_rendered = render_context_pack(loot_pack)

        health_pointers = [row for row in health_pack["evidence_pointers"] if isinstance(row, dict)]
        loot_pointers = [row for row in loot_pack["evidence_pointers"] if isinstance(row, dict)]
        health_refs = {str(row.get("id") or "") for row in health_pointers}
        loot_refs = {str(row.get("id") or "") for row in loot_pointers}

        self.assertEqual(health_pack["question"], "How is health regeneration applied?")
        self.assertEqual(loot_pack["question"], "Where is the loot reward amount decided?")
        self.assertTrue(health_refs)
        self.assertTrue(loot_refs)
        self.assertTrue(health_refs.isdisjoint(loot_refs))
        self.assertIn("node", {str(row.get("kind")) for row in health_pointers})
        self.assertIn("default", {str(row.get("kind")) for row in health_pointers})
        self.assertIn("diagnostic", {str(row.get("kind")) for row in health_pointers})
        self.assertTrue(all(ref.startswith("bp://") for ref in health_refs | loot_refs))
        self.assertNotIn("uasset_graph_nodes.json", health_rendered)
        self.assertNotIn("uasset_graph_nodes.json", loot_rendered)
        self.assertLessEqual(health_pack["estimated_tokens"], 1200)
        self.assertLessEqual(loot_pack["estimated_tokens"], 1200)

    def test_repository_pack_is_bounded_navigable_and_contains_no_full_graph_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, indexed = _make_indexed_fixture(Path(temp_dir))

            pack = build_asset_context_pack.build_pack_from_repository(
                asset_dir,
                budget=1500,
            )
            rendered = render_context_pack(pack)

        self.assertEqual(pack["asset_name"], "RepositoryContextFixture")
        self.assertEqual(pack["revision_id"], indexed["revision_id"])
        self.assertEqual(
            pack["evidence_counts"],
            {
                "graphCount": 1,
                "nodeCount": 2,
                "pinCount": 2,
                "wireCount": 1,
                "linkObservationCount": 1,
                "defaultCount": 1,
                "gapCount": 1,
            },
        )
        self.assertEqual(pack["estimated_tokens"], estimate_tokens(rendered))
        self.assertLessEqual(pack["estimated_tokens"], 1500)

        graph = pack["key_graphs"][0]
        default = pack["key_defaults"][0]
        gap = pack["gaps"][0]
        for evidence_ref in (graph["ref"], default["ref"], gap["ref"]):
            self.assertTrue(str(evidence_ref).startswith("bp://"), evidence_ref)
            self.assertIn(f"@{indexed['revision_id']}", str(evidence_ref))
        self.assertEqual(graph["graph"], "EventGraph")
        self.assertEqual(default["name"], "MaxHealth")
        self.assertEqual(gap["reasonCode"], "missing_target_pin_id")
        self.assertIn("clipboard", str(gap["nextProbe"]).lower())

        next_query = str(pack["next_query"])
        self.assertIn("query_blueprint_evidence.py", next_query)
        self.assertIn("--asset-dir", next_query)
        self.assertIn("search", next_query)
        self.assertIn(str(indexed["revision_id"]), rendered)
        self.assertIn(str(graph["ref"]), rendered)
        self.assertIn(str(default["ref"]), rendered)
        self.assertIn(next_query, rendered)

        serialized = json.dumps(pack, ensure_ascii=False, sort_keys=True)
        self.assertNotIn('"nodes":', serialized)
        self.assertNotIn('"pins":', serialized)
        self.assertNotIn('"links":', serialized)
        self.assertNotIn("FULL_NODE_BODY_SENTINEL", rendered)
        self.assertNotIn("FULL_PIN_BODY_SENTINEL", rendered)

    def test_v2_pack_succeeds_when_legacy_json_is_invalid_and_oversized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir, indexed = _make_indexed_fixture(Path(temp_dir))
            oversized_sentinel = "OVERSIZED_LEGACY_MUST_NOT_BE_READ_"
            (asset_dir / "graphs_from_uasset_manifest.json").write_text(
                "{invalid legacy manifest",
                encoding="utf-8",
            )
            (asset_dir / "graphs_from_uasset" / "EventGraph_7.json").write_text(
                "{invalid legacy graph " + oversized_sentinel * 100_000,
                encoding="utf-8",
            )
            (asset_dir / "uasset_class_defaults.json").write_text(
                "{invalid legacy defaults",
                encoding="utf-8",
            )
            (asset_dir / "uasset_graph_nodes.json").write_text(
                "{invalid oversized legacy index " + oversized_sentinel * 100_000,
                encoding="utf-8",
            )

            pack = build_asset_context_pack.build_pack_from_repository(
                asset_dir,
                budget=1500,
            )
            rendered = render_context_pack(pack)

        self.assertEqual(pack["revision_id"], indexed["revision_id"])
        self.assertEqual(pack["evidence_counts"]["graphCount"], 1)
        self.assertLessEqual(estimate_tokens(rendered), 1500)
        self.assertNotIn(oversized_sentinel, rendered)


if __name__ == "__main__":
    unittest.main()
