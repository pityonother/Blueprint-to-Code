"""Shared path-free policy for public Blueprint and MCP payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from os import PathLike


_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]")
_UNC_PATH = re.compile(r"(?<![A-Za-z0-9_])\\\\[^\\\s]+[\\/]")
_FILE_URI = re.compile(r"(?i)(?<![A-Za-z0-9_])file:(?://)?/")
_POSIX_LOCAL_PATH = re.compile(r"(?<![:/<A-Za-z0-9_])/[^\s]+")
_UNREAL_OBJECT_PATH = re.compile(
    r"^/(?P<mount>[A-Za-z][A-Za-z0-9_]*)/"
    r"(?P<package>[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*)\."
    r"(?P<object>[A-Za-z0-9_]+)$"
)
_PUBLIC_UNREAL_MOUNTS = frozenset(
    {"dinodefense", "engine", "game", "pcg", "plugin", "plugins", "script"}
)


def is_unreal_object_path_field(field_name: str) -> bool:
    """Return whether a public field is explicitly typed as an Object Path."""

    return field_name.casefold().endswith(("objectpath", "objectpaths"))


def is_public_unreal_object_path(value: str, *, field_name: str) -> bool:
    """Validate a canonical Object Path under an explicitly trusted mount."""

    if not is_unreal_object_path_field(field_name):
        return False
    match = _UNREAL_OBJECT_PATH.fullmatch(value)
    if match is None or match.group("mount").casefold() not in _PUBLIC_UNREAL_MOUNTS:
        return False
    if match.group("mount").casefold() == "script":
        return "/" not in match.group("package")
    package_leaf = match.group("package").rsplit("/", 1)[-1]
    object_name = match.group("object")
    return object_name in {package_leaf, f"{package_leaf}_C"}


def public_value_is_path_free(value: object, *, field_name: str = "") -> bool:
    """Recursively reject machine paths and untyped Unreal-looking paths."""

    if isinstance(value, PathLike):
        return False
    if isinstance(value, str):
        if (
            value
            and is_unreal_object_path_field(field_name)
            and not is_public_unreal_object_path(value, field_name=field_name)
        ):
            return False
        if (
            _WINDOWS_ABSOLUTE.search(value)
            or _UNC_PATH.search(value)
            or _FILE_URI.search(value)
            or (
                _POSIX_LOCAL_PATH.search(value)
                and not is_public_unreal_object_path(value, field_name=field_name)
            )
        ):
            return False
        return True
    if isinstance(value, Mapping):
        return all(
            public_value_is_path_free(str(key))
            and public_value_is_path_free(item, field_name=str(key))
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return all(
            public_value_is_path_free(item, field_name=field_name) for item in value
        )
    return True


__all__ = [
    "is_public_unreal_object_path",
    "is_unreal_object_path_field",
    "public_value_is_path_free",
]
