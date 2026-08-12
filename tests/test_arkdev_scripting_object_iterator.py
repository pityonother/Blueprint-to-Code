from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_scripting_probe.contracts import (  # noqa: E402
    assert_path_free,
    attach_semantic_digest,
)
from arkdev_scripting_probe.in_editor.arkdev_explicit_graph_snapshot import (  # noqa: E402
    collect_explicit_graph_snapshot,
)
from arkdev_scripting_probe.in_editor.arkdev_official_scripting_probe import (  # noqa: E402
    build_probe_bundle,
)
from arkdev_scripting_probe.result_reader import validator_summary  # noqa: E402


class _Ledger:
    def __init__(self) -> None:
        self.mutations: list[str] = []
        self.iterator_classes: list[type[object]] = []

    def mutation(self, name: str) -> None:
        self.mutations.append(name)
        raise AssertionError(f"mutation API was called: {name}")


class _ClassName:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


class _Object:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    def modify(self) -> None:
        self._ledger.mutation("modify")

    def get_outer(self) -> object | None:
        return None

    def get_typed_outer(self, _expected: type[object]) -> object | None:
        return None

    def get_path_name(self) -> str:
        return ""

    def set_editor_property(self, _name: str, _value: object) -> None:
        self._ledger.mutation("set_editor_property")


class _EdGraph(_Object):
    def __init__(
        self,
        ledger: _Ledger,
        *,
        path: str = "/Game/Test/BP_Probe.BP_Probe:EventGraph",
        direct_nodes: list[object] | None = None,
    ) -> None:
        super().__init__(ledger)
        self._path = path
        if direct_nodes is not None:
            self.nodes = direct_nodes

    def get_name(self) -> str:
        return "EventGraph"

    def get_path_name(self) -> str:
        return self._path

    def get_class(self) -> _ClassName:
        return _ClassName("EdGraph")

    def get_editor_property(self, name: str) -> object:
        if name == "nodes" and hasattr(self, "nodes"):
            return self.nodes
        raise AttributeError(name)


class _OtherOuter(_Object):
    def __init__(self, ledger: _Ledger, *, path: str) -> None:
        super().__init__(ledger)
        self._path = path

    def get_name(self) -> str:
        return "EventGraph"

    def get_path_name(self) -> str:
        return self._path

    def get_class(self) -> _ClassName:
        return _ClassName("NotEdGraph")


class _Guid:
    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


class _PinType:
    def __init__(self, category: str, subcategory: str) -> None:
        self.pin_category = category
        self.pin_sub_category = subcategory


class _Pin:
    def __init__(
        self,
        name: str,
        direction: str,
        category: str = "",
        subcategory: str = "",
    ) -> None:
        self.pin_name = name
        self.direction = direction
        self.pin_type = _PinType(category, subcategory)

    def get_name(self) -> str:
        return self.pin_name


class _EdGraphNode(_Object):
    def __init__(
        self,
        ledger: _Ledger,
        *,
        name: str,
        guid: str,
        outer: object,
        x: int = 0,
        y: int = 0,
        path: str | None = None,
        typed_outer: object | None = None,
        guid_style: str = "snake",
        pins: list[_Pin] | None = None,
    ) -> None:
        super().__init__(ledger)
        self._name = name
        self._outer = outer
        self._typed_outer = typed_outer
        self._path = path or f"/Game/Test/BP_Probe.BP_Probe:EventGraph.{name}"
        self.node_pos_x = x
        self.node_pos_y = y
        self._properties: dict[str, object] = {}
        if guid_style == "snake":
            self.node_guid = _Guid(guid)
        elif guid_style == "camel":
            self.NodeGuid = _Guid(guid)
        else:
            self._properties["node_guid"] = _Guid(guid)
        self.pins = list(pins or [])

    def get_name(self) -> str:
        return self._name

    def get_path_name(self) -> str:
        return self._path

    def get_class(self) -> _ClassName:
        return _ClassName(type(self).__name__)

    def get_outer(self) -> object:
        return self._outer

    def get_typed_outer(self, expected: type[object]) -> object | None:
        if self._typed_outer is not None:
            return self._typed_outer
        return self._outer if isinstance(self._outer, expected) else None

    def get_editor_property(self, name: str) -> object:
        if name in self._properties:
            return self._properties[name]
        if hasattr(self, name):
            return getattr(self, name)
        raise AttributeError(name)


