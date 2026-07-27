r"""Export an immutable ARK DevKit UClass hierarchy snapshot.

Run this file inside ARK DevKit Unreal Python. The exporter reflects the real
UClass for every class path present in the Asset Registry and publishes one
verified generation:

    class_hierarchy_manifest.json
    generations/<generation-id>/class_hierarchy.jsonl
    generations/<generation-id>/class_hierarchy_checkpoint.json

The root manifest is the single publication pointer. A generation directory is
renamed into place before that pointer is replaced, so readers never observe a
partially written hierarchy. Interrupted reflection resumes from the last
fsynced JSONL byte offset.
"""

from __future__ import print_function

import datetime
import hashlib
import json
import os
import re
import time
import uuid

try:
    import unreal
except Exception:
    unreal = None


SNAPSHOT_SCHEMA = "ark.kb.class-hierarchy-snapshot.v1"
CHECKPOINT_SCHEMA = "ark.kb.class-hierarchy-checkpoint.v1"
CLASS_ROW_SCHEMA = "ark.kb.class-hierarchy-row.v1"

CLASS_OUTPUT_NAME = "class_hierarchy.jsonl"
CHECKPOINT_OUTPUT_NAME = "class_hierarchy_checkpoint.json"
MANIFEST_OUTPUT_NAME = "class_hierarchy_manifest.json"
GENERATIONS_DIRECTORY_NAME = "generations"
STAGING_DIRECTORY_NAME = ".class_hierarchy_work"
MANIFEST_STAGING_NAME = ".class_hierarchy_manifest_to_publish.json"

DEFAULT_BATCH_SIZE = 250
MAX_BATCH_SIZE = 5000
MANIFEST_REPLACE_ATTEMPTS = 4
MANIFEST_REPLACE_DELAY_SECONDS = 0.15

CLASS_TAGS = (
    "GeneratedClass",
    "ParentClass",
    "NativeParentClass",
    "ImplementedInterfaces",
    "ComponentClass",
    "ComponentTemplateClass",
    "NativeComponentClass",
)

_CLASS_PATH_RE = re.compile(r"/[A-Za-z0-9_]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9_.-]+")
_WRAPPED_REFERENCE_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*|"
    r"/[A-Za-z0-9_]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9_.-]+)"
    r"'(?P<target>/[A-Za-z0-9_]+"
    r"(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9_.-]+)"
    r"(?:[:][^']*)?'"
)
_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\\?\\[a-z]:\\|\\\\[^\\/\r\n]+[\\/])"
)
_LOCAL_UNIX_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'])/(?:users|home|root|tmp|var|etc|usr|opt|mnt)/"
)
_GENERATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _utc_now():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _log(message):
    text = "[KBClassHierarchy] " + str(message)
    print(text)
    if unreal is not None:
        try:
            unreal.log(text)
        except Exception:
            pass


def _safe_exception(exc):
    return "{} (errno={}, winerror={})".format(
        type(exc).__name__,
        getattr(exc, "errno", None),
        getattr(exc, "winerror", None),
    )


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _contains_local_path(value):
    text = str(value or "")
    return bool(
        _WINDOWS_PATH_RE.search(text)
        or _LOCAL_UNIX_PATH_RE.search(text)
        or "file://" in text.casefold()
    )


def _assert_sanitized(value):
    serialized = _canonical_json(value)
    if _contains_local_path(serialized):
        raise RuntimeError("Class hierarchy output contains a local path")


def _fsync_directory(path):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except Exception:
        pass
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass


def _write_json_atomic(path, payload):
    _assert_sanitized(payload)
    temporary = path + ".tmp." + str(os.getpid())
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    try:
        with open(temporary, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(MANIFEST_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                _fsync_directory(os.path.dirname(path))
                return
            except PermissionError:
                if attempt + 1 >= MANIFEST_REPLACE_ATTEMPTS:
                    raise
                time.sleep(MANIFEST_REPLACE_DELAY_SECONDS)
    finally:
        if os.path.isfile(temporary):
            try:
                os.remove(temporary)
            except Exception:
                pass


def _file_integrity(path):
    digest = hashlib.sha256()
    byte_count = 0
    line_count = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            byte_count += len(block)
            line_count += block.count(b"\n")
    return {
        "sha256": digest.hexdigest(),
        "bytes": byte_count,
        "lines": line_count,
    }


def _committed_rows_integrity(path, byte_count):
    expected_bytes = int(byte_count)
    if expected_bytes < 0 or not os.path.isfile(path):
        raise ValueError("Committed row byte count is invalid")
    with open(path, "rb") as handle:
        payload = handle.read(expected_bytes)
        if len(payload) != expected_bytes:
            raise ValueError("Committed row prefix is shorter than checkpoint")
    if payload and not payload.endswith(b"\n"):
        raise ValueError("Committed JSONL prefix does not end at a row boundary")
    rows = payload.splitlines()
    row_chain_sha256 = hashlib.sha256(b"").hexdigest()
    for encoded_row in rows:
        try:
            row = json.loads(encoded_row.decode("utf-8"))
        except Exception:
            raise ValueError("Committed row prefix contains invalid JSONL")
        if (
            not isinstance(row, dict)
            or row.get("schema") != CLASS_ROW_SCHEMA
            or not _normalize_class_path(row.get("class_path"))
        ):
            raise ValueError("Committed row prefix contains an invalid class row")
        _assert_sanitized(row)
        row_chain_sha256 = _advance_row_chain(
            row_chain_sha256,
            [row],
        )
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "row_chain_sha256": row_chain_sha256,
        "bytes": len(payload),
        "lines": len(rows),
    }


def _sha256_file(path):
    return _file_integrity(path)["sha256"]


def _append_rows(path, rows):
    with open(path, "ab") as handle:
        for row in rows:
            _assert_sanitized(row)
            handle.write(_canonical_json(row).encode("utf-8"))
            handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())
        return handle.tell()


def _advance_row_chain(previous_sha256, rows):
    try:
        previous = bytes.fromhex(str(previous_sha256))
    except ValueError:
        raise ValueError("Row chain SHA is invalid")
    if len(previous) != 32:
        raise ValueError("Row chain SHA is invalid")
    current = previous
    for row in rows:
        _assert_sanitized(row)
        encoded = _canonical_json(row).encode("utf-8") + b"\n"
        current = hashlib.sha256(current + b"\0" + encoded).digest()
    return current.hex()


