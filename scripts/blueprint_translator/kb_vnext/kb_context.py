"""Bounded reader context packs built from reviewed vNext query results."""

from __future__ import annotations

import json
import math
import re
from typing import Mapping


MIN_CONTEXT_TOKENS = 300
MAX_CONTEXT_TOKENS = 2_000
_LOCAL_PATH = re.compile(
    r"(?i)(?:[a-z]:\\(?:users|windows|program files|programdata)\\|"
    r"/(?:home|users|etc|var|tmp)/)"
)


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def _safe_text(value: object) -> str:
    text = str(value or "")
    if _LOCAL_PATH.search(text):
        return "[LOCAL_PATH_REDACTED]"
    return text


def _fact_line(fact: Mapping[str, object]) -> str:
    value = next(
        (
            fact.get(key)
            for key in (
                "valueText",
                "valueNumber",
                "valueInteger",
                "valueJson",
            )
            if fact.get(key) is not None
        ),
        None,
    )
    rendered = _safe_text(value)
    if len(rendered) > 240:
        rendered = rendered[:237] + "..."
    return (
        f"- {fact.get('factType')}:{fact.get('factName')} "
        f"[{fact.get('status')}/{fact.get('valueKind')}] {rendered}"
    )


def build_bounded_context_pack(
    result: Mapping[str, object],
    *,
    budget_tokens: int = MAX_CONTEXT_TOKENS,
) -> dict[str, object]:
    """Render only reviewed projections; drop optional rows to fit budget."""

    budget = max(
        MIN_CONTEXT_TOKENS,
        min(MAX_CONTEXT_TOKENS, int(budget_tokens)),
    )
    entity = result.get("entity")
    entity_map = entity if isinstance(entity, Mapping) else {}
    lines = [
        "# ARK KB vNext Context",
        "",
        f"- Route: {_safe_text(result.get('route'))}",
        f"- Freshness: {_safe_text(result.get('freshness'))}",
        (
            "- Entity: "
            + _safe_text(entity_map.get("canonicalUri") or "UNRESOLVED")
        ),
        "",
    ]
    required_sections: list[tuple[str, list[str]]] = []
    gaps = result.get("missingRequirements")
    if isinstance(gaps, list) and gaps:
        required_sections.append(
            (
                "## Gaps",
                [
                    "- "
                    + _safe_text(item.get("code"))
                    + ": "
                    + _safe_text(item.get("requirement"))
                    for item in gaps
                    if isinstance(item, Mapping)
                ],
            )
        )
    optional_sections: list[tuple[str, list[str]]] = []
    facts = result.get("facts")
    if isinstance(facts, list) and facts:
        optional_sections.append(
            (
                "## Facts",
                [
                    _fact_line(item)
                    for item in facts
                    if isinstance(item, Mapping)
                ],
            )
        )
    relationships = result.get("relationships")
    if isinstance(relationships, list) and relationships:
        optional_sections.append(
            (
                "## Relationships",
                [
                    "- "
                    + _safe_text(item.get("edgeType"))
                    + " -> "
                    + _safe_text(item.get("targetUri"))
                    + " ["
                    + _safe_text(item.get("status"))
                    + "]"
                    for item in relationships
                    if isinstance(item, Mapping)
                ],
            )
        )
    evidence = result.get("evidence")
    if isinstance(evidence, list) and evidence:
        optional_sections.append(
            (
                "## Evidence",
                [
                    "- "
                    + _safe_text(item.get("evidenceUri"))
                    + " ["
                    + _safe_text(item.get("freshness"))
                    + "]"
                    for item in evidence
                    if isinstance(item, Mapping)
                ],
            )
        )
    probes = result.get("recommendedProbes")
    if isinstance(probes, list) and probes:
        required_sections.append(
            (
                "## Recommended probes",
                [
                    "- "
                    + json.dumps(
                        {
                            str(key): _safe_text(value)
                            for key, value in item.items()
                            if key
                            in {
                                "probeType",
                                "asset",
                                "target",
                                "operation",
                                "budgetTokens",
                                "reason",
                            }
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    for item in probes
                    if isinstance(item, Mapping)
                ],
            )
        )

    selected = list(lines)
    returned = 0
    omitted = 0
    for heading, section_lines in required_sections:
        selected.extend([heading, *section_lines, ""])
        returned += len(section_lines)
    for heading, section_lines in optional_sections:
        heading_added = False
        for line in section_lines:
            candidate = [
                *selected,
                *([] if heading_added else [heading]),
                line,
                "",
            ]
            if estimate_tokens("\n".join(candidate)) > budget:
                omitted += 1
                continue
            if not heading_added:
                selected.append(heading)
                heading_added = True
            selected.append(line)
            returned += 1
        if heading_added:
            selected.append("")
    content = "\n".join(selected).strip() + "\n"
    if estimate_tokens(content) > budget:
        raise ValueError("Required context-pack shell exceeds token budget")
    return {
        "schema": "ark-kb-context-pack/v1",
        "content": content,
        "estimatedTokens": estimate_tokens(content),
        "budgetTokens": budget,
        "returned": returned,
        "omitted": omitted,
        "truncated": omitted > 0,
    }
