import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bp_clipboard_to_prompt.py"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_revision import EvidenceArtifactInvalid  # noqa: E402
from blueprint_translator.evidence_repository import EvidenceRepository  # noqa: E402
from blueprint_translator.evidence_writer import write_evidence_artifacts_from_payload  # noqa: E402


def _v2_compare_reader_payload(
    *,
    pin_default: float,
    include_link: bool,
    array_parsed: bool = False,
    array_value: list[object] | None = None,
    harvest_default: dict[str, object] | None = None,
) -> dict[str, object]:
    source_links = (
        [
            {
                "target_node": "ApplyRewardNode",
                "target_pin_id": "apply-execute",
                "target_pin": "execute",
                "resolution_status": "resolved_pin",
                "kind": "exec",
                "source": "fixture",
                "confidence": "high",
            }
        ]
        if include_link
        else []
    )
    graph_payload = {
        "metadata": {
            "asset_name": "V2CompareFixture",
            "graph_name": "EventGraph",
            "graph_type": "EventGraph",
            "uasset_export_index": 7,
            "uasset_read_status": "complete",
            "confidence": "high",
            "node_count": 2,
            "pin_count": 3,
            "link_count": len(source_links),
        },
        "nodes": [
            {
                "index": 1,
                "package_index": 101,
                "name": "ComputeRewardNode",
                "label": "Compute Reward",
                "class_name": "K2Node_CallFunction",
                "node_type": "K2Node_CallFunction",
                "function": "ComputeReward",
                "source": "fixture",
                "confidence": "high",
                "pins": [
                    {
                        "id": "compute-then",
                        "persistent_guid": "compute-then",
                        "name": "then",
                        "direction": "EGPD_Output",
                        "category": "exec",
                        "default": "",
                        "source": "fixture",
                        "confidence": "high",
                        "links": source_links,
                    },
                    {
                        "id": "reward-value",
                        "persistent_guid": "reward-value",
                        "name": "RewardValue",
                        "direction": "EGPD_Output",
                        "category": "float",
                        "default": pin_default,
                        "source": "fixture",
                        "confidence": "high",
                        "links": [],
                    },
                ],
            },
            {
                "index": 2,
                "package_index": 102,
                "name": "ApplyRewardNode",
                "label": "Apply Reward",
                "class_name": "K2Node_CallFunction",
                "node_type": "K2Node_CallFunction",
                "function": "ApplyReward",
                "source": "fixture",
                "confidence": "high",
                "pins": [
                    {
                        "id": "apply-execute",
                        "persistent_guid": "apply-execute",
                        "name": "execute",
                        "direction": "EGPD_Input",
                        "category": "exec",
                        "default": "",
                        "source": "fixture",
                        "confidence": "high",
                        "links": [],
                    }
                ],
            },
        ],
    }
    payload = {
        "asset_name": "V2CompareFixture",
        "asset_path": "/Game/Test/V2CompareFixture.V2CompareFixture",
        "class_defaults": {
            "asset_name": "V2CompareFixture",
            "variables": {
                "RewardMultiplier": {
                    "type": "FloatProperty",
                    "value": 1.5,
                    "source": "fixture",
                    "confidence": "high",
                },
                "StoredXPRewards": {
                    "type": "ArrayProperty",
                    "value": list(array_value or []),
                    "source": "fixture",
                    "confidence": "high" if array_parsed else "low",
                    "array_parse": {
                        "parsed": array_parsed,
                        "count": len(array_value or []) if array_parsed else None,
                        "element_kind": "ObjectProperty" if array_parsed else "unknown",
                        "raw_size": 4,
                    },
                },
                "ResolvedMetal": {
                    "type": "ObjectProperty",
                    "value": -17,
                    "source": "fixture",
                    "confidence": "high",
                    "package_index": -17,
                    "object": "PrimalItemResource_Metal_C",
                },
            },
        },
        "graphs": [
            {
                "graph": "EventGraph",
                "graph_type": "EventGraph",
                "export_index": 7,
                "status": "complete",
                "confidence": "high",
                "failure_categories": [],
                "node_count": 2,
                "pin_count": 3,
                "link_count": len(source_links),
                "payload": graph_payload,
            }
        ],
    }
    if harvest_default is not None:
        payload["class_defaults"]["variables"]["HarvestResourceEntries"] = harvest_default
    return payload


