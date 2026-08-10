"""Draft, validate, and explicitly confirm local Blueprint Patch Plans."""

from __future__ import annotations

import copy
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from ..blueprint_service import BlueprintService
from ..contracts import McpExecutionError, assert_path_free
from .contracts import PATCH_PLAN_SCHEMA
from .plan_contracts import (
    MAX_PLAN_CHECKPOINTS,
    MAX_PLAN_NODES,
    MAX_PLAN_OPERATIONS,
)
from .plan_validator import PlanValidator, plan_semantic_digest
from .store import TaskStore
from .task_service import TaskService


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class PlanService:
    def __init__(
        self,
        blueprint: BlueprintService,
        tasks: TaskService,
        store: TaskStore,
    ) -> None:
        self.blueprint = blueprint
        self.tasks = tasks
        self.store = store
        self.validator = PlanValidator(blueprint)

    def draft(
        self,
        *,
        task_id: str,
        nodes: Sequence[Mapping[str, object]],
        operations: Sequence[Mapping[str, object]],
        capability_requirements: Sequence[str],
        checkpoints: Sequence[Mapping[str, object]],
        blocking_questions: Sequence[object],
    ) -> dict[str, object]:
        context, session = self.tasks.verified_task(
            task_id,
            allowed_phases={"READY_TO_PLAN", "PLAN_DRAFT"},
        )
        if (
            len(nodes) > MAX_PLAN_NODES
            or len(operations) > MAX_PLAN_OPERATIONS
            or len(checkpoints) > MAX_PLAN_CHECKPOINTS
        ):
            raise McpExecutionError(
                "PATCH_PLAN_LIMIT_EXCEEDED",
                "Blueprint Patch Plan exceeds its bounded node, operation, or checkpoint limit.",
            )
        timestamp = _now()
        plan_id = f"patch-plan://{uuid.uuid4().hex}"
        primary = context["primaryAsset"]
        plan: dict[str, object] = {
            "schema": PATCH_PLAN_SCHEMA,
            "planId": plan_id,
            "taskId": task_id,
            "status": "DRAFT",
            "target": {
                "assetId": primary["assetId"],
                "objectPath": primary["objectPath"],
                "evidenceRevisionId": primary["evidenceRevisionId"],
                "evidenceManifestSha256": primary["evidenceManifestSha256"],
                "graphRefs": [str(item.get("ref") or "") for item in context["graphTargets"]],
            },
            "capabilityRequirements": [str(item) for item in capability_requirements],
            "nodes": copy.deepcopy(list(nodes)),
            "operations": copy.deepcopy(list(operations)),
            "checkpoints": copy.deepcopy(list(checkpoints)),
            "blockingQuestions": self._blocking_questions(blocking_questions),
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        plan["semanticDigest"] = plan_semantic_digest(plan)
        validation = self.validator.validate(context, plan)
        if not validation["valid"]:
            error_codes = [str(item["code"]) for item in validation["errors"]]
            if any(code.endswith("LIMIT_EXCEEDED") for code in error_codes):
                code = "PATCH_PLAN_LIMIT_EXCEEDED"
            else:
                code = "PATCH_PLAN_INVALID"
            raise McpExecutionError(
                code,
                "Blueprint Patch Plan failed structural or exact-reference validation.",
                details={"errorCodes": error_codes, "errorCount": len(error_codes)},
            )
        assert_path_free(plan)
        self.store.save_plan(task_id, plan_id, plan)
        session["phase"] = "PLAN_DRAFT"
        session["patchPlanId"] = plan_id
        session["patchPlanSha256"] = plan["semanticDigest"]
        session["reasonCode"] = ""
        self.tasks.sync_and_save(context, session)
        result = copy.deepcopy(plan)
        result.update(
            {
                "valid": validation["valid"],
                "confirmable": validation["confirmable"],
                "executionReady": False,
                "reason": "EDITOR_BRIDGE_NOT_INSTALLED",
                "humanSummary": validation["humanSummary"],
            }
        )
        assert_path_free(result)
        return result

    def validate(self, task_id: str, plan_id: str) -> dict[str, object]:
        context, _session = self.tasks.verified_task(
            task_id,
            allowed_phases={"PLAN_DRAFT", "PLAN_CONFIRMED"},
        )
        plan = self.store.load_plan(task_id, plan_id)
        if plan.get("taskId") != task_id or plan.get("planId") != plan_id:
            raise McpExecutionError(
                "PATCH_PLAN_INVALID",
                "Stored Blueprint Patch Plan identity is invalid.",
            )
        validation = self.validator.validate(context, plan)
        result: dict[str, object] = {
            "schema": "blueprint-to-code.patch-plan-validation/v1",
            "taskId": task_id,
            "planId": plan_id,
            **validation,
        }
        assert_path_free(result)
        return result

    def confirm(
        self,
        *,
        task_id: str,
        plan_id: str,
        expected_semantic_digest: str,
        confirm: bool,
    ) -> dict[str, object]:
        context, session = self.tasks.verified_task(
            task_id,
            allowed_phases={"PLAN_DRAFT"},
        )
        if not confirm:
            raise McpExecutionError(
                "PLAN_CONFIRMATION_REQUIRED",
                "confirm=true is required after explicit user approval.",
            )
        plan = self.store.load_plan(task_id, plan_id)
        if (
            plan.get("taskId") != task_id
            or plan.get("planId") != plan_id
            or plan.get("status") != "DRAFT"
            or session.get("patchPlanId") != plan_id
        ):
            raise McpExecutionError(
                "PATCH_PLAN_NOT_CONFIRMABLE",
                "Only the current DRAFT plan can be confirmed.",
            )
        actual_digest = str(plan.get("semanticDigest") or "")
        if expected_semantic_digest != actual_digest:
            raise McpExecutionError(
                "PATCH_PLAN_DIGEST_MISMATCH",
                "expectedSemanticDigest does not match the current DRAFT plan.",
            )
        validation = self.validator.validate(context, plan)
        if not validation["valid"] or not validation["confirmable"]:
            raise McpExecutionError(
                "PATCH_PLAN_NOT_CONFIRMABLE",
                "Blueprint Patch Plan still has validation errors or blockers.",
                details={
                    "errorCodes": [
                        str(item["code"]) for item in validation["errors"]
                    ],
                    "blockingQuestionCount": len(plan["blockingQuestions"]),
                },
            )
        plan["status"] = "CONFIRMED"
        plan["updatedAt"] = _now()
        if plan_semantic_digest(plan) != actual_digest:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Plan confirmation changed its semantic digest.",
            )
        self.store.save_plan(task_id, plan_id, plan)
        session["phase"] = "PLAN_CONFIRMED"
        session["patchPlanSha256"] = actual_digest
        session["reasonCode"] = ""
        self.tasks.sync_and_save(context, session)
        result: dict[str, object] = {
            "schema": "blueprint-to-code.patch-plan-confirmation/v1",
            "confirmed": True,
            "planId": plan_id,
            "semanticDigest": actual_digest,
            "capabilityRequirements": copy.deepcopy(plan["capabilityRequirements"]),
            "executionReady": False,
            "reason": "EDITOR_BRIDGE_NOT_INSTALLED",
            "nextPhase": "READ_ONLY_EDITOR_BRIDGE",
        }
        assert_path_free(result)
        return result

    @staticmethod
    def _blocking_questions(values: Sequence[object]) -> list[dict[str, object]]:
        if isinstance(values, (str, bytes)) or len(values) > 20:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "blockingQuestions must contain at most 20 items.",
            )
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for value in values:
            text = (
                " ".join(str(value.get("text") or "").split())
                if isinstance(value, Mapping)
                else " ".join(str(value).split())
            )
            if not text or len(text) > 1000:
                raise McpExecutionError(
                    "INVALID_ARGUMENT",
                    "blockingQuestions contains an invalid item.",
                )
            if text.casefold() in seen:
                continue
            seen.add(text.casefold())
            result.append(
                {
                    "questionId": f"question://{hashlib.sha256(text.casefold().encode('utf-8')).hexdigest()[:24]}",
                    "text": text,
                }
            )
        return result


__all__ = ["PlanService"]
