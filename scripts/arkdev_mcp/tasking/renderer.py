"""Human-readable Markdown rendering for stored Blueprint Patch Plans."""

from __future__ import annotations

from collections.abc import Mapping


def _endpoint(value: object) -> str:
    if not isinstance(value, Mapping) or not isinstance(value.get("pin"), Mapping):
        return "<invalid endpoint>"
    pin = value["pin"]
    pin_ref = str(pin.get("pinRef") or "proposed")
    return (
        f"{value.get('node', '')} :: {pin_ref} "
        f"[{pin.get('name', '')} {pin.get('direction', '')} #{pin.get('ordinal', '')}]"
    )


def render_patch_plan(plan: Mapping[str, object]) -> str:
    target = plan.get("target") if isinstance(plan.get("target"), Mapping) else {}
    operations = plan.get("operations") if isinstance(plan.get("operations"), list) else []
    lines = [
        "# Blueprint Patch Plan",
        "",
        f"- planId: `{plan.get('planId', '')}`",
        f"- taskId: `{plan.get('taskId', '')}`",
        f"- status: `{plan.get('status', '')}`",
        f"- Evidence revision: `{target.get('evidenceRevisionId', '')}`",
        f"- semanticDigest: `{plan.get('semanticDigest', '')}`",
        "- executionReady: false",
        "- reason: `EDITOR_BRIDGE_NOT_INSTALLED`",
        "- typeCompatibilityClaim: false",
        "",
        "## Target graphs",
        "",
    ]
    lines.extend(f"- `{graph_ref}`" for graph_ref in target.get("graphRefs", []))
    lines.extend(["", "## Operations", ""])
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        operation_id = str(operation.get("operationId") or "")
        kind = str(operation.get("kind") or "")
        lines.append(f"### {operation_id} — {kind}")
        lines.append("")
        lines.append(f"- graphRef: `{operation.get('graphRef', '')}`")
        dependencies = ", ".join(str(item) for item in operation.get("dependsOn", []))
        lines.append(f"- dependsOn: {dependencies or '(none)'}")
        if kind in {"CONNECT", "DISCONNECT"}:
            lines.append(f"- from: `{_endpoint(operation.get('from'))}`")
            lines.append(f"- to: `{_endpoint(operation.get('to'))}`")
        else:
            lines.append(f"- payload: `{operation.get('payload', {})}`")
        lines.append("")
    blockers = plan.get("blockingQuestions") if isinstance(plan.get("blockingQuestions"), list) else []
    lines.extend(["## Blockers", ""])
    if blockers:
        lines.extend(f"- {item.get('text', '')}" for item in blockers)
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Validation boundary",
            "",
            "This plan does not claim Pin type compatibility, runtime correctness, DevKit node creation availability, compile correctness, or save correctness.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ["render_patch_plan"]
