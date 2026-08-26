from __future__ import annotations

import copy
import sys
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
from arkdev_scripting_probe.in_editor.arkdev_evidence_node_binding_probe import (  # noqa: E402
    collect_node_binding_result,
)
from arkdev_scripting_probe.node_binding import (  # noqa: E402
    REQUEST_SCHEMA,
    RESULT_SCHEMA,
    validate_request,
    validate_result,
)
from build_arkdev_node_binding_request import build_request  # noqa: E402


ASSET_ID = "fixture-asset"
REVISION_ID = "0123456789abcdef01234567"
MANIFEST_SHA256 = "a" * 64
OBJECT_PATH = "/Game/Test/BP_NodeBinding.BP_NodeBinding"
GRAPH_REF = f"bp://{ASSET_ID}@{REVISION_ID}/g/7"
GRAPH_PATH = f"{OBJECT_PATH}:EventGraph"
NODE_REF = f"{GRAPH_REF}/n/101"
GUID = "11111111111111111111111111111111"
EXPECTED_CLASS = "K2Node_IfThenElse"
OBJECT_NAME = "K2Node_IfThenElse_0"


def _windows_test_path(*parts: str) -> str:
    separator = chr(92)
    return "C:" + separator + separator.join(parts)


def _pin_signature(
    name: str,
    direction: str,
    ordinal: int,
    *,
    category: str = "",
    subcategory: str = "",
) -> dict[str, object]:
    return {
        "name": name,
        "direction": direction,
        "ordinal": ordinal,
        "category": category,
        "subcategory": subcategory,
    }


def _locator(
    *,
    node_ref: str = NODE_REF,
    object_name: str = OBJECT_NAME,
    class_name: str = EXPECTED_CLASS,
    node_guid: str = GUID,
    graph_ref: str = GRAPH_REF,
    x: int | None = 120,
    y: int | None = 240,
    pins: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "nodeRef": node_ref,
        "graphRef": graph_ref,
        "objectName": object_name,
        "className": class_name,
        "nodeGuid": node_guid,
        "evidenceRevisionId": REVISION_ID,
        "evidenceManifestSha256": MANIFEST_SHA256,
        # These fields exercise projection: neither may leak into a request.
        "databasePath": _windows_test_path("private", "evidence.sqlite"),
        "sourcePath": _windows_test_path("private", "BP_NodeBinding.uasset"),
    }
    if x is not None:
        value["x"] = x
    if y is not None:
        value["y"] = y
    if pins is not None:
        value["pins"] = copy.deepcopy(pins)
    return value


class _LocatorSource:
    def __init__(
        self,
        *,
        freshness: str = "FRESH",
        locators: list[dict[str, object]] | None = None,
    ) -> None:
        self.asset = {
            "name": "BP_NodeBinding",
            "objectPath": OBJECT_PATH,
            "assetId": ASSET_ID,
            "evidenceRevisionId": REVISION_ID,
            "evidenceManifestSha256": MANIFEST_SHA256,
            "freshnessStatus": freshness,
            "databasePath": _windows_test_path("private", "evidence.sqlite"),
        }
        rows = locators if locators is not None else [_locator()]
        self.locators = {str(row["nodeRef"]): copy.deepcopy(row) for row in rows}
        self.asset_queries: list[str] = []
        self.node_queries: list[str] = []

    def resolve_asset(self, asset: str) -> dict[str, object]:
        self.asset_queries.append(asset)
        return copy.deepcopy(self.asset)

    def resolve_node(self, node_ref: str) -> dict[str, object]:
        self.node_queries.append(node_ref)
        return copy.deepcopy(self.locators[node_ref])


def _request_node(
    *,
    node_ref: str = NODE_REF,
    object_name: str = OBJECT_NAME,
    expected_class: str = EXPECTED_CLASS,
    expected_guid: str = GUID,
    x: int | None = 120,
    y: int | None = 240,
    pins: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "nodeRef": node_ref,
        "objectName": object_name,
        "expectedClassName": expected_class,
        "expectedNodeGuid": expected_guid,
    }
    if x is not None:
        value["evidenceX"] = x
    if y is not None:
        value["evidenceY"] = y
    if pins is not None:
        value["evidencePins"] = copy.deepcopy(pins)
    return value


def _request(nodes: list[dict[str, object]] | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": REQUEST_SCHEMA,
        "requestId": "node-binding-request://fixture",
        "asset": {
            "name": "BP_NodeBinding",
            "objectPath": OBJECT_PATH,
            "assetId": ASSET_ID,
            "evidenceRevisionId": REVISION_ID,
            "evidenceManifestSha256": MANIFEST_SHA256,
        },
        "graph": {"name": "EventGraph", "graphRef": GRAPH_REF},
        "nodes": copy.deepcopy(nodes if nodes is not None else [_request_node()]),
        "maxNodes": 12,
    }
    attach_semantic_digest(value)
    return value


