from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.contracts import McpExecutionError  # noqa: E402
from arkdev_mcp.solver.asset_discovery import discover_candidates  # noqa: E402
from arkdev_mcp.solver.acquisition_planner import build_acquisition_plan  # noqa: E402
from arkdev_mcp.solver.canonical import semantic_digest  # noqa: E402
from arkdev_mcp.solver.service import SolverService  # noqa: E402
from arkdev_mcp.solver.store import SolverStore  # noqa: E402


SOLVER_ID = "solver://" + "b" * 32
PROBLEM_ID = "problem://" + "1" * 24


def _health(
    asset: str,
    *,
    status: str = "READY",
    freshness: str = "FRESH",
    authority: bool = True,
    migration: bool = False,
    object_path: str | None = None,
) -> dict[str, object]:
    return {
        "ok": True,
        "schema": "blueprint-to-code.blueprint-evidence-health-response/v1",
        "asset": asset,
        "health": {
            "status": status,
            "reasonCode": "",
            "asset": {
                "name": asset,
                "assetId": (asset.lower().encode().hex() + "0" * 24)[:24],
                "objectPath": object_path or f"/Game/Test/{asset}.{asset}",
            },
            "evidence": {
                "revisionId": "c" * 24,
                "manifestSha256": "d" * 64,
                "pointerSha256": "e" * 64,
                "freshnessStatus": freshness,
                "releaseAuthority": authority,
                "migrationRequired": migration,
            },
            "interpretation": {
                "status": "CURRENT" if status == "READY" else "",
                "revisionId": "f" * 24 if status == "READY" else "",
                "manifestSha256": "1" * 64 if status == "READY" else "",
                "pointerSha256": "2" * 64 if status == "READY" else "",
                "semanticDigest": "3" * 64 if status == "READY" else "",
                "interpreterVersion": "fixture",
                "schemaVersion": "v1",
                "generatedAt": "2026-08-13T00:00:00Z",
            },
        },
    }


class FakeBlueprint:
    def __init__(self, health_by_asset: dict[str, dict[str, object]]) -> None:
        self.health_by_asset = health_by_asset
        self.list_calls: list[tuple[str, int, str]] = []
        self.health_calls: list[str] = []
        self.authority_calls: list[str] = []

    def list_assets(
        self, *, query: str = "", limit: int = 25, cursor: str = ""
    ) -> dict[str, object]:
        self.list_calls.append((query, limit, cursor))
        names = sorted(
            name for name in self.health_by_asset if query.casefold() in name.casefold()
        )
        return {
            "schema": "blueprint-to-code.mcp-blueprint-assets/v1",
            "items": [
                {
                    "asset": name,
                    "health": copy.deepcopy(self.health_by_asset[name]["health"]),
                }
                for name in names[:limit]
            ],
            "page": {
                "limit": limit,
                "returned": len(names[:limit]),
                "total": len(names),
                "nextCursor": None,
            },
        }

    def health(self, *, asset: str) -> dict[str, object]:
        self.health_calls.append(asset)
        return copy.deepcopy(self.health_by_asset[asset])

    def get_task_authority(self, *, asset: str) -> dict[str, object]:
        self.authority_calls.append(asset)
        health = self.health_by_asset[asset]["health"]
        evidence = health["evidence"]
        if evidence["freshnessStatus"] != "FRESH":
            raise McpExecutionError(
                "EVIDENCE_STALE", "Current Blueprint evidence is stale."
            )
        return {
            "name": asset,
            "assetId": health["asset"]["assetId"],
            "objectPath": health["asset"]["objectPath"],
            "evidenceRevisionId": evidence["revisionId"],
            "evidenceManifestSha256": evidence["manifestSha256"],
            "freshness": "FRESH",
            "graphTargets": [
                {
                    "ref": f"bp://{health['asset']['assetId']}@{'c' * 24}/g/7",
                    "name": "EventGraph",
                    "graphType": "EventGraph",
                    "status": "complete",
                    "confidence": "high",
                    "nodeCount": 5,
                    "pinCount": 10,
                    "linkCount": 4,
                }
            ],
        }


class FakeTasks:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.contexts: dict[str, dict[str, object]] = {}

    def create(self, **arguments: object) -> dict[str, object]:
        self.calls.append(arguments)
        task_id = str(arguments.get("task_id") or "task://" + "9" * 32)
        if task_id in self.contexts:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR", "The reserved Task already exists."
            )
        context = {
            "taskId": task_id,
            "mode": arguments["mode"],
            "goal": arguments["goal"],
            "completionCriteria": list(arguments["completion_criteria"]),
            "allowedChanges": list(arguments["allowed_changes"]),
            "forbiddenChanges": list(arguments["forbidden_changes"]),
            "primaryAsset": {
                "name": arguments["asset"],
                "assetId": ("TargetAsset".lower().encode().hex() + "0" * 24)[:24],
                "objectPath": "/Game/Test/TargetAsset.TargetAsset",
                "evidenceRevisionId": "c" * 24,
                "evidenceManifestSha256": "d" * 64,
                "freshness": "FRESH",
            },
            "graphTargets": [],
            "supportingAssets": [],
        }
        self.contexts[task_id] = context
        return copy.deepcopy(context)

    def verified_task(
        self, task_id: str, *, persist_verification: bool = True
    ) -> tuple[dict[str, object], dict[str, object]]:
        del persist_verification
        try:
            context = self.contexts[task_id]
        except KeyError as exc:
            raise McpExecutionError(
                "TASK_NOT_FOUND", "Task metadata was not found."
            ) from exc
        return copy.deepcopy(context), {"taskId": task_id, "phase": "DISCOVERY"}


