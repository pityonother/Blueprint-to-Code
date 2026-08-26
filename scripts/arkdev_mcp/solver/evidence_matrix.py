"""Derive bounded Evidence requirements from Requirement IR output contracts."""

from __future__ import annotations

from collections.abc import Mapping

from .canonical import semantic_digest, stable_id
from .contracts import (
    COMPILER_VERSION,
    EVIDENCE_MATRIX_SCHEMA,
    MAX_EVIDENCE_REQUIREMENTS,
    SolverContractError,
)
from .source_registry import preferred_sources_for


_BASE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "FORMULA": ("ASSET_IDENTITY", "GRAPH_STRUCTURE", "DEFAULTS"),
    "COMPLETE_ENUMERATION": (
        "ASSET_IDENTITY",
        "REFERENCE_CLOSURE",
        "DEFAULTS",
    ),
    "INFLUENCE_FACTORS": ("ASSET_IDENTITY", "GRAPH_STRUCTURE", "DEFAULTS"),
    "RANKING": ("ENTITY_DATASET", "DATASET_SCHEMA", "USER_FORMULA"),
    "CURRENT_BEHAVIOR": ("ASSET_IDENTITY", "GRAPH_STRUCTURE", "DEFAULTS"),
    "BLUEPRINT_CHANGE": (
        "ASSET_IDENTITY",
        "GRAPH_STRUCTURE",
        "NODE_IDENTITY",
        "PIN_SIGNATURES",
        "DEFAULTS",
    ),
}
_REASONS: dict[str, str] = {
    "ASSET_IDENTITY": "The target asset must be selected without ambiguity.",
    "GRAPH_STRUCTURE": "The requested output depends on authoritative Graph structure.",
    "NODE_IDENTITY": "A Blueprint change plan needs stable existing-node identities.",
    "PIN_SIGNATURES": "A Blueprint change plan needs stable Pin signatures; live Pin identity is not required in v1.",
    "PIN_IDENTITY": "Authoritative native Pin identity must be separately proven.",
    "DEFAULTS": "Default values are required to preserve or evaluate current behavior.",
    "REFERENCE_CLOSURE": "Complete enumeration requires a bounded authoritative reference closure.",
    "LOCALIZATION": "Localized-only output requires an authoritative localization mapping.",
    "ENTITY_DATASET": "Ranking requires a bounded entity dataset.",
    "DATASET_SCHEMA": "Ranking requires a typed schema for every evaluated entity field.",
    "USER_FORMULA": "The exact user formula and variable mapping must be preserved without execution in v1.",
    "NATIVE_SOURCE": "Required completeness must not silently ignore native or unknown logic.",
    "RUNTIME_STATE": "Runtime state is needed only when the output contract explicitly requires it.",
    "THIRD_PARTY_MOD_ASSET": "A third-party Mod target requires a user-supplied Mod asset descriptor.",
}


def evidence_kinds_for_problem(problem: Mapping[str, object]) -> tuple[str, ...]:
    output_kind = str(problem.get("outputKind") or "")
    try:
        kinds = list(_BASE_EVIDENCE[output_kind])
    except KeyError as exc:
        raise SolverContractError(
            "REQUIREMENT_PROPOSAL_INVALID",
            "Requirement IR contains an unsupported output kind.",
            {"outputKind": output_kind},
        ) from exc
    constraints = problem.get("constraints")
    typed_constraints = constraints if isinstance(constraints, Mapping) else {}
    completeness = str(problem.get("completeness") or "")
    if typed_constraints.get("localizedNamesOnly") is True:
        kinds.append("LOCALIZATION")
    if completeness == "REQUIRED" and output_kind in {"FORMULA", "INFLUENCE_FACTORS"}:
        kinds.append("NATIVE_SOURCE")
    raw_hints = problem.get("targetHints")
    hints = raw_hints if isinstance(raw_hints, list) else []
    if output_kind == "RANKING" and any(
        isinstance(hint, Mapping) and hint.get("role") == "MOD_ASSET" for hint in hints
    ):
        kinds.append("THIRD_PARTY_MOD_ASSET")
    return tuple(dict.fromkeys(kinds))


def _scope(problem: Mapping[str, object]) -> dict[str, object]:
    raw_hints = problem.get("targetHints")
    hints = raw_hints if isinstance(raw_hints, list) else []
    return {
        "targetRoles": list(
            dict.fromkeys(
                str(hint.get("role") or "")
                for hint in hints
                if isinstance(hint, Mapping) and hint.get("role")
            )
        ),
        "targetHints": [
            str(hint.get("text") or "")
            for hint in hints
            if isinstance(hint, Mapping) and hint.get("text")
        ][:8],
    }


def derive_evidence_matrix(
    requirement_ir: Mapping[str, object],
    research_plan: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Derive unresolved requirements; preflight alone may promote them to READY."""

    del (
        research_plan
    )  # The matrix derives from the output contract, never a caller-edited DAG.
    solver_id = str(requirement_ir.get("solverId") or "")
    compiler_version = str(requirement_ir.get("compilerVersion") or COMPILER_VERSION)
    raw_subproblems = requirement_ir.get("subproblems")
    if not isinstance(raw_subproblems, list):
        raise SolverContractError(
            "REQUIREMENT_PROPOSAL_INVALID",
            "Requirement IR subproblems must be an array.",
        )
    requirements: list[dict[str, object]] = []
    for problem in raw_subproblems:
        if not isinstance(problem, Mapping):
            raise SolverContractError(
                "REQUIREMENT_PROPOSAL_INVALID",
                "Requirement IR subproblem must be an object.",
            )
        problem_id = str(problem.get("problemId") or "")
        scope = _scope(problem)
        for kind in evidence_kinds_for_problem(problem):
            requirements.append(
                {
                    "requirementId": stable_id(
                        "requirement",
                        compiler_version,
                        problem_id,
                        kind,
                    ),
                    "problemId": problem_id,
                    "kind": kind,
                    "scope": scope,
                    "blocking": True,
                    "reason": _REASONS[kind],
                    "preferredSources": preferred_sources_for(kind),
                    "candidateAssets": [],
                    "status": "UNRESOLVED",
                    "selectedSource": "",
                    "gaps": [],
                }
            )
    if len(requirements) > MAX_EVIDENCE_REQUIREMENTS:
        raise SolverContractError(
            "SOLVER_LIMIT_EXCEEDED",
            "Evidence Requirement count exceeds the Solver v1 bound.",
            {
                "requirementCount": len(requirements),
                "maximum": MAX_EVIDENCE_REQUIREMENTS,
            },
        )
    payload: dict[str, object] = {
        "schema": EVIDENCE_MATRIX_SCHEMA,
        "solverId": solver_id,
        "compilerVersion": compiler_version,
        "requirements": requirements,
    }
    payload["semanticDigest"] = semantic_digest(payload)
    return payload


__all__ = ["derive_evidence_matrix", "evidence_kinds_for_problem"]
