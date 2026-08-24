"""Compile validated Requirement Proposals into deterministic Requirement IR."""

from __future__ import annotations

from .canonical import semantic_digest, stable_id
from .contracts import (
    COMPILER_VERSION,
    MAX_UNASSIGNED_NON_WHITESPACE,
    REQUIREMENT_IR_SCHEMA,
    SolverContractError,
)
from .proposal_validator import validate_requirement_proposal, validate_solver_id


def _unassigned_text(raw_request: str, subproblems: list[dict[str, object]]) -> str:
    covered = [False] * len(raw_request)
    for item in subproblems:
        start = int(item["sourceStart"])
        end = int(item["sourceEnd"])
        covered[start:end] = [True] * (end - start)
    return "".join(
        character for index, character in enumerate(raw_request) if not covered[index]
    ).strip()


def _mode(subproblems: list[dict[str, object]]) -> str:
    intents = {str(item["intent"]) for item in subproblems}
    if intents == {"ANSWER_CURRENT_BEHAVIOR"}:
        return "ANSWER"
    if intents == {"DESIGN_BLUEPRINT_CHANGE"}:
        return "CHANGE"
    return "MIXED"


def compile_requirement(
    proposal: object,
    *,
    solver_id: str,
    explicit_raw_request: str | None = None,
) -> dict[str, object]:
    """Compile one validated Proposal without interpreting its natural language."""

    normalized = validate_requirement_proposal(
        proposal,
        explicit_raw_request=explicit_raw_request,
    )
    validate_solver_id(solver_id)
    raw_request = str(normalized["rawRequest"])
    compiled_subproblems: list[dict[str, object]] = []
    for index, source in enumerate(normalized["subproblems"]):
        source_copy = dict(source)
        problem_id = stable_id(
            "problem",
            COMPILER_VERSION,
            index,
            source_copy,
        )
        compiled_subproblems.append(
            {
                "problemId": problem_id,
                **source_copy,
                "blockingQuestions": [],
                "status": "CREATED",
            }
        )

    unassigned = _unassigned_text(raw_request, compiled_subproblems)
    unassigned_character_count = sum(not item.isspace() for item in unassigned)
    if unassigned_character_count > MAX_UNASSIGNED_NON_WHITESPACE:
        raise SolverContractError(
            "REQUEST_TEXT_UNASSIGNED",
            "More than 20 non-whitespace request characters are not bound to a subproblem.",
            {
                "unassignedCharacterCount": unassigned_character_count,
                "maximum": MAX_UNASSIGNED_NON_WHITESPACE,
            },
        )

    requirement: dict[str, object] = {
        "schema": REQUIREMENT_IR_SCHEMA,
        "solverId": solver_id,
        "compilerVersion": COMPILER_VERSION,
        "rawRequest": raw_request,
        "language": normalized["language"],
        "mode": _mode(compiled_subproblems),
        "subproblems": compiled_subproblems,
        "globalConstraints": [],
        "blockingQuestions": [],
        "unassignedText": unassigned,
    }
    requirement["semanticDigest"] = semantic_digest(requirement)
    return requirement


__all__ = ["compile_requirement"]
