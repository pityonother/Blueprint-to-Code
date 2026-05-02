"""Context review reports for defaults, components, and missing graph notes."""

from __future__ import annotations

from collections import Counter

from .quality import collect_asset_quality, component_class_hint, variable_hint
from .utils import table_row


def default_candidate_kind(item: dict[str, object]) -> tuple[str, str]:
    name = str(item.get("name", ""))
    reads = int(item.get("reads", 0) or 0)
    writes = int(item.get("writes", 0) or 0)
    hint = variable_hint(name)
    lowered = name.lower()
    if writes >= 2:
        return (
            "graph_written_runtime_state",
            "图里会多处写入，优先当作运行时状态；除非 DevKit Class Defaults 里明确存在，否则不建议手填。",
        )
    if writes == 1:
        return (
            "graph_written_maybe_runtime_state",
            "图里会写入一次，可能是运行时状态或输出变量；先确认是否真的有 Class Default。",
        )
    if hint == "asset_or_component_reference":
        return (
            "asset_or_component_reference",
            "像资源或组件引用；优先在组件面板、资源引用或父类默认值里确认。",
        )
    if reads >= 4 or any(term in lowered for term in ("team", "rider", "saddle", "female", "water", "targeting")):
        return (
            "likely_parent_or_inherited_state",
            "只读或高频读取，更像父类/原生状态；通常不需要在本资产 defaults.json 手填。",
        )
    return (
        "needs_manual_default_check",
        "仍可能是本资产缺失默认值；如果要改玩法，建议在 DevKit Class Defaults 里复查。",
    )


def build_context_review(asset_payload: dict[str, object]) -> dict[str, object]:
    quality = collect_asset_quality(asset_payload)
    defaults = [item for item in quality.get("default_variable_candidates", []) if isinstance(item, dict)]
    components = [item for item in quality.get("component_candidates", []) if isinstance(item, dict)]
    missing = [item for item in quality.get("blueprint_missing_candidates", []) if isinstance(item, dict)]

    default_rows: list[dict[str, object]] = []
    default_counts: Counter = Counter()
    for item in defaults:
        kind, recommendation = default_candidate_kind(item)
        default_counts[kind] += 1
        default_rows.append(
            {
                "name": item.get("name", ""),
                "hint": variable_hint(str(item.get("name", ""))),
                "reads": item.get("reads", 0),
                "writes": item.get("writes", 0),
                "kind": kind,
                "recommendation": recommendation,
            }
        )

    missing_by_function: dict[str, dict[str, object]] = {}
    for item in missing:
        function = str(item.get("function", "")).strip()
        if not function:
            continue
        row = missing_by_function.setdefault(function, {"function": function, "source_graphs": set(), "areas": set()})
        row_sources = row.get("source_graphs")
        row_areas = row.get("areas")
        if isinstance(row_sources, set):
            row_sources.add(str(item.get("source_graph", "")))
        if isinstance(row_areas, set):
            row_areas.add(str(item.get("behavior_area", "") or ""))

    area_lookup = {}
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        area = str(graph.get("behavior_area") or "")
        if area:
            area_lookup[str(graph.get("graph_name", ""))] = area
    for row in quality.get("graph_quality", []):
        if isinstance(row, dict):
            area_lookup[str(row.get("graph", ""))] = str(row.get("behavior_area", ""))
    for row in missing_by_function.values():
        source_graphs = row.get("source_graphs")
        areas = row.get("areas")
        if isinstance(source_graphs, set) and isinstance(areas, set):
            for source in source_graphs:
                area = area_lookup.get(source)
                if area:
                    areas.add(area)

    missing_rows = []
    for row in missing_by_function.values():
        sources = sorted(str(value) for value in row.get("source_graphs", set()) if str(value))
        areas = sorted(str(value) for value in row.get("areas", set()) if str(value))
        function = str(row.get("function", ""))
        missing_rows.append(
            {
                "function": function,
                "source_graphs": sources,
                "areas": areas,
                "notes_inherited": f"inherited: {function}",
                "notes_ignore": f"ignore missing graph: {function}",
            }
        )
    missing_rows.sort(key=lambda row: (-len(row.get("source_graphs", [])), str(row.get("function", ""))))

    component_rows = [
        {
            "name": item.get("name", ""),
            "class_hint": component_class_hint(str(item.get("name", ""))),
            "reads": item.get("reads", 0),
            "writes": item.get("writes", 0),
            "recommendation": "如果它是真组件，确认 class 和关键 defaults；如果只是资源变量，可保留在 defaults 侧。",
        }
        for item in components
    ]

    component_sources: Counter = Counter()
    component_context = asset_payload.get("component_defaults", {})
    if isinstance(component_context, dict):
        for item in component_context.get("components", []):
            if isinstance(item, dict):
                component_sources[str(item.get("source") or "manual_or_unknown")] += 1

    return {
        "metadata": asset_payload.get("metadata", {}),
        "default_candidates": default_rows,
        "default_candidate_counts": dict(default_counts),
        "missing_functions": missing_rows,
        "component_candidates": component_rows,
        "component_source_counts": dict(component_sources),
        "attention_graphs": quality.get("attention_graphs", []),
    }


