import sys
import tempfile
import unittest
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.uasset_graphs import (  # noqa: E402
    compare_clipboard_and_uasset_payloads,
    compare_uasset_with_clipboard,
    build_partial_graph_triage,
    build_quality_gate_payload,
    node_info_from_export,
    mine_graph_candidates,
    object_path_to_uasset_path,
    parse_custom_pins,
    read_uasset_class_defaults,
    parse_export_properties,
    resolve_graph_link_target_pins,
    render_candidate_text,
    render_uasset_class_defaults_report,
    render_pin_link_report,
)


class UAssetGraphCandidateTests(unittest.TestCase):
    def test_object_path_maps_to_devkit_uasset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            asset = content_root / "Genesis2" / "Dinos" / "LionfishLion" / "LionfishLion_Character_BP.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"EventGraph\x00")

            found, attempted = object_path_to_uasset_path(
                "/Game/Genesis2/Dinos/LionfishLion/LionfishLion_Character_BP.LionfishLion_Character_BP",
                extra_roots=[content_root],
            )

        self.assertEqual(found, asset)
        self.assertIn(str(asset), attempted)

    def test_safe_string_scan_keeps_likely_graph_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "Content"
            asset = content_root / "Genesis2" / "Dinos" / "LionfishLion" / "LionfishLion_Character_BP.uasset"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(
                b"\x00".join(
                    [
                        b"Amount Of Combo Meter To Add On Melee Hit",
                        b"pack buff",
                        b"Roar",
                        b"Sleep During Day",
                        b"BPUnstasis",
                        b"BPTimerServer",
                        b"BPPreventRiding",
                        b"collapsed awoken from other buff added",
                        b"/Game/Genesis2/Dinos/LionfishLion/Animations/LionfishLion_Roar_Montage",
                        b"K2Node_CallFunction",
                    ]
                )
            )

            payload, _attempted = mine_graph_candidates(
                "/Game/Genesis2/Dinos/LionfishLion/LionfishLion_Character_BP.LionfishLion_Character_BP",
                extra_roots=[content_root],
                max_candidates=100,
            )

        names = {item["name"] for item in payload["candidates"]}
        self.assertIn("Combo", names)
        self.assertIn("Pack Buff", names)
        self.assertIn("Roar", names)
        self.assertIn("Sleep During Day", names)
        self.assertIn("BPUnstasis", names)
        self.assertIn("BPTimerServer", names)
        self.assertIn("BPPreventRiding", names)
        self.assertIn("collapsed awoken from other buff added", names)
        self.assertNotIn("K2Node_CallFunction", names)
        self.assertNotIn("/Game/Genesis2/Dinos/LionfishLion/Animations/LionfishLion_Roar_Montage", names)
        self.assertIn("BPUnstasis | Unknown", render_candidate_text(payload))

    def test_real_order_tagged_properties_recover_nodes_and_ints(self):
        names = [f"Filler{i}" for i in range(100)] + [
            "Schema",
            "ObjectProperty",
            "Nodes",
            "ArrayProperty",
            "NodePosX",
            "IntProperty",
            "None",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def prop_object(name: str, value: int) -> bytes:
            return fname(name) + fname("ObjectProperty") + struct.pack("<iiB", 0, 4, 0) + struct.pack("<i", value)

        def prop_int(name: str, value: int) -> bytes:
            return fname(name) + fname("IntProperty") + struct.pack("<iiB", 0, 4, 0) + struct.pack("<i", value)

        refs = [2, 3]
        array_value = struct.pack("<i", len(refs)) + b"".join(struct.pack("<i", ref) for ref in refs)
        prop_nodes = (
            fname("Nodes")
            + fname("ArrayProperty")
            + struct.pack("<i", 0)
            + fname("ObjectProperty")
            + struct.pack("<i", 0)
            + struct.pack("<iB", len(array_value), 0)
            + array_value
        )
        data = b"\x00" + prop_object("Schema", -1) + prop_nodes + prop_int("NodePosX", 144) + fname("None")
        exports = [{"display_name": "Graph"}, {"display_name": "NodeA"}, {"display_name": "NodeB"}]

        properties, warnings = parse_export_properties(data, names, [], exports)

        self.assertEqual(warnings, [])
        self.assertEqual(properties["Schema"]["value"], -1)
        self.assertEqual(properties["Nodes"]["value"], refs)
        self.assertEqual(properties["NodePosX"]["value"], 144)

    def test_cdo_class_defaults_recover_scalar_struct_and_soft_object_values(self):
        names = [
            "/Game/Fixture",
            "Fixture",
            "/Script/CoreUObject",
            "MaxStoredXP",
            "MinStoredXPForTreasure",
            "StoredXPTreasureQualityMinMax",
            "TreasureSupplyCrateClass",
            "ExTreasureItemEveryNumLevels",
            "DoubleProperty",
            "IntProperty",
            "StructProperty",
            "SoftObjectProperty",
            "Vector2D",
        ]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        def scalar(name: str, type_name: str, size: int, value: bytes) -> bytes:
            return fname(name) + fname(type_name) + struct.pack("<iiB", 0, size, 2) + (b"G" * 16) + value

        cdo_data = b"".join(
            [
                scalar("MaxStoredXP", "DoubleProperty", 8, struct.pack("<d", 30000.0)),
                scalar("MinStoredXPForTreasure", "DoubleProperty", 8, struct.pack("<d", 5000.0)),
                (
                    fname("StoredXPTreasureQualityMinMax")
                    + fname("StructProperty")
                    + struct.pack("<i", 1)
                    + fname("Vector2D")
                    + struct.pack("<i", 1)
                    + fname("/Script/CoreUObject")
                    + struct.pack("<iiB", 0, 16, 2)
                    + (b"G" * 16)
                    + struct.pack("<dd", 0.000001, 10.000001)
                ),
                scalar("TreasureSupplyCrateClass", "SoftObjectProperty", 4, struct.pack("<i", 1)),
                scalar("ExTreasureItemEveryNumLevels", "IntProperty", 4, struct.pack("<i", 75)),
            ]
        )
        package = {
            "uasset_data": cdo_data,
            "uexp_data": b"",
            "names": names,
            "imports": [],
            "exports": [
                {
                    "object_name": "Default__Fixture_C",
                    "serial_location": {"file": "uasset", "offset": 0, "size": len(cdo_data), "available": True},
                }
            ],
            "soft_object_paths": [
                {"object_path": "/Game/Other.Other_C"},
                {"object_path": "/Game/Fixture/SupplyCrate.SupplyCrate_C"},
            ],
        }

        payload = read_uasset_class_defaults(package, "Fixture")
        report = render_uasset_class_defaults_report(payload)

        variables = payload["variables"]
        self.assertEqual(variables["MaxStoredXP"]["value"], 30000.0)
        self.assertEqual(variables["MinStoredXPForTreasure"]["value"], 5000.0)
        self.assertEqual(variables["StoredXPTreasureQualityMinMax"]["value"]["x"], 0.000001)
        self.assertEqual(variables["StoredXPTreasureQualityMinMax"]["value"]["y"], 10.000001)
        self.assertEqual(variables["TreasureSupplyCrateClass"]["value"], "/Game/Fixture/SupplyCrate.SupplyCrate_C")
        self.assertEqual(variables["ExTreasureItemEveryNumLevels"]["value"], 75)
        self.assertIn("MinStoredXPForTreasure", report)

    def test_custom_pin_scan_recovers_pin_names_and_candidate_links(self):
        names = [f"Filler{i}" for i in range(100)] + ["NodePosX", "IntProperty", "None", "execute", "then", "exec", "object"]

        def fname(name: str) -> bytes:
            return struct.pack("<ii", names.index(name), 0)

        prop = fname("NodePosX") + fname("IntProperty") + struct.pack("<iiB", 0, 4, 0) + struct.pack("<i", 12)
        pin_data = (
            b"A" * 12
            + fname("execute")
            + b"B" * 8
            + struct.pack("<i", 2)
            + b"C" * 8
            + fname("exec")
            + b"D" * 32
            + fname("then")
            + b"E" * 12
            + fname("exec")
            + b"F" * 16
        )
        data = b"\x00" + prop + fname("None") + struct.pack("<i", 0) + struct.pack("<i", 2) + pin_data
        exports = [{"display_name": "Source"}, {"display_name": "Target"}]
        properties, _warnings = parse_export_properties(data, names, [], exports)

        pins, warnings = parse_custom_pins(
            data,
            names,
            properties,
            node_export={"display_name": "Source", "package_index": 1, "class_name": "K2Node_CallFunction"},
            graph_refset={1, 2},
            imports=[],
            exports=exports,
        )

        self.assertEqual(warnings, [])
        self.assertEqual([pin.name for pin in pins], ["execute", "then"])
        self.assertEqual(pins[0].category, "exec")
        self.assertEqual(pins[0].links[0]["target_node"], "Target")
        self.assertEqual(pins[0].source, "uasset_custom_pin_scan")
        self.assertIn(pins[0].confidence, {"medium", "low"})

    def test_node_semantic_reader_emits_function_semantics(self):
        properties = {
            "FunctionReference": {
                "name": "FunctionReference",
                "type": "StructProperty",
                "member_name": "DoThing",
                "confidence": "medium",
            },
            "NodePosX": {"value": 10, "confidence": "high"},
            "NodePosY": {"value": 20, "confidence": "high"},
        }
        pins = []
        node = node_info_from_export(
            node_export={
                "class_name": "K2Node_CallFunction",
                "display_name": "K2Node_CallFunction_1",
                "index": 0,
                "serial_location": {"offset": 100, "size": 44},
            },
            properties=properties,
            pins=pins,
            index=1,
        )

        self.assertEqual(node.function, "DoThing")
        self.assertEqual(node.semantic["kind"], "call_function")
        self.assertEqual(node.raw_offsets, {"start": 100, "end": 144})
        self.assertEqual(node.source, "uasset_binary")

    def test_pin_link_report_summarizes_node_resolved_links(self):
        payload = {
            "asset_name": "Fixture",
            "generated": "now",
            "graphs": [
                {
                    "graph": "EventGraph",
                    "graph_type": "EventGraph",
                    "status": "complete",
                    "confidence": "medium",
                    "payload": {
                        "nodes": [{"name": "Source"}, {"name": "Target"}],
                        "links": [
                            {
                                "source_node": "Source",
                                "source_pin": "then",
                                "source_pin_category": "exec",
                                "target_node": "Target",
                                "target_pin_id": "",
                            }
                        ],
                    },
                }
            ],
        }

        report = render_pin_link_report(payload)

        self.assertIn("node_resolved_pin_unknown", report)
        self.assertIn("EventGraph", report)

    def test_uasset_vs_clipboard_compare_matches_by_class_distribution(self):
        clipboard_payload = {
            "metadata": {"pin_count": 2, "link_count": 1},
            "nodes": [
                {"node_type": "K2Node_Event", "event": "BeginPlay"},
                {"node_type": "K2Node_CallFunction", "function": "DoThing"},
            ],
            "pins": [{}, {}],
            "links": [{}],
        }
        uasset_payload = {
            "metadata": {"pin_count": 2, "link_count": 1},
            "nodes": [
                {"node_type": "K2Node_Event", "event": "BeginPlay"},
                {"node_type": "K2Node_CallFunction", "function": "DoThing"},
            ],
            "pins": [{}, {}],
            "links": [{}],
        }

        result = compare_clipboard_and_uasset_payloads(clipboard_payload, uasset_payload)

        self.assertEqual(result["node_match_ratio"], 1.0)
        self.assertEqual(result["function_hit_ratio"], 1.0)
        self.assertEqual(result["confidence"], "high")

    def test_link_target_pin_resolver_fills_heuristic_target_pin(self):
        from blueprint_translator.models import NodeInfo, PinInfo

        source = NodeInfo(index=1, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Source")
        target = NodeInfo(index=2, class_name="K2Node_CallFunction", node_type="K2Node_CallFunction", name="Target")
        source_pin = PinInfo(id="source_then", name="then", direction="EGPD_Output", category="exec")
        source_pin.links.append({"target_node": "Target", "target_pin_id": "", "confidence": "medium"})
        target_pin = PinInfo(id="target_execute", name="execute", direction="EGPD_Input", category="exec")
        source.pins.append(source_pin)
        target.pins.append(target_pin)

        counts = resolve_graph_link_target_pins([source, target])

        self.assertEqual(counts["resolved_pin_heuristic"], 1)
        self.assertEqual(source_pin.links[0]["target_pin_id"], "target_execute")
        self.assertEqual(source_pin.links[0]["resolution_status"], "resolved_pin_heuristic")

    def test_partial_triage_and_quality_gates_are_structured(self):
        payload = {
            "generated": "now",
            "asset_name": "Fixture",
            "graph_count": 307,
            "node_count": 11117,
            "pin_count": 38640,
            "link_count": 26940,
            "status_counts": {"complete": 250, "partial": 57},
            "failure_category_counts": {"need_manual_clipboard": 1},
            "unknown_properties": [],
            "pin_links": {"summary": {"resolution_counts": {"node_resolved_pin_unknown": 10}, "kind_counts": {"exec": 1}}, "graphs": []},
            "graphs": [
                {
                    "graph": "PartialGraph",
                    "graph_type": "Function",
                    "status": "partial",
                    "confidence": "medium",
                    "node_count": 1,
                    "pin_count": 1,
                    "link_count": 0,
                    "warnings": [],
                    "failure_categories": ["need_manual_clipboard"],
                }
            ],
        }

        triage = build_partial_graph_triage(payload)
        gates = build_quality_gate_payload(payload)

        self.assertEqual(triage["partial_graph_count"], 1)
        self.assertEqual(triage["graphs"][0]["primary_reason"], "manual_only")
        self.assertTrue(gates["passed"])

    def test_compare_uasset_with_clipboard_fixture_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "FixtureAsset"
            graphs_dir = asset_dir / "graphs"
            uasset_dir = asset_dir / "graphs_from_uasset"
            graphs_dir.mkdir(parents=True)
            uasset_dir.mkdir(parents=True)
            (graphs_dir / "EventGraph.txt").write_text(
                """
Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="K2Node_Event_0"
   EventReference=(MemberName="ReceiveBeginPlay")
End Object
""",
                encoding="utf-8",
            )
            (uasset_dir / "EventGraph_1.json").write_text(
                json_dumps(
                    {
                        "metadata": {"graph_name": "EventGraph", "pin_count": 0, "link_count": 0},
                        "nodes": [{"node_type": "K2Node_Event", "event": "ReceiveBeginPlay"}],
                        "pins": [],
                        "links": [],
                    }
                ),
                encoding="utf-8",
            )

            result = compare_uasset_with_clipboard(asset_dir)

        self.assertEqual(result["matched_graph_count"], 1)


def json_dumps(value):
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    unittest.main()
