"""Formula candidate extraction for captured ARK Blueprint assets."""

from __future__ import annotations

import datetime as _dt
import re
from collections import Counter
from typing import Any

from .utils import safe_filename, table_row


FORMULA_SCHEMA = "ark.blueprint.formula_candidates.v1"

FORMULA_FUNCTION_KEYWORDS = (
    "RandomFloatInRange",
    "RandomIntegerInRange",
    "RandomBoolWithWeight",
    "MapRangeClamped",
    "Clamp",
    "FClamp",
    "Lerp",
    "Curve",
    "Select",
    "SelectFloat",
    "SelectString",
    "Branch",
    "IfThenElse",
    "Switch",
    "MakeStruct",
    "BreakStruct",
    "SetFieldsInStruct",
    "Array_Length",
    "GetArrayItem",
    "Multiply_",
    "Divide_",
    "Add_",
    "Subtract_",
    "Greater_",
    "Less_",
    "EqualEqual_",
    "InRange_",
    "FTrunc",
    "FFloor",
    "FCeil",
    "BooleanAND",
    "BooleanOR",
    "Not_PreBool",
    "GetCustomItemData",
    "SetCustomItemData",
    "AddBuff",
    "RemoveBuff",
    "HasBuff",
    "GiveItem",
    "AddItem",
    "ApplyDamage",
    "SetTimer",
    "Delay",
)

MECHANISM_KEYWORDS: dict[str, list[str]] = {
    "xp_rule": ["XP", "Experience", "KillXP", "StoredXP"],
    "stat_weight": ["StatWeight", "InheritStatWeight", "DistributionForMaxWeight", "InheritStatWeightMinMax"],
    "stat_value": ["BaseStat", "StatusValue", "CharacterStatus", "Torpor", "Food", "Stamina", "Health", "Melee"],
    "drop_chance": ["Chance", "Random", "Weight", "Loot", "Drop"],
    "drop_weight": ["Weight", "EntryWeight", "SetWeight", "Loot"],
    "quantity_range": ["Quantity", "MinQuantity", "MaxQuantity"],
    "quality_range": ["Quality", "MinQuality", "MaxQuality"],
    "buff_strength": ["Damage", "Multiplier", "Strength", "Resist", "Heal", "Modifier"],
    "buff_duration": ["Duration", "LifeSpan", "BuffTime"],
    "tick_interval": ["Tick", "Interval", "Period"],
    "cooldown": ["Cooldown", "Delay", "Timer"],
    "consume_rule": ["Consume", "UseItem", "RemoveItem"],
    "crafting_cost": ["Craft", "Cost", "Ingredient"],
    "item_grant": ["GiveItem", "AddItem", "Reward"],
    "treasure_reward": ["Treasure", "Buried", "Reward", "SupplyCrate"],
    "custom_item_data": ["CustomItemData", "GetCustomItemData", "SetCustomItemData"],
}

EXTERNAL_FUNCTIONS = {
    "GetCustomItemData",
    "SetCustomItemData",
    "AddBuff",
    "RemoveBuff",
    "HasBuff",
    "GiveItem",
    "AddItem",
    "ApplyDamage",
    "SetTimer",
    "Delay",
}


def _metadata(asset_payload: dict[str, object]) -> dict[str, Any]:
    value = asset_payload.get("metadata", {})
    return value if isinstance(value, dict) else {}


def _uasset_binary(asset_payload: dict[str, object]) -> dict[str, Any]:
    value = asset_payload.get("uasset_binary", {})
    return value if isinstance(value, dict) else {}


def _graph_name(graph: dict[str, object]) -> str:
    payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
    return str(graph.get("graph_name") or graph.get("graph") or metadata.get("graph_name") or "")


def _graph_payload(graph: dict[str, object]) -> dict[str, Any]:
    value = graph.get("payload", graph)
    return value if isinstance(value, dict) else {}


def _nodes(graph: dict[str, object]) -> list[dict[str, Any]]:
    payload = _graph_payload(graph)
    return [node for node in payload.get("nodes", []) if isinstance(node, dict)]


