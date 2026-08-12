from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_scripting_probe.wc_graph_snapshot import (  # noqa: E402
    REQUEST_SCHEMA,
    RESULT_SCHEMA,
    SNAPSHOT_SCHEMA,
    build_wc_graph_snapshot_result,
    collect_wc_graph_snapshot,
    validate_wc_graph_snapshot,
    validate_wc_graph_snapshot_result,
    validate_wc_snapshot_request,
    validator_summary,
)
from build_arkdev_wc_graph_snapshot_request import build_request  # noqa: E402
from devkit_exporters.arkdev_wc_properties import (  # noqa: E402
    wc_get_all_property_names,
    wc_get_all_property_values,
)


class _Name:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


class _ClassObject:
    def __init__(self, path: str) -> None:
        self.path = path

    def get_name(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    def get_path_name(self) -> str:
        return self.path


class _EdGraph:
    pass


class _EdGraphNode:
    pass


class _K2Node(_EdGraphNode):
    pass


class _Pin:
    def __init__(
        self,
        owner: _Node,
        *,
        index: int,
        with_pin_id: bool,
    ) -> None:
        self.owner = owner
        self.values: dict[str, object] = {
            "PinId": f"{index + 101:032X}" if with_pin_id else "0" * 32,
            "PersistentGuid": f"{index + 201:032X}",
            "PinName": f"Pin{index}",
            "Direction": "EGPD_Output",
            "PinType": "exec",
            "DefaultValue": "",
            "DefaultObject": None,
            "DefaultTextValue": "",
            "LinkedTo": [],
        }

    def wc_get_property_value(self, name: object) -> object:
        key = str(name)
        if key not in self.values:
            raise AttributeError(key)
        return self.values[key]

    def get_outer(self) -> _Node:
        return self.owner


class _Node(_K2Node):
    def __init__(
        self,
        graph: _Graph,
        *,
        index: int,
        valid_guid: bool = True,
        with_pin_id: bool = True,
    ) -> None:
        self.graph = graph
        self.index = index
        self.values: dict[str, object] = {
            "NodeGuid": f"{index + 1:032X}" if valid_guid else "0" * 32,
            "NodePosX": index * 100,
            "NodePosY": index * 20,
        }
        self.pin = _Pin(self, index=index, with_pin_id=with_pin_id)
        self.values["Pins"] = [self.pin]

    def get_name(self) -> str:
        return f"Node{self.index}"

    def get_path_name(self) -> str:
        return f"/Game/Test/BP_WC.BP_WC:EventGraph.Node{self.index}"

    def get_class(self) -> _ClassObject:
        return _ClassObject("/Script/BlueprintGraph.K2Node_CallFunction")

    def get_outer(self) -> _Graph:
        return self.graph

    def wc_get_property_value(self, name: object) -> object:
        key = str(name)
        if key not in self.values:
            raise AttributeError(key)
        return self.values[key]


class _Graph(_EdGraph):
    def __init__(self) -> None:
        self.nodes: list[_Node] = []

    def get_name(self) -> str:
        return "EventGraph"

    def get_path_name(self) -> str:
        return "/Game/Test/BP_WC.BP_WC:EventGraph"

    def get_class(self) -> _ClassObject:
        return _ClassObject("/Script/Engine.EdGraph")

    def wc_get_property_value(self, name: object) -> object:
        if isinstance(name, str):
            raise TypeError("this fake exercises unreal.Name fallback")
        if str(name) == "Nodes":
            return self.nodes
        raise AttributeError(str(name))

    def wc_get_all_property_names(self) -> list[str]:
        return ["Nodes", "Schema"]


class _Blueprint:
    def __init__(self, graph: _Graph) -> None:
        self.graph = graph

    def get_name(self) -> str:
        return "BP_WC"

    def get_path_name(self) -> str:
        return "/Game/Test/BP_WC.BP_WC"

    def get_class(self) -> _ClassObject:
        return _ClassObject("/Script/Engine.Blueprint")


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
    def set_node_pos(cls, *_args: object) -> None:
        cls.mutation_calls.append("set_node_pos")
        raise AssertionError("mutation API was called")


class _SystemLibrary:
    @staticmethod
    def get_engine_version() -> str:
        return "5.5.1-ARKDEV-TEST"


class _FakeUnreal:
    Name = _Name
    EdGraph = _EdGraph
    EdGraphNode = _EdGraphNode
    K2Node = _K2Node
    BlueprintEditorLibrary = _BlueprintEditorLibrary
    SystemLibrary = _SystemLibrary
    asset: _Blueprint | None = None
    mutation_calls: list[str] = []
    object_iterator_calls = 0

    @classmethod
    def load_asset(cls, _object_path: str) -> _Blueprint | None:
        return cls.asset

    @classmethod
    def ObjectIterator(cls, *_args: object) -> object:  # noqa: N802
        cls.object_iterator_calls += 1
        raise AssertionError("ObjectIterator was called")

    @classmethod
    def save_asset(cls, *_args: object) -> None:
        cls.mutation_calls.append("save_asset")
        raise AssertionError("mutation API was called")


def _make_blueprint(
    node_count: int,
    *,
    invalid_guid_indexes: set[int] | None = None,
    with_pin_id: bool = True,
) -> _Blueprint:
    graph = _Graph()
    invalid = invalid_guid_indexes or set()
    graph.nodes = [
        _Node(
            graph,
            index=index,
            valid_guid=index not in invalid,
            with_pin_id=with_pin_id,
        )
        for index in range(node_count)
    ]
    for index, node in enumerate(graph.nodes[:-1]):
        node.pin.values["LinkedTo"] = [graph.nodes[index + 1].pin]
    return _Blueprint(graph)


class ArkDevWcGraphSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeUnreal.asset = None
        _FakeUnreal.mutation_calls.clear()
        _FakeUnreal.object_iterator_calls = 0
        _BlueprintEditorLibrary.mutation_calls.clear()
        self.request = build_request(
            asset_object_path="/Game/Test/BP_WC.BP_WC",
            graph_name="EventGraph",
            max_nodes=12,
            max_pins_per_node=8,
        )

    def test_pass_snapshot_is_exact_bounded_deterministic_and_read_only(self) -> None:
        _FakeUnreal.asset = _make_blueprint(5)

        first = build_wc_graph_snapshot_result(
            _FakeUnreal,
            self.request,
            generated_at="2026-08-12T00:00:00Z",
        )
        second = build_wc_graph_snapshot_result(
            _FakeUnreal,
            self.request,
            generated_at="2026-08-12T00:00:00Z",
        )

        self.assertEqual(first, second)
        self.assertEqual(first["schema"], RESULT_SCHEMA)
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["arkGraphIdentitySource"], "WC_REFLECTION")
        self.assertTrue(first["readyForWcEvidenceAdapter"])
        self.assertFalse(first["readyToRetryPr45"])
        snapshot = first["snapshot"]
        assert isinstance(snapshot, dict)
        self.assertEqual(snapshot["schema"], SNAPSHOT_SCHEMA)
        self.assertEqual(snapshot["source"], "ARKDEV_WC_REFLECTION")
        self.assertEqual(snapshot["counts"]["nodeCountReturned"], 5)
        self.assertEqual(snapshot["counts"]["identityLocatorCount"], 5)
        self.assertEqual(len(snapshot["identityLocators"]), 5)
        self.assertEqual(snapshot["coverage"]["wcNodeGuidPercent"], 100.0)
        self.assertEqual(snapshot["coverage"]["nodePositionPercent"], 100.0)
        self.assertEqual(snapshot["pinIdentity"], "PIN_IDENTITY_PASS")
        self.assertEqual(snapshot["counts"]["pinIdObserved"], 5)
        self.assertEqual(snapshot["counts"]["nativePinIdRecovered"], 5)
        self.assertIn("pinId", snapshot["nodes"][0]["pins"][0])
        self.assertEqual(
            snapshot["nodes"][0]["canonicalClassName"],
            "K2Node_CallFunction",
        )
        self.assertEqual(
            snapshot["nodes"][0]["readMethods"]["graphNodes"],
            "wc_get_property_value:Name",
        )
        validate_wc_graph_snapshot(snapshot)
        validate_wc_graph_snapshot_result(first)
        self.assertEqual(validator_summary(first)["WC_NODES_READ"], "PASS")
        self.assertEqual(_FakeUnreal.mutation_calls, [])
        self.assertEqual(_BlueprintEditorLibrary.mutation_calls, [])
        self.assertEqual(_FakeUnreal.object_iterator_calls, 0)

    def test_node_pass_allows_signature_only_pins_without_pr45_retry(self) -> None:
        _FakeUnreal.asset = _make_blueprint(5, with_pin_id=False)

        result = build_wc_graph_snapshot_result(
            _FakeUnreal,
            self.request,
            generated_at="2026-08-12T00:00:00Z",
        )

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["readyForWcEvidenceAdapter"])
        self.assertFalse(result["readyToRetryPr45"])
        snapshot = result["snapshot"]
        assert isinstance(snapshot, dict)
        self.assertEqual(snapshot["pinIdentity"], "PIN_SIGNATURE_ONLY")
        self.assertEqual(snapshot["counts"]["nativePinIdRecovered"], 0)
        validate_wc_graph_snapshot_result(result)

    def test_sub_ninety_percent_wc_guid_coverage_fails_closed(self) -> None:
        _FakeUnreal.asset = _make_blueprint(5, invalid_guid_indexes={4})

        result = build_wc_graph_snapshot_result(
            _FakeUnreal,
            self.request,
            generated_at="2026-08-12T00:00:00Z",
        )

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["arkGraphIdentitySource"], "CLIPBOARD_REQUIRED")
        self.assertFalse(result["readyForWcEvidenceAdapter"])
        self.assertEqual(result["gate"]["wcNodeGuidRead"], "PARTIAL")
        self.assertEqual(result["gate"]["nodeIdentity"], "UNAVAILABLE")
        validate_wc_graph_snapshot_result(result)

    def test_missing_explicit_asset_is_a_valid_fail_closed_result(self) -> None:
        result = build_wc_graph_snapshot_result(
            _FakeUnreal,
            self.request,
            generated_at="2026-08-12T00:00:00Z",
        )

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["arkGraphIdentitySource"], "CLIPBOARD_REQUIRED")
        snapshot = result["snapshot"]
        assert isinstance(snapshot, dict)
        self.assertEqual(
            snapshot["capabilityMatrix"]["explicitAssetLoad"],
            "UNAVAILABLE",
        )
        self.assertIn("EXPLICIT_ASSET_LOAD_UNAVAILABLE", snapshot["gaps"])
        validate_wc_graph_snapshot_result(result)

    def test_node_cap_is_hard_and_truncation_cannot_pass(self) -> None:
        _FakeUnreal.asset = _make_blueprint(13)

        snapshot = collect_wc_graph_snapshot(_FakeUnreal, self.request)
        result = build_wc_graph_snapshot_result(
            _FakeUnreal,
            self.request,
            generated_at="2026-08-12T00:00:00Z",
        )

        self.assertEqual(len(snapshot["nodes"]), 12)
        self.assertEqual(len(snapshot["identityLocators"]), 12)
        self.assertEqual(snapshot["counts"]["nodesOmitted"], 1)
        self.assertIn("NODE_LIMIT_REACHED", snapshot["gaps"])
        self.assertEqual(result["status"], "FAIL")
        validate_wc_graph_snapshot_result(result)

    def test_request_contract_rejects_unbounded_or_extra_input(self) -> None:
        self.assertEqual(self.request["schema"], REQUEST_SCHEMA)
        unbounded = dict(self.request)
        unbounded["maxNodes"] = 501
        with self.assertRaises(ValueError):
            validate_wc_snapshot_request(unbounded)
        extra = dict(self.request)
        extra["activeAsset"] = True
        with self.assertRaises(ValueError):
            validate_wc_snapshot_request(extra)

    def test_shared_wc_helpers_are_bounded_and_deterministic(self) -> None:
        class _Properties:
            def wc_get_all_property_names(self) -> list[str]:
                return ["Zed", "Alpha", "not valid", "Beta"]

            def wc_get_all_property_values(self) -> dict[str, int]:
                return {"Zed": 3, "Alpha": 1, "Beta": 2}

        owner = _Properties()
        self.assertEqual(
            wc_get_all_property_names(owner, limit=2),
            ["Alpha", "Beta"],
        )
        self.assertEqual(
            list(wc_get_all_property_values(owner, limit=2)),
            ["Alpha", "Beta"],
        )

    def test_result_digest_tampering_is_rejected(self) -> None:
        _FakeUnreal.asset = _make_blueprint(5)
        result = build_wc_graph_snapshot_result(
            _FakeUnreal,
            self.request,
            generated_at="2026-08-12T00:00:00Z",
        )
        result["readyForWcEvidenceAdapter"] = False
        with self.assertRaises(ValueError):
            validate_wc_graph_snapshot_result(result)

    def test_schema_files_are_valid_json_and_bind_contract_ids(self) -> None:
        snapshot_schema = json.loads(
            (ROOT / "schemas" / "arkdev_wc_graph_snapshot.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        result_schema = json.loads(
            (
                ROOT
                / "schemas"
                / "arkdev_wc_graph_snapshot_result.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(snapshot_schema["properties"]["schema"]["const"], SNAPSHOT_SCHEMA)
        self.assertEqual(result_schema["properties"]["schema"]["const"], RESULT_SCHEMA)


if __name__ == "__main__":
    unittest.main()
