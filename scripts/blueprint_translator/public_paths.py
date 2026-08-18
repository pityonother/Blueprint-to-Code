"""Shared path-free policy for public Blueprint and MCP payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from os import PathLike
from urllib.parse import unquote


_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]")
_WINDOWS_DRIVE_RELATIVE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[A-Z]:(?![\\/])[^\\/\s\"']+[\\/]"
)
_UNC_PATH = re.compile(r"(?<![A-Za-z0-9_])\\\\[^\\\s]+[\\/]")
_WINDOWS_ROOTED = re.compile(
    r"(?<![A-Za-z0-9_\\])\\(?!\\)[^\\\s\"']+(?:\\[^\\\s\"']+)+"
)
_FILE_URI = re.compile(r"(?i)(?<![A-Za-z0-9_])file:")
_SINGLE_SLASH_URI_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[A-Z][A-Z0-9+.-]*:/(?!/)[^\s]+"
)
_POSIX_LOCAL_PATH = re.compile(r"(?<![:/<A-Za-z0-9_])/[^\s]+")
_UNREAL_OBJECT_PATH = re.compile(
    r"^/(?P<mount>[A-Za-z][A-Za-z0-9_]*)/"
    r"(?P<package>[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*)\."
    r"(?P<object>[A-Za-z0-9_]+)$"
)
_PUBLIC_UNREAL_MOUNTS = frozenset(
    {
        "asbexportgun",
        "dinodefense",
        "engine",
        "game",
        "pcg",
        "plugin",
        "plugins",
        "script",
    }
)
_UNREAL_OBJECT_PATH_FIELDS = frozenset({"activeasset"})


def is_unreal_object_path_field(field_name: str) -> bool:
    """Return whether a public field is explicitly typed as an Object Path."""

    normalized = field_name.casefold()
    return normalized in _UNREAL_OBJECT_PATH_FIELDS or normalized.endswith(
        ("objectpath", "objectpaths")
    )


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
        candidate = value
        decode_count = 0
        while True:
            if (
                _WINDOWS_ABSOLUTE.search(candidate)
                or _WINDOWS_DRIVE_RELATIVE.search(candidate)
                or _UNC_PATH.search(candidate)
                or _WINDOWS_ROOTED.search(candidate)
                or _FILE_URI.search(candidate)
                or _SINGLE_SLASH_URI_PATH.search(candidate)
                or (
                    _POSIX_LOCAL_PATH.search(candidate)
                    and not is_public_unreal_object_path(
                        candidate, field_name=field_name
                    )
                )
            ):
                return False
            decoded = unquote(candidate)
            if decoded == candidate:
                break
            decode_count += 1
            if decode_count > 8:
                return False
            candidate = decoded
        return True
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, PathLike):
                return False
            field = str(key)
            if not public_value_is_path_free(field) or not public_value_is_path_free(
                item, field_name=field
            ):
                return False
        return True
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
