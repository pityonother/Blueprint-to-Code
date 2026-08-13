from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import McpExecutionError, assert_path_free  # noqa: E402
from arkdev_mcp.solver.contracts import (  # noqa: E402
    COMPILER_VERSION,
    EVIDENCE_KINDS,
    OPERATOR_KINDS,
    SolverContractError,
)
from arkdev_mcp.solver.evidence_matrix import (  # noqa: E402
    derive_evidence_matrix,
)
from arkdev_mcp.solver.operator_registry import OPERATOR_REGISTRY  # noqa: E402
from arkdev_mcp.solver.proposal_validator import (  # noqa: E402
    validate_requirement_proposal,
)
from arkdev_mcp.solver.requirement_compiler import (  # noqa: E402
    compile_requirement,
)
from arkdev_mcp.solver.research_plan import build_research_plan  # noqa: E402
from arkdev_mcp.solver.source_registry import SOURCE_REGISTRY  # noqa: E402


SOLVER_A = "solver://" + ("a" * 32)
SOLVER_B = "solver://" + ("b" * 32)


def proposal_for(
    text: str,
    *,
    output_kind: str = "FORMULA",
    intent: str = "ANSWER_CURRENT_BEHAVIOR",
    completeness: str = "REQUIRED",
    constraints: dict[str, object] | None = None,
    target_hints: list[dict[str, object]] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "blueprint-to-code.requirement-proposal/v1",
        "rawRequest": text,
        "language": "zh-CN",
        "subproblems": [
            {
                "sourceStart": 0,
                "sourceEnd": len(text),
                "sourceText": text,
                "intent": intent,
                "outputKind": output_kind,
                "completeness": completeness,
                "targetHints": target_hints or [],
                "constraints": constraints or {},
                "acceptanceCriteria": acceptance_criteria or [],
            }
        ],
    }


def operator_kinds(plan: dict[str, object]) -> list[str]:
    return [str(item["kind"]) for item in plan["operators"]]


def evidence_kinds(matrix: dict[str, object]) -> set[str]:
    return {str(item["kind"]) for item in matrix["requirements"]}


