from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.evidence_query import EvidenceQueryService  # noqa: E402
from blueprint_translator.evidence_schema import ensure_evidence_schema  # noqa: E402
from blueprint_translator.evidence_writer import write_evidence_store_from_capture  # noqa: E402


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _pin(
    pin_id: str,
    name: str,
    direction: str,
    category: str,
    *,
    default: str = "",
    links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": pin_id,
        "name": name,
        "direction": direction,
        "category": category,
        "subcategory": "",
        "pin_type": {"PinCategory": category, "ContainerType": "None"},
        "default": default,
        "default_object": "",
        "persistent_guid": "",
        "linked_to_raw": "",
        "links": links or [],
        "source": "uasset_exported_pin_object",
        "confidence": "high",
        "warnings": [],
        "raw_offsets": {},
        "resolution": {"status": "resolved_pin", "link_count": len(links or [])},
    }


def _node(
    package_index: int,
    name: str,
    class_name: str,
    *,
    function: str = "",
    variable: str = "",
    event: str = "",
    pins: list[dict[str, object]] | None = None,
    comment: str = "",
) -> dict[str, object]:
    node_pins = pins or []
    return {
        "index": package_index,
        "export_index": package_index - 1,
        "package_index": package_index,
        "key": f"native:{package_index}",
        "label": function or variable or event or name,
        "name": name,
        "class_name": class_name,
        "class": class_name,
        "node_type": class_name,
        "semantic": "fixture node",
        "export_path": class_name,
        "node_guid": "",
        "graph_guid": "",
        "x": package_index * 10,
        "y": 0,
        "function": function,
        "variable": variable,
        "event": event,
        "delegate": "",
        "macro": "",
        "comment": comment,
        "control_kind": "branch" if class_name == "K2Node_IfThenElse" else "call",
        "properties": {},
        "uasset_semantic": {},
        "source": "uasset_binary",
        "confidence": "high",
        "warnings": [],
        "raw_offsets": {"start": package_index * 100, "end": package_index * 100 + 50},
        "pins": node_pins,
        "pin_count": len(node_pins),
        "link_count": sum(len(pin.get("links", [])) for pin in node_pins),
        "keyword_hits": {},
    }


def _edge(
    source_node: str,
    source_pin_id: str,
    source_pin: str,
    target_node: str,
    target_pin_id: str,
    kind: str,
) -> dict[str, object]:
    return {
        "source_node": source_node,
        "source_pin_id": source_pin_id,
        "source_pin": source_pin,
        "source_pin_direction": "EGPD_Output",
        "target_node": target_node,
        "target_pin_id": target_pin_id,
        "kind": kind,
        "resolution_status": "resolved_pin",
        "status": "resolved_pin",
        "link_source": "uasset_exported_pin_linked_to",
        "source": "uasset_exported_pin_linked_to",
        "link_confidence": "high",
        "confidence": "high",
    }


class EvidenceQueryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.asset_dir = root / "TimingAsset"
        self.database_path = root / "timing_asset.evidence.sqlite"
        self._write_legacy_capture(revision_marker="revision-a")
        write_evidence_store_from_capture(self.asset_dir, self.database_path)
        self.service = EvidenceQueryService.open(self.database_path)
        close = getattr(self.service, "close", None)
        if callable(close):
            self.addCleanup(close)

    def _write_legacy_capture(self, *, revision_marker: str) -> None:
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        object_path = "/Game/Test/TimingAsset.TimingAsset"

        entry_to_branch = {
            "target_node": "Branch",
            "target_pin_id": "P_BRANCH_EXEC",
            "source": "uasset_exported_pin_linked_to",
            "confidence": "high",
            "resolution_status": "resolved_pin",
            "kind": "exec",
        }
        now_to_compare = {
            "target_node": "CompareFloat",
            "target_pin_id": "P_COMPARE_A",
            "source": "uasset_exported_pin_linked_to",
            "confidence": "high",
            "resolution_status": "resolved_pin",
            "kind": "data",
        }
        timeout_to_compare = {
            "target_node": "CompareFloat",
            "target_pin_id": "P_COMPARE_B",
            "source": "uasset_exported_pin_linked_to",
            "confidence": "high",
            "resolution_status": "resolved_pin",
            "kind": "data",
        }
        compare_to_branch = {
            "target_node": "Branch",
            "target_pin_id": "P_BRANCH_CONDITION",
            "source": "uasset_exported_pin_linked_to",
            "confidence": "high",
            "resolution_status": "resolved_pin",
            "kind": "data",
        }
        branch_to_success = {
            "target_node": "SuccessCall",
            "target_pin_id": "P_SUCCESS_EXEC",
            "source": "uasset_exported_pin_linked_to",
            "confidence": "high",
            "resolution_status": "resolved_pin",
            "kind": "exec",
        }
        missing_with_candidates = {
            "target_node": "MissingTarget",
            "target_pin_id": "P_UNKNOWN",
            "target_pin_id_candidates": [f"P_CANDIDATE_{index:02d}" for index in range(40)],
            "source": "uasset_exported_pin_linked_to",
            "confidence": "medium",
            "resolution_status": "cross_graph_or_missing_node",
            "status": "cross_graph_or_missing_node",
            "kind": "data",
            "raw_marker": "must-round-trip",
        }

        timing_nodes = [
            _node(
                1001,
                "Entry",
                "K2Node_FunctionEntry",
                event="TimingEntry",
                pins=[_pin("P_ENTRY_THEN", "then", "EGPD_Output", "exec", links=[entry_to_branch])],
            ),
            _node(
                1002,
                "CurrentTime",
                "K2Node_CallFunction",
                function="GetGameTimeInSeconds",
                pins=[_pin("P_NOW_VALUE", "ReturnValue", "EGPD_Output", "float", links=[now_to_compare])],
            ),
            _node(
                1003,
                "NextTimeOutNode",
                "K2Node_VariableGet",
                variable="NextTimeOut",
                pins=[_pin("P_TIMEOUT_VALUE", "NextTimeOut", "EGPD_Output", "float", links=[timeout_to_compare])],
            ),
            _node(
                1004,
                "CompareFloat",
                "K2Node_CallFunction",
                function="Less_FloatFloat",
                pins=[
                    _pin("P_COMPARE_A", "A", "EGPD_Input", "float"),
                    _pin("P_COMPARE_B", "B", "EGPD_Input", "float", default="3.5"),
                    _pin("P_COMPARE_RESULT", "ReturnValue", "EGPD_Output", "bool", links=[compare_to_branch]),
                ],
            ),
            _node(
                1005,
                "Branch",
                "K2Node_IfThenElse",
                pins=[
                    _pin("P_BRANCH_EXEC", "execute", "EGPD_Input", "exec"),
                    _pin("P_BRANCH_CONDITION", "Condition", "EGPD_Input", "bool"),
                    _pin("P_BRANCH_THEN", "then", "EGPD_Output", "exec", links=[branch_to_success]),
                    _pin("P_BRANCH_ELSE", "else", "EGPD_Output", "exec"),
                ],
            ),
            _node(
                1006,
                "SuccessCall",
                "K2Node_CallFunction",
                function="OnBeatAccepted",
                pins=[_pin("P_SUCCESS_EXEC", "execute", "EGPD_Input", "exec")],
            ),
        ]
        timing_edges = [
            _edge("Entry", "P_ENTRY_THEN", "then", "Branch", "P_BRANCH_EXEC", "exec"),
            _edge("CurrentTime", "P_NOW_VALUE", "ReturnValue", "CompareFloat", "P_COMPARE_A", "data"),
            _edge("NextTimeOutNode", "P_TIMEOUT_VALUE", "NextTimeOut", "CompareFloat", "P_COMPARE_B", "data"),
            _edge("CompareFloat", "P_COMPARE_RESULT", "ReturnValue", "Branch", "P_BRANCH_CONDITION", "data"),
            _edge("Branch", "P_BRANCH_THEN", "then", "SuccessCall", "P_SUCCESS_EXEC", "exec"),
        ]
        wide_node = _node(
            1399,
            "WideNode",
            "K2Node_CallFunction",
            function="WideFunction",
            pins=[
                _pin(f"P_WIDE_{index:02d}", f"WidePin{index:02d}", "EGPD_Input", "float")
                for index in range(30)
            ],
        )
        wide_node["properties"] = {
            f"Property{index:02d}": {"value": index, "type": "IntProperty"}
            for index in range(12)
        }

        graph_specs: list[dict[str, object]] = [
            {
                "graph": "TimingGraph",
                "graph_type": "Function",
                "export_index": 100,
                "status": "complete",
                "confidence": "high",
                "nodes": timing_nodes,
                "links": timing_edges,
            },
            {
                "graph": "SharedGraph",
                "graph_type": "Function",
                "export_index": 101,
                "status": "complete",
                "confidence": "high",
                "nodes": [_node(1101, "SharedGraphEntryA", "K2Node_FunctionEntry", event="SharedEntryA")],
                "links": [],
            },
            {
                "graph": "SharedGraph",
                "graph_type": "Macro",
                "export_index": 102,
                "status": "complete",
                "confidence": "medium",
                "nodes": [_node(1201, "SharedGraphEntryB", "K2Node_FunctionEntry", event="SharedEntryB")],
                "links": [],
            },
            {
                "graph": "SearchGraph",
                "graph_type": "Function",
                "export_index": 103,
                "status": "complete",
                "confidence": "high",
                "nodes": [
                    _node(
                        1300 + index,
                        f"SharedSignal_{index}",
                        "K2Node_CallFunction",
                        function=f"SharedSignalHandler_{index}",
                        comment="shared search signal " + ("x" * 120),
                        pins=[_pin(f"P_SHARED_{index}", "ReturnValue", "EGPD_Output", "bool")],
                    )
                    for index in range(8)
                ]
                + [wide_node],
                "links": [],
            },
            {
                "graph": "IncompleteGraph",
                "graph_type": "Function",
                "export_index": 104,
                "status": "partial",
                "confidence": "medium",
                "nodes": [
                    _node(
                        1401,
                        "IncompleteNode",
                        "K2Node_CallFunction",
                        function="UnknownNativeCall",
                        pins=[
                            _pin(
                                "P_INCOMPLETE",
                                "ReturnValue",
                                "EGPD_Output",
                                "bool",
                                links=[missing_with_candidates],
                            )
                        ],
                    )
                ],
                "links": [],
            },
        ]

        manifest_files: list[dict[str, object]] = []
        graph_rows: list[dict[str, object]] = []
        pin_link_graphs: list[dict[str, object]] = []
        for spec in graph_specs:
            graph_name = str(spec["graph"])
            export_index = int(spec["export_index"])
            filename = f"{graph_name.replace(' ', '_')}_{export_index}.json"
            relative_path = f"graphs_from_uasset/{filename}"
            nodes = list(spec["nodes"])
            links = list(spec["links"])
            payload = {
                "metadata": {
                    "generated": revision_marker,
                    "source": "legacy fixture",
                    "source_kind": "uasset_binary",
                    "asset_name": "TimingAsset",
                    "graph_name": graph_name,
                    "graph_type": spec["graph_type"],
                    "uasset_export_index": export_index,
                    "uasset_read_status": spec["status"],
                    "confidence": spec["confidence"],
                    "node_count": len(nodes),
                    "pin_count": sum(len(node.get("pins", [])) for node in nodes),
                    "link_count": len(links),
                },
                "nodes": nodes,
                "pins": [pin for node in nodes for pin in node.get("pins", [])],
                "links": links,
                "diagnostics": {},
            }
            _write_json(self.asset_dir / relative_path, payload)
            manifest_files.append(
                {
                    "graph": graph_name,
                    "graph_type": spec["graph_type"],
                    "export_index": export_index,
                    "status": spec["status"],
                    "confidence": spec["confidence"],
                    "path": relative_path,
                }
            )
            graph_rows.append(
                {
                    "graph": graph_name,
                    "graph_type": spec["graph_type"],
                    "export_index": export_index,
                    "status": spec["status"],
                    "confidence": spec["confidence"],
                    "failure_categories": ["missing_target_pin_id"] if spec["status"] == "partial" else [],
                    "coverage": {},
                    "node_count": len(nodes),
                    "pin_count": sum(len(node.get("pins", [])) for node in nodes),
                    "link_count": len(links),
                    "nodes": [{key: value for key, value in node.items() if key != "pins"} for node in nodes],
                    "warnings": [],
                }
            )
            pin_link_graphs.append(
                {
                    "graph": graph_name,
                    "graph_type": spec["graph_type"],
                    "status": spec["status"],
                    "confidence": spec["confidence"],
                    "link_count": len(links),
                    "unresolved_count": 1 if spec["status"] == "partial" else 0,
                    "links": links,
                    "unresolved": (
                        [
                            {
                                "source_node": "IncompleteNode",
                                "source_pin_id": "P_INCOMPLETE",
                                "target_node": "MissingTarget",
                                "target_pin_id": "",
                                "status": "missing_target_pin_id",
                            }
                        ]
                        if spec["status"] == "partial"
                        else []
                    ),
                }
            )

        _write_json(
            self.asset_dir / "graphs_from_uasset_manifest.json",
            {
                "schema": "blueprint-translator.graphs-from-uasset-manifest.v1",
                "generated": revision_marker,
                "asset_name": "TimingAsset",
                "source_graph_count": len(graph_specs),
                "graph_file_count": len(graph_specs),
                "files": manifest_files,
            },
        )
        _write_json(
            self.asset_dir / "uasset_graph_nodes.json",
            {
                "schema": "blueprint-translator.uasset-graph-nodes.v1",
                "generated": revision_marker,
                "asset_path": object_path,
                "asset_name": "TimingAsset",
                "uasset_path": f"C:/Fixture/{revision_marker}/TimingAsset.uasset",
                "graph_count": len(graph_specs),
                "node_count": sum(len(spec["nodes"]) for spec in graph_specs),
                "pin_count": sum(sum(len(node.get("pins", [])) for node in spec["nodes"]) for spec in graph_specs),
                "link_count": sum(len(spec["links"]) for spec in graph_specs),
                "status_counts": {"complete": 4, "partial": 1},
                "confidence_counts": {"high": 3, "medium": 2},
                "failure_category_counts": {"missing_target_pin_id": 1},
                "node_class_counts": [],
                "graphs": graph_rows,
            },
        )
        _write_json(
            self.asset_dir / "uasset_pin_links.json",
            {
                "schema": "blueprint-translator.uasset-pin-links.v1",
                "generated": revision_marker,
                "asset_path": object_path,
                "asset_name": "TimingAsset",
                "summary": {
                    "link_count": sum(len(spec["links"]) for spec in graph_specs),
                    "resolution_counts": {"resolved_pin": len(timing_edges), "missing_target_pin_id": 1},
                    "kind_counts": {"exec": 2, "data": 3},
                },
                "graphs": pin_link_graphs,
            },
        )
        _write_json(
            self.asset_dir / "uasset_class_defaults.json",
            {
                "schema": "blueprint-translator.uasset-class-defaults.v1",
                "generated": revision_marker,
                "loaded": True,
                "asset_name": "TimingAsset",
                "default_object": "Default__TimingAsset_C",
                "export_index": 500,
                "property_count": 6,
                "variable_count": 6,
                "variables": {
                    "NextTimeOut": {
                        "value": 4.2 if revision_marker == "revision-a" else 5.0,
                        "type": "FloatProperty",
                        "source": "uasset_cdo",
                        "confidence": "high",
                    },
                    "BeatWindow": {
                        "value": 0.35,
                        "type": "FloatProperty",
                        "source": "uasset_cdo",
                        "confidence": "high",
                    },
                    "LargePayload": {
                        "value": "X" * 12000,
                        "type": "StrProperty",
                        "source": "uasset_cdo",
                        "confidence": "high",
                    },
                    "UndecodedEntries": {
                        "value": [],
                        "type": "ArrayProperty",
                        "source": "uasset_cdo",
                        "confidence": "low",
                        "array_parse": {
                            "parsed": False,
                            "element_kind": "unknown",
                            "raw_size": 96,
                        },
                    },
                    "ConfirmedEmptyEntries": {
                        "value": [],
                        "type": "ArrayProperty",
                        "source": "uasset_cdo",
                        "confidence": "high",
                        "array_parse": {
                            "parsed": True,
                            "count": 0,
                            "element_kind": "ObjectProperty",
                            "raw_size": 4,
                        },
                    },
                    "ResolvedMetal": {
                        "value": -17,
                        "type": "ObjectProperty",
                        "source": "uasset_cdo",
                        "confidence": "high",
                        "package_index": -17,
                        "object": "PrimalItemResource_Metal_C",
                    },
                },
                "properties": [],
                "warnings": [],
            },
        )
        _write_json(
            self.asset_dir / "uasset_partial_graph_triage.json",
            {
                "schema": "blueprint-translator.uasset-partial-graph-triage.v1",
                "generated": revision_marker,
                "asset_path": object_path,
                "asset_name": "TimingAsset",
                "partial_graph_count": 1,
                "reason_counts": {"missing_target_pin_id": 1},
                "reason_meanings": {
                    "missing_target_pin_id": "The target pin could not be recovered from the binary graph."
                },
                "graphs": [
                    {
                        "graph": "IncompleteGraph",
                        "graph_type": "Function",
                        "status": "partial",
                        "confidence": "medium",
                        "primary_reason": "missing_target_pin_id",
                        "reasons": ["missing_target_pin_id"],
                        "node_count": 1,
                        "pin_count": 1,
                        "link_count": 0,
                        "coverage": {"node_pin_coverage": 1.0, "node_link_coverage": 0.0},
                        "next_action": "Capture the full graph from the DevKit clipboard.",
                        "warnings": [],
                    }
                ],
            },
        )
        _write_json(
            self.asset_dir / "uasset_failed_graph_queue.json",
            {
                "schema": "blueprint-translator.uasset-failed-graph-queue.v1",
                "generated": revision_marker,
                "asset_name": "TimingAsset",
                "category_meanings": {},
                "graphs": [],
            },
        )

    @staticmethod
    def _compact_token_count(payload: dict[str, object]) -> int:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return estimate_tokens(serialized)

    def _search(self, query: str, *, kinds: list[str] | None = None, **extra: object) -> dict[str, object]:
        request: dict[str, object] = {
            "operation": "search",
            "query": query,
            "budgetTokens": 2000,
            "pageSize": 100,
        }
        if kinds is not None:
            request["kinds"] = kinds
        request.update(extra)
        return self.service.query(request)

    def _ref_for(self, query: str, *, kind: str, name: str) -> str:
        result = self._search(query, kinds=[kind])
        matches = [
            item
            for item in result["items"]
            if item.get("kind") == kind and item.get("name") == name
        ]
        self.assertTrue(matches, f"missing {kind} named {name!r} in {result['items']!r}")
        return str(matches[0]["ref"])

    def test_overview_returns_identity_counts_and_revision(self):
        result = self.service.query({"operation": "overview", "budgetTokens": 800})
        gaps = self.service.query(
            {"operation": "gaps", "pageSize": 100, "budgetTokens": 2400}
        )

        self.assertEqual(result["operation"], "overview")
        self.assertEqual(result["asset"]["name"], "TimingAsset")
        self.assertEqual(result["asset"]["objectPath"], "/Game/Test/TimingAsset.TimingAsset")
        self.assertTrue(result["asset"]["revisionId"])
        self.assertEqual(result["summary"]["graphCount"], 5)
        self.assertEqual(result["summary"]["nodeCount"], 18)
        self.assertEqual(result["summary"]["defaultCount"], 6)
        self.assertEqual(result["summary"]["gapCount"], gaps["coverage"]["requested"])
        self.assertEqual(
            result["coverage"]["byStatus"]["NOT_RECOVERED"],
            gaps["coverage"]["byStatus"]["NOT_RECOVERED"],
        )

    def test_budget_floor_is_rejected_and_original_request_is_preserved_when_capped(self):
        for operation in ("overview", "search", "gaps"):
            request: dict[str, object] = {"operation": operation, "budgetTokens": 1}
            if operation == "search":
                request["query"] = "NextTimeOut"
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "at least 500"):
                self.service.query(request)

        capped = self.service.query({"operation": "overview", "budgetTokens": 9000})
        self.assertEqual(capped["budget"]["requested"], 9000)
        self.assertEqual(capped["budget"]["effective"], 8000)
        self.assertLessEqual(self._compact_token_count(capped), 8000)

    def test_search_finds_nodes_and_defaults_without_reading_a_full_report(self):
        result = self._search("NextTimeOut", kinds=["node", "default"])

        pairs = {(item["kind"], item["name"]) for item in result["items"]}
        self.assertIn(("node", "NextTimeOutNode"), pairs)
        self.assertIn(("default", "NextTimeOut"), pairs)
        self.assertTrue(all(item.get("ref") for item in result["items"]))

    def test_materialized_search_remains_ascii_case_insensitive_without_rowwise_lower(self):
        result = self._search("nexttimeout", kinds=["node", "default"])

        pairs = {(item["kind"], item["name"]) for item in result["items"]}
        self.assertIn(("node", "NextTimeOutNode"), pairs)
        self.assertIn(("default", "NextTimeOut"), pairs)

    def test_entity_resolves_the_exact_search_reference(self):
        node_ref = self._ref_for("CompareFloat", kind="node", name="CompareFloat")

        result = self.service.query(
            {
                "operation": "entity",
                "selector": {"ref": node_ref},
                "budgetTokens": 600,
            }
        )

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["ref"], node_ref)
        self.assertEqual(result["items"][0]["name"], "CompareFloat")
        self.assertEqual(result["items"][0]["kind"], "node")
        self.assertLessEqual(self._compact_token_count(result), 600)

    def test_entity_round_trips_plain_json_default_and_pin_values(self):
        default_ref = self._ref_for("NextTimeOut", kind="default", name="NextTimeOut")
        pin_ref = self._ref_for("B", kind="pin", name="B")

        default_result = self.service.query(
            {
                "operation": "entity",
                "selector": {"ref": default_ref},
                "budgetTokens": 800,
            }
        )
        pin_result = self.service.query(
            {
                "operation": "entity",
                "selector": {"ref": pin_ref},
                "budgetTokens": 800,
            }
        )

        self.assertEqual(default_result["items"][0]["value"], 4.2)
        self.assertEqual(pin_result["items"][0]["default"], "3.5")

    def test_default_entity_distinguishes_unparsed_and_confirmed_empty_arrays(self):
        undecoded_ref = self._ref_for("UndecodedEntries", kind="default", name="UndecodedEntries")
        empty_ref = self._ref_for("ConfirmedEmptyEntries", kind="default", name="ConfirmedEmptyEntries")

        undecoded = self.service.query(
            {"operation": "entity", "selector": {"ref": undecoded_ref}, "budgetTokens": 1200}
        )["items"][0]
        confirmed_empty = self.service.query(
            {"operation": "entity", "selector": {"ref": empty_ref}, "budgetTokens": 1200}
        )["items"][0]

        self.assertEqual(undecoded["value"], [])
        self.assertEqual(undecoded["valueStatus"], "NOT_RECOVERED")
        self.assertFalse(undecoded["valueUsable"])
        self.assertFalse(undecoded["parse"]["parsed"])
        self.assertEqual(confirmed_empty["value"], [])
        self.assertEqual(confirmed_empty["valueStatus"], "CONFIRMED")
        self.assertTrue(confirmed_empty["valueUsable"])
        self.assertEqual(confirmed_empty["parse"]["count"], 0)

    def test_default_entity_exposes_resolved_object_name(self):
        default_ref = self._ref_for("ResolvedMetal", kind="default", name="ResolvedMetal")

        item = self.service.query(
            {"operation": "entity", "selector": {"ref": default_ref}, "budgetTokens": 1200}
        )["items"][0]

        self.assertEqual(item["resolvedObjectName"], "PrimalItemResource_Metal_C")
        self.assertEqual(item["valueStatus"], "CONFIRMED")

    def test_default_entity_coverage_counts_projected_value_status(self):
        confirmed_ref = self._ref_for("ResolvedMetal", kind="default", name="ResolvedMetal")
        undecoded_ref = self._ref_for("UndecodedEntries", kind="default", name="UndecodedEntries")

        confirmed = self.service.query(
            {"operation": "entity", "selector": {"ref": confirmed_ref}, "budgetTokens": 1200}
        )
        undecoded = self.service.query(
            {"operation": "entity", "selector": {"ref": undecoded_ref}, "budgetTokens": 1200}
        )

        self.assertEqual(confirmed["coverage"]["byStatus"]["CONFIRMED"], 1)
        self.assertEqual(confirmed["coverage"]["notRecovered"], 0)
        self.assertEqual(undecoded["coverage"]["byStatus"]["NOT_RECOVERED"], 1)
        self.assertEqual(undecoded["coverage"]["notRecovered"], 1)

    def test_statusless_entity_coverage_counts_returned_canonical_fact_as_confirmed(self):
        node_ref = self._ref_for("WideNode", kind="node", name="WideNode")
        pin_ref = self._ref_for("P_WIDE_00", kind="pin", name="WidePin00")
        node_result = self.service.query(
            {
                "operation": "entity",
                "selector": {"ref": node_ref},
                "propertyLimit": 1,
                "observationLimit": 0,
                "budgetTokens": 1200,
            }
        )
        property_ref = str(node_result["items"][0]["properties"][0]["ref"])
        pin_result = self.service.query(
            {"operation": "entity", "selector": {"ref": pin_ref}, "budgetTokens": 1200}
        )
        property_result = self.service.query(
            {"operation": "entity", "selector": {"ref": property_ref}, "budgetTokens": 1200}
        )

        for result in (node_result, pin_result, property_result):
            self.assertEqual(result["coverage"]["returned"], 1)
            self.assertEqual(result["coverage"]["byStatus"]["CONFIRMED"], 1)
            self.assertEqual(result["coverage"]["notRecovered"], 0)

    def test_neighborhood_returns_only_atomic_node_pin_edge_bundles(self):
        branch_ref = self._ref_for("Branch", kind="node", name="Branch")

        result = self.service.query(
            {
                "operation": "neighborhood",
                "selector": {"ref": branch_ref},
                "traversal": {"maxHops": 1, "direction": "both", "edgeKinds": ["exec", "data"]},
                "budgetTokens": 2400,
            }
        )

        self.assertTrue(result["items"])
        self.assertIn("Branch", {item["node"]["name"] for item in result["items"]})
        for bundle in result["items"]:
            self.assertEqual(bundle["kind"], "node_bundle")
            self.assertEqual(len(bundle["pins"]), bundle["bundleCoverage"]["pins"]["available"])
            self.assertEqual(len(bundle["pins"]), bundle["bundleCoverage"]["pins"]["returned"])
            self.assertEqual(len(bundle["edges"]), bundle["bundleCoverage"]["edges"]["available"])
            self.assertEqual(len(bundle["edges"]), bundle["bundleCoverage"]["edges"]["returned"])

    def test_trace_follows_only_the_requested_edge_kind_and_direction(self):
        entry_ref = self._ref_for("TimingEntry", kind="node", name="Entry")

        result = self.service.query(
            {
                "operation": "trace",
                "selector": {"ref": entry_ref},
                "traversal": {"maxHops": 2, "direction": "downstream", "edgeKinds": ["exec"]},
                "budgetTokens": 2400,
            }
        )

        names = [item["node"]["name"] for item in result["items"]]
        self.assertEqual(names[:3], ["Entry", "Branch", "SuccessCall"])
        self.assertNotIn("CurrentTime", names)
        self.assertNotIn("CompareFloat", names)
        self.assertTrue(all(edge["kind"] == "exec" for item in result["items"] for edge in item["edges"]))

    def test_gaps_exposes_not_recovered_reason_and_next_probe(self):
        result = self.service.query({"operation": "gaps", "budgetTokens": 1200})

        self.assertGreaterEqual(result["coverage"]["byStatus"]["NOT_RECOVERED"], 1)
        gaps = [item for item in result["items"] if item.get("reasonCode") == "missing_target_pin_id"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["status"], "NOT_RECOVERED")
        self.assertIn("clipboard", gaps[0]["nextProbe"].lower())

    def test_gaps_exposes_unparsed_default_without_misreporting_true_empty_array(self):
        result = self.service.query(
            {
                "operation": "gaps",
                "reasonCode": "array_property_not_decoded",
                "pageSize": 100,
                "budgetTokens": 1600,
            }
        )

        gaps = [item for item in result["items"] if item.get("reasonCode") == "array_property_not_decoded"]
        self.assertEqual([item["title"] for item in gaps], ["UndecodedEntries was not decoded"])
        self.assertEqual(gaps[0]["scopeKind"], "default")
        self.assertEqual(gaps[0]["status"], "NOT_RECOVERED")

    def test_same_name_search_results_are_returned_as_distinct_references(self):
        result = self._search("SharedGraph", kinds=["graph"])

        matches = [item for item in result["items"] if item.get("name") == "SharedGraph"]
        self.assertEqual(len(matches), 2)
        self.assertEqual(len({item["ref"] for item in matches}), 2)

    def test_pure_wildcard_search_is_rejected_before_full_library_sort(self):
        with self.assertRaisesRegex(ValueError, "overview|specific search term"):
            self._search("***", kinds=["graph", "node", "pin", "default"])

    def test_fully_materialized_kinds_do_not_scan_canonical_fallback_tables(self):
        with mock.patch.object(
            self.service,
            "_fallback_search_rows",
            wraps=self.service._fallback_search_rows,
        ) as fallback:
            result = self._search("NextTimeOut", kinds=["node", "default"])

        self.assertEqual(fallback.call_count, 0)
        self.assertEqual(
            {(item["kind"], item["name"]) for item in result["items"]},
            {("node", "NextTimeOutNode"), ("default", "NextTimeOut")},
        )

    def test_mixed_search_falls_back_only_for_diagnostic_kind(self):
        with mock.patch.object(
            self.service,
            "_fallback_search_rows",
            wraps=self.service._fallback_search_rows,
        ) as fallback:
            result = self._search(
                "missing_target_pin_id",
                kinds=["node", "diagnostic"],
            )

        self.assertEqual(fallback.call_count, 1)
        self.assertEqual(tuple(fallback.call_args.args[0]), ("diagnostic",))
        self.assertIn("diagnostic", {item["kind"] for item in result["items"]})

    def test_edge_observation_search_stays_on_explicit_fallback(self):
        with mock.patch.object(
            self.service,
            "_fallback_search_rows",
            wraps=self.service._fallback_search_rows,
        ) as fallback:
            result = self._search("MissingTarget", kinds=["edge_observation"])

        self.assertEqual(fallback.call_count, 1)
        self.assertEqual(tuple(fallback.call_args.args[0]), ("edge_observation",))
        self.assertEqual([item["kind"] for item in result["items"]], ["edge_observation"])

    def test_empty_legacy_search_projection_keeps_fallback_recall_and_cursor(self):
        self.service.close()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("DELETE FROM search_entities")
            connection.execute("DROP TABLE IF EXISTS search_materialization")
            connection.commit()
        finally:
            connection.close()
        self.service = EvidenceQueryService.open(self.database_path)
        self.addCleanup(self.service.close)

        exact = self._search("SharedGraph", kinds=["graph"])
        substring = self._search("NextTime", kinds=["node", "default"])
        first = self._search("SharedSignal", kinds=["node"], pageSize=2)
        second = self._search(
            "SharedSignal",
            kinds=["node"],
            pageSize=2,
            cursor=first["page"]["nextCursor"],
        )

        self.assertEqual(len([item for item in exact["items"] if item["name"] == "SharedGraph"]), 2)
        self.assertIn("NextTimeOutNode", {item["name"] for item in substring["items"]})
        self.assertIn("NextTimeOut", {item["name"] for item in substring["items"]})
        self.assertTrue(first["page"]["nextCursor"])
        self.assertFalse(
            {item["ref"] for item in first["items"]}
            & {item["ref"] for item in second["items"]}
        )

    def test_the_entire_compact_serialized_response_obeys_the_hard_budget(self):
        budget = 500
        result = self._search(
            "SharedSignal",
            kinds=["node"],
            budgetTokens=budget,
            pageSize=100,
        )

        self.assertLessEqual(self._compact_token_count(result), budget)
        self.assertGreater(result["coverage"]["byStatus"]["AVAILABLE_NOT_RETURNED"], 0)

    def test_search_cursor_pages_have_no_gaps_or_duplicates(self):
        full = self._search("SharedSignal", kinds=["node"], pageSize=100, budgetTokens=4000)
        expected_refs = {item["ref"] for item in full["items"]}
        cursor: str | None = None
        paged_refs: list[str] = []

        for _page in range(20):
            request: dict[str, object] = {
                "operation": "search",
                "query": "SharedSignal",
                "kinds": ["node"],
                "pageSize": 2,
                "budgetTokens": 1600,
            }
            if cursor is not None:
                request["cursor"] = cursor
            result = self.service.query(request)
            paged_refs.extend(str(item["ref"]) for item in result["items"])
            cursor = result["page"]["nextCursor"]
            if cursor is None:
                break
        else:
            self.fail("search pagination did not terminate")

        self.assertEqual(len(paged_refs), len(set(paged_refs)))
        self.assertEqual(set(paged_refs), expected_refs)

    def test_cursor_from_an_older_revision_is_rejected(self):
        first_page = self._search(
            "SharedSignal",
            kinds=["node"],
            pageSize=2,
            budgetTokens=1600,
        )
        stale_cursor = first_page["page"]["nextCursor"]
        self.assertTrue(stale_cursor)

        close = getattr(self.service, "close", None)
        if callable(close):
            close()
        self._write_legacy_capture(revision_marker="revision-b")
        write_evidence_store_from_capture(self.asset_dir, self.database_path)
        self.service = EvidenceQueryService.open(self.database_path)
        replacement_close = getattr(self.service, "close", None)
        if callable(replacement_close):
            self.addCleanup(replacement_close)

        with self.assertRaisesRegex(ValueError, "STALE_CURSOR"):
            self.service.query(
                {
                    "operation": "search",
                    "query": "SharedSignal",
                    "kinds": ["node"],
                    "pageSize": 2,
                    "budgetTokens": 1600,
                    "cursor": stale_cursor,
                }
            )

    def test_neighborhood_rejects_more_than_three_hops(self):
        branch_ref = self._ref_for("Branch", kind="node", name="Branch")

        with self.assertRaisesRegex(ValueError, "maxHops.*3|3.*maxHops"):
            self.service.query(
                {
                    "operation": "neighborhood",
                    "selector": {"ref": branch_ref},
                    "traversal": {"maxHops": 4, "direction": "both", "edgeKinds": ["exec", "data"]},
                    "budgetTokens": 1200,
                }
            )

    def test_large_value_and_candidate_pages_never_exceed_budget_or_skip_data(self):
        large_ref = self._ref_for("LargePayload", kind="default", name="LargePayload")
        value_offset = 0
        value_pages: list[str] = []
        available_chars = 0
        for _ in range(100):
            result = self.service.query(
                {
                    "operation": "entity",
                    "selector": {"ref": large_ref},
                    "valueOffset": value_offset,
                    "valueChars": 600,
                    "budgetTokens": 800,
                }
            )
            self.assertLessEqual(self._compact_token_count(result), 800)
            self.assertTrue(result["items"])
            item = result["items"][0]
            coverage = item["valueCoverage"]
            available_chars = int(coverage["availableChars"])
            returned = int(coverage["returnedChars"])
            self.assertEqual(int(coverage["offset"]), value_offset)
            self.assertGreater(returned, 0)
            value_pages.append(str(item["valueJsonPage"]))
            next_values = [
                query
                for query in result["nextQueries"]
                if query.get("operation") == "entity" and "valueOffset" in query
            ]
            if not next_values:
                break
            self.assertEqual(int(next_values[0]["valueOffset"]), value_offset + returned)
            value_offset = int(next_values[0]["valueOffset"])
        else:
            self.fail("large value pagination did not terminate")
        raw_value = "".join(value_pages)
        self.assertEqual(len(raw_value), available_chars)
        self.assertEqual(json.loads(raw_value), "X" * 12000)

        observation_search = self._search(
            "MissingTarget",
            kinds=["edge_observation"],
            budgetTokens=1200,
        )
        self.assertEqual(len(observation_search["items"]), 1)
        observation_ref = str(observation_search["items"][0]["ref"])
        too_small = self.service.query(
            {
                "operation": "entity",
                "selector": {"ref": observation_ref},
                "candidateOffset": 0,
                "candidateLimit": 25,
                "budgetTokens": 800,
            }
        )
        self.assertLessEqual(self._compact_token_count(too_small), 800)
        if not too_small["items"]:
            self.assertEqual(int(too_small["nextQueries"][0]["candidateOffset"]), 0)
        candidate_offset = 0
        candidate_ids: list[str] = []
        for _ in range(100):
            result = self.service.query(
                {
                    "operation": "entity",
                    "selector": {"ref": observation_ref},
                    "candidateOffset": candidate_offset,
                    "candidateLimit": 25,
                    "budgetTokens": 1200,
                }
            )
            self.assertLessEqual(self._compact_token_count(result), 1200)
            self.assertTrue(result["items"])
            item = result["items"][0]
            candidate_ids.extend(str(row["nativePinId"]) for row in item["candidates"])
            returned = int(item["candidateCoverage"]["returned"])
            next_candidates = [
                query
                for query in result["nextQueries"]
                if query.get("operation") == "entity" and "candidateOffset" in query
            ]
            if not next_candidates:
                break
            self.assertGreater(returned, 0)
            self.assertEqual(int(next_candidates[0]["candidateOffset"]), candidate_offset + returned)
            candidate_offset = int(next_candidates[0]["candidateOffset"])
        else:
            self.fail("candidate pagination did not terminate")
        self.assertEqual(candidate_ids, [f"P_CANDIDATE_{index:02d}" for index in range(40)])

    def test_observation_is_discoverable_from_search_pin_and_gaps(self):
        search = self._search("MissingTarget", kinds=["edge_observation"])
        self.assertEqual(len(search["items"]), 1)
        observation_ref = str(search["items"][0]["ref"])
        observation = self.service.query(
            {
                "operation": "entity",
                "selector": {"ref": observation_ref},
                "candidateLimit": 1,
                "budgetTokens": 1200,
            }
        )["items"][0]
        self.assertEqual(observation["status"], "NOT_RECOVERED")
        self.assertEqual(observation["rawEvidence"]["raw_marker"], "must-round-trip")

        pin_ref = self._ref_for("P_INCOMPLETE", kind="pin", name="ReturnValue")
        pin = self.service.query(
            {
                "operation": "entity",
                "selector": {"ref": pin_ref},
                "budgetTokens": 1200,
            }
        )["items"][0]
        self.assertIn(observation_ref, {row["ref"] for row in pin["observations"]})

        gaps = self.service.query(
            {
                "operation": "gaps",
                "selector": {"ref": observation["graphRef"]},
                "pageSize": 100,
                "budgetTokens": 2000,
            }
        )
        self.assertIn(observation_ref, {row["ref"] for row in gaps["items"]})
        self.assertGreaterEqual(gaps["coverage"]["byStatus"]["NOT_RECOVERED"], 1)


    def test_node_properties_and_connection_bundles_have_lossless_continuations(self):
        node_ref = self._ref_for("WideNode", kind="node", name="WideNode")
        property_offset = 0
        property_refs: list[str] = []
        property_request: dict[str, object] = {
            "operation": "entity",
            "selector": {"ref": node_ref},
            "propertyOffset": property_offset,
            "propertyLimit": 5,
            "budgetTokens": 600,
        }
        for _ in range(20):
            result = self.service.query(property_request)
            self.assertLessEqual(
                self._compact_token_count(result),
                int(property_request["budgetTokens"]),
            )
            if not result["items"]:
                self.assertTrue(result["nextQueries"])
                self.assertEqual(int(result["nextQueries"][0]["propertyOffset"]), property_offset)
                property_request = dict(result["nextQueries"][0])
                continue
            item = result["items"][0]
            property_refs.extend(str(row["ref"]) for row in item.get("properties", []))
            next_properties = [
                query for query in result["nextQueries"] if "propertyOffset" in query
            ]
            if not next_properties:
                self.assertEqual(item["propertyCoverage"]["available"], len(property_refs))
                break
            returned = int(item["propertyCoverage"]["returned"])
            self.assertEqual(int(next_properties[0]["propertyOffset"]), property_offset + returned)
            if returned == 0:
                self.assertGreater(
                    int(next_properties[0]["budgetTokens"]),
                    int(property_request["budgetTokens"]),
                )
            property_offset = int(next_properties[0]["propertyOffset"])
            property_request = dict(next_properties[0])
        else:
            self.fail("property pagination did not terminate")
        self.assertEqual(len(property_refs), 12)
        self.assertEqual(len(property_refs), len(set(property_refs)))

        request: dict[str, object] = {
            "operation": "neighborhood",
            "selector": {"ref": node_ref},
            "traversal": {"maxHops": 0, "direction": "both", "edgeKinds": ["exec", "data"]},
            "pinLimit": 8,
            "edgeLimit": 8,
            "budgetTokens": 1500,
        }
        pin_refs: list[str] = []
        for _ in range(20):
            result = self.service.query(request)
            self.assertLessEqual(self._compact_token_count(result), 1500)
            self.assertEqual(len(result["items"]), 1)
            bundle = result["items"][0]
            pin_refs.extend(str(row["ref"]) for row in bundle["pins"])
            continuation = bundle["bundleCoverage"].get("nextQuery")
            if continuation is None:
                self.assertEqual(bundle["bundleCoverage"]["pins"]["available"], len(pin_refs))
                break
            request = dict(continuation)
        else:
            self.fail("connection pagination did not terminate")
        self.assertEqual(len(pin_refs), 30)
        self.assertEqual(len(pin_refs), len(set(pin_refs)))