class _K2Node(_EdGraphNode):
    pass


class _Vector:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


class _Package:
    def __init__(self, dirty: bool) -> None:
        self._dirty = dirty

    def is_dirty(self) -> bool:
        return self._dirty


class _Blueprint(_Object):
    def __init__(
        self,
        ledger: _Ledger,
        graph: _EdGraph,
        *,
        status: object = "BS_UP_TO_DATE",
        dirty: bool = False,
    ) -> None:
        super().__init__(ledger)
        self.graph = graph
        self._status = status
        self._package = _Package(dirty)

    def get_editor_property(self, name: str) -> object:
        if name == "status":
            return self._status
        raise AttributeError(name)

    def get_outermost(self) -> _Package:
        return self._package


class _BlueprintEditorLibrary:
    def __init__(
        self,
        ledger: _Ledger,
        *,
        position_api: bool = True,
        pin_api: bool = True,
    ) -> None:
        self._ledger = ledger
        self._position_api = position_api
        self._pin_api = pin_api

    def get_blueprint_asset(self, value: object) -> object:
        return value

    def find_graph(self, blueprint: _Blueprint, name: str) -> _EdGraph | None:
        return blueprint.graph if name == "EventGraph" else None

    def get_all_graphs(self, blueprint: _Blueprint) -> list[_EdGraph]:
        return [blueprint.graph]

    def get_all_graph_names(self, _blueprint: _Blueprint) -> list[str]:
        return ["EventGraph"]

    def get_node_pos(self, node: _EdGraphNode) -> _Vector:
        if not self._position_api:
            raise RuntimeError("position API unavailable")
        return _Vector(node.node_pos_x, node.node_pos_y)

    def list_all_pins(self, node: _EdGraphNode) -> list[_Pin]:
        if not self._pin_api:
            raise RuntimeError("pin API unavailable")
        return list(node.pins)

    def set_node_pos(self, *_args: object) -> None:
        self._ledger.mutation("set_node_pos")

    def compile_blueprint(self, *_args: object) -> None:
        self._ledger.mutation("compile_blueprint")

    def create_connection(self, *_args: object) -> None:
        self._ledger.mutation("create_connection")

    def break_pin_links(self, *_args: object) -> None:
        self._ledger.mutation("break_pin_links")


class _AssetEditorSubsystem:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    def get_all_edited_assets(self) -> list[object]:
        return []

    def open_editor_for_assets(self, *_args: object) -> None:
        self._ledger.mutation("open_editor_for_assets")


class _EditorAssetLibrary:
    def __init__(self, ledger: _Ledger, blueprint: _Blueprint) -> None:
        self._ledger = ledger
        self._blueprint = blueprint

    def load_asset(self, _path: str) -> _Blueprint:
        return self._blueprint

    def save_asset(self, *_args: object) -> None:
        self._ledger.mutation("save_asset")


class _BlueprintGraphEditor:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    def create_connection(self, *_args: object) -> None:
        self._ledger.mutation("graph_editor.create_connection")


class _BlueprintGraphPinLibrary:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    def break_pin_links(self, *_args: object) -> None:
        self._ledger.mutation("pin_library.break_pin_links")

    def set_default_value(self, *_args: object) -> None:
        self._ledger.mutation("pin_library.set_default_value")


class _SystemLibrary:
    @staticmethod
    def get_engine_version() -> str:
        return "5.5.4-0+UE5"


