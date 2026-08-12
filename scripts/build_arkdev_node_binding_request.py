"""Build a path-free exact-node binding request from current Evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.blueprint_service import BlueprintService  # noqa: E402
from arkdev_mcp.contracts import McpExecutionError  # noqa: E402
from arkdev_mcp.tasking.plan_service import PlanService  # noqa: E402
from arkdev_mcp.tasking.store import TaskStore  # noqa: E402
from arkdev_mcp.tasking.task_service import TaskService  # noqa: E402
from arkdev_scripting_probe.contracts import (  # noqa: E402
    NODE_BINDING_REQUEST_SCHEMA,
    assert_path_free,
    attach_semantic_digest,
    canonical_json,
)


REQUEST_SCHEMA = NODE_BINDING_REQUEST_SCHEMA
_GUID = re.compile(r"^[0-9A-Fa-f]{32}$")
_REVISION = re.compile(r"^[0-9a-f]{24,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")


def _mapping(value: object, *, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(code)
    return {str(key): item for key, item in value.items()}


def _asset_projection(value: Mapping[str, object]) -> dict[str, object]:
    freshness = str(value.get("freshnessStatus") or value.get("freshness") or "")
    if freshness != "FRESH":
        raise ValueError("EVIDENCE_NOT_FRESH")
    projection = {
        "name": str(value.get("name") or ""),
        "objectPath": str(value.get("objectPath") or ""),
        "assetId": str(value.get("assetId") or ""),
        "evidenceRevisionId": str(value.get("evidenceRevisionId") or ""),
        "evidenceManifestSha256": str(
            value.get("evidenceManifestSha256") or ""
        ),
    }
    if (
        not projection["name"]
        or len(str(projection["name"])) > 256
        or not str(projection["objectPath"]).startswith(("/Game/", "/Engine/"))
        or len(str(projection["objectPath"])) > 4096
        or not projection["assetId"]
        or len(str(projection["assetId"])) > 256
        or _REVISION.fullmatch(str(projection["evidenceRevisionId"])) is None
        or _SHA256.fullmatch(str(projection["evidenceManifestSha256"])) is None
    ):
        raise ValueError("EVIDENCE_IDENTITY_INVALID")
    return projection


def _graph_belongs_to_asset(
    graph_ref: str,
    asset_projection: Mapping[str, object],
) -> bool:
    parsed = urlsplit(graph_ref)
    if (
        parsed.scheme != "bp"
        or "@" not in parsed.netloc
        or not parsed.path.startswith("/g/")
        or parsed.path == "/g/"
        or "/n/" in parsed.path
    ):
        return False
    asset_id, revision_id = parsed.netloc.split("@", 1)
    return (
        asset_id == str(asset_projection.get("assetId") or "")
        and revision_id == str(asset_projection.get("evidenceRevisionId") or "")
    )


def _fallback_graph_name(
    asset: Mapping[str, object],
    graph_ref: str,
    locators: Sequence[Mapping[str, object]],
) -> str:
    targets = asset.get("graphTargets")
    if isinstance(targets, Sequence) and not isinstance(targets, (str, bytes)):
        for target in targets:
            if isinstance(target, Mapping) and target.get("ref") == graph_ref:
                name = str(target.get("name") or "")
                if name:
                    return name
    names = {
        str(locator.get("graphName") or "")
        for locator in locators
        if str(locator.get("graphName") or "")
    }
    if len(names) == 1:
        return names.pop()
    # The compatibility LocatorSource protocol predates a graph projection and
    # is retained for the focused pure-function contract.  The production CLI
    # uses BlueprintService.get_node_binding_locators and never takes this path.
    return "EventGraph"


def _resolve_current_locators(
    *,
    asset: str,
    graph_ref: str,
    node_refs: Sequence[str],
    locator_source: object,
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    bulk = getattr(locator_source, "get_node_binding_locators", None)
    if callable(bulk):
        response = _mapping(
            bulk(asset=asset, graph_ref=graph_ref, node_refs=node_refs),
            code="LOCATOR_RESPONSE_INVALID",
        )
        asset_value = _mapping(
            response.get("asset"),
            code="EVIDENCE_IDENTITY_INVALID",
        )
        graph_value = _mapping(response.get("graph"), code="GRAPH_NOT_FOUND")
        raw_nodes = response.get("nodes")
        if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
            raise ValueError("LOCATOR_RESPONSE_INVALID")
        locators = [
            _mapping(row, code="LOCATOR_RESPONSE_INVALID") for row in raw_nodes
        ]
        return asset_value, graph_value, locators

    resolve_asset = getattr(locator_source, "resolve_asset", None)
    resolve_node = getattr(locator_source, "resolve_node", None)
    if not callable(resolve_asset) or not callable(resolve_node):
        raise ValueError("LOCATOR_SOURCE_INVALID")
    asset_value = _mapping(
        resolve_asset(asset),
        code="EVIDENCE_IDENTITY_INVALID",
    )
    _asset_projection(asset_value)
    locators: list[dict[str, object]] = []
    for node_ref in node_refs:
        try:
            value = resolve_node(node_ref)
        except KeyError as exc:
            raise ValueError("NODE_NOT_FOUND") from exc
        locators.append(_mapping(value, code="NODE_NOT_FOUND"))
    graph_value = {
        "name": _fallback_graph_name(asset_value, graph_ref, locators),
        "graphRef": graph_ref,
    }
    return asset_value, graph_value, locators


def _pin_projection(value: object) -> list[dict[str, object]] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("PIN_SIGNATURE_INVALID")
    if len(value) > 64:
        raise ValueError("PIN_LIMIT_EXCEEDED")
    result: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()
    for raw in value:
        pin = _mapping(raw, code="PIN_SIGNATURE_INVALID")
        raw_ordinal = pin.get("ordinal")
        if not isinstance(raw_ordinal, int) or isinstance(raw_ordinal, bool):
            raise ValueError("PIN_SIGNATURE_INVALID")
        ordinal = raw_ordinal
        projected = {
            "name": str(pin.get("name") or ""),
            "direction": str(pin.get("direction") or ""),
            "ordinal": ordinal,
            "category": str(pin.get("category") or ""),
            "subcategory": str(pin.get("subcategory") or ""),
        }
        key = (str(projected["name"]), str(projected["direction"]), ordinal)
        if (
            not projected["name"]
            or len(str(projected["name"])) > 256
            or not projected["direction"]
            or len(str(projected["direction"])) > 256
            or len(str(projected["category"])) > 256
            or len(str(projected["subcategory"])) > 256
            or ordinal < 0
        ):
            raise ValueError("PIN_SIGNATURE_INVALID")
        if key in seen:
            raise ValueError("PIN_SIGNATURE_DUPLICATE")
        seen.add(key)
        result.append(projected)
    result.sort(key=lambda pin: (int(pin["ordinal"]), str(pin["name"])))
    if [int(pin["ordinal"]) for pin in result] != list(range(len(result))):
        raise ValueError("PIN_SIGNATURE_INVALID")
    return result


def build_request(
    *,
    asset: str,
    graph_ref: str,
    node_refs: Sequence[str],
    locator_source: object,
) -> dict[str, object]:
    """Build one deterministic request from current, exact Evidence locators."""

    if isinstance(node_refs, (str, bytes)):
        raise ValueError("NODE_LIMIT_EXCEEDED")
    requested = [str(ref).strip() for ref in node_refs]
    if not 1 <= len(requested) <= 12:
        raise ValueError("NODE_LIMIT_EXCEEDED")
    if any(not ref for ref in requested):
        raise ValueError("NODE_REF_INVALID")
    if len(requested) != len(set(requested)):
        raise ValueError("NODE_REF_DUPLICATE")
    requested.sort()

    raw_asset, raw_graph, raw_locators = _resolve_current_locators(
        asset=asset,
        graph_ref=graph_ref,
        node_refs=requested,
        locator_source=locator_source,
    )
    asset_value = _asset_projection(raw_asset)
    if not _graph_belongs_to_asset(graph_ref, asset_value):
        raise ValueError("EVIDENCE_REVISION_MISMATCH")
    if str(raw_graph.get("graphRef") or "") != graph_ref:
        raise ValueError("GRAPH_REF_MISMATCH")
    graph_name = str(raw_graph.get("name") or "")
    if not graph_name or len(graph_name) > 256:
        raise ValueError("GRAPH_NOT_FOUND")

    by_ref: dict[str, dict[str, object]] = {}
    for locator in raw_locators:
        node_ref = str(locator.get("nodeRef") or "")
        if node_ref in by_ref:
            raise ValueError("NODE_REF_DUPLICATE")
        by_ref[node_ref] = locator
    if set(by_ref) != set(requested):
        raise ValueError("NODE_NOT_FOUND")

    nodes: list[dict[str, object]] = []
    total_pins = 0
    for node_ref in requested:
        locator = by_ref[node_ref]
        if str(locator.get("graphRef") or "") != graph_ref:
            raise ValueError("NODE_GRAPH_MISMATCH")
        if not node_ref.startswith(f"{graph_ref}/n/"):
            raise ValueError("NODE_GRAPH_MISMATCH")
        if (
            str(locator.get("evidenceRevisionId") or "")
            != asset_value["evidenceRevisionId"]
            or str(locator.get("evidenceManifestSha256") or "")
            != asset_value["evidenceManifestSha256"]
        ):
            raise ValueError("EVIDENCE_REVISION_MISMATCH")
        object_name = str(locator.get("objectName") or "")
        class_name = str(locator.get("className") or "")
        node_guid = str(locator.get("nodeGuid") or "")
        if (
            not object_name
            or len(object_name) > 256
            or not class_name
            or len(class_name) > 256
        ):
            raise ValueError("NODE_LOCATOR_NOT_AVAILABLE")
        if _GUID.fullmatch(node_guid) is None or set(node_guid) == {"0"}:
            raise ValueError("NODE_GUID_NOT_AVAILABLE")
        node: dict[str, object] = {
            "nodeRef": node_ref,
            "objectName": object_name,
            "expectedClassName": class_name,
            "expectedNodeGuid": node_guid.upper(),
        }
        x = locator.get("x")
        y = locator.get("y")
        if (
            isinstance(x, int)
            and not isinstance(x, bool)
            and isinstance(y, int)
            and not isinstance(y, bool)
        ):
            node["evidenceX"] = x
            node["evidenceY"] = y
        pins = _pin_projection(locator.get("pins"))
        if pins is not None:
            total_pins += len(pins)
            if total_pins > 512:
                raise ValueError("PIN_LIMIT_EXCEEDED")
            node["evidencePins"] = pins
        nodes.append(node)

    graph_value = {"name": graph_name, "graphRef": graph_ref}
    request_seed = {
        "schema": REQUEST_SCHEMA,
        "asset": asset_value,
        "graph": graph_value,
        "nodes": nodes,
        "maxNodes": 12,
    }
    request_id = hashlib.sha256(
        canonical_json(request_seed).encode("utf-8")
    ).hexdigest()
    request: dict[str, object] = {
        "schema": REQUEST_SCHEMA,
        "requestId": f"node-binding-request://{request_id}",
        "asset": asset_value,
        "graph": graph_value,
        "nodes": nodes,
        "maxNodes": 12,
    }
    attach_semantic_digest(request)
    assert_path_free(request)
    return request


def _build_from_current_plan(
    *,
    blueprint: BlueprintService,
    task_root: Path,
    task_id: str,
    plan_id: str,
) -> dict[str, object]:
    store = TaskStore(task_root)
    tasks = TaskService(blueprint, store)
    plans = PlanService(blueprint, tasks, store)
    context, session = tasks.verified_task(
        task_id,
        allowed_phases={"PLAN_DRAFT", "PLAN_CONFIRMED"},
        persist_verification=False,
    )
    if str(session.get("patchPlanId") or "") != plan_id:
        raise ValueError("PATCH_PLAN_NOT_CURRENT")
    plan = store.load_plan(task_id, plan_id)
    if plan.get("taskId") != task_id or plan.get("status") not in {
        "DRAFT",
        "CONFIRMED",
    }:
        raise ValueError("PATCH_PLAN_INVALID")
    expected_phase = f"PLAN_{plan['status']}"
    if str(session.get("phase") or "") != expected_phase:
        raise ValueError("PATCH_PLAN_NOT_CURRENT")
    if str(session.get("patchPlanSha256") or "") != str(
        plan.get("semanticDigest") or ""
    ):
        raise ValueError("PATCH_PLAN_NOT_CURRENT")
    validation = plans.validate(
        task_id,
        plan_id,
        persist_verification=False,
    )
    if not validation.get("valid"):
        raise ValueError("PATCH_PLAN_INVALID")
    target = _mapping(plan.get("target"), code="PATCH_PLAN_INVALID")
    graph_refs = target.get("graphRefs")
    if (
        not isinstance(graph_refs, Sequence)
        or isinstance(graph_refs, (str, bytes))
        or len(graph_refs) != 1
    ):
        raise ValueError("GRAPH_SELECTION_REQUIRED")
    raw_nodes = plan.get("nodes")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
        raise ValueError("PATCH_PLAN_INVALID")
    node_refs = [
        str(node.get("nodeRef") or "")
        for node in raw_nodes
        if isinstance(node, Mapping) and str(node.get("nodeRef") or "")
    ]
    primary = _mapping(context.get("primaryAsset"), code="PATCH_PLAN_INVALID")
    return build_request(
        asset=str(primary.get("name") or ""),
        graph_ref=str(graph_refs[0]),
        node_refs=node_refs,
        locator_source=blueprint,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an exact-node binding request from current Evidence."
    )
    parser.add_argument("--asset", help="Blueprint asset name or object path")
    parser.add_argument("--graph-ref", help="Exact current bp:// graph reference")
    parser.add_argument(
        "--node-ref",
        action="append",
        default=[],
        help="Exact current bp:// node reference; repeat between 1 and 12 times",
    )
    parser.add_argument("--task", help="Opaque task:// handle")
    parser.add_argument("--plan", help="Opaque patch-plan:// handle")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=ROOT / "captures",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--task-root",
        type=Path,
        default=ROOT / ".blueprint-tasks",
        help=argparse.SUPPRESS,
    )
    return parser


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, McpExecutionError):
        return exc.code
    candidate = str(exc).strip().strip("'\"").split(":", 1)[0]
    return candidate if _ERROR_CODE.fullmatch(candidate) else "REQUEST_BUILD_FAILED"


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    plan_mode = bool(arguments.task or arguments.plan)
    explicit_mode = bool(arguments.asset or arguments.graph_ref or arguments.node_ref)
    try:
        if arguments.output.is_file():
            arguments.output.unlink()
        if plan_mode == explicit_mode:
            raise ValueError("INPUT_MODE_INVALID")
        blueprint = BlueprintService(arguments.capture_root)
        if plan_mode:
            if not arguments.task or not arguments.plan:
                raise ValueError("INPUT_MODE_INVALID")
            request = _build_from_current_plan(
                blueprint=blueprint,
                task_root=arguments.task_root,
                task_id=arguments.task,
                plan_id=arguments.plan,
            )
        else:
            if not arguments.asset or not arguments.graph_ref:
                raise ValueError("INPUT_MODE_INVALID")
            request = build_request(
                asset=arguments.asset,
                graph_ref=arguments.graph_ref,
                node_refs=arguments.node_ref,
                locator_source=blueprint,
            )
        # Import lazily so the pure builder remains independently reusable.
        from arkdev_scripting_probe.node_binding import validate_request

        validate_request(request)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print("ARKDEV_NODE_BINDING_REQUEST=COMPLETE")
        return 0
    except (OSError, KeyError, TypeError, ValueError, McpExecutionError) as exc:
        print(
            f"ARKDEV_NODE_BINDING_REQUEST=ERROR:{_safe_error_code(exc)}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["REQUEST_SCHEMA", "build_request", "main"]
