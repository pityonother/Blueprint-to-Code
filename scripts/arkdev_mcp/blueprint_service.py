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
from blueprint_translator.evidence_policy import (
    EvidenceDecision,
    EvidencePolicyError,
    require_evidence,
)
from blueprint_translator.evidence_schema import parse_evidence_ref
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
_MAX_DEFAULT_FACT_CANDIDATES = 20
_MAX_DEFAULT_ENTITY_BUDGET = 8000
_MAX_GAP_CANDIDATES = 100
_MAX_NODE_CANDIDATES = 100
_MAX_PIN_CANDIDATES = 400
_MAX_EDGE_CANDIDATES = 400
_FACT_PAGE_SIZE = 20
_GAP_PAGE_SIZE = 20
_OBJECT_DEFAULT_TYPES = frozenset(
    {
        "ClassProperty",
        "ObjectProperty",
        "SoftClassProperty",
        "SoftObjectProperty",
    }
)


def _is_context_seed_ref(value: object) -> bool:
    try:
        parsed = parse_evidence_ref(str(value or ""))
    except ValueError:
        return False
    return parsed.get("kind") in {"node", "pin"}


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


def _is_exact_default_name(name: object, goal: str) -> bool:
    folded = str(name or "").casefold()
    return bool(
        folded
        and (
            folded == goal.casefold()
            or folded in {term.casefold() for term in _goal_terms(goal)}
        )
    )


def _project_asset_field_value(
    value: object,
    *,
    field_name: str = "value",
) -> tuple[object, bool]:
    """Keep Unreal object identities typed while withholding machine paths."""

    if isinstance(value, str):
        try:
            assert_path_free({field_name: value})
        except McpExecutionError:
            try:
                assert_path_free({"objectPath": value})
            except McpExecutionError:
                return {"withheldByPathPolicy": True}, False
            return {"objectPath": value}, True
        return value, True
    if isinstance(value, Mapping):
        projected: dict[str, object] = {}
        complete = True
        for key, item in value.items():
            projected_item, item_complete = _project_asset_field_value(
                item,
                field_name=str(key),
            )
            projected[str(key)] = projected_item
            complete = complete and item_complete
        return projected, complete
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        projected_items: list[object] = []
        complete = True
        for item in value:
            projected_item, item_complete = _project_asset_field_value(
                item,
                field_name=field_name,
            )
            projected_items.append(projected_item)
            complete = complete and item_complete
        return projected_items, complete
    return value, True


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


