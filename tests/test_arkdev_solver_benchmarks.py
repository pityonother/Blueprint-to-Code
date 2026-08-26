from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "solver_benchmarks"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.solver.evidence_matrix import derive_evidence_matrix  # noqa: E402
from arkdev_mcp.solver.requirement_compiler import compile_requirement  # noqa: E402
from arkdev_mcp.solver.research_plan import build_research_plan  # noqa: E402


SOLVER_ID = "solver://" + ("c" * 32)
BENCHMARK_FILES = (
    "formula.json",
    "enumeration_localized.json",
    "influence.json",
    "ranking.json",
    "blueprint_change.json",
    "unrelated_control.json",
    "multi_question_zh.json",
)
FORBIDDEN_PRODUCTION_MARKERS = (
    "ShoulderDragon",
    "Rhyniognatha",
    "Reaper",
    "精灵龙",
    "莱尼虫",
    "死神",
    "TreasureMap_ShoulderDragon",
)


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def compile_fixture(
    name: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    fixture = load_fixture(name)
    requirement = compile_requirement(fixture["proposal"], solver_id=SOLVER_ID)
    plan = build_research_plan(requirement)
    matrix = derive_evidence_matrix(requirement, plan)
    return requirement, plan, matrix


def plan_signature(
    plan: dict[str, object], problem_id: str | None = None
) -> list[tuple[str, tuple[str, ...]]]:
    operators = [
        item
        for item in plan["operators"]
        if problem_id is None or item["problemId"] == problem_id
    ]
    kinds_by_id = {item["operatorId"]: item["kind"] for item in operators}
    return [
        (
            item["kind"],
            tuple(kinds_by_id[parent] for parent in item["dependsOn"]),
        )
        for item in operators
    ]


def evidence_signature(
    matrix: dict[str, object], problem_id: str | None = None
) -> set[tuple[str, bool]]:
    return {
        (item["kind"], item["blocking"])
        for item in matrix["requirements"]
        if problem_id is None or item["problemId"] == problem_id
    }


class SolverBenchmarkTests(unittest.TestCase):
    def test_all_benchmark_fixtures_compile_to_declared_generic_contracts(self) -> None:
        for name in BENCHMARK_FILES:
            with self.subTest(name=name):
                fixture = load_fixture(name)
                requirement, plan, matrix = compile_fixture(name)
                expected = fixture["expected"]

                self.assertEqual(
                    [item["outputKind"] for item in requirement["subproblems"]],
                    expected["outputKinds"],
                )
                actual_operators = {item["kind"] for item in plan["operators"]}
                actual_evidence = {item["kind"] for item in matrix["requirements"]}
                self.assertTrue(set(expected["operators"]).issubset(actual_operators))
                self.assertTrue(
                    set(expected["evidenceKinds"]).issubset(actual_evidence)
                )
                self.assertLessEqual(len(plan["operators"]), 64)
                self.assertLessEqual(len(matrix["requirements"]), 64)

    def test_asset_name_and_language_changes_preserve_operator_dag_shape(self) -> None:
        fixture = load_fixture("unrelated_control.json")
        base = fixture["proposal"]
        renamed = copy.deepcopy(base)
        renamed_text = renamed["rawRequest"].replace(
            "ServerAuthorityFixture", "DifferentAssetFixture"
        )
        renamed["rawRequest"] = renamed_text
        renamed["subproblems"][0]["sourceText"] = renamed_text
        renamed["subproblems"][0]["sourceEnd"] = len(renamed_text)
        renamed["subproblems"][0]["targetHints"][0]["text"] = "DifferentAssetFixture"
        english = copy.deepcopy(base)
        english_text = (
            "Find every writer of a variable and design a server-only write change."
        )
        english["rawRequest"] = english_text
        english["language"] = "en-US"
        english["subproblems"][0]["sourceText"] = english_text
        english["subproblems"][0]["sourceEnd"] = len(english_text)

        base_plan = build_research_plan(compile_requirement(base, solver_id=SOLVER_ID))
        renamed_plan = build_research_plan(
            compile_requirement(renamed, solver_id=SOLVER_ID)
        )
        english_plan = build_research_plan(
            compile_requirement(english, solver_id=SOLVER_ID)
        )

        self.assertEqual(plan_signature(base_plan), plan_signature(renamed_plan))
        self.assertEqual(plan_signature(base_plan), plan_signature(english_plan))

    def test_top_k_change_preserves_dag_and_evidence_shape(self) -> None:
        fixture = load_fixture("ranking.json")
        top_ten = fixture["proposal"]
        top_five = copy.deepcopy(top_ten)
        text = top_five["rawRequest"].replace("Top 10", "Top 5")
        top_five["rawRequest"] = text
        top_five["subproblems"][0]["sourceText"] = text
        top_five["subproblems"][0]["sourceEnd"] = len(text)
        top_five["subproblems"][0]["constraints"]["topK"] = 5
        top_five["subproblems"][0]["acceptanceCriteria"] = ["返回按公式排序的前 5 项"]

        ten_ir = compile_requirement(top_ten, solver_id=SOLVER_ID)
        five_ir = compile_requirement(top_five, solver_id=SOLVER_ID)
        ten_plan = build_research_plan(ten_ir)
        five_plan = build_research_plan(five_ir)
        ten_matrix = derive_evidence_matrix(ten_ir, ten_plan)
        five_matrix = derive_evidence_matrix(five_ir, five_plan)

        self.assertEqual(plan_signature(ten_plan), plan_signature(five_plan))
        self.assertEqual(
            evidence_signature(ten_matrix), evidence_signature(five_matrix)
        )
        self.assertEqual(ten_ir["subproblems"][0]["constraints"]["topK"], 10)
        self.assertEqual(five_ir["subproblems"][0]["constraints"]["topK"], 5)
        self.assertNotEqual(ten_ir["semanticDigest"], five_ir["semanticDigest"])

    def test_localization_toggle_removes_only_localization_operator_and_requirement(
        self,
    ) -> None:
        fixture = load_fixture("enumeration_localized.json")
        localized = fixture["proposal"]
        neutral = copy.deepcopy(localized)
        neutral["subproblems"][0]["constraints"]["localizedNamesOnly"] = False

        localized_ir = compile_requirement(localized, solver_id=SOLVER_ID)
        neutral_ir = compile_requirement(neutral, solver_id=SOLVER_ID)
        localized_plan = build_research_plan(localized_ir)
        neutral_plan = build_research_plan(neutral_ir)
        localized_matrix = derive_evidence_matrix(localized_ir, localized_plan)
        neutral_matrix = derive_evidence_matrix(neutral_ir, neutral_plan)

        localized_kinds = [item[0] for item in plan_signature(localized_plan)]
        neutral_kinds = [item[0] for item in plan_signature(neutral_plan)]
        self.assertEqual(
            [kind for kind in localized_kinds if kind != "RESOLVE_LOCALIZATION"],
            neutral_kinds,
        )
        localized_evidence = {item[0] for item in evidence_signature(localized_matrix)}
        neutral_evidence = {item[0] for item in evidence_signature(neutral_matrix)}
        self.assertEqual(localized_evidence - {"LOCALIZATION"}, neutral_evidence)

    def test_best_effort_changes_completeness_policy_but_not_core_operator_kinds(
        self,
    ) -> None:
        fixture = load_fixture("influence.json")
        required = fixture["proposal"]
        best_effort = copy.deepcopy(required)
        best_effort["subproblems"][0]["completeness"] = "BEST_EFFORT"

        required_ir = compile_requirement(required, solver_id=SOLVER_ID)
        best_effort_ir = compile_requirement(best_effort, solver_id=SOLVER_ID)
        required_plan = build_research_plan(required_ir)
        best_effort_plan = build_research_plan(best_effort_ir)
        required_matrix = derive_evidence_matrix(required_ir, required_plan)
        best_effort_matrix = derive_evidence_matrix(best_effort_ir, best_effort_plan)

        self.assertEqual(
            [item["kind"] for item in required_plan["operators"]],
            [item["kind"] for item in best_effort_plan["operators"]],
        )
        required_gate = next(
            item
            for item in required_plan["operators"]
            if item["kind"] == "VERIFY_COMPLETENESS"
        )
        best_effort_gate = next(
            item
            for item in best_effort_plan["operators"]
            if item["kind"] == "VERIFY_COMPLETENESS"
        )
        self.assertNotEqual(
            required_gate["stopCondition"], best_effort_gate["stopCondition"]
        )
        self.assertIn(
            "NATIVE_SOURCE", {item["kind"] for item in required_matrix["requirements"]}
        )
        self.assertNotIn(
            "NATIVE_SOURCE",
            {item["kind"] for item in best_effort_matrix["requirements"]},
        )

    def test_production_solver_has_no_benchmark_entity_special_cases(self) -> None:
        solver_root = ROOT / "scripts" / "arkdev_mcp" / "solver"
        production = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(solver_root.glob("*.py"))
        )
        for marker in FORBIDDEN_PRODUCTION_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, production)


if __name__ == "__main__":
    unittest.main()
