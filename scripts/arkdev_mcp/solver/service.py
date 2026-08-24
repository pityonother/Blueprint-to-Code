"""Deterministic Requirement/Evidence orchestration over existing services."""

from __future__ import annotations

import copy
import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from ..contracts import McpExecutionError, assert_path_free
from .acquisition_planner import build_acquisition_plan
from .asset_discovery import discover_candidates, merge_problem_candidates
from .canonical import semantic_digest
from .contracts import MAX_RAW_REQUEST_CHARS, MAX_SUBPROBLEMS
from .renderer import render_solver_state
from .store import SolverStore


_ASSET_REQUIREMENT_KINDS = frozenset(
    {
        "ASSET_IDENTITY",
        "GRAPH_STRUCTURE",
        "NODE_IDENTITY",
        "PIN_SIGNATURES",
        "PIN_IDENTITY",
        "DEFAULTS",
        "REFERENCE_CLOSURE",
        "RUNTIME_STATE",
        "THIRD_PARTY_MOD_ASSET",
    }
)
_DESCRIPTOR_OPERATIONS = {
    "provideDatasetDescriptor": "datasetDescriptors",
    "provideLocalizationDescriptor": "localizationDescriptors",
    "provideModAssetDescriptor": "modAssetDescriptors",
}
_UPDATE_OPERATIONS = frozenset(
    {
        "selectAssetCandidate",
        "addTargetAlias",
        "resolveBlockingQuestion",
        *_DESCRIPTOR_OPERATIONS,
    }
)
_FORBIDDEN_UPDATE_KEYS = frozenset(
    {
        "status",
        "selectedsource",
        "confirmedfacts",
        "operatordag",
        "operators",
        "evidence",
        "ready",
    }
)
_TASK_ID = re.compile(r"^task://[0-9a-f]{32}$")
_PRIVATE_SOURCE_SENTINEL = "USER_PROVIDED_REQUIREMENT_TEXT"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _refresh_digest(payload: dict[str, object]) -> None:
    payload.pop("semanticDigest", None)
    payload["semanticDigest"] = semantic_digest(payload)


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _assert_private_solver_documents_safe(
    documents: Mapping[str, Mapping[str, object]],
) -> None:
    """Mask only proven caller-source copies, then scan every other field strictly."""

    guarded = copy.deepcopy(dict(documents))
    requirement = _mapping(guarded.get("requirement"))
    raw_request = requirement.get("rawRequest")
    if (
        not isinstance(raw_request, str)
        or not 1 <= len(raw_request) <= MAX_RAW_REQUEST_CHARS
    ):
        raise McpExecutionError(
            "INTERNAL_CONTRACT_ERROR",
            "Solver private source metadata is invalid.",
        )
    subproblems = requirement.get("subproblems")
    if (
        not isinstance(subproblems, list)
        or not 1 <= len(subproblems) <= MAX_SUBPROBLEMS
    ):
        raise McpExecutionError(
            "INTERNAL_CONTRACT_ERROR",
            "Solver private source metadata is invalid.",
        )
    guarded_subproblems = []
    for subproblem in subproblems:
        if not isinstance(subproblem, Mapping):
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Solver private source metadata is invalid.",
            )
        guarded_subproblem = dict(subproblem)
        start = guarded_subproblem.get("sourceStart")
        end = guarded_subproblem.get("sourceEnd")
        source_text = guarded_subproblem.get("sourceText")
        source_is_bound = (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and 0 <= start < end <= len(raw_request)
            and source_text == raw_request[start:end]
        )
        if source_is_bound:
            guarded_subproblem["sourceText"] = _PRIVATE_SOURCE_SENTINEL
        elif not isinstance(source_text, str):
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Solver private source metadata is invalid.",
            )
        # Older path-free Solver fixtures may not carry spans. They receive no
        # exemption: their sourceText remains visible to the strict path guard.
        guarded_subproblems.append(guarded_subproblem)
    requirement["rawRequest"] = _PRIVATE_SOURCE_SENTINEL
    requirement["subproblems"] = guarded_subproblems
    guarded["requirement"] = requirement
    assert_path_free(guarded)