class _Ledger:
    def __init__(self) -> None:
        self.forbidden_calls: list[str] = []
        self.object_iterator_calls: list[str] = []
        self.find_calls: list[tuple[object, ...]] = []
        self.load_calls: list[tuple[object, ...]] = []

    def forbidden(self, name: str) -> None:
        self.forbidden_calls.append(name)
        raise AssertionError(f"forbidden API was called: {name}")


class _ClassName:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


class _Guid:
    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


class _Graph:
    def __init__(self, path: str = GRAPH_PATH) -> None:
        self._path = path

    def get_name(self) -> str:
        return "EventGraph"

    def get_path_name(self) -> str:
        return self._path

    def get_class(self) -> _ClassName:
        return _ClassName("EdGraph")


class _NotGraph:
    def __init__(self, path: str = GRAPH_PATH) -> None:
        self._path = path

    def get_name(self) -> str:
        return "EventGraph"

    def get_path_name(self) -> str:
        return self._path

    def get_class(self) -> _ClassName:
        return _ClassName("NotEdGraph")


class _Vector:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


class _PinType:
    def __init__(self, category: str, subcategory: str) -> None:
        self.pin_category = category
        self.pin_sub_category = subcategory


class _Pin:
    def __init__(
        self,
        name: str,
        direction: str,
        *,
        category: str = "",
        subcategory: str = "",
        default: str = "",
    ) -> None:
        self.pin_name = name
        self.direction = direction
        self.pin_type = _PinType(category, subcategory)
        self.default_value = default

    def get_name(self) -> str:
        return self.pin_name

    def get_editor_property(self, name: str) -> object:
        if hasattr(self, name):
            return getattr(self, name)
        raise AttributeError(name)


class _Node:
    def __init__(
        self,
        ledger: _Ledger,
        *,
        name: str = OBJECT_NAME,
        class_name: str = EXPECTED_CLASS,
        guid: str = GUID,
        outer: object,
        guid_style: str = "snake",
        position_style: str = "attributes",
        x: int = 120,
        y: int = 240,
        pins: list[_Pin] | None = None,
    ) -> None:
        self._ledger = ledger
        self._name = name
        self._class_name = class_name
        self._outer = outer
        self._properties: dict[str, object] = {}
        self._pins_data = list(pins or [])
        if guid_style == "snake":
            self.node_guid = _Guid(guid)
        elif guid_style == "camel":
            self.NodeGuid = _Guid(guid)
        elif guid_style == "property":
            self._properties["node_guid"] = _Guid(guid)
        if position_style == "attributes":
            self.node_pos_x = x
            self.node_pos_y = y
        elif position_style == "property":
            self._properties["node_pos_x"] = x
            self._properties["node_pos_y"] = y

    def get_name(self) -> str:
        return self._name

    def get_path_name(self) -> str:
        return f"{GRAPH_PATH}.{self._name}"

    def get_class(self) -> _ClassName:
        return _ClassName(self._class_name)

    def get_outer(self) -> object:
        return self._outer

    def get_typed_outer(self, expected: type[object]) -> object | None:
        return self._outer if isinstance(self._outer, expected) else None

    def get_editor_property(self, name: str) -> object:
        if name in self._properties:
            return self._properties[name]
        if hasattr(self, name):
            return getattr(self, name)
        raise AttributeError(name)

    def set_editor_property(self, _name: str, _value: object) -> None:
        self._ledger.forbidden("set_editor_property")

    def modify(self) -> None:
        self._ledger.forbidden("modify")


class _AllPinsNode(_Node):
    def list_all_pins(self) -> list[_Pin]:
        return list(self._pins_data)


class _SplitPinsNode(_Node):
    def list_input_pins(self) -> list[_Pin]:
        return [pin for pin in self._pins_data if "Input" in pin.direction]

    def list_output_pins(self) -> list[_Pin]:
        return [pin for pin in self._pins_data if "Output" in pin.direction]


class _AttributePinsNode(_Node):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.pins = list(self._pins_data)


class _Blueprint:
    def __init__(self, graph: _Graph | None, path: str = OBJECT_PATH) -> None:
        self.graph = graph
        self._path = path

    def get_name(self) -> str:
        return "BP_NodeBinding"

    def get_path_name(self) -> str:
        return self._path


