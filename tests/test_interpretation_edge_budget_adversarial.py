from __future__ import annotations

import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.interpretation.control_flow import (  # noqa: E402
    build_control_flow,
)
from blueprint_translator.interpretation.data_flow import (  # noqa: E402
    build_data_flow,
)
from blueprint_translator.interpretation.source import (  # noqa: E402
    InterpretationSource,
    load_interpretation_source,
)
from interpretation_fixture import publish_interpretation_fixture  # noqa: E402


def _matching_observation_refs(
    source: InterpretationSource,
    edge: dict[str, object],
) -> set[str]:
    edge_pins = {
        str(edge["source_pin_ref"]),
        str(edge["target_pin_ref"]),
    }
    return {
        str(observation["observation_ref"])
        for observation in source.observations
        if str(observation["graph_ref"]) == str(edge["graph_ref"])
        and {
            str(observation.get("source_pin_ref") or ""),
            str(observation.get("target_pin_ref") or ""),
        }
        == edge_pins
    }


def _with_edge_directions(
    source: InterpretationSource,
    edge: dict[str, object],
    *,
    source_direction: str,
    target_direction: str,
) -> InterpretationSource:
    source_pin_ref = str(edge["source_pin_ref"])
    target_pin_ref = str(edge["target_pin_ref"])
    return replace(
        source,
        pins=tuple(
            {
                **pin,
                "direction": (
                    source_direction
                    if str(pin["pin_ref"]) == source_pin_ref
                    else target_direction
                ),
            }
            if str(pin["pin_ref"]) in {source_pin_ref, target_pin_ref}
            else pin
            for pin in source.pins
        ),
    )


def _with_edge_categories(
    source: InterpretationSource,
    edge: dict[str, object],
    *,
    source_category: str,
    target_category: str,
) -> InterpretationSource:
    source_pin_ref = str(edge["source_pin_ref"])
    target_pin_ref = str(edge["target_pin_ref"])
    replacements = {
        source_pin_ref: source_category,
        target_pin_ref: target_category,
    }
    return replace(
        source,
        pins=tuple(
            {
                **pin,
                "category": replacements[str(pin["pin_ref"])],
                "pin_type": {
                    **(
                        pin.get("pin_type")
                        if isinstance(pin.get("pin_type"), dict)
                        else {}
                    ),
                    "PinCategory": replacements[str(pin["pin_ref"])],
                },
            }
            if str(pin["pin_ref"]) in replacements
            else pin
            for pin in source.pins
        ),
    )


def _with_edge_kind(
    source: InterpretationSource,
    edge: dict[str, object],
    *,
    kind: str,
) -> InterpretationSource:
    return replace(
        source,
        edges=tuple(
            {**row, "kind": kind}
            if str(row["edge_ref"]) == str(edge["edge_ref"])
            else row
            for row in source.edges
        ),
    )


def _assert_invalid_edge_is_only_a_gap(
    testcase: unittest.TestCase,
    *,
    source: InterpretationSource,
    edge: dict[str, object],
    emitted_edge_refs: set[str],
    gaps: tuple[dict[str, object], ...],
    expected_code: str,
    expected_status: str,
) -> None:
    edge_ref = str(edge["edge_ref"])
    source_pin_ref = str(edge["source_pin_ref"])
    target_pin_ref = str(edge["target_pin_ref"])
    testcase.assertNotIn(edge_ref, emitted_edge_refs)
    matching = [gap for gap in gaps if edge_ref in gap["evidenceRefs"]]
    testcase.assertEqual(len(matching), 1)
    gap = matching[0]
    testcase.assertEqual(gap["code"], expected_code)
    testcase.assertEqual(gap["status"], expected_status)
    testcase.assertTrue(
        {
            edge_ref,
            source_pin_ref,
            target_pin_ref,
            *_matching_observation_refs(source, edge),
        }.issubset(set(gap["evidenceRefs"]))
    )


class InterpretationEdgeDirectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        asset_dir, _source_path, _payload = publish_interpretation_fixture(
            Path(self._temporary.name)
        )
        self.source = load_interpretation_source(asset_dir)

    def test_resolved_exec_edges_require_exact_output_to_input_direction(self) -> None:
        edge = next(
            edge
            for edge in self.source.edges
            if str(edge.get("resolution_status") or "").casefold() == "resolved_pin"
            and str(edge.get("kind") or "").casefold() == "exec"
        )
        for source_direction, target_direction, expected_code, expected_status in (
            (
                "EGPD_Output",
                "EGPD_Output",
                "UNRESOLVED_EXEC_EDGE",
                "NOT_RECOVERED",
            ),
            ("EGPD_Output", "mystery", "AMBIGUOUS_EXEC_EDGE", "AMBIGUOUS"),
            (
                "EGPD_Input",
                "EGPD_Output",
                "UNRESOLVED_EXEC_EDGE",
                "NOT_RECOVERED",
            ),
        ):
            with self.subTest(
                source_direction=source_direction,
                target_direction=target_direction,
            ):
                changed = _with_edge_directions(
                    self.source,
                    edge,
                    source_direction=source_direction,
                    target_direction=target_direction,
                )
                result = build_control_flow(changed)
                emitted = {
                    str(successor["edgeRef"])
                    for graph in result.graphs
                    for node in graph["nodes"]
                    for successor in node["successors"]
                }
                _assert_invalid_edge_is_only_a_gap(
                    self,
                    source=changed,
                    edge=edge,
                    emitted_edge_refs=emitted,
                    gaps=result.gaps,
                    expected_code=expected_code,
                    expected_status=expected_status,
                )

    def test_resolved_data_edges_require_exact_output_to_input_direction(self) -> None:
        edge = next(
            edge
            for edge in self.source.edges
            if str(edge.get("resolution_status") or "").casefold() == "resolved_pin"
            and str(edge.get("kind") or "").casefold() == "data"
        )
        for source_direction, target_direction, expected_code, expected_status in (
            (
                "EGPD_Output",
                "EGPD_Output",
                "UNRESOLVED_DATA_EDGE",
                "NOT_RECOVERED",
            ),
            ("EGPD_Output", "mystery", "AMBIGUOUS_DATA_EDGE", "AMBIGUOUS"),
            (
                "EGPD_Input",
                "EGPD_Output",
                "UNRESOLVED_DATA_EDGE",
                "NOT_RECOVERED",
            ),
        ):
            with self.subTest(
                source_direction=source_direction,
                target_direction=target_direction,
            ):
                changed = _with_edge_directions(
                    self.source,
                    edge,
                    source_direction=source_direction,
                    target_direction=target_direction,
                )
                result = build_data_flow(changed)
                emitted = {
                    str(row["edgeRef"])
                    for graph in result.graphs
                    for row in graph["edges"]
                }
                _assert_invalid_edge_is_only_a_gap(
                    self,
                    source=changed,
                    edge=edge,
                    emitted_edge_refs=emitted,
                    gaps=result.gaps,
                    expected_code=expected_code,
                    expected_status=expected_status,
                )

    def test_exec_kind_requires_two_exact_exec_pin_categories(self) -> None:
        edge = next(
            edge
            for edge in self.source.edges
            if str(edge.get("resolution_status") or "").casefold() == "resolved_pin"
            and str(edge.get("kind") or "").casefold() == "exec"
        )
        for source_category, target_category, expected_code, expected_status in (
            ("bool", "bool", "UNRESOLVED_EXEC_EDGE", "NOT_RECOVERED"),
            ("exec", "bool", "UNRESOLVED_EXEC_EDGE", "NOT_RECOVERED"),
            ("exec", "", "AMBIGUOUS_EXEC_EDGE", "AMBIGUOUS"),
        ):
            with self.subTest(
                source_category=source_category,
                target_category=target_category,
            ):
                changed = _with_edge_categories(
                    self.source,
                    edge,
                    source_category=source_category,
                    target_category=target_category,
                )
                control = build_control_flow(changed)
                data = build_data_flow(changed)
                emitted = {
                    str(successor["edgeRef"])
                    for graph in control.graphs
                    for node in graph["nodes"]
                    for successor in node["successors"]
                } | {
                    str(row["edgeRef"])
                    for graph in data.graphs
                    for row in graph["edges"]
                }
                _assert_invalid_edge_is_only_a_gap(
                    self,
                    source=changed,
                    edge=edge,
                    emitted_edge_refs=emitted,
                    gaps=control.gaps,
                    expected_code=expected_code,
                    expected_status=expected_status,
                )

    def test_data_kind_requires_two_compatible_exact_non_exec_categories(self) -> None:
        edge = next(
            edge
            for edge in self.source.edges
            if str(edge.get("resolution_status") or "").casefold() == "resolved_pin"
            and str(edge.get("kind") or "").casefold() == "data"
        )
        for source_category, target_category, expected_code, expected_status in (
            ("exec", "exec", "UNRESOLVED_DATA_EDGE", "NOT_RECOVERED"),
            ("bool", "exec", "UNRESOLVED_DATA_EDGE", "NOT_RECOVERED"),
            ("bool", "int", "UNRESOLVED_DATA_EDGE", "NOT_RECOVERED"),
            ("bool", "", "AMBIGUOUS_DATA_EDGE", "AMBIGUOUS"),
            ("wildcard", "wildcard", "AMBIGUOUS_DATA_EDGE", "AMBIGUOUS"),
        ):
            with self.subTest(
                source_category=source_category,
                target_category=target_category,
            ):
                changed = _with_edge_categories(
                    self.source,
                    edge,
                    source_category=source_category,
                    target_category=target_category,
                )
                control = build_control_flow(changed)
                data = build_data_flow(changed)
                emitted = {
                    str(successor["edgeRef"])
                    for graph in control.graphs
                    for node in graph["nodes"]
                    for successor in node["successors"]
                } | {
                    str(row["edgeRef"])
                    for graph in data.graphs
                    for row in graph["edges"]
                }
                _assert_invalid_edge_is_only_a_gap(
                    self,
                    source=changed,
                    edge=edge,
                    emitted_edge_refs=emitted,
                    gaps=data.gaps,
                    expected_code=expected_code,
                    expected_status=expected_status,
                )

    def test_unknown_edge_kind_is_gap_complete_and_never_cross_admitted(self) -> None:
        for original_kind, expected_code, expected_gap_owner in (
            ("exec", "AMBIGUOUS_EXEC_EDGE", "control"),
            ("data", "AMBIGUOUS_DATA_EDGE", "data"),
        ):
            with self.subTest(original_kind=original_kind):
                edge = next(
                    edge
                    for edge in self.source.edges
                    if str(edge.get("resolution_status") or "").casefold()
                    == "resolved_pin"
                    and str(edge.get("kind") or "").casefold() == original_kind
                )
                changed = _with_edge_kind(self.source, edge, kind="mystery")
                changed_edge = next(
                    row
                    for row in changed.edges
                    if str(row["edge_ref"]) == str(edge["edge_ref"])
                )
                control = build_control_flow(changed)
                data = build_data_flow(changed)
                emitted = {
                    str(successor["edgeRef"])
                    for graph in control.graphs
                    for node in graph["nodes"]
                    for successor in node["successors"]
                } | {
                    str(row["edgeRef"])
                    for graph in data.graphs
                    for row in graph["edges"]
                }
                _assert_invalid_edge_is_only_a_gap(
                    self,
                    source=changed,
                    edge=changed_edge,
                    emitted_edge_refs=emitted,
                    gaps=control.gaps if expected_gap_owner == "control" else data.gaps,
                    expected_code=expected_code,
                    expected_status="AMBIGUOUS",
                )

    def test_many_independent_control_cycles_remain_bounded_and_deterministic(
        self,
    ) -> None:
        graph_ref = "bp://adversarial/revision/graph/control"
        node_count = 8_000
        nodes: list[dict[str, object]] = []
        pins: list[dict[str, object]] = []
        edges: list[dict[str, object]] = []
        for index in range(node_count):
            node_ref = f"{graph_ref}/node/{index:05d}"
            output_ref = f"{node_ref}/pin/output"
            input_ref = f"{node_ref}/pin/input"
            nodes.append(
                {
                    "node_ref": node_ref,
                    "graph_ref": graph_ref,
                    "node_type": "K2Node_CallFunction",
                }
            )
            pins.extend(
                (
                    {
                        "pin_ref": output_ref,
                        "node_ref": node_ref,
                        "direction": "EGPD_Output",
                        "category": "exec",
                        "pin_type": {"PinCategory": "exec"},
                    },
                    {
                        "pin_ref": input_ref,
                        "node_ref": node_ref,
                        "direction": "EGPD_Input",
                        "category": "exec",
                        "pin_type": {"PinCategory": "exec"},
                    },
                )
            )
            edges.append(
                {
                    "edge_ref": f"{graph_ref}/edge/{index:05d}",
                    "graph_ref": graph_ref,
                    "source_pin_ref": output_ref,
                    "target_pin_ref": input_ref,
                    "kind": "exec",
                    "confidence": "high",
                    "resolution_status": "resolved_pin",
                }
            )
        source = replace(
            self.source,
            graphs=(
                {
                    "graph_ref": graph_ref,
                    "name": "ManyIndependentControlCycles",
                    "graph_type": "Function",
                    "status": "complete",
                },
            ),
            nodes=tuple(nodes),
            pins=tuple(pins),
            edges=tuple(edges),
            observations=(),
            references=(),
            defaults=(),
            diagnostics=(),
            coverage=(),
        )

        started = time.perf_counter()
        first = build_control_flow(source)
        elapsed = time.perf_counter() - started
        second = build_control_flow(source)

        self.assertLess(elapsed, 1.5)
        self.assertEqual(first, second)
        cycle_gaps = [gap for gap in first.gaps if gap["code"] == "UNSTRUCTURED_CYCLE"]
        self.assertEqual(len(cycle_gaps), node_count)
        self.assertTrue(all(len(gap["evidenceRefs"]) == 3 for gap in cycle_gaps))

    def test_many_independent_cycles_remain_bounded_and_deterministic(self) -> None:
        graph_ref = "bp://adversarial/revision/graph/1"
        node_count = 5_000
        nodes: list[dict[str, object]] = []
        pins: list[dict[str, object]] = []
        edges: list[dict[str, object]] = []
        for index in range(node_count):
            node_ref = f"{graph_ref}/node/{index:05d}"
            output_ref = f"{node_ref}/pin/output"
            input_ref = f"{node_ref}/pin/input"
            nodes.append(
                {
                    "node_ref": node_ref,
                    "graph_ref": graph_ref,
                    "node_type": "K2Node_Knot",
                }
            )
            pins.extend(
                (
                    {
                        "pin_ref": output_ref,
                        "node_ref": node_ref,
                        "direction": "EGPD_Output",
                        "category": "bool",
                        "pin_type": {},
                    },
                    {
                        "pin_ref": input_ref,
                        "node_ref": node_ref,
                        "direction": "EGPD_Input",
                        "category": "bool",
                        "pin_type": {},
                    },
                )
            )
            edges.append(
                {
                    "edge_ref": f"{graph_ref}/edge/{index:05d}",
                    "graph_ref": graph_ref,
                    "source_pin_ref": output_ref,
                    "target_pin_ref": input_ref,
                    "kind": "data",
                    "confidence": "high",
                    "resolution_status": "resolved_pin",
                }
            )
        source = replace(
            self.source,
            graphs=(
                {
                    "graph_ref": graph_ref,
                    "name": "ManyIndependentCycles",
                    "graph_type": "Function",
                    "status": "complete",
                },
            ),
            nodes=tuple(nodes),
            pins=tuple(pins),
            edges=tuple(edges),
            observations=(),
            references=(),
            defaults=(),
            diagnostics=(),
            coverage=(),
        )

        started = time.perf_counter()
        first = build_data_flow(source)
        elapsed = time.perf_counter() - started
        second = build_data_flow(source)

        self.assertLess(elapsed, 1.5)
        self.assertEqual(first, second)
        cycle_gaps = [gap for gap in first.gaps if gap["code"] == "DATA_CYCLE"]
        self.assertEqual(len(cycle_gaps), node_count)
        self.assertTrue(all(len(gap["evidenceRefs"]) == 3 for gap in cycle_gaps))


if __name__ == "__main__":
    unittest.main()
