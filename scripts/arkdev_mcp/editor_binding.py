"""Exact-only binding of live Editor identity to Evidence and Task metadata."""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence

from arkdev_mcp.blueprint_service import BlueprintService
from arkdev_mcp.contracts import McpExecutionError, assert_path_free
from arkdev_mcp.tasking.task_service import TaskService


_GUID = re.compile(r"^[0-9A-Fa-f]{32}$")
_MAX_ASSET_PAGES = 5
_ASSET_PAGE_SIZE = 100
_EVIDENCE_FAILURES = frozenset(
    {
        "ASSET_NOT_FOUND",
        "EVIDENCE_NOT_FOUND",
        "EVIDENCE_STALE",
        "EVIDENCE_NOT_AUTHORITATIVE",
        "EVIDENCE_REVISION_MISMATCH",
        "EVIDENCE_REVISION_CHANGED",
    }
)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return value
    return ()


def _canonical_guid(value: object) -> str:
    candidate = str(value or "").strip()
    return candidate.upper() if _GUID.fullmatch(candidate) else ""


def _asset_query(object_path: str) -> str:
    leaf = object_path.rsplit("/", 1)[-1]
    object_name = leaf.rsplit(".", 1)[-1] if "." in leaf else leaf
    return object_name[:128]


class EditorBindingService:
    """Enrich a path-free Editor projection without changing any authority."""

    def __init__(
        self,
        blueprint: BlueprintService,
        tasks: TaskService,
    ) -> None:
        self._blueprint = blueprint
        self._tasks = tasks

    def enrich(
        self,
        state: Mapping[str, object],
        *,
        task_id: str = "",
    ) -> dict[str, object]:
        result = copy.deepcopy(dict(state))
        result["activeAssetBinding"] = {}
        result["activeGraphBinding"] = {}
        result["taskBinding"] = {}
        nodes = [dict(item) for item in _sequence(result.get("graphNodes")) if isinstance(item, Mapping)]
        result["graphNodes"] = nodes

        if not result.get("connected") or not result.get("activeAsset"):
            result["activeAssetBinding"] = {"status": "NOT_FOUND"}
            result["activeGraphBinding"] = {"status": "NOT_FOUND"}
            self._mark_unbound(nodes)
            result["taskBinding"] = self._task_binding(
                task_id,
                state=result,
                asset_binding=result["activeAssetBinding"],
                graph_binding=result["activeGraphBinding"],
            )
            assert_path_free(result)
            return result

        active_object_path = str(result.get("activeAsset") or "")
        candidates = self._exact_asset_candidates(active_object_path)
        if not candidates:
            result["activeAssetBinding"] = {"status": "NOT_FOUND"}
            result["activeGraphBinding"] = {"status": "NOT_FOUND"}
            self._mark_unbound(nodes)
        elif len(candidates) > 1:
            result["activeAssetBinding"] = {"status": "AMBIGUOUS"}
            result["activeGraphBinding"] = {"status": "NOT_FOUND"}
            self._mark_unbound(nodes)
        else:
            self._bind_exact_candidate(result, nodes, candidates[0])

        result["taskBinding"] = self._task_binding(
            task_id,
            state=result,
            asset_binding=_mapping(result["activeAssetBinding"]),
            graph_binding=_mapping(result["activeGraphBinding"]),
        )
        assert_path_free(result)
        return result

    def _exact_asset_candidates(
        self,
        object_path: str,
    ) -> list[dict[str, object]]:
        query = _asset_query(object_path)
        cursor = ""
        matches: list[dict[str, object]] = []
        for _page in range(_MAX_ASSET_PAGES):
            payload = self._blueprint.list_assets(
                query=query,
                limit=_ASSET_PAGE_SIZE,
                cursor=cursor,
            )
            for raw in _sequence(payload.get("items")):
                if not isinstance(raw, Mapping):
                    continue
                health = _mapping(raw.get("health"))
                asset = _mapping(health.get("asset"))
                if str(asset.get("objectPath") or "") == object_path:
                    matches.append(dict(raw))
            page = _mapping(payload.get("page"))
            cursor = str(page.get("nextCursor") or "")
            if not cursor:
                break
        return matches

    def _bind_exact_candidate(
        self,
        result: dict[str, object],
        nodes: list[dict[str, object]],
        candidate: Mapping[str, object],
    ) -> None:
        health = _mapping(candidate.get("health"))
        evidence = _mapping(health.get("evidence"))
        if (
            str(health.get("status") or "") != "READY"
            or str(evidence.get("freshnessStatus") or "") != "FRESH"
        ):
            result["activeAssetBinding"] = {"status": "EVIDENCE_STALE"}
            result["activeGraphBinding"] = {"status": "EVIDENCE_STALE"}
            self._mark_unbound(nodes)
            return
        graph_details = _mapping(result.get("activeGraphDetails"))
        graph_name = str(graph_details.get("name") or "")
        if not graph_name:
            result["activeAssetBinding"] = self._asset_projection(health)
            result["activeGraphBinding"] = {"status": "NOT_FOUND"}
            self._mark_unbound(nodes)
            return
        try:
            authority = self._blueprint.get_editor_binding_authority(
                asset=str(candidate.get("asset") or ""),
                graph_name=graph_name,
            )
        except McpExecutionError as exc:
            status = "EVIDENCE_STALE" if exc.code in _EVIDENCE_FAILURES else "NOT_FOUND"
            result["activeAssetBinding"] = {"status": status}
            result["activeGraphBinding"] = {"status": status}
            self._mark_unbound(nodes)
            return
        result["activeAssetBinding"] = {
            "status": "EXACT",
            "assetId": str(authority.get("assetId") or ""),
            "objectPath": str(authority.get("objectPath") or ""),
            "evidenceRevisionId": str(
                authority.get("evidenceRevisionId") or ""
            ),
            "evidenceManifestSha256": str(
                authority.get("evidenceManifestSha256") or ""
            ),
        }
        graph_matches = [
            dict(item)
            for item in _sequence(authority.get("graphMatches"))
            if isinstance(item, Mapping)
        ]
        if not graph_matches:
            result["activeGraphBinding"] = {"status": "NOT_FOUND"}
            self._mark_unbound(nodes)
            return
        if len(graph_matches) > 1:
            result["activeGraphBinding"] = {"status": "AMBIGUOUS"}
            self._mark_unbound(nodes)
            return
        graph = graph_matches[0]
        result["activeGraphBinding"] = {
            "status": "EXACT",
            "name": str(graph.get("name") or ""),
            "graphRef": str(graph.get("ref") or ""),
        }
        self._bind_nodes(nodes, _sequence(graph.get("nodeGuidBindings")))

    @staticmethod
    def _asset_projection(health: Mapping[str, object]) -> dict[str, object]:
        asset = _mapping(health.get("asset"))
        evidence = _mapping(health.get("evidence"))
        return {
            "status": "EXACT",
            "assetId": str(asset.get("assetId") or ""),
            "objectPath": str(asset.get("objectPath") or ""),
            "evidenceRevisionId": str(evidence.get("revisionId") or ""),
            "evidenceManifestSha256": str(
                evidence.get("manifestSha256") or ""
            ),
        }

    @staticmethod
    def _bind_nodes(
        nodes: list[dict[str, object]],
        raw_bindings: Sequence[object],
    ) -> None:
        by_guid: dict[str, list[str]] = defaultdict(list)
        live_guid_counts: dict[str, int] = defaultdict(int)
        for raw in raw_bindings:
            if not isinstance(raw, Mapping):
                continue
            guid = _canonical_guid(raw.get("nodeGuid"))
            node_ref = str(raw.get("evidenceNodeRef") or "")
            if guid and node_ref:
                by_guid[guid].append(node_ref)
        for node in nodes:
            guid = _canonical_guid(node.get("nodeGuid"))
            if guid:
                live_guid_counts[guid] += 1
        for node in nodes:
            guid = _canonical_guid(node.get("nodeGuid"))
            matches = by_guid.get(guid, [])
            if guid and live_guid_counts[guid] == 1 and len(matches) == 1:
                node["bindingStatus"] = "EXACT"
                node["evidenceNodeRef"] = matches[0]
            elif guid and (live_guid_counts[guid] > 1 or len(matches) > 1):
                node["bindingStatus"] = "AMBIGUOUS"
                node["evidenceNodeRef"] = ""
            else:
                node["bindingStatus"] = "UNBOUND"
                node["evidenceNodeRef"] = ""

    @staticmethod
    def _mark_unbound(nodes: list[dict[str, object]]) -> None:
        for node in nodes:
            node["bindingStatus"] = "UNBOUND"
            node["evidenceNodeRef"] = ""

    def _task_binding(
        self,
        task_id: str,
        *,
        state: Mapping[str, object],
        asset_binding: Mapping[str, object],
        graph_binding: Mapping[str, object],
    ) -> dict[str, object]:
        if not task_id:
            return {}
        base: dict[str, object] = {
            "taskId": task_id,
            "status": "TASK_NOT_FOUND",
            "assetMatch": False,
            "graphMatch": False,
            "evidenceMatch": False,
            "taskPhase": "",
            "planId": "",
            "mutationReady": False,
        }
        try:
            task = self._tasks.resume(task_id, persist_verification=False)
        except McpExecutionError as exc:
            if exc.code == "TASK_BLOCKED":
                base["status"] = "TASK_BLOCKED"
            elif exc.code in _EVIDENCE_FAILURES:
                base["status"] = "EVIDENCE_MISMATCH"
            else:
                base["status"] = "TASK_NOT_FOUND"
            return base
        base["taskPhase"] = str(task.get("phase") or "")
        base["planId"] = str(_mapping(task.get("plan")).get("planId") or "")
        if not state.get("connected") or not state.get("activeAsset"):
            base["status"] = "EDITOR_IDLE"
            return base
        evidence_identity = _mapping(task.get("evidenceIdentity"))
        asset_match = str(evidence_identity.get("objectPath") or "") == str(
            state.get("activeAsset") or ""
        )
        active_graph = str(
            _mapping(state.get("activeGraphDetails")).get("name") or ""
        )
        task_graphs = [
            _mapping(item)
            for item in _sequence(task.get("graphTargets"))
        ]
        graph_match = bool(active_graph) and any(
            str(item.get("name") or "") == active_graph for item in task_graphs
        )
        evidence_match = bool(
            asset_binding.get("status") == "EXACT"
            and graph_binding.get("status") == "EXACT"
            and str(asset_binding.get("evidenceRevisionId") or "")
            == str(evidence_identity.get("evidenceRevisionId") or "")
            and str(asset_binding.get("evidenceManifestSha256") or "")
            == str(evidence_identity.get("evidenceManifestSha256") or "")
            and any(
                str(item.get("ref") or "")
                == str(graph_binding.get("graphRef") or "")
                for item in task_graphs
            )
        )
        base.update(
            {
                "assetMatch": asset_match,
                "graphMatch": graph_match,
                "evidenceMatch": evidence_match,
            }
        )
        if not asset_match:
            base["status"] = "ASSET_MISMATCH"
        elif not graph_match:
            base["status"] = "GRAPH_MISMATCH"
        elif not evidence_match:
            base["status"] = "EVIDENCE_MISMATCH"
        else:
            base["status"] = "MATCHED"
        return base


__all__ = ["EditorBindingService"]