def _sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _problem_id(problem: Mapping[str, object]) -> str:
    return str(problem.get("problemId") or "")


def _task_goal(
    solver_id: str,
    problem: Mapping[str, object],
) -> str:
    constraints = _mapping(problem.get("constraints"))
    goal_spec: dict[str, object] = {
        "intent": str(problem.get("intent") or ""),
        "outputKind": str(problem.get("outputKind") or ""),
        "problemId": _problem_id(problem),
        "solverId": solver_id,
    }
    for field in (
        "localizedNamesOnly",
        "outputLanguage",
        "topK",
        "userFormula",
        "formulaVariables",
    ):
        if field in constraints:
            goal_spec[field] = copy.deepcopy(constraints[field])
    goal = "Research validated Solver problem: " + _canonical_json(goal_spec)
    assert_path_free(goal)
    if len(goal) > 1000:
        raise McpExecutionError(
            "SOLVER_LIMIT_EXCEEDED",
            "Solver Task goal exceeds the existing Task Context limit.",
            details={"field": "goal", "length": len(goal), "maximum": 1000},
        )
    return goal


def _problem_map(requirement_ir: Mapping[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for raw in _sequence(requirement_ir.get("subproblems"))[:8]:
        if isinstance(raw, Mapping):
            item = dict(raw)
            identifier = _problem_id(item)
            if identifier:
                result[identifier] = item
    return result


def _health_payload(payload: Mapping[str, object]) -> dict[str, object]:
    return _mapping(payload.get("health"))


def _health_gap(health: Mapping[str, object]) -> str:
    evidence = _mapping(health.get("evidence"))
    status = str(health.get("status") or "").upper()
    freshness = str(evidence.get("freshnessStatus") or "").upper()
    if status == "STALE" or freshness == "STALE":
        return "EVIDENCE_STALE"
    if status == "MIGRATION_REQUIRED" or bool(evidence.get("migrationRequired")):
        return "MIGRATION_REQUIRED"
    if not bool(evidence.get("releaseAuthority", False)):
        return "EVIDENCE_NOT_AUTHORITATIVE"
    if freshness == "SOURCE_UNAVAILABLE":
        return "EVIDENCE_SOURCE_UNAVAILABLE"
    return str(health.get("reasonCode") or "EVIDENCE_NOT_FOUND")


def _health_ready(health: Mapping[str, object]) -> bool:
    evidence = _mapping(health.get("evidence"))
    interpretation = _mapping(health.get("interpretation"))
    return (
        str(health.get("status") or "").upper() == "READY"
        and str(evidence.get("freshnessStatus") or "").upper() == "FRESH"
        and bool(evidence.get("releaseAuthority", False))
        and not bool(evidence.get("migrationRequired", False))
        and str(interpretation.get("status") or "").upper() == "CURRENT"
    )


def _authority_matches_health(
    authority: Mapping[str, object],
    health: Mapping[str, object],
) -> bool:
    asset = _mapping(health.get("asset"))
    evidence = _mapping(health.get("evidence"))
    return (
        str(authority.get("freshness") or "") == "FRESH"
        and str(authority.get("assetId") or "") == str(asset.get("assetId") or "")
        and str(authority.get("objectPath") or "") == str(asset.get("objectPath") or "")
        and str(authority.get("evidenceRevisionId") or "")
        == str(evidence.get("revisionId") or "")
        and str(authority.get("evidenceManifestSha256") or "")
        == str(evidence.get("manifestSha256") or "")
    )


def _new_opaque_id(factory: Callable[[], str], *, label: str) -> str:
    opaque = str(factory())
    if len(opaque) != 32 or any(ch not in "0123456789abcdef" for ch in opaque):
        raise McpExecutionError(
            "INTERNAL_CONTRACT_ERROR",
            f"Opaque {label} ID generator returned an invalid value.",
        )
    return opaque


def _forbidden_update_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).replace("_", "").casefold() in _FORBIDDEN_UPDATE_KEYS:
                return True
            if _forbidden_update_key(item):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_forbidden_update_key(item) for item in value)
    return False


