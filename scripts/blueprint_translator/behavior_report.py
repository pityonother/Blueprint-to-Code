"""ARK-focused behavior summary rendering for captured Blueprint assets."""

from __future__ import annotations

from typing import Iterable

from .quality import behavior_area, collect_asset_quality
from .utils import table_row


def unique_call_rows(items: Iterable[dict[str, object]], fields: tuple[str, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        marker = tuple(str(item.get(field, "")) for field in fields)
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(item)
    return rows


def graph_variable_counters(graph: dict[str, object]) -> tuple[dict[str, int], dict[str, int]]:
    payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
    reads: dict[str, int] = {}
    writes: dict[str, int] = {}
    for item in payload.get("variable_gets", []):
        if isinstance(item, dict):
            name = str(item.get("variable") or item.get("label") or "")
            if name:
                reads[name] = reads.get(name, 0) + 1
    for item in payload.get("variable_sets", []):
        if isinstance(item, dict):
            name = str(item.get("variable") or item.get("label") or "")
            if name:
                writes[name] = writes.get(name, 0) + 1
    return reads, writes


def top_counter_items(counter: dict[str, int], limit: int = 10) -> str:
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return ", ".join(f"{name}({count})" for name, count in items) if items else "-"


def behavior_area_rollup(area_items: list[dict[str, object]], calls: list[dict[str, object]], quality: dict[str, object]) -> dict[str, object]:
    graph_names = {str(graph.get("graph_name", "")) for graph in area_items}
    reads: dict[str, int] = {}
    writes: dict[str, int] = {}
    for graph in area_items:
        graph_reads, graph_writes = graph_variable_counters(graph)
        for name, count in graph_reads.items():
            reads[name] = reads.get(name, 0) + count
        for name, count in graph_writes.items():
            writes[name] = writes.get(name, 0) + count
    local_calls = unique_call_rows(
        [item for item in calls if str(item.get("source_graph", "")) in graph_names and item.get("call_kind") == "local_blueprint_graph"],
        ("source_graph", "function", "target_graph"),
    )
    missing_calls = unique_call_rows(
        [item for item in quality.get("blueprint_missing_candidates", []) if isinstance(item, dict) and str(item.get("source_graph", "")) in graph_names],
        ("source_graph", "function"),
    )
    return {
        "graph_names": graph_names,
        "reads": reads,
        "writes": writes,
        "local_calls": local_calls,
        "missing_calls": missing_calls,
    }


def infer_behavior_lines(area: str, rollup: dict[str, object]) -> list[str]:
    reads = rollup.get("reads", {}) if isinstance(rollup.get("reads", {}), dict) else {}
    writes = rollup.get("writes", {}) if isinstance(rollup.get("writes", {}), dict) else {}
    local_calls = rollup.get("local_calls", []) if isinstance(rollup.get("local_calls", []), list) else []
    missing_calls = rollup.get("missing_calls", []) if isinstance(rollup.get("missing_calls", []), list) else []
    graphs = sorted(str(name) for name in rollup.get("graph_names", set()))
    graph_text = ", ".join(graphs[:5]) if graphs else "-"
    lines: list[str] = []
    if area == "Glide":
        lines.append(f"- Glide logic appears to be split across {graph_text}; start/update checks are tied together through local graph calls.")
        lines.append("- Watch speed, pitch, stamina, and parachute state variables before changing glide feel.")
    elif area == "Sliding":
        lines.append(f"- Sliding logic appears to manage start/clear/decay state across {graph_text}.")
        lines.append("- Variables around slide multiplier, slope decay, replicated slide transform, and stamina are likely behavior-critical.")
    elif area == "Nursing":
        lines.append("- Nursing logic appears to combine server state, allied/team checks, trough visuals, and replicated effectiveness values.")
        lines.append("- Check component visibility/audio/FX references before changing nursing range or visuals.")
    elif area == "MultiUse":
        lines.append("- MultiUse logic likely controls player interaction entries and execution branches.")
        lines.append("- Treat menu entry conditions, team checks, saddle/rider state, and baby-passenger checks as user-facing behavior.")
    elif area == "Damage":
        lines.append("- Damage logic likely adjusts incoming or outgoing damage and may gate baby/passenger stealing behavior.")
        lines.append("- Recheck target team, rider/passenger state, cooldown, and attack-index variables before edits.")
    elif area == "Replication":
        lines.append("- Replication/timer logic appears to coordinate server-owned state updates and client-visible movement changes.")
        lines.append("- Server/client ownership and replicated variables should be reviewed before changing call order.")
    elif area == "Movement":
        lines.append("- Movement logic appears to bridge jump, movement mode, swim/fall state, and animation transitions.")
        lines.append("- Treat movement mode branches and CharacterMovement references as high-impact.")
    elif area == "Parachute":
        lines.append("- Parachute logic appears to manage replicated parachute intent, animation/audio cues, and cooldown timing.")
        lines.append("- Changes to bWantsToParachute, timers, or force duration likely affect both feel and replication.")
    elif area == "HUD":
        lines.append("- HUD logic appears to render status feedback from gameplay variables rather than owning core state.")
        lines.append("- Confirm display-only changes do not hide gameplay-critical warnings or range indicators.")
    elif area == "Passenger":
        lines.append("- Passenger logic appears to compute seat/name-tag offsets and related passenger positioning data.")
        lines.append("- Offset arrays and seat index functions are likely the main risk points.")
    else:
        lines.append(f"- This group does not match a specific ARK behavior area yet; inspect {graph_text} for shared state or parent/native calls.")
    if reads:
        lines.append(f"- Most-read signals: {top_counter_items(reads, 5)}")
    if writes:
        lines.append(f"- Most-written state: {top_counter_items(writes, 5)}")
    if local_calls:
        call_text = ", ".join(f"{item.get('source_graph')} -> {item.get('target_graph')}" for item in local_calls[:5])
        lines.append(f"- Local graph dependencies: {call_text}")
    if missing_calls:
        missing_names = list(dict.fromkeys(str(item.get("function", "")) for item in missing_calls if item.get("function")))
        missing_text = ", ".join(missing_names[:8])
        lines.append(f"- Needs confirmation/capture for: {missing_text}")
    return lines


BEHAVIOR_RULES: dict[str, dict[str, object]] = {
    "Glide": {
        "signals": ("bCanGlide", "bOverrideNewFallVelocity", "StartGlideLocation", "GlidingPullUpMultiplier", "WingTrail", "FlyerForce"),
        "components": ("CharacterMovement", "WingTrail", "ParaAudio"),
        "focus": "Confirm start checks, server/client tick split, fall-velocity override, pull-up modifiers, and visual/audio cues.",
    },
    "Sliding": {
        "signals": ("replicatedSlideLocation", "replicatedSlideRotation", "SlidingAngle", "NewSlideMulti", "TempSlide", "NS_Sliding_VFX"),
        "components": ("CharacterMovement", "Sliding", "VFX"),
        "focus": "Confirm enter/clear paths, slope multiplier changes, replicated transform writes, and client presentation.",
    },
    "Nursing": {
        "signals": ("bIsNursing", "bNurseVisualActive", "BaseNursingRange", "ReplicatedNursingTroughEffectiveness", "NursingTroughFoodEffectiveness"),
        "components": ("TroughVisual", "Nursing", "Status"),
        "focus": "Confirm team checks, enable/disable authority, trough visibility, and replicated effectiveness defaults.",
    },
    "MultiUse": {
        "signals": ("MultiUse", "UseEntries", "BPTryMultiUse", "BPGetMultiUseEntries", "Team", "Rider"),
        "components": ("Inventory", "Status"),
        "focus": "Confirm menu entry availability, use execution branches, team/rider gates, and user-facing text/icon defaults.",
    },
    "Replication": {
        "signals": ("OnRep", "Server", "Client", "Replicated", "Timer", "Authority"),
        "components": ("CharacterMovement", "Status"),
        "focus": "Confirm replicated variables, RepNotify ordering, server-owned writes, and client-only visual updates.",
    },
    "Damage": {
        "signals": ("Damage", "Attack", "Team", "Rider", "Passenger"),
        "components": ("Status",),
        "focus": "Confirm damage adjustment inputs, attacker/target checks, passenger or baby side effects, and return value writes.",
    },
}


def match_rule_terms(terms: Iterable[object], names: Iterable[object]) -> list[str]:
    name_values = [str(name) for name in names if str(name)]
    matches: list[str] = []
    for term in terms:
        lowered = str(term).lower()
        for name in name_values:
            if lowered and lowered in name.lower():
                matches.append(name)
                break
    return list(dict.fromkeys(matches))


def render_behavior_rule_checks(
    sorted_area_items: list[tuple[str, list[dict[str, object]]]],
    rollups: dict[str, dict[str, object]],
    known_defaults: set[str],
    known_components: set[str],
) -> list[str]:
    lines = ["", "## Behavior Rule Checks", ""]
    lines.append(table_row(["Area", "Observed Signals", "Known Defaults", "Known Components", "Review Focus"]))
    lines.append(table_row(["---", "---", "---", "---", "---"]))
    for area, _area_items in sorted_area_items:
        rule = BEHAVIOR_RULES.get(area)
        if not rule:
            continue
        rollup = rollups.get(area, {})
        reads = rollup.get("reads", {}) if isinstance(rollup.get("reads", {}), dict) else {}
        writes = rollup.get("writes", {}) if isinstance(rollup.get("writes", {}), dict) else {}
        graph_names = rollup.get("graph_names", set()) if isinstance(rollup.get("graph_names", set()), set) else set()
        signal_names = set(reads) | set(writes) | set(str(name) for name in graph_names)
        observed = match_rule_terms(rule.get("signals", ()), signal_names)
        defaults = match_rule_terms(rule.get("signals", ()), known_defaults)
        components = match_rule_terms(rule.get("components", ()), known_components)
        lines.append(
            table_row(
                [
                    area,
                    ", ".join(observed[:8]) if observed else "-",
                    ", ".join(defaults[:8]) if defaults else "-",
                    ", ".join(components[:8]) if components else "-",
                    rule.get("focus", ""),
                ]
            )
        )
    lines.append("")
    return lines


def render_behavior_summary(asset_payload: dict[str, object]) -> str:
    metadata = asset_payload.get("metadata", {}) if isinstance(asset_payload.get("metadata", {}), dict) else {}
    graphs = [graph for graph in asset_payload.get("graphs", []) if isinstance(graph, dict)]
    call_graph = asset_payload.get("call_graph", {}) if isinstance(asset_payload.get("call_graph", {}), dict) else {}
    calls = [item for item in call_graph.get("calls", []) if isinstance(item, dict)]
    quality = collect_asset_quality(asset_payload)
    area_graphs: dict[str, list[dict[str, object]]] = {}
    for graph in graphs:
        area = behavior_area(str(graph.get("graph_name", "")))
        area_graphs.setdefault(area, []).append(graph)

    lines = [
        "# Blueprint Behavior Summary",
        "",
        "## Summary",
        "",
        f"- Asset: {metadata.get('asset_name', '-')}",
        f"- Graphs: {metadata.get('graph_count', 0)}",
        f"- Nodes: {metadata.get('node_count', 0)}",
        f"- Note function overrides: {metadata.get('note_function_count', 0)}",
        f"- Confidence: {asset_payload.get('diagnostics', {}).get('confidence_level', '-') if isinstance(asset_payload.get('diagnostics', {}), dict) else '-'}",
        "",
        "## Behavior Areas",
        "",
    ]
    lines.append(table_row(["Area", "Graphs", "Nodes", "Key Graphs"]))
    lines.append(table_row(["---", "---", "---", "---"]))
    for area, area_items in sorted(area_graphs.items(), key=lambda item: (item[0] == "Other", item[0])):
        key_graphs = ", ".join(str(graph.get("graph_name", "")) for graph in area_items[:5])
        nodes = sum(int(graph.get("node_count") or 0) for graph in area_items)
        lines.append(table_row([area, len(area_items), nodes, key_graphs]))

    known_defaults = set()
    defaults = asset_payload.get("class_defaults", {})
    if isinstance(defaults, dict):
        variables = defaults.get("variables", {})
        if isinstance(variables, dict):
            known_defaults.update(str(name) for name in variables)
    known_components = set()
    components = asset_payload.get("component_defaults", {})
    if isinstance(components, dict):
        for component in components.get("components", []):
            if isinstance(component, dict) and component.get("name"):
                known_components.add(str(component.get("name")))

    sorted_area_items = sorted(area_graphs.items(), key=lambda item: (item[0] == "Other", item[0]))
    rollups = {area: behavior_area_rollup(area_items, calls, quality) for area, area_items in sorted_area_items}

    lines.extend(["", "## Inferred Behavior", ""])
    for area, _area_items in sorted_area_items:
        lines.extend([f"### {area}", ""])
        lines.extend(infer_behavior_lines(area, rollups[area]))
        lines.append("")

    lines.extend(render_behavior_rule_checks(sorted_area_items, rollups, known_defaults, known_components))

    lines.extend(["", "## Area Details", ""])
    for area, _area_items in sorted_area_items:
        rollup = rollups[area]
        graph_names = rollup.get("graph_names", set()) if isinstance(rollup.get("graph_names", set()), set) else set()
        area_reads = rollup.get("reads", {}) if isinstance(rollup.get("reads", {}), dict) else {}
        area_writes = rollup.get("writes", {}) if isinstance(rollup.get("writes", {}), dict) else {}
        local_calls = rollup.get("local_calls", []) if isinstance(rollup.get("local_calls", []), list) else []
        missing_calls = rollup.get("missing_calls", []) if isinstance(rollup.get("missing_calls", []), list) else []
        referenced_defaults = {name: area_reads.get(name, 0) + area_writes.get(name, 0) for name in set(area_reads) | set(area_writes) if name in known_defaults}
        referenced_components = {name: area_reads.get(name, 0) + area_writes.get(name, 0) for name in set(area_reads) | set(area_writes) if name in known_components}
        lines.extend([f"### {area}", ""])
        lines.append(f"- Graphs: {', '.join(sorted(graph_names))}")
        lines.append(f"- Top reads: {top_counter_items(area_reads)}")
        lines.append(f"- Top writes: {top_counter_items(area_writes)}")
        lines.append(f"- Class defaults referenced: {top_counter_items(referenced_defaults)}")
        lines.append(f"- Components referenced: {top_counter_items(referenced_components)}")
        if local_calls:
            call_text = ", ".join(f"{item.get('source_graph')} -> {item.get('target_graph')}" for item in local_calls[:12])
            lines.append(f"- Local graph calls: {call_text}")
        else:
            lines.append("- Local graph calls: -")
        if missing_calls:
            missing_text = ", ".join(f"{item.get('source_graph')} -> {item.get('function')}" for item in missing_calls[:12])
            lines.append(f"- Still unresolved graph-like calls: {missing_text}")
        else:
            lines.append("- Still unresolved graph-like calls: -")
        lines.append("")
    return "\n".join(lines)
