from __future__ import annotations

import html
import json
from typing import Any

from .contracts import GAPS_SCHEMA, PSEUDOCODE_HEADER, TRACE_SCHEMA


def _markdown(value: object) -> str:
    text = html.escape(str(value or ""), quote=True)
    for character in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        text = text.replace(character, "\\" + character)
    return text.replace("\r", " ").replace("\n", " ")


def _quoted(value: object) -> str:
    # Pseudocode is a review artifact, not an execution format.  Keep every
    # untrusted non-ASCII/control character in JSON escape form and also escape
    # ASCII metacharacters that could be interpreted as HTML, Markdown, or a
    # shell expression if a line is copied into another tool.
    rendered = json.dumps(str(value or ""), ensure_ascii=True)
    for character in "<>$`(){}[];&|!*_#~":
        rendered = rendered.replace(character, f"\\u{ord(character):04x}")
    return rendered


def render_markdown(interpretation: dict[str, Any], gaps: list[dict[str, Any]]) -> str:
    summary = interpretation["assetSummary"]
    statements = list(interpretation["statements"])
    confirmed = [row for row in statements if row["status"] == "CONFIRMED"]
    nonconfirmed = [row for row in statements if row["status"] != "CONFIRMED"]
    hints = list(interpretation["heuristicReviewHints"])
    lines = [
        f"# Blueprint Interpretation: {_markdown(summary.get('assetName'))}",
        "",
        "> Evidence-derived interpretation. It is not original C++, and it does not fill missing callable bodies.",
        "",
        "## Identity",
        "",
        f"- Asset ID: `{_markdown(interpretation['assetId'])}`",
        f"- Object Path: `{_markdown(interpretation['objectPath'])}`",
        f"- Evidence revision: `{_markdown(interpretation['evidenceRevisionId'])}`",
        f"- Evidence manifest: `{_markdown(interpretation['evidenceManifestSha256'])}`",
        f"- Semantic digest: `{_markdown(interpretation['semanticDigest'])}`",
        "",
        "## Asset summary",
        "",
        f"- Graphs: {summary['graphCount']}",
        f"- Nodes: {summary['nodeCount']}",
        f"- Pins: {summary['pinCount']}",
        f"- Exact edges: {summary['edgeCount']}",
        f"- Diagnostic gaps: {summary['diagnosticGapCount']}",
        "",
        "## Confirmed statements",
        "",
    ]
    if confirmed:
        for statement in confirmed:
            refs = ", ".join(f"`{_markdown(ref)}`" for ref in statement["evidenceRefs"])
            lines.append(
                f"- **{_markdown(statement['kind'])}** {_markdown(statement['text'])} "
                f"— {refs} (`{_markdown(statement['id'])}`)"
            )
    else:
        lines.append("- No confirmed executable statements were recovered.")
    lines.extend(["", "## Explicit gaps", ""])
    if gaps:
        for gap in gaps:
            lines.append(
                f"- **{_markdown(gap['code'])}** [{_markdown(gap['status'])}] "
                f"{_markdown(gap['detail'])} (`{_markdown(gap['id'])}`)"
            )
    else:
        lines.append("- No interpretation gaps were recorded.")
    if nonconfirmed:
        lines.extend(["", "## Non-confirmed statements", ""])
        for statement in nonconfirmed:
            lines.append(
                f"- **{_markdown(statement['status'])}** {_markdown(statement['text'])} "
                f"(`{_markdown(statement['id'])}`)"
            )
    lines.extend(["", "## Heuristic review hints (not evidence)", ""])
    if hints:
        for hint in hints:
            lines.append(
                f"- {_markdown(hint['topic'])}: {_markdown(hint['text'])} "
                "— `basis=KEYWORD_AND_NAME_HEURISTIC`, `confidence=HEURISTIC`, `notEvidence=true`"
            )
    else:
        lines.append("- No keyword/name review hints were produced.")
    lines.extend(
        [
            "",
            "## Pseudocode contract",
            "",
            f"`{_markdown(PSEUDOCODE_HEADER)}`",
            "",
            "Every executable pseudocode line is mapped to a statement and exact Evidence refs in `trace.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def _statement_line(
    statement: dict[str, Any],
    control_node: dict[str, Any] | None,
    block_labels: dict[str, str],
) -> str:
    kind = str(statement["kind"])
    text = str(statement["text"])
    if kind == "EVENT":
        expression = f"EVENT {_quoted(text)}"
    elif kind == "BRANCH":
        successors = list((control_node or {}).get("successors") or [])
        labeled_targets = [
            (
                str(row.get("sourcePinName") or ""),
                block_labels.get(
                    str(row.get("targetNodeRef") or ""), "L_UNRESOLVED"
                ),
            )
            for row in successors
        ]
        true_targets = [
            target
            for pin_name, target in labeled_targets
            if pin_name.strip().casefold() in {"then", "true"}
        ]
        false_targets = [
            target
            for pin_name, target in labeled_targets
            if pin_name.strip().casefold() in {"else", "false"}
        ]
        if len(true_targets) == 1 and len(false_targets) == 1:
            expression = (
                f"BRANCH <condition> ? GOTO {true_targets[0]} : "
                f"GOTO {false_targets[0]}"
            )
        elif labeled_targets:
            expression = "BRANCH <condition> | " + " | ".join(
                f"PIN {_quoted(pin_name)} -> GOTO {target}"
                for pin_name, target in labeled_targets
            )
        else:
            expression = "BRANCH <successor-not-recovered>"
    elif kind == "SET":
        expression = f"SET {_quoted(text)} = <expression-not-recovered>"
    elif kind == "RETURN":
        expression = "RETURN <recovered-output-or-placeholder>"
    elif kind == "DELEGATE":
        expression = f"DELEGATE {_quoted(text)}"
    elif kind == "LOOP":
        expression = f"MACRO_OR_LOOP {_quoted(text)} <body-not-expanded>"
    elif kind == "GAP":
        expression = f"GAP {_quoted(text)}"
    else:
        expression = f"CALL {_quoted(text)}"
    return f"  {expression};  // {statement['id']}"


def render_pseudocode_and_trace(
    interpretation: dict[str, Any],
    gaps: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    statements = list(interpretation["statements"])
    statement_by_node = {
        str(row.get("nodeRef") or ""): row
        for row in statements
        if row.get("nodeRef") and row.get("kind") != "GAP"
    }
    control_graphs = list(interpretation["controlFlow"]["graphs"])
    lines: list[tuple[str, bool, str, list[str]]] = [
        (PSEUDOCODE_HEADER, False, "", []),
        ("", False, "", []),
    ]
    emitted_statements: set[str] = set()
    for graph in control_graphs:
        graph_ref = str(graph["graphRef"])
        lines.append((f"GRAPH {_quoted(graph['name'])}  // {graph_ref}", False, "", [graph_ref]))
        node_to_control = {
            str(row["nodeRef"]): row for row in graph.get("nodes", [])
        }
        block_labels = {
            str(node_ref): str(block["label"])
            for block in graph.get("basicBlocks", [])
            for node_ref in block.get("nodeRefs", [])
        }
        for block in graph.get("basicBlocks", []):
            lines.append(
                (
                    f"{block['label']}:",
                    False,
                    "",
                    [str(ref) for ref in block.get("nodeRefs", [])],
                )
            )
            for node_ref in block.get("nodeRefs", []):
                statement = statement_by_node.get(str(node_ref))
                if statement is None:
                    continue
                statement_id = str(statement["id"])
                lines.append(
                    (
                        _statement_line(
                            statement,
                            node_to_control.get(str(node_ref)),
                            block_labels,
                        ),
                        True,
                        statement_id,
                        list(statement["evidenceRefs"]),
                    )
                )
                emitted_statements.add(statement_id)
        lines.append(("", False, "", []))

    gap_statements = [
        row
        for row in statements
        if row["kind"] == "GAP" and str(row["id"]) not in emitted_statements
    ]
    if gap_statements:
        lines.append(("EXPLICIT_GAPS:", False, "", []))
        for statement in gap_statements:
            lines.append(
                (
                    _statement_line(statement, None, {}),
                    True,
                    str(statement["id"]),
                    list(statement["evidenceRefs"]),
                )
            )

    trace_rows: list[dict[str, Any]] = []
    rendered: list[str] = []
    byte_offset = 0
    for line_number, (line, executable, statement_id, evidence_refs) in enumerate(lines, start=1):
        line_bytes = line.encode("utf-8")
        trace_rows.append(
            {
                "line": line_number,
                "startByte": byte_offset,
                "endByte": byte_offset + len(line_bytes),
                "executable": executable,
                "statementId": statement_id,
                "evidenceRefs": evidence_refs,
            }
        )
        rendered.append(line)
        byte_offset += len(line_bytes) + 1
    pseudocode = "\n".join(rendered) + "\n"
    statement_trace = [
        {
            "statementId": str(statement["id"]),
            "graphRef": str(statement.get("graphRef") or ""),
            "nodeRef": str(statement.get("nodeRef") or ""),
            "evidenceRefs": list(statement["evidenceRefs"]),
            "gapRefs": list(statement["gapRefs"]),
        }
        for statement in statements
    ]
    trace = {
        "schema": TRACE_SCHEMA,
        "assetId": interpretation["assetId"],
        "evidenceRevisionId": interpretation["evidenceRevisionId"],
        "evidenceManifestSha256": interpretation["evidenceManifestSha256"],
        "semanticDigest": interpretation["semanticDigest"],
        "statements": statement_trace,
        "pseudocodeLines": trace_rows,
    }
    return pseudocode, trace


def gaps_payload(interpretation: dict[str, Any], gaps: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for gap in gaps:
        code = str(gap["code"])
        counts[code] = counts.get(code, 0) + 1
    return {
        "schema": GAPS_SCHEMA,
        "assetId": interpretation["assetId"],
        "evidenceRevisionId": interpretation["evidenceRevisionId"],
        "evidenceManifestSha256": interpretation["evidenceManifestSha256"],
        "semanticDigest": interpretation["semanticDigest"],
        "counts": dict(sorted(counts.items())),
        "items": gaps,
    }


__all__ = [
    "gaps_payload",
    "render_markdown",
    "render_pseudocode_and_trace",
]
