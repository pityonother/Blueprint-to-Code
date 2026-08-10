"""Exact-reference structural validator for Blueprint Patch Plan v1."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence

from ..blueprint_service import BlueprintService
from ..contracts import McpExecutionError, assert_path_free
from .canonical import semantic_digest
from .contracts import PATCH_PLAN_SCHEMA
from .plan_contracts import (
    KNOWN_CAPABILITIES,
    MAX_PLAN_CHECKPOINTS,
    MAX_PLAN_NODES,
    MAX_PLAN_OPERATIONS,
    OPERATION_CAPABILITY,
    PATCH_OPERATION_KINDS,
)


_PLAN_NODE_ID = re.compile(r"^plan-node://[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLAN_ID = re.compile(r"^patch-plan://[0-9a-f]{32}$")
_OPERATION_ID = re.compile(r"^op://[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CHECKPOINT_ID = re.compile(
    r"^checkpoint://[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_QUESTION_ID = re.compile(r"^question://[0-9a-f]{24}$")
_MISSING = object()
_NODE_SIGNATURE_KEYS = frozenset(
    {
        "nodeFamily",
        "className",
        "functionOwner",
        "functionName",
        "variableName",
        "eventName",
        "pure",
        "pinSignatures",
    }
)
_PIN_SIGNATURE_KEYS = frozenset(
    {
        "name",
        "direction",
        "category",
        "subcategory",
        "ordinal",
        "containerType",
    }
)


def plan_semantic_digest(plan: Mapping[str, object]) -> str:
    semantic = copy.deepcopy(dict(plan))
    semantic.pop("status", None)
    return semantic_digest(semantic)


def _direction(value: object) -> str:
    raw = str(value or "")
    if raw in {"OUTPUT", "EGPD_Output"}:
        return "OUTPUT"
    if raw in {"INPUT", "EGPD_Input"}:
        return "INPUT"
    return raw


def _pin_ordinal(pin: Mapping[str, object]) -> int:
    try:
        return int(str(pin.get("ref") or "").rsplit("/", 1)[-1])
    except ValueError:
        return -1


class PlanValidator:
    def __init__(self, blueprint: BlueprintService) -> None:
        self.blueprint = blueprint

    def validate(
        self,
        context: dict[str, object],
        plan: dict[str, object],
    ) -> dict[str, object]:
        errors: list[dict[str, object]] = []
        warnings: list[dict[str, object]] = [
            {
                "code": "PIN_TYPE_COMPATIBILITY_NOT_VALIDATED",
                "message": "Pin type compatibility is not validated in Phase 2.",
            },
            {
                "code": "DEVKIT_CREATE_AVAILABILITY_NOT_VALIDATED",
                "message": "DevKit node creation, compile, and save availability are not validated.",
            },
        ]

        def add_error(code: str, message: str, ref: str = "") -> None:
            item: dict[str, object] = {"code": code, "message": message}
            if ref:
                item["ref"] = ref
            if item not in errors:
                errors.append(item)

        if plan.get("schema") != PATCH_PLAN_SCHEMA:
            add_error("SCHEMA_INVALID", "Patch Plan schema is invalid.")
        if _PLAN_ID.fullmatch(str(plan.get("planId") or "")) is None:
            add_error("PLAN_IDENTITY_INVALID", "Patch Plan ID is invalid.")
        if plan.get("taskId") != context.get("taskId"):
            add_error("TASK_IDENTITY_MISMATCH", "Patch Plan task identity is invalid.")
        if plan.get("status") not in {"DRAFT", "CONFIRMED"}:
            add_error("PLAN_STATUS_INVALID", "Patch Plan status is invalid.")

        graph_scope = [str(item.get("ref") or "") for item in context["graphTargets"]]
        primary = context["primaryAsset"]
        expected_target = {
            "assetId": primary["assetId"],
            "objectPath": primary["objectPath"],
            "evidenceRevisionId": primary["evidenceRevisionId"],
            "evidenceManifestSha256": primary["evidenceManifestSha256"],
            "graphRefs": graph_scope,
        }
        if plan.get("target") != expected_target:
            add_error(
                "TARGET_IDENTITY_MISMATCH",
                "Patch Plan target must equal the Task Evidence identity and graph scope.",
            )

        nodes = plan.get("nodes")
        operations = plan.get("operations")
        checkpoints = plan.get("checkpoints")
        blockers = plan.get("blockingQuestions")
        if not isinstance(nodes, list):
            add_error("NODES_INVALID", "Patch Plan nodes must be an array.")
            nodes = []
        if not isinstance(operations, list):
            add_error("OPERATIONS_INVALID", "Patch Plan operations must be an array.")
            operations = []
        if not isinstance(checkpoints, list):
            add_error("CHECKPOINTS_INVALID", "Patch Plan checkpoints must be an array.")
            checkpoints = []
        if not isinstance(blockers, list):
            add_error("BLOCKERS_INVALID", "Patch Plan blockingQuestions must be an array.")
            blockers = []
        if len(nodes) > MAX_PLAN_NODES:
            add_error("PLAN_NODE_LIMIT_EXCEEDED", "Patch Plan exceeds 64 nodes.")
        if len(operations) > MAX_PLAN_OPERATIONS:
            add_error("PLAN_OPERATION_LIMIT_EXCEEDED", "Patch Plan exceeds 128 operations.")
        if len(checkpoints) > MAX_PLAN_CHECKPOINTS:
            add_error("PLAN_CHECKPOINT_LIMIT_EXCEEDED", "Patch Plan exceeds 32 checkpoints.")
        if len(blockers) > 20:
            add_error("PLAN_BLOCKER_LIMIT_EXCEEDED", "Patch Plan exceeds 20 blockers.")
        for blocker in blockers:
            if not isinstance(blocker, Mapping):
                add_error("BLOCKER_INVALID", "A blocking question is not an object.")
                continue
            question_id = str(blocker.get("questionId") or "")
            text = str(blocker.get("text") or "")
            if (
                _QUESTION_ID.fullmatch(question_id) is None
                or not text
                or len(text) > 1000
            ):
                add_error("BLOCKER_INVALID", "A blocking question is invalid.", question_id)

        checkpoint_ids: set[str] = set()
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, Mapping):
                add_error("CHECKPOINT_INVALID", "A checkpoint is not an object.")
                continue
            checkpoint_id = str(checkpoint.get("checkpointId") or "")
            if _CHECKPOINT_ID.fullmatch(checkpoint_id) is None:
                add_error("CHECKPOINT_INVALID", "Checkpoint ID is invalid.", checkpoint_id)
            elif checkpoint_id in checkpoint_ids:
                add_error("CHECKPOINT_DUPLICATE", "Checkpoint ID is duplicated.", checkpoint_id)
            checkpoint_ids.add(checkpoint_id)
            description = str(checkpoint.get("description") or "")
            if not description or len(description) > 1000:
                add_error(
                    "CHECKPOINT_INVALID",
                    "Checkpoint description is invalid.",
                    checkpoint_id,
                )

        existing_nodes: dict[str, dict[str, object]] = {}
        proposed_nodes: dict[str, dict[str, object]] = {}
        node_identities: set[str] = set()
        for node in nodes:
            if not isinstance(node, Mapping):
                add_error("PLAN_NODE_INVALID", "A plan node is not an object.")
                continue
            graph_ref = str(node.get("graphRef") or "")
            if graph_ref not in graph_scope:
                add_error("GRAPH_SCOPE_INVALID", "Plan node is outside Task graph scope.", graph_ref)
            signature = node.get("signature")
            self._validate_node_signature(signature, add_error)
            node_ref = str(node.get("nodeRef") or "")
            local_id = str(node.get("localPlanNodeId") or "")
            if bool(node_ref) == bool(local_id):
                add_error(
                    "PLAN_NODE_IDENTITY_INVALID",
                    "A plan node must identify exactly one existing or proposed node.",
                )
                continue
            identity = node_ref or local_id
            if identity in node_identities:
                add_error("PLAN_NODE_DUPLICATE", "Plan node identity is duplicated.", identity)
                continue
            node_identities.add(identity)
            if node_ref:
                if not node_ref.startswith("bp://") or "/n/" not in node_ref:
                    add_error("EXISTING_NODE_REF_INVALID", "Existing nodeRef is invalid.", node_ref)
                    continue
                try:
                    current = self.blueprint.get_node(
                        asset=str(primary["name"]),
                        node_ref=node_ref,
                        include_neighborhood=False,
                        max_hops=0,
                    )
                except McpExecutionError as exc:
                    add_error("EXISTING_NODE_REF_INVALID", exc.message, node_ref)
                    continue
                exact_node = dict(current["node"])
                if exact_node.get("graphRef") != graph_ref:
                    add_error(
                        "EXISTING_NODE_GRAPH_MISMATCH",
                        "Existing nodeRef does not belong to its declared graphRef.",
                        node_ref,
                    )
                self._validate_existing_signature(signature, exact_node, add_error, node_ref)
                existing_nodes[node_ref] = {
                    "node": exact_node,
                    "pins": {
                        str(pin.get("ref") or ""): dict(pin)
                        for pin in current.get("pins", [])
                    },
                    "edges": frozenset(
                        (
                            str(edge.get("sourcePinRef") or ""),
                            str(edge.get("targetPinRef") or ""),
                        )
                        for edge in current.get("edges", [])
                        if edge.get("sourcePinRef") and edge.get("targetPinRef")
                    ),
                    "graphRef": graph_ref,
                }
            else:
                if _PLAN_NODE_ID.fullmatch(local_id) is None:
                    add_error("PROPOSED_NODE_ID_INVALID", "localPlanNodeId is invalid.", local_id)
                    continue
                proposed_nodes[local_id] = {
                    "signature": signature,
                    "graphRef": graph_ref,
                }

        capabilities = plan.get("capabilityRequirements")
        if not isinstance(capabilities, list):
            add_error("CAPABILITIES_INVALID", "capabilityRequirements must be an array.")
            capabilities = []
        capability_names = [str(item) for item in capabilities]
        if len(capability_names) != len(set(capability_names)):
            add_error("CAPABILITY_DUPLICATE", "Capability requirement is duplicated.")
        for capability in capability_names:
            if capability not in KNOWN_CAPABILITIES:
                add_error("CAPABILITY_UNKNOWN", "Capability requirement is unknown.", capability)

        operation_ids: list[str] = []
        dependencies: dict[str, list[str]] = {}
        for operation in operations:
            if not isinstance(operation, Mapping):
                add_error("OPERATION_INVALID", "A Patch operation is not an object.")
                continue
            operation_id = str(operation.get("operationId") or "")
            kind = str(operation.get("kind") or "")
            graph_ref = str(operation.get("graphRef") or "")
            if _OPERATION_ID.fullmatch(operation_id) is None:
                add_error("OPERATION_ID_INVALID", "operationId is invalid.", operation_id)
            if operation_id in operation_ids:
                add_error("OPERATION_ID_DUPLICATE", "operationId is duplicated.", operation_id)
            operation_ids.append(operation_id)
            if kind not in PATCH_OPERATION_KINDS:
                add_error("OPERATION_KIND_INVALID", "Patch operation kind is invalid.", operation_id)
            if graph_ref not in graph_scope:
                add_error("GRAPH_SCOPE_INVALID", "Patch operation is outside Task graph scope.", operation_id)
            raw_dependencies = operation.get("dependsOn")
            if isinstance(raw_dependencies, (str, bytes)) or not isinstance(
                raw_dependencies, Sequence
            ):
                add_error("OPERATION_DEPENDENCIES_INVALID", "dependsOn must be an array.", operation_id)
                dependencies[operation_id] = []
            else:
                dependencies[operation_id] = [str(item) for item in raw_dependencies]
            checkpoint = str(operation.get("checkpoint") or "")
            if not checkpoint or checkpoint not in checkpoint_ids:
                add_error("CHECKPOINT_NOT_FOUND", "Operation checkpoint was not declared.", operation_id)
            for required in ("preconditions", "payload", "postconditions"):
                if required not in operation:
                    add_error("OPERATION_FIELD_MISSING", f"Operation is missing {required}.", operation_id)
            for sequence_field in ("preconditions", "postconditions"):
                value = operation.get(sequence_field)
                if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                    add_error(
                        "OPERATION_FIELD_INVALID",
                        f"Operation {sequence_field} must be an array.",
                        operation_id,
                    )

            required_capability = OPERATION_CAPABILITY.get(kind)
            if required_capability and required_capability not in capability_names:
                add_error(
                    "CAPABILITY_REQUIRED",
                    f"Operation requires {required_capability}.",
                    operation_id,
                )
            self._validate_operation(
                operation,
                graph_ref=graph_ref,
                existing_nodes=existing_nodes,
                proposed_nodes=proposed_nodes,
                add_error=add_error,
            )

        known_operations = set(operation_ids)
        for operation_id, refs in dependencies.items():
            for dependency in refs:
                if dependency not in known_operations:
                    add_error(
                        "OPERATION_DEPENDENCY_NOT_FOUND",
                        "dependsOn references an unknown operation.",
                        operation_id,
                    )
        if self._has_cycle(dependencies):
            add_error(
                "OPERATION_DEPENDENCY_CYCLE",
                "Patch operation dependsOn graph contains a cycle.",
            )

        expected_digest = plan_semantic_digest(plan)
        if str(plan.get("semanticDigest") or "") != expected_digest:
            add_error(
                "SEMANTIC_DIGEST_INVALID",
                "Patch Plan semantic digest is not deterministic for its content.",
            )
        try:
            assert_path_free(plan)
        except McpExecutionError:
            add_error("PATH_DATA_FORBIDDEN", "Patch Plan contains machine-local path data.")

        valid = not errors
        confirmable = bool(valid and not blockers and not context["blockingQuestions"])
        result: dict[str, object] = {
            "valid": valid,
            "confirmable": confirmable,
            "errors": errors,
            "warnings": warnings,
            "capabilityRequirements": capability_names,
            "executionReady": False,
            "reason": "EDITOR_BRIDGE_NOT_INSTALLED",
            "semanticDigest": str(plan.get("semanticDigest") or ""),
            "humanSummary": self.human_summary(
                plan,
                goal=str(context.get("goal") or ""),
                valid=valid,
                confirmable=confirmable,
            ),
        }
        assert_path_free(result)
        return result

    @staticmethod
    def _validate_node_signature(
        signature: object,
        add_error: object,
    ) -> None:
        if not isinstance(signature, Mapping):
            add_error("NODE_SIGNATURE_INVALID", "NodeSignature is not an object.")
            return
        if not _NODE_SIGNATURE_KEYS.issubset(signature):
            add_error("NODE_SIGNATURE_INCOMPLETE", "NodeSignature is missing required fields.")
            return
        if not str(signature.get("nodeFamily") or "") or not str(
            signature.get("className") or ""
        ):
            add_error("NODE_SIGNATURE_INCOMPLETE", "NodeSignature family and class are required.")
        if not isinstance(signature.get("pure"), bool):
            add_error("NODE_SIGNATURE_INVALID", "NodeSignature pure must be boolean.")
        pins = signature.get("pinSignatures")
        if not isinstance(pins, list):
            add_error("PIN_SIGNATURE_INVALID", "pinSignatures must be an array.")
            return
        seen: set[tuple[str, str, int]] = set()
        for pin in pins:
            if not isinstance(pin, Mapping) or not _PIN_SIGNATURE_KEYS.issubset(pin):
                add_error("PIN_SIGNATURE_INCOMPLETE", "PinSignature is missing required fields.")
                continue
            direction = str(pin.get("direction") or "")
            try:
                ordinal = int(pin.get("ordinal"))
            except (TypeError, ValueError):
                ordinal = -1
            if not str(pin.get("name") or "") or direction not in {"INPUT", "OUTPUT"} or ordinal < 0:
                add_error("PIN_SIGNATURE_INVALID", "PinSignature name, direction, or ordinal is invalid.")
            key = (str(pin.get("name") or ""), direction, ordinal)
            if key in seen:
                add_error("PIN_SIGNATURE_DUPLICATE", "PinSignature is duplicated.")
            seen.add(key)

    @staticmethod
    def _validate_existing_signature(
        signature: object,
        node: Mapping[str, object],
        add_error: object,
        node_ref: str,
    ) -> None:
        if not isinstance(signature, Mapping):
            return
        if signature.get("className") != node.get("className"):
            add_error(
                "NODE_SIGNATURE_MISMATCH",
                "Existing node className does not match current Evidence.",
                node_ref,
            )
        signals = node.get("signals") if isinstance(node.get("signals"), Mapping) else {}
        for signature_key, signal_key in (
            ("functionName", "function"),
            ("variableName", "variable"),
            ("eventName", "event"),
        ):
            expected = str(signature.get(signature_key) or "")
            if expected and expected != str(signals.get(signal_key) or ""):
                add_error(
                    "NODE_SIGNATURE_MISMATCH",
                    f"Existing node {signature_key} does not match current Evidence.",
                    node_ref,
                )

    def _validate_operation(
        self,
        operation: Mapping[str, object],
        *,
        graph_ref: str,
        existing_nodes: Mapping[str, dict[str, object]],
        proposed_nodes: Mapping[str, dict[str, object]],
        add_error: object,
    ) -> None:
        operation_id = str(operation.get("operationId") or "")
        kind = str(operation.get("kind") or "")
        payload = operation.get("payload")
        if not isinstance(payload, Mapping):
            add_error("OPERATION_PAYLOAD_INVALID", "Operation payload must be an object.", operation_id)
            payload = {}
        preconditions = operation.get("preconditions")
        if isinstance(preconditions, (str, bytes)) or not isinstance(
            preconditions, Sequence
        ):
            preconditions = ()
        if kind == "CREATE_NODE":
            local_id = str(payload.get("localPlanNodeId") or "")
            if local_id not in proposed_nodes:
                add_error("PROPOSED_NODE_NOT_FOUND", "CREATE_NODE target was not declared.", operation_id)
            self._validate_operation_graph(
                local_id,
                graph_ref,
                operation_id,
                existing_nodes,
                proposed_nodes,
                add_error,
            )
        elif kind == "DELETE_NODE":
            node_ref = str(payload.get("nodeRef") or operation.get("nodeRef") or "")
            if node_ref not in existing_nodes:
                add_error("EXISTING_NODE_REF_INVALID", "DELETE_NODE requires an exact existing nodeRef.", operation_id)
            self._validate_operation_graph(
                node_ref,
                graph_ref,
                operation_id,
                existing_nodes,
                proposed_nodes,
                add_error,
            )
            if not self._contains_exact(preconditions, node_ref):
                add_error(
                    "DELETE_PRECONDITION_INVALID",
                    "DELETE_NODE preconditions must contain its current exact nodeRef.",
                    operation_id,
                )
        elif kind in {"CONNECT", "DISCONNECT"}:
            for endpoint in (operation.get("from"), operation.get("to")):
                identity = (
                    str(endpoint.get("node") or "")
                    if isinstance(endpoint, Mapping)
                    else ""
                )
                self._validate_operation_graph(
                    identity,
                    graph_ref,
                    operation_id,
                    existing_nodes,
                    proposed_nodes,
                    add_error,
                )
            from_direction = self._validate_endpoint(
                operation.get("from"),
                role="from",
                operation_id=operation_id,
                existing_nodes=existing_nodes,
                proposed_nodes=proposed_nodes,
                add_error=add_error,
                require_existing=kind == "DISCONNECT",
            )
            to_direction = self._validate_endpoint(
                operation.get("to"),
                role="to",
                operation_id=operation_id,
                existing_nodes=existing_nodes,
                proposed_nodes=proposed_nodes,
                add_error=add_error,
                require_existing=kind == "DISCONNECT",
            )
            if from_direction != "OUTPUT" or to_direction != "INPUT":
                add_error(
                    "CONNECT_DIRECTION_INVALID",
                    "CONNECT/DISCONNECT endpoints must be OUTPUT to INPUT.",
                    operation_id,
                )
            if kind == "DISCONNECT":
                source_pin_ref = self._endpoint_pin_ref(operation.get("from"))
                target_pin_ref = self._endpoint_pin_ref(operation.get("to"))
                if (
                    not source_pin_ref
                    or not target_pin_ref
                    or not self._contains_exact(preconditions, source_pin_ref)
                    or not self._contains_exact(preconditions, target_pin_ref)
                    or not self._edge_exists(
                        existing_nodes,
                        source_pin_ref,
                        target_pin_ref,
                    )
                ):
                    add_error(
                        "DISCONNECT_PRECONDITION_INVALID",
                        "DISCONNECT requires an exact current Evidence edge precondition.",
                        operation_id,
                    )
        elif kind == "SET_DEFAULT":
            node_ref = str(payload.get("nodeRef") or "")
            local_id = str(payload.get("localPlanNodeId") or "")
            identity = node_ref or local_id
            self._validate_operation_graph(
                identity,
                graph_ref,
                operation_id,
                existing_nodes,
                proposed_nodes,
                add_error,
            )
            if (
                bool(node_ref) == bool(local_id)
                or "newValue" not in payload
                or not str(payload.get("valueEncoding") or "")
            ):
                add_error("SET_DEFAULT_INVALID", "SET_DEFAULT target or newValue is invalid.", operation_id)
            elif node_ref:
                pin_ref = str(payload.get("pinRef") or "")
                node = existing_nodes.get(node_ref)
                pin = node["pins"].get(pin_ref) if node is not None else None
                old_value = self._default_precondition(
                    preconditions,
                    node_ref=node_ref,
                    pin_ref=pin_ref,
                )
                if (
                    pin is None
                    or old_value is _MISSING
                    or old_value != pin.get("default")
                ):
                    add_error(
                        "SET_DEFAULT_PRECONDITION_INVALID",
                        "Existing SET_DEFAULT requires the exact current Pin default precondition.",
                        operation_id,
                    )
            elif local_id not in proposed_nodes or not self._proposed_pin_matches(
                proposed_nodes[local_id],
                payload.get("pinSignature"),
            ):
                add_error(
                    "SET_DEFAULT_INVALID",
                    "Proposed SET_DEFAULT target or PinSignature is invalid.",
                    operation_id,
                )
        elif kind == "MOVE_NODE":
            identity = str(payload.get("nodeRef") or payload.get("localPlanNodeId") or "")
            self._validate_operation_graph(
                identity,
                graph_ref,
                operation_id,
                existing_nodes,
                proposed_nodes,
                add_error,
            )
            if identity not in existing_nodes and identity not in proposed_nodes:
                add_error("MOVE_NODE_TARGET_INVALID", "MOVE_NODE target is invalid.", operation_id)
            if not isinstance(payload.get("x"), (int, float)) or not isinstance(
                payload.get("y"), (int, float)
            ):
                add_error("MOVE_NODE_COORDINATES_INVALID", "MOVE_NODE requires graph-space x/y.", operation_id)
        elif kind == "ADD_COMMENT":
            text = str(payload.get("text") or "")
            if not text or len(text) > 4000:
                add_error(
                    "COMMENT_PAYLOAD_INVALID",
                    "ADD_COMMENT requires bounded non-empty text.",
                    operation_id,
                )
        elif kind == "PRESERVE":
            node_ref = str(payload.get("nodeRef") or "")
            pin_ref = str(payload.get("pinRef") or "")
            if node_ref:
                self._validate_operation_graph(
                    node_ref,
                    graph_ref,
                    operation_id,
                    existing_nodes,
                    proposed_nodes,
                    add_error,
                )
                current = existing_nodes.get(node_ref)
                if current is None:
                    add_error(
                        "PRESERVE_PRECONDITION_INVALID",
                        "PRESERVE nodeRef is not an exact declared existing node.",
                        operation_id,
                    )
                elif pin_ref and pin_ref not in current["pins"]:
                    add_error(
                        "PIN_OWNERSHIP_MISMATCH",
                        "PRESERVE pinRef does not belong to its nodeRef.",
                        operation_id,
                    )
                if not self._contains_exact(preconditions, node_ref) or (
                    pin_ref and not self._contains_exact(preconditions, pin_ref)
                ):
                    add_error(
                        "PRESERVE_PRECONDITION_INVALID",
                        "PRESERVE preconditions must contain its exact Evidence refs.",
                        operation_id,
                    )
            elif operation.get("from") and operation.get("to"):
                for endpoint in (operation.get("from"), operation.get("to")):
                    identity = (
                        str(endpoint.get("node") or "")
                        if isinstance(endpoint, Mapping)
                        else ""
                    )
                    self._validate_operation_graph(
                        identity,
                        graph_ref,
                        operation_id,
                        existing_nodes,
                        proposed_nodes,
                        add_error,
                    )
                self._validate_endpoint(
                    operation.get("from"),
                    role="from",
                    operation_id=operation_id,
                    existing_nodes=existing_nodes,
                    proposed_nodes=proposed_nodes,
                    add_error=add_error,
                    require_existing=True,
                )
                source_pin_ref = self._endpoint_pin_ref(operation.get("from"))
                target_pin_ref = self._endpoint_pin_ref(operation.get("to"))
                if (
                    not self._contains_exact(preconditions, source_pin_ref)
                    or not self._contains_exact(preconditions, target_pin_ref)
                    or not self._edge_exists(
                        existing_nodes,
                        source_pin_ref,
                        target_pin_ref,
                    )
                ):
                    add_error(
                        "PRESERVE_PRECONDITION_INVALID",
                        "PRESERVE edge preconditions must identify a current exact Evidence edge.",
                        operation_id,
                    )
                self._validate_endpoint(
                    operation.get("to"),
                    role="to",
                    operation_id=operation_id,
                    existing_nodes=existing_nodes,
                    proposed_nodes=proposed_nodes,
                    add_error=add_error,
                    require_existing=True,
                )
            else:
                add_error(
                    "PRESERVE_PRECONDITION_INVALID",
                    "PRESERVE requires an exact node/pin or edge endpoint precondition.",
                    operation_id,
                )

    @staticmethod
    def _validate_operation_graph(
        identity: str,
        graph_ref: str,
        operation_id: str,
        existing_nodes: Mapping[str, dict[str, object]],
        proposed_nodes: Mapping[str, dict[str, object]],
        add_error: object,
    ) -> None:
        node = existing_nodes.get(identity) or proposed_nodes.get(identity)
        if node is not None and node.get("graphRef") != graph_ref:
            add_error(
                "GRAPH_SCOPE_INVALID",
                "Operation node does not belong to its declared graphRef.",
                operation_id,
            )

    @staticmethod
    def _contains_exact(value: object, expected: str) -> bool:
        if not expected:
            return False
        if isinstance(value, Mapping):
            return any(
                PlanValidator._contains_exact(item, expected)
                for item in (*value.keys(), *value.values())
            )
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return any(PlanValidator._contains_exact(item, expected) for item in value)
        return value == expected

    @staticmethod
    def _endpoint_pin_ref(endpoint: object) -> str:
        if not isinstance(endpoint, Mapping) or not isinstance(endpoint.get("pin"), Mapping):
            return ""
        return str(endpoint["pin"].get("pinRef") or "")

    @staticmethod
    def _edge_exists(
        existing_nodes: Mapping[str, dict[str, object]],
        source_pin_ref: str,
        target_pin_ref: str,
    ) -> bool:
        edge = (source_pin_ref, target_pin_ref)
        return any(edge in node.get("edges", ()) for node in existing_nodes.values())

    @staticmethod
    def _default_precondition(
        preconditions: Sequence[object],
        *,
        node_ref: str,
        pin_ref: str,
    ) -> object:
        for item in preconditions:
            if (
                isinstance(item, Mapping)
                and item.get("nodeRef") == node_ref
                and item.get("pinRef") == pin_ref
                and "oldValue" in item
            ):
                return item["oldValue"]
        return _MISSING

    @staticmethod
    def _proposed_pin_matches(
        proposed_node: Mapping[str, object],
        pin_signature: object,
    ) -> bool:
        if not isinstance(pin_signature, Mapping) or not _PIN_SIGNATURE_KEYS.issubset(
            pin_signature
        ):
            return False
        signature = proposed_node.get("signature")
        candidates = (
            signature.get("pinSignatures", [])
            if isinstance(signature, Mapping)
            else []
        )
        return sum(
            1
            for candidate in candidates
            if isinstance(candidate, Mapping)
            and all(candidate.get(key) == pin_signature.get(key) for key in _PIN_SIGNATURE_KEYS)
        ) == 1

    @staticmethod
    def _validate_endpoint(
        endpoint: object,
        *,
        role: str,
        operation_id: str,
        existing_nodes: Mapping[str, dict[str, object]],
        proposed_nodes: Mapping[str, dict[str, object]],
        add_error: object,
        require_existing: bool,
    ) -> str:
        if not isinstance(endpoint, Mapping) or not isinstance(endpoint.get("pin"), Mapping):
            add_error("CONNECT_ENDPOINT_INVALID", f"{role} endpoint is invalid.", operation_id)
            return ""
        node_id = str(endpoint.get("node") or "")
        pin = endpoint["pin"]
        claimed_direction = str(pin.get("direction") or "")
        claimed_name = str(pin.get("name") or "")
        try:
            claimed_ordinal = int(pin.get("ordinal"))
        except (TypeError, ValueError):
            claimed_ordinal = -1
        if claimed_direction not in {"INPUT", "OUTPUT"} or not claimed_name or claimed_ordinal < 0:
            add_error("CONNECT_ENDPOINT_INVALID", f"{role} Pin endpoint is incomplete.", operation_id)
        if node_id in existing_nodes:
            pin_ref = str(pin.get("pinRef") or "")
            exact_pin = existing_nodes[node_id]["pins"].get(pin_ref)
            if exact_pin is None or exact_pin.get("nodeRef") != node_id:
                add_error(
                    "PIN_OWNERSHIP_MISMATCH",
                    f"{role} pinRef does not belong to its existing nodeRef.",
                    operation_id,
                )
                return claimed_direction
            actual_direction = _direction(exact_pin.get("direction"))
            if (
                actual_direction != claimed_direction
                or str(exact_pin.get("name") or "") != claimed_name
                or _pin_ordinal(exact_pin) != claimed_ordinal
            ):
                add_error(
                    "PIN_ENDPOINT_MISMATCH",
                    f"{role} Pin endpoint does not match current Evidence.",
                    operation_id,
                )
            return claimed_direction
        if node_id in proposed_nodes and not require_existing:
            if str(pin.get("pinRef") or ""):
                add_error(
                    "PROPOSED_PIN_REF_FORBIDDEN",
                    "Proposed nodes cannot claim an existing pinRef.",
                    operation_id,
                )
            signature = proposed_nodes[node_id].get("signature")
            pin_signatures = (
                signature.get("pinSignatures", [])
                if isinstance(signature, Mapping)
                else []
            )
            matches = [
                candidate
                for candidate in pin_signatures
                if isinstance(candidate, Mapping)
                and candidate.get("name") == claimed_name
                and candidate.get("direction") == claimed_direction
                and candidate.get("ordinal") == claimed_ordinal
            ]
            if len(matches) != 1:
                add_error(
                    "PROPOSED_PIN_SIGNATURE_MISMATCH",
                    f"{role} proposed Pin endpoint has no unique PinSignature.",
                    operation_id,
                )
            return claimed_direction
        add_error("CONNECT_NODE_NOT_FOUND", f"{role} node was not declared in the plan.", operation_id)
        return claimed_direction

    @staticmethod
    def _has_cycle(dependencies: Mapping[str, Sequence[str]]) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for dependency in dependencies.get(node, []):
                if dependency in dependencies and visit(dependency):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in dependencies)

    @staticmethod
    def human_summary(
        plan: Mapping[str, object],
        *,
        goal: str,
        valid: bool,
        confirmable: bool,
    ) -> str:
        operations = plan.get("operations") if isinstance(plan.get("operations"), list) else []
        nodes = plan.get("nodes") if isinstance(plan.get("nodes"), list) else []
        connections = [item for item in operations if item.get("kind") == "CONNECT"]
        defaults = [item for item in operations if item.get("kind") == "SET_DEFAULT"]
        deletes = [item for item in operations if item.get("kind") == "DELETE_NODE"]
        proposed = [item for item in nodes if item.get("localPlanNodeId")]
        target = plan.get("target") if isinstance(plan.get("target"), Mapping) else {}
        connection_lines: list[str] = []
        for operation in connections[:8]:
            from_endpoint = PlanValidator._summary_endpoint(operation.get("from"))
            to_endpoint = PlanValidator._summary_endpoint(operation.get("to"))
            connection_lines.append(
                f"{operation.get('operationId', '')}: {from_endpoint} -> {to_endpoint}"
            )
        if len(connections) > len(connection_lines):
            connection_lines.append(
                f"... {len(connections) - len(connection_lines)} additional connections"
            )
        connection_summary = "\n".join(connection_lines) or "(none)"
        return (
            f"目标: {goal}\n"
            f"Evidence revision: {target.get('evidenceRevisionId', '')}\n"
            f"Target graphs: {len(target.get('graphRefs', []))}\n"
            f"新增节点: {len(proposed)}; 删除节点: {len(deletes)}\n"
            f"SET_DEFAULT: {len(defaults)}; 精确 Pin-to-Pin connections: {len(connections)}\n"
            f"Connections:\n{connection_summary}\n"
            f"Capabilities: {', '.join(str(item) for item in plan.get('capabilityRequirements', []))}\n"
            f"Blockers: {len(plan.get('blockingQuestions', []))}\n"
            f"Status: {plan.get('status', '')}; valid={str(valid).lower()}; confirmable={str(confirmable).lower()}\n"
            "executionReady=false; reason=EDITOR_BRIDGE_NOT_INSTALLED\n"
            "Pin type compatibility is not validated."
        )

    @staticmethod
    def _summary_endpoint(endpoint: object) -> str:
        if not isinstance(endpoint, Mapping) or not isinstance(endpoint.get("pin"), Mapping):
            return "<invalid>"
        pin = endpoint["pin"]
        pin_identity = str(pin.get("pinRef") or "")
        if not pin_identity:
            pin_identity = (
                f"{pin.get('name', '')}/{pin.get('direction', '')}/"
                f"{pin.get('ordinal', '')}"
            )
        return f"{endpoint.get('node', '')}::{pin_identity}"


__all__ = ["PlanValidator", "plan_semantic_digest"]