def render_context_review(asset_payload: dict[str, object]) -> str:
    review = build_context_review(asset_payload)
    metadata = review.get("metadata", {}) if isinstance(review.get("metadata", {}), dict) else {}
    default_rows = [item for item in review.get("default_candidates", []) if isinstance(item, dict)]
    missing_rows = [item for item in review.get("missing_functions", []) if isinstance(item, dict)]
    component_rows = [item for item in review.get("component_candidates", []) if isinstance(item, dict)]
    attention_graphs = [item for item in review.get("attention_graphs", []) if isinstance(item, dict)]
    lines = [
        "# Blueprint Context Review",
        "",
        "## Summary",
        "",
        f"- Asset: {metadata.get('asset_name', '-')}",
        f"- Parsed graph pages: {metadata.get('graph_count', 0)}",
        f"- Parsed defaults: {metadata.get('default_variable_count', 0)}",
        f"- Parsed components: {metadata.get('component_count', 0)}",
        f"- Missing function candidates: {len(missing_rows)}",
        f"- Default candidates still requiring judgement: {len(default_rows)}",
        "",
        "## What To Do First",
        "",
    ]
    if attention_graphs:
        lines.append("- 先处理图页完整性问题；断链图会影响所有后续解释。")
    if missing_rows:
        lines.append("- 再用下面的缺失函数表更新 `notes.md`，把父类/原生函数从误报里移走。")
    if default_rows:
        lines.append("- 最后复查默认值候选；图里会写入的变量优先按运行时状态处理，不要盲目手填。")
    if not any((attention_graphs, missing_rows, default_rows)):
        lines.append("- 当前上下文没有明显待补项。")

    lines.extend(["", "## Default Candidate Triage", ""])
    counts = review.get("default_candidate_counts", {}) if isinstance(review.get("default_candidate_counts", {}), dict) else {}
    if counts:
        lines.append(table_row(["Kind", "Count"]))
        lines.append(table_row(["---", "---"]))
        for kind, count in sorted(counts.items()):
            lines.append(table_row([kind, count]))
        lines.append("")
    if default_rows:
        lines.append(table_row(["Variable", "Hint", "Reads", "Writes", "Likely Meaning", "Recommendation"]))
        lines.append(table_row(["---", "---", "---", "---", "---", "---"]))
        for item in default_rows[:80]:
            lines.append(
                table_row(
                    [
                        item.get("name", ""),
                        item.get("hint", ""),
                        item.get("reads", 0),
                        item.get("writes", 0),
                        item.get("kind", ""),
                        item.get("recommendation", ""),
                    ]
                )
            )
    else:
        lines.append("- No default candidates need review.")

    lines.extend(["", "## Missing Function Notes Queue", ""])
    if missing_rows:
        lines.append("确认函数来自父类/原生后，把 `Notes line` 写入 `notes.md`；确认是本资产图页就补采该图页。")
        lines.append("")
        lines.append(table_row(["Function", "Source Graphs", "Areas", "Notes line"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in missing_rows[:80]:
            lines.append(
                table_row(
                    [
                        item.get("function", ""),
                        ", ".join(item.get("source_graphs", [])),
                        ", ".join(item.get("areas", [])),
                        item.get("notes_inherited", ""),
                    ]
                )
            )
    else:
        lines.append("- No missing function notes are needed.")

    lines.extend(["", "## Component Candidate Review", ""])
    sources = review.get("component_source_counts", {}) if isinstance(review.get("component_source_counts", {}), dict) else {}
    if sources:
        lines.append(table_row(["Existing Component Source", "Count"]))
        lines.append(table_row(["---", "---"]))
        for source, count in sorted(sources.items(), key=lambda item: (-int(item[1]), str(item[0]))):
            lines.append(table_row([source, count]))
        lines.append("")
    if component_rows:
        lines.append(table_row(["Name", "Class Hint", "Reads", "Writes", "Recommendation"]))
        lines.append(table_row(["---", "---", "---", "---", "---"]))
        for item in component_rows[:80]:
            lines.append(table_row([item.get("name", ""), item.get("class_hint", ""), item.get("reads", 0), item.get("writes", 0), item.get("recommendation", "")]))
    else:
        lines.append("- No new component candidates were found.")
    lines.append("")
    return "\n".join(lines)
