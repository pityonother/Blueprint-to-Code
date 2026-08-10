"""Blueprint Patch Plan vocabulary and bounded limits."""

from __future__ import annotations

from ..editor_bridge import EditorCapability


PATCH_OPERATION_KINDS = frozenset(
    {
        "CREATE_NODE",
        "DELETE_NODE",
        "CONNECT",
        "DISCONNECT",
        "SET_DEFAULT",
        "MOVE_NODE",
        "ADD_COMMENT",
        "PRESERVE",
    }
)

MAX_PLAN_NODES = 64
MAX_PLAN_OPERATIONS = 128
MAX_PLAN_CHECKPOINTS = 32

KNOWN_CAPABILITIES = frozenset(item.value for item in EditorCapability)
OPERATION_CAPABILITY = {
    "CREATE_NODE": EditorCapability.CREATE_NODE.value,
    "DELETE_NODE": EditorCapability.DELETE_NODE.value,
    "CONNECT": EditorCapability.CREATE_CONNECTION.value,
    "DISCONNECT": EditorCapability.BREAK_PIN_LINKS.value,
    "SET_DEFAULT": EditorCapability.SET_PIN_DEFAULT.value,
    "MOVE_NODE": EditorCapability.MOVE_NODE.value,
}

__all__ = [
    "KNOWN_CAPABILITIES",
    "MAX_PLAN_CHECKPOINTS",
    "MAX_PLAN_NODES",
    "MAX_PLAN_OPERATIONS",
    "OPERATION_CAPABILITY",
    "PATCH_OPERATION_KINDS",
]
