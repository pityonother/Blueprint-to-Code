"""Sidecar context parsing for class defaults, components, and notes."""

from __future__ import annotations

import argparse
import json
import re
from typing import Iterable

from .utils import read_optional_text, split_csvish

def context_from_args(args: argparse.Namespace) -> dict[str, object]:
    return {
        "parent_class": args.parent_class or "",
        "interfaces": split_csvish(args.interfaces),
        "tags": split_csvish(args.tags),
        "defaults_text": read_optional_text(args.defaults_file),
        "components_text": read_optional_text(args.components_file),
        "notes_text": read_optional_text(args.notes_file),
    }


def parse_default_value_entry(value: object) -> object:
    if isinstance(value, dict):
        for key in ("default", "value", "DefaultValue", "Default"):
            if key in value:
                return value[key]
    return value


def parse_defaults_context(context: dict[str, object]) -> dict[str, object]:
    text = str(context.get("defaults_text", "")).strip()
    if not text:
        return {"variables": {}, "class_defaults": {}, "parse_error": ""}

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        variables: dict[str, object] = {}
        for line in text.splitlines():
            stripped = line.strip().lstrip("-").strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = re.match(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>.+)$", stripped)
            if match:
                variables[match.group("name")] = match.group("value").strip()
        return {"variables": variables, "class_defaults": {}, "parse_error": str(exc) if not variables else ""}

    variables_raw = data.get("variables", data if isinstance(data, dict) else {}) if isinstance(data, dict) else {}
    class_defaults_raw = data.get("classDefaults", data.get("class_defaults", {})) if isinstance(data, dict) else {}
    variables = {str(key): parse_default_value_entry(value) for key, value in variables_raw.items()} if isinstance(variables_raw, dict) else {}
    class_defaults = {str(key): parse_default_value_entry(value) for key, value in class_defaults_raw.items()} if isinstance(class_defaults_raw, dict) else {}
    return {"variables": variables, "class_defaults": class_defaults, "parse_error": ""}


def default_value_entries(defaults: dict[str, object]) -> dict[str, dict[str, object]]:
    entries: dict[str, dict[str, object]] = {}
    for section, kind in (("variables", "variable"), ("class_defaults", "class_default")):
        values = defaults.get(section, {})
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            key = str(name)
            entries.setdefault(key, {"name": key, "value": value, "kind": kind})
    return entries


def unique_default_refs(refs: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        marker = (str(ref.get("name", "")), str(ref.get("kind", "")), json.dumps(ref.get("value", ""), ensure_ascii=False, sort_keys=True))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(ref)
    return result


def default_references_for_source(source: str, defaults: dict[str, object]) -> list[dict[str, object]]:
    entries = default_value_entries(defaults)
    if not entries or not source:
        return []
    refs: list[dict[str, object]] = []
    for name, entry in entries.items():
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(str(name))}(?![A-Za-z0-9_])", source):
            refs.append(dict(entry))
    return refs


def default_references_for_pin(pin_name: str, defaults: dict[str, object]) -> list[dict[str, object]]:
    entries = default_value_entries(defaults)
    if not entries or not pin_name:
        return []
    normalized_pin = pin_name.lower()
    return [dict(entry) for name, entry in entries.items() if name.lower() == normalized_pin]


def apply_default_context_to_data_flow(data_flow: dict[str, object], defaults: dict[str, object]) -> dict[str, object]:
    if not default_value_entries(defaults):
        return data_flow
    for key in ("dependencies", "branch_conditions", "set_values", "call_parameters"):
        for item in data_flow.get(key, []):
            if not isinstance(item, dict):
                continue
            refs = unique_default_refs(
                default_references_for_source(str(item.get("source", "")), defaults)
                + default_references_for_pin(str(item.get("pin", "")), defaults)
            )
            if refs:
                item["class_default_refs"] = refs
                if item.get("source") == "<unknown>":
                    first = refs[0]
                    item["source"] = f"{first.get('name')} (class default {first.get('value')})"
    return data_flow


