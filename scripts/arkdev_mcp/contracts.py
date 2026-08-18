"""Stable public contracts shared by the ARK Dev MCP adapters."""

from __future__ import annotations

from dataclasses import dataclass, field

from blueprint_translator.public_paths import public_value_is_path_free


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
        "INTERNAL_CONTRACT_ERROR",
    }
)

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


def assert_path_free(value: object) -> None:
    """Fail closed when a public value contains a machine-local path."""

    if not public_value_is_path_free(value):
        raise McpExecutionError(
            "INTERNAL_CONTRACT_ERROR",
            "The response contained machine-local path data.",
        )


__all__ = [
    "ERROR_CODES",
    "ERROR_SCHEMA",
    "TOOL_NAMES",
    "McpExecutionError",
    "assert_path_free",
]
