from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Mapping
from typing import Any


_CONTAINER_TYPES = {
    "ArrayProperty": ("array", "array_parse"),
    "SetProperty": ("set", "set_parse"),
    "StructProperty": ("struct", "struct_parse"),
    "MapProperty": ("map", "map_parse"),
}

_OBJECT_REFERENCE_TYPES = {
    "ObjectProperty",
    "SoftObjectProperty",
    "ClassProperty",
    "SoftClassProperty",
}


def _extra_mapping(extra_json: object) -> dict[str, Any]:
    if isinstance(extra_json, Mapping):
        return {str(key): value for key, value in extra_json.items()}
    if isinstance(extra_json, str):
        try:
            decoded = json.loads(extra_json)
        except (TypeError, ValueError):
            return {}
        if isinstance(decoded, Mapping):
            return {str(key): value for key, value in decoded.items()}
    return {}


def _parse_projection(kind: str, parse: Mapping[str, object] | None) -> dict[str, object]:
    parsed = bool(parse and parse.get("parsed") is True)
    result: dict[str, object] = {"kind": kind, "parsed": parsed}
    if not parse:
        return result
    field_names = {
        "count": "count",
        "element_kind": "elementKind",
        "raw_size": "rawSize",
        "struct_name": "structName",
        "key_kind": "keyKind",
        "value_kind": "valueKind",
    }
    for source_name, output_name in field_names.items():
        value = parse.get(source_name)
        if value is not None and value != "":
            result[output_name] = value
    return result


def _resolved_names(extra: Mapping[str, object]) -> list[str | None]:
    raw_names = extra.get("objects")
    if not isinstance(raw_names, list):
        return []
    names: list[str | None] = []
    for raw_name in raw_names:
        name = str(raw_name or "").strip()
        names.append(name or None)
    return names


def _is_null_reference(value: object) -> bool:
    return value is None or value == 0 or value == ""


