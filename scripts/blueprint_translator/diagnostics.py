"""Human-readable diagnostics for Blueprint parser uncertainty."""

from __future__ import annotations

from typing import Iterable

from .utils import table_row

def render_diagnostics(payload: dict[str, object]) -> str:
    diagnostics = payload["diagnostics"]
    lines = ["## Confidence And Uncertainty", ""]
    lines.append(f"- confidence_level: {diagnostics['confidence_level']}")
    for key in ("unsupported_node_types", "unresolved_links", "missing_link_map", "orphan_nodes", "missing_entry_points", "pins_with_unknown_source", "assumptions", "warnings"):
        value = diagnostics.get(key)
        lines.append(f"- {key}:")
        if isinstance(value, list):
            if value:
                for item in value[:30]:
                    lines.append(f"  - {item}")
            else:
                lines.append("  - none")
        else:
            lines.append(f"  - {value}")
    lines.append("")
    return "\n".join(lines)


def diagnostic_finding(
    code: str,
    severity: str,
    title: str,
    detail: str,
    evidence: Iterable[object] = (),
    next_action: str = "",
) -> dict[str, object]:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": [str(item) for item in evidence if str(item)],
        "next_action": next_action,
    }


def node_labels_from_payload(payload: dict[str, object], key: str) -> list[str]:
    return [str(node.get("label") or node.get("name") or node.get("node_type") or "") for node in payload.get(key, [])]


def is_confirmed_empty_uasset_graph(metadata: dict[str, object], node_count: int) -> bool:
    if node_count != 0:
        return False
    if str(metadata.get("source_kind") or "") != "uasset_binary":
        return False
    return str(metadata.get("uasset_read_status") or "") in {"complete", "complete_empty"}


def asset_likely_needs_component_context(asset_name: str) -> bool:
    lowered = str(asset_name or "").lower()
    if not lowered:
        return True
    if lowered.startswith(("primalitem", "primalgamedata", "core_primalgamedata", "base_primalgamedata")):
        return False
    if "statuscomponent" in lowered:
        return False
    return True