class _FakeUnreal:
    Object = _Object
    EdGraph = _EdGraph
    EdGraphNode = _EdGraphNode
    K2Node = _K2Node
    SystemLibrary = _SystemLibrary

    def __init__(
        self,
        ledger: _Ledger,
        blueprint: _Blueprint,
        *,
        ed_nodes: list[object] | None = None,
        k2_nodes: list[object] | None = None,
        position_api: bool = True,
        pin_api: bool = True,
    ) -> None:
        self._ledger = ledger
        self._blueprint = blueprint
        self._iterators = {
            _EdGraphNode: list(ed_nodes or []),
            _K2Node: list(k2_nodes or []),
        }
        self.BlueprintEditorLibrary = _BlueprintEditorLibrary(
            ledger,
            position_api=position_api,
            pin_api=pin_api,
        )
        self.AssetEditorSubsystem = _AssetEditorSubsystem(ledger)
        self.EditorAssetLibrary = _EditorAssetLibrary(ledger, blueprint)
        self.BlueprintGraphEditor = _BlueprintGraphEditor(ledger)
        self.BlueprintGraphPinLibrary = _BlueprintGraphPinLibrary(ledger)
        self.EditorUtilitySubsystem = type("EditorUtilitySubsystem", (), {})
        self.EditorUtilityBlueprint = type("EditorUtilityBlueprint", (), {})
        self.EditorUtilityWidgetBlueprint = type(
            "EditorUtilityWidgetBlueprint",
            (),
            {},
        )
        self.EditorPythonScripting = type("EditorPythonScripting", (), {})
        self.EditorUtilityLibrary = type("EditorUtilityLibrary", (), {})

    def load_asset(self, _path: str) -> _Blueprint:
        return self._blueprint

    def ObjectIterator(self, class_type: type[object]):  # noqa: N802
        self._ledger.iterator_classes.append(class_type)
        return iter(self._iterators.get(class_type, []))

    def get_editor_subsystem(self, _class: object) -> _AssetEditorSubsystem:
        return self.AssetEditorSubsystem


def _request(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "blueprint-to-code.arkdev-scripting-probe-request/v1",
        "objectPath": "/Game/Test/BP_Probe.BP_Probe",
        "graphName": "EventGraph",
        "maxNodes": 200,
    }
    value.update(updates)
    return value