def _metal_style_harvest_default(
    *,
    resource_ref: int,
    damage_refs: list[int],
    damage_names: list[str],
    nested_parsed: bool = True,
) -> dict[str, object]:
    nested_elements = [
        {"index": index, "value": ref, "object": damage_names[index]}
        for index, ref in enumerate(damage_refs)
    ] if nested_parsed else []
    return {
        "type": "ArrayProperty",
        "value": [
            {
                "ResourceItem": resource_ref,
                "DamageTypeEntryValuesOverrides": list(damage_refs),
                "DamageTypeEntryWeightOverrides": [0.63 for _ in damage_refs],
            }
        ],
        "source": "fixture",
        "confidence": "medium",
        "array_parse": {
            "parsed": True,
            "count": 1,
            "element_kind": "StructProperty",
            "elements": [
                {
                    "index": 0,
                    "properties": [
                        {
                            "name": "ResourceItem",
                            "type": "ObjectProperty",
                            "value": resource_ref,
                            "package_index": resource_ref,
                            "object": "PrimalItemResource_Metal_C",
                        },
                        {
                            "name": "DamageTypeEntryValuesOverrides",
                            "type": "ArrayProperty",
                            "value": list(damage_refs),
                            "objects": list(damage_names) if nested_parsed else [],
                            "inner_type": "ObjectProperty",
                            "array_parse": {
                                "parsed": nested_parsed,
                                "count": len(damage_refs) if nested_parsed else None,
                                "element_kind": "ObjectProperty",
                                "elements": nested_elements,
                            },
                        },
                    ],
                }
            ],
        },
    }


def _write_v2_compare_asset(
    asset_dir: pathlib.Path,
    *,
    pin_default: float,
    include_link: bool,
    array_parsed: bool = False,
    array_value: list[object] | None = None,
    harvest_default: dict[str, object] | None = None,
) -> None:
    write_evidence_artifacts_from_payload(
        "/Game/Test/V2CompareFixture.V2CompareFixture",
        None,
        _v2_compare_reader_payload(
            pin_default=pin_default,
            include_link=include_link,
            array_parsed=array_parsed,
            array_value=array_value,
            harvest_default=harvest_default,
        ),
        asset_dir,
        publish_v3=False,
    )


def _prune_v2_compatibility(asset_dir: pathlib.Path) -> None:
    for path in (
        asset_dir / "evidence" / "evidence.sqlite",
        asset_dir / "evidence" / "manifest.json",
        asset_dir / "output" / "agent_index.md",
    ):
        path.unlink(missing_ok=True)


