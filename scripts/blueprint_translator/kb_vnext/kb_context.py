"""Bounded reader context packs built from reviewed vNext query results."""

from __future__ import annotations

import json
import math
import re
from typing import Mapping


MIN_CONTEXT_TOKENS = 300
MAX_CONTEXT_TOKENS = 2_000
MAX_CONTEXT_CANDIDATES_PER_FACT = 3
_LOCAL_PATH = re.compile(
    r"(?i)(?:[a-z]:\\(?:users|windows|program files|programdata)\\|"
    r"/(?:home|users|etc|var|tmp)/)"
)


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def _safe_text(value: object) -> str:
    text = "" if value is None else str(value)
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
    status = (
        fact.get("status")
        or fact.get("resolutionStatus")
        or "UNKNOWN"
    )
    value_kind = fact.get("valueKind") or "UNKNOWN"
    return (
        f"- {fact.get('factType')}:{fact.get('factName')} "
        f"[{status}/{value_kind}] {rendered}"
    )


def _short_text(value: object, *, maximum: int) -> str:
    rendered = _safe_text(value)
    if len(rendered) <= maximum:
        return rendered
    return rendered[: maximum - 3] + "..."


def _candidate_line(candidate: Mapping[str, object]) -> str:
    value = next(
        (
            candidate.get(key)
            for key in (
                "valueText",
                "valueNumber",
                "valueInteger",
                "valueJson",
            )
            if candidate.get(key) is not None
        ),
        None,
    )
    disposition = (
        "selected" if candidate.get("selected") is True else "rejected"
    )
    owner = candidate.get("declaredOnUri")
    if not owner:
        owner = "entity#" + _safe_text(
            candidate.get("declaredOnEntityId")
        )
    rendered = (
        disposition
        + " candidate #"
        + _short_text(candidate.get("candidateFactId"), maximum=20)
        + " owner="
        + _short_text(owner, maximum=100)
        + " depth="
        + _short_text(candidate.get("inheritanceDepth"), maximum=12)
        + " path="
        + _short_text(candidate.get("pathStatus"), maximum=40)
        + " ["
        + _short_text(candidate.get("status") or "UNKNOWN", maximum=30)
        + "/"
        + _short_text(
            candidate.get("valueKind") or "UNKNOWN",
            maximum=30,
        )
        + "]"
    )
    if value is not None:
        rendered += "=" + _short_text(value, maximum=60)
    if disposition == "rejected":
        rendered += " reason=" + _short_text(
            candidate.get("rejectionReason") or "UNSPECIFIED",
            maximum=60,
        )
    return rendered


def _effective_candidates_line(fact: Mapping[str, object]) -> str:
    raw_candidates = fact.get("candidates")
    candidates = [
        item
        for item in (
            raw_candidates if isinstance(raw_candidates, list) else []
        )
        if isinstance(item, Mapping)
    ]
    candidates.sort(
        key=lambda item: 0 if item.get("selected") is True else 1
    )
    shown = candidates[:MAX_CONTEXT_CANDIDATES_PER_FACT]
    try:
        reported_total = max(0, int(fact.get("candidateTotal") or 0))
    except (TypeError, ValueError):
        reported_total = 0
    try:
        reported_omitted = max(
            0, int(fact.get("candidateOmitted") or 0)
        )
    except (TypeError, ValueError):
        reported_omitted = 0
    total = max(
        reported_total,
        len(candidates) + reported_omitted,
        len(candidates),
    )
    resolution = _safe_text(
        fact.get("resolutionStatus") or "UNKNOWN"
    )
    state = (
        "resolved"
        if fact.get("factId") is not None and resolution == "RESOLVED"
        else "unresolved=" + resolution
    )
    prefix = (
        "- EFFECTIVE_DEFAULT:"
        + _short_text(fact.get("factName"), maximum=80)
        + " "
        + state
    )
    if not shown:
        return prefix + "; no recorded candidates"
    rendered = prefix + "; " + "; ".join(
        _candidate_line(candidate) for candidate in shown
    )
    omitted = max(0, total - len(shown))
    if omitted:
        rendered += f"; {omitted} candidates omitted"
    return rendered


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
        effective_facts = [
            item
            for item in facts
            if isinstance(item, Mapping)
            and item.get("factType") == "EFFECTIVE_DEFAULT"
            and (
                isinstance(item.get("candidates"), list)
                or item.get("candidateTotal") is not None
            )
        ]
        if effective_facts:
            optional_sections.append(
                (
                    "## Effective candidates",
                    [
                        _effective_candidates_line(item)
                        for item in effective_facts
                    ],
                )
            )
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