def normalize_component_entry(name_hint: str, value: object) -> dict[str, object]:
    if isinstance(value, str):
        text = value.strip()
        return {"name": name_hint or text, "class": text if name_hint else "", "defaults": {}, "purpose": ""}
    if not isinstance(value, dict):
        return {"name": name_hint, "class": "", "defaults": {}, "purpose": ""}

    name = str(
        value.get("name")
        or value.get("component_name")
        or value.get("componentName")
        or value.get("ComponentName")
        or name_hint
        or ""
    )
    class_name = str(
        value.get("class")
        or value.get("class_name")
        or value.get("className")
        or value.get("type")
        or value.get("Class")
        or ""
    )
    defaults_raw = {}
    for key in ("defaults", "properties", "component_defaults", "componentDefaults", "values"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            defaults_raw = candidate
            break
    defaults = {str(key): parse_default_value_entry(item) for key, item in defaults_raw.items()}
    purpose = str(value.get("purpose") or value.get("description") or "")
    return {"name": name, "class": class_name, "defaults": defaults, "purpose": purpose}


def normalize_components_data(data: object) -> list[dict[str, object]]:
    if isinstance(data, list):
        return [normalize_component_entry("", item) for item in data]
    if not isinstance(data, dict):
        return []

    raw_components = data.get("components", data.get("Components"))
    if isinstance(raw_components, list):
        return [normalize_component_entry("", item) for item in raw_components]
    if isinstance(raw_components, dict):
        return [normalize_component_entry(str(name), value) for name, value in raw_components.items()]
    if any(key in data for key in ("name", "component_name", "componentName", "class", "class_name", "className")):
        return [normalize_component_entry("", data)]
    return [normalize_component_entry(str(name), value) for name, value in data.items()]


def parse_components_text_sidecar(text: str) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    by_name: dict[str, dict[str, object]] = {}
    current: dict[str, object] | None = None

    def ensure_component(name: str) -> dict[str, object]:
        key = name.lower()
        if key not in by_name:
            item = {"name": name, "class": "", "defaults": {}, "purpose": ""}
            by_name[key] = item
            components.append(item)
        return by_name[key]

    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if not stripped or stripped.startswith("#"):
            continue
        name_match = re.match(r"(?i)^(component\s+name|component|name)\s*:\s*(?P<value>.+)$", stripped)
        if name_match:
            current = ensure_component(name_match.group("value").strip())
            continue
        class_match = re.match(r"(?i)^(class|type)\s*:\s*(?P<value>.+)$", stripped)
        if class_match and current is not None:
            current["class"] = class_match.group("value").strip()
            continue
        purpose_match = re.match(r"(?i)^(purpose|description)\s*:\s*(?P<value>.+)$", stripped)
        if purpose_match and current is not None:
            current["purpose"] = purpose_match.group("value").strip()
            continue
        dotted = re.match(r"(?P<component>[A-Za-z_][A-Za-z0-9_]*)\.(?P<prop>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>.+)$", stripped)
        if dotted:
            current = ensure_component(dotted.group("component"))
            defaults = current.setdefault("defaults", {})
            if isinstance(defaults, dict):
                defaults[dotted.group("prop")] = dotted.group("value").strip()
            continue
        key_value = re.match(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>.+)$", stripped)
        if not key_value:
            continue
        name = key_value.group("name")
        value = key_value.group("value").strip()
        if current is not None and not re.search(r"(?i)component|/script/|_c\b", value):
            defaults = current.setdefault("defaults", {})
            if isinstance(defaults, dict):
                defaults[name] = value
        elif re.search(r"(?i)component|/script/|_c\b", value) or "component" in name.lower():
            current = ensure_component(name)
            current["class"] = value
    return components


def parse_components_context(context: dict[str, object]) -> dict[str, object]:
    text = str(context.get("components_text", "")).strip()
    if not text:
        return {"components": [], "parse_error": ""}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        components = parse_components_text_sidecar(text)
        return {"components": components, "parse_error": str(exc) if not components else ""}
    return {"components": normalize_components_data(data), "parse_error": ""}


def component_default_refs_for_pin(pin_name: str, components: dict[str, object]) -> list[dict[str, object]]:
    if not pin_name:
        return []
    refs: list[dict[str, object]] = []
    for component in components.get("components", []):
        if not isinstance(component, dict):
            continue
        defaults = component.get("defaults", {})
        if not isinstance(defaults, dict):
            continue
        for prop, value in defaults.items():
            if str(prop).lower() == pin_name.lower():
                refs.append(
                    {
                        "name": component.get("name", ""),
                        "class": component.get("class", ""),
                        "property": str(prop),
                        "value": value,
                        "matches": ["pin"],
                    }
                )
    return refs


def component_refs_for_source(source: str, components: dict[str, object]) -> list[dict[str, object]]:
    if not source:
        return []
    lowered = source.lower()
    refs: list[dict[str, object]] = []
    for component in components.get("components", []):
        if not isinstance(component, dict):
            continue
        matches: list[str] = []
        name = str(component.get("name", ""))
        class_name = str(component.get("class", ""))
        if name and len(name) >= 3 and name.lower() in lowered:
            matches.append("name")
        if class_name and len(class_name) >= 3 and class_name.lower() in lowered:
            matches.append("class")
        defaults = component.get("defaults", {})
        if isinstance(defaults, dict):
            for prop, value in defaults.items():
                prop_text = str(prop)
                if len(prop_text) >= 4 and re.search(rf"(?<![A-Za-z0-9_]){re.escape(prop_text)}(?![A-Za-z0-9_])", source):
                    refs.append(
                        {
                            "name": name,
                            "class": class_name,
                            "property": prop_text,
                            "value": value,
                            "matches": ["property"],
                        }
                    )
        if matches:
            refs.append({"name": name, "class": class_name, "matches": matches})
    return refs


def unique_component_refs(refs: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for ref in refs:
        marker = (
            str(ref.get("name", "")),
            str(ref.get("class", "")),
            str(ref.get("property", "")),
            json.dumps(ref.get("value", ""), ensure_ascii=False, sort_keys=True),
        )
        if marker in seen:
            continue
        seen.add(marker)
        result.append(ref)
    return result


def apply_component_context_to_data_flow(data_flow: dict[str, object], components: dict[str, object]) -> dict[str, object]:
    if not components.get("components"):
        return data_flow
    for key in ("dependencies", "branch_conditions", "set_values", "call_parameters"):
        for item in data_flow.get(key, []):
            if not isinstance(item, dict):
                continue
            search_text = " ".join(
                str(item.get(field, ""))
                for field in ("source", "node_label", "node_type", "pin")
            )
            pin_refs = component_default_refs_for_pin(str(item.get("pin", "")), components)
            refs = unique_component_refs(component_refs_for_source(search_text, components) + pin_refs)
            if refs:
                item["component_refs"] = refs
                if item.get("source") == "<unknown>" and pin_refs:
                    default_ref = pin_refs[0]
                    item["source"] = f"{default_ref.get('name')}.{default_ref.get('property')} (component default {default_ref.get('value')})"
    return data_flow


def render_context_section(context: dict[str, object]) -> str:
    if not any(context.values()):
        return ""
    lines = ["## Sidecar Context", ""]
    for key in ("parent_class", "interfaces", "tags"):
        value = context.get(key)
        if value:
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    for title, key in [("Components", "components_text"), ("Class Defaults", "defaults_text"), ("Notes", "notes_text")]:
        text = str(context.get(key, "")).strip()
        if text:
            lines.append("")
            lines.append(f"### {title}")
            lines.append("")
            lines.append("```text")
            lines.append(text[:8000])
            lines.append("```")
    lines.append("")
    return "\n".join(lines)
