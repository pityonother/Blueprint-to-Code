"""Canonical JSON, semantic digests, and stable opaque Solver identifiers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence


_SEMANTIC_EXCLUDED_KEYS = frozenset(
    {
        "createdAt",
        "updatedAt",
        "lastVerifiedAt",
        "solverId",
        "semanticDigest",
    }
)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _semantic_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _semantic_value(item)
            for key, item in value.items()
            if str(key) not in _SEMANTIC_EXCLUDED_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_semantic_value(item) for item in value]
    return value


def semantic_digest(value: object) -> str:
    return canonical_sha256(_semantic_value(value))


def stable_id(prefix: str, *parts: object, length: int = 24) -> str:
    """Return a deterministic opaque handle for a semantic identity."""

    if not prefix or not prefix.islower() or not prefix.replace("-", "").isalnum():
        raise ValueError("stable ID prefix must be a lowercase token")
    if not 16 <= length <= 64:
        raise ValueError("stable ID length must be between 16 and 64")
    digest = canonical_sha256(list(parts))
    return f"{prefix}://{digest[:length]}"


__all__ = [
    "canonical_json",
    "canonical_sha256",
    "semantic_digest",
    "stable_id",
]