class EvidenceSearchIndexScaleTests(unittest.TestCase):
    def test_large_materialized_projection_uses_index_without_canonical_scan(self):
        row_count = 10000
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "large-search.evidence.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                ensure_evidence_schema(connection)
                asset_id = "a" * 24
                revision_id = "b" * 24
                graph_ref = f"bp://{asset_id}@{revision_id}/g/1"
                connection.execute(
                    "INSERT INTO asset_revisions(revision_id, asset_id, asset_name, object_path, "
                    "source_fingerprint, parser_version, schema_version, generated_at, uasset_path) "
                    "VALUES (?, ?, 'LargeFixture', '/Game/Test/LargeFixture.LargeFixture', 'fixture', "
                    "'fixture-v3', 'ark.blueprint.evidence.v2', '2026-01-01T00:00:00Z', '')",
                    (revision_id, asset_id),
                )
                connection.execute(
                    "INSERT INTO graphs(graph_ref, revision_id, export_index, name) VALUES (?, ?, 1, 'LargeGraph')",
                    (graph_ref, revision_id),
                )
                node_rows = []
                search_rows = []
                for index in range(row_count):
                    name = "NeedleNode" if index == row_count - 1 else f"SyntheticNode_{index:05d}"
                    node_ref = f"{graph_ref}/n/{index}"
                    node_rows.append((node_ref, graph_ref, index, f"fixture:{index}", name, "K2Node_CallFunction"))
                    search_rows.append(
                        (
                            node_ref,
                            revision_id,
                            "node",
                            name,
                            graph_ref,
                            "K2Node_CallFunction",
                            f"{name} K2Node_CallFunction",
                        )
                    )
                connection.executemany(
                    "INSERT INTO nodes(node_ref, graph_ref, local_index, node_identity, name, class_name) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    node_rows,
                )
                connection.executemany(
                    "INSERT INTO search_entities(ref, revision_id, kind, name, graph_ref, summary, search_text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    search_rows,
                )
                connection.execute(
                    "INSERT INTO search_materialization(revision_id, kind, row_count, is_complete) "
                    "VALUES (?, 'node', ?, 1)",
                    (revision_id, row_count),
                )
                connection.commit()
            finally:
                connection.close()

            service = EvidenceQueryService.open(database_path)
            try:
                with mock.patch.object(
                    service,
                    "_fallback_search_rows",
                    wraps=service._fallback_search_rows,
                ) as fallback:
                    started = time.perf_counter()
                    result = service.query(
                        {
                            "operation": "search",
                            "query": "NeedleNode",
                            "kinds": ["node"],
                            "pageSize": 10,
                            "budgetTokens": 1000,
                        }
                    )
                    elapsed = time.perf_counter() - started
            finally:
                service.close()

        self.assertEqual(fallback.call_count, 0)
        self.assertEqual([item["name"] for item in result["items"]], ["NeedleNode"])
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