class RequirementCompilerTests(unittest.TestCase):
    def test_validation_errors_preserve_the_public_mcp_error_contract(self) -> None:
        invalid = proposal_for("分析公式")
        invalid["subproblems"][0]["sourceText"] = "不匹配"

        with self.assertRaises(SolverContractError) as raised:
            validate_requirement_proposal(invalid)

        self.assertIsInstance(raised.exception, McpExecutionError)
        self.assertEqual(raised.exception.code, "REQUIREMENT_PROPOSAL_INVALID")
        self.assertFalse(raised.exception.retryable)

    def test_compiler_binds_spans_derives_mode_and_is_semantically_deterministic(
        self,
    ) -> None:
        proposal = proposal_for("等级输入如何影响档位与最终数量？")

        first = compile_requirement(proposal, solver_id=SOLVER_A)
        second = compile_requirement(copy.deepcopy(proposal), solver_id=SOLVER_B)

        self.assertEqual(first["schema"], "blueprint-to-code.requirement-ir/v1")
        self.assertEqual(first["compilerVersion"], COMPILER_VERSION)
        self.assertEqual(first["mode"], "ANSWER")
        self.assertEqual(first["unassignedText"], "")
        self.assertEqual(first["subproblems"][0]["sourceText"], proposal["rawRequest"])
        self.assertEqual(first["subproblems"][0]["status"], "CREATED")
        self.assertRegex(
            first["subproblems"][0]["problemId"], r"^problem://[0-9a-f]{24}$"
        )
        self.assertRegex(first["semanticDigest"], r"^[0-9a-f]{64}$")
        self.assertEqual(first["semanticDigest"], second["semanticDigest"])
        self.assertEqual(
            first["subproblems"][0]["problemId"],
            second["subproblems"][0]["problemId"],
        )
        assert_path_free(first)

    def test_mixed_intents_derive_mixed_mode(self) -> None:
        raw = "Explain current behavior. Design a safe change."
        first = "Explain current behavior."
        second = "Design a safe change."
        second_start = raw.index(second)
        proposal = {
            "schema": "blueprint-to-code.requirement-proposal/v1",
            "rawRequest": raw,
            "language": "en",
            "subproblems": [
                {
                    "sourceStart": 0,
                    "sourceEnd": len(first),
                    "sourceText": first,
                    "intent": "ANSWER_CURRENT_BEHAVIOR",
                    "outputKind": "CURRENT_BEHAVIOR",
                    "completeness": "BEST_EFFORT",
                    "targetHints": [],
                    "constraints": {},
                    "acceptanceCriteria": [],
                },
                {
                    "sourceStart": second_start,
                    "sourceEnd": len(raw),
                    "sourceText": second,
                    "intent": "DESIGN_BLUEPRINT_CHANGE",
                    "outputKind": "BLUEPRINT_CHANGE",
                    "completeness": "BEST_EFFORT",
                    "targetHints": [],
                    "constraints": {
                        "desiredBehavior": ["Apply the safe change"],
                        "invariants": ["Preserve existing behavior"],
                        "acceptanceTests": ["Verify the intended boundary"],
                    },
                    "acceptanceCriteria": [],
                },
            ],
        }

        result = compile_requirement(proposal, solver_id=SOLVER_A)

        self.assertEqual(result["mode"], "MIXED")

    def test_validator_rejects_mismatched_unsorted_and_overlapping_spans(self) -> None:
        raw = "first second third"
        base = proposal_for(raw)

        mismatch = copy.deepcopy(base)
        mismatch["subproblems"][0]["sourceText"] = "different"
        unsorted = copy.deepcopy(base)
        unsorted["subproblems"] = [
            {
                **copy.deepcopy(base["subproblems"][0]),
                "sourceStart": 6,
                "sourceEnd": 12,
                "sourceText": "second",
            },
            {
                **copy.deepcopy(base["subproblems"][0]),
                "sourceStart": 0,
                "sourceEnd": 5,
                "sourceText": "first",
            },
        ]
        overlap = copy.deepcopy(base)
        overlap["subproblems"] = [
            {
                **copy.deepcopy(base["subproblems"][0]),
                "sourceStart": 0,
                "sourceEnd": 12,
                "sourceText": "first second",
            },
            {
                **copy.deepcopy(base["subproblems"][0]),
                "sourceStart": 6,
                "sourceEnd": 18,
                "sourceText": "second third",
            },
        ]

        for candidate in (mismatch, unsorted, overlap):
            with self.subTest(candidate=candidate["subproblems"]):
                with self.assertRaises(SolverContractError) as raised:
                    validate_requirement_proposal(candidate)
                self.assertEqual(raised.exception.code, "REQUIREMENT_PROPOSAL_INVALID")

    def test_more_than_twenty_non_whitespace_unassigned_characters_fail_closed(
        self,
    ) -> None:
        assigned = "分析这个蓝图。"
        proposal = proposal_for(assigned)
        proposal["rawRequest"] = (
            assigned + "还必须验证这一整段没有被任何子问题绑定的重要要求。"
        )

        with self.assertRaises(SolverContractError) as raised:
            compile_requirement(proposal, solver_id=SOLVER_A)

        self.assertEqual(raised.exception.code, "REQUEST_TEXT_UNASSIGNED")
        self.assertGreater(raised.exception.details["unassignedCharacterCount"], 20)

    def test_small_unassigned_fragment_is_preserved_in_ir(self) -> None:
        assigned = "分析这个蓝图。"
        proposal = proposal_for(assigned)
        proposal["rawRequest"] = assigned + "附注"

        result = compile_requirement(proposal, solver_id=SOLVER_A)

        self.assertEqual(result["unassignedText"], "附注")

    def test_typed_constraints_reject_unknown_wrongly_typed_and_misplaced_values(
        self,
    ) -> None:
        candidates = (
            proposal_for("分析公式", constraints={"madeUp": True}),
            proposal_for("分析公式", constraints={"localizedNamesOnly": "yes"}),
            proposal_for("分析公式", constraints={"topK": 10}),
            proposal_for(
                "分析公式",
                constraints={
                    "candidateScope": [
                        "C:" + chr(92) + "private" + chr(92) + "asset"
                    ]
                },
            ),
        )

        for candidate in candidates:
            with self.subTest(constraints=candidate["subproblems"][0]["constraints"]):
                with self.assertRaises(SolverContractError) as raised:
                    validate_requirement_proposal(candidate)
                self.assertEqual(raised.exception.code, "REQUIREMENT_PROPOSAL_INVALID")

    def test_ranking_requires_bounded_top_k_formula_and_variable_mapping(self) -> None:
        missing_formula = proposal_for(
            "给出排行",
            output_kind="RANKING",
            constraints={"topK": 10},
        )
        out_of_range = proposal_for(
            "给出排行",
            output_kind="RANKING",
            constraints={
                "topK": 101,
                "userFormula": "x",
                "formulaVariables": {"x": "value"},
            },
        )
        valid = proposal_for(
            "给出排行",
            output_kind="RANKING",
            constraints={
                "topK": 10,
                "userFormula": "abs(x - target)",
                "formulaVariables": {"x": "candidateValue", "target": "requestedValue"},
            },
        )

        for candidate in (missing_formula, out_of_range):
            with self.assertRaises(SolverContractError):
                validate_requirement_proposal(candidate)

        result = compile_requirement(valid, solver_id=SOLVER_A)
        self.assertEqual(result["subproblems"][0]["constraints"]["topK"], 10)
        self.assertEqual(
            result["subproblems"][0]["constraints"]["userFormula"],
            "abs(x - target)",
        )

    def test_blueprint_change_requires_desired_behavior_invariants_and_tests(
        self,
    ) -> None:
        incomplete = proposal_for(
            "修改蓝图",
            output_kind="BLUEPRINT_CHANGE",
            intent="DESIGN_BLUEPRINT_CHANGE",
            constraints={"desiredBehavior": ["封顶数量"]},
        )
        valid = proposal_for(
            "修改蓝图",
            output_kind="BLUEPRINT_CHANGE",
            intent="DESIGN_BLUEPRINT_CHANGE",
            constraints={
                "desiredBehavior": ["超过阈值后封顶"],
                "invariants": ["阈值以下行为不变"],
                "acceptanceTests": ["阈值上下边界均验证"],
            },
        )

        with self.assertRaises(SolverContractError):
            validate_requirement_proposal(incomplete)
        self.assertEqual(
            compile_requirement(valid, solver_id=SOLVER_A)["mode"], "CHANGE"
        )

    def test_fixed_formula_dag_is_acyclic_stable_and_bounded(self) -> None:
        requirement = compile_requirement(
            proposal_for("等级输入如何影响档位与最终数量？"),
            solver_id=SOLVER_A,
        )

        first = build_research_plan(requirement)
        second_requirement = {**requirement, "solverId": SOLVER_B}
        second = build_research_plan(second_requirement)

        self.assertEqual(
            operator_kinds(first),
            [
                "DISCOVER_TARGETS",
                "CHECK_EVIDENCE_COVERAGE",
                "ACQUIRE_EVIDENCE",
                "TRACE_VALUE_FORMULA",
                "EVALUATE_FORMULA_EXAMPLES",
                "VERIFY_COMPLETENESS",
                "SYNTHESIZE_ANSWER",
            ],
        )
        ids = [item["operatorId"] for item in first["operators"]]
        self.assertEqual(ids, [item["operatorId"] for item in second["operators"]])
        seen: set[str] = set()
        for operator in first["operators"]:
            self.assertTrue(set(operator["dependsOn"]).issubset(seen))
            self.assertLessEqual(operator["budget"]["maxInputs"], 16)
            self.assertLessEqual(operator["budget"]["maxItems"], 500)
            self.assertLessEqual(operator["budget"]["maxEstimatedTokens"], 8000)
            seen.add(operator["operatorId"])
        self.assertEqual(first["operators"][0]["status"], "READY")
        self.assertEqual(first["operators"][1]["status"], "PENDING")
        self.assertEqual(first["operators"][2]["status"], "BLOCKED_BY_EVIDENCE")
        self.assertTrue(
            all(item["status"] == "NOT_IMPLEMENTED" for item in first["operators"][3:])
        )
        self.assertEqual(first["semanticDigest"], second["semanticDigest"])
        assert_path_free(first)

    def test_all_output_contracts_emit_resolvable_evidence_references(self) -> None:
        cases = {
            "FORMULA": (
                proposal_for("追踪一个数值公式", output_kind="FORMULA"),
                [
                    "DISCOVER_TARGETS",
                    "CHECK_EVIDENCE_COVERAGE",
                    "ACQUIRE_EVIDENCE",
                    "TRACE_VALUE_FORMULA",
                    "EVALUATE_FORMULA_EXAMPLES",
                    "VERIFY_COMPLETENESS",
                    "SYNTHESIZE_ANSWER",
                ],
            ),
            "COMPLETE_ENUMERATION": (
                proposal_for(
                    "列出全部中文名称",
                    output_kind="COMPLETE_ENUMERATION",
                    constraints={
                        "localizedNamesOnly": True,
                        "outputLanguage": "zh-CN",
                    },
                ),
                [
                    "DISCOVER_TARGETS",
                    "CHECK_EVIDENCE_COVERAGE",
                    "ACQUIRE_EVIDENCE",
                    "EXPAND_REFERENCE_CLOSURE",
                    "RESOLVE_LOCALIZATION",
                    "VERIFY_COMPLETENESS",
                    "SYNTHESIZE_ANSWER",
                ],
            ),
            "INFLUENCE_FACTORS": (
                proposal_for("找出所有影响因素", output_kind="INFLUENCE_FACTORS"),
                [
                    "DISCOVER_TARGETS",
                    "CHECK_EVIDENCE_COVERAGE",
                    "ACQUIRE_EVIDENCE",
                    "BACKWARD_INFLUENCE_SLICE",
                    "FIND_READERS_AND_WRITERS",
                    "VERIFY_COMPLETENESS",
                    "SYNTHESIZE_ANSWER",
                ],
            ),
            "RANKING": (
                proposal_for(
                    "按用户公式返回前十项",
                    output_kind="RANKING",
                    constraints={
                        "topK": 10,
                        "userFormula": "abs(value - target)",
                        "formulaVariables": {
                            "value": "candidateValue",
                            "target": "requestedValue",
                        },
                    },
                ),
                [
                    "DISCOVER_TARGETS",
                    "CHECK_EVIDENCE_COVERAGE",
                    "ACQUIRE_EVIDENCE",
                    "NORMALIZE_ENTITY_DATASET",
                    "EVALUATE_EXPRESSION",
                    "RANK_RESULTS",
                    "VERIFY_COMPLETENESS",
                    "SYNTHESIZE_ANSWER",
                ],
            ),
            "CURRENT_BEHAVIOR": (
                proposal_for("分析当前行为", output_kind="CURRENT_BEHAVIOR"),
                [
                    "DISCOVER_TARGETS",
                    "CHECK_EVIDENCE_COVERAGE",
                    "ACQUIRE_EVIDENCE",
                    "ANALYZE_CURRENT_BEHAVIOR",
                    "VERIFY_COMPLETENESS",
                    "SYNTHESIZE_ANSWER",
                ],
            ),
            "BLUEPRINT_CHANGE": (
                proposal_for(
                    "设计一个安全的蓝图修改",
                    output_kind="BLUEPRINT_CHANGE",
                    intent="DESIGN_BLUEPRINT_CHANGE",
                    constraints={
                        "desiredBehavior": ["只允许服务端写入"],
                        "invariants": ["现有读取行为不变"],
                        "acceptanceTests": ["客户端写入被拒绝"],
                    },
                ),
                [
                    "DISCOVER_TARGETS",
                    "CHECK_EVIDENCE_COVERAGE",
                    "ACQUIRE_EVIDENCE",
                    "ANALYZE_CURRENT_BEHAVIOR",
                    "BUILD_DESIRED_BEHAVIOR_CONTRACT",
                    "COMPARE_CURRENT_TO_DESIRED",
                    "COMPILE_PATCH_PLAN",
                    "VERIFY_COMPLETENESS",
                ],
            ),
        }

        for output_kind, (proposal, expected_operators) in cases.items():
            with self.subTest(output_kind=output_kind):
                requirement = compile_requirement(proposal, solver_id=SOLVER_A)
                plan = build_research_plan(requirement)
                matrix = derive_evidence_matrix(requirement, plan)
                problem_id = str(requirement["subproblems"][0]["problemId"])
                requirement_ids = {
                    str(item["requirementId"])
                    for item in matrix["requirements"]
                    if item["problemId"] == problem_id
                }

                self.assertEqual(operator_kinds(plan), expected_operators)
                for operator in plan["operators"]:
                    self.assertEqual(operator["problemId"], problem_id)
                    self.assertTrue(
                        set(operator["requiredEvidenceIds"]).issubset(
                            requirement_ids
                        ),
                        msg=f"{operator['kind']} has dangling Evidence references",
                    )

    def test_evidence_matrix_derives_contract_and_never_claims_pin_identity_for_change(
        self,
    ) -> None:
        requirement = compile_requirement(
            proposal_for(
                "修改蓝图",
                output_kind="BLUEPRINT_CHANGE",
                intent="DESIGN_BLUEPRINT_CHANGE",
                constraints={
                    "desiredBehavior": ["限制写入"],
                    "invariants": ["读取行为不变"],
                    "acceptanceTests": ["客户端写入被拒绝"],
                },
            ),
            solver_id=SOLVER_A,
        )
        plan = build_research_plan(requirement)

        matrix = derive_evidence_matrix(requirement, plan)

        self.assertEqual(
            evidence_kinds(matrix),
            {
                "ASSET_IDENTITY",
                "GRAPH_STRUCTURE",
                "NODE_IDENTITY",
                "PIN_SIGNATURES",
                "DEFAULTS",
            },
        )
        self.assertNotIn("PIN_IDENTITY", evidence_kinds(matrix))
        for item in matrix["requirements"]:
            self.assertRegex(item["requirementId"], r"^requirement://[0-9a-f]{24}$")
            self.assertEqual(item["status"], "UNRESOLVED")
            self.assertEqual(item["selectedSource"], "")
            self.assertLessEqual(len(item["candidateAssets"]), 20)
        self.assertLessEqual(len(matrix["requirements"]), 64)
        assert_path_free(matrix)

    def test_registries_are_closed_bounded_and_match_pr46_wc_capability_boundary(
        self,
    ) -> None:
        definitions = OPERATOR_REGISTRY["operators"]
        self.assertEqual({item["kind"] for item in definitions}, set(OPERATOR_KINDS))
        for definition in definitions:
            self.assertEqual(
                set(definition),
                {
                    "kind",
                    "inputTypes",
                    "outputTypes",
                    "requiredEvidenceKinds",
                    "supportsCompleteness",
                    "maxInputs",
                    "defaultBudget",
                },
            )
            self.assertLessEqual(definition["maxInputs"], 16)
            self.assertTrue(
                set(definition["requiredEvidenceKinds"]).issubset(EVIDENCE_KINDS)
            )

        wc = next(
            item
            for item in SOURCE_REGISTRY["sources"]
            if item["kind"] == "WC_REFLECTION"
        )
        self.assertFalse(wc["automatable"])
        self.assertTrue(wc["requiresUserAction"])
        self.assertTrue(wc["readOnly"])
        self.assertFalse(wc["mutation"])
        self.assertIn("NODE_IDENTITY", wc["providedEvidenceKinds"])
        self.assertNotIn("PIN_IDENTITY", wc["providedEvidenceKinds"])
        self.assertNotIn("GRAPH_STRUCTURE", wc["providedEvidenceKinds"])
        self.assertEqual(wc["verifiedArkBuilds"], ["5.5.4-0+UE5"])
        self.assertTrue(
            any("existing-node identity" in item for item in wc["limitations"])
        )
        self.assertTrue(any("Pin identity" in item for item in wc["limitations"]))

    def test_all_solver_json_schemas_validate_real_payloads_and_registries(
        self,
    ) -> None:
        requirement = compile_requirement(
            proposal_for("分析当前行为", output_kind="CURRENT_BEHAVIOR"),
            solver_id=SOLVER_A,
        )
        research_plan = build_research_plan(requirement)
        evidence_matrix = derive_evidence_matrix(requirement, research_plan)
        payloads = {
            "blueprint_requirement_proposal.v1.schema.json": proposal_for(
                "分析当前行为", output_kind="CURRENT_BEHAVIOR"
            ),
            "blueprint_requirement_ir.v1.schema.json": requirement,
            "blueprint_research_plan.v1.schema.json": research_plan,
            "blueprint_evidence_requirement_matrix.v1.schema.json": evidence_matrix,
            "blueprint_solver_operator_registry.v1.schema.json": OPERATOR_REGISTRY,
        }

        for filename, payload in payloads.items():
            with self.subTest(schema=filename):
                schema = json.loads(
                    (ROOT / "schemas" / filename).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(payload)

    def test_solver_ids_must_be_opaque_handles(self) -> None:
        with self.assertRaises(SolverContractError) as raised:
            compile_requirement(
                proposal_for("分析公式"), solver_id="solver://not-valid"
            )
        self.assertEqual(raised.exception.code, "REQUIREMENT_PROPOSAL_INVALID")

    def test_operator_and_evidence_enums_do_not_accept_arbitrary_names(self) -> None:
        self.assertNotIn("CUSTOM_OPERATOR", OPERATOR_KINDS)
        self.assertNotIn("WIKI_FACT", EVIDENCE_KINDS)
        self.assertEqual(len(OPERATOR_KINDS), len(set(OPERATOR_KINDS)))
        self.assertTrue(
            all(re.fullmatch(r"[A-Z0-9_]+", item) for item in OPERATOR_KINDS)
        )


if __name__ == "__main__":
    unittest.main()
