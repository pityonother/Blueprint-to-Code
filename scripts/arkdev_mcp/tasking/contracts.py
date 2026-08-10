"""Task Context, Session, and Graph Slice contract constants."""

TASK_CONTEXT_SCHEMA = "blueprint-to-code.task-context/v1"
TASK_SESSION_SCHEMA = "blueprint-to-code.task-session/v1"
GRAPH_SLICE_SCHEMA = "blueprint-to-code.graph-slice/v1"
PATCH_PLAN_SCHEMA = "blueprint-to-code.blueprint-patch-plan/v1"

TASK_MODES = frozenset(
    {"KNOWLEDGE_QUERY", "BLUEPRINT_DESIGN", "IMPLEMENTATION_PREP"}
)
TASK_PHASES = frozenset(
    {
        "DISCOVERY",
        "READY_TO_PLAN",
        "PLAN_DRAFT",
        "PLAN_CONFIRMED",
        "EXECUTION_PENDING",
        "BLOCKED",
        "DONE",
    }
)

MAX_SUPPORTING_ASSETS = 3
MAX_GRAPH_TARGETS = 2
MAX_GRAPH_SLICES = 8

__all__ = [
    "GRAPH_SLICE_SCHEMA",
    "MAX_GRAPH_SLICES",
    "MAX_GRAPH_TARGETS",
    "MAX_SUPPORTING_ASSETS",
    "PATCH_PLAN_SCHEMA",
    "TASK_CONTEXT_SCHEMA",
    "TASK_MODES",
    "TASK_PHASES",
    "TASK_SESSION_SCHEMA",
]
