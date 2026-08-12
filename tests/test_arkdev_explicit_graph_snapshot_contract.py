from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_scripting_probe.contracts import (  # noqa: E402
    SNAPSHOT_SCHEMA,
    assert_path_free,
)
from arkdev_scripting_probe.in_editor.arkdev_explicit_graph_snapshot import (  # noqa: E402
    collect_explicit_graph_snapshot,
)
from arkdev_scripting_probe.result_reader import (  # noqa: E402
    validate_snapshot_result,
)


class _Guid:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


class _Class:
    def __init__(self, name: str) -> None:
        self.name = name

    def get_name(self) -> str:
        return self.name


class _Node:
    mutation_calls: list[str] = []

    def __init__(self, name: str, guid: str, x: int, y: int) -> None:
        self._name = name
        self.node_guid = _Guid(guid)
        self.node_pos_x = x
        self.node_pos_y = y

    def get_name(self) -> str:
        return self._name

    def get_class(self) -> _Class:
        return _Class("K2Node_CallFunction")

    def set_editor_property(self, _name: str, _value: object) -> None:
        self.mutation_calls.append("set_editor_property")
        raise AssertionError("mutation API was called")

    def modify(self) -> None:
        self.mutation_calls.append("modify")
        raise AssertionError("mutation API was called")


class _Graph:
    def __init__(self, nodes: list[_Node]) -> None:
        self.nodes = nodes

    def get_name(self) -> str:
        return "EventGraph"

    def get_path_name(self) -> str:
        return "/Game/Test/BP_Probe.BP_Probe:EventGraph"

    def get_class(self) -> _Class:
        return _Class("EdGraph")


class _Blueprint:
    def __init__(self, graph: _Graph) -> None:
        self.graph = graph


class _BlueprintEditorLibrary:
    mutation_calls: list[str] = []

    @classmethod
    def get_blueprint_asset(cls, asset: object) -> object:
        return asset

    @classmethod
    def find_graph(cls, blueprint: _Blueprint, graph_name: str) -> _Graph | None:
        return blueprint.graph if graph_name == "EventGraph" else None

    @classmethod
    def compile_blueprint(cls, _blueprint: object) -> None:
        cls.mutation_calls.append("compile_blueprint")
        raise AssertionError("mutation API was called")

    @classmethod
    def set_node_pos(cls, _node: object, _x: int, _y: int) -> None:
        cls.mutation_calls.append("set_node_pos")
        raise AssertionError("mutation API was called")


class _FakeUnreal:
    BlueprintEditorLibrary = _BlueprintEditorLibrary
    asset: object | None = None
    mutation_calls: list[str] = []

    @classmethod
    def load_asset(cls, _object_path: str) -> object | None:
        return cls.asset

    @classmethod
    def save_asset(cls, _object_path: str) -> None:
        cls.mutation_calls.append("save_asset")
        raise AssertionError("mutation API was called")


class ExplicitGraphSnapshotContractTests(unittest.TestCase):
    def setUp(self) -> None:
        _Node.mutation_calls.clear()
        _BlueprintEditorLibrary.mutation_calls.clear()
        _FakeUnreal.mutation_calls.clear()
        _FakeUnreal.asset = None
        self.request = {
            "schema": "blueprint-to-code.arkdev-scripting-probe-request/v1",
            "objectPath": "/Game/Test/BP_Probe.BP_Probe",
            "graphName": "EventGraph",
            "maxNodes": 2,
        }

    def test_snapshot_is_bounded_deterministic_and_never_mutates(self) -> None:
        nodes = [
            _Node("Third", "33333333-3333-3333-3333-333333333333", 200, 50),
            _Node("First", "11111111-1111-1111-1111-111111111111", 0, 0),
            _Node("Second", "22222222-2222-2222-2222-222222222222", 100, 0),
        ]
        _FakeUnreal.asset = _Blueprint(_Graph(nodes))

        target, snapshot = collect_explicit_graph_snapshot(
            _FakeUnreal,
            self.request,
        )

        self.assertEqual(target["status"], "AVAILABLE")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(snapshot["nodeCount"], 3)
        self.assertEqual(snapshot["nodesReturned"], 2)
        self.assertEqual(snapshot["nodesOmitted"], 1)
        self.assertEqual(
            [node["name"] for node in snapshot["nodes"]],
            ["First", "Second"],
        )
        self.assertEqual(
            snapshot["nodes"][0]["nodeGuid"],
            "11111111111111111111111111111111",
        )
        self.assertEqual(snapshot["nodes"][1]["x"], 100)
        validate_snapshot_result(snapshot)
        assert_path_free(snapshot)
        self.assertEqual(_Node.mutation_calls, [])
        self.assertEqual(_BlueprintEditorLibrary.mutation_calls, [])
        self.assertEqual(_FakeUnreal.mutation_calls, [])

    def test_zero_guid_is_omitted_not_fabricated(self) -> None:
        _FakeUnreal.asset = _Blueprint(
            _Graph([_Node("Invalid", "00000000-0000-0000-0000-000000000000", 1, 2)])
        )

        target, snapshot = collect_explicit_graph_snapshot(
            _FakeUnreal,
            self.request,
        )

        self.assertEqual(target["capabilities"]["nodeGuidRead"], "MISSING")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertNotIn("nodeGuid", snapshot["nodes"][0])
        self.assertIn("NODE_GUID_INVALID_OR_UNAVAILABLE", snapshot["gaps"])

    def test_missing_asset_and_graph_are_fail_closed(self) -> None:
        target, snapshot = collect_explicit_graph_snapshot(
            _FakeUnreal,
            self.request,
        )
        self.assertEqual(target["status"], "MISSING")
        self.assertEqual(target["capabilities"]["explicitAssetLoad"], "MISSING")
        self.assertIsNone(snapshot)

        _FakeUnreal.asset = _Blueprint(_Graph([]))
        missing_graph = dict(self.request)
        missing_graph["graphName"] = "DoesNotExist"
        target, snapshot = collect_explicit_graph_snapshot(
            _FakeUnreal,
            missing_graph,
        )
        self.assertEqual(target["capabilities"]["explicitGraphFind"], "MISSING")
        self.assertIsNone(snapshot)

    def test_node_cap_never_exceeds_two_hundred(self) -> None:
        nodes = [
            _Node(f"Node{i}", f"{i + 1:032x}", i, i)
            for i in range(250)
        ]
        _FakeUnreal.asset = _Blueprint(_Graph(nodes))
        request = dict(self.request)
        request["maxNodes"] = 9999

        _target, snapshot = collect_explicit_graph_snapshot(_FakeUnreal, request)

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["nodesReturned"], 200)
        self.assertEqual(snapshot["nodesOmitted"], 50)


if __name__ == "__main__":
    unittest.main()
