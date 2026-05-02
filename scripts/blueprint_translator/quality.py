"""Asset capture quality checks and Unreal/Kismet call classification."""

from __future__ import annotations

from collections import Counter

from .utils import table_row


MATH_OR_DATA_PREFIXES = (
    "abs",
    "add_",
    "array_",
    "boolean",
    "break",
    "concat_",
    "conv_",
    "divide_",
    "equal",
    "fceil",
    "fclamp",
    "finterp",
    "fmax",
    "fmin",
    "greater",
    "less",
    "make",
    "maprange",
    "multiply",
    "nearlyequal",
    "normal",
    "notequal",
    "not_",
    "projectvector",
    "rinterp",
    "rotateangleaxis",
    "select",
    "subtract",
    "vinterp",
    "vector_",
    "vsize",
)

ENGINE_OR_WORLD_NAMES = {
    "consumemovementinputvector",
    "cross_vectorvector",
    "delay",
    "drawdebugline",
    "formatastime",
    "getactorrightvector",
    "getactorupvector",
    "getcomponentbounds",
    "getcharactercontroller",
    "getgametimeinseconds",
    "getnetworktimeinseconds",
    "getactorforwardvector",
    "getcontrolrotation",
    "getcontroller",
    "getdefaultobject",
    "getdistanceto",
    "getdinovelocity",
    "getgroundlocation",
    "getownercontroller",
    "getphysicsvolume",
    "getphysicsvolumeatlocation",
    "getleveluppoints",
    "getshooterhud",
    "gettransform",
    "getplayercharacter",
    "getplayercontroller",
    "getplayerowner",
    "getprimalcharmovementmode",
    "getrandomwanderdestination",
    "getvelocity",
    "getweapon",
    "getworlddeltaseconds",
    "islocallycontrolled",
    "isfirstperson",
    "k2_getactorlocation",
    "k2_getactorrotation",
    "k2_getpawn",
    "k2_getworld",
    "k2_setactorrotation",
    "linetracesingle",
    "linetracesingleforobjects",
    "movetolocation",
    "pctospc",
    "randomfloatinrange",
    "setmovementmode",
    "spawnemitteratlocation",
    "timesince_network",
    "vtracemultibp",
    "vtracemultibp_ignoreactorsarray",
    "vtracesinglebp",
}

TIMER_NAMES = {
    "k2_cleartimer",
    "k2_istimeractive",
    "k2_settimer",
    "k2_settimerfornexttick",
}

ARK_PARENT_OR_RPC_PREFIXES = (
    "bp",
    "client",
    "multi_",
    "owningclient",
    "server",
)

ARK_PARENT_OR_RPC_NAMES = {
    "bpgetcurrentstatusvalue",
    "bphasplayercontroller",
    "bpisconscious",
    "bpistamed",
    "addpassenger",
    "bpnetscharacter",
    "bpnetssetcharactermovementvelocity",
    "bpnetssetcharactermovementvelocity",
    "bpnetssetmovement",
    "blueprintcanriderattack",
    "canchargejump",
    "can parachute",
    "canparachute",
    "cantakepassenger",
    "canruncosmeticevents",
    "cleargliding",
    "clearpassengers",
    "doattack",
    "getequippeditemoftype",
    "getmutationpoints",
    "getnextvalidemptyseat",
    "getpassengersandseatindexes",
    "getpassengersseatindex",
    "hasgeyserbuff",
    "inputrunpressed",
    "inputrunreleased",
    "is valid baby dino for passenger",
    "isatpersonaltamelimit",
    "isalliedwithotherteam",
    "isdedicatedserver",
    "isdead",
    "isfleeing",
    "isgliding",
    "ispointunderwater",
    "isrunning",
    "is sliding",
    "isprimalcharacterorstructure",
    "isinstatusstate",
    "isserver",
    "isspectator",
    "issubmerged",
    "isrunningonserver",
    "modifycurrentstatusvalue",
    "moveforward",
    "notifytribesofbabystolen",
    "requestjumpready",
    "request jump response",
    "shortestangledistance",
    "tamedino",
    "unclaimdino",
    "untamedino",
    "updateimprintingdetailsforcontroller",
}

