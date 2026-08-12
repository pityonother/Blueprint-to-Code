"""Small, read-only helpers for ARK DevKit Wildcard reflection.

The helpers deliberately expose only the three stable ``wc_get_*`` reads used
by repository exporters and probes.  They never enumerate Unreal objects and
never call a mutation API.
"""

from __future__ import annotations

import re
from collections.abc import Mapping


_PROPERTY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,255}$")
MAX_PROPERTY_NAMES = 1200
MAX_PROPERTY_VALUES = 1200


def _member(owner: object, name: str) -> object | None:
    try:
        return getattr(owner, name, None)
    except Exception:
        return None


def _valid_property_name(value: object) -> bool:
    return _PROPERTY_NAME.fullmatch(str(value)) is not None


def wc_get_property_value_detailed(
    owner: object,
    name: object,
    *,
    unreal_module: object | None = None,
) -> tuple[bool, object | None, str]:
    """Read one Wildcard property and report the successful call shape."""

    method = _member(owner, "wc_get_property_value")
    if not callable(method):
        return False, None, ""

    text_name = str(name)
    candidates: list[tuple[object, str]] = [(text_name, "string")]
    name_type = _member(unreal_module, "Name") if unreal_module is not None else None
    if callable(name_type):
        try:
            candidates.append((name_type(text_name), "Name"))
        except Exception:
            pass

    for candidate, shape in candidates:
        try:
            return True, method(candidate), f"wc_get_property_value:{shape}"
        except Exception:
            continue
    return False, None, ""


def wc_get_property_value(
    owner: object,
    name: object,
    *,
    unreal_module: object | None = None,
) -> tuple[bool, object | None]:
    """Compatibility shape used by existing ARK exporters: ``(ok, value)``."""

    ok, value, _method = wc_get_property_value_detailed(
        owner,
        name,
        unreal_module=unreal_module,
    )
    return ok, value


def wc_get_all_property_names_detailed(
    owner: object,
    *,
    limit: int = MAX_PROPERTY_NAMES,
) -> tuple[list[str], bool]:
    """Return deterministic bounded names plus an explicit truncation flag."""

    method = _member(owner, "wc_get_all_property_names")
    if not callable(method):
        return [], False
    safe_limit = max(0, min(int(limit), MAX_PROPERTY_NAMES))
    if safe_limit == 0:
        return [], False
    try:
        raw = method() or []
    except Exception:
        return [], False

    names: set[str] = set()
    try:
        iterator = iter(raw)
    except TypeError:
        return [], False
    for item in iterator:
        text = str(item)
        if _valid_property_name(text):
            names.add(text)
    ordered = sorted(names)
    return ordered[:safe_limit], len(ordered) > safe_limit


def wc_get_all_property_names(
    owner: object,
    *,
    limit: int = MAX_PROPERTY_NAMES,
) -> list[str]:
    """Return a deterministic bounded list of safe property names."""

    names, _truncated = wc_get_all_property_names_detailed(owner, limit=limit)
    return names


def wc_get_all_property_values_detailed(
    owner: object,
    *,
    limit: int = MAX_PROPERTY_VALUES,
) -> tuple[dict[str, object], bool]:
    """Return deterministic bounded values plus an explicit truncation flag."""

    method = _member(owner, "wc_get_all_property_values")
    if not callable(method):
        return {}, False
    safe_limit = max(0, min(int(limit), MAX_PROPERTY_VALUES))
    if safe_limit == 0:
        return {}, False
    try:
        raw = method() or {}
    except Exception:
        return {}, False

    if isinstance(raw, Mapping):
        pairs = raw.items()
    else:
        try:
            pairs = (
                (item[0], item[1])
                for item in raw
                if isinstance(item, (list, tuple)) and len(item) >= 2
            )
        except TypeError:
            return {}, False

    values: dict[str, object] = {}
    for key, value in pairs:
        text = str(key)
        if _valid_property_name(text):
            values[text] = value
    selected = sorted(values)[:safe_limit]
    return {key: values[key] for key in selected}, len(values) > safe_limit


def wc_get_all_property_values(
    owner: object,
    *,
    limit: int = MAX_PROPERTY_VALUES,
) -> dict[str, object]:
    """Return deterministic bounded Wildcard values without stringifying them."""

    values, _truncated = wc_get_all_property_values_detailed(owner, limit=limit)
    return values


__all__ = [
    "MAX_PROPERTY_NAMES",
    "MAX_PROPERTY_VALUES",
    "wc_get_all_property_names",
    "wc_get_all_property_names_detailed",
    "wc_get_all_property_values",
    "wc_get_all_property_values_detailed",
    "wc_get_property_value",
    "wc_get_property_value_detailed",
]
