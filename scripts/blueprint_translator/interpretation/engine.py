from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
from typing import Any, Iterable

from .contracts import (
    INTERPRETATION_SCHEMA,
    INTERPRETER_VERSION,
    STATEMENT_KINDS,
    STATEMENT_STATUSES,
    InterpretationBuild,
    InterpretationPublicationError,
    canonical_json_bytes,
    semantic_digest,
    stable_id,
)
from .control_flow import build_control_flow, control_node_kind
from .data_flow import build_data_flow
from .render import gaps_payload, render_markdown, render_pseudocode_and_trace
from .source import InterpretationSource, load_interpretation_source


_HINT_KEYWORDS = {
    "Glide": ("glide", "gliding"),
    "Sliding": ("slide", "sliding"),
    "Nursing": ("nursing", "nurse", "baby"),
    "Damage": ("damage", "hurt", "attack"),
    "Movement": ("movement", "move", "speed", "velocity"),
}

_PROVENANCE_FIELDS = (
    "status",
    "resolution_status",
    "confidence",
    "source",
    "provenance",
    "read_status",
    "uasset_read_status",
)
_STATUS_PRIORITY = {
    "CONFIRMED": 0,
    "HEURISTIC": 1,
    "SOURCE_NOT_AVAILABLE": 2,
    "NOT_RECOVERED": 3,
    "AMBIGUOUS": 4,
}
_DELEGATE_BIND_NODE_TYPES = frozenset(
    {
        "k2node_adddelegate",
        "k2node_assigndelegate",
        "k2node_createdelegate",
    }
)
_DELEGATE_INVOKE_NODE_TYPES = frozenset({"k2node_calldelegate"})


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_node_type(node: dict[str, Any]) -> str:
    raw = _text(node.get("node_type") or node.get("class_name")).strip("'\"")
    return raw.rsplit("/", 1)[-1].rsplit(".", 1)[-1].strip("'\"").casefold()


def _delegate_operation(node: dict[str, Any]) -> str:
    node_type = _canonical_node_type(node)
    if node_type in _DELEGATE_INVOKE_NODE_TYPES:
        return "INVOKE"
    if node_type in _DELEGATE_BIND_NODE_TYPES:
        return "BIND"
    return "UNKNOWN"


def _contains_provenance_marker(value: str, marker: str) -> bool:
    value_tokens = re.findall(r"[a-z0-9]+", value.casefold())
    marker_tokens = re.findall(r"[a-z0-9]+", marker.casefold())
    width = len(marker_tokens)
    return bool(width) and any(
        value_tokens[index : index + width] == marker_tokens
        for index in range(len(value_tokens) - width + 1)
    )


def _status_from_provenance_values(values: Iterable[object]) -> str:
    normalized = {_text(value).casefold() for value in values if _text(value)}
    statuses: set[str] = set()
    for value in normalized:
        if _contains_provenance_marker(value, "ambiguous"):
            statuses.add("AMBIGUOUS")
        elif _contains_provenance_marker(value, "source_not_available"):
            statuses.add("SOURCE_NOT_AVAILABLE")
        elif any(
            _contains_provenance_marker(value, marker)
            for marker in (
                "not_recovered",
                "unresolved",
                "failed",
                "missing",
                "partial",
                "unknown",
                "needs",
                "need",
                "not_ready",
                "not_available",
                "pending",
                "queued",
                "skipped",
                "incomplete",
            )
        ):
            statuses.add("NOT_RECOVERED")
        elif any(
            _contains_provenance_marker(value, marker)
            for marker in ("heuristic", "unconfirmed", "medium", "low")
        ):
            statuses.add("HEURISTIC")
    return max(statuses or {"CONFIRMED"}, key=_STATUS_PRIORITY.__getitem__)


def _row_provenance_status(row: dict[str, Any]) -> str:
    values = [row.get(key) for key in _PROVENANCE_FIELDS]
    for nested_key in ("metadata", "semantic", "extra"):
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            values.extend(nested.get(key) for key in _PROVENANCE_FIELDS)
    return _status_from_provenance_values(values)