COMPONENT_OR_PRESENTATION_NAMES = {
    "deactivate",
    "fadein",
    "fadeout",
    "isactive",
    "ismontageplaying",
    "istargeting",
    "hidebonebyname",
    "playanimex",
    "setactive",
    "setdrawcolor",
    "setvisibility",
    "setworldscale3d",
    "stopanimex",
}


def classify_function_call(function_name: str) -> str:
    name = function_name.strip()
    lowered = name.lower()
    if not lowered:
        return "unknown"
    if lowered in TIMER_NAMES:
        return "engine_timer"
    if lowered.startswith("k2_") or lowered in ENGINE_OR_WORLD_NAMES:
        return "unreal_engine"
    if lowered.startswith(MATH_OR_DATA_PREFIXES):
        return "kismet_math_or_data"
    if lowered in COMPONENT_OR_PRESENTATION_NAMES:
        return "component_or_presentation"
    if lowered in ARK_PARENT_OR_RPC_NAMES or lowered.startswith(ARK_PARENT_OR_RPC_PREFIXES):
        return "ark_parent_or_rpc"
    return "blueprint_graph_candidate"


def is_blueprint_graph_candidate(function_name: str) -> bool:
    return classify_function_call(function_name) == "blueprint_graph_candidate"


def infer_asset_graph_type(graph_name: str, payload: dict[str, object] | None = None) -> str:
    name = graph_name.strip()
    lowered = name.lower()
    if lowered == "eventgraph" or lowered in {"shijiantubiao", "event graph"}:
        return "EventGraph"
    if lowered.startswith("onrep") or "onrep" in lowered:
        return "RepNotify"
    if "construction" in lowered:
        return "ConstructionScript"
    if lowered.startswith("macro_") or "macro" in lowered:
        return "Macro"
    if lowered.startswith("collapsegraph") or lowered.startswith("collapsed"):
        return "CollapsedGraph"
    if "timer" in lowered or "tick" in lowered:
        return "Timer/Tick"
    if "multiuse" in lowered:
        return "MultiUse"
    if "hud" in lowered:
        return "HUD"
    if payload:
        events = payload.get("events", [])
        if isinstance(events, list):
            event_names = " ".join(str(item.get("event") or item.get("label") or "") for item in events if isinstance(item, dict)).lower()
            if "onrep" in event_names:
                return "RepNotify"
            if "tick" in event_names or "timer" in event_names:
                return "Timer/Tick"
    return "Function"


def behavior_area(graph_name: str) -> str:
    lowered = graph_name.lower()
    compact = lowered.replace(" ", "").replace("_", "")
    if lowered in {"eventgraph", "event graph"} or compact == "shijiantubiao":
        return "Orchestration"
    if lowered.startswith("collapsegraph") or lowered.startswith("collapsed"):
        return "CollapsedGraph"
    if any(term in lowered for term in ("glide", "gliding", "fallvelocity")):
        return "Glide"
    if "slid" in lowered:
        return "Sliding"
    if "parachute" in lowered or "para" in lowered:
        return "Parachute"
    if "nurs" in lowered or "trough" in lowered:
        return "Nursing"
    if "multiuse" in lowered:
        return "MultiUse"
    if "hud" in lowered:
        return "HUD"
    if "damage" in lowered:
        return "Damage"
    if "passenger" in lowered:
        return "Passenger"
    if any(term in lowered for term in ("sleep", "levelup", "status", "conscious", "died", "death")):
        return "Status"
    if "animnotify" in compact or "anim notify" in lowered or "custom event" in lowered:
        return "Animation"
    if any(term in lowered for term in ("movement", "jump", "run", "correction", "forwardinput", "forward input", "rotate", "pitch")):
        return "Movement"
    if "rep" in lowered or "server" in lowered or "client" in lowered or "timer" in lowered or "non dedicated" in lowered or "nondedicated" in compact:
        return "Replication"
    return "Other"


