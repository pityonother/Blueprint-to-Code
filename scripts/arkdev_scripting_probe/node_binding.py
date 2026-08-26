"""Strict contracts for Evidence-guided exact Blueprint node binding."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from arkdev_scripting_probe.contracts import (
    MAX_NODE_BINDING_NODES,
    MAX_NODE_BINDING_PINS_PER_NODE,
    MAX_NODE_BINDING_PINS_TOTAL,
    NODE_BINDING_REQUEST_SCHEMA,
    NODE_BINDING_RESULT_SCHEMA,
    assert_path_free,
    require_digest,
    semantic_digest,
)


REQUEST_SCHEMA = NODE_BINDING_REQUEST_SCHEMA
RESULT_SCHEMA = NODE_BINDING_RESULT_SCHEMA

MAX_NODES = MAX_NODE_BINDING_NODES
MAX_PINS_PER_NODE = MAX_NODE_BINDING_PINS_PER_NODE
MAX_PINS_TOTAL = MAX_NODE_BINDING_PINS_TOTAL

_GUID = re.compile(r"^(?!0{32}$)[0-9A-F]{32}$")
_REQUEST_ID = re.compile(r"^node-binding-request://[^\s]{1,4069}$")
_GAP = re.compile(r"^[A-Z0-9_]{1,128}$")
_ASSET_STATUSES = frozenset({"EXACT", "NOT_FOUND", "ERROR"})
_GRAPH_STATUSES = frozenset({"EXACT", "NOT_FOUND", "ERROR"})
_LOOKUP_METHODS = frozenset({"NONE", "FIND_OBJECT", "LOAD_OBJECT"})
_BINDING_STATUSES = frozenset(
    {"NOT_FOUND", "OUTER_MISMATCH", "CLASS_MISMATCH", "GUID_MISMATCH", "EXACT"}
)
_POSITION_STATUSES = frozenset(
    {"POSITION_AVAILABLE", "POSITION_UNAVAILABLE", "POSITION_ERROR", "NOT_TESTED"}
)
_PIN_READ_STATUSES = frozenset({"PASS", "PARTIAL", "UNAVAILABLE", "NOT_TESTED"})
_PIN_BINDING_STATUSES = frozenset({"SIGNATURE_ONLY", "UNAVAILABLE"})
_ROUTES = frozenset(
    {
        "EXACT_NODE_AND_PIN_BINDING",
        "EXACT_NODE_BINDING_PIN_PARTIAL",
        "EXACT_NODE_BINDING_PIN_UNAVAILABLE",
        "PARTIAL_NODE_BINDING",
        "NODE_BINDING_UNAVAILABLE",
    }
)

_REQUEST_KEYS = frozenset(
    {"schema", "requestId", "asset", "graph", "nodes", "maxNodes", "semanticDigest"}
)
_ASSET_KEYS = frozenset(
    {"name", "objectPath", "assetId", "evidenceRevisionId", "evidenceManifestSha256"}
)
_GRAPH_KEYS = frozenset({"name", "graphRef"})
_REQUEST_NODE_KEYS = frozenset(
    {
        "nodeRef",
        "objectName",
        "expectedClassName",
        "expectedNodeGuid",
        "evidenceX",
        "evidenceY",
        "evidencePins",
    }
)
_PIN_KEYS = frozenset(
    {"name", "direction", "ordinal", "category", "subcategory", "default"}
)
_RESULT_KEYS = frozenset(
    {
        "schema",
        "requestId",
        "requestSemanticDigest",
        "generatedAt",
        "runtime",
        "assetStatus",
        "assetObjectPath",
        "graphStatus",
        "graphPath",
        "nodes",
        "summary",
        "route",
        "mutationReady",
        "objectIteratorCalled",
        "mutationApiCalled",
        "gaps",
        "semanticDigest",
    }
)
_RUNTIME_KEYS = frozenset(
    {
        "engineVersion",
        "pythonVersion",
        "findObjectAvailable",
        "loadObjectAvailable",
        "edGraphNodeClassAvailable",
        "k2NodeClassAvailable",
    }
)
_RESULT_NODE_KEYS = frozenset(
    {
        "nodeRef",
        "objectName",
        "expectedClassName",
        "expectedNodeGuid",
        "lookupMethod",
        "bindingStatus",
        "actualObjectName",
        "actualObjectPath",
        "actualClassName",
        "actualNodeGuid",
        "outerPath",
        "outerMatches",
        "evidenceX",
        "evidenceY",
        "liveX",
        "liveY",
        "positionStatus",
        "positionMatches",
        "pinRead",
        "pinBindingStatus",
        "pinSignatureMatches",
        "pins",
        "pinsReturned",
        "pinsOmitted",
        "pinsTruncated",
        "gaps",
    }
)
_SUMMARY_KEYS = frozenset(
    {
        "requested",
        "exact",
        "notFound",
        "outerMismatch",
        "classMismatch",
        "guidMismatch",
        "positionAvailable",
        "pinReadable",
    }
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be an array")
    return value


def _exact_keys(
    value: Mapping[str, object],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> None:
    keys = {str(key) for key in value}
    if not required <= keys:
        raise ValueError(f"{label} is missing required fields")
    if not keys <= allowed:
        raise ValueError(f"{label} contains unsupported fields")


def _text(value: object, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{label} is invalid")
    return value


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _integer(
    value: object,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} is below its bound")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds its bound")
    return value


def _guid(value: object, label: str) -> str:
    if not isinstance(value, str) or _GUID.fullmatch(value) is None:
        raise ValueError(f"{label} is not a non-zero uppercase NodeGuid")
    return value


def _digest_matches(value: Mapping[str, object]) -> None:
    digest = require_digest(value.get("semanticDigest"))
    if digest != semantic_digest(value):
        raise ValueError("semantic digest mismatch")


def _gaps(value: object, label: str) -> list[str]:
    items = _sequence(value, label)
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or _GAP.fullmatch(item) is None:
            raise ValueError(f"{label} contains an invalid gap code")
        result.append(item)
    if len(result) != len(set(result)) or result != sorted(result):
        raise ValueError(f"{label} must be sorted and unique")
    return result


def _validate_pin(value: object, *, result_pin: bool) -> dict[str, object]:
    pin = dict(_mapping(value, "pin"))
    required = {"name", "direction", "ordinal"}
    _exact_keys(pin, allowed=_PIN_KEYS, required=frozenset(required), label="pin")
    _text(pin.get("name"), "pin.name", maximum=256)
    _text(pin.get("direction"), "pin.direction", maximum=256)
    _integer(pin.get("ordinal"), "pin.ordinal", minimum=0, maximum=MAX_PINS_TOTAL - 1)
    for key in ("category", "subcategory"):
        if key in pin and (not isinstance(pin[key], str) or len(pin[key]) > 256):
            raise ValueError(f"pin.{key} is invalid")
    if "default" in pin:
        default = pin["default"]
        if not isinstance(default, (str, int, float, bool)) and default is not None:
            raise ValueError("pin.default is not a stable scalar")
        if isinstance(default, float) and not math.isfinite(default):
            raise ValueError("pin.default is not a finite scalar")
        if isinstance(default, str) and len(default) > 4096:
            raise ValueError("pin.default exceeds its bound")
    if not result_pin and "default" in pin:
        raise ValueError("Evidence pin signatures may not contain a live default")
    return pin


def _validate_request_node(value: object, graph_ref: str) -> dict[str, object]:
    node = dict(_mapping(value, "request node"))
    required = frozenset(
        {"nodeRef", "objectName", "expectedClassName", "expectedNodeGuid"}
    )
    _exact_keys(
        node,
        allowed=_REQUEST_NODE_KEYS,
        required=required,
        label="request node",
    )
    node_ref = _text(node.get("nodeRef"), "nodeRef")
    if not node_ref.startswith(f"{graph_ref}/n/") or node_ref == f"{graph_ref}/n/":
        raise ValueError("nodeRef does not belong to the requested graph")
    _text(node.get("objectName"), "objectName", maximum=256)
    _text(node.get("expectedClassName"), "expectedClassName", maximum=256)
    _guid(node.get("expectedNodeGuid"), "expectedNodeGuid")
    has_x = "evidenceX" in node
    has_y = "evidenceY" in node
    if has_x != has_y:
        raise ValueError("Evidence position must include both x and y")
    if has_x:
        _integer(node["evidenceX"], "evidenceX")
        _integer(node["evidenceY"], "evidenceY")
    if "evidencePins" in node:
        pins = _sequence(node["evidencePins"], "evidencePins")
        if len(pins) > MAX_PINS_TOTAL:
            raise ValueError("Evidence pin signature exceeds its bound")
        normalized = [_validate_pin(pin, result_pin=False) for pin in pins]
        ordinals = [int(pin["ordinal"]) for pin in normalized]
        if ordinals != list(range(len(ordinals))):
            raise ValueError("Evidence pin ordinals must be contiguous and ordered")
    return node


def validate_request(value: object) -> dict[str, object]:
    """Validate and return one strict, path-free node-binding request."""

    request = dict(_mapping(value, "request"))
    _exact_keys(request, allowed=_REQUEST_KEYS, required=_REQUEST_KEYS, label="request")
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError("node-binding request schema mismatch")
    request_id = request.get("requestId")
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise ValueError("requestId is invalid")

    asset = _mapping(request.get("asset"), "asset")
    _exact_keys(asset, allowed=_ASSET_KEYS, required=_ASSET_KEYS, label="asset")
    _text(asset.get("name"), "asset.name", maximum=256)
    object_path = _text(asset.get("objectPath"), "asset.objectPath")
    if not object_path.startswith(("/Game/", "/Engine/")):
        raise ValueError("asset.objectPath is not an Unreal object path")
    _text(asset.get("assetId"), "asset.assetId", maximum=256)
    revision = _text(
        asset.get("evidenceRevisionId"),
        "asset.evidenceRevisionId",
        maximum=256,
    )
    if not re.fullmatch(r"[0-9a-f]{24,64}", revision):
        raise ValueError("asset.evidenceRevisionId is invalid")
    require_digest(asset.get("evidenceManifestSha256"))

    graph = _mapping(request.get("graph"), "graph")
    _exact_keys(graph, allowed=_GRAPH_KEYS, required=_GRAPH_KEYS, label="graph")
    _text(graph.get("name"), "graph.name", maximum=256)
    graph_ref = _text(graph.get("graphRef"), "graph.graphRef")
    expected_prefix = f"bp://{asset['assetId']}@{revision}/g/"
    if not graph_ref.startswith(expected_prefix) or "/n/" in graph_ref:
        raise ValueError("graphRef is not bound to the asset Evidence revision")

    nodes = _sequence(request.get("nodes"), "nodes")
    if not 1 <= len(nodes) <= MAX_NODES:
        raise ValueError("node request count is outside 1..12")
    if _integer(request.get("maxNodes"), "maxNodes") != MAX_NODES:
        raise ValueError("maxNodes must be the fixed Phase 3C bound")
    normalized = [_validate_request_node(node, graph_ref) for node in nodes]
    refs = [str(node["nodeRef"]) for node in normalized]
    if len(refs) != len(set(refs)):
        raise ValueError("nodeRef values must be unique")
    if refs != sorted(refs):
        raise ValueError("nodes must be deterministically sorted by nodeRef")

    _digest_matches(request)
    assert_path_free(request)
    return request


def _validate_result_node(
    value: object,
    request_node: Mapping[str, object],
) -> dict[str, object]:
    node = dict(_mapping(value, "result node"))
    required = frozenset(
        {
            "nodeRef",
            "objectName",
            "expectedClassName",
            "expectedNodeGuid",
            "lookupMethod",
            "bindingStatus",
            "positionStatus",
            "pinRead",
            "pinBindingStatus",
            "gaps",
        }
    )
    _exact_keys(node, allowed=_RESULT_NODE_KEYS, required=required, label="result node")
    for key in ("nodeRef", "objectName", "expectedClassName", "expectedNodeGuid"):
        if node.get(key) != request_node.get(key):
            raise ValueError("result node is not bound to its request locator")
    lookup = node.get("lookupMethod")
    binding = node.get("bindingStatus")
    position = node.get("positionStatus")
    pin_read = node.get("pinRead")
    pin_binding = node.get("pinBindingStatus")
    if lookup not in _LOOKUP_METHODS:
        raise ValueError("invalid lookup method")
    if binding not in _BINDING_STATUSES:
        raise ValueError("invalid node binding status")
    if position not in _POSITION_STATUSES:
        raise ValueError("invalid position status")
    if pin_read not in _PIN_READ_STATUSES:
        raise ValueError("invalid Pin read status")
    if pin_binding not in _PIN_BINDING_STATUSES:
        raise ValueError("invalid Pin binding status")
    _gaps(node.get("gaps"), "node.gaps")

    for key in ("actualObjectName", "actualObjectPath", "actualClassName", "outerPath"):
        if key in node:
            _text(node[key], key)
    if "actualNodeGuid" in node:
        _guid(node["actualNodeGuid"], "actualNodeGuid")
    if "outerMatches" in node:
        _bool(node["outerMatches"], "outerMatches")

    request_has_position = "evidenceX" in request_node
    for key in ("evidenceX", "evidenceY"):
        if (key in node) != request_has_position:
            raise ValueError("result Evidence position shape differs from request")
        if key in node:
            if node[key] != request_node[key]:
                raise ValueError("result Evidence position differs from request")
            _integer(node[key], key)
    live_x = "liveX" in node
    live_y = "liveY" in node
    if live_x != live_y:
        raise ValueError("live position must include both x and y")
    if live_x:
        _integer(node["liveX"], "liveX")
        _integer(node["liveY"], "liveY")
    if position == "POSITION_AVAILABLE" and not live_x:
        raise ValueError("available position lacks live coordinates")
    if position != "POSITION_AVAILABLE" and live_x:
        raise ValueError("unavailable position contains live coordinates")
    if "positionMatches" in node:
        _bool(node["positionMatches"], "positionMatches")
        if position != "POSITION_AVAILABLE" or not request_has_position:
            raise ValueError("position match lacks live and Evidence positions")
        expected_match = (
            node["liveX"] == request_node["evidenceX"]
            and node["liveY"] == request_node["evidenceY"]
        )
        if node["positionMatches"] is not expected_match:
            raise ValueError("position match is inconsistent")

    has_pins = "pins" in node
    count_keys = {"pinsReturned", "pinsOmitted", "pinsTruncated"}
    if has_pins != (count_keys <= node.keys()):
        raise ValueError("Pin result counts are incomplete")
    if has_pins:
        pins = _sequence(node["pins"], "pins")
        if len(pins) > MAX_PINS_PER_NODE:
            raise ValueError("per-node Pin cap exceeded")
        normalized_pins = [_validate_pin(pin, result_pin=True) for pin in pins]
        returned = _integer(
            node["pinsReturned"], "pinsReturned", minimum=0, maximum=MAX_PINS_PER_NODE
        )
        omitted = _integer(node["pinsOmitted"], "pinsOmitted", minimum=0)
        truncated = _bool(node["pinsTruncated"], "pinsTruncated")
        if returned != len(normalized_pins) or truncated != (omitted > 0):
            raise ValueError("Pin counts or truncation are inconsistent")
        ordinals = [int(pin["ordinal"]) for pin in normalized_pins]
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            raise ValueError("live Pin ordinals must be ordered and unique")
    elif pin_read in {"PASS", "PARTIAL"}:
        raise ValueError("readable Pin status lacks Pin data")

    if "pinSignatureMatches" in node:
        _bool(node["pinSignatureMatches"], "pinSignatureMatches")
        evidence_pins = request_node.get("evidencePins")
        if not has_pins or not isinstance(evidence_pins, Sequence):
            raise ValueError("Pin signature match lacks comparable signatures")
        if node.get("pinsTruncated") or pin_read != "PASS":
            raise ValueError("partial Pin data cannot claim a signature match")
        expected_signature = [
            (pin.get("name"), pin.get("direction"), pin.get("ordinal"))
            for pin in evidence_pins
            if isinstance(pin, Mapping)
        ]
        live_signature = [
            (pin.get("name"), pin.get("direction"), pin.get("ordinal"))
            for pin in node["pins"]
            if isinstance(pin, Mapping)
        ]
        if node["pinSignatureMatches"] is not (live_signature == expected_signature):
            raise ValueError("Pin signature match is inconsistent")

    if pin_binding == "SIGNATURE_ONLY":
        if (
            pin_read != "PASS"
            or not has_pins
            or "evidencePins" not in request_node
            or "pinSignatureMatches" not in node
        ):
            raise ValueError("SIGNATURE_ONLY requires a complete Pin comparison")
    elif "pinSignatureMatches" in node:
        raise ValueError("a compared Pin signature must be labeled SIGNATURE_ONLY")
    if pin_read == "PASS" and "evidencePins" in request_node:
        if pin_binding != "SIGNATURE_ONLY" or "pinSignatureMatches" not in node:
            raise ValueError("complete live and Evidence Pins must be compared")
    if pin_read in {"UNAVAILABLE", "NOT_TESTED"} and pin_binding != "UNAVAILABLE":
        raise ValueError("unreadable Pins cannot claim a signature binding")

    if binding == "EXACT":
        if (
            lookup == "NONE"
            or node.get("outerMatches") is not True
            or node.get("actualClassName") != request_node.get("expectedClassName")
            or node.get("actualNodeGuid") != request_node.get("expectedNodeGuid")
        ):
            raise ValueError("EXACT binding lacks exact outer, class, or GUID proof")
    elif binding == "OUTER_MISMATCH" and node.get("outerMatches") is not False:
        raise ValueError("outer mismatch lacks negative outer proof")
    elif binding == "CLASS_MISMATCH" and (
        node.get("outerMatches") is not True
        or "actualClassName" not in node
        or node.get("actualClassName") == request_node.get("expectedClassName")
    ):
        raise ValueError("class mismatch is not proven")
    elif binding == "GUID_MISMATCH" and (
        node.get("outerMatches") is not True
        or node.get("actualClassName") != request_node.get("expectedClassName")
        or (
            "actualNodeGuid" in node
            and node.get("actualNodeGuid") == request_node.get("expectedNodeGuid")
        )
    ):
        raise ValueError("GUID mismatch is not proven")
    elif binding == "NOT_FOUND":
        if any(
            key in node
            for key in (
                "actualObjectName",
                "actualObjectPath",
                "actualClassName",
                "actualNodeGuid",
                "outerPath",
                "outerMatches",
                "liveX",
                "liveY",
                "pins",
            )
        ):
            raise ValueError("NOT_FOUND node contains live-object evidence")
    if binding != "EXACT" and (
        position != "NOT_TESTED"
        or pin_read != "NOT_TESTED"
        or pin_binding != "UNAVAILABLE"
    ):
        raise ValueError("non-exact node may not claim position or Pin reads")
    return node


def _expected_route(
    *,
    asset_status: str,
    graph_status: str,
    nodes: Sequence[Mapping[str, object]],
) -> str:
    exact = sum(node.get("bindingStatus") == "EXACT" for node in nodes)
    if asset_status != "EXACT" or graph_status != "EXACT" or exact == 0:
        return "NODE_BINDING_UNAVAILABLE"
    if exact != len(nodes):
        return "PARTIAL_NODE_BINDING"
    pin_reads = [node.get("pinRead") for node in nodes]
    if all(status in {"UNAVAILABLE", "NOT_TESTED"} for status in pin_reads):
        return "EXACT_NODE_BINDING_PIN_UNAVAILABLE"
    # Signature equality is not live Pin identity. Keep the exact-Pin route
    # unreachable until a separately verified identity adapter exists.
    return "EXACT_NODE_BINDING_PIN_PARTIAL"


def validate_result(
    value: object,
    *,
    request: Mapping[str, object],
) -> dict[str, object]:
    """Validate a result and bind it exactly to its originating request."""

    validated_request = validate_request(request)
    result = dict(_mapping(value, "result"))
    required = frozenset(
        {
            "schema",
            "requestId",
            "requestSemanticDigest",
            "generatedAt",
            "runtime",
            "assetStatus",
            "graphStatus",
            "nodes",
            "summary",
            "route",
            "mutationReady",
            "objectIteratorCalled",
            "mutationApiCalled",
            "gaps",
            "semanticDigest",
        }
    )
    _exact_keys(result, allowed=_RESULT_KEYS, required=required, label="result")
    if result.get("schema") != RESULT_SCHEMA:
        raise ValueError("node-binding result schema mismatch")
    if result.get("requestId") != validated_request["requestId"]:
        raise ValueError("result requestId mismatch")
    require_digest(result.get("requestSemanticDigest"))
    if result.get("requestSemanticDigest") != validated_request["semanticDigest"]:
        raise ValueError("result is stale or bound to a different request")
    _text(result.get("generatedAt"), "generatedAt", maximum=128)

    runtime = _mapping(result.get("runtime"), "runtime")
    _exact_keys(
        runtime,
        allowed=_RUNTIME_KEYS,
        required=frozenset({"engineVersion", "pythonVersion"}),
        label="runtime",
    )
    _text(runtime.get("engineVersion"), "runtime.engineVersion", maximum=128)
    _text(runtime.get("pythonVersion"), "runtime.pythonVersion", maximum=64)
    for key in _RUNTIME_KEYS - {"engineVersion", "pythonVersion"}:
        if key in runtime:
            _bool(runtime[key], f"runtime.{key}")

    asset_status = result.get("assetStatus")
    graph_status = result.get("graphStatus")
    if asset_status not in _ASSET_STATUSES or graph_status not in _GRAPH_STATUSES:
        raise ValueError("invalid asset or graph status")
    if asset_status != "EXACT" and graph_status == "EXACT":
        raise ValueError("graph cannot be exact when asset is not exact")
    if asset_status == "EXACT" and "assetObjectPath" not in result:
        raise ValueError("exact asset lacks its verified object path")
    if asset_status != "EXACT" and "assetObjectPath" in result:
        raise ValueError("unavailable asset contains an object path claim")
    if graph_status == "EXACT" and "graphPath" not in result:
        raise ValueError("exact graph lacks its verified object path")
    if graph_status != "EXACT" and "graphPath" in result:
        raise ValueError("unavailable graph contains an object path claim")
    if "assetObjectPath" in result:
        _text(result["assetObjectPath"], "assetObjectPath")
        if (
            asset_status == "EXACT"
            and result["assetObjectPath"]
            != validated_request["asset"]["objectPath"]
        ):
            raise ValueError("exact asset path differs from request")
    if "graphPath" in result:
        _text(result["graphPath"], "graphPath")

    request_nodes = _sequence(validated_request["nodes"], "request.nodes")
    result_nodes = _sequence(result.get("nodes"), "result.nodes")
    if len(result_nodes) != len(request_nodes):
        raise ValueError("result node set differs from request")
    nodes = [
        _validate_result_node(item, _mapping(expected, "request node"))
        for item, expected in zip(result_nodes, request_nodes, strict=True)
    ]
    if asset_status != "EXACT" or graph_status != "EXACT":
        if any(node["bindingStatus"] != "NOT_FOUND" for node in nodes):
            raise ValueError("unavailable target contains a live node binding")

    summary = _mapping(result.get("summary"), "summary")
    _exact_keys(summary, allowed=_SUMMARY_KEYS, required=_SUMMARY_KEYS, label="summary")
    for key in _SUMMARY_KEYS:
        _integer(summary.get(key), f"summary.{key}", minimum=0, maximum=MAX_NODES)
    expected_summary = {
        "requested": len(nodes),
        "exact": sum(node["bindingStatus"] == "EXACT" for node in nodes),
        "notFound": sum(node["bindingStatus"] == "NOT_FOUND" for node in nodes),
        "outerMismatch": sum(
            node["bindingStatus"] == "OUTER_MISMATCH" for node in nodes
        ),
        "classMismatch": sum(
            node["bindingStatus"] == "CLASS_MISMATCH" for node in nodes
        ),
        "guidMismatch": sum(node["bindingStatus"] == "GUID_MISMATCH" for node in nodes),
        "positionAvailable": sum(
            node["positionStatus"] == "POSITION_AVAILABLE" for node in nodes
        ),
        "pinReadable": sum(node["pinRead"] in {"PASS", "PARTIAL"} for node in nodes),
    }
    if dict(summary) != expected_summary:
        raise ValueError("result summary does not match node outcomes")
    route = result.get("route")
    if route not in _ROUTES or route != _expected_route(
        asset_status=str(asset_status), graph_status=str(graph_status), nodes=nodes
    ):
        raise ValueError("node-binding route overstates or contradicts the result")

    if _bool(result.get("mutationReady"), "mutationReady"):
        raise ValueError("Phase 3C may not declare mutation readiness")
    if _bool(result.get("objectIteratorCalled"), "objectIteratorCalled"):
        raise ValueError("ObjectIterator is forbidden in Phase 3C")
    if _bool(result.get("mutationApiCalled"), "mutationApiCalled"):
        raise ValueError("mutation APIs are forbidden in Phase 3C")
    _gaps(result.get("gaps"), "result.gaps")
    total_pins = sum(
        len(_sequence(node.get("pins", []), "pins")) for node in nodes
    )
    if total_pins > MAX_PINS_TOTAL:
        raise ValueError("request-wide Pin cap exceeded")

    _digest_matches(result)
    assert_path_free(result)
    return result


__all__ = [
    "MAX_NODES",
    "MAX_PINS_PER_NODE",
    "MAX_PINS_TOTAL",
    "REQUEST_SCHEMA",
    "RESULT_SCHEMA",
    "validate_request",
    "validate_result",
]
