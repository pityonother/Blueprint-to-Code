"""Compile fixed output contracts into deterministic, non-executing Operator DAGs."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from .canonical import semantic_digest, stable_id
from .contracts import (
    COMPILER_VERSION,
    MAX_OPERATOR_COUNT,
    OPERATOR_REGISTRY_VERSION,
    RESEARCH_PLAN_SCHEMA,
    SolverContractError,
)
from .evidence_matrix import evidence_kinds_for_problem
from .operator_registry import OPERATOR_DEFINITIONS


_DAGS: dict[str, tuple[str, ...]] = {
    "FORMULA": (
        "DISCOVER_TARGETS",
        "CHECK_EVIDENCE_COVERAGE",
        "ACQUIRE_EVIDENCE",
        "TRACE_VALUE_FORMULA",
        "EVALUATE_FORMULA_EXAMPLES",
        "VERIFY_COMPLETENESS",
        "SYNTHESIZE_ANSWER",
    ),
    "COMPLETE_ENUMERATION": (
        "DISCOVER_TARGETS",
        "CHECK_EVIDENCE_COVERAGE",
        "ACQUIRE_EVIDENCE",
        "EXPAND_REFERENCE_CLOSURE",
        "VERIFY_COMPLETENESS",
        "SYNTHESIZE_ANSWER",
    ),
    "INFLUENCE_FACTORS": (
        "DISCOVER_TARGETS",
        "CHECK_EVIDENCE_COVERAGE",
        "ACQUIRE_EVIDENCE",
        "BACKWARD_INFLUENCE_SLICE",
        "FIND_READERS_AND_WRITERS",
        "VERIFY_COMPLETENESS",
        "SYNTHESIZE_ANSWER",
    ),
    "RANKING": (
        "DISCOVER_TARGETS",
        "CHECK_EVIDENCE_COVERAGE",
        "ACQUIRE_EVIDENCE",
        "NORMALIZE_ENTITY_DATASET",
        "EVALUATE_EXPRESSION",
        "RANK_RESULTS",
        "VERIFY_COMPLETENESS",
        "SYNTHESIZE_ANSWER",
    ),
    "CURRENT_BEHAVIOR": (
        "DISCOVER_TARGETS",
        "CHECK_EVIDENCE_COVERAGE",
        "ACQUIRE_EVIDENCE",
        "ANALYZE_CURRENT_BEHAVIOR",
        "VERIFY_COMPLETENESS",
        "SYNTHESIZE_ANSWER",
    ),
    "BLUEPRINT_CHANGE": (
        "DISCOVER_TARGETS",
        "CHECK_EVIDENCE_COVERAGE",
        "ACQUIRE_EVIDENCE",
        "ANALYZE_CURRENT_BEHAVIOR",
        "BUILD_DESIRED_BEHAVIOR_CONTRACT",
        "COMPARE_CURRENT_TO_DESIRED",
        "COMPILE_PATCH_PLAN",
        "VERIFY_COMPLETENESS",
    ),
}
_EXECUTABLE_V1 = frozenset({"DISCOVER_TARGETS", "CHECK_EVIDENCE_COVERAGE"})


def operator_kinds_for_problem(problem: Mapping[str, object]) -> tuple[str, ...]:
    output_kind = str(problem.get("outputKind") or "")
    try:
        kinds = list(_DAGS[output_kind])
    except KeyError as exc:
        raise SolverContractError(
            "REQUIREMENT_PROPOSAL_INVALID",
            "Requirement IR contains an unsupported output kind.",
            {"outputKind": output_kind},
        ) from exc
    constraints = problem.get("constraints")
    localized = bool(
        isinstance(constraints, Mapping)
        and constraints.get("localizedNamesOnly") is True
    )
    if output_kind == "COMPLETE_ENUMERATION" and localized:
        kinds.insert(kinds.index("VERIFY_COMPLETENESS"), "RESOLVE_LOCALIZATION")
    return tuple(kinds)


def _stop_condition(kind: str, completeness: str) -> str:
    if kind == "VERIFY_COMPLETENESS":
        if completeness == "REQUIRED":
            return (
                "Stop as blocked unless every blocking Evidence requirement is READY."
            )
        return (
            "Record unresolved Evidence gaps, then allow a bounded best-effort result."
        )
    if kind == "DISCOVER_TARGETS":
        return "Stop after at most 20 deterministic candidates for the subproblem."
    if kind == "CHECK_EVIDENCE_COVERAGE":
        return "Stop after every bounded Evidence requirement has one explicit status."
    if kind == "ACQUIRE_EVIDENCE":
        return "Plan actions only; do not execute acquisition or mutate Evidence."
    return (
        "Not implemented in Solver v1; preserve the planned operator without execution."
    )


def _status(kind: str, ordinal: int) -> str:
    if kind == "DISCOVER_TARGETS":
        return "READY"
    if kind == "CHECK_EVIDENCE_COVERAGE":
        return "PENDING"
    if kind == "ACQUIRE_EVIDENCE":
        return "BLOCKED_BY_EVIDENCE"
    if kind not in _EXECUTABLE_V1 or ordinal >= 2:
        return "NOT_IMPLEMENTED"
    return "PENDING"


def build_research_plan(requirement_ir: Mapping[str, object]) -> dict[str, object]:
    """Build a fixed, linearly ordered DAG for each Requirement IR subproblem."""

    solver_id = str(requirement_ir.get("solverId") or "")
    compiler_version = str(requirement_ir.get("compilerVersion") or COMPILER_VERSION)
    raw_subproblems = requirement_ir.get("subproblems")
    if not isinstance(raw_subproblems, list):
        raise SolverContractError(
            "REQUIREMENT_PROPOSAL_INVALID",
            "Requirement IR subproblems must be an array.",
        )

    operators: list[dict[str, object]] = []
    problem_plans: list[dict[str, object]] = []
    for problem in raw_subproblems:
        if not isinstance(problem, Mapping):
            raise SolverContractError(
                "REQUIREMENT_PROPOSAL_INVALID",
                "Requirement IR subproblem must be an object.",
            )
        problem_id = str(problem.get("problemId") or "")
        completeness = str(problem.get("completeness") or "")
        problem_evidence_kinds = frozenset(evidence_kinds_for_problem(problem))
        problem_operators: list[dict[str, object]] = []
        previous_id = ""
        for ordinal, kind in enumerate(operator_kinds_for_problem(problem)):
            definition = OPERATOR_DEFINITIONS[kind]
            operator_id = stable_id(
                "operator",
                compiler_version,
                problem_id,
                ordinal,
                kind,
            )
            evidence_ids = [
                stable_id("requirement", compiler_version, problem_id, evidence_kind)
                for evidence_kind in definition["requiredEvidenceKinds"]
                if evidence_kind in problem_evidence_kinds
            ]
            operator = {
                "operatorId": operator_id,
                "problemId": problem_id,
                "kind": kind,
                "status": _status(kind, ordinal),
                "dependsOn": [previous_id] if previous_id else [],
                "inputs": list(definition["inputTypes"]),
                "outputs": list(definition["outputTypes"]),
                "budget": copy.deepcopy(definition["defaultBudget"]),
                "requiredEvidenceIds": evidence_ids,
                "stopCondition": _stop_condition(kind, completeness),
            }
            problem_operators.append(operator)
            operators.append(operator)
            previous_id = operator_id

        operator_ids = [str(item["operatorId"]) for item in problem_operators]
        gate = next(
            (
                str(item["operatorId"])
                for item in problem_operators
                if item["kind"] == "VERIFY_COMPLETENESS"
            ),
            "",
        )
        problem_plans.append(
            {
                "problemId": problem_id,
                "operatorIds": operator_ids,
                "rootOperatorIds": operator_ids[:1],
                "terminalOperatorIds": operator_ids[-1:],
                "completenessGateOperatorId": gate,
            }
        )

    if len(operators) > MAX_OPERATOR_COUNT:
        raise SolverContractError(
            "SOLVER_LIMIT_EXCEEDED",
            "Compiled Operator count exceeds the Solver v1 bound.",
            {"operatorCount": len(operators), "maximum": MAX_OPERATOR_COUNT},
        )
    payload: dict[str, object] = {
        "schema": RESEARCH_PLAN_SCHEMA,
        "solverId": solver_id,
        "compilerVersion": compiler_version,
        "operatorRegistryVersion": OPERATOR_REGISTRY_VERSION,
        "subproblems": problem_plans,
        "operators": operators,
    }
    payload["semanticDigest"] = semantic_digest(payload)
    return payload


__all__ = ["build_research_plan", "operator_kinds_for_problem"]
