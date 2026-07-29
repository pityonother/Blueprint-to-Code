"""Bounded, read-only ingestion of typed Blueprint class defaults."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from ..evidence_schema import (
    EVIDENCE_SCHEMA_VERSION,
    LEGACY_CAPTURE_PARSER_VERSION,
    make_asset_id,
    make_default_ref,
    make_revision_id,
)
from ..evidence_writer import DIRECT_PAYLOAD_PARSER_VERSION
from .fact_store import FactValue, store_fact
from .ontology import OntologyBundle
from .source_manifest import SourceRevision


MAX_INLINE_CONTAINER_ITEMS = 64
MAX_INLINE_JSON_BYTES = 4096
MAX_DECODED_VALUE_BYTES = 8 * 1024 * 1024
MAX_EXPLICIT_BLUEPRINT_SOURCES = 32
SQLITE_INTEGER_MIN = -(2**63)
SQLITE_INTEGER_MAX = 2**63 - 1

_OBJECT_TYPES = {
    "ObjectProperty",
    "SoftObjectProperty",
    "ClassProperty",
    "SoftClassProperty",
}
_TEXT_TYPES = {
    "StrProperty",
    "StringProperty",
    "NameProperty",
    "TextProperty",
}
_INTEGER_TYPES = {
    "ByteProperty",
    "EnumProperty",
    "Int8Property",
    "Int16Property",
    "IntProperty",
    "Int32Property",
    "Int64Property",
    "UInt8Property",
    "UInt16Property",
    "UInt32Property",
    "UInt64Property",
}
_NUMBER_TYPES = {
    "DoubleProperty",
    "FloatProperty",
}
_SAFE_CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")
_WINDOWS_PATH = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:[a-z]:[\\/]|\\\\)")
_POSIX_LOCAL_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])/"
    r"(?:Users|home|root|private|tmp|var(?:/tmp)?|etc|usr|opt|"
    r"bin|sbin|proc|sys|dev|srv|run|data|workspace|Volumes|"
    r"Applications|Library|System|mnt/[a-z])"
    r"(?:/|$)"
)
_UNREAL_BASE_URI = re.compile(r"^/(?:Game|Script|Engine|Mods)(?:/[A-Za-z0-9_.-]+)+$")
_PORTABLE_NUMERIC_STRUCT_FIELDS = {
    "Color": frozenset({"x", "y", "z"}),
    "Rotator": frozenset({"x", "y", "z"}),
    "Vector": frozenset({"x", "y", "z"}),
    "Vector2D": frozenset({"x", "y"}),
}
_PORTABLE_NUMERIC_STRUCT_SIZES = {
    "Color": frozenset({4}),
    "Rotator": frozenset({24}),
    "Vector": frozenset({12, 24}),
    "Vector2D": frozenset({16}),
}


class _RejectedAsset(ValueError):
    """The capture identity or revision cannot be trusted."""


class _PartialValue(ValueError):
    """The parser explicitly reported a nested incomplete value."""


class _UnsupportedValue(ValueError):
    """The value cannot be represented safely by this bounded importer."""


class _FreshnessGap(ValueError):
    """The package currently on disk cannot be tied to this Evidence revision."""


@dataclass(frozen=True)
class BlueprintIngestResult:
    counts: dict[str, int]
    covered_properties: frozenset[tuple[str, str]]
    freshness_gap_assets: frozenset[str]
    untrusted_assets: frozenset[str]
    fact_ids: frozenset[int] = frozenset()
    entity_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class _AssetCandidate:
    object_path: str
    asset_name: str
    evidence_revision: str
    source_fingerprint: str
    file_size_total: int
    source_modified: str
    has_uasset: int
    has_uexp: int
    has_ubulk: int
    capture_asset_name: str = ""


@dataclass(frozen=True)
class _ExplicitBlueprintSource:
    source_uri: str
    entity_uri: str
    revision_label: str
    fingerprint: str
    capture_asset_name: str


@dataclass(frozen=True)
class _RevisionIdentity:
    revision_id: str
    asset_id: str
    asset_name: str
    object_path: str
    source_fingerprint: str
    parser_version: str
    schema_version: str
    generated_at: str
    uasset_path: str


@dataclass(frozen=True)
class _SourceManifestEntry:
    path: str
    sha256: str
    size_bytes: int
    source_kind: str


@dataclass(frozen=True)
class _MaterializedValue:
    value: FactValue
    status: str
    evidence_role: str
    partial: bool = False
    summary: bool = False


def _strict_nonnegative_integer(
    value: object,
    *,
    label: str,
    error_type: type[ValueError] = _UnsupportedValue,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise error_type(f"{label} must be a nonnegative integer")
    return value


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _is_canonical_unreal_uri(value: str) -> bool:
    if value != value.strip() or any(
        marker in value for marker in ("\\", "?", "#", "%")
    ):
        return False
    base, separator, subobject = value.partition(":")
    if not _UNREAL_BASE_URI.fullmatch(base):
        return False
    if any(segment in {".", ".."} for segment in base.split("/")):
        return False
    if separator and (
        not subobject
        or subobject != subobject.strip()
        or subobject in {".", ".."}
        or any(marker in subobject for marker in ("/", "\\", "?", "#"))
    ):
        return False
    return True


def _contains_local_path(value: object) -> bool:
    if isinstance(value, str):
        decoded = value
        for _ in range(4):
            expanded = unquote(decoded)
            if expanded == decoded:
                break
            decoded = expanded
        if _is_canonical_unreal_uri(decoded):
            return False
        return bool(
            decoded.startswith("/")
            or _WINDOWS_PATH.search(decoded)
            or _POSIX_LOCAL_PATH.search(decoded)
            or "file://" in decoded.casefold()
        )
    if isinstance(value, Mapping):
        return any(
            _contains_local_path(key) or _contains_local_path(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return any(_contains_local_path(item) for item in value)
    return False


def _contains_unparsed(value: object) -> bool:
    if isinstance(value, Mapping):
        if value.get("parsed") is False:
            return True
        return any(_contains_unparsed(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return any(_contains_unparsed(item) for item in value)
    return False


def _contains_invalid_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return not SQLITE_INTEGER_MIN <= value <= SQLITE_INTEGER_MAX
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(
            _contains_invalid_number(key) or _contains_invalid_number(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return any(_contains_invalid_number(item) for item in value)
    return False


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_unreal_uri(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or text.casefold() in {"none", "none.none", "null"}:
        return None
    if _contains_local_path(text) or not _is_canonical_unreal_uri(text):
        raise _UnsupportedValue("object reference is not a canonical Unreal URI")
    return text


def _require_usable_metadata(metadata: Mapping[str, object]) -> None:
    status = str(metadata.get("value_status") or "").upper()
    if status and status not in {
        "CONFIRMED",
        "VERIFIED",
        "RESOLVED",
        "CONFIRMED_EMPTY",
    }:
        raise _UnsupportedValue("parser explicitly marked the value as unavailable")
    if "value_usable" in metadata:
        usable = metadata.get("value_usable")
        if not isinstance(usable, bool) or not usable:
            raise _UnsupportedValue("parser marked the value as unusable")
    if metadata.get("error"):
        raise _UnsupportedValue("parser reported a value decode error")


def _null_object_reference(value: object, extra: Mapping[str, object]) -> bool:
    package_index = extra.get("package_index")
    if package_index is not None:
        try:
            return int(package_index) == 0
        except (TypeError, ValueError):
            return False
    return (
        value is None
        or value == 0
        or value == ""
        or str(value).casefold() in {"none", "none.none", "null"}
    )


def _resolved_object(
    value: object,
    extra: Mapping[str, object],
) -> str | None:
    if _null_object_reference(value, extra):
        return None
    soft = extra.get("soft_object_path")
    soft_path = soft.get("object_path") if isinstance(soft, Mapping) else None
    for candidate in (
        extra.get("object_path"),
        soft_path,
        extra.get("object"),
        value if isinstance(value, str) else None,
    ):
        if candidate not in (None, ""):
            return _canonical_unreal_uri(candidate)
    raise _UnsupportedValue("non-null object reference is unresolved")


def _metadata_type(metadata: Mapping[str, object]) -> str:
    return str(
        metadata.get("type")
        or metadata.get("type_name")
        or metadata.get("typeName")
        or ""
    )


def _container_parse(
    type_name: str,
    value: object,
    extra: Mapping[str, object],
) -> Mapping[str, object]:
    parse_key = {
        "ArrayProperty": "array_parse",
        "StructProperty": "struct_parse",
        "MapProperty": "map_parse",
    }.get(type_name)
    if parse_key is None:
        raise _UnsupportedValue(f"unsupported container type: {type_name}")
    raw = extra.get(parse_key)
    if isinstance(raw, Mapping):
        parse = raw
    elif isinstance(value, Mapping) and "parsed" in value:
        parse = value
    else:
        raise _UnsupportedValue(f"{parse_key} metadata is missing")
    if parse.get("parsed") is False:
        raise _PartialValue(f"{parse_key}.parsed=false")
    if parse.get("parsed") is not True:
        raise _UnsupportedValue(f"{parse_key}.parsed is not confirmed")
    return parse


def _canonicalize_property(
    value: object,
    metadata: Mapping[str, object],
) -> object:
    _require_usable_metadata(metadata)
    type_name = _metadata_type(metadata)
    if not type_name:
        raise _UnsupportedValue("nested property type is missing")
    if type_name in _OBJECT_TYPES or (not type_name and "object" in metadata):
        resolved = _resolved_object(value, metadata)
        return resolved
    if type_name == "BoolProperty":
        if not isinstance(value, bool):
            raise _UnsupportedValue("nested BoolProperty is not a boolean")
        return value
    if type_name in _INTEGER_TYPES:
        if isinstance(value, bool):
            raise _UnsupportedValue("nested integer property is a boolean")
        if isinstance(value, int):
            if not SQLITE_INTEGER_MIN <= value <= SQLITE_INTEGER_MAX:
                raise _UnsupportedValue(
                    "nested integer exceeds the signed 64-bit range"
                )
            return value
        if type_name in {"ByteProperty", "EnumProperty"} and isinstance(
            value,
            str,
        ):
            return value
        raise _UnsupportedValue("nested integer property is not an integer")
    if type_name in _NUMBER_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _UnsupportedValue("nested number property is not numeric")
        if not math.isfinite(float(value)):
            raise _UnsupportedValue("nested number property is not finite")
        return value
    if type_name in _TEXT_TYPES:
        if not isinstance(value, str):
            raise _UnsupportedValue("nested text property is not a string")
        return value
    if type_name == "ArrayProperty":
        raw_parse = metadata.get("array_parse")
        if not isinstance(raw_parse, Mapping):
            raise _UnsupportedValue("nested array parse metadata is missing")
        return _canonicalize_array(value, raw_parse)
    if type_name == "StructProperty":
        raw_parse = metadata.get("struct_parse")
        if not isinstance(raw_parse, Mapping):
            raise _UnsupportedValue("nested struct parse metadata is missing")
        return _canonicalize_struct(value, raw_parse)
    if type_name == "MapProperty":
        raw_parse = metadata.get("map_parse")
        if not isinstance(raw_parse, Mapping):
            raise _UnsupportedValue("nested map parse metadata is missing")
        return _canonicalize_map(value, raw_parse)
    raise _UnsupportedValue(f"unsupported nested property type: {type_name}")


def _canonicalize_array(
    value: object,
    parse: Mapping[str, object],
) -> list[object]:
    if parse.get("parsed") is False:
        raise _PartialValue("nested array parsed=false")
    if parse.get("parsed") is not True or not isinstance(value, list):
        raise _UnsupportedValue("array value or parse metadata is incomplete")
    expected_count = _strict_nonnegative_integer(
        parse.get("count"),
        label="array count",
    )
    if expected_count != len(value):
        raise _UnsupportedValue("array count does not match decoded elements")
    raw_elements = parse.get("elements")
    elements = (
        [item for item in raw_elements if isinstance(item, Mapping)]
        if isinstance(raw_elements, list)
        else []
    )
    by_index: dict[int, Mapping[str, object]] = {}
    for ordinal, item in enumerate(elements):
        raw_index = item.get("index", ordinal)
        index = _strict_nonnegative_integer(
            raw_index,
            label="array element index",
        )
        if index in by_index or not 0 <= index < len(value):
            raise _UnsupportedValue(
                "array element metadata is duplicate or out of range"
            )
        by_index[index] = item
    element_kind = str(parse.get("element_kind") or "")
    if not element_kind or element_kind == "FPackageIndex":
        raise _UnsupportedValue("array element kind is not portable")
    result: list[object] = []
    for index, item in enumerate(value):
        metadata = by_index.get(index, {})
        _require_usable_metadata(metadata)
        if element_kind in _OBJECT_TYPES:
            resolved = _resolved_object(item, metadata)
            result.append(resolved)
        elif element_kind == "StructProperty":
            result.append(_canonicalize_struct(item, metadata))
        elif element_kind == "ArrayProperty":
            nested = metadata.get("array_parse")
            if not isinstance(nested, Mapping):
                raise _UnsupportedValue(
                    "nested array element parse metadata is missing"
                )
            result.append(_canonicalize_array(item, nested))
        elif element_kind == "MapProperty":
            nested = metadata.get("map_parse")
            if not isinstance(nested, Mapping):
                raise _UnsupportedValue("nested map element parse metadata is missing")
            result.append(_canonicalize_map(item, nested))
        else:
            typed_metadata = {str(key): item for key, item in metadata.items()}
            typed_metadata.setdefault("type", element_kind)
            result.append(_canonicalize_property(item, typed_metadata))
    return result


def _canonicalize_struct(
    value: object,
    parse: Mapping[str, object],
) -> dict[str, object]:
    _require_usable_metadata(parse)
    if parse.get("parsed") is False:
        raise _PartialValue("nested struct parsed=false")
    if not isinstance(value, Mapping):
        raise _UnsupportedValue("struct value or parse metadata is incomplete")
    result = {str(key): item for key, item in value.items()}
    if len(result) != len(value):
        raise _UnsupportedValue("struct fields are not uniquely named")
    raw_properties = parse.get("properties")
    if isinstance(raw_properties, list):
        properties: dict[str, Mapping[str, object]] = {}
        for raw_property in raw_properties:
            if not isinstance(raw_property, Mapping):
                raise _UnsupportedValue("struct property metadata is invalid")
            name = str(raw_property.get("name") or "")
            if not name or name in properties:
                raise _UnsupportedValue(
                    "struct property metadata is missing or duplicate"
                )
            if "value" not in raw_property or _canonical_json(
                raw_property["value"]
            ) != _canonical_json(result.get(name)):
                raise _UnsupportedValue(
                    "struct property metadata value does not match the field"
                )
            properties[name] = raw_property
        if set(properties) != set(result):
            raise _UnsupportedValue(
                "struct property metadata does not cover every field"
            )
        return {
            name: _canonicalize_property(item, properties[name])
            for name, item in result.items()
        }

    if parse.get("parsed") is not True:
        raise _UnsupportedValue("struct parse metadata is not confirmed")
    struct_name = str(parse.get("struct_name") or "")
    expected_fields = _PORTABLE_NUMERIC_STRUCT_FIELDS.get(struct_name)
    expected_sizes = _PORTABLE_NUMERIC_STRUCT_SIZES.get(struct_name)
    raw_fields = parse.get("fields")
    if (
        expected_fields is None
        or expected_sizes is None
        or not isinstance(raw_fields, list)
    ):
        raise _UnsupportedValue("struct has no complete typed property metadata")
    fields = [str(field) for field in raw_fields]
    if (
        len(fields) != len(set(fields))
        or set(fields) != expected_fields
        or set(result) != expected_fields
    ):
        raise _UnsupportedValue(
            "portable struct fields do not match the declared shape"
        )
    raw_size = _strict_nonnegative_integer(
        parse.get("raw_size"),
        label="portable struct raw_size",
    )
    if raw_size not in expected_sizes:
        raise _UnsupportedValue(
            "portable struct raw_size does not match the declared shape"
        )
    if "declared_size" in parse:
        declared_size = _strict_nonnegative_integer(
            parse["declared_size"],
            label="portable struct declared_size",
        )
        if declared_size != raw_size:
            raise _UnsupportedValue(
                "portable struct declared_size does not match raw_size"
            )
    if "struct" in parse and str(parse["struct"] or "") != struct_name:
        raise _UnsupportedValue("portable struct wrapper conflicts with struct_name")
    for item in result.values():
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            raise _UnsupportedValue("portable struct field is not a finite number")
    return result


def _map_value_entries(value: object) -> list[tuple[object, object]]:
    if isinstance(value, Mapping):
        return list(value.items())
    if not isinstance(value, list):
        raise _UnsupportedValue("map value is neither an object nor entries")
    entries: list[tuple[object, object]] = []
    for raw in value:
        if isinstance(raw, Mapping) and "key" in raw and "value" in raw:
            entries.append((raw["key"], raw["value"]))
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            entries.append((raw[0], raw[1]))
        else:
            raise _UnsupportedValue("map entry shape is unsupported")
    return entries


def _map_entry_metadata(
    raw: Mapping[str, object],
    *,
    side: str,
    default_type: str,
) -> dict[str, object]:
    nested = raw.get(f"{side}_metadata")
    metadata = (
        {str(key): value for key, value in nested.items()}
        if isinstance(nested, Mapping)
        else {}
    )
    metadata.setdefault(
        "type",
        raw.get(f"{side}_type") or raw.get(f"{side}_kind") or default_type,
    )
    for suffix in (
        "object",
        "object_path",
        "package_index",
        "array_parse",
        "struct_parse",
        "map_parse",
    ):
        key = f"{side}_{suffix}"
        if key in raw and suffix not in metadata:
            metadata[suffix] = raw[key]
    return metadata


def _canonicalize_map(
    value: object,
    parse: Mapping[str, object],
) -> list[dict[str, object]]:
    if parse.get("parsed") is False:
        raise _PartialValue("nested map parsed=false")
    if parse.get("parsed") is not True:
        raise _UnsupportedValue("map parse metadata is incomplete")
    mapping_input = isinstance(value, Mapping)
    values = _map_value_entries(value)
    count = parse.get("count")
    if count is not None:
        expected_count = _strict_nonnegative_integer(
            count,
            label="map count",
        )
        if expected_count != len(values):
            raise _UnsupportedValue("map count does not match decoded entries")
    raw_metadata = parse.get("entries")
    metadata_entries = (
        [item for item in raw_metadata if isinstance(item, Mapping)]
        if isinstance(raw_metadata, list)
        else []
    )
    key_kind = str(parse.get("key_kind") or "")
    value_kind = str(parse.get("value_kind") or "")
    if not key_kind or not value_kind:
        raise _UnsupportedValue("map key or value kind is missing")
    metadata_by_index: dict[int, Mapping[str, object]] = {}
    metadata_by_key: dict[str, Mapping[str, object]] = {}
    for ordinal, metadata in enumerate(metadata_entries):
        metadata_index = _strict_nonnegative_integer(
            metadata.get("index", ordinal),
            label="map entry index",
        )
        if metadata_index in metadata_by_index or not 0 <= metadata_index < len(values):
            raise _UnsupportedValue("map entry metadata is duplicate or out of range")
        metadata_by_index[metadata_index] = metadata
        if "key" in metadata:
            raw_key_token = _canonical_json(metadata["key"])
            if raw_key_token in metadata_by_key:
                raise _UnsupportedValue("map metadata contains duplicate raw keys")
            metadata_by_key[raw_key_token] = metadata
    if metadata_entries and len(metadata_entries) != len(values):
        raise _UnsupportedValue("map entry metadata is incomplete")
    result: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for index, (raw_key, raw_value) in enumerate(values):
        raw_key_token = _canonical_json(raw_key)
        if mapping_input and metadata_entries:
            metadata = metadata_by_key.get(raw_key_token)
            if metadata is None:
                raise _UnsupportedValue("map metadata does not match a decoded key")
        else:
            metadata = metadata_by_index.get(index, {})
            if (
                metadata
                and "key" in metadata
                and _canonical_json(metadata["key"]) != raw_key_token
            ):
                raise _UnsupportedValue(
                    "map entry metadata key does not match its value"
                )
        key_metadata = _map_entry_metadata(
            metadata,
            side="key",
            default_type=key_kind,
        )
        value_metadata = _map_entry_metadata(
            metadata,
            side="value",
            default_type=value_kind,
        )
        key = _canonicalize_property(raw_key, key_metadata)
        item = _canonicalize_property(raw_value, value_metadata)
        canonical_key = _canonical_json(key)
        if canonical_key in seen_keys:
            raise _UnsupportedValue("map contains duplicate canonical keys")
        seen_keys.add(canonical_key)
        result.append({"key": key, "value": item})
    result.sort(
        key=lambda item: (
            _canonical_json(item["key"]),
            _canonical_json(item["value"]),
        )
    )
    return result


def _bounded_container(
    *,
    type_name: str,
    value: object,
    extra: Mapping[str, object],
    detail_uri: str,
) -> _MaterializedValue:
    parse = _container_parse(type_name, value, extra)
    if type_name == "ArrayProperty":
        canonical = _canonicalize_array(value, parse)
    elif type_name == "StructProperty":
        canonical = _canonicalize_struct(value, parse)
    else:
        canonical = _canonicalize_map(value, parse)
    if _contains_unparsed(canonical):
        raise _PartialValue("canonical value still contains parsed=false")
    if _contains_local_path(canonical) or _contains_invalid_number(canonical):
        raise _UnsupportedValue("container contains a local filesystem path")
    count = len(canonical)
    if count == 0:
        return _MaterializedValue(
            value=FactValue("CONFIRMED_EMPTY"),
            status="CONFIRMED_EMPTY",
            evidence_role="DEFAULT_VALUE_ACTUAL",
        )
    text = _canonical_json(canonical)
    if (
        count > MAX_INLINE_CONTAINER_ITEMS
        or len(text.encode("utf-8")) > MAX_INLINE_JSON_BYTES
    ):
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        summary = _canonical_json(
            {
                "count": count,
                "detail_uri": detail_uri,
                "fingerprint": fingerprint,
                "type": type_name,
            }
        )
        return _MaterializedValue(
            value=FactValue(
                "FINGERPRINT",
                value_text=fingerprint,
                value_json=summary,
            ),
            status="CONFIRMED_FINGERPRINT_ONLY",
            evidence_role="DEFAULT_VALUE_SUMMARY",
            summary=True,
        )
    return _MaterializedValue(
        value=FactValue("JSON", value_json=text),
        status="CONFIRMED",
        evidence_role="DEFAULT_VALUE_ACTUAL",
    )


def _materialized_value(
    *,
    type_name: str,
    value: object,
    extra: Mapping[str, object],
    detail_uri: str,
) -> _MaterializedValue:
    try:
        _require_usable_metadata(extra)
        if _contains_unparsed(value) or _contains_unparsed(extra):
            raise _PartialValue("nested parsed=false")
        if _contains_local_path(value) or _contains_invalid_number(value):
            raise _UnsupportedValue("value contains a local filesystem path")
        if type_name == "BoolProperty":
            if not isinstance(value, bool):
                raise _UnsupportedValue("BoolProperty is not a boolean")
            return _MaterializedValue(
                value=FactValue(
                    "BOOLEAN",
                    value_integer=1 if value else 0,
                ),
                status="CONFIRMED",
                evidence_role="DEFAULT_VALUE_ACTUAL",
            )
        if type_name in _INTEGER_TYPES:
            if isinstance(value, bool):
                raise _UnsupportedValue("integer property is a boolean")
            if isinstance(value, int):
                if not SQLITE_INTEGER_MIN <= value <= SQLITE_INTEGER_MAX:
                    raise _UnsupportedValue(
                        "integer exceeds SQLite signed 64-bit range"
                    )
                return _MaterializedValue(
                    value=FactValue("INTEGER", value_integer=value),
                    status="CONFIRMED",
                    evidence_role="DEFAULT_VALUE_ACTUAL",
                )
            if type_name in {"ByteProperty", "EnumProperty"} and isinstance(
                value,
                str,
            ):
                return _MaterializedValue(
                    value=FactValue("TEXT", value_text=value),
                    status="CONFIRMED",
                    evidence_role="DEFAULT_VALUE_ACTUAL",
                )
            raise _UnsupportedValue("integer property is not an integer")
        if type_name in _NUMBER_TYPES:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _UnsupportedValue("number property is not numeric")
            if not math.isfinite(float(value)):
                raise _UnsupportedValue("number property is not finite")
            return _MaterializedValue(
                value=FactValue("NUMBER", value_number=float(value)),
                status="CONFIRMED",
                evidence_role="DEFAULT_VALUE_ACTUAL",
            )
        if type_name in _TEXT_TYPES:
            if not isinstance(value, str):
                raise _UnsupportedValue("text property is not a string")
            return _MaterializedValue(
                value=FactValue("TEXT", value_text=value),
                status="CONFIRMED",
                evidence_role="DEFAULT_VALUE_ACTUAL",
            )
        if type_name in _OBJECT_TYPES:
            resolved = _resolved_object(value, extra)
            if resolved is None:
                return _MaterializedValue(
                    value=FactValue("CONFIRMED_EMPTY"),
                    status="CONFIRMED_EMPTY",
                    evidence_role="DEFAULT_VALUE_ACTUAL",
                )
            return _MaterializedValue(
                value=FactValue("ENTITY_REF", value_text=resolved),
                status="CONFIRMED",
                evidence_role="DEFAULT_VALUE_ACTUAL",
            )
        if type_name in {
            "ArrayProperty",
            "StructProperty",
            "MapProperty",
        }:
            return _bounded_container(
                type_name=type_name,
                value=value,
                extra=extra,
                detail_uri=detail_uri,
            )
        raise _UnsupportedValue(f"unsupported property type: {type_name}")
    except _PartialValue:
        return _MaterializedValue(
            value=FactValue("UNKNOWN"),
            status="NOT_RECOVERED",
            evidence_role="DEFAULT_VALUE_PARTIAL",
            partial=True,
        )
    except _UnsupportedValue:
        return _MaterializedValue(
            value=FactValue("UNKNOWN"),
            status="NOT_RECOVERED",
            evidence_role="DEFAULT_VALUE_GAP",
        )


def _decode_value(row: sqlite3.Row) -> object:
    codec = str(row["value_codec"] or "json")
    if codec == "json":
        text = str(row["value_json"])
        if len(text.encode("utf-8")) > MAX_DECODED_VALUE_BYTES:
            raise ValueError("JSON value exceeds the bounded decode limit")
        return json.loads(text)
    if codec == "zlib-json-utf8":
        blob = row["value_blob"]
        if blob is None:
            raise ValueError("compressed value_blob is missing")
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(
            bytes(blob),
            MAX_DECODED_VALUE_BYTES + 1,
        )
        if (
            len(decoded) > MAX_DECODED_VALUE_BYTES
            or decoder.unconsumed_tail
            or not decoder.eof
        ):
            raise ValueError("compressed value exceeds the bounded decode limit")
        return json.loads(decoded.decode("utf-8"))
    raise ValueError(f"unsupported value codec: {codec}")


def _safe_evidence_uri(
    raw: object,
    *,
    asset_id: str,
    revision_id: str,
    property_name: str,
) -> str:
    value = str(raw or "")
    expected = make_default_ref(asset_id, revision_id, property_name)
    if value != expected:
        raise _RejectedAsset("default_ref does not match its canonical identity")
    return value


def _read_json_object(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise _RejectedAsset(f"{path.name} is not a JSON object")
    return {str(key): value for key, value in raw.items()}


def _read_revision(connection: sqlite3.Connection) -> _RevisionIdentity:
    required = {
        "revision_id",
        "asset_id",
        "asset_name",
        "object_path",
        "source_fingerprint",
        "parser_version",
        "schema_version",
        "generated_at",
        "uasset_path",
    }
    if not required.issubset(_table_columns(connection, "asset_revisions")):
        raise _RejectedAsset("asset_revisions schema is incomplete")
    rows = list(
        connection.execute(
            """
            SELECT revision_id, asset_id, asset_name, object_path,
                   source_fingerprint, parser_version, schema_version,
                   generated_at, uasset_path
            FROM asset_revisions
            LIMIT 2
            """
        )
    )
    if len(rows) != 1:
        raise _RejectedAsset("Evidence store must contain exactly one revision")
    return _RevisionIdentity(*(str(value or "") for value in rows[0]))


def _validate_source_manifest(
    connection: sqlite3.Connection,
    *,
    asset_root: Path,
    identity: _RevisionIdentity,
) -> tuple[_SourceManifestEntry, ...]:
    required = {
        "revision_id",
        "path",
        "sha256",
        "size_bytes",
        "source_kind",
    }
    if not required.issubset(_table_columns(connection, "source_manifest")):
        raise _RejectedAsset("source_manifest schema is incomplete")
    rows = list(
        connection.execute(
            """
            SELECT revision_id, path, sha256, size_bytes, source_kind
            FROM source_manifest
            ORDER BY path
            """
        )
    )
    if not rows:
        raise _RejectedAsset("source_manifest is empty")
    source_hashes: dict[str, str] = {}
    source_kinds: dict[str, str] = {}
    entries: list[_SourceManifestEntry] = []
    for revision_id, raw_path, raw_sha, _size, raw_kind in rows:
        path = str(raw_path or "").replace("\\", "/")
        digest = str(raw_sha or "")
        kind = str(raw_kind or "")
        size = _strict_nonnegative_integer(
            _size,
            label="source_manifest.size_bytes",
            error_type=_RejectedAsset,
        )
        if str(revision_id or "") != identity.revision_id:
            raise _RejectedAsset("source_manifest revision mismatch")
        if (
            not path
            or path in source_hashes
            or path.startswith("/")
            or ".." in Path(path).parts
            or _contains_local_path(path)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
            or not _SAFE_TOKEN.fullmatch(kind)
            or (path.startswith("binary/") and kind != "package_binary")
        ):
            raise _RejectedAsset("source_manifest contains an unsafe entry")
        source_hashes[path] = digest
        source_kinds[path] = kind
        entries.append(
            _SourceManifestEntry(
                path=path,
                sha256=digest,
                size_bytes=size,
                source_kind=kind,
            )
        )

    compact = json.dumps(
        sorted(source_hashes.items()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(compact).hexdigest()
    if fingerprint != identity.source_fingerprint:
        raise _RejectedAsset("source_manifest fingerprint mismatch")
    expected_revision = make_revision_id(
        source_hashes,
        parser_version=identity.parser_version,
        schema_version=identity.schema_version,
    )
    if expected_revision != identity.revision_id:
        raise _RejectedAsset("source_manifest revision is not reproducible")

    legacy_path = "graphs_from_uasset_manifest.json"
    direct_path = "@memory/normalized_graph_facts"
    legacy_present = legacy_path in source_hashes
    direct_present = direct_path in source_hashes
    if legacy_present and source_kinds.get(legacy_path) != "graph_manifest":
        raise _RejectedAsset("legacy marker source_kind is invalid")
    if direct_present and source_kinds.get(direct_path) != "in_memory_capture":
        raise _RejectedAsset("direct marker source_kind is invalid")
    legacy_mode = legacy_present and source_kinds.get(legacy_path) == "graph_manifest"
    direct_mode = (
        direct_present and source_kinds.get(direct_path) == "in_memory_capture"
    )
    if legacy_mode == direct_mode:
        raise _RejectedAsset("source_manifest must identify exactly one capture mode")
    expected_parser = (
        LEGACY_CAPTURE_PARSER_VERSION if legacy_mode else DIRECT_PAYLOAD_PARSER_VERSION
    )
    if identity.parser_version != expected_parser:
        raise _RejectedAsset("capture mode and parser version conflict")
    return tuple(entries)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _blueprint_aggregate_fingerprint(database_path: Path) -> str:
    digest = hashlib.sha256()
    for path in (
        database_path,
        database_path.parent / "manifest.json",
    ):
        if not path.is_file():
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\n")
    return digest.hexdigest()


def _current_package_metadata(path: Path) -> dict[str, object]:
    extension = path.suffix.casefold()
    uexp = path.with_suffix(".uexp") if extension == ".uasset" else None
    ubulk = path.with_suffix(".ubulk") if extension == ".uasset" else None
    uexp_exists = bool(uexp and uexp.is_file())
    ubulk_exists = bool(ubulk and ubulk.is_file())
    return {
        "file_size_total": (
            path.stat().st_size
            + (uexp.stat().st_size if uexp_exists and uexp else 0)
            + (ubulk.stat().st_size if ubulk_exists and ubulk else 0)
        ),
        "has_uasset": int(extension == ".uasset"),
        "has_uexp": int(uexp_exists),
        "has_ubulk": int(ubulk_exists),
    }


def _validate_current_package(
    *,
    candidate: _AssetCandidate,
    identity: _RevisionIdentity,
    asset_root: Path,
    source_manifest: Sequence[_SourceManifestEntry],
) -> None:
    raw_path = Path(identity.uasset_path)
    package_path = (
        raw_path if raw_path.is_absolute() else (asset_root / raw_path).resolve()
    )
    if not package_path.is_file() or package_path.suffix.casefold() not in {
        ".uasset",
        ".umap",
    }:
        raise _FreshnessGap("current package binary is unavailable")
    binary_entries = [
        entry for entry in source_manifest if entry.source_kind == "package_binary"
    ]
    if not binary_entries:
        raise _FreshnessGap("Evidence source_manifest has no package binary hashes")
    matched_primary = False
    for entry in binary_entries:
        expected_path = package_path.with_name(Path(entry.path).name)
        if expected_path.name == package_path.name:
            matched_primary = True
        if (
            not expected_path.is_file()
            or expected_path.stat().st_size != entry.size_bytes
            or _sha256_file(expected_path) != entry.sha256
        ):
            raise _FreshnessGap(
                "current package binary differs from Evidence source_manifest"
            )
    if not matched_primary:
        raise _FreshnessGap("Evidence source_manifest omits the primary package binary")
    current_metadata = _current_package_metadata(package_path)
    discovery_metadata = {
        "file_size_total": candidate.file_size_total,
        "has_uasset": candidate.has_uasset,
        "has_uexp": candidate.has_uexp,
        "has_ubulk": candidate.has_ubulk,
    }
    # Discovery's final source_fingerprint also folds in a Registry fingerprint,
    # but the per-asset Registry fingerprint is not retained in the published
    # bundle.  Do not pretend it can be recomputed here.  Instead, require the
    # composite identifier to be well formed, compare stable package shape
    # fields, and cryptographically verify the current binaries against
    # Evidence source_manifest above.  File modification time is deliberately
    # excluded: touching an otherwise identical verified package must not
    # change semantic ingestion.
    if (
        not re.fullmatch(
            r"[0-9a-fA-F]{64}",
            candidate.source_fingerprint,
        )
        or current_metadata != discovery_metadata
    ):
        raise _FreshnessGap("current package metadata differs from Discovery inventory")


def _validate_identity(
    connection: sqlite3.Connection,
    *,
    candidate: _AssetCandidate,
    asset_root: Path,
    expected_asset_name: str,
) -> _RevisionIdentity:
    identity = _read_revision(connection)
    if (
        identity.object_path != candidate.object_path
        or identity.asset_name != expected_asset_name
        or identity.revision_id != candidate.evidence_revision
        or identity.schema_version != EVIDENCE_SCHEMA_VERSION
        or not _is_canonical_unreal_uri(identity.object_path)
        or not _SAFE_TOKEN.fullmatch(identity.asset_id)
        or identity.asset_id != make_asset_id(identity.object_path)
        or not _SAFE_TOKEN.fullmatch(identity.revision_id)
        or _contains_local_path(identity.generated_at)
    ):
        raise _RejectedAsset("Discovery and Evidence identities do not match")
    manifest_path = asset_root / "evidence" / "manifest.json"
    if not manifest_path.is_file():
        raise _RejectedAsset("Evidence manifest is missing")
    manifest = _read_json_object(manifest_path)
    expected = {
        "asset_id": identity.asset_id,
        "asset_name": identity.asset_name,
        "object_path": identity.object_path,
        "revision_id": identity.revision_id,
        "source_fingerprint": identity.source_fingerprint,
        "parser_version": identity.parser_version,
        "schema": identity.schema_version,
        "database": "evidence.sqlite",
    }
    if any(str(manifest.get(key) or "") != value for key, value in expected.items()):
        raise _RejectedAsset("Evidence manifest identity mismatch")
    source_manifest = _validate_source_manifest(
        connection,
        asset_root=asset_root,
        identity=identity,
    )
    _validate_current_package(
        candidate=candidate,
        identity=identity,
        asset_root=asset_root,
        source_manifest=source_manifest,
    )
    return identity


def _source_revision_id(
    core: sqlite3.Connection,
    identity: _RevisionIdentity,
) -> int:
    source_uri = f"bp://{identity.asset_id}@{identity.revision_id}"
    core.execute(
        """
        INSERT OR IGNORE INTO source_revisions(
            source_kind, source_uri, source_fingerprint, producer_version,
            schema_version, generated_at, freshness_status
        ) VALUES (
            'blueprint_evidence', ?, ?, ?, ?, ?, 'FRESH'
        )
        """,
        (
            source_uri,
            identity.source_fingerprint,
            identity.parser_version,
            identity.schema_version,
            identity.generated_at,
        ),
    )
    row = core.execute(
        """
        SELECT revision_id
        FROM source_revisions
        WHERE source_kind='blueprint_evidence'
          AND source_uri=?
          AND source_fingerprint=?
        """,
        (source_uri, identity.source_fingerprint),
    ).fetchone()
    if row is None:
        raise RuntimeError("Blueprint source revision was not materialized")
    return int(row[0])


def _explicit_blueprint_sources(
    source_revisions: Sequence[SourceRevision] | None,
) -> dict[str, _ExplicitBlueprintSource] | None:
    if source_revisions is None:
        return None
    if len(source_revisions) > MAX_EXPLICIT_BLUEPRINT_SOURCES:
        raise ValueError(
            "explicit Blueprint subset exceeds the reviewed source bound"
        )
    result: dict[str, _ExplicitBlueprintSource] = {}
    source_uris: set[str] = set()
    for revision in source_revisions:
        encoded_name = (
            revision.source_uri[len("capture://") :]
            if revision.source_uri.startswith("capture://")
            else ""
        )
        capture_asset_name = unquote(encoded_name)
        if (
            revision.source_kind != "BLUEPRINT_EVIDENCE"
            or not encoded_name
            or "/" in capture_asset_name
            or "\\" in capture_asset_name
            or not _SAFE_TOKEN.fullmatch(capture_asset_name)
            or not _is_canonical_unreal_uri(revision.entity_uri)
            or not _SAFE_TOKEN.fullmatch(revision.revision_label)
            or revision.entity_uri in result
            or revision.source_uri in source_uris
        ):
            raise ValueError(
                "explicit Blueprint subset contains an unsafe or duplicate "
                "source identity"
            )
        source_uris.add(revision.source_uri)
        result[revision.entity_uri] = _ExplicitBlueprintSource(
            source_uri=revision.source_uri,
            entity_uri=revision.entity_uri,
            revision_label=revision.revision_label,
            fingerprint=revision.fingerprint,
            capture_asset_name=capture_asset_name,
        )
    return result


def _asset_candidates(
    discovery: sqlite3.Connection,
    *,
    explicit_sources: Mapping[str, _ExplicitBlueprintSource] | None = None,
) -> tuple[list[_AssetCandidate], int]:
    required = {
        "object_path",
        "asset_name",
        "capture_exists",
        "evidence_revision",
        "evidence_freshness",
        "source_fingerprint",
        "file_size_total",
        "source_modified",
        "has_uasset",
        "has_uexp",
        "has_ubulk",
    }
    if not required.issubset(_table_columns(discovery, "assets")):
        return [], 0
    if explicit_sources is not None and not explicit_sources:
        return [], 0
    parameters: tuple[object, ...] = ()
    selection = "capture_exists=1"
    if explicit_sources is not None:
        parameters = tuple(sorted(explicit_sources))
        placeholders = ",".join("?" for _ in parameters)
        selection = f"object_path IN ({placeholders})"
    rows = list(
        discovery.execute(
            f"""
            SELECT object_path, asset_name, evidence_revision,
                   evidence_freshness, source_fingerprint,
                   file_size_total, source_modified,
                   has_uasset, has_uexp, has_ubulk
            FROM assets
            WHERE {selection}
            ORDER BY object_path
            """,
            parameters,
        )
    )
    candidates: list[_AssetCandidate] = []
    stale = 0
    for (
        object_path,
        asset_name,
        revision,
        freshness,
        source_fingerprint,
        file_size_total,
        source_modified,
        has_uasset,
        has_uexp,
        has_ubulk,
    ) in rows:
        explicit = (
            explicit_sources.get(str(object_path or ""))
            if explicit_sources is not None
            else None
        )
        if explicit_sources is not None and explicit is None:
            continue
        if explicit is None and str(freshness or "").upper() != "FRESH":
            stale += 1
            continue
        candidates.append(
            _AssetCandidate(
                object_path=str(object_path or ""),
                asset_name=str(asset_name or ""),
                evidence_revision=(
                    explicit.revision_label
                    if explicit is not None
                    else str(revision or "")
                ),
                source_fingerprint=str(source_fingerprint or ""),
                file_size_total=int(file_size_total or 0),
                source_modified=str(source_modified or ""),
                has_uasset=int(has_uasset or 0),
                has_uexp=int(has_uexp or 0),
                has_ubulk=int(has_ubulk or 0),
                capture_asset_name=(
                    explicit.capture_asset_name
                    if explicit is not None
                    else ""
                ),
            )
        )
    return candidates, stale


def _safe_asset_root(capture_root: Path, asset_name: str) -> Path:
    root = capture_root.resolve()
    candidate = (root / asset_name).resolve()
    if not asset_name or candidate.parent != root or _contains_local_path(asset_name):
        raise _RejectedAsset("unsafe capture asset name")
    return candidate


def _capture_asset_name(candidate: _AssetCandidate) -> str:
    """Resolve the deterministic capture directory without tree enumeration.

    A few legacy Discovery rows store the package path in ``asset_name`` and
    use that same package path as ``object_path``.  Their Evidence identity and
    capture directory still use the final package segment.  Accept only that
    exact, internally consistent legacy shape; arbitrary path-like names stay
    rejected.
    """

    if candidate.capture_asset_name:
        return candidate.capture_asset_name
    raw_name = candidate.asset_name.strip().replace("\\", "/")
    if "/" not in raw_name:
        return raw_name
    if (
        raw_name != candidate.object_path
        or not _is_canonical_unreal_uri(raw_name)
        or raw_name.endswith("/")
    ):
        raise _RejectedAsset("unsafe path-like capture asset name")
    leaf = raw_name.rsplit("/", 1)[-1]
    if not _SAFE_TOKEN.fullmatch(leaf):
        raise _RejectedAsset("unsafe legacy capture asset name")
    return leaf


def materialize_blueprint_defaults(
    discovery: sqlite3.Connection,
    core: sqlite3.Connection,
    *,
    capture_root: Path,
    ontology: OntologyBundle,
    source_revisions: Sequence[SourceRevision] | None = None,
) -> BlueprintIngestResult:
    """Read only Discovery-selected FRESH Evidence stores into Core facts.

    Capture lookup is bounded by rows in ``Discovery.assets``.  The importer
    never enumerates the capture tree and never persists local source paths.
    """

    explicit_sources = _explicit_blueprint_sources(source_revisions)
    candidates, stale_assets = _asset_candidates(
        discovery,
        explicit_sources=explicit_sources,
    )
    if explicit_sources is not None and {
        candidate.object_path for candidate in candidates
    } != set(explicit_sources):
        raise ValueError(
            "explicit Blueprint subset is not present in Discovery inventory"
        )
    entity_ids = {
        str(uri): int(entity_id)
        for uri, entity_id in core.execute(
            "SELECT canonical_uri, entity_id FROM entities"
        )
    }
    covered: set[tuple[str, str]] = set()
    freshness_gaps: set[str] = set()
    untrusted_assets: set[str] = set()
    fact_ids: set[int] = set()
    materialized_entity_ids: set[int] = set()
    counts = {
        "freshAssets": 0,
        "staleAssets": stale_assets,
        "rejectedAssets": 0,
        "freshnessGapAssets": 0,
        "packageVerifiedAssets": 0,
        "sourceRevisions": 0,
        "declaredFacts": 0,
        "factEvidence": 0,
        "notRecoveredFacts": 0,
        "partialFacts": 0,
        "summaryFacts": 0,
    }
    for ordinal, candidate in enumerate(candidates):
        entity_id = entity_ids.get(candidate.object_path)
        if entity_id is None:
            counts["rejectedAssets"] += 1
            untrusted_assets.add(candidate.object_path)
            continue
        try:
            capture_asset_name = _capture_asset_name(candidate)
            asset_root = _safe_asset_root(capture_root, capture_asset_name)
            database_path = asset_root / "evidence" / "evidence.sqlite"
            if not database_path.is_file():
                raise _RejectedAsset("Evidence database is missing")
            explicit = (
                explicit_sources.get(candidate.object_path)
                if explicit_sources is not None
                else None
            )
            if (
                explicit is not None
                and _blueprint_aggregate_fingerprint(database_path)
                != explicit.fingerprint
            ):
                raise _RejectedAsset(
                    "Evidence aggregate changed after source manifest scan"
                )
            evidence = sqlite3.connect(
                f"file:{database_path.resolve().as_posix()}?mode=ro",
                uri=True,
            )
            evidence.row_factory = sqlite3.Row
            evidence.execute("PRAGMA query_only=ON")
            try:
                identity = _validate_identity(
                    evidence,
                    candidate=candidate,
                    asset_root=asset_root,
                    expected_asset_name=capture_asset_name,
                )
                columns = _table_columns(evidence, "class_defaults")
                required = {
                    "default_ref",
                    "revision_id",
                    "name",
                    "type_name",
                    "value_json",
                    "value_codec",
                    "confidence",
                    "extra_json",
                }
                if not required.issubset(columns):
                    raise _RejectedAsset("class_defaults schema is incomplete")
                blob_projection = (
                    "value_blob" if "value_blob" in columns else "NULL AS value_blob"
                )
                default_rows = list(
                    evidence.execute(
                        f"""
                        SELECT default_ref, revision_id, name, type_name,
                               value_json, value_codec, {blob_projection},
                               confidence, extra_json
                        FROM class_defaults
                        ORDER BY default_ref
                        """
                    )
                )
                for row in default_rows:
                    property_name = str(row["name"] or "")
                    if (
                        not property_name.strip()
                        or _contains_local_path(property_name)
                        or str(row["revision_id"] or "") != identity.revision_id
                        or str(row["default_ref"] or "")
                        != make_default_ref(
                            identity.asset_id,
                            identity.revision_id,
                            property_name,
                        )
                    ):
                        raise _RejectedAsset(
                            "class_defaults contains a noncanonical identity"
                        )
            finally:
                evidence.close()
        except _FreshnessGap:
            counts["freshnessGapAssets"] += 1
            freshness_gaps.add(candidate.object_path)
            continue
        except (
            OSError,
            sqlite3.DatabaseError,
            ValueError,
        ):
            counts["rejectedAssets"] += 1
            untrusted_assets.add(candidate.object_path)
            continue

        savepoint = f"blueprint_asset_{ordinal}"
        core.execute(f"SAVEPOINT {savepoint}")
        try:
            source_revision_id = _source_revision_id(core, identity)
            for row in default_rows:
                property_name = str(row["name"] or "")
                if (
                    not property_name.strip()
                    or str(row["revision_id"] or "") != identity.revision_id
                    or _contains_local_path(property_name)
                ):
                    raise RuntimeError("prevalidated class_default identity changed")
                evidence_uri = _safe_evidence_uri(
                    row["default_ref"],
                    asset_id=identity.asset_id,
                    revision_id=identity.revision_id,
                    property_name=property_name,
                )
                try:
                    decoded = _decode_value(row)
                    raw_extra = json.loads(str(row["extra_json"] or "{}"))
                    if not isinstance(raw_extra, Mapping):
                        raise ValueError("extra_json is not an object")
                    extra = {str(key): value for key, value in raw_extra.items()}
                    materialized = _materialized_value(
                        type_name=str(row["type_name"] or ""),
                        value=decoded,
                        extra=extra,
                        detail_uri=evidence_uri,
                    )
                except (
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                    zlib.error,
                ):
                    materialized = _MaterializedValue(
                        value=FactValue("UNKNOWN"),
                        status="NOT_RECOVERED",
                        evidence_role="DEFAULT_VALUE_GAP",
                    )
                confidence = str(row["confidence"] or "UNKNOWN").upper()
                if confidence not in _SAFE_CONFIDENCE:
                    confidence = "UNKNOWN"
                fact_id = store_fact(
                    core,
                    ontology=ontology,
                    subject_entity_id=entity_id,
                    fact_type="DECLARED_DEFAULT",
                    fact_name=property_name,
                    scope_kind="DECLARED",
                    declared_on_entity_id=entity_id,
                    value=materialized.value,
                    status=materialized.status,
                    confidence=confidence,
                    source_revision_id=source_revision_id,
                    evidence_uri=evidence_uri,
                    evidence_role=materialized.evidence_role,
                )
                fact_ids.add(fact_id)
                covered.add((candidate.object_path, property_name))
                if materialized.status == "NOT_RECOVERED":
                    counts["notRecoveredFacts"] += 1
                if materialized.partial:
                    counts["partialFacts"] += 1
                if materialized.summary:
                    counts["summaryFacts"] += 1
            core.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            core.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            core.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        counts["freshAssets"] += 1
        counts["packageVerifiedAssets"] += 1
        counts["sourceRevisions"] += 1
        materialized_entity_ids.add(entity_id)

    counts["declaredFacts"] = len(fact_ids)
    if fact_ids:
        placeholders = ",".join("?" for _ in fact_ids)
        counts["factEvidence"] = int(
            core.execute(
                f"""
                SELECT COUNT(*) FROM fact_evidence
                WHERE fact_id IN ({placeholders})
                """,
                tuple(sorted(fact_ids)),
            ).fetchone()[0]
        )
    if (
        explicit_sources is not None
        and (
            counts["freshAssets"] != len(explicit_sources)
            or counts["freshnessGapAssets"]
            or counts["rejectedAssets"]
        )
    ):
        raise ValueError(
            "explicit Blueprint subset failed evidence or freshness validation"
        )
    core.commit()
    return BlueprintIngestResult(
        counts=counts,
        covered_properties=frozenset(covered),
        freshness_gap_assets=frozenset(freshness_gaps),
        untrusted_assets=frozenset(untrusted_assets),
        fact_ids=frozenset(fact_ids),
        entity_ids=frozenset(materialized_entity_ids),
    )