def _documents(
    *,
    target_hints: list[dict[str, object]],
    requirements: list[dict[str, object]],
    intent: str = "ANSWER_CURRENT_BEHAVIOR",
) -> dict[str, dict[str, object]]:
    problem = {
        "problemId": PROBLEM_ID,
        "sourceText": "Inspect the target",
        "intent": intent,
        "outputKind": "BLUEPRINT_CHANGE"
        if intent == "DESIGN_BLUEPRINT_CHANGE"
        else "CURRENT_BEHAVIOR",
        "targetHints": target_hints,
        "constraints": {},
        "acceptanceCriteria": ["The requested behavior is satisfied"],
        "blockingQuestions": [],
        "status": "PENDING",
    }
    return {
        "requirement": {
            "schema": "blueprint-to-code.requirement-ir/v1",
            "solverId": SOLVER_ID,
            "rawRequest": "Inspect the target",
            "language": "en",
            "mode": "CHANGE" if intent == "DESIGN_BLUEPRINT_CHANGE" else "ANSWER",
            "subproblems": [problem],
            "blockingQuestions": [],
            "semanticDigest": "4" * 64,
        },
        "researchPlan": {
            "schema": "blueprint-to-code.research-plan/v1",
            "solverId": SOLVER_ID,
            "operators": [],
            "semanticDigest": "5" * 64,
        },
        "evidenceMatrix": {
            "schema": "blueprint-to-code.evidence-requirement-matrix/v1",
            "solverId": SOLVER_ID,
            "compilerVersion": "v1",
            "requirements": requirements,
            "semanticDigest": "6" * 64,
        },
        "acquisitionPlan": {
            "schema": "blueprint-to-code.evidence-acquisition-plan/v1",
            "solverId": SOLVER_ID,
            "actions": [],
            "semanticDigest": "7" * 64,
        },
        "state": {
            "schema": "blueprint-to-code.solver-state/v1",
            "solverId": SOLVER_ID,
            "status": "CREATED",
            "problemStatuses": {PROBLEM_ID: "PENDING"},
            "materializedTaskIds": {},
            "createdAt": "2026-08-13T00:00:00Z",
            "updatedAt": "2026-08-13T00:00:00Z",
            "semanticDigest": "8" * 64,
        },
        "bindings": {
            "schema": "blueprint-to-code.solver-bindings/v1",
            "solverId": SOLVER_ID,
            "assets": {},
            "tasks": {},
            "pendingTasks": {},
            "aliases": {},
            "datasetDescriptors": {},
            "localizationDescriptors": {},
            "modAssetDescriptors": {},
            "resolvedBlockingQuestions": {},
            "semanticDigest": "9" * 64,
        },
    }


def _requirement(kind: str, *, sources: list[str] | None = None) -> dict[str, object]:
    return {
        "requirementId": "requirement://"
        + kind.lower().encode().hex()[:24].ljust(24, "0"),
        "problemId": PROBLEM_ID,
        "kind": kind,
        "scope": {},
        "blocking": True,
        "reason": f"{kind} is required",
        "preferredSources": sources or ["CURRENT_V4_EVIDENCE", "BINARY_V4_REBUILD"],
        "candidateAssets": [],
        "status": "UNRESOLVED",
        "selectedSource": "",
        "gaps": [],
    }


def _solver_proposal(
    raw_request: str,
    *,
    intent: str = "DESIGN_BLUEPRINT_CHANGE",
    target_role: str = "PRIMARY_BLUEPRINT",
    acceptance_criteria: list[str] | None = None,
    desired_behavior: list[str] | None = None,
) -> dict[str, object]:
    constraints: dict[str, object] = {}
    if intent == "DESIGN_BLUEPRINT_CHANGE":
        constraints = {
            "desiredBehavior": desired_behavior or ["Apply the requested change"],
            "invariants": ["Preserve unrelated behavior"],
            "acceptanceTests": ["Verify the requested boundary"],
        }
    return {
        "schema": "blueprint-to-code.requirement-proposal/v1",
        "rawRequest": raw_request,
        "language": "en",
        "subproblems": [
            {
                "sourceStart": 0,
                "sourceEnd": len(raw_request),
                "sourceText": raw_request,
                "intent": intent,
                "outputKind": "BLUEPRINT_CHANGE"
                if intent == "DESIGN_BLUEPRINT_CHANGE"
                else "CURRENT_BEHAVIOR",
                "completeness": "BEST_EFFORT",
                "targetHints": [
                    {
                        "text": "TargetAsset",
                        "role": target_role,
                        "aliases": [],
                        "expectedKind": "BLUEPRINT",
                        "userSupplied": False,
                    }
                ],
                "constraints": constraints,
                "acceptanceCriteria": acceptance_criteria
                if acceptance_criteria is not None
                else ["Satisfy the requested behavior"],
            }
        ],
    }