def _sequence_index(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolved_object_fields(
    extra: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Collect object names from struct arrays, including nested containers.

    ``valuePath`` is emitted only for nested references so the historical
    direct-field projection stays stable.  Coverage counts non-null reference
    slots and conservative sentinels for nested containers that were not
    decoded; callers can therefore block cross-revision comparison instead of
    comparing revision-local ``FPackageIndex`` integers.
    """

    array_parse = extra.get("array_parse")
    if not isinstance(array_parse, Mapping):
        return [], {"available": 0, "unresolved": 0, "unparsed": 0}
    elements = array_parse.get("elements")
    if not isinstance(elements, list):
        return [], {"available": 0, "unresolved": 0, "unparsed": 0}
    fields: list[dict[str, object]] = []
    coverage = {"available": 0, "unresolved": 0, "unparsed": 0}

    def add_object_field(
        *,
        element_index: int,
        property_index: int,
        property_name: str,
        value_path: list[object],
        raw_value: object,
        resolved_name: object,
        nested: bool,
    ) -> None:
        name = str(resolved_name or "").strip()
        if not name and _is_null_reference(raw_value):
            return
        coverage["available"] += 1
        if not name:
            coverage["unresolved"] += 1
            return
        field: dict[str, object] = {
            "elementIndex": element_index,
            "propertyIndex": property_index,
            "propertyName": property_name,
            "name": name,
        }
        if nested:
            field["valuePath"] = value_path
        fields.append(field)

    def visit_property(
        raw_property: Mapping[str, object],
        *,
        element_index: int,
        property_index: int,
        property_name: str,
        value_path: list[object],
        nested: bool,
    ) -> None:
        type_name = str(raw_property.get("type") or raw_property.get("typeName") or "")
        if not type_name and "object" in raw_property:
            type_name = "ObjectProperty"
        if type_name in _OBJECT_REFERENCE_TYPES:
            add_object_field(
                element_index=element_index,
                property_index=property_index,
                property_name=property_name,
                value_path=value_path,
                raw_value=raw_property.get("value", raw_property.get("package_index")),
                resolved_name=raw_property.get("object"),
                nested=nested,
            )
            return
        if type_name != "ArrayProperty":
            return

        nested_parse = raw_property.get("array_parse")
        if not isinstance(nested_parse, Mapping) or nested_parse.get("parsed") is not True:
            coverage["available"] += 1
            coverage["unresolved"] += 1
            coverage["unparsed"] += 1
            return

        inner_type = str(
            nested_parse.get("element_kind")
            or raw_property.get("inner_type")
            or raw_property.get("element_kind")
            or ""
        )
        raw_values = raw_property.get("value")
        values = raw_values if isinstance(raw_values, list) else []
        raw_elements = nested_parse.get("elements")
        nested_elements = raw_elements if isinstance(raw_elements, list) else []
        raw_names = raw_property.get("objects")
        object_names = raw_names if isinstance(raw_names, list) else []
        count = _sequence_index(nested_parse.get("count"), max(len(values), len(nested_elements)))
        count = max(0, count)

        if inner_type in _OBJECT_REFERENCE_TYPES:
            elements_by_index = {
                _sequence_index(item.get("index"), ordinal): item
                for ordinal, item in enumerate(nested_elements)
                if isinstance(item, Mapping)
            }
            for nested_index in range(count):
                nested_element = elements_by_index.get(nested_index, {})
                raw_value = (
                    values[nested_index]
                    if nested_index < len(values)
                    else nested_element.get("value")
                )
                resolved_name = nested_element.get("object")
                if not resolved_name and nested_index < len(object_names):
                    resolved_name = object_names[nested_index]
                add_object_field(
                    element_index=element_index,
                    property_index=property_index,
                    property_name=property_name,
                    value_path=[*value_path, nested_index],
                    raw_value=raw_value,
                    resolved_name=resolved_name,
                    nested=True,
                )
            return

        if inner_type != "StructProperty":
            return
        if len(nested_elements) < count:
            missing = count - len(nested_elements)
            coverage["available"] += missing
            coverage["unresolved"] += missing
            coverage["unparsed"] += missing
        for nested_ordinal, raw_element in enumerate(nested_elements):
            if not isinstance(raw_element, Mapping):
                continue
            nested_index = _sequence_index(raw_element.get("index"), nested_ordinal)
            nested_properties = raw_element.get("properties")
            if not isinstance(nested_properties, list):
                coverage["available"] += 1
                coverage["unresolved"] += 1
                coverage["unparsed"] += 1
                continue
            for nested_property_index, nested_property in enumerate(nested_properties):
                if not isinstance(nested_property, Mapping):
                    continue
                nested_property_name = str(nested_property.get("name") or "")
                visit_property(
                    nested_property,
                    element_index=element_index,
                    property_index=property_index,
                    property_name=property_name,
                    value_path=[*value_path, nested_index, nested_property_name],
                    nested=True,
                )

    for element_ordinal, raw_element in enumerate(elements):
        if not isinstance(raw_element, Mapping):
            continue
        element_index = _sequence_index(raw_element.get("index"), element_ordinal)
        properties = raw_element.get("properties")
        if not isinstance(properties, list):
            continue
        for property_index, raw_property in enumerate(properties):
            if not isinstance(raw_property, Mapping):
                continue
            property_name = str(raw_property.get("name") or "")
            visit_property(
                raw_property,
                element_index=element_index,
                property_index=property_index,
                property_name=property_name,
                value_path=[element_index, property_name],
                nested=False,
            )
    return fields, coverage


def project_default_value(
    type_name: str,
    value: object,
    extra_json: object,
    *,
    resolved_object_limit: int = 24,
    value_loaded: bool = True,
) -> dict[str, object]:
    """Return a bounded truth-status projection for one decoded CDO property.

    The stored value remains the canonical payload.  This projection only says
    whether that payload is safe to treat as decoded data.  In particular, an
    empty list with ``array_parse.parsed=false`` is *not* a confirmed empty
    Unreal array.
    """

    extra = _extra_mapping(extra_json)
    if extra.get("error"):
        return {
            "valueStatus": "NOT_RECOVERED",
            "valueUsable": False,
            "parse": {"kind": "error", "parsed": False},
        }
    container = _CONTAINER_TYPES.get(str(type_name))
    if container is not None:
        kind, parse_key = container
        raw_parse = extra.get(parse_key)
        parse = raw_parse if isinstance(raw_parse, Mapping) else None

        # Older map/struct readers put the parse flag directly in the value.
        if parse is None and isinstance(value, Mapping) and "parsed" in value:
            parse = value

        parse_projection = _parse_projection(kind, parse)
        parsed = bool(parse_projection["parsed"])
        result: dict[str, object] = {
            "valueStatus": "CONFIRMED" if parsed else "NOT_RECOVERED",
            "valueUsable": parsed,
            "parse": parse_projection,
        }
        names = _resolved_names(extra)
        if names:
            limit = max(0, int(resolved_object_limit))
            result["resolvedObjectNames"] = names[:limit]
            result["resolvedObjectCoverage"] = {
                "available": len(names),
                "returned": min(len(names), limit),
            }
        object_fields, object_field_coverage = _resolved_object_fields(extra)
        if object_fields or object_field_coverage["available"]:
            limit = max(0, int(resolved_object_limit))
            result["resolvedObjectFields"] = object_fields[:limit]
            coverage_projection = {
                "available": object_field_coverage["available"],
                "returned": min(len(object_fields), limit),
            }
            if object_field_coverage["unresolved"]:
                coverage_projection["unresolved"] = object_field_coverage["unresolved"]
            if object_field_coverage["unparsed"]:
                coverage_projection["unparsedContainers"] = object_field_coverage["unparsed"]
            result["resolvedObjectFieldCoverage"] = coverage_projection
        return result

    if type_name in {"ObjectProperty", "SoftObjectProperty", "ClassProperty", "SoftClassProperty"}:
        resolved_name = str(extra.get("object") or "").strip()
        package_index = extra.get("package_index")
        null_reference = package_index == 0 or (value_loaded and (value is None or value == 0 or value == ""))
        usable = bool(resolved_name) or null_reference
        result = {
            "valueStatus": "CONFIRMED" if usable else "NOT_RECOVERED",
            "valueUsable": usable,
        }
        if resolved_name:
            result["resolvedObjectName"] = resolved_name
        return result

    if value_loaded and isinstance(value, Mapping) and value.get("parsed") is False:
        return {
            "valueStatus": "NOT_RECOVERED",
            "valueUsable": False,
            "parse": {"kind": "unsupported", "parsed": False},
        }
    return {"valueStatus": "CONFIRMED", "valueUsable": True}


def default_parse_gap(
    default_ref: str,
    name: str,
    type_name: str,
    projection: Mapping[str, object],
) -> dict[str, object] | None:
    """Describe a default-value decode gap without pretending it is data."""

    if projection.get("valueStatus") == "CONFIRMED":
        return None
    parse = projection.get("parse")
    parse_kind = str(parse.get("kind") or "value") if isinstance(parse, Mapping) else "value"
    reason_code = f"{parse_kind}_property_not_decoded"
    return {
        "ref": f"{default_ref}/gap/value",
        "kind": "diagnostic",
        "scopeKind": "default",
        "scopeRef": default_ref,
        "name": name,
        "status": "NOT_RECOVERED",
        "reasonCode": reason_code,
        "severity": "warning",
        "title": f"{name} was not decoded",
        "detail": (
            f"{name} is stored as {type_name}, but its serialized value was not fully recovered; "
            "the displayed placeholder must not be interpreted as a confirmed value."
        ),
        "nextProbe": "Regenerate the indexed capture with a parser that supports this property layout.",
    }


def default_value_is_usable(row: object) -> bool:
    if not isinstance(row, Mapping):
        return True
    if "valueUsable" in row:
        return row.get("valueUsable") is not False
    if "value_usable" in row:
        return row.get("value_usable") is not False
    if row.get("error"):
        return False
    type_name = str(row.get("type") or row.get("typeName") or "")
    parse_keys = {
        "ArrayProperty": "array_parse",
        "SetProperty": "set_parse",
        "StructProperty": "struct_parse",
        "MapProperty": "map_parse",
    }
    parse = row.get("parse")
    if not isinstance(parse, Mapping) and type_name in parse_keys:
        parse = row.get(parse_keys[type_name])
    if isinstance(parse, Mapping) and "parsed" in parse:
        return parse.get("parsed") is True
    value = row.get("value")
    if isinstance(value, Mapping) and value.get("parsed") is False:
        return False
    return True


def default_value_is_comparable(row: object) -> bool:
    if not default_value_is_usable(row) or not isinstance(row, Mapping):
        return default_value_is_usable(row)
    for camel_name, snake_name in (
        ("resolvedObjectCoverage", "resolved_object_coverage"),
        ("resolvedObjectFieldCoverage", "resolved_object_field_coverage"),
    ):
        coverage = row.get(camel_name, row.get(snake_name))
        if not isinstance(coverage, Mapping):
            continue
        try:
            available = int(coverage.get("available") or 0)
            returned = int(coverage.get("returned") or 0)
            unresolved = int(coverage.get("unresolved") or 0)
            unparsed = int(
                coverage.get("unparsedContainers")
                or coverage.get("unparsed_containers")
                or 0
            )
        except (TypeError, ValueError):
            return False
        if returned < available or unresolved or unparsed:
            return False
    return True


def downstream_default_metadata(row: Mapping[str, object]) -> dict[str, object]:
    """Translate repository camelCase metadata to capture-compatible snake_case."""

    usable = default_value_is_usable(row)
    status = str(
        row.get("valueStatus")
        or row.get("value_status")
        or ("CONFIRMED" if usable else "NOT_RECOVERED")
    )
    result: dict[str, object] = {
        "value_status": status,
        "value_usable": usable,
    }
    aliases = {
        "parse": ("parse",),
        "resolved_object_name": ("resolvedObjectName", "resolved_object_name"),
        "resolved_object_names": ("resolvedObjectNames", "resolved_object_names"),
        "resolved_object_coverage": ("resolvedObjectCoverage", "resolved_object_coverage"),
        "resolved_object_fields": ("resolvedObjectFields", "resolved_object_fields"),
        "resolved_object_field_coverage": (
            "resolvedObjectFieldCoverage",
            "resolved_object_field_coverage",
        ),
    }
    for output_name, candidates in aliases.items():
        for candidate in candidates:
            if candidate in row:
                result[output_name] = row[candidate]
                break
    return result


def canonical_default_value(row: object) -> object:
    """Return a revision-stable value for comparisons when the value is usable."""

    if not isinstance(row, Mapping):
        return row
    if not default_value_is_usable(row):
        return None
    metadata = downstream_default_metadata(row)
    resolved_name = metadata.get("resolved_object_name")
    if resolved_name not in (None, ""):
        return resolved_name
    resolved_names = metadata.get("resolved_object_names")
    if isinstance(resolved_names, list):
        return deepcopy(resolved_names)
    value = deepcopy(row.get("value"))
    resolved_fields = metadata.get("resolved_object_fields")
    if isinstance(value, list) and isinstance(resolved_fields, list):
        def replace_at_path(path: object, replacement: object) -> bool:
            if not isinstance(path, list) or not path:
                return False
            current: object = value
            for segment in path[:-1]:
                if isinstance(current, list):
                    try:
                        index = int(segment)
                    except (TypeError, ValueError):
                        return False
                    if not 0 <= index < len(current):
                        return False
                    current = current[index]
                elif isinstance(current, dict):
                    key = str(segment)
                    if key not in current:
                        return False
                    current = current[key]
                else:
                    return False
            final = path[-1]
            if isinstance(current, list):
                try:
                    index = int(final)
                except (TypeError, ValueError):
                    return False
                if not 0 <= index < len(current):
                    return False
                current[index] = replacement
                return True
            if isinstance(current, dict):
                key = str(final)
                if key not in current:
                    return False
                current[key] = replacement
                return True
            return False

        for field in resolved_fields:
            if not isinstance(field, Mapping):
                continue
            name = field.get("name")
            if name in (None, ""):
                continue
            if replace_at_path(field.get("valuePath", field.get("value_path")), name):
                continue
            try:
                element_index = int(field.get("elementIndex"))
            except (TypeError, ValueError):
                continue
            property_name = str(field.get("propertyName") or "")
            if (
                0 <= element_index < len(value)
                and isinstance(value[element_index], dict)
                and property_name
                and name not in (None, "")
            ):
                value[element_index][property_name] = name
    return value