def _node_label(node: dict[str, Any]) -> str:
    return str(
        node.get("function")
        or node.get("variable")
        or node.get("event")
        or node.get("macro")
        or node.get("label")
        or node.get("name")
        or node.get("class")
        or node.get("node_type")
        or ""
    )


def _unique(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(str(item) for item in values if str(item))]


def _matches_keyword(value: str, keyword: str) -> bool:
    return keyword.lower() in value.lower()


def _contains_formula_keyword(value: str) -> bool:
    return any(_matches_keyword(value, keyword) for keyword in FORMULA_FUNCTION_KEYWORDS)


def _structured_default_variables(defaults: dict[str, Any]) -> bool:
    variables = defaults.get("variables", {})
    return isinstance(variables, dict) and any(isinstance(value, dict) for value in variables.values())


def _class_defaults(asset_payload: dict[str, object]) -> dict[str, Any]:
    defaults = asset_payload.get("class_defaults", {})
    uasset_defaults = _uasset_binary(asset_payload).get("class_defaults", {})
    if isinstance(defaults, dict) and _structured_default_variables(defaults):
        return defaults
    if isinstance(uasset_defaults, dict) and _structured_default_variables(uasset_defaults):
        return uasset_defaults
    if isinstance(defaults, dict):
        return defaults
    return uasset_defaults if isinstance(uasset_defaults, dict) else {}


