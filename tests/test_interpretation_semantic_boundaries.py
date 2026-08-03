from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    large_interpretation_payload,
    publish_interpretation_fixture,
)

from blueprint_translator.interpretation import engine as engine_module  # noqa: E402
from blueprint_translator.interpretation.engine import (  # noqa: E402
    build_interpretation,
)
from blueprint_translator.interpretation.source import (  # noqa: E402
    load_interpretation_source,
)
from blueprint_translator.interpretation_publication import (  # noqa: E402
    load_current_interpretation,
    publish_interpretation,
)


def _find_node(payload: dict[str, object], name: str) -> dict[str, object]:
    return next(
        node
        for graph in payload["graphs"]
        for node in graph["payload"]["nodes"]
        if node["name"] == name
    )


class InterpretationSemanticBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def _publish(
        self,
        name: str,
        payload: dict[str, object],
    ) -> Path:
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.root,
            name=name,
            payload=payload,
        )
        return asset_dir

    def test_explicit_graph_and_node_provenance_cannot_confirm_statements_or_summary(
        self,
    ) -> None:
        payload = interpretation_payload("ProvenanceBoundaryFixture")
        event_graph = next(
            graph for graph in payload["graphs"] if graph["graph"] == "EventGraph"
        )
        event_graph["status"] = "heuristic"
        event_graph["confidence"] = "heuristic"
        branch = _find_node(payload, "Branch")
        branch["confidence"] = "ambiguous"
        sequence = _find_node(payload, "Sequence")
        sequence["status"] = "not_recovered"
        asset_dir = self._publish("ProvenanceBoundaryFixture", payload)

        built = build_interpretation(asset_dir, budget=32_000)
        graph_ref = next(
            row["graphRef"]
            for row in built.interpretation["assetSummary"]["graphInventory"]
            if row["name"] == "EventGraph"
        )
        statements = [
            row
            for row in built.interpretation["statements"]
            if row["kind"] != "GAP" and row["graphRef"] == graph_ref
        ]

        self.assertTrue(statements)
        self.assertTrue(all(row["evidenceRefs"] for row in statements))
        self.assertTrue(all(row["status"] != "CONFIRMED" for row in statements))
        self.assertEqual(
            next(row["status"] for row in statements if row["kind"] == "BRANCH"),
            "AMBIGUOUS",
        )
        self.assertEqual(
            next(row["status"] for row in statements if row["text"] == "Sequence execution"),
            "NOT_RECOVERED",
        )
        summary = built.interpretation["assetSummary"]
        unconfirmed_node_refs = {row["nodeRef"] for row in statements}
        for key in ("entries", "variableReads", "variableWrites", "delegateBindings"):
            self.assertTrue(
                unconfirmed_node_refs.isdisjoint(
                    {row["nodeRef"] for row in summary[key]}
                )
            )
        self.assertEqual(summary["confirmedLocalCalls"], [])

    def test_non_ready_capture_provenance_is_not_recovered(self) -> None:
        payload = interpretation_payload("NonReadyCaptureFixture")
        event_graph = next(
            graph for graph in payload["graphs"] if graph["graph"] == "EventGraph"
        )
        event_graph["status"] = "needs_clipboard"
        event_graph["confidence"] = "high"
        branch = _find_node(payload, "Branch")
        branch["status"] = "need_manual_clipboard"
        branch["confidence"] = "high"
        asset_dir = self._publish("NonReadyCaptureFixture", payload)

        interpreted = build_interpretation(asset_dir, budget=32_000).interpretation
        event_graph_ref = next(
            row["graphRef"]
            for row in interpreted["assetSummary"]["graphInventory"]
            if row["name"] == "EventGraph"
        )
        statements = [
            row
            for row in interpreted["statements"]
            if row["kind"] != "GAP" and row["graphRef"] == event_graph_ref
        ]

        self.assertTrue(statements)
        self.assertTrue(all(row["status"] == "NOT_RECOVERED" for row in statements))
        unconfirmed_node_refs = {row["nodeRef"] for row in statements}
        for key in ("entries", "variableReads", "variableWrites", "delegateBindings"):
            self.assertTrue(
                unconfirmed_node_refs.isdisjoint(
                    {row["nodeRef"] for row in interpreted["assetSummary"][key]}
                )
            )

    def test_provenance_markers_do_not_match_incidental_low_substrings(self) -> None:
        for index, provenance_source in enumerate(
            ("workflow_export", "data_flow_reader", "allowlisted_parser"),
            start=1,
        ):
            with self.subTest(provenance_source=provenance_source):
                name = f"ProvenanceTokenFixture{index}"
                payload = interpretation_payload(name)
                branch = _find_node(payload, "Branch")
                branch["source"] = provenance_source
                branch["confidence"] = "high"
                function_reference = _find_node(payload, "LocalHelperCall")[
                    "properties"
                ][0]
                function_reference["source"] = provenance_source
                function_reference["confidence"] = "high"
                asset_dir = self._publish(name, payload)

                source = load_interpretation_source(asset_dir)
                local_reference = next(
                    row for row in source.references if row["name"] == "LocalHelper"
                )
                self.assertTrue(
                    str(local_reference["target_ref"]).startswith("bp://")
                )
                interpreted = build_interpretation(
                    asset_dir,
                    budget=32_000,
                ).interpretation
                branch_statement = next(
                    row for row in interpreted["statements"] if row["kind"] == "BRANCH"
                )
                self.assertEqual(branch_statement["status"], "CONFIRMED")
                self.assertTrue(interpreted["assetSummary"]["confirmedLocalCalls"])

    def test_unconfirmed_class_defaults_are_gapped_and_not_summarized(self) -> None:
        payload = interpretation_payload("DefaultProvenanceFixture")
        payload["class_defaults"]["variables"].update(
            {
                "HeuristicDefault": {
                    "value": 7,
                    "type": "IntProperty",
                    "confidence": "heuristic",
                    "source": "pattern_scan",
                },
                "AmbiguousDefault": {
                    "value": 8,
                    "type": "IntProperty",
                    "confidence": "high",
                    "provenance": "ambiguous_candidates",
                },
                "MissingDefault": {
                    "value": [],
                    "type": "ArrayProperty",
                    "confidence": "high",
                    "status": "not_recovered",
                },
                "UnprovenDefault": {
                    "value": 9,
                    "type": "IntProperty",
                },
            }
        )
        asset_dir = self._publish("DefaultProvenanceFixture", payload)

        source = load_interpretation_source(asset_dir)
        unconfirmed_defaults = {
            str(row["default_ref"]): str(row["name"])
            for row in source.defaults
            if row["name"] != "DefaultThreshold"
        }
        built = build_interpretation(asset_dir, budget=32_000)
        interpreted = built.interpretation
        summary_names = {
            row["name"] for row in interpreted["assetSummary"]["classDefaults"]
        }
        data_flow_names = {
            row["name"] for row in interpreted["dataFlow"]["classDefaultRefs"]
        }

        self.assertEqual(summary_names, {"DefaultThreshold"})
        self.assertEqual(data_flow_names, {"DefaultThreshold"})
        matching_gaps = [
            gap
            for gap in built.gaps["items"]
            if gap["code"] == "DEFAULT_NOT_RECOVERED"
            and set(gap["evidenceRefs"]).intersection(unconfirmed_defaults)
        ]
        self.assertEqual(len(matching_gaps), len(unconfirmed_defaults))
        self.assertEqual(
            {
                ref
                for gap in matching_gaps
                for ref in gap["evidenceRefs"]
                if ref in unconfirmed_defaults
            },
            set(unconfirmed_defaults),
        )
        self.assertTrue(
            any(gap["status"] == "AMBIGUOUS" for gap in matching_gaps)
        )
        gap_ids = {gap["id"] for gap in matching_gaps}
        self.assertEqual(
            {
                statement["gapRefs"][0]
                for statement in interpreted["statements"]
                if statement["kind"] == "GAP"
                and statement["gapRefs"]
                and statement["gapRefs"][0] in gap_ids
            },
            gap_ids,
        )
        published = publish_interpretation(asset_dir, budget=32_000)
        loaded = load_current_interpretation(asset_dir)
        self.assertEqual(loaded.revision_id, published.revision_id)
        self.assertTrue(
            all(
                gap["graphRef"] == ""
                for gap in loaded.gaps["items"]
                if gap["code"] == "DEFAULT_NOT_RECOVERED"
                and gap["source"] == "CLASS_DEFAULT_PROVENANCE"
            )
        )

    def test_writer_requires_same_owner_and_confirmed_reference_provenance(
        self,
    ) -> None:
        scenarios = (
            (
                "ForeignOwnerFixture",
                "/Game/Other/Foreign.Foreign",
                "high",
                "",
                "",
            ),
            (
                "HeuristicReferenceFixture",
                "/Game/Test/HeuristicReferenceFixture.HeuristicReferenceFixture",
                "heuristic",
                "",
                "",
            ),
            (
                "NonConfirmedReferenceFixture",
                "/Game/Test/NonConfirmedReferenceFixture.NonConfirmedReferenceFixture",
                "medium",
                "",
                "",
            ),
            (
                "HeuristicSourceReferenceFixture",
                "/Game/Test/HeuristicSourceReferenceFixture.HeuristicSourceReferenceFixture",
                "high",
                "heuristic_parser",
                "",
            ),
            (
                "NeedsClipboardReferenceFixture",
                "/Game/Test/NeedsClipboardReferenceFixture.NeedsClipboardReferenceFixture",
                "high",
                "",
                "needs_clipboard",
            ),
            (
                "NeedManualClipboardReferenceFixture",
                "/Game/Test/NeedManualClipboardReferenceFixture.NeedManualClipboardReferenceFixture",
                "high",
                "",
                "need_manual_clipboard",
            ),
        )
        for name, owner, confidence, provenance_source, status in scenarios:
            with self.subTest(name=name):
                payload = interpretation_payload(name)
                function_reference = _find_node(payload, "LocalHelperCall")[
                    "properties"
                ][0]
                function_reference["member_parent_object_path"] = owner
                function_reference["confidence"] = confidence
                if provenance_source:
                    function_reference["source"] = provenance_source
                if status:
                    function_reference["status"] = status
                asset_dir = self._publish(name, payload)

                source = load_interpretation_source(asset_dir)
                local_reference = next(
                    row for row in source.references if row["name"] == "LocalHelper"
                )
                self.assertFalse(str(local_reference["target_ref"]).startswith("bp://"))
                built = build_interpretation(asset_dir, budget=32_000)
                self.assertEqual(
                    built.interpretation["assetSummary"]["confirmedLocalCalls"],
                    [],
                )
                self.assertTrue(
                    any(
                        gap["code"] == "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE"
                        and local_reference["reference_ref"] in gap["evidenceRefs"]
                        for gap in built.gaps["items"]
                    )
                )

    def test_interpreter_rejects_heuristic_reference_and_target_graph(self) -> None:
        payload = interpretation_payload("InterpreterDefenseFixture")
        asset_dir = self._publish("InterpreterDefenseFixture", payload)
        source = load_interpretation_source(asset_dir)
        local_reference = next(
            row for row in source.references if row["name"] == "LocalHelper"
        )
        target_ref = str(local_reference["target_ref"])

        for changed_source in (
            replace(
                source,
                references=tuple(
                    {**row, "confidence": "HEURISTIC"}
                    if row["reference_ref"] == local_reference["reference_ref"]
                    else row
                    for row in source.references
                ),
            ),
            replace(
                source,
                graphs=tuple(
                    {**row, "status": "heuristic", "confidence": "heuristic"}
                    if row["graph_ref"] == target_ref
                    else row
                    for row in source.graphs
                ),
            ),
        ):
            with self.subTest(boundary=changed_source):
                built = engine_module._build_from_source(changed_source, budget=32_000)
                self.assertEqual(
                    built.interpretation["assetSummary"]["confirmedLocalCalls"],
                    [],
                )
                self.assertTrue(
                    any(
                        gap["code"] == "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE"
                        and local_reference["reference_ref"] in gap["evidenceRefs"]
                        for gap in built.gaps["items"]
                    )
                )

    def test_delegate_binding_and_invocation_use_exact_node_types(self) -> None:
        invocation_payload = interpretation_payload("DelegateInvocationFixture")
        invocation_dir = self._publish(
            "DelegateInvocationFixture",
            invocation_payload,
        )
        invoked = build_interpretation(invocation_dir, budget=32_000).interpretation
        invocation = next(
            row for row in invoked["statements"] if row["text"].startswith("Invoke delegate")
        )
        self.assertNotIn(
            invocation["nodeRef"],
            {row["nodeRef"] for row in invoked["assetSummary"]["delegateBindings"]},
        )

        for node_type in (
            "K2Node_AddDelegate",
            "K2Node_AssignDelegate",
            "K2Node_CreateDelegate",
        ):
            name = node_type.removeprefix("K2Node_") + "Fixture"
            payload = interpretation_payload(name)
            delegate = _find_node(payload, "Delegate")
            delegate["node_type"] = node_type
            delegate["class_name"] = node_type
            asset_dir = self._publish(name, payload)
            interpreted = build_interpretation(asset_dir, budget=32_000).interpretation
            binding = next(
                row
                for row in interpreted["statements"]
                if row["text"].startswith("Bind delegate")
            )
            self.assertIn(
                binding["nodeRef"],
                {
                    row["nodeRef"]
                    for row in interpreted["assetSummary"]["delegateBindings"]
                },
            )

    def test_edge_count_only_counts_edges_emitted_by_control_or_data_flow(self) -> None:
        payload = interpretation_payload("EmittedEdgeCountFixture")
        asset_dir = self._publish("EmittedEdgeCountFixture", payload)
        source = load_interpretation_source(asset_dir)
        graph_by_node = {
            str(node["node_ref"]): str(node["graph_ref"]) for node in source.nodes
        }
        output_pins_by_graph: dict[str, list[dict[str, object]]] = {}
        for pin in source.pins:
            if (
                str(pin.get("direction") or "").casefold() != "egpd_output"
                or str(pin.get("category") or "").casefold() == "exec"
            ):
                continue
            graph_ref = graph_by_node[str(pin["node_ref"])]
            output_pins_by_graph.setdefault(graph_ref, []).append(pin)
        graph_ref, output_pins = next(
            (candidate_graph_ref, pins)
            for candidate_graph_ref, pins in output_pins_by_graph.items()
            if len(pins) >= 2
        )
        fake_edge_ref = f"{graph_ref}/edge/same-direction-boundary"
        fake_edge = {
            "edge_ref": fake_edge_ref,
            "graph_ref": graph_ref,
            "source_pin_ref": str(output_pins[0]["pin_ref"]),
            "target_pin_ref": str(output_pins[1]["pin_ref"]),
            "kind": "data",
            "confidence": "high",
            "resolution_status": "resolved_pin",
        }
        changed = replace(
            source,
            edges=(*source.edges, fake_edge),
            evidence_refs=frozenset({*source.evidence_refs, fake_edge_ref}),
        )

        interpreted = engine_module._build_from_source(
            changed,
            budget=32_000,
        ).interpretation
        emitted_edge_refs = {
            successor["edgeRef"]
            for graph in interpreted["controlFlow"]["graphs"]
            for node in graph["nodes"]
            for successor in node["successors"]
        } | {
            edge["edgeRef"]
            for graph in interpreted["dataFlow"]["graphs"]
            for edge in graph["edges"]
        }

        self.assertNotIn(fake_edge_ref, emitted_edge_refs)
        self.assertEqual(
            interpreted["assetSummary"]["edgeCount"],
            len(emitted_edge_refs),
        )

    def test_large_count_estimate_rejects_before_graph_algorithms(self) -> None:
        payload = large_interpretation_payload(
            "EarlyBudgetBoundaryFixture",
            node_count=100,
        )
        asset_dir = self._publish("EarlyBudgetBoundaryFixture", payload)
        source = load_interpretation_source(asset_dir)

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
            engine_module._build_from_source(source, budget=32_000)


if __name__ == "__main__":
    unittest.main()
