from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import McpExecutionError  # noqa: E402
from arkdev_mcp.solver.store import SolverStore  # noqa: E402


SOLVER_ID = "solver://" + "a" * 32
DOCUMENT_NAMES = (
    "requirement.json",
    "research-plan.json",
    "evidence-matrix.json",
    "acquisition-plan.json",
    "state.json",
    "bindings.json",
)


def _documents() -> dict[str, dict[str, object]]:
    return {
        "requirement": {
            "schema": "blueprint-to-code.requirement-ir/v1",
            "solverId": SOLVER_ID,
            "subproblems": [],
        },
        "researchPlan": {
            "schema": "blueprint-to-code.research-plan/v1",
            "solverId": SOLVER_ID,
            "operators": [],
        },
        "evidenceMatrix": {
            "schema": "blueprint-to-code.evidence-requirement-matrix/v1",
            "solverId": SOLVER_ID,
            "requirements": [],
        },
        "acquisitionPlan": {
            "schema": "blueprint-to-code.evidence-acquisition-plan/v1",
            "solverId": SOLVER_ID,
            "actions": [],
        },
        "state": {
            "schema": "blueprint-to-code.solver-state/v1",
            "solverId": SOLVER_ID,
            "status": "CREATED",
        },
        "bindings": {
            "schema": "blueprint-to-code.solver-bindings/v1",
            "solverId": SOLVER_ID,
            "assets": {},
            "tasks": {},
            "pendingTasks": {},
        },
    }


class SolverStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name) / ".blueprint-solvers"
        self.store = SolverStore(self.root)

    def test_create_persists_exactly_six_canonical_json_documents(self) -> None:
        self.store.create_solver(SOLVER_ID, _documents())

        solver_dir = self.root / ("a" * 32)
        self.assertEqual(
            sorted(path.name for path in solver_dir.iterdir()),
            sorted(DOCUMENT_NAMES),
        )
        loaded = self.store.load_solver(SOLVER_ID)
        self.assertEqual(loaded, _documents())
        for filename in DOCUMENT_NAMES:
            raw = (solver_dir / filename).read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("\n"))
            self.assertEqual(raw.count("\n"), 1)
            self.assertIsInstance(json.loads(raw), dict)

        (solver_dir / "unexpected.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(McpExecutionError) as unexpected:
            self.store.load_solver(SOLVER_ID)
        self.assertEqual(unexpected.exception.code, "INTERNAL_CONTRACT_ERROR")

    def test_handle_validation_rejects_traversal_and_wrong_roots(self) -> None:
        with self.assertRaises(McpExecutionError) as bad_root:
            SolverStore(Path(self._temporary.name) / "solvers")
        self.assertEqual(bad_root.exception.code, "INVALID_ARGUMENT")

        invalid = (
            "../" + "a" * 32,
            "solver://../" + "a" * 32,
            "solver://" + "A" * 32,
            "solver://" + "a" * 31,
            "task://" + "a" * 32,
            "solver://" + "a" * 32 + "/state.json",
        )
        for value in invalid:
            with (
                self.subTest(value=value),
                self.assertRaises(McpExecutionError) as raised,
            ):
                self.store.load_solver(value)
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_save_documents_uses_sibling_temp_flush_and_replace(self) -> None:
        self.store.create_solver(SOLVER_ID, _documents())
        changed = _documents()
        changed["state"] = {**changed["state"], "status": "PREFLIGHT"}

        real_replace = os.replace
        replacements: list[tuple[Path, Path]] = []

        def recording_replace(
            source: str | os.PathLike[str], target: str | os.PathLike[str]
        ) -> None:
            source_path = Path(source)
            target_path = Path(target)
            self.assertEqual(source_path.parent, target_path.parent)
            if source_path.is_file():
                self.assertTrue(source_path.name.endswith(".tmp"))
                with source_path.open("rb") as handle:
                    os.fstat(handle.fileno())
            replacements.append((source_path, target_path))
            real_replace(source_path, target_path)

        with patch("arkdev_mcp.solver.store.os.replace", side_effect=recording_replace):
            self.store.save_solver(SOLVER_ID, changed)

        self.assertEqual(len(replacements), 8)
        self.assertEqual(
            self.store.load_solver(SOLVER_ID)["state"]["status"], "PREFLIGHT"
        )
        self.assertFalse(list((self.root / ("a" * 32)).glob("*.tmp")))

    def test_save_failure_never_exposes_mixed_six_document_generation(self) -> None:
        self.store.create_solver(SOLVER_ID, _documents())
        changed = _documents()
        changed["state"] = {**changed["state"], "status": "PREFLIGHT"}
        changed["bindings"] = {
            **changed["bindings"],
            "tasks": {"problem": "task://" + "1" * 32},
        }
        real_replace = os.replace
        calls = 0

        def fail_second_replace(
            source: str | os.PathLike[str], target: str | os.PathLike[str]
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture interruption")
            real_replace(source, target)

        with patch(
            "arkdev_mcp.solver.store.os.replace", side_effect=fail_second_replace
        ):
            with self.assertRaises(McpExecutionError):
                self.store.save_solver(SOLVER_ID, changed)

        loaded = self.store.load_solver(SOLVER_ID)
        self.assertEqual(loaded, _documents())

    def test_abrupt_interruption_never_exposes_mixed_six_document_generation(
        self,
    ) -> None:
        class SimulatedProcessTermination(BaseException):
            pass

        for interrupt_after, expected in ((1, "old"), (2, "new")):
            with self.subTest(interrupt_after=interrupt_after):
                root = (
                    Path(self._temporary.name)
                    / f"interrupt-{interrupt_after}"
                    / ".blueprint-solvers"
                )
                store = SolverStore(root)
                original = _documents()
                changed = {
                    key: {**document, "generationFixture": "new"}
                    for key, document in _documents().items()
                }
                store.create_solver(SOLVER_ID, original)
                real_replace = os.replace
                live_generation_replacements = 0

                def interrupt_generation_swap(
                    source: str | os.PathLike[str], target: str | os.PathLike[str]
                ) -> None:
                    nonlocal live_generation_replacements
                    source_path = Path(source)
                    target_path = Path(target)
                    real_replace(source_path, target_path)
                    if source_path.parent == root and target_path.parent == root:
                        live_generation_replacements += 1
                        if live_generation_replacements == interrupt_after:
                            raise SimulatedProcessTermination

                try:
                    with patch(
                        "arkdev_mcp.solver.store.os.replace",
                        side_effect=interrupt_generation_swap,
                    ):
                        store.save_solver(SOLVER_ID, changed)
                except SimulatedProcessTermination:
                    pass

                loaded = store.load_solver(SOLVER_ID)
                self.assertEqual(
                    loaded,
                    original if expected == "old" else changed,
                )

    def test_failed_publish_and_failed_rollback_never_leave_a_mixed_generation(
        self,
    ) -> None:
        original = _documents()
        self.store.create_solver(SOLVER_ID, original)
        changed = {
            key: {**document, "generationFixture": "new"}
            for key, document in _documents().items()
        }
        real_replace = os.replace
        live_replacements = 0

        def fail_second_publish_and_rollback(
            source: str | os.PathLike[str], target: str | os.PathLike[str]
        ) -> None:
            nonlocal live_replacements
            source_path = Path(source)
            target_path = Path(target)
            if source_path.parent == self.root and target_path.parent == self.root:
                live_replacements += 1
                if live_replacements in {2, 3, 4}:
                    raise OSError("fixture publish/rollback failure")
            real_replace(source_path, target_path)

        with patch(
            "arkdev_mcp.solver.store.os.replace",
            side_effect=fail_second_publish_and_rollback,
        ):
            with self.assertRaises(McpExecutionError):
                self.store.save_solver(SOLVER_ID, changed)

        loaded = self.store.load_solver(SOLVER_ID)
        self.assertIn(loaded, (original, changed))

    def test_invalid_or_incomplete_metadata_fails_closed(self) -> None:
        self.store.create_solver(SOLVER_ID, _documents())
        solver_dir = self.root / ("a" * 32)
        (solver_dir / "bindings.json").unlink()
        with self.assertRaises(McpExecutionError) as missing:
            self.store.load_solver(SOLVER_ID)
        self.assertEqual(missing.exception.code, "SOLVER_NOT_FOUND")

        (solver_dir / "bindings.json").write_text("[]\n", encoding="utf-8")
        with self.assertRaises(McpExecutionError) as malformed:
            self.store.load_solver(SOLVER_ID)
        self.assertEqual(malformed.exception.code, "INTERNAL_CONTRACT_ERROR")


if __name__ == "__main__":
    unittest.main()