def build_diagnostic_findings(payload: dict[str, object]) -> list[dict[str, object]]:
    diagnostics = payload.get("diagnostics", {})
    metadata = payload.get("metadata", {})
    context = payload.get("context", {})
    findings: list[dict[str, object]] = []

    node_count = int(metadata.get("node_count") or 0)
    empty_uasset_graph = is_confirmed_empty_uasset_graph(metadata if isinstance(metadata, dict) else {}, node_count)
    if node_count == 0 and not empty_uasset_graph:
        findings.append(
            diagnostic_finding(
                "BP000",
                "error",
                "No Blueprint nodes were parsed",
                "The input did not contain recognizable Begin Object / End Object Blueprint node blocks.",
                [f"source={metadata.get('source', '')}", f"raw_characters={metadata.get('raw_characters', 0)}"],
                "Copy nodes from the Blueprint graph with Ctrl+C, or pass a text file that contains Unreal Blueprint node export text.",
            )
        )

    if diagnostics.get("missing_entry_points") and node_count > 0:
        findings.append(
            diagnostic_finding(
                "BP001",
                "warning",
                "No execution entry point was found",
                "The copied selection does not include an Event, Custom Event, or Function Entry node, so execution order may be incomplete.",
                node_labels_from_payload(payload, "events"),
                "Re-copy the graph including the entry node, or label this input as a partial selection in notes.",
            )
        )

    unsupported = list(diagnostics.get("unsupported_node_types", []))
    if unsupported:
        findings.append(
            diagnostic_finding(
                "BP010",
                "warning",
                "Unsupported node types were encountered",
                "These node classes are parsed structurally, but the semantic dictionary does not yet explain their behavior.",
                unsupported,
                "Add semantics for these node types before trusting pseudocode or gameplay explanations for this graph.",
            )
        )

    unresolved = list(diagnostics.get("unresolved_links", []))
    if unresolved:
        missing_map = [item for item in diagnostics.get("missing_link_map", []) if isinstance(item, dict)]
        if missing_map:
            missing_targets = [
                        f"{item.get('target_node')} [{item.get('target_kind', 'node')}] ({', '.join(str(value) for value in item.get('link_types', []))}) - "
                + "; ".join(str(value) for value in item.get("impact", [])[:3])
                for item in missing_map
            ]
        else:
            missing_targets = sorted({str(link.get("target_node", "")) for link in unresolved if isinstance(link, dict)})
        findings.append(
            diagnostic_finding(
                "BP020",
                "warning",
                "LinkedTo targets are missing from the copied selection",
                "Some exec or data links point to nodes that were not present in the input, so flow reconstruction is partial.",
                missing_targets[:40],
                "Copy the listed missing nodes or the whole graph page. If a target belongs to another function/macro/event page, add that graph to --asset-dir.",
            )
        )

    unknown_pins = list(diagnostics.get("pins_with_unknown_source", []))
    if unknown_pins:
        pins = [f"{item.get('node_label', item.get('node', ''))}.{item.get('pin', '')}" for item in unknown_pins if isinstance(item, dict)]
        findings.append(
            diagnostic_finding(
                "BP030",
                "warning",
                "Input pins have no visible source or default",
                "These values may come from class defaults, component defaults, inherited data, or nodes outside the copied selection.",
                pins[:40],
                "Provide --defaults-file, --components-file, --notes-file, or re-copy a wider node selection.",
            )
        )

    variable_gets = node_labels_from_payload(payload, "variable_gets")
    if variable_gets and not str(context.get("defaults_text", "")).strip():
        findings.append(
            diagnostic_finding(
                "BP040",
                "info",
                "Variable reads cannot be checked against class defaults",
                "The graph reads Blueprint variables, but no class default context was supplied.",
                variable_gets[:40],
                "Export or write a defaults sidecar and pass it with --defaults-file.",
            )
        )

    defaults_parse_error = str(payload.get("class_defaults", {}).get("parse_error", "")).strip()
    if defaults_parse_error:
        findings.append(
            diagnostic_finding(
                "BP042",
                "warning",
                "Class defaults sidecar could not be fully parsed",
                "The defaults sidecar was present, but it was not valid JSON and only simple name/value lines could be recovered.",
                [defaults_parse_error],
                "Prefer defaults.json with a variables object, for example {\"variables\":{\"FeedingRange\":3000}}.",
            )
        )

    needs_components = asset_likely_needs_component_context(str(metadata.get("asset_name") or ""))
    if not str(context.get("components_text", "")).strip():
        if needs_components:
            findings.append(
                diagnostic_finding(
                    "BP041",
                    "info",
                    "Component defaults are not available",
                    "ARK Blueprint behavior is often driven by component configuration, but no component sidecar was supplied.",
                    [],
                    "Export or write a component sidecar and pass it with --components-file.",
                )
            )
    if str(context.get("components_text", "")).strip() and needs_components:
        component_context = payload.get("component_defaults", {})
        component_parse_error = str(component_context.get("parse_error", "")).strip() if isinstance(component_context, dict) else ""
        parsed_components = component_context.get("components", []) if isinstance(component_context, dict) else []
        if component_parse_error:
            findings.append(
                diagnostic_finding(
                    "BP043",
                    "warning",
                    "Component sidecar could not be fully parsed",
                    "The component sidecar was present, but it was not valid JSON and only simple text patterns could be recovered.",
                    [component_parse_error],
                    "Prefer components.json with component name, class, and defaults/properties fields.",
                )
            )
        if not parsed_components:
            findings.append(
                diagnostic_finding(
                    "BP044",
                    "warning",
                    "Component sidecar did not contain recognizable components",
                    "Component context was supplied, but no component names/classes/defaults could be extracted.",
                    [],
                    "Use components.json, or write text lines such as Inventory: PrimalInventoryComponent and Inventory.MaxItems=100.",
                )
            )

    graph_name = str(metadata.get("graph_name", "")).strip()
    if not graph_name:
        findings.append(
            diagnostic_finding(
                "BP050",
                "info",
                "Graph name was not provided",
                "Reports cannot distinguish EventGraph, function graphs, macro graphs, or construction scripts without a graph name.",
                [],
                "Pass --graph-name, or use the planned asset-dir workflow where each graph file is named explicitly.",
            )
        )

    orphan_nodes = list(diagnostics.get("orphan_nodes", []))
    if orphan_nodes:
        labels = [str(item.get("label") or item.get("name") or item) for item in orphan_nodes if isinstance(item, dict)]
        findings.append(
            diagnostic_finding(
                "BP060",
                "info",
                "Disconnected nodes were found",
                "These nodes have no parsed links and may be comments, incomplete selections, disabled/dead logic, or separate graph islands.",
                labels[:40],
                "Inspect whether these nodes are intentional isolated helpers or missing incoming/outgoing connections.",
            )
        )

    if diagnostics.get("confidence_level") == "low" and not any(item["severity"] == "error" for item in findings):
        findings.append(
            diagnostic_finding(
                "BP900",
                "warning",
                "Overall parse confidence is low",
                "The graph was parsed, but the number or severity of missing semantics and missing links makes the analysis risky.",
                [f"confidence={diagnostics.get('confidence_level')}"],
                "Use the findings above to add missing graph pages, sidecar defaults, component context, or node semantics.",
            )
        )

    return findings


