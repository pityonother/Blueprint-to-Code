"""Task Context and recoverable Session lifecycle."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime

from blueprint_translator.context_pack import estimate_tokens

from ..blueprint_service import BlueprintService
from ..contracts import McpExecutionError, assert_path_free
from .canonical import canonical_json, canonical_sha256, semantic_digest
from .contracts import (
    MAX_SUPPORTING_ASSETS,
    TASK_CONTEXT_SCHEMA,
    TASK_MODES,
    TASK_SESSION_SCHEMA,
)
from .store import TaskStore


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _bounded_strings(
    value: Sequence[str],
    *,
    name: str,
    minimum: int,
    maximum: int,
    item_maximum: int = 1000,
) -> list[str]:
    if isinstance(value, (str, bytes)) or not minimum <= len(value) <= maximum:
        raise McpExecutionError(
            "INVALID_ARGUMENT",
            f"{name} must contain between {minimum} and {maximum} items.",
        )
    result: list[str] = []
    for raw in value:
        item = " ".join(str(raw).split())
        if not item or len(item) > item_maximum:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                f"{name} contains an invalid item.",
            )
        if item not in result:
            result.append(item)
    if len(result) < minimum:
        raise McpExecutionError(
            "INVALID_ARGUMENT",
            f"{name} must contain between {minimum} and {maximum} unique items.",
        )
    return result


class TaskService:
    def __init__(
        self,
        blueprint: BlueprintService,
        store: TaskStore,
        *,
        clock: Callable[[], str] = _now,
        opaque_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.blueprint = blueprint
        self.store = store
        self.clock = clock
        self.opaque_id_factory = opaque_id_factory or (lambda: uuid.uuid4().hex)

    def create(
        self,
        *,
        mode: str,
        asset: str,
        goal: str,
        completion_criteria: Sequence[str],
        allowed_changes: Sequence[str],
        forbidden_changes: Sequence[str],
        graph_ref: str = "",
        supporting_assets: Sequence[str] = (),
    ) -> dict[str, object]:
        normalized_goal = " ".join(str(goal).split())
        if mode not in TASK_MODES or not 1 <= len(normalized_goal) <= 1000:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Task mode or goal is outside the Task Context bounds.",
            )
        criteria = _bounded_strings(
            completion_criteria,
            name="completionCriteria",
            minimum=1,
            maximum=12,
        )
        allowed = _bounded_strings(
            allowed_changes,
            name="allowedChanges",
            minimum=0,
            maximum=20,
        )
        forbidden = _bounded_strings(
            forbidden_changes,
            name="forbiddenChanges",
            minimum=0,
            maximum=20,
        )
        supporting = _bounded_strings(
            supporting_assets,
            name="supportingAssets",
            minimum=0,
            maximum=MAX_SUPPORTING_ASSETS,
            item_maximum=256,
        )
        if len(str(asset)) > 256 or not str(asset).strip():
            raise McpExecutionError("INVALID_ARGUMENT", "asset is invalid.")
        if graph_ref and (
            len(graph_ref) > 4096
            or not graph_ref.startswith("bp://")
            or "/g/" not in graph_ref
        ):
            raise McpExecutionError("INVALID_ARGUMENT", "graphRef is invalid.")
        try:
            assert_path_free(
                {
                    "asset": str(asset),
                    "goal": normalized_goal,
                    "completionCriteria": criteria,
                    "allowedChanges": allowed,
                    "forbiddenChanges": forbidden,
                    "supportingAssets": supporting,
                }
            )
        except McpExecutionError as exc:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Task input must not contain machine-local path data.",
            ) from exc

        primary_authority = self.blueprint.get_task_authority(asset=str(asset))
        graph_targets = []
        if graph_ref:
            graph_targets = [
                dict(item)
                for item in primary_authority["graphTargets"]
                if item.get("ref") == graph_ref
            ]
            if len(graph_targets) != 1:
                raise McpExecutionError(
                    "INVALID_ARGUMENT",
                    "graphRef is not an exact graph in the current Evidence revision.",
                )

        supporting_context: list[dict[str, object]] = []
        seen_asset_ids = {str(primary_authority["assetId"])}
        for supporting_asset in supporting:
            authority = self.blueprint.get_task_authority(asset=supporting_asset)
            asset_id = str(authority["assetId"])
            if asset_id in seen_asset_ids:
                raise McpExecutionError(
                    "INVALID_ARGUMENT",
                    "Primary and supporting assets must be distinct.",
                )
            seen_asset_ids.add(asset_id)
            supporting_context.append(self._asset_projection(authority, read_only=True))

        timestamp = self.clock()
        task_id = f"task://{self._new_opaque_id()}"
        context: dict[str, object] = {
            "schema": TASK_CONTEXT_SCHEMA,
            "taskId": task_id,
            "mode": mode,
            "goal": normalized_goal,
            "completionCriteria": criteria,
            "allowedChanges": allowed,
            "forbiddenChanges": forbidden,
            "primaryAsset": self._asset_projection(
                primary_authority,
                read_only=False,
            ),
            "supportingAssets": supporting_context,
            "graphTargets": graph_targets,
            "confirmedFacts": [],
            "assumptions": [],
            "blockingQuestions": [],
            "nonBlockingUnknowns": [],
            "graphSlices": [],
            "queryLedger": {
                "researchCalls": 0,
                "uniqueQueries": 0,
                "cacheHits": 0,
                "contextPages": 0,
                "evidenceRevision": primary_authority["evidenceRevisionId"],
            },
            "readiness": "DISCOVERY",
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        context["semanticDigest"] = semantic_digest(context)
        session: dict[str, object] = {
            "schema": TASK_SESSION_SCHEMA,
            "taskId": task_id,
            "phase": "DISCOVERY",
            "taskContextSha256": canonical_sha256(context),
            "evidenceIdentities": [
                self._evidence_identity(context["primaryAsset"]),
                *(
                    self._evidence_identity(item)
                    for item in context["supportingAssets"]
                ),
            ],
            "confirmedKnowledge": [],
            "openQuestions": [],
            "patchPlanId": "",
            "patchPlanSha256": "",
            "queryLedger": copy.deepcopy(context["queryLedger"]),
            "lastVerifiedAt": timestamp,
            "reasonCode": "",
        }
        session["semanticDigest"] = semantic_digest(session)
        assert_path_free(context)
        assert_path_free(session)
        self.store.create_task(task_id, context, session)
        return copy.deepcopy(context)

    def resume(
        self,
        task_id: str,
        *,
        persist_verification: bool = True,
    ) -> dict[str, object]:
        context, session = self.verified_task(
            task_id,
            persist_verification=persist_verification,
        )
        plan_status = ""
        if session.get("patchPlanId"):
            try:
                plan_status = str(
                    self.store.load_plan(task_id, str(session["patchPlanId"])).get(
                        "status", ""
                    )
                )
            except McpExecutionError:
                plan_status = "MISSING"
        result: dict[str, object] = {
            "schema": "blueprint-to-code.task-resume/v1",
            "taskId": task_id,
            "phase": session["phase"],
            "goal": context["goal"],
            "evidenceIdentity": self._evidence_identity(context["primaryAsset"]),
            "graphTargets": [
                {
                    "ref": item.get("ref", ""),
                    "name": item.get("name", ""),
                }
                for item in context["graphTargets"]
            ],
            "confirmedFactSummaries": [
                str(item.get("text") or "")[:240]
                for item in context["confirmedFacts"]
            ],
            "assumptionsCount": len(context["assumptions"]),
            "blockingQuestions": self._resume_metadata(
                context["blockingQuestions"],
                text_limit=160,
            ),
            "nonBlockingUnknowns": self._resume_metadata(
                context["nonBlockingUnknowns"],
                text_limit=120,
            ),
            "storedSliceSummaries": [
                {
                    "sliceId": item.get("sliceId", ""),
                    "querySignature": item.get("querySignature", ""),
                    "question": str(item.get("question") or "")[:120],
                    "graphRefs": list(item.get("graphRefs") or []),
                    "nodeCount": item.get("nodeCount", 0),
                    "pinCount": item.get("pinCount", 0),
                    "edgeCount": item.get("edgeCount", 0),
                    "semanticDigest": item.get("semanticDigest", ""),
                }
                for item in context["graphSlices"]
            ],
            "summaryCounts": {
                "confirmedFacts": len(context["confirmedFacts"]),
                "blockingQuestions": len(context["blockingQuestions"]),
                "nonBlockingUnknowns": len(context["nonBlockingUnknowns"]),
                "storedSlices": len(context["graphSlices"]),
            },
            "queryLedger": copy.deepcopy(context["queryLedger"]),
            "plan": {
                "planId": session.get("patchPlanId", ""),
                "semanticDigest": session.get("patchPlanSha256", ""),
                "status": plan_status,
            },
            "nextRecommendedAction": self._next_action(str(session["phase"])),
            "humanSummary": self._human_summary(context, session, plan_status),
            "estimatedTokens": 0,
        }
        for _attempt in range(2):
            self._fit_resume_budget(result)
            result["estimatedTokens"] = estimate_tokens(canonical_json(result))
        assert_path_free(result)
        return result

    @staticmethod
    def _resume_metadata(
        items: Sequence[dict[str, object]],
        *,
        text_limit: int,
    ) -> list[dict[str, object]]:
        summaries: list[dict[str, object]] = []
        for item in items:
            summary = {
                str(key): value
                for key, value in item.items()
                if str(key).endswith("Id")
            }
            summary["text"] = str(item.get("text") or "")[:text_limit]
            summaries.append(summary)
        return summaries

    @staticmethod
    def _fit_resume_budget(result: dict[str, object], max_tokens: int = 1600) -> None:
        """Compact optional resume detail while retaining counts and all blocker IDs."""

        def over_budget() -> bool:
            return estimate_tokens(canonical_json(result)) > max_tokens

        optional_lists = (
            result["nonBlockingUnknowns"],
            result["confirmedFactSummaries"],
            result["storedSliceSummaries"],
        )
        for items in optional_lists:
            while items and over_budget():
                items.pop()

        blockers = result["blockingQuestions"]
        if over_budget():
            for blocker in blockers:
                if isinstance(blocker, dict):
                    blocker["text"] = str(blocker.get("text") or "")[:64]
        if over_budget():
            for blocker in blockers:
                if isinstance(blocker, dict):
                    blocker.pop("text", None)

        graph_targets = result["graphTargets"]
        while graph_targets and over_budget():
            graph_targets.pop()

        if over_budget():
            result["goal"] = str(result["goal"])[:240]
            result["humanSummary"] = str(result["humanSummary"])[:320]
        if over_budget():
            raise McpExecutionError(
                "RESULT_BUDGET_EXCEEDED",
                "Task resume metadata cannot fit the 1600-token public response budget.",
            )

    def verified_task(
        self,
        task_id: str,
        *,
        allowed_phases: Iterable[str] | None = None,
        persist_verification: bool = True,
    ) -> tuple[dict[str, object], dict[str, object]]:
        context = self.store.load_context(task_id)
        session = self.store.load_session(task_id)
        if context.get("taskId") != task_id or session.get("taskId") != task_id:
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Stored Task identity does not match its opaque handle.",
            )
        if session.get("phase") == "BLOCKED":
            raise McpExecutionError(
                "TASK_BLOCKED",
                "Task is blocked and cannot be refreshed automatically.",
                details={"reasonCode": str(session.get("reasonCode") or "TASK_BLOCKED")},
            )
        try:
            self._verify_asset(context["primaryAsset"])
            for supporting in context.get("supportingAssets", []):
                self._verify_asset(supporting)
        except McpExecutionError as exc:
            if exc.code in {
                "EVIDENCE_REVISION_CHANGED",
                "EVIDENCE_STALE",
                "EVIDENCE_NOT_FOUND",
                "ASSET_NOT_FOUND",
            }:
                if persist_verification:
                    self._block_revision_change(task_id, context, session)
                if exc.code == "EVIDENCE_REVISION_CHANGED":
                    raise
                raise McpExecutionError(
                    "EVIDENCE_REVISION_CHANGED",
                    "Task Evidence identity is no longer current.",
                ) from exc
            raise
        if allowed_phases is not None and str(session.get("phase")) not in set(
            allowed_phases
        ):
            raise McpExecutionError(
                "TASK_PHASE_INVALID",
                "Task phase does not allow this operation.",
                details={"phase": str(session.get("phase") or "")},
            )
        if persist_verification:
            session["lastVerifiedAt"] = self.clock()
            self.sync_and_save(context, session)
        return context, session

    def sync_and_save(
        self,
        context: dict[str, object],
        session: dict[str, object],
    ) -> None:
        context["updatedAt"] = self.clock()
        context["semanticDigest"] = semantic_digest(context)
        session["taskContextSha256"] = canonical_sha256(context)
        session["confirmedKnowledge"] = [
            {
                "id": item.get("id", ""),
                "text": str(item.get("text") or "")[:240],
                "evidenceRefs": list(item.get("evidenceRefs") or []),
            }
            for item in context.get("confirmedFacts", [])
        ]
        session["openQuestions"] = copy.deepcopy(
            context.get("blockingQuestions", [])
        )
        session["queryLedger"] = copy.deepcopy(context.get("queryLedger", {}))
        session["semanticDigest"] = semantic_digest(session)
        assert_path_free(context)
        assert_path_free(session)
        self.store.save_task(str(context["taskId"]), context, session)

    def _verify_asset(self, stored: dict[str, object]) -> None:
        current = self.blueprint.get_task_authority(asset=str(stored["name"]))
        expected = (
            str(stored.get("assetId") or ""),
            str(stored.get("objectPath") or ""),
            str(stored.get("evidenceRevisionId") or ""),
            str(stored.get("evidenceManifestSha256") or ""),
            str(stored.get("freshness") or ""),
        )
        actual = (
            str(current.get("assetId") or ""),
            str(current.get("objectPath") or ""),
            str(current.get("evidenceRevisionId") or ""),
            str(current.get("evidenceManifestSha256") or ""),
            str(current.get("freshness") or ""),
        )
        if actual != expected or actual[-1] != "FRESH":
            raise McpExecutionError(
                "EVIDENCE_REVISION_CHANGED",
                "Task Evidence identity changed; old refs were not remapped.",
            )

    def _block_revision_change(
        self,
        task_id: str,
        context: dict[str, object],
        session: dict[str, object],
    ) -> None:
        session["phase"] = "BLOCKED"
        session["reasonCode"] = "EVIDENCE_REVISION_CHANGED"
        session["lastVerifiedAt"] = self.clock()
        session["semanticDigest"] = semantic_digest(session)
        self.store.save_task(task_id, context, session)

    def _new_opaque_id(self) -> str:
        opaque = str(self.opaque_id_factory())
        if len(opaque) != 32 or any(ch not in "0123456789abcdef" for ch in opaque):
            raise McpExecutionError(
                "INTERNAL_CONTRACT_ERROR",
                "Opaque ID generator returned an invalid value.",
            )
        return opaque

    @staticmethod
    def _asset_projection(
        authority: dict[str, object],
        *,
        read_only: bool,
    ) -> dict[str, object]:
        return {
            "name": authority["name"],
            "assetId": authority["assetId"],
            "objectPath": authority["objectPath"],
            "evidenceRevisionId": authority["evidenceRevisionId"],
            "evidenceManifestSha256": authority["evidenceManifestSha256"],
            "freshness": authority["freshness"],
            "readOnly": read_only,
        }

    @staticmethod
    def _evidence_identity(asset: dict[str, object]) -> dict[str, object]:
        return {
            "assetId": asset.get("assetId", ""),
            "objectPath": asset.get("objectPath", ""),
            "evidenceRevisionId": asset.get("evidenceRevisionId", ""),
            "evidenceManifestSha256": asset.get("evidenceManifestSha256", ""),
            "freshness": asset.get("freshness", ""),
        }

    @staticmethod
    def _next_action(phase: str) -> str:
        return {
            "DISCOVERY": "blueprint_task_research",
            "READY_TO_PLAN": "blueprint_patch_plan_draft",
            "PLAN_DRAFT": "blueprint_patch_plan_validate",
            "PLAN_CONFIRMED": "Stop; Phase 2 has no execution tool.",
        }.get(phase, "Stop and resolve the blocking Task state.")

    @staticmethod
    def _human_summary(
        context: dict[str, object],
        session: dict[str, object],
        plan_status: str,
    ) -> str:
        return (
            f"目标: {context['goal']}\n"
            f"Evidence revision: {context['primaryAsset']['evidenceRevisionId']}\n"
            f"Target graphs: {len(context['graphTargets'])}\n"
            f"Graph slices: {len(context['graphSlices'])}\n"
            f"Blockers: {len(context['blockingQuestions'])}\n"
            f"Plan: {plan_status or 'NONE'}\n"
            f"Status: {session['phase']}"
        )


__all__ = ["TaskService"]
