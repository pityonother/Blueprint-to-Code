"""Fixed v1 Operator Registry; callers cannot register arbitrary operators."""

from __future__ import annotations

from .canonical import semantic_digest
from .contracts import (
    OPERATOR_KINDS,
    OPERATOR_REGISTRY_SCHEMA,
    OPERATOR_REGISTRY_VERSION,
)


_OPERATOR_EVIDENCE: dict[str, tuple[str, ...]] = {
    "DISCOVER_TARGETS": ("ASSET_IDENTITY",),
    "CHECK_EVIDENCE_COVERAGE": ("ASSET_IDENTITY",),
    "ACQUIRE_EVIDENCE": (),
    "TRACE_VALUE_FORMULA": ("GRAPH_STRUCTURE", "DEFAULTS"),
    "EVALUATE_FORMULA_EXAMPLES": ("GRAPH_STRUCTURE", "DEFAULTS"),
    "EXPAND_REFERENCE_CLOSURE": ("REFERENCE_CLOSURE",),
    "BACKWARD_INFLUENCE_SLICE": ("GRAPH_STRUCTURE", "DEFAULTS"),
    "FIND_READERS_AND_WRITERS": ("GRAPH_STRUCTURE",),
    "RESOLVE_LOCALIZATION": ("LOCALIZATION",),
    "NORMALIZE_ENTITY_DATASET": ("ENTITY_DATASET", "DATASET_SCHEMA"),
    "EVALUATE_EXPRESSION": ("USER_FORMULA", "ENTITY_DATASET"),
    "RANK_RESULTS": ("ENTITY_DATASET",),
    "ANALYZE_CURRENT_BEHAVIOR": ("GRAPH_STRUCTURE", "DEFAULTS"),
    "BUILD_DESIRED_BEHAVIOR_CONTRACT": (),
    "COMPARE_CURRENT_TO_DESIRED": ("GRAPH_STRUCTURE", "DEFAULTS"),
    "COMPILE_PATCH_PLAN": ("NODE_IDENTITY", "PIN_SIGNATURES", "DEFAULTS"),
    "VERIFY_COMPLETENESS": (),
    "SYNTHESIZE_ANSWER": (),
}

_OUTPUT_TYPES: dict[str, tuple[str, ...]] = {
    "DISCOVER_TARGETS": ("TargetCandidates",),
    "CHECK_EVIDENCE_COVERAGE": ("EvidenceCoverage",),
    "ACQUIRE_EVIDENCE": ("AcquisitionActions",),
    "TRACE_VALUE_FORMULA": ("FormulaTrace",),
    "EVALUATE_FORMULA_EXAMPLES": ("FormulaExamples",),
    "EXPAND_REFERENCE_CLOSURE": ("ReferenceClosure",),
    "BACKWARD_INFLUENCE_SLICE": ("InfluenceSlice",),
    "FIND_READERS_AND_WRITERS": ("ReadWriteSites",),
    "RESOLVE_LOCALIZATION": ("LocalizedEntities",),
    "NORMALIZE_ENTITY_DATASET": ("NormalizedDataset",),
    "EVALUATE_EXPRESSION": ("EvaluatedDataset",),
    "RANK_RESULTS": ("RankedResults",),
    "ANALYZE_CURRENT_BEHAVIOR": ("CurrentBehaviorContract",),
    "BUILD_DESIRED_BEHAVIOR_CONTRACT": ("DesiredBehaviorContract",),
    "COMPARE_CURRENT_TO_DESIRED": ("BehaviorDifference",),
    "COMPILE_PATCH_PLAN": ("PatchPlanDraftInput",),
    "VERIFY_COMPLETENESS": ("CompletenessResult",),
    "SYNTHESIZE_ANSWER": ("AnswerDraft",),
}


def _definition(kind: str) -> dict[str, object]:
    default_budget = {
        "maxInputs": 16,
        "maxItems": 200,
        "maxEstimatedTokens": 4_000,
    }
    if kind in {"DISCOVER_TARGETS", "CHECK_EVIDENCE_COVERAGE", "ACQUIRE_EVIDENCE"}:
        default_budget = {
            "maxInputs": 8,
            "maxItems": 100,
            "maxEstimatedTokens": 2_000,
        }
    return {
        "kind": kind,
        "inputTypes": ["RequirementSubproblem"],
        "outputTypes": list(_OUTPUT_TYPES[kind]),
        "requiredEvidenceKinds": list(_OPERATOR_EVIDENCE[kind]),
        "supportsCompleteness": kind
        in {
            "CHECK_EVIDENCE_COVERAGE",
            "EXPAND_REFERENCE_CLOSURE",
            "BACKWARD_INFLUENCE_SLICE",
            "FIND_READERS_AND_WRITERS",
            "VERIFY_COMPLETENESS",
        },
        "maxInputs": int(default_budget["maxInputs"]),
        "defaultBudget": default_budget,
    }


OPERATOR_REGISTRY: dict[str, object] = {
    "schema": OPERATOR_REGISTRY_SCHEMA,
    "version": OPERATOR_REGISTRY_VERSION,
    "operators": [_definition(kind) for kind in OPERATOR_KINDS],
}
OPERATOR_REGISTRY["semanticDigest"] = semantic_digest(OPERATOR_REGISTRY)
OPERATOR_DEFINITIONS = {
    str(item["kind"]): item for item in OPERATOR_REGISTRY["operators"]
}


__all__ = ["OPERATOR_DEFINITIONS", "OPERATOR_REGISTRY"]
