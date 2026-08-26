"""Stable, path-free contracts for the official scripting probe."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from os import PathLike


INSTALLATION_SCHEMA = "blueprint-to-code.arkdev-scripting-installation/v1"
PROBE_SCHEMA = "blueprint-to-code.arkdev-scripting-probe/v1"
PROBE_VERSION = "arkdev-official-scripting-probe/v2"
REQUEST_SCHEMA = "blueprint-to-code.arkdev-scripting-probe-request/v1"
SNAPSHOT_SCHEMA = "blueprint-to-code.arkdev-explicit-graph-snapshot/v1"
NODE_BINDING_REQUEST_SCHEMA = "blueprint-to-code.arkdev-node-binding-request/v1"
NODE_BINDING_RESULT_SCHEMA = "blueprint-to-code.arkdev-node-binding-result/v1"
STATUS_VALUES = frozenset(
    {
        "AVAILABLE",
        "MISSING",
        "ERROR",
        "NOT_TESTED",
        "PRESENT_BUT_NOT_USED",
    }
)
VALIDATOR_VALUES = frozenset({"PASS", "UNAVAILABLE", "NOT_TESTED", "ERROR"})
MAX_GRAPH_NODES = 200
MAX_OBJECTS_SCANNED = 50_000
MAX_NODE_BINDING_NODES = 12
MAX_NODE_BINDING_PINS_PER_NODE = 64
MAX_NODE_BINDING_PINS_TOTAL = 512

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?<![A-Za-z0-9_])[a-z]:[\\/]")
_FILE_URI = re.compile(r"(?i)(?<![A-Za-z0-9_])file://")
_UNC_PATH = re.compile(r"(?<![A-Za-z0-9_:])(?:\\\\|//)[^\\/\s]+[\\/]")
_POSIX_MACHINE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_:/])/(?:home|users|root|tmp|var/tmp)/[^\s\"']+"
)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _semantic_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _semantic_value(item)
            for key, item in value.items()
            if str(key) not in {"generatedAt", "semanticDigest"}
        }
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_semantic_value(item) for item in value]
    return value


def semantic_digest(value: object) -> str:
    semantic = canonical_json(_semantic_value(value)).encode("utf-8")
    return hashlib.sha256(semantic).hexdigest()


def attach_semantic_digest(value: dict[str, object]) -> dict[str, object]:
    value["semanticDigest"] = semantic_digest(value)
    return value


def assert_path_free(value: object) -> None:
    """Reject machine-local paths while allowing Unreal virtual object paths."""

    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, PathLike):
            raise ValueError("machine-local path object is not allowed")
        if isinstance(current, str):
            if (
                _WINDOWS_ABSOLUTE_PATH.search(current)
                or _FILE_URI.search(current)
                or _UNC_PATH.search(current)
                or _POSIX_MACHINE_PATH.search(current)
            ):
                raise ValueError("machine-local path text is not allowed")
            continue
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (bytes, bytearray)
        ):
            pending.extend(current)


def require_status(value: object) -> str:
    if not isinstance(value, str) or value not in STATUS_VALUES:
        raise ValueError("invalid capability status")
    return value


def require_digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("invalid semantic digest")
    return value


def status_to_validator(value: object) -> str:
    status = require_status(value)
    if status == "AVAILABLE":
        return "PASS"
    if status == "MISSING":
        return "UNAVAILABLE"
    if status == "ERROR":
        return "ERROR"
    return "NOT_TESTED"


__all__ = [
    "INSTALLATION_SCHEMA",
    "MAX_GRAPH_NODES",
    "MAX_OBJECTS_SCANNED",
    "MAX_NODE_BINDING_NODES",
    "MAX_NODE_BINDING_PINS_PER_NODE",
    "MAX_NODE_BINDING_PINS_TOTAL",
    "NODE_BINDING_REQUEST_SCHEMA",
    "NODE_BINDING_RESULT_SCHEMA",
    "PROBE_SCHEMA",
    "PROBE_VERSION",
    "REQUEST_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "STATUS_VALUES",
    "VALIDATOR_VALUES",
    "assert_path_free",
    "attach_semantic_digest",
    "canonical_json",
    "require_digest",
    "require_status",
    "semantic_digest",
    "status_to_validator",
]