class _BlueprintEditorLibrary:
    def __init__(
        self,
        ledger: _Ledger,
        *,
        position_mode: str = "ok",
        pin_mode: str = "ok",
    ) -> None:
        self._ledger = ledger
        self._position_mode = position_mode
        self._pin_mode = pin_mode
        if position_mode == "missing":
            self.get_node_pos = None  # type: ignore[assignment]
        if pin_mode == "missing":
            self.list_all_pins = None  # type: ignore[assignment]

    @staticmethod
    def get_blueprint_asset(value: object) -> object:
        return value

    @staticmethod
    def find_graph(blueprint: _Blueprint, name: str) -> _Graph | None:
        if name != "EventGraph":
            return None
        return blueprint.graph

    @staticmethod
    def find_event_graph(blueprint: _Blueprint) -> _Graph | None:
        return blueprint.graph

    @staticmethod
    def get_all_graphs(blueprint: _Blueprint) -> list[_Graph]:
        return [blueprint.graph] if blueprint.graph is not None else []

    def get_node_pos(self, node: _Node) -> _Vector:
        if self._position_mode == "error":
            raise RuntimeError("position API failed")
        return _Vector(int(node.node_pos_x), int(node.node_pos_y))

    def list_all_pins(self, node: _Node) -> list[_Pin]:
        if self._pin_mode == "error":
            raise RuntimeError("pin API failed")
        return list(node._pins_data)

    def set_node_pos(self, *_args: object) -> None:
        self._ledger.forbidden("set_node_pos")

    def compile_blueprint(self, *_args: object) -> None:
        self._ledger.forbidden("compile_blueprint")

    def create_connection(self, *_args: object) -> None:
        self._ledger.forbidden("create_connection")

    def break_pin_links(self, *_args: object) -> None:
        self._ledger.forbidden("break_pin_links")


class _EditorAssetLibrary:
    def __init__(
        self,
        ledger: _Ledger,
        blueprint: _Blueprint | None,
    ) -> None:
        self._ledger = ledger
        self._blueprint = blueprint

    def load_asset(self, _path: str) -> _Blueprint | None:
        return self._blueprint

    def save_asset(self, *_args: object) -> None:
        self._ledger.forbidden("save_asset")


class _AssetEditorSubsystem:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    def get_all_edited_assets(self) -> list[object]:
        return []

    def open_editor_for_assets(self, *_args: object) -> None:
        self._ledger.forbidden("open_editor_for_assets")


class _BlueprintGraphEditor:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    def create_connection(self, *_args: object) -> None:
        self._ledger.forbidden("graph_editor.create_connection")


class _BlueprintGraphPinLibrary:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    def create_connection(self, *_args: object) -> None:
        self._ledger.forbidden("pin_library.create_connection")

    def break_pin_links(self, *_args: object) -> None:
        self._ledger.forbidden("pin_library.break_pin_links")

    def set_default_value(self, *_args: object) -> None:
        self._ledger.forbidden("pin_library.set_default_value")


class _SystemLibrary:
    @staticmethod
    def get_engine_version() -> str:
        return "5.5.4-0+UE5"


class _FakeUnreal:
    EdGraph = _Graph
    EdGraphNode = _Node
    K2Node = _Node
    SystemLibrary = _SystemLibrary

    def __init__(
        self,
        blueprint: _Blueprint | None,
        *,
        find_nodes: dict[str, _Node | None] | None = None,
        load_nodes: dict[str, _Node | None] | None = None,
        typed_lookup: bool = True,
        position_mode: str = "ok",
        pin_mode: str = "ok",
    ) -> None:
        self.ledger = _Ledger()
        self._blueprint = blueprint
        self._find_nodes = dict(find_nodes or {})
        self._load_nodes = dict(load_nodes or {})
        self._typed_lookup = typed_lookup
        self.BlueprintEditorLibrary = _BlueprintEditorLibrary(
            self.ledger,
            position_mode=position_mode,
            pin_mode=pin_mode,
        )
        self.EditorAssetLibrary = _EditorAssetLibrary(self.ledger, blueprint)
        self.AssetEditorSubsystem = _AssetEditorSubsystem(self.ledger)
        self.BlueprintGraphEditor = _BlueprintGraphEditor(self.ledger)
        self.BlueprintGraphPinLibrary = _BlueprintGraphPinLibrary(self.ledger)

    def load_asset(self, path: str) -> _Blueprint | None:
        return self._blueprint if path == OBJECT_PATH else None

    def find_object(self, *args: object) -> _Node | None:
        self.ledger.find_calls.append(args)
        if len(args) == 3 and not self._typed_lookup:
            raise TypeError("typed find_object is unsupported")
        return self._find_nodes.get(str(args[1]))

    def load_object(self, *args: object) -> _Node | None:
        self.ledger.load_calls.append(args)
        if len(args) == 3 and not self._typed_lookup:
            raise TypeError("typed load_object is unsupported")
        return self._load_nodes.get(str(args[1]))

    def ObjectIterator(self, class_type: object):  # noqa: N802
        self.ledger.object_iterator_calls.append(str(class_type))
        raise AssertionError("ObjectIterator must never be called")

    def new_object(self, *_args: object) -> object:
        self.ledger.forbidden("new_object")
        return object()

    def save_asset(self, *_args: object) -> None:
        self.ledger.forbidden("unreal.save_asset")

    def get_editor_subsystem(self, _class: object) -> _AssetEditorSubsystem:
        return self.AssetEditorSubsystem


def _assert_no_forbidden_calls(test: unittest.TestCase, unreal: _FakeUnreal) -> None:
    test.assertEqual(unreal.ledger.object_iterator_calls, [])
    test.assertEqual(unreal.ledger.forbidden_calls, [])


