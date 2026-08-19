from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.contracts import McpExecutionError  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    large_interpretation_payload,
    publish_interpretation_fixture,
)


def _assert_path_free(case: unittest.TestCase, value: object, forbidden: Path) -> None:
    encoded = json.dumps(value, ensure_ascii=False)
    case.assertNotIn(str(forbidden), encoded)
    case.assertNotIn(str(forbidden).replace("\\", "/"), encoded)
    case.assertNotRegex(encoded, r"(?i)(?<![A-Za-z0-9_])[a-z]:[\\/]")


class BlueprintServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.capture_root = Path(self._temporary.name) / "captures"
        self.asset_dir, self.source_path, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        publish_interpretation(self.asset_dir, budget=32_000)
        self.service = BlueprintService(self.capture_root)

    def context(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "asset": "InterpretationFixture",
            "goal": "ReceiveBeginPlay",
            "graph_ref": "",
            "seed_refs": (),
            "max_hops": 1,
            "max_nodes": 40,
            "max_pins": 160,
            "max_edges": 160,
            "budget_tokens": 2400,
            "continuation": "",
        }
        arguments.update(overrides)
        return self.service.get_context(**arguments)

    def test_single_term_goal_deduplicates_identical_default_search(self) -> None:
        class RecordingRepository:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[str, ...]]] = []

            def query(self, request: dict[str, object]) -> dict[str, object]:
                kinds = tuple(str(value) for value in request["kinds"])
                self.calls.append((str(request["query"]), kinds))
                return {"items": []}

        repository = RecordingRepository()

        BlueprintService._search_goal(  # noqa: SLF001 - bounded search contract
            repository,  # type: ignore[arg-type]
            "DefaultThreshold",
        )

        self.assertEqual(
            repository.calls,
            [
                ("DefaultThreshold", ("default",)),
                ("DefaultThreshold", ("graph", "node")),
            ],
        )

    def test_default_fact_missing_source_status_fails_closed(self) -> None:
        class StatuslessRepository:
            def query(self, _request: dict[str, object]) -> dict[str, object]:
                return {
                    "items": [
                        {
                            "ref": "bp://asset@revision/default/Value",
                            "kind": "default",
                            "name": "Value",
                            "typeName": "IntProperty",
                            "valueUsable": True,
                            "value": 6,
                        }
                    ]
                }

        facts = BlueprintService._default_facts(  # noqa: SLF001
            StatuslessRepository(),  # type: ignore[arg-type]
            [
                {
                    "ref": "bp://asset@revision/default/Value",
                    "kind": "default",
                    "name": "Value",
                }
            ],
            "Value",
        )

        self.assertEqual(facts[0]["sourceValueStatus"], "NOT_RECOVERED")
        self.assertEqual(facts[0]["status"], "NOT_RECOVERED")
        self.assertIs(facts[0]["valueUsable"], False)

    def test_default_fact_rejects_entity_from_a_different_evidence_ref(
        self,
    ) -> None:
        requested_ref = "bp://asset@revision/default/RequestedValue"
        returned_ref = "bp://asset@revision/default/DifferentValue"

        class MismatchedRepository:
            def query(self, _request: dict[str, object]) -> dict[str, object]:
                return {
                    "items": [
                        {
                            "ref": returned_ref,
                            "kind": "default",
                            "name": "DifferentValue",
                            "typeName": "IntProperty",
                            "valueStatus": "CONFIRMED",
                            "valueUsable": True,
                            "value": 99,
                        }
                    ]
                }

        with self.assertRaises(McpExecutionError):
            BlueprintService._default_facts(  # noqa: SLF001
                MismatchedRepository(),  # type: ignore[arg-type]
                [
                    {
                        "ref": requested_ref,
                        "kind": "default",
                        "name": "RequestedValue",
                    }
                ],
                "RequestedValue",
            )

    def test_default_fact_rejects_extra_entity_items(self) -> None:
        requested_ref = "bp://asset@revision/default/RequestedValue"

        class ExtraItemRepository:
            def query(self, _request: dict[str, object]) -> dict[str, object]:
                return {
                    "items": [
                        {
                            "ref": requested_ref,
                            "kind": "default",
                            "name": "RequestedValue",
                            "typeName": "IntProperty",
                            "valueStatus": "CONFIRMED",
                            "valueUsable": True,
                            "value": 7,
                        },
                        {
                            "ref": "bp://asset@revision/default/UnexpectedValue",
                            "kind": "default",
                            "name": "UnexpectedValue",
                            "typeName": "IntProperty",
                            "valueStatus": "CONFIRMED",
                            "valueUsable": True,
                            "value": 99,
                        },
                    ]
                }

        with self.assertRaises(McpExecutionError):
            BlueprintService._default_facts(  # noqa: SLF001
                ExtraItemRepository(),  # type: ignore[arg-type]
                [
                    {
                        "ref": requested_ref,
                        "kind": "default",
                        "name": "RequestedValue",
                    }
                ],
                "RequestedValue",
            )

    def test_default_fact_preserves_nested_resolved_object_value_path(self) -> None:
        ref = "bp://asset@revision/default/NestedObjects"

        class NestedObjectRepository:
            def query(self, _request: dict[str, object]) -> dict[str, object]:
                return {
                    "items": [
                        {
                            "ref": ref,
                            "kind": "default",
                            "name": "NestedObjects",
                            "typeName": "ArrayProperty",
                            "valueStatus": "CONFIRMED",
                            "valueUsable": True,
                            "value": [1],
                            "resolvedObjectFields": [
                                {
                                    "elementIndex": 0,
                                    "propertyIndex": 0,
                                    "propertyName": "DamageTypeEntryValuesOverrides",
                                    "name": "/Game/Test/Damage.Damage",
                                    "valuePath": [
                                        0,
                                        "DamageTypeEntryValuesOverrides",
                                        1,
                                    ],
                                }
                            ],
                            "resolvedObjectFieldCoverage": {
                                "available": 1,
                                "returned": 1,
                            },
                        }
                    ]
                }

        facts = BlueprintService._default_facts(  # noqa: SLF001
            NestedObjectRepository(),  # type: ignore[arg-type]
            [{"ref": ref, "kind": "default", "name": "NestedObjects"}],
            "NestedObjects",
        )

        field = facts[0]["resolvedObjectFields"][0]
        self.assertEqual(
            field["valuePath"],
            [0, "DamageTypeEntryValuesOverrides", 1],
        )
        self.assertEqual(field["objectPath"], "/Game/Test/Damage.Damage")
        self.assertIs(facts[0]["resolvedObjectFieldIdentityComplete"], True)

    def test_default_fact_retries_bounded_entity_budget_until_item_is_returned(
        self,
    ) -> None:
        ref = "bp://asset@revision/default/NestedObjects"

        class BudgetedRepository:
            def __init__(self) -> None:
                self.budgets: list[int] = []

            def query(self, request: dict[str, object]) -> dict[str, object]:
                budget = int(request["budgetTokens"])
                self.budgets.append(budget)
                if budget < 4800:
                    return {
                        "items": [],
                        "coverage": {"requested": 1, "returned": 0},
                        "nextQueries": [
                            {
                                "operation": "entity",
                                "selector": {"ref": ref},
                                "budgetTokens": budget * 2,
                            }
                        ],
                    }
                return {
                    "items": [
                        {
                            "ref": ref,
                            "kind": "default",
                            "name": "NestedObjects",
                            "typeName": "ArrayProperty",
                            "valueStatus": "CONFIRMED",
                            "valueUsable": True,
                            "value": [1],
                        }
                    ]
                }

        repository = BudgetedRepository()

        facts = BlueprintService._default_facts(  # noqa: SLF001
            repository,  # type: ignore[arg-type]
            [{"ref": ref, "kind": "default", "name": "NestedObjects"}],
            "NestedObjects",
        )

        self.assertEqual(repository.budgets, [1200, 2400, 4800])
        self.assertEqual([item["name"] for item in facts], ["NestedObjects"])

    def test_context_retries_entity_budget_for_large_nested_object_projection(
        self,
    ) -> None:
        name = "NestedObjectBudgetFixture"
        count = 24
        property_name = "DamageTypeEntryValuesOverrides"
        refs = [-(index + 1) for index in range(count)]
        names = [
            f"DmgType_Melee_LongDamageType_{index:03d}_C"
            for index in range(count)
        ]
        payload = interpretation_payload(name)
        payload["class_defaults"]["variables"]["NestedObjects"] = {
            "value": [{property_name: refs}],
            "type": "ArrayProperty",
            "source": "interpretation_fixture",
            "confidence": "high",
            "array_parse": {
                "parsed": True,
                "count": 1,
                "element_kind": "StructProperty",
                "elements": [
                    {
                        "index": 0,
                        "properties": [
                            {
                                "name": property_name,
                                "type": "ArrayProperty",
                                "value": refs,
                                "objects": names,
                                "array_parse": {
                                    "parsed": True,
                                    "count": count,
                                    "element_kind": "ObjectProperty",
                                    "elements": [
                                        {
                                            "index": index,
                                            "value": refs[index],
                                            "object": names[index],
                                        }
                                        for index in range(count)
                                    ],
                                },
                            }
                        ],
                    }
                ],
            },
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=64_000)

        result = self.context(
            asset=name,
            goal="NestedObjects",
            budget_tokens=6_000,
        )

        facts = [
            item
            for item in result["facts"]
            if item.get("kind") == "CLASS_DEFAULT"
        ]
        self.assertEqual(len(facts), 1)
        self.assertEqual(len(facts[0]["resolvedObjectFields"]), 24)
        self.assertIs(facts[0]["resolvedObjectFieldIdentityComplete"], True)
        self.assertEqual(
            facts[0]["resolvedObjectFields"][23]["valuePath"],
            [0, property_name, 23],
        )

    def test_asset_list_reuses_public_health_and_opaque_pagination(self) -> None:
        second_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name="SecondFixture",
        )
        publish_interpretation(second_dir, budget=32_000)

        first = self.service.list_assets(query="Fixture", limit=1, cursor="")
        self.assertEqual(
            first["schema"], "blueprint-to-code.mcp-blueprint-assets/v1"
        )
        self.assertEqual(len(first["items"]), 1)
        self.assertIsInstance(first["page"]["nextCursor"], str)
        second = self.service.list_assets(
            query="Fixture",
            limit=1,
            cursor=first["page"]["nextCursor"],
        )
        self.assertNotEqual(first["items"], second["items"])
        _assert_path_free(self, first, self.capture_root)
        _assert_path_free(self, second, self.capture_root)

    def test_context_is_fresh_path_free_bounded_and_deterministic(self) -> None:
        first = self.context()
        second = self.context()

        self.assertEqual(first, second)
        self.assertEqual(
            first["schema"], "blueprint-to-code.mcp-blueprint-context/v1"
        )
        self.assertEqual(first["freshness"], "FRESH")
        self.assertEqual(first["goal"], "ReceiveBeginPlay")
        self.assertEqual(len(first["querySignature"]), 64)
        self.assertGreaterEqual(len(first["graphTargets"]), 1)
        self.assertLessEqual(len(first["nodes"]), 40)
        self.assertLessEqual(len(first["pins"]), 160)
        self.assertLessEqual(len(first["edges"]), 160)
        _assert_path_free(self, first, self.capture_root)

    def test_context_uses_only_exact_node_or_pin_evidence_refs_as_seeds(self) -> None:
        baseline = self.context(max_hops=0)
        graph_ref = baseline["graphTargets"][0]["ref"]
        node_ref = baseline["nodes"][0]["ref"]
        pin_ref = baseline["pins"][0]["ref"]
        facts = [
            {
                "id": "statement://fixture",
                "kind": "CALL",
                "text": "Call fixture",
                "status": "HEURISTIC",
                "graphRef": graph_ref,
                "nodeRef": node_ref,
                "evidenceRefs": [
                    node_ref,
                    f"{node_ref}/reference/function/not-a-seed",
                    pin_ref,
                ],
                "gapRefs": [],
            }
        ]

        with patch.object(BlueprintService, "_facts", return_value=facts):
            result = self.context(
                goal="fixture",
                graph_ref=graph_ref,
                max_hops=0,
            )

        self.assertGreater(len(result["nodes"]), 0)
        self.assertEqual(result["facts"], facts)

    def test_current_interpreter_1_1_dinodefense_fixture_is_readable_across_services(
        self,
    ) -> None:
        name = "DinoDefenseFixture"
        object_path = f"/DinoDefense/Camera/{name}.{name}"
        payload = interpretation_payload(name)
        payload["asset_path"] = object_path
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        health = self.service.health(asset=name)
        authority = self.service.get_task_authority(asset=name)
        context = self.service.get_context(
            asset=name,
            goal="ReceiveBeginPlay",
        )

        self.assertTrue((asset_dir / "evidence" / "current.json").is_file())
        self.assertTrue((asset_dir / "interpretation" / "current.json").is_file())
        self.assertEqual(health["health"]["status"], "READY")
        self.assertEqual(health["health"]["asset"]["objectPath"], object_path)
        self.assertEqual(
            health["health"]["evidence"]["freshnessStatus"],
            "FRESH",
        )
        self.assertIs(
            health["health"]["evidence"]["releaseAuthority"],
            True,
        )
        self.assertEqual(
            health["health"]["interpretation"]["interpreterVersion"],
            "blueprint-interpreter/1.1.0",
        )
        self.assertEqual(authority["objectPath"], object_path)
        self.assertEqual(authority["freshness"], "FRESH")
        self.assertEqual(context["identity"]["asset"]["objectPath"], object_path)
        self.assertIs(
            context["identity"]["evidence"]["releaseAuthority"],
            True,
        )
        self.assertEqual(
            context["identity"]["interpretation"]["interpreterVersion"],
            "blueprint-interpreter/1.1.0",
        )
        self.assertEqual(context["freshness"], "FRESH")

    def test_context_requires_explicit_graph_selection_when_goal_has_no_match(
        self,
    ) -> None:
        with self.assertRaises(McpExecutionError) as raised:
            self.context(goal="definitely-no-such-graph-or-node")

        self.assertEqual(raised.exception.code, "GRAPH_SELECTION_REQUIRED")
        candidates = raised.exception.details["candidates"]
        self.assertGreater(len(candidates), 0)
        self.assertLessEqual(len(candidates), 5)
        _assert_path_free(self, raised.exception.as_payload(), self.capture_root)

    def test_context_can_answer_exact_class_default_without_graph_selection(
        self,
    ) -> None:
        result = self.context(goal="DefaultThreshold")

        self.assertEqual(result["graphTargets"], [])
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["pins"], [])
        self.assertEqual(result["edges"], [])
        self.assertEqual(len(result["facts"]), 1)
        fact = result["facts"][0]
        self.assertEqual(fact["kind"], "CLASS_DEFAULT")
        self.assertEqual(fact["name"], "DefaultThreshold")
        self.assertEqual(fact["typeName"], "FloatProperty")
        self.assertEqual(fact["value"], 2.5)
        self.assertEqual(fact["status"], "CONFIRMED")
        self.assertEqual(fact["evidenceRefs"], [fact["id"]])
        self.assertTrue(fact["id"].startswith("bp://"))
        _assert_path_free(self, result, self.capture_root)

    def test_context_can_answer_exact_default_name_containing_spaces(self) -> None:
        name = "SpacedDefaultFixture"
        payload = interpretation_payload(name)
        payload["class_defaults"]["variables"]["SpotLight Brightness"] = {
            "value": 6.0,
            "type": "FloatProperty",
            "source": "interpretation_fixture",
            "confidence": "high",
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        result = self.context(asset=name, goal="SpotLight Brightness")

        defaults = [
            item
            for item in result["facts"]
            if item.get("kind") == "CLASS_DEFAULT"
        ]
        self.assertEqual([item["name"] for item in defaults], ["SpotLight Brightness"])
        self.assertEqual(defaults[0]["value"], 6.0)

        normalized = self.context(
            asset=name,
            goal="  SpotLight   Brightness  ",
        )
        normalized_defaults = [
            item
            for item in normalized["facts"]
            if item.get("kind") == "CLASS_DEFAULT"
        ]
        self.assertEqual(
            [item["name"] for item in normalized_defaults],
            ["SpotLight Brightness"],
        )

    def test_context_finds_spaced_exact_default_beyond_each_term_page(self) -> None:
        name = "SpacedDefaultCrowdingFixture"
        payload = interpretation_payload(name)
        for index in range(30):
            payload["class_defaults"]["variables"][f"SpotLight A{index:03d}"] = {
                "value": index,
                "type": "IntProperty",
                "source": "interpretation_fixture",
                "confidence": "high",
            }
            payload["class_defaults"]["variables"][f"BrightnessA{index:03d}"] = {
                "value": index,
                "type": "IntProperty",
                "source": "interpretation_fixture",
                "confidence": "high",
            }
        payload["class_defaults"]["variables"]["SpotLight Brightness"] = {
            "value": 6.0,
            "type": "FloatProperty",
            "source": "interpretation_fixture",
            "confidence": "high",
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=64_000)

        result = self.context(asset=name, goal="SpotLight Brightness")

        defaults = [
            item
            for item in result["facts"]
            if item.get("kind") == "CLASS_DEFAULT"
        ]
        self.assertEqual([item["name"] for item in defaults], ["SpotLight Brightness"])
        self.assertEqual(defaults[0]["value"], 6.0)

    def test_context_keeps_unique_graph_route_when_defaults_share_search_term(
        self,
    ) -> None:
        name = "DefaultCrowdingFixture"
        payload = interpretation_payload(name)
        graph = payload["graphs"][0]
        graph["graph"] = "FooGraph"
        graph["payload"]["metadata"]["graph_name"] = "FooGraph"
        for index in range(100):
            payload["class_defaults"]["variables"][f"FooDefault{index:03d}"] = {
                "value": index,
                "type": "IntProperty",
                "source": "interpretation_fixture",
                "confidence": "high",
            }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        result = self.context(asset=name, goal="Foo")

        self.assertEqual(
            [item["name"] for item in result["graphTargets"]],
            ["FooGraph"],
        )
        self.assertFalse(
            any(item.get("kind") == "CLASS_DEFAULT" for item in result["facts"])
        )

    def test_context_keeps_unique_graph_and_adds_exact_default_fact(self) -> None:
        result = self.context(goal="DefaultThreshold ReceiveBeginPlay")

        self.assertEqual(
            [item["name"] for item in result["graphTargets"]],
            ["EventGraph"],
        )
        defaults = [
            item
            for item in result["facts"]
            if item.get("kind") == "CLASS_DEFAULT"
        ]
        self.assertEqual([item["name"] for item in defaults], ["DefaultThreshold"])

    def test_context_rejects_substring_only_default_as_graphless_answer(self) -> None:
        with self.assertRaises(McpExecutionError) as raised:
            self.context(goal="Threshold")

        self.assertEqual(raised.exception.code, "GRAPH_SELECTION_REQUIRED")

    def test_context_returns_only_exact_defaults_when_fuzzy_defaults_are_crowded(
        self,
    ) -> None:
        name = "ExactDefaultFixture"
        payload = interpretation_payload(name)
        for prefix in ("aa", "bb", "cc", "dd", "ee"):
            for index in range(25):
                payload["class_defaults"]["variables"][
                    f"{prefix}Fuzzy{index:03d}"
                ] = {
                    "value": index,
                    "type": "IntProperty",
                    "source": "interpretation_fixture",
                    "confidence": "high",
                }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=64_000)

        result = self.context(
            asset=name,
            goal="aa bb cc dd ee DefaultThreshold",
        )

        defaults = [
            item
            for item in result["facts"]
            if item.get("kind") == "CLASS_DEFAULT"
        ]
        self.assertEqual([item["name"] for item in defaults], ["DefaultThreshold"])

    def test_context_low_budget_fails_instead_of_returning_stuck_continuation(
        self,
    ) -> None:
        with self.assertRaises(McpExecutionError) as raised:
            self.context(goal="DefaultThreshold", budget_tokens=800)

        self.assertEqual(raised.exception.code, "RESULT_BUDGET_EXCEEDED")
        self.assertGreater(
            raised.exception.details["minimumBudgetTokens"],
            800,
        )

    def test_context_withholds_machine_local_default_value_without_losing_fact(
        self,
    ) -> None:
        name = "LocalPathDefaultFixture"
        payload = interpretation_payload(name)
        payload["class_defaults"]["variables"]["LocalInstallPath"] = {
            "value": r"C:\Users\fixture\private\asset.uasset",
            "type": "StrProperty",
            "source": "interpretation_fixture",
            "confidence": "high",
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        result = self.context(asset=name, goal="LocalInstallPath")

        self.assertEqual(result["graphTargets"], [])
        self.assertEqual(len(result["facts"]), 1)
        fact = result["facts"][0]
        self.assertEqual(fact["name"], "LocalInstallPath")
        self.assertEqual(fact["sourceValueStatus"], "CONFIRMED")
        self.assertEqual(fact["status"], "NOT_RECOVERED")
        self.assertIs(fact["valueUsable"], False)
        self.assertEqual(fact["valueExposure"], "WITHHELD_BY_PATH_POLICY")
        self.assertNotIn("value", fact)
        _assert_path_free(self, result, self.capture_root)

    def test_context_does_not_retype_unreal_looking_string_as_object_path(self) -> None:
        name = "UnrealLookingStringFixture"
        payload = interpretation_payload(name)
        payload["class_defaults"]["variables"]["DisplayText"] = {
            "value": "/Game/Test/FixtureAsset.FixtureAsset",
            "type": "StrProperty",
            "source": "interpretation_fixture",
            "confidence": "high",
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        result = self.context(asset=name, goal="DisplayText")

        fact = result["facts"][0]
        self.assertEqual(fact["status"], "NOT_RECOVERED")
        self.assertEqual(fact["valueExposure"], "WITHHELD_BY_PATH_POLICY")
        self.assertIs(fact["valueUsable"], False)
        self.assertNotIn("objectPath", fact)
        _assert_path_free(self, result, self.capture_root)

    def test_context_projects_unreal_object_default_as_typed_object_path(self) -> None:
        name = "ObjectPathDefaultFixture"
        object_path = "/Game/Test/FixtureAsset.FixtureAsset"
        payload = interpretation_payload(name)
        payload["class_defaults"]["variables"]["ConfiguredAsset"] = {
            "value": object_path,
            "type": "SoftObjectProperty",
            "source": "interpretation_fixture",
            "confidence": "high",
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        result = self.context(asset=name, goal="ConfiguredAsset")

        fact = result["facts"][0]
        self.assertEqual(fact["objectPath"], object_path)
        self.assertEqual(fact["valueExposure"], "OBJECT_PATH")
        self.assertNotIn("value", fact)
        _assert_path_free(self, result, self.capture_root)

    def test_context_projects_resolved_object_array_as_typed_object_paths(self) -> None:
        name = "ObjectArrayDefaultFixture"
        object_path = "/Game/Test/EditorTools.EditorTools"
        payload = interpretation_payload(name)
        payload["class_defaults"]["variables"]["DataLayerAssets"] = {
            "value": [3],
            "type": "ArrayProperty",
            "source": "interpretation_fixture",
            "confidence": "medium",
            "objects": [object_path],
            "array_parse": {
                "parsed": True,
                "count": 1,
                "element_kind": "SoftObjectProperty",
            },
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        result = self.context(asset=name, goal="DataLayerAssets")

        fact = result["facts"][0]
        self.assertEqual(fact["resolvedObjectPaths"], [object_path])
        self.assertEqual(fact["value"], [3])
        self.assertIs(fact["valueUsable"], True)
        _assert_path_free(self, result, self.capture_root)

    def test_context_marks_truncated_resolved_object_array_incomplete(self) -> None:
        name = "LargeObjectArrayDefaultFixture"
        object_paths: list[str] = [
            f"/Game/Test/Object{index:03d}.Object{index:03d}"
            for index in range(30)
        ]
        object_paths[5] = ""
        payload = interpretation_payload(name)
        payload["class_defaults"]["variables"]["ConfiguredAssets"] = {
            "value": list(range(30)),
            "type": "ArrayProperty",
            "source": "interpretation_fixture",
            "confidence": "medium",
            "objects": object_paths,
            "array_parse": {
                "parsed": True,
                "count": 30,
                "element_kind": "SoftObjectProperty",
            },
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=64_000)

        result = self.context(asset=name, goal="ConfiguredAssets")

        fact = result["facts"][0]
        self.assertEqual(
            fact["resolvedObjectCoverage"],
            {"available": 30, "returned": 24},
        )
        self.assertIs(fact["resolvedObjectIdentityComplete"], False)
        self.assertEqual(len(fact["resolvedObjectPaths"]), 24)
        self.assertIsNone(fact["resolvedObjectPaths"][5])
        self.assertEqual(fact["resolvedObjectPaths"][6], object_paths[6])
        self.assertIs(fact["valueUsable"], True)
        _assert_path_free(self, result, self.capture_root)

    def test_context_does_not_confirm_incomplete_compressed_default_value(
        self,
    ) -> None:
        name = "LargeDefaultFixture"
        payload = interpretation_payload(name)
        large_value = list(range(2000))
        payload["class_defaults"]["variables"]["LargeValues"] = {
            "value": large_value,
            "type": "ArrayProperty",
            "source": "interpretation_fixture",
            "confidence": "high",
            "array_parse": {
                "parsed": True,
                "count": len(large_value),
                "element_kind": "IntProperty",
            },
        }
        asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(asset_dir, budget=32_000)

        result = self.context(asset=name, goal="LargeValues")

        fact = result["facts"][0]
        self.assertEqual(fact["sourceValueStatus"], "CONFIRMED")
        self.assertEqual(fact["status"], "NOT_RECOVERED")
        self.assertEqual(fact["valueExposure"], "AVAILABLE_NOT_RETURNED")
        self.assertIs(fact["valueUsable"], False)
        self.assertNotIn("value", fact)
        self.assertNotIn("valueFragment", fact)
        self.assertGreater(fact["valueCoverage"]["availableChars"], 400)
        _assert_path_free(self, result, self.capture_root)

    def test_context_fails_closed_when_evidence_source_is_stale(self) -> None:
        self.source_path.write_bytes(self.source_path.read_bytes() + b"-changed")

        with self.assertRaises(McpExecutionError) as raised:
            self.context()

        self.assertEqual(raised.exception.code, "EVIDENCE_STALE")
        _assert_path_free(self, raised.exception.as_payload(), self.capture_root)

    def test_context_continuation_is_opaque_bounded_and_query_bound(self) -> None:
        name = "LargeInterpretationFixture"
        payload = large_interpretation_payload(name, node_count=30)
        large_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root,
            name=name,
            payload=payload,
        )
        publish_interpretation(large_dir, budget=32_000)

        arguments = {
            "asset": name,
            "goal": "LargePureNode",
            "graph_ref": "",
            "seed_refs": (),
            "max_hops": 0,
            "max_nodes": 1,
            "max_pins": 1,
            "max_edges": 1,
            "budget_tokens": 2400,
            "continuation": "",
        }
        first = self.service.get_context(**arguments)
        self.assertTrue(first["truncated"])
        self.assertEqual(len(first["nodes"]), 1)
        self.assertEqual(len(first["pins"]), 1)
        self.assertIsInstance(first["continuation"], str)
        self.assertNotIn("revision", first["continuation"])

        arguments["continuation"] = first["continuation"]
        second = self.service.get_context(**arguments)
        self.assertEqual(len(second["nodes"]), 1)
        self.assertNotEqual(first["nodes"], second["nodes"])

        arguments["goal"] = "different query"
        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_context(**arguments)
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

        arguments["goal"] = "LargePureNode"
        token = first["continuation"]
        decoded = json.loads(
            base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode(
                "utf-8"
            )
        )
        decoded["r"] = "0" * 24
        arguments["continuation"] = base64.urlsafe_b64encode(
            json.dumps(decoded, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_context(**arguments)
        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_MISMATCH")

        decoded["r"] = first["identity"]["evidence"]["revisionId"]
        decoded["o"]["nodes"] = 10_000
        arguments["continuation"] = base64.urlsafe_b64encode(
            json.dumps(decoded, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_context(**arguments)
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_exact_node_is_revision_bound_and_returns_direct_context(self) -> None:
        context = self.context(max_hops=0)
        node_ref = context["nodes"][0]["ref"]

        result = self.service.get_node(
            asset="InterpretationFixture",
            node_ref=node_ref,
            include_neighborhood=True,
            max_hops=1,
        )
        self.assertEqual(
            result["schema"], "blueprint-to-code.mcp-blueprint-node/v1"
        )
        self.assertEqual(result["node"]["ref"], node_ref)
        self.assertTrue(all(item["ref"].startswith("bp://") for item in result["pins"]))
        _assert_path_free(self, result, self.capture_root)

        wrong_revision = node_ref.replace(
            node_ref.split("@", 1)[1].split("/", 1)[0],
            "0" * 24,
            1,
        )
        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_node(
                asset="InterpretationFixture",
                node_ref=wrong_revision,
                include_neighborhood=False,
                max_hops=0,
            )
        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_MISMATCH")

    def test_exact_node_does_not_fall_back_to_fuzzy_search(self) -> None:
        context = self.context(max_hops=0)
        node_ref = context["nodes"][0]["ref"]
        missing_ref = node_ref.rsplit("/", 1)[0] + "/999999"

        with self.assertRaises(McpExecutionError) as raised:
            self.service.get_node(
                asset="InterpretationFixture",
                node_ref=missing_ref,
                include_neighborhood=False,
                max_hops=0,
            )
        self.assertEqual(raised.exception.code, "NODE_NOT_FOUND")

    def test_context_graph_ref_must_belong_to_current_asset_revision(self) -> None:
        context = self.context(max_hops=0)
        graph_ref = context["graphTargets"][0]["ref"]
        wrong_revision = graph_ref.replace(
            graph_ref.split("@", 1)[1].split("/", 1)[0],
            "f" * 24,
            1,
        )

        with self.assertRaises(McpExecutionError) as raised:
            self.context(graph_ref=wrong_revision)
        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_MISMATCH")

    def test_fixture_helper_can_publish_a_distinct_goal_for_future_regressions(
        self,
    ) -> None:
        payload = interpretation_payload("OtherFixture")
        self.assertEqual(payload["asset_name"], "OtherFixture")


if __name__ == "__main__":
    unittest.main()
