"""One-shot read-only reflection of one native Unreal ``/Script`` class."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from arkdev_scripting_probe.contracts import (  # noqa: E402
    assert_path_free,
    attach_semantic_digest,
    canonical_json,
)
from arkdev_scripting_probe.native_class import (  # noqa: E402
    NATIVE_CLASS_REQUEST_ENV,
    NATIVE_CLASS_REQUEST_SCHEMA,
    NATIVE_CLASS_RESULT_ENV,
    NATIVE_CLASS_RESULT_SCHEMA,
    normalize_native_class_path,
)


DEFAULT_PROPERTY_NAMES = ("is_crouched", "bIsCrouched")
DEFAULT_FUNCTION_NAMES = (
    "crouch",
    "un_crouch",
    "on_start_crouch",
    "on_end_crouch",
)
MAX_REQUEST_BYTES = 64 * 1024


def _member(owner: object, name: str) -> object | None:
    try:
        return getattr(owner, name, None)
    except Exception:
        return None


def _snake_case(name: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).casefold()


def _property_aliases(name: str) -> list[str]:
    aliases: list[str] = []
    for candidate in (name, _snake_case(name)):
        if candidate and candidate not in aliases:
            aliases.append(candidate)
    snake = _snake_case(name)
    if snake.startswith("b_is_"):
        without_bool_prefix = snake[2:]
        if without_bool_prefix not in aliases:
            aliases.append(without_bool_prefix)
    return aliases


def _class_mro(default_object: object) -> list[type[object]]:
    try:
        values = type(default_object).__mro__
    except Exception:
        return []
    return [value for value in values[:16] if value is not object]


def _owner_and_descriptor(
    mro: Sequence[type[object]],
    aliases: Sequence[str],
) -> tuple[str, str, str]:
    for owner in mro:
        try:
            namespace = vars(owner)
        except Exception:
            continue
        for alias in aliases:
            if alias not in namespace:
                continue
            descriptor = namespace[alias]
            doc = str(getattr(descriptor, "__doc__", "") or "")
            doc = " ".join(doc.split())[:500]
            return owner.__name__[:128], alias[:128], doc
    return "", "", ""


def _safe_scalar(value: object) -> tuple[object, str]:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str):
            return value[:500], type(value).__name__
        return value, type(value).__name__
    return f"<{type(value).__name__}>", type(value).__name__


def _read_property(
    default_object: object,
    mro: Sequence[type[object]],
    requested_name: str,
) -> dict[str, object]:
    aliases = _property_aliases(requested_name)
    owner, python_attribute, descriptor = _owner_and_descriptor(mro, aliases)
    getter = _member(default_object, "get_editor_property")
    if callable(getter):
        for candidate in aliases:
            try:
                raw_value = getter(candidate)
            except Exception:
                continue
            value, value_type = _safe_scalar(raw_value)
            return {
                "requestedName": requested_name,
                "readName": candidate,
                "pythonAttribute": python_attribute,
                "ownerClass": owner,
                "descriptor": descriptor,
                "readable": True,
                "value": value,
                "valueType": value_type,
                "scope": "CLASS_DEFAULT_OBJECT",
            }
    return {
        "requestedName": requested_name,
        "readName": "",
        "pythonAttribute": python_attribute,
        "ownerClass": owner,
        "descriptor": descriptor,
        "readable": False,
        "value": None,
        "valueType": "",
        "scope": "CLASS_DEFAULT_OBJECT",
    }


def _read_function(
    default_object: object,
    mro: Sequence[type[object]],
    name: str,
) -> dict[str, object]:
    owner, python_attribute, _descriptor = _owner_and_descriptor(mro, [name])
    available = callable(_member(default_object, name))
    return {
        "name": name,
        "pythonAttribute": python_attribute,
        "ownerClass": owner,
        "available": available,
        "called": False,
    }


def _engine_version(unreal_module: object) -> str:
    system_library = _member(unreal_module, "SystemLibrary")
    getter = _member(system_library, "get_engine_version")
    if callable(getter):
        try:
            return str(getter())[:128]
        except Exception:
            return ""
    return ""


def collect_native_class_result(
    unreal_module: object,
    class_path: str,
    *,
    generated_at: str,
    property_names: Sequence[str] = DEFAULT_PROPERTY_NAMES,
    function_names: Sequence[str] = DEFAULT_FUNCTION_NAMES,
) -> dict[str, object]:
    """Read class metadata and CDO values without calling any mutation API."""

    normalized = normalize_native_class_path(class_path)
    if not normalized:
        raise ValueError("invalid native class path")
    load_class = _member(unreal_module, "load_class")
    get_default_object = _member(unreal_module, "get_default_object")
    class_handle = load_class(None, normalized) if callable(load_class) else None
    default_object = None
    if class_handle is not None and callable(get_default_object):
        try:
            default_object = get_default_object(class_handle)
        except Exception:
            default_object = None
    mro = _class_mro(default_object) if default_object is not None else []
    properties = (
        [
            _read_property(default_object, mro, str(name)[:128])
            for name in list(property_names)[:32]
        ]
        if default_object is not None
        else []
    )
    functions = (
        [
            _read_function(default_object, mro, str(name)[:128])
            for name in list(function_names)[:32]
        ]
        if default_object is not None
        else []
    )
    result: dict[str, object] = {
        "schema": NATIVE_CLASS_RESULT_SCHEMA,
        "sourceKind": "native_class_reflection",
        "source": "official_unreal_python_reflection",
        "assetPath": normalized,
        "className": mro[0].__name__[:128] if mro else "",
        "classLoaded": class_handle is not None,
        "classDefaultObjectRead": default_object is not None,
        "inheritance": [owner.__name__[:128] for owner in mro],
        "engineVersion": _engine_version(unreal_module),
        "generatedAt": generated_at,
        "readOnly": True,
        "properties": properties,
        "functions": functions,
        "runtimeStateAvailable": False,
        "runtimeStateReason": "CLASS_DEFAULT_OBJECT_ONLY",
        "runtimeStateExplanation": (
            "Class default object only; not a live player instance. "
            "Runtime monitoring requires a player instance in game or PIE."
        ),
    }
    attach_semantic_digest(result)
    assert_path_free(result)
    return result


def _load_request(path: Path) -> Mapping[str, object]:
    if not path.is_file() or path.stat().st_size > MAX_REQUEST_BYTES:
        raise ValueError("native class request is missing or oversized")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, Mapping):
        raise ValueError("native class request must be an object")
    if value.get("schema") != NATIVE_CLASS_REQUEST_SCHEMA:
        raise ValueError("native class request schema is invalid")
    return value


def _write_result(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(canonical_json(result) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    request_text = os.environ.get(NATIVE_CLASS_REQUEST_ENV, "")
    result_text = os.environ.get(NATIVE_CLASS_RESULT_ENV, "")
    if not request_text or not result_text:
        return 2
    request = _load_request(Path(request_text))
    class_path = str(request.get("classPath") or "")
    import unreal  # type: ignore[import-not-found]

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result = collect_native_class_result(
        unreal,
        class_path,
        generated_at=generated_at,
    )
    _write_result(Path(result_text), result)
    print("ARKDEV_NATIVE_CLASS_PROBE=COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print("ARKDEV_NATIVE_CLASS_PROBE=ERROR")
        raise SystemExit(1) from None


__all__ = ["collect_native_class_result", "main"]