def _default_variables(asset_payload: dict[str, object]) -> dict[str, dict[str, Any]]:
    defaults = _class_defaults(asset_payload)
    variables = defaults.get("variables", {})
    if not isinstance(variables, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in variables.items():
        name = str(key)
        if isinstance(value, dict):
            normalized[name] = value
        else:
            normalized[name] = {
                "value": value,
                "type": "",
                "source": "class_default",
                "confidence": "unknown",
            }
    return normalized


def _default_properties(asset_payload: dict[str, object]) -> list[dict[str, Any]]:
    defaults = _class_defaults(asset_payload)
    return [item for item in defaults.get("properties", []) if isinstance(item, dict)]


def _asset_path(asset_payload: dict[str, object]) -> str:
    metadata = _metadata(asset_payload)
    uasset = _uasset_binary(asset_payload)
    for key in ("asset_path", "object_path"):
        value = metadata.get(key) or uasset.get(key)
        if value:
            return str(value)
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        payload = _graph_payload(graph)
        graph_metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
        value = graph_metadata.get("asset_path") or graph_metadata.get("object_path")
        if value:
            return str(value)
    return ""


def _asset_type(asset_name: str, asset_path: str) -> str:
    text = f"{asset_path}/{asset_name}".lower()
    if "primalitem" in text:
        return "primal_item_blueprint"
    if "buff" in text:
        return "buff_blueprint"
    if "supplycrate" in text or "loot" in text:
        return "loot_or_supply_crate"
    if "statuscomponent" in text:
        return "status_component_blueprint"
    if "primalgamedata" in text:
        return "primal_game_data"
    return ""


def _domain_for(asset_type: str, asset_name: str) -> str:
    if asset_type == "primal_item_blueprint" or asset_name.startswith("PrimalItem"):
        return "item"
    if asset_type == "buff_blueprint" or asset_name.startswith("Buff"):
        return "buff"
    if asset_type == "loot_or_supply_crate":
        return "loot"
    if asset_type == "status_component_blueprint":
        return "status"
    if asset_type == "primal_game_data":
        return "game_data"
    return "blueprint"


def collect_math_nodes(graph: dict[str, object]) -> list[str]:
    """Return formula-relevant function or node names from one graph."""

    names: list[str] = []
    for node in _nodes(graph):
        label = _node_label(node)
        if _contains_formula_keyword(label):
            names.append(label)
        semantic = node.get("semantic", {})
        if isinstance(semantic, dict):
            for key in ("function", "operator", "kind"):
                value = str(semantic.get(key) or "")
                if _contains_formula_keyword(value):
                    names.append(value)
    return _unique(names)


def collect_formula_variables(graph: dict[str, object]) -> list[str]:
    variables: list[str] = []
    payload = _graph_payload(graph)
    for key in ("variable_gets", "variable_sets"):
        for item in payload.get(key, []):
            if isinstance(item, dict):
                variables.append(str(item.get("variable") or item.get("label") or ""))
    for node in _nodes(graph):
        value = str(node.get("variable") or "")
        if value:
            variables.append(value)
        for pin in node.get("pins", []):
            if not isinstance(pin, dict):
                continue
            default_object = str(pin.get("default_object") or "")
            if default_object and not default_object.startswith("K2Node"):
                variables.append(default_object)
    return _unique(variables)


def _collect_functions(graph: dict[str, object]) -> list[str]:
    payload = _graph_payload(graph)
    values: list[str] = []
    for item in payload.get("function_calls", []):
        if isinstance(item, dict):
            values.append(str(item.get("function") or item.get("label") or ""))
    for node in _nodes(graph):
        values.append(str(node.get("function") or ""))
    return _unique(values)


def infer_mechanism_type(functions: list[str], variables: list[str], graph_name: str, asset_name: str) -> str:
    haystack = " ".join([graph_name, asset_name, *functions, *variables])
    best_type = "unknown_formula_candidate"
    best_score = 0
    for mechanism_type, keywords in MECHANISM_KEYWORDS.items():
        score = sum(1 for keyword in keywords if _matches_keyword(haystack, keyword))
        if score > best_score:
            best_score = score
            best_type = mechanism_type
    return best_type


def _graph_link_quality(graph: dict[str, object]) -> dict[str, object]:
    payload = _graph_payload(graph)
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
    counts = metadata.get("link_resolution_counts", {})
    if not isinstance(counts, dict):
        counts = {}
    method_counts = metadata.get("link_resolution_method_counts", {})
    if not method_counts and isinstance(counts, dict):
        method_counts = counts.get("method_counts", {})
    if not isinstance(method_counts, dict):
        method_counts = {}
    return {
        "resolution_counts": counts,
        "method_counts": method_counts,
        "has_heuristic": int(counts.get("resolved_pin_heuristic") or 0) > 0,
        "unresolved_count": int(counts.get("unresolved") or 0)
        + int(counts.get("node_resolved_pin_unknown") or 0)
        + int(counts.get("cross_graph_or_missing_node") or 0),
    }


def _input_defaults(asset_payload: dict[str, object], mechanism_type: str, graph_name: str, variables: list[str]) -> list[dict[str, object]]:
    default_variables = _default_variables(asset_payload)
    wanted = set(variables)
    keywords = MECHANISM_KEYWORDS.get(mechanism_type, [])
    inputs: list[dict[str, object]] = []
    for name, info in default_variables.items():
        if name in wanted or any(_matches_keyword(name, keyword) for keyword in keywords):
            inputs.append(
                {
                    "name": name,
                    "value": info.get("value"),
                    "type": info.get("type", ""),
                    "confidence": info.get("confidence", "unknown"),
                    "source": info.get("source", "class_default"),
                }
            )
    if mechanism_type == "stat_weight" and "DistributionForMaxWeight" in default_variables:
        info = default_variables["DistributionForMaxWeight"]
        if not any(item.get("name") == "DistributionForMaxWeight" for item in inputs):
            inputs.insert(
                0,
                {
                    "name": "DistributionForMaxWeight",
                    "value": info.get("value"),
                    "type": info.get("type", ""),
                    "confidence": info.get("confidence", "unknown"),
                    "source": info.get("source", "class_default"),
                },
            )
    return inputs[:25]


def collect_external_dependencies(asset_payload: dict[str, object], graph_name: str) -> list[dict[str, object]]:
    graph_lookup = {
        _graph_name(graph): graph
        for graph in asset_payload.get("graphs", [])
        if isinstance(graph, dict)
    }
    graph = graph_lookup.get(graph_name, {})
    functions = _collect_functions(graph) if isinstance(graph, dict) else []
    dependencies: list[dict[str, object]] = []
    for function in functions:
        if function in EXTERNAL_FUNCTIONS:
            dependencies.append(
                {
                    "kind": "native_or_external_function_body",
                    "name": function,
                    "reason": "function body is not visible in the captured Blueprint graph",
                }
            )
    call_graph = asset_payload.get("call_graph", {}) if isinstance(asset_payload.get("call_graph", {}), dict) else {}
    for item in call_graph.get("native_or_inherited_calls", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("source_graph") or "") != graph_name:
            continue
        dependencies.append(
            {
                "kind": str(item.get("call_kind") or "native_or_parent_call"),
                "name": str(item.get("function") or ""),
                "reason": "call target is native, inherited, or missing from this asset capture",
            }
        )
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, object]] = []
    for item in dependencies:
        marker = (str(item.get("kind") or ""), str(item.get("name") or ""))
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def _struct_missing_evidence(asset_payload: dict[str, object]) -> list[str]:
    missing: list[str] = []
    for item in _default_properties(asset_payload):
        name = str(item.get("name") or "")
        if name != "InheritStatWeightMinMax":
            continue
        value = item.get("value", {})
        parsed = value.get("parsed") if isinstance(value, dict) else item.get("parsed")
        if parsed is False or str(item.get("confidence") or "").lower() == "low":
            missing.append("InheritStatWeightMinMax struct is present but not fully parsed")
    return missing


