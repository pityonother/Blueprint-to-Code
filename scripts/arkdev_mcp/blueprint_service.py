"""High-level, path-free Blueprint queries for the read-only MCP surface."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from blueprint_server.request import ApiProblem
from blueprint_server.routes_blueprint import (
    _asset_identifier,
    _resolve_asset,
    blueprint_get_payload,
)
from blueprint_translator.context_pack import estimate_tokens
from blueprint_translator.evidence_repository import (
    EvidenceRepository,
    ResolvedEvidenceState,
    evidence_manifest_payload,
    open_resolved_asset_repository,
    resolve_asset_evidence_state,
)
from blueprint_translator.interpretation_publication import (
    LoadedInterpretation,
    load_current_interpretation,
)

from .contracts import McpExecutionError, assert_path_free


ASSET_LIST_SCHEMA = "blueprint-to-code.mcp-blueprint-assets/v1"
CONTEXT_SCHEMA = "blueprint-to-code.mcp-blueprint-context/v1"
NODE_SCHEMA = "blueprint-to-code.mcp-blueprint-node/v1"

_GOAL_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u3400-\u9fff]{2,}")
_GOAL_STOP_WORDS = frozenset(
    {
        "and",
        "find",
        "for",
        "from",
        "guard",
        "入口",
        "找到",
        "服务端",
        "服务器侧",
    }
)
_MAX_FACT_CANDIDATES = 100
_MAX_GAP_CANDIDATES = 100
_MAX_NODE_CANDIDATES = 100
_MAX_PIN_CANDIDATES = 400
_MAX_EDGE_CANDIDATES = 400
_FACT_PAGE_SIZE = 20
_GAP_PAGE_SIZE = 20


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _encode_continuation(value: Mapping[str, object]) -> str:
    return base64.urlsafe_b64encode(
        _canonical_json(dict(value)).encode("utf-8")
    ).decode("ascii").rstrip("=")


def _decode_continuation(value: str) -> dict[str, object]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise McpExecutionError(
            "INVALID_ARGUMENT",
            "Continuation is malformed.",
        ) from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise McpExecutionError(
            "INVALID_ARGUMENT",
            "Continuation uses an unsupported format.",
        )
    return payload


def _normalized_goal(goal: str) -> str:
    return " ".join(goal.split())


def _goal_terms(goal: str) -> tuple[str, ...]:
    candidates = [match.group(0) for match in _GOAL_TOKEN.finditer(goal)]
    terms: list[str] = []
    for candidate in candidates:
        folded = candidate.casefold()
        if folded in _GOAL_STOP_WORDS or folded in {term.casefold() for term in terms}:
            continue
        terms.append(candidate)
        if len(terms) == 6:
            break
    if not terms and goal.strip():
        terms.append(goal.strip())
    return tuple(terms)


def _dedupe_by_ref(
    items: Iterable[Mapping[str, object]],
    *,
    limit: int,
) -> list[dict[str, object]]:
    returned: list[dict[str, object]] = []
    seen: set[str] = set()
    for source in items:
        item = dict(source)
        ref = str(item.get("ref") or "")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        returned.append(item)
        if len(returned) == limit:
            break
    return returned


def _member(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _bundle_node(bundle: Mapping[str, object]) -> dict[str, object]:
    """Restore the graph identity intentionally omitted by compact query bundles."""

    node = dict(_mapping(bundle.get("node")))
    graph_ref = str(node.get("graphRef") or "")
    node_ref = str(node.get("ref") or "")
    if not graph_ref and "/n/" in node_ref:
        graph_ref = node_ref.rsplit("/n/", 1)[0]
    if graph_ref:
        node["graphRef"] = graph_ref
    return node


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _error_from_exception(exc: Exception) -> McpExecutionError:
    if isinstance(exc, McpExecutionError):
        return exc
    if isinstance(exc, ApiProblem):
        code = str(exc.payload.get("code") or "")
        if code == "BLUEPRINT_ASSET_NOT_FOUND":
            return McpExecutionError("ASSET_NOT_FOUND", "Blueprint asset was not found.")
        if code in {"BLUEPRINT_ASSET_ID_INVALID", "BLUEPRINT_QUERY_INVALID"}:
            return McpExecutionError("INVALID_ARGUMENT", "A Blueprint query argument is invalid.")
        if code == "BLUEPRINT_EVIDENCE_STALE":
            return McpExecutionError("EVIDENCE_STALE", "Current Blueprint evidence is stale.")
        if code == "BLUEPRINT_EVIDENCE_NOT_AUTHORITATIVE":
            return McpExecutionError(
                "EVIDENCE_NOT_AUTHORITATIVE",
                "Current Blueprint evidence is not authoritative.",
            )
        if code in {
            "BLUEPRINT_EVIDENCE_NOT_FOUND",
            "BLUEPRINT_INTERPRETATION_NOT_FOUND",
        }:
            return McpExecutionError(
                "EVIDENCE_NOT_FOUND",
                "Current Blueprint evidence or interpretation was not found.",
            )

    raw_code = str(getattr(exc, "code", "") or "").strip().upper()
    if not raw_code:
        raw_code = str(exc).split(":", 1)[0].strip().upper()
    if any(marker in raw_code for marker in ("STALE_SOURCE", "STALE_EVIDENCE")):
        return McpExecutionError("EVIDENCE_STALE", "Current Blueprint evidence is stale.")
    if "NOT_AUTHORITATIVE" in raw_code or "NOT_AUTHORITY" in raw_code:
        return McpExecutionError(
            "EVIDENCE_NOT_AUTHORITATIVE",
            "Current Blueprint evidence is not authoritative.",
        )
    if "REVISION" in raw_code and ("MISMATCH" in raw_code or "STALE" in raw_code):
        return McpExecutionError(
            "EVIDENCE_REVISION_MISMATCH",
            "The Evidence reference does not belong to the current revision.",
        )
    if isinstance(exc, FileNotFoundError) or "NO_EVIDENCE" in raw_code or "MISSING" in raw_code:
        return McpExecutionError(
            "EVIDENCE_NOT_FOUND",
            "Current Blueprint evidence or interpretation was not found.",
        )
    return McpExecutionError(
        "INTERNAL_CONTRACT_ERROR",
        "The Blueprint query could not satisfy its public contract.",
    )


class BlueprintService:
    """Compose existing Interpretation and bounded Evidence services."""

    def __init__(self, capture_root: str | Path) -> None:
        self.capture_root = Path(capture_root)

    def list_assets(
        self,
        *,
        query: str = "",
        limit: int = 25,
        cursor: str = "",
    ) -> dict[str, object]:
        if len(query) > 128 or limit < 1 or limit > 100 or len(cursor) > 4096:
            raise McpExecutionError("INVALID_ARGUMENT", "Asset-list arguments are invalid.")
        try:
            route = blueprint_get_payload(
                "/api/blueprint/assets",
                urlencode({"q": query, "limit": limit, "cursor": cursor}),
                capture_root=self.capture_root,
            )
            if route is None:
                raise RuntimeError("Blueprint asset-list route was not matched")
            payload = {
                "schema": ASSET_LIST_SCHEMA,
                "items": list(route.payload.get("items") or []),
                "page": dict(route.payload.get("page") or {}),
            }
            assert_path_free(payload)
            return payload
        except Exception as exc:
            raise _error_from_exception(exc) from exc

    def health(self, *, asset: str) -> dict[str, object]:
        """Return the existing public, path-free Evidence health projection."""

        try:
            asset_name, _asset_dir = self._asset_dir(asset)
            route = blueprint_get_payload(
                f"/api/blueprint/assets/{quote(asset_name, safe='')}/evidence/health",
                "",
                capture_root=self.capture_root,
            )
            if route is None:
                raise RuntimeError("Blueprint evidence-health route was not matched")
            payload = dict(route.payload)
            assert_path_free(payload)
            return payload
        except Exception as exc:
            raise _error_from_exception(exc) from exc

    def get_context(
        self,
        *,
        asset: str,
        goal: str,
        graph_ref: str = "",
        seed_refs: Sequence[str] = (),
        max_hops: int = 1,
        max_nodes: int = 40,
        max_pins: int = 160,
        max_edges: int = 160,
        budget_tokens: int = 2400,
        continuation: str = "",
    ) -> dict[str, object]:
        normalized_goal = _normalized_goal(goal)
        normalized_seeds = tuple(dict.fromkeys(str(ref).strip() for ref in seed_refs))
        self._validate_context_arguments(
            goal=normalized_goal,
            graph_ref=graph_ref,
            seed_refs=normalized_seeds,
            max_hops=max_hops,
            max_nodes=max_nodes,
            max_pins=max_pins,
            max_edges=max_edges,
            budget_tokens=budget_tokens,
            continuation=continuation,
        )
        try:
            asset_name, asset_dir = self._asset_dir(asset)
            evidence_state, interpretation = self._load_bound_state(asset_dir)
            with open_resolved_asset_repository(evidence_state) as repository:
                identity = self._identity(
                    asset_name,
                    evidence_state,
                    interpretation,
                    repository,
                )
                signature = self._context_signature(
                    evidence_state=evidence_state,
                    goal=normalized_goal,
                    graph_ref=graph_ref,
                    seed_refs=normalized_seeds,
                    max_hops=max_hops,
                    max_nodes=max_nodes,
                    max_pins=max_pins,
                    max_edges=max_edges,
                    budget_tokens=budget_tokens,
                )
                offsets = self._continuation_offsets(
                    continuation,
                    revision_id=repository.revision_id,
                    signature=signature,
                )
                graph_summaries = repository.graph_summaries()
                search_items = self._search_goal(repository, normalized_goal)
                graph_targets, seeds = self._select_graphs_and_seeds(
                    repository=repository,
                    graph_summaries=graph_summaries,
                    search_items=search_items,
                    graph_ref=graph_ref,
                    seed_refs=normalized_seeds,
                )
                facts = self._facts(
                    interpretation,
                    {str(item["ref"]) for item in graph_targets},
                    normalized_goal,
                )
                for fact in facts:
                    for ref in fact.get("evidenceRefs", []):
                        if "/n/" in str(ref) and str(ref) not in seeds:
                            seeds.append(str(ref))
                        if len(seeds) == 10:
                            break
                    if len(seeds) == 10:
                        break
                nodes, pins, edges = self._graph_context_candidates(
                    repository,
                    seeds=seeds,
                    graph_refs={str(item["ref"]) for item in graph_targets},
                    max_hops=max_hops,
                    budget_tokens=budget_tokens,
                )
                gaps = self._gaps(
                    interpretation,
                    {str(item["ref"]) for item in graph_targets},
                )
                return self._context_page(
                    identity=identity,
                    freshness=evidence_state.freshness_status,
                    goal=normalized_goal,
                    graph_targets=graph_targets,
                    facts=facts,
                    nodes=nodes,
                    pins=pins,
                    edges=edges,
                    gaps=gaps,
                    offsets=offsets,
                    max_nodes=max_nodes,
                    max_pins=max_pins,
                    max_edges=max_edges,
                    budget_tokens=budget_tokens,
                    signature=signature,
                    revision_id=repository.revision_id,
                )
        except Exception as exc:
            raise _error_from_exception(exc) from exc

    def get_node(
        self,
        *,
        asset: str,
        node_ref: str,
        include_neighborhood: bool = True,
        max_hops: int = 1,
    ) -> dict[str, object]:
        if (
            not node_ref.startswith("bp://")
            or "/n/" not in node_ref
            or len(node_ref) > 4096
            or max_hops < 0
            or max_hops > 1
        ):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "nodeRef must be one exact bp:// node reference and maxHops must be 0 or 1.",
            )
        try:
            asset_name, asset_dir = self._asset_dir(asset)
            evidence_state, interpretation = self._load_bound_state(asset_dir)
            with open_resolved_asset_repository(evidence_state) as repository:
                self._require_current_ref(node_ref, repository)
                identity = self._identity(
                    asset_name,
                    evidence_state,
                    interpretation,
                    repository,
                )
                try:
                    entity = repository.query(
                        {
                            "operation": "entity",
                            "selector": {"ref": node_ref},
                            "budgetTokens": 1800,
                        }
                    )
                except KeyError as exc:
                    raise McpExecutionError(
                        "NODE_NOT_FOUND",
                        "The exact Blueprint node was not found.",
                    ) from exc
                if not entity.get("items"):
                    raise McpExecutionError(
                        "NODE_NOT_FOUND",
                        "The exact Blueprint node was not returned within its minimum budget.",
                    )
                exact_node = dict(entity["items"][0])
                if str(exact_node.get("kind")) != "node":
                    raise McpExecutionError(
                        "NODE_NOT_FOUND",
                        "The exact Evidence reference does not identify a node.",
                    )
                traversal = repository.query(
                    {
                        "operation": "neighborhood",
                        "selector": {"ref": node_ref},
                        "traversal": {
                            "maxHops": max_hops if include_neighborhood else 0,
                            "direction": "both",
                            "edgeKinds": ["exec", "data"],
                        },
                        "pageSize": 100,
                        "pinLimit": 100,
                        "edgeLimit": 100,
                        "budgetTokens": 6000,
                    }
                )
                bundles = [
                    item
                    for item in traversal.get("items", [])
                    if isinstance(item, Mapping)
                ]
                neighborhood = _dedupe_by_ref(
                    (
                        _bundle_node(bundle)
                        for bundle in bundles
                        if str(_bundle_node(bundle).get("ref") or "") != node_ref
                    ),
                    limit=99,
                )
                pins = self._bundle_pins(bundles, limit=400)
                edges = self._bundle_edges(bundles, limit=400)
                defaults = [
                    {
                        "pinRef": pin["ref"],
                        "nodeRef": pin.get("nodeRef", ""),
                        "name": pin.get("name", ""),
                        "value": pin.get("default"),
                    }
                    for pin in pins
                    if "default" in pin
                ]
                gap_result = repository.query(
                    {
                        "operation": "gaps",
                        "selector": {"ref": node_ref},
                        "pageSize": 100,
                        "budgetTokens": 2400,
                    }
                )
                result: dict[str, object] = {
                    "schema": NODE_SCHEMA,
                    "identity": identity,
                    "freshness": evidence_state.freshness_status,
                    "node": exact_node,
                    "neighborhood": neighborhood,
                    "pins": pins,
                    "edges": edges,
                    "defaults": defaults,
                    "gaps": list(gap_result.get("items") or []),
                    "truncated": bool(
                        traversal.get("page", {}).get("nextCursor")
                        or gap_result.get("page", {}).get("nextCursor")
                    ),
                }
                assert_path_free(result)
                return result
        except Exception as exc:
            raise _error_from_exception(exc) from exc

    @staticmethod
    def _validate_context_arguments(
        *,
        goal: str,
        graph_ref: str,
        seed_refs: Sequence[str],
        max_hops: int,
        max_nodes: int,
        max_pins: int,
        max_edges: int,
        budget_tokens: int,
        continuation: str,
    ) -> None:
        invalid = (
            not goal
            or len(goal) > 1000
            or (graph_ref and (not graph_ref.startswith("bp://") or "/g/" not in graph_ref))
            or len(graph_ref) > 4096
            or len(seed_refs) > 10
            or any(not ref.startswith("bp://") or len(ref) > 4096 for ref in seed_refs)
            or max_hops < 0
            or max_hops > 2
            or max_nodes < 1
            or max_nodes > 100
            or max_pins < 1
            or max_pins > 400
            or max_edges < 1
            or max_edges > 400
            or budget_tokens < 800
            or budget_tokens > 6000
            or len(continuation) > 4096
        )
        if invalid:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Blueprint context arguments are outside the Phase 1 bounds.",
            )

    def _asset_dir(self, asset: str) -> tuple[str, Path]:
        try:
            asset_name = _asset_identifier(asset)
            return asset_name, _resolve_asset(self.capture_root, asset_name)
        except Exception as exc:
            raise _error_from_exception(exc) from exc

    @staticmethod
    def _load_bound_state(
        asset_dir: Path,
    ) -> tuple[ResolvedEvidenceState, LoadedInterpretation]:
        evidence_state = resolve_asset_evidence_state(asset_dir)
        interpretation = load_current_interpretation(asset_dir)
        evidence_manifest = evidence_manifest_payload(evidence_state)
        expected_revision = str(interpretation.manifest.get("evidenceRevisionId") or "")
        expected_manifest = str(
            interpretation.manifest.get("evidenceManifestSha256") or ""
        )
        if (
            str(evidence_manifest.get("revisionId") or "") != expected_revision
            or str(evidence_state.manifest_sha256 or "") != expected_manifest
        ):
            raise McpExecutionError(
                "EVIDENCE_REVISION_MISMATCH",
                "Interpretation and Evidence bindings changed during the query.",
            )
        return evidence_state, interpretation

    @staticmethod
    def _identity(
        asset_name: str,
        evidence_state: ResolvedEvidenceState,
        interpretation: LoadedInterpretation,
        repository: EvidenceRepository,
    ) -> dict[str, object]:
        evidence_identity = repository.identity()
        interpretation_payload = interpretation.interpretation
        identity = {
            "asset": {
                "name": asset_name,
                "assetId": str(evidence_identity.get("asset_id") or ""),
                "objectPath": str(evidence_identity.get("object_path") or ""),
            },
            "evidence": {
                "revisionId": repository.revision_id,
                "manifestSha256": str(evidence_state.manifest_sha256 or ""),
                "pointerSha256": str(evidence_state.pointer_sha256 or ""),
                "sourceKind": evidence_state.source_kind,
                "releaseAuthority": evidence_state.release_authority,
            },
            "interpretation": {
                "revisionId": interpretation.revision_id,
                "manifestSha256": interpretation.manifest_sha256,
                "pointerSha256": interpretation.pointer_sha256,
                "semanticDigest": str(
                    interpretation_payload.get("semanticDigest") or ""
                ),
                "interpreterVersion": str(
                    interpretation_payload.get("interpreterVersion") or ""
                ),
                "schemaVersion": str(
                    interpretation_payload.get("schemaVersion")
                    or interpretation_payload.get("schema")
                    or ""
                ),
            },
        }
        assert_path_free(identity)
        return identity

    @staticmethod
    def _search_goal(
        repository: EvidenceRepository,
        goal: str,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        seen: set[str] = set()
        for term in _goal_terms(goal):
            response = repository.query(
                {
                    "operation": "search",
                    "query": term,
                    "kinds": ["graph", "node"],
                    "pageSize": 25,
                    "budgetTokens": 1600,
                }
            )
            for source in response.get("items", []):
                if not isinstance(source, Mapping):
                    continue
                ref = str(source.get("ref") or "")
                if ref and ref not in seen:
                    seen.add(ref)
                    results.append(dict(source))
        return results

    def _select_graphs_and_seeds(
        self,
        *,
        repository: EvidenceRepository,
        graph_summaries: Sequence[Mapping[str, object]],
        search_items: Sequence[Mapping[str, object]],
        graph_ref: str,
        seed_refs: Sequence[str],
    ) -> tuple[list[dict[str, object]], list[str]]:
        summaries = {
            str(item.get("ref") or ""): dict(item) for item in graph_summaries
        }
        selected_refs: list[str] = []
        seeds: list[str] = []
        if graph_ref:
            self._require_current_ref(graph_ref, repository)
            if graph_ref not in summaries:
                self._raise_graph_selection(graph_summaries)
            selected_refs.append(graph_ref)

        for seed_ref in seed_refs:
            self._require_current_ref(seed_ref, repository)
            try:
                response = repository.query(
                    {
                        "operation": "entity",
                        "selector": {"ref": seed_ref},
                        "budgetTokens": 1200,
                    }
                )
            except KeyError as exc:
                raise McpExecutionError(
                    "INVALID_ARGUMENT",
                    "A seedRef was not found in the current Evidence revision.",
                ) from exc
            item = _mapping((response.get("items") or [{}])[0])
            item_graph = str(item.get("graphRef") or "")
            if not item_graph:
                raise McpExecutionError(
                    "INVALID_ARGUMENT",
                    "Every seedRef must identify graph-local Evidence.",
                )
            if item_graph not in selected_refs:
                selected_refs.append(item_graph)
            seeds.append(seed_ref)

        matching_graphs: list[str] = []
        for item in search_items:
            item_graph = str(item.get("graphRef") or "")
            if item_graph and item_graph not in matching_graphs:
                matching_graphs.append(item_graph)
            if str(item.get("kind")) == "node" and str(item.get("ref") or "") not in seeds:
                seeds.append(str(item["ref"]))
        if not selected_refs:
            if len(matching_graphs) != 1:
                self._raise_graph_selection(
                    [summaries[ref] for ref in matching_graphs if ref in summaries]
                    or graph_summaries
                )
            selected_refs = matching_graphs
        if len(selected_refs) > 5:
            self._raise_graph_selection(
                [summaries[ref] for ref in selected_refs if ref in summaries]
            )
        selected = [self._graph_projection(summaries[ref]) for ref in selected_refs]
        selected_set = set(selected_refs)
        filtered_seeds = []
        for seed in seeds:
            item = next(
                (source for source in search_items if source.get("ref") == seed),
                None,
            )
            if item is None or str(item.get("graphRef") or "") in selected_set:
                if seed not in filtered_seeds:
                    filtered_seeds.append(seed)
            if len(filtered_seeds) == 10:
                break
        return selected, filtered_seeds

    @staticmethod
    def _graph_projection(item: Mapping[str, object]) -> dict[str, object]:
        return {
            "ref": str(item.get("ref") or ""),
            "name": str(item.get("name") or ""),
            "graphType": str(item.get("graph_type") or ""),
            "status": str(item.get("status") or ""),
            "confidence": str(item.get("confidence") or ""),
            "nodeCount": int(item.get("node_count") or 0),
            "pinCount": int(item.get("pin_count") or 0),
            "linkCount": int(item.get("link_count") or 0),
        }

    def _raise_graph_selection(
        self,
        candidates: Sequence[Mapping[str, object]],
    ) -> None:
        projected = [self._graph_projection(item) for item in candidates[:5]]
        raise McpExecutionError(
            "GRAPH_SELECTION_REQUIRED",
            "The goal does not identify exactly one Blueprint graph.",
            details={"candidates": projected},
        )

    @staticmethod
    def _facts(
        interpretation: LoadedInterpretation,
        graph_refs: set[str],
        goal: str,
    ) -> list[dict[str, object]]:
        payload = interpretation.interpretation
        raw = payload.get("statements")
        statements = raw if isinstance(raw, Sequence) else []
        terms = tuple(term.casefold() for term in _goal_terms(goal))

        def rank(item: Mapping[str, object]) -> tuple[int, int, str]:
            text = str(item.get("text") or "").casefold()
            relevance = 0 if any(term in text for term in terms) else 1
            return relevance, int(item.get("sourceOrder") or 0), str(item.get("id") or "")

        selected = [
            item
            for item in statements
            if isinstance(item, Mapping)
            and str(item.get("graphRef") or "") in graph_refs
        ]
        selected.sort(key=rank)
        return [
            {
                "id": str(item.get("id") or ""),
                "kind": str(item.get("kind") or ""),
                "text": str(item.get("text") or ""),
                "status": str(item.get("status") or ""),
                "graphRef": str(item.get("graphRef") or ""),
                "nodeRef": str(item.get("nodeRef") or ""),
                "evidenceRefs": list(item.get("evidenceRefs") or []),
                "gapRefs": list(item.get("gapRefs") or []),
            }
            for item in selected[:_MAX_FACT_CANDIDATES]
        ]

    @staticmethod
    def _gaps(
        interpretation: LoadedInterpretation,
        graph_refs: set[str],
    ) -> list[dict[str, object]]:
        raw = interpretation.gaps.get("items")
        gaps = raw if isinstance(raw, Sequence) else []
        selected = [
            item
            for item in gaps
            if isinstance(item, Mapping)
            and str(item.get("graphRef") or "") in graph_refs
        ]
        selected.sort(key=lambda item: (str(item.get("status")), str(item.get("id"))))
        return [
            {
                "id": str(item.get("id") or ""),
                "code": str(item.get("code") or ""),
                "status": str(item.get("status") or ""),
                "detail": str(item.get("detail") or ""),
                "graphRef": str(item.get("graphRef") or ""),
                "nodeRef": str(item.get("nodeRef") or ""),
                "pinRef": str(item.get("pinRef") or ""),
                "evidenceRefs": list(item.get("evidenceRefs") or []),
            }
            for item in selected[:_MAX_GAP_CANDIDATES]
        ]

    def _graph_context_candidates(
        self,
        repository: EvidenceRepository,
        *,
        seeds: Sequence[str],
        graph_refs: set[str],
        max_hops: int,
        budget_tokens: int,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
        bundles: list[Mapping[str, object]] = []
        for seed in seeds[:10]:
            response = repository.query(
                {
                    "operation": "neighborhood",
                    "selector": {"ref": seed},
                    "traversal": {
                        "maxHops": max_hops,
                        "direction": "both",
                        "edgeKinds": ["exec", "data"],
                    },
                    "pageSize": 100,
                    "pinLimit": 100,
                    "edgeLimit": 100,
                    "budgetTokens": max(1200, min(6000, budget_tokens)),
                }
            )
            for item in response.get("items", []):
                if not isinstance(item, Mapping):
                    continue
                node = _bundle_node(item)
                if str(node.get("graphRef") or "") not in graph_refs:
                    continue
                normalized = dict(item)
                normalized["node"] = node
                bundles.append(normalized)
        nodes = _dedupe_by_ref(
            (_bundle_node(bundle) for bundle in bundles),
            limit=_MAX_NODE_CANDIDATES,
        )
        pins = self._bundle_pins(bundles, limit=_MAX_PIN_CANDIDATES)
        edges = self._bundle_edges(bundles, limit=_MAX_EDGE_CANDIDATES)
        return nodes, pins, edges

    @staticmethod
    def _bundle_pins(
        bundles: Sequence[Mapping[str, object]],
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for bundle in bundles:
            node = _bundle_node(bundle)
            node_ref = str(node.get("ref") or "")
            graph_ref = str(node.get("graphRef") or "")
            for source in bundle.get("pins", []):
                if isinstance(source, Mapping):
                    item = dict(source)
                    item["nodeRef"] = node_ref
                    item["graphRef"] = graph_ref
                    candidates.append(item)
        return _dedupe_by_ref(candidates, limit=limit)

    @staticmethod
    def _bundle_edges(
        bundles: Sequence[Mapping[str, object]],
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        return _dedupe_by_ref(
            (
                source
                for bundle in bundles
                for source in bundle.get("edges", [])
                if isinstance(source, Mapping)
            ),
            limit=limit,
        )

    @staticmethod
    def _context_signature(
        *,
        evidence_state: ResolvedEvidenceState,
        goal: str,
        graph_ref: str,
        seed_refs: Sequence[str],
        max_hops: int,
        max_nodes: int,
        max_pins: int,
        max_edges: int,
        budget_tokens: int,
    ) -> str:
        manifest = evidence_manifest_payload(evidence_state)
        return _sha256(
            {
                "schema": "blueprint-to-code.mcp-blueprint-context-query/v1",
                "evidenceRevisionId": str(manifest.get("revisionId") or ""),
                "evidenceManifestSha256": str(evidence_state.manifest_sha256 or ""),
                "goal": goal.casefold(),
                "graphRef": graph_ref,
                "seedRefs": list(seed_refs),
                "maxHops": max_hops,
                "maxNodes": max_nodes,
                "maxPins": max_pins,
                "maxEdges": max_edges,
                "budgetTokens": budget_tokens,
            }
        )

    @staticmethod
    def _continuation_offsets(
        continuation: str,
        *,
        revision_id: str,
        signature: str,
    ) -> dict[str, int]:
        names = ("facts", "nodes", "pins", "edges", "gaps")
        if not continuation:
            return {name: 0 for name in names}
        payload = _decode_continuation(continuation)
        if str(payload.get("r") or "") != revision_id:
            raise McpExecutionError(
                "EVIDENCE_REVISION_MISMATCH",
                "Continuation belongs to another Evidence revision.",
            )
        if str(payload.get("q") or "") != signature:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Continuation belongs to another Blueprint context query.",
            )
        raw_offsets = payload.get("o")
        if not isinstance(raw_offsets, Mapping) or set(raw_offsets) != set(names):
            raise McpExecutionError("INVALID_ARGUMENT", "Continuation offsets are invalid.")
        try:
            offsets = {name: int(raw_offsets[name]) for name in names}
        except (TypeError, ValueError) as exc:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Continuation offsets are invalid.",
            ) from exc
        if any(value < 0 for value in offsets.values()):
            raise McpExecutionError("INVALID_ARGUMENT", "Continuation offsets are invalid.")
        return offsets

    def _context_page(
        self,
        *,
        identity: dict[str, object],
        freshness: str,
        goal: str,
        graph_targets: list[dict[str, object]],
        facts: list[dict[str, object]],
        nodes: list[dict[str, object]],
        pins: list[dict[str, object]],
        edges: list[dict[str, object]],
        gaps: list[dict[str, object]],
        offsets: dict[str, int],
        max_nodes: int,
        max_pins: int,
        max_edges: int,
        budget_tokens: int,
        signature: str,
        revision_id: str,
    ) -> dict[str, object]:
        candidates = {
            "facts": facts,
            "nodes": nodes,
            "pins": pins,
            "edges": edges,
            "gaps": gaps,
        }
        limits = {
            "facts": _FACT_PAGE_SIZE,
            "nodes": max_nodes,
            "pins": max_pins,
            "edges": max_edges,
            "gaps": _GAP_PAGE_SIZE,
        }
        if any(offsets[name] > len(values) for name, values in candidates.items()):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Continuation offsets exceed the bounded result set.",
            )
        page = {
            name: values[offsets[name] : offsets[name] + limits[name]]
            for name, values in candidates.items()
        }

        def build() -> dict[str, object]:
            next_offsets = {
                name: offsets[name] + len(page[name]) for name in candidates
            }
            remaining = {
                name: max(0, len(candidates[name]) - next_offsets[name])
                for name in candidates
            }
            truncated = any(remaining.values())
            token = (
                _encode_continuation(
                    {"v": 1, "r": revision_id, "q": signature, "o": next_offsets}
                )
                if truncated
                else ""
            )
            return {
                "schema": CONTEXT_SCHEMA,
                "identity": identity,
                "freshness": freshness,
                "goal": goal,
                "graphTargets": graph_targets,
                "facts": page["facts"],
                "nodes": page["nodes"],
                "pins": page["pins"],
                "edges": page["edges"],
                "gaps": page["gaps"],
                "omitted": {
                    "nodes": remaining["nodes"],
                    "pins": remaining["pins"],
                    "edges": remaining["edges"],
                },
                "truncated": truncated,
                "querySignature": signature,
                "continuation": token,
            }

        response = build()
        # Preserve the directly requested graph slice before secondary
        # interpretation prose.  Large gap records otherwise starve pins from
        # every continuation page at the default budget.
        trim_order = ("edges", "gaps", "facts", "pins", "nodes")
        while estimate_tokens(_canonical_json(response)) > budget_tokens:
            removed = False
            for name in trim_order:
                if page[name]:
                    page[name].pop()
                    removed = True
                    response = build()
                    break
            if not removed:
                raise McpExecutionError(
                    "RESULT_BUDGET_EXCEEDED",
                    "The minimum Blueprint context response exceeds budgetTokens.",
                    details={"minimumBudgetTokens": estimate_tokens(_canonical_json(response))},
                )
        assert_path_free(response)
        return response

    @staticmethod
    def _require_current_ref(ref: str, repository: EvidenceRepository) -> None:
        parsed = urlsplit(ref)
        if parsed.scheme != "bp" or "@" not in parsed.netloc:
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "Evidence references must use the exact bp:// form.",
            )
        asset_id, revision_id = parsed.netloc.split("@", 1)
        if asset_id != repository.asset_id or revision_id != repository.revision_id:
            raise McpExecutionError(
                "EVIDENCE_REVISION_MISMATCH",
                "The Evidence reference does not belong to the current revision.",
            )


__all__ = [
    "ASSET_LIST_SCHEMA",
    "CONTEXT_SCHEMA",
    "NODE_SCHEMA",
    "BlueprintService",
]