def asset_variable_counters(asset_payload: dict[str, object]) -> tuple[Counter, Counter]:
    reads: Counter = Counter()
    writes: Counter = Counter()
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
        for node in payload.get("variable_gets", []):
            if isinstance(node, dict):
                name = str(node.get("variable") or node.get("label") or "")
                if name:
                    reads[name] += 1
        for node in payload.get("variable_sets", []):
            if isinstance(node, dict):
                name = str(node.get("variable") or node.get("label") or "")
                if name:
                    writes[name] += 1
    return reads, writes


LOCAL_OR_PARAMETER_NAMES = {
    "delta",
    "movementmode",
    "newslidemulti",
    "tempslide",
    "tempmaxslide",
    "retvelocity",
    "retdamage",
    "breturnwantstorun",
    "jumpinstant",
    "jumptracehit",
    "jumptraceloc",
    "usinglaunchoffset",
}


def known_default_names(asset_payload: dict[str, object]) -> set[str]:
    defaults = asset_payload.get("class_defaults", {})
    known: set[str] = set()
    if isinstance(defaults, dict):
        variables = defaults.get("variables", {})
        if isinstance(variables, dict):
            known.update(str(key) for key in variables)
        class_defaults = defaults.get("class_defaults", {})
        if isinstance(class_defaults, dict):
            known.update(str(key) for key in class_defaults)
    return known


def is_local_or_parameter_candidate(name: str) -> bool:
    lowered = name.lower()
    if lowered in LOCAL_OR_PARAMETER_NAMES:
        return True
    if lowered.startswith(("ret", "return", "temp")):
        return True
    return False


def component_candidate_names(asset_payload: dict[str, object]) -> set[str]:
    return {str(item.get("name", "")) for item in candidate_components(asset_payload, limit=200)}


def known_component_names(asset_payload: dict[str, object]) -> set[str]:
    names: set[str] = set()
    component_context = asset_payload.get("component_defaults", {})
    if isinstance(component_context, dict):
        for component in component_context.get("components", []):
            if isinstance(component, dict):
                name = str(component.get("name") or "")
                if name:
                    names.add(name)
    names.update(component_candidate_names(asset_payload))
    return names


def candidate_default_variables(asset_payload: dict[str, object], limit: int = 40) -> list[dict[str, object]]:
    reads, writes = asset_variable_counters(asset_payload)
    known = known_default_names(asset_payload)
    component_names = known_component_names(asset_payload)
    candidates = []
    for name, count in (reads + writes).most_common():
        if name in known:
            continue
        if name in component_names:
            continue
        if is_local_or_parameter_candidate(name):
            continue
        score = count + writes.get(name, 0)
        if count >= 2 or writes.get(name, 0) >= 1:
            candidates.append({"name": name, "reads": reads.get(name, 0), "writes": writes.get(name, 0), "score": score})
        if len(candidates) >= limit:
            break
    return candidates


def candidate_components(asset_payload: dict[str, object], limit: int = 30) -> list[dict[str, object]]:
    reads, writes = asset_variable_counters(asset_payload)
    component_context = asset_payload.get("component_defaults", {})
    known = set()
    if isinstance(component_context, dict):
        for component in component_context.get("components", []):
            if isinstance(component, dict):
                known.add(str(component.get("name") or ""))
    terms = ("component", "audio", "fx", "trail", "visual", "camera", "mesh", "status")
    exact_names = {"charactermovement", "mycharacterstatuscomponent"}
    candidates = []
    for name, count in (reads + writes).most_common():
        lowered = name.lower()
        if name in known:
            continue
        hint = variable_hint(name)
        if hint in {"boolean", "number", "enum_or_integer", "vector_or_rotator"} and lowered not in exact_names:
            continue
        if lowered in exact_names or lowered.endswith("component") or any(term in lowered for term in terms):
            candidates.append({"name": name, "reads": reads.get(name, 0), "writes": writes.get(name, 0)})
        if len(candidates) >= limit:
            break
    return candidates