class ObjectIteratorClosureTests(unittest.TestCase):
    def test_direct_property_wins_and_records_bounded_authoritative_counts(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger, direct_nodes=[])
        nodes = [
            _EdGraphNode(
                ledger,
                name=name,
                guid=guid,
                outer=graph,
                x=x,
                y=y,
            )
            for name, guid, x, y in (
                ("Third", "33333333333333333333333333333333", 200, 50),
                ("First", "11111111111111111111111111111111", 0, 0),
                ("Second", "22222222222222222222222222222222", 100, 0),
            )
        ]
        graph.nodes = nodes
        blueprint = _Blueprint(ledger, graph)
        unreal = _FakeUnreal(ledger, blueprint, ed_nodes=nodes)

        target, snapshot = collect_explicit_graph_snapshot(
            unreal,
            _request(maxNodes=2),
        )

        self.assertEqual(target["status"], "AVAILABLE")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["enumerationStrategy"], "DIRECT_GRAPH_PROPERTY")
        self.assertEqual(snapshot["objectsScanned"], 0)
        self.assertEqual(snapshot["matchingNodes"], 3)
        self.assertEqual(snapshot["returnedNodes"], 2)
        self.assertEqual(snapshot["nodesOmitted"], 1)
        self.assertFalse(snapshot["scanTruncated"])
        self.assertEqual(snapshot["nodeCount"], snapshot["matchingNodes"])
        self.assertEqual(snapshot["nodesReturned"], 2)
        self.assertEqual(
            [node["name"] for node in snapshot["authoritativeNodes"]],
            ["First", "Second"],
        )
        self.assertEqual(ledger.iterator_classes, [])
        self.assertEqual(ledger.mutations, [])

    def test_ed_graph_node_iterator_accepts_only_exact_outer_membership(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        wrong_graph = _EdGraph(
            ledger,
            path="/Game/Test/BP_Other.BP_Other:EventGraph",
        )
        wrong = _EdGraphNode(
            ledger,
            name="Wrong",
            guid="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            outer=wrong_graph,
        )
        exact = _EdGraphNode(
            ledger,
            name="Exact",
            guid="BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            outer=graph,
        )
        blueprint = _Blueprint(ledger, graph)
        unreal = _FakeUnreal(ledger, blueprint, ed_nodes=[wrong, exact])

        target, snapshot = collect_explicit_graph_snapshot(unreal, _request())

        self.assertEqual(target["status"], "AVAILABLE")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            snapshot["enumerationStrategy"],
            "OBJECT_ITERATOR_EDGRAPHNODE_EXACT_OUTER",
        )
        self.assertEqual(snapshot["objectsScanned"], 2)
        self.assertEqual(snapshot["matchingNodes"], 1)
        self.assertEqual(snapshot["returnedNodes"], 1)
        self.assertEqual(snapshot["authoritativeNodes"][0]["name"], "Exact")
        self.assertEqual(
            target["capabilities"]["exactOuterNodeEnumeration"],
            "AVAILABLE",
        )

    def test_exact_full_path_and_typed_outer_are_valid_but_name_only_is_not(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        wrapper = _EdGraph(ledger, path=graph.get_path_name())
        same_name_wrong_path = _EdGraph(
            ledger,
            path="/Game/Test/BP_Other.BP_Other:EventGraph",
        )
        non_graph_same_path = _OtherOuter(ledger, path=graph.get_path_name())
        accepted_by_path = _EdGraphNode(
            ledger,
            name="Path",
            guid="11111111111111111111111111111111",
            outer=wrapper,
        )
        accepted_by_typed = _EdGraphNode(
            ledger,
            name="Typed",
            guid="22222222222222222222222222222222",
            outer=non_graph_same_path,
            typed_outer=graph,
        )
        rejected_name_only = _EdGraphNode(
            ledger,
            name="NameOnly",
            guid="33333333333333333333333333333333",
            outer=same_name_wrong_path,
        )
        rejected_type = _EdGraphNode(
            ledger,
            name="WrongType",
            guid="44444444444444444444444444444444",
            outer=non_graph_same_path,
        )
        blueprint = _Blueprint(ledger, graph)
        unreal = _FakeUnreal(
            ledger,
            blueprint,
            ed_nodes=[
                rejected_name_only,
                rejected_type,
                accepted_by_path,
                accepted_by_typed,
            ],
        )

        target, snapshot = collect_explicit_graph_snapshot(unreal, _request())

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["matchingNodes"], 2)
        self.assertEqual(
            {node["name"] for node in snapshot["authoritativeNodes"]},
            {"Path", "Typed"},
        )
        self.assertEqual(
            target["capabilities"]["exactTypedOuterNodeEnumeration"],
            "AVAILABLE",
        )

    def test_k2_iterator_is_only_the_zero_match_fallback(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        exact = _K2Node(
            ledger,
            name="K2",
            guid="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            outer=graph,
        )
        blueprint = _Blueprint(ledger, graph)
        unreal = _FakeUnreal(ledger, blueprint, ed_nodes=[], k2_nodes=[exact])

        _target, snapshot = collect_explicit_graph_snapshot(unreal, _request())

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(
            snapshot["enumerationStrategy"],
            "OBJECT_ITERATOR_K2NODE_EXACT_OUTER",
        )
        self.assertEqual(ledger.iterator_classes, [_EdGraphNode, _K2Node])

    def test_iterator_scan_limit_is_shared_and_fails_closed(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        wrong_graph = _EdGraph(
            ledger,
            path="/Game/Test/BP_Other.BP_Other:EventGraph",
        )
        exact = _EdGraphNode(
            ledger,
            name="ExactBeforeLimit",
            guid="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            outer=graph,
        )
        wrong_nodes = [
            _EdGraphNode(
                ledger,
                name=f"Wrong{i}",
                guid=f"{i + 1:032X}",
                outer=wrong_graph,
            )
            for i in range(3)
        ]
        blueprint = _Blueprint(ledger, graph)
        unreal = _FakeUnreal(
            ledger,
            blueprint,
            ed_nodes=[exact, *wrong_nodes],
        )

        target, snapshot = collect_explicit_graph_snapshot(
            unreal,
            _request(maxObjectsScanned=2),
        )

        self.assertEqual(target["status"], "ERROR")
        self.assertIsNone(snapshot)
        self.assertEqual(target["objectsScanned"], 2)
        self.assertEqual(target["matchingNodes"], 1)
        self.assertEqual(target["returnedNodes"], 0)
        self.assertEqual(target["nodesOmitted"], 1)
        self.assertTrue(target["scanTruncated"])
        self.assertIn("OBJECT_ITERATOR_SCAN_LIMIT_REACHED", target["gaps"])
        self.assertEqual(
            target["capabilities"]["graphNodeEnumeration"],
            "ERROR",
        )
        self.assertEqual(ledger.iterator_classes, [_EdGraphNode])

    def test_validator_requires_the_full_untruncated_snapshot_gate(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        node = _EdGraphNode(
            ledger,
            name="Node",
            guid="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            outer=graph,
            x=10,
            y=20,
        )
        blueprint = _Blueprint(ledger, graph)
        unreal = _FakeUnreal(ledger, blueprint, ed_nodes=[node])
        result, snapshot = build_probe_bundle(
            unreal,
            request=_request(),
            generated_at="2026-08-11T00:00:00Z",
            python_version="3.11.8",
        )
        self.assertIsNotNone(snapshot)
        assert snapshot is not None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe_path = root / "live-probe.json"
            snapshot_path = root / "explicit-graph-snapshot.json"
            probe_path.write_text(json.dumps(result), encoding="utf-8")
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

            summary = validator_summary(probe_path, snapshot_path)
            self.assertEqual(summary["EXPLICIT_GRAPH_SNAPSHOT"], "PASS")

            target = result["explicitTarget"]
            assert isinstance(target, dict)
            target["scanTruncated"] = True
            attach_semantic_digest(result)
            probe_path.write_text(json.dumps(result), encoding="utf-8")
            summary = validator_summary(probe_path, snapshot_path)

        self.assertEqual(summary["EXPLICIT_GRAPH_SNAPSHOT"], "ERROR")

    def test_duplicate_and_invalid_guid_are_counted_without_false_authority(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        valid = _EdGraphNode(
            ledger,
            name="Valid",
            guid="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            outer=graph,
        )
        invalid = _EdGraphNode(
            ledger,
            name="Invalid",
            guid="00000000000000000000000000000000",
            outer=graph,
        )
        blueprint = _Blueprint(ledger, graph)
        unreal = _FakeUnreal(
            ledger,
            blueprint,
            ed_nodes=[valid, valid, invalid],
        )

        target, snapshot = collect_explicit_graph_snapshot(unreal, _request())

        self.assertEqual(target["status"], "MISSING")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["objectsScanned"], 3)
        self.assertEqual(snapshot["matchingNodes"], 2)
        self.assertEqual(snapshot["returnedNodes"], 1)
        self.assertEqual(snapshot["nodesOmitted"], 1)
        self.assertEqual(len(snapshot["authoritativeNodes"]), 1)
        self.assertEqual(snapshot["authoritativeNodes"][0]["name"], "Valid")
        self.assertIn("DUPLICATE_OBJECT_OBSERVED", snapshot["gaps"])
        self.assertIn("NODE_GUID_INVALID_OR_UNAVAILABLE", snapshot["gaps"])
        self.assertEqual(target["capabilities"]["nodeGuidRead"], "MISSING")

    def test_guid_position_pins_compile_and_dirty_are_read_only(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        node = _EdGraphNode(
            ledger,
            name="ReadOnly",
            guid="ABCDEFABCDEFABCDEFABCDEFABCDEFAB",
            guid_style="camel",
            outer=graph,
            x=12,
            y=34,
            pins=[
                _Pin("Z", "OUTPUT", "object", "Actor"),
                _Pin("A", "INPUT", "exec", ""),
            ],
        )
        blueprint = _Blueprint(
            ledger,
            graph,
            status="BlueprintStatus.BS_UP_TO_DATE_WITH_WARNINGS",
            dirty=False,
        )
        unreal = _FakeUnreal(
            ledger,
            blueprint,
            ed_nodes=[node],
            position_api=False,
        )

        target, snapshot = collect_explicit_graph_snapshot(unreal, _request())

        self.assertEqual(target["status"], "AVAILABLE")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        projected = snapshot["authoritativeNodes"][0]
        self.assertEqual((projected["x"], projected["y"]), (12, 34))
        self.assertEqual(projected["pinCount"], 2)
        self.assertEqual([pin["name"] for pin in projected["pins"]], ["A", "Z"])
        self.assertEqual(projected["pins"][1]["category"], "object")
        self.assertEqual(projected["pins"][1]["subcategory"], "Actor")
        self.assertEqual(snapshot["compileStatus"], "BS_UP_TO_DATE_WITH_WARNINGS")
        self.assertFalse(snapshot["packageDirty"])
        self.assertEqual(target["capabilities"]["nodePinRead"], "AVAILABLE")
        self.assertEqual(target["capabilities"]["blueprintStatusRead"], "AVAILABLE")
        self.assertEqual(target["capabilities"]["packageDirtyRead"], "AVAILABLE")
        self.assertEqual(ledger.mutations, [])

    def test_probe_matrix_reflects_mutation_surfaces_without_calling_them(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        node = _EdGraphNode(
            ledger,
            name="Node",
            guid="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            outer=graph,
        )
        blueprint = _Blueprint(ledger, graph)
        unreal = _FakeUnreal(ledger, blueprint, ed_nodes=[node])

        result, snapshot = build_probe_bundle(
            unreal,
            request=_request(),
            generated_at="2026-08-11T00:00:00Z",
            python_version="3.11.8",
        )

        self.assertIsNotNone(snapshot)
        for class_name in (
            "ObjectIterator",
            "EdGraphNode",
            "K2Node",
            "BlueprintGraphEditor",
            "BlueprintGraphPinLibrary",
        ):
            self.assertEqual(result["classes"][class_name], "AVAILABLE")
        for method_name in (
            "Object.get_outer",
            "Object.get_typed_outer",
            "Object.get_path_name",
        ):
            self.assertEqual(result["methods"][method_name], "AVAILABLE")
        observed = {
            (item["owner"], item["method"], item["status"])
            for item in result["mutationApisObserved"]
        }
        self.assertIn(
            (
                "BlueprintGraphEditor",
                "create_connection",
                "PRESENT_BUT_NOT_USED",
            ),
            observed,
        )
        self.assertIn(
            (
                "BlueprintGraphPinLibrary",
                "break_pin_links",
                "PRESENT_BUT_NOT_USED",
            ),
            observed,
        )
        self.assertEqual(ledger.mutations, [])
        assert_path_free(result)

    def test_iterator_order_does_not_change_snapshot_digest(self) -> None:
        ledger = _Ledger()
        graph = _EdGraph(ledger)
        first = _EdGraphNode(
            ledger,
            name="First",
            guid="11111111111111111111111111111111",
            outer=graph,
            x=0,
            y=0,
        )
        second = _EdGraphNode(
            ledger,
            name="Second",
            guid="22222222222222222222222222222222",
            outer=graph,
            x=100,
            y=0,
        )
        blueprint = _Blueprint(ledger, graph)
        forward = _FakeUnreal(ledger, blueprint, ed_nodes=[first, second])
        reverse = _FakeUnreal(ledger, blueprint, ed_nodes=[second, first])

        _target, first_snapshot = collect_explicit_graph_snapshot(
            forward,
            _request(),
        )
        _target, second_snapshot = collect_explicit_graph_snapshot(
            reverse,
            _request(),
        )

        self.assertIsNotNone(first_snapshot)
        self.assertIsNotNone(second_snapshot)
        assert first_snapshot is not None and second_snapshot is not None
        self.assertEqual(
            first_snapshot["semanticDigest"],
            second_snapshot["semanticDigest"],
        )
        assert_path_free(first_snapshot)


if __name__ == "__main__":
    unittest.main()