class RequestBuilderContractTests(unittest.TestCase):
    def test_builder_uses_only_current_fresh_evidence_locators(self) -> None:
        pins = [
            _pin_signature("execute", "EGPD_Input", 0, category="exec"),
            _pin_signature("Condition", "EGPD_Input", 1, category="bool"),
        ]
        first_ref = f"{GRAPH_REF}/n/101"
        second_ref = f"{GRAPH_REF}/n/202"
        locators = [
            _locator(node_ref=first_ref, pins=pins),
            _locator(
                node_ref=second_ref,
                object_name="K2Node_CallFunction_2",
                class_name="K2Node_CallFunction",
                node_guid="22222222222222222222222222222222",
                x=300,
                y=400,
                pins=pins,
            ),
        ]
        source = _LocatorSource(locators=locators)

        reverse = build_request(
            asset=OBJECT_PATH,
            graph_ref=GRAPH_REF,
            node_refs=[second_ref, first_ref],
            locator_source=source,
        )
        forward = build_request(
            asset=OBJECT_PATH,
            graph_ref=GRAPH_REF,
            node_refs=[first_ref, second_ref],
            locator_source=source,
        )

        self.assertEqual(reverse["schema"], REQUEST_SCHEMA)
        self.assertEqual(reverse["semanticDigest"], forward["semanticDigest"])
        self.assertEqual(reverse["nodes"], forward["nodes"])
        self.assertEqual(
            [node["nodeRef"] for node in reverse["nodes"]],
            [first_ref, second_ref],
        )
        self.assertEqual(reverse["asset"]["evidenceRevisionId"], REVISION_ID)
        self.assertEqual(
            reverse["asset"]["evidenceManifestSha256"],
            MANIFEST_SHA256,
        )
        self.assertEqual(reverse["graph"]["graphRef"], GRAPH_REF)
        self.assertEqual(reverse["nodes"][0]["objectName"], OBJECT_NAME)
        self.assertEqual(reverse["nodes"][0]["expectedClassName"], EXPECTED_CLASS)
        self.assertEqual(reverse["nodes"][0]["expectedNodeGuid"], GUID)
        self.assertEqual(reverse["nodes"][0]["evidenceX"], 120)
        self.assertEqual(reverse["nodes"][0]["evidencePins"], pins)
        self.assertNotIn("databasePath", reverse["asset"])
        self.assertNotIn("sourcePath", reverse["nodes"][0])
        self.assertEqual(source.asset_queries, [OBJECT_PATH, OBJECT_PATH])
        self.assertCountEqual(
            source.node_queries,
            [first_ref, second_ref, first_ref, second_ref],
        )
        validate_request(reverse)
        assert_path_free(reverse)

    def test_builder_rejects_stale_and_revision_mismatched_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "EVIDENCE_NOT_FRESH"):
            build_request(
                asset=OBJECT_PATH,
                graph_ref=GRAPH_REF,
                node_refs=[NODE_REF],
                locator_source=_LocatorSource(freshness="STALE"),
            )

        mismatched = _locator()
        mismatched["evidenceRevisionId"] = "fedcba9876543210fedcba98"
        with self.assertRaisesRegex(ValueError, "EVIDENCE_REVISION_MISMATCH"):
            build_request(
                asset=OBJECT_PATH,
                graph_ref=GRAPH_REF,
                node_refs=[NODE_REF],
                locator_source=_LocatorSource(locators=[mismatched]),
            )

    def test_builder_fails_the_whole_request_when_any_guid_is_unavailable(self) -> None:
        invalid_values = ("", "0" * 32, "not-a-guid", "F" * 31)
        for invalid in invalid_values:
            with self.subTest(node_guid=invalid):
                with self.assertRaisesRegex(ValueError, "NODE_GUID_NOT_AVAILABLE"):
                    build_request(
                        asset=OBJECT_PATH,
                        graph_ref=GRAPH_REF,
                        node_refs=[NODE_REF],
                        locator_source=_LocatorSource(
                            locators=[_locator(node_guid=invalid)]
                        ),
                    )

    def test_builder_rejects_cross_graph_nodes_duplicates_and_thirteen_nodes(self) -> None:
        other_graph = f"bp://{ASSET_ID}@{REVISION_ID}/g/8"
        with self.assertRaisesRegex(ValueError, "NODE_GRAPH_MISMATCH"):
            build_request(
                asset=OBJECT_PATH,
                graph_ref=GRAPH_REF,
                node_refs=[NODE_REF],
                locator_source=_LocatorSource(
                    locators=[_locator(graph_ref=other_graph)]
                ),
            )

        with self.assertRaisesRegex(ValueError, "NODE_REF_DUPLICATE"):
            build_request(
                asset=OBJECT_PATH,
                graph_ref=GRAPH_REF,
                node_refs=[NODE_REF, NODE_REF],
                locator_source=_LocatorSource(),
            )

        locators = [
            _locator(
                node_ref=f"{GRAPH_REF}/n/{index}",
                object_name=f"K2Node_CallFunction_{index}",
                class_name="K2Node_CallFunction",
                node_guid=f"{index + 1:032X}",
            )
            for index in range(13)
        ]
        with self.assertRaisesRegex(ValueError, "NODE_LIMIT_EXCEEDED"):
            build_request(
                asset=OBJECT_PATH,
                graph_ref=GRAPH_REF,
                node_refs=[str(row["nodeRef"]) for row in locators],
                locator_source=_LocatorSource(locators=locators),
            )