def variable_hint(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("b") and len(name) > 1 and name[1:2].isupper():
        return "boolean"
    if any(term in lowered for term in ("range", "radius", "distance", "height", "speed", "mult", "multiplier", "threshold", "percent", "cost", "rate", "time", "duration", "offset")):
        return "number"
    if any(term in lowered for term in ("anim", "audio", "fx", "trail", "visual", "mesh", "camera")):
        return "asset_or_component_reference"
    if "team" in lowered or "mode" in lowered or "state" in lowered:
        return "enum_or_integer"
    if "vector" in lowered or "location" in lowered or "rotation" in lowered:
        return "vector_or_rotator"
    return "unknown"


def component_class_hint(name: str) -> str:
    lowered = name.lower()
    if "movement" in lowered:
        return "CharacterMovementComponent"
    if "status" in lowered:
        return "PrimalCharacterStatusComponent"
    if "audio" in lowered:
        return "AudioComponent"
    if "camera" in lowered:
        return "CameraComponent"
    if "fx" in lowered or "trail" in lowered or "_ns" in lowered or "visual" in lowered:
        return "Niagara/Particle/SceneComponent"
    if "mesh" in lowered:
        return "MeshComponent"
    return ""


def build_defaults_suggestions(asset_payload: dict[str, object], limit: int = 60) -> dict[str, object]:
    metadata = asset_payload.get("metadata", {})
    suggestions: dict[str, object] = {
        "asset_name": metadata.get("asset_name", ""),
        "source": "Generated from parsed Blueprint variable reads/writes after excluding known defaults, component-like references, and obvious local/parameter names.",
        "variables": {},
        "classDefaults": {},
    }
    variables = suggestions["variables"]
    assert isinstance(variables, dict)
    for item in candidate_default_variables(asset_payload, limit=limit):
        name = str(item.get("name", ""))
        variables[name] = {
            "default": None,
            "_hint": variable_hint(name),
            "_kind": "class_or_inherited_default_candidate",
            "_reads": item.get("reads", 0),
            "_writes": item.get("writes", 0),
            "_todo": "Confirm whether this is inherited/native state or a true Class Default still missing from the DevKit export.",
        }
    return suggestions


def build_components_suggestions(asset_payload: dict[str, object], limit: int = 50) -> dict[str, object]:
    metadata = asset_payload.get("metadata", {})
    components = []
    for item in candidate_components(asset_payload, limit=limit):
        name = str(item.get("name", ""))
        components.append(
            {
                "name": name,
                "class": component_class_hint(name),
                "defaults": {},
                "purpose": "",
                "_reads": item.get("reads", 0),
                "_writes": item.get("writes", 0),
                "_todo": "Confirm component class and fill important component defaults from the Blueprint Components panel.",
            }
        )
    return {
        "asset_name": metadata.get("asset_name", ""),
        "source": "Generated from parsed Blueprint variable/component-like references. Confirm names/classes in ARK DevKit.",
        "components": components,
    }


def count_diagnostic_value(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 1 if value else 0


def unique_call_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        marker = (str(item.get("source_graph", "")), str(item.get("function", "")))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def collect_asset_quality(asset_payload: dict[str, object]) -> dict[str, object]:
    call_graph = asset_payload.get("call_graph", {})
    calls = [item for item in call_graph.get("calls", []) if isinstance(item, dict)]
    classification_counts = Counter(str(item.get("call_kind") or "unknown") for item in calls)
    missing_candidates = unique_call_items([item for item in call_graph.get("missing_targets", []) if isinstance(item, dict)])
    builtin_calls = [item for item in call_graph.get("native_or_inherited_calls", []) if isinstance(item, dict)]
    graph_rows = []
    graph_type_suggestions = []
    unresolved_rows = []
    unsupported_types: Counter = Counter()
    behavior_counts: Counter = Counter()
    for graph in asset_payload.get("graphs", []):
        if not isinstance(graph, dict):
            continue
        payload = graph.get("payload", {}) if isinstance(graph.get("payload", {}), dict) else {}
        diagnostics = payload.get("diagnostics", {}) if isinstance(payload, dict) else {}
        graph_name = str(graph.get("graph_name", ""))
        current_type = str(graph.get("graph_type") or "Unknown")
        suggested_type = infer_asset_graph_type(graph_name, payload)
        behavior = behavior_area(graph_name)
        behavior_counts[behavior] += 1
        unresolved = len(diagnostics.get("unresolved_links", []))
        unsupported = list(diagnostics.get("unsupported_node_types", []))
        unsupported_types.update(str(item) for item in unsupported)
        row = {
            "graph": graph_name,
            "type": current_type,
            "suggested_type": suggested_type,
            "behavior_area": behavior,
            "nodes": graph.get("node_count", 0),
            "confidence": diagnostics.get("confidence_level", ""),
            "unresolved_links": unresolved,
            "unsupported_node_types": len(unsupported),
            "unknown_source_pins": count_diagnostic_value(diagnostics.get("pins_with_unknown_source", [])),
            "missing_entry_points": count_diagnostic_value(diagnostics.get("missing_entry_points", [])),
        }
        graph_rows.append(row)
        if current_type in {"", "Unknown"} and suggested_type != "Unknown":
            graph_type_suggestions.append({"graph": graph_name, "suggested_type": suggested_type, "behavior_area": behavior})
        if unresolved or diagnostics.get("confidence_level") == "low" or row["missing_entry_points"]:
            unresolved_rows.append(row)
    return {
        "metadata": asset_payload.get("metadata", {}),
        "call_classification_counts": dict(classification_counts),
        "builtin_call_count": len(builtin_calls),
        "blueprint_missing_candidate_count": len(missing_candidates),
        "blueprint_missing_candidates": missing_candidates[:80],
        "builtin_call_examples": builtin_calls[:80],
        "graph_quality": graph_rows,
        "attention_graphs": unresolved_rows,
        "graph_type_suggestions": graph_type_suggestions,
        "behavior_counts": dict(behavior_counts),
        "unsupported_node_types": dict(unsupported_types.most_common()),
        "default_variable_candidates": candidate_default_variables(asset_payload),
        "component_candidates": candidate_components(asset_payload),
    }


def render_capture_quality_report(asset_payload: dict[str, object]) -> str:
    quality = collect_asset_quality(asset_payload)
    metadata = quality.get("metadata", {})
    lines = [
        "# Blueprint Capture Quality Report",
        "",
        "## Summary",
        "",
        f"- Asset: {metadata.get('asset_name', '-')}",
        f"- Graphs: {metadata.get('graph_count', 0)}",
        f"- Nodes: {metadata.get('node_count', 0)}",
        f"- Note function overrides: {metadata.get('note_function_count', 0)}",
        f"- Builtin/native/inherited calls classified: {quality.get('builtin_call_count', 0)}",
        f"- Blueprint graph candidates not matched: {quality.get('blueprint_missing_candidate_count', 0)}",
        f"- Graphs needing attention: {len(quality.get('attention_graphs', []))}",
        "",
        "## Next Capture Actions",
        "",
    ]
    attention = list(quality.get("attention_graphs", []))
    if attention:
        lines.append(table_row(["Graph", "Reason", "Nodes", "Confidence"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in attention:
            reasons = []
            if item.get("unresolved_links"):
                reasons.append(f"{item.get('unresolved_links')} unresolved links")
            if item.get("missing_entry_points"):
                reasons.append("missing entry point")
            if item.get("confidence") == "low":
                reasons.append("low confidence")
            lines.append(table_row([item.get("graph"), ", ".join(reasons) or "review", item.get("nodes"), item.get("confidence")]))
    else:
        lines.append("- No graph-copy completeness problems were detected.")
    lines.extend(["", "## Likely Missing Blueprint Graphs", ""])
    missing = list(quality.get("blueprint_missing_candidates", []))
    if missing:
        lines.append(table_row(["Source Graph", "Function", "Classification"]))
        lines.append(table_row(["---", "---", "---"]))
        seen = set()
        for item in missing:
            key = (item.get("source_graph"), item.get("function"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(table_row([item.get("source_graph"), item.get("function"), item.get("call_kind", "blueprint_graph_candidate")]))
    else:
        lines.append("- No unmatched calls look like definitely missing Blueprint graph pages.")
    lines.extend(["", "## Builtin / Native / Inherited Call Noise", ""])
    counts = quality.get("call_classification_counts", {})
    if counts:
        lines.append(table_row(["Classification", "Count"]))
        lines.append(table_row(["---", "---"]))
        for name, count in sorted(counts.items()):
            lines.append(table_row([name, count]))
    else:
        lines.append("- none")
    lines.extend(["", "## Suggested Graph Types", ""])
    suggestions = list(quality.get("graph_type_suggestions", []))
    if suggestions:
        lines.append(table_row(["Graph", "Suggested Type", "Behavior Area"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in suggestions:
            lines.append(table_row([item.get("graph"), item.get("suggested_type"), item.get("behavior_area")]))
    else:
        lines.append("- No graph type changes suggested.")
    lines.extend(["", "## Behavior Areas", ""])
    behavior_counts = quality.get("behavior_counts", {})
    if behavior_counts:
        lines.append(table_row(["Area", "Graphs"]))
        lines.append(table_row(["---", "---"]))
        for name, count in sorted(behavior_counts.items()):
            lines.append(table_row([name, count]))
    else:
        lines.append("- none")
    lines.extend(["", "## Remaining Class/Inherited Defaults To Check", ""])
    defaults = list(quality.get("default_variable_candidates", []))
    if defaults:
        lines.append(table_row(["Variable", "Reads", "Writes"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in defaults:
            lines.append(table_row([item.get("name"), item.get("reads"), item.get("writes")]))
    else:
        lines.append("- No variable default candidates were detected.")
    lines.extend(["", "## Component Candidates", ""])
    components = list(quality.get("component_candidates", []))
    if components:
        lines.append(table_row(["Name", "Reads", "Writes"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in components:
            lines.append(table_row([item.get("name"), item.get("reads"), item.get("writes")]))
    else:
        lines.append("- No component candidates were detected.")
    lines.extend(["", "## Unsupported Node Types", ""])
    unsupported = quality.get("unsupported_node_types", {})
    if unsupported:
        lines.append(table_row(["Node Type", "Graphs"]))
        lines.append(table_row(["---", "---"]))
        for name, count in unsupported.items():
            lines.append(table_row([name, count]))
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)

def render_next_actions(asset_payload: dict[str, object]) -> str:
    quality = collect_asset_quality(asset_payload)
    metadata = quality.get("metadata", {})
    attention = list(quality.get("attention_graphs", []))
    missing = list(quality.get("blueprint_missing_candidates", []))
    defaults = list(quality.get("default_variable_candidates", []))
    components = list(quality.get("component_candidates", []))
    default_count = int(metadata.get("default_variable_count", 0) or 0)
    component_count = int(metadata.get("component_count", 0) or 0)

    lines = [
        "# Blueprint Next Actions",
        "",
        f"- Asset: {metadata.get('asset_name', '-')}",
        f"- Graphs: {metadata.get('graph_count', 0)}",
        f"- Nodes: {metadata.get('node_count', 0)}",
        f"- Parsed default variables: {default_count}",
        f"- Parsed components: {component_count}",
        f"- Note function overrides: {metadata.get('note_function_count', 0)}",
        "",
        "## 1. 先复查图页完整性",
        "",
    ]
    if attention:
        lines.append(table_row(["Graph", "Action", "Reason"]))
        lines.append(table_row(["---", "---", "---"]))
        for item in attention:
            reasons = []
            if item.get("unresolved_links"):
                reasons.append(f"{item.get('unresolved_links')} unresolved links")
            if item.get("missing_entry_points"):
                reasons.append("missing entry point")
            if item.get("confidence") == "low":
                reasons.append("low confidence")
            action = "重新打开该图页，Ctrl+A / Ctrl+C 后用采集向导覆盖保存。"
            if str(item.get("graph", "")).lower().startswith("collapsegraph"):
                action = "确认是否能进入 collapsed graph 内部；能进入就复制内部图。"
            lines.append(table_row([item.get("graph"), action, ", ".join(reasons)]))
    else:
        lines.append("- 没发现明显复制不完整的图页。")

    lines.extend(["", "## 2. 判断是否需要补采函数图", ""])
    if missing:
        lines.append("这些名字更像资产自身的 Blueprint 函数/事件，而不是 Kismet/native 噪声。能找到对应图页就补采；确认来自父类或原生代码就写进 `notes.md`。")
        lines.append("")
        lines.append(table_row(["Source Graph", "Function"]))
        lines.append(table_row(["---", "---"]))
        for item in missing[:40]:
            lines.append(table_row([item.get("source_graph"), item.get("function")]))
    else:
        lines.append("- 没有明显像漏采 Blueprint 函数图的调用。")

    lines.extend(["", "## 3. 默认值状态 / 填 defaults.json", ""])
    if default_count:
        lines.append(f"- DevKit 导出器已经解析到 {default_count} 个蓝图默认值；这些不需要再手填。")
    else:
        lines.append("- 还没有解析到蓝图默认值，需要先运行 DevKit 默认值导出器。")
    if defaults:
        lines.append("- 下面只保留仍可能来自继承/native 状态的候选，不再混入组件引用和明显局部变量。")
        lines.append("")
        lines.append(table_row(["Variable", "Hint", "Reads", "Writes"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in defaults[:30]:
            name = str(item.get("name", ""))
            lines.append(table_row([name, variable_hint(name), item.get("reads"), item.get("writes")]))
    else:
        lines.append("- 当前没有需要优先手填的 class default 候选。")

    lines.extend(["", "## 4. 组件状态 / 填 components.json", ""])
    if component_count:
        lines.append(f"- `components.json` 已有 {component_count} 个组件或组件候选。优先确认 `source=analysis_candidate` 的条目是否是真实组件。")
    else:
        lines.append("- `components.json` 仍为空；下一步要增强 DevKit 组件导出或先写入组件候选。")
    if components:
        lines.append("")
        lines.append(table_row(["Component/Reference", "Class Hint", "Reads", "Writes"]))
        lines.append(table_row(["---", "---", "---", "---"]))
        for item in components[:30]:
            name = str(item.get("name", ""))
            lines.append(table_row([name, component_class_hint(name), item.get("reads"), item.get("writes")]))
    else:
        if component_count:
            lines.append("- 当前组件候选已经写入 `components.json`，没有新的组件候选。")
        else:
            lines.append("- 暂无组件候选。")

    lines.extend(
        [
            "",
            "## 5. 重新生成报告",
            "",
            "```powershell",
            f"python scripts\\bp_clipboard_to_prompt.py --asset-dir captures\\{metadata.get('asset_name', 'YourAsset')} --output-dir captures\\{metadata.get('asset_name', 'YourAsset')}\\output",
            "```",
            "",
        ]
    )
    return "\n".join(lines)