def _truncate_file(path, byte_count):
    mode = "r+b" if os.path.isfile(path) else "w+b"
    with open(path, mode) as handle:
        handle.truncate(max(0, int(byte_count or 0)))
        handle.flush()
        os.fsync(handle.fileno())


def _normalize_class_path(value):
    text = str(value or "").replace("\\", "/").replace("\x00", "").strip()
    matches = _CLASS_PATH_RE.findall(text)
    if not matches:
        return ""
    return matches[0].rstrip("'\"),]")


def _valid_generation_id(value):
    return bool(_GENERATION_ID_RE.fullmatch(str(value or "")))


def _extract_class_paths(value):
    text = str(value or "").replace("\\", "/").replace("\x00", "")
    return set(
        match.rstrip("'\"),]") for match in _CLASS_PATH_RE.findall(text) if match
    )


def _reference_target_paths(value):
    text = str(value or "").replace("\\", "/").replace("\x00", "")
    wrapped = {
        match.group("target").rstrip("'\"),]")
        for match in _WRAPPED_REFERENCE_RE.finditer(text)
        if match.group("target")
    }
    if wrapped:
        return wrapped
    return _extract_class_paths(text)


def _implemented_interface_paths(value):
    text = str(value or "").replace("\\", "/").replace("\x00", "")
    entries = list(re.finditer(r"(?i)(?:^|[,(])\s*Interface\s*=", text))
    if not entries:
        compact = re.sub(r"[\s\"']", "", text)
        return set(), compact in {"", "()", "[]"}
    paths = set()
    complete = True
    for ordinal, entry in enumerate(entries):
        segment_end = (
            entries[ordinal + 1].start() if ordinal + 1 < len(entries) else len(text)
        )
        segment = text[entry.end() : segment_end]
        graphs = re.search(r"(?i),\s*Graphs\s*=", segment)
        if graphs is not None:
            segment = segment[: graphs.start()]
        targets = _reference_target_paths(segment)
        if len(targets) != 1:
            complete = False
            continue
        paths.update(targets)
    return paths, complete


def _class_paths_from_asset_record(record):
    paths = set()
    asset_class_path = _normalize_class_path(record.get("asset_class_path"))
    if asset_class_path:
        paths.add(asset_class_path)
    tags = record.get("tags")
    if not isinstance(tags, dict):
        tags = {}
    for tag_name in CLASS_TAGS:
        if tag_name == "ImplementedInterfaces":
            interface_paths, _complete = _implemented_interface_paths(
                tags.get(tag_name)
            )
            paths.update(interface_paths)
        else:
            paths.update(_reference_target_paths(tags.get(tag_name)))
    return paths


def _inventory_record_sha256(record):
    _assert_sanitized(record)
    return hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()


def _inventory_signature(payload):
    _assert_sanitized(payload)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _top_level_asset_path(class_path):
    normalized = _normalize_class_path(class_path)
    if not normalized or "." not in normalized or unreal is None:
        return normalized
    constructor = getattr(unreal, "TopLevelAssetPath", None)
    if not callable(constructor):
        raise RuntimeError("Unreal TopLevelAssetPath is unavailable")
    package_name, asset_name = normalized.rsplit(".", 1)
    return constructor(package_name, asset_name)


def _ancestor_paths(class_path, ancestor_getter):
    try:
        ancestors = ancestor_getter(_top_level_asset_path(class_path))
    except Exception:
        return None
    if ancestors is None:
        return None
    if (
        isinstance(ancestors, tuple)
        and len(ancestors) == 2
        and isinstance(ancestors[0], bool)
    ):
        if not ancestors[0]:
            return None
        ancestors = ancestors[1]
    result = set()
    for value in _iter_collection(ancestors):
        ancestor_path = _normalize_class_path(_unreal_text(value))
        if ancestor_path and ancestor_path != class_path:
            result.add(ancestor_path)
    return result


def _expand_ancestor_class_paths(class_paths, ancestor_getter):
    discovered = set(
        path for path in (_normalize_class_path(value) for value in class_paths) if path
    )
    queue = sorted(discovered)
    cursor = 0
    while cursor < len(queue):
        class_path = queue[cursor]
        cursor += 1
        ancestors = _ancestor_paths(class_path, ancestor_getter) or ()
        for ancestor_path in ancestors:
            if ancestor_path in discovered:
                continue
            discovered.add(ancestor_path)
            queue.append(ancestor_path)
    return discovered


def _registry_parent_map(class_paths, ancestor_getter):
    ancestor_map = {}
    complete = True
    unresolved = set()
    for class_path in sorted(set(class_paths)):
        ancestors = _ancestor_paths(class_path, ancestor_getter)
        if ancestors is None:
            complete = False
            unresolved.add(class_path)
            ancestor_map[class_path] = set()
        else:
            ancestor_map[class_path] = set(ancestors)
    all_ancestors = set().union(*ancestor_map.values()) if ancestor_map else set()
    for ancestor in sorted(all_ancestors):
        if ancestor in ancestor_map:
            continue
        values = _ancestor_paths(ancestor, ancestor_getter)
        if values is None:
            complete = False
            unresolved.add(ancestor)
            values = set()
        ancestor_map[ancestor] = set(values)

    parents = {}
    ambiguous = set()
    for class_path, ancestors in ancestor_map.items():
        if not ancestors:
            continue
        if ancestors.intersection(unresolved):
            ambiguous.add(class_path)
            continue
        depths = {
            candidate: len(ancestor_map.get(candidate, set()).intersection(ancestors))
            for candidate in ancestors
        }
        deepest = max(depths.values())
        candidates = sorted(
            candidate for candidate, depth in depths.items() if depth == deepest
        )
        if len(candidates) == 1:
            parents[class_path] = candidates[0]
        else:
            ambiguous.add(class_path)
    return parents, ambiguous, complete


def _read_field(value, names, default=None):
    for name in names:
        try:
            result = getattr(value, name)
        except Exception:
            result = None
        if result is not None:
            return result
        getter = getattr(value, "get_editor_property", None)
        if callable(getter):
            try:
                result = getter(name)
            except Exception:
                result = None
            if result is not None:
                return result
    return default


