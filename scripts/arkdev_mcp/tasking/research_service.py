"""Bounded, revision-scoped Graph Slice research with persistent caching."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, Sequence

from ..blueprint_service import BlueprintService
from ..contracts import McpExecutionError, assert_path_free
from .canonical import canonical_sha256, semantic_digest
from .contracts import GRAPH_SLICE_SCHEMA, MAX_GRAPH_SLICES, MAX_GRAPH_TARGETS
from .store import TaskStore
from .task_service import TaskService


class ResearchService:
    def __init__(
        self,
        blueprint: BlueprintService,
        tasks: TaskService,
        store: TaskStore,
    ) -> None:
        self.blueprint = blueprint
        self.tasks = tasks
        self.store = store

    def research(
        self,
        *,
        task_id: str,
        question: str,
        graph_ref: str = "",
        seed_refs: Sequence[str] = (),
        max_hops: int = 1,
        max_nodes: int = 40,
        max_pins: int = 160,
        max_edges: int = 160,
        budget_tokens: int = 2400,
        task_update: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_question = " ".join(str(question).split())
        normalized_seeds = list(dict.fromkeys(str(item).strip() for item in seed_refs))
        self._validate_arguments(
            question=normalized_question,
            graph_ref=graph_ref,
            seed_refs=normalized_seeds,
            max_hops=max_hops,
            max_nodes=max_nodes,
            max_pins=max_pins,
            max_edges=max_edges,
            budget_tokens=budget_tokens,
        )
        context, session = self.tasks.verified_task(
            task_id,
            allowed_phases={"DISCOVERY", "READY_TO_PLAN"},
        )
        if task_update is not None and not isinstance(task_update, Mapping):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "taskUpdate must be an object.",
            )
        update = task_update or {}
        preview_context = copy.deepcopy(context)
        self._apply_task_update(preview_context, update)
        current_graph_refs = {
            str(item.get("ref") or "") for item in context["graphTargets"]
        }
        if (
            graph_ref
            and graph_ref not in current_graph_refs
            and len(current_graph_refs) >= MAX_GRAPH_TARGETS
        ):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "A Task may target at most two Blueprint graphs.",
            )
        primary = context["primaryAsset"]
        signature = canonical_sha256(
            {
                "schema": "blueprint-to-code.graph-slice-query/v1",
                "evidenceRevisionId": primary["evidenceRevisionId"],
                "evidenceManifestSha256": primary["evidenceManifestSha256"],
                "question": normalized_question.casefold(),
                "graphRef": graph_ref,
                "seedRefs": normalized_seeds,
                "maxHops": max_hops,
                "maxNodes": max_nodes,
                "maxPins": max_pins,
                "maxEdges": max_edges,
                "budgetTokens": budget_tokens,
            }
        )
        ledger = context["queryLedger"]
        existing = next(
            (
                item
                for item in context["graphSlices"]
                if item.get("querySignature") == signature
            ),
            None,
        )
        if existing is None and len(context["graphSlices"]) >= MAX_GRAPH_SLICES:
            raise McpExecutionError(
                "TASK_SLICE_LIMIT_REACHED",
                "Task already contains the maximum of eight distinct Graph Slices.",
            )

        ledger["researchCalls"] = int(ledger.get("researchCalls") or 0) + 1
        if existing is not None:
            payload = self.store.load_slice(task_id, signature)
            ledger["cacheHits"] = int(ledger.get("cacheHits") or 0) + 1
            self._apply_task_update(context, update)
            self._refresh_readiness(context, session)
            self.tasks.sync_and_save(context, session)
            return self._response(payload, context, cached=True)

        raw = self.blueprint.get_context(
            asset=str(primary["name"]),
            goal=normalized_question,
            graph_ref=graph_ref,
            seed_refs=normalized_seeds,
            max_hops=max_hops,
            max_nodes=max_nodes,
            max_pins=max_pins,
            max_edges=max_edges,
            budget_tokens=budget_tokens,
            continuation="",
        )
        evidence = raw["identity"]["evidence"]
        if (
            evidence.get("revisionId") != primary["evidenceRevisionId"]
            or evidence.get("manifestSha256")
            != primary["evidenceManifestSha256"]
        ):
            session["phase"] = "BLOCKED"
            session["reasonCode"] = "EVIDENCE_REVISION_CHANGED"
            self.tasks.sync_and_save(context, session)
            raise McpExecutionError(
                "EVIDENCE_REVISION_CHANGED",
                "Research returned a different Evidence identity.",
            )

        merged_graphs = {
            str(item.get("ref") or ""): dict(item)
            for item in context["graphTargets"]
        }
        for item in raw.get("graphTargets", []):
            merged_graphs[str(item.get("ref") or "")] = dict(item)
        if len(merged_graphs) > MAX_GRAPH_TARGETS:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "A Task may target at most two Blueprint graphs.",
            )
        context["graphTargets"] = [merged_graphs[key] for key in sorted(merged_graphs)]

        payload: dict[str, object] = {
            "schema": GRAPH_SLICE_SCHEMA,
            "sliceId": f"slice://{signature[:32]}",
            "querySignature": signature,
            "question": normalized_question,
            "evidenceIdentity": {
                "assetId": primary["assetId"],
                "objectPath": primary["objectPath"],
                "evidenceRevisionId": primary["evidenceRevisionId"],
                "evidenceManifestSha256": primary["evidenceManifestSha256"],
            },
            "graphTargets": copy.deepcopy(raw.get("graphTargets", [])),
            "nodes": copy.deepcopy(raw.get("nodes", [])),
            "pins": copy.deepcopy(raw.get("pins", [])),
            "edges": copy.deepcopy(raw.get("edges", [])),
            "facts": copy.deepcopy(raw.get("facts", [])),
            "gaps": copy.deepcopy(raw.get("gaps", [])),
            "truncated": bool(raw.get("truncated")),
            "continuation": str(raw.get("continuation") or ""),
        }
        payload["semanticDigest"] = semantic_digest(payload)
        assert_path_free(payload)
        self.store.save_slice(task_id, payload)
        context["graphSlices"].append(self._slice_summary(payload))
        context["confirmedFacts"] = self._merge_confirmed_facts(
            context["confirmedFacts"],
            payload["facts"],
            revision=str(primary["evidenceRevisionId"]),
        )
        ledger["uniqueQueries"] = int(ledger.get("uniqueQueries") or 0) + 1
        ledger["contextPages"] = int(ledger.get("contextPages") or 0) + 1
        self._apply_task_update(context, update)
        self._refresh_readiness(context, session)
        self.tasks.sync_and_save(context, session)
        return self._response(payload, context, cached=False)

    @staticmethod
    def _validate_arguments(
        *,
        question: str,
        graph_ref: str,
        seed_refs: Sequence[str],
        max_hops: int,
        max_nodes: int,
        max_pins: int,
        max_edges: int,
        budget_tokens: int,
    ) -> None:
        invalid = (
            not question
            or len(question) > 1000
            or (graph_ref and (not graph_ref.startswith("bp://") or "/g/" not in graph_ref))
            or len(graph_ref) > 4096
            or len(seed_refs) > 10
            or any(not ref.startswith("bp://") or len(ref) > 4096 for ref in seed_refs)
            or not 0 <= max_hops <= 2
            or not 1 <= max_nodes <= 100
            or not 1 <= max_pins <= 400
            or not 1 <= max_edges <= 400
            or not 800 <= budget_tokens <= 6000
        )
        if invalid:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Task research arguments are outside the bounded Graph Slice contract.",
            )

    def _apply_task_update(
        self,
        context: dict[str, object],
        update: Mapping[str, object],
    ) -> None:
        resolve_ids = self._string_sequence(update.get("resolveBlockingQuestionIds", []))
        if resolve_ids:
            context["blockingQuestions"] = [
                item
                for item in context["blockingQuestions"]
                if item.get("questionId") not in set(resolve_ids)
            ]
        self._append_metadata(
            context["blockingQuestions"],
            self._string_sequence(update.get("addBlockingQuestions", [])),
            id_key="questionId",
            prefix="question",
            maximum=20,
        )
        self._append_metadata(
            context["nonBlockingUnknowns"],
            self._string_sequence(update.get("addNonBlockingUnknowns", [])),
            id_key="unknownId",
            prefix="unknown",
            maximum=20,
        )
        self._append_metadata(
            context["assumptions"],
            self._string_sequence(update.get("addAssumptions", [])),
            id_key="assumptionId",
            prefix="assumption",
            maximum=20,
        )

    @staticmethod
    def _string_sequence(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "taskUpdate fields must be bounded string arrays.",
            )
        result = [" ".join(str(item).split()) for item in value]
        if len(result) > 20 or any(not item or len(item) > 1000 for item in result):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "taskUpdate fields exceed their bounds.",
            )
        return result

    @staticmethod
    def _append_metadata(
        target: list[dict[str, object]],
        texts: Sequence[str],
        *,
        id_key: str,
        prefix: str,
        maximum: int,
    ) -> None:
        existing = {str(item.get("text") or "").casefold() for item in target}
        for text in texts:
            if text.casefold() in existing:
                continue
            target.append(
                {
                    id_key: f"{prefix}://{hashlib.sha256(text.casefold().encode('utf-8')).hexdigest()[:24]}",
                    "text": text,
                }
            )
            existing.add(text.casefold())
        if len(target) > maximum:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Task metadata update exceeds its maximum item count.",
            )

    @staticmethod
    def _merge_confirmed_facts(
        existing: list[dict[str, object]],
        candidates: Sequence[dict[str, object]],
        *,
        revision: str,
    ) -> list[dict[str, object]]:
        merged = {str(item.get("id") or ""): copy.deepcopy(item) for item in existing}
        for item in candidates:
            refs = [str(ref) for ref in item.get("evidenceRefs", [])]
            if (
                str(item.get("status") or "").upper() != "CONFIRMED"
                or not refs
                or any(
                    not ref.startswith("bp://") or f"@{revision}/" not in ref
                    for ref in refs
                )
            ):
                continue
            merged[str(item.get("id") or canonical_sha256(item))] = copy.deepcopy(item)
        return [merged[key] for key in sorted(merged)]

    @staticmethod
    def _slice_summary(payload: dict[str, object]) -> dict[str, object]:
        return {
            "sliceId": payload["sliceId"],
            "querySignature": payload["querySignature"],
            "question": str(payload["question"])[:240],
            "graphRefs": [
                str(item.get("ref") or "") for item in payload["graphTargets"]
            ],
            "nodeCount": len(payload["nodes"]),
            "pinCount": len(payload["pins"]),
            "edgeCount": len(payload["edges"]),
            "semanticDigest": payload["semanticDigest"],
        }

    @staticmethod
    def _refresh_readiness(
        context: dict[str, object],
        session: dict[str, object],
    ) -> None:
        ready = bool(
            context["primaryAsset"].get("freshness") == "FRESH"
            and context.get("graphTargets")
            and not context.get("blockingQuestions")
            and context.get("graphSlices")
            and context.get("completionCriteria")
        )
        context["readiness"] = "READY_TO_PLAN" if ready else "DISCOVERY"
        session["phase"] = context["readiness"]
        session["reasonCode"] = ""

    @staticmethod
    def _response(
        payload: dict[str, object],
        context: dict[str, object],
        *,
        cached: bool,
    ) -> dict[str, object]:
        response = copy.deepcopy(payload)
        response["cached"] = cached
        response["queryLedger"] = copy.deepcopy(context["queryLedger"])
        response["taskReadiness"] = context["readiness"]
        assert_path_free(response)
        return response


__all__ = ["ResearchService"]
