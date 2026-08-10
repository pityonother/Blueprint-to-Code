from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.contracts import McpExecutionError  # noqa: E402
from arkdev_mcp.tasking.plan_service import PlanService  # noqa: E402
from arkdev_mcp.tasking.plan_validator import plan_semantic_digest  # noqa: E402
from arkdev_mcp.tasking.renderer import render_patch_plan  # noqa: E402
from arkdev_mcp.tasking.research_service import ResearchService  # noqa: E402
from arkdev_mcp.tasking.store import TaskStore  # noqa: E402
from arkdev_mcp.tasking.task_service import TaskService  # noqa: E402
from blueprint_translator.interpretation_publication import (  # noqa: E402
    publish_interpretation,
)
from interpretation_fixture import (  # noqa: E402
    interpretation_payload,
    publish_interpretation_fixture,
)


def _direction(value: str) -> str:
    return "OUTPUT" if value in {"OUTPUT", "EGPD_Output"} else "INPUT"


def _ordinal(pin_ref: str) -> int:
    return int(pin_ref.rsplit("/", 1)[-1])


def _pin_signature(pin: dict[str, object]) -> dict[str, object]:
    return {
        "name": pin["name"],
        "direction": _direction(str(pin["direction"])),
        "category": pin.get("category", ""),
        "subcategory": pin.get("subcategory", ""),
        "ordinal": _ordinal(str(pin["ref"])),
        "containerType": pin.get("containerType", "None"),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PatchPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.capture_root = self.root / "captures"
        self.asset_dir, _source, _payload = publish_interpretation_fixture(
            self.capture_root
        )
        publish_interpretation(self.asset_dir, budget=32_000)
        self.blueprint = BlueprintService(self.capture_root)
        self.store = TaskStore(self.root / ".blueprint-tasks")
        self.tasks = TaskService(self.blueprint, self.store)
        self.research = ResearchService(self.blueprint, self.tasks, self.store)
        self.plans = PlanService(self.blueprint, self.tasks, self.store)
        self.graph_ref = self.blueprint.get_task_authority(
            asset="InterpretationFixture"
        )["graphTargets"][0]["ref"]
        self.task = self.tasks.create(
            mode="IMPLEMENTATION_PREP",
            asset="InterpretationFixture",
            goal="Insert an exact guarded execution step",
            completion_criteria=["Every connection has exact Pin endpoints"],
            allowed_changes=["EventGraph"],
            forbidden_changes=["Other assets"],
            graph_ref=self.graph_ref,
            supporting_assets=[],
        )
        self.slice = self.research.research(
            task_id=self.task["taskId"],
            question="ReceiveBeginPlay execution flow",
            graph_ref=self.graph_ref,
            max_hops=1,
        )
        self.event = next(
            node for node in self.slice["nodes"] if node.get("label") == "ReceiveBeginPlay"
        )
        self.sequence = next(
            node for node in self.slice["nodes"] if node.get("name") == "Sequence"
        )
        self.event_pins = [
            pin for pin in self.slice["pins"] if pin.get("nodeRef") == self.event["ref"]
        ]
        self.sequence_pins = [
            pin
            for pin in self.slice["pins"]
            if pin.get("nodeRef") == self.sequence["ref"]
        ]
        self.event_output = next(
            pin for pin in self.event_pins if _direction(str(pin["direction"])) == "OUTPUT"
        )
        self.sequence_input = next(
            pin
            for pin in self.sequence_pins
            if _direction(str(pin["direction"])) == "INPUT"
        )

    def plan_input(self) -> dict[str, object]:
        proposed_signature = {
            "nodeFamily": "BRANCH",
            "className": "K2Node_IfThenElse",
            "functionOwner": "",
            "functionName": "",
            "variableName": "",
            "eventName": "",
            "pure": False,
            "pinSignatures": [
                {
                    "name": "execute",
                    "direction": "INPUT",
                    "category": "exec",
                    "subcategory": "",
                    "ordinal": 0,
                    "containerType": "None",
                },
                {
                    "name": "then",
                    "direction": "OUTPUT",
                    "category": "exec",
                    "subcategory": "",
                    "ordinal": 1,
                    "containerType": "None",
                },
            ],
        }
        nodes = [
            {
                "nodeRef": self.event["ref"],
                "graphRef": self.graph_ref,
                "signature": {
                    "nodeFamily": "EVENT",
                    "className": self.event["className"],
                    "functionOwner": "",
                    "functionName": "",
                    "variableName": "",
                    "eventName": "ReceiveBeginPlay",
                    "pure": False,
                    "pinSignatures": [_pin_signature(pin) for pin in self.event_pins],
                },
            },
            {
                "nodeRef": self.sequence["ref"],
                "graphRef": self.graph_ref,
                "signature": {
                    "nodeFamily": "SEQUENCE",
                    "className": self.sequence["className"],
                    "functionOwner": "",
                    "functionName": "",
                    "variableName": "",
                    "eventName": "",
                    "pure": False,
                    "pinSignatures": [_pin_signature(pin) for pin in self.sequence_pins],
                },
            },
            {
                "localPlanNodeId": "plan-node://branch-guard",
                "graphRef": self.graph_ref,
                "signature": proposed_signature,
            },
        ]
        common = {
            "graphRef": self.graph_ref,
            "preconditions": [],
            "postconditions": [],
            "checkpoint": "checkpoint://graph-structure",
        }
        operations = [
            {
                **common,
                "operationId": "op://create-branch",
                "kind": "CREATE_NODE",
                "dependsOn": [],
                "payload": {"localPlanNodeId": "plan-node://branch-guard"},
            },
            {
                **common,
                "operationId": "op://connect-entry",
                "kind": "CONNECT",
                "dependsOn": ["op://create-branch"],
                "payload": {},
                "from": {
                    "node": self.event["ref"],
                    "pin": {
                        "pinRef": self.event_output["ref"],
                        "name": self.event_output["name"],
                        "direction": "OUTPUT",
                        "ordinal": _ordinal(str(self.event_output["ref"])),
                    },
                },
                "to": {
                    "node": "plan-node://branch-guard",
                    "pin": {
                        "pinRef": "",
                        "name": "execute",
                        "direction": "INPUT",
                        "ordinal": 0,
                    },
                },
            },
            {
                **common,
                "operationId": "op://connect-sequence",
                "kind": "CONNECT",
                "dependsOn": ["op://connect-entry"],
                "payload": {},
                "from": {
                    "node": "plan-node://branch-guard",
                    "pin": {
                        "pinRef": "",
                        "name": "then",
                        "direction": "OUTPUT",
                        "ordinal": 1,
                    },
                },
                "to": {
                    "node": self.sequence["ref"],
                    "pin": {
                        "pinRef": self.sequence_input["ref"],
                        "name": self.sequence_input["name"],
                        "direction": "INPUT",
                        "ordinal": _ordinal(str(self.sequence_input["ref"])),
                    },
                },
            },
        ]
        return {
            "task_id": self.task["taskId"],
            "nodes": nodes,
            "operations": operations,
            "capability_requirements": ["CREATE_NODE", "CREATE_CONNECTION"],
            "checkpoints": [
                {
                    "checkpointId": "checkpoint://graph-structure",
                    "description": "Exact graph structure after both connections",
                }
            ],
            "blocking_questions": [],
        }

    def draft(self, **overrides: object) -> dict[str, object]:
        arguments = self.plan_input()
        arguments.update(overrides)
        return self.plans.draft(**arguments)

    def validate_input(self, arguments: dict[str, object]) -> dict[str, object]:
        context = self.store.load_context(self.task["taskId"])
        primary = context["primaryAsset"]
        plan: dict[str, object] = {
            "schema": "blueprint-to-code.blueprint-patch-plan/v1",
            "planId": "patch-plan://" + "0" * 32,
            "taskId": self.task["taskId"],
            "status": "DRAFT",
            "target": {
                "assetId": primary["assetId"],
                "objectPath": primary["objectPath"],
                "evidenceRevisionId": primary["evidenceRevisionId"],
                "evidenceManifestSha256": primary["evidenceManifestSha256"],
                "graphRefs": [
                    str(item.get("ref") or "")
                    for item in context["graphTargets"]
                ],
            },
            "capabilityRequirements": copy.deepcopy(
                arguments["capability_requirements"]
            ),
            "nodes": copy.deepcopy(arguments["nodes"]),
            "operations": copy.deepcopy(arguments["operations"]),
            "checkpoints": copy.deepcopy(arguments["checkpoints"]),
            "blockingQuestions": [],
            "createdAt": "2026-08-11T00:00:00Z",
            "updatedAt": "2026-08-11T00:00:00Z",
        }
        plan["semanticDigest"] = plan_semantic_digest(plan)
        return self.plans.validator.validate(context, plan)

    def test_valid_draft_is_exact_revision_bound_and_not_execution_ready(self) -> None:
        draft = self.draft()
        validation = self.plans.validate(self.task["taskId"], draft["planId"])

        self.assertEqual(draft["schema"], "blueprint-to-code.blueprint-patch-plan/v1")
        self.assertEqual(draft["status"], "DRAFT")
        self.assertEqual(draft["target"]["assetId"], self.task["primaryAsset"]["assetId"])
        self.assertEqual(draft["target"]["graphRefs"], [self.graph_ref])
        self.assertTrue(validation["valid"])
        self.assertTrue(validation["confirmable"])
        self.assertFalse(validation["executionReady"])
        self.assertEqual(validation["reason"], "EDITOR_BRIDGE_NOT_INSTALLED")
        self.assertIn("Pin type compatibility is not validated", validation["humanSummary"])
        self.assertIn("目标: Insert an exact guarded execution step", validation["humanSummary"])
        self.assertIn("Connections:", validation["humanSummary"])
        encoded = json.dumps(validation, ensure_ascii=False)
        self.assertNotIn(str(self.root), encoded)

    def test_every_proposed_node_requires_exactly_one_create_operation(self) -> None:
        missing = self.plan_input()
        missing["operations"] = missing["operations"][1:]
        missing["operations"][0]["dependsOn"] = []
        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**missing)
        self.assertIn(
            "PROPOSED_NODE_CREATE_MISSING",
            raised.exception.details["errorCodes"],
        )

        duplicate = self.plan_input()
        second_create = copy.deepcopy(duplicate["operations"][0])
        second_create["operationId"] = "op://create-branch-again"
        duplicate["operations"].insert(1, second_create)
        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**duplicate)
        self.assertIn(
            "PROPOSED_NODE_CREATE_DUPLICATE",
            raised.exception.details["errorCodes"],
        )

    def test_create_node_graph_must_match_proposed_node_graph(self) -> None:
        graph_refs = [
            item["ref"]
            for item in self.blueprint.get_task_authority(
                asset="InterpretationFixture"
            )["graphTargets"]
        ]
        second_graph = next(ref for ref in graph_refs if ref != self.graph_ref)
        self.research.research(
            task_id=self.task["taskId"],
            question="Inspect a second exact graph",
            graph_ref=second_graph,
        )
        arguments = self.plan_input()
        arguments["operations"][0]["graphRef"] = second_graph

        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**arguments)

        self.assertIn("GRAPH_SCOPE_INVALID", raised.exception.details["errorCodes"])

    def test_proposed_node_references_require_create_dependency_closure(self) -> None:
        arguments = self.plan_input()
        arguments["operations"][1]["dependsOn"] = []

        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**arguments)

        self.assertIn(
            "PROPOSED_NODE_CREATE_DEPENDENCY_MISSING",
            raised.exception.details["errorCodes"],
        )

    def test_transitive_create_dependency_is_accepted(self) -> None:
        arguments = self.plan_input()
        create = arguments["operations"][0]
        connect = arguments["operations"][1]
        helper = {
            "operationId": "op://preserve-after-create",
            "kind": "PRESERVE",
            "graphRef": self.graph_ref,
            "dependsOn": [create["operationId"]],
            "preconditions": [{"nodeRef": self.event["ref"]}],
            "payload": {"nodeRef": self.event["ref"]},
            "postconditions": [{"unchanged": True}],
            "checkpoint": "checkpoint://graph-structure",
        }
        connect["dependsOn"] = [helper["operationId"]]
        arguments["operations"] = [create, helper, connect]

        draft = self.plans.draft(**arguments)

        self.assertTrue(draft["valid"])

    def test_set_default_and_move_require_proposed_create_dependency(self) -> None:
        for kind, payload, capability in (
            (
                "SET_DEFAULT",
                {
                    "localPlanNodeId": "plan-node://branch-guard",
                    "pinSignature": copy.deepcopy(
                        self.plan_input()["nodes"][2]["signature"][
                            "pinSignatures"
                        ][0]
                    ),
                    "newValue": "true",
                    "valueEncoding": "STRING",
                },
                "SET_PIN_DEFAULT",
            ),
            (
                "MOVE_NODE",
                {
                    "localPlanNodeId": "plan-node://branch-guard",
                    "x": 100,
                    "y": 200,
                },
                "MOVE_NODE",
            ),
        ):
            with self.subTest(kind=kind):
                arguments = self.plan_input()
                operation = {
                    "operationId": f"op://{kind.casefold().replace('_', '-')}",
                    "kind": kind,
                    "graphRef": self.graph_ref,
                    "dependsOn": [],
                    "preconditions": [],
                    "payload": payload,
                    "postconditions": [],
                    "checkpoint": "checkpoint://graph-structure",
                }
                arguments["operations"] = [arguments["operations"][0], operation]
                arguments["capability_requirements"] = ["CREATE_NODE", capability]

                with self.assertRaises(McpExecutionError) as raised:
                    self.plans.draft(**arguments)

                self.assertIn(
                    "PROPOSED_NODE_CREATE_DEPENDENCY_MISSING",
                    raised.exception.details["errorCodes"],
                )

    def test_move_node_requires_exactly_one_target(self) -> None:
        arguments = self.plan_input()
        move = {
            "operationId": "op://move-ambiguous",
            "kind": "MOVE_NODE",
            "graphRef": self.graph_ref,
            "dependsOn": ["op://create-branch"],
            "preconditions": [],
            "payload": {
                "nodeRef": self.event["ref"],
                "localPlanNodeId": "plan-node://branch-guard",
                "x": 100,
                "y": 200,
            },
            "postconditions": [],
            "checkpoint": "checkpoint://graph-structure",
        }
        arguments["operations"] = [arguments["operations"][0], move]
        arguments["capability_requirements"] = ["CREATE_NODE", "MOVE_NODE"]

        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**arguments)

        self.assertIn(
            "MOVE_NODE_TARGET_INVALID",
            raised.exception.details["errorCodes"],
        )

    def test_two_uncreated_proposed_nodes_cannot_form_confirmable_connection(self) -> None:
        arguments = self.plan_input()
        first = copy.deepcopy(arguments["nodes"][2])
        second = copy.deepcopy(first)
        second["localPlanNodeId"] = "plan-node://branch-guard-second"
        connect = copy.deepcopy(arguments["operations"][1])
        connect["operationId"] = "op://connect-uncreated-nodes"
        connect["dependsOn"] = []
        connect["from"] = {
            "node": first["localPlanNodeId"],
            "pin": {
                "pinRef": "",
                "name": "then",
                "direction": "OUTPUT",
                "ordinal": 1,
            },
        }
        connect["to"] = {
            "node": second["localPlanNodeId"],
            "pin": {
                "pinRef": "",
                "name": "execute",
                "direction": "INPUT",
                "ordinal": 0,
            },
        }
        arguments["nodes"] = [first, second]
        arguments["operations"] = [connect]
        arguments["capability_requirements"] = ["CREATE_CONNECTION"]

        validation = self.validate_input(arguments)

        self.assertFalse(validation["valid"])
        self.assertFalse(validation["confirmable"])
        self.assertIn(
            "PROPOSED_NODE_CREATE_MISSING",
            [item["code"] for item in validation["errors"]],
        )

    def test_validator_rejects_wrong_pin_ownership_direction_and_dependency_cycle(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []

        wrong_owner = self.plan_input()
        wrong_owner["operations"][1]["from"]["node"] = self.sequence["ref"]
        cases.append(("PIN_OWNERSHIP_MISMATCH", wrong_owner))

        wrong_direction = self.plan_input()
        wrong_direction["operations"][1]["from"]["pin"]["direction"] = "INPUT"
        cases.append(("CONNECT_DIRECTION_INVALID", wrong_direction))

        cycle = self.plan_input()
        cycle["operations"][0]["dependsOn"] = ["op://connect-sequence"]
        cases.append(("OPERATION_DEPENDENCY_CYCLE", cycle))

        for expected, arguments in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(McpExecutionError) as raised:
                    self.plans.draft(**arguments)
                self.assertEqual(raised.exception.code, "PATCH_PLAN_INVALID")
                self.assertIn(expected, raised.exception.details["errorCodes"])

    def test_operation_nodes_must_belong_to_the_declared_target_graph(self) -> None:
        graph_refs = [
            item["ref"]
            for item in self.blueprint.get_task_authority(
                asset="InterpretationFixture"
            )["graphTargets"]
        ]
        second_graph = next(ref for ref in graph_refs if ref != self.graph_ref)
        self.research.research(
            task_id=self.task["taskId"],
            question="Inspect the second exact graph",
            graph_ref=second_graph,
        )
        arguments = self.plan_input()
        arguments["operations"][1]["graphRef"] = second_graph

        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**arguments)

        self.assertEqual(raised.exception.code, "PATCH_PLAN_INVALID")
        self.assertIn("GRAPH_SCOPE_INVALID", raised.exception.details["errorCodes"])

    def test_disconnect_requires_an_existing_edge_and_exact_preconditions(self) -> None:
        common = {
            "operationId": "op://disconnect-entry",
            "kind": "DISCONNECT",
            "graphRef": self.graph_ref,
            "dependsOn": [],
            "payload": {},
            "postconditions": [],
            "checkpoint": "checkpoint://disconnect",
            "from": {
                "node": self.event["ref"],
                "pin": {
                    "pinRef": self.event_output["ref"],
                    "name": self.event_output["name"],
                    "direction": "OUTPUT",
                    "ordinal": _ordinal(str(self.event_output["ref"])),
                },
            },
            "to": {
                "node": self.sequence["ref"],
                "pin": {
                    "pinRef": self.sequence_input["ref"],
                    "name": self.sequence_input["name"],
                    "direction": "INPUT",
                    "ordinal": _ordinal(str(self.sequence_input["ref"])),
                },
            },
        }
        arguments = self.plan_input()
        arguments.update(
            {
                "nodes": arguments["nodes"][:2],
                "operations": [{**common, "preconditions": []}],
                "capability_requirements": ["BREAK_PIN_LINKS"],
                "checkpoints": [
                    {
                        "checkpointId": "checkpoint://disconnect",
                        "description": "The exact current edge is disconnected",
                    }
                ],
            }
        )
        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**arguments)
        self.assertIn(
            "DISCONNECT_PRECONDITION_INVALID",
            raised.exception.details["errorCodes"],
        )

        arguments["operations"][0]["preconditions"] = [
            {
                "sourcePinRef": self.event_output["ref"],
                "targetPinRef": self.sequence_input["ref"],
            }
        ]
        draft = self.plans.draft(**arguments)
        self.assertTrue(draft["valid"])

    def test_delete_requires_the_current_exact_node_precondition(self) -> None:
        arguments = self.plan_input()
        operation = {
            "operationId": "op://delete-entry",
            "kind": "DELETE_NODE",
            "graphRef": self.graph_ref,
            "dependsOn": [],
            "preconditions": [],
            "payload": {"nodeRef": self.event["ref"]},
            "postconditions": [],
            "checkpoint": "checkpoint://delete",
        }
        arguments.update(
            {
                "nodes": [arguments["nodes"][0]],
                "operations": [operation],
                "capability_requirements": ["DELETE_NODE"],
                "checkpoints": [
                    {
                        "checkpointId": "checkpoint://delete",
                        "description": "The exact current node is absent",
                    }
                ],
            }
        )
        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**arguments)
        self.assertIn(
            "DELETE_PRECONDITION_INVALID",
            raised.exception.details["errorCodes"],
        )

        operation["preconditions"] = [{"nodeRef": self.event["ref"]}]
        draft = self.plans.draft(**arguments)
        self.assertTrue(draft["valid"])

    def test_set_default_requires_exact_old_value_and_value_encoding(self) -> None:
        arguments = self.plan_input()
        operation = {
            "operationId": "op://set-sequence-default",
            "kind": "SET_DEFAULT",
            "graphRef": self.graph_ref,
            "dependsOn": [],
            "preconditions": [
                {
                    "nodeRef": self.sequence["ref"],
                    "pinRef": self.sequence_input["ref"],
                    "oldValue": self.sequence_input.get("default"),
                }
            ],
            "payload": {
                "nodeRef": self.sequence["ref"],
                "pinRef": self.sequence_input["ref"],
                "newValue": "diagnostic",
            },
            "postconditions": [],
            "checkpoint": "checkpoint://default",
        }
        arguments.update(
            {
                "nodes": [arguments["nodes"][1]],
                "operations": [operation],
                "capability_requirements": ["SET_PIN_DEFAULT"],
                "checkpoints": [
                    {
                        "checkpointId": "checkpoint://default",
                        "description": "The exact Pin receives the planned default",
                    }
                ],
            }
        )
        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**arguments)
        self.assertIn("SET_DEFAULT_INVALID", raised.exception.details["errorCodes"])

        operation["payload"]["valueEncoding"] = "STRING"
        draft = self.plans.draft(**arguments)
        self.assertTrue(draft["valid"])

    def test_operation_limit_uses_dedicated_error(self) -> None:
        arguments = self.plan_input()
        template = arguments["operations"][0]
        arguments["operations"] = [
            {**copy.deepcopy(template), "operationId": f"op://create-{index:03d}"}
            for index in range(129)
        ]
        with self.assertRaises(McpExecutionError) as raised:
            self.plans.draft(**arguments)
        self.assertEqual(raised.exception.code, "PATCH_PLAN_LIMIT_EXCEEDED")

    def test_duplicate_nodes_operations_and_missing_capability_are_rejected(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        duplicate_node = self.plan_input()
        duplicate_node["nodes"].append(copy.deepcopy(duplicate_node["nodes"][0]))
        cases.append(("PLAN_NODE_DUPLICATE", duplicate_node))

        duplicate_operation = self.plan_input()
        duplicate_operation["operations"][2]["operationId"] = duplicate_operation[
            "operations"
        ][1]["operationId"]
        cases.append(("OPERATION_ID_DUPLICATE", duplicate_operation))

        missing_capability = self.plan_input()
        missing_capability["capability_requirements"] = ["CREATE_NODE"]
        cases.append(("CAPABILITY_REQUIRED", missing_capability))

        for expected, arguments in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(McpExecutionError) as raised:
                    self.plans.draft(**arguments)
                self.assertEqual(raised.exception.code, "PATCH_PLAN_INVALID")
                self.assertIn(expected, raised.exception.details["errorCodes"])

    def test_blocked_draft_cannot_confirm(self) -> None:
        draft = self.draft(blocking_questions=["Need runtime authority proof"])
        validation = self.plans.validate(self.task["taskId"], draft["planId"])
        self.assertTrue(validation["valid"])
        self.assertFalse(validation["confirmable"])

        with self.assertRaises(McpExecutionError) as raised:
            self.plans.confirm(
                task_id=self.task["taskId"],
                plan_id=draft["planId"],
                expected_semantic_digest=draft["semanticDigest"],
                confirm=True,
            )
        self.assertEqual(raised.exception.code, "PATCH_PLAN_NOT_CONFIRMABLE")

    def test_confirm_requires_true_and_exact_semantic_digest(self) -> None:
        draft = self.draft()
        with self.assertRaises(McpExecutionError) as raised:
            self.plans.confirm(
                task_id=self.task["taskId"],
                plan_id=draft["planId"],
                expected_semantic_digest=draft["semanticDigest"],
                confirm=False,
            )
        self.assertEqual(raised.exception.code, "PLAN_CONFIRMATION_REQUIRED")

        with self.assertRaises(McpExecutionError) as raised:
            self.plans.confirm(
                task_id=self.task["taskId"],
                plan_id=draft["planId"],
                expected_semantic_digest="0" * 64,
                confirm=True,
            )
        self.assertEqual(raised.exception.code, "PATCH_PLAN_DIGEST_MISMATCH")

        confirmed = self.plans.confirm(
            task_id=self.task["taskId"],
            plan_id=draft["planId"],
            expected_semantic_digest=draft["semanticDigest"],
            confirm=True,
        )
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["semanticDigest"], draft["semanticDigest"])
        self.assertFalse(confirmed["executionReady"])
        self.assertEqual(confirmed["nextPhase"], "READ_ONLY_EDITOR_BRIDGE")
        self.assertEqual(self.store.load_session(self.task["taskId"])["phase"], "PLAN_CONFIRMED")

    def test_confirm_revalidates_a_stored_draft_against_create_closure(self) -> None:
        draft = self.draft()
        plan = self.store.load_plan(self.task["taskId"], draft["planId"])
        plan["operations"] = [
            operation
            for operation in plan["operations"]
            if operation.get("kind") != "CREATE_NODE"
        ]
        plan["operations"][0]["dependsOn"] = []
        plan["semanticDigest"] = plan_semantic_digest(plan)
        self.store.save_plan(self.task["taskId"], draft["planId"], plan)

        with self.assertRaises(McpExecutionError) as raised:
            self.plans.confirm(
                task_id=self.task["taskId"],
                plan_id=draft["planId"],
                expected_semantic_digest=plan["semanticDigest"],
                confirm=True,
            )

        self.assertEqual(raised.exception.code, "PATCH_PLAN_NOT_CONFIRMABLE")
        self.assertIn(
            "PROPOSED_NODE_CREATE_MISSING",
            raised.exception.details["errorCodes"],
        )

    def test_confirm_fails_closed_after_revision_change(self) -> None:
        draft = self.draft()
        changed = interpretation_payload()
        changed["graphs"][0]["payload"]["metadata"]["confidence"] = "medium"
        publish_interpretation_fixture(self.capture_root, payload=changed)
        publish_interpretation(self.asset_dir, budget=32_000)

        with self.assertRaises(McpExecutionError) as raised:
            self.plans.confirm(
                task_id=self.task["taskId"],
                plan_id=draft["planId"],
                expected_semantic_digest=draft["semanticDigest"],
                confirm=True,
            )
        self.assertEqual(raised.exception.code, "EVIDENCE_REVISION_CHANGED")
        self.assertEqual(self.store.load_session(self.task["taskId"])["phase"], "BLOCKED")

    def test_digest_is_deterministic_and_renderer_names_exact_pins(self) -> None:
        first = self.draft()
        second = self.draft()
        self.assertNotEqual(first["planId"], second["planId"])
        self.assertEqual(first["semanticDigest"], second["semanticDigest"])

        markdown = render_patch_plan(first)
        self.assertIn(str(self.event_output["ref"]), markdown)
        self.assertIn(str(self.sequence_input["ref"]), markdown)
        self.assertIn("executionReady: false", markdown)

        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "render_blueprint_patch_plan.py"),
                "--task",
                self.task["taskId"],
                "--plan",
                first["planId"],
                "--task-root",
                str(self.store.root),
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONUTF8": "1"},
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn(str(self.event_output["ref"]), process.stdout)
        self.assertNotIn(str(self.store.root), process.stdout)

    def test_plan_metadata_does_not_mutate_evidence_or_interpretation(self) -> None:
        protected = sorted(path for path in self.asset_dir.rglob("*") if path.is_file())
        before = {path: _sha256(path) for path in protected}
        draft = self.draft()
        self.plans.validate(self.task["taskId"], draft["planId"])
        after = {path: _sha256(path) for path in protected}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
