from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.evidence_repository import (  # noqa: E402
    open_bound_evidence_database,
    resolve_asset_evidence_state,
)
from blueprint_translator.interpretation_publication import (  # noqa: E402
    INTERPRETATION_SCHEMA,
    build_interpretation,
    load_current_interpretation,
    publish_interpretation,
)
from blueprint_translator.interpretation.graph_algorithms import (  # noqa: E402
    strongly_connected_components,
)
from blueprint_translator.interpretation import engine as engine_module  # noqa: E402
from blueprint_translator.interpretation.source import (  # noqa: E402
    load_interpretation_source,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    large_interpretation_payload,
    publish_interpretation_fixture,
)


class InterpretationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.asset_dir, _source, _payload = publish_interpretation_fixture(
            Path(self._temporary.name)
        )

    def test_interpretation_is_deterministic_traceable_and_gap_complete(self) -> None:
        first = build_interpretation(self.asset_dir, budget=32_000)
        second = build_interpretation(self.asset_dir, budget=32_000)

        self.assertEqual(first.semantic_digest, second.semantic_digest)
        self.assertEqual(first.interpretation, second.interpretation)
        self.assertEqual(first.markdown, second.markdown)
        self.assertEqual(first.pseudocode, second.pseudocode)
        interpretation = first.interpretation
        self.assertEqual(interpretation["schema"], INTERPRETATION_SCHEMA)
        self.assertEqual(
            interpretation["schemaVersion"],
            "blueprint-to-code.blueprint-interpretation/v1",
        )

        confirmed = [
            row
            for row in interpretation["statements"]
            if row["status"] == "CONFIRMED"
        ]
        self.assertTrue(confirmed)
        self.assertTrue(all(row["evidenceRefs"] for row in confirmed))
        state = resolve_asset_evidence_state(self.asset_dir)
        with open_bound_evidence_database(state) as connection:
            existing_refs = {
                str(row[0])
                for table, column in (
                    ("graphs", "graph_ref"),
                    ("nodes", "node_ref"),
                    ("pins", "pin_ref"),
                    ("edges", "edge_ref"),
                    ("edge_observations", "observation_ref"),
                    ("class_defaults", "default_ref"),
                    ("diagnostics", "diagnostic_ref"),
                    ('"references"', "reference_ref"),
                )
                for row in connection.execute(f"SELECT {column} FROM {table}")
            }
            unresolved_observations = {
                str(row[0])
                for row in connection.execute(
                    "SELECT observation_ref FROM edge_observations "
                    "WHERE COALESCE(NULLIF(resolution_status, ''), status, '') "
                    "!= 'resolved_pin' OR target_pin_ref IS NULL"
                )
            }
            duplicate_names = int(
                connection.execute(
                    "SELECT COUNT(*) FROM nodes WHERE name = 'DuplicateReroute'"
                ).fetchone()[0]
            )
            duplicate_pin_ids = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pins WHERE native_pin_id = 'P_DUPLICATE_PIN'"
                ).fetchone()[0]
            )
            heuristic_edge_targets = {
                str(row[0])
                for row in connection.execute(
                    "SELECT target_pin_ref FROM edges "
                    "WHERE resolution_status = 'resolved_pin_heuristic'"
                )
            }
            exact_edge_refs = {
                str(row[0])
                for row in connection.execute(
                    "SELECT edge_ref FROM edges WHERE resolution_status = 'resolved_pin'"
                )
            }
            all_edge_count = int(
                connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            )
        self.assertEqual(
            [
                ref
                for statement in confirmed
                for ref in statement["evidenceRefs"]
                if ref not in existing_refs
            ],
            [],
        )

        edge_gaps = [
            gap
            for gap in first.gaps["items"]
            if gap.get("source") == "EDGE_OBSERVATION"
        ]
        self.assertEqual(len(edge_gaps), len(unresolved_observations))
        self.assertGreater(len(unresolved_observations), 0)
        for observation_ref in unresolved_observations:
            self.assertEqual(
                sum(
                    observation_ref in gap["evidenceRefs"]
                    for gap in edge_gaps
                ),
                1,
            )
        self.assertEqual(duplicate_names, 2)
        self.assertEqual(duplicate_pin_ids, 2)
        self.assertTrue(heuristic_edge_targets)
        self.assertLess(len(exact_edge_refs), all_edge_count)
        self.assertEqual(
            interpretation["assetSummary"]["edgeCount"],
            len(exact_edge_refs),
        )
        self.assertTrue(
            any(
                gap["code"] == "AMBIGUOUS_DATA_EDGE"
                and len(gap["evidenceRefs"]) >= 2
                for gap in first.gaps["items"]
            )
        )

        branch_graphs = [
            graph
            for graph in interpretation["controlFlow"]["graphs"]
            if graph["name"] == "EventGraph"
        ]
        self.assertEqual(len(branch_graphs), 1)
        branch_nodes = [
            node
            for node in branch_graphs[0]["nodes"]
            if node["kind"] == "BRANCH"
        ]
        self.assertEqual(len(branch_nodes), 1)
        self.assertEqual(
            {successor["sourcePinName"] for successor in branch_nodes[0]["successors"]},
            {"then", "else"},
        )
        self.assertTrue(
            all(successor["sourcePinRef"].startswith("bp://") for successor in branch_nodes[0]["successors"])
        )
        self.assertTrue(
            all(
                successor["edgeRef"] in exact_edge_refs
                for successor in branch_nodes[0]["successors"]
            )
        )
        self.assertTrue(
            any(graph["cycles"] for graph in interpretation["controlFlow"]["graphs"])
        )
        confirmed_ir_target_pins = {
            successor["targetPinRef"]
            for graph in interpretation["controlFlow"]["graphs"]
            for node in graph["nodes"]
            for successor in node["successors"]
        } | {
            edge["targetPinRef"]
            for graph in interpretation["dataFlow"]["graphs"]
            for edge in graph["edges"]
        }
        self.assertTrue(heuristic_edge_targets.isdisjoint(confirmed_ir_target_pins))
        self.assertTrue(
            all(
                edge["edgeRef"] in exact_edge_refs
                for graph in interpretation["dataFlow"]["graphs"]
                for edge in graph["edges"]
            )
        )
        statements_by_node = {
            statement["nodeRef"]: statement
            for statement in interpretation["statements"]
            if statement["kind"] != "GAP"
        }
        for graph in interpretation["controlFlow"]["graphs"]:
            for node in graph["nodes"]:
                successor_edge_refs = {
                    successor["edgeRef"] for successor in node["successors"]
                }
                if successor_edge_refs:
                    self.assertTrue(
                        successor_edge_refs.issubset(
                            statements_by_node[node["nodeRef"]]["evidenceRefs"]
                        )
                    )

        missing_entry = next(
            graph
            for graph in interpretation["controlFlow"]["graphs"]
            if graph["name"] == "MissingEntryGraph"
        )
        self.assertEqual(missing_entry["entryNodeRefs"], [])
        self.assertTrue(missing_entry["rootCandidateNodeRefs"])
        self.assertTrue(all(not node["successors"] for node in missing_entry["nodes"]))
        self.assertTrue(
            any(
                gap["code"] == "NO_ENTRY_POINT"
                and gap["graphRef"] == missing_entry["graphRef"]
                for gap in first.gaps["items"]
            )
        )

        branch_statement = next(
            statement
            for statement in interpretation["statements"]
            if statement["kind"] == "BRANCH"
        )
        branch_graph = next(
            graph
            for graph in interpretation["controlFlow"]["graphs"]
            if graph["graphRef"] == branch_statement["graphRef"]
        )
        labels = {
            node_ref: block["label"]
            for block in branch_graph["basicBlocks"]
            for node_ref in block["nodeRefs"]
        }
        branch_node = next(
            node
            for node in branch_graph["nodes"]
            if node["nodeRef"] == branch_statement["nodeRef"]
        )
        successors = {
            successor["sourcePinName"].casefold(): labels[successor["targetNodeRef"]]
            for successor in branch_node["successors"]
        }
        branch_edge_refs = {
            successor["edgeRef"] for successor in branch_node["successors"]
        }
        self.assertTrue(branch_edge_refs.issubset(branch_statement["evidenceRefs"]))
        branch_line = next(
            line
            for line in first.pseudocode.splitlines()
            if branch_statement["id"] in line
        )
        self.assertIn(
            f"? GOTO {successors['then']} : GOTO {successors['else']}",
            branch_line,
        )

        shared = interpretation["dataFlow"]["sharedExpressions"]
        self.assertTrue(shared)
        self.assertTrue(all(row["sourcePinRef"].startswith("bp://") for row in shared))
        impure_result_ref = next(
            pin["pinRef"]
            for graph in interpretation["dataFlow"]["graphs"]
            for pin in graph["pins"]
            if pin["name"] == "ImpureResult"
        )
        self.assertNotIn(
            impure_result_ref,
            {row["sourcePinRef"] for row in shared},
        )
        self.assertTrue(interpretation["dataFlow"]["classDefaultRefs"])
        self.assertIn("componentRefs", interpretation["dataFlow"])
        set_flag_input = next(
            pin
            for graph in interpretation["dataFlow"]["graphs"]
            for pin in graph["pins"]
            if pin["name"] == "bWasRun"
        )
        self.assertEqual(set_flag_input["direction"], "EGPD_Input")
        self.assertEqual(set_flag_input["exactType"]["category"], "bool")
        self.assertEqual(set_flag_input["default"]["literal"], "false")
        self.assertTrue(set_flag_input["default"]["recovered"])
        typed_object_input = next(
            pin
            for graph in interpretation["dataFlow"]["graphs"]
            for pin in graph["pins"]
            if pin["name"] == "TypedObjectWithoutDefault"
        )
        self.assertEqual(
            typed_object_input["exactType"]["serialized"]["PinSubCategoryObject"],
            "/Script/Engine.Actor",
        )
        self.assertFalse(typed_object_input["default"]["recovered"])
        self.assertEqual(typed_object_input["default"]["classRef"], "")
        object_default_input = next(
            pin
            for graph in interpretation["dataFlow"]["graphs"]
            for pin in graph["pins"]
            if pin["name"] == "ObjectWithDefault"
        )
        self.assertTrue(object_default_input["default"]["recovered"])
        self.assertEqual(
            object_default_input["default"]["objectRef"],
            "/Game/Test/DefaultObject.DefaultObject",
        )
        self.assertEqual(object_default_input["default"]["classRef"], "")
        self.assertFalse(
            any(
                gap["pinRef"] == object_default_input["pinRef"]
                and gap["code"]
                in {"SOURCE_PIN_NOT_RECOVERED", "DEFAULT_NOT_RECOVERED"}
                for gap in first.gaps["items"]
            )
        )

        unknown_call = next(
            node
            for graph in interpretation["controlFlow"]["graphs"]
            for node in graph["nodes"]
            if node["label"] == "UnknownCallWithoutName"
        )
        self.assertTrue(
            any(
                gap["code"] == "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE"
                and gap["nodeRef"] == unknown_call["nodeRef"]
                for gap in first.gaps["items"]
            )
        )

        cycle_gaps = [
            gap
            for gap in first.gaps["items"]
            if gap["code"] in {"UNSTRUCTURED_CYCLE", "DATA_CYCLE"}
        ]
        self.assertTrue(cycle_gaps)
        self.assertTrue(
            all(exact_edge_refs.intersection(gap["evidenceRefs"]) for gap in cycle_gaps)
        )

        local_calls = interpretation["assetSummary"]["confirmedLocalCalls"]
        self.assertEqual([row["name"] for row in local_calls], ["LocalHelper"])
        self.assertTrue(local_calls[0]["targetRef"].startswith("bp://"))

        hints = interpretation["heuristicReviewHints"]
        self.assertTrue(hints)
        self.assertTrue(
            all(
                hint["basis"] == "KEYWORD_AND_NAME_HEURISTIC"
                and hint["confidence"] == "HEURISTIC"
                and hint["notEvidence"] is True
                for hint in hints
            )
        )
        confirmed_text = "\n".join(row["text"] for row in confirmed)
        self.assertNotIn("Glide", confirmed_text)
        self.assertIn("准备🔥", confirmed_text)
        self.assertNotIn("<script>", first.markdown)
        self.assertIn("&lt;script&gt;", first.markdown)

        trace_rows = first.trace["pseudocodeLines"]
        executable = [row for row in trace_rows if row["executable"]]
        statement_ids = {row["id"] for row in interpretation["statements"]}
        self.assertTrue(executable)
        self.assertTrue(all(row["statementId"] in statement_ids for row in executable))
        self.assertEqual(
            sorted(row["statementId"] for row in executable),
            sorted(statement_ids),
        )
        pseudocode_bytes = first.pseudocode.encode("utf-8")
        for row in trace_rows:
            self.assertGreaterEqual(row["startByte"], 0)
            self.assertGreaterEqual(row["endByte"], row["startByte"])
            self.assertLessEqual(row["endByte"], len(pseudocode_bytes))
        self.assertTrue(
            first.pseudocode.startswith(
                "EVIDENCE-DERIVED PSEUDOCODE — NOT ORIGINAL C++ — NOT GUARANTEED COMPILABLE"
            )
        )

    def test_publication_round_trip_reuses_the_same_revision(self) -> None:
        first = publish_interpretation(self.asset_dir, budget=32_000)
        second = publish_interpretation(self.asset_dir, budget=32_000)
        loaded = load_current_interpretation(self.asset_dir)

        self.assertEqual(first.revision_id, second.revision_id)
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertTrue(first.created)
        self.assertTrue(second.reused)
        self.assertEqual(loaded.revision_id, first.revision_id)
        self.assertEqual(
            loaded.interpretation["semanticDigest"],
            first.semantic_digest,
        )
        pointer = json.loads(
            (self.asset_dir / "interpretation" / "current.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pointer["revisionId"], first.revision_id)
        self.assertEqual(pointer["manifestSha256"], first.manifest_sha256)

    def test_owner_and_unique_name_only_never_confirm_a_local_call(self) -> None:
        payload = interpretation_payload("NameOnlyLocalCallFixture")
        local_call = next(
            node
            for graph in payload["graphs"]
            for node in graph["payload"]["nodes"]
            if node["name"] == "LocalHelperCall"
        )
        local_call["properties"][0].pop("member_graph_export_index")
        asset_dir, _source, _payload = publish_interpretation_fixture(
            Path(self._temporary.name),
            name="NameOnlyLocalCallFixture",
            payload=payload,
        )

        built = build_interpretation(asset_dir, budget=32_000)

        self.assertEqual(
            built.interpretation["assetSummary"]["confirmedLocalCalls"],
            [],
        )
        local_call_ref = next(
            statement["nodeRef"]
            for statement in built.interpretation["statements"]
            if statement["text"] == "Call LocalHelper"
        )
        self.assertTrue(
            any(
                gap["code"] == "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE"
                and gap["nodeRef"] == local_call_ref
                for gap in built.gaps["items"]
            )
        )

    def test_boolean_graph_export_index_is_not_an_exact_local_binding(self) -> None:
        payload = interpretation_payload("BooleanLocalCallFixture")
        local_graph = next(
            graph for graph in payload["graphs"] if graph["graph"] == "LocalHelper"
        )
        local_graph["export_index"] = 1
        local_graph["payload"]["metadata"]["uasset_export_index"] = 1
        local_call = next(
            node
            for graph in payload["graphs"]
            for node in graph["payload"]["nodes"]
            if node["name"] == "LocalHelperCall"
        )
        local_call["properties"][0]["member_graph_export_index"] = True
        asset_dir, _source, _payload = publish_interpretation_fixture(
            Path(self._temporary.name),
            name="BooleanLocalCallFixture",
            payload=payload,
        )

        built = build_interpretation(asset_dir, budget=32_000)

        self.assertEqual(
            built.interpretation["assetSummary"]["confirmedLocalCalls"],
            [],
        )

    def test_budget_is_deterministic_and_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "INTERPRETATION_BUDGET_EXCEEDED"):
            build_interpretation(self.asset_dir, budget=200)

    def test_row_budget_fails_before_control_or_data_flow_construction(self) -> None:
        source = load_interpretation_source(self.asset_dir)
        with (
            patch.object(
                engine_module,
                "build_control_flow",
                side_effect=AssertionError("control flow must not run"),
            ),
            patch.object(
                engine_module,
                "build_data_flow",
                side_effect=AssertionError("data flow must not run"),
            ),
            self.assertRaisesRegex(ValueError, "INTERPRETATION_BUDGET_EXCEEDED"),
        ):
            engine_module._build_from_source(source, budget=1)

    def test_confirmed_diagnostic_status_cannot_create_a_confirmed_gap(self) -> None:
        source = load_interpretation_source(self.asset_dir)
        diagnostic = next(
            row
            for row in source.diagnostics
            if row.get("reason_code") == "external_callable_body_not_in_asset"
        )
        changed = replace(
            source,
            diagnostics=tuple(
                {**row, "status": "CONFIRMED"}
                if row["diagnostic_ref"] == diagnostic["diagnostic_ref"]
                else row
                for row in source.diagnostics
            ),
        )
        built = engine_module._build_from_source(changed, budget=32_000)
        matching = [
            gap
            for gap in built.gaps["items"]
            if diagnostic["diagnostic_ref"] in gap["evidenceRefs"]
        ]
        self.assertTrue(matching)
        self.assertTrue(
            all(
                gap["status"]
                in {"SOURCE_NOT_AVAILABLE", "NOT_RECOVERED", "AMBIGUOUS"}
                for gap in matching
            )
        )

    def test_large_graph_respects_the_same_deterministic_budget_gate(self) -> None:
        large_asset, _source, _payload = publish_interpretation_fixture(
            Path(self._temporary.name),
            name="LargeInterpretationFixture",
            payload=large_interpretation_payload(),
        )
        first = build_interpretation(large_asset, budget=100_000)
        second = build_interpretation(large_asset, budget=100_000)
        self.assertEqual(first.semantic_digest, second.semantic_digest)
        with self.assertRaisesRegex(ValueError, "INTERPRETATION_BUDGET_EXCEEDED"):
            build_interpretation(large_asset, budget=32_000)

    def test_deep_control_graph_scc_is_iterative(self) -> None:
        nodes = [f"bp://fixture/deep/{index:04d}" for index in range(5_000)]
        successors = {
            node: [nodes[index + 1]] if index + 1 < len(nodes) else []
            for index, node in enumerate(nodes)
        }
        components = strongly_connected_components(nodes, successors)
        self.assertEqual(len(components), len(nodes))
        self.assertTrue(all(len(component) == 1 for component in components))


if __name__ == "__main__":
    unittest.main()
