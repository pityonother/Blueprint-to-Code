"""Small memory-card and context-pack renderers for Blueprint assets."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from collections import Counter
from typing import Any

from .utils import table_row


MEMORY_CARD_SCHEMA = "ark.asset_memory_card.v1"
CONTEXT_PACK_SCHEMA = "ark.context_pack.v1"
MIN_CONTEXT_BUDGET = 500
DEFAULT_CONTEXT_BUDGET = 1400
ASCII_CHARS_PER_TOKEN = 3
SPACE_CHARS_PER_TOKEN = 8

_QUERY_SYNONYMS = {
    "驯服": ("tame", "taming"),
    "经验": ("xp", "experience"),
    "伤害": ("damage",),
    "攻击": ("attack",),
    "概率": ("chance", "probability", "random"),
    "几率": ("chance", "probability", "random"),
    "冷却": ("cooldown",),
    "计时": ("timer", "time", "interval", "duration"),
    "时间": ("timer", "time", "interval", "duration"),
    "掉落": ("loot", "drop", "reward"),
    "战利品": ("loot", "drop", "reward"),
    "增益": ("buff", "modifier"),
    "生命": ("health",),
    "耐力": ("stamina",),
    "速度": ("speed",),
    "繁殖": ("breeding", "mating"),
    "继承": ("inheritance", "inherit"),
    "食物": ("food",),
    "消耗": ("cost", "consume"),
    "护甲": ("armor",),
    "权重": ("weight",),
    "范围": ("range",),
    "数量": ("count", "amount"),
    "堆叠": ("stack", "count"),
}


def estimate_tokens(text: str) -> int:
    """Return a conservative local estimate without adding a tokenizer dependency."""
    total = 0
    ascii_run = 0
    space_run = 0

    def flush_ascii() -> None:
        nonlocal total, ascii_run
        if ascii_run:
            total += max(1, (ascii_run + ASCII_CHARS_PER_TOKEN - 1) // ASCII_CHARS_PER_TOKEN)
            ascii_run = 0

    def flush_space() -> None:
        nonlocal total, space_run
        if space_run:
            total += max(1, (space_run + SPACE_CHARS_PER_TOKEN - 1) // SPACE_CHARS_PER_TOKEN)
            space_run = 0

    for char in str(text or ""):
        codepoint = ord(char)
        if char.isascii() and (char.isalnum() or char == "_"):
            flush_space()
            ascii_run += 1
            continue
        flush_ascii()
        if char == "\n":
            flush_space()
            total += 1
            continue
        if char.isspace():
            space_run += 1
            continue
        flush_space()
        if 0x3400 <= codepoint <= 0x9FFF:
            total += 1
        else:
            total += 1
    flush_ascii()
    flush_space()
    return total


def _short_text(value: object, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _compact_value(value: object, limit: int = 180) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _short_text(value, limit)
    try:
        return _short_text(json.dumps(value, ensure_ascii=False, default=str), limit)
    except (TypeError, ValueError):
        return _short_text(value, limit)


def _query_terms(question: str) -> list[str]:
    lowered = str(question or "").casefold()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9_]+", lowered):
        terms.append(token)
        terms.extend(part for part in token.split("_") if len(part) >= 2)
    for source, synonyms in _QUERY_SYNONYMS.items():
        if source in lowered:
            terms.extend(synonyms)
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))


def context_query_terms(question: str) -> list[str]:
    """Expose the same compact query expansion to repository-backed packs."""

    return _query_terms(question)


def _search_score(values: list[object], terms: list[str]) -> int:
    if not terms:
        return 0
    haystack = " ".join(str(value or "") for value in values).casefold()
    return sum(3 if re.search(rf"(?:^|[^a-z0-9]){re.escape(term)}(?:$|[^a-z0-9])", haystack) else 1 for term in terms if term in haystack)


def _metadata(asset_payload: dict[str, object]) -> dict[str, Any]:
    value = asset_payload.get("metadata", {})
    return value if isinstance(value, dict) else {}


def _uasset_binary(asset_payload: dict[str, object]) -> dict[str, Any]:
    value = asset_payload.get("uasset_binary", {})
    return value if isinstance(value, dict) else {}


def _class_defaults(asset_payload: dict[str, object]) -> dict[str, Any]:
    defaults = asset_payload.get("class_defaults", {})
    if isinstance(defaults, dict) and defaults.get("variables"):
        return defaults
    uasset_defaults = _uasset_binary(asset_payload).get("class_defaults", {})
    return uasset_defaults if isinstance(uasset_defaults, dict) else {}


def _variables(asset_payload: dict[str, object]) -> dict[str, dict[str, Any]]:
    defaults = _class_defaults(asset_payload)
    variables = defaults.get("variables", {})
    if not isinstance(variables, dict):
        return {}
    return {str(key): value for key, value in variables.items() if isinstance(value, dict)}


def _asset_name(asset_payload: dict[str, object], formula_payload: dict[str, object] | None = None) -> str:
    metadata = _metadata(asset_payload)
    formula_payload = formula_payload or {}
    return str(
        metadata.get("asset_name")
        or formula_payload.get("asset_name")
        or _class_defaults(asset_payload).get("asset_name")
        or "BlueprintAsset"
    )


def _object_path(asset_payload: dict[str, object], formula_payload: dict[str, object] | None = None) -> str:
    metadata = _metadata(asset_payload)
    uasset = _uasset_binary(asset_payload)
    formula_payload = formula_payload or {}
    return str(
        metadata.get("object_path")
        or metadata.get("asset_path")
        or formula_payload.get("asset_path")
        or uasset.get("asset_path")
        or ""
    )


def _asset_type(asset_payload: dict[str, object], formula_payload: dict[str, object] | None = None) -> str:
    metadata = _metadata(asset_payload)
    formula_payload = formula_payload or {}
    return str(metadata.get("asset_type") or formula_payload.get("asset_type") or "")


def _graph_name(graph: dict[str, object]) -> str:
    payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
    return str(graph.get("graph_name") or graph.get("graph") or metadata.get("graph_name") or "")


def _graph_payload(graph: dict[str, object]) -> dict[str, Any]:
    value = graph.get("payload", graph)
    return value if isinstance(value, dict) else {}


def _key_defaults(
    asset_payload: dict[str, object],
    limit: int = 20,
    terms: list[str] | None = None,
) -> list[dict[str, object]]:
    terms = terms or []
    rows: list[dict[str, object]] = []
    for name, info in _variables(asset_payload).items():
        rows.append(
            {
                "name": name,
                "value": _compact_value(info.get("value")),
                "type": info.get("type", ""),
                "confidence": info.get("confidence", "unknown"),
                "_score": _search_score([name, info.get("type", ""), info.get("value")], terms),
            }
        )
    rows.sort(
        key=lambda item: (
            -int(item.get("_score") or 0),
            str(item.get("confidence")) != "high",
            str(item.get("name")),
        )
    )
    for item in rows:
        item.pop("_score", None)
    return rows[:limit]


def _prioritized_unique(values: list[object], terms: list[str], limit: int = 8) -> list[str]:
    unique = list(dict.fromkeys(_short_text(value, 100) for value in values if str(value or "").strip()))
    order = {value: index for index, value in enumerate(unique)}
    unique.sort(key=lambda value: (-_search_score([value], terms), order[value]))
    return unique[:limit]


def _key_graphs(
    asset_payload: dict[str, object],
    limit: int = 10,
    terms: list[str] | None = None,
) -> list[dict[str, object]]:
    terms = terms or []
    graphs: list[dict[str, object]] = []
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        payload = _graph_payload(graph)
        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
        graph_name = _graph_name(graph)
        if not graph_name:
            continue
        nodes = [node for node in payload.get("nodes", []) if isinstance(node, dict)]
        functions = [node.get("function") for node in nodes if node.get("function")]
        variables = [node.get("variable") for node in nodes if node.get("variable")]
        events = [node.get("event") for node in nodes if node.get("event")]
        graph_type = graph.get("graph_type") or metadata.get("graph_type") or ""
        graphs.append(
            {
                "graph": graph_name,
                "graph_type": graph_type,
                "nodes": graph.get("node_count") or metadata.get("node_count") or 0,
                "confidence": graph.get("confidence") or metadata.get("confidence") or "unknown",
                "functions": _prioritized_unique(functions, terms),
                "variables": _prioritized_unique(variables, terms),
                "events": _prioritized_unique(events, terms, limit=4),
                "_score": _search_score([graph_name, graph_type, *functions, *variables, *events], terms),
            }
        )
    graphs.sort(
        key=lambda item: (
            -int(item.get("_score") or 0),
            -int(item.get("nodes") or 0),
            str(item.get("graph")),
        )
    )
    for item in graphs:
        item.pop("_score", None)
    return graphs[:limit]


def _function_counts(asset_payload: dict[str, object], limit: int = 20) -> list[dict[str, object]]:
    counts: Counter[str] = Counter()
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        payload = _graph_payload(graph)
        for item in payload.get("function_calls", []):
            if isinstance(item, dict):
                function = str(item.get("function") or item.get("label") or "")
                if function:
                    counts[function] += 1
        for node in payload.get("nodes", []):
            if isinstance(node, dict):
                function = str(node.get("function") or "")
                if function:
                    counts[function] += 1
    return [{"function": name, "count": count} for name, count in counts.most_common(limit)]


def _variable_counts(asset_payload: dict[str, object], limit: int = 20) -> list[dict[str, object]]:
    counts: Counter[str] = Counter(_variables(asset_payload).keys())
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        payload = _graph_payload(graph)
        for key in ("variable_gets", "variable_sets"):
            for item in payload.get(key, []):
                if isinstance(item, dict):
                    variable = str(item.get("variable") or item.get("label") or "")
                    if variable:
                        counts[variable] += 1
    return [{"variable": name, "count": count} for name, count in counts.most_common(limit)]


def _compact_formula(candidate: dict[str, object]) -> dict[str, object]:
    return {
        "id": candidate.get("id", ""),
        "mechanism_type": candidate.get("mechanism_type", ""),
        "graph": candidate.get("graph", ""),
        "confidence": candidate.get("confidence", ""),
        "visible_rule": _short_text(candidate.get("visible_rule", ""), 420),
        "status": candidate.get("status", "candidate"),
        "missing_evidence": [_short_text(value, 160) for value in list(candidate.get("missing_evidence", []) or [])[:4]],
    }


def _compact_unresolved(item: dict[str, object]) -> dict[str, object]:
    return {
        "id": item.get("id", ""),
        "candidate_id": item.get("candidate_id", ""),
        "mechanism_type": item.get("mechanism_type", ""),
        "known_visible_part": _short_text(item.get("known_visible_part", ""), 360),
        "blocked_by": [_short_text(value, 120) for value in list(item.get("blocked_by", []) or [])[:6]],
        "required_next_probe": list(item.get("required_next_probe", []) or [])[:3],
        "status": item.get("status", "open"),
    }


def _player_summary(asset_name: str, defaults: list[dict[str, object]], formulas: list[dict[str, object]]) -> str:
    display = next((item for item in defaults if item.get("name") == "DescriptiveNameBase" and item.get("value")), None)
    description = next((item for item in defaults if item.get("name") == "ItemDescription" and item.get("value")), None)
    parts = [f"{asset_name} is a captured ARK Blueprint asset."]
    if display:
        parts.append(f"Display name: {display.get('value')}.")
    if description:
        parts.append(f"Description: {description.get('value')}.")
    if formulas:
        types = ", ".join(dict.fromkeys(str(item.get("mechanism_type") or "") for item in formulas if item.get("mechanism_type")))
        parts.append(f"Visible formula candidates: {types}.")
    return " ".join(parts)


def _fingerprint(asset_name: str, object_path: str, formulas: list[dict[str, object]]) -> str:
    payload = json.dumps(
        {"asset": asset_name, "object_path": object_path, "formulas": [item.get("id") for item in formulas]},
        ensure_ascii=True,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _evidence_pointers(asset_payload: dict[str, object], formula_payload: dict[str, object]) -> list[dict[str, object]]:
    pointers: list[dict[str, object]] = []
    metadata = _metadata(asset_payload)
    asset_dir = str(metadata.get("asset_dir") or "")
    for name in ("asset_report.md", "behavior_summary.md", "diagnostics_report.md", "formula_candidates.md"):
        pointers.append({"kind": "report", "path": f"{asset_dir}/output/{name}" if asset_dir else f"output/{name}"})
    for candidate in formula_payload.get("candidates", []) or []:
        if isinstance(candidate, dict):
            pointers.append({"kind": "formula_candidate", "id": candidate.get("id", ""), "graph": candidate.get("graph", "")})
    return pointers[:40]


def build_asset_memory_card(
    asset_payload: dict[str, object],
    formula_payload: dict[str, object],
) -> dict[str, object]:
    asset_name = _asset_name(asset_payload, formula_payload)
    object_path = _object_path(asset_payload, formula_payload)
    asset_type = _asset_type(asset_payload, formula_payload)
    defaults = _key_defaults(asset_payload)
    formulas = [_compact_formula(item) for item in formula_payload.get("candidates", []) or [] if isinstance(item, dict)]
    unresolved = [_compact_unresolved(item) for item in formula_payload.get("unresolved_formulas", []) or [] if isinstance(item, dict)]
    return {
        "schema": MEMORY_CARD_SCHEMA,
        "asset_name": asset_name,
        "object_path": object_path,
        "asset_type": asset_type,
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "fingerprint": _fingerprint(asset_name, object_path, formulas),
        "player_summary": _player_summary(asset_name, defaults, formulas),
        "confirmed_mechanisms": [],
        "formula_candidates": formulas[:8],
        "unresolved": unresolved[:8],
        "key_defaults": defaults,
        "key_graphs": _key_graphs(asset_payload),
        "key_functions": _function_counts(asset_payload),
        "key_variables": _variable_counts(asset_payload),
        "next_related_assets": [],
        "evidence_pointers": _evidence_pointers(asset_payload, formula_payload),
    }


def render_asset_memory_card(card: dict[str, object]) -> str:
    lines = [
        "# Asset Memory Card",
        "",
        f"- Asset: {card.get('asset_name', '-')}",
        f"- Object path: {card.get('object_path', '-') or '-'}",
        f"- Type: {card.get('asset_type', '-') or '-'}",
        f"- Fingerprint: {card.get('fingerprint', '-')}",
        "",
        "## Player Summary",
        "",
        str(card.get("player_summary") or "-"),
        "",
        "## Formula Candidates",
        "",
    ]
    formulas = [item for item in card.get("formula_candidates", []) if isinstance(item, dict)]
    if formulas:
        lines.append(table_row(["Type", "Graph", "Confidence", "Visible Part"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in formulas[:8]:
            lines.append(table_row([item.get("mechanism_type", ""), item.get("graph", ""), item.get("confidence", ""), item.get("visible_rule", "")]))
    else:
        lines.append("- none")
    lines.extend(["", "## Unresolved", ""])
    unresolved = [item for item in card.get("unresolved", []) if isinstance(item, dict)]
    if unresolved:
        lines.append(table_row(["Candidate", "Blocked By"]))
        lines.append(table_row(["---", "---"]))
        for item in unresolved[:8]:
            lines.append(table_row([item.get("candidate_id", ""), ", ".join(str(value) for value in item.get("blocked_by", [])[:6])]))
    else:
        lines.append("- none")
    lines.extend(["", "## Key Defaults", ""])
    defaults = [item for item in card.get("key_defaults", []) if isinstance(item, dict)]
    if defaults:
        lines.append(table_row(["Name", "Value", "Confidence"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in defaults[:12]:
            lines.append(table_row([item.get("name", ""), item.get("value", ""), item.get("confidence", "")]))
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _rank_formula_rows(rows: list[object], terms: list[str]) -> list[dict[str, object]]:
    ranked: list[tuple[int, int, dict[str, object]]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        inputs = item.get("inputs", []) if isinstance(item.get("inputs", []), list) else []
        evidence = item.get("evidence", []) if isinstance(item.get("evidence", []), list) else []
        blocked_by = item.get("blocked_by", []) if isinstance(item.get("blocked_by", []), list) else []
        probes = item.get("required_next_probe", []) if isinstance(item.get("required_next_probe", []), list) else []
        score = _search_score(
            [
                item.get("id"),
                item.get("candidate_id"),
                item.get("mechanism_type"),
                item.get("mechanism"),
                item.get("graph"),
                item.get("visible_rule"),
                item.get("known_visible_part"),
                *blocked_by,
                *[entry.get("detail") for entry in probes if isinstance(entry, dict)],
                *[entry.get("name") for entry in inputs if isinstance(entry, dict)],
                *[entry.get("function") or entry.get("name") for entry in evidence if isinstance(entry, dict)],
            ],
            terms,
        )
        ranked.append((score, index, item))
    if terms:
        ranked.sort(key=lambda row: (-row[0], row[1]))
    return [item for _score, _index, item in ranked]


def _update_estimated_tokens(pack: dict[str, object]) -> int:
    estimate = 0
    for _attempt in range(3):
        pack["estimated_tokens"] = estimate
        updated = estimate_tokens(render_context_pack(pack))
        if updated == estimate:
            break
        estimate = updated
    pack["estimated_tokens"] = estimate
    return estimate


def _fit_base_pack(pack: dict[str, object], budget: int) -> None:
    """Shrink optional shell text until the empty context pack fits its budget."""
    if _update_estimated_tokens(pack) <= budget:
        return
    shrink_steps: list[tuple[str, object]] = [
        ("player_summary", _short_text(pack.get("player_summary", ""), 240)),
        ("question", _short_text(pack.get("question", ""), 240)),
        ("object_path", _short_text(pack.get("object_path", ""), 240)),
        ("player_summary", _short_text(pack.get("player_summary", ""), 100)),
        ("question", _short_text(pack.get("question", ""), 100)),
        ("object_path", _short_text(pack.get("object_path", ""), 100)),
        ("omitted", ["details excluded by token budget"]),
        ("player_summary", ""),
        ("question", ""),
        ("object_path", ""),
        ("asset_name", _short_text(pack.get("asset_name", ""), 80)),
        ("omitted", []),
    ]
    for key, value in shrink_steps:
        pack[key] = value
        if _update_estimated_tokens(pack) <= budget:
            return
    raise ValueError(f"context pack shell cannot fit the {budget}-token budget")


def _append_if_fits(pack: dict[str, object], section: str, item: object, budget: int) -> bool:
    target = pack.get(section)
    if not isinstance(target, list):
        return False
    target.append(item)
    if _update_estimated_tokens(pack) <= budget:
        return True
    target.pop()
    _update_estimated_tokens(pack)
    return False


def _append_with_pointer(
    pack: dict[str, object],
    section: str,
    item: object,
    pointer: dict[str, object],
    budget: int,
) -> bool:
    target = pack.get(section)
    pointers = pack.get("evidence_pointers")
    if not isinstance(target, list) or not isinstance(pointers, list):
        return False
    target.append(item)
    pointers.append(pointer)
    if _update_estimated_tokens(pack) <= budget:
        return True
    pointers.pop()
    target.pop()
    _update_estimated_tokens(pack)
    return False


def build_default_context_pack(
    asset_payload: dict[str, object],
    formula_payload: dict[str, object],
    memory_card: dict[str, object],
    *,
    budget: int = DEFAULT_CONTEXT_BUDGET,
    question: str = "",
) -> dict[str, object]:
    budget = int(budget or 0)
    if budget < MIN_CONTEXT_BUDGET:
        raise ValueError(f"context pack budget minimum is {MIN_CONTEXT_BUDGET} tokens")
    question = _short_text(question, 500)
    terms = _query_terms(question)
    formula_source = _rank_formula_rows(list(formula_payload.get("candidates", []) or []), terms)
    unresolved_source = _rank_formula_rows(list(formula_payload.get("unresolved_formulas", []) or []), terms)
    formula_candidates = [_compact_formula(item) for item in formula_source[:20]]
    unresolved = [_compact_unresolved(item) for item in unresolved_source[:16]]
    key_defaults = _key_defaults(asset_payload, limit=30, terms=terms)
    key_graphs = _key_graphs(asset_payload, limit=20, terms=terms)
    confirmed = [_short_text(item, 240) for item in list(memory_card.get("confirmed_mechanisms", []) or [])[:8]]
    included_sections = [
        "asset_identity",
        "question",
        "player_summary",
        "confirmed_mechanisms",
        "formula_candidates",
        "unresolved",
        "key_defaults",
        "key_graphs",
        "evidence_pointers",
    ]
    omitted = [
        "full graph JSON",
        "full pins",
        "full links",
        "full diagnostics",
        "full graph reports",
        "rows excluded by the token budget",
    ]
    pack: dict[str, object] = {
        "schema": CONTEXT_PACK_SCHEMA,
        "asset_name": _short_text(memory_card.get("asset_name", ""), 320),
        "object_path": _short_text(memory_card.get("object_path", ""), 640),
        "purpose": "question_answering" if question else "default_player_summary",
        "budget": budget,
        "estimated_tokens": 0,
        "budget_enforced": True,
        "question": question,
        "query_terms": terms,
        "source_counts": {
            "formula_candidates": len(formula_source),
            "unresolved": len(unresolved_source),
            "key_defaults": len(_variables(asset_payload)),
            "key_graphs": len([graph for graph in asset_payload.get("graphs", []) if isinstance(graph, dict)]),
        },
        "included_sections": included_sections,
        "player_summary": _short_text(memory_card.get("player_summary", ""), 520),
        "confirmed_mechanisms": [],
        "formula_candidates": [],
        "unresolved": [],
        "key_defaults": [],
        "key_graphs": [],
        "evidence_pointers": [],
        "omitted": omitted,
    }
    _fit_base_pack(pack, budget)

    for pointer in list(memory_card.get("evidence_pointers", []) or [])[:4]:
        if isinstance(pointer, dict):
            _append_if_fits(pack, "evidence_pointers", pointer, budget)
    if key_defaults:
        _append_if_fits(
            pack,
            "evidence_pointers",
            {"kind": "defaults", "path": "uasset_class_defaults.json"},
            budget,
        )

    sections = {
        "confirmed_mechanisms": confirmed,
        "formula_candidates": formula_candidates,
        "key_defaults": key_defaults,
        "key_graphs": key_graphs,
        "unresolved": unresolved,
    }
    for index in range(max((len(items) for items in sections.values()), default=0)):
        for section, items in sections.items():
            if index < len(items):
                item = items[index]
                if section == "formula_candidates" and isinstance(item, dict):
                    _append_with_pointer(
                        pack,
                        section,
                        item,
                        {"kind": "formula_candidate", "id": item.get("id", ""), "graph": item.get("graph", "")},
                        budget,
                    )
                elif section == "key_graphs" and isinstance(item, dict):
                    _append_with_pointer(
                        pack,
                        section,
                        item,
                        {"kind": "graph", "path": "uasset_graph_nodes.json", "graph": item.get("graph", "")},
                        budget,
                    )
                else:
                    _append_if_fits(pack, section, item, budget)
    source_counts = pack.get("source_counts", {}) if isinstance(pack.get("source_counts", {}), dict) else {}
    pack["omitted_counts"] = {
        section: max(int(source_counts.get(section) or 0) - len(pack.get(section, []) or []), 0)
        for section in ("formula_candidates", "unresolved", "key_defaults", "key_graphs")
    }
    _update_estimated_tokens(pack)
    return pack


def render_context_pack(pack: dict[str, object]) -> str:
    source_counts = pack.get("source_counts", {}) if isinstance(pack.get("source_counts", {}), dict) else {}
    lines = [
        "# Asset Context Pack",
        "",
        f"- Asset: {pack.get('asset_name', '-')}",
        f"- Object path: {pack.get('object_path', '-') or '-'}",
        f"- Purpose: {pack.get('purpose', '-')}",
        f"- Budget: {pack.get('budget', '-')} estimated tokens",
        f"- Estimated size: {pack.get('estimated_tokens', '-')} tokens",
        f"- Selected: {len(pack.get('formula_candidates', []) or [])}/{source_counts.get('formula_candidates', 0)} formulas, "
        f"{len(pack.get('key_graphs', []) or [])}/{source_counts.get('key_graphs', 0)} graphs, "
        f"{len(pack.get('key_defaults', []) or [])}/{source_counts.get('key_defaults', 0)} defaults",
        "",
    ]
    if pack.get("revision_id"):
        lines.insert(4, f"- Revision: {pack.get('revision_id')}")
    evidence_counts = pack.get("evidence_counts") if isinstance(pack.get("evidence_counts"), dict) else {}
    if evidence_counts:
        lines.extend(
            [
                "## Evidence Counts",
                "",
                "- Graphs: {graphCount}; Nodes: {nodeCount}; Pins: {pinCount}; Wires: {wireCount}; "
                "Link observations: {linkObservationCount}; Defaults: {defaultCount}; Gaps: {gapCount}".format(
                    **{key: evidence_counts.get(key, 0) for key in (
                        "graphCount", "nodeCount", "pinCount", "wireCount", "linkObservationCount", "defaultCount", "gapCount"
                    )}
                ),
                "",
            ]
        )
    if pack.get("question"):
        lines.extend([f"- Question: {_short_text(pack.get('question'), 500)}", ""])
    lines.extend(["## Player Summary", "", str(pack.get("player_summary") or "-"), ""])
    confirmed = list(pack.get("confirmed_mechanisms", []) or [])
    if confirmed:
        lines.extend(["## Confirmed Mechanisms", ""])
        lines.extend(f"- {_short_text(item, 240)}" for item in confirmed)
        lines.append("")
    lines.extend(["## Formula Candidates", ""])
    formulas = [item for item in pack.get("formula_candidates", []) if isinstance(item, dict)]
    if formulas:
        lines.append(table_row(["Type", "Graph", "Confidence", "Visible Part"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in formulas:
            lines.append(
                table_row(
                    [
                        item.get("mechanism_type", ""),
                        item.get("graph", ""),
                        item.get("confidence", ""),
                        _short_text(item.get("visible_rule", ""), 420),
                    ]
                )
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Unresolved", ""])
    unresolved = [item for item in pack.get("unresolved", []) if isinstance(item, dict)]
    if unresolved:
        lines.append(table_row(["Candidate", "Blocked By", "Next Probe"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in unresolved:
            probes = []
            for probe in item.get("required_next_probe", [])[:3]:
                probes.append(str(probe.get("detail") if isinstance(probe, dict) else probe))
            lines.append(
                table_row(
                    [
                        item.get("candidate_id", ""),
                        ", ".join(str(value) for value in item.get("blocked_by", [])[:6]),
                        "; ".join(probes),
                    ]
                )
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Key Defaults", ""])
    defaults = [item for item in pack.get("key_defaults", []) if isinstance(item, dict)]
    if defaults:
        include_refs = any(item.get("ref") for item in defaults)
        lines.append(table_row(["Name", "Value", "Confidence", *(["Evidence Ref"] if include_refs else [])]))
        lines.append(table_row(["---", "---", "---", *(["---"] if include_refs else [])]))
        for item in defaults:
            lines.append(table_row([item.get("name", ""), item.get("value", ""), item.get("confidence", ""), *([item.get("ref", "")] if include_refs else [])]))
    else:
        lines.append("- none")
    lines.extend(["", "## Key Graphs", ""])
    graphs = [item for item in pack.get("key_graphs", []) if isinstance(item, dict)]
    if graphs:
        include_refs = any(item.get("ref") for item in graphs)
        lines.append(table_row(["Graph", "Type", "Nodes", "Confidence", "Signals", *(["Evidence Ref"] if include_refs else [])]))
        lines.append(table_row(["---", "---", "---:", "---", "---", *(["---"] if include_refs else [])]))
        for item in graphs:
            signals = [
                *[str(value) for value in item.get("functions", [])[:5]],
                *[str(value) for value in item.get("variables", [])[:4]],
                *[str(value) for value in item.get("events", [])[:2]],
            ]
            lines.append(
                table_row(
                    [
                        item.get("graph", ""),
                        item.get("graph_type", ""),
                        item.get("nodes", item.get("node_count", 0)),
                        item.get("confidence", ""),
                        _short_text(", ".join(signals), 360),
                        *([item.get("ref", "")] if include_refs else []),
                    ]
                )
            )
    else:
        lines.append("- none")
    gaps = [item for item in pack.get("gaps", []) if isinstance(item, dict)]
    if gaps:
        lines.extend(["", "## Evidence Gaps", ""])
        for item in gaps:
            lines.append(
                f"- `{item.get('reasonCode', '')}` [{item.get('status', '')}] — {item.get('nextProbe', '')} — `{item.get('ref', '')}`"
            )
    lines.extend(["", "## Evidence Pointers", ""])
    for item in [row for row in pack.get("evidence_pointers", []) if isinstance(row, dict)]:
        if item.get("kind") == "graph" and item.get("graph"):
            label = (
                f"{item.get('id')} :: {item.get('graph')}"
                if item.get("id")
                else f"{item.get('path', 'uasset_graph_nodes.json')} :: {item.get('graph')}"
            )
        else:
            label = item.get("id") or item.get("path") or item.get("graph") or item.get("kind")
        lines.append(f"- {item.get('kind', 'evidence')}: {label}")
    if pack.get("next_query"):
        lines.extend(["", "## Next Query", "", f"`{pack.get('next_query')}`"])
    lines.extend(["", "## Omitted", ""])
    for item in pack.get("omitted", []) or []:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