def _missing_evidence(
    asset_payload: dict[str, object],
    link_quality: dict[str, object],
    external_dependencies: list[dict[str, object]],
    mechanism_type: str,
) -> list[str]:
    missing: list[str] = []
    if link_quality.get("has_heuristic"):
        missing.append("Pin/LinkedTo includes resolved_pin_heuristic links")
    if int(link_quality.get("unresolved_count") or 0) > 0:
        missing.append("Some graph links still do not resolve to exact target pins")
    if external_dependencies:
        missing.append("Native, parent, or external function bodies are not visible")
    if mechanism_type == "stat_weight":
        missing.extend(_struct_missing_evidence(asset_payload))
    return _unique(missing)


def formula_confidence(
    link_quality: dict[str, object],
    external_dependencies: list[dict[str, object]],
    missing_evidence: list[str],
    graph_confidence: str,
) -> str:
    if external_dependencies or len(missing_evidence) >= 2:
        return "low"
    if link_quality.get("has_heuristic") or graph_confidence == "low" or missing_evidence:
        return "medium"
    if graph_confidence == "high":
        return "high"
    return "medium"


def graph_formula_signal(graph: dict[str, object]) -> dict[str, object]:
    functions = _collect_functions(graph)
    variables = collect_formula_variables(graph)
    math_nodes = collect_math_nodes(graph)
    graph_name = _graph_name(graph)
    payload = _graph_payload(graph)
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {}
    return {
        "graph": graph_name,
        "functions": functions,
        "variables": variables,
        "math_nodes": math_nodes,
        "graph_confidence": str(graph.get("confidence") or metadata.get("confidence") or "unknown"),
        "link_quality": _graph_link_quality(graph),
        "has_formula_signal": bool(math_nodes)
        or any(_contains_formula_keyword(value) for value in [graph_name, *functions, *variables]),
    }


def _visible_rule_text(graph_name: str, inputs: list[dict[str, object]], math_nodes: list[str]) -> str:
    input_bits = [f"{item.get('name')} = {item.get('value')}" for item in inputs[:8] if item.get("name")]
    node_bits = math_nodes[:12]
    parts = []
    if input_bits:
        parts.append("; ".join(input_bits))
    if node_bits:
        parts.append("visible nodes: " + " -> ".join(node_bits))
    return f"{graph_name}: " + ("; ".join(parts) if parts else "formula-relevant graph structure detected")


def _candidate_id(asset_name: str, graph_name: str, mechanism_type: str) -> str:
    base = safe_filename(f"{asset_name}_{graph_name}_{mechanism_type}", "formula_candidate")
    return re.sub(r"_+", "_", base).strip("_").lower()


def _evidence(graph_name: str, inputs: list[dict[str, object]], math_nodes: list[str], functions: list[str]) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for item in inputs[:12]:
        evidence.append(
            {
                "source": "class_defaults",
                "graph": graph_name,
                "name": item.get("name", ""),
                "value": item.get("value"),
                "confidence": item.get("confidence", "unknown"),
            }
        )
    for function in functions[:40]:
        if function in math_nodes or _contains_formula_keyword(function):
            evidence.append({"source": "graph_node", "graph": graph_name, "function": function})
    return evidence[:60]