def _unreal_text(value):
    if value is None:
        return ""
    package_name = _read_field(value, ("package_name",), None)
    asset_name = _read_field(value, ("asset_name",), None)
    if package_name is not None and asset_name is not None:
        package_text = str(package_name or "").strip()
        asset_text = str(asset_name or "").strip()
        if package_text and asset_text:
            return package_text + "." + asset_text
    for method_name in ("to_string", "export_text", "get_path_name"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                text = str(method() or "").strip()
                if text:
                    return text
            except Exception:
                pass
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _asset_tag_value(asset_data, tag_name):
    getter = getattr(asset_data, "get_tag_value", None)
    if not callable(getter):
        return ""
    try:
        return _unreal_text(getter(tag_name))
    except Exception:
        return ""


def _asset_record(asset_data):
    object_path = _unreal_text(
        _read_field(asset_data, ("object_path", "soft_object_path"), "")
    )
    package_name = _unreal_text(_read_field(asset_data, ("package_name",), ""))
    asset_name = _unreal_text(_read_field(asset_data, ("asset_name",), ""))
    asset_class_path = _unreal_text(
        _read_field(asset_data, ("asset_class_path", "asset_class"), "")
    )
    tags = {}
    for tag_name in CLASS_TAGS:
        tag_value = _asset_tag_value(asset_data, tag_name)
        if tag_value:
            tags[tag_name] = tag_value
    return {
        "object_path": object_path,
        "package_name": package_name,
        "asset_name": asset_name,
        "asset_class_path": asset_class_path,
        "tags": tags,
    }


def _object_class_path(value):
    if value is None:
        return ""
    return _normalize_class_path(_unreal_text(value))


def _call_first_result(value, names):
    available = False
    for name in names:
        method = getattr(value, name, None)
        if not callable(method):
            continue
        available = True
        try:
            return True, method(), name
        except Exception:
            pass
    return False, None, "AVAILABLE_BUT_FAILED" if available else "UNAVAILABLE"


def _interface_class_path(value):
    direct = _object_class_path(value)
    if direct:
        return direct
    nested = _read_field(
        value,
        ("class", "class_", "interface_class", "interface_class_path"),
        None,
    )
    return _object_class_path(nested)


def _iter_collection(value):
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    try:
        return list(value)
    except Exception:
        return [value]


def _reflect_class_row(
    class_path,
    loader,
    parent_hint="",
    parent_hint_source="",
    interface_hints=(),
    interfaces_complete=False,
):
    normalized = _normalize_class_path(class_path)
    if not normalized:
        raise ValueError("class_path must be a canonical Unreal class path")
    sources = set()
    super_path = _normalize_class_path(parent_hint)
    if super_path:
        parent_status = "CONFIRMED"
        sources.add(parent_hint_source or "asset_registry_class_ancestry")
    elif normalized == "/Script/CoreUObject.Object":
        parent_status = "CONFIRMED_ROOT"
        sources.add("core_uobject_root")
    else:
        parent_status = "NOT_RECOVERED"

    interface_paths = set(
        path
        for path in (_normalize_class_path(value) for value in interface_hints)
        if path
    )
    if interfaces_complete:
        interfaces_status = "CONFIRMED"
        sources.add("asset_registry_implemented_interfaces")
    elif normalized == "/Script/CoreUObject.Object":
        interfaces_status = "CONFIRMED_EMPTY"
    else:
        interfaces_status = "NOT_RECOVERED"

    needs_parent = parent_status == "NOT_RECOVERED"
    needs_interfaces = interfaces_status == "NOT_RECOVERED"
    reflected = None
    if needs_parent or needs_interfaces:
        try:
            reflected = loader(normalized)
        except Exception:
            reflected = None

    if reflected is not None:
        if needs_parent:
            parent_ok, super_class, parent_method = _call_first_result(
                reflected,
                ("get_super_class", "get_super_struct"),
            )
            if parent_ok:
                recovered = _object_class_path(super_class)
                if recovered:
                    super_path = recovered
                    parent_status = "RECOVERED_UNVERIFIED_RUNTIME_API"
                    sources.add(parent_method)
        if needs_interfaces:
            interfaces_ok, interfaces, interfaces_method = _call_first_result(
                reflected,
                ("get_interfaces", "get_implemented_interfaces"),
            )
            if interfaces_ok:
                interface_paths.update(
                    path
                    for path in (
                        _interface_class_path(item)
                        for item in _iter_collection(interfaces)
                    )
                    if path
                )
                interfaces_status = "RECOVERED_UNVERIFIED_RUNTIME_API"
                sources.add(interfaces_method)

    parent_confirmed = parent_status in {"CONFIRMED", "CONFIRMED_ROOT"}
    interfaces_confirmed = interfaces_status in {
        "CONFIRMED",
        "CONFIRMED_EMPTY",
    }
    if parent_confirmed and interfaces_confirmed:
        status = "CONFIRMED_ROOT" if parent_status == "CONFIRMED_ROOT" else "CONFIRMED"
        confidence = "HIGH"
    elif super_path or interface_paths or parent_confirmed or interfaces_confirmed:
        status = "PARTIAL"
        confidence = "MEDIUM"
    else:
        status = "NOT_RECOVERED"
        confidence = "LOW"
    return {
        "schema": CLASS_ROW_SCHEMA,
        "class_path": normalized,
        "super_class_path": super_path,
        "is_native": normalized.startswith("/Script/"),
        "interfaces": sorted(interface_paths),
        "parent_status": parent_status,
        "interfaces_status": interfaces_status,
        "source": "+".join(sorted(sources)) or "not_recovered",
        "status": status,
        "confidence": confidence,
    }


def _quarantined_class_row(
    class_path,
    parent_hint="",
    parent_hint_source="",
    interface_hints=(),
    interfaces_complete=False,
    interruption_attempts=2,
):
    normalized = _normalize_class_path(class_path)
    super_path = _normalize_class_path(parent_hint)
    sources = {"checkpoint_repeated_interruption"}
    if super_path:
        parent_status = "CONFIRMED"
        sources.add(parent_hint_source or "asset_registry_class_ancestry")
    elif normalized == "/Script/CoreUObject.Object":
        parent_status = "CONFIRMED_ROOT"
        sources.add("core_uobject_root")
    else:
        parent_status = "NOT_RECOVERED"

    interface_paths = sorted(
        {
            path
            for path in (_normalize_class_path(value) for value in interface_hints)
            if path
        }
    )
    if interfaces_complete:
        interfaces_status = "CONFIRMED"
        sources.add("asset_registry_implemented_interfaces")
    elif normalized == "/Script/CoreUObject.Object":
        interfaces_status = "CONFIRMED_EMPTY"
    else:
        interfaces_status = "NOT_RECOVERED"

    return {
        "schema": CLASS_ROW_SCHEMA,
        "class_path": normalized,
        "super_class_path": super_path,
        "is_native": normalized.startswith("/Script/"),
        "interfaces": interface_paths,
        "parent_status": parent_status,
        "interfaces_status": interfaces_status,
        "source": "+".join(sorted(sources)),
        "status": "QUARANTINED_AFTER_REPEATED_INTERRUPTION",
        "reason_code": "REPEATED_INTERRUPTION_SAME_CLASS_GENERATION",
        "interruption_attempts": int(interruption_attempts),
        "confidence": (
            "MEDIUM"
            if parent_status != "NOT_RECOVERED" or interfaces_status != "NOT_RECOVERED"
            else "LOW"
        ),
    }


def _manifest_for_generation(
    generation_id,
    source_sha256,
    inventory_signature,
    rows_integrity,
    checkpoint_integrity,
    completed_at,
):
    generation_prefix = GENERATIONS_DIRECTORY_NAME + "/" + generation_id + "/"
    return {
        "schema": SNAPSHOT_SCHEMA,
        "status": "COMPLETE",
        "generation_id": generation_id,
        "generated_at_utc": completed_at,
        "producer": {
            "id": "export_kb_class_hierarchy_snapshot.py",
            "source_sha256": source_sha256,
            "engine_version": _engine_version(),
            "runtime_identity": _runtime_identity(),
        },
        "inventory_signature": inventory_signature,
        "outputs": {
            "classes": generation_prefix + CLASS_OUTPUT_NAME,
            "checkpoint": generation_prefix + CHECKPOINT_OUTPUT_NAME,
        },
        "files": {
            "classes": {
                "sha256": rows_integrity["sha256"],
                "bytes": int(rows_integrity["bytes"]),
                "record_count": int(rows_integrity["lines"]),
            },
            "checkpoint": {
                "sha256": checkpoint_integrity["sha256"],
                "bytes": int(checkpoint_integrity["bytes"]),
            },
        },
    }


def _finalize_staging(output_dir, staging_dir, checkpoint):
    rows_path = os.path.join(staging_dir, CLASS_OUTPUT_NAME)
    checkpoint_path = os.path.join(staging_dir, CHECKPOINT_OUTPUT_NAME)
    rows_integrity = _committed_rows_integrity(
        rows_path,
        os.path.getsize(rows_path),
    )
    try:
        class_total = int(checkpoint.get("class_total"))
        cursor = int(checkpoint.get("cursor"))
        row_count = int(checkpoint.get("row_count"))
        row_bytes = int(checkpoint.get("row_bytes"))
    except (TypeError, ValueError):
        raise RuntimeError("Class hierarchy checkpoint counters are invalid")
    if (
        class_total < 0
        or cursor != class_total
        or row_count != class_total
        or int(rows_integrity["lines"]) != class_total
        or int(rows_integrity["bytes"]) != row_bytes
        or checkpoint.get("active_class_path")
        or checkpoint.get("active_attempt") is not None
        or str(checkpoint.get("row_chain_sha256") or "")
        != rows_integrity["row_chain_sha256"]
    ):
        raise RuntimeError(
            "Class hierarchy checkpoint is not complete: "
            "class_total, cursor, row_count, bytes, and JSONL must agree"
        )
    if checkpoint.get("status") not in {"RUNNING", "COMPLETE"}:
        raise RuntimeError("Class hierarchy checkpoint status is not finalizable")
    expected_row_sha = str(checkpoint.get("row_sha256") or "")
    if (
        checkpoint.get("status") == "COMPLETE"
        and expected_row_sha != rows_integrity["sha256"]
    ) or (
        checkpoint.get("status") == "RUNNING"
        and expected_row_sha not in {"", rows_integrity["sha256"]}
    ):
        raise RuntimeError("Class row SHA does not match checkpoint")
    checkpoint["row_sha256"] = rows_integrity["sha256"]
    checkpoint["row_bytes"] = rows_integrity["bytes"]
    checkpoint["status"] = "COMPLETE"
    checkpoint["completed_at"] = checkpoint.get("completed_at") or _utc_now()
    checkpoint["updated_at"] = _utc_now()
    _write_json_atomic(checkpoint_path, checkpoint)
    checkpoint_integrity = _file_integrity(checkpoint_path)

    generation_id = str(checkpoint.get("generation_id") or "")
    if not _valid_generation_id(generation_id):
        raise RuntimeError("Checkpoint generation_id is invalid")
    generation_root = os.path.join(
        output_dir,
        GENERATIONS_DIRECTORY_NAME,
    )
    if not os.path.isdir(generation_root):
        os.makedirs(generation_root)
    generation_dir = os.path.join(generation_root, generation_id)
    if os.path.exists(generation_dir):
        raise RuntimeError("Generation destination already exists")

    manifest = _manifest_for_generation(
        generation_id,
        str(checkpoint.get("producer_source_sha256") or ""),
        str(checkpoint.get("inventory_signature") or ""),
        rows_integrity,
        checkpoint_integrity,
        str(checkpoint.get("completed_at") or ""),
    )
    manifest_staging = os.path.join(
        staging_dir,
        MANIFEST_STAGING_NAME,
    )
    _write_json_atomic(manifest_staging, manifest)

    os.replace(staging_dir, generation_dir)
    _fsync_directory(generation_root)
    if (
        _validated_manifest(
            output_dir,
            manifest,
            str(checkpoint.get("producer_source_sha256") or ""),
            str(checkpoint.get("inventory_signature") or ""),
        )
        is None
    ):
        raise RuntimeError("Prepared class hierarchy generation did not verify")
    _publish_root_manifest(output_dir, manifest)
    return manifest


def _new_checkpoint(source_sha256, inventory_signature, total_count):
    return {
        "schema": CHECKPOINT_SCHEMA,
        "status": "RUNNING",
        "generation_id": uuid.uuid4().hex,
        "producer_source_sha256": source_sha256,
        "inventory_signature": inventory_signature,
        "class_total": int(total_count),
        "cursor": 0,
        "row_count": 0,
        "row_bytes": 0,
        "row_sha256": "",
        "row_chain_sha256": hashlib.sha256(b"").hexdigest(),
        "active_cursor": None,
        "active_class_path": "",
        "active_attempt": None,
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _checkpoint_rows_committed(checkpoint, row_bytes, rows):
    checkpoint["row_bytes"] = int(row_bytes)
    checkpoint["row_chain_sha256"] = _advance_row_chain(
        checkpoint.get("row_chain_sha256") or "",
        rows,
    )
    checkpoint["updated_at"] = _utc_now()


def _checkpoint_is_resumable(
    checkpoint,
    source_sha256,
    inventory_signature,
    total_count,
    rows_path,
):
    if not isinstance(checkpoint, dict):
        return False
    try:
        cursor = int(checkpoint.get("cursor"))
        row_count = int(checkpoint.get("row_count"))
        row_bytes = int(checkpoint.get("row_bytes"))
        committed = _committed_rows_integrity(rows_path, row_bytes)
        active_class_path = str(checkpoint.get("active_class_path") or "")
        active_cursor = checkpoint.get("active_cursor")
        active_attempt = checkpoint.get("active_attempt")
        active_marker_valid = (
            not active_class_path and active_cursor is None and active_attempt is None
        ) or (
            _normalize_class_path(active_class_path) == active_class_path
            and int(active_cursor) == cursor
            and int(active_attempt) in {1, 2}
        )
        return (
            checkpoint.get("schema") == CHECKPOINT_SCHEMA
            and checkpoint.get("status") == "RUNNING"
            and _valid_generation_id(checkpoint.get("generation_id"))
            and checkpoint.get("producer_source_sha256") == source_sha256
            and checkpoint.get("inventory_signature") == inventory_signature
            and int(checkpoint.get("class_total")) == int(total_count)
            and 0 <= cursor == row_count <= int(total_count)
            and row_bytes >= 0
            and os.path.getsize(rows_path) >= row_bytes
            and int(committed["lines"]) == row_count
            and str(checkpoint.get("row_chain_sha256") or "")
            == committed["row_chain_sha256"]
            and active_marker_valid
        )
    except Exception:
        return False


def _read_checkpoint(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _read_json_object(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _validated_manifest(
    output_dir,
    manifest,
    producer_source_sha256,
    inventory_signature,
):
    output_dir = os.path.abspath(output_dir)
    producer = manifest.get("producer") if isinstance(manifest, dict) else None
    generation_id = str(
        manifest.get("generation_id") if isinstance(manifest, dict) else ""
    )
    if (
        not isinstance(manifest, dict)
        or not isinstance(producer, dict)
        or manifest.get("schema") != SNAPSHOT_SCHEMA
        or manifest.get("status") != "COMPLETE"
        or not _valid_generation_id(generation_id)
        or manifest.get("inventory_signature") != inventory_signature
        or producer.get("source_sha256") != producer_source_sha256
    ):
        return None

    outputs = manifest.get("outputs")
    files = manifest.get("files")
    if not isinstance(outputs, dict) or not isinstance(files, dict):
        return None
    generation_prefix = GENERATIONS_DIRECTORY_NAME + "/" + generation_id + "/"
    expected_outputs = {
        "classes": generation_prefix + CLASS_OUTPUT_NAME,
        "checkpoint": generation_prefix + CHECKPOINT_OUTPUT_NAME,
    }
    if outputs != expected_outputs:
        return None
    actual_files = {}
    for key, output_key in (
        ("classes", "classes"),
        ("checkpoint", "checkpoint"),
    ):
        relative_path = str(outputs.get(output_key) or "").replace("\\", "/")
        if relative_path != expected_outputs[output_key] or os.path.isabs(
            relative_path
        ):
            return None
        resolved = os.path.realpath(os.path.join(output_dir, relative_path))
        root_prefix = os.path.normcase(os.path.realpath(output_dir)) + os.sep
        normalized_resolved = os.path.normcase(resolved)
        if not normalized_resolved.startswith(root_prefix) or not os.path.isfile(
            resolved
        ):
            return None
        expected = files.get(key)
        if not isinstance(expected, dict):
            return None
        try:
            actual = (
                _committed_rows_integrity(
                    resolved,
                    os.path.getsize(resolved),
                )
                if key == "classes"
                else _file_integrity(resolved)
            )
            if actual["sha256"] != str(expected.get("sha256") or "") or int(
                actual["bytes"]
            ) != int(expected.get("bytes") or -1):
                return None
            if key == "classes" and int(actual["lines"]) != int(
                expected.get("record_count") or -1
            ):
                return None
        except Exception:
            return None
        actual_files[key] = (resolved, actual)

    checkpoint = _read_json_object(actual_files["checkpoint"][0])
    class_integrity = actual_files["classes"][1]
    try:
        expected_count = int(files.get("classes", {}).get("record_count"))
        checkpoint_valid = (
            checkpoint.get("schema") == CHECKPOINT_SCHEMA
            and checkpoint.get("status") == "COMPLETE"
            and checkpoint.get("generation_id") == generation_id
            and checkpoint.get("producer_source_sha256") == producer_source_sha256
            and checkpoint.get("inventory_signature") == inventory_signature
            and int(checkpoint.get("class_total")) == expected_count
            and int(checkpoint.get("cursor")) == expected_count
            and int(checkpoint.get("row_count")) == expected_count
            and int(checkpoint.get("row_bytes")) == int(class_integrity["bytes"])
            and checkpoint.get("row_sha256") == class_integrity["sha256"]
            and checkpoint.get("row_chain_sha256")
            == class_integrity["row_chain_sha256"]
            and not checkpoint.get("active_class_path")
            and checkpoint.get("active_cursor") is None
            and checkpoint.get("active_attempt") is None
        )
    except (TypeError, ValueError):
        checkpoint_valid = False
    if not checkpoint_valid:
        return None

    generation_manifest = os.path.join(
        output_dir,
        GENERATIONS_DIRECTORY_NAME,
        generation_id,
        MANIFEST_STAGING_NAME,
    )
    if _read_json_object(generation_manifest) != manifest:
        return None
    return manifest


def _published_manifest_if_current(
    output_dir,
    producer_source_sha256,
    inventory_signature,
):
    manifest_path = os.path.join(
        os.path.abspath(output_dir),
        MANIFEST_OUTPUT_NAME,
    )
    manifest = _read_json_object(manifest_path)
    return _validated_manifest(
        output_dir,
        manifest,
        producer_source_sha256,
        inventory_signature,
    )


def _publish_root_manifest(output_dir, manifest):
    manifest_path = os.path.join(
        os.path.abspath(output_dir),
        MANIFEST_OUTPUT_NAME,
    )
    _write_json_atomic(manifest_path, manifest)
    if _read_json_object(manifest_path) != manifest:
        raise RuntimeError("Published class hierarchy manifest did not verify")


def _recover_prepared_generation(
    output_dir,
    producer_source_sha256,
    inventory_signature,
    current_manifest=None,
):
    generation_root = os.path.join(
        os.path.abspath(output_dir),
        GENERATIONS_DIRECTORY_NAME,
    )
    if not os.path.isdir(generation_root):
        return None
    current_generation_id = (
        str(current_manifest.get("generation_id") or "")
        if isinstance(current_manifest, dict)
        else ""
    )
    current_marker_mtime = -1.0
    if _valid_generation_id(current_generation_id):
        current_marker = os.path.join(
            generation_root,
            current_generation_id,
            MANIFEST_STAGING_NAME,
        )
        if os.path.isfile(current_marker):
            current_marker_mtime = os.path.getmtime(current_marker)
    candidates = []
    for generation_id in os.listdir(generation_root):
        if (
            not _valid_generation_id(generation_id)
            or generation_id == current_generation_id
        ):
            continue
        marker_path = os.path.join(
            generation_root,
            generation_id,
            MANIFEST_STAGING_NAME,
        )
        manifest = _read_json_object(marker_path)
        validated = _validated_manifest(
            output_dir,
            manifest,
            producer_source_sha256,
            inventory_signature,
        )
        marker_mtime = os.path.getmtime(marker_path)
        if validated is not None and marker_mtime > current_marker_mtime:
            candidates.append((marker_mtime, validated))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item[0],
            str(item[1].get("generated_at_utc") or ""),
            str(item[1].get("generation_id") or ""),
        ),
        reverse=True,
    )
    manifest = candidates[0][1]
    _publish_root_manifest(output_dir, manifest)
    return manifest


def _publish_rows(
    output_dir,
    rows,
    producer_source_sha256,
    inventory_signature,
):
    output_dir = os.path.abspath(output_dir)
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    staging_dir = os.path.join(output_dir, STAGING_DIRECTORY_NAME)
    if os.path.exists(staging_dir):
        raise RuntimeError("Class hierarchy staging directory already exists")
    os.makedirs(staging_dir)
    rows_path = os.path.join(staging_dir, CLASS_OUTPUT_NAME)
    _truncate_file(rows_path, 0)
    ordered_rows = sorted(rows, key=lambda row: str(row.get("class_path") or ""))
    row_bytes = _append_rows(rows_path, ordered_rows)
    checkpoint = _new_checkpoint(
        producer_source_sha256,
        inventory_signature,
        len(ordered_rows),
    )
    checkpoint["cursor"] = len(ordered_rows)
    checkpoint["row_count"] = len(ordered_rows)
    checkpoint["row_bytes"] = row_bytes
    checkpoint["row_chain_sha256"] = _advance_row_chain(
        checkpoint["row_chain_sha256"],
        ordered_rows,
    )
    _write_json_atomic(
        os.path.join(staging_dir, CHECKPOINT_OUTPUT_NAME),
        checkpoint,
    )
    return _finalize_staging(output_dir, staging_dir, checkpoint)


def _engine_version():
    if unreal is None:
        return "UNKNOWN"
    system_library = getattr(unreal, "SystemLibrary", None)
    getter = getattr(system_library, "get_engine_version", None)
    if callable(getter):
        try:
            return str(getter() or "UNKNOWN")
        except Exception:
            pass
    return "UNKNOWN"


def _runtime_identity():
    identity = {
        "engine_version": _engine_version(),
        "game_name": "UNKNOWN",
        "devkit_build_id": str(
            os.environ.get("BTC_KB_DEVKIT_BUILD_ID") or "UNSPECIFIED"
        ),
        "python_api": {
            "top_level_asset_path": False,
            "get_type_from_class": False,
            "get_super_class": False,
            "get_super_struct": False,
            "get_interfaces": False,
            "get_implemented_interfaces": False,
        },
    }
    if unreal is not None:
        identity["python_api"]["top_level_asset_path"] = callable(
            getattr(unreal, "TopLevelAssetPath", None)
        )
        identity["python_api"]["get_type_from_class"] = callable(
            getattr(unreal, "get_type_from_class", None)
        )
        actor_type = getattr(unreal, "Actor", None)
        actor_static_class = getattr(actor_type, "static_class", None)
        actor_class = None
        if callable(actor_static_class):
            try:
                actor_class = actor_static_class()
            except Exception:
                pass
        if actor_class is not None:
            for method_name in (
                "get_super_class",
                "get_super_struct",
                "get_interfaces",
                "get_implemented_interfaces",
            ):
                identity["python_api"][method_name] = callable(
                    getattr(actor_class, method_name, None)
                )
        system_library = getattr(unreal, "SystemLibrary", None)
        game_name_getter = getattr(system_library, "get_game_name", None)
        if callable(game_name_getter):
            try:
                identity["game_name"] = str(game_name_getter() or "UNKNOWN")
            except Exception:
                pass
    _assert_sanitized(identity)
    return identity


def _seed_inventory():
    seed_path = os.environ.get("BTC_KB_CLASS_HIERARCHY_SEED_FILE")
    if not seed_path:
        return set(), hashlib.sha256(b"").hexdigest()
    seed_path = os.path.abspath(seed_path)
    if not os.path.isfile(seed_path):
        raise RuntimeError("Configured class seed inventory does not exist")
    digest = hashlib.sha256()
    with open(seed_path, "rb") as handle:
        payload = handle.read()
    digest.update(payload)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = payload.decode("utf-8", errors="ignore")
    paths = _extract_class_paths(text)
    return paths, digest.hexdigest()


def _registry_class_inventory():
    if unreal is None:
        raise RuntimeError("This exporter must run inside ARK DevKit Unreal Python")
    helpers = getattr(unreal, "AssetRegistryHelpers", None)
    if helpers is None:
        raise RuntimeError("AssetRegistryHelpers is unavailable")
    registry = helpers.get_asset_registry()
    search = getattr(registry, "search_all_assets", None)
    if callable(search):
        search(True)
    wait = getattr(registry, "wait_for_completion", None)
    if callable(wait):
        wait()
    getter = getattr(registry, "get_all_assets", None)
    if not callable(getter):
        raise RuntimeError("Asset Registry get_all_assets is unavailable")
    try:
        assets = getter(True)
    except TypeError:
        assets = getter()
    if assets is None:
        raise RuntimeError("Asset Registry returned no asset collection")
    seed_paths, seed_sha256 = _seed_inventory()
    paths = set(seed_paths)
    record_hashes = []
    parent_hints = {}
    parent_hint_sources = {}
    interface_hints = {}
    interface_complete = set()
    asset_count = 0
    for asset_data in assets:
        asset_count += 1
        record = _asset_record(asset_data)
        _assert_sanitized(record)
        record_hashes.append(_inventory_record_sha256(record))
        paths.update(_class_paths_from_asset_record(record))
        tags = record.get("tags") or {}
        generated_paths = _reference_target_paths(tags.get("GeneratedClass"))
        parent_paths = _reference_target_paths(
            tags.get("ParentClass")
        ) or _reference_target_paths(tags.get("NativeParentClass"))
        implemented, interfaces_are_complete = _implemented_interface_paths(
            tags.get("ImplementedInterfaces")
        )
        if len(parent_paths) == 1:
            for generated_path in generated_paths:
                parent_hints[generated_path] = next(iter(parent_paths))
                parent_hint_sources[generated_path] = "asset_registry_parent_tag"
        if "ImplementedInterfaces" in tags and interfaces_are_complete:
            interface_complete.update(generated_paths)
        if implemented:
            for generated_path in generated_paths:
                interface_hints.setdefault(generated_path, set()).update(implemented)
    if asset_count == 0:
        raise RuntimeError("Asset Registry returned an empty asset collection")
    ancestor_getter = getattr(registry, "get_ancestor_class_names", None)
    if callable(ancestor_getter):
        paths = _expand_ancestor_class_paths(paths, ancestor_getter)
        registry_parents, ambiguous, ancestry_complete = _registry_parent_map(
            paths, ancestor_getter
        )
        parent_hints.update(registry_parents)
        parent_hint_sources.update(
            {
                class_path: "asset_registry_class_ancestry"
                for class_path in registry_parents
            }
        )
    else:
        ambiguous = set()
        ancestry_complete = False
    paths.add("/Script/CoreUObject.Object")
    signature_payload = {
        "schema": "ark.kb.class-hierarchy-inventory.v1",
        "runtime": _runtime_identity(),
        "seed_sha256": seed_sha256,
        "seed_class_paths": sorted(seed_paths),
        "registry_record_hashes": sorted(record_hashes),
        "resolved_parent_hints": sorted(parent_hints.items()),
        "ambiguous_ancestry": sorted(ambiguous),
        "ancestry_complete": bool(ancestry_complete),
    }
    inventory_signature = _inventory_signature(signature_payload)
    return {
        "class_paths": sorted(paths),
        "parent_hints": parent_hints,
        "parent_hint_sources": parent_hint_sources,
        "interface_hints": {
            key: sorted(value) for key, value in interface_hints.items()
        },
        "interface_complete": interface_complete,
        "ambiguous_ancestry": ambiguous,
        "ancestry_complete": bool(ancestry_complete),
        "inventory_signature": inventory_signature,
        "runtime_identity": signature_payload["runtime"],
        "seed_sha256": seed_sha256,
    }


def _live_class_loader(class_path):
    if unreal is None:
        return None
    loader = getattr(unreal, "load_class", None)
    if callable(loader):
        try:
            loaded = loader(None, class_path)
            if loaded is not None:
                return loaded
        except Exception:
            pass
    object_loader = getattr(unreal, "load_object", None)
    if callable(object_loader):
        try:
            loaded = object_loader(None, class_path)
            if loaded is not None:
                return loaded
        except Exception:
            pass
    library = getattr(unreal, "EditorAssetLibrary", None)
    blueprint_loader = getattr(library, "load_blueprint_class", None)
    if callable(blueprint_loader) and class_path.endswith("_C"):
        asset_path = class_path[:-2]
        try:
            return blueprint_loader(asset_path)
        except Exception:
            pass
    return None


def _resolve_source_path():
    candidate = globals().get("__file__")
    if candidate and os.path.isfile(str(candidate)):
        return os.path.abspath(str(candidate))
    project_root = globals().get("BLUEPRINT_TO_CODE_PROJECT_ROOT") or os.environ.get(
        "BLUEPRINT_TO_CODE_PROJECT_ROOT"
    )
    if project_root:
        candidate = os.path.join(
            str(project_root),
            "scripts",
            "devkit_exporters",
            "export_kb_class_hierarchy_snapshot.py",
        )
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise RuntimeError("Unable to resolve exporter source path")


def _output_dir():
    explicit = os.environ.get("BTC_KB_CLASS_HIERARCHY_OUTPUT")
    if explicit:
        output = os.path.abspath(explicit)
    else:
        project_root = globals().get(
            "BLUEPRINT_TO_CODE_PROJECT_ROOT"
        ) or os.environ.get("BLUEPRINT_TO_CODE_PROJECT_ROOT")
        if not project_root:
            raise RuntimeError(
                "Set BTC_KB_CLASS_HIERARCHY_OUTPUT or BLUEPRINT_TO_CODE_PROJECT_ROOT"
            )
        output = os.path.join(
            os.path.abspath(str(project_root)),
            "knowledge_base",
            "devkit_class_hierarchy",
        )
    if not os.path.isdir(output):
        os.makedirs(output)
    return output


def _batch_size():
    raw = os.environ.get("BTC_KB_CLASS_HIERARCHY_BATCH_SIZE")
    try:
        value = int(raw) if raw else DEFAULT_BATCH_SIZE
    except Exception:
        raise RuntimeError("BTC_KB_CLASS_HIERARCHY_BATCH_SIZE must be an integer")
    if value < 1 or value > MAX_BATCH_SIZE:
        raise RuntimeError(
            "BTC_KB_CLASS_HIERARCHY_BATCH_SIZE must be between 1 and {}".format(
                MAX_BATCH_SIZE
            )
        )
    return value


def export_class_hierarchy_snapshot():
    source_path = _resolve_source_path()
    source_sha256 = _sha256_file(source_path)
    inventory = _registry_class_inventory()
    class_paths = inventory["class_paths"]
    inventory_signature = inventory["inventory_signature"]
    output_dir = _output_dir()
    published = _published_manifest_if_current(
        output_dir,
        source_sha256,
        inventory_signature,
    )
    recovered = _recover_prepared_generation(
        output_dir,
        source_sha256,
        inventory_signature,
        current_manifest=published,
    )
    if recovered is not None:
        _log(
            "Recovered and verified generation {}".format(
                recovered.get("generation_id")
            )
        )
        return recovered
    if published is not None:
        _log(
            "Published generation {} is current and verified".format(
                published.get("generation_id")
            )
        )
        return published
    staging_dir = os.path.join(output_dir, STAGING_DIRECTORY_NAME)
    rows_path = os.path.join(staging_dir, CLASS_OUTPUT_NAME)
    checkpoint_path = os.path.join(staging_dir, CHECKPOINT_OUTPUT_NAME)
    batch_size = _batch_size()

    if not os.path.isdir(staging_dir):
        os.makedirs(staging_dir)
    checkpoint = _read_checkpoint(checkpoint_path)
    if (
        checkpoint.get("status") == "COMPLETE"
        and checkpoint.get("producer_source_sha256") == source_sha256
        and checkpoint.get("inventory_signature") == inventory_signature
    ):
        manifest = _finalize_staging(output_dir, staging_dir, checkpoint)
        _log(
            "Recovered completed staging generation {}".format(
                manifest.get("generation_id")
            )
        )
        return manifest
    if _checkpoint_is_resumable(
        checkpoint,
        source_sha256,
        inventory_signature,
        len(class_paths),
        rows_path,
    ):
        _truncate_file(rows_path, int(checkpoint.get("row_bytes") or 0))
        active_class_path = str(checkpoint.get("active_class_path") or "")
        cursor = int(checkpoint.get("cursor") or 0)
        if active_class_path:
            if (
                cursor >= len(class_paths)
                or active_class_path != class_paths[cursor]
                or int(checkpoint.get("active_cursor")) != cursor
            ):
                raise RuntimeError(
                    "Checkpoint crash marker does not match class inventory"
                )
            active_attempt = int(checkpoint.get("active_attempt"))
            if active_attempt < 2:
                checkpoint["active_attempt"] = active_attempt + 1
                checkpoint["updated_at"] = _utc_now()
                _write_json_atomic(checkpoint_path, checkpoint)
                _log(
                    "Retrying first interrupted class attempt at cursor {}".format(
                        cursor
                    )
                )
            else:
                quarantine_row = _quarantined_class_row(
                    active_class_path,
                    inventory["parent_hints"].get(active_class_path, ""),
                    inventory["parent_hint_sources"].get(
                        active_class_path,
                        "",
                    ),
                    inventory["interface_hints"].get(active_class_path, ()),
                    active_class_path in inventory["interface_complete"],
                    active_attempt,
                )
                row_bytes = _append_rows(rows_path, [quarantine_row])
                cursor += 1
                checkpoint["cursor"] = cursor
                checkpoint["row_count"] = cursor
                checkpoint["active_cursor"] = None
                checkpoint["active_class_path"] = ""
                checkpoint["active_attempt"] = None
                _checkpoint_rows_committed(
                    checkpoint,
                    row_bytes,
                    [quarantine_row],
                )
                _write_json_atomic(checkpoint_path, checkpoint)
                _log(
                    "Quarantined repeated interruption at cursor {}".format(cursor - 1)
                )
        _log(
            "Resuming {}/{} classes".format(
                checkpoint.get("cursor"),
                len(class_paths),
            )
        )
    else:
        _truncate_file(rows_path, 0)
        checkpoint = _new_checkpoint(
            source_sha256,
            inventory_signature,
            len(class_paths),
        )
        _write_json_atomic(checkpoint_path, checkpoint)

    cursor = int(checkpoint.get("cursor") or 0)
    while cursor < len(class_paths):
        batch_end = min(len(class_paths), cursor + batch_size)
        while cursor < batch_end:
            class_path = class_paths[cursor]
            active_attempt = (
                int(checkpoint.get("active_attempt"))
                if checkpoint.get("active_class_path") == class_path
                and checkpoint.get("active_cursor") == cursor
                else 1
            )
            checkpoint["active_cursor"] = cursor
            checkpoint["active_class_path"] = class_path
            checkpoint["active_attempt"] = active_attempt
            checkpoint["updated_at"] = _utc_now()
            _write_json_atomic(checkpoint_path, checkpoint)
            row = _reflect_class_row(
                class_path,
                _live_class_loader,
                inventory["parent_hints"].get(class_path, ""),
                inventory["parent_hint_sources"].get(class_path, ""),
                inventory["interface_hints"].get(class_path, ()),
                class_path in inventory["interface_complete"],
            )
            row_bytes = _append_rows(rows_path, [row])
            cursor += 1
            checkpoint["cursor"] = cursor
            checkpoint["row_count"] = cursor
            checkpoint["active_cursor"] = None
            checkpoint["active_class_path"] = ""
            checkpoint["active_attempt"] = None
            _checkpoint_rows_committed(checkpoint, row_bytes, [row])
            _write_json_atomic(checkpoint_path, checkpoint)
        _log("Reflected {}/{} classes".format(cursor, len(class_paths)))

    manifest = _finalize_staging(output_dir, staging_dir, checkpoint)
    _log(
        "Published generation {} with {} classes".format(
            manifest.get("generation_id"),
            manifest.get("files", {}).get("classes", {}).get("record_count"),
        )
    )
    return manifest


if unreal is not None or __name__ == "__main__":
    try:
        EXPORT_RESULT = export_class_hierarchy_snapshot()
    except Exception as exc:
        safe_error = _safe_exception(exc)
        _log("FAILED: {}".format(safe_error))
        raise RuntimeError(safe_error) from None
