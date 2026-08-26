"""Deterministic Evidence acquisition actions; this module performs no acquisition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .canonical import semantic_digest
from .contracts import SolverContractError


def _digest(value: object) -> str:
    return semantic_digest(value)


def _action(
    requirement: Mapping[str, object],
    *,
    action_kind: str,
    source_kind: str,
    status: str,
    automatable: bool,
    requires_user_action: bool,
    reason: str,
    instructions: Sequence[str],
) -> dict[str, object]:
    requirement_id = str(requirement.get("requirementId") or "")
    problem_id = str(requirement.get("problemId") or "")
    candidates = requirement.get("candidateAssets")
    asset_candidate = (
        dict(candidates[0])
        if isinstance(candidates, Sequence)
        and not isinstance(candidates, (str, bytes, bytearray))
        and candidates
        and isinstance(candidates[0], Mapping)
        else {}
    )
    identity = {
        "problemId": problem_id,
        "requirementId": requirement_id,
        "actionKind": action_kind,
        "sourceKind": source_kind,
        "asset": str(asset_candidate.get("asset") or ""),
    }
    return {
        "actionId": f"action://{_digest(identity)[:24]}",
        "problemId": problem_id,
        "requirementIds": [requirement_id],
        "actionKind": action_kind,
        "sourceKind": source_kind,
        "status": status,
        "automatable": automatable,
        "requiresUserAction": requires_user_action,
        "assetCandidate": asset_candidate,
        "reason": reason,
        "instructions": list(instructions),
        "dependsOn": [],
    }


def _plan_requirement(requirement: Mapping[str, object]) -> dict[str, object] | None:
    status = str(requirement.get("status") or "")
    if status == "READY":
        return None
    kind = str(requirement.get("kind") or "")
    gaps = {str(item) for item in requirement.get("gaps", [])}
    preferred = [str(item) for item in requirement.get("preferredSources", [])]
    if status == "AMBIGUOUS" or "TARGET_SELECTION_REQUIRED" in gaps:
        return _action(
            requirement,
            action_kind="SELECT_ASSET_CANDIDATE",
            source_kind="",
            status="BLOCKED_BY_TARGET_SELECTION",
            automatable=False,
            requires_user_action=True,
            reason="Multiple equally ranked asset candidates require explicit selection.",
            instructions=("Select one candidate with blueprint_solver_update.",),
        )
    if kind == "THIRD_PARTY_MOD_ASSET" or "USER_SUPPLIED_MOD_ASSET" in preferred:
        return _action(
            requirement,
            action_kind="PROVIDE_MOD_ASSET",
            source_kind="USER_SUPPLIED_MOD_ASSET",
            status="BLOCKED_BY_USER_INPUT",
            automatable=False,
            requires_user_action=True,
            reason="The required third-party Mod asset is not available locally.",
            instructions=("Provide a bounded descriptor for the Mod asset.",),
        )
    if kind in {"ENTITY_DATASET", "DATASET_SCHEMA"} or "DATASET_IMPORT" in preferred:
        return _action(
            requirement,
            action_kind="IMPORT_DATASET",
            source_kind="DATASET_IMPORT",
            status="BLOCKED_BY_USER_INPUT",
            automatable=False,
            requires_user_action=True,
            reason="The required entity dataset has not been imported.",
            instructions=(
                "Provide the dataset descriptor before a separate import workflow.",
            ),
        )
    if kind == "LOCALIZATION" or "LOCALIZATION_IMPORT" in preferred:
        return _action(
            requirement,
            action_kind="IMPORT_LOCALIZATION",
            source_kind="LOCALIZATION_IMPORT",
            status="BLOCKED_BY_USER_INPUT",
            automatable=False,
            requires_user_action=True,
            reason="The required localization source has not been imported.",
            instructions=(
                "Provide the localization descriptor before a separate import workflow.",
            ),
        )
    if kind == "NATIVE_SOURCE" or "NATIVE_SOURCE_REFERENCE" in preferred:
        return _action(
            requirement,
            action_kind="PROVIDE_NATIVE_SOURCE",
            source_kind="NATIVE_SOURCE_REFERENCE",
            status="BLOCKED_BY_USER_INPUT",
            automatable=False,
            requires_user_action=True,
            reason="The required native source reference is unavailable.",
            instructions=("Provide an authoritative native source reference.",),
        )
    rebuild_gap = bool(
        gaps
        & {
            "EVIDENCE_STALE",
            "MIGRATION_REQUIRED",
            "EVIDENCE_NOT_AUTHORITATIVE",
            "EVIDENCE_REVISION_MISMATCH",
        }
    )
    candidates = requirement.get("candidateAssets")
    has_candidate = bool(
        isinstance(candidates, Sequence)
        and not isinstance(candidates, (str, bytes, bytearray))
        and candidates
    )
    if rebuild_gap or (has_candidate and "BINARY_V4_REBUILD" in preferred):
        return _action(
            requirement,
            action_kind="REBUILD_BINARY_V4",
            source_kind="BINARY_V4_REBUILD",
            status="AVAILABLE_TO_AUTOMATE" if rebuild_gap else "BLOCKED_BY_USER_INPUT",
            automatable=rebuild_gap,
            requires_user_action=not rebuild_gap,
            reason="Current v4 Evidence is stale, non-authoritative, or requires migration.",
            instructions=("Run a separate read-only binary v4 rebuild workflow.",),
        )
    return _action(
        requirement,
        action_kind="PROVIDE_ASSET",
        source_kind="USER_SUPPLIED_ASSET",
        status="BLOCKED_BY_USER_INPUT",
        automatable=False,
        requires_user_action=True,
        reason="No usable local source satisfies this Evidence requirement.",
        instructions=(
            "Provide an asset descriptor or select a discovered asset candidate.",
        ),
    )


def build_acquisition_plan(
    solver_id: str,
    requirements: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    actions = [action for item in requirements if (action := _plan_requirement(item))]
    actions = sorted(
        actions,
        key=lambda item: (str(item["problemId"]), str(item["actionId"])),
    )
    if len(actions) > 64:
        raise SolverContractError(
            "SOLVER_LIMIT_EXCEEDED",
            "Evidence acquisition action count exceeds the Solver v1 bound.",
            {"actionCount": len(actions), "maximum": 64},
        )
    payload: dict[str, object] = {
        "schema": "blueprint-to-code.evidence-acquisition-plan/v1",
        "solverId": solver_id,
        "actions": actions,
    }
    payload["semanticDigest"] = _digest(payload)
    return payload


__all__ = ["build_acquisition_plan"]