def _next_probe(missing_evidence: list[str], external_dependencies: list[dict[str, object]]) -> list[dict[str, object]]:
    probes: list[dict[str, object]] = []
    if any("Pin/LinkedTo" in item or "target pins" in item for item in missing_evidence):
        probes.append(
            {
                "kind": "pin_resolution",
                "detail": "Decode exact LinkedTo target PinId values before promoting this candidate.",
            }
        )
    if any("InheritStatWeightMinMax" in item for item in missing_evidence):
        probes.append(
            {
                "kind": "struct_parser",
                "detail": "Parse InheritStatWeightMinMax StructProperty fields from the class default object.",
            }
        )
    for dependency in external_dependencies[:8]:
        probes.append(
            {
                "kind": "external_dependency",
                "detail": f"Confirm visible behavior around {dependency.get('name')} without assuming its native body.",
            }
        )
    return probes


def _db_targets(asset_type: str) -> list[str]:
    mapping = {
        "primal_item_blueprint": "primal_items.sqlite",
        "buff_blueprint": "buffs.sqlite",
        "loot_or_supply_crate": "loot.sqlite",
        "status_component_blueprint": "status_components.sqlite",
        "primal_game_data": "primal_game_data.sqlite",
    }
    return ["formula_candidates", mapping.get(asset_type, "asset_catalog.sqlite")]


def build_formula_candidates(asset_payload: dict[str, object]) -> dict[str, object]:
    metadata = _metadata(asset_payload)
    asset_name = str(metadata.get("asset_name") or _uasset_binary(asset_payload).get("asset_name") or "")
    if not asset_name:
        asset_name = str(_class_defaults(asset_payload).get("asset_name") or "BlueprintAsset")
    asset_path = _asset_path(asset_payload)
    asset_type = str(metadata.get("asset_type") or _asset_type(asset_name, asset_path))
    domain = _domain_for(asset_type, asset_name)
    candidates: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []

    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        signal = graph_formula_signal(graph)
        if not signal.get("has_formula_signal"):
            continue
        graph_name = str(signal.get("graph") or "")
        functions = [str(value) for value in signal.get("functions", []) if str(value)]
        variables = [str(value) for value in signal.get("variables", []) if str(value)]
        math_nodes = [str(value) for value in signal.get("math_nodes", []) if str(value)]
        mechanism_type = infer_mechanism_type(functions, variables, graph_name, asset_name)
        inputs = _input_defaults(asset_payload, mechanism_type, graph_name, variables)
        link_quality = signal.get("link_quality", {}) if isinstance(signal.get("link_quality", {}), dict) else {}
        external_dependencies = collect_external_dependencies(asset_payload, graph_name)
        missing = _missing_evidence(asset_payload, link_quality, external_dependencies, mechanism_type)
        confidence = formula_confidence(link_quality, external_dependencies, missing, str(signal.get("graph_confidence") or "unknown"))
        visible_rule = _visible_rule_text(graph_name, inputs, math_nodes)
        formula_text = (
            f"Candidate only. Visible Blueprint evidence for {mechanism_type}: {visible_rule}. "
            "Do not treat this as a final formula until blockers are resolved."
        )
        candidate_id = _candidate_id(asset_name, graph_name, mechanism_type)
        next_probe = _next_probe(missing, external_dependencies)
        candidate = {
            "id": candidate_id,
            "domain": domain,
            "mechanism_type": mechanism_type,
            "mechanism": f"{graph_name} {mechanism_type}".strip(),
            "player_meaning": f"Visible Blueprint logic may affect {mechanism_type.replace('_', ' ')}.",
            "graph": graph_name,
            "trigger_graphs": [graph_name],
            "visible_rule": visible_rule,
            "formula_text": formula_text,
            "formula_ast": {},
            "inputs": inputs,
            "outputs": [],
            "conditions": [name for name in math_nodes if any(term in name for term in ("Branch", "Select", "Switch", "InRange"))],
            "math_nodes": math_nodes,
            "evidence": _evidence(graph_name, inputs, math_nodes, functions),
            "link_quality": link_quality,
            "external_dependencies": external_dependencies,
            "missing_evidence": missing,
            "confidence": confidence,
            "status": "candidate",
            "db_targets": _db_targets(asset_type),
            "next_probe": next_probe,
        }
        candidates.append(candidate)
        if missing or external_dependencies:
            unresolved.append(
                {
                    "id": f"{candidate_id}_unresolved",
                    "candidate_id": candidate_id,
                    "mechanism_type": mechanism_type,
                    "mechanism": candidate["mechanism"],
                    "known_visible_part": visible_rule,
                    "blocked_by": [item.get("name") for item in external_dependencies if item.get("name")]
                    + [item for item in missing],
                    "missing_evidence": missing,
                    "required_next_probe": next_probe,
                    "priority": 50,
                    "status": "open",
                    "confidence": "unresolved_formula",
                }
            )

    confidence_counts = Counter(str(candidate.get("confidence") or "unknown") for candidate in candidates)
    return {
        "schema": FORMULA_SCHEMA,
        "asset_name": asset_name,
        "asset_path": asset_path,
        "asset_type": asset_type,
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "candidate_count": len(candidates),
            "unresolved_count": len(unresolved),
            "confidence_counts": dict(sorted(confidence_counts.items())),
        },
        "candidates": candidates,
        "unresolved_formulas": unresolved,
    }