class SolverService:
    def __init__(
        self,
        blueprint: object,
        tasks: object,
        store: SolverStore,
        *,
        clock: Callable[[], str] = _now,
        opaque_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.blueprint = blueprint
        self.tasks = tasks
        self.store = store
        self.clock = clock
        self.opaque_id_factory = opaque_id_factory or (lambda: uuid.uuid4().hex)

    def create(
        self,
        *,
        raw_request: str,
        language: str,
        proposal: Mapping[str, object],
    ) -> dict[str, object]:
        # Imported lazily so the orchestrator remains independently testable while the
        # deterministic compiler modules are developed in parallel.
        from .evidence_matrix import derive_evidence_matrix
        from .proposal_validator import validate_requirement_proposal
        from .requirement_compiler import compile_requirement
        from .research_plan import build_research_plan

        normalized = validate_requirement_proposal(
            proposal,
            explicit_raw_request=str(raw_request),
        )
        if str(normalized.get("rawRequest") or "") != str(raw_request) or str(
            normalized.get("language") or ""
        ) != str(language):
            raise McpExecutionError(
                "REQUIREMENT_PROPOSAL_INVALID",
                "Proposal rawRequest and language must match the explicit tool inputs.",
            )
        opaque = _new_opaque_id(self.opaque_id_factory, label="Solver")
        solver_id = f"solver://{opaque}"
        requirement = dict(
            compile_requirement(
                normalized,
                solver_id=solver_id,
                explicit_raw_request=str(raw_request),
            )
        )
        research_plan = dict(build_research_plan(requirement))
        research_plan["solverId"] = solver_id
        evidence_matrix = dict(derive_evidence_matrix(requirement, research_plan))
        evidence_matrix["solverId"] = solver_id
        for document in (requirement, research_plan, evidence_matrix):
            _refresh_digest(document)
        problems = _problem_map(requirement)
        timestamp = self.clock()
        acquisition_plan = build_acquisition_plan(
            solver_id,
            [
                item
                for item in _sequence(evidence_matrix.get("requirements"))
                if isinstance(item, Mapping)
                and str(item.get("status") or "") != "UNRESOLVED"
            ],
        )
        state: dict[str, object] = {
            "schema": "blueprint-to-code.solver-state/v1",
            "solverId": solver_id,
            "status": "CREATED",
            "problemStatuses": {problem_id: "PENDING" for problem_id in problems},
            "materializedTaskIds": {},
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        bindings: dict[str, object] = {
            "schema": "blueprint-to-code.solver-bindings/v1",
            "solverId": solver_id,
            "assets": {},
            "tasks": {},
            "pendingTasks": {},
            "aliases": {},
            "datasetDescriptors": {},
            "localizationDescriptors": {},
            "modAssetDescriptors": {},
            "resolvedBlockingQuestions": {},
        }
        _refresh_digest(state)
        _refresh_digest(bindings)
        documents = {
            "requirement": requirement,
            "researchPlan": research_plan,
            "evidenceMatrix": evidence_matrix,
            "acquisitionPlan": acquisition_plan,
            "state": state,
            "bindings": bindings,
        }
        _assert_private_solver_documents_safe(documents)
        self.store.create_solver(solver_id, documents)
        return render_solver_state(documents)

    def resume(self, solver_id: str) -> dict[str, object]:
        """Return a compact projection without refreshing or writing metadata."""

        documents = self.store.load_solver(solver_id)
        _assert_private_solver_documents_safe(documents)
        return render_solver_state(documents)

    def preflight(
        self,
        solver_id: str,
        *,
        problem_ids: Sequence[str] = (),
    ) -> dict[str, object]:
        documents = copy.deepcopy(self.store.load_solver(solver_id))
        _assert_private_solver_documents_safe(documents)
        state = documents["state"]
        if str(state.get("status") or "") == "TASKS_MATERIALIZED":
            raise McpExecutionError(
                "SOLVER_PHASE_INVALID",
                "A materialized Solver run cannot return to preflight.",
            )
        problems = _problem_map(documents["requirement"])
        selected_ids = tuple(
            dict.fromkeys(str(item) for item in problem_ids if str(item))
        )
        if len(selected_ids) > 8 or any(item not in problems for item in selected_ids):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "problemIds must identify bounded subproblems in this Solver run.",
            )
        target_ids = selected_ids or tuple(problems)
        requirements = [
            dict(item)
            for item in _sequence(documents["evidenceMatrix"].get("requirements"))
            if isinstance(item, Mapping)
        ]
        bindings = documents["bindings"]
        asset_bindings = _mapping(bindings.get("assets"))
        aliases = _mapping(bindings.get("aliases"))

        for problem_id in target_ids:
            problem = problems[problem_id]
            problem_requirements = [
                item
                for item in requirements
                if str(item.get("problemId") or "") == problem_id
            ]
            asset_requirements = [
                item
                for item in problem_requirements
                if str(item.get("kind") or "") in _ASSET_REQUIREMENT_KINDS
            ]
            constraints = _mapping(problem.get("constraints"))
            if isinstance(constraints.get("userFormula"), str) and isinstance(
                constraints.get("formulaVariables"), Mapping
            ):
                for requirement in problem_requirements:
                    if str(requirement.get("kind") or "") == "USER_FORMULA":
                        requirement["status"] = "READY"
                        requirement["selectedSource"] = "REQUIREMENT_PROPOSAL"
                        requirement["gaps"] = []
            selected_binding = _mapping(asset_bindings.get(problem_id))
            candidates: list[dict[str, object]] = []
            if selected_binding.get("asset"):
                selected_asset = str(selected_binding["asset"])
                for requirement in asset_requirements:
                    for raw in _sequence(requirement.get("candidateAssets")):
                        if (
                            isinstance(raw, Mapping)
                            and str(raw.get("asset") or "") == selected_asset
                        ):
                            candidates.append(dict(raw))
                if not candidates:
                    candidates = [
                        {
                            "asset": selected_asset,
                            "score": 1000,
                            "signals": ["EXPLICIT_SELECTION"],
                            "ambiguousTopScore": False,
                        }
                    ]
            else:
                raw_hints = [
                    dict(item)
                    for item in _sequence(problem.get("targetHints"))[:8]
                    if isinstance(item, Mapping)
                ]
                search_hints = [
                    item
                    for item in raw_hints
                    if str(item.get("role") or "") == "PRIMARY_BLUEPRINT"
                ]
                discovered: list[dict[str, object]] = []
                problem_aliases = [
                    str(item) for item in _sequence(aliases.get(problem_id))
                ]
                for hint in search_hints:
                    hint_id = str(hint.get("targetHintId") or "")
                    extra_aliases = problem_aliases + [
                        str(item) for item in _sequence(aliases.get(hint_id))
                    ]
                    discovered.extend(
                        discover_candidates(self.blueprint, hint, aliases=extra_aliases)
                    )
                candidates = merge_problem_candidates(discovered, maximum=20)

            for requirement in asset_requirements:
                requirement["candidateAssets"] = copy.deepcopy(candidates)
            if not candidates:
                for requirement in problem_requirements:
                    if str(requirement.get("status") or "") == "READY":
                        continue
                    kind = str(requirement.get("kind") or "")
                    requirement["status"] = "ACQUISITION_REQUIRED"
                    requirement["selectedSource"] = ""
                    if kind == "THIRD_PARTY_MOD_ASSET":
                        requirement["gaps"] = ["MOD_ASSET_NOT_PROVIDED"]
                    elif kind in {"ENTITY_DATASET", "DATASET_SCHEMA"}:
                        requirement["gaps"] = ["DATASET_NOT_PROVIDED"]
                    elif kind == "LOCALIZATION":
                        requirement["gaps"] = ["LOCALIZATION_NOT_PROVIDED"]
                    elif kind in _ASSET_REQUIREMENT_KINDS:
                        requirement["gaps"] = ["ASSET_NOT_FOUND"]
                continue

            top_score = int(candidates[0].get("score") or 0)
            top = [
                item for item in candidates if int(item.get("score") or 0) == top_score
            ]
            if len(top) != 1:
                asset_bindings.pop(problem_id, None)
                for requirement in asset_requirements:
                    requirement["status"] = "AMBIGUOUS"
                    requirement["selectedSource"] = ""
                    requirement["gaps"] = ["TARGET_SELECTION_REQUIRED"]
                continue

            candidate = top[0]
            asset_name = str(candidate.get("asset") or "")
            try:
                health_response = self.blueprint.health(asset=asset_name)
                health = _health_payload(health_response)
            except McpExecutionError as exc:
                health = {"status": "INVALID", "reasonCode": exc.code}
            if not _health_ready(health):
                gap = _health_gap(health)
                asset_bindings[problem_id] = {"asset": asset_name}
                for requirement in asset_requirements:
                    requirement["status"] = "ACQUISITION_REQUIRED"
                    requirement["selectedSource"] = ""
                    requirement["gaps"] = [gap]
                continue
            try:
                authority = dict(self.blueprint.get_task_authority(asset=asset_name))
            except McpExecutionError as exc:
                for requirement in asset_requirements:
                    requirement["status"] = "ACQUISITION_REQUIRED"
                    requirement["selectedSource"] = ""
                    requirement["gaps"] = [exc.code]
                continue
            if not _authority_matches_health(authority, health):
                for requirement in asset_requirements:
                    requirement["status"] = "ACQUISITION_REQUIRED"
                    requirement["selectedSource"] = ""
                    requirement["gaps"] = ["EVIDENCE_REVISION_MISMATCH"]
                continue
            asset_bindings[problem_id] = {
                "asset": asset_name,
                "assetId": authority.get("assetId", ""),
                "objectPath": authority.get("objectPath", ""),
                "evidenceRevisionId": authority.get("evidenceRevisionId", ""),
                "evidenceManifestSha256": authority.get("evidenceManifestSha256", ""),
                "freshness": authority.get("freshness", ""),
                "graphTargets": copy.deepcopy(authority.get("graphTargets", [])),
            }
            for requirement in asset_requirements:
                if "CURRENT_V4_EVIDENCE" in [
                    str(item) for item in _sequence(requirement.get("preferredSources"))
                ]:
                    requirement["status"] = "READY"
                    requirement["selectedSource"] = "CURRENT_V4_EVIDENCE"
                    requirement["gaps"] = []

        bindings["assets"] = asset_bindings
        documents["evidenceMatrix"]["requirements"] = requirements
        documents["acquisitionPlan"] = build_acquisition_plan(solver_id, requirements)
        problem_statuses = _mapping(state.get("problemStatuses"))
        for problem_id in target_ids:
            statuses = {
                str(item.get("status") or "UNRESOLVED")
                for item in requirements
                if str(item.get("problemId") or "") == problem_id
                and bool(item.get("blocking", True))
            }
            problem_statuses[problem_id] = (
                "READY"
                if statuses <= {"READY"}
                else (
                    "AMBIGUOUS" if "AMBIGUOUS" in statuses else "ACQUISITION_REQUIRED"
                )
            )
        state["problemStatuses"] = problem_statuses
        blocking = [
            item
            for item in requirements
            if bool(item.get("blocking", True))
            and str(item.get("status") or "") != "READY"
        ]
        state["status"] = "ACQUISITION_REQUIRED" if blocking else "READY_FOR_TASKS"
        state["updatedAt"] = self.clock()
        for document in (documents["evidenceMatrix"], bindings, state):
            _refresh_digest(document)
        _assert_private_solver_documents_safe(documents)
        self.store.save_solver(solver_id, documents)
        return render_solver_state(documents)

    def update(
        self,
        solver_id: str,
        *,
        update: Mapping[str, object],
    ) -> dict[str, object]:
        if set(update) != {"operation", "payload"}:
            raise McpExecutionError(
                "SOLVER_UPDATE_INVALID",
                "Solver update must contain exactly operation and payload.",
            )
        operation = str(update.get("operation") or "")
        payload = _mapping(update.get("payload"))
        if (
            operation not in _UPDATE_OPERATIONS
            or not payload
            or _forbidden_update_key(payload)
        ):
            raise McpExecutionError(
                "SOLVER_UPDATE_INVALID",
                "Solver update is outside the controlled update allowlist.",
            )
        try:
            assert_path_free(payload)
            if len(_canonical_json(payload)) > 4096:
                raise ValueError("payload too large")
        except (McpExecutionError, TypeError, ValueError) as exc:
            raise McpExecutionError(
                "SOLVER_UPDATE_INVALID",
                "Solver update payload is invalid or outside its bounded contract.",
            ) from exc
        documents = copy.deepcopy(self.store.load_solver(solver_id))
        _assert_private_solver_documents_safe(documents)
        problems = _problem_map(documents["requirement"])
        bindings = documents["bindings"]
        problem_id = str(payload.get("problemId") or "")
        if problem_id not in problems:
            raise McpExecutionError(
                "SOLVER_UPDATE_INVALID",
                "Solver update problemId is not part of this Solver run.",
            )
        if operation == "selectAssetCandidate":
            asset = str(payload.get("asset") or "")
            candidates = [
                item
                for requirement in _sequence(
                    documents["evidenceMatrix"].get("requirements")
                )
                if isinstance(requirement, Mapping)
                and str(requirement.get("problemId") or "") == problem_id
                for item in _sequence(requirement.get("candidateAssets"))
                if isinstance(item, Mapping) and str(item.get("asset") or "") == asset
            ]
            if not asset or not candidates:
                raise McpExecutionError(
                    "TARGET_CANDIDATE_NOT_FOUND",
                    "Selected asset is not a candidate for this Solver problem.",
                )
            assets = _mapping(bindings.get("assets"))
            assets[problem_id] = {"asset": asset}
            bindings["assets"] = assets
        elif operation == "addTargetAlias":
            alias = " ".join(str(payload.get("alias") or "").split())
            target_hint_id = str(payload.get("targetHintId") or problem_id)
            if not alias or len(alias) > 256:
                raise McpExecutionError(
                    "SOLVER_UPDATE_INVALID", "Target alias is invalid."
                )
            aliases = _mapping(bindings.get("aliases"))
            values = [str(item) for item in _sequence(aliases.get(target_hint_id))]
            if alias not in values:
                values.append(alias)
            if len(values) > 8:
                raise McpExecutionError(
                    "SOLVER_LIMIT_EXCEEDED", "Target alias limit exceeded."
                )
            aliases[target_hint_id] = values
            bindings["aliases"] = aliases
        elif operation == "resolveBlockingQuestion":
            question_id = str(payload.get("questionId") or "")
            answer = " ".join(str(payload.get("answer") or "").split())
            if not question_id or not answer or len(answer) > 1000:
                raise McpExecutionError(
                    "SOLVER_UPDATE_INVALID", "Blocking-question resolution is invalid."
                )
            resolved = _mapping(bindings.get("resolvedBlockingQuestions"))
            resolved[question_id] = answer
            bindings["resolvedBlockingQuestions"] = resolved
        else:
            descriptor = _mapping(payload.get("descriptor"))
            if not descriptor or len(descriptor) > 32:
                raise McpExecutionError(
                    "SOLVER_UPDATE_INVALID", "Descriptor update is invalid."
                )
            field = _DESCRIPTOR_OPERATIONS[operation]
            descriptors = _mapping(bindings.get(field))
            descriptors[problem_id] = descriptor
            bindings[field] = descriptors
        documents["state"]["status"] = "PREFLIGHT"
        documents["state"]["updatedAt"] = self.clock()
        _refresh_digest(bindings)
        _refresh_digest(documents["state"])
        _assert_private_solver_documents_safe(documents)
        self.store.save_solver(solver_id, documents)
        return render_solver_state(documents)

    def materialize_task(
        self,
        solver_id: str,
        *,
        problem_id: str,
    ) -> dict[str, object]:
        documents = copy.deepcopy(self.store.load_solver(solver_id))
        _assert_private_solver_documents_safe(documents)
        if str(documents["state"].get("status") or "") != "READY_FOR_TASKS":
            raise McpExecutionError(
                "EVIDENCE_ACQUISITION_REQUIRED",
                "Solver Evidence requirements are not ready for Task materialization.",
            )
        problems = _problem_map(documents["requirement"])
        problem = problems.get(str(problem_id))
        if problem is None:
            raise McpExecutionError(
                "INVALID_ARGUMENT", "problemId is not part of this Solver run."
            )
        intent = str(problem.get("intent") or "")
        if intent == "DESIGN_BLUEPRINT_CHANGE":
            task_mode = "BLUEPRINT_DESIGN"
        elif intent == "ANSWER_CURRENT_BEHAVIOR":
            task_mode = "KNOWLEDGE_QUERY"
        else:
            raise McpExecutionError(
                "TASK_NOT_APPLICABLE",
                "This Solver problem does not require a Blueprint Task Context.",
            )
        bindings = documents["bindings"]
        tasks = _mapping(bindings.get("tasks"))
        if problem_id in tasks:
            return render_solver_state(documents)
        pending_tasks = _mapping(bindings.get("pendingTasks"))
        asset_binding = _mapping(_mapping(bindings.get("assets")).get(problem_id))
        asset = str(asset_binding.get("asset") or "")
        if not asset:
            if intent == "ANSWER_CURRENT_BEHAVIOR":
                raise McpExecutionError(
                    "TASK_NOT_APPLICABLE",
                    "This Solver problem has no primary Blueprint for Task research.",
                )
            raise McpExecutionError(
                "TARGET_SELECTION_REQUIRED",
                "A primary Blueprint asset must be selected before Task materialization.",
            )
        try:
            authority = dict(self.blueprint.get_task_authority(asset=asset))
        except McpExecutionError as exc:
            raise McpExecutionError(
                "EVIDENCE_ACQUISITION_REQUIRED",
                "Current authoritative Evidence is required before Task materialization.",
                details={"sourceCode": exc.code},
            ) from exc
        expected = (
            str(asset_binding.get("assetId") or ""),
            str(asset_binding.get("objectPath") or ""),
            str(asset_binding.get("evidenceRevisionId") or ""),
            str(asset_binding.get("evidenceManifestSha256") or ""),
            str(asset_binding.get("freshness") or ""),
        )
        actual = (
            str(authority.get("assetId") or ""),
            str(authority.get("objectPath") or ""),
            str(authority.get("evidenceRevisionId") or ""),
            str(authority.get("evidenceManifestSha256") or ""),
            str(authority.get("freshness") or ""),
        )
        if actual != expected or actual[-1] != "FRESH":
            raise McpExecutionError(
                "EVIDENCE_ACQUISITION_REQUIRED",
                "Solver Evidence identity changed before Task materialization.",
            )
        constraints = _mapping(problem.get("constraints"))
        criteria = [
            " ".join(str(item).split())
            for item in _sequence(problem.get("acceptanceCriteria"))
            if " ".join(str(item).split())
        ]
        criteria.extend(
            " ".join(str(item).split())
            for item in _sequence(constraints.get("acceptanceTests"))
            if " ".join(str(item).split())
        )
        criteria = list(dict.fromkeys(criteria))
        if len(criteria) > 12:
            raise McpExecutionError(
                "SOLVER_LIMIT_EXCEEDED",
                "Solver completion criteria exceed the existing Task Context limit.",
                details={
                    "field": "completionCriteria",
                    "count": len(criteria),
                    "maximum": 12,
                },
            )
        if not criteria:
            criteria = ["Satisfy the requested Blueprint request."]
        desired_values = list(
            dict.fromkeys(
                " ".join(str(item).split())
                for item in _sequence(constraints.get("desiredBehavior"))
                if " ".join(str(item).split())
            )
        )
        if len(desired_values) > 20:
            raise McpExecutionError(
                "SOLVER_LIMIT_EXCEEDED",
                "Solver desired behavior exceeds the existing Task Context limit.",
                details={
                    "field": "allowedChanges",
                    "count": len(desired_values),
                    "maximum": 20,
                },
            )
        invariants = list(
            dict.fromkeys(
                " ".join(str(item).split())
                for item in _sequence(constraints.get("invariants"))
                if " ".join(str(item).split())
            )
        )
        if len(invariants) > 20:
            raise McpExecutionError(
                "SOLVER_LIMIT_EXCEEDED",
                "Solver invariants exceed the existing Task Context limit.",
                details={
                    "field": "forbiddenChanges",
                    "count": len(invariants),
                    "maximum": 20,
                },
            )
        goal = _task_goal(solver_id, problem)
        reserved_task_id = str(pending_tasks.get(problem_id) or "")
        if not reserved_task_id:
            reserved_task_id = "task://" + _new_opaque_id(
                self.opaque_id_factory, label="Task reservation"
            )
            expected_bindings_digest = str(bindings.get("semanticDigest") or "")
            pending_tasks[problem_id] = reserved_task_id
            bindings["pendingTasks"] = pending_tasks
            documents["state"]["updatedAt"] = self.clock()
            _refresh_digest(bindings)
            _refresh_digest(documents["state"])
            _assert_private_solver_documents_safe(documents)
            self.store.save_solver(
                solver_id,
                documents,
                expected_bindings_digest=expected_bindings_digest,
            )

        recovered = False
        try:
            existing_context, _session = self.tasks.verified_task(
                reserved_task_id,
                persist_verification=False,
            )
        except McpExecutionError as exc:
            if exc.code != "TASK_NOT_FOUND":
                raise
        else:
            primary = _mapping(existing_context.get("primaryAsset"))
            if (
                str(existing_context.get("taskId") or "") != reserved_task_id
                or str(existing_context.get("mode") or "") != task_mode
                or str(existing_context.get("goal") or "") != goal
                or list(_sequence(existing_context.get("completionCriteria")))
                != criteria
                or list(_sequence(existing_context.get("allowedChanges")))
                != desired_values
                or list(_sequence(existing_context.get("forbiddenChanges")))
                != invariants
                or str(primary.get("name") or "") != asset
                or str(primary.get("assetId") or "") != actual[0]
                or str(primary.get("objectPath") or "") != actual[1]
                or str(primary.get("evidenceRevisionId") or "") != actual[2]
                or str(primary.get("evidenceManifestSha256") or "") != actual[3]
                or str(primary.get("freshness") or "") != actual[4]
            ):
                raise McpExecutionError(
                    "INTERNAL_CONTRACT_ERROR",
                    "Reserved Task metadata does not match this Solver problem.",
                )
            recovered = True

        expected_bindings_digest = str(bindings.get("semanticDigest") or "")
        if recovered:
            task_id = reserved_task_id
        else:
            context = self.tasks.create(
                mode=task_mode,
                asset=asset,
                goal=goal,
                completion_criteria=criteria,
                allowed_changes=desired_values,
                forbidden_changes=invariants,
                graph_ref=str(asset_binding.get("graphRef") or ""),
                supporting_assets=(),
                task_id=reserved_task_id,
            )
            task_id = str(context.get("taskId") or "")
        if task_id != reserved_task_id or not _TASK_ID.fullmatch(task_id):
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "TaskService did not honor the reserved Task handle.",
            )
        tasks[problem_id] = task_id
        pending_tasks.pop(problem_id, None)
        bindings["tasks"] = tasks
        bindings["pendingTasks"] = pending_tasks
        documents["state"]["materializedTaskIds"] = copy.deepcopy(tasks)
        asset_bindings = _mapping(bindings.get("assets"))
        task_problem_ids = {
            identifier
            for identifier, item in problems.items()
            if str(item.get("intent") or "")
            in {"DESIGN_BLUEPRINT_CHANGE", "ANSWER_CURRENT_BEHAVIOR"}
            and str(_mapping(asset_bindings.get(identifier)).get("asset") or "")
        }
        documents["state"]["status"] = (
            "TASKS_MATERIALIZED"
            if task_problem_ids <= set(tasks)
            else "READY_FOR_TASKS"
        )
        documents["state"]["updatedAt"] = self.clock()
        _refresh_digest(bindings)
        _refresh_digest(documents["state"])
        _assert_private_solver_documents_safe(documents)
        self.store.save_solver(
            solver_id,
            documents,
            expected_bindings_digest=expected_bindings_digest,
        )
        return render_solver_state(documents)


__all__ = ["SolverService"]