def _combined_provenance_status(*rows: dict[str, Any] | None) -> str:
    statuses = [
        _row_provenance_status(row)
        for row in rows
        if isinstance(row, dict)
    ]
    return max(statuses or ["CONFIRMED"], key=_STATUS_PRIORITY.__getitem__)


def _default_provenance_status(row: dict[str, Any]) -> str:
    status = _row_provenance_status(row)
    if status != "CONFIRMED":
        return status
    values = [row.get(key) for key in ("status", "confidence", "provenance")]
    extra = row.get("extra")
    if isinstance(extra, dict):
        values.extend(extra.get(key) for key in ("status", "confidence", "provenance"))
    normalized = {
        _text(value).casefold().replace("-", "_").replace(" ", "_")
        for value in values
        if _text(value)
    }
    if any(
        value in {
            "complete",
            "complete_empty",
            "confirmed",
            "exact",
            "high",
            "resolved",
            "resolved_pin",
        }
        for value in normalized
    ):
        return "CONFIRMED"
    return "NOT_RECOVERED"


def _gap_status(value: object, *, fallback: str) -> str:
    status = _text(value).upper()
    if status in {"SOURCE_NOT_AVAILABLE", "NOT_RECOVERED", "AMBIGUOUS"}:
        return status
    return fallback


def _exact_local_reference_targets(
    source: InterpretationSource,
) -> dict[str, str]:
    """Resolve only exact function references whose target is an Evidence graph ref."""

    graphs_by_ref = {str(graph["graph_ref"]): graph for graph in source.graphs}
    nodes_by_ref = {str(node["node_ref"]): node for node in source.nodes}
    resolved: dict[str, str] = {}
    for reference in source.references:
        reference_ref = str(reference.get("reference_ref") or "")
        target_ref = _text(reference.get("target_ref"))
        target_graph = graphs_by_ref.get(target_ref)
        source_graph = graphs_by_ref.get(str(reference.get("graph_ref") or ""))
        source_node = nodes_by_ref.get(str(reference.get("node_ref") or ""))
        if (
            not reference_ref
            or _text(reference.get("kind")).casefold() != "function"
            or target_graph is None
            or _combined_provenance_status(reference) != "CONFIRMED"
            or _combined_provenance_status(target_graph) != "CONFIRMED"
            or _combined_provenance_status(source_graph, source_node) != "CONFIRMED"
        ):
            continue
        resolved[reference_ref] = target_ref
    return resolved


def _gap(
    code: str,
    *,
    graph_ref: str,
    node_ref: str = "",
    detail: str,
    evidence_refs: Iterable[str] = (),
    status: str = "SOURCE_NOT_AVAILABLE",
    source: str = "INTERPRETER",
) -> dict[str, Any]:
    refs = sorted({str(ref) for ref in evidence_refs if str(ref).startswith("bp://")})
    projection = {
        "code": code,
        "graphRef": graph_ref,
        "nodeRef": node_ref,
        "detail": detail,
        "evidenceRefs": refs,
        "source": source,
    }
    return {
        "id": stable_id("gap://", projection),
        "code": code,
        "status": status,
        "graphRef": graph_ref,
        "nodeRef": node_ref,
        "pinRef": "",
        "detail": detail,
        "evidenceRefs": refs,
        "source": source,
    }


def _statement_text(node: dict[str, Any], kind: str) -> str:
    if kind == "EVENT":
        return "Event " + (_text(node.get("event_name")) or _text(node.get("label")))
    if kind == "BRANCH":
        return "Branch using exact exec Pin successors"
    if kind == "SET":
        return "Set " + (_text(node.get("variable_name")) or _text(node.get("label")))
    if kind == "RETURN":
        return "Return from graph"
    if kind == "DELEGATE":
        delegate_name = _text(node.get("delegate_name")) or _text(node.get("label"))
        operation = _delegate_operation(node)
        if operation == "INVOKE":
            return "Invoke delegate " + delegate_name
        if operation == "BIND":
            return "Bind delegate " + delegate_name
        return "Delegate operation " + delegate_name
    if kind == "LOOP":
        return "Macro/loop call " + (
            _text(node.get("macro_name")) or _text(node.get("label"))
        )
    node_type = _text(node.get("node_type") or node.get("class_name"))
    if "ExecutionSequence" in node_type:
        return "Sequence execution"
    if "DynamicCast" in node_type:
        return "Cast " + (_text(node.get("label")) or node_type)
    callable_name = (
        _text(node.get("function_name"))
        or _text(node.get("macro_name"))
        or _text(node.get("label"))
        or node_type
    )
    return "Call " + callable_name