def _format_list(values: list[object], limit: int = 8) -> str:
    if not values:
        return "-"
    text_values: list[str] = []
    for value in values[:limit]:
        if isinstance(value, dict):
            text_values.append(str(value.get("name") or value.get("function") or value.get("detail") or value))
        else:
            text_values.append(str(value))
    return ", ".join(text_values)


def render_formula_candidates(payload: dict[str, object]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary", {}), dict) else {}
    candidates = [item for item in payload.get("candidates", []) if isinstance(item, dict)]
    unresolved = [item for item in payload.get("unresolved_formulas", []) if isinstance(item, dict)]
    lines = [
        "# Formula Candidates",
        "",
        "## Summary",
        "",
        f"- Asset: {payload.get('asset_name', '-')}",
        f"- Object path: {payload.get('asset_path', '-') or '-'}",
        f"- Asset type: {payload.get('asset_type', '-') or '-'}",
        f"- Candidates: {summary.get('candidate_count', len(candidates))}",
        f"- Unresolved formulas: {summary.get('unresolved_count', len(unresolved))}",
        "",
        "These rows are candidates only. Native, parent, external, or heuristic-linked behavior remains unresolved.",
        "",
        "## Candidates",
        "",
    ]
    if candidates:
        lines.append(table_row(["ID", "Type", "Graph", "Confidence", "Visible Evidence", "Blockers"]))
        lines.append(table_row(["---", "---", "---", "---", "---", "---"]))
        for candidate in candidates:
            blockers = _format_list(candidate.get("missing_evidence", []) if isinstance(candidate.get("missing_evidence", []), list) else [])
            lines.append(
                table_row(
                    [
                        candidate.get("id", ""),
                        candidate.get("mechanism_type", ""),
                        candidate.get("graph", ""),
                        candidate.get("confidence", ""),
                        candidate.get("visible_rule", ""),
                        blockers,
                    ]
                )
            )
    else:
        lines.append("- No formula candidates detected.")
    lines.extend(["", "## Unresolved Formulas", ""])
    if unresolved:
        lines.append(table_row(["ID", "Candidate", "Blocked By", "Next Probe"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in unresolved:
            blocked = _format_list(item.get("blocked_by", []) if isinstance(item.get("blocked_by", []), list) else [])
            probes = _format_list(item.get("required_next_probe", []) if isinstance(item.get("required_next_probe", []), list) else [])
            lines.append(table_row([item.get("id", ""), item.get("candidate_id", ""), blocked, probes]))
    else:
        lines.append("- No unresolved formulas generated.")
    lines.append("")
    return "\n".join(lines)
