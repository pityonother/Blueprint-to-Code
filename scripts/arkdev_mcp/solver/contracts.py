"""Closed, bounded contracts for the deterministic BTC Solver compiler."""

from __future__ import annotations

from ..contracts import McpExecutionError


COMPILER_VERSION = "1.0.0"
OPERATOR_REGISTRY_SCHEMA = "blueprint-to-code.solver-operator-registry/v1"
OPERATOR_REGISTRY_VERSION = "1.0.0"
SOURCE_REGISTRY_VERSION = "1.0.0"

PROPOSAL_SCHEMA = "blueprint-to-code.requirement-proposal/v1"
REQUIREMENT_IR_SCHEMA = "blueprint-to-code.requirement-ir/v1"
RESEARCH_PLAN_SCHEMA = "blueprint-to-code.research-plan/v1"
EVIDENCE_MATRIX_SCHEMA = "blueprint-to-code.evidence-requirement-matrix/v1"

MAX_RAW_REQUEST_CHARS = 8_000
MAX_SUBPROBLEMS = 8
MAX_TARGET_HINTS = 8
MAX_ACCEPTANCE_CRITERIA = 20
MAX_OPERATOR_COUNT = 64
MAX_EVIDENCE_REQUIREMENTS = 64
MAX_UNASSIGNED_NON_WHITESPACE = 20

INTENTS = (
    "ANSWER_CURRENT_BEHAVIOR",
    "DESIGN_BLUEPRINT_CHANGE",
)
OUTPUT_KINDS = (
    "FORMULA",
    "COMPLETE_ENUMERATION",
    "INFLUENCE_FACTORS",
    "RANKING",
    "CURRENT_BEHAVIOR",
    "BLUEPRINT_CHANGE",
)
COMPLETENESS_POLICIES = ("REQUIRED", "BEST_EFFORT")
TARGET_ROLES = (
    "PRIMARY_BLUEPRINT",
    "SUPPORTING_BLUEPRINT",
    "BUFF",
    "SUPPLY_CRATE",
    "LOOT_ITEM_SET",
    "LOCALIZATION_SOURCE",
    "ENTITY_DATASET",
    "MOD_ASSET",
    "OTHER",
)
CONSTRAINT_FIELDS = frozenset(
    {
        "outputLanguage",
        "localizedNamesOnly",
        "topK",
        "userFormula",
        "formulaVariables",
        "candidateScope",
        "excludeClasses",
        "includeClasses",
        "desiredBehavior",
        "invariants",
        "acceptanceTests",
        "noWeb",
    }
)

OPERATOR_KINDS = (
    "DISCOVER_TARGETS",
    "CHECK_EVIDENCE_COVERAGE",
    "ACQUIRE_EVIDENCE",
    "TRACE_VALUE_FORMULA",
    "EVALUATE_FORMULA_EXAMPLES",
    "EXPAND_REFERENCE_CLOSURE",
    "BACKWARD_INFLUENCE_SLICE",
    "FIND_READERS_AND_WRITERS",
    "RESOLVE_LOCALIZATION",
    "NORMALIZE_ENTITY_DATASET",
    "EVALUATE_EXPRESSION",
    "RANK_RESULTS",
    "ANALYZE_CURRENT_BEHAVIOR",
    "BUILD_DESIRED_BEHAVIOR_CONTRACT",
    "COMPARE_CURRENT_TO_DESIRED",
    "COMPILE_PATCH_PLAN",
    "VERIFY_COMPLETENESS",
    "SYNTHESIZE_ANSWER",
)
EVIDENCE_KINDS = frozenset(
    {
        "ASSET_IDENTITY",
        "GRAPH_STRUCTURE",
        "NODE_IDENTITY",
        "PIN_SIGNATURES",
        "PIN_IDENTITY",
        "DEFAULTS",
        "REFERENCE_CLOSURE",
        "LOCALIZATION",
        "ENTITY_DATASET",
        "DATASET_SCHEMA",
        "USER_FORMULA",
        "NATIVE_SOURCE",
        "RUNTIME_STATE",
        "THIRD_PARTY_MOD_ASSET",
    }
)
SOURCE_KINDS = (
    "CURRENT_V4_EVIDENCE",
    "BINARY_V4_REBUILD",
    "WC_REFLECTION",
    "CLIPBOARD_GRAPH_CAPTURE",
    "DEFAULTS_EXPORT",
    "LOCALIZATION_IMPORT",
    "DATASET_IMPORT",
    "USER_SUPPLIED_ASSET",
    "USER_SUPPLIED_MOD_ASSET",
    "NATIVE_SOURCE_REFERENCE",
)


class SolverContractError(McpExecutionError):
    """Stable, path-free validation failure for the Solver service boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            retryable=False,
            details=dict(details or {}),
        )


__all__ = [
    "COMPILER_VERSION",
    "COMPLETENESS_POLICIES",
    "CONSTRAINT_FIELDS",
    "EVIDENCE_KINDS",
    "EVIDENCE_MATRIX_SCHEMA",
    "INTENTS",
    "MAX_ACCEPTANCE_CRITERIA",
    "MAX_EVIDENCE_REQUIREMENTS",
    "MAX_OPERATOR_COUNT",
    "MAX_RAW_REQUEST_CHARS",
    "MAX_SUBPROBLEMS",
    "MAX_TARGET_HINTS",
    "MAX_UNASSIGNED_NON_WHITESPACE",
    "OPERATOR_KINDS",
    "OPERATOR_REGISTRY_SCHEMA",
    "OPERATOR_REGISTRY_VERSION",
    "OUTPUT_KINDS",
    "PROPOSAL_SCHEMA",
    "REQUIREMENT_IR_SCHEMA",
    "RESEARCH_PLAN_SCHEMA",
    "SOURCE_KINDS",
    "SOURCE_REGISTRY_VERSION",
    "SolverContractError",
    "TARGET_ROLES",
]