def _node_statements(
    source: InterpretationSource,
    executable_node_refs: frozenset[str],
    control_graphs: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    graphs_by_ref = {str(graph["graph_ref"]): graph for graph in source.graphs}
    pins_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pin in source.pins:
        pins_by_node[str(pin["node_ref"])].append(pin)
    successor_evidence_by_node: dict[str, set[str]] = defaultdict(set)
    for graph in control_graphs:
        for control_node in graph.get("nodes", []):
            if not isinstance(control_node, dict):
                continue
            node_ref = str(control_node.get("nodeRef") or "")
            for successor in control_node.get("successors", []):
                if not isinstance(successor, dict):
                    continue
                successor_evidence_by_node[node_ref].update(
                    str(successor.get(key) or "")
                    for key in ("edgeRef", "sourcePinRef", "targetPinRef")
                    if str(successor.get(key) or "").startswith("bp://")
                )
    statements: list[dict[str, Any]] = []
    source_order = 0
    for node in source.nodes:
        node_ref = str(node["node_ref"])
        if node_ref not in executable_node_refs:
            continue
        kind = control_node_kind(node)
        graph_ref = str(node["graph_ref"])
        status = _combined_provenance_status(graphs_by_ref.get(graph_ref), node)
        evidence_refs = [node_ref]
        if kind == "BRANCH":
            evidence_refs.extend(
                str(pin["pin_ref"])
                for pin in pins_by_node[node_ref]
                if _text(pin.get("name")).casefold() == "condition"
            )
        evidence_refs.extend(successor_evidence_by_node[node_ref])
        text = _statement_text(node, kind)
        projection = {
            "kind": kind,
            "text": text,
            "graphRef": graph_ref,
            "nodeRef": node_ref,
            "evidenceRefs": sorted(evidence_refs),
        }
        statements.append(
            {
                "id": stable_id("statement://", projection),
                "kind": kind,
                "text": text,
                "status": status,
                "evidenceRefs": sorted(evidence_refs),
                "gapRefs": [],
                "graphRef": graph_ref,
                "nodeRef": node_ref,
                "sourceOrder": source_order,
            }
        )
        source_order += 1
    return statements


def _structural_gaps(source: InterpretationSource) -> list[dict[str, Any]]:
    refs_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for reference in source.references:
        refs_by_node[str(reference.get("node_ref") or "")].append(reference)
    local_targets = _exact_local_reference_targets(source)
    graph_refs = {str(graph["graph_ref"]) for graph in source.graphs}
    gaps: list[dict[str, Any]] = []
    for node in source.nodes:
        node_ref = str(node["node_ref"])
        graph_ref = str(node["graph_ref"])
        node_type = _text(node.get("node_type") or node.get("class_name"))
        macro_name = _text(node.get("macro_name"))
        callable_name = (
            _text(node.get("function_name"))
            or macro_name
            or _text(node.get("delegate_name"))
        )
        if "K2Node_Composite" in node_type:
            gaps.append(
                _gap(
                    "COLLAPSED_GRAPH_BODY_NOT_AVAILABLE",
                    graph_ref=graph_ref,
                    node_ref=node_ref,
                    detail="The collapsed graph node exists, but its body is not present in Evidence.",
                    evidence_refs=[node_ref],
                )
            )
        if macro_name or "MacroInstance" in node_type:
            exact_targets = [
                _text(row.get("target_ref"))
                for row in refs_by_node[node_ref]
                if _text(row.get("kind")).casefold() == "macro"
                and _text(row.get("target_ref")) in graph_refs
            ]
            if not exact_targets:
                gaps.append(
                    _gap(
                        "MACRO_BODY_NOT_AVAILABLE",
                        graph_ref=graph_ref,
                        node_ref=node_ref,
                        detail=f"Macro body is not present for {macro_name or node_type}.",
                        evidence_refs=[
                            node_ref,
                            *(
                                str(row["reference_ref"])
                                for row in refs_by_node[node_ref]
                                if row.get("reference_ref")
                            ),
                        ],
                    )
                )
        if "callfunction" in node_type.casefold() and not macro_name:
            exact_targets = [
                local_targets.get(str(row.get("reference_ref") or ""), "")
                for row in refs_by_node[node_ref]
                if local_targets.get(str(row.get("reference_ref") or ""), "")
            ]
            if not exact_targets:
                gaps.append(
                    _gap(
                        "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE",
                        graph_ref=graph_ref,
                        node_ref=node_ref,
                        detail=(
                            f"Only the exact call node for {callable_name or node_type} is available; "
                            "no callable body is bound by Evidence."
                        ),
                        evidence_refs=[
                            node_ref,
                            *(
                                str(row["reference_ref"])
                                for row in refs_by_node[node_ref]
                                if row.get("reference_ref")
                            ),
                        ],
                    )
                )
    reason_map = {
        "external_callable_body_not_in_asset": "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE",
        "macro_body_not_available": "MACRO_BODY_NOT_AVAILABLE",
        "collapsed_graph_body_not_available": "COLLAPSED_GRAPH_BODY_NOT_AVAILABLE",
    }
    for diagnostic in source.diagnostics:
        reason = _text(diagnostic.get("reason_code")).casefold()
        code = reason_map.get(reason)
        if code is None:
            continue
        evidence = [str(diagnostic["diagnostic_ref"])]
        raw_evidence = diagnostic.get("evidence")
        if isinstance(raw_evidence, list):
            evidence.extend(str(ref) for ref in raw_evidence if str(ref) in source.evidence_refs)
        gaps.append(
            _gap(
                code,
                graph_ref=_text(diagnostic.get("scope_ref")),
                detail=_text(diagnostic.get("detail")) or _text(diagnostic.get("title")),
                evidence_refs=evidence,
                status=_gap_status(
                    diagnostic.get("status"), fallback="SOURCE_NOT_AVAILABLE"
                ),
                source="EVIDENCE_DIAGNOSTIC",
            )
        )
    return gaps


def _default_gaps(source: InterpretationSource) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for default in source.defaults:
        provenance_status = _default_provenance_status(default)
        if provenance_status == "CONFIRMED":
            continue
        gap_status = (
            provenance_status
            if provenance_status
            in {"SOURCE_NOT_AVAILABLE", "NOT_RECOVERED", "AMBIGUOUS"}
            else "NOT_RECOVERED"
        )
        default_ref = str(default["default_ref"])
        gaps.append(
            _gap(
                "DEFAULT_NOT_RECOVERED",
                graph_ref="",
                detail=(
                    "Class Default is not confirmed by exact provenance: "
                    f"name={_text(default.get('name'))}; "
                    f"provenance={provenance_status}."
                ),
                evidence_refs=[default_ref],
                status=gap_status,
                source="CLASS_DEFAULT_PROVENANCE",
            )
        )
    return gaps


def _heuristic_hints(source: InterpretationSource) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for node in source.nodes:
        haystack = " ".join(
            _text(node.get(key))
            for key in (
                "name",
                "label",
                "function_name",
                "variable_name",
                "event_name",
                "delegate_name",
                "macro_name",
                "comment",
            )
        ).casefold()
        for topic, keywords in _HINT_KEYWORDS.items():
            matches = sorted({keyword for keyword in keywords if keyword in haystack})
            if not matches:
                continue
            node_ref = str(node["node_ref"])
            projection = {"topic": topic, "nodeRef": node_ref, "matches": matches}
            hints.append(
                {
                    "id": stable_id("hint://", projection),
                    "topic": topic,
                    "text": f"Review {topic} because name/comment keywords matched: {', '.join(matches)}.",
                    "basis": "KEYWORD_AND_NAME_HEURISTIC",
                    "confidence": "HEURISTIC",
                    "notEvidence": True,
                    "reviewRef": node_ref,
                }
            )
    return sorted(hints, key=lambda row: str(row["id"]))


def _asset_summary(
    source: InterpretationSource,
    gaps: list[dict[str, Any]],
    *,
    emitted_edge_refs: frozenset[str],
) -> dict[str, Any]:
    graphs_by_ref = {str(graph["graph_ref"]): graph for graph in source.graphs}

    def node_is_confirmed(node: dict[str, Any]) -> bool:
        return (
            _combined_provenance_status(
                graphs_by_ref.get(str(node.get("graph_ref") or "")),
                node,
            )
            == "CONFIRMED"
        )

    graph_statuses = Counter(_text(row.get("status")) or "UNKNOWN" for row in source.graphs)
    events = [
        {"nodeRef": str(row["node_ref"]), "name": _text(row.get("event_name") or row.get("label"))}
        for row in source.nodes
        if control_node_kind(row) == "EVENT" and node_is_confirmed(row)
    ]
    variable_reads = [
        {"nodeRef": str(row["node_ref"]), "name": _text(row.get("variable_name"))}
        for row in source.nodes
        if "VariableGet" in _text(row.get("node_type")) and node_is_confirmed(row)
    ]
    variable_writes = [
        {"nodeRef": str(row["node_ref"]), "name": _text(row.get("variable_name"))}
        for row in source.nodes
        if "VariableSet" in _text(row.get("node_type")) and node_is_confirmed(row)
    ]
    local_targets = _exact_local_reference_targets(source)
    confirmed_local_calls = [
        {
            "referenceRef": str(row["reference_ref"]),
            "targetRef": local_targets[str(row["reference_ref"])],
            "name": _text(row.get("name")),
            "evidenceRefs": [
                str(row["reference_ref"]),
                local_targets[str(row["reference_ref"])],
            ],
        }
        for row in source.references
        if _text(row.get("kind")).casefold() == "function"
        and str(row["reference_ref"]) in local_targets
    ]
    external = [
        {
            "gapId": str(gap["id"]),
            "graphRef": str(gap["graphRef"]),
            "nodeRef": str(gap.get("nodeRef") or ""),
        }
        for gap in gaps
        if gap["code"] == "EXTERNAL_CALLABLE_BODY_NOT_AVAILABLE"
    ]
    return {
        "assetName": _text(source.identity.get("asset_name")),
        "graphCount": len(source.graphs),
        "nodeCount": len(source.nodes),
        "pinCount": len(source.pins),
        "edgeCount": len(emitted_edge_refs),
        "graphInventory": [
            {
                "graphRef": str(row["graph_ref"]),
                "name": _text(row.get("name")),
                "graphType": _text(row.get("graph_type")),
                "status": _text(row.get("status")),
                "confidence": _text(row.get("confidence")),
                "nodeCount": int(row.get("node_count") or 0),
                "pinCount": int(row.get("pin_count") or 0),
                "coverage": row.get("coverage") or {},
            }
            for row in source.graphs
        ],
        "graphStatusCounts": dict(sorted(graph_statuses.items())),
        "entries": events,
        "variableReads": variable_reads,
        "variableWrites": variable_writes,
        "confirmedLocalCalls": confirmed_local_calls,
        "externalOrMissingCallableBodies": external,
        "delegateBindings": [
            {"nodeRef": str(row["node_ref"]), "name": _text(row.get("delegate_name"))}
            for row in source.nodes
            if row.get("delegate_name")
            and _delegate_operation(row) == "BIND"
            and node_is_confirmed(row)
        ],
        "macros": [
            {"nodeRef": str(row["node_ref"]), "name": _text(row.get("macro_name"))}
            for row in source.nodes
            if row.get("macro_name") and node_is_confirmed(row)
        ],
        "classDefaults": [
            {
                "defaultRef": str(row["default_ref"]),
                "name": _text(row.get("name")),
                "type": _text(row.get("type_name")),
                "value": row.get("value"),
            }
            for row in source.defaults
            if _default_provenance_status(row) == "CONFIRMED"
        ],
        "diagnosticGapCount": len(gaps),
    }


def _validate_statement_evidence(
    statements: list[dict[str, Any]],
    evidence_refs: frozenset[str],
) -> None:
    ids: set[str] = set()
    for statement in statements:
        statement_id = str(statement["id"])
        if statement_id in ids:
            raise InterpretationPublicationError(
                "STATEMENT_ID_DUPLICATE",
                "Interpretation produced duplicate statement identities.",
            )
        ids.add(statement_id)
        if statement.get("kind") not in STATEMENT_KINDS:
            raise InterpretationPublicationError(
                "STATEMENT_KIND_INVALID",
                "Interpretation produced a statement kind outside the v1 contract.",
            )
        if statement.get("status") not in STATEMENT_STATUSES:
            raise InterpretationPublicationError(
                "STATEMENT_STATUS_INVALID",
                "Interpretation produced a statement status outside the v1 contract.",
            )
        refs = list(statement.get("evidenceRefs") or [])
        if any(str(ref) not in evidence_refs for ref in refs):
            raise InterpretationPublicationError(
                "STATEMENT_EVIDENCE_INVALID",
                "Interpretation produced a statement with a non-existent Evidence ref.",
            )
        if statement["status"] == "CONFIRMED" and not refs:
            raise InterpretationPublicationError(
                "CONFIRMED_STATEMENT_WITHOUT_EVIDENCE",
                "A confirmed statement is not backed by exact Evidence refs.",
            )


def _build_from_source(source: InterpretationSource, *, budget: int) -> InterpretationBuild:
    try:
        effective_budget = int(budget)
    except (TypeError, ValueError) as exc:
        raise ValueError("INTERPRETATION_BUDGET_INVALID: budget must be an integer") from exc
    if effective_budget <= 0 or effective_budget > 100_000:
        raise ValueError("INTERPRETATION_BUDGET_INVALID: budget must be between 1 and 100000")
    source_work_units = sum(
        len(rows) * weight
        for rows, weight in (
            (source.graphs, 64),
            (source.nodes, 256),
            (source.pins, 96),
            (source.edges, 192),
            (source.observations, 96),
            (source.references, 64),
            (source.defaults, 64),
            (source.diagnostics, 96),
            (source.coverage, 32),
        )
    )
    if source_work_units > effective_budget:
        raise ValueError(
            "INTERPRETATION_BUDGET_EXCEEDED: "
            f"source_work_units={source_work_units} budget={effective_budget}"
        )
    control = build_control_flow(source)
    data = build_data_flow(source)
    emitted_edge_refs = frozenset(
        {
            str(successor["edgeRef"])
            for graph in control.graphs
            for node in graph.get("nodes", [])
            for successor in node.get("successors", [])
            if str(successor.get("edgeRef") or "")
        }
        | {
            str(edge["edgeRef"])
            for graph in data.graphs
            for edge in graph.get("edges", [])
            if str(edge.get("edgeRef") or "")
        }
    )
    combined_gaps = [
        *control.gaps,
        *data.gaps,
        *_structural_gaps(source),
        *_default_gaps(source),
    ]
    gaps_by_id = {str(gap["id"]): gap for gap in combined_gaps}
    gaps = sorted(gaps_by_id.values(), key=lambda row: str(row["id"]))
    statements = _node_statements(
        source,
        control.executable_node_refs,
        control.graphs,
    )
    source_order = len(statements)
    for gap in gaps:
        projection = {
            "kind": "GAP",
            "gapId": gap["id"],
            "graphRef": gap["graphRef"],
            "evidenceRefs": gap["evidenceRefs"],
        }
        statements.append(
            {
                "id": stable_id("statement://", projection),
                "kind": "GAP",
                "text": f"{gap['code']}: {gap['detail']}",
                "status": gap["status"],
                "evidenceRefs": list(gap["evidenceRefs"]),
                "gapRefs": [gap["id"]],
                "graphRef": gap["graphRef"],
                "nodeRef": gap.get("nodeRef") or "",
                "sourceOrder": source_order,
            }
        )
        source_order += 1
    _validate_statement_evidence(statements, source.evidence_refs)
    hints = _heuristic_hints(source)
    evidence_revision_id = _text(source.identity["revision_id"])
    evidence_manifest_sha256 = _text(source.state.manifest_sha256)
    generated_at = _text(
        source.evidence_manifest.get("generatedAt")
        or source.identity.get("generated_at")
    )
    semantic_projection = {
        "schema": INTERPRETATION_SCHEMA,
        "assetId": _text(source.identity["asset_id"]),
        "objectPath": _text(source.identity["object_path"]),
        "evidenceRevisionId": evidence_revision_id,
        "evidenceManifestSha256": evidence_manifest_sha256,
        "interpreterVersion": INTERPRETER_VERSION,
        "schemaVersion": INTERPRETATION_SCHEMA,
        "selection": {"graphRefs": []},
        "assetSummary": _asset_summary(
            source,
            gaps,
            emitted_edge_refs=emitted_edge_refs,
        ),
        "controlFlow": {"graphs": list(control.graphs)},
        "dataFlow": {
            "graphs": list(data.graphs),
            "sharedExpressions": list(data.shared_expressions),
            "classDefaultRefs": [
                {
                    "defaultRef": str(row["default_ref"]),
                    "name": _text(row.get("name")),
                    "type": _text(row.get("type_name")),
                    "value": row.get("value"),
                }
                for row in source.defaults
                if _default_provenance_status(row) == "CONFIRMED"
            ],
            "componentRefs": [
                {
                    "referenceRef": str(row["reference_ref"]),
                    "graphRef": str(row["graph_ref"]),
                    "nodeRef": str(row["node_ref"]),
                    "targetRef": _text(row.get("target_ref")),
                }
                for row in source.references
                if "component"
                in {
                    _text(row.get("kind")).casefold(),
                    _text(row.get("classification")).casefold(),
                }
            ],
        },
        "statements": statements,
        "heuristicReviewHints": hints,
        "gaps": gaps,
    }
    digest = semantic_digest(semantic_projection)
    interpretation = {
        **{key: value for key, value in semantic_projection.items() if key != "gaps"},
        "semanticDigest": digest,
        "generatedAt": generated_at,
    }
    gaps_document = gaps_payload(interpretation, gaps)
    pseudocode, trace = render_pseudocode_and_trace(interpretation, gaps)
    markdown = render_markdown(interpretation, gaps)
    estimated_tokens = (
        len(canonical_json_bytes(semantic_projection))
        + len(markdown.encode("utf-8"))
        + len(pseudocode.encode("utf-8"))
    ) // 4
    if estimated_tokens > effective_budget:
        raise ValueError(
            "INTERPRETATION_BUDGET_EXCEEDED: "
            f"estimated={estimated_tokens} budget={effective_budget}"
        )
    return InterpretationBuild(
        interpretation=interpretation,
        markdown=markdown,
        trace=trace,
        gaps=gaps_document,
        pseudocode=pseudocode,
        semantic_digest=digest,
        evidence_state=source.state,
    )


def build_interpretation(
    asset_dir: str | Path,
    *,
    budget: int = 20_000,
    allow_stale: bool = False,
    allow_legacy_fallback: bool = False,
) -> InterpretationBuild:
    source = load_interpretation_source(
        asset_dir,
        allow_stale=allow_stale,
        allow_legacy_fallback=allow_legacy_fallback,
    )
    return _build_from_source(source, budget=budget)


__all__ = ["build_interpretation"]