def diagnostic_counts(findings: list[dict[str, object]]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for item in findings:
        severity = str(item.get("severity", "info"))
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def render_diagnostics_report(payload: dict[str, object]) -> str:
    findings = build_diagnostic_findings(payload)
    counts = diagnostic_counts(findings)
    metadata = payload.get("metadata", {})
    diagnostics = payload.get("diagnostics", {})
    lines = [
        "# Blueprint Diagnostics Report",
        "",
        "## Summary",
        "",
        f"- Source: {metadata.get('source', '-')}",
        f"- Asset: {metadata.get('asset_name') or '-'}",
        f"- Graph: {metadata.get('graph_name') or '-'}",
        f"- Nodes: {metadata.get('node_count', 0)}",
        f"- Pins: {metadata.get('pin_count', 0)}",
        f"- Links: {metadata.get('link_count', 0)}",
        f"- Confidence: {diagnostics.get('confidence_level', '-')}",
        f"- Findings: {counts.get('error', 0)} error, {counts.get('warning', 0)} warning, {counts.get('info', 0)} info",
        "",
    ]
    if not findings:
        lines.extend(["## Findings", "", "- No diagnostic findings.", ""])
    else:
        lines.extend(["## Findings", ""])
        for item in findings:
            lines.append(f"### [{str(item.get('severity', 'info')).upper()}] {item.get('code')} - {item.get('title')}")
            lines.append("")
            lines.append(str(item.get("detail", "")))
            evidence = list(item.get("evidence", []))
            if evidence:
                lines.append("")
                lines.append("Evidence:")
                for value in evidence[:50]:
                    lines.append(f"- {value}")
            next_action = str(item.get("next_action", "")).strip()
            if next_action:
                lines.append("")
                lines.append(f"Next action: {next_action}")
            lines.append("")

    assumptions = diagnostics.get("assumptions", [])
    if assumptions:
        lines.extend(["## Parser Assumptions", ""])
        lines.extend(f"- {item}" for item in assumptions)
        lines.append("")

    warnings = diagnostics.get("warnings", [])
    if warnings:
        lines.extend(["## Raw Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")

    missing_map = [item for item in diagnostics.get("missing_link_map", []) if isinstance(item, dict)]
    if missing_map:
        lines.extend(["## Missing LinkedTo Target Map", ""])
        lines.append(table_row(["Missing Target", "Kind", "Link Types", "Referenced From", "Impact", "Copy Hint"]))
        lines.append(table_row(["---", "---", "---", "---", "---", "---"]))
        for item in missing_map:
            references = []
            for ref in item.get("references", [])[:6]:
                if isinstance(ref, dict):
                    references.append(f"{ref.get('source_label')}.{ref.get('source_pin')}")
            lines.append(
                table_row(
                    [
                        item.get("target_node", ""),
                        item.get("target_kind", "node"),
                        ", ".join(str(value) for value in item.get("link_types", [])),
                        "; ".join(references),
                        "; ".join(str(value) for value in item.get("impact", [])[:4]),
                        item.get("copy_hint", ""),
                    ]
                )
            )
        lines.append("")

    return "\n".join(lines)


def diagnostics_payload(payload: dict[str, object]) -> dict[str, object]:
    findings = build_diagnostic_findings(payload)
    return {
        "metadata": payload.get("metadata", {}),
        "diagnostics": payload.get("diagnostics", {}),
        "counts": diagnostic_counts(findings),
        "findings": findings,
    }
