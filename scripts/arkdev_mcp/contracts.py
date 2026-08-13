"""Stable public contracts shared by the ARK Dev MCP adapters."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from os import PathLike


ERROR_SCHEMA = "blueprint-to-code.arkdev-mcp-error/v1"
TOOL_NAMES = (
    "arkdev_status",
    "arkdev_editor_state",
    "blueprint_list_assets",
    "blueprint_get_context",
    "blueprint_get_node",
    "blueprint_task_create",
    "blueprint_task_resume",
    "blueprint_task_research",
    "blueprint_patch_plan_draft",
    "blueprint_patch_plan_validate",
    "blueprint_patch_plan_confirm",
    "blueprint_solver_create",
    "blueprint_solver_resume",
    "blueprint_solver_preflight",
    "blueprint_solver_update",
    "blueprint_solver_materialize_task",
)
ERROR_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "ASSET_NOT_FOUND",
        "EVIDENCE_NOT_FOUND",
        "EVIDENCE_STALE",
        "EVIDENCE_NOT_AUTHORITATIVE",
        "EVIDENCE_REVISION_MISMATCH",
        "GRAPH_SELECTION_REQUIRED",
        "NODE_NOT_FOUND",
        "RESULT_BUDGET_EXCEEDED",
        "EDITOR_BRIDGE_NOT_INSTALLED",
        "EDITOR_BRIDGE_UNAVAILABLE",
        "TASK_NOT_FOUND",
        "TASK_PHASE_INVALID",
        "TASK_BLOCKED",
        "TASK_SLICE_LIMIT_REACHED",
        "EVIDENCE_REVISION_CHANGED",
        "PATCH_PLAN_NOT_FOUND",
        "PATCH_PLAN_INVALID",
        "PATCH_PLAN_LIMIT_EXCEEDED",
        "PATCH_PLAN_NOT_CONFIRMABLE",
        "PATCH_PLAN_DIGEST_MISMATCH",
        "PLAN_CONFIRMATION_REQUIRED",
        "SOLVER_NOT_FOUND",
        "REQUIREMENT_PROPOSAL_INVALID",
        "REQUEST_TEXT_UNASSIGNED",
        "SOLVER_PHASE_INVALID",
        "SOLVER_UPDATE_INVALID",
        "TARGET_SELECTION_REQUIRED",
        "TARGET_CANDIDATE_NOT_FOUND",
        "EVIDENCE_ACQUISITION_REQUIRED",
        "TASK_NOT_APPLICABLE",
        "SOLVER_LIMIT_EXCEEDED",
        "INTERNAL_CONTRACT_ERROR",
    }
)

_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?<![A-Za-z0-9_])[a-z]:[\\/]")
_FILE_URI = re.compile(r"(?i)(?<![A-Za-z0-9_])file://")
_UNC_PATH = re.compile(r"(?<![A-Za-z0-9_:])(?:\\\\|//)[^\\/\s]+[\\/]")
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_:/])/(?!Game(?:/|$)|Engine(?:/|$)|Script(?:/|$))[^\s\"']+"
)
_UNREAL_VIRTUAL_ROOTS = ("/Game/", "/Engine/", "/Script/")


@dataclass
class McpExecutionError(Exception):
    """Code-bearing execution error that is safe to expose to an MCP caller."""

    code: str
    message: str
    retryable: bool = False
    details: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(f"unsupported ARK Dev MCP error code: {self.code}")
        Exception.__init__(self, self.message)

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": ERROR_SCHEMA,
            "code": self.code,
            "message": self.message,
            "retryable": bool(self.retryable),
            "details": dict(self.details),
        }
        assert_path_free(payload)
        return payload


def _contains_machine_path(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    if _WINDOWS_ABSOLUTE_PATH.search(normalized):
        return True
    if _FILE_URI.search(normalized) or _UNC_PATH.search(normalized):
        return True
    if normalized in {root.removesuffix("/") for root in _UNREAL_VIRTUAL_ROOTS}:
        return False
    return bool(_POSIX_ABSOLUTE_PATH.search(normalized))


def assert_path_free(value: object) -> None:
    """Fail closed when a public value contains a machine-local path."""

    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, PathLike):
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "The response contained machine-local path data.",
            )
        if isinstance(current, str):
            if _contains_machine_path(current):
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "The response contained machine-local path data.",
                )
            continue
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (bytes, bytearray)
        ):
            pending.extend(current)


__all__ = [
    "ERROR_CODES",
    "ERROR_SCHEMA",
    "TOOL_NAMES",
    "McpExecutionError",
    "assert_path_free",
]