def _bounded_coverage(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    projection: dict[str, int] = {}
    for key in ("available", "returned", "unresolved", "unparsedContainers"):
        if key not in value:
            continue
        raw = value.get(key)
        if isinstance(raw, bool):
            return {}
        try:
            normalized = int(raw)
        except (TypeError, ValueError):
            return {}
        if normalized < 0:
            return {}
        projection[key] = normalized
    if not {"available", "returned"}.issubset(projection):
        return {}
    if projection["returned"] > projection["available"]:
        return {}
    return projection


def _coverage_is_complete(
    coverage: Mapping[str, int],
    *,
    represented: int,
) -> bool:
    return bool(
        coverage
        and coverage.get("returned") == coverage.get("available")
        and represented == coverage.get("returned")
        and not coverage.get("unresolved", 0)
        and not coverage.get("unparsedContainers", 0)
    )


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
    if isinstance(exc, EvidencePolicyError):
        code = exc.code
        if code == "EVIDENCE_STALE":
            return McpExecutionError(
                "EVIDENCE_STALE",
                "Current Blueprint evidence is stale.",
            )
        if code == "EVIDENCE_EMPTY":
            return McpExecutionError(
                "EVIDENCE_NOT_AUTHORITATIVE",
                "Current Blueprint evidence identifies the asset but has no semantic facts.",
            )
        if code in {
            "SOURCE_UNAVAILABLE",
            "SOURCE_KIND_NOT_CURRENT",
            "RELEASE_AUTHORITY_MISSING",
            "MIGRATION_REQUIRED",
            "MANIFEST_BINDING_MISSING",
            "POINTER_BINDING_MISSING",
        }:
            return McpExecutionError(
                "EVIDENCE_NOT_AUTHORITATIVE",
                "Current Blueprint evidence is not authoritative.",
            )
        return McpExecutionError(
            "EVIDENCE_NOT_AUTHORITATIVE",
            "Current Blueprint evidence has an invalid binding.",
        )
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

    def get_task_authority(self, *, asset: str) -> dict[str, object]:
        """Return current path-free authority and graph identities for Task metadata."""

        try:
            asset_name, asset_dir = self._asset_dir(asset)
            evidence_state, interpretation, decision = self._load_bound_state(asset_dir)
            with open_resolved_asset_repository(
                evidence_state,
                purpose="formal_query",
            ) as repository:
                identity = self._identity(
                    asset_name,
                    evidence_state,
                    interpretation,
                    repository,
                    decision,
                )
                evidence = dict(identity["evidence"])
                asset_identity = dict(identity["asset"])
                payload: dict[str, object] = {
                    "name": asset_identity["name"],
                    "assetId": asset_identity["assetId"],
                    "objectPath": asset_identity["objectPath"],
                    "evidenceRevisionId": evidence["revisionId"],
                    "evidenceManifestSha256": evidence["manifestSha256"],
                    "freshness": evidence_state.freshness_status,
                    "graphTargets": [
                        self._graph_projection(item)
                        for item in repository.graph_summaries()
                    ],
                }
                assert_path_free(payload)
                return payload
        except Exception as exc:
            raise _error_from_exception(exc) from exc

    def get_node_binding_locators(
        self,
        *,
        asset: str,
        graph_ref: str,
        node_refs: Sequence[str],
    ) -> dict[str, object]:
        """Project exact live-lookup locators from one current Evidence revision.

        This is an application service for the repository-external request
        builder, not an MCP tool.  It opens one immutable Evidence generation
        and never returns its database or source paths.
        """

        if isinstance(node_refs, (str, bytes)):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "nodeRefs must be an array of exact Blueprint node references.",
            )
        requested = tuple(str(ref).strip() for ref in node_refs)
        if (
            not graph_ref.startswith("bp://")
            or "/g/" not in graph_ref
            or len(graph_ref) > 4096
            or not 1 <= len(requested) <= 12
            or any(not ref or len(ref) > 4096 for ref in requested)
        ):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "One exact graphRef and between 1 and 12 nodeRefs are required.",
            )
        if len(requested) != len(set(requested)):
            raise McpExecutionError(
                "INVALID_ARGUMENT",
                "nodeRefs must not contain duplicates.",
            )
        try:
            asset_name, asset_dir = self._asset_dir(asset)
            evidence_state, interpretation = self._load_bound_state(asset_dir)
            if evidence_state.freshness_status != "FRESH":
                raise McpExecutionError(
                    "EVIDENCE_STALE",
                    "Current Blueprint evidence is stale.",
                )
            manifest_sha256 = str(evidence_state.manifest_sha256 or "")
            with open_resolved_asset_repository(evidence_state) as repository:
                self._require_current_ref(graph_ref, repository)
                graph = next(
                    (
                        item
                        for item in repository.graph_summaries()
                        if str(item.get("ref") or "") == graph_ref
                    ),
                    None,
                )
                if graph is None:
                    raise McpExecutionError(
                        "INVALID_ARGUMENT",
                        "graphRef is not an exact graph in current Evidence.",
                    )
                for node_ref in requested:
                    self._require_current_ref(node_ref, repository)
                    if not node_ref.startswith(f"{graph_ref}/n/"):
                        raise McpExecutionError(
                            "INVALID_ARGUMENT",
                            "Every nodeRef must belong to the exact target graph.",
                        )
                try:
                    locators = repository.node_binding_locators(
                        graph_ref=graph_ref,
                        node_refs=requested,
                    )
                except KeyError as exc:
                    raise McpExecutionError(
                        "NODE_NOT_FOUND",
                        "One or more exact Blueprint nodes were not found.",
                    ) from exc
                except ValueError as exc:
                    if str(exc) == "NODE_GUID_NOT_AVAILABLE":
                        raise ValueError("NODE_GUID_NOT_AVAILABLE") from exc
                    raise
                if any(not str(locator.get("nodeGuid") or "") for locator in locators):
                    raise ValueError("NODE_GUID_NOT_AVAILABLE")
                if (
                    re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
                    or not evidence_state.release_authority
                    or evidence_state.migration_required
                ):
                    raise McpExecutionError(
                        "EVIDENCE_NOT_AUTHORITATIVE",
                        "Current Blueprint evidence has no authoritative manifest binding.",
                    )
                identity = self._identity(
                    asset_name,
                    evidence_state,
                    interpretation,
                    repository,
                )
                asset_identity = dict(identity["asset"])
                payload: dict[str, object] = {
                    "asset": {
                        "name": asset_identity["name"],
                        "objectPath": asset_identity["objectPath"],
                        "assetId": asset_identity["assetId"],
                        "evidenceRevisionId": repository.revision_id,
                        "evidenceManifestSha256": manifest_sha256,
                        "freshnessStatus": evidence_state.freshness_status,
                    },
                    "graph": {
                        "name": str(graph.get("name") or ""),
                        "graphRef": graph_ref,
                    },
                    "nodes": locators,
                }
                assert_path_free(payload)
                return payload
        except ValueError:
            # This non-MCP application service preserves builder-only fail-closed
            # codes without expanding the stable public MCP error enum.
            raise
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
            evidence_state, interpretation, decision = self._load_bound_state(asset_dir)
            with open_resolved_asset_repository(
                evidence_state,
                purpose="formal_query",
            ) as repository:
                identity = self._identity(
                    asset_name,
                    evidence_state,
                    interpretation,
                    repository,
                    decision,
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
                    goal=normalized_goal,
                    graph_ref=graph_ref,
                    seed_refs=normalized_seeds,
                )
                facts = self._merge_facts(
                    self._default_facts(
                        repository,
                        search_items,
                        normalized_goal,
                    ),
                    self._facts(
                        interpretation,
                        {str(item["ref"]) for item in graph_targets},
                        normalized_goal,
                    ),
                )
                for fact in facts:
                    for ref in fact.get("evidenceRefs", []):
                        if _is_context_seed_ref(ref) and str(ref) not in seeds:
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
            evidence_state, interpretation, decision = self._load_bound_state(asset_dir)
            with open_resolved_asset_repository(
                evidence_state,
                purpose="formal_query",
            ) as repository:
                self._require_current_ref(node_ref, repository)
                identity = self._identity(
                    asset_name,
                    evidence_state,
                    interpretation,
                    repository,
                    decision,
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
    ) -> tuple[ResolvedEvidenceState, LoadedInterpretation, EvidenceDecision]:
        evidence_state = resolve_asset_evidence_state(asset_dir, allow_stale=True)
        decision = require_evidence(evidence_state, purpose="formal_query")
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
        return evidence_state, interpretation, decision

    @staticmethod
    def _identity(
        asset_name: str,
        evidence_state: ResolvedEvidenceState,
        interpretation: LoadedInterpretation,
        repository: EvidenceRepository,
        decision: EvidenceDecision,
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
                "decision": {
                    "reasonCode": decision.reason_code,
                    "reasonCodes": list(decision.reason_codes),
                    "bindingDigest": decision.binding_digest,
                    "evidenceAvailability": decision.evidence_availability,
                    "statusZh": decision.public_status_zh,
                    "nonUpgradeableGaps": list(decision.non_upgradeable_gaps),
                },
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
        searches: list[tuple[str, list[str]]] = []
        normalized_goal = _normalized_goal(goal)
        if normalized_goal:
            # A property name may contain spaces.  Query the complete phrase
            # before its individual terms so the exact row cannot be pushed
            # beyond a bounded term page by many fuzzy matches.
            searches.append((normalized_goal, ["default", "asset_field"]))
        for term in _goal_terms(goal):
            searches.extend(
                (
                    (term, ["graph", "node"]),
                    (term, ["default", "asset_field"]),
                )
            )

        unique_searches: list[tuple[str, list[str]]] = []
        seen_searches: set[tuple[str, tuple[str, ...]]] = set()
        for query, kinds in searches:
            identity = (query.casefold(), tuple(kinds))
            if identity in seen_searches:
                continue
            seen_searches.add(identity)
            unique_searches.append((query, kinds))

        for query, kinds in unique_searches:
            response = repository.query(
                {
                    "operation": "search",
                    "query": query,
                    "kinds": kinds,
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
        goal: str,
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
            default_matches = [
                item
                for item in search_items
                if str(item.get("kind") or "") in {"default", "asset_field"}
            ]
            exact_default_match = any(
                _is_exact_default_name(item.get("name"), goal)
                for item in default_matches
            )
            if len(matching_graphs) == 1:
                selected_refs = matching_graphs
            elif exact_default_match:
                selected_refs = []
            else:
                self._raise_graph_selection(
                    [summaries[ref] for ref in matching_graphs if ref in summaries]
                    or graph_summaries
                )
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
    def _default_entity_item(
        repository: EvidenceRepository,
        ref: str,
        expected_kind: str = "default",
    ) -> dict[str, object]:
        budget = 1200
        for _attempt in range(4):
            response = repository.query(
                {
                    "operation": "entity",
                    "selector": {"ref": ref},
                    "valueChars": 400,
                    "propertyLimit": 0,
                    "observationLimit": 0,
                    "budgetTokens": budget,
                }
            )
            items = response.get("items")
            if (
                isinstance(items, Sequence)
                and not isinstance(items, (str, bytes))
                and items
            ):
                item = _mapping(items[0])
                if (
                    len(items) != 1
                    or str(item.get("kind") or "") != expected_kind
                    or str(item.get("ref") or "") != ref
                ):
                    raise McpExecutionError(
                        "INTERNAL_CONTRACT_ERROR",
                        "The exact field Evidence entity did not match "
                        "the requested reference.",
                    )
                return item

            coverage = _mapping(response.get("coverage"))
            if not (
                int(coverage.get("requested") or 0) == 1
                and int(coverage.get("returned") or 0) == 0
            ):
                break
            next_queries = response.get("nextQueries")
            suggestions = (
                [item for item in next_queries if isinstance(item, Mapping)]
                if isinstance(next_queries, Sequence)
                and not isinstance(next_queries, (str, bytes))
                else []
            )
            suggested_budget = 0
            for suggestion in suggestions:
                if str(suggestion.get("operation") or "") != "entity":
                    continue
                selector = _mapping(suggestion.get("selector"))
                if str(selector.get("ref") or "") != ref:
                    continue
                try:
                    suggested_budget = int(suggestion.get("budgetTokens") or 0)
                except (TypeError, ValueError):
                    suggested_budget = 0
                break
            if suggested_budget <= budget:
                break
            budget = min(suggested_budget, _MAX_DEFAULT_ENTITY_BUDGET)

        raise McpExecutionError(
            "RESULT_BUDGET_EXCEEDED",
            "The exact class-default Evidence entity exceeds its bounded query budget.",
            details={"maximumEvidenceBudgetTokens": _MAX_DEFAULT_ENTITY_BUDGET},
        )

    @staticmethod
    def _default_facts(
        repository: EvidenceRepository,
        search_items: Sequence[Mapping[str, object]],
        goal: str,
    ) -> list[dict[str, object]]:
        goal_terms = {term.casefold() for term in _goal_terms(goal)}
        goal_folded = goal.casefold()
        candidates = _dedupe_by_ref(
            (
                item
                for item in search_items
                if str(item.get("kind") or "") in {"default", "asset_field"}
                and _is_exact_default_name(item.get("name"), goal)
            ),
            limit=_MAX_DEFAULT_FACT_CANDIDATES,
        )

        def rank(item: Mapping[str, object]) -> tuple[int, str, str]:
            name = str(item.get("name") or "")
            folded = name.casefold()
            if folded == goal_folded:
                relevance = 0
            elif folded in goal_terms:
                relevance = 1
            elif any(term in folded for term in goal_terms):
                relevance = 2
            else:
                relevance = 3
            return relevance, folded, str(item.get("ref") or "")

        candidates.sort(key=rank)
        facts: list[dict[str, object]] = []
        for candidate in candidates[:_MAX_DEFAULT_FACT_CANDIDATES]:
            ref = str(candidate.get("ref") or "")
            if not ref:
                continue
            candidate_kind = str(candidate.get("kind") or "")
            item = BlueprintService._default_entity_item(
                repository,
                ref,
                candidate_kind,
            )
            if str(item.get("kind") or "") != candidate_kind:
                continue
            name = str(item.get("name") or "")
            type_name = str(item.get("typeName") or "")
            source_status = str(item.get("valueStatus") or "NOT_RECOVERED")
            source_value_usable = (
                item.get("valueUsable") is True and source_status == "CONFIRMED"
            )
            fact: dict[str, object] = {
                "id": ref,
                "kind": (
                    "ASSET_FIELD"
                    if candidate_kind == "asset_field"
                    else "CLASS_DEFAULT"
                ),
                "text": "",
                "status": source_status,
                "sourceValueStatus": source_status,
                "graphRef": "",
                "nodeRef": "",
                "evidenceRefs": [ref],
                "gapRefs": [],
                "name": name,
                "typeName": type_name,
                "confidence": str(item.get("confidence") or ""),
                "valueUsable": False,
            }
            parse = item.get("parse")
            if parse is not None:
                candidate_fact = {**fact, "parse": parse}
                try:
                    assert_path_free(candidate_fact)
                except McpExecutionError:
                    pass
                else:
                    fact["parse"] = parse

            resolved_name = item.get("resolvedObjectName")
            if isinstance(resolved_name, str) and resolved_name:
                typed_candidate = {**fact, "resolvedObjectPath": resolved_name}
                try:
                    assert_path_free(typed_candidate)
                except McpExecutionError:
                    name_candidate = {**fact, "resolvedObjectName": resolved_name}
                    try:
                        assert_path_free(name_candidate)
                    except McpExecutionError:
                        pass
                    else:
                        fact["resolvedObjectName"] = resolved_name
                else:
                    fact["resolvedObjectPath"] = resolved_name

            raw_resolved_names = item.get("resolvedObjectNames")
            if isinstance(raw_resolved_names, Sequence) and not isinstance(
                raw_resolved_names, (str, bytes)
            ):
                resolved_paths: list[str | None] = []
                resolved_names: list[str | None] = []
                represented_names = 0
                for raw_name in raw_resolved_names:
                    name_value = str(raw_name or "")
                    if not name_value:
                        resolved_paths.append(None)
                        resolved_names.append(None)
                        represented_names += 1
                        continue
                    try:
                        assert_path_free({"resolvedObjectPath": name_value})
                    except McpExecutionError:
                        try:
                            assert_path_free({"resolvedObjectName": name_value})
                        except McpExecutionError:
                            resolved_paths.append(None)
                            resolved_names.append(None)
                            continue
                        resolved_paths.append(None)
                        resolved_names.append(name_value)
                    else:
                        resolved_paths.append(name_value)
                        resolved_names.append(None)
                    represented_names += 1
                if any(value is not None for value in resolved_paths):
                    fact["resolvedObjectPaths"] = resolved_paths
                if any(value is not None for value in resolved_names):
                    fact["resolvedObjectNames"] = resolved_names

                coverage = _bounded_coverage(item.get("resolvedObjectCoverage"))
                if coverage:
                    fact["resolvedObjectCoverage"] = coverage
                    fact["resolvedObjectIdentityComplete"] = (
                        _coverage_is_complete(
                            coverage,
                            represented=represented_names,
                        )
                    )

            raw_resolved_fields = item.get("resolvedObjectFields")
            if isinstance(raw_resolved_fields, Sequence) and not isinstance(
                raw_resolved_fields, (str, bytes)
            ):
                resolved_fields: list[dict[str, object]] = []
                for raw_field in raw_resolved_fields:
                    if not isinstance(raw_field, Mapping):
                        continue
                    field: dict[str, object] = {
                        "elementIndex": int(raw_field.get("elementIndex") or 0),
                        "propertyIndex": int(raw_field.get("propertyIndex") or 0),
                        "propertyName": str(raw_field.get("propertyName") or ""),
                    }
                    raw_value_path = raw_field.get("valuePath")
                    if isinstance(raw_value_path, Sequence) and not isinstance(
                        raw_value_path,
                        (str, bytes),
                    ):
                        field["valuePath"] = list(raw_value_path)
                    name_value = str(raw_field.get("name") or "")
                    if not name_value:
                        continue
                    try:
                        assert_path_free({**field, "objectPath": name_value})
                    except McpExecutionError:
                        field["resolvedObjectName"] = name_value
                    else:
                        field["objectPath"] = name_value
                    try:
                        assert_path_free(field)
                    except McpExecutionError:
                        continue
                    resolved_fields.append(field)
                if resolved_fields:
                    fact["resolvedObjectFields"] = resolved_fields

                field_coverage = _bounded_coverage(
                    item.get("resolvedObjectFieldCoverage")
                )
                if field_coverage:
                    fact["resolvedObjectFieldCoverage"] = field_coverage
                    fact["resolvedObjectFieldIdentityComplete"] = (
                        _coverage_is_complete(
                            field_coverage,
                            represented=len(resolved_fields),
                        )
                    )

            def expose_value(value: object) -> bool:
                complete = True
                if candidate_kind == "asset_field":
                    value, complete = _project_asset_field_value(value)
                candidate_fact = {**fact, "value": value}
                try:
                    assert_path_free(candidate_fact)
                except McpExecutionError:
                    typed_object_path = {
                        **fact,
                        "objectPath": value,
                    }
                    if isinstance(value, str) and type_name in _OBJECT_DEFAULT_TYPES:
                        try:
                            assert_path_free(typed_object_path)
                        except McpExecutionError:
                            fact["valueExposure"] = "WITHHELD_BY_PATH_POLICY"
                        else:
                            fact["objectPath"] = value
                            fact["valueExposure"] = "OBJECT_PATH"
                            return True
                    else:
                        fact["valueExposure"] = "WITHHELD_BY_PATH_POLICY"
                else:
                    fact["value"] = value
                    fact["valueExposure"] = "RETURNED"
                    return complete
                return False

            value_returned = False
            if "value" in item:
                value_returned = expose_value(item["value"])
            elif "valueJsonPage" in item:
                coverage = _mapping(item.get("valueCoverage"))
                complete = (
                    int(coverage.get("offset") or 0) == 0
                    and int(coverage.get("returnedChars") or 0)
                    == int(coverage.get("availableChars") or 0)
                )
                if complete:
                    try:
                        decoded_value = json.loads(str(item["valueJsonPage"]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        fact["valueExposure"] = "NOT_RETURNED"
                    else:
                        value_returned = expose_value(decoded_value)
                else:
                    fact["valueCoverage"] = {
                        "availableChars": int(coverage.get("availableChars") or 0),
                        "returnedChars": int(coverage.get("returnedChars") or 0),
                    }
                    fact["valueExposure"] = "AVAILABLE_NOT_RETURNED"
            if not value_returned and "valueExposure" not in fact:
                fact["valueExposure"] = "NOT_RETURNED"
            if value_returned:
                fact["valueUsable"] = source_value_usable
            else:
                fact["valueUsable"] = False
                if source_status == "CONFIRMED":
                    fact["status"] = "NOT_RECOVERED"
            label = (
                "Asset instance field"
                if candidate_kind == "asset_field"
                else "Class default"
            )
            fact["text"] = f"{label} {name} value has status {fact['status']}."
            assert_path_free(fact)
            facts.append(fact)
        return facts

    @staticmethod
    def _merge_facts(
        *groups: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = []
        seen: set[str] = set()
        for group in groups:
            for source in group:
                item = dict(source)
                identity = str(item.get("id") or "")
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                merged.append(item)
                if len(merged) == _MAX_FACT_CANDIDATES:
                    return merged
        return merged

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
        trim_order = (
            "edges",
            "gaps",
            "secondaryFacts",
            "pins",
            "nodes",
            "defaultFacts",
        )
        while estimate_tokens(_canonical_json(response)) > budget_tokens:
            removed = False
            for name in trim_order:
                if name in {"secondaryFacts", "defaultFacts"}:
                    fact_index = next(
                        (
                            index
                            for index in range(len(page["facts"]) - 1, -1, -1)
                            if (
                                page["facts"][index].get("kind")
                                == "CLASS_DEFAULT"
                            )
                            == (name == "defaultFacts")
                        ),
                        None,
                    )
                    if fact_index is not None:
                        page["facts"].pop(fact_index)
                        removed = True
                        response = build()
                        break
                    continue
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
        if response["truncated"] and not any(page.values()):
            minimum_progress_tokens: list[int] = []
            for name, values in candidates.items():
                offset = offsets[name]
                if offset >= len(values):
                    continue
                page[name].append(values[offset])
                minimum_progress_tokens.append(
                    estimate_tokens(_canonical_json(build()))
                )
                page[name].pop()
            raise McpExecutionError(
                "RESULT_BUDGET_EXCEEDED",
                "The Blueprint context budget cannot return any result item.",
                details={
                    "minimumBudgetTokens": min(minimum_progress_tokens)
                    if minimum_progress_tokens
                    else budget_tokens + 1
                },
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