class NodeBindingProbeTests(unittest.TestCase):
    def test_find_object_exact_binding_reads_position_and_signature_only_pins(self) -> None:
        graph = _Graph()
        evidence_pins = [
            _pin_signature(
                "execute",
                "EGPD_Input",
                0,
                category="evidence-category-is-not-identity",
            ),
            _pin_signature("then", "EGPD_Output", 1, category="exec"),
        ]
        live_pins = [
            _Pin("execute", "EGPD_Input", category="exec"),
            _Pin("then", "EGPD_Output", category="exec", default="stable"),
        ]
        unreal = _FakeUnreal(_Blueprint(graph), pin_mode="ok")
        node = _Node(
            unreal.ledger,
            outer=graph,
            guid="11111111-1111-1111-1111-111111111111",
            pins=live_pins,
        )
        unreal._find_nodes[OBJECT_NAME] = node
        request = _request([_request_node(pins=evidence_pins)])

        result = collect_node_binding_result(
            unreal,
            request,
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )

        self.assertEqual(result["schema"], RESULT_SCHEMA)
        self.assertEqual(result["requestSemanticDigest"], request["semanticDigest"])
        self.assertEqual(result["assetStatus"], "EXACT")
        self.assertEqual(result["graphStatus"], "EXACT")
        self.assertEqual(result["route"], "EXACT_NODE_BINDING_PIN_PARTIAL")
        self.assertFalse(result["mutationReady"])
        self.assertEqual(result["summary"]["requested"], 1)
        self.assertEqual(result["summary"]["exact"], 1)
        self.assertEqual(result["summary"]["positionAvailable"], 1)
        self.assertEqual(result["summary"]["pinReadable"], 1)
        bound = result["nodes"][0]
        self.assertEqual(bound["nodeRef"], NODE_REF)
        self.assertEqual(bound["lookupMethod"], "FIND_OBJECT")
        self.assertEqual(bound["bindingStatus"], "EXACT")
        self.assertEqual(bound["actualClassName"], EXPECTED_CLASS)
        self.assertEqual(bound["actualNodeGuid"], GUID)
        self.assertEqual(bound["positionStatus"], "POSITION_AVAILABLE")
        self.assertEqual((bound["liveX"], bound["liveY"]), (120, 240))
        self.assertTrue(bound["positionMatches"])
        self.assertEqual(bound["pinRead"], "PASS")
        self.assertTrue(bound["pinSignatureMatches"])
        self.assertEqual(bound["pinBindingStatus"], "SIGNATURE_ONLY")
        self.assertEqual(bound["pins"][1]["default"], "stable")
        self.assertEqual(len(unreal.ledger.find_calls[0]), 3)
        self.assertIs(unreal.ledger.find_calls[0][0], graph)
        self.assertEqual(unreal.ledger.find_calls[0][1], OBJECT_NAME)
        self.assertIs(unreal.ledger.find_calls[0][2], _Node)
        self.assertEqual(unreal.ledger.load_calls, [])
        validate_result(result, request=request)
        assert_path_free(result)
        _assert_no_forbidden_calls(self, unreal)

    def test_load_object_is_only_the_missing_find_fallback_with_two_arg_runtime(self) -> None:
        graph = _Graph()
        unreal = _FakeUnreal(
            _Blueprint(graph),
            typed_lookup=False,
            position_mode="missing",
            pin_mode="missing",
        )
        node = _Node(
            unreal.ledger,
            outer=graph,
            guid_style="camel",
            position_style="property",
        )
        unreal._find_nodes[OBJECT_NAME] = None
        unreal._load_nodes[OBJECT_NAME] = node

        result = collect_node_binding_result(
            unreal,
            _request(),
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )

        bound = result["nodes"][0]
        self.assertEqual(bound["bindingStatus"], "EXACT")
        self.assertEqual(bound["lookupMethod"], "LOAD_OBJECT")
        self.assertEqual([len(call) for call in unreal.ledger.find_calls], [3, 2])
        self.assertEqual([len(call) for call in unreal.ledger.load_calls], [3, 2])
        self.assertEqual(bound["positionStatus"], "POSITION_AVAILABLE")
        self.assertEqual(bound["pinRead"], "UNAVAILABLE")
        self.assertEqual(result["route"], "EXACT_NODE_BINDING_PIN_UNAVAILABLE")
        _assert_no_forbidden_calls(self, unreal)

    def test_missing_edgraphnode_type_fails_closed_without_untyped_lookup(self) -> None:
        graph = _Graph()
        unreal = _FakeUnreal(_Blueprint(graph))
        unreal.EdGraphNode = None
        unreal._find_nodes[OBJECT_NAME] = _Node(unreal.ledger, outer=graph)

        result = collect_node_binding_result(
            unreal,
            _request(),
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )

        self.assertEqual(result["nodes"][0]["bindingStatus"], "NOT_FOUND")
        self.assertIn("ED_GRAPH_NODE_CLASS_UNAVAILABLE", result["gaps"])
        self.assertEqual(unreal.ledger.find_calls, [])
        self.assertEqual(unreal.ledger.load_calls, [])
        _assert_no_forbidden_calls(self, unreal)

    def test_outer_path_fallback_requires_edgraph_type_and_exact_full_path(self) -> None:
        cases = (
            (_Graph(GRAPH_PATH), "EXACT"),
            (_Graph(f"{OBJECT_PATH}:OtherGraph"), "OUTER_MISMATCH"),
            (_NotGraph(GRAPH_PATH), "OUTER_MISMATCH"),
        )
        for outer, expected in cases:
            with self.subTest(outer=type(outer).__name__, expected=expected):
                graph = _Graph()
                unreal = _FakeUnreal(
                    _Blueprint(graph),
                    position_mode="missing",
                    pin_mode="missing",
                )
                unreal._find_nodes[OBJECT_NAME] = _Node(
                    unreal.ledger,
                    outer=outer,
                    position_style="missing",
                )

                result = collect_node_binding_result(
                    unreal,
                    _request(),
                    generated_at="2026-08-12T00:00:00Z",
                    python_version="3.11.8",
                )

                self.assertEqual(result["nodes"][0]["bindingStatus"], expected)
                if expected == "EXACT":
                    self.assertEqual(result["summary"]["outerMismatch"], 0)
                else:
                    self.assertEqual(result["summary"]["outerMismatch"], 1)
                    self.assertEqual(result["route"], "NODE_BINDING_UNAVAILABLE")
                _assert_no_forbidden_calls(self, unreal)

    def test_class_guid_and_zero_guid_mismatches_never_become_exact(self) -> None:
        cases = (
            ("WrongRuntimeClass", GUID, "snake", "CLASS_MISMATCH"),
            (EXPECTED_CLASS, "2" * 32, "snake", "GUID_MISMATCH"),
            (EXPECTED_CLASS, "0" * 32, "property", "GUID_MISMATCH"),
            (EXPECTED_CLASS, "", "missing", "GUID_MISMATCH"),
        )
        for actual_class, actual_guid, guid_style, expected in cases:
            with self.subTest(expected=expected, guid_style=guid_style):
                graph = _Graph()
                unreal = _FakeUnreal(
                    _Blueprint(graph),
                    position_mode="missing",
                    pin_mode="missing",
                )
                unreal._find_nodes[OBJECT_NAME] = _Node(
                    unreal.ledger,
                    outer=graph,
                    class_name=actual_class,
                    guid=actual_guid,
                    guid_style=guid_style,
                    position_style="missing",
                )

                result = collect_node_binding_result(
                    unreal,
                    _request(),
                    generated_at="2026-08-12T00:00:00Z",
                    python_version="3.11.8",
                )

                self.assertEqual(result["nodes"][0]["bindingStatus"], expected)
                self.assertNotEqual(result["nodes"][0]["bindingStatus"], "EXACT")
                if expected == "CLASS_MISMATCH":
                    self.assertEqual(result["summary"]["classMismatch"], 1)
                else:
                    self.assertEqual(result["summary"]["guidMismatch"], 1)
                _assert_no_forbidden_calls(self, unreal)

    def test_position_property_fallback_distinguishes_error_and_unavailable(self) -> None:
        cases = (
            ("error", "property", "POSITION_AVAILABLE", (120, 240)),
            ("error", "missing", "POSITION_ERROR", None),
            ("missing", "missing", "POSITION_UNAVAILABLE", None),
        )
        for api_mode, node_mode, expected, live in cases:
            with self.subTest(api_mode=api_mode, node_mode=node_mode):
                graph = _Graph()
                unreal = _FakeUnreal(
                    _Blueprint(graph),
                    position_mode=api_mode,
                    pin_mode="missing",
                )
                unreal._find_nodes[OBJECT_NAME] = _Node(
                    unreal.ledger,
                    outer=graph,
                    guid_style="property",
                    position_style=node_mode,
                )

                result = collect_node_binding_result(
                    unreal,
                    _request(),
                    generated_at="2026-08-12T00:00:00Z",
                    python_version="3.11.8",
                )

                bound = result["nodes"][0]
                self.assertEqual(bound["bindingStatus"], "EXACT")
                self.assertEqual(bound["positionStatus"], expected)
                if live is not None:
                    self.assertEqual((bound["liveX"], bound["liveY"]), live)
                else:
                    self.assertNotIn("liveX", bound)
                    self.assertNotIn("liveY", bound)
                _assert_no_forbidden_calls(self, unreal)

    def test_pin_instance_methods_attributes_and_unavailable_routes_are_honest(self) -> None:
        live_pins = [
            _Pin("execute", "EGPD_Input", category="exec"),
            _Pin("then", "EGPD_Output", category="exec"),
        ]
        evidence_pins = [
            _pin_signature("execute", "EGPD_Input", 0),
            _pin_signature("then", "EGPD_Output", 1),
        ]
        cases: tuple[tuple[type[_Node], str, str, str], ...] = (
            (_AllPinsNode, "error", "PASS", "SIGNATURE_ONLY"),
            (_SplitPinsNode, "missing", "PASS", "SIGNATURE_ONLY"),
            (_AttributePinsNode, "missing", "PASS", "SIGNATURE_ONLY"),
            (_Node, "missing", "UNAVAILABLE", "UNAVAILABLE"),
        )
        for node_type, library_mode, pin_read, binding in cases:
            with self.subTest(node_type=node_type.__name__):
                graph = _Graph()
                unreal = _FakeUnreal(
                    _Blueprint(graph),
                    position_mode="missing",
                    pin_mode=library_mode,
                )
                unreal._find_nodes[OBJECT_NAME] = node_type(
                    unreal.ledger,
                    outer=graph,
                    position_style="missing",
                    pins=live_pins,
                )

                result = collect_node_binding_result(
                    unreal,
                    _request([_request_node(pins=evidence_pins)]),
                    generated_at="2026-08-12T00:00:00Z",
                    python_version="3.11.8",
                )

                bound = result["nodes"][0]
                self.assertEqual(bound["pinRead"], pin_read)
                self.assertEqual(bound["pinBindingStatus"], binding)
                expected_route = (
                    "EXACT_NODE_BINDING_PIN_PARTIAL"
                    if pin_read == "PASS"
                    else "EXACT_NODE_BINDING_PIN_UNAVAILABLE"
                )
                self.assertEqual(result["route"], expected_route)
                _assert_no_forbidden_calls(self, unreal)

    def test_complete_pin_signature_mismatch_is_compared_but_not_exact_pin_binding(self) -> None:
        graph = _Graph()
        unreal = _FakeUnreal(
            _Blueprint(graph),
            position_mode="missing",
            pin_mode="ok",
        )
        unreal._find_nodes[OBJECT_NAME] = _Node(
            unreal.ledger,
            outer=graph,
            position_style="missing",
            pins=[_Pin("then", "EGPD_Output", category="exec")],
        )
        request = _request(
            [_request_node(pins=[_pin_signature("execute", "EGPD_Input", 0)])]
        )

        result = collect_node_binding_result(
            unreal,
            request,
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )

        bound = result["nodes"][0]
        self.assertEqual(bound["bindingStatus"], "EXACT")
        self.assertEqual(bound["pinRead"], "PASS")
        self.assertEqual(bound["pinBindingStatus"], "SIGNATURE_ONLY")
        self.assertFalse(bound["pinSignatureMatches"])
        self.assertEqual(result["route"], "EXACT_NODE_BINDING_PIN_PARTIAL")
        validate_result(result, request=request)
        _assert_no_forbidden_calls(self, unreal)

    def test_pin_caps_are_sixty_four_per_node_and_five_hundred_twelve_total(self) -> None:
        graph = _Graph()
        unreal = _FakeUnreal(
            _Blueprint(graph),
            position_mode="missing",
            pin_mode="missing",
        )
        request_nodes: list[dict[str, object]] = []
        for node_index in range(9):
            node_ref = f"{GRAPH_REF}/n/{node_index + 1}"
            object_name = f"K2Node_CallFunction_{node_index}"
            guid = f"{node_index + 1:032X}"
            live_pins = [
                _Pin(f"Pin{pin_index:03d}", "EGPD_Input", category="int")
                for pin_index in range(80)
            ]
            evidence_pins = [
                _pin_signature(f"Pin{pin_index:03d}", "EGPD_Input", pin_index)
                for pin_index in range(80)
            ]
            unreal._find_nodes[object_name] = _AttributePinsNode(
                unreal.ledger,
                name=object_name,
                class_name="K2Node_CallFunction",
                guid=guid,
                outer=graph,
                position_style="missing",
                pins=live_pins,
            )
            request_nodes.append(
                _request_node(
                    node_ref=node_ref,
                    object_name=object_name,
                    expected_class="K2Node_CallFunction",
                    expected_guid=guid,
                    x=None,
                    y=None,
                    pins=evidence_pins,
                )
            )

        result = collect_node_binding_result(
            unreal,
            _request(request_nodes),
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )

        returned = [len(node["pins"]) for node in result["nodes"]]
        self.assertTrue(all(count <= 64 for count in returned))
        self.assertEqual(sum(returned), 512)
        self.assertIn("PIN_PER_NODE_LIMIT_REACHED", result["gaps"])
        self.assertIn("PIN_TOTAL_LIMIT_REACHED", result["gaps"])
        self.assertEqual(result["route"], "EXACT_NODE_BINDING_PIN_PARTIAL")
        self.assertTrue(all(node["bindingStatus"] == "EXACT" for node in result["nodes"]))
        _assert_no_forbidden_calls(self, unreal)

    def test_result_digest_is_deterministic_path_free_and_bound_to_request(self) -> None:
        graph = _Graph()
        unreal = _FakeUnreal(
            _Blueprint(graph),
            position_mode="missing",
            pin_mode="missing",
        )
        unreal._find_nodes[OBJECT_NAME] = _Node(
            unreal.ledger,
            outer=graph,
            position_style="missing",
        )
        request = _request()

        first = collect_node_binding_result(
            unreal,
            request,
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )
        second = collect_node_binding_result(
            unreal,
            request,
            generated_at="2026-08-12T00:01:00Z",
            python_version="3.11.8",
        )

        self.assertNotEqual(first["generatedAt"], second["generatedAt"])
        self.assertEqual(first["semanticDigest"], second["semanticDigest"])
        self.assertEqual(first["requestSemanticDigest"], request["semanticDigest"])
        validate_result(first, request=request)
        assert_path_free(first)
        _assert_no_forbidden_calls(self, unreal)

    def test_missing_asset_or_graph_routes_to_node_binding_unavailable(self) -> None:
        missing_asset = _FakeUnreal(None)
        asset_result = collect_node_binding_result(
            missing_asset,
            _request(),
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )
        self.assertEqual(asset_result["assetStatus"], "NOT_FOUND")
        self.assertEqual(asset_result["route"], "NODE_BINDING_UNAVAILABLE")
        self.assertEqual(asset_result["summary"]["exact"], 0)
        _assert_no_forbidden_calls(self, missing_asset)

        missing_graph = _FakeUnreal(_Blueprint(None))
        graph_result = collect_node_binding_result(
            missing_graph,
            _request(),
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )
        self.assertEqual(graph_result["assetStatus"], "EXACT")
        self.assertEqual(graph_result["graphStatus"], "NOT_FOUND")
        self.assertEqual(graph_result["route"], "NODE_BINDING_UNAVAILABLE")
        _assert_no_forbidden_calls(self, missing_graph)


