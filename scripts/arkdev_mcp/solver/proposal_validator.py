"""Boundary validation for Codex-authored Requirement Proposals."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping

from ..contracts import assert_path_free
from .contracts import (
    COMPLETENESS_POLICIES,
    CONSTRAINT_FIELDS,
    INTENTS,
    MAX_ACCEPTANCE_CRITERIA,
    MAX_RAW_REQUEST_CHARS,
    MAX_SUBPROBLEMS,
    MAX_TARGET_HINTS,
    OUTPUT_KINDS,
    PROPOSAL_SCHEMA,
    SolverContractError,
    TARGET_ROLES,
)


_PROPOSAL_FIELDS = frozenset({"schema", "rawRequest", "language", "subproblems"})
_SUBPROBLEM_FIELDS = frozenset(
    {
        "sourceStart",
        "sourceEnd",
        "sourceText",
        "intent",
        "outputKind",
        "completeness",
        "targetHints",
        "constraints",
        "acceptanceCriteria",
    }
)
_TARGET_HINT_FIELDS = frozenset(
    {"text", "role", "aliases", "expectedKind", "userSupplied"}
)
_SOLVER_ID = re.compile(r"^solver://[0-9a-f]{32}$")


def _fail(message: str, **details: object) -> None:
    raise SolverContractError("REQUIREMENT_PROPOSAL_INVALID", message, details)


def _require_exact_fields(
    value: Mapping[str, object],
    fields: frozenset[str],
    location: str,
) -> None:
    actual = frozenset(str(key) for key in value)
    if actual != fields:
        _fail(
            f"{location} must contain exactly the documented fields.",
            location=location,
            missing=sorted(fields - actual),
            unexpected=sorted(actual - fields),
        )


def _require_string(
    value: object,
    location: str,
    *,
    minimum: int = 1,
    maximum: int,
) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        _fail(
            f"{location} must be a bounded string.",
            location=location,
            minimum=minimum,
            maximum=maximum,
        )
    return value


def _require_string_list(
    value: object,
    location: str,
    *,
    maximum_items: int,
    minimum_items: int = 0,
    maximum_length: int = 1_000,
) -> list[str]:
    if (
        not isinstance(value, list)
        or not minimum_items <= len(value) <= maximum_items
        or any(
            not isinstance(item, str) or not 1 <= len(item) <= maximum_length
            for item in value
        )
    ):
        _fail(
            f"{location} must be a bounded list of non-empty strings.",
            location=location,
        )
    return value


def validate_solver_id(solver_id: object) -> str:
    if not isinstance(solver_id, str) or not _SOLVER_ID.fullmatch(solver_id):
        _fail("solverId must be an opaque solver:// handle.", location="solverId")
    return solver_id


def _validate_constraints(value: object, output_kind: str, intent: str) -> None:
    if not isinstance(value, Mapping):
        _fail("constraints must be an object.", location="constraints")
    unexpected = set(value) - CONSTRAINT_FIELDS
    if unexpected:
        _fail(
            "constraints contains unsupported fields.",
            location="constraints",
            unexpected=sorted(str(item) for item in unexpected),
        )

    string_fields = {"outputLanguage": 32, "userFormula": 2_000}
    for field, maximum in string_fields.items():
        if field in value:
            _require_string(value[field], f"constraints.{field}", maximum=maximum)
    for field in ("localizedNamesOnly", "noWeb"):
        if field in value and not isinstance(value[field], bool):
            _fail(
                f"constraints.{field} must be a boolean.",
                location=f"constraints.{field}",
            )

    if "topK" in value:
        top_k = value["topK"]
        if (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= 100
        ):
            _fail(
                "constraints.topK must be an integer from 1 through 100.",
                location="constraints.topK",
            )
        if output_kind != "RANKING":
            _fail(
                "constraints.topK is valid only for RANKING.",
                location="constraints.topK",
            )

    if "formulaVariables" in value:
        variables = value["formulaVariables"]
        if (
            not isinstance(variables, Mapping)
            or not 1 <= len(variables) <= 32
            or any(
                not isinstance(key, str)
                or not key
                or not isinstance(item, str)
                or not item
                or len(key) > 128
                or len(item) > 256
                for key, item in variables.items()
            )
        ):
            _fail(
                "constraints.formulaVariables must be a bounded string map.",
                location="constraints.formulaVariables",
            )

    for field in (
        "candidateScope",
        "excludeClasses",
        "includeClasses",
        "desiredBehavior",
        "invariants",
        "acceptanceTests",
    ):
        if field in value:
            _require_string_list(
                value[field],
                f"constraints.{field}",
                maximum_items=100
                if field in {"candidateScope", "excludeClasses", "includeClasses"}
                else 20,
                minimum_items=1
                if field in {"desiredBehavior", "invariants", "acceptanceTests"}
                else 0,
                maximum_length=512,
            )

    if output_kind == "RANKING":
        required = {"topK", "userFormula", "formulaVariables"}
        missing = sorted(required - set(value))
        if missing:
            _fail(
                "RANKING requires topK, userFormula, and formulaVariables.",
                missing=missing,
            )
    elif "userFormula" in value or "formulaVariables" in value:
        _fail(
            "userFormula and formulaVariables are valid only for RANKING.",
            location="constraints",
        )

    if intent == "DESIGN_BLUEPRINT_CHANGE":
        required = {"desiredBehavior", "invariants", "acceptanceTests"}
        missing = sorted(required - set(value))
        if missing:
            _fail(
                "DESIGN_BLUEPRINT_CHANGE requires desiredBehavior, invariants, and acceptanceTests.",
                missing=missing,
            )
        if output_kind != "BLUEPRINT_CHANGE":
            _fail(
                "DESIGN_BLUEPRINT_CHANGE must use BLUEPRINT_CHANGE outputKind.",
                location="outputKind",
            )
    elif output_kind == "BLUEPRINT_CHANGE":
        _fail(
            "BLUEPRINT_CHANGE outputKind requires DESIGN_BLUEPRINT_CHANGE intent.",
            location="intent",
        )


def _validate_target_hint(value: object, location: str) -> None:
    if not isinstance(value, Mapping):
        _fail(f"{location} must be an object.", location=location)
    _require_exact_fields(value, _TARGET_HINT_FIELDS, location)
    _require_string(value["text"], f"{location}.text", maximum=256)
    if value["role"] not in TARGET_ROLES:
        _fail(f"{location}.role is unsupported.", location=f"{location}.role")
    aliases = _require_string_list(
        value["aliases"],
        f"{location}.aliases",
        maximum_items=8,
        maximum_length=256,
    )
    if len(aliases) != len(set(aliases)):
        _fail(f"{location}.aliases must be unique.", location=f"{location}.aliases")
    _require_string(
        value["expectedKind"],
        f"{location}.expectedKind",
        minimum=0,
        maximum=128,
    )
    if not isinstance(value["userSupplied"], bool):
        _fail(
            f"{location}.userSupplied must be a boolean.",
            location=f"{location}.userSupplied",
        )


def validate_requirement_proposal(proposal: object) -> dict[str, object]:
    """Validate and defensively copy an untrusted Requirement Proposal."""

    if not isinstance(proposal, Mapping):
        _fail("Requirement Proposal must be an object.", location="proposal")
    _require_exact_fields(proposal, _PROPOSAL_FIELDS, "proposal")
    if proposal["schema"] != PROPOSAL_SCHEMA:
        _fail("Requirement Proposal schema is unsupported.", location="schema")
    raw_request = _require_string(
        proposal["rawRequest"],
        "rawRequest",
        maximum=MAX_RAW_REQUEST_CHARS,
    )
    _require_string(proposal["language"], "language", minimum=2, maximum=32)
    subproblems = proposal["subproblems"]
    if (
        not isinstance(subproblems, list)
        or not 1 <= len(subproblems) <= MAX_SUBPROBLEMS
    ):
        _fail("subproblems must contain between 1 and 8 items.", location="subproblems")

    previous_end = -1
    for index, subproblem in enumerate(subproblems):
        location = f"subproblems[{index}]"
        if not isinstance(subproblem, Mapping):
            _fail(f"{location} must be an object.", location=location)
        _require_exact_fields(subproblem, _SUBPROBLEM_FIELDS, location)
        start = subproblem["sourceStart"]
        end = subproblem["sourceEnd"]
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not 0 <= start < end <= len(raw_request)
        ):
            _fail(f"{location} has an invalid source span.", location=location)
        if start < previous_end:
            _fail(
                "subproblem spans must be sorted and non-overlapping.",
                location=location,
            )
        source_text = _require_string(
            subproblem["sourceText"],
            f"{location}.sourceText",
            maximum=MAX_RAW_REQUEST_CHARS,
        )
        if raw_request[start:end] != source_text:
            _fail(
                "sourceText must exactly match its rawRequest span.", location=location
            )
        previous_end = end

        intent = subproblem["intent"]
        output_kind = subproblem["outputKind"]
        if intent not in INTENTS:
            _fail(f"{location}.intent is unsupported.", location=f"{location}.intent")
        if output_kind not in OUTPUT_KINDS:
            _fail(
                f"{location}.outputKind is unsupported.",
                location=f"{location}.outputKind",
            )
        if subproblem["completeness"] not in COMPLETENESS_POLICIES:
            _fail(
                f"{location}.completeness is unsupported.",
                location=f"{location}.completeness",
            )

        hints = subproblem["targetHints"]
        if not isinstance(hints, list) or len(hints) > MAX_TARGET_HINTS:
            _fail(
                f"{location}.targetHints exceeds its bound.",
                location=f"{location}.targetHints",
            )
        for hint_index, hint in enumerate(hints):
            _validate_target_hint(hint, f"{location}.targetHints[{hint_index}]")

        _validate_constraints(subproblem["constraints"], str(output_kind), str(intent))
        _require_string_list(
            subproblem["acceptanceCriteria"],
            f"{location}.acceptanceCriteria",
            maximum_items=MAX_ACCEPTANCE_CRITERIA,
        )

    try:
        assert_path_free(proposal)
    except Exception as exc:
        _fail("Requirement Proposal must be path-free.", errorType=type(exc).__name__)
    return copy.deepcopy(dict(proposal))


__all__ = ["validate_requirement_proposal", "validate_solver_id"]