class AssetDiscoveryTests(unittest.TestCase):
    def test_scoring_is_deterministic_bounded_and_never_uses_array_order(self) -> None:
        health_by_asset = {
            "Target": _health("Target", object_path="/Game/Z/Target.Target"),
            "target": _health("target", object_path="/Game/A/target.target"),
            "TargetSupport": _health("TargetSupport"),
        }
        blueprint = FakeBlueprint(health_by_asset)
        hint = {
            "text": "TARGET",
            "role": "PRIMARY_BLUEPRINT",
            "aliases": [],
            "expectedKind": "BLUEPRINT",
            "userSupplied": False,
        }

        first = discover_candidates(blueprint, hint, aliases=())
        reversed_blueprint = FakeBlueprint(
            dict(reversed(list(health_by_asset.items())))
        )
        second = discover_candidates(reversed_blueprint, hint, aliases=())

        self.assertEqual(first, second)
        self.assertEqual([item["asset"] for item in first[:2]], ["Target", "target"])
        self.assertTrue(first[0]["ambiguousTopScore"])
        self.assertLessEqual(len(first), 5)

    def test_object_path_suffix_and_alias_are_supported_matching_signals(self) -> None:
        blueprint = FakeBlueprint(
            {
                "OpaqueCapture": _health(
                    "OpaqueCapture", object_path="/Game/Creatures/Wing/Wing_BP.Wing_BP"
                )
            }
        )
        hint = {
            "text": "Wing_BP",
            "role": "PRIMARY_BLUEPRINT",
            "aliases": ["OpaqueCapture"],
            "expectedKind": "BLUEPRINT",
            "userSupplied": False,
        }

        result = discover_candidates(blueprint, hint, aliases=("ExtraAlias",))

        self.assertEqual(result[0]["asset"], "OpaqueCapture")
        self.assertIn("EXACT_OBJECT_PATH_SUFFIX", result[0]["signals"])
        self.assertIn("EXACT_ALIAS", result[0]["signals"])


class SolverPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.store = SolverStore(Path(self._temporary.name) / ".blueprint-solvers")
        self.tasks = FakeTasks()

    def service(self, blueprint: FakeBlueprint) -> SolverService:
        return SolverService(blueprint, self.tasks, self.store)

    def test_create_semantic_digests_ignore_solver_handle_and_timestamps(self) -> None:
        raw_request = "Inspect current behavior"
        proposal = {
            "schema": "blueprint-to-code.requirement-proposal/v1",
            "rawRequest": raw_request,
            "language": "en",
            "subproblems": [
                {
                    "sourceStart": 0,
                    "sourceEnd": len(raw_request),
                    "sourceText": raw_request,
                    "intent": "ANSWER_CURRENT_BEHAVIOR",
                    "outputKind": "CURRENT_BEHAVIOR",
                    "completeness": "BEST_EFFORT",
                    "targetHints": [],
                    "constraints": {},
                    "acceptanceCriteria": [],
                }
            ],
        }
        first_root = Path(self._temporary.name) / "first" / ".blueprint-solvers"
        second_root = Path(self._temporary.name) / "second" / ".blueprint-solvers"
        first = SolverService(
            FakeBlueprint({}),
            self.tasks,
            SolverStore(first_root),
            clock=lambda: "2026-08-13T00:00:00Z",
            opaque_id_factory=lambda: "1" * 32,
        )
        second = SolverService(
            FakeBlueprint({}),
            self.tasks,
            SolverStore(second_root),
            clock=lambda: "2026-08-14T00:00:00Z",
            opaque_id_factory=lambda: "2" * 32,
        )

        first_result = first.create(
            raw_request=raw_request, language="en", proposal=proposal
        )
        second_result = second.create(
            raw_request=raw_request, language="en", proposal=proposal
        )

        first_documents = first.store.load_solver(first_result["solverId"])
        second_documents = second.store.load_solver(second_result["solverId"])
        for key in (
            "requirement",
            "researchPlan",
            "evidenceMatrix",
            "acquisitionPlan",
            "state",
            "bindings",
        ):
            with self.subTest(document=key):
                self.assertEqual(
                    first_documents[key]["semanticDigest"],
                    semantic_digest(first_documents[key]),
                )
                self.assertEqual(
                    second_documents[key]["semanticDigest"],
                    semantic_digest(second_documents[key]),
                )
                self.assertEqual(
                    first_documents[key]["semanticDigest"],
                    second_documents[key]["semanticDigest"],
                )

    def test_resume_is_read_only_and_preserves_compact_budget(self) -> None:
        self.store.create_solver(
            SOLVER_ID,
            _documents(target_hints=[], requirements=[_requirement("ENTITY_DATASET")]),
        )
        solver_dir = self.store.root / ("b" * 32)
        before = {path.name: path.read_bytes() for path in solver_dir.iterdir()}

        result = self.service(FakeBlueprint({})).resume(SOLVER_ID)

        after = {path.name: path.read_bytes() for path in solver_dir.iterdir()}
        self.assertEqual(after, before)
        self.assertLessEqual(result["estimatedTokens"], 1800)

    def test_acquisition_plan_fails_closed_instead_of_truncating_actions(self) -> None:
        requirements = []
        for index in range(65):
            requirement = _requirement("ENTITY_DATASET", sources=["DATASET_IMPORT"])
            requirement["requirementId"] = f"requirement://{index:024x}"
            requirements.append(requirement)

        with self.assertRaises(McpExecutionError) as raised:
            build_acquisition_plan(SOLVER_ID, requirements)
        self.assertEqual(raised.exception.code, "SOLVER_LIMIT_EXCEEDED")

    def test_fresh_authoritative_candidate_resolves_current_v4_requirements(
        self,
    ) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        documents = _documents(
            target_hints=[
                {
                    "text": "TargetAsset",
                    "role": "PRIMARY_BLUEPRINT",
                    "aliases": [],
                    "expectedKind": "BLUEPRINT",
                    "userSupplied": False,
                }
            ],
            requirements=[
                _requirement("ASSET_IDENTITY"),
                _requirement("GRAPH_STRUCTURE"),
                _requirement("DEFAULTS"),
            ],
        )
        self.store.create_solver(SOLVER_ID, documents)

        result = self.service(blueprint).preflight(SOLVER_ID)

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(result["status"], "READY_FOR_TASKS")
        self.assertEqual(
            {item["status"] for item in stored["evidenceMatrix"]["requirements"]},
            {"READY"},
        )
        self.assertEqual(stored["acquisitionPlan"]["actions"], [])
        self.assertEqual(blueprint.health_calls, ["TargetAsset"])
        self.assertEqual(blueprint.authority_calls, ["TargetAsset"])
        self.assertEqual(stored["bindings"]["assets"][PROBLEM_ID]["freshness"], "FRESH")

    def test_stale_or_migration_candidate_requires_binary_rebuild(self) -> None:
        for label, evidence in (
            ("stale", _health("TargetAsset", status="STALE", freshness="STALE")),
            (
                "migration",
                _health(
                    "TargetAsset",
                    status="MIGRATION_REQUIRED",
                    freshness="SOURCE_UNAVAILABLE",
                    authority=False,
                    migration=True,
                ),
            ),
        ):
            with self.subTest(label=label):
                store = SolverStore(
                    Path(self._temporary.name) / f"{label}" / ".blueprint-solvers"
                )
                store.create_solver(
                    SOLVER_ID,
                    _documents(
                        target_hints=[
                            {
                                "text": "TargetAsset",
                                "role": "PRIMARY_BLUEPRINT",
                                "aliases": [],
                                "expectedKind": "BLUEPRINT",
                                "userSupplied": False,
                            }
                        ],
                        requirements=[_requirement("GRAPH_STRUCTURE")],
                    ),
                )
                blueprint = FakeBlueprint({"TargetAsset": evidence})

                result = SolverService(blueprint, self.tasks, store).preflight(
                    SOLVER_ID
                )

                action = store.load_solver(SOLVER_ID)["acquisitionPlan"]["actions"][0]
                self.assertEqual(result["status"], "ACQUISITION_REQUIRED")
                self.assertEqual(action["actionKind"], "REBUILD_BINARY_V4")
                self.assertEqual(action["sourceKind"], "BINARY_V4_REBUILD")
                self.assertEqual(blueprint.authority_calls, [])

    def test_ambiguous_candidates_require_explicit_selection(self) -> None:
        blueprint = FakeBlueprint(
            {"Target": _health("Target"), "target": _health("target")}
        )
        self.store.create_solver(
            SOLVER_ID,
            _documents(
                target_hints=[
                    {
                        "text": "TARGET",
                        "role": "PRIMARY_BLUEPRINT",
                        "aliases": [],
                        "expectedKind": "BLUEPRINT",
                        "userSupplied": False,
                    }
                ],
                requirements=[_requirement("ASSET_IDENTITY")],
            ),
        )

        result = self.service(blueprint).preflight(SOLVER_ID)

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(result["status"], "ACQUISITION_REQUIRED")
        self.assertEqual(
            stored["evidenceMatrix"]["requirements"][0]["status"], "AMBIGUOUS"
        )
        self.assertEqual(
            stored["acquisitionPlan"]["actions"][0]["actionKind"],
            "SELECT_ASSET_CANDIDATE",
        )
        self.assertEqual(blueprint.health_calls, [])

    def test_missing_mod_and_dataset_generate_user_acquisition_without_fake_candidates(
        self,
    ) -> None:
        self.store.create_solver(
            SOLVER_ID,
            _documents(
                target_hints=[
                    {
                        "text": "AbsentMod",
                        "role": "MOD_ASSET",
                        "aliases": [],
                        "expectedKind": "BLUEPRINT",
                        "userSupplied": True,
                    }
                ],
                requirements=[
                    _requirement(
                        "THIRD_PARTY_MOD_ASSET", sources=["USER_SUPPLIED_MOD_ASSET"]
                    ),
                    _requirement("ENTITY_DATASET", sources=["DATASET_IMPORT"]),
                ],
            ),
        )

        result = self.service(FakeBlueprint({})).preflight(SOLVER_ID)

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(result["status"], "ACQUISITION_REQUIRED")
        self.assertEqual(
            {item["actionKind"] for item in stored["acquisitionPlan"]["actions"]},
            {"PROVIDE_MOD_ASSET", "IMPORT_DATASET"},
        )
        self.assertTrue(
            all(
                item["candidateAssets"] == []
                for item in stored["evidenceMatrix"]["requirements"]
            )
        )

    def test_update_is_allowlisted_and_cannot_forge_ready(self) -> None:
        self.store.create_solver(
            SOLVER_ID,
            _documents(
                target_hints=[],
                requirements=[
                    _requirement("ENTITY_DATASET", sources=["DATASET_IMPORT"])
                ],
            ),
        )
        service = self.service(FakeBlueprint({}))

        service.update(
            SOLVER_ID,
            update={
                "operation": "provideDatasetDescriptor",
                "payload": {
                    "problemId": PROBLEM_ID,
                    "descriptor": {"name": "entities", "version": "1"},
                },
            },
        )
        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(
            stored["bindings"]["datasetDescriptors"][PROBLEM_ID]["name"], "entities"
        )

        for update in (
            {"operation": "setEvidenceStatus", "payload": {"status": "READY"}},
            {
                "operation": "provideDatasetDescriptor",
                "payload": {"problemId": PROBLEM_ID, "descriptor": {"status": "READY"}},
            },
        ):
            with (
                self.subTest(update=update),
                self.assertRaises(McpExecutionError) as raised,
            ):
                service.update(SOLVER_ID, update=update)
            self.assertEqual(raised.exception.code, "SOLVER_UPDATE_INVALID")

    def test_descriptor_update_rejects_nested_path_data_and_oversized_shapes(
        self,
    ) -> None:
        self.store.create_solver(
            SOLVER_ID,
            _documents(
                target_hints=[],
                requirements=[
                    _requirement("ENTITY_DATASET", sources=["DATASET_IMPORT"])
                ],
            ),
        )
        service = self.service(FakeBlueprint({}))

        for descriptor in (
            {"source": "C:" + chr(92) + "private" + chr(92) + "dataset.json"},
            {f"field{index}": "value" for index in range(33)},
        ):
            with (
                self.subTest(descriptor=descriptor),
                self.assertRaises(McpExecutionError) as raised,
            ):
                service.update(
                    SOLVER_ID,
                    update={
                        "operation": "provideDatasetDescriptor",
                        "payload": {"problemId": PROBLEM_ID, "descriptor": descriptor},
                    },
                )
            self.assertEqual(raised.exception.code, "SOLVER_UPDATE_INVALID")

    def test_dataset_descriptor_does_not_forge_evidence_ready(self) -> None:
        self.store.create_solver(
            SOLVER_ID,
            _documents(
                target_hints=[],
                requirements=[
                    _requirement("ENTITY_DATASET", sources=["DATASET_IMPORT"])
                ],
            ),
        )
        service = self.service(FakeBlueprint({}))
        service.update(
            SOLVER_ID,
            update={
                "operation": "provideDatasetDescriptor",
                "payload": {
                    "problemId": PROBLEM_ID,
                    "descriptor": {"name": "entities", "version": "1"},
                },
            },
        )

        result = service.preflight(SOLVER_ID)

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(result["status"], "ACQUISITION_REQUIRED")
        self.assertEqual(
            stored["evidenceMatrix"]["requirements"][0]["status"],
            "ACQUISITION_REQUIRED",
        )
        self.assertEqual(
            stored["acquisitionPlan"]["actions"][0]["actionKind"], "IMPORT_DATASET"
        )

    def test_user_formula_is_ready_from_validated_requirement_not_asset_discovery(
        self,
    ) -> None:
        documents = _documents(
            target_hints=[],
            requirements=[_requirement("USER_FORMULA", sources=[])],
        )
        documents["requirement"]["subproblems"][0]["constraints"] = {
            "topK": 10,
            "userFormula": "abs(x - target)",
            "formulaVariables": {"x": "candidateValue", "target": "requestedValue"},
        }
        self.store.create_solver(SOLVER_ID, documents)

        result = self.service(FakeBlueprint({})).preflight(SOLVER_ID)

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(result["status"], "READY_FOR_TASKS")
        self.assertEqual(stored["evidenceMatrix"]["requirements"][0]["status"], "READY")
        self.assertEqual(
            stored["evidenceMatrix"]["requirements"][0]["selectedSource"],
            "REQUIREMENT_PROPOSAL",
        )
        self.assertEqual(stored["acquisitionPlan"]["actions"], [])

    def test_all_persisted_solver_documents_validate_their_json_schemas(self) -> None:
        raw_request = "Inspect current behavior"
        proposal = {
            "schema": "blueprint-to-code.requirement-proposal/v1",
            "rawRequest": raw_request,
            "language": "en",
            "subproblems": [
                {
                    "sourceStart": 0,
                    "sourceEnd": len(raw_request),
                    "sourceText": raw_request,
                    "intent": "ANSWER_CURRENT_BEHAVIOR",
                    "outputKind": "CURRENT_BEHAVIOR",
                    "completeness": "BEST_EFFORT",
                    "targetHints": [],
                    "constraints": {},
                    "acceptanceCriteria": [],
                }
            ],
        }
        service = SolverService(
            FakeBlueprint({}),
            self.tasks,
            self.store,
            clock=lambda: "2026-08-13T00:00:00Z",
            opaque_id_factory=lambda: "a" * 32,
        )

        result = service.create(
            raw_request=raw_request, language="en", proposal=proposal
        )
        documents = self.store.load_solver(result["solverId"])
        schemas = {
            "requirement": "blueprint_requirement_ir.v1.schema.json",
            "researchPlan": "blueprint_research_plan.v1.schema.json",
            "evidenceMatrix": "blueprint_evidence_requirement_matrix.v1.schema.json",
            "acquisitionPlan": "blueprint_evidence_acquisition_plan.v1.schema.json",
            "state": "blueprint_solver_state.v1.schema.json",
            "bindings": "blueprint_solver_bindings.v1.schema.json",
        }
        for key, filename in schemas.items():
            with self.subTest(document=key):
                schema = json.loads(
                    (ROOT / "schemas" / filename).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(documents[key])

        public_schema = json.loads(
            (ROOT / "schemas" / "blueprint_solver_state.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator(public_schema).validate(result)

    def test_partial_preflight_preserves_unselected_problem_status(self) -> None:
        second_problem_id = "problem://" + "2" * 24
        documents = _documents(
            target_hints=[
                {
                    "text": "TargetAsset",
                    "role": "PRIMARY_BLUEPRINT",
                    "aliases": [],
                    "expectedKind": "BLUEPRINT",
                    "userSupplied": False,
                }
            ],
            requirements=[_requirement("ASSET_IDENTITY")],
        )
        second_problem = copy.deepcopy(documents["requirement"]["subproblems"][0])
        second_problem["problemId"] = second_problem_id
        documents["requirement"]["subproblems"].append(second_problem)
        second_requirement = _requirement("ASSET_IDENTITY")
        second_requirement["requirementId"] = "requirement://" + "2" * 24
        second_requirement["problemId"] = second_problem_id
        documents["evidenceMatrix"]["requirements"].append(second_requirement)
        documents["state"]["problemStatuses"][second_problem_id] = "PENDING"
        self.store.create_solver(SOLVER_ID, documents)

        result = self.service(
            FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        ).preflight(
            SOLVER_ID,
            problem_ids=(PROBLEM_ID,),
        )

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(stored["state"]["problemStatuses"][PROBLEM_ID], "READY")
        self.assertEqual(
            stored["state"]["problemStatuses"][second_problem_id], "PENDING"
        )
        self.assertEqual(result["status"], "ACQUISITION_REQUIRED")

    def test_materialization_reuses_task_service_and_persists_problem_binding(
        self,
    ) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        self.store.create_solver(
            SOLVER_ID,
            _documents(
                target_hints=[
                    {
                        "text": "TargetAsset",
                        "role": "PRIMARY_BLUEPRINT",
                        "aliases": [],
                        "expectedKind": "BLUEPRINT",
                        "userSupplied": False,
                    }
                ],
                requirements=[
                    _requirement("ASSET_IDENTITY"),
                    _requirement("GRAPH_STRUCTURE"),
                ],
                intent="DESIGN_BLUEPRINT_CHANGE",
            ),
        )
        service = self.service(blueprint)
        service.preflight(SOLVER_ID)

        result = service.materialize_task(SOLVER_ID, problem_id=PROBLEM_ID)

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(result["status"], "TASKS_MATERIALIZED")
        self.assertEqual(
            stored["bindings"]["tasks"][PROBLEM_ID],
            self.tasks.calls[0]["task_id"],
        )
        self.assertEqual(self.tasks.calls[0]["asset"], "TargetAsset")
        self.assertEqual(self.tasks.calls[0]["mode"], "BLUEPRINT_DESIGN")
        self.assertGreaterEqual(blueprint.authority_calls.count("TargetAsset"), 2)

    def test_materialization_retry_recovers_reserved_task_without_creating_a_duplicate(
        self,
    ) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        self.store.create_solver(
            SOLVER_ID,
            _documents(
                target_hints=[
                    {
                        "text": "TargetAsset",
                        "role": "PRIMARY_BLUEPRINT",
                        "aliases": [],
                        "expectedKind": "BLUEPRINT",
                        "userSupplied": False,
                    }
                ],
                requirements=[
                    _requirement("ASSET_IDENTITY"),
                    _requirement("GRAPH_STRUCTURE"),
                ],
                intent="DESIGN_BLUEPRINT_CHANGE",
            ),
        )
        service = self.service(blueprint)
        service.preflight(SOLVER_ID)
        real_save = self.store.save_solver
        failed_final_save = False

        def fail_final_binding_save(
            solver_id: str, documents: dict[str, dict[str, object]], **kwargs: object
        ) -> None:
            nonlocal failed_final_save
            task_bindings = documents["bindings"].get("tasks", {})
            if not failed_final_save and PROBLEM_ID in task_bindings:
                failed_final_save = True
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR", "fixture final binding failure"
                )
            real_save(solver_id, documents, **kwargs)

        with patch.object(
            self.store, "save_solver", side_effect=fail_final_binding_save
        ):
            with self.assertRaises(McpExecutionError):
                service.materialize_task(SOLVER_ID, problem_id=PROBLEM_ID)

        interrupted = self.store.load_solver(SOLVER_ID)
        reserved_task_id = interrupted["bindings"]["pendingTasks"][PROBLEM_ID]
        self.assertEqual(self.tasks.calls[0]["task_id"], reserved_task_id)
        self.assertNotIn(PROBLEM_ID, interrupted["bindings"]["tasks"])

        result = service.materialize_task(SOLVER_ID, problem_id=PROBLEM_ID)

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(result["status"], "TASKS_MATERIALIZED")
        self.assertEqual(len(self.tasks.calls), 1)
        self.assertEqual(stored["bindings"]["tasks"][PROBLEM_ID], reserved_task_id)
        self.assertNotIn(PROBLEM_ID, stored["bindings"]["pendingTasks"])

    def test_materialization_does_not_create_task_until_reservation_is_persisted(
        self,
    ) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        self.store.create_solver(
            SOLVER_ID,
            _documents(
                target_hints=[
                    {
                        "text": "TargetAsset",
                        "role": "PRIMARY_BLUEPRINT",
                        "aliases": [],
                        "expectedKind": "BLUEPRINT",
                        "userSupplied": False,
                    }
                ],
                requirements=[_requirement("ASSET_IDENTITY")],
                intent="DESIGN_BLUEPRINT_CHANGE",
            ),
        )
        service = self.service(blueprint)
        service.preflight(SOLVER_ID)

        with patch.object(
            self.store,
            "save_solver",
            side_effect=McpExecutionError(
                "INTERNAL_CONTRACT_ERROR", "fixture reservation failure"
            ),
        ):
            with self.assertRaises(McpExecutionError):
                service.materialize_task(SOLVER_ID, problem_id=PROBLEM_ID)

        stored = self.store.load_solver(SOLVER_ID)
        self.assertEqual(self.tasks.calls, [])
        self.assertEqual(stored["bindings"]["pendingTasks"], {})
        self.assertEqual(stored["bindings"]["tasks"], {})

    def test_non_primary_hint_never_becomes_the_task_primary_asset(self) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        service = self.service(blueprint)
        created = service.create(
            raw_request="Change the supporting Blueprint",
            language="en",
            proposal=_solver_proposal(
                "Change the supporting Blueprint",
                target_role="SUPPORTING_BLUEPRINT",
            ),
        )

        result = service.preflight(created["solverId"])

        self.assertEqual(result["status"], "ACQUISITION_REQUIRED")
        self.assertEqual(blueprint.health_calls, [])
        self.assertEqual(blueprint.authority_calls, [])
        self.assertEqual(self.tasks.calls, [])

    def test_answer_problem_with_primary_asset_materializes_knowledge_task(
        self,
    ) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        service = self.service(blueprint)
        raw_request = "Explain the current Blueprint behavior"
        created = service.create(
            raw_request=raw_request,
            language="en",
            proposal=_solver_proposal(
                raw_request,
                intent="ANSWER_CURRENT_BEHAVIOR",
            ),
        )
        problem_id = created["problemSummaries"][0]["problemId"]
        service.preflight(created["solverId"])

        result = service.materialize_task(created["solverId"], problem_id=problem_id)

        self.assertEqual(result["status"], "TASKS_MATERIALIZED")
        self.assertEqual(self.tasks.calls[0]["mode"], "KNOWLEDGE_QUERY")
        self.assertNotIn(raw_request, self.tasks.calls[0]["goal"])
        self.assertIn(problem_id, self.tasks.calls[0]["goal"])
        self.assertIn("CURRENT_BEHAVIOR", self.tasks.calls[0]["goal"])

    def test_materialization_does_not_copy_oversized_private_source_into_task_goal(
        self,
    ) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        service = self.service(blueprint)
        raw_request = "x" * 1001
        created = service.create(
            raw_request=raw_request,
            language="en",
            proposal=_solver_proposal(raw_request),
        )
        problem_id = created["problemSummaries"][0]["problemId"]
        service.preflight(created["solverId"])

        result = service.materialize_task(created["solverId"], problem_id=problem_id)

        self.assertEqual(result["status"], "TASKS_MATERIALIZED")
        self.assertNotIn(raw_request, self.tasks.calls[0]["goal"])
        self.assertLessEqual(len(self.tasks.calls[0]["goal"]), 1000)

    def test_private_source_does_not_exempt_other_solver_documents_from_path_guard(
        self,
    ) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        service = self.service(blueprint)
        raw_request = "生物体重/死神体重=K如果>1"
        created = service.create(
            raw_request=raw_request,
            language="en",
            proposal=_solver_proposal(raw_request),
        )
        problem_id = created["problemSummaries"][0]["problemId"]
        service.preflight(created["solverId"])
        real_load = self.store.load_solver

        def load_with_tainted_binding(
            solver_id: str,
        ) -> dict[str, dict[str, object]]:
            documents = real_load(solver_id)
            documents["bindings"]["aliases"] = {problem_id: [raw_request]}
            return documents

        with patch.object(
            self.store, "load_solver", side_effect=load_with_tainted_binding
        ):
            with self.assertRaises(McpExecutionError) as raised:
                service.materialize_task(created["solverId"], problem_id=problem_id)

        self.assertEqual(raised.exception.code, "INTERNAL_CONTRACT_ERROR")
        self.assertEqual(self.tasks.calls, [])

    def test_ranking_task_goal_keeps_safe_formula_without_copying_private_source(
        self,
    ) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        service = self.service(blueprint)
        raw_request = "生物体重/死神体重=K如果>1"
        documents = _documents(
            target_hints=[
                {
                    "text": "TargetAsset",
                    "role": "PRIMARY_BLUEPRINT",
                    "aliases": [],
                    "expectedKind": "BLUEPRINT",
                    "userSupplied": False,
                }
            ],
            requirements=[_requirement("ASSET_IDENTITY")],
        )
        documents["requirement"]["rawRequest"] = raw_request
        problem = documents["requirement"]["subproblems"][0]
        problem["sourceStart"] = 0
        problem["sourceEnd"] = len(raw_request)
        problem["sourceText"] = raw_request
        problem["outputKind"] = "RANKING"
        problem["constraints"] = {
            "topK": 10,
            "userFormula": "penaltyCoefficient = min(K, 1 / K)",
            "formulaVariables": {
                "candidateWeight": "candidateWeight",
                "reaperWeight": "reaperWeight",
            },
        }
        self.store.create_solver(SOLVER_ID, documents)
        service.preflight(SOLVER_ID)

        service.materialize_task(SOLVER_ID, problem_id=PROBLEM_ID)

        goal = self.tasks.calls[0]["goal"]
        self.assertNotIn(raw_request, goal)
        self.assertIn('"topK":10', goal)
        self.assertIn("penaltyCoefficient = min(K, 1 / K)", goal)

    def test_materialization_rejects_criteria_beyond_task_limit(self) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        service = self.service(blueprint)
        raw_request = "Design a bounded Blueprint change"
        created = service.create(
            raw_request=raw_request,
            language="en",
            proposal=_solver_proposal(
                raw_request,
                acceptance_criteria=[f"Criterion {index}" for index in range(12)],
            ),
        )
        problem_id = created["problemSummaries"][0]["problemId"]
        service.preflight(created["solverId"])

        with self.assertRaises(McpExecutionError) as raised:
            service.materialize_task(created["solverId"], problem_id=problem_id)

        self.assertEqual(raised.exception.code, "SOLVER_LIMIT_EXCEEDED")
        self.assertEqual(self.tasks.calls, [])

    def test_materialization_preserves_each_desired_behavior_item(self) -> None:
        blueprint = FakeBlueprint({"TargetAsset": _health("TargetAsset")})
        service = self.service(blueprint)
        raw_request = "Design a complete Blueprint change"
        desired_behavior = ["x" * 512, "y" * 512]
        created = service.create(
            raw_request=raw_request,
            language="en",
            proposal=_solver_proposal(
                raw_request,
                desired_behavior=desired_behavior,
            ),
        )
        problem_id = created["problemSummaries"][0]["problemId"]
        service.preflight(created["solverId"])

        service.materialize_task(created["solverId"], problem_id=problem_id)

        self.assertEqual(self.tasks.calls[0]["allowed_changes"], desired_behavior)


if __name__ == "__main__":
    unittest.main()