class NodeBindingValidationTests(unittest.TestCase):
    def test_request_validator_rejects_invalid_bounds_duplicate_nodes_and_paths(self) -> None:
        empty = _request([])
        with self.assertRaises(ValueError):
            validate_request(empty)

        duplicate = _request([_request_node(), _request_node()])
        with self.assertRaises(ValueError):
            validate_request(duplicate)

        wrong_graph = _request(
            [
                _request_node(
                    node_ref=f"bp://{ASSET_ID}@{REVISION_ID}/g/8/n/101"
                )
            ]
        )
        with self.assertRaises(ValueError):
            validate_request(wrong_graph)

        leaked = _request()
        leaked["asset"]["localPath"] = _windows_test_path(
            "Users", "probe", "asset.uasset"
        )
        attach_semantic_digest(leaked)
        with self.assertRaises(ValueError):
            validate_request(leaked)

    def test_result_validator_rejects_request_digest_node_set_and_path_leaks(self) -> None:
        graph = _Graph()
        unreal = _FakeUnreal(
            _Blueprint(graph),
            position_mode="missing",
            pin_mode="missing",
        )
        unreal._find_nodes[OBJECT_NAME] = _Node(
            unreal.ledger,
            outer=graph,
            position_style="missing",
        )
        request = _request()
        result = collect_node_binding_result(
            unreal,
            request,
            generated_at="2026-08-12T00:00:00Z",
            python_version="3.11.8",
        )
        validate_result(result, request=request)

        wrong_digest = copy.deepcopy(result)
        wrong_digest["requestSemanticDigest"] = "b" * 64
        attach_semantic_digest(wrong_digest)
        with self.assertRaises(ValueError):
            validate_result(wrong_digest, request=request)

        wrong_node = copy.deepcopy(result)
        wrong_node["nodes"][0]["nodeRef"] = f"{GRAPH_REF}/n/not-requested"
        attach_semantic_digest(wrong_node)
        with self.assertRaises(ValueError):
            validate_result(wrong_node, request=request)

        leaked = copy.deepcopy(result)
        leaked["runtime"]["logPath"] = _windows_test_path(
            "Users", "probe", "result.log"
        )
        attach_semantic_digest(leaked)
        with self.assertRaises(ValueError):
            validate_result(leaked, request=request)


if __name__ == "__main__":
    unittest.main()