def load_translator():
    spec = importlib.util.spec_from_file_location("bp_translator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CompareTests(unittest.TestCase):
    def test_compare_keeps_one_bound_generation_when_database_path_is_replaced(self):
        bp = load_translator()
        keywords = bp.profile_keywords("ark", [])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            asset_dir = root / "published-a"
            replacement_dir = root / "published-b"
            write_evidence_artifacts_from_payload(
                "/Game/Test/V2CompareFixture.V2CompareFixture",
                None,
                _v2_compare_reader_payload(pin_default=1.0, include_link=True),
                asset_dir,
            )
            write_evidence_artifacts_from_payload(
                "/Game/Test/V2CompareFixture.V2CompareFixture",
                None,
                _v2_compare_reader_payload(pin_default=9.0, include_link=False),
                replacement_dir,
            )
            database_path = next(
                (asset_dir / "evidence" / "revisions").glob("*/evidence.sqlite")
            )
            replacement_database = next(
                (replacement_dir / "evidence" / "revisions").glob("*/evidence.sqlite")
            )
            original_graph_summaries = EvidenceRepository.graph_summaries

            def replace_after_repository_open(
                repository: EvidenceRepository,
            ) -> list[dict[str, object]]:
                rows = original_graph_summaries(repository)
                shutil.copyfile(replacement_database, database_path)
                return rows

            with mock.patch.object(
                EvidenceRepository,
                "graph_summaries",
                autospec=True,
                side_effect=replace_after_repository_open,
            ):
                payload = bp.load_asset_payload_input(
                    SimpleNamespace(), str(asset_dir), keywords
                )

        graph = payload["graphs"][0]["payload"]
        self.assertIsInstance(graph, dict)
        nodes = graph["nodes"]
        reward_pin = next(
            pin
            for node in nodes
            for pin in node["pins"]
            if pin["name"] == "RewardValue"
        )
        self.assertEqual(reward_pin["default"], 1.0)
        self.assertEqual(len(graph["links"]), 1)

    def test_v3_current_compare_survives_pruned_v2_and_rejects_pointer_tamper(self):
        bp = load_translator()
        keywords = bp.profile_keywords("ark", [])
        with tempfile.TemporaryDirectory() as tmp:
            asset_dir = pathlib.Path(tmp) / "current-only"
            write_evidence_artifacts_from_payload(
                "/Game/Test/V2CompareFixture.V2CompareFixture",
                None,
                _v2_compare_reader_payload(pin_default=1.0, include_link=True),
                asset_dir,
            )
            _prune_v2_compatibility(asset_dir)

            payload = bp.load_asset_payload_input(
                SimpleNamespace(), str(asset_dir), keywords
            )
            self.assertEqual(payload["metadata"]["node_count"], 2)

            (asset_dir / "evidence" / "current.json").write_text(
                "{}\n", encoding="utf-8"
            )
            with self.assertRaises(EvidenceArtifactInvalid):
                bp.load_asset_payload_input(SimpleNamespace(), str(asset_dir), keywords)

            pointer_path = asset_dir / "evidence" / "current.json"
            pointer = {
                "schema": "blueprint-to-code.evidence-current/v1",
                "revisionId": "a" * 24,
                "manifest": f"revisions/{'a' * 24}/manifest.json",
                "manifestSha256": "b" * 64,
                "mode": "indexed",
            }
            pointer_path.write_text(json.dumps(pointer) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(FileNotFoundError, "EVIDENCE_REVISION_MISSING"):
                bp.load_asset_payload_input(SimpleNamespace(), str(asset_dir), keywords)

    def test_v2_projection_detects_wiring_and_pin_default_only_changes_with_real_counts(self):
        bp = load_translator()
        keywords = bp.profile_keywords("ark", [])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            baseline_dir = root / "baseline"
            wiring_dir = root / "wiring"
            default_dir = root / "default"
            _write_v2_compare_asset(baseline_dir, pin_default=1.0, include_link=True)
            _write_v2_compare_asset(wiring_dir, pin_default=1.0, include_link=False)
            _write_v2_compare_asset(default_dir, pin_default=2.0, include_link=True)
            baseline_files = {path.relative_to(baseline_dir) for path in baseline_dir.rglob("*") if path.is_file()}

            baseline = bp.load_asset_payload_input(SimpleNamespace(), str(baseline_dir), keywords)
            wiring = bp.load_asset_payload_input(SimpleNamespace(), str(wiring_dir), keywords)
            changed_default = bp.load_asset_payload_input(SimpleNamespace(), str(default_dir), keywords)
            self_diff = bp.compare_asset_payloads(baseline, baseline)
            wiring_diff = bp.compare_asset_payloads(baseline, wiring)
            default_diff = bp.compare_asset_payloads(baseline, changed_default)
            files_after = {path.relative_to(baseline_dir) for path in baseline_dir.rglob("*") if path.is_file()}

        self.assertEqual(baseline["metadata"]["graph_count"], 1)
        self.assertEqual(baseline["metadata"]["node_count"], 2)
        self.assertEqual(baseline["metadata"]["pin_count"], 3)
        self.assertEqual(baseline["metadata"]["wire_count"], 1)
        self.assertEqual(baseline["metadata"]["default_count"], 3)
        self.assertFalse(baseline["class_defaults"]["variables"]["StoredXPRewards"]["value_usable"])
        self.assertEqual(
            baseline["class_defaults"]["variables"]["ResolvedMetal"]["resolved_object_name"],
            "PrimalItemResource_Metal_C",
        )
        self.assertEqual(self_diff["graph_count"], {"old": 1, "new": 1})
        self.assertEqual(self_diff["node_count"], {"old": 2, "new": 2})
        self.assertEqual(self_diff["graph_diffs"][0]["node_count"], {"old": 2, "new": 2})
        self.assertEqual(self_diff["likely_behavior_changes"], [])

        wiring_graph_diff = wiring_diff["graph_diffs"][0]["diff"]
        self.assertTrue(wiring_graph_diff["linked_to_delta"]["removed"])
        self.assertIn("Pin LinkedTo wiring changed.", wiring_graph_diff["likely_logic_changes"])
        default_graph_diff = default_diff["graph_diffs"][0]["diff"]
        self.assertTrue(default_graph_diff["changed_pin_defaults"])
        self.assertIn("Pin default values changed.", default_graph_diff["likely_logic_changes"])
        self.assertEqual(files_after, baseline_files)

    def test_default_comparison_separates_evidence_recovery_from_behavior_change(self):
        bp = load_translator()
        keywords = bp.profile_keywords("ark", [])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            unavailable_dir = root / "unavailable"
            recovered_dir = root / "recovered"
            changed_dir = root / "changed"
            _write_v2_compare_asset(unavailable_dir, pin_default=1.0, include_link=True)
            _write_v2_compare_asset(
                recovered_dir,
                pin_default=1.0,
                include_link=True,
                array_parsed=True,
                array_value=[],
            )
            _write_v2_compare_asset(
                changed_dir,
                pin_default=1.0,
                include_link=True,
                array_parsed=True,
                array_value=["PrimalItemResource_Metal_C"],
            )

            unavailable = bp.load_asset_payload_input(SimpleNamespace(), str(unavailable_dir), keywords)
            recovered = bp.load_asset_payload_input(SimpleNamespace(), str(recovered_dir), keywords)
            changed = bp.load_asset_payload_input(SimpleNamespace(), str(changed_dir), keywords)
            unavailable_self = bp.compare_asset_payloads(unavailable, unavailable)
            recovery_diff = bp.compare_asset_payloads(unavailable, recovered)
            behavior_diff = bp.compare_asset_payloads(recovered, changed)

        self.assertTrue(unavailable_self["unknown_changes"])
        self.assertEqual(unavailable_self["likely_equivalent_changes"], [])
        self.assertNotIn("Class default variables changed.", recovery_diff["likely_behavior_changes"])
        self.assertTrue(any("StoredXPRewards" in note for note in recovery_diff["unknown_changes"]))
        self.assertIn("Class default variables changed.", behavior_diff["likely_behavior_changes"])

    def test_metal_style_nested_object_refs_compare_by_resolved_name_not_package_index(self):
        bp = load_translator()
        keywords = bp.profile_keywords("ark", [])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            baseline_dir = root / "baseline"
            reindexed_dir = root / "reindexed"
            _write_v2_compare_asset(
                baseline_dir,
                pin_default=1.0,
                include_link=True,
                harvest_default=_metal_style_harvest_default(
                    resource_ref=-15,
                    damage_refs=[-8, -9],
                    damage_names=[
                        "DmgType_Melee_MetalHatchet_C",
                        "DmgType_Melee_MetalPick_C",
                    ],
                ),
            )
            _write_v2_compare_asset(
                reindexed_dir,
                pin_default=1.0,
                include_link=True,
                harvest_default=_metal_style_harvest_default(
                    resource_ref=-115,
                    damage_refs=[-108, -109],
                    damage_names=[
                        "DmgType_Melee_MetalHatchet_C",
                        "DmgType_Melee_MetalPick_C",
                    ],
                ),
            )

            baseline = bp.load_asset_payload_input(SimpleNamespace(), str(baseline_dir), keywords)
            reindexed = bp.load_asset_payload_input(SimpleNamespace(), str(reindexed_dir), keywords)
            diff = bp.compare_asset_payloads(baseline, reindexed)

        self.assertNotIn("Class default variables changed.", diff["likely_behavior_changes"])
        self.assertFalse(
            any("HarvestResourceEntries" in note for note in diff["unknown_changes"])
        )

    def test_unparsed_nested_object_refs_are_unknown_not_behavior_changes(self):
        bp = load_translator()
        keywords = bp.profile_keywords("ark", [])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            old_dir = root / "old"
            new_dir = root / "new"
            _write_v2_compare_asset(
                old_dir,
                pin_default=1.0,
                include_link=True,
                harvest_default=_metal_style_harvest_default(
                    resource_ref=-15,
                    damage_refs=[-8, -9],
                    damage_names=[],
                    nested_parsed=False,
                ),
            )
            _write_v2_compare_asset(
                new_dir,
                pin_default=1.0,
                include_link=True,
                harvest_default=_metal_style_harvest_default(
                    resource_ref=-115,
                    damage_refs=[-108, -109],
                    damage_names=[],
                    nested_parsed=False,
                ),
            )

            old = bp.load_asset_payload_input(SimpleNamespace(), str(old_dir), keywords)
            new = bp.load_asset_payload_input(SimpleNamespace(), str(new_dir), keywords)
            diff = bp.compare_asset_payloads(old, new)

        self.assertNotIn("Class default variables changed.", diff["likely_behavior_changes"])
        self.assertTrue(
            any("HarvestResourceEntries" in note for note in diff["unknown_changes"])
        )

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

    def test_compare_asset_detects_graph_sidecar_and_logic_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            old_dir = tmp_path / "old_asset"
            new_dir = tmp_path / "new_asset"
            old_graphs = old_dir / "graphs"
            new_graphs = new_dir / "graphs"
            old_graphs.mkdir(parents=True)
            new_graphs.mkdir(parents=True)
            (old_graphs / "EventGraph.txt").write_text((FIXTURES / "blueprint_old.txt").read_text(encoding="utf-8"), encoding="utf-8")
            (new_graphs / "EventGraph.txt").write_text((FIXTURES / "blueprint_new.txt").read_text(encoding="utf-8"), encoding="utf-8")
            (new_graphs / "Function_InventoryRefresh.txt").write_text((FIXTURES / "real_ark_achatina_inventory_refresh.txt").read_text(encoding="utf-8"), encoding="utf-8")
            (old_dir / "defaults.json").write_text(json.dumps({"variables": {"FeedingRange": 3000}}), encoding="utf-8")
            (new_dir / "defaults.json").write_text(json.dumps({"variables": {"FeedingRange": 4500}}), encoding="utf-8")
            (old_dir / "components.json").write_text(json.dumps({"components": [{"name": "Inventory", "class": "PrimalInventoryComponent", "defaults": {"MaxItems": 100}}]}), encoding="utf-8")
            (new_dir / "components.json").write_text(json.dumps({"components": [{"name": "Inventory", "class": "PrimalInventoryComponent", "defaults": {"MaxItems": 200}}]}), encoding="utf-8")
            for asset_dir in (old_dir, new_dir):
                graphs = [{"name": "EventGraph", "type": "EventGraph", "path": "graphs/EventGraph.txt"}]
                if asset_dir == new_dir:
                    graphs.append({"name": "Function_InventoryRefresh", "type": "Function", "path": "graphs/Function_InventoryRefresh.txt"})
                (asset_dir / "manifest.json").write_text(json.dumps({"asset_name": "TestAsset", "graphs": graphs}), encoding="utf-8")
            asset_snapshots = {
                path: path.read_bytes()
                for asset_dir in (old_dir, new_dir)
                for path in asset_dir.rglob("*")
                if path.is_file()
            }

            out_dir = tmp_path / "out"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--compare-asset",
                    str(old_dir),
                    str(new_dir),
                    "--output-dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertIn("Behavior-relevant changes", result.stdout)
            compare_json = out_dir / "compare.json"
            self.assertTrue(compare_json.exists())
            diff = json.loads(compare_json.read_text(encoding="utf-8"))
            self.assertIn("Function_InventoryRefresh", diff["added_graphs"])
            self.assertTrue(diff["defaults_delta"]["changed"])
            self.assertTrue(diff["components_delta"]["changed"])
            self.assertTrue(any("EventGraph" in note for note in diff["likely_behavior_changes"]))
            report = (out_dir / "compare_report.md").read_text(encoding="utf-8")
            self.assertIn("Blueprint Asset Compare Report", report)
            self.assertIn("Component Delta", report)
            self.assertIn("Function_InventoryRefresh", report)
            impact_report = (out_dir / "behavior_impact_report.md").read_text(encoding="utf-8")
            self.assertIn("Blueprint Behavior Impact Report", impact_report)
            self.assertIn("Impact areas", impact_report)
            self.assertTrue(diff["behavior_impacts"])
            self.assertEqual({path: path.read_bytes() for path in asset_snapshots}, asset_snapshots)

    def test_behavior_impact_classifies_parachute_separately_from_glide(self):
        bp = load_translator()
        diff = {
            "metadata": {"old_asset_name": "Old", "new_asset_name": "New"},
            "graph_count": {"old": 1, "new": 1},
            "node_count": {"old": 1, "new": 1},
            "added_graphs": [],
            "removed_graphs": [],
            "graph_diffs": [{"graph": "SetParachuteState", "likely_logic_changes": ["execution flow changed"]}],
            "defaults_delta": {"changed": {"bWantsToParachute": {"old": False, "new": True}}},
            "components_delta": {},
            "relation_deltas": {},
        }

        rows = bp.build_behavior_impact_rows(diff)
        areas = {row["area"] for row in rows}

        self.assertIn("Parachute", areas)
        self.assertNotIn("Glide", areas)


if __name__ == "__main__":
    unittest.main()
