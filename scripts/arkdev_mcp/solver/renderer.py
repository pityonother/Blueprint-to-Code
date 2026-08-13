"""Compact, path-free public Solver projection."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping

from blueprint_translator.context_pack import estimate_tokens

from ..contracts import assert_path_free


def render_solver_state(
    documents: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    requirement_ir = documents["requirement"]
    matrix = documents["evidenceMatrix"]
    plan = documents["acquisitionPlan"]
    state = documents["state"]
    bindings = documents["bindings"]
    requirements = [
        dict(item)
        for item in matrix.get("requirements", [])
        if isinstance(item, Mapping)
    ]
    requirement_counts = Counter(
        str(item.get("status") or "UNRESOLVED") for item in requirements
    )
    kind_counts = Counter(str(item.get("kind") or "UNKNOWN") for item in requirements)
    problem_statuses = dict(state.get("problemStatuses") or {})
    subproblems = [
        dict(item)
        for item in requirement_ir.get("subproblems", [])
        if isinstance(item, Mapping)
    ]
    task_bindings = dict(bindings.get("tasks") or {})
    actions = [
        {
            "actionId": item.get("actionId", ""),
            "problemId": item.get("problemId", ""),
            "actionKind": item.get("actionKind", ""),
            "sourceKind": item.get("sourceKind", ""),
            "status": item.get("status", ""),
            "requiresUserAction": bool(item.get("requiresUserAction", False)),
            "reason": str(item.get("reason") or "")[:240],
        }
        for item in plan.get("actions", [])
        if isinstance(item, Mapping)
    ][:16]
    status = str(state.get("status") or "CREATED")
    next_action = {
        "CREATED": "blueprint_solver_preflight",
        "PREFLIGHT": "blueprint_solver_preflight",
        "ACQUISITION_REQUIRED": "blueprint_solver_update",
        "READY_FOR_TASKS": "blueprint_solver_materialize_task",
        "TASKS_MATERIALIZED": "blueprint_solver_resume",
        "BLOCKED": "blueprint_solver_update",
    }.get(status, "blueprint_solver_resume")
    payload: dict[str, object] = {
        "schema": "blueprint-to-code.solver-state/v1",
        "solverId": state.get("solverId", ""),
        "status": status,
        "mode": requirement_ir.get("mode", "ANSWER"),
        "requirementSummary": {
            "total": len(requirements),
            "byStatus": dict(sorted(requirement_counts.items())),
            "byKind": dict(sorted(kind_counts.items())),
        },
        "problemSummaries": [
            {
                "problemId": item.get("problemId", ""),
                "intent": item.get("intent", ""),
                "outputKind": item.get("outputKind", ""),
                "status": problem_statuses.get(
                    str(item.get("problemId") or ""), "PENDING"
                ),
            }
            for item in subproblems[:8]
        ],
        "coverageSummary": {
            "ready": requirement_counts.get("READY", 0),
            "unresolved": len(requirements) - requirement_counts.get("READY", 0),
            "candidateProblems": len(dict(bindings.get("assets") or {})),
        },
        "acquisitionActions": actions,
        "blockingQuestions": [
            dict(item)
            for item in requirement_ir.get("blockingQuestions", [])
            if isinstance(item, Mapping)
        ][:16],
        "materializedTasks": [
            {"problemId": problem_id, "taskId": task_id}
            for problem_id, task_id in sorted(task_bindings.items())
        ],
        "nextRecommendedAction": next_action,
        "nextRecommendedTool": next_action,
        "estimatedTokens": 0,
        "semanticDigest": state.get("semanticDigest", ""),
    }
    for _ in range(2):
        payload["estimatedTokens"] = estimate_tokens(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
    assert_path_free(payload)
    return payload


__all__ = ["render_solver_state"]
